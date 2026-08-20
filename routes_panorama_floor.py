# -*- coding: utf-8 -*-
"""Local spherical floor correction for generated ERP panorama candidates."""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from PIL import Image, ImageOps

from . import server_state as state
from .config import MAIN_OUTPUT_DIR, logger
from .models import add_model_candidate, compute_runs_final_status, update_job
from .pure_render_pano import file_sha256, gate_visual_pano
from .records import api_write_to_record, save_api_result_png, update_result_panorama_metadata
from .floor_renderer import image_sha256
from .film_repeat_floor import analyze_film_repeat, parse_plank_dimensions
from .routes_panorama import (
    _job_or_404,
    _panorama_candidate,
    _record_context,
    _set_candidate_meta,
)
from .server_helpers import job_view, require_ref_image_path, to_url
from .server_schemas import (
    FloorVisualizeTarget,
    PanoramaFloorPrepareRequest,
    PanoramaFloorRecordPrepareRequest,
    PanoramaFloorRecordRenderRequest,
    PanoramaFloorRenderRequest,
)
from .routes_tools import _resolve_floor_source
from .spherical_floor_renderer import (
    SPHERICAL_FLOOR_MASK_VERSION,
    SphericalFloorRecipe,
    combine_view_masks,
    encode_png_b64,
    prepare_floor_mask_views,
    render_spherical_floor,
)


router = APIRouter()
PREVIEW_MAX_SIDE = 960


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _texture_path(job) -> str:
    path = os.path.realpath(str(job.png_path or ""))
    if not path or not os.path.isfile(path):
        raise HTTPException(409, "当前任务缺少可用的原始地板小样")
    return path


def _film_from_context(context: dict) -> tuple[Image.Image | None, dict | None]:
    params = context.get("params") if isinstance(context.get("params"), dict) else {}
    film_path = str(params.get("film_path") or "").strip()
    if not film_path:
        return None, None
    film_path = require_ref_image_path(film_path)
    width_mm = params.get("film_width_mm")
    repeat_mm = params.get("film_repeat_length_mm")
    plank_width, plank_length = parse_plank_dimensions(str(params.get("floor_size") or ""))
    if width_mm is None or repeat_mm is None or plank_width is None or plank_length is None:
        raise ValueError("原厂彩膜需要彩膜宽度、重复周期和可解析的板材尺寸")
    with Image.open(film_path) as source:
        source.load()
        film = ImageOps.exif_transpose(source).convert("RGB").copy()
    manifest = analyze_film_repeat(
        film,
        film_width_mm=float(width_mm),
        repeat_length_mm=float(repeat_mm),
        plank_width_mm=float(plank_width),
        plank_length_mm=float(plank_length),
        slit_origin_mm=params.get("film_slit_origin_mm"),
        seam_type=str(params.get("seam_type") or ""),
        floor_size=str(params.get("floor_size") or ""),
    )
    return film, manifest


def _parse_plank_dimensions(context: dict) -> tuple[float | None, float | None]:
    params = context.get("params") if isinstance(context.get("params"), dict) else {}
    text = str(params.get("floor_size") or "")
    values = [float(value) for value in re.findall(r"(?<!\d)(\d{2,5}(?:\.\d+)?)", text)]
    values = [value for value in values if 20 <= value <= 10000]
    if len(values) < 2:
        return None, None
    return min(values[0], values[1]), max(values[0], values[1])


def _recipe_defaults_from_context(context: dict, texture: Image.Image) -> dict:
    plank_width, plank_length = _parse_plank_dimensions(context)
    physical_width = float(plank_length or 2000.0)
    physical_height = physical_width * texture.height / max(1, texture.width)
    return {
        "camera_height_m": 1.55,
        "rotation_deg": 90.0,
        "scale": 1.0,
        "offset_x": 0.0,
        "offset_z": 0.0,
        "texture_width_mm": round(physical_width, 3),
        "texture_height_mm": round(max(50.0, physical_height), 3),
        "plank_width_mm": plank_width,
        "plank_length_mm": plank_length,
        "illumination_strength": 0.65,
        "shadow_strength": 0.85,
        "contact_shadow_strength": 0.35,
        "feather": 0.006,
    }


