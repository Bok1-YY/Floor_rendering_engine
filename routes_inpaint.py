# -*- coding: utf-8 -*-
"""生成式修补路由 —— 两段式抽卡:生成候选 → 挑选提交(usage 生成时记,apply 不计费)。"""
import asyncio
import hashlib
import os
import time
import uuid

from fastapi import APIRouter, HTTPException
from PIL import Image

from . import server_state as state
from .api import call_image_inpaint, resolve_inpaint_engine, effective_inpaint_candidate_count
from .config import logger, safe_upload_path, get_comfyui_settings
from .image_ops import (
    decode_inpaint_mask, prepare_inpaint_masks, normalize_inpaint_source,
    save_inpaint_candidate_png,
)
from .floor_segmentation import scan_object_masks, segment_mask_at_point
from .models import (
    update_job, ensure_model_runs, add_model_candidate, compute_runs_final_status,
)
from .usage_stats import record_usage
from .records import (
    save_api_result_png, api_write_to_record,
    append_edited_result_to_record, load_records_file, safe_output_path, b64_to_pil,
)
from .server_helpers import (
    to_url, thumb_url, result_thumb_url, job_view,
    require_record_json_path, require_upload_image_path, require_output_image_rel,
)
from .server_schemas import (
    GenericInpaintRequest, InpaintApplyRequest, InpaintPayload, InpaintSegmentRequest, InpaintTarget,
)

router = APIRouter()


def _require_inpaint_prompt(req: InpaintPayload) -> None:
    if req.mode == 'add' and not (req.prompt or '').strip():
        raise HTTPException(400, '生成式添加需要描述要添加的内容')


def _resolve_inpaint_source(target: InpaintTarget):
    """按 target 定位源图，校验逻辑与旧三端点一致。返回 (src_pil, workflow_mode, operation)。"""
    if target.kind == 'job':
        job = state.JOBS.get(target.jid)
        if not job:
            raise HTTPException(404, 'job not found')
        if job.status in ('running', 'queued') or job.pro_polishing or job.operation_status == 'running':
            raise HTTPException(409, '任务进行中，请稍后修补')
        abs_src = require_output_image_rel(target.image_rel)
        ensure_model_runs(job)
        cand = {os.path.realpath(str(p)) for p in ((job.model_runs.get(target.stage) or {}).get('paths') or []) if p}
        if os.path.realpath(abs_src) not in cand:
            raise HTTPException(400, '该图不属于此任务的候选')
        with Image.open(abs_src) as image:
            src = normalize_inpaint_source(image)
        return src, job.workflow_mode, 'inpaint'
    if target.kind == 'record':
        json_path = require_record_json_path(target.json_path)
        recs = load_records_file(json_path)
        rec = next((r for r in recs if r.get('id') == target.record_id), None)
        if not rec:
            raise HTTPException(404, '未找到记录')
        if rec.get('immutable_audit'):
            raise HTTPException(409, {
                'code': 'immutable_audit_record',
                'message': '全景门禁审计记录不能从通用历史页发起生成式修补',
            })
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
                    src = normalize_inpaint_source(image)
            except Exception:
                src = None
        if src is None and res.get('result_image_b64'):
            decoded = b64_to_pil(res['result_image_b64'])
            src = normalize_inpaint_source(decoded) if decoded is not None else None
        if src is None:
            raise HTTPException(404, '该结果无可用图片')
        return src, rec.get('workflow_mode', ''), 'record_inpaint'
    room = require_upload_image_path(target.room_path, '房间图', required=True)
    with Image.open(room) as image:
        src = normalize_inpaint_source(image)
    return src, '房间图预处理', 'room_prep'


