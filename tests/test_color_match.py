# 旧区域引擎兼容测试 + 当前全图校色、三区诊断与分片一致性测试
# + 校色端点硬化（路径逃逸、参照目录白名单、候选归属、b64-only 记录）。
import asyncio
import base64
import io
import json
import os

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import server_state, server_helpers, server_schemas, routes_tools
from Floor_engine_server import records, server_api
from Floor_engine_server.color_match import (
    apply_color_adjustments,
    apply_color_adjustments_striped,
    analyze_color_region,
    match_color_global,
    match_color_masked,
    match_color_region,
)
from Floor_engine_server.floor_segmentation import encode_mask_png, segment_floor
from Floor_engine_server.models import new_job
from Floor_engine_server.task_registry import TaskRegistry


def _solid(w, h, color):
    return Image.new('RGB', (w, h), color)


def _split_lr(w, h, left, right):
    """左半 left 色、右半 right 色的图。"""
    img = Image.new('RGB', (w, h), left)
    img.paste(Image.new('RGB', (w // 2, h), right), (w // 2, 0))
    return img


def test_region_changes_inside_untouched_outside():
    # 左红右蓝；选区罩右半；参照=灰绿 → 选区中心显著变化，羽化带外（左半远端）逐像素等于原图
    src = _split_lr(400, 300, (200, 40, 40), (40, 40, 200))
    ref = _solid(120, 120, (110, 150, 110))
    out = match_color_region(src, ref, (0.5, 0.0, 0.5, 1.0), strength=1.0, feather=0.05)
    a_src = np.asarray(src, dtype=np.int16)
    a_out = np.asarray(out, dtype=np.int16)
    # 选区中心（右半中央）应显著变化
    cy, cx = 150, 300
    assert np.abs(a_out[cy, cx] - a_src[cy, cx]).max() > 20
    # 羽化带外：feather_px = 0.05*300 = 15，选区左界 x=200 → x<185 必须逐像素不变
    assert np.array_equal(a_out[:, :185], a_src[:, :185])


def test_strength_zero_identity():
    src = _split_lr(200, 150, (200, 40, 40), (40, 40, 200))
    ref = _solid(60, 60, (110, 150, 110))
    out = match_color_region(src, ref, (0.2, 0.2, 0.6, 0.6), strength=0.0)
    assert np.array_equal(np.asarray(out), np.asarray(src.convert('RGB')))


def test_tiny_rect_identity():
    src = _solid(300, 200, (120, 90, 60))
    ref = _solid(50, 50, (30, 200, 30))
    out = match_color_region(src, ref, (0.5, 0.5, 0.005, 0.005), strength=1.0)
    assert np.array_equal(np.asarray(out), np.asarray(src))


def test_feather_no_hard_jump():
    # 横穿选区左边界的一行像素：相邻差每通道应小于阈值（线性羽化，无硬跳变）
    src = _solid(400, 300, (60, 60, 60))
    ref = _solid(80, 80, (200, 160, 120))
    out = match_color_region(src, ref, (0.5, 0.0, 0.5, 1.0), strength=1.0, feather=0.1)
    row = np.asarray(out, dtype=np.int16)[150, :]
    step = np.abs(np.diff(row, axis=0)).max()
    assert step < 12, f'羽化带出现硬跳变，max diff={step}'


def test_full_strength_region_stats_move_toward_ref():
    # 全强度下选区整体均值应向参照均值靠拢
    src = _solid(300, 200, (200, 50, 50))
    ref = _solid(80, 80, (60, 120, 180))
    out = match_color_region(src, ref, (0.0, 0.0, 1.0, 1.0), strength=1.0, feather=0.0)
    a_out = np.asarray(out, dtype=np.float32)
    src_mean = np.array([200, 50, 50], dtype=np.float32)
    ref_mean = np.array([60, 120, 180], dtype=np.float32)
    out_mean = a_out.reshape(-1, 3).mean(axis=0)
    # 输出均值到参照的距离应远小于原图到参照的距离（LAB 迁移非逐通道 RGB 等值，容差放宽）
    assert np.linalg.norm(out_mean - ref_mean) < np.linalg.norm(src_mean - ref_mean) * 0.5


def test_rgba_and_palette_inputs_ok():
    src = Image.new('RGBA', (200, 150), (200, 40, 40, 255))
    ref = Image.new('RGB', (50, 50), (60, 120, 60)).convert('P')
    out = match_color_region(src, ref, (0.1, 0.1, 0.8, 0.8), strength=0.8)
    assert out.mode == 'RGB' and out.size == (200, 150)


def test_rect_clamped_out_of_bounds():
    src = _solid(200, 150, (120, 90, 60))
    ref = _solid(50, 50, (30, 200, 30))
    # 选区超界 → 钳制到图内，不抛异常
    out = match_color_region(src, ref, (0.8, 0.8, 0.9, 0.9), strength=1.0)
    assert out.size == (200, 150)


def test_outlier_object_in_region_protected():
    # 选区=米色地板 + 一小块绿植（离群色）；迁移向暖木色 ref：
    # 地板像素应明显变化，绿植像素应几乎不动（相似度掩膜豁免），且不得被外推成极端色
    src = _solid(400, 300, (210, 190, 160))          # 米色地板
    plant = Image.new('RGB', (60, 60), (40, 140, 50))  # 绿植
    src.paste(plant, (300, 200))
    ref = _solid(80, 80, (170, 130, 90))             # 暖木色小样
    out = match_color_region(src, ref, (0.0, 0.0, 1.0, 1.0), strength=1.0, feather=0.0)
    a_src = np.asarray(src, dtype=np.int16)
    a_out = np.asarray(out, dtype=np.int16)
    floor_delta = np.abs(a_out[50, 50] - a_src[50, 50]).max()
    plant_delta = np.abs(a_out[230, 330] - a_src[230, 330]).max()
    assert floor_delta > 15, f'地板未被校色 delta={floor_delta}'
    assert plant_delta < 10, f'离群绿植被误染 delta={plant_delta}'


def test_src_not_mutated():
    src = _solid(200, 150, (120, 90, 60))
    before = np.asarray(src).copy()
    ref = _solid(50, 50, (30, 200, 30))
    match_color_region(src, ref, (0.1, 0.1, 0.8, 0.8), strength=1.0)
    assert np.array_equal(np.asarray(src), before)


# ── 高级微调 ─────────────────────────────────────────────────────
def _mean_lab(img):
    return np.asarray(img.convert('LAB'), dtype=np.float32).reshape(-1, 3).mean(axis=0)


def _mean_luma(img):
    rgb = np.asarray(img.convert('RGB'), dtype=np.float32) / 255.0
    return float((rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722).mean())


def test_adjustment_defaults_are_pixel_identical():
    src = _split_lr(120, 80, (40, 90, 150), (210, 170, 80))
    out = apply_color_adjustments(src, {})
    assert np.array_equal(np.asarray(out), np.asarray(src))


def test_temperature_and_tint_follow_lab_directions():
    src = _solid(40, 40, (128, 128, 128))
    base = _mean_lab(src)
    warm = _mean_lab(apply_color_adjustments(src, {'temperature': 50}))
    magenta = _mean_lab(apply_color_adjustments(src, {'tint': 50}))
    assert warm[2] > base[2] + 8
    assert magenta[1] > base[1] + 8


def test_exposure_and_saturation_controls():
    src = _solid(40, 40, (80, 120, 160))
    brighter = apply_color_adjustments(src, {'exposure': 1})
    gray = np.asarray(apply_color_adjustments(src, {'saturation': -100}), dtype=np.int16)
    assert _mean_luma(brighter) > _mean_luma(src) * 1.25
    assert np.abs(gray[..., 0] - gray[..., 1]).max() <= 1
    assert np.abs(gray[..., 1] - gray[..., 2]).max() <= 1


def test_tonal_controls_target_expected_bands():
    src = Image.new('RGB', (3, 1))
    src.putdata([(32, 32, 32), (128, 128, 128), (220, 220, 220)])
    whites = np.asarray(apply_color_adjustments(src, {'whites': 60}), dtype=np.int16)[0, :, 0]
    shadows = np.asarray(apply_color_adjustments(src, {'shadows': 60}), dtype=np.int16)[0, :, 0]
    midtones = np.asarray(apply_color_adjustments(src, {'midtones': 60}), dtype=np.int16)[0, :, 0]
    base = np.array([32, 128, 220])
    assert (whites - base)[2] > (whites - base)[0]
    assert (shadows - base)[0] > (shadows - base)[2]
    assert (midtones - base)[1] > max((midtones - base)[0], (midtones - base)[2])


def test_advanced_adjustment_respects_region_and_master_strength():
    src = _solid(200, 120, (90, 100, 110))
    ref = _solid(40, 40, (140, 120, 90))
    rect = (0.5, 0.0, 0.5, 1.0)
    adjustments = {'exposure': 0.8, 'temperature': 30, 'saturation': 25}
    full = match_color_region(src, ref, rect, strength=1.0, feather=0,
                              adjustments=adjustments)
    half = match_color_region(src, ref, rect, strength=0.5, feather=0,
                              adjustments=adjustments)
    a_src = np.asarray(src, dtype=np.int16)
    a_full = np.asarray(full, dtype=np.int16)
    a_half = np.asarray(half, dtype=np.int16)
    assert np.array_equal(a_full[:, :100], a_src[:, :100])
    expected = np.rint((a_src[:, 150] + a_full[:, 150]) / 2).astype(np.int16)
    assert np.abs(a_half[:, 150] - expected).max() <= 1


def test_global_manual_adjustments_change_pixels_outside_analysis_rect():
    src = _split_lr(240, 120, (55, 75, 110), (175, 135, 80))
    ref = _solid(60, 40, (145, 110, 75))
    out = match_color_global(
        src, ref, (0.5, 0, 0.5, 1), adjustment_mode='manual',
        adjustments={'temperature': 30, 'saturation': 20})
    base = np.asarray(src, dtype=np.int16)
    changed = np.asarray(out, dtype=np.int16)
    assert np.abs(changed[:, :100] - base[:, :100]).max() > 5
    assert np.abs(changed[:, 140:] - base[:, 140:]).max() > 5


def test_global_manual_result_is_independent_of_analysis_rect_and_strength():
    src = _split_lr(180, 100, (70, 90, 120), (170, 130, 75))
    ref = _solid(40, 40, (130, 100, 70))
    adjustments = {'exposure': 0.4, 'tint': -18, 'contrast': 12}
    left_rect = match_color_global(
        src, ref, (0, 0, 0.5, 1), strength=0.1,
        adjustment_mode='manual', adjustments=adjustments)
    right_rect = match_color_global(
        src, ref, (0.5, 0, 0.5, 1), strength=1,
        adjustment_mode='manual', adjustments=adjustments)
    assert np.array_equal(np.asarray(left_rect), np.asarray(right_rect))


def test_global_auto_changes_full_image_and_strength_is_global_blend():
    src = _split_lr(240, 120, (45, 65, 115), (170, 120, 70))
    ref = _solid(60, 50, (115, 145, 95))
    rect = (0.5, 0, 0.5, 1)
    full = match_color_global(src, ref, rect, strength=1, adjustment_mode='auto')
    half = match_color_global(src, ref, rect, strength=0.5, adjustment_mode='auto')
    zero = match_color_global(src, ref, rect, strength=0, adjustment_mode='auto')
    manual_zero = match_color_global(src, ref, rect, adjustment_mode='manual', adjustments={})
    base = np.asarray(src, dtype=np.int16)
    full_arr = np.asarray(full, dtype=np.int16)
    half_arr = np.asarray(half, dtype=np.int16)
    assert np.abs(full_arr[:, :100] - base[:, :100]).max() > 10
    assert np.abs(full_arr[:, 140:] - base[:, 140:]).max() > 10
    expected = np.asarray(Image.blend(src.convert('RGB'), full, 0.5), dtype=np.int16)
    assert np.abs(half_arr - expected).max() <= 1
    assert np.array_equal(np.asarray(zero), np.asarray(src))
    assert np.array_equal(np.asarray(manual_zero), np.asarray(src))


def test_masked_auto_changes_floor_only_and_preserves_scene_luminance():
    src = _split_lr(240, 120, (80, 105, 150), (175, 135, 85))
    ref = _solid(80, 60, (120, 155, 90))
    mask = Image.new('L', src.size, 0)
    mask.paste(255, (120, 0, 240, 120))
    out = match_color_masked(src, ref, mask, strength=1, mask_feather=0)
    base = np.asarray(src)
    changed = np.asarray(out)
    assert np.array_equal(changed[:, :120], base[:, :120])
    assert np.abs(changed[:, 140:].astype(np.int16) - base[:, 140:].astype(np.int16)).max() > 5
    before_l = np.asarray(src.convert('LAB'), dtype=np.int16)[:, 140:, 0]
    after_l = np.asarray(out.convert('LAB'), dtype=np.int16)[:, 140:, 0]
    assert np.abs(after_l - before_l).mean() < 2.0


def test_masked_distribution_returns_quality_and_never_changes_outside_mask():
    src = _split_lr(240, 160, (70, 105, 155), (175, 135, 85))
    ref = _solid(90, 70, (125, 150, 90))
    mask = Image.new('L', src.size, 0)
    mask.paste(255, (0, 80, 240, 160))
    out, report = match_color_masked(
        src, ref, mask, strength=1, mask_feather=0,
        algorithm='distribution', return_quality_report=True)
    assert np.array_equal(np.asarray(out)[:80], np.asarray(src)[:80])
    assert report is not None
    assert 0 <= report.score <= 100
    assert report.diagnostic_overlay is not None


def test_masked_manual_never_touches_pixels_outside_mask():
    src = _solid(160, 100, (90, 110, 130))
    ref = _solid(40, 40, (130, 95, 65))
    mask = Image.new('L', src.size, 0)
    mask.paste(255, (30, 20, 130, 80))
    out = match_color_masked(
        src, ref, mask, adjustment_mode='manual', mask_feather=0,
        adjustments={'temperature': 40, 'saturation': 25})
    base = np.asarray(src)
    changed = np.asarray(out)
    outside = np.ones((100, 160), dtype=bool)
    outside[20:80, 30:130] = False
    assert np.array_equal(changed[outside], base[outside])
    assert np.abs(changed[40, 60].astype(np.int16) - base[40, 60].astype(np.int16)).max() > 5


def test_segmentation_manual_fallback_uses_positive_strokes(monkeypatch):
    from Floor_engine_server import floor_segmentation
    monkeypatch.setattr(floor_segmentation._RUNTIME, '_error', 'test model unavailable')
    monkeypatch.setattr(floor_segmentation._RUNTIME, '_encoder', None)
    monkeypatch.setattr(floor_segmentation._RUNTIME, '_decoder', None)
    positive = np.zeros((80, 120), dtype=bool)
    positive[45:75, 15:105] = True
    working, result = segment_floor(
        _solid(120, 80, (170, 135, 90)), 'test',
        positive_b64=encode_mask_png(positive), auto_seed=False)
    assert working.size == (120, 80)
    assert result.mask is not None
    assert result.mask[55, 50]
    assert not result.mask[10, 10]


def test_striped_global_adjustments_match_single_pass():
    src = Image.new('RGB', (97, 53))
    pixels = np.arange(97 * 53 * 3, dtype=np.uint32).reshape(53, 97, 3)
    src = Image.fromarray((pixels % 256).astype(np.uint8), mode='RGB')
    adjustments = {
        'temperature': 22, 'tint': -11, 'exposure': 0.3, 'contrast': 17,
        'highlights': -8, 'shadows': 13, 'whites': 6, 'blacks': -5,
        'midtones': 9, 'saturation': 18,
    }
    expected = apply_color_adjustments(src, adjustments)
    striped = apply_color_adjustments_striped(src, adjustments, strip_rows=7)
    assert np.array_equal(np.asarray(striped), np.asarray(expected))


def test_adjustment_request_defaults_and_bounds():
    req = server_schemas.ColorMatchPreviewRequest(
        image_rel='result.jpg', ref_path='ref.jpg', rect=_rect())
    assert not any(req.adjustments.model_dump().values())
    with pytest.raises(ValueError):
        server_schemas.ColorMatchAdjustments(exposure=2.1)
    with pytest.raises(ValueError):
        server_schemas.ColorMatchAdjustments(temperature=-101)


def test_illumination_request_automatically_enables_distribution_algorithm():
    preview = server_schemas.ColorMatchPreviewRequest(
        image_rel='result.jpg', ref_path='reference.jpg', rect=_rect(),
        illumination_mode='chroma')
    record = server_schemas.RecordColorMatchRequest(
        json_path='record.json', record_id='r1', result_id='x', rect=_rect(),
        illumination_mode='full')
    assert preview.algorithm == 'distribution'
    assert record.algorithm == 'distribution'


def test_auto_profile_is_relative_to_source_and_bounded():
    src = _solid(120, 80, (80, 100, 150))
    ref = _solid(40, 40, (180, 140, 70))
    _, profile = match_color_region(
        src, ref, (0, 0, 1, 1), adjustment_mode='auto',
        return_auto_adjustments=True)
    assert profile['temperature'] > 0
    assert profile['exposure'] > 0
    assert all(-100 <= profile[key] <= 100 for key in (
        'temperature', 'tint', 'contrast', 'highlights', 'shadows',
        'whites', 'blacks', 'midtones', 'saturation'))
    assert -2 <= profile['exposure'] <= 2


def test_color_analysis_extracts_three_ordered_source_patches():
    src = Image.new('RGB', (360, 240))
    src.paste(_solid(360, 80, (215, 175, 125)), (0, 0))
    src.paste(_solid(360, 80, (150, 110, 72)), (0, 80))
    src.paste(_solid(360, 80, (78, 54, 36)), (0, 160))
    ref = _solid(120, 80, (150, 110, 72))

    analysis = analyze_color_region(src, ref, (0, 0, 1, 1))

    assert analysis['status'] == 'ok'
    assert [zone['zone'] for zone in analysis['zones']] == ['highlight', 'penumbra', 'shadow']
    assert all(zone['image'] is not None for zone in analysis['zones'])
    luminance = [zone['luminance'] for zone in analysis['zones']]
    assert luminance[0] > luminance[1] > luminance[2]
    assert all(zone['image'].width / zone['image'].height == pytest.approx(4 / 3, rel=0.08)
               for zone in analysis['zones'])


def test_color_analysis_warm_and_gray_advice_has_correct_signs():
    ref = _solid(160, 100, (145, 112, 82))
    warm = apply_color_adjustments(ref, {'temperature': 35})
    gray = apply_color_adjustments(ref, {'saturation': -45})

    warm_analysis = analyze_color_region(warm, ref, (0, 0, 1, 1))
    gray_analysis = analyze_color_region(gray, ref, (0, 0, 1, 1))
    warm_codes = {hint['code'] for hint in warm_analysis['zones'][1]['hints']}
    gray_codes = {hint['code'] for hint in gray_analysis['zones'][1]['hints']}

    assert warm_analysis['status'] == gray_analysis['status'] == 'low_dynamic_range'
    assert 'warm' in warm_codes and 'gray' in gray_codes
    assert warm_analysis['recommended_adjustments']['temperature'] < 0
    assert gray_analysis['recommended_adjustments']['saturation'] > 0
    assert warm_analysis['recommended_adjustments']['exposure'] == 0


def test_color_analysis_uniform_light_does_not_invent_light_zones():
    src = _solid(240, 160, (130, 105, 80))
    analysis = analyze_color_region(src, src, (0, 0, 1, 1))

    assert analysis['status'] == 'low_dynamic_range'
    zones = {zone['zone']: zone for zone in analysis['zones']}
    assert zones['highlight']['image'] is None
    assert zones['penumbra']['image'] is not None
    assert zones['shadow']['image'] is None
    assert not any(analysis['recommended_adjustments'].values())


def test_manual_mode_uses_gemini_source_as_zero_point():
    src = _solid(160, 100, (70, 90, 120))
    ref = _solid(40, 40, (210, 160, 60))
    rect = (0, 0, 1, 1)
    original = match_color_region(
        src, ref, rect, strength=0.24, adjustment_mode='manual',
        adjustments={})
    brighter = match_color_region(
        src, ref, rect, strength=0.24, adjustment_mode='manual',
        adjustments={'exposure': 1})
    assert np.array_equal(np.asarray(original), np.asarray(src))
    assert _mean_luma(brighter) > _mean_luma(src)


def test_auto_mode_ignores_manual_values_and_preserves_legacy_auto():
    src = _solid(160, 100, (70, 90, 120))
    ref = _solid(40, 40, (170, 130, 80))
    rect = (0, 0, 1, 1)
    legacy = match_color_region(src, ref, rect, strength=0.4)
    auto = match_color_region(
        src, ref, rect, strength=0.4, adjustment_mode='auto',
        adjustments={'exposure': 2, 'temperature': -100})
    assert np.array_equal(np.asarray(auto), np.asarray(legacy))


def test_adjustment_mode_validation():
    with pytest.raises(ValueError):
        server_schemas.ColorMatchPreviewRequest(
            image_rel='result.jpg', ref_path='ref.jpg', rect=_rect(),
            adjustment_mode='invalid')


# ── 端点硬化 ─────────────────────────────────────────────────────
@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    out_dir = tmp_path / "output_files"
    up_dir = tmp_path / "_ng_uploads"
    out_dir.mkdir(exist_ok=True)
    up_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(out_dir))
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(out_dir))
    monkeypatch.setattr(server_helpers, "UPLOAD_DIR", str(up_dir))
    return out_dir, up_dir


def _rect():
    return server_schemas.ColorMatchRect(x=0.1, y=0.1, w=0.8, h=0.8)


def test_output_rel_rejects_escape(dirs, tmp_path):
    (tmp_path / "evil.jpg").write_bytes(b"x")
    with pytest.raises(HTTPException) as ei:
        server_helpers.require_output_image_rel("../evil.jpg")
    assert ei.value.status_code == 400


def test_ref_path_outside_allowed_dirs_rejected(dirs, tmp_path):
    evil = tmp_path / "evil.jpg"
    _solid(8, 8, (1, 2, 3)).save(evil)
    with pytest.raises(HTTPException) as ei:
        server_helpers.require_ref_image_path(str(evil))
    assert ei.value.status_code == 400


def test_ref_path_inside_upload_dir_ok(dirs):
    _, up_dir = dirs
    ref = up_dir / "swatch.jpg"
    _solid(8, 8, (1, 2, 3)).save(ref)
    assert server_helpers.require_ref_image_path(str(ref)) == os.path.realpath(str(ref))


def test_preview_returns_original_based_auto_profile(dirs):
    out_dir, up_dir = dirs
    src = out_dir / "result.jpg"
    ref = up_dir / "swatch.jpg"
    _solid(80, 60, (70, 90, 140)).save(src)
    _solid(30, 30, (180, 140, 70)).save(ref)
    req = server_schemas.ColorMatchPreviewRequest(
        image_rel="result.jpg", ref_path=str(ref), rect=_rect(),
        adjustment_mode="auto")
    result = routes_tools.color_match_preview(req)
    assert result['preview'].startswith('data:image/jpeg;base64,')
    assert result['auto_adjustments']['temperature'] > 0
    assert set(result['auto_adjustments']) == {
        'temperature', 'tint', 'exposure', 'contrast', 'highlights',
        'shadows', 'whites', 'blacks', 'midtones', 'saturation'}


def test_local_preview_requires_mask_and_returns_lossless_png(dirs):
    out_dir, up_dir = dirs
    src = out_dir / 'result.jpg'
    ref = up_dir / 'swatch.jpg'
    _solid(80, 60, (90, 105, 135)).save(src)
    _solid(30, 30, (155, 125, 80)).save(ref)
    common = dict(image_rel='result.jpg', ref_path=str(ref), rect=_rect(),
                  adjustment_mode='auto', scope='floor_mask')
    with pytest.raises(HTTPException) as error:
        routes_tools.color_match_preview(server_schemas.ColorMatchPreviewRequest(**common))
    assert error.value.status_code == 422
    mask = np.zeros((60, 80), dtype=bool)
    mask[30:, :] = True
    result = routes_tools.color_match_preview(server_schemas.ColorMatchPreviewRequest(
        **common, mask_b64=encode_mask_png(mask)))
    assert result['preview'].startswith('data:image/png;base64,')


def test_preview_optionally_returns_serialized_zone_analysis(dirs):
    out_dir, up_dir = dirs
    src = out_dir / "result.jpg"
    ref = up_dir / "swatch.jpg"
    image = Image.new('RGB', (300, 240))
    image.paste(_solid(300, 80, (210, 170, 120)), (0, 0))
    image.paste(_solid(300, 80, (145, 108, 72)), (0, 80))
    image.paste(_solid(300, 80, (75, 52, 34)), (0, 160))
    image.save(src)
    _solid(80, 60, (145, 108, 72)).save(ref)

    with_analysis = routes_tools.color_match_preview(server_schemas.ColorMatchPreviewRequest(
        image_rel="result.jpg", ref_path=str(ref), rect=server_schemas.ColorMatchRect(x=0, y=0, w=1, h=1),
        adjustment_mode="manual", include_analysis=True))
    without_analysis = routes_tools.color_match_preview(server_schemas.ColorMatchPreviewRequest(
        image_rel="result.jpg", ref_path=str(ref), rect=server_schemas.ColorMatchRect(x=0, y=0, w=1, h=1),
        adjustment_mode="manual"))

    assert with_analysis['analysis']['status'] == 'ok'
    assert all(zone['preview'].startswith('data:image/jpeg;base64,')
               for zone in with_analysis['analysis']['zones'])
    assert with_analysis['quality_report']['diagnostic_overlay'].startswith(
        'data:image/png;base64,')
    assert 0 <= with_analysis['quality_report']['score'] <= 100
    assert 'analysis' not in without_analysis


def test_preview_manual_adjustments_are_applied_outside_analysis_rect(dirs):
    out_dir, up_dir = dirs
    src = out_dir / "result.jpg"
    ref = up_dir / "swatch.jpg"
    source = _split_lr(240, 120, (55, 75, 110), (175, 135, 80))
    source.save(src, quality=95)
    _solid(40, 40, (145, 110, 75)).save(ref)

    result = routes_tools.color_match_preview(server_schemas.ColorMatchPreviewRequest(
        image_rel="result.jpg", ref_path=str(ref),
        rect=server_schemas.ColorMatchRect(x=0.5, y=0, w=0.5, h=1),
        adjustment_mode="manual",
        adjustments=server_schemas.ColorMatchAdjustments(temperature=35, saturation=20)))
    encoded = result['preview'].split(',', 1)[1]
    preview = Image.open(io.BytesIO(base64.b64decode(encoded))).convert('RGB')
    saved_source = Image.open(src).convert('RGB')
    before = np.asarray(saved_source, dtype=np.int16)
    after = np.asarray(preview, dtype=np.int16)

    assert np.abs(after[:, :90] - before[:, :90]).mean() > 3


def test_job_color_match_rejects_foreign_candidate(dirs, monkeypatch):
    out_dir, up_dir = dirs
    # outputs 内的真实图，但不属于该 job 的任何候选
    foreign = out_dir / "foreign.jpg"
    _solid(16, 16, (100, 100, 100)).save(foreign)
    ref = up_dir / "swatch.jpg"
    _solid(8, 8, (1, 2, 3)).save(ref)

    job = new_job("oak", "now")
    job.status = "done"
    jobs = TaskRegistry("jobs", max_entries=60,
                        is_terminal=server_state.job_is_terminal, newest_first=True)
    jobs.add(job.job_id, job)
    monkeypatch.setattr(server_state, "JOBS", jobs)
    req = server_schemas.JobColorMatchRequest(
        image_rel="foreign.jpg", ref_path=str(ref), rect=_rect(), stage="pro")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes_tools.job_color_match(job.job_id, req))
    assert ei.value.status_code == 400


