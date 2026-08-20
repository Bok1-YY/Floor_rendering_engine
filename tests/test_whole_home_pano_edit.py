# -*- coding: utf-8 -*-
"""pano edit/repair 路由的 mock 单测:paid 政策、source_hash、2:1 契约、账本。

call_gpt_image_edit 全部 mock,不发起任何真实付费调用。
"""
from __future__ import annotations

import copy
import os

import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import routes_whole_home as rwh
from Floor_engine_server.server_schemas import (
    WholeHomePanoEditRequest, WholeHomePanoGateRequest,
    WholeHomePanoMaterializeRequest, WholeHomePanoPaidPreviewRequest,
    WholeHomePanoReviewRequest,
)
from Floor_engine_server.whole_home_engine import pano_file_sha256, pano_manifest_hash
from Floor_engine_server.records import load_records_file
from Floor_engine_server.whole_home_pano_paid import (
    claim_pano_paid_stage, create_pano_paid_preview, persistable_pano_paid_preview,
    reset_pano_paid_previews_for_tests, restore_pano_paid_preview,
)


def _make_manifest():
    manifest = {
        'schema_version': 1, 'pano_id': 'pano_1', 'projection': 'equirectangular',
        'coordinate_system': 'right-handed-y-up',
        'camera_center_m': {'x': 4.25, 'y': 1.55, 'z': 3.8},
        'canonical_forward': '+Z', 'heading_deg': 0, 'pitch_deg': 0, 'roll_deg': 0,
        'horizontal_fov_deg': 360, 'vertical_fov_deg': 180,
        'erp_width': 128, 'erp_height': 64, 'cube_face_size': 32,
        'cube_face_order': ['+X', '-X', '+Y', '-Y', '+Z', '-Z'],
        'near_m': 0.05, 'far_m': 30.0,
        'depth_encoding': 'linear_metric_global_range',
        'normal_encoding': 'world_space_xyz_to_rgb',
        'model_facts_hash': 'm', 'material_graph_hash': 'g', 'lighting_hash': 'l',
        'capture_id': 'panocap_test', 'capture_revision': 1,
        'render_contract': {'materials': {'clay': 1}, 'lighting': {'sun': 1}},
        'channels': {}, 'channel_hashes': {}, 'source_hash': '',
    }
    return manifest


@pytest.fixture
def pano_project(tmp_path, monkeypatch):
    manifest = _make_manifest()
    erp_paths = {}
    for index, kind in enumerate(('rgb', 'depth', 'normal', 'edge', 'semantic', 'subject_id')):
        path = tmp_path / f'{kind}_erp.png'
        Image.new('RGB', (128, 64), (200 - index * 10,) * 3).save(path)
        erp_paths[kind] = path
    manifest['channels'] = {
        'rgb_erp': str(erp_paths['rgb']), 'depth_erp': str(erp_paths['depth']),
        'normal_erp': str(erp_paths['normal']), 'edge_erp': str(erp_paths['edge']),
        'semantic_erp': str(erp_paths['semantic']), 'subject_id_erp': str(erp_paths['subject_id']),
        'rgb_atlas': '', 'depth_atlas': '', 'normal_atlas': '',
        'edge_atlas': '', 'semantic_atlas': '', 'subject_id_atlas': '',
    }
    manifest['channel_hashes'] = {
        f'{kind}_erp': pano_file_sha256(str(path)) for kind, path in erp_paths.items()
    }
    manifest['source_hash'] = pano_manifest_hash(manifest)
    capture = {
        'capture_id': 'panocap_test', 'capture_revision': 1, 'active': True,
        'pano_id': 'pano_1', 'camera': {}, 'camera_center_m': {'x': 4.25, 'y': 1.55, 'z': 3.8},
        'manifest': manifest, 'room_id': 'r1', 'status': 'confirmed', 'created_at': 0,
    }
    project = {
        'project_id': 'proj-pano', 'verified': True,
        'model': {'walls': [], 'rooms': [], 'cameras': []},
        'pano_captures': [capture], 'pano_calls': [],
    }
    rwh._ACTIVE_PROJECTS['proj-pano'] = project
    persisted: dict = {}

    def fake_persist(value):
        persisted.clear()
        persisted.update(copy.deepcopy(value))
        rwh._ACTIVE_PROJECTS['proj-pano'] = copy.deepcopy(value)

    monkeypatch.setattr(rwh, '_persist_project', fake_persist)

    def fake_save(project_id, pano_id, kind, image, **kwargs):
        path = tmp_path / f'{kind}.png'
        image.save(path)
        return str(path)

    monkeypatch.setattr(rwh, 'save_pano_image_file', fake_save)
    monkeypatch.setattr(rwh, 'manual_safe_enabled', lambda: True)
    monkeypatch.setattr(rwh, 'manual_paid_enabled', lambda: True)
    monkeypatch.setattr(rwh, 'GPT_IMAGE_2_ERP_WIDTH', 128)
    monkeypatch.setattr(rwh, 'GPT_IMAGE_2_ERP_HEIGHT', 64)
    reset_pano_paid_previews_for_tests()
    return {'tmp': tmp_path, 'manifest': manifest, 'capture': capture,
            'project': project, 'persisted': persisted}


