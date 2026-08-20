# -*- coding: utf-8 -*-
"""Physical floor-board sampling from a manufacturer's repeating print film.

The film is continuous only on its declared repeat axis.  It is first slit into
real board-width lanes, then cut along the roll with the board length advancing
the print phase.  Sampling is performed in world millimetres; the JPEG pixel
aspect ratio is never treated as a physical aspect ratio.
"""
from __future__ import annotations

import hashlib
import base64
import io
import json
import math
import re
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps


FILM_REPEAT_VERSION = "film-repeat-source-v1"
PHYSICAL_LAYOUT_VERSION = "physical-floor-layout-v4"


# The spherical renderer samples one film repeatedly in horizontal strips.  A
# tiny bounded cache avoids rebuilding the 4K manufacturer's-film pyramid for
# every strip without retaining an unbounded set of customer assets.
_FILM_PYRAMID_CACHE: dict[tuple[str, str], list[np.ndarray]] = {}


def _film_pyramid(film: Image.Image, source: np.ndarray, manifest: dict) -> list[np.ndarray]:
    # Production manifests already carry the file digest. The byte fallback is
    # for in-memory previews/tests and prevents Python object-id reuse from ever
    # serving another film's cached pixels.
    source_digest = str(manifest.get("source_sha256") or "")
    if not source_digest:
        source_digest = hashlib.sha256(source.tobytes()).hexdigest()
    key = (source_digest, str(manifest.get("manifest_hash") or ""))
    cached = _FILM_PYRAMID_CACHE.get(key)
    if cached is not None:
        return cached
    pyramid = [source]
    while len(pyramid) < 9 and min(pyramid[-1].shape[:2]) >= 32:
        pyramid.append(cv2.pyrDown(pyramid[-1]))
    if len(_FILM_PYRAMID_CACHE) >= 2:
        _FILM_PYRAMID_CACHE.pop(next(iter(_FILM_PYRAMID_CACHE)))
    _FILM_PYRAMID_CACHE[key] = pyramid
    return pyramid


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_plank_dimensions(text: str) -> tuple[Optional[float], Optional[float]]:
    values = [float(value) for value in re.findall(r"(?<!\d)(\d{2,5}(?:\.\d+)?)", str(text or ""))]
    values = [value for value in values if 20 <= value <= 20000]
    if len(values) < 2:
        return None, None
    return min(values[0], values[1]), max(values[0], values[1])


def _detect_exclusion_rects(rgb: np.ndarray) -> list[dict]:
    height, width = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    bright_neutral = ((hsv[..., 1] < 38) & (hsv[..., 2] > 188)).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    bright_neutral = cv2.morphologyEx(bright_neutral, cv2.MORPH_CLOSE, kernel, iterations=2)
    count, _, stats, _ = cv2.connectedComponentsWithStats(bright_neutral, 8)
    rows = []
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if area < max(800, int(width * height * 0.0008)):
            continue
        touches_outer = x <= 3 or y <= 3 or x + w >= width - 3 or y + h >= height - 3
        corner_region = x >= width * 0.55 and y >= height * 0.55
        rectangular_fill = area / max(1, w * h)
        if corner_region and touches_outer and rectangular_fill >= 0.48:
            rows.append({
                "kind": "printed_label",
                "pixel_rect": [x, y, x + w, y + h],
                "area_px": area,
                "confidence": round(min(1.0, rectangular_fill), 5),
            })
    return sorted(rows, key=lambda row: row["area_px"], reverse=True)


