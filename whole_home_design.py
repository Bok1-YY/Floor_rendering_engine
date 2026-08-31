"""Whole-home design and local research-model domain service.

The source plan and human-confirmed structure remain the geometry authority.
Generated bird's-eye images are concept references for materials, furniture and
atmosphere only.  Local Blender/GLB/IFC research outputs are versioned artifacts;
they are never promoted to construction/BIM truth by this module.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import requests
import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from .api import (
    _call_fal_queue_json,
    _fal_image_from_result,
    _file_to_data_uri,
    call_gemini_generate,
)
from .config import FAL_MODEL_MAP, GEMINI_MODEL_MAP, MAIN_OUTPUT_DIR, get_text_models, load_config, logger
from .server_helpers import result_thumb_url, to_url


SCHEMA_VERSION = "whole-home-design-project-v1"
PROMPT_VERSION = "whole-home-birdseye-v1"
PLAN_PROMPT_VERSION = "whole-home-anchor-plan-v2"
PLAN_VERIFY_VERSION = "whole-home-anchor-verify-v1"
QA_PROMPT_VERSION = "whole-home-structure-qa-v1"
ROOT = os.path.join(MAIN_OUTPUT_DIR, "_whole_home_design")
PROJECT_ROOT = os.path.join(ROOT, "projects")
ASSET_ROOT = os.path.join(ROOT, "assets")
BUNDLE_ROOT = os.path.join(ROOT, "bundles")
MODEL_ROOT = os.path.join(ROOT, "model-runs")
for _folder in (PROJECT_ROOT, ASSET_ROOT, BUNDLE_ROOT, MODEL_ROOT):
    os.makedirs(_folder, exist_ok=True)

_LOCKS_GUARD = threading.RLock()
_LOCKS: dict[str, threading.RLock] = {}

SUPPORTED_RATIOS: tuple[tuple[str, float], ...] = (
    ("9:16", 9 / 16), ("2:3", 2 / 3), ("3:4", 3 / 4),
    ("4:5", 4 / 5), ("1:1", 1.0), ("5:4", 5 / 4),
    ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9),
)

STRUCTURE_REVIEW_ITEMS = (
    "orientation_and_crop",
    "outer_footprint",
    "room_count_and_positions",
    "partitions_and_adjacencies",
    "entrance_balcony_and_openings",
    "kitchen_bathroom_wet_zones",
    "no_added_or_missing_spaces",
    "orthographic_topdown_view",
    "no_labels_dimensions_or_watermarks",
)

STRUCTURE_GUIDANCE_QUESTIONS = (
    {
        "id": "Q01_ANNOTATIONS", "title": "尺寸数字、虚线和说明文字",
        "prompt": "图中的尺寸数字、H/W、虚线和引线是否都只是说明，不需要建成墙？",
        "hint": "通常这些内容只用于读图。若其中确有结构，请选择不确定并在备注中说明。",
        "choices": (("annotations", "对，只是说明"), ("has_structure", "其中有真实结构"), ("unsure", "看不清")),
    },
    {
        "id": "Q02_PARALLEL_LINES", "title": "卫生间和高差区域的平行线",
        "prompt": "湿区里的多条平行线主要是地面高差/下沉边线，而不是全高墙吗？",
        "hint": "只判断它是否应该升到墙高，不需要判断施工做法。",
        "choices": (("floor_feature", "主要是地面高差"), ("wall", "其中有全高墙"), ("unsure", "看不清")),
    },
    {
        "id": "Q03_GLAZING", "title": "外墙细长双线",
        "prompt": "外墙上的细长双线大部分是窗、玻璃或阳台界面吗？",
        "hint": "有采光或可开启界面即可选择门窗/玻璃。",
        "choices": (("mostly_openings", "大部分是门窗/玻璃"), ("contains_walls", "其中有实墙"), ("unsure", "看不清")),
    },
    {
        "id": "Q04_ENTRANCE", "title": "入户门",
        "prompt": "红色人工锚点标出的入口就是本户入户门吗？",
        "hint": "只确认位置；门扇方向看不清时可保留 not_shown。",
        "choices": (("yes", "是入户门"), ("no", "位置不对"), ("unsure", "看不清")),
    },
    {
        "id": "Q05_LOW_FEATURES", "title": "低柜、电视柜和展示柜",
        "prompt": "DISPLAY、TV UNIT、LOW HEIGHT STORAGE 等标注都不是全高墙吗？",
        "hint": "这些通常属于家具或低构造，不应封堵公共空间。",
        "choices": (("not_walls", "都不是全高墙"), ("some_walls", "其中有真实墙体"), ("unsure", "看不清")),
    },
    {
        "id": "Q06_BALCONIES", "title": "阳台与室内连接",
        "prompt": "图上标出的阳台与相邻室内空间之间存在门或玻璃开口吗？",
        "hint": "只确认是否连通；栏杆和窗框细节交给后续模型。",
        "choices": (("connected", "存在门/玻璃开口"), ("closed", "是封闭实墙"), ("unsure", "看不清")),
    },
    {
        "id": "Q07_MISSING_OPENINGS", "title": "遗漏门窗",
        "prompt": "人工锚点之外，图上是否还有明显但未标记的门、窗或开放通道？",
        "hint": "有遗漏时选择有；Gemini 会列出候选，必要时进入专业校正。",
        "choices": (("none", "没有明显遗漏"), ("has_missing", "还有遗漏"), ("unsure", "看不清")),
    },
    {
        "id": "Q08_ROOM_LIST", "title": "空间清单",
        "prompt": "当前人工标记的空间名称和大致位置是否完整？",
        "hint": "储物间、生活阳台、楼梯间和卫生间也算空间。",
        "choices": (("complete", "名称和位置完整"), ("incomplete", "仍有空间缺失"), ("unsure", "不确定")),
    },
    {
        "id": "Q09_READY", "title": "允许生成研究灰模",
        "prompt": "是否允许系统按已确认比例和上述答案生成研究灰模？",
        "hint": "研究灰模不是施工图；所有默认墙高和未知项都会写进假设清单。",
        "choices": (("yes", "允许生成研究灰模"), ("no", "暂不生成"), ("unsure", "仍需专业复核")),
    },
)


def _project_lock(project_id: str) -> threading.RLock:
    safe = os.path.basename(str(project_id or ""))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(safe, threading.RLock())


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".design_", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def project_path(project_id: str) -> str:
    safe = os.path.basename(str(project_id or ""))
    return os.path.join(PROJECT_ROOT, f"{safe}.json")


def load_project(project_id: str) -> Optional[dict]:
    path = project_path(project_id)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            project = json.load(handle)
        anchor_set = project.get("anchor_set") or {}
        if anchor_set.get("anchors") is not None and not anchor_set.get("anchor_set_hash"):
            try:
                anchor_set["anchor_set_hash"] = _project_anchor_set_hash(project)
            except (ValueError, TypeError):
                anchor_set["anchor_set_hash"] = ""
        return project
    except (OSError, ValueError, TypeError):
        return None


def save_project(project: dict) -> None:
    project["updated_at"] = time.time()
    _atomic_json(project_path(project["project_id"]), project)


def list_projects(limit: int = 50) -> list[dict]:
    rows: list[dict] = []
    try:
        names = sorted(os.listdir(PROJECT_ROOT), reverse=True)
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        row = load_project(name[:-5])
        if row:
            rows.append(row)
        if len(rows) >= max(1, min(int(limit), 200)):
            break
    return rows


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"


def _brief_hash(project: dict) -> str:
    brief = project.get("brief") or {}
    return _stable_hash({
        "requirements_text": brief.get("requirements_text") or "",
        "reference_hashes": brief.get("reference_hashes") or [],
        "plan_summary": project.get("plan_summary") or {},
        "source_hash": project.get("source_hash") or "",
        "generation_hash": project.get("generation_hash") or "",
        "anchor_overlay_hash": project.get("anchor_overlay_hash") or "",
        "prompt_version": PROMPT_VERSION,
    })


def mark_candidates_stale(project: dict, reason: str) -> None:
    for candidate in project.get("candidates") or []:
        candidate["stale"] = True
        candidate["stale_reason"] = reason
    for bundle in project.get("bundles") or []:
        bundle["stale"] = True
        bundle["stale_reason"] = reason
    for model_run in project.get("model_runs") or []:
        model_run["stale"] = True
        model_run["stale_reason"] = reason
    project["locked_candidate_id"] = ""


def create_project(source_path: str, original_name: str = "", *, orientation_policy: str = "exif_transpose-v1") -> dict:
    source_hash = file_sha256(source_path)
    project_id = new_id("design")
    normalized_path, normalization = normalize_floorplan(source_path, project_id, orientation_policy=orientation_policy)
    generation_path, generation_crop = extract_generation_plan(source_path, project_id, orientation_policy=orientation_policy)
    generation_hash = file_sha256(generation_path)
    now = time.time()
    project = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "revision": 1,
        "status": "needs_anchor_review",
        "stage": "请先点选全部空间和入户门",
        "error": "",
        "source_path": source_path,
        "source_name": original_name or os.path.basename(source_path),
        "source_hash": source_hash,
        "source_orientation": {
            "source_file_hash": source_hash,
            "raw_pixel_hash": normalization["raw_pixel_hash"],
            "exif_orientation": normalization["exif_orientation"],
            "orientation_policy": normalization["orientation_policy"],
            "canonical_visible_size": normalization["original_size"],
            "normalized_hash": file_sha256(normalized_path),
        },
        "normalized_path": normalized_path,
        "normalization": normalization,
        "generation_path": generation_path,
        "generation_raw_path": generation_path,
        "generation_crop": generation_crop,
        "generation_cleanup": {"version": "annotation-cleanup-v1", "applied_count": 0, "boxes": []},
        "generation_hash": generation_hash,
        "anchor_set": empty_anchor_set(source_hash, file_sha256(normalized_path)),
        "anchor_overlay_path": "",
        "anchor_overlay_hash": "",
        "anchor_verification": {"status": "not_run", "conflicts": [], "changes": [], "inferred_anchor_gaps": []},
        "structure_review": empty_structure_review(),
        "plan_summary": empty_plan_summary("human"),
        "plan_summary_confirmed": False,
        "brief": {"requirements_text": "", "reference_paths": [], "reference_hashes": []},
        "brief_hash": "",
        "paid_previews": {},
        "candidates": [],
        "bundles": [],
        "model_runs": [],
        "locked_candidate_id": "",
        "cancel_requested": False,
        "created_at": now,
        "updated_at": now,
    }
    save_project(project)
    return project


def empty_plan_summary(source: str = "human") -> dict:
    return {
        "version": "plan-summary-v1",
        "room_count": 0,
        "rooms": [],
        "declared_layout": {
            "bedrooms": 0, "halls": 0, "bathrooms": 0,
            "source_text": "", "confidence": 0.0,
        },
        "declared_area_m2": 0.0,
        "overall_dimensions_mm": {
            "width": 0, "depth": 0, "evidence": [], "confidence": 0.0,
        },
        "summary_confidence": 0.0,
        "review_items": [],
        "annotation_boxes": [],
        "entrances": [],
        "openings_summary": [],
        "wet_zones": [],
        "balconies": [],
        "dimension_evidence": [],
        "must_preserve": [],
        "uncertainties": [],
        "source": source,
        "prompt_version": PLAN_PROMPT_VERSION,
        "verification": {"status": "not_run", "conflicts": [], "changes": [], "inferred_anchor_gaps": []},
    }


def empty_anchor_set(source_hash: str = "", normalized_hash: str = "") -> dict:
    return {
        "version": "floorplan-anchors-v1",
        "coordinate_space": "normalized-evidence-1000-v1",
        "source_hash": source_hash,
        "normalized_hash": normalized_hash,
        "anchor_set_hash": "",
        "confirmed_complete": False,
        "anchors": [],
        "updated_at": 0.0,
    }


def _project_anchor_set_hash(project: dict) -> str:
    anchor_set = project.get("anchor_set") or {}
    try:
        from .tools.fastloop_research.contract import compute_anchor_set_hash
    except ImportError:
        from tools.fastloop_research.contract import compute_anchor_set_hash
    return compute_anchor_set_hash(
        str(anchor_set.get("coordinate_space") or ""),
        str(project.get("source_hash") or anchor_set.get("source_hash") or ""),
        file_sha256(project["normalized_path"]),
        list(anchor_set.get("anchors") or []),
    )


def empty_structure_review() -> dict:
    return {
        "version": "whole-home-structure-review-v1",
        "status": "not_run",
        "questions": [],
        "answers": {},
        "scale_calibration": None,
        "seed_graph": None,
        "structure_bundle": None,
        "structure_hash": "",
        "unresolved": [],
        "provider": "",
        "error": "",
        "updated_at": 0.0,
    }


def validate_anchor_set(project: dict, payload: dict) -> dict:
    if str(payload.get("coordinate_space") or "") != "normalized-evidence-1000-v1":
        raise ValueError("锚点坐标系必须是 normalized-evidence-1000-v1")
    if str(payload.get("source_hash") or "") != str(project.get("source_hash") or ""):
        raise ValueError("锚点对应的户型图已变化，请重新标注")
    rows = list(payload.get("anchors") or [])
    if len(rows) > 80:
        raise ValueError("锚点最多 80 个")
    normalized: list[dict] = []
    seen: set[str] = set()
    valid_kinds = {"space", "entrance", "opening", "fixed_feature", "ignore", "scale"}
    for index, raw in enumerate(rows, 1):
        row = dict(raw or {})
        anchor_id = re.sub(r"[^A-Za-z0-9_-]+", "", str(row.get("anchor_id") or f"P{index:02d}"))[:40]
        if not anchor_id or anchor_id in seen:
            raise ValueError("anchor_id 必须非空且唯一")
        seen.add(anchor_id)
        kind = str(row.get("kind") or "")
        if kind not in valid_kinds:
            raise ValueError(f"不支持的锚点类型: {kind}")
        label = re.sub(r"[\x00-\x1f]+", " ", str(row.get("label") or "")).strip()[:120]
        note = re.sub(r"[\x00-\x1f]+", " ", str(row.get("note") or "")).strip()[:500]
        if not label:
            raise ValueError(f"{anchor_id} 缺少人工标签")
        points = list(row.get("points") or [])
        expected = (2,) if kind == "scale" else (1, 2) if kind in {"entrance", "opening"} else (1,)
        if len(points) not in expected:
            raise ValueError(f"{anchor_id} 的点数不符合 {kind} 合同")
        clean_points = []
        for point in points:
            x, y = int(point.get("x", -1)), int(point.get("y", -1))
            if not (0 <= x <= 1000 and 0 <= y <= 1000):
                raise ValueError(f"{anchor_id} 坐标超出 0–1000")
            clean_points.append({"x": x, "y": y})
        if len(clean_points) == 2 and abs(clean_points[0]["x"] - clean_points[1]["x"]) + abs(clean_points[0]["y"] - clean_points[1]["y"]) < 8:
            raise ValueError(f"{anchor_id} 两点距离过短")
        distance_mm = None
        if kind == "scale":
            raw_distance = row.get("distance_mm")
            if isinstance(raw_distance, bool) or not isinstance(raw_distance, (int, float)):
                raise ValueError(f"{anchor_id} 缺少真实比例尺长度")
            distance_mm = round(float(raw_distance), 3)
            if not 10 <= distance_mm <= 1_000_000:
                raise ValueError(f"{anchor_id} 的比例尺长度必须在 10–1000000 mm")
        normalized_row = {"anchor_id": anchor_id, "kind": kind, "label": label, "note": note, "points": clean_points, "source": "human"}
        if distance_mm is not None:
            normalized_row["distance_mm"] = distance_mm
        normalized.append(normalized_row)
    if payload.get("confirmed_complete"):
        if not any(row["kind"] == "space" for row in normalized):
            raise ValueError("至少标注一个空间")
        if not any(row["kind"] == "entrance" for row in normalized):
            raise ValueError("至少标注一个入户门")
        scale_count = sum(row["kind"] == "scale" for row in normalized)
        if scale_count != 1:
            raise ValueError("必须且只能标注一条两点比例尺并填写真实长度")
    result = {
        "version": "floorplan-anchors-v1",
        "coordinate_space": "normalized-evidence-1000-v1",
        "source_hash": project["source_hash"],
        "normalized_hash": file_sha256(project["normalized_path"]),
        "confirmed_complete": bool(payload.get("confirmed_complete")),
        "anchors": normalized,
        "updated_at": time.time(),
    }
    try:
        from .tools.fastloop_research.contract import compute_anchor_set_hash
    except ImportError:
        from tools.fastloop_research.contract import compute_anchor_set_hash
    result["anchor_set_hash"] = compute_anchor_set_hash(
        result["coordinate_space"], result["source_hash"], result["normalized_hash"], result["anchors"]
    )
    return result


def _anchor_font(size: int):
    for path in ("C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_anchor_overlay(project: dict, anchor_set: dict) -> tuple[str, str]:
    with Image.open(project["normalized_path"]) as opened:
        source = opened.convert("RGB")
    legend_width = max(360, min(720, source.width // 2))
    canvas = Image.new("RGB", (source.width + legend_width, source.height), "white")
    canvas.paste(source, (0, 0))
    draw = ImageDraw.Draw(canvas)
    font_size = max(15, source.width // 75)
    small_size = max(12, source.width // 95)
    font = _anchor_font(font_size)
    small = _anchor_font(small_size)
    colors = {"space": "#0f766e", "entrance": "#dc2626", "opening": "#2563eb", "fixed_feature": "#7c3aed", "ignore": "#6b7280", "scale": "#d97706"}
    radius = max(9, source.width // 85)
    for index, anchor in enumerate(anchor_set.get("anchors") or []):
        color = colors.get(anchor["kind"], "#111827")
        pixels = [(round(p["x"] / 1000 * source.width), round(p["y"] / 1000 * source.height)) for p in anchor["points"]]
        if len(pixels) == 2:
            draw.line(pixels, fill=color, width=max(3, radius // 3))
        for part, (x, y) in enumerate(pixels):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="white", outline=color, width=max(3, radius // 4))
            suffix = chr(65 + part) if len(pixels) == 2 else ""
            draw.text((x + radius + 3, y - radius), f"{anchor['anchor_id']}{suffix}", fill=color, font=small, stroke_width=2, stroke_fill="white")
        y = 18 + index * max(34, source.height // max(12, len(anchor_set.get("anchors") or [])))
        legend = f"{anchor['anchor_id']} [{anchor['kind']}] {anchor['label']}"
        if anchor.get("distance_mm"):
            legend += f" · {anchor['distance_mm']:g} mm"
        draw.text((source.width + 18, y), legend[:70], fill=color, font=font)
        if anchor.get("note"):
            draw.text((source.width + 28, y + font_size + 2), str(anchor["note"])[:90], fill="#4b5563", font=small)
    folder = os.path.join(ASSET_ROOT, os.path.basename(project["project_id"]))
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, "floorplan-anchor-overlay.png")
    temporary = destination + ".tmp"
    canvas.save(temporary, "PNG", optimize=True)
    os.replace(temporary, destination)
    return destination, file_sha256(destination)


def normalize_floorplan(source_path: str, project_id: str, *, orientation_policy: str = "exif_transpose-v1") -> tuple[str, dict]:
    with Image.open(source_path) as opened:
        exif_orientation = int(opened.getexif().get(274, 1) or 1)
        raw = opened.convert("RGB")
        raw_pixel_hash = hashlib.sha256(raw.tobytes()).hexdigest()
        if orientation_policy == "exif_transpose-v1":
            image = ImageOps.exif_transpose(opened).convert("RGB")
        elif orientation_policy == "ignore_invalid_exif_user_confirmed_raw":
            image = raw
        else:
            raise ValueError(f"不支持的户型方向策略: {orientation_policy}")
    original_size = image.size
    # Trim only near-white page margin.  Dark architectural content is never
    # inferred or cropped by semantic heuristics.
    white = Image.new("RGB", image.size, "white")
    diff = ImageChops.difference(image, white).convert("L")
    mask = diff.point(lambda value: 255 if value > 12 else 0)
    bbox = mask.getbbox()
    cropped_box = (0, 0, image.width, image.height)
    if bbox:
        pad_x = max(8, int((bbox[2] - bbox[0]) * 0.025))
        pad_y = max(8, int((bbox[3] - bbox[1]) * 0.025))
        cropped_box = (
            max(0, bbox[0] - pad_x), max(0, bbox[1] - pad_y),
            min(image.width, bbox[2] + pad_x), min(image.height, bbox[3] + pad_y),
        )
        image = image.crop(cropped_box)
    ratio_value = image.width / max(1, image.height)
    ratio_label, target_ratio = min(SUPPORTED_RATIOS, key=lambda item: abs(item[1] - ratio_value))
    if ratio_value < target_ratio:
        canvas_size = (int(round(image.height * target_ratio)), image.height)
    else:
        canvas_size = (image.width, int(round(image.width / target_ratio)))
    canvas = Image.new("RGB", canvas_size, (248, 248, 246))
    offset = ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2)
    canvas.paste(image, offset)
    folder = os.path.join(ASSET_ROOT, os.path.basename(project_id))
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, "floorplan-normalized.png")
    temporary = destination + ".tmp"
    canvas.save(temporary, "PNG", optimize=True)
    os.replace(temporary, destination)
    return destination, {
        "version": "floorplan-normalization-v1",
        "original_size": list(original_size),
        "raw_pixel_hash": raw_pixel_hash,
        "exif_orientation": exif_orientation,
        "orientation_policy": orientation_policy,
        "cropped_box": list(cropped_box),
        "content_size": [image.width, image.height],
        "canvas_size": [canvas.width, canvas.height],
        "offset": list(offset),
        "aspect_ratio": ratio_label,
        "crop_policy": "near-white-margin-only",
        "padding_policy": "neutral-no-content-crop",
    }


def extract_generation_plan(source_path: str, project_id: str, *, orientation_policy: str = "exif_transpose-v1") -> tuple[str, dict]:
    """Extract the main architectural plan while excluding detached details.

    Evidence/OCR continues to use the normalized full sheet. Image generation
    receives this structural crop so cabinet details, captions and dimensions do
    not become invented rooms. Thick horizontal/vertical ink is used as the
    signal; thin dimension lines and text are intentionally ignored.
    """
    with Image.open(source_path) as opened:
        if orientation_policy == "exif_transpose-v1":
            image = ImageOps.exif_transpose(opened).convert("RGB")
        elif orientation_policy == "ignore_invalid_exif_user_confirmed_raw":
            image = opened.convert("RGB")
        else:
            raise ValueError(f"不支持的户型方向策略: {orientation_policy}")
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    binary = (gray < 80).astype(np.uint8) * 255
    short = min(image.width, image.height)
    long_kernel = max(25, int(round(short * 0.018)))
    thick_kernel = max(3, int(round(short * 0.0025)))
    dilate_kernel = max(9, int(round(short * 0.0075)))
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (long_kernel, thick_kernel)))
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (thick_kernel, long_kernel)))
    structure = cv2.bitwise_or(horizontal, vertical)
    structure = cv2.dilate(
        structure, cv2.getStructuringElement(cv2.MORPH_RECT, (dilate_kernel, dilate_kernel)),
        iterations=1)
    count, _, stats, _ = cv2.connectedComponentsWithStats(structure)
    minimum_area = max(500, int(image.width * image.height * 0.0006))
    minimum_side = max(35, int(short * 0.02))
    components: list[tuple[int, int, int, int, int]] = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if area >= minimum_area and width >= minimum_side and height >= minimum_side:
            components.append((x, y, width, height, area))
    fallback_reason = ""
    if components:
        left = min(row[0] for row in components)
        top = min(row[1] for row in components)
        right = max(row[0] + row[2] for row in components)
        bottom = max(row[1] + row[3] for row in components)
        width, height = right - left, bottom - top
        if width < image.width * 0.35 or height < image.height * 0.35:
            fallback_reason = "structural_bbox_too_small"
    else:
        left, top, right, bottom = 0, 0, image.width, image.height
        fallback_reason = "no_structural_components"
    if fallback_reason:
        left, top, right, bottom = 0, 0, image.width, image.height
    else:
        # Thick-wall detection intentionally ignores fine balcony/window lines.
        # Keep generous context around the structural cluster so the crop never
        # clips those valid edges while still excluding distant detail diagrams.
        pad_x = max(24, int((right - left) * 0.07))
        pad_y = max(24, int((bottom - top) * 0.07))
        left, top = max(0, left - pad_x), max(0, top - pad_y)
        right, bottom = min(image.width, right + pad_x), min(image.height, bottom + pad_y)
    crop = image.crop((left, top, right, bottom))
    source_ratio = crop.width / max(1, crop.height)
    ratio_label, target_ratio = min(SUPPORTED_RATIOS, key=lambda item: abs(item[1] - source_ratio))
    if source_ratio < target_ratio:
        canvas_size = (int(round(crop.height * target_ratio)), crop.height)
    else:
        canvas_size = (crop.width, int(round(crop.width / target_ratio)))
    canvas = Image.new("RGB", canvas_size, (248, 248, 246))
    offset = ((canvas.width - crop.width) // 2, (canvas.height - crop.height) // 2)
    canvas.paste(crop, offset)
    folder = os.path.join(ASSET_ROOT, os.path.basename(project_id))
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, "floorplan-generation.png")
    temporary = destination + ".tmp"
    canvas.save(temporary, "PNG", optimize=True)
    os.replace(temporary, destination)
    return destination, {
        "version": "generation-plan-crop-v1",
        "source_size": [image.width, image.height],
        "crop_box": [left, top, right, bottom],
        "content_size": [crop.width, crop.height],
        "canvas_size": [canvas.width, canvas.height],
        "offset": list(offset),
        "aspect_ratio": ratio_label,
        "structural_component_count": len(components),
        "fallback_reason": fallback_reason,
    }


def clean_generation_annotations(raw_path: str, project_id: str,
                                 boxes: list[dict]) -> tuple[str, dict]:
    """Erase model-located non-structural text from a black-on-white plan crop."""
    with Image.open(raw_path) as opened:
        image = opened.convert("RGB")
    applied: list[dict] = []
    for row in boxes[:200]:
        if not isinstance(row, dict) or not row.get("safe_to_erase"):
            continue
        confidence = max(0.0, min(1.0, float(row.get("confidence") or 0.0)))
        coords = list(row.get("box_2d") or [])
        if confidence < 0.5 or len(coords) != 4:
            continue
        try:
            y0, x0, y1, x1 = [max(0, min(1000, int(value))) for value in coords]
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        pad_x = max(3, int(image.width * 0.004))
        pad_y = max(3, int(image.height * 0.004))
        left = max(0, int(x0 / 1000 * image.width) - pad_x)
        top = max(0, int(y0 / 1000 * image.height) - pad_y)
        right = min(image.width, int((x1 / 1000) * image.width) + pad_x)
        bottom = min(image.height, int((y1 / 1000) * image.height) + pad_y)
        ImageDraw.Draw(image).rectangle((left, top, right, bottom), fill="white")
        applied.append({
            "label": str(row.get("label") or ""), "kind": str(row.get("kind") or "text"),
            "confidence": confidence, "box_2d": [y0, x0, y1, x1],
            "pixel_box": [left, top, right, bottom],
        })
    folder = os.path.join(ASSET_ROOT, os.path.basename(project_id))
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, "floorplan-generation-clean.png")
    temporary = destination + ".tmp"
    image.save(temporary, "PNG", optimize=True)
    os.replace(temporary, destination)
    return destination, {
        "version": "annotation-cleanup-v1",
        "applied_count": len(applied),
        "boxes": applied,
        "source_hash": file_sha256(raw_path),
        "clean_hash": file_sha256(destination),
    }


def _image_part(path: str) -> dict:
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"
    return {"inlineData": {"mimeType": mime, "data": encoded}}


def call_gemini_json(prompt: str, image_paths: list[str], schema: dict,
                     *, max_output_tokens: int = 7000) -> tuple[Optional[dict], Optional[str]]:
    cfg = load_config()
    key = str(cfg.get("gemini_api_key") or "").strip()
    if not key:
        return None, "未配置 Gemini API Key"
    model = str(cfg.get("design_vision_model") or get_text_models()[0])
    parts = [{"text": prompt}] + [_image_part(path) for path in image_paths]
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "maxOutputTokens": max_output_tokens,
            "temperature": 0.1,
        },
    }
    proxy = str(cfg.get("proxy") or "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        response = requests.post(url, json=payload, timeout=(30, 240), proxies=proxies,
                                 verify=bool(cfg.get("tls_verify", True)))
    except Exception as exc:
        # The URL contains the API key; exception text may echo that URL.
        return None, f"Gemini 结构化请求失败: {type(exc).__name__}"
    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message") or response.text[:600]
        except Exception:
            detail = response.text[:600]
        return None, f"Gemini HTTP {response.status_code}: {detail}"
    try:
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw), None
    except Exception as exc:
        return None, f"Gemini JSON 解析失败: {exc}"


_STRUCTURE_GRAPH_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "outer_boundary": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
        }, "required": ["x", "y"]}},
        "walls": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"},
            "a": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "b": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "thickness_m": {"type": "NUMBER"}, "height_m": {"type": "NUMBER"},
            "left_space_id": {"type": "STRING"}, "right_space_id": {"type": "STRING"},
            "confidence": {"type": "NUMBER"}, "evidence": {"type": "STRING"},
        }, "required": ["id", "a", "b", "thickness_m", "height_m", "left_space_id", "right_space_id", "confidence", "evidence"]}},
        "openings": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "kind": {"type": "STRING", "enum": ["entrance", "door", "window"]},
            "a": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "b": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "owning_wall_id": {"type": "STRING"}, "sill_m": {"type": "NUMBER"}, "head_m": {"type": "NUMBER"},
            "side_a_space_id": {"type": "STRING"}, "side_b_space_id": {"type": "STRING"},
            "swing_direction": {"type": "STRING", "enum": ["hinge_left", "hinge_right", "sliding", "double", "not_shown", "none"]},
            "confidence": {"type": "NUMBER"}, "evidence": {"type": "STRING"},
        }, "required": ["id", "kind", "a", "b", "owning_wall_id", "sill_m", "head_m", "side_a_space_id", "side_b_space_id", "swing_direction", "confidence", "evidence"]}},
        "adjacencies": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "space_a_id": {"type": "STRING"}, "space_b_id": {"type": "STRING"},
            "kind": {"type": "STRING", "enum": ["door", "open_passage"]}, "opening_id": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
        }, "required": ["id", "space_a_id", "space_b_id", "kind", "opening_id", "confidence"]}},
        "unresolved": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["outer_boundary", "walls", "openings", "adjacencies", "unresolved"],
}


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

    def point_distance_to_segment(point: list[float], a: list[float], b: list[float]) -> float:
        px, py = point
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def segment_metrics(anchor: dict, opening: dict) -> dict[str, Any]:
        human = [to_m(point) for point in anchor.get("points") or []]
        ai = [to_m(opening.get("a")), to_m(opening.get("b"))]
        if len(human) == 1:
            distance = point_distance_to_segment(human[0], ai[0], ai[1])
            return {"mode": "point", "status": "pass" if distance <= 0.05 + 1.0e-9 else "fail", "point_distance_m": round(distance, 6)}
        human_to_ai = max(point_distance_to_segment(point, ai[0], ai[1]) for point in human)
        ai_to_human = max(point_distance_to_segment(point, human[0], human[1]) for point in ai)
        hausdorff = max(human_to_ai, ai_to_human)
        human_length, ai_length = math.dist(*human), math.dist(*ai)
        length_error = abs(human_length - ai_length)
        length_ratio = length_error / max(human_length, 1.0e-9)
        hv, av = (human[1][0] - human[0][0], human[1][1] - human[0][1]), (ai[1][0] - ai[0][0], ai[1][1] - ai[0][1])
        cosine = max(-1.0, min(1.0, abs((hv[0] * av[0] + hv[1] * av[1]) / max(math.hypot(*hv) * math.hypot(*av), 1.0e-9))))
        angle = math.degrees(math.acos(cosine))
        passed = hausdorff <= 0.05 + 1.0e-9 and (length_error <= 0.05 + 1.0e-9 or length_ratio <= 0.05 + 1.0e-9) and angle <= 2.0 + 1.0e-9
        return {"mode": "segment", "status": "pass" if passed else "fail", "symmetric_hausdorff_m": round(hausdorff, 6), "length_error_m": round(length_error, 6), "length_error_ratio": round(length_ratio, 6), "angle_error_degrees": round(angle, 6)}

    raw_openings = list(seed.get("openings") or [])
    anchor_bindings: list[dict[str, str]] = []
    used_opening_ids: set[str] = set()
    for anchor in (project.get("anchor_set") or {}).get("anchors", []):
        if anchor.get("kind") not in {"entrance", "opening"}:
            continue
        candidates = [row for row in raw_openings if anchor["kind"] != "entrance" or row.get("kind") == "entrance"]
        checked = [(row, segment_metrics(anchor, row)) for row in candidates]
        matches = [(row, check) for row, check in checked if check["status"] == "pass"]
        if not matches:
            raise ValueError(f"人工{anchor['kind']}锚点 {anchor['anchor_id']} 未与任何 Gemini 开口几何对应")
        if len(matches) > 1:
            raise ValueError(f"人工{anchor['kind']}锚点 {anchor['anchor_id']} 同时匹配多个 Gemini 开口")
        matched, geometry_check = matches[0]
        opening_id = str(matched.get("id") or "")
        if not opening_id or opening_id in used_opening_ids:
            raise ValueError(f"人工开口锚点 {anchor['anchor_id']} 未与 Gemini 开口形成唯一对应")
        used_opening_ids.add(opening_id)
        anchor_bindings.append({
            "anchor_id": anchor["anchor_id"],
            "anchor_kind": anchor["kind"],
            "opening_id": opening_id,
        })

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

    # A buildable branch graph is atomic: every T/X intersection must be an
    # endpoint of every incident branch. Split Gemini's longer centerlines
    # deterministically before assigning openings to their owning child.
    split_parameters: dict[str, set[float]] = {wall["id"]: {0.0, 1.0} for wall in walls}
    for first_index, first in enumerate(walls):
        a, b = first["centerline_m"]
        rx, ry = b[0] - a[0], b[1] - a[1]
        for second in walls[first_index + 1:]:
            c, d = second["centerline_m"]
            sx, sy = d[0] - c[0], d[1] - c[1]
            denominator = rx * sy - ry * sx
            if abs(denominator) <= 1.0e-12:
                continue
            qx, qy = c[0] - a[0], c[1] - a[1]
            t = (qx * sy - qy * sx) / denominator
            u = (qx * ry - qy * rx) / denominator
            if -1.0e-9 <= t <= 1.0 + 1.0e-9 and -1.0e-9 <= u <= 1.0 + 1.0e-9:
                split_parameters[first["id"]].add(max(0.0, min(1.0, t)))
                split_parameters[second["id"]].add(max(0.0, min(1.0, u)))
    atomic_walls: list[dict[str, Any]] = []
    parent_segments: dict[str, list[str]] = {}
    for wall in walls:
        cuts = sorted(split_parameters[wall["id"]])
        a, b = wall["centerline_m"]
        pieces = []
        for piece_index, (start, end) in enumerate(zip(cuts, cuts[1:]), 1):
            if end - start <= 1.0e-9:
                continue
            piece = deepcopy(wall)
            piece["id"] = wall["id"] if len(cuts) == 2 else f"{wall['id']}.S{piece_index:02d}"
            piece["centerline_m"] = [
                [round(a[0] + (b[0] - a[0]) * start, 6), round(a[1] + (b[1] - a[1]) * start, 6)],
                [round(a[0] + (b[0] - a[0]) * end, 6), round(a[1] + (b[1] - a[1]) * end, 6)],
            ]
            atomic_walls.append(piece)
            pieces.append(piece["id"])
        parent_segments[wall["id"]] = pieces
    walls = atomic_walls
    wall_ids = {wall["id"] for wall in walls}

    try:
        from .tools.fastloop_research.contract import derive_wall_junctions, project_opening
    except ImportError:
        from tools.fastloop_research.contract import derive_wall_junctions, project_opening
    junctions = derive_wall_junctions(walls)
    wall_by_id = {wall["id"]: wall for wall in walls}

    def atomic_owner(parent_id: str, a_m: list[float], b_m: list[float]) -> str:
        candidates: list[str] = []
        for child_id in parent_segments.get(parent_id, []):
            child = wall_by_id[child_id]
            ca, cb = child["centerline_m"]
            dx, dy = cb[0] - ca[0], cb[1] - ca[1]
            length = math.hypot(dx, dy)
            tx, ty = dx / length, dy / length
            valid = True
            for point in (a_m, b_m):
                px, py = point[0] - ca[0], point[1] - ca[1]
                projection = px * tx + py * ty
                distance = abs(px * (-ty) + py * tx)
                if distance > 0.05 + 1.0e-9 or projection < -1.0e-9 or projection > length + 1.0e-9:
                    valid = False
            if valid:
                candidates.append(child_id)
        if len(candidates) != 1:
            raise ValueError(f"开口跨越原墙 {parent_id} 的原子分段，无法唯一归属")
        return candidates[0]

    def same_wall_support(owner: dict, opening: dict, *, before: bool) -> dict[str, Any]:
        projection = project_opening(owner, opening)
        margin = projection["start_m"] if before else projection["wall_length_m"] - projection["end_m"]
        if margin < 0.05 - 1.0e-9:
            raise ValueError(f"{opening['id']} 墙端余量不足 50mm，必须由专业结构图声明 return_wall_face 支承")
        return {
            "mode": "same_wall_margin", "supporting_wall_id": owner["id"],
            "junction_id": None, "face_distance_m": round(margin, 6),
            "effective_support_m": round(margin, 6),
            "provenance": "computed_from_confirmed_wall_axis",
            "solid_provenance": "owning_wall_continuous_solid",
        }

    openings = []
    opening_ids: set[str] = set()
    for index, raw in enumerate(seed.get("openings") or [], 1):
        opening_id = str(raw.get("id") or f"OPENING-{index:03d}")
        if opening_id in opening_ids:
            raise ValueError(f"开口 ID 重复: {opening_id}")
        opening_ids.add(opening_id)
        parent_owner = str(raw.get("owning_wall_id") or "")
        if parent_owner not in parent_segments:
            raise ValueError(f"{opening_id} 缺少有效所属墙")
        side_a, side_b = str(raw.get("side_a_space_id") or ""), str(raw.get("side_b_space_id") or "")
        if side_a not in known_spaces or side_b not in known_spaces or side_a == side_b:
            raise ValueError(f"{opening_id} 两侧空间无效")
        a_m, b_m = to_m(raw.get("a")), to_m(raw.get("b"))
        owner = atomic_owner(parent_owner, a_m, b_m)
        width = math.dist(a_m, b_m)
        kind = str(raw.get("kind") or "")
        swing = None if kind == "window" else str(raw.get("swing_direction") or "not_shown")
        opening = {
            "id": opening_id, "kind": kind, "owning_wall_id": owner, "segment_m": [a_m, b_m],
            "width_m": round(width, 6), "sill_m": round(float(raw.get("sill_m") or 0), 4),
            "head_m": round(float(raw.get("head_m") or 2.1), 4), "swing_direction": swing,
            "side_a_space_id": side_a, "side_b_space_id": side_b,
            "jamb_before_supported": True, "jamb_after_supported": True, "junction_clearance_m": 0.05,
            "junction_diagnostics": [], "confirmed": True, "source": "gemini_inferred",
        }
        opening["jamb_before_support"] = same_wall_support(wall_by_id[owner], opening, before=True)
        opening["jamb_after_support"] = same_wall_support(wall_by_id[owner], opening, before=False)
        openings.append(opening)

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
        "source": {
            "schema": "source-provenance-v2",
            "source_file_hash": project["source_hash"],
            "normalized_hash": file_sha256(project["normalized_path"]),
            "raw_pixel_hash": str((project.get("source_orientation") or {}).get("raw_pixel_hash") or project["source_hash"]),
            "exif_orientation": int((project.get("source_orientation") or {}).get("exif_orientation") or 1),
            "orientation_policy": str((project.get("source_orientation") or {}).get("orientation_policy") or "exif_transpose-v1"),
            "canonical_visible_size": list((project.get("source_orientation") or {}).get("canonical_visible_size") or (project.get("normalization") or {}).get("original_size") or [1, 1]),
            "coordinate_space": "normalized-evidence-1000-v1",
            "normalized_to_metric_3x3": [
                [round(canvas_w / 1000.0 * mpp, 12), 0.0, round(-origin_x * mpp, 12)],
                [0.0, round(-canvas_h / 1000.0 * mpp, 12), round(origin_y * mpp, 12)],
                [0.0, 0.0, 1.0],
            ],
            "anchor_set_hash": _project_anchor_set_hash(project),
            "scale_anchor_id": calibration["anchor_id"],
            "anchors": [
                {
                    "anchor_id": anchor["anchor_id"], "kind": anchor["kind"],
                    "points_norm": [[float(point["x"]), float(point["y"])] for point in anchor.get("points") or []],
                    "points_metric_m": [to_m(point) for point in anchor.get("points") or []],
                    "distance_mm": float(anchor["distance_mm"]) if anchor.get("kind") == "scale" else None,
                }
                for anchor in (project.get("anchor_set") or {}).get("anchors", [])
            ],
            "anchor_opening_bindings": anchor_bindings,
        },
        "project": {"id": project["project_id"], "revision": project["revision"]},
        "source_hash": project["source_hash"],
        "structure_hash": "0" * 64,
        "outer_boundary_m": [to_m({"x": x, "y": y}) for x, y in boundary_norm],
        "spaces": spaces,
        "wall_branch_graph": {"version": "wall-branch-graph-v1", "walls": walls, "junctions": junctions},
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
    unresolved = list(review.get("unresolved") or [])
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
            source = bundle.get("source") or {}
            orientation = project.get("source_orientation") or {}
            expected_source = {
                "source_file_hash": project.get("source_hash"),
                "raw_pixel_hash": orientation.get("raw_pixel_hash"),
                "exif_orientation": orientation.get("exif_orientation"),
                "orientation_policy": orientation.get("orientation_policy"),
                "canonical_visible_size": orientation.get("canonical_visible_size"),
                "normalized_hash": file_sha256(project["normalized_path"]),
            }
            mismatched = [key for key, expected in expected_source.items() if source.get(key) != expected]
            if mismatched:
                raise ValueError(f"专业结构包来源方向/像素证据不属于当前项目: {mismatched}")
            from .tools.fastloop_research.contract import canonical_anchor_geometry_payload, compute_anchor_set_hash
            current_anchor_set = project.get("anchor_set") or {}
            current_anchor_hash = compute_anchor_set_hash(
                str(current_anchor_set.get("coordinate_space") or ""), project["source_hash"],
                file_sha256(project["normalized_path"]), list(current_anchor_set.get("anchors") or []),
            )
            bundle_anchor_hash = compute_anchor_set_hash(
                str(source.get("coordinate_space") or ""), str(source.get("source_file_hash") or ""),
                str(source.get("normalized_hash") or ""), list(source.get("anchors") or []),
            )
            current_payload = canonical_anchor_geometry_payload(
                str(current_anchor_set.get("coordinate_space") or ""), project["source_hash"],
                file_sha256(project["normalized_path"]), list(current_anchor_set.get("anchors") or []),
            )
            bundle_payload = canonical_anchor_geometry_payload(
                str(source.get("coordinate_space") or ""), str(source.get("source_file_hash") or ""),
                str(source.get("normalized_hash") or ""), list(source.get("anchors") or []),
            )
            if source.get("anchor_set_hash") != current_anchor_hash or bundle_anchor_hash != current_anchor_hash or bundle_payload != current_payload:
                raise ValueError("专业结构包人工锚点几何不属于当前项目")
            from .tools.fastloop_research import compute_structure_hash, validate_bundle
            bundle["structure_hash"] = compute_structure_hash(bundle)
            validate_bundle(bundle)
        else:
            bundle = _compile_structure_bundle(project, review["seed_graph"])
    except (ValueError, OSError) as exc:
        unresolved.append(f"技术结构合同未通过：{exc}")
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
        unresolved=unresolved,
        updated_at=time.time(),
    )
    project["structure_review"] = review
    return review


_PLAN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "room_count": {"type": "INTEGER"},
        "rooms": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "label": {"type": "STRING"},
            "room_type": {"type": "STRING"}, "coarse_location": {"type": "STRING"},
            "adjacent_room_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "anchor_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "source": {"type": "STRING", "enum": ["human_anchor", "gemini_inferred"]},
            "confidence": {"type": "NUMBER"}, "evidence": {"type": "STRING"},
            "needs_confirmation": {"type": "BOOLEAN"},
        }, "required": ["id", "label", "room_type", "coarse_location", "adjacent_room_ids",
                         "anchor_ids", "source", "confidence", "evidence", "needs_confirmation"]}},
        "declared_layout": {"type": "OBJECT", "properties": {
            "bedrooms": {"type": "INTEGER"}, "halls": {"type": "INTEGER"},
            "bathrooms": {"type": "INTEGER"}, "source_text": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
        }, "required": ["bedrooms", "halls", "bathrooms", "source_text", "confidence"]},
        "declared_area_m2": {"type": "NUMBER"},
        "overall_dimensions_mm": {"type": "OBJECT", "properties": {
            "width": {"type": "INTEGER"}, "depth": {"type": "INTEGER"},
            "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
            "confidence": {"type": "NUMBER"},
        }, "required": ["width", "depth", "evidence", "confidence"]},
        "summary_confidence": {"type": "NUMBER"},
        "review_items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "kind": {"type": "STRING"},
            "label": {"type": "STRING"}, "evidence": {"type": "STRING"},
            "confidence": {"type": "NUMBER"}, "status": {"type": "STRING"},
        }, "required": ["id", "kind", "label", "evidence", "confidence", "status"]}},
        "annotation_boxes": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "label": {"type": "STRING"}, "kind": {"type": "STRING"},
            "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            "confidence": {"type": "NUMBER"}, "safe_to_erase": {"type": "BOOLEAN"},
        }, "required": ["label", "kind", "box_2d", "confidence", "safe_to_erase"]}},
        "entrances": {"type": "ARRAY", "items": {"type": "STRING"}},
        "openings_summary": {"type": "ARRAY", "items": {"type": "STRING"}},
        "wet_zones": {"type": "ARRAY", "items": {"type": "STRING"}},
        "balconies": {"type": "ARRAY", "items": {"type": "STRING"}},
        "dimension_evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
        "must_preserve": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "verification": {"type": "OBJECT", "properties": {
            "status": {"type": "STRING"},
            "conflicts": {"type": "ARRAY", "items": {"type": "STRING"}},
            "changes": {"type": "ARRAY", "items": {"type": "STRING"}},
            "inferred_anchor_gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": ["status", "conflicts", "changes", "inferred_anchor_gaps"]},
    },
    "required": ["room_count", "rooms", "declared_layout", "declared_area_m2",
                 "overall_dimensions_mm", "summary_confidence", "review_items", "annotation_boxes", "entrances",
                 "openings_summary", "wet_zones", "balconies", "dimension_evidence",
                 "must_preserve", "uncertainties", "verification"],
}


def normalize_plan_rooms(raw_rooms: list[dict]) -> list[dict]:
    """Sanitize AI rows without inventing rooms, roles, or reciprocal adjacency."""
    rooms: list[dict] = []
    id_map: dict[str, str] = {}
    used: set[str] = set()
    for index, raw in enumerate(raw_rooms[:80], 1):
        row = dict(raw or {})
        original = str(row.get("id") or f"room_{index}").strip()
        safe = re.sub(r"[^a-z0-9_-]+", "_", original.lower()).strip("_-") or f"room_{index}"
        base = safe
        suffix = 2
        while safe in used:
            safe = f"{base}_{suffix}"
            suffix += 1
        used.add(safe)
        id_map.setdefault(original, safe)
        id_map.setdefault(original.lower(), safe)
        row["id"] = safe
        rooms.append(row)
    known = {row["id"] for row in rooms}
    for room in rooms:
        mapped: list[str] = []
        for raw_adjacent in room.get("adjacent_room_ids") or []:
            original = str(raw_adjacent or "").strip()
            adjacent = id_map.get(original) or id_map.get(original.lower())
            if not adjacent:
                adjacent = re.sub(r"[^a-z0-9_-]+", "_", original.lower()).strip("_-")
            if not adjacent or adjacent == room["id"]:
                continue
            if adjacent in known and adjacent not in mapped:
                mapped.append(adjacent)
        room["adjacent_room_ids"] = mapped
    for room in rooms:
        room["anchor_ids"] = [str(value) for value in (room.get("anchor_ids") or []) if str(value)]
        room["source"] = "human_anchor" if room.get("source") == "human_anchor" else "gemini_inferred"
    return rooms[:80]


def anchor_summary_conflicts(anchor_set: dict, summary: dict) -> list[str]:
    rooms = list(summary.get("rooms") or [])
    conflicts: list[str] = []
    for anchor in anchor_set.get("anchors") or []:
        if anchor.get("kind") != "space":
            continue
        matches = [room for room in rooms if anchor.get("anchor_id") in (room.get("anchor_ids") or [])]
        if len(matches) != 1:
            conflicts.append(f"{anchor.get('anchor_id')} {anchor.get('label')} 未唯一对应一个空间")
        elif str(matches[0].get("label") or "").strip() != str(anchor.get("label") or "").strip():
            conflicts.append(f"{anchor.get('anchor_id')} 人工标签被改写：{anchor.get('label')} → {matches[0].get('label')}")
        elif matches[0].get("source") != "human_anchor":
            conflicts.append(f"{anchor.get('anchor_id')} 未保留 human_anchor 来源")
    return conflicts


def analyze_plan(project_id: str) -> dict:
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise KeyError(project_id)
        anchors = project.get("anchor_set") or {}
        if not anchors.get("confirmed_complete") or not project.get("anchor_overlay_path"):
            project.update(status="needs_anchor_review", stage="请先完成全部空间和入户门锚点", error="锚点尚未确认")
            save_project(project)
            return project
        project.update(status="analyzing_plan", stage="Gemini 正在根据人工锚点读取户型", error="")
        save_project(project)
        normalized = project["normalized_path"]
        generation_raw = project.get("generation_raw_path") or project.get("generation_path") or normalized
        overlay = project["anchor_overlay_path"]
        anchor_json = json.dumps(anchors, ensure_ascii=False, sort_keys=True)
    prompt = f"""Read this residential floor plan using human anchors as hard facts.
