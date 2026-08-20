# -*- coding: utf-8 -*-
"""Derived single-hotspot 360 panorama routes for pure-render jobs."""
from __future__ import annotations

import asyncio
import base64
import copy
import os
import tempfile
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from PIL import Image

from . import server_state as state
from .api import call_gpt_image_edit
from .config import (
    FAL_GPT_IMAGE_2_ENDPOINT,
    GPT_IMAGE_2_MODEL,
    MAIN_OUTPUT_DIR,
    get_usage_prices,
    get_omakase_gemini_model,
    load_config,
    logger,
)
from .panorama_quality_planner import (
    DIRECT_ROUTE as QUALITY_DIRECT_ROUTE,
    PERSPECTIVE_ROUTE,
    compile_panorama_prompt,
    compile_panorama_repair_prompt,
    local_quality_plan,
    plan_panorama_quality,
    validate_quality_plan,
)
from .models import (
    JobRecord,
    add_model_candidate,
    compute_runs_final_status,
    ensure_model_runs,
    update_job,
    update_model_run,
)
from .film_repeat_floor import build_film_contract
from .panorama_film_material import apply_manufacturer_film
from .panorama_local_geometry import (
    build_geometry_contract,
    lock_registered_source_view,
    rectify_panorama_architecture,
    register_source_to_erp,
)
from .pure_render_pano import (
    PURE_RENDER_PANO_HEIGHT,
    PURE_RENDER_PANO_SIZE,
    PURE_RENDER_PANO_WIDTH,
    build_architecture_repair_mask,
    build_architecture_repair_prompt,
    build_pure_render_pano_prompt,
    create_paid_preview,
    file_sha256,
    gate_visual_pano,
    public_paid_preview,
    pure_render_review_status,
    validate_paid_preview,
)
from .pure_render_pano_atlas import (
    DIRECT_PANO_ROUTE,
    build_cube_boundary_repair_mask,
    build_cube_fusion_repair_prompt,
    build_room_geometry_guide_erp,
)
from .records import (
    api_write_to_record,
    find_record_result_id_by_image_file,
    load_records_file,
    save_api_result_png,
    update_result_panorama_metadata,
)
from .server_helpers import job_view, require_ref_image_path, result_thumb_url
from .server_schemas import (
    PanoramaCommitRequest,
    PanoramaPaidPreviewRequest,
    PanoramaReviewRequest,
)
from .usage_stats import record_usage
from .whole_home_pano_edit import (
    build_seam_repair_mask,
    build_seam_repair_prompt,
    circular_shift_erp,
)


