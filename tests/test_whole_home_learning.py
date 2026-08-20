# -*- coding: utf-8 -*-
import asyncio
import copy
import hashlib
import json
import os
import threading
import zipfile

import pytest
from PIL import Image

from Floor_engine_server import (
    routes_whole_home, server_schemas, whole_home_cad, whole_home_engine, whole_home_learning,
)


def _write_image(path, color=(180, 170, 150)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new('RGB', (32, 24), color).save(path)
    return path


@pytest.fixture()
def learning_fixture(monkeypatch, tmp_path):
    output = tmp_path / 'output'
    uploads = tmp_path / 'uploads'
    root = output / '_whole_home'
    run_dir = root / 'runs'
    project_dir = root / 'projects'
    folders = {
        'LEARNING_DIR': root / 'learning',
        'FEEDBACK_DIR': root / 'learning' / 'feedback',
        'RECIPE_DIR': root / 'learning' / 'recipes',
        'CONSENT_DIR': root / 'learning' / 'consents',
        'EXPORT_DIR': root / 'learning' / 'exports',
    }
    for folder in (output, uploads, run_dir, project_dir, *folders.values()):
        folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(whole_home_learning, 'MAIN_OUTPUT_DIR', str(output))
    monkeypatch.setattr(whole_home_learning, 'UPLOAD_DIR', str(uploads))
    monkeypatch.setattr(whole_home_learning, 'RUN_DIR', str(run_dir))
    monkeypatch.setattr(whole_home_engine, 'RUN_DIR', str(run_dir))
    monkeypatch.setattr(whole_home_engine, 'PROJECT_DIR', str(project_dir))
    for name, folder in folders.items():
        monkeypatch.setattr(whole_home_learning, name, str(folder))

    image = _write_image(str(output / 'material.jpg'))
    corrected = _write_image(str(output / 'corrected.png'), (160, 150, 130))
    structure = _write_image(str(output / 'structure.jpg'), (200, 200, 200))
    plan = _write_image(str(uploads / 'plan.png'))
    floor = _write_image(str(uploads / 'floor.jpg'))
    buffers = {
        key: _write_image(str(output / 'buffers' / f'{key}.png'))
        for key in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'plan_overlay')
    }
    project = {
        'project_id': 'project-learning', 'verified': True, 'revision': 7,
        'verified_revision': 7, 'floorplan_path': plan,
        'model': {'rooms': [
            {'id': 'room-a', 'label': 'A', 'selected': True},
            {'id': 'room-b', 'label': 'B', 'selected': True},
        ]},
    }
    whole_home_learning._atomic_json(str(project_dir / 'project-learning.json'), project)
    run = {
        'run_id': 'run-learning', 'project_id': 'project-learning',
        'workflow_id': 'workflow-learning', 'round_index': 1,
        'status': 'done', 'created_at': 10.0, 'updated_at': 20.0,
        'model_revision': 7, 'model_hash': 'model-hash',
        'floorplan_path': plan, 'floor_path': floor, 'style_ref_path': '',
        'prompt': 'api_key=super-secret render', 'request_prompt_sha256': 'prompt-hash',
        'style': 'modern', 'lighting': 'day', 'aspect_ratio': '4:3', 'resolution': '4K',
        'model_keys': ['b2'],
        'capture_groups': [
            {'room_id': 'room-a', 'primary_capture_id': 'capture-a', 'fallback_capture_ids': []},
            {'room_id': 'room-b', 'primary_capture_id': 'capture-b', 'fallback_capture_ids': []},
        ],
        'capture_snapshots': [{
            'capture_id': 'capture-a', 'camera_id': 'camera-a', 'room_id': 'room-a',
            'camera': {'id': 'camera-a', 'position': {'x': 1}, 'target': {'x': 2},
                       'focal_length_mm': 24, 'origin_scope': 'inside_room'},
            **{f'{key}_path': path for key, path in buffers.items()},
        }],
        'room_contract_snapshots': [{'room_id': 'room-a', 'room_label': 'A', 'profile': 'bedroom'}],
        'input_manifest': [],
        'results': [{
            'result_id': 'result-a', 'room_id': 'room-a', 'model_key': 'b2',
            'status': 'done', 'outcome': 'material_rejected', 'deliverable': False,
            'capture_id': 'capture-a', 'final_path': '', 'path': '',
            'attempts': [{
                'attempt_id': 'attempt-a', 'capture_id': 'capture-a',
                'structure_path': structure, 'structure_local_gate': {'gate_pass': True},
                'trace': [{'call_id': 'call-s', 'pass': 'structure', 'model_id': 'model-b2',
                           'provider': 'google', 'prompt_version': 'structure-v1',
                           'prompt_sha256': 'structure-prompt'}],
                'material_attempts': [{
                    'material_attempt_id': 'material-a', 'status': 'rejected',
                    'api_original_path': image, 'material_path': image,
                    'corrected_path': corrected, 'final_path': corrected,
                    'final_local_gate': {'gate_pass': True},
                    'evaluation': {'status': 'done', 'gate_pass': False},
                    'trace': [{'call_id': 'call-m', 'pass': 'material', 'model_id': 'model-b2',
                               'provider': 'google', 'prompt_version': 'material-v1',
                               'prompt_sha256': 'material-prompt'}],
                }],
            }],
        }, {
            'result_id': 'result-b', 'room_id': 'room-b', 'model_key': 'b2',
            'status': 'failed', 'outcome': 'failed', 'deliverable': False,
            'capture_id': 'capture-b', 'path': '', 'final_path': '',
            'attempts': [{'attempt_id': 'attempt-b', 'structure_path': structure,
                          'material_attempts': []}],
        }],
    }
    run['generation_spec_hash'] = whole_home_learning.generation_spec_hash(run)
    whole_home_learning._atomic_json(str(run_dir / 'run-learning.json'), run)
    return run, project, {'output': output, 'uploads': uploads, 'run_dir': run_dir,
                          'project_dir': project_dir, 'corrected': corrected}


