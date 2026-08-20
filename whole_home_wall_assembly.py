"""Deterministic CAD wall assembly construction.

The legacy CAD importer exposes audited structural entities as dictionaries with
``points`` and ``cad_provenance`` fields.  This module turns those entities into
wall *assemblies* without guessing that every line is a 120 mm centreline.

The returned dictionaries deliberately keep the existing whole-home wall fields
(``start``, ``end``, ``thickness_m`` and ``height_m``) while adding a canonical
``footprint_polygon`` and complete source evidence.  That makes the result usable
by the current importer and by the geometry-manifest work planned for model v3.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union


ANGLE_TOLERANCE_DEG = 1.0
MIN_FACE_SEPARATION_M = 0.06
MAX_FACE_SEPARATION_M = 0.60
MIN_PROJECTED_OVERLAP = 0.80
NODE_SNAP_TOLERANCE_M = 0.02
REDUNDANT_EVIDENCE_BUFFER_M = 0.02
_EPSILON = 1e-9
_ROLE_SCHEMA_VERSION = 1


class WallAssemblyError(ValueError):
    """A fail-closed wall or opening validation failure."""

    def __init__(self, code: str, message: str, *, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                **copy.deepcopy(self.details)}


@dataclass(frozen=True)
class _Segment:
    index: int
    entity_index: int
    segment_index: int
    start: tuple[float, float]
    end: tuple[float, float]
    row: Mapping[str, Any]

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def unit(self) -> tuple[float, float]:
        length = self.length
        return ((self.end[0] - self.start[0]) / length,
                (self.end[1] - self.start[1]) / length)


def _round(value: float) -> float:
    return round(float(value), 8)


def _point_dict(point: tuple[float, float]) -> dict:
    return {"x": _round(point[0]), "z": _round(point[1])}


def _point_list(point: tuple[float, float]) -> list[float]:
    return [_round(point[0]), _round(point[1])]


def _normalise_points(row: Mapping[str, Any]) -> list[tuple[float, float]]:
    points = row.get("points") or []
    result: list[tuple[float, float]] = []
    for point in points:
        if isinstance(point, Mapping):
            x = point.get("x")
            z = point.get("z", point.get("y"))
        else:
            try:
                x, z = point[0], point[1]
            except (IndexError, TypeError):
                continue
        try:
            candidate = (float(x), float(z))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in candidate):
            continue
        if not result or math.dist(result[-1], candidate) > _EPSILON:
            result.append(candidate)
    return result


def _is_closed(row: Mapping[str, Any], points: Sequence[tuple[float, float]]) -> bool:
    return bool(row.get("closed")) or (
        len(points) >= 4 and math.dist(points[0], points[-1]) <= _EPSILON
    )


def _ring_points(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove a tolerance-close duplicate endpoint before Polygon creation."""
    values = list(points)
    if len(values) >= 2 and math.dist(values[0], values[-1]) <= _EPSILON:
        return values[:-1]
    return values


def _provenance(row: Mapping[str, Any]) -> dict:
    source = copy.deepcopy(row.get("cad_provenance") or row.get("provenance") or {})
    layer = str(source.get("effective_layer") or source.get("layer")
                or row.get("layer") or "")
    source_handle = str(source.get("source_handle") or source.get("handle") or "")
    root_handle = str(source.get("root_handle") or source.get("handle")
                      or source_handle)
    return {
        **source,
        "handle": str(source.get("handle") or root_handle or source_handle),
        "root_handle": root_handle,
        "source_handle": source_handle,
        "layer": layer,
        "effective_layer": str(source.get("effective_layer") or layer),
        "insert_chain": copy.deepcopy(source.get("insert_chain") or []),
    }


def _source_entity(segment: _Segment) -> dict:
    provenance = _provenance(segment.row)
    original_points = _normalise_points(segment.row)
    if segment.segment_index + 1 < len(original_points):
        source_start = original_points[segment.segment_index]
        source_end = original_points[segment.segment_index + 1]
    else:
        source_start, source_end = segment.start, segment.end
    model_segment = [_point_list(segment.start), _point_list(segment.end)]
    return {
        # ``build_wall_assemblies`` may receive a selected subset.  Preserve
        # the parser's global entity identity when it is available so a
        # geometry-only authority proof can be checked without relying on the
        # subset's temporary list position.
        "entity_index": int(segment.row.get("entity_index", segment.entity_index)),
        "segment_index": segment.segment_index,
        "handle": provenance.get("handle") or "",
        "root_handle": provenance.get("root_handle") or "",
        "source_handle": provenance.get("source_handle") or "",
        "layer": provenance.get("effective_layer") or provenance.get("layer") or "",
        "block": provenance.get("block") or "",
        "insert_chain": copy.deepcopy(provenance.get("insert_chain") or []),
        "source_segment_m": [_point_list(source_start), _point_list(source_end)],
        "model_segment_m": model_segment,
        "node_snap_tolerance_m": NODE_SNAP_TOLERANCE_M,
        "cad_provenance": provenance,
    }


