# -*- coding: utf-8 -*-
import asyncio
import copy
import json
from contextlib import contextmanager

import pytest
from starlette.requests import Request
from starlette.responses import Response

from Floor_engine_server import (
    routes_whole_home,
    server_api,
    server_schemas,
    whole_home_learning,
    whole_home_manual,
)
from Floor_engine_server.whole_home_autopilot import DevelopmentAutopilotError
from Floor_engine_server.whole_home_dev_lock import WholeHomeStateLockTimeout


_FLAG_NAMES = (
    whole_home_manual.MANUAL_SAFE_ENV,
    whole_home_manual.MANUAL_PAID_ENV,
    whole_home_manual.WORKFLOW_ENV,
    whole_home_manual.EXTERNAL_REVIEW_ENV,
    whole_home_manual.REFERENCE_ENV,
    whole_home_manual.DEVELOPMENT_PAID_ENV,
)


@pytest.fixture(autouse=True)
def clean_manual_flags_and_previews(monkeypatch):
    for name in _FLAG_NAMES:
        monkeypatch.delenv(name, raising=False)
    whole_home_manual._PREVIEWS.clear()
    yield
    whole_home_manual._PREVIEWS.clear()


def _manual_project(tmp_path):
    floorplan = tmp_path / 'floorplan.png'
    floor = tmp_path / 'floor.png'
    rgb = tmp_path / 'rgb.png'
    depth = tmp_path / 'depth.png'
    normal = tmp_path / 'normal.png'
    semantic = tmp_path / 'semantic.png'
    for index, path in enumerate((floorplan, floor, rgb, depth, normal, semantic)):
        path.write_bytes(f'image-{index}'.encode('ascii'))
    project = {
        'project_id': 'project-manual', 'verified': True,
        'revision': 9, 'verified_revision': 9,
        'floorplan_path': str(floorplan),
        'captures': [{
            'capture_id': 'capture-one', 'status': 'confirmed',
            'aspect_ratio': '4:3', 'rgb_path': str(rgb),
            'depth_path': str(depth), 'normal_path': str(normal),
            'edge_path': '', 'semantic_path': str(semantic),
        }],
    }
    request = {
        'project_id': project['project_id'], 'capture_ids': ['capture-one'],
        'capture_groups': [], 'floor_path': str(floor),
        'material_mode': 'floor_sample', 'reference_contract_id': '',
        'benchmark_batch_id': '', 'style_ref_path': None,
        'prompt': '', 'style': '现代自然', 'lighting': '自然日光',
        'model_keys': ['b2'], 'candidates_per_camera': 1,
        'aspect_ratio': '4:3', 'resolution': '2K',
        'idempotency_key': 'manual-test-one',
    }
    return project, request, floor


def test_manual_safe_flags_default_closed_and_paths_are_independent():
    value = whole_home_manual.capabilities({})
    assert value['manual_safe'] is True
    assert value['manual_paid'] is False
    assert set(value['feature_flags'].values()) == {False}
    assert whole_home_manual.request_feature_for_path(
        '/api/whole-home/runs', 'POST', environ={}) == 'ordinary_paid_run'
    assert whole_home_manual.request_feature_for_path(
        '/api/whole-home/manual/runs/preview', 'POST', environ={}) == ''
    assert whole_home_manual.request_feature_for_path(
        '/api/whole-home/development-workflows', 'POST', environ={}) == 'agent_workflow'
    assert whole_home_manual.request_feature_for_path(
        '/api/whole-home/development-reviews/runs/x', 'GET', environ={}) == 'external_review'
    assert whole_home_manual.request_feature_for_path(
        '/api/whole-home/development-autopilot/sessions/x', 'GET',
        environ={}) == 'development_paid'
    assert whole_home_manual.request_feature_for_path(
        '/api/whole-home/projects/x/reference-assets/slot', 'GET',
        environ={}) == 'reference_wip'
    assert whole_home_manual.request_feature_for_path(
        '/api/whole-home/projects/x/captures', 'POST',
        body={'reference_slot_id': 'slot'}, environ={}) == 'reference_wip'


