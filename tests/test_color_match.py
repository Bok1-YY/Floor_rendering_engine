# 手动区域校色：match_color_region 单测（选区内外行为、羽化连续性、强度恒等、输入健壮性）
# + 校色端点硬化（路径逃逸、参照目录白名单、候选归属、b64-only 记录）。
import asyncio
import json
import os

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import records, server_api
from Floor_engine_server.api import match_color_region
from Floor_engine_server.models import new_job


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


# ── 端点硬化 ─────────────────────────────────────────────────────
@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    out_dir = tmp_path / "output_files"
    up_dir = tmp_path / "_ng_uploads"
    out_dir.mkdir(exist_ok=True)
    up_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(out_dir))
    monkeypatch.setattr(server_api, "MAIN_OUTPUT_DIR", str(out_dir))
    monkeypatch.setattr(server_api, "UPLOAD_DIR", str(up_dir))
    return out_dir, up_dir


def _rect():
    return server_api.ColorMatchRect(x=0.1, y=0.1, w=0.8, h=0.8)


def test_output_rel_rejects_escape(dirs, tmp_path):
    (tmp_path / "evil.jpg").write_bytes(b"x")
    with pytest.raises(HTTPException) as ei:
        server_api._require_output_image_rel("../evil.jpg")
    assert ei.value.status_code == 400


def test_ref_path_outside_allowed_dirs_rejected(dirs, tmp_path):
    evil = tmp_path / "evil.jpg"
    _solid(8, 8, (1, 2, 3)).save(evil)
    with pytest.raises(HTTPException) as ei:
        server_api._require_ref_image_path(str(evil))
    assert ei.value.status_code == 400


def test_ref_path_inside_upload_dir_ok(dirs):
    _, up_dir = dirs
    ref = up_dir / "swatch.jpg"
    _solid(8, 8, (1, 2, 3)).save(ref)
    assert server_api._require_ref_image_path(str(ref)) == os.path.realpath(str(ref))


def test_job_color_match_rejects_foreign_candidate(dirs, monkeypatch):
    out_dir, up_dir = dirs
    # outputs 内的真实图，但不属于该 job 的任何候选
    foreign = out_dir / "foreign.jpg"
    _solid(16, 16, (100, 100, 100)).save(foreign)
    ref = up_dir / "swatch.jpg"
    _solid(8, 8, (1, 2, 3)).save(ref)

    job = new_job("oak", "now")
    job.status = "done"
    monkeypatch.setattr(server_api, "_job_history", [job])
    req = server_api.JobColorMatchRequest(
        image_rel="foreign.jpg", ref_path=str(ref), rect=_rect(), stage="pro")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server_api.job_color_match(job.job_id, req))
    assert ei.value.status_code == 400


def test_record_color_match_b64_only_404(dirs):
    out_dir, _ = dirs
    jp = out_dir / "oak_记录.json"
    jp.write_text(json.dumps([{
        "id": "r1",
        "results": [{"result_id": "res_1", "result_image_b64": "abc"}],
    }]), encoding="utf-8")
    req = server_api.RecordColorMatchRequest(
        json_path=str(jp), record_id="r1", result_id="res_1", rect=_rect())
    with pytest.raises(HTTPException) as ei:
        server_api.record_color_match(req)
    assert ei.value.status_code == 404
