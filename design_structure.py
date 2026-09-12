"""Whole-home design: structure."""

import json
import math
import time
from copy import deepcopy
from typing import Any, Optional





from .design_provider import (
    call_gemini_json
)
from .design_schema import (
    STRUCTURE_GUIDANCE_QUESTIONS,
    _STRUCTURE_GRAPH_SCHEMA,
    empty_structure_review
)
from .design_store import (
    _stable_hash,
    file_sha256
)

def _guidance_questions() -> list[dict]:
    return [
        {**{key: value for key, value in row.items() if key != "choices"},
         "choices": [{"value": value, "label": label} for value, label in row["choices"]]}
        for row in STRUCTURE_GUIDANCE_QUESTIONS
    ]

def _scale_calibration(project: dict) -> dict:
    scales = [row for row in (project.get("anchor_set") or {}).get("anchors", []) if row.get("kind") == "scale"]
    if len(scales) != 1:
        raise ValueError("研究建模必须且只能有一条人工比例尺")
    scale = scales[0]
    points = scale.get("points") or []
    if len(points) != 2 or not scale.get("distance_mm"):
        raise ValueError("比例尺必须包含两个端点和真实毫米长度")
    canvas = (project.get("normalization") or {}).get("canvas_size") or []
    if len(canvas) != 2 or min(canvas) <= 0:
        raise ValueError("规范化图像尺寸缺失")
    ax, ay = points[0]["x"] / 1000 * canvas[0], points[0]["y"] / 1000 * canvas[1]
    bx, by = points[1]["x"] / 1000 * canvas[0], points[1]["y"] / 1000 * canvas[1]
    pixels = math.hypot(bx - ax, by - ay)
    if pixels < 2:
        raise ValueError("比例尺像素长度过短")
    return {
        "anchor_id": scale["anchor_id"],
        "distance_mm": float(scale["distance_mm"]),
        "metres_per_pixel": float(scale["distance_mm"]) / 1000.0 / pixels,
        "canvas_size": [int(canvas[0]), int(canvas[1])],
    }

def prepare_structure_review(project: dict, *, payload_override: Optional[dict] = None) -> dict:
    anchors = project.get("anchor_set") or {}
    if not anchors.get("confirmed_complete"):
        raise ValueError("请先完成人工空间、入口和比例尺锚点")
    calibration = _scale_calibration(project)
    room_rows = list((project.get("plan_summary") or {}).get("rooms") or [])
    room_ids = [str(row.get("id") or "") for row in room_rows if str(row.get("id") or "")]
    prompt = f"""Extract one editable architectural structure graph from this residential floor plan.
Image 1 is the normalized full evidence. Image 2 contains immutable human anchors.
Use normalized integer coordinates 0..1000 with top-left origin. Human anchors and room IDs are hard facts.
ROOM_IDS={json.dumps(room_ids, ensure_ascii=False)}
ANCHORS={json.dumps(anchors.get('anchors') or [], ensure_ascii=False)}
Return an ordered outer boundary, wall centerline segments, door/window segments, and the room/exterior adjacency graph.
Every wall/opening ID must be unique. Use only ROOM_IDS or exterior for side-space fields. Do not turn text,
dimensions, dashed leaders, furniture, TV/display/low storage, door leaves, window tracks or floor-drop lines into walls.
Exterior walls default 0.20m and interior walls 0.12m only when the drawing does not provide thickness; default
research wall height is 2.80m and must be listed as unresolved. Door/entrance sill is 0; windows require sill<head.
Use swing_direction=not_shown when the drawing does not prove the swing. Do not invent an opening owner.
"""
    payload, error = (payload_override, None) if payload_override is not None else call_gemini_json(
        prompt,
        [project["normalized_path"], project.get("anchor_overlay_path") or project["normalized_path"]],
        _STRUCTURE_GRAPH_SCHEMA,
        max_output_tokens=12000,
    )
    review = empty_structure_review()
    review.update(
        status="needs_answers" if payload else "external_review_pending",
        questions=_guidance_questions(),
        scale_calibration=calibration,
        seed_graph=payload,
        provider="fixture" if payload_override is not None else "gemini" if payload else "gemini_unavailable",
        error=error or "",
        unresolved=list((payload or {}).get("unresolved") or []),
        updated_at=time.time(),
    )
    project["structure_review"] = review
    return review

