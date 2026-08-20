# -*- coding: utf-8 -*-
import copy
import json
from pathlib import Path

import pytest

from Floor_engine_server import (
    routes_whole_home,
    server_schemas,
    whole_home_agent_workflow,
    whole_home_external_review,
)
from Floor_engine_server.whole_home_autopilot import DevelopmentAutopilotError


@pytest.fixture()
def workflow_store(monkeypatch, tmp_path):
    root = tmp_path / 'agent_workflows'
    monkeypatch.setattr(whole_home_agent_workflow, 'WORKFLOW_ROOT', str(root))
    monkeypatch.setenv('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT', '1')
    return root


def _create(workflow_id='workflow-one'):
    return whole_home_agent_workflow.create_workflow(
        workflow_id=workflow_id, title='Cycle', source_digest='a' * 64,
        tasks=[
            {'task_id': 'evidence', 'role': 'Evidence', 'mode': 'read_only'},
            {'task_id': 'implementation', 'role': 'Implementation',
             'mode': 'single_writer', 'depends_on': ['evidence'],
             'allowed_paths': ['whole_home_autopilot.py']},
        ], idempotency_key=f'create-{workflow_id}')


def test_workflow_events_leases_dependencies_and_completion_are_durable(
        workflow_store):
    created = _create()
    assert created['version'] == 1
    assert created['status'] == 'created'
    assert len(list((workflow_store / 'workflow-one' / 'events').glob('*.json'))) == 1

    claimed = whole_home_agent_workflow.claim_task(
        workflow_id='workflow-one', task_id='evidence', agent_id='agent-evidence',
        expected_version=created['version'], lease_seconds=60,
        idempotency_key='claim-evidence')
    token = claimed['claim']['lease_token']
    with pytest.raises(Exception) as stale:
        whole_home_agent_workflow.heartbeat_task(
            workflow_id='workflow-one', task_id='evidence', lease_token=token,
            expected_version=created['version'], idempotency_key='heartbeat-stale')
    assert 'version' in str(stale.value)

    heartbeat = whole_home_agent_workflow.heartbeat_task(
        workflow_id='workflow-one', task_id='evidence', lease_token=token,
        expected_version=claimed['version'], idempotency_key='heartbeat-one')
    complete = whole_home_agent_workflow.complete_task(
        workflow_id='workflow-one', task_id='evidence', lease_token=token,
        expected_version=heartbeat['version'], idempotency_key='complete-evidence',
        result={'status': 'pass', 'summary': 'evidence frozen',
                'report_sha256': 'b' * 64, 'confidence': 1})
    assert complete['tasks'][0]['status'] == 'pass'
    implementation = whole_home_agent_workflow.claim_task(
        workflow_id='workflow-one', task_id='implementation', agent_id='agent-dev',
        expected_version=complete['version'], lease_seconds=60,
        idempotency_key='claim-dev')
    assert implementation['tasks'][1]['status'] == 'running'
    event_files = list((workflow_store / 'workflow-one' / 'events').glob('*.json'))
    assert [path.name[:8] for path in event_files] == sorted(path.name[:8] for path in event_files)
    assert len(event_files) == 5
    (workflow_store / 'workflow-one' / 'workflow.json').unlink()
    recovered = whole_home_agent_workflow.recover_workflow_projection('workflow-one')
    assert recovered['version'] == 5
    assert recovered['tasks'][0]['status'] == 'pass'
    assert recovered['tasks'][1]['lease']['agent_id'] == 'agent-dev'


def test_expired_single_writer_lease_requires_recovery_audit(workflow_store, monkeypatch):
    created = _create('writer-expiry')
    evidence = whole_home_agent_workflow.claim_task(
        workflow_id='writer-expiry', task_id='evidence', agent_id='reader',
        expected_version=created['version'], lease_seconds=30,
        idempotency_key='claim-reader')
    evidence = whole_home_agent_workflow.complete_task(
        workflow_id='writer-expiry', task_id='evidence',
        lease_token=evidence['claim']['lease_token'],
        expected_version=evidence['version'], idempotency_key='done-reader',
        result={'status': 'pass'})
    now = [100.0]
    monkeypatch.setattr(whole_home_agent_workflow.time, 'time', lambda: now[0])
    writer = whole_home_agent_workflow.claim_task(
        workflow_id='writer-expiry', task_id='implementation', agent_id='writer-one',
        expected_version=evidence['version'], lease_seconds=30,
        idempotency_key='claim-writer-one')
    now[0] = 200.0
    with pytest.raises(DevelopmentAutopilotError) as blocked:
        whole_home_agent_workflow.claim_task(
            workflow_id='writer-expiry', task_id='implementation', agent_id='writer-two',
            expected_version=writer['version'], lease_seconds=30,
            idempotency_key='claim-writer-two')
    assert blocked.value.code == 'agent_writer_recovery_audit_required'
    recovery = whole_home_agent_workflow.get_workflow('writer-expiry')
    assert recovery['status'] == 'recovery_audit'
    assert recovery['tasks'][1]['lease'] == {}
    assert recovery['tasks'][1]['claim_history'][-1]['agent_id'] == 'writer-one'


