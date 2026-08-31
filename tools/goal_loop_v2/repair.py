from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.goal_loop_v2.common import CURRENT_PATH, atomic_write_json, load_state, now_utc, read_json, validate_state


FORBIDDEN_KEYS = {"blend_path", "mesh", "mesh_name", "object", "object_name", "vertices", "bpy"}


def _forbidden_parameter_path(value: Any, path: str = "parameters") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                return f"{path}.{key}"
            found = _forbidden_parameter_path(child, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _forbidden_parameter_path(child, f"{path}[{index}]")
            if found:
                return found
    return None


def validate_repair_plan(plan: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("schema") != "goal-loop-v2-repair-plan-v1":
        raise ValueError("repair plan schema mismatch")
    attempt = plan.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= contract["max_repair_attempts"]:
        raise ValueError("repair attempt must be 1..2")
    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("repair operations must be a non-empty array")
    allowed = set(contract["allowed_repair_operations"])
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or operation.get("operation") not in allowed:
            raise ValueError(f"operations[{index}] is not an allowed contract repair")
        if FORBIDDEN_KEYS & set(operation):
            raise ValueError(f"operations[{index}] attempts a forbidden Blender-mesh repair")
        if not isinstance(operation.get("target_id"), str) or not operation["target_id"]:
            raise ValueError(f"operations[{index}].target_id is required")
        if not isinstance(operation.get("parameters"), dict):
            raise ValueError(f"operations[{index}].parameters must be an object")
        forbidden_path = _forbidden_parameter_path(operation["parameters"])
        if forbidden_path:
            raise ValueError(f"operations[{index}] attempts a forbidden Blender-mesh repair at {forbidden_path}")
    if not isinstance(plan.get("prior_structure_hash"), str) or len(plan["prior_structure_hash"]) != 64:
        raise ValueError("prior_structure_hash must be sha256")
    return dict(plan)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and stage a structure-contract-only repair plan.")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    state, contract = load_state()
    plan = validate_repair_plan(read_json(args.plan), contract)
    if plan["attempt"] <= state["iteration"]:
        raise SystemExit("repair attempt is not newer than CURRENT iteration")
    result = {"dry_run": args.dry_run, "attempt": plan["attempt"], "operation_count": len(plan["operations"]), "next_action": "Apply operations to the structure contract, recompute structure_hash, and rebuild from a blank Blender scene."}
    if not args.dry_run:
        state.update({"goal_status": "running", "stage": "repairing", "iteration": plan["attempt"], "failure_class": plan.get("failure_class"), "next_action": result["next_action"], "updated_at": now_utc()})
        atomic_write_json(CURRENT_PATH, validate_state(state, contract))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