def _edit_request(**overrides):
    payload = {'pano_id': 'pano_1', 'source_hash': '',
               'preview_id': 'panopreview_0000000000000000',
               'confirmation_phrase': '确认全景付费 placeholder',
               'annotator_id': 'local-user'}
    payload.update(overrides)
    return WholeHomePanoEditRequest(**payload)


def _confirmed_request(pano_project):
    source_hash = pano_project['manifest']['source_hash']
    preview = create_pano_paid_preview(
        project_id='proj-pano', pano_id='pano_1', source_hash=source_hash,
        provider='fal', endpoint='openai/gpt-image-2/edit', model_id='gpt-image-2',
        output_size='128x64', edit_prompt='test prompt', repair_band_deg=12, actor='test')
    return _edit_request(
        source_hash=source_hash, preview_id=preview['preview_id'],
        confirmation_phrase=preview['confirmation_phrase']), preview


def test_pano_edit_blocked_without_paid(pano_project, monkeypatch):
    monkeypatch.setattr(rwh, 'manual_paid_enabled', lambda: False)
    with pytest.raises(HTTPException) as exc:
        rwh.edit_whole_home_pano('proj-pano', 'pano_1',
                                 _edit_request(source_hash=pano_project['manifest']['source_hash']))
    assert exc.value.status_code == 402
    assert exc.value.detail['code'] == 'pano_edit_paid_disabled'


def test_pano_local_materialize_is_free_and_audited(pano_project):
    result = rwh.materialize_whole_home_pano(
        'proj-pano', 'pano_1', WholeHomePanoMaterializeRequest(
            source_hash=pano_project['manifest']['source_hash'],
            preset='warm-contemporary', annotator_id='test'))
    assert result.get('pano_captures')
    persisted = pano_project['persisted']
    capture = persisted['pano_captures'][0]
    assert capture['status'] == 'edited'
    assert capture['edit_engine'] == 'geometry-material-v1'
    assert os.path.isfile(capture['edited_rgb_path'])
    calls = persisted['pano_calls']
    assert len(calls) == 1 and calls[0]['provider'] == 'local'
    assert calls[0]['extra']['cost_usd'] == 0
    assert calls[0]['extra']['geometry_locked'] is True
    assert persisted['operations'][-1]['type'] == 'pano_materialize'


def test_pano_edit_success_records_ledger(pano_project, monkeypatch):
    monkeypatch.setattr(rwh, 'call_gpt_image_edit',
                        lambda *args, **kwargs: (Image.new('RGB', (128, 64), (10, 20, 30)), None))
    request, _ = _confirmed_request(pano_project)
    result = rwh.edit_whole_home_pano('proj-pano', 'pano_1', request)
    persisted = pano_project['persisted']
    capture = persisted['pano_captures'][0]
    assert capture['status'] == 'edited'
    assert os.path.isfile(capture['edited_rgb_path'])
    calls = persisted.get('pano_calls') or []
    assert len(calls) == 1
    assert calls[0]['success'] is True and calls[0]['kind'] == 'edit'
    assert calls[0]['model_id'] == 'gpt-image-2'
    assert calls[0]['endpoint'] == 'openai/gpt-image-2/edit'
    assert calls[0]['snapshot_locked'] is False
    assert result.get('pano_captures') or result.get('project') is not None


