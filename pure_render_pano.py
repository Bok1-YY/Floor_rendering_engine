# -*- coding: utf-8 -*-
"""Pure-render panorama helpers.

This module deliberately does not depend on the whole-home geometry gate.  A
single perspective render contains no authoritative CAD/depth/normal data, so
the checks here are limited to the intrinsic properties of a monoscopic 2:1
equirectangular image.
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
from PIL import Image

from .panorama_quality_planner import public_quality_plan, validate_quality_plan
from .panorama_local_geometry import (
    analyze_panorama_architecture,
    public_geometry_contract,
    validate_geometry_contract,
)


PURE_RENDER_PANO_POLICY = "pure_render_pano_paid_preview_v1"
PURE_RENDER_PANO_GATE_VERSION = "visual_pano_v2"
PURE_RENDER_PANO_WIDTH = 3840
PURE_RENDER_PANO_HEIGHT = 1920
PURE_RENDER_PANO_SIZE = f"{PURE_RENDER_PANO_WIDTH}x{PURE_RENDER_PANO_HEIGHT}"
PURE_RENDER_PANO_WARNING = (
    "背面、侧面和顶部等源图不可见区域由 AI 补全；结果不代表真实户型、尺寸或施工几何。"
)
PAID_PREVIEW_TTL_SECONDS = 15 * 60
WRAP_SEAM_COLOR_MEDIAN_MAX = 15.0
WRAP_SEAM_EDGE_DIFF_MAX = 0.06
POLE_NEIGHBOR_MEAN_WARN = 25.0
HORIZON_ANGLE_WARN_DEG = 3.0

PURE_RENDER_REVIEW_ITEMS = (
    "wrap_seam",
    "horizon_and_lines",
    "object_integrity",
    "floor_and_material",
    "lighting_continuity",
    "poles",
)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _iso_utc(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(float(epoch_seconds), timezone.utc).isoformat().replace("+00:00", "Z")


def build_pure_render_pano_prompt(params: Optional[dict] = None, *, source_label: str = "") -> str:
    """Build a dedicated ERP prompt without perspective-only camera/aspect instructions."""
    values = dict(params or {})

    def value(name: str) -> str:
        return str(values.get(name) or "").strip()[:300]

    context = [
        ("space type", value("room_type") or value("cn_room_type")),
        ("property type", value("property_type")),
        ("interior style", value("style_type")),
        ("lighting", value("lighting")),
        ("floor tone", value("floor_tone")),
        ("floor format", value("floor_size")),
        ("floor seam", value("seam_type")),
        ("floor gloss", value("glossiness")),
    ]
    context_lines = [f"- {label}: {text}" for label, text in context if text]
    source_line = f" The visual anchor came from {source_label}." if source_label else ""
    lines = [
        "Create exactly one complete monoscopic equirectangular interior panorama: 360 degrees "
        f"horizontally, 180 degrees vertically, exact 2:1 layout ({PURE_RENDER_PANO_SIZE})." + source_line,
        "Image 1 is the authoritative visual anchor for the forward view. Place its composition at "
        "the horizontal midpoint of the ERP canvas (yaw 0/front). Preserve the visible room style, "
        "major furniture, materials, palette, lighting, object identity and floor appearance as "
        "closely as possible. Do not merely stretch or mirror Image 1.",
        "If Image 2 is supplied, it is the authoritative floor-material swatch. Use it only for the "
        "floor finish, tone, plank/tile character and seam appearance; do not copy it onto walls or furniture.",
        "Expand the unseen side, rear, ceiling and floor directions into one plausible continuation of "
        "the same room. Keep scale, circulation, architecture, furniture family, illumination direction "
        "and material response coherent around the full sphere. Avoid duplicated, truncated, floating "
        "or repeated objects.",
        "Use one level optical centre and one physically coherent Manhattan interior. When this ERP is "
        "viewed through a standard rectilinear VR camera, wall corners, columns, doors and windows must "
        "remain straight; orthogonal wall and ceiling edges must share stable vanishing directions. "
        "No barrel distortion, fisheye walls, bulging partitions or funhouse geometry.",
        "The floor is one Euclidean horizontal plane. Across the entire room use one global flooring "
        "coordinate system: one world-space plank direction, one fixed physical plank width and length, "
        "and one consistent scale. In rectilinear VR views all parallel plank edges must remain straight "
        "and converge to the same world vanishing direction. Never rotate or resize the laying pattern "
        "by zone; never create radial, fan-shaped, vortex, circular, curved or locally re-oriented planks. "
        "Keep floor seams restrained and low contrast so an exact deterministic material projection can "
        "be applied after generation while retaining the scene's lighting and shadows.",
        "Panorama requirements:",
        "- longitude -180 and +180 are adjacent; make their geometry, floor pattern, texture and lighting continuous",
        "- keep the horizon level and architectural verticals plausible",
        "- keep the north and south poles filled and coherent, without black holes, pinching or radial swirls",
        "- return one image only; no cubemap, 3x2 atlas, grid, split panels, fisheye frame, border, labels, text or watermark",
        "The final supplied guide is a deterministic Manhattan room-shell depth/normal proxy. Use it only as geometry control: keep its level horizon, straight verticals, wall/floor/ceiling ownership and one optical centre, while rendering the requested photorealistic appearance.",
    ]
    if context_lines:
        lines.extend(["Approved scene context:", *context_lines])
    if values.get("film_path"):
        lines.extend([
            "A manufacturer repeat-film image and a locally rendered physical-laying guide may be supplied after the floor swatch. ",
            f"The film is {value('film_width_mm')} mm wide with a {value('film_repeat_length_mm')} mm longitudinal repeat. ",
            "Treat the guide as the authoritative board cutting, repeat phase, scale and seam geometry. Do not tile the whole photograph, "
            "invent new grain, mirror boards, or move any guide seam. Preserve the product colour and grain from the film.",
        ])
    custom = value("custom_addition")
    if custom:
        lines.append(f"Additional appearance direction, subordinate to the panorama contract: {custom}")
    return "\n".join(lines)


def create_paid_preview(*, job_id: str, action: str, source_model: str,
                        source_index: int, source_hash: str, provider: str,
                        endpoint: str, model_id: str, prompt: str,
                        panorama_index: Optional[int] = None,
                         estimated_cost: Optional[float] = None,
                         quality_plan: Optional[dict] = None,
                         film_contract: Optional[dict] = None,
                         geometry_contract: Optional[dict] = None,
                         now: Optional[float] = None) -> dict:
    if action not in {"generate", "repair"}:
        raise ValueError("panorama_action_invalid")
    created_at = time.time() if now is None else float(now)
    preview_id = f"vrpreview_{secrets.token_hex(12)}"
    bound = {
        "policy": PURE_RENDER_PANO_POLICY,
        "job_id": str(job_id),
        "action": action,
        "source_model": str(source_model),
        "source_index": int(source_index),
        "source_hash": str(source_hash),
        "panorama_index": int(panorama_index) if panorama_index is not None else None,
        "provider": str(provider),
        "endpoint": str(endpoint),
        "model_id": str(model_id),
        "snapshot_locked": False,
        "output_size": PURE_RENDER_PANO_SIZE,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "quality_plan_hash": str((quality_plan or {}).get("plan_hash") or ""),
        "film_contract_hash": str(((film_contract or {}).get("manifest") or {}).get("manifest_hash") or ""),
        "film_guide_hash": str((film_contract or {}).get("guide_sha256") or ""),
        "geometry_contract_hash": str((geometry_contract or {}).get("contract_hash") or ""),
        "max_provider_calls": 1,
    }
    preview_hash = _stable_hash(bound)
    return {
        **bound,
        "preview_id": preview_id,
        "preview_hash": preview_hash,
        "prompt": prompt,
        "quality_plan": copy.deepcopy(quality_plan) if quality_plan else None,
        "film_contract": copy.deepcopy(film_contract) if film_contract else None,
        "geometry_contract": copy.deepcopy(geometry_contract) if geometry_contract else None,
        "created_at_epoch": created_at,
        "expires_at_epoch": created_at + PAID_PREVIEW_TTL_SECONDS,
        "estimated_cost": estimated_cost,
        "status": "ready",
        "claimed_at": None,
        "candidate_index": None,
        "error": "",
    }


def public_paid_preview(row: dict, *, source_thumb: str = "", source_label: str = "") -> dict:
    return {
        "policy": row.get("policy"),
        "preview_id": row.get("preview_id"),
        "preview_hash": row.get("preview_hash"),
        "expires_at": _iso_utc(float(row.get("expires_at_epoch") or 0)),
        "action": row.get("action"),
        "source": {
            "model": row.get("source_model"),
            "index": row.get("source_index"),
            "thumb": source_thumb,
            "sha256": row.get("source_hash"),
            "label": source_label,
        },
        "panorama_index": row.get("panorama_index"),
        "repair_kind": row.get("repair_kind"),
        "provider": row.get("provider"),
        "endpoint": row.get("endpoint"),
        "engine": "gpt-image-2",
        "model_id": row.get("model_id"),
        "snapshot_locked": False,
        "output_size": {"width": PURE_RENDER_PANO_WIDTH, "height": PURE_RENDER_PANO_HEIGHT},
        "max_provider_calls": 1,
        "estimated_cost": row.get("estimated_cost"),
        "quality_plan": public_quality_plan(row.get("quality_plan")),
        "film_contract": copy.deepcopy(row.get("film_contract")),
        "geometry_contract": public_geometry_contract(row.get("geometry_contract")),
        "warning": PURE_RENDER_PANO_WARNING,
    }


def validate_paid_preview(row: dict, *, preview_hash: str, job_id: str,
                          source_hash: str, now: Optional[float] = None,
                          allow_expired: bool = False) -> None:
    current_time = time.time() if now is None else float(now)
    if not isinstance(row, dict) or row.get("policy") != PURE_RENDER_PANO_POLICY:
        raise ValueError("panorama_preview_missing")
    if not allow_expired and current_time > float(row.get("expires_at_epoch") or 0):
        raise ValueError("panorama_preview_expired")
    if not hmac.compare_digest(str(row.get("preview_hash") or ""), str(preview_hash or "")):
        raise ValueError("panorama_preview_hash_mismatch")
    if not hmac.compare_digest(str(row.get("job_id") or ""), str(job_id or "")):
        raise ValueError("panorama_preview_job_mismatch")
    if not hmac.compare_digest(str(row.get("source_hash") or ""), str(source_hash or "")):
        raise ValueError("panorama_preview_source_changed")
    prompt_hash = hashlib.sha256(str(row.get("prompt") or "").encode("utf-8")).hexdigest()
    if not hmac.compare_digest(prompt_hash, str(row.get("prompt_sha256") or "")):
        raise ValueError("panorama_preview_tampered")
    quality_plan = row.get("quality_plan")
    if quality_plan:
        if not validate_quality_plan(quality_plan):
            raise ValueError("panorama_preview_tampered")
    film_contract = row.get("film_contract")
    if film_contract:
        manifest = dict(film_contract.get("manifest") or {})
        expected = str(manifest.pop("manifest_hash", "") or "")
        if not expected or not hmac.compare_digest(_stable_hash(manifest), expected):
            raise ValueError("panorama_preview_tampered")
        if not hmac.compare_digest(expected, str(row.get("film_contract_hash") or "")):
            raise ValueError("panorama_preview_tampered")
        guide_b64 = str(film_contract.get("guide_b64") or "")
        guide_hash = hashlib.sha256(base64.b64decode(guide_b64)).hexdigest() if guide_b64 else ""
        if not hmac.compare_digest(guide_hash, str(row.get("film_guide_hash") or "")):
            raise ValueError("panorama_preview_tampered")
    geometry_contract = row.get("geometry_contract")
    if geometry_contract:
        try:
            validate_geometry_contract(geometry_contract)
        except ValueError as exc:
            raise ValueError("panorama_preview_tampered") from exc
        if not hmac.compare_digest(
                str(geometry_contract.get("contract_hash") or ""),
                str(row.get("geometry_contract_hash") or "")):
            raise ValueError("panorama_preview_tampered")
    if quality_plan and not hmac.compare_digest(
            str(quality_plan.get("plan_hash") or ""),
            str(row.get("quality_plan_hash") or "")):
        raise ValueError("panorama_preview_tampered")


def _edge_map(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    return (cv2.Canny(gray, 40, 120) > 0).astype(np.uint8)


def _wrap_check(candidate: np.ndarray) -> dict:
    width = candidate.shape[1]
    seam = np.abs(candidate[:, 0].astype(np.float32) - candidate[:, -1].astype(np.float32))
    color_median = float(np.median(seam))
    color_p95 = float(np.percentile(seam, 95))
    edges = _edge_map(candidate)
    edge_diff = float(np.mean(np.abs(
        edges[:, 0].astype(np.float32) - edges[:, -1].astype(np.float32))))
    shifted = np.roll(candidate, width // 2, axis=1)
    band = max(2, int(width * .01))
    center_density = float(_edge_map(shifted)[:, width // 2 - band:width // 2 + band].mean())
    # One binary Canny column is unstable when a legitimate object contour
    # crosses the longitude seam.  Colour continuity across the same spherical
    # direction is authoritative; retain edge delta as diagnostics only.
    passed = color_median <= WRAP_SEAM_COLOR_MEDIAN_MAX and color_p95 <= 60.0
    return {
        "check_id": "wrap_seam",
        "status": "pass" if passed else "fail",
        "metric": "boundary_color_median_abs_diff",
        "value": round(color_median, 3),
        "threshold": WRAP_SEAM_COLOR_MEDIAN_MAX,
        "color_p95": round(color_p95, 3),
        "edge_boundary_diff": round(edge_diff, 5),
        "edge_boundary_threshold": WRAP_SEAM_EDGE_DIFF_MAX,
        "shifted_center_edge_density": round(center_density, 5),
        "detail": "adjacent ERP boundary columns; no geometry/reference claim",
    }


def _horizon_check(candidate: np.ndarray) -> dict:
    height, width = candidate.shape[:2]
    target_width = min(1024, width)
    scale = target_width / max(1, width)
    small = cv2.resize(
        np.clip(candidate, 0, 255).astype(np.uint8),
        (target_width, max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=60,
        minLineLength=max(30, target_width // 6), maxLineGap=12)
    angles = []
    if lines is not None:
        for line in lines[:, 0, :]:
            x1, y1, x2, y2 = (float(v) for v in line)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            normalized = ((angle + 90.0) % 180.0) - 90.0
            if abs(normalized) <= 20.0:
                angles.append(abs(normalized))
    median_angle = float(np.median(angles)) if angles else 0.0
    status = "warn" if len(angles) >= 3 and median_angle > HORIZON_ANGLE_WARN_DEG else "pass"
    return {
        "check_id": "horizon",
        "status": status,
        "metric": "median_near_horizontal_line_angle_deg",
        "value": round(median_angle, 3),
        "threshold": HORIZON_ANGLE_WARN_DEG,
        "line_count": len(angles),
        "detail": "reference-free heuristic; warning requires human review",
    }


def _pole_check(candidate: np.ndarray) -> dict:
    height = candidate.shape[0]
    band = max(2, int(height * .06))
    worst = 0.0
    metrics = {}
    for rows, label in ((slice(0, band), "north"), (slice(-band, None), "south")):
        strip = candidate[rows].astype(np.float32)
        col_delta = float(np.mean(np.abs(strip[:, 1:] - strip[:, :-1])))
        row_delta = float(np.mean(np.abs(strip[1:] - strip[:-1])))
        edge_density = float(_edge_map(candidate)[rows].mean())
        score = max(col_delta, row_delta, edge_density * 255.0)
        worst = max(worst, score)
        metrics[label] = {
            "column_delta": round(col_delta, 3),
            "row_delta": round(row_delta, 3),
            "edge_density": round(edge_density, 5),
        }
    return {
        "check_id": "poles",
        "status": "warn" if worst > POLE_NEIGHBOR_MEAN_WARN else "pass",
        "metric": "pole_neighbor_variation",
        "value": round(worst, 3),
        "threshold": POLE_NEIGHBOR_MEAN_WARN,
        "poles": metrics,
        "detail": "reference-free heuristic; warning requires human review",
    }


def _architecture_views_check(image: Image.Image) -> dict:
    """Robust Manhattan/curve gate evaluated only in rectilinear VR views."""
    report = analyze_panorama_architecture(image)
    rows = []
    failure_views = []
    warning_views = []
    worst_view = ""
    worst_score = -1.0
    for row in report.get("views") or []:
        yaw = int(row.get("yaw_deg") or 0)
        vertical = float(row.get("vertical_p90_deviation_deg") or 0.0)
        curve = float(row.get("horizontal_curve_p90_px") or 0.0)
        if vertical > 6.0 or curve > 8.0:
            status = "fail"
            failure_views.append(yaw)
        elif vertical > 3.5 or curve > 4.0:
            status = "warn"
            warning_views.append(yaw)
        else:
            status = "pass"
        score = max(vertical / 6.0, curve / 8.0)
        if score > worst_score:
            worst_score, worst_view = score, f"yaw={yaw},pitch=0"
        rows.append({**row, "status": status})
    status = ("fail" if report.get("status") == "rejected"
              else "warn" if report.get("status") == "rectify_recommended" else "pass")
    return {
        "check_id": "architecture_views",
        "status": status,
        "metric": "rectilinear_vertical_line_p90_deviation_deg",
        "value": round(float(report.get("worst_vertical_p90_deg") or 0.0), 3),
        "horizontal_curve_p90_px": round(float(report.get("worst_horizontal_curve_p90_px") or 0.0), 3),
        "threshold": 6.0,
        "warning_threshold": 3.5,
        "worst_view": worst_view,
        "failure_yaws": failure_views,
        "warning_yaws": warning_views,
        "views": rows,
        "detail": "8 level rectilinear views; robust dominant vertical mode plus horizontal centreline curvature",
    }


def gate_visual_pano(candidate_path: str, *, expected_width: int = PURE_RENDER_PANO_WIDTH,
                     expected_height: int = PURE_RENDER_PANO_HEIGHT) -> dict:
    try:
        with Image.open(candidate_path) as source:
            source.load()
            image = source.convert("RGB")
    except Exception as ex:
        return {
            "version": PURE_RENDER_PANO_GATE_VERSION,
            "status": "failed",
            "gate_pass": False,
            "hard_fail": True,
            "checks": [],
            "failures": ["decode"],
            "warnings": [],
            "summary": f"decode:fail ({ex})"[:1000],
        }

    width, height = image.size
    size_ok = width == expected_width and height == expected_height and width == height * 2
    size_check = {
        "check_id": "size_contract",
        "status": "pass" if size_ok else "fail",
        "metric": "exact_erp_size",
        "value": f"{width}x{height}",
        "threshold": f"{expected_width}x{expected_height}",
        "detail": "strict decode + exact dimensions + 2:1 projection contract",
    }
    if not size_ok:
        return {
            "version": PURE_RENDER_PANO_GATE_VERSION,
            "status": "failed",
            "gate_pass": False,
            "hard_fail": True,
            "checks": [size_check],
            "failures": ["size_contract"],
            "warnings": [],
            "summary": "size_contract:fail",
        }

    candidate = np.asarray(image, dtype=np.float32)
    checks = [
        size_check,
        _wrap_check(candidate),
        _horizon_check(candidate),
        _pole_check(candidate),
        _architecture_views_check(image),
    ]
    repair_failed = any(
        row["status"] == "fail" and row["check_id"] in {"wrap_seam", "architecture_views"}
        for row in checks)
    warnings = [row["check_id"] for row in checks if row["status"] == "warn"]
    failures = [row["check_id"] for row in checks if row["status"] == "fail"]
    status = "repair_recommended" if repair_failed else "passed"
    return {
        "version": PURE_RENDER_PANO_GATE_VERSION,
        "status": status,
        "gate_pass": status == "passed",
        "hard_fail": False,
        "geometry_locked": False,
        "delivery_scope": "ai_expanded_single_hotspot",
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "summary": "; ".join(f"{row['check_id']}:{row['status']}" for row in checks),
    }


def pure_render_review_status(gate: dict, checklist: dict) -> str:
    values = {key: str(checklist.get(key) or "") for key in PURE_RENDER_REVIEW_ITEMS}
    if any(value == "fail" for value in values.values()):
        return "rejected"
    if gate.get("status") == "passed" and all(value == "pass" for value in values.values()):
        return "accepted"
    return "needs_review"


def build_architecture_repair_mask(gate: dict, width: int = PURE_RENDER_PANO_WIDTH,
                                   height: int = PURE_RENDER_PANO_HEIGHT) -> Image.Image:
    """Build conservative ERP bands around rectilinear views that failed line QA.

    The lower 38% of the panorama is protected so a later deterministic floor
    pass remains the only operation allowed to rewrite the flooring geometry.
    """
    row = next((item for item in (gate.get("checks") or [])
                if item.get("check_id") == "architecture_views"), {})
    yaws = list(row.get("failure_yaws") or row.get("warning_yaws") or [])
    if not yaws:
        return Image.new("L", (width, height), 0)
    mask = np.zeros((height, width), dtype=np.uint8)
    xs = np.arange(width, dtype=np.float32)
    longitude = (xs / float(width) - 0.5) * 360.0
    for yaw in yaws:
        delta = np.abs(((longitude - float(yaw) + 180.0) % 360.0) - 180.0)
        mask[:round(height * 0.62), delta <= 52.0] = 255
    kernel_size = max(5, round(width * 0.006) | 1)
    mask = cv2.dilate(
        mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
        iterations=1)
    return Image.fromarray(mask, "L")


def build_architecture_repair_prompt() -> str:
    return (
        "Repair only the white masked wall, column, doorway, window and ceiling regions of this exact "
        "3840x1920 monoscopic equirectangular panorama. In every standard rectilinear VR view, make "
        "architectural verticals straight and make orthogonal wall/ceiling edges share coherent Manhattan "
        "vanishing directions from one unchanged optical centre. Remove barrel, fisheye, bulging-wall and "
        "funhouse deformation. Preserve every unmasked pixel exactly. Do not move furniture, change room "
        "identity, alter the horizon, repaint the floor, change lighting, crop, add borders or output a "
        "cubemap. Return one complete 3840x1920 ERP image only."
    )


__all__ = [
    "PAID_PREVIEW_TTL_SECONDS", "PURE_RENDER_PANO_GATE_VERSION",
    "PURE_RENDER_PANO_HEIGHT", "PURE_RENDER_PANO_POLICY", "PURE_RENDER_PANO_SIZE",
    "PURE_RENDER_PANO_WARNING", "PURE_RENDER_PANO_WIDTH", "PURE_RENDER_REVIEW_ITEMS",
    "build_architecture_repair_mask", "build_architecture_repair_prompt",
    "build_pure_render_pano_prompt", "create_paid_preview", "file_sha256",
    "gate_visual_pano", "public_paid_preview", "pure_render_review_status",
    "validate_paid_preview",
]
