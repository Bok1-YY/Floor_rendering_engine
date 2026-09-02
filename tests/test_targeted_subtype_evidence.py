from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from tools.goal_loop_v2 import build_targeted_subtype_evidence as target


CASES = {
    "OP006": [1230, 790, 1409, 1040],
    "OP008": [635, 1713, 840, 1825],
}


@pytest.mark.parametrize(("opening_id", "box"), CASES.items())
def test_targeted_crop_is_source_pixel_exact(opening_id: str, box: list[int], tmp_path: Path) -> None:
    out = tmp_path / opening_id
    result = target.build(opening_id, box, out_dir=out)
    assert result["targeted_crop_box_px"] == box
    assert result["minimum_target_endpoint_clearance_px"] >= 15
    assert result["artifacts"]["targeted_raw_crop"]["semantic_authority"] is True
    assert result["artifacts"]["locator"]["semantic_authority"] is False
    assert result["artifacts"]["parent_raw_crop"]["semantic_authority_for_targeted_review"] is False
    source_path = target.ROOT / result["source_image"]["path"]
    crop_path = out / result["artifacts"]["targeted_raw_crop"]["relative_path"]
    with Image.open(source_path) as source, Image.open(crop_path) as crop:
        assert crop.convert("RGB").tobytes() == source.convert("RGB").crop(tuple(box)).tobytes()
    assert target.validate(result, opening_id, box, out_dir=out) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["targeted_crop_box_px"].__setitem__(0, 0),
        lambda value: value["artifacts"]["locator"].__setitem__("semantic_authority", True),
        lambda value: value.__setitem__("target_cue_isolated", True),
        lambda value: value.__setitem__("source_subtype_confirmation", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_targeted_evidence_tampering_is_rejected(mutator, tmp_path: Path) -> None:
    out = tmp_path / "OP006"
    candidate = target.build("OP006", CASES["OP006"], out_dir=out)
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash({key: value for key, value in candidate.items() if key != "candidate_hash"})
    with pytest.raises(ValueError):
        target.validate(candidate, "OP006", CASES["OP006"], out_dir=out)


def test_cli_rebuilds_identical_evidence(tmp_path: Path) -> None:
    first = tmp_path / "first"
    canonical = target.build("OP006", CASES["OP006"], out_dir=first)
    second = tmp_path / "second"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_targeted_subtype_evidence.py"),
            "--opening-id", "OP006",
            "--crop-box", *map(str, CASES["OP006"]),
            "--out", str(second),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    assert json.loads((second / "evidence.json").read_text(encoding="utf-8")) == canonical
