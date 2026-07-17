# ==========================================
# 地板 AI 生图引擎 — 无头 HTTP/SSE 服务层 (FastAPI)
# STEP 1 of「迁出 NiceGUI → 真前端」迁移：把引擎能力暴露为 REST/SSE，供 Next.js 前端对接。
# 设计原则：引擎模块(api/prompts/records/models/config/...)零改动，本文件只做编排 + 协议适配。
# 关键：本文件【不依赖 nicegui】。webui.py 仍可独立跑(7869)，本服务跑 7870，互不干扰。
#
# 运行(在 test/ 目录下，把本包当包导入)：
#   python -m Floor_engine_server.server_api
#   或  uvicorn Floor_engine_server.server_api:app --host 127.0.0.1 --port 7870 --workers 1
# 必须单 worker：JOBS 注册表 / 信号量是【进程内】状态，多 worker 不共享。
# ==========================================
"""Headless FastAPI layer over the floor_engine package (no NiceGUI)."""

import os
import io
import time
import json
import math
import uuid
import base64
import asyncio
import hashlib
import threading
import mimetypes
import tempfile
from typing import Optional, List, Literal
from contextlib import asynccontextmanager

from PIL import Image, ImageChops, ImageFilter, ImageOps
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── 引擎依赖（全部包内相对导入；这些模块均与前端无关，可安全 import 而不拉进 NiceGUI）──
from .config import (
    MAIN_OUTPUT_DIR, UPLOAD_DIR, THUMB_DIR, logger,
    load_config, save_config, GEMINI_MODEL_MAP, FAL_MODEL_MAP,
    get_image_provider, save_api_key, save_provider_settings,
    get_speed_profile, save_speed_profile, get_auto_failover, save_auto_failover,
    get_tls_verify, get_tls_ca_bundle, get_proxy, get_speed_profile_params,
    safe_upload_path, extract_clean_prompt, get_bevel_ref_image, LITE_PREVIEW_MODEL,
    get_deepseek_api_key, get_deepseek_base_url, get_deepseek_model, get_omakase_enabled,
    get_omakase_gemini_model,
    save_deepseek_settings,
    update_config, get_usage_prices, get_pptx_branding,
    get_inpaint_provider, get_comfyui_settings, get_inpaint_models,
)
from .api import (
    call_image_generate, call_gemini_generate, call_gemini_edit, test_connection, analyze_style_image,
    call_omakase_scenes,
    call_fal_sd35_generate, call_fal_aura_upscale, SD35_ENDPOINT,
    FLOOR_DESEAM_INSTRUCTION, infer_aspect_ratio_from_b64,
    call_image_inpaint, resolve_inpaint_engine,
    effective_inpaint_candidate_count,
)
from .color_match import (
    match_color_to_reference, match_color_global, analyze_color_region,
)
from .prompts import save_task_files_html
from .sd_prompts import compile_sd35_prompt
from .records import (
    persist_jobs, load_persisted_jobs, record_usage,
    save_api_result_jpg, save_api_result_png, api_write_to_record,
    safe_output_path, scan_json_files, load_records_file, get_record_labels,
    reveal_prompt_fn, list_recent_floor_swatches, b64_to_pil,
    delete_record_entry, delete_result_image, toggle_result_favorite,
    update_result_review, append_edited_result_to_record, load_usage_summary,
    attach_generation_context, load_review_summary, collect_review_gallery,
    export_html_from_json, export_pptx_from_json, export_favorites_pptx,
    migrate_all_record_storage, migrate_record_file,
)
from .task_registry import TaskRegistry
from .models import (
    JobRecord, new_job, update_job, compute_final_status,
    job_time_text, running_model_status_text, add_candidate, ensure_candidate_lists,
    TaskParams, task_params_to_kwargs, ensure_model_runs, update_model_run,
    add_model_candidate, nav_model_candidate, model_run_current_path,
    compute_runs_final_status, legacy_filter_from_targets,
)
from .failure_kb import classify_failure, FAILURE_RULES
from .floor_renderer import RenderRecipe, image_sha256, render_floor, validate_calibration_quad
from .recipes import recommend_recipes, FLOOR_RECIPES, pick_option_key
from .custom_recipes import (
    list_custom_recipes, add_custom_recipe, update_custom_recipe, delete_custom_recipe,
)
from .prompt_data import (
    ROOM_TYPES, CN_ROOM_TYPES, FLOOR_TONES, CONTINENTS, PROPERTY_TYPES,
    VIEWS, STYLES, LOCATION_MAP, PET_TYPES, PET_ACTIONS, PET_FOCUS_OPTIONS,
    LIGHTINGS, ANGLES, FLOOR_SIZES, PANEL_SIZES, MARKET_FURNITURE_CHOICES, AVOID_LIST,
    CN_DEVELOPERS, CN_UNIT_TYPES, CN_TIERS, CN_DELIVERY_CHOICES,
    CN_SPACE_FEATURES, CN_FACILITIES, CN_CITIES, analyze_floor_tone,
)


# ============================================================
# 模块级编排状态（移植自 webui.py：注册表 + 按模型并发槽 + 取消标志）
# ============================================================
# 每个模型最多 max_concurrent_per_model(默认 1) 个进行中任务；B2 / Pro 各一把信号量。
# 在 lifespan 启动钩子里、于本服务事件循环上惰性创建（绝不在 import 期建，否则绑错 loop）。
_model_semaphores: dict = {}
# prep 串行锁：save_task_files_html 按小样路径派生 png/json 输出路径，同图并发首处理会抢写同一 png。
_task_prep_lock: Optional[asyncio.Lock] = None
# 后台任务强引用：asyncio 事件循环只对 task 持弱引用，无强引用者可能在完成前被 GC。
# 所有后台 task 统一经 _spawn() 排程并收进此集合，done 回调里自动清理（见 _spawn）。
_bg_tasks: set = set()
# 内存里最多保留 N 条任务卡（与磁盘 QUEUE_PERSIST_MAX 对齐）；超出丢最旧的【终态】卡，
# in-flight(queued/running/磨缝中) 永不删。
_MAX_RESIDENT_JOBS = 60


def _job_is_terminal(j: JobRecord) -> bool:
    """终态 = 已出结果且不在磨缝;queued/running/磨缝中 in-flight 永不被 trim。"""
    return j.status in ('done', 'partial', 'failed') and not j.pro_polishing


# 任务队列注册表:取消语义与 webui 同义 —— 单任务用取消集合(stop this one);
# 全局用单调代次(stop all:in-flight 任务捕获的旧代次 < 新代次即自行退出)。
# persist 经 records.persist_jobs 落盘(内部会剥掉 retry_ctx 里的 api_key,不存明文)。
JOBS = TaskRegistry('jobs', max_entries=_MAX_RESIDENT_JOBS, is_terminal=_job_is_terminal,
                    on_persist=persist_jobs, newest_first=True)


def _spawn(coro):
    """asyncio.create_task + 持强引用直到完成——避免事件循环仅持弱引用导致后台任务被 GC。
    done 回调把自身从集合移除，不留泄漏。所有后台生图/重试/重抽/磨缝/二改 task 统一走这里。"""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    def _done(task):
        _bg_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error(f"[后台任务] 未处理异常: {task.exception()}")
    t.add_done_callback(_done)
    return t


# ── 快速预览（Nano Banana 2 Lite · 1K · 仅 Google 直连）───────────────────────────
# 与 4K 队列完全解耦：不进 JOBS、不占 b2/pro 信号量、不写记录。pid → 状态快照，前端短轮询。
_MAX_PREVIEWS = 20            # 预览是临时草稿，只留最近 N 条终态


def _preview_is_terminal(v: dict) -> bool:
    return v.get('status') in ('done', 'failed')


PREVIEWS = TaskRegistry('previews', max_entries=_MAX_PREVIEWS, is_terminal=_preview_is_terminal)


# ── URL 工具（移植自 webui，realpath+commonpath 归属校验，拒绝 ../ 逃逸）──
def _to_url(p) -> str:
    if not p:
        return ''
    try:
        rp = os.path.realpath(str(p))
    except Exception:
        return ''
    for base, prefix in ((MAIN_OUTPUT_DIR, '/outputs/'), (UPLOAD_DIR, '/uploads/')):
        try:
            rbase = os.path.realpath(base)
            if os.path.commonpath([rp, rbase]) == rbase:
                return prefix + os.path.relpath(rp, rbase).replace('\\', '/')
        except Exception:
            continue
    return ''


def _thumb_url(p: str, s: int = 320) -> str:
    return f'/thumb/uploads/{os.path.basename(p)}?s={s}'


def _result_thumb_url(p, s: int = 480) -> str:
    full = _to_url(p)
    if not full.startswith('/outputs/'):
        return full
    return f'/thumb/outputs/{full[len("/outputs/"):]}?s={s}'


def _job_view(job: JobRecord) -> dict:
    """把 JobRecord 序列化成前端友好的 JSON（含实时阶段、耗时文案、结果图 URL/缩略图、候选下标）。"""
    ensure_model_runs(job)
    ensure_candidate_lists(job)   # 向后兼容 + 同步 *_path = *_paths[*_idx]
    effective_error = job.operation_error or job.error
    runs = {}
    for key in job.model_targets:
        run = job.model_runs.get(key) or {}
        paths = list(run.get('paths') or [])
        idx = max(0, min(int(run.get('index') or 0), len(paths) - 1)) if paths else 0
        current = paths[idx] if paths else ''
        runs[key] = {
            'key': key,
            'label': run.get('label') or key,
            'provider': run.get('provider') or '',
            'model_id': run.get('model_id') or '',
            'status': run.get('status') or 'queued',
            'stage': run.get('stage') or '',
            'seconds': run.get('seconds'),
            'error': run.get('error') or '',
            'url': _to_url(current),
            'thumb': _result_thumb_url(current),
            'idx': idx,
            'total': len(paths),
            'base_url': _to_url(run.get('base_path')),
            'delivery_status': run.get('delivery_status') or '',
            'seed': run.get('seed'),
            'settings': run.get('settings') or {},
        }
    return {
        'job_id': job.job_id,
        'display_name': job.display_name,
        'ts': job.ts,
        'status': job.status,
        'model_filter': job.model_filter,
        'model_targets': job.model_targets,
        'model_runs': runs,
        'workflow_mode': job.workflow_mode,
        'error': job.error,
        'error_kb': classify_failure(effective_error) if (effective_error and '取消' not in effective_error) else None,
        'b2_stage': job.b2_stage,
        'pro_stage': job.pro_stage,
        'b2_secs': job.b2_secs,
        'pro_secs': job.pro_secs,
        'time_text': job_time_text(job),
        'model_status': running_model_status_text(job),
        'pro_polishing': job.pro_polishing,
        'operation': job.operation,
        'operation_status': job.operation_status,
        'operation_error': job.operation_error,
        'has_retry': bool(job.retry_ctx),
        'record_id': job.record_id,
        'json_path': job.json_path,
        # 房间原图 URL（替换类工作流才有 rp）：供前端「前后对比」滑块
        'room_url': _to_url((job.retry_ctx or {}).get('rp')),
        # 地板小样（优化图，png_path 随队列持久化）：供前端「手动校色」参照
        'floor_url': _to_url(job.png_path),
        'floor_path': job.png_path or '',
        # 结果图：原图走 /outputs，缩略图走 /thumb/outputs（4K 原图大，列表用小图）
        'b2_url': _to_url(job.b2_path),
        'b2_thumb': _result_thumb_url(job.b2_path),
        'b2_idx': job.b2_idx,
        'b2_total': len(job.b2_paths),
        'pro_url': _to_url(job.pro_path),
        'pro_thumb': _result_thumb_url(job.pro_path),
        'pro_idx': job.pro_idx,
        'pro_total': len(job.pro_paths),
    }


# ============================================================
# 生成 worker（移植自 webui._generate_one_model / _run_job，删去所有 UI 调用）
# ============================================================
async def _generate_one_model(job: JobRecord, model_id, prompt_text, stage_key, model_name, *,
                              api_key, pnp, ims, ar, rp, sref, bevel_ref, jpt, rid, should_cancel):
    """单模型生成：占本模型并发槽 → 调引擎 → 存盘 → 写记录 → 记用量。返回 (jpg路径或None, 错误串)。
    与 webui 版逐行一致，仅删去 _refresh_job_card(UI 刷新)——进度改由 SSE 读 job.{key}_stage。"""
    _t0 = time.time()

    def _on_stage(text):
        try:
            setattr(job, f'{stage_key}_stage', text)
            update_model_run(job, stage_key, stage=text, status='running')
        except Exception as ex:
            logger.debug(f"写入阶段状态失败 job={job.job_id}: {ex}")

    sem = _model_semaphores[stage_key]
    img = err = provider = None
    try:
        if sem.locked():
            _on_stage('排队中')   # 槽被别的任务占着 → 徽章显示「排队中」
        async with sem:
            _t0 = time.time()      # 计时从真正开跑算起，不含排队等待
            img, err, provider = await asyncio.to_thread(
                call_image_generate, api_key, model_id, prompt_text,
                pnp, ims, ar, rp, sref, _on_stage, should_cancel, bevel_ref)
    except Exception as e:
        img, err, provider = None, str(e), get_image_provider()
    finally:
        try:
            setattr(job, f'{stage_key}_stage', '')
            update_model_run(job, stage_key, stage='')
        except Exception:
            pass
    setattr(job, f'{stage_key}_secs', round(time.time() - _t0, 1))
    update_model_run(job, stage_key, seconds=getattr(job, f'{stage_key}_secs'))

    path = None
    if img is not None:
        # 图已生成 = 已计费 → 即便随后判定取消也先存盘，避免白花钱（与 webui 同序）
        path = save_api_result_jpg(img, model_name, pnp)
        if not path:
            record_usage(job.workflow_mode, model_name, provider, True, job.operation)
            return None, '图片已生成，但保存到磁盘失败'
        add_model_candidate(job, stage_key, path)
        try:
            await asyncio.to_thread(api_write_to_record, img, model_name, jpt, rid, path)
        except Exception as ex:
            logger.warning(f"写记录失败 job={job.job_id} {model_name}: {ex}")

    _err = err or ''
    # 出图=ok；没出图且非取消=fail（取消不计失败）。provider 用引擎返回的【实际】线路(自动转 Fal 能记对)。
    if img is not None or '取消' not in _err:
        try:
            record_usage(job.workflow_mode, model_name, provider, img is not None, job.operation)
        except Exception as ex:
            logger.debug(f"记录用量失败 job={job.job_id}: {ex}")
    update_model_run(job, stage_key, status=('done' if path else 'failed'), error=_err, provider=provider or '')
    return path, _err


def _model_queue_handle(job: JobRecord, key: str, name: str) -> dict:
    ensure_model_runs(job)
    settings = dict((job.model_runs.get(key) or {}).get('settings') or {})
    value = settings.get(name)
    return dict(value) if isinstance(value, dict) else {}


def _set_model_queue_handle(job: JobRecord, key: str, name: str, handle) -> None:
    """队列提交一成功就原子持久化句柄；重启后的 retry 会恢复同一供应商请求。"""
    run = update_model_run(job, key)
    settings = dict(run.get('settings') or {})
    if handle:
        settings[name] = dict(handle)
    else:
        settings.pop(name, None)
    update_model_run(job, key, settings=settings)
    JOBS.persist()


def _fal_queue_is_terminal_error(err: str) -> bool:
    text = str(err or '').upper()
    return 'FAL 队列任务FAILED' in text or 'FAL 队列任务CANCELLED' in text