def _segmentation_cache_key(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(f'{image.mode}:{image.width}x{image.height}:'.encode('ascii'))
    digest.update(image.tobytes())
    return f'inpaint:{digest.hexdigest()}'


@router.post('/api/inpaint/segment')
def inpaint_segment(req: InpaintSegmentRequest):
    """Offline smart-mask proposals for the same validated source used by inpaint."""
    src, _workflow_mode, _operation = _resolve_inpaint_source(req.target)
    cache_key = _segmentation_cache_key(src)
    if req.strategy == 'point':
        if req.point is None:
            raise HTTPException(422, '点选识别需要 point 坐标')
        result = segment_mask_at_point(src, cache_key, req.point.x, req.point.y)
    else:
        result = scan_object_masks(src, cache_key)
    return {
        'width': result.size[0],
        'height': result.size[1],
        'status': result.status,
        'warnings': result.warnings,
        'model': result.model,
        'candidates': [{
            'id': candidate.id,
            'rle': candidate.rle,
            'bbox': list(candidate.bbox),
            'area': candidate.area,
            'confidence': round(float(candidate.confidence), 4),
            'stability': round(float(candidate.stability), 4),
        } for candidate in result.candidates],
    }


async def _generic_inpaint_bg(iid: str, src_pil, engine_mask, blend_mask, prompt, mode, seed, n,
                              workflow_mode, operation, resolved_engine):
    """并发生成 n 个候选（信号量整体持有一次=一次用户操作），部分失败仍交付成功的候选。"""
    def _set(**kw):
        state.INPAINTS.update_fields(iid, **kw)

    should_cancel = lambda: state.INPAINTS.is_cancelled(iid)
    try:
        async with state.model_semaphores['inpaint']:
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
        os.makedirs(state.INPAINT_TMP_DIR, exist_ok=True)
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
            path = os.path.join(state.INPAINT_TMP_DIR, f'{iid}_{i}.png')
            try:
                await asyncio.to_thread(save_inpaint_candidate_png, out, path)
            except Exception as ex:
                logger.error(f'[修补] 候选 {i} 保存失败 iid={iid}: {ex}')
                last_err = f'候选保存失败：{ex}'
                continue
            candidates.append({'url': to_url(path), 'thumb': result_thumb_url(path), 'path': path})
        if should_cancel():
            state.delete_inpaint_files({'candidates': candidates})
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
        state.INPAINTS.clear_cancelled(iid)


@router.post('/api/inpaint')
async def create_inpaint(req: GenericInpaintRequest):
    """提交修补：定位源图 → 标准化 mask → 后台并发生成 n 个候选，前端轮询后挑选提交。"""
    _require_inpaint_prompt(req)
    src_pil, workflow_mode, operation = _resolve_inpaint_source(req.target)
    mask_raw = decode_inpaint_mask(req.mask_b64)
    requested_grow = req.grow if req.grow is not None else (8 if req.mode == 'remove' else 0)
    engine_mask, blend_mask = await asyncio.to_thread(
        prepare_inpaint_masks, mask_raw, src_pil.size, requested_grow, req.feather, req.mode)
    resolved_engine = resolve_inpaint_engine(req.mode)
    effective_n, notice = effective_inpaint_candidate_count(
        req.mode, req.n, resolved_engine=resolved_engine)
    iid = f'ip_{uuid.uuid4().hex}'
    with state.INPAINTS.locked() as entries:
        state.INPAINTS.trim_locked(reserve=1)
        if state.inpaint_queue_is_full(entries):
            raise HTTPException(429, '修补队列已满，请等待当前任务完成后再试')
        entries[iid] = {'status': 'running', 'stage': '', 'candidates': [], 'error': '',
                          'ts': time.time(), 'target': req.target.model_dump(),
                          'mode': req.mode, 'prompt': (req.prompt or '').strip(),
                          'requested_n': req.n, 'effective_n': effective_n, 'notice': notice,
                          'provider': resolved_engine[0], 'model_key': resolved_engine[1]}
    state.spawn(_generic_inpaint_bg(iid, src_pil, engine_mask, blend_mask, req.prompt, req.mode, req.seed,
                               effective_n, workflow_mode, operation, resolved_engine))
    return {'inpaint_id': iid, 'requested_n': req.n, 'effective_n': effective_n, 'notice': notice}


@router.post('/api/inpaint/{iid}/apply')
async def inpaint_apply(iid: str, req: InpaintApplyRequest):
    """把选中的候选提交到目标（apply 不计费；成功后清理该次全部临时候选）。"""
    with state.INPAINTS.locked() as entries:
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
            job = state.JOBS.get(target.jid)
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
            state.JOBS.persist()
            resp = {'ok': True, 'job': job_view(job)}
        elif target.kind == 'record':
            json_path = require_record_json_path(target.json_path)
            ppath = await asyncio.to_thread(save_api_result_png, out, label,
                                            json_path.replace('_记录.json', '_优化图.png'))
            if not ppath:
                raise HTTPException(500, '结果保存失败')
            msg = await asyncio.to_thread(append_edited_result_to_record, json_path, target.record_id,
                                          target.result_id, out, prompt or label, label, ppath)
            if not str(msg).startswith('✅'):
                raise HTTPException(500, str(msg))
            resp = {'ok': True, 'result_url': to_url(ppath)}
        else:
            stem = os.path.splitext(os.path.basename(target.room_path))[0]
            dest = safe_upload_path(f'{stem}_clean.png', 'room_')
            if not dest:
                raise HTTPException(500, '结果保存路径无效')
            await asyncio.to_thread(lambda: out.convert('RGB').save(dest, format='PNG', optimize=True))
            resp = {'ok': True, 'path': dest, 'url': to_url(dest), 'thumb': thumb_url(dest)}
    except Exception:
        with state.INPAINTS.locked() as entries:
            if entries.get(iid) is entry:
                entry['status'] = 'done'
        raise
    entry = state.INPAINTS.pop(iid)   # pop 连带清取消标记
    state.delete_inpaint_files(entry)
    logger.info(f'[修补] 已提交候选 iid={iid}, kind={target.kind}, index={req.index}')
    return resp


@router.get('/api/inpaint/comfyui/ping')
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


@router.get('/api/inpaint/{iid}')
def inpaint_status(iid: str):
    with state.INPAINTS.locked() as entries:
        v = entries.get(iid)
        if not v:
            raise HTTPException(404, 'inpaint task not found')
        # 只回传前端需要的字段（path 是服务端内部路径，target/prompt 前端已知）
        return {'inpaint_id': iid, 'status': v.get('status', ''), 'stage': v.get('stage', ''),
                'error': v.get('error', ''), 'notice': v.get('notice', ''),
                'requested_n': v.get('requested_n', 1), 'effective_n': v.get('effective_n', 1),
                'candidates': [{'url': c.get('url', ''), 'thumb': c.get('thumb', '')}
                               for c in (v.get('candidates') or [])]}


@router.post('/api/inpaint/{iid}/cancel')
def inpaint_cancel(iid: str):
    """running 中 = 标记取消(引擎轮询停止)；终态 = 直接清理（前端「再抽」前废弃旧候选）。"""
    with state.INPAINTS.locked() as entries:
        entry = entries.get(iid)
        if not entry:
            raise HTTPException(404, 'inpaint task not found')
        if entry.get('status') == 'applying':
            raise HTTPException(409, '候选正在写入，无法取消')
        if entry.get('status') != 'running':
            state.delete_inpaint_files(entry)
            entries.pop(iid, None)
            state.INPAINTS.clear_cancelled(iid)
            return {'cancelled': True}
    state.INPAINTS.request_cancel(iid)
    return {'cancelled': True}
