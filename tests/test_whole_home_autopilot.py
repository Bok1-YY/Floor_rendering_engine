# -*- coding: utf-8 -*-
import asyncio
import json

import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import (
    routes_whole_home,
    server_schemas,
    whole_home_autopilot,
    whole_home_engine,
)


@pytest.fixture()
def autopilot_store(monkeypatch, tmp_path):
    sessions = tmp_path / 'sessions'
    monkeypatch.setattr(whole_home_autopilot, 'SESSION_DIR', str(sessions))
    monkeypatch.setenv('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT', 'true')
    routes_whole_home._DEVELOPMENT_CLAIM_PROOFS.clear()
    yield sessions
    routes_whole_home._DEVELOPMENT_CLAIM_PROOFS.clear()


_CLAIM_PROOFS = {}


def _prepare(session_id='dev-session', *, batch=1, project='project',
             limits=None, key=None, fingerprint=None):
    return whole_home_autopilot.prepare_development_batch(
        session_id=session_id,
        project_id=project,
        batch_index=batch,
        parent_run_id='',
        limits=limits or {'paid_batches': 6, 'image_calls': 140, 'qa_calls': 280},
        idempotency_key=key or f'key-{batch}',
        request_fingerprint=fingerprint or f'fingerprint-{batch}',
    )


def _claim(session_id='dev-session', *, batch=1):
    session = whole_home_autopilot.get_development_session(session_id)
    row = next(item for item in session['batches'] if item['batch_index'] == batch)
    remaining = session['remaining']
    claim = whole_home_autopilot.claim_development_run(
        session_id=session_id, batch_index=batch,
        request_fingerprint=row['request_fingerprint'],
        budget_envelope={
            'result_count': 1, 'model_keys': ['b2', 'pro'],
            'candidates_per_camera': 1,
            'image_calls_min': 0, 'image_calls_max': remaining['image_calls'],
            'qa_calls_min': 0, 'qa_calls_max': remaining['qa_calls'],
        })
    proof = {
        'run_claim_id': claim['run_claim_id'],
        'claim_generation': claim['claim_generation'],
        'claim_token': claim['claim_token'],
        'request_fingerprint': row['request_fingerprint'],
    }
    _CLAIM_PROOFS[(session_id, batch)] = proof
    return proof


def _bind(session_id='dev-session', *, batch=1, run='run-1',
          run_status='queued'):
    proof = _claim(session_id, batch=batch)
    result = whole_home_autopilot.bind_development_run(
        session_id, batch, run, run_status, **proof)
    routes_whole_home._DEVELOPMENT_CLAIM_PROOFS[run] = proof
    return result


def _proof(session_id='dev-session', batch=1):
    return _CLAIM_PROOFS[(session_id, batch)]


def _dispatch(session_id, batch, reservation_id):
    return whole_home_autopilot.mark_logical_call_dispatched(
        session_id, reservation_id, **_proof(session_id, batch))


def _finish(session_id, batch, reservation_id, *, success, error=''):
    return whole_home_autopilot.finish_logical_call(
        session_id, reservation_id, success=success, error=error,
        **_proof(session_id, batch))


def _terminal(session_id, batch, run, status, error=''):
    return whole_home_autopilot.mark_development_run_terminal(
        session_id, batch, run, status, error,
        **_proof(session_id, batch))


def _reserve(session_id='dev-session', *, batch=1, run='run-1', call='call-1',
             kind='generation'):
    return whole_home_autopilot.reserve_logical_call(
        session_id=session_id,
        batch_index=batch,
        run_id=run,
        call_id=call,
        kind=kind,
        phase='structure' if kind == 'generation' else 'final',
        result_id='result-1',
        attempt_id='attempt-1',
        **_proof(session_id, batch),
    )


