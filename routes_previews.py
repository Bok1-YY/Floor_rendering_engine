# -*- coding: utf-8 -*-
"""快速预览路由 —— Nano Banana 2 Lite 出 1K 草图(不进队列、不写记录、恒 Google 直连)。"""
import asyncio
import time
import uuid

from fastapi import APIRouter, HTTPException

from . import server_state as state
from .api import call_gemini_generate, analyze_style_image
from .config import logger, load_config, LITE_PREVIEW_MODEL, get_bevel_ref_image, extract_clean_prompt
from .models import TaskParams, task_params_to_kwargs
from .prompts import save_task_files_html
from .records import save_api_result_jpg
from .usage_stats import record_usage
from .server_helpers import to_url, result_thumb_url, require_upload_image_path, panel_require_second_image
from .server_schemas import PreviewRequest

router = APIRouter()


# ── 快速预览：Nano Banana 2 Lite 出一张 1K 草图（不进队列、不写记录、恒 Google 直连）──
async def _preview_bg(pid: str, req: 'PreviewRequest'):
    """借用 save_task_files_html(persist=False) 的提示词逻辑，用 Lite 出 1K 预览。
    prep(rp/sref/bevel_ref) 是 _run_job_bg(约 269-274 行) 的精简版；预览只用 Pro 提示词、无 retry_ctx。
    刻意在预览侧写精简副本、不重构 4K 热路径（4K 主编排是命脉，本仓库测试覆盖不到它）。"""
    def _set(**kw):
        state.PREVIEWS.update_fields(pid, **kw)

    def _on_stage(t):
        _set(stage=t)

    should_cancel = lambda: state.PREVIEWS.is_cancelled(pid)
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
        # 组装提示词（persist=False：借提示词/PNG，不新建记录）；与 4K 主流程共用 state.task_prep_lock
        tp = TaskParams(image_path=req.image_path, style_analysis_text=style_text, **p.model_dump())
        async with state.task_prep_lock:
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
        _set(status='done', stage='', url=to_url(path), thumb=result_thumb_url(path))
        logger.info(f"[快速预览] 完成 pid={pid}, path={path}")
    except Exception as e:
        logger.exception(f"[快速预览] 异常 pid={pid}")
        _set(status='failed', error=str(e), stage='')
    finally:
        state.PREVIEWS.clear_cancelled(pid)


@router.post('/api/preview')
async def create_preview(req: PreviewRequest):
    if '自由创作' in (req.params.workflow_mode or ''):
        raise HTTPException(422, '自由创作首版不支持快速预览')
    if not ((req.api_key or '').strip() or load_config().get('gemini_api_key', '').strip()):
        raise HTTPException(400, '缺少 API Key')
    req.image_path = require_upload_image_path(req.image_path, '地板图', required=True)
    req.room_path = require_upload_image_path(req.room_path, '房间图')
    req.ref_path = require_upload_image_path(req.ref_path, '参照图')
    panel_require_second_image(req)
    pid = f'pv_{uuid.uuid4().hex}'
    state.PREVIEWS.add(pid, {'status': 'running', 'stage': '', 'url': '', 'thumb': '', 'error': '', 'ts': time.time()})
    state.spawn(_preview_bg(pid, req))   # 秒回 pid，前端轮询 /api/preview/{pid}
    return {'preview_id': pid, 'status': 'running'}


@router.get('/api/preview/{pid}')
def get_preview(pid: str):
    snap = state.PREVIEWS.view(pid) or {}
    if not snap:
        raise HTTPException(404, 'preview not found')
    return {'preview_id': pid, **snap}


@router.post('/api/preview/{pid}/cancel')
def cancel_preview(pid: str):
    state.PREVIEWS.request_cancel(pid)   # _preview_bg 与底层 call_gemini_generate 均会读 should_cancel
    return {'cancelled': True}