def _repeat_registration(rgb: np.ndarray, exclusion_rects: list[dict]) -> dict:
    height, width = rgb.shape[:2]
    valid_columns = np.ones(width, dtype=bool)
    for row in exclusion_rects:
        x0, y0, x1, _ = row["pixel_rect"]
        if y0 >= height * 0.75:
            valid_columns[max(0, x0):min(width, x1)] = False
    indices = np.flatnonzero(valid_columns)
    if indices.size < width // 3:
        indices = np.arange(width)
    band = max(8, min(48, height // 100))
    top = rgb[:band].astype(np.float32)
    bottom = rgb[-band:].astype(np.float32)
    max_shift = max(2, min(32, round(width * 0.005)))
    best = None
    for shift in range(-max_shift, max_shift + 1):
        if shift < 0:
            cols = indices[(indices >= -shift)]
            first, second = cols, cols + shift
        elif shift > 0:
            cols = indices[(indices < width - shift)]
            first, second = cols, cols + shift
        else:
            first = second = indices
        if len(first) < width // 4:
            continue
        delta = np.abs(top[:, first] - bottom[:, second])
        score = float(np.mean(delta))
        if best is None or score < best[0]:
            best = (score, shift, float(np.median(delta)), float(np.percentile(delta, 95)))
    typical = np.abs(
        rgb[height // 3:height // 3 + band, indices].astype(np.float32)
        - rgb[height // 3 + 1:height // 3 + 1 + band, indices].astype(np.float32)
    )
    score, shift, median, p95 = best or (999.0, 0, 999.0, 999.0)
    if shift < 0:
        seam_cols = indices[indices >= -shift]
        seam_first, seam_second = seam_cols, seam_cols + shift
    elif shift > 0:
        seam_cols = indices[indices < width - shift]
        seam_first, seam_second = seam_cols, seam_cols + shift
    else:
        seam_first = seam_second = indices
    seam_delta = np.abs(
        rgb[0, seam_first].astype(np.float32) - rgb[-1, seam_second].astype(np.float32))
    seam_mean = float(np.mean(seam_delta))
    allowed = max(18.0, float(np.mean(typical)) * 3.25)
    return {
        "axis": "long_edge_y",
        "translation_px_x": int(shift),
        "rotation_deg": 0.0,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "boundary_mean_abs_diff": round(score, 4),
        "seam_row_mean_abs_diff": round(seam_mean, 4),
        "boundary_median_abs_diff": round(median, 4),
        "boundary_p95_abs_diff": round(p95, 4),
        "typical_adjacent_mean_abs_diff": round(float(np.mean(typical)), 4),
        "threshold": round(allowed, 4),
        "status": "pass" if seam_mean <= allowed and abs(shift) <= max_shift else "fail",
    }


def _gcd_units(first_mm: float, second_mm: float) -> tuple[int, int, int]:
    # 0.1 mm precision covers imperial conversions while avoiding floating gcd.
    first = max(1, int(round(float(first_mm) * 10.0)))
    second = max(1, int(round(float(second_mm) * 10.0)))
    divisor = math.gcd(first, second)
    return first, second, divisor


def analyze_film_repeat(image: Image.Image, *, film_width_mm: float,
                        repeat_length_mm: float, plank_width_mm: float,
                        plank_length_mm: float, slit_origin_mm: Optional[float] = None,
                        seam_type: str = "", floor_size: str = "") -> dict:
    if not 100 <= float(film_width_mm) <= 10000:
        raise ValueError("彩膜宽度必须在 100–10000mm 之间")
    if not 100 <= float(repeat_length_mm) <= 20000:
        raise ValueError("彩膜周期必须在 100–20000mm 之间")
    if not 20 <= float(plank_width_mm) <= float(film_width_mm):
        raise ValueError("板宽必须大于20mm且不超过彩膜宽度")
    if not 100 <= float(plank_length_mm) <= 20000:
        raise ValueError("板长必须在 100–20000mm 之间")
    source = ImageOps.exif_transpose(image).convert("RGB")
    rgb = np.asarray(source, dtype=np.uint8)
    exclusions = _detect_exclusion_rects(rgb)
    registration = _repeat_registration(rgb, exclusions)
    lane_count = max(1, int(math.floor(float(film_width_mm) / float(plank_width_mm))))
    remaining = float(film_width_mm) - lane_count * float(plank_width_mm)
    origin = remaining / 2.0 if slit_origin_mm is None else float(slit_origin_mm)
    if origin < 0 or origin + lane_count * float(plank_width_mm) > float(film_width_mm) + 1e-6:
        raise ValueError("分切起点使板材通道超出彩膜宽度")
    length_units, repeat_units, divisor = _gcd_units(plank_length_mm, repeat_length_mm)
    phase_states = repeat_units // divisor
    physical_exclusions = []
    for row in exclusions:
        x0, y0, x1, y1 = row["pixel_rect"]
        physical_exclusions.append({
            **row,
            "physical_rect_mm": [
                x0 / source.width * film_width_mm,
                y0 / source.height * repeat_length_mm,
                x1 / source.width * film_width_mm,
                y1 / source.height * repeat_length_mm,
            ],
        })
    manifest = {
        "version": FILM_REPEAT_VERSION,
        "physical_layout_version": PHYSICAL_LAYOUT_VERSION,
        "image_size": [source.width, source.height],
        "film_width_mm": round(float(film_width_mm), 6),
        "repeat_length_mm": round(float(repeat_length_mm), 6),
        "pixels_per_mm_x": round(source.width / float(film_width_mm), 8),
        "pixels_per_mm_y": round(source.height / float(repeat_length_mm), 8),
        "repeat_axis": "long_edge_y",
        "plank_width_mm": round(float(plank_width_mm), 6),
        "plank_length_mm": round(float(plank_length_mm), 6),
        "floor_size": str(floor_size or ""),
        "seam_type": str(seam_type or ""),
        "slitting": {
            "mode": "actual_plank_width",
            "lane_count": lane_count,
            "slit_origin_mm": round(origin, 6),
            "remaining_mm": round(remaining, 6),
            "left_margin_mm": round(origin, 6),
            "right_margin_mm": round(float(film_width_mm) - origin - lane_count * float(plank_width_mm), 6),
        },
        "phase_state_count": int(phase_states),
        "effective_board_states": int(phase_states * lane_count),
        "phase_advance_mm": round(float(plank_length_mm) % float(repeat_length_mm), 6),
        "repeat_registration": registration,
        "exclusion_rects": physical_exclusions,
        "status": "ready" if registration["status"] == "pass" else "repeat_invalid",
    }
    manifest["manifest_hash"] = _stable_hash(manifest)
    return manifest


def analyze_film_path(path: str, params: dict) -> tuple[Image.Image, dict]:
    plank_width, plank_length = parse_plank_dimensions(str((params or {}).get("floor_size") or ""))
    if plank_width is None or plank_length is None:
        raise ValueError("无法从铺装规格解析板宽和板长")
    width_mm = (params or {}).get("film_width_mm")
    repeat_mm = (params or {}).get("film_repeat_length_mm")
    if width_mm is None or repeat_mm is None:
        raise ValueError("原厂彩膜需要填写彩膜宽度和纵向重复周期")
    with Image.open(path) as source:
        source.load()
        image = ImageOps.exif_transpose(source).convert("RGB").copy()
    manifest = analyze_film_repeat(
        image,
        film_width_mm=float(width_mm),
        repeat_length_mm=float(repeat_mm),
        plank_width_mm=float(plank_width),
        plank_length_mm=float(plank_length),
        slit_origin_mm=(params or {}).get("film_slit_origin_mm"),
        seam_type=str((params or {}).get("seam_type") or ""),
        floor_size=str((params or {}).get("floor_size") or ""),
    )
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest.pop("manifest_hash", None)
    manifest["source_sha256"] = digest.hexdigest()
    manifest["manifest_hash"] = _stable_hash(manifest)
    return image, manifest


def _interval_overlaps(start: np.ndarray, length: float, repeat: float,
                       low: float, high: float) -> np.ndarray:
    end = start + length
    first = (start < high) & (np.minimum(end, repeat) > low)
    wraps = end > repeat
    second = wraps & (0.0 < high) & ((end - repeat) > low)
    return first | second


def _candidate_invalid(lane: np.ndarray, phase_index: np.ndarray, manifest: dict) -> np.ndarray:
    slitting = manifest["slitting"]
    width = float(manifest["plank_width_mm"])
    length = float(manifest["plank_length_mm"])
    repeat = float(manifest["repeat_length_mm"])
    phase = np.mod(phase_index * length, repeat)
    x0 = float(slitting["slit_origin_mm"]) + lane * width
    x1 = x0 + width
    invalid = np.zeros(lane.shape, dtype=bool)
    for row in manifest.get("exclusion_rects") or []:
        ex0, ey0, ex1, ey1 = (float(value) for value in row["physical_rect_mm"])
        overlap_x = (x0 < ex1) & (x1 > ex0)
        overlap_y = _interval_overlaps(phase, length, repeat, ey0, ey1)
        invalid |= overlap_x & overlap_y
    return invalid


def _board_assignment(key_a: np.ndarray, key_b: np.ndarray, orientation: np.ndarray,
                      manifest: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lanes = int(manifest["slitting"]["lane_count"])
    phases = max(1, int(manifest["phase_state_count"]))
    # Integer-only deterministic mixing; adjacent geometry changes all terms.
    mixed = (
        key_a.astype(np.int64) * np.int64(73856093)
        ^ key_b.astype(np.int64) * np.int64(19349663)
        ^ orientation.astype(np.int64) * np.int64(83492791)
    )
    mixed &= np.int64(0x7FFFFFFFFFFFFFFF)
    lane = np.mod(mixed + key_a + key_b * 2, lanes).astype(np.int32)
    phase = np.mod(mixed // max(1, lanes) + key_a * 3 + key_b * 5, phases).astype(np.int64)
    rotation = (mixed & 1).astype(bool)
    max_attempts = min(max(4, lanes * min(phases, 8)), 128)
    invalid = _candidate_invalid(lane, phase, manifest)
    for attempt in range(1, max_attempts + 1):
        if not np.any(invalid):
            break
        lane[invalid] = np.mod(lane[invalid] + 1, lanes)
        phase[invalid] = np.mod(phase[invalid] + 1 + attempt * 2, phases)
        invalid = _candidate_invalid(lane, phase, manifest)
    if np.any(invalid):
        raise ValueError("彩膜标签避让后没有足够的合法板材，请提供无标签净版或调整分切起点")
    return lane, phase, rotation


def _seam_gain(local_length_mm: np.ndarray, local_width_mm: np.ndarray,
               plank_length_mm: float, plank_width_mm: float, seam_type: str,
               footprint_mm: np.ndarray | float | None = None,
               board_tone: np.ndarray | float = 0.0) -> np.ndarray:
    distance_l = np.minimum(local_length_mm, plank_length_mm - local_length_mm)
    distance_w = np.minimum(local_width_mm, plank_width_mm - local_width_mm)
    text = str(seam_type or "")
    if "无缝" in text:
        joint_width, side_darkness, end_darkness, bevel_darkness = .65, .16, .22, .035
    elif "圆弧" in text:
        joint_width, side_darkness, end_darkness, bevel_darkness = 1.6, .25, .32, .060
    else:
        joint_width, side_darkness, end_darkness, bevel_darkness = 1.0, .22, .30, .050
    footprint = np.asarray(
        .75 if footprint_mm is None else footprint_mm, dtype=np.float32)
    if footprint.ndim == 1 and distance_w.ndim == 2 and footprint.shape[0] == distance_w.shape[0]:
        footprint = footprint[:, None]
    footprint = np.maximum(footprint, .35)

    def antialiased_coverage(distance: np.ndarray, width: float) -> np.ndarray:
        # Pixel-integrated coverage keeps a sub-millimetre joint visible even
        # when one output pixel spans several millimetres of the world plane.
        return np.clip((width * .5 + footprint * .5 - distance) / footprint, 0.0, 1.0)

    side_joint = antialiased_coverage(distance_w, joint_width)
    end_joint = antialiased_coverage(distance_l, joint_width * 1.15)
    bevel_width = 2.2 if "无缝" in text else 2.8
    side_bevel = np.clip(1.0 - distance_w / bevel_width, 0.0, 1.0)
    end_bevel = np.clip(1.0 - distance_l / (bevel_width * 1.15), 0.0, 1.0)
    gain = (
        1.0
        - side_joint * side_darkness
        - end_joint * end_darkness
        - np.maximum(side_bevel, end_bevel) * bevel_darkness
        + np.asarray(board_tone, dtype=np.float32)
    )
    return np.clip(gain, .55, 1.06)


def sample_film_floor(film: Image.Image, world_x_m: np.ndarray, world_z_m: np.ndarray,
                      manifest: dict, *, rotation_deg: float = 0.0,
                      offset_x: float = 0.0, offset_z: float = 0.0,
                      laying: Optional[str] = None,
                      footprint_mm: np.ndarray | float | None = None) -> tuple[np.ndarray, dict]:
    if manifest.get("status") != "ready":
        raise ValueError("彩膜周期验证未通过")
    source = np.asarray(ImageOps.exif_transpose(film).convert("RGB"), dtype=np.uint8)
    source_pyramid = _film_pyramid(film, source, manifest)
    world_x_m = np.nan_to_num(np.asarray(world_x_m, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    world_z_m = np.nan_to_num(np.asarray(world_z_m, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    theta = math.radians(float(rotation_deg))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    x_mm = (cos_t * world_x_m + sin_t * world_z_m) * 1000.0 + float(offset_x) * 1000.0
    z_mm = (-sin_t * world_x_m + cos_t * world_z_m) * 1000.0 + float(offset_z) * 1000.0
    plank_w = float(manifest["plank_width_mm"])
    plank_l = float(manifest["plank_length_mm"])
    pattern = str(laying or manifest.get("floor_size") or "")

    if "人字" in pattern:
        ratio = int(round(plank_l / plank_w))
        if ratio < 2 or abs(plank_l / plank_w - ratio) > 0.08:
            raise ValueError("人字拼要求板长约为板宽的整数倍")
        gx, gz = x_mm / plank_w, z_mm / plank_w
        cell_x, cell_z = np.floor(gx).astype(np.int64), np.floor(gz).astype(np.int64)
        phase_band = np.mod(cell_x + cell_z, ratio * 2)
        horizontal = phase_band < ratio
        start_x = cell_x - phase_band
        start_z = cell_z - (phase_band - ratio)
        local_l = np.where(horizontal, (gx - start_x) * plank_w, (gz - start_z) * plank_w)
        local_w = np.where(horizontal, (gz - cell_z) * plank_w, (gx - cell_x) * plank_w)
        key_a = np.where(horizontal, start_x, cell_x)
        key_b = np.where(horizontal, cell_z, start_z)
        orientation = np.where(horizontal, 1, 2).astype(np.int8)
    else:
        row = np.floor(z_mm / plank_w).astype(np.int64)
        # Irrational-like row offset prevents a short staircase period while
        # keeping adjacent rows over one-third of a board apart.
        offset_fraction = np.mod(row.astype(np.float64) * 0.3819660112501051, 1.0)
        shifted_x = x_mm + offset_fraction * plank_l
        board = np.floor(shifted_x / plank_l).astype(np.int64)
        local_l = np.mod(shifted_x, plank_l)
        local_w = np.mod(z_mm, plank_w)
        key_a, key_b = board, row
        orientation = np.zeros(row.shape, dtype=np.int8)

    lane, phase_index, rotate = _board_assignment(key_a, key_b, orientation, manifest)
    local_l = np.where(rotate, plank_l - local_l, local_l)
    local_w = np.where(rotate, plank_w - local_w, local_w)
    repeat = float(manifest["repeat_length_mm"])
    film_width = float(manifest["film_width_mm"])
    film_x_mm = float(manifest["slitting"]["slit_origin_mm"]) + lane * plank_w + local_w
    film_y_mm = np.mod(phase_index * plank_l + local_l, repeat)
    image_h, image_w = source.shape[:2]
    shift = float((manifest.get("repeat_registration") or {}).get("translation_px_x") or 0.0)
    map_x = film_x_mm / film_width * (image_w - 1) + film_y_mm / repeat * shift
    map_y = film_y_mm / repeat * (image_h - 1)
    map_x = np.clip(map_x, 0.0, image_w - 1).astype(np.float32)
    map_y = np.mod(map_y, image_h - 1).astype(np.float32)
    if footprint_mm is None:
        sampled = cv2.remap(source, map_x, map_y, cv2.INTER_LANCZOS4,
                            borderMode=cv2.BORDER_REFLECT_101)
        mip_levels = np.zeros(map_x.shape, dtype=np.int8)
    else:
        physical_footprint = np.asarray(footprint_mm, dtype=np.float32)
        if (physical_footprint.ndim == 1 and map_x.ndim == 2
                and physical_footprint.shape[0] == map_x.shape[0]):
            physical_footprint = physical_footprint[:, None]
        physical_footprint = np.broadcast_to(physical_footprint, map_x.shape)
        pixels_per_mm = math.sqrt(
            float(manifest.get("pixels_per_mm_x") or image_w / film_width)
            * float(manifest.get("pixels_per_mm_y") or image_h / repeat))
        # Select the source level before remapping.  Post-blurring an already
        # aliased remap cannot remove the sand-like moire that users observed.
        mip_levels = np.clip(
            np.floor(np.log2(np.maximum(1.0, physical_footprint * pixels_per_mm / 1.35))),
            0, len(source_pyramid) - 1).astype(np.int8)
        sampled = np.empty(map_x.shape + (3,), dtype=np.uint8)
        for level in np.unique(mip_levels):
            scale = float(2 ** int(level))
            level_source = source_pyramid[int(level)]
            level_x = np.clip(map_x / scale, 0.0, level_source.shape[1] - 1).astype(np.float32)
            level_y = np.mod(map_y / scale, max(1, level_source.shape[0] - 1)).astype(np.float32)
            level_pixels = cv2.remap(
                level_source, level_x, level_y, cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101)
            selector = mip_levels == level
            sampled[selector] = level_pixels[selector]
    tone_key = np.mod(
        lane.astype(np.int64) * 17 + phase_index.astype(np.int64) * 7
        + key_a.astype(np.int64) * 3 + key_b.astype(np.int64) * 5, 21)
    board_tone = (tone_key.astype(np.float32) - 10.0) / 10.0 * .018
    gain = _seam_gain(
        local_l, local_w, plank_l, plank_w,
        str(manifest.get("seam_type") or ""),
        footprint_mm=footprint_mm, board_tone=board_tone)
    sampled = np.clip(sampled.astype(np.float32) * gain[..., None], 0, 255).astype(np.uint8)
    metadata = {
        "provider": "local",
        "model": PHYSICAL_LAYOUT_VERSION,
        "film_manifest_hash": manifest.get("manifest_hash"),
        "lane_count": int(manifest["slitting"]["lane_count"]),
        "phase_state_count": int(manifest["phase_state_count"]),
        "effective_board_states": int(manifest["effective_board_states"]),
        "laying": "herringbone" if "人字" in pattern else "straight",
        "source_pixel_periodic": False,
        "physical_roll_repeat": True,
        "screen_space_antialiased_joints": True,
        "source_detail_enhancement": "physical_mipmap_prefilter",
        "mipmap_level_range": [int(mip_levels.min()), int(mip_levels.max())],
        "label_avoidance": bool(manifest.get("exclusion_rects")),
    }
    return sampled, metadata


def render_film_floor_preview(film: Image.Image, manifest: dict, *, size: int = 1024,
                              extent_m: float = 8.0, rotation_deg: float = 0.0) -> Image.Image:
    axis = (np.arange(size, dtype=np.float32) + 0.5) / size * extent_m - extent_m / 2.0
    world_x, world_z = np.meshgrid(axis, axis)
    pixels, _ = sample_film_floor(
        film, world_x, world_z, manifest, rotation_deg=rotation_deg)
    return Image.fromarray(pixels, "RGB")


def build_film_contract(path: str, params: dict, *, guide_size: int = 512) -> tuple[dict, Image.Image]:
    image, manifest = analyze_film_path(path, params)
    guide = render_film_floor_preview(
        image, manifest, size=max(128, min(1024, int(guide_size))), extent_m=8.0,
        rotation_deg=90.0)
    buffer = io.BytesIO()
    guide.save(buffer, "PNG", optimize=True)
    payload = buffer.getvalue()
    return {
        "manifest": manifest,
        "guide_b64": base64.b64encode(payload).decode("ascii"),
        "guide_sha256": hashlib.sha256(payload).hexdigest(),
        "guide_size": list(guide.size),
    }, image


__all__ = [
    "FILM_REPEAT_VERSION", "PHYSICAL_LAYOUT_VERSION", "analyze_film_repeat",
    "analyze_film_path", "parse_plank_dimensions", "render_film_floor_preview", "sample_film_floor",
    "build_film_contract",
]
