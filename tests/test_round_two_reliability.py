"""Offline regression tests for staged billing and recoverable local commits."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import job_service, routes_jobs, server_state as state, usage_stats
from Floor_engine_server.models import new_job, update_model_run
from Floor_engine_server.providers.types import ProviderError
from Floor_engine_server.task_registry import TaskRegistry


@pytest.fixture
def sd_job(tmp_path, monkeypatch):
    registry = TaskRegistry('jobs', max_entries=60, is_terminal=state.job_is_terminal)
    monkeypatch.setattr(state, 'JOBS', registry)
    monkeypatch.setattr(usage_stats, '_USAGE_STATS_FILE', str(tmp_path / 'usage.json'))
    job = new_job('SD regression', '', 'sd35')
    job.model_targets = ['sd35']
    job.workflow_mode = 'test'
    job.retry_ctx = {'ims': '2K'}
    registry.add(job.job_id, job)
    return job


def ambiguous():
    return ProviderError('response lost', failure_code='fal_queue_submit_unknown',
                         retry_safety='ambiguous', may_have_been_billed=True,
                         attempts=[{'phase': 'submit', 'outcome': 'unknown'}])


def test_sd_failure_keeps_billing_metadata(sd_job, monkeypatch):
    monkeypatch.setattr(job_service, 'call_fal_sd35_generate', lambda *a, **k: (None, ambiguous(), None))
    async def run():
        state.init_runtime(1)
        await job_service._generate_sd35_model(sd_job, fal_key='fake', positive='x', negative='',
            pnp='unused.png', ims='2K', ar='4:3', jpt='', rid='', options={}, should_cancel=lambda: False)
    asyncio.run(run())
    run = sd_job.model_runs['sd35']
    assert run['retry_safety'] == 'ambiguous'
    assert run['may_have_been_billed'] is True
    assert run['attempts'] == ambiguous().attempts
    assert usage_stats.load_usage_summary()['totals']['uncertain'] == 1


def test_upscale_without_handle_requires_confirmation(sd_job, tmp_path, monkeypatch):
    base = tmp_path / 'base.png'
    Image.new('RGB', (8, 8)).save(base)
    sd_job.status = 'partial'
    update_model_run(sd_job, 'sd35', base_path=str(base), delivery_status='upscale_failed',
                     retry_safety='ambiguous', may_have_been_billed=True)
    monkeypatch.setattr(routes_jobs, 'load_config', lambda: {'fal_api_key': 'fake'})
    scheduled = []
    monkeypatch.setattr(state, 'spawn', lambda coro: (scheduled.append(True), coro.close()))
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes_jobs.retry_sd_upscale(sd_job.job_id))
    assert error.value.status_code == 409
    assert not scheduled


def test_usage_request_reconciliation_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / 'usage.json'
    monkeypatch.setattr(usage_stats, '_USAGE_STATS_FILE', str(path))
    usage_stats.record_usage('test', 'AuraSR', 'fal', 'uncertain', 'upscale', request_id='one')
    usage_stats.record_usage('test', 'AuraSR', 'fal', 'uncertain', 'upscale', request_id='one')
    usage_stats.record_usage('test', 'AuraSR', 'fal', True, 'upscale', request_id='one')
    usage_stats.record_usage('test', 'AuraSR', 'fal', True, 'upscale', request_id='one')
    counts = json.loads(path.read_text(encoding='utf-8'))['counts']['test']['upscale']['AuraSR']['fal']
    assert counts == {'ok': 1, 'fail': 0, 'uncertain': 0}


@pytest.fixture
def commit_target(tmp_path, monkeypatch):
    from Floor_engine_server import records, server_helpers, result_commits
    output = tmp_path / 'output'
    output.mkdir()
    monkeypatch.setattr(records, 'MAIN_OUTPUT_DIR', str(output))
    monkeypatch.setattr(server_helpers, 'MAIN_OUTPUT_DIR', str(output))
    registry = TaskRegistry('jobs', max_entries=60, is_terminal=state.job_is_terminal)
    monkeypatch.setattr(state, 'JOBS', registry)
    source = output / 'source.png'
    Image.new('RGB', (8, 8), 'red').save(source)
    record = output / 'oak_记录.json'
    record.write_text(json.dumps([{'id': 'r', 'results': [{'result_id': 'src', 'result_image_file': 'source.png'}]}]), encoding='utf-8')
    job = new_job('local commit', '', 'b2')
    job.status = 'done'
    job.json_path = str(record)
    job.record_id = 'r'
    update_model_run(job, 'b2', paths=[str(source)], status='done')
    registry.add(job.job_id, job)
    return job, source, record


def test_failed_record_commit_preserves_image_and_can_resume_once(commit_target, monkeypatch):
    from Floor_engine_server import records, result_commits, storage_maintenance
    job, source, record = commit_target
    result_commits.claim_target(job.job_id, 'b2', str(source), 'inpaint_apply')
    original = records.api_write_to_record
    monkeypatch.setattr(records, 'api_write_to_record', lambda *a, **k: None)
    with pytest.raises(HTTPException) as error:
        result_commits.commit_image(Image.new('RGB', (8, 8), 'blue'), label='edit', job=job, stage='b2', source_path=str(source))
    detail = error.value.detail
    assert error.value.status_code == 503 and detail['image_saved'] is True
    assert job.pending_result_commit == detail['commit_id']
    assert not state.job_is_active(job)
    assert len(job.model_runs['b2']['paths']) == 1
    _, image_path = result_commits._paths(detail['commit_id'])
    assert image_path not in storage_maintenance._current_orphan_paths(records.MAIN_OUTPUT_DIR)
    monkeypatch.setattr(records, 'api_write_to_record', original)
    first = result_commits.retry_commit(detail['commit_id'])
    second = result_commits.retry_commit(detail['commit_id'])
    assert first['result_id'] == second['result_id']
    assert len(records.load_records_file(str(record))[0]['results']) == 2
    assert len(job.model_runs['b2']['paths']) == 2
    assert not job.pending_result_commit
    assert not state.job_is_active(job)


def test_task_remains_exclusive_through_record_io(commit_target, monkeypatch):
    from Floor_engine_server import records, result_commits
    job, source, _ = commit_target
    result_commits.claim_target(job.job_id, 'b2', str(source), 'floor_visualize')
    original = records.api_write_to_record
    def checked_write(*args, **kwargs):
        with pytest.raises(HTTPException) as error:
            routes_jobs.delete_job(job.job_id)
        assert error.value.status_code == 409
        with pytest.raises(HTTPException):
            state.claim_job(job, operation_status='running')
        return original(*args, **kwargs)
    monkeypatch.setattr(records, 'api_write_to_record', checked_write)
    result_commits.commit_image(Image.new('RGB', (8, 8)), label='edit', job=job, stage='b2')
    assert job.operation_status == 'done' and not state.job_is_active(job)


def test_record_success_then_queue_failure_does_not_duplicate_record(commit_target, monkeypatch):
    from Floor_engine_server import records, result_commits
    job, source, record = commit_target
    result_commits.claim_target(job.job_id, 'b2', str(source), 'color_match')
    original = state.JOBS.persist
    monkeypatch.setattr(state.JOBS, 'persist', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(HTTPException) as error:
        result_commits.commit_image(Image.new('RGB', (8, 8)), label='edit', job=job, stage='b2')
    assert len(records.load_records_file(str(record))[0]['results']) == 2
    monkeypatch.setattr(state.JOBS, 'persist', original)
    result_commits.retry_commit(error.value.detail['commit_id'])
    assert len(records.load_records_file(str(record))[0]['results']) == 2
    assert len(job.model_runs['b2']['paths']) == 2


def test_resume_upscale_uses_handle_and_reconciles_usage(sd_job, tmp_path, monkeypatch):
    from Floor_engine_server import records
    base = tmp_path / 'base.png'
    Image.new('RGB', (8, 8)).save(base)
    sd_job.status = 'partial'
    update_model_run(sd_job, 'sd35', base_path=str(base), delivery_status='upscale_failed')
    monkeypatch.setattr(job_service, 'load_config', lambda: {'fal_api_key': 'fake'})
    handle = {'request_id': 'fal-one', 'endpoint': 'fal-ai/aura-sr',
              'status_url': 'https://queue.fal.run/aura/fal-one/status', 'response_url': 'https://queue.fal.run/aura/fal-one'}
    calls = []
    def upscale(*args, **kwargs):
        calls.append(kwargs['queue_handle'])
        if len(calls) == 1:
            kwargs['on_queue_submitted'](handle)
            return None, ambiguous()
        assert kwargs['queue_handle'] == handle
        return Image.new('RGB', (16, 16)), None
    monkeypatch.setattr(job_service, 'call_fal_aura_upscale', upscale)
    async def run():
        state.init_runtime(1)
        await job_service._retry_sd_upscale_bg(sd_job)
        assert sd_job.model_runs['sd35']['retry_safety'] == 'ambiguous'
        await job_service._retry_sd_upscale_bg(sd_job)
    asyncio.run(run())
    totals = usage_stats.load_usage_summary()['totals']
    assert len(calls) == 2 and calls[0] == {}
    assert totals['ok'] == 1 and totals['uncertain'] == 0
    assert sd_job.model_runs['sd35']['delivery_status'] == 'upscaled'
    assert sd_job.operation_retry_safety == 'safe'
    assert sd_job.model_runs['sd35']['attempts'] == ambiguous().attempts



def test_failed_claim_releases_transient_operation_lock(commit_target, monkeypatch):
    from Floor_engine_server import result_commits
    job, source, _ = commit_target
    monkeypatch.setattr(state.JOBS, 'persist', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(HTTPException) as error:
        result_commits.claim_target(job.job_id, 'b2', str(source), 'floor_visualize')
    assert error.value.status_code == 503
    assert not state.job_is_active(job)
    assert job.operation_id == ''


def test_missing_source_record_is_not_recreated(commit_target):
    from Floor_engine_server import records, result_commits
    _, _, record = commit_target
    record.write_text('[]', encoding='utf-8')
    with pytest.raises(HTTPException):
        result_commits.commit_image(Image.new('RGB', (8, 8)), label='edit', json_path=str(record),
                                    record_id='r', source_result_id='src')
    assert records.load_records_file(str(record)) == []
    assert len(result_commits.list_pending_commits()) == 1


def test_recovery_rejects_path_traversal():
    from Floor_engine_server import result_commits
    with pytest.raises(HTTPException) as error:
        result_commits.retry_commit('../queue_state')
    assert error.value.status_code == 404


def test_old_usage_counts_survive_new_request_ledger(tmp_path, monkeypatch):
    path = tmp_path / 'usage.json'
    old = {'version': 2, 'counts': {'test': {'upscale': {'AuraSR': {'fal': {'ok': 4, 'fail': 2, 'uncertain': 3}}}}}}
    path.write_text(json.dumps(old), encoding='utf-8')
    monkeypatch.setattr(usage_stats, '_USAGE_STATS_FILE', str(path))
    usage_stats.record_usage('test', 'AuraSR', 'fal', True, 'upscale', request_id='new')
    value = json.loads(path.read_text(encoding='utf-8'))['counts']['test']['upscale']['AuraSR']['fal']
    assert value == {'ok': 5, 'fail': 2, 'uncertain': 3}



def test_queue_poll_failure_never_resubmits(monkeypatch):
    from Floor_engine_server.providers import fal
    calls = []
    class Response:
        status_code = 503
        text = 'unavailable'
        def json(self): return {'detail': 'unavailable'}
    class Session:
        trust_env = False
        def post(self, *args, **kwargs):
            raise AssertionError('must not submit while recovering')
        def get(self, url, **kwargs):
            calls.append(url)
            return Response()
    monkeypatch.setattr(fal._req, 'Session', Session)
    monkeypatch.setattr(fal, 'load_config', lambda: {})
    value, error = fal._call_fal_queue_json('fake', 'fal-ai/aura-sr', {}, resume_handle={
        'request_id': 'one', 'status_url': 'https://queue.fal.run/aura/one/status',
        'response_url': 'https://queue.fal.run/aura/one'})
    assert value is None and error.retry_safety == 'ambiguous'
    assert len(calls) == 1


def test_upscale_recovery_does_not_require_new_confirmation_or_base_file(sd_job, monkeypatch):
    handle = {'request_id': 'one', 'endpoint': 'fal-ai/aura-sr',
              'status_url': 'https://queue.fal.run/aura/one/status', 'response_url': 'https://queue.fal.run/aura/one'}
    sd_job.status = 'partial'
    update_model_run(sd_job, 'sd35', delivery_status='upscale_failed', retry_safety='ambiguous',
                     base_path='missing.png', settings={'upscale_queue': handle})
    monkeypatch.setattr(routes_jobs, 'load_config', lambda: {'fal_api_key': 'fake'})
    scheduled = []
    monkeypatch.setattr(state, 'spawn', lambda coro: (scheduled.append(True), coro.close()))
    response = asyncio.run(routes_jobs.retry_sd_upscale(sd_job.job_id))
    assert response['status'] == 'running' and scheduled == [True]
    assert sd_job.model_runs['sd35']['settings']['upscale_queue'] == handle


def test_sd_resume_does_not_reencode_missing_source(monkeypatch):
    from Floor_engine_server.providers import fal
    calls = []
    monkeypatch.setattr(fal, '_file_to_data_uri', lambda *_: (_ for _ in ()).throw(AssertionError('must not read input')))
    def queue(*args, **kwargs):
        calls.append(kwargs['resume_handle'])
        assert args[2] == {}
        return {'images': [{'url': 'fake'}]}, None
    monkeypatch.setattr(fal, '_call_fal_queue_json', queue)
    monkeypatch.setattr(fal, '_fal_image_from_result', lambda *a, **k: (Image.new('RGB', (4, 4)), None))
    image, error, _ = fal.call_fal_sd35_generate('fake', '', '', 'missing.png', queue_handle={'request_id': 'one'})
    assert image is not None and error is None and len(calls) == 1


def test_restore_pending_journal_does_not_run_provider(commit_target, monkeypatch):
    from Floor_engine_server import records, result_commits
    job, source, _ = commit_target
    result_commits.claim_target(job.job_id, 'b2', str(source), 'inpaint_apply')
    monkeypatch.setattr(records, 'api_write_to_record', lambda *a, **k: None)
    with pytest.raises(HTTPException) as error:
        result_commits.commit_image(Image.new('RGB', (8, 8)), label='edit', job=job, stage='b2')
    job.pending_result_commit = ''
    result_commits.restore_pending_jobs()
    assert job.pending_result_commit == error.value.detail['commit_id']
    assert job.operation_status == 'failed'



def test_admission_persist_failure_never_records_an_uncertain_charge(sd_job, monkeypatch):
    from Floor_engine_server import billing_phases
    monkeypatch.setattr(state.JOBS, 'persist', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        billing_phases.begin_phase(sd_job, 'sd')
    assert 'billing_stages' not in sd_job.model_runs['sd35']['settings']
    assert usage_stats.load_usage_summary()['totals']['uncertain'] == 0


def test_generated_image_commit_setup_failure_releases_lease(commit_target, monkeypatch):
    from Floor_engine_server import result_commits
    job, _, _ = commit_target
    monkeypatch.setattr(result_commits, '_paths', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        result_commits.commit_generated_image(Image.new('RGB', (8, 8)), job=job, label='SD', stage='b2')
    assert not state.job_is_active(job)


def test_cancel_before_submit_never_opens_network(monkeypatch):
    from Floor_engine_server.providers import fal
    monkeypatch.setattr(fal._req, 'Session', lambda: (_ for _ in ()).throw(AssertionError('network opened')))
    data, error = fal._call_fal_queue_json('fake', 'endpoint', {}, should_cancel=lambda: True)
    assert data is None
    assert error.failure_code == 'cancelled_before_submit'
    assert error.may_have_been_billed is False



def test_inpaint_apply_failure_keeps_candidate_and_releases_target(commit_target, tmp_path, monkeypatch):
    from Floor_engine_server import records, routes_inpaint
    from Floor_engine_server.server_schemas import InpaintApplyRequest
    job, source, record = commit_target
    candidate = tmp_path / 'candidate.png'
    Image.new('RGB', (8, 8), 'blue').save(candidate)
    registry = TaskRegistry('inpaints', max_entries=20, is_terminal=state.inpaint_is_terminal)
    monkeypatch.setattr(state, 'INPAINTS', registry)
    registry.add('iid', {'status': 'done', 'mode': 'remove', 'prompt': '',
        'target': {'kind': 'job', 'jid': job.job_id, 'stage': 'b2', 'image_rel': 'source.png'},
        'candidates': [{'path': str(candidate)}]})
    original = records.api_write_to_record
    monkeypatch.setattr(records, 'api_write_to_record', lambda *a, **k: None)
    with pytest.raises(HTTPException):
        asyncio.run(routes_inpaint.inpaint_apply('iid', InpaintApplyRequest(index=0)))
    assert registry.get('iid')['status'] == 'done'
    assert candidate.is_file() and not state.job_is_active(job)
    monkeypatch.setattr(records, 'api_write_to_record', original)
    response = asyncio.run(routes_inpaint.inpaint_apply('iid', InpaintApplyRequest(index=0)))
    assert response['ok'] is True
    assert registry.get('iid') is None
    assert not candidate.exists()
    assert len(records.load_records_file(str(record))[0]['results']) == 2


def test_floor_projection_claims_target_before_computation(commit_target, monkeypatch):
    from Floor_engine_server import routes_tools
    from Floor_engine_server.server_schemas import FloorVisualizeRequest, FloorVisualizeTarget, FloorPoint
    job, source, _ = commit_target
    def render(request, max_side, claimed_job):
        assert claimed_job is job and state.job_is_active(job)
        with pytest.raises(HTTPException) as error:
            routes_jobs.delete_job(job.job_id)
        assert error.value.status_code == 409
        return Image.new('RGB', (8, 8)), {}, {'job': job, 'source_path': str(source)}
    monkeypatch.setattr(routes_tools, '_run_floor_visualize', render)
    request = FloorVisualizeRequest(target=FloorVisualizeTarget(kind='job', jid=job.job_id, stage='b2', image_rel='source.png'),
        texture_path=str(source), mask_b64='fake-mask', calibration_quad=[FloorPoint(x=x, y=y) for x,y in [(0,0),(1,0),(1,1),(0,1)]])
    response = asyncio.run(routes_tools.floor_visualize_apply(request))
    assert response['ok'] is True
    assert not state.job_is_active(job)
