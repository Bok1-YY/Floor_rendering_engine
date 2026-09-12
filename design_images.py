"""Whole-home design: images."""

import os
import re
import time

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps




from .design_schema import (
    SUPPORTED_RATIOS
)
from .design_store import (
    ASSET_ROOT,
    file_sha256
)

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
    return {
        "version": "floorplan-anchors-v1",
        "coordinate_space": "normalized-evidence-1000-v1",
        "source_hash": project["source_hash"],
        "normalized_hash": file_sha256(project["normalized_path"]),
        "confirmed_complete": bool(payload.get("confirmed_complete")),
        "anchors": normalized,
        "updated_at": time.time(),
    }

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