def test_pano_edit_rejects_broken_2_1(pano_project, monkeypatch):
    monkeypatch.setattr(rwh, 'call_gpt_image_edit',
                        lambda *args, **kwargs: (Image.new('RGB', (100, 50), (0, 0, 0)), None))
    request, _ = _confirmed_request(pano_project)
    with pytest.raises(HTTPException) as exc:
        rwh.edit_whole_home_pano('proj-pano', 'pano_1', request)
    assert exc.value.status_code == 502
    assert exc.value.detail['code'] == 'pano_erp_size_contract_broken'
    calls = pano_project['persisted'].get('pano_calls') or []
    assert calls and calls[-1]['success'] is False


def test_pano_edit_source_hash_mismatch(pano_project):
    with pytest.raises(HTTPException) as exc:
        rwh.edit_whole_home_pano('proj-pano', 'pano_1',
                                 _edit_request(source_hash='f' * 64))
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'pano_source_hash_mismatch'


def test_pano_edit_provider_failure_recorded(pano_project, monkeypatch):
    monkeypatch.setattr(rwh, 'call_gpt_image_edit', lambda *args, **kwargs: (None, '模型不可用'))
    request, _ = _confirmed_request(pano_project)
    with pytest.raises(HTTPException) as exc:
        rwh.edit_whole_home_pano('proj-pano', 'pano_1', request)
    assert exc.value.status_code == 502
    calls = pano_project['persisted'].get('pano_calls') or []
    assert calls and calls[-1]['success'] is False and calls[-1]['error'] == '模型不可用'


def test_pano_edit_resumes_same_queue_request_after_result_download_failure(
        pano_project, monkeypatch):
    attempts = []
    queue_handle = {
        'endpoint': 'openai/gpt-image-2/edit', 'request_id': 'durable-request-1',
        'status_url': 'https://queue.fal.run/openai/gpt-image-2/edit/requests/1/status',
        'response_url': 'https://queue.fal.run/openai/gpt-image-2/edit/requests/1',
        'cancel_url': 'https://queue.fal.run/openai/gpt-image-2/edit/requests/1/cancel',
    }

    def flaky_edit(*args, **kwargs):
        attempts.append(copy.deepcopy(kwargs))
        if len(attempts) == 1:
            kwargs['on_submitted'](queue_handle)
            return None, '解码失败: connection reset'
        assert kwargs['resume_handle']['request_id'] == 'durable-request-1'
        return Image.new('RGB', (128, 64), (10, 20, 30)), None

    monkeypatch.setattr(rwh, 'call_gpt_image_edit', flaky_edit)
    request, preview = _confirmed_request(pano_project)
    with pytest.raises(HTTPException) as exc:
        rwh.edit_whole_home_pano('proj-pano', 'pano_1', request)
    assert exc.value.status_code == 502
    failed = pano_project['persisted']['pano_calls'][0]
    assert failed['status'] == 'failed' and failed['request_id'] == 'durable-request-1'

    monkeypatch.setattr(rwh, 'load_config', lambda: {
        'fal_api_key': 'configured-test-key',
        'fal_gpt_image_endpoint': 'openai/gpt-image-2/edit',
    })
    recovered = rwh.preview_whole_home_pano_edit(
        'proj-pano', 'pano_1', WholeHomePanoPaidPreviewRequest(
            source_hash=preview['source_hash'], provider='fal', engine='gpt-image-2',
            model_id='gpt-image-2', edit_instruction='', style_description='modern',
            repair_band_deg=12, annotator_id='test'))
    assert recovered['preview_id'] == preview['preview_id']
    assert recovered['resume_only'] is True
    assert recovered['resume_request_id'] == 'durable-request-1'

    result = rwh.edit_whole_home_pano('proj-pano', 'pano_1', request)
    assert result.get('pano_captures')
    calls = pano_project['persisted']['pano_calls']
    assert len(calls) == 1
    assert calls[0]['status'] == 'succeeded' and calls[0]['resume_attempts'] == 1
    assert len(attempts) == 2


