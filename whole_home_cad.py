# -*- coding: utf-8 -*-
"""Deterministic CAD ingestion for the whole-home metric model.

DWG is converted locally by the MIT-licensed ACadSharp adapter when available;
a separately configured and commercially-authorized ODA adapter remains an
optional fallback.  CAD failures are deliberately fail-closed before any AI
workflow is entered.  DXF parsing uses ezdxf and preserves an auditable
provenance record for every fact promoted into WholeHomeModel v2.
"""
from __future__ import annotations

import copy
import hashlib
import html
import importlib.metadata
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from .whole_home_cad_space import (
    classify_raw_faces,
    initial_space_layers,
    physical_facts_hash,
    semantic_overlay_hash,
)
from .whole_home_wall_assembly import (
    WallAssemblyError,
    bind_raw_geometry_openings,
    build_wall_assemblies,
    decompose_cad_entity_roles,
    stitch_wall_assemblies_across_openings,
    summarize_raw_geometry_openings,
)
from .whole_home_global_topology import (
    GlobalTopologyError,
    build_global_wall_topology,
)
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError

from .config import MAIN_OUTPUT_DIR, UPLOAD_DIR


CAD_ROOT = os.path.join(MAIN_OUTPUT_DIR, "_whole_home", "cad")
CAD_MODEL_COORDINATE_SYSTEM_V2 = "right-handed-y-up-x-east-z-south-v2"
CAD_PLAN_TRANSFORM_VERSION = 2
REFERENCE_ASSET_ROOT = os.path.join(MAIN_OUTPUT_DIR, "_whole_home", "reference_assets")
REFERENCE_ASSET_MAX_PIXELS = 20_000_000
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ACADSHARP_ENV_NAME = "FLOOR_ACADSHARP_DWG_CONVERTER"
DOTNET_HOST_ENV_NAME = "FLOOR_DOTNET_HOST"
ACADSHARP_ASSEMBLY_NAME = "FloorEngine.ACadSharpDwgConverter.dll"
ODA_ENV_NAMES = ("FLOOR_DWG_CONVERTER", "FLOOR_ODA_FILE_CONVERTER", "ODA_FILE_CONVERTER")
DWG_COMMERCIAL_AUTH_ENV = "FLOOR_DWG_CONVERTER_COMMERCIAL_AUTHORIZED"
DWG_VERSION_NAMES = {
    "AC1009": "R12", "AC1012": "R13", "AC1014": "R14",
    "AC1015": "2000", "AC1018": "2004", "AC1021": "2007",
    "AC1024": "2010", "AC1027": "2013", "AC1032": "2018+",
}
INSUNITS_TO_METRES = {
    1: 0.0254, 2: 0.3048, 3: 1609.344, 4: 0.001, 5: 0.01,
    6: 1.0, 7: 1000.0, 8: 0.0000254, 9: 0.000001,
    10: 0.9144, 11: 1e-10, 12: 1e-9, 13: 1e-6, 14: 0.1,
    15: 10.0, 16: 100.0, 17: 1e12, 18: 1.495978707e11,
    19: 9.460730472e15, 20: 3.085677581e16,
}
_ARCHITECTURAL_UNIT_SCALE_CANDIDATES = (
    {"unit": "mm", "unit_code": 4, "metres_per_unit": 0.001},
    {"unit": "cm", "unit_code": 5, "metres_per_unit": 0.01},
    {"unit": "inch", "unit_code": 1, "metres_per_unit": 0.0254},
    {"unit": "foot", "unit_code": 2, "metres_per_unit": 0.3048},
    {"unit": "m", "unit_code": 6, "metres_per_unit": 1.0},
)
_ANNOTATION_LAYER_RE = re.compile(
    r"(?:paper|layout|title|legend|annotation|annot|text|文字|标注|尺寸|dim|hatch|填充|图框)", re.I)
_WALL_LAYER_RE = re.compile(r"(?:wall|walls|墙|结构|a-wall)", re.I)
_OPENING_TOKENS = {
    "door": ("door", "门"), "window": ("window", "窗"),
}
_OBJECT_ROLES = {
    "bedside_table": ("bedside", "nightstand", "床边柜", "床头柜"),
    "bed": ("bed", "床"), "sofa": ("sofa", "沙发"),
    "toilet": ("toilet", "wc", "马桶", "坐便"),
    "basin": ("basin", "vanity", "washbasin", "洗手盆", "台盆", "浴室柜"),
    "sink": ("kitchen sink", "sink", "水槽", "洗菜盆"),
    "hob": ("hob", "cooktop", "stove", "灶", "炉"),
    "hood": ("hood", "rangehood", "range hood", "油烟机", "烟机"),
    "faucet": ("faucet", "tap", "龙头", "水嘴"),
    "mirror": ("mirror", "镜"),
    "shower_zone": ("shower", "淋浴"), "wardrobe": ("wardrobe", "closet", "衣柜"),
    "tv": ("television", "tv", "电视"),
    "kitchen_run": ("kitchen", "counter", "cabinet", "橱柜", "操作台"),
    "fridge": ("fridge", "refrigerator", "冰箱"),
    "dining_table": ("dining table", "dining_table", "餐桌"),
    "washing_machine": ("washer", "washing", "洗衣机"),
}

_REFERENCE_PROXY_HEIGHTS_M = {
    "bedside_table": .55, "bed": .55, "sofa": .85, "toilet": .75, "basin": .82, "sink": .18,
    "hob": .12, "hood": .65, "faucet": .35, "mirror": 1.0,
    "shower_zone": 2.0, "wardrobe": 2.2, "tv": 1.0, "kitchen_run": .9,
    "fridge": 1.85, "dining_table": .76, "washing_machine": .85,
}

JUSTEASY_REFERENCE_URL_TOKEN = "16770314"


def cad_plan_to_model(point: Sequence[float], transform: Mapping[str, Any]) -> tuple[float, float]:
    """Map a metric CAD XY point into model XZ using the persisted affine contract.

    V1 projects were translation-only.  V2 deliberately maps CAD +Y to model
    -Z so a right-handed camera above +Y can show CAD north at screen-up without
    reflecting the projection matrix.
    """
    x_scale = float(transform.get("x_scale", 1.0) or 1.0)
    z_scale = float(transform.get("z_scale", 1.0) or 1.0)
    return (
        float(point[0]) * x_scale + float(transform.get("x") or 0.0),
        float(point[1]) * z_scale + float(transform.get("z") or 0.0),
    )


def model_plan_to_cad(point: Sequence[float], transform: Mapping[str, Any]) -> tuple[float, float]:
    """Map model XZ back to metric CAD XY for provenance measurements."""
    x_scale = float(transform.get("x_scale", 1.0) or 1.0)
    z_scale = float(transform.get("z_scale", 1.0) or 1.0)
    if abs(x_scale) <= 1e-12 or abs(z_scale) <= 1e-12:
        raise CadError("cad_coordinate_transform_singular", "CAD/模型坐标变换不可逆")
    # model_to_cad stores its own affine coefficients.  The two public
    # transforms happen to share z_scale=-1 in V2, but this function does not
    # rely on that coincidence.
    return (
        float(point[0]) * x_scale + float(transform.get("x") or 0.0),
        float(point[1]) * z_scale + float(transform.get("z") or 0.0),
    )


def cad_plan_transforms_v2(*, min_x: float, max_y: float) -> tuple[dict, dict]:
    forward = {
        "schema_version": CAD_PLAN_TRANSFORM_VERSION,
        "type": "affine_plan_v2",
        "x": round(-float(min_x), 8),
        "z": round(float(max_y), 8),
        "x_scale": 1.0,
        "z_scale": -1.0,
        "matrix_3x3": [
            [1.0, 0.0, round(-float(min_x), 8)],
            [0.0, -1.0, round(float(max_y), 8)],
            [0.0, 0.0, 1.0],
        ],
        "axis_mapping": {"cad_x": "+model_x", "cad_y": "-model_z"},
    }
    inverse = {
        "schema_version": CAD_PLAN_TRANSFORM_VERSION,
        "type": "affine_plan_v2",
        "x": round(float(min_x), 8),
        "z": round(float(max_y), 8),
        "x_scale": 1.0,
        "z_scale": -1.0,
        "matrix_3x3": [
            [1.0, 0.0, round(float(min_x), 8)],
            [0.0, -1.0, round(float(max_y), 8)],
            [0.0, 0.0, 1.0],
        ],
        "axis_mapping": {"model_x": "+cad_x", "model_z": "-cad_y"},
    }
    return forward, inverse


_MODEL_POLYLINE_KEYS = frozenset({
    "centerline", "opening_axis", "source_centerline", "footprint_polygon",
    "wall_footprint_polygon", "model_segment_m", "opening_axis_model_m",
    "source_axis_model_m", "unique_axis_model_m", "model_polygon_m",
})
_MODEL_BBOX_KEYS = frozenset({"source_frame_bbox_model_m", "bbox_model_m"})


def _mirror_model_plan_value(value: Any, *, depth_m: float, key: str = "") -> Any:
    """Mirror known model-plan evidence while leaving source CAD provenance intact."""
    if key in _MODEL_BBOX_KEYS and isinstance(value, Sequence) and len(value) == 4:
        return [
            round(float(value[0]), 8), round(depth_m - float(value[3]), 8),
            round(float(value[2]), 8), round(depth_m - float(value[1]), 8),
        ]
    if key in _MODEL_POLYLINE_KEYS and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = []
        for point in value:
            if isinstance(point, Mapping) and "x" in point and "z" in point:
                row = copy.deepcopy(dict(point))
                row["z"] = round(depth_m - float(row["z"]), 8)
                rows.append(row)
            elif isinstance(point, Sequence) and not isinstance(point, (str, bytes)) and len(point) >= 2:
                row = list(point)
                row[1] = round(depth_m - float(row[1]), 8)
                rows.append(row)
            else:
                rows.append(copy.deepcopy(point))
        return rows
    if isinstance(value, Mapping):
        result = {}
        for child_key, child in value.items():
            child_name = str(child_key)
            # CAD/source coordinates are immutable evidence and are always
            # converted through the public inverse transform when measured.
            if (child_name == "cad_provenance" or "_cad" in child_name
                    or child_name in {"source_segment_m", "source_polygon_m",
                                      "axis_segment_cad_m", "cad_world_bbox_m",
                                      "cad_local_bbox_m"}):
                result[child_key] = copy.deepcopy(child)
            else:
                result[child_key] = _mirror_model_plan_value(
                    child, depth_m=depth_m, key=child_name)
        return result
    if isinstance(value, list):
        return [_mirror_model_plan_value(item, depth_m=depth_m) for item in value]
    return copy.deepcopy(value)


def reorient_cad_model_to_v2(model: Mapping[str, Any], *, depth_m: float) -> dict:
    """Return the production model in the canonical sky-view coordinate basis."""
    result = copy.deepcopy(dict(model))

    def point_dict(point: Any) -> Any:
        if not isinstance(point, Mapping) or "z" not in point:
            return copy.deepcopy(point)
        row = copy.deepcopy(dict(point))
        row["z"] = round(depth_m - float(row["z"]), 8)
        return row

    for wall in result.get("walls") or []:
        wall["start"] = point_dict(wall.get("start"))
        wall["end"] = point_dict(wall.get("end"))
    result["wall_assemblies"] = [
        _mirror_model_plan_value(row, depth_m=depth_m)
        for row in result.get("wall_assemblies") or []
    ]
    for collection_name in ("rooms", "physical_spaces"):
        for row in result.get(collection_name) or []:
            row["polygon"] = [point_dict(point) for point in row.get("polygon") or []]
            row["interior_rings"] = [
                [point_dict(point) for point in ring]
                for ring in row.get("interior_rings") or []
            ]
    for zone in result.get("semantic_zones") or []:
        geometry = zone.get("geometry") if isinstance(zone.get("geometry"), dict) else {}
        geometry["points"] = [point_dict(point) for point in geometry.get("points") or []]
        if geometry.get("start") is not None:
            geometry["start"] = point_dict(geometry["start"])
        if geometry.get("end") is not None:
            geometry["end"] = point_dict(geometry["end"])
        if geometry.get("min_z") is not None and geometry.get("max_z") is not None:
            old_min, old_max = float(geometry["min_z"]), float(geometry["max_z"])
            geometry["min_z"], geometry["max_z"] = (
                round(depth_m - old_max, 8), round(depth_m - old_min, 8))
        if geometry.get("side") == "left":
            geometry["side"] = "right"
        elif geometry.get("side") == "right":
            geometry["side"] = "left"
        zone["geometry"] = geometry
    for footprint in result.get("global_wall_footprints") or []:
        footprint["points"] = [point_dict(point) for point in footprint.get("points") or []]
        footprint["interior_rings"] = [
            [point_dict(point) for point in ring]
            for ring in footprint.get("interior_rings") or []
        ]
    for item in result.get("fixed_objects") or []:
        item["position"] = point_dict(item.get("position"))
        item["insert_position"] = point_dict(item.get("insert_position"))
    for field in (
        "attached_exterior_space_evidence", "terminal_open_connection_evidence",
        "semantic_building_envelope_evidence", "global_wall_topology",
    ):
        if field in result:
            result[field] = _mirror_model_plan_value(result[field], depth_m=depth_m)
    result["coordinate_system"] = CAD_MODEL_COORDINATE_SYSTEM_V2
    result["coordinate_contract_version"] = CAD_PLAN_TRANSFORM_VERSION
    result["plan_axis_convention"] = {
        "model_x": "east/right", "model_y": "elevation/up",
        "model_z": "south/screen-down", "cad_x": "+model_x",
        "cad_y": "-model_z", "topdown_view": "sky-to-ground",
    }
    return result


def _clean_public_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _reference_asset(asset_id: str, filename: str, sha256: str, thumb_folder: str) -> dict:
    return {
        "asset_id": asset_id,
        "filename": filename,
        "sha256": sha256,
        "expected_mime": "image/jpeg",
        "expected_width": 500,
        "expected_height": 500,
        "max_pixels": REFERENCE_ASSET_MAX_PIXELS,
        "public_thumb_url": _clean_public_url(
            f"https://vrpic.justeasy.cn/thumb/pano/20260316/{thumb_folder}/thumb.jpg"
        ),
        "status": "unresolved",
        "export_allowed": False,
    }


def _reference_viewpoint(scene_record_id: int, scene_id: int, scene_name: str,
                         pano_folder: str, relative_landing_rule: str) -> dict:
    return {
        "scene_record_id": scene_record_id,
        "scene_id": scene_id,
        "name": scene_name,
        "pano_resource": _clean_public_url(
            f"https://vrpic.justeasy.cn/pano/20260316/{pano_folder}/"
        ),
        "yaw_policy": "flexible",
        "point_mapping": {
            "status": "not_available",
            "coordinate_system": "not_published",
            "evidence_url": "https://vr.justeasy.cn/view/16770314h7u0u192-1773594850.html",
            "evidence": [
                "public_viewer.is_showmap=0",
                "public_viewer.map_url_empty",
                "public_viewer.lat_lng_empty",
                "public_viewer.sand_target_mapping_null",
            ],
            "uncertainty": "No public floor-map coordinate or deterministic CAD-to-panorama transform is exposed.",
        },
        "landing_policy": {
            "mode": "cad_semantic_relative_region",
            "relative_landing_rule": relative_landing_rule,
            "yaw": "flexible",
            "source": "inferred_from_reference_visual_and_cad_anchors",
        },
    }


JUSTEASY_REFERENCE_CONTRACT = {
    "schema_version": 1,
    "contract_id": "justeasy_16770314_static_v1",
    "title": "晨兴公寓",
    "public_reference_url": "https://vr.justeasy.cn/view/16770314h7u0u192-1773594850.html",
    "reference_role": "style_and_composition_only",
    "geometry_authority": "cad",
    "output": {"mode": "static", "aspect_ratio": "4:3", "resolution": "4K", "panorama": False},
    "camera": {
        "eye_height_m": {"min": 1.35, "max": 1.55},
        "focal_length_mm": {"min": 24, "max": 35},
        # The public panorama exposes no CAD-aligned pitch, and the operator
        # explicitly allows angle optimisation.  Keep roll corrected while a
        # modest local pitch search frames near beds/fixtures without moving
        # the CAD-derived camera landing point.
        "vertical_deviation_deg_max": 10.0,
        "safe_frame": {"x_min": .08, "x_max": .92, "y_min": .08, "y_max": .94},
    },
    "global_hard_constraints": [
        "All connected interior floor areas use one CAD elevation; never invent a step, threshold or platform.",
        "Add, delete, move or resize zero CAD walls, doors, windows, columns or openings.",
        "Freeze every observed fixed object's identity, count and CAD position.",
        "Never change one room type into another room type.",
        "Respect CAD scale; never use ultra-wide stretching to fake a larger space.",
        "Material editing cannot change structure or geometry.",
        "If reference imagery conflicts with CAD, CAD always wins.",
    ],
    "style_contract": {
        "palette": ["warm grey", "greige", "charcoal black", "smoked wood", "low saturation"],
        "floor": "light warm-grey large-format matte tile",
        "details": ["integrated black cabinetry", "slender black frames"],
        "lighting": "balanced natural daylight with restrained 3000K integrated light strips",
    },
    "slots": [
        {"slot_id": "living_openplan_axis", "reference_image_id": "01_living_a", "room_profile": "living_room",
         "reference_asset": _reference_asset("01_living_a", "01_living_a.jpg", "439f67228c3fe72318847eaac5f2c88692ef9183bb85c5c2f984b36f18a214fe", "42-011309_six_14c79115ac65_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172021997, 279876079, "客餐厅", "42-011309_six_14c79115ac65_kjl.tiles", "Choose a collision-free living-area point that maximizes simultaneous visibility of the dining zone, TV wall and authentic corridor."),
         "focal_length_mm": {"min": 24, "max": 28},
         "must_show": ["dining zone", "TV wall", "CAD-authentic corridor"],
         # The audited panorama thumbnail intentionally lets the dining set
         # enter from the left/bottom foreground.  Only this subject may touch
         # those two frame edges; the TV wall and corridor retain the global
         # safe-frame contract.
         "subject_safe_frame_overrides": {
             "dining zone": {"x_min": 0.0, "x_max": .92, "y_min": .08, "y_max": 1.0},
             "CAD-authentic corridor": {"x_min": .08, "x_max": .92, "y_min": .08, "y_max": 1.0},
         },
         "hard_constraints": ["Preserve the real open-plan axis and corridor connectivity."]},
        {"slot_id": "living_tv_window_axis", "reference_image_id": "02_living_b", "room_profile": "living_room",
         "reference_asset": _reference_asset("02_living_b", "02_living_b.jpg", "f5c644463d5741acba59a72ae43e1ce83b573f12f873129df95d4cd865bad16e", "42-011350_six_af56c01c8450_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172022000, 279876082, "客餐厅", "42-011350_six_af56c01c8450_kjl.tiles", "Choose an alternate collision-free living-axis point maximizing the TV wall and a real CAD daylight opening."),
         "focal_length_mm": {"min": 24, "max": 28},
         "must_show": ["TV wall", "CAD-authentic daylight opening"],
         "hard_constraints": ["Use only the window or glazed opening present in CAD."]},
        {"slot_id": "kitchen_cookline_elevation", "reference_image_id": "03_kitchen", "room_profile": "kitchen",
         "reference_asset": _reference_asset("03_kitchen", "03_kitchen.jpg", "8288dc429c1b0c1f6e00214672b5549e0434114b259ec3383b55b081de431cb6", "42-011345_six_6de11760634d_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172021996, 279876078, "厨房", "42-011345_six_6de11760634d_kjl.tiles", "Land inside the CAD kitchen opposite the cookline with the refrigerator identity visible and circulation collision-free."),
        "focal_length_mm": {"min": 32, "max": 35},
        "must_show": ["hob", "hood", "worktop", "refrigerator identity"],
        # The audited cookline elevation deliberately lets the continuous
        # worktop/cabinet run leave through the lower frame edge.  Requiring
        # the whole run inside the generic 8%-94% safe frame rejected the
        # correct in-kitchen camera and selected an exterior fallback instead.
        # Hob and hood keep the strict global frame; only the worktop may
        # touch the bottom edge.
        "subject_safe_frame_overrides": {
            "worktop": {"x_min": .08, "x_max": .92, "y_min": .08, "y_max": 1.0},
        },
        "hard_constraints": ["Keep the cookline order and refrigerator identity from CAD."]},
        {"slot_id": "master_bed_headwall", "reference_image_id": "04_master_bed", "room_profile": "bedroom_master",
         "reference_asset": _reference_asset("04_master_bed", "04_master_bed.jpg", "5030e3203229648ea419767ca3782df90ca1364f464aafe05bd8c945cdb9ca70", "42-011334_six_4cdaf89e10c3_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172022003, 279876085, "主卧", "42-011334_six_4cdaf89e10c3_kjl.tiles", "Choose clear CAD floor near the bed foot or opposite headwall and aim along the existing bed axis."),
         "focal_length_mm": {"min": 32, "max": 35},
         "must_show": ["bed axis", "CAD circulation clearance"],
         "hard_constraints": ["Keep the bed axis and passage width."]},
        {"slot_id": "secondary_bed_soft_headwall", "reference_image_id": "05_secondary_bed_a", "room_profile": "bedroom_secondary",
         "reference_asset": _reference_asset("05_secondary_bed_a", "05_secondary_bed_a.jpg", "af0990cfe275991dddf3133408644d96fbb92ca22b4cb2108e769d893bc6089b", "42-011339_six_5acea0416efc_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172022001, 279876083, "次卧", "42-011339_six_5acea0416efc_kjl.tiles", "Choose clear floor that shows the bed, circulation and the single CAD window, matched by door/window anchors."),
         "focal_length_mm": {"min": 28, "max": 35},
         "must_show": ["bed", "circulation", "the only CAD window"],
         "subject_safe_frame_overrides": {
             "bed": {"x_min": 0.0, "x_max": 1.0, "y_min": .08, "y_max": 1.0},
         },
         "hard_constraints": ["Do not add a second window."]},
        {"slot_id": "secondary_bed_dark_headwall", "reference_image_id": "06_secondary_bed_b", "room_profile": "bedroom_secondary",
         "reference_asset": _reference_asset("06_secondary_bed_b", "06_secondary_bed_b.jpg", "517652e39c59e71a906be636bca10451b32d853c04a6fa347f34c87841503e5b", "42-011329_six_46112f00a1c0_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172021998, 279876081, "次卧", "42-011329_six_46112f00a1c0_kjl.tiles", "Use the alternate secondary-bedroom bed axis on the common CAD elevation while matching actual door/window anchors."),
         "focal_length_mm": {"min": 32, "max": 35},
         "must_show": ["bed axis", "same floor elevation"],
         "subject_safe_frame_overrides": {
             "bed axis": {"x_min": 0.0, "x_max": 1.0, "y_min": .08, "y_max": 1.0},
         },
         "hard_constraints": ["No fake door, mirror, threshold or floor-level change."]},
        {"slot_id": "master_bath_three_fixture", "reference_image_id": "07_master_bath", "room_profile": "bathroom_master",
         "reference_asset": _reference_asset("07_master_bath", "07_master_bath.jpg", "7cb4dd6e18eab0f5dd67fafc6e8e9152901cff2e4bd7b5af9116408cf6950d27", "42-011319_six_b6ea951f3232_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172021999, 279876080, "主卫", "42-011319_six_b6ea951f3232_kjl.tiles", "Land on the entry/circulation side where exactly one toilet, one shower and one basin remain visible."),
         "focal_length_mm": {"min": 24, "max": 28},
         "must_show": ["one toilet", "one shower", "one basin"],
         "hard_constraints": ["Exactly one basin; preserve all three CAD fixture identities."]},
        {"slot_id": "secondary_bath_toilet_shower", "reference_image_id": "08_secondary_bath", "room_profile": "bathroom_secondary",
         "reference_asset": _reference_asset("08_secondary_bath", "08_secondary_bath.jpg", "fe9b771c0d4d1e3a10d383d3079ccb0f0ca3c540b978428a6c741cea766b77a5", "42-011314_six_f6c0cc40dd15_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172022002, 279876084, "次卫", "42-011314_six_f6c0cc40dd15_kjl.tiles", "Choose clear CAD floor that sees the toilet, shower and real window without forcing an absent basin."),
         "focal_length_mm": {"min": 24, "max": 28},
         "must_show": ["toilet", "shower", "CAD window"],
         "hard_constraints": ["Do not force a basin unless CAD contains one."]},
        {"slot_id": "dry_vanity_front", "reference_image_id": "09_dry_area", "room_profile": "dry_vanity",
         "reference_asset": _reference_asset("09_dry_area", "09_dry_area.jpg", "dfc0b167c488ea6525e9635b7677219d48adfb016c5a0728ab891bdf1cd6dd93", "42-011324_six_80b4302bb7de_kjl.tiles"),
         "reference_viewpoint": _reference_viewpoint(172022004, 279876086, "干区", "42-011324_six_80b4302bb7de_kjl.tiles", "Land on the circulation side opposite the single CAD vanity and mirror relationship."),
         "focal_length_mm": {"min": 32, "max": 35},
         "must_show": ["exactly one basin", "exactly one faucet", "CAD-authentic mirror relationship"],
         "hard_constraints": ["No duplicated basin, faucet or false mirrored opening."]},
    ],
}


def reference_contract_for_url(reference_url: str) -> dict:
    value = str(reference_url or "").strip()
    if JUSTEASY_REFERENCE_URL_TOKEN in value and "justeasy" in value.lower():
        return copy.deepcopy(JUSTEASY_REFERENCE_CONTRACT)
    return {}


def reference_slot_for_room(contract: dict, room: dict, camera: Optional[dict] = None, *,
                            reference_slot_id: str = "", require_explicit: bool = False) -> dict:
    if not contract:
        return {}
    slots = {str(row.get("slot_id")): row for row in contract.get("slots") or []}
    explicit = str(reference_slot_id or (camera or {}).get("reference_slot_id") or "").strip()
    if explicit:
        return copy.deepcopy(slots.get(explicit) or {})
    if require_explicit:
        return {}
    text = f"{room.get('label') or ''} {room.get('room_type') or ''} {room.get('reference_room_profile') or ''}".lower()
    desired: list[str]
    if any(token in text for token in ("厨房", "kitchen")):
        desired = ["kitchen_cookline_elevation"]
    elif any(token in text for token in ("主卫", "master bath", "master_bath")):
        desired = ["master_bath_three_fixture"]
    elif any(token in text for token in ("次卫", "secondary bath", "secondary_bath")):
        desired = ["secondary_bath_toilet_shower"]
    elif any(token in text for token in ("干区", "vanity")):
        desired = ["dry_vanity_front"]
    elif any(token in text for token in ("卫生", "bath", "toilet")):
        desired = ["master_bath_three_fixture", "secondary_bath_toilet_shower"]
    elif any(token in text for token in ("主卧", "master bed", "master_bed")):
        desired = ["master_bed_headwall"]
    elif any(token in text for token in ("次卧", "secondary bed", "bedroom")):
        desired = ["secondary_bed_soft_headwall", "secondary_bed_dark_headwall"]
    elif any(token in text for token in ("客厅", "餐厅", "living", "dining")):
        desired = ["living_openplan_axis", "living_tv_window_axis"]
    else:
        desired = []
    # A second camera in the same room can select the alternate listed axis.
    camera_name = str((camera or {}).get("name") or "").lower()
    if len(desired) > 1 and any(token in camera_name for token in ("2", "二", "window", "窗", "dark", "深")):
        desired = desired[1:] + desired[:1]
    return copy.deepcopy(slots.get(desired[0]) or {}) if desired else {}


class CadError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400,
                 details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = copy.deepcopy(details or {})

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


class CadDependencyError(CadError):
    pass


def public_reference_contract(contract: dict) -> dict:
    """Return the reference contract safe for run views, recipes and exports."""
    result = copy.deepcopy(contract or {})
    for slot in result.get("slots") or []:
        asset = slot.get("reference_asset") or {}
        asset.pop("local_path", None)
        if asset.get("public_thumb_url"):
            asset["public_thumb_url"] = _clean_public_url(asset["public_thumb_url"])
        viewpoint = slot.get("reference_viewpoint") or {}
        if viewpoint.get("pano_resource"):
            viewpoint["pano_resource"] = _clean_public_url(viewpoint["pano_resource"])
    return result


def resolve_reference_assets(contract: dict, *, require_all: bool = False,
                             asset_root: str = "") -> dict:
    """Resolve and cryptographically verify pre-positioned reference thumbnails.

    This function performs no network access.  Assets must already exist beneath
    ``<asset_root>/<contract_id>`` using the exact audited filenames.
    """
    result = copy.deepcopy(contract or {})
    if not result:
        return result
    contract_id = str(result.get("contract_id") or "").strip()
    if not contract_id or os.path.basename(contract_id) != contract_id:
        raise CadError("invalid_reference_contract_id", "参考合同 ID 非法", status_code=409)
    root = os.path.realpath(asset_root or REFERENCE_ASSET_ROOT)
    contract_root = os.path.realpath(os.path.join(root, contract_id))
    if os.path.commonpath([root, contract_root]) != root:
        raise CadError("invalid_reference_asset_root", "参考资产目录越界", status_code=409)
    errors: list[dict] = []
    for slot in result.get("slots") or []:
        asset = slot.get("reference_asset") or {}
        asset_id = str(asset.get("asset_id") or "")
        filename = str(asset.get("filename") or "")
        expected_sha = str(asset.get("sha256") or "").lower()
        error: Optional[dict] = None
        if not filename or os.path.basename(filename) != filename:
            error = {"slot_id": slot.get("slot_id"), "asset_id": asset_id, "code": "invalid_filename"}
        else:
            candidate = os.path.realpath(os.path.join(contract_root, filename))
            if os.path.commonpath([contract_root, candidate]) != contract_root:
                error = {"slot_id": slot.get("slot_id"), "asset_id": asset_id, "code": "path_escape"}
            elif not os.path.isfile(candidate):
                error = {"slot_id": slot.get("slot_id"), "asset_id": asset_id, "code": "missing"}
            else:
                actual_sha = sha256_file(candidate)
                if actual_sha.lower() != expected_sha:
                    error = {"slot_id": slot.get("slot_id"), "asset_id": asset_id,
                             "code": "sha256_mismatch", "expected_sha256": expected_sha,
                             "actual_sha256": actual_sha}
                else:
                    try:
                        with Image.open(candidate) as image:
                            width, height = (int(image.size[0]), int(image.size[1]))
                            image_format = str(image.format or "").upper()
                            mime = str(Image.MIME.get(image_format) or "")
                            if width * height > int(asset.get("max_pixels") or REFERENCE_ASSET_MAX_PIXELS):
                                raise ValueError("pixel_limit")
                            if image_format != "JPEG" or mime != str(asset.get("expected_mime") or "image/jpeg"):
                                raise ValueError("mime_mismatch")
                            if width != int(asset.get("expected_width") or 500) or height != int(asset.get("expected_height") or 500):
                                raise ValueError("dimension_mismatch")
                            image.verify()
                    except (UnidentifiedImageError, OSError, ValueError) as exc:
                        error = {"slot_id": slot.get("slot_id"), "asset_id": asset_id,
                                 "code": str(exc) if str(exc) in {"pixel_limit", "mime_mismatch", "dimension_mismatch"} else "invalid_image"}
        if error:
            asset.pop("local_path", None)
            asset["status"] = "error" if error["code"] != "missing" else "unresolved"
            asset["error_code"] = error["code"]
            errors.append(error)
            continue
        asset.update({
            "local_path": candidate,
            "status": "verified",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "width": width,
            "height": height,
            "mime": mime,
        })
        asset.pop("error_code", None)
    if errors and require_all:
        raise CadError(
            "reference_assets_unavailable",
            "参考模式需要全部本地参考缩略图通过文件名、哈希和图像格式校验",
            status_code=409,
            details={"hard_errors": errors, "reference_contract": public_reference_contract(result)},
        )
    return result


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cad_report_summary(report: dict) -> dict:
    """Small project-record pointer; the immutable full report stays on disk."""
    path = os.path.realpath(str(report.get("report_path") or "")) if report.get("report_path") else ""
    return {
        "schema_version": int(report.get("schema_version") or 1),
        "storage": "external_json_v1" if path else "legacy_inline",
        "report_path": path,
        "report_sha256": sha256_file(path) if path and os.path.isfile(path) else "",
        "source_sha256": str(report.get("source_sha256") or ""),
        "insunits": report.get("insunits"),
        "resolved_insunits": report.get("resolved_insunits", report.get("insunits")),
        "declared_unit_scale_to_m": report.get(
            "declared_unit_scale_to_m", report.get("unit_scale_to_m")),
        "unit_scale_to_m": report.get("unit_scale_to_m"),
        "unit_resolution": copy.deepcopy(report.get("unit_resolution") or {}),
        "structural_entity_count": int(report.get("structural_entity_count") or 0),
        "selected_structural_entity_count": int(
            report.get("selected_structural_entity_count") or 0),
        "ignored_nonstructural_count": int(
            report.get("ignored_nonstructural_count") or 0),
        "layer_count": len(report.get("layers") or {}),
        "block_count": len(report.get("blocks") or {}),
        "selected_candidate_id": str(report.get("selected_candidate_id") or ""),
        "candidate_plans": [{
            key: copy.deepcopy(row.get(key)) for key in (
                "candidate_id", "preview_path", "diagnostic_svg_path", "bbox_m",
                "selection_score", "closed_region_count", "structural_entity_count")
        } for row in (report.get("candidate_plans") or [])[:20]],
        "candidate_plan_count": int(report.get("candidate_plan_count") or len(report.get("candidate_plans") or [])),
        "raw_face_count": int(report.get("raw_face_count") or len(report.get("raw_faces") or [])),
        "semantic_preview_path": str(report.get("semantic_preview_path") or ""),
        "artifact_directory": str(report.get("artifact_directory") or ""),
        "alignment_metrics": copy.deepcopy(report.get("alignment_metrics") or {}),
        "selected_entity_role_summary": copy.deepcopy(
            report.get("selected_entity_role_summary") or {}),
        "raw_opening_summary": copy.deepcopy(report.get("raw_opening_summary") or {}),
        "global_wall_topology": copy.deepcopy(report.get("global_wall_topology") or {}),
        "cad_to_model": copy.deepcopy(report.get("cad_to_model") or {}),
        "model_to_cad": copy.deepcopy(report.get("model_to_cad") or {}),
        "hard_error_summary": [{
            "code": str(row.get("code") or ""), "message": str(row.get("message") or "")[:300],
        } for row in (report.get("hard_errors") or [])[:50]],
        "warning_summary": [{
            "code": str(row.get("code") or ""), "message": str(row.get("message") or "")[:300],
        } for row in (report.get("warnings") or [])[:50]],
    }


def persist_cad_report(project_id: str, report: dict, purpose: str = "derived") -> dict:
    value = copy.deepcopy(report)
    root = _asset_directory(project_id, f"{purpose}_{uuid.uuid4().hex[:12]}")
    path = os.path.join(root, "parse_report.json")
    value["artifact_directory"] = root
    value.pop("report_path", None)
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    value["report_path"] = path
    return value


def load_cad_report(value: dict) -> dict:
    """Read a new external report or return a copy of a legacy inline report."""
    if not isinstance(value, dict):
        return {}
    if value.get("storage") != "external_json_v1":
        return copy.deepcopy(value)
    path = os.path.realpath(str(value.get("report_path") or ""))
    root = os.path.realpath(CAD_ROOT)
    if (not path or not os.path.isfile(path)
            or os.path.commonpath([root, path]) != root
            or sha256_file(path) != str(value.get("report_sha256") or "")):
        raise CadError("cad_report_integrity_failed", "CAD 完整解析报告缺失或哈希不一致", status_code=409)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except Exception as ex:
        raise CadError("cad_report_read_failed", "CAD 完整解析报告无法读取", status_code=409) from ex
    if not isinstance(report, dict):
        raise CadError("cad_report_invalid", "CAD 完整解析报告格式无效", status_code=409)
    report["report_path"] = path
    return report


def save_cad_draft_model(project_id: str, model: dict, artifact_directory: str = "") -> dict:
    root = os.path.realpath(artifact_directory or _asset_directory(project_id, "drafts"))
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"space_draft_{uuid.uuid4().hex[:12]}.json")
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(model, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    return {"storage": "external_json_v1", "path": path, "sha256": sha256_file(path)}


def load_cad_draft_model(pointer: dict) -> dict:
    if not isinstance(pointer, dict) or pointer.get("storage") != "external_json_v1":
        return {}
    path = os.path.realpath(str(pointer.get("path") or ""))
    root = os.path.realpath(CAD_ROOT)
    if (not path or not os.path.isfile(path) or os.path.commonpath([root, path]) != root
            or sha256_file(path) != str(pointer.get("sha256") or "")):
        raise CadError("cad_draft_integrity_failed", "CAD 人工空间草稿缺失或哈希不一致", status_code=409)
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise CadError("cad_draft_invalid", "CAD 人工空间草稿格式无效", status_code=409)
    return value


def inspect_cad_file(path: str) -> dict:
    extension = os.path.splitext(path)[1].lower()
    with open(path, "rb") as handle:
        prefix = handle.read(1024 * 1024)
    if extension == ".dwg":
        signature = prefix[:6].decode("ascii", errors="ignore")
        if not re.fullmatch(r"AC\d{4}", signature):
            raise CadError("invalid_dwg_magic", "文件扩展名为 DWG，但缺少有效 AC 版本魔数")
        return {
            "format": "dwg", "version": signature,
            "version_name": DWG_VERSION_NAMES.get(signature, "unknown"),
            "sha256": sha256_file(path), "size_bytes": os.path.getsize(path),
        }
    if extension == ".dxf":
        if prefix.startswith(b"AutoCAD Binary DXF\r\n\x1a\x00"):
            return {
                "format": "dxf", "encoding": "binary", "version": "", "version_name": "",
                "sha256": sha256_file(path), "size_bytes": os.path.getsize(path),
            }
        text = prefix.decode("utf-8", errors="ignore") or prefix.decode("latin1", errors="ignore")
        normalized = text.upper().replace("\r", "")
        if "SECTION" not in normalized or not any(token in normalized for token in ("HEADER", "ENTITIES")):
            raise CadError("invalid_dxf_text", "文件扩展名为 DXF，但不是可识别的 ASCII DXF")
        version_match = re.search(r"\$ACADVER\s*\n\s*1\s*\n\s*([^\s]+)", normalized)
        return {
            "format": "dxf", "encoding": "ascii", "version": (version_match.group(1) if version_match else ""),
            "version_name": DWG_VERSION_NAMES.get(version_match.group(1), "") if version_match else "",
            "sha256": sha256_file(path), "size_bytes": os.path.getsize(path),
        }
    raise CadError("unsupported_cad_extension", "CAD 仅支持 .dwg 或 .dxf")


def save_cad_upload(file: Any, *, max_bytes: int = 100 * 1024 * 1024) -> dict:
    original = os.path.basename(str(getattr(file, "filename", "") or "cad.dxf"))
    extension = os.path.splitext(original)[1].lower()
    if extension not in {".dwg", ".dxf"}:
        raise CadError("unsupported_cad_extension", "CAD 仅支持 .dwg 或 .dxf")
    stem = re.sub(r"[^\w.-]+", "_", os.path.splitext(original)[0], flags=re.UNICODE).strip("._")[:80] or "cad"
    destination = os.path.join(UPLOAD_DIR, f"cad_{stem}_{uuid.uuid4().hex[:12]}{extension}")
    temporary = f"{destination}.{uuid.uuid4().hex}.upload"
    total = 0
    try:
        with open(temporary, "xb") as output:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise CadError("cad_upload_too_large", "CAD 文件超过 100 MiB 上限", status_code=413)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        # inspect_cad_file dispatches on extension, while the temporary suffix is
        # intentionally not user controlled.  Validate through an isolated name
        # with the authoritative extension before the atomic replace.
        validation_path = f"{temporary}{extension}"
        os.replace(temporary, validation_path)
        temporary = validation_path
        metadata = inspect_cad_file(temporary)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
    return {"path": destination, "name": os.path.basename(destination), **metadata}


def require_managed_cad_path(path: str) -> str:
    try:
        resolved = os.path.realpath(str(path or ""))
        base = os.path.realpath(UPLOAD_DIR)
        if os.path.commonpath([resolved, base]) != base:
            raise ValueError
    except Exception as ex:
        raise CadError("invalid_cad_path", "CAD 路径无效，请重新上传") from ex
    if not os.path.isfile(resolved):
        raise CadError("missing_cad_file", "CAD 文件已失效，请重新上传")
    inspect_cad_file(resolved)
    return resolved


def detect_oda_executable(environ: Optional[dict[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    candidates: list[str] = []
    for name in ODA_ENV_NAMES:
        if env.get(name):
            candidates.append(env[name])
    program_files = [env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), r"C:\Program Files"]
    for root in dict.fromkeys(value for value in program_files if value):
        candidates.extend([
            os.path.join(root, "ODA", "ODAFileConverter", "ODAFileConverter.exe"),
            os.path.join(root, "ODA", "ODA File Converter", "ODAFileConverter.exe"),
            os.path.join(root, "Open Design Alliance", "ODAFileConverter", "ODAFileConverter.exe"),
        ])
        oda_root = os.path.join(root, "ODA")
        try:
            for name in sorted(os.listdir(oda_root), reverse=True):
                candidates.append(os.path.join(oda_root, name, "ODAFileConverter.exe"))
        except OSError:
            pass
    for candidate in candidates:
        path = os.path.realpath(os.path.expandvars(str(candidate).strip().strip('"')))
        if os.path.isfile(path) and os.path.basename(path).lower() == "odafileconverter.exe":
            return path
    return ""


def dwg_commercial_use_authorized(environ: Optional[dict[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(DWG_COMMERCIAL_AUTH_ENV) or "").strip().lower() == "true"


def _existing_file(candidates: Iterable[str]) -> str:
    for candidate in candidates:
        path = os.path.realpath(os.path.expandvars(str(candidate or "").strip().strip('"')))
        if os.path.isfile(path):
            return path
    return ""


def detect_dotnet_host(environ: Optional[dict[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    executable = "dotnet.exe" if os.name == "nt" else "dotnet"
    configured = str(env.get(DOTNET_HOST_ENV_NAME) or "")
    local = os.path.join(PROJECT_ROOT, ".tools", "dotnet", executable)
    system = shutil.which(executable, path=env.get("PATH")) or ""
    return _existing_file([configured, local, system])


def detect_acadsharp_converter(environ: Optional[dict[str, str]] = None) -> dict:
    env = os.environ if environ is None else environ
    configured = str(env.get(ACADSHARP_ENV_NAME) or "")
    candidates = [
        configured,
        os.path.join(PROJECT_ROOT, "tools", "acadsharp_dwg_converter", "runtime", ACADSHARP_ASSEMBLY_NAME),
        os.path.join(PROJECT_ROOT, "tools", "acadsharp_dwg_converter", "bin", "Release", "net8.0",
                     ACADSHARP_ASSEMBLY_NAME),
    ]
    tool = _existing_file(candidates)
    if not tool:
        return {}
    if tool.lower().endswith(".exe"):
        return {"kind": "exe", "tool_path": tool, "host_path": "", "license": "MIT"}
    if not tool.lower().endswith(".dll"):
        return {}
    host = detect_dotnet_host(env)
    if not host:
        return {}
    return {"kind": "dotnet_dll", "tool_path": tool, "host_path": host, "license": "MIT"}


def cad_runtime_status() -> dict:
    acadsharp = detect_acadsharp_converter()
    oda_path = detect_oda_executable()
    try:
        # Reading distribution metadata avoids importing ezdxf.  Its first
        # import performs a platform probe on Windows, which may spawn `ver`;
        # authorization-denied DWG paths are contractually zero-subprocess.
        ezdxf_version = importlib.metadata.version("ezdxf")
    except Exception:
        ezdxf_version = ""
    try:
        shapely_version = importlib.metadata.version("Shapely")
    except Exception:
        shapely_version = ""
    parser_ready = bool(ezdxf_version and shapely_version)
    oda_authorized = bool(oda_path and dwg_commercial_use_authorized())
    converter_available = bool(acadsharp or oda_path)
    commercially_usable = bool(acadsharp or oda_authorized)
    adapter = "acadsharp_mit_v1" if acadsharp else "oda_file_converter_v1"
    return {
        "ready_for_dxf": parser_ready,
        "ready_for_dwg": bool(parser_ready and commercially_usable),
        "ezdxf_available": bool(ezdxf_version), "ezdxf_version": ezdxf_version,
        "shapely_available": bool(shapely_version), "shapely_version": shapely_version,
        "converter_available": converter_available, "commercial_use_authorized": commercially_usable,
        "converter_adapter": adapter,
        "converter_license": "MIT" if acadsharp else ("commercial_authorization" if oda_authorized else ""),
        "acadsharp_available": bool(acadsharp), "oda_available": bool(oda_path),
        "converter_configuration": {
            "acadsharp_path_env": ACADSHARP_ENV_NAME, "dotnet_host_env": DOTNET_HOST_ENV_NAME,
            "path_env_names": list(ODA_ENV_NAMES), "commercial_authorization_env": DWG_COMMERCIAL_AUTH_ENV,
        },
    }


def _asset_directory(project_id: str, token: str) -> str:
    safe_project = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.basename(project_id))[:100] or "project"
    safe_token = re.sub(r"[^A-Za-z0-9_-]+", "_", token)[:100] or uuid.uuid4().hex
    path = os.path.join(CAD_ROOT, safe_project, safe_token)
    os.makedirs(path, exist_ok=True)
    return path


def _convert_dwg_with_acadsharp(source_path: str, project_id: str, adapter: dict,
                                *, timeout: float) -> tuple[str, dict]:
    metadata = inspect_cad_file(source_path)
    root = _asset_directory(project_id, f"convert_acadsharp_{uuid.uuid4().hex[:12]}")
    input_dir, output_dir = os.path.join(root, "input"), os.path.join(root, "output")
    os.makedirs(input_dir, exist_ok=False)
    os.makedirs(output_dir, exist_ok=False)
    isolated_source = os.path.join(input_dir, os.path.basename(source_path))
    shutil.copy2(source_path, isolated_source)
    expected = os.path.join(output_dir, os.path.splitext(os.path.basename(source_path))[0] + ".dxf")
    tool_path = os.path.realpath(str(adapter.get("tool_path") or ""))
    host_path = os.path.realpath(str(adapter.get("host_path") or "")) if adapter.get("host_path") else ""
    if adapter.get("kind") == "dotnet_dll":
        args = [host_path, tool_path, isolated_source, expected]
    elif adapter.get("kind") == "exe":
        args = [tool_path, isolated_source, expected]
    else:
        raise CadDependencyError("acadsharp_invalid_adapter", "ACadSharp 转换器配置无效", status_code=503)
    started = time.time()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        args, shell=False, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(timeout=max(1.0, min(float(timeout), 600.0)))
    except subprocess.TimeoutExpired as ex:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], shell=False,
                           capture_output=True, check=False, creationflags=creationflags)
        else:
            process.kill()
        process.communicate()
        raise CadError("acadsharp_timeout", "ACadSharp 转换超时，已终止转换器进程树",
                       status_code=504, details={"timeout_seconds": timeout}) from ex
    payload: dict = {}
    for line in reversed((stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
        except Exception:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    evidence = {
        "status": "done" if process.returncode == 0 and payload.get("ok") is True else "failed",
        "adapter": "acadsharp_mit_v1", "license": "MIT",
        "adapter_version": str(payload.get("adapter_version") or ""),
        "source_version": str(payload.get("source_version") or metadata.get("version") or ""),
        "tool_sha256": sha256_file(tool_path), "returncode": process.returncode,
        "seconds": round(time.time() - started, 3), "stderr": (stderr or "")[-4000:],
        "source": metadata,
    }
    if process.returncode != 0 or payload.get("ok") is not True:
        evidence["converter_error"] = {
            "code": str(payload.get("code") or "conversion_failed"),
            "message": str(payload.get("message") or "ACadSharp 未返回成功结果"),
            "exception_type": str(payload.get("exception_type") or ""),
        }
        raise CadError("acadsharp_failed", "ACadSharp 转换失败，未进入任何 AI 步骤", details=evidence)
    if not os.path.isfile(expected):
        raise CadError("acadsharp_output_missing", "ACadSharp 未生成 DXF 输出", details=evidence)
    inspect_cad_file(expected)
    evidence.update(output_path=expected, output_sha256=sha256_file(expected),
                    output_bytes=os.path.getsize(expected))
    return expected, evidence


def convert_dwg_to_ascii_dxf(source_path: str, project_id: str, *, timeout: float = 120.0,
                             executable: str = "") -> tuple[str, dict]:
    metadata = inspect_cad_file(source_path)
    if metadata["format"] != "dwg":
        return source_path, {"status": "not_required", "source": metadata}
    if not executable:
        acadsharp = detect_acadsharp_converter()
        if acadsharp:
            return _convert_dwg_with_acadsharp(source_path, project_id, acadsharp, timeout=timeout)
    oda = executable or detect_oda_executable()
    if not oda:
        raise CadDependencyError(
            "dwg_converter_missing",
            "未检测到 ACadSharp 本地转换器或已配置的 DWG 转换器",
            status_code=503, details={"runtime": cad_runtime_status()})
    if not dwg_commercial_use_authorized():
        raise CadDependencyError(
            "dwg_converter_commercial_authorization_required",
            "DWG 转换器尚未声明商业使用授权；请由获得商业授权的 CAD 软件导出 DXF，或在确认许可证后显式设置授权标志",
            status_code=403, details={"runtime": cad_runtime_status()})
    if os.path.basename(os.path.realpath(oda)).lower() != "odafileconverter.exe" or not os.path.isfile(oda):
        raise CadDependencyError("oda_invalid_executable", "ODA 可执行文件无效", status_code=503)
    root = _asset_directory(project_id, f"convert_{uuid.uuid4().hex[:12]}")
    input_dir, output_dir = os.path.join(root, "input"), os.path.join(root, "output")
    os.makedirs(input_dir, exist_ok=False)
    os.makedirs(output_dir, exist_ok=False)
    isolated_source = os.path.join(input_dir, os.path.basename(source_path))
    shutil.copy2(source_path, isolated_source)
    # ODA documented CLI: input, output, target version, type, recursive, audit, optional filter.
    args = [os.path.realpath(oda), input_dir, output_dir, "ACAD2018", "DXF", "0", "1", os.path.basename(isolated_source)]
    started = time.time()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        args, shell=False, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(timeout=max(1.0, min(float(timeout), 600.0)))
    except subprocess.TimeoutExpired as ex:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           shell=False, capture_output=True, check=False,
                           creationflags=creationflags)
        else:
            process.kill()
        process.communicate()
        raise CadError("oda_timeout", "ODA 转换超时，已终止转换器进程树", status_code=504,
                       details={"timeout_seconds": timeout}) from ex
    evidence = {
        "status": "done" if process.returncode == 0 else "failed",
        "executable": os.path.realpath(oda), "args": args[1:],
        "returncode": process.returncode, "seconds": round(time.time() - started, 3),
        "stdout": stdout[-4000:], "stderr": stderr[-4000:],
        "source": metadata,
    }
    if process.returncode != 0:
        raise CadError("oda_failed", "ODA 转换失败，未进入任何 AI 步骤", details=evidence)
    expected = os.path.join(output_dir, os.path.splitext(os.path.basename(source_path))[0] + ".dxf")
    if not os.path.isfile(expected):
        matches = [os.path.join(output_dir, name) for name in os.listdir(output_dir)
                   if name.lower().endswith(".dxf")]
        expected = matches[0] if len(matches) == 1 else ""
    if not expected:
        raise CadError("oda_output_missing", "ODA 未生成唯一 DXF 输出", details=evidence)
    inspect_cad_file(expected)
    evidence.update(output_path=expected, output_sha256=sha256_file(expected))
    return expected, evidence


def _repair_legacy_cad_text(value: str) -> str:
    """Undo the GBK-as-Latin-1 mojibake emitted by some R2004 readers.

    A candidate is accepted only when it adds CJK characters without adding a
    replacement marker, so ordinary English CAD names remain byte-for-byte
    unchanged.
    """
    original = str(value or "")
    original_cjk = len(re.findall(r"[\u3400-\u9fff]", original))
    for encoding in ("latin1", "cp1252"):
        try:
            candidate = original.encode(encoding).decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidate_cjk = len(re.findall(r"[\u3400-\u9fff]", candidate))
        if "\ufffd" not in candidate and candidate_cjk > original_cjk:
            return candidate
    return original


def _sanitize_dxf_defaults(document: Any) -> dict:
    """Remove explicit zero-valued DXF defaults that break block transforms.

    Some DWG readers serialize a missing/default thickness or extrusion vector
    as an explicit all-zero value.  ezdxf rightfully treats a zero extrusion as
    invalid when transforming INSERT children.  Discarding only these exact
    defaults restores the DXF default (thickness=0, extrusion=(0, 0, 1)) and
    does not change any 2D coordinates, scale, rotation, or visible geometry.
    """
    zero_thickness_removed = 0
    zero_extrusion_removed = 0
    entitydb = getattr(document, "entitydb", None)
    entities = list(entitydb.values()) if entitydb is not None else []
    for entity in entities:
        namespace = getattr(entity, "dxf", None)
        if namespace is None:
            continue
        try:
            if namespace.hasattr("thickness") and abs(float(namespace.thickness)) <= 1e-12:
                namespace.discard("thickness")
                zero_thickness_removed += 1
        except (AttributeError, TypeError, ValueError):
            pass
        try:
            if namespace.hasattr("extrusion"):
                extrusion = namespace.extrusion
                magnitude = math.sqrt(
                    float(extrusion.x) ** 2 + float(extrusion.y) ** 2 + float(extrusion.z) ** 2
                )
                if magnitude <= 1e-12:
                    namespace.discard("extrusion")
                    zero_extrusion_removed += 1
        except (AttributeError, TypeError, ValueError):
            pass
    return {
        "method": "discard_explicit_zero_dxf_defaults_v1",
        "zero_thickness_removed": zero_thickness_removed,
        "zero_extrusion_removed": zero_extrusion_removed,
        "geometry_fields_changed": [],
    }


def _provenance(entity: Any, *, source_kind: str, parent: Optional[dict] = None) -> dict:
    dxf = getattr(entity, "dxf", None)
    source_handle = str(getattr(dxf, "handle", "") or "")
    root_handle = str((parent or {}).get("root_handle") or source_handle)
    encoded_layer = str(getattr(dxf, "layer", "") or "0")
    raw_layer = _repair_legacy_cad_text(encoded_layer)
    inherited_layer = str((parent or {}).get("effective_layer") or (parent or {}).get("layer") or "")
    effective_layer = raw_layer if raw_layer not in ("", "0") else (inherited_layer or "0")
    block = str((parent or {}).get("block") or "")
    result = {
        "handle": root_handle or source_handle, "root_handle": root_handle,
        "source_handle": source_handle, "layer": effective_layer,
        "raw_layer": raw_layer, "effective_layer": effective_layer, "block": block,
        "insert_chain": copy.deepcopy((parent or {}).get("insert_chain") or []),
        "source_kind": source_kind, "transform": copy.deepcopy((parent or {}).get("transform") or []),
        "confidence": 1.0,
    }
    if encoded_layer != raw_layer:
        result["encoded_layer"] = encoded_layer
    return result


def _insert_parent(entity: Any, inherited: Optional[dict]) -> dict:
    dxf = entity.dxf
    insert = getattr(dxf, "insert", None)
    handle = str(getattr(dxf, "handle", "") or "")
    encoded_layer = str(getattr(dxf, "layer", "") or "0")
    raw_layer = _repair_legacy_cad_text(encoded_layer)
    inherited_layer = str((inherited or {}).get("effective_layer") or (inherited or {}).get("layer") or "")
    effective_layer = raw_layer if raw_layer not in ("", "0") else (inherited_layer or "0")
    transform = {
        "insert": [float(insert.x), float(insert.y), float(getattr(insert, "z", 0.0))] if insert is not None else [],
        "xscale": float(getattr(dxf, "xscale", 1.0) or 1.0),
        "yscale": float(getattr(dxf, "yscale", 1.0) or 1.0),
        "zscale": float(getattr(dxf, "zscale", 1.0) or 1.0),
        "rotation_deg": float(getattr(dxf, "rotation", 0.0) or 0.0),
    }
    block_name = _repair_legacy_cad_text(str(getattr(dxf, "name", "") or ""))
    anonymous_evaluated = block_name.upper().startswith("*U")
    result = {
        "root_handle": str((inherited or {}).get("root_handle") or handle),
        "layer": effective_layer, "raw_layer": raw_layer, "effective_layer": effective_layer,
        "block": block_name,
        "anonymous_block_evaluated": anonymous_evaluated,
        "transform": [*copy.deepcopy((inherited or {}).get("transform") or []), transform],
        "insert_chain": [*copy.deepcopy((inherited or {}).get("insert_chain") or []), {
            "handle": handle, "block": block_name,
            "raw_layer": raw_layer, "effective_layer": effective_layer,
            "transform": transform,
            "anonymous_block_evaluated": anonymous_evaluated,
        }],
    }
    if encoded_layer != raw_layer:
        result["encoded_layer"] = encoded_layer
    return result


def _expanded_entities(entities: Iterable[Any], parent: Optional[dict] = None) -> Iterable[tuple[Any, dict]]:
    for entity in entities:
        if entity.dxftype() == "INSERT":
            if int(getattr(entity, "mcount", 1) or 1) > 1:
                try:
                    insert_parent = _insert_parent(entity, parent)
                    for sub_insert in entity.multi_insert():
                        yield from _expanded_entities([sub_insert], insert_parent)
                except Exception as ex:
                    raise CadError("cad_minsert_expansion_failed", f"MINSERT 展开失败: {ex}") from ex
                continue
            insert_parent = _insert_parent(entity, parent)
            yield entity, _provenance(entity, source_kind="INSERT", parent=insert_parent)
            try:
                yield from _expanded_entities(entity.virtual_entities(), insert_parent)
            except Exception as ex:
                raise CadError("insert_expansion_failed", f"块 {insert_parent['block']} 展开失败: {ex}") from ex
        else:
            yield entity, _provenance(entity, source_kind=entity.dxftype(), parent=parent)


def _entity_points(entity: Any, scale: float, chord_error_m: float) -> list[tuple[float, float]]:
    kind = entity.dxftype()
    if kind == "LINE":
        return [(float(entity.dxf.start.x) * scale, float(entity.dxf.start.y) * scale),
                (float(entity.dxf.end.x) * scale, float(entity.dxf.end.y) * scale)]
    if kind not in {"LWPOLYLINE", "POLYLINE", "ARC", "SPLINE", "CIRCLE", "ELLIPSE"}:
        return []
    try:
        from ezdxf.path import make_path  # type: ignore
        path = make_path(entity)
        return [(float(point.x) * scale, float(point.y) * scale)
                for point in path.flattening(distance=max(chord_error_m / scale, 1e-9), segments=8)]
    except Exception:
        if kind == "LWPOLYLINE":
            return [(float(point[0]) * scale, float(point[1]) * scale) for point in entity.get_points("xy")]
        if kind == "POLYLINE":
            return [(float(vertex.dxf.location.x) * scale, float(vertex.dxf.location.y) * scale)
                    for vertex in entity.vertices]
        return []


def _closed_entity(entity: Any) -> bool:
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        return bool(entity.closed)
    if kind == "POLYLINE":
        return bool(entity.is_closed)
    return kind in {"CIRCLE"}


def _poly_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(sum(points[i][0] * points[(i + 1) % len(points)][1]
                   - points[(i + 1) % len(points)][0] * points[i][1]
                   for i in range(len(points))) / 2.0)


def _point_inside(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if ((first[1] > y) != (second[1] > y)
                and x < (second[0] - first[0]) * (y - first[1]) / ((second[1] - first[1]) or 1e-12) + first[0]):
            inside = not inside
    return inside


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _polygonal_topology_closed(geometry: Any) -> bool:
    """Return true when every polygon shell and hole is a valid closed ring.

    Shapely exposes a Polygon-with-holes boundary as a MultiLineString.  Testing
    ``Polygon.boundary.is_ring`` therefore reports false even though both the
    exterior and every interior ring are individually closed.  Inspect the
    polygon rings themselves, and apply the same rule to every MultiPolygon
    component.
    """
    if geometry is None or getattr(geometry, "is_empty", True):
        return False
    if getattr(geometry, "geom_type", "") == "Polygon":
        polygons = [geometry]
    elif getattr(geometry, "geom_type", "") == "MultiPolygon":
        polygons = list(getattr(geometry, "geoms", ()))
    else:
        return False
    return bool(polygons) and all(
        bool(getattr(polygon, "is_valid", False))
        and bool(getattr(polygon.exterior, "is_ring", False))
        and all(bool(getattr(ring, "is_ring", False)) for ring in polygon.interiors)
        for polygon in polygons
    )


def _bbox_distance(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    dx = max(left[0] - right[2], right[0] - left[2], 0.0)
    dy = max(left[1] - right[3], right[1] - left[3], 0.0)
    return math.hypot(dx, dy)


def _cluster_geometry(rows: list[dict], tolerance_m: float = 0.35) -> list[list[int]]:
    parent = list(range(len(rows)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    # Candidate decomposition runs before wall-layer semantics and can receive
    # thousands of expanded block primitives.  The former all-pairs scan made
    # that path quadratic.  STRtree keeps the exact bbox-distance contract but
    # limits comparisons to spatial neighbours.
    try:
        from shapely.geometry import box  # type: ignore
        from shapely.strtree import STRtree  # type: ignore
        boxes = [box(*row["bbox"]) for row in rows]
        tree = STRtree(boxes)
        identity = {id(value): index for index, value in enumerate(boxes)}
        for left, value in enumerate(boxes):
            query = value.buffer(tolerance_m, cap_style=3, join_style=2)
            for match in tree.query(query):
                if isinstance(match, int) or hasattr(match, "item"):
                    right = int(match)
                else:
                    right = identity.get(id(match), -1)
                if right <= left:
                    continue
                if _bbox_distance(rows[left]["bbox"], rows[right]["bbox"]) > tolerance_m:
                    continue
                a, b = root(left), root(right)
                if a != b:
                    parent[b] = a
    except Exception:
        # Older Shapely distributions have no STRtree query-index contract.
        # A sweep-line fallback remains sub-quadratic for separated drawings.
        ordered = sorted(range(len(rows)), key=lambda index: rows[index]["bbox"][0])
        for position, left in enumerate(ordered):
            right_limit = rows[left]["bbox"][2] + tolerance_m
            for right in ordered[position + 1:]:
                if rows[right]["bbox"][0] > right_limit:
                    break
                if _bbox_distance(rows[left]["bbox"], rows[right]["bbox"]) > tolerance_m:
                    continue
                a, b = root(left), root(right)
                if a != b:
                    parent[b] = a
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        groups[root(index)].append(index)
    return list(groups.values())


def _prove_attached_exterior_double_boundary(
    geometry: Sequence[Mapping[str, Any]],
    selected_indexes: Sequence[int],
    selected_bounds: Sequence[float],
) -> dict:
    """Prove an attached balcony/terrace bay from two source boundary chains.

    The plan candidate is deliberately based on authoritative structural
    geometry, so a balcony drawn on layer 0 can sit just outside its bbox.  We
    expand the candidate only for a pair of non-branching U-shaped chains that
    attach twice to the same candidate side, are nested, and remain at a
    measured 60--600 mm offset.  One chain or an unattached plot frame is never
    enough.  Names, layers and colours do not participate in the proof.
    """
    try:
        from shapely.geometry import LineString, Point, Polygon, box  # type: ignore
        from shapely.ops import linemerge, unary_union  # type: ignore
    except Exception:
        return {"schema_version": 1,
                "method": "cad_attached_exterior_double_boundary_v1",
                "status": "unresolved", "spaces": [],
                "promoted_entity_indexes": []}
    try:
        min_x, min_y, max_x, max_y = [float(value) for value in selected_bounds]
    except (TypeError, ValueError):
        return {"schema_version": 1,
                "method": "cad_attached_exterior_double_boundary_v1",
                "status": "unresolved", "spaces": [],
                "promoted_entity_indexes": []}
    selected_set = {int(value) for value in selected_indexes}
    expanded_limit = box(min_x - 3.0, min_y - 3.0, max_x + 3.0, max_y + 3.0)
    selected_box = box(min_x, min_y, max_x, max_y)
    segments: list[dict] = []
    for fallback, row in enumerate(geometry):
        index = int(row.get("entity_index", fallback))
        if index in selected_set:
            continue
        if str(row.get("entity_type") or "") not in {
                "LINE", "LWPOLYLINE", "POLYLINE"}:
            continue
        points = row.get("points") or []
        if len(points) != 2:
            continue
        try:
            first = (float(points[0][0]), float(points[0][1]))
            second = (float(points[1][0]), float(points[1][1]))
        except (TypeError, ValueError, IndexError):
            continue
        line = LineString([first, second])
        if not .03 <= line.length <= 8.0 or not expanded_limit.intersects(line):
            continue
        # At least 50 mm of the source must lie outside the structural bbox.
        outside_length = float(line.difference(
            selected_box.buffer(.01, join_style=2)).length)
        if outside_length < min(.05, float(line.length) * .80):
            continue
        segments.append({"index": index, "row": row, "line": line,
                         "first": first, "second": second})
    if len(segments) < 6:
        return {"schema_version": 1,
                "method": "cad_attached_exterior_double_boundary_v1",
                "status": "unresolved", "spaces": [],
                "promoted_entity_indexes": []}

    parent = list(range(len(segments)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    endpoint_cells: dict[tuple[int, int], list[tuple[int, tuple[float, float]]]] = {}
    cell_size = .02
    for number, segment in enumerate(segments):
        for endpoint in (segment["first"], segment["second"]):
            cell = (math.floor(endpoint[0] / cell_size),
                    math.floor(endpoint[1] / cell_size))
            for x_offset in (-1, 0, 1):
                for y_offset in (-1, 0, 1):
                    for other_number, other_endpoint in endpoint_cells.get(
                            (cell[0] + x_offset, cell[1] + y_offset), []):
                        if math.dist(endpoint, other_endpoint) <= .02 + 1e-9:
                            union(number, other_number)
            endpoint_cells.setdefault(cell, []).append((number, endpoint))
    components: dict[int, list[dict]] = defaultdict(list)
    for number, segment in enumerate(segments):
        components[find(number)].append(segment)

    def attachment_side(point: tuple[float, float]) -> str:
        distances = {
            "left": abs(point[0] - min_x), "right": abs(point[0] - max_x),
            "bottom": abs(point[1] - min_y), "top": abs(point[1] - max_y),
        }
        side = min(distances, key=distances.get)
        return side if distances[side] <= .02 + 1e-9 else ""

    chains: list[dict] = []
    for rows in components.values():
        if not 3 <= len(rows) <= 20:
            continue
        merged = linemerge(unary_union([row["line"] for row in rows]))
        if getattr(merged, "geom_type", "") != "LineString":
            continue
        coordinates = [tuple(map(float, point)) for point in merged.coords]
        if len(coordinates) < 4 or not 1.0 <= merged.length <= 30.0:
            continue
        first_side = attachment_side(coordinates[0])
        last_side = attachment_side(coordinates[-1])
        if not first_side or first_side != last_side:
            continue
        closed_polygon = Polygon(coordinates)
        if (not closed_polygon.is_valid or not .5 <= closed_polygon.area <= 50.0
                or closed_polygon.intersection(selected_box).area > .05):
            continue
        span = {
            "left": min_x - min(point[0] for point in coordinates),
            "right": max(point[0] for point in coordinates) - max_x,
            "bottom": min_y - min(point[1] for point in coordinates),
            "top": max(point[1] for point in coordinates) - max_y,
        }[first_side]
        if not .60 <= span <= 3.0:
            continue
        chains.append({
            "rows": rows, "line": merged, "coordinates": coordinates,
            "polygon": closed_polygon, "side": first_side, "span_m": span,
            "indexes": sorted(int(row["index"]) for row in rows),
        })

    matches: list[dict] = []
    for left_number, left in enumerate(chains):
        for right in chains[left_number + 1:]:
            if left["side"] != right["side"]:
                continue
            outer, inner = (left, right) if left["polygon"].contains(
                right["polygon"]) else (right, left) if right["polygon"].contains(
                    left["polygon"]) else (None, None)
            if outer is None or inner is None:
                continue
            separation = float(outer["line"].distance(inner["line"]))
            endpoint_error = min(
                max(math.dist(outer["coordinates"][0], inner["coordinates"][0]),
                    math.dist(outer["coordinates"][-1], inner["coordinates"][-1])),
                max(math.dist(outer["coordinates"][0], inner["coordinates"][-1]),
                    math.dist(outer["coordinates"][-1], inner["coordinates"][0])),
            )
            if (not .06 <= separation <= .60
                    or endpoint_error > .25 + 1e-9
                    or abs(outer["line"].length - inner["line"].length) > .80):
                continue
            matches.append({
                "outer": outer, "inner": inner,
                "separation_m": separation,
                "endpoint_error_m": endpoint_error,
            })
    used_chains: set[int] = set()
    spaces: list[dict] = []
    promoted: set[int] = set()
    for match in sorted(matches, key=lambda value: (
            -float(value["outer"]["polygon"].area),
            float(value["endpoint_error_m"]),
            tuple(value["outer"]["indexes"]))):
        chain_ids = {id(match["outer"]), id(match["inner"])}
        if chain_ids.intersection(used_chains):
            continue
        outer, inner = match["outer"], match["inner"]
        source_rows = [row["row"] for row in outer["rows"] + inner["rows"]]
        source_indexes = sorted(set(outer["indexes"] + inner["indexes"]))
        handles = sorted({str((row.get("cad_provenance") or {}).get(
            "source_handle") or (row.get("cad_provenance") or {}).get(
                "root_handle") or "") for row in source_rows
            if str((row.get("cad_provenance") or {}).get("source_handle")
                   or (row.get("cad_provenance") or {}).get("root_handle") or "")})
        if len(handles) < 6:
            continue
        space_id = f"cad_attached_exterior_space_{len(spaces) + 1}"
        spaces.append({
            "space_id": space_id,
            "attachment_side": outer["side"],
            "source_entity_indexes": source_indexes,
            "source_handles": handles,
            "outer_chain_entity_indexes": copy.deepcopy(outer["indexes"]),
            "inner_chain_entity_indexes": copy.deepcopy(inner["indexes"]),
            "outer_chain_m": [[round(value, 8) for value in point]
                              for point in outer["coordinates"]],
            "inner_chain_m": [[round(value, 8) for value in point]
                              for point in inner["coordinates"]],
            "floor_polygon_cad_m": [[round(value, 8) for value in point]
                                    for point in list(
                                        outer["polygon"].exterior.coords)[:-1]],
            "area_m2": round(float(outer["polygon"].area), 8),
            "outward_span_m": round(float(outer["span_m"]), 8),
            "measured_boundary_separation_m": round(
                float(match["separation_m"]), 8),
            "attachment_endpoint_pair_error_m": round(
                float(match["endpoint_error_m"]), 8),
            "thresholds": {
                "minimum_source_rows_per_chain": 3,
                "maximum_source_rows_per_chain": 20,
                "maximum_endpoint_join_distance_m": .02,
                "minimum_outward_span_m": .60,
                "maximum_outward_span_m": 3.0,
                "minimum_boundary_separation_m": .06,
                "maximum_boundary_separation_m": .60,
                "maximum_attachment_endpoint_pair_error_m": .25,
                "minimum_source_handle_count": 6,
            },
            "decision_basis": [
                "two_nonbranching_source_boundary_chains",
                "both_chains_attach_twice_to_same_plan_boundary_side",
                "outer_chain_strictly_contains_inner_chain",
                "measured_boundary_separation_is_wall_scale",
                "source_geometry_not_layer_name_defines_extension",
            ],
        })
        used_chains.update(chain_ids)
        promoted.update(source_indexes)
    if not spaces:
        return {"schema_version": 1,
                "method": "cad_attached_exterior_double_boundary_v1",
                "status": "unresolved", "spaces": [],
                "promoted_entity_indexes": [],
                "diagnostics": {
                    "candidate_segment_count": len(segments),
                    "connected_component_count": len(components),
                    "qualified_chain_count": len(chains),
                    "pair_match_count": len(matches),
                    "qualified_chains": [{
                        "entity_indexes": copy.deepcopy(row["indexes"]),
                        "attachment_side": row["side"],
                        "source_row_count": len(row["rows"]),
                        "length_m": round(float(row["line"].length), 8),
                        "area_m2": round(float(row["polygon"].area), 8),
                        "outward_span_m": round(float(row["span_m"]), 8),
                        "bbox_m": [round(float(value), 8)
                                   for value in row["polygon"].bounds],
                    } for row in chains[:50]],
                }}
    all_points = [point for space in spaces
                  for point in space["floor_polygon_cad_m"]]
    expanded_bounds = [
        min(min_x, min(point[0] for point in all_points)),
        min(min_y, min(point[1] for point in all_points)),
        max(max_x, max(point[0] for point in all_points)),
        max(max_y, max(point[1] for point in all_points)),
    ]
    return {
        "schema_version": 1,
        "method": "cad_attached_exterior_double_boundary_v1",
        "status": "proved",
        "spaces": spaces,
        "promoted_entity_indexes": sorted(promoted),
        "original_candidate_bbox_m": [round(value, 8)
                                      for value in (min_x, min_y, max_x, max_y)],
        "expanded_candidate_bbox_m": [round(value, 8)
                                      for value in expanded_bounds],
    }


def _role_from_name(value: str) -> str:
    text = value.lower()
    for role, tokens in _OBJECT_ROLES.items():
        if any(token.lower() in text for token in tokens):
            return role
    return ""


def _role_from_symbol_footprint(layer: str, points: list[tuple[float, float]],
                                features: Optional[dict[str, int]] = None) -> str:
    """Recognize only high-specificity plan symbols when dynamic-block names are anonymous.

    ACadSharp correctly preserves evaluated anonymous block geometry, but a DWG
    dynamic block may expose only a ``*U`` name in DXF.  A double bed has a very
    distinctive residential footprint; keeping this rule deliberately narrow
    avoids turning rugs, sofas, cabinets, or generic rectangles into facts.
    """
    if not points:
        return ""
    bounds = _bbox(points)
    short_side, long_side = sorted((bounds[2] - bounds[0], bounds[3] - bounds[1]))
    layer_text = str(layer or "")
    counts = features or {}
    if (re.search(r"(?:furniture|家具)", layer_text, re.I)
            and 1.30 <= short_side <= 2.10 and 1.75 <= long_side <= 2.50):
        return "bed"
    if (re.search(r"(?:furniture|家具)", layer_text, re.I)
            and .70 <= short_side <= 1.10 and 2.20 <= long_side <= 3.20):
        return "sofa"
    if re.search(r"(?:kitchen|bath|厨卫)", layer_text, re.I):
        arcs, circles = int(counts.get("ARC") or 0), int(counts.get("CIRCLE") or 0)
        lines, polylines = int(counts.get("LINE") or 0), int(counts.get("LWPOLYLINE") or 0)
        if arcs >= 20 and circles >= 4 and long_side <= 1.0:
            return "hob"
        if polylines == 1 and lines <= 3 and circles == 0 and .35 <= short_side <= .55 and .6 <= long_side <= .9:
            return "toilet"
        if short_side >= .75 and long_side >= .85 and lines >= 10 and circles == 0:
            return "shower_zone"
        if circles >= 4 and lines >= 10 and .4 <= short_side <= .7 and .55 <= long_side <= .9:
            return "washing_machine"
        if polylines >= 2 and circles >= 1 and .35 <= short_side <= .65 and .55 <= long_side <= .9:
            return "basin"
    return ""


def _opening_from_name(value: str) -> str:
    text = value.lower()
    for kind, tokens in _OPENING_TOKENS.items():
        if any(token.lower() in text for token in tokens):
            return kind
    return ""


def _is_structural_wall_semantics(layer: str, block: str = "") -> bool:
    """Accept only explicit wall layer/block semantics as topology authority."""
    return bool(_WALL_LAYER_RE.search(f"{layer or ''} {block or ''}"))


def _exclude_compact_anonymous_wall_glyphs(geometry: list[dict]) -> list[dict]:
    """Demote compact closed anonymous INSERT glyphs inherited onto a wall layer."""
    try:
        from shapely.geometry import LineString, Polygon  # type: ignore
        from shapely.ops import polygonize, unary_union  # type: ignore
    except Exception:
        return []
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(geometry):
        provenance = row.get("cad_provenance") or {}
        root_handle = str(provenance.get("root_handle") or "")
        block_name = str(provenance.get("block") or "")
        if row.get("wall_candidate") and root_handle and block_name.upper().startswith("*U"):
            groups[root_handle].append(index)
    excluded: list[dict] = []
    for root_handle, indexes in groups.items():
        rows = [geometry[index] for index in indexes]
        points = [point for row in rows for point in row.get("points") or []]
        if not points:
            continue
        bounds = _bbox(points)
        width, depth = bounds[2] - bounds[0], bounds[3] - bounds[1]
        closed = any(bool(row.get("closed")) for row in rows)
        if not closed and len(rows) >= 3:
            try:
                closed = bool(list(polygonize(unary_union([
                    LineString(row["points"]) for row in rows if len(row.get("points") or []) >= 2
                ]))))
            except Exception:
                closed = False
        if not (closed and .08 <= width <= 1.5 and .08 <= depth <= 1.5):
            continue
        for index in indexes:
            geometry[index]["wall_candidate"] = False
            geometry[index]["structural_exclusion_reason"] = "compact_anonymous_insert_glyph"
        excluded.append({
            "root_handle": root_handle,
            "block": str((rows[0].get("cad_provenance") or {}).get("block") or ""),
            "bbox_m": [round(value, 8) for value in bounds],
            "entity_indexes": indexes,
            "reason": "compact_anonymous_insert_glyph",
        })
    return excluded


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    """Return whether two room-label words differ by at most one edit.

    CAD annotations are often typed manually and publicly available drawings
    contain simple transpositions, insertions, and substitutions.  Keep this
    deliberately narrow (one alphabetic word, at least five characters) so a
    furniture/block name cannot become an arbitrary room label.
    """
    if left == right:
        return True
    if min(len(left), len(right)) < 5 or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        mismatches = [index for index, pair in enumerate(zip(left, right))
                      if pair[0] != pair[1]]
        if len(mismatches) == 1:
            return True
        return (len(mismatches) == 2
                and mismatches[1] == mismatches[0] + 1
                and left[mismatches[0]] == right[mismatches[1]]
                and left[mismatches[1]] == right[mismatches[0]])
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = long_index = differences = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
        else:
            differences += 1
            if differences > 1:
                return False
        long_index += 1
    return True


def _room_profile_from_text(value: str) -> tuple[str, str]:
    text = str(value or "").strip().lower()
    # Room names embedded in title blocks/area schedules are document
    # metadata, not spatial anchors.  They commonly contain several
    # ``AREA OF ... = ... SQ.FT`` rows in one MTEXT entity; accepting one of
    # those words expands the plan envelope into the title block.
    if (re.search(r"\barea\s+of\b", text)
            and re.search(r"\b(?:sq\.?\s*ft\.?|sqft|square\s+feet)\b", text)):
        return "", ""
    rules = (
        ("bathroom_master", "bathroom", ("主卫", "master bath", "master bathroom")),
        ("bathroom_secondary", "bathroom", ("次卫", "客卫", "secondary bath", "guest bath")),
        ("dry_vanity", "bathroom", ("干区", "dry vanity", "vanity")),
        ("bedroom_master", "bedroom", ("主卧", "master bed", "master bedroom")),
        ("bedroom_secondary", "bedroom", ("次卧", "客卧", "secondary bed", "guest bed")),
        ("kitchen", "kitchen", ("厨房", "kitchen")),
        ("living_room", "dining_room", ("餐厅", "dining")),
        ("living_room", "living_room", ("客厅", "living")),
        ("balcony", "balcony", ("阳台", "balcony")),
        ("porch", "balcony", ("门廊", "porch", "veranda", "verandah")),
        ("pooja", "other", ("祈祷室", "佛堂", "pooja", "puja", "prayer room")),
        ("storage", "storage", ("衣帽间", "储藏间", "储物间", "walk-in closet", "closet", "storage", "store room")),
        ("foyer", "foyer", ("玄关", "门厅", "foyer", "entry")),
        ("circulation", "circulation", ("走廊", "过道", "circulation", "corridor", "hallway")),
        ("bathroom", "bathroom", ("卫生间", "浴室", "bathroom", "toilet")),
        ("bedroom", "bedroom", ("卧室", "bedroom", "bed room")),
    )
    for reference_profile, semantic_profile, tokens in rules:
        if any(token in text for token in tokens):
            return reference_profile, semantic_profile
    # Apply typo tolerance only to standalone, long, canonical room words.
    # Phrases and short tokens still require an exact match above.
    words = re.findall(r"[a-z]+", text)
    fuzzy_rules = (
        ("kitchen", "kitchen", "kitchen"),
        ("living_room", "dining_room", "dining"),
        ("living_room", "living_room", "living"),
        ("balcony", "balcony", "balcony"),
        ("porch", "balcony", "porch"),
        ("storage", "storage", "storage"),
        ("bathroom", "bathroom", "bathroom"),
        ("bedroom", "bedroom", "bedroom"),
    )
    for reference_profile, semantic_profile, canonical in fuzzy_rules:
        if any(_edit_distance_at_most_one(word, canonical) for word in words):
            return reference_profile, semantic_profile
    return "", ""


def _required_role_groups(reference_profile: str, observed_roles: set[str]) -> list[list[str]]:
    """Return CAD-observable anchor groups for a reference room profile.

    Groups are alternatives only where the contract genuinely allows them.  A
    missing role remains a required empty observation at the reference gate; it
    is never silently completed by an AI layout proxy.
    """
    profile = str(reference_profile or "")
    if profile in {"bedroom_master", "bedroom_secondary", "bedroom"}:
        return [["bed"]]
    if profile == "living_room":
        return [["sofa"], ["tv"]]
    if profile == "kitchen":
        return [["kitchen_run"], ["hob"], ["sink"], ["fridge"]]
    if profile == "bathroom_master":
        return [["toilet"], ["shower_zone"], ["basin"]]
    if profile == "bathroom_secondary":
        groups = [["toilet"], ["shower_zone"]]
        if "basin" in observed_roles:
            groups.append(["basin"])
        return groups
    if profile == "dry_vanity":
        groups = [["basin"]]
        if "faucet" in observed_roles:
            groups.append(["faucet"])
        if "mirror" in observed_roles:
            groups.append(["mirror"])
        return groups
    return []


def _hatch_surface_record(
    entity: Any, provenance: dict, scale: float, chord_error_m: float,
) -> dict | None:
    """Preserve source HATCH boundary geometry as non-structural surface evidence.

    Hatches never become walls.  Their source paths can nevertheless prove
    that an otherwise unlabelled perimeter face is a deliberately paved
    exterior space.  Store only flattened boundary polygons and provenance;
    layer, pattern and colour remain audit metadata and are not classifiers.
    """
    try:
        from ezdxf.path import from_hatch  # type: ignore
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import polygonize, unary_union  # type: ignore
    except Exception:
        return None
    polygons = []
    flattening_distance = max(
        1e-5, float(chord_error_m) / max(abs(float(scale)), 1e-12))
    try:
        paths = list(from_hatch(entity))
    except Exception:
        return None
    for path in paths:
        try:
            points = [
                (float(vertex.x) * scale, float(vertex.y) * scale)
                for vertex in path.flattening(flattening_distance)
            ]
        except Exception:
            continue
        if len(points) < 3:
            continue
        if math.dist(points[0], points[-1]) > 1e-8:
            points.append(points[0])
        candidate = Polygon(points)
        if not candidate.is_valid:
            candidate = candidate.buffer(0)
        if (candidate.geom_type == "Polygon" and not candidate.is_empty
                and float(candidate.area) >= .001):
            polygons.append(candidate)
    if not polygons:
        return None
    merged = unary_union(polygons).buffer(0)
    components = (
        list(merged.geoms)
        if merged.geom_type in {"MultiPolygon", "GeometryCollection"}
        else [merged]
    )
    components = [
        value for value in components
        if value.geom_type == "Polygon" and float(value.area) >= .001
    ]
    if not components:
        return None
    min_x = min(float(value.bounds[0]) for value in components)
    min_y = min(float(value.bounds[1]) for value in components)
    max_x = max(float(value.bounds[2]) for value in components)
    max_y = max(float(value.bounds[3]) for value in components)
    namespace = getattr(entity, "dxf", None)
    return {
        "schema_version": 1,
        "method": "cad_hatch_boundary_surface_evidence_v1",
        "entity_type": "HATCH",
        "source_handle": str(
            provenance.get("source_handle") or provenance.get("handle") or ""),
        "root_handle": str(
            provenance.get("root_handle") or provenance.get("source_handle")
            or provenance.get("handle") or ""),
        "boundary_path_count": len(paths),
        "polygon_component_count": len(components),
        "area_m2": round(float(merged.area), 8),
        "bbox_m": [round(value, 8) for value in (min_x, min_y, max_x, max_y)],
        "polygons_m": [[
            [round(float(x), 8), round(float(y), 8)]
            for x, y in list(value.exterior.coords)[:-1]
        ] for value in components],
        "solid_fill": bool(getattr(namespace, "solid_fill", 0) or False),
        "pattern_name": str(getattr(namespace, "pattern_name", "") or "")[:100],
        "cad_provenance": copy.deepcopy(provenance),
        "decision_basis": [
            "source_hatch_boundary_paths_only",
            "non_structural_surface_evidence",
            "pattern_layer_colour_not_used_for_classification",
        ],
    }


def _candidate_preview_svg(path: str, rows: list[dict], bbox: tuple[float, float, float, float],
                           title: str, *, context_rows: Optional[list[dict]] = None) -> None:
    width, height, padding = 1200, 900, 30
    span_x, span_y = max(bbox[2] - bbox[0], 0.001), max(bbox[3] - bbox[1], 0.001)
    scale = min((width - 2 * padding) / span_x, (height - 2 * padding) / span_y)

    def project(point: tuple[float, float]) -> tuple[float, float]:
        return padding + (point[0] - bbox[0]) * scale, height - padding - (point[1] - bbox[1]) * scale

    lines = []
    for row in rows:
        points = row["points"]
        for first, second in zip(points, points[1:]):
            x1, y1 = project(first)
            x2, y2 = project(second)
            lines.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"/>')
    context_lines = []
    for row in context_rows or []:
        row_bbox = row.get("bbox") or ()
        if len(row_bbox) != 4 or _bbox_distance(tuple(row_bbox), bbox) > 0:
            continue
        points = row.get("points") or []
        for first, second in zip(points, points[1:]):
            x1, y1 = project(first)
            x2, y2 = project(second)
            context_lines.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"/>'
            )
    content = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
               f'<rect width="100%" height="100%" fill="white"/><text x="20" y="24" font-family="sans-serif" font-size="16">{html.escape(title)}</text>'
               '<g stroke="#a9b0ba" stroke-width="1" fill="none" opacity="0.8">'
               + "".join(context_lines) + '</g><g stroke="#252525" stroke-width="2" fill="none">'
               + "".join(lines) + "</g></svg>")
    temporary = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(temporary, "x", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(temporary, path)


def _candidate_preview_png(path: str, rows: list[dict], bbox: tuple[float, float, float, float],
                           title: str, *, context_rows: Optional[list[dict]] = None) -> dict:
    """Render the selected CAD plan to a provider-safe raster with an exact map."""
    from PIL import Image, ImageDraw, ImageFont

    width, height, padding = 1200, 900, 30
    span_x, span_y = max(bbox[2] - bbox[0], .001), max(bbox[3] - bbox[1], .001)
    scale = min((width - 2 * padding) / span_x, (height - 2 * padding) / span_y)

    def project(point: tuple[float, float]) -> tuple[float, float]:
        return padding + (point[0] - bbox[0]) * scale, height - padding - (point[1] - bbox[1]) * scale

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("msyh.ttc", 18)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 7), title, fill=(45, 45, 45), font=font)
    for row in context_rows or []:
        row_bbox = row.get("bbox") or ()
        if len(row_bbox) != 4 or _bbox_distance(tuple(row_bbox), bbox) > 0:
            continue
        points = [project(point) for point in row.get("points") or []]
        if len(points) >= 2:
            draw.line(points, fill=(174, 181, 191), width=1)
    for row in rows:
        points = [project(point) for point in row.get("points") or []]
        if len(points) >= 2:
            draw.line(points, fill=(28, 28, 28), width=3)
    temporary = f"{path}.{uuid.uuid4().hex}.tmp"
    image.save(temporary, "PNG")
    os.replace(temporary, path)
    return {
        "image_width": width, "image_height": height, "padding": padding,
        "pixels_per_metre": round(scale, 10),
        "cad_bbox_m": [round(value, 8) for value in bbox],
        "model_origin_cad_m": [round(bbox[0], 8), round(bbox[1], 8)],
        "coordinate_contract_version": CAD_PLAN_TRANSFORM_VERSION,
        "x_direction": "left_to_right", "cad_y_direction": "bottom_to_top",
        "model_z_direction": "top_to_bottom",
        "topdown_view": "sky_to_ground",
    }


def validate_cad_model(model: dict, parse_report: dict) -> dict:
    hard_errors: list[dict] = []
    warnings: list[dict] = []
    rooms = model.get("rooms") or []
    walls = model.get("walls") or []
    if not walls:
        hard_errors.append({"code": "cad_no_walls", "message": "CAD 主平面没有可验证墙线"})
    if not rooms:
        hard_errors.append({"code": "cad_no_closed_rooms", "message": "CAD 主平面没有可验证闭合房间"})
    structural_entity_count = int(parse_report.get("structural_entity_count") or
                                  (parse_report.get("alignment_metrics") or {}).get("structural_entity_count") or 0)
    if structural_entity_count <= 0:
        hard_errors.append({
            "code": "cad_wall_semantics_unresolved",
            "message": "解析报告不能证明存在明确 wall 图层/块语义的结构实体",
        })
    geometry_authority = parse_report.get("geometry_authority_evidence") \
        if isinstance(parse_report.get("geometry_authority_evidence"), dict) else {}
    geometry_selected_indexes = {
        int(value) for value in geometry_authority.get("selected_indexes") or []
        if isinstance(value, int) or str(value).isdigit()
    }
    geometry_authority_proved = (
        geometry_authority.get("status") == "proved"
        and bool(geometry_selected_indexes)
    )
    supplemental_geometry_indexes: set[int] = set()
    for evidence in parse_report.get("selected_entity_role_evidence") or []:
        if not isinstance(evidence, dict):
            continue
        proof = evidence.get("endpoint_bridge_evidence")
        source_indexes = {
            int(value) for value in evidence.get("entity_indexes") or []
            if isinstance(value, int) or str(value).isdigit()
        }
        supports = proof.get("endpoint_supports") if isinstance(proof, dict) else []
        support_indexes = {
            int(row.get("entity_index")) for row in supports
            if isinstance(row, dict)
            and (isinstance(row.get("entity_index"), int)
                 or str(row.get("entity_index") or "").isdigit())
        }
        try:
            support_distances = [float(row.get("distance_m")) for row in supports]
        except (TypeError, ValueError):
            support_distances = []
        if (evidence.get("role") == "wall_face"
                and evidence.get("confidence") == "high"
                and evidence.get("reason_codes") == [
                    "context_singleton_endpoint_bridge_geometry"]
                and len(source_indexes) == 1
                and len(supports) == 2
                and len(support_indexes) == 2
                and support_indexes.issubset(geometry_selected_indexes)
                and len(support_distances) == 2
                and max(support_distances) <= .02 + 1e-9):
            supplemental_geometry_indexes.update(source_indexes)
        shell_proof = evidence.get("perimeter_wall_shell_evidence") \
            if isinstance(evidence.get("perimeter_wall_shell_evidence"), dict) \
            else {}
        shell_retained_indexes = {
            int(value) for value in evidence.get("retained_entity_indexes") or []
            if isinstance(value, int) or str(value).isdigit()
        }
        shell_outer_indexes = {
            int(value) for value in shell_proof.get("outer_entity_indexes") or []
            if isinstance(value, int) or str(value).isdigit()
        }
        shell_inner_indexes = {
            int(value) for value in shell_proof.get("inner_entity_indexes") or []
            if isinstance(value, int) or str(value).isdigit()
        }
        try:
            shell_insets = [float(value) for value in
                            shell_proof.get("inset_samples_m") or []]
            shell_thickness = float(
                shell_proof.get("measured_wall_thickness_m"))
            shell_outer_rectangularity = float(
                shell_proof.get("outer_rectangularity"))
            shell_inner_rectangularity = float(
                shell_proof.get("inner_rectangularity"))
            shell_angle_difference = float(
                shell_proof.get("axis_angle_difference_deg"))
        except (TypeError, ValueError):
            shell_insets = []
            shell_thickness = 0.0
            shell_outer_rectangularity = 0.0
            shell_inner_rectangularity = 0.0
            shell_angle_difference = float("inf")
        if (
            evidence.get("role") == "wall_face"
            and evidence.get("confidence") == "high"
            and evidence.get("reason_codes") == [
                "duplicate_nested_perimeter_wall_shell_geometry"]
            and shell_proof.get("method")
                == "cad_duplicate_nested_perimeter_wall_shell_v1"
            and len(shell_outer_indexes) >= 2
            and len(shell_inner_indexes) >= 2
            and len(shell_outer_indexes.intersection(shell_inner_indexes)) == 0
            and (shell_outer_indexes | shell_inner_indexes).issubset(
                geometry_selected_indexes)
            and int(shell_proof.get("outer_duplicate_count") or 0)
                == len(shell_outer_indexes)
            and int(shell_proof.get("inner_duplicate_count") or 0)
                == len(shell_inner_indexes)
            and len(shell_retained_indexes) == 8
            and len(shell_proof.get("outer_polygon_m") or []) == 4
            and len(shell_proof.get("inner_polygon_m") or []) == 4
            and shell_outer_rectangularity >= .995 - 1e-9
            and shell_inner_rectangularity >= .995 - 1e-9
            and shell_angle_difference <= 1.0 + 1e-9
            and len(shell_insets) == 4
            and min(shell_insets, default=0.0) >= .06 - 1e-9
            and max(shell_insets, default=float("inf")) <= .60 + 1e-9
            and max(shell_insets, default=0.0)
                - min(shell_insets, default=0.0) <= .02 + 1e-9
            and .06 - 1e-9 <= shell_thickness <= .60 + 1e-9
            and max((abs(value - shell_thickness) for value in shell_insets),
                    default=float("inf")) <= .02 + 1e-9
        ):
            supplemental_geometry_indexes.update(shell_retained_indexes)
    proved_wall_indexes = geometry_selected_indexes | supplemental_geometry_indexes
    promoted_nonstructural = []
    for wall in walls:
        provenance = wall.get("cad_provenance") if isinstance(wall.get("cad_provenance"), dict) else {}
        if not _is_structural_wall_semantics(
                str(provenance.get("effective_layer") or provenance.get("layer") or ""),
                str(provenance.get("block") or "")):
            frame_proof = provenance.get("frame_geometry_opening_evidence") \
                if isinstance(provenance.get("frame_geometry_opening_evidence"), dict) else {}
            try:
                regular_frame_host_proved = (
                    provenance.get("wall_assembly_source_representation")
                    == "frame_geometry_opening_host"
                    and frame_proof.get("method")
                    == "cad_window_frame_measured_host_v1"
                    and frame_proof.get("kind") == "window"
                    and len(set(str(value) for value in
                                frame_proof.get("opening_source_handles") or []
                                if str(value))) >= 4
                    and int(frame_proof.get("long_rail_count") or 0) >= 2
                    and int(frame_proof.get("cross_member_count") or 0) >= 2
                    and float(frame_proof.get("interior_wall_overlap_ratio") or 0)
                    >= .90 - 1e-9
                    and len(frame_proof.get("wall_endpoint_support_distance_m") or []) == 2
                    and max(float(value) for value in
                            frame_proof.get("wall_endpoint_support_distance_m") or [])
                    <= .12 + 1e-9
                    and len(frame_proof.get("wall_mask_endpoint_distance_m") or []) == 2
                    and max(float(value) for value in
                            frame_proof.get("wall_mask_endpoint_distance_m") or [])
                    <= .15 + 1e-9
                )
                sparse_frame_host_proved = (
                    provenance.get("wall_assembly_source_representation")
                    == "frame_geometry_opening_host"
                    and frame_proof.get("method")
                    == "cad_sparse_window_frame_wall_face_host_v1"
                    and frame_proof.get("kind") == "window"
                    and len(set(str(value) for value in
                                frame_proof.get("opening_source_handles") or []
                                if str(value))) >= 2
                    and 2 <= int(frame_proof.get("source_row_count") or 0) <= 3
                    and int(frame_proof.get(
                        "negative_wall_face_support_count") or 0) >= 2
                    and int(frame_proof.get(
                        "positive_wall_face_support_count") or 0) >= 2
                    and int(frame_proof.get("long_rail_count") or 0) >= 2
                    and float(frame_proof.get("interior_wall_overlap_ratio") or 0)
                    <= .20 + 1e-9
                    and .06 <= float(
                        frame_proof.get("supported_wall_face_span_m") or 0) <= .60
                    and abs(float(
                        frame_proof.get("wall_band_midpoint_offset_m") or 0))
                    <= .08 + 1e-9
                    and len(frame_proof.get(
                        "canonical_wall_mask_endpoint_distance_m") or []) == 2
                    and max(float(value) for value in frame_proof.get(
                        "canonical_wall_mask_endpoint_distance_m") or [])
                    <= .15 + 1e-9)
                root_frame_host_proved = (
                    provenance.get("wall_assembly_source_representation")
                    == "frame_geometry_opening_host"
                    and frame_proof.get("method")
                    == "cad_root_window_frame_wall_face_host_v1"
                    and frame_proof.get("kind") == "window"
                    and len(set(str(value) for value in
                                frame_proof.get("opening_source_handles") or []
                                if str(value))) == 1
                    and 4 <= int(frame_proof.get("source_row_count") or 0) <= 64
                    and int(frame_proof.get(
                        "negative_wall_face_support_count") or 0) >= 2
                    and int(frame_proof.get(
                        "positive_wall_face_support_count") or 0) >= 2
                    and int(frame_proof.get("long_rail_count") or 0) >= 2
                    and int(frame_proof.get("cross_member_count") or 0) >= 2
                    and float(frame_proof.get("interior_wall_overlap_ratio") or 0)
                    <= .20 + 1e-9
                    and .06 <= float(
                        frame_proof.get("supported_wall_face_span_m") or 0) <= .60
                    and abs(float(frame_proof.get("frame_short_span_m") or 0)
                            - float(frame_proof.get(
                                "supported_wall_face_span_m") or 0))
                    <= .03 + 1e-9
                    and abs(float(
                        frame_proof.get("wall_band_midpoint_offset_m") or 0))
                    <= .08 + 1e-9
                    and len(frame_proof.get(
                        "canonical_wall_mask_endpoint_distance_m") or []) == 2
                    and max(float(value) for value in frame_proof.get(
                        "canonical_wall_mask_endpoint_distance_m") or [])
                    <= .15 + 1e-9)
                frame_host_proved = bool(
                    regular_frame_host_proved or sparse_frame_host_proved
                    or root_frame_host_proved)
            except (TypeError, ValueError):
                frame_host_proved = False
            global_proof = provenance.get("global_topology_opening_evidence") \
                if isinstance(provenance.get("global_topology_opening_evidence"), dict) else {}
            try:
                global_opening_host_proved = (
                    provenance.get("wall_assembly_source_representation")
                    == "global_topology_opening_host"
                    and str(global_proof.get("candidate_id") or "")
                    and len(set(str(value) for value in
                                global_proof.get("source_handles") or []
                                if str(value))) >= 1
                    and float(global_proof.get("wall_mask_axis_coverage_ratio") or 0)
                    >= .90 - 1e-9
                    and .06 <= float(
                        global_proof.get("wall_cross_section_thickness_m") or 0)
                    <= .60
                )
            except (TypeError, ValueError):
                global_opening_host_proved = False
            terminal_open_proof = provenance.get(
                "terminal_open_connection_evidence") \
                if isinstance(provenance.get(
                    "terminal_open_connection_evidence"), dict) else {}
            try:
                terminal_open_host_proved = bool(
                    provenance.get("wall_assembly_source_representation")
                    == "terminal_open_connection_host"
                    and terminal_open_proof.get("method")
                    == "cad_labeled_terminal_open_connection_v1"
                    and str(terminal_open_proof.get("candidate_id") or "")
                    and len(set(str(value) for value in
                                terminal_open_proof.get("source_handles") or []
                                if str(value))) >= 2
                    and len(set(str(value) for value in
                                terminal_open_proof.get(
                                    "source_wall_assembly_ids") or []
                                if str(value))) == 2
                    and .35 <= float(terminal_open_proof.get(
                        "clear_gap_width_m") or 0) <= 1.50
                    and .35 <= float(terminal_open_proof.get(
                        "terminal_axis_extension_m") or 0) <= 1.80
                    and float(terminal_open_proof.get(
                        "terminal_transverse_angle_deg") or 0) >= 89.0 - 1e-9
                    and float(terminal_open_proof.get(
                        "wall_thickness_spread_m") or 0) <= .04 + 1e-9
                    and float(terminal_open_proof.get(
                        "intermediate_wall_coverage_m") or 0) <= .01 + 1e-9
                    and int(terminal_open_proof.get(
                        "unique_transverse_support_count") or 0) == 1
                    and terminal_open_proof.get("storage_anchor_profile")
                    == "storage"
                    and terminal_open_proof.get("kitchen_anchor_profile")
                    == "kitchen"
                    and int(terminal_open_proof.get(
                        "topology_space_count_delta") or 0) == 1
                    and terminal_open_proof.get(
                        "closed_space_semantic_anchor_ids")
                    == [terminal_open_proof.get("storage_anchor_id")]
                )
            except (TypeError, ValueError):
                terminal_open_host_proved = False
            door_proof = provenance.get("door_swing_geometry_opening_evidence") \
                if isinstance(provenance.get(
                    "door_swing_geometry_opening_evidence"), dict) else {}
            try:
                door_method = str(door_proof.get("method") or "")
                door_terminal_methods = {
                    "cad_door_swing_unique_terminal_wall_support_v1",
                    "cad_door_leaf_unique_terminal_wall_support_v1",
                }
                door_arc_source_proved = bool(
                    door_method in {
                        "cad_door_swing_unique_jamb_host_v1",
                        "cad_door_swing_unique_terminal_wall_support_v1",
                        "cad_door_swing_wall_pair_transverse_jamb_host_v1",
                    }
                    and {"circular_swing_arc", "radial_door_leaf",
                         "wall_network_supported"}.issubset(set(
                             str(value) for value in
                             door_proof.get("source_reason_codes") or [])))
                leaf_source = door_proof.get("parallel_leaf_without_arc_evidence") \
                    if isinstance(door_proof.get(
                        "parallel_leaf_without_arc_evidence"), dict) else {}
                door_leaf_source_proved = bool(
                    door_method in {
                        "cad_door_leaf_unique_jamb_host_v1",
                        "cad_door_leaf_unique_terminal_wall_support_v1",
                        "cad_door_leaf_unique_source_face_jamb_host_v1",
                        "cad_door_leaf_unique_wall_gap_axis_host_v1",
                    }
                    and {"parallel_door_leaf_rails",
                         "hinge_endpoint_wall_supported",
                         "swing_leaf_without_arc",
                         "wall_network_supported"}.issubset(set(
                             str(value) for value in
                             door_proof.get("source_reason_codes") or []))
                    and leaf_source.get("method")
                    == "cad_parallel_door_leaf_without_arc_v1"
                    and 3 <= int(leaf_source.get("parallel_rail_count") or 0) <= 5
                    and int(leaf_source.get("parallel_rail_count") or 0)
                    == int(leaf_source.get("source_row_count") or 0)
                    and float(leaf_source.get("leaf_angle_spread_deg") or 0)
                    <= 1.0 + 1e-9
                    and float(leaf_source.get(
                        "hinge_endpoint_cluster_radius_m") or 0) <= .08 + 1e-9
                    and float(leaf_source.get(
                        "free_endpoint_cluster_radius_m") or 0) <= .08 + 1e-9
                    and float(leaf_source.get("hinge_wall_distance_m") or 0)
                    <= .12 + 1e-9
                    and float(leaf_source.get("free_endpoint_wall_distance_m") or 0)
                    >= .20 - 1e-9)
                source_face_supports = door_proof.get(
                    "source_face_jamb_supports") or []
                door_source_face_proved = bool(
                    door_method != "cad_door_leaf_unique_source_face_jamb_host_v1"
                    or (
                        len(source_face_supports) == 2
                        and {int(row.get("endpoint_index", -1))
                             for row in source_face_supports
                             if isinstance(row, dict)} == {0, 1}
                        and all(
                            row.get("method")
                            == "cad_source_wall_face_pair_at_door_jamb_v1"
                            and len(set(str(value) for value in row.get(
                                "wall_face_source_handles") or [] if str(value))) >= 2
                            and .06 <= float(
                                row.get("face_separation_m") or 0) <= .60
                            and abs(float(row.get(
                                "wall_band_midpoint_offset_m") or 0)) <= .08 + 1e-9
                            and min(float(value) for value in row.get(
                                "wall_face_outward_extension_m") or []) >= .05 - 1e-9
                            and max(float(value) for value in row.get(
                                "wall_face_axis_angle_difference_deg") or [])
                            <= 1.0 + 1e-9
                            for row in source_face_supports
                            if isinstance(row, dict))
                        and max(float(row.get("face_separation_m") or 0)
                                for row in source_face_supports)
                        - min(float(row.get("face_separation_m") or 0)
                              for row in source_face_supports) <= .04 + 1e-9
                    ))
                unique_gap = door_proof.get("unique_wall_gap_axis_evidence") \
                    if isinstance(door_proof.get(
                        "unique_wall_gap_axis_evidence"), dict) else {}
                door_unique_gap_proved = bool(
                    door_method != "cad_door_leaf_unique_wall_gap_axis_host_v1"
                    or (
                        unique_gap.get("method")
                        == "cad_parallel_leaf_unique_wall_gap_axis_v1"
                        and int(unique_gap.get("axis_candidate_count") or 0) >= 1
                        and len(set(str(value) for value in unique_gap.get(
                            "wall_face_source_handles") or [] if str(value))) >= 2
                        and .06 <= float(unique_gap.get(
                            "wall_face_separation_m") or 0) <= .60
                        and max(float(value) for value in unique_gap.get(
                            "source_endpoint_wall_support_distance_m") or [])
                        <= .151 + 1e-9
                        and max(float(value) for value in unique_gap.get(
                            "wall_mask_endpoint_distance_m") or []) <= .10 + 1e-9
                        and float(unique_gap.get(
                            "axis_midpoint_wall_clearance_m") or 0)
                        >= max(.06, float(unique_gap.get(
                            "wall_face_separation_m") or 0) / 2 - .03) - 1e-9
                        and (int(unique_gap.get("axis_candidate_count") or 0) == 1
                             or float(unique_gap.get(
                                 "axis_clearance_selection_margin_m") or 0)
                             >= .10 - 1e-9)
                    ))
                projected_arc = door_proof.get(
                    "projected_arc_transverse_jamb_evidence") \
                    if isinstance(door_proof.get(
                        "projected_arc_transverse_jamb_evidence"), dict) else {}
                door_projected_arc_proved = bool(
                    door_method
                    != "cad_door_swing_wall_pair_transverse_jamb_host_v1"
                    or (
                        projected_arc.get("method")
                        == "cad_arc_leaf_wall_pair_transverse_jamb_projection_v1"
                        and len({int(value) for value in projected_arc.get(
                            "wall_face_entity_indexes") or []}) == 2
                        and int(projected_arc.get(
                            "transverse_jamb_entity_index") or -1)
                        not in {int(value) for value in projected_arc.get(
                            "wall_face_entity_indexes") or []}
                        and len(set(str(value) for value in projected_arc.get(
                            "wall_face_source_handles") or [] if str(value))) >= 3
                        and .06 <= float(projected_arc.get(
                            "wall_face_separation_m") or 0) <= .60
                        and float(projected_arc.get(
                            "hinge_to_wall_centerline_offset_m") or 0)
                        <= .20 + 1e-9
                        and float(projected_arc.get(
                            "transverse_jamb_snap_distance_m") or 0)
                        <= .20 + 1e-9
                        and float(projected_arc.get(
                            "transverse_jamb_angle_difference_deg") or 0)
                        >= 88.5 - 1e-9
                    ))
                door_host_proved = (
                    provenance.get("wall_assembly_source_representation")
                    == "door_swing_geometry_opening_host"
                    and door_method in {
                        "cad_door_swing_unique_jamb_host_v1",
                        "cad_door_swing_unique_terminal_wall_support_v1",
                        "cad_door_swing_wall_pair_transverse_jamb_host_v1",
                        "cad_door_leaf_unique_jamb_host_v1",
                        "cad_door_leaf_unique_terminal_wall_support_v1",
                        "cad_door_leaf_unique_source_face_jamb_host_v1",
                        "cad_door_leaf_unique_wall_gap_axis_host_v1",
                    }
                    and door_proof.get("kind") == "door"
                    and len(set(str(value) for value in
                                door_proof.get("opening_source_handles") or []
                                if str(value))) >= 1
                    and (door_arc_source_proved or door_leaf_source_proved)
                    and door_source_face_proved
                    and door_unique_gap_proved
                    and door_projected_arc_proved
                    and len(door_proof.get("jamb_cross_section_width_m") or []) == 2
                    and max(float(value) for value in
                             door_proof.get("wall_mask_endpoint_distance_m") or [])
                    <= (.04 if door_method in door_terminal_methods
                        else .05 if door_method
                        == "cad_door_swing_wall_pair_transverse_jamb_host_v1"
                        else .25 if door_method
                        == "cad_door_leaf_unique_source_face_jamb_host_v1"
                        else .10 if door_method
                        == "cad_door_leaf_unique_wall_gap_axis_host_v1"
                        else .15) + 1e-9
                    and max(float(value) for value in
                            door_proof.get("jamb_cross_section_width_m") or [])
                    - min(float(value) for value in
                          door_proof.get("jamb_cross_section_width_m") or [])
                    <= .04 + 1e-9
                    and (door_method not in door_terminal_methods
                         or (
                             len(door_proof.get("terminal_wall_supports") or []) == 2
                             and len({str(row.get("wall_assembly_id") or "")
                                      for row in door_proof.get(
                                          "terminal_wall_supports") or []}) == 2
                             and any(row.get("orientation") == "collinear"
                                     for row in door_proof.get(
                                         "terminal_wall_supports") or [])
                         ))
                )
            except (TypeError, ValueError):
                door_host_proved = False
            repeated_proof = provenance.get(
                "repeated_window_frame_opening_evidence") \
                if isinstance(provenance.get(
                    "repeated_window_frame_opening_evidence"), dict) else {}
            try:
                repeated_window_host_proved = (
                    provenance.get("wall_assembly_source_representation")
                    == "repeated_window_frame_opening_host"
                    and repeated_proof.get("method")
                    == "cad_repeated_collinear_window_frame_host_v1"
                    and repeated_proof.get("kind") == "window"
                    and len(set(str(value) for value in
                                repeated_proof.get("opening_source_handles") or []
                                if str(value))) >= 4
                    and len(set(str(value) for value in repeated_proof.get(
                        "reference_opening_source_handles") or [] if str(value))) >= 4
                    and str(repeated_proof.get("reference_wall_assembly_id") or "")
                    and int(repeated_proof.get("long_rail_count") or 0) >= 3
                    and int(repeated_proof.get("cross_member_count") or 0) >= 2
                    and max(float(value) for value in repeated_proof.get(
                        "wall_mask_endpoint_distance_m") or []) <= .05 + 1e-9
                    and "axis_transverse_offset_m" in repeated_proof
                    and float(repeated_proof.get("axis_transverse_offset_m"))
                    <= .005 + 1e-9
                    and "opening_width_difference_m" in repeated_proof
                    and float(repeated_proof.get("opening_width_difference_m"))
                    <= .01 + 1e-9
                )
            except (TypeError, ValueError):
                repeated_window_host_proved = False
            if (frame_host_proved or global_opening_host_proved or door_host_proved
                    or repeated_window_host_proved
                    or terminal_open_host_proved):
                # The line is a compatibility owner for an opening cut, not an
                # independently promoted furniture/wall entity.  Its measured
                # opening-host proof is retained and revalidated by GeometryContract.
                continue
            source_entities = provenance.get("source_entities") or []
            source_indexes = {
                int(row.get("entity_index")) for row in source_entities
                if isinstance(row, dict)
                and (isinstance(row.get("entity_index"), int)
                     or str(row.get("entity_index") or "").isdigit())
            }
            if (not geometry_authority_proved or not source_indexes
                    or not source_indexes.issubset(proved_wall_indexes)):
                promoted_nonstructural.append(str(wall.get("id") or ""))
    if promoted_nonstructural:
        hard_errors.append({
            "code": "cad_nonstructural_wall_promoted",
            "message": "模型中存在既无 wall 语义、也不属于已证明几何平面候选的墙体",
            "wall_ids": promoted_nonstructural[:100],
        })
    try:
        from shapely.geometry import Polygon  # type: ignore
        room_shapes = []
        for room in rooms:
            shape = Polygon([(p["x"], p["z"]) for p in room.get("polygon") or []])
            if not shape.is_valid:
                hard_errors.append({
                    "code": "cad_room_polygon_invalid", "room_id": room.get("id"),
                    "message": "CAD 房间 polygon 自相交或拓扑无效",
                })
                continue
            room_shapes.append((room, shape))
        for index, (left, left_shape) in enumerate(room_shapes):
            for right, right_shape in room_shapes[index + 1:]:
                overlap_area = float(left_shape.intersection(right_shape).area)
                if overlap_area > 1e-6:
                    hard_errors.append({
                        "code": "cad_room_overlap", "room_ids": [left["id"], right["id"]],
                        "overlap_area_m2": round(overlap_area, 8),
                        "message": "CAD 房间存在真实面积重叠，必须人工修正",
                    })
    except ImportError as ex:
        raise CadDependencyError("shapely_missing", "缺少 shapely，无法验证 CAD 空间拓扑", status_code=503) from ex
    metrics = copy.deepcopy(parse_report.get("alignment_metrics") or {})
    inverse = model.get("model_to_cad") if isinstance(model.get("model_to_cad"), dict) else {}
    back_projection_errors: list[float] = []
    for wall in walls:
        provenance = wall.get("cad_provenance") if isinstance(wall.get("cad_provenance"), dict) else {}
        source_segment = provenance.get("source_segment_m")
        if not isinstance(source_segment, list) or len(source_segment) != 2:
            continue
        actual = [model_plan_to_cad((
            float((wall.get(field) or {}).get("x") or 0),
            float((wall.get(field) or {}).get("z") or 0),
        ), inverse) for field in ("start", "end")]
        expected = [(float(point[0]), float(point[1])) for point in source_segment]
        direct = [math.dist(actual[0], expected[0]), math.dist(actual[1], expected[1])]
        reverse = [math.dist(actual[0], expected[1]), math.dist(actual[1], expected[0])]
        back_projection_errors.extend(direct if max(direct) <= max(reverse) else reverse)
    if back_projection_errors:
        ordered = sorted(back_projection_errors)
        metrics["wall_boundary_p95_m"] = round(
            ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * .95) - 1))], 8)
    wall_boundary_p95 = metrics.get("wall_boundary_p95_m")
    if wall_boundary_p95 is None or float(wall_boundary_p95) > 0.05:
        hard_errors.append({"code": "cad_wall_alignment_failed", "message": "CAD 墙边界 p95 超过 0.05m"})
    room_back_projection_errors: list[float] = []
    for room in rooms:
        provenance = room.get("cad_provenance") if isinstance(room.get("cad_provenance"), dict) else {}
        source_polygon = provenance.get("source_polygon_m")
        actual_polygon = [
            model_plan_to_cad((
                float(point.get("x") or 0), float(point.get("z") or 0),
            ), inverse)
            for point in room.get("polygon") or [] if isinstance(point, dict)
        ]
        if not isinstance(source_polygon, list) or len(source_polygon) < 3 or len(actual_polygon) < 3:
            continue
        expected_polygon = [(float(point[0]), float(point[1])) for point in source_polygon]
        room_back_projection_errors.extend(
            min(math.dist(point, expected) for expected in expected_polygon)
            for point in actual_polygon
        )
        room_back_projection_errors.extend(
            min(math.dist(expected, point) for point in actual_polygon)
            for expected in expected_polygon
        )
    if room_back_projection_errors:
        ordered = sorted(room_back_projection_errors)
        metrics["room_boundary_p95_m"] = round(
            ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * .95) - 1))], 8)
        if metrics["room_boundary_p95_m"] > .05:
            hard_errors.append({
                "code": "cad_room_boundary_alignment_failed",
                "message": "CAD 房间 polygon 反投影 p95 超过 0.05m",
            })
    if float(metrics.get("room_coverage") or 0) < 0.98:
        hard_errors.append({"code": "cad_room_coverage_failed", "message": "CAD 房间覆盖率低于 0.98"})
    if metrics.get("outer_wall_closed") is not True:
        hard_errors.append({"code": "cad_outer_wall_not_closed", "message": "CAD 外围拓扑不能证明闭合"})
    if metrics.get("room_nonoverlap") is not True or float(metrics.get("room_overlap_area_m2") or 0) > 1e-6:
        hard_errors.append({"code": "cad_room_nonoverlap_failed", "message": "CAD 房间区域存在重叠"})
    if float(metrics.get("cad_derivation_coverage") or 0) < 1.0:
        hard_errors.append({"code": "cad_derivation_incomplete", "message": "模型并非 100% 来自可追溯 CAD 实体"})
    if metrics.get("opening_endpoint_errors"):
        hard_errors.append({"code": "cad_opening_alignment_failed", "message": "CAD 开口端点未能绑定墙体"})
    if metrics.get("opening_width_errors"):
        hard_errors.append({"code": "cad_opening_width_failed", "message": "CAD 开口宽度无法从实体可靠反投影"})
    missing_provenance = []
    provenance_collections = ("walls", "openings", "physical_spaces") if model.get("physical_spaces") else (
        "walls", "openings", "rooms")
    for collection in provenance_collections:
        for row in model.get(collection) or []:
            provenance = row.get("cad_provenance")
            if not isinstance(provenance, dict) or not (
                    provenance.get("source_handle") or provenance.get("root_handle")):
                missing_provenance.append({"collection": collection, "id": row.get("id")})
            elif collection == "walls" and not provenance.get("source_segment_m"):
                missing_provenance.append({"collection": collection, "id": row.get("id"),
                                           "reason": "source_segment_m_missing"})
            elif collection in ("rooms", "physical_spaces") and not provenance.get("source_polygon_m"):
                missing_provenance.append({"collection": collection, "id": row.get("id"),
                                           "reason": "source_polygon_m_missing"})
    for row in model.get("fixed_objects") or []:
        if row.get("observed") and (row.get("source") == "cad" or row.get("cad_provenance")):
            provenance = row.get("cad_provenance")
            if not isinstance(provenance, dict) or not (
                    provenance.get("source_handle") or provenance.get("root_handle")):
                missing_provenance.append({"collection": "fixed_objects", "id": row.get("id")})
    if missing_provenance:
        hard_errors.append({
            "code": "cad_provenance_incomplete",
            "message": "存在不能追溯到 CAD handle/INSERT 链的权威实体",
            "entities": missing_provenance[:100],
        })
    return {"hard_errors": hard_errors, "warnings": warnings, "alignment_metrics": metrics,
            "checked_at": time.time()}


def cad_facts_hash(model: dict) -> str:
    """Hash only immutable CAD authority, not later semantic annotations.

    Room labels/profiles, selection flags and review state are deliberately
    outside the CAD fact set: the semantic-only workflow may supplement those
    fields.  Geometry, opening identity and observed block identity remain
    immutable and are always covered by this digest.
    """
    wall_keys = ("id", "wall_assembly_id", "start", "end", "thickness_m", "height_m", "kind",
                 "boundary_kind", "source", "cad_provenance")
    opening_keys = ("id", "wall_id", "wall_assembly_id", "kind", "offset_m", "width_m", "height_m",
                    "sill_height_m", "width_source", "height_source",
                    "sill_height_source", "source", "cad_provenance")
    room_keys = ("id", "polygon", "floor_elevation_m", "ceiling_height_m",
                 "source", "cad_provenance")
    # ``room_id`` and the reference-anchor readiness fields are semantic
    # projections derived after CAD parsing.  They can legitimately change
    # when Gemini names the already-audited room polygons or when a local
    # anchor validator gains more context.  Freeze the INSERT identity,
    # transform and footprint here; do not mistake those derived bindings for
    # a CAD geometry mutation.
    object_keys = ("id", "name", "kind", "position", "insert_position", "size", "rotation_y_deg",
                   "insert_scale", "size_source", "height_source", "cad_world_bbox_m",
                   "cad_local_bbox_m", "rotation_source", "source", "observed", "cad_provenance")

    def select(row: dict, keys: tuple[str, ...]) -> dict:
        return {key: copy.deepcopy(row.get(key)) for key in keys if key in row}

    payload = {
        "walls": [select(row, wall_keys) for row in model.get("walls") or []],
        # WallAssembly is the v3 source-backed authority.  Keep the complete
        # deterministic object in the fact digest; older v2 models simply hash
        # an empty list and remain readable.
        "wall_assemblies": copy.deepcopy(model.get("wall_assemblies") or []),
        "global_wall_footprints": copy.deepcopy(
            model.get("global_wall_footprints") or []),
        "openings": [select(row, opening_keys) for row in model.get("openings") or []],
        "rooms": [select(row, room_keys) for row in (
            model.get("physical_spaces") or model.get("rooms") or [])],
        "fixed_objects": [
            select(row, object_keys) for row in model.get("fixed_objects") or []
            if row.get("source") == "cad" or isinstance(row.get("cad_provenance"), dict)
        ],
    }

    def canonical(value: Any) -> Any:
        if isinstance(value, dict):
            # ``encoded_*`` carries only the undecoded display spelling kept
            # for audit/debugging.  normalize_model intentionally removes it;
            # the decoded effective layer/block and every geometric transform
            # remain covered by the digest.
            return {
                str(key): canonical(item) for key, item in value.items()
                if not str(key).startswith("encoded_")
            }
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if isinstance(value, tuple):
            return [canonical(item) for item in value]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return float(round(float(value), 8))
        return value

    return hashlib.sha256(json.dumps(
        canonical(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def validate_cad_semantic_overlay(before: dict, after: dict) -> dict:
    """Allow semantic labels and AI layout proxies, never new observed facts."""
    hard_errors: list[dict] = []
    before_hash, after_hash = cad_facts_hash(before), cad_facts_hash(after)
    if before_hash != after_hash:
        hard_errors.append({
            "code": "cad_semantic_facts_changed",
            "message": "语义补全改变了 CAD 权威几何、开口或观察物身份",
            "before": before_hash, "after": after_hash,
        })
    for row in after.get("fixed_objects") or []:
        if row.get("source") == "cad" or isinstance(row.get("cad_provenance"), dict):
            continue
        if not (row.get("purpose") == "layout_proxy"
                and row.get("observed") is False
                and row.get("source") == "ai"):
            hard_errors.append({
                "code": "cad_semantic_observed_object_injected", "object_id": row.get("id"),
                "message": "CAD 语义补全只能新增 source=ai、observed=false 的 layout_proxy",
            })
    return {"hard_errors": hard_errors, "before_hash": before_hash, "after_hash": after_hash}


def _feet_inches_to_metres(feet: str, inches: str | None) -> float:
    return (float(feet) * 12.0 + float(inches or 0.0)) * 0.0254


def _architectural_dimension_pairs(text: str) -> list[tuple[float, float]]:
    """Extract explicit ``10'-2\" X 9'-0\"``-style room dimensions."""
    value = str(text or "").replace("’", "'").replace("′", "'")
    value = value.replace("“", '"').replace("”", '"').replace("″", '"')
    length = r"([0-9]+(?:\.[0-9]+)?)\s*'\s*(?:-\s*)?([0-9]+(?:\.[0-9]+)?)?\s*\"?"
    result: list[tuple[float, float]] = []
    for match in re.finditer(length + r"\s*[xX×]\s*" + length, value):
        first = _feet_inches_to_metres(match.group(1), match.group(2))
        second = _feet_inches_to_metres(match.group(3), match.group(4))
        if first > 0 and second > 0:
            result.append((first, second))
    return result


def _architectural_area_m2(text: str) -> list[float]:
    value = str(text or "").replace(",", "")
    result = []
    for match in re.finditer(
            r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:sq\.?\s*ft\.?|sqft|square\s+feet)\b",
            value, re.I):
        area = float(match.group(1)) * 0.09290304
        if area > 0:
            result.append(area)
    return result


def _candidate_polygon_count(rows: list[dict]) -> int:
    """Count scale-invariant closed faces in a candidate line arrangement."""
    try:
        from shapely.geometry import LineString, Point  # type: ignore
        from shapely.ops import polygonize, unary_union  # type: ignore
        lines = [LineString(row.get("points") or []) for row in rows
                 if len(row.get("points") or []) >= 2]
        if not lines:
            return 0
        bounds = _bbox([point for row in rows for point in row.get("points") or []])
        bbox_area = max(
            (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]), 1e-12)
        minimum = bbox_area * 1e-5
        return sum(float(polygon.area) >= minimum
                   for polygon in polygonize(unary_union(lines)))
    except Exception:
        return 0


def _orthographic_plan_root_metrics(rows: list[dict]) -> dict:
    """Measure whether one source root has a two-axis plan-view line field.

    Elevations usually have one dominant long direction; a plan has many long
    architectural runs in both orthogonal directions.  Angles are normalized
    modulo 90 degrees so rotated drawings receive the same result.  This is a
    geometry metric only: layer, block, filename and view-name text are absent.
    """
    segments = []
    for row in rows:
        points = row.get("points") or []
        for first, second in zip(points, points[1:]):
            length = math.dist(first, second)
            if length <= 1e-9:
                continue
            angle = math.degrees(math.atan2(
                float(second[1]) - float(first[1]),
                float(second[0]) - float(first[0]))) % 180.0
            segments.append((length, angle))
    if not segments:
        return {
            "method": "cad_orthographic_long_run_balance_v1",
            "dominant_axis_rotation_deg": 0.0,
            "long_axis_run_counts": [0, 0],
            "long_axis_lengths_m": [0.0, 0.0],
            "long_run_balance_count": 0,
            "orthogonal_length_balance_ratio": 0.0,
            "orthogonal_aligned_length_ratio": 0.0,
        }
    bin_width = 2.0
    bins = [0.0] * int(90 / bin_width)
    for length, angle in segments:
        if length < .10 - 1e-9:
            continue
        bins[int((angle % 90.0) // bin_width) % len(bins)] += length
    winning_bin = max(range(len(bins)), key=lambda index: (bins[index], -index))
    rotation = (winning_bin + .5) * bin_width
    axis_counts = [0, 0]
    axis_lengths = [0.0, 0.0]
    aligned_length = 0.0
    total_length = sum(length for length, _angle in segments)
    for length, angle in segments:
        differences = []
        for axis in (rotation, rotation + 90.0):
            raw = abs(angle - axis) % 180.0
            differences.append(min(raw, 180.0 - raw))
        axis_index = 0 if differences[0] <= differences[1] else 1
        if differences[axis_index] > 5.0 + 1e-9:
            continue
        aligned_length += length
        axis_lengths[axis_index] += length
        if length >= .50 - 1e-9:
            axis_counts[axis_index] += 1
    aligned_axis_length = sum(axis_lengths)
    return {
        "method": "cad_orthographic_long_run_balance_v1",
        "dominant_axis_rotation_deg": round(rotation, 8),
        "minimum_long_run_length_m": .50,
        "maximum_axis_angle_difference_deg": 5.0,
        "long_axis_run_counts": axis_counts,
        "long_axis_lengths_m": [round(value, 8) for value in axis_lengths],
        "long_run_balance_count": min(axis_counts),
        "orthogonal_length_balance_ratio": round(
            min(axis_lengths) / max(aligned_axis_length, 1e-12), 8),
        "orthogonal_aligned_length_ratio": round(
            aligned_length / max(total_length, 1e-12), 8),
        "source_segment_count": len(segments),
    }


def _filter_text_free_projected_plan_structure(
    rows: Sequence[Mapping[str, Any]],
    candidate_bounds: Sequence[float],
    geometry_authority_evidence: Mapping[str, Any],
) -> dict:
    """Keep the unique building-spanning ink component of a dense plan view.

    Some BIM/FreeCAD exports flatten an entire plan, including furniture, into
    thousands of generic LINE children under one INSERT.  Once the independent
    multi-view gate has proved which root is the plan, disconnected furniture
    must not inherit wall authority merely because it shares that root.  This
    filter is intentionally narrow: it requires the text-free orthographic
    proof, a single source root, at least 1,000 LINE primitives, a unique
    component whose ink spans both axes of the selected view, and a retained
    line field that independently remains architectural in both axes.

    The 1 mm connection tolerance is evidence grouping only; it does not alter
    the 20 mm CAD node-snap contract used by wall assembly.
    """
    result = {
        "schema_version": 1,
        "method": "cad_dense_projected_plan_primary_structure_v1",
        "status": "not_applicable",
        "retained_entity_indexes": [],
        "excluded_entity_indexes": [],
    }
    source_rows = [dict(row) for row in rows]
    if len(source_rows) < 1000 or len(candidate_bounds) != 4:
        return result
    proved_candidates = [
        row for row in geometry_authority_evidence.get("candidates") or []
        if isinstance(row, Mapping)
        and row.get("proof_status") == "proved"
        and (
            (isinstance(row.get("orthographic_plan_root_evidence"), Mapping)
             and row["orthographic_plan_root_evidence"].get("method")
             == "cad_multi_view_orthographic_plan_root_v1")
            or
            (isinstance(row.get("orthographic_plan_view_evidence"), Mapping)
             and row["orthographic_plan_view_evidence"].get("method")
             == "cad_multi_view_orthographic_plan_view_v1")
        )
    ]
    if len(proved_candidates) != 1:
        return result
    proved_candidate = proved_candidates[0]
    root_proof = proved_candidate.get("orthographic_plan_root_evidence")
    view_proof = proved_candidate.get("orthographic_plan_view_evidence")
    proof = root_proof or view_proof
    roots = {
        str((row.get("cad_provenance") or {}).get("root_handle") or "")
        for row in source_rows
    }
    if root_proof:
        if (len(roots) != 1 or not next(iter(roots))
                or next(iter(roots)) != str(
                    proof.get("selected_root_handle") or "")):
            return result
    else:
        selected_indexes = {
            int(value) for value in proof.get("selected_entity_indexes") or []
            if isinstance(value, int) or str(value).isdigit()
        }
        source_indexes = {
            int(row.get("entity_index", position))
            for position, row in enumerate(source_rows)
        }
        if not selected_indexes or source_indexes != selected_indexes:
            return result
    if not all(
            str(row.get("entity_type") or "").upper()
            in {"LINE", "ARC", "LWPOLYLINE", "POLYLINE"}
            and len(row.get("points") or []) >= 2
            for row in source_rows):
        return result
    try:
        from shapely.geometry import LineString, Polygon, box  # type: ignore
        from shapely.ops import polygonize, unary_union  # type: ignore
        from shapely.prepared import prep  # type: ignore

        tolerance = .001
        lines = [LineString(row["points"]) for row in source_rows]
        ink = unary_union([
            line.buffer(tolerance, cap_style=2, join_style=2)
            for line in lines if line.length > 1e-9
        ])
        components = list(ink.geoms) if hasattr(ink, "geoms") else [ink]
        components = sorted(
            (component for component in components if not component.is_empty),
            key=lambda component: (-float(component.area), tuple(component.bounds)),
        )
        if len(components) < 2:
            return result
        primary, runner = components[0], components[1]
        min_x, min_y, max_x, max_y = [float(value) for value in candidate_bounds]
        width = max(max_x - min_x, 1e-12)
        depth = max(max_y - min_y, 1e-12)
        primary_width = float(primary.bounds[2] - primary.bounds[0])
        primary_depth = float(primary.bounds[3] - primary.bounds[1])
        width_coverage = primary_width / width
        depth_coverage = primary_depth / depth
        dominance_ratio = float(primary.area) / max(float(runner.area), 1e-12)
        if (width_coverage < .95 - 1e-9
                or depth_coverage < .95 - 1e-9
                or dominance_ratio < 4.0 - 1e-9):
            result.update(
                status="unresolved",
                reason="primary_ink_component_not_unique_or_building_spanning",
            )
            return result
        prepared_primary = prep(primary)
        retained_positions = [
            position for position, line in enumerate(lines)
            if prepared_primary.covers(line.interpolate(.5, normalized=True))
        ]
        component_rows = [source_rows[position] for position in retained_positions]
        component_metrics = _orthographic_plan_root_metrics(component_rows)
        rotation = float(component_metrics.get(
            "dominant_axis_rotation_deg") or 0.0)
        short_nonorthogonal_positions: list[int] = []
        for position in retained_positions:
            line = lines[position]
            first, second = tuple(line.coords[0]), tuple(line.coords[-1])
            angle = math.degrees(math.atan2(
                second[1] - first[1], second[0] - first[0])) % 180.0
            differences = []
            for axis in (rotation, rotation + 90.0):
                raw = abs(angle - axis) % 180.0
                differences.append(min(raw, 180.0 - raw))
            if line.length < .15 - 1e-9 and min(differences) > 5.0 + 1e-9:
                short_nonorthogonal_positions.append(position)
        short_nonorthogonal_set = set(short_nonorthogonal_positions)
        retained_positions = [
            position for position in retained_positions
            if position not in short_nonorthogonal_set
        ]
        # Recover a second high-specificity projected fixture motif: a small
        # closed circular hub with three or more non-orthogonal source spokes.
        # This is common in flattened sanitary/shower symbols.  Architectural
        # walls do not terminate as a fan on a 40--200 mm circular hub, so only
        # the spokes are removed; the surrounding orthogonal enclosure remains
        # authoritative wall evidence.
        radial_fixture_detail_positions: set[int] = set()
        radial_fixture_partition_positions: set[int] = set()
        radial_fixture_evidence: list[dict] = []
        short_circle_positions = [
            position for position, line in enumerate(lines)
            if .005 <= line.length <= .08
        ]
        circle_join_tolerance_m = .005

        def circle_endpoint_key(point: tuple[float, float]) -> tuple[int, int]:
            return (round(point[0] / circle_join_tolerance_m),
                    round(point[1] / circle_join_tolerance_m))

        circle_incident: dict[tuple[int, int], list[int]] = {}
        for position in short_circle_positions:
            line = lines[position]
            for point in (tuple(line.coords[0]), tuple(line.coords[-1])):
                circle_incident.setdefault(circle_endpoint_key(point), []).append(
                    position)
        circle_seen: set[int] = set()
        for seed in short_circle_positions:
            if seed in circle_seen:
                continue
            pending = [seed]
            circle_seen.add(seed)
            component: list[int] = []
            while pending:
                position = pending.pop()
                component.append(position)
                line = lines[position]
                for point in (tuple(line.coords[0]), tuple(line.coords[-1])):
                    for neighbour in circle_incident.get(
                            circle_endpoint_key(point), []):
                        if neighbour not in circle_seen:
                            circle_seen.add(neighbour)
                            pending.append(neighbour)
            if not 12 <= len(component) <= 64:
                continue
            component_points = [
                tuple(point) for position in component
                for point in (lines[position].coords[0], lines[position].coords[-1])
            ]
            center = (
                sum(point[0] for point in component_points) / len(component_points),
                sum(point[1] for point in component_points) / len(component_points),
            )
            radii = [math.dist(center, point) for point in component_points]
            radius = sum(radii) / len(radii)
            component_bounds = unary_union(
                [lines[position] for position in component]).bounds
            span = max(float(component_bounds[2] - component_bounds[0]),
                       float(component_bounds[3] - component_bounds[1]))
            if (not .04 <= radius <= .20
                    or max((abs(value - radius) for value in radii), default=0.0)
                    > .012
                    or span > .45):
                continue
            spoke_positions: list[int] = []
            spoke_far_points: list[tuple[float, float]] = []
            spoke_angles: list[float] = []
            for position in retained_positions:
                line = lines[position]
                if not .15 <= line.length <= 2.0:
                    continue
                first, second = tuple(line.coords[0]), tuple(line.coords[-1])
                first_hub_distance = min(math.dist(first, point)
                                         for point in component_points)
                second_hub_distance = min(math.dist(second, point)
                                          for point in component_points)
                if min(first_hub_distance, second_hub_distance) > .02:
                    continue
                hub_point, far_point = ((first, second)
                                        if first_hub_distance <= second_hub_distance
                                        else (second, first))
                angle = math.degrees(math.atan2(
                    far_point[1] - hub_point[1],
                    far_point[0] - hub_point[0])) % 180.0
                axis_delta = min(
                    min(abs(angle), abs(angle - 180.0)), abs(angle - 90.0))
                if axis_delta <= 5.0 + 1e-9:
                    continue
                spoke_positions.append(position)
                spoke_far_points.append(far_point)
                spoke_angles.append(angle)
            distinct_far_points: list[tuple[float, float]] = []
            for point in spoke_far_points:
                if not any(math.dist(point, existing) <= .08
                           for existing in distinct_far_points):
                    distinct_far_points.append(point)
            angle_bins = {round(angle / 15.0) for angle in spoke_angles}
            if (len(spoke_positions) < 3 or len(distinct_far_points) < 3
                    or len(angle_bins) < 3):
                continue
            local_partition_positions: set[int] = set()
            for position in retained_positions:
                rail = lines[position]
                if not .50 <= rail.length <= 2.0:
                    continue
                rail_start, rail_end = (tuple(rail.coords[0]),
                                        tuple(rail.coords[-1]))
                start_hits = [point for point in distinct_far_points
                              if math.dist(rail_start, point) <= .03]
                end_hits = [point for point in distinct_far_points
                            if math.dist(rail_end, point) <= .03]
                if not start_hits or not end_hits or any(
                        math.dist(first, second) <= .08
                        for first in start_hits for second in end_hits):
                    continue
                rail_angle = math.degrees(math.atan2(
                    rail_end[1] - rail_start[1],
                    rail_end[0] - rail_start[0])) % 180.0
                rail_axis_delta = min(
                    min(abs(rail_angle), abs(rail_angle - 180.0)),
                    abs(rail_angle - 90.0))
                if rail_axis_delta > 2.0:
                    continue
                compact_companions: list[int] = []
                rail_bounds = tuple(float(value) for value in rail.bounds)
                rail_vertical = abs(rail_angle - 90.0) <= 2.0
                for companion_position in retained_positions:
                    if companion_position == position:
                        continue
                    companion = lines[companion_position]
                    if not .50 <= companion.length <= 2.0:
                        continue
                    companion_start, companion_end = (
                        tuple(companion.coords[0]), tuple(companion.coords[-1]))
                    companion_angle = math.degrees(math.atan2(
                        companion_end[1] - companion_start[1],
                        companion_end[0] - companion_start[0])) % 180.0
                    raw_difference = abs(rail_angle - companion_angle) % 180.0
                    if min(raw_difference, 180.0 - raw_difference) > 1.0:
                        continue
                    separation = float(rail.distance(companion))
                    if not .04 <= separation <= .15:
                        continue
                    companion_bounds = tuple(float(value)
                                             for value in companion.bounds)
                    if rail_vertical:
                        overlap = max(0.0, min(rail_bounds[3], companion_bounds[3])
                                      - max(rail_bounds[1], companion_bounds[1]))
                    else:
                        overlap = max(0.0, min(rail_bounds[2], companion_bounds[2])
                                      - max(rail_bounds[0], companion_bounds[0]))
                    if (overlap / max(float(rail.length), 1e-12) >= .90
                            and abs(float(companion.length)
                                    - float(rail.length)) <= .15):
                        compact_companions.append(companion_position)
                if compact_companions:
                    local_partition_positions.add(position)
                    local_partition_positions.update(compact_companions)
            radial_fixture_detail_positions.update(spoke_positions)
            radial_fixture_partition_positions.update(
                local_partition_positions)
            radial_fixture_evidence.append({
                "hub_center_m": [round(center[0], 8), round(center[1], 8)],
                "hub_radius_m": round(radius, 8),
                "hub_chord_count": len(component),
                "spoke_entity_indexes": sorted(
                    int(source_rows[position].get("entity_index", position))
                    for position in spoke_positions),
                "distinct_spoke_endpoint_count": len(distinct_far_points),
                "spoke_direction_bin_count": len(angle_bins),
                "compact_partition_entity_indexes": sorted(
                    int(source_rows[position].get("entity_index", position))
                    for position in local_partition_positions),
                "decision_basis": [
                    "small_closed_circular_source_hub",
                    "three_or_more_nonorthogonal_radial_source_spokes",
                    "three_or_more_distinct_far_endpoints",
                    "compact_double_rail_partition_with_two_spoke_endpoints_removed",
                    "surrounding_orthogonal_enclosure_preserved",
                ],
            })
        if (radial_fixture_detail_positions
                or radial_fixture_partition_positions):
            retained_positions = [
                position for position in retained_positions
                if (position not in radial_fixture_detail_positions
                    and position not in radial_fixture_partition_positions)
            ]
        # A projected BIM plan can merge a bed that is flush against a wall
        # into the building-spanning ink component.  Recognise only a highly
        # specific source motif: a 1.4--2.4 m near-square orthogonal frame,
        # a closed non-orthogonal textile triangle inside it, and at least 40
        # short curved-detail chords (pillows/headboard) in the same frame.
        # A frame side independently paired with a parallel structural face
        # outside the frame is preserved, because a flush item can share the
        # visible inner wall face in a flattened projection.
        projected_bed_detail_positions: set[int] = set()
        projected_bed_evidence: list[dict] = []
        projected_bed_candidate_diagnostics: list[dict] = []
        projected_compact_bay_detail_positions: set[int] = set()
        projected_compact_bay_evidence: list[dict] = []
        projected_staggered_counter_detail_positions: set[int] = set()
        projected_staggered_counter_evidence: list[dict] = []
        world_axis_rotation = min(
            abs(rotation % 180.0), abs((rotation % 180.0) - 90.0),
            abs((rotation % 180.0) - 180.0))
        if world_axis_rotation <= 5.0 + 1e-9:
            retained_facts: list[dict] = []
            for position in retained_positions:
                line = lines[position]
                first, second = tuple(line.coords[0]), tuple(line.coords[-1])
                angle = math.degrees(math.atan2(
                    second[1] - first[1], second[0] - first[0])) % 180.0
                horizontal_delta = min(abs(angle), abs(angle - 180.0))
                vertical_delta = abs(angle - 90.0)
                axis = ("horizontal" if horizontal_delta <= 2.0
                        else "vertical" if vertical_delta <= 2.0 else "other")
                retained_facts.append({
                    "position": position, "line": line, "angle": angle,
                    "axis": axis, "bounds": tuple(float(value)
                                                    for value in line.bounds),
                })
            verticals = [fact for fact in retained_facts
                         if fact["axis"] == "vertical"
                         and 1.4 <= fact["line"].length <= 2.4]
            horizontals = [fact["line"] for fact in retained_facts
                           if fact["axis"] == "horizontal"]
            horizontal_union = unary_union(horizontals) if horizontals else None
            excluded_position_set = set(range(len(source_rows))) - set(
                retained_positions)

            def interval_overlap_ratio(first: dict, second: dict) -> float:
                if first["axis"] == "vertical":
                    overlap = max(0.0, min(first["bounds"][3], second["bounds"][3])
                                  - max(first["bounds"][1], second["bounds"][1]))
                else:
                    overlap = max(0.0, min(first["bounds"][2], second["bounds"][2])
                                  - max(first["bounds"][0], second["bounds"][0]))
                return overlap / max(float(first["line"].length), 1e-12)

            for first_number, first_fact in enumerate(verticals):
                for second_fact in verticals[first_number + 1:]:
                    first_x = (first_fact["bounds"][0] + first_fact["bounds"][2]) / 2
                    second_x = (second_fact["bounds"][0] + second_fact["bounds"][2]) / 2
                    frame_min_x, frame_max_x = sorted((first_x, second_x))
                    frame_width = frame_max_x - frame_min_x
                    frame_min_y = max(first_fact["bounds"][1],
                                      second_fact["bounds"][1])
                    frame_max_y = min(first_fact["bounds"][3],
                                      second_fact["bounds"][3])
                    frame_depth = frame_max_y - frame_min_y
                    if (not 1.4 <= frame_width <= 2.4
                            or not 1.4 <= frame_depth <= 2.4
                            or max(frame_width, frame_depth)
                            / max(min(frame_width, frame_depth), 1e-12) > 1.6):
                        continue
                    lower = LineString([(frame_min_x, frame_min_y),
                                        (frame_max_x, frame_min_y)])
                    upper = LineString([(frame_min_x, frame_max_y),
                                        (frame_max_x, frame_max_y)])
                    if horizontal_union is None:
                        continue
                    horizontal_cover = horizontal_union.buffer(
                        .012, cap_style=2, join_style=2)
                    lower_ratio = 1.0 - float(
                        lower.difference(horizontal_cover).length) / frame_width
                    upper_ratio = 1.0 - float(
                        upper.difference(horizontal_cover).length) / frame_width
                    if min(lower_ratio, upper_ratio) < .98 - 1e-9:
                        projected_bed_candidate_diagnostics.append({
                            "bbox_m": [round(frame_min_x, 8), round(frame_min_y, 8),
                                       round(frame_max_x, 8), round(frame_max_y, 8)],
                            "status": "rejected_boundary_coverage",
                            "lower_boundary_coverage_ratio": round(lower_ratio, 8),
                            "upper_boundary_coverage_ratio": round(upper_ratio, 8),
                        })
                        continue
                    frame = Polygon([
                        (frame_min_x, frame_min_y),
                        (frame_max_x, frame_min_y),
                        (frame_max_x, frame_max_y),
                        (frame_min_x, frame_max_y),
                    ])
                    frame_cover = frame.buffer(.012, join_style=2)
                    internal_nonorthogonal = []
                    for position, line in enumerate(lines):
                        if (line.length < .20
                                or not frame_cover.covers(line)):
                            continue
                        start, end = tuple(line.coords[0]), tuple(line.coords[-1])
                        angle = math.degrees(math.atan2(
                            end[1] - start[1], end[0] - start[0])) % 180.0
                        if (min(abs(angle), abs(angle - 180.0)) <= 2.0
                                or abs(angle - 90.0) <= 2.0):
                            continue
                        internal_nonorthogonal.append({
                            "position": position, "line": line,
                        })
                    textile_faces = list(polygonize(unary_union([
                        fact["line"] for fact in internal_nonorthogonal
                    ]))) if len(internal_nonorthogonal) >= 3 else []
                    textile_faces = [face for face in textile_faces
                                     if .05 <= float(face.area) <= .80
                                     and frame_cover.covers(face)]
                    if not textile_faces:
                        projected_bed_candidate_diagnostics.append({
                            "bbox_m": [round(value, 8) for value in frame.bounds],
                            "status": "rejected_textile_face",
                            "internal_nonorthogonal_line_count": len(
                                internal_nonorthogonal),
                        })
                        continue
                    detail_positions = [
                        position for position in excluded_position_set
                        if lines[position].length <= .10 + 1e-9
                        and frame_cover.covers(
                            lines[position].interpolate(.5, normalized=True))
                    ]
                    if len(detail_positions) < 40:
                        projected_bed_candidate_diagnostics.append({
                            "bbox_m": [round(value, 8) for value in frame.bounds],
                            "status": "rejected_curved_detail_density",
                            "closed_textile_face_count": len(textile_faces),
                            "short_curved_detail_chord_count": len(detail_positions),
                        })
                        continue

                    removable: set[int] = set()
                    preserved: set[int] = set()
                    for fact in retained_facts:
                        if not frame_cover.covers(fact["line"]):
                            continue
                        structurally_paired = False
                        if fact["axis"] in {"horizontal", "vertical"}:
                            for support in retained_facts:
                                if (support["position"] == fact["position"]
                                        or support["axis"] != fact["axis"]
                                        or frame.buffer(.02).covers(
                                            support["line"].interpolate(
                                                .5, normalized=True))):
                                    continue
                                separation = float(fact["line"].distance(
                                    support["line"]))
                                if (.06 <= separation <= .60
                                        and interval_overlap_ratio(
                                            fact, support) >= .80 - 1e-9):
                                    structurally_paired = True
                                    break
                        if structurally_paired:
                            preserved.add(int(fact["position"]))
                        else:
                            removable.add(int(fact["position"]))
                    if len(removable) < 3:
                        projected_bed_candidate_diagnostics.append({
                            "bbox_m": [round(value, 8) for value in frame.bounds],
                            "status": "rejected_removable_frame_scope",
                            "removable_entity_count": len(removable),
                            "preserved_entity_count": len(preserved),
                        })
                        continue
                    projected_bed_detail_positions.update(removable)
                    projected_bed_evidence.append({
                        "bbox_m": [round(value, 8) for value in frame.bounds],
                        "width_m": round(frame_width, 8),
                        "depth_m": round(frame_depth, 8),
                        "closed_textile_face_count": len(textile_faces),
                        "short_curved_detail_chord_count": len(detail_positions),
                        "removed_entity_indexes": sorted(
                            int(source_rows[position].get("entity_index", position))
                            for position in removable),
                        "preserved_structural_pair_entity_indexes": sorted(
                            int(source_rows[position].get("entity_index", position))
                            for position in preserved),
                        "decision_basis": [
                            "near_square_sleeping_furniture_frame",
                            "closed_nonorthogonal_textile_triangle_inside_frame",
                            "dense_short_curved_pillow_or_headboard_detail",
                            "outside_parallel_wall_face_preserved",
                        ],
                    })
            if projected_bed_detail_positions:
                retained_positions = [
                    position for position in retained_positions
                    if position not in projected_bed_detail_positions
                ]

        # Some flattened BIM plans connect fitted cabinetry to the main ink
        # component, so connectivity alone cannot separate it from walls.  A
        # particularly stable source signature is a run of two or more narrow
        # rectangular bays that share one long rail, are separated by only a
        # drafting-sized break, and carry several <=60 mm trim/return strokes.
        # Real double-line walls can have transverse caps, but do not normally
        # form a repeated interior cabinet-bay chain with those tiny source
        # returns.  Apply this only inside the independently proved dense plan
        # view and keep the whole motif at least 150 mm inside its building-
        # spanning ink bounds.  No layer, block, colour or handle names enter
        # the decision.
        if world_axis_rotation <= 5.0 + 1e-9:
            bay_facts: list[dict] = []
            for position in retained_positions:
                line = lines[position]
                if line.length <= 1e-9:
                    continue
                first, second = tuple(line.coords[0]), tuple(line.coords[-1])
                angle = math.degrees(math.atan2(
                    second[1] - first[1], second[0] - first[0])) % 180.0
                horizontal_delta = min(abs(angle), abs(angle - 180.0))
                vertical_delta = abs(angle - 90.0)
                axis = ("horizontal" if horizontal_delta <= 2.0
                        else "vertical" if vertical_delta <= 2.0 else "")
                if not axis:
                    continue
                bounds = tuple(float(value) for value in line.bounds)
                bay_facts.append({
                    "position": position, "line": line, "axis": axis,
                    "fixed": ((bounds[1] + bounds[3]) / 2.0
                              if axis == "horizontal"
                              else (bounds[0] + bounds[2]) / 2.0),
                    "interval": ((bounds[0], bounds[2])
                                 if axis == "horizontal"
                                 else (bounds[1], bounds[3])),
                    "bounds": bounds,
                })
            axis_unions = {
                axis: unary_union([
                    fact["line"] for fact in bay_facts
                    if fact["axis"] == axis
                ])
                for axis in ("horizontal", "vertical")
            }
            cap_tolerance_m = .035
            bays: list[dict] = []
            for first_number, first_fact in enumerate(bay_facts):
                for second_fact in bay_facts[first_number + 1:]:
                    if first_fact["axis"] != second_fact["axis"]:
                        continue
                    separation = abs(float(first_fact["fixed"])
                                     - float(second_fact["fixed"]))
                    # Repeated fitted cabinet/service bays have meaningful
                    # front-to-back depth.  Thin paired rails around 100 mm
                    # are commonly window/frame drafting, and treating two
                    # adjacent frames as cabinetry can delete an opening
                    # host before the canonical opening pass.
                    if not .25 <= separation <= .75:
                        continue
                    overlap_start = max(first_fact["interval"][0],
                                        second_fact["interval"][0])
                    overlap_end = min(first_fact["interval"][1],
                                      second_fact["interval"][1])
                    overlap_length = overlap_end - overlap_start
                    if (not .65 <= overlap_length <= 1.30
                            or overlap_length / separation < 1.20):
                        continue
                    transverse_axis = ("vertical"
                                       if first_fact["axis"] == "horizontal"
                                       else "horizontal")
                    transverse_cover = axis_unions[transverse_axis].buffer(
                        cap_tolerance_m, cap_style=2, join_style=2)
                    lower = min(float(first_fact["fixed"]),
                                float(second_fact["fixed"]))
                    upper = max(float(first_fact["fixed"]),
                                float(second_fact["fixed"]))
                    cap_coverages = []
                    for endpoint in (overlap_start, overlap_end):
                        cap = (LineString([(endpoint, lower),
                                           (endpoint, upper)])
                               if first_fact["axis"] == "horizontal"
                               else LineString([(lower, endpoint),
                                                (upper, endpoint)]))
                        cap_coverages.append(float(
                            cap.intersection(transverse_cover).length)
                            / separation)
                    if min(cap_coverages) < .80 - 1e-9:
                        continue
                    polygon = (Polygon([
                        (overlap_start, lower), (overlap_end, lower),
                        (overlap_end, upper), (overlap_start, upper),
                    ]) if first_fact["axis"] == "horizontal" else Polygon([
                        (lower, overlap_start), (upper, overlap_start),
                        (upper, overlap_end), (lower, overlap_end),
                    ]))
                    bays.append({
                        "axis": first_fact["axis"],
                        "rail_positions": [first_fact["position"],
                                           second_fact["position"]],
                        "lower": lower, "upper": upper,
                        "start": overlap_start, "end": overlap_end,
                        "separation_m": separation,
                        "cap_coverages": cap_coverages,
                        "polygon": polygon,
                    })

            neighbours: dict[int, set[int]] = defaultdict(set)
            for first_number, first_bay in enumerate(bays):
                for second_number, second_bay in enumerate(
                        bays[first_number + 1:], first_number + 1):
                    if first_bay["axis"] != second_bay["axis"]:
                        continue
                    shared_rail_distance = min(
                        abs(float(first_value) - float(second_value))
                        for first_value in (first_bay["lower"],
                                            first_bay["upper"])
                        for second_value in (second_bay["lower"],
                                             second_bay["upper"])
                    )
                    axial_gap = max(
                        0.0,
                        max(float(first_bay["start"]),
                            float(second_bay["start"]))
                        - min(float(first_bay["end"]),
                              float(second_bay["end"])),
                    )
                    if (shared_rail_distance <= .03 + 1e-9
                            and axial_gap <= .08 + 1e-9):
                        neighbours[first_number].add(second_number)
                        neighbours[second_number].add(first_number)
            visited_bays: set[int] = set()
            for seed in range(len(bays)):
                if seed in visited_bays:
                    continue
                pending = [seed]
                visited_bays.add(seed)
                component_indexes: list[int] = []
                while pending:
                    current = pending.pop()
                    component_indexes.append(current)
                    for neighbour in neighbours.get(current, set()):
                        if neighbour not in visited_bays:
                            visited_bays.add(neighbour)
                            pending.append(neighbour)
                if len(component_indexes) < 2:
                    continue
                component = [bays[index] for index in component_indexes]
                axial_start = min(float(row["start"]) for row in component)
                axial_end = max(float(row["end"]) for row in component)
                axial_span = axial_end - axial_start
                lateral_min = min(float(row["lower"]) for row in component)
                lateral_max = max(float(row["upper"]) for row in component)
                lateral_span = lateral_max - lateral_min
                if (not 1.35 <= axial_span <= 4.0
                        or not .25 <= lateral_span <= .90):
                    continue
                envelope = (Polygon([
                    (axial_start, lateral_min), (axial_end, lateral_min),
                    (axial_end, lateral_max), (axial_start, lateral_max),
                ]) if component[0]["axis"] == "horizontal" else Polygon([
                    (lateral_min, axial_start), (lateral_max, axial_start),
                    (lateral_max, axial_end), (lateral_min, axial_end),
                ]))
                primary_bounds = tuple(float(value) for value in primary.bounds)
                envelope_bounds = tuple(float(value) for value in envelope.bounds)
                if min(
                    envelope_bounds[0] - primary_bounds[0],
                    envelope_bounds[1] - primary_bounds[1],
                    primary_bounds[2] - envelope_bounds[2],
                    primary_bounds[3] - envelope_bounds[3],
                ) < .15 - 1e-9:
                    continue
                envelope_cover = envelope.buffer(
                    cap_tolerance_m, join_style=2)
                core_removable = {
                    int(fact["position"]) for fact in bay_facts
                    if envelope_cover.covers(fact["line"])
                }
                # Expand only along the proved repeated-bay chain.  A square
                # 180 mm buffer used to swallow perpendicular wall/opening
                # evidence next to compact cabinets (real case 10, door
                # handle B37).  Perpendicular growth remains at the same
                # 35 mm source-connection tolerance used by the core proof.
                axial_extension_m = .18
                lateral_extension_m = cap_tolerance_m
                if component[0]["axis"] == "horizontal":
                    motif_extension_cover = box(
                        axial_start - axial_extension_m,
                        lateral_min - lateral_extension_m,
                        axial_end + axial_extension_m,
                        lateral_max + lateral_extension_m,
                    )
                else:
                    motif_extension_cover = box(
                        lateral_min - lateral_extension_m,
                        axial_start - axial_extension_m,
                        lateral_max + lateral_extension_m,
                        axial_end + axial_extension_m,
                    )
                extension_removable = {
                    int(fact["position"]) for fact in bay_facts
                    if fact["line"].length <= 1.30 + 1e-9
                    and motif_extension_cover.covers(fact["line"])
                }
                removable = core_removable | extension_removable
                trim_positions = {
                    position for position in removable
                    if lines[position].length <= .06 + 1e-9
                }
                if len(removable) < 8 or len(trim_positions) < 4:
                    continue
                if removable.issubset(projected_compact_bay_detail_positions):
                    continue
                projected_compact_bay_detail_positions.update(removable)
                projected_compact_bay_evidence.append({
                    "bbox_m": [round(value, 8) for value in envelope_bounds],
                    "axis": component[0]["axis"],
                    "bay_count": len(component_indexes),
                    "axial_span_m": round(axial_span, 8),
                    "lateral_span_m": round(lateral_span, 8),
                    "trim_return_count": len(trim_positions),
                    "bounded_extension_entity_count": len(
                        extension_removable - core_removable),
                    "removed_entity_indexes": sorted(
                        int(source_rows[position].get("entity_index", position))
                        for position in removable),
                    "thresholds": {
                        "minimum_bay_count": 2,
                        "minimum_bay_long_span_m": .65,
                        "maximum_bay_long_span_m": 1.30,
                        "minimum_bay_short_span_m": .25,
                        "maximum_bay_short_span_m": .75,
                        "minimum_bay_aspect_ratio": 1.20,
                        "minimum_cap_coverage_ratio": .80,
                        "maximum_cap_connection_tolerance_m":
                            cap_tolerance_m,
                        "maximum_shared_rail_distance_m": .03,
                        "maximum_adjacent_bay_gap_m": .08,
                        "minimum_chain_axial_span_m": 1.35,
                        "minimum_trim_return_count": 4,
                        "maximum_trim_return_length_m": .06,
                        "maximum_bounded_axial_extension_distance_m":
                            axial_extension_m,
                        "maximum_bounded_lateral_extension_distance_m":
                            lateral_extension_m,
                        "maximum_bounded_extension_entity_length_m": 1.30,
                        "minimum_primary_bounds_clearance_m": .15,
                    },
                    "decision_basis": [
                        "two_or_more_adjacent_narrow_rectangular_bays",
                        "one_shared_long_rail_and_bounded_axial_gaps",
                        "multiple_source_trim_or_return_strokes",
                        "motif_strictly_inside_building_spanning_ink_bounds",
                        "no_layer_block_colour_handle_or_name_semantics",
                    ],
                })
            if projected_compact_bay_detail_positions:
                retained_positions = [
                    position for position in retained_positions
                    if position not in projected_compact_bay_detail_positions
                ]

        # A second fitted-counter signature has one continuous long rail and
        # an opposite rail exported as several slightly staggered segments.
        # The rail geometry alone is not enough to distinguish it from a
        # deliberately segmented wall.  Require all of the following source
        # evidence: near-complete opposite-rail coverage, two terminal caps,
        # at least two aligned compact rectangular appliance faces inside the
        # strip, and at least eight drafting-sized return strokes.  This gate
        # is intentionally more restrictive than the repeated-bay rule above
        # and remains scoped to the independently proved dense plan view.
        if world_axis_rotation <= 5.0 + 1e-9:
            counter_facts: list[dict] = []
            source_axis_facts: list[dict] = []
            retained_position_set = set(retained_positions)
            for position, line in enumerate(lines):
                if line.length <= 1e-9:
                    continue
                first, second = tuple(line.coords[0]), tuple(line.coords[-1])
                angle = math.degrees(math.atan2(
                    second[1] - first[1], second[0] - first[0])) % 180.0
                horizontal_delta = min(abs(angle), abs(angle - 180.0))
                vertical_delta = abs(angle - 90.0)
                axis = ("horizontal" if horizontal_delta <= 2.0
                        else "vertical" if vertical_delta <= 2.0 else "")
                if not axis:
                    continue
                bounds = tuple(float(value) for value in line.bounds)
                fact = {
                    "position": position,
                    "line": line,
                    "axis": axis,
                    "fixed": ((bounds[1] + bounds[3]) / 2.0
                              if axis == "horizontal"
                              else (bounds[0] + bounds[2]) / 2.0),
                    "interval": ((bounds[0], bounds[2])
                                 if axis == "horizontal"
                                 else (bounds[1], bounds[3])),
                    "bounds": bounds,
                }
                source_axis_facts.append(fact)
                if position in retained_position_set:
                    counter_facts.append(fact)
            source_axis_unions = {
                axis: unary_union([
                    fact["line"] for fact in source_axis_facts
                    if fact["axis"] == axis
                ])
                for axis in ("horizontal", "vertical")
            }

            def merge_counter_intervals(
                    intervals: Sequence[tuple[float, float]],
            ) -> list[tuple[float, float]]:
                merged: list[tuple[float, float]] = []
                for start, end in sorted(intervals):
                    if end <= start + 1e-12:
                        continue
                    if merged and start <= merged[-1][1] + 1e-9:
                        merged[-1] = (merged[-1][0],
                                      max(merged[-1][1], end))
                    else:
                        merged.append((start, end))
                return merged

            for rail in counter_facts:
                if not 1.50 <= float(rail["line"].length) <= 4.0:
                    continue
                companions = [
                    fact for fact in counter_facts
                    if fact["position"] != rail["position"]
                    and fact["axis"] == rail["axis"]
                    and fact["line"].length >= .20 - 1e-9
                    and .35 <= abs(float(fact["fixed"])
                                   - float(rail["fixed"])) <= .80
                    and fact["interval"][1] >= rail["interval"][0] - .04
                    and fact["interval"][0] <= rail["interval"][1] + .04
                ]
                companions.sort(key=lambda fact: (
                    float(fact["fixed"]), float(fact["interval"][0]),
                    float(fact["interval"][1]), int(fact["position"])))
                for first_number, first_companion in enumerate(companions):
                    fixed_limit = float(first_companion["fixed"]) + .03
                    group = [
                        fact for fact in companions[first_number:]
                        if float(fact["fixed"]) <= fixed_limit + 1e-9
                    ]
                    if len(group) < 3:
                        continue
                    clipped_intervals = [
                        (max(float(rail["interval"][0]),
                             float(fact["interval"][0])),
                         min(float(rail["interval"][1]),
                             float(fact["interval"][1])))
                        for fact in group
                    ]
                    merged_intervals = merge_counter_intervals(
                        clipped_intervals)
                    if not merged_intervals:
                        continue
                    rail_span = (float(rail["interval"][1])
                                 - float(rail["interval"][0]))
                    covered_length = sum(
                        end - start for start, end in merged_intervals)
                    coverage_ratio = covered_length / max(rail_span, 1e-12)
                    internal_gaps = [
                        merged_intervals[number + 1][0]
                        - merged_intervals[number][1]
                        for number in range(len(merged_intervals) - 1)
                    ]
                    terminal_gaps = [
                        merged_intervals[0][0]
                        - float(rail["interval"][0]),
                        float(rail["interval"][1])
                        - merged_intervals[-1][1],
                    ]
                    maximum_gap = max(internal_gaps + terminal_gaps + [0.0])
                    if (coverage_ratio < .90 - 1e-9
                            or maximum_gap > .04 + 1e-9):
                        continue
                    lateral_min = min(
                        float(rail["fixed"]),
                        min(float(fact["fixed"]) for fact in group),
                    )
                    lateral_max = max(
                        float(rail["fixed"]),
                        max(float(fact["fixed"]) for fact in group),
                    )
                    lateral_span = lateral_max - lateral_min
                    axial_start = float(rail["interval"][0])
                    axial_end = float(rail["interval"][1])
                    envelope = (Polygon([
                        (axial_start, lateral_min),
                        (axial_end, lateral_min),
                        (axial_end, lateral_max),
                        (axial_start, lateral_max),
                    ]) if rail["axis"] == "horizontal" else Polygon([
                        (lateral_min, axial_start),
                        (lateral_max, axial_start),
                        (lateral_max, axial_end),
                        (lateral_min, axial_end),
                    ]))
                    envelope_bounds = tuple(float(value)
                                            for value in envelope.bounds)
                    primary_bounds = tuple(float(value)
                                           for value in primary.bounds)
                    if min(
                        envelope_bounds[0] - primary_bounds[0],
                        envelope_bounds[1] - primary_bounds[1],
                        primary_bounds[2] - envelope_bounds[2],
                        primary_bounds[3] - envelope_bounds[3],
                    ) < .15 - 1e-9:
                        continue
                    transverse_axis = ("vertical"
                                       if rail["axis"] == "horizontal"
                                       else "horizontal")
                    transverse_cover = source_axis_unions[
                        transverse_axis].buffer(
                            .035, cap_style=2, join_style=2)
                    cap_coverages: list[float] = []
                    for endpoint in (axial_start, axial_end):
                        cap = (LineString([(endpoint, lateral_min),
                                           (endpoint, lateral_max)])
                               if rail["axis"] == "horizontal"
                               else LineString([(lateral_min, endpoint),
                                                (lateral_max, endpoint)]))
                        cap_coverages.append(float(
                            cap.intersection(transverse_cover).length)
                            / max(lateral_span, 1e-12))
                    if min(cap_coverages) < .80 - 1e-9:
                        continue
                    envelope_cover = envelope.buffer(.035, join_style=2)
                    local_source_positions = [
                        position for position, line in enumerate(lines)
                        if envelope_cover.covers(line)
                    ]
                    trim_positions = [
                        position for position in local_source_positions
                        if lines[position].length <= .06 + 1e-9
                    ]
                    if len(trim_positions) < 8:
                        continue
                    local_faces = list(polygonize(unary_union([
                        lines[position] for position in local_source_positions
                    ])))
                    compact_rectangular_faces: list[Any] = []
                    for face in local_faces:
                        face_bounds = tuple(float(value)
                                            for value in face.bounds)
                        dimensions = sorted((
                            face_bounds[2] - face_bounds[0],
                            face_bounds[3] - face_bounds[1],
                        ))
                        rotated_rectangle = face.minimum_rotated_rectangle
                        rectangularity = (float(face.area)
                                          / max(float(rotated_rectangle.area),
                                                1e-12))
                        lateral_clearance = (min(
                            face_bounds[0] - envelope_bounds[0],
                            envelope_bounds[2] - face_bounds[2],
                        ) if rail["axis"] == "vertical" else min(
                            face_bounds[1] - envelope_bounds[1],
                            envelope_bounds[3] - face_bounds[3],
                        ))
                        if (not .05 <= float(face.area) <= .80
                                or dimensions[0] < .15 - 1e-9
                                or dimensions[1] > 1.20 + 1e-9
                                or lateral_clearance < .05 - 1e-9
                                or rectangularity < .95 - 1e-9):
                            continue
                        compact_rectangular_faces.append(face)
                    aligned_face_pairs: list[tuple[Any, Any]] = []
                    for first_face_number, first_face in enumerate(
                            compact_rectangular_faces):
                        for second_face in compact_rectangular_faces[
                                first_face_number + 1:]:
                            first_bounds = tuple(float(value)
                                                 for value in first_face.bounds)
                            second_bounds = tuple(float(value)
                                                  for value in second_face.bounds)
                            if rail["axis"] == "vertical":
                                lateral_overlap = max(
                                    0.0,
                                    min(first_bounds[2], second_bounds[2])
                                    - max(first_bounds[0], second_bounds[0]),
                                )
                                minimum_lateral_span = min(
                                    first_bounds[2] - first_bounds[0],
                                    second_bounds[2] - second_bounds[0],
                                )
                                axial_gap = max(
                                    0.0,
                                    max(first_bounds[1], second_bounds[1])
                                    - min(first_bounds[3], second_bounds[3]),
                                )
                            else:
                                lateral_overlap = max(
                                    0.0,
                                    min(first_bounds[3], second_bounds[3])
                                    - max(first_bounds[1], second_bounds[1]),
                                )
                                minimum_lateral_span = min(
                                    first_bounds[3] - first_bounds[1],
                                    second_bounds[3] - second_bounds[1],
                                )
                                axial_gap = max(
                                    0.0,
                                    max(first_bounds[0], second_bounds[0])
                                    - min(first_bounds[2], second_bounds[2]),
                                )
                            if (lateral_overlap
                                    / max(minimum_lateral_span, 1e-12)
                                    >= .90 - 1e-9
                                    and axial_gap <= .08 + 1e-9):
                                aligned_face_pairs.append(
                                    (first_face, second_face))
                    if not aligned_face_pairs:
                        continue
                    # A proved staggered counter can connect to an L-shaped
                    # appliance/cabinet extension on the companion-rail side.
                    # Grow into that extension only when a compact rectangular
                    # device face (400--1200 mm sides) carries at least forty
                    # <=60 mm source-detail strokes.  The local graph is capped
                    # at 2 m per segment and a fixed search envelope, so a long
                    # architectural wall touching the fixture cannot be pulled
                    # into the removable component.
                    group_fixed_center = statistics.median([
                        float(fact["fixed"]) for fact in group])
                    companion_negative = (
                        group_fixed_center < float(rail["fixed"]))
                    if rail["axis"] == "vertical":
                        search_min_x = (lateral_min - .95
                                        if companion_negative
                                        else lateral_min)
                        search_max_x = (lateral_max
                                        if companion_negative
                                        else lateral_max + .95)
                        search_min_y = axial_start - .25
                        search_max_y = axial_end + .75
                    else:
                        search_min_x = axial_start - .25
                        search_max_x = axial_end + .75
                        search_min_y = (lateral_min - .95
                                        if companion_negative
                                        else lateral_min)
                        search_max_y = (lateral_max
                                        if companion_negative
                                        else lateral_max + .95)
                    compound_search = Polygon([
                        (search_min_x, search_min_y),
                        (search_max_x, search_min_y),
                        (search_max_x, search_max_y),
                        (search_min_x, search_max_y),
                    ])
                    compound_search_positions = [
                        position for position, line in enumerate(lines)
                        if line.length <= 2.0 + 1e-9
                        and compound_search.covers(line)
                    ]
                    compound_faces = list(polygonize(unary_union([
                        lines[position]
                        for position in compound_search_positions
                    ])))
                    compound_component_positions: set[int] = set()
                    compound_device_face_evidence: list[dict] = []
                    for device_face in compound_faces:
                        device_bounds = tuple(float(value)
                                              for value in device_face.bounds)
                        device_dimensions = sorted((
                            device_bounds[2] - device_bounds[0],
                            device_bounds[3] - device_bounds[1],
                        ))
                        device_rectangle = (
                            device_face.minimum_rotated_rectangle)
                        device_rectangularity = (
                            float(device_face.area)
                            / max(float(device_rectangle.area), 1e-12))
                        if (not .25 <= float(device_face.area) <= .80
                                or device_dimensions[0] < .40 - 1e-9
                                or device_dimensions[1] > 1.20 + 1e-9
                                or device_rectangularity < .95 - 1e-9
                                or float(device_face.distance(envelope))
                                > .20 + 1e-9):
                            continue
                        if rail["axis"] == "vertical":
                            device_on_companion_side = (
                                device_bounds[2] <= lateral_min + .05
                                if companion_negative else
                                device_bounds[0] >= lateral_max - .05)
                        else:
                            device_on_companion_side = (
                                device_bounds[3] <= lateral_min + .05
                                if companion_negative else
                                device_bounds[1] >= lateral_max - .05)
                        if not device_on_companion_side:
                            continue
                        device_detail_cover = device_face.buffer(
                            .15, join_style=2)
                        device_micro_positions = [
                            position for position
                            in compound_search_positions
                            if lines[position].length <= .06 + 1e-9
                            and device_detail_cover.intersects(lines[position])
                        ]
                        if len(device_micro_positions) < 40:
                            continue
                        seed_positions = {
                            position for position
                            in compound_search_positions
                            if (envelope_cover.covers(lines[position])
                                or device_detail_cover.intersects(
                                    lines[position]))
                        }
                        connected_positions = set(seed_positions)
                        pending_positions = list(seed_positions)
                        while pending_positions:
                            current = pending_positions.pop()
                            current_line = lines[current]
                            for neighbour in compound_search_positions:
                                if (neighbour in connected_positions
                                        or float(current_line.distance(
                                            lines[neighbour]))
                                        > .035 + 1e-9):
                                    continue
                                connected_positions.add(neighbour)
                                pending_positions.append(neighbour)
                        connected_short_count = sum(
                            lines[position].length <= .06 + 1e-9
                            for position in connected_positions)
                        if (len(connected_positions) < 50
                                or connected_short_count < 40):
                            continue
                        connected_union = unary_union([
                            lines[position]
                            for position in connected_positions])
                        connected_bounds = tuple(
                            float(value) for value in connected_union.bounds)
                        if min(
                            connected_bounds[0] - primary_bounds[0],
                            connected_bounds[1] - primary_bounds[1],
                            primary_bounds[2] - connected_bounds[2],
                            primary_bounds[3] - connected_bounds[3],
                        ) < .15 - 1e-9:
                            continue
                        compound_component_positions.update(
                            connected_positions)
                        compound_device_face_evidence.append({
                            "bbox_m": [round(value, 8)
                                       for value in device_bounds],
                            "area_m2": round(float(device_face.area), 8),
                            "rectangularity": round(
                                device_rectangularity, 8),
                            "micro_detail_entity_count": len(
                                device_micro_positions),
                            "connected_source_entity_count": len(
                                connected_positions),
                            "connected_short_detail_entity_count":
                                connected_short_count,
                            "connected_bbox_m": [round(value, 8)
                                                 for value in connected_bounds],
                        })
                    removable = {
                        int(fact["position"]) for fact in counter_facts
                        if envelope_cover.covers(fact["line"])
                    }
                    removable.update(
                        position for position
                        in compound_component_positions
                        if position in retained_position_set)
                    if (len(removable) < 8
                            or removable.issubset(
                                projected_staggered_counter_detail_positions)):
                        continue
                    projected_staggered_counter_detail_positions.update(
                        removable)
                    projected_staggered_counter_evidence.append({
                        "bbox_m": [round(value, 8)
                                   for value in envelope_bounds],
                        "axis": rail["axis"],
                        "continuous_rail_entity_index": int(
                            source_rows[int(rail["position"])].get(
                                "entity_index", rail["position"])),
                        "opposite_rail_entity_indexes": sorted(
                            int(source_rows[int(fact["position"])].get(
                                "entity_index", fact["position"]))
                            for fact in group),
                        "opposite_rail_segment_count": len(group),
                        "opposite_rail_fixed_spread_m": round(
                            max(float(fact["fixed"]) for fact in group)
                            - min(float(fact["fixed"]) for fact in group), 8),
                        "opposite_rail_coverage_ratio": round(
                            coverage_ratio, 8),
                        "maximum_opposite_rail_gap_m": round(maximum_gap, 8),
                        "axial_span_m": round(rail_span, 8),
                        "lateral_span_m": round(lateral_span, 8),
                        "terminal_cap_coverage_ratios": [
                            round(value, 8) for value in cap_coverages],
                        "compact_rectangular_face_count": len(
                            compact_rectangular_faces),
                        "aligned_compact_face_pair_count": len(
                            aligned_face_pairs),
                        "compact_rectangular_face_bboxes_m": [
                            [round(float(value), 8)
                             for value in face.bounds]
                            for face in compact_rectangular_faces[:20]],
                        "trim_return_count": len(trim_positions),
                        "compound_fixture_extension_entity_count": len(
                            compound_component_positions),
                        "compound_fixture_device_face_evidence":
                            compound_device_face_evidence,
                        "removed_entity_indexes": sorted(
                            int(source_rows[position].get(
                                "entity_index", position))
                            for position in removable),
                        "thresholds": {
                            "minimum_continuous_rail_length_m": 1.50,
                            "maximum_continuous_rail_length_m": 4.0,
                            "minimum_opposite_rail_segment_count": 3,
                            "maximum_opposite_rail_fixed_spread_m": .03,
                            "minimum_opposite_rail_coverage_ratio": .90,
                            "maximum_opposite_rail_gap_m": .04,
                            "minimum_rail_separation_m": .35,
                            "maximum_rail_separation_m": .80,
                            "minimum_terminal_cap_coverage_ratio": .80,
                            "minimum_compact_rectangular_face_count": 2,
                            "minimum_compact_face_lateral_clearance_m": .05,
                            "minimum_aligned_face_lateral_overlap_ratio": .90,
                            "maximum_aligned_face_axial_gap_m": .08,
                            "minimum_trim_return_count": 8,
                            "maximum_trim_return_length_m": .06,
                            "minimum_primary_bounds_clearance_m": .15,
                            "minimum_compound_device_face_side_m": .40,
                            "maximum_compound_device_face_side_m": 1.20,
                            "minimum_compound_device_face_rectangularity": .95,
                            "minimum_compound_device_micro_detail_count": 40,
                            "maximum_compound_source_segment_length_m": 2.0,
                            "maximum_compound_connection_distance_m": .035,
                            "minimum_compound_connected_source_count": 50,
                        },
                        "decision_basis": [
                            "continuous_long_rail_with_staggered_opposite_segments",
                            "near_complete_opposite_rail_coverage_and_terminal_caps",
                            "two_aligned_compact_rectangular_appliance_faces",
                            "dense_source_trim_or_return_strokes",
                            "dense_compact_device_frame_extends_proved_counter_component",
                            "motif_strictly_inside_building_spanning_ink_bounds",
                            "no_layer_block_colour_handle_or_name_semantics",
                        ],
                    })
            if projected_staggered_counter_detail_positions:
                retained_positions = [
                    position for position in retained_positions
                    if position not in
                    projected_staggered_counter_detail_positions
                ]
        retained_rows = [source_rows[position] for position in retained_positions]
        retained_metrics = _orthographic_plan_root_metrics(retained_rows)
        retained_ratio = len(retained_rows) / max(len(source_rows), 1)
        if (len(retained_rows) < 100
                or not .10 <= retained_ratio <= .90
                or int(retained_metrics.get("long_run_balance_count") or 0) < 20
                or float(retained_metrics.get(
                    "orthogonal_length_balance_ratio") or 0.0) < .25 - 1e-9
                or float(retained_metrics.get(
                    "orthogonal_aligned_length_ratio") or 0.0) < .60 - 1e-9):
            result.update(
                status="unresolved",
                reason="primary_ink_component_lacks_independent_plan_signature",
            )
            return result
        retained_position_set = set(retained_positions)
        retained_indexes = [
            int(source_rows[position].get("entity_index", position))
            for position in retained_positions
        ]
        excluded_indexes = [
            int(row.get("entity_index", position))
            for position, row in enumerate(source_rows)
            if position not in retained_position_set
        ]
        short_nonorthogonal_indexes = [
            int(source_rows[position].get("entity_index", position))
            for position in short_nonorthogonal_positions
        ]
        projected_bed_detail_indexes = [
            int(source_rows[position].get("entity_index", position))
            for position in sorted(projected_bed_detail_positions)
        ]
        radial_fixture_detail_indexes = [
            int(source_rows[position].get("entity_index", position))
            for position in sorted(radial_fixture_detail_positions)
        ]
        radial_fixture_partition_indexes = [
            int(source_rows[position].get("entity_index", position))
            for position in sorted(radial_fixture_partition_positions)
        ]
        projected_compact_bay_detail_indexes = [
            int(source_rows[position].get("entity_index", position))
            for position in sorted(projected_compact_bay_detail_positions)
        ]
        projected_staggered_counter_detail_indexes = [
            int(source_rows[position].get("entity_index", position))
            for position in sorted(
                projected_staggered_counter_detail_positions)
        ]
        result.update({
            "status": "proved",
            "source_root_handle": (next(iter(roots)) if root_proof else ""),
            "source_root_count": len(roots),
            "authority_proof_method": str(proof.get("method") or ""),
            "input_entity_count": len(source_rows),
            "retained_entity_count": len(retained_indexes),
            "excluded_entity_count": len(excluded_indexes),
            "retained_entity_ratio": round(retained_ratio, 8),
            "retained_entity_indexes": retained_indexes,
            "excluded_entity_indexes": excluded_indexes,
            "short_nonorthogonal_detail_entity_indexes":
                short_nonorthogonal_indexes,
            "short_nonorthogonal_detail_entity_count": len(
                short_nonorthogonal_indexes),
            "projected_bed_detail_entity_indexes":
                projected_bed_detail_indexes,
            "projected_bed_detail_entity_count": len(
                projected_bed_detail_indexes),
            "projected_bed_detail_evidence": projected_bed_evidence,
            "projected_bed_candidate_diagnostics":
                projected_bed_candidate_diagnostics[:20],
            "projected_bed_candidate_diagnostics_truncated": len(
                projected_bed_candidate_diagnostics) > 20,
            "radial_fixture_detail_entity_indexes":
                radial_fixture_detail_indexes,
            "radial_fixture_detail_entity_count": len(
                radial_fixture_detail_indexes),
            "radial_fixture_detail_evidence": radial_fixture_evidence,
            "radial_fixture_partition_entity_indexes":
                radial_fixture_partition_indexes,
            "radial_fixture_partition_entity_count": len(
                radial_fixture_partition_indexes),
            "projected_compact_bay_detail_entity_indexes":
                projected_compact_bay_detail_indexes,
            "projected_compact_bay_detail_entity_count": len(
                projected_compact_bay_detail_indexes),
            "projected_compact_bay_detail_evidence":
                projected_compact_bay_evidence,
            "projected_staggered_counter_detail_entity_indexes":
                projected_staggered_counter_detail_indexes,
            "projected_staggered_counter_detail_entity_count": len(
                projected_staggered_counter_detail_indexes),
            "projected_staggered_counter_detail_evidence":
                projected_staggered_counter_evidence,
            "connection_tolerance_m": tolerance,
            "component_count": len(components),
            "primary_component_bbox_m": [
                round(float(value), 8) for value in primary.bounds],
            "primary_width_coverage_ratio": round(width_coverage, 8),
            "primary_depth_coverage_ratio": round(depth_coverage, 8),
            "primary_to_runner_area_ratio": round(dominance_ratio, 8),
            "retained_orthographic_metrics": retained_metrics,
            "component_evidence": [{
                "area_m2": round(float(component.area), 8),
                "bbox_m": [round(float(value), 8)
                           for value in component.bounds],
            } for component in components[:20]],
            "component_evidence_truncated": len(components) > 20,
            "thresholds": {
                "minimum_input_entity_count": 1000,
                "connection_tolerance_m": tolerance,
                "minimum_primary_axis_coverage_ratio": .95,
                "minimum_primary_to_runner_area_ratio": 4.0,
                "minimum_retained_entity_count": 100,
                "minimum_retained_entity_ratio": .10,
                "maximum_retained_entity_ratio": .90,
                "minimum_retained_long_run_count_per_axis": 20,
                "minimum_retained_orthogonal_length_balance_ratio": .25,
                "minimum_retained_aligned_length_ratio": .60,
                "maximum_short_nonorthogonal_detail_length_m": .15,
                "minimum_short_nonorthogonal_axis_difference_deg": 5.0,
                "minimum_sleeping_furniture_frame_side_m": 1.4,
                "maximum_sleeping_furniture_frame_side_m": 2.4,
                "minimum_internal_curved_detail_chord_count": 40,
                "minimum_closed_textile_face_area_m2": .05,
                "maximum_closed_textile_face_area_m2": .80,
                "minimum_radial_fixture_hub_radius_m": .04,
                "maximum_radial_fixture_hub_radius_m": .20,
                "minimum_radial_fixture_spoke_count": 3,
                "minimum_projected_compact_bay_count": 2,
                "minimum_projected_compact_bay_trim_return_count": 4,
                "minimum_projected_staggered_counter_trim_return_count": 8,
            },
            "decision_basis": [
                "independently_proved_text_free_orthographic_plan_root",
                ("single_generic_line_source_root" if root_proof
                 else "direct_primitive_multi_root_spatial_view"),
                "unique_building_spanning_connected_ink_component",
                "retained_component_preserves_two_axis_architectural_signature",
                "short_tessellated_nonorthogonal_detail_removed",
                "source_proved_sleeping_furniture_detail_removed",
                "source_proved_radial_service_fixture_spokes_removed",
                "source_proved_repeated_compact_bay_chain_removed",
                "source_proved_staggered_counter_appliance_strip_removed",
                "disconnected_projected_detail_not_promoted_to_wall",
                "no_layer_block_filename_or_view_name_semantics",
            ],
        })
        return result
    except Exception as ex:
        result.update(status="unresolved", reason="component_analysis_failed",
                      diagnostic=f"{type(ex).__name__}: {ex}"[:240])
        return result


def _geometry_only_structural_evidence(
    geometry: list[dict], texts: list[dict], inserts: list[dict],
) -> dict:
    """Find a plan before wall-layer semantics are available.

    This is deliberately a proof gate, not a general "all LINEs are walls"
    fallback.  A candidate needs a coherent closed line arrangement plus
    independent residential plan annotations.  Root INSERT identity and
    spatial connectivity are used only to form views; block/layer names never
    by themselves promote a source row into authoritative wall geometry.
    """
    if not geometry:
        return {"status": "unresolved", "candidates": [], "selected_indexes": []}
    all_points = [point for row in geometry for point in row.get("points") or []]
    bounds = _bbox(all_points)
    diagonal = max(math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]), 1e-9)
    # Keep the view gap tight: a larger fraction joins dimension chains and
    # title borders to the house and turns a plot sheet into one false plan.
    tolerance = max(diagonal * .001, 1e-9)

    candidate_sets: set[tuple[int, ...]] = set()
    for group in _cluster_geometry(geometry, tolerance_m=tolerance):
        if len(group) >= 4:
            candidate_sets.add(tuple(sorted(group)))
    roots: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(geometry):
        provenance = row.get("cad_provenance") or {}
        root_handle = str(provenance.get("root_handle") or "")
        if root_handle:
            roots[root_handle].append(index)
    for indexes in roots.values():
        if len(indexes) >= 4:
            candidate_sets.add(tuple(sorted(indexes)))
    semantic_text_rows = [
        row for row in texts
        if isinstance(row.get("point_m"), (list, tuple))
        and len(row["point_m"]) >= 2
        and any(_room_profile_from_text(str(row.get("text") or "")))
    ]
    if len(semantic_text_rows) >= 2:
        anchor_points = [
            (float(row["point_m"][0]), float(row["point_m"][1]))
            for row in semantic_text_rows
        ]
        anchor_bounds = _bbox(anchor_points)
        anchor_span = max(anchor_bounds[2] - anchor_bounds[0],
                          anchor_bounds[3] - anchor_bounds[1], diagonal * .01)
        padding = anchor_span * .30
        envelope = (
            anchor_bounds[0] - padding, anchor_bounds[1] - padding,
            anchor_bounds[2] + padding, anchor_bounds[3] + padding,
        )
        envelope_width = envelope[2] - envelope[0]
        envelope_depth = envelope[3] - envelope[1]
        anchor_indexes = []
        for index, row in enumerate(geometry):
            row_bounds = tuple(row["bbox"])
            # The entire primitive must be inside the room-label envelope.
            # A dimension/plot rail can have its midpoint inside this envelope
            # while extending far beyond both ends of the actual floor plan.
            if not (envelope[0] <= row_bounds[0]
                    and envelope[1] <= row_bounds[1]
                    and row_bounds[2] <= envelope[2]
                    and row_bounds[3] <= envelope[3]):
                continue
            # A title border can cross the anchor envelope while spanning the
            # whole sheet.  Such a primitive is view context, not plan ink.
            if ((row_bounds[2] - row_bounds[0]) > envelope_width * 1.25
                    or (row_bounds[3] - row_bounds[1]) > envelope_depth * 1.25):
                continue
            anchor_indexes.append(index)
        if len(anchor_indexes) >= 4:
            candidate_sets.add(tuple(sorted(anchor_indexes)))
    # A direct modelspace plan often has one root per primitive.  Preserve the
    # complete arrangement as a candidate in addition to its connected views.
    candidate_sets.add(tuple(range(len(geometry))))

    context_roots: set[str] = set()
    for insert in inserts:
        if not (insert.get("semantic_role") or insert.get("opening_kind")):
            continue
        provenance = insert.get("cad_provenance") or {}
        root_handle = str(provenance.get("root_handle") or
                          provenance.get("source_handle") or "")
        if root_handle:
            context_roots.add(root_handle)

    candidates: list[dict] = []
    for number, indexes in enumerate(sorted(candidate_sets), 1):
        rows = [geometry[index] for index in indexes]
        points = [point for row in rows for point in row.get("points") or []]
        if not points:
            continue
        candidate_bounds = _bbox(points)
        width = candidate_bounds[2] - candidate_bounds[0]
        depth = candidate_bounds[3] - candidate_bounds[1]
        if width <= 0 or depth <= 0:
            continue
        local_texts = [
            row for row in texts
            if isinstance(row.get("point_m"), (list, tuple))
            and len(row["point_m"]) >= 2
            and candidate_bounds[0] <= float(row["point_m"][0]) <= candidate_bounds[2]
            and candidate_bounds[1] <= float(row["point_m"][1]) <= candidate_bounds[3]
        ]
        room_anchors = [
            row for row in local_texts
            if any(_room_profile_from_text(str(row.get("text") or "")))
        ]
        anchor_density = 0.0
        if room_anchors:
            local_anchor_bounds = _bbox([
                (float(row["point_m"][0]), float(row["point_m"][1]))
                for row in room_anchors
            ])
            anchor_area = max(
                (local_anchor_bounds[2] - local_anchor_bounds[0])
                * (local_anchor_bounds[3] - local_anchor_bounds[1]), 1e-12)
            candidate_area = max(width * depth, 1e-12)
            anchor_density = min(1.0, anchor_area / candidate_area)
        text_blob = " ".join(str(row.get("text") or "") for row in local_texts)
        positive_plan_text = bool(re.search(
            r"(?:architectural\s+plan|floor\s*plan|house\s*plan|\bplan\b|平面图)",
            text_blob, re.I))
        negative_view_text = bool(re.search(
            r"(?:roof\s*plan|elevation|section|front\s+view|side\s+view|立面|剖面|屋顶)",
            text_blob, re.I))
        face_count = _candidate_polygon_count(rows)
        root_handles = sorted({
            str((row.get("cad_provenance") or {}).get("root_handle") or "")
            for row in rows
            if str((row.get("cad_provenance") or {}).get("root_handle") or "")
        })
        context_count = sum(handle in context_roots for handle in root_handles)
        complete_source_root = bool(
            len(root_handles) == 1
            and set(indexes) == set(roots.get(root_handles[0]) or []))
        orthographic_metrics = _orthographic_plan_root_metrics(rows)
        proof = (
            (len(room_anchors) >= 2 and face_count >= 2)
            or (positive_plan_text and len(room_anchors) >= 1 and face_count >= 1)
        ) and not (negative_view_text and not positive_plan_text)
        length = sum(math.dist(first, second) for row in rows
                     for first, second in zip(row.get("points") or [],
                                              (row.get("points") or [])[1:]))
        score = (
            len(room_anchors) * 1000 + min(face_count, 20) * 100
            + anchor_density * 5000
            + (500 if positive_plan_text else 0)
            - (800 if negative_view_text and not positive_plan_text else 0)
            + length / diagonal - context_count * 2
        )
        promoted = []
        for index in indexes:
            provenance = geometry[index].get("cad_provenance") or {}
            root_handle = str(provenance.get("root_handle") or "")
            if root_handle and root_handle in context_roots:
                continue
            promoted.append(index)
        candidates.append({
            "candidate_id": f"cad_geometry_view_{number}",
            "entity_indexes": list(indexes),
            "promoted_entity_indexes": promoted,
            "bbox_m": [round(value, 8) for value in candidate_bounds],
            "root_handles": root_handles[:100],
            "root_handle_count": len(root_handles),
            "room_anchor_count": len(room_anchors),
            "room_anchor_bbox_density": round(anchor_density, 8),
            "closed_region_count": face_count,
            "positive_plan_text": positive_plan_text,
            "negative_view_text": negative_view_text,
            "context_root_count": context_count,
            "complete_source_root": complete_source_root,
            "orthographic_plan_metrics": orthographic_metrics,
            "proof_status": "proved" if proof and promoted else "unresolved",
            "selection_score": round(score, 8),
            "decision_basis": [
                "closed_line_arrangement",
                "independent_room_or_plan_text",
                "root_insert_and_spatial_view_decomposition",
                "known_fixture_and_opening_roots_excluded",
                "no_layer_block_or_filename_only_promotion",
            ],
        })
    # Text-free BIM/FreeCAD exports often contain one top-level INSERT for the
    # plan and several independent elevation views, all flattened to generic
    # LINE entities.  Select a root without reading its name only when one
    # complete source root has a decisive two-axis long-run signature: both
    # axes contain at least 30 >=500 mm runs, their length is balanced, and the
    # weaker axis still has at least twice as many runs as the runner-up view.
    complete_root_candidates = [
        row for row in candidates
        if row.get("complete_source_root") is True
        and int(row.get("root_handle_count") or 0) == 1]
    complete_root_candidates.sort(key=lambda row: (
        -int((row.get("orthographic_plan_metrics") or {}).get(
            "long_run_balance_count") or 0),
        -float((row.get("orthographic_plan_metrics") or {}).get(
            "orthogonal_length_balance_ratio") or 0.0),
        str(row.get("candidate_id") or ""),
    ))
    if len(complete_root_candidates) >= 3:
        winner = complete_root_candidates[0]
        runner = complete_root_candidates[1]
        winner_metrics = winner["orthographic_plan_metrics"]
        runner_balance = int((runner.get("orthographic_plan_metrics") or {}).get(
            "long_run_balance_count") or 0)
        winner_balance = int(winner_metrics.get("long_run_balance_count") or 0)
        selection_ratio = winner_balance / max(runner_balance, 1)
        if (winner_balance >= 30
                and float(winner_metrics.get(
                    "orthogonal_length_balance_ratio") or 0.0) >= .25 - 1e-9
                and float(winner_metrics.get(
                    "orthogonal_aligned_length_ratio") or 0.0) >= .60 - 1e-9
                and int(winner.get("closed_region_count") or 0) >= 5
                and selection_ratio >= 2.0 - 1e-9
                and winner.get("promoted_entity_indexes")):
            winner["proof_status"] = "proved"
            winner["orthographic_plan_root_evidence"] = {
                "method": "cad_multi_view_orthographic_plan_root_v1",
                "source_root_count": len(complete_root_candidates),
                "selected_root_handle": winner["root_handles"][0],
                "selected_metrics": copy.deepcopy(winner_metrics),
                "runner_up_candidate_id": runner["candidate_id"],
                "runner_up_long_run_balance_count": runner_balance,
                "long_run_balance_selection_ratio": round(selection_ratio, 8),
                "thresholds": {
                    "minimum_independent_source_root_count": 3,
                    "minimum_long_run_count_per_axis": 30,
                    "minimum_orthogonal_length_balance_ratio": .25,
                    "minimum_orthogonal_aligned_length_ratio": .60,
                    "minimum_closed_region_count": 5,
                    "minimum_runner_up_selection_ratio": 2.0,
                },
                "decision_basis": [
                    "independent_complete_source_root_views",
                    "many_long_runs_in_both_orthogonal_directions",
                    "balanced_two_axis_architectural_line_field",
                    "decisive_geometry_only_margin_over_other_views",
                    "no_layer_block_filename_or_view_name_semantics",
                ],
            }
            winner["decision_basis"] = copy.deepcopy(
                winner["orthographic_plan_root_evidence"]["decision_basis"])
    # A second exporter family explodes the plan into thousands of direct
    # model-space primitives (one handle/root per primitive) while elevations
    # remain INSERTs.  Root identity cannot select that plan.  When no earlier
    # proof exists, compare independent spatial view envelopes instead: the
    # floor plan must have at least 100 balanced long runs in both axes and a
    # >=2x margin over every roof/elevation view.  Identical nested candidates
    # are collapsed by bbox before the margin is computed.
    if not any(row.get("proof_status") == "proved" for row in candidates):
        eligible_views = [
            row for row in candidates
            if not row.get("negative_view_text")
            and len(row.get("entity_indexes") or [])
            < len(geometry) * .80
            and len(row.get("promoted_entity_indexes") or []) >= 200
            and int(row.get("closed_region_count") or 0) >= 20
            and (float(row["bbox_m"][2]) - float(row["bbox_m"][0])) >= 3.0
            and (float(row["bbox_m"][3]) - float(row["bbox_m"][1])) >= 3.0
        ]
        envelope_groups: dict[tuple[float, ...], list[dict]] = {}
        for row in eligible_views:
            envelope_key = tuple(round(float(value), 3)
                                 for value in row.get("bbox_m") or [])
            envelope_groups.setdefault(envelope_key, []).append(row)
        independent_views = [
            max(rows, key=lambda row: (
                int((row.get("orthographic_plan_metrics") or {}).get(
                    "long_run_balance_count") or 0),
                len(row.get("promoted_entity_indexes") or []),
                str(row.get("candidate_id") or ""),
            ))
            for rows in envelope_groups.values()
        ]
        independent_views.sort(key=lambda row: (
            -int((row.get("orthographic_plan_metrics") or {}).get(
                "long_run_balance_count") or 0),
            -float((row.get("orthographic_plan_metrics") or {}).get(
                "orthogonal_aligned_length_ratio") or 0.0),
            str(row.get("candidate_id") or ""),
        ))
        if len(independent_views) >= 5:
            winner, runner = independent_views[:2]
            metrics = winner.get("orthographic_plan_metrics") or {}
            winner_balance = int(metrics.get("long_run_balance_count") or 0)
            runner_balance = int((runner.get(
                "orthographic_plan_metrics") or {}).get(
                    "long_run_balance_count") or 0)
            selection_ratio = winner_balance / max(runner_balance, 1)
            if (winner_balance >= 100
                    and float(metrics.get(
                        "orthogonal_length_balance_ratio") or 0.0) >= .35
                    and float(metrics.get(
                        "orthogonal_aligned_length_ratio") or 0.0) >= .85
                    and selection_ratio >= 2.0
                    and winner.get("promoted_entity_indexes")):
                winner["proof_status"] = "proved"
                winner["orthographic_plan_view_evidence"] = {
                    "method": "cad_multi_view_orthographic_plan_view_v1",
                    "independent_view_envelope_count": len(independent_views),
                    "selected_candidate_id": winner["candidate_id"],
                    "selected_entity_indexes": copy.deepcopy(
                        winner["promoted_entity_indexes"]),
                    "selected_metrics": copy.deepcopy(metrics),
                    "runner_up_candidate_id": runner["candidate_id"],
                    "runner_up_long_run_balance_count": runner_balance,
                    "long_run_balance_selection_ratio": round(
                        selection_ratio, 8),
                    "thresholds": {
                        "minimum_independent_view_envelope_count": 5,
                        "minimum_long_run_count_per_axis": 100,
                        "minimum_orthogonal_length_balance_ratio": .35,
                        "minimum_orthogonal_aligned_length_ratio": .85,
                        "minimum_closed_region_count": 20,
                        "minimum_runner_up_selection_ratio": 2.0,
                    },
                    "decision_basis": [
                        "independent_spatial_view_envelopes",
                        "direct_primitive_plan_does_not_require_shared_root",
                        "many_long_runs_in_both_orthogonal_directions",
                        "decisive_geometry_only_margin_over_roof_and_elevations",
                        "no_layer_block_filename_or_view_name_semantics",
                    ],
                }
                winner["decision_basis"] = copy.deepcopy(
                    winner["orthographic_plan_view_evidence"][
                        "decision_basis"])
    # The complete sheet and an anchor-envelope candidate can both contain the
    # same room labels.  They are not two competing plans when the tighter
    # candidate is spatially contained and keeps all anchors; the broad one is
    # title/dimension context and must not force a fake ambiguity.
    proved_before_sort = [row for row in candidates if row["proof_status"] == "proved"]
    for broad in proved_before_sort:
        broad_bounds = broad["bbox_m"]
        broad_area = max(
            (broad_bounds[2] - broad_bounds[0])
            * (broad_bounds[3] - broad_bounds[1]), 1e-12)
        tighter_matches = []
        for tight in proved_before_sort:
            if tight is broad or tight["room_anchor_count"] != broad["room_anchor_count"]:
                continue
            tight_bounds = tight["bbox_m"]
            tight_area = max(
                (tight_bounds[2] - tight_bounds[0])
                * (tight_bounds[3] - tight_bounds[1]), 1e-12)
            contained = (
                broad_bounds[0] <= tight_bounds[0]
                and broad_bounds[1] <= tight_bounds[1]
                and tight_bounds[2] <= broad_bounds[2]
                and tight_bounds[3] <= broad_bounds[3]
            )
            if (contained and tight_area <= broad_area * .85
                    and float(tight.get("room_anchor_bbox_density") or 0)
                    > float(broad.get("room_anchor_bbox_density") or 0)):
                tighter_matches.append(tight)
        if tighter_matches:
            tight = max(tighter_matches, key=lambda row: (
                float(row.get("room_anchor_bbox_density") or 0),
                float(row.get("selection_score") or 0)))
            broad["proof_status"] = "superseded_context"
            broad["superseded_by"] = tight["candidate_id"]
    # Connected-view, root-view and anchor-envelope construction can produce
    # two almost identical candidates.  They are not competing floor plans
    # when they share the same envelope, anchors and closed regions and at
    # least 98% of their source entities.  Prefer the minimal source set so a
    # pair of dimension/context strokes cannot create a false hard ambiguity.
    # Materially different overlays still remain ambiguous.
    duplicate_candidates = [
        row for row in candidates if row["proof_status"] == "proved"]
    visited_duplicates: set[str] = set()
    for candidate in duplicate_candidates:
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in visited_duplicates:
            continue
        candidate_bounds = candidate["bbox_m"]
        span = max(candidate_bounds[2] - candidate_bounds[0],
                   candidate_bounds[3] - candidate_bounds[1], 1e-9)
        tolerance = span * 1e-6 + 1e-9
        candidate_indexes = set(candidate["promoted_entity_indexes"])
        equivalents = [candidate]
        for other in duplicate_candidates:
            other_id = str(other["candidate_id"])
            if other is candidate or other_id in visited_duplicates:
                continue
            if (other["room_anchor_count"] != candidate["room_anchor_count"]
                    or other["closed_region_count"] != candidate["closed_region_count"]):
                continue
            if any(abs(float(left) - float(right)) > tolerance
                   for left, right in zip(candidate_bounds, other["bbox_m"])):
                continue
            other_indexes = set(other["promoted_entity_indexes"])
            union = candidate_indexes | other_indexes
            overlap = len(candidate_indexes & other_indexes) / max(len(union), 1)
            if overlap >= .98:
                equivalents.append(other)
        if len(equivalents) == 1:
            continue
        winner = min(equivalents, key=lambda row: (
            len(row["promoted_entity_indexes"]),
            -float(row.get("room_anchor_bbox_density") or 0),
            -float(row.get("selection_score") or 0),
            str(row["candidate_id"]),
        ))
        for duplicate in equivalents:
            visited_duplicates.add(str(duplicate["candidate_id"]))
            if duplicate is winner:
                continue
            duplicate["proof_status"] = "superseded_near_duplicate"
            duplicate["superseded_by"] = winner["candidate_id"]
            duplicate["near_duplicate_evidence"] = {
                "method": "same_envelope_anchor_face_and_98pct_source_overlap_v1",
                "source_entity_overlap_ratio": round(
                    len(set(duplicate["promoted_entity_indexes"])
                        & set(winner["promoted_entity_indexes"]))
                    / max(len(set(duplicate["promoted_entity_indexes"])
                              | set(winner["promoted_entity_indexes"])), 1),
                    8),
            }
    candidates.sort(key=lambda row: (
        row["proof_status"] != "proved", -float(row["selection_score"]),
        str(row["candidate_id"])))
    proved = [row for row in candidates if row["proof_status"] == "proved"]
    if not proved:
        return {"status": "unresolved", "candidates": candidates[:20],
                "selected_indexes": []}
    selected = proved[0]
    ambiguous = len(proved) > 1 and float(proved[1]["selection_score"]) >= float(
        selected["selection_score"]) * .98
    return {
        "schema_version": 1,
        "method": "cad_geometry_plan_authority_v1",
        "status": "ambiguous" if ambiguous else "proved",
        "selected_candidate_id": selected["candidate_id"],
        "selected_indexes": copy.deepcopy(selected["promoted_entity_indexes"]),
        "candidates": copy.deepcopy(candidates[:20]),
        "decision_basis": copy.deepcopy(selected["decision_basis"]),
    }


def _exclude_detached_site_boundary_components(
    assemblies: list[dict], footprints: list[dict], space_polygons: Sequence[Any],
    topology_summary: Mapping[str, Any], *, origin_x: float, origin_z: float,
) -> tuple[list[dict], list[dict], dict]:
    """Remove an oversized detached site perimeter from production walls.

    A multi-view residential sheet can draw the house plan inside a much
    larger open plot/carport boundary.  That boundary is legitimate CAD ink,
    but it is not a 2.8 m residential wall.  The rule is deliberately narrow:
    global topology must already prove at least one physical space and at
    least two wall components; the candidate component must be detached from
    every physical space, enclose most of the occupied span on both axes, and
    be substantially larger than the occupied building.  Removed source
    assemblies remain as rejected, provenance-only audit evidence.
    """
    result_assemblies = copy.deepcopy(assemblies)
    result_footprints = copy.deepcopy(footprints)
    result_summary = copy.deepcopy(dict(topology_summary))
    if (str(result_summary.get("status") or "") != "proved"
            or len(result_footprints) < 2 or not space_polygons):
        return result_assemblies, result_footprints, result_summary
    try:
        from shapely.geometry import LineString, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore

        model_spaces = []
        for raw in space_polygons:
            exterior = [(float(x) - origin_x, float(z) - origin_z)
                        for x, z in list(raw.exterior.coords)[:-1]]
            holes = [[(float(x) - origin_x, float(z) - origin_z)
                      for x, z in list(ring.coords)[:-1]]
                     for ring in raw.interiors]
            polygon = Polygon(exterior, holes)
            if polygon.is_valid and not polygon.is_empty:
                model_spaces.append(polygon)
        if not model_spaces:
            return result_assemblies, result_footprints, result_summary
        occupied = unary_union(model_spaces).buffer(0)
        occupied_bounds = tuple(float(value) for value in occupied.bounds)
        occupied_width = occupied_bounds[2] - occupied_bounds[0]
        occupied_height = occupied_bounds[3] - occupied_bounds[1]
        if occupied_width <= 1e-9 or occupied_height <= 1e-9:
            return result_assemblies, result_footprints, result_summary

        parsed_components: list[tuple[int, Any]] = []
        for index, footprint in enumerate(result_footprints):
            exterior = [(float(point["x"]), float(point["z"]))
                        for point in footprint.get("points") or []]
            holes = [[(float(point["x"]), float(point["z"])) for point in ring]
                     for ring in footprint.get("interior_rings") or []]
            polygon = Polygon(exterior, holes)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            parts = (list(polygon.geoms)
                     if getattr(polygon, "geom_type", "") == "MultiPolygon"
                     else [polygon])
            for part in parts:
                if not part.is_empty:
                    parsed_components.append((index, part))
        if len(parsed_components) < 2:
            return result_assemblies, result_footprints, result_summary

        # A site perimeter can touch one exterior wall and therefore be
        # unioned into the same global polygon as the house.  Component-level
        # distance cannot separate that case.  Only activate this second path
        # when the union is dramatically oversized, then require at least
        # three long, accepted source assemblies to lie wholly outside a
        # 350 mm physical-space neighbourhood.  This produces source-backed
        # cut geometry instead of clipping an arbitrary mask by appearance.
        occupied_boundary_length = float(occupied.boundary.length)
        near_occupied = occupied.buffer(.35, join_style=2)
        oversized_components: list[tuple[int, Any, dict]] = []
        for footprint_index, component in parsed_components:
            bounds = tuple(float(value) for value in component.bounds)
            component_width = bounds[2] - bounds[0]
            component_height = bounds[3] - bounds[1]
            overlap_x = max(0.0, min(bounds[2], occupied_bounds[2])
                            - max(bounds[0], occupied_bounds[0]))
            overlap_z = max(0.0, min(bounds[3], occupied_bounds[3])
                            - max(bounds[1], occupied_bounds[1]))
            overlap_x_ratio = overlap_x / occupied_width
            overlap_z_ratio = overlap_z / occupied_height
            span_ratio = max(component_width / occupied_width,
                             component_height / occupied_height)
            boundary_length = float(component.boundary.length)
            if (overlap_x_ratio >= .75 - 1e-9
                    and overlap_z_ratio >= .75 - 1e-9
                    and span_ratio >= 1.50 - 1e-9
                    and boundary_length
                    >= max(16.0, occupied_boundary_length * 2.0) - 1e-9):
                oversized_components.append((footprint_index, component, {
                    "bounds": bounds,
                    "overlap_x_ratio": overlap_x_ratio,
                    "overlap_z_ratio": overlap_z_ratio,
                    "span_ratio": span_ratio,
                    "boundary_length": boundary_length,
                }))
        all_outside_assemblies: list[tuple[dict, Any, Any, float]] = []
        if len(oversized_components) == 1:
            _, oversized_component, _ = oversized_components[0]
            for assembly in result_assemblies:
                if str(assembly.get("review_status") or "") \
                        not in {"accepted", "confirmed"}:
                    continue
                try:
                    line = LineString(assembly.get("centerline") or [])
                except (TypeError, ValueError):
                    continue
                if (line.length < .02 - 1e-9
                        or float(line.intersection(near_occupied).length)
                        > 1e-7
                        or not oversized_component.buffer(.03).covers(line)):
                    continue
                try:
                    source_polygon = Polygon(
                        assembly.get("footprint_polygon") or [])
                    if not source_polygon.is_valid:
                        source_polygon = source_polygon.buffer(0)
                except (TypeError, ValueError):
                    source_polygon = line.buffer(
                        max(float(assembly.get("thickness_m") or .1) / 2,
                            .03), cap_style=2, join_style=2)
                if source_polygon.is_empty:
                    continue
                all_outside_assemblies.append((
                    assembly, line, source_polygon, float(line.length)))
        outside_assemblies = [
            row for row in all_outside_assemblies
            if row[3] >= .75 - 1e-9
        ]
        outside_total_length = sum(row[3] for row in outside_assemblies)
        if (len(outside_assemblies) >= 3
                and outside_total_length
                >= max(12.0, occupied_boundary_length * .50) - 1e-9):
            footprint_index, oversized_component, metrics = \
                oversized_components[0]
            outside_lines = unary_union([row[1] for row in outside_assemblies])
            outside_bounds = tuple(float(value) for value in outside_lines.bounds)
            outside_overlap_x = max(
                0.0, min(outside_bounds[2], occupied_bounds[2])
                - max(outside_bounds[0], occupied_bounds[0])) / occupied_width
            outside_overlap_z = max(
                0.0, min(outside_bounds[3], occupied_bounds[3])
                - max(outside_bounds[1], occupied_bounds[1])) / occupied_height
            if outside_overlap_x >= .75 - 1e-9 \
                    and outside_overlap_z >= .75 - 1e-9:
                proof = {
                    "method":
                        "cad_oversized_coalesced_site_boundary_clip_v1",
                    "wall_footprint_index": footprint_index,
                    "wall_footprint_id": str(
                        result_footprints[footprint_index].get("id") or ""),
                    "component_bounds_m": [
                        round(value, 8) for value in metrics["bounds"]],
                    "occupied_space_bounds_m": [
                        round(value, 8) for value in occupied_bounds],
                    "physical_space_count": len(model_spaces),
                    "original_wall_component_count": len(parsed_components),
                    "component_to_physical_space_distance_m": 0.0,
                    "occupied_x_span_overlap_ratio": round(
                        metrics["overlap_x_ratio"], 8),
                    "occupied_z_span_overlap_ratio": round(
                        metrics["overlap_z_ratio"], 8),
                    "maximum_component_to_occupied_span_ratio": round(
                        metrics["span_ratio"], 8),
                    "component_boundary_length_m": round(
                        metrics["boundary_length"], 8),
                    "occupied_space_boundary_length_m": round(
                        occupied_boundary_length, 8),
                    "outside_source_assembly_count": len(
                        outside_assemblies),
                    "outside_source_assembly_total_length_m": round(
                        outside_total_length, 8),
                    "outside_source_bounds_m": [
                        round(value, 8) for value in outside_bounds],
                    "outside_source_x_span_overlap_ratio": round(
                        outside_overlap_x, 8),
                    "outside_source_z_span_overlap_ratio": round(
                        outside_overlap_z, 8),
                    "thresholds": {
                        "physical_space_neighbourhood_m": .35,
                        "minimum_source_assembly_length_m": .75,
                        "minimum_outside_source_assembly_count": 3,
                        "minimum_outside_source_total_length_m": 12.0,
                        "minimum_outside_to_occupied_boundary_length_ratio": .50,
                        "minimum_occupied_axis_overlap_ratio": .75,
                        "minimum_component_to_occupied_span_ratio": 1.50,
                        "minimum_component_to_occupied_boundary_length_ratio": 2.0,
                    },
                    "decision_basis": [
                        "single_union_component_substantially_exceeds_occupied_building",
                        "three_or_more_long_accepted_source_walls_fully_outside_space_neighbourhood",
                        "outside_source_walls_enclose_most_of_occupied_span_on_both_axes",
                        "only_source_backed_outside_wall_bands_removed",
                        "remaining_wall_footprint_must_touch_proved_physical_space",
                    ],
                }
                outside_mask = unary_union([
                    row[2] for row in all_outside_assemblies
                ]).buffer(.03, join_style=2)
                rebuilt_footprints: list[dict] = []
                for index, footprint in enumerate(result_footprints):
                    matching_parts = [
                        component for component_index, component
                        in parsed_components if component_index == index
                    ]
                    geometry = unary_union(matching_parts).buffer(0)
                    if index == footprint_index:
                        geometry = geometry.difference(outside_mask).buffer(0)
                    parts = (list(geometry.geoms)
                             if getattr(geometry, "geom_type", "")
                             == "MultiPolygon" else [geometry])
                    parts = [part for part in parts if not part.is_empty
                             and part.area >= 1e-5
                             and part.distance(occupied) <= .35 + 1e-9]
                    for part_number, part in enumerate(parts, 1):
                        record = copy.deepcopy(footprint)
                        if len(parts) > 1:
                            record["id"] = (
                                f"{str(footprint.get('id') or 'wall_footprint')}"
                                f"_production_{part_number}")
                        record["points"] = [
                            {"x": round(float(x), 8),
                             "z": round(float(z), 8)}
                            for x, z in list(part.exterior.coords)[:-1]
                        ]
                        record["interior_rings"] = [[
                            {"x": round(float(x), 8),
                             "z": round(float(z), 8)}
                            for x, z in list(ring.coords)[:-1]
                        ] for ring in part.interiors]
                        rebuilt_footprints.append(record)
                rejected_assembly_ids = []
                nonspace_assembly_ids = []
                for assembly, line, _polygon, length \
                        in all_outside_assemblies:
                    original_geometry = {
                        "source_representation": str(
                            assembly.get("source_representation") or ""),
                        "centerline": copy.deepcopy(
                            assembly.get("centerline") or []),
                        "footprint_polygon": copy.deepcopy(
                            assembly.get("footprint_polygon") or []),
                        "thickness_m": assembly.get("thickness_m"),
                        "length_m": assembly.get("length_m"),
                    }
                    assembly["source_centerline"] = copy.deepcopy(
                        original_geometry["centerline"])
                    is_long_site_boundary = length >= .75 - 1e-9
                    assembly["source_representation"] = (
                        "detached_site_boundary_evidence"
                        if is_long_site_boundary else
                        "nonspace_projected_geometry_evidence")
                    assembly["resolved_as"] = (
                        "nonproduction_site_boundary"
                        if is_long_site_boundary else
                        "nonproduction_nonspace_geometry")
                    assembly["review_status"] = "rejected"
                    reason = (
                        "cad_detached_site_boundary_not_physical_space_boundary"
                        if is_long_site_boundary else
                        "cad_projected_geometry_not_adjacent_to_physical_space")
                    assembly["reason"] = reason
                    assembly["reason_codes"] = sorted(set(
                        (assembly.get("reason_codes") or []) + [reason]
                    ))
                    assembly["production_blockers"] = []
                    if is_long_site_boundary:
                        assembly["detached_site_boundary_evidence"] = {
                            **copy.deepcopy(proof),
                            "source_wall_geometry": original_geometry,
                        }
                    else:
                        assembly["nonspace_projected_geometry_evidence"] = {
                            "method":
                                "cad_nonspace_geometry_within_oversized_site_plan_v1",
                            "source_to_physical_space_distance_m": round(
                                float(line.distance(occupied)), 8),
                            "physical_space_neighbourhood_m": .35,
                            "source_length_m": round(length, 8),
                            "oversized_site_component_proof": copy.deepcopy(
                                proof),
                            "source_wall_geometry": original_geometry,
                            "decision_basis": [
                                "oversized_coalesced_site_plan_independently_proved",
                                "accepted_source_geometry_fully_outside_all_physical_space_neighbourhoods",
                                "geometry_removed_from_global_wall_footprint_and_compatibility_walls",
                                "source_geometry_preserved_for_audit",
                            ],
                        }
                    for key in ("start", "end", "centerline",
                                "opening_axis", "footprint_polygon",
                                "thickness_m", "length_m"):
                        assembly.pop(key, None)
                    if is_long_site_boundary:
                        rejected_assembly_ids.append(str(
                            assembly.get("id") or ""))
                    else:
                        nonspace_assembly_ids.append(str(
                            assembly.get("id") or ""))
                kept_polygons = []
                for footprint in rebuilt_footprints:
                    polygon = Polygon(
                        [(float(point["x"]), float(point["z"]))
                         for point in footprint.get("points") or []],
                        [[(float(point["x"]), float(point["z"]))
                          for point in ring]
                         for ring in footprint.get("interior_rings") or []],
                    ).buffer(0)
                    if not polygon.is_empty:
                        kept_polygons.append(polygon)
                result_footprints = rebuilt_footprints
                result_summary.update({
                    "wall_footprint_count": len(result_footprints),
                    "wall_component_count": len(kept_polygons),
                    "wall_area_m2": round(float(
                        unary_union(kept_polygons).area), 6),
                    "detached_site_boundary_component_count": 1,
                    "detached_site_boundary_assembly_count": len(
                        rejected_assembly_ids),
                    "detached_site_boundary_assembly_ids":
                        rejected_assembly_ids[:100],
                    "nonspace_projected_geometry_assembly_count": len(
                        nonspace_assembly_ids),
                    "nonspace_projected_geometry_assembly_ids":
                        nonspace_assembly_ids[:100],
                    "detached_site_boundary_evidence": [proof],
                    "detached_site_boundary_evidence_truncated": False,
                })
                decision_basis = list(
                    result_summary.get("decision_basis") or [])
                decision_basis.append(
                    "oversized_coalesced_site_boundary_source_bands_excluded_from_production_walls")
                result_summary["decision_basis"] = sorted(set(
                    decision_basis))
                return (result_assemblies, result_footprints,
                        result_summary)

        removed_indexes: set[int] = set()
        removal_proofs: list[dict] = []
        for footprint_index, component in parsed_components:
            bounds = tuple(float(value) for value in component.bounds)
            component_width = bounds[2] - bounds[0]
            component_height = bounds[3] - bounds[1]
            overlap_x = max(0.0, min(bounds[2], occupied_bounds[2])
                            - max(bounds[0], occupied_bounds[0]))
            overlap_z = max(0.0, min(bounds[3], occupied_bounds[3])
                            - max(bounds[1], occupied_bounds[1]))
            overlap_x_ratio = overlap_x / occupied_width
            overlap_z_ratio = overlap_z / occupied_height
            span_ratio = max(component_width / occupied_width,
                             component_height / occupied_height)
            distance = float(component.distance(occupied))
            boundary_length = float(component.boundary.length)
            occupied_boundary_length = float(occupied.boundary.length)
            if (distance < .35 - 1e-9
                    or overlap_x_ratio < .75 - 1e-9
                    or overlap_z_ratio < .75 - 1e-9
                    or span_ratio < 1.25 - 1e-9
                    or boundary_length
                    < max(8.0, occupied_boundary_length * .75) - 1e-9):
                continue
            removed_indexes.add(footprint_index)
            removal_proofs.append({
                "method": "cad_detached_site_boundary_component_v1",
                "wall_footprint_index": footprint_index,
                "wall_footprint_id": str(
                    result_footprints[footprint_index].get("id") or ""),
                "component_bounds_m": [round(value, 8) for value in bounds],
                "occupied_space_bounds_m": [
                    round(value, 8) for value in occupied_bounds],
                "physical_space_count": len(model_spaces),
                "original_wall_component_count": len(parsed_components),
                "component_to_physical_space_distance_m": round(distance, 8),
                "occupied_x_span_overlap_ratio": round(overlap_x_ratio, 8),
                "occupied_z_span_overlap_ratio": round(overlap_z_ratio, 8),
                "maximum_component_to_occupied_span_ratio": round(
                    span_ratio, 8),
                "component_boundary_length_m": round(boundary_length, 8),
                "occupied_space_boundary_length_m": round(
                    occupied_boundary_length, 8),
                "thresholds": {
                    "minimum_detachment_distance_m": .35,
                    "minimum_occupied_axis_overlap_ratio": .75,
                    "minimum_component_to_occupied_span_ratio": 1.25,
                    "minimum_component_boundary_length_m": 8.0,
                    "minimum_component_to_occupied_boundary_length_ratio": .75,
                },
                "decision_basis": [
                    "global_topology_has_multiple_wall_components",
                    "component_detached_from_all_proved_physical_spaces",
                    "component_encloses_most_of_occupied_span_on_both_axes",
                    "component_substantially_exceeds_occupied_building_span",
                    "source_preserved_as_nonproduction_site_boundary_evidence",
                ],
            })
        if not removed_indexes:
            return result_assemblies, result_footprints, result_summary

        removed_geometries = [
            component for index, component in parsed_components
            if index in removed_indexes
        ]
        removed_mask = unary_union(removed_geometries).buffer(.03)
        rejected_assembly_ids: list[str] = []
        for assembly in result_assemblies:
            if str(assembly.get("review_status") or "") \
                    not in {"accepted", "confirmed"}:
                continue
            source_geometry = None
            try:
                polygon = Polygon(assembly.get("footprint_polygon") or [])
                if polygon.is_valid and not polygon.is_empty:
                    source_geometry = polygon
            except (TypeError, ValueError):
                source_geometry = None
            if source_geometry is None:
                try:
                    line = LineString(assembly.get("centerline") or [])
                    if line.length > 1e-9:
                        source_geometry = line
                except (TypeError, ValueError):
                    source_geometry = None
            if (source_geometry is None
                    or not removed_mask.covers(source_geometry)):
                continue
            original_geometry = {
                "source_representation": str(
                    assembly.get("source_representation") or ""),
                "centerline": copy.deepcopy(assembly.get("centerline") or []),
                "footprint_polygon": copy.deepcopy(
                    assembly.get("footprint_polygon") or []),
                "thickness_m": assembly.get("thickness_m"),
                "length_m": assembly.get("length_m"),
            }
            proof = min(
                removal_proofs,
                key=lambda row: min(
                    float(component.distance(source_geometry))
                    for index, component in parsed_components
                    if index == row["wall_footprint_index"]
                ),
            )
            assembly["source_centerline"] = copy.deepcopy(
                original_geometry["centerline"])
            assembly["source_representation"] = \
                "detached_site_boundary_evidence"
            assembly["resolved_as"] = "nonproduction_site_boundary"
            assembly["review_status"] = "rejected"
            assembly["reason"] = \
                "cad_detached_site_boundary_not_physical_space_boundary"
            assembly["reason_codes"] = sorted(set(
                (assembly.get("reason_codes") or [])
                + ["cad_detached_site_boundary_not_physical_space_boundary"]
            ))
            assembly["production_blockers"] = []
            assembly["detached_site_boundary_evidence"] = {
                **copy.deepcopy(proof),
                "source_wall_geometry": original_geometry,
            }
            for key in ("start", "end", "centerline", "opening_axis",
                        "footprint_polygon", "thickness_m", "length_m"):
                assembly.pop(key, None)
            rejected_assembly_ids.append(str(assembly.get("id") or ""))

        result_footprints = [
            row for index, row in enumerate(result_footprints)
            if index not in removed_indexes
        ]
        kept_polygons = []
        for footprint in result_footprints:
            polygon = Polygon(
                [(float(point["x"]), float(point["z"]))
                 for point in footprint.get("points") or []],
                [[(float(point["x"]), float(point["z"])) for point in ring]
                 for ring in footprint.get("interior_rings") or []],
            ).buffer(0)
            if not polygon.is_empty:
                kept_polygons.append(polygon)
        production_wall_area = float(
            unary_union(kept_polygons).area) if kept_polygons else 0.0
        result_summary.update({
            "wall_footprint_count": len(result_footprints),
            "wall_component_count": len(kept_polygons),
            "wall_area_m2": round(production_wall_area, 6),
            "detached_site_boundary_component_count": len(removed_indexes),
            "detached_site_boundary_assembly_count": len(
                rejected_assembly_ids),
            "detached_site_boundary_assembly_ids": rejected_assembly_ids[:100],
            "detached_site_boundary_evidence": removal_proofs[:20],
            "detached_site_boundary_evidence_truncated": len(
                removal_proofs) > 20,
        })
        decision_basis = list(result_summary.get("decision_basis") or [])
        if "detached_oversized_site_boundary_excluded_from_production_walls" \
                not in decision_basis:
            decision_basis.append(
                "detached_oversized_site_boundary_excluded_from_production_walls")
        result_summary["decision_basis"] = decision_basis
        return result_assemblies, result_footprints, result_summary
    except Exception:
        # This is a production exclusion gate: any incomplete proof must leave
        # geometry untouched instead of guessing that a detached component is
        # non-architectural.
        return (copy.deepcopy(assemblies), copy.deepcopy(footprints),
                copy.deepcopy(dict(topology_summary)))


def _bind_openings_to_global_wall_footprints(
    candidates: list[dict], assemblies: list[dict], footprints: list[dict], *,
    origin_x: float, origin_z: float,
) -> tuple[list[dict], list[dict]]:
    """Bind a proven door/window axis to the canonical whole-plan wall mask.

    Some drawings expose a continuous wall face, or only one of two faces,
    through an opening.  Local two-face WallAssembly pairing cannot host that
    opening even though the global source-backed wall footprint proves it.
    This fallback accepts only an axis that is almost completely contained by
    the wall mask and has one unique, architectural-thickness cross-section.
    """
    try:
        from shapely.geometry import LineString, Point, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:
        return candidates, assemblies
    polygons = []
    for footprint in footprints:
        try:
            exterior = [(float(point["x"]), float(point["z"]))
                        for point in footprint.get("points") or []]
            holes = [[(float(point["x"]), float(point["z"])) for point in ring]
                     for ring in footprint.get("interior_rings") or []]
            polygon = Polygon(exterior, holes)
        except (KeyError, TypeError, ValueError):
            continue
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if not polygon.is_empty and polygon.geom_type == "Polygon":
            polygons.append(polygon)
        elif not polygon.is_empty and polygon.geom_type == "MultiPolygon":
            polygons.extend(part for part in polygon.geoms if not part.is_empty)
    if not polygons:
        return candidates, assemblies
    wall_mask = unary_union(polygons).buffer(0)
    result_candidates = copy.deepcopy(candidates)
    result_assemblies = copy.deepcopy(assemblies)
    assembly_by_id = {
        str(row.get("id") or ""): row for row in result_assemblies
        if str(row.get("id") or "")
    }
    local_wall_supports = []
    for support in result_assemblies:
        if str(support.get("review_status") or "") not in {"accepted", "confirmed"}:
            continue
        try:
            support_axis = LineString(support.get("centerline") or [])
            support_polygon = Polygon(support.get("footprint_polygon") or [])
            support_thickness = float(support.get("thickness_m"))
        except (TypeError, ValueError):
            continue
        if (support_axis.length > 1e-9 and support_polygon.is_valid
                and not support_polygon.is_empty
                and .06 <= support_thickness <= .60):
            local_wall_supports.append({
                "assembly": support, "axis": support_axis,
                "polygon": support_polygon, "thickness": support_thickness,
            })
    for candidate in result_candidates:
        candidate_status = str(candidate.get("status") or "")
        if candidate_status == "rejected":
            continue
        if candidate_status in {"accepted", "confirmed"}:
            # Local binding is provisional until the selected canonical host
            # can physically contain the measured opening.  Dense projected
            # plans may leave a tiny accepted jamb fragment (>=100 mm, so it
            # is renderable) carrying the candidate's assembly id.  Skipping
            # every already-accepted candidate here then rejects a perfectly
            # proved door much later when its width is clipped to that short
            # fragment.  Re-open only this capacity-invalid case and require
            # the same strict global jamb/wall-mask proof used for unresolved
            # openings; a valid local host remains untouched.
            host_id = str(candidate.get("wall_assembly_id") or "")
            host = assembly_by_id.get(host_id)
            host_axis = (host or {}).get("centerline") or []
            try:
                host_length = float(LineString(host_axis).length)
                opening_width = float(candidate.get("width_m") or 0.0)
            except (TypeError, ValueError):
                host_length, opening_width = 0.0, 0.0
            capacity_tolerance = max(.02, opening_width * .02)
            if (host is not None and opening_width >= .20
                    and host_length + capacity_tolerance
                    >= opening_width - 1e-9):
                continue
            candidate.setdefault("evidence_geometry", {})[
                "superseded_local_opening_host"] = {
                    "wall_assembly_id": host_id,
                    "host_found": host is not None,
                    "host_centerline_length_m": round(host_length, 8),
                    "opening_width_m": round(opening_width, 8),
                    "capacity_tolerance_m": round(capacity_tolerance, 8),
                    "reason": "local_host_shorter_than_measured_opening",
                }
            candidate["status"] = "review"
            candidate["reason_codes"] = sorted(set(
                (candidate.get("reason_codes") or [])
                + ["local_opening_host_capacity_insufficient"]
            ))
        evidence_geometry = candidate.get("evidence_geometry") or {}
        raw_options = [("primary", candidate.get("axis_segment_cad_m") or [])]
        # Once two mirrored leaves have been merged, their hinge-to-hinge
        # primary axis is the only physical opening span.  The inherited
        # per-leaf alternatives are half-width swing radii; allowing one of
        # them to win wall-mask scoring silently turns one double door back
        # into a single leaf and makes width disagree with axis length.
        if not evidence_geometry.get("double_leaf_door"):
            raw_options.extend(
                (f"candidate_{index}", row.get("axis_segment_cad_m") or [])
                for index, row in enumerate(
                    evidence_geometry.get("axis_candidates") or [], 1))
            for merged_index, merged in enumerate(
                    evidence_geometry.get("merged_circular_swing_evidence") or [], 1):
                merged_evidence = merged.get("evidence_geometry") \
                    if isinstance(merged, Mapping) else {}
                raw_options.extend(
                    (f"merged_arc_{merged_index}_candidate_{axis_index}",
                     row.get("axis_segment_cad_m") or [])
                    for axis_index, row in enumerate(
                        merged_evidence.get("axis_candidates") or [], 1))
        matches = []
        seen = set()
        for source, raw_axis in raw_options:
            try:
                cad_first = (float(raw_axis[0][0]), float(raw_axis[0][1]))
                cad_second = (float(raw_axis[-1][0]), float(raw_axis[-1][1]))
            except (TypeError, ValueError, IndexError):
                continue
            first = (cad_first[0] - origin_x, cad_first[1] - origin_z)
            second = (cad_second[0] - origin_x, cad_second[1] - origin_z)
            key = tuple(sorted((tuple(round(value, 8) for value in first),
                                tuple(round(value, 8) for value in second))))
            if key in seen:
                continue
            seen.add(key)
            line = LineString([first, second])
            if not .20 <= line.length <= 3.0:
                continue
            try:
                observed_width = float(candidate.get("width_m") or 0.0)
            except (TypeError, ValueError):
                observed_width = 0.0
            if (observed_width > 0.0
                    and abs(line.length - observed_width)
                    > max(.16, observed_width * .20)):
                continue
            coverage = float(line.intersection(wall_mask.buffer(.015)).length) / line.length
            midpoint = line.interpolate(.5, normalized=True)
            dx = (second[0] - first[0]) / line.length
            dz = (second[1] - first[1]) / line.length
            cross = LineString([
                (midpoint.x - dz * .50, midpoint.y + dx * .50),
                (midpoint.x + dz * .50, midpoint.y - dx * .50),
            ])
            cross_geometry = cross.intersection(wall_mask)
            components = (list(cross_geometry.geoms)
                          if getattr(cross_geometry, "geom_type", "")
                          in {"MultiLineString", "GeometryCollection"}
                          else [cross_geometry])
            supported = [part for part in components
                         if getattr(part, "length", 0) > 0
                         and part.distance(Point(midpoint.x, midpoint.y)) <= .02]
            thickness = max((float(part.length) for part in supported), default=0.0)
            if coverage < .90 or not .06 <= thickness <= .60:
                continue
            # A door supplies multiple physical closed-axis alternatives.  An
            # incorrect alternative can lie *inside* a continuous wall and
            # therefore score perfect wall-mask coverage.  Doors must instead
            # pass the two-jamb/terminal proof below; generic mask coverage is
            # only a valid opening host for non-door candidates.
            if str(candidate.get("kind") or "") == "door":
                continue
            matches.append({
                "source": source, "cad_axis": [list(cad_first), list(cad_second)],
                "first": first, "second": second, "coverage": coverage,
                "thickness": thickness,
            })
        if not matches:
            # A real window is, by definition, a gap in the wall mask.  Some
            # plans draw both wall faces only up to the jambs, so requiring the
            # opening axis itself to be covered by the mask makes the strongest
            # window evidence impossible to bind.  In that convention the CAD
            # window frame is the dimensional authority: two opposing long
            # rails measure wall thickness, cross members prove both jambs and
            # the two axis endpoints must still land next to the source-backed
            # wall mask.  This is intentionally window-only; a generic symbol
            # or a lone rectangle cannot create a production wall host.
            reason_codes = {str(value) for value in candidate.get("reason_codes") or []}
            door_matches = []
            arc_door_ready = {
                "circular_swing_arc", "radial_door_leaf",
                "wall_network_supported"}.issubset(reason_codes)
            leaf_proof = evidence_geometry if (
                evidence_geometry.get("method")
                == "cad_parallel_door_leaf_without_arc_v1") else {}
            try:
                leaf_door_ready = bool(
                    {"parallel_door_leaf_rails",
                     "hinge_endpoint_wall_supported",
                     "swing_leaf_without_arc",
                     "wall_network_supported"}.issubset(reason_codes)
                    and 3 <= int(leaf_proof.get("source_row_count") or 0) <= 5
                    and int(leaf_proof.get("parallel_rail_count") or 0)
                    == int(leaf_proof.get("source_row_count") or 0)
                    and len(set(str(value) for value in
                                candidate.get("source_handles") or []
                                if str(value))) >= 3
                    and len(set(int(value) for value in
                                candidate.get("source_entity_indexes") or [])) >= 3
                    and float(leaf_proof.get("leaf_angle_spread_deg") or 0)
                    <= 1.0 + 1e-9
                    and float(leaf_proof.get("leaf_length_spread_m") or 0)
                    <= max(.02, float(candidate.get("width_m") or 0) * .02) + 1e-9
                    and float(leaf_proof.get(
                        "hinge_endpoint_cluster_radius_m") or 0) <= .08 + 1e-9
                    and float(leaf_proof.get(
                        "free_endpoint_cluster_radius_m") or 0) <= .08 + 1e-9
                    and float(leaf_proof.get("hinge_wall_distance_m") or 0)
                    <= .12 + 1e-9
                    and float(leaf_proof.get("free_endpoint_wall_distance_m") or 0)
                    >= .20 - 1e-9
                    and .06 <= float(leaf_proof.get(
                        "selected_wall_face_separation_m") or 0) <= .60
                    and len(leaf_proof.get("axis_candidates") or []) >= 1)
            except (TypeError, ValueError):
                leaf_door_ready = False
            door_axis_attempts: list[dict] = []
            if (str(candidate.get("kind") or "") == "door"
                    and (arc_door_ready or leaf_door_ready)):
                leaf_without_arc = leaf_door_ready and not (
                    evidence_geometry.get("arc_radius_m") and arc_door_ready)

                def leaf_source_face_jambs(raw_axis: list[Any]) -> Optional[dict]:
                    if not leaf_without_arc:
                        return None
                    try:
                        target = LineString([
                            (float(raw_axis[0][0]), float(raw_axis[0][1])),
                            (float(raw_axis[-1][0]), float(raw_axis[-1][1])),
                        ])
                    except (TypeError, ValueError, IndexError):
                        return None
                    matches = []
                    for option in leaf_proof.get("axis_candidates") or []:
                        option_axis = option.get("axis_segment_cad_m") or []
                        try:
                            option_line = LineString([
                                (float(option_axis[0][0]), float(option_axis[0][1])),
                                (float(option_axis[-1][0]), float(option_axis[-1][1])),
                            ])
                            supports = option.get("source_face_jamb_supports") or []
                            thickness_delta = float(option.get(
                                "source_face_jamb_thickness_delta_m"))
                        except (TypeError, ValueError, IndexError):
                            continue
                        if (target.hausdorff_distance(option_line) > .01 + 1e-9
                                or option.get("source_face_jamb_proved") is not True
                                or len(supports) != 2
                                or {int(row.get("endpoint_index", -1))
                                    for row in supports if isinstance(row, dict)}
                                != {0, 1}
                                or thickness_delta > .04 + 1e-9):
                            continue
                        valid = True
                        for support in supports:
                            try:
                                separation = float(support["face_separation_m"])
                                midpoint_offset = float(
                                    support["wall_band_midpoint_offset_m"])
                                endpoint_face_distances = [float(value) for value in
                                    support["wall_face_endpoint_distance_m"]]
                                outward_extensions = [float(value) for value in
                                    support["wall_face_outward_extension_m"]]
                                angle_differences = [float(value) for value in
                                    support["wall_face_axis_angle_difference_deg"]]
                                source_handles = {str(value) for value in
                                    support["wall_face_source_handles"] if str(value)}
                            except (KeyError, TypeError, ValueError):
                                valid = False
                                break
                            if (support.get("method")
                                    != "cad_source_wall_face_pair_at_door_jamb_v1"
                                    or len(source_handles) < 2
                                    or not .06 <= separation <= .60
                                    or abs(midpoint_offset) > .08 + 1e-9
                                    or len(endpoint_face_distances) != 2
                                    or max(endpoint_face_distances)
                                    > separation / 2 + .12 + 1e-9
                                    or len(outward_extensions) != 2
                                    or min(outward_extensions) < .05 - 1e-9
                                    or len(angle_differences) != 2
                                    or max(angle_differences) > 1.0 + 1e-9):
                                valid = False
                                break
                        if valid:
                            matches.append({
                                "supports": copy.deepcopy(supports),
                                "thickness_delta_m": thickness_delta,
                                "jamb_widths": [float(row["face_separation_m"])
                                                for row in supports],
                                "score": (
                                    sum(abs(float(row[
                                        "wall_band_midpoint_offset_m"]))
                                        for row in supports),
                                    sum(max(float(value) for value in row[
                                        "wall_face_endpoint_distance_m"])
                                        for row in supports)),
                            })
                    matches.sort(key=lambda value: value["score"])
                    if not matches:
                        return None
                    if (len(matches) > 1
                            and abs(float(matches[1]["score"][0])
                                    - float(matches[0]["score"][0])) <= .005 + 1e-9
                            and abs(float(matches[1]["score"][1])
                                    - float(matches[0]["score"][1])) <= .005 + 1e-9):
                        return None
                    return matches[0]

                def leaf_unique_wall_gap_axis(
                    raw_axis: list[Any],
                    wall_mask_endpoint_distances: list[float],
                ) -> Optional[dict]:
                    options = leaf_proof.get("axis_candidates") or []
                    if not leaf_without_arc or not options:
                        return None
                    try:
                        target = LineString([
                            (float(raw_axis[0][0]), float(raw_axis[0][1])),
                            (float(raw_axis[-1][0]), float(raw_axis[-1][1])),
                        ])
                    except (TypeError, ValueError, IndexError):
                        return None
                    matched_options = []
                    for option in options:
                        option_axis = option.get("axis_segment_cad_m") or []
                        try:
                            option_line = LineString([
                                (float(option_axis[0][0]), float(option_axis[0][1])),
                                (float(option_axis[-1][0]), float(option_axis[-1][1])),
                            ])
                        except (TypeError, ValueError, IndexError):
                            continue
                        if target.hausdorff_distance(option_line) <= .01 + 1e-9:
                            matched_options.append(option)
                    if len(matched_options) != 1:
                        return None
                    option = matched_options[0]
                    option_axis = option.get("axis_segment_cad_m") or []
                    try:
                        separation = float(option["wall_face_separation_m"])
                        endpoint_support = [float(value) for value in option[
                            "endpoint_wall_support_distance_m"]]
                        midpoint_clearance = float(option[
                            "axis_midpoint_wall_clearance_m"])
                        face_handles = sorted({str(value) for value in option[
                            "wall_face_source_handles"] if str(value)})
                    except (KeyError, TypeError, ValueError, IndexError):
                        return None
                    other_clearances = []
                    for other in options:
                        if other is option:
                            continue
                        try:
                            other_clearances.append(float(
                                other["axis_midpoint_wall_clearance_m"]))
                        except (KeyError, TypeError, ValueError):
                            return None
                    selection_margin = (
                        midpoint_clearance - max(other_clearances)
                        if other_clearances else None)
                    minimum_clearance = max(.06, separation / 2 - .03)
                    if (len(face_handles) < 2
                            or not .06 <= separation <= .60
                            or len(endpoint_support) != 2
                            or max(endpoint_support) > .151 + 1e-9
                            or len(wall_mask_endpoint_distances) != 2
                            or max(wall_mask_endpoint_distances) > .10 + 1e-9
                            or midpoint_clearance < minimum_clearance - 1e-9
                            or (selection_margin is not None
                                and selection_margin < .10 - 1e-9)):
                        return None
                    return {
                        "method": "cad_parallel_leaf_unique_wall_gap_axis_v1",
                        "axis_segment_cad_m": copy.deepcopy(option_axis),
                        "wall_face_entity_indexes": copy.deepcopy(
                            option.get("wall_face_entity_indexes") or []),
                        "wall_face_source_handles": face_handles,
                        "wall_face_separation_m": separation,
                        "source_endpoint_wall_support_distance_m": endpoint_support,
                        "wall_mask_endpoint_distance_m": copy.deepcopy(
                            wall_mask_endpoint_distances),
                        "axis_midpoint_wall_clearance_m": midpoint_clearance,
                        "minimum_axis_midpoint_wall_clearance_m": minimum_clearance,
                        "axis_candidate_count": len(options),
                        "axis_clearance_selection_margin_m": (
                            selection_margin if selection_margin is not None else None),
                        "thresholds": {
                            "maximum_axis_match_distance_m": .01,
                            "minimum_wall_face_separation_m": .06,
                            "maximum_wall_face_separation_m": .60,
                            "maximum_source_endpoint_wall_support_distance_m": .151,
                            "maximum_wall_mask_endpoint_distance_m": .10,
                            "minimum_absolute_midpoint_clearance_m": .06,
                            "wall_half_thickness_clearance_allowance_m": .03,
                            "minimum_multi_axis_clearance_selection_margin_m": .10,
                        },
                    }

                def arc_projected_transverse_jamb(
                    raw_axis: list[Any],
                    wall_mask_endpoint_distances: list[float],
                ) -> Optional[dict]:
                    if not arc_door_ready:
                        return None
                    try:
                        target = LineString([
                            (float(raw_axis[0][0]), float(raw_axis[0][1])),
                            (float(raw_axis[-1][0]), float(raw_axis[-1][1])),
                        ])
                    except (TypeError, ValueError, IndexError):
                        return None
                    matches = []
                    arc_evidence_sources = ([evidence_geometry]
                                            if evidence_geometry.get("arc_radius_m")
                                            else [])
                    arc_evidence_sources.extend(
                        row.get("evidence_geometry") or []
                        for row in evidence_geometry.get(
                            "merged_circular_swing_evidence") or []
                        if isinstance(row, Mapping))
                    for option in [
                            value for source_evidence in arc_evidence_sources
                            if isinstance(source_evidence, Mapping)
                            for value in source_evidence.get("axis_candidates") or []]:
                        if (option.get("projection_method")
                                != "cad_arc_leaf_wall_pair_transverse_jamb_projection_v1"):
                            continue
                        option_axis = option.get("axis_segment_cad_m") or []
                        try:
                            option_line = LineString([
                                (float(option_axis[0][0]), float(option_axis[0][1])),
                                (float(option_axis[-1][0]), float(option_axis[-1][1])),
                            ])
                            separation = float(option["wall_face_separation_m"])
                            hinge_offset = float(option[
                                "hinge_to_wall_centerline_offset_m"])
                            snap_distance = float(option[
                                "transverse_jamb_snap_distance_m"])
                            transverse_angle = float(option[
                                "transverse_jamb_angle_difference_deg"])
                            face_indexes = sorted({int(value) for value in
                                                   option[
                                                       "wall_face_entity_indexes"]})
                            transverse_index = int(option[
                                "transverse_jamb_entity_index"])
                            source_handles = sorted({str(value) for value in
                                                     option[
                                                         "wall_face_source_handles"]
                                                     if str(value)})
                        except (KeyError, TypeError, ValueError, IndexError):
                            continue
                        if (target.hausdorff_distance(option_line) > .005 + 1e-9
                                or len(face_indexes) != 2
                                or transverse_index in face_indexes
                                or len(source_handles) < 3
                                or not .06 <= separation <= .60
                                or hinge_offset > .20 + 1e-9
                                or snap_distance > .20 + 1e-9
                                or transverse_angle < 88.5 - 1e-9
                                or len(wall_mask_endpoint_distances) != 2
                                or max(wall_mask_endpoint_distances) > .05 + 1e-9):
                            continue
                        matches.append({
                            "method":
                                "cad_arc_leaf_wall_pair_transverse_jamb_projection_v1",
                            "axis_segment_cad_m": copy.deepcopy(option_axis),
                            "wall_face_entity_indexes": face_indexes,
                            "transverse_jamb_entity_index": transverse_index,
                            "wall_face_source_handles": source_handles,
                            "wall_face_separation_m": separation,
                            "hinge_to_wall_centerline_offset_m": hinge_offset,
                            "transverse_jamb_snap_distance_m": snap_distance,
                            "transverse_jamb_angle_difference_deg": transverse_angle,
                            "wall_mask_endpoint_distance_m": copy.deepcopy(
                                wall_mask_endpoint_distances),
                            "score": (hinge_offset + snap_distance
                                      + sum(wall_mask_endpoint_distances)),
                        })
                    matches.sort(key=lambda value: (
                        value["score"],
                        tuple(tuple(point) for point in
                              value["axis_segment_cad_m"])))
                    return matches[0] if len(matches) == 1 else None

                seen_door_axes = set()
                for source, raw_axis in raw_options:
                    try:
                        door_cad_first = (float(raw_axis[0][0]), float(raw_axis[0][1]))
                        door_cad_second = (float(raw_axis[-1][0]), float(raw_axis[-1][1]))
                    except (TypeError, ValueError, IndexError):
                        continue
                    door_first = (door_cad_first[0] - origin_x,
                                  door_cad_first[1] - origin_z)
                    door_second = (door_cad_second[0] - origin_x,
                                   door_cad_second[1] - origin_z)
                    key = tuple(sorted((
                        tuple(round(value, 8) for value in door_first),
                        tuple(round(value, 8) for value in door_second),
                    )))
                    if key in seen_door_axes:
                        continue
                    seen_door_axes.add(key)
                    door_line = LineString([door_first, door_second])
                    try:
                        observed_width = float(candidate.get("width_m") or 0.0)
                    except (TypeError, ValueError):
                        observed_width = 0.0
                    attempt = {
                        "source": source,
                        "axis_segment_cad_m": [list(door_cad_first),
                                                list(door_cad_second)],
                        "axis_length_m": round(float(door_line.length), 8),
                        "observed_width_m": round(observed_width, 8),
                    }
                    if (not .40 <= door_line.length <= 2.0
                            or abs(door_line.length - observed_width)
                            > max(.02, observed_width * .02) + 1e-9):
                        attempt["result"] = "axis_length_width_mismatch"
                        door_axis_attempts.append(attempt)
                        continue
                    endpoint_distances = [float(wall_mask.distance(Point(point)))
                                          for point in (door_first, door_second)]
                    attempt["wall_mask_endpoint_distance_m"] = [
                        round(value, 8) for value in endpoint_distances]
                    source_face_jambs = leaf_source_face_jambs(raw_axis)
                    unique_wall_gap = leaf_unique_wall_gap_axis(
                        raw_axis, endpoint_distances)
                    projected_arc_jamb = arc_projected_transverse_jamb(
                        raw_axis, endpoint_distances)
                    if (max(endpoint_distances) > .15 + 1e-9
                            and source_face_jambs is None
                            and unique_wall_gap is None
                            and projected_arc_jamb is None):
                        attempt["result"] = "wall_mask_endpoint_too_far"
                        door_axis_attempts.append(attempt)
                        continue
                    if source_face_jambs is not None:
                        jamb_widths = source_face_jambs["jamb_widths"]
                        attempt["jamb_cross_section_width_m"] = [
                            round(value, 8) for value in jamb_widths]
                        attempt["source_face_jamb_supports"] = copy.deepcopy(
                            source_face_jambs["supports"])
                        attempt["result"] = "accepted_source_face_jamb_pairs"
                        door_axis_attempts.append(attempt)
                        door_matches.append({
                            "source": source,
                            "cad_axis": [list(door_cad_first),
                                         list(door_cad_second)],
                            "first": door_first, "second": door_second,
                            "line": door_line,
                            "endpoint_distances": endpoint_distances,
                            "jamb_widths": jamb_widths,
                            "jamb_sample_offsets": [0.0, 0.0],
                            "source_face_jamb_supports": copy.deepcopy(
                                source_face_jambs["supports"]),
                            "binding_method": "source_face_jamb_pairs",
                            "score": (source_face_jambs["score"][0]
                                      + source_face_jambs["score"][1], source),
                        })
                        continue
                    if unique_wall_gap is not None:
                        jamb_widths = [
                            float(unique_wall_gap["wall_face_separation_m"]),
                            float(unique_wall_gap["wall_face_separation_m"]),
                        ]
                        attempt["jamb_cross_section_width_m"] = [
                            round(value, 8) for value in jamb_widths]
                        attempt["unique_wall_gap_axis_evidence"] = copy.deepcopy(
                            unique_wall_gap)
                        attempt["result"] = "accepted_unique_leaf_wall_gap_axis"
                        door_axis_attempts.append(attempt)
                        door_matches.append({
                            "source": source,
                            "cad_axis": [list(door_cad_first),
                                         list(door_cad_second)],
                            "first": door_first, "second": door_second,
                            "line": door_line,
                            "endpoint_distances": endpoint_distances,
                            "jamb_widths": jamb_widths,
                            "jamb_sample_offsets": [0.0, 0.0],
                            "unique_wall_gap_axis_evidence": copy.deepcopy(
                                unique_wall_gap),
                            "binding_method": "unique_leaf_wall_gap_axis",
                            "score": (
                                sum(endpoint_distances)
                                - float(unique_wall_gap[
                                    "axis_midpoint_wall_clearance_m"]), source),
                        })
                        continue
                    if projected_arc_jamb is not None:
                        jamb_widths = [
                            float(projected_arc_jamb["wall_face_separation_m"]),
                            float(projected_arc_jamb["wall_face_separation_m"]),
                        ]
                        attempt["jamb_cross_section_width_m"] = [
                            round(value, 8) for value in jamb_widths]
                        attempt["projected_arc_transverse_jamb_evidence"] = \
                            copy.deepcopy(projected_arc_jamb)
                        attempt["result"] = \
                            "accepted_arc_wall_pair_transverse_jamb_projection"
                        door_axis_attempts.append(attempt)
                        door_matches.append({
                            "source": source,
                            "cad_axis": [list(door_cad_first),
                                         list(door_cad_second)],
                            "first": door_first, "second": door_second,
                            "line": door_line,
                            "endpoint_distances": endpoint_distances,
                            "jamb_widths": jamb_widths,
                            "jamb_sample_offsets": [0.0, 0.0],
                            "projected_arc_transverse_jamb_evidence":
                                copy.deepcopy(projected_arc_jamb),
                            "binding_method":
                                "projected_arc_transverse_jamb",
                            "score": (float(projected_arc_jamb["score"]), source),
                        })
                        continue
                    unit = ((door_second[0] - door_first[0]) / door_line.length,
                            (door_second[1] - door_first[1]) / door_line.length)
                    normal = (-unit[1], unit[0])
                    jamb_widths = []
                    jamb_sample_offsets = []
                    for endpoint, direction in ((door_first, -1.0),
                                                (door_second, 1.0)):
                        samples = []
                        for outward_offset in (.03, .06, .10, .14):
                            sample = (
                                endpoint[0] + unit[0] * direction * outward_offset,
                                endpoint[1] + unit[1] * direction * outward_offset,
                            )
                            cross = LineString([
                                (sample[0] - normal[0] * .50,
                                 sample[1] - normal[1] * .50),
                                (sample[0] + normal[0] * .50,
                                 sample[1] + normal[1] * .50),
                            ])
                            geometry = cross.intersection(wall_mask)
                            parts = (list(geometry.geoms)
                                     if getattr(geometry, "geom_type", "")
                                     in {"MultiLineString", "GeometryCollection"}
                                     else [geometry])
                            supported = [part for part in parts
                                         if getattr(part, "length", 0) > 0
                                         and part.distance(Point(sample)) <= .02 + 1e-9
                                         and .06 - 1e-9 <= float(part.length) <= .60 + 1e-9]
                            if len(supported) == 1:
                                samples.append((outward_offset,
                                                float(supported[0].length)))
                        if not samples:
                            jamb_widths = []
                            break
                        samples.sort(key=lambda value: (value[0], value[1]))
                        jamb_sample_offsets.append(samples[0][0])
                        jamb_widths.append(samples[0][1])
                    attempt["jamb_cross_section_width_m"] = [
                        round(value, 8) for value in jamb_widths]
                    attempt["jamb_sample_outward_offset_m"] = [
                        round(value, 8) for value in jamb_sample_offsets]
                    if (len(jamb_widths) == 2
                            and abs(jamb_widths[0] - jamb_widths[1])
                            <= .04 + 1e-9):
                        attempt["result"] = "accepted_global_jamb_cross_sections"
                        door_axis_attempts.append(attempt)
                        door_matches.append({
                            "source": source,
                            "cad_axis": [list(door_cad_first), list(door_cad_second)],
                            "first": door_first, "second": door_second,
                            "line": door_line,
                            "endpoint_distances": endpoint_distances,
                            "jamb_widths": jamb_widths,
                            "jamb_sample_offsets": jamb_sample_offsets,
                            "binding_method": "global_jamb_cross_sections",
                            "score": (sum(endpoint_distances)
                                      + abs(jamb_widths[0] - jamb_widths[1]), source),
                        })
                        continue

                    # At a wall corner one side of a real door opening can end
                    # in a transverse terminal wall rather than a second
                    # collinear jamb.  The swing arc still gives two possible
                    # closed axes.  Accept only the axis whose endpoints each
                    # have one unique, distinct accepted wall support with
                    # matching thickness; at least one support must continue
                    # collinearly and the other may be a bounded right-angle
                    # terminal.  This is independent of layer/block names.
                    if max(endpoint_distances) > .04 + 1e-9:
                        attempt["result"] = \
                            "jamb_cross_sections_unresolved_and_not_terminal_close"
                        door_axis_attempts.append(attempt)
                        continue
                    door_axis_angle = math.degrees(math.atan2(
                        door_second[1] - door_first[1],
                        door_second[0] - door_first[0])) % 180.0
                    terminal_supports = []
                    for endpoint_index, endpoint in enumerate(
                            (door_first, door_second)):
                        endpoint_point = Point(endpoint)
                        endpoint_matches = []
                        for support in local_wall_supports:
                            support_axis = support["axis"]
                            support_angle = math.degrees(math.atan2(
                                support_axis.coords[-1][1] - support_axis.coords[0][1],
                                support_axis.coords[-1][0] - support_axis.coords[0][0],
                            )) % 180.0
                            angle_difference = abs(door_axis_angle - support_angle)
                            angle_difference = min(
                                angle_difference, 180.0 - angle_difference)
                            orientation = (
                                "collinear" if angle_difference <= 1.5 + 1e-9
                                else "transverse_terminal"
                                if angle_difference >= 88.5 - 1e-9 else "")
                            if not orientation:
                                continue
                            footprint_distance = float(endpoint_point.distance(
                                support["polygon"]))
                            axis_endpoint_distance = min(
                                float(endpoint_point.distance(Point(
                                    support_axis.coords[0]))),
                                float(endpoint_point.distance(Point(
                                    support_axis.coords[-1]))),
                            )
                            terminal_limit = support["thickness"] / 2.0 + .04
                            if (footprint_distance <= .04 + 1e-9
                                    and axis_endpoint_distance
                                    <= terminal_limit + 1e-9):
                                endpoint_matches.append({
                                    "endpoint_index": endpoint_index,
                                    "wall_assembly_id": str(
                                        support["assembly"].get("id") or ""),
                                    "orientation": orientation,
                                    "axis_angle_difference_deg": round(
                                        angle_difference, 8),
                                    "wall_thickness_m": round(
                                        support["thickness"], 8),
                                    "endpoint_footprint_distance_m": round(
                                        footprint_distance, 8),
                                    "endpoint_axis_terminal_distance_m": round(
                                        axis_endpoint_distance, 8),
                                    "endpoint_axis_terminal_distance_limit_m": round(
                                        terminal_limit, 8),
                                })
                        endpoint_matches.sort(key=lambda row: (
                            row["endpoint_footprint_distance_m"],
                            row["endpoint_axis_terminal_distance_m"],
                            row["wall_assembly_id"],
                        ))
                        if not endpoint_matches:
                            terminal_supports = []
                            break
                        best_support = endpoint_matches[0]
                        tied_ids = {
                            row["wall_assembly_id"] for row in endpoint_matches
                            if abs(float(row["endpoint_footprint_distance_m"])
                                   - float(best_support[
                                       "endpoint_footprint_distance_m"]))
                            <= .002 + 1e-9
                        }
                        if len(tied_ids) != 1:
                            terminal_supports = []
                            break
                        terminal_supports.append(best_support)
                    if (len(terminal_supports) != 2
                            or len({row["wall_assembly_id"]
                                    for row in terminal_supports}) != 2
                            or not any(row["orientation"] == "collinear"
                                       for row in terminal_supports)
                            or abs(float(terminal_supports[0]["wall_thickness_m"])
                                   - float(terminal_supports[1]["wall_thickness_m"]))
                            > .02 + 1e-9):
                        attempt["terminal_wall_supports"] = copy.deepcopy(
                            terminal_supports)
                        attempt["result"] = "terminal_wall_supports_unresolved"
                        door_axis_attempts.append(attempt)
                        continue
                    jamb_widths = [float(row["wall_thickness_m"])
                                   for row in terminal_supports]
                    door_matches.append({
                        "source": source,
                        "cad_axis": [list(door_cad_first), list(door_cad_second)],
                        "first": door_first, "second": door_second,
                        "line": door_line,
                        "endpoint_distances": endpoint_distances,
                        "jamb_widths": jamb_widths,
                        "jamb_sample_offsets": [0.0, 0.0],
                        "terminal_wall_supports": terminal_supports,
                        "binding_method": "terminal_wall_supports",
                        "score": (sum(endpoint_distances)
                                  + sum(float(row["endpoint_footprint_distance_m"])
                                        for row in terminal_supports), source),
                    })
                    attempt["terminal_wall_supports"] = copy.deepcopy(
                        terminal_supports)
                    attempt["result"] = "accepted_terminal_wall_supports"
                    door_axis_attempts.append(attempt)
            if door_axis_attempts:
                candidate.setdefault("evidence_geometry", {})[
                    "global_door_axis_binding_attempts"] = copy.deepcopy(
                        door_axis_attempts)
            door_matches.sort(key=lambda row: row["score"])
            # The same physical closed axis is often projected from several
            # nearly coincident source-face pairs.  Raw projection multiplicity
            # is evidence redundancy, not an ambiguous door orientation.  A
            # dense architectural export can expose both faces, centre lines,
            # finish lines and jamb returns from the *same* wall band; their
            # centre-axis hypotheses can span almost one 120 mm partition.
            # Form deterministic 120 mm Hausdorff clusters first, then apply
            # the 30 mm selection margin only between physically distinct
            # axes.  Orthogonal swing alternatives remain roughly one leaf
            # width apart and therefore cannot collapse into this cluster.
            physical_axis_tolerance_m = .12
            physical_door_clusters: list[dict] = []
            for match in door_matches:
                equivalent_cluster = next((
                    cluster for cluster in physical_door_clusters
                    if abs(float(cluster["representative"]["line"].length)
                           - float(match["line"].length))
                    <= physical_axis_tolerance_m + 1e-9
                    and float(cluster["representative"]["line"].hausdorff_distance(
                        match["line"])) <= physical_axis_tolerance_m + 1e-9
                ), None)
                if equivalent_cluster is None:
                    physical_door_clusters.append({
                        "representative": match, "members": [match]})
                else:
                    equivalent_cluster["members"].append(match)
            physical_door_matches = [
                cluster["representative"] for cluster in physical_door_clusters]
            if physical_door_matches:
                best_door = physical_door_matches[0]
                distinct_runner_up = (
                    physical_door_matches[1]
                    if len(physical_door_matches) > 1 else None)
                if not (distinct_runner_up is not None
                        and float(distinct_runner_up["score"][0])
                        - float(best_door["score"][0]) <= .03 + 1e-9):
                    handles = sorted({str(value) for value in
                                      candidate.get("source_handles") or [] if str(value)})
                    if handles:
                        candidate_id = str(candidate.get("candidate_id") or "door")
                        host_id = f"cad_wall_door_swing_host_{candidate_id}"
                        thickness = sum(best_door["jamb_widths"]) / 2.0
                        footprint = best_door["line"].buffer(
                            thickness / 2, cap_style=2, join_style=2)
                        source_entities = [{
                            "entity_index": int(value), "segment_index": 0,
                            "handle": handles[0],
                            "root_handle": str(candidate.get("source_root_handle")
                                               or handles[0]),
                            "source_handle": handles[0], "layer": "", "block": "",
                            "insert_chain": [],
                            "source_segment_m": copy.deepcopy(best_door["cad_axis"]),
                            "model_segment_m": [list(best_door["first"]),
                                                list(best_door["second"])],
                            "cad_provenance": {"source_handles": handles},
                        } for value in (candidate.get("source_entity_indexes") or [0])]
                        terminal_binding = best_door.get("binding_method") \
                            == "terminal_wall_supports"
                        source_face_binding = best_door.get("binding_method") \
                            == "source_face_jamb_pairs"
                        unique_gap_binding = best_door.get("binding_method") \
                            == "unique_leaf_wall_gap_axis"
                        projected_arc_binding = best_door.get("binding_method") \
                            == "projected_arc_transverse_jamb"
                        proof_method = (
                            "cad_door_swing_wall_pair_transverse_jamb_host_v1"
                            if projected_arc_binding else
                            "cad_door_leaf_unique_source_face_jamb_host_v1"
                            if leaf_without_arc and source_face_binding else
                            "cad_door_leaf_unique_wall_gap_axis_host_v1"
                            if leaf_without_arc and unique_gap_binding else
                            "cad_door_leaf_unique_terminal_wall_support_v1"
                            if leaf_without_arc and terminal_binding else
                            "cad_door_leaf_unique_jamb_host_v1"
                            if leaf_without_arc else
                            "cad_door_swing_unique_terminal_wall_support_v1"
                            if terminal_binding else
                            "cad_door_swing_unique_jamb_host_v1")
                        proof = {
                            "method": proof_method,
                            "kind": "door", "candidate_id": candidate_id,
                            "opening_source_handles": handles,
                            "opening_axis_cad_m": copy.deepcopy(best_door["cad_axis"]),
                            "opening_width_m": round(best_door["line"].length, 8),
                            "axis_candidate_count": len(seen_door_axes),
                            "raw_viable_axis_count": len(door_matches),
                            "viable_axis_count": len(physical_door_matches),
                            "selected_axis_equivalent_projection_count": len(
                                physical_door_clusters[0]["members"]),
                            "axis_equivalence_hausdorff_tolerance_m":
                                physical_axis_tolerance_m,
                            "selected_axis_source": best_door["source"],
                            "wall_mask_endpoint_distance_m": [
                                round(value, 8) for value in
                                best_door["endpoint_distances"]],
                            "jamb_cross_section_width_m": [
                                round(value, 8) for value in best_door["jamb_widths"]],
                            "jamb_sample_outward_offset_m": [
                                round(value, 8) for value in
                                best_door["jamb_sample_offsets"]],
                            **({"terminal_wall_supports": copy.deepcopy(
                                best_door["terminal_wall_supports"])}
                               if terminal_binding else {}),
                            **({"source_face_jamb_supports": copy.deepcopy(
                                best_door["source_face_jamb_supports"])}
                               if source_face_binding else {}),
                            **({"unique_wall_gap_axis_evidence": copy.deepcopy(
                                best_door["unique_wall_gap_axis_evidence"])}
                               if unique_gap_binding else {}),
                            **({"projected_arc_transverse_jamb_evidence":
                                copy.deepcopy(best_door[
                                    "projected_arc_transverse_jamb_evidence"])}
                               if projected_arc_binding else {}),
                            "source_reason_codes": sorted(reason_codes),
                            **({"parallel_leaf_without_arc_evidence": copy.deepcopy(
                                leaf_proof)} if leaf_without_arc else {}),
                            "thresholds": {
                                "maximum_wall_mask_endpoint_distance_m": (
                                    .04 if terminal_binding else
                                    .05 if projected_arc_binding else
                                    .25 if source_face_binding else .15),
                                "maximum_jamb_width_delta_m": .04,
                                "minimum_wall_thickness_m": .06,
                                "maximum_wall_thickness_m": .60,
                                "minimum_axis_selection_margin": .03,
                                **({
                                    "maximum_terminal_footprint_distance_m": .04,
                                    "maximum_terminal_axis_extra_m": .04,
                                    "maximum_terminal_thickness_delta_m": .02,
                                    "maximum_collinear_angle_difference_deg": 1.5,
                                    "minimum_transverse_angle_difference_deg": 88.5,
                                } if terminal_binding else {}),
                                **({
                                    "maximum_source_face_axis_difference_deg": 1.0,
                                    "maximum_source_face_midpoint_offset_m": .08,
                                    "minimum_source_face_outward_extension_m": .05,
                                    "maximum_source_face_thickness_delta_m": .04,
                                } if source_face_binding else {}),
                                **({
                                    "maximum_projected_arc_hinge_offset_m": .20,
                                    "maximum_transverse_jamb_snap_distance_m": .20,
                                    "minimum_transverse_jamb_angle_difference_deg": 88.5,
                                } if projected_arc_binding else {}),
                                **({
                                    "maximum_unique_gap_wall_mask_endpoint_distance_m": .10,
                                    "maximum_unique_gap_source_endpoint_distance_m": .151,
                                    "minimum_unique_gap_midpoint_clearance_m": .06,
                                } if unique_gap_binding else {}),
                            },
                            "decision_basis": ([
                                "source_circular_swing_arc_and_radial_leaf",
                                "measured_parallel_wall_face_pair",
                                "unique_transverse_source_jamb_intersection",
                                "leaf_length_preserved_on_local_wall_centerline",
                            ] if projected_arc_binding else [
                                ("source_three_or_more_parallel_open_leaf_rails"
                                 if leaf_without_arc else
                                 "source_circular_swing_arc_and_radial_leaf"),
                                "both_axis_endpoints_have_unique_distinct_accepted_wall_supports",
                                "matching_measured_terminal_wall_thicknesses",
                                "at_least_one_collinear_jamb_and_optional_right_angle_terminal",
                                "unique_best_physical_closed_door_axis",
                            ] if terminal_binding else [
                                ("source_three_or_more_parallel_open_leaf_rails"
                                 if leaf_without_arc else
                                 "source_circular_swing_arc_and_radial_leaf"),
                                ("two_original_wall_faces_extend_outward_at_each_jamb"
                                 if source_face_binding else
                                 "one_unique_closed_axis_crosses_a_source_backed_wall_gap"
                                 if unique_gap_binding else
                                 "both_axis_ends_extend_into_source_backed_wall_jambs"),
                                ("matching_source_face_pair_separations"
                                 if source_face_binding else
                                 "leaf_length_and_measured_hinge_wall_faces_bound_the_gap"
                                 if unique_gap_binding else
                                 "matching_measured_jamb_cross_sections"),
                                "unique_best_physical_closed_door_axis",
                            ]),
                        }
                        result_assemblies.append({
                            "id": host_id,
                            "source_representation":
                                "door_swing_geometry_opening_host",
                            "resolved_as": "centerline",
                            "start": {"x": round(best_door["first"][0], 8),
                                      "z": round(best_door["first"][1], 8)},
                            "end": {"x": round(best_door["second"][0], 8),
                                    "z": round(best_door["second"][1], 8)},
                            "centerline": [list(best_door["first"]),
                                           list(best_door["second"])],
                            "opening_axis": [list(best_door["first"]),
                                             list(best_door["second"])],
                            "length_m": round(best_door["line"].length, 8),
                            "thickness_m": round(thickness, 8),
                            "thickness_source":
                                ("cad_arc_projected_wall_face_pair_thickness"
                                 if projected_arc_binding else
                                 "cad_door_terminal_wall_support_thickness"
                                 if terminal_binding else
                                 "cad_door_source_face_pair_jamb_thickness"
                                 if source_face_binding else
                                 "cad_door_leaf_hinge_wall_face_span"
                                 if unique_gap_binding else
                                 "cad_door_jamb_global_wall_cross_sections"),
                            "height_m": 2.8,
                            "height_source": "project_default_assumption",
                            "footprint_polygon": [[round(float(x), 8),
                                                   round(float(z), 8)]
                                                  for x, z in list(
                                                      footprint.exterior.coords)[:-1]],
                            "boundary_kind": "door_swing_geometry_opening_host",
                            "kind": "interior", "source": "cad",
                            "review_status": "accepted", "confidence_grade": "A",
                            "confidence": 1.0, "legacy_wall_compatible": True,
                            "door_swing_geometry_opening_evidence": proof,
                            "source_entity_handles": handles,
                            "source_root_handles": ([str(
                                candidate.get("source_root_handle"))]
                                if candidate.get("source_root_handle") else handles[:1]),
                            "source_layers": [], "source_insert_chains": [],
                            "source_entities": source_entities,
                            "cad_provenance": {
                                "wall_assembly_source_representation":
                                    "door_swing_geometry_opening_host",
                                "handle": handles[0], "source_handle": handles[0],
                                "root_handle": str(candidate.get(
                                    "source_root_handle") or handles[0]),
                                "source_segment_m": copy.deepcopy(
                                    best_door["cad_axis"]),
                                "source_entities": copy.deepcopy(source_entities),
                            },
                        })
                        candidate.update({
                            "status": "accepted", "wall_assembly_id": host_id,
                            "axis_segment_cad_m": copy.deepcopy(best_door["cad_axis"]),
                            "center_cad_m": [round(
                                (best_door["cad_axis"][0][0]
                                 + best_door["cad_axis"][1][0]) / 2, 8), round(
                                (best_door["cad_axis"][0][1]
                                 + best_door["cad_axis"][1][1]) / 2, 8)],
                            "wall_source_handles": handles,
                        })
                        candidate["reason_codes"] = sorted(set(
                            reason for reason in (
                                (candidate.get("reason_codes") or [])
                                + [("canonical_door_swing_geometry_host_bound"
                                    if projected_arc_binding else
                                    "canonical_door_leaf_geometry_host_bound"
                                    if leaf_without_arc else
                                    "canonical_door_swing_geometry_host_bound")])
                            if reason != "opening_wall_assembly_unresolved"))
                        candidate.setdefault("evidence_geometry", {})[
                            "canonical_binding_axis_source"] = best_door["source"]
                        continue
            frame = evidence_geometry
            raw_axis = candidate.get("axis_segment_cad_m") or []
            try:
                cad_first = (float(raw_axis[0][0]), float(raw_axis[0][1]))
                cad_second = (float(raw_axis[-1][0]), float(raw_axis[-1][1]))
                first = (cad_first[0] - origin_x, cad_first[1] - origin_z)
                second = (cad_second[0] - origin_x, cad_second[1] - origin_z)
                line = LineString([first, second])
                observed_width = float(candidate.get("width_m") or 0.0)
                frame_thickness = float(frame.get("seed_rail_separation_m") or 0.0)
                signed_offsets = [float(value) for value in
                                  frame.get("signed_wall_face_offsets_m") or []]
                endpoint_support = [float(value) for value in
                                    frame.get("wall_endpoint_support_distance_m") or []]
                interior_overlap = float(frame.get("interior_wall_overlap_ratio") or 0.0)
                long_rail_count = int(frame.get("long_rail_count") or 0)
                cross_member_count = int(frame.get("cross_member_count") or 0)
            except (TypeError, ValueError, IndexError):
                continue
            handles = sorted({str(value) for value in candidate.get("source_handles") or []
                              if str(value)})
            endpoint_mask_distances = [
                float(wall_mask.distance(Point(point))) for point in (first, second)
            ]
            offset_span = ((max(signed_offsets) - min(signed_offsets))
                           if signed_offsets else 0.0)
            strict_frame_ready = bool(
                str(candidate.get("kind") or "") == "window"
                and frame.get("grouping_method")
                == "loose_maximal_parallel_rail_pair"
                and frame.get("opposite_wall_face_support") is True
                and long_rail_count >= 2 and cross_member_count >= 2
                and len(handles) >= 4 and len(endpoint_support) == 2
                and any(value < -.02 for value in signed_offsets)
                and any(value > .02 for value in signed_offsets)
                and max(endpoint_support) <= .12 + 1e-9
                and max(endpoint_mask_distances) <= .15 + 1e-9
                and interior_overlap >= .90 - 1e-9
                and .40 <= line.length <= 3.0
                and abs(line.length - observed_width)
                <= max(.02, observed_width * .02) + 1e-9
                and .06 <= frame_thickness <= .60
                and .06 <= offset_span <= .60
                and abs(offset_span - frame_thickness) <= .03 + 1e-9)

            sparse_proof = (frame.get("sparse_frame_evidence")
                            if isinstance(frame.get("sparse_frame_evidence"), dict)
                            else {})
            try:
                sparse_wall_thickness = float(
                    sparse_proof.get("supported_wall_band_width_m") or 0)
                sparse_midpoint_offset = float(
                    sparse_proof.get("wall_band_midpoint_offset_m") or 0)
                sparse_frame_ready = bool(
                    str(candidate.get("kind") or "") == "window"
                    and frame.get("grouping_method")
                    == "loose_maximal_parallel_rail_pair"
                    and frame.get("opposite_wall_face_support") is True
                    and sparse_proof.get("method")
                    == "sparse_parallel_frame_unique_wall_gap_v1"
                    and 2 <= int(sparse_proof.get("source_row_count") or 0) <= 3
                    and int(sparse_proof.get(
                        "negative_wall_face_support_count") or 0) >= 2
                    and int(sparse_proof.get(
                        "positive_wall_face_support_count") or 0) >= 2
                    and long_rail_count >= 2
                    and len(handles) >= 2
                    and len(endpoint_support) == 2
                    and max(endpoint_support) <= .09 + 1e-9
                    and max(endpoint_mask_distances) <= .15 + 1e-9
                    and interior_overlap <= .20 + 1e-9
                    and .40 <= line.length <= 3.0
                    and abs(line.length - observed_width)
                    <= max(.02, observed_width * .02) + 1e-9
                    and .06 <= sparse_wall_thickness <= .60
                    and abs(sparse_midpoint_offset) <= .08 + 1e-9)
            except (TypeError, ValueError):
                sparse_frame_ready = False
                sparse_wall_thickness = 0.0
                sparse_midpoint_offset = 0.0
            root_frame_row_count = len({
                int(value) for value in candidate.get("source_entity_indexes") or []
            })
            negative_face_count = sum(value < -.02 for value in signed_offsets)
            positive_face_count = sum(value > .02 for value in signed_offsets)
            try:
                root_frame_short_span = float(frame.get("short_span_m") or 0.0)
                root_frame_wall_thickness = offset_span
                root_frame_midpoint_offset = (
                    (max(signed_offsets) + min(signed_offsets)) / 2.0
                    if signed_offsets else 0.0)
                root_frame_ready = bool(
                    str(candidate.get("kind") or "") == "window"
                    and frame.get("grouping_method") == "source_root"
                    and frame.get("opposite_wall_face_support") is True
                    and bool(str(candidate.get("source_root_handle") or ""))
                    and len(handles) == 1
                    and 4 <= root_frame_row_count <= 64
                    and negative_face_count >= 2
                    and positive_face_count >= 2
                    and long_rail_count >= 2
                    and cross_member_count >= 2
                    and len(endpoint_support) == 2
                    and max(endpoint_support) <= .12 + 1e-9
                    and max(endpoint_mask_distances) <= .15 + 1e-9
                    and interior_overlap <= .20 + 1e-9
                    and .40 <= line.length <= 3.0
                    and abs(line.length - observed_width)
                    <= max(.02, observed_width * .02) + 1e-9
                    and .06 <= root_frame_wall_thickness <= .60
                    and .06 <= root_frame_short_span <= .60
                    and abs(root_frame_short_span - root_frame_wall_thickness)
                    <= .03 + 1e-9
                    and abs(root_frame_midpoint_offset) <= .08 + 1e-9)
            except (TypeError, ValueError):
                root_frame_ready = False
                root_frame_short_span = 0.0
                root_frame_wall_thickness = 0.0
                root_frame_midpoint_offset = 0.0
            if sparse_frame_ready or root_frame_ready:
                is_root_frame = root_frame_ready and not sparse_frame_ready
                supported_wall_thickness = (
                    root_frame_wall_thickness if is_root_frame
                    else sparse_wall_thickness)
                wall_midpoint_offset = (
                    root_frame_midpoint_offset if is_root_frame
                    else sparse_midpoint_offset)
                support_negative_count = (
                    negative_face_count if is_root_frame else int(
                        sparse_proof.get("negative_wall_face_support_count") or 0))
                support_positive_count = (
                    positive_face_count if is_root_frame else int(
                        sparse_proof.get("positive_wall_face_support_count") or 0))
                source_row_count = (
                    root_frame_row_count if is_root_frame else int(
                        sparse_proof.get("source_row_count") or 0))
                unit = ((second[0] - first[0]) / line.length,
                        (second[1] - first[1]) / line.length)
                normal = (-unit[1], unit[0])
                canonical_first = (
                    first[0] + normal[0] * wall_midpoint_offset,
                    first[1] + normal[1] * wall_midpoint_offset)
                canonical_second = (
                    second[0] + normal[0] * wall_midpoint_offset,
                    second[1] + normal[1] * wall_midpoint_offset)
                canonical_cad_first = (
                    cad_first[0] + normal[0] * wall_midpoint_offset,
                    cad_first[1] + normal[1] * wall_midpoint_offset)
                canonical_cad_second = (
                    cad_second[0] + normal[0] * wall_midpoint_offset,
                    cad_second[1] + normal[1] * wall_midpoint_offset)
                canonical_endpoint_mask_distances = [
                    float(wall_mask.distance(Point(point)))
                    for point in (canonical_first, canonical_second)]
                if max(canonical_endpoint_mask_distances) > .15 + 1e-9:
                    continue
                candidate_id = str(candidate.get("candidate_id") or "window")
                host_id = (
                    f"cad_wall_root_frame_opening_host_{candidate_id}"
                    if is_root_frame else
                    f"cad_wall_sparse_frame_opening_host_{candidate_id}")
                canonical_line = LineString([canonical_first, canonical_second])
                footprint = canonical_line.buffer(
                    supported_wall_thickness / 2, cap_style=2, join_style=2)
                source_entities = [{
                    "entity_index": int(value), "segment_index": 0,
                    "handle": handles[0],
                    "root_handle": str(candidate.get("source_root_handle")
                                       or handles[0]),
                    "source_handle": handles[0], "layer": "", "block": "",
                    "insert_chain": [],
                    "source_segment_m": [list(canonical_cad_first),
                                         list(canonical_cad_second)],
                    "model_segment_m": [list(canonical_first),
                                        list(canonical_second)],
                    "cad_provenance": {"source_handles": handles},
                } for value in (candidate.get("source_entity_indexes") or [0])]
                proof = {
                    "method": ("cad_root_window_frame_wall_face_host_v1"
                               if is_root_frame else
                               "cad_sparse_window_frame_wall_face_host_v1"),
                    "kind": "window", "candidate_id": candidate_id,
                    "opening_source_handles": handles,
                    "original_frame_axis_cad_m": [list(cad_first), list(cad_second)],
                    "opening_axis_cad_m": [list(canonical_cad_first),
                                           list(canonical_cad_second)],
                    "opening_width_m": round(canonical_line.length, 8),
                    "supported_wall_face_span_m": round(supported_wall_thickness, 8),
                    "wall_band_midpoint_offset_m": round(wall_midpoint_offset, 8),
                    "signed_wall_face_offsets_m": [round(value, 8)
                                                    for value in signed_offsets],
                    "negative_wall_face_support_count": support_negative_count,
                    "positive_wall_face_support_count": support_positive_count,
                    "source_row_count": source_row_count,
                    "long_rail_count": long_rail_count,
                    "cross_member_count": cross_member_count,
                    "frame_short_span_m": round(root_frame_short_span, 8)
                    if is_root_frame else None,
                    "interior_wall_overlap_ratio": round(interior_overlap, 8),
                    "wall_endpoint_support_distance_m": [round(value, 8)
                                                           for value in endpoint_support],
                    "wall_mask_endpoint_distance_m": [round(value, 8)
                                                        for value in endpoint_mask_distances],
                    "canonical_wall_mask_endpoint_distance_m": [round(value, 8)
                            for value in canonical_endpoint_mask_distances],
                    "thresholds": {
                        "minimum_source_handle_count": 1 if is_root_frame else 2,
                        "minimum_source_row_count": 4 if is_root_frame else 2,
                        "maximum_source_row_count": 64 if is_root_frame else 3,
                        "minimum_long_rail_count": 2,
                        "minimum_wall_face_support_per_side": 2,
                        "maximum_interior_wall_overlap_ratio": .20,
                        "maximum_wall_endpoint_support_distance_m": (
                            .12 if is_root_frame else .09),
                        "maximum_wall_mask_endpoint_distance_m": .15,
                        "maximum_wall_band_midpoint_offset_m": .08,
                        "minimum_supported_wall_face_span_m": .06,
                        "maximum_supported_wall_face_span_m": .60,
                    },
                    "decision_basis": [
                        ("single_root_expanded_window_frame_source_geometry"
                         if is_root_frame else
                         "sparse_parallel_window_frame_source_geometry"),
                        "two_repeated_source_wall_faces_on_each_side",
                        "wall_thickness_measured_from_structural_face_span",
                        "frame_rail_spacing_not_used_as_wall_thickness",
                        "canonical_axis_shifted_to_measured_wall_band_midpoint",
                        "both_opening_endpoints_adjacent_to_global_wall_mask",
                    ],
                }
                result_assemblies.append({
                    "id": host_id,
                    "source_representation": "frame_geometry_opening_host",
                    "resolved_as": "centerline",
                    "start": {"x": round(canonical_first[0], 8),
                              "z": round(canonical_first[1], 8)},
                    "end": {"x": round(canonical_second[0], 8),
                            "z": round(canonical_second[1], 8)},
                    "centerline": [[round(value, 8) for value in canonical_first],
                                   [round(value, 8) for value in canonical_second]],
                    "opening_axis": [[round(value, 8) for value in canonical_first],
                                     [round(value, 8) for value in canonical_second]],
                    "length_m": round(canonical_line.length, 8),
                    "thickness_m": round(supported_wall_thickness, 8),
                    "thickness_source": (
                        "cad_root_frame_supported_wall_face_span"
                        if is_root_frame else
                        "cad_sparse_frame_supported_wall_face_span"),
                    "height_m": 2.8,
                    "height_source": "project_default_assumption",
                    "footprint_polygon": [[round(float(x), 8), round(float(z), 8)]
                                          for x, z in list(
                                              footprint.exterior.coords)[:-1]],
                    "boundary_kind": "frame_geometry_opening_host",
                    "kind": "interior", "source": "cad",
                    "review_status": "accepted", "confidence_grade": "A",
                    "confidence": 1.0, "legacy_wall_compatible": True,
                    "frame_geometry_opening_evidence": proof,
                    "source_entity_handles": handles,
                    "source_root_handles": ([str(candidate.get(
                        "source_root_handle"))] if candidate.get(
                            "source_root_handle") else handles[:1]),
                    "source_layers": [], "source_insert_chains": [],
                    "source_entities": source_entities,
                    "cad_provenance": {
                        "wall_assembly_source_representation":
                            "frame_geometry_opening_host",
                        "handle": handles[0], "source_handle": handles[0],
                        "root_handle": str(candidate.get("source_root_handle")
                                           or handles[0]),
                        "source_segment_m": [list(canonical_cad_first),
                                             list(canonical_cad_second)],
                        "source_entities": copy.deepcopy(source_entities),
                    },
                })
                candidate.update({
                    "status": "accepted", "wall_assembly_id": host_id,
                    "axis_segment_cad_m": [list(canonical_cad_first),
                                           list(canonical_cad_second)],
                    "center_cad_m": [round(
                        (canonical_cad_first[0] + canonical_cad_second[0]) / 2, 8),
                        round((canonical_cad_first[1]
                               + canonical_cad_second[1]) / 2, 8)],
                    "wall_source_handles": handles,
                })
                candidate["reason_codes"] = sorted(set(
                    reason for reason in (
                        (candidate.get("reason_codes") or [])
                        + ["canonical_root_window_frame_host_bound"
                           if is_root_frame else
                           "canonical_sparse_window_frame_host_bound"])
                    if reason != "opening_wall_assembly_unresolved"))
                candidate.setdefault("evidence_geometry", {})[
                    "canonical_binding_axis_source"] = \
                    ("root_frame_measured_wall_face_midpoint"
                     if is_root_frame else
                     "sparse_frame_measured_wall_face_midpoint")
                continue

            repeated_window_match = None
            if (not strict_frame_ready
                    and str(candidate.get("kind") or "") == "window"
                    and frame.get("grouping_method")
                    == "loose_maximal_parallel_rail_pair"
                    and frame.get("opposite_wall_face_support") is True
                    and long_rail_count >= 3 and cross_member_count >= 2
                    and len(handles) >= 4 and len(endpoint_support) == 2
                    and max(endpoint_mask_distances) <= .05 + 1e-9
                    and .40 <= line.length <= 3.0
                    and abs(line.length - observed_width)
                    <= max(.02, observed_width * .02) + 1e-9
                    and .06 <= frame_thickness <= .60):
                repeated_matches = []
                current_unit = ((second[0] - first[0]) / line.length,
                                (second[1] - first[1]) / line.length)
                current_angle = math.degrees(math.atan2(
                    current_unit[1], current_unit[0])) % 180.0
                for reference in result_candidates:
                    if (reference is candidate
                            or str(reference.get("status") or "")
                            not in {"accepted", "confirmed"}
                            or str(reference.get("kind") or "") != "window"):
                        continue
                    reference_frame = reference.get("evidence_geometry") or {}
                    try:
                        reference_raw_axis = reference.get("axis_segment_cad_m") or []
                        reference_cad_first = (
                            float(reference_raw_axis[0][0]),
                            float(reference_raw_axis[0][1]))
                        reference_cad_second = (
                            float(reference_raw_axis[-1][0]),
                            float(reference_raw_axis[-1][1]))
                        reference_first = (
                            reference_cad_first[0] - origin_x,
                            reference_cad_first[1] - origin_z)
                        reference_second = (
                            reference_cad_second[0] - origin_x,
                            reference_cad_second[1] - origin_z)
                        reference_line = LineString([
                            reference_first, reference_second])
                        reference_rail_separation = float(
                            reference_frame.get("seed_rail_separation_m"))
                        reference_width = float(reference.get("width_m"))
                    except (TypeError, ValueError, IndexError):
                        continue
                    if (reference_line.length < .40
                            or int(reference_frame.get("long_rail_count") or 0) < 2
                            or int(reference_frame.get("cross_member_count") or 0) < 2
                            or reference_frame.get("grouping_method")
                            != "loose_maximal_parallel_rail_pair"
                            or abs(reference_line.length - line.length) > .01 + 1e-9
                            or abs(reference_width - observed_width) > .01 + 1e-9
                            or abs(reference_rail_separation - frame_thickness)
                            > .01 + 1e-9):
                        continue
                    reference_angle = math.degrees(math.atan2(
                        reference_second[1] - reference_first[1],
                        reference_second[0] - reference_first[0])) % 180.0
                    angle_difference = abs(current_angle - reference_angle)
                    angle_difference = min(
                        angle_difference, 180.0 - angle_difference)
                    transverse_offsets = [abs(
                        (point[0] - first[0]) * current_unit[1]
                        - (point[1] - first[1]) * current_unit[0]
                    ) for point in (reference_first, reference_second)]
                    if angle_difference > 1.0 + 1e-9 \
                            or max(transverse_offsets) > .005 + 1e-9:
                        continue
                    current_interval = sorted(
                        (0.0, line.length))
                    reference_interval = sorted(
                        ((reference_first[0] - first[0]) * current_unit[0]
                         + (reference_first[1] - first[1]) * current_unit[1],
                         (reference_second[0] - first[0]) * current_unit[0]
                         + (reference_second[1] - first[1]) * current_unit[1]))
                    interval_gap = max(
                        0.0,
                        max(current_interval[0], reference_interval[0])
                        - min(current_interval[1], reference_interval[1]))
                    if not .10 <= interval_gap <= 2.0:
                        continue
                    reference_host_id = str(reference.get("wall_assembly_id") or "")
                    reference_hosts = [row for row in result_assemblies
                                       if str(row.get("id") or "") == reference_host_id
                                       and str(row.get("review_status") or "")
                                       in {"accepted", "confirmed"}]
                    if len(reference_hosts) != 1:
                        continue
                    reference_host = reference_hosts[0]
                    try:
                        reference_wall_thickness = float(
                            reference_host.get("thickness_m"))
                        reference_wall_height = float(
                            reference_host.get("height_m"))
                    except (TypeError, ValueError):
                        continue
                    if (not .06 <= reference_wall_thickness <= .60
                            or reference_wall_height <= 0
                            or max(endpoint_support)
                            > reference_wall_thickness + 1e-9):
                        continue
                    repeated_matches.append({
                        "reference_candidate": reference,
                        "reference_host": reference_host,
                        "reference_axis": [list(reference_cad_first),
                                           list(reference_cad_second)],
                        "axis_angle_difference_deg": angle_difference,
                        "axis_transverse_offset_m": max(transverse_offsets),
                        "opening_width_difference_m": abs(
                            reference_width - observed_width),
                        "frame_rail_separation_difference_m": abs(
                            reference_rail_separation - frame_thickness),
                        "axis_interval_gap_m": interval_gap,
                        "wall_thickness_m": reference_wall_thickness,
                        "wall_height_m": reference_wall_height,
                        "score": (max(transverse_offsets),
                                  abs(reference_width - observed_width),
                                  interval_gap, reference_host_id),
                    })
                repeated_matches.sort(key=lambda row: row["score"])
                if repeated_matches:
                    best_repeated = repeated_matches[0]
                    equally_plausible_hosts = {
                        str(row["reference_host"].get("id") or "")
                        for row in repeated_matches
                        if abs(float(row["score"][0])
                               - float(best_repeated["score"][0])) <= .002 + 1e-9
                        and abs(float(row["score"][1])
                                - float(best_repeated["score"][1])) <= .002 + 1e-9
                    }
                    if len(equally_plausible_hosts) == 1:
                        repeated_window_match = best_repeated

            if repeated_window_match is not None:
                candidate_id = str(candidate.get("candidate_id") or "window")
                host_id = f"cad_wall_repeated_window_host_{candidate_id}"
                host_thickness = float(repeated_window_match["wall_thickness_m"])
                footprint = line.buffer(
                    host_thickness / 2, cap_style=2, join_style=2)
                reference_candidate = repeated_window_match[
                    "reference_candidate"]
                reference_host = repeated_window_match["reference_host"]
                source_entities = [{
                    "entity_index": int(value), "segment_index": 0,
                    "handle": handles[0],
                    "root_handle": str(candidate.get("source_root_handle")
                                       or handles[0]),
                    "source_handle": handles[0], "layer": "", "block": "",
                    "insert_chain": [],
                    "source_segment_m": [list(cad_first), list(cad_second)],
                    "model_segment_m": [list(first), list(second)],
                    "cad_provenance": {"source_handles": handles},
                } for value in (candidate.get("source_entity_indexes") or [0])]
                proof = {
                    "method": "cad_repeated_collinear_window_frame_host_v1",
                    "kind": "window", "candidate_id": candidate_id,
                    "opening_source_handles": handles,
                    "opening_axis_cad_m": [list(cad_first), list(cad_second)],
                    "opening_width_m": round(line.length, 8),
                    "frame_rail_separation_m": round(frame_thickness, 8),
                    "long_rail_count": long_rail_count,
                    "cross_member_count": cross_member_count,
                    "wall_mask_endpoint_distance_m": [round(value, 8)
                                                        for value in endpoint_mask_distances],
                    "wall_endpoint_support_distance_m": [round(value, 8)
                                                           for value in endpoint_support],
                    "reference_candidate_id": str(
                        reference_candidate.get("candidate_id") or ""),
                    "reference_wall_assembly_id": str(
                        reference_host.get("id") or ""),
                    "reference_opening_source_handles": sorted(set(
                        str(value) for value in
                        reference_candidate.get("source_handles") or [] if str(value))),
                    "reference_axis_cad_m": copy.deepcopy(
                        repeated_window_match["reference_axis"]),
                    "reference_wall_thickness_m": round(host_thickness, 8),
                    "axis_angle_difference_deg": round(
                        repeated_window_match["axis_angle_difference_deg"], 8),
                    "axis_transverse_offset_m": round(
                        repeated_window_match["axis_transverse_offset_m"], 8),
                    "opening_width_difference_m": round(
                        repeated_window_match["opening_width_difference_m"], 8),
                    "frame_rail_separation_difference_m": round(
                        repeated_window_match[
                            "frame_rail_separation_difference_m"], 8),
                    "axis_interval_gap_m": round(
                        repeated_window_match["axis_interval_gap_m"], 8),
                    "thresholds": {
                        "minimum_opening_source_handle_count": 4,
                        "minimum_long_rail_count": 3,
                        "minimum_cross_member_count": 2,
                        "maximum_wall_mask_endpoint_distance_m": .05,
                        "maximum_axis_angle_difference_deg": 1.0,
                        "maximum_axis_transverse_offset_m": .005,
                        "maximum_opening_width_difference_m": .01,
                        "maximum_frame_rail_separation_difference_m": .01,
                        "minimum_axis_interval_gap_m": .10,
                        "maximum_axis_interval_gap_m": 2.0,
                    },
                    "decision_basis": [
                        "complete_source_window_frame_geometry",
                        "unique_accepted_collinear_reference_window",
                        "matching_window_width_and_frame_rail_pattern",
                        "reference_wall_assembly_supplies_measured_host_thickness",
                    ],
                }
                result_assemblies.append({
                    "id": host_id,
                    "source_representation": "repeated_window_frame_opening_host",
                    "resolved_as": "centerline",
                    "start": {"x": round(first[0], 8), "z": round(first[1], 8)},
                    "end": {"x": round(second[0], 8), "z": round(second[1], 8)},
                    "centerline": [[round(value, 8) for value in first],
                                   [round(value, 8) for value in second]],
                    "opening_axis": [[round(value, 8) for value in first],
                                     [round(value, 8) for value in second]],
                    "length_m": round(line.length, 8),
                    "thickness_m": round(host_thickness, 8),
                    "thickness_source": "matched_repeated_window_wall_assembly",
                    "height_m": round(float(
                        repeated_window_match["wall_height_m"]), 8),
                    "height_source": "matched_reference_window_wall_assembly",
                    "footprint_polygon": [[round(float(x), 8), round(float(z), 8)]
                                          for x, z in list(
                                              footprint.exterior.coords)[:-1]],
                    "boundary_kind": "repeated_window_frame_opening_host",
                    "kind": str(reference_host.get("kind") or "exterior"),
                    "source": "cad", "review_status": "accepted",
                    "confidence_grade": "A", "confidence": 1.0,
                    "legacy_wall_compatible": True,
                    "repeated_window_frame_opening_evidence": proof,
                    "source_entity_handles": handles,
                    "source_root_handles": ([str(candidate.get(
                        "source_root_handle"))] if candidate.get(
                            "source_root_handle") else handles[:1]),
                    "source_layers": [], "source_insert_chains": [],
                    "source_entities": source_entities,
                    "cad_provenance": {
                        "wall_assembly_source_representation":
                            "repeated_window_frame_opening_host",
                        "handle": handles[0], "source_handle": handles[0],
                        "root_handle": str(candidate.get("source_root_handle")
                                           or handles[0]),
                        "source_segment_m": [list(cad_first), list(cad_second)],
                        "source_entities": copy.deepcopy(source_entities),
                    },
                })
                candidate.update({
                    "status": "accepted", "wall_assembly_id": host_id,
                    "axis_segment_cad_m": [list(cad_first), list(cad_second)],
                    "center_cad_m": [round((cad_first[0] + cad_second[0]) / 2, 8),
                                     round((cad_first[1] + cad_second[1]) / 2, 8)],
                    "wall_source_handles": sorted(set(
                        str(value) for value in
                        reference_host.get("source_entity_handles") or []
                        if str(value))),
                })
                candidate["reason_codes"] = sorted(set(
                    reason for reason in (
                        (candidate.get("reason_codes") or [])
                        + ["canonical_repeated_window_frame_host_bound"])
                    if reason != "opening_wall_assembly_unresolved"))
                candidate.setdefault("evidence_geometry", {})[
                    "canonical_binding_axis_source"] = \
                    "unique_repeated_collinear_window_frame"
                continue
            if (str(candidate.get("kind") or "") != "window"
                    or frame.get("grouping_method")
                    != "loose_maximal_parallel_rail_pair"
                    or frame.get("opposite_wall_face_support") is not True
                    or long_rail_count < 2 or cross_member_count < 2
                    or len(handles) < 4 or len(endpoint_support) != 2
                    or not any(value < -.02 for value in signed_offsets)
                    or not any(value > .02 for value in signed_offsets)
                    or max(endpoint_support) > .12 + 1e-9
                    or max(endpoint_mask_distances) > .15 + 1e-9
                    or interior_overlap < .90 - 1e-9
                    or not .40 <= line.length <= 3.0
                    or abs(line.length - observed_width)
                    > max(.02, observed_width * .02) + 1e-9
                    or not .06 <= frame_thickness <= .60
                    or not .06 <= offset_span <= .60
                    or abs(offset_span - frame_thickness) > .03 + 1e-9):
                continue

            candidate_id = str(candidate.get("candidate_id") or "window")
            host_id = f"cad_wall_frame_opening_host_{candidate_id}"
            footprint = line.buffer(
                frame_thickness / 2, cap_style=2, join_style=2)
            source_entities = [{
                "entity_index": int(value), "segment_index": 0,
                "handle": handles[0],
                "root_handle": str(candidate.get("source_root_handle") or handles[0]),
                "source_handle": handles[0], "layer": "", "block": "",
                "insert_chain": [],
                "source_segment_m": [list(cad_first), list(cad_second)],
                "model_segment_m": [list(first), list(second)],
                "cad_provenance": {"source_handles": handles},
            } for value in (candidate.get("source_entity_indexes") or [0])]
            proof = {
                "method": "cad_window_frame_measured_host_v1",
                "kind": "window", "candidate_id": candidate_id,
                "opening_source_handles": handles,
                "opening_axis_cad_m": [list(cad_first), list(cad_second)],
                "opening_width_m": round(line.length, 8),
                "frame_rail_separation_m": round(frame_thickness, 8),
                "signed_wall_face_offsets_m": [round(value, 8)
                                                for value in signed_offsets],
                "long_rail_count": long_rail_count,
                "cross_member_count": cross_member_count,
                "interior_wall_overlap_ratio": round(interior_overlap, 8),
                "wall_endpoint_support_distance_m": [round(value, 8)
                                                       for value in endpoint_support],
                "wall_mask_endpoint_distance_m": [round(value, 8)
                                                    for value in endpoint_mask_distances],
                "thresholds": {
                    "minimum_source_handle_count": 4,
                    "minimum_long_rail_count": 2,
                    "minimum_cross_member_count": 2,
                    "minimum_interior_wall_overlap_ratio": .90,
                    "maximum_wall_endpoint_support_distance_m": .12,
                    "maximum_wall_mask_endpoint_distance_m": .15,
                    "maximum_rail_to_face_span_delta_m": .03,
                },
                "decision_basis": [
                    "opposing_parallel_window_frame_rails",
                    "two_source_backed_window_jambs",
                    "opposite_wall_face_support",
                    "both_opening_endpoints_adjacent_to_global_wall_mask",
                ],
            }
            result_assemblies.append({
                "id": host_id,
                "source_representation": "frame_geometry_opening_host",
                "resolved_as": "centerline",
                "start": {"x": round(first[0], 8), "z": round(first[1], 8)},
                "end": {"x": round(second[0], 8), "z": round(second[1], 8)},
                "centerline": [[round(value, 8) for value in first],
                               [round(value, 8) for value in second]],
                "opening_axis": [[round(value, 8) for value in first],
                                 [round(value, 8) for value in second]],
                "length_m": round(line.length, 8),
                "thickness_m": round(frame_thickness, 8),
                "thickness_source": "cad_window_frame_rail_spacing",
                "height_m": 2.8,
                "height_source": "project_default_assumption",
                "footprint_polygon": [
                    [round(float(x), 8), round(float(z), 8)]
                    for x, z in list(footprint.exterior.coords)[:-1]],
                "boundary_kind": "frame_geometry_opening_host",
                "kind": "exterior", "source": "cad",
                "review_status": "accepted", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": True,
                "frame_geometry_opening_evidence": proof,
                "source_entity_handles": handles,
                "source_root_handles": ([str(candidate.get("source_root_handle"))]
                                        if candidate.get("source_root_handle") else handles[:1]),
                "source_layers": [], "source_insert_chains": [],
                "source_entities": source_entities,
                "cad_provenance": {
                    "wall_assembly_source_representation":
                        "frame_geometry_opening_host",
                    "handle": handles[0], "source_handle": handles[0],
                    "root_handle": str(candidate.get("source_root_handle") or handles[0]),
                    "source_segment_m": [list(cad_first), list(cad_second)],
                    "source_entities": copy.deepcopy(source_entities),
                },
            })
            candidate.update({
                "status": "accepted", "wall_assembly_id": host_id,
                "axis_segment_cad_m": [list(cad_first), list(cad_second)],
                "center_cad_m": [round((cad_first[0] + cad_second[0]) / 2, 8),
                                 round((cad_first[1] + cad_second[1]) / 2, 8)],
                "wall_source_handles": handles,
            })
            candidate["reason_codes"] = sorted(set(
                reason for reason in (
                    (candidate.get("reason_codes") or [])
                    + ["canonical_window_frame_geometry_host_bound"])
                if reason != "opening_wall_assembly_unresolved"))
            candidate.setdefault("evidence_geometry", {})[
                "canonical_binding_axis_source"] = "measured_window_frame_geometry"
            continue
        matches.sort(key=lambda row: (-row["coverage"],
                                      abs(row["thickness"] - .20), row["source"]))
        best = matches[0]
        if (len(matches) > 1
                and abs(matches[1]["coverage"] - best["coverage"]) <= .01
                and abs(matches[1]["thickness"] - best["thickness"]) <= .01
                and matches[1]["first"] != best["first"]):
            continue
        candidate_id = str(candidate.get("candidate_id") or "opening")
        host_id = f"cad_wall_global_opening_host_{candidate_id}"
        line = LineString([best["first"], best["second"]])
        footprint = line.buffer(best["thickness"] / 2, cap_style=2, join_style=2)
        handles = sorted({str(value) for value in candidate.get("source_handles") or []
                          if str(value)})
        source_entities = [{
            "entity_index": int(value), "segment_index": 0,
            "handle": handles[0] if handles else candidate_id,
            "root_handle": str(candidate.get("source_root_handle") or ""),
            "source_handle": handles[0] if handles else candidate_id,
            "layer": "", "block": "", "insert_chain": [],
            "source_segment_m": copy.deepcopy(best["cad_axis"]),
            "model_segment_m": [list(best["first"]), list(best["second"])],
            "cad_provenance": {"source_handles": handles},
        } for value in (candidate.get("source_entity_indexes") or [0])]
        host = {
            "id": host_id,
            "source_representation": "global_topology_opening_host",
            "resolved_as": "centerline",
            "start": {"x": round(best["first"][0], 8),
                      "z": round(best["first"][1], 8)},
            "end": {"x": round(best["second"][0], 8),
                    "z": round(best["second"][1], 8)},
            "centerline": [[round(value, 8) for value in best["first"]],
                           [round(value, 8) for value in best["second"]]],
            "opening_axis": [[round(value, 8) for value in best["first"]],
                             [round(value, 8) for value in best["second"]]],
            "length_m": round(line.length, 8),
            "thickness_m": round(best["thickness"], 8),
            "thickness_source": "global_wall_mask_cross_section",
            "height_m": 2.8, "height_source": "project_default_assumption",
            "footprint_polygon": [[round(float(x), 8), round(float(z), 8)]
                                  for x, z in list(footprint.exterior.coords)[:-1]],
            "boundary_kind": "global_topology_opening_host",
            "kind": "interior", "source": "cad",
            "review_status": "accepted", "confidence_grade": "A",
            "confidence": 1.0, "legacy_wall_compatible": True,
            "global_topology_opening_evidence": {
                "candidate_id": candidate_id,
                "opening_axis_cad_m": copy.deepcopy(best["cad_axis"]),
                "wall_mask_axis_coverage_ratio": round(best["coverage"], 8),
                "wall_cross_section_thickness_m": round(best["thickness"], 8),
                "selected_axis_source": best["source"],
                "source_handles": handles,
                "thresholds": {"min_axis_coverage_ratio": .90,
                               "min_wall_thickness_m": .06,
                               "max_wall_thickness_m": .60},
            },
            "source_entity_handles": handles or [candidate_id],
            "source_root_handles": ([str(candidate.get("source_root_handle"))]
                                    if candidate.get("source_root_handle") else []),
            "source_layers": [], "source_insert_chains": [],
            "source_entities": source_entities,
            "cad_provenance": {
                "wall_assembly_source_representation":
                    "global_topology_opening_host",
                "handle": handles[0] if handles else candidate_id,
                "source_handle": handles[0] if handles else candidate_id,
                "root_handle": str(candidate.get("source_root_handle") or
                                   (handles[0] if handles else candidate_id)),
                "source_segment_m": copy.deepcopy(best["cad_axis"]),
                "source_entities": copy.deepcopy(source_entities),
            },
        }
        result_assemblies.append(host)
        candidate.update({
            "status": "accepted", "wall_assembly_id": host_id,
            "axis_segment_cad_m": copy.deepcopy(best["cad_axis"]),
            "center_cad_m": [round((best["cad_axis"][0][0]
                                     + best["cad_axis"][1][0]) / 2, 8),
                             round((best["cad_axis"][0][1]
                                     + best["cad_axis"][1][1]) / 2, 8)],
            "wall_source_handles": handles,
        })
        candidate["reason_codes"] = sorted(set(
            reason for reason in (
                (candidate.get("reason_codes") or [])
                + ["canonical_global_wall_mask_axis_bound"])
            if reason != "opening_wall_assembly_unresolved"))
        candidate.setdefault("evidence_geometry", {})[
            "canonical_binding_axis_source"] = best["source"]
    return result_candidates, result_assemblies


def _infer_labeled_terminal_open_connections(
    rows: Sequence[Mapping[str, Any]], assemblies: list[dict],
    candidates: list[dict], text_anchors: Sequence[Mapping[str, Any]], *,
    origin_x: float, origin_z: float,
) -> tuple[list[dict], list[dict], dict]:
    """Prove an undrawn open connection at one measured wall terminal.

    Architectural plans sometimes leave a passage completely blank: one wall
    stops, a transverse wall provides the far jamb, and there is no door leaf,
    swing arc, threshold or block to classify.  That negative-space convention
    is useful topology evidence only when it closes exactly one independently
    labelled enclosed space.  This routine therefore requires all of:

    * two accepted, source-backed wall assemblies with matching measured
      thickness and a unique near-perpendicular terminal relationship;
    * a 0.35--1.50 m clear, unobstructed continuation of the terminating wall;
    * a storage anchor on the approach side and a kitchen anchor on the far
      side of the transverse wall; and
    * a deterministic topology replay where the proposed axis adds exactly one
      face, containing only the storage semantic anchor.

    The accepted host is a compatibility owner for an opening cut.  Its axis is
    also a room-discovery barrier, but it is not merged into the rendered global
    wall footprint.
    """
    try:
        from shapely.geometry import LineString, Point, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:
        return copy.deepcopy(candidates), copy.deepcopy(assemblies), {
            "schema_version": 1,
            "method": "cad_labeled_terminal_open_connection_v1",
            "status": "unavailable",
            "proved_count": 0,
            "reason": "shapely_unavailable",
        }

    result_candidates = copy.deepcopy(candidates)
    result_assemblies = copy.deepcopy(assemblies)
    structural_representations = {
        "paired_faces", "closed_footprint", "centerline",
        "collinear_face_continuation",
    }
    supports: list[dict] = []
    for assembly in result_assemblies:
        if (str(assembly.get("review_status") or "")
                not in {"accepted", "confirmed"}
                or str(assembly.get("source_representation") or "")
                not in structural_representations):
            continue
        try:
            centerline = assembly.get("centerline") or []
            axis = LineString([
                (float(centerline[0][0]), float(centerline[0][1])),
                (float(centerline[-1][0]), float(centerline[-1][1])),
            ])
            footprint = Polygon([
                (float(point[0]), float(point[1]))
                for point in assembly.get("footprint_polygon") or []
            ])
            thickness = float(assembly.get("thickness_m"))
        except (TypeError, ValueError, IndexError):
            continue
        if (axis.length < .20 or not footprint.is_valid or footprint.is_empty
                or not .06 <= thickness <= .60):
            continue
        handles = sorted({
            str(value) for value in assembly.get("source_entity_handles") or []
            if str(value)
        })
        if not handles:
            continue
        supports.append({
            "assembly": assembly, "axis": axis, "footprint": footprint,
            "thickness": thickness, "handles": handles,
        })

    semantic_anchors = []
    for anchor in text_anchors:
        profile = str(anchor.get("semantic_profile") or "")
        point_m = anchor.get("point_m") or []
        try:
            cad_point = Point(float(point_m[0]), float(point_m[1]))
            point = Point(cad_point.x - origin_x, cad_point.y - origin_z)
        except (TypeError, ValueError, IndexError):
            continue
        if profile:
            semantic_anchors.append({
                "anchor": anchor, "profile": profile, "point": point,
                "cad_point": cad_point,
            })
    storage_anchors = [row for row in semantic_anchors
                       if row["profile"] == "storage"]
    kitchen_anchors = [row for row in semantic_anchors
                       if row["profile"] == "kitchen"]
    if not supports or not storage_anchors or not kitchen_anchors:
        return result_candidates, result_assemblies, {
            "schema_version": 1,
            "method": "cad_labeled_terminal_open_connection_v1",
            "status": "unresolved", "proved_count": 0,
            "proposal_count": 0,
            "reason": "required_structural_or_semantic_evidence_missing",
        }

    geometric_proposals: list[dict] = []
    geometric_gap_audit: list[dict] = []
    for terminal_support in supports:
        axis_coords = list(terminal_support["axis"].coords)
        for endpoint_index, (endpoint, previous) in enumerate((
                (axis_coords[0], axis_coords[-1]),
                (axis_coords[-1], axis_coords[0]))):
            length = math.dist(endpoint, previous)
            direction = ((endpoint[0] - previous[0]) / length,
                         (endpoint[1] - previous[1]) / length)
            ray = LineString([
                endpoint,
                (endpoint[0] + direction[0] * 1.80,
                 endpoint[1] + direction[1] * 1.80),
            ])
            far_supports = []
            for transverse_support in supports:
                if transverse_support is terminal_support:
                    continue
                transverse_coords = list(transverse_support["axis"].coords)
                transverse_vector = (
                    transverse_coords[-1][0] - transverse_coords[0][0],
                    transverse_coords[-1][1] - transverse_coords[0][1],
                )
                transverse_length = math.hypot(*transverse_vector)
                if transverse_length <= 1e-9:
                    continue
                parallel_dot = abs(
                    direction[0] * transverse_vector[0]
                    + direction[1] * transverse_vector[1]) / transverse_length
                angle_difference = math.degrees(math.acos(max(
                    -1.0, min(1.0, parallel_dot))))
                if angle_difference < 89.0 - 1e-9:
                    continue
                intersection = ray.intersection(transverse_support["footprint"])
                if intersection.is_empty:
                    continue
                intersection_points = []
                for geometry in getattr(intersection, "geoms", [intersection]):
                    if hasattr(geometry, "coords"):
                        intersection_points.extend(list(geometry.coords))
                    elif getattr(geometry, "geom_type", "") == "Point":
                        intersection_points.append((geometry.x, geometry.y))
                if not intersection_points:
                    continue
                entry_distance = min(ray.project(Point(point))
                                     for point in intersection_points)
                center_intersection = ray.intersection(transverse_support["axis"])
                if center_intersection.is_empty:
                    center_distance = entry_distance + transverse_support[
                        "thickness"] / 2
                else:
                    center_points = []
                    for geometry in getattr(
                            center_intersection, "geoms", [center_intersection]):
                        if getattr(geometry, "geom_type", "") == "Point":
                            center_points.append((geometry.x, geometry.y))
                        elif hasattr(geometry, "coords"):
                            center_points.extend(list(geometry.coords))
                    center_distance = min(
                        (ray.project(Point(point)) for point in center_points),
                        default=entry_distance + transverse_support["thickness"] / 2,
                    )
                clear_gap = entry_distance
                if (not .35 <= clear_gap <= 1.50
                        or not .35 <= center_distance <= 1.80
                        or abs(terminal_support["thickness"]
                               - transverse_support["thickness"]) > .04 + 1e-9):
                    continue
                audit_row = {
                    "terminal_wall_assembly_id": str(
                        terminal_support["assembly"].get("id") or ""),
                    "transverse_wall_assembly_id": str(
                        transverse_support["assembly"].get("id") or ""),
                    "opening_axis_model_m": [
                        [round(endpoint[0], 8), round(endpoint[1], 8)],
                        [round(endpoint[0] + direction[0] * center_distance, 8),
                         round(endpoint[1] + direction[1] * center_distance, 8)],
                    ],
                    "clear_gap_width_m": round(clear_gap, 8),
                    "terminal_axis_extension_m": round(center_distance, 8),
                    "terminal_transverse_angle_deg": round(
                        angle_difference, 8),
                }
                clear_line = LineString([
                    (endpoint[0] + direction[0] * .015,
                     endpoint[1] + direction[1] * .015),
                    (endpoint[0] + direction[0] * max(.015, entry_distance - .015),
                     endpoint[1] + direction[1] * max(.015, entry_distance - .015)),
                ])
                other_mask_parts = [
                    support["footprint"] for support in supports
                    if (support is not terminal_support
                        and support is not transverse_support)
                ]
                obstructed_length = 0.0
                if other_mask_parts:
                    obstruction = unary_union(other_mask_parts).buffer(.005)
                    obstructed_length = float(clear_line.intersection(
                        obstruction).length)
                if obstructed_length > .01 + 1e-9:
                    audit_row.update(
                        disposition="rejected",
                        reason="intermediate_wall_obstruction",
                        intermediate_wall_coverage_m=round(
                            obstructed_length, 8),
                    )
                    geometric_gap_audit.append(audit_row)
                    continue

                transverse_first = transverse_coords[0]
                transverse_unit = (
                    transverse_vector[0] / transverse_length,
                    transverse_vector[1] / transverse_length,
                )

                def signed_side(point: Point) -> float:
                    return (transverse_unit[0] * (point.y - transverse_first[1])
                            - transverse_unit[1] * (point.x - transverse_first[0]))

                approach = Point(
                    endpoint[0] + direction[0] * max(.01, clear_gap / 2),
                    endpoint[1] + direction[1] * max(.01, clear_gap / 2),
                )
                approach_side = signed_side(approach)
                if abs(approach_side) <= transverse_support["thickness"] / 2:
                    continue
                semantic_pairs = []
                for storage in storage_anchors:
                    storage_side = signed_side(storage["point"])
                    storage_vector = (
                        storage["point"].x - endpoint[0],
                        storage["point"].y - endpoint[1],
                    )
                    storage_axis_projection = (
                        storage_vector[0] * direction[0]
                        + storage_vector[1] * direction[1]
                    )
                    if (storage_side * approach_side <= 0
                            or storage["point"].distance(
                                transverse_support["axis"]) > 2.50
                            or not -.20 <= storage_axis_projection
                            <= center_distance + .20):
                        continue
                    for kitchen in kitchen_anchors:
                        kitchen_side = signed_side(kitchen["point"])
                        if (storage_side * kitchen_side >= 0
                                or kitchen["point"].distance(
                                    transverse_support["axis"]) > 3.00):
                            continue
                        semantic_pairs.append((storage, kitchen))
                if len(semantic_pairs) != 1:
                    audit_row.update(
                        disposition="rejected",
                        reason="unique_storage_kitchen_anchor_pair_missing",
                        semantic_pair_count=len(semantic_pairs),
                    )
                    geometric_gap_audit.append(audit_row)
                    continue
                storage, kitchen = semantic_pairs[0]
                far_supports.append({
                    "terminal": terminal_support,
                    "transverse": transverse_support,
                    "endpoint_index": endpoint_index,
                    "endpoint": endpoint,
                    "direction": direction,
                    "clear_gap": clear_gap,
                    "center_distance": center_distance,
                    "angle_difference": angle_difference,
                    "obstructed_length": obstructed_length,
                    "storage": storage, "kitchen": kitchen,
                })
                audit_row.update(
                    disposition="semantic_pair_proved",
                    storage_anchor_id=str(
                        storage["anchor"].get("anchor_id") or ""),
                    kitchen_anchor_id=str(
                        kitchen["anchor"].get("anchor_id") or ""),
                )
                geometric_gap_audit.append(audit_row)
            if not far_supports:
                continue
            far_supports.sort(key=lambda row: row["center_distance"])
            best_distance = far_supports[0]["center_distance"]
            unique = [row for row in far_supports
                      if abs(row["center_distance"] - best_distance) <= .05]
            if len(unique) != 1:
                continue
            proposal = unique[0]
            proposal["unique_support_count"] = len(unique)
            proposal["next_support_margin_m"] = (
                far_supports[1]["center_distance"] - best_distance
                if len(far_supports) > 1 else None)
            geometric_proposals.append(proposal)

    if not geometric_proposals:
        return result_candidates, result_assemblies, {
            "schema_version": 1,
            "method": "cad_labeled_terminal_open_connection_v1",
            "status": "unresolved", "proved_count": 0,
            "proposal_count": 0,
            "reason": "no_unique_terminal_transverse_gap",
            "geometric_gap_audit": geometric_gap_audit[:100],
        }

    # Deduplicate the same physical gap discovered from equivalent accepted
    # wall representations before the topology replay.
    deduplicated: dict[tuple[Any, ...], dict] = {}
    for proposal in geometric_proposals:
        endpoint = proposal["endpoint"]
        direction = proposal["direction"]
        second = (
            endpoint[0] + direction[0] * proposal["center_distance"],
            endpoint[1] + direction[1] * proposal["center_distance"],
        )
        key = tuple(sorted((
            (round(endpoint[0], 3), round(endpoint[1], 3)),
            (round(second[0], 3), round(second[1], 3)),
        )))
        current = deduplicated.get(key)
        if current is None or len(proposal["terminal"]["handles"]) + len(
                proposal["transverse"]["handles"]) < len(
                    current["terminal"]["handles"]) + len(
                        current["transverse"]["handles"]):
            deduplicated[key] = proposal
    proposals = list(deduplicated.values())

    try:
        base_topology = build_global_wall_topology(
            rows, wall_assemblies=result_assemblies,
            opening_candidates=result_candidates,
            # Direct fine faces remain part of the physical replay, while the
            # newly added overlapping residual recovery is disabled: the
            # label under test must not create the base residual and then
            # self-validate the proposed open connection.
            semantic_anchors=text_anchors,
            enable_semantic_residual_supplements=False,
            origin_x=origin_x, origin_z=origin_z, wall_height_m=2.8)
    except GlobalTopologyError:
        return result_candidates, result_assemblies, {
            "schema_version": 1,
            "method": "cad_labeled_terminal_open_connection_v1",
            "status": "unresolved", "proved_count": 0,
            "proposal_count": len(proposals),
            "reason": "base_topology_unavailable",
        }
    base_spaces = base_topology.get("_space_polygons") or []
    proved: list[dict] = []
    rejected_proposals: list[dict] = []
    for proposal_index, proposal in enumerate(proposals, 1):
        endpoint = proposal["endpoint"]
        direction = proposal["direction"]
        second = (
            endpoint[0] + direction[0] * proposal["center_distance"],
            endpoint[1] + direction[1] * proposal["center_distance"],
        )
        cad_axis = [
            [round(endpoint[0] + origin_x, 8),
             round(endpoint[1] + origin_z, 8)],
            [round(second[0] + origin_x, 8),
             round(second[1] + origin_z, 8)],
        ]
        terminal = proposal["terminal"]
        transverse = proposal["transverse"]
        structural_handles = sorted(set(
            terminal["handles"] + transverse["handles"]))
        semantic_handles = sorted({
            str(((row["anchor"].get("cad_provenance") or {}).get(
                "source_handle") or ""))
            for row in (proposal["storage"], proposal["kitchen"])
            if str(((row["anchor"].get("cad_provenance") or {}).get(
                "source_handle") or ""))
        })
        candidate_id = (
            "cad_terminal_open_connection_"
            + hashlib.sha256(json.dumps(
                {"axis": cad_axis, "handles": structural_handles},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()[:12]
        )
        trial_candidate = {
            "candidate_id": candidate_id,
            "kind": "open_connection", "status": "accepted",
            "confidence": 1.0,
            "source_root_handle": structural_handles[0],
            "source_handles": structural_handles + semantic_handles,
            "source_entity_indexes": sorted({
                int(entity.get("entity_index"))
                for support in (terminal, transverse)
                for entity in support["assembly"].get("source_entities") or []
                if isinstance(entity, Mapping)
                and (isinstance(entity.get("entity_index"), int)
                     or str(entity.get("entity_index") or "").isdigit())
            }),
            "wall_source_handles": structural_handles,
            "width_m": round(proposal["clear_gap"], 8),
            "center_cad_m": [
                round((cad_axis[0][0] + cad_axis[1][0]) / 2, 8),
                round((cad_axis[0][1] + cad_axis[1][1]) / 2, 8),
            ],
            "axis_segment_cad_m": cad_axis,
            "reason_codes": [
                "labeled_terminal_open_connection",
                "unique_transverse_wall_terminal",
                "topology_single_space_closure_proved",
            ],
        }
        try:
            trial_topology = build_global_wall_topology(
                rows, wall_assemblies=result_assemblies,
                opening_candidates=[*result_candidates, trial_candidate],
                semantic_anchors=text_anchors,
                enable_semantic_residual_supplements=False,
                origin_x=origin_x, origin_z=origin_z, wall_height_m=2.8)
        except GlobalTopologyError:
            rejected_proposals.append({
                "proposal_index": proposal_index,
                "candidate_id": candidate_id,
                "reason": "trial_topology_unavailable",
                "opening_axis_cad_m": copy.deepcopy(cad_axis),
            })
            continue
        trial_spaces = trial_topology.get("_space_polygons") or []
        if len(trial_spaces) - len(base_spaces) != 1:
            rejected_proposals.append({
                "proposal_index": proposal_index,
                "candidate_id": candidate_id,
                "reason": "topology_space_count_delta_not_one",
                "opening_axis_cad_m": copy.deepcopy(cad_axis),
                "topology_space_count_before": len(base_spaces),
                "topology_space_count_after": len(trial_spaces),
            })
            continue
        storage_point = proposal["storage"]["cad_point"]
        base_storage_spaces = [space for space in base_spaces
                               if space.buffer(.005).covers(storage_point)]
        trial_storage_spaces = [space for space in trial_spaces
                                if space.buffer(.005).covers(storage_point)]
        if base_storage_spaces or len(trial_storage_spaces) != 1:
            rejected_proposals.append({
                "proposal_index": proposal_index,
                "candidate_id": candidate_id,
                "reason": "storage_anchor_face_transition_not_unique",
                "opening_axis_cad_m": copy.deepcopy(cad_axis),
                "base_storage_space_count": len(base_storage_spaces),
                "trial_storage_space_count": len(trial_storage_spaces),
            })
            continue
        storage_space = trial_storage_spaces[0]
        contained_semantics = [
            row for row in semantic_anchors
            if storage_space.buffer(.005).covers(row["cad_point"])
        ]
        if ([row["profile"] for row in contained_semantics] != ["storage"]
                or contained_semantics[0]["anchor"].get("anchor_id")
                != proposal["storage"]["anchor"].get("anchor_id")):
            rejected_proposals.append({
                "proposal_index": proposal_index,
                "candidate_id": candidate_id,
                "reason": "closed_face_semantics_not_storage_only",
                "opening_axis_cad_m": copy.deepcopy(cad_axis),
                "closed_face_semantic_anchors": [{
                    "anchor_id": str(row["anchor"].get("anchor_id") or ""),
                    "profile": row["profile"],
                } for row in contained_semantics],
            })
            continue
        thickness = (terminal["thickness"] + transverse["thickness"]) / 2
        footprint = LineString([endpoint, second]).buffer(
            thickness / 2, cap_style=2, join_style=2)
        source_entities = []
        for support in (terminal, transverse):
            source_entities.extend(copy.deepcopy(
                support["assembly"].get("source_entities") or []))
        proof = {
            "method": "cad_labeled_terminal_open_connection_v1",
            "candidate_id": candidate_id,
            "source_wall_assembly_ids": [
                str(terminal["assembly"].get("id") or ""),
                str(transverse["assembly"].get("id") or ""),
            ],
            "terminal_wall_assembly_id": str(
                terminal["assembly"].get("id") or ""),
            "transverse_wall_assembly_id": str(
                transverse["assembly"].get("id") or ""),
            "source_handles": structural_handles,
            "semantic_source_handles": semantic_handles,
            "opening_axis_cad_m": copy.deepcopy(cad_axis),
            "opening_axis_model_m": [list(endpoint), list(second)],
            "clear_gap_width_m": round(proposal["clear_gap"], 8),
            "terminal_axis_extension_m": round(
                proposal["center_distance"], 8),
            "terminal_transverse_angle_deg": round(
                proposal["angle_difference"], 8),
            "wall_thickness_samples_m": [
                round(terminal["thickness"], 8),
                round(transverse["thickness"], 8),
            ],
            "wall_thickness_spread_m": round(abs(
                terminal["thickness"] - transverse["thickness"]), 8),
            "intermediate_wall_coverage_m": round(
                proposal["obstructed_length"], 8),
            "unique_transverse_support_count": int(
                proposal["unique_support_count"]),
            "storage_anchor_id": str(
                proposal["storage"]["anchor"].get("anchor_id") or ""),
            "storage_anchor_profile": "storage",
            "kitchen_anchor_id": str(
                proposal["kitchen"]["anchor"].get("anchor_id") or ""),
            "kitchen_anchor_profile": "kitchen",
            "topology_space_count_before": len(base_spaces),
            "topology_space_count_after": len(trial_spaces),
            "topology_space_count_delta": 1,
            "closed_storage_space_area_m2": round(float(storage_space.area), 8),
            "closed_space_semantic_anchor_ids": [str(
                contained_semantics[0]["anchor"].get("anchor_id") or "")],
            "thresholds": {
                "min_clear_gap_width_m": .35,
                "max_clear_gap_width_m": 1.50,
                "min_terminal_transverse_angle_deg": 89.0,
                "max_wall_thickness_spread_m": .04,
                "max_intermediate_wall_coverage_m": .01,
                "required_topology_space_count_delta": 1,
            },
        }
        host_id = f"cad_wall_terminal_open_host_{candidate_id}"
        host = {
            "id": host_id,
            "source_representation": "terminal_open_connection_host",
            "resolved_as": "centerline",
            "start": {"x": round(endpoint[0], 8),
                      "z": round(endpoint[1], 8)},
            "end": {"x": round(second[0], 8),
                    "z": round(second[1], 8)},
            "centerline": [list(endpoint), list(second)],
            "opening_axis": [list(endpoint), list(second)],
            "length_m": round(math.dist(endpoint, second), 8),
            "thickness_m": round(thickness, 8),
            "thickness_source":
                "matched_terminal_and_transverse_cad_wall_assemblies",
            "height_m": round(min(
                float(terminal["assembly"].get("height_m") or 2.8),
                float(transverse["assembly"].get("height_m") or 2.8)), 8),
            "height_source":
                "matched_terminal_and_transverse_cad_wall_assemblies",
            "footprint_polygon": [
                [round(float(x), 8), round(float(z), 8)]
                for x, z in list(footprint.exterior.coords)[:-1]
            ],
            "boundary_kind": "terminal_open_connection_host",
            "kind": "interior", "source": "cad",
            "review_status": "accepted", "confidence_grade": "A",
            "confidence": 1.0, "legacy_wall_compatible": True,
            "terminal_open_connection_evidence": proof,
            "source_entity_handles": structural_handles + semantic_handles,
            "source_root_handles": structural_handles,
            "source_layers": sorted({
                str(value) for support in (terminal, transverse)
                for value in support["assembly"].get("source_layers") or []
                if str(value)
            }),
            "source_insert_chains": [],
            "source_entities": source_entities,
            "cad_provenance": {
                "wall_assembly_source_representation":
                    "terminal_open_connection_host",
                "handle": structural_handles[0],
                "source_handle": structural_handles[0],
                "root_handle": structural_handles[0],
                "source_segment_m": copy.deepcopy(cad_axis),
                "source_entities": copy.deepcopy(source_entities),
                "terminal_open_connection_evidence": copy.deepcopy(proof),
            },
        }
        trial_candidate["wall_assembly_id"] = host_id
        trial_candidate["evidence_geometry"] = {
            "method": "cad_labeled_terminal_open_connection_v1",
            "terminal_open_connection_evidence": copy.deepcopy(proof),
            "canonical_binding_axis_source":
                "measured_terminal_to_transverse_wall_centerline",
        }
        result_candidates.append(trial_candidate)
        result_assemblies.append(host)
        proved.append(copy.deepcopy(proof))

    return result_candidates, result_assemblies, {
        "schema_version": 1,
        "method": "cad_labeled_terminal_open_connection_v1",
        "status": "proved" if proved else "unresolved",
        "proposal_count": len(proposals),
        "proved_count": len(proved),
        "connections": proved,
        "rejected_proposals": rejected_proposals,
        "geometric_gap_audit": geometric_gap_audit[:100],
        "geometric_gap_audit_truncated": len(geometric_gap_audit) > 100,
    }


def _resolve_wall_evidence_coincident_with_accepted_openings(
    assemblies: list[dict], candidates: list[dict], *,
    origin_x: float, origin_z: float,
) -> list[dict]:
    """Give a terminal audit-only disposition to a drawn opening threshold.

    Door symbols frequently include a straight threshold/closed-position leaf
    exactly on the opening axis.  Before the opening is bound this source line
    correctly remains unresolved wall evidence.  Once a unique opening has
    been accepted, however, retaining the same line as an unresolved wall both
    inflates the production blocker count and renders a misleading floor trace.

    This pass is deliberately strict and symmetric: the source line and the
    accepted opening axis must cover one another almost completely, be within
    15 mm, agree in direction within one degree and agree in length within
    20 mm (or two percent).  A line matching zero or multiple openings remains
    untouched.  No source coordinates are snapped or rewritten.
    """
    try:
        from shapely.geometry import LineString, Point  # type: ignore
    except Exception:
        return copy.deepcopy(assemblies)

    host_thickness_by_id: dict[str, float] = {}
    for assembly in assemblies:
        identifier = str(assembly.get("id") or "")
        try:
            thickness = float(assembly.get("thickness_m") or 0.0)
        except (TypeError, ValueError):
            thickness = 0.0
        if identifier and .06 <= thickness <= .60:
            host_thickness_by_id[identifier] = thickness

    accepted_axes: list[dict] = []
    for candidate in candidates:
        if str(candidate.get("status") or "") not in {"accepted", "confirmed"}:
            continue
        raw_axis = candidate.get("axis_segment_cad_m") or []
        try:
            cad_first = (float(raw_axis[0][0]), float(raw_axis[0][1]))
            cad_second = (float(raw_axis[-1][0]), float(raw_axis[-1][1]))
        except (TypeError, ValueError, IndexError):
            continue
        model_axis = LineString([
            (cad_first[0] - origin_x, cad_first[1] - origin_z),
            (cad_second[0] - origin_x, cad_second[1] - origin_z),
        ])
        if .20 <= model_axis.length <= 3.0:
            wall_assembly_id = str(candidate.get("wall_assembly_id") or "")
            accepted_axes.append({
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "wall_assembly_id": wall_assembly_id,
                "host_thickness_m": host_thickness_by_id.get(wall_assembly_id),
                "source_handles": sorted({
                    str(value) for value in candidate.get("source_handles") or []
                    if str(value)}),
                "cad_axis": [list(cad_first), list(cad_second)],
                "line": model_axis,
                "evidence_geometry": copy.deepcopy(
                    candidate.get("evidence_geometry") or {}),
            })

    result = copy.deepcopy(assemblies)
    for assembly in result:
        if str(assembly.get("review_status") or "") in {
                "accepted", "confirmed", "rejected", "reject"}:
            continue
        raw_line = assembly.get("source_centerline") or []
        try:
            source_line = LineString([
                (float(raw_line[0][0]), float(raw_line[0][1])),
                (float(raw_line[-1][0]), float(raw_line[-1][1])),
            ])
        except (TypeError, ValueError, IndexError):
            continue
        if not .06 <= source_line.length <= 3.0:
            continue

        source_handles = {
            str(value) for value in assembly.get("source_entity_handles") or []
            if str(value)
        }
        owned_matches = [
            accepted for accepted in accepted_axes
            if source_handles.intersection(accepted.get("source_handles") or [])
        ]
        if len(owned_matches) == 1 and source_line.length <= .30 + 1e-9:
            match = owned_matches[0]
            owned_handles = sorted(source_handles.intersection(
                match.get("source_handles") or []))
            proof = {
                "method": "accepted_opening_source_handle_ownership_v1",
                "candidate_id": match["candidate_id"],
                "accepted_wall_assembly_id": match["wall_assembly_id"],
                "owned_source_handles": owned_handles,
                "source_length_m": round(float(source_line.length), 8),
                "opening_axis_cad_m": copy.deepcopy(match["cad_axis"]),
                "maximum_source_length_m": .30,
                "decision_basis": [
                    "exact_source_handle_owned_by_one_accepted_opening",
                    "short_opening_glyph_segment_not_structural_wall",
                ],
            }
            assembly.update({
                "source_representation": "opening_evidence",
                "resolved_as": "opening_evidence",
                "review_status": "rejected", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": False,
                "footprint_polygon": None, "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_opening_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_wall_source_resolved_as_opening_evidence"])
                    if reason != "cad_wall_representation_unresolved")),
                "opening_evidence": proof,
            })
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "opening_evidence"
            continue

        source_angle = math.degrees(math.atan2(
            source_line.coords[-1][1] - source_line.coords[0][1],
            source_line.coords[-1][0] - source_line.coords[0][0])) % 180.0
        matches: list[dict] = []
        for accepted in accepted_axes:
            axis = accepted["line"]
            axis_angle = math.degrees(math.atan2(
                axis.coords[-1][1] - axis.coords[0][1],
                axis.coords[-1][0] - axis.coords[0][0])) % 180.0
            angle_difference = abs(source_angle - axis_angle)
            angle_difference = min(angle_difference, 180.0 - angle_difference)
            maximum_distance = float(source_line.hausdorff_distance(axis))
            source_covered = float(
                source_line.intersection(axis.buffer(.015, cap_style=2)).length)
            axis_covered = float(
                axis.intersection(source_line.buffer(.015, cap_style=2)).length)
            source_coverage = source_covered / source_line.length
            axis_coverage = axis_covered / axis.length
            length_difference = abs(source_line.length - axis.length)
            axis_first = tuple(axis.coords[0])
            axis_second = tuple(axis.coords[-1])
            axis_unit = ((axis_second[0] - axis_first[0]) / axis.length,
                         (axis_second[1] - axis_first[1]) / axis.length)
            source_projections = [
                (point[0] - axis_first[0]) * axis_unit[0]
                + (point[1] - axis_first[1]) * axis_unit[1]
                for point in source_line.coords
            ]
            source_lateral_offsets = [abs(
                (point[0] - axis_first[0]) * axis_unit[1]
                - (point[1] - axis_first[1]) * axis_unit[0])
                for point in source_line.coords]
            source_axis_overhang = [
                max(0.0, -min(source_projections)),
                max(0.0, max(source_projections) - axis.length),
            ]
            bidirectional_match = bool(
                angle_difference <= 1.0 + 1e-9
                and maximum_distance <= .015 + 1e-9
                and source_coverage >= .995 - 1e-9
                and axis_coverage >= .995 - 1e-9
                and length_difference
                <= max(.02, axis.length * .02) + 1e-9)
            # A CAD door threshold is sometimes deliberately 20--50 mm longer
            # than the actual leaf/opening axis so it meets both wall faces.
            # Hausdorff distance then measures the legitimate axial overhang,
            # not a lateral mismatch.  Accept that representation only when
            # the unique source line contains the complete accepted opening,
            # remains collinear within 15 mm, and has at most 50 mm overhang
            # on either end (60 mm total).  A merely nearby or partly
            # overlapping structural wall cannot pass this asymmetric proof.
            contained_threshold_match = bool(
                not bidirectional_match
                and angle_difference <= 1.0 + 1e-9
                and max(source_lateral_offsets, default=float("inf"))
                <= .015 + 1e-9
                and axis_coverage >= .995 - 1e-9
                and source_coverage >= .94 - 1e-9
                and length_difference <= .06 + 1e-9
                and max(source_axis_overhang) <= .05 + 1e-9
                and sum(source_axis_overhang) <= .06 + 1e-9
                and min(source_projections) <= .005 + 1e-9
                and max(source_projections) >= axis.length - .005 - 1e-9)
            if bidirectional_match or contained_threshold_match:
                matches.append({
                    **accepted,
                    "match_method": (
                        "accepted_opening_contained_threshold_axis_v1"
                        if contained_threshold_match else ""),
                    "source_coverage_ratio": source_coverage,
                    "opening_axis_coverage_ratio": axis_coverage,
                    "maximum_distance_m": maximum_distance,
                    "maximum_lateral_offset_m": max(
                        source_lateral_offsets, default=float("inf")),
                    "source_axis_overhang_m": source_axis_overhang,
                    "angle_difference_deg": angle_difference,
                    "length_difference_m": length_difference,
                })
        if len(matches) == 1:
            match = matches[0]
            assembly.update({
            "source_representation": "opening_evidence",
            "resolved_as": "opening_evidence",
            "review_status": "rejected",
            "confidence_grade": "A",
            "confidence": 1.0,
            "legacy_wall_compatible": False,
            "footprint_polygon": None,
            "centerline": None,
            "thickness_m": None,
            "thickness_source": "not_applicable_opening_evidence",
            "production_blockers": [],
            "reason_codes": sorted(set(
                reason for reason in (
                    (assembly.get("reason_codes") or [])
                    + ["cad_wall_source_resolved_as_opening_evidence"])
                if reason != "cad_wall_representation_unresolved")),
            "opening_evidence": {
                **({"method": match["match_method"]}
                   if match.get("match_method") else {}),
                "candidate_id": match["candidate_id"],
                "accepted_wall_assembly_id": match["wall_assembly_id"],
                "opening_axis_cad_m": copy.deepcopy(match["cad_axis"]),
                "source_axis_model_m": [
                    [round(float(value), 8) for value in source_line.coords[0]],
                    [round(float(value), 8) for value in source_line.coords[-1]],
                ],
                "source_coverage_ratio": round(
                    float(match["source_coverage_ratio"]), 8),
                "opening_axis_coverage_ratio": round(
                    float(match["opening_axis_coverage_ratio"]), 8),
                "maximum_distance_m": round(
                    float(match["maximum_distance_m"]), 8),
                "maximum_lateral_offset_m": round(
                    float(match["maximum_lateral_offset_m"]), 8),
                "source_axis_overhang_m": [round(float(value), 8)
                                            for value in
                                            match["source_axis_overhang_m"]],
                "source_length_m": round(float(source_line.length), 8),
                "opening_axis_length_m": round(float(match["line"].length), 8),
                "axis_angle_difference_deg": round(
                    float(match["angle_difference_deg"]), 8),
                "length_difference_m": round(
                    float(match["length_difference_m"]), 8),
                "thresholds": {
                    **({"minimum_opening_axis_coverage_ratio": .995,
                        "minimum_source_coverage_ratio": .94,
                        "maximum_lateral_offset_m": .015,
                        "maximum_per_end_axis_overhang_m": .05,
                        "maximum_total_axis_overhang_m": .06,
                        "maximum_length_difference_m": .06}
                       if match.get("match_method") else {
                        "minimum_bidirectional_coverage_ratio": .995,
                        "maximum_axis_distance_m": .015,
                        "maximum_length_difference_m": max(
                            .02, round(float(match["line"].length) * .02, 8))}),
                    "maximum_angle_difference_deg": 1.0,
                },
                "decision_basis": ([
                    "unique_accepted_opening_axis",
                    "source_threshold_contains_complete_opening_axis",
                    "bounded_collinear_threshold_overhang",
                ] if match.get("match_method") else [
                    "unique_accepted_opening_axis",
                    "bidirectional_full_axis_coverage",
                    "collinear_source_threshold_geometry",
                ]),
            },
            })
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "opening_evidence"
            continue

        # A proved frame may contain a second longitudinal rail offset from the
        # canonical opening axis.  It is opening hardware, not a second wall.
        # Consume it only inside the CAD-authored frame bbox, with complete
        # bidirectional axial coverage and a unique geometrically proved frame.
        frame_companion_matches: list[dict] = []
        for accepted in accepted_axes:
            evidence = accepted.get("evidence_geometry") or {}
            raw_bounds = evidence.get("bbox_m") or []
            if not (
                isinstance(evidence, Mapping)
                and int(evidence.get("long_rail_count") or 0) >= 2
                and int(evidence.get("cross_member_count") or 0) >= 2
                and bool(evidence.get("opposite_wall_face_support"))
                and len(raw_bounds) == 4
            ):
                continue
            axis = accepted["line"]
            axis_first = tuple(axis.coords[0])
            axis_second = tuple(axis.coords[-1])
            axis_unit = ((axis_second[0] - axis_first[0]) / axis.length,
                         (axis_second[1] - axis_first[1]) / axis.length)
            axis_angle = math.degrees(math.atan2(
                axis_unit[1], axis_unit[0])) % 180.0
            angle_difference = abs(source_angle - axis_angle)
            angle_difference = min(angle_difference,
                                   180.0 - angle_difference)
            projections = [
                (point[0] - axis_first[0]) * axis_unit[0]
                + (point[1] - axis_first[1]) * axis_unit[1]
                for point in source_line.coords]
            interval = (min(projections), max(projections))
            overlap = max(0.0, min(interval[1], axis.length)
                          - max(interval[0], 0.0))
            source_axial_coverage = overlap / source_line.length
            axis_axial_coverage = overlap / axis.length
            signed_offsets = [
                (point[0] - axis_first[0]) * axis_unit[1]
                - (point[1] - axis_first[1]) * axis_unit[0]
                for point in source_line.coords]
            lateral_offset = sum(abs(value) for value in signed_offsets) \
                / len(signed_offsets)
            lateral_spread = max(signed_offsets) - min(signed_offsets)
            model_bounds = (
                float(raw_bounds[0]) - origin_x,
                float(raw_bounds[1]) - origin_z,
                float(raw_bounds[2]) - origin_x,
                float(raw_bounds[3]) - origin_z,
            )
            bbox_contains_source = all(
                model_bounds[0] - .015 <= float(point[0])
                <= model_bounds[2] + .015
                and model_bounds[1] - .015 <= float(point[1])
                <= model_bounds[3] + .015
                for point in source_line.coords)
            try:
                short_span = float(evidence.get("short_span_m") or 0.0)
            except (TypeError, ValueError):
                short_span = 0.0
            if not (
                angle_difference <= 1.0 + 1e-9
                and source_axial_coverage >= .995 - 1e-9
                and axis_axial_coverage >= .995 - 1e-9
                and abs(source_line.length - axis.length)
                    <= max(.02, axis.length * .02) + 1e-9
                and .02 - 1e-9 <= lateral_offset
                    <= min(.30, short_span / 2.0 + .015) + 1e-9
                and abs(lateral_spread) <= .005 + 1e-9
                and bbox_contains_source
            ):
                continue
            frame_companion_matches.append({
                **accepted,
                "source_axial_coverage_ratio": source_axial_coverage,
                "opening_axis_axial_coverage_ratio": axis_axial_coverage,
                "lateral_offset_m": lateral_offset,
                "lateral_offset_spread_m": abs(lateral_spread),
                "angle_difference_deg": angle_difference,
                "source_frame_bbox_model_m": list(model_bounds),
                "short_span_m": short_span,
            })
        if len(frame_companion_matches) == 1:
            match = frame_companion_matches[0]
            proof = {
                "method": "accepted_opening_frame_companion_rail_v1",
                "candidate_id": match["candidate_id"],
                "accepted_wall_assembly_id": match["wall_assembly_id"],
                "opening_axis_cad_m": copy.deepcopy(match["cad_axis"]),
                "source_axis_model_m": [
                    [round(float(value), 8) for value in source_line.coords[0]],
                    [round(float(value), 8) for value in source_line.coords[-1]],
                ],
                "source_frame_bbox_model_m": [round(float(value), 8)
                                                for value in
                                                match["source_frame_bbox_model_m"]],
                "frame_short_span_m": round(float(match["short_span_m"]), 8),
                "source_axial_coverage_ratio": round(float(
                    match["source_axial_coverage_ratio"]), 8),
                "opening_axis_axial_coverage_ratio": round(float(
                    match["opening_axis_axial_coverage_ratio"]), 8),
                "measured_lateral_offset_m": round(float(
                    match["lateral_offset_m"]), 8),
                "lateral_offset_spread_m": round(float(
                    match["lateral_offset_spread_m"]), 8),
                "axis_angle_difference_deg": round(float(
                    match["angle_difference_deg"]), 8),
                "source_length_m": round(float(source_line.length), 8),
                "opening_axis_length_m": round(float(match["line"].length), 8),
                "frame_geometry": copy.deepcopy(match["evidence_geometry"]),
                "thresholds": {
                    "minimum_bidirectional_axial_coverage_ratio": .995,
                    "minimum_lateral_offset_m": .02,
                    "maximum_lateral_offset_m": min(
                        .30, round(float(match["short_span_m"]) / 2.0 + .015, 8)),
                    "maximum_lateral_offset_spread_m": .005,
                    "maximum_angle_difference_deg": 1.0,
                    "maximum_bbox_tolerance_m": .015,
                },
                "decision_basis": [
                    "unique_accepted_opening_axis",
                    "proved_multi_rail_frame_geometry",
                    "source_rail_inside_source_frame_bbox",
                    "bidirectional_full_axial_interval_coverage",
                    "bounded_parallel_frame_rail_offset",
                ],
            }
            assembly.update({
                "source_representation": "opening_evidence",
                "resolved_as": "opening_evidence",
                "review_status": "rejected", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": False,
                "footprint_polygon": None, "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_opening_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_wall_source_resolved_as_opening_evidence"])
                    if reason != "cad_wall_representation_unresolved")),
                "opening_evidence": proof,
            })
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "opening_evidence"
            continue

        # A malformed "closed" two-point return path may be one visible face
        # of an already accepted door/window gap.  Unlike a threshold on the
        # centre axis, that face is offset by exactly half the measured host
        # wall thickness.  Resolve it only when a single accepted opening has
        # full axial overlap, matching length/direction and the measured
        # half-thickness offset.  This cannot consume an arbitrary nearby
        # parallel wall because the source type, axial interval and host
        # thickness all participate in the proof.
        return_path_proof = assembly.get("degenerate_return_path_evidence")
        opening_face_matches: list[dict] = []
        if (isinstance(return_path_proof, dict)
                and return_path_proof.get("method")
                == "cad_closed_two_point_return_path_v1"):
            for accepted in accepted_axes:
                axis = accepted["line"]
                host_thickness = accepted.get("host_thickness_m")
                if not isinstance(host_thickness, (int, float)):
                    continue
                axis_angle = math.degrees(math.atan2(
                    axis.coords[-1][1] - axis.coords[0][1],
                    axis.coords[-1][0] - axis.coords[0][0])) % 180.0
                angle_difference = abs(source_angle - axis_angle)
                angle_difference = min(
                    angle_difference, 180.0 - angle_difference)
                if angle_difference > 1.0 + 1e-9:
                    continue
                axis_first = tuple(axis.coords[0])
                axis_second = tuple(axis.coords[-1])
                axis_unit = ((axis_second[0] - axis_first[0]) / axis.length,
                             (axis_second[1] - axis_first[1]) / axis.length)
                projections = [
                    (point[0] - axis_first[0]) * axis_unit[0]
                    + (point[1] - axis_first[1]) * axis_unit[1]
                    for point in source_line.coords]
                source_interval = (min(projections), max(projections))
                overlap = max(0.0, min(source_interval[1], axis.length)
                              - max(source_interval[0], 0.0))
                source_axial_coverage = overlap / source_line.length
                axis_axial_coverage = overlap / axis.length
                lateral_offsets = [abs(
                    (point[0] - axis_first[0]) * axis_unit[1]
                    - (point[1] - axis_first[1]) * axis_unit[0])
                    for point in source_line.coords]
                lateral_offset = sum(lateral_offsets) / len(lateral_offsets)
                lateral_spread = max(lateral_offsets) - min(lateral_offsets)
                expected_half_thickness = float(host_thickness) / 2.0
                offset_delta = abs(lateral_offset - expected_half_thickness)
                length_difference = abs(source_line.length - axis.length)
                maximum_length_difference = max(.02, axis.length * .02)
                if not (
                    source_axial_coverage >= .995 - 1e-9
                    and axis_axial_coverage >= .995 - 1e-9
                    and .03 <= lateral_offset <= .30
                    and lateral_spread <= .005 + 1e-9
                    and offset_delta <= .02 + 1e-9
                    and length_difference <= maximum_length_difference + 1e-9
                ):
                    continue
                opening_face_matches.append({
                    **accepted,
                    "source_axial_coverage_ratio": source_axial_coverage,
                    "opening_axis_axial_coverage_ratio": axis_axial_coverage,
                    "lateral_offset_m": lateral_offset,
                    "lateral_offset_spread_m": lateral_spread,
                    "expected_half_thickness_m": expected_half_thickness,
                    "offset_delta_m": offset_delta,
                    "angle_difference_deg": angle_difference,
                    "length_difference_m": length_difference,
                    "maximum_length_difference_m": maximum_length_difference,
                })
        if len(opening_face_matches) == 1:
            match = opening_face_matches[0]
            assembly.update({
                "source_representation": "opening_evidence",
                "resolved_as": "opening_evidence",
                "review_status": "rejected",
                "confidence_grade": "A",
                "confidence": 1.0,
                "legacy_wall_compatible": False,
                "footprint_polygon": None,
                "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_opening_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_wall_source_resolved_as_opening_evidence"])
                    if reason not in {
                        "cad_wall_representation_unresolved",
                        "cad_wall_footprint_invalid"})),
                "opening_evidence": {
                    "method": "accepted_opening_parallel_wall_face_v1",
                    "candidate_id": match["candidate_id"],
                    "accepted_wall_assembly_id": match["wall_assembly_id"],
                    "opening_axis_cad_m": copy.deepcopy(match["cad_axis"]),
                    "source_axis_model_m": [
                        [round(float(value), 8)
                         for value in source_line.coords[0]],
                        [round(float(value), 8)
                         for value in source_line.coords[-1]],
                    ],
                    "host_wall_thickness_m": round(
                        float(match["host_thickness_m"]), 8),
                    "expected_half_thickness_m": round(
                        float(match["expected_half_thickness_m"]), 8),
                    "measured_lateral_offset_m": round(
                        float(match["lateral_offset_m"]), 8),
                    "lateral_offset_spread_m": round(
                        float(match["lateral_offset_spread_m"]), 8),
                    "half_thickness_offset_delta_m": round(
                        float(match["offset_delta_m"]), 8),
                    "source_axial_coverage_ratio": round(
                        float(match["source_axial_coverage_ratio"]), 8),
                    "opening_axis_axial_coverage_ratio": round(
                        float(match["opening_axis_axial_coverage_ratio"]), 8),
                    "source_length_m": round(float(source_line.length), 8),
                    "opening_axis_length_m": round(
                        float(match["line"].length), 8),
                    "axis_angle_difference_deg": round(
                        float(match["angle_difference_deg"]), 8),
                    "length_difference_m": round(
                        float(match["length_difference_m"]), 8),
                    "degenerate_return_path_evidence": copy.deepcopy(
                        return_path_proof),
                    "thresholds": {
                        "minimum_bidirectional_axial_coverage_ratio": .995,
                        "minimum_lateral_offset_m": .03,
                        "maximum_lateral_offset_m": .30,
                        "maximum_lateral_offset_spread_m": .005,
                        "maximum_half_thickness_offset_delta_m": .02,
                        "maximum_angle_difference_deg": 1.0,
                        "maximum_length_difference_m": round(float(
                            match["maximum_length_difference_m"]), 8),
                    },
                    "decision_basis": [
                        "closed_source_has_exactly_two_unique_points",
                        "unique_accepted_opening_axis",
                        "bidirectional_full_axial_interval_coverage",
                        "parallel_source_face_matches_measured_half_wall_thickness",
                    ],
                },
            })
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "opening_evidence"
            continue

        jamb_matches: list[dict] = []
        if source_line.length <= .60 + 1e-9:
            source_endpoints = [
                (float(source_line.coords[0][0]), float(source_line.coords[0][1])),
                (float(source_line.coords[-1][0]), float(source_line.coords[-1][1])),
            ]
            for accepted in accepted_axes:
                axis = accepted["line"]
                axis_angle = math.degrees(math.atan2(
                    axis.coords[-1][1] - axis.coords[0][1],
                    axis.coords[-1][0] - axis.coords[0][0])) % 180.0
                angle_difference = abs(source_angle - axis_angle)
                angle_difference = min(
                    angle_difference, 180.0 - angle_difference)
                if angle_difference < 89.0 - 1e-9:
                    continue
                axis_endpoints = [
                    (float(axis.coords[0][0]), float(axis.coords[0][1])),
                    (float(axis.coords[-1][0]), float(axis.coords[-1][1])),
                ]
                endpoint_pairs = []
                for axis_index, axis_point in enumerate(axis_endpoints):
                    source_distance = float(source_line.distance(Point(axis_point)))
                    nearest_source_index = min(
                        range(len(source_endpoints)),
                        key=lambda source_index: math.dist(
                            source_endpoints[source_index], axis_point))
                    endpoint_pairs.append((
                        source_distance, nearest_source_index, axis_index))
                endpoint_distance, source_index, axis_index = min(endpoint_pairs)
                if endpoint_distance > .015 + 1e-9:
                    continue
                jamb_matches.append({
                    **accepted,
                    "angle_difference_deg": angle_difference,
                    "endpoint_distance_m": endpoint_distance,
                    "source_endpoint_index": source_index,
                    "opening_axis_endpoint_index": axis_index,
                })
        if len(jamb_matches) != 1:
            continue
        match = jamb_matches[0]
        proof = {
            "support_method": "accepted_opening_axis_endpoint_jamb_v1",
            "supports": [{
                "wall_assembly_id": match["wall_assembly_id"],
                "candidate_id": match["candidate_id"],
                "axis_angle_difference_deg": round(
                    float(match["angle_difference_deg"]), 8),
                "endpoint_distance_m": round(
                    float(match["endpoint_distance_m"]), 8),
                "source_endpoint_index": int(match["source_endpoint_index"]),
                "opening_axis_endpoint_index": int(
                    match["opening_axis_endpoint_index"]),
            }],
            "source_length_m": round(float(source_line.length), 8),
            "endpoint_support_ratio": 1.0,
            "coverage_ratio": 0.0,
            "uncovered_length_m": round(float(source_line.length), 8),
            "opening_axis_cad_m": copy.deepcopy(match["cad_axis"]),
            "thresholds": {
                "minimum_axis_angle_difference_deg": 89.0,
                "maximum_endpoint_distance_m": .015,
                "minimum_source_length_m": .06,
                "maximum_source_length_m": .60,
            },
            "decision_basis": [
                "unique_accepted_opening_axis",
                "perpendicular_short_source_segment",
                "shared_opening_axis_endpoint",
            ],
        }
        assembly.update({
            "source_representation": "junction_evidence",
            "resolved_as": "junction_evidence",
            "review_status": "rejected",
            "confidence_grade": "A",
            "confidence": 1.0,
            "legacy_wall_compatible": False,
            "footprint_polygon": None,
            "centerline": None,
            "thickness_m": None,
            "thickness_source": "not_applicable_junction_evidence",
            "production_blockers": [],
            "reason_codes": sorted(set(
                reason for reason in (
                    (assembly.get("reason_codes") or [])
                    + ["cad_wall_source_is_transverse_cap_or_junction"])
                if reason != "cad_wall_representation_unresolved")),
            "junction_evidence": proof,
        })
        provenance = assembly.get("cad_provenance")
        if isinstance(provenance, dict):
            provenance["wall_assembly_source_representation"] = \
                "junction_evidence"
    return result


def _resolve_collinear_wall_face_continuations(
    assemblies: list[dict],
) -> list[dict]:
    """Resolve a staggered pair of wall faces between two proven junctions.

    At perpendicular wall junctions, architectural CAD often extends the two
    faces of the same wall to different limits.  Their usable overlap can be
    below the normal 80% pairing threshold even though the wall is fully
    determined by an already-paired continuation and two transverse terminal
    walls.  This post-pass accepts only that bounded case; it never lowers the
    primary paired-face threshold.
    """
    try:
        from shapely.geometry import LineString, Point, Polygon  # type: ignore
    except Exception:
        return copy.deepcopy(assemblies)

    epsilon = 1e-9

    def line(value: Any) -> Optional[Any]:
        try:
            result = LineString([
                (float(value[0][0]), float(value[0][1])),
                (float(value[-1][0]), float(value[-1][1])),
            ])
            return result if result.length > epsilon else None
        except (TypeError, ValueError, IndexError, KeyError):
            return None

    def angle(value: Any) -> float:
        return math.degrees(math.atan2(
            value.coords[-1][1] - value.coords[0][1],
            value.coords[-1][0] - value.coords[0][0])) % 180.0

    def angle_difference(first: Any, second: Any) -> float:
        difference = abs(angle(first) - angle(second))
        return min(difference, 180.0 - difference)

    def interval_on_axis(value: Any, axis_first: tuple[float, float],
                         unit: tuple[float, float]) -> tuple[float, float]:
        values = [
            (float(point[0]) - axis_first[0]) * unit[0]
            + (float(point[1]) - axis_first[1]) * unit[1]
            for point in value.coords
        ]
        return min(values), max(values)

    def point_on_axis(axis_first: tuple[float, float],
                      unit: tuple[float, float], scalar: float) -> tuple[float, float]:
        return axis_first[0] + scalar * unit[0], axis_first[1] + scalar * unit[1]

    def infinite_line_intersection(first: Any, second: Any) -> Optional[tuple[float, float]]:
        p = (float(first.coords[0][0]), float(first.coords[0][1]))
        r = (float(first.coords[-1][0]) - p[0],
             float(first.coords[-1][1]) - p[1])
        q = (float(second.coords[0][0]), float(second.coords[0][1]))
        s = (float(second.coords[-1][0]) - q[0],
             float(second.coords[-1][1]) - q[1])
        denominator = r[0] * s[1] - r[1] * s[0]
        if abs(denominator) <= epsilon:
            return None
        offset = (q[0] - p[0], q[1] - p[1])
        parameter = (offset[0] * s[1] - offset[1] * s[0]) / denominator
        return p[0] + parameter * r[0], p[1] + parameter * r[1]

    def point_to_infinite_line_distance(point: tuple[float, float],
                                        value: Any) -> float:
        first = (float(value.coords[0][0]), float(value.coords[0][1]))
        vector = (float(value.coords[-1][0]) - first[0],
                  float(value.coords[-1][1]) - first[1])
        length = math.hypot(vector[0], vector[1])
        if length <= epsilon:
            return float("inf")
        offset = (point[0] - first[0], point[1] - first[1])
        return abs(vector[0] * offset[1] - vector[1] * offset[0]) / length

    result = copy.deepcopy(assemblies)
    accepted_rows: list[dict] = []
    for assembly in result:
        if str(assembly.get("review_status") or "") not in {"accepted", "confirmed"}:
            continue
        axis = line(assembly.get("centerline") or [])
        try:
            thickness = float(assembly.get("thickness_m") or 0)
        except (TypeError, ValueError):
            continue
        if axis is None or not .06 <= thickness <= .60:
            continue
        footprint = None
        try:
            raw_footprint = assembly.get("footprint_polygon") or []
            if len(raw_footprint) >= 3:
                footprint = Polygon(raw_footprint)
        except (TypeError, ValueError):
            footprint = None
        accepted_rows.append({
            "assembly": assembly, "axis": axis, "thickness": thickness,
            "footprint": footprint,
        })

    for pending in result:
        if str(pending.get("review_status") or "") in {
                "accepted", "confirmed", "rejected", "reject"}:
            continue
        source = line(pending.get("source_centerline") or [])
        if source is None or not .30 <= source.length <= 5.0:
            continue
        source_first = (float(source.coords[0][0]), float(source.coords[0][1]))
        direction = (
            (float(source.coords[-1][0]) - source_first[0]) / source.length,
            (float(source.coords[-1][1]) - source_first[1]) / source.length,
        )
        pending_candidates: list[dict] = []
        for host_row in accepted_rows:
            host = host_row["assembly"]
            if str(host.get("source_representation") or "") != "paired_faces":
                continue
            thickness = float(host_row["thickness"])
            source_entities = host.get("source_entities") or []
            entity_lines = []
            for entity in source_entities:
                entity_line = line((entity or {}).get("model_segment_m") or []) \
                    if isinstance(entity, dict) else None
                if entity_line is not None:
                    entity_lines.append((entity, entity_line))
            for continuation_entity, continuation_line in entity_lines:
                if angle_difference(source, continuation_line) > 1.0 + epsilon:
                    continue
                collinear_distance = max(
                    point_to_infinite_line_distance(
                        (float(source.coords[index][0]),
                         float(source.coords[index][1])), continuation_line)
                    for index in (0, -1))
                if collinear_distance > .005 + epsilon:
                    continue
                continuation_gap = min(
                    Point(source.coords[index]).distance(
                        Point(continuation_line.coords[other]))
                    for index in (0, -1) for other in (0, -1))
                if continuation_gap > thickness + .02 + epsilon:
                    continue
                for mate_entity, mate_line in entity_lines:
                    if mate_entity is continuation_entity:
                        continue
                    if angle_difference(source, mate_line) > 1.0 + epsilon:
                        continue
                    face_separation = float(source.distance(mate_line))
                    if abs(face_separation - thickness) > .005 + epsilon:
                        continue
                    source_interval = interval_on_axis(
                        source, source_first, direction)
                    mate_interval = interval_on_axis(
                        mate_line, source_first, direction)
                    overlap_start = max(source_interval[0], mate_interval[0])
                    overlap_end = min(source_interval[1], mate_interval[1])
                    overlap_length = max(0.0, overlap_end - overlap_start)
                    overlap_ratio = overlap_length / max(source.length, epsilon)
                    if overlap_length < .30 - epsilon or overlap_ratio < .60 - epsilon:
                        continue
                    first_face_point = point_on_axis(
                        source_first, direction, overlap_start)
                    second_face_point = point_on_axis(
                        source_first, direction, overlap_end)
                    normal = (-direction[1], direction[0])
                    signed_offset = (
                        (float(mate_line.coords[0][0]) - first_face_point[0]) * normal[0]
                        + (float(mate_line.coords[0][1]) - first_face_point[1]) * normal[1]
                    )
                    axis_offset = signed_offset / 2.0
                    overlap_axis = LineString([
                        (first_face_point[0] + normal[0] * axis_offset,
                         first_face_point[1] + normal[1] * axis_offset),
                        (second_face_point[0] + normal[0] * axis_offset,
                         second_face_point[1] + normal[1] * axis_offset),
                    ])
                    occupied_overlap = float(overlap_axis.intersection(
                        host_row["axis"].buffer(.02, cap_style=2)).length)
                    if occupied_overlap > .02 + epsilon:
                        continue

                    terminal_supports: list[dict] = []
                    terminal_points: list[tuple[float, float]] = []
                    terminal_valid = True
                    for endpoint_index in (0, -1):
                        endpoint = Point(overlap_axis.coords[endpoint_index])
                        matches = []
                        for support_row in accepted_rows:
                            support = support_row["assembly"]
                            if support is host:
                                continue
                            support_axis = support_row["axis"]
                            transverse_angle = angle_difference(
                                overlap_axis, support_axis)
                            if transverse_angle < 89.0 - epsilon:
                                continue
                            intersection = infinite_line_intersection(
                                overlap_axis, support_axis)
                            if intersection is None:
                                continue
                            axis_extension = endpoint.distance(Point(intersection))
                            axis_limit = thickness / 2.0 + .02
                            support_extension = Point(intersection).distance(support_axis)
                            support_limit = float(support_row["thickness"]) / 2.0 + .02
                            if (axis_extension > axis_limit + epsilon
                                    or support_extension > support_limit + epsilon):
                                continue
                            matches.append({
                                "wall_assembly_id": str(support.get("id") or ""),
                                "axis_angle_difference_deg": round(
                                    transverse_angle, 8),
                                "axis_extension_m": round(axis_extension, 8),
                                "axis_extension_limit_m": round(axis_limit, 8),
                                "support_axis_extension_m": round(
                                    support_extension, 8),
                                "support_axis_extension_limit_m": round(
                                    support_limit, 8),
                                "intersection_model_m": [
                                    round(intersection[0], 8),
                                    round(intersection[1], 8),
                                ],
                                "intersection": intersection,
                            })
                        if len(matches) != 1:
                            terminal_valid = False
                            break
                        terminal_supports.append(matches[0])
                        terminal_points.append(matches[0]["intersection"])
                    if (not terminal_valid
                            or len({row["wall_assembly_id"]
                                    for row in terminal_supports}) != 2):
                        continue
                    pending_candidates.append({
                        "host": host,
                        "continuation_entity": continuation_entity,
                        "mate_entity": mate_entity,
                        "thickness": thickness,
                        "overlap_length": overlap_length,
                        "overlap_ratio": overlap_ratio,
                        "continuation_gap": continuation_gap,
                        "collinear_distance": collinear_distance,
                        "face_separation": face_separation,
                        "occupied_overlap": occupied_overlap,
                        "overlap_axis": overlap_axis,
                        "terminal_supports": terminal_supports,
                        "terminal_points": terminal_points,
                    })
        if len(pending_candidates) != 1:
            continue
        match = pending_candidates[0]
        first, second = match["terminal_points"]
        canonical_axis = LineString([first, second])
        if canonical_axis.length < .30:
            continue
        footprint = canonical_axis.buffer(
            match["thickness"] / 2.0, cap_style=2, join_style=2)
        footprint_points = [
            [round(float(x), 8), round(float(z), 8)]
            for x, z in list(footprint.exterior.coords)[:-1]
        ]
        source_entities = copy.deepcopy(pending.get("source_entities") or [])
        mate_entity = copy.deepcopy(match["mate_entity"])
        if mate_entity and str(mate_entity.get("handle") or "") not in {
                str(row.get("handle") or "") for row in source_entities
                if isinstance(row, dict)}:
            source_entities.append(mate_entity)
        handles = sorted({
            str(row.get("handle") or "").strip()
            for row in source_entities if isinstance(row, dict)
            and str(row.get("handle") or "").strip()
        })
        proof_supports = []
        for row in match["terminal_supports"]:
            proof_supports.append({
                key: copy.deepcopy(value) for key, value in row.items()
                if key != "intersection"
            })
        proof = {
            "method": "bounded_staggered_paired_faces_v1",
            "source_wall_assembly_id": str(match["host"].get("id") or ""),
            "continuation_face_handle": str(
                match["continuation_entity"].get("handle") or ""),
            "mate_face_handle": str(match["mate_entity"].get("handle") or ""),
            "face_separation_m": round(match["face_separation"], 8),
            "wall_thickness_m": round(match["thickness"], 8),
            "continuation_face_gap_m": round(match["continuation_gap"], 8),
            "continuation_face_collinear_distance_m": round(
                match["collinear_distance"], 8),
            "projected_overlap_length_m": round(match["overlap_length"], 8),
            "projected_overlap_ratio": round(match["overlap_ratio"], 8),
            "occupied_overlap_length_m": round(match["occupied_overlap"], 8),
            "terminal_supports": proof_supports,
            "thresholds": {
                "maximum_angle_difference_deg": 1.0,
                "maximum_collinear_distance_m": .005,
                "maximum_thickness_delta_m": .005,
                "maximum_continuation_gap_extra_m": .02,
                "minimum_projected_overlap_length_m": .30,
                "minimum_projected_overlap_ratio": .60,
                "maximum_occupied_overlap_length_m": .02,
                "minimum_terminal_angle_difference_deg": 89.0,
                "maximum_terminal_extension_extra_m": .02,
            },
        }
        pending.update({
            "source_representation": "collinear_face_continuation",
            "resolved_as": "collinear_face_continuation",
            "start": {"x": round(first[0], 8), "z": round(first[1], 8)},
            "end": {"x": round(second[0], 8), "z": round(second[1], 8)},
            "centerline": [[round(first[0], 8), round(first[1], 8)],
                           [round(second[0], 8), round(second[1], 8)]],
            "opening_axis": [[round(first[0], 8), round(first[1], 8)],
                             [round(second[0], 8), round(second[1], 8)]],
            "length_m": round(float(canonical_axis.length), 8),
            "thickness_m": round(match["thickness"], 8),
            "thickness_source": "matched_staggered_cad_wall_faces",
            "height_m": float(match["host"].get("height_m") or 2.8),
            "height_source": str(match["host"].get("height_source")
                                 or "matched_source_wall_assembly"),
            "footprint_polygon": footprint_points,
            "boundary_kind": "collinear_face_continuation",
            "kind": str(match["host"].get("kind") or "interior"),
            "review_status": "accepted",
            "confidence_grade": "A", "confidence": 1.0,
            "legacy_wall_compatible": True,
            "reason_codes": [], "production_blockers": [],
            "collinear_face_continuation_evidence": proof,
            "source_entity_handles": handles,
            "source_root_handles": sorted({
                str(row.get("root_handle") or row.get("handle") or "")
                for row in source_entities if isinstance(row, dict)}),
            "source_entities": source_entities,
        })
        provenance = pending.get("cad_provenance")
        if isinstance(provenance, dict):
            provenance["wall_assembly_source_representation"] = \
                "collinear_face_continuation"
            provenance["source_entities"] = copy.deepcopy(source_entities)
    return result


def _resolve_wall_evidence_with_global_topology(
    assemblies: list[dict], footprints: list[dict], space_polygons: Sequence[Any],
    topology_summary: Mapping[str, Any], *, origin_x: float, origin_z: float,
    building_envelope_evidence: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Give source wall-face rows a terminal whole-plan disposition.

    A mixed-convention CAD may use measured double faces for interior walls and
    single boundary runs for parts of the exterior shell.  The local pairer is
    deliberately unable to assign a thickness to the latter.  Once a proved
    whole-plan wall footprint and closed physical spaces exist, however, a
    source run can be consumed by that footprint when three independent cross
    sections are stable and almost the full run tracks a physical-space
    boundary.  The source row becomes audit-only evidence; it does not create a
    second overlapping wall and cannot use an arbitrary filled mask as proof.
    """
    try:
        from shapely.geometry import LineString, Point, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:
        return copy.deepcopy(assemblies)
    if str(topology_summary.get("status") or "") != "proved":
        return copy.deepcopy(assemblies)

    wall_polygons = []
    for footprint in footprints:
        try:
            exterior = [(float(point["x"]), float(point["z"]))
                        for point in footprint.get("points") or []]
            holes = [[(float(point["x"]), float(point["z"])) for point in ring]
                     for ring in footprint.get("interior_rings") or []]
            polygon = Polygon(exterior, holes)
        except (KeyError, TypeError, ValueError):
            continue
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.geom_type == "Polygon" and not polygon.is_empty:
            wall_polygons.append(polygon)
        elif polygon.geom_type == "MultiPolygon":
            wall_polygons.extend(part for part in polygon.geoms if not part.is_empty)
    if not wall_polygons:
        return copy.deepcopy(assemblies)
    wall_mask = unary_union(wall_polygons).buffer(0)
    opening_host_polygons = []
    opening_host_ids = []
    opening_host_representations = {
        "opening_host_stitch", "global_topology_opening_host",
        "frame_geometry_opening_host", "window_frame_host_extension",
        "door_swing_geometry_opening_host", "repeated_window_frame_opening_host",
        "terminal_open_connection_host",
    }
    for assembly in assemblies:
        if (str(assembly.get("review_status") or "") not in {"accepted", "confirmed"}
                or str(assembly.get("source_representation") or "")
                not in opening_host_representations):
            continue
        try:
            polygon = Polygon([(float(point[0]), float(point[1]))
                               for point in assembly.get("footprint_polygon") or []])
        except (TypeError, ValueError, IndexError):
            continue
        if polygon.is_valid and not polygon.is_empty:
            opening_host_polygons.append(polygon)
            opening_host_ids.append(str(assembly.get("id") or ""))
    # Opening hosts restore the measured wall envelope only for evidence
    # sampling.  They are never merged into the rendered global footprint: the
    # physical window/door gap must remain a void in production geometry.
    evidence_mask = unary_union([wall_mask, *opening_host_polygons]).buffer(0)

    model_spaces = []
    for raw_polygon in space_polygons:
        try:
            exterior = [(float(x) - origin_x, float(z) - origin_z)
                        for x, z in list(raw_polygon.exterior.coords)[:-1]]
            holes = [[(float(x) - origin_x, float(z) - origin_z)
                      for x, z in list(ring.coords)[:-1]]
                     for ring in raw_polygon.interiors]
            polygon = Polygon(exterior, holes)
        except (AttributeError, TypeError, ValueError):
            continue
        if polygon.is_valid and not polygon.is_empty:
            model_spaces.append(polygon)
    if not model_spaces:
        return copy.deepcopy(assemblies)
    space_boundary = unary_union([polygon.boundary for polygon in model_spaces])

    topology_hash_payload = {
        "summary": {
            key: copy.deepcopy(value) for key, value in topology_summary.items()
            if key not in {"topology_hash"}
        },
        "footprints": copy.deepcopy(footprints),
        "opening_host_ids": sorted(value for value in opening_host_ids if value),
    }
    topology_hash = hashlib.sha256(json.dumps(
        topology_hash_payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    footprint_ids = sorted(str(row.get("id") or "") for row in footprints
                           if str(row.get("id") or ""))

    result = copy.deepcopy(assemblies)
    envelope_handles: set[str] = set()
    if (isinstance(building_envelope_evidence, Mapping)
            and str(building_envelope_evidence.get("method") or "")
            == "cad_semantic_nested_building_envelope_v1"
            and str(building_envelope_evidence.get("status") or "") == "proved"
            and int(building_envelope_evidence.get("semantic_anchor_count") or 0) >= 3
            and len(set(building_envelope_evidence.get(
                "semantic_profiles") or [])) >= 2):
        outer_handles = {
            str(value) for value in
            building_envelope_evidence.get("outer_source_handles") or []
            if str(value)}
        inner_handles = {
            str(value) for value in
            building_envelope_evidence.get("inner_source_handles") or []
            if str(value)}
        if outer_handles and inner_handles and outer_handles.isdisjoint(inner_handles):
            envelope_handles = outer_handles | inner_handles
    for assembly in result:
        if str(assembly.get("review_status") or "") in {
                "accepted", "confirmed", "rejected", "reject"}:
            continue
        assembly_handles = {
            str(value) for value in assembly.get("source_entity_handles") or []
            if str(value)}
        if (str(assembly.get("source_representation") or "")
                == "closed_footprint" and assembly_handles
                and assembly_handles.issubset(envelope_handles)):
            assembly.update({
                "source_representation": "global_topology_envelope_evidence",
                "resolved_as": "global_topology_envelope_evidence",
                "review_status": "rejected", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": False,
                "footprint_polygon": None, "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_global_topology_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_envelope_contour_consumed_by_proved_global_topology"])
                    if reason != "closed_perimeter_wall_role_unproven")),
                "global_topology_envelope_evidence": {
                    "method": "proved_semantic_envelope_contour_consumption_v1",
                    "global_topology_method": str(
                        topology_summary.get("method") or ""),
                    "global_topology_status": "proved",
                    "global_wall_footprint_ids": footprint_ids,
                    "source_entity_handles": sorted(assembly_handles),
                    "semantic_anchor_count": int(
                        building_envelope_evidence.get(
                            "semantic_anchor_count") or 0),
                    "semantic_profiles": sorted(set(
                        str(value) for value in
                        building_envelope_evidence.get(
                            "semantic_profiles") or [] if str(value))),
                    "decision_basis": [
                        "unique_nested_source_envelope_pair",
                        "multiple_source_semantic_anchors_inside_inner_contour",
                        "proved_global_wall_footprint_is_geometry_authority",
                        "closed_contour_retained_as_audit_evidence_only",
                    ],
                },
            })
            continue
        raw = assembly.get("source_centerline") or []
        try:
            source_line = LineString([
                (float(raw[0][0]), float(raw[0][1])),
                (float(raw[-1][0]), float(raw[-1][1])),
            ])
        except (TypeError, ValueError, IndexError):
            continue
        resolution_audit = {
            "method": "global_topology_wall_evidence_resolution_audit_v1",
            "source_length_m": round(float(source_line.length), 8),
            "decision": "pending",
        }
        assembly["global_topology_resolution_audit"] = resolution_audit
        # 60--600 mm source segments can be real transverse wall caps.  They
        # must reach the measured connector/boundary-face gates below before
        # the generic >=150 mm longitudinal-strip resolver is considered.
        if .02 - 1e-9 <= source_line.length < .06 - 1e-9:
            micro_coverage = float(source_line.intersection(
                wall_mask.buffer(.015, join_style=2)).length) / source_line.length
            micro_endpoint_distances = [float(
                Point(point).distance(wall_mask.boundary))
                for point in source_line.coords]
            micro_midpoint_distance = float(source_line.interpolate(
                .5, normalized=True).distance(wall_mask.boundary))
            resolution_audit.update({
                "source_wall_mask_coverage_ratio": round(micro_coverage, 8),
                "endpoint_wall_boundary_distances_m": [
                    round(value, 8) for value in micro_endpoint_distances],
                "midpoint_wall_boundary_distance_m": round(
                    micro_midpoint_distance, 8),
            })
            micro_embedded = bool(
                micro_coverage >= .995 - 1e-9
                and min(micro_endpoint_distances)
                    >= max(.015, float(source_line.length)) - 1e-9
                and micro_midpoint_distance >= .025 - 1e-9)
            micro_boundary = bool(
                micro_coverage >= .995 - 1e-9
                and max(micro_endpoint_distances) <= .02 + 1e-9
                and micro_midpoint_distance <= .02 + 1e-9)
            if micro_embedded or micro_boundary:
                proof = {
                    "method": (
                        "proved_global_wall_embedded_micro_detail_v1"
                        if micro_embedded
                        else "proved_global_wall_boundary_micro_detail_v1"),
                    "global_topology_method": str(
                        topology_summary.get("method") or ""),
                    "global_topology_status": "proved",
                    "global_topology_hash": topology_hash,
                    "global_wall_footprint_ids": footprint_ids,
                    "source_length_m": round(float(source_line.length), 8),
                    "source_wall_mask_coverage_ratio": round(
                        micro_coverage, 8),
                    "endpoint_wall_boundary_distances_m": [
                        round(value, 8) for value in micro_endpoint_distances],
                    "midpoint_wall_boundary_distance_m": round(
                        micro_midpoint_distance, 8),
                    "thresholds": {
                        "minimum_source_length_m": .02,
                        "maximum_source_length_m": .06,
                        "minimum_source_wall_mask_coverage_ratio": .995,
                        "minimum_endpoint_wall_boundary_distance_m": .015,
                        "minimum_endpoint_independent_support_ratio": 1.0,
                        "minimum_midpoint_wall_boundary_distance_m": .025,
                    },
                    "decision_basis": [
                        "source_extent_below_supported_wall_length",
                        ("source_fully_embedded_in_proved_wall_material"
                         if micro_embedded
                         else "source_tracks_proved_wall_mask_boundary"),
                        ("both_endpoints_have_independent_wall_material_support"
                         if micro_embedded
                         else "both_endpoints_and_midpoint_touch_proved_boundary"),
                        "micro_detail_is_audit_evidence_not_duplicate_wall",
                    ],
                }
                assembly.update({
                    "source_representation": "global_topology_micro_evidence",
                    "resolved_as": "global_topology_micro_evidence",
                    "review_status": "rejected", "confidence_grade": "A",
                    "confidence": 1.0, "legacy_wall_compatible": False,
                    "footprint_polygon": None, "centerline": None,
                    "thickness_m": None,
                    "thickness_source": (
                        "not_applicable_global_topology_evidence"),
                    "production_blockers": [],
                    "reason_codes": sorted(set(
                        reason for reason in (
                            (assembly.get("reason_codes") or []) + [
                                "cad_embedded_micro_detail_consumed_by_proved_global_topology"])
                        if reason != "cad_wall_representation_unresolved")),
                    "global_topology_micro_evidence": proof,
                })
                resolution_audit.update(
                    decision="resolved",
                    reason="embedded_micro_detail_consumed_by_global_topology")
                provenance = assembly.get("cad_provenance")
                if isinstance(provenance, dict):
                    provenance["wall_assembly_source_representation"] = \
                        "global_topology_micro_evidence"
                continue
        if not .06 <= source_line.length <= 20.0:
            resolution_audit.update(
                decision="unresolved",
                reason="source_length_outside_resolution_range",
                thresholds={"minimum_source_length_m": .06,
                            "maximum_source_length_m": 20.0})
            continue
        wall_coverage = float(
            source_line.intersection(wall_mask.buffer(.015, join_style=2)).length
        ) / source_line.length
        resolution_audit["source_wall_mask_coverage_ratio"] = round(
            wall_coverage, 8)
        if wall_coverage < .995 - 1e-9:
            resolution_audit.update(
                decision="unresolved",
                reason="source_wall_mask_coverage_below_threshold",
                minimum_source_wall_mask_coverage_ratio=.995)
            continue

        dx = (source_line.coords[-1][0] - source_line.coords[0][0]) \
            / source_line.length
        dz = (source_line.coords[-1][1] - source_line.coords[0][1]) \
            / source_line.length
        valid_cross_sections = []
        for fraction in (.12, .25, .38, .50, .62, .75, .88):
            sample = source_line.interpolate(fraction, normalized=True)
            cross = LineString([
                (sample.x - dz * .75, sample.y + dx * .75),
                (sample.x + dz * .75, sample.y - dx * .75),
            ])
            intersection = cross.intersection(evidence_mask)
            components = (list(intersection.geoms)
                          if getattr(intersection, "geom_type", "")
                          in {"MultiLineString", "GeometryCollection"}
                          else [intersection])
            supported = [component for component in components
                         if getattr(component, "length", 0) > 0
                         and component.distance(Point(sample.x, sample.y)) <= .015 + 1e-9]
            supported = [component for component in supported
                         if .06 - 1e-9 <= float(component.length) <= .60 + 1e-9]
            if len(supported) != 1:
                continue
            component = supported[0]
            signed_offsets = sorted(
                (float(point[0]) - float(sample.x)) * (-dz)
                + (float(point[1]) - float(sample.y)) * dx
                for point in component.coords)
            valid_cross_sections.append({
                "fraction": fraction,
                "width_m": round(float(component.length), 8),
                "sample_to_section_distance_m": round(
                    float(component.distance(Point(sample.x, sample.y))), 8),
                "signed_min_offset_m": round(min(signed_offsets), 8),
                "signed_max_offset_m": round(max(signed_offsets), 8),
                "section_midpoint_offset_m": round(
                    (min(signed_offsets) + max(signed_offsets)) / 2.0, 8),
                "nearest_section_edge_offset_m": round(
                    min(abs(min(signed_offsets)), abs(max(signed_offsets))), 8),
            })
        resolution_audit["valid_cross_section_count"] = len(
            valid_cross_sections)
        resolution_audit["valid_cross_section_widths_m"] = [
            row["width_m"] for row in valid_cross_sections]

        # A transverse cap/jamb connector spans the wall thickness rather
        # than following its centreline, so perpendicular samples run along
        # the wall and intentionally yield no bounded 60--600 mm section.
        # Give it an audit-only terminal disposition only when the complete
        # source segment is covered, both endpoints touch the proved wall-mask
        # boundary, its midpoint is inside the mask, and its length agrees
        # with an independently measured plan-wide wall width.
        endpoint_boundary_distances = [
            float(Point(point).distance(wall_mask.boundary))
            for point in source_line.coords]
        midpoint = source_line.interpolate(.5, normalized=True)
        midpoint_boundary_distance = float(
            midpoint.distance(wall_mask.boundary))
        reference_widths = []
        for key in ("inferred_single_run_width_m", "measured_spacing_p75_m"):
            try:
                value = float(topology_summary.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and .06 <= value <= .60:
                reference_widths.append(value)
        connector_reference_width = min(
            reference_widths,
            key=lambda value: abs(float(source_line.length) - value),
            default=float("nan"),
        )
        resolution_audit.update({
            "endpoint_wall_boundary_distances_m": [
                round(value, 8) for value in endpoint_boundary_distances],
            "midpoint_wall_boundary_distance_m": round(
                midpoint_boundary_distance, 8),
            "connector_reference_width_m": (
                round(connector_reference_width, 8)
                if math.isfinite(connector_reference_width) else None),
            "connector_reference_width_delta_m": (
                round(abs(float(source_line.length)
                          - connector_reference_width), 8)
                if math.isfinite(connector_reference_width) else None),
            "valid_cross_sections": copy.deepcopy(valid_cross_sections),
        })
        if (not valid_cross_sections
                and .06 <= float(source_line.length) <= .60 + 1e-9
                and math.isfinite(connector_reference_width)
                and abs(float(source_line.length) - connector_reference_width)
                <= .02 + 1e-9
                and max(endpoint_boundary_distances) <= .02 + 1e-9
                and midpoint_boundary_distance
                >= max(.025, float(source_line.length) * .20) - 1e-9):
            connector_proof = {
                "method": "proved_global_wall_transverse_connector_v1",
                "global_topology_method": str(
                    topology_summary.get("method") or ""),
                "global_topology_status": "proved",
                "global_topology_hash": topology_hash,
                "global_wall_footprint_ids": footprint_ids,
                "source_length_m": round(float(source_line.length), 8),
                "source_wall_mask_coverage_ratio": round(wall_coverage, 8),
                "endpoint_wall_boundary_distances_m": [
                    round(value, 8) for value in endpoint_boundary_distances],
                "midpoint_wall_boundary_distance_m": round(
                    midpoint_boundary_distance, 8),
                "reference_wall_width_m": round(
                    connector_reference_width, 8),
                "valid_perpendicular_cross_section_count": 0,
                "thresholds": {
                    "minimum_connector_length_m": .06,
                    "maximum_connector_length_m": .60,
                    "maximum_reference_width_delta_m": .02,
                    "maximum_endpoint_boundary_distance_m": .02,
                    "minimum_midpoint_boundary_distance_m": max(
                        .025, round(float(source_line.length) * .20, 8)),
                    "minimum_source_wall_mask_coverage_ratio": .995,
                },
                "decision_basis": [
                    "source_segment_fully_covered_by_proved_wall_mask",
                    "both_source_endpoints_touch_opposite_wall_boundaries",
                    "source_midpoint_lies_inside_wall_material",
                    "source_length_matches_independently_measured_wall_width",
                    "transverse_connector_is_audit_evidence_not_duplicate_wall",
                ],
            }
            assembly.update({
                "source_representation": "global_topology_connector_evidence",
                "resolved_as": "global_topology_connector_evidence",
                "review_status": "rejected", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": False,
                "footprint_polygon": None, "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_global_topology_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_short_wall_connector_consumed_by_proved_global_topology"])
                    if reason != "cad_wall_representation_unresolved")),
                "global_topology_connector_evidence": connector_proof,
            })
            resolution_audit.update(
                decision="resolved",
                reason="transverse_wall_connector_consumed_by_global_topology")
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "global_topology_connector_evidence"
            continue
        # Some plans explicitly draw the short terminal face of a wall band.
        # Topology closure can leave that source face tracking the final mask
        # boundary instead of spanning its interior.  It remains terminal audit
        # evidence when its full length matches an independently measured wall
        # width; it must not emit duplicate wall geometry.
        if (not valid_cross_sections
                and .06 <= float(source_line.length) <= .60 + 1e-9
                and math.isfinite(connector_reference_width)
                and abs(float(source_line.length) - connector_reference_width)
                <= .02 + 1e-9
                and max(endpoint_boundary_distances) <= .02 + 1e-9
                and midpoint_boundary_distance <= .02 + 1e-9):
            boundary_face_proof = {
                "method": "proved_global_wall_short_boundary_face_v1",
                "global_topology_method": str(
                    topology_summary.get("method") or ""),
                "global_topology_status": "proved",
                "global_topology_hash": topology_hash,
                "global_wall_footprint_ids": footprint_ids,
                "source_length_m": round(float(source_line.length), 8),
                "source_wall_mask_coverage_ratio": round(wall_coverage, 8),
                "endpoint_wall_boundary_distances_m": [
                    round(value, 8) for value in endpoint_boundary_distances],
                "midpoint_wall_boundary_distance_m": round(
                    midpoint_boundary_distance, 8),
                "reference_wall_width_m": round(
                    connector_reference_width, 8),
                "valid_perpendicular_cross_section_count": 0,
                "thresholds": {
                    "minimum_boundary_face_length_m": .06,
                    "maximum_boundary_face_length_m": .60,
                    "maximum_reference_width_delta_m": .02,
                    "maximum_endpoint_boundary_distance_m": .02,
                    "maximum_midpoint_boundary_distance_m": .02,
                    "minimum_source_wall_mask_coverage_ratio": .995,
                },
                "decision_basis": [
                    "source_segment_fully_covered_by_proved_wall_mask",
                    "source_segment_tracks_proved_wall_mask_boundary",
                    "source_length_matches_independently_measured_wall_width",
                    "short_wall_end_face_is_audit_evidence_not_duplicate_wall",
                ],
            }
            assembly.update({
                "source_representation": "global_topology_boundary_evidence",
                "resolved_as": "global_topology_boundary_evidence",
                "review_status": "rejected", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": False,
                "footprint_polygon": None, "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_global_topology_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_short_wall_boundary_face_consumed_by_proved_global_topology"])
                    if reason != "cad_wall_representation_unresolved")),
                "global_topology_boundary_evidence": boundary_face_proof,
            })
            resolution_audit.update(
                decision="resolved",
                reason="short_wall_boundary_face_consumed_by_global_topology")
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "global_topology_boundary_evidence"
            continue
        if source_line.length < .15 - 1e-9:
            resolution_audit.update(
                decision="unresolved",
                reason="short_source_connector_or_boundary_role_unproved",
                thresholds={
                    "minimum_longitudinal_source_length_m": .15,
                    "minimum_short_evidence_length_m": .06,
                    "maximum_short_evidence_length_m": .20,
                    "maximum_reference_width_delta_m": .02,
                })
            continue

        # At real wall corners a single CAD source stroke can follow the wall
        # centreline for part of its run and a measured boundary face for the
        # remainder.  Requiring one role for the entire stroke leaves such
        # junction evidence unresolved even though every sample is supported.
        # Accept the source as audit-only only when at least six spread samples
        # are independently classifiable as either a centred inferred-width
        # strip or a boundary face; one junction sample may be indeterminate.
        try:
            piecewise_inferred_width = float(
                topology_summary.get("inferred_single_run_width_m"))
        except (TypeError, ValueError):
            piecewise_inferred_width = float("nan")
        piecewise_sections = []
        for section in valid_cross_sections:
            width = float(section["width_m"])
            signed_min = float(section["signed_min_offset_m"])
            signed_max = float(section["signed_max_offset_m"])
            midpoint_offset = float(section["section_midpoint_offset_m"])
            source_role = ""
            if (math.isfinite(piecewise_inferred_width)
                    and .06 <= piecewise_inferred_width <= .20
                    and abs(width - piecewise_inferred_width) <= .02 + 1e-9
                    and abs(midpoint_offset) <= .015 + 1e-9):
                source_role = "centerline"
            elif (abs(signed_min) <= .015 + 1e-9
                  and signed_max >= .06 - 1e-9):
                source_role = "positive_boundary_face"
            elif (abs(signed_max) <= .015 + 1e-9
                  and signed_min <= -.06 + 1e-9):
                source_role = "negative_boundary_face"
            if source_role:
                piecewise_sections.append({
                    **copy.deepcopy(section), "source_role": source_role})
        piecewise_span = (
            float(piecewise_sections[-1]["fraction"])
            - float(piecewise_sections[0]["fraction"])
            if len(piecewise_sections) >= 2 else 0.0)
        piecewise_roles = {
            str(section.get("source_role") or "")
            for section in piecewise_sections
            if str(section.get("source_role") or "")}
        collinear_duplicate_source_ids = []
        if piecewise_roles == {"centerline"}:
            source_angle = math.degrees(math.atan2(dz, dx)) % 180.0
            for other in assemblies:
                other_id = str(other.get("id") or "")
                if not other_id or other_id == str(assembly.get("id") or ""):
                    continue
                raw_other = other.get("source_centerline") or []
                try:
                    other_line = LineString([
                        (float(raw_other[0][0]), float(raw_other[0][1])),
                        (float(raw_other[-1][0]), float(raw_other[-1][1])),
                    ])
                except (TypeError, ValueError, IndexError):
                    continue
                if other_line.length < .15 - 1e-9:
                    continue
                other_dx = (other_line.coords[-1][0]
                            - other_line.coords[0][0]) / other_line.length
                other_dz = (other_line.coords[-1][1]
                            - other_line.coords[0][1]) / other_line.length
                other_angle = math.degrees(math.atan2(
                    other_dz, other_dx)) % 180.0
                angle_difference = abs(source_angle - other_angle)
                angle_difference = min(angle_difference,
                                       180.0 - angle_difference)
                overlap = float(source_line.intersection(
                    other_line.buffer(.005, cap_style=2)).length)
                if (angle_difference <= 1.0 + 1e-9
                        and overlap / source_line.length >= .80 - 1e-9
                        and overlap / other_line.length >= .80 - 1e-9):
                    collinear_duplicate_source_ids.append(other_id)
        if (len(piecewise_sections) >= 6
                and len(valid_cross_sections) - len(piecewise_sections) <= 1
                and piecewise_span >= .50 - 1e-9
                and (len(piecewise_roles) >= 2
                     or len(collinear_duplicate_source_ids) == 1)):
            piecewise_proof = {
                "method": "proved_global_wall_piecewise_role_v1",
                "global_topology_method": str(
                    topology_summary.get("method") or ""),
                "global_topology_status": "proved",
                "global_topology_hash": topology_hash,
                "global_wall_footprint_ids": footprint_ids,
                "opening_host_wall_assembly_ids": sorted(
                    value for value in opening_host_ids if value),
                "source_length_m": round(float(source_line.length), 8),
                "source_wall_mask_coverage_ratio": round(wall_coverage, 8),
                "classified_cross_sections": piecewise_sections,
                "valid_cross_section_count": len(valid_cross_sections),
                "classified_cross_section_count": len(piecewise_sections),
                "classified_fraction_span": round(piecewise_span, 8),
                "source_roles": sorted(piecewise_roles),
                "collinear_duplicate_source_ids": sorted(
                    collinear_duplicate_source_ids),
                "inferred_single_run_width_m": (
                    round(piecewise_inferred_width, 8)
                    if math.isfinite(piecewise_inferred_width) else None),
                "thresholds": {
                    "minimum_source_length_m": .15,
                    "minimum_source_wall_mask_coverage_ratio": .995,
                    "minimum_classified_cross_section_count": 6,
                    "maximum_unclassified_cross_section_count": 1,
                    "minimum_classified_fraction_span": .50,
                    "minimum_collinear_duplicate_overlap_ratio": .80,
                    "maximum_centerline_width_delta_m": .02,
                    "maximum_centerline_midpoint_offset_m": .015,
                    "maximum_boundary_face_edge_offset_m": .015,
                },
                "decision_basis": [
                    "proved_whole_plan_source_wall_footprint",
                    "six_spread_source_cross_sections_have_wall_roles",
                    "each_sample_is_centerline_or_measured_boundary_face",
                    ("unique_collinear_duplicate_source"
                     if collinear_duplicate_source_ids
                     else "source_role_changes_at_wall_junction"),
                    "junction_role_transition_does_not_emit_duplicate_wall",
                ],
            }
            assembly.update({
                "source_representation": "global_topology_piecewise_evidence",
                "resolved_as": "global_topology_piecewise_evidence",
                "review_status": "rejected", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": False,
                "footprint_polygon": None, "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_global_topology_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_piecewise_wall_source_consumed_by_proved_global_topology"])
                    if reason != "cad_wall_representation_unresolved")),
                "global_topology_piecewise_evidence": piecewise_proof,
            })
            resolution_audit.update(
                decision="resolved",
                reason="piecewise_wall_role_consumed_by_global_topology")
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "global_topology_piecewise_evidence"
            continue
        best_sections = None
        best_section_score = None
        for first_index in range(len(valid_cross_sections)):
            for second_index in range(first_index + 1, len(valid_cross_sections)):
                for third_index in range(second_index + 1, len(valid_cross_sections)):
                    sections = [valid_cross_sections[first_index],
                                valid_cross_sections[second_index],
                                valid_cross_sections[third_index]]
                    if sections[-1]["fraction"] - sections[0]["fraction"] < .40 - 1e-9:
                        continue
                    section_widths = [float(row["width_m"]) for row in sections]
                    score = (max(section_widths) - min(section_widths),
                             -sum(section_widths), sections[0]["fraction"])
                    if best_section_score is None or score < best_section_score:
                        best_section_score = score
                        best_sections = sections
        if best_sections is None:
            resolution_audit.update(
                decision="unresolved",
                reason="three_spread_stable_cross_sections_unavailable")
            continue
        cross_sections = best_sections
        widths = [float(row["width_m"]) for row in cross_sections]
        width_delta = max(widths) - min(widths)
        if width_delta > .06 + 1e-9:
            resolution_audit.update(
                decision="unresolved",
                reason="cross_section_width_delta_above_threshold",
                maximum_cross_section_width_delta_m=round(width_delta, 8),
                threshold_m=.06)
            continue
        boundary_tolerance = max(widths) + .025
        boundary_coverage = float(source_line.intersection(
            space_boundary.buffer(boundary_tolerance, cap_style=2,
                                  join_style=2)).length) / source_line.length
        nearest_boundary_distance = float(source_line.distance(space_boundary))
        closed_space_supported = bool(
            boundary_coverage >= .80 - 1e-9
            and nearest_boundary_distance <= boundary_tolerance + 1e-9)

        wall_boundary_coverage = float(source_line.intersection(
            wall_mask.boundary.buffer(.015, cap_style=2, join_style=2)
        ).length) / source_line.length
        local_wall_boundary_coverage = float(source_line.intersection(
            wall_mask.boundary.buffer(.04, cap_style=2, join_style=2)
        ).length) / source_line.length
        try:
            inferred_single_width = float(
                topology_summary.get("inferred_single_run_width_m"))
            measured_spacing = float(topology_summary.get("measured_spacing_p75_m"))
        except (TypeError, ValueError):
            inferred_single_width = measured_spacing = float("nan")
        centered_strip = bool(
            math.isfinite(inferred_single_width)
            and .06 <= inferred_single_width <= .20
            and wall_boundary_coverage <= .20 + 1e-9
            and all(abs(float(row["section_midpoint_offset_m"])) <= .015 + 1e-9
                    and abs(float(row["width_m"]) - inferred_single_width)
                    <= .02 + 1e-9
                    for row in cross_sections))
        all_valid_widths = [float(row["width_m"])
                            for row in valid_cross_sections]
        local_width = statistics.median(all_valid_widths)
        independently_supported_local_centerline = bool(
            len(valid_cross_sections) >= 5
            and source_line.length >= .60 - 1e-9
            and wall_boundary_coverage <= .20 + 1e-9
            and max(all_valid_widths) - min(all_valid_widths) <= .02 + 1e-9
            and all(abs(float(row["section_midpoint_offset_m"]))
                    <= .015 + 1e-9 for row in valid_cross_sections)
            and math.isfinite(inferred_single_width)
            and local_width >= inferred_single_width + .03 - 1e-9
            and .09 <= local_width <= .60)
        wall_sides = []
        for row in cross_sections:
            signed_min = float(row["signed_min_offset_m"])
            signed_max = float(row["signed_max_offset_m"])
            if abs(signed_min) <= .015 + 1e-9 and signed_max >= .06 - 1e-9:
                wall_sides.append("positive")
            elif abs(signed_max) <= .015 + 1e-9 and signed_min <= -.06 + 1e-9:
                wall_sides.append("negative")
            else:
                wall_sides.append("")
        boundary_face_strip = bool(
            math.isfinite(measured_spacing)
            and .06 <= measured_spacing <= .60
            and wall_boundary_coverage >= .80 - 1e-9
            and len(set(wall_sides)) == 1 and wall_sides[0]
            and all(abs(float(row["width_m"]) - measured_spacing)
                    <= .06 + 1e-9 for row in cross_sections))
        local_wall_sides = []
        for row in valid_cross_sections:
            signed_min = float(row["signed_min_offset_m"])
            signed_max = float(row["signed_max_offset_m"])
            if abs(signed_min) <= .04 + 1e-9 and signed_max >= .09 - 1e-9:
                local_wall_sides.append("positive")
            elif abs(signed_max) <= .04 + 1e-9 and signed_min <= -.09 + 1e-9:
                local_wall_sides.append("negative")
            else:
                local_wall_sides.append("")
        independently_supported_local_boundary_face = bool(
            len(valid_cross_sections) >= 5
            and source_line.length >= .60 - 1e-9
            and local_wall_boundary_coverage >= .80 - 1e-9
            and max(all_valid_widths) - min(all_valid_widths) <= .02 + 1e-9
            and len(set(local_wall_sides)) == 1 and local_wall_sides[0]
            and .09 <= local_width <= .60)
        strip_role = (
            "inferred_single_run_centerline" if centered_strip
            else "independently_supported_local_centerline"
            if independently_supported_local_centerline
            else "measured_wall_boundary_face" if boundary_face_strip else "")
        if not strip_role and independently_supported_local_boundary_face:
            strip_role = "independently_supported_local_boundary_face"
        if not closed_space_supported and not strip_role:
            resolution_audit.update(
                decision="unresolved",
                reason="closed_space_boundary_or_strip_role_unproved",
                space_boundary_coverage_ratio=round(boundary_coverage, 8),
                nearest_space_boundary_distance_m=round(
                    nearest_boundary_distance, 8),
                wall_mask_boundary_coverage_ratio=round(
                    wall_boundary_coverage, 8),
                centered_strip=centered_strip,
                independently_supported_local_centerline=(
                    independently_supported_local_centerline),
                independently_supported_local_boundary_face=(
                    independently_supported_local_boundary_face),
                inferred_single_run_width_m=(
                    round(inferred_single_width, 8)
                    if math.isfinite(inferred_single_width) else None),
                local_cross_section_width_m=round(local_width, 8),
                boundary_face_strip=boundary_face_strip,
                local_wall_boundary_coverage_ratio=round(
                    local_wall_boundary_coverage, 8))
            continue

        proof = {
            "method": (
                "accepted_space_boundary_stable_wall_cross_section_v1"
                if closed_space_supported
                else "proved_global_wall_strip_role_v1"),
            "global_topology_method": str(topology_summary.get("method") or ""),
            "global_topology_status": "proved",
            "global_topology_hash": topology_hash,
            "global_wall_footprint_ids": footprint_ids,
            "opening_host_wall_assembly_ids": sorted(
                value for value in opening_host_ids if value),
            "source_length_m": round(float(source_line.length), 8),
            "source_wall_mask_coverage_ratio": round(wall_coverage, 8),
            "cross_sections": cross_sections,
            "maximum_cross_section_width_delta_m": round(width_delta, 8),
            "thresholds": {
                "minimum_source_length_m": .15,
                "minimum_source_wall_mask_coverage_ratio": .995,
                "minimum_space_boundary_coverage_ratio": .80,
                "minimum_wall_cross_section_width_m": .06,
                "maximum_wall_cross_section_width_m": .60,
                "maximum_cross_section_width_delta_m": .06,
            },
        }
        if closed_space_supported:
            proof.update({
                "space_boundary_coverage_ratio": round(boundary_coverage, 8),
                "nearest_space_boundary_distance_m": round(
                    nearest_boundary_distance, 8),
                "decision_basis": [
                    "proved_whole_plan_source_wall_footprint",
                    "three_stable_perpendicular_wall_cross_sections",
                    "source_run_tracks_closed_physical_space_boundary",
                    "global_footprint_is_geometry_authority_no_duplicate_wall_emitted",
                ],
            })
            proof["thresholds"].update({
                "minimum_space_boundary_coverage_ratio": .80,
                "space_boundary_distance_extra_m": .025,
            })
        else:
            reference_width = (
                inferred_single_width if centered_strip
                else local_width
                if strip_role in {
                    "independently_supported_local_centerline",
                    "independently_supported_local_boundary_face"}
                else measured_spacing)
            proof.update({
                "strip_role": strip_role,
                "reference_width_m": round(reference_width, 8),
                "wall_mask_boundary_coverage_ratio": round(
                    wall_boundary_coverage, 8),
                "local_wall_boundary_coverage_ratio": round(
                    local_wall_boundary_coverage, 8),
                "consistent_wall_side": (
                    local_wall_sides[0]
                    if strip_role
                    == "independently_supported_local_boundary_face"
                    else wall_sides[0] if boundary_face_strip else "centered"),
                "independent_cross_sections": (
                    copy.deepcopy(valid_cross_sections)
                    if strip_role in {
                        "independently_supported_local_centerline",
                        "independently_supported_local_boundary_face"}
                    else []),
                "decision_basis": [
                    "proved_whole_plan_source_wall_footprint",
                    "three_stable_perpendicular_wall_cross_sections",
                    ("source_run_centered_in_locally_inferred_single_wall_strip"
                     if centered_strip else
                     "five_stable_sections_prove_independent_local_wall_width"
                     if strip_role
                     == "independently_supported_local_centerline" else
                     "five_stable_sections_prove_independent_local_wall_boundary_face"
                     if strip_role
                     == "independently_supported_local_boundary_face" else
                     "source_run_is_consistent_measured_wall_strip_boundary_face"),
                    "global_footprint_is_geometry_authority_no_duplicate_wall_emitted",
                ],
            })
            proof["thresholds"].update({
                "maximum_centerline_midpoint_offset_m": .015,
                "maximum_boundary_face_edge_offset_m": .015,
                "maximum_local_boundary_face_edge_offset_m": .04,
                "maximum_centerline_reference_width_delta_m": .02,
                "minimum_independent_local_width_delta_m": .03,
                "minimum_independent_cross_section_count": 5,
                "maximum_boundary_face_reference_width_delta_m": .06,
                "minimum_boundary_face_wall_mask_boundary_coverage_ratio": .80,
                "minimum_local_boundary_face_wall_mask_boundary_coverage_ratio": .80,
                "maximum_centerline_wall_mask_boundary_coverage_ratio": .20,
            })
        assembly.update({
            "source_representation": "global_topology_evidence",
            "resolved_as": "global_topology_evidence",
            "review_status": "rejected", "confidence_grade": "A",
            "confidence": 1.0, "legacy_wall_compatible": False,
            "footprint_polygon": None, "centerline": None,
            "thickness_m": None,
            "thickness_source": "not_applicable_global_topology_evidence",
            "production_blockers": [],
            "reason_codes": sorted(set(
                reason for reason in (
                    (assembly.get("reason_codes") or [])
                    + ["cad_wall_source_consumed_by_proved_global_topology"])
                if reason != "cad_wall_representation_unresolved")),
            "global_topology_evidence": proof,
        })
        resolution_audit.update(
            decision="resolved",
            reason="source_consumed_by_proved_global_topology")
        provenance = assembly.get("cad_provenance")
        if isinstance(provenance, dict):
            provenance["wall_assembly_source_representation"] = \
                "global_topology_evidence"

    accepted_supports = []
    extension_supports = []
    for assembly in result:
        if str(assembly.get("review_status") or "") not in {"accepted", "confirmed"}:
            continue
        try:
            polygon = Polygon([(float(point[0]), float(point[1]))
                               for point in assembly.get("footprint_polygon") or []])
        except (TypeError, ValueError, IndexError):
            continue
        try:
            support_axis = LineString(assembly.get("centerline") or [])
            support_angle = math.degrees(math.atan2(
                support_axis.coords[-1][1] - support_axis.coords[0][1],
                support_axis.coords[-1][0] - support_axis.coords[0][0])) % 180.0
        except (TypeError, ValueError, IndexError):
            continue
        if (polygon.is_valid and not polygon.is_empty
                and support_axis.length > 1e-9):
            support_row = (assembly, polygon, support_angle)
            extension_supports.append(support_row)
            if str(assembly.get("source_representation") or "") \
                    not in opening_host_representations:
                accepted_supports.append(support_row)
    pending_indexes = [
        index for index, assembly in enumerate(result)
        if str(assembly.get("review_status") or "")
        not in {"accepted", "confirmed", "rejected", "reject"}
    ]
    corner_matches: dict[int, list[dict]] = defaultdict(list)
    for left_position, left_index in enumerate(pending_indexes):
        left = result[left_index]
        try:
            left_line = LineString(left.get("source_centerline") or [])
        except (TypeError, ValueError):
            continue
        if not .10 <= left_line.length <= .80:
            continue
        for right_index in pending_indexes[left_position + 1:]:
            right = result[right_index]
            try:
                right_line = LineString(right.get("source_centerline") or [])
            except (TypeError, ValueError):
                continue
            if not .10 <= right_line.length <= .80:
                continue
            left_endpoints = [tuple(left_line.coords[0]), tuple(left_line.coords[-1])]
            right_endpoints = [tuple(right_line.coords[0]), tuple(right_line.coords[-1])]
            endpoint_pairs = sorted(
                (math.dist(left_point, right_point), left_endpoint, right_endpoint)
                for left_endpoint, left_point in enumerate(left_endpoints)
                for right_endpoint, right_point in enumerate(right_endpoints))
            shared_distance, left_shared, right_shared = endpoint_pairs[0]
            if shared_distance > .02 + 1e-9:
                continue
            left_angle = math.degrees(math.atan2(
                left_line.coords[-1][1] - left_line.coords[0][1],
                left_line.coords[-1][0] - left_line.coords[0][0])) % 180.0
            right_angle = math.degrees(math.atan2(
                right_line.coords[-1][1] - right_line.coords[0][1],
                right_line.coords[-1][0] - right_line.coords[0][0])) % 180.0
            angle_difference = abs(left_angle - right_angle)
            angle_difference = min(angle_difference, 180.0 - angle_difference)
            if angle_difference < 87.0 - 1e-9:
                continue
            free_points = [left_endpoints[1 - left_shared],
                           right_endpoints[1 - right_shared]]
            source_angles = [left_angle, right_angle]
            supports = []
            for endpoint_index, point in enumerate(free_points):
                matches = []
                for support_assembly, polygon, support_angle in accepted_supports:
                    endpoint_distance = float(polygon.distance(Point(point)))
                    support_axis_difference = abs(
                        source_angles[endpoint_index] - support_angle)
                    support_axis_difference = min(
                        support_axis_difference, 180.0 - support_axis_difference)
                    if (endpoint_distance <= .02 + 1e-9
                            and support_axis_difference <= 1.5 + 1e-9):
                        matches.append((
                            endpoint_distance,
                            str(support_assembly.get("id") or ""),
                            support_axis_difference,
                        ))
                matches.sort()
                if not matches:
                    supports = []
                    break
                best_distance = matches[0][0]
                best_ids = sorted({identifier for distance, identifier, _ in matches
                                   if abs(distance - best_distance) <= .002 + 1e-9})
                if len(best_ids) != 1:
                    supports = []
                    break
                support_axis_difference = min(
                    difference for distance, identifier, difference in matches
                    if identifier == best_ids[0]
                    and abs(distance - best_distance) <= .002 + 1e-9)
                supports.append({
                    "endpoint_index": endpoint_index,
                    "wall_assembly_id": best_ids[0],
                    "endpoint_distance_m": round(best_distance, 8),
                    "source_to_support_axis_angle_difference_deg": round(
                        support_axis_difference, 8),
                })
            if (len(supports) != 2
                    or len({row["wall_assembly_id"] for row in supports}) != 2):
                continue
            coverages = [
                float(line.intersection(wall_mask.buffer(.015)).length) / line.length
                for line in (left_line, right_line)
            ]
            if min(coverages) < .995 - 1e-9:
                continue
            handles = sorted({
                str(value) for assembly in (left, right)
                for value in assembly.get("source_entity_handles") or [] if str(value)
            })
            proof = {
                "support_method": "proved_global_topology_corner_chain_v1",
                "supports": supports,
                "chain_source_handles": handles,
                "shared_endpoint_distance_m": round(shared_distance, 8),
                "axis_angle_difference_deg": round(angle_difference, 8),
                "source_wall_mask_coverage_ratios": [round(value, 8)
                                                     for value in coverages],
                "global_topology_hash": topology_hash,
                "coverage_ratio": 0.0,
                "uncovered_length_m": round(left_line.length + right_line.length, 8),
                "thresholds": {
                    "maximum_shared_endpoint_distance_m": .02,
                    "minimum_axis_angle_difference_deg": 87.0,
                    "maximum_free_endpoint_support_distance_m": .02,
                    "maximum_source_to_support_axis_angle_difference_deg": 1.5,
                    "minimum_wall_mask_coverage_ratio": .995,
                    "maximum_chain_segment_length_m": .80,
                },
                "decision_basis": [
                    "two_source_segments_form_one_right_angle_corner_chain",
                    "each_source_segment_is_collinear_with_its_unique_local_wall_support",
                    "two_distinct_accepted_wall_free_endpoint_supports",
                    "proved_global_wall_mask_full_chain_coverage",
                ],
            }
            match = {"indexes": (left_index, right_index), "proof": proof}
            corner_matches[left_index].append(match)
            corner_matches[right_index].append(match)
    selected_corner_keys = set()
    for index in pending_indexes:
        matches = corner_matches.get(index) or []
        if len(matches) != 1:
            continue
        match = matches[0]
        key = tuple(match["indexes"])
        if key in selected_corner_keys:
            continue
        if any(len(corner_matches.get(member) or []) != 1 for member in key):
            continue
        selected_corner_keys.add(key)
        for member in key:
            assembly = result[member]
            assembly.update({
                "source_representation": "junction_evidence",
                "resolved_as": "junction_evidence",
                "review_status": "rejected", "confidence_grade": "A",
                "confidence": 1.0, "legacy_wall_compatible": False,
                "footprint_polygon": None, "centerline": None,
                "thickness_m": None,
                "thickness_source": "not_applicable_junction_evidence",
                "production_blockers": [],
                "reason_codes": sorted(set(
                    reason for reason in (
                        (assembly.get("reason_codes") or [])
                        + ["cad_wall_source_is_transverse_cap_or_junction"])
                    if reason != "cad_wall_representation_unresolved")),
                "junction_evidence": copy.deepcopy(match["proof"]),
            })
            provenance = assembly.get("cad_provenance")
            if isinstance(provenance, dict):
                provenance["wall_assembly_source_representation"] = \
                    "junction_evidence"

    # A source wall face may continue a short distance beyond the measured
    # overlap of one accepted wall (or opening host) and terminate at a global
    # wall corner.  It is not a second wall centreline.  Resolve only the
    # unique case where the source run lies on the accepted support's measured
    # face, begins no farther than one support thickness beyond its terminal,
    # and ends at a one-way boundary corner of the proved global wall mask.
    remaining_indexes = [
        index for index, assembly in enumerate(result)
        if str(assembly.get("review_status") or "")
        not in {"accepted", "confirmed", "rejected", "reject"}
    ]
    for index in remaining_indexes:
        assembly = result[index]
        try:
            source_line = LineString(assembly.get("source_centerline") or [])
        except (TypeError, ValueError):
            continue
        if not .10 <= source_line.length <= .80:
            continue
        source_coverage = float(source_line.intersection(
            wall_mask.buffer(.015, join_style=2)).length) / source_line.length
        source_boundary_coverage = float(source_line.intersection(
            wall_mask.boundary.buffer(.015, cap_style=2, join_style=2)
        ).length) / source_line.length
        if (source_coverage < .995 - 1e-9
                or source_boundary_coverage < .80 - 1e-9):
            continue
        source_dx = (source_line.coords[-1][0] - source_line.coords[0][0]) \
            / source_line.length
        source_dz = (source_line.coords[-1][1] - source_line.coords[0][1]) \
            / source_line.length
        source_angle = math.degrees(math.atan2(source_dz, source_dx)) % 180.0
        matches = []
        for support, support_polygon, support_angle in extension_supports:
            try:
                support_axis = LineString(support.get("centerline") or [])
                thickness = float(support.get("thickness_m"))
            except (TypeError, ValueError):
                continue
            if not (.06 <= thickness <= .60 and support_axis.length > 1e-9):
                continue
            angle_difference = abs(source_angle - support_angle)
            angle_difference = min(angle_difference, 180.0 - angle_difference)
            if angle_difference > 1.5 + 1e-9:
                continue
            support_dx = (support_axis.coords[-1][0] - support_axis.coords[0][0]) \
                / support_axis.length
            support_dz = (support_axis.coords[-1][1] - support_axis.coords[0][1]) \
                / support_axis.length
            origin = support_axis.coords[0]

            def projection(point: Sequence[float]) -> float:
                return ((float(point[0]) - float(origin[0])) * support_dx
                        + (float(point[1]) - float(origin[1])) * support_dz)

            def face_offset(point: Sequence[float]) -> float:
                return abs((float(point[0]) - float(origin[0])) * (-support_dz)
                           + (float(point[1]) - float(origin[1])) * support_dx)

            offsets = [face_offset(source_line.coords[0]),
                       face_offset(source_line.coords[-1])]
            measured_face_offset = sum(offsets) / 2.0
            expected_face_offset = thickness / 2.0
            if (max(offsets) - min(offsets) > .015 + 1e-9
                    or abs(measured_face_offset - expected_face_offset)
                    > .02 + 1e-9):
                continue
            support_interval = sorted([
                projection(support_axis.coords[0]),
                projection(support_axis.coords[-1]),
            ])
            source_projections = [projection(source_line.coords[0]),
                                  projection(source_line.coords[-1])]
            source_interval = sorted(source_projections)
            if source_interval[0] > support_interval[1]:
                axial_gap = source_interval[0] - support_interval[1]
                support_endpoint_index = source_projections.index(source_interval[0])
            elif support_interval[0] > source_interval[1]:
                axial_gap = support_interval[0] - source_interval[1]
                support_endpoint_index = source_projections.index(source_interval[1])
            else:
                axial_gap = 0.0
                support_endpoint_index = min(
                    range(2), key=lambda endpoint_index: Point(
                        source_line.coords[endpoint_index]).distance(support_polygon))
            if axial_gap > thickness + .02 + 1e-9:
                continue
            support_endpoint_distance = float(Point(
                source_line.coords[support_endpoint_index]).distance(support_polygon))
            if support_endpoint_distance > thickness + .02 + 1e-9:
                continue
            terminal_endpoint_index = 1 - support_endpoint_index
            terminal = Point(source_line.coords[terminal_endpoint_index])
            if float(terminal.distance(wall_mask.boundary)) > .015 + 1e-9:
                continue
            outward_sign = 1.0 if terminal_endpoint_index == 1 else -1.0
            forward_outside = [not wall_mask.buffer(1e-8).covers(Point(
                terminal.x + source_dx * outward_sign * distance,
                terminal.y + source_dz * outward_sign * distance,
            )) for distance in (.03, .06)]
            normal_inside = [wall_mask.buffer(1e-8).covers(Point(
                terminal.x + (-source_dz) * sign * .06,
                terminal.y + source_dx * sign * .06,
            )) for sign in (-1.0, 1.0)]
            if not all(forward_outside) or not any(normal_inside):
                continue
            matches.append({
                "wall_assembly_id": str(support.get("id") or ""),
                "source_to_support_axis_angle_difference_deg": round(
                    angle_difference, 8),
                "support_wall_thickness_m": round(thickness, 8),
                "measured_face_offset_m": round(measured_face_offset, 8),
                "expected_half_thickness_offset_m": round(
                    expected_face_offset, 8),
                "face_offset_delta_m": round(
                    abs(measured_face_offset - expected_face_offset), 8),
                "axial_gap_m": round(axial_gap, 8),
                "support_source_endpoint_index": support_endpoint_index,
                "support_endpoint_distance_m": round(
                    support_endpoint_distance, 8),
                "terminal_source_endpoint_index": terminal_endpoint_index,
                "terminal_global_boundary_distance_m": round(
                    float(terminal.distance(wall_mask.boundary)), 8),
                "terminal_forward_outside_samples": forward_outside,
                "terminal_normal_inside_sample_count": sum(normal_inside),
            })
        unique_matches = {
            row["wall_assembly_id"]: row for row in matches
            if row.get("wall_assembly_id")
        }
        if len(unique_matches) != 1:
            continue
        support = next(iter(unique_matches.values()))
        proof = {
            "support_method": "accepted_wall_face_global_corner_extension_v1",
            "supports": [support],
            "source_length_m": round(float(source_line.length), 8),
            "source_wall_mask_coverage_ratio": round(source_coverage, 8),
            "source_wall_mask_boundary_coverage_ratio": round(
                source_boundary_coverage, 8),
            "global_topology_hash": topology_hash,
            "coverage_ratio": 0.0,
            "uncovered_length_m": round(float(source_line.length), 8),
            "thresholds": {
                "minimum_source_length_m": .10,
                "maximum_source_length_m": .80,
                "maximum_axis_angle_difference_deg": 1.5,
                "maximum_face_offset_delta_m": .02,
                "maximum_axial_gap_extra_m": .02,
                "minimum_wall_mask_coverage_ratio": .995,
                "minimum_wall_mask_boundary_coverage_ratio": .80,
                "maximum_terminal_global_boundary_distance_m": .015,
            },
            "decision_basis": [
                "unique_accepted_wall_or_opening_host_support",
                "source_run_collinear_with_measured_support_outer_face",
                "bounded_terminal_gap_after_support_axis",
                "terminal_is_one_way_proved_global_wall_corner",
            ],
        }
        assembly.update({
            "source_representation": "junction_evidence",
            "resolved_as": "junction_evidence",
            "review_status": "rejected", "confidence_grade": "A",
            "confidence": 1.0, "legacy_wall_compatible": False,
            "footprint_polygon": None, "centerline": None,
            "thickness_m": None,
            "thickness_source": "not_applicable_junction_evidence",
            "production_blockers": [],
            "reason_codes": sorted(set(
                reason for reason in (
                    (assembly.get("reason_codes") or [])
                    + ["cad_wall_source_is_transverse_cap_or_junction"])
                if reason != "cad_wall_representation_unresolved")),
            "junction_evidence": proof,
        })
        provenance = assembly.get("cad_provenance")
        if isinstance(provenance, dict):
            provenance["wall_assembly_source_representation"] = \
                "junction_evidence"
    return result


def _prune_topology_invariant_projected_detail(
    selected_rows: Sequence[Mapping[str, Any]],
    wall_assemblies: Sequence[Mapping[str, Any]],
    topology_result: Mapping[str, Any],
    projected_plan_structure_filter: Mapping[str, Any],
    *,
    opening_candidates: Sequence[Mapping[str, Any]] = (),
    semantic_anchors: Sequence[Mapping[str, Any]] = (),
    origin_x: float = 0.0,
    origin_z: float = 0.0,
    wall_height_m: float = 2.8,
    _additional_micro_source_indexes: Optional[set[int]] = None,
    _dependency_retry_depth: int = 0,
    _forced_excluded_source_indexes: Optional[set[int]] = None,
) -> dict:
    """Remove unresolved projected ink only after a topology-invariance trial.

    The first global pass can prove the physical spaces while still leaving a
    bounded set of furniture/detail segments pending.  For the narrow text-free
    dense-plan contract, retry without exactly those pending source rows.  The
    retry is accepted only when all physical spaces match almost exactly and
    the rebuilt assembly set has no unresolved decisions.  This is not a
    generic "ignore warnings" path: a changed/missing room, a material wall
    area change, or any new ambiguity rejects the entire trial.
    """
    result: dict[str, Any] = {
        "schema_version": 1,
        "method": "cad_projected_detail_topology_invariance_v1",
        "status": "not_applicable",
    }
    if projected_plan_structure_filter.get("status") != "proved":
        result["reason"] = "projected_plan_structure_not_proved"
        return result
    unresolved = [
        copy.deepcopy(dict(row)) for row in wall_assemblies
        if str(row.get("review_status") or "")
        not in {"accepted", "confirmed", "rejected", "reject"}
    ]
    if not unresolved:
        result["reason"] = "no_unresolved_projected_geometry"
        return result
    allowed_source_representations = {
        "human_confirmed_ambiguous",
        "closed_footprint",
        "invalid_closed_footprint",
    }
    if any(
            str(row.get("source_representation") or "")
            not in allowed_source_representations
            for row in unresolved):
        result.update(
            reason="unsupported_unresolved_source_representation",
            unsupported_source_representations=sorted({
                str(row.get("source_representation") or "")
                for row in unresolved
                if str(row.get("source_representation") or "")
                not in allowed_source_representations
            }),
        )
        return result
    unresolved_by_source_index: dict[int, list[dict]] = defaultdict(list)
    for assembly in unresolved:
        source_entities = assembly.get("source_entities") or []
        if len(source_entities) != 1:
            result.update(
                reason="unresolved_geometry_is_not_single_source_entity",
                assembly_id=str(assembly.get("id") or ""),
                source_entity_count=len(source_entities),
            )
            return result
        try:
            source_index = int(source_entities[0]["entity_index"])
            unresolved_by_source_index[source_index].append(assembly)
        except (KeyError, TypeError, ValueError):
            result.update(
                reason="unresolved_source_entity_index_invalid",
                assembly_id=str(assembly.get("id") or ""),
            )
            return result
    all_unresolved_indexes = set(unresolved_by_source_index)
    selected_entity_indexes = {
        int(row.get("entity_index", -1)) for row in selected_rows
    }
    if not all_unresolved_indexes.issubset(selected_entity_indexes):
        result.update(
            reason="unresolved_source_entity_not_in_selected_geometry",
            missing_source_entity_indexes=sorted(
                all_unresolved_indexes - selected_entity_indexes)[:100],
        )
        return result
    input_count = len(selected_rows)
    all_unresolved_ratio = len(all_unresolved_indexes) / max(input_count, 1)
    direct_multiview_plan = (
        str(projected_plan_structure_filter.get("authority_proof_method") or "")
        == "cad_multi_view_orthographic_plan_view_v1")
    maximum_excluded_count = 250 if direct_multiview_plan else 100
    maximum_excluded_ratio = .20 if direct_multiview_plan else .15
    if not (1 <= len(all_unresolved_indexes) <= maximum_excluded_count
            and all_unresolved_ratio <= maximum_excluded_ratio + 1e-9):
        result.update(status="unresolved", reason="excluded_detail_scope_too_large")
        return result

    pruning_mode = "complete_unresolved_scope"
    excluded_indexes = set(all_unresolved_indexes)
    dependency_micro_indexes: set[int] = set()
    standard_scope = (
        len(all_unresolved_indexes) <= 100
        and all_unresolved_ratio <= .15 + 1e-9)
    if _forced_excluded_source_indexes is not None:
        forced_indexes = set(_forced_excluded_source_indexes)
        if not forced_indexes.issubset(all_unresolved_indexes):
            result.update(
                status="unresolved",
                reason="forced_excluded_scope_not_pending",
                non_pending_source_entity_indexes=sorted(
                    forced_indexes - all_unresolved_indexes)[:100],
            )
            return result
        excluded_indexes = forced_indexes
        pruning_mode = "counterfactual_forced_subset_scope"
    elif direct_multiview_plan and not standard_scope:
        def unresolved_source_length(assembly: Mapping[str, Any]) -> float:
            audit = assembly.get("global_topology_resolution_audit") or {}
            try:
                length = float(audit.get("source_length_m") or 0.0)
            except (TypeError, ValueError):
                length = 0.0
            if length > 0:
                return length
            centerline = assembly.get("source_centerline") or []
            try:
                return math.dist(
                    (float(centerline[0][0]), float(centerline[0][1])),
                    (float(centerline[-1][0]), float(centerline[-1][1])),
                )
            except (TypeError, ValueError, IndexError):
                return 0.0

        unresolved_micro_indexes = {
            source_index
            for source_index, decisions in unresolved_by_source_index.items()
            if all(
                str(decision.get("source_representation") or "")
                == "human_confirmed_ambiguous"
                and 0.0 < unresolved_source_length(decision) <= .075 + 1e-9
                for decision in decisions
            )
        }
        selected_source_lengths = {}
        for position, row in enumerate(selected_rows):
            points = row.get("points") or []
            try:
                source_length = sum(
                    math.dist(
                        (float(first[0]), float(first[1])),
                        (float(second[0]), float(second[1])),
                    )
                    for first, second in zip(points, points[1:])
                )
                source_index = int(row.get("entity_index", position))
            except (TypeError, ValueError, IndexError):
                continue
            selected_source_lengths[source_index] = source_length
        dependency_micro_indexes = {
            index for index, source_length in selected_source_lengths.items()
            if 0.0 < source_length <= .075 + 1e-9
        }
        additional_micro_indexes = set(
            _additional_micro_source_indexes or set())
        micro_detail_indexes = (
            unresolved_micro_indexes
            | (additional_micro_indexes & dependency_micro_indexes))
        if micro_detail_indexes:
            excluded_indexes = micro_detail_indexes
            pruning_mode = "sub_75mm_micro_detail_partial_scope"
    excluded_ratio = len(excluded_indexes) / max(input_count, 1)
    if (len(excluded_indexes) > maximum_excluded_count
            or excluded_ratio > maximum_excluded_ratio + 1e-9):
        result.update(
            status="unresolved",
            reason="expanded_micro_detail_scope_too_large",
            excluded_entity_count=len(excluded_indexes),
            excluded_entity_ratio=round(excluded_ratio, 8),
        )
        return result
    expected_remaining_unresolved_indexes = (
        all_unresolved_indexes - excluded_indexes)
    trial_rows = [
        copy.deepcopy(dict(row)) for row in selected_rows
        if int(row.get("entity_index", -1)) not in excluded_indexes
    ]
    original_raw_spaces = list(topology_result.get("_space_polygons") or [])
    original_summary = topology_result.get("summary") or {}
    if (not original_raw_spaces
            or str(original_summary.get("status") or "") != "proved"):
        result["reason"] = "original_topology_not_proved"
        return result
    _, original_physical_faces = classify_raw_faces(
        original_raw_spaces,
        origin_x=origin_x, origin_z=origin_z,
        text_anchors=[copy.deepcopy(dict(row))
                      for row in semantic_anchors],
    )
    if original_physical_faces:
        original_spaces = [row["shape"] for row in original_physical_faces]
        space_comparison_scope = "classified_physical_spaces"
    else:
        # A source without any classifiable physical faces receives the older,
        # stricter raw-topology comparison.  An empty semantic result must
        # never become permission to discard all source regions.
        original_spaces = original_raw_spaces
        space_comparison_scope = "raw_topology_spaces_fallback"
    try:
        from shapely.ops import unary_union  # type: ignore

        trial_assemblies = build_wall_assemblies(
            trial_rows, wall_height_m=wall_height_m,
            height_source="project_default_assumption",
            origin_x=origin_x, origin_z=origin_z,
        )
        trial_topology = build_global_wall_topology(
            trial_rows, wall_assemblies=trial_assemblies,
            opening_candidates=opening_candidates,
            semantic_anchors=semantic_anchors,
            origin_x=origin_x, origin_z=origin_z,
            wall_height_m=wall_height_m,
        )
        trial_raw_spaces = list(trial_topology.get("_space_polygons") or [])
        trial_summary = trial_topology.get("summary") or {}
        if str(trial_summary.get("status") or "") != "proved":
            result.update(
                status="unresolved",
                reason="trial_topology_unproved",
                original_space_count=len(original_spaces),
                original_raw_topology_space_count=len(original_raw_spaces),
                trial_raw_topology_space_count=len(trial_raw_spaces),
                trial_topology_status=str(
                    trial_summary.get("status") or ""),
            )
            return result
        _, trial_physical_faces = classify_raw_faces(
            trial_raw_spaces,
            origin_x=origin_x, origin_z=origin_z,
            text_anchors=[copy.deepcopy(dict(row))
                          for row in semantic_anchors],
        )
        trial_spaces = (
            [row["shape"] for row in trial_physical_faces]
            if space_comparison_scope == "classified_physical_spaces"
            else trial_raw_spaces)
        if len(trial_spaces) != len(original_spaces):
            result.update(
                status="unresolved",
                reason="trial_physical_space_count_changed",
                space_comparison_scope=space_comparison_scope,
                original_space_count=len(original_spaces),
                trial_space_count=len(trial_spaces),
                original_raw_topology_space_count=len(original_raw_spaces),
                trial_raw_topology_space_count=len(trial_raw_spaces),
                trial_topology_status=str(
                    trial_summary.get("status") or ""),
            )
            return result
        original_union = unary_union(original_spaces)
        trial_union = unary_union(trial_spaces)
        union_area = float(original_union.union(trial_union).area)
        union_iou = (float(original_union.intersection(trial_union).area)
                     / max(union_area, 1e-12))
        matched_ious = [
            max(
                float(space.intersection(candidate).area)
                / max(float(space.union(candidate).area), 1e-12)
                for candidate in trial_spaces
            )
            for space in original_spaces
        ]
        reverse_matched_ious = [
            max(
                float(space.intersection(candidate).area)
                / max(float(space.union(candidate).area), 1e-12)
                for candidate in original_spaces
            )
            for space in trial_spaces
        ]
        minimum_space_iou = min([*matched_ious, *reverse_matched_ious])
        original_wall_area = float(original_summary.get("wall_area_m2") or 0.0)
        trial_wall_area = float(trial_summary.get("wall_area_m2") or 0.0)
        wall_area_reduction = (
            (original_wall_area - trial_wall_area)
            / max(original_wall_area, 1e-12))
        if (union_iou < .995 - 1e-9
                or minimum_space_iou < .99 - 1e-9
                or wall_area_reduction < -1e-9
                or wall_area_reduction > .05 + 1e-9):
            result.update(
                status="unresolved",
                reason="trial_physical_topology_changed",
                space_comparison_scope=space_comparison_scope,
                original_space_count=len(original_spaces),
                trial_space_count=len(trial_spaces),
                original_raw_topology_space_count=len(original_raw_spaces),
                trial_raw_topology_space_count=len(trial_raw_spaces),
                space_union_iou=round(union_iou, 8),
                minimum_matched_space_iou=round(minimum_space_iou, 8),
                wall_area_reduction_ratio=round(wall_area_reduction, 8),
            )
            return result
        trial_assemblies = _resolve_wall_evidence_with_global_topology(
            trial_assemblies, trial_topology["wall_footprints"],
            trial_raw_spaces,
            trial_summary, origin_x=origin_x, origin_z=origin_z,
        )
        trial_assemblies = (
            _resolve_wall_evidence_coincident_with_accepted_openings(
                trial_assemblies, opening_candidates,
                origin_x=origin_x, origin_z=origin_z))
        trial_unresolved = [
            row for row in trial_assemblies
            if str(row.get("review_status") or "")
            not in {"accepted", "confirmed", "rejected", "reject"}
        ]
        trial_unresolved_indexes = {
            int(source_entity.get("entity_index"))
            for assembly in trial_unresolved
            for source_entity in assembly.get("source_entities") or []
            if (isinstance(source_entity, Mapping)
                and (isinstance(source_entity.get("entity_index"), int)
                     or str(source_entity.get("entity_index") or "").isdigit()))
        }
        removed_source_indexes_still_unresolved = (
            trial_unresolved_indexes & excluded_indexes)
        new_unresolved_source_indexes = (
            trial_unresolved_indexes - expected_remaining_unresolved_indexes)
        eligible_dependency_indexes = (
            (new_unresolved_source_indexes & dependency_micro_indexes)
            - set(_additional_micro_source_indexes or set()))
        if (not removed_source_indexes_still_unresolved
                and eligible_dependency_indexes
                and _dependency_retry_depth < 3):
            dependency_indexes = (
                set(_additional_micro_source_indexes or set())
                | eligible_dependency_indexes)
            retried = _prune_topology_invariant_projected_detail(
                selected_rows, wall_assemblies, topology_result,
                projected_plan_structure_filter,
                opening_candidates=opening_candidates,
                semantic_anchors=semantic_anchors,
                origin_x=origin_x, origin_z=origin_z,
                wall_height_m=wall_height_m,
                _additional_micro_source_indexes=dependency_indexes,
                _dependency_retry_depth=_dependency_retry_depth + 1,
            )
            retried["dependency_closure_retry_count"] = max(
                int(retried.get("dependency_closure_retry_count") or 0),
                _dependency_retry_depth + 1,
            )
            retried["dependency_closure_source_indexes"] = sorted(
                dependency_indexes)
            return retried
        if (removed_source_indexes_still_unresolved
                or new_unresolved_source_indexes
                or (pruning_mode == "complete_unresolved_scope"
                    and trial_unresolved)):
            result.update(
                status="unresolved",
                reason="trial_wall_assembly_decisions_remain_unresolved",
                trial_unresolved_count=len(trial_unresolved),
                removed_source_indexes_still_unresolved=sorted(
                    removed_source_indexes_still_unresolved)[:100],
                new_unresolved_source_indexes=sorted(
                    new_unresolved_source_indexes)[:100],
            )
            return result
        terminal_source_entities: dict[int, list[dict]] = defaultdict(list)
        for assembly in wall_assemblies:
            if str(assembly.get("review_status") or "") in {
                    "rejected", "reject"}:
                continue
            for source_entity in assembly.get("source_entities") or []:
                if not isinstance(source_entity, Mapping):
                    continue
                try:
                    source_index = int(source_entity.get("entity_index"))
                except (TypeError, ValueError):
                    continue
                if source_index not in excluded_indexes:
                    continue
                record = copy.deepcopy(dict(source_entity))
                if record not in terminal_source_entities[source_index]:
                    terminal_source_entities[source_index].append(record)
        proof = {
            "method": "cad_projected_detail_topology_invariance_v1",
            "space_comparison_scope": space_comparison_scope,
            "original_raw_topology_space_count": len(original_raw_spaces),
            "trial_raw_topology_space_count": len(trial_raw_spaces),
            "original_space_count": len(original_spaces),
            "trial_space_count": len(trial_spaces),
            "space_union_iou": round(union_iou, 8),
            "minimum_matched_space_iou": round(minimum_space_iou, 8),
            "original_wall_area_m2": round(original_wall_area, 8),
            "trial_wall_area_m2": round(trial_wall_area, 8),
            "wall_area_reduction_ratio": round(wall_area_reduction, 8),
            "input_entity_count": input_count,
            "opening_barrier_count": sum(
                str(row.get("status") or "") in {"accepted", "confirmed"}
                for row in opening_candidates),
            "excluded_entity_count": len(excluded_indexes),
            "excluded_entity_ratio": round(excluded_ratio, 8),
            "excluded_entity_indexes": sorted(excluded_indexes),
            "unresolved_assembly_decision_count": len(unresolved),
            "terminal_source_entity_count": len(
                terminal_source_entities),
            "duplicate_segment_decision_count": (
                len(unresolved) - len(all_unresolved_indexes)),
            "pruning_mode": pruning_mode,
            "forced_subset_scope":
                _forced_excluded_source_indexes is not None,
            "trial_unresolved_wall_assembly_count": len(trial_unresolved),
            "trial_unresolved_removed_source_count": len(
                removed_source_indexes_still_unresolved),
            "trial_new_unresolved_source_count": len(
                new_unresolved_source_indexes),
            "remaining_unresolved_source_entity_indexes": sorted(
                trial_unresolved_indexes),
            "thresholds": {
                "maximum_excluded_entity_count": maximum_excluded_count,
                "maximum_excluded_entity_ratio": maximum_excluded_ratio,
                "minimum_space_union_iou": .995,
                "minimum_individual_space_iou": .99,
                "maximum_wall_area_reduction_ratio": .05,
                "required_trial_unresolved_removed_source_count": 0,
                "required_trial_new_unresolved_source_count": 0,
                "maximum_micro_detail_source_length_m": .075,
            },
            "decision_basis": [
                "only_sub_75mm_source_geometry_or_bounded_unresolved_scope_removed",
                "unresolved_source_representation_explicitly_bounded",
                "source_entity_scope_separate_from_segment_level_decisions",
                "partial_trial_cannot_create_or_retain_removed_source_decisions",
                *( ["independently_proved_direct_primitive_multiview_plan"]
                   if direct_multiview_plan else []),
                "same_physical_space_count",
                "same_source_backed_opening_barriers_used_in_both_passes",
                ("same_conservatively_classified_physical_space_count"
                 if space_comparison_scope == "classified_physical_spaces"
                 else "same_raw_topology_space_count"),
                "bidirectional_individual_space_geometry_match",
                "near_identical_physical_space_union",
                "bounded_wall_area_reduction",
                "rebuilt_removed_source_wall_assemblies_receive_terminal_decisions",
                "no_layer_block_filename_or_view_name_semantics",
            ],
        }
        terminal_evidence = []
        for number, source_index in enumerate(sorted(
                terminal_source_entities), 1):
            source_entities = terminal_source_entities[source_index]
            handles = sorted({
                str(value)
                for source_entity in source_entities
                for value in (
                    source_entity.get("handle"),
                    source_entity.get("source_handle"),
                    source_entity.get("root_handle"),
                )
                if str(value or "")
            })
            terminal = {
                "height_m": wall_height_m,
                "source_entity_handles": handles,
                "source_entities": source_entities,
            }
            terminal["id"] = f"cad_projected_detail_evidence_{number}"
            terminal["source_representation"] = "projected_detail_evidence"
            terminal["review_status"] = "rejected"
            terminal["production_blockers"] = []
            terminal["reason"] = "cad_projected_detail_topology_invariant"
            terminal["reason_codes"] = [
                "cad_projected_detail_topology_invariant"]
            terminal["projected_detail_evidence"] = {
                **copy.deepcopy(proof),
                "source_entity_indexes": [source_index],
            }
            terminal_evidence.append(terminal)
        result.update({
            "status": "proved",
            **copy.deepcopy(proof),
            "_trial_rows": trial_rows,
            "_trial_assemblies": trial_assemblies,
            "_trial_topology": trial_topology,
            "_terminal_evidence_assemblies": terminal_evidence,
        })
        return result
    except (GlobalTopologyError, WallAssemblyError, ValueError, TypeError) as ex:
        result.update(
            status="unresolved", reason="trial_rebuild_failed",
            diagnostic=f"{type(ex).__name__}: {ex}"[:240],
        )
        return result


def _partition_topology_invariant_projected_detail(
    selected_rows: Sequence[Mapping[str, Any]],
    wall_assemblies: Sequence[Mapping[str, Any]],
    topology_result: Mapping[str, Any],
    projected_plan_structure_filter: Mapping[str, Any],
    *,
    opening_candidates: Sequence[Mapping[str, Any]] = (),
    semantic_anchors: Sequence[Mapping[str, Any]] = (),
    origin_x: float = 0.0,
    origin_z: float = 0.0,
    wall_height_m: float = 2.8,
) -> dict:
    """Partition a bounded pending set into removable and topology evidence.

    The complete projected-detail trial can fail because a few short source
    caps are required to close a physical room even though they are not wall
    volumes.  A deterministic accumulated pass groups pending lines into
    room-local connected clusters and finds the maximal cluster subset that can
    be removed while preserving the original physical spaces.  Every retained
    cluster must then have a counterfactual trial proving that removing it
    changes the classified room set; otherwise the partition is rejected.
    """
    result: dict[str, Any] = {
        "schema_version": 1,
        "method": "cad_projected_detail_counterfactual_partition_v1",
        "status": "not_applicable",
    }
    pending_by_index: dict[int, list[dict]] = defaultdict(list)
    for assembly in wall_assemblies:
        if str(assembly.get("review_status") or "") in {
                "accepted", "confirmed", "rejected", "reject"}:
            continue
        source_entities = assembly.get("source_entities") or []
        if len(source_entities) != 1:
            result.update(
                status="unresolved",
                reason="pending_geometry_is_not_single_source_entity",
            )
            return result
        try:
            source_index = int(source_entities[0]["entity_index"])
        except (KeyError, TypeError, ValueError):
            result.update(
                status="unresolved", reason="pending_source_index_invalid")
            return result
        pending_by_index[source_index].append(copy.deepcopy(dict(assembly)))
    all_indexes = sorted(pending_by_index)
    if not 1 <= len(all_indexes) <= 100:
        result.update(
            status="unresolved", reason="pending_partition_scope_outside_limit",
            pending_source_entity_count=len(all_indexes),
        )
        return result

    attempts: list[dict] = []
    safe_indexes: set[int] = set()
    essential_failures: dict[tuple[int, ...], dict] = {}
    permitted_counterfactual_failures = {
        "trial_physical_space_count_changed",
        "trial_physical_topology_changed",
        "trial_wall_assembly_decisions_remain_unresolved",
    }

    def public_trial(trial: Mapping[str, Any]) -> dict:
        return {
            key: copy.deepcopy(value) for key, value in trial.items()
            if not str(key).startswith("_")
        }

    def evaluate(excluded: set[int]) -> dict:
        trial = _prune_topology_invariant_projected_detail(
            selected_rows, wall_assemblies, topology_result,
            projected_plan_structure_filter,
            opening_candidates=opening_candidates,
            semantic_anchors=semantic_anchors,
            origin_x=origin_x, origin_z=origin_z,
            wall_height_m=wall_height_m,
            _forced_excluded_source_indexes=set(excluded),
        )
        attempts.append({
            "excluded_entity_indexes": sorted(excluded),
            "excluded_entity_count": len(excluded),
            "status": str(trial.get("status") or ""),
            "reason": str(trial.get("reason") or ""),
            "original_space_count": trial.get("original_space_count"),
            "trial_space_count": trial.get("trial_space_count"),
            "space_union_iou": trial.get("space_union_iou"),
            "minimum_matched_space_iou": trial.get(
                "minimum_matched_space_iou"),
        })
        return trial

    from shapely.geometry import LineString, Polygon  # type: ignore

    source_shapes: dict[int, Any] = {}
    for source_index, decisions in pending_by_index.items():
        shape = None
        for decision in decisions:
            raw_centerline = decision.get("source_centerline") or []
            try:
                if len(raw_centerline) >= 2:
                    shape = LineString([
                        (float(point[0]), float(point[1]))
                        for point in raw_centerline])
                    break
            except (TypeError, ValueError, IndexError):
                pass
            raw_polygon = (decision.get("source_polygon_m")
                           or decision.get("footprint_polygon") or [])
            try:
                if len(raw_polygon) >= 3:
                    candidate = Polygon([
                        (float(point[0]), float(point[1]))
                        for point in raw_polygon])
                    if not candidate.is_empty:
                        shape = candidate.boundary
                        break
            except (TypeError, ValueError, IndexError):
                pass
            source_entities = decision.get("source_entities") or []
            if source_entities:
                raw_segment = source_entities[0].get(
                    "model_segment_m") or []
                try:
                    if len(raw_segment) >= 2:
                        shape = LineString([
                            (float(point[0]), float(point[1]))
                            for point in raw_segment])
                        break
                except (TypeError, ValueError, IndexError):
                    pass
        if shape is not None:
            source_shapes[source_index] = shape

    parent = list(range(len(all_indexes)))

    def find_parent(position: int) -> int:
        while parent[position] != position:
            parent[position] = parent[parent[position]]
            position = parent[position]
        return position

    def union_positions(first: int, second: int) -> None:
        first_root, second_root = find_parent(first), find_parent(second)
        if first_root != second_root:
            parent[second_root] = first_root

    cluster_distance_m = 1.0
    for first_position, first_index in enumerate(all_indexes):
        first_shape = source_shapes.get(first_index)
        if first_shape is None:
            continue
        for second_position in range(first_position):
            second_index = all_indexes[second_position]
            second_shape = source_shapes.get(second_index)
            if (second_shape is not None
                    and float(first_shape.distance(second_shape))
                    <= cluster_distance_m + 1e-9):
                union_positions(first_position, second_position)
    clusters_by_root: dict[int, list[int]] = defaultdict(list)
    for position, source_index in enumerate(all_indexes):
        clusters_by_root[find_parent(position)].append(source_index)
    source_clusters = sorted(
        (sorted(cluster) for cluster in clusters_by_root.values()),
        key=lambda cluster: (min(cluster), len(cluster)),
    )
    unproved_clusters: list[list[int]] = []
    for cluster in source_clusters:
        trial = evaluate(safe_indexes | set(cluster))
        if trial.get("status") == "proved":
            safe_indexes.update(cluster)
            continue
        if str(trial.get("reason") or "") \
                in permitted_counterfactual_failures:
            essential_failures[tuple(cluster)] = public_trial(trial)
        else:
            unproved_clusters.append(cluster)

    essential_indexes = set(all_indexes) - safe_indexes
    proved_essential_indexes = {
        source_index for cluster in essential_failures
        for source_index in cluster
    }
    if unproved_clusters or essential_indexes != proved_essential_indexes:
        result.update(
            status="unresolved",
            reason="pending_partition_has_unproved_essential_source",
            safe_source_entity_indexes=sorted(safe_indexes),
            unproved_source_entity_indexes=sorted(
                essential_indexes - proved_essential_indexes),
            unproved_source_clusters=unproved_clusters,
            source_cluster_count=len(source_clusters),
            source_cluster_distance_m=cluster_distance_m,
            partition_attempts=attempts,
        )
        return result
    stable_trial = evaluate(safe_indexes)
    if stable_trial.get("status") != "proved":
        result.update(
            status="unresolved",
            reason="partitioned_safe_scope_not_topology_invariant",
            safe_source_entity_indexes=sorted(safe_indexes),
            partition_attempts=attempts,
            stable_trial=public_trial(stable_trial),
        )
        return result

    safe_scope_hash = hashlib.sha256(json.dumps(
        sorted(safe_indexes), separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    terminal_counterfactual_evidence: list[dict] = []
    for number, (source_cluster, counterfactual) in enumerate(sorted(
            essential_failures.items()), 1):
        decisions = [
            decision for source_index in source_cluster
            for decision in pending_by_index[source_index]
        ]
        source_entities: list[dict] = []
        for decision in decisions:
            for source_entity in decision.get("source_entities") or []:
                record = copy.deepcopy(dict(source_entity))
                if record not in source_entities:
                    source_entities.append(record)
        handles = sorted({
            str(value)
            for source_entity in source_entities
            for value in (
                source_entity.get("handle"),
                source_entity.get("source_handle"),
                source_entity.get("root_handle"),
            ) if str(value or "")
        })
        common_proof = {
            "source_entity_index": source_cluster[0],
            "source_entity_indexes": list(source_cluster),
            "source_entity_handles": handles,
            "safe_excluded_scope_hash": safe_scope_hash,
            "safe_excluded_source_entity_count": len(safe_indexes),
            "reference_space_comparison_scope": stable_trial.get(
                "space_comparison_scope"),
            "reference_physical_space_count": stable_trial.get(
                "trial_space_count"),
            "reference_raw_topology_space_count": stable_trial.get(
                "trial_raw_topology_space_count"),
            "reference_space_union_iou": stable_trial.get(
                "space_union_iou"),
            "reference_minimum_matched_space_iou": stable_trial.get(
                "minimum_matched_space_iou"),
            "counterfactual_status": counterfactual.get("status"),
            "counterfactual_reason": counterfactual.get("reason"),
            "counterfactual_physical_space_count": counterfactual.get(
                "trial_space_count"),
            "counterfactual_raw_topology_space_count": counterfactual.get(
                "trial_raw_topology_space_count"),
            "counterfactual_space_union_iou": counterfactual.get(
                "space_union_iou"),
            "counterfactual_minimum_matched_space_iou": counterfactual.get(
                "minimum_matched_space_iou"),
            "counterfactual_trial_unresolved_wall_assembly_count":
                counterfactual.get("trial_unresolved_count"),
            "counterfactual_removed_source_indexes_still_unresolved":
                counterfactual.get(
                    "removed_source_indexes_still_unresolved") or [],
            "counterfactual_new_unresolved_source_indexes":
                counterfactual.get("new_unresolved_source_indexes") or [],
        }
        physical_counterfactual = str(counterfactual.get("reason") or "") in {
            "trial_physical_space_count_changed",
            "trial_physical_topology_changed",
        }
        if physical_counterfactual:
            representation = "projected_topology_boundary_evidence"
            reason_code = "cad_projected_topology_boundary_counterfactual"
            proof_key = "projected_topology_boundary_evidence"
            proof = {
                **common_proof,
                "method":
                    "cad_projected_topology_boundary_counterfactual_v1",
            "thresholds": {
                "minimum_reference_space_union_iou": .995,
                "minimum_reference_individual_space_iou": .99,
                "required_counterfactual_physical_change": True,
                "maximum_partition_source_entity_count": 100,
                "maximum_source_cluster_distance_m": cluster_distance_m,
            },
            "decision_basis": [
                "bounded_pending_source_scope_partitioned_by_geometry_only",
                "safe_source_subset_preserves_original_physical_rooms",
                "removing_this_retained_source_cluster_changes_physical_room_topology",
                "source_cluster_retained_for_topology_but_emits_no_duplicate_wall_volume",
                "no_layer_block_colour_handle_or_name_semantics",
            ],
            }
        else:
            representation = "projected_geometry_dependency_evidence"
            reason_code = "cad_projected_geometry_dependency_counterfactual"
            proof_key = "projected_geometry_dependency_evidence"
            proof = {
                **common_proof,
                "method":
                    "cad_projected_geometry_dependency_counterfactual_v1",
                "thresholds": {
                    "minimum_reference_space_union_iou": .995,
                    "minimum_reference_individual_space_iou": .99,
                    "required_counterfactual_unresolved_decision_count": 1,
                    "maximum_partition_source_entity_count": 100,
                    "maximum_source_cluster_distance_m": cluster_distance_m,
                },
                "decision_basis": [
                    "bounded_pending_source_scope_partitioned_by_geometry_only",
                    "safe_source_subset_preserves_original_physical_rooms",
                    "removing_this_cluster_creates_new_or_retained_unresolved_wall_decisions",
                    "dependency_cluster_retained_as_audit_only_and_emits_no_wall_volume",
                    "no_layer_block_colour_handle_or_name_semantics",
                ],
            }
        terminal = {
            "id": f"cad_{representation}_{number}",
            "source_representation": representation,
            "resolved_as": representation,
            "review_status": "rejected",
            "confidence_grade": "A", "confidence": 1.0,
            "legacy_wall_compatible": False,
            "height_m": wall_height_m,
            "footprint_polygon": None,
            "centerline": None,
            "thickness_m": None,
            "thickness_source":
                f"not_applicable_{representation}",
            "production_blockers": [],
            "reason": reason_code,
            "reason_codes": [reason_code],
            "source_entity_handles": handles,
            "source_entities": source_entities,
            proof_key: proof,
        }
        terminal_counterfactual_evidence.append(terminal)

    def pending_source_index(assembly: Mapping[str, Any]) -> Optional[int]:
        if str(assembly.get("review_status") or "") in {
                "accepted", "confirmed", "rejected", "reject"}:
            return None
        source_entities = assembly.get("source_entities") or []
        if len(source_entities) != 1:
            return None
        try:
            return int(source_entities[0].get("entity_index"))
        except (TypeError, ValueError):
            return None

    trial_assemblies = [
        copy.deepcopy(dict(assembly))
        for assembly in stable_trial["_trial_assemblies"]
        if pending_source_index(assembly) not in essential_indexes
    ]
    terminal_evidence = [
        *copy.deepcopy(stable_trial["_terminal_evidence_assemblies"]),
        *terminal_counterfactual_evidence,
    ]
    stable_public = public_trial(stable_trial)
    result.update({
        **stable_public,
        "schema_version": 1,
        "method": "cad_projected_detail_counterfactual_partition_v1",
        "status": "proved",
        "pruning_mode": "counterfactual_partitioned_scope",
        "partition_attempt_count": len(attempts),
        "partition_attempts": attempts,
        "source_cluster_count": len(source_clusters),
        "source_cluster_distance_m": cluster_distance_m,
        "safe_source_entity_indexes": sorted(safe_indexes),
        "essential_topology_source_entity_indexes": sorted(
            essential_indexes),
        "essential_topology_source_entity_count": len(essential_indexes),
        "essential_topology_source_cluster_count": len(
            essential_failures),
        "trial_unresolved_wall_assembly_count": 0,
        "remaining_unresolved_source_entity_indexes": [],
        "_trial_rows": stable_trial["_trial_rows"],
        "_trial_assemblies": trial_assemblies,
        "_trial_topology": stable_trial["_trial_topology"],
        "_terminal_evidence_assemblies": terminal_evidence,
    })
    decision_basis = list(result.get("decision_basis") or [])
    decision_basis.extend([
        "accumulated_room_local_clusters_find_topology_safe_source_subset",
        "each_retained_source_cluster_has_a_physical_room_counterfactual",
    ])
    result["decision_basis"] = list(dict.fromkeys(decision_basis))
    return result


def _infer_annotation_unit_resolution(
    selected: dict,
    texts: list[dict],
    *,
    declared_units: int,
    declared_scale: float,
) -> Optional[dict]:
    """Resolve a wrong INSUNITS header only from explicit in-plan annotations.

    The candidate search is intentionally finite (mm/cm/in/ft/m).  A room
    dimension must physically fit inside the selected plan, and a stated total
    area must be a plausible fraction of the selected plan bounding box.  If
    more than one unit survives, no correction is made.
    """
    try:
        bounds = tuple(float(value) for value in selected.get("bbox_m") or [])
        if len(bounds) != 4 or declared_scale <= 0:
            return None
    except (TypeError, ValueError):
        return None
    current_width = max(0.0, bounds[2] - bounds[0])
    current_depth = max(0.0, bounds[3] - bounds[1])
    if current_width <= 0 or current_depth <= 0:
        return None
    raw_spans = sorted((current_width / declared_scale, current_depth / declared_scale))
    annotation_margin = max(current_width, current_depth) * .25
    context: list[tuple[dict, bool, bool]] = []
    for row in texts:
        point = row.get("point_m")
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            point_x, point_y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        inside_plan = (
            bounds[0] <= point_x <= bounds[2]
            and bounds[1] <= point_y <= bounds[3]
        )
        inside_annotation_margin = (
            bounds[0] - annotation_margin <= point_x <= bounds[2] + annotation_margin
            and bounds[1] - annotation_margin <= point_y <= bounds[3] + annotation_margin
        )
        if inside_plan or inside_annotation_margin:
            context.append((row, inside_plan, inside_annotation_margin))
    dimension_rows: list[dict] = []
    area_rows: list[dict] = []
    seen_dimensions: set[tuple[float, float]] = set()
    seen_areas: set[float] = set()
    for row, inside_plan, inside_annotation_margin in context:
        text = str(row.get("text") or "")
        handle = str((row.get("cad_provenance") or {}).get("handle") or "")
        for first, second in (_architectural_dimension_pairs(text) if inside_plan else []):
            key = tuple(round(value, 4) for value in sorted((first, second)))
            if key in seen_dimensions:
                continue
            seen_dimensions.add(key)
            dimension_rows.append({
                "text": text[:160], "handle": handle,
                "dimensions_m": [round(first, 6), round(second, 6)],
                "point_m": [round(float(value), 8) for value in row["point_m"][:2]],
                "annotation_scope": "inside_selected_plan_bbox",
            })
        for area in (_architectural_area_m2(text) if inside_annotation_margin else []):
            key = round(area, 3)
            if key in seen_areas:
                continue
            seen_areas.add(key)
            area_rows.append({
                "text": text[:160], "handle": handle,
                "area_m2": round(area, 6),
                "point_m": [round(float(value), 8) for value in row["point_m"][:2]],
                "annotation_scope": (
                    "inside_selected_plan_bbox" if inside_plan
                    else "selected_plan_bbox_plus_25pct_margin"
                ),
            })
    if len(dimension_rows) < 2 and not area_rows:
        return None

    decisions: list[dict] = []
    accepted: list[dict] = []
    for candidate in _ARCHITECTURAL_UNIT_SCALE_CANDIDATES:
        scale = float(candidate["metres_per_unit"])
        plan_spans = [raw_spans[0] * scale, raw_spans[1] * scale]
        bbox_area = plan_spans[0] * plan_spans[1]
        dimension_fit = True
        dimension_utilization: list[float] = []
        for row in dimension_rows:
            room_spans = sorted(float(value) for value in row["dimensions_m"])
            fits = (room_spans[0] <= plan_spans[0] * 1.02
                    and room_spans[1] <= plan_spans[1] * 1.02)
            dimension_fit = dimension_fit and fits
            dimension_utilization.extend([
                room_spans[0] / max(plan_spans[0], 1e-9),
                room_spans[1] / max(plan_spans[1], 1e-9),
            ])
        # Reject units that make the whole home implausibly larger than every
        # explicitly stated room.  This is a relative annotation constraint,
        # not a guessed absolute house or door dimension.
        dimension_scale_bounded = (
            not dimension_utilization
            or max(dimension_utilization) >= 0.20
        )
        area_ratios = [float(row["area_m2"]) / max(bbox_area, 1e-9)
                       for row in area_rows]
        area_fit = bool(area_ratios) and all(.35 <= ratio <= 1.05 for ratio in area_ratios)
        dimension_evidence_fit = (
            len(dimension_rows) >= 2 and dimension_fit and dimension_scale_bounded)
        accepted_by = [
            method for method, passed in (
                ("room_dimension_fit", dimension_evidence_fit),
                ("total_area_fit", area_fit),
            ) if passed
        ]
        decision = {
            **candidate,
            "plan_span_m": [round(value, 6) for value in plan_spans],
            "bbox_area_m2": round(bbox_area, 6),
            "dimension_fit": dimension_evidence_fit,
            "dimension_max_utilization": round(max(dimension_utilization), 6)
            if dimension_utilization else None,
            "area_ratios": [round(value, 6) for value in area_ratios],
            "area_fit": area_fit,
            "accepted_by": accepted_by,
        }
        decisions.append(decision)
        if accepted_by:
            accepted.append(decision)
    if len(accepted) != 1:
        return None
    resolved = accepted[0]
    resolved_scale = float(resolved["metres_per_unit"])
    if math.isclose(resolved_scale, declared_scale, rel_tol=0, abs_tol=1e-12):
        return None
    return {
        "schema_version": 1,
        "method": "cad_explicit_annotation_unit_resolution_v1",
        "declared_insunits": int(declared_units),
        "declared_metres_per_unit": float(declared_scale),
        "resolved_insunits": int(resolved["unit_code"]),
        "resolved_unit": str(resolved["unit"]),
        "resolved_metres_per_unit": resolved_scale,
        "scale_correction_factor": resolved_scale / declared_scale,
        "selected_candidate_id": str(selected.get("candidate_id") or ""),
        "dimension_evidence": dimension_rows,
        "area_evidence": area_rows,
        "candidate_decisions": decisions,
        "decision_basis": [
            "room_dimensions_inside_selected_plan_bbox_only",
            "total_area_inside_selected_plan_bbox_or_25pct_annotation_margin",
            "finite_architectural_unit_candidate_set",
            "unique_surviving_unit_required",
            "no_door_width_or_house_size_prior",
        ],
    }


def _infer_metric_plan_metadata_unit_resolution(
    selected: Mapping[str, Any],
    texts: Sequence[Mapping[str, Any]],
    dimension_styles: Sequence[Mapping[str, Any]],
    dimensions: Sequence[Mapping[str, Any]] = (),
    *,
    measurement_system: int,
    declared_units: int,
    declared_scale: float,
) -> Optional[dict]:
    """Resolve contradictory imperial INSUNITS from metric CAD metadata.

    This is deliberately narrower than a generic architectural-size guess.  It
    requires all of the following independent source facts:

    * ``$MEASUREMENT`` explicitly selects the metric CAD convention;
    * either a positive floor-plan title is spatially associated with the
      selected structure, or at least three spatial room-label anchors plus
      two explicit ``<>m`` DIMENSION overrides prove that the selected view is
      a metric floor plan;
    * an active/default dimension style uses unit scale 1 and has text/arrow
      sizes consistent with that title;
    * exactly one metric drawing unit (mm/cm/m) maps both annotation sizes and
      the selected structure bounds into conservative model-space ranges.

    A missing field or more than one survivor fails closed.  The method never
    uses a door-width measurement, a filename, layer/block names, publisher
    identity, or the test manifest.
    """
    if int(measurement_system or 0) != 1 or declared_scale <= 0:
        return None
    try:
        bounds = tuple(float(value) for value in selected.get("bbox_m") or [])
        if len(bounds) != 4:
            return None
    except (TypeError, ValueError):
        return None
    current_spans = sorted((
        max(0.0, bounds[2] - bounds[0]),
        max(0.0, bounds[3] - bounds[1]),
    ))
    if current_spans[0] <= 0 or current_spans[1] <= 0:
        return None
    raw_spans = [value / declared_scale for value in current_spans]
    margin = max(current_spans) * .25
    title_rows: list[dict] = []
    room_anchor_rows: list[dict] = []
    negative_view_rows: list[dict] = []
    for row in texts:
        text = str(row.get("text") or "").strip()
        point = row.get("point_m")
        try:
            point_x, point_y = float(point[0]), float(point[1])
            current_height = float(row.get("text_height_m") or 0)
        except (TypeError, ValueError, IndexError):
            continue
        if current_height <= 0:
            continue
        title_associated = (
            bounds[0] - margin <= point_x <= bounds[2] + margin
            and bounds[1] - margin <= point_y <= bounds[3] + margin
        )
        # Room semantics and negative view titles must be inside the selected
        # structure.  Reusing the generous title margin here lets a nearby
        # elevation title on a multi-view sheet poison the actual floor plan.
        semantic_margin = max(current_spans) * .02
        semantic_associated = (
            bounds[0] - semantic_margin <= point_x <= bounds[2] + semantic_margin
            and bounds[1] - semantic_margin <= point_y <= bounds[3] + semantic_margin
        )
        if not title_associated and not semantic_associated:
            continue
        evidence_row = {
            "text": text[:160],
            "handle": str((row.get("cad_provenance") or {}).get("handle") or ""),
            "raw_text_height": current_height / declared_scale,
            "point_m_at_declared_scale": [round(point_x, 8), round(point_y, 8)],
            "annotation_scope": "selected_plan_bbox_plus_25pct_margin",
        }
        if title_associated and _POSITIVE_ARCHITECTURAL_PLAN_RE.search(text):
            title_rows.append(evidence_row)
        if semantic_associated and _NEGATIVE_ARCHITECTURAL_VIEW_RE.search(text):
            negative_view_rows.append(evidence_row)
        reference_profile, semantic_profile = _room_profile_from_text(text)
        if semantic_associated and reference_profile and semantic_profile:
            room_anchor_rows.append({
                **evidence_row,
                "annotation_scope": "selected_plan_bbox_plus_2pct_margin",
                "reference_profile": reference_profile,
                "semantic_profile": semantic_profile,
            })
    if negative_view_rows:
        return None

    metric_dimension_rows: list[dict] = []
    for row in dimensions:
        override = str(row.get("text_override") or "").strip()
        if not re.fullmatch(r"<>\s*m", override, re.I):
            continue
        try:
            raw_measurement = float(row.get("raw_measurement") or 0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(raw_measurement) or raw_measurement <= 0:
            continue
        provenance = row.get("cad_provenance") or {}
        metric_dimension_rows.append({
            "handle": str(provenance.get("handle") or ""),
            "raw_measurement": raw_measurement,
            "text_override": override,
            "dimension_style": str(row.get("dimension_style") or "")[:120],
        })
    metric_dimension_rows = list({
        (row["handle"], row["raw_measurement"], row["dimension_style"]): row
        for row in metric_dimension_rows
    }.values())

    # Some public CAD sheets omit a FLOOR PLAN title but contain spatial room
    # labels and explicit ``<>m`` DIMENSION overrides.  Those are independent
    # source facts: the room labels prove the selected view type, while the
    # DIMENSION overrides prove the drawing unit.  Require several of each and
    # more than one semantic room class so a furniture label cannot unlock it.
    room_profiles = sorted({
        str(row["semantic_profile"]) for row in room_anchor_rows
    })
    room_anchor_alternative = (
        len(room_anchor_rows) >= 3
        and len(room_profiles) >= 2
        and len(metric_dimension_rows) >= 2
    )
    if not title_rows and not room_anchor_alternative:
        return None
    annotation_rows = title_rows or room_anchor_rows

    style_rows: list[dict] = []
    for row in dimension_styles:
        try:
            dimscale = float(row.get("dimscale") or 0)
            dimlfac = float(row.get("dimlfac") or 0)
            dimtxt = float(row.get("dimtxt") or 0)
            dimasz = float(row.get("dimasz") or 0)
        except (TypeError, ValueError):
            continue
        if (not .999 <= dimscale <= 1.001
                or not .999 <= dimlfac <= 1.001
                or dimtxt <= 0 or dimasz <= 0):
            continue
        # The style and actual title must be expressed at the same raw drawing
        # scale.  This excludes stale/default styles from becoming sole proof.
        if not any(.25 <= dimtxt / max(row_["raw_text_height"], 1e-12) <= 4.0
                   and .25 <= dimasz / max(row_["raw_text_height"], 1e-12) <= 4.0
                   for row_ in annotation_rows):
            continue
        style_rows.append({
            "name": str(row.get("name") or "")[:120],
            "dimscale": dimscale,
            "dimlfac": dimlfac,
            "raw_dimtxt": dimtxt,
            "raw_dimasz": dimasz,
        })
    if not style_rows:
        return None

    decisions: list[dict] = []
    accepted: list[dict] = []
    for candidate in _ARCHITECTURAL_UNIT_SCALE_CANDIDATES:
        # $MEASUREMENT=1 is an explicit metric convention, so imperial
        # candidates cannot resolve a contradictory imperial INSUNITS header.
        if int(candidate["unit_code"]) not in {4, 5, 6}:
            continue
        scale = float(candidate["metres_per_unit"])
        plan_spans = [raw_spans[0] * scale, raw_spans[1] * scale]
        bbox_area = plan_spans[0] * plan_spans[1]
        annotation_heights = [
            float(row["raw_text_height"]) * scale for row in annotation_rows]
        style_text_heights = [float(row["raw_dimtxt"]) * scale for row in style_rows]
        style_arrow_sizes = [float(row["raw_dimasz"]) * scale for row in style_rows]
        annotation_height_fit = all(
            .04 <= value <= .50 for value in annotation_heights)
        style_size_fit = all(
            .04 <= value <= .50
            for value in style_text_heights + style_arrow_sizes)
        # These intentionally broad limits only reject paper-sized fragments or
        # kilometre-scale views.  Unit authority comes from the metric header and
        # two annotation sources, not from assuming a typical house size.
        plan_extent_fit = (
            3.0 <= plan_spans[0] <= 200.0
            and 3.0 <= plan_spans[1] <= 200.0
            and 10.0 <= bbox_area <= 20000.0
        )
        accepted_by = ([
            "metric_measurement_header",
            ("associated_floor_plan_title_height" if title_rows
             else "associated_room_label_heights"),
            *([] if title_rows else [
                "explicit_metric_dimension_overrides",
                "multiple_room_semantic_profiles",
            ]),
            "unit_scale_one_dimension_style",
            "bounded_modelspace_extent",
        ] if annotation_height_fit and style_size_fit and plan_extent_fit else [])
        decision = {
            **candidate,
            "plan_span_m": [round(value, 6) for value in plan_spans],
            "bbox_area_m2": round(bbox_area, 6),
            "annotation_heights_m": [round(value, 6) for value in annotation_heights],
            "style_text_heights_m": [round(value, 6) for value in style_text_heights],
            "style_arrow_sizes_m": [round(value, 6) for value in style_arrow_sizes],
            "annotation_height_fit": annotation_height_fit,
            "style_size_fit": style_size_fit,
            "plan_extent_fit": plan_extent_fit,
            "accepted_by": accepted_by,
        }
        decisions.append(decision)
        if accepted_by:
            accepted.append(decision)
    if len(accepted) != 1:
        return None
    resolved = accepted[0]
    resolved_scale = float(resolved["metres_per_unit"])
    if math.isclose(resolved_scale, declared_scale, rel_tol=0, abs_tol=1e-12):
        return None
    return {
        "schema_version": 1,
        "method": "cad_metric_plan_metadata_unit_resolution_v1",
        "declared_insunits": int(declared_units),
        "declared_metres_per_unit": float(declared_scale),
        "measurement_system": int(measurement_system),
        "resolved_insunits": int(resolved["unit_code"]),
        "resolved_unit": str(resolved["unit"]),
        "resolved_metres_per_unit": resolved_scale,
        "scale_correction_factor": resolved_scale / declared_scale,
        "selected_candidate_id": str(selected.get("candidate_id") or ""),
        "floor_plan_title_evidence": title_rows,
        "room_anchor_evidence": room_anchor_rows,
        "room_anchor_semantic_profiles": room_profiles,
        "explicit_metric_dimension_evidence": metric_dimension_rows,
        "negative_view_title_evidence": negative_view_rows,
        "dimension_style_evidence": style_rows,
        "candidate_decisions": decisions,
        "decision_basis": [
            "metric_measurement_header_required",
            ("associated_positive_floor_plan_title_with_modelspace_height"
             if title_rows else
             "three_associated_room_labels_across_two_semantic_profiles"),
            *([] if title_rows else [
                "two_explicit_metric_dimension_overrides",
                "no_associated_negative_view_title",
            ]),
            "unit_scale_one_dimension_style_consistent_with_annotation",
            "finite_metric_unit_candidate_set",
            "unique_surviving_unit_required",
            "no_filename_layer_block_publisher_or_test_manifest_evidence",
        ],
    }


_NEGATIVE_ARCHITECTURAL_VIEW_RE = re.compile(
    r"\b(?:elevation|section|roof\s+plan|foundation\s+plan|wall\s+detail)\b",
    re.I,
)
_POSITIVE_ARCHITECTURAL_PLAN_RE = re.compile(
    r"\b(?:floor\s+plan|ground\s+floor|first\s+floor|second\s+floor)\b",
    re.I,
)


def _candidate_semantic_view_metrics(bounds: Sequence[float],
                                     texts: Sequence[Mapping[str, Any]]) -> dict:
    """Return text evidence that distinguishes plans from elevations/sections."""
    try:
        min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    except (TypeError, ValueError):
        return {
            "room_anchor_count": 0, "room_anchor_profiles": [],
            "positive_plan_title_count": 0, "negative_view_title_count": 0,
        }
    margin = min(.35, max(.05, max(max_x - min_x, max_y - min_y) * .05))
    anchors: list[dict] = []
    positive = 0
    negative = 0
    for index, row in enumerate(texts):
        point = row.get("point_m")
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        value = str(row.get("text") or "")
        if (min_x <= x <= max_x and min_y <= y <= max_y):
            reference_profile, semantic_profile = _room_profile_from_text(value)
            if semantic_profile:
                anchors.append({
                    "text_index": index,
                    "reference_profile": reference_profile,
                    "semantic_profile": semantic_profile,
                    "point_m": [round(x, 8), round(y, 8)],
                })
        if (min_x - margin <= x <= max_x + margin
                and min_y - margin <= y <= max_y + margin):
            positive += bool(_POSITIVE_ARCHITECTURAL_PLAN_RE.search(value))
            negative += bool(_NEGATIVE_ARCHITECTURAL_VIEW_RE.search(value))
    return {
        "room_anchor_count": len(anchors),
        "room_anchor_profiles": sorted({
            str(row["semantic_profile"]) for row in anchors}),
        "room_anchor_text_indexes": [int(row["text_index"]) for row in anchors],
        "positive_plan_title_count": int(positive),
        "negative_view_title_count": int(negative),
    }


def _near_duplicate_candidate_views(first: Mapping[str, Any],
                                    second: Mapping[str, Any]) -> bool:
    """Return whether two ranked views differ only by contained crumbs."""
    first_indexes = {
        int(value) for value in first.get("structure_entity_indexes") or []
        if isinstance(value, int) or str(value).isdigit()
    }
    second_indexes = {
        int(value) for value in second.get("structure_entity_indexes") or []
        if isinstance(value, int) or str(value).isdigit()
    }
    if not first_indexes or not second_indexes:
        return False
    if not (first_indexes.issubset(second_indexes)
            or second_indexes.issubset(first_indexes)):
        return False
    smaller = min(len(first_indexes), len(second_indexes))
    difference = len(first_indexes.symmetric_difference(second_indexes))
    if difference > max(4, math.ceil(smaller * .01)):
        return False
    first_bounds = first.get("bbox_m") or []
    second_bounds = second.get("bbox_m") or []
    return bool(
        len(first_bounds) == len(second_bounds) == 4
        and max(abs(float(left) - float(right))
                for left, right in zip(first_bounds, second_bounds))
        <= .01 + 1e-9)


def _semantic_plan_composite_groups(
    candidates: Sequence[Mapping[str, Any]],
    texts: Sequence[Mapping[str, Any]],
) -> list[list[int]]:
    """Find disconnected structural clusters that prove one floor-plan view.

    Components must overlap substantially in one projection axis and touch or
    overlap in the other.  The union then needs at least two CAD room labels
    and no elevation/section title.  Widely separated repeated plans stay
    independent and continue to trigger the ambiguity gate.
    """
    count = len(candidates)
    adjacency: dict[int, set[int]] = {index: set() for index in range(count)}

    def interval_overlap(first: tuple[float, float],
                         second: tuple[float, float]) -> float:
        return max(0.0, min(first[1], second[1]) - max(first[0], second[0]))

    def interval_gap(first: tuple[float, float],
                     second: tuple[float, float]) -> float:
        return max(0.0, max(first[0], second[0]) - min(first[1], second[1]))

    for left_index in range(count):
        left = candidates[left_index].get("bbox_m") or []
        if len(left) != 4:
            continue
        for right_index in range(left_index + 1, count):
            right = candidates[right_index].get("bbox_m") or []
            if len(right) != 4:
                continue
            left_x = (float(left[0]), float(left[2]))
            left_y = (float(left[1]), float(left[3]))
            right_x = (float(right[0]), float(right[2]))
            right_y = (float(right[1]), float(right[3]))
            x_overlap = interval_overlap(left_x, right_x)
            y_overlap = interval_overlap(left_y, right_y)
            x_ratio = x_overlap / max(
                min(left_x[1] - left_x[0], right_x[1] - right_x[0]), 1e-9)
            y_ratio = y_overlap / max(
                min(left_y[1] - left_y[0], right_y[1] - right_y[0]), 1e-9)
            connected = (
                x_ratio >= .60 and interval_gap(left_y, right_y) <= .35
            ) or (
                y_ratio >= .60 and interval_gap(left_x, right_x) <= .35
            )
            if connected:
                adjacency[left_index].add(right_index)
                adjacency[right_index].add(left_index)

    groups: list[list[int]] = []
    visited: set[int] = set()
    for seed in range(count):
        if seed in visited:
            continue
        stack = [seed]
        component: list[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(sorted(adjacency[current] - visited, reverse=True))
        if len(component) < 2:
            continue
        bounds = [candidates[index].get("bbox_m") or [] for index in component]
        union_bounds = [
            min(float(row[0]) for row in bounds),
            min(float(row[1]) for row in bounds),
            max(float(row[2]) for row in bounds),
            max(float(row[3]) for row in bounds),
        ]
        metrics = _candidate_semantic_view_metrics(union_bounds, texts)
        if (metrics["room_anchor_count"] >= 2
                and metrics["negative_view_title_count"] == 0):
            groups.append(sorted(component))
    return sorted(groups, key=lambda row: tuple(row))


def parse_dxf(path: str, project_id: str, *, chord_error_m: float = 0.005,
              preferred_candidate_id: str = "",
              _unit_scale_override: float | None = None,
              _unit_resolution_evidence: Optional[dict] = None) -> tuple[dict, dict]:
    try:
        import ezdxf  # type: ignore
    except Exception as ex:
        raise CadDependencyError("ezdxf_missing", "缺少 ezdxf，无法解析 CAD", status_code=503) from ex
    try:
        from shapely.geometry import LineString, Point, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
        from shapely.ops import polygonize, unary_union  # type: ignore
    except Exception as ex:
        raise CadDependencyError("shapely_missing", "缺少 Shapely，不能执行 CAD 拓扑硬门禁", status_code=503) from ex
    try:
        document = ezdxf.readfile(path)
    except Exception as ex:
        raise CadError("dxf_parse_failed", f"DXF 解析失败: {ex}") from ex
    dxf_normalization = _sanitize_dxf_defaults(document)
    units = int(document.header.get("$INSUNITS", 0) or 0)
    measurement_system = int(document.header.get("$MEASUREMENT", 0) or 0)
    declared_scale = INSUNITS_TO_METRES.get(units)
    if declared_scale is None:
        raise CadError("cad_units_missing", "DXF 缺少可确认的 $INSUNITS；禁止猜测比例",
                       details={"insunits": units})
    scale = float(_unit_scale_override or declared_scale)
    resolved_units = int(
        (_unit_resolution_evidence or {}).get("resolved_insunits") or units)
    unit_resolution_report = copy.deepcopy(_unit_resolution_evidence) if _unit_resolution_evidence else {
        "schema_version": 1,
        "method": "$INSUNITS",
        "declared_insunits": units,
        "declared_metres_per_unit": float(declared_scale),
        "resolved_insunits": units,
        "resolved_unit": next((str(row["unit"]) for row in _ARCHITECTURAL_UNIT_SCALE_CANDIDATES
                               if int(row["unit_code"]) == units), ""),
        "resolved_metres_per_unit": scale,
        "scale_correction_factor": 1.0,
        "decision_basis": ["declared_dxf_header"],
    }
    dimension_styles: list[dict] = []
    for style in document.dimstyles:
        namespace = style.dxf
        try:
            dimension_styles.append({
                "name": str(getattr(namespace, "name", "") or ""),
                "dimscale": float(getattr(namespace, "dimscale", 0) or 0),
                "dimlfac": float(getattr(namespace, "dimlfac", 0) or 0),
                "dimtxt": float(getattr(namespace, "dimtxt", 0) or 0),
                "dimasz": float(getattr(namespace, "dimasz", 0) or 0),
            })
        except (TypeError, ValueError):
            continue
    inventory = Counter()
    layers = Counter()
    blocks = Counter()
    texts: list[dict] = []
    dimensions: list[dict] = []
    scale_conflicts: list[dict] = []
    geometry: list[dict] = []
    inserts: list[dict] = []
    hatch_surfaces: list[dict] = []
    root_geometry_features: dict[str, Counter] = defaultdict(Counter)
    for entity in document.modelspace():
        kind = entity.dxftype()
        if "PROXY" in kind.upper():
            raise CadError("cad_proxy_entity_unresolved", f"检测到未解析 {kind}，禁止作为建筑事实")
        if kind != "INSERT":
            continue
        name = str(getattr(entity.dxf, "name", "") or "")
        try:
            block = document.blocks.get(name)
            flags = int(getattr(block.block_record.dxf, "flags", 0) or 0)
            if flags & 12:
                raise CadError("cad_xref_unresolved", f"块 {name} 是未绑定或覆盖 XREF")
        except CadError:
            raise
        except Exception:
            pass
        try:
            if entity.has_extension_dict and "ACAD_FILTER" in entity.get_extension_dict():
                raise CadError("cad_xclip_unresolved", f"块 {name} 含 XCLIP，当前解析器不能证明裁剪后的完整拓扑")
        except CadError:
            raise
        except Exception:
            pass
    for entity, provenance in _expanded_entities(document.modelspace()):
        kind = entity.dxftype()
        layer = provenance["layer"]
        root_handle = str(provenance.get("root_handle") or "")
        if root_handle:
            root_geometry_features[root_handle][kind] += 1
        inventory[kind] += 1
        layers[layer] += 1
        if provenance.get("block"):
            blocks[provenance["block"]] += 1
        if kind in {"TEXT", "MTEXT"}:
            insert = getattr(entity.dxf, "insert", None)
            raw_text = (entity.plain_text() if kind == "MTEXT" and hasattr(entity, "plain_text")
                        else str(getattr(entity.dxf, "text", "") or ""))
            decoded_text = _repair_legacy_cad_text(str(raw_text))
            raw_text_height = float(
                getattr(entity.dxf, "char_height", 0)
                or getattr(entity.dxf, "height", 0) or 0)
            text_record = {"text": decoded_text[:500],
                          "point_m": ([float(insert.x) * scale, float(insert.y) * scale]
                                      if insert is not None else None),
                          "text_height_m": raw_text_height * scale,
                          "cad_provenance": provenance}
            if decoded_text != str(raw_text):
                text_record["encoded_text"] = str(raw_text)[:500]
            texts.append(text_record)
            continue
        if kind == "DIMENSION":
            raw_measurement = float(entity.get_measurement() or 0)
            measurement = raw_measurement * scale
            override = str(getattr(entity.dxf, "text", "") or "").strip()
            dimensions.append({"raw_measurement": raw_measurement,
                               "measurement_m": measurement,
                               "text_override": override,
                               "dimension_style": str(
                                   getattr(entity.dxf, "dimstyle", "") or ""),
                               "cad_provenance": provenance})
            numeric = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:m|米)?\s*", override, re.I)
            if numeric and measurement > 0:
                stated = float(numeric.group(1))
                if abs(stated - measurement) / max(stated, measurement) > .02:
                    scale_conflicts.append({"handle": provenance.get("handle"),
                                            "measurement_m": measurement, "override": stated})
            continue
        if kind == "INSERT":
            insert = getattr(entity.dxf, "insert", None)
            name = str(getattr(entity.dxf, "name", "") or "")
            inserts.append({
                "name": name, "layer": layer,
                "point": (float(insert.x) * scale, float(insert.y) * scale),
                "rotation_deg": float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
                "xscale": float(getattr(entity.dxf, "xscale", 1.0) or 1.0),
                "yscale": float(getattr(entity.dxf, "yscale", 1.0) or 1.0),
                "opening_kind": _opening_from_name(f"{name} {layer}"),
                "semantic_role": _role_from_name(f"{name} {layer}"),
                "cad_provenance": provenance,
            })
            continue
        if kind == "HATCH":
            surface = _hatch_surface_record(
                entity, provenance, scale, chord_error_m)
            if surface is not None:
                hatch_surfaces.append(surface)
            continue
        if _ANNOTATION_LAYER_RE.search(layer) or kind in {"HATCH", "LEADER", "MLEADER", "VIEWPORT", "IMAGE"}:
            continue
        points = _entity_points(entity, scale, chord_error_m)
        if len(points) < 2:
            continue
        if _closed_entity(entity) and points[0] != points[-1]:
            points.append(points[0])
        dxf_namespace = getattr(entity, "dxf", None)
        try:
            aci_color = int(getattr(dxf_namespace, "color", 256) or 256)
        except (TypeError, ValueError):
            aci_color = 256
        try:
            true_color = int(getattr(dxf_namespace, "true_color", 0) or 0)
        except (TypeError, ValueError):
            true_color = 0
        try:
            lineweight = int(getattr(dxf_namespace, "lineweight", -1) or -1)
        except (TypeError, ValueError):
            lineweight = -1
        geometry.append({
            "entity_index": len(geometry),
            "entity_type": kind, "points": points, "bbox": _bbox(points),
            "closed": _closed_entity(entity), "layer": layer,
            "aci_color": aci_color, "true_color": true_color,
            "lineweight": lineweight,
            "wall_candidate": _is_structural_wall_semantics(
                layer, str(provenance.get("block") or "")),
            "cad_provenance": provenance,
        })
    # Dynamic DWG blocks commonly arrive as anonymous ``*U`` inserts.  Their
    # evaluated child TEXT/MTEXT still carries useful CAD-authored semantics,
    # so promote that evidence back to the root insert before candidate scoring.
    root_semantic_texts: dict[str, list[str]] = defaultdict(list)
    for row in texts:
        provenance = row.get("cad_provenance") or {}
        root_handle = str(provenance.get("root_handle") or "")
        value = str(row.get("text") or "").strip()
        if root_handle and value and value not in root_semantic_texts[root_handle]:
            root_semantic_texts[root_handle].append(value)
    for insert in inserts:
        provenance = insert.get("cad_provenance") or {}
        root_handle = str(provenance.get("root_handle") or provenance.get("source_handle") or "")
        semantic_texts = root_semantic_texts.get(root_handle) or []
        insert["semantic_texts"] = semantic_texts[:20]
        evidence = " ".join([
            str(insert.get("name") or ""), str(insert.get("layer") or ""), *semantic_texts,
        ])
        insert["semantic_role"] = insert.get("semantic_role") or _role_from_name(evidence)
        insert["opening_kind"] = insert.get("opening_kind") or _opening_from_name(evidence)
    if not geometry:
        raise CadError("cad_no_modelspace_geometry", "DXF modelspace 没有可用于建模的几何")
    root = _asset_directory(project_id, f"parse_{uuid.uuid4().hex[:12]}")
    # Some authoring tools place anonymous fixture/detail blocks on the active
    # wall layer.  Layer inheritance is valid DXF semantics, but a compact
    # closed *U glyph (basin/toilet/detail symbol) is not a 2.8m-high wall.
    # Exclude only this high-specificity shape; long/open inserted wall runs
    # remain structural and fully traceable.
    excluded_compact_wall_glyphs = _exclude_compact_anonymous_wall_glyphs(geometry)
    structural_indexes = [index for index, row in enumerate(geometry) if row.get("wall_candidate")]
    geometry_authority_evidence = {
        "schema_version": 1,
        "method": "explicit_wall_semantics_v1",
        "status": "proved" if structural_indexes else "unresolved",
        "selected_indexes": copy.deepcopy(structural_indexes),
        "candidates": [],
        "decision_basis": ["explicit_wall_layer_or_block_semantics"],
    }
    if not structural_indexes:
        geometry_authority_evidence = _geometry_only_structural_evidence(
            geometry, texts, inserts)
        structural_indexes = sorted({
            int(value) for value in geometry_authority_evidence.get(
                "selected_indexes") or []
            if isinstance(value, int) and 0 <= value < len(geometry)
        })
        for index in structural_indexes:
            geometry[index]["wall_candidate"] = True
            geometry[index]["wall_authority_source"] = (
                "cad_geometry_plan_authority_v1")
    ignored_indexes = [index for index, row in enumerate(geometry) if not row.get("wall_candidate")]
    ignored_nonstructural = [{
        "entity_index": index,
        "entity_type": geometry[index].get("entity_type") or "",
        "layer": geometry[index].get("layer") or "",
        "block": (geometry[index].get("cad_provenance") or {}).get("block") or "",
        "bbox_m": [round(value, 8) for value in geometry[index].get("bbox") or []],
        "cad_provenance": copy.deepcopy(geometry[index].get("cad_provenance") or {}),
        **({"reason": geometry[index].get("structural_exclusion_reason")}
           if geometry[index].get("structural_exclusion_reason") else {}),
    } for index in ignored_indexes]
    if not structural_indexes:
        hard_error = {
            "code": "cad_wall_semantics_unresolved",
            "message": "CAD 中没有可由明确 wall 图层或 wall 块语义证明的结构线；禁止把家具或普通线降级为墙",
        }
        report = {
            "schema_version": 1, "source_path": path, "source_sha256": sha256_file(path),
            "insunits": units, "resolved_insunits": resolved_units,
            "declared_unit_scale_to_m": float(declared_scale),
            "unit_scale_to_m": scale, "unit_resolution": copy.deepcopy(unit_resolution_report),
            "chord_error_m": chord_error_m,
            "inventory": dict(inventory), "layers": dict(layers), "blocks": dict(blocks),
            "normalization": {
                "text_encoding": "gbk_from_latin1_only_when_cjk_gain_v1",
                "dxf_defaults": dxf_normalization,
                "unit_resolution": copy.deepcopy(unit_resolution_report),
            },
            "texts": texts[:500], "dimensions": dimensions[:500],
            "structural_entity_count": 0,
            "geometry_authority_evidence": copy.deepcopy(geometry_authority_evidence),
            "scale_conflicts": copy.deepcopy(scale_conflicts[:100]),
            "excluded_compact_wall_glyphs": excluded_compact_wall_glyphs,
            "ignored_nonstructural_count": len(ignored_nonstructural),
            "ignored_nonstructural_entities": ignored_nonstructural[:1000],
            "candidate_plans": [], "selected_candidate_id": "",
            "hard_errors": [hard_error], "warnings": [], "artifact_directory": root,
        }
        report_path = os.path.join(root, "parse_report.json")
        with open(report_path, "x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        report["report_path"] = report_path
        raise CadError("cad_wall_semantics_unresolved", hard_error["message"],
                       details={"parse_report": report})
    structural_geometry = [geometry[index] for index in structural_indexes]
    groups = _cluster_geometry(structural_geometry)
    candidates = []
    for index, local_indexes in enumerate(groups, 1):
        indexes = [structural_indexes[value] for value in local_indexes]
        rows = [geometry[value] for value in indexes]
        all_points = [point for row in rows for point in row["points"]]
        bounds = _bbox(all_points)
        length = sum(math.dist(a, b) for row in rows for a, b in zip(row["points"], row["points"][1:]))
        area = max(0.0, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
        closed_count = sum(row["closed"] and _poly_area(row["points"][:-1] if row["points"][0] == row["points"][-1] else row["points"]) >= 0.5 for row in rows)
        score = length + min(area, 10000) * 0.05 + closed_count * 10
        context_inserts = [
            row for row in inserts
            if bounds[0] <= row["point"][0] <= bounds[2]
            and bounds[1] <= row["point"][1] <= bounds[3]
        ]
        context_texts = [
            row for row in texts
            if row.get("point_m")
            and bounds[0] <= row["point_m"][0] <= bounds[2]
            and bounds[1] <= row["point_m"][1] <= bounds[3]
        ]
        semantic_anchor_count = sum(
            bool(row.get("semantic_role") or row.get("opening_kind")) for row in context_inserts
        )
        semantic_view = _candidate_semantic_view_metrics(bounds, texts)
        selection_score = (
            score + (1000 if closed_count else 0)
            + len(context_inserts) * 4 + semantic_anchor_count * 10 + len(context_texts) * 2
            + semantic_view["room_anchor_count"] * 1000
            + semantic_view["positive_plan_title_count"] * 2000
            - semantic_view["negative_view_title_count"] * 2000
        )
        svg_path = os.path.join(root, f"candidate_{index}.svg")
        _candidate_preview_svg(
            svg_path, rows, bounds, f"CAD candidate {index}",
            context_rows=[row for row in geometry if not row.get("wall_candidate")],
        )
        png_path = os.path.join(root, f"candidate_{index}.png")
        _candidate_preview_png(
            png_path, rows, bounds, f"CAD candidate {index}",
            context_rows=[row for row in geometry if not row.get("wall_candidate")],
        )
        candidates.append({
            "candidate_id": f"cad_plan_{index}",
            "entity_indexes": indexes, "structure_entity_indexes": indexes,
            "structural_entity_count": len(indexes),
            "bbox_m": [round(value, 5) for value in bounds], "length_m": round(length, 5),
            "bbox_area_m2": round(area, 5), "closed_region_count": int(closed_count),
            "score": round(score, 5), "selection_score": round(selection_score, 5),
            "context_insert_count": len(context_inserts),
            "semantic_anchor_count": semantic_anchor_count,
            "context_text_count": len(context_texts),
            **semantic_view,
            "preview_path": png_path, "diagnostic_svg_path": svg_path,
        })
    base_candidates = copy.deepcopy(candidates)
    for composite_number, member_indexes in enumerate(
            _semantic_plan_composite_groups(base_candidates, texts), 1):
        members = [base_candidates[index] for index in member_indexes]
        indexes = sorted({
            int(value) for member in members
            for value in member.get("structure_entity_indexes") or []
        })
        rows = [geometry[value] for value in indexes]
        all_points = [point for row in rows for point in row["points"]]
        bounds = _bbox(all_points)
        length = sum(math.dist(a, b) for row in rows
                     for a, b in zip(row["points"], row["points"][1:]))
        area = max(0.0, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
        closed_count = sum(
            row["closed"] and _poly_area(
                row["points"][:-1]
                if row["points"][0] == row["points"][-1]
                else row["points"]) >= .5
            for row in rows)
        context_inserts = [
            row for row in inserts
            if bounds[0] <= row["point"][0] <= bounds[2]
            and bounds[1] <= row["point"][1] <= bounds[3]
        ]
        context_texts = [
            row for row in texts
            if row.get("point_m")
            and bounds[0] <= row["point_m"][0] <= bounds[2]
            and bounds[1] <= row["point_m"][1] <= bounds[3]
        ]
        semantic_anchor_count = sum(
            bool(row.get("semantic_role") or row.get("opening_kind"))
            for row in context_inserts)
        semantic_view = _candidate_semantic_view_metrics(bounds, texts)
        if (semantic_view["room_anchor_count"] < 2
                or semantic_view["negative_view_title_count"]):
            continue
        score = length + min(area, 10000) * .05 + closed_count * 10
        selection_score = (
            score + (1000 if closed_count else 0)
            + len(context_inserts) * 4
            + semantic_anchor_count * 10
            + len(context_texts) * 2
            + semantic_view["room_anchor_count"] * 1000
            + semantic_view["positive_plan_title_count"] * 2000
            + 250
        )
        member_ids = [str(row["candidate_id"]) for row in members]
        candidate_id = "cad_plan_composite_" + "_".join(
            value.rsplit("_", 1)[-1] for value in member_ids)
        svg_path = os.path.join(root, f"candidate_composite_{composite_number}.svg")
        png_path = os.path.join(root, f"candidate_composite_{composite_number}.png")
        context_rows = [row for row in geometry if not row.get("wall_candidate")]
        _candidate_preview_svg(
            svg_path, rows, bounds,
            f"CAD composite plan candidate {composite_number}",
            context_rows=context_rows)
        _candidate_preview_png(
            png_path, rows, bounds,
            f"CAD composite plan candidate {composite_number}",
            context_rows=context_rows)
        candidates.append({
            "candidate_id": candidate_id,
            "entity_indexes": indexes,
            "structure_entity_indexes": indexes,
            "structural_entity_count": len(indexes),
            "bbox_m": [round(value, 5) for value in bounds],
            "length_m": round(length, 5),
            "bbox_area_m2": round(area, 5),
            "closed_region_count": int(closed_count),
            "score": round(score, 5),
            "selection_score": round(selection_score, 5),
            "context_insert_count": len(context_inserts),
            "semantic_anchor_count": semantic_anchor_count,
            "context_text_count": len(context_texts),
            **semantic_view,
            "composite_member_candidate_ids": member_ids,
            "composite_evidence": {
                "method": "adjacent_semantic_floor_plan_clusters_v1",
                "member_candidate_ids": member_ids,
                "minimum_room_anchor_count": 2,
                "negative_view_title_count": 0,
                "decision_basis": [
                    "substantial_projection_overlap",
                    "bounded_orthogonal_view_gap",
                    "multiple_room_text_anchors",
                    "no_elevation_or_section_title",
                ],
            },
            "preview_path": png_path,
            "diagnostic_svg_path": svg_path,
        })
    locked_indexes: list[int] = []
    if _unit_resolution_evidence:
        locked_indexes = sorted({
            int(value) for value in
            (_unit_resolution_evidence.get("selected_structure_entity_indexes") or [])
            if isinstance(value, int) or str(value).isdigit()
        }.intersection(structural_indexes))
    if locked_indexes:
        # Candidate connectivity uses metric tolerances, so a wrong header can
        # merge a complete plan on pass one and split it into wall fragments on
        # the corrected pass.  Keep the exact source-entity selection that the
        # explicit annotations proved; a scale-dependent candidate id is not a
        # stable identity contract.
        rows = [geometry[value] for value in locked_indexes]
        all_points = [point for row in rows for point in row["points"]]
        bounds = _bbox(all_points)
        length = sum(math.dist(a, b) for row in rows
                     for a, b in zip(row["points"], row["points"][1:]))
        area = max(0.0, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
        closed_count = sum(
            row["closed"]
            and _poly_area(row["points"][:-1]
                           if row["points"][0] == row["points"][-1]
                           else row["points"]) >= 0.5
            for row in rows)
        context_inserts = [
            row for row in inserts
            if bounds[0] <= row["point"][0] <= bounds[2]
            and bounds[1] <= row["point"][1] <= bounds[3]
        ]
        context_texts = [
            row for row in texts
            if row.get("point_m")
            and bounds[0] <= row["point_m"][0] <= bounds[2]
            and bounds[1] <= row["point_m"][1] <= bounds[3]
        ]
        semantic_anchor_count = sum(
            bool(row.get("semantic_role") or row.get("opening_kind"))
            for row in context_inserts)
        score = length + min(area, 10000) * .05 + closed_count * 10
        selection_score = (
            score + (1000 if closed_count else 0)
            + len(context_inserts) * 4
            + semantic_anchor_count * 10
            + len(context_texts) * 2
        )
        svg_path = os.path.join(root, "candidate_unit_evidence.svg")
        png_path = os.path.join(root, "candidate_unit_evidence.png")
        context_rows = [row for row in geometry if not row.get("wall_candidate")]
        _candidate_preview_svg(
            svg_path, rows, bounds, "CAD candidate: unit evidence lock",
            context_rows=context_rows)
        _candidate_preview_png(
            png_path, rows, bounds, "CAD candidate: unit evidence lock",
            context_rows=context_rows)
        candidates.append({
            "candidate_id": "cad_plan_unit_evidence",
            "entity_indexes": locked_indexes,
            "structure_entity_indexes": locked_indexes,
            "structural_entity_count": len(locked_indexes),
            "bbox_m": [round(value, 5) for value in bounds],
            "length_m": round(length, 5), "bbox_area_m2": round(area, 5),
            "closed_region_count": int(closed_count),
            "score": round(score, 5), "selection_score": round(selection_score, 5),
            "context_insert_count": len(context_inserts),
            "semantic_anchor_count": semantic_anchor_count,
            "context_text_count": len(context_texts),
            "preview_path": png_path, "diagnostic_svg_path": svg_path,
            "selection_lock": (
                "explicit_annotation_proven_source_entities_v1"
                if str((_unit_resolution_evidence or {}).get("method") or "")
                == "cad_explicit_annotation_unit_resolution_v1"
                else "metric_plan_metadata_proven_source_entities_v1"
            ),
        })
    candidates.sort(key=lambda row: row["selection_score"], reverse=True)
    hard_errors: list[dict] = []
    candidate_by_id = {str(row["candidate_id"]): row for row in candidates}
    preferred_candidate_id = str(preferred_candidate_id or "").strip()
    if preferred_candidate_id and preferred_candidate_id not in candidate_by_id:
        raise CadError(
            "cad_candidate_not_found", "指定 CAD 平面候选不存在，未调用任何 AI",
            details={"candidate_id": preferred_candidate_id,
                     "available_candidate_ids": list(candidate_by_id)},
        )
    unit_locked_candidate = candidate_by_id.get("cad_plan_unit_evidence")
    if (not preferred_candidate_id and unit_locked_candidate is None and len(candidates) > 1
            and candidates[1]["selection_score"] >= candidates[0]["selection_score"] * 0.98
            and not _near_duplicate_candidate_views(candidates[0], candidates[1])):
        hard_errors.append({"code": "cad_ambiguous_plan_candidates", "message": "多个候选平面得分接近，必须人工选择",
                            "candidate_ids": [candidates[0]["candidate_id"], candidates[1]["candidate_id"]]})
    if geometry_authority_evidence.get("status") == "ambiguous":
        hard_errors.append({
            "code": "cad_ambiguous_geometry_plan_candidates",
            "message": "多个非语义 CAD 平面候选均满足几何门禁，必须显式选择",
            "candidate_ids": [
                str(row.get("candidate_id") or "")
                for row in geometry_authority_evidence.get("candidates") or []
                if row.get("proof_status") == "proved"
            ][:10],
        })
    selected = (candidate_by_id.get(preferred_candidate_id)
                or unit_locked_candidate or candidates[0])
    if _unit_scale_override is None:
        unit_resolution = _infer_annotation_unit_resolution(
            selected, texts,
            declared_units=units,
            declared_scale=float(declared_scale),
        )
        if unit_resolution is None:
            unit_resolution = _infer_metric_plan_metadata_unit_resolution(
                selected, texts, dimension_styles, dimensions,
                measurement_system=measurement_system,
                declared_units=units,
                declared_scale=float(declared_scale),
            )
        if unit_resolution is not None:
            # ``candidates`` below are clustered with a fixed 350 mm metric
            # tolerance.  When the header unit is wrong, that temporary view is
            # scale-dependent and can omit genuine wall fragments before the
            # corrected pass.  The geometry-only authority above uses a
            # drawing-diagonal-relative tolerance and is the stable source
            # identity contract.  Lock it when available, then let the strict
            # role decomposition remove fixtures after re-scaling.
            authority_indexes = [
                int(value) for value in
                (geometry_authority_evidence.get("selected_indexes") or [])
                if isinstance(value, int) or str(value).isdigit()
            ] if (geometry_authority_evidence.get("method")
                  == "cad_geometry_plan_authority_v1"
                  and geometry_authority_evidence.get("status") == "proved") else []
            stable_structure_indexes = sorted(set(
                authority_indexes or [
                    int(value) for value in selected["structure_entity_indexes"]]))
            unit_resolution["selected_structure_entity_indexes"] = [
                int(value) for value in stable_structure_indexes]
            unit_resolution["selected_source_handles"] = sorted({
                str((geometry[value].get("cad_provenance") or {}).get("source_handle")
                    or (geometry[value].get("cad_provenance") or {}).get("root_handle")
                    or "")
                for value in stable_structure_indexes
                if str((geometry[value].get("cad_provenance") or {}).get("source_handle")
                       or (geometry[value].get("cad_provenance") or {}).get("root_handle")
                       or "")
            })
            unit_resolution["candidate_identity_lock"] = (
                "scale_invariant_geometry_authority_source_entities_v2"
                if authority_indexes else
                "stable_source_entity_indexes_and_handles_v1")
            # Re-read and re-flatten the source at the proven metric scale so
            # ARC/SPLINE tessellation still honors the requested 5 mm chord
            # error.  Scaling already-flattened points would silently turn the
            # initial inch-scale chord error into a coarse metric curve.
            return parse_dxf(
                path, project_id,
                chord_error_m=chord_error_m,
                preferred_candidate_id=preferred_candidate_id,
                _unit_scale_override=float(
                    unit_resolution["resolved_metres_per_unit"]),
                _unit_resolution_evidence=unit_resolution,
            )
    selected_rows = [geometry[index] for index in selected["structure_entity_indexes"]]
    selected_bounds = selected["bbox_m"]
    attached_exterior_space_evidence = _prove_attached_exterior_double_boundary(
        geometry, selected["structure_entity_indexes"], selected_bounds)
    if attached_exterior_space_evidence.get("status") == "proved":
        attached_indexes = {
            int(value) for value in attached_exterior_space_evidence.get(
                "promoted_entity_indexes") or []
            if isinstance(value, int) or str(value).isdigit()
        }
        selected_indexes_with_exterior = sorted(set(
            int(value) for value in selected["structure_entity_indexes"]
        ) | attached_indexes)
        selected_rows = [geometry[index]
                         for index in selected_indexes_with_exterior]
        selected_bounds = copy.deepcopy(
            attached_exterior_space_evidence["expanded_candidate_bbox_m"])
        selected["structure_entity_indexes"] = copy.deepcopy(
            selected_indexes_with_exterior)
        selected["entity_indexes"] = sorted(set(
            int(value) for value in selected.get("entity_indexes") or [])
            | attached_indexes)
        selected["original_bbox_m"] = copy.deepcopy(selected.get("bbox_m") or [])
        selected["bbox_m"] = [round(float(value), 5)
                              for value in selected_bounds]
        selected["attached_exterior_space_count"] = len(
            attached_exterior_space_evidence.get("spaces") or [])
        selected["selection_reasons"] = sorted(set(
            (selected.get("selection_reasons") or [])
            + ["attached_exterior_double_boundary_geometry_proved"]))
        authority_indexes = {
            int(value) for value in geometry_authority_evidence.get(
                "selected_indexes") or []
            if isinstance(value, int) or str(value).isdigit()
        }
        geometry_authority_evidence["selected_indexes"] = sorted(
            authority_indexes | attached_indexes)
        geometry_authority_evidence["attached_exterior_space_evidence"] = \
            copy.deepcopy(attached_exterior_space_evidence)
        geometry_authority_evidence["decision_basis"] = sorted(set(
            (geometry_authority_evidence.get("decision_basis") or [])
            + ["attached_exterior_double_boundary_geometry_proved"]))
        for space in attached_exterior_space_evidence.get("spaces") or []:
            for chain_kind, key in (("outer", "outer_chain_entity_indexes"),
                                    ("inner", "inner_chain_entity_indexes")):
                for value in space.get(key) or []:
                    try:
                        row = geometry[int(value)]
                    except (TypeError, ValueError, IndexError):
                        continue
                    row["attached_exterior_boundary_evidence"] = {
                        "method": "cad_attached_exterior_double_boundary_v1",
                        "space_id": str(space.get("space_id") or ""),
                        "chain_kind": chain_kind,
                        "chain_entity_indexes": copy.deepcopy(space.get(key) or []),
                        "measured_boundary_separation_m": float(
                            space.get("measured_boundary_separation_m") or 0.0),
                        "attachment_side": str(space.get("attachment_side") or ""),
                    }
    projected_plan_structure_filter = _filter_text_free_projected_plan_structure(
        selected_rows, selected_bounds, geometry_authority_evidence)
    if projected_plan_structure_filter.get("status") == "proved":
        retained_indexes = {
            int(value) for value in projected_plan_structure_filter.get(
                "retained_entity_indexes") or []
        }
        excluded_indexes = {
            int(value) for value in projected_plan_structure_filter.get(
                "excluded_entity_indexes") or []
        }
        short_nonorthogonal_indexes = {
            int(value) for value in projected_plan_structure_filter.get(
                "short_nonorthogonal_detail_entity_indexes") or []
        }
        selected_rows = [
            row for row in selected_rows
            if int(row.get("entity_index", -1)) in retained_indexes
        ]
        selected["structure_entity_indexes_before_projected_plan_filter"] = \
            copy.deepcopy(selected.get("structure_entity_indexes") or [])
        selected["structure_entity_indexes"] = sorted(retained_indexes)
        selected["projected_plan_structure_filter"] = copy.deepcopy(
            projected_plan_structure_filter)
        geometry_authority_evidence["projected_plan_structure_filter"] = \
            copy.deepcopy(projected_plan_structure_filter)
        for index in excluded_indexes:
            if not 0 <= index < len(geometry):
                continue
            geometry[index]["wall_candidate"] = False
            geometry[index]["structural_exclusion_reason"] = (
                "dense_projected_plan_short_nonorthogonal_detail"
                if index in short_nonorthogonal_indexes else
                "dense_projected_plan_nonprimary_ink_component")
            geometry[index]["projected_plan_structure_filter_evidence"] = {
                "method": "cad_dense_projected_plan_primary_structure_v1",
                "status": "excluded",
                "source_root_handle": projected_plan_structure_filter.get(
                    "source_root_handle"),
                "connection_tolerance_m": projected_plan_structure_filter.get(
                    "connection_tolerance_m"),
                "reason": (
                    "short_tessellated_nonorthogonal_detail"
                    if index in short_nonorthogonal_indexes else
                    "nonprimary_ink_component"),
            }
    has_explicit_room_boundaries = any(
        re.search(r"(?:room|space).*(?:boundary|outline)|(?:boundary|outline).*(?:room|space)",
                  str(row.get("layer") or ""), re.I)
        and selected_bounds[0] - .01 <= (row.get("bbox") or selected_bounds)[0]
        and (row.get("bbox") or selected_bounds)[2] <= selected_bounds[2] + .01
        and selected_bounds[1] - .01 <= (row.get("bbox") or selected_bounds)[1]
        and (row.get("bbox") or selected_bounds)[3] <= selected_bounds[3] + .01
        for row in geometry
    )
    if has_explicit_room_boundaries:
        selected_indexes = set(selected["structure_entity_indexes"])
        selected_indexes.update(
            index for index in structural_indexes
            if selected_bounds[0] - .01 <= (geometry[index].get("bbox") or selected_bounds)[0]
            and (geometry[index].get("bbox") or selected_bounds)[2] <= selected_bounds[2] + .01
            and selected_bounds[1] - .01 <= (geometry[index].get("bbox") or selected_bounds)[1]
            and (geometry[index].get("bbox") or selected_bounds)[3] <= selected_bounds[3] + .01
        )
        selected_rows = [geometry[index] for index in sorted(selected_indexes)]
    selected_input_entity_indexes = {
        int(row.get("entity_index")) for row in selected_rows
        if isinstance(row.get("entity_index"), int)
    }
    role_context_rows = [
        row for row in geometry
        if int(row.get("entity_index", -1)) not in selected_input_entity_indexes
        and selected_bounds[0] - .35 <= (row.get("bbox") or selected_bounds)[0]
        and (row.get("bbox") or selected_bounds)[2] <= selected_bounds[2] + .35
        and selected_bounds[1] - .35 <= (row.get("bbox") or selected_bounds)[1]
        and (row.get("bbox") or selected_bounds)[3] <= selected_bounds[3] + .35
    ]
    role_semantic_anchors: list[dict] = []
    semantic_margin = max(
        selected_bounds[2] - selected_bounds[0],
        selected_bounds[3] - selected_bounds[1],
    ) * .02
    for text_index, text_row in enumerate(texts):
        point = text_row.get("point_m")
        try:
            point_m = (float(point[0]), float(point[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if not (selected_bounds[0] - semantic_margin <= point_m[0]
                <= selected_bounds[2] + semantic_margin
                and selected_bounds[1] - semantic_margin <= point_m[1]
                <= selected_bounds[3] + semantic_margin):
            continue
        reference_profile, semantic_profile = _room_profile_from_text(
            str(text_row.get("text") or ""))
        if not reference_profile or not semantic_profile:
            continue
        provenance = text_row.get("cad_provenance") or {}
        role_semantic_anchors.append({
            "anchor_id": f"cad_role_anchor_{text_index + 1}",
            "point_m": point_m,
            "reference_profile": reference_profile,
            "semantic_profile": semantic_profile,
            "source_handle": str(provenance.get("handle") or ""),
        })
    role_decomposition = decompose_cad_entity_roles(
        selected_rows, context_rows=role_context_rows,
        semantic_anchors=role_semantic_anchors)
    selected_rows = role_decomposition["wall_rows"]
    selected_entity_role_summary = role_decomposition["summary"]
    selected_entity_role_evidence = role_decomposition["evidence"]
    raw_opening_candidates = role_decomposition["raw_opening_candidates"]
    raw_opening_summary = role_decomposition["raw_opening_summary"]
    semantic_building_envelope_evidence = copy.deepcopy(
        role_decomposition.get("semantic_building_envelope_evidence") or {})
    semantic_building_envelope_diagnostics = copy.deepcopy(
        role_decomposition.get("semantic_building_envelope_diagnostics") or {})
    if not selected_rows:
        hard_errors.append({
            "code": "cad_no_authoritative_wall_geometry",
            "message": "CAD 实体角色分解后没有可证明的墙体几何",
        })
    semantic_preview_path = os.path.join(root, "selected_candidate.png")
    semantic_preview_mapping = _candidate_preview_png(
        semantic_preview_path, selected_rows, tuple(selected["bbox_m"]),
        f"Selected CAD plan: {selected['candidate_id']}",
        context_rows=[
            *role_decomposition["opening_evidence_rows"],
            *role_decomposition["context_rows"],
            *role_decomposition["review_rows"],
            *[row for row in geometry if not row.get("wall_candidate")],
        ],
    )
    min_x, min_y, max_x, max_y = selected["bbox_m"]
    origin_x, origin_y = min_x, min_y
    marker_root_geometry_points: dict[str, list[tuple[float, float]]] = \
        defaultdict(list)
    for geometry_row in geometry:
        if geometry_row.get("wall_candidate"):
            continue
        geometry_provenance = geometry_row.get("cad_provenance") or {}
        geometry_root_handle = str(
            geometry_provenance.get("root_handle")
            or geometry_provenance.get("source_handle") or "")
        if geometry_root_handle:
            marker_root_geometry_points[geometry_root_handle].extend(
                geometry_row.get("points") or [])
    text_anchors: list[dict] = []
    for source_kind, source_rows in (("text", texts), ("insert", inserts)):
        for source_index, source_row in enumerate(source_rows, 1):
            point = source_row.get("point_m") if source_kind == "text" else source_row.get("point")
            text = str(source_row.get("text") if source_kind == "text" else source_row.get("name") or "")
            reference_profile, semantic_profile = _room_profile_from_text(text)
            if not point or not (min_x <= point[0] <= max_x and min_y <= point[1] <= max_y):
                continue
            text_anchors.append({
                "anchor_id": f"cad_anchor_{source_kind}_{source_index}",
                "source_kind": source_kind, "text": text[:100],
                "point_m": [float(point[0]), float(point[1])],
                "point": {"x": round(float(point[0]) - origin_x, 5),
                          "z": round(float(point[1]) - origin_y, 5)},
                "reference_profile": reference_profile,
                "semantic_profile": semantic_profile,
                "cad_provenance": copy.deepcopy(source_row.get("cad_provenance") or {}),
            })
            if source_kind != "insert":
                continue
            provenance = source_row.get("cad_provenance") or {}
            root_handle = str(
                provenance.get("root_handle")
                or provenance.get("source_handle") or "")
            root_points = marker_root_geometry_points.get(root_handle) or []
            semantic_role = str(
                source_row.get("semantic_role")
                or _role_from_symbol_footprint(
                    str(source_row.get("layer") or ""), root_points,
                    root_geometry_features.get(root_handle)))
            if semantic_role != "bed" or not root_points:
                continue
            marker_bounds = _bbox(root_points)
            marker_point = [
                float((marker_bounds[0] + marker_bounds[2]) / 2),
                float((marker_bounds[1] + marker_bounds[3]) / 2),
            ]
            if not (min_x <= marker_point[0] <= max_x
                    and min_y <= marker_point[1] <= max_y):
                continue
            text_anchors.append({
                "anchor_id": f"cad_space_marker_bed_{source_index}",
                "source_kind": "space_marker",
                "text": "",
                "point_m": marker_point,
                "point": {
                    "x": round(marker_point[0] - origin_x, 5),
                    "z": round(marker_point[1] - origin_y, 5),
                },
                "reference_profile": "",
                "semantic_profile": "",
                "space_marker": "bed",
                "cad_provenance": copy.deepcopy(provenance),
            })
    room_boundary_rows = [
        row for row in geometry
        if re.search(r"(?:room|space).*(?:boundary|outline)|(?:boundary|outline).*(?:room|space)",
                     str(row.get("layer") or ""), re.I)
        and len(row.get("points") or []) >= 2
        and min_x - .01 <= (row.get("bbox") or (min_x,))[0]
        and (row.get("bbox") or (0, 0, max_x))[2] <= max_x + .01
        and min_y - .01 <= (row.get("bbox") or (0, min_y))[1]
        and (row.get("bbox") or (0, 0, 0, max_y))[3] <= max_y + .01
    ]
    linework = [LineString(row["points"]) for row in selected_rows if len(row["points"]) >= 2]
    if room_boundary_rows:
        # Explicit room/space-boundary layers are stronger room evidence than
        # re-polygonizing their loops together with overlapping double-line
        # walls.  The latter creates artificial remainder cells at concave
        # space corners.  Walls stay authoritative for WallAssembly; these
        # closed boundary entities are authoritative only for physical faces.
        polygonized = []
        for row in room_boundary_rows:
            points = row.get("points") or []
            candidate = Polygon(points[:-1] if points[0] == points[-1] else points).buffer(0)
            if candidate.geom_type == "Polygon" and candidate.area >= .5:
                polygonized.append(candidate)
    else:
        polygonized = [
            polygon for polygon in polygonize(unary_union(linework)) if polygon.area >= .5
        ]
    raw_faces, accepted_faces = classify_raw_faces(
        polygonized, origin_x=origin_x, origin_z=origin_y,
        text_anchors=text_anchors, surface_regions=hatch_surfaces)
    if not accepted_faces:
        hard_errors.append({"code": "cad_no_closed_regions", "message": "过滤墙体条带、外框和小碎面后没有可确认的物理空间"})
    wall_assembly_warnings: list[dict] = []
    if int(selected_entity_role_summary.get("review_entity_count") or 0):
        wall_assembly_warnings.append({
            "code": "cad_entity_role_review_required",
            "message": "部分继承到结构层的紧凑或嵌套几何已从自动墙体中隔离并保留人工复查证据",
            "count": int(selected_entity_role_summary.get("review_entity_count") or 0),
        })
    try:
        wall_assemblies = build_wall_assemblies(
            selected_rows, wall_height_m=2.8,
            height_source="project_default_assumption",
            origin_x=origin_x, origin_z=origin_y,
        )
    except WallAssemblyError as ex:
        # Keep the source-backed legacy projection available for review, but
        # never silently bless its historic 120mm render thickness.  The v3
        # acceptance gate treats an absent assembly set as unresolved.
        wall_assemblies = []
        wall_assembly_warnings.append({
            "code": ex.code, "message": ex.message,
            "details": copy.deepcopy(ex.details),
        })
    wall_assemblies = stitch_wall_assemblies_across_openings(
        wall_assemblies, raw_opening_candidates,
        origin_x=origin_x, origin_z=origin_y,
    )
    raw_opening_candidates = bind_raw_geometry_openings(
        raw_opening_candidates, wall_assemblies,
        origin_x=origin_x, origin_z=origin_y,
    )
    raw_opening_summary = summarize_raw_geometry_openings(raw_opening_candidates)
    rejected_redundant_wall_evidence = [
        row for row in wall_assemblies
        if row.get("review_status") in {"rejected", "reject"}
        and row.get("source_representation") == "redundant_evidence"
    ]
    # Rejected provenance-only evidence has already received a terminal,
    # source-backed disposition.  It must remain available for audit, but it
    # is neither production geometry nor an unresolved production decision.
    unresolved_wall_assemblies = [
        row for row in wall_assemblies
        if row.get("review_status") not in {"rejected", "reject"}
        and (
            row.get("review_status") not in {"accepted", "confirmed"}
            or not row.get("footprint_polygon")
            or not row.get("centerline")
            or not row.get("thickness_m")
        )
    ]
    if unresolved_wall_assemblies:
        wall_assembly_warnings.append({
            "code": "cad_wall_assembly_review_required",
            "message": "部分 CAD 结构线无法自动证明为双墙面、墙体 footprint 或带实测厚度的中心线",
            "assembly_ids": [str(row.get("id") or "")
                             for row in unresolved_wall_assemblies[:100]],
            "count": len(unresolved_wall_assemblies),
        })

    global_wall_footprints: list[dict] = []
    global_wall_topology: dict = {}
    terminal_open_connection_evidence: dict = {
        "schema_version": 1,
        "method": "cad_labeled_terminal_open_connection_v1",
        "status": "not_evaluated", "proposal_count": 0, "proved_count": 0,
        "connections": [],
    }
    projected_detail_topology_invariance: dict = {
        "schema_version": 1,
        "method": "cad_projected_detail_topology_invariance_v1",
        "status": "not_evaluated",
    }
    try:
        topology_result = build_global_wall_topology(
            selected_rows,
            wall_assemblies=wall_assemblies,
            opening_candidates=raw_opening_candidates,
            semantic_anchors=text_anchors,
            origin_x=origin_x,
            origin_z=origin_y,
            wall_height_m=2.8,
        )
        global_wall_footprints = topology_result["wall_footprints"]
        global_wall_topology = topology_result["summary"]
        accepted_before_global_binding = sum(
            str(row.get("status") or "") in {"accepted", "confirmed"}
            for row in raw_opening_candidates)
        raw_opening_candidates, wall_assemblies = (
            _bind_openings_to_global_wall_footprints(
                raw_opening_candidates, wall_assemblies,
                global_wall_footprints, origin_x=origin_x, origin_z=origin_y))
        (raw_opening_candidates, wall_assemblies,
         terminal_open_connection_evidence) = (
            _infer_labeled_terminal_open_connections(
                selected_rows, wall_assemblies, raw_opening_candidates,
                text_anchors, origin_x=origin_x, origin_z=origin_y))
        wall_assemblies = _resolve_wall_evidence_coincident_with_accepted_openings(
            wall_assemblies, raw_opening_candidates,
            origin_x=origin_x, origin_z=origin_y)
        accepted_before_face_continuations = sum(
            str(row.get("review_status") or "") in {"accepted", "confirmed"}
            for row in wall_assemblies)
        wall_assemblies = _resolve_collinear_wall_face_continuations(
            wall_assemblies)
        accepted_after_face_continuations = sum(
            str(row.get("review_status") or "") in {"accepted", "confirmed"}
            for row in wall_assemblies)
        raw_opening_summary = summarize_raw_geometry_openings(
            raw_opening_candidates)
        accepted_after_global_binding = sum(
            str(row.get("status") or "") in {"accepted", "confirmed"}
            for row in raw_opening_candidates)
        if (accepted_after_global_binding > accepted_before_global_binding
                or accepted_after_face_continuations
                > accepted_before_face_continuations):
            # Accepted global hosts contribute source-backed topology barriers;
            # rebuild room discovery without changing the rendered wall mask.
            topology_result = build_global_wall_topology(
                selected_rows, wall_assemblies=wall_assemblies,
                opening_candidates=raw_opening_candidates,
                semantic_anchors=text_anchors,
                origin_x=origin_x, origin_z=origin_y, wall_height_m=2.8)
            global_wall_footprints = topology_result["wall_footprints"]
            global_wall_topology = topology_result["summary"]
        topology_spaces = topology_result.get("_space_polygons") or []
        if topology_spaces:
            topology_raw_faces, topology_accepted_faces = classify_raw_faces(
                topology_spaces,
                origin_x=origin_x,
                origin_z=origin_y,
                text_anchors=text_anchors,
                surface_regions=hatch_surfaces,
            )
            if topology_accepted_faces:
                raw_faces, accepted_faces = topology_raw_faces, topology_accepted_faces
                hard_errors = [
                    row for row in hard_errors
                    if row.get("code") != "cad_no_closed_regions"
                ]
            wall_assemblies = _resolve_wall_evidence_with_global_topology(
                wall_assemblies, global_wall_footprints, topology_spaces,
                global_wall_topology, origin_x=origin_x, origin_z=origin_y,
                building_envelope_evidence=
                    semantic_building_envelope_evidence)
            if projected_plan_structure_filter.get("status") == "proved":
                # Keep one immutable reference topology.  A dense direct-
                # primitive plan can require a first partial micro-detail pass
                # and then a second complete pass over the now-bounded pending
                # set.  Every accepted pass is compared directly with this
                # reference, so sequential tolerances cannot accumulate drift.
                pruning_reference_topology = topology_result
                pruning_trial = _prune_topology_invariant_projected_detail(
                    selected_rows, wall_assemblies, pruning_reference_topology,
                    projected_plan_structure_filter,
                    opening_candidates=raw_opening_candidates,
                    semantic_anchors=text_anchors,
                    origin_x=origin_x, origin_z=origin_y,
                    wall_height_m=2.8,
                )
                projected_detail_topology_invariance = {
                    key: copy.deepcopy(value)
                    for key, value in pruning_trial.items()
                    if not str(key).startswith("_")
                }
                if pruning_trial.get("status") == "proved":
                    pruning_passes: list[dict] = []
                    terminal_evidence_assemblies: list[dict] = []
                    rebound_wall_assemblies: list[dict] = []
                    active_trial = pruning_trial
                    for pass_number in range(2):
                        selected_rows = active_trial["_trial_rows"]
                        topology_result = active_trial["_trial_topology"]
                        global_wall_footprints = topology_result[
                            "wall_footprints"]
                        global_wall_topology = topology_result["summary"]
                        # Each trial rebuild starts from source rows.  Recreate
                        # opening hosts against its footprints instead of
                        # retaining stale assembly owner IDs from the previous
                        # pass.
                        trial_assembly_ids = {
                            str(row.get("id") or "")
                            for row in active_trial["_trial_assemblies"]
                        }
                        rebind_candidates = copy.deepcopy(
                            raw_opening_candidates)
                        for candidate in rebind_candidates:
                            owner_id = str(
                                candidate.get("wall_assembly_id") or "")
                            if (str(candidate.get("status") or "")
                                    not in {"accepted", "confirmed"}
                                    or not owner_id
                                    or owner_id in trial_assembly_ids):
                                continue
                            candidate["status"] = "review"
                            candidate.pop("wall_assembly_id", None)
                            candidate.pop("offset_m", None)
                            candidate["reason_codes"] = [
                                str(code) for code in candidate.get(
                                    "reason_codes") or []
                                if str(code) not in {
                                    "canonical_wall_axis_bound",
                                    "opening_compatibility_wall_unresolved",
                                }
                            ]
                        raw_opening_candidates, rebound_wall_assemblies = (
                            _bind_openings_to_global_wall_footprints(
                                rebind_candidates,
                                active_trial["_trial_assemblies"],
                                global_wall_footprints,
                                origin_x=origin_x, origin_z=origin_y))
                        rebound_wall_assemblies = (
                            _resolve_wall_evidence_coincident_with_accepted_openings(
                                rebound_wall_assemblies,
                                raw_opening_candidates,
                                origin_x=origin_x, origin_z=origin_y))
                        pass_public = {
                            key: copy.deepcopy(value)
                            for key, value in active_trial.items()
                            if not str(key).startswith("_")
                        }
                        pruning_passes.append(pass_public)
                        for terminal in active_trial[
                                "_terminal_evidence_assemblies"]:
                            record = copy.deepcopy(terminal)
                            representation = record.get(
                                "source_representation")
                            prefix = (
                                "cad_projected_topology_boundary_evidence_"
                                if representation
                                == "projected_topology_boundary_evidence"
                                else "cad_projected_geometry_dependency_evidence_"
                                if representation
                                == "projected_geometry_dependency_evidence"
                                else "cad_projected_detail_evidence_")
                            record["id"] = (
                                f"{prefix}"
                                f"{len(terminal_evidence_assemblies) + 1}")
                            terminal_evidence_assemblies.append(record)
                        raw_opening_summary = summarize_raw_geometry_openings(
                            raw_opening_candidates)
                        if pass_number != 0:
                            break
                        subsequent_trial = (
                            _prune_topology_invariant_projected_detail(
                                selected_rows, rebound_wall_assemblies,
                                pruning_reference_topology,
                                projected_plan_structure_filter,
                                opening_candidates=raw_opening_candidates,
                                semantic_anchors=text_anchors,
                                origin_x=origin_x, origin_z=origin_y,
                                wall_height_m=2.8,
                            ))
                        if subsequent_trial.get("status") != "proved":
                            direct_attempt = {
                                key: copy.deepcopy(value)
                                for key, value in subsequent_trial.items()
                                if not str(key).startswith("_")
                            }
                            subsequent_trial = (
                                _partition_topology_invariant_projected_detail(
                                    selected_rows, rebound_wall_assemblies,
                                    pruning_reference_topology,
                                    projected_plan_structure_filter,
                                    opening_candidates=raw_opening_candidates,
                                    semantic_anchors=text_anchors,
                                    origin_x=origin_x, origin_z=origin_y,
                                    wall_height_m=2.8,
                                ))
                            if subsequent_trial.get("status") != "proved":
                                projected_detail_topology_invariance[
                                    "subsequent_pass_attempt"] = direct_attempt
                                projected_detail_topology_invariance[
                                    "counterfactual_partition_attempt"] = {
                                    key: copy.deepcopy(value)
                                    for key, value in subsequent_trial.items()
                                    if not str(key).startswith("_")
                                }
                                break
                            projected_detail_topology_invariance[
                                "subsequent_pass_attempt"] = direct_attempt
                        active_trial = subsequent_trial
                    wall_assemblies = [
                        *rebound_wall_assemblies,
                        *terminal_evidence_assemblies,
                    ]
                    topology_spaces = topology_result.get(
                        "_space_polygons") or []
                    topology_raw_faces, topology_accepted_faces = \
                        classify_raw_faces(
                            topology_spaces,
                            origin_x=origin_x, origin_z=origin_y,
                            text_anchors=text_anchors,
                            surface_regions=hatch_surfaces,
                        )
                    if topology_accepted_faces:
                        raw_faces = topology_raw_faces
                        accepted_faces = topology_accepted_faces
                    excluded_indexes = sorted({
                        int(index)
                        for proof in pruning_passes
                        for index in proof.get("excluded_entity_indexes") or []
                    })
                    pruned_count = len(excluded_indexes)
                    if len(pruning_passes) > 1:
                        final_pass = pruning_passes[-1]
                        projected_detail_topology_invariance.update({
                            "pass_count": len(pruning_passes),
                            "passes": pruning_passes,
                            "pruning_mode":
                                "sequential_topology_invariant_scope",
                            "excluded_entity_count": pruned_count,
                            "excluded_entity_ratio": round(
                                pruned_count / max(int(
                                    pruning_passes[0].get(
                                        "input_entity_count") or 0), 1), 8),
                            "excluded_entity_indexes": excluded_indexes,
                            "trial_space_count": final_pass.get(
                                "trial_space_count"),
                            "space_union_iou": final_pass.get(
                                "space_union_iou"),
                            "minimum_matched_space_iou": final_pass.get(
                                "minimum_matched_space_iou"),
                            "trial_wall_area_m2": final_pass.get(
                                "trial_wall_area_m2"),
                            "wall_area_reduction_ratio": final_pass.get(
                                "wall_area_reduction_ratio"),
                            "trial_unresolved_wall_assembly_count":
                                final_pass.get(
                                    "trial_unresolved_wall_assembly_count"),
                            "remaining_unresolved_source_entity_indexes":
                                final_pass.get(
                                    "remaining_unresolved_source_entity_indexes"),
                            "terminal_source_entity_count": sum(
                                int(proof.get(
                                    "terminal_source_entity_count") or 0)
                                for proof in pruning_passes),
                        })
                        decision_basis = list(
                            projected_detail_topology_invariance.get(
                                "decision_basis") or [])
                        if "second_pass_compared_to_original_topology" \
                                not in decision_basis:
                            decision_basis.append(
                                "second_pass_compared_to_original_topology")
                        projected_detail_topology_invariance[
                            "decision_basis"] = decision_basis
                    selected_entity_role_summary[
                        "topology_invariant_projected_detail_count"] = \
                        pruned_count
                    selected_entity_role_summary[
                        "retained_wall_entity_count"] = len(selected_rows)
                    role_counts = selected_entity_role_summary.setdefault(
                        "role_counts", {})
                    role_counts["wall_face"] = len(selected_rows)
                    role_counts["projected_detail"] = pruned_count
                    reason_counts = selected_entity_role_summary.setdefault(
                        "reason_counts", {})
                    reason_counts[
                        "cad_projected_detail_topology_invariant"] = pruned_count
    except GlobalTopologyError as ex:
        wall_assembly_warnings.append({
            "code": ex.code,
            "message": ex.message,
            "details": copy.deepcopy(ex.details),
        })

    if topology_spaces and global_wall_footprints:
        (wall_assemblies, global_wall_footprints,
         global_wall_topology) = _exclude_detached_site_boundary_components(
            wall_assemblies, global_wall_footprints, topology_spaces,
            global_wall_topology, origin_x=origin_x, origin_z=origin_y)

    # Global topology can resolve both a previously unhosted opening and the
    # coincident threshold/closed-leaf line that was correctly kept pending by
    # the local pass.  Production readiness and the visible review traces must
    # therefore be derived from the final assembly state, never the pre-global
    # snapshot above.
    rejected_redundant_wall_evidence = [
        row for row in wall_assemblies
        if row.get("review_status") in {"rejected", "reject"}
        and row.get("source_representation") == "redundant_evidence"
    ]
    rejected_opening_wall_evidence = [
        row for row in wall_assemblies
        if row.get("review_status") in {"rejected", "reject"}
        and row.get("source_representation") == "opening_evidence"
    ]
    unresolved_wall_assemblies = [
        row for row in wall_assemblies
        if row.get("review_status") not in {"rejected", "reject"}
        and (
            row.get("review_status") not in {"accepted", "confirmed"}
            or not row.get("footprint_polygon")
            or not row.get("centerline")
            or not row.get("thickness_m")
        )
    ]
    wall_assembly_warnings = [
        row for row in wall_assembly_warnings
        if row.get("code") != "cad_wall_assembly_review_required"
    ]
    if unresolved_wall_assemblies:
        wall_assembly_warnings.append({
            "code": "cad_wall_assembly_review_required",
            "message": "部分 CAD 结构线无法自动证明为双墙面、墙体 footprint 或带实测厚度的中心线",
            "assembly_ids": [str(row.get("id") or "")
                             for row in unresolved_wall_assemblies[:100]],
            "count": len(unresolved_wall_assemblies),
        })

    # The old compatibility projection extruded every raw wall-face segment
    # as a 120 mm centreline.  A normal double-line CAD wall therefore became
    # two parallel full-height walls, while unresolved evidence was presented
    # as if it were accepted geometry.  WallAssembly is the authority: a
    # terminal accepted assembly becomes a full-height wall with measured
    # thickness.  A non-terminal assembly is kept as a *low review trace* so
    # that a decorated real-world DWG remains spatially inspectable without
    # presenting an unmeasured face as production geometry.  Rejected
    # redundant evidence is audit-only and is never rendered.
    walls = []

    def model_point(value: Any) -> Optional[tuple[float, float]]:
        try:
            if isinstance(value, dict):
                return float(value["x"]), float(value["z"])
            return float(value[0]), float(value[1])
        except (KeyError, TypeError, ValueError, IndexError):
            return None

    for assembly in wall_assemblies:
        if assembly.get("review_status") not in {"accepted", "confirmed"}:
            continue
        centerline = assembly.get("centerline") or []
        if not isinstance(centerline, list) or len(centerline) < 2:
            continue

        first, second = model_point(centerline[0]), model_point(centerline[-1])
        if first is None or second is None or math.dist(first, second) < .10:
            continue
        provenance = copy.deepcopy(assembly.get("cad_provenance") or {})
        provenance.update({
            "wall_assembly_id": str(assembly.get("id") or ""),
            "wall_assembly_source_representation": str(
                assembly.get("source_representation") or ""),
            # validate_cad_model back-projects this canonical centreline.  Raw
            # face segments remain available under source_entities.
            "source_segment_m": [
                [round(first[0] + origin_x, 8), round(first[1] + origin_y, 8)],
                [round(second[0] + origin_x, 8), round(second[1] + origin_y, 8)],
            ],
        })
        if isinstance(assembly.get("frame_geometry_opening_evidence"), dict):
            provenance["frame_geometry_opening_evidence"] = copy.deepcopy(
                assembly["frame_geometry_opening_evidence"])
        if isinstance(assembly.get("global_topology_opening_evidence"), dict):
            provenance["global_topology_opening_evidence"] = copy.deepcopy(
                assembly["global_topology_opening_evidence"])
        if isinstance(assembly.get("door_swing_geometry_opening_evidence"), dict):
            provenance["door_swing_geometry_opening_evidence"] = copy.deepcopy(
                assembly["door_swing_geometry_opening_evidence"])
        if isinstance(assembly.get(
                "repeated_window_frame_opening_evidence"), dict):
            provenance["repeated_window_frame_opening_evidence"] = copy.deepcopy(
                assembly["repeated_window_frame_opening_evidence"])
        if isinstance(assembly.get("terminal_open_connection_evidence"), dict):
            provenance["terminal_open_connection_evidence"] = copy.deepcopy(
                assembly["terminal_open_connection_evidence"])
        walls.append({
            "id": f"cad_wall_{len(walls) + 1}",
            "wall_assembly_id": str(assembly.get("id") or ""),
            "start": {"x": round(first[0], 5), "z": round(first[1], 5)},
            "end": {"x": round(second[0], 5), "z": round(second[1], 5)},
            "kind": str(assembly.get("kind") or "interior"),
            "thickness_m": float(assembly.get("thickness_m") or 0),
            "height_m": float(assembly.get("height_m") or 2.8),
            "source": "cad", "confidence": float(assembly.get("confidence") or 0),
            "boundary_kind": "centerline",
            "review_status": "accepted",
            "cad_provenance": provenance,
        })

    for assembly in unresolved_wall_assemblies:
        centerline = assembly.get("source_centerline") or []
        if not isinstance(centerline, list) or len(centerline) < 2:
            continue
        first, second = model_point(centerline[0]), model_point(centerline[-1])
        if first is None or second is None or math.dist(first, second) < .10:
            continue
        provenance = copy.deepcopy(assembly.get("cad_provenance") or {})
        provenance.update({
            "wall_assembly_id": str(assembly.get("id") or ""),
            "wall_assembly_source_representation": str(
                assembly.get("source_representation") or ""),
            "source_segment_m": [
                [round(first[0] + origin_x, 8), round(first[1] + origin_y, 8)],
                [round(second[0] + origin_x, 8), round(second[1] + origin_y, 8)],
            ],
        })
        walls.append({
            "id": f"cad_wall_{len(walls) + 1}",
            "wall_assembly_id": str(assembly.get("id") or ""),
            "start": {"x": round(first[0], 5), "z": round(first[1], 5)},
            "end": {"x": round(second[0], 5), "z": round(second[1], 5)},
            "kind": "interior",
            # This is deliberately a 120 mm-high, 30 mm-wide floor trace, not
            # a guessed full-height wall.  The true thickness remains null on
            # WallAssembly until reviewed.
            "thickness_m": .03, "height_m": .12,
            "source": "cad_review_evidence", "confidence": 0.0,
            "boundary_kind": "unresolved_review_evidence",
            "review_status": "needs_review",
            "display_mode": "review_floor_trace",
            "cad_provenance": provenance,
        })
    if not walls:
        # Backward-compatible review surface for single-line/outline CAD that
        # has no accepted WallAssembly at all.  These rows are explicitly
        # marked as unresolved evidence and the WallAssembly warning remains;
        # once any measured assembly exists this fallback is never mixed into
        # canonical geometry.
        for row in selected_rows:
            for segment_index, (first, second) in enumerate(
                    zip(row["points"], row["points"][1:])):
                if math.dist(first, second) < .10:
                    continue
                walls.append({
                    "id": f"cad_wall_{len(walls) + 1}",
                    "start": {"x": round(first[0] - origin_x, 5),
                              "z": round(first[1] - origin_y, 5)},
                    "end": {"x": round(second[0] - origin_x, 5),
                            "z": round(second[1] - origin_y, 5)},
                    "kind": "interior", "thickness_m": .12, "height_m": 2.8,
                    "source": "cad_review_evidence", "confidence": 0.0,
                    "boundary_kind": "unresolved_review_evidence",
                    "review_status": "needs_review",
                    "cad_provenance": {
                        **copy.deepcopy(row["cad_provenance"]),
                        "segment_index": segment_index,
                        "source_segment_m": [
                            [round(first[0], 8), round(first[1], 8)],
                            [round(second[0], 8), round(second[1], 8)],
                        ],
                    },
                })
    physical_spaces, semantic_zones, semantic_errors = initial_space_layers(accepted_faces)
    # Room names and open-plan use boundaries are semantic overlays.  They
    # must never turn a source-backed, closed physical shell into a geometry
    # failure.  A face that contains mutually incompatible enclosed-room
    # labels, however, proves a missing physical wall and remains a geometry
    # hard error.
    geometry_semantic_codes = {
        "cad_physical_boundary_missing_for_enclosed_room_labels",
    }
    hard_errors.extend(
        copy.deepcopy(row) for row in semantic_errors
        if str(row.get("code") or "") in geometry_semantic_codes
    )
    accepted_by_id = {entry["face_id"]: entry for entry in accepted_faces}
    for physical in physical_spaces:
        entry = accepted_by_id[physical["face_ids"][0]]
        polygon_shape = entry["shape"]
        polygon_boundary = polygon_shape.boundary
        boundary_sources = []
        for source_row in selected_rows:
            if polygon_boundary.distance(LineString(source_row["points"])) <= max(chord_error_m * 2, .01):
                provenance = source_row["cad_provenance"]
                boundary_sources.append({
                    "root_handle": provenance.get("root_handle") or "",
                    "source_handle": provenance.get("source_handle") or "",
                    "layer": provenance.get("effective_layer") or provenance.get("layer") or "",
                    "raw_layer": provenance.get("raw_layer") or "",
                    "effective_layer": provenance.get("effective_layer") or provenance.get("layer") or "",
                    "block": provenance.get("block") or "",
                    "transform": copy.deepcopy(provenance.get("transform") or []),
                })
        provenance_row = min(
            selected_rows,
            key=lambda row: polygon_boundary.distance(LineString(row["points"])),
        )
        physical.update({
            "floor_elevation_m": 0.0, "ceiling_height_m": 2.8,
            "source": "cad", "confidence": 1.0,
            "cad_provenance": {
                **copy.deepcopy(provenance_row["cad_provenance"]),
                "source_polygon_m": copy.deepcopy(entry["cad_polygon_m"]),
                "source_face_ids": copy.deepcopy(physical["face_ids"]),
                "boundary_sources": boundary_sources,
            },
        })
    physical_by_id = {row["id"]: row for row in physical_spaces}
    rooms = [{
        "id": zone["id"], "label": zone["label"], "room_type": zone["zone_type"],
        "semantic_profile": zone["zone_type"],
        "semantic_status": str(zone.get("semantic_status") or "complete"),
        **({"reference_room_profile": zone["reference_room_profile"]}
           if zone.get("reference_room_profile") else {}),
        "physical_space_id": zone["physical_space_id"],
        "polygon": copy.deepcopy((zone.get("geometry") or {}).get("points") or []),
        "floor_elevation_m": 0.0, "ceiling_height_m": 2.8,
        "selected": True, "source": "cad", "confidence": 1.0,
        "cad_provenance": {
            "source_kind": "semantic_zone_on_cad_physical_space",
            "source_handle": (physical_by_id[zone["physical_space_id"]].get("cad_provenance") or {}).get("source_handle") or "",
            "root_handle": (physical_by_id[zone["physical_space_id"]].get("cad_provenance") or {}).get("root_handle") or "",
            "source_face_ids": copy.deepcopy(physical_by_id[zone["physical_space_id"]]["face_ids"]),
        },
    } for zone in semantic_zones]
    room_contracts = []
    root_geometry_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in geometry:
        provenance = row.get("cad_provenance") or {}
        root_handle = str(provenance.get("root_handle") or "")
        if root_handle and not row.get("wall_candidate"):
            root_geometry_points[root_handle].extend(row.get("points") or [])
    fixed_objects, openings = [], []
    seen_fixed_roots: set[tuple[str, str]] = set()
    seen_fixed_geometry: dict[tuple[Any, ...], dict] = {}
    seen_opening_roots: set[tuple[str, str]] = set()
    for insert in inserts:
        provenance = insert.get("cad_provenance") or {}
        root_handle = str(provenance.get("root_handle") or provenance.get("source_handle") or "")
        root_points = root_geometry_points.get(root_handle) or []
        world_bbox = _bbox(root_points) if root_points else None
        center_cad = (
            ((world_bbox[0] + world_bbox[2]) / 2, (world_bbox[1] + world_bbox[3]) / 2)
            if world_bbox else insert["point"]
        )
        x, y = center_cad[0] - origin_x, center_cad[1] - origin_y
        if not (min_x - .5 <= insert["point"][0] <= max_x + .5 and min_y - .5 <= insert["point"][1] <= max_y + .5):
            continue
        semantic_role = str(insert.get("semantic_role") or _role_from_symbol_footprint(
            str(insert.get("layer") or ""), root_points,
            root_geometry_features.get(root_handle)))
        fixed_key = (root_handle, semantic_role)
        if semantic_role and fixed_key not in seen_fixed_roots:
            seen_fixed_roots.add(fixed_key)
            rotation_deg = float(insert.get("rotation_deg") or 0.0)
            radians = math.radians(-rotation_deg)
            cos_r, sin_r = math.cos(radians), math.sin(radians)
            local_points = [
                (
                    (point[0] - insert["point"][0]) * cos_r - (point[1] - insert["point"][1]) * sin_r,
                    (point[0] - insert["point"][0]) * sin_r + (point[1] - insert["point"][1]) * cos_r,
                )
                for point in root_points
            ]
            local_bbox = _bbox(local_points) if local_points else None
            extent_x = float(local_bbox[2] - local_bbox[0]) if local_bbox else 0.0
            extent_z = float(local_bbox[3] - local_bbox[1]) if local_bbox else 0.0
            extent_known = extent_x >= .02 and extent_z >= .02
            # Boundary points deliberately match both adjacent polygons and are
            # blocked: a CAD anchor must belong to exactly one room.
            room_matches = [
                room["id"] for room in rooms
                if Polygon([
                    (float(point["x"]) + origin_x, float(point["z"]) + origin_y)
                    for point in room.get("polygon") or []
                ]).covers(Point(center_cad[0], center_cad[1]))
            ]
            anchor_blockers = []
            if not extent_known:
                anchor_blockers.append("cad_fixed_object_extent_unknown")
            if len(room_matches) != 1:
                anchor_blockers.append("cad_fixed_object_room_not_unique")
            geometry_bounds_signature = (
                tuple(round(float(value), 4) for value in world_bbox)
                if world_bbox else (
                    round(float(center_cad[0]), 4), round(float(center_cad[1]), 4),
                    round(float(extent_x), 4), round(float(extent_z), 4),
                )
            )
            geometry_signature = (
                semantic_role, *geometry_bounds_signature,
                round(rotation_deg % 360.0, 4),
            )
            duplicate = seen_fixed_geometry.get(geometry_signature)
            if duplicate is not None:
                duplicate_provenance = duplicate.setdefault("cad_provenance", {})
                duplicate_evidence = duplicate_provenance.setdefault(
                    "duplicate_geometry_evidence", [])
                duplicate_evidence.append({
                    "root_handle": provenance.get("root_handle") or root_handle,
                    "source_handle": provenance.get("source_handle") or root_handle,
                    "handle": provenance.get("handle") or root_handle,
                    "block": provenance.get("block") or insert.get("name") or "",
                    "insert_chain": copy.deepcopy(provenance.get("insert_chain") or []),
                    "reason": "same_role_transform_and_world_bbox",
                })
                duplicate["duplicate_source_count"] = 1 + len(duplicate_evidence)
            else:
                fixed_objects.append({
                "id": f"cad_object_{len(fixed_objects) + 1}", "name": insert["name"],
                "kind": semantic_role, "semantic_role": semantic_role,
                "position": {"x": round(x, 5), "y": 0, "z": round(y, 5)},
                "insert_position": {"x": round(insert["point"][0] - origin_x, 5), "y": 0,
                                    "z": round(insert["point"][1] - origin_y, 5)},
                "size": {"x": round(extent_x, 5) if extent_known else 0.0,
                         "y": float(_REFERENCE_PROXY_HEIGHTS_M.get(semantic_role, .8)),
                         "z": round(extent_z, 5) if extent_known else 0.0},
                "room_id": room_matches[0] if len(room_matches) == 1 else "",
                "room_match_ids": room_matches,
                "rotation_y_deg": round(rotation_deg, 8),
                "insert_scale": {"x": float(insert.get("xscale") or 1.0),
                                 "y": float(insert.get("yscale") or 1.0)},
                "size_source": "cad_expanded_virtual_entities_bbox_2d" if extent_known else "unknown",
                "height_source": "render_proxy_role_default_not_cad_fact",
                "cad_world_bbox_m": ([round(value, 8) for value in world_bbox] if world_bbox else None),
                "cad_local_bbox_m": ([round(value, 8) for value in local_bbox] if local_bbox else None),
                "rotation_source": "cad_insert_dxf",
                "reference_anchor_ready": not anchor_blockers,
                "reference_anchor_blockers": anchor_blockers,
                "source": "cad", "confidence": 1.0, "observed": True,
                "purpose": "observed_architecture", "review_status": "accepted",
                "cad_provenance": copy.deepcopy(provenance),
                })
                seen_fixed_geometry[geometry_signature] = fixed_objects[-1]
        opening_key = (root_handle, str(insert.get("opening_kind") or ""))
        if insert["opening_kind"] and walls and opening_key not in seen_opening_roots:
            seen_opening_roots.add(opening_key)
            center = (x, y)

            def assembly_axis(assembly: dict) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
                centerline = assembly.get("centerline") or assembly.get("opening_axis") or []
                if not isinstance(centerline, list) or len(centerline) < 2:
                    return None
                try:
                    first, second = centerline[0], centerline[-1]
                    start = ((float(first.get("x")), float(first.get("z")))
                             if isinstance(first, dict) else (float(first[0]), float(first[1])))
                    end = ((float(second.get("x")), float(second.get("z")))
                           if isinstance(second, dict) else (float(second[0]), float(second[1])))
                    return (start, end) if math.dist(start, end) >= .1 else None
                except (TypeError, ValueError, IndexError):
                    return None

            assembly_candidates = [
                (assembly, axis) for assembly in wall_assemblies
                if assembly.get("review_status") in {"accepted", "confirmed"}
                and (axis := assembly_axis(assembly)) is not None
            ]
            nearest_assembly: Optional[dict] = None
            nearest_axis: Optional[tuple[tuple[float, float], tuple[float, float]]] = None
            if assembly_candidates:
                nearest_assembly, nearest_axis = min(
                    assembly_candidates,
                    key=lambda row: _point_segment_distance(center, row[1][0], row[1][1]),
                )
                assembly_distance = _point_segment_distance(center, nearest_axis[0], nearest_axis[1])
                allowed_distance = max(.25, float(nearest_assembly.get("thickness_m") or 0) / 2 + .10)
                if assembly_distance > allowed_distance:
                    nearest_assembly, nearest_axis = None, None

            def opening_wall_distance(wall: dict) -> float:
                first = (wall["start"]["x"], wall["start"]["z"])
                second = (wall["end"]["x"], wall["end"]["z"])
                samples = [(point[0] - origin_x, point[1] - origin_y) for point in root_points]
                return min((_point_segment_distance(point, first, second) for point in samples),
                           default=_point_segment_distance(center, first, second))

            assembly_id = str((nearest_assembly or {}).get("id") or "")
            wall_candidates = [
                wall for wall in walls
                if (not assembly_id or str(wall.get("wall_assembly_id") or "") == assembly_id)
                and math.dist(
                (wall["start"]["x"], wall["start"]["z"]),
                (wall["end"]["x"], wall["end"]["z"])) >= .1
            ] or walls
            nearest = min(wall_candidates, key=opening_wall_distance)
            distance = (_point_segment_distance(center, nearest_axis[0], nearest_axis[1])
                        if nearest_axis else opening_wall_distance(nearest))
            if distance <= 0.25:
                first = nearest_axis[0] if nearest_axis else (
                    nearest["start"]["x"], nearest["start"]["z"])
                second = nearest_axis[1] if nearest_axis else (
                    nearest["end"]["x"], nearest["end"]["z"])
                length = math.dist(first, second)
                model_root_points = [(point[0] - origin_x, point[1] - origin_y) for point in root_points]
                projected = [_segment_projection(point, first, second) * length for point in model_root_points]
                width = max(projected) - min(projected) if len(projected) >= 2 else 0.0
                width_known = .2 <= width <= min(5.0, length)
                width_source = "cad_expanded_virtual_entities_projection"
                if not width_known:
                    bbox_width = max(
                        float(world_bbox[2] - world_bbox[0]) if world_bbox else 0.0,
                        float(world_bbox[3] - world_bbox[1]) if world_bbox else 0.0,
                    )
                    width_known = .4 <= bbox_width <= min(3.0, length)
                    width = bbox_width if width_known else 0.0
                    width_source = "cad_expanded_virtual_entities_bbox" if width_known else "unknown"
                offset = max(0.0, min(
                    length - width,
                    _segment_projection(center, first, second) * length - width / 2,
                ))
                openings.append({
                    "id": f"cad_opening_{len(openings) + 1}", "wall_id": nearest["id"],
                    **({"wall_assembly_id": assembly_id or nearest["wall_assembly_id"]}
                       if assembly_id or nearest.get("wall_assembly_id") else {}),
                    "kind": insert["opening_kind"], "offset_m": round(offset, 5), "width_m": round(width, 5),
                    "height_m": 2.1 if insert["opening_kind"] == "door" else 1.2,
                    "sill_height_m": 0 if insert["opening_kind"] == "door" else .9,
                    "width_source": width_source,
                    "height_source": "render_proxy_residential_default_not_cad_fact",
                    "sill_height_source": "render_proxy_residential_default_not_cad_fact",
                    "reference_anchor_ready": width_known,
                    "reference_anchor_blockers": [] if width_known else ["cad_opening_extent_unknown"],
                    "rotation_y_deg": round(float(insert.get("rotation_deg") or 0.0), 8),
                    "insert_scale": {"x": float(insert.get("xscale") or 1.0),
                                     "y": float(insert.get("yscale") or 1.0)},
                    "source": "cad", "confidence": 1.0, "review_status": "accepted",
                    "cad_provenance": copy.deepcopy(provenance),
                })
    _annotate_opening_space_sides(
        raw_opening_candidates, rooms,
        origin_x=origin_x, origin_y=origin_y,
    )
    invalidated_global_hosts = {
        str(candidate.get("wall_assembly_id") or "")
        for candidate in raw_opening_candidates
        if candidate.get("synthetic_host_disposition") == "remove"
    }
    if invalidated_global_hosts:
        walls[:] = [
            wall for wall in walls
            if str(wall.get("wall_assembly_id") or "") not in invalidated_global_hosts
        ]
        wall_assemblies[:] = [
            assembly for assembly in wall_assemblies
            if str(assembly.get("id") or "") not in invalidated_global_hosts
        ]
    for candidate in raw_opening_candidates:
        if candidate.get("status") != "accepted":
            continue
        assembly_id = str(candidate.get("wall_assembly_id") or "")
        try:
            center_cad = candidate.get("center_cad_m") or []
            center = (float(center_cad[0]) - origin_x, float(center_cad[1]) - origin_y)
        except (TypeError, ValueError, IndexError):
            candidate["status"] = "rejected"
            candidate.setdefault("reason_codes", []).append("opening_center_invalid")
            continue
        compatible_walls = [wall for wall in walls
                            if str(wall.get("wall_assembly_id") or "") == assembly_id]
        if not compatible_walls:
            candidate["status"] = "review"
            candidate.setdefault("reason_codes", []).append("opening_compatibility_wall_unresolved")
            continue
        nearest = min(
            compatible_walls,
            key=lambda wall: _point_segment_distance(
                center,
                (float((wall.get("start") or {}).get("x") or 0),
                 float((wall.get("start") or {}).get("z") or 0)),
                (float((wall.get("end") or {}).get("x") or 0),
                 float((wall.get("end") or {}).get("z") or 0)),
            ),
        )
        first = (float(nearest["start"]["x"]), float(nearest["start"]["z"]))
        second = (float(nearest["end"]["x"]), float(nearest["end"]["z"]))
        length = math.dist(first, second)
        width = min(float(candidate.get("width_m") or 0), length)
        if width < .20:
            candidate["status"] = "rejected"
            candidate.setdefault("reason_codes", []).append("opening_width_invalid")
            continue
        offset = max(0.0, min(
            length - width,
            _segment_projection(center, first, second) * length - width / 2,
        ))
        duplicate = False
        for opening in openings:
            if str(opening.get("wall_assembly_id") or "") != assembly_id:
                continue
            existing_start = float(opening.get("offset_m") or 0)
            existing_end = existing_start + float(opening.get("width_m") or 0)
            if min(offset + width, existing_end) - max(offset, existing_start) > min(
                    width, existing_end - existing_start) * .60:
                duplicate = True
                break
        if duplicate:
            candidate["status"] = "rejected"
            candidate.setdefault("reason_codes", []).append("duplicate_insert_opening")
            continue
        kind = str(candidate.get("kind") or "window")
        source_handles = [str(value) for value in candidate.get("source_handles") or []]
        source_handle = source_handles[0] if source_handles else ""
        root_handle = str(candidate.get("source_root_handle") or source_handle)
        openings.append({
            "id": f"cad_raw_opening_{len(openings) + 1}",
            "wall_id": nearest["id"], "wall_assembly_id": assembly_id,
            "kind": kind, "offset_m": round(offset, 5), "width_m": round(width, 5),
            "height_m": 2.1 if kind in {"door", "open_connection"} else 1.2,
            "sill_height_m": 0 if kind in {"door", "open_connection"} else .9,
            "width_source": "cad_raw_geometry_axis_projection",
            "height_source": "render_proxy_residential_default_not_cad_fact",
            "sill_height_source": "render_proxy_residential_default_not_cad_fact",
            "reference_anchor_ready": True, "reference_anchor_blockers": [],
            "source": "cad_raw_geometry", "confidence": float(candidate.get("confidence") or 0),
            "review_status": "accepted",
            "cad_provenance": {
                "source_kind": "raw_geometry_opening_v1",
                "candidate_id": candidate.get("candidate_id"),
                # validate_cad_model and downstream consumers use the standard
                # single-handle/root-handle keys; retain the complete list too.
                "source_handle": source_handle,
                "root_handle": root_handle,
                "source_root_handle": root_handle,
                "source_handles": copy.deepcopy(source_handles),
                "source_entity_indexes": copy.deepcopy(candidate.get("source_entity_indexes") or []),
                "wall_source_handles": copy.deepcopy(candidate.get("wall_source_handles") or []),
                "evidence_geometry": copy.deepcopy(candidate.get("evidence_geometry") or {}),
                "axis_segment_cad_m": copy.deepcopy(candidate.get("axis_segment_cad_m") or []),
                "reason_codes": copy.deepcopy(candidate.get("reason_codes") or []),
            },
        })
    raw_opening_summary = summarize_raw_geometry_openings(raw_opening_candidates)
    objects_by_room: dict[str, list[dict]] = defaultdict(list)
    for row in fixed_objects:
        if row.get("room_id"):
            objects_by_room[str(row["room_id"])].append(row)
    for room in rooms:
        if not room.get("reference_room_profile"):
            continue
        observed_roles = {str(row.get("semantic_role") or row.get("kind") or "")
                          for row in objects_by_room.get(str(room["id"]), [])}
        required_groups = _required_role_groups(str(room["reference_room_profile"]), observed_roles)
        missing_groups = [group for group in required_groups if not observed_roles.intersection(group)]
        room_contracts.append({
            "room_id": room["id"], "profile": room["semantic_profile"],
            "reference_room_profile": room["reference_room_profile"],
            "status": "complete" if not missing_groups else "blocked",
            "required_role_groups": required_groups,
            "preferred_roles": sorted(observed_roles),
            "min_visible_groups": len(required_groups),
            "missing_role_groups": missing_groups,
            "source": "cad_local_text_and_observed_block_containment",
        })
    room_shapes = [Polygon([(point["x"], point["z"]) for point in room.get("polygon") or []])
                   for room in rooms]
    physical_shapes = [Polygon([(point["x"], point["z"]) for point in row.get("polygon") or []])
                       for row in physical_spaces]
    room_union = unary_union(room_shapes) if room_shapes else None
    room_area = float(room_union.area) if room_union is not None else 0.0
    overlap_area = sum(float(left.intersection(right).area)
                       for index, left in enumerate(room_shapes) for right in room_shapes[index + 1:])
    physical_union = unary_union(physical_shapes) if physical_shapes else None
    outer_closed = _polygonal_topology_closed(physical_union)
    metrics = {
        "structural_entity_count": len(structural_indexes),
        "excluded_compact_wall_glyphs": excluded_compact_wall_glyphs,
        "selected_structural_entity_count": len(selected_rows),
        "role_decomposition_input_entity_count": int(
            selected_entity_role_summary.get("input_entity_count") or 0),
        "role_decomposition_retained_wall_entity_count": int(
            selected_entity_role_summary.get("retained_wall_entity_count") or 0),
        "role_decomposition_review_entity_count": int(
            selected_entity_role_summary.get("review_entity_count") or 0),
        "wall_assembly_count": len(wall_assemblies),
        "unresolved_wall_assembly_count": len(unresolved_wall_assemblies),
        "production_unresolved_wall_assembly_count": len(unresolved_wall_assemblies),
        "rejected_redundant_wall_evidence_count": len(
            rejected_redundant_wall_evidence),
        "rejected_opening_wall_evidence_count": len(
            rejected_opening_wall_evidence),
        "ignored_nonstructural_count": len(ignored_nonstructural),
        "wall_boundary_p95_m": 0.0 if walls else None,
        "opening_count": len(openings), "opening_endpoint_errors": 0,
        "opening_width_errors": 0,
        "opening_reference_extent_errors": sum(row.get("width_source") == "unknown" for row in openings),
        "room_nonoverlap": overlap_area <= 1e-6,
        "room_overlap_area_m2": round(overlap_area, 8),
        "room_coverage": 1.0 if physical_spaces and overlap_area <= 1e-6 else 0.0,
        "room_coverage_basis": "filtered CAD physical spaces with semantic compatibility projection",
        "outer_wall_closed": outer_closed,
        "cad_derivation_coverage": 1.0 if walls and physical_spaces else 0.0,
        "global_wall_footprint_count": len(global_wall_footprints),
        "global_wall_source_coverage_ratio": global_wall_topology.get(
            "source_coverage_ratio"),
        "global_wall_area_m2": global_wall_topology.get("wall_area_m2"),
        "global_space_candidate_count": global_wall_topology.get(
            "space_candidate_count"),
    }
    reference_anchor_errors = [
        {"code": blocker, "object_id": row.get("id"), "room_match_ids": row.get("room_match_ids") or []}
        for row in fixed_objects for blocker in row.get("reference_anchor_blockers") or []
    ] + [
        {"code": blocker, "opening_id": row.get("id")}
        for row in openings for blocker in row.get("reference_anchor_blockers") or []
    ]
    reference_anchor_errors.extend(
        {"code": "cad_room_required_anchor_missing", "room_id": row.get("room_id"),
         "missing_role_groups": copy.deepcopy(row.get("missing_role_groups") or [])}
        for row in room_contracts if row.get("status") != "complete"
    )
    reference_anchor_report = {
        "status": "ready" if not reference_anchor_errors else "blocked",
        "hard_errors": reference_anchor_errors,
        "source": "cad_insert_virtual_geometry_and_room_containment",
    }
    semantic_hard_errors = [copy.deepcopy(row) for row in semantic_errors]
    cad_to_model_v2, model_to_cad_v2 = cad_plan_transforms_v2(
        min_x=origin_x, max_y=max_y)
    report = {
        "schema_version": 1, "source_path": path, "source_sha256": sha256_file(path),
        "insunits": units, "resolved_insunits": resolved_units,
        "declared_unit_scale_to_m": float(declared_scale),
        "unit_scale_to_m": scale, "unit_resolution": copy.deepcopy(unit_resolution_report),
        "chord_error_m": chord_error_m,
        "inventory": dict(inventory), "layers": dict(layers), "blocks": dict(blocks),
        "normalization": {
            "text_encoding": "gbk_from_latin1_only_when_cjk_gain_v1",
            "dxf_defaults": dxf_normalization,
            "unit_resolution": copy.deepcopy(unit_resolution_report),
        },
        "texts": texts[:300], "dimensions": dimensions[:300],
        "hatch_surface_evidence": copy.deepcopy(hatch_surfaces[:300]),
        "hatch_surface_evidence_count": len(hatch_surfaces),
        "hatch_surface_evidence_truncated": len(hatch_surfaces) > 300,
        "scale_conflicts": copy.deepcopy(scale_conflicts[:100]),
        "geometry_authority_evidence": copy.deepcopy(geometry_authority_evidence),
        "projected_plan_structure_filter": copy.deepcopy(
            projected_plan_structure_filter),
        "projected_detail_topology_invariance": copy.deepcopy(
            projected_detail_topology_invariance),
        "attached_exterior_space_evidence": copy.deepcopy(
            attached_exterior_space_evidence),
        "terminal_open_connection_evidence": copy.deepcopy(
            terminal_open_connection_evidence),
        "semantic_building_envelope_evidence": copy.deepcopy(
            semantic_building_envelope_evidence),
        "hatch_surface_evidence": copy.deepcopy(hatch_surfaces[:300]),
        "semantic_building_envelope_diagnostics": copy.deepcopy(
            semantic_building_envelope_diagnostics),
        "text_anchors": text_anchors[:500],
        "raw_faces": raw_faces[:500],
        "raw_face_count": len(raw_faces),
        "raw_faces_truncated": len(raw_faces) > 500,
        "structural_entity_count": len(structural_indexes),
        "excluded_compact_wall_glyphs": excluded_compact_wall_glyphs,
        "selected_structural_entity_count": len(selected_rows),
        "selected_entity_role_summary": copy.deepcopy(selected_entity_role_summary),
        "selected_entity_role_evidence": copy.deepcopy(selected_entity_role_evidence[:500]),
        "selected_entity_role_evidence_truncated": len(selected_entity_role_evidence) > 500,
        "raw_opening_summary": copy.deepcopy(raw_opening_summary),
        "raw_opening_candidates": copy.deepcopy(raw_opening_candidates[:300]),
        "raw_opening_candidates_truncated": len(raw_opening_candidates) > 300,
        "ignored_nonstructural_count": len(ignored_nonstructural),
        "ignored_nonstructural_entities": ignored_nonstructural[:200],
        "global_wall_topology": copy.deepcopy(global_wall_topology),
        "candidate_plans": candidates[:20], "candidate_plan_count": len(candidates),
        "candidate_plans_truncated": len(candidates) > 20,
        "selected_candidate_id": selected["candidate_id"],
        "selection_explanation": (
            f"显式选择 {selected['candidate_id']}；仍保留所有候选和分数证据"
            if preferred_candidate_id else
            "可审计 CAD 单位证据纠正单位后，锁定第一遍已证明的源结构实体集合；仍保留米制子候选和分数证据"
            if unit_locked_candidate is not None else
            "仅在显式 wall/结构图层中建候选；先要求闭合平面，再结合结构长度、家具/门窗块和语义锚点密度排序"
        ),
        "selection_method": (
            "explicit_candidate_id" if preferred_candidate_id
            else (
                "explicit_annotation_unit_source_entity_lock_v1"
                if str(unit_resolution_report.get("method") or "")
                == "cad_explicit_annotation_unit_resolution_v1"
                else "metric_plan_metadata_unit_source_entity_lock_v1"
            )
            if unit_locked_candidate is not None
            else "deterministic_context_score_v3"),
        "semantic_preview_path": semantic_preview_path,
        "semantic_preview_mapping": semantic_preview_mapping,
        "alignment_metrics": metrics, "hard_errors": hard_errors,
        "geometry_readiness": {
            "status": "blocked" if hard_errors else (
                "needs_review" if unresolved_wall_assemblies else "passed"),
            "issues": copy.deepcopy(hard_errors),
            "metrics": copy.deepcopy(metrics),
        },
        "semantic_readiness": {
            "status": "needs_review" if semantic_hard_errors else "complete",
            "issues": copy.deepcopy(semantic_hard_errors),
            "unassigned_space_ids": sorted({
                str(row.get("physical_space_id") or "")
                for row in semantic_hard_errors
                if str(row.get("physical_space_id") or "")
            }),
        },
        "warnings": wall_assembly_warnings,
        "artifact_directory": root, "reference_anchor_report": copy.deepcopy(reference_anchor_report),
        "coordinate_contract_version": CAD_PLAN_TRANSFORM_VERSION,
        "coordinate_system": CAD_MODEL_COORDINATE_SYSTEM_V2,
        "cad_to_model": cad_to_model_v2,
        "model_to_cad": model_to_cad_v2,
    }
    model = {
        "schema_version": 2, "space_model_schema_version": 1,
        "geometry_schema_version": 3, "input_grade": "vector_authoritative",
        "model_id": f"cad_model_{uuid.uuid4().hex[:12]}",
        "coordinate_system": CAD_MODEL_COORDINATE_SYSTEM_V2,
        "coordinate_contract_version": CAD_PLAN_TRANSFORM_VERSION,
        "width_m": max(2.0, max_x - min_x),
        "depth_m": max(2.0, max_y - min_y), "wall_height_m": 2.8, "wall_thickness_m": .12,
        "scale": {
            "status": "cad_authoritative",
            "method": str(unit_resolution_report.get("method") or "$INSUNITS"),
            "unit_code": resolved_units,
            "declared_unit_code": units,
            "metres_per_unit": scale,
            "declared_metres_per_unit": float(declared_scale),
            "unit_resolution": copy.deepcopy(unit_resolution_report),
        },
        "walls": walls, "wall_assemblies": wall_assemblies, "openings": openings,
        "global_wall_footprints": copy.deepcopy(global_wall_footprints),
        "global_wall_topology": copy.deepcopy(global_wall_topology),
        "attached_exterior_space_evidence": copy.deepcopy(
            attached_exterior_space_evidence),
        "terminal_open_connection_evidence": copy.deepcopy(
            terminal_open_connection_evidence),
        "semantic_building_envelope_evidence": copy.deepcopy(
            semantic_building_envelope_evidence),
        "physical_spaces": physical_spaces, "semantic_zones": semantic_zones,
        "excluded_face_ids": [row["face_id"] for row in raw_faces if row.get("disposition") == "excluded"],
        # rooms is an explicit downstream compatibility projection.  It is
        # regenerated whenever semantic_zones change and is never CAD authority.
        "rooms": rooms, "rooms_projection_source": "semantic_zones_v1",
        "fixed_objects": fixed_objects,
        "cameras": [], "uncertainties": [], "cad_facts_hash": "",
        "room_contracts": room_contracts,
        "reference_anchor_report": copy.deepcopy(reference_anchor_report),
        "semantic_report": {
            "status": "needs_review" if semantic_hard_errors else "complete",
            "hard_errors": semantic_hard_errors,
            "warnings": [], "source": "cad_local_text_containment",
        },
        "geometry_readiness": copy.deepcopy(report["geometry_readiness"]),
        "semantic_readiness": copy.deepcopy(report["semantic_readiness"]),
        "geometry_report": {"hard_errors": [], "warnings": [], "source": "cad_local_gate"},
        "cad_to_model": copy.deepcopy(report["cad_to_model"]),
        "model_to_cad": copy.deepcopy(report["model_to_cad"]),
    }
    model = reorient_cad_model_to_v2(model, depth_m=max_y - min_y)
    model["cad_facts_hash"] = cad_facts_hash(model)
    model["physical_facts_hash"] = physical_facts_hash(model)
    model["semantic_overlay_hash"] = semantic_overlay_hash(model)
    model["space_confirmation"] = {
        "status": "needs_review" if not openings or hard_errors else "auto_draft",
        "reason_codes": (["cad_opening_topology_unproven"] if not openings else [])
        + sorted({str(row.get("code") or "") for row in hard_errors if row.get("code")}),
        "physical_space_count": len(physical_spaces),
        "semantic_zone_count": len(semantic_zones),
        "validation_version": "cad-space-draft-v1",
    }
    validation = validate_cad_model(model, report)
    model["geometry_report"] = copy.deepcopy(validation)
    report["hard_errors"].extend(validation["hard_errors"])
    report["warnings"].extend(validation["warnings"])
    report["validation"] = validation
    report_path = os.path.join(root, "parse_report.json")
    with open(report_path, "x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    report["report_path"] = report_path
    if report["hard_errors"]:
        raise CadError("cad_hard_review_required", "CAD 本地解析未通过硬门禁，未调用 Gemini",
                       details={"parse_report": report, "model": model})
    return model, report


def _segment_projection(point: tuple[float, float], first: tuple[float, float],
                        second: tuple[float, float]) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    denom = dx * dx + dy * dy
    return max(0.0, min(1.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denom)) if denom else 0.0


def cad_hybrid_model_from_ai(candidate_model: dict, parse_report: dict, ai_model: dict) -> tuple[dict, dict]:
    """Apply AI room semantics to an audited CAD raster without replacing CAD walls.

    The AI coordinates are image-normalized through ``ai_model``.  They are
    deterministically inverted through the raster map saved by the CAD parser.
    Wall geometry remains the original handle-backed CAD collection; AI-only
    fixed items stay layout proxies and therefore never enter ``cad_facts_hash``.
    """
    try:
        from shapely.geometry import LineString, Point, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception as ex:
        raise CadDependencyError("shapely_missing", "缺少 Shapely，不能核对 AI 房间与 CAD 边界", status_code=503) from ex
    mapping = parse_report.get("semantic_preview_mapping") or {}
    width_px = float(mapping.get("image_width") or 0)
    height_px = float(mapping.get("image_height") or 0)
    padding = float(mapping.get("padding") or 0)
    pixels_per_metre = float(mapping.get("pixels_per_metre") or 0)
    cad_bbox = mapping.get("cad_bbox_m") or []
    ai_width = float(ai_model.get("width_m") or 0)
    ai_depth = float(ai_model.get("depth_m") or 0)
    if (width_px <= 0 or height_px <= 0 or pixels_per_metre <= 0
            or len(cad_bbox) != 4 or ai_width <= 0 or ai_depth <= 0):
        raise CadError("cad_ai_mapping_missing", "CAD 语义预览缺少可逆坐标映射；未修改模型")
    origin_x, origin_z = float(cad_bbox[0]), float(cad_bbox[1])
    model_width = float(cad_bbox[2]) - origin_x
    model_depth = float(cad_bbox[3]) - origin_z
    v2_coordinates = int(candidate_model.get("coordinate_contract_version") or 0) >= 2
    inverse_transform = (candidate_model.get("model_to_cad")
                         if isinstance(candidate_model.get("model_to_cad"), Mapping)
                         else {"x": origin_x, "z": origin_z})

    def image_to_model(point: dict) -> dict:
        image_x = float(point.get("x") or 0) / ai_width * width_px
        image_y = float(point.get("z") or 0) / ai_depth * height_px
        model_z = ((image_y - padding) / pixels_per_metre if v2_coordinates
                   else (height_px - padding - image_y) / pixels_per_metre)
        return {
            "x": round(max(0.0, min(model_width, (image_x - padding) / pixels_per_metre)), 5),
            "z": round(max(0.0, min(model_depth, model_z)), 5),
        }

    def source_point(point: Mapping[str, Any]) -> list[float]:
        cad_x, cad_y = model_plan_to_cad(
            (float(point["x"]), float(point["z"])), inverse_transform)
        return [round(cad_x, 8), round(cad_y, 8)]

    walls = copy.deepcopy(candidate_model.get("walls") or [])
    wall_segments = [(
        row,
        (float((row.get("start") or {}).get("x") or 0), float((row.get("start") or {}).get("z") or 0)),
        (float((row.get("end") or {}).get("x") or 0), float((row.get("end") or {}).get("z") or 0)),
    ) for row in walls]
    wall_union = unary_union([LineString([first, second]) for _, first, second in wall_segments])

    def cad_wall_path(first: dict, second: dict) -> list[dict]:
        """Find a conservative wall-graph path for an AI diagonal/gap edge."""
        import heapq

        quantum = .02
        coordinates: dict[tuple[int, int], tuple[float, float]] = {}
        adjacency: dict[tuple[int, int], list[tuple[float, tuple[int, int]]]] = defaultdict(list)

        def key(point: tuple[float, float]) -> tuple[int, int]:
            return round(point[0] / quantum), round(point[1] / quantum)

        for _, start, end in wall_segments:
            start_key, end_key = key(start), key(end)
            coordinates.setdefault(start_key, start)
            coordinates.setdefault(end_key, end)
            distance = math.dist(start, end)
            adjacency[start_key].append((distance, end_key))
            adjacency[end_key].append((distance, start_key))
        if not coordinates:
            return []
        requested_start = (float(first["x"]), float(first["z"]))
        requested_end = (float(second["x"]), float(second["z"]))
        start_key = min(coordinates, key=lambda value: math.dist(coordinates[value], requested_start))
        end_key = min(coordinates, key=lambda value: math.dist(coordinates[value], requested_end))
        if (math.dist(coordinates[start_key], requested_start) > .38
                or math.dist(coordinates[end_key], requested_end) > .38):
            return []
        queue = [(0.0, start_key)]
        distances = {start_key: 0.0}
        previous: dict[tuple[int, int], tuple[int, int]] = {}
        while queue:
            distance, current = heapq.heappop(queue)
            if current == end_key:
                break
            if distance > distances.get(current, float("inf")):
                continue
            for edge_length, neighbor in adjacency.get(current) or []:
                candidate = distance + edge_length
                if candidate < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    previous[neighbor] = current
                    heapq.heappush(queue, (candidate, neighbor))
        if end_key not in distances:
            return []
        keys = [end_key]
        while keys[-1] != start_key:
            keys.append(previous[keys[-1]])
        keys.reverse()
        path = [coordinates[value] for value in keys]
        direct = LineString([requested_start, requested_end])
        routed = LineString(path)
        if routed.length > direct.length * 2.0 + .5 or routed.hausdorff_distance(direct) > 1.5:
            return []
        return [{"x": round(point[0], 5), "z": round(point[1], 5)} for point in path]

    def room_profiles(label: str, room_type: str) -> tuple[str, str]:
        text = f"{label} {room_type}".lower()
        if "master" in text and ("bed" in text or "卧" in text):
            return "bedroom_master", "bedroom"
        if "bed" in text or "卧" in text:
            return "bedroom_secondary", "bedroom"
        if "kitchen" in text or "厨房" in text:
            return "kitchen", "kitchen"
        if "living" in text or "dining" in text or "客厅" in text:
            return "living_room", "living_room"
        if "bath" in text or "toilet" in text or "卫生" in text:
            return "bathroom_master", "bathroom"
        if "foyer" in text or "entry" in text or "hall" in text or "玄关" in text:
            return "foyer", "foyer"
        return str(room_type or "other"), str(room_type or "other")

    rooms: list[dict] = []
    boundary_distances: list[float] = []
    for index, source_room in enumerate(ai_model.get("rooms") or [], 1):
        polygon = [image_to_model(point) for point in source_room.get("polygon") or []]
        deduped: list[dict] = []
        for point in polygon:
            if not deduped or math.dist(
                    (point["x"], point["z"]), (deduped[-1]["x"], deduped[-1]["z"])) > .02:
                deduped.append(point)
        if len(deduped) > 2 and math.dist(
                (deduped[0]["x"], deduped[0]["z"]),
                (deduped[-1]["x"], deduped[-1]["z"])) <= .02:
            deduped.pop()
        shape = Polygon([(point["x"], point["z"]) for point in deduped]) if len(deduped) >= 3 else None
        semantic_polygon_repair = None
        if shape is not None and not shape.is_valid:
            repaired = shape.buffer(0)
            component_count = len(repaired.geoms) if repaired.geom_type == "MultiPolygon" else 1
            if repaired.geom_type == "MultiPolygon":
                repaired = max(repaired.geoms, key=lambda value: value.area)
            if repaired.geom_type == "Polygon" and repaired.area >= .5:
                semantic_polygon_repair = {
                    "method": "shapely_buffer_zero_largest_component_v1",
                    "original_area_m2": round(float(shape.area), 5),
                    "repaired_area_m2": round(float(repaired.area), 5),
                    "component_count": component_count,
                }
                shape = repaired
                deduped = [
                    {"x": round(float(x), 5), "z": round(float(z), 5)}
                    for x, z in list(shape.exterior.coords)[:-1]
                ]
        if shape is None or not shape.is_valid or shape.area < .5:
            raise CadError("cad_ai_room_polygon_invalid", "AI 返回了无效 CAD 房间面；原 CAD 模型未修改",
                           details={"room_id": source_room.get("id"), "area_m2": float(shape.area) if shape else 0})
        label = str(source_room.get("label") or f"CAD AI 房间 {index}")[:100]
        reference_profile, semantic_profile = room_profiles(label, str(source_room.get("room_type") or ""))
        if semantic_profile in {"bedroom", "bathroom", "kitchen"}:
            repaired_polygon: list[dict] = []
            for point_index, start in enumerate(deduped):
                end = deduped[(point_index + 1) % len(deduped)]
                repaired_polygon.append(start)
                edge = LineString([(start["x"], start["z"]), (end["x"], end["z"])])
                if edge.difference(wall_union.buffer(.18, cap_style=2)).length < .3:
                    continue
                path = cad_wall_path(start, end)
                repaired_polygon.extend(path[1:-1] if len(path) > 2 else [])
            candidate_shape = Polygon([(point["x"], point["z"]) for point in repaired_polygon])
            if candidate_shape.is_valid and candidate_shape.area >= .5:
                deduped = repaired_polygon
                shape = candidate_shape
        boundary = shape.boundary
        boundary_sources = []
        for wall, first, second in wall_segments:
            distance = float(boundary.distance(LineString([first, second])))
            if distance <= .22:
                boundary_sources.append(copy.deepcopy(wall.get("cad_provenance") or {}))
        for point in deduped:
            boundary_distances.append(min(
                (_point_segment_distance((point["x"], point["z"]), first, second)
                 for _, first, second in wall_segments), default=999.0))
        base_provenance = copy.deepcopy((boundary_sources or [
            (walls[0].get("cad_provenance") or {}) if walls else {}
        ])[0])
        base_provenance.update({
            "source_polygon_m": [source_point(point) for point in deduped],
            "boundary_sources": boundary_sources,
            "derivation": "gemini_room_polygon_on_audited_cad_raster_v1",
            "semantic_preview_sha256": sha256_file(str(parse_report.get("semantic_preview_path") or "")),
        })
        if semantic_polygon_repair:
            base_provenance["semantic_polygon_repair"] = semantic_polygon_repair
        rooms.append({
            "id": str(source_room.get("id") or f"cad_ai_room_{index}"), "label": label,
            "room_type": semantic_profile, "semantic_profile": semantic_profile,
            "semantic_status": "complete", "reference_room_profile": reference_profile,
            "polygon": deduped, "floor_elevation_m": 0.0, "ceiling_height_m": 2.8,
            "selected": True, "source": "ai_edited",
            "confidence": float(source_room.get("confidence") or .8),
            "cad_provenance": base_provenance,
        })
    if not rooms:
        raise CadError("cad_ai_rooms_empty", "AI 没有返回可用房间；原 CAD 模型未修改")
    room_shapes = {room["id"]: Polygon([(point["x"], point["z"]) for point in room["polygon"]])
                   for room in rooms}
    # Gemini may deliberately overlap an administrative foyer polygon with an
    # open living/dining zone.  Preserve both identities but give the smaller
    # foyer/kitchen zone ownership of the shared floor pixels.  Enclosed-room
    # overlaps remain a hard error.
    for left_index, left in enumerate(rooms):
        for right in rooms[left_index + 1:]:
            left_shape, right_shape = room_shapes[left["id"]], room_shapes[right["id"]]
            if left_shape.intersection(right_shape).area <= .05:
                continue
            profiles = {left.get("semantic_profile"), right.get("semantic_profile")}
            if "living_room" not in profiles or not profiles.issubset({"living_room", "foyer", "kitchen"}):
                continue
            living = left if left.get("semantic_profile") == "living_room" else right
            other = right if living is left else left
            clipped = room_shapes[living["id"]].difference(room_shapes[other["id"]])
            if clipped.geom_type == "MultiPolygon":
                clipped = max(clipped.geoms, key=lambda value: value.area)
            if clipped.geom_type != "Polygon" or clipped.area < .5:
                continue
            points = [{"x": round(float(x), 5), "z": round(float(z), 5)}
                      for x, z in list(clipped.exterior.coords)[:-1]]
            living["polygon"] = points
            living["cad_provenance"]["source_polygon_m"] = [
                source_point(point) for point in points]
            living["cad_provenance"]["open_plan_overlap_repair"] = {
                "method": "subtract_named_foyer_or_kitchen_from_living_v1",
                "other_room_id": other["id"],
            }
            room_shapes[living["id"]] = clipped
    overlap_area_before = sum(float(left.intersection(right).area)
                              for index, left in enumerate(room_shapes.values())
                              for right in list(room_shapes.values())[index + 1:])
    # Room polygons are semantic zones inferred from a raster, not new CAD
    # geometry.  Coarse Gemini masks commonly overlap by one wall thickness or
    # include a hall inside a larger living polygon.  Allocate every pixel once
    # with a deterministic priority (small service rooms first, living last),
    # while all physical boundaries continue to come from the CAD wall graph.
    semantic_priority = {
        "bathroom": 5, "kitchen": 4, "bedroom": 3, "foyer": 2,
        "hallway": 2, "living_room": 1, "other": 0,
    }
    claimed = None
    overlap_repairs: list[dict] = []
    ordered_rooms = sorted(
        rooms,
        key=lambda row: (
            -semantic_priority.get(str(row.get("semantic_profile") or "other"), 0),
            float(room_shapes[row["id"]].area),
            str(row["id"]),
        ),
    )
    for room in ordered_rooms:
        original = room_shapes[room["id"]]
        clipped = original if claimed is None else original.difference(claimed)
        removed_area = max(0.0, float(original.area - clipped.area))
        if clipped.geom_type == "MultiPolygon":
            clipped = max(clipped.geoms, key=lambda value: value.area)
        if clipped.geom_type != "Polygon" or clipped.area < .5:
            raise CadError(
                "cad_ai_room_overlap_unresolvable",
                "AI 房间语义重叠后没有剩余可用区域；原 CAD 模型未修改",
                details={"room_id": room["id"], "removed_area_m2": round(removed_area, 5)},
            )
        if removed_area > .01:
            points = [{"x": round(float(x), 5), "z": round(float(z), 5)}
                      for x, z in list(clipped.exterior.coords)[:-1]]
            room["polygon"] = points
            room["cad_provenance"]["source_polygon_m"] = [
                source_point(point) for point in points]
            room["cad_provenance"]["semantic_overlap_repair"] = {
                "method": "priority_disjoint_semantic_partition_v1",
                "removed_area_m2": round(removed_area, 5),
            }
            room_shapes[room["id"]] = clipped
            overlap_repairs.append({
                "room_id": room["id"], "removed_area_m2": round(removed_area, 5),
            })
        claimed = clipped if claimed is None else unary_union([claimed, clipped])
    overlap_area = sum(float(left.intersection(right).area)
                       for index, left in enumerate(room_shapes.values())
                       for right in list(room_shapes.values())[index + 1:])
    if overlap_area > 1e-6:
        raise CadError("cad_ai_room_overlap", "AI 房间面互相重叠；原 CAD 模型未修改",
                       details={"overlap_area_m2": round(overlap_area, 5)})

    fixed_objects = copy.deepcopy(candidate_model.get("fixed_objects") or [])
    for row in fixed_objects:
        position = row.get("position") or {}
        point = Point(float(position.get("x") or 0), float(position.get("z") or 0))
        matches = [room_id for room_id, shape in room_shapes.items() if shape.buffer(.02).covers(point)]
        row["room_id"] = matches[0] if len(matches) == 1 else ""
        row["room_match_ids"] = matches
        if row.get("semantic_role") == "basin" and matches:
            room = next(item for item in rooms if item["id"] == matches[0])
            if room.get("semantic_profile") == "kitchen":
                row["semantic_role"] = row["kind"] = "sink"
        blockers = []
        if not row.get("room_id"):
            blockers.append("cad_fixed_object_room_not_unique")
        if float((row.get("size") or {}).get("x") or 0) <= 0 or float((row.get("size") or {}).get("z") or 0) <= 0:
            blockers.append("cad_fixed_object_extent_unknown")
        row["reference_anchor_ready"] = not blockers
        row["reference_anchor_blockers"] = blockers

    role_aliases = {"counter": "kitchen_run", "cabinet": "kitchen_run", "vanity": "basin"}
    for index, source in enumerate(ai_model.get("fixed_objects") or [], 1):
        position = image_to_model(source.get("position") or source.get("center") or {})
        source_room_id = str(source.get("room_id") or "")
        room_id = source_room_id if source_room_id in room_shapes else next((
            key for key, shape in room_shapes.items()
            if shape.buffer(.05).covers(Point(position["x"], position["z"]))), "")
        role = _role_from_name(f"{source.get('name') or ''} {source.get('kind') or ''}")
        role = role_aliases.get(role or str(source.get("kind") or "").lower(), role)
        if not role or not room_id:
            continue
        if any(str(row.get("semantic_role") or "") == role
               and str(row.get("room_id") or "") == room_id for row in fixed_objects):
            continue
        fixed_objects.append({
            "id": f"cad_ai_proxy_{index}", "name": str(source.get("name") or role)[:100],
            "kind": role, "semantic_role": role, "room_id": room_id,
            "position": {**position, "y": 0},
            "size": {
                "x": max(.1, float((source.get("size") or {}).get("x") or source.get("width_m") or 1.0)),
                "y": max(.1, float((source.get("size") or {}).get("y") or source.get("height_m") or
                                     _REFERENCE_PROXY_HEIGHTS_M.get(role, .8))),
                "z": max(.1, float((source.get("size") or {}).get("z") or source.get("depth_m") or .6)),
            },
            "rotation_y_deg": float(source.get("rotation_y_deg") or 0),
            "source": "ai", "observed": False, "purpose": "layout_proxy",
            "review_status": "pending", "reference_anchor_ready": True,
            "reference_anchor_blockers": [], "confidence": float(source.get("confidence") or .7),
            "assumption": "Gemini semantic proxy on an audited CAD raster; not an immutable CAD fact",
        })

    def opening_center(opening: dict, rows: list[dict]) -> Optional[tuple[float, float]]:
        wall = next((row for row in rows if str(row.get("id") or "") == str(opening.get("wall_id") or "")), None)
        if not wall:
            return None
        first, second = wall.get("start") or {}, wall.get("end") or {}
        a = (float(first.get("x") or 0), float(first.get("z") or 0))
        b = (float(second.get("x") or 0), float(second.get("z") or 0))
        length = math.dist(a, b)
        if length <= 1e-9:
            return None
        distance = float(opening.get("offset_m") or 0) + float(opening.get("width_m") or 0) / 2
        t = max(0.0, min(1.0, distance / length))
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t

    openings = copy.deepcopy(candidate_model.get("openings") or [])
    ai_walls = ai_model.get("walls") or []
    for index, source in enumerate(ai_model.get("openings") or [], 1):
        ai_center = opening_center(source, ai_walls)
        if ai_center is None:
            continue
        position = image_to_model({"x": ai_center[0], "z": ai_center[1]})
        kind = str(source.get("kind") or "")
        local_center_rows = [(row, opening_center(row, walls)) for row in openings]
        duplicate = next((
            row for row, center in local_center_rows
            if center is not None and str(row.get("kind") or "") == kind
            and math.dist(center, (position["x"], position["z"])) <= .65
        ), None)
        if duplicate is not None:
            if float(duplicate.get("width_m") or 0) <= 0:
                local_wall = next((row for row in walls if row.get("id") == duplicate.get("wall_id")), None)
                wall_length = math.dist(
                    (float((local_wall.get("start") or {}).get("x") or 0),
                     float((local_wall.get("start") or {}).get("z") or 0)),
                    (float((local_wall.get("end") or {}).get("x") or 0),
                     float((local_wall.get("end") or {}).get("z") or 0)),
                ) if local_wall else 0
                ai_width_value = float(source.get("width_m") or 0)
                if .3 <= ai_width_value <= wall_length:
                    duplicate["width_m"] = round(ai_width_value, 5)
                    duplicate["width_source"] = "gemini_visual_width_on_cad_block_center"
                    duplicate["reference_anchor_ready"] = True
                    duplicate["reference_anchor_blockers"] = []
            continue
        point = (position["x"], position["z"])
        wall_candidates = [(row, first, second) for row, first, second in wall_segments
                           if math.dist(first, second) >= .5]
        if not wall_candidates:
            continue
        nearest, first, second = min(
            wall_candidates, key=lambda value: _point_segment_distance(point, value[1], value[2]))
        distance_to_wall = _point_segment_distance(point, first, second)
        if distance_to_wall > .28:
            continue
        wall_length = math.dist(first, second)
        width_value = max(.3, min(float(source.get("width_m") or .9), wall_length))
        center_distance = _segment_projection(point, first, second) * wall_length
        offset = max(0.0, min(wall_length - width_value, center_distance - width_value / 2))
        provenance = copy.deepcopy(nearest.get("cad_provenance") or {})
        provenance.update({
            "semantic_derivation": "gemini_opening_on_audited_cad_raster_v1",
            "ai_opening_id": source.get("id"), "ai_to_cad_wall_distance_m": round(distance_to_wall, 5),
        })
        openings.append({
            "id": f"cad_ai_opening_{index}", "wall_id": nearest.get("id"), "kind": kind,
            "offset_m": round(offset, 5), "width_m": round(width_value, 5),
            "height_m": float(source.get("height_m") or (2.1 if kind == "door" else 1.2)),
            "sill_height_m": float(source.get("sill_height_m") or (0 if kind == "door" else .9)),
            "width_source": "gemini_visual_width_locally_attached_to_cad_wall",
            "height_source": "gemini_visual_semantic_on_cad_raster",
            "sill_height_source": "gemini_visual_semantic_on_cad_raster",
            "reference_anchor_ready": True, "reference_anchor_blockers": [],
            "source": "ai_edited", "confidence": float(source.get("confidence") or .75),
            "review_status": "accepted", "cad_provenance": provenance,
        })

    contracts = []
    for room in rooms:
        roles = {str(row.get("semantic_role") or row.get("kind") or "")
                 for row in fixed_objects if str(row.get("room_id") or "") == room["id"]}
        groups = _required_role_groups(str(room.get("reference_room_profile") or ""), roles)
        missing = [group for group in groups if not roles.intersection(group)]
        contracts.append({
            "room_id": room["id"], "profile": room["semantic_profile"],
            "reference_room_profile": room["reference_room_profile"],
            "status": "complete" if not missing else "blocked", "required_role_groups": groups,
            "preferred_roles": sorted(roles), "min_visible_groups": len(groups),
            "missing_role_groups": missing, "source": "cad_ai_room_semantics_plus_cad_symbols",
        })

    model = copy.deepcopy(candidate_model)
    model.update(
        rooms=rooms, openings=openings, fixed_objects=fixed_objects, room_contracts=contracts,
        semantic_report={
            "status": "complete" if all(row["status"] == "complete" for row in contracts) else "needs_review",
            "hard_errors": [{"code": "semantic_required_role_missing", "room_id": row["room_id"],
                             "missing_role_groups": row["missing_role_groups"]}
                            for row in contracts if row["status"] != "complete"],
            "warnings": [], "source": "gemini_on_audited_cad_raster_plus_local_symbol_rules_v1",
        },
        cad_semantic_derivation={
            "method": "gemini_room_polygon_on_audited_cad_raster_v1",
            "ai_model": ai_model.get("ai_model") or "", "preview_path": parse_report.get("semantic_preview_path") or "",
            "preview_sha256": sha256_file(str(parse_report.get("semantic_preview_path") or "")),
            "room_vertex_to_cad_wall_p95_m": round(sorted(boundary_distances)[
                min(len(boundary_distances) - 1, max(0, math.ceil(len(boundary_distances) * .95) - 1))], 5)
                if boundary_distances else None,
            "semantic_overlap_area_before_m2": round(overlap_area_before, 5),
            "semantic_overlap_repairs": overlap_repairs,
        },
    )
    # Enter the ordinary runtime through the same canonical representation
    # used by every later semantic pass.  Hashing a pre-normalized model would
    # otherwise treat schema clamping (for example an unknown 0m opening width)
    # as a later CAD mutation even though no provider changed the drawing.
    from .whole_home_engine import normalize_model, validate_model
    cad_semantic_derivation = copy.deepcopy(model["cad_semantic_derivation"])
    model = normalize_model(model, source="cad")
    # Gemini supplies opening semantics only when the CAD block itself did not
    # expose a reliable type/extent.  If one of those inferred openings spans a
    # three-room junction, reject that inference instead of rejecting the
    # otherwise valid CAD shell.  Handle-backed CAD openings are never removed.
    opening_by_id = {str(row.get("id") or ""): row for row in model.get("openings") or []}
    rejected_opening_ids = {
        str(issue.get("opening_id") or "")
        for issue in validate_model(model).get("hard_errors") or []
        if str(issue.get("code") or "") == "opening_spans_room_junction"
        and str(issue.get("opening_id") or "") in opening_by_id
        and str((opening_by_id[str(issue.get("opening_id") or "")].get("cad_provenance") or {}).get(
            "semantic_derivation") or "").startswith("gemini_")
    }
    if rejected_opening_ids:
        model["openings"] = [
            row for row in model.get("openings") or []
            if str(row.get("id") or "") not in rejected_opening_ids
        ]
        cad_semantic_derivation["rejected_openings"] = [
            {
                "opening_id": opening_id,
                "reason": "opening_spans_room_junction",
                "action": "discarded_ai_semantic_opening_kept_cad_shell",
            }
            for opening_id in sorted(rejected_opening_ids)
        ]
    model["cad_semantic_derivation"] = cad_semantic_derivation
    refresh_hybrid_reference_anchor_report(model)
    model["cad_facts_hash"] = cad_facts_hash(model)
    updated_report = copy.deepcopy(parse_report)
    updated_report["hard_errors"] = [row for row in updated_report.get("hard_errors") or []
                                     if row.get("code") != "cad_room_semantics_unresolved"]
    metrics = updated_report.setdefault("alignment_metrics", {})
    metrics.update(room_nonoverlap=overlap_area <= .05, room_overlap_area_m2=round(overlap_area, 8),
                   room_coverage=1.0, outer_wall_closed=True, cad_derivation_coverage=1.0,
                   room_semantic_boundary_p95_m=model["cad_semantic_derivation"]["room_vertex_to_cad_wall_p95_m"])
    updated_report["cad_semantic_derivation"] = copy.deepcopy(model["cad_semantic_derivation"])
    if rejected_opening_ids:
        updated_report.setdefault("warnings", []).append({
            "code": "cad_ai_openings_rejected_by_local_topology",
            "message": "AI 推断开口横跨房间交点，已丢弃该推断并保留 CAD 墙图",
            "opening_ids": sorted(rejected_opening_ids),
        })
    validation = validate_cad_model(model, updated_report)
    updated_report["validation"] = validation
    updated_report["hard_errors"].extend(validation.get("hard_errors") or [])
    updated_report.setdefault("warnings", []).extend(validation.get("warnings") or [])
    return model, updated_report


def refresh_hybrid_reference_anchor_report(model: dict) -> dict:
    """Recompute reference-camera readiness after CAD semantic enrichment.

    The raw CAD candidate is intentionally fail-closed before room semantics
    exist.  Once the audited raster has supplied room zones, observed INSERTs
    can be bound deterministically to those zones.  Keeping the raw report at
    that point would falsely claim that every observed object is orphaned.

    An opening with an unresolved 2D extent stays in the model and remains
    fully auditable, but is excluded from the reference-camera anchor pool.  It
    cannot safely supply a must-show bound and it must not block unrelated
    rooms whose required object anchors are complete.
    """
    hard_errors: list[dict] = []
    excluded_openings: list[dict] = []
    for row in model.get("fixed_objects") or []:
        if row.get("reference_anchor_ready") is not False:
            continue
        blockers = list(row.get("reference_anchor_blockers") or ["cad_fixed_object_anchor_unresolved"])
        hard_errors.extend({
            "code": str(code),
            "object_id": row.get("id"),
            "room_match_ids": copy.deepcopy(row.get("room_match_ids") or []),
        } for code in blockers)
    for row in model.get("room_contracts") or []:
        if str(row.get("status") or "") == "complete":
            continue
        hard_errors.append({
            "code": "cad_room_required_anchor_missing",
            "room_id": row.get("room_id"),
            "missing_role_groups": copy.deepcopy(row.get("missing_role_groups") or []),
        })
    for row in model.get("openings") or []:
        if row.get("reference_anchor_ready") is not False:
            continue
        excluded_openings.append({
            "opening_id": row.get("id"),
            "codes": list(row.get("reference_anchor_blockers") or ["cad_opening_anchor_unresolved"]),
            "action": "retained_as_cad_evidence_excluded_from_reference_camera_anchors",
        })
    report = {
        "status": "ready" if not hard_errors else "blocked",
        "hard_errors": hard_errors,
        "warnings": ([{
            "code": "cad_opening_anchor_excluded",
            "message": "Unresolved CAD opening extents remain visible evidence but cannot drive a reference camera.",
            "openings": excluded_openings,
        }] if excluded_openings else []),
        "excluded_opening_ids": [str(row.get("opening_id") or "") for row in excluded_openings],
        "source": "cad_ai_semantic_rooms_plus_observed_insert_containment_v1",
    }
    model["reference_anchor_report"] = report
    return report


def _point_segment_distance(point: tuple[float, float], first: tuple[float, float],
                            second: tuple[float, float]) -> float:
    t = _segment_projection(point, first, second)
    return math.dist(point, (first[0] + (second[0] - first[0]) * t,
                             first[1] + (second[1] - first[1]) * t))


def _annotate_opening_space_sides(
    candidates: Sequence[dict], rooms: Sequence[Mapping[str, Any]], *,
    origin_x: float, origin_y: float,
) -> None:
    """Attach topology-backed side spaces and safely reclassify interior doors.

    Raw parallel-frame geometry alone cannot distinguish a window frame from a
    closed door frame.  Room topology is available by the time the canonical
    model is assembled, so use samples on both normals of the opening axis as
    independent evidence.  Only a narrow, high-specificity rule is automatic:
    a residential-width frame connecting two *different* non-balcony indoor
    spaces is a door.  Exterior and balcony boundaries remain windows unless
    stronger source geometry proves otherwise.
    """
    from shapely.geometry import Point, Polygon  # type: ignore

    prepared: list[tuple[dict, Polygon]] = []
    for room in rooms:
        try:
            polygon = Polygon([
                (float(point["x"]), float(point["z"]))
                for point in room.get("polygon") or []
            ])
        except (TypeError, ValueError, KeyError):
            continue
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.area <= 1e-8:
            continue
        prepared.append(({
            "space_id": str(room.get("id") or ""),
            "physical_space_id": str(room.get("physical_space_id") or ""),
            "label": str(room.get("label") or ""),
            "room_type": str(room.get("room_type") or room.get("semantic_profile") or ""),
            "semantic_profile": str(room.get("semantic_profile") or room.get("room_type") or ""),
        }, polygon))

    def space_at(point: tuple[float, float]) -> dict | None:
        matches = [metadata for metadata, polygon in prepared
                   if polygon.buffer(.02).covers(Point(point))]
        if not matches:
            return None
        return sorted(matches, key=lambda row: (
            str(row.get("physical_space_id") or ""), str(row.get("space_id") or "")))[0]

    for candidate in candidates:
        axis = candidate.get("axis_segment_cad_m") or []
        try:
            first = (float(axis[0][0]) - origin_x, float(axis[0][1]) - origin_y)
            second = (float(axis[-1][0]) - origin_x, float(axis[-1][1]) - origin_y)
        except (TypeError, ValueError, IndexError):
            continue
        length = math.dist(first, second)
        if length <= .1:
            continue
        midpoint = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
        normal = (-(second[1] - first[1]) / length,
                  (second[0] - first[0]) / length)
        sides: list[dict | None] = [None, None]
        sample_rows: list[dict] = []
        for distance in (.12, .20, .30, .45, .60):
            samples = [
                (midpoint[0] + normal[0] * distance,
                 midpoint[1] + normal[1] * distance),
                (midpoint[0] - normal[0] * distance,
                 midpoint[1] - normal[1] * distance),
            ]
            hits = [space_at(point) for point in samples]
            for index, hit in enumerate(hits):
                if sides[index] is None and hit is not None:
                    sides[index] = hit
            sample_rows.append({
                "distance_m": distance,
                "positive_space_id": str((hits[0] or {}).get("space_id") or ""),
                "negative_space_id": str((hits[1] or {}).get("space_id") or ""),
            })
        distinct_ids = {
            str(row.get("physical_space_id") or row.get("space_id") or "")
            for row in sides if row
        }
        width = float(candidate.get("width_m") or length)
        side_types = {str(row.get("semantic_profile") or row.get("room_type") or "")
                      for row in sides if row}
        suggested_kind = str(candidate.get("kind") or "unknown")
        confidence = .60
        reason = "source_geometry_kind_retained"
        near_rows = sample_rows[:3]
        near_pairs = [
            (str(row.get("positive_space_id") or ""),
             str(row.get("negative_space_id") or ""))
            for row in near_rows
        ]
        stable_near_pair = (
            bool(near_pairs)
            and all(positive and negative and positive != negative
                    for positive, negative in near_pairs)
            and len(set(near_pairs)) == 1
        )
        if (stable_near_pair and len(distinct_ids) == 2 and .65 <= width <= 1.20
                and "balcony" not in side_types):
            suggested_kind = "door"
            confidence = .96
            reason = "stable_near_two_sided_interior_adjacency_and_residential_width"
        elif (len(distinct_ids) == 2 and .65 <= width <= 1.20
              and "balcony" not in side_types):
            confidence = .40
            reason = "near_side_adjacency_unresolved_distant_room_hit_not_door_proof"
        elif "balcony" in side_types and width >= 1.20:
            suggested_kind = "window"
            confidence = .96
            reason = "balcony_boundary_and_wide_frame"
        elif len(distinct_ids) == 1 and width >= 1.20:
            suggested_kind = "window"
            confidence = .85
            reason = "single_interior_side_and_wide_perimeter_frame"
        candidate["side_space_evidence"] = {
            "schema_version": 1,
            "method": "opening_axis_room_normal_sampling_v1",
            "positive": copy.deepcopy(sides[0]),
            "negative": copy.deepcopy(sides[1]),
            "samples": sample_rows,
        }
        candidate["suggested_kind"] = suggested_kind
        candidate["suggested_kind_confidence"] = round(confidence, 4)
        candidate["suggested_kind_reason"] = reason
        if (candidate.get("status") == "accepted"
                and len(distinct_ids) == 2 and .65 <= width <= 1.20
                and "balcony" not in side_types and not stable_near_pair):
            candidate["status"] = "review"
            candidate["kind_resolution"] = {
                "method": "opening_space_adjacency_kind_resolution_v2",
                "decision": "review_required",
                "from_kind": str(candidate.get("kind") or "unknown"),
                "to_kind": str(candidate.get("kind") or "unknown"),
                "confidence": round(confidence, 4),
                "reason": reason,
            }
            candidate["reason_codes"] = sorted(set(
                (candidate.get("reason_codes") or []) + [
                    "opening_near_side_adjacency_unresolved"]
            ))
            if str(candidate.get("wall_assembly_id") or "").startswith(
                    "cad_wall_global_opening_host_"):
                candidate["synthetic_host_disposition"] = "remove"
        if (candidate.get("status") == "accepted"
                and str(candidate.get("kind") or "") == "window"
                and suggested_kind == "door" and confidence >= .95):
            candidate["source_geometry_kind"] = "window"
            candidate["kind"] = "door"
            candidate["kind_resolution"] = {
                "method": "opening_space_adjacency_kind_resolution_v1",
                "decision": "auto_applied",
                "from_kind": "window",
                "to_kind": "door",
                "confidence": round(confidence, 4),
                "reason": reason,
            }
            candidate["reason_codes"] = sorted(set(
                (candidate.get("reason_codes") or []) + [
                    "interior_space_adjacency_reclassified_as_door"]
            ))


def render_cad_floorplan_preview(model: dict, project_id: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    root = _asset_directory(project_id, "project")
    path = os.path.join(root, "cad_floorplan.png")
    rows = [{"points": [(wall["start"]["x"], wall["start"]["z"]),
                         (wall["end"]["x"], wall["end"]["z"])]}
            for wall in model.get("walls") or []]
    footprints = [row for row in model.get("global_wall_footprints") or []
                  if isinstance(row, dict) and len(row.get("points") or []) >= 3]
    footprint_points = [
        (float(point.get("x") or 0), float(point.get("z") or 0))
        for footprint in footprints
        for ring in [footprint.get("points") or [], *(footprint.get("interior_rings") or [])]
        for point in ring
    ]
    if not rows and not footprint_points:
        raise CadError("cad_preview_empty", "CAD 模型没有可预览墙线")
    bounds = _bbox(footprint_points or [point for row in rows for point in row["points"]])
    width, height, padding = 1600, 1200, 70
    span_x, span_y = max(bounds[2] - bounds[0], .001), max(bounds[3] - bounds[1], .001)
    scale = min((width - 2 * padding) / span_x, (height - 2 * padding) / span_y)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        label_font = ImageFont.truetype("msyh.ttc", 22)
        small_font = ImageFont.truetype("msyh.ttc", 16)
    except OSError:
        label_font = small_font = ImageFont.load_default()

    def project(point: tuple[float, float]) -> tuple[float, float]:
        x_pixel = padding + (point[0] - bounds[0]) * scale
        if int(model.get("coordinate_contract_version", 0) or 0) >= 2:
            # V2 stores CAD +Y as model -Z.  Screen Y grows downward, so model
            # +Z must also grow downward; no second vertical reflection here.
            y_pixel = padding + (point[1] - bounds[1]) * scale
        else:
            y_pixel = height - padding - (point[1] - bounds[1]) * scale
        return x_pixel, y_pixel

    for footprint in footprints:
        exterior = [
            project((float(point.get("x") or 0), float(point.get("z") or 0)))
            for point in footprint.get("points") or []
        ]
        draw.polygon(exterior, fill=(35, 35, 35), outline=(20, 20, 20), width=2)
        for ring in footprint.get("interior_rings") or []:
            interior = [
                project((float(point.get("x") or 0), float(point.get("z") or 0)))
                for point in ring
            ]
            if len(interior) >= 3:
                draw.polygon(interior, fill=(250, 248, 242))

    physical_spaces = [
        row for row in model.get("physical_spaces") or []
        if isinstance(row, dict) and len(row.get("polygon") or []) >= 3
    ]
    floor_surfaces = physical_spaces or (model.get("rooms") or [])
    for surface in floor_surfaces:
        points = [(float(point.get("x") or 0), float(point.get("z") or 0))
                  for point in surface.get("polygon") or []]
        if len(points) < 3:
            continue
        projected = [project(point) for point in points]
        draw.polygon(projected, fill=(246, 241, 230), outline=(160, 145, 120), width=2)
        if physical_spaces:
            continue
        center = (sum(point[0] for point in projected) / len(projected),
                  sum(point[1] for point in projected) / len(projected))
        label = str(surface.get("label") or surface.get("id") or "room")
        try:
            draw.text(center, label, fill=(95, 69, 52), font=label_font, anchor="mm")
        except UnicodeEncodeError:
            draw.text(center, str(surface.get("id") or "room"), fill=(95, 69, 52), font=label_font, anchor="mm")
    if physical_spaces:
        # Rooms are semantic zones and may split one open physical floor.  Draw
        # their labels, but never their nearest-anchor boundaries as if they
        # were architectural edges.
        for room in model.get("rooms") or []:
            points = [(float(point.get("x") or 0), float(point.get("z") or 0))
                      for point in room.get("polygon") or []]
            if len(points) < 3:
                continue
            projected = [project(point) for point in points]
            center = (sum(point[0] for point in projected) / len(projected),
                      sum(point[1] for point in projected) / len(projected))
            label = str(room.get("label") or room.get("id") or "room")
            try:
                draw.text(center, label, fill=(95, 69, 52), font=label_font, anchor="mm")
            except UnicodeEncodeError:
                draw.text(center, str(room.get("id") or "room"), fill=(95, 69, 52),
                          font=label_font, anchor="mm")
    if not footprints:
        for row in rows:
            projected = [project(point) for point in row["points"]]
            draw.line(projected, fill=(30, 30, 30), width=4)
    walls_by_id = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    for opening in model.get("openings") or []:
        wall = walls_by_id.get(str(opening.get("wall_id") or ""))
        if not wall or float(opening.get("width_m") or 0) <= 0:
            continue
        first = (float(wall["start"]["x"]), float(wall["start"]["z"]))
        second = (float(wall["end"]["x"]), float(wall["end"]["z"]))
        length = max(math.dist(first, second), 1e-9)
        start_t = float(opening.get("offset_m") or 0) / length
        end_t = (float(opening.get("offset_m") or 0) + float(opening.get("width_m") or 0)) / length
        opening_segment = [
            (first[0] + (second[0] - first[0]) * start_t, first[1] + (second[1] - first[1]) * start_t),
            (first[0] + (second[0] - first[0]) * end_t, first[1] + (second[1] - first[1]) * end_t),
        ]
        draw.line([project(point) for point in opening_segment],
                  fill=(38, 126, 180) if opening.get("kind") == "window" else (190, 68, 52), width=9)
    for obj in model.get("fixed_objects") or []:
        size = obj.get("size") or {}
        width_m, depth_m = float(size.get("x") or 0), float(size.get("z") or 0)
        if width_m <= 0 or depth_m <= 0:
            continue
        center = obj.get("position") or {}
        cx, cz = float(center.get("x") or 0), float(center.get("z") or 0)
        radians = math.radians(float(obj.get("rotation_y_deg") or 0))
        cos_r, sin_r = math.cos(radians), math.sin(radians)
        corners = []
        for local_x, local_z in ((-width_m / 2, -depth_m / 2), (width_m / 2, -depth_m / 2),
                                 (width_m / 2, depth_m / 2), (-width_m / 2, depth_m / 2)):
            corners.append((cx + local_x * cos_r - local_z * sin_r,
                            cz + local_x * sin_r + local_z * cos_r))
        projected = [project(point) for point in corners]
        draw.polygon(projected, outline=(118, 73, 178), width=4)
        draw.text(project((cx, cz)), str(obj.get("semantic_role") or obj.get("kind") or "object"),
                  fill=(86, 45, 145), font=small_font, anchor="mm")
    draw.text((20, 20), "CAD authoritative floor plan preview", fill=(20, 20, 20))
    temporary = f"{path}.{uuid.uuid4().hex}.tmp"
    image.save(temporary, "PNG")
    os.replace(temporary, path)
    return path


def ingest_cad(path: str, project_id: str, *, timeout: float = 120.0,
               preferred_candidate_id: str = "") -> tuple[dict, dict, str]:
    source = require_managed_cad_path(path)
    source_meta = inspect_cad_file(source)
    dxf_path, conversion = convert_dwg_to_ascii_dxf(source, project_id, timeout=timeout)
    try:
        model, report = parse_dxf(
            dxf_path, project_id, preferred_candidate_id=preferred_candidate_id)
    except CadError as ex:
        details = copy.deepcopy(ex.details or {})
        report = details.get("parse_report")
        model = details.get("model")
        if isinstance(report, dict) and isinstance(model, dict) and model:
            # A hard review gate is an expected parser outcome, not a reason to
            # lose the original DWG identity, converter evidence or usable
            # preview.  Persist a new immutable enriched report instead of
            # mutating the already-written parser report behind its hash.
            report["source"] = source_meta
            report["conversion"] = conversion
            preview_path = render_cad_floorplan_preview(model, project_id)
            report["preview_path"] = preview_path
            report = persist_cad_report(project_id, report, purpose="ingest_review")
            details.update(parse_report=report, model=model)
        raise CadError(
            ex.code, ex.message, status_code=ex.status_code, details=details,
        ) from ex
    report["source"] = source_meta
    report["conversion"] = conversion
    preview_path = render_cad_floorplan_preview(model, project_id)
    report["preview_path"] = preview_path
    report = persist_cad_report(project_id, report, purpose="ingest")
    return model, report, preview_path
