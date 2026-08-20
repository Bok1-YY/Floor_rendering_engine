# -*- coding: utf-8 -*-
from __future__ import annotations

import io

from PIL import Image

from Floor_engine_server import api
from Floor_engine_server.models import ensure_model_runs, new_job, update_model_run
from Floor_engine_server.server_helpers import job_view


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 12), (30, 80, 120)).save(buffer, "PNG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status_code: int, headers: dict, chunks):
        self.status_code = status_code
        self.headers = headers
        self._chunks = list(chunks)
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise api._req.exceptions.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        del chunk_size
        for value in self._chunks:
            if isinstance(value, Exception):
                raise value
            yield value

    def close(self):
        self.closed = True


class FakeSession:
    responses = []
    instances = []

    def __init__(self):
        self.proxies = {}
        self.trust_env = True
        self.requests = []
        FakeSession.instances.append(self)

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        value = FakeSession.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        pass


def test_fal_media_download_resumes_partial_bytes_with_range(monkeypatch):
    raw = _png_bytes()
    split = len(raw) // 2
    FakeSession.responses = [
        FakeResponse(200, {"Content-Length": str(len(raw))}, [
            raw[:split], api._req.exceptions.SSLError("unexpected eof"),
        ]),
        FakeResponse(206, {
            "Content-Length": str(len(raw) - split),
            "Content-Range": f"bytes {split}-{len(raw) - 1}/{len(raw)}",
        }, [raw[split:]]),
    ]
    FakeSession.instances = []
    monkeypatch.setattr(api._req, "Session", FakeSession)
    monkeypatch.setattr(api, "load_config", lambda: {
        "proxy": "http://127.0.0.1:7890",
        "fal_media_download_attempts": 2,
        "fal_media_retry_backoffs": [0],
        "tls_verify": True,
    })

    image, error = api._fal_image_from_result({
        "images": [{"url": "https://v3b.fal.media/result.png"}],
    })

    assert error is None
    assert image is not None and image.size == (24, 12)
    assert FakeSession.instances[0].proxies["https"] == "http://127.0.0.1:7890"
    second_headers = FakeSession.instances[1].requests[0][1]["headers"]
    assert second_headers == {"Range": f"bytes={split}-"}


def test_fal_media_download_switches_from_proxy_to_direct(monkeypatch):
    raw = _png_bytes()
    FakeSession.responses = [
        api._req.exceptions.ConnectionError("proxy reset 1"),
        api._req.exceptions.ConnectionError("proxy reset 2"),
        FakeResponse(200, {"Content-Length": str(len(raw))}, [raw]),
    ]
    FakeSession.instances = []
    monkeypatch.setattr(api._req, "Session", FakeSession)
    monkeypatch.setattr(api, "load_config", lambda: {
        "proxy": "http://127.0.0.1:7890",
        "fal_queue_proxy": "",
        "fal_media_download_attempts": 3,
        "fal_media_retry_backoffs": [0],
        "tls_verify": True,
    })

    image, error = api._fal_image_from_result({
        "images": [{"url": "https://v3b.fal.media/result.png"}],
    })

    assert error is None
    assert image is not None
    assert FakeSession.instances[0].proxies
    assert FakeSession.instances[1].proxies
    assert FakeSession.instances[2].proxies == {}
    assert FakeSession.instances[2].trust_env is False


def test_job_view_exposes_only_resumable_interrupted_panorama_handle():
    job = new_job("demo", "now", "both")
    job.model_targets = ["b2", "pro"]
    ensure_model_runs(job)
    preview_id = "vrpreview_resumable"
    job.panorama_previews = {
        preview_id: {
            "status": "interrupted",
            "policy": "pure_render_pano_paid_preview_v1",
            "preview_hash": "a" * 64,
            "source_model": "b2",
            "source_index": 1,
            "created_at_epoch": 100.0,
            "error": "download interrupted",
        },
        "vrpreview_no_handle": {
            "status": "interrupted",
            "policy": "pure_render_pano_paid_preview_v1",
            "preview_hash": "b" * 64,
            "created_at_epoch": 200.0,
        },
    }
    update_model_run(job, "vr360", settings={
        "pano_queue_handles": {
            preview_id: {"request_id": "fal-existing-request"},
        },
    })

    view = job_view(job)

    assert view["panorama_resume"] == {
        "preview_id": preview_id,
        "preview_hash": "a" * 64,
        "route": "perspective_to_erp",
        "request_ids": ["fal-existing-request"],
        "source_model": "b2",
        "source_index": 1,
        "created_at_epoch": 100.0,
        "reason": "download interrupted",
    }
    assert "pano_queue_handles" not in view["model_runs"]["b2"]["settings"]
