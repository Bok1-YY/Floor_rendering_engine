# ==========================================
# 地板 AI 生图引擎 — 无头 HTTP/SSE 服务层 (FastAPI)
# STEP 1 of「迁出 NiceGUI → 真前端」迁移：把引擎能力暴露为 REST/SSE，供 Next.js 前端对接。
# 设计原则：引擎模块(api/prompts/records/models/config/...)零改动，本文件只做编排 + 协议适配。
# 关键：本文件【不依赖 nicegui】。webui.py 仍可独立跑(7869)，本服务跑 7870，互不干扰。
#
# 运行(在 test/ 目录下，把本包当包导入)：
#   python -m Floor_engine_server.server_api
#   或  uvicorn Floor_engine_server.server_api:app --host 127.0.0.1 --port 7870 --workers 1
# 必须单 worker：_job_history / 信号量是【进程内】状态，多 worker 不共享。
# ==========================================
"""Headless FastAPI layer over the floor_engine package (no NiceGUI)."""

import os
import io
import time
import json
import base64
import asyncio
import hashlib
import threading
from typing import Optional, List, Literal
from contextlib import asynccontextmanager

from PIL import Image
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── 引擎依赖（全部包内相对导入；这些模块均与前端无关，可安全 import 而不拉进 NiceGUI）──
from .config import (
    MAIN_OUTPUT_DIR, UPLOAD_DIR, THUMB_DIR, logger,
    _load_config, GEMINI_MODEL_MAP, FAL_MODEL_MAP,
    get_image_provider, save_api_key, save_provider_settings,
    get_speed_profile, save_speed_profile, get_auto_failover, save_auto_failover,
    get_tls_verify, get_proxy, get_speed_profile_params,
    safe_upload_path, extract_clean_prompt, get_bevel_ref_image,
)
from .api import (
    call_image_generate, call_gemini_edit, test_connection, analyze_style_image,
    FLOOR_DESEAM_INSTRUCTION, _infer_aspect_ratio_from_b64, _match_color_to_reference,
)
from .prompts import save_task_files_html
from .records import (
    persist_jobs, load_persisted_jobs, record_usage,
    _save_api_result_jpg, _api_write_to_record,
    _safe_output_path, scan_json_files, _load_records, get_record_labels,
    reveal_prompt_fn, _list_recent_floor_swatches,
)
from .models import (
    JobRecord, new_job, update_job, compute_final_status,
    job_time_text, running_model_status_text, add_candidate, ensure_candidate_lists,
    TaskParams, task_params_to_kwargs,
)
from .failure_kb import classify_failure
from .recipes import recommend_recipes, FLOOR_RECIPES


# ============================================================
# 模块级编排状态（移植自 webui.py：注册表 + 按模型并发槽 + 取消标志）
# ============================================================
_job_history: List[JobRecord] = []
_job_lock = threading.Lock()
# 每个模型最多 max_concurrent_per_model(默认 1) 个进行中任务；B2 / Pro 各一把信号量。
# 在 lifespan 启动钩子里、于本服务事件循环上惰性创建（绝不在 import 期建，否则绑错 loop）。
_b2_semaphore: Optional[asyncio.Semaphore] = None
_pro_semaphore: Optional[asyncio.Semaphore] = None
# prep 串行锁：save_task_files_html 按小样路径派生 png/json 输出路径，同图并发首处理会抢写同一 png。
_task_prep_lock: Optional[asyncio.Lock] = None
# 取消：单任务用集合(stop this one)；全局用单调计数器(stop all：in-flight 任务捕获的旧值 < 新值即自行退出)。
_cancel_jobs: set = set()
_cancel_generation = [0]


def _is_cancelled(job_id: str, generation: Optional[int] = None) -> bool:
    """与 webui 同义：本任务被单独停 → True；或全局计数器已超过任务捕获的代次 → True。"""
    return job_id in _cancel_jobs or (generation is not None and generation < _cancel_generation[0])


def _persist_jobs() -> None:
    with _job_lock:
        jobs = list(_job_history)
    persist_jobs(jobs)   # records.persist_jobs 内部会剥掉 retry_ctx 里的 api_key，不存明文