async def _generate_sd35_model(job: JobRecord, *, fal_key: str, positive: str, negative: str,
                               pnp: str, ims: str, ar: str, jpt: str, rid: str,
                               options: dict, should_cancel):
    """SD3.5 基础图 + AuraSR 交付图；超分失败保留基础图并标 partial。"""
    key = 'sd35'
    ensure_model_runs(job)
    sem = _model_semaphores[key]
    started = time.time()
    sd_usage_recorded = False
    previous = (job.model_runs or {}).get(key) or {}
    previous_base_path = str(previous.get('base_path') or '')
    # retry 可承接「SD 已落盘、AuraSR 未完成」的现场；regen 必须重新生成，不能复用旧基础图。
    existing_base = (previous_base_path
                     if job.operation != 'regen' and os.path.isfile(previous_base_path) else '')

    def _stage(text):
        update_model_run(job, key, stage=text, status='running')

    if sem.locked():
        _stage('排队中')
    prior_settings = dict(previous.get('settings') or {})
    if job.operation == 'regen':
        # 旧 run 可能保留了超时但仍可轮询的请求；重抽语义是新请求，不能承接旧队列。
        prior_settings.pop('sd_queue', None)
        prior_settings.pop('upscale_queue', None)
    prior_settings.update({k: options.get(k) for k in ('steps', 'guidance_scale', 'reference_strength')})
    update_model_run(job, key, model_id=SD35_ENDPOINT, provider='fal', status='running', error='',
                     settings=prior_settings)
    try:
        async with sem:
            if existing_base:
                # 程序可能在 SD 已落盘、AuraSR 未完成时重启；直接复用基础图，绝不重交 SD。
                sd_usage_recorded = True
                base = Image.open(existing_base); base.load()
                base_path = existing_base
                used_seed = previous.get('seed')
            else:
                base, err, used_seed = await asyncio.to_thread(
                    call_fal_sd35_generate, fal_key, positive, negative, pnp, ar,
                    seed=options.get('seed'), steps=options.get('steps', 28),
                    guidance_scale=options.get('guidance_scale', 3.5),
                    reference_strength=options.get('reference_strength', 0.5),
                    on_stage=_stage, should_cancel=should_cancel,
                    queue_handle=_model_queue_handle(job, key, 'sd_queue'),
                    on_queue_submitted=lambda h: _set_model_queue_handle(job, key, 'sd_queue', h),
                )
                update_model_run(job, key, seed=used_seed)
                if base is None:
                    if _fal_queue_is_terminal_error(err) or '取消' in str(err or ''):
                        _set_model_queue_handle(job, key, 'sd_queue', None)
                    if '取消' not in str(err or ''):
                        record_usage(job.workflow_mode, 'SD35', 'fal', False, job.operation)
                        sd_usage_recorded = True
                    update_model_run(job, key, status='failed', error=str(err or 'SD 生成失败'), stage='',
                                     seconds=round(time.time() - started, 1))
                    return None, str(err or 'SD 生成失败')

                # API 已成功出图即产生费用；磁盘保存失败也必须按成功调用计成本。
                record_usage(job.workflow_mode, 'SD35', 'fal', True, job.operation)
                sd_usage_recorded = True
                base_path = save_api_result_png(base, 'SD35_Base', pnp)
                if not base_path:
                    update_model_run(job, key, status='failed', error='基础图保存失败', stage='')
                    return None, 'SD 基础图已生成，但保存失败'
                update_model_run(job, key, base_path=base_path, delivery_status='base_ready')
                JOBS.persist()  # AuraSR 前先确保基础图可在重启后恢复
                _set_model_queue_handle(job, key, 'sd_queue', None)

            final_image = base
            final_path = base_path
            upscale_error = ''
            target_long = 4096 if str(ims).upper().startswith('4') else (2048 if str(ims).upper().startswith('2') else 0)
            if target_long:
                upscaled, up_err = await asyncio.to_thread(
                    call_fal_aura_upscale, fal_key, base, on_stage=_stage, should_cancel=should_cancel,
                    queue_handle=_model_queue_handle(job, key, 'upscale_queue'),
                    on_queue_submitted=lambda h: _set_model_queue_handle(job, key, 'upscale_queue', h))
                if upscaled is None:
                    upscale_error = f'{target_long // 1024}K 超分失败：{up_err}'
                    if _fal_queue_is_terminal_error(up_err) or '取消' in str(up_err or ''):
                        _set_model_queue_handle(job, key, 'upscale_queue', None)
                    if '取消' not in str(up_err or ''):
                        record_usage(job.workflow_mode, 'AuraSR', 'fal', False, 'upscale')
                else:
                    record_usage(job.workflow_mode, 'AuraSR', 'fal', True, 'upscale')
                    scale = target_long / max(upscaled.size)
                    if scale < 0.999:
                        upscaled = upscaled.resize(
                            (max(1, round(upscaled.width * scale)), max(1, round(upscaled.height * scale))),
                            Image.Resampling.LANCZOS,
                        )
                    saved_upscale = save_api_result_png(upscaled, 'SD35_4K', pnp)
                    if saved_upscale:
                        final_image = upscaled
                        final_path = saved_upscale
                        _set_model_queue_handle(job, key, 'upscale_queue', None)
                    else:
                        upscale_error = '超分已完成但保存失败，已保留基础图'
            add_model_candidate(job, key, final_path)
            metadata = {
                'provider': 'fal', 'model': SD35_ENDPOINT, 'seed': used_seed,
                'steps': options.get('steps', 28), 'guidance_scale': options.get('guidance_scale', 3.5),
                'reference_strength': options.get('reference_strength', 0.5),
                'base_image_file': os.path.relpath(base_path, MAIN_OUTPUT_DIR).replace('\\', '/'),
                'prompt_sha256': hashlib.sha256(positive.encode()).hexdigest(),
                'negative_prompt_sha256': hashlib.sha256(negative.encode()).hexdigest(),
            }
            try:
                await asyncio.to_thread(api_write_to_record, final_image, 'SD 3.5', jpt, rid, final_path, metadata)
            except Exception as ex:
                logger.warning(f'写 SD 记录失败 job={job.job_id}: {ex}')
            update_model_run(
                job, key, status=('partial' if upscale_error else 'done'), error=upscale_error,
                stage='', seconds=round(time.time() - started, 1),
                delivery_status=('upscale_failed' if upscale_error else ('upscaled' if target_long else 'base_ready')),
            )
            return final_path, upscale_error
    except Exception as ex:
        logger.exception(f'[SD35] 生成异常 job={job.job_id}')
        update_model_run(job, key, status='failed', error=str(ex), stage='', seconds=round(time.time() - started, 1))
        if not sd_usage_recorded:
            record_usage(job.workflow_mode, 'SD35', 'fal', False, job.operation)
        return None, str(ex)


async def _run_job_bg(job: JobRecord, req: 'JobSubmitRequest'):
    """主生图编排（移植自 webui._run_job 去 UI）：prep 串行 → 接缝/倒角规则 → 按模型并发生成 → 终态判定。"""
    jid = job.job_id
    generation = JOBS.generation
    mf = job.model_filter
    targets = list(job.model_targets or [])
    run_b2 = 'b2' in targets
    run_pro = 'pro' in targets
    run_sd = 'sd35' in targets
    p = req.params
    cfg = load_config()
    api_key = (req.api_key or '').strip() or cfg.get('gemini_api_key', '').strip()
    fal_key = (cfg.get('fal_api_key') or '').strip()

    if JOBS.is_cancelled(jid, generation):
        update_job(job, status='failed', error='已取消（用户停止）',
                   operation='generate', operation_status='cancelled', operation_error='已取消')
        JOBS.clear_cancelled(jid)
        JOBS.persist()
        return

    update_job(job, status='running', started_at=time.time(), operation='generate',
               operation_status='running', operation_error='')
    b2j = proj = None
    try:
        is_ref_mode = '参照模式' in (p.workflow_mode or '')
        is_panel = '墙板' in (p.workflow_mode or '')
        _psub = p.panel_submode or ''
        # 墙板·再设计复用参照模式的风格分析(analyze_style_image 通用、非地板专用)
        _panel_redesign = is_panel and ('再设计' in _psub or _psub == '')

        # 参照模式 / 墙板再设计 Step-1：提取风格描述（失败即中止，不发起计费生图）
        style_text = ''
        if (is_ref_mode or _panel_redesign) and req.ref_path:
            style_text, sa_err = await asyncio.to_thread(analyze_style_image, api_key, req.ref_path)
            if sa_err or not style_text:
                update_job(job, status='failed',
                           error=f'风格分析失败，已中止（未发起生图）: {sa_err or "返回为空"}',
                           operation_status='failed', operation_error=sa_err or '返回为空')
                return

        # 组装提示词参数 → save_task_files_html（prep 串行：同张小样并发任务共享输出路径）
        tp = TaskParams(image_path=req.image_path, style_analysis_text=style_text, **p.model_dump())
        async with _task_prep_lock:
            (_pil, sms, prt, saved_image_path, jpt, rid, pnp, prt_pro) = await asyncio.to_thread(
                save_task_files_html, **task_params_to_kwargs(tp))
        logger.info(f"[API任务] prompt_saved job={jid}, record={rid}, json={jpt}, png={pnp}")

        cpt = extract_clean_prompt(prt)
        ar = (p.aspect_ratio or '4:3').split(' ')[0]
        cpt_pro = extract_clean_prompt(prt_pro) if prt_pro else cpt
        sd_bundle = compile_sd35_prompt(
            tp,
            positive_addition=req.sd_options.positive_addition,
            negative_addition=req.sd_options.negative_addition,
        ) if run_sd else None
        # B2 也用 Pro 终极指令（实测无缝效果最佳）；唯「圆弧倒角·直拼」例外，B2 保留自己的软细缝词。
        _seam_v = p.seam_type or ''
        _size_v = p.floor_size or ''
        _is_straight_bevel = ('圆弧倒角' in _seam_v and '无缝' not in _seam_v
                              and '人字拼' not in _size_v and '正方形拼' not in _size_v)
        if not _is_straight_bevel:
            cpt = cpt_pro
        ims = (p.resolution or '4K').split(' ')[0]
        # 图片通道：sref=风格参照图, rp=房间/场景底图。
        # 参照模式：参照图当 sref。墙板：再设计→场景参照图当 sref；替换→原墙板场景图当 rp；纯原创→两者皆空。
        if is_panel:
            rp = (req.room_path or None) if '替换' in _psub else None
            sref = (req.ref_path or None) if _panel_redesign else None
        else:
            rp = None if is_ref_mode else (req.room_path or None)
            sref = (req.ref_path or None) if is_ref_mode else None
        # 圆弧倒角(任意拼法)：自动附内置倒角参考图（只供模型抄板边圆弧形状）；B2/Pro 同带。
        _is_pressed_bevel = ('圆弧倒角' in _seam_v and '无缝' not in _seam_v)
        bevel_ref = get_bevel_ref_image() if _is_pressed_bevel else None

        # 存生成上下文，供「重试」只重跑未成图的模型（持久化时 api_key 会被剥除）
        job.retry_ctx = dict(api_key=api_key, pnp=pnp, cpt=cpt, cpt_pro=cpt_pro,
                             sd_positive=(sd_bundle.positive if sd_bundle else ''),
                             sd_negative=(sd_bundle.negative if sd_bundle else ''),
                             sd_options=req.sd_options.model_dump(),
                             ims=ims, ar=ar, rp=rp, sref=sref, bevel_ref=bevel_ref,
                             jpt=jpt, rid=rid, model_filter=mf, model_targets=targets)
        update_job(job, json_path=jpt, record_id=rid, png_path=pnp)

        # gen_context 快照：完整入参落记录 JSON，供「复用参数」「前后对比」。绝不含 api_key。
        try:
            await asyncio.to_thread(attach_generation_context, jpt, rid, dict(
                image_path=req.image_path, room_path=req.room_path or '',
                ref_path=req.ref_path or '', model_filter=mf, model_targets=targets,
                sd_options=req.sd_options.model_dump() if run_sd else None, params=p.model_dump()))
        except Exception as ex:
            logger.warning(f"[API任务] gen_context 写入失败 job={jid}: {ex}")

        if JOBS.is_cancelled(jid, generation):
            update_job(job, status='failed', error='已取消（用户停止）')
            return

        should_cancel = lambda: JOBS.is_cancelled(jid, generation)

        def _gen_one(model_id, prompt_text, stage_key, model_name):
            return _generate_one_model(job, model_id, prompt_text, stage_key, model_name,
                                       api_key=api_key, pnp=pnp, ims=ims, ar=ar, rp=rp, sref=sref,
                                       bevel_ref=bevel_ref, jpt=jpt, rid=rid, should_cancel=should_cancel)

        tasks = []
        if run_b2:
            tasks.append(('b2', _gen_one(GEMINI_MODEL_MAP['Nano Banana 2'], cpt, 'b2', 'Nano Banana 2')))
        if run_pro:
            tasks.append(('pro', _gen_one(GEMINI_MODEL_MAP['Nano Banana Pro'], cpt_pro, 'pro', 'Nano Banana Pro')))
        if run_sd and sd_bundle:
            tasks.append(('sd35', _generate_sd35_model(
                job, fal_key=fal_key, positive=sd_bundle.positive, negative=sd_bundle.negative,
                pnp=pnp, ims=ims, ar=ar, jpt=jpt, rid=rid,
                options=req.sd_options.model_dump(), should_cancel=should_cancel,
            )))
        results = await asyncio.gather(*[c for _, c in tasks], return_exceptions=True)

        errors = []
        for (k, _), res in zip(tasks, results):
            if isinstance(res, Exception):
                errors.append(f'{k.upper()}: {res}')
                update_model_run(job, k, status='failed', error=str(res), stage='')
            else:
                _path, _e = res
                if k == 'b2':
                    b2j = _path
                elif k == 'pro':
                    proj = _path
                if _e:
                    errors.append(f'{k.upper()}: {_e}')

        if JOBS.is_cancelled(jid, generation):
            # 取消后：已返回的图(已计费)已在 _gen_one 里存盘，这里据实标注，不丢弃
            final = compute_runs_final_status(job)
            if final in ('done', 'partial'):
                update_job(job, status=final,
                           b2_path=b2j, pro_path=proj, error='已取消，但已出图已保留（已付费）',
                           operation_status='cancelled', operation_error='已取消')
            else:
                update_job(job, status='failed', error='已取消（无结果）',
                           operation_status='cancelled', operation_error='已取消')
            return

        err_msg = ' '.join(errors)
        final = compute_runs_final_status(job)
        update_job(job, status=final, b2_path=b2j, pro_path=proj, error=err_msg.strip(),
                   operation_status=('done' if final in ('done', 'partial') else 'failed'),
                   operation_error=err_msg.strip())
        logger.info(f"[API任务] finished job={jid}, status={final}, targets={targets}")
    except Exception as e:
        logger.exception(f"[API任务] unhandled job={jid}")
        update_job(job, status=compute_runs_final_status(job), b2_path=b2j, pro_path=proj, error=str(e),
                   operation_status='failed', operation_error=str(e))
    finally:
        JOBS.clear_cancelled(jid)
        JOBS.persist()


async def _retry_bg(job: JobRecord):
    """重试：用 retry_ctx 只重跑还没出图的模型（移植 webui._retry_job 去 UI）。"""
    ctx = job.retry_ctx or {}
    api_key = (ctx.get('api_key') or '').strip() or load_config().get('gemini_api_key', '').strip()
    fal_key = (load_config().get('fal_api_key') or '').strip()
    targets = list(ctx.get('model_targets') or job.model_targets)
    need_b2 = 'b2' in targets and not (job.b2_path and os.path.exists(str(job.b2_path)))
    need_pro = 'pro' in targets and not (job.pro_path and os.path.exists(str(job.pro_path)))
    need_sd = 'sd35' in targets and not model_run_current_path(job, 'sd35')
    if not (need_b2 or need_pro or need_sd):
        update_job(job, status=compute_runs_final_status(job), operation='retry', operation_status='done')
        JOBS.persist()
        return
    JOBS.clear_cancelled(job.job_id)   # 清掉残留取消标记，否则 should_cancel 恒 True
    generation = JOBS.generation
    update_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='',
               operation='retry', operation_status='running', operation_error='')
    try:
        should_cancel = lambda: JOBS.is_cancelled(job.job_id, generation)

        def _retry_one(model_id, prompt_text, stage_key, model_name):
            return _generate_one_model(job, model_id, prompt_text, stage_key, model_name,
                                       api_key=api_key, pnp=ctx['pnp'], ims=ctx['ims'], ar=ctx['ar'],
                                       rp=ctx['rp'], sref=ctx['sref'], bevel_ref=ctx.get('bevel_ref'),
                                       jpt=ctx['jpt'], rid=ctx['rid'], should_cancel=should_cancel)

        tasks = []
        if need_b2:
            tasks.append(('b2', _retry_one(GEMINI_MODEL_MAP['Nano Banana 2'], ctx['cpt'], 'b2', 'Nano Banana 2')))
        if need_pro:
            tasks.append(('pro', _retry_one(GEMINI_MODEL_MAP['Nano Banana Pro'], ctx['cpt_pro'], 'pro', 'Nano Banana Pro')))
        if need_sd:
            tasks.append(('sd35', _generate_sd35_model(
                job, fal_key=fal_key, positive=ctx.get('sd_positive', ''), negative=ctx.get('sd_negative', ''),
                pnp=ctx['pnp'], ims=ctx['ims'], ar=ctx['ar'], jpt=ctx['jpt'], rid=ctx['rid'],
                options=dict(ctx.get('sd_options') or {}), should_cancel=should_cancel,
            )))
        results = await asyncio.gather(*[c for _, c in tasks], return_exceptions=True)
        errs = []
        for (k, _), res in zip(tasks, results):
            e = str(res) if isinstance(res, Exception) else res[1]
            if e and '取消' not in e:
                errs.append(f'{k.upper()}: {e}')
        final = compute_runs_final_status(job)
        op_error = ('；'.join(errs)).strip()
        cancelled = JOBS.is_cancelled(job.job_id, generation)
        update_job(job, status=final, error=op_error,
                   operation_status=('cancelled' if cancelled else ('failed' if op_error else 'done')),
                   operation_error=('已取消' if cancelled else op_error))
    except Exception as e:
        logger.exception(f"[API重试] unhandled job={job.job_id}")
        update_job(job, status=compute_runs_final_status(job), error=str(e),
                   operation_status='failed', operation_error=str(e))
    finally:
        JOBS.clear_cancelled(job.job_id)
        JOBS.persist()