def _review(run, *, status='pass', version=0, key='review-1', artifact='material-a', tags=None):
    return whole_home_learning.review_result(
        run, 'result-a', artifact_id=artifact, review_status=status,
        review_tags=tags or [], review_note='', reviewer_id='tester',
        expected_review_version=version, idempotency_key=key,
    )


def test_review_state_get_is_read_only_and_explicit_review_materializes_recipe(learning_fixture):
    run, _, paths = learning_fixture
    artifacts = whole_home_learning.enumerate_reviewable_artifacts(run)
    assert [row['artifact_id'] for row in artifacts] == ['material-a']
    assert artifacts[0]['path'] == os.path.realpath(paths['corrected'])

    run_path = paths['run_dir'] / 'run-learning.json'
    project_path = paths['project_dir'] / 'project-learning.json'
    authoritative_before = {
        path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in (run_path, project_path)
    }
    recipe_dir = paths['output'] / '_whole_home' / 'learning' / 'recipes'
    assert list(recipe_dir.rglob('*.json')) == []

    assert whole_home_learning.get_run_review_state(run)['round_status'] == 'awaiting_human_review'
    assert list(recipe_dir.rglob('*.json')) == []
    assert {
        path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in (run_path, project_path)
    } == authoritative_before

    mutation = _review(run)
    assert mutation['review_state']['counts']['pass'] == 1
    recipe_files = list(recipe_dir.rglob('*.json'))
    assert len(recipe_files) == 1


