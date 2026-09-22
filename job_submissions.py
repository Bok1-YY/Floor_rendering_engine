"""Single-worker admission boundary: persist intent, persist queue, consume, spawn.

Admission is deliberately synchronous and bounded (no network / await): HTTP
cancellation cannot interrupt the persistence-to-dispatch decision. The shared
receipt lock also serializes direct callers on different threads. Model work is
owned by the existing BackgroundTasks supervisor, never by the HTTP request.
"""
import uuid

from fastapi import HTTPException

from . import server_state as state, submission_store as receipts
from .models import job_is_active
from .server_helpers import job_view


def checked_read(submission_id):
    entry = receipts.read(submission_id)
    if entry is None and any(job.submission_id == submission_id for job in state.JOBS.snapshot()):
        raise receipts.unavailable()
    if entry:
        job = state.JOBS.get(entry['job_id'])
        if job and job.submission_id != submission_id:
            raise receipts.unavailable()
    return entry


def lookup(submission_id, store_id):
    with receipts.LOCK:
        receipts.require_store(store_id)
        entry = checked_read(submission_id)
        if entry is None:
            return {'status': 'not_found', 'submission_id': submission_id, 'can_continue': True}
        job = state.JOBS.get(entry['job_id'])
        status = 'prepared' if entry['phase'] == 'prepared' else 'accepted' if job else 'job_unavailable'
        return {'status': status, 'submission_id': submission_id, 'job_id': entry['job_id'],
                'created_at': entry['created_at'], 'can_continue': status == 'prepared',
                'job': job_view(job) if job and status == 'accepted' else None}


def submit(kind, request, prepare, worker):
    with receipts.LOCK:
        store_id = receipts.identity()
        if request.submission_store_id:
            receipts.require_store(str(request.submission_store_id))
        submission_id = str(request.submission_id or uuid.uuid4())
        fingerprint = receipts.digest(kind, request)
        entry = checked_read(submission_id)
        if entry:
            if entry['kind'] != kind or entry['digest'] != fingerprint:
                raise HTTPException(409, {'code': 'submission_conflict',
                                         'message': '同一提交标识对应的内容不同，请恢复原提交或明确新建任务'})
            if entry['phase'] == 'dispatch_committed':
                job = state.JOBS.get(entry['job_id'])
                if job:
                    return job_view(job)
                raise HTTPException(410, {'code': 'submission_job_unavailable',
                                         'job_id': entry['job_id'],
                                         'message': '此提交已受理，任务卡已不可用；请核对历史记录'})
        state.require_accepting()
        if sum(job_is_active(job) for job in state.JOBS.snapshot()) >= 60:
            raise HTTPException(429, {'code': 'queue_full', 'message': '任务队列已满，请稍后再试'},
                                headers={'Retry-After': '5'})
        # Validation and path normalization must happen after fingerprint/replay.
        job = prepare(request)
        if entry:
            job.job_id = entry['job_id']
        job.submission_id = submission_id
        if entry is None:
            entry = receipts.prepared(submission_id, store_id, kind, fingerprint, job.job_id)
            receipts.write(entry)
        state.admit_job(job)
        try:
            receipts.write({**entry, 'phase': 'dispatch_committed'})
        except Exception:
            # A replace may have succeeded before an IO error was observed.
            # Never schedule here, even if a read-back would show committed.
            job.status = 'failed'
            job.error = '提交凭据保存结果未确认，未启动生成；请查询本次提交'
            try:
                state.JOBS.persist()
            except Exception:
                pass
            raise
        coroutine = None
        try:
            coroutine = worker(job, request)
            state.spawn(coroutine)
        except Exception as exc:
            if coroutine is not None:
                coroutine.close()
            job.status = 'failed'
            job.error = '任务调度失败，未启动生成；创建请求不会自动重试'
            try:
                state.JOBS.persist()
            except Exception:
                pass
            raise HTTPException(503, {'code': 'submission_dispatch_failed',
                                     'job_id': job.job_id, 'message': job.error}) from exc
        return job_view(job)


def recover():
    """No worker is started here; discarded cards are never reconstructed."""
    for job in state.JOBS.snapshot():
        if not job.submission_id:
            continue
        try:
            entry = receipts.read(job.submission_id)
            if entry is None:
                # A missing receipt for a persisted identity is corruption,
                # not evidence that the paid operation never existed.
                raise receipts.unavailable()
            if entry['phase'] == 'prepared':
                state.JOBS.pop(job.job_id)
        except HTTPException:
            job.status = 'failed'
            job.error = '提交凭据异常，请核对数据；未自动重新生成'