def _get_job(jid: str) -> Optional[JobRecord]:
    with _job_lock:
        for j in _job_history:
            if j.job_id == jid:
                return j
    return None


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
    ensure_candidate_lists(job)   # 向后兼容 + 同步 *_path = *_paths[*_idx]
    return {
        'job_id': job.job_id,
        'display_name': job.display_name,
        'ts': job.ts,
        'status': job.status,
        'model_filter': job.model_filter,
        'workflow_mode': job.workflow_mode,
        'error': job.error,
        'error_kb': classify_failure(job.error) if (job.error and '取消' not in job.error) else None,
        'b2_stage': job.b2_stage,
        'pro_stage': job.pro_stage,
        'b2_secs': job.b2_secs,
        'pro_secs': job.pro_secs,
        'time_text': job_time_text(job),
        'model_status': running_model_status_text(job),
        'pro_polishing': job.pro_polishing,
        'record_id': job.record_id,
        'json_path': job.json_path,
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
        except Exception as ex:
            logger.debug(f"写入阶段状态失败 job={job.job_id}: {ex}")

    sem = _b2_semaphore if stage_key == 'b2' else _pro_semaphore
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
        except Exception:
            pass
    setattr(job, f'{stage_key}_secs', round(time.time() - _t0, 1))

    path = None
    if img is not None:
        # 图已生成 = 已计费 → 即便随后判定取消也先存盘，避免白花钱（与 webui 同序）
        path = _save_api_result_jpg(img, model_name, pnp)
        add_candidate(job, stage_key, path)
        try:
            await asyncio.to_thread(_api_write_to_record, img, model_name, jpt, rid, path)
        except Exception as ex:
            logger.warning(f"写记录失败 job={job.job_id} {model_name}: {ex}")

    _err = err or ''
    # 出图=ok；没出图且非取消=fail（取消不计失败）。provider 用引擎返回的【实际】线路(自动转 Fal 能记对)。
    if img is not None or '取消' not in _err:
        try:
            record_usage(job.workflow_mode, model_name, provider, img is not None)
        except Exception as ex:
            logger.debug(f"记录用量失败 job={job.job_id}: {ex}")
    return path, _err


async def _run_job_bg(job: JobRecord, req: 'JobSubmitRequest'):
    """主生图编排（移植自 webui._run_job 去 UI）：prep 串行 → 接缝/倒角规则 → 按模型并发生成 → 终态判定。"""
    jid = job.job_id
    generation = _cancel_generation[0]
    mf = job.model_filter
    run_b2 = mf in ('b2', 'both')
    run_pro = mf in ('pro', 'both')
    p = req.params
    api_key = (req.api_key or '').strip() or _load_config().get('gemini_api_key', '').strip()

    if _is_cancelled(jid, generation):
        update_job(job, status='failed', error='已取消（用户停止）')
        return

    update_job(job, status='running', started_at=time.time())
    b2j = proj = None
    try:
        is_ref_mode = '参照模式' in (p.workflow_mode or '')

        # 参照模式 Step-1：提取风格描述（失败即中止，不发起计费生图）
        style_text = ''
        if is_ref_mode and req.ref_path:
            style_text, sa_err = await asyncio.to_thread(analyze_style_image, api_key, req.ref_path)
            if sa_err or not style_text:
                update_job(job, status='failed',
                           error=f'风格分析失败，已中止（未发起生图）: {sa_err or "返回为空"}')
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
        # B2 也用 Pro 终极指令（实测无缝效果最佳）；唯「圆弧倒角·直拼」例外，B2 保留自己的软细缝词。
        _seam_v = p.seam_type or ''
        _size_v = p.floor_size or ''
        _is_straight_bevel = ('圆弧倒角' in _seam_v and '无缝' not in _seam_v
                              and '人字拼' not in _size_v and '正方形拼' not in _size_v)
        if not _is_straight_bevel:
            cpt = cpt_pro
        ims = (p.resolution or '4K').split(' ')[0]
        # 参照模式：参照图当风格参照(sref)，不发房间替换图(rp)
        rp = None if is_ref_mode else (req.room_path or None)
        sref = (req.ref_path or None) if is_ref_mode else None
        # 圆弧倒角(任意拼法)：自动附内置倒角参考图（只供模型抄板边圆弧形状）；B2/Pro 同带。
        _is_pressed_bevel = ('圆弧倒角' in _seam_v and '无缝' not in _seam_v)
        bevel_ref = get_bevel_ref_image() if _is_pressed_bevel else None

        # 存生成上下文，供「重试」只重跑未成图的模型（持久化时 api_key 会被剥除）
        job.retry_ctx = dict(api_key=api_key, pnp=pnp, cpt=cpt, cpt_pro=cpt_pro,
                             ims=ims, ar=ar, rp=rp, sref=sref, bevel_ref=bevel_ref,
                             jpt=jpt, rid=rid, model_filter=mf)
        update_job(job, json_path=jpt, record_id=rid, png_path=pnp)

        if _is_cancelled(jid, generation):
            update_job(job, status='failed', error='已取消（用户停止）')
            return

        should_cancel = lambda: _is_cancelled(jid, generation)

        def _gen_one(model_id, prompt_text, stage_key, model_name):
            return _generate_one_model(job, model_id, prompt_text, stage_key, model_name,
                                       api_key=api_key, pnp=pnp, ims=ims, ar=ar, rp=rp, sref=sref,
                                       bevel_ref=bevel_ref, jpt=jpt, rid=rid, should_cancel=should_cancel)

        tasks = []
        if run_b2:
            tasks.append(('b2', _gen_one(GEMINI_MODEL_MAP['Nano Banana 2'], cpt, 'b2', 'Nano Banana 2')))
        if run_pro:
            tasks.append(('pro', _gen_one(GEMINI_MODEL_MAP['Nano Banana Pro'], cpt_pro, 'pro', 'Nano Banana Pro')))
        results = await asyncio.gather(*[c for _, c in tasks], return_exceptions=True)

        b2_err = pro_err = ''
        for (k, _), res in zip(tasks, results):
            if isinstance(res, Exception):
                if k == 'b2':
                    b2_err = str(res)
                else:
                    pro_err = str(res)
            else:
                _path, _e = res
                if k == 'b2':
                    b2j, b2_err = _path, _e
                else:
                    proj, pro_err = _path, _e

        if _is_cancelled(jid, generation):
            # 取消后：已返回的图(已计费)已在 _gen_one 里存盘，这里据实标注，不丢弃
            if b2j or proj:
                update_job(job, status=('done' if (b2j and proj) else 'partial'),
                           b2_path=b2j, pro_path=proj, error='已取消，但已出图已保留（已付费）')
            else:
                update_job(job, status='failed', error='已取消（无结果）')
            return

        err_msg = ('B2: ' + b2_err if b2_err else '') + (' Pro: ' + pro_err if pro_err else '')
        final = compute_final_status(mf, b2j, proj)
        update_job(job, status=final, b2_path=b2j, pro_path=proj, error=err_msg.strip())
        logger.info(f"[API任务] finished job={jid}, status={final}, b2={bool(b2j)}, pro={bool(proj)}")
    except Exception as e:
        logger.exception(f"[API任务] unhandled job={jid}")
        update_job(job, status='failed', b2_path=b2j, pro_path=proj, error=str(e))
    finally:
        _cancel_jobs.discard(jid)
        _persist_jobs()


async def _retry_bg(job: JobRecord):
    """重试：用 retry_ctx 只重跑还没出图的模型（移植 webui._retry_job 去 UI）。"""
    ctx = job.retry_ctx or {}
    api_key = (ctx.get('api_key') or '').strip() or _load_config().get('gemini_api_key', '').strip()
    mf = ctx.get('model_filter', job.model_filter)
    need_b2 = (mf in ('b2', 'both')) and not (job.b2_path and os.path.exists(str(job.b2_path)))
    need_pro = (mf in ('pro', 'both')) and not (job.pro_path and os.path.exists(str(job.pro_path)))
    if not (need_b2 or need_pro):
        update_job(job, status='done')
        return
    _cancel_jobs.discard(job.job_id)   # 清掉残留取消标记，否则 should_cancel 恒 True
    generation = _cancel_generation[0]
    update_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='')
    try:
        should_cancel = lambda: _is_cancelled(job.job_id, generation)

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
        results = await asyncio.gather(*[c for _, c in tasks], return_exceptions=True)
        errs = []
        for (k, _), res in zip(tasks, results):
            e = str(res) if isinstance(res, Exception) else res[1]
            if e and '取消' not in e:
                errs.append(f'{k.upper()}: {e}')
        final = compute_final_status(mf, job.b2_path, job.pro_path)
        update_job(job, status=final, error=('；'.join(errs)).strip())
    except Exception as e:
        logger.exception(f"[API重试] unhandled job={job.job_id}")
        update_job(job, status='failed', error=str(e))
    finally:
        _cancel_jobs.discard(job.job_id)
        _persist_jobs()