def test_reference_recipe_is_audited_and_generation_hash_ignores_runtime_resolution(learning_fixture):
    run, _, paths = learning_fixture
    local_reference = str(paths['output'] / 'private-reference.jpg')
    _write_image(local_reference)
    contract = copy.deepcopy(whole_home_cad.JUSTEASY_REFERENCE_CONTRACT)
    slot = contract['slots'][0]
    slot['reference_asset'].update(
        local_path=local_reference, status='verified', verified_at='first',
        width=500, height=500, mime='image/jpeg')
    run.update(
        material_mode='reference', reference_contract_id=contract['contract_id'],
        reference_contract_snapshot=contract,
        reference_asset_snapshots=[{
            'role': 'current_slot_reference', 'slot_id': slot['slot_id'],
            'asset_id': slot['reference_asset']['asset_id'],
            'sha256': slot['reference_asset']['sha256'], 'width': 500, 'height': 500,
            'mime': 'image/jpeg', 'scene_id': slot['reference_viewpoint']['scene_id'],
        }],
    )
    run['results'][0]['slot_id'] = slot['slot_id']
    run['room_contract_snapshots'][0]['reference_slot'] = copy.deepcopy(slot)
    run['input_manifest'] = [{
        'path': local_reference, 'role': 'current_slot_reference',
        'slot_id': slot['slot_id'], 'asset_id': slot['reference_asset']['asset_id'],
        'sha256': slot['reference_asset']['sha256'], 'width': 500, 'height': 500,
        'scene_id': slot['reference_viewpoint']['scene_id'],
    }]
    first_hash = whole_home_learning.generation_spec_hash(run)
    changed_runtime = copy.deepcopy(run)
    changed_asset = changed_runtime['reference_contract_snapshot']['slots'][0]['reference_asset']
    changed_asset.update(local_path=r'C:\private\other.jpg', status='error', verified_at='later',
                         width=999, height=999)
    assert whole_home_learning.generation_spec_hash(changed_runtime) == first_hash
    changed_runtime['reference_contract_snapshot']['slots'][0]['reference_asset']['sha256'] = 'different'
    assert whole_home_learning.generation_spec_hash(changed_runtime) != first_hash

    artifact = whole_home_learning.enumerate_reviewable_artifacts(run)[0]
    recipe = whole_home_learning.build_recipe_snapshot(run, run['results'][0], artifact)
    serialized = json.dumps(recipe, ensure_ascii=False)
    assert local_reference not in serialized
    reference_snapshot = recipe['inputs']['reference_asset_snapshots'][0]
    assert reference_snapshot['asset_id'] == '01_living_a'
    assert reference_snapshot['width'] == 500
    assert reference_snapshot['scene_id'] == 279876079


def test_review_requires_live_artifact_and_reject_tag(learning_fixture):
    run, _, _ = learning_fixture
    with pytest.raises(whole_home_learning.WholeHomeLearningError) as missing:
        _review(run, artifact='missing')
    assert missing.value.status_code == 409
    with pytest.raises(whole_home_learning.WholeHomeLearningError) as tag:
        _review(run, status='reject')
    assert tag.value.status_code == 422
    no_image = whole_home_learning.review_result(
        run, 'result-b', artifact_id='', review_status='reject', review_tags=['no-image'],
        review_note='', reviewer_id='tester', expected_review_version=0,
        idempotency_key='reject-no-image')
    assert no_image['event']['artifact_id'] == ''


def test_review_is_append_only_versioned_and_idempotent(learning_fixture):
    run, _, _ = learning_fixture
    first = _review(run)
    assert first['review_state']['review_version'] == 1
    duplicate = _review(run)
    assert duplicate['event']['event_id'] == first['event']['event_id']
    assert duplicate['review_state']['event_count'] == 1
    with pytest.raises(whole_home_learning.WholeHomeLearningError):
        _review(run, status='backup', key='review-1')
    second = _review(run, status='backup', version=1, key='review-2')
    assert second['event']['previous_event_id'] == first['event']['event_id']


def test_completion_freezes_event_set_and_reset_reopens(learning_fixture):
    run, _, _ = learning_fixture
    _review(run)
    run_status = run['status']
    ledger_before = list(run.get('call_ledger') or [])
    completed = whole_home_learning.complete_run_review(
        run, 'tester', expected_review_version=1, idempotency_key='complete-1')
    assert completed['round_status'] == 'review_complete'
    assert completed['completion_event_id']
    assert run['status'] == run_status
    assert list(run.get('call_ledger') or []) == ledger_before
    duplicate = whole_home_learning.complete_run_review(
        run, 'tester', expected_review_version=1, idempotency_key='complete-1')
    assert duplicate['completion_event_id'] == completed['completion_event_id']
    reopened = _review(run, status='unreviewed', version=2, key='reset-1')
    assert reopened['review_state']['round_status'] == 'awaiting_human_review'
    assert reopened['review_state']['completion_event_id'] == ''
    with pytest.raises(whole_home_learning.WholeHomeLearningError):
        whole_home_learning.complete_run_review(
            run, 'tester', expected_review_version=3, idempotency_key='complete-2')


