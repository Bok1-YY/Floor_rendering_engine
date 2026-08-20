# -*- coding: utf-8 -*-
"""pano capture 保存链路的集成验证 + data_url 上限实测(计划注意点)。

3840×1920 ERP 由后端从 atlas 生成、不经 data_url 上传;前端只上传 3×2 atlas,
本测试用真实渲染的 atlas 走完整保存链路,并实测最坏情况 base64 尺寸。
"""
from __future__ import annotations

import base64
import copy
import io
import os

import numpy as np
import pytest
from PIL import Image

from Floor_engine_server import routes_whole_home as rwh
from Floor_engine_server import whole_home_pano_render as pano_render
from Floor_engine_server.server_schemas import WholeHomePanoCaptureRequest


def _data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def _room_model() -> dict:
    return {
        'walls': [
            {'id': 'wN', 'start': {'x': 0, 'z': 0}, 'end': {'x': 5, 'z': 0}, 'height_m': 3.0},
            {'id': 'wE', 'start': {'x': 5, 'z': 0}, 'end': {'x': 5, 'z': 5}, 'height_m': 3.0},
            {'id': 'wS', 'start': {'x': 5, 'z': 5}, 'end': {'x': 0, 'z': 5}, 'height_m': 3.0},
            {'id': 'wW', 'start': {'x': 0, 'z': 5}, 'end': {'x': 0, 'z': 0}, 'height_m': 3.0},
        ],
        'rooms': [{'id': 'r1', 'polygon': [{'x': 0, 'z': 0}, {'x': 5, 'z': 0},
                                           {'x': 5, 'z': 5}, {'x': 0, 'z': 5}]}],
        'openings': [], 'fixed_objects': [], 'cameras': [],
    }


def _capture_request(atlases: dict[str, str]) -> WholeHomePanoCaptureRequest:
    return WholeHomePanoCaptureRequest(
        pano_id='pano_room_1',
        camera={'position': {'x': 2.5, 'y': 1.55, 'z': 2.5},
                'target': {'x': 2.5, 'y': 1.55, 'z': 3.5}, 'focal_length_mm': 12},
        camera_center_m={'x': 2.5, 'y': 1.55, 'z': 2.5},
        erp_width=512, erp_height=256, cube_face_size=128,
        rgb_atlas_data_url=atlases['rgb'], depth_atlas_data_url=atlases['depth'],
        normal_atlas_data_url=atlases['normal'], edge_atlas_data_url=atlases['edge'],
        semantic_atlas_data_url=atlases['semantic'],
        subject_id_atlas_data_url=atlases['subject_id'],
        semantic_legend={}, subject_id_legend={
            'version': 'whole-home-subject-id-v1', 'pixel_origin': 'top-left', 'subjects': []},
        render_contract={
            'materials': {'clay': {'color': '#d8d4ca'}},
            'lighting': {'hemisphere': {'intensity': 2.2}},
        },
        source_hash='', room_id='r1', annotator_id='test',
    )


def test_pano_capture_save_generates_erp_channels(tmp_path, monkeypatch):
    model = _room_model()
    faces = pano_render.render_cube_faces(model, (2.5, 1.55, 2.5), 128, 0.1, 30.0)
    atlases = {
        kind: _data_url(pano_render.cube_faces_to_atlas(
            {face: faces[face][kind] for face in pano_render.CUBE_FACE_ORDER}))
        for kind in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'subject_id')
    }
    project = {'project_id': 'proj-pano-int', 'verified': True, 'model': model,
               'pano_captures': [], 'pano_hotspots': []}
    rwh._ACTIVE_PROJECTS['proj-pano-int'] = project
    persisted: dict = {}

    def fake_persist(value):
        persisted.clear()
        persisted.update(copy.deepcopy(value))
        rwh._ACTIVE_PROJECTS['proj-pano-int'] = copy.deepcopy(value)

    monkeypatch.setattr(rwh, '_persist_project', fake_persist)

    def fake_save_data(project_id, pano_id, kind, data_url, **kwargs):
        raw = base64.b64decode(data_url.split(',', 1)[1])
        folder = tmp_path / str(kwargs.get('capture_id') or 'legacy')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{kind}.png'
        path.write_bytes(raw)
        return str(path)

    monkeypatch.setattr(rwh, 'save_pano_data', fake_save_data)

    def fake_save_image(project_id, pano_id, kind, image, **kwargs):
        folder = tmp_path / str(kwargs.get('capture_id') or 'legacy')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{kind}.png'
        image.save(path)
        return str(path)

    monkeypatch.setattr(rwh, 'save_pano_image_file', fake_save_image)
    request = _capture_request(atlases)
    rwh._save_whole_home_pano_capture('proj-pano-int', request)
    capture = persisted['pano_captures'][0]
    channels = capture['manifest']['channels']
    for kind in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'subject_id'):
        path = channels.get(f'{kind}_erp') or ''
        assert path and os.path.isfile(path), f'{kind}_erp 未生成'
        with Image.open(path) as erp:
            assert erp.size == (512, 256), f'{kind}_erp 尺寸错误'
    assert capture['manifest']['source_hash']
    # source_hash 覆盖 ERP 通道路径(编辑前必须匹配)。
    assert len(channels['rgb_erp']) > 0
    # 离线端到端最后一环:保存的 ERP 通过自参考硬门禁。
    from Floor_engine_server import whole_home_pano_gate as pano_gate

    gate_result = pano_gate.gate_pano_erp(
        channels['rgb_erp'], channels, capture['manifest'], model, face_size=64)
    assert gate_result['gate_pass'] is True, gate_result['summary']
    assert all(row['status'] in ('pass', 'skipped') for row in gate_result['checks'])

    # 同 pano 重拍必须生成不可变新 revision；旧文件保留、旧 capture 明确 superseded。
    first_capture_id = capture['capture_id']
    first_rgb_path = channels['rgb_erp']
    rwh._save_whole_home_pano_capture('proj-pano-int', _capture_request(atlases))
    rows = persisted['pano_captures']
    assert len(rows) == 2
    assert rows[0]['capture_id'] == first_capture_id and rows[0]['active'] is False
    assert rows[0]['status'] == 'superseded'
    assert rows[1]['capture_revision'] == 2 and rows[1]['active'] is True
    assert rows[1]['capture_id'] != first_capture_id
    assert rows[1]['manifest']['channels']['rgb_erp'] != first_rgb_path
    assert os.path.isfile(first_rgb_path), '旧 revision 文件不得被覆盖或删除'


