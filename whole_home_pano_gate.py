# -*- coding: utf-8 -*-
"""球面全景硬门禁(文档 §9):wrap seam、cube edges、12 yaw 结构、极点、opening 窗口。

v1 范围与限制(如实声明):
  * 候选只有 RGB ERP(AI 编辑输出无 depth/normal),depth order 检查标注 skipped;
  * 全部阈值角度化(§9.2):horizontal_error_deg = pixel_error × 360 / ERP_width,
    不同分辨率使用同一物理阈值;
  * 参考通道来自确定性 clay 渲染(manifest.channels 的 *_erp 路径),是唯一几何权威。
"""
from __future__ import annotations

import math
import os
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from .whole_home_pano_render import CUBE_FACE_ORDER, erp_to_cube, face_basis

GATE_VERSION = 'whole-home-pano-gate-v2'

# 首轮保守门槛(文档 §9.2,后续用真实项目标定并版本化,不得静默改历史)。
WRAP_SEAM_COLOR_MEDIAN_MAX = 15.0        # /255
WRAP_BAND_VS_REFERENCE_MAX = 22.0        # /255,候选带相对参考带的变化上限
CUBE_EDGE_COLOR_MEDIAN_MAX = 22.0        # /255
POLE_NEIGHBOR_MEAN_MAX = 25.0            # /255
STRUCTURE_DIRECTED_RECALL_MIN = 0.95     # reference structure found near candidate edges
STRUCTURE_P95_DISPLACEMENT_DEG_MAX = .5
STRUCTURE_LONG_LINE_PRECISION_MIN = .85
OPENING_EDGE_RATIO_MIN = 0.5             # 开口窗口边缘保留率下限(不消失)
OPENING_EDGE_RATIO_MAX = 2.0             # 上限(不新增)
BAND_FRACTION = 0.06                     # 缝带宽度(相对图像宽度)
PROTECTED_RGB_P95_MAX = 12.0
PROTECTED_CHANGED_FRACTION_MAX = .05


def pixels_to_deg(pixels: float, width: int) -> float:
    return float(pixels) * 360.0 / max(1, int(width))


def _rgb_array(path: str) -> Optional[np.ndarray]:
    if not path or not os.path.isfile(path):
        return None
    image = Image.open(path)
    image.load()
    return np.asarray(image.convert('RGB'), dtype=np.float32)


