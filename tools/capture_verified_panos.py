#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create auditable software cube/ERP captures for a verified whole-home project.

This is a deterministic fallback capture path, not a WebGL audit substitute.
Every manifest records ``webgl_capture=false`` and the numpy z-buffer renderer.
The resulting six-channel capture still uses the public HTTP API and can be
opened by the same panorama viewer and paid-preview pipeline.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "Floor_engine_server"
if PACKAGE_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME, REPOSITORY / "__init__.py",
        submodule_search_locations=[str(REPOSITORY)])
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load Floor_engine_server package")
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)

from PIL import Image  # noqa: E402
from shapely.affinity import rotate, translate  # noqa: E402
from shapely.geometry import LineString, Point, Polygon, box  # noqa: E402

from Floor_engine_server.whole_home_engine import load_project  # noqa: E402
from Floor_engine_server.whole_home_pano_render import (  # noqa: E402
    CUBE_FACE_ORDER,
    cube_faces_to_atlas,
    render_cube_faces,
)
from Floor_engine_server.whole_home_reference_camera import (  # noqa: E402
    pano_hotspot_origin_clear,
)


def _data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _post_json(url: str, payload: dict, *, timeout: int = 300) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {ex.code}: {body[:4000]}") from ex


def _safe_center(model: dict, room: dict) -> tuple[float, float]:
    shape = Polygon([(float(row["x"]), float(row["z"])) for row in room.get("polygon") or []])
    if not shape.is_valid:
        shape = shape.buffer(0)
    if shape.is_empty or shape.geom_type != "Polygon":
        raise ValueError(f"room {room.get('id')} has no valid polygon")
    min_x, min_z, max_x, max_z = shape.bounds
    candidates = [shape.representative_point(), shape.centroid]
    step = max(.15, min(.35, math.sqrt(max(shape.area, .01)) / 12))
    z_value = min_z + .25
    while z_value <= max_z - .25 + 1e-9:
        x_value = min_x + .25
        while x_value <= max_x - .25 + 1e-9:
            point = Point(x_value, z_value)
            if shape.covers(point):
                candidates.append(point)
            x_value += step
        z_value += step
    obstacles = []
    for item in model.get("fixed_objects") or []:
        if item.get("review_status") == "rejected":
            continue
        position, size = item.get("position") or {}, item.get("size") or {}
        sx, sz = max(.02, float(size.get("x") or 0)), max(.02, float(size.get("z") or 0))
        footprint = box(-sx / 2, -sz / 2, sx / 2, sz / 2)
        footprint = rotate(
            footprint, float(item.get("rotation_y_deg") or 0),
            origin=(0, 0), use_radians=False)
        obstacles.append(translate(
            footprint, float(position.get("x") or 0), float(position.get("z") or 0)
        ).buffer(max(.12, float(item.get("clearance_m") or 0))))
    walls = []
    for wall in model.get("walls") or []:
        start, end = wall.get("start") or {}, wall.get("end") or {}
        line = LineString([
            (float(start.get("x") or 0), float(start.get("z") or 0)),
            (float(end.get("x") or 0), float(end.get("z") or 0)),
        ])
        if line.length > 1e-6:
            walls.append((line, max(.02, float(wall.get("thickness_m") or .12)) / 2))

    def clearance(point: Point) -> float:
        scores = [float(point.distance(shape.boundary))]
        scores.extend(float(point.distance(line)) - half for line, half in walls)
        scores.extend(
            -1.0 if footprint.covers(point) else float(point.distance(footprint))
            for footprint in obstacles)
        return min(scores) if scores else 0.0

    # A panorama only needs to be inside its semantic room; visual usefulness
    # depends on clearance from the actual structural shell and CAD furniture.
    # Stable tie-breaking makes repeated captures land at the same coordinate.
    candidates = sorted(
        candidates,
        key=lambda point: (-clearance(point), point.x, point.y))
    for point in candidates:
        probes = [(point.x, point.y)] + [
            (point.x + .20 * math.cos(index * math.pi / 4),
             point.y + .20 * math.sin(index * math.pi / 4))
            for index in range(8)
        ]
        if all(shape.buffer(1e-6).covers(Point(probe))
               and pano_hotspot_origin_clear(model, probe) for probe in probes):
            return round(float(point.x), 5), round(float(point.y), 5)
    raise ValueError(f"room {room.get('id')} has no 0.20m-clear panorama center")