def test_pano_atlas_data_url_fits_schema_limits():
    """最坏情况(随机噪点)512-face atlas 的 base64 长度应远小于 60M 上限。"""
    rng = np.random.default_rng(7)
    worst_case = Image.fromarray(rng.integers(0, 256, (1024, 1536, 3), dtype=np.uint8), 'RGB')
    data_url = _data_url(worst_case)
    # 6 通道 × 该长度即 JSON body 量级;单通道与 60M 上限对比。
    assert len(data_url) < 60_000_000
    # 记录实测值供基准参考(噪点图 PNG ≈ 原始像素量)。
    expected_upper = int(1536 * 1024 * 3 * 4 / 3 * 1.05)
    assert len(data_url) < expected_upper, f'实测 {len(data_url)} 超出预期上限 {expected_upper}'


def test_persist_project_refreshes_active_floorplan_cache(monkeypatch):
    project_id = 'pano-cache-sync'
    rwh._ACTIVE_PROJECTS[project_id] = {
        'project_id': project_id, 'source_type': 'floorplan', 'pano_captures': []}
    monkeypatch.setattr(rwh, 'save_project', lambda value: None)
    updated = {
        'project_id': project_id, 'source_type': 'floorplan',
        'pano_captures': [{'capture_id': 'panocap_new'}],
    }
    rwh._persist_project(updated)
    assert rwh._ACTIVE_PROJECTS[project_id]['pano_captures'][0]['capture_id'] == 'panocap_new'
    updated['pano_captures'][0]['capture_id'] = 'mutated-after-persist'
    assert rwh._ACTIVE_PROJECTS[project_id]['pano_captures'][0]['capture_id'] == 'panocap_new'


def test_cad_public_project_view_keeps_reloadable_pano_capture(tmp_path, monkeypatch):
    rgb = tmp_path / 'rgb_erp.png'
    edited = tmp_path / 'materialized_rgb.png'
    rgb.write_bytes(b'rgb')
    edited.write_bytes(b'edited')
    monkeypatch.setattr(
        rwh, 'to_url',
        lambda path: f"/api/files/{os.path.basename(str(path))}" if path else '')
    project = {
        'project_id': 'home_pano_reload', 'source_type': 'cad',
        'status': 'verified', 'verified': True, 'revision': 3,
        'model': {'walls': [], 'rooms': [], 'openings': [], 'fixed_objects': []},
        'pano_captures': [{
            'capture_id': 'panocap_v11', 'pano_id': 'pano_living_verified_v11',
            'status': 'gated', 'edited_rgb_path': str(edited),
            'manifest': {
                'source_hash': 'a' * 64, 'erp_width': 3840, 'erp_height': 1920,
                'channels': {'rgb_erp': str(rgb)},
            },
        }],
        'pano_paid_previews': [{'confirmation_phrase': 'must-not-leak'}],
    }
    view = rwh._whole_home_project_view(project)
    assert len(view['pano_captures']) == 1
    row = view['pano_captures'][0]
    assert row['pano_id'] == 'pano_living_verified_v11'
    assert row['manifest']['source_hash'] == 'a' * 64
    assert row['channel_urls']['rgb_erp'].startswith('/api/files/')
    assert row['edited_rgb_url'].startswith('/api/files/')
    assert 'pano_paid_previews' not in view
