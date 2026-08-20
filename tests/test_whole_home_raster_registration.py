import json

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from Floor_engine_server.whole_home_raster_registration import (
    RasterRegistrationError,
    build_structure_evidence,
    lock_raster_scale,
    prepare_raster_source,
    wall_ink_support,
)
from Floor_engine_server.whole_home_geometry import SourceRegistration


def _plan(path):
    image = Image.new("RGB", (420, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 35, 380, 265), outline="black", width=10)
    draw.line((210, 40, 210, 260), fill="black", width=8)
    draw.line((45, 150, 375, 150), fill="black", width=8)
    image.save(path)


def test_prepare_registration_is_reversible_and_deterministic(tmp_path):
    source = tmp_path / "plan.png"
    _plan(source)
    first = prepare_raster_source(
        str(source), str(tmp_path / "a"), crop_polygon=[[20, 20], [400, 20], [400, 280], [20, 280]],
        rotation_degrees=1.5,
    )
    second = prepare_raster_source(
        str(source), str(tmp_path / "b"), crop_polygon=[[20, 20], [400, 20], [400, 280], [20, 280]],
        rotation_degrees=1.5,
    )
    assert first["input_grade"] == "raster_draft"
    assert first["roundtrip_error_px"] <= 0.25
    assert first["registration_hash"] == second["registration_hash"]
    source_to = np.asarray(first["source_to_canonical"])
    inverse = np.asarray(first["canonical_to_source"])
    assert np.allclose(source_to @ inverse, np.eye(3), atol=1e-7)


def test_perspective_rejects_mirror_and_warps_ordered_quad(tmp_path):
    source = tmp_path / "plan.png"
    _plan(source)
    result = prepare_raster_source(
        str(source), str(tmp_path / "ok"),
        perspective_points=[[30, 20], [390, 35], [375, 280], [45, 265]],
    )
    assert result["canonical_width"] > 300
    assert result["canonical_height"] > 200
    with pytest.raises(RasterRegistrationError, match="mirror"):
        prepare_raster_source(
            str(source), str(tmp_path / "bad"),
            perspective_points=[[30, 20], [45, 265], [375, 280], [390, 35]],
        )


def test_scale_lock_requires_real_consistent_uniform_dimensions(tmp_path):
    source = tmp_path / "plan.png"
    _plan(source)
    draft = prepare_raster_source(str(source), str(tmp_path / "registration"))
    locked = lock_raster_scale(draft, [
        {"id": "horizontal", "start_px": [20, 20], "end_px": [220, 20], "length_m": 10},
        {"id": "vertical", "start_px": [20, 20], "end_px": [20, 120], "length_m": 5.05},
    ], reviewer="tester")
    assert locked["input_grade"] == "raster_human_locked"
    assert SourceRegistration(locked)["registration_hash"] == locked["registration_hash"]
    assert locked["scale_anchors"][0]["actual_length_m"] == 10
    matrix = np.asarray(locked["canonical_to_model"])
    assert matrix[0, 0] == pytest.approx(matrix[1, 1])
    assert matrix[0, 1] == matrix[1, 0] == 0
    with pytest.raises(RasterRegistrationError, match="2%"):
        lock_raster_scale(draft, [
            {"start_px": [0, 0], "end_px": [100, 0], "length_m": 5},
            {"start_px": [0, 0], "end_px": [100, 0], "length_m": 6},
        ], reviewer="tester")


def test_structure_evidence_supports_drawn_walls_and_rejects_empty_candidate(tmp_path):
    source = tmp_path / "plan.png"
    _plan(source)
    draft = prepare_raster_source(str(source), str(tmp_path / "registration"))
    evidence = build_structure_evidence(
        draft["canonical_artifact_path"], str(tmp_path / "evidence"))
    assert 0 < evidence["ink_fraction"] < 0.5
    supported = wall_ink_support(evidence["mask_path"], [
        {"start_px": [40, 40], "end_px": [380, 40]},
        {"start_px": [210, 40], "end_px": [210, 260]},
    ])
    unsupported = wall_ink_support(evidence["mask_path"], [
        {"start_px": [80, 90], "end_px": [170, 90]},
    ])
    assert supported["support_ratio"] > unsupported["support_ratio"]
    assert supported["distance_p95_px"] < unsupported["distance_p95_px"]


def test_registration_hash_excludes_storage_location(tmp_path):
    source = tmp_path / "plan.png"
    _plan(source)
    a = prepare_raster_source(str(source), str(tmp_path / "one"))
    b = prepare_raster_source(str(source), str(tmp_path / "two"))
    assert a["registration_hash"] == b["registration_hash"]
    assert json.dumps(a["source_to_canonical"]) == json.dumps(b["source_to_canonical"])
