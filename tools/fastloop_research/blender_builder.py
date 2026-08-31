"""Blender-side deterministic builder for ``research-structure-bundle-v1``."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence

import bpy
from mathutils import Vector


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if os.fspath(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(REPOSITORY_ROOT))

from tools.fastloop_research.contract import (  # noqa: E402
    floor_mesh,
    openings_for_wall,
    project_opening,
    stable_token,
    validate_bundle,
    wall_mesh,
)


RENDER_SIZE = 1024
CAMERA_MARGIN = 1.12
COLLECTION_NAME = "COL-FASTLOOP-RESEARCH"


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _load_bundle(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read bundle: {exc}")
    return validate_bundle(raw)


def _assert_factory_startup() -> None:
    if bpy.data.filepath:
        _fail("builder requires an unsaved --factory-startup scene")
    expected = {"Camera": "CAMERA", "Cube": "MESH", "Light": "LIGHT"}
    actual = {obj.name: obj.type for obj in bpy.data.objects}
    if len(bpy.data.scenes) != 1 or actual != expected:
        _fail(f"builder requires untouched factory startup objects; found {actual}")
    cube = bpy.data.objects.get("Cube")
    if cube is None or len(cube.data.vertices) != 8 or tuple(round(v, 6) for v in cube.dimensions) != (2.0, 2.0, 2.0):
        _fail("factory Cube is not untouched")


def _clear_factory() -> bpy.types.Collection:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    collection = bpy.data.collections.new(COLLECTION_NAME)
    bpy.context.scene.collection.children.link(collection)
    return collection


def _configure_scene(scene: bpy.types.Scene, bundle: Mapping[str, Any]) -> None:
    scene.name = "SCENE-FASTLOOP-RESEARCH"
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"
    scene["schema"] = bundle["schema"]
    scene["source_hash"] = bundle["source_hash"]
    scene["structure_hash"] = bundle["structure_hash"]
    scene["coordinate_units"] = "metres"
    scene["up_axis"] = "Z"
    scene["research_only"] = True
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = RENDER_SIZE
    scene.render.resolution_y = RENDER_SIZE
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = False
    scene.render.use_file_extension = True
    scene.render.use_overwrite = True
    scene.render.dither_intensity = 0.0
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.show_specular_highlight = False
    scene.display.shading.background_type = "WORLD"
    if hasattr(scene.display.shading, "show_outline"):
        scene.display.shading.show_outline = True
    world = scene.world or bpy.data.worlds.new("WORLD-FASTLOOP-RESEARCH")
    scene.world = world
    world.name = "WORLD-FASTLOOP-RESEARCH"
    world.color = (0.92, 0.92, 0.90)
    if hasattr(bpy.context.preferences.filepaths, "save_version"):
        bpy.context.preferences.filepaths.save_version = 0


def _mesh_object(
    collection: bpy.types.Collection,
    name: str,
    payload: Mapping[str, Any],
    *,
    color: tuple[float, float, float, float],
    properties: Mapping[str, Any],
) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}-MESH")
    mesh.from_pydata(payload["vertices"], [], payload["faces"])
    mesh.update(calc_edges=True)
    if not mesh.vertices or not mesh.polygons:
        _fail(f"{name}: empty mesh")
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.color = color
    for key, value in properties.items():
        if value is None:
            obj[key] = "null"
        elif isinstance(value, (str, int, float, bool)):
            obj[key] = value
        else:
            obj[key] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return obj


def _semantic_objects(
    collection: bpy.types.Collection,
    bundle: Mapping[str, Any],
    wall_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[list[bpy.types.Object], list[bpy.types.Object]]:
    openings: list[bpy.types.Object] = []
    for opening in bundle["opening_contract"]["openings"]:
        wall = wall_by_id[opening["owning_wall_id"]]
        projection = project_opening(wall, opening)
        a = Vector(wall["centerline_m"][0])
        b = Vector(wall["centerline_m"][1])
        tangent = (b - a).normalized()
        center = a + tangent * ((projection["start_m"] + projection["end_m"]) * 0.5)
        obj = bpy.data.objects.new(f"OPENING-{stable_token(opening['id'])}", None)
        collection.objects.link(obj)
        obj.empty_display_type = "CUBE"
        obj.empty_display_size = 1.0
        obj.location = (center.x, center.y, (float(opening["sill_m"]) + float(opening["head_m"])) * 0.5)
        obj.rotation_euler[2] = math.atan2(tangent.y, tangent.x)
        obj.scale = (
            float(opening["width_m"]) * 0.5,
            float(wall["thickness_m"]) * 0.5,
            (float(opening["head_m"]) - float(opening["sill_m"])) * 0.5,
        )
        for key in (
            "id",
            "kind",
            "owning_wall_id",
            "width_m",
            "sill_m",
            "head_m",
            "side_a_space_id",
            "side_b_space_id",
            "source",
        ):
            obj[key] = opening[key]
        obj["research_only"] = True
        obj["structure_hash"] = bundle["structure_hash"]
        openings.append(obj)

    spaces: list[bpy.types.Object] = []
    for space in bundle["spaces"]:
        obj = bpy.data.objects.new(f"SPACE-{stable_token(space['id'])}", None)
        collection.objects.link(obj)
        obj.empty_display_type = "CIRCLE"
        obj.empty_display_size = 0.20
        obj.location = (float(space["point_m"][0]), float(space["point_m"][1]), 0.05)
        obj["id"] = space["id"]
        obj["label"] = space["label"]
        obj["research_only"] = True
        obj["structure_hash"] = bundle["structure_hash"]
        spaces.append(obj)
    return openings, spaces


def _world_bbox(objects: Iterable[bpy.types.Object]) -> tuple[Vector, Vector]:
    points: list[Vector] = []
    for obj in objects:
        if obj.type == "MESH" and obj.data is not None:
            points.extend(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)
    if not points:
        _fail("no geometry for camera framing")
    return (
        Vector((min(v.x for v in points), min(v.y for v in points), min(v.z for v in points))),
        Vector((max(v.x for v in points), max(v.y for v in points), max(v.z for v in points))),
    )


def _new_camera(collection: bpy.types.Collection, name: str) -> bpy.types.Object:
    data = bpy.data.cameras.new(name)
    data.type = "ORTHO"
    data.clip_start = 0.01
    data.clip_end = 10000.0
    camera = bpy.data.objects.new(name, data)
    collection.objects.link(camera)
    return camera


def _bbox_corners(minimum: Vector, maximum: Vector) -> list[Vector]:
    return [Vector((x, y, z)) for x in (minimum.x, maximum.x) for y in (minimum.y, maximum.y) for z in (minimum.z, maximum.z)]


def _frame_iso(camera: bpy.types.Object, minimum: Vector, maximum: Vector, direction: Vector) -> None:
    target = (minimum + maximum) * 0.5
    direction.normalize()
    distance = max(20.0, (maximum - minimum).length * 3.0)
    camera.location = target + direction * distance
    rotation = (target - camera.location).to_track_quat("-Z", "Y")
    camera.rotation_euler = rotation.to_euler()
    horizontal_axis = rotation @ Vector((1.0, 0.0, 0.0))
    vertical_axis = rotation @ Vector((0.0, 1.0, 0.0))
    corners = _bbox_corners(minimum, maximum)
    width = max((corner - target).dot(horizontal_axis) for corner in corners) - min((corner - target).dot(horizontal_axis) for corner in corners)
    height = max((corner - target).dot(vertical_axis) for corner in corners) - min((corner - target).dot(vertical_axis) for corner in corners)
    camera.data.ortho_scale = max(width, height) * CAMERA_MARGIN


def _create_cameras(
    collection: bpy.types.Collection,
    minimum: Vector,
    maximum: Vector,
) -> list[bpy.types.Object]:
    top = _new_camera(collection, "CAM-TOP")
    center = (minimum + maximum) * 0.5
    span_x, span_y = maximum.x - minimum.x, maximum.y - minimum.y
    top.location = (center.x, center.y, maximum.z + max(10.0, max(span_x, span_y) * 2.0))
    top.rotation_euler = (0.0, 0.0, 0.0)
    top.data.ortho_scale = max(span_x, span_y) * CAMERA_MARGIN
    ne = _new_camera(collection, "CAM-NE")
    nw = _new_camera(collection, "CAM-NW")
    _frame_iso(ne, minimum, maximum, Vector((1.0, 1.0, 0.82)))
    _frame_iso(nw, minimum, maximum, Vector((-1.0, 1.0, 0.82)))
    return [top, ne, nw]


def _render(scene: bpy.types.Scene, camera: bpy.types.Object, path: Path) -> None:
    scene.camera = camera
    scene.render.filepath = os.fspath(path)
    bpy.ops.render.render(write_still=True)
    if not path.is_file() or path.stat().st_size <= 8:
        _fail(f"render missing or empty: {path.name}")


def _section_intervals(
    wall: Mapping[str, Any],
    openings: Sequence[Mapping[str, Any]],
    cut_z: float,
) -> list[tuple[float, float]]:
    length = math.dist(wall["centerline_m"][0], wall["centerline_m"][1])
    voids = sorted(
        (
            project_opening(wall, opening)["start_m"],
            project_opening(wall, opening)["end_m"],
        )
        for opening in openings
        if float(opening["sill_m"]) < cut_z < float(opening["head_m"])
    )
    result: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in voids:
        if start > cursor:
            result.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        result.append((cursor, length))
    return result


def _top_section_proxies(
    collection: bpy.types.Collection,
    bundle: Mapping[str, Any],
) -> list[bpy.types.Object]:
    proxies: list[bpy.types.Object] = []
    cut_z = 1.20
    for wall in bundle["wall_branch_graph"]["walls"]:
        a = Vector(wall["centerline_m"][0])
        b = Vector(wall["centerline_m"][1])
        tangent = (b - a).normalized()
        openings = openings_for_wall(bundle, wall["id"])
        for index, (start, end) in enumerate(_section_intervals(wall, openings, cut_z)):
            temporary_wall = {
                **wall,
                "id": f"{wall['id']}-section-{index}",
                "centerline_m": [list(a + tangent * start), list(a + tangent * end)],
                "base_m": 0.01,
                "height_m": 0.07,
            }
            payload = wall_mesh(temporary_wall, [])
            proxy = _mesh_object(
                collection,
                f"QA-SECTION-WALL-{stable_token(wall['id'])}-{index:03d}",
                payload,
                color=(0.20, 0.22, 0.24, 1.0),
                properties={"qa_temporary": True},
            )
            proxies.append(proxy)
    return proxies


def _validate_png(path: Path) -> dict[str, int]:
    with path.open("rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            _fail(f"invalid PNG signature: {path.name}")
    image = bpy.data.images.load(os.fspath(path), check_existing=False)
    try:
        width, height = image.size
        if width != RENDER_SIZE or height != RENDER_SIZE:
            _fail(f"{path.name}: expected {RENDER_SIZE}x{RENDER_SIZE}, got {width}x{height}")
        return {"width": width, "height": height, "bytes": path.stat().st_size}
    finally:
        bpy.data.images.remove(image)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def _script_argv() -> list[str]:
    if "--" not in sys.argv:
        _fail("missing Blender argument separator '--'")
    return sys.argv[sys.argv.index("--") + 1 :]


def build(bundle_path: Path, output: Path) -> dict[str, Any]:
    bundle_path = bundle_path.expanduser().resolve()
    output = output.expanduser().resolve()
    bundle = _load_bundle(bundle_path)
    if not output.is_dir():
        _fail(f"output directory does not exist: {output}")
    known_outputs = [
        output / name
        for name in (
            "scene.blend",
            "scene.glb",
            "top.png",
            "north-east.png",
            "north-west.png",
            "model-report.json",
        )
    ]
    existing = [path.name for path in known_outputs if path.exists()]
    if existing:
        _fail(f"refusing to overwrite builder artifacts: {existing}")

    _assert_factory_startup()
    collection = _clear_factory()
    scene = bpy.context.scene
    _configure_scene(scene, bundle)
    floor_payload = floor_mesh(
        bundle["outer_boundary_m"],
        float(bundle["assumptions"]["floor_slab_thickness_m"]),
    )
    floor = _mesh_object(
        collection,
        "GEO-FLOOR",
        floor_payload,
        color=(0.74, 0.72, 0.66, 1.0),
        properties={
            "id": "floor",
            "kind": "floor_slab",
            "research_only": True,
            "structure_hash": bundle["structure_hash"],
        },
    )

    wall_objects: list[bpy.types.Object] = []
    wall_reports: list[dict[str, Any]] = []
    wall_by_id = {wall["id"]: wall for wall in bundle["wall_branch_graph"]["walls"]}
    for wall in bundle["wall_branch_graph"]["walls"]:
        openings = openings_for_wall(bundle, wall["id"])
        payload = wall_mesh(wall, openings)
        obj = _mesh_object(
            collection,
            f"GEO-WALL-{stable_token(wall['id'])}",
            payload,
            color=(0.48, 0.51, 0.54, 1.0),
            properties={
                "id": wall["id"],
                "kind": "wall_branch",
                "source": wall["source"],
                "left_space_id": wall["left_space_id"],
                "right_space_id": wall["right_space_id"],
                "thickness_m": wall["thickness_m"],
                "base_m": wall["base_m"],
                "height_m": wall["height_m"],
                "research_only": True,
                "structure_hash": bundle["structure_hash"],
            },
        )
        wall_objects.append(obj)
        wall_reports.append(
            {
                "id": wall["id"],
                "name": obj.name,
                "vertices": len(obj.data.vertices),
                "faces": len(obj.data.polygons),
                "occupied_cells": payload["occupied_cells"],
                "non_manifold_edges": payload["non_manifold_edges"],
                "modifier_count": len(obj.modifiers),
                "opening_cuts": payload["opening_cuts"],
            }
        )
    opening_objects, space_objects = _semantic_objects(collection, bundle, wall_by_id)
    geometry = [floor, *wall_objects]
    minimum, maximum = _world_bbox(geometry)
    cameras = _create_cameras(collection, minimum, maximum)

    # Top evidence is a 1.20m plan section so headers do not hide door/window
    # voids.  Temporary QA geometry is deleted before saving/exporting.
    proxies = _top_section_proxies(collection, bundle)
    for wall in wall_objects:
        wall.hide_render = True
    _render(scene, cameras[0], output / "top.png")
    for wall in wall_objects:
        wall.hide_render = False
    for proxy in proxies:
        bpy.data.objects.remove(proxy, do_unlink=True)
    _render(scene, cameras[1], output / "north-east.png")
    _render(scene, cameras[2], output / "north-west.png")

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    export_objects = [*geometry, *opening_objects, *space_objects]
    for obj in export_objects:
        obj.select_set(True)
    if export_objects:
        bpy.context.view_layer.objects.active = export_objects[0]
    glb_path = output / "scene.glb"
    bpy.ops.export_scene.gltf(
        filepath=os.fspath(glb_path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="NONE",
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        export_extras=True,
        export_yup=True,
    )
    if not glb_path.is_file() or glb_path.stat().st_size <= 0:
        _fail("scene.glb was not exported")

    # Workbench and the glTF exporter may create zero-user temporary material
    # datablocks even when no object has a material.  The structural graymodel
    # contract is stricter: remove them before saving the engineering source.
    for obj in [item for item in bpy.data.objects if item.type == "MESH" and item.data is not None]:
        obj.data.materials.clear()
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    scene.camera = cameras[0]
    scene.render.filepath = "//top.png"
    blend_path = output / "scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=os.fspath(blend_path), check_existing=False)
    if not blend_path.is_file() or blend_path.stat().st_size <= 0:
        _fail("scene.blend was not saved")

    renders = {
        name: _validate_png(output / name)
        for name in ("top.png", "north-east.png", "north-west.png")
    }
    report = {
        "schema": "research-model-report-v1",
        "status": "built_pending_cold_verification",
        "source_hash": bundle["source_hash"],
        "structure_hash": bundle["structure_hash"],
        "generator": "tools/fastloop_research/blender_builder.py",
        "blender_version": bpy.app.version_string,
        "coordinate_system": {"units": "metres", "up_axis": "Z"},
        "method": "wall-axis-height-occupancy-grid; no Boolean modifiers",
        "counts": {
            "floor": 1,
            "wall_branches": len(wall_objects),
            "opening_semantics": len(opening_objects),
            "space_semantics": len(space_objects),
            "cameras": len(cameras),
            "materials": len(bpy.data.materials),
            "lights": len([obj for obj in bpy.data.objects if obj.type == "LIGHT"]),
        },
        "wall_branches": wall_reports,
        "cameras": [
            {
                "name": camera.name,
                "type": camera.data.type,
                "location_m": [round(float(value), 6) for value in camera.location],
                "rotation_euler_rad": [round(float(value), 8) for value in camera.rotation_euler],
                "ortho_scale_m": round(float(camera.data.ortho_scale), 6),
            }
            for camera in cameras
        ],
        "renders": renders,
        "bbox_m": {
            "min": [round(float(value), 6) for value in minimum],
            "max": [round(float(value), 6) for value in maximum],
        },
        "unresolved_issue_count": len(bundle["unresolved_issues"]),
    }
    _write_json(output / "model-report.json", report)
    return report


def main() -> int:
    args = _parse_args(_script_argv())
    result = build(args.bundle, args.output)
    print(json.dumps({"ok": True, "report": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise
