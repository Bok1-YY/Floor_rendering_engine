"""One deterministic geometry kernel shared by browser and software rendering.

The canonical model is editable.  A locked model is compiled into this compact
triangle manifest so consumers do not implement their own wall/opening logic.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Iterable

import numpy as np
from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .whole_home_geometry import (
    build_geometry_manifest,
    geometry_entity_confirmed,
    geometry_facts_hash,
)


GEOMETRY_KERNEL_VERSION = "whole-home-geometry-kernel-v2"
GEOMETRY_MANIFEST_VERSION = 1


class GeometryKernelError(ValueError):
    pass


def model_facts_hash(model: dict) -> str:
    """Compatibility alias for the stable plan-geometry fingerprint."""
    return geometry_facts_hash(model)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


class _Builder:
    def __init__(self):
        self.vertices: list[list[float]] = []
        self.parts: list[dict] = []

    def add_triangles(self, part_id: str, entity_id: str, semantic_kind: str,
                      triangles: Iterable[Iterable[Iterable[float]]], **metadata: Any) -> None:
        indices: list[int] = []
        bounds: list[list[float]] = []
        for triangle in triangles:
            rows = [[round(_number(axis), 8) for axis in point] for point in triangle]
            if len(rows) != 3 or any(len(point) != 3 for point in rows):
                raise GeometryKernelError("triangles must contain three xyz points")
            base = len(self.vertices)
            self.vertices.extend(rows)
            indices.extend([base, base + 1, base + 2])
            bounds.extend(rows)
        if not indices:
            return
        array = np.asarray(bounds, dtype=np.float64)
        self.parts.append({
            "id": part_id,
            "entity_id": entity_id,
            "semantic_kind": semantic_kind,
            "indices": indices,
            "bounds_min": [round(float(value), 8) for value in array.min(axis=0)],
            "bounds_max": [round(float(value), 8) for value in array.max(axis=0)],
            **metadata,
        })


def _box_triangles(center: tuple[float, float, float], size: tuple[float, float, float],
                   angle_deg: float = 0.0) -> list[list[list[float]]]:
    sx, sy, sz = (max(0.0001, _number(value)) for value in size)
    cx, cy, cz = center
    vertices = np.asarray([
        [-sx / 2, -sy / 2, -sz / 2], [sx / 2, -sy / 2, -sz / 2],
        [sx / 2, sy / 2, -sz / 2], [-sx / 2, sy / 2, -sz / 2],
        [-sx / 2, -sy / 2, sz / 2], [sx / 2, -sy / 2, sz / 2],
        [sx / 2, sy / 2, sz / 2], [-sx / 2, sy / 2, sz / 2],
    ], dtype=np.float64)
    angle = math.radians(angle_deg)
    rotation = np.asarray([
        [math.cos(angle), 0.0, math.sin(angle)],
        [0.0, 1.0, 0.0],
        [-math.sin(angle), 0.0, math.cos(angle)],
    ])
    vertices = vertices @ rotation.T + np.asarray([cx, cy, cz])
    faces = (
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2),
        (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5),
    )
    return [[vertices[index].tolist() for index in face] for face in faces]


def _polygon(value: Any, interior_rings: Any = None) -> Polygon:
    points = []
    for point in value or []:
        if isinstance(point, dict):
            points.append((_number(point.get("x")), _number(point.get("z"))))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            points.append((_number(point[0]), _number(point[1])))
    holes = []
    for ring in interior_rings or []:
        coordinates = []
        for point in ring or []:
            if isinstance(point, dict):
                coordinates.append((_number(point.get("x")), _number(point.get("z"))))
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                coordinates.append((_number(point[0]), _number(point[1])))
        if len(coordinates) >= 3:
            holes.append(coordinates)
    polygon = Polygon(points, holes=holes)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area <= 1e-8:
        raise GeometryKernelError("polygon footprint is empty")
    if polygon.geom_type != "Polygon":
        polygon = max(polygon.geoms, key=lambda item: item.area)
    return polygon


def _surface_triangles(polygon: Polygon, y: float, upward: bool) -> list[list[list[float]]]:
    """Triangulate a polygon without crossing concavities or interior rings.

    ``shapely.ops.triangulate`` is an unconstrained Delaunay triangulation.  A
    centroid filter looks plausible for a concave floor but may retain a
    triangle which crosses the boundary or a hole.  That made the 3D manifest
    disagree with its own canonical floor plan.  GEOS' constrained Delaunay
    implementation preserves every polygon edge, including hole boundaries.
    """
    result = []
    collection = constrained_delaunay_triangles(polygon)
    triangles = list(collection.geoms) if hasattr(collection, "geoms") else [collection]
    retained = []
    for item in triangles:
        if item.is_empty or item.geom_type != "Polygon" or item.area <= 1e-12:
            continue
        if not polygon.covers(item):
            raise GeometryKernelError("constrained floor triangle crosses polygon boundary")
        retained.append(item)
        coordinates = list(item.exterior.coords)[:3]
        points = [[float(x), y, float(z)] for x, z in coordinates]
        result.append(points if not upward else [points[0], points[2], points[1]])
    covered = unary_union(retained) if retained else Polygon()
    if polygon.symmetric_difference(covered).area > max(1e-8, polygon.area * 1e-9):
        raise GeometryKernelError("constrained floor triangulation does not cover polygon")
    return result


def _assembly_footprint(assembly: dict) -> Polygon | None:
    """Return the same plan footprint used by the correspondence gate."""
    footprint = assembly.get("footprint_polygon")
    if footprint:
        try:
            return _polygon(footprint)
        except GeometryKernelError:
            return None
    wall = _legacy_wall_from_assembly(assembly)
    if not wall:
        return None
    start, end = wall["start"], wall["end"]
    first = (_number(start.get("x")), _number(start.get("z")))
    second = (_number(end.get("x")), _number(end.get("z")))
    thickness = max(0.0, _number(assembly.get("thickness_m")))
    if math.dist(first, second) <= 1e-8 or thickness <= 0:
        return None
    return LineString([first, second]).buffer(thickness / 2.0, cap_style="flat")


def _select_global_wall_footprints(
    assemblies: list[dict], footprints: list[tuple[dict, Polygon]],
) -> tuple[list[tuple[dict, Polygon]], dict]:
    """Use a global wall mask only when it proves correspondence to assemblies.

    A global topology result is an optimisation/repair representation, not an
    independent source of truth.  It may replace confirmed WallAssemblies only
    when both representations describe the same 2D wall area under the strict
    manifest tolerance.  Otherwise the compiler fails closed to the confirmed
    assemblies and records why the replacement was rejected.
    """
    if not footprints:
        return [], {
            "method": "global-wall-footprint-correspondence-v1",
            "decision": "not_provided",
            "candidate_count": 0,
        }
    assembly_polygons = [polygon for row in assemblies
                         if (polygon := _assembly_footprint(row)) is not None]
    if not assembly_polygons:
        return footprints, {
            "method": "global-wall-footprint-correspondence-v1",
            "decision": "selected_without_wall_assemblies",
            "candidate_count": len(footprints),
        }
    assembly_union = unary_union(assembly_polygons)
    footprint_union = unary_union([polygon for _, polygon in footprints])
    symmetric_difference = float(
        assembly_union.symmetric_difference(footprint_union).area)
    reference_area = max(float(assembly_union.area), float(footprint_union.area), 1e-12)
    difference_ratio = symmetric_difference / reference_area
    union_area = float(assembly_union.union(footprint_union).area)
    iou = (float(assembly_union.intersection(footprint_union).area) / union_area
           if union_area > 1e-12 else 1.0)
    accepted = bool(
        iou >= 0.995
        and (symmetric_difference <= 1e-4 or difference_ratio <= 0.001)
    )
    evidence = {
        "method": "global-wall-footprint-correspondence-v1",
        "decision": "selected" if accepted else "rejected_fallback_to_wall_assemblies",
        "candidate_count": len(footprints),
        "confirmed_wall_assembly_count": len(assemblies),
        "assembly_footprint_count": len(assembly_polygons),
        "iou": round(iou, 9),
        "symmetric_difference_m2": round(symmetric_difference, 9),
        "symmetric_difference_ratio": round(difference_ratio, 9),
        "limits": {
            "minimum_iou": 0.995,
            "maximum_symmetric_difference_m2": 1e-4,
            "maximum_symmetric_difference_ratio": 0.001,
            "difference_policy": "either",
        },
    }
    return (footprints if accepted else []), evidence


def _prism_triangles(polygon: Polygon, bottom: float, top: float) -> list[list[list[float]]]:
    result = _surface_triangles(polygon, bottom, False)
    result.extend(_surface_triangles(polygon, top, True))
    rings = [polygon.exterior, *polygon.interiors]
    for ring in rings:
        coordinates = list(ring.coords)
        for first, second in zip(coordinates, coordinates[1:]):
            x0, z0 = first
            x1, z1 = second
            result.extend([
                [[x0, bottom, z0], [x1, bottom, z1], [x1, top, z1]],
                [[x0, bottom, z0], [x1, top, z1], [x0, top, z0]],
            ])
    return result


def _wall_parts(wall: dict, openings: list[dict]) -> list[tuple[float, float, float, float]]:
    start, end = wall.get("start") or {}, wall.get("end") or {}
    length = math.hypot(
        _number(end.get("x")) - _number(start.get("x")),
        _number(end.get("z")) - _number(start.get("z")),
    )
    height = max(0.1, _number(wall.get("height_m"), 2.8))
    wall_targets = {
        str(value) for value in (wall.get("id"), wall.get("wall_assembly_id"))
        if str(value or "")
    }
    cuts = sorted([
        opening for opening in openings
        if wall_targets.intersection({
            str(value) for value in (
                opening.get("wall_id"), opening.get("wall_assembly_id"))
            if str(value or "")
        })
        and opening.get("review_status") != "rejected"
        and _number(opening.get("width_m")) > 0.02
    ], key=lambda opening: _number(opening.get("offset_m", opening.get("start_offset_m"))))
    result: list[tuple[float, float, float, float]] = []
    cursor = 0.0
    for opening in cuts:
        left = max(cursor, min(length, _number(
            opening.get("offset_m", opening.get("start_offset_m")))))
        right = max(left, min(length, left + _number(opening.get("width_m"))))
        if left > cursor + 0.005:
            result.append((cursor, left, 0.0, height))
        sill = max(0.0, _number(opening.get("sill_height_m")))
        opening_top = min(height, sill + max(0.05, _number(opening.get("height_m"), 2.1)))
        if sill > 0.02:
            result.append((left, right, 0.0, sill))
        if opening_top < height - 0.02:
            result.append((left, right, opening_top, height))
        cursor = max(cursor, right)
    if cursor < length - 0.005:
        result.append((cursor, length, 0.0, height))
    return result


def _legacy_wall_from_assembly(assembly: dict) -> dict | None:
    centerline = assembly.get("centerline") or []
    if isinstance(centerline, dict):
        start, end = centerline.get("start"), centerline.get("end")
    elif isinstance(centerline, list) and len(centerline) >= 2:
        start, end = centerline[0], centerline[-1]
    else:
        return None
    def point(value: Any) -> dict:
        if isinstance(value, dict):
            return {"x": _number(value.get("x")), "z": _number(value.get("z"))}
        return {"x": _number(value[0]), "z": _number(value[1])}
    return {
        "id": str(assembly.get("id") or ""),
        "wall_assembly_id": str(assembly.get("id") or ""),
        "start": point(start), "end": point(end),
        "thickness_m": _number(assembly.get("thickness_m"), 0.12),
        "height_m": _number(assembly.get("height_m"), 2.8),
        "source_representation": assembly.get("source_representation"),
    }


def compile_geometry_manifest(
    model: dict, *, registration_hash: str = "", project_id: str = "",
    model_revision: int | None = None,
) -> dict:
    """Compile a canonical model into deterministic indexed triangle parts."""
    builder = _Builder()
    input_grade = str(model.get("input_grade") or "")
    strict_contract = input_grade != "legacy_unproven" and bool(
        int(model.get("geometry_schema_version", 0) or 0) >= 3
        or input_grade in {"vector_authoritative", "raster_human_locked"}
    )
    declared_assemblies = {
        str(row.get("id") or ""): row for row in model.get("wall_assemblies") or []
        if isinstance(row, dict) and str(row.get("id") or "")
    }
    legacy_missing_review = not strict_contract and not declared_assemblies
    openings = sorted(
        [copy.deepcopy(row) for row in model.get("openings") or []
         if isinstance(row, dict) and geometry_entity_confirmed(
             row, legacy_missing=legacy_missing_review)],
        key=lambda row: str(row.get("id") or ""),
    )

    physical_spaces = [
        row for row in model.get("physical_spaces") or [] if isinstance(row, dict)
    ]
    floor_spaces = sorted(
        physical_spaces or [row for row in model.get("rooms") or [] if isinstance(row, dict)],
        key=lambda row: str(row.get("id") or ""),
    )
    floor_source_kind = "physical_space" if physical_spaces else "room"
    for room in floor_spaces:
        room_id = str(room.get("id") or "")
        try:
            polygon = _polygon(room.get("polygon"))
        except GeometryKernelError:
            continue
        floor_y = _number(room.get("floor_elevation_m"))
        builder.add_triangles(
            f"floor:{room_id}", room_id, "floor",
            _surface_triangles(polygon, floor_y, True), source_kind=floor_source_kind)
        ceiling_y = floor_y + max(
            0.1, _number(
                room.get("ceiling_height_m"),
                _number(model.get("wall_height_m"), 2.8),
            ),
        )
        builder.add_triangles(
            f"ceiling:{room_id}", room_id, "ceiling",
            _surface_triangles(polygon, ceiling_y, False),
            source_kind=floor_source_kind, render_role="wall",
        )

    assemblies = sorted(
        [row for row in model.get("wall_assemblies") or []
         if isinstance(row, dict) and geometry_entity_confirmed(
             row, legacy_missing=not strict_contract)],
        key=lambda row: str(row.get("id") or ""),
    )
    global_footprint_rows = sorted(
        [row for row in model.get("global_wall_footprints") or []
         if isinstance(row, dict) and len(row.get("points") or []) >= 3],
        key=lambda row: str(row.get("id") or ""),
    )
    candidate_global_footprints: list[tuple[dict, Polygon]] = []
    for footprint in global_footprint_rows:
        footprint_id = str(footprint.get("id") or "global-wall-footprint")
        try:
            polygon = _polygon(
                footprint.get("points"), footprint.get("interior_rings"))
        except GeometryKernelError:
            continue
        candidate_global_footprints.append((footprint, polygon))
    global_footprints, global_footprint_selection = _select_global_wall_footprints(
        assemblies, candidate_global_footprints)
    for footprint, polygon in global_footprints:
        footprint_id = str(footprint.get("id") or "global-wall-footprint")
        builder.add_triangles(
            f"wall:{footprint_id}:global-footprint",
            footprint_id,
            "wall",
            _prism_triangles(
                polygon,
                _number(footprint.get("floor_elevation_m"), 0.0),
                _number(footprint.get("floor_elevation_m"), 0.0)
                + max(0.1, _number(footprint.get("height_m"), 2.8)),
            ),
            source_kind="cad_global_topology",
            source_representation="global_wall_footprint",
        )
    use_global_footprints = bool(global_footprints)
    projected_assembly_ids: set[str] = set()
    wall_rows: list[dict] = []
    for assembly in assemblies:
        assembly_id = str(assembly.get("id") or "")
        if use_global_footprints:
            projected_assembly_ids.add(assembly_id)
            continue
        representation = str(assembly.get("source_representation") or "")
        assembly_openings = [
            row for row in openings
            if str(row.get("wall_assembly_id") or row.get("wall_id") or "") == assembly_id
            and row.get("review_status") != "rejected"
        ]
        wall = _legacy_wall_from_assembly(assembly)
        if wall and (representation != "closed_footprint" or assembly_openings):
            wall_rows.append(wall)
            projected_assembly_ids.add(assembly_id)
            continue
        footprint = assembly.get("footprint_polygon")
        if not footprint:
            if wall:
                wall_rows.append(wall)
                projected_assembly_ids.add(assembly_id)
            continue
        try:
            polygon = _polygon(footprint)
        except GeometryKernelError:
            continue
        builder.add_triangles(
            f"wall:{assembly_id}:footprint", assembly_id, "wall",
            _prism_triangles(polygon, 0.0, max(0.1, _number(assembly.get("height_m"), 2.8))),
            source_kind="wall_assembly", source_representation=representation)
        projected_assembly_ids.add(assembly_id)

    legacy = sorted(
        [row for row in model.get("walls") or [] if isinstance(row, dict)],
        key=lambda row: str(row.get("id") or ""),
    )
    for wall in legacy:
        if use_global_footprints:
            continue
        assembly_id = str(wall.get("wall_assembly_id") or "")
        if assembly_id and assembly_id in projected_assembly_ids:
            continue
        # A legacy centerline linked to a pending/rejected assembly must never
        # bypass that review decision by falling through to the old renderer.
        if assembly_id and assembly_id in declared_assemblies:
            continue
        if strict_contract and not geometry_entity_confirmed(wall):
            continue
        wall_rows.append(wall)

    wall_contract: dict[str, dict[str, float]] = {}
    wall_geometry_contract: dict[str, dict] = {}
    for assembly in assemblies:
        wall = _legacy_wall_from_assembly(assembly)
        if not wall:
            continue
        wall_geometry_contract[str(assembly.get("id") or "")] = wall
        start, end = wall["start"], wall["end"]
        wall_contract[str(assembly.get("id") or "")] = {
            "length_m": math.hypot(end["x"] - start["x"], end["z"] - start["z"]),
            "height_m": max(0.1, _number(assembly.get("height_m"), 2.8)),
        }
    for wall in wall_rows:
        start, end = wall.get("start") or {}, wall.get("end") or {}
        wall_contract[str(wall.get("id") or "")] = {
            "length_m": math.hypot(
                _number(end.get("x")) - _number(start.get("x")),
                _number(end.get("z")) - _number(start.get("z")),
            ),
            "height_m": max(0.1, _number(wall.get("height_m"), 2.8)),
        }
        if wall.get("wall_assembly_id"):
            wall_contract[str(wall["wall_assembly_id"])] = wall_contract[str(wall.get("id") or "")]
    for wall in legacy:
        wall_id = str(wall.get("id") or "")
        assembly_id = str(wall.get("wall_assembly_id") or "")
        if wall_id:
            wall_geometry_contract[wall_id] = wall
        if assembly_id and assembly_id not in wall_geometry_contract:
            wall_geometry_contract[assembly_id] = wall

    seen_opening_ids: set[str] = set()
    for opening in openings:
        opening_id = str(opening.get("id") or "").strip()
        target = str(opening.get("wall_assembly_id") or opening.get("wall_id") or "").strip()
        if not opening_id or opening_id in seen_opening_ids:
            raise GeometryKernelError("confirmed openings need non-empty unique ids")
        seen_opening_ids.add(opening_id)
        host = wall_contract.get(target)
        if host is None:
            raise GeometryKernelError(f"confirmed opening {opening_id} has no confirmed host wall")
        offset = _number(opening.get("offset_m", opening.get("start_offset_m")), -1.0)
        width = _number(opening.get("width_m"), -1.0)
        height = _number(opening.get("height_m"), -1.0)
        sill = _number(opening.get("sill_height_m"), -1.0)
        if offset < 0 or width <= 0 or height <= 0 or sill < 0:
            raise GeometryKernelError(f"confirmed opening {opening_id} has invalid dimensions")
        if offset + width > host["length_m"] + 1e-8:
            raise GeometryKernelError(f"confirmed opening {opening_id} extends beyond its host wall")
        if sill + height > host["height_m"] + 1e-8:
            raise GeometryKernelError(f"confirmed opening {opening_id} extends above its host wall")

        # The global footprint is a faithful 2D wall mask.  A source opening
        # gap in that mask must still receive its 3D sill/header structure;
        # otherwise a window becomes a floor-to-ceiling breach.  These small
        # source-hosted boxes may overlap an already-continuous footprint, which
        # is harmless, but they never fill the actual opening volume.
        if use_global_footprints:
            host_wall = wall_geometry_contract.get(target)
            if host_wall:
                start, end = host_wall.get("start") or {}, host_wall.get("end") or {}
                x0, z0 = _number(start.get("x")), _number(start.get("z"))
                x1, z1 = _number(end.get("x")), _number(end.get("z"))
                host_length = math.hypot(x1 - x0, z1 - z0)
                if host_length > .01:
                    ux, uz = (x1 - x0) / host_length, (z1 - z0) / host_length
                    center_along = offset + width / 2.0
                    angle = -math.degrees(math.atan2(uz, ux))
                    vertical_parts = []
                    if sill > .02:
                        vertical_parts.append((0.0, sill, "sill"))
                    opening_top = sill + height
                    if opening_top < host["height_m"] - .02:
                        vertical_parts.append((opening_top, host["height_m"], "header"))
                    for bottom, top, role in vertical_parts:
                        builder.add_triangles(
                            f"wall:global-opening:{opening_id}:{role}",
                            opening_id,
                            "wall",
                            _box_triangles(
                                (x0 + ux * center_along, (bottom + top) / 2.0,
                                 z0 + uz * center_along),
                                (width, top - bottom, max(
                                    .02, _number(host_wall.get("thickness_m"), .12))),
                                angle,
                            ),
                            source_kind="cad_global_topology",
                            wall_assembly_id=str(opening.get("wall_assembly_id") or ""),
                            opening_id=opening_id,
                            source_representation="global_opening_vertical_closure",
                        )

    for wall in wall_rows:
        start, end = wall.get("start") or {}, wall.get("end") or {}
        x0, z0 = _number(start.get("x")), _number(start.get("z"))
        x1, z1 = _number(end.get("x")), _number(end.get("z"))
        length = math.hypot(x1 - x0, z1 - z0)
        if length <= 0.01:
            continue
        ux, uz = (x1 - x0) / length, (z1 - z0) / length
        angle = -math.degrees(math.atan2(uz, ux))
        wall_id = str(wall.get("id") or "")
        for index, (left, right, bottom, top) in enumerate(_wall_parts(wall, openings)):
            center_along = (left + right) / 2.0
            builder.add_triangles(
                f"wall:{wall_id}:{index}", wall_id, "wall",
                _box_triangles(
                    (x0 + ux * center_along, (bottom + top) / 2.0,
                     z0 + uz * center_along),
                    (right - left, top - bottom, max(0.02, _number(wall.get("thickness_m"), 0.12))),
                    angle,
                ),
                source_kind="wall",
                wall_assembly_id=str(wall.get("wall_assembly_id") or ""),
                source_representation=str(wall.get("source_representation") or "centerline"),
            )

    objects = sorted(
        [row for row in model.get("fixed_objects") or [] if isinstance(row, dict)
         and row.get("review_status") != "rejected"],
        key=lambda row: str(row.get("id") or ""),
    )
    for item in objects:
        item_id = str(item.get("id") or "")
        position, size = item.get("position") or {}, item.get("size") or {}
        sx, sy, sz = (max(0.02, _number(size.get(axis))) for axis in ("x", "y", "z"))
        bottom = _number(position.get("y"))
        builder.add_triangles(
            f"object:{item_id}", item_id,
            str(item.get("semantic_role") or item.get("kind") or "object"),
            _box_triangles(
                (_number(position.get("x")), bottom + sy / 2.0, _number(position.get("z"))),
                (sx, sy, sz), _number(item.get("rotation_y_deg"))),
            source_kind="fixed_object")

    wall_parts = [row for row in builder.parts if row["semantic_kind"] == "wall"]
    floor_parts = [row for row in builder.parts if row["semantic_kind"] == "floor"]
    ceiling_parts = [row for row in builder.parts if row["semantic_kind"] == "ceiling"]
    object_parts = [
        row for row in builder.parts
        if row["semantic_kind"] not in {"wall", "floor", "ceiling"}
    ]
    opening_voids = [{
            "id": str(row.get("id") or ""),
            "opening_id": str(row.get("id") or ""),
            "wall_id": str(row.get("wall_id") or ""),
            "wall_assembly_id": str(row.get("wall_assembly_id") or ""),
            "kind": str(row.get("kind") or "opening"),
            "offset_m": round(_number(row.get("offset_m", row.get("start_offset_m"))), 8),
            "width_m": round(_number(row.get("width_m")), 8),
            "height_m": round(_number(row.get("height_m"), 2.1), 8),
            "sill_height_m": round(_number(row.get("sill_height_m")), 8),
            "wall_length_m": round(wall_contract[
                str(row.get("wall_assembly_id") or row.get("wall_id") or "")
            ]["length_m"], 8),
            "wall_height_m": round(wall_contract[
                str(row.get("wall_assembly_id") or row.get("wall_id") or "")
            ]["height_m"], 8),
        } for row in openings]
    return build_geometry_manifest(
        project_id=str(project_id or model.get("project_id") or "unbound"),
        model_revision=max(1, int(model_revision or model.get("model_revision") or 1)),
        model_facts_hash=model_facts_hash(model),
        registration_hash=str(
            registration_hash
            or (model.get("source_registration") or {}).get("registration_hash")
            or "unregistered"
        ),
        geometry_kernel_version=GEOMETRY_KERNEL_VERSION,
        units="meter",
        coordinate_system=str(model.get("coordinate_system") or "metres-y-up"),
        vertices=builder.vertices,
        wall_parts=wall_parts,
        floor_parts=floor_parts,
        ceiling_parts=ceiling_parts,
        opening_voids=opening_voids,
        opening_contract="owned-dimensions-v1",
        global_wall_footprint_selection=global_footprint_selection,
        object_parts=object_parts,
        # ``parts`` is a read-only compatibility index for consumers which do
        # not yet separate structure and fixed equipment.  It contains the
        # exact same indexed parts and is covered by manifest_hash.
        parts=builder.parts,
    )


def manifest_triangles(manifest: dict) -> list[dict]:
    """Expand indexed manifest triangles for the numpy renderer."""
    vertices = manifest.get("vertices") or []
    result = []
    parts = manifest.get("parts")
    if not isinstance(parts, list):
        parts = [
            *(manifest.get("wall_parts") or []),
            *(manifest.get("floor_parts") or []),
            *(manifest.get("ceiling_parts") or []),
            *(manifest.get("object_parts") or []),
        ]
    for part in parts:
        indices = part.get("indices") or []
        for index in range(0, len(indices), 3):
            try:
                points = np.asarray([
                    vertices[indices[index]], vertices[indices[index + 1]], vertices[indices[index + 2]],
                ], dtype=np.float64)
            except (IndexError, TypeError, ValueError) as ex:
                raise GeometryKernelError("manifest contains an invalid triangle index") from ex
            normal = np.cross(points[1] - points[0], points[2] - points[0])
            length = float(np.linalg.norm(normal))
            if length > 1e-12:
                normal /= length
            result.append({
                "points": points,
                "normal": normal,
                "role": str(part.get("render_role") or part.get("semantic_kind") or "other"),
                "anchor_id": str(part.get("entity_id") or ""),
            })
    return result
