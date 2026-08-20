# -*- coding: utf-8 -*-
"""定点球面全景 P0:schema 契约、manifest hash、热点安全门禁的单元测试。

覆盖文档 docs/定点球面全景_AI生成与一致性方案.md 的 §10 数据合同与 §7.2
热点规则;全景通道不得混用 perspective 的 aspect_ratio。
"""
from __future__ import annotations

import copy
import math

import numpy as np
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from Floor_engine_server import routes_whole_home, server_schemas, whole_home_engine, whole_home_pano_render

_ONE_PX_PNG = ('data:image/png;base64,'
               'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ'
               'AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def _pano_payload(**overrides) -> dict:
    payload = {
        'pano_id': 'pano_living_01',
        'camera': {
            'position': {'x': 4.25, 'y': 1.55, 'z': 3.8},
            'target': {'x': 4.25, 'y': 1.55, 'z': 4.8},
            'focal_length_mm': 28,
        },
        'camera_center_m': {'x': 4.25, 'y': 1.55, 'z': 3.8},
        'erp_width': 4096, 'erp_height': 2048, 'cube_face_size': 1024,
        'rgb_atlas_data_url': _ONE_PX_PNG, 'depth_atlas_data_url': _ONE_PX_PNG,
        'normal_atlas_data_url': _ONE_PX_PNG, 'semantic_atlas_data_url': _ONE_PX_PNG,
        'subject_id_atlas_data_url': _ONE_PX_PNG,
        'render_contract': {
            'materials': {'clay': {'color': '#d8d4ca'}},
            'lighting': {'hemisphere': {'intensity': 2.2}},
        },
    }
    payload.update(overrides)
    return payload


# ── schema 契约 ────────────────────────────────────────────

def test_pano_capture_accepts_strict_2_1():
    request = server_schemas.WholeHomePanoCaptureRequest(**_pano_payload())
    assert request.erp_width == 4096 and request.erp_height == 2048
    assert request.cube_face_order == ['+X', '-X', '+Y', '-Y', '+Z', '-Z']
    assert request.projection == 'equirectangular'


def test_pano_capture_rejects_non_2_1():
    with pytest.raises(ValidationError):
        server_schemas.WholeHomePanoCaptureRequest(**_pano_payload(erp_width=4096, erp_height=2000))


def test_pano_capture_rejects_cropped_width():
    with pytest.raises(ValidationError):
        server_schemas.WholeHomePanoCaptureRequest(**_pano_payload(erp_width=4000, erp_height=2048))


def test_pano_capture_rejects_camera_center_mismatch():
    with pytest.raises(ValidationError):
        server_schemas.WholeHomePanoCaptureRequest(
            **_pano_payload(camera_center_m={'x': 4.25, 'y': 1.55, 'z': 9.9}))


def test_pano_capture_rejects_bad_face_order():
    with pytest.raises(ValidationError):
        server_schemas.WholeHomePanoCaptureRequest(
            **_pano_payload(cube_face_order=['+X', '-X', '+Z', '-Z', '+Y', '-Y']))


def test_pano_capture_rejects_bad_encoding():
    with pytest.raises(ValidationError):
        server_schemas.WholeHomePanoCaptureRequest(
            **_pano_payload(depth_encoding='per_image_percentile'))
    with pytest.raises(ValidationError):
        server_schemas.WholeHomePanoCaptureRequest(
            **_pano_payload(normal_encoding='camera_space_rgb'))
    with pytest.raises(ValidationError):
        server_schemas.WholeHomePanoCaptureRequest(**_pano_payload(projection='perspective'))


def test_perspective_capture_still_rejects_2_1():
    payload = {
        'camera': {}, 'aspect_ratio': '2:1',
        'rgb_data_url': _ONE_PX_PNG, 'depth_data_url': _ONE_PX_PNG,
        'normal_data_url': _ONE_PX_PNG, 'semantic_data_url': _ONE_PX_PNG,
    }
    with pytest.raises(ValidationError):
        server_schemas.WholeHomeCaptureRequest(**payload)


# ── manifest hash(§10 source_hash 覆盖范围)────────────────

def _manifest(**overrides) -> dict:
    manifest = {
        'schema_version': 1, 'pano_id': 'pano_living_01',
        'projection': 'equirectangular', 'coordinate_system': 'right-handed-y-up',
        'camera_center_m': {'x': 4.25, 'y': 1.55, 'z': 3.8},
        'canonical_forward': '+Z', 'heading_deg': 0, 'pitch_deg': 0, 'roll_deg': 0,
        'horizontal_fov_deg': 360, 'vertical_fov_deg': 180,
        'erp_width': 4096, 'erp_height': 2048, 'cube_face_size': 1024,
        'cube_face_order': ['+X', '-X', '+Y', '-Y', '+Z', '-Z'],
        'near_m': 0.05, 'far_m': 30.0,
        'depth_encoding': 'linear_metric_global_range',
        'normal_encoding': 'world_space_xyz_to_rgb',
        'model_facts_hash': 'm', 'material_graph_hash': 'g', 'lighting_hash': 'l',
        'capture_id': 'panocap_1', 'capture_revision': 1,
        'render_contract': {'materials': {'clay': 1}, 'lighting': {'sun': 1}},
        'channels': {'rgb_erp': 'panos/pano_living_01/rgb.png'},
        'channel_hashes': {'rgb_erp': 'abc'},
        'source_hash': '',
    }
    manifest.update(overrides)
    return manifest


def test_pano_manifest_hash_is_stable_and_field_complete():
    manifest = _manifest()
    first = whole_home_engine.pano_manifest_hash(manifest)
    assert first == whole_home_engine.pano_manifest_hash(copy.deepcopy(manifest))
    # 文档 §10:source_hash 覆盖除生成结果与 QA 外的全部字段;逐字段翻转都必须改变 hash。
    mutations = [
        ('projection', 'perspective'),
        ('camera_center_m', {'x': 9.0, 'y': 1.55, 'z': 3.8}),
        ('heading_deg', 90),
        ('pitch_deg', 45),
        ('roll_deg', 12),
        ('canonical_forward', '+X'),
        ('near_m', 0.1),
        ('far_m', 60.0),
        ('cube_face_size', 512),
        ('cube_face_order', ['+X', '-X', '+Z', '-Z', '+Y', '-Y']),
        ('depth_encoding', 'per_image_percentile'),
        ('normal_encoding', 'camera_space_rgb'),
        ('model_facts_hash', 'm2'),
        ('material_graph_hash', 'g2'),
        ('lighting_hash', 'l2'),
        ('scene_recipe_id', 'scene_2'),
        ('scene_hash', 'scene-hash-2'),
        ('render_contract', {'materials': {'clay': 2}, 'lighting': {'sun': 1}}),
        ('channel_hashes', {'rgb_erp': 'def'}),
        ('channels', {'rgb_erp': 'panos/pano_living_01/rgb2.png'}),
        ('erp_width', 2048),
    ]
    for field, value in mutations:
        mutated = _manifest(**{field: value})
        assert whole_home_engine.pano_manifest_hash(mutated) != first, f'field {field} 未参与 hash'
    # 生成结果(候选 PNG 路径)不应改变 hash:channels 之外的字段被忽略。
    assert whole_home_engine.pano_manifest_hash({**_manifest(), 'qa_summary': 'x'}) == first


def test_pano_manifest_source_hash_self_consistent():
    manifest = _manifest()
    manifest['source_hash'] = ''
    computed = whole_home_engine.pano_manifest_hash(manifest)
    manifest['source_hash'] = computed
    # source_hash 本身不参与 hash(否则自指),固定后可反复校验。
    assert whole_home_engine.pano_manifest_hash(manifest) == computed


# ── 热点安全门禁(§7.2)────────────────────────────────────

def _wall_model() -> dict:
    return {
        'walls': [{'id': 'w1', 'start': {'x': 0, 'z': 0}, 'end': {'x': 4, 'z': 0}}],
        'fixed_objects': [], 'rooms': [],
    }


def test_pano_hotspot_center_sphere_rejects_wall_contact():
    model = _wall_model()
    assert routes_whole_home._pano_hotspot_center_clear(model, 2.0, 2.0) is True
    # 球半径 0.20m:距墙 0.25m 时朝墙采样点距墙 0.05m,必须拒绝。
    assert routes_whole_home._pano_hotspot_center_clear(model, 2.0, 0.25) is False


def test_pano_hotspot_min_spacing_rule():
    project = {
        'pano_hotspots': [{
            'pano_id': 'pano_a', 'room_id': 'living',
            'camera_center_m': {'x': 4.0, 'y': 1.55, 'z': 4.0},
        }],
    }
    model = _wall_model()
    # 间距 1.0m < 1.5m → 409
    with pytest.raises(HTTPException) as exc:
        routes_whole_home._assert_pano_hotspot_safe(
            project, model, 'pano_b', 5.0, 4.0, 'living')
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'pano_hotspot_too_close'
    # 间距 2.0m → 通过(超大开放区多热点)
    routes_whole_home._assert_pano_hotspot_safe(
        project, model, 'pano_b', 6.0, 4.0, 'living')


def test_pano_hotspot_collision_rejected():
    model = _wall_model()
    with pytest.raises(HTTPException) as exc:
        routes_whole_home._assert_pano_hotspot_safe(
            {'pano_hotspots': []}, model, 'pano_wall', 2.0, 0.1, 'living')
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'pano_hotspot_collision'


# ── 彩色轴测试(文档 §7.3-7):六面方向无翻转/对调 ────────────

def _axis_probe_model() -> dict:
    """在六个世界轴方向各放一个不同语义角色的盒子作为彩色轴。

    水平方向盒子中心高度对齐眼高 1.5m(避免底面 edge-on 落在视线平面);
    垂直方向盒子沿视线轴放置。
    """
    from Floor_engine_server.whole_home_software_renderer import SEMANTIC_COLORS

    roles = {'+X': 'sofa', '-X': 'bed', '+Y': 'tv', '-Y': 'basin', '+Z': 'toilet', '-Z': 'sink'}
    center = (2.5, 1.5, 2.5)
    distance = 1.2
    boxes = []
    for axis, role in roles.items():
        position = list(center)
        index = {'X': 0, 'Y': 1, 'Z': 2}[axis[1]]
        position[index] += distance if axis[0] == '+' else -distance
        if axis[1] != 'Y':
            position[1] = 1.25  # 盒体 y∈[1.25,1.75],视线 y=1.5 穿过中部
        boxes.append({
            'id': f'probe{axis}', 'position': {'x': position[0], 'y': position[1], 'z': position[2]},
            'size': {'x': .5, 'y': .5, 'z': .5}, 'semantic_role': role,
            'rotation_y_deg': 0, 'review_status': 'accepted',
        })
    return {'walls': [], 'rooms': [], 'openings': [], 'fixed_objects': boxes}, roles


def test_pano_axis_probe_face_directions():
    from Floor_engine_server import whole_home_pano_render as pano_render
    from Floor_engine_server.whole_home_software_renderer import SEMANTIC_COLORS

    model, roles = _axis_probe_model()
    faces = pano_render.render_cube_faces(model, (2.5, 1.5, 2.5), 32, 0.05, 30.0)
    for face, role in roles.items():
        center = np.asarray(faces[face]['semantic'].convert('RGB'))[16, 16]
        expected = np.array(SEMANTIC_COLORS[role], dtype=np.uint8)
        assert np.array_equal(center, expected), f'{face} 中心应看到 {role} 色,实际 {center.tolist()}'


def test_pano_subject_id_channel_uses_stable_instance_color():
    from Floor_engine_server import whole_home_pano_render as pano_render

    model = {
        'walls': [], 'rooms': [], 'openings': [],
        'fixed_objects': [{
            'id': 'fixture-1', 'position': {'x': 2.5, 'y': 1.25, 'z': 3.7},
            'size': {'x': .5, 'y': .5, 'z': .5}, 'semantic_role': 'sofa',
            'rotation_y_deg': 0, 'review_status': 'accepted',
        }],
    }
    subjects = [{
        'subject': 'sofa', 'anchor_id': 'fixture-1',
        'anchor_kind': 'fixed_object', 'role': 'sofa',
    }]
    faces = pano_render.render_cube_faces(
        model, (2.5, 1.5, 2.5), 32, 0.05, 30.0, subjects=subjects)
    center = np.asarray(faces['+Z']['subject_id'].convert('RGB'))[16, 16]
    assert np.array_equal(center, np.array([0, 0, 1], dtype=np.uint8))


def test_locked_geometry_manifest_keeps_audited_fixed_objects_in_scene():
    """A structural manifest must not erase CAD-proven furnishing anchors."""
    from Floor_engine_server.whole_home_software_renderer import (
        SEMANTIC_COLORS,
        build_scene_triangles,
    )

    model = {
        'geometry_manifest': {
            'manifest_hash': 'f' * 64,
            'vertices': [[-10, 0, 10], [-9, 0, 10], [-10, 1, 10]],
            'parts': [{
                'id': 'structural-wall', 'indices': [0, 1, 2],
                'render_role': 'wall', 'entity_id': 'wall-1',
            }],
        },
        'walls': [], 'rooms': [], 'openings': [],
        'fixed_objects': [{
            'id': 'cad-bed-1',
            'position': {'x': 0, 'y': 0, 'z': 2},
            'size': {'x': 1.8, 'y': .55, 'z': 2.0},
            'semantic_role': 'bed', 'review_status': 'accepted',
        }],
    }
    triangles = build_scene_triangles(model)
    assert any(row.anchor_id == 'wall-1' for row in triangles)
    bed_rows = [row for row in triangles if row.anchor_id == 'cad-bed-1']
    assert len(bed_rows) == 12
    assert all(row.role == 'bed' for row in bed_rows)
    assert 'bed' in SEMANTIC_COLORS


def test_pano_axis_probe_plus_z_right_handedness():
    """+Z 面 right=-X:世界 -X 方向盒子应在图像右侧,+X 在左侧(不左右翻转)。"""
    from Floor_engine_server import whole_home_pano_render as pano_render
    from Floor_engine_server.whole_home_software_renderer import SEMANTIC_COLORS

    model = {
        'walls': [], 'rooms': [], 'openings': [],
        'fixed_objects': [
            {'id': 'left', 'position': {'x': 2.5 - .6, 'y': 1.25, 'z': 2.5 + 1.2},
             'size': {'x': .5, 'y': .5, 'z': .5}, 'semantic_role': 'sofa',
             'rotation_y_deg': 0, 'review_status': 'accepted'},
            {'id': 'right', 'position': {'x': 2.5 + .6, 'y': 1.25, 'z': 2.5 + 1.2},
             'size': {'x': .5, 'y': .5, 'z': .5}, 'semantic_role': 'bed',
             'rotation_y_deg': 0, 'review_status': 'accepted'},
        ],
    }
    faces = pano_render.render_cube_faces(model, (2.5, 1.5, 2.5), 32, 0.05, 30.0)
    semantic = np.asarray(faces['+Z']['semantic'].convert('RGB'))
    # 视线沿 +Z:right=(-1,0,0) → -X 方向(sofa)在图像右侧。
    assert np.array_equal(semantic[16, 8], np.array(SEMANTIC_COLORS['bed'], dtype=np.uint8)), \
        f'+Z 面左侧应为 +X 方向 bed 色,实际 {semantic[16, 8].tolist()}'
    assert np.array_equal(semantic[16, 24], np.array(SEMANTIC_COLORS['sofa'], dtype=np.uint8)), \
        f'+Z 面右侧应为 -X 方向 sofa 色,实际 {semantic[16, 24].tolist()}'


def test_pano_axis_probe_plus_x_up_not_flipped():
    """+X 面 up=+Y:世界上方盒子应在图像上方(不上下翻转)。"""
    from Floor_engine_server import whole_home_pano_render as pano_render
    from Floor_engine_server.whole_home_software_renderer import SEMANTIC_COLORS

    model = {
        'walls': [], 'rooms': [], 'openings': [],
        'fixed_objects': [
            {'id': 'up', 'position': {'x': 2.5 + 1.2, 'y': 1.85, 'z': 2.5},
             'size': {'x': .5, 'y': .5, 'z': .5}, 'semantic_role': 'tv',
             'rotation_y_deg': 0, 'review_status': 'accepted'},
            {'id': 'down', 'position': {'x': 2.5 + 1.2, 'y': 0.65, 'z': 2.5},
             'size': {'x': .5, 'y': .5, 'z': .5}, 'semantic_role': 'basin',
             'rotation_y_deg': 0, 'review_status': 'accepted'},
        ],
    }
    faces = pano_render.render_cube_faces(model, (2.5, 1.5, 2.5), 32, 0.05, 30.0)
    semantic = np.asarray(faces['+X']['semantic'].convert('RGB'))
    assert np.array_equal(semantic[8, 16], np.array(SEMANTIC_COLORS['tv'], dtype=np.uint8)), \
        f'+X 面上方应为 +Y 方向 tv 色,实际 {semantic[8, 16].tolist()}'
    assert np.array_equal(semantic[24, 16], np.array(SEMANTIC_COLORS['basin'], dtype=np.uint8)), \
        f'+X 面下方应为 -Y 方向 basin 色,实际 {semantic[24, 16].tolist()}'


def test_pano_axis_probe_erp_cardinal_directions():
    """ERP 关键方向与六面渲染一致:中心 +Z、左右边 -Z、顶部 +Y、底部 -Y、u=1/4 与 3/4 为 ±X。"""
    from Floor_engine_server import whole_home_pano_render as pano_render
    from Floor_engine_server.whole_home_software_renderer import SEMANTIC_COLORS

    model, roles = _axis_probe_model()
    faces = pano_render.render_cube_faces(model, (2.5, 1.5, 2.5), 32, 0.05, 30.0)
    erp = pano_render.cube_to_erp(
        {face: faces[face]['semantic'] for face in pano_render.CUBE_FACE_ORDER}, 128, 64)
    arr = np.asarray(erp)
    probes = {
        (32, 64): roles['+Z'],     # λ=0 → +Z
        (32, 0): roles['-Z'],      # λ=-π 左边界 → -Z
        (32, 127): roles['-Z'],    # λ=+π 右边界 → -Z(闭环)
        (0, 64): roles['+Y'],      # 顶部 → +Y
        (63, 64): roles['-Y'],     # 底部 → -Y
        (32, 32): roles['-X'],     # λ=-π/2 → -X
        (32, 96): roles['+X'],     # λ=+π/2 → +X
    }
    for (row, col), role in probes.items():
        expected = np.array(SEMANTIC_COLORS[role], dtype=np.float64)
        actual = arr[row, col].astype(np.float64)
        # 双线性采样在盒子边缘会与背景轻微混合,按最近角色色匹配。
        nearest = min(SEMANTIC_COLORS.values(), key=lambda color: float(np.linalg.norm(np.array(color, dtype=np.float64) - actual)))
        assert np.linalg.norm(actual - expected) < 40 and tuple(nearest) == tuple(SEMANTIC_COLORS[role]), \
            f'ERP({row},{col}) 应为 {role} 色,实际 {arr[row, col].tolist()}'


def test_pano_categorical_cube_to_erp_never_invents_mixed_labels():
    """语义/ID 是离散标签；ERP 投影后每个像素必须仍来自原始标签集合。"""
    from Floor_engine_server import whole_home_pano_render as pano_render
    from PIL import Image as PILImage

    colors = ((0, 0, 0), (216, 212, 202), (139, 92, 246))
    faces = {}
    for face_index, face in enumerate(pano_render.CUBE_FACE_ORDER):
        array = np.zeros((24, 24, 3), dtype=np.uint8)
        array[:, :12] = colors[face_index % len(colors)]
        array[:, 12:] = colors[(face_index + 1) % len(colors)]
        faces[face] = PILImage.fromarray(array, 'RGB')
    erp = pano_render.cube_to_erp(
        faces, 192, 96, interpolation='nearest')
    actual = {tuple(row) for row in np.unique(np.asarray(erp).reshape(-1, 3), axis=0)}
    assert actual <= set(colors), f'最近邻语义投影产生了虚构类别色: {actual - set(colors)}'


def test_verified_pano_room_selection_prefers_specific_room_contract():
    from tools.capture_verified_panos import _select_rooms

    def room(room_id, room_type, x0, z0, x1, z1):
        return {
            'id': room_id, 'room_type': room_type,
            'semantic_profile': room_type,
            'polygon': [
                {'x': x0, 'z': z0}, {'x': x1, 'z': z0},
                {'x': x1, 'z': z1}, {'x': x0, 'z': z1},
            ],
        }

    model = {
        'rooms': [
            room('living', 'living_room', 0, 0, 5, 5),
            room('master', 'bedroom', 6, 0, 10, 4),
            # A larger generic bedroom must not displace an explicitly
            # identified secondary bedroom.
            room('generic', 'bedroom', 11, 0, 16, 5),
            room('secondary', 'bedroom', 11, 0, 14, 3),
        ],
        'room_contracts': [
            {'room_id': 'master', 'reference_room_profile': 'bedroom_master'},
            {'room_id': 'secondary', 'reference_room_profile': 'bedroom_secondary'},
        ],
    }
    selected = [(label, row['id']) for label, row in _select_rooms(model)]
    assert selected == [
        ('living', 'living'), ('master_bedroom', 'master'),
        ('secondary_bedroom', 'secondary'),
    ]


# ── cube↔ERP 往返数学、wrap、极点、atlas golden(§8/§9) ──────

def _gradient_faces(face_size: int = 64) -> dict:
    from PIL import Image as PILImage

    faces = {}
    for index, face in enumerate(whole_home_pano_render.CUBE_FACE_ORDER):
        xx, yy = np.meshgrid(np.arange(face_size), np.arange(face_size))
        arr = np.stack([xx, yy, np.full((face_size, face_size), index * 40)], axis=-1).astype(np.uint8)
        faces[face] = PILImage.fromarray(arr, 'RGB')
    return faces


def test_pano_cube_erp_roundtrip_preserves_linear_gradients():
    """线性渐变在双线性采样下内部区域近乎无损往返。

    面边缘(±45°/±135° 经线附近)的角分辨率高于 ERP 像素密度,重采样会
    损失边缘梯度(文档 §8 的 seam-aware sampling/gutter 即为此设),故断言
    区分内部与边缘区域。
    """
    from Floor_engine_server import whole_home_pano_render as pano_render
    from PIL import Image as PILImage

    face_size = 64
    original = _gradient_faces(face_size)
    erp = pano_render.cube_to_erp(original, face_size * 4, face_size * 2)
    assert erp.size == (face_size * 4, face_size * 2), 'ERP 必须严格 2:1'
    restored = pano_render.erp_to_cube(erp, face_size)
    for face in pano_render.CUBE_FACE_ORDER:
        before = np.asarray(original[face], dtype=np.float64)
        after = np.asarray(restored[face], dtype=np.float64)
        error = np.abs(before - after)
        margin = face_size // 16  # 5% 边缘带
        interior = error[margin:-margin, margin:-margin]
        assert float(interior.mean()) < 2.5, f'{face} 内部往返平均误差 {interior.mean():.2f} 过大'
        assert float(interior.max()) <= 16.0, f'{face} 内部往返最大误差 {interior.max():.2f} 过大'
        assert float(error.mean()) < 6.0, f'{face} 全图往返平均误差 {error.mean():.2f} 过大(边缘重采样损失)'


def test_pano_erp_wrap_edges_share_the_same_meridian():
    """±180° 是同一经线:左右边界应连续(G/B 相同,R 差在 wrap 采样容差内)。"""
    from Floor_engine_server import whole_home_pano_render as pano_render

    erp = pano_render.cube_to_erp(_gradient_faces(), 256, 128)
    arr = np.asarray(erp).astype(np.float64)
    left, right = arr[:, 0], arr[:, -1]
    assert np.all(np.abs(left[:, 1] - right[:, 1]) <= 1), 'G 通道左右边界不连续'
    assert np.all(np.abs(left[:, 2] - right[:, 2]) <= 1), 'B 通道左右边界不连续'
    assert float(np.abs(left[:, 0] - right[:, 0]).mean()) < 4.0, 'R 通道 wrap 差过大'


def test_pano_erp_poles_map_to_y_faces():
    """顶部整行压缩到 +Y、底部到 -Y;极点行不得出现 ±Z 面。"""
    from Floor_engine_server import whole_home_pano_render as pano_render

    erp = pano_render.cube_to_erp(_gradient_faces(), 256, 128)
    arr = np.asarray(erp)
    assert int(arr[0, :, 2].mean()) in range(78, 82), f'顶部应为 +Y 面(80),实际 {int(arr[0, :, 2].mean())}'
    assert int(arr[-1, :, 2].mean()) in range(118, 122), f'底部应为 -Y 面(120),实际 {int(arr[-1, :, 2].mean())}'


def test_pano_atlas_layout_golden():
    """3×2 图集布局 golden:row0 = +X|-X|+Y,row1 = -Y|+Z|-Z;拆分逐像素恢复。"""
    from Floor_engine_server import whole_home_pano_render as pano_render

    original = _gradient_faces()
    atlas = pano_render.cube_faces_to_atlas(original)
    assert atlas.size == (64 * 3, 64 * 2)
    arr = np.asarray(atlas)
    expected = {
        (0, 0): '+X', (0, 1): '-X', (0, 2): '+Y',
        (1, 0): '-Y', (1, 1): '+Z', (1, 2): '-Z',
    }
    face_index = {face: index for index, face in enumerate(pano_render.CUBE_FACE_ORDER)}
    for (row, col), face in expected.items():
        cell = arr[row * 64:(row + 1) * 64, col * 64:(col + 1) * 64, 2]
        assert int(cell.mean()) == face_index[face] * 40, f'格子({row},{col})应为 {face}'
    restored = pano_render.atlas_to_cube_faces(atlas)
    for face in pano_render.CUBE_FACE_ORDER:
        assert np.array_equal(np.asarray(restored[face]), np.asarray(original[face])), f'{face} 拆分不一致'


def test_pano_face_order_golden():
    from Floor_engine_server import whole_home_pano_render as pano_render

    assert tuple(pano_render.CUBE_FACE_ORDER) == ('+X', '-X', '+Y', '-Y', '+Z', '-Z')


# ── 整张 ERP 编辑 prompt 与环形修缝(§7.5)─────────────────────

def test_pano_erp_edit_prompt_contract():
    from Floor_engine_server.whole_home_pano_edit import build_erp_edit_prompt

    manifest = {'pano_id': 'pano_living_01', 'erp_width': 3840, 'erp_height': 1920}
    prompt = build_erp_edit_prompt(manifest, style_description='现代极简')
    for required in (
            'Edit Image 1 only', 'equirectangular', '2:1', 'Geometry authority',
            'Image 2 fixes metric depth order', 'Image 3 fixes world-space surface orientation',
            'Image 4 fixes architectural and object edge boundaries',
            'Image 5 fixes semantic role identity', 'Image 6 fixes exact opening/object instance identity',
            'Panorama contract',
            '-180 and +180 continuous', 'Return one image only'):
        assert required in prompt, f'prompt 缺少 {required}'
    assert 'Text-only approved style/material direction: 现代极简' in prompt
    assert 'Object rule: do not add, delete, duplicate, or move any object.' in prompt


def test_pano_erp_edit_prompt_allows_only_explicit_missing_roles_and_style_refs():
    from Floor_engine_server.whole_home_pano_edit import build_erp_edit_prompt

    manifest = {'pano_id': 'pano_living_01', 'erp_width': 3840, 'erp_height': 1920}
    prompt = build_erp_edit_prompt(
        manifest, style_description='温暖现代', generation_targets=['tv', 'sofa', 'sofa'],
        consistency_contract='same oak floor and warm ivory walls',
        appearance_reference_count=2)
    assert 'Controlled furnishing exception' in prompt
    assert 'sofa, tv' in prompt
    assert 'Add exactly one coherent instance for each listed missing role' in prompt
    assert 'Images 7-8 are accepted panoramas from this exact home' in prompt
    assert 'same oak floor and warm ivory walls' in prompt
    assert 'Object rule: do not add, delete, duplicate, or move any object.' not in prompt


def test_pano_generation_targets_use_room_contract_or_groups():
    project = {'model': {'room_contracts': [{
        'room_id': 'living',
        'missing_role_groups': [['sofa', 'sectional'], ['tv'], []],
    }]}}
    assert routes_whole_home._pano_generation_targets(
        project, {'room_id': 'living'}) == ['sofa', 'tv']
    assert routes_whole_home._pano_generation_targets(
        project, {'room_id': 'bedroom'}) == []


def test_flux_canny_prompt_and_wrap_inputs_are_deterministic():
    from PIL import Image
    from Floor_engine_server.whole_home_pano_edit import (
        build_flux_canny_erp_prompt, finalize_flux_canny_output,
        prepare_flux_canny_inputs,
    )

    rgb = Image.new('RGB', (1024, 512), (120, 130, 140))
    edge = Image.new('RGB', (1024, 512), 'white')
    pixels = edge.load()
    for y in range(512):
        pixels[100, y] = (0, 0, 0)
    padded_rgb, control = prepare_flux_canny_inputs(
        rgb, edge, core_width=1024, core_height=512, gutter_px=32)
    assert padded_rgb.size == control.size == (1088, 512)
    control_array = np.asarray(control)
    assert int(control_array.min()) == 0 and int(control_array.max()) == 255
    # Circular gutters are exact copies, not reflected or blank padding.
    padded_array = np.asarray(padded_rgb)
    assert np.array_equal(padded_array[:, :32], padded_array[:, 1024:1056])
    provider = Image.new('RGB', (1088, 512), (10, 20, 30))
    final = finalize_flux_canny_output(
        provider, target_width=2048, target_height=1024,
        core_width=1024, core_height=512, gutter_px=32)
    assert final.size == (2048, 1024)
    prompt = build_flux_canny_erp_prompt(
        {'pano_id': 'living'}, 'warm oak', generation_targets=['sofa'],
        consistency_contract='same home', gutter_px=32, core_width=1024)
    assert 'authoritative architectural' in prompt
    assert 'same continuous scene' in prompt
    assert 'sofa' in prompt and 'timber screen' in prompt


def test_geometry_locked_materializer_preserves_holdout_pixels(tmp_path):
    from PIL import Image
    from Floor_engine_server.whole_home_pano_material import (
        materialize_geometry_locked_erp, verify_geometry_locked_replay,
    )
    from Floor_engine_server.whole_home_software_renderer import SEMANTIC_COLORS

    width, height = 128, 64
    rgb = Image.new('RGB', (width, height), (180, 180, 180))
    depth = Image.new('L', (width, height), 210).convert('RGB')
    normal = Image.new('RGB', (width, height), (128, 255, 128))
    semantic_array = np.full((height, width, 3), SEMANTIC_COLORS['wall'], dtype=np.uint8)
    semantic_array[height // 2:] = SEMANTIC_COLORS['floor']
    semantic = Image.fromarray(semantic_array, 'RGB')
    mask_array = np.full((height, width), 255, dtype=np.uint8)
    mask_array[:, 60:68] = 0
    mask = Image.fromarray(mask_array, 'L').convert('RGB')
    result = materialize_geometry_locked_erp(
        rgb, depth, normal, semantic, {
            'camera_center_m': {'x': 1, 'y': 1.55, 'z': 2},
            'near_m': .05, 'far_m': 30,
        }, holdout_mask=mask)
    output = np.asarray(result)
    source = np.asarray(rgb)
    assert result.size == (width, height)
    assert np.array_equal(output[:, 60:68], source[:, 60:68])
    assert np.mean(np.abs(output[:, :40].astype(int) - source[:, :40].astype(int))) > 5
    assert int(output[12, 20].mean()) > 190  # warm ivory wall, never TV-black
    # A horizontal wall cap is ceiling by world-normal orientation even when
    # compact 8-bit depth reconstructs it below an arbitrary height threshold.
    assert int(output[height // 2 - 1, 20, 0]) >= 243
    assert int(output[50, 20, 0]) > int(output[50, 20, 2]) + 35  # oak floor
    assert np.max(np.abs(output[:, 0].astype(int) - output[:, -1].astype(int))) <= 6
    paths = {}
    for name, image in {
            'rgb_erp': rgb, 'depth_erp': depth, 'normal_erp': normal,
            'semantic_erp': semantic}.items():
        path = tmp_path / f'{name}.png'
        image.save(path)
        paths[name] = str(path)
    holdout_path = tmp_path / 'holdout.png'
    mask.save(holdout_path)
    candidate_path = tmp_path / 'candidate.png'
    result.save(candidate_path)
    proof = verify_geometry_locked_replay(
        str(candidate_path), paths, {
            'source_hash': 'source-lock',
            'camera_center_m': {'x': 1, 'y': 1.55, 'z': 2},
            'near_m': .05, 'far_m': 30,
        }, holdout_mask_path=str(holdout_path))
    assert proof['status'] == 'pass' and proof['value'] == 0
    assert proof['spatial_operations'] == []


def test_pano_circular_shift_moves_seam_to_center():
    from Floor_engine_server.whole_home_pano_edit import circular_shift_erp
    from PIL import Image as PILImage

    width, height = 64, 32
    source = np.zeros((height, width, 3), dtype=np.uint8)
    source[:, 0] = [255, 0, 0]        # 原左边界(接缝)标记
    image = PILImage.fromarray(source, 'RGB')
    shifted = circular_shift_erp(image)
    arr = np.asarray(shifted)
    # 原左边界列(λ=-180°)移位到图像中央列 width/2。
    assert np.all(arr[:, width // 2] == [255, 0, 0]), '接缝未移到中央'
    # 再 shift 一次回到原位。
    back = circular_shift_erp(shifted)
    assert np.array_equal(np.asarray(back), source), '双 shift 未复原'


def test_pano_seam_repair_mask_angle_band():
    from Floor_engine_server.whole_home_pano_edit import build_seam_repair_mask

    width, height = 1024, 512
    mask = build_seam_repair_mask(width, height, band_deg=12.0)
    arr = np.asarray(mask)
    assert mask.size == (width, height)
    half_band = math.ceil(width * (math.radians(12.0) / math.pi))
    center = width // 2
    assert arr[:, center].max() == 255, '中央带应为白'
    assert arr[:, max(0, center - half_band + 1)].max() == 255
    assert arr[:, center - half_band - 2].max() == 0, '带外应为黑'
    assert arr[:, center + half_band + 2].max() == 0, '带外应为黑'
