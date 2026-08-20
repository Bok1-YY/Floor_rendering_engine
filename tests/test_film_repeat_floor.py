# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import os
import pytest
from PIL import Image, ImageDraw

from Floor_engine_server.film_repeat_floor import (
    PHYSICAL_LAYOUT_VERSION,
    _seam_gain,
    analyze_film_path,
    analyze_film_repeat,
    parse_plank_dimensions,
    render_film_floor_preview,
    sample_film_floor,
)
from Floor_engine_server import records
from Floor_engine_server import routes_library
from Floor_engine_server.server_schemas import FilmAnalyzeRequest


def _film_with_label(width=336, height=468):
    y, x = np.mgrid[0:height, 0:width]
    # Periodic on the long axis and deliberately non-periodic across width.
    grain = 128 + 28 * np.sin(x / 8.0) + 14 * np.sin(2 * np.pi * y / height)
    rgb = np.stack((grain + 24, grain + 8, grain - 12), axis=-1)
    image = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((width - 58, height - 38, width - 1, height - 1), fill="white")
    return image


def _manifest(image, *, floor_size="长窄板直拼：1900 x 136 mm",
              plank_width=136, plank_length=1900):
    return analyze_film_repeat(
        image,
        film_width_mm=984,
        repeat_length_mm=1890,
        plank_width_mm=plank_width,
        plank_length_mm=plank_length,
        seam_type="无缝拼接 (SPC/LVT专用)",
        floor_size=floor_size,
    )


def test_parses_current_straight_and_herringbone_sizes():
    assert parse_plank_dimensions("长窄板直拼：1900 x 136 mm") == (136.0, 1900.0)
    assert parse_plank_dimensions("常规人字拼：125 x 625 mm") == (125.0, 625.0)


def test_analyzes_physical_slitting_phase_and_label_avoidance():
    manifest = _manifest(_film_with_label())

    assert manifest["status"] == "ready"
    assert manifest["slitting"]["lane_count"] == 7
    assert manifest["slitting"]["left_margin_mm"] == 16.0
    assert manifest["slitting"]["right_margin_mm"] == 16.0
    assert manifest["phase_state_count"] == 189
    assert manifest["effective_board_states"] == 1323
    assert manifest["phase_advance_mm"] == 10.0
    assert manifest["exclusion_rects"]
    assert manifest["exclusion_rects"][0]["kind"] == "printed_label"


def test_straight_film_sampler_never_emits_label_pixels():
    film = _film_with_label()
    manifest = _manifest(film)
    axis = np.linspace(-6.0, 6.0, 420, dtype=np.float32)
    world_x, world_z = np.meshgrid(axis, axis)

    pixels, metadata = sample_film_floor(film, world_x, world_z, manifest)

    assert pixels.shape == (420, 420, 3)
    # The source label is pure white; every candidate board intersecting its
    # footprint must be skipped instead of sampled or inpainted.
    assert not np.any(np.all(pixels >= 250, axis=2))
    assert metadata["lane_count"] == 7
    assert metadata["physical_roll_repeat"] is True
    assert metadata["source_pixel_periodic"] is False


def test_herringbone_sampler_uses_real_90_degree_board_field():
    film = _film_with_label()
    manifest = _manifest(
        film, floor_size="常规人字拼：125 x 625 mm",
        plank_width=125, plank_length=625)
    axis = np.linspace(-3.0, 3.0, 256, dtype=np.float32)
    world_x, world_z = np.meshgrid(axis, axis)

    pixels, metadata = sample_film_floor(
        film, world_x, world_z, manifest,
        laying="常规人字拼：125 x 625 mm")

    assert pixels.shape == (256, 256, 3)
    assert metadata["laying"] == "herringbone"
    assert manifest["slitting"]["lane_count"] == 7
    assert manifest["phase_state_count"] == 378


def test_repeat_film_preview_is_deterministic():
    film = _film_with_label()
    manifest = _manifest(film)
    first = np.asarray(render_film_floor_preview(film, manifest, size=192, extent_m=8))
    second = np.asarray(render_film_floor_preview(film, manifest, size=192, extent_m=8))
    assert np.array_equal(first, second)


