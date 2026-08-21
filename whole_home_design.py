"""Whole-home concept design domain service.

This module deliberately stops before geometric modelling.  The source plan is
the structural authority; generated bird's-eye images are concept references for
materials, furniture and atmosphere.  Projects are append-only in practice:
changing an accepted input revision makes older candidates stale instead of
rewriting their evidence.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile
from copy import deepcopy
from typing import Any, Callable, Iterable, Optional

import requests
import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

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
PLAN_PROMPT_VERSION = "whole-home-plan-summary-v1"
QA_PROMPT_VERSION = "whole-home-structure-qa-v1"
ROOT = os.path.join(MAIN_OUTPUT_DIR, "_whole_home_design")
PROJECT_ROOT = os.path.join(ROOT, "projects")
ASSET_ROOT = os.path.join(ROOT, "assets")
BUNDLE_ROOT = os.path.join(ROOT, "bundles")
for _folder in (PROJECT_ROOT, ASSET_ROOT, BUNDLE_ROOT):
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
            return json.load(handle)
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
        "prompt_version": PROMPT_VERSION,
    })


def mark_candidates_stale(project: dict, reason: str) -> None:
    for candidate in project.get("candidates") or []:
        candidate["stale"] = True
        candidate["stale_reason"] = reason
    for bundle in project.get("bundles") or []:
        bundle["stale"] = True
        bundle["stale_reason"] = reason
    project["locked_candidate_id"] = ""


def create_project(source_path: str, original_name: str = "") -> dict:
    source_hash = file_sha256(source_path)
    project_id = new_id("design")
    normalized_path, normalization = normalize_floorplan(source_path, project_id)
    generation_path, generation_crop = extract_generation_plan(source_path, project_id)
    generation_hash = file_sha256(generation_path)
    now = time.time()
    project = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "revision": 1,
        "status": "needs_plan_review",
        "stage": "等待轻量户型摘要",
        "error": "",
        "source_path": source_path,
        "source_name": original_name or os.path.basename(source_path),
        "source_hash": source_hash,
        "normalized_path": normalized_path,
        "normalization": normalization,
        "generation_path": generation_path,
        "generation_raw_path": generation_path,
        "generation_crop": generation_crop,
        "generation_cleanup": {"version": "annotation-cleanup-v1", "applied_count": 0, "boxes": []},
        "generation_hash": generation_hash,
        "plan_summary": empty_plan_summary("human"),
        "plan_summary_confirmed": False,
        "brief": {"requirements_text": "", "reference_paths": [], "reference_hashes": []},
        "brief_hash": "",
        "paid_previews": {},
        "candidates": [],
        "bundles": [],
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
    }


def normalize_floorplan(source_path: str, project_id: str) -> tuple[str, dict]:
    with Image.open(source_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
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
        "cropped_box": list(cropped_box),
        "content_size": [image.width, image.height],
        "canvas_size": [canvas.width, canvas.height],
        "offset": list(offset),
        "aspect_ratio": ratio_label,
        "crop_policy": "near-white-margin-only",
        "padding_policy": "neutral-no-content-crop",
    }


def extract_generation_plan(source_path: str, project_id: str) -> tuple[str, dict]:
    """Extract the main architectural plan while excluding detached details.

    Evidence/OCR continues to use the normalized full sheet. Image generation
    receives this structural crop so cabinet details, captions and dimensions do
    not become invented rooms. Thick horizontal/vertical ink is used as the
    signal; thin dimension lines and text are intentionally ignored.
    """
    with Image.open(source_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
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


_PLAN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "room_count": {"type": "INTEGER"},
        "rooms": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "label": {"type": "STRING"},
            "room_type": {"type": "STRING"}, "coarse_location": {"type": "STRING"},
            "adjacent_room_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "confidence": {"type": "NUMBER"}, "evidence": {"type": "STRING"},
            "needs_confirmation": {"type": "BOOLEAN"},
        }, "required": ["id", "label", "room_type", "coarse_location", "adjacent_room_ids",
                         "confidence", "evidence", "needs_confirmation"]}},
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
    },
    "required": ["room_count", "rooms", "declared_layout", "declared_area_m2",
                 "overall_dimensions_mm", "summary_confidence", "review_items", "annotation_boxes", "entrances",
                 "openings_summary", "wet_zones", "balconies", "dimension_evidence",
                 "must_preserve", "uncertainties"],
}


_ROOM_LABELS = {
    "hallway": ("过道", "hallway"),
    "corridor": ("走廊", "hallway"),
    "entry": ("玄关", "entry"),
    "foyer": ("玄关", "entry"),
    "living_room": ("客厅", "living_room"),
    "dining_room": ("餐厅", "dining_room"),
}


def normalize_plan_rooms(raw_rooms: list[dict]) -> list[dict]:
    """Make AI room rows and adjacency internally consistent without guessing geometry."""
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
    missing_referrers: dict[str, set[str]] = {}
    for room in rooms:
        mapped: list[str] = []
        for raw_adjacent in room.get("adjacent_room_ids") or []:
            original = str(raw_adjacent or "").strip()
            adjacent = id_map.get(original) or id_map.get(original.lower())
            if not adjacent:
                adjacent = re.sub(r"[^a-z0-9_-]+", "_", original.lower()).strip("_-")
            if not adjacent or adjacent == room["id"]:
                continue
            if adjacent not in mapped:
                mapped.append(adjacent)
            if adjacent not in known:
                missing_referrers.setdefault(adjacent, set()).add(room["id"])
        room["adjacent_room_ids"] = mapped
    for missing_id, referrers in sorted(missing_referrers.items()):
        label, room_type = _ROOM_LABELS.get(missing_id, (f"待确认：{missing_id}", "unknown"))
        rooms.append({
            "id": missing_id, "label": label, "room_type": room_type,
            "coarse_location": "unknown", "adjacent_room_ids": sorted(referrers),
            "confidence": 0.0,
            "evidence": f"被 {', '.join(sorted(referrers))} 的邻接关系引用，但模型漏掉了空间行",
            "needs_confirmation": True,
        })
    by_id = {room["id"]: room for room in rooms}
    for room in rooms:
        for adjacent in list(room.get("adjacent_room_ids") or []):
            target = by_id.get(adjacent)
            if target is not None and room["id"] not in (target.get("adjacent_room_ids") or []):
                target.setdefault("adjacent_room_ids", []).append(room["id"])
    return rooms[:80]


def analyze_plan(project_id: str) -> dict:
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise KeyError(project_id)
        project.update(stage="Gemini 正在生成轻量户型摘要", error="")
        save_project(project)
        normalized = project["normalized_path"]
        generation_raw = project.get("generation_raw_path") or project.get("generation_path") or normalized
    prompt = """You are a conservative residential floor-plan evidence reader.
