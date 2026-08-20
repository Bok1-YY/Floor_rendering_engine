# -*- coding: utf-8 -*-
"""球面硬门禁测试(文档 §9):正常候选 pass;已知错位候选 hard fail。"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from Floor_engine_server import whole_home_pano_gate as gate
from Floor_engine_server import whole_home_pano_render as pano_render
from Floor_engine_server.whole_home_pano_edit import build_structure_holdout_mask


def _render_reference(tmp_path) -> tuple[str, dict]:
    model = {
        'walls': [
            {'id': 'wN', 'start': {'x': 0, 'z': 0}, 'end': {'x': 6, 'z': 0}, 'height_m': 3.0},
            {'id': 'wE', 'start': {'x': 6, 'z': 0}, 'end': {'x': 6, 'z': 6}, 'height_m': 3.0},
            {'id': 'wS', 'start': {'x': 6, 'z': 6}, 'end': {'x': 0, 'z': 6}, 'height_m': 3.0},
            {'id': 'wW', 'start': {'x': 0, 'z': 6}, 'end': {'x': 0, 'z': 0}, 'height_m': 3.0},
        ],
        'rooms': [{'id': 'r1', 'polygon': [{'x': 0, 'z': 0}, {'x': 6, 'z': 0},
                                           {'x': 6, 'z': 6}, {'x': 0, 'z': 6}]}],
        'openings': [
            {'id': 'o1', 'wall_id': 'wS', 'offset_m': 2.4, 'width_m': 1.2,
             'sill_height_m': 0, 'height_m': 2.1, 'review_status': 'accepted'},
        ],
        'fixed_objects': [
            {'id': 'f1', 'position': {'x': 3.0, 'y': 0, 'z': 2.2}, 'size': {'x': 1.4, 'y': .9, 'z': .7},
             'semantic_role': 'sofa', 'review_status': 'accepted'},
        ],
    }
    faces = pano_render.render_cube_faces(model, (3.0, 1.5, 3.0), 96, 0.1, 30.0)
    erp = pano_render.cube_to_erp(
        {face: faces[face]['rgb'] for face in pano_render.CUBE_FACE_ORDER}, 384, 192)
    path = tmp_path / 'reference_rgb_erp.png'
    erp.save(path)
    manifest = {
        'schema_version': 1, 'pano_id': 'pano_gate_1', 'projection': 'equirectangular',
        'coordinate_system': 'right-handed-y-up', 'camera_center_m': {'x': 3.0, 'y': 1.5, 'z': 3.0},
        'canonical_forward': '+Z', 'heading_deg': 0, 'pitch_deg': 0, 'roll_deg': 0,
        'horizontal_fov_deg': 360, 'vertical_fov_deg': 180,
        'erp_width': 384, 'erp_height': 192, 'cube_face_size': 96,
        'cube_face_order': ['+X', '-X', '+Y', '-Y', '+Z', '-Z'],
        'near_m': 0.1, 'far_m': 30.0,
        'depth_encoding': 'linear_metric_global_range',
        'normal_encoding': 'world_space_xyz_to_rgb',
        'channels': {'rgb_erp': str(path)}, 'source_hash': '',
    }
    return str(path), manifest


def test_gate_passes_on_self_reference(tmp_path):
    reference_path, manifest = _render_reference(tmp_path)
    result = gate.gate_pano_erp(reference_path, manifest['channels'], manifest, {}, face_size=96)
    for check in result['checks']:
        assert check['status'] in ('pass', 'skipped'), f"{check['check_id']} 不应失败: {check}"
    assert result['gate_pass'] is True
    assert result['gate_level'] == 'p0_rgb_structural'
    assert result['full_contract_pass'] is False
    assert result['not_evaluable'] == ['depth_order']
    assert result['version'] == gate.GATE_VERSION


def test_gate_fails_on_broken_wrap_seam(tmp_path):
    """把 ERP 左带覆盖为纯白 → 左右接缝断裂,wrap_seam 必须 hard fail。"""
    reference_path, manifest = _render_reference(tmp_path)
    arr = np.asarray(Image.open(reference_path)).copy()
    band = max(2, int(arr.shape[1] * gate.BAND_FRACTION))
    arr[:, :band] = 255
    candidate_path = tmp_path / 'broken_wrap.png'
    Image.fromarray(arr).save(candidate_path)
    result = gate.gate_pano_erp(str(candidate_path), manifest['channels'], manifest, {}, face_size=96)
    wrap = next(row for row in result['checks'] if row['check_id'] == 'wrap_seam')
    assert wrap['status'] == 'fail', '接缝断裂未检出'
    assert result['gate_pass'] is False


def test_gate_fails_on_pole_tearing(tmp_path):
    """顶部极点替换为高对比放射状条纹 → 极点拉丝,poles 必须 hard fail。"""
    reference_path, manifest = _render_reference(tmp_path)
    arr = np.asarray(Image.open(reference_path)).copy()
    band = max(2, int(arr.shape[0] * 0.06))
    width = arr.shape[1]
    stripes = np.tile(((np.arange(width) % 2) * 255).astype(np.uint8), (band, 1))
    arr[:band] = np.stack([stripes, stripes, np.zeros_like(stripes)], axis=-1)
    candidate_path = tmp_path / 'pole_tear.png'
    Image.fromarray(arr).save(candidate_path)
    result = gate.gate_pano_erp(str(candidate_path), manifest['channels'], manifest, {}, face_size=96)
    pole = next(row for row in result['checks'] if row['check_id'] == 'poles')
    assert pole['status'] == 'fail', '极点拉丝未检出'
    assert result['gate_pass'] is False


def test_gate_fails_on_broken_2_1(tmp_path):
    reference_path, manifest = _render_reference(tmp_path)
    arr = np.asarray(Image.open(reference_path))
    candidate_path = tmp_path / 'cropped.png'
    Image.fromarray(arr[:, :-4]).save(candidate_path)
    result = gate.gate_pano_erp(str(candidate_path), manifest['channels'], manifest, {}, face_size=96)
    size_check = next(row for row in result['checks'] if row['check_id'] == 'size_manifest')
    assert size_check['status'] == 'fail', '2:1 破坏未检出'
    assert result['gate_pass'] is False


def test_gate_fails_on_duplicated_opening(tmp_path):
    """复制一个 opening 角窗口到另一处 → 窗口外新增结构,structure_views 应检出。"""
    reference_path, manifest = _render_reference(tmp_path)
    arr = np.asarray(Image.open(reference_path)).copy()
    width = arr.shape[1]
    source = arr[:, int(width * 0.5): int(width * 0.6)].copy()
    target_start = int(width * 0.2)
    arr[:, target_start: target_start + source.shape[1]] = source
    candidate_path = tmp_path / 'duplicated_opening.png'
    Image.fromarray(arr).save(candidate_path)
    result = gate.gate_pano_erp(str(candidate_path), manifest['channels'], manifest, {}, face_size=96)
    assert result['gate_pass'] is False, '重复开口结构未检出'


def test_structure_gate_tolerates_added_short_material_texture(tmp_path):
    reference_path, _ = _render_reference(tmp_path)
    reference = np.asarray(Image.open(reference_path), dtype=np.float32)
    candidate = reference.copy()
    # Add short, discontinuous material marks without moving any reference edge.
    for y in range(30, candidate.shape[0] - 30, 18):
        for x in range(20, candidate.shape[1] - 20, 42):
            candidate[y:y + 2, x:x + 12] = np.clip(
                candidate[y:y + 2, x:x + 12] * .7 + 45, 0, 255)
    check = gate._structure_view_check(candidate, reference)
    assert check['status'] == 'pass', check
    assert check['value'] >= gate.STRUCTURE_DIRECTED_RECALL_MIN


def test_structure_holdout_mask_and_protected_gate_reject_locked_edge_repaint(tmp_path):
    reference_path, _ = _render_reference(tmp_path)
    reference_image = Image.open(reference_path)
    mask = build_structure_holdout_mask(reference_image, protection_deg=.5)
    mask_path = tmp_path / 'holdout.png'
    mask.save(mask_path)
    mask_array = np.asarray(mask)
    assert np.any(mask_array[..., 0] == 0)
    assert np.any(mask_array[..., 0] == 255)

    reference = np.asarray(reference_image, dtype=np.float32)
    candidate = reference.copy()
    candidate[mask_array[..., 0] == 0] = 255 - candidate[mask_array[..., 0] == 0]
    check = gate._protected_region_check(candidate, reference, str(mask_path))
    assert check['status'] == 'fail'
    assert check['changed_fraction'] > gate.PROTECTED_CHANGED_FRACTION_MAX


def test_pixels_to_deg_angle_scale():
    assert gate.pixels_to_deg(1, 3840) == pytest.approx(360 / 3840)
    assert gate.pixels_to_deg(1, 2048) == pytest.approx(360 / 2048)


def test_cube_edge_pairs_are_exactly_12():
    pairs = gate._cube_edge_pairs()
    assert len(pairs) == 12


def test_geometry_locked_replay_certifies_only_spatial_checks():
    result = {
        'checks': [
            {'check_id': 'size_manifest', 'status': 'pass', 'value': 1},
            {'check_id': 'wrap_seam', 'status': 'pass', 'value': 0},
            {'check_id': 'cube_edges', 'status': 'fail', 'value': 99},
            {'check_id': 'structure_views', 'status': 'fail', 'value': .4},
            {'check_id': 'poles', 'status': 'pass', 'value': 0},
            {'check_id': 'opening_identity', 'status': 'fail', 'value': 1,
             'opening_edge_ratio_max': 6},
            {'check_id': 'protected_region', 'status': 'pass', 'value': 0},
            {'check_id': 'depth_order', 'status': 'skipped', 'value': 0},
        ],
    }
    certified = gate.certify_geometry_locked_gate(result, {
        'check_id': 'geometry_locked_replay', 'status': 'pass',
        'metric': 'mismatched_pixels', 'value': 0,
        'coordinate_transform': 'identity_pixel_grid', 'spatial_operations': [],
    })
    assert certified['gate_pass'] is True
    assert certified['full_contract_pass'] is False
    assert certified['gate_profile'] == 'geometry_locked_material'
    for check_id in ('cube_edges', 'structure_views', 'opening_identity'):
        row = next(item for item in certified['checks'] if item['check_id'] == check_id)
        assert row['status'] == 'pass'
        assert row['diagnostic_status'] == 'fail'
        assert row['certified_by'] == 'geometry_locked_replay'


def test_geometry_locked_replay_never_overrides_real_wrap_failure():
    result = {'checks': [
        {'check_id': 'size_manifest', 'status': 'pass'},
        {'check_id': 'wrap_seam', 'status': 'fail'},
        {'check_id': 'cube_edges', 'status': 'fail'},
        {'check_id': 'structure_views', 'status': 'fail'},
        {'check_id': 'poles', 'status': 'pass'},
        {'check_id': 'opening_identity', 'status': 'fail'},
        {'check_id': 'protected_region', 'status': 'pass'},
        {'check_id': 'depth_order', 'status': 'skipped'},
    ]}
    certified = gate.certify_geometry_locked_gate(result, {
        'check_id': 'geometry_locked_replay', 'status': 'pass', 'value': 0,
        'coordinate_transform': 'identity_pixel_grid', 'spatial_operations': [],
    })
    assert certified['gate_pass'] is False
    assert certified['failures'] == ['wrap_seam']


def test_pano_qa_constraints_contract():
    rows = gate.pano_qa_constraints(
        {'pano_id': 'pano_x'},
        {'openings': [{'review_status': 'accepted'}, {'review_status': 'pending'}],
         'fixed_objects': [{'review_status': 'accepted'}, {'review_status': 'rejected'}]})
    assert len(rows) == 10
    ids = [row['constraint_id'] for row in rows]
    assert ids == ['P101', 'P102', 'P103', 'P104', 'P105', 'P106', 'P107', 'P108', 'P109', 'P110']
    # 数量/身份约束必须反映 accepted 实体的真实计数(文档 §9.1-6/7)。
    assert '1 个' in rows[4]['constraint']
    assert '1 个' in rows[5]['constraint']
