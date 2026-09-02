from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_opening_vertical_display_evidence as target


ROOT = Path(__file__).resolve().parents[1]
ARGS = {
    "plan_path": ROOT / "reports/op004_vertical_display_plan_20260903/plan.json",
    "audit_path": ROOT / "reports/op004_vertical_provenance_20260903/audit.json",
    "subtype_bundle_path": ROOT / "reports/op004_clean_subtype_20260903/bundle.json",
    "subtype_result_path": ROOT / "reports/op004_clean_subtype_20260903/selected-result.json",
    "display_dir": ROOT / "artifacts/goal_loop_v2/1308/op004_layer3b_vertical_research_v001",
}


def test_op004_display_evidence_is_no_cut_and_labeled(tmp_path: Path) -> None:
    result = target.build("OP004", out_dir=tmp_path, **ARGS)
    assert result["image_bindings"]["composite"]["size"] == [5000, 1390]
    assert result["display_contract"] == {
        "wall_count": 35,
        "guide_count": 2,
        "opening_cuts": 0,
        "opening_elements": 0,
        "head_guide_binding": "unbound_research_default",
        "sill_m": None,
        "door_leaf_created": False,
        "ifc_opening_created": False,
    }
    assert result["source_vertical_confirmation"] is False
    assert result["build_authorized"] is False
    assert target.validate(result, "OP004", out_dir=tmp_path, **ARGS) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["display_contract"].__setitem__("opening_cuts", 1),
        lambda value: value["display_contract"].__setitem__("sill_m", 0.0),
        lambda value: value["display_contract"].__setitem__("head_guide_binding", "source_opening_geometry"),
        lambda value: value.__setitem__("source_vertical_confirmation", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_display_evidence_tampering_is_rejected(tmp_path: Path, mutator) -> None:
    candidate = target.build("OP004", out_dir=tmp_path, **ARGS)
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash({key: value for key, value in candidate.items() if key != "candidate_hash"})
    with pytest.raises(ValueError):
        target.validate(candidate, "OP004", out_dir=tmp_path, **ARGS)


def test_cli_rebuilds_identical_evidence(tmp_path: Path) -> None:
    first = tmp_path / "first"
    canonical = target.build("OP004", out_dir=first, **ARGS)
    second = tmp_path / "second"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_opening_vertical_display_evidence.py"),
            "--opening-id", "OP004",
            "--plan", str(ARGS["plan_path"]),
            "--audit", str(ARGS["audit_path"]),
            "--subtype-bundle", str(ARGS["subtype_bundle_path"]),
            "--subtype-result", str(ARGS["subtype_result_path"]),
            "--display-dir", str(ARGS["display_dir"]),
            "--out", str(second),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads((second / "evidence.json").read_text(encoding="utf-8"))
    assert generated == canonical
    assert completed.stdout.strip() == canonical["candidate_hash"]
