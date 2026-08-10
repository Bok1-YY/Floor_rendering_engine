"""Standalone sample-to-sample colour matching engine.

This is a small, dependency-light extraction of the LAB/Reinhard colour
matching logic in ``../color_match.py``.  It intentionally has no dependency
on the Floor Engine server or web application.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageOps


Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class MatchReport:
    """Small diagnostic summary returned together with the corrected image."""

    source_mean_lab: tuple[float, float, float]
    reference_mean_lab: tuple[float, float, float]
    estimated_mean_delta_e: float
    selected_rect: Rect


def open_image(path: str | Path) -> Image.Image:
    """Open an image, apply its EXIF orientation, and detach it from the file."""

    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB").copy()


def _signed_lab_array(image: Image.Image) -> np.ndarray:
    lab = np.asarray(image.convert("LAB"), dtype=np.float32).copy()
    lab[..., 1] = np.where(lab[..., 1] > 127, lab[..., 1] - 256, lab[..., 1])
    lab[..., 2] = np.where(lab[..., 2] > 127, lab[..., 2] - 256, lab[..., 2])
    return lab


def _normalise_rect(rect: Iterable[float] | None) -> Rect:
    if rect is None:
        return 0.0, 0.0, 1.0, 1.0
    values = tuple(float(value) for value in rect)
    if len(values) != 4:
        raise ValueError("rect must contain x,y,width,height")
    x, y, width, height = values
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    width = min(max(width, 0.0), 1.0 - x)
    height = min(max(height, 0.0), 1.0 - y)
    if width < 0.01 or height < 0.01:
        raise ValueError("selected sample area is too small")
    return x, y, width, height


def _crop_from_rect(image: Image.Image, rect: Rect) -> Image.Image:
    x, y, width, height = rect
    left = int(round(x * image.width))
    top = int(round(y * image.height))
    right = int(round((x + width) * image.width))
    bottom = int(round((y + height) * image.height))
    right = max(left + 1, min(right, image.width))
    bottom = max(top + 1, min(bottom, image.height))
    return image.crop((left, top, right, bottom))


def _bounded_sample(image: Image.Image, max_size: int) -> Image.Image:
    sample = image.convert("RGB").copy()
    sample.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return sample


def _robust_profile(pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Estimate LAB mean/std while rejecting props, borders and glare outliers."""

    if len(pixels) < 64:
        raise ValueError("not enough pixels in the selected sample area")
    center = np.median(pixels, axis=0)
    scale = np.maximum(
        np.median(np.abs(pixels - center), axis=0) * 1.4826,
        (2.0, 1.5, 1.5),
    )
    distance = np.sqrt((((pixels - center) / scale) ** 2).mean(axis=1))
    inliers = pixels[distance <= 3.2]
    if len(inliers) < max(64, int(0.05 * len(pixels))):
        inliers = pixels
    mean = inliers.mean(axis=0)
    std = np.maximum(inliers.std(axis=0), (2.0, 1.5, 1.5))
    return mean.astype(np.float32), std.astype(np.float32)


def _profile_image(image: Image.Image, max_size: int) -> tuple[np.ndarray, np.ndarray]:
    sample = _bounded_sample(image, max_size)
    pixels = _signed_lab_array(sample).reshape(-1, 3)
    return _robust_profile(pixels)


def match_sample_color(
    source: Image.Image,
    reference: Image.Image,
    *,
    source_rect: Iterable[float] | None = None,
    strength: float = 0.85,
    preserve_luminance: bool = True,
    strip_rows: int = 256,
) -> tuple[Image.Image, MatchReport]:
    """Match a newly photographed large sample to an older small reference.

    ``source_rect`` is a normalised ``(x, y, width, height)`` region containing
    clean material in the new photograph.  Its statistics drive a global,
    pointwise transform, so output resolution and texture are not changed.

    With ``preserve_luminance=True`` only signed LAB a/b are transferred.  This
    is the safest default for material photography because it retains grain,
    embossing, highlights, shadows and exposure from the new photograph.
    """

    source_rgb = source.convert("RGB")
    reference_rgb = reference.convert("RGB")
    rect = _normalise_rect(source_rect)
    strength = float(min(max(strength, 0.0), 1.0))
    rows = max(1, int(strip_rows))

    source_sample = _crop_from_rect(source_rgb, rect)
    source_mean, source_std = _profile_image(source_sample, 1600)
    reference_mean, reference_std = _profile_image(reference_rgb, 1200)

    # As in Floor Engine, variance scaling is deliberately bounded.  We want
    # the older sample's colour, not its camera noise or surface texture.
    ratio = np.clip(reference_std / source_std, 0.85, 1.15).astype(np.float32)
    target_mean = reference_mean.copy()
    if preserve_luminance:
        ratio[0] = 1.0
        target_mean[0] = source_mean[0]
    else:
        ratio[0] = float(np.clip(reference_std[0] / source_std[0], 0.7, 1.3))

    output = Image.new("RGB", source_rgb.size)
    for top in range(0, source_rgb.height, rows):
        bottom = min(source_rgb.height, top + rows)
        original_strip = source_rgb.crop((0, top, source_rgb.width, bottom))
        lab = _signed_lab_array(original_strip)
        if preserve_luminance:
            lab[..., 1:] = (
                (lab[..., 1:] - source_mean[1:]) * ratio[1:] + target_mean[1:]
            )
        else:
            lab = (lab - source_mean) * ratio + target_mean
            lab[..., 0] = np.clip(lab[..., 0], 0, 255)
        lab[..., 1:] = np.clip(lab[..., 1:], -128, 127)
        encoded = lab.copy()
        encoded[..., 1:] = np.where(
            encoded[..., 1:] < 0, encoded[..., 1:] + 256, encoded[..., 1:]
        )
        corrected = Image.fromarray(
            np.rint(encoded).astype(np.uint8), mode="LAB"
        ).convert("RGB")
        if strength < 1.0:
            corrected = Image.blend(original_strip, corrected, strength)
        output.paste(corrected, (0, top))

    predicted_mean = source_mean + strength * (target_mean - source_mean)
    # Pillow stores L* on 0..255; convert its residual to the conventional
    # 0..100 scale before reporting the approximate CIE76 mean distance.
    residual = predicted_mean - target_mean
    residual[0] *= 100.0 / 255.0
    report = MatchReport(
        source_mean_lab=tuple(float(value) for value in source_mean),
        reference_mean_lab=tuple(float(value) for value in reference_mean),
        estimated_mean_delta_e=float(np.linalg.norm(residual)),
        selected_rect=rect,
    )
    return output, report


def save_image(
    image: Image.Image,
    path: str | Path,
    *,
    source_info: dict | None = None,
) -> None:
    """Save with high-quality, format-appropriate defaults."""

    destination = Path(path)
    suffix = destination.suffix.lower()
    options: dict[str, object] = {}
    source_info = source_info or {}
    if source_info.get("dpi"):
        options["dpi"] = source_info["dpi"]
    if source_info.get("icc_profile"):
        options["icc_profile"] = source_info["icc_profile"]
    if suffix in {".jpg", ".jpeg"}:
        options.update(quality=95, subsampling=0, optimize=True)
    elif suffix in {".tif", ".tiff"}:
        options["compression"] = "tiff_lzw"
    elif suffix == ".png":
        options["compress_level"] = 6
    elif suffix not in {".bmp", ".webp"}:
        raise ValueError("output format must be JPG, PNG, TIFF, BMP, or WEBP")
    image.save(destination, **options)
