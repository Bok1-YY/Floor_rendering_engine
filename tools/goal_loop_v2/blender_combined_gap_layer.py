"""Build the combined 43-piece, full-height XY-gap research layer in Blender."""
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
DEFAULT_PLAN = ROOT / "reports/combined_gap_plan_20260903/plan.json"
BLENDER_EXE = Path(r"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")
EXPECTED_IDS = ("OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010")
EXPECTED_EXCLUDED = ("OP005", "OP011", "PORTAL-WB011-WB006-01", "OP012")
BRANCH_ID = "1308-combined-xy-gap-research-v001"
COLLECTION_NAME = "COL-1308-COMBINED-XY-GAP-RESEARCH-v001"
FAIL_CLOSED = (
    "source_correction_authorized",
    "xy_experiment_confirmation",
    "cut_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "semantic_promotion",
    "build_authorized",
    "ready",
)
REQUIRED_LABELS = (
    "COMBINED LAYER 2",
    "XY GAP RESEARCH ONLY",
    "NINE CANDIDATE GAPS",
    "FULL-HEIGHT VISUALIZATION ONLY",
    "NOT SOURCE-CONFIRMED",
    "NO DOOR/WINDOW SEMANTICS",
    "NO Z/HEAD/SILL CLAIM",
    "NO TRAVERSABILITY / ADJACENCY",
    "NOT FOR CONSTRUCTION",
)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _load(value: str | Path | Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value)), None
    path = Path(value)
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _assert_false_fields(value: Mapping[str, Any], fields: Sequence[str], *, context: str) -> None:
    for key in fields:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _validate_piece_intervals(plan: Mapping[str, Any]) -> None:
    parameters = [float(value) for value in plan["host_parameters"]]
    if len(parameters) != 2:
        raise ValueError("gap host parameter count drift")
    gap_lo, gap_hi = sorted(parameters)
    if gap_lo < -1e-9 or gap_hi > 1.0 + 1e-9 or gap_hi - gap_lo <= 1e-9:
        raise ValueError("gap interval is outside host")
    expected = []
    if gap_lo > 1e-9:
        expected.append([0.0, gap_lo])
    if 1.0 - gap_hi > 1e-9:
        expected.append([gap_hi, 1.0])
    actual = [piece["host_parameter_interval"] for piece in plan["remaining_host_pieces"]]
    if len(actual) != len(expected):
        raise ValueError("host residual count drift")
    for actual_interval, expected_interval in zip(actual, expected):
        if len(actual_interval) != 2 or not all(
            math.isclose(float(a), float(e), abs_tol=1e-9)
            for a, e in zip(actual_interval, expected_interval)
        ):
            raise ValueError("host residual does not complement gap")
        start, end = map(float, actual_interval)
        if end <= start or (end > gap_lo + 1e-9 and start < gap_hi - 1e-9):
            raise ValueError("host residual overlaps gap")


