"""Fail-closed OP002-only opening-cut topology candidate."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from typing import Any, Mapping

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.junction_wall_solids import build_junction_wall_solid_candidate, _topology

SCHEMA = "op002-opening-cut-candidate-v1"
ALLOWED_OPENING_ID = "OP002"


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _surface_geometry(surface: Mapping[str, Any]):
    polygons = [Polygon(row["exterior"], row.get("holes", [])) for row in surface["polygons"]]
    return unary_union(polygons)


def _cut_polygon(segment, thickness: float, endpoint_delta: float = 0.0, thickness_delta: float = 0.0):
    p0, p1 = [tuple(map(float, point)) for point in segment]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("OP002 cut segment is degenerate")
    tx, ty = dx / length, dy / length
    nx, ny = -ty, tx
    start = (p0[0] - tx * endpoint_delta, p0[1] - ty * endpoint_delta)
    end = (p1[0] + tx * endpoint_delta, p1[1] + ty * endpoint_delta)
    half = thickness / 2.0 + thickness_delta
    if half <= 0:
        raise ValueError("OP002 cut thickness is invalid")
    return Polygon([
        (start[0] + nx * half, start[1] + ny * half),
        (end[0] + nx * half, end[1] + ny * half),
        (end[0] - nx * half, end[1] - ny * half),
        (start[0] - nx * half, start[1] - ny * half),
    ])


def _face_membership(document, wall_geometry, points):
    outer = Polygon(document["outer_boundary"]["polygon_m"])
    free = outer.difference(wall_geometry.intersection(outer))
    faces = [free] if free.geom_type == "Polygon" else [part for part in free.geoms if part.geom_type == "Polygon"]
    result = []
    for point in points:
        hits = [index for index, face in enumerate(faces) if face.covers(Point(point))]
        result.append(hits)
    return {"face_count": len(faces), "point_face_indices": result, "same_face": len(result) == 2 and len(result[0]) == 1 and result[0] == result[1]}


def build_op002_opening_cut_candidate(document: Mapping[str, Any], *, _skip_validate: bool = False) -> dict[str, Any]:
    doc = validate_v21_document(document)
    opening = next((row for row in doc["opening_contract"]["openings"] if row["id"] == ALLOWED_OPENING_ID), None)
    if opening is None or opening["host"]["mode"] != "wall_cut" or opening["build_disposition"] != "cut":
        raise ValueError("OP002 active wall-cut payload is unavailable")
    host_id = opening["host"]["owning_wall_atom_id"]
    host = next((row for row in doc["wall_graph"]["atoms"] if row["id"] == host_id), None)
    if host is None:
        raise ValueError("OP002 owning wall atom is missing")
    segment = opening["effective_void"]["segment_m"]
    wall_candidate = build_junction_wall_solid_candidate(doc)
    before = _surface_geometry(wall_candidate["wall_union"]["solid_m"])
    cut = _cut_polygon(segment, host["thickness_m"])
    after = before.difference(cut)
    before_topology = wall_candidate["room_topology"]["junction_policy_v2"]
    after_topology = _topology(doc, after, 9, 0.05)
    p0, p1 = segment
    midpoint = [(p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    offset = host["thickness_m"] / 2.0 + 0.05
    side_points = [[midpoint[0] + nx * offset, midpoint[1] + ny * offset], [midpoint[0] - nx * offset, midpoint[1] - ny * offset]]
    probes = []
    for name, endpoint_delta, thickness_delta in (
        ("endpoint_inward_4px", -0.0293, 0.0),
        ("exact", 0.0, 0.0),
        ("endpoint_outward_4px", 0.0293, 0.0),
        ("thickness_inward_1mm", 0.0, -0.001),
        ("thickness_outward_1mm", 0.0, 0.001),
    ):
        geometry = before.difference(_cut_polygon(segment, host["thickness_m"], endpoint_delta, thickness_delta))
        topology = _topology(doc, geometry, 9, 0.05)
        probes.append({"scenario": name, "topology": topology, "side_membership": _face_membership(doc, geometry, side_points)})
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "opening_id": ALLOWED_OPENING_ID,
        "host_atom_id": host_id,
        "segment_m": deepcopy(segment),
        "host_thickness_m": host["thickness_m"],
        "jamb_support_m": [opening["jamb_before"]["effective_support_m"], opening["jamb_after"]["effective_support_m"]],
        "wall_candidate_hash": wall_candidate["candidate_hash"],
        "cut_geometry_hash": _hash(list(cut.exterior.coords)),
        "before_topology": before_topology,
        "after_topology": after_topology,
        "side_probe_points_m": side_points,
        "side_membership_after_cut": _face_membership(doc, after, side_points),
        "sensitivity": probes,
        "limitations": {"opening_semantics_confirmed": False, "side_spaces_confirmed": False, "adjacency_confirmed": False, "build": False},
        "status": "pending_independent_review",
        "cut_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate_op002_opening_cut_candidate(doc, result)


def validate_op002_opening_cut_candidate(document: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    for key in ("cut_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate.get(key) is not False:
            raise ValueError("OP002 opening-cut candidate was promoted")
    if candidate.get("schema") != SCHEMA or candidate.get("opening_id") != ALLOWED_OPENING_ID:
        raise ValueError("OP002 opening-cut schema/allowlist violation")
    expected = build_op002_opening_cut_candidate(doc, _skip_validate=True)
    if dict(candidate) != expected:
        raise ValueError("OP002 opening-cut geometry/topology drift")
    if candidate.get("candidate_hash") != _hash({key: value for key, value in candidate.items() if key != "candidate_hash"}):
        raise ValueError("OP002 opening-cut candidate hash drift")
    if candidate.get("source_structure_hash") != doc["structure_hash"]:
        raise ValueError("OP002 opening-cut source drift")
    return deepcopy(dict(candidate))


__all__ = ["build_op002_opening_cut_candidate", "validate_op002_opening_cut_candidate"]
