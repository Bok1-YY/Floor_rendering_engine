"""Source-registered, contract-level wall endpoint repair attempt 1."""

from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v2_contract import compute_v2_structure_hash, validate_v2_document
from tools.goal_loop_v2.geometry_adjudication import proposal_content_hash


MAX_REPAIR_DISPLACEMENT_M = 0.03
TOLERANCE_M = 0.001


def _point(value: Sequence[float]) -> tuple[float, float]:
    return float(value[0]), float(value[1])


def _matrix_point(matrix, point) -> tuple[float, float]:
    x, y = _point(point)
    w = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if abs(w) <= 1e-12:
        raise ValueError("singular point transform")
    return ((matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / w, (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / w)


def _invert_affine(matrix) -> list[list[float]]:
    a, b, c = map(float, matrix[0])
    d, e, f = map(float, matrix[1])
    if [float(value) for value in matrix[2]] != [0.0, 0.0, 1.0]:
        raise ValueError("only affine 2D matrices are supported")
    determinant = a * e - b * d
    if abs(determinant) <= 1e-12:
        raise ValueError("singular affine matrix")
    return [[e / determinant, -b / determinant, (b * f - e * c) / determinant], [-d / determinant, a / determinant, (d * c - a * f) / determinant], [0.0, 0.0, 1.0]]


def _line_rectangle_interval(point, direction, support_wall) -> tuple[float, float] | None:
    p = _point(point)
    a, b = map(_point, support_wall["proposed_centerline_m"])
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    tx, ty = dx / length, dy / length
    nx, ny = -ty, tx
    half = float(support_wall.get("nominal_thickness_m") or 0.0) * 0.5
    constraints = [((p[0] - a[0]) * tx + (p[1] - a[1]) * ty, direction[0] * tx + direction[1] * ty, 0.0, length), ((p[0] - a[0]) * nx + (p[1] - a[1]) * ny, direction[0] * nx + direction[1] * ny, -half, half)]
    lower, upper = -math.inf, math.inf
    for origin, slope, minimum, maximum in constraints:
        if abs(slope) <= 1e-12:
            if origin < minimum - TOLERANCE_M or origin > maximum + TOLERANCE_M:
                return None
            continue
        first, second = (minimum - origin) / slope, (maximum - origin) / slope
        lower, upper = max(lower, min(first, second)), min(upper, max(first, second))
        if upper < lower - 1e-12:
            return None
    return lower, upper


def _target_wall_face(owner_wall, endpoint_index: int, support_wall) -> tuple[tuple[float, float], tuple[float, float], float]:
    segment = list(map(_point, owner_wall["proposed_centerline_m"]))
    endpoint, other = segment[endpoint_index], segment[1 - endpoint_index]
    axis = (endpoint[0] - other[0], endpoint[1] - other[1])
    length = math.hypot(*axis)
    if length <= 1e-12:
        raise ValueError("owner wall axis is degenerate")
    direction = (axis[0] / length, axis[1] / length)
    interval = _line_rectangle_interval(endpoint, direction, support_wall)
    if interval is None:
        raise ValueError("owner wall axis does not intersect finite supporting-wall rectangle")
    signed_move = min(interval, key=abs)
    target = endpoint[0] + direction[0] * signed_move, endpoint[1] + direction[1] * signed_move
    return target, interval, signed_move


def _segments_intersect(first, second) -> bool:
    a, b = map(_point, first)
    c, d = map(_point, second)
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    denominator = rx * sy - ry * sx
    if abs(denominator) <= 1e-12:
        return False
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / denominator
    u = (qx * ry - qy * rx) / denominator
    return -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9


def _intersection_signature(walls: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    result = []
    for index, first in enumerate(walls):
        for second in walls[index + 1:]:
            if _segments_intersect(first["proposed_centerline_m"], second["proposed_centerline_m"]):
                result.append(sorted([str(first["id"]), str(second["id"])]))
    return sorted(result)


def _hash_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def proposal_declared_hash(proposal: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(proposal))
    payload.pop("proposal_sha256", None)
    return _hash_value(payload)


def _axis_angle_degrees(first, second) -> float:
    a0, a1 = map(_point, first)
    b0, b1 = map(_point, second)
    av, bv = (a1[0] - a0[0], a1[1] - a0[1]), (b1[0] - b0[0], b1[1] - b0[1])
    denominator = math.hypot(*av) * math.hypot(*bv)
    if denominator <= 1e-12:
        return math.inf
    cosine = max(-1.0, min(1.0, abs((av[0] * bv[0] + av[1] * bv[1]) / denominator)))
    return math.degrees(math.acos(cosine))


def _validate_request(document, proposal, adjudication, request):
    keys = {"schema", "source_document_hash", "source_proposal_hash", "source_adjudication_hash", "operation", "attempt", "max_attempts", "max_displacement_m", "repair_authority", "build_authorized"}
    if not isinstance(request, Mapping) or set(request) != keys or request.get("schema") != "endpoint-repair-request-v1":
        raise ValueError("invalid endpoint repair request")
    if request["source_document_hash"] != document["structure_hash"] or request["source_proposal_hash"] != proposal_content_hash(proposal) or request["source_adjudication_hash"] != _hash_value(adjudication):
        raise ValueError("endpoint repair request has stale document/proposal/adjudication hash")
    if request["operation"] != "move_wall_endpoint" or request["attempt"] != 1 or request["max_attempts"] != 1:
        raise ValueError("only move_wall_endpoint attempt 1 is allowed")
    if request["repair_authority"] != "independent_reference_reviewer" or request["build_authorized"] is not False:
        raise ValueError("endpoint repair request lacks non-build independent authority")
    maximum = float(request["max_displacement_m"])
    if not 0 < maximum <= MAX_REPAIR_DISPLACEMENT_M:
        raise ValueError("endpoint repair displacement threshold is not defensible")


def build_endpoint_repair_plan(document: Mapping[str, Any], proposal: Mapping[str, Any], adjudication: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v2_document(document)
    _validate_request(doc, proposal, adjudication, request)
    walls = {str(row["id"]): row for row in proposal["wall_graph"]["walls"]}
    raw_to_canonical = doc["source"]["canonical"]["raw_to_canonical_3x3"]
    canonical_to_metric = doc["source"]["metric_registration"]["canonical_px_to_metric_3x3"]
    metric_to_canonical = _invert_affine(canonical_to_metric)
    canonical_to_raw = _invert_affine(raw_to_canonical)
    raw_size = doc["source"]["original"]["size_px"]
    original_intersections = _intersection_signature(list(walls.values()))
    openings_hash = _hash_value(proposal.get("openings") or [])
    operations, skipped = [], []
    unresolved_endpoint_ids = {
        (row.get("wall_id"), int(row.get("endpoint_index")))
        for decision in adjudication.get("decisions") or []
        if (decision.get("evidence") or {}).get("check") == "wall_face_termination_completeness" and decision.get("outcome") == "unresolved"
        for row in (decision.get("evidence") or {}).get("unresolved") or []
    }
    if not unresolved_endpoint_ids:
        raise ValueError("adjudication report does not identify unresolved wall endpoints")
    diagnostics = (proposal.get("wall_graph") or {}).get("endpoint_diagnostics") or []
    for row in diagnostics:
        identity = {"wall_id": row.get("wall_id"), "endpoint_index": row.get("endpoint_index")}
        if (row.get("wall_id"), int(row.get("endpoint_index"))) not in unresolved_endpoint_ids:
            continue
        candidates = list(row.get("wall_face_support_candidates") or [])
        if row.get("status") == "dangling_unresolved" or not candidates:
            skipped.append({**identity, "reason": "dangling endpoint has no explicit source target"})
            continue
        if len(candidates) != 1:
            skipped.append({**identity, "reason": "support target is not unique"})
            continue
        wall, support = walls.get(str(row.get("wall_id"))), walls.get(str(candidates[0].get("wall_id")))
        endpoint_index = int(row.get("endpoint_index"))
        if not wall or not support or endpoint_index not in (0, 1):
            skipped.append({**identity, "reason": "unknown wall/support/endpoint"})
            continue
        old_metric = _point(wall["proposed_centerline_m"][endpoint_index])
        old_raw = _point(wall["proposed_centerline_px"][endpoint_index])
        old_canonical = _matrix_point(raw_to_canonical, old_raw)
        if math.dist(_matrix_point(canonical_to_metric, old_canonical), old_metric) > TOLERANCE_M:
            skipped.append({**identity, "reason": "raw/canonical/metric evidence does not round-trip"})
            continue
        try:
            new_metric, support_interval, signed_axis_move = _target_wall_face(wall, endpoint_index, support)
        except ValueError as exc:
            skipped.append({**identity, "reason": str(exc)})
            continue
        displacement = math.dist(old_metric, new_metric)
        threshold = min(float(request["max_displacement_m"]), float(support.get("nominal_thickness_m") or 0) * 0.25)
        if displacement <= TOLERANCE_M or displacement > threshold + 1e-9:
            skipped.append({**identity, "reason": "displacement outside repair interval", "displacement_m": displacement, "threshold_m": threshold})
            continue
        new_canonical = _matrix_point(metric_to_canonical, new_metric)
        new_raw = _matrix_point(canonical_to_raw, new_canonical)
        if not (0 <= new_raw[0] <= raw_size[0] and 0 <= new_raw[1] <= raw_size[1]):
            skipped.append({**identity, "reason": "target lies outside canonical source pixels"})
            continue
        attacked_walls = deepcopy(list(walls.values()))
        attacked_wall = next(w for w in attacked_walls if w["id"] == wall["id"])
        old_axis = deepcopy(attacked_wall["proposed_centerline_m"])
        attacked_wall["proposed_centerline_m"][endpoint_index] = list(new_metric)
        angle_change = _axis_angle_degrees(old_axis, attacked_wall["proposed_centerline_m"])
        if angle_change > 0.1 + 1e-9:
            skipped.append({**identity, "reason": "move would rotate source wall axis beyond 0.1 degree", "angle_change_degrees": angle_change})
            continue
        if _intersection_signature(attacked_walls) != original_intersections:
            skipped.append({**identity, "reason": "move would create or suppress a centerline intersection"})
            continue
        support_source_px = deepcopy(support.get("source_centerline_px") or support.get("proposed_centerline_px"))
        operations.append({
            "operation": "move_wall_endpoint", "wall_id": wall["id"], "endpoint_index": endpoint_index,
            "supporting_wall_id": support["id"], "old_metric_point_m": list(old_metric), "new_metric_point_m": list(new_metric),
            "old_raw_px": list(old_raw), "new_raw_px": list(new_raw), "old_canonical_px": list(old_canonical), "new_canonical_px": list(new_canonical),
            "supporting_wall_source_axis_px": support_source_px, "canonical_pixel_sha256": doc["source"]["canonical"]["pixel_sha256"],
            "displacement_m": displacement, "maximum_displacement_m": threshold, "geometry_tolerance_m": TOLERANCE_M,
            "axis_angle_change_degrees": angle_change, "maximum_axis_angle_change_degrees": 0.1,
            "support_rectangle_axis_interval_m": list(support_interval), "signed_axis_move_m": signed_axis_move,
            "topology_signature_before": original_intersections, "openings_hash_before": openings_hash,
        })
    operations.sort(key=lambda item: (item["wall_id"], item["endpoint_index"]))
    return {"schema": "endpoint-repair-plan-v1", "operation": "move_wall_endpoint", "attempt": 1, "max_attempts": 1, "source_document_hash": doc["structure_hash"], "source_proposal_hash": proposal_content_hash(proposal), "source_adjudication_hash": _hash_value(adjudication), "operations": operations, "skipped": skipped}


def apply_endpoint_repair_plan(document, proposal, adjudication, request, plan):
    expected = build_endpoint_repair_plan(document, proposal, adjudication, request)
    if plan != expected:
        raise ValueError("endpoint repair plan differs from independent recomputation")
    repaired_proposal = deepcopy(proposal)
    repaired_document = deepcopy(validate_v2_document(document))
    original_openings = deepcopy(repaired_proposal.get("openings") or [])
    original_adjacency = deepcopy(repaired_document["adjacency_truth"])
    walls = {row["id"]: row for row in repaired_proposal["wall_graph"]["walls"]}
    branches = {row["id"]: row for row in repaired_document["wall_graph"]["branches"]}
    atoms = repaired_document["wall_graph"]["atoms"]
    junctions = {row["id"]: row for row in repaired_document["wall_graph"]["junctions"]}
    raw_size = repaired_document["source"]["original"]["size_px"]
    for operation in plan["operations"]:
        wall_id, endpoint_index = operation["wall_id"], operation["endpoint_index"]
        wall = walls[wall_id]
        wall["proposed_centerline_m"][endpoint_index] = deepcopy(operation["new_metric_point_m"])
        wall["proposed_centerline_px"][endpoint_index] = deepcopy(operation["new_raw_px"])
        wall["proposed_centerline_norm"][endpoint_index] = [operation["new_raw_px"][0] / raw_size[0] * 1000.0, operation["new_raw_px"][1] / raw_size[1] * 1000.0]
        branch = branches[wall_id]
        branch["centerline_m"][endpoint_index] = deepcopy(operation["new_metric_point_m"])
        endpoint_atoms = [atom for atom in atoms if atom["branch_id"] == wall_id and math.isclose(float(atom["branch_interval"][endpoint_index]), float(endpoint_index), abs_tol=1e-9)]
        if len(endpoint_atoms) != 1:
            raise ValueError("repaired branch endpoint does not map to exactly one atom")
        atom = endpoint_atoms[0]
        atom["centerline_m"][endpoint_index] = deepcopy(operation["new_metric_point_m"])
        node_id = atom["start_node_id" if endpoint_index == 0 else "end_node_id"]
        node = junctions[node_id]
        node["axis_point_m"] = deepcopy(operation["new_metric_point_m"])
        incident = next(row for row in node["incidents"] if row["atom_id"] == atom["id"] and row["end"] == ("start" if endpoint_index == 0 else "end"))
        incident["contact_point_m"] = deepcopy(operation["new_metric_point_m"])
        diagnostic = next(row for row in repaired_proposal["wall_graph"]["endpoint_diagnostics"] if row["wall_id"] == wall_id and int(row["endpoint_index"]) == endpoint_index)
        diagnostic["point_m"] = deepcopy(operation["new_metric_point_m"])
        diagnostic["status"] = "wall_face_termination_repair_candidate_pending_independent_review"
        candidate = next(row for row in diagnostic["wall_face_support_candidates"] if row["wall_id"] == operation["supporting_wall_id"])
        candidate.update(wall_face_distance_m=0.0, continuous_at_1mm=False)
        diagnostic["repair_evidence"] = {"operation": "move_wall_endpoint", "support_rectangle_axis_interval_m": operation["support_rectangle_axis_interval_m"], "signed_axis_move_m": operation["signed_axis_move_m"], "pending_independent_review": True}
    if repaired_proposal.get("openings") != original_openings or repaired_document["adjacency_truth"] != original_adjacency:
        raise ValueError("endpoint repair changed opening or semantic adjacency")
    if _intersection_signature(repaired_proposal["wall_graph"]["walls"]) != (plan["operations"][0]["topology_signature_before"] if plan["operations"] else _intersection_signature(proposal["wall_graph"]["walls"])):
        raise ValueError("endpoint repair changed wall intersection topology")
    repaired_proposal["proposal_sha256"] = proposal_declared_hash(repaired_proposal)
    repaired_document["structure_hash"] = compute_v2_structure_hash(repaired_document)
    repaired_document = validate_v2_document(repaired_document)
    return repaired_proposal, repaired_document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("document", "proposal", "adjudication", "request", "geometry-request"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output-proposal", required=True, type=Path)
    parser.add_argument("--output-document", required=True, type=Path)
    parser.add_argument("--output-adjudication-request-candidate", required=True, type=Path)
    args = parser.parse_args(argv)
    document, proposal, adjudication, request = [json.loads(getattr(args, name).read_text(encoding="utf-8")) for name in ("document", "proposal", "adjudication", "request")]
    plan = build_endpoint_repair_plan(document, proposal, adjudication, request)
    repaired_proposal, repaired_document = apply_endpoint_repair_plan(document, proposal, adjudication, request, plan)
    geometry_request = json.loads(getattr(args, "geometry_request").read_text(encoding="utf-8"))
    geometry_request["schema"] = "geometry-adjudication-request-candidate-v1"
    geometry_request["source_structure_hash"] = repaired_document["structure_hash"]
    geometry_request["proposal_content_hash"] = proposal_content_hash(repaired_proposal)
    geometry_request["adjudication_authority"] = "pending_independent_reference_reviewer"
    geometry_request["verdict"] = "pending"
    geometry_request["build_authorized"] = False
    args.plan.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.output_proposal.write_text(json.dumps(repaired_proposal, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.output_document.write_text(json.dumps(repaired_document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.output_adjudication_request_candidate.write_text(json.dumps(geometry_request, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"operation_count": len(plan["operations"]), "skipped_count": len(plan["skipped"]), "proposal_hash": proposal_content_hash(repaired_proposal), "document_hash": repaired_document["structure_hash"], "adjudication_request_status": "pending_independent_reference_reviewer"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