def test_disabled_environment_hides_development_routes(monkeypatch, tmp_path):
    monkeypatch.setattr(whole_home_autopilot, 'SESSION_DIR', str(tmp_path / 'sessions'))
    monkeypatch.delenv('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT', raising=False)
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as info:
        whole_home_autopilot.get_development_session('missing')
    assert info.value.status_code == 404
    with pytest.raises(HTTPException) as route_info:
        routes_whole_home.get_development_autopilot_session('missing')
    assert route_info.value.status_code == 404
    assert route_info.value.detail['code'] == 'development_autopilot_disabled'


def test_preflight_failure_creates_no_paid_batch_or_reservation(
        autopilot_store, monkeypatch):
    request = server_schemas.WholeHomeDevelopmentAutopilotRunRequest(
        project_id='missing-project',
        capture_ids=['capture'],
        floor_path='missing-floor.jpg',
        development_session_id='preflight-session',
        batch_index=1,
        idempotency_key='preflight-key',
    )
    monkeypatch.setattr(routes_whole_home, '_existing_idempotent_run', lambda *args, **kwargs: None)
    with pytest.raises(HTTPException):
        asyncio.run(routes_whole_home.create_development_autopilot_run(request))
    session = whole_home_autopilot.get_development_session('preflight-session')
    assert session['used'] == {'paid_batches': 0, 'image_calls': 0, 'qa_calls': 0}
    assert session['reserved'] == {'image_calls': 0, 'qa_calls': 0}
    assert session['reservations'] == []
    assert session['batches'][0]['status'] == 'preflight_failed'


def test_first_reservation_counts_paid_batch_and_restart_keeps_it_reserved(
        autopilot_store):
    _prepare()
    _bind()
    reservation = _reserve()
    assert reservation['status'] == 'reserved'
    # get_development_session always rereads the durable JSON; there is no cache.
    session = whole_home_autopilot.get_development_session('dev-session')
    assert session['used']['paid_batches'] == 1
    assert session['reserved']['image_calls'] == 1
    assert session['remaining']['image_calls'] == 139
    assert session['batches'][0]['paid_started_at']
    payload = json.loads((autopilot_store / 'dev-session.json').read_text(encoding='utf-8'))
    assert payload['reservations'][0]['status'] == 'reserved'


def test_cross_run_budget_is_aggregate_and_batch_claim_is_idempotent(
        autopilot_store):
    limits = {'paid_batches': 2, 'image_calls': 2, 'qa_calls': 2}
    first = _prepare(limits=limits)
    repeated = _prepare(limits=limits)
    assert len(first['batches']) == len(repeated['batches']) == 1
    _bind()
    one = _reserve(call='call-1', run='run-1')
    _dispatch('dev-session', 1, one['reservation_id'])
    _finish('dev-session', 1, one['reservation_id'], success=True)
    _terminal('dev-session', 1, 'run-1', 'done')
    _prepare(batch=2, limits=limits)
    _bind(batch=2, run='run-2')
    two = _reserve(batch=2, call='call-2', run='run-2')
    _dispatch('dev-session', 2, two['reservation_id'])
    _finish('dev-session', 2, two['reservation_id'], success=False, error='provider failed')
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as info:
        _reserve(batch=2, call='call-3', run='run-2')
    assert info.value.code in {
        'development_budget_exhausted',
        'development_budget_envelope_exhausted',
    }
    session = whole_home_autopilot.get_development_session('dev-session')
    assert session['used'] == {'paid_batches': 2, 'image_calls': 2, 'qa_calls': 0}
    assert session['remaining']['image_calls'] == 0


def test_terminal_paid_batch_cannot_be_restarted_with_same_configuration(
        autopilot_store):
    _prepare()
    _bind()
    reservation = _reserve()
    _dispatch('dev-session', 1, reservation['reservation_id'])
    _finish('dev-session', 1, reservation['reservation_id'], success=False)
    _terminal('dev-session', 1, 'run-1', 'partial')
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as info:
        _prepare()
    assert info.value.code == 'development_batch_already_terminal'


