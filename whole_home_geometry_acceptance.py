"""Server-side measurement for Plan-to-3D Correspondence Lock v1.

The policy thresholds live in :mod:`whole_home_geometry`.  This module owns
the measurements that feed those thresholds, so API clients cannot bless a
CAD model by posting an arbitrary ``passed`` report.  Raster-only quantities
which inherently need a human review are accepted only as an audited review
record and are still combined with server-derived registration/manifest facts.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .whole_home_geometry import (
    SourceRegistration,
    build_geometry_acceptance_report,
    geometry_entity_confirmed,
)
from .whole_home_geometry_kernel import (
    GEOMETRY_KERNEL_VERSION,
    compile_geometry_manifest,
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return float(ordered[index])


def _point2(value: Any) -> tuple[float, float]:
    if isinstance(value, Mapping):
        return (_number(value.get("x")), _number(value.get("z")))
    return (_number(value[0]), _number(value[1]))


def _polygon(value: Any) -> Polygon | None:
    try:
        points = [_point2(point) for point in value or []]
        polygon = Polygon(points)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            return None
        if polygon.geom_type != "Polygon":
            polygon = max(polygon.geoms, key=lambda row: row.area)
        return polygon if polygon.area > 1e-10 else None
    except (TypeError, ValueError, IndexError):
        return None


def _wall_polygon(row: Mapping[str, Any]) -> Polygon | None:
    footprint = _polygon(row.get("footprint_polygon"))
    if footprint is not None:
        return footprint
    start, end = row.get("start") or {}, row.get("end") or {}
    first, second = _point2(start), _point2(end)
    if math.dist(first, second) <= 1e-8:
        centerline = row.get("centerline") or []
        if len(centerline) >= 2:
            first, second = _point2(centerline[0]), _point2(centerline[-1])
    if math.dist(first, second) <= 1e-8:
        return None
    thickness = max(0.0, _number(row.get("thickness_m")))
    if thickness <= 0:
        return None
    from shapely.geometry import LineString
    return LineString([first, second]).buffer(thickness / 2, cap_style="flat")


def _union(polygons: list[Polygon]):
    return unary_union([row for row in polygons if row is not None]) if polygons else Polygon()


def _part_projection(part: Mapping[str, Any], vertices: list) -> list[Polygon]:
    indices = part.get("indices") or []
    rows: list[Polygon] = []
    for offset in range(0, len(indices), 3):
        try:
            points = [(vertices[indices[offset + index]][0], vertices[indices[offset + index]][2])
                      for index in range(3)]
        except (IndexError, TypeError):
            continue
        polygon = Polygon(points)
        if polygon.area > 1e-12:
            rows.append(polygon)
    return rows


def measure_manifest_correspondence(model: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict:
    """Compare editable 2D model facts with compiled 3D top projections."""
    vertices = manifest.get("vertices") or []
    expected_floors = _union([
        polygon for room in model.get("rooms") or []
        if isinstance(room, Mapping) and (polygon := _polygon(room.get("polygon"))) is not None
    ])
    actual_floors = _union([
        polygon
        for part in manifest.get("floor_parts") or []
        if isinstance(part, Mapping)
        for polygon in _part_projection(part, vertices)
    ])
    floor_union = expected_floors.union(actual_floors).area
    floor_iou = (expected_floors.intersection(actual_floors).area / floor_union
                 if floor_union > 1e-12 else 1.0)

    assemblies = [row for row in model.get("wall_assemblies") or []
                  if isinstance(row, Mapping) and geometry_entity_confirmed(row)]
    expected_wall_rows = assemblies or [
        row for row in model.get("walls") or [] if isinstance(row, Mapping)
    ]
    expected_walls = _union([
        polygon for row in expected_wall_rows
        if (polygon := _wall_polygon(row)) is not None
    ])
    actual_walls = _union([
        polygon
        for part in manifest.get("wall_parts") or []
        if isinstance(part, Mapping)
        for polygon in _part_projection(part, vertices)
    ])
    symmetric_difference = expected_walls.symmetric_difference(actual_walls).area
    wall_area = max(expected_walls.area, actual_walls.area, 1e-12)
    projection_union = expected_walls.union(actual_walls).union(
        expected_floors.union(actual_floors))
    projection_intersection = expected_walls.union(expected_floors).intersection(
        actual_walls.union(actual_floors))

    strict = bool(int(model.get("geometry_schema_version", 0) or 0) >= 3
                  or model.get("input_grade") in {"vector_authoritative", "raster_human_locked"})
    model_openings = {
        str(row.get("id") or ""): row for row in model.get("openings") or []
        if isinstance(row, Mapping) and geometry_entity_confirmed(row, legacy_missing=not strict)
    }
    manifest_openings = {
        str(row.get("opening_id") or row.get("id") or ""): row
        for row in manifest.get("opening_voids") or [] if isinstance(row, Mapping)
    }
    opening_errors: list[float] = []
    for opening_id in set(model_openings) | set(manifest_openings):
        source, compiled = model_openings.get(opening_id), manifest_openings.get(opening_id)
        if not source or not compiled:
            opening_errors.append(float("inf"))
            continue
        for field, alias in (("offset_m", "start_offset_m"), ("width_m", "width_m"),
                             ("height_m", "height_m"), ("sill_height_m", "sill_height_m")):
            opening_errors.append(abs(
                _number(source.get(field, source.get(alias))) - _number(compiled.get(field))))
    valid_wall_ids = {
        str(row.get("id") or "") for row in assemblies
    }
    if not model.get("wall_assemblies"):
        valid_wall_ids |= {
            str(row.get("id") or "") for row in model.get("walls") or []
            if isinstance(row, Mapping) and geometry_entity_confirmed(row, legacy_missing=not strict)
        }
    orphan_count = sum(
        1 for row in manifest_openings.values()
        if str(row.get("wall_assembly_id") or row.get("wall_id") or "") not in valid_wall_ids
    )
    return {
        "floor_footprint_iou": round(float(floor_iou), 9),
        "wall_footprint_symmetric_difference_m2": round(float(symmetric_difference), 9),
        "wall_footprint_symmetric_difference_ratio": round(float(symmetric_difference / wall_area), 9),
        "opening_interval_error_m": (
            round(max(opening_errors), 9) if opening_errors and all(math.isfinite(v) for v in opening_errors)
            else (0.0 if not opening_errors else 1e9)
        ),
        "projection_iou": round(float(
            projection_intersection.area / projection_union.area
            if projection_union.area > 1e-12 else 1.0), 9),
        "orphan_manifest_opening_count": orphan_count,
    }


def _source_handles(row: Mapping[str, Any]) -> set[str]:
    direct = {str(value) for value in row.get("source_entity_handles") or [] if str(value)}
    provenance = row.get("cad_provenance") if isinstance(row.get("cad_provenance"), Mapping) else {}
    for field in ("handle", "root_handle", "source_handle"):
        if provenance.get(field):
            direct.add(str(provenance[field]))
    return direct


def _cad_boundary_errors(model: Mapping[str, Any]) -> list[float]:
    inverse = model.get("model_to_cad") if isinstance(model.get("model_to_cad"), Mapping) else {}
    inverse_x, inverse_z = _number(inverse.get("x")), _number(inverse.get("z"))
    inverse_x_scale = _number(inverse.get("x_scale"), 1.0) or 1.0
    inverse_z_scale = _number(inverse.get("z_scale"), 1.0) or 1.0
    result: list[float] = []
    for wall in model.get("walls") or []:
        provenance = wall.get("cad_provenance") if isinstance(wall.get("cad_provenance"), Mapping) else {}
        source = provenance.get("source_segment_m") or []
        if len(source) != 2:
            continue
        actual = [
            (_number((wall.get(field) or {}).get("x")) * inverse_x_scale + inverse_x,
             _number((wall.get(field) or {}).get("z")) * inverse_z_scale + inverse_z)
            for field in ("start", "end")
        ]
        expected = [_point2(point) for point in source]
        direct = [math.dist(actual[index], expected[index]) for index in range(2)]
        reverse = [math.dist(actual[index], expected[1 - index]) for index in range(2)]
        result.extend(direct if max(direct) <= max(reverse) else reverse)
    return result


def _cad_room_area_errors(model: Mapping[str, Any]) -> list[float]:
    result: list[float] = []
    rooms = model.get("physical_spaces") or model.get("rooms") or []
    for room in rooms:
        provenance = room.get("cad_provenance") if isinstance(room.get("cad_provenance"), Mapping) else {}
        expected, actual = _polygon(provenance.get("source_polygon_m")), _polygon(room.get("polygon"))
        if expected is None or actual is None:
            continue
        result.append(abs(actual.area - expected.area) / max(expected.area, 1e-12))
    return result


def measure_cad_correspondence(project: Mapping[str, Any]) -> dict:
    """Build CAD metrics only from saved parser/model evidence."""
    model = project.get("model") if isinstance(project.get("model"), Mapping) else {}
    report = project.get("parse_report") if isinstance(project.get("parse_report"), Mapping) else {}
    alignment = report.get("alignment_metrics") if isinstance(report.get("alignment_metrics"), Mapping) else {}
    collections = [model.get("walls") or [], model.get("openings") or [],
                   model.get("physical_spaces") or model.get("rooms") or []]
    eligible = [row for rows in collections for row in rows if isinstance(row, Mapping)]
    provenance_coverage = (
        sum(bool(_source_handles(row)) for row in eligible) / len(eligible) if eligible else 0.0)

    formal_handles = set().union(*[_source_handles(row) for row in model.get("walls") or []])
    assemblies = [row for row in model.get("wall_assemblies") or [] if isinstance(row, Mapping)]
    resolved = [
        row for row in assemblies
        if geometry_entity_confirmed(row)
        and row.get("footprint_polygon") and row.get("centerline")
        and _number(row.get("thickness_m")) > 0
    ]
    rejected = [
        row for row in assemblies
        if str(row.get("review_status") or "").lower() in {"rejected", "reject"}
    ]
    resolved_dispositions = resolved + rejected
    covered_handles = (set().union(*[_source_handles(row) for row in resolved_dispositions])
                       if resolved_dispositions else set())
    assembly_coverage = (len(formal_handles & covered_handles) / len(formal_handles)
                         if formal_handles else (1.0 if resolved else 0.0))
    unresolved_count = sum(
        str(row.get("review_status") or "").lower() not in {"rejected", "reject"}
        and (
            not geometry_entity_confirmed(row)
            or not row.get("footprint_polygon") or not row.get("centerline")
            or _number(row.get("thickness_m")) <= 0
        )
        for row in assemblies
    )
    if not assemblies:
        unresolved_count = len(model.get("walls") or [])

    boundary_errors = _cad_boundary_errors(model)
    room_area_errors = _cad_room_area_errors(model)
    wall_ids = {str(row.get("id") or "") for row in model.get("walls") or []}
    wall_ids |= {str(row.get("id") or "") for row in assemblies}
    orphans = 0
    outside = 0
    intervals: dict[str, list[tuple[float, float]]] = defaultdict(list)
    wall_lengths = {}
    for row in model.get("walls") or []:
        wall_lengths[str(row.get("id") or "")] = math.dist(
            _point2(row.get("start") or {}), _point2(row.get("end") or {}))
    for row in assemblies:
        centerline = row.get("centerline") or row.get("opening_axis") or []
        if len(centerline) >= 2:
            wall_lengths[str(row.get("id") or "")] = math.dist(
                _point2(centerline[0]), _point2(centerline[-1]))
    confirmed_openings = [
        opening for opening in model.get("openings") or []
        if isinstance(opening, Mapping) and geometry_entity_confirmed(opening)
    ]
    for opening in confirmed_openings:
        wall_id = str(opening.get("wall_assembly_id") or opening.get("wall_id") or "")
        if wall_id not in wall_ids:
            orphans += 1
            continue
        start = _number(opening.get("start_offset_m", opening.get("offset_m")))
        end = start + _number(opening.get("width_m"))
        if start < -1e-9 or end > wall_lengths.get(wall_id, 0.0) + 1e-9:
            outside += 1
        intervals[wall_id].append((start, end))
    overlaps = 0
    for rows in intervals.values():
        rows.sort()
        overlaps += sum(rows[index][0] < rows[index - 1][1] - 1e-9
                        for index in range(1, len(rows)))

    endpoint_errors = alignment.get("opening_endpoint_errors") or []
    width_errors = alignment.get("opening_width_errors") or []
    opening_error = 0.0 if not endpoint_errors and not width_errors else 1e9
    return {
        "provenance_coverage": round(provenance_coverage, 9),
        "wall_assembly_coverage": round(assembly_coverage, 9),
        "boundary_p95_m": round(_percentile(boundary_errors, .95), 9),
        "boundary_max_m": round(max(boundary_errors, default=0.0), 9),
        "max_room_area_relative_error": round(max(room_area_errors, default=0.0), 9),
        "room_coverage": _number(alignment.get("room_coverage")),
        "room_overlap_area_m2": _number(alignment.get("room_overlap_area_m2")),
        "outer_max_gap_m": 0.0 if alignment.get("outer_wall_closed") is True else 1e9,
        "opening_eligible_count": len(confirmed_openings),
        "opening_center_width_p95_m": opening_error,
        "orphan_opening_count": orphans,
        "outside_opening_count": outside,
        "overlapping_opening_count": overlaps,
        "unresolved_wall_count": unresolved_count,
        "unresolved_opening_count": sum(
            isinstance(opening, Mapping)
            and str(opening.get("review_status") or "").lower() not in {"rejected", "reject"}
            and not geometry_entity_confirmed(opening)
            for opening in model.get("openings") or []
        ),
    }


RASTER_REVIEW_METRICS = frozenset({
    "wall_centerline_p95_m", "room_iou", "opening_precision", "opening_recall",
    "human_review_completion", "unresolved_review_count",
})


_CAD_UNIT_NAMES = {4: "mm", 5: "cm", 6: "m"}


def build_cad_source_registration(
    *, source_hash: str, parse_report: Mapping[str, Any], model: Mapping[str, Any],
) -> dict:
    """Create CAD SourceRegistration v2 with an explicit right-handed 3D basis."""
    unit_code = int(
        parse_report.get("resolved_insunits")
        or (model.get("scale") or {}).get("unit_code")
        or parse_report.get("insunits")
        or 0)
    cad_units = _CAD_UNIT_NAMES.get(unit_code, "")
    scale = _number(parse_report.get("unit_scale_to_m"),
                    _number((model.get("scale") or {}).get("metres_per_unit")))
    if not cad_units or scale <= 0:
        raise ValueError("Plan-to-3D v1 requires confirmed CAD units in mm, cm or m")
    cad_to_model = model.get("cad_to_model") if isinstance(model.get("cad_to_model"), Mapping) else {}
    unit_resolution = parse_report.get("unit_resolution")
    if not isinstance(unit_resolution, Mapping):
        unit_resolution = (model.get("scale") or {}).get("unit_resolution") or {}
    unit_method = str(unit_resolution.get("method") or "$INSUNITS")
    if int(cad_to_model.get("schema_version", 0) or 0) < 2:
        raise ValueError("Plan-to-3D requires CAD coordinate contract v2; reparse the source")
    translate_x = _number(cad_to_model.get("x"))
    translate_z = _number(cad_to_model.get("z"))
    return SourceRegistration({
        "version": 2, "source_type": "cad",
        "input_grade": "vector_authoritative", "source_hash": str(source_hash),
        "cad_units": cad_units,
        "source_to_canonical": [[scale, 0.0, 0.0], [0.0, scale, 0.0], [0.0, 0.0, 1.0]],
        # Canonical coordinates are [cad_x_m, cad_y_m, elevation_m].
        # The basis has positive determinant: CAD north becomes model -Z,
        # while elevation becomes model +Y.  This removes the old 2D mirror.
        "canonical_xyz_to_model": [
            [1.0, 0.0, 0.0, translate_x],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, -1.0, 0.0, translate_z],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "axis_mapping": {
            "cad_x": "+model_x", "cad_y": "-model_z", "elevation": "+model_y",
        },
        "measured_roundtrip_error": 0.0,
        "registration_method": f"{unit_method}_then_cad_xy_to_right_handed_model_xyz_v2",
    }).to_dict()


def measure_raster_correspondence(
    registration: Mapping[str, Any], reviewed_metrics: Mapping[str, Any] | None,
    server_metrics: Mapping[str, Any] | None = None,
) -> dict:
    """Merge server-derived registration facts with audited raster review facts."""
    metrics = {
        "scale_anchor_count": int(registration.get("scale_anchor_count") or 0),
        "scale_disagreement": _number(registration.get("scale_disagreement")),
        "registration_roundtrip_px": _number(registration.get("roundtrip_error"), 1e9),
    }
    reviewed_metrics = reviewed_metrics if isinstance(reviewed_metrics, Mapping) else {}
    for field in RASTER_REVIEW_METRICS:
        if field in reviewed_metrics:
            metrics[field] = _number(reviewed_metrics.get(field), 1e9)
    # Pixel alignment is recomputed by the server when the source registration
    # is saved.  Client review may complete room/opening checks but cannot
    # replace this measured wall value with a self-reported number.
    server_metrics = server_metrics if isinstance(server_metrics, Mapping) else {}
    if "wall_centerline_p95_m" in server_metrics:
        metrics["wall_centerline_p95_m"] = _number(
            server_metrics.get("wall_centerline_p95_m"), 1e9)
    return metrics


def build_project_geometry_acceptance(
    project: Mapping[str, Any], *, raster_review: Mapping[str, Any] | None = None,
    reviewer: str = "", review_note: str = "", assumptions_confirmed: bool = False,
) -> tuple[dict, dict, dict]:
    """Compile the manifest, measure correspondence and build a report."""
    model = copy.deepcopy(project.get("model") or {})
    registration = copy.deepcopy(
        project.get("source_registration") or model.get("source_registration") or {})
    revision = max(1, int(project.get("revision") or 1))
    manifest = compile_geometry_manifest(
        model, project_id=str(project.get("project_id") or ""),
        model_revision=revision,
        registration_hash=str(registration.get("registration_hash") or ""),
    )
    input_grade = str(project.get("input_grade") or registration.get("input_grade") or "legacy_unproven")
    source_type = str(project.get("source_type") or registration.get("source_type") or "")
    if input_grade.startswith("raster_"):
        source_metrics = measure_raster_correspondence(
            registration, raster_review,
            server_metrics=project.get("raster_alignment_metrics")
            if isinstance(project.get("raster_alignment_metrics"), Mapping) else None,
        )
        source_key = "raster"
    else:
        source_metrics = measure_cad_correspondence(project)
        source_key = "cad"
    metrics = {
        source_key: source_metrics,
        "manifest": measure_manifest_correspondence(model, manifest),
    }
    human_review = {
        "required": input_grade.startswith("raster_") or source_type == "cad",
        "completed": bool(reviewer and review_note),
        "reviewer": str(reviewer), "note": str(review_note),
        "engineering_assumptions_required": True,
        "assumptions_confirmed": bool(assumptions_confirmed),
    }
    report = build_geometry_acceptance_report(
        project_id=str(project.get("project_id") or ""),
        source_type=source_type, input_grade=input_grade,
        source_hash=str(registration.get("source_hash") or ""),
        model_revision=revision, model_facts_hash=manifest["model_facts_hash"],
        registration_hash=manifest["registration_hash"],
        cad_facts_hash=str((project.get("cad_import") or {}).get("cad_facts_hash") or ""),
        geometry_kernel_version=GEOMETRY_KERNEL_VERSION,
        manifest_hash=manifest["manifest_hash"], metrics=metrics,
        human_review=human_review,
    )
    return manifest, report, metrics


__all__ = [
    "RASTER_REVIEW_METRICS", "measure_manifest_correspondence",
    "measure_cad_correspondence", "measure_raster_correspondence",
    "build_cad_source_registration", "build_project_geometry_acceptance",
]