def test_pano_repair_success(pano_project, monkeypatch):
    edited_path = pano_project['tmp'] / 'edited_rgb.png'
    Image.new('RGB', (128, 64), (50, 60, 70)).save(edited_path)
    pano_project['capture']['edited_rgb_path'] = str(edited_path)
    pano_project['capture']['gate'] = {
        'gate_pass': False, 'failures': ['wrap_seam'], 'version': 'whole-home-pano-gate-v1'}
    request, preview = _confirmed_request(pano_project)
    claim_pano_paid_stage(
        preview['preview_id'], preview['confirmation_phrase'], stage='edit',
        project_id='proj-pano', pano_id='pano_1', source_hash=preview['source_hash'])
    pano_project['project']['pano_calls'].append({
        'call_id': 'prior-edit', 'capture_id': 'panocap_test', 'kind': 'edit', 'status': 'succeeded'})
    monkeypatch.setattr(rwh, 'call_gpt_image_edit',
                        lambda *args, **kwargs: (Image.new('RGB', (128, 64), (80, 90, 100)), None))
    result = rwh.repair_whole_home_pano_seam(
        'proj-pano', 'pano_1',
        request)
    persisted = pano_project['persisted']
    capture = persisted['pano_captures'][0]
    assert capture['status'] == 'repaired'
    assert os.path.isfile(capture['repaired_rgb_path'])
    calls = persisted.get('pano_calls') or []
    assert calls and calls[-1]['kind'] == 'repair' and calls[-1]['success'] is True
    assert result.get('pano_captures') or result.get('project') is not None


def test_pano_paid_preview_is_free_and_binds_actual_fal_endpoint(pano_project, monkeypatch):
    provider_called = False

    def forbidden_provider(*args, **kwargs):
        nonlocal provider_called
        provider_called = True

    monkeypatch.setattr(rwh, 'call_gpt_image_edit', forbidden_provider)
    monkeypatch.setattr(rwh, 'load_config', lambda: {
        'fal_api_key': 'configured-test-key',
        'fal_gpt_image_endpoint': 'openai/gpt-image-2/edit',
    })
    preview = rwh.preview_whole_home_pano_edit(
        'proj-pano', 'pano_1', WholeHomePanoPaidPreviewRequest(
            source_hash=pano_project['manifest']['source_hash'], provider='fal',
            model_id='gpt-image-2', edit_instruction='', style_description='modern',
            repair_band_deg=12, annotator_id='test'))
    assert provider_called is False
    assert preview['endpoint'] == 'openai/gpt-image-2/edit'
    assert preview['snapshot_locked'] is False
    assert preview['caps'] == {'edit_calls': 1, 'repair_calls': 1}
    assert 'edit_prompt' not in preview
    assert pano_project['persisted']['pano_paid_previews'][0]['edit_prompt']