def test_batch_index_conflict_is_rejected(autopilot_store):
    _prepare()
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as info:
        _prepare(key='different-key', fingerprint='different-fingerprint')
    assert info.value.code == 'development_batch_conflict'


def test_session_cancel_refunds_only_not_dispatched_and_blocks_future_reserve(
        autopilot_store):
    _prepare()
    _bind()
    _reserve()
    cancelled = whole_home_autopilot.cancel_development_session('dev-session')
    assert cancelled['status'] == 'cancelled'
    assert cancelled['reservations'][0]['status'] == 'cancelled_before_dispatch'
    assert cancelled['reserved']['image_calls'] == 0
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as info:
        _reserve(call='call-2')
    assert info.value.code == 'development_run_claim_fenced'


def _development_run(session_id='dispatch-session', *, batch=1):
    proof = _CLAIM_PROOFS.get((session_id, batch), {})
    return {
        'run_id': 'run-dispatch',
        'project_id': 'project-dispatch',
        'execution_policy': 'development_autopilot_v1',
        'development_session_id': session_id,
        'development_batch_index': batch,
        'development_run_claim_id': proof.get('run_claim_id') or '',
        'development_claim_generation': proof.get('claim_generation') or 0,
        'development_request_fingerprint': proof.get('request_fingerprint') or '',
        'development_limits_snapshot': {
            'paid_batches': 6, 'image_calls': 140, 'qa_calls': 280,
        },
        'floorplan_path': '', 'floor_path': '', 'style_ref_path': '',
        'material_mode': 'reference', 'call_ledger': [], 'results': [],
    }


def _patch_run_storage(monkeypatch, tmp_path):
    run_dir = tmp_path / 'runs'
    review_dir = tmp_path / 'reviews'
    run_dir.mkdir()
    review_dir.mkdir()
    monkeypatch.setattr(whole_home_engine, 'RUN_DIR', str(run_dir))
    monkeypatch.setattr(whole_home_engine, 'REVIEW_DIR', str(review_dir))
    return run_dir


def test_image_and_qa_reservations_are_persisted_before_provider_dispatch(
        autopilot_store, monkeypatch, tmp_path):
    _prepare('dispatch-session')
    _bind('dispatch-session', run='run-dispatch')
    run_dir = _patch_run_storage(monkeypatch, tmp_path)
    image_path = tmp_path / 'input.png'
    Image.new('RGB', (32, 24), 'gray').save(image_path)
    run = _development_run()
    result = {'result_id': 'result-1', 'trace': []}
    attempt = {'attempt_id': 'attempt-1', 'trace': []}
    routes_whole_home.state.model_semaphores['b2'] = asyncio.Semaphore(1)
    observations = []

    def assert_persisted_generation(*args, **kwargs):
        persisted = json.loads(
            (run_dir / 'run-dispatch.json').read_text(encoding='utf-8'))
        row = persisted['call_ledger'][-1]
        session = whole_home_autopilot.get_development_session('dispatch-session')
        observations.append(('generation', row['budget_status'], session['reserved']['image_calls']))
        assert row['budget_status'] == 'dispatched'
        assert session['reserved']['image_calls'] == 1
        return object(), None, 'google'

    monkeypatch.setattr(routes_whole_home, 'call_image_generate', assert_persisted_generation)
    asyncio.run(routes_whole_home._call_generation(
        run, result, attempt, 'key', 'Nano Banana 2', 'model-b2', 'b2',
        'structure', 'prompt', [str(image_path)], '2K', '4:3'))
    session = whole_home_autopilot.get_development_session('dispatch-session')
    assert session['used']['image_calls'] == 1
    assert run['call_ledger'][0]['budget_status'] == 'done'

    def assert_persisted_qa(*args, **kwargs):
        persisted = json.loads(
            (run_dir / 'run-dispatch.json').read_text(encoding='utf-8'))
        row = persisted['call_ledger'][-1]
        session_now = whole_home_autopilot.get_development_session('dispatch-session')
        observations.append(('qa', row['budget_status'], session_now['reserved']['qa_calls']))
        assert row['budget_status'] == 'dispatched'
        assert session_now['reserved']['qa_calls'] == 1
        return {
            'status': 'done', 'phase': 'final', 'gate_pass': True,
            'hard_fail': False, 'checks': [], 'evaluator_model': 'qa-model',
        }, None

    monkeypatch.setattr(routes_whole_home, 'evaluate_whole_home_phase', assert_persisted_qa)
    asyncio.run(routes_whole_home._evaluate_with_retries(
        'key', {}, {}, str(image_path), '', phase='final',
        run=run, result=result, attempt_row=attempt, attempts=1))
    session = whole_home_autopilot.get_development_session('dispatch-session')
    assert session['used']['qa_calls'] == 1
    assert observations == [('generation', 'dispatched', 1), ('qa', 'dispatched', 1)]