def _recipe_defaults(job, texture: Image.Image) -> dict:
    return _recipe_defaults_from_context(_record_context(job), texture)


def _apply_geometry_defaults(defaults: dict, parent: dict) -> dict:
    values = dict(defaults)
    contract = dict(parent.get("geometry_contract") or {})
    camera = dict(contract.get("camera") or {})
    floor_frame = dict(contract.get("floor_frame") or {})
    if camera.get("camera_height_m") is not None:
        values["camera_height_m"] = float(camera["camera_height_m"])
    if floor_frame.get("plank_direction_deg") is not None:
        values["rotation_deg"] = float(floor_frame["plank_direction_deg"])
    if floor_frame.get("origin_x_m") is not None:
        values["offset_x"] = float(floor_frame["origin_x_m"])
    if floor_frame.get("origin_z_m") is not None:
        values["offset_z"] = float(floor_frame["origin_z_m"])
    return values


def _recipe_from_request(req: PanoramaFloorRenderRequest) -> SphericalFloorRecipe:
    values = req.recipe.model_dump()
    return SphericalFloorRecipe(**values)


def _decode_view_masks(req: PanoramaFloorRenderRequest) -> list[dict]:
    return [{"id": item.id, "mask_b64": item.mask_b64} for item in req.view_masks]


def _source_and_parent(job, panorama_index: int):
    source_path, parent = _panorama_candidate(job, panorama_index)
    if parent.get("projection") != "equirectangular":
        raise HTTPException(409, "球面地板校正只支持 2:1 ERP 全景")
    return source_path, parent


def _gate_floor_output(output: Image.Image, floor_meta: dict) -> dict:
    fd, gate_path = tempfile.mkstemp(prefix=".spherical_floor_gate_", suffix=".png",
                                     dir=MAIN_OUTPUT_DIR)
    os.close(fd)
    try:
        output.save(gate_path, format="PNG")
        gate = gate_visual_pano(gate_path)
    finally:
        try:
            os.remove(gate_path)
        except OSError:
            pass
    floor_ok = bool(floor_meta.get("outside_mask_byte_identical"))
    floor_check = {
        "check_id": "spherical_floor_projection",
        "status": "pass" if floor_ok else "fail",
        "metric": "single_world_plane_and_outside_mask_identity",
        "value": round(float(floor_meta.get("mask_coverage") or 0.0), 6),
        "threshold": "single horizontal plane; outside mask byte-identical",
        "renderer": floor_meta.get("model"),
        "detail": "deterministic ERP ray/plane intersection; no perspective homography",
    }
    gate["checks"] = list(gate.get("checks") or []) + [floor_check]
    if not floor_ok:
        gate["failures"] = list(dict.fromkeys(list(gate.get("failures") or []) + [
            "spherical_floor_projection"]))
        gate["status"] = "failed"
        gate["gate_pass"] = False
        gate["hard_fail"] = True
    gate["summary"] = "; ".join(
        f"{row.get('check_id')}:{row.get('status')}" for row in gate["checks"])
    return gate