def test_flux_canny_preview_and_edit_use_structure_control_path(pano_project, monkeypatch):
    monkeypatch.setattr(rwh, 'load_config', lambda: {
        'fal_api_key': 'configured-test-key',
        'fal_flux_canny_erp_endpoint': 'fal-ai/flux-control-lora-canny/image-to-image',
    })
    monkeypatch.setattr(rwh, 'FLUX_CANNY_ERP_CORE_WIDTH', 1024)
    monkeypatch.setattr(rwh, 'FLUX_CANNY_ERP_CORE_HEIGHT', 512)
    monkeypatch.setattr(rwh, 'FLUX_CANNY_ERP_GUTTER_PX', 32)
    monkeypatch.setattr(rwh, 'FLUX_CANNY_ERP_PROVIDER_WIDTH', 1088)
    monkeypatch.setattr(rwh, 'FLUX_CANNY_ERP_PROVIDER_HEIGHT', 512)
    monkeypatch.setattr(rwh, '_PANO_FLUX_CANNY_PARAMS', {
        'core_width': 1024, 'core_height': 512, 'gutter_px': 32,
        'provider_width': 1088, 'provider_height': 512,
        'seed': 24681357, 'strength': .62, 'control_lora_strength': 1.25,
        'num_inference_steps': 32, 'guidance_scale': 3.0,
        'estimated_cost_usd': .08,
    })
    observed = {}

    def fake_flux(*args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return Image.new('RGB', (1088, 512), (30, 40, 50)), None

    monkeypatch.setattr(rwh, 'call_fal_flux_canny_edit', fake_flux)
    monkeypatch.setattr(
        rwh, 'call_gpt_image_edit',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('GPT path must not run')))
    preview = rwh.preview_whole_home_pano_edit(
        'proj-pano', 'pano_1', WholeHomePanoPaidPreviewRequest(
            source_hash=pano_project['manifest']['source_hash'], provider='fal',
            engine='flux-canny', model_id='flux-control-lora-canny',
            edit_instruction='', style_description='warm contemporary',
            repair_band_deg=12, annotator_id='test'))
    assert preview['engine'] == 'flux-canny'
    assert preview['output_size'] == '1088x512'
    assert preview['caps'] == {'edit_calls': 1, 'repair_calls': 0}
    request = _edit_request(
        source_hash=preview['source_hash'], preview_id=preview['preview_id'],
        confirmation_phrase=preview['confirmation_phrase'])
    rwh.edit_whole_home_pano('proj-pano', 'pano_1', request)
    capture = pano_project['persisted']['pano_captures'][0]
    assert capture['edit_engine'] == 'flux-canny'
    assert capture['flux_canny_inputs']['control_sha256']
    assert capture['flux_canny_provider_output_sha256']
    with Image.open(capture['edited_rgb_path']) as edited:
        assert edited.size == (128, 64)
    assert observed['kwargs']['size'] == '1088x512'
    call = pano_project['persisted']['pano_calls'][0]
    assert call['model_id'] == 'flux-control-lora-canny'
    assert call['extra']['control_sha256'] == capture['flux_canny_inputs']['control_sha256']


def test_pano_paid_preview_can_be_recovered_after_restart_for_repair(pano_project, monkeypatch):
    """恢复返回原合同，不能生成第二个 preview 或第二次 edit 额度。"""
    monkeypatch.setattr(rwh, 'load_config', lambda: {
        'fal_api_key': 'configured-test-key',
        'fal_gpt_image_endpoint': 'openai/gpt-image-2/edit',
    })
    monkeypatch.setattr(rwh, 'call_gpt_image_edit',
                        lambda *args, **kwargs: (Image.new('RGB', (128, 64), (10, 20, 30)), None))
    preview_request = WholeHomePanoPaidPreviewRequest(
        source_hash=pano_project['manifest']['source_hash'], provider='fal',
        model_id='gpt-image-2', edit_instruction='', style_description='modern',
        repair_band_deg=12, annotator_id='test')
    preview = rwh.preview_whole_home_pano_edit('proj-pano', 'pano_1', preview_request)
    rwh.edit_whole_home_pano(
        'proj-pano', 'pano_1', _edit_request(
            source_hash=preview['source_hash'], preview_id=preview['preview_id'],
            confirmation_phrase=preview['confirmation_phrase']))

    reset_pano_paid_previews_for_tests()
    recovered = rwh.preview_whole_home_pano_edit(
        'proj-pano', 'pano_1', preview_request)
    assert recovered['preview_id'] == preview['preview_id']
    assert recovered['confirmation_phrase'] == preview['confirmation_phrase']
    assert len(pano_project['persisted']['pano_paid_previews']) == 1
    assert len(pano_project['persisted']['pano_calls']) == 1


def test_pano_paid_confirmation_mismatch_never_calls_provider(pano_project, monkeypatch):
    request, _ = _confirmed_request(pano_project)
    request = request.model_copy(update={'confirmation_phrase': '错误确认短语 definitely wrong'})
    provider_called = False

    def forbidden_provider(*args, **kwargs):
        nonlocal provider_called
        provider_called = True
        return None, 'should not run'

    monkeypatch.setattr(rwh, 'call_gpt_image_edit', forbidden_provider)
    with pytest.raises(HTTPException) as exc:
        rwh.edit_whole_home_pano('proj-pano', 'pano_1', request)
    assert exc.value.status_code == 409
    assert provider_called is False
    assert not pano_project['project']['pano_calls']