def _edge_map(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    return (edges > 0).astype(np.uint8)


def erp_to_perspective(erp: Image.Image, yaw_deg: float, pitch_deg: float,
                       fov_deg: float, width: int, height: int) -> Image.Image:
    """ERP → 透视检查视图(§9.1-5):固定射线公式,不做特征匹配。"""
    arr = np.asarray(erp.convert('RGB'), dtype=np.float32)
    erp_h, erp_w = arr.shape[0], arr.shape[1]
    focal = (width / 2.0) / math.tan(math.radians(max(1.0, float(fov_deg)) / 2.0))
    yaw = math.radians(float(yaw_deg))
    pitch = math.radians(float(pitch_deg))
    forward = np.array([math.cos(pitch) * math.sin(yaw),
                        math.sin(pitch),
                        math.cos(pitch) * math.cos(yaw)])
    right = np.cross(forward, np.array([0.0, 1.0, 0.0]))
    if float(np.linalg.norm(right)) < 1e-9:
        right = np.array([1.0, 0.0, 0.0])
    right = right / np.linalg.norm(right)
    up = np.cross(right, forward)
    xs = (np.arange(width, dtype=np.float64) + .5) - width / 2.0
    ys = (np.arange(height, dtype=np.float64) + .5) - height / 2.0
    px, py = np.meshgrid(xs, ys)
    directions = (
        forward[None, None, :]
        + (px / focal)[..., None] * right[None, None, :]
        + (-py / focal)[..., None] * up[None, None, :]
    )
    norms = np.linalg.norm(directions, axis=-1, keepdims=True)
    directions = directions / np.maximum(norms, 1e-12)
    lam = np.arctan2(directions[..., 0], directions[..., 2])
    phi = np.arcsin(np.clip(directions[..., 1], -1.0, 1.0))
    sample_x = (lam / (2.0 * np.pi) + .5) * erp_w
    sample_y = (.5 - phi / np.pi) * erp_h
    x0 = np.floor(sample_x).astype(np.int32)
    y0 = np.clip(np.floor(sample_y).astype(np.int32), 0, erp_h - 2)
    fx = (sample_x - x0)[..., None]
    fy = (sample_y - y0)[..., None]
    x0w = x0 % erp_w
    x1w = (x0 + 1) % erp_w
    sampled = (
        arr[y0, x0w] * (1 - fx) * (1 - fy)
        + arr[y0, x1w] * fx * (1 - fy)
        + arr[y0 + 1, x0w] * (1 - fx) * fy
        + arr[y0 + 1, x1w] * fx * fy
    )
    return Image.fromarray(np.clip(sampled, 0, 255).astype(np.uint8), 'RGB')


def _wrap_band_check(candidate: np.ndarray, reference: np.ndarray) -> dict:
    """Compare only the adjacent -180/+180 samples, never two different bands."""
    width = candidate.shape[1]
    band = max(2, int(width * BAND_FRACTION))
    seam_candidate = np.abs(candidate[:, 0].astype(np.float32) - candidate[:, -1].astype(np.float32))
    seam_reference = np.abs(reference[:, 0].astype(np.float32) - reference[:, -1].astype(np.float32))
    color_median = float(np.median(seam_candidate))
    reference_median = float(np.median(seam_reference))
    edge_c = _edge_map(candidate)
    edge_r = _edge_map(reference)
    edge_diff = float(np.mean(np.abs(edge_c[:, 0].astype(np.float32) - edge_c[:, -1].astype(np.float32))))
    edge_ref = float(np.mean(np.abs(edge_r[:, 0].astype(np.float32) - edge_r[:, -1].astype(np.float32))))
    pass_color = color_median <= max(WRAP_SEAM_COLOR_MEDIAN_MAX, reference_median + 8.0)
    pass_edge = edge_diff <= max(edge_ref + .04, .06)
    # §9.1-3:circular shift W/2 后,原接缝移到中央,中央带不能出现新直线。
    shifted_c = np.roll(candidate, width // 2, axis=1)
    shifted_r = np.roll(reference, width // 2, axis=1)
    center_c = _edge_map(shifted_c)[:, width // 2 - band: width // 2 + band].mean()
    center_r = _edge_map(shifted_r)[:, width // 2 - band: width // 2 + band].mean()
    pass_shift = center_c <= max(center_r * 2.0 + 1e-6, 0.08)
    return {
        'check_id': 'wrap_seam', 'status': 'pass' if (pass_color and pass_edge and pass_shift) else 'fail',
        'metric': 'color_median_abs_diff', 'value': round(color_median, 2),
        'threshold': round(max(WRAP_SEAM_COLOR_MEDIAN_MAX, reference_median + 8.0), 2),
        'reference_seam_median': round(reference_median, 2),
        'edge_candidate_band_diff': round(edge_diff, 4), 'edge_reference_band_diff': round(edge_ref, 4),
        'shifted_center_edge_candidate': round(float(center_c), 4),
        'shifted_center_edge_reference': round(float(center_r), 4),
        'detail': 'adjacent ERP boundary columns + shifted-center edge continuity',
    }


def _cube_edge_pairs():
    """12 条 cube 共享边:相邻面(forward 正交)的交线方向 = cross(forward_a, forward_b)。"""
    edges: dict[str, dict[str, np.ndarray]] = {}
    for face in CUBE_FACE_ORDER:
        _, right, up = face_basis(face)
        edges[face] = {'top': up, 'bottom': -up, 'left': -right, 'right': right}
    pairs = []

    def edge_label_for(face: str, direction: np.ndarray) -> Optional[str]:
        for label, vector in edges[face].items():
            if np.allclose(vector, direction):
                return label
        return None

    for index, face_a in enumerate(CUBE_FACE_ORDER):
        for face_b in CUBE_FACE_ORDER[index + 1:]:
            forward_a = face_basis(face_a)[0]
            forward_b = face_basis(face_b)[0]
            if abs(float(np.dot(forward_a, forward_b))) >= 1e-6:
                continue  # 非相邻(对面或同一面)
            direction = np.cross(forward_a, forward_b)
            norm = float(np.linalg.norm(direction))
            if norm < 1e-9:
                continue
            direction = direction / norm
            edge_a = edge_label_for(face_a, direction) or edge_label_for(face_a, -direction)
            edge_b = edge_label_for(face_b, direction) or edge_label_for(face_b, -direction)
            if edge_a and edge_b:
                pairs.append((face_a, edge_a, face_b, edge_b))
    return pairs


def _edge_band(array: np.ndarray, edge: str, band: int) -> np.ndarray:
    if edge == 'top':
        return array[:band, :]
    if edge == 'bottom':
        return array[-band:, :]
    if edge == 'left':
        return array[:, :band]
    return array[:, -band:]


def _edge_strip_along_edge(array: np.ndarray, edge: str, band: int) -> np.ndarray:
    """沿共享边方向的一维像素序列:top/bottom 沿列方向,left/right 沿行方向。"""
    strip = _edge_band(array, edge, band)
    if edge in ('left', 'right'):
        strip = strip.transpose(1, 0, 2)
    return strip.reshape(-1, 3)


def _cube_edge_check(candidate: np.ndarray, reference: np.ndarray, face_size: int) -> dict:
    """12 条 cube 边:重投影回 cubemap 后按相同面方向比较两侧带(§9.1-4)。"""
    candidate_cube = erp_to_cube(Image.fromarray(np.clip(candidate, 0, 255).astype(np.uint8), 'RGB'), face_size)
    reference_cube = erp_to_cube(Image.fromarray(np.clip(reference, 0, 255).astype(np.uint8), 'RGB'), face_size)
    band = max(2, face_size // 16)
    worst = 0.0
    worst_pair = ''
    failures = 0
    pairs = _cube_edge_pairs()
    for face_a, edge_a, face_b, edge_b in pairs:
        strip_ca = _edge_strip_along_edge(np.asarray(candidate_cube[face_a]), edge_a, band)
        strip_cb = _edge_strip_along_edge(np.asarray(candidate_cube[face_b]), edge_b, band)
        strip_ra = _edge_strip_along_edge(np.asarray(reference_cube[face_a]), edge_a, band)
        strip_rb = _edge_strip_along_edge(np.asarray(reference_cube[face_b]), edge_b, band)
        # 同一球面方向的带,逐像素比较两侧(§9.1-4:按相同球面方向采样)。
        length = min(strip_ca.shape[0], strip_cb.shape[0])
        cand_diff = float(np.median(np.abs(strip_ca[:length] - strip_cb[:length])))
        ref_diff = float(np.median(np.abs(strip_ra[:length] - strip_rb[:length])))
        score = cand_diff - ref_diff
        if score > worst:
            worst = score
            worst_pair = f'{face_a}:{edge_a}-{face_b}:{edge_b}'
        if score > CUBE_EDGE_COLOR_MEDIAN_MAX:
            failures += 1
    return {
        'check_id': 'cube_edges', 'status': 'pass' if failures == 0 else 'fail',
        'metric': 'median_pair_delta_vs_reference', 'value': round(worst, 2),
        'threshold': CUBE_EDGE_COLOR_MEDIAN_MAX, 'failures': failures,
        'worst_pair': worst_pair, 'total_pairs': len(pairs),
        'detail': '12 cube edge strips compared by shared spherical direction',
    }


def _structure_view_check(candidate: np.ndarray, reference: np.ndarray) -> dict:
    """Reference-directed edge recall tolerates texture but not structural drift."""
    erp_w = candidate.shape[1]
    candidate_pil = Image.fromarray(np.clip(candidate, 0, 255).astype(np.uint8), 'RGB')
    reference_pil = Image.fromarray(np.clip(reference, 0, 255).astype(np.uint8), 'RGB')
    view_width, view_height = 320, 240
    worst_recall = 1.0
    worst_p95_deg = 0.0
    worst_long_line_precision = 1.0
    worst_view = ''
    failures = 0
    views = 0
    degrees_per_pixel = 60.0 / view_width
    tolerance_px = max(1.0, STRUCTURE_P95_DISPLACEMENT_DEG_MAX / degrees_per_pixel)
    for yaw in range(0, 360, 30):
        for pitch in (0, 45, -45):
            cand_view = erp_to_perspective(candidate_pil, yaw, pitch, 60, view_width, view_height)
            ref_view = erp_to_perspective(reference_pil, yaw, pitch, 60, view_width, view_height)
            cand_edge = _edge_map(np.asarray(cand_view))
            ref_edge = _edge_map(np.asarray(ref_view))
            distances = cv2.distanceTransform((1 - cand_edge).astype(np.uint8), cv2.DIST_L2, 3)
            ref_distances = distances[ref_edge > 0]
            recall = (float(np.mean(ref_distances <= tolerance_px))
                      if ref_distances.size else 1.0)
            p95_deg = (float(np.percentile(ref_distances, 95)) * degrees_per_pixel
                       if ref_distances.size else 0.0)
            reference_distances = cv2.distanceTransform(
                (1 - ref_edge).astype(np.uint8), cv2.DIST_L2, 3)
            lines = cv2.HoughLinesP(
                (cand_edge * 255).astype(np.uint8), 1, np.pi / 180,
                threshold=60, minLineLength=int(view_height * .65), maxLineGap=6)
            long_line_total = 0
            long_line_supported = 0
            for line in ([] if lines is None else lines[:, 0, :]):
                x0, y0, x1, y1 = [int(value) for value in line]
                length = math.hypot(x1 - x0, y1 - y0)
                if length < view_height * .65:
                    continue
                count = max(8, int(length / 4))
                xs = np.clip(np.rint(np.linspace(x0, x1, count)).astype(np.int32), 0, view_width - 1)
                ys = np.clip(np.rint(np.linspace(y0, y1, count)).astype(np.int32), 0, view_height - 1)
                support = float(np.mean(reference_distances[ys, xs] <= tolerance_px))
                long_line_total += 1
                if support >= .80:
                    long_line_supported += 1
            long_line_precision = (
                float(long_line_supported) / long_line_total if long_line_total else 1.0)
            views += 1
            if recall < worst_recall or p95_deg > worst_p95_deg:
                worst_view = f'yaw={yaw},pitch={pitch}'
            worst_recall = min(worst_recall, recall)
            worst_p95_deg = max(worst_p95_deg, p95_deg)
            worst_long_line_precision = min(worst_long_line_precision, long_line_precision)
            if (recall < STRUCTURE_DIRECTED_RECALL_MIN
                    or p95_deg > STRUCTURE_P95_DISPLACEMENT_DEG_MAX
                    or long_line_precision < STRUCTURE_LONG_LINE_PRECISION_MIN):
                failures += 1
    return {
        'check_id': 'structure_views', 'status': 'pass' if failures == 0 else 'fail',
        'metric': 'reference_edge_directed_recall_min', 'value': round(worst_recall, 4),
        'threshold': STRUCTURE_DIRECTED_RECALL_MIN,
        'p95_displacement_deg': round(worst_p95_deg, 4),
        'p95_displacement_threshold_deg': STRUCTURE_P95_DISPLACEMENT_DEG_MAX,
        'candidate_long_line_precision_min': round(worst_long_line_precision, 4),
        'candidate_long_line_precision_threshold': STRUCTURE_LONG_LINE_PRECISION_MIN,
        'views': views, 'failures': failures,
        'worst_view': worst_view,
        'angle_scale': f'{pixels_to_deg(1, erp_w):.4f} deg/px (horizontal)',
        'detail': '36 perspective views; reference edges must have nearby candidate support',
    }


def _protected_region_check(candidate: np.ndarray, reference: np.ndarray,
                            mask_path: str) -> dict:
    mask = _rgb_array(mask_path)
    if mask is None or mask.shape[:2] != candidate.shape[:2]:
        return {
            'check_id': 'protected_region', 'status': 'fail', 'metric': 'mask_missing',
            'value': 1, 'threshold': 0, 'detail': 'structure holdout mask missing or wrong size',
        }
    protected = np.mean(mask, axis=2) < 64
    if not np.any(protected):
        return {
            'check_id': 'protected_region', 'status': 'fail', 'metric': 'protected_pixels',
            'value': 0, 'threshold': '>0', 'detail': 'mask contains no protected structure',
        }
    delta = np.max(np.abs(candidate - reference), axis=2)[protected]
    p95 = float(np.percentile(delta, 95))
    changed_fraction = float(np.mean(delta > PROTECTED_RGB_P95_MAX))
    passed = (p95 <= PROTECTED_RGB_P95_MAX
              and changed_fraction <= PROTECTED_CHANGED_FRACTION_MAX)
    return {
        'check_id': 'protected_region', 'status': 'pass' if passed else 'fail',
        'metric': 'protected_rgb_delta_p95', 'value': round(p95, 3),
        'threshold': PROTECTED_RGB_P95_MAX,
        'changed_fraction': round(changed_fraction, 5),
        'changed_fraction_threshold': PROTECTED_CHANGED_FRACTION_MAX,
        'protected_pixel_count': int(protected.sum()),
        'detail': 'black mask bands are immutable architectural samples',
    }


def _pole_check(candidate: np.ndarray, reference: np.ndarray) -> dict:
    """极点检查(§9.1-9):顶部/底部带的相邻行/列差与 edge 密度异常(拉丝/洞/放射状重复)。"""
    height, width = candidate.shape[:2]
    band = max(2, int(height * 0.06))
    worst = 0.0
    for rows, label in ((slice(0, band), 'north_pole'), (slice(-band, None), 'south_pole')):
        cand_strip = candidate[rows].astype(np.float32)
        ref_strip = reference[rows].astype(np.float32)
        cand_col = float(np.mean(np.abs(cand_strip[:, 1:] - cand_strip[:, :-1])))
        ref_col = float(np.mean(np.abs(ref_strip[:, 1:] - ref_strip[:, :-1])))
        cand_row = float(np.mean(np.abs(cand_strip[1:] - cand_strip[:-1])))
        ref_row = float(np.mean(np.abs(ref_strip[1:] - ref_strip[:-1])))
        worst = max(worst, abs(cand_col - ref_col), abs(cand_row - ref_row))
        cand_edges = float(_edge_map(candidate)[rows].mean())
        ref_edges = float(_edge_map(reference)[rows].mean())
        worst = max(worst, abs(cand_edges - ref_edges) * 255.0)
    return {
        'check_id': 'poles', 'status': 'pass' if worst <= POLE_NEIGHBOR_MEAN_MAX else 'fail',
        'metric': 'pole_neighbor_delta_vs_reference', 'value': round(worst, 2),
        'threshold': POLE_NEIGHBOR_MEAN_MAX,
        'detail': 'north/south pole band row/column continuity & edge density',
    }


def _opening_window_check(candidate: np.ndarray, reference: np.ndarray,
                          model: dict, manifest: dict) -> dict:
    """opening identity(§9.1-6/9.1-7):每个 accepted opening 的角窗口内结构
    edge 保留率在 [0.5, 2.0];±180° 接缝处不重复(v1 以 wrap 带 edge 覆盖)。"""
    center = manifest.get('camera_center_m') or {}
    cx, cy, cz = (float(center.get(axis) or 0) for axis in ('x', 'y', 'z'))
    erp_w = candidate.shape[1]
    openings = [row for row in model.get('openings') or [] if row.get('review_status') == 'accepted']
    edge_c = _edge_map(candidate)
    edge_r = _edge_map(reference)
    worst_ratio = 1.0
    highest_ratio = 1.0
    checked = 0
    failures = 0
    for opening in openings:
        wall = next((row for row in model.get('walls') or []
                     if str(row.get('id') or '') == str(opening.get('wall_id') or '')), None)
        if not wall:
            continue
        start, end = wall.get('start') or {}, wall.get('end') or {}
        x0, z0 = float(start.get('x') or 0), float(start.get('z') or 0)
        x1, z1 = float(end.get('x') or 0), float(end.get('z') or 0)
        along = float(opening.get('offset_m') or 0) + float(opening.get('width_m') or .8) / 2
        length = max(.001, math.hypot(x1 - x0, z1 - z0))
        ox = x0 + (x1 - x0) * along / length
        oz = z0 + (z1 - z0) * along / length
        dx, dz = ox - cx, oz - cz
        if math.hypot(dx, dz) < 1e-6:
            continue
        lam_center = math.atan2(dx, dz)
        half_angle = max(1.0, math.degrees(
            math.atan2(float(opening.get('width_m') or .8) / 2.0, math.hypot(dx, dz))))
        col_center = int((lam_center / (2 * math.pi) + .5) * erp_w) % erp_w
        span = max(2, int(half_angle * 2 / 360.0 * erp_w))
        cols = (np.arange(col_center - span // 2, col_center + span // 2)) % erp_w
        rows = slice(int(erp_w * 0), edge_c.shape[0])  # 全高窗口(v1 简化)
        window_c = edge_c[:, cols].sum()
        window_r = edge_r[:, cols].sum()
        if window_r <= 0:
            continue
        ratio = float(window_c) / float(window_r)
        checked += 1
        if ratio < OPENING_EDGE_RATIO_MIN or ratio > OPENING_EDGE_RATIO_MAX:
            failures += 1
        worst_ratio = min(worst_ratio, ratio) if ratio < worst_ratio else worst_ratio
        highest_ratio = max(highest_ratio, ratio)
    return {
        'check_id': 'opening_identity', 'status': 'pass' if failures == 0 else 'fail',
        'metric': 'opening_edge_ratio_min', 'value': round(worst_ratio, 3),
        'opening_edge_ratio_max': round(highest_ratio, 3),
        'threshold': f'[{OPENING_EDGE_RATIO_MIN}, {OPENING_EDGE_RATIO_MAX}]',
        'openings_checked': checked, 'failures': failures,
        'detail': 'structure edge retention inside accepted opening angular windows',
    }


def gate_pano_erp(candidate_path: str, reference_channels: dict, manifest: dict,
                  model: dict, *, face_size: int = 256,
                  protected_mask_path: str = '') -> dict:
    """球面硬门禁主入口(§9.1)。返回 checks 列表与 gate_pass。"""
    gate_level = 'p0_rgb_structural'
    candidate = _rgb_array(candidate_path)
    reference = _rgb_array(reference_channels.get('rgb_erp') or '')
    if candidate is None:
        return {'gate_pass': False, 'full_contract_pass': False,
                'gate_level': gate_level, 'not_evaluable': ['all'],
                'version': GATE_VERSION, 'checks': [],
                'summary': '候选 ERP 无法读取', 'hard_fail': True}
    if reference is None:
        return {'gate_pass': False, 'full_contract_pass': False,
                'gate_level': gate_level, 'not_evaluable': ['all'],
                'version': GATE_VERSION, 'checks': [],
                'summary': '参考 ERP 无法读取', 'hard_fail': True}
    width, height = candidate.shape[1], candidate.shape[0]
    size_ok = (width == height * 2)
    manifest_ok = int(manifest.get('erp_width') or 0) == width and int(manifest.get('erp_height') or 0) == height
    checks = [{
        'check_id': 'size_manifest', 'status': 'pass' if (size_ok and manifest_ok) else 'fail',
        'metric': 'erp_2_1_and_manifest', 'value': f'{width}x{height}',
        'threshold': f'{manifest.get("erp_width")}x{manifest.get("erp_height")}',
        'detail': 'strict 2:1 + manifest size match',
    }]
    if size_ok and manifest_ok:
        checks.extend([
            _wrap_band_check(candidate, reference),
            _cube_edge_check(candidate, reference, face_size),
            _structure_view_check(candidate, reference),
            _pole_check(candidate, reference),
            _opening_window_check(candidate, reference, model, manifest),
        ])
        if protected_mask_path:
            checks.append(_protected_region_check(
                candidate, reference, protected_mask_path))
        checks.append({
            'check_id': 'depth_order', 'status': 'skipped',
            'metric': 'n/a', 'value': 0, 'threshold': 'n/a',
            'detail': 'v1 候选仅有 RGB;深度顺序检查依赖候选 depth,待 P1(文档 §9.1-8)',
        })
    else:
        for missing in ('wrap_seam', 'cube_edges', 'structure_views', 'poles', 'opening_identity'):
            checks.append({'check_id': missing, 'status': 'fail', 'metric': 'n/a',
                           'value': 0, 'threshold': 'n/a', 'detail': '尺寸契约失败,跳过'})
    # gate_pass 只代表当前 P0 RGB/结构门禁；depth_order 明确不可评估，绝不把
    # skipped 冒充完整合同通过。full_contract_pass 在 P1 有候选深度前恒为 False。
    required = [row for row in checks if row['check_id'] != 'depth_order']
    passed = all(row['status'] == 'pass' for row in required)
    failures = [row for row in checks if row['status'] == 'fail']
    not_evaluable = [row['check_id'] for row in checks if row['status'] == 'skipped']
    return {
        'gate_pass': passed,
        'full_contract_pass': passed and not not_evaluable,
        'gate_level': gate_level,
        'not_evaluable': not_evaluable,
        'version': GATE_VERSION,
        'checks': checks,
        'hard_fail': not passed,
        'summary': '; '.join(f"{row['check_id']}:{row['status']}" for row in checks)[:1000],
        'failures': [row['check_id'] for row in failures],
        'angle_scale': f'{pixels_to_deg(1, width):.4f} deg/px',
    }


def certify_geometry_locked_gate(result: dict, replay_check: dict) -> dict:
    """Use pixel-exact deterministic replay as structural proof for local material RGB.

    RGB Canny ratios remain attached as diagnostics, but they cannot distinguish
    a new material boundary from a moved wall.  A replay with zero spatial
    operations and zero mismatched pixels is a stronger proof for the three
    spatial/identity checks.  Seam, pole, size and protected-region checks are
    never overridden.
    """
    checks = result.setdefault('checks', [])
    checks.append(dict(replay_check or {}))
    replay_passed = (
        replay_check.get('status') == 'pass'
        and int(replay_check.get('value') or 0) == 0
        and replay_check.get('coordinate_transform') == 'identity_pixel_grid'
        and replay_check.get('spatial_operations') == [])
    if replay_passed:
        for row in checks:
            if row.get('check_id') not in {
                    'cube_edges', 'structure_views', 'opening_identity'}:
                continue
            row['diagnostic_status'] = row.get('status')
            row['diagnostic_value'] = row.get('value')
            if row.get('opening_edge_ratio_max') is not None:
                row['diagnostic_opening_edge_ratio_max'] = row.get('opening_edge_ratio_max')
            row['status'] = 'pass'
            row['certified_by'] = 'geometry_locked_replay'
            row['detail'] = (
                'RGB edge metric retained as diagnostic; structural identity certified by '
                'pixel-exact identity-grid replay with zero spatial operations')
    required = [row for row in checks if row.get('check_id') != 'depth_order']
    passed = bool(required) and all(row.get('status') == 'pass' for row in required)
    not_evaluable = [row.get('check_id') for row in checks if row.get('status') == 'skipped']
    result.update({
        'gate_pass': passed,
        'full_contract_pass': passed and not not_evaluable,
        'hard_fail': not passed,
        'not_evaluable': not_evaluable,
        'failures': [row.get('check_id') for row in checks if row.get('status') == 'fail'],
        'summary': '; '.join(
            f"{row.get('check_id')}:{row.get('status')}" for row in checks)[:1000],
        'gate_profile': 'geometry_locked_material',
    })
    return result


def pano_qa_constraints(manifest: dict, model: dict) -> list[dict]:
    """Gemini QA 逐条约束清单(文档 §9 对抗性 QA,与 evaluate_whole_home_phase
    的 constraint 模式一致):候选全景必须逐项作答,不确定即失败。"""
    pano_id = str(manifest.get('pano_id') or '')
    openings = [row for row in model.get('openings') or [] if row.get('review_status') == 'accepted']
    objects = [row for row in model.get('fixed_objects') or [] if row.get('review_status') != 'rejected']
    return [
        {'constraint_id': 'P101', 'constraint': '候选保持完整 2:1 equirectangular 布局,'
                                               '无画框、标签、分栏、文字或裁切。'},
        {'constraint_id': 'P102', 'constraint': '经度 -180° 与 +180° 接缝在几何、纹理、光线和地板缝上连续。'},
        {'constraint_id': 'P103', 'constraint': '地平线平直;天顶/地底无放射状重复、洞或拉丝。'},
        {'constraint_id': 'P104', 'constraint': '墙体数量与位置与 clay 参考一致;墙线保持直线,不弯曲。'},
        {'constraint_id': 'P105', 'constraint': f'每个 accepted opening 必须出现且朝向/宽度不变,'
                                               f'当前共 {len(openings)} 个;不得新增或删除。'},
        {'constraint_id': 'P106', 'constraint': f'每个 required object 只出现一次(共 {len(objects)} 个),'
                                               f'不得在 ±180° 接缝重复或镜像。'},
        {'constraint_id': 'P107', 'constraint': '主要遮挡顺序与 clay depth 参考一致;墙后空间不得被显露。'},
        {'constraint_id': 'P108', 'constraint': '视点不可移动;所有方向必须来自同一投影中心。'},
        {'constraint_id': 'P109', 'constraint': '与整屋其他热点共享的物体/材质一致(按参考图核验)。'},
        {'constraint_id': 'P110', 'constraint': f'全景 id {pano_id}:仅允许把 clay 场景转成装修外观,'
                                               f'禁止改变任何建筑事实。'},
    ]
