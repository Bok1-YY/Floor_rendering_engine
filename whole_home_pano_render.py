# -*- coding: utf-8 -*-
"""同光心六面体渲染 + cube↔ERP 确定性转换(文档 §3.3/§3.4/§7.3/§8)。

不变量:
  * 六面共享同一投影中心 C,只改变射线方向;
  * face order 项目级固定:['+X', '-X', '+Y', '-Y', '+Z', '-Z'];
  * 每面 basis 与 ERP 采样公式共用同一组 (forward, right, up),保证往返一致;
  * ERP:u∈[0,W), v∈[0,H);λ=2π(u/W-1/2), φ=π(1/2-v/H);
    d=(cosφ·sinλ, sinφ, cosφ·cosλ),λ=0 朝 +Z;u=0 与 u=W-1 同属 -Z 经线;
  * depth 为整组统一 metric near/far;normal 为 world-space XYZ→RGB。
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
from PIL import Image

from .whole_home_software_renderer import (
    _rasterize,
    build_scene_triangles,
)

CUBE_FACE_ORDER = ('+X', '-X', '+Y', '-Y', '+Z', '-Z')

# 3×2 图集布局(文档 §7.6):row0 = +X|-X|+Y,row1 = -Y|+Z|-Z
_ATLAS_LAYOUT = (('+X', '-X', '+Y'), ('-Y', '+Z', '-Z'))


def _subject_id_color(index: int) -> tuple[int, int, int]:
    value = max(1, min(0xFFFFFF, int(index) + 1))
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)

_world_up = np.array([0.0, 1.0, 0.0])
_CUBE_BASES: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}


def face_basis(face: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """面 (forward, right, up) 基;渲染与 ERP 采样共用,±Y 面显式定义避免退化。

    约定与 whole_home_software_renderer._camera_basis 完全一致:
    right = forward × world_up(±Y 面用 (0,0,1) 作参考),up = right × forward。
    因此 ±X/±Z 面的 up 恒为 +Y(图像上方=世界上方),+Z 面的右为 -X。
    """
    cached = _CUBE_BASES.get(face)
    if cached is not None:
        return cached
    if face == '+X':
        forward = np.array([1.0, 0.0, 0.0])
        right = np.array([0.0, 0.0, 1.0])
    elif face == '-X':
        forward = np.array([-1.0, 0.0, 0.0])
        right = np.array([0.0, 0.0, -1.0])
    elif face == '+Y':
        forward = np.array([0.0, 1.0, 0.0])
        right = np.array([1.0, 0.0, 0.0])
    elif face == '-Y':
        forward = np.array([0.0, -1.0, 0.0])
        right = np.array([-1.0, 0.0, 0.0])
    elif face == '+Z':
        forward = np.array([0.0, 0.0, 1.0])
        right = np.array([-1.0, 0.0, 0.0])
    else:  # -Z
        forward = np.array([0.0, 0.0, -1.0])
        right = np.array([1.0, 0.0, 0.0])
    up = np.cross(right, forward)
    basis = (forward, right, up)
    _CUBE_BASES[face] = basis
    return basis


def _face_camera(center: tuple[float, float, float], face: str) -> dict:
    """面渲染用相机 dict;90° FOV 等价 focal 12mm 正方形面(_rasterize 的 scale)。"""
    forward, _, _ = face_basis(face)
    return {
        'position': {'x': float(center[0]), 'y': float(center[1]), 'z': float(center[2])},
        'target': {'x': float(center[0] + forward[0]),
                   'y': float(center[1] + forward[1]),
                   'z': float(center[2] + forward[2])},
        'focal_length_mm': 12.0,
    }


def render_cube_faces(model: dict, center_m: tuple[float, float, float],
                      face_size: int, near_m: float, far_m: float,
                      *, subjects: Iterable[dict] | None = None
                      ) -> dict[str, dict[str, Image.Image]]:
    """同一光心渲染六面六通道(文档 §3.1 不变量 + §5.2-4/5)。

    返回 {face: {'rgb','depth','normal','edge','semantic','subject_id'}};depth 使用
    整组统一 metric near/far,normal 为 world-space XYZ→RGB。
    """
    subject_rows = list(subjects) if subjects is not None else []
    triangles = build_scene_triangles(model, subject_rows)
    subject_colors = {
        str(row.get('anchor_id') or ''): _subject_id_color(index)
        for index, row in enumerate(subject_rows) if str(row.get('anchor_id') or '')
    }
    faces: dict[str, dict[str, Image.Image]] = {}
    for face in CUBE_FACE_ORDER:
        _, right, up = face_basis(face)
        basis = np.stack([right, up, face_basis(face)[0]])
        camera = _face_camera(center_m, face)
        faces[face] = _rasterize(
            triangles, camera, face_size, face_size,
            subject_colors=subject_colors,
            metric_depth_range=(float(near_m), float(far_m)),
            world_normal=True, basis_override=basis)
    return faces


def cube_faces_to_atlas(faces: dict[str, Image.Image], face_order: Iterable[str] = CUBE_FACE_ORDER
                        ) -> Image.Image:
    """六面 → 3×2 图集(确定性布局,文档 §7.6)。"""
    order = tuple(face_order)
    face_size = faces[order[0]].size[0]
    atlas = Image.new('RGB', (face_size * 3, face_size * 2))
    for row_index, row in enumerate(_ATLAS_LAYOUT):
        for col_index, face in enumerate(row):
            atlas.paste(faces[face], (col_index * face_size, row_index * face_size))
    return atlas


def atlas_to_cube_faces(atlas: Image.Image, face_order: Iterable[str] = CUBE_FACE_ORDER
                        ) -> dict[str, Image.Image]:
    """3×2 图集按已知布局确定性拆分回六面(不做特征匹配,文档 §8)。"""
    width, height = atlas.size
    face_size = width // 3
    if width % 3 or height != face_size * 2:
        raise ValueError(f'atlas 尺寸 {width}x{height} 不符合 3×2 布局(face={face_size})')
    order = tuple(face_order)
    faces: dict[str, Image.Image] = {}
    for row_index, row in enumerate(_ATLAS_LAYOUT):
        for col_index, face in enumerate(row):
            faces[face] = atlas.crop((
                col_index * face_size, row_index * face_size,
                (col_index + 1) * face_size, (row_index + 1) * face_size))
    return {face: faces[face] for face in order}


def cube_to_erp(faces: dict[str, Image.Image], erp_width: int, erp_height: int,
                face_order: Iterable[str] = CUBE_FACE_ORDER, *,
                interpolation: str = 'bilinear') -> Image.Image:
    """cube→ERP 固定射线公式(文档 §3.4);只做投影,不做相机求解。

    RGB/depth/normal use bilinear sampling.  Categorical semantic/subject-ID
    channels must request ``nearest``: interpolating class colours invents
    non-existent labels and can leak geometry across cube-face boundaries.
    """
    if interpolation not in {'bilinear', 'nearest'}:
        raise ValueError(f'unsupported cube ERP interpolation: {interpolation}')
    order = tuple(face_order)
    face_size = faces[order[0]].size[0]
    arrays = {face: np.asarray(faces[face].convert('RGB'), dtype=np.float32) for face in order}

    u = (np.arange(erp_width, dtype=np.float64) + .5) / erp_width
    v = (np.arange(erp_height, dtype=np.float64) + .5) / erp_height
    lam = 2.0 * np.pi * (u - .5)
    phi = np.pi * (.5 - v)
    cos_phi = np.cos(phi)[:, None]
    dx = cos_phi * np.sin(lam)[None, :]
    dy = np.broadcast_to(np.sin(phi)[:, None], (erp_height, erp_width))
    dz = cos_phi * np.cos(lam)[None, :]
    directions = np.stack([dx, dy, dz], axis=-1).astype(np.float64)  # (H, W, 3)

    face_forward = np.stack([face_basis(face)[0] for face in order])  # (6,3)
    face_right = np.stack([face_basis(face)[1] for face in order])
    face_up = np.stack([face_basis(face)[2] for face in order])

    dots = directions @ face_forward.T                       # (H, W, 6)
    # 带符号取最大:方向 d 与其所朝面法向的点积最大;abs 会在相反方向时选错面。
    axis_face = np.argmax(dots, axis=-1)                     # (H, W)

    output = np.zeros((erp_height, erp_width, 3), dtype=np.float32)
    max_coord = max(face_size - 1, 1)
    for face_index, face in enumerate(order):
        mask = axis_face == face_index
        if not np.any(mask):
            continue
        sc = np.abs(dots[mask, face_index])
        tc = np.einsum('ij,j->i', directions[mask], face_right[face_index]) / sc
        ts = np.einsum('ij,j->i', directions[mask], face_up[face_index]) / sc
        px = (tc + 1.0) * .5 * max_coord
        py = (1.0 - (ts + 1.0) * .5) * max_coord
        if interpolation == 'nearest':
            nearest_x = np.clip(np.floor(px + .5).astype(np.int32), 0, face_size - 1)
            nearest_y = np.clip(np.floor(py + .5).astype(np.int32), 0, face_size - 1)
            output[mask] = arrays[face][nearest_y, nearest_x]
            continue
        x0 = np.clip(np.floor(px).astype(np.int32), 0, face_size - 2)
        y0 = np.clip(np.floor(py).astype(np.int32), 0, face_size - 2)
        fx = (px - x0).astype(np.float32)[:, None]
        fy = (py - y0).astype(np.float32)[:, None]
        arr = arrays[face]
        sampled = (
            arr[y0, x0] * ((1 - fx) * (1 - fy))
            + arr[y0, x0 + 1] * (fx * (1 - fy))
            + arr[y0 + 1, x0] * ((1 - fx) * fy)
            + arr[y0 + 1, x0 + 1] * (fx * fy)
        )
        output[mask] = sampled
    return Image.fromarray(np.clip(output, 0, 255).astype(np.uint8), 'RGB')


def erp_to_cube(erp: Image.Image, face_size: int,
                face_order: Iterable[str] = CUBE_FACE_ORDER
                ) -> dict[str, Image.Image]:
    """ERP→cube 逆变换:X 方向 wrap 采样,极点由 λ 全域覆盖(文档 §8/§9.1-4)。"""
    order = tuple(face_order)
    arr = np.asarray(erp.convert('RGB'), dtype=np.float32)
    height, width = arr.shape[0], arr.shape[1]
    max_coord = max(face_size - 1, 1)
    faces: dict[str, Image.Image] = {}
    px_axis = (np.arange(face_size, dtype=np.float64) + .5) / max_coord
    tc_grid, ts_grid = np.meshgrid(2.0 * px_axis - 1.0, 1.0 - 2.0 * px_axis)  # (N, N)
    for face in order:
        forward, right, up = face_basis(face)
        directions = (
            forward[None, None, :]
            + tc_grid[..., None] * right[None, None, :]
            + ts_grid[..., None] * up[None, None, :]
        )
        norms = np.linalg.norm(directions, axis=-1, keepdims=True)
        directions = directions / np.maximum(norms, 1e-12)
        lam = np.arctan2(directions[..., 0], directions[..., 2])   # d=(cosφ·sinλ, sinφ, cosφ·cosλ)
        phi = np.arcsin(np.clip(directions[..., 1], -1.0, 1.0))
        sample_x = (lam / (2.0 * np.pi) + .5) * width
        sample_y = (.5 - phi / np.pi) * height
        x0 = np.floor(sample_x).astype(np.int32)
        y0 = np.clip(np.floor(sample_y).astype(np.int32), 0, height - 2)
        fx = (sample_x - x0)[..., None]
        fy = (sample_y - y0)[..., None]
        x0w = x0 % width                     # X 向 wrap:±π 是同一经线
        x1w = (x0 + 1) % width
        sampled = (
            arr[y0, x0w] * (1 - fx) * (1 - fy)
            + arr[y0, x1w] * fx * (1 - fy)
            + arr[y0 + 1, x0w] * (1 - fx) * fy
            + arr[y0 + 1, x1w] * fx * fy
        )
        faces[face] = Image.fromarray(np.clip(sampled, 0, 255).astype(np.uint8), 'RGB')
    return faces


def cube_channels_to_erp(faces_by_channel: dict[str, dict[str, Image.Image]],
                         erp_width: int, erp_height: int,
                         face_order: Iterable[str] = CUBE_FACE_ORDER
                         ) -> dict[str, Image.Image]:
    """六面多通道 → ERP 多通道;subject_id 等可选通道缺省时跳过。"""
    return {
        channel: cube_to_erp(
            faces, erp_width, erp_height, face_order,
            interpolation='nearest' if channel in {'semantic', 'subject_id'} else 'bilinear')
        for channel, faces in faces_by_channel.items()
    }
