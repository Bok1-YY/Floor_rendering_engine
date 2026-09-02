"""Build the no-cut OP002 Layer3B vertical research display in Blender."""
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
DEFAULT_PLAN = ROOT / "reports/op002_vertical_display_plan_20260903/plan.json"
BLENDER_EXE = Path(r"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")
BRANCH_ID = "1308-op002-layer3b-vertical-research-v001"
COLLECTION_NAME = "COL-1308-OP002-LAYER3B-VERTICAL-RESEARCH-v001"
FAIL_CLOSED = (
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "adjacency_confirmation",
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


def _assert_fail_closed(value: Mapping[str, Any], *, context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def validate_display_plan_for_blender(
    value: Mapping[str, Any],
    *,
    document: Mapping[str, Any],
    source_file_sha256: str | None,
) -> dict[str, Any]:
    plan = deepcopy(dict(value))
    payload = {key: item for key, item in plan.items() if key != "candidate_hash"}
    if (
        plan.get("schema") != "op002-vertical-display-plan-v2"
        or plan.get("branch_id") != BRANCH_ID
        or plan.get("branch_kind") != "vertical_parameter_research_display_without_opening_cut"
        or plan.get("candidate_hash") != _canonical_hash(payload)
        or plan.get("source_structure_hash") != document["structure_hash"]
    ):
        raise ValueError("OP002 Blender display plan identity/hash drift")
    if source_file_sha256 is not None and plan.get("source_document_sha256") != source_file_sha256:
        raise ValueError("OP002 Blender display source file drift")
    _assert_fail_closed(plan, context="OP002 Blender display plan")
    if (
        plan.get("baseline", {}).get("source_wall_atom_count") != 35
        or plan["baseline"].get("intact_source_wall_count") != 35
        or plan["baseline"].get("opening_cuts") != 0
        or plan.get("guide_object_count") != 2
        or plan.get("opening_geometry_created") is not False
        or plan.get("floor_cut_created") is not False
        or plan.get("sill_geometry_created") is not False
        or plan.get("door_leaf_created") is not False
        or plan.get("lintel_structural_element_created") is not False
        or plan.get("ifc_opening_created") is not False
        or plan.get("ifc_void_or_fill_created") is not False
        or plan.get("research_only") is not True
        or plan.get("not_for_construction") is not True
    ):
        raise ValueError("OP002 Blender display geometry/policy drift")
    vertical = plan.get("vertical_assumptions", {})
    if (
        vertical.get("wall_height_m", {}).get("value") != 2.8
        or vertical["wall_height_m"].get("provenance_class") != "research_assumption"
        or vertical.get("head_m", {}).get("value") != 2.1
        or vertical["head_m"].get("provenance_class") != "research_assumption"
        or vertical.get("sill_m", {}).get("value") is not None
        or vertical["sill_m"].get("provenance_class") != "unknown"
    ):
        raise ValueError("OP002 Blender display vertical provenance drift")
    guides = plan.get("guide_specs")
    if (
        not isinstance(guides, list)
        or len(guides) != 2
        or [guide.get("role") for guide in guides]
        != ["nonsemantic_xy_locator", "nonsemantic_head_assumption_guide"]
        or any(guide.get("opening_geometry") is not False or guide.get("source_fact") is not False for guide in guides)
        or guides[1].get("z_center_m") != 2.1
        or guides[1].get("z_min_m") != 2.08
        or guides[1].get("z_max_m") != 2.12
        or guides[0].get("z_min_m", 0) <= 2.8
    ):
        raise ValueError("OP002 Blender display guide contract drift")
    atoms = document["wall_graph"]["atoms"]
    if len(atoms) != 35 or len({atom["id"] for atom in atoms}) != 35:
        raise ValueError("OP002 Blender display source wall count drift")
    host_id = plan["op002_xy_binding"]["host_atom_id"]
    if host_id not in {atom["id"] for atom in atoms}:
        raise ValueError("OP002 Blender display host drift")
    return plan


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
    return (
        [(x, y, z + z_min_m) for x, y, z in vertices],
        faces,
    )


def build_display_specs(
    source: str | Path | Mapping[str, Any],
    plan: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    document_raw, source_file_sha256 = _load(source)
    document = validate_v21_document(document_raw)
    plan_raw, plan_file_sha256 = _load(plan)
    display = validate_display_plan_for_blender(
        plan_raw,
        document=document,
        source_file_sha256=source_file_sha256,
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
        guides.append(
            {
                **deepcopy(guide),
                "vertices": vertices,
                "faces": faces,
            }
        )
    if len(walls) != 35 or len(guides) != 2:
        raise ValueError("OP002 Blender display spec count drift")
    return {
        "schema": "op002-vertical-research-display-specs-v1",
        "branch_id": BRANCH_ID,
        "plan_candidate_hash": display["candidate_hash"],
        "plan_file_sha256": plan_file_sha256,
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": source_file_sha256 or display["source_document_sha256"],
        "wall_height_m": wall_height,
        "head_level_m": float(display["vertical_assumptions"]["head_m"]["value"]),
        "sill_level_m": None,
        "host_atom_id": display["op002_xy_binding"]["host_atom_id"],
        "source_segment_m": deepcopy(display["op002_xy_binding"]["source_segment_m"]),
        "display_face_normal_xy": deepcopy(display["op002_xy_binding"]["display_face_normal_xy"]),
        "walls": walls,
        "guides": guides,
        "labels": deepcopy(display["labels"]),
        "forbidden_object_roles": deepcopy(display["forbidden_object_roles"]),
        "opening_cuts": 0,
        "opening_geometry_created": False,
        "door_leaf_created": False,
        "ifc_opening_created": False,
        "research_only": True,
        "not_for_construction": True,
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "adjacency_confirmation": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
    }


def _artifact(path: Path, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "relative_path": path.name,
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def run(
    source: str | Path,
    plan: str | Path,
    out: str | Path,
) -> dict[str, Any]:
    import bpy
    from mathutils import Vector

    source_path = Path(source).resolve()
    plan_path = Path(plan).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    specs = build_display_specs(source_path, plan_path)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.unit_settings.scale_length = 1.0
    scene["goal_loop_branch_id"] = BRANCH_ID
    scene["research_only"] = True
    scene["not_for_construction"] = True
    scene["source_vertical_confirmation"] = False
    scene["source_subtype_confirmation"] = False
    scene["effective_void_confirmation"] = False
    scene["opening_geometry_authorized"] = False
    scene["build_authorized"] = False
    scene["ready"] = False
    scene["score_effect"] = "none"

    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION_NAME)
        scene.collection.children.link(collection)
    elif collection not in scene.collection.children:
        scene.collection.children.link(collection)
    for obj in list(collection.objects):
        if obj.get("goal_loop_branch_id") == BRANCH_ID:
            bpy.data.objects.remove(obj, do_unlink=True)
    hidden_startup = []
    for name, expected_type in (("Cube", "MESH"), ("Light", "LIGHT"), ("Camera", "CAMERA")):
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == expected_type:
            obj.hide_render = True
            obj.hide_set(True)
            hidden_startup.append(name)

    meta = bpy.data.objects.new("META-1308-OP002-LAYER3B-VERTICAL-RESEARCH", None)
    collection.objects.link(meta)
    for key, value in {
        "goal_loop_branch_id": BRANCH_ID,
        "goal_loop_role": "vertical_research_metadata",
        "opening_id": "OP002",
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "source_structure_hash": specs["source_structure_hash"],
        "wall_height_m": specs["wall_height_m"],
        "wall_height_provenance": "ASSUME-Z-RESEARCH",
        "head_level_m": specs["head_level_m"],
        "head_level_provenance": "ASSUME-Z-RESEARCH",
        "sill_level_state": "unknown_not_authorized",
        "opening_cuts": 0,
        "opening_geometry_authorized": False,
        "research_only": True,
        "not_for_construction": True,
        "build_authorized": False,
    }.items():
        meta[key] = value

    checkpoint = out / "1308_op002_layer3b_checkpoint_v000.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint))

    wall_material = bpy.data.materials.get("MAT-1308-OP002-L3B-WALL-GRAY") or bpy.data.materials.new("MAT-1308-OP002-L3B-WALL-GRAY")
    wall_material.diffuse_color = (0.52, 0.55, 0.58, 1.0)
    wall_material.metallic = 0.0
    wall_material.roughness = 0.82
    locator_material = bpy.data.materials.get("MAT-1308-OP002-L3B-XY-BLUE") or bpy.data.materials.new("MAT-1308-OP002-L3B-XY-BLUE")
    locator_material.diffuse_color = (0.02, 0.30, 0.95, 1.0)
    locator_material.metallic = 0.0
    locator_material.roughness = 0.55
    head_material = bpy.data.materials.get("MAT-1308-OP002-L3B-HEAD-ORANGE") or bpy.data.materials.new("MAT-1308-OP002-L3B-HEAD-ORANGE")
    head_material.diffuse_color = (1.0, 0.28, 0.03, 1.0)
    head_material.metallic = 0.0
    head_material.roughness = 0.50

    walls = []
    for spec in specs["walls"]:
        mesh = bpy.data.meshes.new(spec["name"] + "-MESH")
        mesh.from_pydata(spec["vertices"], [], spec["faces"])
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new(spec["name"], mesh)
        collection.objects.link(obj)
        obj.data.materials.append(wall_material)
        obj["goal_loop_branch_id"] = BRANCH_ID
        obj["goal_loop_role"] = "intact_source_wall"
        obj["source_atom_id"] = spec["source_atom_id"]
        obj["source_centerline_m"] = json.dumps(spec["centerline_m"])
        obj["source_thickness_m"] = spec["thickness_m"]
        obj["research_height_m"] = specs["wall_height_m"]
        obj["opening_cut"] = False
        obj["research_only"] = True
        obj["not_for_construction"] = True
        obj["build_authorized"] = False
        walls.append(obj)

    guide_objects = []
    for index, spec in enumerate(specs["guides"]):
        mesh = bpy.data.meshes.new(spec["object_name"] + "-MESH")
        mesh.from_pydata(spec["vertices"], [], spec["faces"])
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new(spec["object_name"], mesh)
        collection.objects.link(obj)
        obj.data.materials.append(locator_material if index == 0 else head_material)
        obj["goal_loop_branch_id"] = BRANCH_ID
        obj["goal_loop_role"] = spec["role"]
        obj["opening_id"] = "OP002"
        obj["source_fact"] = False
        obj["opening_geometry"] = False
        obj["research_only"] = True
        obj["not_for_construction"] = True
        obj["source_correction_authorized"] = False
        obj["build_authorized"] = False
        guide_objects.append(obj)

    all_points = [
        obj.matrix_world @ vertex.co
        for obj in walls
        for vertex in obj.data.vertices
    ]
    mins = Vector(
        (
            min(point.x for point in all_points),
            min(point.y for point in all_points),
            min(point.z for point in all_points),
        )
    )
    maxs = Vector(
        (
            max(point.x for point in all_points),
            max(point.y for point in all_points),
            max(point.z for point in all_points),
        )
    )
    center = (mins + maxs) * 0.5
    span = max(maxs.x - mins.x, maxs.y - mins.y)
    segment_mid = Vector(
        (
            (specs["source_segment_m"][0][0] + specs["source_segment_m"][1][0]) / 2.0,
            (specs["source_segment_m"][0][1] + specs["source_segment_m"][1][1]) / 2.0,
            specs["wall_height_m"] / 2.0,
        )
    )
    normal = specs["display_face_normal_xy"]

    def camera(name: str, location: Sequence[float], target: Sequence[float], scale: float):
        data = bpy.data.cameras.new(name)
        data.type = "ORTHO"
        data.ortho_scale = float(scale)
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        obj.location = Vector(location)
        obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()
        obj["goal_loop_branch_id"] = BRANCH_ID
        obj["goal_loop_role"] = "validation_camera"
        obj["research_only"] = True
        return obj

    cameras = {
        "top": camera(
            "CAM-1308-OP002-L3B-TOP",
            (center.x, center.y, maxs.z + span * 2.0),
            (center.x, center.y, 0.0),
            span * 1.10,
        ),
        "northeast": camera(
            "CAM-1308-OP002-L3B-NE",
            (center.x + span * 1.5, center.y + span * 1.5, maxs.z + span * 1.35),
            (center.x, center.y, specs["wall_height_m"] * 0.38),
            span * 1.32,
        ),
        "front_closeup": camera(
            "CAM-1308-OP002-L3B-FRONT-CLOSEUP",
            (
                segment_mid.x + normal[0] * 0.6,
                segment_mid.y + normal[1] * 0.6,
                segment_mid.z,
            ),
            tuple(segment_mid),
            3.4,
        ),
    }
    cameras["front_closeup"].data.clip_start = 0.01

    wall_counts = Counter(obj["source_atom_id"] for obj in walls)
    expected_atom_ids = {spec["source_atom_id"] for spec in specs["walls"]}
    wall_topology_errors = [
        obj.name
        for obj in walls
        if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6
    ]
    wall_property_errors = [
        obj.name
        for obj in walls
        if (
            obj.get("opening_cut") is not False
            or obj.get("research_only") is not True
            or obj.get("not_for_construction") is not True
            or obj.get("build_authorized") is not False
        )
    ]
    guide_topology_errors = [
        obj.name
        for obj in guide_objects
        if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6
    ]
    guide_property_errors = [
        obj.name
        for obj in guide_objects
        if (
            obj.get("source_fact") is not False
            or obj.get("opening_geometry") is not False
            or obj.get("research_only") is not True
            or obj.get("not_for_construction") is not True
            or obj.get("build_authorized") is not False
        )
    ]
    forbidden_objects = [
        obj.name
        for obj in collection.objects
        if obj.get("goal_loop_role") in specs["forbidden_object_roles"]
    ]
    actual_guide_bounds = {
        obj.get("goal_loop_role"): {
            "z_min_m": min(vertex.co.z for vertex in obj.data.vertices),
            "z_max_m": max(vertex.co.z for vertex in obj.data.vertices),
        }
        for obj in guide_objects
    }
    expected_guide_bounds = {
        guide["role"]: {
            "z_min_m": guide["z_min_m"],
            "z_max_m": guide["z_max_m"],
        }
        for guide in specs["guides"]
    }
    guide_bound_errors = [
        role
        for role in expected_guide_bounds
        if any(
            not math.isclose(
                actual_guide_bounds[role][key],
                expected_guide_bounds[role][key],
                abs_tol=1e-6,
            )
            for key in ("z_min_m", "z_max_m")
        )
    ]
    validation = {
        "schema": "blender-op002-layer3b-vertical-research-validation-v1",
        "branch_id": BRANCH_ID,
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "plan_file_sha256": specs["plan_file_sha256"],
        "source_structure_hash": specs["source_structure_hash"],
        "source_document_sha256": specs["source_document_sha256"],
        "unit_system": "METRIC",
        "length_unit": "METERS",
        "scale_length": 1.0,
        "expected_wall_count": 35,
        "actual_wall_count": len(walls),
        "distinct_source_atom_count": len(wall_counts),
        "missing_source_atom_ids": sorted(expected_atom_ids - set(wall_counts)),
        "extra_source_atom_ids": sorted(set(wall_counts) - expected_atom_ids),
        "duplicate_source_atom_ids": sorted(atom_id for atom_id, count in wall_counts.items() if count != 1),
        "wall_topology_errors": wall_topology_errors,
        "wall_property_errors": wall_property_errors,
        "expected_guide_count": 2,
        "actual_guide_count": len(guide_objects),
        "guide_roles": [obj.get("goal_loop_role") for obj in guide_objects],
        "guide_topology_errors": guide_topology_errors,
        "guide_property_errors": guide_property_errors,
        "expected_guide_bounds_m": expected_guide_bounds,
        "actual_guide_bounds_m": actual_guide_bounds,
        "guide_bound_errors": guide_bound_errors,
        "forbidden_objects": forbidden_objects,
        "wall_height_m": specs["wall_height_m"],
        "wall_height_provenance": "unverified_research_assumption",
        "head_level_m": specs["head_level_m"],
        "head_level_provenance": "unverified_research_assumption",
        "sill_level_m": None,
        "sill_level_state": "unknown_not_authorized",
        "opening_cuts": 0,
        "opening_elements": 0,
        "opening_geometry_created": False,
        "floor_cut_created": False,
        "sill_geometry_created": False,
        "door_leaf_created": False,
        "lintel_structural_element_created": False,
        "ifc_opening_created": False,
        "ifc_void_or_fill_created": False,
        "validation_camera_count": len(cameras),
        "hidden_startup_objects": hidden_startup,
        "labels": specs["labels"],
        "research_only": True,
        "not_for_construction": True,
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "adjacency_confirmation": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "pass": (
            len(walls) == 35
            and len(wall_counts) == 35
            and all(count == 1 for count in wall_counts.values())
            and not wall_topology_errors
            and not wall_property_errors
            and len(guide_objects) == 2
            and not guide_topology_errors
            and not guide_property_errors
            and not guide_bound_errors
            and not forbidden_objects
        ),
    }
    validation_path = out / "validation.json"
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not validation["pass"]:
        raise RuntimeError("OP002 Layer3B Blender display validation failed")

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
    for key, active_camera in cameras.items():
        scene.camera = active_camera
        path = out / f"1308_op002_layer3b_{key}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_artifacts.append(_artifact(path, f"render_{key}"))

    final_blend = out / "1308_op002_layer3b_vertical_research_v001.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(final_blend))
    bpy.ops.object.select_all(action="DESELECT")
    for obj in [*walls, *guide_objects, meta]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = walls[0]
    glb = out / "1308_op002_layer3b_vertical_research_v001.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(glb),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
        export_yup=True,
        export_animations=False,
        export_normals=True,
        export_cameras=False,
        export_lights=False,
        export_extras=True,
    )
    artifacts = [
        _artifact(checkpoint, "checkpoint_blend"),
        _artifact(final_blend, "blender_source"),
        _artifact(glb, "portable_glb"),
        *render_artifacts,
        _artifact(validation_path, "validation"),
    ]
    manifest = {
        "schema": "blender-op002-layer3b-vertical-research-artifact-manifest-v1",
        "branch_id": BRANCH_ID,
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "plan_file_sha256": specs["plan_file_sha256"],
        "source_structure_hash": specs["source_structure_hash"],
        "source_document_sha256": specs["source_document_sha256"],
        "blender_version": bpy.app.version_string,
        "wall_count": len(walls),
        "guide_count": len(guide_objects),
        "guide_roles": [obj.get("goal_loop_role") for obj in guide_objects],
        "opening_cuts": 0,
        "opening_elements": 0,
        "door_leaf_created": False,
        "ifc_opening_created": False,
        "wall_height_m": specs["wall_height_m"],
        "head_level_m": specs["head_level_m"],
        "sill_level_m": None,
        "artifact_path_mode": "relative_to_manifest",
        "artifacts": artifacts,
        "labels": specs["labels"],
        "research_only": True,
        "not_for_construction": True,
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "adjacency_confirmation": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
    }
    manifest_path = out / "artifact_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "event": "op002_layer3b_vertical_research_complete",
                "manifest": str(manifest_path),
                "validation_pass": True,
                "wall_count": len(walls),
                "guide_count": len(guide_objects),
                "opening_cuts": 0,
            }
        )
    )
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
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(_argv(argv))
    run(args.source, args.plan, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BLENDER_EXE",
    "BRANCH_ID",
    "build_display_specs",
    "run",
    "validate_display_plan_for_blender",
]