def test_pano_channel_content_tamper_is_rejected_before_preview(pano_project, monkeypatch):
    rgb_path = pano_project['manifest']['channels']['rgb_erp']
    Image.new('RGB', (128, 64), (9, 9, 9)).save(rgb_path)
    monkeypatch.setattr(rwh, 'load_config', lambda: {'fal_api_key': 'configured-test-key'})
    with pytest.raises(HTTPException) as exc:
        rwh.preview_whole_home_pano_edit(
            'proj-pano', 'pano_1', WholeHomePanoPaidPreviewRequest(
                source_hash=pano_project['manifest']['source_hash'], provider='fal',
                model_id='gpt-image-2', edit_instruction='', style_description='',
                repair_band_deg=12, annotator_id='test'))
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'pano_channel_hash_mismatch'


def test_pano_edit_hard_cap_blocks_second_paid_call(pano_project, monkeypatch):
    monkeypatch.setattr(rwh, 'call_gpt_image_edit',
                        lambda *args, **kwargs: (Image.new('RGB', (128, 64), (10, 20, 30)), None))
    first, _ = _confirmed_request(pano_project)
    rwh.edit_whole_home_pano('proj-pano', 'pano_1', first)
    second, _ = _confirmed_request(pano_project)
    with pytest.raises(HTTPException) as exc:
        rwh.edit_whole_home_pano('proj-pano', 'pano_1', second)
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'pano_edit_cap_exhausted'
    assert len(pano_project['persisted']['pano_calls']) == 1


def test_pano_review_persists_candidate_hash_and_uncertain_fails(pano_project):
    candidate = pano_project['tmp'] / 'edited_rgb.png'
    Image.new('RGB', (128, 64), (1, 2, 3)).save(candidate)
    capture = pano_project['capture']
    capture['edited_rgb_path'] = str(candidate)
    capture['gate'] = {
        'gate_pass': True, 'version': 'whole-home-pano-gate-v1',
        'gate_level': 'p0_rgb_structural', 'failures': [],
    }
    checklist = {
        'wall_openings': 'pass', 'duplicates': 'pass', 'material_continuity': 'pass',
        'lighting_continuity': 'pass', 'poles': 'pass',
        'cross_hotspot_same_object': 'uncertain',
    }
    result = rwh.review_whole_home_pano(
        'proj-pano', 'pano_1', WholeHomePanoReviewRequest(
            source_hash=pano_project['manifest']['source_hash'],
            gate_version='whole-home-pano-gate-v1', checklist=checklist,
            annotator_id='reviewer'))
    saved = result['pano_captures'][0]
    assert saved['status'] == 'review_failed'
    assert saved['human_review']['accepted'] is False
    assert saved['human_review']['candidate_sha256'] == pano_file_sha256(str(candidate))
    assert saved['human_review']['annotator_id'] == 'reviewer'


def test_pano_gate_uses_path_pano_id_not_missing_body_field(pano_project, monkeypatch):
    candidate = pano_project['tmp'] / 'edited_rgb.png'
    Image.new('RGB', (128, 64), (1, 2, 3)).save(candidate)
    active = rwh._ACTIVE_PROJECTS['proj-pano']
    active['pano_captures'][0]['edited_rgb_path'] = str(candidate)
    monkeypatch.setattr(rwh, 'gate_pano_erp', lambda *args, **kwargs: {
        'gate_pass': True, 'full_contract_pass': False,
        'gate_level': 'p0_rgb_structural', 'not_evaluable': ['depth_order'],
        'version': 'whole-home-pano-gate-v1', 'checks': [], 'hard_fail': False,
        'summary': 'ok', 'failures': [],
    })
    result = rwh.gate_whole_home_pano(
        'proj-pano', 'pano_1', WholeHomePanoGateRequest(
            source_hash=pano_project['manifest']['source_hash'], face_size=64,
            annotator_id='test'))
    assert result['pano_id'] == 'pano_1'
    assert result['gate']['gate_pass'] is True


