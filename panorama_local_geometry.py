# -*- coding: utf-8 -*-
"""Local camera, floor-mask and mild panorama-geometry contracts.

This module deliberately separates *measurement* from generation.  It uses
OpenCV, the bundled MobileSAM results and optional local relative depth to
describe how an image is projected.  It never asks an image model to invent
floor texture or geometry.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import cv2
import numpy as np
from PIL import Image, ImageOps

from .floor_renderer import image_sha256
from .local_depth import MODEL_VERSION as DEPTH_MODEL_VERSION
from .local_depth import depth_model_status, predict_relative_depth
from .whole_home_pano_gate import erp_to_perspective


LOCAL_GEOMETRY_VERSION = "local-panorama-geometry-v2"
LOCAL_MASK_VERSION = "local-floor-mask-semantic-depth-v3"
LOCAL_ARCHITECTURE_VERSION = "local-architecture-rectifier-v1"


def _stable_hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    order = np.argsort(values)
    ordered = values[order]
    cumulative = np.cumsum(np.maximum(weights[order], 1e-6))
    return float(ordered[np.searchsorted(cumulative, cumulative[-1] * .5)])


def parse_horizontal_fov(params: dict, *, default: float = 84.0) -> tuple[float, str]:
    """Parse a full-frame-equivalent lens label into a bounded horizontal FOV."""
    text = str(params.get("angle") or "")
    match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*mm", text, re.I)
    if not match:
        return float(default), "default_natural_view"
    focal = float(match.group(1))
    if not 8.0 <= focal <= 300.0:
        return float(default), "default_invalid_lens"
    fov = math.degrees(2.0 * math.atan(36.0 / (2.0 * focal)))
    return float(np.clip(fov, 35.0, 120.0)), "full_frame_lens_label"


def _line_observations(image: Image.Image, *, max_side: int = 1200) -> tuple[list[dict], tuple[int, int]]:
    source = ImageOps.exif_transpose(image).convert("RGB")
    scale = min(1.0, max_side / max(source.size))
    size = (max(2, round(source.width * scale)), max(2, round(source.height * scale)))
    rgb = np.asarray(source.resize(size, Image.Resampling.LANCZOS), dtype=np.uint8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    edges = cv2.Canny(gray, 45, 135)
    minimum = max(36, round(min(size) * .075))
    raw = cv2.HoughLinesP(
        edges, 1, np.pi / 360.0, threshold=max(30, minimum // 2),
        minLineLength=minimum, maxLineGap=max(8, minimum // 8))
    rows: list[dict] = []
    for line in ([] if raw is None else raw[:, 0, :]):
        x0, y0, x1, y1 = [float(value) for value in line]
        length = math.hypot(x1 - x0, y1 - y0)
        if length < minimum:
            continue
        angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
        angle = ((angle + 90.0) % 180.0) - 90.0
        p0 = np.array((x0, y0, 1.0), dtype=np.float64)
        p1 = np.array((x1, y1, 1.0), dtype=np.float64)
        homogeneous = np.cross(p0, p1)
        norm = math.hypot(homogeneous[0], homogeneous[1])
        if norm <= 1e-8:
            continue
        homogeneous /= norm
        rows.append({
            "p0": (x0, y0), "p1": (x1, y1), "length": length,
            "angle": angle, "line": homogeneous,
            "mid_y": (y0 + y1) * .5,
        })
    return rows, size


def _fit_vanishing_point(lines: Sequence[dict]) -> tuple[float, float] | None:
    if len(lines) < 2:
        return None
    matrix = np.stack([row["line"] * math.sqrt(max(row["length"], 1.0)) for row in lines])
    try:
        _, _, vt = np.linalg.svd(matrix, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    point = vt[-1]
    if abs(float(point[2])) < 1e-8:
        return None
    point = point / point[2]
    if not np.isfinite(point).all():
        return None
    return float(point[0]), float(point[1])


def _estimate_manhattan(lines: Sequence[dict], size: tuple[int, int]) -> dict:
    width, height = size
    if len(lines) < 6:
        return {
            "roll_deg": 0.0, "horizon_y": height * .5,
            "line_count": len(lines), "vertical_line_count": 0,
            "horizon_status": "insufficient_lines", "confidence": 0.0,
            "vanishing_points": [],
        }
    features = np.array([
        (math.cos(math.radians(row["angle"] * 2.0)),
         math.sin(math.radians(row["angle"] * 2.0))) for row in lines
    ], dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, .005)
    try:
        _, labels, _ = cv2.kmeans(features, 3, None, criteria, 5, cv2.KMEANS_PP_CENTERS)
    except cv2.error:
        labels = np.zeros((len(lines), 1), dtype=np.int32)
    clusters: list[list[dict]] = [[] for _ in range(3)]
    for row, label in zip(lines, labels.reshape(-1)):
        clusters[int(label)].append(row)
    cluster_rows = []
    for group in clusters:
        angles = np.array([row["angle"] for row in group], dtype=np.float64)
        weights = np.array([row["length"] for row in group], dtype=np.float64)
        vp = _fit_vanishing_point(group)
        vertical_deviation = np.array([
            angle - 90.0 if angle >= 0 else angle + 90.0 for angle in angles
        ], dtype=np.float64)
        cluster_rows.append({
            "lines": group,
            "weight": float(weights.sum()),
            "vp": vp,
            "vertical_abs_median": float(np.median(np.abs(vertical_deviation))) if len(group) else 90.0,
            "vertical_signed_median": _weighted_median(vertical_deviation, weights) if len(group) else 0.0,
        })
    # Estimate gravity from all truly near-vertical long segments.  Selecting a
    # k-means cluster by its median can let a large perspective/floor diagonal
    # cluster win when furniture produces many short lines.
    direct_vertical = [row for row in lines if abs(abs(float(row["angle"])) - 90.0) <= 15.0]
    direct_angles = np.asarray([
        float(row["angle"]) - 90.0 if float(row["angle"]) >= 0
        else float(row["angle"]) + 90.0 for row in direct_vertical
    ], dtype=np.float64)
    direct_weights = np.asarray([float(row["length"]) for row in direct_vertical], dtype=np.float64)
    roll = _weighted_median(direct_angles, direct_weights) if direct_vertical else 0.0
    vertical_index = int(np.argmin([row["vertical_abs_median"] for row in cluster_rows]))
    vertical = cluster_rows[vertical_index]
    horizontal = [row for index, row in enumerate(cluster_rows) if index != vertical_index and row["vp"]]
    horizon_y = height * .5
    horizon_status = "default_center"
    vanishing_points = [list(row["vp"]) for row in horizontal]
    if len(horizontal) >= 2:
        first = np.array((*horizontal[0]["vp"], 1.0), dtype=np.float64)
        second = np.array((*horizontal[1]["vp"], 1.0), dtype=np.float64)
        horizon = np.cross(first, second)
        if abs(float(horizon[1])) > 1e-8:
            candidate = -(horizon[0] * (width * .5) + horizon[2]) / horizon[1]
            if -.25 * height <= candidate <= 1.25 * height:
                horizon_y = float(candidate)
                horizon_status = "two_horizontal_vanishing_points"
    elif len(horizontal) == 1:
        candidate = float(horizontal[0]["vp"][1])
        if -.25 * height <= candidate <= 1.25 * height:
            horizon_y = candidate
            horizon_status = "single_horizontal_vanishing_point"
    vertical_count = len(direct_vertical)
    line_factor = min(1.0, vertical_count / 8.0)
    horizon_factor = 1.0 if horizon_status.startswith("two_") else (.65 if horizon_status.startswith("single_") else .2)
    spread_factor = min(1.0, len(lines) / 28.0)
    confidence = .45 * line_factor + .35 * horizon_factor + .20 * spread_factor
    return {
        "roll_deg": round(float(roll), 5),
        "horizon_y": round(float(horizon_y), 3),
        "line_count": len(lines),
        "vertical_line_count": vertical_count,
        "horizon_status": horizon_status,
        "confidence": round(float(np.clip(confidence, 0.0, 1.0)), 5),
        "vanishing_points": vanishing_points,
    }


def analyze_perspective_geometry(image: Image.Image, params: dict, *, source_hash: str = "",
                                 reference_role: str = "perspective_source") -> dict:
    source = ImageOps.exif_transpose(image).convert("RGB")
    lines, working_size = _line_observations(source)
    manhattan = _estimate_manhattan(lines, working_size)
    fov, fov_source = parse_horizontal_fov(params)
    focal_px = (working_size[0] * .5) / math.tan(math.radians(fov) * .5)
    pitch_deg = math.degrees(math.atan2(
        working_size[1] * .5 - float(manhattan["horizon_y"]), max(focal_px, 1e-6)))
    camera_height = float(params.get("camera_height_m") or 1.55)
    camera_height = float(np.clip(camera_height, .5, 2.5))
    camera_confidence = float(manhattan["confidence"])
    status = "ready" if camera_confidence >= .45 else "needs_calibration"
    contract = {
        "version": LOCAL_GEOMETRY_VERSION,
        "status": status,
        "reference_role": reference_role,
        "source_sha256": source_hash or image_sha256(source),
        "source_size": [source.width, source.height],
        "working_size": list(working_size),
        "camera": {
            "horizontal_fov_deg": round(fov, 5),
            "fov_source": fov_source,
            "pitch_deg": round(float(np.clip(pitch_deg, -30.0, 30.0)), 5),
            "roll_deg": float(manhattan["roll_deg"]),
            "source_yaw_deg": 0.0,
            "camera_height_m": round(camera_height, 5),
            "camera_height_source": ("request" if params.get("camera_height_m") else "standard_visual_assumption"),
        },
        "floor_frame": {
            "normal": [0.0, 1.0, 0.0],
            "plank_direction_deg": float(params.get("floor_rotation_deg") or 90.0),
            "origin_x_m": 0.0,
            "origin_z_m": 0.0,
            "scale_source": "camera_height_assumption",
        },
        "manhattan": manhattan,
        "confidence": round(camera_confidence, 5),
        "warnings": ([] if status == "ready" else ["相机/地平线自动标定置信度不足，需要本地校准"]),
    }
    contract["contract_hash"] = _stable_hash(contract)
    return contract


def build_geometry_contract(path: str, params: dict, *, reference_role: str = "perspective_source") -> dict:
    with Image.open(path) as opened:
        opened.load()
        source = ImageOps.exif_transpose(opened).convert("RGB").copy()
    with open(path, "rb") as handle:
        source_hash = hashlib.sha256(handle.read()).hexdigest()
    return analyze_perspective_geometry(
        source, params, source_hash=source_hash, reference_role=reference_role)


def build_cubemap_geometry_contract(params: dict) -> dict:
    """Authoritative camera contract for a canonical 90-degree cube atlas."""
    camera_height = float(np.clip(float(params.get("camera_height_m") or 1.55), .5, 2.5))
    contract = {
        "version": LOCAL_GEOMETRY_VERSION,
        "status": "ready",
        "reference_role": "canonical_cubemap_no_room_reference",
        "source_sha256": "",
        "source_size": [3072, 2048],
        "working_size": [1024, 1024],
        "camera": {
            "horizontal_fov_deg": 90.0,
            "fov_source": "canonical_cube_face",
            "pitch_deg": 0.0,
            "roll_deg": 0.0,
            "source_yaw_deg": 0.0,
            "camera_height_m": round(camera_height, 5),
            "camera_height_source": ("request" if params.get("camera_height_m") else "standard_visual_assumption"),
        },
        "floor_frame": {
            "normal": [0.0, 1.0, 0.0],
            "plank_direction_deg": float(params.get("floor_rotation_deg") or 90.0),
            "origin_x_m": 0.0,
            "origin_z_m": 0.0,
            "scale_source": "canonical_cube_plus_camera_height_assumption",
        },
        "manhattan": {
            "roll_deg": 0.0, "horizon_y": 512.0,
            "line_count": 0, "vertical_line_count": 0,
            "horizon_status": "canonical_cube_equator", "confidence": 1.0,
            "vanishing_points": [],
        },
        "confidence": .8,
        "warnings": ["未提供空间参考图；投影受六面相机合同约束，但不代表真实房间尺寸"],
    }
    contract["contract_hash"] = _stable_hash(contract)
    return contract


def validate_geometry_contract(contract: dict, source_path: str = "") -> None:
    row = dict(contract or {})
    expected = str(row.pop("contract_hash", ""))
    if not expected or expected != _stable_hash(row):
        raise ValueError("local_geometry_contract_tampered")
    if source_path:
        with open(source_path, "rb") as handle:
            actual = hashlib.sha256(handle.read()).hexdigest()
        if actual != str(contract.get("source_sha256") or ""):
            raise ValueError("local_geometry_source_changed")


def public_geometry_contract(contract: dict | None) -> dict | None:
    if not contract:
        return None
    row = json.loads(json.dumps(contract, ensure_ascii=False))
    registration = row.get("registration")
    if isinstance(registration, dict):
        registration.pop("homography", None)
    return row


def _match_view(source: Image.Image, target: Image.Image) -> dict:
    width = 512
    height = max(192, round(width * source.height / source.width))
    first = np.asarray(source.resize((width, height), Image.Resampling.LANCZOS), dtype=np.uint8)
    second = np.asarray(target.resize((width, height), Image.Resampling.LANCZOS), dtype=np.uint8)
    first_gray = cv2.cvtColor(first, cv2.COLOR_RGB2GRAY)
    second_gray = cv2.cvtColor(second, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    first_gray, second_gray = clahe.apply(first_gray), clahe.apply(second_gray)
    detector = cv2.SIFT_create(nfeatures=4000, contrastThreshold=.012, edgeThreshold=15)
    kp_a, desc_a = detector.detectAndCompute(first_gray, None)
    kp_b, desc_b = detector.detectAndCompute(second_gray, None)
    if desc_a is None or desc_b is None:
        return {"inliers": 0, "matches": 0, "spread": 0.0, "median_error_px": 999.0}
    matches = []
    for pair in cv2.BFMatcher().knnMatch(desc_a, desc_b, k=2):
        if len(pair) == 2 and pair[0].distance < .76 * pair[1].distance:
            matches.append(pair[0])
    if len(matches) < 6:
        return {"inliers": 0, "matches": len(matches), "spread": 0.0, "median_error_px": 999.0}
    points_a = np.float32([kp_a[item.queryIdx].pt for item in matches])
    points_b = np.float32([kp_b[item.trainIdx].pt for item in matches])
    affine, mask = cv2.estimateAffinePartial2D(
        points_a, points_b, method=cv2.RANSAC, ransacReprojThreshold=4.0,
        maxIters=5000, confidence=.999, refineIters=20)
    if affine is None or mask is None:
        return {"inliers": 0, "matches": len(matches), "spread": 0.0, "median_error_px": 999.0}
    keep = mask.reshape(-1) > 0
    inliers = int(keep.sum())
    if not inliers:
        return {"inliers": 0, "matches": len(matches), "spread": 0.0, "median_error_px": 999.0}
    predicted = cv2.transform(points_a.reshape(-1, 1, 2), affine).reshape(-1, 2)
    error = np.linalg.norm(predicted - points_b, axis=1)[keep]
    selected = points_a[keep]
    spread = float((np.ptp(selected[:, 0]) / width) * (np.ptp(selected[:, 1]) / height))
    corners = np.float32([[[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]])
    transformed_corners = cv2.transform(corners, affine)[0]
    area_ratio = abs(float(cv2.contourArea(transformed_corners))) / max(1.0, width * height)
    edge_lengths = [float(np.linalg.norm(transformed_corners[(index + 1) % 4] - transformed_corners[index]))
                    for index in range(4)]
    scale = math.sqrt(float(affine[0, 0]) ** 2 + float(affine[1, 0]) ** 2)
    rotation = math.degrees(math.atan2(float(affine[1, 0]), float(affine[0, 0])))
    bounds_ok = bool(
        np.all(transformed_corners[:, 0] >= -width * .6)
        and np.all(transformed_corners[:, 0] <= width * 1.6)
        and np.all(transformed_corners[:, 1] >= -height * .6)
        and np.all(transformed_corners[:, 1] <= height * 1.6))
    geometry_valid = bool(
        .22 <= scale <= 3.5 and abs(rotation) <= 20.0
        and .05 <= area_ratio <= 4.0
        and min(edge_lengths) >= min(width, height) * .18
        and bounds_ok)
    homography, _ = cv2.findHomography(points_a, points_b, cv2.USAC_MAGSAC, 3.5)
    return {
        "inliers": inliers,
        "matches": len(matches),
        "inlier_ratio": round(inliers / max(1, len(matches)), 5),
        "spread": round(spread, 5),
        "median_error_px": round(float(np.median(error)), 5),
        "affine": affine.tolist(),
        "homography_audit": homography.tolist() if homography is not None else None,
        "transform_geometry_valid": geometry_valid,
        "transform_scale": round(scale, 5),
        "transform_rotation_deg": round(rotation, 5),
        "transform_area_ratio": round(area_ratio, 5),
        "transform_corners": transformed_corners.tolist(),
        "working_size": [width, height],
    }


def register_source_to_erp(source: Image.Image, erp: Image.Image, contract: dict | None = None) -> dict:
    """Locate a generated perspective source inside an ERP using local features."""
    source = ImageOps.exif_transpose(source).convert("RGB")
    erp = ImageOps.exif_transpose(erp).convert("RGB")
    camera = dict((contract or {}).get("camera") or {})
    base_pitch = float(camera.get("pitch_deg") or 0.0)
    parsed_fov = float(camera.get("horizontal_fov_deg") or 84.0)
    fovs = sorted(set(round(value, 3) for value in (
        max(60.0, parsed_fov), 84.0, 100.0, 116.0)))
    pitches = sorted(set(round(float(np.clip(value, -25.0, 25.0)), 3)
                         for value in (base_pitch - 8.0, base_pitch, base_pitch + 8.0)))
    candidates = []
    target_width = 512
    target_height = max(192, round(target_width * source.height / source.width))
    for yaw in (0.0,):
        for pitch in pitches:
            for fov in fovs:
                view = erp_to_perspective(erp, yaw, pitch, fov, target_width, target_height)
                match = _match_view(source, view)
                match.update({"yaw_deg": yaw, "pitch_deg": pitch, "fov_deg": fov})
                candidates.append(match)
    best = max(candidates, key=lambda row: (
        int(row.get("inliers") or 0), float(row.get("spread") or 0.0),
        -float(row.get("median_error_px") or 999.0)))
    if int(best.get("inliers") or 0) < 8:
        for yaw in (-12.0, 12.0):
            for pitch in pitches:
                for fov in fovs:
                    view = erp_to_perspective(erp, yaw % 360.0, pitch, fov, target_width, target_height)
                    match = _match_view(source, view)
                    match.update({"yaw_deg": yaw, "pitch_deg": pitch, "fov_deg": fov})
                    candidates.append(match)
        best = max(candidates, key=lambda row: (
            int(row.get("inliers") or 0), float(row.get("spread") or 0.0),
            -float(row.get("median_error_px") or 999.0)))
    pose_ready = (
        int(best.get("inliers") or 0) >= 6
        and (float(best.get("spread") or 0.0) >= .12
             or (bool(best.get("transform_geometry_valid"))
                 and int(best.get("inliers") or 0) >= 20
                 and float(best.get("spread") or 0.0) >= .075))
        and float(best.get("median_error_px") or 999.0) <= 4.0
    )
    source_lock_ready = bool(
        pose_ready and int(best.get("inliers") or 0) >= 60
        and float(best.get("spread") or 0.0) >= .25
        and bool(best.get("transform_geometry_valid")))
    return {
        **best,
        "version": LOCAL_GEOMETRY_VERSION,
        "status": "ready" if pose_ready else "needs_calibration",
        "source_lock_ready": source_lock_ready,
        "searched_views": len(candidates),
        "source_sha256": image_sha256(source),
        "erp_sha256": image_sha256(erp),
    }


def lock_registered_source_view(source: Image.Image, erp: Image.Image,
                                registration: dict, *, feather_px: int = 28,
                                chunk_rows: int = 96) -> tuple[Image.Image, dict]:
    """Reproject a high-confidence original perspective sector into an ERP.

    The source-to-view homography keeps every straight source line straight.
    Only the transformed source footprint is written and its boundary is
    feathered inward; unseen panorama sectors remain byte-identical.
    """
    if str((registration or {}).get("status") or "") != "ready" or not registration.get("source_lock_ready"):
        return erp.convert("RGB").copy(), {"status": "skipped", "reason": "source_lock_not_safe"}
    affine = np.asarray(registration.get("affine"), dtype=np.float64)
    working_size = list(registration.get("working_size") or [])
    if affine.shape != (2, 3) or len(working_size) != 2 or not np.isfinite(affine).all():
        return erp.convert("RGB").copy(), {"status": "skipped", "reason": "registration_matrix_invalid"}
    view_width, view_height = int(working_size[0]), int(working_size[1])
    if view_width < 64 or view_height < 64:
        return erp.convert("RGB").copy(), {"status": "skipped", "reason": "registration_size_invalid"}
    source_work = np.asarray(
        source.convert("RGB").resize((view_width, view_height), Image.Resampling.LANCZOS),
        dtype=np.uint8)
    patch = cv2.warpAffine(
        source_work, affine, (view_width, view_height), flags=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    footprint = cv2.warpAffine(
        np.full((view_height, view_width), 255, dtype=np.uint8), affine,
        (view_width, view_height), flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if cv2.countNonZero(footprint) < view_width * view_height * .05:
        return erp.convert("RGB").copy(), {"status": "skipped", "reason": "source_footprint_too_small"}
    distance = cv2.distanceTransform((footprint > 0).astype(np.uint8), cv2.DIST_L2, 5)
    patch_alpha = np.clip(distance / max(2.0, float(feather_px)), 0.0, 1.0).astype(np.float32)

    yaw = math.radians(float(registration.get("yaw_deg") or 0.0))
    pitch = math.radians(float(registration.get("pitch_deg") or 0.0))
    forward = np.array((math.cos(pitch) * math.sin(yaw), math.sin(pitch),
                        math.cos(pitch) * math.cos(yaw)), dtype=np.float32)
    right = np.cross(forward, np.array((0.0, 1.0, 0.0), dtype=np.float32))
    right /= max(float(np.linalg.norm(right)), 1e-7)
    up = np.cross(right, forward)
    up /= max(float(np.linalg.norm(up)), 1e-7)
    focal = (view_width * .5) / math.tan(math.radians(float(registration["fov_deg"])) * .5)

    base = np.asarray(erp.convert("RGB"), dtype=np.uint8)
    height, width = base.shape[:2]
    output = base.copy()
    u = (np.arange(width, dtype=np.float32) + .5) / width
    lam = 2.0 * np.pi * (u - .5)
    sin_lam, cos_lam = np.sin(lam)[None], np.cos(lam)[None]
    written = 0
    for start in range(0, height, max(1, int(chunk_rows))):
        stop = min(height, start + max(1, int(chunk_rows)))
        v = (np.arange(start, stop, dtype=np.float32) + .5) / height
        phi = np.pi * (.5 - v)
        cos_phi = np.cos(phi)[:, None]
        directions = np.stack((
            cos_phi * sin_lam,
            np.broadcast_to(np.sin(phi)[:, None], (stop - start, width)),
            cos_phi * cos_lam,
        ), axis=-1)
        depth = directions @ forward
        valid = depth > 1e-5
        local_x = np.zeros_like(depth)
        local_y = np.zeros_like(depth)
        np.divide(directions @ right, depth, out=local_x, where=valid)
        np.divide(directions @ up, depth, out=local_y, where=valid)
        map_x = (local_x * focal + view_width * .5).astype(np.float32)
        map_y = (-local_y * focal + view_height * .5).astype(np.float32)
        valid &= (map_x >= 0) & (map_x < view_width - 1) & (map_y >= 0) & (map_y < view_height - 1)
        sampled_alpha = cv2.remap(
            patch_alpha, map_x, map_y, cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        sampled_alpha *= valid.astype(np.float32)
        selected = sampled_alpha > 1e-4
        if not np.any(selected):
            continue
        sampled = cv2.remap(
            patch, map_x, map_y, cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        alpha = sampled_alpha[..., None]
        block = output[start:stop].astype(np.float32)
        mixed = np.rint(block * (1.0 - alpha) + sampled.astype(np.float32) * alpha)
        output[start:stop][selected] = np.clip(mixed, 0, 255).astype(np.uint8)[selected]
        written += int(selected.sum())
    return Image.fromarray(output, "RGB"), {
        "status": "applied",
        "mode": "registered_perspective_sector_reprojection",
        "written_fraction": round(written / max(1, width * height), 6),
        "feather_px": int(feather_px),
        "outside_sector_preserved": True,
    }


def _decode_b64_image(value: str, mode: str) -> Image.Image:
    raw = str(value or "").split(",", 1)[-1]
    with Image.open(io.BytesIO(base64.b64decode(raw, validate=True))) as source:
        source.load()
        return source.convert(mode).copy()


def _mask_boundary(mask: np.ndarray) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8)
    return cv2.morphologyEx(binary, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0


def refine_erp_floor_mask(image: Image.Image, mask: Image.Image,
                          view_rows: Sequence[dict] = ()) -> tuple[Image.Image, dict]:
    """Conservatively clean a semantic ERP mask and audit depth support.

    Relative depth is used as boundary evidence only.  It never supplies metric
    scale and never expands the mask across a strong depth discontinuity.
    """
    source = ImageOps.exif_transpose(image).convert("RGB")
    raw = np.asarray(mask.convert("L").resize(source.size, Image.Resampling.NEAREST), dtype=np.uint8)
    binary = (raw >= 128).astype(np.uint8)
    height, width = binary.shape
    # A horizontal floor is never above the ERP horizon.
    binary[:max(0, round(height * .495))] = 0
    pad = max(8, round(width * .006))
    wrapped = np.concatenate((binary[:, -pad:], binary, binary[:, :pad]), axis=1)
    wrapped = cv2.morphologyEx(
        wrapped, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=1)
    wrapped = cv2.morphologyEx(wrapped, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    binary = wrapped[:, pad:pad + width]
    # Drop tiny, disconnected false positives while retaining every substantial
    # lower-hemisphere floor component.  Do not invent a fixed nadir cap.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    cleaned = np.zeros_like(binary)
    minimum = max(32, round(binary.size * .0008))
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < minimum:
            continue
        ys = np.nonzero(labels == index)[0]
        if ys.size and float(np.median(ys)) >= height * .50:
            cleaned[labels == index] = 1
    binary = cleaned

    confidence_values = []
    semantic_values = []
    semantic_rows = []
    depth_rows = []
    nadir_supported = False
    for row in view_rows:
        try:
            view = _decode_b64_image(str(row.get("image_b64") or ""), "RGB")
            view_mask = np.asarray(_decode_b64_image(str(row.get("mask_b64") or ""), "L")) >= 128
        except Exception:
            continue
        confidence_values.append(float(row.get("confidence") or 0.0))
        semantic_values.append(float(row.get("semantic_confidence") or 0.0))
        semantic_rows.append({
            "id": row.get("id"),
            "status": row.get("semantic_status"),
            "confidence": row.get("semantic_confidence"),
            "model": row.get("semantic_model"),
        })
        if str(row.get("id") or "") == "nadir":
            cy0, cy1 = round(view.height * .42), round(view.height * .58)
            cx0, cx1 = round(view.width * .42), round(view.width * .58)
            nadir_supported = bool(float(view_mask[cy0:cy1, cx0:cx1].mean()) >= .50)
        depth = predict_relative_depth(view)
        if depth.status != "ok" or depth.edge is None:
            depth_rows.append({"id": row.get("id"), "status": depth.status, "error": depth.error})
            continue
        boundary = _mask_boundary(view_mask.astype(np.uint8))
        support = float(np.mean(depth.edge[boundary] >= .22)) if np.any(boundary) else 0.0
        depth_rows.append({
            "id": row.get("id"), "status": "ok",
            "boundary_depth_support": round(support, 5),
        })
    coverage = float(binary.mean())
    # Detect significant old-floor islands left outside the semantic mask.
    # This is an audit only: ambiguous pixels are never auto-painted.  A portal
    # or corridor with the same original floor therefore routes to the free
    # local mask editor instead of producing a visibly two-material result.
    audit_width = min(960, width)
    audit_height = max(1, round(height * audit_width / width))
    audit_rgb = np.asarray(source.resize((audit_width, audit_height), Image.Resampling.LANCZOS), dtype=np.uint8)
    audit_mask = cv2.resize(binary, (audit_width, audit_height), interpolation=cv2.INTER_NEAREST) > 0
    residual_fraction = 0.0
    residual_components = []
    if int(audit_mask.sum()) >= 512:
        audit_lab = cv2.cvtColor(audit_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        sample = audit_lab[audit_mask]
        low_l, high_l = np.percentile(sample[:, 0], (25.0, 98.0))
        sample = sample[(sample[:, 0] >= low_l) & (sample[:, 0] <= high_l)]
        if len(sample) >= 256:
            centre = np.median(sample, axis=0)
            scale = np.maximum(
                np.median(np.abs(sample - centre), axis=0) * 1.4826,
                np.array((8.0, 4.0, 4.0), dtype=np.float32))
            distance = np.sqrt(np.mean(((audit_lab - centre) / scale) ** 2, axis=2))
            yy = np.arange(audit_height)[:, None]
            residual = (distance <= 2.6) & ~audit_mask & (yy >= audit_height * .50)
            residual = cv2.morphologyEx(
                residual.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            count, labels, stats, centroids = cv2.connectedComponentsWithStats(residual, 8)
            minimum = max(24, round(residual.size * .0006))
            retained = np.zeros_like(residual, dtype=bool)
            for index in range(1, count):
                area = int(stats[index, cv2.CC_STAT_AREA])
                box_width = int(stats[index, cv2.CC_STAT_WIDTH])
                box_height = int(stats[index, cv2.CC_STAT_HEIGHT])
                centre_y = float(centroids[index, 1])
                if area < minimum or centre_y < audit_height * .54:
                    continue
                # Thin vertical wood cabinets are not floor islands.  Keep
                # broad/trapezoid components and substantial ambiguous zones.
                if box_height > box_width * 1.5 and area < residual.size * .01:
                    continue
                retained |= labels == index
                residual_components.append({
                    "area_fraction": round(area / residual.size, 6),
                    "bbox": [
                        int(stats[index, cv2.CC_STAT_LEFT]), int(stats[index, cv2.CC_STAT_TOP]),
                        box_width, box_height,
                    ],
                })
            residual_fraction = float(retained.mean())
    semantic_confidence = float(np.median(confidence_values)) if confidence_values else 0.0
    clipseg_confidence = float(np.median(semantic_values)) if semantic_values else 0.0
    depth_ok = [float(row.get("boundary_depth_support") or 0.0)
                for row in depth_rows if row.get("status") == "ok"]
    depth_support = float(np.median(depth_ok)) if depth_ok else 0.0
    plausible = .04 <= coverage <= .70
    combined_confidence = (
        .35 * semantic_confidence + .25 * clipseg_confidence
        + .15 * min(1.0, depth_support / .35)
        + .15 * float(nadir_supported) + .10 * float(plausible))
    residual_ok = residual_fraction <= .006
    status = ("ready" if binary.any() and plausible and combined_confidence >= .42 and residual_ok
              else "needs_calibration")
    output = Image.fromarray(binary.astype(np.uint8) * 255, "L")
    metadata = {
        "version": LOCAL_MASK_VERSION,
        "status": status,
        "coverage": round(coverage, 6),
        "semantic_confidence": round(semantic_confidence, 5),
        "clipseg_floor_confidence": round(clipseg_confidence, 5),
        "semantic_views": semantic_rows,
        "depth_boundary_support": round(depth_support, 5),
        "nadir_supported": nadir_supported,
        "fixed_nadir_fill": False,
        "combined_confidence": round(float(np.clip(combined_confidence, 0.0, 1.0)), 5),
        "residual_floor_like_fraction": round(residual_fraction, 6),
        "residual_floor_like_threshold": .006,
        "residual_floor_like_components": residual_components,
        "depth_model": depth_model_status(),
        "depth_views": depth_rows,
        "warnings": ([] if status == "ready" else [
            ("检测到蒙版外仍有与原地板一致的门洞/走廊区域，需要本地补画"
             if not residual_ok else "自动地板边界置信度不足，需要本地蒙版校准")]),
    }
    return output, metadata


def _horizontal_curve_candidates(view: Image.Image) -> list[dict]:
    gray = cv2.cvtColor(np.asarray(view, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 45, 135)
    connected = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (35, 9)), iterations=1)
    contours, _ = cv2.findContours(connected, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    candidates = []
    for contour in contours:
        points = contour[:, 0, :].astype(np.float32)
        if len(points) < 40:
            continue
        xs, ys = points[:, 0], points[:, 1]
        x_span, y_span = float(np.ptp(xs)), float(np.ptp(ys))
        median_y = float(np.median(ys))
        if x_span < view.width * .28 or y_span > x_span * .20 or median_y > view.height * .72:
            continue
        left, right = int(np.floor(xs.min())), int(np.ceil(xs.max()))
        x_axis = np.arange(left, right + 1, dtype=np.float32)
        centreline = np.full(x_axis.shape, np.nan, dtype=np.float32)
        rounded_x = np.rint(xs).astype(np.int32)
        for index, x_value in enumerate(range(left, right + 1)):
            values = ys[np.abs(rounded_x - x_value) <= 1]
            if values.size:
                centreline[index] = float(np.median(values))
        known = np.isfinite(centreline)
        if int(known.sum()) < max(24, len(centreline) // 5):
            continue
        centre_points = np.stack((x_axis[known], centreline[known]), axis=1).astype(np.float32)
        vx, vy, x0, y0 = [float(value) for value in cv2.fitLine(
            centre_points, cv2.DIST_HUBER, 0, .01, .01).reshape(-1)]
        residual = np.abs(
            vy * (centre_points[:, 0] - x0) - vx * (centre_points[:, 1] - y0))
        candidates.append({
            "points": points,
            "p90": float(np.percentile(residual, 90)),
            "x_span": x_span,
            "median_y": median_y,
            "line": (vx, vy, x0, y0),
        })
    return sorted(candidates, key=lambda row: row["p90"], reverse=True)


def _rectify_view_horizontal_curve(view: Image.Image) -> tuple[Image.Image, Image.Image, dict]:
    candidates = [row for row in _horizontal_curve_candidates(view) if 4.0 < row["p90"] <= 8.0]
    if not candidates:
        return view.copy(), Image.new("L", view.size, 0), {"status": "not_needed"}
    candidate = candidates[0]
    points = candidate["points"]
    xs, ys = points[:, 0], points[:, 1]
    left, right = max(0, int(np.floor(xs.min()))), min(view.width - 1, int(np.ceil(xs.max())))
    x_axis = np.arange(left, right + 1, dtype=np.float32)
    curve = np.full(x_axis.shape, np.nan, dtype=np.float32)
    rounded_x = np.rint(xs).astype(np.int32)
    for index, x_value in enumerate(range(left, right + 1)):
        values = ys[np.abs(rounded_x - x_value) <= 1]
        if values.size:
            curve[index] = float(np.median(values))
    known = np.isfinite(curve)
    if int(known.sum()) < max(24, len(curve) // 5):
        return view.copy(), Image.new("L", view.size, 0), {"status": "rejected", "reason": "curve_samples_sparse"}
    curve = np.interp(x_axis, x_axis[known], curve[known]).astype(np.float32)
    curve = cv2.GaussianBlur(curve[None], (0, 0), sigmaX=7.0)[0]
    vx, vy, x0, y0 = candidate["line"]
    if abs(vx) < 1e-5:
        return view.copy(), Image.new("L", view.size, 0), {"status": "rejected", "reason": "curve_line_vertical"}
    target = y0 + (x_axis - x0) * (vy / vx)
    displacement = np.clip(curve - target, -8.0, 8.0)
    if float(np.max(np.abs(displacement))) < 1.0:
        return view.copy(), Image.new("L", view.size, 0), {"status": "not_needed"}

    height, width = view.height, view.width
    full_displacement = np.zeros(width, dtype=np.float32)
    full_displacement[left:right + 1] = displacement
    taper = np.zeros(width, dtype=np.float32)
    taper[left:right + 1] = 1.0
    taper_width = max(12, round((right - left + 1) * .10))
    if taper_width * 2 < right - left + 1:
        ramp = np.linspace(0.0, 1.0, taper_width, dtype=np.float32)
        taper[left:left + taper_width] = ramp
        taper[right - taper_width + 1:right + 1] = ramp[::-1]
    yy, xx = np.mgrid[:height, :width].astype(np.float32)
    curve_full = np.interp(np.arange(width), x_axis, curve, left=curve[0], right=curve[-1])
    vertical_influence = np.exp(-((yy - curve_full[None]) / 78.0) ** 2).astype(np.float32)
    vertical_influence[yy > height * .74] = 0.0
    influence = vertical_influence * taper[None]
    map_x = xx
    map_y = yy + full_displacement[None] * influence
    corrected = cv2.remap(
        np.asarray(view.convert("RGB"), dtype=np.uint8), map_x, map_y,
        cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT_101)
    alpha = np.clip(influence * .98, 0.0, 1.0)
    return Image.fromarray(corrected, "RGB"), Image.fromarray(
        np.rint(alpha * 255).astype(np.uint8), "L"), {
            "status": "applied",
            "curve_p90_before_px": round(float(candidate["p90"]), 5),
            "max_vertical_displacement_px": round(float(np.max(np.abs(displacement))), 5),
            "x_range": [left, right],
        }


def _project_perspective_edit(erp: Image.Image, patch: Image.Image, alpha_mask: Image.Image,
                              yaw_deg: float, pitch_deg: float, fov_deg: float,
                              *, chunk_rows: int = 96) -> Image.Image:
    base = np.asarray(erp.convert("RGB"), dtype=np.uint8)
    patch_arr = np.asarray(patch.convert("RGB"), dtype=np.uint8)
    alpha_arr = np.asarray(alpha_mask.convert("L"), dtype=np.float32) / 255.0
    height, width = base.shape[:2]
    view_height, view_width = alpha_arr.shape
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    forward = np.array((math.cos(pitch) * math.sin(yaw), math.sin(pitch),
                        math.cos(pitch) * math.cos(yaw)), dtype=np.float32)
    right = np.cross(forward, np.array((0.0, 1.0, 0.0), dtype=np.float32))
    right /= max(float(np.linalg.norm(right)), 1e-7)
    up = np.cross(right, forward)
    up /= max(float(np.linalg.norm(up)), 1e-7)
    focal = (view_width * .5) / math.tan(math.radians(fov_deg) * .5)
    u = (np.arange(width, dtype=np.float32) + .5) / width
    lam = 2.0 * np.pi * (u - .5)
    sin_lam, cos_lam = np.sin(lam)[None], np.cos(lam)[None]
    output = base.copy()
    for start in range(0, height, max(1, int(chunk_rows))):
        stop = min(height, start + max(1, int(chunk_rows)))
        v = (np.arange(start, stop, dtype=np.float32) + .5) / height
        phi = np.pi * (.5 - v)
        cos_phi = np.cos(phi)[:, None]
        directions = np.stack((
            cos_phi * sin_lam,
            np.broadcast_to(np.sin(phi)[:, None], (stop - start, width)),
            cos_phi * cos_lam,
        ), axis=-1)
        depth = directions @ forward
        valid = depth > 1e-5
        local_x, local_y = np.zeros_like(depth), np.zeros_like(depth)
        np.divide(directions @ right, depth, out=local_x, where=valid)
        np.divide(directions @ up, depth, out=local_y, where=valid)
        map_x = (local_x * focal + view_width * .5).astype(np.float32)
        map_y = (-local_y * focal + view_height * .5).astype(np.float32)
        valid &= (map_x >= 0) & (map_x < view_width - 1) & (map_y >= 0) & (map_y < view_height - 1)
        alpha = cv2.remap(alpha_arr, map_x, map_y, cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        alpha *= valid.astype(np.float32)
        selected = alpha > 1e-4
        if not np.any(selected):
            continue
        sampled = cv2.remap(patch_arr, map_x, map_y, cv2.INTER_LANCZOS4,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        block = output[start:stop].astype(np.float32)
        mixed = np.rint(block * (1.0 - alpha[..., None]) + sampled * alpha[..., None])
        output[start:stop][selected] = np.clip(mixed, 0, 255).astype(np.uint8)[selected]
    return Image.fromarray(output, "RGB")


def _architecture_view_rows(image: Image.Image) -> list[dict]:
    rows = []
    for yaw in range(0, 360, 45):
        view = erp_to_perspective(image, yaw, 0.0, 84.0, 720, 480)
        lines, _ = _line_observations(view, max_side=720)
        deviations = []
        signed = []
        weights = []
        for row in lines:
            angle = float(row["angle"])
            value = angle - 90.0 if angle >= 0 else angle + 90.0
            if abs(value) <= 20.0 and row["length"] >= 110:
                deviations.append(abs(value))
                signed.append(value)
                weights.append(float(row["length"]))
        if len(deviations) >= 3:
            signed_array = np.asarray(signed, dtype=np.float64)
            weight_array = np.asarray(weights, dtype=np.float64)
            median_signed = _weighted_median(signed_array, weight_array)
            # Architectural verticals form the dominant orientation mode.
            # Furniture legs and perspective diagonals can also fall inside the
            # broad +/-20 degree prefilter; keep only the robust gravity mode.
            residual = np.abs(signed_array - median_signed)
            mode = residual <= max(2.5, float(np.percentile(residual, 55)))
            robust = np.abs(signed_array[mode]) if np.any(mode) else np.abs(signed_array)
            p90 = float(np.percentile(robust, 90))
            robust_count = int(np.sum(mode)) if np.any(mode) else len(deviations)
        else:
            p90 = 0.0
            median_signed = 0.0
            robust_count = len(deviations)
        curve_p90 = max((row["p90"] for row in _horizontal_curve_candidates(view)), default=0.0)
        rows.append({
            "yaw_deg": yaw,
            "vertical_line_count": robust_count,
            "vertical_candidate_count": len(deviations),
            "vertical_p90_deviation_deg": round(p90, 5),
            "signed_roll_deg": round(median_signed, 5),
            "horizontal_curve_p90_px": round(curve_p90, 5),
        })
    return rows


def analyze_panorama_architecture(image: Image.Image) -> dict:
    source = ImageOps.exif_transpose(image).convert("RGB")
    rows = _architecture_view_rows(source)
    eligible = [row for row in rows if int(row["vertical_line_count"]) >= 3]
    worst = max((float(row["vertical_p90_deviation_deg"]) for row in eligible), default=0.0)
    worst_curve = max((float(row.get("horizontal_curve_p90_px") or 0.0) for row in rows), default=0.0)
    signed = np.asarray([float(row["signed_roll_deg"]) for row in eligible], dtype=np.float32)
    coherent_roll = float(np.median(signed)) if signed.size else 0.0
    roll_mad = float(np.median(np.abs(signed - coherent_roll))) if signed.size else 999.0
    if worst > 6.0 or worst_curve > 8.0:
        status = "rejected"
    elif worst > 3.5 or worst_curve > 4.0:
        status = "rectify_recommended"
    else:
        status = "passed"
    return {
        "version": LOCAL_ARCHITECTURE_VERSION,
        "status": status,
        "worst_vertical_p90_deg": round(worst, 5),
        "worst_horizontal_curve_p90_px": round(worst_curve, 5),
        "warning_threshold_deg": 3.5,
        "reject_threshold_deg": 6.0,
        "horizontal_curve_warning_px": 4.0,
        "horizontal_curve_reject_px": 8.0,
        "coherent_roll_deg": round(coherent_roll, 5),
        "coherent_roll_mad_deg": round(roll_mad, 5),
        "views": rows,
        "projection_rule": "rectilinear_views_only_raw_erp_curvature_is_normal",
    }


def _rotate_erp_roll(image: Image.Image, roll_deg: float, *, chunk_rows: int = 96) -> Image.Image:
    source = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = source.shape[:2]
    theta = math.radians(float(roll_deg))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    u = (np.arange(width, dtype=np.float32) + .5) / width
    lam = 2.0 * np.pi * (u - .5)
    sin_lam, cos_lam = np.sin(lam)[None], np.cos(lam)[None]
    output = np.zeros_like(source)
    for start in range(0, height, max(1, int(chunk_rows))):
        stop = min(height, start + max(1, int(chunk_rows)))
        v = (np.arange(start, stop, dtype=np.float32) + .5) / height
        phi = np.pi * (.5 - v)
        cos_phi = np.cos(phi)[:, None]
        x = cos_phi * sin_lam
        y = np.broadcast_to(np.sin(phi)[:, None], (stop - start, width))
        z = cos_phi * cos_lam
        # Sample the rolled source for every desired level output ray.
        source_x = cos_t * x - sin_t * y
        source_y = sin_t * x + cos_t * y
        source_z = z
        source_lam = np.arctan2(source_x, source_z)
        source_phi = np.arcsin(np.clip(source_y, -1.0, 1.0))
        map_x = ((source_lam / (2.0 * np.pi) + .5) * width - .5).astype(np.float32)
        map_y = ((.5 - source_phi / np.pi) * height - .5).astype(np.float32)
        output[start:stop] = cv2.remap(
            source, map_x, map_y, cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_WRAP)
    return Image.fromarray(output, "RGB")


def rectify_panorama_architecture(image: Image.Image) -> tuple[Image.Image, dict]:
    """Correct mild coherent roll/curve errors and reject unsafe larger warps."""
    source = ImageOps.exif_transpose(image).convert("RGB")
    before = analyze_panorama_architecture(source)
    if before["status"] == "passed":
        return source.copy(), {"status": "not_needed", "before": before, "after": before}
    if before["status"] == "rejected":
        return source.copy(), {"status": "rejected", "reason": "severe_architecture_distortion", "before": before}
    candidate = source.copy()
    operations = []
    correction = -float(before.get("coherent_roll_deg") or 0.0)
    mad = float(before.get("coherent_roll_mad_deg") or 999.0)
    if .35 <= abs(correction) <= 2.5 and mad <= 2.0:
        candidate = _rotate_erp_roll(candidate, correction)
        operations.append({"mode": "coherent_spherical_roll", "correction_deg": round(correction, 5)})

    for row in before.get("views") or []:
        curve = float(row.get("horizontal_curve_p90_px") or 0.0)
        if not 4.0 < curve <= 8.0:
            continue
        yaw = float(row.get("yaw_deg") or 0.0)
        view = erp_to_perspective(candidate, yaw, 0.0, 84.0, 720, 480)
        corrected_view, alpha, operation = _rectify_view_horizontal_curve(view)
        if operation.get("status") != "applied":
            continue
        candidate = _project_perspective_edit(
            candidate, corrected_view, alpha, yaw, 0.0, 84.0)
        operations.append({"mode": "local_horizontal_curve", "yaw_deg": yaw, **operation})

    if not operations:
        return source.copy(), {
            "status": "rejected", "reason": "non_coherent_or_excessive_local_warp",
            "requested_correction_deg": round(correction, 5), "before": before,
        }
    after = analyze_panorama_architecture(candidate)
    before_score = max(
        float(before["worst_vertical_p90_deg"]) / 3.5,
        float(before["worst_horizontal_curve_p90_px"]) / 4.0)
    after_score = max(
        float(after["worst_vertical_p90_deg"]) / 3.5,
        float(after["worst_horizontal_curve_p90_px"]) / 4.0)
    if after_score < before_score and after["status"] == "passed":
        return candidate, {
            "status": "applied", "operations": operations,
            "protected_floor": True, "mode": "bounded_rectilinear_mesh",
            "before": before, "after": after,
        }
    return source.copy(), {
        "status": "rejected", "reason": "rectification_did_not_pass_gate",
        "operations": operations, "before": before, "after": after,
    }


__all__ = [
    "LOCAL_ARCHITECTURE_VERSION", "LOCAL_GEOMETRY_VERSION", "LOCAL_MASK_VERSION",
    "analyze_panorama_architecture", "analyze_perspective_geometry",
    "build_cubemap_geometry_contract", "build_geometry_contract", "parse_horizontal_fov", "public_geometry_contract",
    "rectify_panorama_architecture", "refine_erp_floor_mask",
    "lock_registered_source_view", "register_source_to_erp", "validate_geometry_contract",
]