async def _edit_bg(job: JobRecord, *, api_key, instruction, model_id, model_label,
                   preserve, image_size, color_match=False):
    """对 job 现有 Pro(或 B2)成图做一次图生图编辑/磨缝（移植 webui._polish_pro 去 UI）。
    color_match=True 时把结果色彩对齐回原图（磨缝消偏色）；自定义编辑保留模型输出色彩。"""
    jid = job.job_id
    generation = _cancel_generation[0]
    src_path = job.pro_path or job.b2_path
    if not src_path or not os.path.exists(str(src_path)):
        update_job(job, status='failed', error='没有可编辑的成图')
        return
    update_job(job, status='running', started_at=time.time(), pro_polishing=True, pro_stage='')

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
        ar = _infer_aspect_ratio_from_b64(b64)
        if _pro_semaphore.locked():
            _on_stage('排队中')
        async with _pro_semaphore:
            out, err = await asyncio.to_thread(
                call_gemini_edit, api_key, model_id, instruction, b64,
                image_size, ar, preserve, _on_stage, lambda: _is_cancelled(jid, generation))
        if out is None:
            update_job(job, status='failed', error=f'编辑失败：{err}')
            return
        if color_match:
            try:
                out = await asyncio.to_thread(_match_color_to_reference, out, src_pil)
            except Exception as ex:
                logger.warning(f"[编辑] 色彩对齐失败(用未对齐图) job={jid}: {ex}")
        ppath = _save_api_result_jpg(out, model_label, job.png_path or src_path)
        add_candidate(job, 'pro', ppath)   # 结果并入 Pro 候选，‹n/N› 可切回原图对比
        if job.json_path and job.record_id:
            try:
                await asyncio.to_thread(_api_write_to_record, out, model_label, job.json_path, job.record_id, ppath)
            except Exception as ex:
                logger.warning(f"[编辑] 写记录失败 job={jid}: {ex}")
        update_job(job, status='done', pro_path=ppath)
        logger.info(f"[API编辑] 完成 job={jid}, label={model_label}, path={ppath}")
    except Exception as e:
        logger.exception(f"[API编辑] 异常 job={jid}")
        update_job(job, status='failed', error=str(e))
    finally:
        update_job(job, pro_polishing=False, pro_stage='')
        _cancel_jobs.discard(jid)
        _persist_jobs()


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