def validate_combined_plan_for_blender(
    value: Mapping[str, Any],
    *,
    document: Mapping[str, Any],
    source_file_sha256: str | None,
) -> dict[str, Any]:
    plan = deepcopy(dict(value))
    payload = {key: item for key, item in plan.items() if key != "candidate_hash"}
    if plan.get("schema") != "combined-gap-plan-v3" or plan.get("branch_id") != BRANCH_ID:
        raise ValueError("combined plan identity drift")
    if plan.get("candidate_hash") != _canonical_hash(payload):
        raise ValueError("combined plan candidate hash drift")
    if plan.get("source_structure_hash") != document["structure_hash"]:
        raise ValueError("combined plan source structure drift")
    if source_file_sha256 is not None and plan.get("source_document_sha256") != source_file_sha256:
        raise ValueError("combined plan source file drift")
    if tuple(plan.get("included_opening_ids", ())) != EXPECTED_IDS:
        raise ValueError("combined included opening coverage drift")
    if tuple(plan.get("excluded_opening_ids", ())) != EXPECTED_EXCLUDED:
        raise ValueError("combined excluded opening coverage drift")
    if set(plan["included_opening_ids"]) & set(plan["excluded_opening_ids"]):
        raise ValueError("combined included/excluded overlap")
    _assert_false_fields(plan, FAIL_CLOSED, context="combined plan")
    if (
        plan.get("gap_z_policy") != "full_height_visualization_only"
        or tuple(plan.get("artifact_labels", ())) != REQUIRED_LABELS
        or plan.get("reproducibility_scope") != "current_evidence_workspace"
        or plan.get("portable_bundle") is not False
        or plan.get("portability_blocker") != "upstream_manifests_embed_machine_absolute_artifact_paths"
    ):
        raise ValueError("combined research/portability policy drift")

    atoms = {atom["id"]: atom for atom in document["wall_graph"]["atoms"]}
    plans = list(plan.get("plans", []))
    if len(atoms) != 35 or len(plans) != len(EXPECTED_IDS):
        raise ValueError("combined source/plan count drift")
    if [row.get("opening_id") for row in plans] != list(EXPECTED_IDS):
        raise ValueError("combined plan row order drift")
    host_ids = [row.get("host_atom_id") for row in plans]
    if (
        host_ids != plan.get("host_atom_ids")
        or len(set(host_ids)) != len(host_ids)
        or any(host_id not in atoms for host_id in host_ids)
    ):
        raise ValueError("combined host identity/distinctness drift")
    if (
        plan.get("source_wall_atom_count") != len(atoms)
        or plan.get("untouched_atom_count") != len(atoms) - len(set(host_ids))
        or plan.get("host_piece_count") != sum(len(row["remaining_host_pieces"]) for row in plans)
        or plan.get("expected_wall_piece_count") != plan["untouched_atom_count"] + plan["host_piece_count"]
        or plan.get("expected_wall_piece_count") != 43
    ):
        raise ValueError("combined derived piece counts drift")

    for row in plans:
        _assert_false_fields(
            row,
            (
                "source_correction_authorized",
                "xy_experiment_confirmation",
                "cut_confirmation",
                "pair_confirmation",
                "adjacency_confirmation",
                "semantic_promotion",
                "build_authorized",
                "ready",
            ),
            context=str(row["opening_id"]),
        )
        row_payload = {key: item for key, item in row.items() if key != "variant_hash"}
        if row.get("variant_hash") != _canonical_hash(row_payload):
            raise ValueError("combined nested variant hash drift")
        if row.get("gap_z_policy") != "full_height_visualization_only":
            raise ValueError("combined nested Z policy drift")
        _validate_piece_intervals(row)

    bindings = list(plan.get("variant_artifact_bindings", []))
    if (
        len(bindings) != len(EXPECTED_IDS)
        or [row.get("opening_id") for row in bindings] != list(EXPECTED_IDS)
        or any(binding.get("variant_hash") != plans[index]["variant_hash"] for index, binding in enumerate(bindings))
    ):
        raise ValueError("combined artifact binding coverage drift")
    op001 = plan.get("op001_projection_source_distinction", {})
    if (
        op001.get("projection_mode") != "orthogonal_projection_within_wall_solid"
        or op001.get("segments_are_distinct") is not True
        or op001.get("source_gap_segment_m") == op001.get("projected_gap_segment_m")
        or op001.get("source_segment_preserved_as_provenance") is not True
    ):
        raise ValueError("OP001 projection/source distinction drift")
    op003 = plan.get("op003_endpoint_residual_rule", {})
    if (
        op003.get("gap_starts_at_host_parameter_zero") is not True
        or op003.get("zero_length_endpoint_residual_omitted") is not True
        or op003.get("remaining_host_piece_count") != 1
        or op003.get("short_residual_piece_count_below_0_05m") != 0
    ):
        raise ValueError("OP003 endpoint residual rule drift")
    return plan


