"""Offline behavior regressions for admission, persistence and lifecycle."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from Floor_engine_server import records, server_state as state
from Floor_engine_server.models import new_job
from Floor_engine_server.task_registry import TaskRegistry


def test_active_operation_survives_history_eviction():
    registry = TaskRegistry('test', max_entries=1, is_terminal=state.job_is_terminal)
    job = new_job('editing', '')
    job.status = 'done'
    job.operation_status = 'running'
    registry.add(job.job_id, job)
    registry.add('new', new_job('new', ''))
    assert registry.get(job.job_id) is job


def test_queue_persistence_keeps_old_active_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'QUEUE_STATE_FILE', str(tmp_path / 'queue.json'))
    jobs = [new_job(str(i), '') for i in range(65)]
    for job in jobs[:64]:
        job.status = 'done'
    records.persist_jobs(jobs)
    rows = json.loads((tmp_path / 'queue.json').read_text(encoding='utf-8'))
    assert jobs[-1].job_id in {row['job_id'] for row in rows}
    assert len(rows) == 61


def test_concurrent_persistence_always_leaves_valid_json(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'QUEUE_STATE_FILE', str(tmp_path / 'queue.json'))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: records.persist_jobs([new_job(str(i), '')]), range(40)))
    assert len(json.loads((tmp_path / 'queue.json').read_text(encoding='utf-8'))) == 1
    assert not list(tmp_path.glob('*.tmp'))


def test_persistence_failure_is_observable(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'QUEUE_STATE_FILE', str(tmp_path / 'missing' / 'queue.json'))
    with pytest.raises(OSError):
        records.persist_jobs([new_job('test', '')])


def test_preview_admission_is_bounded_and_recovers_after_completion(monkeypatch):
    from fastapi import HTTPException
    registry = TaskRegistry('previews', max_entries=20, is_terminal=state.preview_is_terminal)
    monkeypatch.setattr(state, 'PREVIEWS', registry)
    for index in range(3):
        state.admit_preview(str(index), {'status': 'running'})
    with pytest.raises(HTTPException) as error:
        state.admit_preview('overflow', {'status': 'running'})
    assert error.value.status_code == 429
    assert error.value.headers['Retry-After'] == '5'
    registry.update_fields('0', status='done')
    state.admit_preview('accepted', {'status': 'running'})
    assert registry.get('accepted') is not None


def test_failed_submission_does_not_leave_active_job(monkeypatch):
    from fastapi import HTTPException
    def fail(_):
        raise OSError('disk unavailable')
    registry = TaskRegistry('jobs', max_entries=60, is_terminal=state.job_is_terminal, on_persist=fail)
    monkeypatch.setattr(state, 'JOBS', registry)
    with pytest.raises(HTTPException) as error:
        state.admit_job(new_job('test', ''))
    assert error.value.status_code == 503
    assert len(registry) == 0


def test_shutdown_keeps_thread_waiter_alive_until_work_finishes():
    import logging
    import threading
    from Floor_engine_server.background_tasks import BackgroundTasks
    async def scenario():
        manager = BackgroundTasks(logging.getLogger('test'))
        release = threading.Event()
        started = threading.Event()
        def worker():
            started.set()
            release.wait(3)
            return 'paid result retained'
        task = manager.spawn(asyncio.to_thread(worker), kind='test', reference='one')
        await asyncio.to_thread(started.wait, 2)
        try:
            assert await manager.shutdown(0) == 1
            assert not task.cancelled()
            assert manager.stopping.is_set()
        finally:
            release.set()
        assert await task == 'paid result retained'
    asyncio.run(scenario())


def test_free_job_http_contract_and_capacity(tmp_path, monkeypatch):
    import httpx
    from PIL import Image
    from Floor_engine_server import server_api, server_helpers, routes_jobs
    registry = TaskRegistry('jobs', max_entries=60, is_terminal=state.job_is_terminal)
    monkeypatch.setattr(state, 'JOBS', registry)
    monkeypatch.setattr(routes_jobs, 'load_config', lambda: {'gemini_api_key': 'fake'})
    monkeypatch.setattr(server_helpers, 'UPLOAD_DIR', str(tmp_path))
    source = tmp_path / 'input.png'
    Image.new('RGB', (4, 4)).save(source)
    calls = []
    def capture(coro):
        calls.append(coro)
        coro.close()
    monkeypatch.setattr(state, 'spawn', capture)
    def post(body):
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server_api.app), base_url='http://testserver') as client:
                return await client.post('/api/jobs/free', json=body)
        return asyncio.run(request())
    body = {'prompt': 'test', 'image_paths': [str(source)], 'model_targets': ['pro']}
    response = post(body)
    assert response.status_code == 200
    assert response.json()['model_targets'] == ['pro']
    assert response.json()['status'] == 'queued'
    assert len(calls) == 1
    for i in range(59):
        job = new_job(str(i), '')
        registry.add(job.job_id, job)
    response = post(body)
    assert response.status_code == 429
    assert response.json()['detail']['code'] == 'queue_full'
    assert len(calls) == 1


def test_interrupted_operation_restores_nonactive_status(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'QUEUE_STATE_FILE', str(tmp_path / 'queue.json'))
    job = new_job('edit', '')
    job.status = 'done'
    job.operation_status = 'running'
    records.persist_jobs([job])
    restored = records.load_persisted_jobs()[0]
    assert restored.operation_status == 'failed'
    assert not state.job_is_active(restored)


@pytest.mark.parametrize('cancel_after_generation', [False, True])
def test_full_generation_saves_paid_result_and_retry_does_not_regenerate(swatch_image, tmp_path, monkeypatch, cancel_after_generation):
    from PIL import Image
    from Floor_engine_server import job_service
    from Floor_engine_server.server_schemas import JobSubmitRequest, GenParams
    monkeypatch.setattr(records, 'QUEUE_STATE_FILE', str(tmp_path / 'queue.json'))
    registry = TaskRegistry('jobs', max_entries=60, is_terminal=state.job_is_terminal, on_persist=records.persist_jobs)
    monkeypatch.setattr(state, 'JOBS', registry)
    monkeypatch.setattr(job_service, 'load_config', lambda: {'gemini_api_key': 'fake'})
    monkeypatch.setattr(job_service, 'get_auto_color_match_enabled', lambda: False)
    monkeypatch.setattr(job_service, 'record_usage', lambda *a, **k: None)
    job = new_job('end to end', '', 'b2')
    job.model_targets = ['b2']
    job.workflow_mode = '纯效果图 (生成全新空间)'
    registry.add(job.job_id, job)
    calls = []
    def generate(*args, **kwargs):
        calls.append(args)
        if cancel_after_generation:
            registry.request_cancel(job.job_id)
        return Image.new('RGB', (64, 64), 'tan'), None, 'google'
    monkeypatch.setattr(job_service, 'call_image_generate', generate)
    req = JobSubmitRequest(image_path=swatch_image, model_targets=['b2'], params=GenParams(workflow_mode=job.workflow_mode, cinematic_enabled=False))
    async def scenario():
        state.init_runtime(1)
        await job_service._run_job_bg(job, req)
        assert job.b2_path and __import__('os').path.isfile(job.b2_path)
        rows = records.load_records_file(job.json_path)
        assert any(row.get('results') for row in rows)
        await job_service._retry_bg(job)
    asyncio.run(scenario())
    assert len(calls) == 1
    assert records.load_persisted_jobs()[0].b2_path == job.b2_path


def test_persistence_snapshot_is_detached_from_live_entries():
    value = {'status': 'running', 'nested': {'paths': ['original']}}
    saved = []
    def persist(snapshot):
        value['nested']['paths'].append('later')
        saved.extend(snapshot)
    registry = TaskRegistry('test', max_entries=20, is_terminal=lambda _: False, on_persist=persist)
    registry.add('one', value)
    registry.persist()
    assert saved[0]['nested']['paths'] == ['original']