def test_zero_artifacts_requires_no_human_completion(learning_fixture):
    run, _, _ = learning_fixture
    run['results'] = [run['results'][1]]
    state = whole_home_learning.get_run_review_state(run)
    assert state['round_status'] == 'review_not_required'
    assert state['requires_human_review'] is False
    assert state['can_complete'] is True
    completed = whole_home_learning.complete_run_review(
        run, 'tester', expected_review_version=0, idempotency_key='none')
    assert completed['round_status'] == 'review_complete'
    assert completed['completion_event_id']


def test_interrupted_partial_running_material_is_reviewable(learning_fixture):
    run, _, _ = learning_fixture
    run['status'] = 'partial'
    run['results'][0]['status'] = 'running'
    state = whole_home_learning.get_run_review_state(run)
    assert state['reviewable_count'] == 1
    reviewed = _review(run)
    assert reviewed['event']['review_status'] == 'pass'


def test_zero_artifact_confirmation_is_required_before_continue_and_is_free(learning_fixture, monkeypatch):
    run, _, _ = learning_fixture
    run['results'] = [run['results'][1]]
    calls = []

    async def fake_create(request, metadata=None):
        calls.append((request, metadata))
        return {'run_id': 'zero-child'}

    monkeypatch.setattr(routes_whole_home, '_run_entry', lambda run_id: run)
    monkeypatch.setattr(routes_whole_home, '_existing_idempotent_run', lambda *args, **kwargs: None)
    monkeypatch.setattr(routes_whole_home, '_create_whole_home_run', fake_create)
    monkeypatch.setattr(routes_whole_home, 'workflow_covered_room_ids', lambda parent: [])
    before_status = run['status']
    before_ledger = list(run.get('call_ledger') or [])
    blocked = server_schemas.WholeHomeContinueRequest(
        expected_review_version=0, continuation_completion_event_id='not-complete',
        idempotency_key='zero-continue')
    with pytest.raises(routes_whole_home.HTTPException):
        asyncio.run(routes_whole_home.continue_whole_home_run('run-learning', blocked))
    assert calls == []
    completed = whole_home_learning.complete_run_review(
        run, 'tester', expected_review_version=0, idempotency_key='zero-complete')
    assert run['status'] == before_status and list(run.get('call_ledger') or []) == before_ledger
    allowed = server_schemas.WholeHomeContinueRequest(
        expected_review_version=completed['review_version'],
        continuation_completion_event_id=completed['completion_event_id'],
        idempotency_key='zero-continue')
    response = asyncio.run(routes_whole_home.continue_whole_home_run('run-learning', allowed))
    assert response['run_id'] == 'zero-child'
    assert len(calls) == 1