def build_piece_specs(
    source: str | Path | Mapping[str, Any],
    plan: str | Path | Mapping[str, Any],
    wall_height: float = 2.8,
) -> dict[str, Any]:
    if wall_height <= 0:
        raise ValueError("wall height must be positive")
    document_raw, source_file_sha256 = _load(source)
    document = validate_v21_document(document_raw)
    plan_raw, plan_file_sha256 = _load(plan)
    combined = validate_combined_plan_for_blender(
        plan_raw,
        document=document,
        source_file_sha256=source_file_sha256,
    )
    atoms = {atom["id"]: atom for atom in document["wall_graph"]["atoms"]}
    plan_by_host = {row["host_atom_id"]: row for row in combined["plans"]}
    pieces = []
    for atom in document["wall_graph"]["atoms"]:
        if atom["id"] in plan_by_host:
            continue
        vertices, faces = wall_box_geometry(atom, wall_height)
        pieces.append(
            {
                "name": "GEO-WALL-" + atom["id"],
                "source_atom_id": atom["id"],
                "opening_id": "",
                "is_gap_host_piece": False,
                "host_parameter_interval": [0.0, 1.0],
                "centerline_m": deepcopy(atom["centerline_m"]),
                "thickness_m": float(atom["thickness_m"]),
                "vertices": vertices,
                "faces": faces,
            }
        )
    for row in combined["plans"]:
        host = atoms[row["host_atom_id"]]
        for index, residual in enumerate(row["remaining_host_pieces"], start=1):
            atom = {
                "centerline_m": residual["centerline_m"],
                "thickness_m": host["thickness_m"],
            }
            vertices, faces = wall_box_geometry(atom, wall_height)
            pieces.append(
                {
                    "name": f"GEO-WALL-{host['id']}-{row['opening_id']}-P{index:02d}",
                    "source_atom_id": host["id"],
                    "opening_id": row["opening_id"],
                    "is_gap_host_piece": True,
                    "host_parameter_interval": deepcopy(residual["host_parameter_interval"]),
                    "centerline_m": deepcopy(residual["centerline_m"]),
                    "thickness_m": float(host["thickness_m"]),
                    "vertices": vertices,
                    "faces": faces,
                }
            )
    counts = Counter(piece["source_atom_id"] for piece in pieces)
    expected_counts = {
        atom_id: len(plan_by_host[atom_id]["remaining_host_pieces"]) if atom_id in plan_by_host else 1
        for atom_id in atoms
    }
    if (
        len(pieces) != combined["expected_wall_piece_count"]
        or counts != Counter(expected_counts)
        or sum(piece["is_gap_host_piece"] for piece in pieces) != combined["host_piece_count"]
    ):
        raise ValueError("combined piece construction drift")
    return {
        "schema": "combined-gap-piece-specs-v1",
        "branch_id": BRANCH_ID,
        "plan_candidate_hash": combined["candidate_hash"],
        "plan_file_sha256": plan_file_sha256,
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": source_file_sha256 or combined["source_document_sha256"],
        "wall_height_m": float(wall_height),
        "included_opening_ids": list(combined["included_opening_ids"]),
        "excluded_opening_ids": list(combined["excluded_opening_ids"]),
        "host_atom_ids": list(combined["host_atom_ids"]),
        "gap_intervals_by_host": {
            row["host_atom_id"]: sorted(float(value) for value in row["host_parameters"])
            for row in combined["plans"]
        },
        "gap_windows_by_opening": {
            row["opening_id"]: {
                "center_m": [
                    (float(row["projected_segment_m"][0][0]) + float(row["projected_segment_m"][1][0])) / 2.0,
                    (float(row["projected_segment_m"][0][1]) + float(row["projected_segment_m"][1][1])) / 2.0,
                ],
                "ortho_scale_m": max(2.4, float(row["projected_width_m"]) * 2.5),
                "resolution_px": [1200, 1200],
            }
            for row in combined["plans"]
        },
        "expected_source_atom_piece_counts": expected_counts,
        "expected_wall_piece_count": combined["expected_wall_piece_count"],
        "untouched_atom_count": combined["untouched_atom_count"],
        "host_piece_count": combined["host_piece_count"],
        "pieces": pieces,
        "artifact_labels": list(combined["artifact_labels"]),
        "evidence_plan_portable": False,
        "research_only": True,
        "not_for_construction": True,
        "gap_z_policy": "full_height_visualization_only",
        "source_correction_authorized": False,
        "xy_experiment_confirmation": False,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
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
    wall_height: float = 2.8,
) -> dict[str, Any]:
    import bpy
    from mathutils import Vector

    source_path = Path(source).resolve()
    plan_path = Path(plan).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    specs = build_piece_specs(source_path, plan_path, wall_height)

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.unit_settings.scale_length = 1.0
    scene["goal_loop_branch_id"] = BRANCH_ID
    scene["research_only"] = True
    scene["not_for_construction"] = True
    scene["formal_build_authorized"] = False
    scene["build_authorized"] = False
    scene["source_correction_authorized"] = False
    scene["semantic_promotion"] = False
    scene["score_effect"] = "none"
    scene["gap_z_policy"] = "full_height_visualization_only"
    scene["wall_height_assumption_m"] = float(wall_height)
    scene["expected_wall_piece_count"] = specs["expected_wall_piece_count"]
    scene["evidence_plan_portable"] = False

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

    meta = bpy.data.objects.new("META-1308-COMBINED-XY-GAP-RESEARCH", None)
    collection.objects.link(meta)
    for key, value in {
        "goal_loop_branch_id": BRANCH_ID,
        "goal_loop_role": "combined_xy_gap_research_metadata",
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "source_structure_hash": specs["source_structure_hash"],
        "wall_height_m": specs["wall_height_m"],
        "expected_wall_piece_count": specs["expected_wall_piece_count"],
        "opening_elements": 0,
        "gap_z_policy": "full_height_visualization_only",
        "evidence_plan_portable": False,
        "research_only": True,
        "not_for_construction": True,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
    }.items():
        meta[key] = value

    checkpoint = out / "1308_combined_xy_gap_checkpoint_v000.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint))

    material = bpy.data.materials.get("MAT-1308-COMBINED-XY-GAP-RESEARCH-GRAY")
    if material is None:
        material = bpy.data.materials.new("MAT-1308-COMBINED-XY-GAP-RESEARCH-GRAY")
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
        obj["goal_loop_branch_id"] = BRANCH_ID
        obj["goal_loop_role"] = "wall_piece"
        obj["source_atom_id"] = spec["source_atom_id"]
        obj["opening_id"] = spec["opening_id"]
        obj["is_gap_host_piece"] = spec["is_gap_host_piece"]
        obj["host_parameter_interval"] = json.dumps(spec["host_parameter_interval"])
        obj["source_centerline_m"] = json.dumps(spec["centerline_m"])
        obj["source_thickness_m"] = spec["thickness_m"]
        obj["research_height_m"] = specs["wall_height_m"]
        obj["plan_candidate_hash"] = specs["plan_candidate_hash"]
        obj["gap_z_policy"] = "full_height_visualization_only"
        obj["research_only"] = True
        obj["not_for_construction"] = True
        obj["source_correction_authorized"] = False
        obj["semantic_promotion"] = False
        obj["score_effect"] = "none"
        obj["build_authorized"] = False
        walls.append(obj)

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
    span_z = maxs.z - mins.z

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
            "CAM-1308-COMBINED-TOP-ORTHO",
            (center.x, center.y, maxs.z + span * 2.0),
            (center.x, center.y, 0.0),
            span * 1.10,
        ),
        "northeast": camera(
            "CAM-1308-COMBINED-NE-AXON",
            (center.x + span * 1.5, center.y + span * 1.5, maxs.z + span * 1.35),
            (center.x, center.y, span_z * 0.38),
            span * 1.32,
        ),
        "northwest": camera(
            "CAM-1308-COMBINED-NW-AXON",
            (center.x - span * 1.5, center.y + span * 1.5, maxs.z + span * 1.35),
            (center.x, center.y, span_z * 0.38),
            span * 1.32,
        ),
    }
    first_window = specs["gap_windows_by_opening"][specs["included_opening_ids"][0]]
    closeup_center = first_window["center_m"]
    closeup_camera = camera(
        "CAM-1308-COMBINED-GAP-CLOSEUP-TOP",
        (closeup_center[0], closeup_center[1], maxs.z + 8.0),
        (closeup_center[0], closeup_center[1], 0.0),
        first_window["ortho_scale_m"],
    )

    counts = Counter(obj["source_atom_id"] for obj in walls)
    expected_counts = Counter(specs["expected_source_atom_piece_counts"])
    non_host_count_errors = [
        atom_id
        for atom_id in expected_counts
        if atom_id not in specs["host_atom_ids"] and counts[atom_id] != 1
    ]
    host_count_errors = [
        atom_id
        for atom_id in specs["host_atom_ids"]
        if counts[atom_id] != expected_counts[atom_id]
    ]
    topology_errors = [
        obj.name
        for obj in walls
        if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6
    ]
    property_errors = [
        obj.name
        for obj in walls
        if (
            obj.get("research_only") is not True
            or obj.get("not_for_construction") is not True
            or obj.get("build_authorized") is not False
            or obj.get("semantic_promotion") is not False
            or obj.get("source_correction_authorized") is not False
            or obj.get("score_effect") != "none"
        )
    ]
    gap_overlap_errors = []
    for obj in walls:
        if obj.get("is_gap_host_piece") is True:
            start, end = json.loads(obj["host_parameter_interval"])
            gap_lo, gap_hi = specs["gap_intervals_by_host"][obj["source_atom_id"]]
            if not (end <= gap_lo + 1e-9 or start >= gap_hi - 1e-9):
                gap_overlap_errors.append(obj.name)
    opening_element_objects = [
        obj.name
        for obj in collection.objects
        if obj.get("goal_loop_role") in {"door", "window", "opening_element", "ifc_void", "ifc_fill"}
    ]

    validation = {
        "schema": "blender-combined-gap-layer-validation-v1",
        "branch_id": BRANCH_ID,
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "plan_file_sha256": specs["plan_file_sha256"],
        "source_structure_hash": specs["source_structure_hash"],
        "source_document_sha256": specs["source_document_sha256"],
        "wall_height_m": specs["wall_height_m"],
        "unit_system": "METRIC",
        "length_unit": "METERS",
        "scale_length": 1.0,
        "source_wall_atom_count": len(expected_counts),
        "expected_wall_piece_count": specs["expected_wall_piece_count"],
        "actual_wall_piece_count": len(walls),
        "untouched_atom_count": specs["untouched_atom_count"],
        "actual_untouched_piece_count": sum(obj.get("is_gap_host_piece") is False for obj in walls),
        "host_atom_count": len(specs["host_atom_ids"]),
        "expected_host_piece_count": specs["host_piece_count"],
        "actual_host_piece_count": sum(obj.get("is_gap_host_piece") is True for obj in walls),
        "included_opening_ids": specs["included_opening_ids"],
        "excluded_opening_ids": specs["excluded_opening_ids"],
        "host_atom_ids": specs["host_atom_ids"],
        "expected_source_atom_piece_counts": dict(expected_counts),
        "actual_source_atom_piece_counts": dict(counts),
        "non_host_count_errors": non_host_count_errors,
        "host_count_errors": host_count_errors,
        "topology_errors": topology_errors,
        "property_errors": property_errors,
        "gap_overlap_errors": gap_overlap_errors,
        "world_bbox_m": {"min": list(mins), "max": list(maxs)},
        "full_top_metric_window": {
            "center_m": [center.x, center.y],
            "ortho_scale_m": span * 1.10,
            "resolution_px": [1200, 1200],
            "meters_per_pixel": span * 1.10 / 1200.0,
        },
        "hidden_startup_objects": hidden_startup,
        "validation_camera_count": len(cameras) + 1,
        "gap_closeup_metric_windows": specs["gap_windows_by_opening"],
        "opening_elements": len(opening_element_objects),
        "opening_element_objects": opening_element_objects,
        "artifact_labels": specs["artifact_labels"],
        "gap_z_policy": "full_height_visualization_only",
        "evidence_plan_portable": False,
        "research_only": True,
        "not_for_construction": True,
        "source_correction_authorized": False,
        "xy_experiment_confirmation": False,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "pass": (
            len(walls) == specs["expected_wall_piece_count"]
            and counts == expected_counts
            and not non_host_count_errors
            and not host_count_errors
            and not topology_errors
            and not property_errors
            and not gap_overlap_errors
            and not opening_element_objects
        ),
    }
    validation_path = out / "validation.json"
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not validation["pass"]:
        raise RuntimeError("combined gap layer structural validation failed")

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
        path = out / f"1308_combined_xy_gap_{key}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_artifacts.append(_artifact(path, f"render_{key}"))
    for opening_id in specs["included_opening_ids"]:
        window = specs["gap_windows_by_opening"][opening_id]
        gap_center = window["center_m"]
        closeup_camera.location = Vector((gap_center[0], gap_center[1], maxs.z + 8.0))
        closeup_camera.rotation_euler = (
            Vector((gap_center[0], gap_center[1], 0.0)) - closeup_camera.location
        ).to_track_quat("-Z", "Y").to_euler()
        closeup_camera.data.ortho_scale = float(window["ortho_scale_m"])
        scene.camera = closeup_camera
        path = out / f"1308_combined_xy_gap_{opening_id}_closeup_top.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_artifacts.append(_artifact(path, f"render_gap_closeup_{opening_id}"))

    final_blend = out / "1308_combined_xy_gap_research_v001.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(final_blend))

    bpy.ops.object.select_all(action="DESELECT")
    for obj in walls:
        obj.select_set(True)
    meta.select_set(True)
    bpy.context.view_layer.objects.active = walls[0]
    glb = out / "1308_combined_xy_gap_research_v001.glb"
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
        "schema": "blender-combined-gap-layer-artifact-manifest-v1",
        "branch_id": BRANCH_ID,
        "plan_candidate_hash": specs["plan_candidate_hash"],
        "plan_file_sha256": specs["plan_file_sha256"],
        "source_structure_hash": specs["source_structure_hash"],
        "source_document_sha256": specs["source_document_sha256"],
        "blender_version": bpy.app.version_string,
        "wall_piece_count": len(walls),
        "untouched_atom_count": specs["untouched_atom_count"],
        "host_atom_count": len(specs["host_atom_ids"]),
        "host_piece_count": specs["host_piece_count"],
        "opening_elements": 0,
        "wall_height_m": specs["wall_height_m"],
        "artifact_path_mode": "relative_to_manifest",
        "evidence_plan_portable": False,
        "artifact_files_relocatable_with_manifest": True,
        "artifacts": artifacts,
        "artifact_labels": specs["artifact_labels"],
        "gap_z_policy": "full_height_visualization_only",
        "research_only": True,
        "not_for_construction": True,
        "source_correction_authorized": False,
        "xy_experiment_confirmation": False,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
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
                "event": "combined_xy_gap_research_complete",
                "manifest": str(manifest_path),
                "validation_pass": True,
                "wall_pieces": len(walls),
                "opening_elements": 0,
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
    parser.add_argument("--wall-height", type=float, default=2.8)
    args = parser.parse_args(_argv(argv))
    run(args.source, args.plan, args.out, args.wall_height)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BLENDER_EXE",
    "BRANCH_ID",
    "build_piece_specs",
    "run",
    "validate_combined_plan_for_blender",
]
