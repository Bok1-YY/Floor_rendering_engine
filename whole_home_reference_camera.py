# -*- coding: utf-8 -*-
"""Deterministic reference-slot camera proposals.

The public JustEasy viewer exposes scene semantics, not a CAD-space camera
transform.  This module therefore treats the reference as a relative landing
contract and derives every coordinate from the current CAD model.  It performs
no network or model calls.  Browser subject-ID evidence is deliberately left
pending: the backend cannot truthfully claim pixel bounds before WebGL renders
the exact camera.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from collections import defaultdict
from typing import Any, Iterable


LANDING_SOURCE = "inferred_from_reference_visual_and_cad_anchors"
LANDING_MODE = "cad_semantic_relative_region"
EYE_HEIGHT_M = 1.45
TARGET_HEIGHT_M = 1.45
YAW_COARSE_STEP_DEG = 10
YAW_REFINE_STEP_DEG = 2
PITCH_SAMPLES_DEG = (-8.0, -4.0, 0.0, 4.0)

_SLOT_SUBJECT_SAFE_FRAME_OVERRIDES = {
    "kitchen_cookline_elevation": {
        "worktop": {"x_min": .08, "x_max": .92, "y_min": .08, "y_max": 1.0},
    },
    "living_openplan_axis": {
        "dining zone": {"x_min": 0.0, "x_max": .92, "y_min": .08, "y_max": 1.0},
        "CAD-authentic corridor": {"x_min": .08, "x_max": .92, "y_min": .08, "y_max": 1.0},
    },
    "secondary_bed_soft_headwall": {
        "bed": {"x_min": 0.0, "x_max": 1.0, "y_min": .08, "y_max": 1.0},
    },
    "secondary_bed_dark_headwall": {
        "bed axis": {"x_min": 0.0, "x_max": 1.0, "y_min": .08, "y_max": 1.0},
    },
}


_SLOT_REQUIREMENTS: dict[str, dict] = {
    "living_openplan_axis": {
        "anchor_groups": [
            # The reference composition prefers a dining foreground, but the
            # current CAD may place its only dining table behind a solid wall.
            # In that case CAD wins: omit it with evidence instead of forcing
            # a hallucinated open plan.
            {"subject": "dining zone", "roles": ["dining_table"],
             "optional_visibility": True},
            {"subject": "TV wall", "roles": ["tv"]},
            {"subject": "CAD-authentic corridor", "opening_kinds": ["door", "open_connection"]},
        ],
        "must_validate": ["same_floor_elevation", "cad_corridor_connectivity", "camera_clearance"],
    },
    "living_tv_window_axis": {
        "anchor_groups": [
            {"subject": "TV wall", "roles": ["tv"]},
            {"subject": "CAD-authentic daylight opening", "opening_kinds": ["window"]},
        ],
        "must_validate": ["same_floor_elevation", "camera_clearance"],
    },
    "kitchen_cookline_elevation": {
        "anchor_groups": [
            {"subject": "hob", "roles": ["hob"]},
            {"subject": "hood", "roles": ["hood"], "derived_from_roles": ["hob"]},
            {"subject": "worktop", "roles": ["kitchen_run"]},
            {"subject": "refrigerator identity", "roles": ["fridge"],
             "optional_visibility": True},
        ],
        "must_validate": ["cookline_order_preserved", "camera_clearance", "door_clearance"],
    },
    "master_bed_headwall": {
        "anchor_groups": [{"subject": "bed axis", "roles": ["bed"]}],
        "must_validate": ["bed_axis_preserved", "cad_circulation_clearance", "same_floor_elevation"],
    },
    "secondary_bed_soft_headwall": {
        "anchor_groups": [
            {"subject": "bed", "roles": ["bed"]},
            {"subject": "the only CAD window", "opening_kinds": ["window"], "exact_count": 1,
             "optional_visibility": True},
        ],
        "must_validate": ["cad_circulation_clearance", "same_floor_elevation"],
    },
    "secondary_bed_dark_headwall": {
        "anchor_groups": [{"subject": "bed axis", "roles": ["bed"]}],
        "must_validate": ["bed_axis_preserved", "same_floor_elevation", "no_false_threshold"],
    },
    "master_bath_three_fixture": {
        "anchor_groups": [
            {"subject": "one toilet", "roles": ["toilet"], "exact_count": 1},
            {"subject": "one shower", "roles": ["shower_zone"], "exact_count": 1},
            {"subject": "one basin", "roles": ["basin"], "exact_count": 1},
        ],
        "must_validate": ["fixture_count_preserved", "camera_clearance", "door_clearance"],
    },
    "secondary_bath_toilet_shower": {
        "anchor_groups": [
            {"subject": "toilet", "roles": ["toilet"]},
            {"subject": "shower", "roles": ["shower_zone"]},
            {"subject": "CAD window", "opening_kinds": ["window"],
             "optional_if_cad_absent": True, "optional_visibility": True},
        ],
        "must_validate": ["fixture_count_preserved", "camera_clearance", "door_clearance"],
    },
    "dry_vanity_front": {
        "anchor_groups": [
            {"subject": "exactly one basin", "roles": ["basin"], "exact_count": 1},
            {"subject": "exactly one faucet", "roles": ["faucet"], "exact_count": 1,
             "derived_from_roles": ["basin"]},
            {"subject": "CAD-authentic mirror relationship", "roles": ["mirror"],
             "derived_from_roles": ["basin"]},
        ],
        "must_validate": ["fixture_count_preserved", "mirror_not_opening", "camera_clearance"],
    },
}


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return round(value, 8)
    return value


def reference_proposal_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        _canonical(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def reference_model_facts_hash(model: dict) -> str:
    return reference_proposal_hash({
        "model_id": model.get("model_id"), "cad_facts_hash": model.get("cad_facts_hash") or "",
        "rooms": model.get("rooms") or [], "openings": model.get("openings") or [],
        "fixed_objects": model.get("fixed_objects") or [],
    })


def split_reference_contract(contract: dict) -> dict:
    """Attach explicit pixel and geometry requirements without losing wording."""
    result = copy.deepcopy(contract or {})
    for slot in result.get("slots") or []:
        slot_id = str(slot.get("slot_id") or "")
        requirements = copy.deepcopy(_SLOT_REQUIREMENTS.get(slot_id) or {})
        slot["must_show_text"] = [str(value) for value in slot.get("must_show") or []]
        slot["anchor_groups"] = requirements.get("anchor_groups") or []
        slot["must_validate"] = requirements.get("must_validate") or []
        slot["subject_safe_frame_overrides"] = {
            **copy.deepcopy(_SLOT_SUBJECT_SAFE_FRAME_OVERRIDES.get(slot_id) or {}),
            **copy.deepcopy(slot.get("subject_safe_frame_overrides") or {}),
        }
    return result


def _profile(room: dict) -> str:
    return str(room.get("reference_room_profile") or room.get("semantic_profile") or room.get("room_type") or "")


def bind_reference_slots_to_rooms(model: dict, contract: dict) -> dict:
    """Bind all slots deterministically while preserving independent slot pools."""
    rooms_by_profile: dict[str, list[dict]] = defaultdict(list)
    for room in model.get("rooms") or []:
        if room.get("selected", True):
            rooms_by_profile[_profile(room)].append(room)
    for rooms in rooms_by_profile.values():
        rooms.sort(key=lambda row: str(row.get("id") or ""))

    bindings: dict[str, str] = {}
    blocked: list[dict] = []
    slots = contract.get("slots") or []
    secondary_slots = [slot for slot in slots if slot.get("room_profile") == "bedroom_secondary"]
    secondary_rooms = rooms_by_profile.get("bedroom_secondary") or []
    if secondary_slots:
        if len(secondary_rooms) == 1:
            for slot in secondary_slots:
                bindings[str(slot["slot_id"])] = str(secondary_rooms[0]["id"])
        elif len(secondary_rooms) == 2:
            # Stable one-slot-per-room assignment.  Coordinates still come from
            # each room's own bed/window anchors, never the public panorama.
            for slot, room in zip(sorted(secondary_slots, key=lambda row: str(row["slot_id"])), secondary_rooms):
                bindings[str(slot["slot_id"])] = str(room["id"])
        else:
            blocked.append({
                "code": "reference_secondary_room_binding_ambiguous",
                "slot_ids": [str(row.get("slot_id") or "") for row in secondary_slots],
                "room_ids": [str(row.get("id") or "") for row in secondary_rooms],
                "message": "次卧必须恰有 1 或 2 个可审计 CAD 房间；更多房间不能凭公开参考图猜坐标映射。",
            })

    bathroom_slot_ids = {
        "master_bath_three_fixture", "secondary_bath_toilet_shower", "dry_vanity_front",
    }
    bathroom_slots = [slot for slot in slots if str(slot.get("slot_id") or "") in bathroom_slot_ids]
    bathroom_rooms = [room for room in model.get("rooms") or []
                      if room.get("selected", True)
                      and _profile(room) in {"bathroom", "bathroom_master", "bathroom_secondary", "dry_vanity"}]
    if bathroom_slots and len(bathroom_rooms) == 1:
        # The audited JustEasy work uses three viewpoints of one wet/dry suite;
        # scene names do not prove three separate CAD rooms.
        for slot in bathroom_slots:
            bindings[str(slot["slot_id"])] = str(bathroom_rooms[0]["id"])

    for slot in slots:
        slot_id = str(slot.get("slot_id") or "")
        if slot_id in bindings or slot.get("room_profile") == "bedroom_secondary":
            continue
        matches = rooms_by_profile.get(str(slot.get("room_profile") or "")) or []
        if len(matches) != 1:
            blocked.append({
                "code": "reference_room_binding_not_unique", "slot_id": slot_id,
                "room_profile": slot.get("room_profile"),
                "room_ids": [str(row.get("id") or "") for row in matches],
                "message": "reference slot 必须唯一绑定 CAD 房间。",
            })
            continue
        bindings[slot_id] = str(matches[0]["id"])
    return {"status": "ready" if not blocked and len(bindings) == len(slots) else "blocked",
            "bindings": bindings, "hard_errors": blocked}


def _opening_center(opening: dict, walls: dict[str, dict]) -> tuple[float, float] | None:
    wall = walls.get(str(opening.get("wall_id") or ""))
    if not wall:
        return None
    first, second = wall.get("start") or {}, wall.get("end") or {}
    x1, z1 = float(first.get("x") or 0), float(first.get("z") or 0)
    x2, z2 = float(second.get("x") or 0), float(second.get("z") or 0)
    length = math.hypot(x2 - x1, z2 - z1)
    if length <= 1e-9:
        return None
    distance = float(opening.get("offset_m") or 0) + float(opening.get("width_m") or 0) / 2
    t = max(0.0, min(1.0, distance / length))
    return x1 + (x2 - x1) * t, z1 + (z2 - z1) * t


def _room_openings(model: dict, room: dict) -> list[dict]:
    try:
        from shapely.geometry import Point, Polygon  # type: ignore
    except Exception:
        return []
    polygon = Polygon([(float(row["x"]), float(row["z"])) for row in room.get("polygon") or []])
    walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    rows = []
    for opening in model.get("openings") or []:
        center = _opening_center(opening, walls)
        if center and polygon.buffer(.08).covers(Point(*center)):
            rows.append({**opening, "anchor_position": center})
    return rows


def _living_open_semantic_boundaries(model: dict, room: dict) -> list[dict]:
    """Derive visible circulation anchors from overlapping open-plan zones.

    Gemini room polygons are semantic ownership zones.  In an open-plan CAD
    drawing the living and dining/kitchen zones may overlap without any wall or
    authored opening between them.  Treating "corridor" as "some door" then
    rejects the exact open-plan axis we want.  This helper records the overlap
    as a *reference-only visibility probe*; it never inserts an opening or
    changes CAD geometry.
    """
    try:
        from shapely.geometry import LineString, Point, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:
        return []
    if _profile(room) != "living_room":
        return []
    try:
        living = Polygon([(float(row["x"]), float(row["z"]))
                          for row in room.get("polygon") or []])
    except Exception:
        return []
    wall_lines = []
    for wall in model.get("walls") or []:
        first, second = wall.get("start") or {}, wall.get("end") or {}
        wall_lines.append(LineString([
            (float(first.get("x") or 0), float(first.get("z") or 0)),
            (float(second.get("x") or 0), float(second.get("z") or 0)),
        ]))
    wall_union = unary_union(wall_lines) if wall_lines else None
    rows = []
    adjacent_profiles = {"kitchen", "foyer", "entryway", "dining_room"}
    for adjacent in model.get("rooms") or []:
        adjacent_id = str(adjacent.get("id") or "")
        if adjacent_id == str(room.get("id") or "") or _profile(adjacent) not in adjacent_profiles:
            continue
        try:
            other = Polygon([(float(row["x"]), float(row["z"]))
                             for row in adjacent.get("polygon") or []])
            overlap = living.intersection(other)
        except Exception:
            continue
        overlap_area = float(getattr(overlap, "area", 0.0))
        overlap_length = float(getattr(overlap, "length", 0.0))
        if overlap.is_empty or (overlap_area < .08 and overlap_length < .6):
            continue
        # Prefer the point with the greatest clearance from CAD wall strokes.
        candidates = []
        if overlap_area >= .08:
            min_x, min_z, max_x, max_z = overlap.bounds
            for xi in range(1, 6):
                for zi in range(1, 6):
                    point = Point(min_x + (max_x - min_x) * xi / 6,
                                  min_z + (max_z - min_z) * zi / 6)
                    if overlap.covers(point):
                        clearance = float(point.distance(wall_union)) if wall_union is not None else 999.0
                        candidates.append((clearance, point))
        else:
            components = list(overlap.geoms) if hasattr(overlap, "geoms") else [overlap]
            for component in components:
                if float(getattr(component, "length", 0.0)) < .3:
                    continue
                for index in range(1, 6):
                    point = component.interpolate(index / 6, normalized=True)
                    clearance = float(point.distance(wall_union)) if wall_union is not None else 999.0
                    candidates.append((clearance, point))
        representative = overlap.representative_point()
        candidates.append((float(representative.distance(wall_union))
                           if wall_union is not None else 999.0, representative))
        clearance, point = max(candidates, key=lambda row: (row[0], row[1].x, row[1].y))
        if clearance < .16:
            continue
        rows.append({
            "anchor_id": f"cad_open_boundary_{room.get('id')}_{adjacent_id}",
            "anchor_kind": "cad_open_semantic_boundary",
            "role": "open_connection",
            "position": {"x": round(float(point.x), 5), "z": round(float(point.y), 5)},
            "adjacent_room_id": adjacent_id,
            "derivation": "audited_cad_wall_free_semantic_overlap_v1",
            "clearance_to_cad_wall_m": round(clearance, 5),
        })
    # Also derive the narrow circulation neck contained by the living polygon
    # itself.  The real DWG has a hall-shaped lower branch, while the adjacent
    # kitchen overlap above describes a dining boundary and caused the camera
    # to stand behind the refrigerator.  A horizontal cross-section inside the
    # lower third finds that wall-free neck without inventing geometry.
    neck_rows = []
    try:
        inset = living.buffer(-.12, join_style=2)
        min_x, min_z, max_x, max_z = living.bounds
        for fraction in (.10, .16, .22, .28, .34):
            z = min_z + (max_z - min_z) * fraction
            section = inset.intersection(LineString([(min_x - 1, z), (max_x + 1, z)]))
            components = list(section.geoms) if hasattr(section, "geoms") else [section]
            viable = [part for part in components
                      if .65 <= float(getattr(part, "length", 0.0)) <= (max_x - min_x) * .45]
            if not viable:
                continue
            part = max(viable, key=lambda value: float(value.length))
            point = part.interpolate(.5, normalized=True)
            clearance = float(point.distance(wall_union)) if wall_union is not None else 999.0
            if clearance < .16:
                continue
            neck_rows.append({
                "anchor_id": f"cad_living_corridor_axis_{room.get('id')}",
                "anchor_kind": "cad_open_semantic_boundary",
                "role": "open_connection",
                "position": {"x": round(float(point.x), 5), "z": round(float(point.y), 5)},
                "derivation": "audited_cad_living_neck_axis_v1",
                "clearance_to_cad_wall_m": round(clearance, 5),
            })
            break
    except Exception:
        neck_rows = []
    return neck_rows or sorted(rows, key=lambda row: str(row.get("anchor_id") or ""))


def resolve_slot_anchor_groups(model: dict, room: dict, slot: dict) -> dict:
    objects = [row for row in model.get("fixed_objects") or []
               if str(row.get("room_id") or "") == str(room.get("id") or "")]
    if str(slot.get("slot_id") or "") == "living_openplan_axis":
        try:
            from shapely.geometry import Point, Polygon  # type: ignore
            room_shape = Polygon([(float(row["x"]), float(row["z"]))
                                  for row in room.get("polygon") or []])
            for row in model.get("fixed_objects") or []:
                if row in objects or str(row.get("semantic_role") or row.get("kind") or "") != "dining_table":
                    continue
                position = row.get("position") or {}
                if room_shape.distance(Point(float(position.get("x") or 0),
                                             float(position.get("z") or 0))) <= 2.0:
                    objects.append(row)
        except Exception:
            pass
        # The public reference asks for the open-plan corridor axis.  A CAD
        # plan can express that axis as a wall-free overlap between semantic
        # living and kitchen/dining zones rather than as a Door entity.
        open_boundaries = _living_open_semantic_boundaries(model, room)
    else:
        open_boundaries = []
    openings = _room_openings(model, room)
    if str(slot.get("slot_id") or "") == "living_openplan_axis":
        # The audited living semantic zone includes the entry corridor axis,
        # while the actual door center can sit just beyond its coarse AI mask.
        # Admit only nearby, locally anchored CAD openings; their coordinates
        # and wall IDs remain CAD facts and the LOS gate still applies.
        try:
            from shapely.geometry import Point, Polygon  # type: ignore
            room_shape = Polygon([(float(row["x"]), float(row["z"]))
                                  for row in room.get("polygon") or []])
            walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
            present = {str(row.get("id") or "") for row in openings}
            for row in model.get("openings") or []:
                center = _opening_center(row, walls)
                if (not center or str(row.get("id") or "") in present
                        or row.get("reference_anchor_ready") is False
                        or room_shape.distance(Point(*center)) > 2.0):
                    continue
                openings.append({**row, "anchor_position": center,
                                 "anchor_scope": "nearby_cad_corridor_opening"})
        except Exception:
            pass
    groups, errors, omitted = [], [], []
    for requirement in slot.get("anchor_groups") or []:
        alternatives = []
        roles = {str(value) for value in requirement.get("roles") or []}
        kinds = {str(value) for value in requirement.get("opening_kinds") or []}
        for row in objects:
            role = str(row.get("semantic_role") or row.get("kind") or "")
            if role in roles and row.get("reference_anchor_ready") is not False:
                position = row.get("position") or {}
                alternatives.append({
                    "anchor_id": str(row.get("id") or ""), "anchor_kind": "fixed_object",
                    "role": role, "position": {"x": float(position.get("x") or 0),
                                                "z": float(position.get("z") or 0)},
                })
        for row in openings:
            if str(row.get("kind") or "") in kinds and row.get("reference_anchor_ready") is not False:
                center = row["anchor_position"]
                alternatives.append({
                    "anchor_id": str(row.get("id") or ""), "anchor_kind": "opening",
                    "role": str(row.get("kind") or ""),
                    "position": {"x": center[0], "z": center[1]},
                    "wall_id": str(row.get("wall_id") or ""),
                    "opening": copy.deepcopy(row),
                })
        if (str(requirement.get("subject") or "") == "CAD-authentic corridor"
                and open_boundaries):
            alternatives = copy.deepcopy(open_boundaries)
        elif kinds.intersection({"door", "open_connection"}):
            alternatives.extend(copy.deepcopy(open_boundaries))
        if not alternatives and requirement.get("derived_from_roles"):
            parent_roles = {str(value) for value in requirement.get("derived_from_roles") or []}
            for row in objects:
                parent_role = str(row.get("semantic_role") or row.get("kind") or "")
                if parent_role not in parent_roles or row.get("reference_anchor_ready") is False:
                    continue
                position = row.get("position") or {}
                derived_role = next(iter(sorted(roles)), parent_role)
                alternatives.append({
                    "anchor_id": f"{row.get('id') or ''}::derived::{derived_role}",
                    "source_anchor_id": str(row.get("id") or ""),
                    "anchor_kind": "derived_fixture_component",
                    "role": derived_role,
                    "derived_from_role": parent_role,
                    "position": {"x": float(position.get("x") or 0),
                                 "z": float(position.get("z") or 0)},
                })
        exact_count = requirement.get("exact_count")
        if not alternatives and requirement.get("optional_if_cad_absent"):
            omitted.append({"subject": str(requirement.get("subject") or ""),
                            "reason": "cad_anchor_absent_do_not_invent"})
            continue
        if exact_count is not None and len(alternatives) != int(exact_count):
            errors.append({"code": "reference_anchor_exact_count_failed",
                           "slot_id": slot.get("slot_id"), "subject": requirement.get("subject"),
                           "expected": int(exact_count), "actual": len(alternatives)})
        elif not alternatives:
            errors.append({"code": "reference_anchor_group_missing", "slot_id": slot.get("slot_id"),
                           "subject": requirement.get("subject"), "roles": sorted(roles),
                           "opening_kinds": sorted(kinds)})
        groups.append({"subject": str(requirement.get("subject") or ""),
                       "alternatives": alternatives, "exact_count": exact_count,
                       "required": not bool(requirement.get("optional_visibility"))})
    return {"status": "ready" if not errors else "blocked", "anchor_groups": groups,
            "omitted_subjects": omitted, "hard_errors": errors}


def _rotated_footprint(row: dict, clearance: float = 0.0):
    from shapely.affinity import rotate  # type: ignore
    from shapely.geometry import box  # type: ignore

    position, size = row.get("position") or {}, row.get("size") or {}
    width, depth = float(size.get("x") or 0), float(size.get("z") or 0)
    if width <= 0 or depth <= 0:
        return None
    cx, cz = float(position.get("x") or 0), float(position.get("z") or 0)
    footprint = box(cx - width / 2, cz - depth / 2, cx + width / 2, cz + depth / 2)
    footprint = rotate(footprint, float(row.get("rotation_y_deg") or 0), origin=(cx, cz), use_radians=False)
    return footprint.buffer(max(0.0, clearance), join_style=2)


def build_reference_walkable_region(model: dict, room: dict) -> dict:
    try:
        from shapely.geometry import LineString, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception as ex:
        return {"status": "blocked", "hard_errors": [{"code": "reference_geometry_dependency_missing",
                                                         "message": str(ex)}]}
    polygon = Polygon([(float(row["x"]), float(row["z"])) for row in room.get("polygon") or []])
    if not polygon.is_valid or polygon.area <= .25:
        return {"status": "blocked", "hard_errors": [{"code": "reference_room_polygon_invalid",
                                                         "room_id": room.get("id")}]}
    inset_used = .35
    walkable = polygon.buffer(-inset_used, join_style=2)
    if walkable.is_empty or walkable.area < .25:
        inset_used = .18
        walkable = polygon.buffer(-inset_used, join_style=2)
    if walkable.is_empty or walkable.area < .12:
        return {"status": "blocked", "hard_errors": [{"code": "reference_navmesh_empty",
                                                         "room_id": room.get("id")}]}
    obstacles = []
    for row in model.get("fixed_objects") or []:
        if str(row.get("room_id") or "") != str(room.get("id") or ""):
            continue
        footprint = _rotated_footprint(row, float(row.get("clearance_m") or .25))
        if footprint is not None:
            obstacles.append(footprint)
    walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    for opening in _room_openings(model, room):
        if opening.get("kind") != "door":
            continue
        wall = walls.get(str(opening.get("wall_id") or ""))
        if not wall:
            continue
        first, second = wall.get("start") or {}, wall.get("end") or {}
        x1, z1 = float(first.get("x") or 0), float(first.get("z") or 0)
        x2, z2 = float(second.get("x") or 0), float(second.get("z") or 0)
        length = max(math.hypot(x2 - x1, z2 - z1), 1e-9)
        start_d = float(opening.get("offset_m") or 0)
        end_d = start_d + float(opening.get("width_m") or 0)
        a = (x1 + (x2 - x1) * start_d / length, z1 + (z2 - z1) * start_d / length)
        b = (x1 + (x2 - x1) * end_d / length, z1 + (z2 - z1) * end_d / length)
        obstacles.append(LineString([a, b]).buffer(.55, cap_style=2))
    if obstacles:
        walkable = walkable.difference(unary_union(obstacles))
    if walkable.is_empty or walkable.area < .08:
        return {"status": "blocked", "hard_errors": [{"code": "reference_navmesh_occluded",
                                                         "room_id": room.get("id")}],
                "inset_m": inset_used}
    return {"status": "ready", "geometry": walkable, "inset_m": inset_used,
            "obstacle_count": len(obstacles), "hard_errors": []}


def _sample_points(geometry: Any, *, step: float = .28, limit: int = 48) -> list[tuple[float, float]]:
    from shapely.geometry import Point  # type: ignore

    min_x, min_z, max_x, max_z = geometry.bounds
    points = []
    x = min_x
    while x <= max_x + 1e-9:
        z = min_z
        while z <= max_z + 1e-9:
            point = Point(x, z)
            if geometry.covers(point):
                points.append((float(x), float(z)))
            z += step
        x += step
    centroid = geometry.representative_point()
    points.append((float(centroid.x), float(centroid.y)))
    center = (float(centroid.x), float(centroid.y))
    points = sorted(set((round(x, 5), round(z, 5)) for x, z in points),
                    key=lambda row: (-math.dist(row, center), row[0], row[1]))
    if len(points) <= limit:
        return points
    # Preserve relative regions across the room instead of taking one centroid cluster.
    stride = max(1, len(points) // limit)
    return points[::stride][:limit]


def _focal_samples(slot: dict) -> list[int]:
    bounds = slot.get("focal_length_mm") or {}
    minimum, maximum = int(bounds.get("min") or 24), int(bounds.get("max") or 35)
    if minimum <= 24 and maximum <= 28:
        values = [24, 26, 28]
    elif minimum <= 28 < maximum:
        values = [28, 32, 35]
    else:
        values = [32, 34, 35]
    return [value for value in values if minimum <= value <= maximum and value != 20]


def _angle_delta(first: float, second: float) -> float:
    return (first - second + 180.0) % 360.0 - 180.0


def _opening_segment(opening: dict, walls: dict[str, dict]):
    from shapely.geometry import LineString  # type: ignore

    wall = walls.get(str(opening.get("wall_id") or ""))
    if not wall:
        return None
    first, second = wall.get("start") or {}, wall.get("end") or {}
    x1, z1 = float(first.get("x") or 0), float(first.get("z") or 0)
    x2, z2 = float(second.get("x") or 0), float(second.get("z") or 0)
    length = math.hypot(x2 - x1, z2 - z1)
    if length <= 1e-9:
        return None
    start = max(0.0, min(length, float(opening.get("offset_m") or 0)))
    end = max(start, min(length, start + float(opening.get("width_m") or 0)))
    ux, uz = (x2 - x1) / length, (z2 - z1) / length
    return LineString([(x1 + ux * start, z1 + uz * start),
                       (x1 + ux * end, z1 + uz * end)])


def _intersection_uses_opening_assembly(intersection: Any, wall: dict, opening: dict,
                                        walls: dict[str, dict]) -> bool:
    """Treat parallel CAD wall faces as one physical wall aperture.

    Architectural DWGs commonly draw both faces of a wall (and finish layers)
    as independent LINE entities.  An opening is authored on one face.  The
    render/LOS aperture may propagate only to nearby parallel faces and only
    inside that opening's along-wall span; deeper unrelated walls stay solid.
    """
    source = walls.get(str(opening.get("wall_id") or ""))
    segment = _opening_segment(opening, walls)
    if not source or segment is None or intersection.is_empty:
        return False
    source_start, source_end = source.get("start") or {}, source.get("end") or {}
    wall_start, wall_end = wall.get("start") or {}, wall.get("end") or {}
    sdx = float(source_end.get("x") or 0) - float(source_start.get("x") or 0)
    sdz = float(source_end.get("z") or 0) - float(source_start.get("z") or 0)
    wdx = float(wall_end.get("x") or 0) - float(wall_start.get("x") or 0)
    wdz = float(wall_end.get("z") or 0) - float(wall_start.get("z") or 0)
    sl, wl = math.hypot(sdx, sdz), math.hypot(wdx, wdz)
    if sl <= 1e-9 or wl <= 1e-9:
        return False
    parallel = abs((sdx * wdx + sdz * wdz) / (sl * wl)) >= math.cos(math.radians(6))
    return bool(parallel and intersection.distance(segment) <= .40)


def _reference_visibility_context(model: dict) -> dict:
    """Pre-build immutable Shapely primitives shared by a proposal scan.

    A proposal evaluates the same point/anchor line of sight for dozens of
    yaw/focal combinations.  Reconstructing every wall and fixed-object
    geometry in that inner loop made a nine-slot proposal take minutes even
    though the CAD facts never changed during the scan.
    """
    from shapely.geometry import LineString  # type: ignore

    walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    wall_lines = []
    for wall in walls.values():
        first, second = wall.get("start") or {}, wall.get("end") or {}
        wall_lines.append((wall, LineString([
            (float(first.get("x") or 0), float(first.get("z") or 0)),
            (float(second.get("x") or 0), float(second.get("z") or 0)),
        ])))
    opening_segments = {
        str(row.get("id") or ""): _opening_segment(row, walls)
        for row in model.get("openings") or []
    }
    fixed_objects = []
    for row in model.get("fixed_objects") or []:
        position, size = row.get("position") or {}, row.get("size") or {}
        blocker_top = float(position.get("y") or 0) + float(size.get("y") or 0)
        fixed_objects.append((row, blocker_top, _rotated_footprint(row, 0)))
    return {
        "walls": walls,
        "wall_lines": wall_lines,
        "opening_segments": opening_segments,
        "fixed_objects": fixed_objects,
    }


def _anchor_visible(model: dict, origin: tuple[float, float], anchor: dict, yaw: float,
                    focal: float, aspect_ratio: str,
                    passage_opening: dict | None = None, *,
                    visibility_cache: dict | None = None,
                    visibility_context: dict | None = None) -> tuple[bool, str, float]:
    from shapely.geometry import LineString, Point  # type: ignore

    target = anchor.get("position") or {}
    point = (float(target.get("x") or 0), float(target.get("z") or 0))
    distance = math.dist(origin, point)
    if distance <= .12:
        return False, "anchor_too_close", 0.0
    bearing = math.degrees(math.atan2(point[0] - origin[0], point[1] - origin[1]))
    horizontal_fov = math.degrees(2 * math.atan(36.0 / (2 * float(focal))))
    margin = horizontal_fov / 2 - abs(_angle_delta(bearing, yaw))
    if margin < 0:
        return False, "outside_horizontal_fov", margin
    anchor_opening = anchor.get("opening") if isinstance(anchor.get("opening"), dict) else {}
    cache_key = (
        round(float(origin[0]), 5), round(float(origin[1]), 5),
        str(anchor.get("anchor_id") or ""), str(anchor.get("source_anchor_id") or ""),
        str(anchor.get("role") or ""), round(point[0], 5), round(point[1], 5),
        str(anchor_opening.get("id") or ""),
        str((passage_opening or {}).get("id") or ""),
    )
    if visibility_cache is not None and cache_key in visibility_cache:
        cached_pass, cached_reason = visibility_cache[cache_key]
        return bool(cached_pass), str(cached_reason), margin
    sight = LineString([origin, point])
    # A target opening lies on its wall; ignore intersections at the final 8cm.
    shortened = LineString([origin, (
        point[0] + (origin[0] - point[0]) * min(.08 / distance, .5),
        point[1] + (origin[1] - point[1]) * min(.08 / distance, .5),
    )])
    context = visibility_context or _reference_visibility_context(model)
    walls = context["walls"]
    allowed_openings = [row for row in (anchor.get("opening"), passage_opening) if isinstance(row, dict)]
    allowed_ids = {str(row.get("id") or "") for row in allowed_openings}
    for opening in model.get("openings") or []:
        if (str(opening.get("id") or "") in allowed_ids
                or str(opening.get("kind") or "") not in {"door", "open_connection"}
                or opening.get("review_status") == "rejected"
                or opening.get("reference_anchor_ready") is False):
            continue
        segment = context["opening_segments"].get(str(opening.get("id") or ""))
        if segment is not None and sight.distance(segment) <= .08:
            allowed_openings.append(opening)
            allowed_ids.add(str(opening.get("id") or ""))
    for wall, wall_line in context["wall_lines"]:
        intersection = shortened.intersection(wall_line)
        if not intersection.is_empty and not intersection.equals(Point(origin)):
            if any(_intersection_uses_opening_assembly(intersection, wall, opening, walls)
                   for opening in allowed_openings):
                continue
            if visibility_cache is not None:
                visibility_cache[cache_key] = (False, "wall_occlusion")
            return False, "wall_occlusion", margin
    for row, blocker_top, footprint in context["fixed_objects"]:
        if str(row.get("id") or "") == str(anchor.get("anchor_id") or ""):
            continue
        target_role = str(anchor.get("role") or "")
        blocker_role = str(row.get("semantic_role") or row.get("kind") or "")
        fixture_stack = {
            "hob", "hood", "sink", "faucet", "kitchen_run",
            "basin", "mirror", "toilet", "shower_zone",
        }
        if target_role in fixture_stack and blocker_role in fixture_stack:
            continue
        # Low furnishings belong in the foreground composition; a dining table,
        # sofa or sanitary fixture below eye level must not become an infinite
        # full-height 2D occluder.  Tall cabinets/wardrobes still block LOS.
        if blocker_top <= 1.15:
            continue
        if footprint is None:
            continue
        intersection = sight.intersection(footprint)
        if not intersection.is_empty and intersection.distance(Point(origin)) < max(.01, distance - .08):
            if visibility_cache is not None:
                visibility_cache[cache_key] = (False, "object_occlusion")
            return False, "object_occlusion", margin
    if visibility_cache is not None:
        visibility_cache[cache_key] = (True, "visible")
    return True, "visible", margin


def _yaw_evaluation(model: dict, point: tuple[float, float], groups: list[dict], yaw: float,
                    focal: float, aspect_ratio: str,
                    passage_opening: dict | None = None, *,
                    visibility_cache: dict | None = None,
                    visibility_context: dict | None = None) -> dict:
    selected, omitted_optional, reasons, margins = [], [], defaultdict(int), []
    for group in groups:
        visible = []
        for anchor in group.get("alternatives") or []:
            passed, reason, margin = _anchor_visible(
                model, point, anchor, yaw, focal, aspect_ratio, passage_opening,
                visibility_cache=visibility_cache, visibility_context=visibility_context)
            if passed:
                visible.append((margin, anchor))
            else:
                reasons[reason] += 1
        if not visible:
            if group.get("required") is False:
                omitted_optional.append({"subject": str(group.get("subject") or ""),
                                         "reason": "cad_anchor_not_visible_do_not_invent"})
                continue
            reasons["anchor_group_not_visible"] += 1
            return {"pass": False, "reasons": dict(reasons), "subjects": []}
        margin, anchor = max(visible, key=lambda row: (row[0], str(row[1].get("anchor_id") or "")))
        margins.append(margin)
        selected.append({"subject": group.get("subject"), "anchor_id": anchor.get("anchor_id"),
                         "anchor_kind": anchor.get("anchor_kind"), "role": anchor.get("role"),
                         **({"source_anchor_id": anchor.get("source_anchor_id")}
                            if anchor.get("source_anchor_id") else {}),
                         **({"position": copy.deepcopy(anchor.get("position"))}
                            if anchor.get("anchor_kind") == "derived_fixture_component" else {}),
                         **({"position": copy.deepcopy(anchor.get("position"))}
                            if anchor.get("anchor_kind") == "cad_open_semantic_boundary" else {})})
    return {"pass": True, "reasons": dict(reasons), "subjects": selected,
            "omitted_optional_subjects": omitted_optional,
            "score": min(margins or [0]) + sum(margins) * .1 + len(selected) * .35}


def _reference_anchor_box(model: dict, subject: dict):
    anchor_id = str(subject.get("anchor_id") or "")
    source_anchor_id = str(subject.get("source_anchor_id") or "")
    for row in model.get("fixed_objects") or []:
        if str(row.get("id") or "") not in {anchor_id, source_anchor_id}:
            continue
        position, size = row.get("position") or {}, row.get("size") or {}
        sx, sy, sz = (max(.02, float(size.get(axis) or 0)) for axis in ("x", "y", "z"))
        derived_role = str(subject.get("role") or "") if subject.get("anchor_kind") == "derived_fixture_component" else ""
        if derived_role == "hood":
            sy, sz = .45, max(.25, sz * .65)
            bottom_y = 1.75
        elif derived_role == "faucet":
            sx, sy, sz = max(.12, sx * .18), .28, max(.08, sz * .18)
            bottom_y = float(position.get("y") or 0) + float(size.get("y") or 0)
        elif derived_role == "mirror":
            sx, sy, sz = max(.35, sx * .9), .75, .035
            bottom_y = 1.15
        elif (not derived_role
              and str(row.get("semantic_role") or row.get("kind") or "") in {"hob", "sink"}):
            bottom_y = .9 if float(position.get("y") or 0) <= .05 else float(position.get("y") or 0)
        else:
            bottom_y = float(position.get("y") or 0)
        cx, cy, cz = (float(position.get("x") or 0),
                      bottom_y + sy / 2,
                      float(position.get("z") or 0))
        angle = math.radians(float(row.get("rotation_y_deg") or 0))
        corners = []
        for lx in (-sx / 2, sx / 2):
            for ly in (-sy / 2, sy / 2):
                for lz in (-sz / 2, sz / 2):
                    corners.append((cx + math.cos(angle) * lx + math.sin(angle) * lz,
                                    cy + ly,
                                    cz - math.sin(angle) * lx + math.cos(angle) * lz))
        return corners
    walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    for opening in model.get("openings") or []:
        if str(opening.get("id") or "") != anchor_id:
            continue
        wall = walls.get(str(opening.get("wall_id") or ""))
        center = _opening_center(opening, walls)
        if not wall or not center:
            return []
        first, second = wall.get("start") or {}, wall.get("end") or {}
        dx = float(second.get("x") or 0) - float(first.get("x") or 0)
        dz = float(second.get("z") or 0) - float(first.get("z") or 0)
        length = max(1e-9, math.hypot(dx, dz))
        tx, tz = dx / length, dz / length
        half_width = max(.02, float(opening.get("width_m") or .8)) / 2
        bottom = float(opening.get("sill_height_m") or 0)
        top = bottom + max(.05, float(opening.get("height_m") or 2.1))
        return [(center[0] + tx * side * half_width, y, center[1] + tz * side * half_width)
                for side in (-1, 1) for y in (bottom, top)]
    position = subject.get("position") or {}
    if subject.get("anchor_kind") == "cad_open_semantic_boundary" and position:
        cx, cz = float(position.get("x") or 0), float(position.get("z") or 0)
        return [(cx + x, y, cz + z)
                for x in (-.21, .21) for y in (0.0, 1.8) for z in (-.018, .018)]
    return []


def _subjects_fit_safe_frame(model: dict, point: tuple[float, float], yaw: float, focal: float,
                             pitch_deg: float, aspect_ratio: str, subjects: list[dict], safe_frame: dict):
    ratio = {"4:3": 4 / 3, "16:9": 16 / 9, "3:4": 3 / 4, "9:16": 9 / 16}.get(aspect_ratio, 4 / 3)
    vertical_tan = 12.0 / max(1.0, float(focal))
    horizontal_tan = vertical_tan * ratio
    radians = math.radians(yaw)
    pitch = math.radians(pitch_deg)
    sin_yaw, cos_yaw = math.sin(radians), math.cos(radians)
    sin_pitch, cos_pitch = math.sin(pitch), math.cos(pitch)
    forward = (sin_yaw * cos_pitch, sin_pitch, cos_yaw * cos_pitch)
    right = (-cos_yaw, 0.0, sin_yaw)
    up = (-sin_yaw * sin_pitch, cos_pitch, -cos_yaw * sin_pitch)
    bounds = []
    for subject in subjects:
        projected = []
        for x, y, z in _reference_anchor_box(model, subject):
            dx, dy, dz = x - point[0], y - EYE_HEIGHT_M, z - point[1]
            depth = dx * forward[0] + dy * forward[1] + dz * forward[2]
            if depth <= .05:
                return False, []
            camera_x = dx * right[0] + dy * right[1] + dz * right[2]
            camera_y = dx * up[0] + dy * up[1] + dz * up[2]
            screen_x = .5 + camera_x / (2 * depth * horizontal_tan)
            screen_y = .5 - camera_y / (2 * depth * vertical_tan)
            projected.append((screen_x, screen_y))
        if not projected:
            return False, []
        row = {
            "subject": str(subject.get("subject") or ""),
            "anchor_id": str(subject.get("anchor_id") or ""),
            "x_min": min(value[0] for value in projected),
            "x_max": max(value[0] for value in projected),
            "y_min": min(value[1] for value in projected),
            "y_max": max(value[1] for value in projected),
        }
        subject_name = str(subject.get("subject") or "")
        override = ((safe_frame.get("subject_overrides") or {}).get(subject_name) or {})
        x_min_allowed = float(override.get("x_min", safe_frame.get("x_min", .08)))
        x_max_allowed = float(override.get("x_max", safe_frame.get("x_max", .92)))
        y_min_allowed = float(override.get("y_min", safe_frame.get("y_min", .08)))
        y_max_allowed = float(override.get("y_max", safe_frame.get("y_max", .94)))
        if not (row["x_min"] >= x_min_allowed and row["x_max"] <= x_max_allowed
                and row["y_min"] >= y_min_allowed and row["y_max"] <= y_max_allowed):
            return False, []
        bounds.append(row)
    return bool(bounds), bounds


def _camera_origin_clear(model: dict, point: tuple[float, float]) -> bool:
    from shapely.geometry import LineString, Point  # type: ignore

    probe = Point(*point)
    for wall in model.get("walls") or []:
        first, second = wall.get("start") or {}, wall.get("end") or {}
        line = LineString([(float(first.get("x") or 0), float(first.get("z") or 0)),
                           (float(second.get("x") or 0), float(second.get("z") or 0))])
        if probe.distance(line) < .14:
            return False
    for row in model.get("fixed_objects") or []:
        footprint = _rotated_footprint(row, max(.12, float(row.get("clearance_m") or 0)))
        if footprint is not None and footprint.covers(probe):
            return False
    return True


def pano_hotspot_origin_clear(model: dict, point: tuple[float, float]) -> bool:
    """球面热点中心的地面碰撞检查(文档 §7.2 热点安全门禁)。

    与透视参考机位共用同一套墙距/固定物 footprint 规则;热点中心球的半径
    检查由调用方叠加(热点中心 0.18–0.25m 内不得穿墙或家具)。
    """
    return _camera_origin_clear(model, point)


def _reference_point_specs(model: dict, room: dict, walkable: Any) -> list[dict]:
    """Return room, doorway and continuous CAD-adjacent camera origins.

    The AI room polygon is a semantic zone, not always the full circulation
    surface (notably a wet/dry bathroom suite).  Adjacent points are permitted
    only when they are locally collision-free and every required anchor still
    passes the CAD wall LOS gate.  No public panorama coordinate is imported.
    """
    from shapely.geometry import Point, Polygon  # type: ignore

    room_shape = Polygon([(float(row["x"]), float(row["z"])) for row in room.get("polygon") or []])
    specs = [{"point": point, "origin_scope": "inside_room", "passage_opening": None,
              "origin_room_ids": [str(room.get("id") or "")]}
             for point in _sample_points(walkable)]
    walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    all_rooms = []
    for row in model.get("rooms") or []:
        try:
            all_rooms.append((str(row.get("id") or ""), Polygon([
                (float(value["x"]), float(value["z"])) for value in row.get("polygon") or []])))
        except Exception:
            continue
    room_union = None
    if all_rooms:
        from shapely.ops import unary_union  # type: ignore
        room_union = unary_union([shape for _, shape in all_rooms])
    interior_envelope = room_union.convex_hull if room_union is not None else None

    # Door-centred candidates on both sides.  Only the outside/adjacent side is
    # added because room-interior positions are already represented above.
    origin_openings = _room_openings(model, room)
    if _profile(room) == "living_room":
        present = {str(row.get("id") or "") for row in origin_openings}
        for opening in model.get("openings") or []:
            center = _opening_center(opening, walls)
            if (not center or str(opening.get("id") or "") in present
                    or str(opening.get("kind") or "") not in {"door", "open_connection"}
                    or opening.get("review_status") == "rejected"
                    or opening.get("reference_anchor_ready") is False
                    or room_shape.distance(Point(*center)) > 2.0):
                continue
            origin_openings.append({**opening, "anchor_position": center,
                                    "anchor_scope": "nearby_cad_corridor_opening"})
            present.add(str(opening.get("id") or ""))
    for opening in origin_openings:
        if str(opening.get("kind") or "") not in {"door", "open_connection"}:
            continue
        wall = walls.get(str(opening.get("wall_id") or ""))
        center = opening.get("anchor_position")
        if not wall or not center:
            continue
        first, second = wall.get("start") or {}, wall.get("end") or {}
        dx = float(second.get("x") or 0) - float(first.get("x") or 0)
        dz = float(second.get("z") or 0) - float(first.get("z") or 0)
        length = math.hypot(dx, dz)
        if length <= 1e-9:
            continue
        tx, tz, nx, nz = dx / length, dz / length, -dz / length, dx / length
        for distance in (.35, .50, .65, .80, 1.0, 1.4, 1.8, 2.2):
            for tangent in (-.60, -.28, 0.0, .28, .60):
                for sign in (-1, 1):
                    point = (float(center[0]) + tx * tangent + nx * distance * sign,
                             float(center[1]) + tz * tangent + nz * distance * sign)
                    if room_shape.buffer(.03).covers(Point(*point)) or not _camera_origin_clear(model, point):
                        continue
                    origin_rooms = [room_id for room_id, shape in all_rooms
                                    if shape.buffer(.02).covers(Point(*point))]
                    if origin_rooms:
                        specs.append({"point": point, "origin_scope": "adjacent_portal",
                                      "passage_opening": opening,
                                      "portal_opening_id": str(opening.get("id") or ""),
                                      "origin_room_ids": origin_rooms})
                    else:
                        # CAD polygonization can omit a narrow circulation
                        # void.  Such a point is not an adjacent-room portal:
                        # retain the opening only as LOS evidence and persist
                        # the honest semantic-free-space origin scope.
                        specs.append({
                            "point": point,
                            "origin_scope": "cad_semantic_adjacent_free_space",
                            "passage_opening": opening,
                            "reference_passage_opening_id": str(opening.get("id") or ""),
                            "origin_room_ids": [],
                            "inferred_circulation_void": True,
                        })

    # Continuous corridor/dry-zone regions are often intentionally not a
    # separate semantic room.  Sample a narrow audited band around the target
    # room; wall LOS and collision checks decide whether it is truly connected.
    min_x, min_z, max_x, max_z = room_shape.bounds
    x = min_x - 2.4
    while x <= max_x + 2.4 + 1e-9:
        z = min_z - 2.4
        while z <= max_z + 2.4 + 1e-9:
            point = (round(x, 5), round(z, 5))
            probe = Point(*point)
            origin_rooms = [room_id for room_id, shape in all_rooms if shape.buffer(.02).covers(probe)]
            # CAD polygonization can omit circulation voids (hall/dry zone)
            # even though enclosing room boundaries clearly prove that they
            # are inside the apartment.  Admit only points inside the convex
            # interior envelope and within 1.35m of a known semantic room;
            # the existing exact wall LOS and object collision gates remain
            # mandatory, so this is not an exterior/free-space fallback.
            inferred_interior = bool(
                interior_envelope is not None and interior_envelope.buffer(.02).covers(probe)
                and room_union is not None and room_union.distance(probe) <= 1.35)
            if (not room_shape.buffer(.03).covers(probe)
                    and room_shape.buffer(2.45).covers(probe)
                    and (origin_rooms or inferred_interior)
                    and _camera_origin_clear(model, point)):
                specs.append({"point": point, "origin_scope": "cad_semantic_adjacent_free_space",
                              "passage_opening": None, "origin_room_ids": origin_rooms,
                              "inferred_circulation_void": not bool(origin_rooms)})
            z += .32
        x += .32

    priority = {"inside_room": 0, "adjacent_portal": 1, "cad_semantic_adjacent_free_space": 2}
    unique = {}
    for spec in specs:
        key = (round(spec["point"][0], 3), round(spec["point"][1], 3),
               str(spec.get("portal_opening_id")
                   or spec.get("reference_passage_opening_id") or ""))
        existing = unique.get(key)
        if existing is None or priority[spec["origin_scope"]] < priority[existing["origin_scope"]]:
            unique[key] = spec
    return sorted(unique.values(), key=lambda row: (
        priority[row["origin_scope"]], round(row["point"][0], 4), round(row["point"][1], 4)))


def _diverse_point_specs(rows: list[dict], limit: int) -> list[dict]:
    if len(rows) <= limit:
        return rows
    center_x = sum(float(row["point"][0]) for row in rows) / len(rows)
    center_z = sum(float(row["point"][1]) for row in rows) / len(rows)
    selected = [max(rows, key=lambda row: (
        math.dist(row["point"], (center_x, center_z)),
        -float(row["point"][0]), -float(row["point"][1])))]
    remaining = [row for row in rows if row is not selected[0]]
    while remaining and len(selected) < limit:
        choice = max(remaining, key=lambda row: (
            min(math.dist(row["point"], chosen["point"]) for chosen in selected),
            -float(row["point"][0]), -float(row["point"][1])))
        selected.append(choice)
        remaining.remove(choice)
    return selected


def generate_reference_camera_candidates(model: dict, contract: dict, *, aspect_ratio: str = "4:3",
                                         max_per_slot: int = 8, project_revision: int = 0) -> dict:
    """Generate nine independent local candidate pools or fail closed."""
    contract = split_reference_contract(contract)
    contract_id = str(contract.get("contract_id") or "")
    slots = contract.get("slots") or []
    anchor_report = model.get("reference_anchor_report") or {}
    preflight_errors = []
    if anchor_report and anchor_report.get("status") != "ready":
        preflight_errors.extend(copy.deepcopy(anchor_report.get("hard_errors") or []))
    binding = bind_reference_slots_to_rooms(model, contract)
    preflight_errors.extend(copy.deepcopy(binding.get("hard_errors") or []))
    rooms = {str(row.get("id") or ""): row for row in model.get("rooms") or []}
    cad_hash = str(model.get("cad_facts_hash") or "")
    model_digest = reference_model_facts_hash(model)
    base = {"schema_version": 1, "mode": "reference", "pool_scope": "reference_slot",
            "contract_id": contract_id, "aspect_ratio": aspect_ratio,
            "project_revision": int(project_revision), "cad_facts_hash": cad_hash,
            "model_facts_hash": model_digest}
    if preflight_errors:
        return {**base, "status": "blocked", "proposal_id": "", "proposal_hash": "",
                "slot_pools": [], "candidates": [], "hard_errors": preflight_errors}

    all_candidates, pools, global_errors = [], [], []
    room_slot_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    visibility_context = _reference_visibility_context(model)
    visibility_cache: dict[tuple, tuple[bool, str]] = {}
    room_scan_cache: dict[str, tuple[dict, list[dict]]] = {}
    for slot in slots:
        slot_id = str(slot.get("slot_id") or "")
        room_id = str(binding["bindings"].get(slot_id) or "")
        room = rooms.get(room_id)
        errors, rejections = [], defaultdict(int)
        if not room:
            errors.append({"code": "reference_bound_room_missing", "slot_id": slot_id, "room_id": room_id})
            anchor_resolution, walkable = {}, {}
        else:
            anchor_resolution = resolve_slot_anchor_groups(model, room, slot)
            errors.extend(copy.deepcopy(anchor_resolution.get("hard_errors") or []))
            cached_scan = room_scan_cache.get(room_id)
            if cached_scan is None:
                walkable = build_reference_walkable_region(model, room)
                raw_point_specs = (_reference_point_specs(model, room, walkable["geometry"])
                                   if not walkable.get("hard_errors") else [])
                by_scope = defaultdict(list)
                for row in raw_point_specs:
                    by_scope[str(row.get("origin_scope") or "")].append(row)
                bounded_point_specs = (
                    _diverse_point_specs(by_scope["inside_room"], 48)
                    + _diverse_point_specs(by_scope["adjacent_portal"], 36)
                    + _diverse_point_specs(by_scope["cad_semantic_adjacent_free_space"], 64)
                )
                room_scan_cache[room_id] = (walkable, bounded_point_specs)
            else:
                walkable, bounded_point_specs = cached_scan
            errors.extend(copy.deepcopy(walkable.get("hard_errors") or []))
        candidates = []
        if not errors:
            # Scan a spatially diverse, bounded set instead of stopping after
            # the first portal.  The pool is cached per room because multiple
            # reference slots deliberately bind to the same CAD space.
            point_specs = bounded_point_specs
            focal_samples = _focal_samples(slot)
            safe_frame = copy.deepcopy((contract.get("camera") or {}).get("safe_frame") or {})
            safe_frame["subject_overrides"] = copy.deepcopy(
                slot.get("subject_safe_frame_overrides") or {})
            for point_spec in point_specs:
                point = point_spec["point"]
                if slot_id.startswith("living_") and room_slot_points[room_id]:
                    polygon_points = [(float(row["x"]), float(row["z"])) for row in room.get("polygon") or []]
                    xs, zs = zip(*polygon_points)
                    separation = max(.9, math.hypot(max(xs) - min(xs), max(zs) - min(zs)) * .12)
                    if any(math.dist(point, other) < separation for other in room_slot_points[room_id]):
                        rejections["living_camera_separation_failed"] += 1
                        continue
                coarse = []
                for focal in focal_samples:
                    for yaw in range(0, 360, YAW_COARSE_STEP_DEG):
                        evaluation = _yaw_evaluation(
                            model, point, anchor_resolution["anchor_groups"], yaw,
                            focal, aspect_ratio, point_spec.get("passage_opening"),
                            visibility_cache=visibility_cache,
                            visibility_context=visibility_context)
                        if evaluation.get("pass"):
                            coarse.append((float(evaluation.get("score") or 0), yaw, focal, evaluation))
                        else:
                            for reason, count in (evaluation.get("reasons") or {}).items():
                                rejections[reason] += int(count)
                for _, coarse_yaw, focal, _ in sorted(coarse, reverse=True)[:2]:
                    for offset in range(-10, 11, YAW_REFINE_STEP_DEG):
                        yaw = float((coarse_yaw + offset) % 360)
                        evaluation = _yaw_evaluation(
                            model, point, anchor_resolution["anchor_groups"], yaw,
                            focal, aspect_ratio, point_spec.get("passage_opening"),
                            visibility_cache=visibility_cache,
                            visibility_context=visibility_context)
                        if not evaluation.get("pass"):
                            continue
                        for pitch_deg in PITCH_SAMPLES_DEG:
                            safe_frame_pass, projected_bounds = _subjects_fit_safe_frame(
                                model, point, yaw, focal, pitch_deg, aspect_ratio,
                                evaluation["subjects"], safe_frame)
                            if not safe_frame_pass:
                                rejections["analytic_safe_frame_failed"] += 1
                            # This projection uses conservative axis-aligned
                            # proxy boxes and intentionally cannot model wall
                            # cut-outs or exact pixel occlusion.  Keep it as a
                            # ranking hint; the server software rasterizer is
                            # the authoritative pre-paid safe-frame gate.
                            analytic_penalty = 4.0 if not safe_frame_pass else 0.0
                            radians = math.radians(yaw)
                            candidate_id = f"refcam_{slot_id}_{len(candidates) + 1:03d}"
                            # A reference composition may be visible from an adjacent
                            # circulation zone, but that must remain a fallback.  The
                            # former 1.5/2.5 point penalty was too small: a marginally
                            # wider subject frame routinely beat a legal in-room pose,
                            # leaving a doorway/column in the greybox.  The material
                            # model then tried to imitate the reference by silently
                            # moving the camera.  Prefer CAD-room landings whenever one
                            # exists; adjacent scopes stay available for genuinely tight
                            # bathrooms/open-plan axes with no in-room solution.
                            scope_penalty = {
                                "inside_room": 0.0,
                                "adjacent_portal": 12.0,
                                "cad_semantic_adjacent_free_space": 20.0,
                            }.get(str(point_spec.get("origin_scope") or ""), 24.0)
                            validation = {
                                "version": 1, "slot_id": slot_id,
                                "scene_id": str((slot.get("reference_viewpoint") or {}).get("scene_id") or ""),
                                "room_id": room_id, "landing_policy_mode": LANDING_MODE,
                                "landing_source": LANDING_SOURCE,
                                "yaw_source": "local_360_coarse10_refine2_pitch_local",
                                "pitch_deg": pitch_deg,
                                "cad_position_pass": True, "collision_pass": True,
                                "visibility_pass": True, "projection_method": "backend_2d_fov_los",
                                "width": 0, "height": 0, "pixel_origin": "top-left",
                                "buffer_sha": "", "must_show_bounds": [],
                                "safe_frame_status": "pending_browser", "safe_frame_pass": None,
                                "must_show_subjects": evaluation["subjects"],
                                "cad_absent_optional_subjects": copy.deepcopy(
                                    (anchor_resolution.get("omitted_subjects") or [])
                                    + (evaluation.get("omitted_optional_subjects") or [])),
                                "must_validate": {key: True for key in slot.get("must_validate") or []},
                            }
                            candidates.append({
                                "candidate_id": candidate_id, "slot_id": slot_id,
                                "reference_slot_id": slot_id, "room_id": room_id,
                                "room_label": str(room.get("label") or room_id),
                                "origin_scope": point_spec["origin_scope"],
                                "pool_scope": "reference_slot",
                                "local_score": round(float(evaluation["score"]) - scope_penalty
                                                     - abs(pitch_deg) * .03 - analytic_penalty, 5),
                                "metrics": {
                                    "room_profile": str(room.get("semantic_profile") or room.get("room_type") or "other"),
                                    "safety_gate": True, "visibility_gate": True,
                                    "origin_scope": point_spec["origin_scope"],
                                    "analytic_safe_frame_bounds": projected_bounds,
                                    "analytic_safe_frame_pass": safe_frame_pass,
                                    "origin_scope_penalty": scope_penalty,
                                    "origin_preference": "inside_room_first",
                                    "pitch_deg": pitch_deg,
                                },
                                "camera": {
                                    "id": candidate_id, "name": f"{slot_id} 自动候选",
                                    "position": {"x": round(point[0], 5), "y": EYE_HEIGHT_M,
                                                 "z": round(point[1], 5)},
                                    "target": {"x": round(point[0] + math.sin(radians) * 4, 5),
                                               "y": round(EYE_HEIGHT_M + math.tan(math.radians(pitch_deg)) * 4, 5),
                                               "z": round(point[1] + math.cos(radians) * 4, 5)},
                                    "focal_length_mm": focal, "room_id": room_id,
                                    "reference_slot_id": slot_id, "source": "auto_geometry",
                                    "origin_scope": point_spec["origin_scope"],
                                    **({"portal_opening_id": point_spec.get("portal_opening_id"),
                                        "origin_room_ids": point_spec.get("origin_room_ids") or []}
                                       if point_spec["origin_scope"] == "adjacent_portal" else {}),
                                    **({
                                        "reference_passage_opening_id": point_spec.get(
                                            "reference_passage_opening_id") or "",
                                        "inferred_circulation_void": bool(
                                            point_spec.get("inferred_circulation_void")),
                                    } if point_spec["origin_scope"]
                                    == "cad_semantic_adjacent_free_space" else {}),
                                    "reference_contract_validation": validation,
                                },
                                "reference_contract_validation": validation,
                            })
            candidates.sort(key=lambda row: (-float(row.get("local_score") or 0),
                                              str(row.get("candidate_id") or "")))
            # Deduplicate exact poses first, then deliberately diversify by
            # landing point.  The former top-N truncation returned eight pitch
            # variants from one doorway, so the software gate had no alternate
            # view when a refrigerator dominated that doorway.
            unique, keys = [], set()
            for row in candidates:
                camera = row["camera"]
                position, target = camera["position"], camera["target"]
                yaw = round(math.degrees(math.atan2(target["x"] - position["x"],
                                                    target["z"] - position["z"])) % 360, 0)
                horizontal = max(1e-9, math.hypot(target["x"] - position["x"],
                                                  target["z"] - position["z"]))
                pitch = round(math.degrees(math.atan2(target["y"] - position["y"], horizontal)), 1)
                key = (round(position["x"], 2), round(position["z"], 2), yaw,
                       pitch, camera["focal_length_mm"])
                if key not in keys:
                    keys.add(key)
                    unique.append(row)
            point_primary: dict[tuple[float, float], dict] = {}
            for row in unique:
                position = row["camera"]["position"]
                point_primary.setdefault(
                    (round(float(position["x"]), 2), round(float(position["z"]), 2)), row)
            remaining = list(point_primary.values())
            diverse = []
            while remaining and len(diverse) < max_per_slot:
                if not diverse:
                    choice = remaining[0]
                else:
                    used_scopes = {str(row.get("origin_scope") or "") for row in diverse}
                    choice = max(remaining, key=lambda row: (
                        float(row.get("local_score") or 0)
                        + min(4.0, min(math.hypot(
                            float(row["camera"]["position"]["x"])
                            - float(chosen["camera"]["position"]["x"]),
                            float(row["camera"]["position"]["z"])
                            - float(chosen["camera"]["position"]["z"]))
                            for chosen in diverse)) * 2.5
                        + (3.0 if str(row.get("origin_scope") or "") not in used_scopes else 0.0),
                        float(row.get("local_score") or 0),
                        str(row.get("candidate_id") or ""),
                    ))
                diverse.append(choice)
                remaining.remove(choice)
            if len(diverse) < max_per_slot:
                diverse.extend(row for row in unique if row not in diverse)
            candidates = diverse[:max_per_slot]
        if candidates:
            selected_camera = candidates[0]["camera"]
            point = selected_camera["position"]
            room_slot_points[room_id].append((float(point["x"]), float(point["z"])))
        else:
            errors.append({"code": "reference_slot_no_legal_camera", "slot_id": slot_id,
                           "room_id": room_id, "rejection_summary": dict(rejections)})
        pool = {"slot_id": slot_id, "room_id": room_id, "pool_scope": "reference_slot",
                "room_label": str((room or {}).get("label") or room_id),
                "landing_policy_mode": LANDING_MODE, "landing_source": LANDING_SOURCE,
                "origin_preference": "inside_room_first",
                "primary_origin_scope": str(((candidates[0].get("camera") or {})
                                             .get("origin_scope") or "")) if candidates else "",
                "inset_m": walkable.get("inset_m"), "focal_samples_mm": _focal_samples(slot),
                "status": "ready" if candidates and not errors else "blocked",
                "candidate_ids": [row["candidate_id"] for row in candidates],
                "candidates": candidates, "hard_errors": errors,
                "rejection_summary": dict(rejections)}
        pools.append(pool)
        all_candidates.extend(candidates)
        global_errors.extend(errors)

    proposal_id = f"reference_proposal_{uuid.uuid4().hex[:12]}"
    proposal_payload = {**base, "proposal_id": proposal_id,
                        "slot_pools": pools, "candidates": all_candidates}
    proposal_hash = reference_proposal_hash(proposal_payload)
    for row in all_candidates:
        row["proposal_id"] = proposal_id
        row["proposal_hash"] = proposal_hash
        row["camera"]["reference_contract_validation"]["proposal_id"] = proposal_id
        row["camera"]["reference_contract_validation"]["proposal_hash"] = proposal_hash
    return {**proposal_payload, "proposal_hash": proposal_hash,
            "status": "ready" if len(pools) == len(slots) and not global_errors else "blocked",
            "hard_errors": global_errors}


def find_reference_candidate(proposal: dict, candidate_id: str, slot_id: str) -> dict:
    matches = [row for row in proposal.get("candidates") or []
               if str(row.get("candidate_id") or "") == str(candidate_id or "")
               and str(row.get("slot_id") or "") == str(slot_id or "")]
    return copy.deepcopy(matches[0]) if len(matches) == 1 else {}


def evaluate_subject_id_pixels(image: Any, legend: dict, expected_subjects: Iterable[str],
                               safe_frame: dict) -> dict:
    """Recompute top-left pixel evidence from exact 24-bit subject colours."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    expected = [str(value) for value in expected_subjects]
    entries = legend.get("subjects") if isinstance(legend, dict) else None
    if not isinstance(entries, list):
        entries = []
    errors, by_subject, colors = [], defaultdict(list), set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        subject = str(entry.get("subject") or "")
        color = entry.get("color")
        if (not subject or not isinstance(color, list) or len(color) != 3
                or any(not isinstance(value, int) or value < 0 or value > 255 for value in color)):
            errors.append({"code": "subject_id_legend_invalid", "subject": subject})
            continue
        color_tuple = tuple(color)
        if color_tuple == (0, 0, 0) or color_tuple in colors:
            errors.append({"code": "subject_id_color_not_unique", "subject": subject})
            continue
        colors.add(color_tuple)
        by_subject[subject].append({**entry, "color": color_tuple})
    if set(by_subject) != set(expected) or any(len(by_subject[value]) != 1 for value in by_subject):
        errors.append({"code": "subject_id_expected_subjects_mismatch", "expected": expected,
                       "actual": sorted(by_subject),
                       "counts": {key: len(value) for key, value in by_subject.items()}})
    pixels = rgb.load()
    bounds = []
    minimum_subject_pixels = max(4, int(width * height * .001))
    for subject in expected:
        rows = by_subject.get(subject) or []
        if len(rows) != 1:
            continue
        color = rows[0]["color"]
        xs, ys = [], []
        for y in range(height):
            for x in range(width):
                if pixels[x, y] == color:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            errors.append({"code": "subject_id_occluded_or_absent", "subject": subject})
            continue
        bound = {
            "subject": subject, "anchor_id": str(rows[0].get("anchor_id") or ""),
            "pixel_count": len(xs),
            "x_min": min(xs) / width, "x_max": (max(xs) + 1) / width,
            "y_min": min(ys) / height, "y_max": (max(ys) + 1) / height,
        }
        if len(xs) < minimum_subject_pixels:
            errors.append({"code": "subject_id_visible_area_too_small", "subject": subject,
                           "pixel_count": len(xs), "minimum_pixels": minimum_subject_pixels,
                           "bounds": copy.deepcopy(bound)})
        override = ((safe_frame.get("subject_overrides") or {}).get(subject) or {})
        x_min_allowed = float(override.get("x_min", safe_frame.get("x_min", .08)))
        x_max_allowed = float(override.get("x_max", safe_frame.get("x_max", .92)))
        y_min_allowed = float(override.get("y_min", safe_frame.get("y_min", .08)))
        y_max_allowed = float(override.get("y_max", safe_frame.get("y_max", .94)))
        if not (bound["x_min"] >= x_min_allowed and bound["x_max"] <= x_max_allowed
                and bound["y_min"] >= y_min_allowed and bound["y_max"] <= y_max_allowed):
            errors.append({"code": "subject_id_outside_safe_frame", "subject": subject,
                           "bounds": copy.deepcopy(bound)})
        bounds.append(bound)
    return {
        "version": "whole-home-subject-pixel-gate-v2",
        "pass": not errors and len(bounds) == len(expected), "width": width, "height": height,
        "pixel_origin": "top-left", "must_show_bounds": bounds, "hard_errors": errors,
    }