def test_http_middleware_reference_gate_does_not_forward_disabled_body():
    raw = json.dumps({'mode': 'reference'}).encode('utf-8')
    delivered = False

    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}

    async def call_next(request):
        nonlocal delivered
        delivered = True
        return Response(status_code=204)

    request = Request({
        'type': 'http', 'http_version': '1.1', 'method': 'POST',
        'scheme': 'http', 'path': '/api/whole-home/projects/x/camera-candidates',
        'raw_path': b'/api/whole-home/projects/x/camera-candidates',
        'query_string': b'', 'headers': [],
        'server': ('testserver', 80), 'client': ('127.0.0.1', 1234),
    }, receive)
    response = asyncio.run(
        server_api.reject_cross_origin_mutations(request, call_next))
    assert response.status_code == 404
    assert delivered is False

    room_raw = json.dumps({'mode': 'room'}).encode('utf-8')
    seen = {}

    async def room_receive():
        return {'type': 'http.request', 'body': room_raw, 'more_body': False}

    async def room_next(room_request):
        seen.update(json.loads((await room_request.body()).decode('utf-8')))
        return Response(status_code=204)

    room_request = Request({
        'type': 'http', 'http_version': '1.1', 'method': 'POST',
        'scheme': 'http', 'path': '/api/whole-home/projects/x/camera-candidates',
        'raw_path': b'/api/whole-home/projects/x/camera-candidates',
        'query_string': b'', 'headers': [],
        'server': ('testserver', 80), 'client': ('127.0.0.1', 1234),
    }, room_receive)
    room_response = asyncio.run(
        server_api.reject_cross_origin_mutations(room_request, room_next))
    assert room_response.status_code == 204
    assert seen == {'mode': 'room'}


def test_manual_schema_requires_one_capture_one_model_2k_floor_sample():
    base = {
        'project_id': 'project', 'capture_ids': ['capture'],
        'floor_path': 'floor.png', 'model_keys': ['b2'],
        'idempotency_key': 'schema-one',
    }
    value = server_schemas.WholeHomeManualRunPreviewRequest.model_validate(base)
    assert value.resolution == '2K'
    assert value.candidates_per_camera == 1
    for patch in (
        {'capture_ids': []}, {'capture_ids': ['a', 'b']},
        {'model_keys': []}, {'model_keys': ['b2', 'pro']},
        {'resolution': '4K'}, {'material_mode': 'reference'},
        {'capture_groups': [{'room_id': 'r', 'primary_capture_id': 'c'}]},
    ):
        with pytest.raises(Exception):
            server_schemas.WholeHomeManualRunPreviewRequest.model_validate(
                {**base, **patch})


def test_manual_style_pack_preview_requires_locked_scene_bound_capture(tmp_path):
    project, request, _ = _manual_project(tmp_path)
    recipe = {
        'recipe_id': 'scene-locked', 'scene_hash': 's' * 64,
        'recipe_hash': 'r' * 64, 'status': 'locked',
    }
    project.update(scene_recipes=[recipe], active_scene_recipe_id=recipe['recipe_id'])
    project['captures'][0].update(
        scene_recipe_id=recipe['recipe_id'], scene_hash=recipe['scene_hash'])
    request.update(
        material_mode='style_pack', scene_recipe_id=recipe['recipe_id'],
        floor_path='', style_ref_path=None)

    schema = server_schemas.WholeHomeManualRunPreviewRequest.model_validate(request)
    assert schema.material_mode == 'style_pack' and not schema.floor_path
    preview = whole_home_manual.create_manual_run_preview(project=project, request=request)
    assert preview['request']['scene_recipe_id'] == recipe['recipe_id']
    assert {row['label'] for row in preview['input_manifest']} == {
        'floorplan', 'capture:capture-one:rgb_path', 'capture:capture-one:depth_path',
        'capture:capture-one:normal_path', 'capture:capture-one:edge_path',
        'capture:capture-one:semantic_path',
    }

    project['captures'][0]['scene_hash'] = 'old-scene'
    with pytest.raises(DevelopmentAutopilotError) as stale:
        whole_home_manual.create_manual_run_preview(project=project, request=request)
    assert stale.value.code == 'manual_capture_scene_mismatch'


