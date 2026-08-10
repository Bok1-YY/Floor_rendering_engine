# -*- coding: utf-8 -*-
"""工具路由 —— 地板识色+智能配方、本地地板可视化渲染、全图校色(预览/任务/记录)。"""
import asyncio
import base64
import io
import os
import tempfile
import threading
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from PIL import Image, ImageOps

from . import server_state as state
from .color_match import (
    ColorAnalysisError,
    analyze_color_region,
    match_color_global,
    match_color_masked,
)
from .config import logger, load_config, safe_upload_path
from .floor_renderer import RenderRecipe, image_sha256, render_floor, validate_calibration_quad
from .image_ops import decode_floor_mask, FLOOR_PREVIEW_MAX_SIDE
from .floor_segmentation import segment_floor, encode_mask_png
from .models import (
    update_job, ensure_model_runs, add_model_candidate, compute_runs_final_status,
)
from .image_prep import analyze_floor_tone
from .prompt_data import (
    FLOOR_TONES, STYLES, LIGHTINGS, ANGLES, RESOLUTIONS, ASPECT_RATIOS,
)
from .recipes import recommend_recipes, pick_option_key
from .records import (
    load_records_file, safe_output_path, b64_to_pil,
    save_api_result_jpg, save_api_result_png, api_write_to_record,
    append_edited_result_to_record,
)
from .server_helpers import (
    to_url, thumb_url, job_view,
    require_record_json_path, require_upload_image_path,
    require_output_image_rel, require_ref_image_path,
    record_color_match_ref_path,
)
from .server_schemas import (
    ColorMatchAdjustments, ColorMatchPreviewRequest, ColorMatchRect, ColorMatchSegmentRequest,
    FloorVisualizeRequest, FloorVisualizeTarget,
    JobColorMatchRequest, RecordColorMatchRequest,
)

router = APIRouter()


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
        'aspect_ratio': pick_option_key(ASPECT_RATIOS, r.get('aspect_kw') or []) or '',
        'resolution': pick_option_key(RESOLUTIONS, r.get('res_kw') or []) or '',
    }


@router.get('/api/floor/analyze')
def floor_analyze(path: str):
    path = require_upload_image_path(path, '地板图', required=True)
    tone, _html = analyze_floor_tone(path)
    matched = next((t for t in FLOOR_TONES if tone and (tone in t or t in tone)), tone or FLOOR_TONES[0])
    return {'tone': matched, 'recipes': [_resolve_recipe(r) for r in recommend_recipes(matched, 6)]}


_floor_render_lock = threading.Lock()  # 4K OpenCV working set is sizeable; serialize local renders.

def _resolve_floor_source(target: FloorVisualizeTarget):
    """Return detached RGB image plus the validated write-back context."""
    if target.kind == 'job':
        job = state.JOBS.get(target.jid)
        if not job:
            raise HTTPException(404, 'job not found')
        if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
            raise HTTPException(409, '任务进行中，请稍后贴地板')
        abs_src = require_output_image_rel(target.image_rel)
        ensure_model_runs(job)
        candidates = {os.path.realpath(str(p)) for p in
                      ((job.model_runs.get(target.stage) or {}).get('paths') or []) if p}
        if os.path.realpath(abs_src) not in candidates:
            raise HTTPException(400, '该图不属于此任务的候选')
        with Image.open(abs_src) as im:
            src = ImageOps.exif_transpose(im).convert('RGB').copy()
        return src, {'job': job, 'source_path': abs_src}
    if target.kind == 'record':
        json_path = require_record_json_path(target.json_path)
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
    room_path = require_upload_image_path(target.room_path, '房间图', required=True)
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
    texture_path = require_ref_image_path(req.texture_path)
    mask = decode_floor_mask(req.mask_b64)
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


@router.post('/api/floor-visualize/preview')
def floor_visualize_preview(req: FloorVisualizeRequest):
    out, metadata, _ = _run_floor_visualize(req, max_side=FLOOR_PREVIEW_MAX_SIDE)
    buf = io.BytesIO()
    out.save(buf, format='PNG', optimize=True)
    return {
        'preview': 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode(),
        'width': out.width,
        'height': out.height,
        'warnings': metadata.get('warnings') or [],
        'metadata': metadata,
    }


