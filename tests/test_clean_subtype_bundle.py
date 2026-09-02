from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_clean_subtype_bundle as target


ROOT = Path(__file__).resolve().parents[1]
OP004_RESULT = ROOT / "reports/op004_clean_subtype_20260903/selected-result.json"
OP001_RESULT = ROOT / "reports/op001_clean_subtype_20260903/selected-result.json"


@pytest.fixture(scope="module")
def bundle() -> dict:
    return target.build("OP004", result_path=OP004_RESULT)


def test_op004_bundle_accepts_visual_door_candidate_only(bundle: dict) -> None:
    assert bundle["schema"] == "clean-subtype-bundle-v1"
    assert bundle["opening_id"] == "OP004"
    assert bundle["visual_subtype_candidate"] == "door"
    assert bundle["selected_result"]["kind_specific_visual_cue_present"] is True
    assert bundle["accepted_for_layer3a_visual_subtype_research"] is True
    assert bundle["selected_result"]["parsed"] == {
        "opening_id": "OP004",
        "visual_kind": "door",
        "wall_break_visible": "yes",
        "swing_arc_visible": "yes",
        "sliding_track_visible": "no",
        "confidence": "high",
    }
    assert bundle["selected_review_cost_usd"] == pytest.approx(0.0003883)
    assert bundle["vertical_parameters_reviewed"] is False
    assert bundle["source_subtype_confirmation"] is False
    assert bundle["pair_confirmation"] is False
    assert bundle["semantic_promotion"] is False
    assert bundle["score_effect"] == "none"
    assert bundle["build_authorized"] is False
    assert bundle["ready"] is False
    assert target.validate(bundle, "OP004", result_path=OP004_RESULT) == bundle


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("visual_subtype_candidate", "window_or_fixed_glazing"),
        ("vertical_parameters_reviewed", True),
        ("source_subtype_confirmation", True),
        ("pair_confirmation", True),
        ("semantic_promotion", True),
        ("score_effect", "increase"),
        ("build_authorized", True),
    ],
)
def test_rehashed_bundle_tampering_is_rejected(bundle: dict, field: str, value) -> None:
    candidate = deepcopy(bundle)
    candidate[field] = value
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate, "OP004", result_path=OP004_RESULT)


def test_cli_copies_result_and_rebuilds_identical(bundle: dict, tmp_path: Path) -> None:
    out = tmp_path / "op004"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_clean_subtype_bundle.py"),
            "--opening-id",
            "OP004",
            "--result",
            str(OP004_RESULT),
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
    assert generated == bundle
    assert completed.stdout.strip() == bundle["candidate_hash"]
    assert (out / "REPORT.md").is_file()
    assert (out / "REVIEW_CARD_ZH.md").is_file()


def test_selected_result_raw_locator_order_is_fail_closed(tmp_path: Path) -> None:
    selected = json.loads(OP004_RESULT.read_text(encoding="utf-8"))
    selected["image_bindings"].reverse()
    path = tmp_path / "selected-result.json"
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="identity/evidence"):
        target.build("OP004", result_path=path)


def test_op001_bundle_requires_entry_risk_quarantine() -> None:
    result = target.build("OP001", result_path=OP001_RESULT)
    assert result["visual_subtype_candidate"] == "door"
    assert result["accepted_for_layer3a_visual_subtype_research"] is True
    assert result["selected_result"]["canonical_result"]["risk_context"] == target.OP001_RISK_CONTEXT
    assert result["traversability_confirmation"] is False
    assert result["pair_confirmation"] is False
    assert result["root_confirmation"] is False
    assert result["building_exterior_root_confirmation"] is False
    assert result["unit_root_confirmation"] is False
    risk = result["op001_entry_root_risk_context"]
    assert risk["entry_label_is_source_pixel_context_only"] is True
    assert risk["building_exterior_intersection"] is False
    assert risk["unit_root_hypothesis"] is True
    assert risk["unit_root_confirmation"] is False
    assert risk["building_exterior_root_confirmation"] is False
    assert risk["root_confirmation"] is False


def test_op001_missing_risk_context_is_rejected(tmp_path: Path) -> None:
    selected = json.loads(OP001_RESULT.read_text(encoding="utf-8"))
    selected["risk_context"] = None
    path = tmp_path / "selected-result.json"
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="identity/evidence"):
        target.build("OP001", result_path=path)


@pytest.mark.parametrize(
    "field",
    ["root_confirmation", "building_exterior_root_confirmation", "unit_root_confirmation"],
)
def test_op001_rehashed_root_promotion_is_rejected(field: str) -> None:
    candidate = target.build("OP001", result_path=OP001_RESULT)
    candidate[field] = True
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate, "OP001", result_path=OP001_RESULT)
