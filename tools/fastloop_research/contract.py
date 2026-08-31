"""Strict ``research-structure-bundle-v1`` validation and mesh math.

All geometry fields ending in ``_m`` are metres in a right-handed, Z-up
coordinate system.  This module is deliberately pure Python so the product
backend, Blender, and IfcOpenShell process all consume the same validation and
cutting rules.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "research-structure-bundle-v1"
WALL_SCHEMA = "wall-branch-graph-v1"
OPENING_SCHEMA = "opening-contract-v1"
ADJACENCY_SCHEMA = "adjacency-truth-v1"
JUNCTION_CLEARANCE_M = 0.05
OPENING_ANGLE_TOLERANCE_DEGREES = 5.0
GEOMETRY_TOLERANCE_M = 0.001
SOURCE_SCHEMA = "source-provenance-v2"
SOURCE_KEYS = {
    "schema", "source_file_hash", "normalized_hash", "raw_pixel_hash",
    "exif_orientation", "orientation_policy", "canonical_visible_size",
    "coordinate_space", "normalized_to_metric_3x3", "anchor_set_hash", "scale_anchor_id", "anchors",
    "anchor_opening_bindings",
}
SOURCE_ANCHOR_KEYS = {"anchor_id", "kind", "points_norm", "points_metric_m", "distance_mm"}
SOURCE_BINDING_KEYS = {"anchor_id", "anchor_kind", "opening_id"}
TOP_LEVEL_KEYS = {
    "schema",
    "source",
    "project",
    "source_hash",
    "structure_hash",
    "outer_boundary_m",
    "spaces",
    "wall_branch_graph",
    "opening_contract",
    "adjacency_truth",
    "assumptions",
    "unresolved_issues",
}
WALL_KEYS = {
    "id",
    "centerline_m",
    "thickness_m",
    "base_m",
    "height_m",
    "left_space_id",
    "right_space_id",
    "source",
    "confirmed",
}
OPENING_KEYS = {
    "id",
    "kind",
    "owning_wall_id",
    "segment_m",
    "width_m",
    "sill_m",
    "head_m",
    "swing_direction",
    "side_a_space_id",
    "side_b_space_id",
    "jamb_before_supported",
    "jamb_after_supported",
    "jamb_before_support",
    "jamb_after_support",
    "junction_clearance_m",
    "junction_diagnostics",
    "confirmed",
    "source",
}
JUNCTION_KEYS = {"id", "point_m", "kind", "incident_wall_ids", "provenance"}
JAMB_SUPPORT_KEYS = {"mode", "supporting_wall_id", "junction_id", "face_distance_m", "effective_support_m", "provenance", "solid_provenance"}
EDGE_KEYS = {
    "id",
    "space_a_id",
    "space_b_id",
    "kind",
    "opening_id",
    "confirmed",
}
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SWINGS = {"hinge_left", "hinge_right", "sliding", "double", "not_shown"}


class ResearchModelError(ValueError):
    """Raised before any output write when a structure contract is unsafe."""


def _fail(message: str) -> None:
    raise ResearchModelError(message)


def _exact_keys(value: Any, expected: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{path}: expected an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        _fail(f"{path}: exact keys required; missing={missing}, extra={extra}")
    return value


def _finite(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{path}: expected a finite number")
    result = float(value)
    if not math.isfinite(result):
        _fail(f"{path}: expected a finite number")
    return result


def _bounded(value: Any, path: str, lower: float, upper: float) -> float:
    result = _finite(value, path)
    if not lower <= result <= upper:
        _fail(f"{path}: expected {lower} <= value <= {upper}")
    return result


def _point(value: Any, path: str) -> tuple[float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 2
    ):
        _fail(f"{path}: expected [x, y]")
    return _finite(value[0], f"{path}[0]"), _finite(value[1], f"{path}[1]")


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        _fail(f"{path}: expected a stable ID matching {ID_RE.pattern}")
    return value


def _nonempty_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(f"{path}: expected a non-empty trimmed string")
    if any(ord(character) < 32 for character in value):
        _fail(f"{path}: control characters are not allowed")
    return value


def _json_metadata(value: Any, path: str) -> None:
    if not isinstance(value, (str, Mapping)):
        _fail(f"{path}: expected a non-empty string or JSON object")
    if isinstance(value, str):
        _nonempty_text(value, path)
    elif not value:
        _fail(f"{path}: metadata object cannot be empty")
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        _fail(f"{path}: not canonical JSON metadata: {exc}")


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        _fail(f"{path}: expected lowercase sha256")
    return value


def _signed_area(points: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(
        a[0] * b[1] - b[0] * a[1]
        for a, b in zip(points, points[1:] + points[:1])
    )


def _orientation(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    epsilon: float = 1.0e-9,
) -> int:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return 1 if cross > epsilon else -1 if cross < -epsilon else 0


def _on_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    point: tuple[float, float],
    epsilon: float = 1.0e-9,
) -> bool:
    return (
        min(a[0], b[0]) - epsilon <= point[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= point[1] <= max(a[1], b[1]) + epsilon
        and _orientation(a, b, point, epsilon) == 0
    )


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _on_segment(a, b, c))
        or (o2 == 0 and _on_segment(a, b, d))
        or (o3 == 0 and _on_segment(c, d, a))
        or (o4 == 0 and _on_segment(c, d, b))
    )


def _proper_intersection_parameter(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> float | None:
    """Return distance along AB for one point intersection, else ``None``."""

    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    denominator = rx * sy - ry * sx
    if abs(denominator) <= 1.0e-9:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / denominator
    u = (qx * ry - qy * rx) / denominator
    if -1.0e-9 <= t <= 1.0 + 1.0e-9 and -1.0e-9 <= u <= 1.0 + 1.0e-9:
        return t * math.hypot(rx, ry)
    return None


def _distance_point_to_segment(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1.0e-18:
        return math.dist(point, start)
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def _segment_hausdorff(first, second) -> tuple[float, float, float]:
    first_to_second = max(_distance_point_to_segment(point, *second) for point in first)
    second_to_first = max(_distance_point_to_segment(point, *first) for point in second)
    return first_to_second, second_to_first, max(first_to_second, second_to_first)


def _segment_angle_degrees(first, second) -> float:
    first_vector = (first[1][0] - first[0][0], first[1][1] - first[0][1])
    second_vector = (second[1][0] - second[0][0], second[1][1] - second[0][1])
    denominator = math.hypot(*first_vector) * math.hypot(*second_vector)
    if denominator <= 1.0e-18:
        return 180.0
    cosine = max(-1.0, min(1.0, abs((first_vector[0] * second_vector[0] + first_vector[1] * second_vector[1]) / denominator)))
    return math.degrees(math.acos(cosine))


def _endpoint_match_error(first, second) -> float:
    direct = max(math.dist(first[0], second[0]), math.dist(first[1], second[1]))
    reverse = max(math.dist(first[0], second[1]), math.dist(first[1], second[0]))
    return min(direct, reverse)


def analyze_wall_junctions(
    walls: Sequence[Mapping[str, Any]], *, tolerance_m: float = GEOMETRY_TOLERANCE_M
) -> list[dict[str, Any]]:
    """Classify L/T/X/collinear relations; unsplit T/X and overlaps fail."""

    diagnostics: list[dict[str, Any]] = []
    for first_index, first in enumerate(walls):
        first_a, first_b = (tuple(map(float, point)) for point in first["centerline_m"])
        first_length = math.dist(first_a, first_b)
        for second in walls[first_index + 1 :]:
            second_a, second_b = (tuple(map(float, point)) for point in second["centerline_m"])
            second_length = math.dist(second_a, second_b)
            collinear = _orientation(first_a, first_b, second_a) == 0 and _orientation(first_a, first_b, second_b) == 0
            if collinear:
                dx, dy = first_b[0] - first_a[0], first_b[1] - first_a[1]
                tx, ty = dx / first_length, dy / first_length
                second_interval = sorted(((second_a[0] - first_a[0]) * tx + (second_a[1] - first_a[1]) * ty, (second_b[0] - first_a[0]) * tx + (second_b[1] - first_a[1]) * ty))
                overlap = min(first_length, second_interval[1]) - max(0.0, second_interval[0])
                if overlap > tolerance_m:
                    diagnostics.append({"kind": "collinear_overlap", "severity": "error", "wall_ids": [first["id"], second["id"]], "overlap_m": round(overlap, 9)})
                continue
            first_parameter = _proper_intersection_parameter(first_a, first_b, second_a, second_b)
            second_parameter = _proper_intersection_parameter(second_a, second_b, first_a, first_b)
            if first_parameter is None or second_parameter is None:
                endpoint_gap = min(math.dist(a, b) for a in (first_a, first_b) for b in (second_a, second_b))
                if tolerance_m < endpoint_gap <= JUNCTION_CLEARANCE_M:
                    diagnostics.append({"kind": "near_miss_gap", "severity": "error", "wall_ids": [first["id"], second["id"]], "gap_m": round(endpoint_gap, 9)})
                continue
            first_endpoint = min(first_parameter, first_length - first_parameter) <= tolerance_m
            second_endpoint = min(second_parameter, second_length - second_parameter) <= tolerance_m
            if first_endpoint and second_endpoint:
                diagnostics.append({"kind": "l_junction", "severity": "pass", "wall_ids": [first["id"], second["id"]]})
            elif first_endpoint != second_endpoint:
                diagnostics.append({"kind": "t_junction", "severity": "pass", "wall_ids": [first["id"], second["id"]]})
            else:
                diagnostics.append({"kind": "unsplit_x_junction", "severity": "error", "wall_ids": [first["id"], second["id"]]})
    return diagnostics


def derive_wall_junctions(walls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Derive unique geometric junctions, grouped at 1mm coordinates."""

    points: dict[tuple[int, int], dict[str, Any]] = {}
    for first_index, first in enumerate(walls):
        a, b = (tuple(map(float, point)) for point in first["centerline_m"])
        first_length = math.dist(a, b)
        for second in walls[first_index + 1 :]:
            c, d = (tuple(map(float, point)) for point in second["centerline_m"])
            if _orientation(a, b, c) == 0 and _orientation(a, b, d) == 0:
                shared = [point for point in (a, b) if min(math.dist(point, c), math.dist(point, d)) <= GEOMETRY_TOLERANCE_M]
                if not shared:
                    continue
                point = shared[0]
            else:
                first_parameter = _proper_intersection_parameter(a, b, c, d)
                if first_parameter is None:
                    continue
                ratio = first_parameter / first_length
                point = (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)
            key = (round(point[0] / GEOMETRY_TOLERANCE_M), round(point[1] / GEOMETRY_TOLERANCE_M))
            row = points.setdefault(key, {"point_m": [point[0], point[1]], "incident_wall_ids": set()})
            row["incident_wall_ids"].update((first["id"], second["id"]))
    result: list[dict[str, Any]] = []
    for index, row in enumerate(sorted(points.values(), key=lambda item: (item["point_m"][0], item["point_m"][1])), 1):
        incident = sorted(row["incident_wall_ids"])
        interiors = 0
        for wall in walls:
            if wall["id"] not in incident:
                continue
            a, b = (tuple(map(float, point)) for point in wall["centerline_m"])
            if min(math.dist(tuple(row["point_m"]), a), math.dist(tuple(row["point_m"]), b)) > GEOMETRY_TOLERANCE_M:
                interiors += 1
        if interiors >= 2 or (interiors == 0 and len(incident) >= 4):
            kind = "X"
        elif len(incident) >= 3 or interiors == 1:
            kind = "T"
        else:
            first = next(wall for wall in walls if wall["id"] == incident[0])
            second = next(wall for wall in walls if wall["id"] == incident[1])
            first_a, first_b = tuple(first["centerline_m"][0]), tuple(first["centerline_m"][1])
            second_a, second_b = tuple(second["centerline_m"][0]), tuple(second["centerline_m"][1])
            kind = "continuation" if _orientation(first_a, first_b, second_a) == 0 and _orientation(first_a, first_b, second_b) == 0 else "L"
        result.append({"id": f"J-{index:03d}", "point_m": [round(value, 6) for value in row["point_m"]], "kind": kind, "incident_wall_ids": incident, "provenance": "derived_geometry"})
    return result


