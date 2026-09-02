"""Build one isolated, full-height XY opening-gap research variant in Blender."""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.blender_research_wall_layer import file_sha256, wall_box_geometry

DEFAULT_SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
DEFAULT_PLANS = ROOT / "reports/opening_gap_variant_plans_20260902/plans.json"
BLENDER_EXE = Path(r"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")
EXPECTED_IDS = ("OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010")
EXPECTED_EXCLUDED = ("OP005", "OP011", "PORTAL-WB011-WB006-01", "OP012")


def _load(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return json.loads(Path(value).read_text(encoding="utf-8"))


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_plan_bundle_for_blender(value: Mapping[str, Any]) -> dict[str, Any]:
    plans = dict(value)
    if plans.get("schema") != "opening-gap-variant-plans-v2":
        raise ValueError("gap plan schema drift")
    if tuple(plans.get("opening_ids", ())) != EXPECTED_IDS or tuple(plans.get("excluded_opening_ids", ())) != EXPECTED_EXCLUDED:
        raise ValueError("gap plan coverage drift")
    for key in ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if plans.get(key) is not False:
            raise ValueError("gap plan bundle was promoted")
    if plans.get("score_effect") != "none":
        raise ValueError("gap plan score drift")
    payload = {key: item for key, item in plans.items() if key != "candidate_hash"}
    if plans.get("candidate_hash") != _canonical_hash(payload):
        raise ValueError("gap plan candidate hash drift")
    for plan in plans.get("plans", []):
        payload = {key: item for key, item in plan.items() if key != "variant_hash"}
        if plan.get("variant_hash") != _canonical_hash(payload):
            raise ValueError("gap plan variant hash drift")
        if any(plan.get(key) is not False for key in ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready")):
            raise ValueError("gap plan variant was promoted")
    return plans


def build_piece_specs(
    source: str | Path | Mapping[str, Any],
    plans: str | Path | Mapping[str, Any],
    opening_id: str,
    wall_height: float = 2.8,
) -> dict[str, Any]:
    document = validate_v21_document(_load(source))
    plan_bundle = validate_plan_bundle_for_blender(_load(plans))
    try:
        plan = next(item for item in plan_bundle["plans"] if item["opening_id"] == opening_id)
    except StopIteration as exc:
        raise ValueError(f"opening plan unavailable: {opening_id}") from exc
    if opening_id not in plan_bundle["opening_ids"] or opening_id in plan_bundle["excluded_opening_ids"]:
        raise ValueError("opening is not admitted to an isolated XY experiment")

    atoms = {atom["id"]: atom for atom in document["wall_graph"]["atoms"]}
    host_id = plan["host_atom_id"]
    if host_id not in atoms:
        raise ValueError("gap host is absent from source wall atoms")
    specs = []
    for atom in document["wall_graph"]["atoms"]:
        if atom["id"] == host_id:
            continue
        vertices, faces = wall_box_geometry(atom, wall_height)
        specs.append(
            {
                "name": "GEO-WALL-" + atom["id"],
                "source_atom_id": atom["id"],
                "is_gap_host_piece": False,
                "host_parameter_interval": [0.0, 1.0],
                "centerline_m": atom["centerline_m"],
                "thickness_m": float(atom["thickness_m"]),
                "vertices": vertices,
                "faces": faces,
            }
        )
    for index, piece in enumerate(plan["remaining_host_pieces"], start=1):
        atom = {"centerline_m": piece["centerline_m"], "thickness_m": atoms[host_id]["thickness_m"]}
        vertices, faces = wall_box_geometry(atom, wall_height)
        specs.append(
            {
                "name": f"GEO-WALL-{host_id}-P{index:02d}",
                "source_atom_id": host_id,
                "is_gap_host_piece": True,
                "host_parameter_interval": piece["host_parameter_interval"],
                "centerline_m": piece["centerline_m"],
                "thickness_m": float(atoms[host_id]["thickness_m"]),
                "vertices": vertices,
                "faces": faces,
            }
        )
    if len(specs) != plan["expected_wall_object_count"]:
        raise ValueError("gap wall-piece count differs from plan")
    counts = Counter(spec["source_atom_id"] for spec in specs)
    if any(counts[atom_id] != 1 for atom_id in atoms if atom_id != host_id) or counts[host_id] != len(plan["remaining_host_pieces"]):
        raise ValueError("gap variant changed a non-host atom or lost a host piece")
    return {
        "schema": "opening-gap-piece-specs-v1",
        "opening_id": opening_id,
        "variant_id": plan["variant_id"],
        "variant_hash": plan["variant_hash"],
        "plan_bundle_candidate_hash": plan_bundle["candidate_hash"],
        "source_structure_hash": document["structure_hash"],
        "host_atom_id": host_id,
        "gap_host_parameter_interval": sorted(plan["host_parameters"]),
        "source_gap_segment_m": plan["source_gap_segment_m"],
        "projected_gap_segment_m": plan["projected_segment_m"],
        "gap_width_m": plan["projected_width_m"],
        "projection_mode": plan["projection_mode"],
        "wall_height_m": float(wall_height),
        "pieces": specs,
        "expected_wall_object_count": plan["expected_wall_object_count"],
        "research_only": True,
        "one_opening_only": True,
        "gap_z_policy": "full_height_visualization_only",
        "cut_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
    }


def _artifact(path: Path, kind: str) -> dict[str, Any]:
    return {"kind": kind, "path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": file_sha256(path)}


def run(plans: str | Path, source: str | Path, opening_id: str, out: str | Path, wall_height: float = 2.8) -> dict[str, Any]:
    import bpy
    from mathutils import Vector

    plans_path, source_path, out = Path(plans).resolve(), Path(source).resolve(), Path(out).resolve()
    specs = build_piece_specs(source_path, plans_path, opening_id, wall_height)
    out.mkdir(parents=True, exist_ok=True)
    branch_id = f"1308-gap-{opening_id}-v001"
    collection_name = f"COL-1308-GAP-{opening_id}-v001"
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.unit_settings.scale_length = 1.0
    scene["goal_loop_branch_id"] = branch_id
    scene["research_only"] = True
    scene["not_for_construction"] = True
    scene["formal_build_authorized"] = False
    scene["xy_gap_opening_id"] = opening_id
    scene["xy_gap_semantics_confirmed"] = False

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

    meta = bpy.data.objects.new(f"META-1308-GAP-{opening_id}", None)
    collection.objects.link(meta)
    for key, value in {
        "goal_loop_branch_id": branch_id,
        "goal_loop_role": "research_gap_metadata",
        "opening_id": opening_id,
        "variant_hash": specs["variant_hash"],
        "plan_bundle_candidate_hash": specs["plan_bundle_candidate_hash"],
        "source_structure_hash": specs["source_structure_hash"],
        "gap_z_policy": "full_height_visualization_only",
        "research_only": True,
        "not_for_construction": True,
        "build_authorized": False,
    }.items():
        meta[key] = value

    checkpoint = out / f"1308_{opening_id}_gap_variant_checkpoint_v000.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint))
    material = bpy.data.materials.get("MAT-1308-GAP-RESEARCH-GRAY") or bpy.data.materials.new("MAT-1308-GAP-RESEARCH-GRAY")
    material.diffuse_color = (0.52, 0.55, 0.58, 1.0)
    material.metallic = 0.0
    material.roughness = 0.82

    walls = []
    for spec in specs["pieces"]:
        mesh = bpy.data.meshes.new(spec["name"] + "-MESH")
        mesh.from_pydata(spec["vertices"], [], spec["faces"])
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new(spec["name"], mesh)
        collection.objects.link(obj)
        obj.data.materials.append(material)
        obj["goal_loop_branch_id"] = branch_id
        obj["goal_loop_role"] = "wall_piece"
        obj["source_atom_id"] = spec["source_atom_id"]
        obj["is_gap_host_piece"] = spec["is_gap_host_piece"]
        obj["host_parameter_interval"] = json.dumps(spec["host_parameter_interval"])
        obj["opening_id"] = opening_id
        obj["research_only"] = True
        obj["not_for_construction"] = True
        obj["build_authorized"] = False
        walls.append(obj)

    all_points = [obj.matrix_world @ vertex.co for obj in walls for vertex in obj.data.vertices]
    mins = Vector((min(point.x for point in all_points), min(point.y for point in all_points), min(point.z for point in all_points)))
    maxs = Vector((max(point.x for point in all_points), max(point.y for point in all_points), max(point.z for point in all_points)))
    center = (mins + maxs) * 0.5
    span = max(maxs.x - mins.x, maxs.y - mins.y)
    gap_mid = Vector(((specs["projected_gap_segment_m"][0][0] + specs["projected_gap_segment_m"][1][0]) / 2, (specs["projected_gap_segment_m"][0][1] + specs["projected_gap_segment_m"][1][1]) / 2, 0))
    closeup_scale = max(2.4, specs["gap_width_m"] * 2.5)

    def camera(name: str, location, target, scale: float):
        data = bpy.data.cameras.new(name)
        data.type = "ORTHO"
        data.ortho_scale = float(scale)
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        obj.location = Vector(location)
        obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()
        obj["goal_loop_branch_id"] = branch_id
        obj["goal_loop_role"] = "validation_camera"
        return obj

    cameras = {
        "top": camera(f"CAM-1308-{opening_id}-TOP", (center.x, center.y, maxs.z + span * 2), (center.x, center.y, 0), span * 1.1),
        "northeast": camera(f"CAM-1308-{opening_id}-NE", (center.x + span * 1.5, center.y + span * 1.5, maxs.z + span * 1.35), (center.x, center.y, 1.0), span * 1.32),
        "closeup_top": camera(f"CAM-1308-{opening_id}-CLOSEUP-TOP", (gap_mid.x, gap_mid.y, maxs.z + 8), (gap_mid.x, gap_mid.y, 0), closeup_scale),
    }

    source_counts = Counter(obj["source_atom_id"] for obj in walls)
    expected_host_piece_count = sum(spec["is_gap_host_piece"] for spec in specs["pieces"])
    gap_lo, gap_hi = specs["gap_host_parameter_interval"]
    overlap_errors = []
    for obj in walls:
        if obj["source_atom_id"] == specs["host_atom_id"]:
            start, end = json.loads(obj["host_parameter_interval"])
            if not (end <= gap_lo + 1e-9 or start >= gap_hi - 1e-9):
                overlap_errors.append(obj.name)
    non_host_count_errors = [atom_id for atom_id, count in source_counts.items() if atom_id != specs["host_atom_id"] and count != 1]
    topology_errors = [obj.name for obj in walls if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6]
    validation = {
        "schema": "blender-opening-gap-variant-validation-v2",
        "branch_id": branch_id,
        "opening_id": opening_id,
        "variant_hash": specs["variant_hash"],
        "plan_bundle_candidate_hash": specs["plan_bundle_candidate_hash"],
        "source_structure_hash": specs["source_structure_hash"],
        "source_document_sha256": file_sha256(source_path),
        "plans_file_sha256": file_sha256(plans_path),
        "expected_wall_piece_count": specs["expected_wall_object_count"],
        "actual_wall_piece_count": len(walls),
        "expected_host_piece_count": expected_host_piece_count,
        "actual_host_piece_count": source_counts[specs["host_atom_id"]],
        "non_host_atom_count_errors": non_host_count_errors,
        "topology_errors": topology_errors,
        "gap_overlap_errors": overlap_errors,
        "gap_host_parameter_interval": specs["gap_host_parameter_interval"],
        "source_gap_segment_m": specs["source_gap_segment_m"],
        "projected_gap_segment_m": specs["projected_gap_segment_m"],
        "gap_width_m": specs["gap_width_m"],
        "gap_center_m": [gap_mid.x, gap_mid.y],
        "closeup_camera_ortho_scale_m": closeup_scale,
        "closeup_render_resolution_px": [1200, 1200],
        "projection_mode": specs["projection_mode"],
        "wall_height_m": float(wall_height),
        "hidden_startup_objects": hidden_startup,
        "opening_elements": 0,
        "one_opening_only": True,
        "gap_z_policy": "full_height_visualization_only",
        "xy_experiment_confirmation": False,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "research_only": True,
        "not_for_construction": True,
        "pass": len(walls) == specs["expected_wall_object_count"] and source_counts[specs["host_atom_id"]] == expected_host_piece_count and not non_host_count_errors and not topology_errors and not overlap_errors,
    }
    validation_path = out / "validation.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not validation["pass"]:
        raise RuntimeError("gap variant validation failed")

    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 1200
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.show_specular_highlight = False
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.96, 0.96, 0.96)
    render_artifacts = []
    for key, cam in cameras.items():
        scene.camera = cam
        path = out / f"1308_{opening_id}_gap_variant_{key}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_artifacts.append(_artifact(path, f"render_{key}"))

    blend = out / f"1308_{opening_id}_gap_variant_v001.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.object.select_all(action="DESELECT")
    for obj in walls:
        obj.select_set(True)
    meta.select_set(True)
    bpy.context.view_layer.objects.active = walls[0]
    glb = out / f"1308_{opening_id}_gap_variant_v001.glb"
    bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True, export_apply=True, export_materials="EXPORT", export_yup=True, export_animations=False, export_normals=True, export_cameras=False, export_lights=False, export_extras=True)
    manifest = {
        "schema": "blender-opening-gap-variant-artifact-manifest-v1",
        "opening_id": opening_id,
        "branch_id": branch_id,
        "variant_hash": specs["variant_hash"],
        "source_structure_hash": specs["source_structure_hash"],
        "wall_piece_count": len(walls),
        "opening_elements": 0,
        "gap_center_m": [gap_mid.x, gap_mid.y],
        "closeup_camera_ortho_scale_m": closeup_scale,
        "closeup_render_resolution_px": [1200, 1200],
        "artifacts": [_artifact(checkpoint, "checkpoint_blend"), _artifact(blend, "blender_source"), _artifact(glb, "portable_glb"), *render_artifacts, _artifact(validation_path, "validation")],
        "one_opening_only": True,
        "gap_z_policy": "full_height_visualization_only",
        "xy_experiment_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "research_only": True,
        "not_for_construction": True,
    }
    manifest_path = out / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "gap_variant_complete", "opening_id": opening_id, "manifest": str(manifest_path), "validation_pass": True, "wall_pieces": len(walls)}))
    return manifest


def _argv(argv: Sequence[str] | None = None) -> list[str]:
    if argv is not None:
        return list(argv)
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1 :]
    return sys.argv[1:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wall-height", type=float, default=2.8)
    args = parser.parse_args(_argv(argv))
    run(args.plans, args.source, args.opening_id, args.out, args.wall_height)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_piece_specs", "run", "validate_plan_bundle_for_blender", "BLENDER_EXE"]