def test_pano_gate_history_archive_is_immutable_and_idempotent(pano_project, monkeypatch):
    output = pano_project['tmp'] / 'managed-output'
    candidate = output / '_whole_home' / 'candidate.png'
    reference = output / '_whole_home' / 'reference.png'
    candidate.parent.mkdir(parents=True)
    Image.new('RGB', (128, 64), (1, 2, 3)).save(candidate)
    Image.new('RGB', (128, 64), (200, 200, 200)).save(reference)
    monkeypatch.setattr(rwh, 'MAIN_OUTPUT_DIR', str(output))
    capture = copy.deepcopy(pano_project['capture'])
    capture['manifest']['channels']['rgb_erp'] = str(reference)
    project = copy.deepcopy(pano_project['project'])
    project['pano_calls'] = [{
        'call_id': 'paid-call', 'capture_id': capture['capture_id'], 'kind': 'edit',
        'provider': 'fal', 'model_id': 'gpt-image-2', 'status': 'succeeded',
        'request_id': 'fal-request-id',
    }]
    gate = {
        'version': 'whole-home-pano-gate-v1', 'gate_level': 'p0_rgb_structural',
        'gate_pass': False, 'full_contract_pass': False,
        'failures': ['wrap_seam', 'structure_views'], 'not_evaluable': ['depth_order'],
    }
    first = rwh._archive_pano_gate_record(project, capture, str(candidate), gate)
    second = rwh._archive_pano_gate_record(project, capture, str(candidate), gate)
    assert first == second
    rows = load_records_file(first['json_path'])
    assert len(rows) == 1
    assert rows[0]['immutable_audit'] is True
    assert rows[0]['results'][0]['review_status'] == 'rejected'
    assert rows[0]['results'][0]['result_image_file'].endswith('_whole_home/candidate.png')
    assert rows[0]['pano_audit']['provider_call']['request_id'] == 'fal-request-id'
    assert rows[0]['pano_audit']['projection'] == 'equirectangular'
    assert rows[0]['pano_audit']['erp_width'] == 128
    assert rows[0]['pano_audit']['erp_height'] == 64
    assert rows[0]['pano_audit']['canonical_forward'] == '+Z'


def test_pano_paid_preview_restores_after_restart_for_repair_only():
    preview = create_pano_paid_preview(
        project_id='restart-project', pano_id='restart-pano', source_hash='a' * 64,
        provider='fal', endpoint='openai/gpt-image-2/edit', model_id='gpt-image-2',
        output_size='3840x1920', edit_prompt='restart-safe prompt',
        repair_band_deg=12, actor='test')
    claim_pano_paid_stage(
        preview['preview_id'], preview['confirmation_phrase'], stage='edit',
        project_id='restart-project', pano_id='restart-pano', source_hash='a' * 64)
    persisted = persistable_pano_paid_preview(preview['preview_id'])
    reset_pano_paid_previews_for_tests()
    restore_pano_paid_preview(persisted)
    repaired = claim_pano_paid_stage(
        preview['preview_id'], preview['confirmation_phrase'], stage='repair',
        project_id='restart-project', pano_id='restart-pano', source_hash='a' * 64)
    assert repaired['edit_claimed'] is True and repaired['repair_claimed'] is True
    with pytest.raises(ValueError, match='pano_paid_edit_already_claimed'):
        claim_pano_paid_stage(
            preview['preview_id'], preview['confirmation_phrase'], stage='edit',
            project_id='restart-project', pano_id='restart-pano', source_hash='a' * 64)


def test_pano_paid_preview_tamper_cannot_restore():
    preview = create_pano_paid_preview(
        project_id='tamper-project', pano_id='tamper-pano', source_hash='b' * 64,
        provider='fal', endpoint='openai/gpt-image-2/edit', model_id='gpt-image-2',
        output_size='3840x1920', edit_prompt='original prompt',
        repair_band_deg=12, actor='test')
    persisted = persistable_pano_paid_preview(preview['preview_id'])
    persisted['edit_prompt'] = 'tampered prompt'
    reset_pano_paid_previews_for_tests()
    with pytest.raises(ValueError, match='pano_paid_preview_tampered'):
        restore_pano_paid_preview(persisted)