def test_external_agent_review_is_separate_from_human_review(
        workflow_store, monkeypatch, tmp_path):
    monkeypatch.setenv('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT', 'true')
    monkeypatch.setattr(
        whole_home_external_review, 'EXTERNAL_REVIEW_DIR', str(tmp_path / 'external'))
    monkeypatch.setattr(
        whole_home_external_review, 'MANAGED_ARTIFACT_ROOT', str(tmp_path))
    artifact = tmp_path / 'attempt-one.png'
    from PIL import Image
    Image.new('RGB', (32, 24), 'gray').save(artifact)
    workflow = whole_home_agent_workflow.create_workflow(
        workflow_id='external-review-workflow', title='review',
        source_digest='a' * 64,
        tasks=[{'task_id': 'visual-review', 'role': 'Visual Review',
                'mode': 'read_only'}],
        idempotency_key='create-external-review')
    claimed = whole_home_agent_workflow.claim_task(
        workflow_id='external-review-workflow', task_id='visual-review',
        agent_id='visual-reviewer', expected_version=workflow['version'],
        lease_seconds=60, idempotency_key='claim-external-review')
    run = {
        'run_id': 'run-dev', 'execution_policy': 'development_autopilot_v1',
        'development_session_id': 'session',
        'human_review': {'review_version': 7, 'events': [{'human': True}]},
        'results': [{
            'result_id': 'result-one', 'selected_attempt_id': 'attempt-one',
            'attempts': [{'attempt_id': 'attempt-one',
                          'structure_path': str(artifact),
                          'material_attempts': []}],
        }],
    }
    monkeypatch.setattr(routes_whole_home, '_run_entry', lambda run_id: run)
    request = server_schemas.WholeHomeExternalReviewRequest(
        workflow_id='external-review-workflow', task_id='visual-review',
        lease_token=claimed['claim']['lease_token'],
        result_id='result-one', artifact_id='attempt-one', review_status='reject',
        review_tags=['geometry'], review_note='DWG mismatch',
        failure_dimension='model_geometry_adherence',
        confidence=.95, expected_review_version=0, idempotency_key='review-one')
    response = routes_whole_home.review_whole_home_external_result('run-dev', request)
    assert response['label_scope'] == 'development_external_review'
    assert response['excluded_from_human_learning'] is True
    assert response['reviews'][0]['review_status'] == 'reject'
    assert run['human_review'] == {
        'review_version': 7, 'events': [{'human': True}]}
    stored = json.loads((tmp_path / 'external' / 'run-dev.json').read_text('utf-8'))
    assert stored['reviews'][0]['reviewer_id'] == 'agent:visual-reviewer'


def _stable_failure_fields(**overrides) -> dict:
    value = {
        'dimension': 'renderer_channels',
        'pipeline_stage': 'structure_generation',
        'failure_code': 'missing_required_subject',
        'affected_subjects': ['shower_zone', 'toilet'],
        'causal_component': 'nano_banana_geometry_adherence',
    }
    value.update(overrides)
    return value


def _single_writer_failure_cycle(workflow_id: str, workflow: dict, index: int, *,
                                 task_id: str = 'implementation',
                                 report_sha256: str, summary: str,
                                 report_path: str = '', **failure_fields) -> dict:
    claimed = whole_home_agent_workflow.claim_task(
        workflow_id=workflow_id, task_id=task_id,
        agent_id=f'writer-{index}', expected_version=workflow['version'],
        lease_seconds=60, idempotency_key=f'claim-{index}')
    return whole_home_agent_workflow.complete_task(
        workflow_id=workflow_id, task_id=task_id,
        lease_token=claimed['claim']['lease_token'],
        expected_version=claimed['version'],
        idempotency_key=f'complete-{index}',
        result={
            'status': 'fail', **_stable_failure_fields(**failure_fields),
            'report_sha256': report_sha256, 'summary': summary,
            'report_path': report_path,
            'confidence': .9,
        })


