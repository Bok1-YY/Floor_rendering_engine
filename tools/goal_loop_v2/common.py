from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
CURRENT_PATH = ROOT / "docs" / "goal_loop_v2" / "CURRENT.json"
CONTRACT_PATH = ROOT / "docs" / "goal_loop_v2" / "goal-contract.json"
GOAL_STATUSES = {"paused", "paused_external", "running", "awaiting_final_human", "complete"}
STAGES = {"contract_hardening", "reference_freeze", "building", "mechanical_review", "gemini_review", "independent_review", "repairing", "method_pivot", "final_review", "complete"}
SAMPLE_STATUSES = {"pending", "running", "accepted"}
STATE_KEYS = {"schema", "goal_status", "active_sample", "stage", "iteration", "method_id", "sample_status", "last_accepted_commit", "last_run_id", "last_score", "score_history", "failure_class", "next_action", "budget_spent_usd", "budget_remaining_usd", "blockers", "final_human_accepted", "updated_at"}
CONTRACT_KEYS = {"schema", "version", "samples", "sample_order", "max_repair_attempts", "max_builds_per_session", "session_budget_usd", "minimum_score", "minimum_score_gain", "score_contract", "allowed_repair_operations", "acceptance"}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def validate_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != CONTRACT_KEYS:
        raise ValueError("goal contract uses unknown or missing fields")
    if value.get("schema") != "goal-loop-v2-contract" or value.get("version") != 2:
        raise ValueError("goal contract schema/version mismatch")
    if value.get("samples") != ["1308", "121m2"]:
        raise ValueError("goal contract must retain both frozen samples")
    if value.get("max_repair_attempts") != 2:
        raise ValueError("automatic repair is capped at two attempts")
    operations = value.get("allowed_repair_operations")
    if not isinstance(operations, list) or len(operations) != len(set(operations)) or not operations:
        raise ValueError("allowed repair operations must be a unique non-empty array")
    return dict(value)


def validate_state(value: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != STATE_KEYS:
        raise ValueError("CURRENT uses unknown or missing fields")
    if value.get("schema") != "goal-loop-v2-state":
        raise ValueError("CURRENT schema mismatch")
    if value.get("goal_status") not in GOAL_STATUSES:
        raise ValueError("CURRENT goal_status is invalid")
    if value.get("stage") not in STAGES:
        raise ValueError("CURRENT stage is invalid")
    if value.get("active_sample") not in {None, *contract["samples"]}:
        raise ValueError("CURRENT active_sample is invalid")
    sample_status = value.get("sample_status")
    if not isinstance(sample_status, dict) or set(sample_status) != set(contract["samples"]):
        raise ValueError("CURRENT sample_status must cover both samples exactly")
    if any(status not in SAMPLE_STATUSES for status in sample_status.values()):
        raise ValueError("CURRENT contains an invalid sample status")
    if not isinstance(value.get("iteration"), int) or not 0 <= value["iteration"] <= contract["max_repair_attempts"]:
        raise ValueError("CURRENT iteration exceeds the automatic repair cap")
    if not isinstance(value.get("next_action"), str) or not value["next_action"].strip():
        raise ValueError("CURRENT next_action must be one executable action")
    spent, remaining = value.get("budget_spent_usd"), value.get("budget_remaining_usd")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0 for item in (spent, remaining)):
        raise ValueError("CURRENT budget values must be non-negative numbers")
    if spent + remaining > contract["session_budget_usd"] + 1.0e-9:
        raise ValueError("CURRENT budget exceeds the session cap")
    if not isinstance(value.get("blockers"), list) or not isinstance(value.get("final_human_accepted"), bool):
        raise ValueError("CURRENT blockers/final human fields are invalid")
    if value["goal_status"] == "complete" and not (
        value["active_sample"] is None
        and all(status == "accepted" for status in sample_status.values())
        and value["stage"] in {"final_review", "complete"}
        and value["blockers"] == []
        and value["final_human_accepted"] is True
    ):
        raise ValueError("CURRENT cannot be complete before both samples and final human review are accepted")
    return dict(value)


def load_state() -> tuple[dict[str, Any], dict[str, Any]]:
    contract = validate_contract(read_json(CONTRACT_PATH))
    state = validate_state(read_json(CURRENT_PATH), contract)
    return state, contract


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
