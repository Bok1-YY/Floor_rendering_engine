"""HTTP job endpoints; workflow implementation lives in job_service."""
from .billing_phases import require_retry_confirmation, phase_recovery
from .models import model_run_current_path
import asyncio
import json
import os
import time
from uuid import UUID
from . import job_submissions

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from PIL import Image

from . import server_state as state
from .api import FLOOR_DESEAM_INSTRUCTION
from .config import MAIN_OUTPUT_DIR, load_config, GEMINI_MODEL_MAP
from .models import new_job, update_job, ensure_model_runs, nav_model_candidate, legacy_filter_from_targets
from .records import safe_output_path, load_records_file, b64_to_pil
from .server_helpers import (
    to_url, result_thumb_url, job_view,
    require_record_json_path, require_upload_image_path, panel_require_second_image,
)
from .server_schemas import (
    EditRequest, FreeJobSubmitRequest, JobSubmitRequest, RecordEditRequest,
    RetryJobRequest,
)

from .job_service import (
    supports_auto_color_match,
    _set_auto_color_settings,
    _auto_color_match_generated,
    _generate_one_model,
    _generate_sd35_model,
    _run_job_bg,
    _run_free_job_bg,
    _retry_bg,
    _retry_sd_upscale_bg,
    _edit_bg,
    _regen_once,
    _regen_bg,
    _record_edit_bg
)

router = APIRouter()

AUTO_COLOR_STRENGTH = 0.7
AUTO_COLOR_MASK_FEATHER = 0.003
_AUTO_COLOR_WORKFLOWS = ('纯效果图', '地板替换', '宠物友好', '参照模式', 'Omakase')


# ============================================================
# 生成 worker（移植自 webui._generate_one_model / _run_job，删去所有 UI 调用）
# ============================================================


# ── 任务：提交 / 列表 / 详情 / SSE / 取消 / 重试 ──
@router.post('/api/jobs')
async def create_job(req: JobSubmitRequest):
    return job_submissions.submit('job', req, _prepare_job, _run_job_bg)


def _prepare_job(req):
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
    return job


@router.post('/api/jobs/free')
async def create_free_job(req: FreeJobSubmitRequest):
    return job_submissions.submit('free', req, _prepare_free_job, _run_free_job_bg)


def _prepare_free_job(req):
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
    return job


@router.get('/api/job-submissions/{submission_id}')
def get_submission(submission_id: UUID, store_id: UUID):
    if submission_id.version != 4 or store_id.version != 4:
        raise HTTPException(422, '提交标识必须为 UUID v4')
    return job_submissions.lookup(str(submission_id), str(store_id))


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
            if state.job_is_terminal(job):
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
    with state.JOBS.locked() as entries:
        job = entries.get(jid)
        if not job:
            raise HTTPException(404, 'job not found')
        if state.job_is_active(job):
            raise HTTPException(409, '任务仍在运行，请先停止并等待结束后再清除')
        entries.pop(jid)
        state.JOBS.clear_cancelled(jid)
        removed = 1
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
        for key in job.model_targets if key != 'sd35'
    )
    sd_run = job.model_runs.get('sd35')
    if sd_run and not model_run_current_path(job, 'sd35'):
        phase = 'upscale' if os.path.isfile(str(sd_run.get('base_path') or '')) else 'sd'
        action = require_retry_confirmation(sd_run, phase, bool(req and req.confirm_possible_duplicate_charge))
        if action == 'confirm':
            settings = dict(sd_run.get('settings') or {})
            settings.pop('upscale_queue' if phase == 'upscale' else 'sd_queue', None)
            update_model_run(job, 'sd35', settings=settings)
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
    state.claim_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='',
               operation='retry', operation_status='running', operation_error='')
    state.spawn(_retry_bg(job))
    return job_view(job)


@router.post('/api/jobs/{jid}/sd-upscale')
async def retry_sd_upscale(jid: str, req: RetryJobRequest | None = None):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    ensure_model_runs(job)
    run = job.model_runs.get('sd35') or {}
    base_path = run.get('base_path') or ''
    action = require_retry_confirmation(run, 'upscale', bool(req and req.confirm_possible_duplicate_charge))
    if run.get('delivery_status') != 'upscale_failed' or (action != 'resume' and not os.path.isfile(base_path)):
        raise HTTPException(400, '没有可重试的 SD 基础图')
    if job.operation_status == 'running':
        raise HTTPException(409, '任务进行中')
    if not (load_config().get('fal_api_key') or '').strip():
        raise HTTPException(400, '重试 SD 超分需要 Fal API Key')
    if action == 'confirm':
        settings = dict(run.get('settings') or {})
        settings.pop('upscale_queue', None)
        update_model_run(job, 'sd35', settings=settings)
    state.claim_job(job, status='running', started_at=time.time(), operation='sd_upscale',
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
    if state.job_is_active(job):
        raise HTTPException(409, '任务正在处理，请稍后再试')
    api_key = load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    # 回执前预置 active 状态（镜像 _edit_bg 开场），前端据此即刻开 SSE
    state.claim_job(job, started_at=time.time(), pro_polishing=True, pro_stage='',
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
    if state.job_is_active(job):
        raise HTTPException(409, '任务正在处理，请稍后再试')
    api_key = (req.api_key or '').strip() or load_config().get('gemini_api_key', '').strip()
    if not api_key:
        raise HTTPException(400, '缺少 API Key')
    model_id = GEMINI_MODEL_MAP.get(req.model_choice, GEMINI_MODEL_MAP['Nano Banana Pro'])
    # 回执前预置 active 状态（镜像 _edit_bg 开场），前端据此即刻开 SSE
    state.claim_job(job, started_at=time.time(), pro_polishing=True, pro_stage='',
               operation='edit', operation_status='running', operation_error='')
    state.spawn(_edit_bg(
        job, api_key=api_key, instruction=req.instruction, model_id=model_id,
        model_label=f'{req.model_choice} 二改', preserve=req.preserve_floor_geometry,
        image_size=req.image_size, color_match=req.color_match))
    return job_view(job)

# ============================================================
# STEP 2.5 迁移补齐：重抽/多抽、记录内二改、导出、记录管理、识色、用量
# ============================================================


# ── 重抽/多抽 ──
@router.post('/api/jobs/{jid}/regen')
async def regen_job(jid: str, n: int = Query(default=1, ge=1, le=6)):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, 'job not found')
    if not job.retry_ctx:
        raise HTTPException(400, '该任务缺少重抽上下文(可能重启后丢失)，请重新提交')
    if state.job_is_active(job):
        raise HTTPException(409, '任务进行中，请稍后再抽')
    # 回执前预置 active 状态（镜像 _regen_bg 开场），前端据此即刻开 SSE
    state.claim_job(job, status='running', started_at=time.time(), error='',
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
    state.admit_job(job)   # 顺手收口最旧的终态卡（新建的二改 job 是 queued/in-flight，不会被删）
    model_id = GEMINI_MODEL_MAP.get(req.model_choice, GEMINI_MODEL_MAP['Nano Banana Pro'])
    state.spawn(_record_edit_bg(
        job, src_pil=src_pil, api_key=api_key, instruction=req.instruction, model_id=model_id,
        model_label=f'{req.model_choice} 二改', image_size=req.image_size,
        preserve=req.preserve_floor_geometry, json_path=json_path,
        record_id=req.record_id, source_ref=req.result_id, color_match=req.color_match))
    return job_view(job)