def _select_rooms(model: dict) -> list[tuple[str, dict]]:
    rooms = [row for row in model.get("rooms") or [] if row.get("selected", True)]
    contract_profiles = {
        str(row.get("room_id") or ""): str(
            row.get("reference_room_profile") or row.get("profile") or "")
        for row in model.get("room_contracts") or []
    }

    def profile(room: dict) -> str:
        # A reviewed room contract is more specific than the parser's generic
        # semantic class (both master and secondary bedrooms are simply
        # ``bedroom`` there).  Do not let that generic value hide the contract.
        return str(room.get("reference_room_profile")
                   or contract_profiles.get(str(room.get("id") or ""))
                   or room.get("semantic_profile")
                   or room.get("room_type") or "").lower()

    selectors = [
        ("living", lambda value: "living" in profile(value)),
        ("master_bedroom", lambda value: "bedroom_master" in profile(value)
         or "primary_bedroom" in profile(value)),
        ("secondary_bedroom", lambda value: "bedroom_secondary" in profile(value)
         or profile(value) == "bedroom"),
    ]
    selected: list[tuple[str, dict]] = []
    used: set[str] = set()
    for label, predicate in selectors:
        matches = [row for row in rooms if predicate(row) and str(row.get("id")) not in used]
        if not matches:
            continue
        if label == "secondary_bedroom":
            specific = [
                row for row in matches
                if "bedroom_secondary" in profile(row)
                or "secondary_bedroom" in profile(row)
            ]
            if specific:
                matches = specific
        matches.sort(key=lambda row: -Polygon([
            (float(point["x"]), float(point["z"]))
            for point in row.get("polygon") or []]).area)
        selected.append((label, matches[0]))
        used.add(str(matches[0].get("id")))
    if len(selected) < 3:
        remaining = [row for row in rooms if str(row.get("id")) not in used]
        remaining.sort(key=lambda row: -Polygon([
            (float(point["x"]), float(point["z"]))
            for point in row.get("polygon") or []]).area)
        for row in remaining[:3 - len(selected)]:
            selected.append((f"room_{len(selected) + 1}", row))
    if len(selected) != 3:
        raise ValueError(f"need three rooms, found {len(selected)}")
    return selected


def capture(project_id: str, base_url: str, face_size: int, capture_version: str,
            only_label: str = '') -> dict:
    project = load_project(project_id)
    if not project or not project.get("verified"):
        raise SystemExit("project must exist and be verified")
    model = project.get("model") or {}
    rows = []
    selected_rooms = _select_rooms(model)
    if only_label:
        selected_rooms = [row for row in selected_rooms if row[0] == only_label]
        if not selected_rooms:
            raise ValueError(f'unknown or unavailable room label: {only_label}')
    for label, room in selected_rooms:
        center_x, center_z = _safe_center(model, room)
        center = (center_x, 1.55, center_z)
        faces = render_cube_faces(model, center, face_size, .05, 30.0, subjects=[])
        atlases = {
            kind: cube_faces_to_atlas({
                face: faces[face][kind] for face in CUBE_FACE_ORDER})
            for kind in ("rgb", "depth", "normal", "edge", "semantic", "subject_id")
        }
        pano_id = f"pano_{label}_verified_{capture_version}"
        initial_heading = {
            "living": 90.0,
            "master_bedroom": 0.0,
            "secondary_bedroom": 180.0,
        }.get(label, 0.0)
        payload = {
            "pano_id": pano_id,
            "camera": {
                "id": f"camera_{label}_verified_{capture_version}",
                "position": {"x": center_x, "y": 1.55, "z": center_z},
                "target": {"x": center_x, "y": 1.55, "z": center_z + 1.0},
                "focal_length_mm": 12.0,
            },
            "projection": "equirectangular",
            "coordinate_system": "right-handed-y-up",
            "camera_center_m": {"x": center_x, "y": 1.55, "z": center_z},
            "canonical_forward": "+Z",
            # Viewer-only initial heading.  ERP pixels and every geometry
            # channel keep the canonical cube projection unchanged.
            "heading_deg": initial_heading, "pitch_deg": 0, "roll_deg": 0,
            "horizontal_fov_deg": 360, "vertical_fov_deg": 180,
            "erp_width": 3840, "erp_height": 1920,
            "cube_face_size": face_size,
            "cube_face_order": list(CUBE_FACE_ORDER),
            "near_m": .05, "far_m": 30.0,
            "depth_encoding": "linear_metric_global_range",
            "normal_encoding": "world_space_xyz_to_rgb",
            **{f"{kind}_atlas_data_url": _data_url(image)
               for kind, image in atlases.items()},
            "semantic_legend": {},
            "subject_id_legend": {
                "version": "whole-home-subject-id-v1",
                "pixel_origin": "top-left", "subjects": [],
            },
            "render_contract": {
                "renderer": "numpy_zbuffer_v1",
                "webgl_capture": False,
                "capture_class": "deterministic_software_fallback_not_webgl_audit",
                "materials": {"clay": {"color": "#d8d4ca"}},
                "lighting": {"hemisphere": {"intensity": 2.2}},
            },
            "source_hash": "",
            "room_id": str(room.get("id") or ""),
            "annotator_id": "codex_software_capture",
        }
        response = _post_json(
            f"{base_url.rstrip('/')}/api/whole-home/projects/{project_id}/pano-captures",
            payload)
        active = next(
            row for row in reversed(response.get("pano_captures") or [])
            if row.get("pano_id") == pano_id and row.get("active", True))
        rows.append({
            "pano_id": pano_id, "room_id": room.get("id"), "room_label": room.get("label"),
            "center_m": {"x": center_x, "y": 1.55, "z": center_z},
            "capture_id": active.get("capture_id"),
            "source_hash": (active.get("manifest") or {}).get("source_hash"),
            "rgb_erp_path": ((active.get("manifest") or {}).get("channels") or {}).get("rgb_erp"),
            "renderer": "numpy_zbuffer_v1", "webgl_capture": False,
        })
    return {"project_id": project_id, "captures": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--face-size", type=int, default=512, choices=(128, 256, 512, 768, 1024))
    parser.add_argument("--capture-version", default="v2")
    parser.add_argument("--only", default="", choices=(
        "", "living", "master_bedroom", "secondary_bedroom"),
        help="render only one selected room label")
    args = parser.parse_args()
    print(json.dumps(capture(
        args.project_id, args.base_url, args.face_size, args.capture_version, args.only),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