def test_third_canonical_single_writer_failure_routes_to_failure_triage(
        workflow_store):
    workflow = whole_home_agent_workflow.create_workflow(
        workflow_id='repeated-failure', title='guard', source_digest='a' * 64,
        tasks=[{
            'task_id': f'implementation-{index}', 'role': 'Implementation',
            'mode': 'single_writer'} for index in range(1, 4)],
        idempotency_key='create-repeated')
    variants = [
        ('A' * 64, 'first wording', r'C:\Temp\run-one\report.md'),
        ('B' * 64, 'completely different prose', '/tmp/run-two/report.md'),
        ('C' * 64, 'third summary and report identity differ', 'relative/report-three.md'),
    ]
    for index, (report_hash, summary, report_path) in enumerate(variants, 1):
        workflow = _single_writer_failure_cycle(
            'repeated-failure', workflow, index,
            task_id=f'implementation-{index}', report_sha256=report_hash,
            summary=summary, report_path=report_path)
    signatures = [row['failure_signature'] for row in workflow['failure_history']]
    assert workflow['status'] == 'failure_triage'
    assert workflow['stop_reason'].startswith('repeated_failure_signature:')
    assert workflow['next_route'] == {
        'stage': 'failure_triage',
        'reason': 'repeated_canonical_failure_signature',
        'signature_version': 'failure-signature-v1',
        'failure_signature': signatures[0],
        'workflow_occurrence_count': 3,
        'micro_tweak_retry_allowed': False,
        'required_route': ['evidence', 'solution', 'challenge', 'decision'],
    }
    assert len(set(signatures)) == 1
    assert workflow['failure_signature_counts'] == {signatures[0]: 3}
    assert {row['audit_context']['task_id'] for row in workflow['failure_history']} == {
        'implementation-1', 'implementation-2', 'implementation-3'}
    assert {row['audit_context']['report_sha256'] for row in workflow['failure_history']} == {
        'a' * 64, 'b' * 64, 'c' * 64}
    with pytest.raises(DevelopmentAutopilotError) as stopped:
        whole_home_agent_workflow.claim_task(
            workflow_id='repeated-failure', task_id='implementation-1',
            agent_id='writer-four', expected_version=workflow['version'],
            lease_seconds=60, idempotency_key='claim-four')
    assert stopped.value.code == 'agent_workflow_not_claimable'


def test_different_failure_signature_resets_consecutive_guard(workflow_store):
    workflow = whole_home_agent_workflow.create_workflow(
        workflow_id='different-failures', title='guard', source_digest='a' * 64,
        tasks=[{'task_id': 'implementation', 'role': 'Implementation',
                'mode': 'single_writer'}], idempotency_key='create-different')
    workflow = _single_writer_failure_cycle(
        'different-failures', workflow, 1,
        report_sha256='a' * 64, summary='one')
    workflow = _single_writer_failure_cycle(
        'different-failures', workflow, 2,
        report_sha256='b' * 64, summary='two')
    workflow = _single_writer_failure_cycle(
        'different-failures', workflow, 3,
        report_sha256='c' * 64, summary='new root cause',
        failure_code='geometry_drift')
    assert workflow['status'] == 'running'
    assert workflow.get('stop_reason') == ''
    assert workflow['tasks'][0]['consecutive_failure_count'] == 1
    assert sorted(workflow['failure_signature_counts'].values()) == [1, 2]


@pytest.mark.parametrize(('field', 'changed'), [
    ('dimension', 'prompt_contract'),
    ('pipeline_stage', 'material_generation'),
    ('failure_code', 'geometry_drift'),
    ('affected_subjects', ['shower_zone', 'basin']),
    ('causal_component', 'prompt_builder'),
])
def test_each_stable_root_cause_field_changes_signature(field, changed):
    baseline = _stable_failure_fields()
    different = copy.deepcopy(baseline)
    different[field] = changed
    assert whole_home_agent_workflow.canonical_failure_signature(
        baseline) != whole_home_agent_workflow.canonical_failure_signature(different)


def test_volatile_paths_uuid_ids_and_subject_order_do_not_change_signature():
    stable = _stable_failure_fields()
    noisy = _stable_failure_fields(
        pipeline_stage=(
            'structure_generation C:\\Temp\\run_20260812_120000_abcd1234\\x '
            'run_20260812_120000_abcd1234 task_20260812_120001_abcd1234 '
            'attempt_20260812_120002_abcd1234 '
            '550e8400-e29b-41d4-a716-446655440000 pid=30400 line 91 '
            '1773594850 120ms'),
        affected_subjects=['toilet', 'shower_zone', 'toilet'],
    )
    assert whole_home_agent_workflow.canonical_failure_signature(
        stable) == whole_home_agent_workflow.canonical_failure_signature(noisy)


