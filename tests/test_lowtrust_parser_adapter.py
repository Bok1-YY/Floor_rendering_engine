from copy import deepcopy
import pytest

from tools.goal_loop_v2.lowtrust_parser_adapter import (
    build_lowtrust_parser_candidate, validate_lowtrust_parser_candidate,
)


def _candidate():
    return build_lowtrust_parser_candidate(
        "floorplan-to-3d-trial", "trial-20260901",
        {"path": "input.png", "file_sha256": "a" * 64, "width_px": 2245, "height_px": 3043},
        {"walls": [{"polygon": [[1, 2], [3, 4]]}], "doors": [],
         "windows": [[[5, 6], [7, 8]]], "rooms": [], "unexpected": {"raw": True}},
    )


def test_parser_output_is_quarantined_and_preserved():
    c = _candidate()
    assert c["status"] == "quarantined_pixel_candidate"
    assert c["semantic_promotion"] is False
    assert c["build_authorized"] is False
    assert c["raw_output"]["unexpected"]["raw"] is True
    assert len(c["pixel_candidates"]["walls"]) == 1
    assert validate_lowtrust_parser_candidate(c)["candidate_hash"] == c["candidate_hash"]


@pytest.mark.parametrize("change", [
    lambda c: c.update(build_authorized=True),
    lambda c: c["pixel_candidates"]["walls"][0].update(status="confirmed"),
    lambda c: c["source_image"].update(file_sha256="b" * 64),
])
def test_candidate_rejects_promotion_or_provenance_drift(change):
    c = deepcopy(_candidate())
    change(c)
    with pytest.raises(ValueError):
        validate_lowtrust_parser_candidate(c)