Image 1 is the complete clean evidence sheet. Image 2 is the same evidence with numbered human anchors and a legend.
Image 3 is the cropped generation plan. Anchor JSON follows:
{anchor_json}

Rules: preserve every human label verbatim. Attach space anchor_ids to matching room rows; use entrance/opening/
fixed_feature/ignore anchors in their corresponding summary fields and never turn them into fake rooms. Never move,
rename, or silently discard a human anchor. Infer walls and room extents from pixels, not marker radius. You may add clearly
visible unmarked spaces with source=gemini_inferred; anchored rooms use source=human_anchor. Return conflicts when
an anchor appears outside the plan or contradicts visible evidence. Read title, area, exact dimensions, entrance,
openings, balconies, wet zones and level differences conservatively. Do not invent geometry. annotation_boxes use
0..1000 coordinates against Image 3 and may cover safe non-structural text only. Set verification.status=draft."""
    first, first_error = call_gemini_json(prompt, [normalized, overlay, generation_raw], _PLAN_SCHEMA)
    if first:
        with _project_lock(project_id):
            current = load_project(project_id) or {}
            current.update(status="verifying_plan", stage="Gemini 正在独立复核锚点与户型摘要")
            save_project(current)
        verify_prompt = f"""Act as an independent adversarial floor-plan verifier.
