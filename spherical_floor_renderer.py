# -*- coding: utf-8 -*-
"""Deterministic floor material projection for monoscopic ERP panoramas.

Unlike :mod:`floor_renderer`, this module does not use a single perspective
homography.  Every equirectangular pixel is converted to a camera ray and the
ray is intersected with one shared horizontal floor plane.  The resulting
world X/Z coordinates form a single UV system around the full 360 degrees.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

import cv2
import numpy as np
from PIL import Image

from .floor_renderer import image_sha256, texture_quality_warnings
from .floor_segmentation import encode_mask_png, segment_floor
from .film_repeat_floor import sample_film_floor
from .local_floor_semantics import predict_floor_semantics
from .whole_home_pano_gate import erp_to_perspective


SPHERICAL_FLOOR_RENDERER_VERSION = "spherical-floor-render-v4"
SPHERICAL_FLOOR_MASK_VERSION = "spherical-floor-mask-clipseg-mobilesam-depth-v3"


@dataclass(frozen=True)
class SphericalFloorRecipe:
    camera_height_m: float = 1.55
    rotation_deg: float = 90.0
    scale: float = 1.0
    offset_x: float = 0.0
    offset_z: float = 0.0
    texture_width_mm: float = 1900.0
    texture_height_mm: float = 1268.0
    plank_width_mm: float | None = None
    plank_length_mm: float | None = None
    illumination_strength: float = 0.65
    shadow_strength: float = 0.85
    contact_shadow_strength: float = 0.35
    feather: float = 0.006


@dataclass(frozen=True)
class PanoMaskView:
    id: str
    label: str
    yaw_deg: float
    pitch_deg: float
    fov_deg: float
    width: int
    height: int


FLOOR_MASK_VIEWS: tuple[PanoMaskView, ...] = (
    PanoMaskView("front", "前方", 0.0, -15.0, 100.0, 640, 480),
    PanoMaskView("right", "右侧", 90.0, -15.0, 100.0, 640, 480),
    PanoMaskView("back", "后方", 180.0, -15.0, 100.0, 640, 480),
    PanoMaskView("left", "左侧", 270.0, -15.0, 100.0, 640, 480),
    PanoMaskView("nadir", "脚下", 0.0, -90.0, 100.0, 640, 640),
)


def validate_spherical_recipe(recipe: SphericalFloorRecipe) -> None:
    if not 0.5 <= float(recipe.camera_height_m) <= 2.5:
        raise ValueError("相机高度必须在 0.5–2.5 米之间")
    if not 0.15 <= float(recipe.scale) <= 4.0:
        raise ValueError("纹理比例必须在 0.15–4.0 之间")
    if not -180.0 <= float(recipe.rotation_deg) <= 180.0:
        raise ValueError("铺装方向必须在 -180°–180° 之间")
    if not 50.0 <= float(recipe.texture_width_mm) <= 50000.0:
        raise ValueError("整张纹理宽度必须在 50–50000 mm 之间")
    if not 50.0 <= float(recipe.texture_height_mm) <= 50000.0:
        raise ValueError("整张纹理高度必须在 50–50000 mm 之间")
    for value, label in (
        (recipe.illumination_strength, "亮部跟随"),
        (recipe.shadow_strength, "阴影跟随"),
        (recipe.contact_shadow_strength, "接触阴影"),
    ):
        if not 0.0 <= float(value) <= 1.5:
            raise ValueError(f"{label}必须在 0–1.5 之间")
    if not 0.0 <= float(recipe.feather) <= 0.08:
        raise ValueError("羽化必须在 0–0.08 之间")


def _srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.04045, value / 12.92,
                    ((value + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.0031308, value * 12.92,
                    1.055 * np.maximum(value, 0.0) ** (1.0 / 2.4) - 0.055)


def encode_png_b64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_mask_b64(value: str) -> Image.Image:
    raw = str(value or "").strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        payload = base64.b64decode(raw, validate=True)
        with Image.open(io.BytesIO(payload)) as source:
            if source.width < 1 or source.height < 1 or source.width > 2048 or source.height > 2048:
                raise ValueError("地板遮罩尺寸超出 2048×2048 安全限制")
            source.load()
            return source.convert("L").copy()
    except Exception as ex:
        raise ValueError("地板遮罩不是有效的 PNG") from ex


def _view_basis(view: PanoMaskView) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    yaw = math.radians(float(view.yaw_deg))
    pitch = math.radians(float(view.pitch_deg))
    forward = np.array((math.cos(pitch) * math.sin(yaw), math.sin(pitch),
                        math.cos(pitch) * math.cos(yaw)), dtype=np.float32)
    right = np.cross(forward, np.array((0.0, 1.0, 0.0), dtype=np.float32))
    if float(np.linalg.norm(right)) < 1e-7:
        # At the nadir, choose the same X/Z orientation as the front view.
        right = np.array((1.0, 0.0, 0.0), dtype=np.float32)
    right /= max(float(np.linalg.norm(right)), 1e-7)
    up = np.cross(right, forward)
    up /= max(float(np.linalg.norm(up)), 1e-7)
    focal = (view.width / 2.0) / math.tan(math.radians(view.fov_deg) / 2.0)
    return forward, right, up, float(focal)


def prepare_floor_mask_views(erp: Image.Image, *, cache_key: str = "") -> list[dict]:
    """Create five rectilinear views and local MobileSAM floor proposals."""
    source = erp.convert("RGB")
    rows: list[dict] = []
    for view in FLOOR_MASK_VIEWS:
        perspective = erp_to_perspective(
            source, view.yaw_deg, view.pitch_deg, view.fov_deg,
            view.width, view.height)
        positive_b64 = ""
        auto_seed = True
        if view.id == "nadir":
            # The complete nadir view is the same horizontal floor plane, but a
            # single MobileSAM point can return only one visually divided half
            # of a plank field.  Seven interior positive prompts join the plane
            # without inventing a fixed ERP bottom band.  Side views remain
            # semantic-only so furniture and raised platforms stay protected.
            positive = np.zeros((view.height, view.width), dtype=bool)
            radius = max(3, round(min(view.width, view.height) * .008))
            for nx, ny in ((.5, .5), (.3, .5), (.7, .5), (.5, .3), (.5, .7), (.25, .25), (.75, .75)):
                px, py = round(nx * (view.width - 1)), round(ny * (view.height - 1))
                positive[max(0, py - radius):min(view.height, py + radius + 1),
                         max(0, px - radius):min(view.width, px + radius + 1)] = True
            positive_b64 = encode_mask_png(positive)
            auto_seed = False
        working, result = segment_floor(
            perspective, f"{cache_key}:{view.id}:{image_sha256(perspective)}",
            positive_b64=positive_b64, auto_seed=auto_seed)
        sam_mask = (result.mask.astype(bool) if result.mask is not None
                    else np.zeros((working.height, working.width), dtype=bool))
        semantics = predict_floor_semantics(perspective)
        semantic_confidence = 0.0
        if semantics.status == "ok" and semantics.probability is not None:
            probability = semantics.probability
            if probability.shape != sam_mask.shape:
                probability = cv2.resize(
                    probability, (sam_mask.shape[1], sam_mask.shape[0]),
                    interpolation=cv2.INTER_CUBIC)
            fused = (probability >= .32) | (sam_mask & (probability >= .16))
            fused = cv2.morphologyEx(
                fused.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
            semantic_confidence = float(np.median(probability[fused])) if np.any(fused) else 0.0
        else:
            fused = sam_mask
        mask = Image.fromarray(fused.astype(np.uint8) * 255, "L")
        if mask.size != perspective.size:
            mask = mask.resize(perspective.size, Image.Resampling.NEAREST)
        rows.append({
            "id": view.id,
            "label": view.label,
            "yaw_deg": view.yaw_deg,
            "pitch_deg": view.pitch_deg,
            "fov_deg": view.fov_deg,
            "width": view.width,
            "height": view.height,
            "image_b64": encode_png_b64(perspective),
            "mask_b64": encode_png_b64(mask),
            "confidence": round(float(result.confidence), 5),
            "status": result.status,
            "warnings": list(result.warnings or []) + ([semantics.error] if semantics.error else []),
            "semantic_model": semantics.model,
            "semantic_status": semantics.status,
            "semantic_confidence": round(semantic_confidence, 5),
            "semantic_prompts": list(semantics.prompts),
        })
    return rows


def combine_view_masks(view_masks: Sequence[dict], width: int, height: int,
                       *, chunk_rows: int = 96) -> Image.Image:
    """Back-project edited view masks to one authoritative ERP floor mask.

    Each ERP direction is owned by the covering view whose optical axis has the
    largest dot product.  This makes edits deterministic in overlap areas and
    avoids OR-ing a false positive from a very oblique view over a correct one.
    """
    supplied: dict[str, np.ndarray] = {}
    for row in view_masks:
        view_id = str(row.get("id") or "")
        view = next((item for item in FLOOR_MASK_VIEWS if item.id == view_id), None)
        if view is None:
            continue
        mask = decode_mask_b64(str(row.get("mask_b64") or ""))
        if mask.size != (view.width, view.height):
            mask = mask.resize((view.width, view.height), Image.Resampling.NEAREST)
        supplied[view_id] = np.asarray(mask, dtype=np.uint8) >= 128
    if not supplied:
        raise ValueError("至少需要一个透视地板遮罩")

    u = (np.arange(width, dtype=np.float32) + 0.5) / float(width)
    lam = 2.0 * np.pi * (u - 0.5)
    sin_lam = np.sin(lam)[None, :]
    cos_lam = np.cos(lam)[None, :]
    output = np.zeros((height, width), dtype=np.uint8)
    for start in range(0, height, max(1, int(chunk_rows))):
        stop = min(height, start + max(1, int(chunk_rows)))
        v = (np.arange(start, stop, dtype=np.float32) + 0.5) / float(height)
        phi = np.pi * (0.5 - v)
        cos_phi = np.cos(phi)[:, None]
        directions = np.stack((
            cos_phi * sin_lam,
            np.broadcast_to(np.sin(phi)[:, None], (stop - start, width)),
            cos_phi * cos_lam,
        ), axis=-1)
        best = np.full((stop - start, width), -2.0, dtype=np.float32)
        best_positive = np.full((stop - start, width), -2.0, dtype=np.float32)
        selected_value = np.zeros((stop - start, width), dtype=np.uint8)
        for view in FLOOR_MASK_VIEWS:
            mask = supplied.get(view.id)
            if mask is None:
                continue
            forward, right, up, focal = _view_basis(view)
            depth = directions @ forward
            valid = depth > 1e-4
            px = np.zeros_like(depth)
            py = np.zeros_like(depth)
            np.divide(directions @ right, depth, out=px, where=valid)
            np.divide(directions @ up, depth, out=py, where=valid)
            px = px * focal + view.width / 2.0
            py = -py * focal + view.height / 2.0
            xi = np.clip(np.rint(px).astype(np.int32), 0, view.width - 1)
            yi = np.clip(np.rint(py).astype(np.int32), 0, view.height - 1)
            valid &= (px >= 0) & (px < view.width) & (py >= 0) & (py < view.height)
            sampled_positive = mask[yi, xi]
            positive = valid & sampled_positive & (depth > best_positive)
            best_positive[positive] = depth[positive]
            take = valid & (depth > best)
            selected_value[take] = sampled_positive[take].astype(np.uint8) * 255
            best[take] = depth[take]
        # A negative at the very edge of the closest view used to erase a
        # correct central-floor positive from the neighbouring view, leaving
        # old-material islands in doorways and behind furniture. Rescue only
        # high-quality overlapping positives: the alternate ray must be near
        # its optical axis and nearly as frontal as the owner. This is not a
        # blanket OR, so oblique cabinet/table false positives remain excluded.
        rescue = (
            (selected_value == 0)
            & (best_positive >= 0.68)
            & (best_positive >= best - 0.12)
        )
        selected_value[rescue] = 255
        # A horizontal floor can only be seen below the geometric horizon.
        selected_value[directions[..., 1] >= 0.015] = 0
        output[start:stop] = selected_value

    # Morphology must be circular in longitude.  Pad with wrapped columns,
    # process, then take only the centre so the ERP seam cannot be cut apart.
    pad = max(8, round(width * 0.006))
    wrapped = np.concatenate((output[:, -pad:], output, output[:, :pad]), axis=1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    wrapped = cv2.morphologyEx(wrapped, cv2.MORPH_CLOSE, kernel, iterations=1)
    wrapped = cv2.morphologyEx(wrapped, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    return Image.fromarray(wrapped[:, pad:pad + width], "L")


def _wrap_gaussian(gray: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0.1:
        return gray.astype(np.float32)
    pad = min(gray.shape[1] // 3, max(8, int(math.ceil(sigma * 3.0))))
    wrapped = np.concatenate((gray[:, -pad:], gray, gray[:, :pad]), axis=1)
    blurred = cv2.GaussianBlur(wrapped, (0, 0), sigmaX=sigma, sigmaY=sigma,
                               borderType=cv2.BORDER_REFLECT_101)
    return blurred[:, pad:pad + gray.shape[1]].astype(np.float32)


def _make_texture_periodic(texture: np.ndarray) -> tuple[np.ndarray, bool]:
    """Move non-periodic photo edges inward and blend them deterministically."""
    edge_delta = max(
        float(np.mean(np.abs(texture[:, 0].astype(np.float32) - texture[:, -1].astype(np.float32)))),
        float(np.mean(np.abs(texture[0].astype(np.float32) - texture[-1].astype(np.float32)))),
    )
    if edge_delta <= 8.0:
        return texture, False
    output = np.roll(texture, (texture.shape[0] // 2, texture.shape[1] // 2), axis=(0, 1)).astype(np.float32)
    for axis, length in ((1, texture.shape[1]), (0, texture.shape[0])):
        centre = length // 2
        band = max(2, round(length * 0.018))
        start, stop = centre - band, centre + band
        if axis == 1:
            first = output[:, start].copy()
            last = output[:, stop].copy()
            for index, position in enumerate(range(start, stop + 1)):
                t = index / max(1, stop - start)
                t = t * t * (3.0 - 2.0 * t)
                output[:, position] = first * (1.0 - t) + last * t
        else:
            first = output[start].copy()
            last = output[stop].copy()
            for index, position in enumerate(range(start, stop + 1)):
                t = index / max(1, stop - start)
                t = t * t * (3.0 - 2.0 * t)
                output[position] = first * (1.0 - t) + last * t
    return np.clip(output, 0, 255).astype(np.uint8), True


def render_spherical_floor(scene: Image.Image, texture: Image.Image, mask: Image.Image,
                           recipe: SphericalFloorRecipe = SphericalFloorRecipe(),
                           *, max_side: int = 0, chunk_rows: int = 96,
                           film_image: Image.Image | None = None,
                           film_manifest: dict | None = None,
                           ) -> tuple[Image.Image, dict]:
    """Project ``texture`` onto one horizontal world plane inside an ERP."""
    validate_spherical_recipe(recipe)
    source = scene.convert("RGB")
    tex_image = texture.convert("RGB")
    floor_mask = mask.convert("L")
    if max_side and max(source.size) > max_side:
        ratio = max_side / max(source.size)
        size = (max(2, round(source.width * ratio)), max(1, round(source.height * ratio)))
        source = source.resize(size, Image.Resampling.LANCZOS)
    if floor_mask.size != source.size:
        floor_mask = floor_mask.resize(source.size, Image.Resampling.NEAREST)

    base = np.asarray(source, dtype=np.uint8)
    if film_image is not None and film_manifest:
        tex = np.asarray(film_image.convert("RGB"), dtype=np.uint8)
        texture_periodicized = False
    else:
        tex, texture_periodicized = _make_texture_periodic(np.asarray(tex_image, dtype=np.uint8))
    binary = (np.asarray(floor_mask, dtype=np.uint8) >= 128).astype(np.uint8)
    if cv2.countNonZero(binary) == 0:
        raise ValueError("地板遮罩为空，请先在透视视图中标记地面")
    height, width = base.shape[:2]
    # Never allow an accidental mask to paint the upper hemisphere.
    horizon_row = max(0, min(height, int(math.floor(height * (0.5 - 0.015 / math.pi)))))
    binary[:horizon_row] = 0
    if cv2.countNonZero(binary) == 0:
        raise ValueError("地板遮罩没有位于地平线以下的有效区域")

    gray = cv2.cvtColor(base, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    linear_gray = _srgb_to_linear(gray)
    sigma_large = max(4.0, min(width, height) * 0.025)
    low = _wrap_gaussian(linear_gray, sigma_large)
    sigma_contact = max(2.0, min(width, height) * 0.006)
    contact_base = _wrap_gaussian(linear_gray, sigma_contact)
    contact = np.clip(contact_base / np.maximum(low, 0.025), 0.62, 1.0)
    valid = binary > 0
    median_low = float(np.median(low[valid])) if np.any(valid) else 1.0
    feather_px = float(recipe.feather) * min(width, height)
    mask_distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    if feather_px > 0:
        alpha = np.clip(mask_distance / max(1.0, feather_px), 0.0, 1.0).astype(np.float32)
    else:
        alpha = binary.astype(np.float32)
    alpha *= binary

    tex_w_m = max(0.05, recipe.texture_width_mm / 1000.0 * recipe.scale)
    tex_h_m = max(0.05, recipe.texture_height_mm / 1000.0 * recipe.scale)
    texture_pyramid = [tex]
    while len(texture_pyramid) < 8 and min(texture_pyramid[-1].shape[:2]) >= 32:
        texture_pyramid.append(cv2.pyrDown(texture_pyramid[-1]))
    theta = math.radians(float(recipe.rotation_deg))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    u = (np.arange(width, dtype=np.float32) + 0.5) / float(width)
    lam = 2.0 * np.pi * (u - 0.5)
    sin_lam = np.sin(lam)[None, :]
    cos_lam = np.cos(lam)[None, :]
    result = base.copy()
    linear_lut = _srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0)

    for start in range(0, height, max(1, int(chunk_rows))):
        stop = min(height, start + max(1, int(chunk_rows)))
        block_mask = binary[start:stop] > 0
        if not np.any(block_mask):
            continue
        v = (np.arange(start, stop, dtype=np.float32) + 0.5) / float(height)
        phi = np.pi * (0.5 - v)
        dy = np.sin(phi)[:, None]
        cos_phi = np.cos(phi)[:, None]
        safe_dy = np.where(dy < -1e-5, dy, np.nan)
        distance = -float(recipe.camera_height_m) / safe_dy
        world_x = distance * cos_phi * sin_lam
        world_z = distance * cos_phi * cos_lam
        rotated_x = cos_t * world_x + sin_t * world_z
        rotated_z = -sin_t * world_x + cos_t * world_z
        uv_x = np.mod(rotated_x / tex_w_m + recipe.offset_x, 1.0).astype(np.float32)
        uv_y = np.mod(rotated_z / tex_h_m + recipe.offset_z, 1.0).astype(np.float32)
        # Explicit mip selection is required here: cv2.remap does not
        # automatically anti-alias a repeating texture that becomes hundreds
        # of texels per ERP pixel near the horizon.  The plane footprint is
        # mostly latitude-dependent, so one level per output row is stable and
        # avoids the noisy moire visible in ordinary full-resolution remaps.
        phi_abs = np.maximum(np.abs(phi), 1e-4)
        radial_m = (float(recipe.camera_height_m) / np.maximum(np.sin(phi_abs) ** 2, 1e-6)
                    * (np.pi / height))
        radius_m = float(recipe.camera_height_m) * np.abs(np.cos(phi) / np.maximum(np.sin(phi_abs), 1e-6))
        tangential_m = radius_m * (2.0 * np.pi / width)
        texels_per_m = max(tex.shape[1] / tex_w_m, tex.shape[0] / tex_h_m)
        footprint = np.maximum(radial_m, tangential_m) * texels_per_m
        world_footprint_mm = np.maximum(radial_m, tangential_m) * 1000.0
        row_levels = np.clip(np.rint(np.log2(np.maximum(footprint, 1.0))).astype(np.int32),
                             0, len(texture_pyramid) - 1)
        if film_image is not None and film_manifest:
            mapped, _ = sample_film_floor(
                film_image,
                world_x,
                world_z,
                film_manifest,
                rotation_deg=float(recipe.rotation_deg),
                offset_x=float(recipe.offset_x),
                offset_z=float(recipe.offset_z),
                footprint_mm=world_footprint_mm,
            )
            # Film minification happens inside sample_film_floor *before*
            # remapping.  Blurring here would destroy analytic plank joints.
        else:
            mapped = np.zeros((stop - start, width, 3), dtype=np.uint8)
            for level in np.unique(row_levels):
                row_selector = row_levels == level
                level_texture = texture_pyramid[int(level)]
                map_x = (uv_x[row_selector] * level_texture.shape[1]).astype(np.float32)
                map_y = (uv_y[row_selector] * level_texture.shape[0]).astype(np.float32)
                mapped[row_selector] = cv2.remap(
                    level_texture, map_x, map_y, cv2.INTER_LANCZOS4,
                    borderMode=cv2.BORDER_WRAP)
        base_strip = base[start:stop]
        base_lin = linear_lut[base_strip]
        tex_lin = linear_lut[mapped]
        ratio = np.clip(low[start:stop] / max(0.03, median_low), 0.45, 1.55)
        gain = np.where(
            ratio < 1.0,
            1.0 + (ratio - 1.0) * float(recipe.shadow_strength),
            1.0 + (ratio - 1.0) * float(recipe.illumination_strength),
        )
        # The old implementation applied medium-frequency source-floor texture
        # everywhere, which stamped AI plank noise onto the manufacturer film.
        # Contact shadow is physically useful only near furniture/wall mask
        # boundaries, so fade it out rapidly inside the open floor field.
        contact_radius = max(5.0, min(width, height) * .014)
        contact_influence = np.exp(-mask_distance[start:stop] / contact_radius)
        contact_gain = 1.0 + ((contact[start:stop] - 1.0)
                              * float(recipe.contact_shadow_strength) * contact_influence)
        rendered = np.clip(tex_lin * gain[..., None] * contact_gain[..., None], 0.0, 1.0)
        a = alpha[start:stop]
        mixed = base_lin * (1.0 - a[..., None]) + rendered * a[..., None]
        encoded = np.rint(np.clip(_linear_to_srgb(mixed), 0.0, 1.0) * 255.0).astype(np.uint8)
        result_strip = result[start:stop]
        result_strip[block_mask] = encoded[block_mask]

    metadata = {
        "provider": "local",
        "model": SPHERICAL_FLOOR_RENDERER_VERSION,
        "projection": "equirectangular",
        "scene_size": [width, height],
        "texture_size": [tex_image.width, tex_image.height],
        "texture_sha256": image_sha256(tex_image),
        "texture_periodicized": texture_periodicized,
        "material_source": "manufacturer_repeat_film" if film_image is not None and film_manifest else "floor_sample",
        "film_repeat": ({
            "version": film_manifest.get("version"),
            "manifest_hash": film_manifest.get("manifest_hash"),
            "film_width_mm": film_manifest.get("film_width_mm"),
            "repeat_length_mm": film_manifest.get("repeat_length_mm"),
            "slitting": film_manifest.get("slitting"),
            "phase_state_count": film_manifest.get("phase_state_count"),
            "effective_board_states": film_manifest.get("effective_board_states"),
            "repeat_registration": film_manifest.get("repeat_registration"),
            "exclusion_rects": film_manifest.get("exclusion_rects"),
        } if film_image is not None and film_manifest else None),
        "recipe": asdict(recipe),
        "mask_coverage": round(float(binary.mean()), 6),
        "outside_mask_byte_identical": bool(np.array_equal(result[~valid], base[~valid])),
        "world_plane": {"normal": [0.0, 1.0, 0.0], "height_m": -recipe.camera_height_m},
        "contact_shadow_localized": True,
        "warnings": texture_quality_warnings(tex_image, width, recipe.scale),
    }
    return Image.fromarray(result, "RGB"), metadata


__all__ = [
    "FLOOR_MASK_VIEWS", "SPHERICAL_FLOOR_MASK_VERSION",
    "SPHERICAL_FLOOR_RENDERER_VERSION", "PanoMaskView", "SphericalFloorRecipe",
    "combine_view_masks", "decode_mask_b64", "encode_png_b64",
    "prepare_floor_mask_views", "render_spherical_floor", "validate_spherical_recipe",
]
