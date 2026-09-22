"""Admission failures are exercised before and after the durable dispatch fence."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import job_submissions as service, submission_store as store
from Floor_engine_server import records, routes_jobs, server_state as state, server_helpers
from Floor_engine_server.models import new_job
from Floor_engine_server.server_schemas import FreeJobSubmitRequest, JobSubmitRequest, GenParams
from Floor_engine_server.task_registry import TaskRegistry


@pytest.fixture
def context(tmp_path, monkeypatch):
    registry = TaskRegistry('jobs', max_entries=60, is_terminal=state.job_is_terminal,
                            terminal_history_only=True, on_persist=records.persist_jobs)
    monkeypatch.setattr(state, 'JOBS', registry)
    monkeypatch.setattr(records, 'QUEUE_STATE_FILE', str(tmp_path / 'queue.json'))
    monkeypatch.setattr(server_helpers, 'UPLOAD_DIR', str(tmp_path))
    monkeypatch.setattr(routes_jobs, 'load_config', lambda: {'gemini_api_key': 'secret-test-value'})
    image = tmp_path / 'floor.png'
    Image.new('RGB', (4, 4)).save(image)
    scheduled = []
    def spawn(coro):
        scheduled.append(True)
        coro.close()
    monkeypatch.setattr(state, 'spawn', spawn)
    return image, scheduled


def request(context, kind='free', **overrides):
    image, _ = context
    identity = {'submission_id': uuid.uuid4(), 'submission_store_id': store.identity()}
    if kind == 'free':
        return FreeJobSubmitRequest(**{**identity, 'prompt': 'private prompt',
            'image_paths': [str(image)], 'api_key': 'secret-test-value', **overrides})
    return JobSubmitRequest(**{**identity, 'image_path': str(image),
        'params': GenParams(workflow_mode='纯效果图'), **overrides})


def submit(req):
    endpoint = routes_jobs.create_free_job if isinstance(req, FreeJobSubmitRequest) else routes_jobs.create_job
    return asyncio.run(endpoint(req.model_copy(deep=True)))


@pytest.mark.parametrize('kind', ['job', 'free'])
def test_parallel_replay_schedules_once(context, kind):
    req = request(context, kind)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: submit(req), range(20)))
    assert len({result['job_id'] for result in results}) == 1
    assert len(context[1]) == 1
    found = service.lookup(str(req.submission_id), str(req.submission_store_id))
    assert found['status'] == 'accepted'


def test_conflict_and_new_intent(context):
    req = request(context)
    first = submit(req)
    with pytest.raises(HTTPException) as error:
        submit(req.model_copy(update={'prompt': 'changed'}))
    assert error.value.detail['code'] == 'submission_conflict'
    with pytest.raises(HTTPException) as error:
        submit(request(context, 'job', submission_id=req.submission_id))
    assert error.value.status_code == 409
    assert submit(req.model_copy(update={'submission_id': uuid.uuid4()}))['job_id'] != first['job_id']
    assert len(context[1]) == 2


def test_replay_ignores_changed_configuration_and_missing_input(context, monkeypatch):
    req = request(context)
    first = submit(req)
    context[0].unlink()
    monkeypatch.setattr(routes_jobs, 'load_config', lambda: {})
    state.background.stopping.set()
    assert submit(req)['job_id'] == first['job_id']
    assert len(context[1]) == 1


@pytest.mark.parametrize('removal', ['delete', 'clear', 'trim'])
def test_removed_card_does_not_erase_receipt(context, removal):
    req = request(context)
    result = submit(req)
    state.JOBS.get(result['job_id']).status = 'done'
    if removal == 'delete':
        routes_jobs.delete_job(result['job_id'])
    elif removal == 'clear':
        routes_jobs.clear_completed()
    else:
        for index in range(65):
            job = new_job(str(index), '')
            job.status = 'done'
            state.JOBS.add(job.job_id, job)
    assert service.lookup(str(req.submission_id), str(req.submission_store_id))['status'] == 'job_unavailable'
    with pytest.raises(HTTPException) as error:
        submit(req)
    assert error.value.status_code == 410
    assert len(context[1]) == 1


@pytest.mark.parametrize('failure', ['prepared', 'queue', 'committed', 'after_replace', 'spawn'])
def test_failure_at_every_boundary(context, monkeypatch, failure):
    req = request(context)
    original = store.write
    def writing(entry):
        if failure == 'prepared' and entry['phase'] == 'prepared':
            raise store.unavailable()
        if failure in ('committed', 'after_replace') and entry['phase'] == 'dispatch_committed':
            if failure == 'after_replace':
                original(entry)
            raise store.unavailable()
        original(entry)
    monkeypatch.setattr(store, 'write', writing)
    if failure == 'queue':
        monkeypatch.setattr(state.JOBS, '_on_persist', lambda _: (_ for _ in ()).throw(OSError('disk')))
    if failure == 'spawn':
        monkeypatch.setattr(state, 'spawn', lambda _: (_ for _ in ()).throw(RuntimeError('stopping')))
    with pytest.raises(HTTPException):
        submit(req)
    assert context[1] == []
    entry = store.read(str(req.submission_id))
    if failure in ('after_replace', 'spawn'):
        assert entry['phase'] == 'dispatch_committed'
        submit(req)
        assert context[1] == []
    elif entry:
        assert entry['phase'] == 'prepared'


def test_prepared_can_be_manually_continued_after_restart(context):
    req = request(context)
    job = new_job('pending', '')
    job.submission_id = str(req.submission_id)
    store.write(store.prepared(str(req.submission_id), str(req.submission_store_id), 'free', store.digest('free', req), job.job_id))
    state.admit_job(job)
    state.JOBS.replace((j.job_id, j) for j in records.load_persisted_jobs())
    service.recover()
    assert len(state.JOBS) == 0
    assert context[1] == []
    assert submit(req)['job_id'] == job.job_id
    assert len(context[1]) == 1


def test_corruption_and_store_mismatch_fail_closed(context):
    req = request(context)
    submit(req)
    store.receipt_path(str(req.submission_id)).write_text('{broken', encoding='utf-8')
    with pytest.raises(HTTPException) as error:
        submit(req)
    assert error.value.status_code == 503
    with pytest.raises(HTTPException) as error:
        submit(req.model_copy(update={'submission_store_id': uuid.uuid4()}))
    assert error.value.detail['code'] == 'submission_store_mismatch'
    assert len(context[1]) == 1


def test_receipts_have_no_request_secrets(context):
    req = request(context)
    submit(req)
    raw = store.receipt_path(str(req.submission_id)).read_text(encoding='utf-8')
    assert 'secret-test-value' not in raw
    assert 'private prompt' not in raw
    assert str(context[0]) not in raw
    assert store.digest('free', req) == store.digest('free', req.model_copy(update={'api_key': 'another'}))


def test_missing_receipt_for_resident_job_is_not_treated_as_unsubmitted(context):
    req = request(context)
    submit(req)
    store.receipt_path(str(req.submission_id)).unlink()
    with pytest.raises(HTTPException) as error:
        service.lookup(str(req.submission_id), str(req.submission_store_id))
    assert error.value.status_code == 503
    with pytest.raises(HTTPException):
        submit(req)
    assert len(context[1]) == 1


def test_identity_does_not_reset_when_marker_is_lost(context):
    submit(request(context))
    (store.folder() / 'store.json').unlink()
    assert store.health()['ready'] is False
    with pytest.raises(HTTPException):
        store.identity()


def test_queue_full_and_missing_input_do_not_prepare_or_dispatch(context):
    req = request(context)
    context[0].unlink()
    with pytest.raises(HTTPException):
        submit(req)
    assert store.read(str(req.submission_id)) is None
    for index in range(60):
        job = new_job(str(index), '')
        state.JOBS.add(job.job_id, job)
    with pytest.raises(HTTPException) as error:
        submit(req)
    assert error.value.status_code == 429
    assert store.read(str(req.submission_id)) is None
    assert context[1] == []


def test_http_contract_validation_and_missing_lookup(context):
    from Floor_engine_server.server_api import app
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            health = (await client.get('/api/healthz')).json()
            assert health['submissions']['ready']
            body = request(context).model_dump(mode='json')
            body.pop('submission_store_id')
            assert (await client.post('/api/jobs/free', json=body)).status_code == 422
            result = await client.get(f'/api/job-submissions/{uuid.uuid4()}', params={'store_id': store.identity()})
            assert result.json()['status'] == 'not_found'
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['prepared', 'dispatch_committed'])
def test_real_process_exit_and_restart_never_redispatches(context, tmp_path, phase):
    # Child only writes isolated receipt/queue fixtures; no model worker exists.
    root = Path(__file__).resolve().parents[1]
    script = '''
import importlib.util, sys, os, json
root, output, queue, phase = sys.argv[1:]
spec = importlib.util.spec_from_file_location('Floor_engine_server', root+'/__init__.py', submodule_search_locations=[root])
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
from Floor_engine_server import records, submission_store as store
from Floor_engine_server.models import new_job
import uuid
records.MAIN_OUTPUT_DIR = output; records.QUEUE_STATE_FILE = queue
job = new_job('crash', ''); job.submission_id = str(uuid.uuid4())
entry = store.prepared(job.submission_id, store.identity(), 'free', 'a'*64, job.job_id)
store.write(entry); records.persist_jobs([job])
if phase == 'dispatch_committed': store.write({**entry, 'phase': phase})
os._exit(37)
'''
    result = subprocess.run([sys.executable, '-c', script, str(root), records.MAIN_OUTPUT_DIR,
                             records.QUEUE_STATE_FILE, phase], env={**os.environ, 'FLOOR_DATA_DIR': str(tmp_path / 'child')},
                            capture_output=True, timeout=30)
    assert result.returncode == 37, result.stderr.decode(errors='replace')
    jobs = records.load_persisted_jobs()
    assert jobs and jobs[0].status == 'failed'
    submission_id = jobs[0].submission_id
    state.JOBS.replace((job.job_id, job) for job in jobs)
    service.recover()
    found = service.lookup(submission_id, store.identity())
    assert found['status'] == ('prepared' if phase == 'prepared' else 'accepted')
    assert context[1] == []
