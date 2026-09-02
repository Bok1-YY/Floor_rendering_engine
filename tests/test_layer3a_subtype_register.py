from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_layer3a_subtype_register as target


def test_register_has_complete_coverage_and_explicit_unresolved() -> None:
    result = target.build()
    assert result["schema"] == "layer3a-visual-subtype-register-v1"
    assert result["opening_ids"] == list(target.OPENING_IDS)
    assert result["excluded_opening_ids"] == list(target.EXCLUDED_IDS)
    assert result["coverage_count"] == 9
    assert result["research_bundle_acceptance_count"] == 9
    assert result["layer3a_visual_advisory_coverage_complete"] is True
    assert result["explicit_unresolved_ids"] == []
    assert result["downstream_accepted_with_quarantine_ids"] == list(target.OPENING_IDS)
    assert result["all_visual_candidates_available_for_quarantined_research"] is True
    assert result["all_subtypes_source_confirmed"] is False
    assert result["all_subtypes_downstream_ready"] is False
    assert result["vertical_entry_authorized"] is False
    assert result["score_effect"] == "none"
    assert result["build_authorized"] is False
    assert result["ready"] is False
    op009 = next(row for row in result["rows"] if row["opening_id"] == "OP009")
    assert op009["normalized_visual_candidate"] == {
        "visual_kind_family": "glazed_interface",
        "sliding_access_operation": "unconfirmed",
        "access_traversability": "unconfirmed",
    }
    op001 = next(row for row in result["rows"] if row["opening_id"] == "OP001")
    assert "building_exterior_root" in op001["quarantine"]
    assert op001["root_confirmation"] is False
    for opening_id in ("OP006", "OP008"):
        row = next(item for item in result["rows"] if item["opening_id"] == opening_id)
        assert row["targeted_remediation"]["subtype_use_status"] == "resolved_after_tighter_crop"
        assert row["targeted_remediation"]["original_advisory_preserved"] is True
        assert row["targeted_remediation"]["neighboring_visual_cues_present"] is False
        assert row["targeted_remediation"]["target_cue_isolated"] is True
    assert result["targeted_remediation_cost_usd"] == pytest.approx(0.000933)
    assert result["base_wide_crop_review_cost_usd"] == pytest.approx(0.0035225)
    assert result["cumulative_visual_review_cost_usd"] == pytest.approx(0.0044555)
    assert result["cost_accounting_model"] == "cumulative_base_advisories_plus_targeted_remediation"
    assert target.validate(result) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["opening_ids"].append("OP005"),
        lambda value: value["explicit_unresolved_ids"].append("OP006"),
        lambda value: value.__setitem__("all_subtypes_source_confirmed", True),
        lambda value: value.__setitem__("vertical_entry_authorized", True),
        lambda value: value.__setitem__("score_effect", "increase"),
        lambda value: value.__setitem__("build_authorized", True),
        lambda value: value["rows"][0].__setitem__("root_confirmation", True),
        lambda value: value["rows"][4].__setitem__("downstream_use_status", "needs_tighter_crop"),
    ],
)
def test_rehashed_register_tampering_is_rejected(mutator) -> None:
    candidate = deepcopy(target.build())
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate)


def test_cli_rebuilds_identical_register(tmp_path: Path) -> None:
    output = tmp_path / "register" / "register.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_layer3a_subtype_register.py"),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads(output.read_text(encoding="utf-8"))
    assert generated == target.build()
    assert completed.stdout.strip() == generated["candidate_hash"]
    assert (output.parent / "REPORT.md").is_file()
    assert (output.parent / "REVIEW_CARD_ZH.md").is_file()


def test_cross_opening_bundle_substitution_is_rejected() -> None:
    candidate = deepcopy(target.build())
    op006 = next(row for row in candidate["rows"] if row["opening_id"] == "OP006")
    op007 = next(row for row in candidate["rows"] if row["opening_id"] == "OP007")
    op007["bundle_candidate_hash"] = op006["bundle_candidate_hash"]
    op007["selected_raw_response_sha256"] = op006["selected_raw_response_sha256"]
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate)
