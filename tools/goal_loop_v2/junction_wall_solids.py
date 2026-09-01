"""Fail-closed wall-solid candidates derived from endpoint-policy inventory v1.

The candidate is a geometry experiment, not source truth or a build input.
Atom strips end at face-abutment and multiway axes; only explicit ``cap``
endpoints extend by half an atom thickness.  No opening subtraction is applied.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.endpoint_policy_inventory import (
    build_endpoint_policy_inventory,
    validate_endpoint_policy_inventory,
)
from tools.goal_loop_v2 import room_polygon_candidates as room_polygon_v1


SCHEMA = "goal-loop-v2-junction-wall-solid-candidate-v1"
ENDPOINT_POLICY_BASELINE_COMMIT = "6a4568dcde5614e1240aa8bab04e178c625e5585"
LIMITATIONS = {
    "wall_solid_geometry_source_confirmed": False,
    "junction_geometry_confirmed": False,
    "opening_cut_geometry_applied": False,
    "room_topology_confirmed": False,
    "semantic_promotion": False,
    "build": False,
}


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _require_shapely():
    try:
        from shapely.geometry import Point, Polygon
        from shapely.geometry.polygon import orient
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - host dependency
        raise RuntimeError("junction wall-solid candidates require Shapely 2.x") from exc
    return Point, Polygon, orient, unary_union


def _finite_point(value: Any, path: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{path}: expected a 2D point")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) for v in value):
        raise ValueError(f"{path}: expected finite coordinates")
    return float(value[0]), float(value[1])


def _round(value: float, precision: int) -> float:
    result = round(float(value), precision)
    return 0.0 if result == 0 else result


def _parameters(coordinate_precision: Any, min_face_area_m2: Any, sensitivity_delta_m: Any) -> tuple[int, float, float]:
    if isinstance(coordinate_precision, bool) or not isinstance(coordinate_precision, int) or not 3 <= coordinate_precision <= 12:
        raise ValueError("coordinate_precision must be an integer from 3 to 12")
    for name, value, upper in (("min_face_area_m2", min_face_area_m2, 1.0), ("sensitivity_delta_m", sensitivity_delta_m, 0.01)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 <= float(value) <= upper:
            raise ValueError(f"{name} must be in [0, {upper:g}]")
    return coordinate_precision, float(min_face_area_m2), float(sensitivity_delta_m)


def _rotate_ring(points: list[list[float]]) -> list[list[float]]:
    start = min(range(len(points)), key=lambda index: tuple(points[index:] + points[:index]))
    return points[start:] + points[:start]


def _canonical_polygon(polygon: Any, orient: Any, precision: int) -> dict[str, Any]:
    normalized = orient(polygon, sign=1.0)
    exterior = [[_round(x, precision), _round(y, precision)] for x, y in list(normalized.exterior.coords)[:-1]]
    exterior = _rotate_ring(exterior)
    exterior.append(deepcopy(exterior[0]))
    holes = []
    for ring in normalized.interiors:
        points = [[_round(x, precision), _round(y, precision)] for x, y in list(ring.coords)[:-1]]
        points = _rotate_ring(points)
        points.append(deepcopy(points[0]))
        holes.append(points)
    holes.sort(key=canonical_json)
    return {"exterior": exterior, "holes": holes}


def _polygon_parts(geometry: Any) -> list[Any]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [part for part in getattr(geometry, "geoms", []) for part in _polygon_parts(part)]


def _canonical_surface(geometry: Any, orient: Any, precision: int) -> dict[str, Any]:
    polygons = [_canonical_polygon(part, orient, precision) for part in _polygon_parts(geometry)]
    polygons.sort(key=canonical_json)
    return {"type": "polygon_set", "polygons": polygons}


def _atom_geometry(atom: Mapping[str, Any], start_extension: float, end_extension: float, Polygon: Any) -> Any:
    start = _finite_point(atom["centerline_m"][0], f"atom {atom['id']} start")
    end = _finite_point(atom["centerline_m"][1], f"atom {atom['id']} end")
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    thickness = atom.get("thickness_m")
    if length <= 0:
        raise ValueError(f"atom {atom['id']} has a degenerate centerline")
    if isinstance(thickness, bool) or not isinstance(thickness, (int, float)) or not math.isfinite(float(thickness)) or float(thickness) <= 0:
        raise ValueError(f"atom {atom['id']} has invalid thickness")
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    half = float(thickness) / 2.0
    a = (start[0] - ux * start_extension, start[1] - uy * start_extension)
    b = (end[0] + ux * end_extension, end[1] + uy * end_extension)
    return Polygon([
        (a[0] + nx * half, a[1] + ny * half),
        (b[0] + nx * half, b[1] + ny * half),
        (b[0] - nx * half, b[1] - ny * half),
        (a[0] - nx * half, a[1] - ny * half),
    ])


def _endpoint_extension(record: Mapping[str, Any], atom: Mapping[str, Any], face_delta_m: float) -> float:
    policy = record["policy_candidate"]
    if policy == "free_end":
        return float(atom["thickness_m"]) / 2.0
    if policy == "face_abutment_candidate":
        return face_delta_m
    if policy == "multiway_junction_candidate":
        return 0.0
    raise ValueError(f"endpoint policy is unresolved for {record['atom_id']}:{record['endpoint_index']}")


def _derive_wall_geometry(document: Mapping[str, Any], inventory: Mapping[str, Any], face_delta_m: float) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    _, Polygon, _, unary_union = _require_shapely()
    atoms = {row["id"]: row for row in document["wall_graph"]["atoms"]}
    records = {(row["atom_id"], row["endpoint_index"]): row for row in inventory["records"]}
    atom_geometries: dict[str, Any] = {}
    for atom_id, atom in atoms.items():
        extensions = [_endpoint_extension(records[(atom_id, index)], atom, face_delta_m) for index in (0, 1)]
        if sum(extensions) <= -math.dist(*[_finite_point(point, f"atom {atom_id}") for point in atom["centerline_m"]]):
            raise ValueError(f"face sensitivity collapses atom {atom_id}")
        atom_geometries[atom_id] = _atom_geometry(atom, extensions[0], extensions[1], Polygon)
    wall_union = unary_union(list(atom_geometries.values()))
    node_geometries: dict[str, Any] = {}
    node_records: dict[str, list[Mapping[str, Any]]] = {}
    for row in inventory["records"]:
        node_records.setdefault(row["node_id"], []).append(row)
    for node_id, rows in node_records.items():
        pieces = []
        for row in rows:
            atom = atoms[row["atom_id"]]
            half = float(atom["thickness_m"]) / 2.0
            point = _finite_point(row["point_m"], f"node {node_id}")
            other = _finite_point(atom["centerline_m"][1 - row["endpoint_index"]], f"atom {atom['id']}")
            dx, dy = other[0] - point[0], other[1] - point[1]
            length = math.hypot(dx, dy)
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux
            outward = _endpoint_extension(row, atom, face_delta_m)
            inward = min(half, length)
            outer = (point[0] - ux * outward, point[1] - uy * outward)
            inner = (point[0] + ux * inward, point[1] + uy * inward)
            pieces.append(Polygon([
                (outer[0] + nx * half, outer[1] + ny * half),
                (inner[0] + nx * half, inner[1] + ny * half),
                (inner[0] - nx * half, inner[1] - ny * half),
                (outer[0] - nx * half, outer[1] - ny * half),
            ]))
        node_geometries[node_id] = unary_union(pieces)
    return wall_union, atom_geometries, node_geometries


def _topology(document: Mapping[str, Any], wall_union: Any, precision: int, min_area: float) -> dict[str, Any]:
    Point, Polygon, _, _ = _require_shapely()
    outer = Polygon([_finite_point(point, "outer boundary") for point in document["outer_boundary"]["polygon_m"]])
    clipped = wall_union.intersection(outer)
    raw = _polygon_parts(outer.difference(clipped))
    kept = [face for face in raw if face.area >= min_area]
    discarded = [face for face in raw if face.area < min_area]
    anchors_by_face = [[] for _ in kept]
    relation_counts: dict[str, int] = {}
    for space in sorted(document["spaces"], key=lambda row: row["id"]):
        point = Point(_finite_point(space["point_m"], f"space {space['id']}"))
        inside = [index for index, face in enumerate(kept) if face.contains(point)]
        covering = [index for index, face in enumerate(kept) if face.covers(point)]
        if len(inside) == 1 and inside == covering:
            relation = "inside_single_face"
            anchors_by_face[inside[0]].append(space["id"])
        elif covering:
            relation = "ambiguous_face_boundary"
        elif clipped.covers(point):
            relation = "inside_inferred_wall_solid"
        elif not outer.covers(point):
            relation = "outside_outer_boundary"
        else:
            relation = "unassigned_after_face_filter"
        relation_counts[relation] = relation_counts.get(relation, 0) + 1
    return {
        "wall_component_count": len(_polygon_parts(clipped)),
        "raw_face_count": len(raw),
        "face_candidate_count": len(kept),
        "discarded_sliver_count": len(discarded),
        "discarded_sliver_area_m2": _round(sum(face.area for face in discarded), precision),
        "inferred_wall_solid_inside_outer_area_m2": _round(clipped.area, precision),
        "candidate_face_area_m2": _round(sum(face.area for face in kept), precision),
        "single_anchor_face_count": sum(len(ids) == 1 for ids in anchors_by_face),
        "multi_anchor_face_count": sum(len(ids) > 1 for ids in anchors_by_face),
        "unlabeled_face_count": sum(not ids for ids in anchors_by_face),
        "anchor_relation_counts": {key: relation_counts[key] for key in sorted(relation_counts)},
    }


def _v1_topology(document: Mapping[str, Any], precision: int, min_area: float) -> tuple[dict[str, Any], str]:
    candidate = room_polygon_v1.build_room_polygon_candidate(
        document, coordinate_precision=precision, min_face_area_m2=min_area
    )
    keys = {
        "raw_face_count", "face_candidate_count", "discarded_sliver_count", "discarded_sliver_area_m2",
        "inferred_wall_solid_inside_outer_area_m2", "candidate_face_area_m2", "single_anchor_face_count",
        "multi_anchor_face_count", "unlabeled_face_count", "anchor_relation_counts",
    }
    topology = {key: deepcopy(candidate["coverage"][key]) for key in keys}
    topology["wall_component_count"] = None
    return topology, candidate["candidate_hash"]


def _comparison(current: Mapping[str, Any], legacy: Mapping[str, Any]) -> dict[str, Any]:
    count_keys = (
        "raw_face_count", "face_candidate_count", "discarded_sliver_count", "single_anchor_face_count",
        "multi_anchor_face_count", "unlabeled_face_count",
    )
    return {
        "topology_counts_equal": all(current[key] == legacy[key] for key in count_keys),
        "count_deltas_v2_minus_v1": {key: current[key] - legacy[key] for key in count_keys},
        "wall_area_delta_m2_v2_minus_v1": _round(
            current["inferred_wall_solid_inside_outer_area_m2"] - legacy["inferred_wall_solid_inside_outer_area_m2"], 9
        ),
        "face_area_delta_m2_v2_minus_v1": _round(current["candidate_face_area_m2"] - legacy["candidate_face_area_m2"], 9),
        "anchor_relation_counts_equal": current["anchor_relation_counts"] == legacy["anchor_relation_counts"],
    }


def _derive(document: Mapping[str, Any], inventory: Mapping[str, Any], precision: int, min_area: float, delta: float) -> dict[str, Any]:
    _, _, orient, _ = _require_shapely()
    exact_union, atom_geometries, node_geometries = _derive_wall_geometry(document, inventory, 0.0)
    exact_topology = _topology(document, exact_union, precision, min_area)
    legacy_topology, legacy_hash = _v1_topology(document, precision, min_area)
    atoms = []
    for atom_id in sorted(atom_geometries):
        surface = _canonical_surface(atom_geometries[atom_id], orient, precision)
        atoms.append({"atom_id": atom_id, "solid_m": surface, "geometry_hash": _hash(surface)})
    records_by_node: dict[str, list[Mapping[str, Any]]] = {}
    for row in inventory["records"]:
        records_by_node.setdefault(row["node_id"], []).append(row)
    junctions = []
    for node_id in sorted(node_geometries):
        rows = sorted(records_by_node[node_id], key=lambda row: (row["atom_id"], row["endpoint_index"]))
        policies = {row["policy_candidate"] for row in rows}
        if len(policies) != 1:
            raise ValueError(f"junction {node_id} has mixed endpoint policies")
        surface = _canonical_surface(node_geometries[node_id], orient, precision)
        junctions.append({
            "node_id": node_id,
            "policy_candidate": next(iter(policies)),
            "incident_endpoints": [{"atom_id": row["atom_id"], "endpoint_index": row["endpoint_index"]} for row in rows],
            "inference": {
                "source": "wall-endpoint-policy-inventory-v1",
                "rule": {
                    "face_abutment_candidate": "terminate_strip_at_recorded_wall_face_contact",
                    "multiway_junction_candidate": "union_incident_half_thickness_strip_ends_at_recorded_axis",
                    "free_end": "square_cap_extending_one_half_thickness",
                }[next(iter(policies))],
                "opening_subtraction": "none",
                "source_geometry_promotion": False,
            },
            "solid_m": surface,
            "geometry_hash": _hash(surface),
            "area_m2": _round(node_geometries[node_id].area, precision),
        })
    sensitivity = []
    for name, face_delta in (("face_abutment_inward_1mm", -delta), ("endpoint_policy_exact", 0.0), ("face_abutment_outward_1mm", delta)):
        union, _, _ = _derive_wall_geometry(document, inventory, face_delta)
        topology = _topology(document, union, precision, min_area)
        sensitivity.append({
            "scenario": name,
            "face_abutment_extension_m": face_delta,
            "topology": topology,
            "delta_from_exact": None if face_delta == 0 else _comparison(topology, exact_topology),
        })
    wall_surface = _canonical_surface(exact_union, orient, precision)
    return {
        "atom_solids": atoms,
        "junction_solids": junctions,
        "wall_union": {
            "solid_m": wall_surface,
            "geometry_hash": _hash(wall_surface),
            "area_m2": _round(exact_union.area, precision),
            "component_count": len(_polygon_parts(exact_union)),
        },
        "room_topology": {
            "junction_policy_v2": exact_topology,
            "room_polygon_v1": legacy_topology,
            "room_polygon_v1_candidate_hash": legacy_hash,
            "comparison": _comparison(exact_topology, legacy_topology),
        },
        "sensitivity": sensitivity,
    }


def build_junction_wall_solid_candidate(
    document: Mapping[str, Any],
    endpoint_policy: Mapping[str, Any] | None = None,
    *,
    coordinate_precision: int = 9,
    min_face_area_m2: float = 0.05,
    sensitivity_delta_m: float = 0.001,
) -> dict[str, Any]:
    doc = validate_v21_document(document)
    precision, min_area, delta = _parameters(coordinate_precision, min_face_area_m2, sensitivity_delta_m)
    policy = build_endpoint_policy_inventory(doc) if endpoint_policy is None else validate_endpoint_policy_inventory(doc, endpoint_policy)
    if any(row["policy_candidate"] == "unresolved" or not row["metadata_geometry_valid"] for row in policy["records"]):
        raise ValueError("endpoint policy inventory contains unresolved or invalid endpoint geometry")
    derived = _derive(doc, policy, precision, min_area, delta)
    junction_policy_counts: dict[str, int] = {}
    for row in derived["junction_solids"]:
        key = row["policy_candidate"]
        junction_policy_counts[key] = junction_policy_counts.get(key, 0) + 1
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "provenance": {
            "endpoint_policy_schema": policy["schema"],
            "endpoint_policy_baseline_commit": ENDPOINT_POLICY_BASELINE_COMMIT,
            "endpoint_policy_candidate_hash": policy["candidate_hash"],
            "endpoint_policy_source_snapshot_hash": policy["source_snapshot_hash"],
            "room_polygon_v1_schema": room_polygon_v1.SCHEMA,
            "document_mutated": False,
            "scoring_mutated": False,
            "build_mutated": False,
        },
        "method": {
            "name": "per_junction_endpoint_policy_wall_solids",
            "atom_cross_section": "recorded_thickness_m",
            "face_abutment": "flat_at_contact",
            "multiway": "union_at_axis",
            "free_end": "square_half_thickness_cap",
            "opening_cut_policy": "none",
            "comparison_baseline": "goal-loop-v2-room-polygon-candidate-v1",
        },
        "parameters": {
            "coordinate_precision": precision,
            "min_face_area_m2": min_area,
            "sensitivity_delta_m": delta,
        },
        "coverage": {
            "atom_count": policy["coverage"]["atom_count"],
            "junction_count": policy["coverage"]["junction_count"],
            "endpoint_count": policy["coverage"]["endpoint_count"],
            "endpoint_policy_counts": deepcopy(policy["coverage"]["policy_counts"]),
            "junction_policy_counts": {key: junction_policy_counts[key] for key in sorted(junction_policy_counts)},
            "atom_solid_count": len(derived["atom_solids"]),
            "junction_solid_count": len(derived["junction_solids"]),
        },
        **derived,
        "limitations": deepcopy(LIMITATIONS),
        "status": "pending_independent_review",
        "wall_solid_confirmation": False,
        "room_topology_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return validate_junction_wall_solid_candidate(doc, result, policy)


def validate_junction_wall_solid_candidate(
    document: Mapping[str, Any], candidate: Mapping[str, Any], endpoint_policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    doc = validate_v21_document(document)
    policy = build_endpoint_policy_inventory(doc) if endpoint_policy is None else validate_endpoint_policy_inventory(doc, endpoint_policy)
    required = {
        "schema", "source_structure_hash", "provenance", "method", "parameters", "coverage", "atom_solids",
        "junction_solids", "wall_union", "room_topology", "sensitivity", "limitations", "status",
        "wall_solid_confirmation", "room_topology_confirmation", "semantic_promotion", "build_authorized", "ready",
        "candidate_hash",
    }
    if not isinstance(candidate, Mapping) or set(candidate) != required or candidate.get("schema") != SCHEMA:
        raise ValueError("junction wall-solid candidate keys/schema invalid")
    if candidate["source_structure_hash"] != doc["structure_hash"]:
        raise ValueError("junction wall-solid source drift")
    if candidate["limitations"] != LIMITATIONS or candidate["status"] != "pending_independent_review":
        raise ValueError("junction wall-solid limitations/status drift")
    for key in ("wall_solid_confirmation", "room_topology_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate[key] is not False:
            raise ValueError("junction wall-solid candidate was promoted")
    precision, min_area, delta = _parameters(
        candidate["parameters"].get("coordinate_precision"),
        candidate["parameters"].get("min_face_area_m2"),
        candidate["parameters"].get("sensitivity_delta_m"),
    )
    if set(candidate["parameters"]) != {"coordinate_precision", "min_face_area_m2", "sensitivity_delta_m"}:
        raise ValueError("junction wall-solid parameters invalid")
    derived = _derive(doc, policy, precision, min_area, delta)
    expected_provenance = {
        "endpoint_policy_schema": policy["schema"],
        "endpoint_policy_baseline_commit": ENDPOINT_POLICY_BASELINE_COMMIT,
        "endpoint_policy_candidate_hash": policy["candidate_hash"],
        "endpoint_policy_source_snapshot_hash": policy["source_snapshot_hash"],
        "room_polygon_v1_schema": room_polygon_v1.SCHEMA,
        "document_mutated": False,
        "scoring_mutated": False,
        "build_mutated": False,
    }
    expected_method = {
        "name": "per_junction_endpoint_policy_wall_solids",
        "atom_cross_section": "recorded_thickness_m",
        "face_abutment": "flat_at_contact",
        "multiway": "union_at_axis",
        "free_end": "square_half_thickness_cap",
        "opening_cut_policy": "none",
        "comparison_baseline": "goal-loop-v2-room-polygon-candidate-v1",
    }
    junction_policy_counts: dict[str, int] = {}
    for row in derived["junction_solids"]:
        key = row["policy_candidate"]
        junction_policy_counts[key] = junction_policy_counts.get(key, 0) + 1
    expected_coverage = {
        "atom_count": policy["coverage"]["atom_count"],
        "junction_count": policy["coverage"]["junction_count"],
        "endpoint_count": policy["coverage"]["endpoint_count"],
        "endpoint_policy_counts": deepcopy(policy["coverage"]["policy_counts"]),
        "junction_policy_counts": {key: junction_policy_counts[key] for key in sorted(junction_policy_counts)},
        "atom_solid_count": len(derived["atom_solids"]),
        "junction_solid_count": len(derived["junction_solids"]),
    }
    if candidate["provenance"] != expected_provenance or candidate["method"] != expected_method:
        raise ValueError("junction wall-solid inference/provenance drift")
    if candidate["coverage"] != expected_coverage:
        raise ValueError("junction wall-solid coverage drift")
    for key in ("atom_solids", "junction_solids", "wall_union", "room_topology", "sensitivity"):
        if candidate[key] != derived[key]:
            raise ValueError("junction wall-solid geometry/topology drift")
    if candidate["candidate_hash"] != _hash({key: value for key, value in candidate.items() if key != "candidate_hash"}):
        raise ValueError("junction wall-solid candidate hash drift")
    return deepcopy(dict(candidate))


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build a fail-closed per-junction wall-solid candidate")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-face-area-m2", type=float, default=0.05)
    parser.add_argument("--sensitivity-delta-m", type=float, default=0.001)
    args = parser.parse_args()
    document = json.loads(args.source.read_text(encoding="utf-8"))
    candidate = build_junction_wall_solid_candidate(
        document, min_face_area_m2=args.min_face_area_m2, sensitivity_delta_m=args.sensitivity_delta_m
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "candidate_hash": candidate["candidate_hash"],
        "coverage": candidate["coverage"],
        "topology": candidate["room_topology"],
        "sensitivity": candidate["sensitivity"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())


__all__ = ["build_junction_wall_solid_candidate", "validate_junction_wall_solid_candidate"]
