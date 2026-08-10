"""Shared advanced colour analysis and deterministic distribution transfer.

The module is deliberately UI-free.  It is used both by the standalone sample
matcher and by Floor Engine's masked colour correction path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np
from PIL import Image


Algorithm = Literal["classic", "distribution"]
IlluminationMode = Literal["off", "chroma", "full"]


class ColorAnalysisError(ValueError):
    """Raised when an image pair cannot provide enough usable colour data."""


@dataclass
class ReliabilityMasks:
    selected: np.ndarray
    valid: np.ndarray
    clipped: np.ndarray
    glare: np.ndarray
    shadow: np.ndarray
    outlier: np.ndarray


@dataclass
class QuantileStep:
    rotation: np.ndarray
    source_knots: tuple[np.ndarray, ...]
    target_knots: tuple[np.ndarray, ...]


@dataclass
class IlluminationField:
    coefficients: np.ndarray
    channels: tuple[int, ...]
    limits: np.ndarray


@dataclass
class ColorQualityReport:
    score: int
    level: Literal["high", "medium", "low"]
    summary: str
    source_usable_ratio: float
    reference_usable_ratio: float
    clipped_ratio: float
    glare_ratio: float
    shadow_ratio: float
    outlier_ratio: float
    spatial_chroma_span: float
    spatial_luminance_span: float
    initial_delta_e00: float
    estimated_delta_e00: float
    predicted_gamut_clip_ratio: float
    algorithm: Algorithm
    requested_illumination_mode: IlluminationMode
    applied_illumination_mode: IlluminationMode
    fallback_reason: str = ""
    warnings: tuple[str, ...] = ()
    diagnostic_overlay: Image.Image | None = field(default=None, repr=False)

    def to_dict(self, *, include_overlay: bool = False) -> dict:
        result = {
            "score": self.score,
            "level": self.level,
            "summary": self.summary,
            "source_usable_ratio": round(self.source_usable_ratio, 4),
            "reference_usable_ratio": round(self.reference_usable_ratio, 4),
            "clipped_ratio": round(self.clipped_ratio, 4),
            "glare_ratio": round(self.glare_ratio, 4),
            "shadow_ratio": round(self.shadow_ratio, 4),
            "outlier_ratio": round(self.outlier_ratio, 4),
            "spatial_chroma_span": round(self.spatial_chroma_span, 2),
            "spatial_luminance_span": round(self.spatial_luminance_span, 2),
            "initial_delta_e00": round(self.initial_delta_e00, 2),
            "estimated_delta_e00": round(self.estimated_delta_e00, 2),
            "predicted_gamut_clip_ratio": round(self.predicted_gamut_clip_ratio, 4),
            "algorithm": self.algorithm,
            "requested_illumination_mode": self.requested_illumination_mode,
            "applied_illumination_mode": self.applied_illumination_mode,
            "fallback_reason": self.fallback_reason,
            "warnings": list(self.warnings),
        }
        if include_overlay:
            result["diagnostic_overlay"] = self.diagnostic_overlay
        return result


@dataclass
class ColorTransformPlan:
    algorithm: Algorithm
    preserve_luminance: bool
    source_mean: np.ndarray
    reference_mean: np.ndarray
    ratio: np.ndarray
    matrix: np.ndarray | None
    channel_indices: tuple[int, ...]
    quantile_steps: tuple[QuantileStep, ...]
    illumination: IlluminationField | None
    report: ColorQualityReport


def signed_lab_array(image: Image.Image) -> np.ndarray:
    lab = np.asarray(image.convert("LAB"), dtype=np.float32).copy()
    lab[..., 1] = np.where(lab[..., 1] > 127, lab[..., 1] - 256, lab[..., 1])
    lab[..., 2] = np.where(lab[..., 2] > 127, lab[..., 2] - 256, lab[..., 2])
    return lab


def lab_array_to_image(lab: np.ndarray) -> Image.Image:
    encoded = lab.copy()
    encoded[..., 0] = np.clip(encoded[..., 0], 0, 255)
    encoded[..., 1:] = np.clip(encoded[..., 1:], -128, 127)
    encoded[..., 1:] = np.where(encoded[..., 1:] < 0, encoded[..., 1:] + 256, encoded[..., 1:])
    return Image.fromarray(np.rint(encoded).astype(np.uint8), mode="LAB").convert("RGB")


def _lab100(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64).copy()
    result[..., 0] *= 100.0 / 255.0
    return result


def delta_e_ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Vectorized CIEDE2000 using conventional L*=0..100, signed a*/b*."""

    x = np.asarray(lab1, dtype=np.float64)
    y = np.asarray(lab2, dtype=np.float64)
    l1, a1, b1 = np.moveaxis(x, -1, 0)
    l2, a2, b2 = np.moveaxis(y, -1, 0)
    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    cbar = (c1 + c2) / 2.0
    cbar7 = cbar ** 7
    g = 0.5 * (1.0 - np.sqrt(cbar7 / (cbar7 + 25.0 ** 7 + 1e-30)))
    ap1, ap2 = (1.0 + g) * a1, (1.0 + g) * a2
    cp1, cp2 = np.hypot(ap1, b1), np.hypot(ap2, b2)
    hp1 = np.mod(np.degrees(np.arctan2(b1, ap1)), 360.0)
    hp2 = np.mod(np.degrees(np.arctan2(b2, ap2)), 360.0)
    hp1 = np.where((cp1 == 0), 0.0, hp1)
    hp2 = np.where((cp2 == 0), 0.0, hp2)
    dl = l2 - l1
    dc = cp2 - cp1
    dh_raw = hp2 - hp1
    dh = np.where(cp1 * cp2 == 0, 0.0, dh_raw)
    dh = np.where(dh > 180.0, dh - 360.0, dh)
    dh = np.where(dh < -180.0, dh + 360.0, dh)
    d_big_h = 2.0 * np.sqrt(cp1 * cp2) * np.sin(np.radians(dh / 2.0))
    lbar = (l1 + l2) / 2.0
    cpbar = (cp1 + cp2) / 2.0
    hp_sum = hp1 + hp2
    hp_diff = np.abs(hp1 - hp2)
    hpbar = np.where(cp1 * cp2 == 0, hp_sum, hp_sum / 2.0)
    hpbar = np.where((cp1 * cp2 != 0) & (hp_diff > 180.0) & (hp_sum < 360.0),
                     (hp_sum + 360.0) / 2.0, hpbar)
    hpbar = np.where((cp1 * cp2 != 0) & (hp_diff > 180.0) & (hp_sum >= 360.0),
                     (hp_sum - 360.0) / 2.0, hpbar)
    t = (1.0 - 0.17 * np.cos(np.radians(hpbar - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hpbar))
         + 0.32 * np.cos(np.radians(3.0 * hpbar + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hpbar - 63.0)))
    sl = 1.0 + 0.015 * (lbar - 50.0) ** 2 / np.sqrt(20.0 + (lbar - 50.0) ** 2)
    sc = 1.0 + 0.045 * cpbar
    sh = 1.0 + 0.015 * cpbar * t
    delta_theta = 30.0 * np.exp(-((hpbar - 275.0) / 25.0) ** 2)
    rc = 2.0 * np.sqrt(cpbar ** 7 / (cpbar ** 7 + 25.0 ** 7 + 1e-30))
    rt = -rc * np.sin(np.radians(2.0 * delta_theta))
    return np.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (d_big_h / sh) ** 2
                   + rt * (dc / sc) * (d_big_h / sh))


