"""Build a generic intact-wall, two-guide vertical research display in Blender."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
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
BLENDER_EXE = Path(r"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")
FAIL_CLOSED = (
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "root_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _load(value: str | Path | Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value)), None
    path = Path(value)
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _offset_geometry(
    centerline_m: list[list[float]],
    thickness_m: float,
    z_min_m: float,
    z_max_m: float,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    vertices, faces = wall_box_geometry(
        {"centerline_m": centerline_m, "thickness_m": thickness_m},
        z_max_m - z_min_m,
    )
    return [(x, y, z + z_min_m) for x, y, z in vertices], faces


def validate_plan_for_blender(
    value: Mapping[str, Any],
    *,
    document: Mapping[str, Any],
    source_sha256: str | None,
) -> dict[str, Any]:
    plan = deepcopy(dict(value))
    payload = {key: item for key, item in plan.items() if key != "candidate_hash"}
    if (
        plan.get("schema") != "opening-vertical-display-plan-v1"
        or not isinstance(plan.get("opening_id"), str)
        or not isinstance(plan.get("branch_id"), str)
        or plan.get("candidate_hash") != _canonical_hash(payload)
        or plan.get("source_structure_hash") != document["structure_hash"]
    ):
        raise ValueError("generic Blender vertical plan identity/hash drift")
    if source_sha256 is not None and plan.get("source_document_sha256") != source_sha256:
        raise ValueError("generic Blender vertical source drift")
    for key in FAIL_CLOSED:
        if plan.get(key) is not False:
            raise ValueError(f"generic Blender vertical plan promoted {key}")
    if (
        plan.get("score_effect") != "none"
        or plan.get("research_only") is not True
        or plan.get("not_for_construction") is not True
        or plan.get("baseline") != {
            "source_wall_atom_count": 35,
            "intact_source_wall_count": 35,
            "opening_cuts": 0,
        }
        or plan.get("guide_object_count") != 2
        or plan["vertical_assumptions"]["head_guide_m"]["binding"] != "unbound_research_default"
        or plan["vertical_assumptions"]["sill_m"]["value"] is not None
        or any(
            plan.get(key) is not False
            for key in (
                "opening_geometry_created",
                "floor_cut_created",
                "sill_geometry_created",
                "door_leaf_created",
                "lintel_structural_element_created",
                "ifc_opening_created",
                "ifc_void_or_fill_created",
            )
        )
    ):
        raise ValueError("generic Blender vertical plan policy drift")
    guides = plan.get("guide_specs")
    if (
        not isinstance(guides, list)
        or len(guides) != 2
        or [guide.get("role") for guide in guides]
        != ["nonsemantic_xy_locator", "nonsemantic_unbound_head_guide"]
        or any(guide.get("opening_geometry") is not False or guide.get("source_fact") is not False for guide in guides)
    ):
        raise ValueError("generic Blender vertical guide drift")
    if len(document["wall_graph"]["atoms"]) != 35:
        raise ValueError("generic Blender vertical source wall count drift")
    return plan


def build_specs(
    source: str | Path | Mapping[str, Any],
    plan: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    document_raw, source_sha256 = _load(source)
    document = validate_v21_document(document_raw)
    plan_raw, plan_sha256 = _load(plan)
    display = validate_plan_for_blender(
        plan_raw,
        document=document,
        source_sha256=source_sha256,
    )
    wall_height = float(display["vertical_assumptions"]["wall_height_m"]["value"])
    walls = []
    for atom in document["wall_graph"]["atoms"]:
        vertices, faces = wall_box_geometry(atom, wall_height)
        walls.append(
            {
                "name": "GEO-WALL-" + atom["id"],
                "source_atom_id": atom["id"],
                "centerline_m": deepcopy(atom["centerline_m"]),
                "thickness_m": float(atom["thickness_m"]),
                "vertices": vertices,
                "faces": faces,
            }
        )
    guides = []
    for guide in display["guide_specs"]:
        vertices, faces = _offset_geometry(
            guide["centerline_m"],
            float(guide["xy_thickness_m"]),
            float(guide["z_min_m"]),
            float(guide["z_max_m"]),
        )
        guides.append({**deepcopy(guide), "vertices": vertices, "faces": faces})
    return {
        "schema": "opening-vertical-display-specs-v1",
        "opening_id": display["opening_id"],
        "branch_id": display["branch_id"],
        "plan_candidate_hash": display["candidate_hash"],
        "plan_file_sha256": plan_sha256,
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": source_sha256 or display["source_document_sha256"],
        "wall_height_m": wall_height,
        "head_guide_m": float(display["vertical_assumptions"]["head_guide_m"]["value"]),
        "head_guide_binding": "unbound_research_default",
        "sill_m": None,
        "source_segment_m": deepcopy(display["xy_binding"]["segment_m"]),
        "display_face_normal_xy": deepcopy(display["xy_binding"]["display_face_normal_xy"]),
        "walls": walls,
        "guides": guides,
        "labels": deepcopy(display["labels"]),
        "forbidden_object_roles": deepcopy(display["forbidden_object_roles"]),
    }


def _artifact(path: Path, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "relative_path": path.name,
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def run(source: str | Path, plan: str | Path, out: str | Path) -> dict[str, Any]:
    import bpy
    from mathutils import Vector

    source_path = Path(source).resolve()
    plan_path = Path(plan).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    specs = build_specs(source_path, plan_path)
    opening_id = specs["opening_id"]
    branch_id = specs["branch_id"]
    collection_name = f"COL-1308-{opening_id}-LAYER3B-VERTICAL-RESEARCH-v001"
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.unit_settings.scale_length = 1.0
    for key, value in {
        "goal_loop_branch_id": branch_id,
        "research_only": True,
        "not_for_construction": True,
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "opening_geometry_authorized": False,
        "build_authorized": False,
        "ready": False,
        "score_effect": "none",
    }.items():
        scene[key] = value
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)
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
    meta = bpy.data.objects.new(f"META-1308-{opening_id}-LAYER3B-VERTICAL-RESEARCH", None)
    collection.objects.link(meta)
    for key, value in {
        "goal_loop_branch_id": branch_id,
        "goal_loop_role": "vertical_research_metadata",
        "opening_id": opening_id,
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "wall_height_m": specs["wall_height_m"],
        "head_guide_m": specs["head_guide_m"],
        "head_guide_binding": specs["head_guide_binding"],
        "sill_state": "unknown_not_authorized",
        "opening_cuts": 0,
        "opening_geometry_authorized": False,
        "research_only": True,
        "not_for_construction": True,
        "build_authorized": False,
    }.items():
        meta[key] = value
    checkpoint = out / f"1308_{opening_id}_layer3b_checkpoint_v000.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint))
    wall_material = bpy.data.materials.new(f"MAT-1308-{opening_id}-L3B-WALL-GRAY")
    wall_material.diffuse_color = (0.52, 0.55, 0.58, 1.0)
    wall_material.roughness = 0.82
    guide_materials = []
    for name, color in (
        (f"MAT-1308-{opening_id}-L3B-XY-BLUE", (0.02, 0.30, 0.95, 1.0)),
        (f"MAT-1308-{opening_id}-L3B-HEAD-ORANGE", (1.0, 0.28, 0.03, 1.0)),
    ):
        material = bpy.data.materials.new(name)
        material.diffuse_color = color
        material.roughness = 0.5
        guide_materials.append(material)
    walls = []
    for spec in specs["walls"]:
        mesh = bpy.data.meshes.new(spec["name"] + "-MESH")
        mesh.from_pydata(spec["vertices"], [], spec["faces"])
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new(spec["name"], mesh)
        collection.objects.link(obj)
        obj.data.materials.append(wall_material)
        for key, value in {
            "goal_loop_branch_id": branch_id,
            "goal_loop_role": "intact_source_wall",
            "source_atom_id": spec["source_atom_id"],
            "opening_cut": False,
            "research_only": True,
            "not_for_construction": True,
            "build_authorized": False,
        }.items():
            obj[key] = value
        walls.append(obj)
    guides = []
    for index, spec in enumerate(specs["guides"]):
        mesh = bpy.data.meshes.new(spec["object_name"] + "-MESH")
        mesh.from_pydata(spec["vertices"], [], spec["faces"])
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new(spec["object_name"], mesh)
        collection.objects.link(obj)
        obj.data.materials.append(guide_materials[index])
        for key, value in {
            "goal_loop_branch_id": branch_id,
            "goal_loop_role": spec["role"],
            "opening_id": opening_id,
            "source_fact": False,
            "opening_geometry": False,
            "research_only": True,
            "not_for_construction": True,
            "build_authorized": False,
        }.items():
            obj[key] = value
        guides.append(obj)
    all_points = [obj.matrix_world @ vertex.co for obj in walls for vertex in obj.data.vertices]
    mins = Vector((min(p.x for p in all_points), min(p.y for p in all_points), min(p.z for p in all_points)))
    maxs = Vector((max(p.x for p in all_points), max(p.y for p in all_points), max(p.z for p in all_points)))
    center = (mins + maxs) * 0.5
    span = max(maxs.x - mins.x, maxs.y - mins.y)
    segment_mid = Vector((
        sum(point[0] for point in specs["source_segment_m"]) / 2.0,
        sum(point[1] for point in specs["source_segment_m"]) / 2.0,
        specs["wall_height_m"] / 2.0,
    ))
    normal = specs["display_face_normal_xy"]

    def camera(name: str, location: Sequence[float], target: Sequence[float], scale: float):
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
        "top": camera(f"CAM-1308-{opening_id}-L3B-TOP", (center.x, center.y, maxs.z + span * 2), (center.x, center.y, 0), span * 1.1),
        "northeast": camera(f"CAM-1308-{opening_id}-L3B-NE", (center.x + span * 1.5, center.y + span * 1.5, maxs.z + span * 1.35), (center.x, center.y, 1.0), span * 1.32),
        "front_closeup": camera(
            f"CAM-1308-{opening_id}-L3B-FRONT",
            (segment_mid.x + normal[0] * 0.6, segment_mid.y + normal[1] * 0.6, segment_mid.z),
            tuple(segment_mid),
            3.4,
        ),
    }
    cameras["front_closeup"].data.clip_start = 0.01
    counts = Counter(obj["source_atom_id"] for obj in walls)
    wall_errors = [obj.name for obj in walls if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6 or obj.get("opening_cut") is not False]
    guide_errors = [obj.name for obj in guides if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6 or obj.get("opening_geometry") is not False or obj.get("source_fact") is not False]
    forbidden = [obj.name for obj in collection.objects if obj.get("goal_loop_role") in specs["forbidden_object_roles"]]
    validation = {
        "schema": "blender-opening-layer3b-vertical-research-validation-v1",
        "opening_id": opening_id,
        "branch_id": branch_id,
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "plan_file_sha256": specs["plan_file_sha256"],
        "source_structure_hash": specs["source_structure_hash"],
        "source_document_sha256": specs["source_document_sha256"],
        "expected_wall_count": 35,
        "actual_wall_count": len(walls),
        "distinct_source_atom_count": len(counts),
        "duplicate_source_atom_ids": sorted(key for key, value in counts.items() if value != 1),
        "wall_errors": wall_errors,
        "expected_guide_count": 2,
        "actual_guide_count": len(guides),
        "guide_roles": [obj.get("goal_loop_role") for obj in guides],
        "guide_errors": guide_errors,
        "forbidden_objects": forbidden,
        "wall_height_m": specs["wall_height_m"],
        "head_guide_m": specs["head_guide_m"],
        "head_guide_binding": "unbound_research_default",
        "sill_m": None,
        "opening_cuts": 0,
        "opening_elements": 0,
        "door_leaf_created": False,
        "ifc_opening_created": False,
        "validation_camera_count": len(cameras),
        "hidden_startup_objects": hidden_startup,
        "labels": specs["labels"],
        "research_only": True,
        "not_for_construction": True,
        **{key: False for key in FAIL_CLOSED},
        "score_effect": "none",
        "pass": len(walls) == 35 and len(counts) == 35 and all(value == 1 for value in counts.values()) and len(guides) == 2 and not wall_errors and not guide_errors and not forbidden,
    }
    validation_path = out / "validation.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not validation["pass"]:
        raise RuntimeError("generic vertical research Blender validation failed")
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
    renders = []
    for key, active_camera in cameras.items():
        scene.camera = active_camera
        path = out / f"1308_{opening_id}_layer3b_{key}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        renders.append(_artifact(path, f"render_{key}"))
    blend = out / f"1308_{opening_id}_layer3b_vertical_research_v001.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.object.select_all(action="DESELECT")
    for obj in [*walls, *guides, meta]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = walls[0]
    glb = out / f"1308_{opening_id}_layer3b_vertical_research_v001.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(glb),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_yup=True,
        export_animations=False,
        export_normals=True,
        export_cameras=False,
        export_lights=False,
        export_extras=True,
    )
    artifacts = [
        _artifact(checkpoint, "checkpoint_blend"),
        _artifact(blend, "blender_source"),
        _artifact(glb, "portable_glb"),
        *renders,
        _artifact(validation_path, "validation"),
    ]
    manifest = {
        "schema": "blender-opening-layer3b-vertical-research-manifest-v1",
        "opening_id": opening_id,
        "branch_id": branch_id,
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "plan_file_sha256": specs["plan_file_sha256"],
        "source_structure_hash": specs["source_structure_hash"],
        "source_document_sha256": specs["source_document_sha256"],
        "wall_count": len(walls),
        "guide_count": len(guides),
        "guide_roles": [obj.get("goal_loop_role") for obj in guides],
        "opening_cuts": 0,
        "opening_elements": 0,
        "head_guide_binding": "unbound_research_default",
        "sill_m": None,
        "artifact_path_mode": "relative_to_manifest",
        "artifacts": artifacts,
        "labels": specs["labels"],
        "research_only": True,
        "not_for_construction": True,
        **{key: False for key in FAIL_CLOSED},
        "score_effect": "none",
    }
    manifest_path = out / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "opening_layer3b_vertical_research_complete", "opening_id": opening_id, "walls": len(walls), "guides": len(guides), "opening_cuts": 0, "manifest": str(manifest_path)}))
    return manifest


def _argv(argv: Sequence[str] | None = None) -> list[str]:
    if argv is not None:
        return list(argv)
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1 :]
    return sys.argv[1:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(_argv(argv))
    run(args.source, args.plan, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BLENDER_EXE", "build_specs", "run", "validate_plan_for_blender"]
