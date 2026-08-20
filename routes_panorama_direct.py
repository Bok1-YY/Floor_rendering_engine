# -*- coding: utf-8 -*-
"""Paid direct cubemap-atlas → deterministic ERP panorama workflow."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import mimetypes
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from PIL import Image

from . import server_state as state
from .api import _call_fal_queue_json, _fal_image_from_result
from .config import (
    FAL_GPT_IMAGE_2_ENDPOINT,
    GPT_IMAGE_2_MODEL,
    MAIN_OUTPUT_DIR,
    get_usage_prices,
    get_omakase_gemini_model,
    load_config,
    logger,
)
from .panorama_quality_planner import DIRECT_ROUTE, plan_panorama_quality
from .models import (
    TaskParams,
    add_model_candidate,
    ensure_model_runs,
    new_job,
    task_params_to_kwargs,
    update_job,
    update_model_run,
)
from .film_repeat_floor import build_film_contract
from .panorama_film_material import apply_manufacturer_film
from .panorama_local_geometry import (
    build_cubemap_geometry_contract,
    build_geometry_contract,
    lock_registered_source_view,
    rectify_panorama_architecture,
    register_source_to_erp,
)
from .prompts import save_task_files_html
from .pure_render_pano import (
    PURE_RENDER_PANO_HEIGHT,
    PURE_RENDER_PANO_WIDTH,
    file_sha256,
    gate_visual_pano,
)
from .pure_render_pano_atlas import (
    DIRECT_ATLAS_HEIGHT,
    DIRECT_ATLAS_WIDTH,
    DIRECT_PANO_ROUTE,
    build_atlas_template,
    build_room_geometry_guide,
    combine_direct_gates,
    compose_and_gate_atlas,
    create_direct_paid_preview,
    public_direct_paid_preview,
    validate_direct_paid_preview,
)
from .records import api_write_to_record, attach_generation_context, save_api_result_png
from .server_helpers import job_view, require_upload_image_path, thumb_url
from .server_schemas import DirectPanoramaCommitRequest, DirectPanoramaPreviewRequest
from .usage_stats import record_usage
from .whole_home_pano_render import erp_to_cube


router = APIRouter()
B2_DIRECT_ENDPOINT = "fal-ai/nano-banana-2/edit"
B2_DIRECT_MODEL = "gemini-3.1-flash-image"

_PREVIEWS: dict[str, dict] = {}
_PREVIEW_LOCK = threading.Lock()
_HANDLE_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _engines(cfg: dict) -> list[dict]:
    return [
        {
            "key": "b2_atlas",
            "label": "B2 六面图集",
            "provider": "fal",
            "endpoint": str((cfg.get("fal_model_map") or {}).get(B2_DIRECT_MODEL)
                            or B2_DIRECT_ENDPOINT),
            "model_id": B2_DIRECT_MODEL,
        },
        {
            "key": "gpt_atlas",
            "label": "GPT Image 2 六面图集",
            "provider": "fal",
            "endpoint": str(cfg.get("fal_gpt_image_endpoint")
                            or FAL_GPT_IMAGE_2_ENDPOINT),
            "model_id": GPT_IMAGE_2_MODEL,
        },
    ]


def _estimated_costs() -> dict:
    prices = get_usage_prices()
    return {
        "b2_atlas": prices.get("B2:fal", prices.get("B2")),
        "gpt_atlas": prices.get("VR360:fal", prices.get("VR360")),
    }


def _trim_previews(now: float) -> None:
    expired = [key for key, row in _PREVIEWS.items()
               if now > float(row.get("expires_at_epoch") or 0) + 3600]
    for key in expired:
        _PREVIEWS.pop(key, None)
    if len(_PREVIEWS) > 40:
        ordered = sorted(_PREVIEWS.items(), key=lambda item: float(item[1].get("created_at_epoch") or 0))
        for key, _ in ordered[:len(_PREVIEWS) - 40]:
            _PREVIEWS.pop(key, None)


def _find_committed_preview(preview_id: str) -> tuple[object | None, dict | None]:
    for job in state.JOBS.snapshot():
        row = (job.panorama_previews or {}).get(preview_id)
        if isinstance(row, dict):
            return job, row
    return None, None


def _preview_or_404(preview_id: str) -> tuple[object | None, dict]:
    with _PREVIEW_LOCK:
        row = _PREVIEWS.get(preview_id)
        if isinstance(row, dict):
            return None, row
    job, row = _find_committed_preview(preview_id)
    if not isinstance(row, dict):
        raise HTTPException(404, "球面效果图付费预览不存在，请重新预览")
    return job, row


def _file_data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as handle:
        payload = base64.b64encode(handle.read()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _direct_handles(job, preview_id: str) -> dict:
    ensure_model_runs(job)
    settings = dict((job.model_runs.get("vr360") or {}).get("settings") or {})
    rows = settings.get("direct_queue_handles") or {}
    handles = rows.get(preview_id) if isinstance(rows, dict) else None
    return copy.deepcopy(handles) if isinstance(handles, dict) else {}


def _set_direct_handle(job, preview_id: str, engine_key: str, handle) -> None:
    with _HANDLE_LOCK:
        run = update_model_run(job, "vr360")
        settings = dict(run.get("settings") or {})
        rows = copy.deepcopy(settings.get("direct_queue_handles") or {})
        handles = dict(rows.get(preview_id) or {})
        if handle:
            handles[engine_key] = dict(handle)
        else:
            handles.pop(engine_key, None)
        if handles:
            rows[preview_id] = handles
        else:
            rows.pop(preview_id, None)
        settings["direct_queue_handles"] = rows
        update_model_run(job, "vr360", settings=settings)
        state.JOBS.persist()


def _terminal_fal_error(error: str) -> bool:
    text = str(error or "").upper()
    return "FAL 队列任务FAILED" in text or "FAL 队列任务CANCELLED" in text


def _preview_error(error: ValueError) -> HTTPException:
    code = str(error)
    messages = {
        "direct_panorama_preview_missing": "球面效果图付费预览不存在",
        "direct_panorama_preview_expired": "球面效果图付费预览已过期，请重新确认",
        "direct_panorama_preview_hash_mismatch": "球面效果图付费预览校验失败",
        "direct_panorama_source_changed": "地板小样已经变化，请重新预览",
        "direct_panorama_preview_tampered": "球面效果图付费预览内容已被修改",
    }
    return HTTPException(409, {"code": code, "message": messages.get(code, code)})


@router.post("/api/jobs/panorama-direct/preview")
def preview_direct_panorama(req: DirectPanoramaPreviewRequest):
    if "球面效果图" not in str(req.params.workflow_mode or ""):
        raise HTTPException(422, "请选择“球面效果图”工作流")
    source_path = require_upload_image_path(req.image_path, "地板小样", required=True)
    room_reference_path = (require_upload_image_path(
        req.room_reference_path, "空间参考效果图", required=False) or ""
        if req.room_reference_path else "")
    if req.params.film_path:
        req.params.film_path = require_upload_image_path(req.params.film_path, "原厂彩膜") or ""
    cfg = load_config()
    if not str(cfg.get("fal_api_key") or "").strip():
        raise HTTPException(400, "球面效果图需要先配置 Fal API Key")
    source_hash = file_sha256(source_path)
    params = req.params.model_dump()
    quality_plan = plan_panorama_quality(
        route=DIRECT_ROUTE,
        source_path=source_path,
        source_hash=source_hash,
        params=params,
        api_key=str(cfg.get("gemini_api_key") or ""),
        model=get_omakase_gemini_model(),
    )
    film_contract = None
    if params.get("film_path"):
        film_path = require_upload_image_path(str(params.get("film_path") or ""), "原厂彩膜", required=True)
        film_contract, _ = build_film_contract(film_path, params, guide_size=512)
    geometry_contract = build_cubemap_geometry_contract(params)
    room_reference_hash = ""
    if room_reference_path:
        room_reference_hash = file_sha256(room_reference_path)
        geometry_contract = build_geometry_contract(
            room_reference_path, params, reference_role="direct_room_reference")
    row = create_direct_paid_preview(
        source_path=source_path,
        source_hash=source_hash,
        params=params,
        engines=_engines(cfg),
        estimated_costs=_estimated_costs(),
        quality_plan=quality_plan,
        film_contract=film_contract,
        geometry_contract=geometry_contract,
        room_reference_path=room_reference_path,
        room_reference_hash=room_reference_hash,
    )
    with _PREVIEW_LOCK:
        _trim_previews(time.time())
        _PREVIEWS[row["preview_id"]] = row
    return public_direct_paid_preview(row, source_thumb=thumb_url(source_path))


@router.post("/api/jobs/panorama-direct/commit")
async def commit_direct_panorama(req: DirectPanoramaCommitRequest):
    existing_job, preview = _preview_or_404(req.preview_id)
    source_path = require_upload_image_path(
        str(preview.get("source_path") or ""), "地板小样", required=True)
    try:
        validate_direct_paid_preview(
            preview,
            preview_hash=req.preview_hash,
            source_hash=file_sha256(source_path),
            allow_expired=str(preview.get("status") or "") != "ready",
        )
    except ValueError as ex:
        raise _preview_error(ex) from ex

    if existing_job is not None:
        job = existing_job
        status = str(preview.get("status") or "")
        if status in {"succeeded", "failed", "cancelled"}:
            return job_view(job)
        if status == "running" and job.operation_status == "running":
            return job_view(job)
        handles = _direct_handles(job, req.preview_id)
        unfinished = [engine["key"] for engine in (preview.get("engines") or [])
                      if (preview.get("branches") or {}).get(engine["key"], {}).get("status") != "succeeded"]
        if any(not handles.get(key) for key in unfinished):
            raise HTTPException(409, {
                "code": "direct_panorama_paid_submission_ambiguous",
                "message": "请求曾被认领但有分支缺少可恢复队列句柄；为避免重复计费不会再次提交",
            })
        resume = True
    else:
        resume = False
        params = dict(preview.get("params") or {})
        room_label = str(params.get("cn_room_type") if params.get("cn_mode") else params.get("room_type") or "空间")
        job = new_job(f"{os.path.basename(source_path)} · {room_label} · 球面效果图",
                      time.strftime("%H:%M:%S"), "custom")
        job.workflow_mode = str(params.get("workflow_mode") or "球面效果图")
        job.delivery_mode = DIRECT_PANO_ROUTE
        job.model_targets = ["vr360"]
        job.panorama_previews = {req.preview_id: copy.deepcopy(preview)}
        preview = job.panorama_previews[req.preview_id]
        preview["job_id"] = job.job_id
        preview["status"] = "claimed"
        preview["claimed_at"] = time.time()
        preview["branches"] = {
            engine["key"]: {"status": "queued", "error": "", "candidate_index": None}
            for engine in (preview.get("engines") or [])
        }
        ensure_model_runs(job)
        update_model_run(
            job, "vr360", label="360° VR · 六面图集", provider="fal",
            model_id="B2 + GPT Image 2", status="queued", stage="等待生成六面图集",
            error="", delivery_status="atlas_pending",
        )
        update_job(
            job, status="queued", operation="panorama_direct",
            operation_status="running", operation_error="",
        )
        state.JOBS.add(job.job_id, job)
        state.JOBS.persist()
        with _PREVIEW_LOCK:
            _PREVIEWS.pop(req.preview_id, None)

    update_job(
        job, status="running", started_at=time.time(), error="",
        operation="panorama_direct", operation_status="running", operation_error="",
    )
    preview["status"] = "running"
    preview["error"] = ""
    update_model_run(job, "vr360", status="running", stage="准备 3×2 六面图集", error="")
    state.JOBS.persist()
    state.spawn(_run_direct_panorama_bg(job, req.preview_id, resume=resume))
    return job_view(job)


async def _prepare_job_record(job, preview: dict) -> tuple[str, str, str]:
    if job.json_path and job.record_id and job.png_path:
        return job.json_path, job.record_id, job.png_path
    source_path = str(preview.get("source_path") or "")
    params = dict(preview.get("params") or {})
    params.update({
        "workflow_mode": str(params.get("workflow_mode") or "球面效果图 (六面图集直出 VR)"),
        "model_choice": "B2 + GPT Image 2",
        "aspect_ratio": "3:2",
        "resolution": "4K",
        "angle": "single optical center cubemap",
    })
    task = TaskParams(image_path=source_path, **{
        key: value for key, value in params.items()
        if key in TaskParams.__dataclass_fields__ and key != "image_path"
    })
    async with state.task_prep_lock:
        processed, message, _prompt, _last, json_path, record_id, png_path, _pro = await asyncio.to_thread(
            save_task_files_html, **task_params_to_kwargs(task))
    if processed is None or not json_path or not record_id or not png_path:
        raise RuntimeError(str(message or "球面效果图记录准备失败"))
    context = {
        "params": dict(preview.get("params") or {}),
        "floor_path": source_path,
        "model_filter": "custom",
        "model_targets": ["vr360"],
        "delivery_mode": DIRECT_PANO_ROUTE,
        "panorama_preview_id": preview.get("preview_id"),
    }
    await asyncio.to_thread(attach_generation_context, json_path, record_id, context)
    update_job(job, json_path=json_path, record_id=record_id, png_path=png_path)
    state.JOBS.persist()
    return json_path, record_id, png_path


def _branch_stage(job, preview: dict, engine_key: str, text: str) -> None:
    branches = preview.setdefault("branches", {})
    branch = branches.setdefault(engine_key, {})
    branch["stage"] = text
    active = []
    for engine in preview.get("engines") or []:
        row = branches.get(engine["key"]) or {}
        if row.get("status") in {"queued", "running"}:
            active.append(f"{engine.get('label')}: {row.get('stage') or row.get('status')}")
    update_model_run(job, "vr360", stage=" · ".join(active)[:500], status="running")


def _call_direct_engine(job, preview: dict, engine: dict, template_path: str,
                        mask_path: str, floor_path: str, reference_paths: list[str], *, resume: bool,
                        should_cancel):
    key = str(engine["key"])
    branch = preview.setdefault("branches", {}).setdefault(key, {})
    branch.update(status="running", error="")

    def on_stage(text: str) -> None:
        _branch_stage(job, preview, key, text)

    def on_submitted(handle: dict) -> None:
        _set_direct_handle(job, str(preview.get("preview_id") or ""), key, handle)
        branch["provider_request_id"] = str(handle.get("request_id") or "")
        branch["status"] = "running"

    image_urls = [_file_data_uri(template_path), _file_data_uri(floor_path)] + [
        _file_data_uri(path) for path in reference_paths if path and os.path.isfile(path)
    ]
    if key == "b2_atlas":
        payload = {
            "prompt": str((preview.get("prompts") or {}).get(key) or ""),
            "image_urls": image_urls,
            "num_images": 1,
            "aspect_ratio": "3:2",
            "resolution": "4K",
            "output_format": "png",
            "sync_mode": False,
            "limit_generations": True,
        }
    else:
        payload = {
            "prompt": str((preview.get("prompts") or {}).get(key) or ""),
            "image_urls": image_urls,
            "mask_image_url": _file_data_uri(mask_path),
            "image_size": {"width": DIRECT_ATLAS_WIDTH, "height": DIRECT_ATLAS_HEIGHT},
            "quality": "high",
            "num_images": 1,
            "output_format": "png",
            "sync_mode": False,
        }
    data, error = _call_fal_queue_json(
        str(load_config().get("fal_api_key") or "").strip(),
        str(engine.get("endpoint") or ""),
        payload,
        on_stage=on_stage,
        should_cancel=should_cancel,
        resume_handle=_direct_handles(job, str(preview.get("preview_id") or "")).get(key) if resume else None,
        on_submitted=on_submitted,
    )
    if data is None:
        return None, str(error or f"{engine.get('label')} 生成失败"), False
    image, decode_error = _fal_image_from_result(
        data, plural=True, direct=False,
        on_stage=on_stage, should_cancel=should_cancel,
    )
    return image, str(decode_error or ""), True


async def _process_direct_branch(job, preview: dict, engine: dict, template_path: str,
                                 mask_path: str, floor_path: str, reference_paths: list[str], *, resume: bool,
                                 should_cancel) -> dict:
    key = str(engine["key"])
    label = str(engine.get("label") or key)
    branch = preview.setdefault("branches", {}).setdefault(key, {})
    image, error, provider_succeeded = await asyncio.to_thread(
        _call_direct_engine, job, preview, engine, template_path, mask_path, floor_path, reference_paths,
        resume=resume, should_cancel=should_cancel)
    if provider_succeeded:
        record_usage(job.workflow_mode, "B2" if key == "b2_atlas" else "GPT Image 2 VR360",
                     "fal", True, "panorama_direct_atlas")
    else:
        if "取消" not in error:
            record_usage(job.workflow_mode, "B2" if key == "b2_atlas" else "GPT Image 2 VR360",
                         "fal", False, "panorama_direct_atlas")
        existing_handle = _direct_handles(job, str(preview.get("preview_id") or "")).get(key)
        branch.update(
            status=("interrupted" if existing_handle and not _terminal_fal_error(error)
                    and "取消" not in error else "failed"),
            error=error, stage="")
        if _terminal_fal_error(error) or "取消" in error:
            _set_direct_handle(job, str(preview.get("preview_id") or ""), key, None)
        raise RuntimeError(error)
    if image is None:
        raise RuntimeError(error or f"{label} 未返回图像")

    audit = {
        "schema_version": 1,
        "generation_route": DIRECT_PANO_ROUTE,
        "engine_key": key,
        "engine_label": label,
        "provider": "fal",
        "endpoint": str(engine.get("endpoint") or ""),
        "model_id": str(engine.get("model_id") or ""),
        "source_sha256": str(preview.get("source_hash") or ""),
        "paid_preview_id": str(preview.get("preview_id") or ""),
        "quality_plan_hash": str(preview.get("quality_plan_hash") or ""),
        "execution_prompt_sha256": str((preview.get("prompt_sha256") or {}).get(key) or ""),
        "film_contract_hash": str(preview.get("film_contract_hash") or ""),
        "geometry_contract_hash": str(preview.get("geometry_contract_hash") or ""),
    }
    atlas_path = save_api_result_png(image, f"VR360_{key}_Atlas", floor_path, audit)
    if not atlas_path:
        raise RuntimeError(f"{label} 图集已生成，但保存失败")
    branch["atlas_file"] = os.path.relpath(atlas_path, MAIN_OUTPUT_DIR).replace("\\", "/")
    material_reference_path = str((preview.get("params") or {}).get("film_path") or "")
    if not material_reference_path or not os.path.isfile(material_reference_path):
        material_reference_path = floor_path
    with Image.open(material_reference_path) as source:
        source.load()
        floor_reference = source.convert("RGB").copy()
    erp, faces, atlas_gate = await asyncio.to_thread(
        compose_and_gate_atlas, image, floor_reference)
    branch["atlas_gate"] = atlas_gate
    if atlas_gate.get("hard_fail"):
        branch.update(status="failed", error=f"图集自动门禁失败：{atlas_gate.get('summary')}", stage="")
        _set_direct_handle(job, str(preview.get("preview_id") or ""), key, None)
        raise RuntimeError(branch["error"])

    source_registration = None
    source_lock_meta = None
    room_reference_path = str(preview.get("room_reference_path") or "")
    if room_reference_path and preview.get("geometry_contract"):
        try:
            with Image.open(room_reference_path) as source:
                source.load()
                room_reference = source.convert("RGB").copy()
            source_registration = await asyncio.to_thread(
                register_source_to_erp, room_reference, erp, preview.get("geometry_contract"))
            if source_registration.get("status") == "ready":
                _branch_stage(job, preview, key, "🔒 本地回投并锁定空间参考扇区…")
                erp, source_lock_meta = await asyncio.to_thread(
                    lock_registered_source_view, room_reference, erp, source_registration)
        except Exception as ex:
            source_registration = {
                "version": "local-panorama-geometry-v2",
                "status": "needs_calibration", "error": str(ex),
            }
    _branch_stage(job, preview, key, "📐 本地校验并轻度矫正墙体几何…")
    erp, architecture_meta = await asyncio.to_thread(
        rectify_panorama_architecture, erp.convert("RGB"))

    film_floor_meta = None
    film_floor_error = ""
    if preview.get("film_contract"):
        try:
            _branch_stage(job, preview, key, "🪵 按原厂彩膜周期精确分切并投影地板…")
            params = dict(preview.get("params") or {})
            erp, film_floor_meta = await asyncio.to_thread(
                apply_manufacturer_film,
                erp,
                str(params.get("film_path") or ""),
                params,
                manifest=((preview.get("film_contract") or {}).get("manifest") or {}),
                geometry_contract=preview.get("geometry_contract"),
                geometry_source_path=room_reference_path,
            )
        except Exception as ex:
            film_floor_error = str(ex)
            logger.exception("[球面效果图] 原厂彩膜地板自动投影失败")

    # Persist faces from the final locally rectified/materialized ERP so the
    # downloadable cube set and the VR result cannot disagree.
    final_faces = await asyncio.to_thread(erp_to_cube, erp, 1024)
    face_files = {}
    for face, face_image in final_faces.items():
        path = save_api_result_png(face_image, f"VR360_{key}_{face}", floor_path, audit)
        if path:
            face_files[face] = os.path.relpath(path, MAIN_OUTPUT_DIR).replace("\\", "/")
    branch["face_files"] = face_files
    erp_audit = dict(audit, projection="equirectangular")
    output_path = save_api_result_png(erp, f"VR360_{key}", floor_path, erp_audit)
    if not output_path:
        raise RuntimeError(f"{label} ERP 合成后保存失败")
    erp_gate = await asyncio.to_thread(gate_visual_pano, output_path)
    gate = combine_direct_gates(atlas_gate, erp_gate)
    if architecture_meta and architecture_meta.get("status") == "rejected":
        gate["status"] = "repair_recommended"
        gate["gate_pass"] = False
        gate["failures"] = list(dict.fromkeys(
            list(gate.get("failures") or []) + ["local_architecture_geometry"]))
    if source_registration and source_registration.get("status") != "ready":
        gate["status"] = "repair_recommended"
        gate["gate_pass"] = False
        gate["failures"] = list(dict.fromkeys(
            list(gate.get("failures") or []) + ["source_geometry_registration"]))
    if film_floor_meta and film_floor_meta.get("status") != "applied":
        gate["status"] = "repair_recommended"
        gate["gate_pass"] = False
        gate["failures"] = list(dict.fromkeys(
            list(gate.get("failures") or []) + ["local_floor_calibration"]))
    if film_floor_error:
        gate["status"] = "repair_recommended"
        gate["gate_pass"] = False
        gate["failures"] = list(dict.fromkeys(list(gate.get("failures") or []) + ["manufacturer_film_floor"]))
        gate["summary"] = f"{gate.get('summary', '')}; manufacturer_film_floor:fail ({film_floor_error})"[:1000]
    if gate.get("hard_fail"):
        branch.update(status="failed", error=f"ERP 自动门禁失败：{gate.get('summary')}", stage="")
        _set_direct_handle(job, str(preview.get("preview_id") or ""), key, None)
        raise RuntimeError(branch["error"])

    panorama = {
        "schema_version": 2,
        "projection": "equirectangular",
        "width": PURE_RENDER_PANO_WIDTH,
        "height": PURE_RENDER_PANO_HEIGHT,
        "generation_route": DIRECT_PANO_ROUTE,
        "source_model": key,
        "source_index": 0,
        "source_sha256": str(preview.get("source_hash") or ""),
        "engine_key": key,
        "engine_label": label,
        "provider": "fal",
        "endpoint": str(engine.get("endpoint") or ""),
        "model_id": str(engine.get("model_id") or ""),
        "snapshot_locked": False,
        "geometry_locked": bool(
            (not preview.get("room_reference_hash")
             or (source_registration or {}).get("status") == "ready")
            and (architecture_meta or {}).get("status") in {"not_needed", "applied"}
            and (not film_floor_meta or film_floor_meta.get("status") == "applied")),
        "delivery_scope": "ai_generated_single_center_cubemap",
        "viewer_initial_yaw_deg": 90,
        "atlas": {
            "layout": [["+X", "-X", "+Y"], ["-Y", "+Z", "-Z"]],
            "source_file": branch.get("atlas_file"),
            "face_files": face_files,
            "registration": (atlas_gate.get("registration") or {}),
        },
        "gate": gate,
        "review": {"status": "needs_review", "checklist": {}, "reviewed_at": ""},
        "generated_at": _utc_now(),
        "paid_preview_id": str(preview.get("preview_id") or ""),
        "quality_plan": preview.get("quality_plan"),
        "quality_plan_hash": str(preview.get("quality_plan_hash") or ""),
        "execution_prompt_sha256": str((preview.get("prompt_sha256") or {}).get(key) or ""),
        "geometry_contract_hash": str(preview.get("geometry_contract_hash") or ""),
        "geometry_contract": preview.get("geometry_contract"),
        "source_registration": source_registration,
        "source_view_lock": source_lock_meta,
        "wall_rectification": architecture_meta,
        "room_reference_sha256": str(preview.get("room_reference_hash") or ""),
        "floor_material_source": ("manufacturer_repeat_film"
                                  if preview.get("film_contract") else "floor_sample"),
        "floor_delivery_mode": (str(film_floor_meta.get("delivery_mode") or "local_exact_film_v4")
                                if film_floor_meta else "model_replica"),
        "manufacturer_film_floor": film_floor_meta,
        "manufacturer_film_error": film_floor_error,
    }
    metadata = {
        "projection": "equirectangular",
        "engine_label": label,
        "generation_route": DIRECT_PANO_ROUTE,
        "panorama": panorama,
    }
    candidate_index = add_model_candidate(job, "vr360", output_path, metadata)
    record_result_id = await asyncio.to_thread(
        api_write_to_record,
        erp,
        f"360° VR · {label}",
        job.json_path,
        job.record_id,
        output_path,
        metadata,
        None,
        f"六面图集直出并确定性合成 ERP ({PURE_RENDER_PANO_WIDTH}x{PURE_RENDER_PANO_HEIGHT})",
    )
    panorama["record_result_id"] = record_result_id or ""
    run = job.model_runs.get("vr360") or {}
    metas = list(run.get("candidate_meta") or [])
    if candidate_index < len(metas):
        metas[candidate_index] = {
            "projection": "equirectangular", "engine_label": label,
            "generation_route": DIRECT_PANO_ROUTE, "panorama": panorama,
        }
        run["candidate_meta"] = metas
    branch.update(
        status="succeeded", error="", stage="", candidate_index=candidate_index,
        gate_status=str(gate.get("status") or ""), completed_at=time.time(),
    )
    _set_direct_handle(job, str(preview.get("preview_id") or ""), key, None)
    return {"candidate_index": candidate_index, "gate_status": gate.get("status")}


async def _run_direct_panorama_bg(job, preview_id: str, *, resume: bool) -> None:
    preview = (job.panorama_previews or {}).get(preview_id) or {}
    started = time.time()
    generation = state.JOBS.generation
    temporary_paths: list[str] = []
    should_cancel = lambda: state.JOBS.is_cancelled(job.job_id, generation)
    try:
        _, _, floor_path = await _prepare_job_record(job, preview)
        template, mask = build_atlas_template()
        template_path = save_api_result_png(
            template, "VR360_Atlas_Template", floor_path,
            {"generation_route": DIRECT_PANO_ROUTE, "preview_id": preview_id})
        if not template_path:
            raise RuntimeError("六面图集模板保存失败")
        preview["template_file"] = os.path.relpath(template_path, MAIN_OUTPUT_DIR).replace("\\", "/")
        fd, mask_path = tempfile.mkstemp(prefix=".vr360_atlas_", suffix="_mask.png", dir=MAIN_OUTPUT_DIR)
        os.close(fd)
        mask.save(mask_path, format="PNG")
        temporary_paths.append(mask_path)
        reference_paths: list[str] = []
        room_reference_path = str(preview.get("room_reference_path") or "")
        if room_reference_path and os.path.isfile(room_reference_path):
            reference_paths.append(room_reference_path)
        geometry_guide = build_room_geometry_guide()
        fd, geometry_path = tempfile.mkstemp(prefix=".vr360_geometry_", suffix=".png", dir=MAIN_OUTPUT_DIR)
        os.close(fd)
        geometry_guide.save(geometry_path, format="PNG")
        temporary_paths.append(geometry_path)
        params = dict(preview.get("params") or {})
        film_path = str(params.get("film_path") or "")
        if film_path and os.path.isfile(film_path):
            reference_paths.append(film_path)
        guide_b64 = str(((preview.get("film_contract") or {}).get("guide_b64") or ""))
        if guide_b64:
            fd, guide_path = tempfile.mkstemp(prefix=".vr360_film_guide_", suffix=".png", dir=MAIN_OUTPUT_DIR)
            os.close(fd)
            with open(guide_path, "wb") as handle:
                handle.write(base64.b64decode(guide_b64))
            temporary_paths.append(guide_path)
            reference_paths.append(guide_path)
        reference_paths.append(geometry_path)

        pending_engines = [
            engine for engine in (preview.get("engines") or [])
            if (preview.get("branches") or {}).get(engine["key"], {}).get("status") != "succeeded"
        ]
        semaphore = state.model_semaphores["vr360"]
        if semaphore.locked():
            update_model_run(job, "vr360", stage="球面效果图排队中")
        async with semaphore:
            results = await asyncio.gather(*[
                _process_direct_branch(
                    job, preview, engine, template_path, mask_path, floor_path,
                    reference_paths, resume=resume, should_cancel=should_cancel)
                for engine in pending_engines
            ], return_exceptions=True)

        errors = [str(result) for result in results if isinstance(result, Exception)]
        ensure_model_runs(job)
        run = job.model_runs.get("vr360") or {}
        count = len(run.get("paths") or [])
        branches = list((preview.get("branches") or {}).values())
        interrupted = any(
            row.get("status") in {"running", "interrupted"}
            and _direct_handles(job, preview_id).get(key)
            for key, row in (preview.get("branches") or {}).items()
        )
        if should_cancel():
            final = "partial" if count else "failed"
            preview["status"] = "cancelled"
            operation_status = "cancelled"
            error_text = "已取消；已经生成的候选已保留" if count else "已取消"
        elif interrupted:
            final = "partial" if count else "failed"
            preview["status"] = "interrupted"
            operation_status = "failed"
            error_text = "；".join(errors) or "供应商队列中断，可用原确认恢复"
        elif count >= 2:
            final = "done"
            preview["status"] = "succeeded"
            operation_status = "done"
            error_text = ""
        elif count == 1:
            final = "partial"
            preview["status"] = "succeeded"
            operation_status = "done"
            error_text = "；".join(errors) or "一个图集候选未通过"
        else:
            final = "failed"
            preview["status"] = "failed"
            operation_status = "failed"
            error_text = "；".join(errors) or "两条图集线路均未生成可交付全景"
        gate_statuses = [str(row.get("gate_status") or "") for row in branches if row.get("gate_status")]
        delivery = "repair_recommended" if "repair_recommended" in gate_statuses else (
            "passed" if gate_statuses else "failed")
        preview["completed_at"] = time.time()
        preview["error"] = error_text
        update_model_run(
            job, "vr360", status=("done" if count else "failed"), stage="", error=error_text,
            seconds=round(time.time() - started, 1), delivery_status=delivery,
        )
        update_job(
            job, status=final, error=error_text,
            operation_status=operation_status, operation_error=error_text,
        )
    except Exception as ex:
        error = str(ex)
        logger.exception(f"[球面效果图] 处理失败 job={job.job_id} preview={preview_id}")
        handles = _direct_handles(job, preview_id)
        cancelled = should_cancel() or "取消" in error
        preview["status"] = "cancelled" if cancelled else ("interrupted" if handles else "failed")
        preview["error"] = error
        run = (job.model_runs or {}).get("vr360") or {}
        has_paths = bool(run.get("paths"))
        update_model_run(
            job, "vr360", status="partial" if has_paths else "failed", stage="", error=error,
            seconds=round(time.time() - started, 1),
        )
        update_job(
            job, status="partial" if has_paths else "failed", error=error,
            operation_status="cancelled" if cancelled else "failed", operation_error=error,
        )
    finally:
        for path in temporary_paths:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        state.JOBS.clear_cancelled(job.job_id)
        state.JOBS.persist()


__all__ = ["router"]