def test_concurrent_review_compare_and_swap_keeps_valid_json(learning_fixture):
    run, _, _ = learning_fixture
    outcomes = []

    def worker(key):
        try:
            outcomes.append(_review(run, key=key))
        except whole_home_learning.WholeHomeLearningError as ex:
            outcomes.append(ex.status_code)

    threads = [threading.Thread(target=worker, args=(f'key-{index}',)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(isinstance(row, dict) for row in outcomes) == 1
    assert outcomes.count(409) == 1
    feedback = whole_home_learning._read_json(whole_home_learning._feedback_path('run-learning'))
    assert len(feedback['events']) == 1


def test_consent_does_not_mutate_project_or_model_hash(learning_fixture):
    _, project, paths = learning_fixture
    project_path = paths['project_dir'] / 'project-learning.json'
    before = hashlib.sha256(project_path.read_bytes()).hexdigest()
    original_model_hash = whole_home_engine.model_hash(project['model'])
    consent = whole_home_learning.set_training_consent(project, True, 'tester')
    assert consent['allowed'] is True
    assert hashlib.sha256(project_path.read_bytes()).hexdigest() == before
    assert whole_home_engine.model_hash(project['model']) == original_model_hash


def test_summary_coverage_is_pass_only_and_scoped_to_workflow_spec(learning_fixture):
    run, _, paths = learning_fixture
    _review(run)
    assert whole_home_learning.workflow_covered_room_ids(run) == ['room-a']
    summary = whole_home_learning.learning_summary('project-learning')
    assert summary['covered_room_ids'] == ['room-a']
    assert summary['uncovered_room_ids'] == ['room-b']
    _review(run, status='unreviewed', version=1, key='reset')
    assert whole_home_learning.workflow_covered_room_ids(run) == []
    other = json.loads(json.dumps(run))
    other.update(run_id='run-other', workflow_id='workflow-other', created_at=30)
    whole_home_learning._atomic_json(str(paths['run_dir'] / 'run-other.json'), other)
    assert whole_home_learning.workflow_covered_room_ids(run) == []


def test_export_requires_consent_is_strong_weak_safe_and_relative(learning_fixture, tmp_path):
    run, project, _ = learning_fixture
    _review(run)
    with pytest.raises(whole_home_learning.WholeHomeLearningError) as denied:
        whole_home_learning.build_learning_export('project-learning')
    assert denied.value.status_code == 403
    whole_home_learning.set_training_consent(project, True, 'tester')
    export_path = whole_home_learning.build_learning_export('project-learning')
    with zipfile.ZipFile(export_path) as archive:
        manifest = [json.loads(line) for line in archive.read('manifest.jsonl').decode().splitlines()]
        all_text = '\n'.join(
            archive.read(name).decode('utf-8', errors='ignore')
            for name in archive.namelist() if name.endswith(('.json', '.jsonl'))
        )
    assert {row['label_strength'] for row in manifest} == {'strong', 'weak'}
    assert 'super-secret' not in all_text
    assert 'C:\\' not in all_text and str(tmp_path) not in all_text
    assert all(not item['archive_path'].startswith('/') for row in manifest for item in row['files'])


def test_reference_export_never_packages_or_names_private_reference_asset(learning_fixture):
    run, project, paths = learning_fixture
    local_reference = str(paths['output'] / '_whole_home' / 'reference_assets' /
                          'contract' / '01_living_a.jpg')
    _write_image(local_reference)
    contract = copy.deepcopy(whole_home_cad.JUSTEASY_REFERENCE_CONTRACT)
    slot = contract['slots'][0]
    slot['reference_asset'].update(
        local_path=local_reference, status='verified', width=500, height=500,
        public_thumb_url='https://example.invalid/thumb.jpg?signed=private')
    run.update(
        material_mode='reference', reference_contract_id=contract['contract_id'],
        reference_contract_snapshot=contract,
        reference_asset_snapshots=[{
            'role': 'current_slot_reference', 'slot_id': slot['slot_id'],
            'asset_id': slot['reference_asset']['asset_id'],
            'sha256': slot['reference_asset']['sha256'], 'width': 500, 'height': 500,
            'scene_id': slot['reference_viewpoint']['scene_id'], 'export_allowed': False,
        }],
    )
    run['results'][0]['slot_id'] = slot['slot_id']
    run['room_contract_snapshots'][0]['reference_slot'] = copy.deepcopy(slot)
    run['input_manifest'] = [{
        'path': local_reference, 'role': 'current_slot_reference',
        'asset_id': slot['reference_asset']['asset_id'],
        'sha256': slot['reference_asset']['sha256'],
    }]
    run['generation_spec_hash'] = whole_home_learning.generation_spec_hash(run)
    whole_home_learning._atomic_json(str(paths['run_dir'] / 'run-learning.json'), run)
    _review(run)
    whole_home_learning.set_training_consent(project, True, 'tester')
    export_path = whole_home_learning.build_learning_export('project-learning')
    with zipfile.ZipFile(export_path) as archive:
        names = archive.namelist()
        all_text = '\n'.join(
            archive.read(name).decode('utf-8', errors='ignore')
            for name in names if name.endswith(('.json', '.jsonl'))
        )
    assert '01_living_a.jpg' not in names
    assert local_reference not in all_text
    assert 'signed=private' not in all_text


def test_continue_route_requires_current_completion_and_filters_covered(monkeypatch, learning_fixture):
    run, _, _ = learning_fixture
    calls = []

    async def fake_create(request, metadata=None):
        calls.append((request, metadata))
        return {'run_id': 'child'}

    monkeypatch.setattr(routes_whole_home, '_run_entry', lambda run_id: run)
    monkeypatch.setattr(routes_whole_home, '_existing_idempotent_run', lambda *args, **kwargs: None)
    monkeypatch.setattr(routes_whole_home, '_create_whole_home_run', fake_create)
    monkeypatch.setattr(routes_whole_home, 'workflow_covered_room_ids', lambda parent: ['room-a'])
    monkeypatch.setattr(routes_whole_home, 'get_run_review_state', lambda parent: {
        'round_status': 'awaiting_human_review', 'review_version': 0,
        'completion_event_id': '',
    })
    request = server_schemas.WholeHomeContinueRequest(
        expected_review_version=2, continuation_completion_event_id='complete-1',
        idempotency_key='continue-1')
    with pytest.raises(routes_whole_home.HTTPException):
        asyncio.run(routes_whole_home.continue_whole_home_run('run-learning', request))
    assert calls == []
    monkeypatch.setattr(routes_whole_home, 'get_run_review_state', lambda parent: {
        'round_status': 'review_complete', 'review_version': 2,
        'completion_event_id': 'complete-1',
    })
    response = asyncio.run(routes_whole_home.continue_whole_home_run('run-learning', request))
    assert response['run_id'] == 'child'
    assert [group.room_id for group in calls[0][0].capture_groups] == ['room-b']
    assert calls[0][1]['parent_run_id'] == 'run-learning'
    assert calls[0][1]['continuation_completion_event_id'] == 'complete-1'


def test_nested_attempt_counts_as_partial_evidence_without_becoming_deliverable(learning_fixture):
    run, _, _ = learning_fixture
    assert whole_home_engine.run_has_viewable_artifact(run) is True
    assert all(not result.get('deliverable') for result in run['results'])


def test_create_spawn_failure_is_persisted_terminal_not_left_active(monkeypatch, learning_fixture):
    run, project, paths = learning_fixture
    capture = dict(run['capture_snapshots'][0])
    capture.update(status='confirmed', aspect_ratio='4:3')
    project = dict(project)
    project['captures'] = [capture]
    project['floorplan_path'] = str(paths['uploads'] / 'plan.png')
    persisted = []
    monkeypatch.setattr(routes_whole_home, '_project_entry', lambda project_id: project)
    monkeypatch.setattr(routes_whole_home, '_valid_capture', lambda *args: True)
    monkeypatch.setattr(routes_whole_home, 'require_upload_image_path', lambda path, *args, **kwargs: path)
    monkeypatch.setattr(routes_whole_home, 'load_config', lambda: {'gemini_api_key': 'configured'})
    monkeypatch.setattr(routes_whole_home, 'build_room_generation_contract',
                        lambda project_value, capture_value: {'room_id': 'room-a'})
    monkeypatch.setattr(routes_whole_home, '_persist_run', lambda value: persisted.append(dict(value)))
    monkeypatch.setattr(routes_whole_home.state, 'spawn',
                        lambda coro: (_ for _ in ()).throw(RuntimeError('spawn failed')))
    request = server_schemas.WholeHomeRunRequest(
        project_id='project-learning',
        capture_groups=[{'room_id': 'room-a', 'primary_capture_id': 'capture-a'}],
        floor_path=str(paths['uploads'] / 'floor.jpg'), model_keys=['b2'],
        idempotency_key='spawn-failure',
    )
    with pytest.raises(routes_whole_home.HTTPException) as failure:
        asyncio.run(routes_whole_home._create_whole_home_run(request))
    assert failure.value.status_code == 500
    assert persisted[-1]['status'] == 'failed'
    failed_id = persisted[-1]['run_id']
    assert failed_id not in routes_whole_home._ACTIVE_RUNS
    assert failed_id not in routes_whole_home._RUN_KEYS


def test_creation_idempotency_rejects_different_request_fingerprint(monkeypatch):
    routes_whole_home._ACTIVE_RUNS['run-idempotent-test'] = {
        'run_id': 'run-idempotent-test', 'project_id': 'project-x',
        'creation_idempotency_key': 'same-key',
        'creation_request_fingerprint': 'fingerprint-a',
    }
    try:
        with pytest.raises(routes_whole_home.HTTPException) as collision:
            routes_whole_home._existing_idempotent_run(
                'project-x', 'same-key', request_fingerprint='fingerprint-b')
        assert collision.value.status_code == 409
    finally:
        routes_whole_home._ACTIVE_RUNS.pop('run-idempotent-test', None)