Images 1-3 and anchor JSON are authoritative exactly as in the extraction pass. Audit the draft JSON below, correct
wrong room roles, missing/invalid adjacency, title-area-dimension contradictions and unsupported inferred spaces.
Human anchor labels and coordinates are immutable. Keep source=human_anchor for anchored rooms and
source=gemini_inferred for additions. List every correction in verification.changes, every human-anchor problem in
verification.conflicts, and likely missing manual anchors in verification.inferred_anchor_gaps. Return the complete
corrected schema with verification.status=verified when no conflicts, otherwise conflict.

ANCHORS:
{anchor_json}

DRAFT:
{json.dumps(first, ensure_ascii=False)}"""
        payload, verify_error = call_gemini_json(verify_prompt, [normalized, overlay, generation_raw], _PLAN_SCHEMA)
    else:
        payload, verify_error = None, None
    with _project_lock(project_id):
        project = load_project(project_id) or {}
        if payload:
            summary = empty_plan_summary("gemini_verified")
            for key in summary:
                if key in payload:
                    summary[key] = payload[key]
            summary["room_count"] = max(0, min(80, int(summary.get("room_count") or 0)))
            summary["rooms"] = normalize_plan_rooms(list(summary.get("rooms") or []))
            summary["room_count"] = len(summary["rooms"])
            summary["summary_confidence"] = max(
                0.0, min(1.0, float(summary.get("summary_confidence") or 0.0)))
            for room in summary["rooms"]:
                room["confidence"] = max(0.0, min(1.0, float(room.get("confidence") or 0.0)))
                room["needs_confirmation"] = bool(room.get("needs_confirmation"))
            review_items = list(summary.get("review_items") or [])[:100]
            reviewed_room_ids = {
                str(item.get("room_id") or "") for item in review_items if isinstance(item, dict)
            }
            for room in summary["rooms"]:
                room_id = str(room.get("id") or "")
                if room.get("needs_confirmation") and room_id not in reviewed_room_ids:
                    review_items.append({
                        "id": f"room_{room_id}", "kind": "room_role",
                        "room_id": room_id,
                        "label": f"确认空间角色：{room.get('label') or room_id}",
                        "evidence": str(room.get("evidence") or "模型将该空间标记为待确认"),
                        "confidence": room.get("confidence") or 0.0,
                        "status": "needs_confirmation",
                    })
            summary["review_items"] = review_items[:100]
            summary["annotation_boxes"] = list(summary.get("annotation_boxes") or [])[:200]
            verification = dict(summary.get("verification") or {})
            verification["conflicts"] = list(verification.get("conflicts") or []) + anchor_summary_conflicts(anchors, summary)
            verification["status"] = "conflict" if verification.get("conflicts") else "verified"
            verification["conflicts"] = [str(v) for v in verification.get("conflicts") or []][:100]
            verification["changes"] = [str(v) for v in verification.get("changes") or []][:100]
            verification["inferred_anchor_gaps"] = [str(v) for v in verification.get("inferred_anchor_gaps") or []][:100]
            summary["verification"] = verification
            previous_generation_hash = str(project.get("generation_hash") or "")
            try:
                clean_path, cleanup = clean_generation_annotations(
                    generation_raw, project_id, summary["annotation_boxes"])
                project["generation_raw_path"] = generation_raw
                project["generation_path"] = clean_path
                project["generation_cleanup"] = cleanup
                project["generation_hash"] = cleanup["clean_hash"]
                if previous_generation_hash and previous_generation_hash != project["generation_hash"]:
                    mark_candidates_stale(project, "生成结构图文字清理结果已更新")
            except Exception as exc:
                logger.warning("[全屋设计] 结构图文字清理失败 project=%s: %s", project_id, exc)
                project["generation_path"] = generation_raw
                project["generation_hash"] = file_sha256(generation_raw)
                project["generation_cleanup"] = {
                    "version": "annotation-cleanup-v1", "applied_count": 0,
                    "boxes": [], "error": type(exc).__name__,
                }
            project["brief_hash"] = _brief_hash(project)
            project["plan_summary"] = summary
            project["anchor_verification"] = verification
            project["stage"] = "请修正锚点冲突" if verification["conflicts"] else "请确认 Gemini 双重验证摘要"
            project["status"] = "needs_anchor_review" if verification["conflicts"] else "needs_plan_review"
            project["error"] = "；".join(verification["conflicts"][:3])
        else:
            project["plan_summary"] = empty_plan_summary("human")
            project["anchor_verification"] = {"status": "unverified", "conflicts": [], "changes": [], "inferred_anchor_gaps": []}
            project["stage"] = "Gemini 双重验证失败，请重试"
            project["status"] = "needs_anchor_review"
            project["error"] = verify_error or first_error or "Gemini 双重验证不可用"
        save_project(project)
        return project


def build_design_prompt(project: dict, *, phase: str, direction_index: int = 1,
                        refinement_text: str = "") -> str:
    plan = project.get("plan_summary") or {}
    brief = project.get("brief") or {}
    anchors = project.get("anchor_set") or {}
    room_lines = [
        f"- {row.get('id')}: {row.get('label')} / {row.get('room_type')} / "
        f"{row.get('coarse_location')} / adjacent={','.join(row.get('adjacent_room_ids') or [])}"
        for row in plan.get("rooms") or []
    ]
    phase_rule = (
        f"Create design direction {direction_index} as a distinct but faithful interpretation of the same brief."
        if phase == "draft" else
        "Refine the selected draft into a polished final image. Improve material realism, furniture detail, "
        "lighting and edge quality only; do not redesign or move architectural structure."
    )
    return f"""Create a clean, photorealistic whole-home interior design concept from the supplied floor plan.

