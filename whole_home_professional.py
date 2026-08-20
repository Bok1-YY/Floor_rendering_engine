# -*- coding: utf-8 -*-
"""Professional proposal contracts for raster-first whole-home projects.

This module deliberately stays independent from HTTP and persistence.  It turns
the existing metric model into three immutable, hash-bound products:

* ``FloorplanGraphV1`` -- reviewed plan topology, independent of rendering;
* ``ConstructionProfileV1`` -- explicit vertical assumptions missing from 2D;
* ``SceneRecipeV1`` -- deterministic furnishing/style proposal candidates.

The contracts are useful before a Blender worker exists: they stop prompts and
render jobs from silently inventing a different house on every run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from typing import Any, Iterable, Mapping

try:
    from shapely.geometry import Polygon, box
except Exception:  # pragma: no cover - the production dependency is required.
    Polygon = None
    box = None


FLOORPLAN_GRAPH_VERSION = "floorplan-graph-v1"
CONSTRUCTION_PROFILE_VERSION = "construction-profile-v1"
SCENE_RECIPE_VERSION = "scene-recipe-v1"
MARKETING_PROPOSAL_VERSION = "marketing-proposal-v1"
STYLE_PACK_ID = "modern_warm_natural_v1"


class ProfessionalContractError(ValueError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _round(value: Any, digits: int = 6) -> float:
    return round(_number(value), digits)


def _point(value: Mapping[str, Any] | None) -> dict[str, float]:
    value = value or {}
    return {"x": _round(value.get("x")), "z": _round(value.get("z"))}


def _sorted_rows(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted((row for row in rows if isinstance(row, Mapping)),
                  key=lambda row: str(row.get("id") or ""))


def _source_handles(row: Mapping[str, Any]) -> list[str]:
    provenance = row.get("cad_provenance")
    if not isinstance(provenance, Mapping):
        return []
    handles: set[str] = set()
    for key in ("handle", "root_handle", "source_handle"):
        if provenance.get(key):
            handles.add(str(provenance[key]))
    for key in ("handles", "source_handles"):
        values = provenance.get(key)
        if isinstance(values, list):
            handles.update(str(value) for value in values if value)
    return sorted(handles)


def build_floorplan_graph(project: Mapping[str, Any]) -> dict[str, Any]:
    """Build a stable plan graph without copying render/camera state."""
    model = project.get("model") if isinstance(project.get("model"), Mapping) else {}
    registration = project.get("source_registration")
    if not isinstance(registration, Mapping):
        registration = model.get("source_registration") if isinstance(
            model.get("source_registration"), Mapping) else {}

    walls = []
    for row in _sorted_rows(model.get("walls") or []):
        walls.append({
            "id": str(row.get("id") or ""),
            "start": _point(row.get("start")),
            "end": _point(row.get("end")),
            "thickness_m": _round(row.get("thickness_m"), 5),
            "height_m": _round(row.get("height_m") or model.get("wall_height_m"), 5),
            "kind": str(row.get("kind") or "interior"),
            "source": str(row.get("source") or "unknown"),
            "confidence": _round(row.get("confidence"), 5),
            "source_handles": _source_handles(row),
        })

    rooms = []
    for row in _sorted_rows(model.get("rooms") or []):
        rooms.append({
            "id": str(row.get("id") or ""),
            "label": str(row.get("label") or ""),
            "room_type": str(row.get("room_type") or row.get("semantic_profile") or "other"),
            "polygon": [_point(point) for point in row.get("polygon") or []],
            "area_m2": _round(row.get("area_m2"), 3),
            "selected": bool(row.get("selected", True)),
            "semantic_status": str(row.get("semantic_status") or "pending"),
            "source": str(row.get("source") or "unknown"),
            "confidence": _round(row.get("confidence"), 5),
            "source_handles": _source_handles(row),
        })

    openings = []
    for row in _sorted_rows(model.get("openings") or []):
        openings.append({
            "id": str(row.get("id") or ""),
            "wall_id": str(row.get("wall_id") or ""),
            "kind": str(row.get("kind") or "door"),
            "offset_m": _round(row.get("offset_m"), 5),
            "width_m": _round(row.get("width_m"), 5),
            "height_m": _round(row.get("height_m"), 5),
            "sill_height_m": _round(row.get("sill_height_m"), 5),
            "review_status": str(row.get("review_status") or "pending"),
            "source": str(row.get("source") or "unknown"),
            "confidence": _round(row.get("confidence"), 5),
            "source_handles": _source_handles(row),
        })

    unresolved = []
    unresolved.extend(f"room:{row['id']}" for row in rooms
                      if row["selected"] and row["semantic_status"] not in ("complete", "confirmed"))
    unresolved.extend(f"opening:{row['id']}" for row in openings
                      if row["review_status"] not in ("accepted", "confirmed"))
    unresolved.extend(f"uncertainty:{index + 1}" for index, _ in enumerate(
        model.get("uncertainties") or []))

    input_grade = str(project.get("input_grade") or model.get("input_grade") or "raster_draft")
    scale_locked = bool(registration.get("registration_hash")) and input_grade in (
        "raster_human_locked", "vector_authoritative")
    verified = bool(project.get("verified"))
    status = "locked" if verified else "reviewed" if scale_locked and not unresolved else "draft"
    graph = {
        "version": FLOORPLAN_GRAPH_VERSION,
        "project_id": str(project.get("project_id") or ""),
        "project_revision": int(project.get("revision") or 0),
        "coordinate_system": "metres-y-up",
        "plan_plane": "x-z",
        "topdown_camera_contract": {
            "position_axis": "+Y", "view_direction": "-Y", "screen_up": "-Z",
        },
        "source": {
            "source_type": str(project.get("source_type") or "floorplan"),
            "input_grade": input_grade,
            "registration_hash": str(registration.get("registration_hash") or ""),
            "source_hash": str(registration.get("source_hash") or ""),
            "model_facts_hash": str(model.get("model_facts_hash") or ""),
        },
        "extent_m": {
            "width": _round(model.get("width_m"), 5),
            "depth": _round(model.get("depth_m"), 5),
        },
        "walls": walls,
        "rooms": rooms,
        "openings": openings,
        "review": {
            "status": status,
            "scale_locked": scale_locked,
            "geometry_verified": verified,
            "unresolved_ids": sorted(unresolved),
            "uncertainties": [str(value)[:500] for value in model.get("uncertainties") or []],
        },
    }
    graph["graph_hash"] = canonical_hash(graph)
    return validate_floorplan_graph(graph)


def validate_floorplan_graph(value: Mapping[str, Any]) -> dict[str, Any]:
    graph = copy.deepcopy(dict(value))
    supplied_hash = str(graph.pop("graph_hash", ""))
    if graph.get("version") != FLOORPLAN_GRAPH_VERSION:
        raise ProfessionalContractError("floorplan_graph_version_invalid", "FloorplanGraph 版本不受支持")
    walls = graph.get("walls") or []
    rooms = graph.get("rooms") or []
    if len(walls) < 3:
        raise ProfessionalContractError("floorplan_graph_too_few_walls", "户型至少需要三面有效墙")
    if not rooms:
        raise ProfessionalContractError("floorplan_graph_rooms_missing", "户型尚未形成任何房间")
    wall_ids = [str(row.get("id") or "") for row in walls]
    if any(not value for value in wall_ids) or len(wall_ids) != len(set(wall_ids)):
        raise ProfessionalContractError("floorplan_graph_wall_ids_invalid", "墙体 ID 缺失或重复")
    room_ids = [str(row.get("id") or "") for row in rooms]
    if any(not value for value in room_ids) or len(room_ids) != len(set(room_ids)):
        raise ProfessionalContractError("floorplan_graph_room_ids_invalid", "房间 ID 缺失或重复")
    for room in rooms:
        if len(room.get("polygon") or []) < 3:
            raise ProfessionalContractError(
                "floorplan_graph_room_polygon_invalid", f"房间 {room.get('id')} 没有闭合边界")
    for opening in graph.get("openings") or []:
        if str(opening.get("wall_id") or "") not in wall_ids:
            raise ProfessionalContractError(
                "floorplan_graph_opening_host_missing", f"开口 {opening.get('id')} 没有有效宿主墙")
    graph["graph_hash"] = canonical_hash(graph)
    if supplied_hash and supplied_hash != graph["graph_hash"]:
        raise ProfessionalContractError("floorplan_graph_hash_mismatch", "FloorplanGraph 内容与哈希不一致")
    return graph


_PROFILE_RANGES = {
    "wall_height_m": (2.2, 4.5, 2.8),
    "interior_door_height_m": (1.8, 2.5, 2.1),
    "window_sill_height_m": (0.0, 1.5, 0.9),
    "window_head_height_m": (1.2, 3.5, 2.1),
    "floor_finish_thickness_m": (0.005, 0.15, 0.015),
    "ceiling_drop_m": (0.0, 0.6, 0.08),
    "skirting_height_m": (0.03, 0.3, 0.08),
}


def default_construction_profile(project: Mapping[str, Any]) -> dict[str, Any]:
    model = project.get("model") if isinstance(project.get("model"), Mapping) else {}
    fields: dict[str, Any] = {}
    for name, (_, _, default) in _PROFILE_RANGES.items():
        model_value = model.get("wall_height_m") if name == "wall_height_m" else None
        fields[name] = {
            "value": _round(model_value if model_value is not None else default, 5),
            "source": "model_draft" if model_value is not None else "residential_default_assumption",
            "confirmed": False,
        }
    profile = {
        "version": CONSTRUCTION_PROFILE_VERSION,
        "project_id": str(project.get("project_id") or ""),
        "project_revision": int(project.get("revision") or 0),
        "status": "assumptions_pending",
        "fields": fields,
        "reviewer": "",
        "confirmed_at": None,
    }
    profile["profile_hash"] = canonical_hash(profile)
    return profile


def confirm_construction_profile(project: Mapping[str, Any], values: Mapping[str, Any], *,
                                 reviewer: str, now: float | None = None) -> dict[str, Any]:
    unknown = sorted(set(values) - set(_PROFILE_RANGES))
    if unknown:
        raise ProfessionalContractError(
            "construction_profile_field_unknown", "存在不支持的构造参数", {"fields": unknown})
    missing = sorted(set(_PROFILE_RANGES) - set(values))
    if missing:
        raise ProfessionalContractError(
            "construction_profile_fields_missing", "必须逐项确认全部构造假设", {"fields": missing})
    fields: dict[str, Any] = {}
    for name, (minimum, maximum, _) in _PROFILE_RANGES.items():
        number = _number(values.get(name), float("nan"))
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ProfessionalContractError(
                "construction_profile_value_invalid", f"{name} 必须在 {minimum}–{maximum} 之间",
                {"field": name, "minimum": minimum, "maximum": maximum})
        fields[name] = {"value": round(number, 5), "source": "human_confirmation", "confirmed": True}
    if fields["window_head_height_m"]["value"] >= fields["wall_height_m"]["value"]:
        raise ProfessionalContractError(
            "construction_profile_window_head_invalid", "窗顶高度必须低于墙体高度")
    if fields["window_sill_height_m"]["value"] >= fields["window_head_height_m"]["value"]:
        raise ProfessionalContractError(
            "construction_profile_window_range_invalid", "窗台高度必须低于窗顶高度")
    profile = {
        "version": CONSTRUCTION_PROFILE_VERSION,
        "project_id": str(project.get("project_id") or ""),
        "project_revision": int(project.get("revision") or 0),
        "status": "confirmed",
        "fields": fields,
        "reviewer": str(reviewer or "local-user")[:100],
        "confirmed_at": float(now if now is not None else time.time()),
    }
    profile["profile_hash"] = canonical_hash(profile)
    return profile


_ASSET_SPECS: dict[str, dict[str, Any]] = {
    "mw_sofa_3seat_v1": {"role": "sofa", "size_m": [2.25, 0.92, 0.82]},
    "mw_tv_console_v1": {"role": "tv_console", "size_m": [1.8, 0.42, 0.48]},
    "mw_coffee_table_v1": {"role": "coffee_table", "size_m": [1.1, 0.6, 0.38]},
    "mw_living_rug_v1": {"role": "rug", "size_m": [2.4, 1.7, 0.02], "overlap_layer": True},
    "mw_dining_table_v1": {"role": "dining_table", "size_m": [1.5, 0.82, 0.76]},
    "mw_dining_chair_v1": {"role": "dining_chair", "size_m": [0.48, 0.52, 0.82]},
    "mw_bed_1800_v1": {"role": "bed", "size_m": [1.9, 2.15, 0.62]},
    "mw_bed_1500_v1": {"role": "bed", "size_m": [1.6, 2.1, 0.58]},
    "mw_bedside_v1": {"role": "bedside", "size_m": [0.48, 0.42, 0.48]},
    "mw_wardrobe_v1": {"role": "wardrobe", "size_m": [1.8, 0.62, 2.35]},
    "mw_desk_v1": {"role": "desk", "size_m": [1.2, 0.58, 0.75]},
    "mw_desk_chair_v1": {"role": "chair", "size_m": [0.52, 0.52, 0.86]},
    "mw_kitchen_run_v1": {"role": "kitchen_run", "size_m": [2.4, 0.62, 2.3]},
    "mw_vanity_v1": {"role": "vanity", "size_m": [0.9, 0.52, 0.86]},
    "mw_toilet_v1": {"role": "toilet", "size_m": [0.42, 0.7, 0.78]},
    "mw_plant_v1": {"role": "plant", "size_m": [0.48, 0.48, 1.2]},
}


def modern_warm_natural_style_pack() -> dict[str, Any]:
    value = {
        "style_pack_id": STYLE_PACK_ID,
        "version": 1,
        "title": "现代暖木自然",
        "delivery_scope": "marketing_concept_only",
        "palette": {
            "wall": "warm-off-white", "floor": "light-warm-oak",
            "textile": "oatmeal-and-warm-grey", "metal": "charcoal-black",
            "accent": "restrained-natural-green",
        },
        "lighting": {
            "daylight": "soft-natural-daylight", "artificial_cct_k": [3000, 3500],
            "exposure": "residential-balanced-agx",
        },
        "asset_policy": {
            "allowed_sources": ["project_procedural", "poly_haven_cc0", "blendkit_royalty_free_reviewed"],
            "raw_asset_redistribution": False,
            "unreviewed_asset_blocked": True,
        },
        "assets": [
            {"asset_id": asset_id, **copy.deepcopy(spec),
             "source_kind": "project_procedural", "license": "AGPL-3.0-project-code",
             "render_status": "proxy_until_pbr_asset_approved"}
            for asset_id, spec in sorted(_ASSET_SPECS.items())
        ],
    }
    value["style_pack_hash"] = canonical_hash(value)
    return value


def _room_kind(room: Mapping[str, Any]) -> str:
    text = f"{room.get('room_type', '')} {room.get('label', '')}".lower()
    if any(token in text for token in ("living", "客厅", "起居")):
        return "living"
    if any(token in text for token in ("dining", "餐厅", "餐区")):
        return "dining"
    if any(token in text for token in ("primary_bed", "master", "主卧")):
        return "primary_bedroom"
    if any(token in text for token in ("bedroom", "卧室", "次卧", "儿童房", "客卧")):
        return "bedroom"
    if any(token in text for token in ("kitchen", "厨房")):
        return "kitchen"
    if any(token in text for token in ("bath", "toilet", "卫生间", "卫浴")):
        return "bathroom"
    if any(token in text for token in ("study", "office", "书房")):
        return "study"
    if any(token in text for token in ("balcony", "阳台")):
        return "balcony"
    return "other"


def _room_polygon(room: Mapping[str, Any]):
    if Polygon is None:
        return None
    points = [(float(point.get("x", 0)), float(point.get("z", 0)))
              for point in room.get("polygon") or [] if isinstance(point, Mapping)]
    if len(points) < 3:
        return None
    polygon = Polygon(points)
    return polygon.buffer(0) if not polygon.is_valid else polygon


def _opening_clearance_zones(graph: Mapping[str, Any]) -> list[Any]:
    if box is None:
        return []
    walls = {str(row.get("id") or ""): row for row in graph.get("walls") or []}
    zones = []
    for opening in graph.get("openings") or []:
        if opening.get("kind") not in ("door", "open_connection"):
            continue
        wall = walls.get(str(opening.get("wall_id") or ""))
        if not wall:
            continue
        start, end = wall["start"], wall["end"]
        dx, dz = end["x"] - start["x"], end["z"] - start["z"]
        length = math.hypot(dx, dz)
        if length <= 1e-6:
            continue
        center_distance = float(opening.get("offset_m") or 0) + float(opening.get("width_m") or .9) / 2
        ratio = max(0.0, min(1.0, center_distance / length))
        x, z = start["x"] + dx * ratio, start["z"] + dz * ratio
        zones.append(box(x - .65, z - .65, x + .65, z + .65))
    return zones


def _layout_room(room: Mapping[str, Any], variant_index: int,
                 opening_zones: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    polygon = _room_polygon(room)
    if polygon is None or polygon.is_empty:
        return [], [{"code": "room_polygon_invalid", "room_id": room.get("id")}]
    min_x, min_z, max_x, max_z = polygon.bounds
    span_x, span_z = max_x - min_x, max_z - min_z
    if min(span_x, span_z) < 1.2:
        return [], [{"code": "room_too_narrow_for_layout", "room_id": room.get("id")}]
    long_x = span_x >= span_z
    mirror = variant_index == 2
    alternate = variant_index == 3
    safe = polygon.buffer(-.06)
    placements: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    footprints: list[tuple[str, Any, bool]] = []

    def place(asset_id: str, a: float, b: float, rotation: float = 0.0,
              *, optional: bool = False, instance_suffix: str = "") -> None:
        spec = _ASSET_SPECS[asset_id]
        a_value = 1 - a if mirror else a
        x = min_x + (a_value * span_x if long_x else b * span_x)
        z = min_z + (b * span_z if long_x else a_value * span_z)
        yaw = rotation + (0 if long_x else 90)
        width, depth, height = spec["size_m"]
        if int(round(yaw / 90)) % 2:
            footprint_w, footprint_d = depth, width
        else:
            footprint_w, footprint_d = width, depth
        footprint = box(x - footprint_w / 2, z - footprint_d / 2,
                        x + footprint_w / 2, z + footprint_d / 2)
        if safe.is_empty or not safe.covers(footprint):
            issues.append({
                "code": "asset_does_not_fit", "room_id": room.get("id"),
                "asset_id": asset_id, "optional": optional,
            })
            return
        if not spec.get("overlap_layer"):
            collides = [instance_id for instance_id, other, overlap in footprints
                        if not overlap and footprint.intersection(other).area > .01]
            if collides:
                issues.append({
                    "code": "asset_collision", "room_id": room.get("id"),
                    "asset_id": asset_id, "with": collides, "optional": optional,
                })
                return
            if any(footprint.intersection(zone).area > .01 for zone in opening_zones):
                issues.append({
                    "code": "door_clearance_collision", "room_id": room.get("id"),
                    "asset_id": asset_id, "optional": optional,
                })
                return
        instance_id = f"{room.get('id')}_{spec['role']}_{len(placements) + 1}{instance_suffix}"
        placements.append({
            "instance_id": instance_id,
            "asset_id": asset_id,
            "room_id": str(room.get("id") or ""),
            "semantic_role": spec["role"],
            "size_m": {"width": width, "depth": depth, "height": height},
            "transform": {
                "position_m": {"x": round(x, 5), "y": round(height / 2, 5), "z": round(z, 5)},
                "rotation_y_deg": round(yaw % 360, 3),
                "scale": [1.0, 1.0, 1.0],
            },
            "footprint_m": {"width": footprint_w, "depth": footprint_d},
            "placement_source": "deterministic_room_template_v1",
        })
        footprints.append((instance_id, footprint, bool(spec.get("overlap_layer"))))

    kind = _room_kind(room)
    if kind == "living":
        place("mw_living_rug_v1", .5, .5)
        place("mw_sofa_3seat_v1", .22 if not alternate else .5, .5 if not alternate else .22, 90)
        place("mw_tv_console_v1", .80 if not alternate else .5, .5 if not alternate else .8, 90)
        place("mw_coffee_table_v1", .5, .5, 0)
        place("mw_plant_v1", .82, .82, optional=True)
    elif kind == "dining":
        place("mw_dining_table_v1", .5, .5, 0)
        for index, (a, b, yaw) in enumerate(((.28, .5, 90), (.72, .5, 90), (.5, .25, 0), (.5, .75, 0))):
            place("mw_dining_chair_v1", a, b, yaw, optional=index >= 2, instance_suffix=f"_{index + 1}")
    elif kind in ("primary_bedroom", "bedroom"):
        bed = "mw_bed_1800_v1" if kind == "primary_bedroom" and min(span_x, span_z) >= 2.8 else "mw_bed_1500_v1"
        place(bed, .5 if alternate else .28, .5 if alternate else .5, 90)
        place("mw_wardrobe_v1", .78, .78 if alternate else .22, 0)
        place("mw_bedside_v1", .5 if alternate else .28, .78, 0, optional=True)
    elif kind == "kitchen":
        place("mw_kitchen_run_v1", .5, .18 if not alternate else .82, 0)
    elif kind == "bathroom":
        place("mw_vanity_v1", .28, .22, 0)
        place("mw_toilet_v1", .7, .7, 0)
    elif kind == "study":
        place("mw_desk_v1", .5, .2, 0)
        place("mw_desk_chair_v1", .5, .48, 0)
        place("mw_plant_v1", .82, .78, optional=True)
    elif kind == "balcony":
        place("mw_plant_v1", .3, .5, optional=True)
        place("mw_plant_v1", .7, .5, optional=True, instance_suffix="_2")
    elif polygon.area >= 5:
        place("mw_desk_v1", .5, .25, 0, optional=True)

    required_issues = [issue for issue in issues if not issue.get("optional")]
    if kind in ("living", "dining", "primary_bedroom", "bedroom", "kitchen", "bathroom") and not placements:
        required_issues.append({"code": "required_room_layout_empty", "room_id": room.get("id")})
    return placements, required_issues + [issue for issue in issues if issue.get("optional")]


def generate_scene_recipe(project: Mapping[str, Any], construction_profile: Mapping[str, Any], *,
                          variant_index: int = 1, now: float | None = None) -> dict[str, Any]:
    if variant_index not in (1, 2, 3):
        raise ProfessionalContractError("scene_variant_invalid", "方案候选只能为 1、2 或 3")
    graph = build_floorplan_graph(project)
    profile = copy.deepcopy(dict(construction_profile))
    if profile.get("version") != CONSTRUCTION_PROFILE_VERSION:
        raise ProfessionalContractError("construction_profile_invalid", "构造参数合同不存在或版本错误")
    supplied_profile_hash = str(profile.pop("profile_hash", ""))
    profile_hash = canonical_hash(profile)
    if supplied_profile_hash and supplied_profile_hash != profile_hash:
        raise ProfessionalContractError("construction_profile_hash_mismatch", "构造参数已被修改")

    instances: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    opening_zones = _opening_clearance_zones(graph)
    room_summaries = []
    for room in graph["rooms"]:
        if not room.get("selected", True):
            continue
        room_instances, room_issues = _layout_room(room, variant_index, opening_zones)
        instances.extend(room_instances)
        issues.extend(room_issues)
        room_summaries.append({
            "room_id": room["id"], "room_type": _room_kind(room),
            "instance_count": len(room_instances),
            "blocking_issue_count": sum(not issue.get("optional") for issue in room_issues),
        })

    blockers = [issue for issue in issues if not issue.get("optional")]
    score = max(0, round(100 - len(blockers) * 12 - (len(issues) - len(blockers)) * 2, 2))
    style_pack = modern_warm_natural_style_pack()
    created_at = float(now if now is not None else time.time())
    stable = {
        "version": SCENE_RECIPE_VERSION,
        "project_id": str(project.get("project_id") or ""),
        "project_revision": int(project.get("revision") or 0),
        "floorplan_graph_hash": graph["graph_hash"],
        "construction_profile_hash": supplied_profile_hash or profile_hash,
        "style_pack_id": STYLE_PACK_ID,
        "style_pack_hash": style_pack["style_pack_hash"],
        "variant_index": variant_index,
        "status": "draft",
        "delivery_scope": "marketing_concept_only",
        "instances": instances,
        "rooms": room_summaries,
        "materials": {
            "walls": "warm_off_white_matte_v1",
            "floor": "light_warm_oak_natural_v1",
            "ceiling": "soft_white_matte_v1",
            "metal": "charcoal_powdercoat_v1",
        },
        "lighting": {
            "rig": "soft_daylight_warm_fill_v1", "artificial_cct_k": 3200,
            "color_management": "AgX", "exposure": 0.0,
        },
        "quality": {
            "status": "passed" if not blockers else "needs_review",
            "score": score,
            "blocking_issues": blockers,
            "warnings": [issue for issue in issues if issue.get("optional")],
            "instance_count": len(instances),
        },
        "created_at": created_at,
        "review": {"reviewer": "", "note": "", "reviewed_at": None},
    }
    recipe_hash = canonical_hash(stable)
    stable["recipe_id"] = f"scene_{recipe_hash[:16]}"
    stable["scene_hash"] = canonical_hash({
        key: value for key, value in stable.items() if key not in ("created_at", "review", "status")
    })
    stable["recipe_hash"] = canonical_hash(stable)
    return stable


def review_scene_recipe(recipe: Mapping[str, Any], *, reviewer: str, note: str,
                        lock: bool, project_verified: bool, construction_confirmed: bool,
                        now: float | None = None) -> dict[str, Any]:
    value = copy.deepcopy(dict(recipe))
    if value.get("version") != SCENE_RECIPE_VERSION:
        raise ProfessionalContractError("scene_recipe_version_invalid", "SceneRecipe 版本不受支持")
    supplied_hash = str(value.pop("recipe_hash", ""))
    if supplied_hash and supplied_hash != canonical_hash(value):
        raise ProfessionalContractError("scene_recipe_hash_mismatch", "SceneRecipe 内容与哈希不一致")
    if value.get("quality", {}).get("status") != "passed":
        raise ProfessionalContractError(
            "scene_recipe_quality_blocked", "方案仍有家具越界、碰撞或缺失，不能通过复核",
            {"issues": value.get("quality", {}).get("blocking_issues") or []})
    if lock and not construction_confirmed:
        raise ProfessionalContractError("construction_profile_not_confirmed", "请先逐项确认层高和门窗高度假设")
    if lock and not project_verified:
        raise ProfessionalContractError("geometry_not_verified", "请先锁定户型几何，再锁定场景方案")
    value["status"] = "locked" if lock else "reviewed"
    value["review"] = {
        "reviewer": str(reviewer or "local-user")[:100],
        "note": str(note or "")[:2000],
        "reviewed_at": float(now if now is not None else time.time()),
    }
    value["recipe_hash"] = canonical_hash(value)
    return value


def build_marketing_proposal(project: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    recipe = dict(recipe or {})
    panos = project.get("pano_captures") or []
    certified = []
    enhanced = []
    for row in panos:
        gate = row.get("gate") if isinstance(row.get("gate"), Mapping) else {}
        review = row.get("review") if isinstance(row.get("review"), Mapping) else {}
        same_scene = bool(recipe.get("scene_hash") and (
            row.get("scene_hash") == recipe.get("scene_hash")
            or (row.get("manifest") or {}).get("scene_hash") == recipe.get("scene_hash")
        ))
        if same_scene and gate.get("gate_pass") is True and review.get("passed") is True:
            certified.append(str(row.get("capture_id") or row.get("pano_id") or ""))
        if same_scene and (row.get("edited_rgb_path") or row.get("repaired_rgb_path")):
            enhanced.append(str(row.get("capture_id") or row.get("pano_id") or ""))
    blockers = []
    if recipe.get("status") != "locked":
        blockers.append("scene_recipe_not_locked")
    if len(certified) < 3:
        blockers.append("certified_panorama_count_below_3")
    value = {
        "version": MARKETING_PROPOSAL_VERSION,
        "project_id": str(project.get("project_id") or ""),
        "project_revision": int(project.get("revision") or 0),
        "scene_recipe_id": str(recipe.get("recipe_id") or ""),
        "scene_hash": str(recipe.get("scene_hash") or ""),
        "status": "ready" if not blockers else "draft",
        "audience": "renovation_sales_lead",
        "deliverables": {
            "certified_master_panoramas": certified,
            "ai_enhanced_derivatives": enhanced,
            "required_panorama_count": {"minimum": 3, "maximum": 8},
            "hero_stills": [],
            "tour_manifest": None,
            "share_link": None,
            "qr_code": None,
        },
        "blockers": blockers,
        "disclaimers": [
            "本方案为装修营销概念效果，不是施工图。",
            "层高、门窗高度和构造尺寸以已确认假设为准，施工前必须现场复尺。",
            "概念家具和材质不对应真实 SKU、库存或报价。",
            "AI 美化图为非认证衍生版本，结构以通过几何与人工门禁的认证母版为准。",
        ],
    }
    value["proposal_hash"] = canonical_hash(value)
    return value


def professional_capabilities() -> dict[str, Any]:
    return {
        "version": 1,
        "product_mode": "raster_first_renovation_sales_proposal",
        "primary_inputs": ["png", "jpg", "jpeg", "webp", "single_page_pdf"],
        "advanced_inputs": ["dwg", "dxf"],
        "delivery_scope": "marketing_concept_only",
        "human_review_target_minutes": {"median": 8, "p90": 15},
        "style_packs": [modern_warm_natural_style_pack()],
        "scene_variants": 3,
        "panorama_target": {"minimum": 3, "maximum": 8, "width": 4096, "height": 2048},
        "output_grades": ["certified_master", "ai_enhanced_derivative"],
        "cost_budget_cny": {"minimum": 10, "maximum": 30},
        "construction_or_pricing_authority": False,
    }
