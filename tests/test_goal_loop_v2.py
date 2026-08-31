from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import common
from tools.goal_loop_v2.repair import validate_repair_plan
from tools.goal_loop_v2.score import score_layers


def _reports(*, source_fail: set[str] = set(), artifact_fail: set[str] = set()):
    _, contract = common.load_state()
    source_ids = contract["score_contract"]["source_contract"]["checks"]
    artifact_ids = contract["score_contract"]["artifact_mechanical"]["checks"]
    identity = {"sample_id": "1308", "source_hash": "a" * 64, "reference_hash": "b" * 64, "scoring_version": contract["score_contract"]["scoring_version"]}
    return (
        {"schema": "goal-loop-v2-score-layer-v1", "layer": "source_contract", **identity, "checks": [{"id": item, "status": "fail" if item in source_fail else "pass", "evidence": item} for item in source_ids]},
        {"schema": "goal-loop-v2-score-layer-v1", "layer": "artifact_mechanical", **identity, "checks": [{"id": item, "status": "fail" if item in artifact_fail else "pass", "evidence": item} for item in artifact_ids]},
    )


def test_current_and_contract_are_strict_and_valid() -> None:
    state, contract = common.load_state()
    assert state["active_sample"] == "1308"
    assert contract["max_repair_attempts"] == 2
    legacy = deepcopy(state)
    legacy["old_progress"] = 99
    with pytest.raises(ValueError, match="unknown or missing"):
        common.validate_state(legacy, contract)


def test_status_and_resume_dry_run_work_without_project_environment() -> None:
    before = common.CURRENT_PATH.read_bytes()
    for script, extra in (("status.py", ["--json"]), ("resume.py", ["--dry-run"])):
        completed = subprocess.run([sys.executable, str(common.ROOT / "tools" / "goal_loop_v2" / script), *extra], cwd=common.ROOT, capture_output=True, text=True, check=True)
        assert "1308" in completed.stdout
    assert common.CURRENT_PATH.read_bytes() == before


def test_score_layers_use_minimum_and_never_compensate() -> None:
    source, artifact = _reports(source_fail={"S08_PROVENANCE_UNRESOLVED"})
    result = score_layers(source, artifact)
    assert result["source_contract_score"] == 95
    assert result["artifact_mechanical_score"] == 100
    assert result["total_score"] == 95
    assert result["accepted"] is False  # every failed check is a hard gate


def test_score_rejects_missing_and_duplicate_stable_ids() -> None:
    source, artifact = _reports()
    source["checks"].pop()
    with pytest.raises(ValueError, match="exactly match"):
        score_layers(source, artifact)
    source, artifact = _reports()
    source["checks"].append(deepcopy(source["checks"][0]))
    with pytest.raises(ValueError, match="exactly match"):
        score_layers(source, artifact)


def test_progress_requires_two_points_or_net_hard_blocker_reduction() -> None:
    source, artifact = _reports(source_fail={"S08_PROVENANCE_UNRESOLVED"})
    identity = {key: source[key] for key in ("sample_id", "source_hash", "reference_hash", "scoring_version")}
    current = score_layers(source, artifact, previous={**identity, "total_score": 94, "hard_failures": ["S08_PROVENANCE_UNRESOLVED"]})
    assert current["score_delta"] == 1 and current["progressed"] is False
    current = score_layers(source, artifact, previous={**identity, "total_score": 93, "hard_failures": ["S08_PROVENANCE_UNRESOLVED"]})
    assert current["score_delta"] == 2 and current["progressed"] is True
    current = score_layers(source, artifact, previous={**identity, "total_score": 95, "hard_failures": ["S08_PROVENANCE_UNRESOLVED", "M01_SCHEMA_HASH_UNITS"]})
    assert current["hard_blocker_reduction"] == 1 and current["progressed"] is True
    # Fixing one hard gate while adding another is no net reduction.
    current = score_layers(source, artifact, previous={**identity, "total_score": 95, "hard_failures": ["M01_SCHEMA_HASH_UNITS"]})
    assert current["hard_blocker_reduction"] == 0 and current["progressed"] is False


def test_score_rejects_cross_sample_reference_and_renamed_hard_blockers() -> None:
    source, artifact = _reports()
    artifact["sample_id"] = "121m2"
    with pytest.raises(ValueError, match="identity mismatch"):
        score_layers(source, artifact)
    source, artifact = _reports(source_fail={"S08_PROVENANCE_UNRESOLVED"})
    identity = {key: source[key] for key in ("sample_id", "source_hash", "reference_hash", "scoring_version")}
    with pytest.raises(ValueError, match="unknown hard blocker"):
        score_layers(source, artifact, previous={**identity, "total_score": 95, "hard_failures": ["OLD-A", "OLD-B"]})


def test_repair_plan_is_contract_only_and_attempts_are_capped() -> None:
    _, contract = common.load_state()
    plan = {"schema": "goal-loop-v2-repair-plan-v1", "attempt": 1, "prior_structure_hash": "a" * 64, "operations": [{"operation": "move_wall_endpoint", "target_id": "W1", "parameters": {"point_m": [1, 2]}}]}
    assert validate_repair_plan(plan, contract)["attempt"] == 1
    invalid = deepcopy(plan)
    invalid["attempt"] = 3
    with pytest.raises(ValueError, match="1..2"):
        validate_repair_plan(invalid, contract)
    invalid = deepcopy(plan)
    invalid["operations"][0]["mesh_name"] = "GEO-WALL-W1"
    with pytest.raises(ValueError, match="forbidden Blender-mesh"):
        validate_repair_plan(invalid, contract)
    invalid = deepcopy(plan)
    invalid["operations"][0]["parameters"] = {"nested": [{"object_name": "GEO-WALL-W1"}]}
    with pytest.raises(ValueError, match="forbidden Blender-mesh"):
        validate_repair_plan(invalid, contract)


def test_fake_complete_state_is_rejected_and_real_complete_is_explicit() -> None:
    state, contract = common.load_state()
    fake = deepcopy(state)
    fake["goal_status"] = "complete"
    with pytest.raises(ValueError, match="cannot be complete"):
        common.validate_state(fake, contract)
    complete = deepcopy(state)
    complete.update(goal_status="complete", active_sample=None, stage="complete", blockers=[], final_human_accepted=True)
    complete["sample_status"] = {"1308": "accepted", "121m2": "accepted"}
    assert common.validate_state(complete, contract)["goal_status"] == "complete"


def test_atomic_state_write_replaces_complete_json(tmp_path: Path) -> None:
    destination = tmp_path / "CURRENT.json"
    destination.write_text("{}", encoding="utf-8")
    state, contract = common.load_state()
    common.atomic_write_json(destination, common.validate_state(state, contract))
    assert json.loads(destination.read_text(encoding="utf-8"))["schema"] == "goal-loop-v2-state"
    assert not list(tmp_path.glob(".*.tmp"))
