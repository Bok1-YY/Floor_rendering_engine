"""Durable SD/Aura stage outcomes and request-level usage reconciliation."""
from copy import deepcopy
from urllib.parse import urlsplit
import uuid

from . import server_state as state
from .models import update_model_run
from .providers.fal import SD35_ENDPOINT, AURA_SR_ENDPOINT
from .usage_stats import record_usage

PHASES = {'sd': ('sd_queue', SD35_ENDPOINT, 'SD35', 'generate'),
          'upscale': ('upscale_queue', AURA_SR_ENDPOINT, 'AuraSR', 'upscale')}


def valid_handle(handle, phase):
    if not isinstance(handle, dict) or not handle.get('request_id'):
        return False
    if handle.get('endpoint') not in (None, '', PHASES[phase][1]):
        return False
    for key in ('status_url', 'response_url'):
        try:
            url = urlsplit(str(handle.get(key) or ''))
        except ValueError:
            return False
        if url.scheme != 'https' or url.netloc != 'queue.fal.run' or not url.path.startswith('/'):
            return False
    return True


def phase_recovery(run, phase):
    settings = (run or {}).get('settings') or {}
    handle = settings.get(PHASES[phase][0])
    if valid_handle(handle, phase):
        return 'resume'
    detail = (settings.get('billing_stages') or {}).get(phase) or {}
    risk = detail.get('retry_safety', (run or {}).get('retry_safety', 'safe'))
    return 'confirm' if risk == 'ambiguous' or handle else 'retry'


def begin_phase(job, phase):
    with state.JOBS.locked():
        run = update_model_run(job, 'sd35')
        settings = deepcopy(run.get('settings') or {})
        before = deepcopy(settings)
        stages = settings.setdefault('billing_stages', {})
        prior = stages.get(phase) or {}
        handle = settings.get(PHASES[phase][0])
        resuming = valid_handle(handle, phase)
        request_id = (prior.get('request_id') or f"fal:{handle['request_id']}") if resuming else uuid.uuid4().hex
        stages[phase] = {**(prior if resuming else {}), 'request_id': request_id, 'status': 'pending',
                         'retry_safety': 'ambiguous', 'may_have_been_billed': True}
        settings['active_billing_phase'] = phase
        update_model_run(job, 'sd35', settings=settings)
    # A durable identity exists before any network submit. Failure prevents the call.
    try:
        state.JOBS.persist()
    except Exception:
        update_model_run(job, 'sd35', settings=before)
        raise
    return request_id


def finish_phase(job, phase, *, error=None, success=False):
    with state.JOBS.locked():
        run = update_model_run(job, 'sd35')
        settings = deepcopy(run.get('settings') or {})
        stages = settings.setdefault('billing_stages', {})
        detail = dict(stages.get(phase) or {})
        risk = 'safe' if success else str(getattr(error, 'retry_safety', 'fatal'))
        events = list(detail.get('attempts') or [])
        for event in getattr(error, 'attempts', []) or []:
            if event not in events:
                events.append(event)
        fields = {
            'failure_code': '' if success else str(getattr(error, 'failure_code', 'provider_failure')),
            'retry_safety': risk,
            'may_have_been_billed': bool(success or getattr(error, 'may_have_been_billed', False)),
            'attempts': events,
        }
        status = 'success' if success else ('uncertain' if risk == 'ambiguous' else 'failed')
        detail.update(fields, status=status, error=str(error or ''))
        stages[phase] = detail
        settings['active_billing_phase'] = phase
        update_model_run(job, 'sd35', settings=settings, **fields)
    _name, _endpoint, label, operation = PHASES[phase]
    if getattr(error, 'failure_code', '') != 'cancelled_before_submit':
        record_usage(job.workflow_mode, label, 'fal', status, operation,
                     request_id=detail.get('request_id'))
    try:
        state.JOBS.persist()
    except Exception:
        state.logger.exception('已取得 provider 结果，但状态保存失败；继续保留已生成图片')


def require_retry_confirmation(run, phase, confirmed):
    from fastapi import HTTPException
    action = phase_recovery(run, phase)
    if action == 'confirm' and not confirmed:
        raise HTTPException(409, {'code': 'possible_duplicate_charge_confirmation_required',
            'message': '上一次请求结果不确定，可能已经计费；确认后才能重新提交', 'phase': phase})
    return action