OUTPUT CONTRACT:
- Strict vertical overhead orthographic view, roof removed, 2.5D walls with restrained height and natural shadows.
- Preserve the exact plan orientation, exterior footprint, partitions, room count, adjacency, entrance,
  balconies, kitchen/bath wet zones and all major doors/windows from Image 1.
- Never crop, rotate, mirror, widen, narrow, add or remove any architectural space.
- Furnish every usable room plausibly while keeping doors, circulation and fixed wet zones clear.
- Clean neutral background. No labels, room names, dimensions, legends, UI, watermark or inset source plan.
- This is a marketing concept image, not a new floor plan and not a construction drawing.

CONFIRMED LIGHTWEIGHT PLAN SUMMARY:
declared_layout={plan.get('declared_layout') or {}}
declared_area_m2={plan.get('declared_area_m2') or 0}
overall_dimensions_mm={plan.get('overall_dimensions_mm') or {}}
room_count={plan.get('room_count') or 0}
rooms:
{chr(10).join(room_lines) or '- Refer to Image 1; human summary contains no room rows.'}
entrances={plan.get('entrances') or []}
balconies={plan.get('balconies') or []}
wet_zones={plan.get('wet_zones') or []}
openings={plan.get('openings_summary') or []}
must_preserve={plan.get('must_preserve') or []}

