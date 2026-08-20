import copy
import os

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from Floor_engine_server.local_depth import MODEL_PATH, depth_model_status, predict_relative_depth
from Floor_engine_server.panorama_local_geometry import (
    LOCAL_GEOMETRY_VERSION,
    analyze_panorama_architecture,
    analyze_perspective_geometry,
    refine_erp_floor_mask,
    register_source_to_erp,
    validate_geometry_contract,
)
from Floor_engine_server.whole_home_pano_gate import erp_to_perspective


def _architectural_image(width=960, height=540):
    image = Image.new("RGB", (width, height), (224, 220, 212))
    draw = ImageDraw.Draw(image)
    for x in (80, 250, 480, 710, 880):
        draw.line((x, 25, x, height - 20), fill=(30, 30, 30), width=5)
    for y in (90, 210, 360, 480):
        draw.line((20, y, width - 20, y), fill=(45, 45, 45), width=4)
    draw.rectangle((300, 180, 660, 430), outline=(10, 10, 10), width=8)
    return image


def test_local_geometry_contract_is_signed_and_uses_lens_fov():
    contract = analyze_perspective_geometry(
        _architectural_image(), {"angle": "50mm lens (Standard)"})

    assert contract["version"] == LOCAL_GEOMETRY_VERSION
    assert contract["status"] == "ready"
    assert 38.0 <= contract["camera"]["horizontal_fov_deg"] <= 41.0
    assert abs(contract["camera"]["roll_deg"]) <= 1.0
    validate_geometry_contract(contract)

    tampered = copy.deepcopy(contract)
    tampered["camera"]["camera_height_m"] = 2.2
    with pytest.raises(ValueError, match="tampered"):
        validate_geometry_contract(tampered)


def test_source_registration_finds_exact_front_erp_view():
    rng = np.random.default_rng(17)
    erp = np.zeros((512, 1024, 3), dtype=np.uint8)
    erp[:] = (170, 166, 158)
    for _ in range(160):
        x = int(rng.integers(260, 764))
        y = int(rng.integers(90, 420))
        colour = tuple(int(value) for value in rng.integers(20, 235, size=3))
        cv2.circle(erp, (x, y), int(rng.integers(3, 12)), colour, -1)
    for index in range(24):
        x0 = 275 + (index % 8) * 58
        y0 = 110 + (index // 8) * 115
        cv2.rectangle(erp, (x0, y0), (x0 + 31, y0 + 47),
                      (20 + index * 7, 230 - index * 5, 55 + index * 3), 3)
        cv2.line(erp, (x0, y0), (x0 + 31, y0 + 47), (15, 15, 15), 2)
    erp_image = Image.fromarray(erp, "RGB")
    source = erp_to_perspective(erp_image, 0, 0, 84, 512, 288)
    contract = analyze_perspective_geometry(source, {"angle": "24mm lens"})

    registration = register_source_to_erp(source, erp_image, contract)

    assert registration["status"] == "ready"
    assert registration["inliers"] >= 12
    assert abs(registration["yaw_deg"]) <= 12
    assert registration["spread"] >= .12


def test_floor_mask_refinement_never_invents_fixed_nadir_band(monkeypatch):
    scene = Image.new("RGB", (512, 256), (180, 170, 155))
    raw = np.zeros((256, 512), dtype=np.uint8)
    raw[150:220, 30:482] = 255

    monkeypatch.setattr(
        "Floor_engine_server.panorama_local_geometry.depth_model_status",
        lambda: {"model": "test", "available": False, "error": "disabled"})
    output, metadata = refine_erp_floor_mask(scene, Image.fromarray(raw, "L"), [])
    result = np.asarray(output)

    assert metadata["fixed_nadir_fill"] is False
    assert not np.any(result[:128])
    assert not np.any(result[-20:])
    assert np.any(result[160:210])
    assert metadata["status"] == "needs_calibration"
    assert metadata["residual_floor_like_fraction"] > metadata["residual_floor_like_threshold"]


def test_architecture_gate_checks_rectilinear_views_not_raw_erp_curves():
    # Uniform ERP with repeated straight meridians is benign after rectilinear extraction.
    arr = np.full((512, 1024, 3), 210, dtype=np.uint8)
    for x in range(0, 1024, 64):
        arr[:, x:x + 3] = 35
    report = analyze_panorama_architecture(Image.fromarray(arr, "RGB"))
    assert report["projection_rule"] == "rectilinear_views_only_raw_erp_curvature_is_normal"
    assert report["status"] in {"passed", "rectify_recommended"}


@pytest.mark.skipif(not os.path.isfile(MODEL_PATH), reason="bundled depth model missing")
def test_bundled_depth_model_runs_offline_and_is_relative_only():
    image = _architectural_image(320, 180)
    result = predict_relative_depth(image)

    assert depth_model_status()["available"] is True
    assert result.status == "ok"
    assert result.depth is not None and result.depth.shape == (180, 320)
    assert result.edge is not None and result.edge.shape == (180, 320)
    assert 0.0 <= float(result.depth.min()) <= float(result.depth.max()) <= 1.0