async def _retry_sd_upscale_bg(job: JobRecord):
    run = (job.model_runs or {}).get('sd35') or {}
    base_path = run.get('base_path') or ''
    fal_key = (load_config().get('fal_api_key') or '').strip()
    generation = JOBS.generation
    try:
        update_model_run(job, 'sd35', status='running', stage='🔎 4K 超分中…', error='')
        base = Image.open(base_path); base.load()
        async with _model_semaphores['sd35']:
            out, err = await asyncio.to_thread(
                call_fal_aura_upscale, fal_key, base,
                on_stage=lambda t: update_model_run(job, 'sd35', stage=t),
                should_cancel=lambda: JOBS.is_cancelled(job.job_id, generation),
                queue_handle=_model_queue_handle(job, 'sd35', 'upscale_queue'),
                on_queue_submitted=lambda h: _set_model_queue_handle(job, 'sd35', 'upscale_queue', h),
            )
        if out is None:
            if _fal_queue_is_terminal_error(err) or '取消' in str(err or ''):
                _set_model_queue_handle(job, 'sd35', 'upscale_queue', None)
            cancelled = '取消' in str(err or '') or JOBS.is_cancelled(job.job_id, generation)
            if not cancelled:
                record_usage(job.workflow_mode, 'AuraSR', 'fal', False, 'upscale')
            label = '已取消' if cancelled else f'SD 超分失败：{err}'
            update_model_run(job, 'sd35', status='partial', stage='', error=label,
                             delivery_status='upscale_failed')
            update_job(job, status='partial',
                       operation_status=('cancelled' if cancelled else 'failed'), operation_error=label)
            return
        record_usage(job.workflow_mode, 'AuraSR', 'fal', True, 'upscale')
        ims = str((job.retry_ctx or {}).get('ims') or '4K')
        target_long = 4096 if ims.upper().startswith('4') else 2048
        scale = target_long / max(out.size)
        if scale < 0.999:
            out = out.resize((round(out.width * scale), round(out.height * scale)), Image.Resampling.LANCZOS)
        path = save_api_result_png(out, 'SD35_4K', job.png_path or base_path)
        if not path:
            raise RuntimeError('超分图保存失败')
        add_model_candidate(job, 'sd35', path)
        _set_model_queue_handle(job, 'sd35', 'upscale_queue', None)
        update_model_run(job, 'sd35', status='done', stage='', error='', delivery_status='upscaled')
        try:
            await asyncio.to_thread(api_write_to_record, out, 'SD 3.5 · 4K重试', job.json_path, job.record_id, path)
        except Exception as ex:
            logger.warning(f'写超分重试记录失败 job={job.job_id}: {ex}')
        update_job(job, status=compute_runs_final_status(job), operation_status='done', operation_error='')
    except Exception as ex:
        logger.exception(f'[SD35] 重试超分失败 job={job.job_id}')
        update_model_run(job, 'sd35', status='partial', stage='', error=str(ex), delivery_status='upscale_failed')
        update_job(job, status='partial', operation_status='failed', operation_error=str(ex))
    finally:
        JOBS.clear_cancelled(job.job_id)
        JOBS.persist()


async def _edit_bg(job: JobRecord, *, api_key, instruction, model_id, model_label,
                   preserve, image_size, color_match=False):
    """对 job 现有 Pro(或 B2)成图做一次图生图编辑/磨缝（移植 webui._polish_pro 去 UI）。
    color_match=True 时把结果色彩对齐回原图（磨缝消偏色）；自定义编辑保留模型输出色彩。"""
    jid = job.job_id
    generation = JOBS.generation
    src_path = job.pro_path or job.b2_path
    base_status = compute_final_status(job.model_filter, job.b2_path, job.pro_path)
    if not src_path or not os.path.exists(str(src_path)):
        update_job(job, status=base_status, operation_status='failed',
                   operation_error='没有可编辑的成图', pro_polishing=False, pro_stage='')
        JOBS.clear_cancelled(jid)
        JOBS.persist()
        return
    update_job(job, started_at=time.time(), pro_polishing=True, pro_stage='', operation_status='running')

    def _on_stage(t):
        try:
            job.pro_stage = t
        except Exception:
            pass

    try:
        src_pil = Image.open(src_path)
        src_pil.load()
        buf = io.BytesIO()
        src_pil.convert('RGB').save(buf, format='JPEG', quality=95)
        b64 = base64.b64encode(buf.getvalue()).decode()
        ar = infer_aspect_ratio_from_b64(b64)
        if _model_semaphores['pro'].locked():
            _on_stage('排队中')
        async with _model_semaphores['pro']:
            out, err = await asyncio.to_thread(
                call_gemini_edit, api_key, model_id, instruction, b64,
                image_size, ar, preserve, _on_stage, lambda: JOBS.is_cancelled(jid, generation))
        if out is None:
            record_usage(job.workflow_mode, model_label, 'google', False, job.operation)
            update_job(job, status=base_status, operation_status='failed', operation_error=f'编辑失败：{err}')
            return
        if color_match:
            try:
                out = await asyncio.to_thread(match_color_to_reference, out, src_pil)
            except Exception as ex:
                logger.warning(f"[编辑] 色彩对齐失败(用未对齐图) job={jid}: {ex}")
        ppath = save_api_result_jpg(out, model_label, job.png_path or src_path)
        if not ppath:
            record_usage(job.workflow_mode, model_label, 'google', False, job.operation)
            update_job(job, status=base_status, operation_status='failed', operation_error='编辑结果保存失败')
            return
        add_candidate(job, 'pro', ppath)   # 结果并入 Pro 候选，‹n/N› 可切回原图对比
        record_usage(job.workflow_mode, model_label, 'google', True, job.operation)
        if job.json_path and job.record_id:
            try:
                await asyncio.to_thread(api_write_to_record, out, model_label, job.json_path, job.record_id, ppath)
            except Exception as ex:
                logger.warning(f"[编辑] 写记录失败 job={jid}: {ex}")
        update_job(job, status=compute_final_status(job.model_filter, job.b2_path, ppath),
                   pro_path=ppath, operation_status='done', operation_error='')
        logger.info(f"[API编辑] 完成 job={jid}, label={model_label}, path={ppath}")
    except Exception as e:
        logger.exception(f"[API编辑] 异常 job={jid}")
        update_job(job, status=base_status, operation_status='failed', operation_error=str(e))
    finally:
        update_job(job, pro_polishing=False, pro_stage='')
        JOBS.clear_cancelled(jid)
        JOBS.persist()


# ============================================================
# 请求/响应模型
# ============================================================
class GenParams(BaseModel):
    """镜像 models.TaskParams（除 image_path / style_analysis_text 由服务端填）。"""
    model_config = {'protected_namespaces': ()}   # 允许 model_choice 等 model_* 字段（避开 pydantic v2 保留命名空间）

    workflow_mode: str
    model_choice: str = 'Pro'
    continent: str = '欧洲'
    country: str = ''
    city: str = ''
    neighborhood: str = ''
    property_type: str = '现代别墅'
    room_type: str = '客餐厅一体'
    view: str = '自然通透景观'
    style_type: str = ''
    lighting: str = ''
    floor_tone: str = ''
    floor_size: str = ''
    seam_type: str = '无缝拼接 (SPC/LVT专用)'
    glossiness: str = '哑光 (3-5°)'
    angle: str = '28mm lens (Wide)'
    aspect_ratio: str = '4:3'
    resolution: str = '4K'
    avoid_items: List[str] = []
    custom_addition: str = ''
    pet_type: str = ''
    pet_action: str = ''
    pet_focus: str = ''
    market_furniture: str = ''
    last_image_path: str = ''
    cn_mode: bool = False
    cn_developer: str = '── 不指定 ──'
    cn_city: str = '上海'
    cn_tier: str = '── 不指定 ──'
    cn_unit_type: str = '── 不指定 ──'
    cn_delivery: str = '🏆 样板间 / 展示单位'
    cn_room_type: str = '客餐厅一体'
    cn_view: str = '自然通透景观'
    cn_space_features: Optional[List[str]] = None
    cn_facilities: Optional[List[str]] = None
    style_ref_correction: str = ''
    scene_override: str = ''   # Omakase 模式：AI 原创场景散文，接管整个场景层(仅 Omakase 工作流生效)
    panel_submode: str = '再设计'   # 墙板模式子行为：再设计 / 替换 / 纯原创(仅墙板模式生效)
    panel_size: str = ''            # 墙板尺寸/板型(预设或自定义；仅墙板再设计/纯原创生效)


class SDOptions(BaseModel):
    seed: Optional[int] = Field(default=None, ge=0)
    steps: int = Field(default=28, ge=10, le=50)
    guidance_scale: float = Field(default=3.5, ge=1.0, le=10.0)
    reference_strength: float = Field(default=0.5, ge=0.1, le=1.0)
    positive_addition: str = Field(default='', max_length=1000)
    negative_addition: str = Field(default='', max_length=1000)


class JobSubmitRequest(BaseModel):
    model_config = {'protected_namespaces': ()}   # 允许 model_filter

    image_path: str                       # /api/uploads/floor 返回的绝对路径
    model_filter: Literal['b2', 'pro', 'both'] = 'both'
    model_targets: Optional[List[Literal['b2', 'pro', 'sd35']]] = None
    sd_options: SDOptions = Field(default_factory=SDOptions)
    api_key: str = ''                     # 缺省回退 engine_config.json 里的 gemini_api_key
    room_path: Optional[str] = None       # 房间替换图（地板替换流程）
    ref_path: Optional[str] = None        # 参照模式参考图
    params: GenParams


class PreviewRequest(BaseModel):
    """快速预览（NB2 Lite · 1K）：字段同 JobSubmit 但无 model_filter（预览恒用 Lite）。"""
    model_config = {'protected_namespaces': ()}

    image_path: str
    api_key: str = ''
    room_path: Optional[str] = None
    ref_path: Optional[str] = None
    params: GenParams


class EditRequest(BaseModel):
    model_config = {'protected_namespaces': ()}

    instruction: str = Field(min_length=1, max_length=2000)
    api_key: str = ''
    image_size: Literal['2K', '4K'] = '4K'
    preserve_floor_geometry: bool = True
    model_choice: Literal['Nano Banana 2', 'Nano Banana Pro'] = 'Nano Banana Pro'
    # 保持原图色彩（防偏色）：二改后把整体色温/饱和度拉回原图。默认开=对齐旧 NiceGUI 语义
    color_match: bool = True


class RevealRequest(BaseModel):
    json_path: str
    record_id: str
    password: str


class ErrRequest(BaseModel):
    err: str


class ConfigPatch(BaseModel):
    gemini_api_key: Optional[str] = Field(default=None, max_length=500)
    fal_api_key: Optional[str] = Field(default=None, max_length=500)
    image_provider: Optional[Literal['google', 'fal']] = None
    speed_profile: Optional[Literal['fast', 'resilient']] = None
    auto_failover: Optional[bool] = None
    proxy: Optional[str] = Field(default=None, max_length=1000)
    fal_queue_proxy: Optional[str] = Field(default=None, max_length=1000)
    tls_verify: Optional[bool] = None
    tls_ca_bundle: Optional[str] = Field(default=None, max_length=2000)
    max_concurrent_per_model: Optional[int] = Field(default=None, ge=1, le=8)
    sd_enabled: Optional[bool] = None
    # 生成式修补引擎：fal=云 API（remove/add 分模型）；comfyui=用户自备 ComfyUI 实例
    inpaint_provider: Optional[Literal['fal', 'comfyui']] = None
    inpaint_remove_model: Optional[Literal['bria-eraser', 'finegrain-eraser', 'lama',
                                           'flux-fill', 'gemini-mark']] = None
    inpaint_add_model: Optional[Literal['flux-fill', 'qwen-inpaint', 'gemini-mark']] = None
    comfyui_base_url: Optional[str] = Field(default=None, max_length=500)
    comfyui_workflow_path: Optional[str] = Field(default=None, max_length=1000)
    comfyui_timeout: Optional[int] = Field(default=None, ge=60, le=3600)
    inpaint_remove_prompt: Optional[str] = Field(default=None, max_length=1000)
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: Optional[str] = None
    deepseek_model: Optional[str] = None
    omakase_gemini_model: Optional[str] = Field(default=None, max_length=200)
    omakase_enabled: Optional[bool] = None
    # 成本估算单价：{'B2': 0.1, 'Pro': 0.5, 'B2:fal': 0.12,...}（元/张成功图）；负数拒绝
    usage_prices: Optional[dict] = None
    # PPTX 导出品牌（logo 走 POST /api/uploads/logo，不在此 patch）
    pptx_company: Optional[str] = Field(default=None, max_length=200)
    pptx_contact: Optional[str] = Field(default=None, max_length=200)


def _config_view() -> dict:
    cfg = load_config()
    brand = get_pptx_branding()
    return {
        'has_gemini_key': bool((cfg.get('gemini_api_key') or '').strip()),
        'has_fal_key': bool((cfg.get('fal_api_key') or '').strip()),
        'image_provider': get_image_provider(),
        'speed_profile': get_speed_profile(),
        'auto_failover': get_auto_failover(),
        'tls_verify': get_tls_verify(),
        'tls_ca_bundle': get_tls_ca_bundle(),
        'proxy': get_proxy(),
        'fal_queue_proxy': str(cfg.get('fal_queue_proxy') or ''),
        'max_concurrent_per_model': int(cfg.get('max_concurrent_per_model', 1) or 1),
        'sd_enabled': bool(cfg.get('sd_enabled', False)),
        'inpaint_provider': get_inpaint_provider(),
        'inpaint_remove_model': get_inpaint_models()['remove'],
        'inpaint_add_model': get_inpaint_models()['add'],
        'comfyui_base_url': get_comfyui_settings()['base_url'],
        'comfyui_workflow_path': get_comfyui_settings()['workflow_path'],
        'comfyui_timeout': get_comfyui_settings()['timeout'],
        'inpaint_remove_prompt': str(cfg.get('inpaint_remove_prompt') or ''),
        'speed_params': get_speed_profile_params(cfg),
        # Omakase：Gemini 复用主 Key，DeepSeek 只回是否已配置，绝不回明文 key
        'has_deepseek_key': bool((cfg.get('deepseek_api_key') or '').strip()),
        'omakase_enabled': get_omakase_enabled(),
        'omakase_gemini_model': get_omakase_gemini_model(),
        'deepseek_model': get_deepseek_model(),
        'deepseek_base_url': get_deepseek_base_url(),
        'usage_prices': get_usage_prices(),
        'pptx_company': brand['company'],
        'pptx_contact': brand['contact'],
        'pptx_logo_url': _to_url(brand['logo_path']),
    }


# ============================================================
# FastAPI app + 生命周期
# ============================================================
@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model_semaphores, _task_prep_lock
    try:
        lim = max(1, int(load_config().get('max_concurrent_per_model', 1)))
    except Exception as ex:
        logger.warning(f"读取 max_concurrent_per_model 失败，用默认 1: {ex}")
        lim = 1
    _model_semaphores = {key: asyncio.Semaphore(lim) for key in ('b2', 'pro', 'sd35')}
    # 生成式修补独立信号量(恒 1)：修补与主生成互不阻塞、也不占 b2/pro 槽
    _model_semaphores['inpaint'] = asyncio.Semaphore(1)
    _task_prep_lock = asyncio.Lock()
    migrated = migrate_all_record_storage()
    JOBS.replace((j.job_id, j) for j in load_persisted_jobs())   # 启动恢复；中断态已被修正为 partial/failed
    logger.info(f"[server_api] 启动完成：迁移 {migrated} 个记录文件，恢复 {len(JOBS)} 条历史任务，每模型并发 {lim}")
    yield


app = FastAPI(title="Floor Engine API", version="step1", lifespan=lifespan)

# CORS：开给前端 dev origin。绑 127.0.0.1，本机自用，不放公网。
_ALLOWED_ORIGINS = [o.strip() for o in
                    os.environ.get('FLOOR_API_CORS', 'http://localhost:3000,http://127.0.0.1:3000').split(',')
                    if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def reject_cross_origin_mutations(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
        # 同源豁免：浏览器对一切 POST 都带 Origin 头。生产静态站由本后端同源托管，
        # 同源 mutation 不是跨域攻击面；不豁免会把同源部署的所有写操作全部 403。
        same_origin = origin == f'{request.url.scheme}://{request.url.netloc}'
        if not same_origin and origin not in _ALLOWED_ORIGINS:
            return Response('Forbidden origin', status_code=403)
    return await call_next(request)


# ── 健康检查 ──
@app.get('/api/healthz')
def healthz():
    return {'ok': True}


def _require_record_json_path(json_path: str) -> str:
    """Resolve a client-provided record path and keep it inside output_files."""
    if not json_path:
        raise HTTPException(400, '记录路径为空')
    try:
        base = os.path.realpath(MAIN_OUTPUT_DIR)
        path = os.path.realpath(json_path)
        if os.path.commonpath([base, path]) != base:
            raise HTTPException(400, '记录路径不在 output_files 内')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, '记录路径无效')
    if not os.path.basename(path).endswith('_记录.json'):
        raise HTTPException(400, '记录文件名无效')
    if not os.path.exists(path):
        raise HTTPException(404, '记录文件不存在')
    return path


_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_MAX_UPLOAD_PIXELS = 80_000_000


def _require_upload_image_path(path: Optional[str], label: str, *, required: bool = False) -> Optional[str]:
    if not path:
        if required:
            raise HTTPException(400, f'{label}不存在，请先上传')
        return None
    try:
        base = os.path.realpath(UPLOAD_DIR)
        resolved = os.path.realpath(path)
        if os.path.commonpath([base, resolved]) != base:
            raise HTTPException(400, f'{label}路径无效，请重新上传')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, f'{label}路径无效，请重新上传')
    if os.path.splitext(resolved)[1].lower() not in _IMAGE_EXTS or not os.path.isfile(resolved):
        raise HTTPException(400, f'{label}文件已失效，请重新上传')
    return resolved


def _panel_require_second_image(req):
    """墙板模式子行为的第二张图校验：再设计需场景参照图(ref)，替换需原墙板场景图(room)。
    纯原创只需 image_path(已在上游校验)。缺图直接 400，避免静默丢图照常计费。"""
    wf = req.params.workflow_mode or ''
    if '墙板' not in wf:
        return
    sub = req.params.panel_submode or '再设计'
    if '替换' in sub and not (req.room_path and os.path.exists(req.room_path)):
        raise HTTPException(400, '墙板替换：请先上传原墙板场景图')
    if ('再设计' in sub or sub == '') and not (req.ref_path and os.path.exists(req.ref_path)):
        raise HTTPException(400, '墙板再设计：请先上传场景参照图')


