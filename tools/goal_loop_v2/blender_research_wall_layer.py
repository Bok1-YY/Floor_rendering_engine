"""Rebuild the isolated 1308 wall-only research artifact inside Blender."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


DEFAULT_SOURCE_SHA256 = "db4fa7a656b8a0267494d4e299a436503d219ba57f06f168dd5150427f299eb1"
DEFAULT_STRUCTURE_HASH = "700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1"
DEFAULT_BRANCH_ID = "1308-source-wall-layer-v001"
DEFAULT_COLLECTION_NAME = "COL-1308-RESEARCH-GRAY-v001"
DEFAULT_META_NAME = "META-1308-RESEARCH-WALL-LAYER"


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def wall_box_geometry(atom: Mapping[str, Any], height: float) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    (x0, y0), (x1, y1) = atom["centerline_m"]
    x0, y0, x1, y1 = map(float, (x0, y0, x1, y1))
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise ValueError("wall atom has zero length")
    thickness = float(atom["thickness_m"])
    if thickness <= 0 or height <= 0:
        raise ValueError("wall thickness and height must be positive")
    nx, ny = -dy / length * thickness / 2.0, dx / length * thickness / 2.0
    footprint = [
        (x0 + nx, y0 + ny),
        (x1 + nx, y1 + ny),
        (x1 - nx, y1 - ny),
        (x0 - nx, y0 - ny),
    ]
    vertices = [(x, y, z) for z in (0.0, float(height)) for x, y in footprint]
    faces = [
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]
    return vertices, faces


def _task_name(sample_id: str, suffix: str) -> str:
    return f"{sample_id}_{suffix}"


def _artifact(path: Path, kind: str) -> dict[str, Any]:
    return {"kind": kind, "path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": file_sha256(path)}


def run(
    source: str | Path,
    out: str | Path,
    *,
    sample_id: str = "1308",
    branch_id: str = DEFAULT_BRANCH_ID,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    expected_source_sha256: str = DEFAULT_SOURCE_SHA256,
    expected_structure_hash: str = DEFAULT_STRUCTURE_HASH,
    wall_height: float = 2.8,
) -> dict[str, Any]:
    import bpy
    from mathutils import Vector

    source = Path(source).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    if source_hash != expected_source_sha256:
        raise ValueError(f"source sha drift: {source_hash}")
    document = json.loads(raw.decode("utf-8"))
    if document.get("structure_hash") != expected_structure_hash:
        raise ValueError("source structure hash drift")
    atoms = list(document["wall_graph"]["atoms"])
    if len(atoms) != 35 or len({atom["id"] for atom in atoms}) != 35:
        raise ValueError("expected 35 unique wall atoms")

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.unit_settings.scale_length = 1.0
    scene["goal_loop_branch_id"] = branch_id
    scene["research_only"] = True
    scene["not_for_construction"] = True
    scene["formal_build_authorized"] = False
    scene["wall_layer_opening_cuts"] = 0
    scene["wall_height_assumption_m"] = float(wall_height)

    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)
        scene.collection.children.link(collection)
    elif collection not in scene.collection.children:
        scene.collection.children.link(collection)
    for obj in list(collection.objects):
        if obj.get("goal_loop_branch_id") == branch_id:
            bpy.data.objects.remove(obj, do_unlink=True)

    hidden_startup = []
    for name, expected_type in (("Cube", "MESH"), ("Light", "LIGHT"), ("Camera", "CAMERA")):
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == expected_type:
            obj.hide_render = True
            obj.hide_set(True)
            hidden_startup.append(name)

    meta = bpy.data.objects.new(DEFAULT_META_NAME if branch_id == DEFAULT_BRANCH_ID else f"META-{branch_id}", None)
    collection.objects.link(meta)
    meta["goal_loop_branch_id"] = branch_id
    meta["goal_loop_role"] = "research_metadata"
    meta["branch_kind"] = "source_faithful_wall_geometry_research"
    meta["source_structure_hash"] = expected_structure_hash
    meta["source_document_sha256"] = source_hash
    meta["wall_height_m"] = float(wall_height)
    meta["opening_cuts"] = 0
    meta["research_only"] = True
    meta["not_for_construction"] = True
    meta["build_authorized"] = False

    checkpoint = out / _task_name(sample_id, "research_wall_layer_checkpoint_v000.blend")
    bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint))

    material_name = "MAT-1308-RESEARCH-GRAY" if sample_id == "1308" else f"MAT-{sample_id}-RESEARCH-GRAY"
    material = bpy.data.materials.get(material_name) or bpy.data.materials.new(material_name)
    material.diffuse_color = (0.52, 0.55, 0.58, 1.0)
    material.metallic = 0.0
    material.roughness = 0.82

    walls = []
    expected_geometry: dict[str, tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]] = {}
    for atom in atoms:
        name = "GEO-WALL-" + atom["id"]
        vertices, faces = wall_box_geometry(atom, wall_height)
        expected_geometry[atom["id"]] = (vertices, faces)
        mesh = bpy.data.meshes.new(name + "-MESH")
        mesh.from_pydata(vertices, [], faces)
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        obj.data.materials.append(material)
        obj["goal_loop_branch_id"] = branch_id
        obj["goal_loop_role"] = "wall_atom"
        obj["wall_atom_id"] = atom["id"]
        obj["source_p0_m"] = json.dumps(atom["centerline_m"][0])
        obj["source_p1_m"] = json.dumps(atom["centerline_m"][1])
        obj["source_thickness_m"] = float(atom["thickness_m"])
        obj["research_height_m"] = float(wall_height)
        obj["source_structure_hash"] = expected_structure_hash
        obj["research_only"] = True
        obj["not_for_construction"] = True
        obj["build_authorized"] = False
        walls.append(obj)

    all_points = [obj.matrix_world @ vertex.co for obj in walls for vertex in obj.data.vertices]
    mins = Vector((min(p.x for p in all_points), min(p.y for p in all_points), min(p.z for p in all_points)))
    maxs = Vector((max(p.x for p in all_points), max(p.y for p in all_points), max(p.z for p in all_points)))
    center = (mins + maxs) * 0.5
    span_x, span_y, span_z = maxs.x - mins.x, maxs.y - mins.y, maxs.z - mins.z
    span = max(span_x, span_y)

    def make_camera(name: str, location: Sequence[float], target: Sequence[float], scale: float):
        data = bpy.data.cameras.new(name)
        data.type = "ORTHO"
        data.ortho_scale = float(scale)
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        obj.location = Vector(location)
        obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()
        obj["goal_loop_branch_id"] = branch_id
        obj["goal_loop_role"] = "validation_camera"
        obj["research_only"] = True
        return obj

    cameras = {
        "top": make_camera(f"CAM-{sample_id}-TOP-ORTHO", (center.x, center.y, maxs.z + span * 2.0), (center.x, center.y, 0.0), span * 1.10),
        "northeast": make_camera(f"CAM-{sample_id}-NE-AXON", (center.x + span * 1.5, center.y + span * 1.5, maxs.z + span * 1.35), (center.x, center.y, span_z * 0.38), span * 1.32),
        "northwest": make_camera(f"CAM-{sample_id}-NW-AXON", (center.x - span * 1.5, center.y + span * 1.5, maxs.z + span * 1.35), (center.x, center.y, span_z * 0.38), span * 1.32),
    }

    per_atom = []
    geometry_errors = []
    for atom, obj in zip(atoms, walls):
        expected_vertices, _ = expected_geometry[atom["id"]]
        actual_vertices = [tuple(vertex.co) for vertex in obj.data.vertices]
        vertex_error = max(math.dist(actual, expected) for actual, expected in zip(actual_vertices, expected_vertices))
        endpoint_error = max(math.dist(json.loads(obj["source_p0_m"]), atom["centerline_m"][0]), math.dist(json.loads(obj["source_p1_m"]), atom["centerline_m"][1]))
        thickness_error = abs(float(obj["source_thickness_m"]) - float(atom["thickness_m"]))
        height_error = abs(float(obj["research_height_m"]) - float(wall_height))
        length = math.dist(*atom["centerline_m"])
        topology_ok = len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6
        if max(vertex_error, endpoint_error, thickness_error, height_error) > 1e-6 or not topology_ok:
            geometry_errors.append(atom["id"])
        per_atom.append({"atom_id": atom["id"], "object_name": obj.name, "source_length_m": length, "source_thickness_m": float(atom["thickness_m"]), "vertex_error_m": vertex_error, "endpoint_error_m": endpoint_error, "thickness_error_m": thickness_error, "height_error_m": height_error, "vertices": len(obj.data.vertices), "faces": len(obj.data.polygons), "closed_box_topology": topology_ok})

    validation = {
        "schema": "blender-research-wall-layer-validation-v2",
        "branch_id": branch_id,
        "source_structure_hash": expected_structure_hash,
        "source_document_sha256": source_hash,
        "expected_wall_atoms": len(atoms),
        "actual_wall_objects": len(walls),
        "wall_atom_ids": sorted(atom["id"] for atom in atoms),
        "missing_atom_ids": [],
        "extra_atom_ids": [],
        "duplicate_atom_ids": 0,
        "total_vertices": sum(len(obj.data.vertices) for obj in walls),
        "total_faces": sum(len(obj.data.polygons) for obj in walls),
        "world_bbox_m": {"min": list(mins), "max": list(maxs)},
        "tiny_source_atoms_below_0_05m": [row["atom_id"] for row in per_atom if row["source_length_m"] < 0.05],
        "per_atom": per_atom,
        "geometry_errors": geometry_errors,
        "opening_cuts": 0,
        "hidden_startup_objects": hidden_startup,
        "research_only": True,
        "not_for_construction": True,
        "formal_build_authorized": False,
        "pass": len(walls) == len(atoms) and not geometry_errors,
    }
    validation_path = out / "wall_layer_structural_validation.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not validation["pass"]:
        raise RuntimeError("wall layer structural validation failed")

    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 1200
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.show_specular_highlight = False
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.96, 0.96, 0.96)

    render_artifacts = []
    for key, camera in cameras.items():
        scene.camera = camera
        path = out / _task_name(sample_id, f"wall_layer_{key}.png")
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_artifacts.append(_artifact(path, f"render_{key}"))

    final_blend = out / _task_name(sample_id, "research_wall_layer_v001.blend")
    bpy.ops.wm.save_as_mainfile(filepath=str(final_blend))

    bpy.ops.object.select_all(action="DESELECT")
    for obj in walls:
        obj.select_set(True)
    meta.select_set(True)
    bpy.context.view_layer.objects.active = walls[0]
    glb = out / _task_name(sample_id, "research_wall_layer_v001.glb")
    bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True, export_apply=True, export_materials="EXPORT", export_image_format="AUTO", export_yup=True, export_animations=False, export_normals=True, export_cameras=False, export_lights=False, export_extras=True)

    artifacts = [_artifact(checkpoint, "checkpoint_blend"), _artifact(final_blend, "blender_source"), _artifact(glb, "portable_glb"), *render_artifacts, _artifact(validation_path, "structural_validation")]
    artifact_manifest = {
        "schema": "blender-research-wall-layer-artifact-manifest-v1",
        "sample_id": sample_id,
        "branch_id": branch_id,
        "source_structure_hash": expected_structure_hash,
        "source_document_sha256": source_hash,
        "blender_version": bpy.app.version_string,
        "wall_object_count": len(walls),
        "opening_cuts": 0,
        "artifacts": artifacts,
        "research_only": True,
        "not_for_construction": True,
        "formal_build_authorized": False,
    }
    manifest_path = out / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(artifact_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "research_wall_layer_complete", "manifest": str(manifest_path), "validation_pass": True, "wall_objects": len(walls)}))
    return artifact_manifest


def _argv(argv: Sequence[str] | None = None) -> list[str]:
    if argv is not None:
        return list(argv)
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1 :]
    return sys.argv[1:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-id", default="1308")
    parser.add_argument("--branch-id", default=DEFAULT_BRANCH_ID)
    parser.add_argument("--collection-name", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--expected-source-sha256", default=DEFAULT_SOURCE_SHA256)
    parser.add_argument("--expected-structure-hash", default=DEFAULT_STRUCTURE_HASH)
    parser.add_argument("--wall-height", type=float, default=2.8)
    args = parser.parse_args(_argv(argv))
    run(args.source, args.out, sample_id=args.sample_id, branch_id=args.branch_id, collection_name=args.collection_name, expected_source_sha256=args.expected_source_sha256, expected_structure_hash=args.expected_structure_hash, wall_height=args.wall_height)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["wall_box_geometry", "run"]
