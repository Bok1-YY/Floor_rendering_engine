"""Deterministic raster registration and weak structural evidence for whole-home v3.

Image models may propose walls and openings, but the source image remains the
authority.  This module creates the reversible pixel transform used to compare
those proposals with the source.  It deliberately contains no provider calls.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps


REGISTRATION_VERSION = "whole-home-source-registration-v1"
EVIDENCE_VERSION = "whole-home-raster-structure-evidence-v1"


class RasterRegistrationError(ValueError):
    """A source image cannot be registered without inventing geometry."""


def _sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _matrix_list(matrix: np.ndarray) -> list[list[float]]:
    return [[round(float(value), 12) for value in row] for row in matrix.tolist()]


def _as_homogeneous(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape == (2, 3):
        return np.vstack([matrix, [0.0, 0.0, 1.0]])
    if matrix.shape != (3, 3):
        raise RasterRegistrationError("registration matrix must be 3x3")
    return matrix


def _transform_points(matrix: np.ndarray, points: Iterable[Iterable[float]]) -> np.ndarray:
    rows = np.asarray(list(points), dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] != 2:
        raise RasterRegistrationError("points must contain x/y pairs")
    homogeneous = np.column_stack([rows, np.ones(len(rows), dtype=np.float64)])
    projected = homogeneous @ matrix.T
    denominator = projected[:, 2]
    if np.any(np.abs(denominator) < 1e-12):
        raise RasterRegistrationError("registration maps a point to infinity")
    return projected[:, :2] / denominator[:, None]


def _load_source_image(path: str, page_index: int) -> Image.Image:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        try:
            import fitz  # type: ignore
        except ImportError as ex:  # pragma: no cover - dependency is present in production
            raise RasterRegistrationError("PyMuPDF is required for PDF floor plans") from ex
        document = fitz.open(path)
        try:
            if page_index < 0 or page_index >= document.page_count:
                raise RasterRegistrationError(
                    f"PDF page_index {page_index} is outside 0..{document.page_count - 1}")
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            document.close()
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def _crop_matrix(width: int, height: int, crop_polygon: Optional[list[list[float]]]) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    if not crop_polygon:
        return np.eye(3, dtype=np.float64), (0, 0, width, height)
    points = np.asarray(crop_polygon, dtype=np.float64)
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
        raise RasterRegistrationError("crop_polygon must have at least three x/y points")
    min_x = max(0, int(math.floor(float(points[:, 0].min()))))
    min_y = max(0, int(math.floor(float(points[:, 1].min()))))
    max_x = min(width, int(math.ceil(float(points[:, 0].max()))))
    max_y = min(height, int(math.ceil(float(points[:, 1].max()))))
    if max_x - min_x < 32 or max_y - min_y < 32:
        raise RasterRegistrationError("crop region is too small")
    matrix = np.array([[1.0, 0.0, -min_x], [0.0, 1.0, -min_y], [0.0, 0.0, 1.0]])
    return matrix, (min_x, min_y, max_x, max_y)


def _rotation_matrix(width: int, height: int, degrees: float) -> tuple[np.ndarray, tuple[int, int]]:
    if abs(degrees) < 1e-9:
        return np.eye(3, dtype=np.float64), (width, height)
    center = (width / 2.0, height / 2.0)
    raw = cv2.getRotationMatrix2D(center, degrees, 1.0)
    cosine, sine = abs(raw[0, 0]), abs(raw[0, 1])
    out_width = max(1, int(math.ceil(height * sine + width * cosine)))
    out_height = max(1, int(math.ceil(height * cosine + width * sine)))
    raw[0, 2] += out_width / 2.0 - center[0]
    raw[1, 2] += out_height / 2.0 - center[1]
    return _as_homogeneous(raw), (out_width, out_height)


def _quad_orientation(points: np.ndarray) -> float:
    # Shoelace sign is sufficient to reject an accidentally mirrored point order.
    return float(np.sum(points[:, 0] * np.roll(points[:, 1], -1)
                        - points[:, 1] * np.roll(points[:, 0], -1)))


def _perspective_matrix(
        source_points: Optional[list[list[float]]], current_from_source: np.ndarray,
        current_size: tuple[int, int]) -> tuple[np.ndarray, tuple[int, int], list[list[float]]]:
    if not source_points:
        return np.eye(3, dtype=np.float64), current_size, []
    if len(source_points) != 4:
        raise RasterRegistrationError("perspective_points must contain four ordered corners")
    original = np.asarray(source_points, dtype=np.float64)
    current = _transform_points(current_from_source, original).astype(np.float32)
    if abs(_quad_orientation(current)) < 1.0:
        raise RasterRegistrationError("perspective quadrilateral is degenerate")
    top = float(np.linalg.norm(current[1] - current[0]))
    bottom = float(np.linalg.norm(current[2] - current[3]))
    left = float(np.linalg.norm(current[3] - current[0]))
    right = float(np.linalg.norm(current[2] - current[1]))
    out_width = max(32, int(round(max(top, bottom))))
    out_height = max(32, int(round(max(left, right))))
    target = np.asarray([
        [0.0, 0.0], [out_width - 1.0, 0.0],
        [out_width - 1.0, out_height - 1.0], [0.0, out_height - 1.0],
    ], dtype=np.float32)
    if _quad_orientation(current) * _quad_orientation(target) <= 0:
        raise RasterRegistrationError("perspective point order would mirror the source")
    matrix = cv2.getPerspectiveTransform(current, target)
    return matrix, (out_width, out_height), _matrix_list(current)


def prepare_raster_source(
        source_path: str, output_dir: str, *, page_index: int = 0,
        crop_polygon: Optional[list[list[float]]] = None,
        rotation_degrees: float = 0.0,
        perspective_points: Optional[list[list[float]]] = None) -> dict:
    """Create original/canonical artifacts and an invertible source pixel transform."""
    source_path = os.path.realpath(source_path)
    if not os.path.isfile(source_path):
        raise RasterRegistrationError("source floor plan does not exist")
    if not math.isfinite(rotation_degrees) or abs(rotation_degrees) > 180:
        raise RasterRegistrationError("rotation_degrees must be between -180 and 180")
    os.makedirs(output_dir, exist_ok=True)
    image = _load_source_image(source_path, page_index)
    original_width, original_height = image.size
    original_path = os.path.join(output_dir, "source_original.png")
    image.save(original_path, "PNG")

    array = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    crop_transform, crop_box = _crop_matrix(original_width, original_height, crop_polygon)
    left, top, right, bottom = crop_box
    array = array[top:bottom, left:right]

    rotation_transform, rotated_size = _rotation_matrix(array.shape[1], array.shape[0], rotation_degrees)
    if not np.allclose(rotation_transform, np.eye(3)):
        array = cv2.warpPerspective(
            array, rotation_transform, rotated_size,
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
    through_rotation = rotation_transform @ crop_transform
    perspective_transform, canonical_size, transformed_quad = _perspective_matrix(
        perspective_points, through_rotation, rotated_size)
    if not np.allclose(perspective_transform, np.eye(3)):
        array = cv2.warpPerspective(
            array, perspective_transform, canonical_size,
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
    source_to_canonical = perspective_transform @ through_rotation
    determinant = float(np.linalg.det(source_to_canonical))
    if not math.isfinite(determinant) or abs(determinant) < 1e-12:
        raise RasterRegistrationError("source_to_canonical is not invertible")
    canonical_to_source = np.linalg.inv(source_to_canonical)
    canonical_path = os.path.join(output_dir, "source_canonical.png")
    cv2.imwrite(canonical_path, array)

    probe = np.asarray([
        [0.0, 0.0], [original_width - 1.0, 0.0],
        [original_width - 1.0, original_height - 1.0],
        [0.0, original_height - 1.0],
        [original_width / 2.0, original_height / 2.0],
    ])
    roundtrip = _transform_points(
        canonical_to_source, _transform_points(source_to_canonical, probe))
    roundtrip_error = float(np.max(np.linalg.norm(roundtrip - probe, axis=1)))
    if roundtrip_error > 0.25:
        raise RasterRegistrationError(
            f"source registration roundtrip error {roundtrip_error:.4f}px exceeds 0.25px")

    registration = {
        "version": 1,
        "registration_algorithm_version": REGISTRATION_VERSION,
        "source_type": "raster",
        "source_hash": _sha256_file(source_path),
        "normalized_source_hash": _sha256_file(original_path),
        "canonical_hash": _sha256_file(canonical_path),
        "input_grade": "raster_draft",
        "source_space": "source_pixels",
        "original_width": original_width,
        "original_height": original_height,
        "canonical_width": int(array.shape[1]),
        "canonical_height": int(array.shape[0]),
        "page_index": page_index,
        "crop_polygon": crop_polygon or [],
        "rotation_degrees": round(float(rotation_degrees), 8),
        "perspective_points": perspective_points or [],
        "perspective_points_after_crop_rotation": transformed_quad,
        "source_to_canonical": _matrix_list(source_to_canonical),
        "canonical_to_source": _matrix_list(canonical_to_source),
        "scale_anchors": [],
        "roundtrip_error_px": round(roundtrip_error, 9),
        "measured_roundtrip_error": round(roundtrip_error, 9),
        "original_artifact_path": original_path,
        "canonical_artifact_path": canonical_path,
    }
    registration["registration_hash"] = _canonical_hash({
        key: value for key, value in registration.items()
        if key not in {"registration_hash", "original_artifact_path", "canonical_artifact_path"}
    })
    return registration


def lock_raster_scale(
        registration: dict, anchors: list[dict], *, reviewer: str,
        origin_px: Optional[list[float]] = None) -> dict:
    """Add a uniform pixel-to-metre transform; conflicting anchors fail closed."""
    if not anchors:
        raise RasterRegistrationError("at least one measured dimension is required")
    normalized: list[dict] = []
    scales: list[float] = []
    for index, anchor in enumerate(anchors):
        start = anchor.get("start_px") or []
        end = anchor.get("end_px") or []
        if len(start) != 2 or len(end) != 2:
            raise RasterRegistrationError(f"scale anchor {index} must contain two pixel points")
        distance_px = math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
        length_m = float(anchor.get("length_m") or 0)
        if distance_px < 2 or not math.isfinite(length_m) or length_m <= 0:
            raise RasterRegistrationError(f"scale anchor {index} is invalid")
        scale = length_m / distance_px
        scales.append(scale)
        normalized.append({
            "id": str(anchor.get("id") or f"scale-{index + 1}")[:100],
            "start_px": [round(float(start[0]), 6), round(float(start[1]), 6)],
            "end_px": [round(float(end[0]), 6), round(float(end[1]), 6)],
            "length_m": round(length_m, 8),
            "metres_per_pixel": round(scale, 12),
            # SourceRegistration v1 canonical field names.  The *_px aliases
            # remain for the raster editor and backward compatibility.
            "start": [round(float(start[0]), 6), round(float(start[1]), 6)],
            "end": [round(float(end[0]), 6), round(float(end[1]), 6)],
            "actual_length_m": round(length_m, 8),
            "meters_per_pixel": round(scale, 12),
        })
    median_scale = float(np.median(np.asarray(scales, dtype=np.float64)))
    max_disagreement = max(abs(scale - median_scale) / median_scale for scale in scales)
    if len(scales) > 1 and max_disagreement > 0.02 + 1e-12:
        raise RasterRegistrationError(
            f"dimension anchors disagree by {max_disagreement * 100:.3f}%, exceeding 2%")
    origin = origin_px or [0.0, 0.0]
    if len(origin) != 2:
        raise RasterRegistrationError("origin_px must be an x/y pair")
    # Canonical image y maps to model z without anisotropic scaling.
    canonical_to_model = np.asarray([
        [median_scale, 0.0, -float(origin[0]) * median_scale],
        [0.0, median_scale, -float(origin[1]) * median_scale],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    model_to_canonical = np.linalg.inv(canonical_to_model)
    result = dict(registration)
    result.update({
        "input_grade": "raster_human_locked",
        "scale_anchors": normalized,
        "scale_m_per_px": round(median_scale, 12),
        "scale_disagreement_ratio": round(max_disagreement, 12),
        "canonical_to_model": _matrix_list(canonical_to_model),
        "model_to_canonical": _matrix_list(model_to_canonical),
        "origin_px": [round(float(origin[0]), 6), round(float(origin[1]), 6)],
        "reviewer": reviewer.strip()[:100] or "local-user",
    })
    # Build through the same public contract used by the API route.  This
    # prevents the raster helper and server from producing incompatible hashes
    # or accepting different matrix/anchor rules.
    from .whole_home_geometry import SourceRegistration
    result.pop("registration_hash", None)
    return SourceRegistration(result).to_dict()


def build_structure_evidence(canonical_path: str, output_dir: str) -> dict:
    """Build deterministic weak evidence; it never promotes pixels directly to walls."""
    image = cv2.imread(canonical_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RasterRegistrationError("canonical image cannot be read")
    os.makedirs(output_dir, exist_ok=True)
    blur = cv2.GaussianBlur(image, (3, 3), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 9)
    # Global Otsu reliably retains the interior of wide, uniformly dark wall
    # bands; adaptive thresholding is better for thin/faded strokes.  AND used
    # to erase the centre of thick walls because a locally uniform dark patch
    # is not an adaptive edge.  OR keeps both kinds of legitimate weak ink
    # evidence (still without promoting either directly to model geometry).
    combined = cv2.bitwise_or(otsu, adaptive)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    structure = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, close_kernel)
    structure = cv2.morphologyEx(
        structure, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    distance = cv2.distanceTransform(structure, cv2.DIST_L2, 5)
    maximum = float(distance.max())
    distance_u16 = np.clip(distance / max(maximum, 1e-9) * 65535.0, 0, 65535).astype(np.uint16)
    mask_path = os.path.join(output_dir, "structure_mask.png")
    distance_path = os.path.join(output_dir, "structure_distance.png")
    cv2.imwrite(mask_path, structure)
    cv2.imwrite(distance_path, distance_u16)
    result = {
        "version": EVIDENCE_VERSION,
        "canonical_hash": _sha256_file(canonical_path),
        "mask_path": mask_path,
        "mask_hash": _sha256_file(mask_path),
        "distance_path": distance_path,
        "distance_hash": _sha256_file(distance_path),
        "ink_fraction": round(float(np.count_nonzero(structure)) / float(structure.size), 8),
        "max_half_band_px": round(maximum, 5),
    }
    result["evidence_hash"] = _canonical_hash({
        key: value for key, value in result.items()
        if key not in {"mask_path", "distance_path", "evidence_hash"}
    })
    return result


def wall_ink_support(mask_path: str, segments_px: list[dict], *, sample_step_px: float = 1.0) -> dict:
    """Measure bidirectional pixel support for candidate wall centre lines."""
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise RasterRegistrationError("structure mask cannot be read")
    inverse = (mask == 0).astype(np.uint8)
    distance_to_ink = cv2.distanceTransform(inverse, cv2.DIST_L2, 5)
    distances: list[float] = []
    supported = 0
    samples = 0
    for segment in segments_px:
        start, end = segment.get("start_px") or [], segment.get("end_px") or []
        if len(start) != 2 or len(end) != 2:
            continue
        length = math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
        count = max(2, int(math.ceil(length / max(sample_step_px, 0.25))) + 1)
        for amount in np.linspace(0.0, 1.0, count):
            x = int(round(float(start[0]) + (float(end[0]) - float(start[0])) * amount))
            y = int(round(float(start[1]) + (float(end[1]) - float(start[1])) * amount))
            if not (0 <= x < mask.shape[1] and 0 <= y < mask.shape[0]):
                distances.append(float(max(mask.shape)))
            else:
                distance = float(distance_to_ink[y, x])
                distances.append(distance)
                supported += distance <= 2.0
            samples += 1
    if not distances:
        return {"sample_count": 0, "support_ratio": 0.0, "distance_p95_px": None}
    return {
        "sample_count": samples,
        "support_ratio": round(supported / samples, 8),
        "distance_p95_px": round(float(np.percentile(distances, 95)), 5),
        "distance_max_px": round(max(distances), 5),
    }
