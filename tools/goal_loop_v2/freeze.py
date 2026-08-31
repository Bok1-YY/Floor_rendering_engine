"""Apply an independently issued reference-freeze verdict to a v2 document."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.v2_contract import assess_v2_build_readiness, compute_v2_structure_hash, validate_v2_document  # noqa: E402
from tools.goal_loop_v2.common import atomic_write_json, read_json  # noqa: E402


ALLOWED_OPERATIONS = {
    "set_outer_status", "set_junction_status", "set_opening_source_status",
    "set_opening_effective_status", "remove_resolved_issues",
}


def _rows_by_id(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result = {row.get("id"): row for row in rows if isinstance(row, dict)}
    if len(result) != len(rows) or None in result:
        raise ValueError(f"{label}: duplicate or missing IDs")
    return result


def apply_freeze_verdict(document: Mapping[str, Any], verdict: Mapping[str, Any], *, document_file_sha256: str | None = None) -> dict[str, Any]:
    current = validate_v2_document(document)
    if verdict.get("schema") != "goal-loop-v2-reference-freeze-verdict-v1" or verdict.get("verdict") != "accept_reference_freeze":
        raise ValueError("unsupported or non-accepting reference-freeze verdict")
    if verdict.get("build_authorized") is not False:
        raise ValueError("reference-freeze verdict must not authorize build")
    if verdict.get("prior_structure_hash") != current["structure_hash"]:
        raise ValueError("reference-freeze verdict structure hash mismatch")
    if document_file_sha256 is not None and verdict.get("prior_document_file_sha256") != document_file_sha256:
        raise ValueError("reference-freeze verdict document file hash mismatch")

    decisions = verdict.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("reference-freeze verdict decisions are required")
    decision_by_id = {row.get("blocker_id"): row for row in decisions if isinstance(row, dict)}
    issue_by_id = _rows_by_id(current["unresolved_issues"], "unresolved issues")
    if set(decision_by_id) != set(issue_by_id) or any(row.get("decision") not in {"freeze", "keep_unresolved"} for row in decisions):
        raise ValueError("reference-freeze decisions must cover the exact unresolved blocker set")
    frozen_ids = {blocker_id for blocker_id, row in decision_by_id.items() if row["decision"] == "freeze"}
    remaining_ids = {blocker_id for blocker_id, row in decision_by_id.items() if row["decision"] == "keep_unresolved"}
    if remaining_ids != set(verdict.get("remaining_blocker_ids") or []) or frozen_ids & remaining_ids or frozen_ids | remaining_ids != set(issue_by_id):
        raise ValueError("reference-freeze blocker partition mismatch")

    result = deepcopy(current)
    junctions = _rows_by_id(result["wall_graph"]["junctions"], "junctions")
    openings = _rows_by_id(result["opening_contract"]["openings"], "openings")
    removed: set[str] = set()
    for index, operation in enumerate(verdict.get("operations") or []):
        if not isinstance(operation, Mapping) or operation.get("operation") not in ALLOWED_OPERATIONS:
            raise ValueError(f"operations[{index}]: unsupported")
        name = operation["operation"]
        if name == "set_outer_status":
            result["outer_boundary"]["status"] = operation.get("status")
            continue
        entity_ids = operation.get("entity_ids")
        if not isinstance(entity_ids, list) or len(entity_ids) != len(set(entity_ids)):
            raise ValueError(f"operations[{index}].entity_ids: unique array required")
        if name == "set_junction_status":
            target = junctions
            for entity_id in entity_ids:
                if entity_id not in target:
                    raise ValueError(f"operations[{index}]: unknown junction {entity_id}")
                target[entity_id]["status"] = operation.get("status")
        elif name == "set_opening_source_status":
            for entity_id in entity_ids:
                if entity_id not in openings:
                    raise ValueError(f"operations[{index}]: unknown opening {entity_id}")
                openings[entity_id]["source_observation"]["status"] = operation.get("status")
        elif name == "set_opening_effective_status":
            for entity_id in entity_ids:
                if entity_id not in openings or openings[entity_id]["effective_void"] is None:
                    raise ValueError(f"operations[{index}]: opening {entity_id} has no effective-void evidence")
                openings[entity_id]["effective_void"]["status"] = operation.get("status")
        elif name == "remove_resolved_issues":
            removed.update(entity_ids)
    if removed != frozen_ids:
        raise ValueError("remove_resolved_issues must equal the independently frozen blocker set")
    result["unresolved_issues"] = [row for row in result["unresolved_issues"] if row["id"] not in removed]
    if {row["id"] for row in result["unresolved_issues"]} != remaining_ids:
        raise ValueError("frozen document remaining blocker set mismatch")
    result["structure_hash"] = compute_v2_structure_hash(result)
    validate_v2_document(result)
    readiness = assess_v2_build_readiness(result)
    if readiness["ready"]:
        raise ValueError("reference freeze must not silently authorize Blender/IFC build")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a fail-closed v2 reference-freeze verdict.")
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--verdict", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--readiness-output", required=True, type=Path)
    args = parser.parse_args(argv)
    document_bytes = args.document.read_bytes()
    document = json.loads(document_bytes.decode("utf-8"))
    result = apply_freeze_verdict(document, read_json(args.verdict), document_file_sha256=hashlib.sha256(document_bytes).hexdigest())
    readiness = assess_v2_build_readiness(result)
    atomic_write_json(args.output, result)
    atomic_write_json(args.readiness_output, readiness)
    print(json.dumps({"document": str(args.output.resolve()), "remaining_blockers": [row["id"] for row in result["unresolved_issues"]], "readiness": readiness}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