def test_screen_space_antialiasing_keeps_real_plank_joints_visible():
    # At 5 mm/pixel a 0.65 mm SPC joint cannot be represented by a binary
    # threshold.  Its pixel-integrated coverage must still produce a visible
    # boundary, and an end joint must be stronger than a side joint.
    local_l = np.array([[950.0, 950.0, 0.0]], dtype=np.float32)
    local_w = np.array([[68.0, 0.0, 68.0]], dtype=np.float32)
    gains = _seam_gain(
        local_l, local_w, 1900.0, 136.0, "无缝拼接 (SPC/LVT专用)",
        footprint_mm=np.array([5.0], dtype=np.float32),
    )[0]
    assert gains[1] < gains[0] - 0.01
    assert gains[2] < gains[1]


def test_physical_sampler_reports_v3_material_contract():
    film = _film_with_label()
    manifest = _manifest(film)
    grid = np.zeros((2, 3), dtype=np.float32)
    _, metadata = sample_film_floor(
        film, grid, grid, manifest, footprint_mm=np.array([4.0, 8.0], dtype=np.float32))
    assert metadata["model"] == PHYSICAL_LAYOUT_VERSION == "physical-floor-layout-v4"
    assert metadata["screen_space_antialiased_joints"] is True
    assert metadata["source_detail_enhancement"] == "physical_mipmap_prefilter"


def test_invalid_slit_origin_is_rejected():
    with pytest.raises(ValueError, match="分切起点"):
        analyze_film_repeat(
            _film_with_label(), film_width_mm=984, repeat_length_mm=1890,
            plank_width_mm=136, plank_length_mm=1900, slit_origin_mm=100)


@pytest.mark.skipif(
    not os.path.isfile(r"C:\Users\Boki\Desktop\VL88238XL(EIR)-006 Full Layout.jpg"),
    reason="operator film regression file is not present",
)
def test_real_vl88238xl_film_contract_regression():
    _, manifest = analyze_film_path(
        r"C:\Users\Boki\Desktop\VL88238XL(EIR)-006 Full Layout.jpg",
        {
            "film_width_mm": 984,
            "film_repeat_length_mm": 1890,
            "floor_size": "长窄板直拼：1900 x 136 mm",
            "seam_type": "无缝拼接 (SPC/LVT专用)",
        },
    )
    assert manifest["image_size"] == [3360, 4680]
    assert manifest["status"] == "ready"
    assert manifest["repeat_registration"]["translation_px_x"] == 2
    assert manifest["slitting"]["lane_count"] == 7
    assert manifest["effective_board_states"] == 1323
    assert manifest["exclusion_rects"][0]["kind"] == "printed_label"


def test_vr_png_saver_bounds_recursive_parent_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(tmp_path))
    very_long_parent = "_".join(["Nano_Banana_Pro_API原图_自动校色_VR360"] * 12) + ".png"
    path = records.save_api_result_png(
        Image.new("RGB", (32, 16), "tan"),
        "VR360_球面地板校正",
        str(tmp_path / very_long_parent),
        {"projection": "equirectangular"},
    )
    assert path
    assert os.path.isfile(path)
    assert len(os.path.basename(path)) < 150


def test_free_film_analysis_endpoint_returns_physical_contract(tmp_path, monkeypatch):
    path = tmp_path / "film.png"
    _film_with_label().save(path)
    monkeypatch.setattr(
        routes_library, "require_upload_image_path",
        lambda value, _label, required=False: str(value))
    response = routes_library.analyze_repeat_film(FilmAnalyzeRequest(
        film_path=str(path), film_width_mm=984, film_repeat_length_mm=1890,
        floor_size="长窄板直拼：1900 x 136 mm",
        seam_type="无缝拼接 (SPC/LVT专用)",
    ))
    assert response["manifest"]["status"] == "ready"
    assert response["manifest"]["slitting"]["lane_count"] == 7
    assert response["guide"].startswith("data:image/png;base64,")