def _polygon(value: Any, path: str) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        _fail(f"{path}: expected an array")
    points = [_point(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if points and points[0] == points[-1]:
        _fail(f"{path}: polygon is implicitly closed; do not repeat the first point")
    if len(points) < 4 or len(set(points)) != len(points):
        _fail(f"{path}: expected at least four distinct vertices")
    if abs(_signed_area(points)) <= 1.0e-6:
        _fail(f"{path}: polygon has zero area")
    edges = [(points[index], points[(index + 1) % len(points)]) for index in range(len(points))]
    for first in range(len(edges)):
        for second in range(first + 1, len(edges)):
            if second == first + 1 or (first == 0 and second == len(edges) - 1):
                continue
            if _segments_intersect(*edges[first], *edges[second]):
                _fail(f"{path}: self-intersection between edges {first} and {second}")
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    width, depth = max(xs) - min(xs), max(ys) - min(ys)
    if min(width, depth) < 0.5 or max(width, depth) > 1000.0:
        _fail(f"{path}: implausible metre scale ({width:.3f}m x {depth:.3f}m)")
    return points


def _point_inside(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _on_segment(previous, current, point):
            return False
        if (current[1] > point[1]) != (previous[1] > point[1]):
            intersection_x = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < intersection_x:
                inside = not inside
        previous = current
    return inside


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(f"bundle is not canonical JSON: {exc}")


def compute_structure_hash(bundle: Mapping[str, Any]) -> str:
    """Hash the complete source-bound contract except its self-hash field."""

    if not isinstance(bundle, Mapping):
        _fail("bundle: expected an object")
    payload = deepcopy(dict(bundle))
    payload.pop("structure_hash", None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def canonical_anchor_geometry_payload(
    coordinate_space: str,
    source_hash: str,
    normalized_hash: str,
    anchors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the label/note-independent human anchor geometry authority."""

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(anchors):
        anchor_id = _identifier(raw.get("anchor_id"), f"anchors[{index}].anchor_id")
        if anchor_id in seen:
            _fail(f"anchors[{index}].anchor_id: duplicate ID")
        seen.add(anchor_id)
        kind = raw.get("kind")
        if not isinstance(kind, str):
            _fail(f"anchors[{index}].kind: expected string")
        raw_points = raw.get("points_norm", raw.get("points"))
        if not isinstance(raw_points, list):
            _fail(f"anchors[{index}]: missing canonical points")
        points = []
        for point_index, raw_point in enumerate(raw_points):
            if isinstance(raw_point, Mapping):
                point = [_finite(raw_point.get("x"), f"anchors[{index}].points[{point_index}].x"), _finite(raw_point.get("y"), f"anchors[{index}].points[{point_index}].y")]
            else:
                parsed = _point(raw_point, f"anchors[{index}].points[{point_index}]")
                point = [parsed[0], parsed[1]]
            points.append([int(value) if float(value).is_integer() else float(value) for value in point])
        distance = raw.get("distance_mm")
        distance_value = None if distance is None else _finite(distance, f"anchors[{index}].distance_mm")
        if distance_value is not None and distance_value.is_integer():
            distance_value = int(distance_value)
        records.append({"anchor_id": anchor_id, "kind": kind, "points_norm": points, "distance_mm": distance_value})
    records.sort(key=lambda item: item["anchor_id"])
    return {"coordinate_space": coordinate_space, "source_hash": source_hash, "normalized_hash": normalized_hash, "anchors": records}


def compute_anchor_set_hash(
    coordinate_space: str,
    source_hash: str,
    normalized_hash: str,
    anchors: Sequence[Mapping[str, Any]],
) -> str:
    return hashlib.sha256(canonical_json(canonical_anchor_geometry_payload(coordinate_space, source_hash, normalized_hash, anchors))).hexdigest()


def project_opening(wall: Mapping[str, Any], opening: Mapping[str, Any]) -> dict[str, float]:
    a = tuple(map(float, wall["centerline_m"][0]))
    b = tuple(map(float, wall["centerline_m"][1]))
    start = tuple(map(float, opening["segment_m"][0]))
    end = tuple(map(float, opening["segment_m"][1]))
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    tx, ty = dx / length, dy / length

    def one(point: tuple[float, float]) -> tuple[float, float]:
        px, py = point[0] - a[0], point[1] - a[1]
        projection = px * tx + py * ty
        distance = abs(px * (-ty) + py * tx)
        return projection, distance

    p0, d0 = one(start)
    p1, d1 = one(end)
    projected_segment = ((a[0] + tx * p0, a[1] + ty * p0), (a[0] + tx * p1, a[1] + ty * p1))
    opening_segment = (start, end)
    first_to_second, second_to_first, hausdorff = _segment_hausdorff(opening_segment, projected_segment)
    return {
        "wall_length_m": length,
        "start_m": min(p0, p1),
        "end_m": max(p0, p1),
        "endpoint_0_distance_m": d0,
        "endpoint_1_distance_m": d1,
        "projected_width_m": abs(p1 - p0),
        "opening_to_projection_hausdorff_m": first_to_second,
        "projection_to_opening_hausdorff_m": second_to_first,
        "symmetric_hausdorff_m": hausdorff,
        "endpoint_match_error_m": _endpoint_match_error(opening_segment, projected_segment),
        "angle_error_degrees": _segment_angle_degrees(opening_segment, (a, b)),
    }


def _validate_spaces(
    raw_spaces: Any,
    outer: Sequence[tuple[float, float]],
) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(raw_spaces, list) or not raw_spaces:
        _fail("spaces: expected a non-empty array")
    spaces: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_spaces):
        path = f"spaces[{index}]"
        record = _exact_keys(raw, {"id", "label", "point_m"}, path)
        stable_id = _identifier(record["id"], f"{path}.id")
        if stable_id == "exterior" or stable_id in ids:
            _fail(f"{path}.id: duplicate or reserved ID {stable_id!r}")
        point = _point(record["point_m"], f"{path}.point_m")
        if not _point_inside(point, outer):
            _fail(f"{path}.point_m: room point must be strictly inside outer_boundary_m")
        _nonempty_text(record["label"], f"{path}.label")
        ids.add(stable_id)
        spaces.append(deepcopy(dict(record)))
    return spaces, ids


def _validate_walls(raw_graph: Any, space_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    graph = _exact_keys(raw_graph, {"version", "walls", "junctions"}, "wall_branch_graph")
    if graph["version"] != WALL_SCHEMA:
        _fail(f"wall_branch_graph.version: expected {WALL_SCHEMA}")
    if not isinstance(graph["walls"], list) or not graph["walls"]:
        _fail("wall_branch_graph.walls: expected a non-empty array")
    allowed_spaces = space_ids | {"exterior"}
    walls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(graph["walls"]):
        path = f"wall_branch_graph.walls[{index}]"
        record = _exact_keys(raw, WALL_KEYS, path)
        stable_id = _identifier(record["id"], f"{path}.id")
        if stable_id in by_id:
            _fail(f"{path}.id: duplicate ID {stable_id!r}")
        centerline = record["centerline_m"]
        if not isinstance(centerline, list) or len(centerline) != 2:
            _fail(f"{path}.centerline_m: expected two points")
        a = _point(centerline[0], f"{path}.centerline_m[0]")
        b = _point(centerline[1], f"{path}.centerline_m[1]")
        length = math.dist(a, b)
        if not 0.10 <= length <= 1000.0:
            _fail(f"{path}.centerline_m: wall length must be 0.10..1000m")
        _bounded(record["thickness_m"], f"{path}.thickness_m", 0.03, 1.0)
        base = _bounded(record["base_m"], f"{path}.base_m", 0.0, 20.0)
        height = _bounded(record["height_m"], f"{path}.height_m", 0.10, 50.0)
        left, right = record["left_space_id"], record["right_space_id"]
        if left not in allowed_spaces or right not in allowed_spaces or left == right:
            _fail(f"{path}: left/right spaces must be distinct known IDs")
        _nonempty_text(record["source"], f"{path}.source")
        if record["confirmed"] is not True:
            _fail(f"{path}.confirmed: only confirmed walls are buildable")
        normalized = deepcopy(dict(record))
        walls.append(normalized)
        by_id[stable_id] = normalized
    if not isinstance(graph["junctions"], list):
        _fail("wall_branch_graph.junctions: expected an array")
    detected = derive_wall_junctions(walls)
    for detected_junction in detected:
        if detected_junction["kind"] not in {"T", "X"}:
            continue
        point = tuple(detected_junction["point_m"])
        unsplit = [
            wall_id for wall_id in detected_junction["incident_wall_ids"]
            if min(math.dist(point, tuple(by_id[wall_id]["centerline_m"][0])), math.dist(point, tuple(by_id[wall_id]["centerline_m"][1]))) > GEOMETRY_TOLERANCE_M
        ]
        if unsplit:
            _fail(f"wall_branch_graph: non-atomic {detected_junction['kind']} junction; split walls at intersection {unsplit}")
    if len(graph["junctions"]) != len(detected):
        _fail("wall_branch_graph.junctions: every actual intersection requires exactly one declaration")
    junction_by_id: dict[str, dict[str, Any]] = {}
    used_detected: set[int] = set()
    for index, raw in enumerate(graph["junctions"]):
        path = f"wall_branch_graph.junctions[{index}]"
        record = _exact_keys(raw, JUNCTION_KEYS, path)
        junction_id = _identifier(record["id"], f"{path}.id")
        if junction_id in junction_by_id:
            _fail(f"{path}.id: duplicate junction ID")
        point = _point(record["point_m"], f"{path}.point_m")
        incident = record["incident_wall_ids"]
        if not isinstance(incident, list) or len(incident) < 2 or len(incident) != len(set(incident)) or any(item not in by_id for item in incident):
            _fail(f"{path}.incident_wall_ids: expected unique known walls")
        if record["kind"] not in {"L", "T", "X", "return", "continuation"}:
            _fail(f"{path}.kind: expected L|T|X|return|continuation")
        _nonempty_text(record["provenance"], f"{path}.provenance")
        candidates = [
            (detected_index, item) for detected_index, item in enumerate(detected)
            if detected_index not in used_detected
            and math.dist(point, tuple(item["point_m"])) <= GEOMETRY_TOLERANCE_M + 1.0e-9
            and set(incident) == set(item["incident_wall_ids"])
            and (record["kind"] == item["kind"] or (record["kind"] == "return" and item["kind"] == "L"))
        ]
        if len(candidates) != 1:
            _fail(f"{path}: ghost, duplicate, or mismatched junction declaration")
        used_detected.add(candidates[0][0])
        junction_by_id[junction_id] = deepcopy(dict(record))
    for wall in walls:
        for endpoint in wall["centerline_m"]:
            if not any(wall["id"] in item["incident_wall_ids"] and math.dist(tuple(endpoint), tuple(item["point_m"])) <= GEOMETRY_TOLERANCE_M + 1.0e-9 for item in graph["junctions"]):
                _fail(f"wall_branch_graph: dangling endpoint on {wall['id']}")
    return walls, by_id, junction_by_id


def _validate_openings(
    raw_contract: Any,
    walls: list[dict[str, Any]],
    wall_by_id: dict[str, dict[str, Any]],
    junction_by_id: dict[str, dict[str, Any]],
    space_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, float]]]:
    contract = _exact_keys(
        raw_contract,
        {"version", "junction_clearance_m", "openings"},
        "opening_contract",
    )
    if contract["version"] != OPENING_SCHEMA:
        _fail(f"opening_contract.version: expected {OPENING_SCHEMA}")
    clearance = _finite(contract["junction_clearance_m"], "opening_contract.junction_clearance_m")
    if not math.isclose(clearance, JUNCTION_CLEARANCE_M, abs_tol=1.0e-9):
        _fail("opening_contract.junction_clearance_m: must be exactly 0.05m")
    if not isinstance(contract["openings"], list):
        _fail("opening_contract.openings: expected an array")
    allowed_spaces = space_ids | {"exterior"}
    openings: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    projections: dict[str, dict[str, float]] = {}
    for index, raw in enumerate(contract["openings"]):
        path = f"opening_contract.openings[{index}]"
        record = _exact_keys(raw, OPENING_KEYS, path)
        stable_id = _identifier(record["id"], f"{path}.id")
        if stable_id in by_id:
            _fail(f"{path}.id: duplicate ID {stable_id!r}")
        kind = record["kind"]
        if kind not in {"entrance", "door", "window"}:
            _fail(f"{path}.kind: expected entrance|door|window")
        wall_id = record["owning_wall_id"]
        if wall_id not in wall_by_id:
            _fail(f"{path}.owning_wall_id: unknown wall {wall_id!r}")
        segment = record["segment_m"]
        if not isinstance(segment, list) or len(segment) != 2:
            _fail(f"{path}.segment_m: expected two points")
        a = _point(segment[0], f"{path}.segment_m[0]")
        b = _point(segment[1], f"{path}.segment_m[1]")
        actual_width = math.dist(a, b)
        width = _bounded(record["width_m"], f"{path}.width_m", 0.10, 20.0)
        if abs(actual_width - width) > JUNCTION_CLEARANCE_M + 1.0e-9:
            _fail(f"{path}.width_m: differs from segment length by more than 50mm")
        wall = wall_by_id[wall_id]
        projection = project_opening(wall, record)
        if projection["symmetric_hausdorff_m"] > JUNCTION_CLEARANCE_M + 1.0e-9:
            _fail(f"{path}.segment_m: symmetric Hausdorff distance from owning wall exceeds 50mm")
        if projection["endpoint_match_error_m"] > JUNCTION_CLEARANCE_M + 1.0e-9:
            _fail(f"{path}.segment_m: projected endpoint correspondence exceeds 50mm")
        if projection["angle_error_degrees"] > OPENING_ANGLE_TOLERANCE_DEGREES + 1.0e-9:
            _fail(f"{path}.segment_m: direction differs from owning wall by more than 5 degrees")
        if abs(projection["projected_width_m"] - width) > JUNCTION_CLEARANCE_M + 1.0e-9:
            _fail(f"{path}.width_m: projected wall width differs by more than 50mm")
        if projection["start_m"] < -JUNCTION_CLEARANCE_M - 1.0e-9 or projection["end_m"] > projection["wall_length_m"] + JUNCTION_CLEARANCE_M + 1.0e-9:
            _fail(f"{path}: opening projects more than 50mm outside owning wall")
        sill = _finite(record["sill_m"], f"{path}.sill_m")
        head = _finite(record["head_m"], f"{path}.head_m")
        base = float(wall["base_m"])
        top = base + float(wall["height_m"])
        if kind in {"entrance", "door"}:
            if not math.isclose(sill, 0.0, abs_tol=1.0e-9) or not math.isclose(base, 0.0, abs_tol=1.0e-9):
                _fail(f"{path}: doors/entrances require sill_m=0 and wall base_m=0")
            if record["swing_direction"] not in SWINGS:
                _fail(f"{path}.swing_direction: explicit door swing is required")
        elif record["swing_direction"] is not None:
            _fail(f"{path}.swing_direction: windows require null")
        if not base <= sill < head <= top:
            _fail(f"{path}: require wall.base <= sill < head <= wall.height")
        side_a, side_b = record["side_a_space_id"], record["side_b_space_id"]
        if side_a not in allowed_spaces or side_b not in allowed_spaces or side_a == side_b:
            _fail(f"{path}: side spaces must be distinct known IDs")
        if {side_a, side_b} != {wall["left_space_id"], wall["right_space_id"]}:
            _fail(f"{path}: opening side spaces must match owning wall sides")
        if record["jamb_before_supported"] is not True or record["jamb_after_supported"] is not True:
            _fail(f"{path}: both jamb support flags must be true")
        wall_a, wall_b = (tuple(map(float, point)) for point in wall["centerline_m"])
        wall_dx, wall_dy = wall_b[0] - wall_a[0], wall_b[1] - wall_a[1]
        wall_length = math.hypot(wall_dx, wall_dy)
        tangent = (wall_dx / wall_length, wall_dy / wall_length)
        jamb_specs = (
            ("jamb_before_support", projection["start_m"], projection["start_m"]),
            ("jamb_after_support", wall_length - projection["end_m"], projection["end_m"]),
        )
        for support_key, same_wall_margin, jamb_parameter in jamb_specs:
            support_path = f"{path}.{support_key}"
            support = _exact_keys(record[support_key], JAMB_SUPPORT_KEYS, support_path)
            mode = support["mode"]
            support_wall_id = support["supporting_wall_id"]
            effective = _finite(support["effective_support_m"], f"{support_path}.effective_support_m")
            _nonempty_text(support["provenance"], f"{support_path}.provenance")
            _nonempty_text(support["solid_provenance"], f"{support_path}.solid_provenance")
            if mode == "same_wall_margin":
                if support_wall_id != wall_id or support["junction_id"] is not None or same_wall_margin < JUNCTION_CLEARANCE_M - 1.0e-9 or effective < JUNCTION_CLEARANCE_M - 1.0e-9:
                    _fail(f"{support_path}: same-wall support requires at least 50mm owner solid")
                if abs(_finite(support["face_distance_m"], f"{support_path}.face_distance_m") - same_wall_margin) > GEOMETRY_TOLERANCE_M + 1.0e-9:
                    _fail(f"{support_path}: declared same-wall margin differs by more than 1mm")
            elif mode == "return_wall_face":
                if support_wall_id not in wall_by_id or support_wall_id == wall_id:
                    _fail(f"{support_path}: return support requires another known wall")
                junction_id = support["junction_id"]
                junction = junction_by_id.get(junction_id)
                if not junction or junction["kind"] != "return" or {wall_id, support_wall_id} - set(junction["incident_wall_ids"]):
                    _fail(f"{support_path}: return support requires an explicit return junction")
                support_wall = wall_by_id[support_wall_id]
                if float(support_wall["base_m"]) > sill + GEOMETRY_TOLERANCE_M or float(support_wall["base_m"]) + float(support_wall["height_m"]) < head - GEOMETRY_TOLERANCE_M:
                    _fail(f"{support_path}: support wall solid does not cover opening height")
                jamb_point = (wall_a[0] + tangent[0] * jamb_parameter, wall_a[1] + tangent[1] * jamb_parameter)
                support_a, support_b = (tuple(map(float, point)) for point in support_wall["centerline_m"])
                support_dx, support_dy = support_b[0] - support_a[0], support_b[1] - support_a[1]
                perpendicular_error = abs(90.0 - math.degrees(math.acos(max(-1.0, min(1.0, abs((wall_dx * support_dx + wall_dy * support_dy) / max(wall_length * math.hypot(support_dx, support_dy), 1.0e-12)))))))
                if perpendicular_error > 10.0 + 1.0e-9:
                    _fail(f"{support_path}: return support wall is not perpendicular within 10 degrees")
                actual_face_distance = max(0.0, _distance_point_to_segment(jamb_point, support_a, support_b) - float(support_wall["thickness_m"]) * 0.5)
                if actual_face_distance > GEOMETRY_TOLERANCE_M + 1.0e-9 or abs(_finite(support["face_distance_m"], f"{support_path}.face_distance_m") - actual_face_distance) > GEOMETRY_TOLERANCE_M + 1.0e-9:
                    _fail(f"{support_path}: return wall face is not continuous within 1mm")
                support_length = math.hypot(support_dx, support_dy)
                support_normal = (-support_dy / support_length, support_dx / support_length)
                half_support = float(support_wall["thickness_m"]) * 0.5
                footprint = [
                    (point[0] + sign * support_normal[0] * half_support, point[1] + sign * support_normal[1] * half_support)
                    for point in (support_a, support_b) for sign in (-1.0, 1.0)
                ]
                support_interval = sorted((point[0] - wall_a[0]) * tangent[0] + (point[1] - wall_a[1]) * tangent[1] for point in footprint)
                if support_key == "jamb_before_support":
                    union_contiguous = support_interval[-1] >= -GEOMETRY_TOLERANCE_M
                    actual_effective = jamb_parameter - min(0.0, support_interval[0])
                else:
                    union_contiguous = support_interval[0] <= wall_length + GEOMETRY_TOLERANCE_M
                    actual_effective = max(wall_length, support_interval[1]) - jamb_parameter
                if not union_contiguous or actual_effective < JUNCTION_CLEARANCE_M - 1.0e-9:
                    _fail(f"{support_path}: independently derived wall-solid union support is below 50mm (actual={actual_effective:.6f}m, interval={support_interval})")
                if abs(effective - actual_effective) > GEOMETRY_TOLERANCE_M + 1.0e-9:
                    _fail(f"{support_path}: declared effective support differs from wall-solid union by more than 1mm")
            else:
                _fail(f"{support_path}.mode: expected same_wall_margin|return_wall_face")
        item_clearance = _finite(record["junction_clearance_m"], f"{path}.junction_clearance_m")
        if not math.isclose(item_clearance, JUNCTION_CLEARANCE_M, abs_tol=1.0e-9):
            _fail(f"{path}.junction_clearance_m: must be exactly 0.05m")
        if record["junction_diagnostics"] != []:
            _fail(f"{path}.junction_diagnostics: must be empty before building")
        if record["confirmed"] is not True:
            _fail(f"{path}.confirmed: only confirmed openings are buildable")
        _nonempty_text(record["source"], f"{path}.source")

        protected_start = projection["start_m"] - JUNCTION_CLEARANCE_M
        protected_end = projection["end_m"] + JUNCTION_CLEARANCE_M
        owning_a = tuple(map(float, wall["centerline_m"][0]))
        owning_b = tuple(map(float, wall["centerline_m"][1]))
        return_support_wall_ids = {
            record[key]["supporting_wall_id"]
            for key in ("jamb_before_support", "jamb_after_support")
            if record[key]["mode"] == "return_wall_face"
        }
        for other in walls:
            if other["id"] == wall_id:
                continue
            other_a = tuple(map(float, other["centerline_m"][0]))
            other_b = tuple(map(float, other["centerline_m"][1]))
            intersection = _proper_intersection_parameter(owning_a, owning_b, other_a, other_b)
            if intersection is not None and protected_start - 1.0e-9 <= intersection <= protected_end + 1.0e-9 and other["id"] not in return_support_wall_ids:
                _fail(f"{path}: protected jamb interval intersects wall junction {other['id']}")
            if (
                intersection is None
                and _segments_intersect(owning_a, owning_b, other_a, other_b)
                and _orientation(owning_a, owning_b, other_a) == 0
                and _orientation(owning_a, owning_b, other_b) == 0
            ):
                dx = owning_b[0] - owning_a[0]
                dy = owning_b[1] - owning_a[1]
                owning_length = math.hypot(dx, dy)
                tx, ty = dx / owning_length, dy / owning_length
                other_support = sorted(
                    (
                        (other_a[0] - owning_a[0]) * tx + (other_a[1] - owning_a[1]) * ty,
                        (other_b[0] - owning_a[0]) * tx + (other_b[1] - owning_a[1]) * ty,
                    )
                )
                overlap_start = max(0.0, other_support[0], protected_start)
                overlap_end = min(owning_length, other_support[1], protected_end)
                if overlap_end >= overlap_start - 1.0e-9:
                    _fail(f"{path}: protected jamb interval overlaps collinear wall {other['id']}")
        normalized = deepcopy(dict(record))
        openings.append(normalized)
        by_id[stable_id] = normalized
        projections[stable_id] = projection

    for opening in openings:
        for support_key in ("jamb_before_support", "jamb_after_support"):
            support = opening[support_key]
            if support["mode"] != "return_wall_face":
                continue
            junction_point = tuple(junction_by_id[support["junction_id"]]["point_m"])
            for other in openings:
                if other["id"] == opening["id"] or other["owning_wall_id"] != support["supporting_wall_id"]:
                    continue
                other_segment = tuple(tuple(map(float, point)) for point in other["segment_m"])
                if _distance_point_to_segment(junction_point, *other_segment) <= float(support["effective_support_m"]) + JUNCTION_CLEARANCE_M:
                    _fail(f"opening_contract: {other['id']} cuts return-wall support for {opening['id']}")

    by_wall: dict[str, list[tuple[float, float, str]]] = {}
    for opening in openings:
        projection = projections[opening["id"]]
        by_wall.setdefault(opening["owning_wall_id"], []).append(
            (projection["start_m"], projection["end_m"], opening["id"])
        )
    for wall_id, intervals in by_wall.items():
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] - previous[1] < 2.0 * JUNCTION_CLEARANCE_M - 1.0e-9:
                _fail(
                    f"opening_contract: openings {previous[2]} and {current[2]} on {wall_id} "
                    "need independent 50mm jamb protection"
                )
    return openings, by_id, projections


def _validate_source(raw_source, *, source_hash: str, opening_by_id) -> dict[str, Any]:
    source = _exact_keys(raw_source, SOURCE_KEYS, "bundle.source")
    if source["schema"] != SOURCE_SCHEMA:
        _fail(f"bundle.source.schema: expected {SOURCE_SCHEMA}")
    if _hash(source["source_file_hash"], "bundle.source.source_file_hash") != source_hash:
        _fail("bundle.source.source_file_hash: must equal bundle.source_hash")
    _hash(source["normalized_hash"], "bundle.source.normalized_hash")
    _hash(source["raw_pixel_hash"], "bundle.source.raw_pixel_hash")
    orientation = source["exif_orientation"]
    if isinstance(orientation, bool) or not isinstance(orientation, int) or not 1 <= orientation <= 8:
        _fail("bundle.source.exif_orientation: expected integer 1..8")
    if source["orientation_policy"] not in {"exif_transpose-v1", "ignore_invalid_exif_user_confirmed_raw"}:
        _fail("bundle.source.orientation_policy: unsupported canonical-visible policy")
    size = source["canonical_visible_size"]
    if not isinstance(size, list) or len(size) != 2 or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in size):
        _fail("bundle.source.canonical_visible_size: expected [positive width, positive height]")
    if source["coordinate_space"] not in {"normalized-evidence-1000-v1", "raw-full-canvas-normalized-1000-v1"}:
        _fail("bundle.source.coordinate_space: unsupported canonical normalized-1000 space")
    matrix = source["normalized_to_metric_3x3"]
    if not isinstance(matrix, list) or len(matrix) != 3 or any(not isinstance(row, list) or len(row) != 3 for row in matrix):
        _fail("bundle.source.normalized_to_metric_3x3: expected 3x3 affine matrix")
    numeric_matrix = [[_finite(value, f"bundle.source.normalized_to_metric_3x3[{r}][{c}]") for c, value in enumerate(row)] for r, row in enumerate(matrix)]
    if any(abs(value) > 1.0e-12 for value in numeric_matrix[2][:2]) or not math.isclose(numeric_matrix[2][2], 1.0, abs_tol=1.0e-12):
        _fail("bundle.source.normalized_to_metric_3x3: last row must be [0,0,1]")

    def metric_from_norm(point: tuple[float, float]) -> tuple[float, float]:
        return (
            numeric_matrix[0][0] * point[0] + numeric_matrix[0][1] * point[1] + numeric_matrix[0][2],
            numeric_matrix[1][0] * point[0] + numeric_matrix[1][1] * point[1] + numeric_matrix[1][2],
        )
    if not isinstance(source["anchors"], list) or not source["anchors"]:
        _fail("bundle.source.anchors: expected a non-empty array")
    supplied_anchor_hash = _hash(source["anchor_set_hash"], "bundle.source.anchor_set_hash")
    expected_anchor_hash = compute_anchor_set_hash(source["coordinate_space"], source["source_file_hash"], source["normalized_hash"], source["anchors"])
    if supplied_anchor_hash != expected_anchor_hash:
        _fail(f"bundle.source.anchor_set_hash: expected canonical hash {expected_anchor_hash}")
    anchors: dict[str, dict[str, Any]] = {}
    allowed_kinds = {"space", "entrance", "opening", "fixed_feature", "ignore", "scale"}
    for index, raw in enumerate(source["anchors"]):
        path = f"bundle.source.anchors[{index}]"
        record = _exact_keys(raw, SOURCE_ANCHOR_KEYS, path)
        anchor_id = _identifier(record["anchor_id"], f"{path}.anchor_id")
        if anchor_id in anchors:
            _fail(f"{path}.anchor_id: duplicate ID {anchor_id!r}")
        if record["kind"] not in allowed_kinds:
            _fail(f"{path}.kind: unsupported anchor kind")
        points_norm, points_metric = record["points_norm"], record["points_metric_m"]
        expected_count = (2,) if record["kind"] == "scale" else (1, 2) if record["kind"] in {"entrance", "opening"} else (1,)
        if not isinstance(points_norm, list) or len(points_norm) not in expected_count or not isinstance(points_metric, list) or len(points_metric) != len(points_norm):
            _fail(f"{path}: normalized and metric anchor geometry point counts are invalid")
        normalized_points: list[tuple[float, float]] = []
        metric_points: list[tuple[float, float]] = []
        for point_index, (raw_norm, raw_metric) in enumerate(zip(points_norm, points_metric)):
            norm = _point(raw_norm, f"{path}.points_norm[{point_index}]")
            if not 0.0 <= norm[0] <= 1000.0 or not 0.0 <= norm[1] <= 1000.0:
                _fail(f"{path}.points_norm[{point_index}]: outside 0..1000")
            metric = _point(raw_metric, f"{path}.points_metric_m[{point_index}]")
            if math.dist(metric, metric_from_norm(norm)) > 1.0e-6 + 1.0e-12:
                _fail(f"{path}.points_metric_m[{point_index}]: differs from affine transform by more than 1e-6m")
            normalized_points.append(norm)
            metric_points.append(metric)
        distance_mm = record["distance_mm"]
        if record["kind"] == "scale":
            distance = _finite(distance_mm, f"{path}.distance_mm")
            if not 10.0 <= distance <= 1_000_000.0 or abs(math.dist(*metric_points) - distance / 1000.0) > 1.0e-6 + 1.0e-12:
                _fail(f"{path}.distance_mm: does not match transformed scale geometry")
        elif distance_mm is not None:
            _fail(f"{path}.distance_mm: non-scale anchors require null")
        anchors[anchor_id] = {"kind": record["kind"], "metric_points": metric_points}
    scale_anchor_id = _identifier(source["scale_anchor_id"], "bundle.source.scale_anchor_id")
    if (anchors.get(scale_anchor_id) or {}).get("kind") != "scale" or sum(anchor["kind"] == "scale" for anchor in anchors.values()) != 1:
        _fail("bundle.source.scale_anchor_id: must reference the only scale anchor")
    if not isinstance(source["anchor_opening_bindings"], list):
        _fail("bundle.source.anchor_opening_bindings: expected an array")
    bound_anchors: set[str] = set()
    bound_openings: set[str] = set()
    for index, raw in enumerate(source["anchor_opening_bindings"]):
        path = f"bundle.source.anchor_opening_bindings[{index}]"
        record = _exact_keys(raw, SOURCE_BINDING_KEYS, path)
        anchor_id = _identifier(record["anchor_id"], f"{path}.anchor_id")
        opening_id = _identifier(record["opening_id"], f"{path}.opening_id")
        anchor_kind = record["anchor_kind"]
        if anchor_id in bound_anchors or opening_id in bound_openings:
            _fail(f"{path}: anchor and opening bindings must both be one-to-one")
        anchor = anchors.get(anchor_id)
        if not anchor or anchor["kind"] != anchor_kind or anchor_kind not in {"entrance", "opening"}:
            _fail(f"{path}: anchor kind does not match a declared entrance/opening anchor")
        if opening_id not in opening_by_id:
            _fail(f"{path}.opening_id: unknown opening {opening_id!r}")
        if anchor_kind == "entrance" and opening_by_id[opening_id]["kind"] != "entrance":
            _fail(f"{path}: entrance anchor must bind an entrance opening")
        opening_segment = tuple(tuple(map(float, point)) for point in opening_by_id[opening_id]["segment_m"])
        anchor_points = tuple(anchor["metric_points"])
        if len(anchor_points) == 1:
            if _distance_point_to_segment(anchor_points[0], *opening_segment) > JUNCTION_CLEARANCE_M + 1.0e-9:
                _fail(f"{path}: independently derived point-to-opening distance exceeds 50mm")
        else:
            _, _, hausdorff = _segment_hausdorff(anchor_points, opening_segment)
            endpoint_error = _endpoint_match_error(anchor_points, opening_segment)
            anchor_length, opening_length = math.dist(*anchor_points), math.dist(*opening_segment)
            length_error = abs(anchor_length - opening_length)
            length_ratio = length_error / max(anchor_length, 1.0e-12)
            angle_error = _segment_angle_degrees(anchor_points, opening_segment)
            if hausdorff > JUNCTION_CLEARANCE_M + 1.0e-9 or endpoint_error > JUNCTION_CLEARANCE_M + 1.0e-9:
                _fail(f"{path}: independently derived segment Hausdorff/endpoint error exceeds 50mm")
            if length_error > JUNCTION_CLEARANCE_M + 1.0e-9 and length_ratio > 0.05 + 1.0e-9:
                _fail(f"{path}: independently derived length differs by more than 50mm and 5%")
            if angle_error > 2.0 + 1.0e-9:
                _fail(f"{path}: independently derived direction differs by more than 2 degrees")
        bound_anchors.add(anchor_id)
        bound_openings.add(opening_id)
    required = {anchor_id for anchor_id, anchor in anchors.items() if anchor["kind"] in {"entrance", "opening"}}
    if bound_anchors != required:
        _fail(f"bundle.source.anchor_opening_bindings: missing bindings for {sorted(required - bound_anchors)}")
    return deepcopy(dict(source))


def _validate_adjacency(
    raw_truth: Any,
    space_ids: set[str],
    openings: list[dict[str, Any]],
    opening_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    truth = _exact_keys(raw_truth, {"version", "edges", "confirmed"}, "adjacency_truth")
    if truth["version"] != ADJACENCY_SCHEMA:
        _fail(f"adjacency_truth.version: expected {ADJACENCY_SCHEMA}")
    if truth["confirmed"] is not True:
        _fail("adjacency_truth.confirmed: complete truth must be confirmed")
    if not isinstance(truth["edges"], list) or not truth["edges"]:
        _fail("adjacency_truth.edges: expected a non-empty array")
    allowed_spaces = space_ids | {"exterior"}
    ids: set[str] = set()
    pair_keys: set[tuple[str, str, str]] = set()
    door_coverage: Counter[str] = Counter()
    graph = {space_id: set() for space_id in allowed_spaces}
    edges: list[dict[str, Any]] = []
    for index, raw in enumerate(truth["edges"]):
        path = f"adjacency_truth.edges[{index}]"
        record = _exact_keys(raw, EDGE_KEYS, path)
        stable_id = _identifier(record["id"], f"{path}.id")
        if stable_id in ids:
            _fail(f"{path}.id: duplicate ID {stable_id!r}")
        ids.add(stable_id)
        a, b = record["space_a_id"], record["space_b_id"]
        if a not in allowed_spaces or b not in allowed_spaces or a == b:
            _fail(f"{path}: adjacency spaces must be distinct known IDs")
        kind = record["kind"]
        if kind not in {"door", "open_passage"}:
            _fail(f"{path}.kind: expected door|open_passage")
        key = (*sorted((a, b)), kind)
        if key in pair_keys:
            _fail(f"{path}: duplicate adjacency pair/type")
        pair_keys.add(key)
        opening_id = record["opening_id"]
        if kind == "door":
            if opening_id not in opening_by_id or opening_by_id[opening_id]["kind"] not in {"door", "entrance"}:
                _fail(f"{path}.opening_id: door edge requires an active door/entrance")
            opening = opening_by_id[opening_id]
            if {a, b} != {opening["side_a_space_id"], opening["side_b_space_id"]}:
                _fail(f"{path}: edge spaces must match opening sides")
            door_coverage[opening_id] += 1
        elif opening_id is not None:
            _fail(f"{path}.opening_id: open_passage requires null")
        if record["confirmed"] is not True:
            _fail(f"{path}.confirmed: only confirmed adjacency is buildable")
        graph[a].add(b)
        graph[b].add(a)
        edges.append(deepcopy(dict(record)))
    for opening in openings:
        expected = 1 if opening["kind"] in {"door", "entrance"} else 0
        if door_coverage[opening["id"]] != expected:
            _fail(
                f"adjacency_truth: opening {opening['id']} requires {expected} door edge(s), "
                f"found {door_coverage[opening['id']]}"
            )
    seen, queue = {"exterior"}, ["exterior"]
    while queue:
        current = queue.pop(0)
        for neighbor in graph[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    missing = sorted(space_ids - seen)
    if missing:
        _fail(f"adjacency_truth: spaces unreachable from exterior: {missing}")
    return edges


def validate_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached, normalized build contract or raise before writes."""

    root = _exact_keys(bundle, TOP_LEVEL_KEYS, "bundle")
    if root["schema"] != SCHEMA:
        _fail(f"bundle.schema: expected {SCHEMA}")
    _json_metadata(root["project"], "bundle.project")
    source_hash = _hash(root["source_hash"], "bundle.source_hash")
    supplied_hash = _hash(root["structure_hash"], "bundle.structure_hash")
    expected_hash = compute_structure_hash(root)
    if supplied_hash != expected_hash:
        _fail(f"bundle.structure_hash: expected canonical hash {expected_hash}")
    outer = _polygon(root["outer_boundary_m"], "outer_boundary_m")
    spaces, space_ids = _validate_spaces(root["spaces"], outer)
    walls, wall_by_id, junction_by_id = _validate_walls(root["wall_branch_graph"], space_ids)
    junction_errors = [item for item in analyze_wall_junctions(walls) if item["severity"] == "error" and item["kind"] != "unsplit_x_junction"]
    if junction_errors:
        first = junction_errors[0]
        _fail(f"wall_branch_graph: {first['kind']} between {first['wall_ids']}")
    openings, opening_by_id, _ = _validate_openings(
        root["opening_contract"], walls, wall_by_id, junction_by_id, space_ids
    )
    _validate_source(root["source"], source_hash=source_hash, opening_by_id=opening_by_id)
    edges = _validate_adjacency(root["adjacency_truth"], space_ids, openings, opening_by_id)
    assumptions = _exact_keys(
        root["assumptions"],
        {"scale_m_per_unit", "floor_slab_thickness_m", "research_only"},
        "assumptions",
    )
    scale = _finite(assumptions["scale_m_per_unit"], "assumptions.scale_m_per_unit")
    if not math.isclose(scale, 1.0, abs_tol=1.0e-12):
        _fail("assumptions.scale_m_per_unit: metric bundle fields require scale 1.0")
    _bounded(
        assumptions["floor_slab_thickness_m"],
        "assumptions.floor_slab_thickness_m",
        0.02,
        1.0,
    )
    if assumptions["research_only"] is not True:
        _fail("assumptions.research_only: must be true")
    if not isinstance(root["unresolved_issues"], list) or not all(
        isinstance(item, str) and item.strip() == item and item
        for item in root["unresolved_issues"]
    ):
        _fail("unresolved_issues: expected an array of non-empty trimmed strings")

    all_ids = [space["id"] for space in spaces] + [wall["id"] for wall in walls]
    all_ids += [opening["id"] for opening in openings] + [edge["id"] for edge in edges]
    duplicates = sorted(item for item, count in Counter(all_ids).items() if count > 1)
    if duplicates:
        _fail(f"bundle: IDs must be globally unique; duplicates={duplicates}")

    # Preserve the caller's canonical document byte semantics. Mesh helpers
    # normalize winding internally; changing point order here would invalidate
    # the already verified structure_hash on the Blender/IFC re-validation.
    return deepcopy(dict(root))


def openings_for_wall(bundle: Mapping[str, Any], wall_id: str) -> list[dict[str, Any]]:
    wall_by_id = {wall["id"]: wall for wall in bundle["wall_branch_graph"]["walls"]}
    wall = wall_by_id[wall_id]
    result = [
        deepcopy(opening)
        for opening in bundle["opening_contract"]["openings"]
        if opening["owning_wall_id"] == wall_id
    ]
    result.sort(key=lambda item: project_opening(wall, item)["start_m"])
    return result


def _dedupe_sorted(values: Iterable[float], epsilon: float = 1.0e-9) -> list[float]:
    result: list[float] = []
    for value in sorted(float(item) for item in values):
        if not result or abs(value - result[-1]) > epsilon:
            result.append(value)
    return result


def wall_mesh(
    wall: Mapping[str, Any],
    openings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create a manifold wall mesh by occupied T/Z grid cells, no Booleans."""

    a = tuple(map(float, wall["centerline_m"][0]))
    b = tuple(map(float, wall["centerline_m"][1]))
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    tx, ty = dx / length, dy / length
    nx, ny = -ty, tx
    half = float(wall["thickness_m"]) * 0.5
    base = float(wall["base_m"])
    top = base + float(wall["height_m"])
    projection_by_id = {opening["id"]: project_opening(wall, opening) for opening in openings}
    t_cuts = _dedupe_sorted(
        [0.0, length]
        + [value for opening in openings for value in (
            projection_by_id[opening["id"]]["start_m"],
            projection_by_id[opening["id"]]["end_m"],
        )]
    )
    z_cuts = _dedupe_sorted(
        [base, top]
        + [value for opening in openings for value in (
            float(opening["sill_m"]),
            float(opening["head_m"]),
        ) if base < value < top]
    )
    occupied: dict[tuple[int, int], bool] = {}
    for ti in range(len(t_cuts) - 1):
        tm = (t_cuts[ti] + t_cuts[ti + 1]) * 0.5
        for zi in range(len(z_cuts) - 1):
            zm = (z_cuts[zi] + z_cuts[zi + 1]) * 0.5
            void = any(
                projection_by_id[opening["id"]]["start_m"] < tm < projection_by_id[opening["id"]]["end_m"]
                and float(opening["sill_m"]) < zm < float(opening["head_m"])
                for opening in openings
            )
            occupied[(ti, zi)] = not void

    vertices: list[list[float]] = []
    vertex_index: dict[tuple[int, int, int], int] = {}
    faces: list[list[int]] = []

    def vertex(ti: int, side: int, zi: int) -> int:
        key = (ti, side, zi)
        if key not in vertex_index:
            t = t_cuts[ti]
            normal = -half if side == 0 else half
            vertex_index[key] = len(vertices)
            vertices.append([
                a[0] + t * tx + normal * nx,
                a[1] + t * ty + normal * ny,
                z_cuts[zi],
            ])
        return vertex_index[key]

    for ti in range(len(t_cuts) - 1):
        for zi in range(len(z_cuts) - 1):
            if not occupied[(ti, zi)]:
                continue
            # Two long faces, outward -N and +N.
            faces.append([vertex(ti, 0, zi), vertex(ti + 1, 0, zi), vertex(ti + 1, 0, zi + 1), vertex(ti, 0, zi + 1)])
            faces.append([vertex(ti, 1, zi), vertex(ti, 1, zi + 1), vertex(ti + 1, 1, zi + 1), vertex(ti + 1, 1, zi)])
            if ti == 0 or not occupied[(ti - 1, zi)]:
                faces.append([vertex(ti, 0, zi), vertex(ti, 0, zi + 1), vertex(ti, 1, zi + 1), vertex(ti, 1, zi)])
            if ti == len(t_cuts) - 2 or not occupied[(ti + 1, zi)]:
                faces.append([vertex(ti + 1, 0, zi), vertex(ti + 1, 1, zi), vertex(ti + 1, 1, zi + 1), vertex(ti + 1, 0, zi + 1)])
            if zi == 0 or not occupied[(ti, zi - 1)]:
                faces.append([vertex(ti, 0, zi), vertex(ti, 1, zi), vertex(ti + 1, 1, zi), vertex(ti + 1, 0, zi)])
            if zi == len(z_cuts) - 2 or not occupied[(ti, zi + 1)]:
                faces.append([vertex(ti, 0, zi + 1), vertex(ti + 1, 0, zi + 1), vertex(ti + 1, 1, zi + 1), vertex(ti, 1, zi + 1)])

    edge_counts: Counter[tuple[int, int]] = Counter()
    for face in faces:
        for first, second in zip(face, face[1:] + face[:1]):
            edge_counts[tuple(sorted((first, second)))] += 1
    non_manifold = sum(count != 2 for count in edge_counts.values())
    if non_manifold:
        _fail(f"wall {wall['id']}: generated non-manifold edge count {non_manifold}")
    return {
        "vertices": vertices,
        "faces": faces,
        "occupied_cells": sum(occupied.values()),
        "t_cuts_m": t_cuts,
        "z_cuts_m": z_cuts,
        "non_manifold_edges": non_manifold,
        "opening_cuts": [
            {
                "id": opening["id"],
                "kind": opening["kind"],
                "start_m": projection_by_id[opening["id"]]["start_m"],
                "end_m": projection_by_id[opening["id"]]["end_m"],
                "sill_m": float(opening["sill_m"]),
                "head_m": float(opening["head_m"]),
            }
            for opening in openings
        ],
    }


def _point_in_triangle(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    return (
        _orientation(a, b, point) >= 0
        and _orientation(b, c, point) >= 0
        and _orientation(c, a, point) >= 0
    )


def triangulate_polygon(points: Sequence[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Deterministic ear clipping for one validated simple CCW polygon."""

    ring = list(points)
    if _signed_area(ring) < 0.0:
        ring.reverse()
    original_index = {point: index for index, point in enumerate(points)}
    remaining = list(range(len(ring)))
    triangles: list[tuple[int, int, int]] = []
    guard = 0
    while len(remaining) > 3:
        guard += 1
        if guard > len(ring) ** 2:
            _fail("outer_boundary_m: ear clipping did not converge")
        clipped = False
        for position, current in enumerate(remaining):
            previous = remaining[position - 1]
            following = remaining[(position + 1) % len(remaining)]
            a, b, c = ring[previous], ring[current], ring[following]
            if _orientation(a, b, c) <= 0:
                continue
            if any(
                candidate not in {previous, current, following}
                and _point_in_triangle(ring[candidate], a, b, c)
                for candidate in remaining
            ):
                continue
            triangles.append((original_index[a], original_index[b], original_index[c]))
            remaining.pop(position)
            clipped = True
            break
        if not clipped:
            _fail("outer_boundary_m: polygon cannot be triangulated")
    a, b, c = (ring[index] for index in remaining)
    triangles.append((original_index[a], original_index[b], original_index[c]))
    return triangles


def floor_mesh(points: Sequence[Sequence[float]], thickness_m: float) -> dict[str, Any]:
    ring = [tuple(map(float, point)) for point in points]
    if _signed_area(ring) < 0.0:
        ring.reverse()
    triangles = triangulate_polygon(ring)
    count = len(ring)
    vertices = [[x, y, -thickness_m] for x, y in ring] + [[x, y, 0.0] for x, y in ring]
    faces: list[list[int]] = []
    for triangle in triangles:
        faces.append(list(reversed(triangle)))
        faces.append([index + count for index in triangle])
    for index in range(count):
        following = (index + 1) % count
        faces.append([index, following, following + count, index + count])
    return {"vertices": vertices, "faces": faces}


def stable_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "id"
    if token != value or len(token) > 48:
        token = f"{token[:37].rstrip('-._')}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:10]}"
    return token