def test_record_color_match_b64_only_404(dirs):
    out_dir, _ = dirs
    jp = out_dir / "oak_记录.json"
    jp.write_text(json.dumps([{
        "id": "r1",
        "results": [{"result_id": "res_1", "result_image_b64": "abc"}],
    }]), encoding="utf-8")
    req = server_schemas.RecordColorMatchRequest(
        json_path=str(jp), record_id="r1", result_id="res_1", rect=_rect())
    with pytest.raises(HTTPException) as ei:
        routes_tools.record_color_match(req)
    assert ei.value.status_code == 404


def test_record_color_match_falls_back_to_material_optimized_image(dirs):
    out_dir, _ = dirs
    material_dir = out_dir / "oak"
    material_dir.mkdir()
    jp = material_dir / "oak_记录.json"
    src = out_dir / "result.jpg"
    ref = material_dir / "oak_优化图.png"
    _solid(80, 60, (70, 90, 140)).save(src)
    _solid(30, 30, (180, 140, 70)).save(ref)
    jp.write_text(json.dumps([{
        "id": "r1",
        "results": [{"result_id": "res_1", "result_image_file": "result.jpg"}],
    }]), encoding="utf-8")

    result = routes_tools.record_color_match(server_schemas.RecordColorMatchRequest(
        json_path=str(jp), record_id="r1", result_id="res_1", rect=_rect()))

    saved = records.load_records_file(str(jp))[0]["results"]
    assert result["ok"] is True
    assert len(saved) == 2
    assert saved[-1]["model_label"] == "手动校色 Edit"
