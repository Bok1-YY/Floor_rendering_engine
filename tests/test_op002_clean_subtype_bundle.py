from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_op002_clean_subtype_bundle as target


def test_bundle_accepts_visual_door_candidate_only() -> None:
    result = target.build()
    assert result["schema"] == "op002-clean-subtype-bundle-v1"
    assert result["opening_id"] == "OP002"
    assert result["visual_subtype_candidate"] == "door"
    assert result["accepted_for_layer3a_op002_visual_subtype_research"] is True
    assert result["selected_result"]["parsed"] == {
        "opening_id": "OP002",
        "visual_kind": "door",
        "wall_break_visible": "yes",
        "swing_arc_visible": "yes",
        "sliding_track_visible": "no",
        "confidence": "high",
    }
    assert result["selected_review_cost_usd"] == pytest.approx(0.0003829)
    assert result["vertical_parameters_reviewed"] is False
    assert result["source_subtype_confirmation"] is False
    assert result["effective_void_confirmation"] is False
    assert result["traversability_confirmation"] is False
    assert result["semantic_promotion"] is False
    assert result["score_effect"] == "none"
    assert result["build_authorized"] is False
    assert result["ready"] is False
    assert target.validate(result) == result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("visual_subtype_candidate", "window_or_fixed_glazing"),
        ("source_subtype_confirmation", True),
        ("traversability_confirmation", True),
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


def test_cli_copies_selected_result_and_rebuilds_identical(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_op002_clean_subtype_bundle.py"),
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


def test_selected_result_image_order_is_fail_closed(tmp_path: Path) -> None:
    selected = json.loads(target.SELECTED_RESULT.read_text(encoding="utf-8"))
    selected["image_bindings"].reverse()
    path = tmp_path / "selected-result.json"
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="raw-first"):
        target.build(result_path=path)
