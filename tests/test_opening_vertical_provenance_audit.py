from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_opening_vertical_provenance_audit as target


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "reports/op004_clean_subtype_20260903/bundle.json"
RESULT = ROOT / "reports/op004_clean_subtype_20260903/selected-result.json"


def _build() -> dict:
    return target.build(
        "OP004",
        subtype_bundle_path=BUNDLE,
        subtype_result_path=RESULT,
    )


def test_op004_has_unbound_head_default_and_unknown_sill() -> None:
    result = _build()
    assert result["opening_candidate_state"] == {
        "opening_status": "candidate",
        "source_observation_status": "candidate",
        "build_disposition": "exclude_pending_resolution",
        "build_kind": None,
        "effective_void_present": False,
        "source_host_present": False,
        "assumption_ids": ["ASSUME-Z-RESEARCH"],
    }
    assert result["xy_research_binding"]["host_atom_id"] == "ATOM-WB007-01"
    assert result["xy_research_binding"]["vertical_authority"] is False
    assert result["vertical_parameters"]["wall_height_m"]["research_default_value"] == pytest.approx(2.8)
    assert result["vertical_parameters"]["head_m"]["source_observed_value"] is None
    assert result["vertical_parameters"]["head_m"]["research_default_value"] == pytest.approx(2.1)
    assert (
        result["vertical_parameters"]["head_m"]["provenance_class"]
        == "research_assumption_unbound_to_opening_geometry"
    )
    assert result["vertical_parameters"]["sill_m"]["source_observed_value"] is None
    assert result["vertical_parameters"]["sill_m"]["provenance_class"] == "unknown"
    assert result["vertical_parameters"]["sill_m"]["treatment"] == "unknown"
    assert result["vertical_evidence_present"] is False
    assert result["isolated_blender_research_display"]["head_guide_binding"] == "unbound_research_default"
    assert result["isolated_blender_research_display"]["opening_geometry_authorized"] is False
    for key in target.FAIL_CLOSED:
        assert result[key] is False
    assert result["score_effect"] == "none"
    assert target.validate(
        result,
        "OP004",
        subtype_bundle_path=BUNDLE,
        subtype_result_path=RESULT,
    ) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["vertical_parameters"]["head_m"].__setitem__("source_observed_value", 2.1),
        lambda value: value["vertical_parameters"]["head_m"].__setitem__("provenance_class", "source_explicit"),
        lambda value: value["vertical_parameters"]["sill_m"].__setitem__("source_observed_value", 0.0),
        lambda value: value["xy_research_binding"].__setitem__("vertical_authority", True),
        lambda value: value.__setitem__("source_vertical_confirmation", True),
        lambda value: value.__setitem__("effective_void_confirmation", True),
        lambda value: value.__setitem__("pair_confirmation", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_op004_vertical_tampering_is_rejected(mutator) -> None:
    candidate = deepcopy(_build())
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(
            candidate,
            "OP004",
            subtype_bundle_path=BUNDLE,
            subtype_result_path=RESULT,
        )


def test_cli_rebuilds_identical_audit(tmp_path: Path) -> None:
    output = tmp_path / "audit" / "audit.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_opening_vertical_provenance_audit.py"),
            "--opening-id",
            "OP004",
            "--subtype-bundle",
            str(BUNDLE),
            "--subtype-result",
            str(RESULT),
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
    assert generated == _build()
    assert completed.stdout.strip() == generated["candidate_hash"]
    assert (output.parent / "REPORT.md").is_file()
    assert (output.parent / "REVIEW_CARD_ZH.md").is_file()