@router.post("/api/jobs/{jid}/panorama/floor/prepare")
async def prepare_panorama_floor(jid: str, req: PanoramaFloorPrepareRequest):
    job = _job_or_404(jid)
    if job.status in ("queued", "running") or job.pro_polishing or job.operation_status == "running":
        raise HTTPException(409, "任务正在处理，请稍后再校正地板")
    source_path, parent = _source_and_parent(job, req.panorama_index)
    texture_path = _texture_path(job)

    def run():
        with Image.open(source_path) as source:
            source.load()
            erp = ImageOps.exif_transpose(source).convert("RGB").copy()
        with Image.open(texture_path) as source:
            source.load()
            texture = ImageOps.exif_transpose(source).convert("RGB").copy()
        views = prepare_floor_mask_views(erp, cache_key=file_sha256(source_path)[:24])
        return views, _apply_geometry_defaults(_recipe_defaults(job, texture), parent), texture.size

    try:
        views, defaults, texture_size = await asyncio.to_thread(run)
    except Exception as ex:
        logger.exception("[球面地板] 自动遮罩准备失败")
        raise HTTPException(500, f"自动地板识别失败：{ex}") from ex
    warnings = [
        "自动遮罩可能把木色柜体、桌面、花盆或窗框识别为地板；保存前请逐个方向检查红色区域。",
        "当前版本按一个水平地板平面投影；楼梯、台阶和室外地面请从遮罩中擦除。",
    ]
    for view in views:
        warnings.extend(str(value) for value in (view.get("warnings") or []) if value)
    return {
        "projection": "equirectangular",
        "mask_version": SPHERICAL_FLOOR_MASK_VERSION,
        "panorama_index": req.panorama_index,
        "source_sha256": file_sha256(source_path),
        "texture_size": list(texture_size),
        "parent_gate_status": (parent.get("gate") or {}).get("status"),
        "views": views,
        "defaults": defaults,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _run_preview(job, req: PanoramaFloorRenderRequest):
    source_path, _ = _source_and_parent(job, req.panorama_index)
    if file_sha256(source_path) != req.source_sha256:
        raise HTTPException(409, "全景候选已经变化，请重新识别地板")
    texture_path = _texture_path(job)
    with Image.open(source_path) as source:
        source.load()
        erp = ImageOps.exif_transpose(source).convert("RGB").copy()
    with Image.open(texture_path) as source:
        source.load()
        texture = ImageOps.exif_transpose(source).convert("RGB").copy()
    film, film_manifest = _film_from_context(_record_context(job))
    preview_width = min(PREVIEW_MAX_SIDE, erp.width)
    preview_height = max(1, round(erp.height * preview_width / erp.width))
    mask = combine_view_masks(_decode_view_masks(req), preview_width, preview_height)
    output, metadata = render_spherical_floor(
        erp, texture, mask, _recipe_from_request(req), max_side=PREVIEW_MAX_SIDE,
        film_image=film, film_manifest=film_manifest)
    buffer = io.BytesIO()
    output.save(buffer, format="PNG", optimize=True)
    return {
        "preview": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "mask_b64": encode_png_b64(mask),
        "width": output.width,
        "height": output.height,
        "warnings": metadata.get("warnings") or [],
        "metadata": metadata,
    }


@router.post("/api/jobs/{jid}/panorama/floor/preview")
async def preview_panorama_floor(jid: str, req: PanoramaFloorRenderRequest):
    job = _job_or_404(jid)
    try:
        return await asyncio.to_thread(_run_preview, job, req)
    except HTTPException:
        raise
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from ex
    except Exception as ex:
        logger.exception("[球面地板] 预览失败")
        raise HTTPException(500, f"球面地板预览失败：{ex}") from ex


@router.post("/api/jobs/{jid}/panorama/floor/apply")
async def apply_panorama_floor(jid: str, req: PanoramaFloorRenderRequest):
    job = _job_or_404(jid)
    if job.status in ("queued", "running") or job.pro_polishing or job.operation_status == "running":
        raise HTTPException(409, "任务正在处理，请稍后再保存地板校正")
    source_path, parent = _source_and_parent(job, req.panorama_index)
    if file_sha256(source_path) != req.source_sha256:
        raise HTTPException(409, "全景候选已经变化，请重新识别地板")
    texture_path = _texture_path(job)

    def render():
        with Image.open(source_path) as source:
            source.load()
            erp = ImageOps.exif_transpose(source).convert("RGB").copy()
        with Image.open(texture_path) as source:
            source.load()
            texture = ImageOps.exif_transpose(source).convert("RGB").copy()
        film, film_manifest = _film_from_context(_record_context(job))
        mask = combine_view_masks(_decode_view_masks(req), erp.width, erp.height)
        output, floor_meta = render_spherical_floor(
            erp, texture, mask, _recipe_from_request(req),
            film_image=film, film_manifest=film_manifest)
        gate = _gate_floor_output(output, floor_meta)
        return output, floor_meta, gate

    try:
        output, floor_meta, gate = await asyncio.to_thread(render)
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from ex
    except Exception as ex:
        logger.exception("[球面地板] 4K 渲染失败")
        raise HTTPException(500, f"球面地板渲染失败：{ex}") from ex

    panorama_meta = dict(parent)
    panorama_meta.update({
        "schema_version": 2,
        "projection": "equirectangular",
        "width": output.width,
        "height": output.height,
        "source_sha256": req.source_sha256,
        "parent_panorama_index": req.panorama_index,
        "floor_correction": {
            **floor_meta,
            "status": "needs_review",
            "mask_version": SPHERICAL_FLOOR_MASK_VERSION,
            "applied_at": _utc_now(),
        },
        "gate": gate,
        "review": {"status": "needs_review", "checklist": {}, "reviewed_at": ""},
        "generated_at": _utc_now(),
    })
    metadata = {
        "projection": "equirectangular",
        "generation_route": str(parent.get("generation_route") or "ai_expanded_erp"),
        "panorama": panorama_meta,
    }
    output_path = await asyncio.to_thread(
        save_api_result_png, output, "VR360_球面地板校正", source_path, metadata)
    if not output_path:
        raise HTTPException(500, "球面地板结果保存失败")
    candidate_index = add_model_candidate(job, "vr360", output_path, metadata)
    source_result_id = str(parent.get("record_result_id") or "") or None
    record_result_id = ""
    if job.json_path and job.record_id:
        record_result_id = await asyncio.to_thread(
            api_write_to_record, output, "360° VR · 球面地板校正", job.json_path,
            job.record_id, output_path, metadata, source_result_id,
            "本地球面射线投影地板（3840×1920）") or ""
    panorama_meta["record_result_id"] = record_result_id
    _set_candidate_meta(job, "vr360", candidate_index, metadata)
    update_model_run(
        job, "vr360",
        delivery_status=str(gate.get("status") or "needs_review"),
    )

    latest_parent = dict(parent)
    latest_parent["floor_correction_result_index"] = candidate_index
    _set_candidate_meta(job, "vr360", req.panorama_index, {
        "projection": "equirectangular", "panorama": latest_parent})
    parent_result_id = str(latest_parent.get("record_result_id") or "")
    if parent_result_id:
        await asyncio.to_thread(
            update_result_panorama_metadata, job.json_path, job.record_id,
            parent_result_id, latest_parent)
    update_job(job, status=compute_runs_final_status(job))
    state.JOBS.persist()
    logger.info("[球面地板] 已保存 job=%s parent=%s candidate=%s path=%s",
                job.job_id, req.panorama_index, candidate_index, output_path)
    return {
        "ok": True,
        "job": job_view(job),
        "candidate_index": candidate_index,
        "warnings": floor_meta.get("warnings") or [],
        "metadata": metadata,
    }


def _record_target(req: PanoramaFloorRecordPrepareRequest) -> FloorVisualizeTarget:
    return FloorVisualizeTarget(
        kind="record", json_path=req.json_path, record_id=req.record_id,
        result_id=req.result_id)


def _record_source(req: PanoramaFloorRecordPrepareRequest):
    scene, context = _resolve_floor_source(_record_target(req))
    if scene.width != scene.height * 2:
        raise HTTPException(409, "所选历史结果不是完整的 2:1 ERP 全景")
    texture_path = require_ref_image_path(req.texture_path)
    with Image.open(texture_path) as source:
        source.load()
        texture = ImageOps.exif_transpose(source).convert("RGB").copy()
    result = next((row for row in (context["record"].get("results") or [])
                   if row.get("result_id") == req.result_id), {})
    parent = ((result.get("generation_metadata") or {}).get("panorama") or {})
    return scene, texture, context, dict(parent)


@router.post("/api/records/panorama/floor/prepare")
async def prepare_record_panorama_floor(req: PanoramaFloorRecordPrepareRequest):
    try:
        scene, texture, context, parent = await asyncio.to_thread(_record_source, req)
        views = await asyncio.to_thread(
            prepare_floor_mask_views, scene, cache_key=image_sha256(scene)[:24])
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("[球面地板] 历史记录遮罩准备失败")
        raise HTTPException(500, f"自动地板识别失败：{ex}") from ex
    warnings = [
        "自动遮罩可能把木色柜体、桌面、花盆或窗框识别为地板；保存前请逐个方向检查红色区域。",
        "当前版本按一个水平地板平面投影；楼梯、台阶和室外地面请从遮罩中擦除。",
    ]
    gen_context = context["record"].get("gen_context") or {}
    return {
        "projection": "equirectangular",
        "mask_version": SPHERICAL_FLOOR_MASK_VERSION,
        "panorama_index": 0,
        "source_sha256": image_sha256(scene),
        "texture_size": list(texture.size),
        "parent_gate_status": (parent.get("gate") or {}).get("status"),
        "views": views,
        "defaults": _apply_geometry_defaults(
            _recipe_defaults_from_context(gen_context, texture), parent),
        "warnings": warnings,
    }


def _record_render_request(req: PanoramaFloorRecordRenderRequest):
    scene, texture, context, parent = _record_source(req)
    if image_sha256(scene) != req.source_sha256:
        raise HTTPException(409, "历史全景已经变化，请重新识别地板")
    view_masks = [{"id": item.id, "mask_b64": item.mask_b64} for item in req.view_masks]
    recipe = SphericalFloorRecipe(**req.recipe.model_dump())
    film, film_manifest = _film_from_context(context["record"].get("gen_context") or {})
    return scene, texture, context, parent, view_masks, recipe, film, film_manifest


@router.post("/api/records/panorama/floor/preview")
async def preview_record_panorama_floor(req: PanoramaFloorRecordRenderRequest):
    try:
        scene, texture, _, _, view_masks, recipe, film, film_manifest = await asyncio.to_thread(
            _record_render_request, req)
        width = min(PREVIEW_MAX_SIDE, scene.width)
        height = max(1, round(scene.height * width / scene.width))
        mask = await asyncio.to_thread(combine_view_masks, view_masks, width, height)
        output, metadata = await asyncio.to_thread(
            render_spherical_floor, scene, texture, mask, recipe, max_side=PREVIEW_MAX_SIDE,
            film_image=film, film_manifest=film_manifest)
    except HTTPException:
        raise
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from ex
    buffer = io.BytesIO()
    output.save(buffer, format="PNG", optimize=True)
    return {
        "preview": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "mask_b64": encode_png_b64(mask),
        "width": output.width,
        "height": output.height,
        "warnings": metadata.get("warnings") or [],
        "metadata": metadata,
    }


@router.post("/api/records/panorama/floor/apply")
async def apply_record_panorama_floor(req: PanoramaFloorRecordRenderRequest):
    try:
        scene, texture, context, parent, view_masks, recipe, film, film_manifest = await asyncio.to_thread(
            _record_render_request, req)
        mask = await asyncio.to_thread(combine_view_masks, view_masks, scene.width, scene.height)
        output, floor_meta = await asyncio.to_thread(
            render_spherical_floor, scene, texture, mask, recipe,
            film_image=film, film_manifest=film_manifest)
        gate = await asyncio.to_thread(_gate_floor_output, output, floor_meta)
    except HTTPException:
        raise
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from ex
    panorama_meta = dict(parent)
    panorama_meta.update({
        "schema_version": 2,
        "projection": "equirectangular",
        "width": output.width,
        "height": output.height,
        "source_sha256": req.source_sha256,
        "parent_record_result_id": req.result_id,
        "floor_correction": {
            **floor_meta,
            "status": "needs_review",
            "mask_version": SPHERICAL_FLOOR_MASK_VERSION,
            "applied_at": _utc_now(),
        },
        "gate": gate,
        "review": {"status": "needs_review", "checklist": {}, "reviewed_at": ""},
        "generated_at": _utc_now(),
    })
    metadata = {
        "projection": "equirectangular",
        "generation_route": str(parent.get("generation_route") or "ai_expanded_erp"),
        "panorama": panorama_meta,
    }
    output_path = await asyncio.to_thread(
        save_api_result_png, output, "VR360_球面地板校正", context["source_path"], metadata)
    if not output_path:
        raise HTTPException(500, "球面地板结果保存失败")
    result_id = await asyncio.to_thread(
        api_write_to_record, output, "360° VR · 球面地板校正", context["json_path"],
        req.record_id, output_path, metadata, req.result_id,
        "本地球面射线投影地板（3840×1920）")
    if not result_id:
        raise HTTPException(500, "球面地板结果写入历史记录失败")
    return {
        "ok": True,
        "result_id": result_id,
        "result_url": to_url(output_path),
        "warnings": floor_meta.get("warnings") or [],
        "metadata": metadata,
    }


__all__ = ["router"]