# ── 任务：提交 / 列表 / 详情 / SSE / 取消 / 重试 ──
@app.post('/api/jobs')
async def create_job(req: JobSubmitRequest):
    cfg = load_config()
    targets = list(req.model_targets or ({'b2': ['b2'], 'pro': ['pro'], 'both': ['b2', 'pro']}[req.model_filter]))
    if not targets or len(targets) != len(set(targets)):
        raise HTTPException(422, 'model_targets 至少选择一个且不可重复')
    if any(k in targets for k in ('b2', 'pro')) and not ((req.api_key or '').strip() or cfg.get('gemini_api_key', '').strip()):
        raise HTTPException(400, '所选 B2/Pro 缺少 Gemini API Key')
    if 'sd35' in targets:
        if not bool(cfg.get('sd_enabled', False)):
            raise HTTPException(400, 'SD 3.5 实验模型尚未在设置中启用')
        if not (cfg.get('fal_api_key') or '').strip():
            raise HTTPException(400, '所选 SD 3.5 缺少 Fal API Key')
        if '纯效果图' not in (req.params.workflow_mode or ''):
            raise HTTPException(422, 'SD 3.5 首期仅支持纯效果图工作流')
    req.image_path = _require_upload_image_path(req.image_path, '地板图', required=True)
    req.room_path = _require_upload_image_path(req.room_path, '房间图')
    req.ref_path = _require_upload_image_path(req.ref_path, '参照图')
    _panel_require_second_image(req)
    labels = {'b2': 'B2', 'pro': 'Pro', 'sd35': 'SD3.5'}
    label = '[' + '+'.join(labels[k] for k in targets) + ']'
    room_disp = req.params.cn_room_type if req.params.cn_mode else req.params.room_type
    dname = f"{os.path.splitext(os.path.basename(req.image_path))[0]} · {room_disp} {label}"
    legacy_filter = legacy_filter_from_targets(targets)
    job = new_job(dname, time.strftime('%H:%M:%S'), legacy_filter)
    job.model_targets = targets
    ensure_model_runs(job)
    job.workflow_mode = req.params.workflow_mode
    JOBS.add(job.job_id, job)   # 登记并顺手收口最旧的终态卡，防长会话内存缓涨
    _spawn(_run_job_bg(job, req))   # 立即返回，不为整个 4K 生成挂起 HTTP
    return _job_view(job)


@app.get('/api/jobs')
def list_jobs(status: str = '', limit: int = 50):
    jobs = JOBS.snapshot()
    if status:
        jobs = [j for j in jobs if j.status == status]
    return [_job_view(j) for j in jobs[:max(1, limit)]]


@app.get('/api/jobs/{jid}')
def get_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    return _job_view(job)


@app.get('/api/jobs/{jid}/stream')
async def stream_job(jid: str, request: Request):
    """SSE：每秒推一次任务快照；进入终态后再推一条 done 事件并关闭。"""
    async def gen():
        while True:
            if await request.is_disconnected():
                break
            job = JOBS.get(jid)
            if job is None:
                yield f"event: error\ndata: {json.dumps({'error': 'job not found'})}\n\n"
                break
            data = json.dumps(_job_view(job), ensure_ascii=False)
            yield f"data: {data}\n\n"
            if (job.status in ('done', 'partial', 'failed')
                    and job.operation_status != 'running' and not job.pro_polishing):
                yield f"event: done\ndata: {data}\n\n"
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.status not in ('queued', 'running') and job.operation_status != 'running':
        raise HTTPException(409, '任务当前不在运行')
    JOBS.request_cancel(jid)   # 终态由 worker finally 据「是否已出图」判定（已计费的图保留）
    if not job.error:
        update_job(job, error='已取消（用户停止）')
    return {'cancelled': True}


@app.post('/api/jobs/cancel-all')
def cancel_all():
    JOBS.bump_generation()
    n = 0
    for job in JOBS.snapshot():
        if job.status in ('queued', 'running') or job.operation_status == 'running':
            JOBS.request_cancel(job.job_id)
            n += 1
    return {'stopped': n}


@app.post('/api/jobs/clear-completed')
def clear_completed():
    """清掉「完成」状态的任务卡（保留 部分/失败 供逐卡删/重试）。改注册表后落盘，
    否则前端 2.5s 轮询或重启会把它们读回来。只清队列列表，不动出图文件与「记录」。"""
    removed = 0
    with JOBS.locked() as entries:
        victims = [jid for jid, job in entries.items()
                   if job.status == 'done' and job.operation_status != 'running' and not job.pro_polishing]
        for jid in victims:
            del entries[jid]
            JOBS.clear_cancelled(jid)
            removed += 1
    JOBS.persist()  # 必须在 locked() 外：persist 内部会再取同一把锁（不可重入）
    return {'cleared': removed}


@app.post('/api/jobs/{jid}/delete')
def delete_job(jid: str):
    """从队列移除单条任务卡（任意状态；运行中的建议先停止）。仅移除列表项，不动出图/记录。"""
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.status in ('queued', 'running') or job.operation_status == 'running' or job.pro_polishing:
        raise HTTPException(409, '任务仍在运行，请先停止并等待结束后再清除')
    removed = 1 if JOBS.pop(jid) is not None else 0   # pop 连带清取消标记
    JOBS.persist()  # 锁外
    return {'deleted': removed}


@app.post('/api/jobs/{jid}/retry')
async def retry_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.retry_ctx:
        raise HTTPException(400, '该任务缺少重试信息（可能重启后丢失），请重新提交')
    if job.status not in ('failed', 'partial'):
        return _job_view(job)
    # 回执前预置 active 状态（镜像 _retry_bg 开场），前端据此即刻开 SSE；后台会再设同值，幂等
    update_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='',
               operation='retry', operation_status='running', operation_error='')
    _spawn(_retry_bg(job))
    return _job_view(job)


@app.post('/api/jobs/{jid}/sd-upscale')
async def retry_sd_upscale(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    ensure_model_runs(job)
    run = job.model_runs.get('sd35') or {}
    base_path = run.get('base_path') or ''
    if run.get('delivery_status') != 'upscale_failed' or not os.path.isfile(base_path):
        raise HTTPException(400, '没有可重试的 SD 基础图')
    if job.operation_status == 'running':
        raise HTTPException(409, '任务进行中')
    if not (load_config().get('fal_api_key') or '').strip():
        raise HTTPException(400, '重试 SD 超分需要 Fal API Key')
    update_job(job, status='running', started_at=time.time(), operation='sd_upscale',
               operation_status='running', operation_error='')
    _spawn(_retry_sd_upscale_bg(job))
    return _job_view(job)


@app.get('/api/jobs/{jid}/result')
def job_result(jid: str, model: str = 'pro', idx: int = -1):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    ensure_model_runs(job)
    run = job.model_runs.get(model)
    if not run or not (run.get('paths') or []):
        raise HTTPException(404, 'no result for this model')
    requested = int(run.get('index') or 0) if idx < 0 else idx
    i, total, path = nav_model_candidate(job, model, requested)
    return {'model': model, 'idx': i, 'total': total,
            'url': _to_url(path), 'thumb': _result_thumb_url(path)}


# ── 编辑 / 磨缝（对 job 现有成图）──
@app.post('/api/jobs/{jid}/polish')
async def polish_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.pro_path or not os.path.exists(str(job.pro_path)):
        raise HTTPException(400, '没有可磨缝的 Pro 图')
    if job.status in ('queued', 'running') or job.pro_polishing or job.operation_status == 'running':
        raise HTTPException(409, '任务正在处理，请稍后再试')
    api_key = load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    # 回执前预置 active 状态（镜像 _edit_bg 开场），前端据此即刻开 SSE
    update_job(job, started_at=time.time(), pro_polishing=True, pro_stage='',
               operation='polish', operation_status='running', operation_error='')
    _spawn(_edit_bg(
        job, api_key=api_key, instruction=FLOOR_DESEAM_INSTRUCTION,
        model_id=GEMINI_MODEL_MAP['Nano Banana Pro'], model_label='Nano Banana Pro_磨缝',
        preserve=False, image_size='4K', color_match=True))
    return _job_view(job)


@app.post('/api/jobs/{jid}/edit')
async def edit_job(jid: str, req: EditRequest):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.status in ('queued', 'running') or job.pro_polishing or job.operation_status == 'running':
        raise HTTPException(409, '任务正在处理，请稍后再试')
    api_key = (req.api_key or '').strip() or load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    model_id = GEMINI_MODEL_MAP.get(req.model_choice, GEMINI_MODEL_MAP['Nano Banana Pro'])
    # 回执前预置 active 状态（镜像 _edit_bg 开场），前端据此即刻开 SSE
    update_job(job, started_at=time.time(), pro_polishing=True, pro_stage='',
               operation='edit', operation_status='running', operation_error='')
    _spawn(_edit_bg(
        job, api_key=api_key, instruction=req.instruction, model_id=model_id,
        model_label=f'{req.model_choice} 二改', preserve=req.preserve_floor_geometry,
        image_size=req.image_size, color_match=req.color_match))
    return _job_view(job)


# ── 快速预览：Nano Banana 2 Lite 出一张 1K 草图（不进队列、不写记录、恒 Google 直连）──
async def _preview_bg(pid: str, req: 'PreviewRequest'):
    """借用 save_task_files_html(persist=False) 的提示词逻辑，用 Lite 出 1K 预览。
    prep(rp/sref/bevel_ref) 是 _run_job_bg(约 269-274 行) 的精简版；预览只用 Pro 提示词、无 retry_ctx。
    刻意在预览侧写精简副本、不重构 4K 热路径（4K 主编排是命脉，本仓库测试覆盖不到它）。"""
    def _set(**kw):
        PREVIEWS.update_fields(pid, **kw)

    def _on_stage(t):
        _set(stage=t)

    should_cancel = lambda: PREVIEWS.is_cancelled(pid)
    api_key = (req.api_key or '').strip() or load_config().get('gemini_api_key', '').strip()
    p = req.params
    try:
        if should_cancel():
            _set(status='failed', error='已取消'); return
        is_ref_mode = '参照模式' in (p.workflow_mode or '')
        is_panel = '墙板' in (p.workflow_mode or '')
        _psub = p.panel_submode or ''
        _panel_redesign = is_panel and ('再设计' in _psub or _psub == '')
        # 参照模式 / 墙板再设计 Step-1：提取风格（失败即中止，不发计费请求）
        style_text = ''
        if (is_ref_mode or _panel_redesign) and req.ref_path:
            style_text, sa_err = await asyncio.to_thread(analyze_style_image, api_key, req.ref_path)
            if sa_err or not style_text:
                _set(status='failed', error=f'风格分析失败: {sa_err or "返回为空"}'); return
        # 组装提示词（persist=False：借提示词/PNG，不新建记录）；与 4K 主流程共用 _task_prep_lock
        tp = TaskParams(image_path=req.image_path, style_analysis_text=style_text, **p.model_dump())
        async with _task_prep_lock:
            (_pil, _sms, prt, _sip, _jpt, _rid, pnp, prt_pro) = await asyncio.to_thread(
                save_task_files_html, persist=False, **task_params_to_kwargs(tp))
        cpt = extract_clean_prompt(prt_pro or prt)   # 预览用 Pro 终极提示词，最贴近最终 4K 观感
        ar = (p.aspect_ratio or '4:3').split(' ')[0]
        if is_panel:
            rp = (req.room_path or None) if '替换' in _psub else None
            sref = (req.ref_path or None) if _panel_redesign else None
        else:
            rp = None if is_ref_mode else (req.room_path or None)
            sref = (req.ref_path or None) if is_ref_mode else None
        _seam_v = p.seam_type or ''
        _is_pressed_bevel = ('圆弧倒角' in _seam_v and '无缝' not in _seam_v)
        bevel_ref = get_bevel_ref_image() if _is_pressed_bevel else None
        if should_cancel():
            _set(status='failed', error='已取消', stage=''); return
        # Lite 只出 1K、且不在 Fal → 直接调 Google 直连函数，绕过 call_image_generate 的 provider 路由/转 Fal
        img, err = await asyncio.to_thread(
            call_gemini_generate, api_key, LITE_PREVIEW_MODEL, cpt, pnp,
            '1K', ar, rp, sref, _on_stage, should_cancel, bevel_ref)
        if img is None:
            record_usage(p.workflow_mode, 'NB2 Lite', 'google', False, 'preview')
            _set(status='failed', error=err or '预览生成失败', stage=''); return
        path = save_api_result_jpg(img, 'NB2Lite预览', pnp)
        if not path:
            record_usage(p.workflow_mode, 'NB2 Lite', 'google', False, 'preview')
            _set(status='failed', error='预览结果保存失败', stage=''); return
        record_usage(p.workflow_mode, 'NB2 Lite', 'google', True, 'preview')
        _set(status='done', stage='', url=_to_url(path), thumb=_result_thumb_url(path))
        logger.info(f"[快速预览] 完成 pid={pid}, path={path}")
    except Exception as e:
        logger.exception(f"[快速预览] 异常 pid={pid}")
        _set(status='failed', error=str(e), stage='')
    finally:
        PREVIEWS.clear_cancelled(pid)


@app.post('/api/preview')
async def create_preview(req: PreviewRequest):
    if not ((req.api_key or '').strip() or load_config().get('gemini_api_key', '').strip()):
        raise HTTPException(400, '缺少 API Key')
    req.image_path = _require_upload_image_path(req.image_path, '地板图', required=True)
    req.room_path = _require_upload_image_path(req.room_path, '房间图')
    req.ref_path = _require_upload_image_path(req.ref_path, '参照图')
    _panel_require_second_image(req)
    pid = f'pv_{uuid.uuid4().hex}'
    PREVIEWS.add(pid, {'status': 'running', 'stage': '', 'url': '', 'thumb': '', 'error': '', 'ts': time.time()})
    _spawn(_preview_bg(pid, req))   # 秒回 pid，前端轮询 /api/preview/{pid}
    return {'preview_id': pid, 'status': 'running'}


@app.get('/api/preview/{pid}')
def get_preview(pid: str):
    snap = PREVIEWS.view(pid) or {}
    if not snap:
        raise HTTPException(404, 'preview not found')
    return {'preview_id': pid, **snap}


@app.post('/api/preview/{pid}/cancel')
def cancel_preview(pid: str):
    PREVIEWS.request_cancel(pid)   # _preview_bg 与底层 call_gemini_generate 均会读 should_cancel
    return {'cancelled': True}


# ── 上传 / 历史小样 ──
def _save_upload(file: UploadFile, prefix: str) -> dict:
    dest = safe_upload_path(file.filename or 'upload.jpg', prefix)
    if not dest:
        raise HTTPException(400, '不支持的文件类型（仅 jpg/jpeg/png/webp）')
    tmp = f'{dest}.{uuid.uuid4().hex}.upload'
    total = 0
    try:
        with open(tmp, 'xb') as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    raise HTTPException(413, '图片超过 50 MiB 上限')
                out.write(chunk)
        try:
            with Image.open(tmp) as image:
                image.verify()
            with Image.open(tmp) as image:
                if (image.format or '').upper() not in {'JPEG', 'PNG', 'WEBP'}:
                    raise HTTPException(400, '图片真实格式不受支持（仅 JPEG/PNG/WebP）')
                expected = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP'}[
                    os.path.splitext(dest)[1].lower()
                ]
                if (image.format or '').upper() != expected:
                    raise HTTPException(400, '图片扩展名与真实格式不一致')
                if image.width * image.height > _MAX_UPLOAD_PIXELS:
                    raise HTTPException(413, '图片像素超过 8000 万上限')
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, '文件不是有效图片或图片已损坏')
        if os.path.exists(dest):
            dest = safe_upload_path(file.filename or 'upload.jpg', prefix)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
    return {'path': dest, 'url': _to_url(dest), 'name': os.path.basename(dest), 'thumb': _thumb_url(dest)}


@app.post('/api/uploads/floor')
def upload_floor(file: UploadFile = File(...)):
    return _save_upload(file, '')


@app.post('/api/uploads/room')
def upload_room(file: UploadFile = File(...)):
    return _save_upload(file, 'room_')


@app.post('/api/uploads/ref')
def upload_ref(file: UploadFile = File(...)):
    return _save_upload(file, 'ref_')


def _remove_managed_logo(path: str) -> None:
    """Best-effort cleanup for logo uploads owned by this app; never delete arbitrary paths."""
    if not path:
        return
    try:
        rp = os.path.realpath(path)
        base = os.path.realpath(UPLOAD_DIR)
        if (os.path.commonpath([rp, base]) == base
                and os.path.basename(rp).startswith('logo_')
                and os.path.isfile(rp)):
            os.remove(rp)
    except Exception as ex:
        logger.warning(f"[品牌] 清理旧 logo 失败(忽略): {ex}")


@app.post('/api/uploads/logo')
def upload_logo(file: UploadFile = File(...)):
    """PPTX 品牌 logo：存上传目录（logo_ 前缀已加入小样扫描排除名单）并写入配置。"""
    old_path = get_pptx_branding()['logo_path']
    out = _save_upload(file, 'logo_')
    if not update_config({'pptx_logo_path': out['path']}):
        _remove_managed_logo(out['path'])
        raise HTTPException(500, 'logo 已上传但配置保存失败，请检查写权限')
    if old_path and os.path.realpath(old_path) != os.path.realpath(out['path']):
        _remove_managed_logo(old_path)
    return out


@app.post('/api/uploads/logo/clear')
def clear_logo():
    """清除 PPTX logo 配置，并回收本程序管理的旧 logo 文件。"""
    old_path = get_pptx_branding()['logo_path']
    if not update_config({'pptx_logo_path': ''}):
        raise HTTPException(500, 'logo 配置清除失败，请检查写权限')
    _remove_managed_logo(old_path)
    return {'ok': True}


@app.get('/api/swatches/recent')
def recent_swatches(limit: int = 24):
    out = []
    for p in list_recent_floor_swatches(limit):
        out.append({'path': p, 'url': _to_url(p), 'name': os.path.basename(p), 'thumb': _thumb_url(p)})
    return out


# ── 记录 ──
@app.get('/api/records')
def list_records():
    out = []
    for jp in scan_json_files():
        recs = load_records_file(jp)
        favorite_count = sum(
            1 for rec in recs if isinstance(rec, dict)
            for res in (rec.get('results') or []) if isinstance(res, dict) and res.get('favorite')
        )
        out.append({
            'json_path': jp,
            'labels': get_record_labels(jp, recs),
            'favorite_count': favorite_count,
        })
    return out