class JobSubmitRequest(BaseModel):
    model_config = {'protected_namespaces': ()}   # 允许 model_filter

    image_path: str                       # /api/uploads/floor 返回的绝对路径
    model_filter: Literal['b2', 'pro', 'both'] = 'both'
    api_key: str = ''                     # 缺省回退 engine_config.json 里的 gemini_api_key
    room_path: Optional[str] = None       # 房间替换图（地板替换流程）
    ref_path: Optional[str] = None        # 参照模式参考图
    params: GenParams


class EditRequest(BaseModel):
    model_config = {'protected_namespaces': ()}

    instruction: str
    api_key: str = ''
    image_size: str = '4K'
    preserve_floor_geometry: bool = True
    model_choice: str = 'Nano Banana Pro'   # GEMINI_MODEL_MAP 的 key


class RevealRequest(BaseModel):
    json_path: str
    record_id: str
    password: str


class ErrRequest(BaseModel):
    err: str


class ConfigPatch(BaseModel):
    gemini_api_key: Optional[str] = None
    fal_api_key: Optional[str] = None
    image_provider: Optional[str] = None
    speed_profile: Optional[str] = None
    auto_failover: Optional[bool] = None
    proxy: Optional[str] = None


def _config_view() -> dict:
    cfg = _load_config()
    return {
        'has_gemini_key': bool((cfg.get('gemini_api_key') or '').strip()),
        'has_fal_key': bool((cfg.get('fal_api_key') or '').strip()),
        'image_provider': get_image_provider(),
        'speed_profile': get_speed_profile(),
        'auto_failover': get_auto_failover(),
        'tls_verify': get_tls_verify(),
        'proxy': get_proxy(),
        'max_concurrent_per_model': int(cfg.get('max_concurrent_per_model', 1) or 1),
        'speed_params': get_speed_profile_params(cfg),
    }