def _bounded_image_and_mask(image: Image.Image, mask: Image.Image | None,
                            max_side: int = 1600) -> tuple[Image.Image, np.ndarray]:
    sample = image.convert("RGB").copy()
    original_size = sample.size
    sample.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if mask is None:
        selected = np.ones((sample.height, sample.width), dtype=bool)
    else:
        resized = mask.convert("L")
        if resized.size != original_size:
            resized = resized.resize(original_size, Image.Resampling.NEAREST)
        if resized.size != sample.size:
            resized = resized.resize(sample.size, Image.Resampling.NEAREST)
        selected = np.asarray(resized, dtype=np.uint8) >= 128
    return sample, selected


def _mask_aware_blur(values: np.ndarray, selected: np.ndarray, sigma: float) -> np.ndarray:
    weight = selected.astype(np.float32)
    numerator = cv2.GaussianBlur(values.astype(np.float32) * weight, (0, 0), sigma)
    denominator = cv2.GaussianBlur(weight, (0, 0), sigma)
    return numerator / np.maximum(denominator, 1e-4)


def build_reliability_masks(image: Image.Image, mask: Image.Image | None = None,
                            max_side: int = 1600) -> tuple[Image.Image, np.ndarray, ReliabilityMasks]:
    sample, selected = _bounded_image_and_mask(image, mask, max_side)
    if int(selected.sum()) < 256:
        raise ColorAnalysisError("有效选区像素不足，请框选更大的纯材料区域")
    rgb = np.asarray(sample, dtype=np.float32)
    lab = signed_lab_array(sample)
    l100 = lab[..., 0] * (100.0 / 255.0)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    short = min(sample.size)
    sigma = float(np.clip(short * 0.04, 8.0, 64.0))
    local_l = _mask_aware_blur(l100, selected, sigma)
    local_c = _mask_aware_blur(chroma, selected, sigma)
    residual = l100 - local_l
    selected_residual = residual[selected]
    residual_mad = max(1.0, float(np.median(np.abs(
        selected_residual - np.median(selected_residual))) * 1.4826))
    l_values = l100[selected]
    c_values = chroma[selected]
    p20, p85 = np.quantile(l_values, (0.20, 0.85))
    c45 = float(np.quantile(c_values, 0.45))
    clipped = selected & (((rgb.max(axis=2) >= 252) & (l100 >= 96.0))
                          | ((rgb.min(axis=2) <= 3) & (l100 <= 4.0)))
    glare = selected & (residual > max(4.0, 2.5 * residual_mad)) & (l100 >= p85) \
        & ((chroma <= c45) | ((chroma - local_c) < -2.0))
    shadow = selected & (residual < -max(5.0, 2.5 * residual_mad)) & (l100 <= p20)
    ab = lab[..., 1:]
    center = np.median(ab[selected], axis=0)
    scale = np.maximum(np.median(np.abs(ab[selected] - center), axis=0) * 1.4826, (1.5, 1.5))
    distance = np.sqrt((((ab - center) / scale) ** 2).mean(axis=2))
    outlier = selected & (distance > 3.2)
    kernel = np.ones((3, 3), np.uint8)

    def clean(values: np.ndarray) -> np.ndarray:
        data = cv2.morphologyEx(values.astype(np.uint8), cv2.MORPH_OPEN, kernel)
        return cv2.dilate(data, kernel, iterations=1).astype(bool) & selected

    clipped, glare, shadow, outlier = map(clean, (clipped, glare, shadow, outlier))
    # Categories are exclusive for honest ratios and a stable diagnostic map.
    glare &= ~clipped
    shadow &= ~(clipped | glare)
    outlier &= ~(clipped | glare | shadow)
    valid = selected & ~(clipped | glare | shadow | outlier)
    minimum = max(256, int(selected.sum() * 0.02))
    if int(valid.sum()) < minimum:
        raise ColorAnalysisError("反光、阴影或异色区域过多，请重新框选纯材料区域")
    return sample, lab, ReliabilityMasks(selected, valid, clipped, glare, shadow, outlier)


