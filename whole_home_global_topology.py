# -*- coding: utf-8 -*-
"""Deterministic whole-plan wall topology reconstructed from CAD wall faces.

The local WallAssembly pass remains valuable evidence for measured thicknesses
and openings, but it is intentionally segment-oriented.  This module builds a
single plan-wide wall footprint from the same source-backed rows so the draft
renderer does not mistake unresolved face fragments for the building shell.

Two geometries are kept deliberately separate:

* ``wall_footprints`` use a small, measured closing radius and are rendered;
* ``space_polygons`` use the median observed opening width to close door/window
  gaps for room discovery only and are never rendered as wall thickness.
"""
from __future__ import annotations

import copy
import math
import statistics
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


GLOBAL_TOPOLOGY_VERSION = "cad-global-wall-topology-v1"
SOURCE_INK_HALF_WIDTH_M = 0.01
MIN_SOURCE_SEGMENT_M = 0.005


class GlobalTopologyError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = copy.deepcopy(details or {})


def _finite_point(value: Any) -> tuple[float, float] | None:
    try:
        if isinstance(value, Mapping):
            point = float(value["x"]), float(value.get("z", value.get("y")))
        else:
            point = float(value[0]), float(value[1])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return point if all(math.isfinite(axis) for axis in point) else None


def _row_segments(rows: Sequence[Mapping[str, Any]]) -> tuple[list[LineString], list[dict]]:
    lines: list[LineString] = []
    evidence: list[dict] = []
    seen: set[tuple[tuple[float, float], tuple[float, float], str, int]] = set()
    for row_index, row in enumerate(rows):
        points = [_finite_point(value) for value in (row.get("points") or [])]
        points = [point for point in points if point is not None]
        provenance = row.get("cad_provenance") if isinstance(
            row.get("cad_provenance"), Mapping) else {}
        source_handle = str(
            provenance.get("source_handle") or provenance.get("handle")
            or provenance.get("root_handle") or f"row-{row_index}")
        root_handle = str(provenance.get("root_handle") or source_handle)
        for segment_index, (first, second) in enumerate(zip(points, points[1:])):
            if math.dist(first, second) < MIN_SOURCE_SEGMENT_M:
                continue
            rounded_first = tuple(round(axis, 8) for axis in first)
            rounded_second = tuple(round(axis, 8) for axis in second)
            undirected = tuple(sorted((rounded_first, rounded_second)))
            key = (undirected[0], undirected[1], source_handle, segment_index)
            if key in seen:
                continue
            seen.add(key)
            lines.append(LineString([first, second]))
            evidence.append({
                "source_handle": source_handle,
                "root_handle": root_handle,
                "segment_index": segment_index,
                "source_segment_m": [list(rounded_first), list(rounded_second)],
            })
    return lines, evidence


def _quantile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = min(1.0, max(0.0, fraction)) * (len(ordered) - 1)
    left = int(math.floor(position))
    right = int(math.ceil(position))
    if left == right:
        return ordered[left]
    weight = position - left
    return ordered[left] * (1.0 - weight) + ordered[right] * weight


def _polygons(value: Any, *, min_area: float = 0.001) -> list[Polygon]:
    if value is None or getattr(value, "is_empty", True):
        return []
    candidates = list(value.geoms) if value.geom_type in {"MultiPolygon", "GeometryCollection"} else [value]
    result: list[Polygon] = []
    for candidate in candidates:
        if candidate.geom_type != "Polygon" or float(candidate.area) < min_area:
            continue
        if not candidate.is_valid:
            candidate = candidate.buffer(0)
        if candidate.geom_type == "Polygon" and not candidate.is_empty and candidate.area >= min_area:
            result.append(candidate)
        elif candidate.geom_type == "MultiPolygon":
            result.extend(part for part in candidate.geoms if part.area >= min_area)
    return sorted(result, key=lambda polygon: (-float(polygon.area), tuple(polygon.bounds)))


def _minimum_rotated_rectangle_side(value: Polygon) -> float:
    """Return the shorter side of a polygon's deterministic oriented bounds."""
    rectangle = value.minimum_rotated_rectangle
    if rectangle.is_empty or rectangle.geom_type != "Polygon":
        return 0.0
    coordinates = list(rectangle.exterior.coords)
    lengths = [
        math.dist(first, second)
        for first, second in zip(coordinates, coordinates[1:])
        if math.dist(first, second) > 1e-9
    ]
    return min(lengths) if lengths else 0.0


