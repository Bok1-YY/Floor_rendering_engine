from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_opening_vertical_display_plan as target


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "reports/op004_vertical_provenance_20260903/audit.json"
BUNDLE = ROOT / "reports/op004_clean_subtype_20260903/bundle.json"
RESULT = ROOT / "reports/op004_clean_subtype_20260903/selected-result.json"


def _build() -> dict:
    return target.build(
        "OP004",
        audit_path=AUDIT,
        subtype_bundle_path=BUNDLE,
        subtype_result_path=RESULT,
    )


def test_op004_plan_has_intact_walls_and_unbound_guide() -> None:
    result = _build()
    assert result["baseline"] == {
        "source_wall_atom_count": 35,
        "intact_source_wall_count": 35,
        "opening_cuts": 0,
    }
    assert result["guide_object_count"] == 2
    assert [guide["role"] for guide in result["guide_specs"]] == [
        "nonsemantic_xy_locator",
        "nonsemantic_unbound_head_guide",
    ]
    assert result["vertical_assumptions"]["head_guide_m"]["binding"] == "unbound_research_default"
    assert result["vertical_assumptions"]["head_guide_m"]["value"] == pytest.approx(2.1)
    assert result["vertical_assumptions"]["sill_m"]["value"] is None
    assert all(guide["opening_geometry"] is False for guide in result["guide_specs"])
    assert result["opening_geometry_created"] is False
    assert result["door_leaf_created"] is False
    assert result["ifc_opening_created"] is False
    for key in target.FAIL_CLOSED:
        assert result[key] is False
    assert result["score_effect"] == "none"
    assert target.validate(
        result,
        "OP004",
        audit_path=AUDIT,
        subtype_bundle_path=BUNDLE,
        subtype_result_path=RESULT,
    ) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["baseline"].__setitem__("opening_cuts", 1),
        lambda value: value["vertical_assumptions"]["head_guide_m"].__setitem__("binding", "source_opening_geometry"),
        lambda value: value["vertical_assumptions"]["sill_m"].__setitem__("value", 0.0),
        lambda value: value["guide_specs"][1].__setitem__("role", "lintel_structural_element"),
        lambda value: value.__setitem__("opening_geometry_created", True),
        lambda value: value.__setitem__("source_vertical_confirmation", True),
        lambda value: value.__setitem__("root_confirmation", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_generic_display_plan_tampering_is_rejected(mutator) -> None:
    candidate = deepcopy(_build())
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(
            candidate,
            "OP004",
            audit_path=AUDIT,
            subtype_bundle_path=BUNDLE,
            subtype_result_path=RESULT,
        )


def test_cli_rebuilds_identical_plan(tmp_path: Path) -> None:
    output = tmp_path / "plan" / "plan.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_opening_vertical_display_plan.py"),
            "--opening-id",
            "OP004",
            "--audit",
            str(AUDIT),
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