def _record_color_match_ref_path(json_path: str, record: dict) -> str:
    """解析记录校色的默认小样。

    优先使用队列校色同源的 *_优化图.png；然后兼容新版 gen_context
    和旧版 sample_image_file。所有候选都经参照图白名单校验。
    """
    gc = record.get('gen_context') if isinstance(record, dict) else None
    sample_rel = record.get('sample_image_file') if isinstance(record, dict) else ''
    sample_path = safe_output_path(sample_rel) if sample_rel else ''
    candidates = [
        json_path.replace('_记录.json', '_优化图.png'),
        str((gc or {}).get('image_path') or ''),
        sample_path or '',
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return _require_ref_image_path(candidate)
        except HTTPException:
            continue
    return ''


@app.get('/api/records/load')
def load_records(json_path: str):
    json_path = _require_record_json_path(json_path)
    # 旧版 NiceGUI 在选中文件时会执行这个幂等迁移；Next 记录页迁移时漏了。
    # 恢复后，仅内联 base64 的旧图会转为文件引用，从而可以浏览和校色。
    try:
        migrate_record_file(json_path)
    except Exception as ex:
        logger.warning(f'[记录] 旧图文件化失败，继续读取原记录: {json_path} / {ex}')
    recs = load_records_file(json_path)
    # 结果图引用改写成 URL；内联 base64(老记录)不回传大 blob，仅标记 has_inline。
    for r in recs:
        for secret in ('prompt_en', 'prompt_en_pro', '_pe', '_pe_pro', 'sample_image_b64'):
            r.pop(secret, None)
        gc = r.get('gen_context')
        if isinstance(gc, dict):
            if gc.get('room_path'):
                gc['room_url'] = _to_url(gc['room_path'])
            if gc.get('image_path'):
                gc['image_url'] = _to_url(gc['image_path'])
        color_ref = _record_color_match_ref_path(json_path, r)
        r['color_match_ref_path'] = color_ref
        r['color_match_ref_url'] = _to_url(color_ref)
        for res in r.get('results', []) if isinstance(r, dict) else []:
            rel = res.get('result_image_file')
            if rel:
                ap = safe_output_path(rel)
                res['result_url'] = _to_url(ap) if ap else ''
                res['result_thumb'] = _result_thumb_url(ap) if ap else ''
            res['has_inline'] = bool(res.pop('result_image_b64', None))
    return recs


@app.post('/api/records/reveal')
def reveal(req: RevealRequest):
    json_path = _require_record_json_path(req.json_path)
    text = reveal_prompt_fn(json_path, req.record_id, req.password)
    ok = not (text.startswith('🔒') or text.startswith('❌'))
    return {'text': text, 'ok': ok}


# ── 配方 / 失败知识库 / 连通性 / 配置 / 模型 ──
@app.get('/api/recipes')
def recipes(tone: str = '', limit: int = 6):
    if tone:
        return recommend_recipes(tone, limit)
    return FLOOR_RECIPES[:limit] if limit else FLOOR_RECIPES


# ── 自定义配方（我的配方）：CRUD，沿项目 POST-mutation 惯例 ──
class CustomRecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    params: dict


class CustomRecipeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=40)
    params: Optional[dict] = None


@app.get('/api/recipes/custom')
def custom_recipes_list():
    return list_custom_recipes()


@app.post('/api/recipes/custom')
def custom_recipes_add(req: CustomRecipeCreate):
    return add_custom_recipe(req.name, req.params)


@app.post('/api/recipes/custom/{rid}/update')
def custom_recipes_update(rid: str, req: CustomRecipeUpdate):
    r = update_custom_recipe(rid, name=req.name, params=req.params)
    if r is None:
        raise HTTPException(404, '配方不存在')
    return r


@app.post('/api/recipes/custom/{rid}/delete')
def custom_recipes_delete(rid: str):
    if not delete_custom_recipe(rid):
        raise HTTPException(404, '配方不存在')
    return {'ok': True}


@app.post('/api/failure/classify')
def classify(req: ErrRequest):
    return classify_failure(req.err)


@app.get('/api/connection/test')
def connection_test(gemini: str = '', fal: str = '', proxy: str = ''):
    cfg = load_config()
    g = gemini or cfg.get('gemini_api_key', '')
    f = fal or cfg.get('fal_api_key', '')
    p = proxy or get_proxy()
    return {'result': test_connection(g, f, p)}


@app.get('/api/config')
def get_config():
    return _config_view()


@app.put('/api/config')
def put_config(req: ConfigPatch):
    patch = req.model_dump(exclude_none=True)
    for key in ('gemini_api_key', 'fal_api_key', 'proxy', 'fal_queue_proxy', 'tls_ca_bundle',
                'deepseek_api_key', 'deepseek_base_url', 'deepseek_model',
                'omakase_gemini_model', 'pptx_company', 'pptx_contact',
                'comfyui_base_url', 'comfyui_workflow_path', 'inpaint_remove_prompt'):
        if key in patch:
            patch[key] = str(patch[key] or '').strip()
    if 'usage_prices' in patch:
        clean = {}
        for k, v in (patch['usage_prices'] or {}).items():
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f'单价必须是数字：{k}')
            if not math.isfinite(f):
                raise HTTPException(400, f'单价必须是有限数字：{k}')
            if f < 0:
                raise HTTPException(400, f'单价不能为负：{k}')
            if str(k).strip():
                clean[str(k).strip()] = f
        patch['usage_prices'] = clean
    if not update_config(patch):
        raise HTTPException(500, '配置保存失败，请检查程序目录写权限或磁盘空间')
    return _config_view()


# ── Omakase：Gemini 主线路生成场景，DeepSeek 配置后自动备用（此处不生图）──
class OmakaseRequest(BaseModel):
    idea: str = Field(default='', max_length=2000)


@app.post('/api/omakase/scenes')
async def omakase_scenes(req: OmakaseRequest):
    if not get_omakase_enabled():
        raise HTTPException(400, 'Omakase 模式未启用，请先在设置里开启')
    idea = (req.idea or '').strip()
    if not idea:
        raise HTTPException(400, '请先描述你想要的画面/氛围')
    cfg = load_config()
    gemini_key = (cfg.get('gemini_api_key') or '').strip()
    deepseek_key = get_deepseek_api_key()
    if not gemini_key and not deepseek_key:
        raise HTTPException(400, '缺少 Gemini API Key，且未配置 DeepSeek 备用 Key')
    options, err, provider, fallback_used = await asyncio.to_thread(
        call_omakase_scenes, idea,
        gemini_api_key=gemini_key,
        gemini_model=get_omakase_gemini_model(),
        deepseek_api_key=deepseek_key,
        deepseek_base_url=get_deepseek_base_url(),
        deepseek_model=get_deepseek_model())
    if err:
        info = classify_failure(err)
        detail = f"{info.get('title', 'Omakase 生成失败')}：{info.get('action', '')}".rstrip('：')
        raise HTTPException(502, detail)
    notice = ''
    if fallback_used:
        notice = ('Gemini 暂不可用，已自动使用 DeepSeek 备用线路生成。'
                  if gemini_key else
                  '未配置 Gemini API Key，已使用 DeepSeek 备用线路生成。')
    return {
        'options': options,
        'provider': provider,
        'fallback_used': fallback_used,
        'notice': notice,
    }


@app.get('/api/models')
def models_endpoint():
    return {'gemini': GEMINI_MODEL_MAP, 'fal': FAL_MODEL_MAP, 'provider': get_image_provider()}


# 下拉选项：从 prompt_data 取真源，避免前端硬编码与引擎漂移（workflow/seam/gloss/res 是 UI 级常量）
_WORKFLOW_MODES = [
    '纯效果图 (生成全新空间)', '地板替换 (保持原图换地板)',
    '宠物友好 (动物独处/主宠互动)', '参照模式 (风格参照图生新图)',
    'Omakase (AI 代笔场景)',
    '墙板模式 (护墙板/木饰面：再设计/替换/原创)',
]
_SEAM_TYPES = ['无缝拼接 (SPC/LVT专用)', '常规倒角缝 (如强化/木地板)', '圆弧倒角 (Pressed Bevel)']
_GLOSSINESS = ['超哑光 (0-3°)', '哑光 (3-5°)', '高光 (High Gloss)']
_RESOLUTIONS = ['4K', '2K']
_ASPECT_RATIOS = ['4:3 (横向)', '16:9 (超宽)', '3:4 (竖向)', '9:16 (手机)']


@app.get('/api/options')
def options():
    """前端表单全量下拉/多选项；除少数 UI 级常量外均取自 prompt_data 真源，避免与引擎漂移。"""
    return {
        'workflow_modes': _WORKFLOW_MODES,
        'model_filters': [
            {'value': 'b2', 'label': '⚡ B2'},
            {'value': 'pro', 'label': '⚡ Pro'},
            {'value': 'both', 'label': '⚡ 双模型'},
        ],
        'resolutions': _RESOLUTIONS,
        'aspect_ratios': _ASPECT_RATIOS,
        'seam_types': _SEAM_TYPES,
        'glossiness': _GLOSSINESS,
        # ── 通用 ──
        'room_types': ROOM_TYPES,
        'property_types': PROPERTY_TYPES,
        'views': VIEWS,
        'floor_tones': FLOOR_TONES,
        'styles': STYLES,
        'lightings': LIGHTINGS,
        'angles': ANGLES,
        'floor_sizes': FLOOR_SIZES,
        'panel_sizes': PANEL_SIZES,
        'market_furniture': MARKET_FURNITURE_CHOICES,
        'avoid_items': AVOID_LIST,
        # ── 地区级联：大洲→国家→城市 ──
        'continents': CONTINENTS,
        'location_map': LOCATION_MAP,
        # ── 宠物友好 ──
        'pet_types': PET_TYPES,
        'pet_actions': PET_ACTIONS,
        'pet_focus': PET_FOCUS_OPTIONS,
        # ── 国内市场 ──
        'cn_room_types': CN_ROOM_TYPES,
        'cn_developers': CN_DEVELOPERS,
        'cn_cities': CN_CITIES,
        'cn_tiers': CN_TIERS,
        'cn_unit_types': CN_UNIT_TYPES,
        'cn_delivery_choices': CN_DELIVERY_CHOICES,
        'cn_space_features': list(CN_SPACE_FEATURES.keys()),
        'cn_facilities': list(CN_FACILITIES.keys()),
    }


# ============================================================
# STEP 2.5 迁移补齐：重抽/多抽、记录内二改、导出、记录管理、识色、用量
# ============================================================

async def _regen_once(job: JobRecord):
    """复用 job.retry_ctx 再跑全部模型、把新图 append 进候选（移植 webui._regen_job 去 UI）。
    返回本轮每模型错误拼接串（'B2: xx Pro: yy'，全成功为 ''），供 _regen_bg 写进 job.error。"""
    ctx = job.retry_ctx or {}
    api_key = (ctx.get('api_key') or '').strip() or load_config().get('gemini_api_key', '').strip()
    targets = list(ctx.get('model_targets') or job.model_targets)
    fal_key = (load_config().get('fal_api_key') or '').strip()
    generation = JOBS.generation
    should_cancel = lambda: JOBS.is_cancelled(job.job_id, generation)

    def gen_one(model_id, prompt_text, sk, name):
        return _generate_one_model(job, model_id, prompt_text, sk, name, api_key=api_key,
                                   pnp=ctx['pnp'], ims=ctx['ims'], ar=ctx['ar'], rp=ctx['rp'],
                                   sref=ctx['sref'], bevel_ref=ctx.get('bevel_ref'),
                                   jpt=ctx['jpt'], rid=ctx['rid'], should_cancel=should_cancel)

    tasks = []
    if 'b2' in targets:
        tasks.append(('b2', gen_one(GEMINI_MODEL_MAP['Nano Banana 2'], ctx['cpt'], 'b2', 'Nano Banana 2')))
    if 'pro' in targets:
        tasks.append(('pro', gen_one(GEMINI_MODEL_MAP['Nano Banana Pro'], ctx['cpt_pro'], 'pro', 'Nano Banana Pro')))
    if 'sd35' in targets:
        sd_options = dict(ctx.get('sd_options') or {})
        sd_options['seed'] = None  # 重抽必须产生新 seed；retry 才复用原 seed
        tasks.append(('sd35', _generate_sd35_model(
            job, fal_key=fal_key, positive=ctx.get('sd_positive', ''), negative=ctx.get('sd_negative', ''),
            pnp=ctx['pnp'], ims=ctx['ims'], ar=ctx['ar'], jpt=ctx['jpt'], rid=ctx['rid'],
            options=sd_options, should_cancel=should_cancel,
        )))
    results = await asyncio.gather(*[c for _, c in tasks], return_exceptions=True)
    # 收集每模型错误串（镜像 _run_job_bg）：否则重抽失败对用户完全静默——没出新图也没任何提示
    errors = []
    for (k, _), res in zip(tasks, results):
        e = str(res) if isinstance(res, Exception) else (res[1] or '')
        if e:
            errors.append(f'{k.upper()}: {e}')
    return ' '.join(errors)


async def _regen_bg(job: JobRecord, n: int):
    """一键多抽 ×n：串行重抽 n 次（每次 append 候选）；用户停止(JOBS.request_cancel)即跑完当前后停。
    保留最后一轮的每模型错误串写进 job.error（用户主动取消不算失败原因，过滤掉）。"""
    if not job.retry_ctx:
        update_job(job, error='缺少重抽上下文')
        return
    JOBS.clear_cancelled(job.job_id)
    # 整批期间保持 running（不在迭代间置终态）——否则 SSE 见终态即关流，多抽第二张起就断了。
    update_job(job, status='running', started_at=time.time(), error='',
               operation='regen', operation_status='running', operation_error='')
    err = ''
    last_err = ''
    try:
        for _i in range(max(1, n)):
            if JOBS.is_cancelled(job.job_id, JOBS.generation):
                break
            round_err = ((await _regen_once(job)) or '').strip()
            if round_err and '取消' not in round_err:
                last_err = round_err
            JOBS.persist()
    except Exception as e:
        logger.exception(f"[API多抽] 异常 job={job.job_id}")
        err = str(e)
    finally:
        final = compute_runs_final_status(job)
        op_error = err or last_err
        cancelled = JOBS.is_cancelled(job.job_id, JOBS.generation)
        update_job(job, status=final, error=op_error,
                   operation_status=('cancelled' if cancelled else ('failed' if op_error else 'done')),
                   operation_error=op_error or ('已取消' if cancelled else ''))
        JOBS.clear_cancelled(job.job_id)
        JOBS.persist()


async def _record_edit_bg(job: JobRecord, *, src_pil, api_key, instruction, model_id, model_label,
                          image_size, preserve, json_path, record_id, source_ref, color_match=False):
    """记录内二改：对已存记录的某张结果做图生图编辑，结果 append 回该记录（移植 webui._do_edit 去 UI）。
    color_match=True 时把结果色彩对齐回原图（镜像 _edit_bg 的防偏色分支）。"""
    jid = job.job_id
    generation = JOBS.generation
    update_job(job, status='running', started_at=time.time(), pro_polishing=True)

    def _on_stage(t):
        try:
            job.pro_stage = t
        except Exception:
            pass

    try:
        buf = io.BytesIO()
        src_pil.convert('RGB').save(buf, format='JPEG', quality=95)
        b64 = base64.b64encode(buf.getvalue()).decode()
        ar = infer_aspect_ratio_from_b64(b64)
        if _model_semaphores['pro'].locked():
            _on_stage('排队中')
        async with _model_semaphores['pro']:
            out, err = await asyncio.to_thread(
                call_gemini_edit, api_key, model_id, instruction, b64,
                image_size, ar, preserve, _on_stage, lambda: JOBS.is_cancelled(jid, generation))
        if out is None:
            record_usage(job.workflow_mode, model_label, 'google', False, 'record_edit')
            update_job(job, status='failed', error=f'二改失败：{err}', operation_status='failed', operation_error=str(err or ''))
            return
        if color_match:
            try:
                # ref 强制 RGB：老记录 b64 源图可能是 RGBA/P，Pillow LAB 只支持从 RGB 转换
                out = await asyncio.to_thread(match_color_to_reference, out, src_pil.convert('RGB'))
            except Exception as ex:
                logger.warning(f"[记录二改] 色彩对齐失败(用未对齐图) job={jid}: {ex}")
        ppath = save_api_result_jpg(out, model_label, job.png_path or os.path.join(MAIN_OUTPUT_DIR, 'edit'))
        if not ppath:
            record_usage(job.workflow_mode, model_label, 'google', False, 'record_edit')
            update_job(job, status='failed', error='二改结果保存失败', operation_status='failed', operation_error='二改结果保存失败')
            return
        add_candidate(job, 'pro', ppath)
        record_usage(job.workflow_mode, model_label, 'google', True, 'record_edit')
        try:
            await asyncio.to_thread(append_edited_result_to_record, json_path, record_id,
                                    source_ref, out, instruction, model_label, ppath)
        except Exception as ex:
            logger.warning(f"[记录二改] 写记录失败 job={jid}: {ex}")
        update_job(job, status='done', pro_path=ppath, operation_status='done', operation_error='')
        logger.info(f"[记录二改] 完成 job={jid}, record={record_id}, path={ppath}")
    except Exception as e:
        logger.exception(f"[记录二改] 异常 job={jid}")
        update_job(job, status='failed', error=str(e), operation_status='failed', operation_error=str(e))
    finally:
        update_job(job, pro_polishing=False, pro_stage='')
        JOBS.clear_cancelled(jid)
        JOBS.persist()