Image 1 is the complete evidence sheet. Image 2 is the automatically cropped main plan used for generation.
Read the Chinese or international residential plan without redesigning it. The user must receive
an already-filled summary and should only need to correct mistakes—not compose a plan description from zero.

Evidence priority:
1. Read the title/caption first for declared layout and area (for example 3房2厅2卫, 121㎡).
2. Read visible overall and chained dimensions exactly; use 0 rather than guessing an unreadable value.
3. Use walls, door swings, windows, bay windows, AC boxes, gas meter, plumbing fixtures and sunken-slab
   annotations to propose room roles and adjacency.
4. Include circulation/hall, kitchen, bathrooms and balcony/service spaces as separate room rows when visible.
5. A room role inferred only from symbols must have lower confidence and needs_confirmation=true.
6. Never invent metric polygons, coordinates, doors or room labels that are not supported by pixels.

Return stable ASCII snake_case room IDs. For every room include a short visual evidence sentence, confidence
0..1 and needs_confirmation. Put every ambiguous entrance, room role, opening or balcony in review_items and
uncertainties. Put all non-negotiable outline features, curved/bay windows, wet zones and level differences in
must_preserve. All user-visible labels, evidence, review labels, summaries and uncertainty text must be in
Simplified Chinese; only IDs, canonical room_type and coarse_location remain ASCII. The original plan remains
the sole structural authority.

For Image 2 only, return annotation_boxes for non-structural text that a local white-fill cleanup may safely
erase: room names, H/W notes, dimension numbers, +elevation text, 下沉 text, 煤气表 text and A/C letters.
Use box_2d=[y_min,x_min,y_max,x_max] normalized to 0..1000 against Image 2. Set safe_to_erase=false if a box
would touch a wall, door swing, window/frame, column, fixture outline or any uncertain geometry. Do not box
architectural lines themselves."""
    payload, error = call_gemini_json(prompt, [normalized, generation_raw], _PLAN_SCHEMA)
    with _project_lock(project_id):
        project = load_project(project_id) or {}
        if payload:
            summary = empty_plan_summary("gemini")
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
            project["stage"] = "请确认轻量户型摘要"
            project["status"] = "needs_plan_review"
            project["error"] = ""
        else:
            project["plan_summary"] = empty_plan_summary("human")
            project["stage"] = "自动识别不可用，请人工填写户型摘要"
            project["status"] = "needs_plan_review"
            project["error"] = error or "自动识别不可用"
        save_project(project)
        return project


def build_design_prompt(project: dict, *, phase: str, direction_index: int = 1,
                        refinement_text: str = "") -> str:
    plan = project.get("plan_summary") or {}
    brief = project.get("brief") or {}
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

USER DESIGN REQUIREMENTS:
{brief.get('requirements_text') or ''}

PHASE:
{phase_rule}
{('ADDITIONAL REFINEMENT REQUEST: ' + refinement_text) if refinement_text else ''}

Image 1 is always the structural authority. Later images are appearance/material references only."""


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