def test_exhausted_image_budget_never_calls_provider(
        autopilot_store, monkeypatch, tmp_path):
    limits = {'paid_batches': 1, 'image_calls': 1, 'qa_calls': 1}
    _prepare('dispatch-session', limits=limits)
    _bind('dispatch-session', run='run-dispatch')
    seed = _reserve('dispatch-session', run='run-dispatch', call='seed')
    _dispatch('dispatch-session', 1, seed['reservation_id'])
    _finish('dispatch-session', 1, seed['reservation_id'], success=True)
    _patch_run_storage(monkeypatch, tmp_path)
    image_path = tmp_path / 'input.png'
    Image.new('RGB', (32, 24), 'gray').save(image_path)
    run = _development_run()
    result = {'result_id': 'result-1', 'trace': []}
    attempt = {'attempt_id': 'attempt-1', 'trace': []}
    routes_whole_home.state.model_semaphores['b2'] = asyncio.Semaphore(1)
    calls = []
    monkeypatch.setattr(
        routes_whole_home, 'call_image_generate',
        lambda *args, **kwargs: calls.append(True))
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as info:
        asyncio.run(routes_whole_home._call_generation(
            run, result, attempt, 'key', 'Nano Banana 2', 'model-b2', 'b2',
            'structure', 'prompt', [str(image_path)], '2K', '4:3'))
    assert info.value.code in {
        'development_budget_exhausted',
        'development_budget_envelope_exhausted',
    }
    assert calls == []
    assert run['call_ledger'][-1]['budget_status'] == 'denied'


def test_qa_retry_cannot_bypass_development_budget(
        autopilot_store, monkeypatch, tmp_path):
    limits = {'paid_batches': 1, 'image_calls': 1, 'qa_calls': 1}
    _prepare('qa-session', limits=limits)
    _bind('qa-session', run='run-qa')
    seed = _reserve('qa-session', run='run-qa', call='seed-qa', kind='qa')
    _dispatch('qa-session', 1, seed['reservation_id'])
    _finish('qa-session', 1, seed['reservation_id'], success=False, error='unavailable')
    image_path = tmp_path / 'result.png'
    Image.new('RGB', (32, 24), 'gray').save(image_path)
    attempt = {
        'attempt_id': 'attempt-1', 'capture_id': 'capture-1',
        'structure_path': str(image_path),
        'material_attempts': [{
            'material_attempt_id': 'material-1',
            'final_path': str(image_path), 'api_original_path': str(image_path),
            'qa_attempts': [],
        }],
    }
    result = {
        'result_id': 'result-1', 'camera_name': 'camera',
        'capture_id': 'capture-1', 'selected_attempt_id': 'attempt-1',
        'evaluation': {'status': 'unavailable'}, 'attempts': [attempt],
    }
    run = {
        **_development_run('qa-session'),
        'run_id': 'run-qa', 'status': 'done',
        'floor_path': '', 'project_snapshot': {'project_id': 'project-dispatch'},
        'capture_snapshots': [{'capture_id': 'capture-1'}],
        'results': [result],
    }
    monkeypatch.setattr(routes_whole_home, '_run_entry', lambda run_id: run)
    monkeypatch.setattr(routes_whole_home, '_persist_run', lambda value: None)
    calls = []
    monkeypatch.setattr(
        routes_whole_home, 'evaluate_whole_home_phase',
        lambda *args, **kwargs: calls.append(True))
    with pytest.raises(HTTPException) as info:
        asyncio.run(routes_whole_home.retry_whole_home_qa(
            'run-qa', server_schemas.WholeHomeQaRetryRequest(api_key='key')))
    assert info.value.status_code == 409
    assert info.value.detail['code'] == 'development_budget_envelope_exhausted'
    assert calls == []
    assert run['call_ledger'][-1]['budget_status'] == 'denied'