def _deduplicate_source_entities(entities: Iterable[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[Any, ...]] = set()
    for entity in entities:
        segment = entity.get("source_segment_m") or []
        key = (
            entity.get("entity_index"), entity.get("segment_index"),
            entity.get("source_handle"), repr(segment),
        )
        if key not in seen:
            seen.add(key)
            result.append(copy.deepcopy(entity))
    return result


def _evidence_fields(entities: Sequence[dict]) -> dict:
    entities = _deduplicate_source_entities(entities)
    handles = sorted({str(entity.get("source_handle") or entity.get("root_handle") or "")
                      for entity in entities
                      if entity.get("source_handle") or entity.get("root_handle")})
    root_handles = sorted({str(entity.get("root_handle") or "")
                           for entity in entities if entity.get("root_handle")})
    layers = sorted({str(entity.get("layer") or "")
                     for entity in entities if entity.get("layer")})
    insert_chains = [copy.deepcopy(entity.get("insert_chain") or [])
                     for entity in entities]
    return {
        "source_entity_handles": handles,
        "source_root_handles": root_handles,
        "source_layers": layers,
        "source_insert_chains": insert_chains,
        "source_entities": list(entities),
    }


def _snap_segment_endpoints(segments: Sequence[_Segment], tolerance: float) -> list[_Segment]:
    """Snap source endpoints within ``tolerance`` using deterministic clusters."""
    if not segments:
        return []
    points = [point for segment in segments for point in (segment.start, segment.end)]
    clusters: list[list[int]] = []
    point_cluster: dict[int, int] = {}
    # A simple transitive union is unsafe: 0, 19, 38 and 57 mm would become
    # one single-link cluster.  Require every pair in a candidate cluster to be
    # within the 20 mm contract (and retain the centroid displacement check) so
    # a 21 mm gap can never be snapped merely because its midpoint is close.
    for point_index in sorted(range(len(points)),
                              key=lambda index: (points[index][0], points[index][1], index)):
        best: Optional[tuple[float, int]] = None
        for cluster_index, member_indexes in enumerate(clusters):
            candidates = [points[index] for index in member_indexes] + [points[point_index]]
            max_pair_distance = max(
                math.dist(candidates[first], candidates[second])
                for first in range(len(candidates))
                for second in range(first + 1, len(candidates))
            )
            centroid = (sum(point[0] for point in candidates) / len(candidates),
                        sum(point[1] for point in candidates) / len(candidates))
            max_displacement = max(math.dist(point, centroid) for point in candidates)
            if (max_pair_distance <= tolerance + _EPSILON
                    and max_displacement <= tolerance + _EPSILON):
                score = math.dist(points[point_index], centroid)
                if best is None or (score, cluster_index) < best:
                    best = (score, cluster_index)
        if best is None:
            point_cluster[point_index] = len(clusters)
            clusters.append([point_index])
        else:
            point_cluster[point_index] = best[1]
            clusters[best[1]].append(point_index)
    representatives = []
    for member_indexes in clusters:
        values = [points[index] for index in member_indexes]
        representatives.append((sum(point[0] for point in values) / len(values),
                                sum(point[1] for point in values) / len(values)))
    snapped: list[_Segment] = []
    for index, segment in enumerate(segments):
        start = representatives[point_cluster[index * 2]]
        end = representatives[point_cluster[index * 2 + 1]]
        if math.dist(start, end) > _EPSILON:
            snapped.append(_Segment(segment.index, segment.entity_index,
                                    segment.segment_index, start, end, segment.row))
    return snapped


def _angle_difference(first: _Segment, second: _Segment) -> float:
    dot = max(-1.0, min(1.0, abs(first.unit[0] * second.unit[0]
                                 + first.unit[1] * second.unit[1])))
    return math.degrees(math.acos(dot))


def _projection(point: tuple[float, float], origin: tuple[float, float],
                unit: tuple[float, float]) -> float:
    return (point[0] - origin[0]) * unit[0] + (point[1] - origin[1]) * unit[1]


def _point_on_infinite_line(segment: _Segment, axis_origin: tuple[float, float],
                            axis_unit: tuple[float, float], t: float) -> tuple[float, float]:
    """Point on segment's infinite line at an axis-aligned projected coordinate."""
    seg_unit = segment.unit
    denominator = seg_unit[0] * axis_unit[0] + seg_unit[1] * axis_unit[1]
    if abs(denominator) <= _EPSILON:
        raise WallAssemblyError("cad_wall_face_projection_failed",
                                "墙面线无法投影到共同轴")
    local = (t - _projection(segment.start, axis_origin, axis_unit)) / denominator
    return (segment.start[0] + local * seg_unit[0],
            segment.start[1] + local * seg_unit[1])


def _pair_candidate(first: _Segment, second: _Segment) -> Optional[dict]:
    angle = _angle_difference(first, second)
    if angle > ANGLE_TOLERANCE_DEG + _EPSILON:
        return None
    axis = first.unit
    origin = first.start
    first_interval = sorted((_projection(first.start, origin, axis),
                             _projection(first.end, origin, axis)))
    second_interval = sorted((_projection(second.start, origin, axis),
                              _projection(second.end, origin, axis)))
    overlap_start = max(first_interval[0], second_interval[0])
    overlap_end = min(first_interval[1], second_interval[1])
    overlap = max(0.0, overlap_end - overlap_start)
    overlap_ratio = overlap / max(_EPSILON, min(first.length, second.length))
    if overlap_ratio + _EPSILON < MIN_PROJECTED_OVERLAP:
        return None
    first_mid = _point_on_infinite_line(first, origin, axis,
                                        (overlap_start + overlap_end) / 2)
    second_mid = _point_on_infinite_line(second, origin, axis,
                                         (overlap_start + overlap_end) / 2)
    separation = math.dist(first_mid, second_mid)
    if not (MIN_FACE_SEPARATION_M - _EPSILON <= separation
            <= MAX_FACE_SEPARATION_M + _EPSILON):
        return None
    if str(first.row.get("wall_role") or "") == "centerline" or str(
            second.row.get("wall_role") or "") == "centerline":
        return None
    first_points = (
        _point_on_infinite_line(first, origin, axis, overlap_start),
        _point_on_infinite_line(first, origin, axis, overlap_end),
    )
    second_points = (
        _point_on_infinite_line(second, origin, axis, overlap_start),
        _point_on_infinite_line(second, origin, axis, overlap_end),
    )
    return {
        "angle_difference_deg": angle,
        "separation_m": separation,
        "overlap_ratio": min(1.0, overlap_ratio),
        "overlap_start": overlap_start,
        "overlap_end": overlap_end,
        "axis_origin": origin,
        "axis_unit": axis,
        # Arc-length intervals on the source segments permit a long face to
        # pair with disjoint short faces while still preventing double use.
        "first_source_interval_m": sorted(
            _projection(point, first.start, first.unit) for point in first_points),
        "second_source_interval_m": sorted(
            _projection(point, second.start, second.unit) for point in second_points),
    }


def _polygon_coordinates(polygon: Polygon) -> list[list[float]]:
    return [[_round(x), _round(y)] for x, y in list(polygon.exterior.coords)[:-1]]


def _row_entity_index(row: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(row.get("entity_index", fallback))
    except (TypeError, ValueError):
        return fallback


def _row_root_handle(row: Mapping[str, Any], fallback: int) -> str:
    provenance = _provenance(row)
    return str(provenance.get("root_handle") or provenance.get("source_handle")
               or provenance.get("handle") or f"direct:{fallback}")


def _row_segments(row: Mapping[str, Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    points = _normalise_points(row)
    return [(first, second) for first, second in zip(points, points[1:])
            if math.dist(first, second) > _EPSILON]


def _group_bounds(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float]:
    points = [point for row in rows for point in _normalise_points(row)]
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points))


def _axis_angle(first: tuple[float, float], second: tuple[float, float]) -> float:
    angle = math.degrees(math.atan2(second[1] - first[1], second[0] - first[0])) % 180.0
    return 0.0 if abs(angle - 180.0) <= _EPSILON else angle


def _undirected_angle_difference(first: float, second: float) -> float:
    difference = abs((first - second) % 180.0)
    return min(difference, 180.0 - difference)


def _oriented_group_axis(rows: Sequence[Mapping[str, Any]]) -> dict:
    points = [point for row in rows for point in _normalise_points(row)]
    if len(points) < 2:
        return {"long_m": 0.0, "short_m": 0.0, "axis": [(0.0, 0.0), (0.0, 0.0)],
                "angle_deg": 0.0, "rectangularity": 0.0}
    hull = unary_union([Point(point) for point in points]).convex_hull
    if hull.geom_type in {"Point", "LineString"}:
        coords = list(hull.coords)
        first, second = coords[0], coords[-1]
        return {"long_m": math.dist(first, second), "short_m": 0.0,
                "axis": [tuple(first), tuple(second)],
                "angle_deg": _axis_angle(tuple(first), tuple(second)),
                "rectangularity": 0.0}
    rectangle = hull.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)[:-1]
    edges = [(coords[index], coords[(index + 1) % 4]) for index in range(4)]
    lengths = [math.dist(first, second) for first, second in edges]
    long_index = max(range(4), key=lambda index: lengths[index])
    long_m, short_m = lengths[long_index], min(lengths)
    short_edges = [edge for index, edge in enumerate(edges)
                   if index % 2 != long_index % 2]
    first_mid = ((short_edges[0][0][0] + short_edges[0][1][0]) / 2,
                 (short_edges[0][0][1] + short_edges[0][1][1]) / 2)
    second_mid = ((short_edges[1][0][0] + short_edges[1][1][0]) / 2,
                  (short_edges[1][0][1] + short_edges[1][1][1]) / 2)
    return {
        "long_m": long_m, "short_m": short_m,
        "axis": [first_mid, second_mid],
        "angle_deg": _axis_angle(first_mid, second_mid),
        "rectangularity": (float(hull.area) / float(rectangle.area)
                           if rectangle.area > _EPSILON else 0.0),
    }


def _group_source_fields(rows: Sequence[Mapping[str, Any]], indexes: Sequence[int]) -> dict:
    provenances = [_provenance(row) for row in rows]
    handles = sorted({str(value) for provenance in provenances
                      for value in (provenance.get("source_handle"), provenance.get("handle"))
                      if value})
    roots = sorted({str(provenance.get("root_handle") or "")
                    for provenance in provenances if provenance.get("root_handle")})
    layers = sorted({str(provenance.get("effective_layer") or provenance.get("layer") or
                         row.get("layer") or "")
                     for provenance, row in zip(provenances, rows)})
    blocks = sorted({str(provenance.get("block") or "")
                     for provenance in provenances if provenance.get("block")})
    colors = sorted({int(row.get("aci_color")) for row in rows
                     if isinstance(row.get("aci_color"), (int, float))})
    return {
        "root_handle": roots[0] if len(roots) == 1 else "",
        "source_handles": handles,
        "entity_indexes": sorted(set(int(index) for index in indexes)),
        "entity_types": sorted({str(row.get("entity_type") or "") for row in rows}),
        "layers": layers, "blocks": blocks, "colors": colors,
    }


def _circle_from_points(points: Sequence[tuple[float, float]]) -> Optional[dict]:
    if len(points) < 3:
        return None
    first, middle, last = points[0], points[len(points) // 2], points[-1]
    determinant = 2 * (first[0] * (middle[1] - last[1])
                       + middle[0] * (last[1] - first[1])
                       + last[0] * (first[1] - middle[1]))
    if abs(determinant) <= 1e-8:
        return None
    first_sq = first[0] ** 2 + first[1] ** 2
    middle_sq = middle[0] ** 2 + middle[1] ** 2
    last_sq = last[0] ** 2 + last[1] ** 2
    center = (
        (first_sq * (middle[1] - last[1]) + middle_sq * (last[1] - first[1])
         + last_sq * (first[1] - middle[1])) / determinant,
        (first_sq * (last[0] - middle[0]) + middle_sq * (first[0] - last[0])
         + last_sq * (middle[0] - first[0])) / determinant,
    )
    radius = math.dist(center, first)
    if radius <= _EPSILON:
        return None
    radial_errors = [abs(math.dist(center, point) - radius) for point in points]
    arc_length = sum(math.dist(first, second) for first, second in zip(points, points[1:]))
    sweep_deg = math.degrees(arc_length / radius)
    return {"center": center, "radius_m": radius, "sweep_deg": sweep_deg,
            "max_radial_error_m": max(radial_errors, default=0.0),
            "endpoints": (first, last)}


def _tessellated_arc_chains(
    indexed_rows: Sequence[tuple[int, Mapping[str, Any]]],
) -> list[dict]:
    """Recover source arcs exported as short chords or one open polyline.

    Some DWG exporters flatten every curve before writing the file.  Depending
    on the writer, the chords arrive either as independent LINE entities or as
    one open LWPOLYLINE/POLYLINE.  Treating either encoding as wall faces both
    loses door openings and creates false wall evidence.  This recovery is
    deliberately only a *motif source*: the caller must still prove a matching
    radial leaf and independent wall support before it can emit an opening.
    """
    recovered: list[dict] = []

    # A single open polyline is already ordered, so validate it directly before
    # building the endpoint graph used by independently authored LINE chords.
    # The maximum chord is slightly larger here because common anonymous door
    # blocks use eight 11.25-degree chords for a roughly 900 mm leaf (about
    # 175 mm per chord).  Circular fit, sweep, radius, path length, radial leaf
    # and wall-network support remain mandatory downstream.
    for index, row in indexed_rows:
        if str(row.get("entity_type") or "") not in {
                "LWPOLYLINE", "POLYLINE"} or bool(row.get("closed")):
            continue
        segments = _row_segments(row)
        if not 6 <= len(segments) <= 32:
            continue
        chord_lengths = [math.dist(first, second) for first, second in segments]
        if any(not .015 <= length <= .20 for length in chord_lengths):
            continue
        points = _normalise_points(row)
        if len(points) != len(segments) + 1:
            continue
        circle = _circle_from_points(points)
        path_length = sum(chord_lengths)
        if (not circle or not .40 <= float(circle["radius_m"]) <= 1.50
                or not 55.0 <= float(circle["sweep_deg"]) <= 125.0
                or float(circle["max_radial_error_m"]) > .03
                or not .45 <= path_length <= 2.20):
            continue
        recovered.append({
            "rows": [row], "indexes": [int(index)], "circle": circle,
            "points": points,
            "evidence": {
                "method": "cad_tessellated_circular_swing_chain_v1",
                "source_encoding": "single_open_polyline",
                "source_entity_count": 1,
                "chord_count": len(segments),
                "path_length_m": _round(path_length),
                "minimum_chord_length_m": .015,
                "maximum_chord_length_m": .20,
                "maximum_chain_radial_error_m": .03,
            },
        })

    chords: list[dict] = []
    for index, row in indexed_rows:
        if str(row.get("entity_type") or "") not in {
                "LINE", "LWPOLYLINE", "POLYLINE"}:
            continue
        segments = _row_segments(row)
        if len(segments) != 1:
            continue
        first, second = segments[0]
        length = math.dist(first, second)
        if not .015 <= length <= .16:
            continue
        chords.append({
            "index": int(index), "row": row, "first": first,
            "second": second, "length_m": length,
        })

    # This tolerance only joins successive source chords into an evidence
    # chain.  It is intentionally independent of the invariant 20 mm wall-node
    # snap contract and never mutates source coordinates.
    endpoint_tolerance_m = .002

    def endpoint_key(point: tuple[float, float]) -> tuple[int, int]:
        return (round(point[0] / endpoint_tolerance_m),
                round(point[1] / endpoint_tolerance_m))

    incident: dict[tuple[int, int], list[int]] = {}
    for number, chord in enumerate(chords):
        for point in (chord["first"], chord["second"]):
            incident.setdefault(endpoint_key(point), []).append(number)

    components: list[list[int]] = []
    visited: set[int] = set()
    for seed in range(len(chords)):
        if seed in visited:
            continue
        pending = [seed]
        visited.add(seed)
        component: list[int] = []
        while pending:
            number = pending.pop()
            component.append(number)
            for point in (chords[number]["first"], chords[number]["second"]):
                for neighbour in incident.get(endpoint_key(point), []):
                    if neighbour not in visited:
                        visited.add(neighbour)
                        pending.append(neighbour)
        components.append(component)

    for component in components:
        if not 6 <= len(component) <= 32:
            continue
        local_incident: dict[tuple[int, int], list[int]] = {}
        for number in component:
            chord = chords[number]
            for point in (chord["first"], chord["second"]):
                local_incident.setdefault(endpoint_key(point), []).append(number)
        degrees = [len(values) for values in local_incident.values()]
        endpoints = sorted(key for key, values in local_incident.items()
                           if len(values) == 1)
        if max(degrees, default=0) > 2 or len(endpoints) != 2:
            continue

        ordered_numbers: list[int] = []
        ordered_points: list[tuple[float, float]] = []
        used: set[int] = set()
        current = endpoints[0]
        while len(used) < len(component):
            choices = sorted(number for number in local_incident[current]
                             if number not in used)
            if len(choices) != 1:
                break
            number = choices[0]
            used.add(number)
            chord = chords[number]
            first_key = endpoint_key(chord["first"])
            if first_key == current:
                current_point, next_point = chord["first"], chord["second"]
            else:
                current_point, next_point = chord["second"], chord["first"]
            if not ordered_points:
                ordered_points.append(current_point)
            ordered_points.append(next_point)
            ordered_numbers.append(number)
            current = endpoint_key(next_point)
        if len(used) != len(component):
            continue

        circle = _circle_from_points(ordered_points)
        path_length = sum(math.dist(first, second) for first, second in zip(
            ordered_points, ordered_points[1:]))
        if (not circle or not .40 <= float(circle["radius_m"]) <= 1.50
                or not 55.0 <= float(circle["sweep_deg"]) <= 125.0
                or float(circle["max_radial_error_m"]) > .03
                or not .45 <= path_length <= 2.20):
            continue
        rows = [chords[number]["row"] for number in ordered_numbers]
        indexes = [int(chords[number]["index"])
                   for number in ordered_numbers]
        recovered.append({
            "rows": rows, "indexes": indexes, "circle": circle,
            "points": ordered_points,
            "evidence": {
                "method": "cad_tessellated_circular_swing_chain_v1",
                "source_encoding": "independent_line_chords",
                "source_entity_count": len(indexes),
                "chord_count": len(indexes),
                "path_length_m": _round(path_length),
                "endpoint_join_tolerance_m": endpoint_tolerance_m,
                "minimum_chord_length_m": .015,
                "maximum_chord_length_m": .16,
                "maximum_chain_radial_error_m": .03,
            },
        })
    return recovered


def _opening_candidate(
    candidate_id: str, kind: str, rows: Sequence[Mapping[str, Any]], indexes: Sequence[int],
    axis: Sequence[tuple[float, float]], width_m: float, confidence: float,
    reason_codes: Sequence[str], evidence_geometry: Mapping[str, Any],
) -> dict:
    center = ((axis[0][0] + axis[-1][0]) / 2, (axis[0][1] + axis[-1][1]) / 2)
    source = _group_source_fields(rows, indexes)
    return {
        "candidate_id": candidate_id, "kind": kind, "status": "review",
        "confidence": _round(confidence),
        "source_root_handle": source["root_handle"],
        "source_handles": source["source_handles"],
        "source_entity_indexes": source["entity_indexes"],
        "wall_source_handles": [],
        "width_m": _round(width_m),
        "center_cad_m": _point_list(center),
        "axis_segment_cad_m": [_point_list(tuple(axis[0])), _point_list(tuple(axis[-1]))],
        "evidence_geometry": copy.deepcopy(dict(evidence_geometry)),
        "reason_codes": list(reason_codes),
    }


def _raw_opening_summary(candidates: Sequence[Mapping[str, Any]]) -> dict:
    statuses: dict[str, int] = {"accepted": 0, "review": 0, "rejected": 0}
    kinds: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for candidate in candidates:
        status = str(candidate.get("status") or "review")
        statuses[status] = statuses.get(status, 0) + 1
        kind = str(candidate.get("kind") or "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
        for reason in candidate.get("reason_codes") or []:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    return {
        "schema_version": _ROLE_SCHEMA_VERSION,
        "method": "cad_raw_geometry_opening_v1",
        "candidate_count": len(candidates),
        "accepted_count": statuses.get("accepted", 0),
        "review_count": statuses.get("review", 0),
        "rejected_count": statuses.get("rejected", 0),
        "kind_counts": dict(sorted(kinds.items())),
        "reason_counts": dict(sorted(reasons.items())),
    }


def summarize_raw_geometry_openings(candidates: Sequence[Mapping[str, Any]]) -> dict:
    """Public deterministic summary used by full and pointer CAD reports."""
    return _raw_opening_summary(candidates)


def decompose_cad_entity_roles(
    selected_rows: Sequence[Mapping[str, Any]],
    *,
    context_rows: Sequence[Mapping[str, Any]] = (),
    semantic_anchors: Sequence[Mapping[str, Any]] = (),
) -> dict:
    """Separate authoritative wall rows from inherited CAD detail geometry.

    Names, layers, handles and ACI colours are retained only as evidence.  The
    decisions use geometry: repeated compact insert signatures, nested closed
    contours, elongated parallel frame rails, wall-network support and a
    circular swing plus radial leaf.  High-specificity nested cabinet contours
    are context; other uncertain compact inserts remain fail-closed review.
    """
    selected = [copy.deepcopy(dict(row)) for row in selected_rows]
    context = [copy.deepcopy(dict(row)) for row in context_rows]
    selected_plan_bounds = (_group_bounds(selected) if selected
                            else (0.0, 0.0, 0.0, 0.0))
    indexed: list[tuple[int, dict]] = []
    used_indexes: set[int] = set()
    for fallback, row in enumerate(selected):
        index = _row_entity_index(row, fallback)
        while index in used_indexes:
            index += 1
        used_indexes.add(index)
        row["entity_index"] = index
        indexed.append((index, row))
    selected_by_index = dict(indexed)
    groups: dict[str, list[tuple[int, dict]]] = {}
    for fallback, (index, row) in enumerate(indexed):
        groups.setdefault(_row_root_handle(row, fallback), []).append((index, row))

    # Opening symbols are often authored as loose model-space primitives: each
    # rail has its own handle/root even though the rails form one physical
    # frame.  Build one stable raw index here so root-group and geometry-group
    # opening detection share the exact same provenance indexes as the later
    # door-swing pass.
    raw_rows = selected + context
    raw_indexed: list[tuple[int, dict]] = []
    raw_used = set(used_indexes)
    next_index = max(raw_used, default=-1) + 1
    for fallback, row in enumerate(raw_rows):
        index = _row_entity_index(row, fallback)
        if row in selected:
            raw_indexed.append((index, row))
            continue
        while index in raw_used:
            index = next_index
            next_index += 1
        raw_used.add(index)
        row["entity_index"] = index
        raw_indexed.append((index, row))
    raw_groups: dict[str, list[tuple[int, dict]]] = {}
    for fallback, (index, row) in enumerate(raw_indexed):
        raw_groups.setdefault(_row_root_handle(row, fallback), []).append((index, row))

    all_wall_lines = [(index, LineString([first, second])) for index, row in indexed
                      for first, second in _row_segments(row)]
    # Door-axis recovery compares these segments in nested loops.  Cache the
    # immutable scalar facts once so large drawings do not repeatedly cross
    # the Python/GEOS boundary just to read endpoints, angles and bounds.
    all_wall_line_facts = []
    for entity_index, line in all_wall_lines:
        start = tuple(line.coords[0])
        end = tuple(line.coords[-1])
        all_wall_line_facts.append({
            "index": int(entity_index),
            "line": line,
            "angle": _axis_angle(start, end),
            "midpoint": ((start[0] + end[0]) / 2,
                         (start[1] + end[1]) / 2),
            "bounds": tuple(float(value) for value in line.bounds),
        })
    role_by_index: dict[int, tuple[str, str, list[str]]] = {
        index: ("wall_face", "high", ["structural_source_geometry"])
        for index, _ in indexed
    }
    opening_candidates: list[dict] = []
    opening_group_roots: set[str] = set()
    fixture_envelope_evidence_by_index: dict[int, list[dict]] = {}
    micro_marker_evidence_by_index: dict[int, dict] = {}
    stair_run_evidence_by_index: dict[int, list[dict]] = {}
    partial_stair_splits_by_index: dict[int, dict] = {}
    counter_band_evidence_by_index: dict[int, list[dict]] = {}
    dense_fixture_evidence_by_index: dict[int, list[dict]] = {}
    closed_wall_band_evidence_by_index: dict[int, dict] = {}
    perimeter_wall_shell_evidence_by_index: dict[int, dict] = {}
    perimeter_wall_shell_rows: list[dict] = []
    perimeter_wall_shell_proofs: list[dict] = []
    semantic_envelope_evidence_by_index: dict[int, dict] = {}
    semantic_building_envelope_evidence: dict = {}
    semantic_building_envelope_diagnostics: dict = {}

    # Detect geometrically explicit window/frame symbols before compact-detail
    # filtering, because door/window blocks are intentionally repeated.  In
    # addition to proper INSERT/root groups, recover disconnected authoring
    # where every LINE has a unique handle.  Loose primitives are grouped only
    # when their 31 mm buffers form one component; the downstream elongated
    # multi-rail test plus support at both ends remains the acceptance proof.
    frame_groups: list[tuple[str, list[tuple[int, dict]], bool, dict]] = [
        (root_handle, entries, False, {})
        for root_handle, entries in sorted(raw_groups.items())
    ]
    loose_geometries: list[tuple[int, dict, Any]] = []
    for index, row in raw_indexed:
        root_handle = _row_root_handle(row, index)
        if index in selected_by_index or len(raw_groups.get(root_handle) or []) != 1:
            continue
        if str(row.get("entity_type") or "") not in {"LINE", "LWPOLYLINE", "POLYLINE"}:
            continue
        segments = [LineString([first, second]) for first, second in _row_segments(row)]
        if not segments:
            continue
        geometry = unary_union(segments)
        loose_geometries.append((index, row, geometry))
    # Use a pair of maximal parallel rails as the seed instead of a generic
    # connected component.  This prevents nearby bed/cabinet geometry from
    # swallowing a real perimeter window into a >40-entity furniture cluster.
    loose_rails: list[tuple[int, dict, tuple[float, float], tuple[float, float],
                            LineString]] = []
    for index, row, _geometry in loose_geometries:
        segments = _row_segments(row)
        if len(segments) != 1:
            continue
        first, second = segments[0]
        length = math.dist(first, second)
        if .40 <= length <= 3.00:
            loose_rails.append((index, row, first, second,
                                LineString([first, second])))
    seen_components: set[tuple[int, ...]] = set()
    seen_sparse_pairs: set[tuple[int, int]] = set()
    for rail_number, first_rail in enumerate(loose_rails):
        first_index, _first_row, first_start, first_end, first_line = first_rail
        first_length = first_line.length
        axis_unit = ((first_end[0] - first_start[0]) / first_length,
                     (first_end[1] - first_start[1]) / first_length)
        for second_rail in loose_rails[rail_number + 1:]:
            second_index, _second_row, second_start, second_end, second_line = second_rail
            if _undirected_angle_difference(
                    _axis_angle(first_start, first_end),
                    _axis_angle(second_start, second_end)) > 2.0:
                continue
            separation = first_line.distance(second_line)
            if not .035 <= separation <= .35:
                continue
            first_interval = (0.0, first_length)
            second_values = [
                (point[0] - first_start[0]) * axis_unit[0]
                + (point[1] - first_start[1]) * axis_unit[1]
                for point in (second_start, second_end)
            ]
            second_interval = (min(second_values), max(second_values))
            overlap_start = max(first_interval[0], second_interval[0])
            overlap_end = min(first_interval[1], second_interval[1])
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap < .40 or overlap < .80 * min(first_length, second_line.length):
                continue
            first_axis_point = (first_start[0] + overlap_start * axis_unit[0],
                                first_start[1] + overlap_start * axis_unit[1])
            second_axis_point = (first_start[0] + overlap_end * axis_unit[0],
                                 first_start[1] + overlap_end * axis_unit[1])
            axis_normal = (-axis_unit[1], axis_unit[0])
            signed_separation = (
                (second_start[0] - first_start[0]) * axis_normal[0]
                + (second_start[1] - first_start[1]) * axis_normal[1])
            first_axis_point = (
                first_axis_point[0] + axis_normal[0] * signed_separation / 2,
                first_axis_point[1] + axis_normal[1] * signed_separation / 2,
            )
            second_axis_point = (
                second_axis_point[0] + axis_normal[0] * signed_separation / 2,
                second_axis_point[1] + axis_normal[1] * signed_separation / 2,
            )
            seed_axis = LineString([first_axis_point, second_axis_point])
            # Preserve the two source rails as their own candidate before a
            # wider corridor collects nearby furniture/detail primitives.  The
            # pair is admitted only when both axis endpoints already have
            # strict parallel structural-wall support; the later two-sided or
            # exterior-face proof remains mandatory.  This is necessary for
            # sparse windows drawn with rails only and surrounded by unrelated
            # bathroom/kitchen detail.
            parallel_seed_support = [
                line for entity_index, line in all_wall_lines
                if entity_index not in {first_index, second_index}
                and _undirected_angle_difference(
                    _axis_angle(first_axis_point, second_axis_point),
                    _axis_angle(tuple(line.coords[0]), tuple(line.coords[-1])))
                <= 5.0]
            seed_support_union = (unary_union(parallel_seed_support)
                                  if parallel_seed_support else None)
            seed_endpoint_support = [
                (float(Point(endpoint).distance(seed_support_union))
                 if seed_support_union is not None else float("inf"))
                for endpoint in (first_axis_point, second_axis_point)]
            sparse_pair_signature = tuple(sorted((first_index, second_index)))
            if (sparse_pair_signature not in seen_sparse_pairs
                    and max(seed_endpoint_support, default=float("inf"))
                    <= .09 + _EPSILON):
                seen_sparse_pairs.add(sparse_pair_signature)
                frame_groups.append((
                    f"loose-sparse-frame:{sparse_pair_signature[0]}:"
                    f"{sparse_pair_signature[1]}",
                    [(first_index, first_rail[1]),
                     (second_index, second_rail[1])],
                    True,
                    {
                        "seed_rail_angle_difference_deg": _round(
                            _undirected_angle_difference(
                                _axis_angle(first_start, first_end),
                                _axis_angle(second_start, second_end))),
                        "seed_rail_separation_m": _round(separation),
                        "seed_axis_cad_m": [
                            _point_list(first_axis_point),
                            _point_list(second_axis_point)],
                        "seed_long_m": _round(overlap),
                        "sparse_pair_only": True,
                        "seed_wall_endpoint_support_distance_m": [
                            _round(value) for value in seed_endpoint_support],
                    },
                ))
            corridor = seed_axis.buffer(separation / 2 + .07, cap_style=3, join_style=2)
            entries = [
                (index, row) for index, row, geometry in loose_geometries
                if corridor.buffer(.005).covers(geometry)
            ]
            signature = tuple(sorted(index for index, _row in entries))
            if len(entries) < 2 or len(entries) > 40 or signature in seen_components:
                continue
            seen_components.add(signature)
            frame_groups.append((
                f"loose-frame:{min(first_index, second_index)}", entries, True,
                {
                    "seed_rail_angle_difference_deg": _round(
                        _undirected_angle_difference(
                            _axis_angle(first_start, first_end),
                            _axis_angle(second_start, second_end))),
                    "seed_rail_separation_m": _round(separation),
                    "seed_axis_cad_m": [
                        _point_list(first_axis_point), _point_list(second_axis_point)],
                    "seed_long_m": _round(overlap),
                },
            ))

    for group_number, (root_handle, entries, loose_component, seed_evidence) in enumerate(
            frame_groups, 1):
        rows = [row for _, row in entries]
        indexes = [index for index, _ in entries]
        axis = _oriented_group_axis(rows)
        if loose_component:
            seed_axis = seed_evidence.get("seed_axis_cad_m") or []
            axis = {
                "long_m": float(seed_evidence.get("seed_long_m") or 0.0),
                "short_m": float(seed_evidence.get("seed_rail_separation_m") or 0.0),
                "axis": [tuple(point) for point in seed_axis],
                "angle_deg": _axis_angle(tuple(seed_axis[0]), tuple(seed_axis[-1])),
                "rectangularity": 1.0,
            }
        long_m, short_m = float(axis["long_m"]), float(axis["short_m"])
        if not (.40 <= long_m <= 3.00 and .035 <= short_m <= .35
                and long_m / max(short_m, _EPSILON) >= 2.5 and len(entries) <= 40):
            continue
        long_rails = 0
        cross_members = 0
        for row in rows:
            for first, second in _row_segments(row):
                length = math.dist(first, second)
                angle_difference = _undirected_angle_difference(
                    _axis_angle(first, second), float(axis["angle_deg"]))
                if length >= long_m * .65 and angle_difference <= 5.0:
                    long_rails += 1
                elif length >= short_m * .60 and angle_difference >= 70.0:
                    cross_members += 1
        closed_outline = any(_is_closed(row, _normalise_points(row)) for row in rows)
        sparse_loose_frame = (
            loose_component and 2 <= len(entries) <= 3 and long_rails >= 2)
        if long_rails < 2 or not (
                cross_members >= 1 or closed_outline or sparse_loose_frame):
            continue
        own_indexes = set(indexes)
        supporting_lines = [line for entity_index, line in all_wall_lines
                            if entity_index not in own_indexes]
        own_geometry = unary_union([LineString([first, second]) for row in rows
                                    for first, second in _row_segments(row)])
        support_distance = (float(own_geometry.distance(unary_union(supporting_lines)))
                            if supporting_lines and not own_geometry.is_empty else float("inf"))
        if support_distance > .25:
            continue
        parallel_support = [
            line for entity_index, line in all_wall_lines
            if entity_index not in own_indexes
            and _undirected_angle_difference(
                float(axis["angle_deg"]),
                _axis_angle(tuple(line.coords[0]), tuple(line.coords[-1])),
            ) <= 5.0
        ]
        support_union = unary_union(parallel_support) if parallel_support else None
        endpoint_support = [
            (float(Point(endpoint).distance(support_union))
             if support_union is not None else float("inf"))
            for endpoint in axis["axis"]
        ]
        if max(endpoint_support, default=float("inf")) > .30:
            continue
        axis_line = LineString(axis["axis"])
        axis_length = axis_line.length
        axis_start = tuple(axis_line.coords[0])
        axis_end = tuple(axis_line.coords[-1])
        axis_unit = ((axis_end[0] - axis_start[0]) / axis_length,
                     (axis_end[1] - axis_start[1]) / axis_length)
        axis_normal = (-axis_unit[1], axis_unit[0])
        interior_wall_overlaps: list[float] = []
        signed_wall_face_offsets: list[float] = []
        for wall_line in parallel_support:
            if wall_line.distance(axis_line) > max(.35, short_m + .10):
                continue
            wall_midpoint = wall_line.interpolate(.5, normalized=True)
            signed_wall_face_offsets.append(
                (float(wall_midpoint.x) - axis_start[0]) * axis_normal[0]
                + (float(wall_midpoint.y) - axis_start[1]) * axis_normal[1])
            values = [
                (point[0] - axis_start[0]) * axis_unit[0]
                + (point[1] - axis_start[1]) * axis_unit[1]
                for point in (tuple(wall_line.coords[0]), tuple(wall_line.coords[-1]))
            ]
            overlap = max(0.0, min(axis_length, max(values)) - max(0.0, min(values)))
            interior_wall_overlaps.append(overlap)
        interior_wall_overlap_ratio = (
            max(interior_wall_overlaps, default=0.0) / max(axis_length, _EPSILON))
        negative_face_offsets = [value for value in signed_wall_face_offsets
                                 if value <= -.02]
        positive_face_offsets = [value for value in signed_wall_face_offsets
                                 if value >= .02]
        opposite_wall_face_support = bool(
            negative_face_offsets and positive_face_offsets)
        axis_midpoint = axis_line.interpolate(.5, normalized=True)
        selected_boundary_distance = min(
            abs(float(axis_midpoint.x) - float(selected_plan_bounds[0])),
            abs(float(axis_midpoint.x) - float(selected_plan_bounds[2])),
            abs(float(axis_midpoint.y) - float(selected_plan_bounds[1])),
            abs(float(axis_midpoint.y) - float(selected_plan_bounds[3])),
        )
        same_side_offsets = (negative_face_offsets if negative_face_offsets
                             and not positive_face_offsets else
                             positive_face_offsets if positive_face_offsets
                             and not negative_face_offsets else [])
        nearest_same_side_offset = min(
            (abs(value) for value in same_side_offsets), default=float("inf"))
        same_side_offset_spread = (
            max(same_side_offsets) - min(same_side_offsets)
            if same_side_offsets else float("inf"))
        sparse_exterior_support = bool(
            sparse_loose_frame
            and not opposite_wall_face_support
            and len(same_side_offsets) >= 2
            and .04 - _EPSILON <= nearest_same_side_offset <= .20 + _EPSILON
            and same_side_offset_spread <= .01 + _EPSILON
            and selected_boundary_distance <= .50 + _EPSILON)
        if not opposite_wall_face_support and not sparse_exterior_support:
            continue
        sparse_frame_proof: dict[str, Any] = {}
        if sparse_loose_frame:
            nearest_negative = max(negative_face_offsets, default=0.0)
            nearest_positive = min(positive_face_offsets, default=0.0)
            supported_wall_band_width = (
                nearest_positive - nearest_negative
                if opposite_wall_face_support else 0.0)
            wall_band_midpoint_offset = (
                (nearest_positive + nearest_negative) / 2
                if opposite_wall_face_support else nearest_same_side_offset)
            if (max(endpoint_support, default=float("inf")) > .09 + _EPSILON
                    or interior_wall_overlap_ratio > .20 + _EPSILON
                    or (opposite_wall_face_support
                        and (len(negative_face_offsets) < 2
                             or len(positive_face_offsets) < 2
                             or not .06 <= supported_wall_band_width <= .60
                             or abs(wall_band_midpoint_offset) > .08 + _EPSILON))
                    or float(seed_evidence.get(
                        "seed_rail_angle_difference_deg") or 0) > 1.0 + _EPSILON):
                continue
            sparse_frame_proof = {
                "method": (
                    "sparse_parallel_frame_unique_wall_gap_v1"
                    if opposite_wall_face_support else
                    "sparse_parallel_frame_exterior_wall_face_v1"),
                "source_row_count": len(entries),
                "negative_wall_face_support_count": len(negative_face_offsets),
                "positive_wall_face_support_count": len(positive_face_offsets),
                "supported_wall_band_width_m": _round(supported_wall_band_width),
                "wall_band_midpoint_offset_m": _round(wall_band_midpoint_offset),
                "single_side_wall_face_support_count": len(same_side_offsets),
                "single_side_wall_face_offset_spread_m": (
                    _round(same_side_offset_spread)
                    if math.isfinite(same_side_offset_spread) else None),
                "selected_plan_boundary_distance_m": _round(
                    selected_boundary_distance),
                "thresholds": {
                    "minimum_source_row_count": 2,
                    "maximum_source_row_count": 3,
                    "minimum_long_rail_count": 2,
                    "minimum_wall_face_support_per_side": 2,
                    "maximum_endpoint_support_distance_m": .09,
                    "maximum_interior_wall_overlap_ratio": .20,
                    "minimum_supported_wall_band_width_m": .06,
                    "maximum_supported_wall_band_width_m": .60,
                    "maximum_wall_band_midpoint_offset_m": .08,
                    "minimum_exterior_same_side_support_count": 2,
                    "minimum_exterior_wall_face_offset_m": .04,
                    "maximum_exterior_wall_face_offset_m": .20,
                    "maximum_exterior_same_side_offset_spread_m": .01,
                    "maximum_selected_plan_boundary_distance_m": .50,
                    "maximum_seed_rail_angle_difference_deg": 1.0,
                },
                "decision_basis": [
                    "two_or_three_independent_parallel_context_rails",
                    ("two_sided_repeated_structural_wall_face_support"
                     if opposite_wall_face_support else
                     "repeated_single_side_exterior_wall_face_support"),
                    ("unique_centered_gap_between_wall_faces"
                     if opposite_wall_face_support else
                     "frame_lies_at_selected_plan_exterior_boundary"),
                    "frame_axis_does_not_overlap_structural_wall_run",
                    "strict_wall_endpoint_support",
                ],
            }
        confidence = (.95 if sparse_loose_frame and long_rails >= 3
                      else .93 if sparse_loose_frame
                      else .97 if long_rails >= 3 and cross_members >= 2 else .90)
        reasons = ["parallel_frame_rails", "elongated_frame_geometry",
                   "wall_network_supported"]
        if loose_component:
            reasons.extend(["loose_frame_geometry_component",
                            "parallel_wall_end_support"])
        reasons.append(
            "opposite_wall_face_support" if opposite_wall_face_support
            else "single_exterior_wall_face_support")
        if sparse_loose_frame:
            reasons.append("sparse_frame_unique_wall_gap_geometry")
        opening_candidates.append(_opening_candidate(
            f"cad_raw_opening_frame_{group_number}", "window", rows, indexes,
            axis["axis"], long_m, confidence,
            reasons,
            {"bbox_m": [_round(value) for value in _group_bounds(rows)],
             "long_rail_count": long_rails, "cross_member_count": cross_members,
             "short_span_m": _round(short_m), "wall_support_distance_m": _round(support_distance),
             "orientation_deg": _round(float(axis["angle_deg"])),
             "wall_endpoint_support_distance_m": [
                 _round(value) for value in endpoint_support],
             "interior_wall_overlap_ratio": _round(interior_wall_overlap_ratio),
             "signed_wall_face_offsets_m": [
                 _round(value) for value in sorted(signed_wall_face_offsets)],
             "opposite_wall_face_support": opposite_wall_face_support,
             **({"sparse_frame_evidence": sparse_frame_proof}
                if sparse_frame_proof else {}),
             **copy.deepcopy(seed_evidence),
             "grouping_method": (
                 "loose_maximal_parallel_rail_pair" if loose_component else "source_root")},
        ))
        opening_group_roots.update(
            _row_root_handle(row, index) for index, row in entries)
        for index in indexes:
            if index in selected_by_index:
                role_by_index[index] = (
                    "opening_symbol", "high",
                    ["parallel_frame_rails", "wall_network_supported"],
                )

    # Find compact repeated INSERT geometry without inspecting its block name.
    signatures: dict[tuple, list[str]] = {}
    group_metrics: dict[str, dict] = {}
    for root_handle, entries in groups.items():
        rows = [row for _, row in entries]
        bounds = _group_bounds(rows)
        width, depth = bounds[2] - bounds[0], bounds[3] - bounds[1]
        entity_types: dict[str, int] = {}
        for row in rows:
            kind = str(row.get("entity_type") or "")
            entity_types[kind] = entity_types.get(kind, 0) + 1
        inserted = any((_provenance(row).get("insert_chain")
                        or _provenance(row).get("block")) for row in rows)
        signature = (
            tuple(sorted(entity_types.items())), len(rows),
            round(min(width, depth) / .02) if min(width, depth) else 0,
            round(max(width, depth) / .02) if max(width, depth) else 0,
            sum(_is_closed(row, _normalise_points(row)) for row in rows),
        )
        group_metrics[root_handle] = {
            "bounds": bounds, "width": width, "depth": depth, "inserted": inserted,
            "signature": signature,
        }
        if inserted:
            signatures.setdefault(signature, []).append(root_handle)
    repeated_roots = {root for roots in signatures.values() if len(roots) >= 3 for root in roots}
    semantic_fixture_anchor_points: list[dict] = []
    for anchor in semantic_anchors:
        if str(anchor.get("semantic_profile") or "") not in {
                "kitchen", "bathroom", "storage"}:
            continue
        point = anchor.get("point_m")
        try:
            point_xy = (float(point[0]), float(point[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if all(math.isfinite(value) for value in point_xy):
            semantic_fixture_anchor_points.append({
                "anchor_id": str(anchor.get("anchor_id") or "")[:120],
                "semantic_profile": str(anchor.get("semantic_profile") or "")[:120],
                "point_m": point_xy,
                "source_handle": str(anchor.get("source_handle") or "")[:120],
            })
    for root_handle, entries in groups.items():
        if root_handle in opening_group_roots:
            continue
        metrics = group_metrics[root_handle]
        width, depth = metrics["width"], metrics["depth"]
        indexes = [index for index, _ in entries]
        rows = [row for _, row in entries]
        circular = all(str(row.get("entity_type") or "") in {"CIRCLE", "ELLIPSE"}
                       for row in rows)
        repeated_compact = (
            root_handle in repeated_roots and len(rows) >= 4
            and max(width, depth) <= 2.0
            and (min(width, depth) <= .25 or len(rows) >= 6)
        )
        inserted_compact_closed = (
            metrics["inserted"] and len(rows) >= 3 and max(width, depth) <= 1.5
            and (any(_is_closed(row, _normalise_points(row)) for row in rows)
                 or min(width, depth) <= .15)
        )
        group_lines = [LineString([first, second]) for row in rows
                       for first, second in _row_segments(row)]
        group_linework = unary_union(group_lines) if group_lines else None
        polygonized_face_count = len(list(polygonize(group_linework))) \
            if group_linework is not None else 0
        external_lines = [line for index, line in all_wall_lines
                          if index not in set(indexes)]
        external_distance = (float(group_linework.distance(unary_union(external_lines)))
                             if group_linework is not None and external_lines else 0.0)
        total_length = sum(float(line.length) for line in group_lines)
        perimeter = max(2.0 * (width + depth), _EPSILON)
        group_axis = _oriented_group_axis(rows)
        group_long_m = max(width, depth)
        group_short_m = min(width, depth)
        group_angle = float(group_axis["angle_deg"])
        diagonal_lines: list[LineString] = []
        for row in rows:
            for first, second in _row_segments(row):
                line = LineString([first, second])
                if line.length < max(.35, group_long_m * .25):
                    continue
                line_angle = _axis_angle(first, second)
                axis_difference = min(
                    _undirected_angle_difference(line_angle, group_angle),
                    _undirected_angle_difference(line_angle, group_angle + 90.0),
                )
                if axis_difference >= 15.0:
                    diagonal_lines.append(line)
        mirrored_diagonal_pair_angles: list[float] = []
        for diagonal_index, first_line in enumerate(diagonal_lines):
            first_angle = _axis_angle(
                tuple(first_line.coords[0]), tuple(first_line.coords[-1]))
            for second_line in diagonal_lines[diagonal_index + 1:]:
                second_angle = _axis_angle(
                    tuple(second_line.coords[0]), tuple(second_line.coords[-1]))
                if _undirected_angle_difference(first_angle, second_angle) < 15.0:
                    continue
                intersection = first_line.intersection(second_line)
                endpoint_distance = min(
                    math.dist(first_point, second_point)
                    for first_point in (
                        tuple(first_line.coords[0]), tuple(first_line.coords[-1]))
                    for second_point in (
                        tuple(second_line.coords[0]), tuple(second_line.coords[-1])))
                if (getattr(intersection, "geom_type", "") == "Point"
                        or endpoint_distance <= .03 + _EPSILON):
                    mirrored_diagonal_pair_angles.append(
                        _undirected_angle_difference(first_angle, second_angle))
        mirrored_diagonal_pair_count = len(mirrored_diagonal_pair_angles)
        circle_marker_count = sum(
            str(row.get("entity_type") or "") in {"CIRCLE", "ELLIPSE"}
            for row in rows)
        dense_isolated_insert = (
            metrics["inserted"] and len(rows) >= 8
            and .25 <= min(width, depth) and max(width, depth) <= 2.5
            and polygonized_face_count >= 3
            and total_length / perimeter >= 1.5
            and external_distance >= .05
        )
        curve_marked_dense_furniture = (
            metrics["inserted"]
            and len(rows) >= 20
            and .80 <= group_short_m <= 2.50
            and group_long_m <= 3.0
            and total_length / perimeter >= 2.0
            and circle_marker_count >= 1
            and mirrored_diagonal_pair_count >= 1
        )
        curve_marked_compact_fixture = (
            metrics["inserted"]
            and len(rows) >= 16
            and .25 <= group_short_m <= group_long_m <= 1.50
            and total_length / perimeter >= 2.0
            and circle_marker_count >= 1
            and polygonized_face_count >= 1
        )
        group_center = ((metrics["bounds"][0] + metrics["bounds"][2]) / 2,
                        (metrics["bounds"][1] + metrics["bounds"][3]) / 2)
        nearby_fixture_anchors = [{
            **anchor,
            "distance_m": math.dist(group_center, anchor["point_m"]),
        } for anchor in semantic_fixture_anchor_points
            if math.dist(group_center, anchor["point_m"]) <= 1.0 + _EPSILON]
        all_line_primitives = all(
            str(row.get("entity_type") or "") == "LINE" for row in rows)
        group_rectangularity = float(group_axis.get("rectangularity") or 0.0)
        semantic_compact_multicell_fixture = (
            metrics["inserted"]
            and all_line_primitives
            and 8 <= len(rows) <= 30
            and .30 <= group_short_m <= group_long_m <= .80
            and group_long_m / max(group_short_m, _EPSILON) <= 1.25
            and group_rectangularity >= .90
            and 2 <= polygonized_face_count <= 8
            and 1.4 <= total_length / perimeter <= 3.0
            and bool(nearby_fixture_anchors)
        )
        parallel_pair_lengths = [float(line.length) for line in group_lines]
        oversized_parallel_insert_frame = False
        oversized_parallel_insert_frame_evidence = None
        if (metrics["inserted"] and len(rows) == len(group_lines) == 2
                and all(str(row.get("entity_type") or "") == "LINE"
                        for row in rows)
                and all(.40 <= length <= 2.50
                        for length in parallel_pair_lengths)):
            first_line, second_line = group_lines
            first_angle = _axis_angle(
                tuple(first_line.coords[0]), tuple(first_line.coords[-1]))
            second_angle = _axis_angle(
                tuple(second_line.coords[0]), tuple(second_line.coords[-1]))
            angle_difference = _undirected_angle_difference(
                first_angle, second_angle)
            separation = float(first_line.distance(second_line))
            length_delta = abs(parallel_pair_lengths[0]
                               - parallel_pair_lengths[1])
            oversized_parallel_insert_frame = bool(
                angle_difference <= 1.0 + _EPSILON
                and .60 + _EPSILON < separation <= 1.50 + _EPSILON
                and length_delta <= .02 + _EPSILON)
            if oversized_parallel_insert_frame:
                oversized_parallel_insert_frame_evidence = {
                    "evidence_kind": "oversized_parallel_insert_frame_v1",
                    "bbox_m": [_round(value) for value in metrics["bounds"]],
                    "rail_lengths_m": [_round(value)
                                       for value in parallel_pair_lengths],
                    "rail_separation_m": _round(separation),
                    "angle_difference_deg": _round(angle_difference),
                    "maximum_wall_thickness_m": .60,
                    "decision_basis": [
                        "single_insert_root_geometry",
                        "two_equal_parallel_rails",
                        "rail_separation_exceeds_supported_wall_thickness",
                        "excluded_from_full_height_wall_geometry",
                    ],
                }
        if circular:
            decision = ("context_fixture", "high", ["closed_circular_detail_not_wall"])
        elif repeated_compact:
            decision = ("context_fixture", "high", ["repeated_compact_geometry_signature"])
        elif dense_isolated_insert:
            decision = ("context_fixture", "high", ["compact_dense_isolated_root_geometry"])
        elif curve_marked_dense_furniture:
            decision = ("context_fixture", "high", [
                "curve_marked_dense_furniture_geometry"])
        elif curve_marked_compact_fixture:
            decision = ("context_fixture", "high", [
                "curve_marked_compact_fixture_geometry"])
        elif semantic_compact_multicell_fixture:
            decision = ("context_fixture", "high", [
                "semantic_compact_multicell_fixture_geometry"])
        elif oversized_parallel_insert_frame:
            decision = ("context_fixture", "high", [
                "oversized_parallel_insert_frame_geometry"])
        elif inserted_compact_closed:
            decision = ("review", "review", ["compact_insert_geometry_requires_review"])
        else:
            continue
        for index in indexes:
            role_by_index[index] = decision
            if oversized_parallel_insert_frame_evidence is not None:
                dense_fixture_evidence_by_index.setdefault(index, []).append(
                    copy.deepcopy(oversized_parallel_insert_frame_evidence))
            if (dense_isolated_insert or curve_marked_dense_furniture
                    or curve_marked_compact_fixture
                    or semantic_compact_multicell_fixture) \
                    and index == indexes[0]:
                dense_fixture_evidence_by_index.setdefault(index, []).append({
                    "evidence_kind": (
                        "compact_dense_isolated_root_v1"
                        if dense_isolated_insert else
                        "curve_marked_dense_furniture_v1"
                        if curve_marked_dense_furniture else
                        "curve_marked_compact_fixture_v1"
                        if curve_marked_compact_fixture else
                        "semantic_compact_multicell_fixture_v1"),
                    "bbox_m": [_round(value) for value in metrics["bounds"]],
                    "primitive_count": len(rows),
                    "segment_count": len(group_lines),
                    "polygonized_face_count": polygonized_face_count,
                    "total_length_m": _round(total_length),
                    "linework_to_bbox_perimeter_ratio": _round(
                        total_length / perimeter),
                    "external_source_line_distance_m": _round(external_distance),
                    "circle_marker_count": circle_marker_count,
                    "diagonal_segment_count": len(diagonal_lines),
                    "mirrored_diagonal_pair_count": mirrored_diagonal_pair_count,
                    "mirrored_diagonal_pair_angles_deg": [
                        _round(value) for value in
                        sorted(mirrored_diagonal_pair_angles)],
                    "group_rectangularity": _round(group_rectangularity),
                    "nearby_semantic_fixture_anchors": [{
                        **{key: value for key, value in anchor.items()
                           if key != "point_m"},
                        "point_m": _point_list(anchor["point_m"]),
                        "distance_m": _round(anchor["distance_m"]),
                    } for anchor in nearby_fixture_anchors],
                    "thresholds": {
                        "minimum_primitive_count": 8,
                        "minimum_polygonized_face_count": 3,
                        "minimum_linework_to_bbox_perimeter_ratio": 1.5,
                        "minimum_external_source_line_distance_m": .05,
                        "maximum_span_m": 2.5,
                        "curve_marked_minimum_primitive_count": 20,
                        "curve_marked_minimum_short_span_m": .80,
                        "curve_marked_maximum_short_span_m": 2.50,
                        "curve_marked_maximum_long_span_m": 3.0,
                        "curve_marked_minimum_linework_to_bbox_perimeter_ratio": 2.0,
                        "curve_marked_minimum_circle_marker_count": 1,
                        "curve_marked_minimum_mirrored_diagonal_pair_count": 1,
                        "curve_marked_minimum_diagonal_pair_angle_deg": 15.0,
                        "semantic_multicell_minimum_primitive_count": 8,
                        "semantic_multicell_maximum_primitive_count": 30,
                        "semantic_multicell_minimum_span_m": .30,
                        "semantic_multicell_maximum_span_m": .80,
                        "semantic_multicell_maximum_aspect_ratio": 1.25,
                        "semantic_multicell_minimum_rectangularity": .90,
                        "semantic_multicell_minimum_polygonized_face_count": 2,
                        "semantic_multicell_maximum_polygonized_face_count": 8,
                        "semantic_multicell_minimum_linework_ratio": 1.4,
                        "semantic_multicell_maximum_linework_ratio": 3.0,
                        "semantic_multicell_maximum_anchor_distance_m": 1.0,
                    },
                    "decision_basis": ([
                        "single_insert_root_geometry",
                        "compact_dense_closed_linework",
                        "isolated_from_external_wall_network",
                    ] if dense_isolated_insert else [
                        "single_insert_root_geometry",
                        "compact_dense_linework",
                        "curve_marker",
                        "mirrored_internal_diagonal_pair",
                        "no_block_layer_colour_or_name_evidence",
                    ] if curve_marked_dense_furniture else [
                        "single_insert_root_geometry",
                        "compact_near_square_multicell_linework",
                        "source_semantic_fixture_anchor_within_one_metre",
                        "kitchen_bathroom_or_storage_semantic_only",
                        "no_block_layer_colour_or_name_evidence",
                    ]),
                })

    # Direct model-space drafting marks often consist of three or four tiny
    # strokes with separate handles (an ``X`` plus a short cap).  A component
    # below 150 mm that contains a genuine crossing diagonal pair cannot be a
    # supported wall centreline: its complete extent is smaller than normal
    # wall thickness.  Classify only the crossing component, never an isolated
    # short cardinal wall cap or an ordinary T junction.
    tiny_rows: list[dict] = []
    for index, row in indexed:
        if role_by_index[index][0] != "wall_face":
            continue
        segments = [LineString([first, second])
                    for first, second in _row_segments(row)
                    if _EPSILON < math.dist(first, second) <= .13 + _EPSILON]
        if len(segments) != 1:
            continue
        tiny_rows.append({"index": index, "row": row, "line": segments[0]})
    tiny_adjacency: dict[int, set[int]] = {
        position: set() for position in range(len(tiny_rows))}
    for position, first in enumerate(tiny_rows):
        for other_position in range(position + 1, len(tiny_rows)):
            second = tiny_rows[other_position]
            if first["line"].distance(second["line"]) <= .012 + _EPSILON:
                tiny_adjacency[position].add(other_position)
                tiny_adjacency[other_position].add(position)
    tiny_visited: set[int] = set()
    for seed in range(len(tiny_rows)):
        if seed in tiny_visited:
            continue
        stack = [seed]
        component_positions: list[int] = []
        while stack:
            current = stack.pop()
            if current in tiny_visited:
                continue
            tiny_visited.add(current)
            component_positions.append(current)
            stack.extend(sorted(tiny_adjacency[current] - tiny_visited,
                                reverse=True))
        component = [tiny_rows[position] for position in component_positions]
        if len(component) < 3:
            continue
        component_lines = [entry["line"] for entry in component]
        component_geometry = unary_union(component_lines)
        bounds = component_geometry.bounds
        span_x = float(bounds[2] - bounds[0])
        span_y = float(bounds[3] - bounds[1])
        if max(span_x, span_y) > .15 + _EPSILON:
            continue
        crossing_pairs = []
        for position, first_line in enumerate(component_lines):
            first_angle = _axis_angle(
                tuple(first_line.coords[0]), tuple(first_line.coords[-1]))
            for second_line in component_lines[position + 1:]:
                second_angle = _axis_angle(
                    tuple(second_line.coords[0]), tuple(second_line.coords[-1]))
                angle_difference = _undirected_angle_difference(
                    first_angle, second_angle)
                intersection = first_line.intersection(second_line)
                first_interior_margin = min(.01, float(first_line.length) * .15)
                second_interior_margin = min(.01, float(second_line.length) * .15)
                interior_crossing = bool(
                    getattr(intersection, "geom_type", "") == "Point"
                    and min(Point(first_line.coords[0]).distance(intersection),
                            Point(first_line.coords[-1]).distance(intersection))
                        >= first_interior_margin - _EPSILON
                    and min(Point(second_line.coords[0]).distance(intersection),
                            Point(second_line.coords[-1]).distance(intersection))
                        >= second_interior_margin - _EPSILON)
                if (25.0 - _EPSILON <= angle_difference <= 155.0 + _EPSILON
                        and interior_crossing):
                    crossing_pairs.append({
                        "angle_difference_deg": _round(angle_difference),
                        "point_m": _point_list((intersection.x, intersection.y)),
                    })
        if not crossing_pairs:
            continue
        proof = {
            "evidence_kind": "micro_cross_marker_v1",
            "source_entity_indexes": sorted(
                int(entry["index"]) for entry in component),
            "bbox_m": [_round(value) for value in bounds],
            "primitive_count": len(component),
            "maximum_primitive_length_m": _round(max(
                float(line.length) for line in component_lines)),
            "total_length_m": _round(sum(
                float(line.length) for line in component_lines)),
            "crossing_pairs": crossing_pairs,
            "thresholds": {
                "minimum_primitive_count": 3,
                "maximum_component_span_m": .15,
                "maximum_primitive_length_m": .13,
                "maximum_join_distance_m": .012,
                "minimum_crossing_angle_deg": 25.0,
            },
            "decision_basis": [
                "multiple_source_roots_form_one_micro_component",
                "genuine_nonparallel_crossing_pair",
                "component_extent_below_supported_wall_scale",
                "excluded_from_full_height_wall_geometry",
            ],
        }
        for entry in component:
            index = int(entry["index"])
            role_by_index[index] = (
                "context_fixture", "high", ["micro_cross_marker_geometry"])
            micro_marker_evidence_by_index[index] = copy.deepcopy(proof)

    # Some CAD writers emit one sanitary/fixture outline as two direct
    # model-space polylines instead of one INSERT: an invalid closed outline
    # (usually a duplicated bowl rim) plus an overlapping open curved companion.
    # Detect only this high-specificity cross-root motif.  A structural circular
    # column or curved wall remains closed and valid, so it cannot satisfy the
    # invalid-closed + open-companion requirement.
    dense_curved_candidates: list[dict] = []
    for index, row in indexed:
        if str(row.get("entity_type") or "") not in {
                "LWPOLYLINE", "POLYLINE"}:
            continue
        points = _normalise_points(row)
        if not 64 <= len(points) <= 256:
            continue
        bounds = _group_bounds([row])
        width = float(bounds[2] - bounds[0])
        depth = float(bounds[3] - bounds[1])
        short_span, long_span = sorted((width, depth))
        if (not .25 <= short_span <= .70
                or not long_span <= .80
                or long_span / max(short_span, _EPSILON) > 2.0):
            continue
        segments = _row_segments(row)
        lengths = [math.dist(first, second) for first, second in segments]
        path_length = sum(lengths)
        if not 1.0 <= path_length <= 5.0 or len(segments) < 63:
            continue
        nonorthogonal_count = sum(
            min(
                _undirected_angle_difference(
                    _axis_angle(first, second), 0.0),
                _undirected_angle_difference(
                    _axis_angle(first, second), 90.0),
            ) > 5.0
            for first, second in segments
            if math.dist(first, second) > _EPSILON
        )
        nonzero_count = sum(length > _EPSILON for length in lengths)
        nonorthogonal_ratio = nonorthogonal_count / max(nonzero_count, 1)
        if nonorthogonal_ratio < .60:
            continue
        closed = _is_closed(row, points)
        invalid_closed = False
        if closed and len(points) >= 4:
            try:
                invalid_closed = not Polygon(_ring_points(points)).is_valid
            except (TypeError, ValueError):
                invalid_closed = True
        dense_curved_candidates.append({
            "index": index, "row": row, "points": points,
            "bounds": bounds,
            "center": ((bounds[0] + bounds[2]) / 2,
                       (bounds[1] + bounds[3]) / 2),
            "width_m": width, "depth_m": depth,
            "path_length_m": path_length,
            "nonorthogonal_ratio": nonorthogonal_ratio,
            "closed": closed, "invalid_closed": invalid_closed,
        })
    consumed_direct_fixture_indexes: set[int] = set()
    for position, first in enumerate(dense_curved_candidates):
        for second in dense_curved_candidates[position + 1:]:
            if not ((first["invalid_closed"] and not second["closed"])
                    or (second["invalid_closed"] and not first["closed"])):
                continue
            if math.dist(first["center"], second["center"]) > .12 + _EPSILON:
                continue
            first_bounds = first["bounds"]
            second_bounds = second["bounds"]
            intersection_width = max(
                0.0, min(first_bounds[2], second_bounds[2])
                - max(first_bounds[0], second_bounds[0]))
            intersection_depth = max(
                0.0, min(first_bounds[3], second_bounds[3])
                - max(first_bounds[1], second_bounds[1]))
            intersection_area = intersection_width * intersection_depth
            first_area = first["width_m"] * first["depth_m"]
            second_area = second["width_m"] * second["depth_m"]
            overlap_ratio = intersection_area / max(
                min(first_area, second_area), _EPSILON)
            if overlap_ratio < .65:
                continue
            fixture_indexes = {int(first["index"]), int(second["index"])}
            for fixture_index in fixture_indexes:
                role_by_index[fixture_index] = (
                    "context_fixture", "high",
                    ["overlapping_invalid_closed_and_open_curved_fixture"])
            evidence = {
                "evidence_kind": "direct_dense_curved_fixture_pair_v1",
                "entity_indexes": sorted(fixture_indexes),
                "bbox_overlap_ratio": _round(overlap_ratio),
                "center_distance_m": _round(math.dist(
                    first["center"], second["center"])),
                "members": [{
                    "entity_index": int(candidate["index"]),
                    "bbox_m": [_round(value) for value in candidate["bounds"]],
                    "point_count": len(candidate["points"]),
                    "path_length_m": _round(candidate["path_length_m"]),
                    "nonorthogonal_segment_ratio": _round(
                        candidate["nonorthogonal_ratio"]),
                    "closed": bool(candidate["closed"]),
                    "invalid_closed": bool(candidate["invalid_closed"]),
                } for candidate in (first, second)],
                "thresholds": {
                    "minimum_point_count": 64,
                    "maximum_point_count": 256,
                    "minimum_short_span_m": .25,
                    "maximum_short_span_m": .70,
                    "maximum_long_span_m": .80,
                    "minimum_nonorthogonal_segment_ratio": .60,
                    "maximum_center_distance_m": .12,
                    "minimum_bbox_overlap_ratio": .65,
                },
                "decision_basis": [
                    "overlapping_compact_dense_curved_source_polylines",
                    "one_invalid_closed_outline_and_one_open_companion",
                    "structural_valid_closed_column_pair_excluded",
                    "no_layer_block_colour_handle_or_name_semantics",
                ],
            }
            evidence_index = min(fixture_indexes)
            dense_fixture_evidence_by_index.setdefault(
                evidence_index, []).append(evidence)
            consumed_direct_fixture_indexes.update(fixture_indexes)

    # Multiple concentric rectangular outlines plus their contained diagonals
    # are a common cabinet/fixture drafting convention.  Treat the whole group
    # as review evidence, never as an automatically extruded wall footprint.
    closed_entries: list[tuple[int, dict, Polygon, tuple[float, float], dict]] = []
    for index, row in indexed:
        points = _normalise_points(row)
        if not _is_closed(row, points) or len(points) < 4:
            continue
        polygon = Polygon(_ring_points(points))
        if not polygon.is_valid or polygon.area <= _EPSILON:
            continue
        bounds = polygon.bounds
        center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        closed_entries.append((index, row, polygon, center, _oriented_group_axis([row])))
    consumed_nested: set[int] = set()
    for position, (index, _, polygon, center, axis) in enumerate(closed_entries):
        if index in consumed_nested:
            continue
        cluster = [(index, polygon, axis)]
        for other_index, _, other_polygon, other_center, other_axis in closed_entries[position + 1:]:
            if other_index in consumed_nested or math.dist(center, other_center) > .04:
                continue
            if _undirected_angle_difference(float(axis["angle_deg"]),
                                            float(other_axis["angle_deg"])) > 2.0:
                continue
            if (polygon.buffer(.01).contains(other_polygon)
                    or other_polygon.buffer(.01).contains(polygon)):
                cluster.append((other_index, other_polygon, other_axis))
        if len(cluster) < 3:
            continue
        outer = max(cluster, key=lambda value: value[1].area)[1]
        outer_axis = max(cluster, key=lambda value: value[1].area)[2]
        if (float(outer_axis["long_m"]) > 3.5 or float(outer_axis["short_m"]) < .25
                or float(outer_axis["rectangularity"]) < .90):
            continue
        nested_indexes = {value[0] for value in cluster}
        internal_line_indexes: set[int] = set()
        for other_index, other_row in indexed:
            if other_index in nested_indexes:
                continue
            segments = _row_segments(other_row)
            if segments and all(outer.buffer(.01).covers(LineString([first, second]))
                                for first, second in segments):
                nested_indexes.add(other_index)
                if not _is_closed(other_row, _normalise_points(other_row)):
                    internal_line_indexes.add(other_index)
        if not internal_line_indexes:
            continue
        for nested_index in nested_indexes:
            role_by_index[nested_index] = (
                "context_fixture", "high", ["nested_compact_closed_contours"])
        consumed_nested.update(nested_indexes)

    # A selected wall-layer row can still be cabinetry when an enclosing
    # rectangular envelope and its drafting detail were split across layers or
    # blocks.  Use selected + nearby context geometry, but require a deliberately
    # high-specificity combination: cabinet-scale elongated envelope, at least
    # two nested contours, and a genuine internal X.  Names, layers and colours
    # are evidence only and never participate in this decision.
    prepared_geometry: list[dict] = []
    for selected_index, row in indexed:
        points = _normalise_points(row)
        segments = [LineString([first, second]) for first, second in _row_segments(row)]
        polygon = None
        if _is_closed(row, points) and len(points) >= 4:
            candidate_polygon = Polygon(_ring_points(points))
            if candidate_polygon.is_valid and candidate_polygon.area > _EPSILON:
                polygon = candidate_polygon
        prepared_geometry.append({
            "selected_index": selected_index, "row": row, "segments": segments,
            "polygon": polygon,
            "bounds": polygon.bounds if polygon is not None else _group_bounds([row]),
        })
    for row in context:
        points = _normalise_points(row)
        segments = [LineString([first, second]) for first, second in _row_segments(row)]
        polygon = None
        if _is_closed(row, points) and len(points) >= 4:
            candidate_polygon = Polygon(_ring_points(points))
            if candidate_polygon.is_valid and candidate_polygon.area > _EPSILON:
                polygon = candidate_polygon
        prepared_geometry.append({
            "selected_index": None, "row": row, "segments": segments,
            "polygon": polygon,
            "bounds": polygon.bounds if polygon is not None else _group_bounds([row]),
        })

    # A fitted kitchen counter is often drawn as an inner L exactly 450--750
    # mm from two perpendicular outer supports.  Treating those inner edges as
    # walls creates small fake rooms.  Equal offset alone is not sufficient --
    # a real thick L-shaped wall has the same geometry.  We therefore require
    # two *independent* compact, curve-rich source groups, one fully contained
    # in each arm of the band (for example two sanitary/worktop symbols).  The
    # decision never reads layer, block, colour or handle semantics.
    rich_fixture_groups: list[dict] = []
    for root_handle, entries in sorted(raw_groups.items()):
        # The proof must come from context geometry, not the candidate wall
        # rows that are about to be classified.
        if any(index in selected_by_index and row is selected_by_index[index]
               for index, row in entries):
            continue
        rows = [row for _, row in entries]
        bounds = _group_bounds(rows)
        width = max(0.0, bounds[2] - bounds[0])
        depth = max(0.0, bounds[3] - bounds[1])
        curved_count = sum(
            str(row.get("entity_type") or
                (row.get("cad_provenance") or {}).get("source_kind") or "").upper()
            in {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
            for row in rows)
        segment_count = sum(len(_row_segments(row)) for row in rows)
        if (len(rows) < 6 or curved_count < 2 or segment_count < 6
                or min(width, depth) < .08 or max(width, depth) > 1.20
                or width * depth < .01 or width * depth > .80):
            continue
        bbox_polygon = Polygon([
            (bounds[0], bounds[1]), (bounds[2], bounds[1]),
            (bounds[2], bounds[3]), (bounds[0], bounds[3]),
        ])
        source = _group_source_fields(rows, [index for index, _ in entries])
        rich_fixture_groups.append({
            "root_handle": root_handle,
            "rows": rows,
            "bbox": bounds,
            "bbox_polygon": bbox_polygon,
            "primitive_count": len(rows),
            "curved_primitive_count": curved_count,
            "segment_count": segment_count,
            "source_handles": source["source_handles"],
            "entity_indexes": source["entity_indexes"],
        })

    selected_single_segments: list[dict] = []
    support_segments: list[dict] = []
    for geometry in prepared_geometry:
        for first, second in _row_segments(geometry["row"]):
            line = LineString([first, second])
            if line.length <= _EPSILON:
                continue
            segment = {
                "selected_index": geometry["selected_index"],
                "row": geometry["row"], "line": line,
                "first": first, "second": second,
                "angle_deg": _axis_angle(first, second),
            }
            support_segments.append(segment)
        if (geometry["selected_index"] is not None
                and len(geometry["segments"]) == 1):
            line = geometry["segments"][0]
            if .75 <= line.length <= 4.0:
                selected_single_segments.append({
                    "selected_index": int(geometry["selected_index"]),
                    "row": geometry["row"], "line": line,
                    "first": tuple(line.coords[0]), "second": tuple(line.coords[-1]),
                    "angle_deg": _axis_angle(
                        tuple(line.coords[0]), tuple(line.coords[-1])),
                })

    def endpoint_connection(first: Mapping[str, Any], second: Mapping[str, Any]
                            ) -> Optional[tuple[float, float]]:
        matches = sorted(
            (math.dist(left, right), left, right)
            for left in (first["first"], first["second"])
            for right in (second["first"], second["second"])
            if math.dist(left, right) <= NODE_SNAP_TOLERANCE_M + _EPSILON)
        if len(matches) != 1:
            return None
        _, left, right = matches[0]
        return ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)

    def projected_overlap_ratio(inner: Any, support: Any) -> float:
        first = tuple(inner.coords[0])
        second = tuple(inner.coords[-1])
        length = inner.length
        unit = ((second[0] - first[0]) / length,
                (second[1] - first[1]) / length)
        values = sorted(
            (point[0] - first[0]) * unit[0] + (point[1] - first[1]) * unit[1]
            for point in (tuple(support.coords[0]), tuple(support.coords[-1])))
        overlap = max(0.0, min(length, values[1]) - max(0.0, values[0]))
        return overlap / length

    def band_between(inner: Any, support: Any) -> Optional[Any]:
        projected = []
        for point in (tuple(inner.coords[0]), tuple(inner.coords[-1])):
            nearest = support.interpolate(support.project(Point(point)))
            projected.append((float(nearest.x), float(nearest.y)))
        polygon = Polygon([
            tuple(inner.coords[0]), tuple(inner.coords[-1]),
            projected[1], projected[0],
        ])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon if not polygon.is_empty and polygon.area > _EPSILON else None

    processed_counter_pairs: set[tuple[int, int]] = set()
    for first_number, inner_first in enumerate(selected_single_segments):
        first_index = int(inner_first["selected_index"])
        if role_by_index[first_index][0] != "wall_face":
            continue
        for inner_second in selected_single_segments[first_number + 1:]:
            second_index = int(inner_second["selected_index"])
            if role_by_index[second_index][0] != "wall_face":
                continue
            pair_signature = tuple(sorted((first_index, second_index)))
            if pair_signature in processed_counter_pairs:
                continue
            if not 89.0 <= _undirected_angle_difference(
                    float(inner_first["angle_deg"]),
                    float(inner_second["angle_deg"])) <= 90.0 + _EPSILON:
                continue
            inner_corner = endpoint_connection(inner_first, inner_second)
            if inner_corner is None:
                continue

            support_options: list[list[dict]] = []
            for inner, own_index in ((inner_first, first_index),
                                     (inner_second, second_index)):
                options: list[dict] = []
                for support in support_segments:
                    if support["selected_index"] == own_index:
                        continue
                    if _undirected_angle_difference(
                            float(inner["angle_deg"]),
                            float(support["angle_deg"])) > 1.0:
                        continue
                    offset = float(inner["line"].distance(support["line"]))
                    if not .45 <= offset <= .75:
                        continue
                    overlap = projected_overlap_ratio(inner["line"], support["line"])
                    if overlap < .90:
                        continue
                    band = band_between(inner["line"], support["line"])
                    if band is None:
                        continue
                    options.append({**support, "offset_m": offset,
                                    "overlap_ratio": overlap, "band": band})
                support_options.append(options)
            if not support_options[0] or not support_options[1]:
                continue

            proofs: list[dict] = []
            for first_support in support_options[0]:
                for second_support in support_options[1]:
                    if first_support["line"].distance(second_support["line"]) > .03:
                        continue
                    if abs(float(first_support["offset_m"])
                           - float(second_support["offset_m"])) > .03:
                        continue
                    arm_matches: list[list[dict]] = []
                    for support in (first_support, second_support):
                        matches = [
                            group for group in rich_fixture_groups
                            if support["band"].buffer(.03).covers(
                                group["bbox_polygon"])
                        ]
                        arm_matches.append(matches)
                    distinct_pairs = [
                        (left, right) for left in arm_matches[0]
                        for right in arm_matches[1]
                        if left["root_handle"] != right["root_handle"]
                    ]
                    if not distinct_pairs:
                        continue
                    distinct_pairs.sort(key=lambda pair: (
                        pair[0]["root_handle"], pair[1]["root_handle"]))
                    fixtures = distinct_pairs[0]
                    proofs.append({
                        "first_support": first_support,
                        "second_support": second_support,
                        "fixtures": fixtures,
                    })
            # Multiple geometrically different outer-support pairs are an
            # ambiguity, not permission to pick the nearest one.
            proof_signatures = {
                tuple(sorted((
                    str(_row_root_handle(proof["first_support"]["row"], 0)),
                    str(_row_root_handle(proof["second_support"]["row"], 0)),
                ))) for proof in proofs
            }
            if len(proofs) != 1 or len(proof_signatures) != 1:
                continue
            proof = proofs[0]
            processed_counter_pairs.add(pair_signature)
            fixtures = proof["fixtures"]
            evidence = {
                "evidence_kind": "curve_rich_fitted_counter_band_v1",
                "inner_entity_indexes": list(pair_signature),
                "inner_source_handles": sorted({
                    handle for row, index in (
                        (inner_first["row"], first_index),
                        (inner_second["row"], second_index))
                    for handle in _group_source_fields([row], [index])[
                        "source_handles"]
                }),
                "inner_corner_m": _point_list(inner_corner),
                "arm_offsets_m": [
                    _round(float(proof["first_support"]["offset_m"])),
                    _round(float(proof["second_support"]["offset_m"])),
                ],
                "outer_support_source_handles": sorted({
                    handle for support in (
                        proof["first_support"], proof["second_support"])
                    for handle in _group_source_fields(
                        [support["row"]],
                        [int(support["row"].get("entity_index", -1))])[
                            "source_handles"]
                }),
                "fixture_groups": [{
                    "source_handles": fixture["source_handles"],
                    "entity_indexes": fixture["entity_indexes"],
                    "bbox_m": [_round(value) for value in fixture["bbox"]],
                    "primitive_count": fixture["primitive_count"],
                    "curved_primitive_count": fixture["curved_primitive_count"],
                    "segment_count": fixture["segment_count"],
                } for fixture in fixtures],
                "thresholds": {
                    "minimum_counter_offset_m": .45,
                    "maximum_counter_offset_m": .75,
                    "maximum_offset_disagreement_m": .03,
                    "minimum_support_overlap_ratio": .90,
                    "minimum_fixture_group_count": 2,
                    "minimum_curved_primitive_count_per_group": 2,
                },
                "decision_basis": [
                    "perpendicular_connected_inner_edges",
                    "equal_offset_perpendicular_outer_supports",
                    "independent_curve_rich_fixture_group_in_each_arm",
                ],
            }
            for index in pair_signature:
                if role_by_index[index][0] != "wall_face":
                    continue
                role_by_index[index] = (
                    "context_fixture", "high",
                    ["fitted_counter_inner_edge_geometry"],
                )
                counter_band_evidence_by_index.setdefault(index, []).append(
                    copy.deepcopy(evidence))

    processed_envelopes: set[tuple[float, ...]] = set()
    for envelope in prepared_geometry:
        envelope_polygon = envelope["polygon"]
        if envelope_polygon is None:
            continue
        axis = _oriented_group_axis([envelope["row"]])
        long_m = float(axis["long_m"])
        short_m = float(axis["short_m"])
        rectangularity = float(axis["rectangularity"])
        if not (.75 <= long_m <= 3.50 and .30 <= short_m <= .75
                and long_m / max(short_m, _EPSILON) >= 1.50
                and rectangularity >= .90):
            continue
        signature = tuple(round(value, 4) for value in (
            *envelope_polygon.bounds, envelope_polygon.area,
            float(axis["angle_deg"]),
        ))
        if signature in processed_envelopes:
            continue
        processed_envelopes.add(signature)
        buffered_envelope = envelope_polygon.buffer(.01)
        min_x, min_y, max_x, max_y = envelope_polygon.bounds
        nested: list[dict] = []
        diagonals: list[dict] = []
        covered_selected_indexes: set[int] = set()
        envelope_angle = float(axis["angle_deg"])
        for geometry in prepared_geometry:
            other_min_x, other_min_y, other_max_x, other_max_y = geometry["bounds"]
            if (other_max_x < min_x - .01 or other_min_x > max_x + .01
                    or other_max_y < min_y - .01 or other_min_y > max_y + .01):
                continue
            other_polygon = geometry["polygon"]
            if (other_polygon is not None and other_polygon is not envelope_polygon
                    and other_polygon.area < envelope_polygon.area * .95
                    and buffered_envelope.covers(other_polygon)):
                nested.append(geometry)
            segments = geometry["segments"]
            if (geometry["selected_index"] is not None and segments
                    and all(buffered_envelope.covers(segment) for segment in segments)):
                covered_selected_indexes.add(int(geometry["selected_index"]))
            for segment in segments:
                if (not buffered_envelope.covers(segment)
                        or segment.length < max(.25, long_m * .30)):
                    continue
                first, second = segment.coords[0], segment.coords[-1]
                segment_angle = _axis_angle(first, second)
                axis_difference = min(
                    _undirected_angle_difference(segment_angle, envelope_angle),
                    _undirected_angle_difference(segment_angle, envelope_angle + 90.0),
                )
                if axis_difference >= 15.0:
                    diagonals.append({"geometry": geometry, "line": segment,
                                      "angle_deg": segment_angle})
        if len(nested) < 2 or len(diagonals) < 2:
            continue
        x_pairs: list[tuple[dict, dict]] = []
        for diagonal_index, first in enumerate(diagonals):
            for second in diagonals[diagonal_index + 1:]:
                if first["line"].equals(second["line"]):
                    continue
                angle_difference = _undirected_angle_difference(
                    float(first["angle_deg"]), float(second["angle_deg"]))
                if angle_difference < 25.0:
                    continue
                intersection = first["line"].intersection(second["line"])
                if (intersection.geom_type == "Point"
                        and envelope_polygon.contains(intersection)
                        and envelope_polygon.boundary.distance(intersection)
                        >= min(.03, short_m * .10)):
                    x_pairs.append((first, second))
        if not x_pairs:
            continue
        envelope_source = _group_source_fields(
            [envelope["row"]], [int(envelope["selected_index"])]
            if envelope["selected_index"] is not None else [])
        nested_handles = sorted({handle for geometry in nested
                                 for handle in _group_source_fields(
                                     [geometry["row"]], [])["source_handles"]})
        diagonal_handles = sorted({handle for pair in x_pairs for diagonal in pair
                                   for handle in _group_source_fields(
                                       [diagonal["geometry"]["row"]], [])["source_handles"]})
        envelope_evidence = {
            "evidence_kind": "compact_fixture_envelope_v1",
            "envelope_source_handles": envelope_source["source_handles"],
            "nested_source_handles": nested_handles,
            "diagonal_source_handles": diagonal_handles,
            "bbox_m": [_round(value) for value in envelope_polygon.bounds],
            "long_span_m": _round(long_m), "short_span_m": _round(short_m),
            "rectangularity": _round(rectangularity),
            "nested_contour_count": len(nested), "internal_x_pair_count": len(x_pairs),
            "decision_basis": ["compact_rectangular_envelope", "nested_contours",
                               "internal_crossing_diagonals", "fully_covered_selected_geometry"],
        }
        for selected_index in covered_selected_indexes:
            if role_by_index[selected_index][0] != "wall_face":
                continue
            role_by_index[selected_index] = (
                "context_fixture", "high", ["compact_fixture_envelope_covered"])
            fixture_envelope_evidence_by_index.setdefault(selected_index, []).append(
                copy.deepcopy(envelope_evidence))

    # Stair treads and drafting hatches are often inherited onto the same
    # layer as the shell.  Adjacent treads satisfy the local parallel-face
    # wall thresholds and used to become a ladder of full-height walls.  A
    # real stair run has a much stronger, name-independent signature: at
    # least six near-equal, regularly spaced parallel single segments whose
    # corresponding endpoints are supported by one continuous perpendicular
    # rail/stringer.  Only the repeated treads are removed; the supporting
    # segment remains available to the wall topology (it may be a real outer
    # wall face beside an exterior stair).  When the opposite stringer is
    # split into multiple collinear pieces, however, the combined pieces and
    # their outboard landing edges are stair evidence: no individual fragment
    # should become a full-height wall merely because it cannot satisfy the
    # single-segment 80% support test.
    tread_rows: list[dict] = []
    stair_split_rows: list[dict] = []
    for index, row in indexed:
        if role_by_index[index][0] != "wall_face":
            continue
        segments = _row_segments(row)
        if len(segments) != 1:
            continue
        first, second = segments[0]
        length = math.dist(first, second)
        if not .45 <= length <= 8.0:
            continue
        candidate = {
            "index": index, "row": row, "first": first, "second": second,
            "length": length, "angle_deg": _axis_angle(first, second),
            "line": LineString([first, second]),
        }
        stair_split_rows.append(candidate)
        if length <= 2.50:
            tread_rows.append(candidate)

    processed_stair_runs: set[tuple[int, ...]] = set()
    for seed in tread_rows:
        angle_radians = math.radians(float(seed["angle_deg"]))
        axis_unit = (math.cos(angle_radians), math.sin(angle_radians))
        axis_normal = (-axis_unit[1], axis_unit[0])

        def projection(point: tuple[float, float], unit: tuple[float, float]) -> float:
            return point[0] * unit[0] + point[1] * unit[1]

        seed_interval = sorted([
            projection(seed["first"], axis_unit),
            projection(seed["second"], axis_unit),
        ])
        aligned: list[dict] = []
        for candidate in tread_rows:
            if _undirected_angle_difference(
                    float(seed["angle_deg"]), float(candidate["angle_deg"])) > 1.0:
                continue
            if not .88 <= float(candidate["length"]) / float(seed["length"]) <= 1.12:
                continue
            interval = sorted([
                projection(candidate["first"], axis_unit),
                projection(candidate["second"], axis_unit),
            ])
            overlap = max(0.0, min(seed_interval[1], interval[1])
                          - max(seed_interval[0], interval[0]))
            if overlap / max(min(float(seed["length"]), float(candidate["length"])),
                             _EPSILON) < .85:
                continue
            midpoint = candidate["line"].interpolate(.5, normalized=True)
            aligned.append({
                **candidate,
                "normal_offset": projection(
                    (float(midpoint.x), float(midpoint.y)), axis_normal),
                "axis_interval": interval,
            })
        aligned.sort(key=lambda value: (value["normal_offset"], value["index"]))
        if len(aligned) < 6:
            continue

        # Split first on architectural tread-spacing bounds, then on spacing
        # regularity.  Missing/irregular lines fail closed instead of being
        # absorbed into a larger run.
        coarse_runs: list[list[dict]] = []
        current: list[dict] = []
        for candidate in aligned:
            if not current:
                current = [candidate]
                continue
            gap = float(candidate["normal_offset"]) - float(current[-1]["normal_offset"])
            if .10 <= gap <= .40:
                current.append(candidate)
            else:
                coarse_runs.append(current)
                current = [candidate]
        if current:
            coarse_runs.append(current)

        for coarse_run in coarse_runs:
            if len(coarse_run) < 6:
                continue
            gaps = [
                float(second["normal_offset"]) - float(first["normal_offset"])
                for first, second in zip(coarse_run, coarse_run[1:])
            ]
            median_spacing = sorted(gaps)[len(gaps) // 2]
            spacing_tolerance = max(.012, median_spacing * .08)
            regular_runs: list[list[dict]] = []
            regular: list[dict] = []
            for position, candidate in enumerate(coarse_run):
                if not regular:
                    regular = [candidate]
                    continue
                previous_gap = gaps[position - 1]
                if abs(previous_gap - median_spacing) <= spacing_tolerance:
                    regular.append(candidate)
                else:
                    regular_runs.append(regular)
                    regular = [candidate]
            if regular:
                regular_runs.append(regular)

            for run in regular_runs:
                if len(run) < 6:
                    continue
                signature = tuple(sorted(int(value["index"]) for value in run))
                if signature in processed_stair_runs:
                    continue
                normal_span = (float(run[-1]["normal_offset"])
                               - float(run[0]["normal_offset"]))
                if not .60 <= normal_span <= 5.00:
                    continue

                # Recover the two corresponding endpoint sets in canonical
                # axis order, then require one perpendicular source segment
                # to support at least 80% of either side.
                low_endpoints: list[tuple[float, float]] = []
                high_endpoints: list[tuple[float, float]] = []
                for value in run:
                    ordered = sorted(
                        (value["first"], value["second"]),
                        key=lambda point: projection(point, axis_unit))
                    low_endpoints.append(ordered[0])
                    high_endpoints.append(ordered[-1])
                own_indexes = set(signature)
                rail_matches: list[dict] = []
                for rail_index, rail_line in all_wall_lines:
                    if rail_index in own_indexes or rail_line.length < normal_span * .80:
                        continue
                    rail_angle = _axis_angle(
                        tuple(rail_line.coords[0]), tuple(rail_line.coords[-1]))
                    if _undirected_angle_difference(
                            float(seed["angle_deg"]), rail_angle) < 80.0:
                        continue
                    for side, endpoints in (("low", low_endpoints),
                                            ("high", high_endpoints)):
                        distances = [float(Point(point).distance(rail_line))
                                     for point in endpoints]
                        support_count = sum(distance <= .03 for distance in distances)
                        if support_count / len(endpoints) < .80:
                            continue
                        rail_row = next((row for index, row in indexed
                                         if index == rail_index), {})
                        rail_matches.append({
                            "entity_index": rail_index,
                            "source_handles": _group_source_fields(
                                [rail_row], [rail_index])["source_handles"],
                            "supported_side": side,
                            "support_count": support_count,
                            "max_supported_endpoint_distance_m": _round(
                                max((distance for distance in distances
                                     if distance <= .03), default=0.0)),
                            "rail_length_m": _round(rail_line.length),
                        })
                if not rail_matches:
                    continue
                rail_match = min(rail_matches, key=lambda value: (
                    -int(value["support_count"]),
                    float(value["max_supported_endpoint_distance_m"]),
                    int(value["entity_index"]),
                ))
                supporting_side = str(rail_match["supported_side"])
                fragmented_side = "high" if supporting_side == "low" else "low"
                fragmented_endpoints = (high_endpoints if fragmented_side == "high"
                                        else low_endpoints)
                fragmented_candidates: list[dict] = []
                for rail_index, rail_line in all_wall_lines:
                    if (rail_index in own_indexes
                            or rail_index == int(rail_match["entity_index"])):
                        continue
                    rail_angle = _axis_angle(
                        tuple(rail_line.coords[0]), tuple(rail_line.coords[-1]))
                    if _undirected_angle_difference(
                            float(seed["angle_deg"]), rail_angle) < 80.0:
                        continue
                    distances = [float(Point(point).distance(rail_line))
                                 for point in fragmented_endpoints]
                    supported = [position for position, distance in enumerate(distances)
                                 if distance <= .03]
                    if not supported:
                        continue
                    fragmented_candidates.append({
                        "entity_index": int(rail_index), "line": rail_line,
                        "supported_positions": supported,
                        "source_handles": _group_source_fields(
                            [next((row for index, row in indexed
                                   if index == rail_index), {})],
                            [rail_index])["source_handles"],
                    })
                fragmented_rail = None
                if len(fragmented_candidates) >= 2:
                    fragmented_union = unary_union([
                        value["line"] for value in fragmented_candidates])
                    combined_distances = [
                        float(Point(point).distance(fragmented_union))
                        for point in fragmented_endpoints]
                    combined_support = sum(
                        distance <= .03 for distance in combined_distances)
                    if combined_support / len(fragmented_endpoints) >= .80:
                        fragmented_rail = {
                            "supported_side": fragmented_side,
                            "entity_indexes": sorted(
                                value["entity_index"]
                                for value in fragmented_candidates),
                            "source_handles": sorted({
                                handle for value in fragmented_candidates
                                for handle in value["source_handles"]
                            }),
                            "segment_count": len(fragmented_candidates),
                            "support_count": combined_support,
                            "max_supported_endpoint_distance_m": _round(max(
                                (distance for distance in combined_distances
                                 if distance <= .03), default=0.0)),
                        }

                landing_edges: list[dict] = []
                partial_landing_splits: list[dict] = []
                if fragmented_rail is not None:
                    fragmented_indexes = set(fragmented_rail["entity_indexes"])
                    fragmented_union = unary_union([
                        value["line"] for value in fragmented_candidates
                        if value["entity_index"] in fragmented_indexes])
                    run_min = float(run[0]["normal_offset"])
                    run_max = float(run[-1]["normal_offset"])
                    maximum_landing_gap = max(1.50, median_spacing * 6.0)
                    for landing in stair_split_rows:
                        landing_index = int(landing["index"])
                        if (landing_index in own_indexes
                                or landing_index in fragmented_indexes
                                or landing_index == int(rail_match["entity_index"])):
                            continue
                        if role_by_index[landing_index][0] != "wall_face":
                            continue
                        if _undirected_angle_difference(
                                float(seed["angle_deg"]),
                                float(landing["angle_deg"])) > 1.0:
                            continue
                        if not (.80 <= float(landing["length"])
                                / max(float(seed["length"]), _EPSILON) <= 2.0):
                            continue
                        interval = sorted([
                            projection(landing["first"], axis_unit),
                            projection(landing["second"], axis_unit),
                        ])
                        overlap = max(0.0, min(seed_interval[1], interval[1])
                                      - max(seed_interval[0], interval[0]))
                        if overlap / max(float(seed["length"]), _EPSILON) < .85:
                            continue
                        midpoint = landing["line"].interpolate(.5, normalized=True)
                        offset = projection(
                            (float(midpoint.x), float(midpoint.y)), axis_normal)
                        gap = (run_min - offset if offset < run_min
                               else offset - run_max if offset > run_max else 0.0)
                        if not median_spacing * 1.5 <= gap <= maximum_landing_gap:
                            continue
                        endpoint_distance = min(
                            float(Point(landing["first"]).distance(fragmented_union)),
                            float(Point(landing["second"]).distance(fragmented_union)),
                        )
                        if endpoint_distance > .03:
                            continue
                        landing_edges.append({
                            "entity_index": landing_index,
                            "source_handles": _group_source_fields(
                                [landing["row"]], [landing_index])["source_handles"],
                            "normal_gap_m": _round(gap),
                            "fragmented_rail_endpoint_distance_m": _round(
                                endpoint_distance),
                        })
                    # A landing line may share its first tread-width with the
                    # stair and then continue as a real house wall.  Treating
                    # the whole LINE as either furniture or structure is
                    # wrong.  Split only a single straight source segment
                    # whose tread-sized terminal interval is fully supported
                    # by the fragmented stair stringer and whose sole long
                    # overhang continues away from the stair.
                    for landing in stair_split_rows:
                        landing_index = int(landing["index"])
                        if (landing_index in own_indexes
                                or landing_index in fragmented_indexes
                                or landing_index == int(rail_match["entity_index"])
                                or landing_index in partial_stair_splits_by_index
                                or role_by_index[landing_index][0] != "wall_face"):
                            continue
                        source_segments = _row_segments(landing["row"])
                        if (len(source_segments) != 1
                                or _is_closed(landing["row"], _normalise_points(
                                    landing["row"]))):
                            continue
                        if _undirected_angle_difference(
                                float(seed["angle_deg"]),
                                float(landing["angle_deg"])) > 1.0:
                            continue
                        if float(landing["length"]) < float(seed["length"]) * 2.0:
                            continue
                        ordered_endpoints = sorted([
                            (projection(landing["first"], axis_unit), landing["first"]),
                            (projection(landing["second"], axis_unit), landing["second"]),
                        ], key=lambda value: value[0])
                        landing_interval = (
                            float(ordered_endpoints[0][0]),
                            float(ordered_endpoints[1][0]))
                        clip_start = max(seed_interval[0], landing_interval[0])
                        clip_end = min(seed_interval[1], landing_interval[1])
                        clip_length = max(0.0, clip_end - clip_start)
                        if clip_length / max(float(seed["length"]), _EPSILON) < .85:
                            continue
                        left_overhang = max(0.0, seed_interval[0] - landing_interval[0])
                        right_overhang = max(0.0, landing_interval[1] - seed_interval[1])
                        left_long = left_overhang >= .30
                        right_long = right_overhang >= .30
                        if left_long == right_long:
                            continue
                        short_overhang = right_overhang if left_long else left_overhang
                        if short_overhang > .03:
                            continue
                        stair_endpoint = (ordered_endpoints[1][1] if left_long
                                          else ordered_endpoints[0][1])
                        endpoint_distance = float(
                            Point(stair_endpoint).distance(fragmented_union))
                        if endpoint_distance > .03:
                            continue
                        midpoint = landing["line"].interpolate(.5, normalized=True)
                        offset = projection(
                            (float(midpoint.x), float(midpoint.y)), axis_normal)
                        gap = (run_min - offset if offset < run_min
                               else offset - run_max if offset > run_max else 0.0)
                        if not median_spacing * .5 <= gap <= maximum_landing_gap:
                            continue

                        low_scalar, low_point = ordered_endpoints[0]
                        high_scalar, high_point = ordered_endpoints[1]

                        def landing_point(scalar: float) -> tuple[float, float]:
                            ratio = ((scalar - low_scalar)
                                     / max(high_scalar - low_scalar, _EPSILON))
                            return (
                                float(low_point[0])
                                + (float(high_point[0]) - float(low_point[0])) * ratio,
                                float(low_point[1])
                                + (float(high_point[1]) - float(low_point[1])) * ratio,
                            )

                        split_scalar = clip_start if left_long else clip_end
                        split_point = landing_point(split_scalar)
                        structural_points = ([low_point, split_point] if left_long
                                             else [split_point, high_point])
                        context_points = ([split_point, high_point] if left_long
                                          else [low_point, split_point])
                        split_evidence = {
                            "entity_index": landing_index,
                            "source_handles": _group_source_fields(
                                [landing["row"]], [landing_index])["source_handles"],
                            "structural_points_m": [
                                [_round(float(point[0])), _round(float(point[1]))]
                                for point in structural_points],
                            "context_points_m": [
                                [_round(float(point[0])), _round(float(point[1]))]
                                for point in context_points],
                            "stair_interval_overlap_m": _round(clip_length),
                            "stair_interval_overlap_ratio": _round(
                                clip_length / max(float(seed["length"]), _EPSILON)),
                            "structural_overhang_m": _round(
                                left_overhang if left_long else right_overhang),
                            "fragmented_rail_endpoint_distance_m": _round(
                                endpoint_distance),
                            "normal_gap_m": _round(gap),
                            "decision_basis": [
                                "single_straight_source_segment",
                                "terminal_tread_width_matches_stair_run",
                                "terminal_endpoint_supported_by_stair_stringer",
                                "single_long_structural_overhang",
                            ],
                        }
                        partial_landing_splits.append(split_evidence)
                        partial_stair_splits_by_index[landing_index] = copy.deepcopy(
                            split_evidence)
                processed_stair_runs.add(signature)
                run_spacing = [
                    float(second["normal_offset"]) - float(first["normal_offset"])
                    for first, second in zip(run, run[1:])
                ]
                evidence = {
                    "evidence_kind": "regular_stair_tread_run_v1",
                    "tread_entity_indexes": list(signature),
                    "tread_source_handles": sorted({
                        handle for value in run
                        for handle in _group_source_fields(
                            [value["row"]], [int(value["index"])])["source_handles"]
                    }),
                    "tread_count": len(run),
                    "median_tread_length_m": _round(sorted(
                        float(value["length"]) for value in run)[len(run) // 2]),
                    "median_spacing_m": _round(sorted(run_spacing)[len(run_spacing) // 2]),
                    "max_spacing_deviation_m": _round(max(
                        abs(value - median_spacing) for value in run_spacing)),
                    "normal_span_m": _round(normal_span),
                    "orientation_deg": _round(float(seed["angle_deg"])),
                    "supporting_rail": rail_match,
                    **({"fragmented_opposite_rail": fragmented_rail}
                       if fragmented_rail is not None else {}),
                    **({"landing_edges": landing_edges} if landing_edges else {}),
                    **({"partial_landing_splits": partial_landing_splits}
                       if partial_landing_splits else {}),
                    "thresholds": {
                        "min_tread_count": 6,
                        "min_spacing_m": .10,
                        "max_spacing_m": .40,
                        "max_angle_difference_deg": 1.0,
                        "min_endpoint_support_ratio": .80,
                        "max_endpoint_distance_m": .03,
                    },
                    "decision_basis": [
                        "repeated_equal_parallel_segments",
                        "regular_architectural_tread_spacing",
                        "continuous_perpendicular_endpoint_support",
                    ],
                }
                for value in run:
                    tread_index = int(value["index"])
                    if role_by_index[tread_index][0] != "wall_face":
                        continue
                    role_by_index[tread_index] = (
                        "context_fixture", "high",
                        ["regular_stair_tread_run", "perpendicular_stair_rail_support"],
                    )
                    stair_run_evidence_by_index.setdefault(tread_index, []).append(
                        copy.deepcopy(evidence))
                if fragmented_rail is not None:
                    for rail_index in fragmented_rail["entity_indexes"]:
                        if role_by_index[rail_index][0] != "wall_face":
                            continue
                        role_by_index[rail_index] = (
                            "context_fixture", "high",
                            ["fragmented_stair_stringer_geometry",
                             "regular_stair_tread_run"],
                        )
                        stair_run_evidence_by_index.setdefault(
                            rail_index, []).append(copy.deepcopy(evidence))
                for landing in landing_edges:
                    landing_index = int(landing["entity_index"])
                    if role_by_index[landing_index][0] != "wall_face":
                        continue
                    role_by_index[landing_index] = (
                        "context_fixture", "high",
                        ["stair_landing_edge_geometry",
                         "fragmented_stair_stringer_geometry"],
                    )
                    stair_run_evidence_by_index.setdefault(
                        landing_index, []).append(copy.deepcopy(evidence))
                for split in partial_landing_splits:
                    split_index = int(split["entity_index"])
                    role, confidence, reasons = role_by_index[split_index]
                    role_by_index[split_index] = (
                        role, confidence, sorted(set(
                            [*reasons, "stair_landing_partial_context_split"])))
                    stair_run_evidence_by_index.setdefault(
                        split_index, []).append(copy.deepcopy(evidence))

    def closed_wall_band_proof(
        entity_index: int, row: Mapping[str, Any], polygon: Any, axis: Mapping[str, Any],
    ) -> Optional[dict]:
        """Prove a thin closed polygon is wall material, not a room/furniture box."""
        segments = [(LineString([first, second]), first, second)
                    for first, second in _row_segments(row)]
        pair_candidates: list[dict] = []
        for left_number, (left, left_first, left_second) in enumerate(segments):
            left_angle = _axis_angle(left_first, left_second)
            radians = math.radians(left_angle)
            unit = (math.cos(radians), math.sin(radians))
            left_projection = sorted(
                point[0] * unit[0] + point[1] * unit[1]
                for point in (left_first, left_second))
            for right_number, (right, right_first, right_second) in enumerate(
                    segments[left_number + 1:], left_number + 1):
                if _undirected_angle_difference(
                        left_angle, _axis_angle(right_first, right_second)) > 1.0:
                    continue
                separation = float(left.distance(right))
                if not .06 <= separation <= .60:
                    continue
                right_projection = sorted(
                    point[0] * unit[0] + point[1] * unit[1]
                    for point in (right_first, right_second))
                overlap = max(0.0, min(left_projection[1], right_projection[1])
                              - max(left_projection[0], right_projection[0]))
                if overlap < .20 - _EPSILON:
                    continue
                pair_candidates.append({
                    "separation_m": separation,
                    "overlap_m": overlap,
                    "edge_indexes": [left_number, right_number],
                })
        if not pair_candidates:
            return None
        cluster_options = []
        for seed in pair_candidates:
            members = [value for value in pair_candidates
                       if abs(value["separation_m"]
                              - seed["separation_m"]) <= .015 + _EPSILON]
            total_overlap = sum(value["overlap_m"] for value in members)
            thickness = sum(value["separation_m"] * value["overlap_m"]
                            for value in members) / total_overlap
            cluster_options.append((total_overlap, -abs(thickness - .20),
                                    thickness, members))
        _overlap_score, _thickness_score, thickness, members = max(
            cluster_options, key=lambda value: (value[0], value[1]))
        dominant_overlap = sum(value["overlap_m"] for value in members)
        total_pair_overlap = sum(value["overlap_m"] for value in pair_candidates)
        fill_ratio = float(polygon.area / polygon.minimum_rotated_rectangle.area) \
            if polygon.minimum_rotated_rectangle.area > _EPSILON else 1.0
        paired_area_ratio = dominant_overlap * thickness / float(polygon.area)
        is_short_rectangle = bool(
            float(axis["rectangularity"]) >= .90
            and .35 <= float(axis["long_m"]) < 1.0
            and .06 <= float(axis["short_m"]) <= .60
            and float(axis["long_m"]) / max(float(axis["short_m"]), _EPSILON)
            >= 3.0)
        endpoint_supports = []
        if is_short_rectangle:
            for endpoint_number, endpoint in enumerate(axis["axis"]):
                matches = []
                for support_index, support_line in all_wall_lines:
                    if support_index == entity_index:
                        continue
                    distance = float(Point(endpoint).distance(support_line))
                    if distance <= .03 + _EPSILON:
                        matches.append({"entity_index": int(support_index),
                                        "distance_m": _round(distance)})
                if matches:
                    matches.sort(key=lambda value: (
                        value["distance_m"], value["entity_index"]))
                    endpoint_supports.append({
                        "endpoint_index": endpoint_number,
                        **matches[0],
                    })
        complex_band = bool(
            fill_ratio <= .60 + _EPSILON
            and dominant_overlap >= .50 - _EPSILON
            and dominant_overlap / max(total_pair_overlap, _EPSILON) >= .60
            and .45 <= paired_area_ratio <= 1.80)
        if not complex_band and not (is_short_rectangle and endpoint_supports):
            return None
        return {
            "method": "cad_closed_uniform_wall_band_v1",
            "wall_thickness_m": _round(thickness),
            "representative_centerline_m": [
                _point_list(tuple(point)) for point in axis["axis"]],
            "wall_face_pair_count": len(members),
            "dominant_paired_overlap_m": _round(dominant_overlap),
            "total_pair_overlap_m": _round(total_pair_overlap),
            "dominant_overlap_ratio": _round(
                dominant_overlap / max(total_pair_overlap, _EPSILON)),
            "paired_area_ratio": _round(paired_area_ratio),
            "polygon_fill_ratio": _round(fill_ratio),
            "short_rectangle": is_short_rectangle,
            "endpoint_supports": endpoint_supports,
            "thresholds": {
                "minimum_wall_thickness_m": .06,
                "maximum_wall_thickness_m": .60,
                "maximum_thickness_cluster_delta_m": .015,
                "minimum_edge_pair_overlap_m": .20,
                "minimum_complex_band_overlap_m": .50,
                "maximum_complex_band_fill_ratio": .60,
                "minimum_dominant_overlap_ratio": .60,
                "minimum_paired_area_ratio": .45,
                "maximum_paired_area_ratio": 1.80,
                "minimum_short_rectangle_length_m": .35,
                "maximum_short_rectangle_length_m": 1.0,
                "minimum_short_rectangle_aspect_ratio": 3.0,
                "maximum_endpoint_support_distance_m": .03,
            },
            "decision_basis": ([
                "low_fill_closed_polygon",
                "dominant_repeated_opposing_wall_faces",
                "paired_wall_area_explains_source_polygon",
            ] if complex_band else [
                "thin_rectangular_closed_wall_band",
                "endpoint_connected_to_independent_structural_source",
            ]),
        }

    # A closed polyline is not automatically a wall: cabinetry, sanitary
    # fixtures and columns are commonly authored on inherited wall layers.
    # Auto-accept only a high-specificity elongated rectangular wall band.
    # Compact/square/complex footprints stay review evidence and therefore do
    # not become visible 3D walls until a person or stronger topology proves
    # their structural role.
    for index, row in indexed:
        if role_by_index[index][0] != "wall_face":
            continue
        points = _normalise_points(row)
        if not _is_closed(row, points) or len(points) < 4:
            continue
        polygon = Polygon(_ring_points(points))
        if not polygon.is_valid or polygon.area <= _EPSILON:
            continue
        axis = _oriented_group_axis([row])
        long_m = float(axis["long_m"])
        short_m = float(axis["short_m"])
        rectangularity = float(axis["rectangularity"])
        if (long_m >= 1.0 and .06 <= short_m <= .60
                and long_m / max(short_m, _EPSILON) >= 3.0
                and rectangularity >= .90):
            role_by_index[index] = (
                "wall_footprint", "high", ["closed_elongated_wall_band_geometry"])
        elif (band_proof := closed_wall_band_proof(
                index, row, polygon, axis)) is not None:
            row["closed_wall_band_evidence"] = copy.deepcopy(band_proof)
            closed_wall_band_evidence_by_index[index] = copy.deepcopy(band_proof)
            role_by_index[index] = (
                "wall_footprint", "high", ["closed_uniform_wall_band_geometry"])
        elif long_m >= 1.50 and short_m >= 1.0 and polygon.area >= 2.0:
            # A large closed contour on a wall source is useful perimeter/room
            # evidence, but it is not proof that the whole polygon is a solid
            # wall footprint.  Retain its edges for the editable draft while
            # making the WallAssembly fail closed for production.
            row["wall_footprint_review_required"] = True
            role_by_index[index] = (
                "wall_footprint", "review", ["closed_perimeter_wall_role_unproven"])
        else:
            role_by_index[index] = (
                "review", "review", ["closed_geometry_wall_band_unproven"])

    # Several production DWGs repeat the complete exterior and interior face
    # of the perimeter wall (for plot/display purposes).  A single large
    # rectangle is not enough to prove wall material; it may be a room, crop
    # frame or drawing border.  Promote a perimeter shell only when BOTH face
    # polygons have independent duplicate source entities, are four-sided
    # near-exact rectangles, and their stable inset directly measures a valid
    # wall thickness.  The duplicated source contours become audit evidence;
    # eight source-derived edge fragments are offered to the ordinary paired
    # face matcher, so the normal 1 degree / 60--600 mm / 80% gates still own
    # the final wall decision.
    perimeter_candidates: list[dict] = []
    semantic_perimeter_candidates: list[dict] = []
    for index, row in indexed:
        if "closed_perimeter_wall_role_unproven" not in role_by_index[index][2]:
            continue
        points = _normalise_points(row)
        polygon = Polygon(_ring_points(points))
        if not polygon.is_valid or polygon.area <= _EPSILON:
            continue
        coordinates = list(polygon.exterior.coords)[:-1]
        rectangle = polygon.minimum_rotated_rectangle
        rectangularity = (float(polygon.area / rectangle.area)
                          if rectangle.area > _EPSILON else 0.0)
        edge_angles = [
            _axis_angle(first, second)
            for first, second in zip(
                coordinates, coordinates[1:] + coordinates[:1])
            if math.dist(first, second) > _EPSILON
        ]
        orthogonal_deviations = [
            min(angle % 90.0, 90.0 - (angle % 90.0))
            for angle in edge_angles
        ]
        if (4 <= len(coordinates) <= 16
                and len(edge_angles) == len(coordinates)
                and max(orthogonal_deviations, default=90.0) <= 1.0 + _EPSILON):
            semantic_perimeter_candidates.append({
                "index": index, "row": row, "polygon": polygon,
                "coordinates": [tuple(point) for point in coordinates],
                "rectangularity": rectangularity,
                "axis": _oriented_group_axis([row]),
                "edge_angles_deg": edge_angles,
                "maximum_orthogonal_deviation_deg": max(
                    orthogonal_deviations, default=0.0),
            })
        if len(coordinates) != 4 or rectangularity < .995 - _EPSILON:
            continue
        perimeter_candidates.append({
            "index": index, "row": row, "polygon": polygon,
            "coordinates": [tuple(point) for point in coordinates],
            "rectangularity": rectangularity,
            "axis": _oriented_group_axis([row]),
        })

    # A multi-view construction sheet can place dimension frames and long
    # extension axes on the same raw source as the floor plan.  Prove the
    # building envelope without layer/name heuristics: exactly one pair of
    # 4--16-edge orthogonal nested contours must have a uniform 60--600 mm
    # inset, and the inner face must contain at least three source text anchors
    # spanning two room semantics.  The pair remains ordinary wall evidence;
    # it is used only to keep annotation scaffolding outside/crossing it out of
    # topology.
    normalised_semantic_anchors: list[dict] = []
    for anchor in semantic_anchors:
        point = anchor.get("point_m")
        profile = str(anchor.get("semantic_profile") or "").strip()
        try:
            point_xy = (float(point[0]), float(point[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if not profile or not all(math.isfinite(value) for value in point_xy):
            continue
        normalised_semantic_anchors.append({
            "anchor_id": str(anchor.get("anchor_id") or "")[:120],
            "semantic_profile": profile[:120],
            "reference_profile": str(
                anchor.get("reference_profile") or "")[:120],
            "point_m": point_xy,
            "source_handle": str(anchor.get("source_handle") or "")[:120],
        })
    semantic_envelope_pairs: list[dict] = []
    semantic_pair_decisions: list[dict] = []
    for outer in semantic_perimeter_candidates:
        for inner in semantic_perimeter_candidates:
            if outer["index"] == inner["index"]:
                continue
            decision = {
                "outer_entity_index": int(outer["index"]),
                "inner_entity_index": int(inner["index"]),
                "outer_source_handles": _group_source_fields(
                    [outer["row"]], [outer["index"]])["source_handles"],
                "inner_source_handles": _group_source_fields(
                    [inner["row"]], [inner["index"]])["source_handles"],
                "rejection_reasons": [],
            }
            outer_polygon = outer["polygon"]
            inner_polygon = inner["polygon"]
            if not outer_polygon.contains(inner_polygon):
                decision["rejection_reasons"].append(
                    "outer_does_not_strictly_contain_inner")
                semantic_pair_decisions.append(decision)
                continue
            angle_difference = _undirected_angle_difference(
                float(outer["axis"]["angle_deg"]),
                float(inner["axis"]["angle_deg"]))
            decision["axis_angle_difference_deg"] = _round(angle_difference)
            if angle_difference > 1.0 + _EPSILON:
                decision["rejection_reasons"].append(
                    "axis_angle_difference_exceeds_threshold")
                semantic_pair_decisions.append(decision)
                continue
            inner_edges = list(zip(
                inner["coordinates"],
                inner["coordinates"][1:] + inner["coordinates"][:1]))
            inset_samples = [float(Point((
                (first[0] + second[0]) / 2,
                (first[1] + second[1]) / 2,
            )).distance(outer_polygon.exterior))
                for first, second in inner_edges]
            decision["inset_samples_m"] = [
                _round(value) for value in inset_samples]
            if (not inset_samples
                    or min(inset_samples) < MIN_FACE_SEPARATION_M - _EPSILON
                    or max(inset_samples) > MAX_FACE_SEPARATION_M + _EPSILON
                    or max(inset_samples) - min(inset_samples) > .02 + _EPSILON):
                decision["rejection_reasons"].append(
                    "inset_samples_outside_wall_thickness_contract")
                semantic_pair_decisions.append(decision)
                continue
            contained_anchors = [
                anchor for anchor in normalised_semantic_anchors
                if inner_polygon.buffer(.01).covers(Point(anchor["point_m"]))
            ]
            semantic_profiles = sorted({
                anchor["semantic_profile"] for anchor in contained_anchors})
            decision["contained_semantic_anchor_count"] = len(contained_anchors)
            decision["contained_semantic_profiles"] = semantic_profiles
            if len(contained_anchors) < 3 or len(semantic_profiles) < 2:
                decision["rejection_reasons"].append(
                    "insufficient_contained_room_semantics")
                semantic_pair_decisions.append(decision)
                continue
            decision["accepted"] = True
            semantic_pair_decisions.append(decision)
            semantic_envelope_pairs.append({
                "outer": outer,
                "inner": inner,
                "inset_samples": inset_samples,
                "contained_anchors": contained_anchors,
                "semantic_profiles": semantic_profiles,
                "angle_difference": angle_difference,
            })
    semantic_building_envelope_diagnostics = {
        "schema_version": 1,
        "method": "cad_semantic_nested_building_envelope_diagnostics_v1",
        "status": ("unique_pair_proved" if len(semantic_envelope_pairs) == 1
                   else "unproved"),
        "semantic_anchor_count": len(normalised_semantic_anchors),
        "semantic_profiles": sorted({
            anchor["semantic_profile"] for anchor in normalised_semantic_anchors}),
        "orthogonal_perimeter_candidate_count": len(
            semantic_perimeter_candidates),
        "orthogonal_perimeter_candidates": [{
            "entity_index": int(candidate["index"]),
            "source_handles": _group_source_fields(
                [candidate["row"]], [candidate["index"]])["source_handles"],
            "edge_count": len(candidate["coordinates"]),
            "rectangularity": _round(candidate["rectangularity"]),
            "maximum_orthogonal_deviation_deg": _round(
                candidate["maximum_orthogonal_deviation_deg"]),
            "area_m2": _round(candidate["polygon"].area),
        } for candidate in semantic_perimeter_candidates],
        "accepted_pair_count": len(semantic_envelope_pairs),
        "pair_decisions": semantic_pair_decisions[:100],
        "pair_decisions_truncated": len(semantic_pair_decisions) > 100,
    }
    if len(semantic_envelope_pairs) == 1:
        envelope_pair = semantic_envelope_pairs[0]
        envelope_outer = envelope_pair["outer"]
        envelope_inner = envelope_pair["inner"]
        semantic_building_envelope_evidence = {
            "schema_version": 1,
            "method": "cad_semantic_nested_building_envelope_v1",
            "status": "proved",
            "outer_entity_index": int(envelope_outer["index"]),
            "inner_entity_index": int(envelope_inner["index"]),
            "outer_source_handles": _group_source_fields(
                [envelope_outer["row"]], [envelope_outer["index"]]
            )["source_handles"],
            "inner_source_handles": _group_source_fields(
                [envelope_inner["row"]], [envelope_inner["index"]]
            )["source_handles"],
            "outer_polygon_m": [
                _point_list(point) for point in envelope_outer["coordinates"]],
            "inner_polygon_m": [
                _point_list(point) for point in envelope_inner["coordinates"]],
            "inset_samples_m": [
                _round(value) for value in envelope_pair["inset_samples"]],
            "measured_wall_thickness_m": _round(sum(
                envelope_pair["inset_samples"]
            ) / len(envelope_pair["inset_samples"])),
            "axis_angle_difference_deg": _round(
                envelope_pair["angle_difference"]),
            "outer_edge_count": len(envelope_outer["coordinates"]),
            "inner_edge_count": len(envelope_inner["coordinates"]),
            "outer_maximum_orthogonal_deviation_deg": _round(
                envelope_outer["maximum_orthogonal_deviation_deg"]),
            "inner_maximum_orthogonal_deviation_deg": _round(
                envelope_inner["maximum_orthogonal_deviation_deg"]),
            "semantic_anchor_count": len(envelope_pair["contained_anchors"]),
            "semantic_profiles": envelope_pair["semantic_profiles"],
            "semantic_anchors": [{
                **{key: value for key, value in anchor.items()
                   if key != "point_m"},
                "point_m": _point_list(anchor["point_m"]),
            } for anchor in envelope_pair["contained_anchors"]],
            "candidate_pair_count": 1,
            "thresholds": {
                "minimum_wall_thickness_m": MIN_FACE_SEPARATION_M,
                "maximum_wall_thickness_m": MAX_FACE_SEPARATION_M,
                "maximum_inset_spread_m": .02,
                "maximum_axis_angle_difference_deg": 1.0,
                "minimum_orthogonal_edge_count": 4,
                "maximum_orthogonal_edge_count": 16,
                "maximum_edge_orthogonal_deviation_deg": 1.0,
                "minimum_semantic_anchor_count": 3,
                "minimum_semantic_profile_count": 2,
                "semantic_boundary_tolerance_m": .01,
            },
            "decision_basis": [
                "unique_nested_orthogonal_face_pair",
                "uniform_source_measured_wall_inset",
                "multiple_source_room_anchors_inside_inner_face",
                "multiple_room_semantic_profiles",
                "no_layer_block_colour_or_filename_evidence",
            ],
        }

    duplicate_groups: list[list[dict]] = []
    remaining_perimeters = list(perimeter_candidates)
    while remaining_perimeters:
        representative = remaining_perimeters.pop(0)
        group = [representative]
        for candidate in list(remaining_perimeters):
            boundary_delta = float(
                representative["polygon"].exterior.hausdorff_distance(
                    candidate["polygon"].exterior))
            symmetric_area = float(
                representative["polygon"].symmetric_difference(
                    candidate["polygon"]).area)
            if boundary_delta <= .002 + _EPSILON and symmetric_area <= .002:
                group.append(candidate)
                remaining_perimeters.remove(candidate)
        if len(group) >= 2:
            duplicate_groups.append(sorted(group, key=lambda value: value["index"]))

    used_perimeter_groups: set[int] = set()
    next_shell_index = max(used_indexes, default=-1) + 1
    for outer_group_number, outer_group in sorted(
            enumerate(duplicate_groups),
            key=lambda item: -float(item[1][0]["polygon"].area)):
        if outer_group_number in used_perimeter_groups:
            continue
        outer = outer_group[0]
        viable_inner_groups: list[tuple[int, list[dict], list[float]]] = []
        for inner_group_number, inner_group in enumerate(duplicate_groups):
            if (inner_group_number == outer_group_number
                    or inner_group_number in used_perimeter_groups):
                continue
            inner = inner_group[0]
            if not outer["polygon"].contains(inner["polygon"]):
                continue
            angle_difference = _undirected_angle_difference(
                float(outer["axis"]["angle_deg"]),
                float(inner["axis"]["angle_deg"]))
            if angle_difference > 1.0 + _EPSILON:
                continue
            inset_samples = [float(Point(point).distance(
                outer["polygon"].exterior)) for point in inner["coordinates"]]
            if (not inset_samples
                    or min(inset_samples) < MIN_FACE_SEPARATION_M - _EPSILON
                    or max(inset_samples) > MAX_FACE_SEPARATION_M + _EPSILON
                    or max(inset_samples) - min(inset_samples) > .02 + _EPSILON):
                continue
            viable_inner_groups.append(
                (inner_group_number, inner_group, inset_samples))
        # Multiple possible inner shells are not guessed.  They may represent
        # a second wall system, finish layer or plot frame and need review.
        if len(viable_inner_groups) != 1:
            continue
        inner_group_number, inner_group, inset_samples = viable_inner_groups[0]
        inner = inner_group[0]
        thickness = sum(inset_samples) / len(inset_samples)
        shell_id = f"cad_duplicate_perimeter_shell_{len(perimeter_wall_shell_proofs) + 1}"
        source_rows = [value["row"] for value in outer_group + inner_group]
        source_indexes = [int(value["index"])
                          for value in outer_group + inner_group]
        source_fields = _group_source_fields(source_rows, source_indexes)
        proof = {
            "method": "cad_duplicate_nested_perimeter_wall_shell_v1",
            "shell_id": shell_id,
            "outer_duplicate_count": len(outer_group),
            "inner_duplicate_count": len(inner_group),
            "outer_entity_indexes": [int(value["index"])
                                     for value in outer_group],
            "inner_entity_indexes": [int(value["index"])
                                     for value in inner_group],
            "outer_source_handles": _group_source_fields(
                [value["row"] for value in outer_group],
                [int(value["index"]) for value in outer_group])["source_handles"],
            "inner_source_handles": _group_source_fields(
                [value["row"] for value in inner_group],
                [int(value["index"]) for value in inner_group])["source_handles"],
            "outer_polygon_m": [_point_list(point)
                                for point in outer["coordinates"]],
            "inner_polygon_m": [_point_list(point)
                                for point in inner["coordinates"]],
            "outer_rectangularity": _round(outer["rectangularity"]),
            "inner_rectangularity": _round(inner["rectangularity"]),
            "axis_angle_difference_deg": _round(_undirected_angle_difference(
                float(outer["axis"]["angle_deg"]),
                float(inner["axis"]["angle_deg"]))),
            "inset_samples_m": [_round(value) for value in inset_samples],
            "measured_wall_thickness_m": _round(thickness),
            "source": source_fields,
            "thresholds": {
                "minimum_duplicate_count_per_face": 2,
                "maximum_duplicate_boundary_delta_m": .002,
                "maximum_duplicate_symmetric_difference_m2": .002,
                "minimum_rectangularity": .995,
                "minimum_wall_thickness_m": MIN_FACE_SEPARATION_M,
                "maximum_wall_thickness_m": MAX_FACE_SEPARATION_M,
                "maximum_inset_spread_m": .02,
                "maximum_axis_angle_difference_deg": 1.0,
            },
            "decision_basis": [
                "outer_face_has_independent_duplicate_sources",
                "inner_face_has_independent_duplicate_sources",
                "four_sided_nested_parallel_rectangles",
                "stable_inset_measures_wall_thickness",
                "ordinary_paired_face_thresholds_still_required",
            ],
        }
        for value in outer_group + inner_group:
            index = int(value["index"])
            role_by_index[index] = (
                "structural_evidence", "high",
                ["duplicate_nested_perimeter_wall_shell_geometry"])
            perimeter_wall_shell_evidence_by_index[index] = copy.deepcopy(proof)
        for face_kind, representative in (("outer", outer), ("inner", inner)):
            coordinates = representative["coordinates"]
            for edge_index, (first, second) in enumerate(zip(
                    coordinates, coordinates[1:] + coordinates[:1])):
                fragment = copy.deepcopy(representative["row"])
                fragment["entity_index"] = next_shell_index
                next_shell_index += 1
                fragment["points"] = [tuple(first), tuple(second)]
                fragment["closed"] = False
                fragment.pop("wall_footprint_review_required", None)
                fragment.pop("closed_wall_band_evidence", None)
                fragment["perimeter_wall_shell_fragment_evidence"] = {
                    "shell_id": shell_id,
                    "face_kind": face_kind,
                    "edge_index": edge_index,
                    "measured_wall_thickness_m": _round(thickness),
                }
                provenance = fragment.get("cad_provenance")
                if not isinstance(provenance, dict):
                    provenance = {}
                    fragment["cad_provenance"] = provenance
                provenance["geometry_clip"] = {
                    "method": "duplicate_nested_perimeter_edge_fragment_v1",
                    "shell_id": shell_id,
                    "face_kind": face_kind,
                    "edge_index": edge_index,
                    "source_segment_m": [
                        _point_list(first), _point_list(second)],
                    "all_shell_source_handles": copy.deepcopy(
                        source_fields["source_handles"]),
                }
                perimeter_wall_shell_rows.append(fragment)
        perimeter_wall_shell_proofs.append(proof)
        used_perimeter_groups.update(
            {outer_group_number, inner_group_number})

    # Geometry-only plan selection intentionally prefers the tight room-anchor
    # view over a whole drawing sheet.  A genuine perimeter segment can sit
    # just outside that view (for example on the far side of a window or fitted
    # counter) even though both of its endpoints are tied into the retained
    # structure.  Recover only a singleton context segment whose two endpoints
    # are independently supported by two distinct retained wall rows.  Plot
    # borders, dimension rails, furniture groups and one-ended loose lines fail
    # this proof.  Layer/block/name never participates in the decision.
    retained_supports: list[dict] = []
    for index, row in indexed:
        if role_by_index[index][0] not in {"wall_face", "wall_footprint"}:
            continue
        for first, second in _row_segments(row):
            line = LineString([first, second])
            if line.length > _EPSILON:
                retained_supports.append({
                    "index": index, "line": line, "row": row})
    # Do not let derived perimeter fragments bootstrap unrelated context rows
    # into walls.  They are authoritative only for their own paired-face
    # shell; generic endpoint-bridge recovery must still be supported by
    # independently selected source wall rows.
    supplemental_wall_rows: list[dict] = []
    supplemental_wall_evidence: list[dict] = []
    supplemental_context_evidence: list[dict] = []
    bridge_candidates: list[dict] = []
    for root_handle, entries in sorted(raw_groups.items()):
        if len(entries) != 1 or root_handle in opening_group_roots:
            continue
        context_index, row = entries[0]
        if context_index in selected_by_index and row is selected_by_index[context_index]:
            continue
        segments = _row_segments(row)
        if len(segments) != 1:
            continue
        first, second = segments[0]
        line = LineString([first, second])
        if not .35 <= line.length <= 5.0:
            continue
        endpoint_supports: list[list[dict]] = []
        for endpoint in (first, second):
            matches = [{
                "entity_index": int(support["index"]),
                "distance_m": float(Point(endpoint).distance(support["line"])),
                "source_handles": _group_source_fields(
                    [support["row"]],
                    [int(support["index"])])["source_handles"],
            } for support in retained_supports
                if Point(endpoint).distance(support["line"])
                <= NODE_SNAP_TOLERANCE_M + _EPSILON]
            endpoint_supports.append(sorted(matches, key=lambda value: (
                value["distance_m"], value["entity_index"])))
        if not endpoint_supports[0] or not endpoint_supports[1]:
            continue
        independent_pairs = [
            (left, right) for left in endpoint_supports[0]
            for right in endpoint_supports[1]
            if left["entity_index"] != right["entity_index"]
        ]
        if not independent_pairs:
            continue
        independent_pairs.sort(key=lambda pair: (
            pair[0]["distance_m"] + pair[1]["distance_m"],
            pair[0]["entity_index"], pair[1]["entity_index"]))
        supports = independent_pairs[0]
        bridge_candidates.append({
            "root_handle": root_handle,
            "context_index": context_index,
            "row": row,
            "line": line,
            "first": first,
            "second": second,
            "supports": supports,
        })

    # A two-ended context bridge is not automatically a wall.  Fitted
    # furniture frequently spans between two real walls and therefore passes
    # the endpoint test.  Reject that false promotion only when the context
    # itself supplies a second, almost identical rail whose separation cannot
    # be a supported wall thickness.  A 60--600 mm pair remains eligible as a
    # genuine omitted wall band.  The very narrow paired-rail case may also own
    # two mirrored sloping sides (a trapezoidal furniture/front symbol); those
    # sides are context as one inseparable geometric motif.
    excluded_bridge_indexes: set[int] = set()
    nonwall_band_proofs: dict[int, dict] = {}

    def aligned_parallel_pair(left: Mapping[str, Any],
                              right: Mapping[str, Any]) -> Optional[dict]:
        left_line = left["line"]
        right_line = right["line"]
        if (_undirected_angle_difference(
                _axis_angle(tuple(left_line.coords[0]), tuple(left_line.coords[-1])),
                _axis_angle(tuple(right_line.coords[0]), tuple(right_line.coords[-1])))
                > 1.0 + _EPSILON):
            return None
        length_delta = abs(float(left_line.length) - float(right_line.length))
        if length_delta > max(.02, min(left_line.length, right_line.length) * .02):
            return None
        direct = max(
            math.dist(tuple(left_line.coords[0]), tuple(right_line.coords[0])),
            math.dist(tuple(left_line.coords[-1]), tuple(right_line.coords[-1])))
        reverse = max(
            math.dist(tuple(left_line.coords[0]), tuple(right_line.coords[-1])),
            math.dist(tuple(left_line.coords[-1]), tuple(right_line.coords[0])))
        endpoint_correspondence = min(direct, reverse)
        separation = float(left_line.distance(right_line))
        if endpoint_correspondence > separation + .02 + _EPSILON:
            return None
        if not (separation <= .05 + _EPSILON
                or .61 - _EPSILON <= separation <= 1.20 + _EPSILON):
            return None
        return {
            "separation_m": _round(separation),
            "length_delta_m": _round(length_delta),
            "endpoint_correspondence_m": _round(endpoint_correspondence),
            "nonwall_width_class": (
                "too_narrow_for_wall_band" if separation <= .05 + _EPSILON
                else "too_wide_for_wall_band"),
        }

    narrow_pairs: list[tuple[dict, dict, dict]] = []
    for left_number, left in enumerate(bridge_candidates):
        for right in bridge_candidates[left_number + 1:]:
            proof = aligned_parallel_pair(left, right)
            if proof is None:
                continue
            for member, other in ((left, right), (right, left)):
                index = int(member["context_index"])
                excluded_bridge_indexes.add(index)
                nonwall_band_proofs[index] = {
                    "method": "paired_context_nonwall_band_v1",
                    "paired_entity_index": int(other["context_index"]),
                    "paired_source_handles": _group_source_fields(
                        [other["row"]], [int(other["context_index"])])["source_handles"],
                    **copy.deepcopy(proof),
                    "thresholds": {
                        "maximum_axis_angle_difference_deg": 1.0,
                        "maximum_length_difference_m": max(
                            .02, _round(min(left["line"].length,
                                            right["line"].length) * .02)),
                        "maximum_narrow_nonwall_band_m": .05,
                        "minimum_supported_wall_band_m": .06,
                        "maximum_supported_wall_band_m": .60,
                        "maximum_wide_nonwall_band_m": 1.20,
                        "maximum_endpoint_alignment_residual_m": .02,
                    },
                    "decision_basis": [
                        "two_independent_context_source_rows",
                        "bidirectionally_aligned_parallel_equal_length_rails",
                        "rail_separation_outside_supported_wall_thickness",
                        "endpoint_bridge_alone_is_insufficient_wall_authority",
                    ],
                }
            if proof["nonwall_width_class"] == "too_narrow_for_wall_band":
                narrow_pairs.append((left, right, proof))

    for first_rail, second_rail, pair_proof in narrow_pairs:
        motifs: list[tuple[dict, dict, dict, Any]] = []
        for rail in (first_rail, second_rail):
            rail_endpoints = [
                tuple(rail["line"].coords[0]), tuple(rail["line"].coords[-1])]
            attached: list[dict] = []
            for candidate in bridge_candidates:
                if candidate is first_rail or candidate is second_rail:
                    continue
                line = candidate["line"]
                angle_difference = _undirected_angle_difference(
                    _axis_angle(tuple(rail["line"].coords[0]),
                                tuple(rail["line"].coords[-1])),
                    _axis_angle(tuple(line.coords[0]), tuple(line.coords[-1])))
                if not 15.0 <= angle_difference <= 75.0:
                    continue
                endpoints = [tuple(line.coords[0]), tuple(line.coords[-1])]
                matches = [(rail_number, endpoint_number,
                            math.dist(rail_endpoint, endpoint))
                           for rail_number, rail_endpoint in enumerate(rail_endpoints)
                           for endpoint_number, endpoint in enumerate(endpoints)
                           if math.dist(rail_endpoint, endpoint) <= .02 + _EPSILON]
                if not matches:
                    continue
                rail_number, endpoint_number, distance = min(
                    matches, key=lambda value: (value[2], value[0], value[1]))
                attached.append({
                    "candidate": candidate,
                    "rail_endpoint_number": rail_number,
                    "attached_endpoint_number": endpoint_number,
                    "free_endpoint": endpoints[1 - endpoint_number],
                    "attachment_distance_m": distance,
                    "angle_difference_deg": angle_difference,
                })
            for left_number, left in enumerate(attached):
                for right in attached[left_number + 1:]:
                    if left["rail_endpoint_number"] == right["rail_endpoint_number"]:
                        continue
                    left_line = left["candidate"]["line"]
                    right_line = right["candidate"]["line"]
                    if abs(left_line.length - right_line.length) > .03 + _EPSILON:
                        continue
                    free_span = LineString([left["free_endpoint"], right["free_endpoint"]])
                    if not .20 <= free_span.length <= max(.20, rail["line"].length - .10):
                        continue
                    if _undirected_angle_difference(
                            _axis_angle(tuple(rail["line"].coords[0]),
                                        tuple(rail["line"].coords[-1])),
                            _axis_angle(tuple(free_span.coords[0]),
                                        tuple(free_span.coords[-1]))) > 1.0 + _EPSILON:
                        continue
                    motifs.append((rail, left, right, free_span))
        if len(motifs) != 1:
            continue
        rail, left, right, free_span = motifs[0]
        diagonal_indexes = sorted({
            int(left["candidate"]["context_index"]),
            int(right["candidate"]["context_index"]),
        })
        for item, peer in ((left, right), (right, left)):
            candidate = item["candidate"]
            index = int(candidate["context_index"])
            excluded_bridge_indexes.add(index)
            nonwall_band_proofs[index] = {
                "method": "paired_context_trapezoidal_fixture_side_v1",
                "narrow_rail_entity_indexes": sorted({
                    int(first_rail["context_index"]),
                    int(second_rail["context_index"]),
                }),
                "mirrored_diagonal_entity_indexes": diagonal_indexes,
                "paired_diagonal_entity_index": int(peer["candidate"]["context_index"]),
                "source_length_m": _round(candidate["line"].length),
                "mirrored_length_delta_m": _round(abs(
                    candidate["line"].length - peer["candidate"]["line"].length)),
                "attachment_distance_m": _round(float(item["attachment_distance_m"])),
                "rail_angle_difference_deg": _round(float(item["angle_difference_deg"])),
                "free_endpoint_span_m": _round(float(free_span.length)),
                "narrow_rail_separation_m": pair_proof["separation_m"],
                "thresholds": {
                    "maximum_attachment_distance_m": .02,
                    "minimum_rail_angle_difference_deg": 15.0,
                    "maximum_rail_angle_difference_deg": 75.0,
                    "maximum_mirrored_length_difference_m": .03,
                    "minimum_free_endpoint_span_m": .20,
                    "maximum_free_span_axis_difference_deg": 1.0,
                },
                "decision_basis": [
                    "two_narrow_parallel_context_rails_below_wall_thickness",
                    "two_equal_mirrored_sloping_context_sides",
                    "opposite_rail_endpoint_attachment",
                    "parallel_nonmeeting_free_endpoint_span",
                    "trapezoidal_fixture_geometry_not_double_leaf_door",
                ],
            }

    for bridge in bridge_candidates:
        context_index = int(bridge["context_index"])
        if context_index in excluded_bridge_indexes:
            row = bridge["row"]
            source = _group_source_fields([row], [context_index])
            supplemental_context_evidence.append({
                "root_handle": bridge["root_handle"],
                "source": source,
                "bbox_m": [_round(value) for value in _group_bounds([row])],
                "length_m": _round(bridge["line"].length),
                "proof": copy.deepcopy(nonwall_band_proofs[context_index]),
            })
            continue
        row = bridge["row"]
        root_handle = bridge["root_handle"]
        context_index = int(bridge["context_index"])
        line = bridge["line"]
        supports = bridge["supports"]
        promoted = copy.deepcopy(row)
        promoted["wall_authority_source"] = "cad_context_endpoint_bridge_v1"
        supplemental_wall_rows.append(promoted)
        source = _group_source_fields([row], [context_index])
        supplemental_wall_evidence.append({
            "root_handle": root_handle,
            "row": promoted,
            "source": source,
            "bbox_m": [_round(value) for value in _group_bounds([row])],
            "length_m": _round(line.length),
            "endpoint_supports": [{
                "entity_index": int(value["entity_index"]),
                "distance_m": _round(float(value["distance_m"])),
                "source_handles": value["source_handles"],
            } for value in supports],
        })

    # Door swings may be raw ARC+LINE geometry without useful block semantics.
    all_segments = [(index, row, first, second) for index, row in raw_indexed
                    for first, second in _row_segments(row)
                    if str(row.get("entity_type") or "") in {"LINE", "LWPOLYLINE", "POLYLINE"}]
    swing_arc_sources: list[dict] = []
    for arc_index, arc_row in raw_indexed:
        if str(arc_row.get("entity_type") or "") != "ARC":
            continue
        circle = _circle_from_points(_normalise_points(arc_row))
        if not circle or not (.45 <= circle["radius_m"] <= 1.50
                              and 35.0 <= circle["sweep_deg"] <= 125.0
                              and circle["max_radial_error_m"] <= .015):
            continue
        swing_arc_sources.append({
            "rows": [arc_row], "indexes": [int(arc_index)],
            "circle": circle, "points": _normalise_points(arc_row),
            "evidence": {},
        })
    swing_arc_sources.extend(_tessellated_arc_chains(raw_indexed))
    door_number = 0
    for arc_source in swing_arc_sources:
        arc_rows = list(arc_source["rows"])
        arc_indexes = [int(value) for value in arc_source["indexes"]]
        circle = arc_source["circle"]
        leaf_matches = []
        for leaf_index, leaf_row, first, second in all_segments:
            if leaf_index in arc_indexes:
                continue
            length = math.dist(first, second)
            if not circle["radius_m"] * .80 <= length <= circle["radius_m"] * 1.20:
                continue
            hinge_limit = min(.25, circle["radius_m"] * .28)
            first_hinge_distance = math.dist(first, circle["center"])
            second_hinge_distance = math.dist(second, circle["center"])
            first_tip_distance = min(
                math.dist(first, endpoint) for endpoint in circle["endpoints"])
            second_tip_distance = min(
                math.dist(second, endpoint) for endpoint in circle["endpoints"])
            if first_hinge_distance <= hinge_limit and second_tip_distance <= .04:
                leaf_matches.append((leaf_index, leaf_row, first, second,
                                     first_hinge_distance, second_tip_distance))
            elif second_hinge_distance <= hinge_limit and first_tip_distance <= .04:
                leaf_matches.append((leaf_index, leaf_row, second, first,
                                     second_hinge_distance, first_tip_distance))
        if not leaf_matches:
            continue
        arc_endpoints = [tuple(point) for point in circle["endpoints"]]
        leaf_index, leaf_row, hinge, leaf_tip, hinge_inset, leaf_tip_error = min(
            leaf_matches,
            key=lambda value: (
                min(math.dist(value[3], endpoint) for endpoint in arc_endpoints),
                abs(math.dist(value[2], value[3]) - circle["radius_m"]),
                value[0],
            ),
        )

        # The radial leaf may be drawn in either the open or the closed
        # position.  Its direction therefore cannot itself define the wall
        # opening.  Score the leaf tip plus both arc endpoints against only
        # parallel structural wall lines, requiring support at both ends of
        # the candidate axis.  This selects the closed/wall-aligned radius
        # without using block names, source-specific rules or a wider snap.
        radial_ends: list[tuple[float, float]] = []
        for endpoint in [leaf_tip, *arc_endpoints]:
            if not any(math.dist(endpoint, existing) <= .04 for existing in radial_ends):
                radial_ends.append(endpoint)
        leaf_length = math.dist(hinge, leaf_tip)
        wall_line_union = unary_union(
            [line for _entity_index, line in all_wall_lines]) \
            if all_wall_lines else None
        axis_scores: list[dict] = []
        for endpoint in radial_ends:
            candidate_angle = _axis_angle(hinge, endpoint)
            parallel_lines = [
                fact["line"] for fact in all_wall_line_facts
                if _undirected_angle_difference(
                    candidate_angle, fact["angle"],
                ) <= 10.0
            ]
            support = unary_union(parallel_lines) if parallel_lines else None
            hinge_distance = (float(Point(hinge).distance(support))
                              if support is not None else float("inf"))
            endpoint_distance = (float(Point(endpoint).distance(support))
                                 if support is not None else float("inf"))
            axis_scores.append({
                "start": hinge,
                "endpoint": endpoint,
                "source": ("drawn_leaf" if math.dist(endpoint, leaf_tip) <= .04
                           else "opposite_arc_endpoint"),
                "hinge_support_distance_m": hinge_distance,
                "endpoint_support_distance_m": endpoint_distance,
                "max_support_distance_m": max(hinge_distance, endpoint_distance),
                "parallel_wall_line_count": len(parallel_lines),
                "is_drawn_leaf": math.dist(endpoint, leaf_tip) <= .04,
            })

        # Some production door blocks draw the swing ARC around a nominal
        # centre while the physical leaf hinge is inset into its jamb.  In
        # that convention the opposite ARC radius is close to the wall but is
        # both slightly oblique and shorter than the measured leaf.  Recover a
        # canonical closed axis only when three independent source facts agree:
        # a measured parallel wall-face pair fixes the wall centreline, a
        # transverse source wall face fixes the hinge-side jamb, and the raw
        # leaf fixes the exact opening length.  This is geometry-only; layers,
        # block names and drawing-specific coordinates do not participate.
        projected_seen: set[tuple] = set()
        for radial_end in radial_ends:
            radial_angle = _axis_angle(hinge, radial_end)
            for left_number, left_fact in enumerate(all_wall_line_facts):
                left_index = left_fact["index"]
                left_line = left_fact["line"]
                left_angle = left_fact["angle"]
                if _undirected_angle_difference(radial_angle, left_angle) > 10.0:
                    continue
                radians = math.radians(left_angle)
                unit = (math.cos(radians), math.sin(radians))
                normal = (-unit[1], unit[0])
                left_offset = (left_fact["midpoint"][0] * normal[0]
                               + left_fact["midpoint"][1] * normal[1])
                for right_fact in all_wall_line_facts[left_number + 1:]:
                    right_index = right_fact["index"]
                    right_line = right_fact["line"]
                    if (left_index == right_index
                            or _undirected_angle_difference(
                                left_angle, right_fact["angle"]) > 1.0):
                        continue
                    right_offset = (right_fact["midpoint"][0] * normal[0]
                                    + right_fact["midpoint"][1] * normal[1])
                    separation = abs(right_offset - left_offset)
                    if not .06 <= separation <= .60:
                        continue
                    centre_offset = (left_offset + right_offset) / 2
                    hinge_normal_offset = (
                        hinge[0] * normal[0] + hinge[1] * normal[1])
                    if abs(centre_offset - hinge_normal_offset) > .20:
                        continue
                    hinge_axial = hinge[0] * unit[0] + hinge[1] * unit[1]
                    projected_hinge = (
                        unit[0] * hinge_axial + normal[0] * centre_offset,
                        unit[1] * hinge_axial + normal[1] * centre_offset,
                    )
                    direction = (1.0 if (
                        (radial_end[0] - hinge[0]) * unit[0]
                        + (radial_end[1] - hinge[1]) * unit[1]) >= 0 else -1.0)
                    directed_unit = (unit[0] * direction, unit[1] * direction)

                    # The physical hinge-side jamb may be perpendicular to the
                    # host wall.  Snap only to an actual transverse source line
                    # intersecting the projected centreline within 200 mm.
                    # A parallel face pair alone cannot invent this snap.
                    probe = LineString([
                        (projected_hinge[0] - directed_unit[0] * .20,
                         projected_hinge[1] - directed_unit[1] * .20),
                        (projected_hinge[0] + directed_unit[0] * .20,
                         projected_hinge[1] + directed_unit[1] * .20),
                    ])
                    probe_bounds = tuple(float(value) for value in probe.bounds)
                    transverse_hits: list[dict] = []
                    for transverse_fact in all_wall_line_facts:
                        transverse_index = transverse_fact["index"]
                        transverse_line = transverse_fact["line"]
                        transverse_angle = transverse_fact["angle"]
                        angle_difference = _undirected_angle_difference(
                            left_angle, transverse_angle)
                        if angle_difference < 88.5:
                            continue
                        transverse_bounds = transverse_fact["bounds"]
                        if (transverse_bounds[2] < probe_bounds[0] - _EPSILON
                                or transverse_bounds[0] > probe_bounds[2] + _EPSILON
                                or transverse_bounds[3] < probe_bounds[1] - _EPSILON
                                or transverse_bounds[1] > probe_bounds[3] + _EPSILON):
                            continue
                        intersection = probe.intersection(transverse_line)
                        points = ([intersection] if getattr(
                            intersection, "geom_type", "") == "Point" else [])
                        for point in points:
                            distance = math.dist(
                                projected_hinge, (float(point.x), float(point.y)))
                            if distance <= .20 + _EPSILON:
                                transverse_hits.append({
                                    "index": int(transverse_index),
                                    "point": (float(point.x), float(point.y)),
                                    "distance_m": distance,
                                    "angle_difference_deg": angle_difference,
                                })
                    if not transverse_hits:
                        continue
                    transverse_hits.sort(key=lambda value: (
                        value["distance_m"], value["index"]))
                    best_hit = transverse_hits[0]
                    if (len(transverse_hits) > 1
                            and abs(float(transverse_hits[1]["distance_m"])
                                    - float(best_hit["distance_m"])) <= .005
                            and transverse_hits[1]["point"] != best_hit["point"]):
                        continue
                    canonical_hinge = best_hit["point"]
                    opposite = (
                        canonical_hinge[0] + directed_unit[0] * leaf_length,
                        canonical_hinge[1] + directed_unit[1] * leaf_length,
                    )
                    pair_union = unary_union([left_line, right_line])
                    if min(Point(canonical_hinge).distance(pair_union),
                           Point(opposite).distance(pair_union)) > .15 + _EPSILON:
                        continue
                    support_distances = ([
                        float(Point(point).distance(wall_line_union))
                        for point in (canonical_hinge, opposite)
                    ] if wall_line_union is not None else [float("inf"), float("inf")])
                    if max(support_distances) > .15 + _EPSILON:
                        continue
                    key = tuple(sorted((
                        tuple(round(value, 4) for value in canonical_hinge),
                        tuple(round(value, 4) for value in opposite),
                    )))
                    if key in projected_seen:
                        continue
                    projected_seen.add(key)
                    wall_source = _group_source_fields(
                        [selected_by_index[index] for index in
                         sorted({int(left_index), int(right_index),
                                 int(best_hit["index"])})
                         if index in selected_by_index],
                        sorted({int(left_index), int(right_index),
                                int(best_hit["index"])}),
                    )
                    axis_scores.append({
                        "start": canonical_hinge,
                        "endpoint": opposite,
                        "source": "measured_wall_face_pair_and_transverse_jamb",
                        "hinge_support_distance_m": support_distances[0],
                        "endpoint_support_distance_m": support_distances[1],
                        "max_support_distance_m": max(support_distances),
                        "parallel_wall_line_count": 2,
                        "is_drawn_leaf": False,
                        "wall_face_entity_indexes": sorted(
                            {int(left_index), int(right_index)}),
                        "transverse_jamb_entity_index": int(best_hit["index"]),
                        "wall_face_source_handles": wall_source["source_handles"],
                        "wall_face_separation_m": separation,
                        "hinge_to_wall_centerline_offset_m": abs(
                            centre_offset - hinge_normal_offset),
                        "transverse_jamb_snap_distance_m": best_hit["distance_m"],
                        "transverse_jamb_angle_difference_deg": best_hit[
                            "angle_difference_deg"],
                    })
        # Source drawings often repeat one physical face as two subsegments.
        # Collapse projected axes that differ by at most 5 mm before exposing
        # them to the global uniqueness gate; provenance remains on the best
        # (closest-to-hinge) proof instead of manufacturing two door choices.
        radial_scores = [row for row in axis_scores if row.get("source")
                         != "measured_wall_face_pair_and_transverse_jamb"]
        projected_scores = sorted(
            [row for row in axis_scores if row.get("source")
             == "measured_wall_face_pair_and_transverse_jamb"],
            key=lambda value: (
                float(value["max_support_distance_m"]),
                float(value["hinge_to_wall_centerline_offset_m"]),
                float(value["transverse_jamb_snap_distance_m"]),
                abs(float(value["wall_face_separation_m"]) - .20),
                tuple(tuple(point) for point in (
                    value["start"], value["endpoint"])),
            ))
        unique_projected_scores: list[dict] = []
        for value in projected_scores:
            candidate_line = LineString([value["start"], value["endpoint"]])
            if any(candidate_line.hausdorff_distance(LineString([
                    existing["start"], existing["endpoint"]])) <= .005 + _EPSILON
                   for existing in unique_projected_scores):
                continue
            unique_projected_scores.append(value)
        axis_scores = radial_scores + unique_projected_scores
        selected_axis = min(axis_scores, key=lambda value: (
            float(value["max_support_distance_m"]),
            float(value["hinge_support_distance_m"])
            + float(value["endpoint_support_distance_m"]),
            0 if value["is_drawn_leaf"] else 1,
            tuple(value["endpoint"]),
        ))
        closed_start = tuple(selected_axis.get("start") or hinge)
        closed_end = tuple(selected_axis["endpoint"])
        support_distance = float(selected_axis["max_support_distance_m"])
        if support_distance > .25:
            continue
        door_number += 1
        source_reason_codes = [
            "circular_swing_arc", "radial_door_leaf",
            "wall_network_supported",
        ]
        if arc_source.get("evidence"):
            source_reason_codes.append("tessellated_circular_swing_chain")
        opening_candidates.append(_opening_candidate(
            f"cad_raw_opening_swing_{door_number}", "door",
            [*arc_rows, leaf_row], [*arc_indexes, leaf_index],
            [closed_start, closed_end],
            leaf_length, .96,
            source_reason_codes,
            {"arc_center_m": _point_list(circle["center"]),
             "arc_radius_m": _round(circle["radius_m"]),
             "arc_sweep_deg": _round(circle["sweep_deg"]),
             "leaf_length_m": _round(leaf_length),
             "hinge_inset_from_arc_center_m": _round(hinge_inset),
             "leaf_tip_to_arc_endpoint_m": _round(leaf_tip_error),
             "wall_support_distance_m": _round(support_distance),
             "drawn_leaf_axis_cad_m": [_point_list(hinge), _point_list(leaf_tip)],
             "selected_closed_axis_source": selected_axis.get("source") or (
                 "drawn_leaf" if selected_axis["is_drawn_leaf"]
                 else "opposite_arc_endpoint"),
             **({"tessellated_arc_chain": copy.deepcopy(
                    arc_source["evidence"])}
                if arc_source.get("evidence") else {}),
             "axis_candidates": [{
                 "axis_segment_cad_m": [
                     _point_list(tuple(value.get("start") or hinge)),
                     _point_list(value["endpoint"])],
                 "hinge_support_distance_m": _round(value["hinge_support_distance_m"]),
                 "endpoint_support_distance_m": _round(value["endpoint_support_distance_m"]),
                 "max_support_distance_m": _round(value["max_support_distance_m"]),
                 "parallel_wall_line_count": value["parallel_wall_line_count"],
                 "is_drawn_leaf": value["is_drawn_leaf"],
                 **({"wall_face_entity_indexes": copy.deepcopy(
                         value["wall_face_entity_indexes"]),
                     "transverse_jamb_entity_index": value[
                         "transverse_jamb_entity_index"],
                     "wall_face_source_handles": copy.deepcopy(
                         value["wall_face_source_handles"]),
                     "wall_face_separation_m": _round(
                         value["wall_face_separation_m"]),
                     "hinge_to_wall_centerline_offset_m": _round(
                         value["hinge_to_wall_centerline_offset_m"]),
                     "transverse_jamb_snap_distance_m": _round(
                         value["transverse_jamb_snap_distance_m"]),
                     "transverse_jamb_angle_difference_deg": _round(
                         value["transverse_jamb_angle_difference_deg"]),
                     "projection_method":
                         "cad_arc_leaf_wall_pair_transverse_jamb_projection_v1"}
                    if value.get("source")
                    == "measured_wall_face_pair_and_transverse_jamb" else {}),
             } for value in axis_scores]},
        ))
        for opening_index in [*arc_indexes, leaf_index]:
            if opening_index not in selected_by_index:
                continue
            role_by_index[opening_index] = (
                "opening_symbol", "high",
                [*source_reason_codes, "opening_root_geometry_not_wall"],
            )

    # A few legacy drawings contain a visually coherent door whose ARC centre
    # is not concentric with the drawn leaf (often after a block/evaluation
    # transform).  Never relax the circular-swing radius contract.  Instead,
    # accept the independent source motif only when a capped two-rail leaf, an
    # ARC, a two-point return path on the duplicated exterior shell, and the
    # measured inner shell face jointly determine one opening axis.
    linear_source_segments: list[dict] = []
    for index, row in raw_indexed:
        segments = _row_segments(row)
        if (len(segments) != 1 or str(row.get("entity_type") or "")
                not in {"LINE", "LWPOLYLINE", "POLYLINE"}):
            continue
        first, second = segments[0]
        line = LineString([first, second])
        if line.length > _EPSILON:
            linear_source_segments.append({
                "index": int(index), "row": row, "first": first,
                "second": second, "line": line,
                "angle": _axis_angle(first, second),
            })
    arc_sources: list[dict] = []
    for index, row in raw_indexed:
        if str(row.get("entity_type") or "") != "ARC":
            continue
        circle = _circle_from_points(_normalise_points(row))
        if (circle and .45 <= float(circle["radius_m"]) <= 1.50
                and 35.0 <= float(circle["sweep_deg"]) <= 125.0
                and float(circle["max_radial_error_m"]) <= .015):
            arc_sources.append({"index": int(index), "row": row,
                                "circle": circle})

    shell_leaf_candidates: list[dict] = []
    for face_index, face_row in indexed:
        face_points = _normalise_points(face_row)
        unique_face_points: list[tuple[float, float]] = []
        for point in face_points:
            if not any(math.dist(point, existing) <= _EPSILON
                       for existing in unique_face_points):
                unique_face_points.append(point)
        if (not bool(face_row.get("closed")) or len(unique_face_points) != 2):
            continue
        face_line = LineString(unique_face_points)
        if not .60 <= face_line.length <= 1.35:
            continue
        face_path_length = sum(math.dist(first, second)
                               for first, second in zip(
                                   face_points, face_points[1:]))
        if abs(face_path_length / face_line.length - 2.0) > .02:
            continue
        for shell_proof in perimeter_wall_shell_proofs:
            try:
                outer_polygon = Polygon(shell_proof["outer_polygon_m"])
                inner_polygon = Polygon(shell_proof["inner_polygon_m"])
                shell_thickness = float(
                    shell_proof["measured_wall_thickness_m"])
            except (TypeError, ValueError, KeyError):
                continue
            if (face_line.difference(outer_polygon.boundary.buffer(
                    .005, cap_style=2, join_style=2)).length > 1e-7):
                continue
            inner_coordinates = list(inner_polygon.exterior.coords)[:-1]
            inner_edges = [LineString([first, second]) for first, second in zip(
                inner_coordinates, inner_coordinates[1:] + inner_coordinates[:1])]
            aligned_inner_edges = [
                edge for edge in inner_edges
                if _undirected_angle_difference(
                    _axis_angle(tuple(face_line.coords[0]),
                                tuple(face_line.coords[-1])),
                    _axis_angle(tuple(edge.coords[0]),
                                tuple(edge.coords[-1]))) <= 1.0 + _EPSILON
                and abs(float(face_line.distance(edge)) - shell_thickness)
                    <= .02 + _EPSILON]
            if len(aligned_inner_edges) != 1:
                continue
            inner_edge = aligned_inner_edges[0]
            face_midpoint = face_line.interpolate(.5, normalized=True)
            inner_nearest = inner_edge.interpolate(inner_edge.project(face_midpoint))
            shift = (float(inner_nearest.x - face_midpoint.x),
                     float(inner_nearest.y - face_midpoint.y))
            shift_length = math.hypot(*shift)
            if abs(shift_length - shell_thickness) > .02 + _EPSILON:
                continue
            canonical_axis = [
                (float(point[0] + shift[0] / 2),
                 float(point[1] + shift[1] / 2))
                for point in face_line.coords]
            canonical_line = LineString(canonical_axis)
            face_angle = _axis_angle(canonical_axis[0], canonical_axis[1])
            for left_number, left in enumerate(linear_source_segments):
                if not .55 <= left["line"].length <= 1.35:
                    continue
                for right in linear_source_segments[left_number + 1:]:
                    if (left["index"] == right["index"]
                            or not .55 <= right["line"].length <= 1.35
                            or _undirected_angle_difference(
                                left["angle"], right["angle"]) > 1.0 + _EPSILON):
                        continue
                    rail_separation = float(left["line"].distance(right["line"]))
                    if not .025 <= rail_separation <= .08:
                        continue
                    left_length = float(left["line"].length)
                    unit = ((left["second"][0] - left["first"][0]) / left_length,
                            (left["second"][1] - left["first"][1]) / left_length)
                    left_interval = sorted(_projection(
                        point, left["first"], unit)
                        for point in (left["first"], left["second"]))
                    right_interval = sorted(_projection(
                        point, left["first"], unit)
                        for point in (right["first"], right["second"]))
                    rail_overlap = max(0.0, min(left_interval[1], right_interval[1])
                                       - max(left_interval[0], right_interval[0]))
                    if (rail_overlap / min(left["line"].length,
                                           right["line"].length)
                            < .85 - _EPSILON
                            or abs(left["line"].length - right["line"].length)
                            > .10 + _EPSILON):
                        continue
                    cap_matches: list[dict] = []
                    for cap in linear_source_segments:
                        if cap["index"] in {left["index"], right["index"]}:
                            continue
                        if (abs(cap["line"].length - rail_separation)
                                > .015 + _EPSILON
                                or _undirected_angle_difference(
                                    left["angle"], cap["angle"])
                                < 88.5 - _EPSILON):
                            continue
                        for left_endpoint_index, left_endpoint in enumerate(
                                (left["first"], left["second"])):
                            for right_endpoint_index, right_endpoint in enumerate(
                                    (right["first"], right["second"])):
                                direct = max(math.dist(cap["first"], left_endpoint),
                                             math.dist(cap["second"], right_endpoint))
                                reverse = max(math.dist(cap["second"], left_endpoint),
                                              math.dist(cap["first"], right_endpoint))
                                endpoint_error = min(direct, reverse)
                                if endpoint_error <= .015 + _EPSILON:
                                    cap_matches.append({
                                        "cap": cap,
                                        "left_endpoint_index": left_endpoint_index,
                                        "right_endpoint_index": right_endpoint_index,
                                        "endpoint_error_m": endpoint_error,
                                    })
                    if not cap_matches:
                        continue
                    cap_match = min(cap_matches, key=lambda value: (
                        value["endpoint_error_m"], value["cap"]["index"]))
                    left_endpoints = (left["first"], left["second"])
                    right_endpoints = (right["first"], right["second"])
                    free_center = (
                        (left_endpoints[cap_match["left_endpoint_index"]][0]
                         + right_endpoints[cap_match["right_endpoint_index"]][0]) / 2,
                        (left_endpoints[cap_match["left_endpoint_index"]][1]
                         + right_endpoints[cap_match["right_endpoint_index"]][1]) / 2,
                    )
                    hinge_center = (
                        (left_endpoints[1 - cap_match["left_endpoint_index"]][0]
                         + right_endpoints[1 - cap_match["right_endpoint_index"]][0]) / 2,
                        (left_endpoints[1 - cap_match["left_endpoint_index"]][1]
                         + right_endpoints[1 - cap_match["right_endpoint_index"]][1]) / 2,
                    )
                    leaf_angle = _axis_angle(hinge_center, free_center)
                    leaf_wall_angle = _undirected_angle_difference(
                        leaf_angle, face_angle)
                    if leaf_wall_angle < 88.5 - _EPSILON:
                        continue
                    endpoint_distances = [math.dist(hinge_center, point)
                                          for point in canonical_axis]
                    hinge_endpoint_index = min(
                        range(2), key=lambda value: endpoint_distances[value])
                    if endpoint_distances[hinge_endpoint_index] > .16 + _EPSILON:
                        continue
                    opposite_endpoint = canonical_axis[1 - hinge_endpoint_index]
                    leaf_length = (left["line"].length
                                   + right["line"].length) / 2
                    if not .55 <= leaf_length / canonical_line.length <= 1.05:
                        continue
                    arc_matches: list[dict] = []
                    for arc in arc_sources:
                        endpoints = [tuple(point) for point in
                                     arc["circle"]["endpoints"]]
                        direct = (math.dist(endpoints[0], free_center),
                                  math.dist(endpoints[1], opposite_endpoint))
                        reverse = (math.dist(endpoints[1], free_center),
                                   math.dist(endpoints[0], opposite_endpoint))
                        errors = min((direct, reverse), key=lambda value: sum(value))
                        if errors[0] <= .08 + _EPSILON and errors[1] <= .16 + _EPSILON:
                            arc_matches.append({"arc": arc,
                                                "endpoint_errors_m": errors})
                    if len(arc_matches) != 1:
                        continue
                    arc_match = arc_matches[0]
                    shell_leaf_candidates.append({
                        "face_index": int(face_index), "face_row": face_row,
                        "left": left, "right": right,
                        "cap": cap_match["cap"], "arc": arc_match["arc"],
                        "canonical_axis": canonical_axis,
                        "opening_width_m": float(canonical_line.length),
                        "shell_proof": shell_proof,
                        "shell_thickness_m": shell_thickness,
                        "leaf_length_m": float(leaf_length),
                        "rail_separation_m": rail_separation,
                        "rail_overlap_ratio": rail_overlap / min(
                            left["line"].length, right["line"].length),
                        "cap_endpoint_error_m": cap_match["endpoint_error_m"],
                        "hinge_endpoint_distance_m": endpoint_distances[
                            hinge_endpoint_index],
                        "arc_endpoint_errors_m": arc_match["endpoint_errors_m"],
                        "free_center": free_center, "hinge_center": hinge_center,
                        "leaf_wall_angle_difference_deg": leaf_wall_angle,
                        "face_path_length_m": face_path_length,
                    })

    shell_leaf_candidates.sort(key=lambda value: (
        sum(value["arc_endpoint_errors_m"]),
        value["hinge_endpoint_distance_m"], value["face_index"]))
    used_shell_leaf_sources: set[int] = set()
    for candidate_proof in shell_leaf_candidates:
        indexes = {
            candidate_proof["face_index"], candidate_proof["left"]["index"],
            candidate_proof["right"]["index"], candidate_proof["cap"]["index"],
            candidate_proof["arc"]["index"],
        }
        if indexes.intersection(used_shell_leaf_sources):
            continue
        if any(indexes.intersection(set(candidate.get("source_entity_indexes") or []))
               for candidate in opening_candidates
               if str(candidate.get("kind") or "") == "door"):
            continue
        used_shell_leaf_sources.update(indexes)
        rows = [
            candidate_proof["face_row"], candidate_proof["left"]["row"],
            candidate_proof["right"]["row"], candidate_proof["cap"]["row"],
            candidate_proof["arc"]["row"],
        ]
        door_number += 1
        opening_candidates.append(_opening_candidate(
            f"cad_raw_opening_shell_leaf_{door_number}", "door",
            rows, sorted(indexes), candidate_proof["canonical_axis"],
            candidate_proof["opening_width_m"], .99,
            ["capped_parallel_door_leaf", "two_point_outer_shell_opening_face",
             "duplicated_inner_outer_wall_shell", "swing_arc_endpoint_supported",
             "wall_network_supported"],
            {
                "method": "cad_capped_leaf_shell_face_swing_v1",
                "opening_face_return_method":
                    "cad_closed_two_point_return_path_v1",
                "opening_face_source_handle": _provenance(
                    candidate_proof["face_row"]).get("source_handle") or "",
                "opening_face_path_length_m": _round(
                    candidate_proof["face_path_length_m"]),
                "opening_width_m": _round(candidate_proof["opening_width_m"]),
                "wall_thickness_m": _round(candidate_proof["shell_thickness_m"]),
                "wall_shell_id": candidate_proof["shell_proof"]["shell_id"],
                "wall_shell_source_handles": copy.deepcopy(
                    candidate_proof["shell_proof"]["source"]["source_handles"]),
                "leaf_rail_source_handles": _group_source_fields(
                    [candidate_proof["left"]["row"],
                     candidate_proof["right"]["row"]],
                    [candidate_proof["left"]["index"],
                     candidate_proof["right"]["index"]])["source_handles"],
                "leaf_cap_source_handle": _provenance(
                    candidate_proof["cap"]["row"]).get("source_handle") or "",
                "arc_source_handle": _provenance(
                    candidate_proof["arc"]["row"]).get("source_handle") or "",
                "leaf_length_m": _round(candidate_proof["leaf_length_m"]),
                "leaf_rail_separation_m": _round(
                    candidate_proof["rail_separation_m"]),
                "leaf_rail_overlap_ratio": _round(
                    candidate_proof["rail_overlap_ratio"]),
                "leaf_cap_endpoint_error_m": _round(
                    candidate_proof["cap_endpoint_error_m"]),
                "leaf_hinge_to_opening_endpoint_m": _round(
                    candidate_proof["hinge_endpoint_distance_m"]),
                "leaf_to_wall_axis_angle_difference_deg": _round(
                    candidate_proof["leaf_wall_angle_difference_deg"]),
                "arc_radius_m": _round(
                    candidate_proof["arc"]["circle"]["radius_m"]),
                "arc_sweep_deg": _round(
                    candidate_proof["arc"]["circle"]["sweep_deg"]),
                "arc_endpoint_errors_m": [_round(value) for value in
                                           candidate_proof["arc_endpoint_errors_m"]],
                "drawn_leaf_axis_cad_m": [
                    _point_list(candidate_proof["hinge_center"]),
                    _point_list(candidate_proof["free_center"])],
                "axis_candidates": [{
                    "axis_segment_cad_m": [_point_list(point) for point in
                                           candidate_proof["canonical_axis"]],
                    "projection_method":
                        "cad_duplicate_shell_outer_face_to_centerline_v1",
                    "wall_face_separation_m": _round(
                        candidate_proof["shell_thickness_m"]),
                }],
                "thresholds": {
                    "minimum_opening_width_m": .60,
                    "maximum_opening_width_m": 1.35,
                    "minimum_leaf_rail_count": 2,
                    "minimum_leaf_rail_separation_m": .025,
                    "maximum_leaf_rail_separation_m": .08,
                    "minimum_leaf_rail_overlap_ratio": .85,
                    "maximum_leaf_length_difference_m": .10,
                    "maximum_leaf_cap_endpoint_error_m": .015,
                    "minimum_leaf_to_wall_axis_angle_difference_deg": 88.5,
                    "maximum_hinge_to_opening_endpoint_m": .16,
                    "minimum_leaf_to_opening_width_ratio": .55,
                    "maximum_leaf_to_opening_width_ratio": 1.05,
                    "maximum_arc_to_leaf_free_endpoint_m": .08,
                    "maximum_arc_to_opposite_opening_endpoint_m": .16,
                },
                "decision_basis": [
                    "two_parallel_leaf_faces_have_transverse_free_end_cap",
                    "leaf_hinge_is_supported_by_one_opening_endpoint",
                    "closed_two_point_return_path_lies_on_duplicated_outer_shell",
                    "duplicated_inner_shell_measures_canonical_wall_centerline",
                    "arc_endpoints_support_leaf_free_end_and_opposite_jamb",
                    "arc_radius_is_not_used_as_leaf_length",
                ],
            },
        ))
        for opening_index in indexes:
            if opening_index in selected_by_index:
                role_by_index[opening_index] = (
                    "opening_symbol", "high",
                    ["capped_parallel_door_leaf",
                     "two_point_outer_shell_opening_face",
                     "swing_arc_endpoint_supported",
                     "opening_root_geometry_not_wall"],
                )

    # Some architectural plans omit the swing ARC but draw the open door leaf
    # as a thin slab: three to five independent, almost coincident parallel
    # rails.  One endpoint cluster is the hinge at a measured double-face wall
    # and the other is free in room space.  A single diagonal line is far too
    # ambiguous (furniture and leaders are common), so this recovery requires
    # the complete repeated-leaf motif and a 60--600 mm source wall-face pair.
    leaf_rails: list[dict] = []
    for index, row in raw_indexed:
        if index in selected_by_index:
            continue
        if str(row.get("entity_type") or "") not in {
                "LINE", "LWPOLYLINE", "POLYLINE"}:
            continue
        segments = _row_segments(row)
        if len(segments) != 1:
            continue
        first, second = segments[0]
        line = LineString([first, second])
        if not .55 <= line.length <= 1.35:
            continue
        leaf_rails.append({
            "index": index, "row": row, "first": first, "second": second,
            "line": line, "length": float(line.length),
            "angle": _axis_angle(first, second),
            "midpoint": ((first[0] + second[0]) / 2,
                         (first[1] + second[1]) / 2),
        })

    parent = list(range(len(leaf_rails)))

    def leaf_find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def leaf_union(left: int, right: int) -> None:
        left_root, right_root = leaf_find(left), leaf_find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    leaf_cell_size = .15
    leaf_cells: dict[tuple[int, int], list[int]] = {}
    for number, rail in enumerate(leaf_rails):
        midpoint = rail["midpoint"]
        cell = (math.floor(midpoint[0] / leaf_cell_size),
                math.floor(midpoint[1] / leaf_cell_size))
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for other_number in leaf_cells.get(
                        (cell[0] + x_offset, cell[1] + y_offset), []):
                    other = leaf_rails[other_number]
                    if (_undirected_angle_difference(
                            rail["angle"], other["angle"]) > 1.0 + _EPSILON
                            or abs(rail["length"] - other["length"])
                            > max(.02, min(rail["length"], other["length"]) * .02)):
                        continue
                    direct = max(
                        math.dist(rail["first"], other["first"]),
                        math.dist(rail["second"], other["second"]))
                    reverse = max(
                        math.dist(rail["first"], other["second"]),
                        math.dist(rail["second"], other["first"]))
                    if min(direct, reverse) <= .08 + _EPSILON:
                        leaf_union(number, other_number)
        leaf_cells.setdefault(cell, []).append(number)

    leaf_components: dict[int, list[dict]] = {}
    for number, rail in enumerate(leaf_rails):
        leaf_components.setdefault(leaf_find(number), []).append(rail)
    wall_union = unary_union([line for _index, line in all_wall_lines]) \
        if all_wall_lines else None
    leaf_number = 0
    for component in sorted(leaf_components.values(), key=lambda rows: (
            min(int(row["index"]) for row in rows), len(rows))):
        if not 3 <= len(component) <= 5 or wall_union is None:
            continue
        source_handles = _group_source_fields(
            [row["row"] for row in component],
            [int(row["index"]) for row in component])["source_handles"]
        if len(source_handles) < 3:
            continue
        seed = component[0]
        oriented: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for rail in component:
            direct = max(math.dist(seed["first"], rail["first"]),
                         math.dist(seed["second"], rail["second"]))
            reverse = max(math.dist(seed["first"], rail["second"]),
                          math.dist(seed["second"], rail["first"]))
            oriented.append((
                (rail["first"], rail["second"])
                if direct <= reverse else (rail["second"], rail["first"])))
        first_center = (
            sum(pair[0][0] for pair in oriented) / len(oriented),
            sum(pair[0][1] for pair in oriented) / len(oriented))
        second_center = (
            sum(pair[1][0] for pair in oriented) / len(oriented),
            sum(pair[1][1] for pair in oriented) / len(oriented))
        first_radius = max(math.dist(first_center, pair[0]) for pair in oriented)
        second_radius = max(math.dist(second_center, pair[1]) for pair in oriented)
        lengths = [float(row["length"]) for row in component]
        leaf_length = sum(lengths) / len(lengths)
        angles = [float(row["angle"]) for row in component]
        angle_spread = max(
            _undirected_angle_difference(angles[0], value)
            for value in angles)
        length_spread = max(lengths) - min(lengths)
        if (max(first_radius, second_radius) > .08 + _EPSILON
                or angle_spread > 1.0 + _EPSILON
                or length_spread > max(.02, leaf_length * .02) + _EPSILON):
            continue
        endpoint_wall_distances = [
            float(Point(point).distance(wall_union))
            for point in (first_center, second_center)]
        hinge_index = (0 if endpoint_wall_distances[0]
                       <= endpoint_wall_distances[1] else 1)
        hinge = (first_center, second_center)[hinge_index]
        free_end = (second_center, first_center)[hinge_index]
        hinge_distance = endpoint_wall_distances[hinge_index]
        free_distance = endpoint_wall_distances[1 - hinge_index]
        if (hinge_distance > .12 + _EPSILON
                or free_distance < .20 - _EPSILON
                or free_distance - hinge_distance < .12 - _EPSILON):
            continue
        leaf_angle = _axis_angle(hinge, free_end)
        nearby_wall_lines = [
            (wall_index, line) for wall_index, line in all_wall_lines
            if line.distance(Point(hinge)) <= .22 + _EPSILON
            and 20.0 - _EPSILON <= _undirected_angle_difference(
                leaf_angle,
                _axis_angle(tuple(line.coords[0]), tuple(line.coords[-1])))
            <= 70.0 + _EPSILON
        ]
        axis_options: list[dict] = []
        seen_axes: set[tuple] = set()

        def source_face_jamb_support(
            endpoint: tuple[float, float],
            outward: tuple[float, float],
            axis_unit: tuple[float, float],
            axis_normal: tuple[float, float],
            endpoint_number: int,
        ) -> Optional[dict]:
            axis_offset = endpoint[0] * axis_normal[0] \
                + endpoint[1] * axis_normal[1]
            faces: list[dict] = []
            for wall_index, wall_line in all_wall_lines:
                wall_angle = _axis_angle(
                    tuple(wall_line.coords[0]), tuple(wall_line.coords[-1]))
                axis_angle = _axis_angle(
                    endpoint,
                    (endpoint[0] + axis_unit[0], endpoint[1] + axis_unit[1]))
                angle_difference = _undirected_angle_difference(
                    axis_angle, wall_angle)
                if angle_difference > 1.0 + _EPSILON:
                    continue
                face_offset = sum(
                    point[0] * axis_normal[0] + point[1] * axis_normal[1]
                    for point in wall_line.coords) / len(wall_line.coords)
                if abs(face_offset - axis_offset) > .40 + _EPSILON:
                    continue
                projections = [
                    (point[0] - endpoint[0]) * outward[0]
                    + (point[1] - endpoint[1]) * outward[1]
                    for point in wall_line.coords]
                outward_extension = max(projections)
                endpoint_distance = float(wall_line.distance(Point(endpoint)))
                if (outward_extension < .05 - _EPSILON
                        or endpoint_distance > .35 + _EPSILON):
                    continue
                faces.append({
                    "entity_index": int(wall_index), "line": wall_line,
                    "face_offset": face_offset,
                    "endpoint_distance_m": endpoint_distance,
                    "outward_extension_m": outward_extension,
                    "axis_angle_difference_deg": angle_difference,
                })
            physical_pairs: dict[tuple[float, float], dict] = {}
            for left_number, left in enumerate(faces):
                for right in faces[left_number + 1:]:
                    if left["entity_index"] == right["entity_index"]:
                        continue
                    separation = abs(left["face_offset"] - right["face_offset"])
                    midpoint_offset = (
                        (left["face_offset"] + right["face_offset"]) / 2
                        - axis_offset)
                    if (not .06 - _EPSILON <= separation <= .60 + _EPSILON
                            or abs(midpoint_offset) > .08 + _EPSILON
                            or max(left["endpoint_distance_m"],
                                   right["endpoint_distance_m"])
                            > separation / 2 + .12 + _EPSILON):
                        continue
                    indexes = sorted({left["entity_index"], right["entity_index"]})
                    face_rows = [selected_by_index[index] for index in indexes
                                 if index in selected_by_index]
                    if len(face_rows) != 2:
                        continue
                    source = _group_source_fields(face_rows, indexes)
                    pair = {
                        "method": "cad_source_wall_face_pair_at_door_jamb_v1",
                        "endpoint_index": endpoint_number,
                        "wall_face_entity_indexes": indexes,
                        "wall_face_source_handles": source["source_handles"],
                        "face_separation_m": _round(separation),
                        "wall_band_midpoint_offset_m": _round(midpoint_offset),
                        "wall_face_endpoint_distance_m": [
                            _round(left["endpoint_distance_m"]),
                            _round(right["endpoint_distance_m"])],
                        "wall_face_outward_extension_m": [
                            _round(left["outward_extension_m"]),
                            _round(right["outward_extension_m"])],
                        "wall_face_axis_angle_difference_deg": [
                            _round(left["axis_angle_difference_deg"]),
                            _round(right["axis_angle_difference_deg"])],
                        "score": (
                            abs(midpoint_offset),
                            max(left["endpoint_distance_m"],
                                right["endpoint_distance_m"]),
                            -min(left["outward_extension_m"],
                                 right["outward_extension_m"]),
                            tuple(indexes)),
                    }
                    signature = (round(separation, 3), round(midpoint_offset, 3))
                    previous = physical_pairs.get(signature)
                    if previous is None or pair["score"] < previous["score"]:
                        physical_pairs[signature] = pair
            pairs = sorted(physical_pairs.values(), key=lambda value: value["score"])
            if not pairs:
                return None
            if (len(pairs) > 1
                    and abs(float(pairs[1]["score"][0])
                            - float(pairs[0]["score"][0])) <= .005 + _EPSILON
                    and abs(float(pairs[1]["score"][1])
                            - float(pairs[0]["score"][1])) <= .005 + _EPSILON):
                return None
            best = copy.deepcopy(pairs[0])
            best.pop("score", None)
            best["thresholds"] = {
                "maximum_wall_face_axis_angle_difference_deg": 1.0,
                "minimum_face_separation_m": .06,
                "maximum_face_separation_m": .60,
                "maximum_wall_band_midpoint_offset_m": .08,
                "maximum_endpoint_extra_distance_m": .12,
                "minimum_outward_extension_m": .05,
                "maximum_physical_pair_score_tie_m": .005,
            }
            return best

        for left_number, (left_index, left_line) in enumerate(nearby_wall_lines):
            left_angle = _axis_angle(
                tuple(left_line.coords[0]), tuple(left_line.coords[-1]))
            radians = math.radians(left_angle)
            unit = (math.cos(radians), math.sin(radians))
            normal = (-unit[1], unit[0])
            left_offset = sum(
                point[0] * normal[0] + point[1] * normal[1]
                for point in left_line.coords) / len(left_line.coords)
            for right_index, right_line in nearby_wall_lines[left_number + 1:]:
                if (left_index == right_index or _undirected_angle_difference(
                        left_angle, _axis_angle(tuple(right_line.coords[0]),
                                                tuple(right_line.coords[-1])))
                        > 1.0 + _EPSILON):
                    continue
                right_offset = sum(
                    point[0] * normal[0] + point[1] * normal[1]
                    for point in right_line.coords) / len(right_line.coords)
                face_separation = abs(right_offset - left_offset)
                if not .06 - _EPSILON <= face_separation <= .60 + _EPSILON:
                    continue
                axial_position = hinge[0] * unit[0] + hinge[1] * unit[1]
                center_offset = (left_offset + right_offset) / 2
                canonical_hinge = (
                    unit[0] * axial_position + normal[0] * center_offset,
                    unit[1] * axial_position + normal[1] * center_offset)
                for direction in (-1.0, 1.0):
                    opposite = (
                        canonical_hinge[0] + unit[0] * leaf_length * direction,
                        canonical_hinge[1] + unit[1] * leaf_length * direction)
                    axis = LineString([canonical_hinge, opposite])
                    support_distances = [
                        float(Point(point).distance(wall_union))
                        for point in (canonical_hinge, opposite)]
                    if max(support_distances) > .20 + _EPSILON:
                        continue
                    key = tuple(sorted((
                        tuple(round(value, 4) for value in canonical_hinge),
                        tuple(round(value, 4) for value in opposite))))
                    if key in seen_axes:
                        continue
                    seen_axes.add(key)
                    midpoint_clearance = float(axis.interpolate(
                        .5, normalized=True).distance(wall_union))
                    axis_wall_source = _group_source_fields(
                        [selected_by_index[index] for index in
                         sorted({int(left_index), int(right_index)})
                         if index in selected_by_index],
                        sorted({int(left_index), int(right_index)}))
                    source_jamb_supports = [
                        source_face_jamb_support(
                            canonical_hinge, (-unit[0] * direction,
                                              -unit[1] * direction),
                            (unit[0] * direction, unit[1] * direction),
                            normal, 0),
                        source_face_jamb_support(
                            opposite, (unit[0] * direction,
                                       unit[1] * direction),
                            (unit[0] * direction, unit[1] * direction),
                            normal, 1),
                    ]
                    complete_source_jambs = all(
                        isinstance(value, dict) for value in source_jamb_supports)
                    source_jamb_thickness_delta = (
                        abs(float(source_jamb_supports[0]["face_separation_m"])
                            - float(source_jamb_supports[1]["face_separation_m"]))
                        if complete_source_jambs else None)
                    axis_options.append({
                        "axis_segment_cad_m": [
                            _point_list(canonical_hinge), _point_list(opposite)],
                        "wall_face_entity_indexes": sorted(
                            {int(left_index), int(right_index)}),
                        "wall_face_source_handles": axis_wall_source[
                            "source_handles"],
                        "wall_face_separation_m": _round(face_separation),
                        "endpoint_wall_support_distance_m": [
                            _round(value) for value in support_distances],
                        "max_endpoint_wall_support_distance_m": _round(
                            max(support_distances)),
                        "axis_midpoint_wall_clearance_m": _round(
                            midpoint_clearance),
                        "leaf_to_wall_axis_angle_difference_deg": _round(
                            _undirected_angle_difference(leaf_angle, left_angle)),
                        "source_face_jamb_supports": copy.deepcopy(
                            source_jamb_supports),
                        "source_face_jamb_thickness_delta_m": (
                            _round(source_jamb_thickness_delta)
                            if source_jamb_thickness_delta is not None else None),
                        "source_face_jamb_proved": bool(
                            complete_source_jambs
                            and source_jamb_thickness_delta <= .04 + _EPSILON),
                    })
        axis_options.sort(key=lambda value: (
            float(value["max_endpoint_wall_support_distance_m"]),
            -float(value["axis_midpoint_wall_clearance_m"]),
            abs(float(value["wall_face_separation_m"]) - .20),
            tuple(tuple(point) for point in value["axis_segment_cad_m"])))
        if not axis_options:
            continue
        selected_axis = axis_options[0]
        leaf_number += 1
        opening_candidates.append(_opening_candidate(
            f"cad_raw_opening_parallel_leaf_{leaf_number}", "door",
            [row["row"] for row in component],
            [int(row["index"]) for row in component],
            [tuple(selected_axis["axis_segment_cad_m"][0]),
             tuple(selected_axis["axis_segment_cad_m"][1])],
            leaf_length, .94,
            ["parallel_door_leaf_rails", "hinge_endpoint_wall_supported",
             "swing_leaf_without_arc", "wall_network_supported"],
            {"method": "cad_parallel_door_leaf_without_arc_v1",
             "source_row_count": len(component),
             "parallel_rail_count": len(component),
             "leaf_length_m": _round(leaf_length),
             "leaf_length_spread_m": _round(length_spread),
             "leaf_angle_spread_deg": _round(angle_spread),
             "hinge_endpoint_cluster_radius_m": _round(
                 first_radius if hinge_index == 0 else second_radius),
             "free_endpoint_cluster_radius_m": _round(
                 second_radius if hinge_index == 0 else first_radius),
             "hinge_wall_distance_m": _round(hinge_distance),
             "free_endpoint_wall_distance_m": _round(free_distance),
             "drawn_leaf_axis_cad_m": [
                 _point_list(hinge), _point_list(free_end)],
             "axis_candidates": copy.deepcopy(axis_options),
             "selected_wall_face_separation_m": selected_axis[
                 "wall_face_separation_m"],
             "thresholds": {
                 "minimum_parallel_rail_count": 3,
                 "maximum_parallel_rail_count": 5,
                 "maximum_axis_angle_spread_deg": 1.0,
                 "maximum_leaf_length_spread_m": max(.02, _round(
                     leaf_length * .02)),
                 "maximum_endpoint_cluster_radius_m": .08,
                 "maximum_hinge_wall_distance_m": .12,
                 "minimum_free_endpoint_wall_distance_m": .20,
                 "minimum_leaf_to_wall_axis_angle_difference_deg": 20.0,
                 "maximum_leaf_to_wall_axis_angle_difference_deg": 70.0,
                 "minimum_wall_face_separation_m": .06,
                 "maximum_wall_face_separation_m": .60,
             },
             "decision_basis": [
                 "three_or_more_independent_parallel_equal_length_leaf_rails",
                 "one_tight_endpoint_cluster_uniquely_supported_by_wall_network",
                 "opposite_endpoint_cluster_free_in_room_space",
                 "closed_axis_candidates_derived_from_measured_wall_face_pairs",
                 "no_layer_block_or_filename_semantics_used_for_decision",
             ]},
        ))

    # Deduplicate nested frames or duplicate CAD entities without discarding
    # their provenance; the first candidate accumulates all source evidence.
    def candidate_quality(row: Mapping[str, Any]) -> tuple:
        evidence_geometry = row.get("evidence_geometry") or {}
        endpoint_support = evidence_geometry.get("wall_endpoint_support_distance_m") or []
        try:
            endpoint_error = max(float(value) for value in endpoint_support)
        except (TypeError, ValueError):
            endpoint_error = float("inf")
        try:
            rail_angle_error = float(
                evidence_geometry.get("seed_rail_angle_difference_deg", 0.0))
        except (TypeError, ValueError):
            rail_angle_error = float("inf")
        try:
            rail_span_error = abs(
                float(evidence_geometry.get("short_span_m"))
                - float(evidence_geometry.get("seed_rail_separation_m")))
        except (TypeError, ValueError):
            rail_span_error = 0.0
        signed_offsets = [float(value) for value in (
            evidence_geometry.get("signed_wall_face_offsets_m") or [])]
        negative_offsets = [abs(value) for value in signed_offsets if value < 0]
        positive_offsets = [abs(value) for value in signed_offsets if value > 0]
        face_symmetry_error = (
            abs(min(negative_offsets) - min(positive_offsets))
            if negative_offsets and positive_offsets else float("inf"))
        return (
            rail_angle_error,
            rail_span_error,
            face_symmetry_error,
            endpoint_error,
            -float(row.get("width_m") or 0.0),
            len(row.get("source_handles") or []),
            str(row.get("candidate_id") or ""),
        )

    def merge_candidate_sources(target: dict, source: Mapping[str, Any], reason: str) -> None:
        target["source_handles"] = sorted(set(
            (target.get("source_handles") or []) + (source.get("source_handles") or [])))
        target["source_entity_indexes"] = sorted(set(
            (target.get("source_entity_indexes") or [])
            + (source.get("source_entity_indexes") or [])))
        target["reason_codes"] = sorted(set(
            (target.get("reason_codes") or []) + [reason]
            + (source.get("reason_codes") or [])))
        evidence_geometry = target.setdefault("evidence_geometry", {})
        merged_ids = evidence_geometry.setdefault("merged_candidate_ids", [])
        merged_ids.extend(
            value for value in [source.get("candidate_id"),
                                *((source.get("evidence_geometry") or {}).get(
                                    "merged_candidate_ids") or [])]
            if value and value not in merged_ids)
        source_evidence = source.get("evidence_geometry") \
            if isinstance(source.get("evidence_geometry"), Mapping) else {}
        if source_evidence.get("arc_radius_m"):
            circular_evidence = evidence_geometry.setdefault(
                "merged_circular_swing_evidence", [])
            source_id = str(source.get("candidate_id") or "")
            if not any(str(row.get("candidate_id") or "") == source_id
                       for row in circular_evidence if isinstance(row, Mapping)):
                circular_evidence.append({
                    "candidate_id": source_id,
                    "source_handles": copy.deepcopy(
                        source.get("source_handles") or []),
                    "source_entity_indexes": copy.deepcopy(
                        source.get("source_entity_indexes") or []),
                    "evidence_geometry": copy.deepcopy(source_evidence),
                })

    deduplicated: list[dict] = []
    for candidate in sorted(opening_candidates, key=lambda row: (
            str(row.get("kind") or ""), tuple(row.get("center_cad_m") or []),
            -float(row.get("confidence") or 0))):
        def candidates_duplicate(row: Mapping[str, Any]) -> bool:
            if (row.get("kind") != candidate.get("kind")
                    or abs(float(row.get("width_m") or 0)
                           - float(candidate.get("width_m") or 0)) > .12):
                return False
            if math.dist(row.get("center_cad_m") or (0, 0),
                         candidate.get("center_cad_m") or (0, 0)) <= .08:
                return True
            if str(candidate.get("kind") or "") != "door":
                return False
            row_evidence = row.get("evidence_geometry") or {}
            candidate_evidence = candidate.get("evidence_geometry") or {}
            representations = {
                "parallel_leaf" if evidence.get("method")
                == "cad_parallel_door_leaf_without_arc_v1" else
                "circular_swing" if evidence.get("arc_radius_m") else ""
                for evidence in (row_evidence, candidate_evidence)
            }
            shared_handles = set(str(value) for value in
                                 row.get("source_handles") or [] if str(value)) \
                .intersection(str(value) for value in
                              candidate.get("source_handles") or [] if str(value))
            return representations == {"parallel_leaf", "circular_swing"} \
                and bool(shared_handles)

        duplicate = next((row for row in deduplicated
                          if candidates_duplicate(row)), None)
        if duplicate is None:
            deduplicated.append(candidate)
            continue
        if candidate.get("kind") == "door":
            duplicate_is_leaf = ((duplicate.get("evidence_geometry") or {}).get(
                "method") == "cad_parallel_door_leaf_without_arc_v1")
            candidate_is_leaf = ((candidate.get("evidence_geometry") or {}).get(
                "method") == "cad_parallel_door_leaf_without_arc_v1")
            if candidate_is_leaf and not duplicate_is_leaf:
                duplicate_index = deduplicated.index(duplicate)
                merge_candidate_sources(
                    candidate, duplicate,
                    "shared_radial_leaf_circular_swing_geometry_merged")
                deduplicated[duplicate_index] = candidate
                continue
            if duplicate_is_leaf and not candidate_is_leaf:
                merge_candidate_sources(
                    duplicate, candidate,
                    "shared_radial_leaf_circular_swing_geometry_merged")
                continue
        if (candidate.get("kind") == "window"
                and candidate_quality(candidate) < candidate_quality(duplicate)):
            duplicate_index = deduplicated.index(duplicate)
            merge_candidate_sources(candidate, duplicate, "duplicate_geometry_merged")
            deduplicated[duplicate_index] = candidate
        else:
            merge_candidate_sources(duplicate, candidate, "duplicate_geometry_merged")

    # Double-leaf doors are drawn as two mirrored swing arcs whose non-leaf
    # radius endpoints meet at the opening centre.  Treating them as two doors
    # creates adjacent half-width openings.  Merge only the strict mirrored
    # geometry: equal radii, common tips, collinear hinges and hinge distance
    # equal to the sum of both leaf lengths.
    doors = [row for row in deduplicated if row.get("kind") == "door"]
    non_doors = [row for row in deduplicated if row.get("kind") != "door"]

    def mirrored_door_pair(first: Mapping[str, Any], second: Mapping[str, Any]) -> Optional[dict]:
        first_evidence = first.get("evidence_geometry") or {}
        second_evidence = second.get("evidence_geometry") or {}
        try:
            first_hinge = tuple(map(float, first_evidence["arc_center_m"]))
            second_hinge = tuple(map(float, second_evidence["arc_center_m"]))
            first_radius = float(first_evidence["arc_radius_m"])
            second_radius = float(second_evidence["arc_radius_m"])
        except (KeyError, TypeError, ValueError):
            return None
        if abs(first_radius - second_radius) > .03:
            return None
        hinge_distance = math.dist(first_hinge, second_hinge)
        if abs(hinge_distance - (first_radius + second_radius)) > .08:
            return None

        def meeting_tips(row: Mapping[str, Any], hinge: tuple[float, float]) -> list[tuple[float, float]]:
            result = []
            for option in (row.get("evidence_geometry") or {}).get("axis_candidates") or []:
                axis = option.get("axis_segment_cad_m") or []
                try:
                    points = [tuple(map(float, axis[0])), tuple(map(float, axis[-1]))]
                except (TypeError, ValueError, IndexError):
                    continue
                tip = max(points, key=lambda point: math.dist(point, hinge))
                if abs(math.dist(tip, hinge) - float(
                        (row.get("evidence_geometry") or {}).get("arc_radius_m") or 0)) <= .04:
                    result.append(tip)
            return result

        tip_pairs = [(left, right) for left in meeting_tips(first, first_hinge)
                     for right in meeting_tips(second, second_hinge)
                     if math.dist(left, right) <= .08]
        if not tip_pairs:
            return None
        hinge_angle = _axis_angle(first_hinge, second_hinge)
        if any(_undirected_angle_difference(
                hinge_angle, _axis_angle(first_hinge, tip)) > 3.0
               for tip, _ in tip_pairs):
            return None
        return {"hinges": (first_hinge, second_hinge),
                "meeting_tip_error_m": min(math.dist(*pair) for pair in tip_pairs)}

    merged_doors: list[dict] = []
    remaining_doors = list(doors)
    while remaining_doors:
        first = remaining_doors.pop(0)
        pair_index = None
        proof = None
        for index, second in enumerate(remaining_doors):
            candidate_proof = mirrored_door_pair(first, second)
            if candidate_proof is not None:
                pair_index, proof = index, candidate_proof
                break
        if pair_index is None or proof is None:
            merged_doors.append(first)
            continue
        second = remaining_doors.pop(pair_index)
        survivor = copy.deepcopy(min((first, second), key=candidate_quality))
        first_hinge, second_hinge = proof["hinges"]
        survivor["axis_segment_cad_m"] = [
            _point_list(first_hinge), _point_list(second_hinge)]
        survivor["center_cad_m"] = [
            _round((first_hinge[0] + second_hinge[0]) / 2),
            _round((first_hinge[1] + second_hinge[1]) / 2),
        ]
        survivor["width_m"] = _round(math.dist(first_hinge, second_hinge))
        for source in (first, second):
            if source.get("candidate_id") != survivor.get("candidate_id"):
                merge_candidate_sources(
                    survivor, source, "mirrored_double_leaf_geometry_merged")
        survivor.setdefault("evidence_geometry", {}).update({
            "double_leaf_door": True,
            "double_leaf_hinges_cad_m": [
                _point_list(first_hinge), _point_list(second_hinge)],
            "double_leaf_meeting_tip_error_m": _round(
                proof["meeting_tip_error_m"]),
        })
        merged_doors.append(survivor)
    deduplicated = non_doors + merged_doors

    # A multi-panel window is often drawn as two 0.7 m rails on either side of
    # a central mullion plus one 1.6 m outer frame.  Pair enumeration can emit
    # the full frame and both overlapping subframes.  Merge only collinear,
    # physically touching/overlapping window axes; separated windows remain
    # independent even when they share the same exterior wall.
    windows = [row for row in deduplicated if row.get("kind") == "window"]
    non_windows = [row for row in deduplicated if row.get("kind") != "window"]

    def window_axes_connected(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
        try:
            first_axis = LineString(first.get("axis_segment_cad_m") or [])
            second_axis = LineString(second.get("axis_segment_cad_m") or [])
        except (TypeError, ValueError):
            return False
        if first_axis.length <= _EPSILON or second_axis.length <= _EPSILON:
            return False
        first_coords = list(first_axis.coords)
        second_coords = list(second_axis.coords)
        if _undirected_angle_difference(
                _axis_angle(tuple(first_coords[0]), tuple(first_coords[-1])),
                _axis_angle(tuple(second_coords[0]), tuple(second_coords[-1]))) > 3.0:
            return False
        if first_axis.distance(second_axis) > .12:
            return False
        origin = tuple(first_coords[0])
        length = first_axis.length
        unit = ((first_coords[-1][0] - origin[0]) / length,
                (first_coords[-1][1] - origin[1]) / length)
        first_interval = (0.0, length)
        values = [
            (point[0] - origin[0]) * unit[0] + (point[1] - origin[1]) * unit[1]
            for point in second_coords
        ]
        second_interval = (min(values), max(values))
        gap = max(0.0, max(first_interval[0], second_interval[0])
                  - min(first_interval[1], second_interval[1]))
        return gap <= .12

    merged_windows: list[dict] = []
    remaining_windows = list(windows)
    while remaining_windows:
        cluster = [remaining_windows.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining_windows):
                if any(window_axes_connected(candidate, member) for member in cluster):
                    cluster.append(candidate)
                    remaining_windows.remove(candidate)
                    changed = True
        survivor = copy.deepcopy(min(cluster, key=candidate_quality))
        if len(cluster) > 1:
            raw_axis = survivor.get("axis_segment_cad_m") or []
            axis_start = (float(raw_axis[0][0]), float(raw_axis[0][1]))
            axis_end = (float(raw_axis[-1][0]), float(raw_axis[-1][1]))
            survivor_length = math.dist(axis_start, axis_end)
            survivor_unit = ((axis_end[0] - axis_start[0]) / survivor_length,
                             (axis_end[1] - axis_start[1]) / survivor_length)
            projections = [
                (float(point[0]) - axis_start[0]) * survivor_unit[0]
                + (float(point[1]) - axis_start[1]) * survivor_unit[1]
                for candidate in cluster
                for point in (candidate.get("axis_segment_cad_m") or [])
            ]
            union_start, union_end = min(projections), max(projections)
            merged_axis = [
                [axis_start[0] + union_start * survivor_unit[0],
                 axis_start[1] + union_start * survivor_unit[1]],
                [axis_start[0] + union_end * survivor_unit[0],
                 axis_start[1] + union_end * survivor_unit[1]],
            ]
            survivor["axis_segment_cad_m"] = [
                [_round(value) for value in point] for point in merged_axis]
            survivor["width_m"] = _round(union_end - union_start)
            survivor["center_cad_m"] = [
                _round((merged_axis[0][axis] + merged_axis[1][axis]) / 2)
                for axis in (0, 1)
            ]
            for candidate in cluster:
                if candidate is cluster[0] and candidate.get("candidate_id") == survivor.get(
                        "candidate_id"):
                    continue
                merge_candidate_sources(
                    survivor, candidate, "adjacent_subframe_geometry_merged")
            survivor.setdefault("evidence_geometry", {})[
                "merged_subframe_count"] = len(cluster)
        merged_windows.append(survivor)
    deduplicated = sorted(non_windows + merged_windows, key=lambda row: (
        str(row.get("kind") or ""), tuple(row.get("center_cad_m") or []),
        str(row.get("candidate_id") or "")))

    # A proved door/window motif can be authored as one compact INSERT root
    # that also contains tiny jamb caps, swing returns and symbol rails.  The
    # ARC and radial leaf alone establish the opening, but leaving the sibling
    # primitives as wall rows creates dozens of 30--60 mm fake WallAssembly
    # blockers.  Consume the complete root only when every independent source
    # fact says it is a compact opening symbol: one opening candidate names the
    # root, the root spans at most 2.5 m, and it contains no >1.6 m wall run.
    raw_by_index = {index: row for index, row in raw_indexed}
    for candidate in deduplicated:
        candidate_indexes = {
            int(value) for value in candidate.get("source_entity_indexes") or []
            if isinstance(value, int) or str(value).isdigit()}
        candidate_roots = {
            _row_root_handle(raw_by_index[index], index)
            for index in candidate_indexes if index in raw_by_index}
        if len(candidate_roots) != 1:
            continue
        root_handle = next(iter(candidate_roots))
        entries = raw_groups.get(root_handle) or []
        if not entries:
            continue
        bounds = _group_bounds([row for _, row in entries])
        spans = (float(bounds[2] - bounds[0]), float(bounds[3] - bounds[1]))
        segment_lengths = [
            math.dist(first, second) for _, row in entries
            for first, second in _row_segments(row)]
        try:
            opening_width = float(candidate.get("width_m"))
        except (TypeError, ValueError):
            continue
        if (not .35 <= opening_width <= 2.50
                or max(spans) > 2.50 + _EPSILON
                or max(segment_lengths, default=0.0) > 1.60 + _EPSILON):
            continue
        for index, _row in entries:
            if index not in selected_by_index:
                continue
            role_by_index[index] = (
                "opening_symbol", "high",
                ["proved_compact_opening_source_root",
                 "opening_root_geometry_not_wall"],
            )

    # A small standalone ARC deep inside a plan is commonly a basin/counter
    # detail.  It is not a curved wall unless its endpoints reach independent
    # wall runs.  This rule is deliberately narrow: a single source root,
    # radius <=300 mm, and both endpoints at least 80 mm from every other
    # >=300 mm structural run.  Connected arcs and architectural curves remain
    # wall evidence.
    for index, row in indexed:
        if role_by_index[index][0] != "wall_face":
            continue
        if str(row.get("entity_type") or "") != "ARC":
            continue
        root_handle = _row_root_handle(row, index)
        if len(raw_groups.get(root_handle) or []) != 1:
            continue
        circle = _circle_from_points(_normalise_points(row))
        if (not circle or not .03 <= float(circle["radius_m"]) <= .30
                or float(circle["sweep_deg"]) < 20.0
                or float(circle["sweep_deg"]) > 340.0):
            continue
        semantic_service_anchor = None
        if semantic_building_envelope_evidence:
            inner_envelope = Polygon(
                semantic_building_envelope_evidence["inner_polygon_m"])
            arc_center = Point(circle["center"])
            if (inner_envelope.covers(arc_center)
                    and float(arc_center.distance(inner_envelope.boundary))
                    >= .25 - _EPSILON):
                nearby_service_anchors = sorted(
                    (float(arc_center.distance(Point(anchor["point_m"]))), anchor)
                    for anchor in normalised_semantic_anchors
                    if anchor["semantic_profile"] in {
                        "kitchen", "bathroom", "storage", "utility"}
                    and float(arc_center.distance(Point(anchor["point_m"])))
                    <= 1.25 + _EPSILON
                )
                if nearby_service_anchors:
                    semantic_service_anchor = nearby_service_anchors[0]
        if semantic_service_anchor is not None:
            role_by_index[index] = (
                "context_fixture", "high",
                ["semantic_service_space_compact_arc_detail_not_wall"],
            )
            continue
        other_runs = [
            fact["line"] for fact in all_wall_line_facts
            if int(fact["index"]) != int(index)
            and float(fact["line"].length) >= .30]
        if not other_runs:
            continue
        support = unary_union(other_runs)
        endpoints = [Point(point) for point in circle["endpoints"]]
        endpoint_distances = [float(point.distance(support)) for point in endpoints]
        if min(endpoint_distances) < .08 - _EPSILON:
            continue
        role_by_index[index] = (
            "context_fixture", "high",
            ["isolated_compact_arc_detail_not_wall"],
        )

    # A source-proved attached exterior chain may contain a tiny face filler at
    # a mitred/stepped corner.  Below the minimum physical wall-face spacing it
    # cannot define another wall volume.  Keep it as high-confidence chain
    # provenance only when the parse-level double-boundary proof names this
    # exact entity; ordinary short lines remain untouched and fail closed.
    for index, row in indexed:
        attached = row.get("attached_exterior_boundary_evidence")
        segments = _row_segments(row)
        if not (isinstance(attached, Mapping)
                and attached.get("method")
                    == "cad_attached_exterior_double_boundary_v1"
                and str(attached.get("space_id") or "")
                and attached.get("chain_kind") in {"outer", "inner"}
                and int(index) in {
                    int(value) for value in
                    attached.get("chain_entity_indexes") or []
                    if isinstance(value, int) or str(value).isdigit()}
                and .06 <= float(attached.get(
                    "measured_boundary_separation_m") or 0.0) <= .60
                and len(segments) == 1
                and .02 <= math.dist(*segments[0]) < .06):
            continue
        role_by_index[index] = (
            "structural_evidence", "high",
            ["attached_exterior_short_chain_connector_evidence"])

    if semantic_building_envelope_evidence:
        envelope_outer_polygon = Polygon(
            semantic_building_envelope_evidence["outer_polygon_m"])
        envelope_inner_polygon = Polygon(
            semantic_building_envelope_evidence["inner_polygon_m"])
        envelope_source_indexes = {
            int(semantic_building_envelope_evidence["outer_entity_index"]),
            int(semantic_building_envelope_evidence["inner_entity_index"]),
        }
        for index, row in indexed:
            if index in envelope_source_indexes:
                continue
            if role_by_index[index][0] not in {
                    "wall_face", "wall_footprint", "review"}:
                continue
            if isinstance(row.get("attached_exterior_boundary_evidence"), Mapping):
                continue
            segments = _row_segments(row)
            if not segments:
                continue
            lines = [LineString([first, second]) for first, second in segments
                     if math.dist(first, second) > _EPSILON]
            if not lines:
                continue
            geometry = unary_union(lines)
            points = _normalise_points(row)
            is_closed = _is_closed(row, points)
            row_polygon = None
            if is_closed and len(points) >= 4:
                candidate_polygon = Polygon(_ring_points(points))
                if candidate_polygon.is_valid and candidate_polygon.area > _EPSILON:
                    row_polygon = candidate_polygon

            classification = ""
            metrics: dict[str, Any] = {}
            if (row_polygon is not None
                    and row_polygon.contains(envelope_outer_polygon)
                    and float(row_polygon.exterior.distance(
                        envelope_outer_polygon.exterior)) >= .60 - _EPSILON):
                classification = "enclosing_annotation_frame"
                metrics = {
                    "minimum_boundary_separation_m": _round(
                        row_polygon.exterior.distance(
                            envelope_outer_polygon.exterior)),
                }
            elif not geometry.intersects(envelope_outer_polygon.buffer(.02)):
                classification = "outside_building_envelope"
                metrics = {
                    "distance_to_outer_envelope_m": _round(
                        geometry.distance(envelope_outer_polygon)),
                }
            elif len(segments) == 1 and not is_closed:
                line = lines[0]
                outside_length = float(
                    line.difference(envelope_outer_polygon).length)
                inner_length = float(
                    line.intersection(envelope_inner_polygon).length)
                outside_endpoint_count = sum(
                    not envelope_outer_polygon.buffer(.01).covers(Point(point))
                    for point in segments[0])
                if (outside_length >= .60 - _EPSILON
                        and inner_length >= .30 - _EPSILON
                        and outside_endpoint_count >= 1):
                    classification = "dimension_axis_crosses_building_envelope"
                    metrics = {
                        "source_length_m": _round(line.length),
                        "outside_envelope_length_m": _round(outside_length),
                        "inside_inner_face_length_m": _round(inner_length),
                        "outside_endpoint_count": outside_endpoint_count,
                    }
            if not classification:
                continue
            reason = {
                "enclosing_annotation_frame":
                    "enclosing_annotation_frame_outside_semantic_envelope",
                "outside_building_envelope":
                    "geometry_outside_semantic_building_envelope",
                "dimension_axis_crosses_building_envelope":
                    "dimension_scaffolding_crosses_semantic_envelope",
            }[classification]
            compact_evidence = {
                "method": "cad_semantic_nested_building_envelope_filter_v1",
                "classification": classification,
                "outer_source_handles": copy.deepcopy(
                    semantic_building_envelope_evidence[
                        "outer_source_handles"]),
                "inner_source_handles": copy.deepcopy(
                    semantic_building_envelope_evidence[
                        "inner_source_handles"]),
                "semantic_anchor_count": int(
                    semantic_building_envelope_evidence[
                        "semantic_anchor_count"]),
                "semantic_profiles": copy.deepcopy(
                    semantic_building_envelope_evidence["semantic_profiles"]),
                "metrics": metrics,
                "decision_basis": [
                    "proved_semantic_nested_building_envelope",
                    classification,
                    "excluded_from_wall_topology",
                ],
            }
            row["semantic_building_envelope_filter_evidence"] = \
                copy.deepcopy(compact_evidence)
            semantic_envelope_evidence_by_index[index] = \
                copy.deepcopy(compact_evidence)
            role_by_index[index] = ("context_fixture", "high", [reason])

        # Section/elevation callouts are commonly inserted just outside the
        # building with one short leader penetrating only the exterior wall
        # band.  Per-row filtering correctly rejects the arrow/chevron but can
        # leave that shallow leader as a false wall.  Consume the complete
        # source root only when its geometry is compact and elongated, most of
        # its length is outside the proved outer contour, none reaches the
        # inner contour, the penetration is <=300 mm, and the same root carries
        # a genuinely non-collinear exterior companion.  No layer/block/name
        # semantics participate in this decision.
        for root_handle, entries in raw_groups.items():
            if any(isinstance(row.get("attached_exterior_boundary_evidence"),
                              Mapping) for _, row in entries):
                continue
            wall_indexes = [
                index for index, _row in entries
                if role_by_index.get(index, ("", "", []))[0]
                in {"wall_face", "wall_footprint", "review"}]
            exterior_indexes = [
                index for index, _row in entries
                if "geometry_outside_semantic_building_envelope"
                in role_by_index.get(index, ("", "", []))[2]]
            if not wall_indexes or not exterior_indexes or len(entries) < 2:
                continue
            root_segments = [
                segment for _index, row in entries for segment in _row_segments(row)
                if math.dist(*segment) > _EPSILON]
            if len(root_segments) < 2:
                continue
            root_lines = [LineString([first, second])
                          for first, second in root_segments]
            root_geometry = unary_union(root_lines)
            bounds = _group_bounds([row for _, row in entries])
            span_x = max(0.0, float(bounds[2]) - float(bounds[0]))
            span_y = max(0.0, float(bounds[3]) - float(bounds[1]))
            long_span, short_span = max(span_x, span_y), min(span_x, span_y)
            total_length = float(sum(line.length for line in root_lines))
            outside_length = float(
                root_geometry.difference(envelope_outer_polygon).length)
            outer_overlap_length = float(
                root_geometry.intersection(envelope_outer_polygon).length)
            inner_overlap_length = float(
                root_geometry.intersection(envelope_inner_polygon).length)
            orientations = []
            for first, second in root_segments:
                if math.dist(first, second) < .05 - _EPSILON:
                    continue
                angle = math.degrees(math.atan2(
                    float(second[1]) - float(first[1]),
                    float(second[0]) - float(first[0]))) % 180.0
                orientations.append(angle)
            non_collinear = any(
                min(abs(first - second), 180.0 - abs(first - second))
                >= 20.0 - _EPSILON
                for position, first in enumerate(orientations)
                for second in orientations[position + 1:])
            if (not .30 <= long_span <= 2.50
                    or short_span > .60 + _EPSILON
                    or long_span / max(short_span, .02) < 2.0 - _EPSILON
                    or total_length <= _EPSILON
                    or outside_length < .60 - _EPSILON
                    or outside_length / total_length < .60 - _EPSILON
                    or outer_overlap_length > .30 + _EPSILON
                    or inner_overlap_length > .02 + _EPSILON
                    or not non_collinear):
                continue
            compact_evidence = {
                "method": "cad_exterior_section_callout_root_filter_v1",
                "classification": "exterior_section_callout",
                "root_handle": root_handle,
                "source_entity_indexes": sorted(index for index, _ in entries),
                "metrics": {
                    "bbox_m": [_round(value) for value in bounds],
                    "long_span_m": _round(long_span),
                    "short_span_m": _round(short_span),
                    "source_length_m": _round(total_length),
                    "outside_outer_envelope_length_m": _round(outside_length),
                    "outer_envelope_overlap_length_m": _round(
                        outer_overlap_length),
                    "inner_envelope_overlap_length_m": _round(
                        inner_overlap_length),
                    "orientation_count": len(orientations),
                },
                "decision_basis": [
                    "proved_semantic_nested_building_envelope",
                    "same_source_root_has_exterior_non_collinear_companion",
                    "majority_of_compact_root_geometry_outside_outer_envelope",
                    "shallow_contact_does_not_reach_inner_building_face",
                    "excluded_from_wall_topology",
                ],
            }
            for index, row in entries:
                row["semantic_building_envelope_filter_evidence"] = \
                    copy.deepcopy(compact_evidence)
                semantic_envelope_evidence_by_index[index] = \
                    copy.deepcopy(compact_evidence)
                role_by_index[index] = (
                    "context_fixture", "high",
                    ["exterior_section_callout_shallow_envelope_contact"])

    evidence: list[dict] = []
    for evidence_number, (root_handle, entries) in enumerate(sorted(groups.items()), 1):
        rows = [row for _, row in entries]
        indexes = [index for index, _ in entries]
        decisions = [role_by_index[index] for index in indexes]
        role = (decisions[0][0] if len({decision[0] for decision in decisions}) == 1
                else "mixed")
        confidence = (decisions[0][1] if len({decision[1] for decision in decisions}) == 1
                      else "review")
        reasons = sorted({reason for decision in decisions for reason in decision[2]})
        bounds = _group_bounds(rows)
        segments = [segment for row in rows for segment in _row_segments(row)]
        opening_ids = [candidate["candidate_id"] for candidate in deduplicated
                       if set(indexes).intersection(candidate.get("source_entity_indexes") or [])]
        semantic_filter_proofs = [
            semantic_envelope_evidence_by_index[index]
            for index in indexes if index in semantic_envelope_evidence_by_index]
        common_semantic_filter_proof = (
            semantic_filter_proofs[0]
            if semantic_filter_proofs
            and len(semantic_filter_proofs) == len(indexes)
            and all(proof == semantic_filter_proofs[0]
                    for proof in semantic_filter_proofs)
            else None)
        evidence.append({
            "evidence_id": f"cad_role_evidence_{evidence_number}",
            **_group_source_fields(rows, indexes),
            "root_handle": root_handle,
            "bbox_m": [_round(value) for value in bounds],
            "total_length_m": _round(sum(math.dist(*segment) for segment in segments)),
            "max_segment_m": _round(max((math.dist(*segment) for segment in segments), default=0)),
            "closed_count": sum(_is_closed(row, _normalise_points(row)) for row in rows),
            "role": role, "confidence": confidence, "reason_codes": reasons,
            "retained_entity_indexes": [index for index in indexes
                                         if role_by_index[index][0].startswith("wall_")],
            "opening_candidate_ids": opening_ids,
            **({"fixture_envelopes": [copy.deepcopy(item)
                                      for index in indexes
                                      for item in fixture_envelope_evidence_by_index.get(index, [])]}
               if any(index in fixture_envelope_evidence_by_index for index in indexes) else {}),
            **({"stair_runs": [copy.deepcopy(item)
                                for index in indexes
                                for item in stair_run_evidence_by_index.get(index, [])]}
               if any(index in stair_run_evidence_by_index for index in indexes) else {}),
            **({"partial_context_splits": [copy.deepcopy(
                    partial_stair_splits_by_index[index])
                    for index in indexes if index in partial_stair_splits_by_index]}
               if any(index in partial_stair_splits_by_index for index in indexes)
               else {}),
            **({"counter_bands": [copy.deepcopy(item)
                                   for index in indexes
                                   for item in counter_band_evidence_by_index.get(index, [])]}
               if any(index in counter_band_evidence_by_index for index in indexes) else {}),
            **({"dense_fixture_groups": [copy.deepcopy(item)
                                          for index in indexes
                                          for item in dense_fixture_evidence_by_index.get(index, [])]}
               if any(index in dense_fixture_evidence_by_index for index in indexes) else {}),
            **({"micro_cross_marker_evidence": copy.deepcopy(
                    micro_marker_evidence_by_index[indexes[0]])}
               if indexes and all(index in micro_marker_evidence_by_index
                                  for index in indexes) else {}),
            **({"closed_wall_band_evidence": copy.deepcopy(
                    closed_wall_band_evidence_by_index[indexes[0]])
               } if len(indexes) == 1
               and indexes[0] in closed_wall_band_evidence_by_index else {}),
            **({"perimeter_wall_shell_evidence": copy.deepcopy(
                    perimeter_wall_shell_evidence_by_index[indexes[0]])
               } if len(indexes) == 1
               and indexes[0] in perimeter_wall_shell_evidence_by_index else {}),
            **({"semantic_building_envelope_filter_evidence": copy.deepcopy(
                    common_semantic_filter_proof)
               } if common_semantic_filter_proof is not None else {}),
            **({"attached_exterior_boundary_evidence": copy.deepcopy(
                    rows[0].get("attached_exterior_boundary_evidence"))}
               if len(rows) == 1 and isinstance(
                    rows[0].get("attached_exterior_boundary_evidence"), Mapping)
               else {}),
        })

    for proof in perimeter_wall_shell_proofs:
        shell_rows = [
            row for row in perimeter_wall_shell_rows
            if (row.get("perimeter_wall_shell_fragment_evidence") or {}).get(
                "shell_id") == proof["shell_id"]]
        bounds = _group_bounds(shell_rows)
        fragment_indexes = sorted(int(row["entity_index"]) for row in shell_rows)
        evidence.append({
            "evidence_id": f"cad_role_evidence_{len(evidence) + 1}",
            **copy.deepcopy(proof["source"]),
            "root_handle": proof["shell_id"],
            "bbox_m": [_round(value) for value in bounds],
            "total_length_m": _round(sum(
                math.dist(first, second) for row in shell_rows
                for first, second in _row_segments(row))),
            "max_segment_m": _round(max((
                math.dist(first, second) for row in shell_rows
                for first, second in _row_segments(row)), default=0.0)),
            "closed_count": 0,
            "role": "wall_face", "confidence": "high",
            "reason_codes": ["duplicate_nested_perimeter_wall_shell_geometry"],
            "retained_entity_indexes": fragment_indexes,
            "opening_candidate_ids": [],
            "perimeter_wall_shell_evidence": copy.deepcopy(proof),
        })

    for supplemental in supplemental_wall_evidence:
        evidence.append({
            "evidence_id": f"cad_role_evidence_{len(evidence) + 1}",
            **copy.deepcopy(supplemental["source"]),
            "root_handle": supplemental["root_handle"],
            "bbox_m": supplemental["bbox_m"],
            "total_length_m": supplemental["length_m"],
            "max_segment_m": supplemental["length_m"],
            "closed_count": 0,
            "role": "wall_face", "confidence": "high",
            "reason_codes": ["context_singleton_endpoint_bridge_geometry"],
            "retained_entity_indexes": copy.deepcopy(
                supplemental["source"]["entity_indexes"]),
            "opening_candidate_ids": [],
            "endpoint_bridge_evidence": {
                "evidence_kind": "context_singleton_endpoint_bridge_v1",
                "endpoint_supports": supplemental["endpoint_supports"],
                "thresholds": {
                    "minimum_length_m": .35,
                    "maximum_length_m": 5.0,
                    "maximum_endpoint_support_distance_m": NODE_SNAP_TOLERANCE_M,
                    "minimum_distinct_support_rows": 2,
                },
                "decision_basis": [
                    "singleton_context_source",
                    "both_endpoints_source_wall_supported",
                    "distinct_retained_wall_supports",
                ],
            },
        })

    for supplemental in supplemental_context_evidence:
        evidence.append({
            "evidence_id": f"cad_role_evidence_{len(evidence) + 1}",
            **copy.deepcopy(supplemental["source"]),
            "root_handle": supplemental["root_handle"],
            "bbox_m": supplemental["bbox_m"],
            "total_length_m": supplemental["length_m"],
            "max_segment_m": supplemental["length_m"],
            "closed_count": 0,
            "role": "context_fixture", "confidence": "high",
            "reason_codes": ["paired_context_nonwall_geometry"],
            "retained_entity_indexes": [],
            "opening_candidate_ids": [],
            "nonwall_context_evidence": copy.deepcopy(supplemental["proof"]),
        })

    roles: dict[str, int] = {}
    confidences: dict[str, int] = {"high": 0, "medium": 0, "review": 0}
    reasons: dict[str, int] = {}
    for role, confidence, reason_codes in role_by_index.values():
        roles[role] = roles.get(role, 0) + 1
        confidence_key = confidence if confidence in confidences else "medium"
        confidences[confidence_key] += 1
        for reason in reason_codes:
            reasons[reason] = reasons.get(reason, 0) + 1
    if supplemental_wall_rows:
        roles["wall_face"] = roles.get("wall_face", 0) + len(supplemental_wall_rows)
        confidences["high"] += len(supplemental_wall_rows)
        reasons["context_singleton_endpoint_bridge_geometry"] = len(
            supplemental_wall_rows)
    if perimeter_wall_shell_rows:
        roles["wall_face"] = roles.get("wall_face", 0) + len(
            perimeter_wall_shell_rows)
        confidences["high"] += len(perimeter_wall_shell_rows)
        reasons["duplicate_nested_perimeter_wall_shell_geometry"] = (
            reasons.get("duplicate_nested_perimeter_wall_shell_geometry", 0)
            + len(perimeter_wall_shell_rows))
    if supplemental_context_evidence:
        roles["context_fixture"] = roles.get("context_fixture", 0) + len(
            supplemental_context_evidence)
        confidences["high"] += len(supplemental_context_evidence)
        reasons["paired_context_nonwall_geometry"] = len(
            supplemental_context_evidence)
    def split_fragment(index: int, row: dict, kind: str) -> dict:
        evidence = partial_stair_splits_by_index[index]
        points = evidence[f"{kind}_points_m"]
        fragment = copy.deepcopy(row)
        fragment["points"] = [tuple(float(value) for value in point)
                              for point in points]
        fragment["bbox"] = (
            min(point[0] for point in fragment["points"]),
            min(point[1] for point in fragment["points"]),
            max(point[0] for point in fragment["points"]),
            max(point[1] for point in fragment["points"]),
        )
        fragment["closed"] = False
        fragment["partial_geometry_role"] = (
            "structural_wall_remainder" if kind == "structural"
            else "stair_landing_context_fragment")
        fragment["partial_geometry_evidence"] = copy.deepcopy(evidence)
        provenance = fragment.get("cad_provenance")
        if isinstance(provenance, dict):
            provenance["geometry_clip"] = {
                "method": "stair_terminal_interval_split_v1",
                "fragment_role": fragment["partial_geometry_role"],
                "fragment_points_m": copy.deepcopy(points),
            }
        return fragment

    wall_rows = [
        split_fragment(index, selected_by_index[index], "structural")
        if index in partial_stair_splits_by_index else selected_by_index[index]
        for index, decision in role_by_index.items()
        if decision[0] in {"wall_face", "wall_footprint"}
    ]
    wall_rows.extend(copy.deepcopy(supplemental_wall_rows))
    wall_rows.extend(copy.deepcopy(perimeter_wall_shell_rows))
    opening_rows = [selected_by_index[index] for index, decision in role_by_index.items()
                    if decision[0] == "opening_symbol"]
    context_selected = [selected_by_index[index] for index, decision in role_by_index.items()
                        if decision[0] == "context_fixture"]
    context_selected.extend(
        split_fragment(index, selected_by_index[index], "context")
        for index in sorted(partial_stair_splits_by_index))
    review_rows = [selected_by_index[index] for index, decision in role_by_index.items()
                   if decision[0] == "review"]
    summary = {
        "schema_version": _ROLE_SCHEMA_VERSION,
        "method": "cad_geometry_role_decomposition_v1",
        "input_entity_count": len(selected),
        "supplemental_context_input_entity_count": len(context),
        "supplemental_structural_entity_count": len(supplemental_wall_rows),
        "perimeter_wall_shell_fragment_count": len(perimeter_wall_shell_rows),
        "perimeter_wall_shell_count": len(perimeter_wall_shell_proofs),
        "semantic_building_envelope_status": (
            "proved" if semantic_building_envelope_evidence else "unproved"),
        "semantic_building_envelope_filtered_entity_count": len(
            semantic_envelope_evidence_by_index),
        "supplemental_context_fixture_count": len(supplemental_context_evidence),
        "retained_wall_entity_count": len(wall_rows),
        "opening_evidence_entity_count": len(opening_rows),
        "context_entity_count": len(context_selected),
        "partial_context_fragment_count": len(partial_stair_splits_by_index),
        "review_entity_count": len(review_rows),
        "source_root_count": len(groups),
        "role_counts": dict(sorted(roles.items())),
        "confidence_counts": confidences,
        "reason_counts": dict(sorted(reasons.items())),
    }
    return {
        "wall_rows": wall_rows,
        "opening_evidence_rows": opening_rows,
        "context_rows": context_selected,
        "review_rows": review_rows,
        "summary": summary,
        "evidence": evidence,
        "raw_opening_candidates": deduplicated,
        "raw_opening_summary": _raw_opening_summary(deduplicated),
        "semantic_building_envelope_evidence": copy.deepcopy(
            semantic_building_envelope_evidence),
        "semantic_building_envelope_diagnostics": copy.deepcopy(
            semantic_building_envelope_diagnostics),
    }


def stitch_wall_assemblies_across_openings(
    assemblies: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    origin_x: float = 0.0,
    origin_z: float = 0.0,
) -> list[dict]:
    """Create a canonical host wall only for a uniquely proven opening gap.

    A door symbol commonly occupies the deliberate gap between two collinear
    wall assemblies.  Binding it to either fragment would cut a second false
    hole.  When the two fragments have identical measured thickness/height and
    the CAD opening axis accounts for their entire gap (apart from <=80 mm of
    jamb/face offset at either end), replace them with one source-backed host.
    No source endpoint is snapped or rewritten by this operation.
    """
    result = [copy.deepcopy(dict(row)) for row in assemblies]

    def accepted_axis(row: Mapping[str, Any]) -> Optional[tuple[tuple[float, float],
                                                                  tuple[float, float]]]:
        if str(row.get("review_status") or "") not in {"accepted", "confirmed"}:
            return None
        raw = row.get("opening_axis") or row.get("centerline") or []
        try:
            first = (float(raw[0][0]), float(raw[0][1]))
            second = (float(raw[-1][0]), float(raw[-1][1]))
        except (TypeError, ValueError, IndexError):
            return None
        return (first, second) if math.dist(first, second) > .1 else None

    def candidate_axes(candidate: Mapping[str, Any]) -> list[tuple[tuple[float, float],
                                                                    tuple[float, float],
                                                                    list[list[float]]]]:
        raw_axes = [candidate.get("axis_segment_cad_m") or []]
        raw_axes.extend(
            row.get("axis_segment_cad_m") or []
            for row in (candidate.get("evidence_geometry") or {}).get("axis_candidates") or []
        )
        axes: list[tuple[tuple[float, float], tuple[float, float], list[list[float]]]] = []
        seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
        for raw in raw_axes:
            try:
                cad_first = (float(raw[0][0]), float(raw[0][1]))
                cad_second = (float(raw[-1][0]), float(raw[-1][1]))
            except (TypeError, ValueError, IndexError):
                continue
            key = tuple(sorted((
                tuple(_round(value) for value in cad_first),
                tuple(_round(value) for value in cad_second),
            )))
            if key in seen or math.dist(cad_first, cad_second) <= .1:
                continue
            seen.add(key)
            axes.append((
                (cad_first[0] - origin_x, cad_first[1] - origin_z),
                (cad_second[0] - origin_x, cad_second[1] - origin_z),
                [_point_list(cad_first), _point_list(cad_second)],
            ))
        return axes

    for candidate in sorted(candidates, key=lambda row: str(row.get("candidate_id") or "")):
        pair_matches: list[dict] = []
        for opening_first, opening_second, cad_axis in candidate_axes(candidate):
            opening_width = math.dist(opening_first, opening_second)
            opening_angle = _axis_angle(opening_first, opening_second)
            viable = []
            for index, assembly in enumerate(result):
                axis = accepted_axis(assembly)
                if axis is None:
                    continue
                wall_first, wall_second = axis
                if _undirected_angle_difference(
                        opening_angle, _axis_angle(wall_first, wall_second)) > ANGLE_TOLERANCE_DEG:
                    continue
                try:
                    thickness = float(assembly.get("thickness_m"))
                    height = float(assembly.get("height_m"))
                except (TypeError, ValueError):
                    continue
                if not (.06 <= thickness <= .60 and height > 0):
                    continue
                viable.append((index, assembly, wall_first, wall_second, thickness, height))
            for left_position, left in enumerate(viable):
                for right in viable[left_position + 1:]:
                    left_index, left_row, left_first, left_second, left_thickness, left_height = left
                    right_index, right_row, right_first, right_second, right_thickness, right_height = right
                    if (abs(left_thickness - right_thickness) > .005
                            or abs(left_height - right_height) > .005):
                        continue
                    unit = (
                        (left_second[0] - left_first[0]) / math.dist(left_first, left_second),
                        (left_second[1] - left_first[1]) / math.dist(left_first, left_second),
                    )
                    # The axes must be truly collinear; this is deliberately
                    # stricter than the ordinary 20 mm node-snap contract.
                    cross_offsets = [abs(
                        (point[0] - left_first[0]) * unit[1]
                        - (point[1] - left_first[1]) * unit[0]
                    ) for point in (right_first, right_second)]
                    if max(cross_offsets) > .005:
                        continue
                    left_interval = sorted(_projection(point, left_first, unit)
                                           for point in (left_first, left_second))
                    right_interval = sorted(_projection(point, left_first, unit)
                                            for point in (right_first, right_second))
                    if left_interval[0] > right_interval[0]:
                        left_interval, right_interval = right_interval, left_interval
                        left_index, right_index = right_index, left_index
                        left_row, right_row = right_row, left_row
                    gap_start, gap_end = left_interval[1], right_interval[0]
                    gap_width = gap_end - gap_start
                    if gap_width <= .20:
                        continue
                    opening_interval = sorted(_projection(point, left_first, unit)
                                              for point in (opening_first, opening_second))
                    jamb_offsets = (
                        abs(opening_interval[0] - gap_start),
                        abs(opening_interval[1] - gap_end),
                    )
                    if max(jamb_offsets) > .08 or abs(gap_width - opening_width) > .16:
                        continue
                    pair_matches.append({
                        "score": (sum(jamb_offsets), abs(gap_width - opening_width),
                                  str(left_row.get("id") or ""), str(right_row.get("id") or "")),
                        "indexes": tuple(sorted((left_index, right_index))),
                        "rows": (left_row, right_row),
                        "axis_origin": left_first,
                        "unit": unit,
                        "outer_interval": (left_interval[0], right_interval[1]),
                        "gap_interval": (gap_start, gap_end),
                        "jamb_offsets": jamb_offsets,
                        "cad_axis": cad_axis,
                        "thickness": (left_thickness + right_thickness) / 2,
                        "height": (left_height + right_height) / 2,
                    })
        if not pair_matches:
            # Some CADs draw windows on top of one continuous exterior face
            # while the opposite face is interrupted by the frame.  A normal
            # two-assembly gap stitch cannot represent that convention.  A
            # window-only fallback is allowed when one accepted paired wall is
            # collinear, its measured thickness exactly matches the frame rail
            # spacing, and one of its original source faces continuously covers
            # the entire opening axis.  The source face, not a house-shape
            # heuristic, defines the extension bound.
            frame_evidence = candidate.get("evidence_geometry") or {}
            frame_thickness = frame_evidence.get("seed_rail_separation_m")
            extension_matches: list[dict] = []
            if (str(candidate.get("kind") or "") == "window"
                    and frame_evidence.get("opposite_wall_face_support") is True
                    and frame_evidence.get("grouping_method")
                    == "loose_maximal_parallel_rail_pair"):
                try:
                    frame_thickness = float(frame_thickness)
                except (TypeError, ValueError):
                    frame_thickness = 0.0
                for opening_first, opening_second, cad_axis in candidate_axes(candidate):
                    opening_line = LineString([opening_first, opening_second])
                    opening_width = opening_line.length
                    opening_angle = _axis_angle(opening_first, opening_second)
                    for index, assembly in enumerate(result):
                        wall_axis = accepted_axis(assembly)
                        if wall_axis is None:
                            continue
                        wall_first, wall_second = wall_axis
                        if _undirected_angle_difference(
                                opening_angle,
                                _axis_angle(wall_first, wall_second)) > ANGLE_TOLERANCE_DEG:
                            continue
                        try:
                            thickness = float(assembly.get("thickness_m"))
                            height = float(assembly.get("height_m"))
                        except (TypeError, ValueError):
                            continue
                        if (not .06 <= frame_thickness <= .60
                                or abs(thickness - frame_thickness) > .005
                                or height <= 0):
                            continue
                        wall_length = math.dist(wall_first, wall_second)
                        wall_unit = (
                            (wall_second[0] - wall_first[0]) / wall_length,
                            (wall_second[1] - wall_first[1]) / wall_length,
                        )
                        transverse_offsets = [abs(
                            (point[0] - wall_first[0]) * wall_unit[1]
                            - (point[1] - wall_first[1]) * wall_unit[0]
                        ) for point in (opening_first, opening_second)]
                        if max(transverse_offsets) > .005:
                            continue
                        wall_interval = sorted(_projection(point, wall_first, wall_unit)
                                               for point in (wall_first, wall_second))
                        opening_interval = sorted(
                            _projection(point, wall_first, wall_unit)
                            for point in (opening_first, opening_second))
                        overlap = max(
                            0.0,
                            min(wall_interval[1], opening_interval[1])
                            - max(wall_interval[0], opening_interval[0]),
                        )
                        if overlap < .40 or overlap / opening_width < .25:
                            continue
                        face_matches: list[dict] = []
                        for entity in assembly.get("source_entities") or []:
                            raw_segment = entity.get("model_segment_m") or []
                            try:
                                face_first = (float(raw_segment[0][0]),
                                              float(raw_segment[0][1]))
                                face_second = (float(raw_segment[-1][0]),
                                               float(raw_segment[-1][1]))
                            except (TypeError, ValueError, IndexError):
                                continue
                            face_line = LineString([face_first, face_second])
                            if (face_line.length <= .1
                                    or _undirected_angle_difference(
                                        opening_angle,
                                        _axis_angle(face_first, face_second))
                                    > ANGLE_TOLERANCE_DEG
                                    or abs(face_line.distance(opening_line)
                                           - thickness / 2) > .02):
                                continue
                            face_interval = sorted(
                                _projection(point, wall_first, wall_unit)
                                for point in (face_first, face_second))
                            start_jamb = max(0.0, face_interval[0] - opening_interval[0])
                            end_jamb = max(0.0, opening_interval[1] - face_interval[1])
                            if max(start_jamb, end_jamb) > .08:
                                continue
                            face_matches.append({
                                "entity": entity,
                                "interval": face_interval,
                                "jamb_offsets": (start_jamb, end_jamb),
                            })
                        if not face_matches:
                            continue
                        face_matches.sort(key=lambda row: (
                            sum(row["jamb_offsets"]),
                            -(row["interval"][1] - row["interval"][0]),
                            str((row["entity"] or {}).get("handle") or ""),
                        ))
                        source_face = face_matches[0]
                        extension_matches.append({
                            "score": (abs(thickness - frame_thickness), -overlap,
                                      str(assembly.get("id") or "")),
                            "index": index, "row": assembly,
                            "axis_origin": wall_first, "unit": wall_unit,
                            "outer_interval": (
                                min(wall_interval[0], source_face["interval"][0]),
                                max(wall_interval[1], source_face["interval"][1]),
                            ),
                            "opening_interval": opening_interval,
                            "cad_axis": cad_axis, "thickness": thickness,
                            "height": height, "source_face": source_face,
                        })
            extension_matches.sort(key=lambda row: row["score"])
            if extension_matches:
                best_extension = extension_matches[0]
                unique_indexes = {row["index"] for row in extension_matches
                                  if abs(float(row["score"][0])
                                         - float(best_extension["score"][0])) <= .005}
                if len(unique_indexes) == 1:
                    first = (
                        best_extension["axis_origin"][0]
                        + best_extension["unit"][0]
                        * best_extension["outer_interval"][0],
                        best_extension["axis_origin"][1]
                        + best_extension["unit"][1]
                        * best_extension["outer_interval"][0],
                    )
                    second = (
                        best_extension["axis_origin"][0]
                        + best_extension["unit"][0]
                        * best_extension["outer_interval"][1],
                        best_extension["axis_origin"][1]
                        + best_extension["unit"][1]
                        * best_extension["outer_interval"][1],
                    )
                    thickness = float(best_extension["thickness"])
                    footprint = LineString([first, second]).buffer(
                        thickness / 2, cap_style=2, join_style=2)
                    source_row = best_extension["row"]
                    source_entities = [copy.deepcopy(entity)
                                       for entity in source_row.get("source_entities") or []]
                    candidate_id = str(candidate.get("candidate_id") or "window")
                    host = {
                        "id": f"cad_wall_window_host_{candidate_id}",
                        "source_representation": "window_frame_host_extension",
                        "resolved_as": "centerline",
                        "start": _point_dict(first), "end": _point_dict(second),
                        "centerline": [_point_list(first), _point_list(second)],
                        "opening_axis": [_point_list(first), _point_list(second)],
                        "length_m": _round(math.dist(first, second)),
                        "thickness_m": _round(thickness),
                        "thickness_source": (
                            "window_frame_rail_spacing_and_source_wall_assembly"),
                        "height_m": _round(best_extension["height"]),
                        "height_source": "matched_source_wall_assembly",
                        "footprint_polygon": _polygon_coordinates(footprint),
                        "boundary_kind": "window_frame_host_extension",
                        "kind": str(source_row.get("kind") or "interior"),
                        "source": "cad", "review_status": "accepted",
                        "confidence_grade": "A", "confidence": 1.0,
                        "legacy_wall_compatible": True,
                        "window_frame_host_evidence": {
                            "candidate_id": candidate_id,
                            "source_wall_assembly_id": str(source_row.get("id") or ""),
                            "source_face_handle": str(
                                best_extension["source_face"]["entity"].get("handle") or ""),
                            "opening_source_handles": sorted(set(
                                str(value) for value in candidate.get("source_handles") or []
                                if str(value))),
                            "opening_axis_cad_m": copy.deepcopy(best_extension["cad_axis"]),
                            "frame_rail_separation_m": _round(frame_thickness),
                            "wall_thickness_m": _round(thickness),
                            "opening_overlap_m": _round(
                                min(best_extension["opening_interval"][1],
                                    best_extension["outer_interval"][1])
                                - max(best_extension["opening_interval"][0],
                                      best_extension["outer_interval"][0])),
                            "source_face_interval_m": [
                                _round(value) for value in
                                best_extension["source_face"]["interval"]],
                            "host_interval_m": [
                                _round(value) for value in
                                best_extension["outer_interval"]],
                            "max_axis_offset_m": .005,
                            "max_thickness_delta_m": .005,
                            "max_source_face_jamb_m": .08,
                        },
                        "cad_provenance": _compat_provenance(
                            source_entities, "window_frame_host_extension"),
                        **_evidence_fields(source_entities),
                    }
                    result.pop(best_extension["index"])
                    result.append(host)
            continue
        pair_matches.sort(key=lambda row: row["score"])
        best = pair_matches[0]
        # Require one unique physical pair.  Multiple equally plausible host
        # pairs remain review evidence instead of receiving a guessed wall.
        if any(row["indexes"] != best["indexes"]
               and abs(float(row["score"][0]) - float(best["score"][0])) <= .01
               for row in pair_matches[1:]):
            continue
        first = (
            best["axis_origin"][0] + best["unit"][0] * best["outer_interval"][0],
            best["axis_origin"][1] + best["unit"][1] * best["outer_interval"][0],
        )
        second = (
            best["axis_origin"][0] + best["unit"][0] * best["outer_interval"][1],
            best["axis_origin"][1] + best["unit"][1] * best["outer_interval"][1],
        )
        thickness = float(best["thickness"])
        footprint = LineString([first, second]).buffer(
            thickness / 2, cap_style=2, join_style=2)
        source_entities = [
            copy.deepcopy(entity)
            for row in best["rows"]
            for entity in row.get("source_entities") or []
        ]
        candidate_id = str(candidate.get("candidate_id") or "opening")
        host = {
            "id": f"cad_wall_opening_host_{candidate_id}",
            "source_representation": "opening_host_stitch",
            "resolved_as": "centerline",
            "start": _point_dict(first), "end": _point_dict(second),
            "centerline": [_point_list(first), _point_list(second)],
            "opening_axis": [_point_list(first), _point_list(second)],
            "length_m": _round(math.dist(first, second)),
            "thickness_m": _round(thickness),
            "thickness_source": "matched_adjacent_cad_wall_assemblies",
            "height_m": _round(best["height"]),
            "height_source": "matched_adjacent_cad_wall_assemblies",
            "footprint_polygon": _polygon_coordinates(footprint),
            "boundary_kind": "opening_host_stitch",
            "kind": "interior", "source": "cad",
            "review_status": "accepted", "confidence_grade": "A", "confidence": 1.0,
            "legacy_wall_compatible": True,
            "opening_host_evidence": {
                "candidate_id": candidate_id,
                "source_wall_assembly_ids": [str(row.get("id") or "")
                                             for row in best["rows"]],
                "opening_axis_cad_m": copy.deepcopy(best["cad_axis"]),
                "gap_interval_m": [_round(value) for value in best["gap_interval"]],
                "jamb_offsets_m": [_round(value) for value in best["jamb_offsets"]],
                "max_jamb_offset_m": .08,
                "max_gap_width_delta_m": .16,
            },
            "cad_provenance": _compat_provenance(source_entities, "opening_host_stitch"),
            **_evidence_fields(source_entities),
        }
        for index in sorted(best["indexes"], reverse=True):
            result.pop(index)
        result.append(host)
    return result


def bind_raw_geometry_openings(
    candidates: Sequence[Mapping[str, Any]],
    assemblies: Sequence[Mapping[str, Any]],
    *,
    origin_x: float = 0.0,
    origin_z: float = 0.0,
) -> list[dict]:
    """Attach raw door/window candidates to accepted canonical wall axes."""
    axes: list[tuple[Mapping[str, Any], tuple[float, float], tuple[float, float]]] = []
    for assembly in assemblies:
        if assembly.get("review_status") not in {"accepted", "confirmed"}:
            continue
        raw_axis = assembly.get("opening_axis") or assembly.get("centerline") or []
        if not isinstance(raw_axis, list) or len(raw_axis) < 2:
            continue
        try:
            first = tuple(map(float, raw_axis[0]))
            second = tuple(map(float, raw_axis[-1]))
        except (TypeError, ValueError):
            continue
        if math.dist(first, second) > .1:
            axes.append((assembly, first, second))
    result: list[dict] = []
    occupied: dict[str, list[tuple[float, float]]] = {}
    for raw_candidate in candidates:
        candidate = copy.deepcopy(dict(raw_candidate))
        raw_axis_options: list[tuple[str, list[Any]]] = [
            ("role_decomposition_primary", candidate.get("axis_segment_cad_m") or [])]
        evidence_geometry = candidate.get("evidence_geometry") or {}
        for axis_number, axis_evidence in enumerate(
                evidence_geometry.get("axis_candidates") or [], 1):
            raw_axis_options.append((
                f"role_decomposition_candidate_{axis_number}",
                axis_evidence.get("axis_segment_cad_m") or [],
            ))
        axis_options: list[tuple[str, list[list[float]], tuple[float, float],
                                 tuple[float, float]]] = []
        seen_axes: set[tuple[tuple[float, float], tuple[float, float]]] = set()
        for source, source_axis in raw_axis_options:
            try:
                cad_first = (float(source_axis[0][0]), float(source_axis[0][1]))
                cad_second = (float(source_axis[-1][0]), float(source_axis[-1][1]))
            except (TypeError, ValueError, IndexError):
                continue
            key = tuple(sorted((
                tuple(_round(value) for value in cad_first),
                tuple(_round(value) for value in cad_second),
            )))
            if key in seen_axes or math.dist(cad_first, cad_second) <= .1:
                continue
            seen_axes.add(key)
            axis_options.append((
                source,
                [_point_list(cad_first), _point_list(cad_second)],
                (cad_first[0] - origin_x, cad_first[1] - origin_z),
                (cad_second[0] - origin_x, cad_second[1] - origin_z),
            ))
        if not axis_options:
            candidate["status"] = "rejected"
            candidate.setdefault("reason_codes", []).append("opening_axis_invalid")
            result.append(candidate)
            continue
        if (str(candidate.get("kind") or "") == "door"
                and evidence_geometry.get("method")
                == "cad_parallel_door_leaf_without_arc_v1"):
            # An open leaf without an ARC has two symmetric closed positions.
            # A nearby local wall axis can make the wrong direction appear
            # plausible and can also clip the measured leaf length.  Preserve
            # every source-derived axis for the later whole-plan jamb/terminal
            # proof; local projection is not sufficient production authority.
            candidate["status"] = "review"
            candidate["reason_codes"] = sorted(set(
                (candidate.get("reason_codes") or []) + [
                    "parallel_leaf_requires_global_jamb_proof",
                    "opening_wall_assembly_unresolved",
                ]))
            result.append(candidate)
            continue
        matches = []
        for option_number, (axis_source, cad_axis, first, second) in enumerate(axis_options):
            candidate_angle = _axis_angle(first, second)
            for assembly, wall_start, wall_end in axes:
                wall_length = math.dist(wall_start, wall_end)
                wall_angle = _axis_angle(wall_start, wall_end)
                if _undirected_angle_difference(candidate_angle, wall_angle) > 10.0:
                    continue
                distances = [LineString([wall_start, wall_end]).distance(Point(point))
                             for point in (first, second)]
                allowed = max(.25, float(assembly.get("thickness_m") or 0) / 2 + .12)
                if max(distances) > allowed:
                    continue
                projections = [_projection(
                    point, wall_start,
                    ((wall_end[0] - wall_start[0]) / wall_length,
                     (wall_end[1] - wall_start[1]) / wall_length),
                ) for point in (first, second)]
                interval = (max(0.0, min(projections)), min(wall_length, max(projections)))
                width = interval[1] - interval[0]
                expected_width = float(candidate.get("width_m") or math.dist(first, second))
                if width < min(.30, expected_width * .60):
                    continue
                # A circular swing leaf is the dimensional authority for a
                # door width.  Clipping that leaf against a nearby short wall
                # assembly can otherwise turn a genuine 715 mm door into (for
                # example) a 559 mm opening and prevent the later whole-plan
                # jamb proof from seeing it.  Keep the permissive overlap rule
                # for non-door frame evidence, but do not locally bind a swing
                # door unless the complete measured leaf interval survives.
                reason_codes = {
                    str(value) for value in candidate.get("reason_codes") or []
                }
                if (str(candidate.get("kind") or "") == "door"
                        and "circular_swing_arc" in reason_codes
                        and abs(width - expected_width)
                        > max(.02, expected_width * .02) + _EPSILON):
                    continue
                matches.append((
                    max(distances), abs(width - expected_width), option_number,
                    assembly, interval, wall_length, axis_source, cad_axis,
                ))
        if not matches:
            candidate["status"] = "review"
            candidate.setdefault("reason_codes", []).append("opening_wall_assembly_unresolved")
            result.append(candidate)
            continue
        _, _, option_number, assembly, interval, _, axis_source, cad_axis = min(
            matches,
            key=lambda value: (
                value[0], value[1], value[2], str(value[3].get("id") or "")),
        )
        assembly_id = str(assembly.get("id") or "")
        overlaps = [existing for existing in occupied.get(assembly_id, [])
                    if min(existing[1], interval[1]) - max(existing[0], interval[0])
                    > min(interval[1] - interval[0], existing[1] - existing[0]) * .60]
        if overlaps:
            candidate["status"] = "rejected"
            candidate.setdefault("reason_codes", []).append("duplicate_opening_interval")
            result.append(candidate)
            continue
        occupied.setdefault(assembly_id, []).append(interval)
        candidate.update({
            "status": "accepted", "wall_assembly_id": assembly_id,
            "wall_source_handles": copy.deepcopy(assembly.get("source_entity_handles") or []),
            "axis_segment_cad_m": cad_axis,
            "center_cad_m": [
                _round((cad_axis[0][0] + cad_axis[-1][0]) / 2),
                _round((cad_axis[0][1] + cad_axis[-1][1]) / 2),
            ],
            "offset_m": _round(interval[0]),
            "width_m": _round(interval[1] - interval[0]),
        })
        candidate.setdefault("evidence_geometry", {})[
            "canonical_binding_axis_source"] = axis_source
        if option_number:
            candidate.setdefault("reason_codes", []).append(
                "alternate_swing_axis_selected_at_canonical_binding")
        candidate["reason_codes"] = sorted(set(
            reason for reason in (
                (candidate.get("reason_codes") or []) + ["canonical_wall_axis_bound"])
            if reason != "opening_wall_assembly_unresolved"))
        result.append(candidate)
    return result


def _compat_provenance(entities: Sequence[dict], representation: str) -> dict:
    primary = copy.deepcopy((entities[0].get("cad_provenance") or {}) if entities else {})
    primary["wall_assembly_source_representation"] = representation
    primary["source_entities"] = copy.deepcopy(list(entities))
    primary["source_segment_m"] = copy.deepcopy(
        (entities[0].get("source_segment_m") or []) if len(entities) == 1 else [])
    return primary


def _paired_assembly(identifier: str, first: _Segment, second: _Segment,
                     pair: Mapping[str, Any], height_m: float,
                     height_source: str) -> dict:
    origin = pair["axis_origin"]
    axis = pair["axis_unit"]
    start_t, end_t = pair["overlap_start"], pair["overlap_end"]
    a0 = _point_on_infinite_line(first, origin, axis, start_t)
    a1 = _point_on_infinite_line(first, origin, axis, end_t)
    b0 = _point_on_infinite_line(second, origin, axis, start_t)
    b1 = _point_on_infinite_line(second, origin, axis, end_t)
    footprint = Polygon([a0, a1, b1, b0])
    if not footprint.is_valid or footprint.area <= _EPSILON:
        raise WallAssemblyError("cad_wall_footprint_invalid",
                                "双线墙无法形成有效 footprint")
    center_start = ((a0[0] + b0[0]) / 2, (a0[1] + b0[1]) / 2)
    center_end = ((a1[0] + b1[0]) / 2, (a1[1] + b1[1]) / 2)
    entities = [_source_entity(first), _source_entity(second)]
    result = {
        "id": identifier,
        "source_representation": "paired_faces",
        "resolved_as": "paired_faces",
        "start": _point_dict(center_start),
        "end": _point_dict(center_end),
        "centerline": [_point_list(center_start), _point_list(center_end)],
        "opening_axis": [_point_list(center_start), _point_list(center_end)],
        "length_m": _round(math.dist(center_start, center_end)),
        "thickness_m": _round(float(pair["separation_m"])),
        "thickness_source": "cad_geometry",
        "height_m": _round(height_m),
        "height_source": height_source,
        "footprint_polygon": _polygon_coordinates(footprint),
        "boundary_kind": "paired_faces",
        "kind": "interior",
        "source": "cad",
        "review_status": "accepted",
        "confidence_grade": "A",
        "confidence": 1.0,
        "legacy_wall_compatible": True,
        "pairing_evidence": {
            "angle_difference_deg": _round(float(pair["angle_difference_deg"])),
            "face_separation_m": _round(float(pair["separation_m"])),
            "projected_overlap_ratio": _round(float(pair["overlap_ratio"])),
            "source_intervals_m": {
                str(first.index): [_round(value) for value in pair["first_source_interval_m"]],
                str(second.index): [_round(value) for value in pair["second_source_interval_m"]],
            },
            "thresholds": {
                "max_angle_difference_deg": ANGLE_TOLERANCE_DEG,
                "min_face_separation_m": MIN_FACE_SEPARATION_M,
                "max_face_separation_m": MAX_FACE_SEPARATION_M,
                "min_projected_overlap_ratio": MIN_PROJECTED_OVERLAP,
                "node_snap_tolerance_m": NODE_SNAP_TOLERANCE_M,
            },
        },
        "cad_provenance": _compat_provenance(entities, "paired_faces"),
        **_evidence_fields(entities),
    }
    return result


def _centerline_assembly(identifier: str, segment: _Segment, thickness_m: float,
                         thickness_source: str, height_m: float,
                         height_source: str, *, representation: str = "centerline",
                         review: Optional[dict] = None) -> dict:
    if not math.isfinite(thickness_m) or thickness_m <= 0:
        raise WallAssemblyError("cad_wall_thickness_invalid", "中心线墙厚必须大于零")
    centerline = LineString([segment.start, segment.end])
    footprint = centerline.buffer(thickness_m / 2, cap_style=2, join_style=2)
    entities = [_source_entity(segment)]
    result = {
        "id": identifier,
        "source_representation": representation,
        "resolved_as": "centerline",
        "start": _point_dict(segment.start),
        "end": _point_dict(segment.end),
        "centerline": [_point_list(segment.start), _point_list(segment.end)],
        "opening_axis": [_point_list(segment.start), _point_list(segment.end)],
        "length_m": _round(segment.length),
        "thickness_m": _round(thickness_m),
        "thickness_source": thickness_source,
        "height_m": _round(height_m),
        "height_source": height_source,
        "footprint_polygon": _polygon_coordinates(footprint),
        "boundary_kind": "centerline",
        "kind": "interior",
        "source": "cad",
        "review_status": "accepted",
        "confidence_grade": "A" if thickness_source in {"cad_geometry", "cad_attribute"} else "B",
        "confidence": 1.0,
        "legacy_wall_compatible": True,
        "cad_provenance": _compat_provenance(entities, representation),
        **_evidence_fields(entities),
    }
    if review:
        result["human_review"] = copy.deepcopy(review)
    return result


def _closed_footprint_assembly(identifier: str, row: Mapping[str, Any],
                               entity_index: int, points: Sequence[tuple[float, float]],
                               height_m: float, height_source: str) -> dict:
    ring = _ring_points(points)
    # A DXF entity may carry the closed flag even when its evaluated geometry
    # collapses to a point or a two-point return path.  Shapely quite rightly
    # rejects such input while constructing LinearRing; translate that library
    # exception into our audited fail-closed contract instead of leaking a 500.
    unique_points = {
        (_round(point[0]), _round(point[1]))
        for point in ring
    }
    if len(unique_points) < 3:
        raise WallAssemblyError(
            "cad_wall_footprint_invalid",
            "闭合墙体 footprint 至少需要三个不同顶点",
            details={
                "entity_index": entity_index,
                "point_count": len(ring),
                "unique_point_count": len(unique_points),
            },
        )
    try:
        polygon = Polygon(ring)
    except (TypeError, ValueError) as ex:
        raise WallAssemblyError(
            "cad_wall_footprint_invalid",
            "闭合墙体 footprint 无法形成有效多边形",
            details={
                "entity_index": entity_index,
                "point_count": len(ring),
                "unique_point_count": len(unique_points),
            },
        ) from ex
    if not polygon.is_valid or polygon.area <= _EPSILON:
        raise WallAssemblyError("cad_wall_footprint_invalid",
                                "闭合墙体 footprint 无效",
                                details={"entity_index": entity_index})
    provenance = _provenance(row)
    source_points = _normalise_points(row)
    entity = {
        "entity_index": int(row.get("entity_index", entity_index)),
        "segment_index": None,
        "handle": provenance.get("handle") or "",
        "root_handle": provenance.get("root_handle") or "",
        "source_handle": provenance.get("source_handle") or "",
        "layer": provenance.get("effective_layer") or provenance.get("layer") or "",
        "block": provenance.get("block") or "",
        "insert_chain": copy.deepcopy(provenance.get("insert_chain") or []),
        "source_polygon_m": [_point_list(point) for point in source_points],
        "cad_provenance": provenance,
    }
    # An elongated rectangular footprint has an unambiguous opening axis.  A
    # complex footprint is still valid wall geometry, but manual offset-based
    # openings must remain blocked until a wall run/edge is selected.
    rectangle = polygon.minimum_rotated_rectangle
    rectangle_coords = list(rectangle.exterior.coords)[:-1]
    edges = [(rectangle_coords[index], rectangle_coords[(index + 1) % 4]) for index in range(4)]
    lengths = [math.dist(first, second) for first, second in edges]
    long_index = max(range(4), key=lambda index: lengths[index])
    long_length = lengths[long_index]
    short_length = min(lengths)
    short_edges = [edge for index, edge in enumerate(edges)
                   if index % 2 != long_index % 2]
    representative_axis = [
        _point_list(((short_edges[0][0][0] + short_edges[0][1][0]) / 2,
                     (short_edges[0][0][1] + short_edges[0][1][1]) / 2)),
        _point_list(((short_edges[1][0][0] + short_edges[1][1][0]) / 2,
                     (short_edges[1][0][1] + short_edges[1][1][1]) / 2)),
    ] if len(short_edges) == 2 else None
    opening_axis: Optional[list[list[float]]] = None
    if rectangle.area > _EPSILON and polygon.area / rectangle.area >= .90 and long_length > short_length * 1.5:
        first_edge = edges[long_index]
        opposite_edge = edges[(long_index + 2) % 4]
        first_mid = ((first_edge[0][0] + first_edge[1][0]) / 2,
                     (first_edge[0][1] + first_edge[1][1]) / 2)
        second_mid = ((opposite_edge[0][0] + opposite_edge[1][0]) / 2,
                      (opposite_edge[0][1] + opposite_edge[1][1]) / 2)
        # Connect midpoints of the short sides, not of the long sides.
        if len(short_edges) == 2:
            first_mid = ((short_edges[0][0][0] + short_edges[0][1][0]) / 2,
                         (short_edges[0][0][1] + short_edges[0][1][1]) / 2)
            second_mid = ((short_edges[1][0][0] + short_edges[1][1][0]) / 2,
                          (short_edges[1][0][1] + short_edges[1][1][1]) / 2)
        opening_axis = [_point_list(first_mid), _point_list(second_mid)]
    band_proof = row.get("closed_wall_band_evidence") \
        if isinstance(row.get("closed_wall_band_evidence"), Mapping) else {}
    band_thickness = (float(band_proof.get("wall_thickness_m") or 0.0)
                      if band_proof else 0.0)
    band_centerline = representative_axis if (
        band_proof and representative_axis and .06 <= band_thickness <= .60) else None
    evidence = _evidence_fields([entity])
    result = {
        "id": identifier,
        "source_representation": "closed_footprint",
        "resolved_as": "closed_footprint",
        "footprint_polygon": _polygon_coordinates(polygon),
        "area_m2": _round(polygon.area),
        "thickness_m": (_round(band_thickness) if band_centerline
                        else _round(short_length) if opening_axis else None),
        "thickness_source": ("cad_closed_uniform_wall_band_edge_pairs"
                             if band_centerline else
                             "cad_geometry" if opening_axis else
                             "cad_geometry_variable"),
        "height_m": _round(height_m),
        "height_source": height_source,
        "boundary_kind": "closed_footprint",
        "kind": "interior",
        "source": "cad",
        "review_status": "accepted",
        "confidence_grade": "A",
        "confidence": 1.0,
        "legacy_wall_compatible": bool(opening_axis or band_centerline),
        "cad_provenance": _compat_provenance([entity], "closed_footprint"),
        **evidence,
    }
    if opening_axis:
        result["opening_axis"] = opening_axis
        result["centerline"] = copy.deepcopy(opening_axis)
        result["start"] = {"x": opening_axis[0][0], "z": opening_axis[0][1]}
        result["end"] = {"x": opening_axis[1][0], "z": opening_axis[1][1]}
        result["length_m"] = _round(math.dist(opening_axis[0], opening_axis[1]))
    elif band_centerline:
        result["centerline"] = copy.deepcopy(band_centerline)
        result["start"] = {"x": band_centerline[0][0],
                           "z": band_centerline[0][1]}
        result["end"] = {"x": band_centerline[1][0],
                         "z": band_centerline[1][1]}
        result["length_m"] = _round(math.dist(
            band_centerline[0], band_centerline[1]))
        result["centerline_scope"] = "representative_wall_band_axis"
        result["closed_wall_band_evidence"] = copy.deepcopy(band_proof)
        result["opening_blockers"] = [
            "cad_closed_wall_band_requires_specific_run_for_opening"]
    else:
        result["opening_blockers"] = ["cad_closed_footprint_opening_axis_ambiguous"]
    if row.get("wall_footprint_review_required"):
        result.update(
            review_status="needs_review",
            confidence_grade="C",
            confidence=0.0,
            production_blockers=["cad_closed_perimeter_wall_role_unproven"],
            reason_codes=["closed_perimeter_wall_role_unproven"],
        )
    return result


def _invalid_closed_footprint_assembly(
    identifier: str,
    row: Mapping[str, Any],
    entity_index: int,
    points: Sequence[tuple[float, float]],
    error: WallAssemblyError,
) -> dict:
    """Keep a degenerate closed entity as review evidence without aborting siblings."""
    provenance = _provenance(row)
    source_points = _normalise_points(row)
    entity = {
        "entity_index": int(row.get("entity_index", entity_index)),
        "segment_index": None,
        "handle": provenance.get("handle") or "",
        "root_handle": provenance.get("root_handle") or "",
        "source_handle": provenance.get("source_handle") or "",
        "layer": provenance.get("effective_layer") or provenance.get("layer") or "",
        "block": provenance.get("block") or "",
        "insert_chain": copy.deepcopy(provenance.get("insert_chain") or []),
        "source_polygon_m": [_point_list(point) for point in source_points],
        "model_polygon_m": [_point_list(point) for point in points],
        "cad_provenance": provenance,
    }
    result = {
        "id": identifier,
        "source_representation": "invalid_closed_footprint",
        "resolved_as": None,
        "footprint_polygon": None,
        "thickness_m": None,
        "thickness_source": "unresolved",
        "source": "cad",
        "review_status": "needs_review",
        "confidence_grade": "C",
        "confidence": 0.0,
        "legacy_wall_compatible": False,
        "reason_codes": [error.code],
        "production_blockers": [error.code],
        "validation_error": error.to_dict(),
        "cad_provenance": _compat_provenance([entity], "invalid_closed_footprint"),
        **_evidence_fields([entity]),
    }
    # Some CAD exporters encode a single visible wall/opening face as a
    # closed LWPOLYLINE that travels from A to B and immediately returns B to
    # A.  It is not a polygon and must remain fail-closed at this stage, but
    # retaining the unique source axis lets a later accepted-opening pass
    # prove (or reject) its audit-only role without inventing geometry.
    unique_points: list[tuple[float, float]] = []
    for point in points:
        if not any(math.dist(point, existing) <= _EPSILON
                   for existing in unique_points):
            unique_points.append(tuple(point))
    if len(unique_points) == 2:
        unique_length = math.dist(unique_points[0], unique_points[1])
        total_path_length = sum(
            math.dist(first, second) for first, second in zip(points, points[1:]))
        if unique_length > _EPSILON:
            result["source_centerline"] = [
                _point_list(unique_points[0]), _point_list(unique_points[1])]
            result["length_m"] = _round(unique_length)
            result["degenerate_return_path_evidence"] = {
                "method": "cad_closed_two_point_return_path_v1",
                "unique_point_count": 2,
                "unique_axis_model_m": copy.deepcopy(
                    result["source_centerline"]),
                "unique_axis_length_m": _round(unique_length),
                "source_path_length_m": _round(total_path_length),
                "return_length_ratio": _round(
                    total_path_length / unique_length),
                "decision_basis": [
                    "closed_source_has_exactly_two_unique_points",
                    "source_path_returns_along_same_axis",
                    "not_a_valid_polygon",
                ],
            }
    return result


def _ambiguous_assembly(identifier: str, segment: _Segment, reason: str) -> dict:
    entities = [_source_entity(segment)]
    return {
        "id": identifier,
        "source_representation": "human_confirmed_ambiguous",
        "resolved_as": None,
        "source_centerline": [_point_list(segment.start), _point_list(segment.end)],
        "length_m": _round(segment.length),
        "thickness_m": None,
        "thickness_source": "unresolved",
        "footprint_polygon": None,
        "source": "cad",
        "review_status": "needs_review",
        "confidence_grade": "C",
        "confidence": 0.0,
        "legacy_wall_compatible": False,
        "reason_codes": [reason],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": _compat_provenance(entities, "human_confirmed_ambiguous"),
        **_evidence_fields(entities),
    }


def _redundant_evidence_assembly(
    identifier: str,
    segment: _Segment,
    accepted: Mapping[str, Any],
    *,
    covered_length_m: float,
    uncovered_length_m: float,
    coverage_ratio: float,
    angle_difference_deg: float,
    accepted_supports: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict:
    """Retain a duplicate source line as rejected evidence, not wall geometry."""
    entities = [_source_entity(segment)]
    return {
        "id": identifier,
        "source_representation": "redundant_evidence",
        "resolved_as": "redundant_evidence",
        "source_centerline": [_point_list(segment.start), _point_list(segment.end)],
        "length_m": _round(segment.length),
        "thickness_m": None,
        "thickness_source": "not_applicable_redundant_evidence",
        "footprint_polygon": None,
        "source": "cad",
        "review_status": "rejected",
        "confidence_grade": "A",
        "confidence": 1.0,
        "legacy_wall_compatible": False,
        "reason_codes": ["cad_wall_source_redundant_with_accepted_footprint"],
        "production_blockers": [],
        "redundancy_evidence": {
            "accepted_wall_assembly_id": str(accepted.get("id") or ""),
            "accepted_source_representation": str(
                accepted.get("source_representation") or ""),
            "accepted_source_entity_handles": copy.deepcopy(
                accepted.get("source_entity_handles") or []),
            "accepted_wall_assembly_ids": sorted({
                str(row.get("wall_assembly_id") or "")
                for row in (accepted_supports or [])
                if str(row.get("wall_assembly_id") or "")
            }) or [str(accepted.get("id") or "")],
            "supporting_assemblies": copy.deepcopy(list(accepted_supports or [])),
            "footprint_buffer_m": REDUNDANT_EVIDENCE_BUFFER_M,
            "covered_length_m": _round(covered_length_m),
            "uncovered_length_m": _round(uncovered_length_m),
            "coverage_ratio": _round(coverage_ratio),
            "axis_angle_difference_deg": _round(angle_difference_deg),
            "decision_thresholds": {
                "max_uncovered_length_m": 1e-7,
                "max_axis_angle_difference_deg": ANGLE_TOLERANCE_DEG,
            },
        },
        "cad_provenance": _compat_provenance(entities, "redundant_evidence"),
        **_evidence_fields(entities),
    }


def _redundant_evidence_match(
    segment: _Segment,
    accepted_assemblies: Sequence[Mapping[str, Any]],
) -> Optional[dict]:
    """Return strict proof only for a fully covered, axis-aligned duplicate."""
    source_line = LineString([segment.start, segment.end])
    matches: list[dict] = []
    parallel_supports: list[dict] = []
    for accepted in accepted_assemblies:
        footprint_points = accepted.get("footprint_polygon") or []
        axis_points = accepted.get("centerline") or accepted.get("opening_axis") or []
        try:
            footprint = Polygon([
                (float(point[0]), float(point[1])) for point in footprint_points])
            axis_first = (float(axis_points[0][0]), float(axis_points[0][1]))
            axis_second = (float(axis_points[-1][0]), float(axis_points[-1][1]))
        except (TypeError, ValueError, IndexError):
            continue
        if (not footprint.is_valid or footprint.area <= _EPSILON
                or math.dist(axis_first, axis_second) <= _EPSILON):
            continue
        angle_difference = _undirected_angle_difference(
            _axis_angle(segment.start, segment.end),
            _axis_angle(axis_first, axis_second),
        )
        if angle_difference > ANGLE_TOLERANCE_DEG + _EPSILON:
            continue
        support = footprint.buffer(REDUNDANT_EVIDENCE_BUFFER_M)
        supported_length = float(source_line.intersection(support).length)
        if supported_length > 1e-7:
            parallel_supports.append({
                "accepted": accepted, "footprint": footprint,
                "angle_difference_deg": angle_difference,
                "supported_length_m": supported_length,
            })
        uncovered_length = float(source_line.difference(support).length)
        # Do not use a rounded percentage gate: any physically meaningful tail
        # outside the support footprint remains production unresolved.
        if uncovered_length > 1e-7:
            continue
        covered_length = max(0.0, segment.length - uncovered_length)
        matches.append({
            "accepted": accepted,
            "covered_length_m": covered_length,
            "uncovered_length_m": uncovered_length,
            "coverage_ratio": covered_length / max(segment.length, _EPSILON),
            "angle_difference_deg": angle_difference,
            "accepted_supports": [{
                "wall_assembly_id": str(accepted.get("id") or ""),
                "source_entity_handles": copy.deepcopy(
                    accepted.get("source_entity_handles") or []),
                "supported_length_m": _round(supported_length),
                "axis_angle_difference_deg": _round(angle_difference),
            }],
        })
    if parallel_supports:
        union_support = unary_union([
            row["footprint"] for row in parallel_supports
        ]).buffer(REDUNDANT_EVIDENCE_BUFFER_M)
        uncovered_length = float(source_line.difference(union_support).length)
        if uncovered_length <= 1e-7:
            contributors = [row for row in parallel_supports
                            if row["supported_length_m"] > 1e-7]
            contributors.sort(key=lambda row: str(
                row["accepted"].get("id") or ""))
            matches.append({
                "accepted": contributors[0]["accepted"],
                "covered_length_m": max(0.0, segment.length - uncovered_length),
                "uncovered_length_m": uncovered_length,
                "coverage_ratio": (segment.length - uncovered_length)
                / max(segment.length, _EPSILON),
                "angle_difference_deg": max(
                    float(row["angle_difference_deg"])
                    for row in contributors),
                "accepted_supports": [{
                    "wall_assembly_id": str(row["accepted"].get("id") or ""),
                    "source_entity_handles": copy.deepcopy(
                        row["accepted"].get("source_entity_handles") or []),
                    "supported_length_m": _round(row["supported_length_m"]),
                    "axis_angle_difference_deg": _round(
                        row["angle_difference_deg"]),
                } for row in contributors],
            })
    if not matches:
        return None
    return min(matches, key=lambda match: (
        float(match["angle_difference_deg"]),
        str(match["accepted"].get("id") or ""),
    ))


def _junction_evidence_match(
    segment: _Segment,
    accepted_assemblies: Sequence[Mapping[str, Any]],
) -> Optional[dict]:
    """Prove a short transverse source segment is a wall cap/junction.

    A cap is not another centreline wall.  It must be completely covered by
    accepted wall footprints, be no longer than the locally observed wall
    thickness, and be transverse to at least one supporting wall axis.
    """
    supports = []
    footprint_rows: list[dict] = []
    source_line = LineString([segment.start, segment.end])
    for accepted in accepted_assemblies:
        footprint_points = accepted.get("footprint_polygon") or []
        axis_points = accepted.get("centerline") or accepted.get("opening_axis") or []
        try:
            footprint = Polygon([(float(point[0]), float(point[1]))
                                 for point in footprint_points])
            axis_first = (float(axis_points[0][0]), float(axis_points[0][1]))
            axis_second = (float(axis_points[-1][0]), float(axis_points[-1][1]))
            thickness = float(accepted.get("thickness_m"))
        except (TypeError, ValueError, IndexError):
            continue
        if not footprint.is_valid or thickness <= 0:
            continue
        footprint_rows.append({
            "accepted": accepted, "footprint": footprint,
            "axis_first": axis_first, "axis_second": axis_second,
            "thickness": thickness,
        })
        if segment.length > thickness * 1.35 + NODE_SNAP_TOLERANCE_M:
            continue
        uncovered = float(source_line.difference(
            footprint.buffer(NODE_SNAP_TOLERANCE_M)).length)
        angle = _undirected_angle_difference(
            _axis_angle(segment.start, segment.end),
            _axis_angle(axis_first, axis_second))
        axis_endpoint_distance = min(
            float(source_line.distance(Point(axis_first))),
            float(source_line.distance(Point(axis_second))),
        )
        # A transverse line in the middle of a wall footprint may be a real
        # crossing wall or other unresolved evidence.  It is a terminal cap
        # only when it also reaches a canonical endpoint of the supporting
        # wall axis; full footprint coverage alone is circular proof.
        endpoint_limit = thickness / 2.0 + NODE_SNAP_TOLERANCE_M
        if (uncovered <= 1e-7 and angle >= 45.0
                and axis_endpoint_distance <= endpoint_limit + _EPSILON):
            supports.append({
                "wall_assembly_id": str(accepted.get("id") or ""),
                "source_entity_handles": copy.deepcopy(
                    accepted.get("source_entity_handles") or []),
                "wall_thickness_m": _round(thickness),
                "axis_angle_difference_deg": _round(angle),
                "axis_endpoint_distance_m": _round(axis_endpoint_distance),
                "axis_endpoint_distance_limit_m": _round(endpoint_limit),
                "uncovered_length_m": _round(uncovered),
            })
    if footprint_rows:
        union_support = unary_union([
            row["footprint"] for row in footprint_rows
        ]).buffer(NODE_SNAP_TOLERANCE_M)
        union_uncovered = float(source_line.difference(union_support).length)
        if union_uncovered <= 1e-7:
            for row in footprint_rows:
                thickness = float(row["thickness"])
                if segment.length > thickness * 1.35 + NODE_SNAP_TOLERANCE_M:
                    continue
                angle = _undirected_angle_difference(
                    _axis_angle(segment.start, segment.end),
                    _axis_angle(row["axis_first"], row["axis_second"]))
                axis_endpoint_distance = min(
                    float(source_line.distance(Point(row["axis_first"]))),
                    float(source_line.distance(Point(row["axis_second"]))),
                )
                endpoint_limit = thickness / 2.0 + NODE_SNAP_TOLERANCE_M
                if angle < 45.0 or axis_endpoint_distance > endpoint_limit + _EPSILON:
                    continue
                accepted = row["accepted"]
                supports.append({
                    "wall_assembly_id": str(accepted.get("id") or ""),
                    "source_entity_handles": copy.deepcopy(
                        accepted.get("source_entity_handles") or []),
                    "wall_thickness_m": _round(thickness),
                    "axis_angle_difference_deg": _round(angle),
                    "axis_endpoint_distance_m": _round(axis_endpoint_distance),
                    "axis_endpoint_distance_limit_m": _round(endpoint_limit),
                    "uncovered_length_m": _round(union_uncovered),
                    "coverage_method": "accepted_wall_footprint_union_v1",
                })
    if not supports and footprint_rows:
        face_cap_matches: list[dict] = []
        source_midpoint = source_line.interpolate(.5, normalized=True)
        for row in footprint_rows:
            thickness = float(row["thickness"])
            length_difference = abs(segment.length - thickness)
            if length_difference > max(.02, thickness * .05) + _EPSILON:
                continue
            angle = _undirected_angle_difference(
                _axis_angle(segment.start, segment.end),
                _axis_angle(row["axis_first"], row["axis_second"]))
            if angle < 89.0:
                continue
            endpoints = [Point(row["axis_first"]), Point(row["axis_second"])]
            endpoint_index = min(
                range(2), key=lambda value: endpoints[value].distance(source_line))
            endpoint = endpoints[endpoint_index]
            endpoint_line_distance = float(endpoint.distance(source_line))
            midpoint_distance = float(endpoint.distance(source_midpoint))
            expected_offset = thickness / 2.0
            if (abs(endpoint_line_distance - expected_offset) > .02 + _EPSILON
                    or abs(midpoint_distance - expected_offset) > .02 + _EPSILON):
                continue
            face_cap_matches.append({
                "wall_assembly_id": str(row["accepted"].get("id") or ""),
                "source_entity_handles": copy.deepcopy(
                    row["accepted"].get("source_entity_handles") or []),
                "wall_thickness_m": _round(thickness),
                "source_length_m": _round(segment.length),
                "length_difference_m": _round(length_difference),
                "axis_angle_difference_deg": _round(angle),
                "axis_endpoint_index": endpoint_index,
                "axis_endpoint_to_source_line_m": _round(endpoint_line_distance),
                "axis_endpoint_to_source_midpoint_m": _round(midpoint_distance),
                "expected_half_thickness_offset_m": _round(expected_offset),
            })
        if len(face_cap_matches) == 1:
            return {
                "support_method": "single_accepted_wall_face_cap_v1",
                "supports": [face_cap_matches[0]],
                "endpoint_support_ratio": 1.0,
                "coverage_ratio": 0.0,
                "uncovered_length_m": _round(segment.length),
                "source_length_m": _round(segment.length),
            }

        endpoint_supports: list[list[dict]] = []
        for endpoint_index, endpoint in enumerate((segment.start, segment.end)):
            point = Point(endpoint)
            matches = []
            for row in footprint_rows:
                thickness = float(row["thickness"])
                if segment.length > thickness * 1.35 + NODE_SNAP_TOLERANCE_M:
                    continue
                distance = float(point.distance(row["footprint"]))
                limit = thickness / 2.0 + NODE_SNAP_TOLERANCE_M
                if distance <= limit + _EPSILON:
                    matches.append({
                        "endpoint_index": endpoint_index,
                        "wall_assembly_id": str(
                            row["accepted"].get("id") or ""),
                        "source_entity_handles": copy.deepcopy(
                            row["accepted"].get("source_entity_handles") or []),
                        "wall_thickness_m": _round(thickness),
                        "endpoint_footprint_distance_m": _round(distance),
                        "endpoint_distance_limit_m": _round(limit),
                        "axis_angle_difference_deg": _round(
                            _undirected_angle_difference(
                                _axis_angle(segment.start, segment.end),
                                _axis_angle(row["axis_first"], row["axis_second"]))),
                        "axis_endpoint_distance_m": _round(min(
                            float(source_line.distance(Point(row["axis_first"]))),
                            float(source_line.distance(Point(row["axis_second"]))),
                        )),
                    })
            endpoint_supports.append(matches)
        distinct_pairs = [
            (left, right) for left in endpoint_supports[0]
            for right in endpoint_supports[1]
            if left["wall_assembly_id"] != right["wall_assembly_id"]
        ] if len(endpoint_supports) == 2 else []
        transverse_pairs = [pair for pair in distinct_pairs if any(
            float(row["axis_angle_difference_deg"]) >= 45.0
            and float(row["axis_endpoint_distance_m"])
            <= float(row["endpoint_distance_limit_m"]) + _EPSILON
            for row in pair)]
        if transverse_pairs:
            pair = min(transverse_pairs, key=lambda values: (
                sum(float(row["endpoint_footprint_distance_m"])
                    for row in values),
                tuple(row["wall_assembly_id"] for row in values),
            ))
            union_support = unary_union([
                row["footprint"] for row in footprint_rows
            ]).buffer(NODE_SNAP_TOLERANCE_M)
            uncovered = float(source_line.difference(union_support).length)
            return {
                "support_method":
                    "two_endpoint_distinct_accepted_wall_support_v1",
                "supports": sorted(
                    [copy.deepcopy(row) for row in pair],
                    key=lambda row: (row["endpoint_index"],
                                     row["wall_assembly_id"])),
                "endpoint_support_ratio": 1.0,
                "coverage_ratio": _round(max(
                    0.0, (segment.length - uncovered)
                    / max(segment.length, _EPSILON))),
                "uncovered_length_m": _round(uncovered),
                "source_length_m": _round(segment.length),
            }
    if not supports:
        return None
    unique_supports: dict[str, dict] = {}
    for row in supports:
        identifier = str(row["wall_assembly_id"])
        existing = unique_supports.get(identifier)
        if existing is None or row.get("coverage_method"):
            unique_supports[identifier] = row
    return {
        "supports": sorted(
            unique_supports.values(), key=lambda row: row["wall_assembly_id"]),
        "coverage_ratio": 1.0,
        "uncovered_length_m": 0.0,
    }


def _junction_evidence_assembly(
    identifier: str, segment: _Segment, proof: Mapping[str, Any],
) -> dict:
    entities = [_source_entity(segment)]
    return {
        "id": identifier,
        "source_representation": "junction_evidence",
        "resolved_as": "junction_evidence",
        "source_centerline": [_point_list(segment.start), _point_list(segment.end)],
        "length_m": _round(segment.length),
        "thickness_m": None,
        "thickness_source": "not_applicable_junction_evidence",
        "footprint_polygon": None,
        "source": "cad",
        "review_status": "rejected",
        "confidence_grade": "A",
        "confidence": 1.0,
        "legacy_wall_compatible": False,
        "reason_codes": ["cad_wall_source_is_transverse_cap_or_junction"],
        "production_blockers": [],
        "junction_evidence": copy.deepcopy(dict(proof)),
        "cad_provenance": _compat_provenance(entities, "junction_evidence"),
        **_evidence_fields(entities),
    }


def build_wall_assemblies(
    structural_rows: Sequence[Mapping[str, Any]],
    *,
    wall_height_m: float = 2.8,
    height_source: str = "project_default_assumption",
    id_prefix: str = "cad_wall_assembly_",
    origin_x: float = 0.0,
    origin_z: float = 0.0,
) -> list[dict]:
    """Build fail-closed wall assemblies from audited CAD structural rows.

    Open entities are paired automatically only when all four v1 thresholds are
    satisfied.  An open entity is accepted as an explicit centreline only when
    the row sets ``wall_role``/``source_representation`` to ``centerline`` *and*
    provides a positive ``thickness_m``.  Every other unpaired segment remains a
    ``human_confirmed_ambiguous`` review item.
    """
    if not math.isfinite(wall_height_m) or wall_height_m <= 0:
        raise WallAssemblyError("cad_wall_height_invalid", "墙高必须大于零")
    if not all(math.isfinite(value) for value in (origin_x, origin_z)):
        raise WallAssemblyError("cad_wall_origin_invalid", "CAD 模型原点必须是有限数值")
    assemblies: list[dict] = []
    open_segments: list[_Segment] = []
    segment_counter = 0
    for entity_index, row in enumerate(structural_rows):
        source_points = _normalise_points(row)
        if len(source_points) < 2:
            if row.get("closed"):
                error = WallAssemblyError(
                    "cad_wall_footprint_invalid",
                    "闭合墙体 footprint 归一化后没有足够的不同顶点",
                    details={
                        "entity_index": entity_index,
                        "point_count": len(source_points),
                        "unique_point_count": len(source_points),
                    },
                )
                assemblies.append(_invalid_closed_footprint_assembly(
                    f"{id_prefix}{len(assemblies) + 1}", row, entity_index,
                    source_points, error,
                ))
            continue
        points = [(point[0] - origin_x, point[1] - origin_z) for point in source_points]
        representation = str(row.get("source_representation") or row.get("wall_role") or "").strip()
        closed = _is_closed(row, points)
        if closed:
            if representation == "centerline":
                raise WallAssemblyError(
                    "cad_closed_centerline_unsupported",
                    "闭合实体不能作为单条中心线墙；请标记为 closed_footprint",
                    details={"entity_index": entity_index},
                )
            try:
                assembly = _closed_footprint_assembly(
                    f"{id_prefix}{len(assemblies) + 1}", row, entity_index,
                    points, wall_height_m, height_source,
                )
            except WallAssemblyError as ex:
                if ex.code != "cad_wall_footprint_invalid":
                    raise
                assembly = _invalid_closed_footprint_assembly(
                    f"{id_prefix}{len(assemblies) + 1}", row, entity_index,
                    points, ex,
                )
            assemblies.append(assembly)
            continue
        for segment_index, (first, second) in enumerate(zip(points, points[1:])):
            if math.dist(first, second) <= _EPSILON:
                continue
            open_segments.append(_Segment(segment_counter, entity_index, segment_index,
                                          first, second, row))
            segment_counter += 1
    open_segments = _snap_segment_endpoints(open_segments, NODE_SNAP_TOLERANCE_M)

    # Explicit centrelines are not offered to the double-face matcher.
    explicit_indexes: set[int] = set()
    for segment in open_segments:
        role = str(segment.row.get("source_representation")
                   or segment.row.get("wall_role") or "").strip()
        if role == "centerline":
            explicit_indexes.add(segment.index)
            thickness = segment.row.get("thickness_m")
            source = str(segment.row.get("thickness_source") or "").strip()
            try:
                thickness_value = float(thickness)
            except (TypeError, ValueError):
                thickness_value = 0.0
            if thickness_value <= 0 or not source:
                assemblies.append(_ambiguous_assembly(
                    f"{id_prefix}{len(assemblies) + 1}", segment,
                    "cad_centerline_thickness_unresolved",
                ))
            else:
                assemblies.append(_centerline_assembly(
                    f"{id_prefix}{len(assemblies) + 1}", segment,
                    thickness_value, source, wall_height_m, height_source,
                ))

    candidates: list[tuple[tuple[float, float, float, int, int], _Segment, _Segment, dict]] = []
    pairable = [segment for segment in open_segments if segment.index not in explicit_indexes]
    for first_index, first in enumerate(pairable):
        for second in pairable[first_index + 1:]:
            pair = _pair_candidate(first, second)
            if pair is None:
                continue
            score = (-float(pair["overlap_ratio"]),
                     float(pair["angle_difference_deg"]),
                     float(pair["separation_m"]), first.index, second.index)
            candidates.append((score, first, second, pair))
    candidates.sort(key=lambda item: item[0])
    occupied_intervals: dict[int, list[tuple[float, float]]] = {}
    paired: set[int] = set()
    for _, first, second, pair in candidates:
        first_interval = tuple(float(value) for value in pair["first_source_interval_m"])
        second_interval = tuple(float(value) for value in pair["second_source_interval_m"])

        def conflicts(segment_index: int, interval: tuple[float, float]) -> bool:
            return any(
                min(interval[1], existing[1]) - max(interval[0], existing[0]) > _EPSILON
                for existing in occupied_intervals.get(segment_index, [])
            )

        if conflicts(first.index, first_interval) or conflicts(second.index, second_interval):
            continue
        occupied_intervals.setdefault(first.index, []).append(first_interval)
        occupied_intervals.setdefault(second.index, []).append(second_interval)
        paired.update((first.index, second.index))
        assemblies.append(_paired_assembly(
            f"{id_prefix}{len(assemblies) + 1}", first, second, pair,
            wall_height_m, height_source,
        ))
    accepted_assemblies = [assembly for assembly in assemblies
                           if assembly.get("review_status") == "accepted"
                           and assembly.get("footprint_polygon")]
    for segment in pairable:
        if segment.index not in paired:
            redundancy = _redundant_evidence_match(segment, accepted_assemblies)
            if redundancy is not None:
                assemblies.append(_redundant_evidence_assembly(
                    f"{id_prefix}{len(assemblies) + 1}", segment,
                    redundancy["accepted"],
                    covered_length_m=float(redundancy["covered_length_m"]),
                    uncovered_length_m=float(redundancy["uncovered_length_m"]),
                    coverage_ratio=float(redundancy["coverage_ratio"]),
                    angle_difference_deg=float(redundancy["angle_difference_deg"]),
                    accepted_supports=redundancy.get("accepted_supports") or [],
                ))
            else:
                junction = _junction_evidence_match(segment, accepted_assemblies)
                if junction is not None:
                    assemblies.append(_junction_evidence_assembly(
                        f"{id_prefix}{len(assemblies) + 1}", segment, junction))
                else:
                    assemblies.append(_ambiguous_assembly(
                        f"{id_prefix}{len(assemblies) + 1}", segment,
                        "cad_wall_representation_unresolved",
                    ))
    return assemblies


def confirm_ambiguous_assembly(
    assembly: Mapping[str, Any],
    *,
    thickness_m: float,
    reviewer: str,
    reason: str,
    height_m: float = 2.8,
    height_source: str = "project_default_assumption",
) -> dict:
    """Resolve an ambiguous source line as a human-confirmed centreline wall."""
    if str(assembly.get("source_representation") or "") != "human_confirmed_ambiguous":
        raise WallAssemblyError("cad_wall_not_ambiguous", "只能确认待审查的模糊墙体")
    if not str(reviewer or "").strip() or not str(reason or "").strip():
        raise WallAssemblyError("cad_wall_review_audit_missing",
                                "人工确认必须记录 reviewer 和 reason")
    points = assembly.get("source_centerline") or []
    if len(points) != 2:
        raise WallAssemblyError("cad_wall_centerline_missing", "模糊墙体缺少源中心线")
    row = {"points": points, "cad_provenance": copy.deepcopy(
        assembly.get("cad_provenance") or {})}
    segment = _Segment(0, 0, 0, tuple(map(float, points[0])),
                       tuple(map(float, points[1])), row)
    result = _centerline_assembly(
        str(assembly.get("id") or "cad_wall_assembly_confirmed"), segment,
        float(thickness_m), "human_measurement", height_m, height_source,
        representation="human_confirmed_ambiguous",
        review={"reviewer": str(reviewer).strip(), "reason": str(reason).strip(),
                "decision": "confirmed_as_centerline"},
    )
    # Retain the original entity evidence rather than the reconstructed row.
    original_entities = copy.deepcopy(assembly.get("source_entities") or [])
    if original_entities:
        result.update(_evidence_fields(original_entities))
        result["cad_provenance"] = _compat_provenance(
            original_entities, "human_confirmed_ambiguous")
    result["confidence_grade"] = "B"
    return result


def _opening_axis(assembly: Mapping[str, Any]) -> tuple[tuple[float, float], tuple[float, float]]:
    axis = assembly.get("opening_axis") or assembly.get("centerline") or []
    if len(axis) != 2:
        raise WallAssemblyError(
            "cad_opening_axis_unresolved",
            "目标墙体没有唯一开口轴；必须先选择具体墙段",
            details={"wall_assembly_id": assembly.get("id")},
        )
    return (tuple(map(float, axis[0])), tuple(map(float, axis[1])))


def bind_manual_opening_annotations(
    assemblies: Sequence[Mapping[str, Any]],
    annotations: Sequence[Mapping[str, Any]],
    *,
    existing_openings: Sequence[Mapping[str, Any]] = (),
) -> list[dict]:
    """Validate and bind evidence-backed manual openings to wall assemblies.

    Intervals are measured in metres from the assembly's ``opening_axis`` start.
    Touching intervals are allowed; positive overlap is rejected.  The function
    never mutates the assemblies, annotations, or existing openings.
    """
    by_id = {str(row.get("id") or ""): row for row in assemblies if row.get("id")}
    occupied: dict[str, list[tuple[float, float, str]]] = {}
    for opening in existing_openings:
        wall_id = str(opening.get("wall_assembly_id") or opening.get("wall_id") or "")
        try:
            offset = float(opening.get("start_offset_m", opening.get("offset_m")))
            width = float(opening.get("width_m"))
        except (TypeError, ValueError):
            continue
        if width > 0:
            occupied.setdefault(wall_id, []).append(
                (offset, offset + width, str(opening.get("id") or "existing")))
    result: list[dict] = []
    for index, annotation in enumerate(annotations, 1):
        wall_id = str(annotation.get("wall_assembly_id") or "").strip()
        assembly = by_id.get(wall_id)
        if assembly is None:
            raise WallAssemblyError(
                "cad_opening_wall_not_found", "人工开口必须绑定存在的 wall assembly",
                details={"wall_assembly_id": wall_id},
            )
        if assembly.get("review_status") != "accepted" or not assembly.get("footprint_polygon"):
            raise WallAssemblyError(
                "cad_opening_wall_unresolved", "人工开口不能绑定尚未确认的墙体",
                details={"wall_assembly_id": wall_id},
            )
        first, second = _opening_axis(assembly)
        wall_length = math.dist(first, second)
        try:
            offset = float(annotation.get("start_offset_m", annotation.get("offset_m")))
            width = float(annotation.get("width_m"))
        except (TypeError, ValueError) as ex:
            raise WallAssemblyError("cad_opening_interval_invalid",
                                    "人工开口 offset 和 width 必须为数字") from ex
        if not all(math.isfinite(value) for value in (offset, width)) or width <= 0:
            raise WallAssemblyError("cad_opening_width_invalid", "人工开口宽度必须大于零")
        if offset < -_EPSILON or offset + width > wall_length + _EPSILON:
            raise WallAssemblyError(
                "cad_opening_interval_outside_wall", "人工开口区间超出目标墙体",
                details={"wall_assembly_id": wall_id, "offset_m": offset,
                         "width_m": width, "wall_length_m": wall_length},
            )
        kind = str(annotation.get("kind") or "").strip()
        if kind not in {"door", "window", "open_connection"}:
            raise WallAssemblyError("cad_opening_kind_invalid", "人工开口类型无效")
        interval = (max(0.0, offset), min(wall_length, offset + width))
        for existing_start, existing_end, existing_id in occupied.get(wall_id, []):
            if min(interval[1], existing_end) - max(interval[0], existing_start) > _EPSILON:
                raise WallAssemblyError(
                    "cad_opening_overlap", "同一墙体上的开口不能重叠",
                    details={"wall_assembly_id": wall_id,
                             "conflicting_opening_id": existing_id},
                )
        occupied.setdefault(wall_id, []).append(
            (interval[0], interval[1], str(annotation.get("id") or f"manual_{index}")))
        reviewer = str(annotation.get("reviewer") or "").strip()
        reason = str(annotation.get("reason") or "").strip()
        if not reviewer or not reason:
            raise WallAssemblyError("cad_opening_review_audit_missing",
                                    "人工开口必须记录 reviewer 和 reason")
        source_entities = copy.deepcopy(assembly.get("source_entities") or [])
        nearby_handles = sorted({
            str(entity.get("source_handle") or entity.get("root_handle") or "")
            for entity in source_entities
            if entity.get("source_handle") or entity.get("root_handle")
        })
        start_ratio = interval[0] / wall_length
        end_ratio = interval[1] / wall_length
        start_point = (first[0] + (second[0] - first[0]) * start_ratio,
                       first[1] + (second[1] - first[1]) * start_ratio)
        end_point = (first[0] + (second[0] - first[0]) * end_ratio,
                     first[1] + (second[1] - first[1]) * end_ratio)
        output = {
            "id": str(annotation.get("id") or f"cad_manual_opening_{index}"),
            "wall_assembly_id": wall_id,
            "wall_id": wall_id,
            "kind": kind,
            "start_offset_m": _round(interval[0]),
            "offset_m": _round(interval[0]),
            "width_m": _round(width),
            "height_m": _round(float(annotation.get("height_m")
                                     or (2.1 if kind in {"door", "open_connection"} else 1.2))),
            "sill_height_m": _round(float(annotation.get("sill_height_m")
                                          if annotation.get("sill_height_m") is not None
                                          else (0.0 if kind != "window" else 0.9))),
            "height_source": str(annotation.get("height_source")
                                 or "project_default_assumption"),
            "sill_height_source": str(annotation.get("sill_height_source")
                                      or "project_default_assumption"),
            "source_kind": "human_annotated_on_vector_source",
            "nearby_source_handles": nearby_handles,
            "evidence_geometry": {
                "opening_axis_segment_m": [_point_list(start_point), _point_list(end_point)],
                "wall_footprint_polygon": copy.deepcopy(assembly.get("footprint_polygon")),
            },
            "reviewer": reviewer,
            "reason": reason,
            "base_revision": annotation.get("base_revision"),
            "operation_id": annotation.get("operation_id"),
            "source": "cad_human_annotation",
            "review_status": "accepted",
            "cad_provenance": {
                "wall_assembly_id": wall_id,
                "source_entities": source_entities,
                "annotation_kind": "human_annotated_on_vector_source",
            },
        }
        result.append(output)
    return result


__all__ = [
    "ANGLE_TOLERANCE_DEG",
    "MIN_FACE_SEPARATION_M",
    "MAX_FACE_SEPARATION_M",
    "MIN_PROJECTED_OVERLAP",
    "NODE_SNAP_TOLERANCE_M",
    "WallAssemblyError",
    "decompose_cad_entity_roles",
    "bind_raw_geometry_openings",
    "summarize_raw_geometry_openings",
    "build_wall_assemblies",
    "stitch_wall_assemblies_across_openings",
    "confirm_ambiguous_assembly",
    "bind_manual_opening_annotations",
]