# ── 请求模型 ──
class ResultRef(BaseModel):
    json_path: str
    record_id: str
    result_id: str = Field(min_length=1, max_length=80)


class ResultReviewRequest(ResultRef):
    review_status: str = 'unreviewed'
    review_tags: List[str] = Field(default_factory=list, max_length=20)
    review_note: str = Field(default='', max_length=2000)
    best: bool = False


class RecordRef(BaseModel):
    json_path: str
    record_id: str


class RecordEditRequest(BaseModel):
    model_config = {'protected_namespaces': ()}
    json_path: str
    record_id: str
    result_id: str = Field(min_length=1, max_length=80)
    instruction: str = Field(min_length=1, max_length=2000)
    api_key: str = ''
    image_size: Literal['2K', '4K'] = '4K'
    preserve_floor_geometry: bool = True
    model_choice: Literal['Nano Banana 2', 'Nano Banana Pro'] = 'Nano Banana Pro'
    # 保持原图色彩（防偏色）：同 EditRequest.color_match
    color_match: bool = True


def _serve_export(result_msg: str, out_dir: str, media_type: str):
    """records.export_* 写文件并返回 '✅ 已导出：<basename>'；据此定位文件流式下载。"""
    if not result_msg.startswith('✅'):
        raise HTTPException(400, result_msg)
    base = result_msg.split('：', 1)[1].strip() if '：' in result_msg else result_msg
    path = os.path.join(out_dir, base)
    if not os.path.exists(path):
        raise HTTPException(500, f'导出文件未找到: {base}')
    return FileResponse(path, media_type=media_type, filename=base)


_PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'


# ── 重抽/多抽 ──
@app.post('/api/jobs/{jid}/regen')
async def regen_job(jid: str, n: int = Query(default=1, ge=1, le=6)):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.retry_ctx:
        raise HTTPException(400, '该任务缺少重抽上下文(可能重启后丢失)，请重新提交')
    if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
        raise HTTPException(409, '任务进行中，请稍后再抽')
    # 回执前预置 active 状态（镜像 _regen_bg 开场），前端据此即刻开 SSE
    update_job(job, status='running', started_at=time.time(), error='',
               operation='regen', operation_status='running', operation_error='')
    _spawn(_regen_bg(job, n))
    return _job_view(job)


# ── 记录内二改 ──
@app.post('/api/records/edit')
async def record_edit(req: RecordEditRequest):
    api_key = (req.api_key or '').strip() or load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    json_path = _require_record_json_path(req.json_path)
    recs = load_records_file(json_path)
    rec = next((r for r in recs if r.get('id') == req.record_id), None)
    if not rec:
        raise HTTPException(404, '未找到记录')
    results = rec.get('results', [])
    res = next((item for item in results if item.get('result_id') == req.result_id), None)
    if res is None:
        raise HTTPException(404, '未找到该效果图')
    src_pil = None
    rel = res.get('result_image_file')
    abs_src = safe_output_path(rel) if rel else None
    if abs_src:
        try:
            src_pil = Image.open(abs_src)
            src_pil.load()
        except Exception:
            src_pil = None
    if src_pil is None and res.get('result_image_b64'):
        src_pil = b64_to_pil(res['result_image_b64'])
    if src_pil is None:
        raise HTTPException(404, '该结果无可用图片')

    material = os.path.basename(json_path).replace('_记录.json', '')
    job = new_job(f'二改 · {material}', time.strftime('%H:%M:%S'), 'pro')
    job.workflow_mode = rec.get('workflow_mode', '')
    job.json_path = json_path
    job.record_id = req.record_id
    job.png_path = abs_src or os.path.join(MAIN_OUTPUT_DIR, 'edit')
    job.operation = 'record_edit'
    job.operation_status = 'running'
    JOBS.add(job.job_id, job)   # 顺手收口最旧的终态卡（新建的二改 job 是 queued/in-flight，不会被删）
    model_id = GEMINI_MODEL_MAP.get(req.model_choice, GEMINI_MODEL_MAP['Nano Banana Pro'])
    _spawn(_record_edit_bg(
        job, src_pil=src_pil, api_key=api_key, instruction=req.instruction, model_id=model_id,
        model_label=f'{req.model_choice} 二改', image_size=req.image_size,
        preserve=req.preserve_floor_geometry, json_path=json_path,
        record_id=req.record_id, source_ref=req.result_id, color_match=req.color_match))
    return _job_view(job)


# ── 记录管理：删除结果 / 删除记录 / 收藏结果 ──
@app.post('/api/records/result/delete')
def delete_result(req: ResultRef):
    json_path = _require_record_json_path(req.json_path)
    if not delete_result_image(json_path, req.record_id, req.result_id):
        raise HTTPException(404, '未找到该效果图')
    return {'ok': True}


@app.post('/api/records/result/favorite')
def favorite_result(req: ResultRef):
    json_path = _require_record_json_path(req.json_path)
    new = toggle_result_favorite(json_path, req.record_id, req.result_id)
    if new is None:
        raise HTTPException(404, '未找到该效果图')
    return {'favorite': new}


@app.post('/api/records/result/review')
def review_result(req: ResultReviewRequest):
    json_path = _require_record_json_path(req.json_path)
    new = update_result_review(
        json_path, req.record_id, req.result_id,
        review_status=req.review_status,
        review_tags=req.review_tags,
        review_note=req.review_note,
        best=req.best,
    )
    if new is None:
        raise HTTPException(404, '未找到该效果图')
    return new


@app.post('/api/records/delete')
def delete_record(req: RecordRef):
    json_path = _require_record_json_path(req.json_path)
    if not delete_record_entry(json_path, req.record_id):
        raise HTTPException(404, '未找到该记录')
    return {'ok': True}


# ── 导出：HTML / PPTX / 收藏夹PPTX ──
@app.get('/api/records/export/html')
def export_html(json_path: str):
    json_path = _require_record_json_path(json_path)
    return _serve_export(export_html_from_json(json_path), os.path.dirname(json_path), 'text/html')


@app.get('/api/records/export/pptx')
def export_pptx(json_path: str):
    json_path = _require_record_json_path(json_path)
    return _serve_export(export_pptx_from_json(json_path), os.path.dirname(json_path), _PPTX_MIME)


@app.get('/api/records/export/favorites-pptx')
def export_favorites():
    return _serve_export(export_favorites_pptx(), MAIN_OUTPUT_DIR, _PPTX_MIME)


# ── 地板识色 + 智能配方 ──
def _resolve_recipe(r: dict) -> dict:
    """把配方的 kw 提示用 pick_option_key 解析成前端可直接套用的具体选项值
    （拼缝/光泽/板材尺寸跟地板小样定,配方不覆盖——与老版 _apply_recipe 一致）。"""
    return {
        'key': r.get('key', ''),
        'label': r.get('label', ''),
        'sub': r.get('sub', ''),
        'style_type': pick_option_key(STYLES, r.get('style_kw') or []) or '',
        'lighting': pick_option_key(LIGHTINGS, r.get('light_kw') or []) or '',
        'angle': pick_option_key(ANGLES, r.get('angle_kw') or []) or '',
        'aspect_ratio': pick_option_key(_ASPECT_RATIOS, r.get('aspect_kw') or []) or '',
        'resolution': pick_option_key(_RESOLUTIONS, r.get('res_kw') or []) or '',
    }


@app.get('/api/floor/analyze')
def floor_analyze(path: str):
    path = _require_upload_image_path(path, '地板图', required=True)
    tone, _html = analyze_floor_tone(path)
    matched = next((t for t in FLOOR_TONES if tone and (tone in t or t in tone)), tone or FLOOR_TONES[0])
    return {'tone': matched, 'recipes': [_resolve_recipe(r) for r in recommend_recipes(matched, 6)]}


# ── 用量统计（cost=按配置单价 × 成功张数的估算，未配单价的行为 None）──
@app.get('/api/usage')
def usage():
    return load_usage_summary(get_usage_prices())


# ── 评审复盘：聚合统计 + 好图样本库（均只读，不碰 JOBS 队列）──
@app.get('/api/review/summary')
def review_summary():
    return load_review_summary()


@app.get('/api/review/gallery')
def review_gallery(filter: str = 'pass', limit: int = 60):
    if filter not in ('pass', 'best'):
        raise HTTPException(400, "filter 仅支持 pass / best")
    out = []
    for it in collect_review_gallery(filter, limit):
        res = it['res']
        rel = res.get('result_image_file')
        ap = safe_output_path(rel) if rel else ''
        out.append({
            'json_path': it['json_path'],
            'material': it['material'],
            'record_id': it['record_id'],
            'result_id': it['result_id'],
            'style': it['style'],
            'room_type': it['room_type'],
            'workflow_mode': it['workflow_mode'],
            'model_label': res.get('model_label', ''),
            'result_timestamp': res.get('result_timestamp', ''),
            'review_status': res.get('review_status', 'unreviewed'),
            'review_tags': res.get('review_tags') or [],
            'review_note': res.get('review_note', ''),
            'best': bool(res.get('best')),
            'result_url': _to_url(ap) if ap else '',
            'result_thumb': _result_thumb_url(ap) if ap else '',
        })
    return out


# ============================================================
# 手动校色（区域化 Reinhard）：纯本地 numpy、同步秒级、零 API 费用。
# 预览先行（缩图 + data URL），人眼确认后才全分辨率提交为新候选。
# ============================================================
def _require_output_image_rel(rel: str) -> str:
    """成图相对路径 → outputs 内绝对路径；越界/不存在 → 400。"""
    ap = safe_output_path(rel or '')
    if not ap or not os.path.isfile(ap):
        raise HTTPException(400, '成图路径无效')
    return ap


def _require_ref_image_path(path: str) -> str:
    """参照图绝对路径：realpath 后须落在上传目录或输出目录内（仿 _to_url 反逃逸）。"""
    try:
        rp = os.path.realpath(str(path or ''))
    except Exception:
        raise HTTPException(400, '参照图路径无效')
    for base in (UPLOAD_DIR, MAIN_OUTPUT_DIR):
        try:
            rbase = os.path.realpath(base)
            if os.path.commonpath([rp, rbase]) == rbase and os.path.isfile(rp):
                return rp
        except Exception:
            continue
    raise HTTPException(400, '参照图必须是已上传的小样或本程序的输出图')


# ============================================================
# 真实纹理投影：本地确定性 UV/透视渲染，不调生成模型。
# AI 只负责提供房间底图；小样像素、纹理比例和缝隙由此管线保真。
# ============================================================
class FloorPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class FloorVisualizeTarget(BaseModel):
    kind: Literal['job', 'record', 'room']
    jid: str = ''
    stage: Literal['b2', 'pro', 'sd35'] = 'pro'
    image_rel: str = ''
    json_path: str = ''
    record_id: str = ''
    result_id: str = Field(default='', max_length=80)
    room_path: str = ''


class FloorVisualizeRequest(BaseModel):
    target: FloorVisualizeTarget
    texture_path: str = Field(min_length=1)
    mask_b64: str = Field(min_length=8)
    calibration_quad: List[FloorPoint] = Field(min_length=4, max_length=4)
    scale: float = Field(default=1.0, ge=0.15, le=4.0)
    rotation: float = Field(default=0.0, ge=-180, le=180)
    offset_x: float = Field(default=0.0, ge=-4, le=4)
    offset_y: float = Field(default=0.0, ge=-4, le=4)
    illumination_strength: float = Field(default=0.65, ge=0, le=1.5)
    shadow_strength: float = Field(default=0.85, ge=0, le=1.5)
    feather: float = Field(default=0.008, ge=0, le=0.08)
    texture_width_mm: Optional[float] = Field(default=None, gt=0, le=100000)
    texture_height_mm: Optional[float] = Field(default=None, gt=0, le=100000)
    plank_width_mm: Optional[float] = Field(default=None, gt=0, le=5000)
    plank_length_mm: Optional[float] = Field(default=None, gt=0, le=20000)


_FLOOR_PREVIEW_MAX_SIDE = 1280
_MAX_FLOOR_MASK_BYTES = 20 * 1024 * 1024
_MAX_FLOOR_MASK_PIXELS = 30_000_000
_floor_render_lock = threading.Lock()  # 4K OpenCV working set is sizeable; serialize local renders.


def _decode_floor_mask(value: str) -> Image.Image:
    raw_value = (value or '').split(',', 1)[-1]
    try:
        raw = base64.b64decode(raw_value, validate=True)
    except Exception:
        raise HTTPException(400, '地板遮罩不是有效的 base64 PNG')
    if not raw or len(raw) > _MAX_FLOOR_MASK_BYTES:
        raise HTTPException(413, '地板遮罩文件过大')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > _MAX_FLOOR_MASK_PIXELS:
                raise HTTPException(413, '地板遮罩尺寸过大')
            mask = im.convert('L')
            mask.load()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, '地板遮罩图片无法解码')
    if mask.getbbox() is None:
        raise HTTPException(400, '地板遮罩为空，请先涂抹地面区域')
    return mask


def _resolve_floor_source(target: FloorVisualizeTarget):
    """Return detached RGB image plus the validated write-back context."""
    if target.kind == 'job':
        job = JOBS.get(target.jid)
        if not job:
            raise HTTPException(404, 'job not found')
        if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
            raise HTTPException(409, '任务进行中，请稍后贴地板')
        abs_src = _require_output_image_rel(target.image_rel)
        ensure_model_runs(job)
        candidates = {os.path.realpath(str(p)) for p in
                      ((job.model_runs.get(target.stage) or {}).get('paths') or []) if p}
        if os.path.realpath(abs_src) not in candidates:
            raise HTTPException(400, '该图不属于此任务的候选')
        with Image.open(abs_src) as im:
            src = ImageOps.exif_transpose(im).convert('RGB').copy()
        return src, {'job': job, 'source_path': abs_src}
    if target.kind == 'record':
        json_path = _require_record_json_path(target.json_path)
        recs = load_records_file(json_path)
        rec = next((r for r in recs if r.get('id') == target.record_id), None)
        if not rec:
            raise HTTPException(404, '未找到记录')
        result = next((r for r in rec.get('results', []) if r.get('result_id') == target.result_id), None)
        if result is None:
            raise HTTPException(404, '未找到该效果图')
        rel = result.get('result_image_file')
        abs_src = safe_output_path(rel) if rel else None
        src = None
        if abs_src and os.path.isfile(abs_src):
            with Image.open(abs_src) as im:
                src = ImageOps.exif_transpose(im).convert('RGB').copy()
        elif result.get('result_image_b64'):
            decoded = b64_to_pil(result['result_image_b64'])
            if decoded is not None:
                src = ImageOps.exif_transpose(decoded).convert('RGB').copy()
        if src is None:
            raise HTTPException(404, '该结果无可用图片')
        return src, {'json_path': json_path, 'record': rec, 'source_path': abs_src or json_path}
    room_path = _require_upload_image_path(target.room_path, '房间图', required=True)
    with Image.open(room_path) as im:
        src = ImageOps.exif_transpose(im).convert('RGB').copy()
    return src, {'room_path': room_path, 'source_path': room_path}


def _run_floor_visualize(req: FloorVisualizeRequest, max_side: int = 0):
    if load_config().get('floor_visualizer_enabled', True) is False:
        raise HTTPException(503, '真实纹理投影已在配置中关闭')
    try:
        validate_calibration_quad(req.calibration_quad)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    scene, context = _resolve_floor_source(req.target)
    texture_path = _require_ref_image_path(req.texture_path)
    mask = _decode_floor_mask(req.mask_b64)
    try:
        with Image.open(texture_path) as im:
            texture = ImageOps.exif_transpose(im).convert('RGB').copy()
        recipe = RenderRecipe(
            scale=req.scale, rotation=req.rotation,
            offset_x=req.offset_x, offset_y=req.offset_y,
            illumination_strength=req.illumination_strength,
            shadow_strength=req.shadow_strength, feather=req.feather,
            texture_width_mm=req.texture_width_mm,
            texture_height_mm=req.texture_height_mm,
        )
        with _floor_render_lock:
            out, metadata = render_floor(scene, texture, mask, req.calibration_quad,
                                         recipe=recipe, max_side=max_side)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    except Exception as ex:
        logger.exception('[真实贴地板] 渲染失败')
        raise HTTPException(500, f'本地渲染失败：{ex}')
    metadata.update({
        'source_sha256': image_sha256(scene),
        'texture_path': os.path.basename(texture_path),
        'physical_dimensions': {
            'texture_width_mm': req.texture_width_mm,
            'texture_height_mm': req.texture_height_mm,
            'plank_width_mm': req.plank_width_mm,
            'plank_length_mm': req.plank_length_mm,
        },
    })
    return out, metadata, context


@app.post('/api/floor-visualize/preview')
def floor_visualize_preview(req: FloorVisualizeRequest):
    out, metadata, _ = _run_floor_visualize(req, max_side=_FLOOR_PREVIEW_MAX_SIDE)
    buf = io.BytesIO()
    out.save(buf, format='PNG', optimize=True)
    return {
        'preview': 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode(),
        'width': out.width,
        'height': out.height,
        'warnings': metadata.get('warnings') or [],
        'metadata': metadata,
    }