def diagnostic_overlay(masks: ReliabilityMasks) -> Image.Image:
    rgba = np.zeros((*masks.selected.shape, 4), dtype=np.uint8)
    rgba[masks.valid] = (34, 197, 94, 78)
    rgba[masks.clipped | masks.glare] = (239, 68, 68, 150)
    rgba[masks.shadow] = (59, 130, 246, 145)
    rgba[masks.outlier] = (234, 179, 8, 145)
    return Image.fromarray(rgba, mode="RGBA")


def _sample_pixels(values: np.ndarray, limit: int = 120_000) -> np.ndarray:
    if len(values) <= limit:
        return values.astype(np.float32, copy=True)
    indices = np.linspace(0, len(values) - 1, limit, dtype=np.int64)
    return values[indices].astype(np.float32, copy=True)


def _profile(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = values.mean(axis=0)
    std = np.maximum(values.std(axis=0), (2.0, 1.5, 1.5))
    return mean.astype(np.float32), std.astype(np.float32)


def _matrix_sqrt(matrix: np.ndarray, inverse: bool = False) -> np.ndarray:
    values, vectors = np.linalg.eigh(matrix.astype(np.float64))
    values = np.maximum(values, 1e-4)
    powers = 1.0 / np.sqrt(values) if inverse else np.sqrt(values)
    return (vectors * powers) @ vectors.T


def _bounded_covariance_map(source: np.ndarray, reference: np.ndarray) -> np.ndarray:
    source_cov = np.cov(source, rowvar=False) + np.eye(source.shape[1]) * 1e-3
    reference_cov = np.cov(reference, rowvar=False) + np.eye(reference.shape[1]) * 1e-3
    matrix = _matrix_sqrt(reference_cov) @ _matrix_sqrt(source_cov, inverse=True)
    u, singular, vt = np.linalg.svd(matrix)
    singular = np.clip(singular, 0.85, 1.15)
    return ((u * singular) @ vt).astype(np.float32)


def _unique_knots(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    quantiles = np.linspace(0.0, 1.0, 129)
    xp = np.quantile(source, quantiles).astype(np.float32)
    fp = np.quantile(target, quantiles).astype(np.float32)
    unique, first = np.unique(xp, return_index=True)
    return unique, fp[first]


def _interp_with_tails(values: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    if len(xp) < 2:
        return values.copy()
    mapped = np.interp(values, xp, fp).astype(np.float32)
    lo_den = max(float(xp[1] - xp[0]), 1e-5)
    hi_den = max(float(xp[-1] - xp[-2]), 1e-5)
    lo_slope = float(np.clip((fp[1] - fp[0]) / lo_den, 0.5, 1.5))
    hi_slope = float(np.clip((fp[-1] - fp[-2]) / hi_den, 0.5, 1.5))
    low = values < xp[0]
    high = values > xp[-1]
    mapped[low] = fp[0] + (values[low] - xp[0]) * lo_slope
    mapped[high] = fp[-1] + (values[high] - xp[-1]) * hi_slope
    return mapped


def _fixed_rotations(dimensions: int, count: int = 6) -> tuple[np.ndarray, ...]:
    if dimensions == 2:
        angles = np.radians((0.0, 31.0, 67.0, 103.0, 139.0, 173.0))
        return tuple(np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]],
                              dtype=np.float32) for a in angles[:count])
    rng = np.random.default_rng(20260810)
    rotations = []
    for _ in range(count):
        q, _ = np.linalg.qr(rng.normal(size=(dimensions, dimensions)))
        if np.linalg.det(q) < 0:
            q[:, 0] *= -1
        rotations.append(q.astype(np.float32))
    return tuple(rotations)