router = APIRouter()
_SOURCE_MODELS = {"b2", "pro", "sd35"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _job_or_404(job_id: str) -> JobRecord:
    job = state.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _assert_panorama_job(job: JobRecord) -> None:
    mode = str(job.workflow_mode or "")
    if "纯效果图" not in mode and "球面效果图" not in mode:
        raise HTTPException(422, "360° VR 仅支持纯效果图或球面效果图工作流")


def _managed_output_path(path: str) -> str:
    try:
        base = os.path.realpath(MAIN_OUTPUT_DIR)
        resolved = os.path.realpath(str(path or ""))
        if os.path.commonpath([base, resolved]) != base or not os.path.isfile(resolved):
            raise ValueError
    except Exception as ex:
        raise HTTPException(400, "候选图已失效或不属于本任务输出目录") from ex
    return resolved


def _candidate(job: JobRecord, model: str, index: int) -> tuple[str, dict]:
    ensure_model_runs(job)
    run = job.model_runs.get(model) or {}
    paths = list(run.get("paths") or [])
    if index < 0 or index >= len(paths):
        raise HTTPException(404, "未找到所选候选图")
    path = _managed_output_path(paths[index])
    metas = list(run.get("candidate_meta") or [])
    metadata = metas[index] if index < len(metas) and isinstance(metas[index], dict) else {}
    return path, dict(metadata)


def _set_candidate_meta(job: JobRecord, model: str, index: int, metadata: dict) -> None:
    ensure_model_runs(job)
    run = job.model_runs.get(model) or {}
    paths = list(run.get("paths") or [])
    if index < 0 or index >= len(paths):
        raise ValueError("panorama_candidate_missing")
    metas = [dict(value) if isinstance(value, dict) else {}
             for value in (run.get("candidate_meta") or [])]
    metas.extend({} for _ in range(len(paths) - len(metas)))
    metas[index] = dict(metadata)
    run["candidate_meta"] = metas[:len(paths)]


def _panorama_candidate(job: JobRecord, index: int) -> tuple[str, dict]:
    path, metadata = _candidate(job, "vr360", index)
    panorama = metadata.get("panorama") if isinstance(metadata, dict) else None
    if not isinstance(panorama, dict):
        raise HTTPException(409, "所选结果不是可修复的全景")
    if panorama.get("projection") != "equirectangular":
        raise HTTPException(409, "所选结果不是 2:1 ERP 全景")
    return path, dict(panorama)


def _record_context(job: JobRecord) -> dict:
    if not job.json_path or not job.record_id or not os.path.isfile(job.json_path):
        return {}
    record = next((row for row in load_records_file(job.json_path)
                   if row.get("id") == job.record_id), None)
    context = record.get("gen_context") if isinstance(record, dict) else None
    return dict(context or {})


def _preview_rows(job: JobRecord) -> dict:
    if not isinstance(job.panorama_previews, dict):
        job.panorama_previews = {}
    return job.panorama_previews


def _price_for_vr360() -> float | None:
    prices = get_usage_prices()
    value = prices.get("VR360:fal", prices.get("VR360"))
    return float(value) if value is not None else None


def _queue_handle(job: JobRecord, preview_id: str) -> dict:
    ensure_model_runs(job)
    run = job.model_runs.get("vr360") or {}
    settings = dict(run.get("settings") or {})
    handles = settings.get("pano_queue_handles") or {}
    value = handles.get(preview_id) if isinstance(handles, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def _set_queue_handle(job: JobRecord, preview_id: str, handle) -> None:
    run = update_model_run(job, "vr360")
    settings = dict(run.get("settings") or {})
    handles = dict(settings.get("pano_queue_handles") or {})
    if handle:
        handles[preview_id] = dict(handle)
    else:
        handles.pop(preview_id, None)
    settings["pano_queue_handles"] = handles
    update_model_run(job, "vr360", settings=settings)
    state.JOBS.persist()


def _terminal_fal_error(error: str) -> bool:
    text = str(error or "").upper()
    return "FAL 队列任务FAILED" in text or "FAL 队列任务CANCELLED" in text


def _preview_http_error(error: ValueError) -> HTTPException:
    code = str(error)
    messages = {
        "panorama_preview_missing": "付费预览不存在",
        "panorama_preview_expired": "付费预览已过期，请重新确认",
        "panorama_preview_hash_mismatch": "付费预览校验失败",
        "panorama_preview_job_mismatch": "付费预览不属于当前任务",
        "panorama_preview_source_changed": "源候选已经变化，请重新预览",
        "panorama_preview_tampered": "付费预览内容已被修改",
    }
    return HTTPException(409, {"code": code, "message": messages.get(code, code)})


@router.post("/api/jobs/{jid}/panorama/preview")
def preview_job_panorama(jid: str, req: PanoramaPaidPreviewRequest):
    job = _job_or_404(jid)
    _assert_panorama_job(job)
    if job.status in ("queued", "running") or job.pro_polishing or job.operation_status == "running":
        raise HTTPException(409, "任务正在处理，请稍后再生成全景")
    cfg = load_config()
    if not str(cfg.get("fal_api_key") or "").strip():
        raise HTTPException(400, "生成 360° VR 需要先配置 Fal API Key")

    endpoint = str(cfg.get("fal_gpt_image_endpoint") or FAL_GPT_IMAGE_2_ENDPOINT).strip()
    context = _record_context(job)
    params = context.get("params") if isinstance(context.get("params"), dict) else {}
    film_contract = None
    if params.get("film_path"):
        film_path = require_ref_image_path(str(params.get("film_path") or ""))
        film_contract, _ = build_film_contract(film_path, params, guide_size=512)
    if req.action == "generate":
        if "纯效果图" not in str(job.workflow_mode or ""):
            raise HTTPException(422, "球面效果图已经直接生成 ERP，不需要再次从透视图扩展")
        model = str(req.source_model or "")
        if model not in _SOURCE_MODELS:
            raise HTTPException(422, "全景只能从 B2、Pro 或 SD3.5 候选生成")
        source_path, _ = _candidate(job, model, req.source_index)
        base_prompt = build_pure_render_pano_prompt(
            params,
            source_label=(job.model_runs.get(model) or {}).get("label") or model,
        )
        source_model = model
        source_index = req.source_index
        panorama_index = None
        repair_kind = ""
    else:
        source_path, panorama = _panorama_candidate(job, int(req.panorama_index or 0))
        gate = panorama.get("gate") or {}
        if gate.get("status") != "repair_recommended":
            raise HTTPException(409, "只有接缝门禁建议修复的全景才能提交修缝")
        if panorama.get("repair_claimed") or panorama.get("repair_result_index") is not None:
            raise HTTPException(409, "该全景已经使用过唯一一次付费修缝")
        failures = set((gate.get("failures") or []))
        if panorama.get("generation_route") == DIRECT_PANO_ROUTE:
            base_prompt = build_cube_fusion_repair_prompt()
            repair_kind = "cube_boundaries"
        elif "architecture_views" in failures:
            base_prompt = build_architecture_repair_prompt()
            repair_kind = "architecture"
        else:
            base_prompt = build_seam_repair_prompt()
            repair_kind = "wrap_seam"
        source_model = "vr360"
        source_index = int(req.panorama_index or 0)
        panorama_index = int(req.panorama_index or 0)

    geometry_contract = None
    if req.action == "generate":
        geometry_contract = build_geometry_contract(
            source_path, params, reference_role="perspective_source")

    source_hash = file_sha256(source_path)
    if req.action == "generate":
        quality_plan = plan_panorama_quality(
            route=PERSPECTIVE_ROUTE,
            source_path=source_path,
            source_hash=source_hash,
            params=params,
            api_key=str(cfg.get("gemini_api_key") or ""),
            model=get_omakase_gemini_model(),
        )
        prompt = compile_panorama_prompt(base_prompt, quality_plan)
    else:
        inherited_plan = panorama.get("quality_plan")
        if validate_quality_plan(inherited_plan):
            quality_plan = copy.deepcopy(inherited_plan)
            quality_plan["cache_hit"] = True
            quality_plan["planner_call_count"] = 0
        else:
            plan_route = (QUALITY_DIRECT_ROUTE
                          if panorama.get("generation_route") == DIRECT_PANO_ROUTE
                          else PERSPECTIVE_ROUTE)
            quality_plan = local_quality_plan(
                route=plan_route,
                source_hash=str(panorama.get("source_sha256") or source_hash),
                params=params,
                reason="旧全景没有可复用的导演规划，修复使用本地连续性合同",
            )
        prompt = compile_panorama_repair_prompt(
            base_prompt, quality_plan, panorama.get("gate") or {}, repair_kind)
    row = create_paid_preview(
        job_id=job.job_id,
        action=req.action,
        source_model=source_model,
        source_index=source_index,
        source_hash=source_hash,
        panorama_index=panorama_index,
        provider="fal",
        endpoint=endpoint,
        model_id=GPT_IMAGE_2_MODEL,
        prompt=prompt,
        quality_plan=quality_plan,
        film_contract=film_contract,
        geometry_contract=geometry_contract,
        estimated_cost=_price_for_vr360(),
    )
    row["repair_kind"] = repair_kind
    _preview_rows(job)[row["preview_id"]] = row
    state.JOBS.persist()
    label = (job.model_runs.get(source_model) or {}).get("label") or source_model
    return public_paid_preview(
        row, source_thumb=result_thumb_url(source_path), source_label=label)


def _resolve_preview_source(job: JobRecord, preview: dict) -> tuple[str, dict]:
    if preview.get("action") == "repair":
        return _panorama_candidate(job, int(preview.get("panorama_index") or 0))
    model = str(preview.get("source_model") or "")
    if model not in _SOURCE_MODELS:
        raise HTTPException(409, "付费预览的源模型无效")
    return _candidate(job, model, int(preview.get("source_index") or 0))


@router.post("/api/jobs/{jid}/panorama/commit")
async def commit_job_panorama(jid: str, req: PanoramaCommitRequest):
    job = _job_or_404(jid)
    _assert_panorama_job(job)
    preview = _preview_rows(job).get(req.preview_id)
    if not isinstance(preview, dict):
        raise HTTPException(404, "付费预览不存在")
    source_path, source_meta = _resolve_preview_source(job, preview)
    try:
        validate_paid_preview(
            preview,
            preview_hash=req.preview_hash,
            job_id=job.job_id,
            source_hash=file_sha256(source_path),
            allow_expired=preview.get("status") != "ready",
        )
    except ValueError as ex:
        raise _preview_http_error(ex) from ex

    preview_status = str(preview.get("status") or "ready")
    if preview_status == "succeeded":
        return job_view(job)
    if preview_status in {"failed", "cancelled"}:
        return job_view(job)
    if preview_status in {"claimed", "running"} and job.operation_status == "running":
        return job_view(job)

    resume = preview_status == "interrupted" or preview_status in {"claimed", "running"}
    if resume and not _queue_handle(job, req.preview_id):
        raise HTTPException(409, {
            "code": "panorama_paid_submission_ambiguous",
            "message": "请求曾被认领但没有可恢复的队列句柄；为避免重复计费不会再次提交",
        })
    if not resume:
        if job.status in ("queued", "running") or job.pro_polishing or job.operation_status == "running":
            raise HTTPException(409, "任务正在处理，请稍后再试")
        if preview.get("action") == "repair":
            parent_index = int(preview.get("panorama_index") or 0)
            if source_meta.get("repair_claimed") or source_meta.get("repair_result_index") is not None:
                raise HTTPException(409, "该全景已经使用过唯一一次付费修缝")
            source_meta["repair_claimed"] = True
            source_meta["repair_claimed_at"] = _utc_now()
            _set_candidate_meta(job, "vr360", parent_index, {
                "projection": "equirectangular", "panorama": source_meta})
            record_result_id = str(source_meta.get("record_result_id") or "")
            if record_result_id:
                update_result_panorama_metadata(
                    job.json_path, job.record_id, record_result_id, source_meta)
        preview["status"] = "claimed"
        preview["claimed_at"] = time.time()

    update_model_run(
        job, "vr360", status="running",
        stage=("排队修复全景接缝" if preview.get("action") == "repair" else "排队生成 360° VR"),
        error="", provider="fal", model_id=str(preview.get("model_id") or GPT_IMAGE_2_MODEL),
    )
    update_job(
        job,
        status="running",
        started_at=time.time(),
        error="",
        operation=("panorama_repair" if preview.get("action") == "repair" else "panorama_generate"),
        operation_status="running",
        operation_error="",
    )
    preview["status"] = "running"
    preview["error"] = ""
    state.JOBS.persist()
    state.spawn(_run_panorama_bg(job, req.preview_id, resume=resume))
    return job_view(job)


async def _run_panorama_bg(job: JobRecord, preview_id: str, *, resume: bool) -> None:
    preview = _preview_rows(job).get(preview_id) or {}
    started = time.time()
    generation = state.JOBS.generation
    temporary_paths: list[str] = []
    usage_recorded = False

    def stage(text: str) -> None:
        update_model_run(job, "vr360", stage=text, status="running")

    def on_submitted(handle: dict) -> None:
        _set_queue_handle(job, preview_id, handle)
        preview["provider_request_id"] = str(handle.get("request_id") or "")
        preview["status"] = "running"

    should_cancel = lambda: state.JOBS.is_cancelled(job.job_id, generation)
    try:
        source_path, parent_panorama = _resolve_preview_source(job, preview)
        source_paths = [source_path]
        mask_path = ""
        if preview.get("action") == "generate":
            floor_path = str(job.png_path or "")
            if floor_path and os.path.isfile(floor_path) and os.path.realpath(floor_path) != os.path.realpath(source_path):
                source_paths.append(floor_path)
            context = _record_context(job)
            params = context.get("params") if isinstance(context.get("params"), dict) else {}
            film_path = str(params.get("film_path") or "")
            if film_path:
                source_paths.append(require_ref_image_path(film_path))
            guide_b64 = str(((preview.get("film_contract") or {}).get("guide_b64") or ""))
            if guide_b64:
                fd, guide_path = tempfile.mkstemp(prefix=".vr360_film_guide_", suffix=".png", dir=MAIN_OUTPUT_DIR)
                os.close(fd)
                with open(guide_path, "wb") as handle:
                    handle.write(base64.b64decode(guide_b64))
                temporary_paths.append(guide_path)
                source_paths.append(guide_path)
            geometry_guide = build_room_geometry_guide_erp()
            fd, geometry_path = tempfile.mkstemp(prefix=".vr360_geometry_", suffix=".png", dir=MAIN_OUTPUT_DIR)
            os.close(fd)
            geometry_guide.save(geometry_path, format="PNG")
            temporary_paths.append(geometry_path)
            source_paths.append(geometry_path)
        else:
            direct_atlas_repair = parent_panorama.get("generation_route") == DIRECT_PANO_ROUTE
            architecture_repair = preview.get("repair_kind") == "architecture"
            with Image.open(source_path) as source:
                source.load()
                shifted = (source.convert("RGB") if direct_atlas_repair or architecture_repair
                           else circular_shift_erp(source.convert("RGB")))
            if direct_atlas_repair:
                mask = build_cube_boundary_repair_mask(
                    PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT)
            elif architecture_repair:
                mask = build_architecture_repair_mask(
                    parent_panorama.get("gate") or {},
                    PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT)
            else:
                mask = build_seam_repair_mask(
                    PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT)
            for suffix, image in (("_shifted.png", shifted), ("_mask.png", mask)):
                fd, path = tempfile.mkstemp(prefix=".vr360_", suffix=suffix, dir=MAIN_OUTPUT_DIR)
                os.close(fd)
                image.save(path, format="PNG")
                temporary_paths.append(path)
            source_paths = [temporary_paths[0]]
            mask_path = temporary_paths[1]

        semaphore = state.model_semaphores["vr360"]
        if semaphore.locked():
            stage("排队中")
        async with semaphore:
            stage("🎨 GPT Image 2 生成 2:1 ERP…" if preview.get("action") == "generate"
                  else ("📐 GPT Image 2 修复墙体结构…"
                        if preview.get("repair_kind") == "architecture"
                        else "🧵 GPT Image 2 修复全景边界…"))
            image, error = await asyncio.to_thread(
                call_gpt_image_edit,
                "",
                str(preview.get("prompt") or ""),
                source_paths,
                mask_path,
                model_id=str(preview.get("model_id") or GPT_IMAGE_2_MODEL),
                size=PURE_RENDER_PANO_SIZE,
                provider="fal",
                endpoint=str(preview.get("endpoint") or FAL_GPT_IMAGE_2_ENDPOINT),
                resume_handle=_queue_handle(job, preview_id) if resume else None,
                on_submitted=on_submitted,
                on_stage=stage,
                should_cancel=should_cancel,
            )
        if image is None:
            raise RuntimeError(str(error or "GPT Image 2 全景生成失败"))
        if (preview.get("action") == "repair"
                and parent_panorama.get("generation_route") != DIRECT_PANO_ROUTE
                and preview.get("repair_kind") != "architecture"):
            image = circular_shift_erp(image.convert("RGB"), -(image.width // 2))
        image.load()
        if image.size != (PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT):
            raise RuntimeError(
                f"模型输出 {image.width}x{image.height}，要求严格为 {PURE_RENDER_PANO_SIZE}")

        architecture_meta = None
        source_registration = None
        source_lock_meta = None
        if preview.get("action") == "generate":
            geometry_contract = preview.get("geometry_contract")
            if geometry_contract:
                try:
                    with Image.open(source_path) as source:
                        source.load()
                        reference = source.convert("RGB").copy()
                    source_registration = await asyncio.to_thread(
                        register_source_to_erp, reference, image, geometry_contract)
                    if source_registration.get("status") == "ready":
                        stage("🔒 本地回投并锁定原图可见扇区…")
                        image, source_lock_meta = await asyncio.to_thread(
                            lock_registered_source_view, reference, image, source_registration)
                except Exception as ex:
                    source_registration = {
                        "version": "local-panorama-geometry-v2",
                        "status": "needs_calibration", "error": str(ex),
                    }
            stage("📐 本地校验并轻度矫正墙体几何…")
            image, architecture_meta = await asyncio.to_thread(
                rectify_panorama_architecture, image.convert("RGB"))

        film_floor_meta = None
        film_floor_error = ""
        if preview.get("action") == "generate" and preview.get("film_contract"):
            try:
                stage("🪵 按原厂彩膜周期精确分切并投影地板…")
                context = _record_context(job)
                params = context.get("params") if isinstance(context.get("params"), dict) else {}
                film_path = require_ref_image_path(str(params.get("film_path") or ""))
                image, film_floor_meta = await asyncio.to_thread(
                    apply_manufacturer_film,
                    image,
                    film_path,
                    params,
                    manifest=((preview.get("film_contract") or {}).get("manifest") or {}),
                    geometry_contract=preview.get("geometry_contract"),
                    geometry_source_path=source_path,
                )
            except Exception as ex:
                film_floor_error = str(ex)
                logger.exception("[VR360] 原厂彩膜地板自动投影失败")

        fd, gate_path = tempfile.mkstemp(prefix=".vr360_gate_", suffix=".png", dir=MAIN_OUTPUT_DIR)
        os.close(fd)
        image.convert("RGB").save(gate_path, format="PNG")
        temporary_paths.append(gate_path)
        gate = gate_visual_pano(gate_path)
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
        if gate.get("status") == "failed":
            raise RuntimeError(f"全景自动门禁失败：{gate.get('summary') or 'unknown'}")

        if preview.get("action") == "repair":
            source_model = str(parent_panorama.get("source_model") or "")
            source_index = int(parent_panorama.get("source_index") or 0)
            source_sha256 = str(parent_panorama.get("source_sha256") or "")
        else:
            source_model = str(preview.get("source_model") or "")
            source_index = int(preview.get("source_index") or 0)
            source_sha256 = str(preview.get("source_hash") or "")

        panorama_meta = {
            "schema_version": 2,
            "projection": "equirectangular",
            "width": PURE_RENDER_PANO_WIDTH,
            "height": PURE_RENDER_PANO_HEIGHT,
            "source_model": source_model,
            "source_index": source_index,
            "source_sha256": source_sha256,
            "provider": "fal",
            "endpoint": str(preview.get("endpoint") or FAL_GPT_IMAGE_2_ENDPOINT),
            "model_id": str(preview.get("model_id") or GPT_IMAGE_2_MODEL),
            "snapshot_locked": False,
            "delivery_scope": "ai_expanded_single_hotspot",
            "viewer_initial_yaw_deg": 90,
            "gate": gate,
            "review": {"status": "needs_review", "checklist": {}, "reviewed_at": ""},
            "generated_at": _utc_now(),
            "paid_preview_id": preview_id,
            "quality_plan": preview.get("quality_plan"),
            "quality_plan_hash": str(preview.get("quality_plan_hash") or ""),
            "execution_prompt_sha256": str(preview.get("prompt_sha256") or ""),
            "film_contract_hash": str(preview.get("film_contract_hash") or ""),
            "geometry_contract_hash": str(preview.get("geometry_contract_hash") or ""),
            "geometry_contract": preview.get("geometry_contract"),
            "source_registration": source_registration,
            "source_view_lock": source_lock_meta,
            "wall_rectification": architecture_meta,
            "geometry_locked": bool(
                (preview.get("geometry_contract") or {}).get("status") == "ready"
                and (source_registration or {}).get("status") == "ready"
                and (architecture_meta or {}).get("status") in {"not_needed", "applied"}),
            "floor_material_source": ("manufacturer_repeat_film"
                                      if preview.get("film_contract") else "floor_sample"),
            "floor_delivery_mode": (str(film_floor_meta.get("delivery_mode") or "local_exact_film_v4")
                                    if film_floor_meta else "model_replica"),
            "manufacturer_film_floor": film_floor_meta,
            "manufacturer_film_error": film_floor_error,
        }
        if preview.get("action") == "repair":
            panorama_meta["generation_route"] = str(
                parent_panorama.get("generation_route") or "ai_expanded_erp")
            panorama_meta["engine_key"] = str(parent_panorama.get("engine_key") or "")
            panorama_meta["engine_label"] = (
                str(parent_panorama.get("engine_label") or "360° VR") + " · 融合修复")
            panorama_meta["delivery_scope"] = str(
                parent_panorama.get("delivery_scope") or panorama_meta["delivery_scope"])
            panorama_meta["repair_of_index"] = int(preview.get("panorama_index") or 0)
            panorama_meta["repair_source_sha256"] = str(preview.get("source_hash") or "")
            panorama_meta["repair_kind"] = str(preview.get("repair_kind") or "")

        metadata = {"projection": "equirectangular", "panorama": panorama_meta}
        output_path = save_api_result_png(image, "VR360", source_path, metadata)
        if not output_path:
            raise RuntimeError("全景已生成，但保存 PNG 失败")
        candidate_index = add_model_candidate(job, "vr360", output_path, metadata)

        if preview.get("action") == "repair":
            source_result_id = str(parent_panorama.get("record_result_id") or "") or None
        else:
            source_result_id = find_record_result_id_by_image_file(
                job.json_path, job.record_id, source_path)
        record_result_id = await asyncio.to_thread(
            api_write_to_record,
            image,
            "360° VR 全景" if preview.get("action") == "generate" else "360° VR · 接缝修复",
            job.json_path,
            job.record_id,
            output_path,
            metadata,
            source_result_id,
            f"AI 扩展单点 360° 全景 ({PURE_RENDER_PANO_SIZE})",
        )
        panorama_meta["record_result_id"] = record_result_id or ""
        _set_candidate_meta(job, "vr360", candidate_index, {
            "projection": "equirectangular", "panorama": panorama_meta})

        if preview.get("action") == "repair":
            parent_index = int(preview.get("panorama_index") or 0)
            _, latest_parent = _panorama_candidate(job, parent_index)
            latest_parent["repair_result_index"] = candidate_index
            _set_candidate_meta(job, "vr360", parent_index, {
                "projection": "equirectangular", "panorama": latest_parent})
            parent_result_id = str(latest_parent.get("record_result_id") or "")
            if parent_result_id:
                await asyncio.to_thread(
                    update_result_panorama_metadata,
                    job.json_path, job.record_id, parent_result_id, latest_parent)

        preview["status"] = "succeeded"
        preview["candidate_index"] = candidate_index
        preview["completed_at"] = time.time()
        preview["error"] = ""
        _set_queue_handle(job, preview_id, None)
        record_usage(job.workflow_mode, "GPT Image 2 VR360", "fal", True,
                     "panorama_repair" if preview.get("action") == "repair" else "panorama_generate")
        usage_recorded = True
        update_model_run(
            job, "vr360", status="done", stage="", error="",
            seconds=round(time.time() - started, 1), delivery_status=str(gate.get("status") or ""))
        update_job(
            job, status=compute_runs_final_status(job), error="",
            operation_status="done", operation_error="")
    except Exception as ex:
        error = str(ex)
        logger.exception(f"[VR360] 处理失败 job={job.job_id} preview={preview_id}")
        handle = _queue_handle(job, preview_id)
        cancelled = "取消" in error or should_cancel()
        if cancelled:
            preview["status"] = "cancelled"
        elif handle and not _terminal_fal_error(error):
            preview["status"] = "interrupted"
        else:
            preview["status"] = "failed"
            if _terminal_fal_error(error):
                _set_queue_handle(job, preview_id, None)
        preview["error"] = error
        if not usage_recorded and not cancelled:
            record_usage(
                job.workflow_mode, "GPT Image 2 VR360", "fal", False,
                "panorama_repair" if preview.get("action") == "repair" else "panorama_generate")
        ensure_model_runs(job)
        run = job.model_runs.get("vr360") or {}
        update_model_run(
            job, "vr360", status=("partial" if run.get("paths") else "failed"),
            stage="", error=error, seconds=round(time.time() - started, 1))
        update_job(
            job, status=compute_runs_final_status(job), error=error,
            operation_status=("cancelled" if cancelled else "failed"),
            operation_error=error)
    finally:
        for path in temporary_paths:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        state.JOBS.clear_cancelled(job.job_id)
        state.JOBS.persist()


@router.post("/api/jobs/{jid}/panorama/review")
def review_job_panorama(jid: str, req: PanoramaReviewRequest):
    job = _job_or_404(jid)
    _assert_panorama_job(job)
    if job.operation_status == "running":
        raise HTTPException(409, "任务正在处理，请稍后复核")
    _, panorama = _panorama_candidate(job, req.panorama_index)
    checklist = req.checklist.model_dump()
    review_status = pure_render_review_status(panorama.get("gate") or {}, checklist)
    panorama["review"] = {
        "status": review_status,
        "checklist": checklist,
        "reviewed_at": _utc_now(),
    }
    _set_candidate_meta(job, "vr360", req.panorama_index, {
        "projection": "equirectangular", "panorama": panorama})
    record_result_id = str(panorama.get("record_result_id") or "")
    if record_result_id:
        updated = update_result_panorama_metadata(
            job.json_path, job.record_id, record_result_id, panorama)
        if updated is None:
            raise HTTPException(409, "全景已复核，但对应历史记录更新失败")
    state.JOBS.persist()
    return job_view(job)


__all__ = ["router"]
