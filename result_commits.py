"""Recoverable, idempotent local image -> record -> job commits. No provider calls."""
import json
import os
import re
import tempfile
import uuid

from fastapi import HTTPException
from PIL import Image, PngImagePlugin

from . import records, server_state as state
from .models import add_model_candidate, compute_runs_final_status, update_model_run


def _paths(commit_id):
    if not re.fullmatch(r'[0-9a-f]{32}', str(commit_id)):
        raise HTTPException(404, '提交不存在')
    root = os.path.realpath(records.MAIN_OUTPUT_DIR)
    folder = os.path.join(root, '.result_commits')
    if os.path.commonpath([root, os.path.realpath(folder)]) != root:
        raise HTTPException(409, '提交目录无效')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f'{commit_id}.json'), os.path.join(root, f'result_{commit_id}.png')


def _write(path, value):
    fd, tmp = tempfile.mkstemp(prefix='.commit_', suffix='.tmp', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def claim_target(jid, stage, source_path, operation, *, commit_id=None):
    job = state.JOBS.get(jid)
    if not job:
        raise HTTPException(404, '任务卡已被清除')
    def validate(current):
        paths = ((current.model_runs or {}).get(stage) or {}).get('paths') or []
        if source_path and os.path.normcase(os.path.realpath(source_path)) not in {
                os.path.normcase(os.path.realpath(p)) for p in paths}:
            raise HTTPException(400, '该图不属于此任务的候选')
    state.claim_job(job, _validate=validate, _resume_commit_id=commit_id,
                    operation=operation, operation_id=commit_id or uuid.uuid4().hex,
                    operation_status='running', operation_error='', _local_commit_active=True)
    return job


def fail_operation(job, error):
    if job is None:
        return
    with state.JOBS.locked():
        job.operation_status = 'failed'
        job.operation_error = str(error.detail.get('message') if isinstance(error, HTTPException) and isinstance(error.detail, dict) else error)
        job._local_commit_active = False
    try:
        state.JOBS.persist()
    except Exception:
        # The journal and deterministic PNG still provide recovery on restart.
        pass


def commit_image(image, *, label, job=None, stage='', json_path='', record_id='',
                 source_result_id=None, metadata=None, edit_prompt='', commit_id=None, source_path='', run_updates=None, finish_operation=True):
    cid = commit_id or (job.operation_id if job else '') or uuid.uuid4().hex
    manifest, image_path = _paths(cid)
    with records.record_file_lock(manifest):
        payload = {'version': 1, 'commit_id': cid, 'status': 'prepared',
                   'job_id': job.job_id if job else '', 'stage': stage,
                   'json_path': job.json_path if job else json_path,
                   'record_id': job.record_id if job else record_id,
                   'source_result_id': source_result_id, 'source_path': source_path, 'label': label,
                   'metadata': dict(metadata or {}), 'edit_prompt': edit_prompt,
                   'run_updates': dict(run_updates or {}), 'finish_operation': finish_operation,
                   'result_image_file': os.path.basename(image_path)}
        try:
            if os.path.exists(manifest) and os.path.isfile(image_path):
                return _finish(manifest, image_path, job)
            _write(manifest, payload)
            fd, tmp = tempfile.mkstemp(prefix='.result_', suffix='.png', dir=records.MAIN_OUTPUT_DIR)
            os.close(fd)
            try:
                info = PngImagePlugin.PngInfo()
                info.add_text('floor_engine', json.dumps(payload['metadata'], ensure_ascii=False))
                image.convert('RGB').save(tmp, 'PNG', pnginfo=info, optimize=True)
                os.replace(tmp, image_path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return _finish(manifest, image_path, job)
        except Exception as error:
            _raise_pending(payload, image_path, job, error)


def _raise_pending(payload, image_path, job, error):
    cid = payload['commit_id']
    manifest, _ = _paths(cid)
    phase = 'image'
    try:
        with open(manifest, encoding='utf-8') as stream:
            current = json.load(stream)
        phase = 'record' if current.get('status') == 'prepared' else 'job'
        current['failed_stage'] = phase
        _write(manifest, current)
    except (OSError, ValueError):
        pass
    if job and job.operation_id == cid:
        job.pending_result_commit = cid if os.path.isfile(image_path) else ''
        fail_operation(job, error)
    from .server_helpers import to_url
    raise HTTPException(503, {'code': 'result_commit_pending', 'commit_id': cid, 'failure_stage': phase,
        'message': '图片已保留，记录或任务写入未完成；可恢复本地写入，不会再次调用模型'
                   if os.path.isfile(image_path) else '本地结果尚未保存成功，请重试应用候选',
        'image_saved': os.path.isfile(image_path), 'result_url': to_url(image_path) if os.path.isfile(image_path) else ''}) from error


def _finish(manifest, image_path, job):
    with open(manifest, encoding='utf-8') as stream:
        payload = json.load(stream)
    if payload.get('json_path') and payload.get('status') != 'done':
        from .server_helpers import require_record_json_path
        path = require_record_json_path(payload['json_path'])
        # All-local commit: serialize record deletion through the final journal
        # receipt. Lock order is journal -> record -> brief job-state sections;
        # no asset-lifecycle lock or network call is acquired here.
        with records.record_file_lock(path):
            return _finish_locked(manifest, image_path, job)
    return _finish_locked(manifest, image_path, job)


def _finish_locked(manifest, image_path, job):
    with open(manifest, encoding='utf-8') as stream:
        payload = json.load(stream)
    cid = payload['commit_id']
    if not os.path.isfile(image_path):
        raise HTTPException(410, '已保存图片不存在，请重新应用原候选')
    if payload['status'] != 'done':
        if payload.get('job_id'):
            with state.JOBS.locked():
                if state.JOBS.get(payload['job_id']) is not job or job.operation_id != cid:
                    raise HTTPException(409, '目标任务或操作已变化')
                source = payload.get('source_path')
                if source and source not in (((job.model_runs or {}).get(payload['stage']) or {}).get('paths') or []):
                    raise HTTPException(409, '源候选已变化')
        rid = payload.get('result_id')
        if payload.get('json_path') and payload.get('record_id'):
            from .server_helpers import require_record_json_path
            payload['json_path'] = require_record_json_path(payload['json_path'])
            if payload.get('status') == 'record_written' and rid:
                found = any(item.get('result_id') == rid for row in records.load_records_file(payload['json_path'])
                            if row.get('id') == payload['record_id'] for item in row.get('results', []))
                if not found:
                    raise HTTPException(409, '已写入的结果后来被删除，不能重新创建')
            with Image.open(image_path) as image:
                rid = records.api_write_to_record(image, payload['label'], payload['json_path'],
                    payload['record_id'], image_path, payload.get('metadata'), payload.get('source_result_id'),
                    commit_id=cid, edit_prompt=payload.get('edit_prompt', ''), require_source=bool(payload.get('source_result_id')))
            if not rid:
                raise OSError('结果记录写入失败')
        elif not job:
            raise OSError('结果缺少目标记录')
        payload.update(status='record_written', result_id=rid)
        _write(manifest, payload)
        if job:
            with state.JOBS.locked():
                if state.JOBS.get(job.job_id) is not job or job.operation_id != cid:
                    raise HTTPException(409, '目标任务或操作已变化')
                paths = ((job.model_runs or {}).get(payload['stage']) or {}).get('paths') or []
                if image_path not in paths:
                    add_model_candidate(job, payload['stage'], image_path, {'commit_id': cid})
                job.status = compute_runs_final_status(job)
                if payload.get('run_updates'):
                    update_model_run(job, payload['stage'], **payload['run_updates'])
                    job.status = compute_runs_final_status(job)
                if payload.get('finish_operation', True) or job.operation == 'result_commit':
                    job.operation_status = 'done'
                    job.operation_error = ''
                    job.operation_failure_code = ''
                    job.operation_retry_safety = 'safe'
                    job.operation_may_have_been_billed = False
                    job.operation_attempts = []
                job.pending_result_commit = ''
            state.JOBS.persist()
        # Completed receipts do not keep deleted images alive in orphan scans.
        payload = {k: payload.get(k) for k in ('version', 'commit_id', 'job_id', 'stage', 'result_id')}
        payload['status'] = 'done'
        _write(manifest, payload)
        if job and job.operation_id == cid:
            job._local_commit_active = False
    from .server_helpers import job_view, to_url
    return {'ok': True, 'commit_id': cid, 'result_id': payload.get('result_id'),
            'result_url': to_url(image_path), 'url': to_url(image_path),
            **({'job': job_view(job)} if job else {})}


def retry_commit(commit_id):
    manifest, image_path = _paths(commit_id)
    with records.record_file_lock(manifest):
        if not os.path.isfile(manifest):
            raise HTTPException(404, '提交不存在')
        with open(manifest, encoding='utf-8') as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict) or payload.get('commit_id') != commit_id:
            raise HTTPException(409, '提交日志无效，已保存图片不会被覆盖')
        job = state.JOBS.get(payload.get('job_id')) if payload.get('job_id') else None
        if payload['status'] != 'done' and payload.get('job_id'):
            job = claim_target(payload['job_id'], payload['stage'], None, 'result_commit', commit_id=commit_id)
        try:
            return _finish(manifest, image_path, job)
        except Exception as error:
            _raise_pending(payload, image_path, job, error)


def restore_pending_jobs():
    """Reattach journals after a crash, without automatically executing them."""
    folder = os.path.join(records.MAIN_OUTPUT_DIR, '.result_commits')
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if not re.fullmatch(r'[0-9a-f]{32}\.json', name):
            continue
        try:
            with open(os.path.join(folder, name), encoding='utf-8') as stream:
                row = json.load(stream)
            if not isinstance(row, dict) or row.get('commit_id') != name[:-5]:
                continue
            job = state.JOBS.get(row.get('job_id'))
            image_path = os.path.join(records.MAIN_OUTPUT_DIR, f"result_{row.get('commit_id')}.png")
            if job and row.get('status') != 'done' and os.path.isfile(image_path):
                job.pending_result_commit = row['commit_id']
                job.operation_status = 'failed'
                job.operation_error = '本地结果写入已中断，可恢复写入'
        except (OSError, ValueError, TypeError):
            continue


def commit_generated_image(image, *, job, label, stage, metadata=None, run_updates=None):
    """A worker already owns the job; journal the paid image without a new claim."""
    with state.JOBS.locked():
        job.operation_id = uuid.uuid4().hex
        job._local_commit_active = True
    try:
        return commit_image(image, label=label, job=job, stage=stage, metadata=metadata,
                            run_updates=run_updates, finish_operation=False)
    except Exception as error:
        fail_operation(job, error)
        raise


def list_pending_commits():
    from .server_helpers import to_url
    folder = os.path.join(records.MAIN_OUTPUT_DIR, '.result_commits')
    if not os.path.isdir(folder):
        return []
    pending = []
    for name in sorted(os.listdir(folder)):
        if not re.fullmatch(r'[0-9a-f]{32}\.json', name):
            continue
        try:
            lock = records.record_file_lock(os.path.join(folder, name))
            if not lock.acquire(blocking=False):
                continue
            try:
                with open(os.path.join(folder, name), encoding='utf-8') as stream:
                    row = json.load(stream)
            finally:
                lock.release()
            if not isinstance(row, dict) or row.get('commit_id') != name[:-5]:
                continue
            image_path = os.path.join(records.MAIN_OUTPUT_DIR, f"result_{name[:-5]}.png")
            if row.get('status') == 'done' or not os.path.isfile(image_path):
                continue
            # Don't expose in-progress transactions as failures.
            job = state.JOBS.get(row.get('job_id')) if row.get('job_id') else None
            if job and state.job_is_active(job):
                continue
            pending.append({'commit_id': name[:-5], 'result_url': to_url(image_path),
                            'label': str(row.get('label') or '本地结果'),
                            'can_restore': not row.get('job_id') or job is not None})
        except (OSError, ValueError, TypeError):
            continue
    return pending