HUMAN ANCHOR CONTRACT (0..1000 on the clean normalized evidence image):
{json.dumps(anchors.get('anchors') or [], ensure_ascii=False)}
Image 1 is the clean structural generation plan. Image 2 is the numbered human-anchor semantic guide.
Use Image 2 to understand human-confirmed labels and locations, but NEVER copy marker circles, IDs, lines,
legend text, coordinates, or annotations into the output.

USER DESIGN REQUIREMENTS:
{brief.get('requirements_text') or ''}

PHASE:
{phase_rule}
{('ADDITIONAL REFINEMENT REQUEST: ' + refinement_text) if refinement_text else ''}

Image 1 is always the structural authority. Image 2 is semantic guidance; later images are appearance references."""


def _fal_endpoint(model_id: str) -> str:
    cfg = load_config()
    custom = cfg.get("fal_model_map") if isinstance(cfg.get("fal_model_map"), dict) else {}
    endpoint = custom.get(model_id) or FAL_MODEL_MAP.get(model_id)
    if not endpoint:
        raise ValueError(f"没有为模型配置 Fal edit endpoint: {model_id}")
    return str(endpoint)


def call_design_image(*, model_id: str, prompt: str, image_paths: list[str],
                      resolution: str, aspect_ratio: str, should_cancel: Callable[[], bool],
                      on_stage: Callable[[str], None], on_submitted: Callable[[dict], None],
                      resume_handle: Optional[dict] = None) -> tuple[Optional[Image.Image], Optional[str], str, str]:
    cfg = load_config()
    provider = str(cfg.get("image_provider") or "google").strip().lower()
    if provider == "fal":
        key = str(cfg.get("fal_api_key") or "").strip()
        if not key:
            return None, "未配置 Fal API Key", "fal", ""
        try:
            endpoint = _fal_endpoint(model_id)
        except ValueError as exc:
            return None, str(exc), "fal", ""
        uris = [_file_to_data_uri(path) for path in image_paths]
        if not all(uris):
            return None, "设计参考图不存在", "fal", endpoint
        payload = {
            "prompt": prompt,
            "image_urls": uris,
            "num_images": 1,
            "output_format": "png",
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "sync_mode": False,
            "limit_generations": True,
        }
        data, error = _call_fal_queue_json(
            key, endpoint, payload, on_stage=on_stage, should_cancel=should_cancel,
            resume_handle=resume_handle, on_submitted=on_submitted,
        )
        if data is None:
            return None, error or "Fal 设计任务失败", "fal", endpoint
        image, decode_error = _fal_image_from_result(
            data, plural=True, direct=False, on_stage=on_stage, should_cancel=should_cancel)
        return image, decode_error, "fal", endpoint
    key = str(cfg.get("gemini_api_key") or "").strip()
    if not key:
        return None, "未配置 Gemini API Key", "google", ""
    image, error = call_gemini_generate(
        key, model_id, prompt, image_paths[0], resolution, aspect_ratio,
        on_stage=on_stage, should_cancel=should_cancel, input_image_paths=image_paths,
    )
    return image, error, "google", ""


def _save_candidate_image(project_id: str, candidate_id: str, image: Image.Image) -> str:
    folder = os.path.join(ASSET_ROOT, os.path.basename(project_id), "candidates")
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, f"{os.path.basename(candidate_id)}.png")
    fd, temporary = tempfile.mkstemp(prefix=".candidate_", suffix=".png", dir=folder)
    os.close(fd)
    try:
        image.convert("RGB").save(temporary, "PNG", optimize=True)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return destination


_QA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hard_fail": {"type": "BOOLEAN"},
        "summary": {"type": "STRING"},
        "checks": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "check_id": {"type": "STRING"},
            "status": {"type": "STRING", "enum": ["pass", "fail", "uncertain"]},
            "evidence": {"type": "STRING"},
        }, "required": ["check_id", "status", "evidence"]}},
    },
    "required": ["hard_fail", "summary", "checks"],
}


def evaluate_structure(project: dict, candidate_path: str, extra_image_paths: Optional[list[str]] = None) -> dict:
    cfg = load_config()
    if not str(cfg.get("gemini_api_key") or "").strip():
        return {
            "version": QA_PROMPT_VERSION,
            "status": "manual_required",
            "hard_fail": False,
            "summary": "没有 Gemini Key；必须人工完成全部结构核对。",
            "checks": [],
            "provider": "human_required",
        }
    anchors = project.get("anchor_set") or {}
    prompt = f"""You are an adversarial architectural QA reviewer.
