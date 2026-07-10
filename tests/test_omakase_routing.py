import asyncio
import json

import pytest
from fastapi import HTTPException

from Floor_engine_server import api, server_api
from Floor_engine_server.failure_kb import classify_failure


OPTIONS = [
    {"text": "场景 A", "why": "理由 A", "recommended": True},
    {"text": "场景 B", "why": "理由 B", "recommended": False},
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_gemini_scenes_uses_structured_output_and_normalizes_recommended(monkeypatch):
    captured = {}
    raw_options = [
        {"text": "  场景 A  ", "why": "理由 A", "recommended": True},
        {"text": "场景 B", "why": "理由 B", "recommended": True},
    ]

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        content = json.dumps({"options": raw_options}, ensure_ascii=False)
        return FakeResponse(payload={
            "candidates": [{"content": {"parts": [{"text": content}]}}],
        })

    monkeypatch.setattr(api, "_load_config", lambda: {"tls_verify": True})
    monkeypatch.setattr(api._req, "post", fake_post)

    options, err = api.call_gemini_scenes(
        "温馨的亲子时光", api_key="gemini-secret", model="gemini-2.5-flash")

    assert err is None
    assert captured["url"].endswith("/gemini-2.5-flash:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "gemini-secret"
    generation = captured["json"]["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseSchema"]["properties"]["options"]["minItems"] == 2
    assert [option["recommended"] for option in options] == [True, False]
    assert options[0]["text"] == "场景 A"


def test_omakase_router_does_not_call_deepseek_when_gemini_succeeds(monkeypatch):
    monkeypatch.setattr(api, "call_gemini_scenes", lambda *a, **k: (OPTIONS, None))

    def unexpected(*args, **kwargs):
        raise AssertionError("DeepSeek must not be called after Gemini succeeds")

    monkeypatch.setattr(api, "call_deepseek_scenes", unexpected)
    result = api.call_omakase_scenes(
        "idea", gemini_api_key="g", gemini_model="gemini-2.5-flash",
        deepseek_api_key="d")

    assert result == (OPTIONS, None, "gemini", False)


def test_omakase_router_falls_back_to_deepseek(monkeypatch):
    monkeypatch.setattr(api, "call_gemini_scenes",
                        lambda *a, **k: ([], "Omakase Gemini HTTP 503"))
    monkeypatch.setattr(api, "call_deepseek_scenes", lambda *a, **k: (OPTIONS, None))

    result = api.call_omakase_scenes(
        "idea", gemini_api_key="g", gemini_model="gemini-2.5-flash",
        deepseek_api_key="d")

    assert result == (OPTIONS, None, "deepseek", True)


def test_omakase_router_preserves_deepseek_only_config(monkeypatch):
    monkeypatch.setattr(api, "call_deepseek_scenes", lambda *a, **k: (OPTIONS, None))

    result = api.call_omakase_scenes(
        "idea", gemini_api_key="", gemini_model="gemini-2.5-flash",
        deepseek_api_key="d")

    assert result == (OPTIONS, None, "deepseek", True)


def test_omakase_router_reports_both_failures(monkeypatch):
    monkeypatch.setattr(api, "call_gemini_scenes",
                        lambda *a, **k: ([], "Omakase Gemini HTTP 503"))
    monkeypatch.setattr(api, "call_deepseek_scenes",
                        lambda *a, **k: ([], "DeepSeek HTTP 429"))

    options, err, provider, fallback = api.call_omakase_scenes(
        "idea", gemini_api_key="g", gemini_model="gemini-2.5-flash",
        deepseek_api_key="d")

    assert options == [] and provider == "deepseek" and fallback is True
    assert "Gemini 主线路失败" in err and "DeepSeek 备用线路失败" in err
    assert classify_failure(err)["key"] == "omakase_both_failed"


def test_omakase_endpoint_exposes_fallback_notice(monkeypatch):
    async def immediate(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(server_api.asyncio, "to_thread", immediate)
    monkeypatch.setattr(server_api, "get_omakase_enabled", lambda: True)
    monkeypatch.setattr(server_api, "_load_config", lambda: {"gemini_api_key": "g"})
    monkeypatch.setattr(server_api, "get_deepseek_api_key", lambda: "d")
    monkeypatch.setattr(server_api, "get_omakase_gemini_model", lambda: "gemini-2.5-flash")
    monkeypatch.setattr(server_api, "get_deepseek_base_url", lambda: "https://api.deepseek.com")
    monkeypatch.setattr(server_api, "get_deepseek_model", lambda: "deepseek-chat")
    monkeypatch.setattr(
        server_api, "call_omakase_scenes",
        lambda *a, **k: (OPTIONS, None, "deepseek", True),
    )

    response = asyncio.run(server_api.omakase_scenes(server_api.OmakaseRequest(idea="idea")))

    assert response["provider"] == "deepseek"
    assert response["fallback_used"] is True
    assert "DeepSeek 备用线路" in response["notice"]


def test_omakase_endpoint_rejects_when_no_text_provider_key(monkeypatch):
    monkeypatch.setattr(server_api, "get_omakase_enabled", lambda: True)
    monkeypatch.setattr(server_api, "_load_config", lambda: {})
    monkeypatch.setattr(server_api, "get_deepseek_api_key", lambda: "")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server_api.omakase_scenes(server_api.OmakaseRequest(idea="idea")))

    assert exc.value.status_code == 400
    assert "Gemini API Key" in exc.value.detail
