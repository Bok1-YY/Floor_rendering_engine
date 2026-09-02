from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_op002_vertical_display_plan as target


def test_plan_keeps_walls_intact_and_defines_only_two_guides() -> None:
    result = target.build()
    assert result["schema"] == "op002-vertical-display-plan-v2"
    assert result["baseline"]["source_wall_atom_count"] == 35
    assert result["baseline"]["intact_source_wall_count"] == 35
    assert result["baseline"]["opening_cuts"] == 0
    assert result["guide_object_count"] == 2
    assert [guide["role"] for guide in result["guide_specs"]] == [
        "nonsemantic_xy_locator",
        "nonsemantic_head_assumption_guide",
    ]
    assert all(guide["opening_geometry"] is False for guide in result["guide_specs"])
    assert result["vertical_assumptions"]["wall_height_m"]["value"] == pytest.approx(2.8)
    assert result["vertical_assumptions"]["head_m"]["value"] == pytest.approx(2.1)
    assert result["vertical_assumptions"]["sill_m"]["value"] is None
    assert result["vertical_assumptions"]["sill_m"]["provenance_class"] == "unknown"
    assert result["guide_specs"][1]["z_center_m"] == pytest.approx(2.1)
    assert result["guide_specs"][1]["z_min_m"] == pytest.approx(2.08)
    assert result["guide_specs"][1]["z_max_m"] == pytest.approx(2.12)
    assert result["guide_specs"][0]["z_min_m"] > 2.8
    assert result["labels"] == target.LABELS
    assert result["forbidden_object_roles"] == target.FORBIDDEN_OBJECT_ROLES
    for key in target.FAIL_CLOSED:
        assert result[key] is False
    assert result["score_effect"] == "none"
    assert target.validate(result) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["baseline"].__setitem__("opening_cuts", 1),
        lambda value: value["vertical_assumptions"]["sill_m"].__setitem__("value", 0.0),
        lambda value: value["guide_specs"][1].__setitem__("z_center_m", 2.2),
        lambda value: value["guide_specs"][1].__setitem__("role", "lintel_structural_element"),
        lambda value: value.__setitem__("opening_geometry_created", True),
        lambda value: value.__setitem__("door_leaf_created", True),
        lambda value: value.__setitem__("ifc_opening_created", True),
        lambda value: value.__setitem__("source_vertical_confirmation", True),
        lambda value: value.__setitem__("semantic_promotion", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_display_plan_tampering_is_rejected(mutator) -> None:
    candidate = deepcopy(target.build())
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate)


def test_vertical_audit_upstream_drift_is_rejected(tmp_path: Path) -> None:
    audit = json.loads(target.AUDIT.read_text(encoding="utf-8"))
    audit["vertical_parameters"]["sill_m"]["treatment"] = "accepted"
    audit["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in audit.items() if key != "candidate_hash"}
    )
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError):
        target.build(audit_path=path)


def test_cli_rebuilds_identical_candidate(tmp_path: Path) -> None:
    output = tmp_path / "plan" / "plan.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_op002_vertical_display_plan.py"),
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