# ============================================================
# FastAPI app + 生命周期
# ============================================================
@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _b2_semaphore, _pro_semaphore, _task_prep_lock
    try:
        lim = max(1, int(_load_config().get('max_concurrent_per_model', 1)))
    except Exception as ex:
        logger.warning(f"读取 max_concurrent_per_model 失败，用默认 1: {ex}")
        lim = 1
    _b2_semaphore = asyncio.Semaphore(lim)
    _pro_semaphore = asyncio.Semaphore(lim)
    _task_prep_lock = asyncio.Lock()
    with _job_lock:
        _job_history.extend(load_persisted_jobs())   # 启动恢复；中断态已被修正为 partial/failed
    logger.info(f"[server_api] 启动完成：恢复 {len(_job_history)} 条历史任务，每模型并发 {lim}")
    yield


app = FastAPI(title="Floor Engine API", version="step1", lifespan=lifespan)

# CORS：开给前端 dev origin。绑 127.0.0.1，本机自用，不放公网。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in
                   os.environ.get('FLOOR_API_CORS', 'http://localhost:3000,http://127.0.0.1:3000').split(',')
                   if o.strip()],
    allow_methods=['*'],
    allow_headers=['*'],
)


# ── 健康检查 ──
@app.get('/api/healthz')
def healthz():
    return {'ok': True}


# ── 任务：提交 / 列表 / 详情 / SSE / 取消 / 重试 ──
@app.post('/api/jobs')
async def create_job(req: JobSubmitRequest):
    if not ((req.api_key or '').strip() or _load_config().get('gemini_api_key', '').strip()):
        raise HTTPException(400, '缺少 API Key')
    if not req.image_path or not os.path.exists(req.image_path):
        raise HTTPException(400, '地板图文件不存在，请先上传')
    label = {'b2': '[B2]', 'pro': '[Pro]', 'both': '[双模型]'}.get(req.model_filter, '')
    room_disp = req.params.cn_room_type if req.params.cn_mode else req.params.room_type
    dname = f"{os.path.splitext(os.path.basename(req.image_path))[0]} · {room_disp} {label}"
    job = new_job(dname, time.strftime('%H:%M:%S'), req.model_filter)
    job.workflow_mode = req.params.workflow_mode
    with _job_lock:
        _job_history.insert(0, job)
    asyncio.create_task(_run_job_bg(job, req))   # 立即返回，不为整个 4K 生成挂起 HTTP
    return _job_view(job)


@app.get('/api/jobs')
def list_jobs(status: str = '', limit: int = 50):
    with _job_lock:
        jobs = list(_job_history)
    if status:
        jobs = [j for j in jobs if j.status == status]
    return [_job_view(j) for j in jobs[:max(1, limit)]]