def _norm_point(raw: Any, label: str) -> tuple[int, int]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} 必须是坐标对象")
    x, y = int(raw.get("x", -1)), int(raw.get("y", -1))
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        raise ValueError(f"{label} 超出 0–1000")
    return x, y

def _compile_structure_bundle(project: dict, seed: dict) -> dict:
    calibration = _scale_calibration(project)
    canvas_w, canvas_h = calibration["canvas_size"]
    mpp = calibration["metres_per_pixel"]
    boundary_norm = [_norm_point(point, "outer_boundary") for point in list(seed.get("outer_boundary") or [])]
    if len(boundary_norm) < 3:
        raise ValueError("Gemini 未返回可用外轮廓")
    pixels = [(x / 1000 * canvas_w, y / 1000 * canvas_h) for x, y in boundary_norm]
    origin_x = min(x for x, _ in pixels)
    origin_y = max(y for _, y in pixels)

    def to_m(point: Any) -> list[float]:
        x, y = _norm_point(point, "结构点")
        px, py = x / 1000 * canvas_w, y / 1000 * canvas_h
        return [round((px - origin_x) * mpp, 6), round((origin_y - py) * mpp, 6)]

    def norm_distance_to_segment(point: dict, a: dict, b: dict) -> float:
        px, py = _norm_point(point, "人工开口锚点")
        ax, ay = _norm_point(a, "AI开口起点")
        bx, by = _norm_point(b, "AI开口终点")
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    raw_openings = list(seed.get("openings") or [])
    anchor_bindings: dict[str, str] = {}
    used_opening_ids: set[str] = set()
    for anchor in (project.get("anchor_set") or {}).get("anchors", []):
        if anchor.get("kind") not in {"entrance", "opening"}:
            continue
        candidates = [row for row in raw_openings if anchor["kind"] != "entrance" or row.get("kind") == "entrance"]
        matches = [
            row for row in candidates
            if max(norm_distance_to_segment(point, row.get("a") or {}, row.get("b") or {}) for point in anchor.get("points") or []) <= 35.0
        ]
        if not matches:
            raise ValueError(f"人工{anchor['kind']}锚点 {anchor['anchor_id']} 未与任何 Gemini 开口几何对应")
        if len(matches) > 1:
            raise ValueError(f"人工{anchor['kind']}锚点 {anchor['anchor_id']} 同时匹配多个 Gemini 开口")
        opening_id = str(matches[0].get("id") or "")
        if not opening_id or opening_id in used_opening_ids:
            raise ValueError(f"人工开口锚点 {anchor['anchor_id']} 未与 Gemini 开口形成唯一对应")
        used_opening_ids.add(opening_id)
        anchor_bindings[anchor["anchor_id"]] = opening_id

    spaces = []
    known_spaces = {"exterior"}
    room_by_id = {str(row.get("id")): row for row in (project.get("plan_summary") or {}).get("rooms", []) if row.get("id")}
    anchor_by_id = {row.get("anchor_id"): row for row in (project.get("anchor_set") or {}).get("anchors", [])}
    for room_id, room in room_by_id.items():
        anchor = next((anchor_by_id.get(anchor_id) for anchor_id in room.get("anchor_ids") or [] if anchor_by_id.get(anchor_id)), None)
        if not anchor:
            anchor = next((row for row in anchor_by_id.values() if row.get("kind") == "space" and row.get("label") == room.get("label")), None)
        if not anchor:
            raise ValueError(f"空间 {room_id} 缺少人工点位")
        spaces.append({"id": room_id, "label": str(room.get("label") or room_id), "point_m": to_m(anchor["points"][0])})
        known_spaces.add(room_id)

    walls = []
    wall_ids: set[str] = set()
    for index, raw in enumerate(seed.get("walls") or [], 1):
        wall_id = str(raw.get("id") or f"WALL-{index:03d}")
        if wall_id in wall_ids:
            raise ValueError(f"墙 ID 重复: {wall_id}")
        wall_ids.add(wall_id)
        left, right = str(raw.get("left_space_id") or ""), str(raw.get("right_space_id") or "")
        if left not in known_spaces or right not in known_spaces or left == right:
            raise ValueError(f"{wall_id} 两侧空间无效")
        walls.append({
            "id": wall_id, "centerline_m": [to_m(raw.get("a")), to_m(raw.get("b"))],
            "thickness_m": round(float(raw.get("thickness_m") or 0.12), 4), "base_m": 0.0,
            "height_m": round(float(raw.get("height_m") or 2.8), 4),
            "left_space_id": left, "right_space_id": right, "source": "gemini_inferred",
            "confirmed": True,
        })

    openings = []
    opening_ids: set[str] = set()
    for index, raw in enumerate(seed.get("openings") or [], 1):
        opening_id = str(raw.get("id") or f"OPENING-{index:03d}")
        if opening_id in opening_ids:
            raise ValueError(f"开口 ID 重复: {opening_id}")
        opening_ids.add(opening_id)
        owner = str(raw.get("owning_wall_id") or "")
        if owner not in wall_ids:
            raise ValueError(f"{opening_id} 缺少有效所属墙")
        side_a, side_b = str(raw.get("side_a_space_id") or ""), str(raw.get("side_b_space_id") or "")
        if side_a not in known_spaces or side_b not in known_spaces or side_a == side_b:
            raise ValueError(f"{opening_id} 两侧空间无效")
        a_m, b_m = to_m(raw.get("a")), to_m(raw.get("b"))
        width = math.dist(a_m, b_m)
        kind = str(raw.get("kind") or "")
        swing = None if kind == "window" else str(raw.get("swing_direction") or "not_shown")
        openings.append({
            "id": opening_id, "kind": kind, "owning_wall_id": owner, "segment_m": [a_m, b_m],
            "width_m": round(width, 6), "sill_m": round(float(raw.get("sill_m") or 0), 4),
            "head_m": round(float(raw.get("head_m") or 2.1), 4), "swing_direction": swing,
            "side_a_space_id": side_a, "side_b_space_id": side_b,
            "jamb_before_supported": True, "jamb_after_supported": True, "junction_clearance_m": 0.05,
            "junction_diagnostics": [], "confirmed": True, "source": "gemini_inferred",
        })

    edges = []
    for index, raw in enumerate(seed.get("adjacencies") or [], 1):
        a, b = str(raw.get("space_a_id") or ""), str(raw.get("space_b_id") or "")
        if a not in known_spaces or b not in known_spaces or a == b:
            raise ValueError(f"邻接 {index} 两侧空间无效")
        opening_id = str(raw.get("opening_id") or "")
        if opening_id and opening_id not in opening_ids:
            raise ValueError(f"邻接 {index} 引用了未知开口")
        kind = str(raw.get("kind") or "open_passage")
        edges.append({"id": str(raw.get("id") or f"ADJ-{index:03d}"), "space_a_id": a, "space_b_id": b,
                      "kind": kind, "opening_id": opening_id if kind == "door" else None, "confirmed": True})

    bundle = {
        "schema": "research-structure-bundle-v1",
        "source": {"normalized_hash": file_sha256(project["normalized_path"]), "scale_anchor_id": calibration["anchor_id"], "anchor_opening_bindings": anchor_bindings},
        "project": {"id": project["project_id"], "revision": project["revision"]},
        "source_hash": project["source_hash"],
        "structure_hash": "0" * 64,
        "outer_boundary_m": [to_m({"x": x, "y": y}) for x, y in boundary_norm],
        "spaces": spaces,
        "wall_branch_graph": {"version": "wall-branch-graph-v1", "walls": walls},
        "opening_contract": {"version": "opening-contract-v1", "junction_clearance_m": 0.05, "openings": openings},
        "adjacency_truth": {"version": "adjacency-truth-v1", "edges": edges, "confirmed": True},
        "assumptions": {"scale_m_per_unit": 1.0, "floor_slab_thickness_m": 0.12, "research_only": True},
        "unresolved_issues": list(seed.get("unresolved") or []),
    }
    try:
        from .tools.fastloop_research import compute_structure_hash, validate_bundle
        bundle["structure_hash"] = compute_structure_hash(bundle)
        validate_bundle(bundle)
    except ImportError:
        bundle["structure_hash"] = _stable_hash({key: value for key, value in bundle.items() if key != "structure_hash"})
    return bundle