def test_preview_rehashes_every_input_and_paid_commit_is_one_time(
        monkeypatch, tmp_path):
    project, request, floor = _manual_project(tmp_path)
    preview = whole_home_manual.create_manual_run_preview(
        project=project, request=request)
    assert preview['paid_enabled'] is False
    assert preview['caps'] == {'image_calls': 4, 'qa_calls': 8}
    assert len(preview['input_manifest']) == 7
    with pytest.raises(DevelopmentAutopilotError) as paid_off:
        whole_home_manual.claim_manual_run_commit(
            preview_id=preview['preview_id'],
            preview_sha256=preview['preview_sha256'],
            confirmation_phrase=preview['confirmation_phrase'],
            project=project)
    assert paid_off.value.code == 'manual_paid_not_enabled'

    monkeypatch.setenv(whole_home_manual.MANUAL_PAID_ENV, '1')
    floor.write_bytes(b'changed-after-preview')
    with pytest.raises(DevelopmentAutopilotError) as changed:
        whole_home_manual.claim_manual_run_commit(
            preview_id=preview['preview_id'],
            preview_sha256=preview['preview_sha256'],
            confirmation_phrase=preview['confirmation_phrase'],
            project=project)
    assert changed.value.code == 'manual_preview_inputs_changed'

    fresh = whole_home_manual.create_manual_run_preview(
        project=project, request=request)
    claim = whole_home_manual.claim_manual_run_commit(
        preview_id=fresh['preview_id'],
        preview_sha256=fresh['preview_sha256'],
        confirmation_phrase=fresh['confirmation_phrase'],
        project=project)
    assert claim['request']['model_keys'] == ['b2']
    whole_home_manual.finish_manual_run_commit(
        fresh['preview_id'], success=True, run_id='run-one')
    with pytest.raises(DevelopmentAutopilotError) as repeated:
        whole_home_manual.claim_manual_run_commit(
            preview_id=fresh['preview_id'],
            preview_sha256=fresh['preview_sha256'],
            confirmation_phrase=fresh['confirmation_phrase'],
            project=project)
    assert repeated.value.code == 'manual_preview_already_consumed'


def test_manual_commit_route_uses_server_preview_and_mocked_dispatch_only(
        monkeypatch, tmp_path):
    project, request, _ = _manual_project(tmp_path)
    monkeypatch.setenv(whole_home_manual.MANUAL_PAID_ENV, '1')
    preview = whole_home_manual.create_manual_run_preview(
        project=project, request=request)
    monkeypatch.setattr(
        routes_whole_home, '_project_entry',
        lambda project_id: copy.deepcopy(project))
    captured = {}

    async def mocked_create(req, metadata):
        captured['request'] = req.model_dump(exclude={'api_key'})
        captured['metadata'] = copy.deepcopy(metadata)
        return {'run_id': 'mock-run', 'status': 'queued'}

    monkeypatch.setattr(routes_whole_home, '_create_whole_home_run', mocked_create)
    response = asyncio.run(routes_whole_home.commit_whole_home_manual_run(
        server_schemas.WholeHomeManualRunCommitRequest(
            preview_id=preview['preview_id'],
            preview_sha256=preview['preview_sha256'],
            confirmation_phrase=preview['confirmation_phrase'])))
    assert response['run_id'] == 'mock-run'
    assert captured['metadata']['execution_policy'] == 'manual_safe_v1'
    assert captured['request']['capture_ids'] == ['capture-one']
    assert captured['request']['resolution'] == '2K'


def test_manual_call_caps_block_before_another_logical_call():
    run = {
        'execution_policy': whole_home_manual.MANUAL_POLICY,
        'manual_call_caps': {'image_calls': 4, 'qa_calls': 8},
        'call_ledger': [
            *[{'kind': 'generation'} for _ in range(4)],
            *[{'kind': 'qa'} for _ in range(8)],
        ],
    }
    for kind in ('generation', 'qa'):
        with pytest.raises(DevelopmentAutopilotError) as blocked:
            routes_whole_home._assert_manual_call_cap(run, kind)
        assert blocked.value.code == 'manual_call_cap_exhausted'


def test_get_review_state_is_observational(monkeypatch):
    monkeypatch.setattr(
        whole_home_learning, 'ensure_run_recipes',
        lambda run: (_ for _ in ()).throw(AssertionError('GET wrote recipe')))
    monkeypatch.setattr(
        whole_home_learning, '_review_state',
        lambda run: {'run_id': run['run_id'], 'round_status': 'review_not_required'})
    assert whole_home_learning.get_run_review_state(
        {'run_id': 'terminal', 'status': 'done'})['run_id'] == 'terminal'


def test_manual_service_owner_rejects_second_owner(tmp_path):
    with whole_home_manual.service_owner(str(tmp_path), timeout=0.05):
        with pytest.raises(WholeHomeStateLockTimeout):
            with whole_home_manual.service_owner(str(tmp_path), timeout=0.01):
                pass