def _build_quantile_steps(source: np.ndarray, reference: np.ndarray) \
        -> tuple[np.ndarray, tuple[QuantileStep, ...]]:
    current = source.copy()
    steps = []
    for rotation in _fixed_rotations(source.shape[1]):
        rotated = current @ rotation.T
        target_rotated = reference @ rotation.T
        source_knots, target_knots = [], []
        for channel in range(source.shape[1]):
            xp, fp = _unique_knots(rotated[:, channel], target_rotated[:, channel])
            source_knots.append(xp)
            target_knots.append(fp)
            mapped = _interp_with_tails(rotated[:, channel], xp, fp)
            rotated[:, channel] += 0.6 * (mapped - rotated[:, channel])
        current = rotated @ rotation
        steps.append(QuantileStep(rotation, tuple(source_knots), tuple(target_knots)))
    return current, tuple(steps)


def _polynomial_basis(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.column_stack((np.ones_like(x), x, y, x * x, x * y, y * y))


def _fit_huber(basis: np.ndarray, values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    current_weights = weights.astype(np.float64)
    coefficients = np.zeros(basis.shape[1], dtype=np.float64)
    for _ in range(6):
        root = np.sqrt(np.maximum(current_weights, 1e-6))
        coefficients = np.linalg.lstsq(basis * root[:, None], values * root, rcond=None)[0]
        residual = values - basis @ coefficients
        scale = max(float(np.median(np.abs(residual)) * 1.4826), 0.5)
        huber = np.minimum(1.0, 1.5 * scale / np.maximum(np.abs(residual), 1e-6))
        current_weights = weights * huber
    return coefficients.astype(np.float32)


def _fit_illumination(lab: np.ndarray, valid: np.ndarray, mode: IlluminationMode) \
        -> tuple[IlluminationField | None, float, float, str]:
    if mode == "off":
        return None, 0.0, 0.0, ""
    height, width = valid.shape
    grid_x = min(12, max(4, width // 32))
    grid_y = min(12, max(4, height // 32))
    rows = []
    for gy in range(grid_y):
        y0, y1 = gy * height // grid_y, (gy + 1) * height // grid_y
        for gx in range(grid_x):
            x0, x1 = gx * width // grid_x, (gx + 1) * width // grid_x
            tile = valid[y0:y1, x0:x1]
            count = int(tile.sum())
            if count < 128 or count < tile.size * 0.20:
                continue
            pixels = lab[y0:y1, x0:x1][tile]
            rows.append(((x0 + x1) / (2.0 * width) * 2.0 - 1.0,
                         (y0 + y1) / (2.0 * height) * 2.0 - 1.0,
                         count, np.median(pixels, axis=0)))
    if len(rows) < 16:
        return None, 0.0, 0.0, "有效区域空间覆盖不足，已跳过光照均衡"
    xs = np.array([row[0] for row in rows])
    ys = np.array([row[1] for row in rows])
    if np.ptp(xs) < 0.9 or np.ptp(ys) < 0.9:
        return None, 0.0, 0.0, "有效区域横纵跨度不足，已跳过光照均衡"
    weights = np.array([row[2] for row in rows], dtype=np.float64)
    medians = np.stack([row[3] for row in rows])
    basis = _polynomial_basis(xs, ys)
    channels = (1, 2) if mode == "chroma" else (0, 1, 2)
    coefficients = np.zeros((3, 6), dtype=np.float32)
    limits = np.zeros((3, 2), dtype=np.float32)
    for channel in channels:
        coeff = _fit_huber(basis, medians[:, channel], weights)
        predicted = basis @ coeff
        center = float(np.average(predicted, weights=weights))
        coeff[0] -= center
        field_values = basis @ coeff
        absolute_cap = 12.0 * 255.0 / 100.0 if channel == 0 else 8.0
        lo, hi = np.quantile(field_values, (0.05, 0.95))
        limits[channel] = (max(float(lo), -absolute_cap), min(float(hi), absolute_cap))
        coefficients[channel] = coeff
    chroma_span = float(np.linalg.norm(
        np.quantile(medians[:, 1:], 0.9, axis=0) - np.quantile(medians[:, 1:], 0.1, axis=0)))
    luminance_span = float((np.quantile(medians[:, 0], 0.9)
                            - np.quantile(medians[:, 0], 0.1)) * 100.0 / 255.0)
    return IlluminationField(coefficients, channels, limits), chroma_span, luminance_span, ""


def _field_values(field: IlluminationField, height: int, width: int,
                  y_offset: int = 0, full_height: int | None = None) -> np.ndarray:
    total_height = full_height or height
    x = (np.arange(width, dtype=np.float32) + 0.5) / width * 2.0 - 1.0
    y = (np.arange(y_offset, y_offset + height, dtype=np.float32) + 0.5) / total_height * 2.0 - 1.0
    xx, yy = np.meshgrid(x, y)
    basis = np.stack((np.ones_like(xx), xx, yy, xx * xx, xx * yy, yy * yy), axis=-1)
    values = np.einsum("hwk,ck->hwc", basis, field.coefficients, optimize=True)
    for channel in field.channels:
        values[..., channel] = np.clip(values[..., channel], *field.limits[channel])
    return values.astype(np.float32)


def apply_plan_to_lab(lab: np.ndarray, plan: ColorTransformPlan, *, y_offset: int = 0,
                      full_height: int | None = None) -> np.ndarray:
    output = lab.astype(np.float32, copy=True)
    if plan.illumination is not None:
        field_values = _field_values(plan.illumination, output.shape[0], output.shape[1],
                                     y_offset, full_height)
        for channel in plan.illumination.channels:
            output[..., channel] -= field_values[..., channel]
    if plan.algorithm == "classic" or plan.matrix is None:
        if plan.preserve_luminance:
            output[..., 1:] = ((output[..., 1:] - plan.source_mean[1:])
                               * plan.ratio[1:] + plan.reference_mean[1:])
        else:
            output = ((output - plan.source_mean) * plan.ratio + plan.reference_mean)
    else:
        indices = list(plan.channel_indices)
        values = output[..., indices].reshape(-1, len(indices))
        values = ((values - plan.source_mean[indices]) @ plan.matrix.T
                  + plan.reference_mean[indices])
        for step in plan.quantile_steps:
            rotated = values @ step.rotation.T
            for channel in range(len(indices)):
                mapped = _interp_with_tails(rotated[:, channel],
                                            step.source_knots[channel],
                                            step.target_knots[channel])
                rotated[:, channel] += 0.6 * (mapped - rotated[:, channel])
            values = rotated @ step.rotation
        output[..., indices] = values.reshape(output.shape[0], output.shape[1], len(indices))
    output[..., 0] = np.clip(output[..., 0], 0, 255)
    output[..., 1:] = np.clip(output[..., 1:], -128, 127)
    return output


def _quality_score(masks: ReliabilityMasks, reference_masks: ReliabilityMasks,
                   chroma_span: float, transform_delta: float, gamut_ratio: float) -> int:
    selected = max(1, int(masks.selected.sum()))
    usable = float(masks.valid.sum()) / selected
    ref_usable = float(reference_masks.valid.sum()) / max(1, int(reference_masks.selected.sum()))
    clipped = float(masks.clipped.sum()) / selected
    excluded = float((masks.glare | masks.shadow).sum()) / selected
    score = 100.0
    score -= min(25.0, max(0.0, 0.70 - usable) / 0.70 * 25.0)
    score -= min(10.0, max(0.0, 0.70 - ref_usable) / 0.70 * 10.0)
    score -= min(20.0, clipped / 0.15 * 20.0)
    score -= min(20.0, excluded / 0.30 * 20.0)
    score -= min(15.0, max(0.0, chroma_span - 3.0) / 9.0 * 15.0)
    score -= min(10.0, gamut_ratio / 0.05 * 10.0)
    score -= min(10.0, max(0.0, transform_delta - 12.0) / 18.0 * 10.0)
    return int(round(np.clip(score, 0, 100)))


def build_color_transform_plan(source: Image.Image, reference: Image.Image, *,
                               sample_mask: Image.Image | None = None,
                               algorithm: Algorithm = "classic",
                               illumination_mode: IlluminationMode = "off",
                               preserve_luminance: bool = True) -> ColorTransformPlan:
    if algorithm not in ("classic", "distribution"):
        raise ValueError("algorithm must be classic or distribution")
    if illumination_mode not in ("off", "chroma", "full"):
        raise ValueError("illumination_mode must be off, chroma, or full")
    source_sample, source_lab, masks = build_reliability_masks(source, sample_mask, 1600)
    _, reference_lab, ref_masks = build_reliability_masks(reference, None, 1200)
    source_pixels = _sample_pixels(source_lab[masks.valid])
    reference_pixels = _sample_pixels(reference_lab[ref_masks.valid])
    source_mean, source_std = _profile(source_pixels)
    reference_mean, reference_std = _profile(reference_pixels)
    target_mean = reference_mean.copy()
    ratio = np.clip(reference_std / source_std, 0.85, 1.15).astype(np.float32)
    if preserve_luminance:
        ratio[0] = 1.0
        target_mean[0] = source_mean[0]
    else:
        ratio[0] = float(np.clip(reference_std[0] / source_std[0], 0.7, 1.3))

    illumination = None
    chroma_span = luminance_span = 0.0
    illumination_warning = ""
    if algorithm == "distribution" and illumination_mode != "off":
        illumination, chroma_span, luminance_span, illumination_warning = _fit_illumination(
            source_lab, masks.valid, illumination_mode)
        if illumination is not None:
            field_values = _field_values(illumination, source_lab.shape[0], source_lab.shape[1])
            flattened = source_lab.copy()
            for channel in illumination.channels:
                flattened[..., channel] -= field_values[..., channel]
            source_pixels = _sample_pixels(flattened[masks.valid])
            source_mean, source_std = _profile(source_pixels)
            if preserve_luminance:
                source_mean[0] = target_mean[0]
    elif illumination_mode != "off":
        illumination_warning = "光照均衡仅在精细对色模式中启用"

    matrix = None
    steps: tuple[QuantileStep, ...] = ()
    fallback_reason = ""
    transformed_sample = source_pixels.copy()
    channel_indices = (1, 2) if preserve_luminance else (0, 1, 2)
    if algorithm == "distribution":
        unique_count = len(np.unique(np.rint(source_pixels[:, list(channel_indices)]), axis=0))
        reference_unique = len(np.unique(np.rint(reference_pixels[:, list(channel_indices)]), axis=0))
        if min(unique_count, reference_unique) < 16:
            fallback_reason = "颜色层级过少，精细分布已回退为受限统计映射"
            algorithm = "classic"
        else:
            src_channels = source_pixels[:, list(channel_indices)]
            ref_channels = reference_pixels[:, list(channel_indices)]
            matrix = _bounded_covariance_map(src_channels, ref_channels)
            warm = ((src_channels - source_mean[list(channel_indices)]) @ matrix.T
                    + target_mean[list(channel_indices)])
            transformed_channels, steps = _build_quantile_steps(warm, ref_channels)
            transformed_sample[:, list(channel_indices)] = transformed_channels

    if algorithm == "classic":
        if preserve_luminance:
            transformed_sample[:, 1:] = ((transformed_sample[:, 1:] - source_mean[1:])
                                         * ratio[1:] + target_mean[1:])
        else:
            transformed_sample = ((transformed_sample - source_mean) * ratio + target_mean)

    unclipped = transformed_sample.copy()
    gamut = ((unclipped[:, 0] < 0) | (unclipped[:, 0] > 255)
             | (np.abs(unclipped[:, 1]) > 128) | (np.abs(unclipped[:, 2]) > 128))
    gamut_ratio = float(gamut.mean())
    source_median = np.median(source_pixels, axis=0)
    reference_median = np.median(reference_pixels, axis=0)
    result_median = np.median(transformed_sample, axis=0)
    if preserve_luminance:
        reference_median = reference_median.copy()
        reference_median[0] = source_median[0]
    initial_delta = float(delta_e_ciede2000(_lab100(source_median), _lab100(reference_median)))
    estimated_delta = float(delta_e_ciede2000(_lab100(result_median), _lab100(reference_median)))
    if chroma_span == 0.0:
        _, chroma_span, luminance_span, _ = _fit_illumination(source_lab, masks.valid, "chroma")
    score = _quality_score(masks, ref_masks, chroma_span, initial_delta, gamut_ratio)
    level: Literal["high", "medium", "low"] = "high" if score >= 80 else "medium" if score >= 55 else "low"
    selected_count = max(1, int(masks.selected.sum()))
    ref_selected = max(1, int(ref_masks.selected.sum()))
    warnings = []
    if illumination_warning:
        warnings.append(illumination_warning)
    if fallback_reason:
        warnings.append(fallback_reason)
    if score < 55:
        warnings.append("输入质量较低，建议避开反光和深阴影重新框选")
    if chroma_span > 8.0:
        warnings.append("检测到明显空间色偏，可能存在混合光源")
    if gamut_ratio > 0.02:
        warnings.append("部分颜色接近可表示色域边界，输出会进行安全裁切")
    applied_illumination: IlluminationMode = illumination_mode if illumination is not None else "off"
    summary = ({"high": "取样质量良好，可进行精细对色",
                "medium": "取样可用，建议检查诊断遮罩",
                "low": "取样风险较高，建议重新框选"})[level]
    report = ColorQualityReport(
        score=score,
        level=level,
        summary=summary,
        source_usable_ratio=float(masks.valid.sum()) / selected_count,
        reference_usable_ratio=float(ref_masks.valid.sum()) / ref_selected,
        clipped_ratio=float(masks.clipped.sum()) / selected_count,
        glare_ratio=float(masks.glare.sum()) / selected_count,
        shadow_ratio=float(masks.shadow.sum()) / selected_count,
        outlier_ratio=float(masks.outlier.sum()) / selected_count,
        spatial_chroma_span=chroma_span,
        spatial_luminance_span=luminance_span,
        initial_delta_e00=initial_delta,
        estimated_delta_e00=estimated_delta,
        predicted_gamut_clip_ratio=gamut_ratio,
        algorithm=algorithm,
        requested_illumination_mode=illumination_mode,
        applied_illumination_mode=applied_illumination,
        fallback_reason=fallback_reason,
        warnings=tuple(warnings),
        diagnostic_overlay=diagnostic_overlay(masks),
    )
    return ColorTransformPlan(
        algorithm=algorithm,
        preserve_luminance=preserve_luminance,
        source_mean=source_mean,
        reference_mean=target_mean,
        ratio=ratio,
        matrix=matrix,
        channel_indices=channel_indices,
        quantile_steps=steps,
        illumination=illumination,
        report=report,
    )


def apply_color_transform_plan(image: Image.Image, plan: ColorTransformPlan, *,
                               strength: float = 1.0, strip_rows: int = 256) -> Image.Image:
    source = image.convert("RGB")
    strength = float(np.clip(strength, 0.0, 1.0))
    output = Image.new("RGB", source.size)
    for top in range(0, source.height, max(1, int(strip_rows))):
        bottom = min(source.height, top + max(1, int(strip_rows)))
        original = source.crop((0, top, source.width, bottom))
        lab = signed_lab_array(original)
        corrected_lab = apply_plan_to_lab(lab, plan, y_offset=top, full_height=source.height)
        corrected = lab_array_to_image(corrected_lab)
        if strength < 1.0:
            corrected = Image.blend(original, corrected, strength)
        output.paste(corrected, (0, top))
    return output


__all__ = [
    "Algorithm", "IlluminationMode", "ColorAnalysisError", "ColorQualityReport",
    "ColorTransformPlan", "build_color_transform_plan", "apply_color_transform_plan",
    "apply_plan_to_lab", "signed_lab_array", "lab_array_to_image", "delta_e_ciede2000",
]
