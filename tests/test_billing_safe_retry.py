import asyncio
import base64
import io

import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import api, routes_jobs, server_state, usage_stats
from Floor_engine_server.models import new_job, update_model_run
from Floor_engine_server.providers.types import ProviderError
from Floor_engine_server.server_schemas import RetryJobRequest
from Floor_engine_server.task_registry import TaskRegistry


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def close(self):
        return None


def _image_payload():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    return {"candidates": [{"content": {"parts": [{
        "inlineData": {"data": base64.b64encode(buffer.getvalue()).decode("ascii")},
    }]}}]}


def _patch_generate_config(monkeypatch, attempts=3):
    monkeypatch.setattr(api, "load_config", lambda: {
        "retry_attempts": attempts,
        "retry_backoffs": [0],
        "speed_profile": "fast",
        "tls_verify": True,
    })
    monkeypatch.setattr(api.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)


def test_google_read_timeout_is_ambiguous_and_not_retried(tmp_path, monkeypatch):
    image = tmp_path / "sample.png"
    Image.new("RGB", (4, 4), "tan").save(image)
    _patch_generate_config(monkeypatch)
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise api._req.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(api._req, "post", post)
    result, error = api.call_gemini_generate("k", "model", "prompt", str(image))
    assert result is None and calls == 1
    assert isinstance(error, ProviderError)
    assert error.retry_safety == "ambiguous"
    assert error.may_have_been_billed is True


def test_google_explicit_503_is_safely_retried(tmp_path, monkeypatch):
    image = tmp_path / "sample.png"
    Image.new("RGB", (4, 4), "tan").save(image)
    _patch_generate_config(monkeypatch)
    responses = [FakeResponse(503, {"error": {"message": "busy"}}), FakeResponse(200, _image_payload())]
    monkeypatch.setattr(api._req, "post", lambda *_args, **_kwargs: responses.pop(0))
    result, error = api.call_gemini_generate("k", "model", "prompt", str(image))
    assert result is not None and error is None
    assert responses == []


def test_ambiguous_google_failure_never_auto_fails_over(monkeypatch):
    error = ProviderError(
        "unknown", failure_code="google_read_timeout_unknown",
        retry_safety="ambiguous", may_have_been_billed=True,
    )
    fal_calls = 0
    monkeypatch.setattr(api, "load_config", lambda: {
        "image_provider": "google", "auto_failover": True, "fal_api_key": "f",
    })
    monkeypatch.setattr(api, "call_gemini_generate", lambda *_a, **_k: (None, error))

    def fal(*_args, **_kwargs):
        nonlocal fal_calls
        fal_calls += 1
        return object(), None

    monkeypatch.setattr(api, "call_fal_generate", fal)
    outcome = api.call_image_generate("g", "model", "prompt", "image.png")
    image, returned_error, provider = outcome
    assert image is None and returned_error is error and provider == "google"
    assert fal_calls == 0
    assert outcome.retry_safety == "ambiguous"


def test_edit_read_timeout_is_not_retried(monkeypatch):
    _patch_generate_config(monkeypatch)
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise api._req.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(api._req, "post", post)
    result, error = api.call_gemini_edit("k", "model", "change", "aW1hZ2U=")
    assert result is None and calls == 1
    assert isinstance(error, ProviderError) and error.retry_safety == "ambiguous"


def test_fal_direct_read_timeout_is_not_retried(tmp_path, monkeypatch):
    image = tmp_path / "sample.png"
    Image.new("RGB", (4, 4), "tan").save(image)
    monkeypatch.setattr(api, "load_config", lambda: {
        "retry_attempts": 3, "retry_backoffs": [0], "fal_retry_attempts": 3,
        "fal_model_map": {"model": "fal-ai/demo"}, "tls_verify": True,
    })
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise api._req.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(api._req, "post", post)
    result, error = api.call_fal_generate("k", "model", "prompt", str(image))
    assert result is None and calls == 1
    assert isinstance(error, ProviderError) and error.retry_safety == "ambiguous"


def test_ambiguous_job_retry_requires_confirmation(monkeypatch):
    jobs = TaskRegistry("jobs", max_entries=10, is_terminal=lambda _job: True)
    job = new_job("oak", "now")
    job.status = "failed"
    job.retry_ctx = {"api_key": "x"}
    job.model_targets = ["pro"]
    update_model_run(job, "pro", retry_safety="ambiguous", may_have_been_billed=True)
    jobs.add(job.job_id, job)
    monkeypatch.setattr(server_state, "JOBS", jobs)
    spawned = []

    def capture(coro):
        spawned.append(coro)
        coro.close()

    monkeypatch.setattr(server_state, "spawn", capture)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_jobs.retry_job(job.job_id, RetryJobRequest()))
    assert exc.value.status_code == 409

    response = asyncio.run(routes_jobs.retry_job(
        job.job_id, RetryJobRequest(confirm_possible_duplicate_charge=True)))
    assert response["status"] == "running"
    assert len(spawned) == 1


def test_usage_summary_reports_uncertain_cost_range(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    monkeypatch.setattr(usage_stats, "_USAGE_STATS_FILE", str(path))
    usage_stats.record_usage("纯效果图", "Nano Banana Pro", "google", "success")
    usage_stats.record_usage("纯效果图", "Nano Banana Pro", "google", "uncertain")
    usage_stats.record_usage("纯效果图", "Nano Banana Pro", "google", "failed")
    summary = usage_stats.load_usage_summary({"Pro": 2.0})
    assert summary["totals"]["uncertain"] == 1
    assert summary["totals"]["cost_min"] == 2.0
    assert summary["totals"]["cost_max"] == 4.0
