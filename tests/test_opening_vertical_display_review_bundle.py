from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_opening_vertical_display_review_bundle as target


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "reports/op004_vertical_display_evidence_20260903/evidence.json"
RESULT = ROOT / "reports/op004_vertical_display_review_20260903/selected-result.json"


def _build() -> dict:
    return target.build("OP004", evidence_path=EVIDENCE, result_path=RESULT)


def test_op004_bundle_accepts_only_display_clarity() -> None:
    result = _build()
    assert result["accepted_for_layer3b_research_display"] is True
    parsed = result["selected_result"]["parsed"]
    assert parsed["intact_wall_baseline_visible"] == "yes"
    assert parsed["orange_unbound_head_guide_visible"] == "yes"
    assert parsed["floor_to_head_opening_cut_visible"] == "no"
    assert parsed["door_leaf_threshold_or_sill_geometry_visible"] == "no"
    assert parsed["display_misleading_as_confirmed_opening"] == "no"
    assert result["selected_review_cost_usd"] == pytest.approx(0.0008062)
    assert result["source_vertical_confirmation"] is False
    assert result["root_confirmation"] is False
    assert result["build_authorized"] is False
    assert target.validate(result, "OP004", evidence_path=EVIDENCE, result_path=RESULT) == result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accepted_for_layer3b_research_display", False),
        ("source_vertical_confirmation", True),
        ("effective_void_confirmation", True),
        ("root_confirmation", True),
        ("score_effect", "increase"),
        ("build_authorized", True),
    ],
)
def test_rehashed_bundle_tampering_is_rejected(field: str, value) -> None:
    candidate = deepcopy(_build())
    candidate[field] = value
    candidate["candidate_hash"] = target._candidate_hash({key: item for key, item in candidate.items() if key != "candidate_hash"})
    with pytest.raises(ValueError):
        target.validate(candidate, "OP004", evidence_path=EVIDENCE, result_path=RESULT)


def test_cli_rebuilds_identical_bundle(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_opening_vertical_display_review_bundle.py"),
            "--opening-id", "OP004",
            "--evidence", str(EVIDENCE),
            "--result", str(RESULT),
            "--out", str(out),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert generated == _build()
    assert completed.stdout.strip() == generated["candidate_hash"]