def submit_structure_review(project: dict, answers: dict[str, str], *, technical_bundle: Optional[dict] = None) -> dict:
    review = project.get("structure_review") or empty_structure_review()
    question_map = {row["id"]: row for row in review.get("questions") or _guidance_questions()}
    if set(answers) != set(question_map):
        missing = sorted(set(question_map) - set(answers))
        unknown = sorted(set(answers) - set(question_map))
        raise ValueError(f"九问答案不完整: missing={missing}, unknown={unknown}")
    for question_id, value in answers.items():
        allowed = {choice["value"] for choice in question_map[question_id]["choices"]}
        if value not in allowed:
            raise ValueError(f"{question_id} 包含非法答案")
    review["answers"] = dict(answers)
    # Migrate old mixed lists by removing only known generated issue forms.
    # Independent model findings are retained across answer edits.
    unresolved = [item for item in review.get("source_unresolved", review.get("unresolved") or [])
                  if not any(str(item).startswith(f"{key}: ") for key in question_map)
                  and not str(item).startswith("技术结构合同未通过：")]
    review["source_unresolved"] = list(unresolved)
    review["answer_issues"] = [f"{key}: {value}" for key, value in answers.items()
        if value in {"unsure", "has_structure", "contains_walls", "some_walls", "has_missing", "incomplete", "no"}]
    review["technical_issues"] = []
    unresolved.extend(f"{key}: {value}" for key, value in answers.items() if value in {"unsure", "has_structure", "contains_walls", "some_walls", "has_missing", "incomplete", "no"})
    if technical_bundle is None and not review.get("seed_graph"):
        review.update(status="external_review_pending", unresolved=unresolved, error=review.get("error") or "Gemini 结构图不可用", updated_at=time.time())
        project["structure_review"] = review
        return review
    try:
        if technical_bundle is not None:
            if technical_bundle.get("schema") != "research-structure-bundle-v1":
                raise ValueError("专业结构包版本错误")
            bundle = deepcopy(technical_bundle)
            if bundle.get("source_hash") != project.get("source_hash"):
                raise ValueError("专业结构包不属于当前原户型")
            if (bundle.get("project") or {}).get("id") != project.get("project_id"):
                raise ValueError("专业结构包不属于当前项目")
            if int((bundle.get("project") or {}).get("revision") or -1) != int(project.get("revision") or 0):
                raise ValueError("专业结构包 revision 已过期")
            if (bundle.get("source") or {}).get("normalized_hash") != file_sha256(project["normalized_path"]):
                raise ValueError("专业结构包不属于当前规范化证据图")
            from .tools.fastloop_research import compute_structure_hash, validate_bundle
            bundle["structure_hash"] = compute_structure_hash(bundle)
            validate_bundle(bundle)
        else:
            bundle = _compile_structure_bundle(project, review["seed_graph"])
    except (ValueError, OSError) as exc:
        review["technical_issues"] = [f"技术结构合同未通过：{exc}"]
        unresolved.extend(review["technical_issues"])
        review.update(
            status="needs_professional_review", structure_bundle=None, structure_hash="",
            unresolved=list(dict.fromkeys(unresolved)), error="技术结构图需要专业校正", updated_at=time.time(),
        )
        project["structure_review"] = review
        return review
    review.update(
        status="needs_professional_review" if unresolved or answers.get("Q09_READY") != "yes" else "verified",
        structure_bundle=bundle,
        structure_hash=bundle["structure_hash"],
        unresolved=list(dict.fromkeys(unresolved)),
        error="",
        updated_at=time.time(),
    )
    project["structure_review"] = review
    return review