@router.post('/api/floor-visualize/apply')
async def floor_visualize_apply(req: FloorVisualizeRequest):
    out, metadata, context = await asyncio.to_thread(_run_floor_visualize, req, 0)
    label = '真实纹理投影'
    target = req.target
    if target.kind == 'job':
        job = context['job']
        current = state.JOBS.get(job.job_id)
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
        state.JOBS.persist()
        logger.info(f'[真实贴地板] 任务候选已保存 job={job.job_id}, stage={target.stage}, path={ppath}')
        return {'ok': True, 'job': job_view(job), 'url': to_url(ppath),
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
        return {'ok': True, 'result_url': to_url(ppath), 'result_id': result_id,
                'warnings': metadata.get('warnings') or [], 'metadata': metadata}
    room_path = context['room_path']
    stem = os.path.splitext(os.path.basename(room_path))[0]
    dest = safe_upload_path(f'{stem}_floor.png', 'room_')
    if not dest:
        raise HTTPException(500, '结果保存路径无效')
    await asyncio.to_thread(lambda: out.save(dest, format='PNG', optimize=True))
    return {'ok': True, 'path': dest, 'url': to_url(dest), 'thumb': thumb_url(dest),
            'warnings': metadata.get('warnings') or [], 'metadata': metadata}


_PREVIEW_MAX_SIDE = 1600


def _run_color_match(abs_src: str, ref_path: str, rect: ColorMatchRect,
                     strength: float, feather: float, max_side: int = 0,
                     adjustments: Optional[ColorMatchAdjustments] = None,
                     adjustment_mode: Literal['auto', 'manual'] = 'auto',
                     scope: Literal['global', 'floor_mask'] = 'global',
                     mask_b64: str = '', mask_feather: float = 0.003,
                     return_auto_adjustments: bool = False,
                     return_analysis: bool = False,
                     algorithm: Literal['classic', 'distribution'] = 'classic',
                     illumination_mode: Literal['off', 'chroma', 'full'] = 'off',
                     return_quality_report: bool = False):
    """读图 → （可选缩到 max_side 长边）→ 全图校色。
    rect 只用于地板统计/诊断，feather 保留为兼容字段但不再限制修改范围。"""
    src = Image.open(abs_src)
    src.load()
    if max_side:
        src.thumbnail((max_side, max_side), Image.LANCZOS)
    ref = Image.open(ref_path)
    ref.load()
    mask = None
    if scope == 'floor_mask':
        if not mask_b64:
            raise HTTPException(422, '地板局部校色需要有效蒙版')
        mask = decode_floor_mask(mask_b64)
        try:
            result = match_color_masked(
                src, ref, mask, strength=strength,
                adjustments=(adjustments.model_dump() if adjustments else None),
                adjustment_mode=adjustment_mode, mask_feather=mask_feather,
                return_auto_adjustments=return_auto_adjustments,
                algorithm=algorithm, illumination_mode=illumination_mode,
                return_quality_report=return_quality_report)
        except ColorAnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc
    else:
        try:
            result = match_color_global(
                src, ref, (rect.x, rect.y, rect.w, rect.h),
                strength=strength, feather=feather,
                adjustments=(adjustments.model_dump() if adjustments else None),
                adjustment_mode=adjustment_mode,
                return_auto_adjustments=return_auto_adjustments,
                algorithm=algorithm, illumination_mode=illumination_mode,
                return_quality_report=return_quality_report)
        except ColorAnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc
    if not return_analysis:
        return result
    if return_quality_report:
        out, auto_adjustments, quality_report = result
    else:
        out, auto_adjustments = result
        quality_report = None
    analysis = analyze_color_region(src, ref, (rect.x, rect.y, rect.w, rect.h), mask)
    return out, auto_adjustments, analysis, quality_report


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


def _serialize_quality_report(report) -> Optional[dict]:
    if report is None:
        return None
    serialized = report.to_dict()
    overlay = report.diagnostic_overlay
    serialized['diagnostic_overlay'] = None
    if overlay is not None:
        buf = io.BytesIO()
        overlay.save(buf, format='PNG', optimize=True)
        serialized['diagnostic_overlay'] = (
            'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode())
    return serialized


def _save_color_mask(mask_b64: str, result_path: str, size: tuple[int, int]) -> str:
    """Persist the exact local-edit mask beside a result for audit/re-edit."""
    mask = decode_floor_mask(mask_b64).convert('L')
    if mask.size != size:
        mask = mask.resize(size, Image.Resampling.NEAREST)
    root, _ = os.path.splitext(result_path)
    target = root + '_mask.png'
    fd, tmp = tempfile.mkstemp(prefix='.color_mask_', suffix='.png', dir=os.path.dirname(target))
    os.close(fd)
    try:
        mask.save(tmp, format='PNG', optimize=True)
        os.replace(tmp, target)
        return os.path.basename(target)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


@router.post('/api/color-match/preview')
def color_match_preview(req: ColorMatchPreviewRequest):
    """预览：缩图处理，返回 data URL（1600 长边 JPEG，无临时文件、无状态）。
    同步 def → FastAPI 线程池执行，numpy 不堵事件循环。"""
    abs_src = require_output_image_rel(req.image_rel)
    ref = require_ref_image_path(req.ref_path)
    result = _run_color_match(
        abs_src, ref, req.rect, req.strength, req.feather,
        max_side=_PREVIEW_MAX_SIDE, adjustments=req.adjustments,
        adjustment_mode=req.adjustment_mode, scope=req.scope,
        mask_b64=req.mask_b64, mask_feather=req.mask_feather,
        return_auto_adjustments=True,
        return_analysis=req.include_analysis,
        algorithm=req.algorithm, illumination_mode=req.illumination_mode,
        return_quality_report=req.include_analysis)
    if req.include_analysis:
        out, auto_adjustments, analysis, quality_report = result
    else:
        out, auto_adjustments = result
        analysis = None
        quality_report = None
    buf = io.BytesIO()
    if req.scope == 'floor_mask':
        out.save(buf, format='PNG', optimize=True)
        mime = 'image/png'
    else:
        out.save(buf, format='JPEG', quality=85)
        mime = 'image/jpeg'
    b64 = base64.b64encode(buf.getvalue()).decode()
    response = {'preview': f'data:{mime};base64,{b64}',
                'width': out.size[0], 'height': out.size[1],
                'auto_adjustments': auto_adjustments}
    if analysis is not None:
        response['analysis'] = _serialize_color_analysis(analysis)
    if quality_report is not None:
        response['quality_report'] = _serialize_quality_report(quality_report)
    return response


@router.post('/api/color-match/segment')
def color_match_segment(req: ColorMatchSegmentRequest):
    """Generate/refine a local floor mask using offline MobileSAM and brush prompts."""
    abs_src = require_output_image_rel(req.image_rel)
    try:
        stat = os.stat(abs_src)
        cache_key = f'{os.path.realpath(abs_src)}:{stat.st_size}:{stat.st_mtime_ns}'
        with Image.open(abs_src) as image:
            working, result = segment_floor(
                image, cache_key,
                positive_b64=req.positive_mask_b64,
                negative_b64=req.negative_mask_b64,
                previous_b64=req.previous_mask_b64,
                auto_seed=req.auto_seed)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('[AI蒙版] 分割失败')
        raise HTTPException(500, f'AI 地板蒙版失败：{exc}')
    return {
        'mask_b64': encode_mask_png(result.mask) if result.mask is not None else '',
        'width': working.width,
        'height': working.height,
        'confidence': round(float(result.confidence), 4),
        'status': result.status,
        'warnings': result.warnings,
        'model': result.model,
    }



@router.post('/api/jobs/{jid}/color-match')
async def job_color_match(jid: str, req: JobColorMatchRequest):
    """提交（任务侧）：全分辨率处理 → 落盘 → 并入该 stage 候选（‹n/N› 可切回原图）→ 写记录。"""
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
        raise HTTPException(409, '任务进行中，请稍后校色')
    abs_src = require_output_image_rel(req.image_rel)
    ensure_model_runs(job)
    # 归属校验：必须是该任务对应 stage 的候选之一（防跨任务写入 + 免候选下标竞态）
    cand = {os.path.realpath(str(p)) for p in ((job.model_runs.get(req.stage) or {}).get('paths') or []) if p}
    if os.path.realpath(abs_src) not in cand:
        raise HTTPException(400, '该图不属于此任务的候选')
    ref = require_ref_image_path(req.ref_path or job.png_path or '')
    result = await asyncio.to_thread(
        _run_color_match, abs_src, ref, req.rect, req.strength, req.feather,
        adjustments=req.adjustments, adjustment_mode=req.adjustment_mode,
        scope=req.scope, mask_b64=req.mask_b64, mask_feather=req.mask_feather,
        algorithm=req.algorithm, illumination_mode=req.illumination_mode,
        return_quality_report=True)
    out, quality_report = result
    save_result = save_api_result_png if req.scope == 'floor_mask' else save_api_result_jpg
    metadata = {
        'operation': 'color_match', 'scope': req.scope,
        'adjustment_mode': req.adjustment_mode, 'strength': req.strength,
        'mask_feather': req.mask_feather, 'algorithm': req.algorithm,
        'illumination_mode': req.illumination_mode,
        'quality_report': quality_report.to_dict() if quality_report else None,
    }
    if req.scope == 'floor_mask':
        ppath = await asyncio.to_thread(save_result, out, '局部校色',
                                        job.png_path or abs_src, metadata)
    else:
        ppath = await asyncio.to_thread(save_result, out, '手动校色',
                                        job.png_path or abs_src)
    if not ppath:
        raise HTTPException(500, '校色结果保存失败')
    if req.scope == 'floor_mask':
        try:
            metadata['mask_file'] = await asyncio.to_thread(
                _save_color_mask, req.mask_b64, ppath, out.size)
        except Exception as ex:
            logger.warning(f'[校色] 蒙版留档失败 job={jid}: {ex}')
    add_model_candidate(job, req.stage, ppath)
    update_job(job, status=compute_runs_final_status(job))
    if job.json_path and job.record_id:
        try:
            await asyncio.to_thread(api_write_to_record, out, '手动校色',
                                    job.json_path, job.record_id, ppath, metadata)
        except Exception as ex:
            logger.warning(f"[校色] 写记录失败 job={jid}: {ex}")
    state.JOBS.persist()   # 锁外调（内部自取锁）
    logger.info(f"[校色] 完成 job={jid}, stage={req.stage}, strength={req.strength}, path={ppath}")
    return job_view(job)



@router.post('/api/records/color-match')
def record_color_match(req: RecordColorMatchRequest):
    """提交（记录侧）：全分辨率处理 → 结果 append 回该记录。不碰 JOBS 队列。"""
    json_path = require_record_json_path(req.json_path)
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
        ref_path = record_color_match_ref_path(json_path, rec)
    if not ref_path:
        raise HTTPException(400, '该记录没有参照小样，请在弹窗中上传参照图')
    ref = require_ref_image_path(ref_path)
    out, quality_report = _run_color_match(
        abs_src, ref, req.rect, req.strength, req.feather,
        adjustments=req.adjustments, adjustment_mode=req.adjustment_mode,
        scope=req.scope, mask_b64=req.mask_b64, mask_feather=req.mask_feather,
        algorithm=req.algorithm, illumination_mode=req.illumination_mode,
        return_quality_report=True)
    metadata = {
        'operation': 'color_match', 'scope': req.scope,
        'adjustment_mode': req.adjustment_mode, 'strength': req.strength,
        'mask_feather': req.mask_feather, 'algorithm': req.algorithm,
        'illumination_mode': req.illumination_mode,
        'quality_report': quality_report.to_dict() if quality_report else None,
    }
    if req.scope == 'floor_mask':
        ppath = save_api_result_png(
            out, '局部校色', json_path.replace('_记录.json', '_优化图.png'),
            metadata)
    else:
        ppath = save_api_result_jpg(out, '手动校色', json_path.replace('_记录.json', '_优化图.png'))
    if not ppath:
        raise HTTPException(500, '校色结果保存失败')
    if req.scope == 'floor_mask':
        try:
            metadata['mask_file'] = _save_color_mask(req.mask_b64, ppath, out.size)
        except Exception as ex:
            logger.warning(f'[校色] 蒙版留档失败 record={req.record_id}: {ex}')
    if req.scope == 'floor_mask' and req.adjustment_mode == 'auto':
        label = f'地板局部自动{req.strength:.2f}'
    elif req.adjustment_mode == 'auto':
        label = f'自动强度{req.strength:.2f}'
    else:
        label = ('原图基准 · 手动微调' if any(req.adjustments.model_dump().values())
                 else 'Gemini 原图')
    msg = append_edited_result_to_record(json_path, req.record_id, req.result_id,
                                         out, label, '手动校色', ppath, metadata)
    if not str(msg).startswith('✅'):
        raise HTTPException(500, str(msg))
    return {'ok': True, 'result_url': to_url(ppath)}
