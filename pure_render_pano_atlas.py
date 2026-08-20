# -*- coding: utf-8 -*-
"""Direct cubemap-atlas panorama generation helpers.

The provider creates one 3x2 image containing six views from one optical
centre.  Everything after that paid call is deterministic: register the
atlas, split the six faces, project the cube to a fixed 2:1 ERP and run
reference-free visual checks.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import hmac
import json
import math
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .panorama_quality_planner import (
    compile_panorama_prompt,
    public_quality_plan,
    validate_quality_plan,
)
from .panorama_local_geometry import public_geometry_contract, validate_geometry_contract
from .pure_render_pano import (
    PAID_PREVIEW_TTL_SECONDS,
    PURE_RENDER_PANO_HEIGHT,
    PURE_RENDER_PANO_SIZE,
    PURE_RENDER_PANO_WARNING,
    PURE_RENDER_PANO_WIDTH,
    gate_visual_pano,
)
from .whole_home_pano_render import CUBE_FACE_ORDER, cube_to_erp, face_basis


DIRECT_PANO_POLICY = "direct_cubemap_atlas_paid_preview_v1"
DIRECT_PANO_ROUTE = "direct_cubemap_atlas"
DIRECT_PANO_TEMPLATE_VERSION = "cubemap_atlas_3x2_v1"
DIRECT_PANO_GATE_VERSION = "direct_cubemap_atlas_gate_v1"
DIRECT_ATLAS_WIDTH = 3072
DIRECT_ATLAS_HEIGHT = 2048
DIRECT_FACE_SIZE = 1024
DIRECT_ATLAS_LAYOUT = (("+X", "-X", "+Y"), ("-Y", "+Z", "-Z"))
DIRECT_PANO_WARNING = (
    "六个方向由 AI 在一次图集生成中补全，系统只负责确定性拆面与球面投影；"
    "它仍是单观察点、单目全景，不代表真实户型、尺寸或施工几何。"
)


def _stable_hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _iso_utc(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(float(epoch_seconds), timezone.utc).isoformat().replace("+00:00", "Z")


def _rgb(value) -> tuple[int, int, int]:
    return tuple(int(channel) for channel in value[:3])


def build_atlas_template() -> tuple[Image.Image, Image.Image]:
    """Return the canonical 3x2 registration canvas and an edit mask.

    The thin coloured rails make the six cells and orientation observable to
    an image model.  They live in a narrow border that is removed during atlas
    registration.  The mask keeps those rails stable for engines supporting a
    mask while allowing every face interior to be generated.
    """
    canvas = Image.new("RGB", (DIRECT_ATLAS_WIDTH, DIRECT_ATLAS_HEIGHT), (22, 24, 28))
    mask = Image.new("L", canvas.size, 0)
    draw = ImageDraw.Draw(canvas)
    mask_draw = ImageDraw.Draw(mask)
    # Saturated registration rails leaked into real B2/GPT outputs and became
    # coloured cube seams.  Keep the cells observable with six close neutral
    # values and crop a wider safe gutter after generation.
    palette = {
        "+X": (91, 94, 99), "-X": (99, 96, 91), "+Y": (94, 98, 96),
        "-Y": (98, 94, 97), "+Z": (92, 96, 101), "-Z": (101, 97, 92),
    }
    border = 24
    for row_index, row in enumerate(DIRECT_ATLAS_LAYOUT):
        for col_index, face in enumerate(row):
            x0, y0 = col_index * DIRECT_FACE_SIZE, row_index * DIRECT_FACE_SIZE
            x1, y1 = x0 + DIRECT_FACE_SIZE, y0 + DIRECT_FACE_SIZE
            colour = palette[face]
            # A low-contrast neutral interior avoids biasing the scene while the
            # rails/corner wedges provide deterministic grid registration.
            for y in range(y0 + border, y1 - border):
                blend = (y - y0) / DIRECT_FACE_SIZE
                shade = tuple(round(116 + blend * 22 + (c - 128) * .06) for c in colour)
                draw.line((x0 + border, y, x1 - border - 1, y), fill=shade)
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=colour, width=border)
            wedge = 38
            draw.polygon(((x0 + border, y0 + border),
                          (x0 + border + wedge, y0 + border),
                          (x0 + border, y0 + border + wedge)), fill=colour)
            # White means editable for GPT Image style masks.
            mask_draw.rectangle((x0 + border, y0 + border, x1 - border - 1, y1 - border - 1), fill=255)
    return canvas, mask


def build_room_geometry_faces(face_size: int = DIRECT_FACE_SIZE) -> dict[str, Image.Image]:
    """Render a deterministic cuboid depth/normal proxy from one optical centre."""
    size = max(128, int(face_size))
    coordinate = ((np.arange(size, dtype=np.float32) + .5) / size) * 2.0 - 1.0
    sx, sy = np.meshgrid(coordinate, -coordinate)
    origin = np.array((0.0, 1.55, 0.0), dtype=np.float32)
    planes = (
        (0, 3.2, np.array((-1.0, 0.0, 0.0), np.float32), (170, 185, 202)),
        (0, -3.2, np.array((1.0, 0.0, 0.0), np.float32), (162, 180, 201)),
        (1, 2.8, np.array((0.0, -1.0, 0.0), np.float32), (202, 205, 216)),
        (1, 0.0, np.array((0.0, 1.0, 0.0), np.float32), (192, 142, 92)),
        (2, 3.8, np.array((0.0, 0.0, -1.0), np.float32), (174, 194, 180)),
        (2, -3.8, np.array((0.0, 0.0, 1.0), np.float32), (181, 190, 168)),
    )
    faces: dict[str, Image.Image] = {}
    for face in CUBE_FACE_ORDER:
        forward, right, up = face_basis(face)
        directions = (forward[None, None, :] + sx[..., None] * right[None, None, :]
                      + sy[..., None] * up[None, None, :]).astype(np.float32)
        candidates = []
        for axis, value, _, _ in planes:
            denominator = directions[..., axis]
            distance = np.where(
                np.abs(denominator) > 1e-7,
                (float(value) - float(origin[axis])) / denominator,
                np.inf,
            )
            candidates.append(np.where(distance > 1e-5, distance, np.inf))
        stack = np.stack(candidates, axis=-1)
        owner = np.argmin(stack, axis=-1)
        depth = np.take_along_axis(stack, owner[..., None], axis=-1)[..., 0]
        output = np.zeros((size, size, 3), dtype=np.float32)
        for index, (_, _, normal, colour) in enumerate(planes):
            selected = owner == index
            if not np.any(selected):
                continue
            base = np.array(colour, dtype=np.float32)
            normal_light = .72 + .28 * abs(float(np.dot(normal, np.array((.35, .8, .4), np.float32))))
            attenuation = np.clip(1.08 - depth[selected] / 18.0, .62, 1.0)
            output[selected] = base[None] * normal_light * attenuation[:, None]
        # Plane ownership discontinuities are the exact Manhattan target edges.
        edge = np.zeros((size, size), dtype=np.uint8)
        edge[:, 1:] |= (owner[:, 1:] != owner[:, :-1]).astype(np.uint8)
        edge[1:, :] |= (owner[1:, :] != owner[:-1, :]).astype(np.uint8)
        edge = cv2.dilate(edge, np.ones((3, 3), np.uint8), iterations=1) > 0
        output[edge] = (28, 31, 36)
        # Stable horizon/optical-axis cross gives the edit model an explicit
        # level-camera signal without any text or face labels in the content.
        centre = size // 2
        output[max(0, centre - 1):centre + 1] = np.minimum(
            output[max(0, centre - 1):centre + 1], 82)
        output[:, max(0, centre - 1):centre + 1] = np.minimum(
            output[:, max(0, centre - 1):centre + 1], 82)
        faces[face] = Image.fromarray(np.clip(output, 0, 255).astype(np.uint8), "RGB")
    return faces


def build_room_geometry_guide(face_size: int = DIRECT_FACE_SIZE) -> Image.Image:
    faces = build_room_geometry_faces(face_size)
    atlas = Image.new("RGB", (face_size * 3, face_size * 2))
    for row_index, row in enumerate(DIRECT_ATLAS_LAYOUT):
        for col_index, face in enumerate(row):
            atlas.paste(faces[face], (col_index * face_size, row_index * face_size))
    return atlas


def build_room_geometry_guide_erp(width: int = PURE_RENDER_PANO_WIDTH,
                                  height: int = PURE_RENDER_PANO_HEIGHT) -> Image.Image:
    return cube_to_erp_chunked(build_room_geometry_faces(512), width, height)


def build_direct_atlas_prompt(params: Optional[dict] = None, *, engine_label: str = "") -> str:
    """Build the shared one-call, six-view atlas contract for both engines."""
    values = dict(params or {})

    def value(name: str) -> str:
        return str(values.get(name) or "").strip()[:300]

    context = [
        ("space type", value("cn_room_type") if values.get("cn_mode") else value("room_type")),
        ("property type", value("property_type")),
        ("interior style", value("style_type")),
        ("lighting", value("lighting")),
        ("view/outside", value("view")),
        ("floor tone", value("floor_tone")),
        ("floor format", value("floor_size")),
        ("floor seam", value("seam_type")),
        ("floor gloss", value("glossiness")),
    ]
    context_lines = [f"- {label}: {text}" for label, text in context if text]
    lines = [
        "Generate exactly ONE cubemap atlas for a photorealistic interior. Image 1 is the mandatory "
        "3 columns by 2 rows registration template. Replace each panel interior with a 90-degree "
        "rectilinear view, but preserve the exact panel positions and 3x2 canvas structure.",
        "All six panels must depict the SAME room from the SAME optical centre at the SAME moment. "
        "Only the viewing direction changes. Use a level camera, identical exposure, identical room "
        "geometry, identical furniture identity and identical lighting in every panel.",
        "The required panel order is immutable: top row +X | -X | +Y; bottom row -Y | +Z | -Z. "
        "+Z is the principal/front view. +Y looks straight up at the ceiling and -Y straight down "
        "at the floor. Adjacent cube edges must join exactly after cubemap projection.",
        "Use one physically coherent Manhattan room shell. Every cube face is rectilinear: wall corners, "
        "columns, doors, windows and ceiling lines stay straight and continue into the adjacent face with "
        "the same vanishing geometry. No fisheye, barrel distortion, bulging walls or curved architecture.",
        "Image 2 is the authoritative floor material swatch. Apply it only to the floor with believable "
        "scale, plank/tile direction, seams, gloss and lighting response. The floor is one horizontal "
        "world plane with one global plank direction and fixed physical board dimensions across all six "
        "faces. Never create radial, fan-shaped, locally rotated or locally resized floor zones. Keep the "
        "seams restrained so a deterministic spherical material pass can replace them after composition. "
        "Never paste the floor sample onto walls, ceiling or furniture.",
        "Every panel must be fully filled. Do not create six unrelated rooms, perspective thumbnails, "
        "fisheye circles, an ERP strip, captions, face labels, arrows, borders, gutters, watermarks, black "
        "areas, duplicate panels or mirrored panels. Output one image only.",
        "The final supplied image is a deterministic six-face Manhattan depth/normal guide. Match its level optical centre, straight verticals, wall/floor/ceiling ownership and cube-edge geometry exactly, but render photorealistic materials rather than copying the guide colours.",
    ]
    if engine_label:
        lines.append(f"Generation engine label for audit only: {engine_label}.")
    if context_lines:
        lines.extend(("Approved scene context:", *context_lines))
    if values.get("film_path"):
        lines.extend((
            "A manufacturer repeat-film image and a locally rendered physical-laying guide are supplied after the floor swatch. ",
            f"Film width: {value('film_width_mm')} mm; longitudinal repeat: {value('film_repeat_length_mm')} mm. ",
            "The guide is authoritative for real board cuts, roll phase, scale, direction and every cross-face seam. Replicate it exactly; "
            "never repeat the entire source rectangle, invent wood grain, mirror boards, swap +Y/-Y, or relocate a board joint.",
        ))
    if values.get("_room_reference_geometry"):
        lines.extend((
            "A room-reference effect image is supplied before the geometry guide. It is the authoritative +Z/front-view geometry anchor, not merely a style moodboard.",
            "Preserve its horizon, camera height, wall/window/door proportions and principal furniture layout in +Z. Extend only the unseen directions around the same optical centre.",
        ))
    custom = value("custom_addition")
    if custom:
        lines.append(f"Additional appearance direction, subordinate to the cubemap contract: {custom}")
    return "\n".join(lines)


def _center_crop_ratio(image: Image.Image, target_ratio: float = 1.5) -> tuple[Image.Image, dict]:
    width, height = image.size
    ratio = width / max(1, height)
    if abs(ratio - target_ratio) / target_ratio > .10:
        raise ValueError(f"atlas_aspect_invalid:{width}x{height}")
    if ratio > target_ratio:
        crop_width = max(3, round(height * target_ratio))
        left = (width - crop_width) // 2
        box = (left, 0, left + crop_width, height)
    else:
        crop_height = max(2, round(width / target_ratio))
        top = (height - crop_height) // 2
        box = (0, top, width, top + crop_height)
    return image.crop(box), {
        "source_size": {"width": width, "height": height},
        "crop_box": list(box),
        "source_aspect": round(ratio, 6),
        "registration_mode": "exact_grid" if box == (0, 0, width, height) else "centered_ratio_crop",
    }


def register_and_split_atlas(atlas: Image.Image, *, face_size: int = DIRECT_FACE_SIZE
                             ) -> tuple[dict[str, Image.Image], dict]:
    """Normalize provider-specific 3:2 output and split the canonical six cells."""
    source = atlas.convert("RGB")
    if source.width < 768 or source.height < 512:
        raise ValueError(f"atlas_too_small:{source.width}x{source.height}")
    cropped, registration = _center_crop_ratio(source)
    width, height = cropped.size
    x_edges = [round(width * value / 3) for value in range(4)]
    y_edges = [round(height * value / 2) for value in range(3)]
    faces: dict[str, Image.Image] = {}
    cell_manifest = []
    for row_index, row in enumerate(DIRECT_ATLAS_LAYOUT):
        for col_index, face in enumerate(row):
            box = (x_edges[col_index], y_edges[row_index],
                   x_edges[col_index + 1], y_edges[row_index + 1])
            cell = cropped.crop(box)
            # Remove the narrow registration rail if the engine preserved it.
            trim = max(2, round(min(cell.size) * .026))
            interior = cell.crop((trim, trim, cell.width - trim, cell.height - trim))
            normalized = interior.resize((face_size, face_size), Image.Resampling.LANCZOS)
            faces[face] = normalized
            cell_manifest.append({"face": face, "box": list(box), "trim_px": trim})
    registration.update({
        "version": DIRECT_PANO_TEMPLATE_VERSION,
        "registered_size": {"width": width, "height": height},
        "face_size": face_size,
        "layout": [list(row) for row in DIRECT_ATLAS_LAYOUT],
        "cells": cell_manifest,
    })
    return {face: faces[face] for face in CUBE_FACE_ORDER}, registration


def _cube_face_index_map(width: int, height: int, *, chunk_rows: int = 128) -> np.ndarray:
    u = (np.arange(width, dtype=np.float64) + .5) / width
    lam = 2.0 * np.pi * (u - .5)
    forwards = np.stack([face_basis(face)[0] for face in CUBE_FACE_ORDER])
    labels = np.empty((height, width), dtype=np.uint8)
    sin_lam = np.sin(lam)[None, :]
    cos_lam = np.cos(lam)[None, :]
    for start in range(0, height, max(1, int(chunk_rows))):
        stop = min(height, start + max(1, int(chunk_rows)))
        v = (np.arange(start, stop, dtype=np.float64) + .5) / height
        phi = np.pi * (.5 - v)
        cos_phi = np.cos(phi)[:, None]
        directions = np.stack((
            cos_phi * sin_lam,
            np.broadcast_to(np.sin(phi)[:, None], (stop - start, width)),
            cos_phi * cos_lam,
        ), axis=-1)
        labels[start:stop] = np.argmax(directions @ forwards.T, axis=-1).astype(np.uint8)
    return labels


def cube_to_erp_chunked(faces: dict[str, Image.Image], erp_width: int,
                        erp_height: int, *, chunk_rows: int = 96) -> Image.Image:
    """Memory-bounded equivalent of the project-wide cube_to_erp transform.

    The original whole-home converter intentionally vectorises the complete
    canvas.  Two simultaneous 3840x1920 candidates would otherwise allocate
    more than a gigabyte of temporary direction/dot arrays, so this direct
    dual-engine route evaluates the exact same ray equations in row blocks.
    """
    order = tuple(CUBE_FACE_ORDER)
    face_size = faces[order[0]].size[0]
    arrays = {face: np.asarray(faces[face].convert("RGB"), dtype=np.float32) for face in order}
    forwards = np.stack([face_basis(face)[0] for face in order])
    rights = np.stack([face_basis(face)[1] for face in order])
    ups = np.stack([face_basis(face)[2] for face in order])
    u = (np.arange(erp_width, dtype=np.float64) + .5) / erp_width
    lam = 2.0 * np.pi * (u - .5)
    sin_lam = np.sin(lam)[None, :]
    cos_lam = np.cos(lam)[None, :]
    output = np.zeros((erp_height, erp_width, 3), dtype=np.uint8)
    max_coord = max(face_size - 1, 1)
    rows = max(1, int(chunk_rows))
    for start in range(0, erp_height, rows):
        stop = min(erp_height, start + rows)
        v = (np.arange(start, stop, dtype=np.float64) + .5) / erp_height
        phi = np.pi * (.5 - v)
        cos_phi = np.cos(phi)[:, None]
        directions = np.stack((
            cos_phi * sin_lam,
            np.broadcast_to(np.sin(phi)[:, None], (stop - start, erp_width)),
            cos_phi * cos_lam,
        ), axis=-1)
        dots = directions @ forwards.T
        face_indices = np.argmax(dots, axis=-1)
        block = np.zeros((stop - start, erp_width, 3), dtype=np.float32)
        for face_index, face in enumerate(order):
            selected = face_indices == face_index
            if not np.any(selected):
                continue
            scale = np.abs(dots[selected, face_index])
            tc = np.einsum("ij,j->i", directions[selected], rights[face_index]) / scale
            ts = np.einsum("ij,j->i", directions[selected], ups[face_index]) / scale
            px = (tc + 1.0) * .5 * max_coord
            py = (1.0 - (ts + 1.0) * .5) * max_coord
            x0 = np.clip(np.floor(px).astype(np.int32), 0, face_size - 2)
            y0 = np.clip(np.floor(py).astype(np.int32), 0, face_size - 2)
            fx = (px - x0).astype(np.float32)[:, None]
            fy = (py - y0).astype(np.float32)[:, None]
            arr = arrays[face]
            block[selected] = (
                arr[y0, x0] * ((1 - fx) * (1 - fy))
                + arr[y0, x0 + 1] * (fx * (1 - fy))
                + arr[y0 + 1, x0] * ((1 - fx) * fy)
                + arr[y0 + 1, x0 + 1] * (fx * fy)
            )
        output[start:stop] = np.clip(block, 0, 255).astype(np.uint8)
    return Image.fromarray(output, mode="RGB")


def gate_atlas_faces(faces: dict[str, Image.Image], registration: Optional[dict] = None,
                     floor_reference: Optional[Image.Image] = None) -> dict:
    checks: list[dict] = []
    hard_failures: list[str] = []
    arrays: dict[str, np.ndarray] = {}
    for face in CUBE_FACE_ORDER:
        image = faces.get(face)
        valid = image is not None and image.width == image.height and image.width >= 256
        checks.append({
            "check_id": f"face_{face}_square",
            "status": "pass" if valid else "fail",
            "value": f"{image.width}x{image.height}" if image is not None else "missing",
            "threshold": "square >=256",
        })
        if not valid:
            hard_failures.append(f"face_{face}_square")
            continue
        arr = np.asarray(image.convert("RGB"), dtype=np.float32)
        arrays[face] = arr
        variation = float(np.mean(np.std(arr, axis=(0, 1))))
        nonblank = variation >= 3.0
        checks.append({
            "check_id": f"face_{face}_content",
            "status": "pass" if nonblank else "fail",
            "metric": "mean_channel_stddev",
            "value": round(variation, 3),
            "threshold": 3.0,
        })
        if not nonblank:
            hard_failures.append(f"face_{face}_content")

    duplicate_pairs = []
    if len(arrays) == 6:
        thumbs = {
            face: cv2.resize(arr, (64, 64), interpolation=cv2.INTER_AREA)
            for face, arr in arrays.items()
        }
        for index, first in enumerate(CUBE_FACE_ORDER):
            for second in CUBE_FACE_ORDER[index + 1:]:
                delta = float(np.mean(np.abs(thumbs[first] - thumbs[second])))
                if delta < 2.0:
                    duplicate_pairs.append({"faces": [first, second], "mean_abs_diff": round(delta, 3)})
        checks.append({
            "check_id": "duplicate_faces",
            "status": "fail" if duplicate_pairs else "pass",
            "metric": "64px_pair_mean_abs_diff",
            "value": duplicate_pairs,
            "threshold": ">=2.0 for every pair",
        })
        if duplicate_pairs:
            hard_failures.append("duplicate_faces")

    if len(arrays) == 6 and floor_reference is not None:
        reference = np.asarray(
            floor_reference.convert("RGB").resize((256, 256), Image.Resampling.LANCZOS),
            dtype=np.uint8)
        reference_lab = cv2.cvtColor(reference, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, .5)
        _, _, palette = cv2.kmeans(
            reference_lab, 8, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        floor_scores = {}
        for face, arr in arrays.items():
            small = cv2.resize(arr.astype(np.uint8), (256, 256), interpolation=cv2.INTER_AREA)
            lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
            distance = np.sqrt(np.sum((lab[:, None] - palette[None]) ** 2, axis=2)).min(axis=1)
            floor_scores[face] = round(float(np.mean(distance < 18.0)), 5)
        ordered = sorted(floor_scores.items(), key=lambda item: item[1], reverse=True)
        detected_face, detected_score = ordered[0]
        expected_score = floor_scores.get("-Y", 0.0)
        margin = detected_score - expected_score
        mismatch = detected_face != "-Y" and margin >= .12 and detected_score >= .55
        checks.append({
            "check_id": "axis_floor_face_semantics",
            "status": "fail" if mismatch else "pass",
            "metric": "manufacturer_floor_palette_coverage",
            "value": floor_scores,
            "detected_floor_face": detected_face,
            "expected_floor_face": "-Y",
            "confidence_margin": round(margin, 5),
            "threshold": {"minimum_best_score": .55, "mismatch_margin": .12},
            "detail": "-Y must be the strongest top-down manufacturer-floor face; +Y is ceiling only",
        })
        if mismatch:
            hard_failures.append("axis_floor_face_semantics")

    edge_median = 0.0
    edge_p90 = 0.0
    edge_status = "fail"
    if len(arrays) == 6:
        # Project a small ERP, then compare adjacent pixels only where the cube
        # face selector changes.  This covers all twelve cube boundaries while
        # remaining independent of scene semantics/reference geometry.
        small_faces = {
            face: Image.fromarray(cv2.resize(arr, (256, 256), interpolation=cv2.INTER_AREA).astype(np.uint8))
            for face, arr in arrays.items()
        }
        erp = np.asarray(cube_to_erp(small_faces, 768, 384), dtype=np.float32)
        labels = _cube_face_index_map(768, 384)
        horizontal = labels[:, 1:] != labels[:, :-1]
        vertical = labels[1:, :] != labels[:-1, :]
        samples = []
        if np.any(horizontal):
            samples.append(np.mean(np.abs(erp[:, 1:] - erp[:, :-1]), axis=2)[horizontal])
        if np.any(vertical):
            samples.append(np.mean(np.abs(erp[1:] - erp[:-1]), axis=2)[vertical])
        if samples:
            values = np.concatenate(samples)
            edge_median = float(np.median(values))
            edge_p90 = float(np.percentile(values, 90))
            edge_status = "pass" if edge_median <= 35.0 and edge_p90 <= 135.0 else "fail"
    checks.append({
        "check_id": "cube_edges",
        "status": edge_status,
        "metric": "projected_face_boundary_rgb_diff",
        "value": round(edge_median, 3),
        "p90": round(edge_p90, 3),
        "threshold": {"median_max": 35.0, "p90_max": 135.0},
        "detail": "all cube-face selector boundaries; reference-free visual heuristic",
    })

    if hard_failures:
        status = "failed"
    elif edge_status == "fail":
        status = "repair_recommended"
    else:
        status = "passed"
    return {
        "version": DIRECT_PANO_GATE_VERSION,
        "status": status,
        "gate_pass": status == "passed",
        "hard_fail": bool(hard_failures),
        "geometry_locked": False,
        "delivery_scope": "ai_generated_single_center_cubemap",
        "registration": dict(registration or {}),
        "checks": checks,
        "failures": hard_failures + (["cube_edges"] if edge_status == "fail" else []),
        "warnings": [],
        "summary": "; ".join(f"{row['check_id']}:{row['status']}" for row in checks),
    }


def compose_and_gate_atlas(atlas: Image.Image, floor_reference: Optional[Image.Image] = None
                           ) -> tuple[Image.Image, dict[str, Image.Image], dict]:
    faces, registration = register_and_split_atlas(atlas)
    atlas_gate = gate_atlas_faces(faces, registration, floor_reference=floor_reference)
    if atlas_gate.get("hard_fail"):
        return Image.new("RGB", (PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT)), faces, {
            **atlas_gate,
            "atlas_gate": atlas_gate,
            "erp_gate": None,
        }
    erp = cube_to_erp_chunked(faces, PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT)
    # Avoid a filesystem round-trip: gate_visual_pano intentionally accepts a
    # path, so reproduce that strict call via a temporary lossless encode in
    # the caller.  Here return the atlas gate for combination there.
    return erp, faces, atlas_gate


def combine_direct_gates(atlas_gate: dict, erp_gate: dict) -> dict:
    hard_fail = bool(atlas_gate.get("hard_fail") or erp_gate.get("hard_fail"))
    if hard_fail:
        status = "failed"
    elif (atlas_gate.get("status") == "repair_recommended"
          or erp_gate.get("status") == "repair_recommended"):
        status = "repair_recommended"
    else:
        status = "passed"
    return {
        "version": DIRECT_PANO_GATE_VERSION,
        "status": status,
        "gate_pass": status == "passed",
        "hard_fail": hard_fail,
        "geometry_locked": False,
        "delivery_scope": "ai_generated_single_center_cubemap",
        "checks": list(atlas_gate.get("checks") or []) + list(erp_gate.get("checks") or []),
        "failures": list(dict.fromkeys(
            list(atlas_gate.get("failures") or []) + list(erp_gate.get("failures") or []))),
        "warnings": list(dict.fromkeys(
            list(atlas_gate.get("warnings") or []) + list(erp_gate.get("warnings") or []))),
        "atlas_gate": atlas_gate,
        "erp_gate": erp_gate,
        "summary": f"atlas[{atlas_gate.get('summary', '')}] | erp[{erp_gate.get('summary', '')}]",
    }


def build_cube_boundary_repair_mask(width: int = PURE_RENDER_PANO_WIDTH,
                                    height: int = PURE_RENDER_PANO_HEIGHT,
                                    *, band_px: int = 18) -> Image.Image:
    """White mask around all cube boundaries plus the ERP wrap seam."""
    labels = _cube_face_index_map(width, height)
    boundary = np.zeros((height, width), dtype=np.uint8)
    boundary[:, 1:] |= (labels[:, 1:] != labels[:, :-1]).astype(np.uint8) * 255
    boundary[1:, :] |= (labels[1:, :] != labels[:-1, :]).astype(np.uint8) * 255
    seam = max(2, int(band_px))
    boundary[:, :seam] = 255
    boundary[:, -seam:] = 255
    kernel_size = max(3, seam * 2 + 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    boundary = cv2.dilate(boundary, kernel, iterations=1)
    return Image.fromarray(boundary, mode="L")


def build_cube_fusion_repair_prompt() -> str:
    return (
        "Repair only the white masked bands of this monoscopic 2:1 equirectangular panorama. "
        "The bands correspond to cubemap face boundaries and the longitude wrap seam. Blend geometry, "
        "floor planks, materials, lighting and object contours continuously across each boundary. "
        "Preserve every unmasked pixel and the overall room, furniture identities, camera centre, horizon, "
        "north/south poles and exact 3840x1920 ERP layout. Return one image only, without text or borders."
    )


def create_direct_paid_preview(*, source_path: str, source_hash: str, params: dict,
                               engines: list[dict], estimated_costs: dict,
                               quality_plan: Optional[dict] = None,
                               film_contract: Optional[dict] = None,
                               geometry_contract: Optional[dict] = None,
                               room_reference_path: str = "",
                               room_reference_hash: str = "",
                               now: Optional[float] = None) -> dict:
    created_at = time.time() if now is None else float(now)
    preview_id = f"vrdirect_{secrets.token_hex(12)}"
    params_snapshot = dict(params or {})
    prompt_values = dict(params_snapshot)
    if room_reference_path:
        prompt_values["_room_reference_geometry"] = True
    prompts = {}
    for engine in engines:
        base_prompt = build_direct_atlas_prompt(
            prompt_values, engine_label=str(engine.get("label") or engine["key"]))
        prompts[str(engine["key"])] = (
            compile_panorama_prompt(base_prompt, quality_plan)
            if quality_plan else base_prompt
        )
    bound = {
        "policy": DIRECT_PANO_POLICY,
        "preview_id": preview_id,
        "source_hash": source_hash,
        "params_sha256": _stable_hash(params_snapshot),
        "engines": [{k: row.get(k) for k in ("key", "label", "provider", "endpoint", "model_id")}
                    for row in engines],
        "prompt_sha256": {key: hashlib.sha256(value.encode("utf-8")).hexdigest()
                           for key, value in prompts.items()},
        "quality_plan_hash": str((quality_plan or {}).get("plan_hash") or ""),
        "film_contract_hash": str(((film_contract or {}).get("manifest") or {}).get("manifest_hash") or ""),
        "film_guide_hash": str((film_contract or {}).get("guide_sha256") or ""),
        "geometry_contract_hash": str((geometry_contract or {}).get("contract_hash") or ""),
        "room_reference_hash": str(room_reference_hash or ""),
        "output_size": PURE_RENDER_PANO_SIZE,
        "atlas_layout": [list(row) for row in DIRECT_ATLAS_LAYOUT],
        "max_provider_calls": len(engines),
    }
    return {
        **bound,
        "preview_hash": _stable_hash(bound),
        "source_path": source_path,
        "params": params_snapshot,
        "prompts": prompts,
        "quality_plan": copy.deepcopy(quality_plan) if quality_plan else None,
        "film_contract": copy.deepcopy(film_contract) if film_contract else None,
        "geometry_contract": copy.deepcopy(geometry_contract) if geometry_contract else None,
        "room_reference_path": str(room_reference_path or ""),
        "created_at_epoch": created_at,
        "expires_at_epoch": created_at + PAID_PREVIEW_TTL_SECONDS,
        "estimated_costs": dict(estimated_costs or {}),
        "status": "ready",
        "job_id": "",
        "error": "",
    }


def validate_direct_paid_preview(row: dict, *, preview_hash: str, source_hash: str,
                                 now: Optional[float] = None, allow_expired: bool = False) -> None:
    current = time.time() if now is None else float(now)
    if not isinstance(row, dict) or row.get("policy") != DIRECT_PANO_POLICY:
        raise ValueError("direct_panorama_preview_missing")
    if not allow_expired and current > float(row.get("expires_at_epoch") or 0):
        raise ValueError("direct_panorama_preview_expired")
    if not hmac.compare_digest(str(row.get("preview_hash") or ""), str(preview_hash or "")):
        raise ValueError("direct_panorama_preview_hash_mismatch")
    if not hmac.compare_digest(str(row.get("source_hash") or ""), str(source_hash or "")):
        raise ValueError("direct_panorama_source_changed")
    if not hmac.compare_digest(str(row.get("params_sha256") or ""), _stable_hash(row.get("params") or {})):
        raise ValueError("direct_panorama_preview_tampered")
    for key, prompt in (row.get("prompts") or {}).items():
        actual = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual, str((row.get("prompt_sha256") or {}).get(key) or "")):
            raise ValueError("direct_panorama_preview_tampered")
    quality_plan = row.get("quality_plan")
    if quality_plan:
        if not validate_quality_plan(quality_plan):
            raise ValueError("direct_panorama_preview_tampered")
    film_contract = row.get("film_contract")
    if film_contract:
        manifest = dict(film_contract.get("manifest") or {})
        expected = str(manifest.pop("manifest_hash", "") or "")
        if not expected or not hmac.compare_digest(_stable_hash(manifest), expected):
            raise ValueError("direct_panorama_preview_tampered")
        if not hmac.compare_digest(expected, str(row.get("film_contract_hash") or "")):
            raise ValueError("direct_panorama_preview_tampered")
        guide_b64 = str(film_contract.get("guide_b64") or "")
        guide_hash = hashlib.sha256(base64.b64decode(guide_b64)).hexdigest() if guide_b64 else ""
        if not hmac.compare_digest(guide_hash, str(row.get("film_guide_hash") or "")):
            raise ValueError("direct_panorama_preview_tampered")
    geometry_contract = row.get("geometry_contract")
    if geometry_contract:
        try:
            validate_geometry_contract(geometry_contract)
        except ValueError as exc:
            raise ValueError("direct_panorama_preview_tampered") from exc
        if not hmac.compare_digest(
                str(geometry_contract.get("contract_hash") or ""),
                str(row.get("geometry_contract_hash") or "")):
            raise ValueError("direct_panorama_preview_tampered")
    room_reference_path = str(row.get("room_reference_path") or "")
    if room_reference_path:
        try:
            with open(room_reference_path, "rb") as handle:
                current_reference_hash = hashlib.sha256(handle.read()).hexdigest()
        except OSError as exc:
            raise ValueError("direct_panorama_preview_tampered") from exc
        if not hmac.compare_digest(
                current_reference_hash, str(row.get("room_reference_hash") or "")):
            raise ValueError("direct_panorama_preview_tampered")
    if quality_plan and not hmac.compare_digest(
            str(quality_plan.get("plan_hash") or ""),
            str(row.get("quality_plan_hash") or "")):
        raise ValueError("direct_panorama_preview_tampered")


def public_direct_paid_preview(row: dict, *, source_thumb: str = "") -> dict:
    costs = dict(row.get("estimated_costs") or {})
    numeric_costs = [float(value) for value in costs.values() if value is not None]
    total = sum(numeric_costs) if len(numeric_costs) == len(row.get("engines") or []) else None
    return {
        "policy": row.get("policy"),
        "preview_id": row.get("preview_id"),
        "preview_hash": row.get("preview_hash"),
        "expires_at": _iso_utc(float(row.get("expires_at_epoch") or 0)),
        "source": {"thumb": source_thumb, "sha256": row.get("source_hash"), "label": "地板小样"},
        "engines": [dict(engine, estimated_cost=costs.get(engine.get("key")))
                    for engine in (row.get("engines") or [])],
        "atlas": {
            "width": DIRECT_ATLAS_WIDTH,
            "height": DIRECT_ATLAS_HEIGHT,
            "layout": [list(value) for value in DIRECT_ATLAS_LAYOUT],
            "face_size": DIRECT_FACE_SIZE,
        },
        "output_size": {"width": PURE_RENDER_PANO_WIDTH, "height": PURE_RENDER_PANO_HEIGHT},
        "max_provider_calls": int(row.get("max_provider_calls") or 0),
        "estimated_cost": total,
        "quality_plan": public_quality_plan(row.get("quality_plan")),
        "film_contract": copy.deepcopy(row.get("film_contract")),
        "geometry_contract": public_geometry_contract(row.get("geometry_contract")),
        "room_reference": ({
            "sha256": row.get("room_reference_hash"),
            "label": "空间参考效果图",
        } if row.get("room_reference_hash") else None),
        "warning": DIRECT_PANO_WARNING,
    }


__all__ = [
    "DIRECT_ATLAS_HEIGHT", "DIRECT_ATLAS_LAYOUT", "DIRECT_ATLAS_WIDTH", "DIRECT_FACE_SIZE",
    "DIRECT_PANO_GATE_VERSION", "DIRECT_PANO_POLICY", "DIRECT_PANO_ROUTE",
    "DIRECT_PANO_TEMPLATE_VERSION", "DIRECT_PANO_WARNING", "build_atlas_template",
    "build_room_geometry_faces", "build_room_geometry_guide", "build_room_geometry_guide_erp",
    "build_cube_boundary_repair_mask", "build_cube_fusion_repair_prompt",
    "build_direct_atlas_prompt", "combine_direct_gates", "compose_and_gate_atlas",
    "cube_to_erp_chunked",
    "create_direct_paid_preview", "gate_atlas_faces", "public_direct_paid_preview",
    "register_and_split_atlas", "validate_direct_paid_preview",
]