def test_manual_lifespan_skips_authoritative_startup_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(server_api, 'MAIN_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(server_api, 'manual_safe_enabled', lambda: True)
    for name in (
        'migrate_all_record_storage', 'load_persisted_jobs',
        'recover_interrupted_floorplan_state',
        'recover_interrupted_whole_home_state',
    ):
        monkeypatch.setattr(
            server_api, name,
            lambda *args, _name=name, **kwargs: (_ for _ in ()).throw(
                AssertionError(f'{_name} must not run')))

    async def exercise():
        async with server_api.lifespan(None):
            return True

    assert asyncio.run(exercise()) is True


def test_all_service_modes_hold_owner_before_recovery(monkeypatch, tmp_path):
    from contextlib import contextmanager

    held = {'value': False}

    @contextmanager
    def owner(data_root, timeout):
        assert data_root == str(tmp_path)
        held['value'] = True
        try:
            yield 'lock'
        finally:
            held['value'] = False

    monkeypatch.setattr(server_api, 'MAIN_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(server_api, 'service_owner', owner)
    monkeypatch.setattr(server_api, 'manual_safe_enabled', lambda: False)
    monkeypatch.setattr(server_api, 'migrate_all_record_storage',
                        lambda: 0 if held['value'] else pytest.fail('migration ran before owner'))
    monkeypatch.setattr(server_api, 'load_persisted_jobs', lambda: [])
    monkeypatch.setattr(server_api, 'recover_interrupted_floorplan_state', lambda: (0, 0))
    monkeypatch.setattr(server_api, 'recover_interrupted_whole_home_state', lambda: (0, 0))
    monkeypatch.setattr(server_api, 'development_autopilot_enabled', lambda: False)

    async def exercise():
        async with server_api.lifespan(None):
            assert held['value'] is True

    asyncio.run(exercise())
    assert held['value'] is False


def _legacy_session_document():
    reservations = []
    counter = 0

    def add(count, kind, status):
        nonlocal counter
        for _ in range(count):
            counter += 1
            reservations.append({
                'reservation_id': f'reservation-{counter}',
                'call_id': f'call-{counter}', 'run_id': 'legacy-run',
                'kind': kind, 'status': status,
            })

    add(64, 'generation', 'done')
    add(41, 'qa', 'done')
    add(14, 'generation', 'reserved')
    add(3, 'generation', 'dispatched')
    add(1, 'qa', 'dispatched')
    return {
        'schema_version': 1, 'session_id': 'legacy-session',
        'project_id': 'project', 'status': 'running', 'stop_reason': '',
        'state_version': 7,
        'limits': {'paid_batches': 6, 'image_calls': 140, 'qa_calls': 280},
        'batches': [{
            'batch_index': index, 'status': 'running',
            'paid_started_at': float(index), 'run_id': 'legacy-run',
        } for index in range(1, 4)],
        'runs': ['legacy-run'], 'reservations': reservations,
        'reconciliations': [], 'migration_history': [],
    }


def test_legacy_reconciliation_dry_run_exact_budget_then_guarded_temp_apply(
        tmp_path):
    session_path = tmp_path / 'legacy-session.json'
    session_path.write_text(
        json.dumps(_legacy_session_document()), encoding='utf-8')
    run_path = tmp_path / 'legacy-run.json'
    run_path.write_text(json.dumps({
        'run_id': 'legacy-run', 'status': 'running', 'call_ledger': [],
    }), encoding='utf-8')
    before = session_path.read_bytes()
    with pytest.raises(DevelopmentAutopilotError) as incomplete:
        whole_home_manual.preview_legacy_reconciliation(
            session_path=str(session_path), run_paths=[])
    assert incomplete.value.code == 'manual_reconcile_run_manifest_incomplete'
    assert session_path.read_bytes() == before
    preview = whole_home_manual.preview_legacy_reconciliation(
        session_path=str(session_path), run_paths=[str(run_path)])
    projected = preview['plan']['projected_session']
    assert session_path.read_bytes() == before
    assert preview['plan']['summary']['uncertain_after_restart'] == 18
    assert projected['status'] == 'paused'
    assert projected['used'] == {
        'paid_batches': 3, 'image_calls': 81, 'qa_calls': 42}
    assert projected['remaining'] == {
        'paid_batches': 3, 'image_calls': 59, 'qa_calls': 238}
    assert projected['reserved'] == {'image_calls': 0, 'qa_calls': 0}
    backup_dir = tmp_path / 'backups'
    applied = whole_home_manual.apply_legacy_reconciliation(
        session_path=str(session_path), run_paths=[str(run_path)],
        expected_session_sha256=preview['session_sha256'],
        expected_run_manifest_sha256=preview['run_manifest_sha256'],
        expected_state_version=preview['state_version'],
        expected_plan_hash=preview['plan_hash'],
        idempotency_key='manual-legacy-apply-one',
        confirmation_phrase=preview['confirmation_phrase'],
        backup_dir=str(backup_dir))
    assert applied['provider_imported'] is False
    assert applied['provider_calls'] == 0
    backup_path = backup_dir / (
        f'legacy-session.{preview["session_sha256"]}.pre-manual-reconcile.json')
    assert backup_path.read_bytes() == before
    current = json.loads(session_path.read_text('utf-8'))
    assert current['status'] == 'paused'
