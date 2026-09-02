from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.fastloop_research.contract import canonical_json
import tools.goal_loop_v2.build_opening_gap_variant_plans as module


ROOT = Path(__file__).resolve().parents[1]


def _rehash(value):
    value["candidate_hash"] = hashlib.sha256(canonical_json({key: item for key, item in value.items() if key != "candidate_hash"})).hexdigest()


def test_nine_isolated_plans_and_projection_contracts():
    result = module.build()
    plans = {plan["opening_id"]: plan for plan in result["plans"]}
    assert result["opening_ids"] == ["OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010"]
    assert result["excluded_opening_ids"] == ["OP005", "OP011", "PORTAL-WB011-WB006-01", "OP012"]
    assert plans["OP001"]["projection_mode"] == "orthogonal_projection_within_wall_solid"
    assert plans["OP001"]["maximum_perpendicular_offset_m"] == pytest.approx(0.019683242619379468)
    assert plans["OP001"]["host_half_thickness_m"] == pytest.approx(0.06)
    assert plans["OP001"]["projected_vs_source_width_delta_m"] < 0.0001
    assert all(plan["projection_mode"] == "centerline_collinear" for oid, plan in plans.items() if oid != "OP001")
    assert plans["OP003"]["expected_wall_object_count"] == 35
    assert all(plan["expected_wall_object_count"] == 36 for oid, plan in plans.items() if oid != "OP003")
    assert all(plan["projection_within_host_segment"] and plan["projection_within_wall_solid"] for plan in plans.values())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["opening_ids"].append("OP011"),
        lambda value: value["plans"][0].__setitem__("host_atom_id", "FORGED"),
        lambda value: value["plans"][0].__setitem__("projected_segment_m", [[0, 0], [1, 0]]),
        lambda value: value["plans"][0].__setitem__("build_authorized", True),
        lambda value: value["plans"][0].__setitem__("projection_mode", "centerline_collinear"),
    ],
)
def test_rehashed_host_projection_inclusion_and_promotion_attacks_fail(mutate):
    forged = deepcopy(module.build())
    mutate(forged)
    for plan in forged["plans"]:
        plan["variant_hash"] = hashlib.sha256(canonical_json({key: item for key, item in plan.items() if key != "variant_hash"})).hexdigest()
    _rehash(forged)
    with pytest.raises(ValueError):
        module.validate(forged)


def test_rehashed_upstream_review_drift_fails(tmp_path, monkeypatch):
    review = json.loads(module.REVIEW.read_text(encoding="utf-8"))
    review["included_for_xy_experiment"] = review["included_for_xy_experiment"][:-1]
    review["candidate_hash"] = "0" * 64
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    monkeypatch.setattr(module, "REVIEW", path)
    with pytest.raises(ValueError):
        module.build()


def test_cli_from_temporary_cwd(tmp_path):
    script = ROOT / "tools/goal_loop_v2/build_opening_gap_variant_plans.py"
    output = tmp_path / "plans.json"
    completed = subprocess.run([sys.executable, str(script), "--output", str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert output.is_file() and json.loads(output.read_text(encoding="utf-8"))["candidate_hash"] in completed.stdout
