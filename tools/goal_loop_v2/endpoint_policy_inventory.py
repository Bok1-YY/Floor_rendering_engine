"""Fail-closed wall-atom endpoint policy inventory.

This layer mirrors explicit junction metadata; it does not construct wall
solids or promote a candidate junction into source truth.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document

SCHEMA = "wall-endpoint-policy-inventory-v1"


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _classify(node: Mapping[str, Any]) -> str:
    policy = node.get("solid_union_policy")
    incidents = node.get("incidents", [])
    if policy == "cap" and len(incidents) == 1:
        return "free_end"
    if policy == "face_abutment" and len(incidents) == 1:
        return "face_abutment_candidate"
    if policy == "union" and len(incidents) >= 3:
        return "multiway_junction_candidate"
    return "unresolved"


def _derive_records(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes = {row["id"]: row for row in doc["wall_graph"]["junctions"]}
    records = []
    for atom in sorted(doc["wall_graph"]["atoms"], key=lambda row: row["id"]):
        for endpoint_index, (node_key, point) in enumerate(
            (("start_node_id", atom["centerline_m"][0]), ("end_node_id", atom["centerline_m"][1]))
        ):
            node_id = atom[node_key]
            if node_id not in nodes:
                raise ValueError(f"missing endpoint node {node_id}")
            node = nodes[node_id]
            incident_ids = sorted(item["atom_id"] for item in node.get("incidents", []))
            records.append({
                "atom_id": atom["id"],
                "endpoint_index": endpoint_index,
                "point_m": deepcopy(point),
                "node_id": node_id,
                "node_kind": node.get("kind"),
                "node_status": node.get("status"),
                "solid_union_policy": node.get("solid_union_policy"),
                "termination_kind": node.get("termination_kind"),
                "incident_atom_ids": incident_ids,
                "incident_count": len(incident_ids),
                "policy_candidate": _classify(node),
                "policy_confirmation": False,
            })
    return records


def build_endpoint_policy_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    nodes = {row["id"]: row for row in doc["wall_graph"]["junctions"]}
    records = _derive_records(doc)
    counts: dict[str, int] = {}
    for row in records:
        key = row["policy_candidate"]
        counts[key] = counts.get(key, 0) + 1
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "source_snapshot_hash": _hash({
            "atoms": doc["wall_graph"]["atoms"],
            "junctions": doc["wall_graph"]["junctions"],
        }),
        "coverage": {
            "atom_count": len(doc["wall_graph"]["atoms"]),
            "junction_count": len(nodes),
            "endpoint_count": len(records),
            "policy_counts": {key: counts[key] for key in sorted(counts)},
        },
        "records": records,
        "limitations": {
            "junction_geometry_confirmed": False,
            "wall_solid_constructed": False,
            "room_topology_confirmed": False,
            "build": False,
        },
        "status": "pending_independent_review",
        "policy_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return validate_endpoint_policy_inventory(doc, result)


def validate_endpoint_policy_inventory(document: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    required = {
        "schema", "source_structure_hash", "source_snapshot_hash", "coverage", "records",
        "limitations", "status", "policy_confirmation", "semantic_promotion",
        "build_authorized", "ready", "candidate_hash",
    }
    if not isinstance(candidate, Mapping) or set(candidate) != required:
        raise ValueError("endpoint policy inventory keys invalid")
    for key in ("policy_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate.get(key) is not False:
            raise ValueError("endpoint policy inventory was promoted")
    if candidate.get("schema") != SCHEMA or candidate.get("source_structure_hash") != doc["structure_hash"]:
        raise ValueError("endpoint policy inventory source/schema drift")
    if candidate.get("status") != "pending_independent_review" or candidate.get("limitations") != {
        "junction_geometry_confirmed": False,
        "wall_solid_constructed": False,
        "room_topology_confirmed": False,
        "build": False,
    }:
        raise ValueError("endpoint policy inventory status/limitations drift")
    expected = deepcopy(dict(candidate))
    records = _derive_records(doc)
    if candidate.get("records") != records:
        raise ValueError("endpoint policy record drift")
    counts: dict[str, int] = {}
    for row in records:
        counts[row["policy_candidate"]] = counts.get(row["policy_candidate"], 0) + 1
    expected_coverage = {
        "atom_count": len(doc["wall_graph"]["atoms"]),
        "junction_count": len(doc["wall_graph"]["junctions"]),
        "endpoint_count": len(records),
        "policy_counts": {key: counts[key] for key in sorted(counts)},
    }
    if candidate.get("coverage") != expected_coverage:
        raise ValueError("endpoint policy coverage drift")
    expected_snapshot = _hash({"atoms": doc["wall_graph"]["atoms"], "junctions": doc["wall_graph"]["junctions"]})
    if candidate.get("source_snapshot_hash") != expected_snapshot:
        raise ValueError("endpoint policy source snapshot drift")
    if candidate.get("candidate_hash") != _hash({key: value for key, value in candidate.items() if key != "candidate_hash"}):
        raise ValueError("endpoint policy inventory hash drift")
    return expected


__all__ = ["build_endpoint_policy_inventory", "validate_endpoint_policy_inventory"]
