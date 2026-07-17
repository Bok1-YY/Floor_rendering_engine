"""Deterministic, SKU-preserving floor texture projection.

Generative models are intentionally not used here.  A user supplied floor mask
and perspective calibration quadrilateral drive an OpenCV homography; the
original product image is sampled with wrapped UV coordinates and only the
scene's low-frequency luminance is re-applied.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import cv2
import numpy as np
from PIL import Image, ImageOps


@dataclass(frozen=True)
class RenderRecipe:
    scale: float = 1.0
    rotation: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    illumination_strength: float = 0.65
    shadow_strength: float = 0.85
    feather: float = 0.008
    texture_width_mm: float | None = None
    texture_height_mm: float | None = None


def image_sha256(image: Image.Image) -> str:
    arr = np.asarray(ImageOps.exif_transpose(image).convert("RGB"), dtype=np.uint8)
    return hashlib.sha256(arr.tobytes()).hexdigest()


def _points(quad: Iterable) -> np.ndarray:
    pts = []
    for item in quad:
        if isinstance(item, dict):
            pts.append((float(item["x"]), float(item["y"])))
        elif hasattr(item, "x") and hasattr(item, "y"):
            pts.append((float(item.x), float(item.y)))
        else:
            pts.append((float(item[0]), float(item[1])))
    return np.asarray(pts, dtype=np.float32)


def validate_calibration_quad(quad: Sequence) -> np.ndarray:
    """Validate normalized TL, TR, BR, BL points and return float32 points."""
    pts = _points(quad)
    if pts.shape != (4, 2) or not np.isfinite(pts).all():
        raise ValueError("标定四边形必须包含 4 个有效点")
    if np.any(pts < 0) or np.any(pts > 1):
        raise ValueError("标定点必须在图片范围内")
    crosses = []
    for i in range(4):
        a, b, c = pts[i], pts[(i + 1) % 4], pts[(i + 2) % 4]
        ab, bc = b - a, c - b
        crosses.append(float(ab[0] * bc[1] - ab[1] * bc[0]))
    if min(abs(v) for v in crosses) < 1e-5 or not (all(v > 0 for v in crosses) or all(v < 0 for v in crosses)):
        raise ValueError("标定四边形不能自交、凹陷或面积过小")
    # Enforce the semantic corner order as well as convexity.
    if pts[0, 0] >= pts[1, 0] or pts[3, 0] >= pts[2, 0]:
        raise ValueError("标定点顺序应为左上、右上、右下、左下")
    return pts


def texture_quality_warnings(texture: Image.Image, projected_width: int = 0, scale: float = 1.0) -> list[str]:
    tex = np.asarray(ImageOps.exif_transpose(texture).convert("RGB"), dtype=np.uint8)
    h, w = tex.shape[:2]
    warnings: list[str] = []
    if min(w, h) < 1200 or w * h < 3_000_000:
        warnings.append(f"小样只有 {w}×{h}，近景木纹可能不足；建议使用无损高分辨率扫描图")
    gray = cv2.cvtColor(tex, cv2.COLOR_RGB2GRAY)
    detail = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    if detail < 45.0:
        warnings.append(f"小样高频细节偏少（清晰度指标 {detail:.1f}），渲染器不会凭空生成真实木纹")
    if projected_width and projected_width * max(0.1, scale) > w * 1.4:
        warnings.append("当前铺装比例会放大小样像素，建议减小纹理比例或更换更高清素材")
    if 1.15 <= w / max(1, h) <= 2.5:
        warnings.append("当前素材看起来像多片地板的整张铺装图：可直接使用，但总分辨率会分摊到多片板；近景建议后续补充 600 DPI 单片扫描")
    return warnings


def _srgb_to_linear(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(np.maximum(v, 0), 1 / 2.4) - 0.055)


def _normalize_inputs(scene: Image.Image, texture: Image.Image, mask: Image.Image, max_side: int):
    scene = ImageOps.exif_transpose(scene).convert("RGB")
    texture = ImageOps.exif_transpose(texture).convert("RGB")
    mask = ImageOps.exif_transpose(mask).convert("L")
    if max_side and max(scene.size) > max_side:
        ratio = max_side / max(scene.size)
        size = (max(1, round(scene.width * ratio)), max(1, round(scene.height * ratio)))
        scene = scene.resize(size, Image.Resampling.LANCZOS)
    if mask.size != scene.size:
        mask = mask.resize(scene.size, Image.Resampling.NEAREST)
    return scene, texture, mask


def render_floor(scene: Image.Image, texture: Image.Image, mask: Image.Image,
                 calibration_quad: Sequence, recipe: RenderRecipe = RenderRecipe(),
                 max_side: int = 0) -> tuple[Image.Image, dict]:
    """Project a repeating product texture into ``scene`` and preserve scene lighting.

    The returned PNG-ready RGB image is byte-identical to the input outside the
    binary mask. Feathering is inward only.
    """
    if not (0.15 <= recipe.scale <= 4.0):
        raise ValueError("纹理比例必须在 0.15–4.0 之间")
    if not (-180 <= recipe.rotation <= 180):
        raise ValueError("旋转角度必须在 -180°–180° 之间")
    if not (0 <= recipe.illumination_strength <= 1.5 and 0 <= recipe.shadow_strength <= 1.5):
        raise ValueError("光照和阴影强度必须在 0–1.5 之间")
    if not (0 <= recipe.feather <= 0.08):
        raise ValueError("羽化必须在 0–0.08 之间")

    normalized_quad = validate_calibration_quad(calibration_quad)
    scene, texture, mask = _normalize_inputs(scene, texture, mask, max_side)
    base = np.asarray(scene, dtype=np.uint8)
    tex = np.asarray(texture, dtype=np.uint8)
    binary = (np.asarray(mask, dtype=np.uint8) >= 128).astype(np.uint8)
    if cv2.countNonZero(binary) == 0:
        raise ValueError("地板遮罩为空，请先涂抹地面区域")

    h, w = base.shape[:2]
    qpx = normalized_quad * np.array([w - 1, h - 1], dtype=np.float32)
    top = np.linalg.norm(qpx[1] - qpx[0])
    bottom = np.linalg.norm(qpx[2] - qpx[3])
    left = np.linalg.norm(qpx[3] - qpx[0])
    right = np.linalg.norm(qpx[2] - qpx[1])
    plane_w = max(2.0, (top + bottom) / 2.0)
    plane_h = max(2.0, (left + right) / 2.0)
    plane = np.float32([[0, 0], [plane_w, 0], [plane_w, plane_h], [0, plane_h]])
    homography = cv2.getPerspectiveTransform(plane, qpx.astype(np.float32))
    if not np.isfinite(homography).all() or abs(float(np.linalg.det(homography))) < 1e-10:
        raise ValueError("标定四边形无法建立稳定透视变换")
    inv = np.linalg.inv(homography)

    ys, xs = np.nonzero(binary)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    theta = math.radians(recipe.rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    cx, cy = plane_w / 2.0, plane_h / 2.0
    pixels_per_texel_x = max(1e-6, plane_w * recipe.scale / max(1, tex.shape[1]))
    pixels_per_texel_y = pixels_per_texel_x
    if recipe.texture_width_mm and recipe.texture_height_mm:
        tile_height = plane_w * recipe.scale * recipe.texture_height_mm / recipe.texture_width_mm
        pixels_per_texel_y = max(1e-6, tile_height / max(1, tex.shape[0]))
    mask_roi = binary[y0:y1, x0:x1]
    # Compute the low-frequency light field once, then map/composite in narrow
    # horizontal strips.  A full 4K float RGB working set can exceed 1.5 GB;
    # striping keeps peak memory suitable for ordinary customer PCs.
    gray = cv2.cvtColor(base[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    linear_lut = _srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0)
    luma = linear_lut[gray]
    del gray
    sigma = max(4.0, min(w, h) * 0.025)
    low_roi = cv2.GaussianBlur(luma, (0, 0), sigmaX=sigma, sigmaY=sigma)
    del luma
    valid = mask_roi > 0
    median = float(np.median(low_roi[valid])) if np.any(valid) else 1.0

    feather_px = recipe.feather * min(w, h)
    if feather_px > 0:
        distance = cv2.distanceTransform(mask_roi, cv2.DIST_L2, 5)
        alpha_roi = np.clip(distance / max(1.0, feather_px), 0, 1)
    else:
        alpha_roi = mask_roi.astype(np.float32)
    alpha_roi *= mask_roi
    result = base.copy()
    xs_grid = np.arange(x0, x1, dtype=np.float32)[None, :]
    strip_rows = 128
    for local_y in range(0, y1 - y0, strip_rows):
        rows = min(strip_rows, y1 - y0 - local_y)
        ys_grid = np.arange(y0 + local_y, y0 + local_y + rows, dtype=np.float32)[:, None]
        den = inv[2, 0] * xs_grid + inv[2, 1] * ys_grid + inv[2, 2]
        den[np.abs(den) < 1e-6] = np.nan
        px = (inv[0, 0] * xs_grid + inv[0, 1] * ys_grid + inv[0, 2]) / den
        py = (inv[1, 0] * xs_grid + inv[1, 1] * ys_grid + inv[1, 2]) / den
        dx, dy = px - cx, py - cy
        rx = cos_t * dx + sin_t * dy + cx
        ry = -sin_t * dx + cos_t * dy + cy
        map_x = np.mod(rx / pixels_per_texel_x + recipe.offset_x * tex.shape[1],
                       tex.shape[1]).astype(np.float32)
        map_y = np.mod(ry / pixels_per_texel_y + recipe.offset_y * tex.shape[0],
                       tex.shape[0]).astype(np.float32)
        mapped = cv2.remap(tex, map_x, map_y, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_WRAP)

        base_strip = base[y0 + local_y:y0 + local_y + rows, x0:x1]
        base_lin = _srgb_to_linear(base_strip.astype(np.float32) / 255.0)
        tex_lin = _srgb_to_linear(mapped.astype(np.float32) / 255.0)
        ratio = np.clip(low_roi[local_y:local_y + rows] / max(0.03, median), 0.45, 1.55)
        gain = np.where(
            ratio < 1,
            1 + (ratio - 1) * recipe.shadow_strength,
            1 + (ratio - 1) * recipe.illumination_strength,
        )
        rendered_lin = np.clip(tex_lin * gain[..., None], 0, 1)
        alpha = alpha_roi[local_y:local_y + rows]
        mixed_lin = base_lin * (1 - alpha[..., None]) + rendered_lin * alpha[..., None]
        mixed = np.rint(np.clip(_linear_to_srgb(mixed_lin), 0, 1) * 255.0).astype(np.uint8)
        selected = valid[local_y:local_y + rows]
        result_strip = result[y0 + local_y:y0 + local_y + rows, x0:x1]
        # Explicit assignment only inside the mask guarantees byte identity outside it.
        result_strip[selected] = mixed[selected]

    meta = {
        "provider": "local",
        "model": "deterministic-floor-render-v1",
        "scene_size": [w, h],
        "texture_size": [int(tex.shape[1]), int(tex.shape[0])],
        "texture_sha256": image_sha256(texture),
        "calibration_quad": normalized_quad.tolist(),
        "recipe": recipe.__dict__.copy(),
        "mask_coverage": round(float(binary.mean()), 6),
    }
    meta["warnings"] = texture_quality_warnings(texture, round(plane_w), recipe.scale)
    return Image.fromarray(result, "RGB"), meta
