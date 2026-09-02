from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_combined_gap_registered_review_bundle as target


def test_bundle_accepts_exact_combined_xy_scope() -> None:
    result = target.build()
    assert result["schema"] == "combined-gap-registered-review-bundle-v1"
    assert result["opening_ids"] == list(target.EXPECTED_IDS)
    assert result["accepted_for_combined_xy_research"] is True
    assert result["selected_result"]["accepted_for_combined_xy_research"] is True
    assert result["selected_result"]["parsed"]["unexpected_extra_full_height_gap_visible"] == "no"
    assert all(
        row["model_gap_centered_on_visible_source_opening"] == "yes"
        and row["model_gap_width_matches_source_xy"] == "yes"
        and row["neighboring_wall_or_junction_obstruction"] == "no"
        for row in result["selected_result"]["parsed"]["per_opening"]
    )
    assert result["selected_review_cost_usd"] == pytest.approx(0.0023769)
    assert result["semantic_promotion"] is False
    assert result["score_effect"] == "none"
    assert result["build_authorized"] is False
    assert result["ready"] is False
    assert target.validate(result) == result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accepted_for_combined_xy_research", False),
        ("semantic_promotion", True),
        ("build_authorized", True),
        ("score_effect", "increase"),
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


def test_cli_copies_selected_result_and_rebuilds_identical(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_combined_gap_registered_review_bundle.py"),
            "--evidence",
            str(target.EVIDENCE),
            "--result",
            str(target.SELECTED_RESULT),
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
    assert (out / "REPORT.md").is_file()
    assert (out / "REVIEW_CARD_ZH.md").is_file()
