# -*- coding: utf-8 -*-
"""Legacy job orchestration behind routes_jobs.py's compatibility alias."""
"""任务队列路由 —— 提交/列表/SSE/取消/删除/重试/磨缝/二改/重抽 + 全部生图后台协程。

4K 主编排(_run_job_bg/_edit_bg/_generate_sd35_model)是本产品命脉,测试覆盖不到它,
改动务必人工冒烟。所有端点路径与响应契约受 tests/test_route_contract.py 守护。
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from PIL import Image

from . import server_state as state
from .api import (
    call_image_generate, call_gemini_edit, analyze_style_image,
    call_fal_sd35_generate, call_fal_aura_upscale, SD35_ENDPOINT,
    FLOOR_DESEAM_INSTRUCTION, infer_aspect_ratio_from_b64,
)
from .color_match import match_color_masked, match_color_to_reference
from .config import (
    MAIN_OUTPUT_DIR, logger, load_config, GEMINI_MODEL_MAP,
    get_image_provider, get_bevel_ref_image, extract_clean_prompt,
    get_omakase_gemini_model, get_auto_color_match_enabled,
)
from .cinematic_planner import (
    CINEMATIC_FALLBACK_DIRECTION,
    plan_cinematic_scene,
    supports_cinematic,
)
from .floor_segmentation import segment_floor
from .models import (
    JobRecord, new_job, update_job, compute_final_status, add_candidate,
    TaskParams, task_params_to_kwargs, ensure_model_runs, update_model_run,
    add_model_candidate, nav_model_candidate, model_run_current_path,
    compute_runs_final_status, legacy_filter_from_targets,
)
from .prompts import save_task_files_html
from .usage_stats import record_usage
from .records import (
    save_api_result_jpg, save_api_result_png, api_write_to_record,
    safe_output_path, load_records_file, b64_to_pil,
    append_edited_result_to_record, attach_generation_context,
    create_free_generation_record,
)
from .sd_prompts import compile_sd35_prompt
from .server_helpers import (
    to_url, result_thumb_url, job_view,
    require_record_json_path, require_upload_image_path, panel_require_second_image,
)
from .server_schemas import (
    EditRequest, FreeJobSubmitRequest, JobSubmitRequest, RecordEditRequest,
    RetryJobRequest,
)
from .providers.types import coerce_provider_outcome
from .job_execution import (
    fal_queue_is_terminal_error as _fal_queue_is_terminal_error,
    model_queue_handle as _model_queue_handle,
    set_model_queue_handle as _set_model_queue_handle,
)
from .job_operations import failure_metadata

router = APIRouter()

AUTO_COLOR_STRENGTH = 0.7
AUTO_COLOR_MASK_FEATHER = 0.003
_AUTO_COLOR_WORKFLOWS = ('纯效果图', '地板替换', '宠物友好', '参照模式', 'Omakase')


def supports_auto_color_match(workflow_mode: str) -> bool:
    """Only floor-oriented generation workflows are safe for automatic masking."""
    mode = workflow_mode or ''
    return any(name in mode for name in _AUTO_COLOR_WORKFLOWS)


def _set_auto_color_settings(job: JobRecord, key: str, **values) -> None:
    run = update_model_run(job, key)
    settings = dict(run.get('settings') or {})
    settings.update(values)
    update_model_run(job, key, settings=settings)


def _auto_color_match_generated(image: Image.Image, raw_path: str, reference_path: str):
    """Return a floor-masked color-corrected copy without changing scene luminance."""
    if image is None:
        return None, {}, 'API 原图为空'
    if not reference_path or not os.path.isfile(reference_path):
        return None, {}, '地板小样不存在，已保留 API 原图'

    stat = os.stat(raw_path)
    cache_key = f'auto-color:{os.path.realpath(raw_path)}:{stat.st_size}:{stat.st_mtime_ns}'
    _working, result = segment_floor(image, cache_key, auto_seed=True)
    if result.status != 'ok' or result.mask is None:
        detail = '；'.join(result.warnings or []) or '未识别到可靠地板区域'
        return None, {
            'mask_model': result.model,
            'mask_confidence': round(float(result.confidence or 0.0), 4),
            'mask_warnings': list(result.warnings or []),
        }, f'{detail}，已保留 API 原图'

    mask = Image.fromarray((result.mask.astype('uint8') * 255), mode='L')
    with Image.open(reference_path) as reference:
        reference.load()
        corrected = match_color_masked(
            image,
            reference,
            mask,
            strength=AUTO_COLOR_STRENGTH,
            adjustment_mode='auto',
            mask_feather=AUTO_COLOR_MASK_FEATHER,
        )
    metadata = {
        'operation': 'auto_color_match',
        'variant': 'auto_color_corrected',
        'scope': 'floor_mask',
        'strength': AUTO_COLOR_STRENGTH,
        'mask_feather': AUTO_COLOR_MASK_FEATHER,
        'mask_model': result.model,
        'mask_confidence': round(float(result.confidence or 0.0), 4),
        'mask_coverage': round(float(result.mask.mean()), 4),
        'mask_warnings': list(result.warnings or []),
        'preserve_luminance': True,
    }
    return corrected, metadata, ''


# ============================================================
# 生成 worker（移植自 webui._generate_one_model / _run_job，删去所有 UI 调用）
# ============================================================
async def _generate_one_model(job: JobRecord, model_id, prompt_text, stage_key, model_name, *,
                              api_key, pnp, ims, ar, rp, sref, bevel_ref, jpt, rid, should_cancel,
                              input_image_paths=None, cinematic_mode=False,
                              auto_color_match=False):
    """单模型生成：占本模型并发槽 → 调引擎 → 存盘 → 写记录 → 记用量。返回 (jpg路径或None, 错误串)。
    与 webui 版逐行一致，仅删去 _refresh_job_card(UI 刷新)——进度改由 SSE 读 job.{key}_stage。"""
    _t0 = time.time()

    def _on_stage(text):
        try:
            setattr(job, f'{stage_key}_stage', text)
            update_model_run(job, stage_key, stage=text, status='running')
        except Exception as ex:
            logger.debug(f"写入阶段状态失败 job={job.job_id}: {ex}")

    sem = state.model_semaphores[stage_key]
    img = err = provider = None
    outcome = None
    try:
        if sem.locked():
            _on_stage('排队中')   # 槽被别的任务占着 → 徽章显示「排队中」
        async with sem:
            _t0 = time.time()      # 计时从真正开跑算起，不含排队等待
            raw_outcome = await asyncio.to_thread(
                call_image_generate, api_key, model_id, prompt_text,
                pnp, ims, ar, rp, sref, _on_stage, should_cancel, bevel_ref,
                input_image_paths, cinematic_mode)
            outcome = coerce_provider_outcome(raw_outcome, model_id=model_id)
            img, err, provider = outcome
    except Exception as e:
        img, err, provider = None, str(e), get_image_provider()
        outcome = coerce_provider_outcome((img, err, provider), provider=provider, model_id=model_id)
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
        path = save_api_result_jpg(img, f'{model_name}_API原图', pnp)
        if not path:
            record_usage(job.workflow_mode, model_name, provider, True, job.operation)
            return None, '图片已生成，但保存到磁盘失败'
        add_model_candidate(job, stage_key, path)
        update_model_run(job, stage_key, base_path=path)
        _set_auto_color_settings(
            job,
            stage_key,
            auto_color_match_enabled=bool(auto_color_match),
            auto_color_status=('pending' if auto_color_match else 'disabled'),
            auto_color_error='',
        )
        raw_result_id = None
        try:
            raw_result_id = await asyncio.to_thread(
                api_write_to_record,
                img,
                f'{model_name} · API 原图',
                jpt,
                rid,
                path,
                {'variant': 'api_original'},
                None,
                f'API 原图 ({img.width}×{img.height})',
            )
        except Exception as ex:
            logger.warning(f"写记录失败 job={job.job_id} {model_name}: {ex}")

        if auto_color_match and not should_cancel():
            _on_stage('🎯 自动校色中…')
            try:
                corrected, color_meta, color_err = await asyncio.to_thread(
                    _auto_color_match_generated, img, path, pnp)
                corrected_saved = False
                if corrected is not None:
                    corrected_meta = dict(color_meta)
                    if raw_result_id:
                        corrected_meta['source_result_id'] = raw_result_id
                    corrected_path = await asyncio.to_thread(
                        save_api_result_png,
                        corrected,
                        f'{model_name}_自动校色',
                        path,
                        corrected_meta,
                    )
                    if corrected_path:
                        add_model_candidate(job, stage_key, corrected_path)
                        path = corrected_path
                        corrected_saved = True
                        _set_auto_color_settings(
                            job,
                            stage_key,
                            auto_color_status='done',
                            auto_color_error='',
                            auto_color_metadata=corrected_meta,
                        )
                        try:
                            await asyncio.to_thread(
                                api_write_to_record,
                                corrected,
                                f'{model_name} · 自动校色',
                                jpt,
                                rid,
                                corrected_path,
                                corrected_meta,
                                raw_result_id,
                                f'自动校色 ({corrected.width}×{corrected.height})',
                            )
                        except Exception as ex:
                            logger.warning(
                                f"自动校色图写记录失败 job={job.job_id} {model_name}: {ex}")
                    else:
                        color_err = '自动校色图保存失败，已保留 API 原图'
                if not corrected_saved:
                    message = color_err or '自动校色失败，已保留 API 原图'
                    _set_auto_color_settings(
                        job,
                        stage_key,
                        auto_color_status='failed',
                        auto_color_error=message,
                        auto_color_metadata=color_meta,
                    )
                    logger.warning(f"[自动校色] job={job.job_id} {model_name}: {message}")
            except Exception as ex:
                message = f'{ex}；已保留 API 原图'
                _set_auto_color_settings(
                    job,
                    stage_key,
                    auto_color_status='failed',
                    auto_color_error=message,
                )
                logger.warning(
                    f"[自动校色] 处理失败 job={job.job_id} {model_name}: {ex}",
                    exc_info=True,
                )
            finally:
                try:
                    setattr(job, f'{stage_key}_stage', '')
                    update_model_run(job, stage_key, stage='')
                except Exception:
                    pass
        elif auto_color_match:
            _set_auto_color_settings(
                job,
                stage_key,
                auto_color_status='cancelled',
                auto_color_error='用户已停止，保留 API 原图',
            )

    _err = err or ''
    # 出图=ok；没出图且非取消=fail（取消不计失败）。provider 用引擎返回的【实际】线路(自动转 Fal 能记对)。
    if img is not None or '取消' not in _err:
        try:
            usage_status = ('uncertain' if outcome and outcome.retry_safety == 'ambiguous'
                            else ('success' if img is not None else 'failed'))
            record_usage(job.workflow_mode, model_name, provider, usage_status, job.operation)
        except Exception as ex:
            logger.debug(f"记录用量失败 job={job.job_id}: {ex}")
    update_model_run(
        job, stage_key,
        status=('done' if path else 'failed'), error=_err, provider=provider or '',
        failure_code=(outcome.failure_code if outcome else ''),
        retry_safety=(outcome.retry_safety if outcome else 'fatal'),
        may_have_been_billed=bool(outcome and outcome.may_have_been_billed),
        attempts=list(outcome.attempts if outcome else []),
    )
    return path, _err


async def _generate_sd35_model(job: JobRecord, *, fal_key: str, positive: str, negative: str,
                               pnp: str, ims: str, ar: str, jpt: str, rid: str,
                               options: dict, should_cancel):
    """SD3.5 基础图 + AuraSR 交付图；超分失败保留基础图并标 partial。"""
    key = 'sd35'
    ensure_model_runs(job)
    sem = state.model_semaphores[key]
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
                state.JOBS.persist()  # AuraSR 前先确保基础图可在重启后恢复
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
    generation = state.JOBS.generation
    mf = job.model_filter
    targets = list(job.model_targets or [])
    run_b2 = 'b2' in targets
    run_pro = 'pro' in targets
    run_sd = 'sd35' in targets
    p = req.params
    cfg = load_config()
    api_key = (req.api_key or '').strip() or cfg.get('gemini_api_key', '').strip()
    fal_key = (cfg.get('fal_api_key') or '').strip()
    auto_color_active = bool(
        get_auto_color_match_enabled()
        and supports_auto_color_match(p.workflow_mode)
        and (run_b2 or run_pro)
    )

    if state.JOBS.is_cancelled(jid, generation):
        update_job(job, status='failed', error='已取消（用户停止）',
                   operation='generate', operation_status='cancelled', operation_error='已取消')
        state.JOBS.clear_cancelled(jid)
        state.JOBS.persist()
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

        # 可选电影真实感：Omakase 的场景候选已由文本模型规划，不重复计费；
        # 宠物/纯效果图/参照模式在正式生图前补一次轻量导演规划。失败只降级，不阻断付费生图。
        cinematic_active = bool(
            p.cinematic_enabled
            and supports_cinematic(p.workflow_mode)
            and (run_b2 or run_pro)
        )
        cinematic_direction = ''
        cinematic_meta = {}
        cinematic_error = ''
        if cinematic_active:
            if 'Omakase' in (p.workflow_mode or ''):
                cinematic_direction = CINEMATIC_FALLBACK_DIRECTION
                cinematic_meta = {
                    'source': 'omakase_scene',
                    'final_direction': cinematic_direction,
                }
            else:
                cinematic_direction, cinematic_meta, plan_err = await asyncio.to_thread(
                    plan_cinematic_scene,
                    p,
                    api_key=api_key,
                    model=get_omakase_gemini_model(),
                    style_analysis_text=style_text,
                )
                if plan_err:
                    cinematic_error = plan_err
                    cinematic_direction = CINEMATIC_FALLBACK_DIRECTION
                    cinematic_meta = {
                        'source': 'local_fallback',
                        'final_direction': cinematic_direction,
                    }
                    logger.warning(
                        f"[电影真实感] 规划失败，使用本地降级 job={jid}: {plan_err}"
                    )
                else:
                    cinematic_meta = {'source': 'gemini', **cinematic_meta}

        # 组装提示词参数 → save_task_files_html（prep 串行：同张小样并发任务共享输出路径）
        tp = TaskParams(
            image_path=req.image_path,
            style_analysis_text=style_text,
            cinematic_plan_text=cinematic_direction,
            **p.model_dump(),
        )
        async with state.task_prep_lock:
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
                             jpt=jpt, rid=rid, model_filter=mf, model_targets=targets,
                             cinematic_mode=cinematic_active,
                             auto_color_match=auto_color_active)
        update_job(job, json_path=jpt, record_id=rid, png_path=pnp)

        # gen_context 快照：完整入参落记录 JSON，供「复用参数」「前后对比」。绝不含 api_key。
        try:
            await asyncio.to_thread(attach_generation_context, jpt, rid, dict(
                image_path=req.image_path, room_path=req.room_path or '',
                ref_path=req.ref_path or '', model_filter=mf, model_targets=targets,
                sd_options=req.sd_options.model_dump() if run_sd else None,
                cinematic_plan=(cinematic_meta if cinematic_active else None),
                cinematic_error=cinematic_error,
                params=p.model_dump()))
        except Exception as ex:
            logger.warning(f"[API任务] gen_context 写入失败 job={jid}: {ex}")

        if state.JOBS.is_cancelled(jid, generation):
            update_job(job, status='failed', error='已取消（用户停止）',
                       operation_status='cancelled', operation_error='已取消')
            return

        should_cancel = lambda: state.JOBS.is_cancelled(jid, generation)

        def _gen_one(model_id, prompt_text, stage_key, model_name):
            return _generate_one_model(job, model_id, prompt_text, stage_key, model_name,
                                       api_key=api_key, pnp=pnp, ims=ims, ar=ar, rp=rp, sref=sref,
                                       bevel_ref=bevel_ref, jpt=jpt, rid=rid,
                                       should_cancel=should_cancel,
                                       cinematic_mode=cinematic_active,
                                       auto_color_match=auto_color_active)

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

        if state.JOBS.is_cancelled(jid, generation):
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
        state.JOBS.clear_cancelled(jid)
        state.JOBS.persist()


async def _run_free_job_bg(job: JobRecord, req: 'FreeJobSubmitRequest'):
    """自由创作编排：不识色、不分析风格、不编译提示词，只按 Slot 顺序透传。"""
    jid = job.job_id
    generation = state.JOBS.generation
    targets = list(job.model_targets)
    cfg = load_config()
    api_key = (req.api_key or '').strip() or cfg.get('gemini_api_key', '').strip()
    b2j = proj = None
    try:
        if state.JOBS.is_cancelled(jid, generation):
            update_job(job, status='failed', error='已取消（用户停止）',
                       operation='generate', operation_status='cancelled', operation_error='已取消')
            return
        update_job(job, status='running', started_at=time.time(), operation='generate',
                   operation_status='running', operation_error='')

        primary = req.image_paths[0]
        jpt, rid = await asyncio.to_thread(
            create_free_generation_record, primary, req.prompt, req.image_paths,
            targets, req.aspect_ratio, req.resolution)
        job.retry_ctx = dict(
            api_key=api_key, pnp=primary, cpt=req.prompt, cpt_pro=req.prompt,
            ims=req.resolution, ar=req.aspect_ratio, rp=None, sref=None, bevel_ref=None,
            input_image_paths=list(req.image_paths), jpt=jpt, rid=rid,
            model_filter=job.model_filter, model_targets=targets,
        )
        update_job(job, json_path=jpt, record_id=rid, png_path='')

        if state.JOBS.is_cancelled(jid, generation):
            update_job(job, status='failed', error='已取消（用户停止）',
                       operation_status='cancelled', operation_error='已取消')
            return
        should_cancel = lambda: state.JOBS.is_cancelled(jid, generation)

        def gen_one(model_id, stage_key, model_name):
            return _generate_one_model(
                job, model_id, req.prompt, stage_key, model_name,
                api_key=api_key, pnp=primary, ims=req.resolution, ar=req.aspect_ratio,
                rp=None, sref=None, bevel_ref=None, jpt=jpt, rid=rid,
                should_cancel=should_cancel, input_image_paths=list(req.image_paths))

        tasks = []
        if 'b2' in targets:
            tasks.append(('b2', gen_one(GEMINI_MODEL_MAP['Nano Banana 2'], 'b2', 'Nano Banana 2')))
        if 'pro' in targets:
            tasks.append(('pro', gen_one(GEMINI_MODEL_MAP['Nano Banana Pro'], 'pro', 'Nano Banana Pro')))
        results = await asyncio.gather(*[c for _, c in tasks], return_exceptions=True)
        errors = []
        for (key, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                path, err = None, str(result)
                update_model_run(job, key, status='failed', error=err, stage='')
            else:
                path, err = result
            if key == 'b2':
                b2j = path
            elif key == 'pro':
                proj = path
            if err:
                errors.append(f'{key.upper()}: {err}')

        final = compute_runs_final_status(job)
        err_msg = ' '.join(errors).strip()
        cancelled = state.JOBS.is_cancelled(jid, generation)
        update_job(
            job, status=final, b2_path=b2j, pro_path=proj,
            error=('已取消，但已出图已保留（已付费）' if cancelled and final in ('done', 'partial') else err_msg),
            operation_status=('cancelled' if cancelled else ('done' if final in ('done', 'partial') else 'failed')),
            operation_error=('已取消' if cancelled else err_msg),
        )
        logger.info(f"[自由创作] finished job={jid}, status={final}, slots={len(req.image_paths)}")
    except Exception as ex:
        logger.exception(f"[自由创作] unhandled job={jid}")
        update_job(job, status=compute_runs_final_status(job), b2_path=b2j, pro_path=proj,
                   error=str(ex), operation_status='failed', operation_error=str(ex))
    finally:
        state.JOBS.clear_cancelled(jid)
        state.JOBS.persist()


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
        state.JOBS.persist()
        return
    state.JOBS.clear_cancelled(job.job_id)   # 清掉残留取消标记，否则 should_cancel 恒 True
    generation = state.JOBS.generation
    update_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='',
               operation='retry', operation_status='running', operation_error='')
    try:
        should_cancel = lambda: state.JOBS.is_cancelled(job.job_id, generation)

        def _retry_one(model_id, prompt_text, stage_key, model_name):
            return _generate_one_model(job, model_id, prompt_text, stage_key, model_name,
                                       api_key=api_key, pnp=ctx['pnp'], ims=ctx['ims'], ar=ctx['ar'],
                                       rp=ctx['rp'], sref=ctx['sref'], bevel_ref=ctx.get('bevel_ref'),
                                       jpt=ctx['jpt'], rid=ctx['rid'], should_cancel=should_cancel,
                                       input_image_paths=ctx.get('input_image_paths'),
                                       cinematic_mode=bool(ctx.get('cinematic_mode', False)),
                                       auto_color_match=bool(ctx.get('auto_color_match', False)))

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
        cancelled = state.JOBS.is_cancelled(job.job_id, generation)
        update_job(job, status=final, error=op_error,
                   operation_status=('cancelled' if cancelled else ('failed' if op_error else 'done')),
                   operation_error=('已取消' if cancelled else op_error))
    except Exception as e:
        logger.exception(f"[API重试] unhandled job={job.job_id}")
        update_job(job, status=compute_runs_final_status(job), error=str(e),
                   operation_status='failed', operation_error=str(e))
    finally:
        state.JOBS.clear_cancelled(job.job_id)
        state.JOBS.persist()


async def _retry_sd_upscale_bg(job: JobRecord):
    run = (job.model_runs or {}).get('sd35') or {}
    base_path = run.get('base_path') or ''
    fal_key = (load_config().get('fal_api_key') or '').strip()
    generation = state.JOBS.generation
    try:
        update_model_run(job, 'sd35', status='running', stage='🔎 4K 超分中…', error='')
        base = Image.open(base_path); base.load()
        async with state.model_semaphores['sd35']:
            out, err = await asyncio.to_thread(
                call_fal_aura_upscale, fal_key, base,
                on_stage=lambda t: update_model_run(job, 'sd35', stage=t),
                should_cancel=lambda: state.JOBS.is_cancelled(job.job_id, generation),
                queue_handle=_model_queue_handle(job, 'sd35', 'upscale_queue'),
                on_queue_submitted=lambda h: _set_model_queue_handle(job, 'sd35', 'upscale_queue', h),
            )
        if out is None:
            if _fal_queue_is_terminal_error(err) or '取消' in str(err or ''):
                _set_model_queue_handle(job, 'sd35', 'upscale_queue', None)
            cancelled = '取消' in str(err or '') or state.JOBS.is_cancelled(job.job_id, generation)
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
        state.JOBS.clear_cancelled(job.job_id)
        state.JOBS.persist()


async def _edit_bg(job: JobRecord, *, api_key, instruction, model_id, model_label,
                   preserve, image_size, color_match=False):
    """对 job 现有 Pro(或 B2)成图做一次图生图编辑/磨缝（移植 webui._polish_pro 去 UI）。
    color_match=True 时把结果色彩对齐回原图（磨缝消偏色）；自定义编辑保留模型输出色彩。"""
    jid = job.job_id
    generation = state.JOBS.generation
    src_path = job.pro_path or job.b2_path
    base_status = compute_final_status(job.model_filter, job.b2_path, job.pro_path)
    if not src_path or not os.path.exists(str(src_path)):
        update_job(job, status=base_status, operation_status='failed',
                   operation_error='没有可编辑的成图', pro_polishing=False, pro_stage='')
        state.JOBS.clear_cancelled(jid)
        state.JOBS.persist()
        return
    update_job(
        job, started_at=time.time(), pro_polishing=True, pro_stage='', operation_status='running',
        operation_failure_code='', operation_retry_safety='safe',
        operation_may_have_been_billed=False, operation_attempts=[],
    )

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
        if state.model_semaphores['pro'].locked():
            _on_stage('排队中')
        async with state.model_semaphores['pro']:
            out, err = await asyncio.to_thread(
                call_gemini_edit, api_key, model_id, instruction, b64,
                image_size, ar, preserve, _on_stage, lambda: state.JOBS.is_cancelled(jid, generation))
        if out is None:
            safety = str(getattr(err, 'retry_safety', 'fatal'))
            record_usage(job.workflow_mode, model_label, 'google',
                         'uncertain' if safety == 'ambiguous' else 'failed', job.operation)
            update_job(
                job, status=base_status, operation_status='failed', operation_error=f'编辑失败：{err}',
                **failure_metadata(err),
            )
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
        state.JOBS.clear_cancelled(jid)
        state.JOBS.persist()



# ── 任务：提交 / 列表 / 详情 / SSE / 取消 / 重试 ──
@router.post('/api/jobs')
async def create_job(req: JobSubmitRequest):
    if '自由创作' in (req.params.workflow_mode or ''):
        raise HTTPException(422, '自由创作请使用 /api/jobs/free')
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
    req.image_path = require_upload_image_path(req.image_path, '地板图', required=True)
    if req.params.film_path:
        req.params.film_path = require_upload_image_path(req.params.film_path, '原厂彩膜') or ''
    req.room_path = require_upload_image_path(req.room_path, '房间图')
    req.ref_path = require_upload_image_path(req.ref_path, '参照图')
    panel_require_second_image(req)
    labels = {'b2': 'B2', 'pro': 'Pro', 'sd35': 'SD3.5'}
    label = '[' + '+'.join(labels[k] for k in targets) + ']'
    room_disp = req.params.cn_room_type if req.params.cn_mode else req.params.room_type
    dname = f"{os.path.splitext(os.path.basename(req.image_path))[0]} · {room_disp} {label}"
    legacy_filter = legacy_filter_from_targets(targets)
    job = new_job(dname, time.strftime('%H:%M:%S'), legacy_filter)
    job.model_targets = targets
    ensure_model_runs(job)
    job.workflow_mode = req.params.workflow_mode
    state.JOBS.add(job.job_id, job)   # 登记并顺手收口最旧的终态卡，防长会话内存缓涨
    state.spawn(_run_job_bg(job, req))   # 立即返回，不为整个 4K 生成挂起 HTTP
    return job_view(job)


@router.post('/api/jobs/free')
async def create_free_job(req: FreeJobSubmitRequest):
    cfg = load_config()
    targets = list(req.model_targets)
    if not targets or len(targets) != len(set(targets)):
        raise HTTPException(422, 'model_targets 至少选择一个且不可重复')
    if not ((req.api_key or '').strip() or cfg.get('gemini_api_key', '').strip()):
        raise HTTPException(400, '所选 B2/Pro 缺少 Gemini API Key')
    req.image_paths = [
        require_upload_image_path(path, f'Slot {index}', required=True)
        for index, path in enumerate(req.image_paths, start=1)
    ]
    if len(req.image_paths) != len(set(req.image_paths)):
        raise HTTPException(422, '自由创作的图片槽不可重复')
    labels = {'b2': 'B2', 'pro': 'Pro'}
    label = '[' + '+'.join(labels[key] for key in targets) + ']'
    primary_name = os.path.splitext(os.path.basename(req.image_paths[0]))[0]
    job = new_job(f'{primary_name} · 自由创作 {label}', time.strftime('%H:%M:%S'),
                  legacy_filter_from_targets(targets))
    job.model_targets = targets
    ensure_model_runs(job)
    job.workflow_mode = '自由创作 (自定义提示词/多图)'
    state.JOBS.add(job.job_id, job)
    state.spawn(_run_free_job_bg(job, req))
    return job_view(job)


@router.get('/api/jobs')
def list_jobs(status: str = '', limit: int = 50):
    jobs = state.JOBS.snapshot()
    if status:
        jobs = [j for j in jobs if j.status == status]
    return [job_view(j) for j in jobs[:max(1, limit)]]


@router.get('/api/jobs/{jid}')
def get_job(jid: str):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    return job_view(job)


@router.get('/api/jobs/{jid}/stream')
async def stream_job(jid: str, request: Request):
    """SSE：每秒推一次任务快照；进入终态后再推一条 done 事件并关闭。"""
    async def gen():
        while True:
            if await request.is_disconnected():
                break
            job = state.JOBS.get(jid)
            if job is None:
                yield f"event: error\ndata: {json.dumps({'error': 'job not found'})}\n\n"
                break
            data = json.dumps(job_view(job), ensure_ascii=False)
            yield f"data: {data}\n\n"
            if (job.status in ('done', 'partial', 'failed')
                    and job.operation_status != 'running' and not job.pro_polishing):
                yield f"event: done\ndata: {data}\n\n"
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.status not in ('queued', 'running') and job.operation_status != 'running':
        raise HTTPException(409, '任务当前不在运行')
    state.JOBS.request_cancel(jid)   # 终态由 worker finally 据「是否已出图」判定（已计费的图保留）
    if not job.error:
        update_job(job, error='已取消（用户停止）')
    return {'cancelled': True}


@router.post('/api/jobs/cancel-all')
def cancel_all():
    state.JOBS.bump_generation()
    n = 0
    for job in state.JOBS.snapshot():
        if job.status in ('queued', 'running') or job.operation_status == 'running':
            state.JOBS.request_cancel(job.job_id)
            n += 1
    return {'stopped': n}


@router.post('/api/jobs/clear-completed')
def clear_completed():
    """清掉「完成」状态的任务卡（保留 部分/失败 供逐卡删/重试）。改注册表后落盘，
    否则前端 2.5s 轮询或重启会把它们读回来。只清队列列表，不动出图文件与「记录」。"""
    removed = 0
    with state.JOBS.locked() as entries:
        victims = [jid for jid, job in entries.items()
                   if job.status == 'done' and job.operation_status != 'running' and not job.pro_polishing]
        for jid in victims:
            del entries[jid]
            state.JOBS.clear_cancelled(jid)
            removed += 1
    state.JOBS.persist()  # 必须在 locked() 外：persist 内部会再取同一把锁（不可重入）
    return {'cleared': removed}


@router.post('/api/jobs/{jid}/delete')
def delete_job(jid: str):
    """从队列移除单条任务卡（任意状态；运行中的建议先停止）。仅移除列表项，不动出图/记录。"""
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.status in ('queued', 'running') or job.operation_status == 'running' or job.pro_polishing:
        raise HTTPException(409, '任务仍在运行，请先停止并等待结束后再清除')
    removed = 1 if state.JOBS.pop(jid) is not None else 0   # pop 连带清取消标记
    state.JOBS.persist()  # 锁外
    return {'deleted': removed}


@router.post('/api/jobs/{jid}/retry')
async def retry_job(jid: str, req: RetryJobRequest | None = None):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.retry_ctx:
        raise HTTPException(400, '该任务缺少重试信息（可能重启后丢失），请重新提交')
    if job.status not in ('failed', 'partial'):
        return job_view(job)
    ensure_model_runs(job)
    ambiguous = any(
        str((job.model_runs.get(key) or {}).get('retry_safety') or '') == 'ambiguous'
        for key in job.model_targets
    )
    if ambiguous and not bool(req and req.confirm_possible_duplicate_charge):
        raise HTTPException(409, {
            'code': 'possible_duplicate_charge_confirmation_required',
            'message': '上一次请求结果不确定，可能已经计费；确认后才能再次生成',
        })
    if ambiguous:
        for key in job.model_targets:
            run = job.model_runs.get(key) or {}
            if run.get('retry_safety') == 'ambiguous':
                settings = dict(run.get('settings') or {})
                settings['duplicate_charge_confirmed_at'] = time.time()
                run['settings'] = settings
    # 回执前预置 active 状态（镜像 _retry_bg 开场），前端据此即刻开 SSE；后台会再设同值，幂等
    update_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='',
               operation='retry', operation_status='running', operation_error='')
    state.spawn(_retry_bg(job))
    return job_view(job)


@router.post('/api/jobs/{jid}/sd-upscale')
async def retry_sd_upscale(jid: str):
    job = state.JOBS.get(jid)
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
    state.spawn(_retry_sd_upscale_bg(job))
    return job_view(job)


@router.get('/api/jobs/{jid}/result')
def job_result(jid: str, model: str = 'pro', idx: int = -1):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    ensure_model_runs(job)
    run = job.model_runs.get(model)
    if not run or not (run.get('paths') or []):
        raise HTTPException(404, 'no result for this model')
    requested = int(run.get('index') or 0) if idx < 0 else idx
    i, total, path = nav_model_candidate(job, model, requested)
    candidate_meta = list(run.get('candidate_meta') or [])
    metadata = (candidate_meta[i] if i < len(candidate_meta)
                and isinstance(candidate_meta[i], dict) else {})
    return {'model': model, 'idx': i, 'total': total,
            'url': to_url(path), 'thumb': result_thumb_url(path),
            'metadata': dict(metadata)}


# ── 编辑 / 磨缝（对 job 现有成图）──
@router.post('/api/jobs/{jid}/polish')
async def polish_job(jid: str):
    job = state.JOBS.get(jid)
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
    state.spawn(_edit_bg(
        job, api_key=api_key, instruction=FLOOR_DESEAM_INSTRUCTION,
        model_id=GEMINI_MODEL_MAP['Nano Banana Pro'], model_label='Nano Banana Pro_磨缝',
        preserve=False, image_size='4K', color_match=True))
    return job_view(job)


@router.post('/api/jobs/{jid}/edit')
async def edit_job(jid: str, req: EditRequest):
    job = state.JOBS.get(jid)
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
    state.spawn(_edit_bg(
        job, api_key=api_key, instruction=req.instruction, model_id=model_id,
        model_label=f'{req.model_choice} 二改', preserve=req.preserve_floor_geometry,
        image_size=req.image_size, color_match=req.color_match))
    return job_view(job)

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
    generation = state.JOBS.generation
    should_cancel = lambda: state.JOBS.is_cancelled(job.job_id, generation)

    def gen_one(model_id, prompt_text, sk, name):
        return _generate_one_model(job, model_id, prompt_text, sk, name, api_key=api_key,
                                   pnp=ctx['pnp'], ims=ctx['ims'], ar=ctx['ar'], rp=ctx['rp'],
                                   sref=ctx['sref'], bevel_ref=ctx.get('bevel_ref'),
                                   jpt=ctx['jpt'], rid=ctx['rid'], should_cancel=should_cancel,
                                   input_image_paths=ctx.get('input_image_paths'),
                                   cinematic_mode=bool(ctx.get('cinematic_mode', False)),
                                   auto_color_match=bool(ctx.get('auto_color_match', False)))

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
    """一键多抽 ×n：串行重抽 n 次（每次 append 候选）；用户停止(state.JOBS.request_cancel)即跑完当前后停。
    保留最后一轮的每模型错误串写进 job.error（用户主动取消不算失败原因，过滤掉）。"""
    if not job.retry_ctx:
        update_job(job, error='缺少重抽上下文')
        return
    state.JOBS.clear_cancelled(job.job_id)
    # 整批期间保持 running（不在迭代间置终态）——否则 SSE 见终态即关流，多抽第二张起就断了。
    update_job(job, status='running', started_at=time.time(), error='',
               operation='regen', operation_status='running', operation_error='')
    err = ''
    last_err = ''
    try:
        for _i in range(max(1, n)):
            if state.JOBS.is_cancelled(job.job_id, state.JOBS.generation):
                break
            round_err = ((await _regen_once(job)) or '').strip()
            if round_err and '取消' not in round_err:
                last_err = round_err
            state.JOBS.persist()
    except Exception as e:
        logger.exception(f"[API多抽] 异常 job={job.job_id}")
        err = str(e)
    finally:
        final = compute_runs_final_status(job)
        op_error = err or last_err
        cancelled = state.JOBS.is_cancelled(job.job_id, state.JOBS.generation)
        update_job(job, status=final, error=op_error,
                   operation_status=('cancelled' if cancelled else ('failed' if op_error else 'done')),
                   operation_error=op_error or ('已取消' if cancelled else ''))
        state.JOBS.clear_cancelled(job.job_id)
        state.JOBS.persist()


async def _record_edit_bg(job: JobRecord, *, src_pil, api_key, instruction, model_id, model_label,
                          image_size, preserve, json_path, record_id, source_ref, color_match=False):
    """记录内二改：对已存记录的某张结果做图生图编辑，结果 append 回该记录（移植 webui._do_edit 去 UI）。
    color_match=True 时把结果色彩对齐回原图（镜像 _edit_bg 的防偏色分支）。"""
    jid = job.job_id
    generation = state.JOBS.generation
    update_job(
        job, status='running', started_at=time.time(), pro_polishing=True,
        operation_failure_code='', operation_retry_safety='safe',
        operation_may_have_been_billed=False, operation_attempts=[],
    )

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
        if state.model_semaphores['pro'].locked():
            _on_stage('排队中')
        async with state.model_semaphores['pro']:
            out, err = await asyncio.to_thread(
                call_gemini_edit, api_key, model_id, instruction, b64,
                image_size, ar, preserve, _on_stage, lambda: state.JOBS.is_cancelled(jid, generation))
        if out is None:
            safety = str(getattr(err, 'retry_safety', 'fatal'))
            record_usage(job.workflow_mode, model_label, 'google',
                         'uncertain' if safety == 'ambiguous' else 'failed', 'record_edit')
            update_job(
                job, status='failed', error=f'二改失败：{err}', operation_status='failed',
                operation_error=str(err or ''),
                **failure_metadata(err),
            )
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
        state.JOBS.clear_cancelled(jid)
        state.JOBS.persist()



# ── 重抽/多抽 ──
@router.post('/api/jobs/{jid}/regen')
async def regen_job(jid: str, n: int = Query(default=1, ge=1, le=6)):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.retry_ctx:
        raise HTTPException(400, '该任务缺少重抽上下文(可能重启后丢失)，请重新提交')
    if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
        raise HTTPException(409, '任务进行中，请稍后再抽')
    # 回执前预置 active 状态（镜像 _regen_bg 开场），前端据此即刻开 SSE
    update_job(job, status='running', started_at=time.time(), error='',
               operation='regen', operation_status='running', operation_error='')
    state.spawn(_regen_bg(job, n))
    return job_view(job)


# ── 记录内二改 ──
@router.post('/api/records/edit')
async def record_edit(req: RecordEditRequest):
    api_key = (req.api_key or '').strip() or load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    json_path = require_record_json_path(req.json_path)
    recs = load_records_file(json_path)
    rec = next((r for r in recs if r.get('id') == req.record_id), None)
    if not rec:
        raise HTTPException(404, '未找到记录')
    if rec.get('immutable_audit'):
        raise HTTPException(409, {
            'code': 'immutable_audit_record',
            'message': '全景门禁审计记录不能从通用历史页发起二改',
        })
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
    state.JOBS.add(job.job_id, job)   # 顺手收口最旧的终态卡（新建的二改 job 是 queued/in-flight，不会被删）
    model_id = GEMINI_MODEL_MAP.get(req.model_choice, GEMINI_MODEL_MAP['Nano Banana Pro'])
    state.spawn(_record_edit_bg(
        job, src_pil=src_pil, api_key=api_key, instruction=req.instruction, model_id=model_id,
        model_label=f'{req.model_choice} 二改', image_size=req.image_size,
        preserve=req.preserve_floor_geometry, json_path=json_path,
        record_id=req.record_id, source_ref=req.result_id, color_match=req.color_match))
    return job_view(job)