@app.get('/api/jobs/{jid}')
def get_job(jid: str):
    job = _get_job(jid)
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
            job = _get_job(jid)
            if job is None:
                yield f"event: error\ndata: {json.dumps({'error': 'job not found'})}\n\n"
                break
            data = json.dumps(_job_view(job), ensure_ascii=False)
            yield f"data: {data}\n\n"
            if job.status in ('done', 'partial', 'failed'):
                yield f"event: done\ndata: {data}\n\n"
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    job = _get_job(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    _cancel_jobs.add(jid)   # 终态由 worker finally 据「是否已出图」判定（已计费的图保留）
    if not job.error:
        update_job(job, error='已取消（用户停止）')
    return {'cancelled': True}


@app.post('/api/jobs/cancel-all')
def cancel_all():
    _cancel_generation[0] += 1
    n = 0
    for job in list(_job_history):
        if job.status in ('queued', 'running'):
            _cancel_jobs.add(job.job_id)
            n += 1
    return {'stopped': n}


@app.post('/api/jobs/{jid}/retry')
async def retry_job(jid: str):
    job = _get_job(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.retry_ctx:
        raise HTTPException(400, '该任务缺少重试信息（可能重启后丢失），请重新提交')
    if job.status not in ('failed', 'partial'):
        return _job_view(job)
    asyncio.create_task(_retry_bg(job))
    return _job_view(job)


@app.get('/api/jobs/{jid}/result')
def job_result(jid: str, model: str = 'pro', idx: int = -1):
    job = _get_job(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    ensure_candidate_lists(job)
    paths = job.pro_paths if model == 'pro' else job.b2_paths
    cur = job.pro_idx if model == 'pro' else job.b2_idx
    if not paths:
        raise HTTPException(404, 'no result for this model')
    i = cur if idx < 0 else max(0, min(idx, len(paths) - 1))
    return {'model': model, 'idx': i, 'total': len(paths),
            'url': _to_url(paths[i]), 'thumb': _result_thumb_url(paths[i])}


# ── 编辑 / 磨缝（对 job 现有成图）──
@app.post('/api/jobs/{jid}/polish')
async def polish_job(jid: str):
    job = _get_job(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.pro_path or not os.path.exists(str(job.pro_path)):
        raise HTTPException(400, '没有可磨缝的 Pro 图')
    if job.pro_polishing:
        return _job_view(job)
    api_key = _load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    asyncio.create_task(_edit_bg(
        job, api_key=api_key, instruction=FLOOR_DESEAM_INSTRUCTION,
        model_id=GEMINI_MODEL_MAP['Nano Banana Pro'], model_label='Nano Banana Pro_磨缝',
        preserve=False, image_size='4K', color_match=True))
    return _job_view(job)


@app.post('/api/jobs/{jid}/edit')
async def edit_job(jid: str, req: EditRequest):
    job = _get_job(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.pro_polishing:
        return _job_view(job)
    api_key = (req.api_key or '').strip() or _load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    model_id = GEMINI_MODEL_MAP.get(req.model_choice, GEMINI_MODEL_MAP['Nano Banana Pro'])
    asyncio.create_task(_edit_bg(
        job, api_key=api_key, instruction=req.instruction, model_id=model_id,
        model_label=f'{req.model_choice} 二改', preserve=req.preserve_floor_geometry,
        image_size=req.image_size, color_match=False))
    return _job_view(job)


# ── 上传 / 历史小样 ──
def _save_upload(file: UploadFile, prefix: str) -> dict:
    data = file.file.read()
    dest = safe_upload_path(file.filename or 'upload.jpg', prefix)
    if not dest:
        raise HTTPException(400, '不支持的文件类型（仅 jpg/jpeg/png/webp/bmp）')
    with open(dest, 'wb') as f:
        f.write(data)
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


@app.get('/api/swatches/recent')
def recent_swatches(limit: int = 24):
    out = []
    for p in _list_recent_floor_swatches(limit):
        out.append({'path': p, 'url': _to_url(p), 'name': os.path.basename(p), 'thumb': _thumb_url(p)})
    return out


# ── 记录 ──
@app.get('/api/records')
def list_records():
    out = []
    for jp in scan_json_files():
        out.append({'json_path': jp, 'labels': get_record_labels(jp)})
    return out


@app.get('/api/records/load')
def load_records(json_path: str):
    recs = _load_records(json_path)
    # 结果图引用改写成 URL；内联 base64(老记录)不回传大 blob，仅标记 has_inline。
    for r in recs:
        for res in r.get('results', []) if isinstance(r, dict) else []:
            rel = res.get('result_image_file')
            if rel:
                ap = _safe_output_path(rel)
                res['result_url'] = _to_url(ap) if ap else ''
                res['result_thumb'] = _result_thumb_url(ap) if ap else ''
            res['has_inline'] = bool(res.pop('result_image_b64', None))
    return recs


@app.post('/api/records/reveal')
def reveal(req: RevealRequest):
    text = reveal_prompt_fn(req.json_path, req.record_id, req.password)
    ok = not (text.startswith('🔒') or text.startswith('❌'))
    return {'text': text, 'ok': ok}


# ── 配方 / 失败知识库 / 连通性 / 配置 / 模型 ──
@app.get('/api/recipes')
def recipes(tone: str = '', limit: int = 6):
    if tone:
        return recommend_recipes(tone, limit)
    return FLOOR_RECIPES[:limit] if limit else FLOOR_RECIPES


@app.post('/api/failure/classify')
def classify(req: ErrRequest):
    return classify_failure(req.err)


@app.get('/api/connection/test')
def connection_test(gemini: str = '', fal: str = '', proxy: str = ''):
    cfg = _load_config()
    g = gemini or cfg.get('gemini_api_key', '')
    f = fal or cfg.get('fal_api_key', '')
    p = proxy or get_proxy()
    return {'result': test_connection(g, f, p)}


@app.get('/api/config')
def get_config():
    return _config_view()


@app.put('/api/config')
def put_config(req: ConfigPatch):
    cfg = _load_config()
    # save_api_key 同时写 key+proxy：只改其一时用现值补另一个
    if req.gemini_api_key is not None or req.proxy is not None:
        save_api_key(req.gemini_api_key if req.gemini_api_key is not None else cfg.get('gemini_api_key', ''),
                     req.proxy if req.proxy is not None else get_proxy())
    if req.fal_api_key is not None or req.image_provider is not None:
        save_provider_settings(fal_api_key_val=req.fal_api_key, image_provider_val=req.image_provider)
    if req.speed_profile is not None:
        save_speed_profile(req.speed_profile)
    if req.auto_failover is not None:
        save_auto_failover(req.auto_failover)
    return _config_view()


@app.get('/api/models')
def models_endpoint():
    return {'gemini': GEMINI_MODEL_MAP, 'fal': FAL_MODEL_MAP, 'provider': get_image_provider()}


# ============================================================
# 静态/缩略图（移植自 webui：懒生成缩略图 + _safe_output_path 越界防护）
# ============================================================
@app.get('/thumb/uploads/{name}')
def serve_upload_thumb(name: str, s: int = 320):
    name = os.path.basename(name)   # 挡路径穿越
    src = os.path.join(UPLOAD_DIR, name)
    if not os.path.isfile(src):
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
            logger.warning(f"[缩略图] 生成失败，回退原图 {name}: {ex}")
            return FileResponse(src)
    return FileResponse(cache, media_type='image/jpeg')


@app.get('/thumb/outputs/{relpath:path}')
def serve_output_thumb(relpath: str, s: int = 480):
    src = _safe_output_path(relpath)   # 越界/不存在 → None
    if not src:
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
            logger.warning(f"[结果缩略图] 生成失败，回退原图 {relpath}: {ex}")
            return FileResponse(src)
    return FileResponse(cache, media_type='image/jpeg')


# 原图静态服务（缩略图路由已在上面注册，优先匹配；这里挂目录服务原图/下载）
app.mount('/outputs', StaticFiles(directory=MAIN_OUTPUT_DIR), name='outputs')
app.mount('/uploads', StaticFiles(directory=UPLOAD_DIR), name='uploads')


# ============================================================
# 直接运行入口：python -m Floor_engine_server.server_api （在 test/ 目录下）
# ============================================================
if __name__ == '__main__':
    import uvicorn
    host = os.environ.get('FLOOR_API_HOST', '127.0.0.1')
    port = int(os.environ.get('FLOOR_API_PORT', '7870'))
    # 传 app 对象 = 单进程单 worker（_job_history/信号量是进程内状态，必须单 worker）
    uvicorn.run(app, host=host, port=port)
