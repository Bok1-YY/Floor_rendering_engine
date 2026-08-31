"""Shared job-execution state helpers, independent of HTTP routes."""

from . import server_state as state
from .models import JobRecord, ensure_model_runs, update_model_run


def model_queue_handle(job: JobRecord, key: str, name: str) -> dict:
    ensure_model_runs(job)
    settings = dict((job.model_runs.get(key) or {}).get('settings') or {})
    value = settings.get(name)
    return dict(value) if isinstance(value, dict) else {}


def set_model_queue_handle(job: JobRecord, key: str, name: str, handle) -> None:
    """Persist a provider queue handle immediately after successful submission."""
    run = update_model_run(job, key)
    settings = dict(run.get('settings') or {})
    if handle:
        settings[name] = dict(handle)
    else:
        settings.pop(name, None)
    update_model_run(job, key, settings=settings)
    state.JOBS.persist()


def fal_queue_is_terminal_error(error: str) -> bool:
    text = str(error or '').upper()
    return 'FAL 队列任务FAILED' in text or 'FAL 队列任务CANCELLED' in text


__all__ = ['fal_queue_is_terminal_error', 'model_queue_handle', 'set_model_queue_handle']
