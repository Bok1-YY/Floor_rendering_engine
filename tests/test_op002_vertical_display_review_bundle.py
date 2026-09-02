from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_op002_vertical_display_review_bundle as target


def test_bundle_preserves_failure_and_accepts_retry() -> None:
    result = target.build()
    assert result["schema"] == "op002-layer3b-display-review-bundle-v1"
    assert result["historical_failure_preserved"] is True
    assert result["historical_schema_rejected_result"]["usable_advisory"] is False
    assert result["historical_schema_rejected_result"]["raw_opening_id"] == "OP002 Layer3B"
    assert result["selected_result"]["accepted_for_layer3b_research_display"] is True
    parsed = result["selected_result"]["parsed"]
    assert parsed["intact_wall_baseline_visible"] == "yes"
    assert parsed["orange_head_assumption_guide_visible"] == "yes"
    assert parsed["floor_to_head_opening_cut_visible"] == "no"
    assert parsed["door_leaf_threshold_or_sill_geometry_visible"] == "no"
    assert parsed["display_misleading_as_confirmed_opening"] == "no"
    assert result["selected_review_cost_usd"] == pytest.approx(0.0008356)
    assert result["total_review_cost_usd"] == pytest.approx(0.0016709)
    assert result["source_vertical_confirmation"] is False
    assert result["semantic_promotion"] is False
    assert result["score_effect"] == "none"
    assert result["build_authorized"] is False
    assert result["ready"] is False
    assert target.validate(result) == result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("historical_failure_preserved", False),
        ("accepted_for_layer3b_research_display", False),
        ("source_vertical_confirmation", True),
        ("semantic_promotion", True),
        ("score_effect", "increase"),
        ("build_authorized", True),
    ],
)
def test_rehashed_bundle_tampering_is_rejected(field: str, value) -> None:
    candidate = deepcopy(target.build())
    candidate[field] = value
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate)


def test_cli_copies_both_results_and_rebuilds_identical(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_op002_vertical_display_review_bundle.py"),
            "--selected",
            str(target.SELECTED_RESULT),
            "--historical",
            str(target.HISTORICAL_RESULT),
            "--out",
            str(out),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert generated == target.build()
    assert completed.stdout.strip() == generated["candidate_hash"]
    assert (out / "selected-result.json").is_file()
    assert (out / "historical-schema-rejected-result.json").is_file()
    assert (out / "REPORT.md").is_file()
    assert (out / "REVIEW_CARD_ZH.md").is_file()