def evaluate_structure(project: dict, candidate_path: str) -> dict:
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
    prompt = f"""You are an adversarial architectural QA reviewer.
Image 1 is the authoritative original floor plan. Image 2 is a generated vertical overhead 2.5D concept.
Compare them, using this confirmed summary as supporting evidence only:
{json.dumps(project.get('plan_summary') or {}, ensure_ascii=False)}

Return one check for every ID below:
{', '.join(STRUCTURE_REVIEW_ITEMS)}
Mark fail for any changed orientation/crop, exterior footprint, room count/location, partition/adjacency,
entrance/balcony/major opening, kitchen/bath wet-zone location, added/missing space, non-orthographic view,
or generated labels/dimensions/watermarks. Uncertainty in any architectural check is a hard failure.
A beautiful image with altered structure must fail."""
    structure_source = project.get("generation_path") or project["normalized_path"]
    payload, error = call_gemini_json(prompt, [structure_source, candidate_path], _QA_SCHEMA)
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
    row["source_url"] = to_url(project.get("source_path"))
    row["normalized_url"] = to_url(project.get("normalized_path"))
    row["generation_url"] = to_url(project.get("generation_path"))
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
    for preview in (row.get("paid_previews") or {}).values():
        preview.pop("confirmation_phrase", None)
    if list_mode:
        for key in ("paid_previews", "brief", "plan_summary"):
            row.pop(key, None)
        row["candidates"] = [candidate for candidate in row.get("candidates") or [] if candidate.get("path")]
    row.pop("source_path", None)
    row.pop("normalized_path", None)
    row.pop("generation_path", None)
    row.pop("generation_raw_path", None)
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
        if project.get("status") in ("generating_drafts", "refining"):
            project["status"] = "interrupted" if project_has_resume else "failed"
            changed = True
        if changed:
            save_project(project)
    return resumable


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
        "units": "metres",
        "coordinate_system": "blender-z-up",
        "geometry_authority": ["source/floorplan-original", "qa/plan-summary.json"],
        "appearance_authority": ["design/final-locked.png", "references/*"],
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
        ("design/final-locked.png", candidate["path"]),
    ]
    parent = next((row for row in project.get("candidates") or []
                   if row.get("candidate_id") == candidate.get("parent_candidate_id")), None)
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
    parent_snapshot = ({
        "candidate_id": parent.get("candidate_id"),
        "result_hash": parent.get("result_hash"),
        "prompt_version": parent.get("prompt_version"),
        "prompt": parent.get("prompt"),
        "provider": parent.get("provider"),
        "model_id": parent.get("model_id"),
        "endpoint": parent.get("endpoint"),
        "resolution": parent.get("resolution"),
        "aspect_ratio": parent.get("aspect_ratio"),
    } if parent else {})
    if parent and parent.get("path"):
        file_rows.append(("design/selected-draft.png", parent["path"]))
    for index, path in enumerate(project.get("brief", {}).get("reference_paths") or [], 1):
        file_rows.append((f"references/{index:02d}{os.path.splitext(path)[1].lower()}", path))
    text_rows: list[tuple[str, bytes]] = [
        ("manifest.json", _json_bytes(manifest)),
        ("AGENT_TASK.md", task.encode("utf-8")),
        ("design/design-brief.md", brief_md.encode("utf-8")),
        ("design/design-spec.json", _json_bytes(brief_snapshot)),
        ("qa/plan-summary.json", _json_bytes(project.get("plan_summary") or {})),
        ("qa/automated-structure-qa.json", _json_bytes(candidate.get("structure_qa") or {})),
        ("qa/human-structure-review.json", _json_bytes(candidate.get("human_review") or {})),
        ("prompts/draft-prompt-snapshot.json", _json_bytes(parent_snapshot)),
        ("prompts/refine-prompt-snapshot.json", _json_bytes({
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