@app.post('/api/floor-visualize/apply')
async def floor_visualize_apply(req: FloorVisualizeRequest):
    out, metadata, context = await asyncio.to_thread(_run_floor_visualize, req, 0)
    label = '真实纹理投影'
    target = req.target
    if target.kind == 'job':
        job = context['job']
        current = JOBS.get(job.job_id)
        if current is not job:
            raise HTTPException(409, '任务卡已被清除，无法写回')
        if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
            raise HTTPException(409, '任务状态已变化，请稍后重试')
        ppath = await asyncio.to_thread(save_api_result_png, out, label,
                                        job.png_path or context['source_path'], metadata)
        if not ppath:
            raise HTTPException(500, '结果保存失败')
        add_model_candidate(job, target.stage, ppath)
        update_job(job, status=compute_runs_final_status(job))
        if job.json_path and job.record_id:
            await asyncio.to_thread(api_write_to_record, out, label, job.json_path,
                                    job.record_id, ppath, metadata)
        JOBS.persist()
        logger.info(f'[真实贴地板] 任务候选已保存 job={job.job_id}, stage={target.stage}, path={ppath}')
        return {'ok': True, 'job': _job_view(job), 'url': _to_url(ppath),
                'warnings': metadata.get('warnings') or [], 'metadata': metadata}
    if target.kind == 'record':
        json_path = context['json_path']
        ppath = await asyncio.to_thread(save_api_result_png, out, label,
                                        context['source_path'], metadata)
        if not ppath:
            raise HTTPException(500, '结果保存失败')
        result_id = await asyncio.to_thread(api_write_to_record, out, label, json_path,
                                            target.record_id, ppath, metadata)
        if not result_id:
            raise HTTPException(500, '结果写入记录失败')
        return {'ok': True, 'result_url': _to_url(ppath), 'result_id': result_id,
                'warnings': metadata.get('warnings') or [], 'metadata': metadata}
    room_path = context['room_path']
    stem = os.path.splitext(os.path.basename(room_path))[0]
    dest = safe_upload_path(f'{stem}_floor.png', 'room_')
    if not dest:
        raise HTTPException(500, '结果保存路径无效')
    await asyncio.to_thread(lambda: out.save(dest, format='PNG', optimize=True))
    return {'ok': True, 'path': dest, 'url': _to_url(dest), 'thumb': _thumb_url(dest),
            'warnings': metadata.get('warnings') or [], 'metadata': metadata}