def test_cancel_route_marks_active_runs_without_touching_human_state(
        autopilot_store, monkeypatch):
    _prepare('cancel-session')
    _bind('cancel-session', run='run-active')
    run = {'run_id': 'run-active', 'status': 'running', 'stage': ''}
    monkeypatch.setitem(routes_whole_home._ACTIVE_RUNS, 'run-active', run)
    monkeypatch.setattr(routes_whole_home, '_persist_run', lambda value: None)
    try:
        response = routes_whole_home.cancel_development_autopilot_session('cancel-session')
        assert response['status'] == 'cancelled'
        assert 'run-active' in routes_whole_home._CANCELLED
        assert 'development_autopilot' in run['stage']
    finally:
        routes_whole_home._ACTIVE_RUNS.pop('run-active', None)
        routes_whole_home._CANCELLED.discard('run-active')


def test_generation_project_snapshot_excludes_large_cad_debug_histories():
    project = {
        'project_id': 'cad-project', 'source_type': 'cad', 'verified': True,
        'verified_revision': 4, 'revision': 4, 'floorplan_path': 'plan.png',
        'model': {'schema_version': 2, 'rooms': []},
        'reference_contract': {'contract_id': 'reference'},
        'auto_camera_plans': [{'plan_id': 'plan-1'}],
        'cad_reparse_failures': [{'raw': 'x' * 100_000}],
        'cad_error': {'raw': 'y' * 100_000},
        'parse_report': {'raw': 'z' * 100_000},
        'cad_semantic_attempts': [{'raw': 'q' * 100_000}],
        'captures': [{'capture_id': 'separate-snapshot'}],
        'operations': [{'type': 'history'}],
    }
    snapshot = routes_whole_home._generation_project_snapshot(project)
    assert snapshot['snapshot_format'] == 'whole_home_generation_minimal_v1'
    assert snapshot['model'] == project['model']
    assert snapshot['reference_contract'] == project['reference_contract']
    assert snapshot['auto_camera_plans'] == project['auto_camera_plans']
    for key in ('cad_reparse_failures', 'cad_error', 'parse_report',
                'cad_semantic_attempts', 'captures', 'operations'):
        assert key not in snapshot