def test_corrupt_event_recovery_returns_structured_fail_closed_error(
        workflow_store):
    whole_home_agent_workflow.create_workflow(
        workflow_id='corrupt-event', title='corrupt', source_digest='a' * 64,
        tasks=[{'task_id': 'evidence', 'role': 'Evidence'}],
        idempotency_key='create-corrupt')
    event = next((workflow_store / 'corrupt-event' / 'events').glob('*.json'))
    event.write_text('{not-json', encoding='utf-8')
    with pytest.raises(DevelopmentAutopilotError) as info:
        whole_home_agent_workflow.recover_workflow_projection('corrupt-event')
    assert info.value.code == 'agent_workflow_event_corrupt'
    assert info.value.status_code == 409


def test_future_source_snapshots_are_noncollectable_and_manifested(
        workflow_store, tmp_path):
    workflow = whole_home_agent_workflow.create_workflow(
        workflow_id='safe-snapshot', title='snapshot', source_digest='a' * 64,
        tasks=[{'task_id': 'evidence', 'role': 'Evidence'}],
        idempotency_key='create-snapshot')
    source_root = tmp_path / 'source'
    source = source_root / 'tests' / 'test_historical.py'
    source.parent.mkdir(parents=True)
    source.write_text('def test_old():\n    assert False\n', encoding='utf-8')
    archived = whole_home_agent_workflow.archive_source_snapshot(
        workflow_id='safe-snapshot', source_root=str(source_root),
        relative_paths=['tests/test_historical.py'],
        expected_version=workflow['version'], idempotency_key='snapshot-one')
    record = archived['source_snapshot_archives'][0]
    manifest_path = workflow_store / 'safe-snapshot' / record['manifest_path']
    manifest = json.loads(manifest_path.read_text('utf-8'))
    snapshot_path = workflow_store / 'safe-snapshot' / manifest['records'][0]['snapshot_path']
    assert snapshot_path.name == 'test_historical.py.source_snapshot'
    assert snapshot_path.suffix != '.py'
    assert manifest['pytest_collectable'] is False
    assert snapshot_path.read_bytes() == source.read_bytes()
    assert archived['test_collection_contract']['pytest_roots'] == ['tests']
    assert archived['test_collection_contract']['root_discovery_allowed'] is False


def test_failure_api_schema_requires_and_normalizes_stable_fields():
    common = {
        'lease_token': 'a' * 32, 'expected_version': 1,
        'idempotency_key': 'complete-schema',
    }
    with pytest.raises(Exception):
        server_schemas.WholeHomeAgentTaskCompleteRequest.model_validate({
            **common, 'result': {'status': 'fail', 'summary': 'free prose only'}})
    request = server_schemas.WholeHomeAgentTaskCompleteRequest.model_validate({
        **common,
        'result': {
            'status': 'fail', **_stable_failure_fields(
                affected_subjects=[' toilet ', 'shower_zone', 'toilet'])},
    })
    assert request.result.affected_subjects == ['shower_zone', 'toilet']
    with pytest.raises(Exception):
        server_schemas.WholeHomeAgentTaskCompleteRequest.model_validate({
            **common,
            'result': {
                'status': 'fail', **_stable_failure_fields(
                    failure_code='the renderer failed at C:\\Temp\\run-id'),
            },
        })


def test_legacy_task_local_signatures_remain_audit_only_and_do_not_fake_count(
        workflow_store):
    workflow = whole_home_agent_workflow.create_workflow(
        workflow_id='legacy-signature', title='legacy', source_digest='a' * 64,
        tasks=[{'task_id': 'implementation', 'role': 'Implementation',
                'mode': 'single_writer'}], idempotency_key='create-legacy')
    path = workflow_store / 'legacy-signature' / 'workflow.json'
    legacy = json.loads(path.read_text('utf-8'))
    legacy.pop('failure_signature_counts', None)
    legacy.pop('failure_signature_version', None)
    legacy['tasks'][0].update(
        consecutive_failure_signature='legacy-report-based-signature',
        consecutive_failure_count=99,
        failure_history=[{'failure_signature': 'legacy-report-based-signature'}])
    path.write_text(json.dumps(legacy), encoding='utf-8')
    loaded = whole_home_agent_workflow.get_workflow('legacy-signature')
    assert loaded['failure_signature_counts'] == {}
    result = _single_writer_failure_cycle(
        'legacy-signature', loaded, 1,
        report_sha256='f' * 64, summary='new v1 failure')
    assert list(result['failure_signature_counts'].values()) == [1]
    assert result['status'] == 'running'


def test_pytest_config_ignores_only_audits_and_keeps_live_collectable_roots():
    repo = Path(__file__).resolve().parents[1]
    config = (repo / 'pytest.ini').read_text(encoding='utf-8')
    assert '[pytest]' in config
    assert 'addopts = --import-mode=importlib --ignore=data/_dev_audits' in config
    assert 'testpaths' not in config
    assert (repo / 'tests' / 'test_whole_home_agent_workflow.py').is_file()
    assert (repo / 'standalone_color_calibrator' / 'test_engine.py').is_file()