def _model_ring(ring: Any, origin_x: float, origin_z: float) -> list[dict]:
    result: list[dict] = []
    for x, z in list(ring.coords)[:-1]:
        # Five decimal places can collapse a narrow notch into an interior-ring
        # touch after a far-origin CAD translation.  Eight decimals retain the
        # parser's metric precision while remaining deterministic and keep the
        # persisted footprint topologically equivalent to the in-process mask.
        point = {"x": round(float(x) - origin_x, 8),
                 "z": round(float(z) - origin_z, 8)}
        if not result or result[-1] != point:
            result.append(point)
    return result


def _closing(value: Any, radius: float) -> Any:
    # Mitre joins keep orthogonal architectural corners deterministic and do
    # not introduce hundreds of arc vertices into the geometry manifest.
    return value.buffer(radius, join_style=2).buffer(-radius, join_style=2).buffer(0)


def build_global_wall_topology(
    rows: Sequence[Mapping[str, Any]], *,
    wall_assemblies: Sequence[Mapping[str, Any]] | None = None,
    opening_candidates: Sequence[Mapping[str, Any]] | None = None,
    semantic_anchors: Sequence[Mapping[str, Any]] | None = None,
    enable_semantic_residual_supplements: bool = True,
    origin_x: float = 0.0,
    origin_z: float = 0.0,
    wall_height_m: float = 2.8,
) -> dict:
    """Build a source-backed whole-plan footprint plus room-discovery faces."""
    lines, evidence = _row_segments(rows)
    if not lines:
        raise GlobalTopologyError(
            "cad_global_topology_source_empty",
            "全局拓扑没有可用的 CAD 墙体线段",
        )
    network = unary_union(lines)
    source_length = float(sum(line.length for line in lines))

    accepted_thicknesses: list[float] = []
    for assembly in wall_assemblies or []:
        if str(assembly.get("review_status") or "") not in {"accepted", "confirmed"}:
            continue
        try:
            thickness = float(assembly.get("thickness_m"))
        except (TypeError, ValueError):
            continue
        if 0.06 <= thickness <= 0.60 and math.isfinite(thickness):
            accepted_thicknesses.append(thickness)

    # The 75th percentile captures the common exterior/interior face spacing
    # without letting rare 0.6m shafts turn into the global closing scale.
    measured_spacing = _quantile(accepted_thicknesses, 0.75) or 0.24
    wall_close_radius = min(0.16, max(0.08, measured_spacing / 2.0))
    thin_measurements = [value for value in accepted_thicknesses if value <= 0.20]
    inferred_single_width = min(0.15, max(
        0.08, statistics.median(thin_measurements) if thin_measurements else 0.10))

    source_ink = network.buffer(
        SOURCE_INK_HALF_WIDTH_M, cap_style=2, join_style=2)
    measured_wall = _closing(source_ink, wall_close_radius)

    # Closing a double-line corridor creates a thick core.  Source runs that
    # still have no nearby core are genuine single-line structural evidence;
    # promote only their uncovered portions using a locally measured thin-wall
    # width.  This avoids both duplicate parallel walls and 20mm-high slivers.
    core = measured_wall.buffer(-max(0.02, inferred_single_width / 4.0))
    core_support = core.buffer(max(0.03, inferred_single_width * 0.35))
    unsupported: list[Any] = []
    for line in lines:
        remainder = line.difference(core_support)
        if not remainder.is_empty and float(remainder.length) >= 0.05:
            unsupported.append(remainder)
    promoted = [value.buffer(
        inferred_single_width / 2.0, cap_style=2, join_style=2)
        for value in unsupported]
    wall_geometry = unary_union([measured_wall, *promoted]).buffer(0)
    wall_polygons = _polygons(wall_geometry)
    if not wall_polygons:
        raise GlobalTopologyError(
            "cad_global_wall_footprint_empty",
            "全局拓扑未能形成可渲染墙体 footprint",
        )

    opening_widths: list[float] = []
    opening_barrier_lines: list[LineString] = []
    opening_barrier_evidence: list[dict] = []
    for candidate in opening_candidates or []:
        # A review candidate has not proved a unique wall host and must never
        # alter physical room topology.  Only canonical-bound openings can
        # contribute a topology barrier or influence the closing radius.
        if str(candidate.get("status") or "").lower() not in {"accepted", "confirmed"}:
            continue
        try:
            width = float(candidate.get("width_m"))
        except (TypeError, ValueError):
            continue
        axis = candidate.get("axis_segment_cad_m")
        if not isinstance(axis, Sequence) or len(axis) != 2:
            continue
        first, second = _finite_point(axis[0]), _finite_point(axis[1])
        if first is None or second is None:
            continue
        axis_length = math.dist(first, second)
        source_handles = [
            str(value) for value in candidate.get("source_handles") or []
            if str(value)
        ]
        # A topology-only barrier still needs source-backed geometry.  It must
        # be the observed opening span (not the drawn open leaf), agree with the
        # measured width, and carry at least one CAD source handle.  It never
        # enters wall_geometry or the rendered GeometryManifest.
        if (not source_handles or not (0.35 <= axis_length <= 2.50)
                or abs(axis_length - width) > max(.16, width * .20)):
            continue
        opening_widths.append(width)
        opening_barrier_lines.append(LineString([first, second]))
        opening_barrier_evidence.append({
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "kind": str(candidate.get("kind") or "opening"),
            "status": str(candidate.get("status") or "review"),
            "source_handles": source_handles,
            "axis_segment_cad_m": [list(first), list(second)],
            "width_m": round(width, 6),
            "axis_length_m": round(axis_length, 6),
        })
    # Coarse closing recovers rooms whose doors have no separately classified
    # leaf/threshold evidence.  It is intentionally capped, but a 450--550 mm
    # radius can erase a genuine narrow store whose clear width is close to
    # twice that radius.  Run a second measured-wall-spacing close and retain
    # only fine-scale faces that are almost entirely absent from the coarse
    # face set.  A large open-plan fine face overlaps the coarse rooms and is
    # rejected; an independently enclosed narrow room survives and is added.
    topology_close_radius = min(0.55, max(
        0.45, (statistics.median(opening_widths) / 2.0 + 0.02)
        if opening_widths else 0.45))
    fine_topology_close_radius = min(0.30, max(
        0.16, measured_spacing / 2.0 + 0.04))
    topology_source_ink = source_ink
    if opening_barrier_lines:
        topology_source_ink = unary_union([
            source_ink,
            *[line.buffer(SOURCE_INK_HALF_WIDTH_M, cap_style=2, join_style=2)
              for line in opening_barrier_lines],
        ]).buffer(0)
    def coarse_spaces_at_radius(radius: float) -> list[Polygon]:
        spaces: list[Polygon] = []
        for barrier in _polygons(_closing(topology_source_ink, radius)):
            for ring in barrier.interiors:
                candidate = Polygon(ring)
                if candidate.is_valid and candidate.area >= 0.50:
                    spaces.append(candidate)
        return spaces

    # A furnished plan may express an internal door only as a leaf/frame
    # block whose canonical opening host is still under review.  The default
    # 450--550 mm morphology must not use that unproved candidate as a wall,
    # but it can leave two bedrooms connected through a 1.0--1.3 m throat.
    # Bed centres are independent source-backed *space markers*: two distinct
    # beds inside one coarse face prove that the face decomposition is too
    # coarse, without proving a new wall location.  Try progressively wider
    # topology-only closing radii and select the smallest one that minimises
    # marker conflicts.  The rendered wall_geometry above is never changed.
    marker_points: list[dict] = []
    for anchor in semantic_anchors or []:
        marker_kind = str(anchor.get("space_marker") or "").strip().lower()
        raw_point = anchor.get("point_m") or []
        if not marker_kind:
            continue
        try:
            point = Point(float(raw_point[0]), float(raw_point[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if any(
            existing["kind"] == marker_kind
            and float(existing["point"].distance(point)) <= .20
            for existing in marker_points
        ):
            continue
        marker_points.append({
            "anchor_id": str(anchor.get("anchor_id") or ""),
            "kind": marker_kind,
            "point": point,
        })

    radius_candidates = [topology_close_radius]
    if len(marker_points) >= 2:
        radius_candidates.extend((.55, .60, .65, .70))
    radius_candidates = sorted({
        round(float(radius), 6) for radius in radius_candidates
        if float(radius) + 1e-9 >= topology_close_radius
    })
    topology_trials: list[dict] = []
    for radius in radius_candidates:
        barrier = _closing(topology_source_ink, radius)
        spaces: list[Polygon] = []
        for barrier_polygon in _polygons(barrier):
            for ring in barrier_polygon.interiors:
                candidate = Polygon(ring)
                if candidate.is_valid and candidate.area >= 0.50:
                    spaces.append(candidate)
        marker_memberships: list[list[int]] = []
        for marker in marker_points:
            marker_memberships.append([
                index for index, space in enumerate(spaces)
                if space.buffer(.005).covers(marker["point"])
            ])
        conflict_count = 0
        for marker_kind in sorted({row["kind"] for row in marker_points}):
            for space_index in range(len(spaces)):
                count = sum(
                    marker["kind"] == marker_kind
                    and space_index in marker_memberships[index]
                    for index, marker in enumerate(marker_points)
                )
                conflict_count += max(0, count - 1)
        uncovered_count = sum(not membership for membership in marker_memberships)
        multiply_covered_count = sum(
            max(0, len(membership) - 1) for membership in marker_memberships)
        topology_trials.append({
            "radius_m": radius,
            "barrier": barrier,
            "spaces": spaces,
            "space_count": len(spaces),
            "marker_conflict_count": conflict_count,
            "uncovered_marker_count": uncovered_count,
            "multiply_covered_marker_count": multiply_covered_count,
        })
    chosen_topology_trial = min(
        topology_trials,
        key=lambda trial: (
            int(trial["marker_conflict_count"]),
            int(trial["uncovered_marker_count"]),
            int(trial["multiply_covered_marker_count"]),
            float(trial["radius_m"]),
        ),
    )
    topology_close_radius_base = topology_close_radius
    topology_close_radius = float(chosen_topology_trial["radius_m"])
    base_topology_trial = topology_trials[0]
    coarse_space_polygons = list(base_topology_trial["spaces"])
    adaptive_local_separator_count = 0
    adaptive_local_split_space_count = 0
    if topology_close_radius > topology_close_radius_base + 1e-9:
        added_barrier = chosen_topology_trial["barrier"].difference(
            base_topology_trial["barrier"]).buffer(0)
        conflicted_indexes: set[int] = set()
        for marker_kind in sorted({row["kind"] for row in marker_points}):
            for space_index, space in enumerate(coarse_space_polygons):
                contained = [
                    marker for marker in marker_points
                    if marker["kind"] == marker_kind
                    and space.buffer(.005).covers(marker["point"])
                ]
                if len(contained) > 1:
                    conflicted_indexes.add(space_index)
        locally_split_spaces: list[Polygon] = []
        for space_index, space in enumerate(coarse_space_polygons):
            if space_index not in conflicted_indexes:
                locally_split_spaces.append(space)
                continue
            original_markers = [
                marker for marker in marker_points
                if space.buffer(.005).covers(marker["point"])
            ]
            separator_candidates = _polygons(
                added_barrier.intersection(space).buffer(0), min_area=.001)
            pieces = [space]
            for separator in sorted(
                    separator_candidates, key=lambda value: float(value.area)):
                trial_geometry = unary_union(pieces).difference(separator).buffer(0)
                trial_pieces = _polygons(trial_geometry, min_area=.50)
                if len(trial_pieces) <= len(pieces):
                    continue
                before_conflicts = sum(
                    max(0, sum(
                        marker["kind"] == marker_kind
                        and piece.buffer(.005).covers(marker["point"])
                        for marker in original_markers
                    ) - 1)
                    for marker_kind in {row["kind"] for row in original_markers}
                    for piece in pieces
                )
                after_conflicts = sum(
                    max(0, sum(
                        marker["kind"] == marker_kind
                        and piece.buffer(.005).covers(marker["point"])
                        for marker in original_markers
                    ) - 1)
                    for marker_kind in {row["kind"] for row in original_markers}
                    for piece in trial_pieces
                )
                uncovered = sum(
                    not any(piece.buffer(.005).covers(marker["point"])
                            for piece in trial_pieces)
                    for marker in original_markers
                )
                if after_conflicts >= before_conflicts or uncovered:
                    continue
                pieces = trial_pieces
                adaptive_local_separator_count += 1
            if len(pieces) == 1:
                # Fail closed: a wider global close must never silently erase
                # unrelated narrow rooms merely because one marker conflict
                # could not be localised to a source-wall throat.
                locally_split_spaces.append(space)
                continue
            adaptive_local_split_space_count += len(pieces)
            locally_split_spaces.extend(pieces)
        coarse_space_polygons = locally_split_spaces
    fine_topology_barrier = _closing(
        topology_source_ink, fine_topology_close_radius)
    fine_space_polygons: list[Polygon] = []
    for barrier in _polygons(fine_topology_barrier):
        for ring in barrier.interiors:
            candidate = Polygon(ring)
            if candidate.is_valid and candidate.area >= 0.50:
                fine_space_polygons.append(candidate)
    coarse_space_union = unary_union(coarse_space_polygons).buffer(0) \
        if coarse_space_polygons else None
    fine_anchor_points: list[dict] = []
    for anchor in semantic_anchors or []:
        if not (str(anchor.get("semantic_profile") or "")
                or str(anchor.get("reference_profile") or "")):
            continue
        raw_point = anchor.get("point_m") or []
        try:
            point = Point(float(raw_point[0]), float(raw_point[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if (coarse_space_union is not None
                and coarse_space_union.buffer(.005).covers(point)):
            continue
        fine_anchor_points.append({
            "anchor_id": str(anchor.get("anchor_id") or ""),
            "semantic_profile": str(anchor.get("semantic_profile") or ""),
            "reference_profile": str(anchor.get("reference_profile") or ""),
            "point": point,
        })
    fine_supplements: list[Polygon] = []
    fine_supplement_evidence: list[dict] = []
    fine_rejections: list[dict] = []
    for candidate in fine_space_polygons:
        overlap_area = (float(candidate.intersection(coarse_space_union).area)
                        if coarse_space_union is not None else 0.0)
        overlap_ratio = overlap_area / max(float(candidate.area), 1e-9)
        contained_anchors = [
            anchor for anchor in fine_anchor_points
            if candidate.buffer(.005).covers(anchor["point"])
        ]
        if overlap_ratio <= .05 + 1e-9 and contained_anchors:
            fine_supplements.append(candidate)
            fine_supplement_evidence.append({
                "area_m2": round(float(candidate.area), 6),
                "bounds_m": [round(float(value), 6)
                             for value in candidate.bounds],
                "coarse_overlap_area_m2": round(overlap_area, 6),
                "coarse_overlap_ratio": round(overlap_ratio, 8),
                "semantic_anchors": [{
                    "anchor_id": anchor["anchor_id"],
                    "semantic_profile": anchor["semantic_profile"],
                    "reference_profile": anchor["reference_profile"],
                } for anchor in contained_anchors],
                "decision": "accepted_fine_space_with_uncovered_semantic_anchor",
            })
            continue

        # A fine-scale face can contain a real narrow room together with one
        # or more already accepted coarse rooms.  Rejecting the whole face on
        # overlap loses the narrow room (the real 08 DWG kitchen exercised
        # exactly this case).  Subtract the coarse truth and consider only
        # source-space residual components.  Semantic text is an eligibility
        # gate, never a polygon generator: the residual still needs at least
        # 1 m2 area, a 750 mm oriented short side and the anchor at least
        # 50 mm inside.  These constraints reject morphology border ribbons.
        residual_evidence: list[dict] = []
        accepted_residual_count = 0
        if (enable_semantic_residual_supplements
                and coarse_space_union is not None and contained_anchors):
            residual_geometry = candidate.difference(coarse_space_union).buffer(0)
            for residual in _polygons(residual_geometry, min_area=.50):
                residual_anchors = [
                    anchor for anchor in contained_anchors
                    if residual.buffer(.005).covers(anchor["point"])
                ]
                area = float(residual.area)
                short_side = _minimum_rotated_rectangle_side(residual)
                anchor_clearances = [
                    float(anchor["point"].distance(residual.boundary))
                    for anchor in residual_anchors
                ]
                accepted = bool(
                    area >= 1.0 - 1e-9
                    and short_side >= .75 - 1e-9
                    and anchor_clearances
                    and max(anchor_clearances) >= .05 - 1e-9
                )
                residual_evidence.append({
                    "area_m2": round(area, 6),
                    "bounds_m": [round(float(value), 6)
                                 for value in residual.bounds],
                    "minimum_rotated_rectangle_side_m": round(short_side, 6),
                    "semantic_anchor_count": len(residual_anchors),
                    "maximum_semantic_anchor_boundary_clearance_m": round(
                        max(anchor_clearances) if anchor_clearances else 0.0, 6),
                    "decision": (
                        "accepted_semantic_residual_space"
                        if accepted else "rejected_semantic_residual_space"),
                })
                if not accepted:
                    continue
                fine_supplements.append(residual)
                accepted_residual_count += 1
                fine_supplement_evidence.append({
                    "area_m2": round(area, 6),
                    "bounds_m": [round(float(value), 6)
                                 for value in residual.bounds],
                    "coarse_overlap_area_m2": 0.0,
                    "coarse_overlap_ratio": 0.0,
                    "source_fine_face_area_m2": round(float(candidate.area), 6),
                    "source_fine_face_coarse_overlap_ratio": round(
                        overlap_ratio, 8),
                    "minimum_rotated_rectangle_side_m": round(short_side, 6),
                    "maximum_semantic_anchor_boundary_clearance_m": round(
                        max(anchor_clearances), 6),
                    "semantic_anchors": [{
                        "anchor_id": anchor["anchor_id"],
                        "semantic_profile": anchor["semantic_profile"],
                        "reference_profile": anchor["reference_profile"],
                    } for anchor in residual_anchors],
                    "decision": (
                        "accepted_uncovered_semantic_residual_from_overlapping_fine_face"),
                })
        if accepted_residual_count:
            continue
        fine_rejections.append({
            "area_m2": round(float(candidate.area), 6),
            "bounds_m": [round(float(value), 6) for value in candidate.bounds],
            "coarse_overlap_area_m2": round(overlap_area, 6),
            "coarse_overlap_ratio": round(overlap_ratio, 8),
            "contained_uncovered_semantic_anchor_count": len(
                contained_anchors),
            "semantic_residual_candidates": residual_evidence,
            "reason": (
                "fine_face_semantic_residual_below_geometry_thresholds"
                if contained_anchors and residual_evidence
                else "fine_face_overlaps_coarse_room_set"
                if overlap_ratio > .05 + 1e-9
                else "fine_face_has_no_uncovered_semantic_anchor"),
        })
    space_polygons: list[Polygon] = [
        *coarse_space_polygons, *fine_supplements]
    space_polygons.sort(key=lambda polygon: (-float(polygon.area), tuple(polygon.bounds)))

    handles = sorted({str(row["source_handle"]) for row in evidence if row.get("source_handle")})
    footprints = []
    for index, polygon in enumerate(wall_polygons, 1):
        footprints.append({
            "id": f"cad_global_wall_footprint_{index}",
            "points": _model_ring(polygon.exterior, origin_x, origin_z),
            "interior_rings": [
                _model_ring(ring, origin_x, origin_z) for ring in polygon.interiors
            ],
            "floor_elevation_m": 0.0,
            "height_m": float(wall_height_m),
            "source": "cad_global_topology",
            "review_status": "needs_review",
            "source_representation": "global_wall_footprint",
            "source_entity_handles": handles,
            "cad_provenance": {
                "method": GLOBAL_TOPOLOGY_VERSION,
                "source_segment_count": len(evidence),
                "source_entity_handles": handles,
            },
        })

    coverage_mask = wall_geometry.buffer(0.011)
    covered_length = float(sum(
        line.intersection(coverage_mask).length for line in lines
    ))
    topology_proved = bool(
        source_length > 0
        and covered_length / max(source_length, 1e-9) >= .995
        and len(accepted_thicknesses) >= 2
        and wall_polygons
        and space_polygons
    )
    if topology_proved:
        for footprint in footprints:
            footprint["review_status"] = "accepted"
    summary = {
        "schema_version": 1,
        "method": GLOBAL_TOPOLOGY_VERSION,
        "status": "proved" if topology_proved else "draft",
        "source_segment_count": len(evidence),
        "source_entity_count": len(handles),
        "source_length_m": round(source_length, 6),
        "source_coverage_ratio": round(
            min(1.0, covered_length / max(source_length, 1e-9)), 8),
        "accepted_thickness_sample_count": len(accepted_thicknesses),
        "measured_spacing_p75_m": round(measured_spacing, 6),
        "wall_close_radius_m": round(wall_close_radius, 6),
        "inferred_single_run_width_m": round(inferred_single_width, 6),
        "promoted_single_run_count": len(unsupported),
        "topology_close_radius_m": round(topology_close_radius, 6),
        "topology_close_radius_base_m": round(
            topology_close_radius_base, 6),
        "space_marker_count": len(marker_points),
        "space_marker_kinds": dict(sorted(Counter(
            marker["kind"] for marker in marker_points).items())),
        "adaptive_topology_trials": [{
            key: value for key, value in trial.items()
            if key not in {"spaces", "barrier"}
        } for trial in topology_trials],
        "adaptive_topology_selected": bool(
            topology_close_radius > topology_close_radius_base + 1e-9),
        "adaptive_topology_scope": (
            "conflicted_space_local_separators"
            if adaptive_local_separator_count else "base_topology_unchanged"),
        "adaptive_local_separator_count": adaptive_local_separator_count,
        "adaptive_local_split_space_count": adaptive_local_split_space_count,
        "fine_topology_close_radius_m": round(
            fine_topology_close_radius, 6),
        "coarse_space_candidate_count": len(coarse_space_polygons),
        "fine_space_candidate_count": len(fine_space_polygons),
        "fine_supplement_space_count": len(fine_supplements),
        "fine_semantic_residual_thresholds": {
            "enabled": bool(enable_semantic_residual_supplements),
            "minimum_area_m2": 1.0,
            "minimum_rotated_rectangle_side_m": .75,
            "minimum_semantic_anchor_boundary_clearance_m": .05,
            "maximum_direct_coarse_overlap_ratio": .05,
        },
        "fine_supplement_evidence": fine_supplement_evidence[:100],
        "fine_supplement_evidence_truncated": len(
            fine_supplement_evidence) > 100,
        "fine_rejected_overlap_count": len(fine_rejections),
        "fine_rejected_overlap_evidence": fine_rejections[:100],
        "fine_rejected_overlap_evidence_truncated": len(fine_rejections) > 100,
        "opening_width_sample_count": len(opening_widths),
        "opening_axis_barrier_count": len(opening_barrier_lines),
        "opening_axis_barriers": opening_barrier_evidence[:100],
        "opening_axis_barriers_truncated": len(opening_barrier_evidence) > 100,
        "wall_footprint_count": len(footprints),
        "wall_area_m2": round(float(wall_geometry.area), 6),
        "wall_component_count": len(wall_polygons),
        "wall_interior_ring_count": sum(len(polygon.interiors) for polygon in wall_polygons),
        "space_candidate_count": len(space_polygons),
        "decision_basis": [
            "source_wall_rows_only",
            "measured_double_face_spacing",
            "unsupported_single_run_promotion",
            "source_backed_opening_axis_room_barrier_only",
            "source_backed_space_marker_conflict_minimisation",
            "measured_wall_spacing_topology_close",
            "nonoverlapping_fine_scale_space_supplement",
            "semantic_anchored_fine_residual_space_supplement",
            "closed_space_and_full_source_coverage_gate",
        ],
    }
    return {
        "summary": summary,
        "wall_footprints": footprints,
        # Private in-process geometry: callers must remove this key before
        # persisting a report or model.
        "_space_polygons": space_polygons,
    }


__all__ = [
    "GLOBAL_TOPOLOGY_VERSION", "GlobalTopologyError",
    "build_global_wall_topology",
]