def test_v2_reconciliation_is_dry_run_cas_conservative_and_idempotent(
        autopilot_store):
    _prepare('recover-session')
    _bind('recover-session', run='run-recover', run_status='running')
    reserved = _reserve(
        'recover-session', run='run-recover', call='reserved-call')
    dispatched = _reserve(
        'recover-session', run='run-recover', call='dispatched-call')
    _dispatch('recover-session', 1, dispatched['reservation_id'])
    before_bytes = (autopilot_store / 'recover-session.json').read_bytes()
    session = whole_home_autopilot.get_development_session('recover-session')
    run = {
        'run_id': 'run-recover', 'status': 'partial', 'error': 'restart',
        'call_ledger': [
            {'call_id': reserved['call_id'], 'budget_status': 'reserved'},
            {'call_id': dispatched['call_id'], 'budget_status': 'dispatched'},
        ],
    }
    preview = whole_home_autopilot.reconcile_development_session(
        'recover-session', [run], apply=False,
        expected_state_version=session['state_version'])
    assert preview['mode'] == 'dry_run'
    assert preview['summary'] == {
        'released_before_dispatch': 0,
        'uncertain_after_restart': 2,
        'terminal_reservations': 2,
        'terminal_batches': 1,
    }
    assert (autopilot_store / 'recover-session.json').read_bytes() == before_bytes

    applied = whole_home_autopilot.reconcile_development_session(
        'recover-session', [run], apply=True,
        expected_state_version=session['state_version'],
        idempotency_key='apply-recovery')
    assert applied['applied'] is True
    current = whole_home_autopilot.get_development_session('recover-session')
    statuses = {row['call_id']: row['status'] for row in current['reservations']}
    assert statuses == {
        'reserved-call': 'uncertain_after_restart',
        'dispatched-call': 'uncertain_after_restart',
    }
    assert current['reserved'] == {'image_calls': 0, 'qa_calls': 0}
    assert current['status'] == 'paused'
    assert current['batches'][0]['status'] == 'partial'
    repeated = whole_home_autopilot.reconcile_development_session(
        'recover-session', [run], apply=True,
        expected_state_version=current['state_version'],
        idempotency_key='apply-recovery')
    assert repeated['idempotent_noop'] is True
    assert repeated['applied'] is False


def test_run_claim_is_atomic_idempotent_and_covers_worst_case_envelope(
        autopilot_store):
    limits = {'paid_batches': 2, 'image_calls': 12, 'qa_calls': 24}
    _prepare('claim-session', limits=limits)
    envelope = {
        'result_count': 2, 'model_keys': ['b2', 'pro'],
        'candidates_per_camera': 1,
        'image_calls_min': 4, 'image_calls_max': 12,
        'qa_calls_min': 4, 'qa_calls_max': 24,
    }
    first = whole_home_autopilot.claim_development_run(
        session_id='claim-session', batch_index=1,
        request_fingerprint='fingerprint-1', budget_envelope=envelope)
    repeated = whole_home_autopilot.claim_development_run(
        session_id='claim-session', batch_index=1,
        request_fingerprint='fingerprint-1', budget_envelope=envelope)
    assert first['run_claim_id'] == repeated['run_claim_id']
    assert first['claim_token']
    assert repeated['claim_token'] == ''
    assert repeated['claim_replayed'] is True
    whole_home_autopilot.bind_development_run(
        'claim-session', 1, 'run-claimed',
        run_claim_id=first['run_claim_id'],
        claim_generation=first['claim_generation'],
        claim_token=first['claim_token'],
        request_fingerprint='fingerprint-1')
    _prepare('claim-session', batch=2, limits=limits)
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as busy:
        whole_home_autopilot.claim_development_run(
            session_id='claim-session', batch_index=2,
            request_fingerprint='fingerprint-2', budget_envelope=envelope)
    assert busy.value.code == 'development_run_claim_busy'


def test_run_claim_rejects_when_worst_case_exceeds_remaining(autopilot_store):
    _prepare('small-envelope', limits={
        'paid_batches': 1, 'image_calls': 2, 'qa_calls': 2})
    with pytest.raises(whole_home_autopilot.DevelopmentAutopilotError) as info:
        whole_home_autopilot.claim_development_run(
            session_id='small-envelope', batch_index=1,
            request_fingerprint='fingerprint-1', budget_envelope={
                'result_count': 2, 'model_keys': ['b2', 'pro'], 'candidates_per_camera': 1,
                'image_calls_min': 4, 'image_calls_max': 12,
                'qa_calls_min': 4, 'qa_calls_max': 24,
            })
    assert info.value.code == 'development_budget_envelope_unavailable'
    session = whole_home_autopilot.get_development_session('small-envelope')
    assert session['used'] == {'paid_batches': 0, 'image_calls': 0, 'qa_calls': 0}
    assert session['reservations'] == []