class ColorMatchRect(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class ColorMatchAdjustments(BaseModel):
    """以 Gemini 原图为零点的全图专业调整。"""
    temperature: float = Field(default=0, ge=-100, le=100)
    tint: float = Field(default=0, ge=-100, le=100)
    exposure: float = Field(default=0, ge=-2, le=2)
    contrast: float = Field(default=0, ge=-100, le=100)
    highlights: float = Field(default=0, ge=-100, le=100)
    shadows: float = Field(default=0, ge=-100, le=100)
    whites: float = Field(default=0, ge=-100, le=100)
    blacks: float = Field(default=0, ge=-100, le=100)
    midtones: float = Field(default=0, ge=-100, le=100)
    saturation: float = Field(default=0, ge=-100, le=100)


class ColorMatchPreviewRequest(BaseModel):
    image_rel: str = Field(min_length=1)   # 成图相对 /outputs 路径
    ref_path: str = Field(min_length=1)    # 参照小样绝对路径
    rect: ColorMatchRect                 # 只用于地板统计和三区诊断
    strength: float = Field(default=0.8, ge=0, le=1)
    feather: float = Field(default=0.05, ge=0, le=0.3)  # 兼容字段，全图校色忽略
    adjustments: ColorMatchAdjustments = Field(default_factory=ColorMatchAdjustments)
    adjustment_mode: Literal['auto', 'manual'] = 'auto'
    include_analysis: bool = False


_PREVIEW_MAX_SIDE = 1600


def _run_color_match(abs_src: str, ref_path: str, rect: ColorMatchRect,
                     strength: float, feather: float, max_side: int = 0,
                     adjustments: Optional[ColorMatchAdjustments] = None,
                     adjustment_mode: Literal['auto', 'manual'] = 'auto',
                     return_auto_adjustments: bool = False,
                     return_analysis: bool = False):
    """读图 → （可选缩到 max_side 长边）→ 全图校色。
    rect 只用于地板统计/诊断，feather 保留为兼容字段但不再限制修改范围。"""
    src = Image.open(abs_src)
    src.load()
    if max_side:
        src.thumbnail((max_side, max_side), Image.LANCZOS)
    ref = Image.open(ref_path)
    ref.load()
    result = match_color_global(src, ref, (rect.x, rect.y, rect.w, rect.h),
                                strength=strength, feather=feather,
                                adjustments=(adjustments.model_dump() if adjustments else None),
                                adjustment_mode=adjustment_mode,
                                return_auto_adjustments=return_auto_adjustments)
    if not return_analysis:
        return result
    out, auto_adjustments = result
    analysis = analyze_color_region(src, ref, (rect.x, rect.y, rect.w, rect.h))
    return out, auto_adjustments, analysis


def _serialize_color_analysis(analysis: dict) -> dict:
    """Encode representative PIL crops for the stateless JSON preview response."""
    serialized = {key: value for key, value in analysis.items() if key != 'zones'}
    zones = []
    for zone in analysis.get('zones') or []:
        item = {key: value for key, value in zone.items() if key != 'image'}
        image = zone.get('image')
        item['preview'] = None
        if image is not None:
            thumb = image.convert('RGB').copy()
            thumb.thumbnail((480, 360), Image.LANCZOS)
            buf = io.BytesIO()
            thumb.save(buf, format='JPEG', quality=86)
            item['preview'] = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
        zones.append(item)
    serialized['zones'] = zones
    return serialized


@app.post('/api/color-match/preview')
def color_match_preview(req: ColorMatchPreviewRequest):
    """预览：缩图处理，返回 data URL（1600 长边 JPEG，无临时文件、无状态）。
    同步 def → FastAPI 线程池执行，numpy 不堵事件循环。"""
    abs_src = _require_output_image_rel(req.image_rel)
    ref = _require_ref_image_path(req.ref_path)
    result = _run_color_match(
        abs_src, ref, req.rect, req.strength, req.feather,
        max_side=_PREVIEW_MAX_SIDE, adjustments=req.adjustments,
        adjustment_mode=req.adjustment_mode, return_auto_adjustments=True,
        return_analysis=req.include_analysis)
    if req.include_analysis:
        out, auto_adjustments, analysis = result
    else:
        out, auto_adjustments = result
        analysis = None
    buf = io.BytesIO()
    out.save(buf, format='JPEG', quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    response = {'preview': f'data:image/jpeg;base64,{b64}',
                'width': out.size[0], 'height': out.size[1],
                'auto_adjustments': auto_adjustments}
    if analysis is not None:
        response['analysis'] = _serialize_color_analysis(analysis)
    return response


class JobColorMatchRequest(ColorMatchPreviewRequest):
    ref_path: str = ''                       # 空 → 回退本任务地板小样(job.png_path)
    stage: Literal['b2', 'pro', 'sd35'] = 'pro'


@app.post('/api/jobs/{jid}/color-match')
async def job_color_match(jid: str, req: JobColorMatchRequest):
    """提交（任务侧）：全分辨率处理 → 落盘 → 并入该 stage 候选（‹n/N› 可切回原图）→ 写记录。"""
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
        raise HTTPException(409, '任务进行中，请稍后校色')
    abs_src = _require_output_image_rel(req.image_rel)
    ensure_model_runs(job)
    # 归属校验：必须是该任务对应 stage 的候选之一（防跨任务写入 + 免候选下标竞态）
    cand = {os.path.realpath(str(p)) for p in ((job.model_runs.get(req.stage) or {}).get('paths') or []) if p}
    if os.path.realpath(abs_src) not in cand:
        raise HTTPException(400, '该图不属于此任务的候选')
    ref = _require_ref_image_path(req.ref_path or job.png_path or '')
    out = await asyncio.to_thread(_run_color_match, abs_src, ref, req.rect,
                                  req.strength, req.feather,
                                  adjustments=req.adjustments,
                                  adjustment_mode=req.adjustment_mode)
    ppath = await asyncio.to_thread(save_api_result_jpg, out, '手动校色',
                                    job.png_path or abs_src)
    if not ppath:
        raise HTTPException(500, '校色结果保存失败')
    add_model_candidate(job, req.stage, ppath)
    update_job(job, status=compute_runs_final_status(job))
    if job.json_path and job.record_id:
        try:
            await asyncio.to_thread(api_write_to_record, out, '手动校色',
                                    job.json_path, job.record_id, ppath)
        except Exception as ex:
            logger.warning(f"[校色] 写记录失败 job={jid}: {ex}")
    JOBS.persist()   # 锁外调（内部自取锁）
    logger.info(f"[校色] 完成 job={jid}, stage={req.stage}, strength={req.strength}, path={ppath}")
    return _job_view(job)


class RecordColorMatchRequest(BaseModel):
    json_path: str
    record_id: str
    result_id: str = Field(min_length=1, max_length=80)
    ref_path: str = ''                       # 空 → 回退记录同目录优化图/历史小样
    rect: ColorMatchRect                 # 只用于地板统计
    strength: float = Field(default=0.8, ge=0, le=1)
    feather: float = Field(default=0.05, ge=0, le=0.3)  # 兼容字段，全图校色忽略
    adjustments: ColorMatchAdjustments = Field(default_factory=ColorMatchAdjustments)
    adjustment_mode: Literal['auto', 'manual'] = 'auto'


@app.post('/api/records/color-match')
def record_color_match(req: RecordColorMatchRequest):
    """提交（记录侧）：全分辨率处理 → 结果 append 回该记录。不碰 JOBS 队列。"""
    json_path = _require_record_json_path(req.json_path)
    recs = load_records_file(json_path)
    rec = next((r for r in recs if r.get('id') == req.record_id), None)
    if not rec:
        raise HTTPException(404, '未找到记录')
    res = next((item for item in rec.get('results', [])
                if item.get('result_id') == req.result_id), None)
    if res is None:
        raise HTTPException(404, '未找到该效果图')
    rel = res.get('result_image_file')
    abs_src = safe_output_path(rel) if rel else None
    if not abs_src or not os.path.isfile(abs_src):
        raise HTTPException(404, '该结果无落盘图片，无法校色')
    ref_path = (req.ref_path or '').strip()
    if not ref_path:
        ref_path = _record_color_match_ref_path(json_path, rec)
    if not ref_path:
        raise HTTPException(400, '该记录没有参照小样，请在弹窗中上传参照图')
    ref = _require_ref_image_path(ref_path)
    out = _run_color_match(abs_src, ref, req.rect, req.strength, req.feather,
                           adjustments=req.adjustments,
                           adjustment_mode=req.adjustment_mode)
    ppath = save_api_result_jpg(out, '手动校色', json_path.replace('_记录.json', '_优化图.png'))
    if not ppath:
        raise HTTPException(500, '校色结果保存失败')
    if req.adjustment_mode == 'auto':
        label = f'自动强度{req.strength:.2f}'
    else:
        label = ('原图基准 · 手动微调' if any(req.adjustments.model_dump().values())
                 else 'Gemini 原图')
    msg = append_edited_result_to_record(json_path, req.record_id, req.result_id,
                                         out, label, '手动校色', ppath)
    if not str(msg).startswith('✅'):
        raise HTTPException(500, str(msg))
    return {'ok': True, 'result_url': _to_url(ppath)}


# ============================================================
# 生成式修补（inpaint：移除自动外扩；添加默认严格限制在涂抹区）
# 引擎按 inpaint_provider 与 remove/add 模型配置分派；comfyui=用户自备实例。
# 三个入口：job 结果二改 / records 记录图 / 上传房间图预处理（生成前清理家具）。
# ============================================================
class InpaintPayload(BaseModel):
    mask_b64: str = Field(min_length=1, max_length=12_000_000)  # 纯 base64 PNG，白=重绘区
    prompt: str = Field(default='', max_length=2000)
    mode: Literal['remove', 'add'] = 'remove'
    grow: Optional[int] = Field(default=None, ge=0, le=64)  # 空值：remove=8，add=0
    feather: float = Field(default=0.01, ge=0, le=0.1)   # 羽化半径 / 短边比例
    seed: Optional[int] = None
    n: int = Field(default=3, ge=1, le=3)        # 候选数（Lightroom 式抽卡；n 张记 n 次费用）


def _require_inpaint_prompt(req: InpaintPayload) -> None:
    if req.mode == 'add' and not (req.prompt or '').strip():
        raise HTTPException(400, '生成式添加需要描述要添加的内容')


def _decode_inpaint_mask(mask_b64: str) -> Image.Image:
    try:
        raw = base64.b64decode(mask_b64, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format or '').upper() != 'PNG':
                raise HTTPException(400, '遮罩必须是 PNG 图片')
            if image.width * image.height > 5_000_000:
                raise HTTPException(413, '遮罩像素超过 500 万上限')
            image.load()
            return image.copy()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, '遮罩解码失败（需要 PNG 的 base64）')


def _prepare_inpaint_masks(mask: Image.Image, target_size, grow: int, feather: float,
                           mode: str) -> tuple[Image.Image, Image.Image]:
    """返回 (engine_mask, blend_mask)：模型只看二值范围，最终合成单独使用羽化范围。

    remove 自动外扩以覆盖物体边缘/阴影；add 只采用用户显式 grow（前端默认 0）。
    add 羽化限制在有效选区内部，所以默认选区外逐像素不变。"""
    m = mask.convert('L')
    tw, th = target_size
    if m.size != (tw, th):
        mw, mh = m.size
        if abs((mw / mh) - (tw / th)) > 0.01 * (tw / th):
            raise HTTPException(400, '遮罩与原图宽高比不一致，请重新涂抹')
        m = m.resize((tw, th), Image.NEAREST)
    m = m.point(lambda v: 255 if v >= 128 else 0)
    bbox = m.getbbox()
    if not bbox:
        raise HTTPException(400, '遮罩为空：请先在图上涂抹要处理的区域')
    auto_grow = min(64, round(0.08 * max(bbox[2] - bbox[0], bbox[3] - bbox[1]))) if mode == 'remove' else 0
    effective_grow = max(grow, auto_grow)
    engine_mask = m
    if effective_grow > 0:
        engine_mask = m.filter(ImageFilter.GaussianBlur(effective_grow)).point(
            lambda v: 255 if v >= 32 else 0)
    blend_mask = engine_mask
    if feather > 0:
        blurred = engine_mask.filter(ImageFilter.GaussianBlur(max(1.0, feather * min(tw, th))))
        blend_mask = (ImageChops.multiply(engine_mask, blurred) if mode == 'add' else blurred)
    return engine_mask, blend_mask


def _prepare_inpaint_mask(mask: Image.Image, target_size, grow: int, feather: float) -> Image.Image:
    """旧内部调用兼容：沿用 remove 的最终合成 mask。"""
    return _prepare_inpaint_masks(mask, target_size, grow, feather, 'remove')[1]


def _normalize_inpaint_source(image: Image.Image) -> Image.Image:
    """对齐浏览器的 EXIF 方向，并脱离原文件句柄。"""
    normalized = ImageOps.exif_transpose(image).convert('RGB')
    normalized.load()
    return normalized.copy()


# ── 通用修补流：生成候选 → 挑选 → 提交（Lightroom 式抽卡体验）────────────
# 三种目标（job 候选 / 记录结果 / 房间图）统一走 INPAINTS 轮询表：
#   POST /api/inpaint → 后台并发生成 n 个候选（临时目录，前端轮询挑选）
#   → POST /api/inpaint/{iid}/apply 才落到目标。usage 在生成时记（n 张记 n 次），apply 不计费。
_MAX_INPAINTS = 20
_MAX_ACTIVE_INPAINTS = 3       # 1 个执行 + 最多 2 个等待，避免付费任务无限堆积
_INPAINT_TMP_DIR = os.path.join(MAIN_OUTPUT_DIR, '_inpaint_candidates')


def _delete_inpaint_files(entry) -> None:
    for cand in (entry or {}).get('candidates') or []:
        try:
            p = cand.get('path')
            if p and os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


def _inpaint_is_terminal(v: dict) -> bool:
    return v.get('status') in ('done', 'failed', 'cancelled')


# iid → {status, stage, candidates, error, ts, target, mode, prompt};trim 连带删临时候选文件
INPAINTS = TaskRegistry('inpaints', max_entries=_MAX_INPAINTS,
                        is_terminal=_inpaint_is_terminal, on_evict=_delete_inpaint_files)


def _save_inpaint_candidate_png(image: Image.Image, path: str) -> None:
    """原子保存无损候选；避免 JPEG 临时图 + 最终图二次有损编码。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.inpaint_', suffix='.png', dir=os.path.dirname(path))
    os.close(fd)
    try:
        image.convert('RGB').save(tmp, format='PNG', optimize=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _inpaint_queue_is_full(entries) -> bool:
    """在 INPAINTS.locked() 块内检查运行中背压与会话表硬上限。"""
    active = sum(v.get('status') in ('running', 'applying') for v in entries.values())
    return active >= _MAX_ACTIVE_INPAINTS or len(entries) >= _MAX_INPAINTS


class InpaintTarget(BaseModel):
    """修补目标：job 候选 / 记录结果 / 房间图。字段按 kind 选用，校验在 _resolve_inpaint_source。"""
    kind: Literal['job', 'record', 'room']
    jid: str = ''
    stage: Literal['b2', 'pro', 'sd35'] = 'pro'
    image_rel: str = ''
    json_path: str = ''
    record_id: str = ''
    result_id: str = Field(default='', max_length=80)
    room_path: str = ''


class GenericInpaintRequest(InpaintPayload):
    target: InpaintTarget


class InpaintApplyRequest(BaseModel):
    index: int = Field(ge=0, le=2)


def _resolve_inpaint_source(target: InpaintTarget):
    """按 target 定位源图，校验逻辑与旧三端点一致。返回 (src_pil, workflow_mode, operation)。"""
    if target.kind == 'job':
        job = JOBS.get(target.jid)
        if not job:
            raise HTTPException(404, 'job not found')
        if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
            raise HTTPException(409, '任务进行中，请稍后修补')
        abs_src = _require_output_image_rel(target.image_rel)
        ensure_model_runs(job)
        cand = {os.path.realpath(str(p)) for p in ((job.model_runs.get(target.stage) or {}).get('paths') or []) if p}
        if os.path.realpath(abs_src) not in cand:
            raise HTTPException(400, '该图不属于此任务的候选')
        with Image.open(abs_src) as image:
            src = _normalize_inpaint_source(image)
        return src, job.workflow_mode, 'inpaint'
    if target.kind == 'record':
        json_path = _require_record_json_path(target.json_path)
        recs = load_records_file(json_path)
        rec = next((r for r in recs if r.get('id') == target.record_id), None)
        if not rec:
            raise HTTPException(404, '未找到记录')
        res = next((item for item in rec.get('results', [])
                    if item.get('result_id') == target.result_id), None)
        if res is None:
            raise HTTPException(404, '未找到该效果图')
        src = None
        rel = res.get('result_image_file')
        abs_src = safe_output_path(rel) if rel else None
        if abs_src and os.path.isfile(abs_src):
            try:
                with Image.open(abs_src) as image:
                    src = _normalize_inpaint_source(image)
            except Exception:
                src = None
        if src is None and res.get('result_image_b64'):
            decoded = b64_to_pil(res['result_image_b64'])
            src = _normalize_inpaint_source(decoded) if decoded is not None else None
        if src is None:
            raise HTTPException(404, '该结果无可用图片')
        return src, rec.get('workflow_mode', ''), 'record_inpaint'
    room = _require_upload_image_path(target.room_path, '房间图', required=True)
    with Image.open(room) as image:
        src = _normalize_inpaint_source(image)
    return src, '房间图预处理', 'room_prep'


async def _generic_inpaint_bg(iid: str, src_pil, engine_mask, blend_mask, prompt, mode, seed, n,
                              workflow_mode, operation, resolved_engine):
    """并发生成 n 个候选（信号量整体持有一次=一次用户操作），部分失败仍交付成功的候选。"""
    def _set(**kw):
        INPAINTS.update_fields(iid, **kw)

    should_cancel = lambda: INPAINTS.is_cancelled(iid)
    try:
        async with _model_semaphores['inpaint']:
            async def one(i: int):
                variant_seed = (int(seed) + i) if seed is not None else None
                # 只让第 0 路上报阶段文案，避免多路互相覆盖
                stage_cb = (lambda t: _set(stage=t)) if i == 0 else None
                return await asyncio.to_thread(
                    call_image_inpaint, src_pil, engine_mask, prompt, blend_mask=blend_mask, mode=mode,
                    seed=variant_seed, on_stage=stage_cb, should_cancel=should_cancel,
                    resolved_engine=resolved_engine)
            results = await asyncio.gather(*[one(i) for i in range(max(1, n))],
                                           return_exceptions=True)
        os.makedirs(_INPAINT_TMP_DIR, exist_ok=True)
        candidates = []
        last_err = ''
        for i, res in enumerate(results):
            if isinstance(res, BaseException):
                logger.error(f'[修补] 候选 {i} 异常 iid={iid}: {res}')
                last_err = str(res)
                continue
            out, err, provider, usage_label = res
            if out is None:
                last_err = str(err or '生成失败')
                if '取消' not in last_err and not should_cancel():
                    record_usage(workflow_mode, usage_label, provider, False, operation)
                continue
            # 上游已经成功出图即可能产生费用；即便用户随后取消，也按成功调用记账。
            record_usage(workflow_mode, usage_label, provider, True, operation)
            if should_cancel():
                continue
            path = os.path.join(_INPAINT_TMP_DIR, f'{iid}_{i}.png')
            try:
                await asyncio.to_thread(_save_inpaint_candidate_png, out, path)
            except Exception as ex:
                logger.error(f'[修补] 候选 {i} 保存失败 iid={iid}: {ex}')
                last_err = f'候选保存失败：{ex}'
                continue
            candidates.append({'url': _to_url(path), 'thumb': _result_thumb_url(path), 'path': path})
        if should_cancel():
            _delete_inpaint_files({'candidates': candidates})
            _set(status='cancelled', error='已取消', stage='', candidates=[])
            return
        if not candidates:
            _set(status='failed', error=last_err or '修补失败', stage='')
            return
        note = f'{max(1, n) - len(candidates)} 个候选失败：{last_err}' if len(candidates) < max(1, n) and last_err else ''
        _set(status='done', stage='', candidates=candidates, error=note)
        logger.info(f'[修补] 候选就绪 iid={iid}, {len(candidates)}/{n}')
    except Exception as e:
        logger.exception(f'[修补] 异常 iid={iid}')
        _set(status='failed', error=str(e), stage='')
    finally:
        INPAINTS.clear_cancelled(iid)


@app.post('/api/inpaint')
async def create_inpaint(req: GenericInpaintRequest):
    """提交修补：定位源图 → 标准化 mask → 后台并发生成 n 个候选，前端轮询后挑选提交。"""
    _require_inpaint_prompt(req)
    src_pil, workflow_mode, operation = _resolve_inpaint_source(req.target)
    mask_raw = _decode_inpaint_mask(req.mask_b64)
    requested_grow = req.grow if req.grow is not None else (8 if req.mode == 'remove' else 0)
    engine_mask, blend_mask = await asyncio.to_thread(
        _prepare_inpaint_masks, mask_raw, src_pil.size, requested_grow, req.feather, req.mode)
    resolved_engine = resolve_inpaint_engine(req.mode)
    effective_n, notice = effective_inpaint_candidate_count(
        req.mode, req.n, resolved_engine=resolved_engine)
    iid = f'ip_{uuid.uuid4().hex}'
    with INPAINTS.locked() as entries:
        INPAINTS.trim_locked(reserve=1)
        if _inpaint_queue_is_full(entries):
            raise HTTPException(429, '修补队列已满，请等待当前任务完成后再试')
        entries[iid] = {'status': 'running', 'stage': '', 'candidates': [], 'error': '',
                          'ts': time.time(), 'target': req.target.model_dump(),
                          'mode': req.mode, 'prompt': (req.prompt or '').strip(),
                          'requested_n': req.n, 'effective_n': effective_n, 'notice': notice,
                          'provider': resolved_engine[0], 'model_key': resolved_engine[1]}
    _spawn(_generic_inpaint_bg(iid, src_pil, engine_mask, blend_mask, req.prompt, req.mode, req.seed,
                               effective_n, workflow_mode, operation, resolved_engine))
    return {'inpaint_id': iid, 'requested_n': req.n, 'effective_n': effective_n, 'notice': notice}


@app.post('/api/inpaint/{iid}/apply')
async def inpaint_apply(iid: str, req: InpaintApplyRequest):
    """把选中的候选提交到目标（apply 不计费；成功后清理该次全部临时候选）。"""
    with INPAINTS.locked() as entries:
        entry = entries.get(iid)
        if not entry:
            raise HTTPException(404, 'inpaint task not found')
        if entry.get('status') != 'done':
            raise HTTPException(409, '候选尚未就绪')
        candidates = entry.get('candidates') or []
        if req.index >= len(candidates):
            raise HTTPException(400, '候选序号无效')
        cand_path = candidates[req.index].get('path') or ''
        entry['status'] = 'applying'  # 锁内抢占；并发第二次 apply 会得到 409
    try:
        if not os.path.isfile(cand_path):
            raise HTTPException(410, '候选文件已被清理，请重新生成')
        out = await asyncio.to_thread(lambda: (lambda im: (im.load(), im)[1])(Image.open(cand_path)))
        target = InpaintTarget(**entry['target'])
        mode = entry.get('mode') or 'remove'
        prompt = entry.get('prompt') or ''
        label = '生成式移除' if mode == 'remove' else '生成式添加'
        if target.kind == 'job':
            job = JOBS.get(target.jid)
            if not job:
                raise HTTPException(404, '任务卡已被清除，无法写回')
            if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
                raise HTTPException(409, '任务进行中，请稍后提交')
            ppath = await asyncio.to_thread(save_api_result_png, out, label, job.png_path or cand_path)
            if not ppath:
                raise HTTPException(500, '结果保存失败')
            add_model_candidate(job, target.stage, ppath)
            update_job(job, status=compute_runs_final_status(job))
            if job.json_path and job.record_id:
                try:
                    await asyncio.to_thread(api_write_to_record, out, label, job.json_path, job.record_id, ppath)
                except Exception as ex:
                    logger.warning(f'[修补] 写记录失败 iid={iid}: {ex}')
            JOBS.persist()
            resp = {'ok': True, 'job': _job_view(job)}
        elif target.kind == 'record':
            json_path = _require_record_json_path(target.json_path)
            ppath = await asyncio.to_thread(save_api_result_png, out, label,
                                            json_path.replace('_记录.json', '_优化图.png'))
            if not ppath:
                raise HTTPException(500, '结果保存失败')
            msg = await asyncio.to_thread(append_edited_result_to_record, json_path, target.record_id,
                                          target.result_id, out, prompt or label, label, ppath)
            if not str(msg).startswith('✅'):
                raise HTTPException(500, str(msg))
            resp = {'ok': True, 'result_url': _to_url(ppath)}
        else:
            stem = os.path.splitext(os.path.basename(target.room_path))[0]
            dest = safe_upload_path(f'{stem}_clean.png', 'room_')
            if not dest:
                raise HTTPException(500, '结果保存路径无效')
            await asyncio.to_thread(lambda: out.convert('RGB').save(dest, format='PNG', optimize=True))
            resp = {'ok': True, 'path': dest, 'url': _to_url(dest), 'thumb': _thumb_url(dest)}
    except Exception:
        with INPAINTS.locked() as entries:
            if entries.get(iid) is entry:
                entry['status'] = 'done'
        raise
    entry = INPAINTS.pop(iid)   # pop 连带清取消标记
    _delete_inpaint_files(entry)
    logger.info(f'[修补] 已提交候选 iid={iid}, kind={target.kind}, index={req.index}')
    return resp


@app.get('/api/inpaint/comfyui/ping')
def comfyui_ping(url: str = ''):
    """后端代理探测 ComfyUI（浏览器直连内网地址会撞 CORS，一切 ComfyUI 通信走后端）。"""
    base = (url or get_comfyui_settings()['base_url']).strip().rstrip('/')
    if not base:
        raise HTTPException(400, '未配置 ComfyUI 地址')
    import requests as _preq
    session = _preq.Session()
    session.trust_env = False   # 内网地址不走系统代理
    try:
        resp = session.get(f'{base}/system_stats', timeout=(5, 10))
        resp.raise_for_status()
        stats = resp.json()
        devices = [d.get('name', '') for d in (stats.get('devices') or [])]
        return {'ok': True, 'version': (stats.get('system') or {}).get('comfyui_version', ''),
                'devices': devices}
    except Exception as ex:
        return {'ok': False, 'error': str(ex)[:300]}


@app.get('/api/inpaint/{iid}')
def inpaint_status(iid: str):
    with INPAINTS.locked() as entries:
        v = entries.get(iid)
        if not v:
            raise HTTPException(404, 'inpaint task not found')
        # 只回传前端需要的字段（path 是服务端内部路径，target/prompt 前端已知）
        return {'inpaint_id': iid, 'status': v.get('status', ''), 'stage': v.get('stage', ''),
                'error': v.get('error', ''), 'notice': v.get('notice', ''),
                'requested_n': v.get('requested_n', 1), 'effective_n': v.get('effective_n', 1),
                'candidates': [{'url': c.get('url', ''), 'thumb': c.get('thumb', '')}
                               for c in (v.get('candidates') or [])]}


@app.post('/api/inpaint/{iid}/cancel')
def inpaint_cancel(iid: str):
    """running 中 = 标记取消(引擎轮询停止)；终态 = 直接清理（前端「再抽」前废弃旧候选）。"""
    with INPAINTS.locked() as entries:
        entry = entries.get(iid)
        if not entry:
            raise HTTPException(404, 'inpaint task not found')
        if entry.get('status') == 'applying':
            raise HTTPException(409, '候选正在写入，无法取消')
        if entry.get('status') != 'running':
            _delete_inpaint_files(entry)
            entries.pop(iid, None)
            INPAINTS.clear_cancelled(iid)
            return {'cancelled': True}
    INPAINTS.request_cancel(iid)
    return {'cancelled': True}


# ── 失败知识库：常见失败参考 ──
@app.get('/api/failure/rules')
def failure_rules():
    out = []
    for r in FAILURE_RULES:
        d = r if isinstance(r, dict) else {}
        out.append({
            'key': d.get('key', ''),
            'title': d.get('title', ''),
            'cause': d.get('cause', ''),
            'action': d.get('action', ''),
        })
    return [r for r in out if r['title']]


# ============================================================
# 静态/缩略图（移植自 webui：懒生成缩略图 + safe_output_path 越界防护）
# ============================================================
@app.get('/thumb/uploads/{name}')
def serve_upload_thumb(name: str, s: int = 320):
    name = os.path.basename(name)   # 挡路径穿越
    src = os.path.join(UPLOAD_DIR, name)
    if os.path.splitext(name)[1].lower() not in _IMAGE_EXTS or not os.path.isfile(src):
        return Response(status_code=404)
    s = max(64, min(int(s), 1600))
    try:
        mtime = int(os.path.getmtime(src))
    except OSError:
        return Response(status_code=404)
    stem = os.path.splitext(name)[0]
    cache = os.path.join(THUMB_DIR, f'{stem}__{mtime}__{s}.jpg')
    if not os.path.exists(cache):
        try:
            im = Image.open(src)
            im.draft('RGB', (s, s))
            im = im.convert('RGB')
            im.thumbnail((s, s), Image.Resampling.LANCZOS)
            tmp = cache + '.tmp'
            im.save(tmp, 'JPEG', quality=82)
            os.replace(tmp, cache)
        except Exception as ex:
            logger.warning(f"[缩略图] 生成失败 {name}: {ex}")
            return Response(status_code=415)
    return FileResponse(cache, media_type='image/jpeg')


@app.get('/thumb/outputs/{relpath:path}')
def serve_output_thumb(relpath: str, s: int = 480):
    src = safe_output_path(relpath)   # 越界/不存在 → None
    if not src or os.path.splitext(src)[1].lower() not in _IMAGE_EXTS:
        return Response(status_code=404)
    s = max(64, min(int(s), 1600))
    try:
        mtime = int(os.path.getmtime(src))
    except OSError:
        return Response(status_code=404)
    key = hashlib.md5(f'{os.path.realpath(src)}__{mtime}__{s}'.encode('utf-8')).hexdigest()
    cache = os.path.join(THUMB_DIR, f'out_{key}.jpg')
    if not os.path.exists(cache):
        try:
            im = Image.open(src)
            im.draft('RGB', (s, s))
            im = im.convert('RGB')
            im.thumbnail((s, s), Image.Resampling.LANCZOS)
            tmp = cache + '.tmp'
            im.save(tmp, 'JPEG', quality=82)
            os.replace(tmp, cache)
        except Exception as ex:
            logger.warning(f"[结果缩略图] 生成失败 {relpath}: {ex}")
            return Response(status_code=415)
    return FileResponse(cache, media_type='image/jpeg')


@app.get('/outputs/{relpath:path}')
def serve_output_image(relpath: str):
    path = safe_output_path(relpath)
    if not path or os.path.splitext(path)[1].lower() not in _IMAGE_EXTS:
        return Response(status_code=404)
    return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')


@app.get('/uploads/{name}')
def serve_upload_image(name: str):
    name = os.path.basename(name)
    path = os.path.realpath(os.path.join(UPLOAD_DIR, name))
    if (os.path.splitext(path)[1].lower() not in _IMAGE_EXTS
            or os.path.commonpath([os.path.realpath(UPLOAD_DIR), path]) != os.path.realpath(UPLOAD_DIR)
            or not os.path.isfile(path)):
        return Response(status_code=404)
    return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')


# ============================================================
# 前端静态站（Next.js 静态导出 web/out）——「单一程序」用：
# 后端直接把前端整站挂在 /，做到一个进程、一个端口。
# 本 mount 必须放在最后：/ 是贪婪匹配，注册在最后才不会盖住上面的
# /api、/thumb、/outputs、/uploads。找不到 out/（纯后端开发）时自动跳过。
# ============================================================
def _find_frontend_dir():
    """依次探测前端静态目录，兼容源码运行与 Nuitka onefile 冻结后。"""
    import sys
    cands = []
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, 'web', 'out'))          # 源码/dev 布局
    # Nuitka onefile：数据被 --include-data-dir 释放到解包目录；exe 同级或其内
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0] or sys.executable))
    cands.append(os.path.join(exe_dir, 'web', 'out'))       # out/ 与 exe 同级（非内嵌时）
    cands.append(os.path.join(here, '..', 'web', 'out'))    # 包在子目录时的兜底
    for d in cands:
        if os.path.isfile(os.path.join(d, 'index.html')):
            return os.path.abspath(d)
    return None


_FRONTEND_DIR = _find_frontend_dir()
if _FRONTEND_DIR:
    # html=True：目录请求回退 index.html，支持前端深链接刷新
    app.mount('/', StaticFiles(directory=_FRONTEND_DIR, html=True), name='frontend')
    logger.info(f"[前端] 已挂载静态站: {_FRONTEND_DIR}")
else:
    logger.warning("[前端] 未找到 web/out（未构建前端？），仅提供 /api 后端服务")


# ============================================================
# 直接运行入口：python -m Floor_engine_server.server_api （在 test/ 目录下）
# ============================================================
if __name__ == '__main__':
    import uvicorn
    host = os.environ.get('FLOOR_API_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('Floor Engine 当前仅支持本机监听，请使用 FLOOR_API_HOST=127.0.0.1')
    port = int(os.environ.get('FLOOR_API_PORT', '7870'))
    # 传 app 对象 = 单进程单 worker（JOBS 注册表/信号量是进程内状态，必须单 worker）
    uvicorn.run(app, host=host, port=port)
