"""Fail-closed room-face candidates derived from the audited 2D wall graph.

The output is deliberately not source truth.  Wall solids are inferred by
buffering each wall-atom centreline by half its recorded thickness, then
subtracting their union from the confirmed outer-boundary polygon.  Space
anchors may label a face as a candidate, but never confirm its boundary,
semantics, adjacency, traversability, Z, or build readiness.
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


SCHEMA = "goal-loop-v2-room-polygon-candidate-v1"
LIMITATIONS = {
    "wall_solid_geometry_source_confirmed": False,
    "room_boundary_source_confirmed": False,
    "opening_cut_geometry_applied": False,
    "space_label_source_confirmed": False,
    "adjacency_confirmed": False,
    "traversability_confirmed": False,
    "z_height_confirmed": False,
    "build": False,
}


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(role: str, path: str | Path) -> dict[str, Any]:
    evidence_path = Path(path).expanduser().resolve()
    payload = evidence_path.read_bytes()
    suffix = evidence_path.suffix.lower()
    if suffix == ".json":
        value = json.loads(payload.decode("utf-8"))
        media_type = "application/json"
        canonical_sha256 = _hash(value)
    elif suffix == ".md":
        text = payload.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        media_type = "text/markdown"
        canonical_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        raise ValueError("room polygon evidence must be JSON or Markdown")
    return {
        "role": role,
        "path": str(evidence_path),
        "media_type": media_type,
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_sha256": canonical_sha256,
    }


def _require_shapely():
    try:
        from shapely.geometry import LineString, Point, Polygon
        from shapely.geometry.polygon import orient
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - depends on host environment
        raise RuntimeError("room polygon candidates require Shapely 2.x") from exc
    return LineString, Point, Polygon, orient, unary_union


def _finite_point(value: Any, path: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{path}: expected a 2D point")
    x, y = value
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in (x, y)):
        raise ValueError(f"{path}: point must contain finite numbers")
    return float(x), float(y)


def _round(value: float, precision: int) -> float:
    rounded = round(float(value), precision)
    return 0.0 if rounded == 0 else rounded


def _rotate_ring(points: list[list[float]]) -> list[list[float]]:
    if not points:
        return points
    start = min(range(len(points)), key=lambda index: tuple(points[index:]) + tuple(points[:index]))
    return points[start:] + points[:start]


def _canonical_ring(coords: Any, precision: int) -> list[list[float]]:
    points = [[_round(x, precision), _round(y, precision)] for x, y in list(coords)[:-1]]
    points = _rotate_ring(points)
    return points + [deepcopy(points[0])]


def _canonical_polygon(polygon: Any, orient: Any, precision: int) -> dict[str, Any]:
    normalized = orient(polygon, sign=1.0)
    exterior = _canonical_ring(normalized.exterior.coords, precision)
    holes = sorted(
        (_canonical_ring(interior.coords, precision) for interior in normalized.interiors),
        key=canonical_json,
    )
    return {"exterior": exterior, "holes": holes}


def _source_snapshots(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "outer_boundary": deepcopy(document["outer_boundary"]["polygon_m"]),
        "wall_atoms": [
            {
                "id": atom["id"],
                "centerline_m": deepcopy(atom["centerline_m"]),
                "thickness_m": atom["thickness_m"],
                "status": atom["status"],
            }
            for atom in sorted(document["wall_graph"]["atoms"], key=lambda row: row["id"])
        ],
        "space_anchors": [
            {"id": space["id"], "point_m": deepcopy(space["point_m"]), "status": space["status"]}
            for space in sorted(document["spaces"], key=lambda row: row["id"])
        ],
    }


def _parameters(coordinate_precision: Any, min_face_area_m2: Any) -> tuple[int, float]:
    if isinstance(coordinate_precision, bool) or not isinstance(coordinate_precision, int) or not 3 <= coordinate_precision <= 12:
        raise ValueError("coordinate_precision must be an integer from 3 to 12")
    if isinstance(min_face_area_m2, bool) or not isinstance(min_face_area_m2, (int, float)) or not 0 <= float(min_face_area_m2) <= 1.0:
        raise ValueError("min_face_area_m2 must be in [0, 1]")
    return coordinate_precision, float(min_face_area_m2)


def _derive(document: Mapping[str, Any], coordinate_precision: int, min_face_area_m2: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    LineString, Point, Polygon, orient, unary_union = _require_shapely()
    outer_points = [_finite_point(point, "outer_boundary.polygon_m") for point in document["outer_boundary"]["polygon_m"]]
    outer = Polygon(outer_points)
    if outer.is_empty or not outer.is_valid or outer.area <= 0:
        raise ValueError("outer boundary cannot produce a valid positive-area polygon")

    wall_solids = []
    for atom in document["wall_graph"]["atoms"]:
        points = [_finite_point(point, f"wall atom {atom['id']} centerline") for point in atom["centerline_m"]]
        thickness = atom["thickness_m"]
        if isinstance(thickness, bool) or not isinstance(thickness, (int, float)) or not math.isfinite(float(thickness)) or float(thickness) <= 0:
            raise ValueError(f"wall atom {atom['id']} has invalid thickness")
        line = LineString(points)
        if line.is_empty or line.length <= 0:
            raise ValueError(f"wall atom {atom['id']} has degenerate centerline")
        wall_solids.append(line.buffer(float(thickness) / 2.0, quad_segs=1, cap_style="square", join_style="mitre", mitre_limit=5.0))

    wall_union = unary_union(wall_solids)
    clipped_wall = wall_union.intersection(outer)
    free = outer.difference(clipped_wall)
    if free.is_empty:
        raise ValueError("wall-solid hypothesis removes the entire outer boundary")
    raw_faces = [free] if free.geom_type == "Polygon" else [geometry for geometry in getattr(free, "geoms", []) if geometry.geom_type == "Polygon"]
    kept = [face for face in raw_faces if face.area >= min_face_area_m2]
    discarded = [face for face in raw_faces if face.area < min_face_area_m2]

    geometries = []
    for face in kept:
        polygon_m = _canonical_polygon(face, orient, coordinate_precision)
        geometries.append((face, polygon_m, _hash(polygon_m)))
    geometries.sort(key=lambda row: (tuple(_round(value, coordinate_precision) for value in row[0].bounds), _round(row[0].area, coordinate_precision), row[2]))

    faces: list[dict[str, Any]] = []
    for geometry, polygon_m, geometry_hash in geometries:
        faces.append({
            "id": f"ROOMFACE-{geometry_hash[:12].upper()}",
            "polygon_m": polygon_m,
            "geometry_hash": geometry_hash,
            "area_m2": _round(geometry.area, coordinate_precision),
            "bounds_m": [_round(value, coordinate_precision) for value in geometry.bounds],
            "source_anchor_space_ids": [],
            "label_candidate_space_id": None,
            "anchor_assignment_status": "unlabeled_face_candidate",
            "geometry_status": "derived_candidate_only",
            "room_boundary_confirmation": False,
        })

    assignments = []
    face_by_id = {
        face_row["id"]: geometry
        for geometry, face_row in zip([row[0] for row in geometries], faces)
    }
    for space in sorted(document["spaces"], key=lambda row: row["id"]):
        point_xy = _finite_point(space.get("point_m"), f"space {space['id']} point_m")
        point = Point(point_xy)
        containing = [face_id for face_id, geometry in face_by_id.items() if geometry.contains(point)]
        covering = [face_id for face_id, geometry in face_by_id.items() if geometry.covers(point)]
        boundary = sorted(set(covering) - set(containing))
        if len(containing) == 1 and not boundary:
            relation = "inside_single_face"
            face_ids = containing
        elif boundary or len(containing) > 1:
            relation = "ambiguous_face_boundary"
            face_ids = sorted(set(containing + boundary))
        elif clipped_wall.covers(point):
            relation = "inside_inferred_wall_solid"
            face_ids = []
        elif not outer.covers(point):
            relation = "outside_outer_boundary"
            face_ids = []
        else:
            relation = "unassigned_after_face_filter"
            face_ids = []
        assignments.append({
            "space_id": space["id"],
            "point_m": [_round(point_xy[0], coordinate_precision), _round(point_xy[1], coordinate_precision)],
            "face_candidate_ids": sorted(face_ids),
            "relation": relation,
            "confirmation": False,
        })

    assignments_by_face: dict[str, list[str]] = {face["id"]: [] for face in faces}
    for assignment in assignments:
        if assignment["relation"] == "inside_single_face":
            assignments_by_face[assignment["face_candidate_ids"][0]].append(assignment["space_id"])
    for face in faces:
        anchors = sorted(assignments_by_face[face["id"]])
        face["source_anchor_space_ids"] = anchors
        if len(anchors) == 1:
            face["label_candidate_space_id"] = anchors[0]
            face["anchor_assignment_status"] = "single_anchor_face_candidate"
        elif len(anchors) > 1:
            face["anchor_assignment_status"] = "ambiguous_multiple_anchors"

    status_counts: dict[str, int] = {}
    for assignment in assignments:
        status_counts[assignment["relation"]] = status_counts.get(assignment["relation"], 0) + 1
    coverage = {
        "wall_atom_count": len(document["wall_graph"]["atoms"]),
        "space_anchor_count": len(document["spaces"]),
        "raw_face_count": len(raw_faces),
        "face_candidate_count": len(faces),
        "discarded_sliver_count": len(discarded),
        "discarded_sliver_area_m2": _round(sum(face.area for face in discarded), coordinate_precision),
        "outer_area_m2": _round(outer.area, coordinate_precision),
        "inferred_wall_solid_inside_outer_area_m2": _round(clipped_wall.area, coordinate_precision),
        "candidate_face_area_m2": _round(sum(row[0].area for row in geometries), coordinate_precision),
        "single_anchor_face_count": sum(face["anchor_assignment_status"] == "single_anchor_face_candidate" for face in faces),
        "multi_anchor_face_count": sum(face["anchor_assignment_status"] == "ambiguous_multiple_anchors" for face in faces),
        "unlabeled_face_count": sum(face["anchor_assignment_status"] == "unlabeled_face_candidate" for face in faces),
        "anchor_relation_counts": {key: status_counts[key] for key in sorted(status_counts)},
    }
    return faces, assignments, coverage


def build_room_polygon_candidate(
    document: Mapping[str, Any],
    source_document_file: str | Path | None = None,
    evidence_files: Mapping[str, str | Path] | None = None,
    *,
    coordinate_precision: int = 9,
    min_face_area_m2: float = 0.05,
) -> dict[str, Any]:
    doc = validate_v21_document(document)
    coordinate_precision, min_face_area_m2 = _parameters(coordinate_precision, min_face_area_m2)
    snapshots = _source_snapshots(doc)
    faces, assignments, coverage = _derive(doc, coordinate_precision, min_face_area_m2)
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "source_document": None if source_document_file is None else _binding("source_document", source_document_file),
        "evidence_bindings": [] if evidence_files is None else [_binding(role, path) for role, path in sorted(evidence_files.items())],
        "source_snapshot_hashes": {
            "outer_boundary_sha256": _hash(snapshots["outer_boundary"]),
            "wall_atoms_sha256": _hash(snapshots["wall_atoms"]),
            "space_anchors_sha256": _hash(snapshots["space_anchors"]),
        },
        "method": {
            "name": "outer_boundary_minus_buffered_wall_atom_centerlines",
            "wall_buffer_distance": "atom_thickness_m / 2",
            "cap_style": "square",
            "join_style": "mitre",
            "opening_cut_policy": "none",
            "face_definition": "connected_polygon_components_after_wall_solid_subtraction",
            "anchor_role": "candidate_label_only",
        },
        "parameters": {"coordinate_precision": coordinate_precision, "min_face_area_m2": min_face_area_m2},
        "coverage": coverage,
        "faces": faces,
        "anchor_assignments": assignments,
        "limitations": deepcopy(LIMITATIONS),
        "status": "pending_independent_review",
        "room_polygon_confirmation": False,
        "space_assignment_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return validate_room_polygon_candidate(doc, result)


def _validate_binding(binding: Mapping[str, Any], expected_role: str | None = None) -> None:
    required = {"role", "path", "media_type", "file_sha256", "canonical_sha256"}
    if not isinstance(binding, Mapping) or set(binding) != required:
        raise ValueError("room polygon provenance binding invalid")
    if expected_role is not None and binding["role"] != expected_role:
        raise ValueError("room polygon provenance role mismatch")
    if _binding(binding["role"], binding["path"]) != dict(binding):
        raise ValueError("room polygon provenance file drift")


def validate_room_polygon_candidate(document: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    required = {
        "schema", "source_structure_hash", "source_document", "evidence_bindings",
        "source_snapshot_hashes", "method", "parameters", "coverage", "faces",
        "anchor_assignments", "limitations", "status", "room_polygon_confirmation",
        "space_assignment_confirmation", "adjacency_confirmation", "semantic_promotion",
        "build_authorized", "ready", "candidate_hash",
    }
    if not isinstance(candidate, Mapping) or set(candidate) != required or candidate.get("schema") != SCHEMA:
        raise ValueError("room polygon candidate keys/schema invalid")
    if candidate["source_structure_hash"] != doc["structure_hash"]:
        raise ValueError("room polygon candidate source drift")
    if candidate["limitations"] != LIMITATIONS or candidate["status"] != "pending_independent_review":
        raise ValueError("room polygon candidate limitations/status invalid")
    for key in ("room_polygon_confirmation", "space_assignment_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate[key] is not False:
            raise ValueError("room polygon candidate was promoted")
    expected_method = {
        "name": "outer_boundary_minus_buffered_wall_atom_centerlines",
        "wall_buffer_distance": "atom_thickness_m / 2",
        "cap_style": "square",
        "join_style": "mitre",
        "opening_cut_policy": "none",
        "face_definition": "connected_polygon_components_after_wall_solid_subtraction",
        "anchor_role": "candidate_label_only",
    }
    if candidate["method"] != expected_method:
        raise ValueError("room polygon candidate method drift")
    precision, min_area = _parameters(candidate["parameters"].get("coordinate_precision"), candidate["parameters"].get("min_face_area_m2"))
    if set(candidate["parameters"]) != {"coordinate_precision", "min_face_area_m2"}:
        raise ValueError("room polygon candidate parameters invalid")
    if candidate["source_document"] is not None:
        _validate_binding(candidate["source_document"], "source_document")
        if candidate["source_document"]["canonical_sha256"] != _hash(doc):
            raise ValueError("room polygon source document differs from document")
    roles: set[str] = set()
    for binding in candidate["evidence_bindings"]:
        _validate_binding(binding)
        if binding["role"] in roles:
            raise ValueError("duplicate room polygon evidence role")
        roles.add(binding["role"])
    snapshots = _source_snapshots(doc)
    expected_hashes = {
        "outer_boundary_sha256": _hash(snapshots["outer_boundary"]),
        "wall_atoms_sha256": _hash(snapshots["wall_atoms"]),
        "space_anchors_sha256": _hash(snapshots["space_anchors"]),
    }
    if candidate["source_snapshot_hashes"] != expected_hashes:
        raise ValueError("room polygon source snapshot drift")
    expected_faces, expected_assignments, expected_coverage = _derive(doc, precision, min_area)
    if candidate["faces"] != expected_faces or candidate["anchor_assignments"] != expected_assignments or candidate["coverage"] != expected_coverage:
        raise ValueError("room polygon geometry or assignment drift")
    if candidate["candidate_hash"] != _hash({key: value for key, value in candidate.items() if key != "candidate_hash"}):
        raise ValueError("room polygon candidate hash drift")
    return deepcopy(dict(candidate))


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build a fail-closed room polygon candidate")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--wall-2d-fact", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-face-area-m2", type=float, default=0.05)
    args = parser.parse_args()
    document = json.loads(args.source.read_text(encoding="utf-8"))
    evidence = None if args.wall_2d_fact is None else {"authorized_wall_2d_fact": args.wall_2d_fact}
    candidate = build_room_polygon_candidate(document, args.source, evidence, min_face_area_m2=args.min_face_area_m2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(args.output.resolve()), "candidate_hash": candidate["candidate_hash"], "coverage": candidate["coverage"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())


__all__ = ["build_room_polygon_candidate", "validate_room_polygon_candidate"]
