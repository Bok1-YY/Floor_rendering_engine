"""Fail-closed receiving-wall candidates for face-abutment endpoints."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from typing import Any, Mapping

from shapely.geometry import LineString, Point, Polygon

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.endpoint_policy_inventory import build_endpoint_policy_inventory
from tools.goal_loop_v2.junction_wall_solids import _atom_geometry

SCHEMA = "face-abutment-target-candidate-v1"
RAY_LENGTH_M = 0.25
MAX_TARGET_GAP_M = 0.05
TIE_TOLERANCE_M = 1e-7


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _geometry_points(geometry):
    if geometry.is_empty: return []
    if geometry.geom_type == "Point": return [(geometry.x, geometry.y)]
    if hasattr(geometry, "geoms"): return [point for part in geometry.geoms for point in _geometry_points(part)]
    return list(geometry.coords)


def _derive(document: Mapping[str, Any]):
    inventory = build_endpoint_policy_inventory(document)
    atoms = {row["id"]: row for row in document["wall_graph"]["atoms"]}
    polygons = {atom_id: _atom_geometry(atom, 0.0, 0.0, Polygon) for atom_id, atom in atoms.items()}
    records = []
    for endpoint in inventory["records"]:
        if endpoint["policy_candidate"] != "face_abutment_candidate": continue
        atom = atoms[endpoint["atom_id"]]; index = endpoint["endpoint_index"]
        point = tuple(atom["centerline_m"][index]); other = tuple(atom["centerline_m"][1-index])
        vx, vy = point[0]-other[0], point[1]-other[1]; length = math.hypot(vx, vy); ux, uy = vx/length, vy/length
        ray = LineString([point, (point[0]+ux*RAY_LENGTH_M, point[1]+uy*RAY_LENGTH_M)])
        hits = []
        for target_id, polygon in polygons.items():
            if target_id == atom["id"]: continue
            values = [0.0] if polygon.covers(Point(point)) else []
            for candidate in _geometry_points(ray.intersection(polygon)):
                distance = (candidate[0]-point[0])*ux + (candidate[1]-point[1])*uy
                if distance >= -1e-9: values.append(max(0.0, distance))
            if values: hits.append((min(values), target_id))
        hits.sort(key=lambda row: (row[0], row[1]))
        eligible = [row for row in hits if row[0] <= MAX_TARGET_GAP_M]
        if not eligible:
            status, target, gap, ties = "unresolved_no_forward_target", None, None, []
        else:
            gap = eligible[0][0]; ties = [row[1] for row in eligible if abs(row[0]-gap) <= TIE_TOLERANCE_M]
            if len(ties) == 1: status, target = "resolved_unique_candidate", ties[0]
            else: status, target = "ambiguous_tied_targets", None
        records.append({
            "atom_id": atom["id"], "endpoint_index": index, "node_id": endpoint["node_id"],
            "point_m": deepcopy(endpoint["point_m"]), "outward_unit": [ux, uy],
            "target_status": status, "target_atom_id": target, "face_gap_m": gap,
            "tied_target_atom_ids": ties, "target_confirmation": False,
        })
    counts: dict[str, int] = {}
    for row in records: counts[row["target_status"]] = counts.get(row["target_status"], 0) + 1
    return inventory, records, {key: counts[key] for key in sorted(counts)}


def build_face_abutment_targets(document: Mapping[str, Any], *, _skip_validate=False):
    doc = validate_v21_document(document); inventory, records, counts = _derive(doc)
    result = {
        "schema": SCHEMA, "source_structure_hash": doc["structure_hash"],
        "endpoint_inventory_hash": inventory["candidate_hash"],
        "parameters": {"ray_length_m": RAY_LENGTH_M, "max_target_gap_m": MAX_TARGET_GAP_M, "tie_tolerance_m": TIE_TOLERANCE_M},
        "coverage": {"face_abutment_endpoint_count": len(records), "status_counts": counts},
        "records": records,
        "status": "pending_independent_review", "target_confirmation": False,
        "wall_solid_mutation": False, "semantic_promotion": False, "build_authorized": False, "ready": False,
        "candidate_hash": "0"*64,
    }
    result["candidate_hash"] = _hash({key:value for key,value in result.items() if key!="candidate_hash"})
    return result if _skip_validate else validate_face_abutment_targets(doc, result)


def validate_face_abutment_targets(document: Mapping[str, Any], candidate: Mapping[str, Any]):
    doc = validate_v21_document(document)
    if candidate.get("schema") != SCHEMA or candidate.get("source_structure_hash") != doc["structure_hash"]: raise ValueError("face-abutment source/schema drift")
    for key in ("target_confirmation","wall_solid_mutation","semantic_promotion","build_authorized","ready"):
        if candidate.get(key) is not False: raise ValueError("face-abutment candidate was promoted")
    if dict(candidate) != build_face_abutment_targets(doc, _skip_validate=True): raise ValueError("face-abutment geometry/ranking drift")
    return deepcopy(dict(candidate))


__all__ = ["build_face_abutment_targets", "validate_face_abutment_targets"]
