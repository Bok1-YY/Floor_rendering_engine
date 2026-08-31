"""Pure-Python contract-to-artifact geometry verification."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .contract import ResearchModelError, analyze_wall_junctions, project_opening


POSITION_TOLERANCE_M = 0.001
ANGLE_TOLERANCE_DEGREES = 0.1


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]], tolerance: float) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _distance_to_segment_2d(point, previous, current) <= tolerance:
            return True
        if (current[1] > y) != (previous[1] > y):
            cross_x = (previous[0] - current[0]) * (y - current[1]) / (previous[1] - current[1]) + current[0]
            if x < cross_x:
                inside = not inside
        previous = current
    return inside


def _distance_to_segment_2d(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1.0e-18:
        return math.dist(point, start)
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def verify_wall_opening_voids(
    wall: Mapping[str, Any],
    openings: list[Mapping[str, Any]],
    vertices_world: list[tuple[float, float, float]],
    faces: list[list[int]],
    *,
    tolerance_m: float = POSITION_TOLERANCE_M,
) -> dict[str, Any]:
    """Verify door/window voids from real wall mesh faces, not semantic empties."""

    a, b = _point(wall["centerline_m"][0], "wall.centerline"), _point(wall["centerline_m"][1], "wall.centerline")
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    tx, ty = dx / length, dy / length
    nx, ny = -ty, tx
    local = [((x - a[0]) * tx + (y - a[1]) * ty, (x - a[0]) * nx + (y - a[1]) * ny, z) for x, y, z in vertices_world]
    n_min, n_max = min(value[1] for value in local), max(value[1] for value in local)
    polygons = [[local[index] for index in face] for face in faces]
    skins = {
        "front": [[(value[0], value[2]) for value in polygon] for polygon in polygons if max(value[1] for value in polygon) - min(value[1] for value in polygon) <= tolerance_m and abs(sum(value[1] for value in polygon) / len(polygon) - n_min) <= tolerance_m],
        "back": [[(value[0], value[2]) for value in polygon] for polygon in polygons if max(value[1] for value in polygon) - min(value[1] for value in polygon) <= tolerance_m and abs(sum(value[1] for value in polygon) / len(polygon) - n_max) <= tolerance_m],
    }
    projections = {opening["id"]: project_opening(wall, opening) for opening in openings}
    base, top = float(wall["base_m"]), float(wall["base_m"]) + float(wall["height_m"])
    t_cuts = sorted({0.0, length, *(value for opening in openings for value in (projections[opening["id"]]["start_m"], projections[opening["id"]]["end_m"]))})
    z_cuts = sorted({base, top, *(float(value) for opening in openings for value in (opening["sill_m"], opening["head_m"]))})
    samples: list[dict[str, Any]] = []
    for t0, t1 in zip(t_cuts, t_cuts[1:]):
        for z0, z1 in zip(z_cuts, z_cuts[1:]):
            if t1 - t0 <= 1.0e-9 or z1 - z0 <= 1.0e-9:
                continue
            t_mid, z_mid = (t0 + t1) * 0.5, (z0 + z1) * 0.5
            expected_solid = not any(projections[opening["id"]]["start_m"] < t_mid < projections[opening["id"]]["end_m"] and float(opening["sill_m"]) < z_mid < float(opening["head_m"]) for opening in openings)
            coverage = {side: any(_point_in_polygon((t_mid, z_mid), polygon, tolerance_m) for polygon in side_polygons) for side, side_polygons in skins.items()}
            if any(value != expected_solid for value in coverage.values()):
                raise ResearchModelError(f"artifact wall {wall['id']} mesh occupancy disagrees with opening void at t={t_mid:.4f}, z={z_mid:.4f}")
            samples.append({"t_m": t_mid, "z_m": z_mid, "expected_solid": expected_solid})

    cuts: list[dict[str, Any]] = []
    for opening in openings:
        projection = projections[opening["id"]]
        sill, head = float(opening["sill_m"]), float(opening["head_m"])
        vertical_planes = []
        horizontal_planes = []
        for polygon in polygons:
            ts, ns, zs = [value[0] for value in polygon], [value[1] for value in polygon], [value[2] for value in polygon]
            if max(ts) - min(ts) <= tolerance_m and max(ns) - min(ns) >= float(wall["thickness_m"]) - 2 * tolerance_m and min(zs) <= (sill + head) * 0.5 <= max(zs):
                vertical_planes.append(sum(ts) / len(ts))
            if max(zs) - min(zs) <= tolerance_m and max(ns) - min(ns) >= float(wall["thickness_m"]) - 2 * tolerance_m and min(ts) <= (projection["start_m"] + projection["end_m"]) * 0.5 <= max(ts):
                horizontal_planes.append(sum(zs) / len(zs))
        start_error = min((abs(value - projection["start_m"]) for value in vertical_planes), default=math.inf)
        end_error = min((abs(value - projection["end_m"]) for value in vertical_planes), default=math.inf)
        head_error = min((abs(value - head) for value in horizontal_planes), default=math.inf)
        sill_error = 0.0 if math.isclose(sill, base, abs_tol=1.0e-9) else min((abs(value - sill) for value in horizontal_planes), default=math.inf)
        if max(start_error, end_error, head_error, sill_error) > tolerance_m + 1.0e-9:
            raise ResearchModelError(f"artifact opening {opening['id']} mesh cut boundary exceeds 1mm")
        cuts.append({"id": opening["id"], "start_error_m": start_error, "end_error_m": end_error, "sill_error_m": sill_error, "head_error_m": head_error})
    return {"status": "pass", "sample_count": len(samples), "cuts": cuts}


def _point(value: Any, path: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ResearchModelError(f"{path}: expected [x, y]")
    return float(value[0]), float(value[1])


def _segment_error(expected: Any, actual: Any) -> tuple[float, float]:
    expected_segment = (_point(expected[0], "expected segment"), _point(expected[1], "expected segment"))
    actual_segment = (_point(actual[0], "actual segment"), _point(actual[1], "actual segment"))
    direct = max(math.dist(expected_segment[0], actual_segment[0]), math.dist(expected_segment[1], actual_segment[1]))
    reverse = max(math.dist(expected_segment[0], actual_segment[1]), math.dist(expected_segment[1], actual_segment[0]))
    position_error = min(direct, reverse)
    ev = (expected_segment[1][0] - expected_segment[0][0], expected_segment[1][1] - expected_segment[0][1])
    av = (actual_segment[1][0] - actual_segment[0][0], actual_segment[1][1] - actual_segment[0][1])
    denominator = math.hypot(*ev) * math.hypot(*av)
    if denominator <= 1.0e-18:
        angle_error = 180.0
    else:
        cosine = max(-1.0, min(1.0, abs((ev[0] * av[0] + ev[1] * av[1]) / denominator)))
        angle_error = math.degrees(math.acos(cosine))
    return position_error, angle_error


def verify_contract_geometry(
    bundle: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    tolerance_m: float = POSITION_TOLERANCE_M,
    angle_tolerance_degrees: float = ANGLE_TOLERANCE_DEGREES,
) -> dict[str, Any]:
    """Compare independent artifact coordinates with the structure contract."""

    if report.get("schema") != "research-artifact-geometry-v1":
        raise ResearchModelError("artifact geometry report schema mismatch")
    if report.get("structure_hash") != bundle.get("structure_hash"):
        raise ResearchModelError("artifact geometry report structure hash mismatch")
    checks: list[dict[str, Any]] = []

    expected_walls = {item["id"]: item for item in bundle["wall_branch_graph"]["walls"]}
    actual_walls = {item.get("id"): item for item in report.get("walls") or []}
    if set(actual_walls) != set(expected_walls):
        raise ResearchModelError("artifact wall ID set mismatch")
    for wall_id, expected in expected_walls.items():
        actual = actual_walls[wall_id]
        position_error, angle_error = _segment_error(expected["centerline_m"], actual.get("centerline_m"))
        scalar_error = max(abs(float(expected[key]) - float(actual.get(key))) for key in ("thickness_m", "base_m", "height_m"))
        if position_error > tolerance_m + 1.0e-9 or scalar_error > tolerance_m + 1.0e-9 or angle_error > angle_tolerance_degrees + 1.0e-9:
            raise ResearchModelError(f"artifact wall {wall_id} exceeds 1mm/0.1-degree contract tolerance")
        checks.append({"id": f"wall:{wall_id}", "status": "pass", "position_error_m": position_error, "scalar_error_m": scalar_error, "angle_error_degrees": angle_error})

    expected_openings = {item["id"]: item for item in bundle["opening_contract"]["openings"]}
    actual_openings = {item.get("id"): item for item in report.get("openings") or []}
    if set(actual_openings) != set(expected_openings):
        raise ResearchModelError("artifact opening ID set mismatch")
    for opening_id, expected in expected_openings.items():
        actual = actual_openings[opening_id]
        position_error, angle_error = _segment_error(expected["segment_m"], actual.get("segment_m"))
        scalar_error = max(abs(float(expected[key]) - float(actual.get(key))) for key in ("width_m", "sill_m", "head_m"))
        if position_error > tolerance_m + 1.0e-9 or scalar_error > tolerance_m + 1.0e-9 or angle_error > angle_tolerance_degrees + 1.0e-9:
            raise ResearchModelError(f"artifact opening {opening_id} exceeds 1mm/0.1-degree contract tolerance")
        checks.append({"id": f"opening:{opening_id}", "status": "pass", "position_error_m": position_error, "scalar_error_m": scalar_error, "angle_error_degrees": angle_error})

    expected_spaces = {item["id"]: _point(item["point_m"], "space.point_m") for item in bundle["spaces"]}
    actual_spaces = {item.get("id"): _point(item.get("point_m"), "artifact space.point_m") for item in report.get("spaces") or []}
    if set(actual_spaces) != set(expected_spaces):
        raise ResearchModelError("artifact space ID set mismatch")
    if any(math.dist(expected_spaces[item], actual_spaces[item]) > tolerance_m + 1.0e-9 for item in expected_spaces):
        raise ResearchModelError("artifact space point exceeds 1mm contract tolerance")

    artifact_junctions = analyze_wall_junctions(report.get("walls") or [], tolerance_m=tolerance_m)
    junction_errors = [item for item in artifact_junctions if item["severity"] == "error"]
    if junction_errors:
        raise ResearchModelError(f"artifact wall junction failure: {junction_errors[0]['kind']}")
    return {
        "schema": "research-contract-artifact-verify-v1",
        "status": "pass",
        "structure_hash": bundle["structure_hash"],
        "tolerance_m": tolerance_m,
        "angle_tolerance_degrees": angle_tolerance_degrees,
        "checks": checks,
        "junctions": artifact_junctions,
    }