Image 1 is the authoritative clean floor plan. Image 2 is the human numbered-anchor guide. Image 3 is the generated top view.
When Images 4 and 5 are present, they are the north-east and north-west Blender axonometric views; use them to catch
wall-height, opening, junction and connectivity contradictions that a top view can hide.
Human anchors are hard facts. A missing, moved, renamed, or wrong-role anchored space is a hard failure. Any copied
marker ID, point, direction line, legend, or coordinate in Image 3 is a hard failure.
ANCHORS={json.dumps(anchors.get('anchors') or [], ensure_ascii=False)}
Compare them, using this confirmed summary as supporting evidence only:
{json.dumps(project.get('plan_summary') or {}, ensure_ascii=False)}

Return one check for every ID below:
{', '.join(STRUCTURE_REVIEW_ITEMS)}
Mark fail for any changed orientation/crop, exterior footprint, room count/location, partition/adjacency,
entrance/balcony/major opening, kitchen/bath wet-zone location, added/missing space, non-orthographic view,
or generated labels/dimensions/watermarks. Uncertainty in any architectural check is a hard failure.
A beautiful image with altered structure must fail."""
    structure_source = project.get("generation_path") or project["normalized_path"]
    image_paths = [structure_source]
    if project.get("anchor_overlay_path"):
        image_paths.append(project["anchor_overlay_path"])
    image_paths.append(candidate_path)
    image_paths.extend(path for path in (extra_image_paths or []) if path and os.path.isfile(path))
    payload, error = call_gemini_json(prompt, image_paths, _QA_SCHEMA)
    if not payload:
        return {
            "version": QA_PROMPT_VERSION,
            "status": "manual_required",
            "hard_fail": False,
            "summary": error or "自动 QA 不可用；必须人工核对。",
            "checks": [],
            "provider": "gemini_unavailable",
        }
    checks = list(payload.get("checks") or [])
    by_id = {str(row.get("check_id")): row for row in checks if isinstance(row, dict)}
    normalized_checks = []
    hard_fail = bool(payload.get("hard_fail"))
    for check_id in STRUCTURE_REVIEW_ITEMS:
        row = by_id.get(check_id) or {
            "check_id": check_id, "status": "uncertain", "evidence": "模型漏答",
        }
        if row.get("status") != "pass":
            hard_fail = True
        normalized_checks.append(row)
    return {
        "version": QA_PROMPT_VERSION,
        "status": "failed" if hard_fail else "passed",
        "hard_fail": hard_fail,
        "summary": str(payload.get("summary") or ""),
        "checks": normalized_checks,
        "provider": "gemini",
    }


def public_project(project: dict, *, list_mode: bool = False) -> dict:
    row = deepcopy(project)
    row["anchor_set"] = row.get("anchor_set") or empty_anchor_set(
        str(project.get("source_hash") or ""),
        file_sha256(project["normalized_path"]) if project.get("normalized_path") and os.path.isfile(project["normalized_path"]) else "",
    )
    row["anchor_verification"] = row.get("anchor_verification") or {
        "status": "not_run", "conflicts": [], "changes": [], "inferred_anchor_gaps": [],
    }
    row["structure_review"] = row.get("structure_review") or empty_structure_review()
    row["structure_review"].pop("seed_graph", None)
    row["structure_review"].pop("structure_bundle", None)
    row["model_runs"] = row.get("model_runs") or []
    row["source_url"] = to_url(project.get("source_path"))
    row["normalized_url"] = to_url(project.get("normalized_path"))
    row["generation_url"] = to_url(project.get("generation_path"))
    row["anchor_overlay_url"] = to_url(project.get("anchor_overlay_path"))
    row["legacy_unanchored"] = not bool((project.get("anchor_set") or {}).get("confirmed_complete"))
    for candidate in row.get("candidates") or []:
        candidate["url"] = to_url(candidate.get("path"))
        candidate["thumb"] = result_thumb_url(candidate.get("path")) if candidate.get("path") else ""
        candidate.pop("queue_handle", None)
        candidate.pop("prompt", None)
    if isinstance(row.get("brief"), dict):
        row["brief"]["reference_paths"] = []
    for bundle in row.get("bundles") or []:
        bundle["download_url"] = (
            f"/api/whole-home-design/projects/{project['project_id']}/bundles/{bundle['bundle_id']}"
        )
        bundle.pop("path", None)
    for model_run in row.get("model_runs") or []:
        for artifact in model_run.get("artifacts") or []:
            artifact["download_url"] = (
                f"/api/whole-home-design/projects/{project['project_id']}/model-runs/"
                f"{model_run['run_id']}/artifacts/{artifact['kind']}"
            )
            artifact.pop("path", None)
        model_run.pop("output_root", None)
        model_run.pop("structure_bundle", None)
        model_run.pop("idempotency_key", None)
        model_run.pop("background_started_at", None)
        report = model_run.get("mechanical_report") or {}
        model_run["mechanical_report"] = {
            "schema": report.get("schema"), "status": report.get("status"),
            "structure_hash": report.get("structure_hash"), "checks": list(report.get("checks") or []),
            "blend_status": ((report.get("blender") or {}).get("blend") or {}).get("status"),
            "glb_status": ((report.get("blender") or {}).get("glb") or {}).get("status"),
            "ifc_status": (report.get("ifc") or {}).get("status"),
        }
    for preview in (row.get("paid_previews") or {}).values():
        preview.pop("confirmation_phrase", None)
    if list_mode:
        for key in ("paid_previews", "brief", "plan_summary", "anchor_set", "anchor_verification", "structure_review", "model_runs"):
            row.pop(key, None)
        row["candidates"] = [candidate for candidate in row.get("candidates") or [] if candidate.get("path")]
    row.pop("source_path", None)
    row.pop("normalized_path", None)
    row.pop("generation_path", None)
    row.pop("generation_raw_path", None)
    row.pop("anchor_overlay_path", None)
    return row


def recover_interrupted_projects() -> list[tuple[str, str]]:
    resumable: list[tuple[str, str]] = []
    for project in list_projects(200):
        changed = False
        project_has_resume = False
        for candidate in project.get("candidates") or []:
            if candidate.get("status") in ("queued", "running"):
                handle = candidate.get("queue_handle") or {}
                if candidate.get("provider") == "fal" and handle.get("request_id"):
                    candidate["status"] = "interrupted"
                    candidate["stage"] = "等待恢复已有 Fal 队列任务"
                    resumable.append((project["project_id"], candidate["candidate_id"]))
                    project_has_resume = True
                else:
                    candidate["status"] = "failed"
                    candidate["error"] = "程序重启时调用状态未知；未自动重新付费提交"
                changed = True
        for model_run in project.get("model_runs") or []:
            if model_run.get("status") in {"queued", "building"}:
                model_run["status"] = "interrupted"
                model_run["stage"] = "程序重启中断了本地 Blender 子进程；可以重新生成，不会重复付费"
                model_run["error"] = ""
                model_run["stale"] = True
                model_run["stale_reason"] = "程序重启中断了本地建模子进程"
                model_run["updated_at"] = time.time()
                changed = True
        if project.get("status") in ("generating_drafts", "refining"):
            project["status"] = "interrupted" if project_has_resume else "failed"
            changed = True
        if changed:
            save_project(project)
    return resumable


def create_model_run_record(project: dict) -> dict:
    review = project.get("structure_review") or {}
    if review.get("status") != "verified" or not review.get("structure_bundle") or not review.get("structure_hash"):
        raise ValueError("九问和技术结构图尚未通过研究建模门")
    structure_hash = str(review["structure_hash"])
    existing = next((row for row in reversed(project.get("model_runs") or [])
                     if row.get("structure_hash") == structure_hash and not row.get("stale")
                     and row.get("status") not in {"failed_product", "cancelled", "interrupted"}), None)
    if existing:
        return existing
    run_id = new_id("model")
    now = time.time()
    row = {
        "run_id": run_id,
        "status": "queued",
        "stage": "等待本地 Blender 研究建模",
        "error": "",
        "structure_hash": structure_hash,
        "structure_bundle": deepcopy(review["structure_bundle"]),
        "output_root": os.path.join(MODEL_ROOT, run_id[-6:]),
        "score": None,
        "artifacts": [],
        "unresolved": list(review.get("unresolved") or []),
        "mechanical_report": {},
        "gemini_review": {"version": QA_PROMPT_VERSION, "status": "not_run", "hard_fail": False, "summary": "", "checks": [], "provider": ""},
        "loop": {
            "version": "goal-loop-v2", "status": "not_started", "attempt": 0,
            "max_attempts": 2, "method_id": "deterministic-contract-v2",
            "mechanical_score": None, "gemini_score": None, "total_score": None,
            "score_delta": None, "hard_gates": [], "repair_history": [],
            "next_action": "从当前结构合同生成新的空白 Blender 灰模",
        },
        "stale": False,
        "created_at": now,
        "updated_at": now,
    }
    project.setdefault("model_runs", []).append(row)
    return row


def _model_row(project: dict, run_id: str) -> dict:
    row = next((value for value in project.get("model_runs") or [] if value.get("run_id") == run_id), None)
    if not row:
        raise ValueError("研究建模任务不存在")
    return row


def _normalize_model_artifacts(result: dict) -> list[dict]:
    raw = result.get("artifacts") or {}
    rows: list[dict] = []
    if isinstance(raw, dict):
        iterable = [({"kind": key, **(value if isinstance(value, dict) else {"path": value})}) for key, value in raw.items()]
    elif isinstance(raw, list):
        iterable = raw
    else:
        iterable = []
    for item in iterable:
        path = str(item.get("path") or "")
        if not path or not os.path.isfile(path):
            continue
        filename = os.path.basename(path)
        kind_map = {
            "scene.blend": "blend", "scene.glb": "glb", "research.ifc": "ifc",
            "top.png": "top", "north-east.png": "north_east", "north-west.png": "north_west",
            "mechanical-report.json": "mechanical_report", "model-report.json": "model_report",
            "ifc-report.json": "ifc_report", "unresolved-issues.json": "unresolved_report",
        }
        if filename not in kind_map:
            continue
        rows.append({
            "kind": kind_map[filename],
            "filename": filename,
            "path": path,
            "bytes": int(item.get("bytes") or os.path.getsize(path)),
            "sha256": str(item.get("sha256") or file_sha256(path)),
        })
    return rows


def run_model_job(project_id: str, run_id: str) -> dict:
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise ValueError("全屋设计项目不存在")
        row = _model_row(project, run_id)
        if row.get("status") not in {"queued", "interrupted"}:
            return row
        row.update(status="building", stage="正在生成 Blender、GLB 和研究 IFC", error="", updated_at=time.time())
        row.setdefault("loop", {})["status"] = "building"
        row["loop"]["next_action"] = "等待本轮机械验证完成"
        bundle = deepcopy(row["structure_bundle"])
        output_root = row["output_root"]
        save_project(project)
    try:
        from .tools.fastloop_research import run_research_model
        result = run_research_model(bundle, Path(output_root))
    except Exception as exc:
        logger.exception("全屋研究建模失败 project=%s run=%s", project_id, run_id)
        result = {"status": "failed_product", "error": f"{type(exc).__name__}: {exc}", "artifacts": {}}
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise ValueError("全屋设计项目不存在")
        row = _model_row(project, run_id)
        artifacts = _normalize_model_artifacts(result)
        row["artifacts"] = artifacts
        mechanical_path = next((item["path"] for item in artifacts if item["kind"] == "mechanical_report"), "")
        if mechanical_path:
            try:
                with open(mechanical_path, "r", encoding="utf-8") as handle:
                    row["mechanical_report"] = json.load(handle)
            except (OSError, ValueError, TypeError):
                row["mechanical_report"] = {}
        row["unresolved"] = list(dict.fromkeys([*(row.get("unresolved") or []), *(result.get("unresolved") or [])]))
        result_status = str(result.get("status") or "failed_product")
        if result_status in {"mechanical_verified", "blocked_dependency_missing"}:
            top_path = next((item["path"] for item in artifacts if item["kind"] == "top"), "")
            axon_paths = [item["path"] for item in artifacts if item["kind"] in {"north_east", "north_west"}]
            if result_status == "blocked_dependency_missing" and not top_path:
                row.update(status="blocked_dependency_missing", stage="本地建模依赖缺失", error=str(result.get("message") or "缺少 Blender"))
                row.setdefault("loop", {}).update(status="paused_external", next_action="安装缺失的本地建模依赖后重新开始本轮")
                row["updated_at"] = time.time()
                project["stage"] = row["stage"]
                save_project(project)
                return row
            qa = evaluate_structure(project, top_path, axon_paths) if top_path else {
                "version": QA_PROMPT_VERSION, "status": "manual_required", "hard_fail": False,
                "summary": "顶视图缺失，不能运行复合审查", "checks": [], "provider": "local_missing_artifact",
            }
            row["gemini_review"] = qa
            row.setdefault("loop", {})["status"] = "evaluating"
            if result_status == "blocked_dependency_missing":
                row.update(status="blocked_dependency_missing", stage="Blender 研究模型已生成；研究 IFC 依赖缺失", error=str(result.get("message") or "缺少 IfcOpenShell"))
            elif qa.get("status") == "passed":
                row.update(status="ready_research", stage="研究灰模已通过机械与 Gemini 审查", error="")
                row["loop"].update(status="accepted", next_action="交给独立 verifier 复算")
            elif qa.get("status") == "failed" or qa.get("hard_fail"):
                row.update(status="needs_correction", stage="Gemini 发现结构差异，需要校正", error=str(qa.get("summary") or ""))
                row["loop"].update(status="repairing", next_action="生成只修改结构合同的最小 repair plan")
            else:
                row.update(status="external_review_pending", stage="本地研究模型可用；等待 Gemini 复合审查", error=str(qa.get("summary") or ""))
                row["loop"].update(status="paused_external", next_action="恢复 Gemini 后复审现有本地产物，不重新付费建模")
        else:
            row.update(status="failed_product", stage="研究建模失败", error=str(result.get("error") or "本地建模内核失败"))
            row.setdefault("loop", {}).update(status="repairing", next_action="根据机械失败生成只修改结构合同的最小 repair plan")
        row["updated_at"] = time.time()
        project["status"] = "locked" if project.get("locked_candidate_id") else project.get("status")
        project["stage"] = row["stage"]
        save_project(project)
        return row


def retry_model_review(project_id: str, run_id: str) -> dict:
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise ValueError("全屋设计项目不存在")
        row = _model_row(project, run_id)
        top_path = next((item.get("path") for item in row.get("artifacts") or [] if item.get("kind") == "top"), "")
        axon_paths = [item.get("path") for item in row.get("artifacts") or [] if item.get("kind") in {"north_east", "north_west"}]
    if not top_path or not os.path.isfile(top_path):
        raise ValueError("研究模型顶视图不存在")
    qa = evaluate_structure(project, top_path, axon_paths)
    with _project_lock(project_id):
        project = load_project(project_id)
        row = _model_row(project, run_id)
        row["gemini_review"] = qa
        if qa.get("status") == "passed":
            row.update(status="ready_research", stage="研究灰模已通过 Gemini 复合审查", error="")
        elif qa.get("status") == "failed" or qa.get("hard_fail"):
            row.update(status="needs_correction", stage="Gemini 发现结构差异，需要校正", error=str(qa.get("summary") or ""))
        else:
            row.update(status="external_review_pending", stage="本地研究模型可用；等待 Gemini 复合审查", error=str(qa.get("summary") or ""))
        row["updated_at"] = time.time()
        save_project(project)
        return row


def _fixed_zip_write(archive: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname.replace("\\", "/"), date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_modeling_bundle(project: dict, candidate: dict) -> dict:
    bundle_id = new_id("bundle")
    folder = os.path.join(BUNDLE_ROOT, os.path.basename(project["project_id"]))
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, f"{bundle_id}.zip")
    manifest = {
        "schema_version": "whole-home-design-bundle-v1",
        "bundle_id": bundle_id,
        "target_profile": "blender-mcp-v1",
        "project_id": project["project_id"],
        "project_revision": project["revision"],
        "source_hash": project["source_hash"],
        "generation_hash": project.get("generation_hash") or "",
        "brief_hash": project["brief_hash"],
        "locked_candidate_id": candidate["candidate_id"],
        "candidate_hash": candidate["result_hash"],
        "anchor_overlay_hash": project.get("anchor_overlay_hash") or "",
        "units": "metres",
        "coordinate_system": "blender-z-up",
        "geometry_authority": ["source/floorplan-original", "qa/plan-summary.json"],
        "appearance_authority": ["design/locked-concept-2k.png", "references/*"],
        "blocked_when_scale_missing": True,
        "created_at": time.time(),
    }
    acceptance = {
        "version": "blender-model-acceptance-v1",
        "required_outputs": [
            "scene.blend", "scene.glb", "model_report.json", "audit/top.png",
            "audit/front.png", "audit/right.png", "audit/perspective.png",
        ],
        "model_report_required_fields": [
            "blender_version", "units", "wall_count", "door_count", "window_count",
            "room_count", "asset_inventory", "unresolved_issues", "input_bundle_sha256",
            "mcp_tool_summary",
        ],
        "hard_rules": [
            "Use original floor-plan dimensions and confirmed PlanSummary for structure.",
            "Use the locked concept only for furniture, materials, colour, lighting and atmosphere.",
            "If source and concept conflict, source wins.",
            "If scale evidence is missing or contradictory, stop with blocked_missing_scale; never guess metres.",
            "Do not create a 360 panorama or claim construction/BIM accuracy.",
        ],
    }
    task = """# Blender MCP modelling task

Build one editable Blender model from this bundle. Geometry authority is the original floor plan and
confirmed plan summary. The generated design image is only an appearance and furnishing reference.
Never copy a wall, opening or room change introduced by the generated image. Use metres and Blender Z-up.
If no trustworthy scale/dimension evidence exists, stop and report `blocked_missing_scale`.

Deliver exactly the files listed in `blender/acceptance.json`. Save checkpoints during MCP work, inspect
the scene before and after each logical change, and include unresolved issues in `model_report.json`.
This task does not request a 360 panorama or a construction/BIM claim.
"""
    brief_md = f"""# Whole-home design brief

## User requirements

{project.get('brief', {}).get('requirements_text') or ''}

## Structural authority

Use `source/floorplan-original{os.path.splitext(project['source_path'])[1].lower()}` and
`qa/plan-summary.json`. The concept image is not dimension authority.
"""
    file_rows: list[tuple[str, str]] = [
        (f"source/floorplan-original{os.path.splitext(project['source_path'])[1].lower()}", project["source_path"]),
        ("source/floorplan-normalized.png", project["normalized_path"]),
        ("source/floorplan-generation-raw.png", project.get("generation_raw_path") or project.get("generation_path") or project["normalized_path"]),
        ("source/floorplan-generation.png", project.get("generation_path") or project["normalized_path"]),
        ("design/locked-concept-2k.png", candidate["path"]),
    ]
    if project.get("anchor_overlay_path") and os.path.isfile(project["anchor_overlay_path"]):
        file_rows.append(("source/floorplan-anchor-overlay.png", project["anchor_overlay_path"]))
    brief_snapshot = {
        "requirements_text": project.get("brief", {}).get("requirements_text") or "",
        "references": [
            {
                "bundle_name": f"references/{index:02d}{os.path.splitext(path)[1].lower()}",
                "sha256": file_sha256(path),
            }
            for index, path in enumerate(project.get("brief", {}).get("reference_paths") or [], 1)
        ],
        "brief_hash": project.get("brief_hash") or "",
    }
    for index, path in enumerate(project.get("brief", {}).get("reference_paths") or [], 1):
        file_rows.append((f"references/{index:02d}{os.path.splitext(path)[1].lower()}", path))
    text_rows: list[tuple[str, bytes]] = [
        ("manifest.json", _json_bytes(manifest)),
        ("AGENT_TASK.md", task.encode("utf-8")),
        ("design/design-brief.md", brief_md.encode("utf-8")),
        ("design/design-spec.json", _json_bytes(brief_snapshot)),
        ("qa/plan-summary.json", _json_bytes(project.get("plan_summary") or {})),
        ("qa/human-anchors.json", _json_bytes(project.get("anchor_set") or {})),
        ("qa/anchor-verification.json", _json_bytes(project.get("anchor_verification") or {})),
        ("qa/automated-structure-qa.json", _json_bytes(candidate.get("structure_qa") or {})),
        ("qa/human-structure-review.json", _json_bytes(candidate.get("human_review") or {})),
        ("prompts/concept-prompt-snapshot.json", _json_bytes({
            "candidate_id": candidate.get("candidate_id"),
            "prompt_version": candidate.get("prompt_version"),
            "prompt": candidate.get("prompt"),
            "provider": candidate.get("provider"),
            "model_id": candidate.get("model_id"),
            "endpoint": candidate.get("endpoint"),
        })),
        ("blender/acceptance.json", _json_bytes(acceptance)),
        ("blender/expected-output-layout.txt", ("\n".join(acceptance["required_outputs"]) + "\n").encode("utf-8")),
    ]
    checksums: list[str] = []
    fd, temporary = tempfile.mkstemp(prefix=".bundle_", suffix=".zip", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for arcname, data in sorted(text_rows):
                _fixed_zip_write(archive, arcname, data)
                checksums.append(f"{hashlib.sha256(data).hexdigest()}  {arcname}")
            for arcname, path in sorted(file_rows):
                with open(path, "rb") as handle:
                    data = handle.read()
                _fixed_zip_write(archive, arcname, data)
                checksums.append(f"{hashlib.sha256(data).hexdigest()}  {arcname}")
            checksum_bytes = ("\n".join(sorted(checksums)) + "\n").encode("utf-8")
            _fixed_zip_write(archive, "SHA256SUMS", checksum_bytes)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return {
        "bundle_id": bundle_id,
        "path": destination,
        "sha256": file_sha256(destination),
        "candidate_id": candidate["candidate_id"],
        "project_revision": project["revision"],
        "stale": False,
        "created_at": time.time(),
    }
