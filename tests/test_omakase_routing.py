import asyncio
import json

import pytest
from fastapi import HTTPException

from Floor_engine_server import config, server_schemas, routes_config
from Floor_engine_server import api, server_api
from Floor_engine_server.failure_kb import classify_failure


OPTIONS = [
    {"text": "场景 A", "why": "理由 A", "recommended": True, "subject_type": "person"},
    {"text": "场景 B", "why": "理由 B", "recommended": False, "subject_type": "none"},
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

    monkeypatch.setattr(api, "load_config", lambda: {"tls_verify": True})
    monkeypatch.setattr(api._req, "post", fake_post)

    options, err = api.call_gemini_scenes(
        "温馨的亲子时光", api_key="gemini-secret", model="gemini-3.6-flash")

    assert err is None
    assert captured["url"].endswith("/gemini-3.6-flash:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "gemini-secret"
    generation = captured["json"]["generationConfig"]
    assert "temperature" not in generation
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseSchema"]["properties"]["options"]["minItems"] == 2
    assert [option["recommended"] for option in options] == [True, False]
    assert options[0]["text"] == "场景 A"
    assert options[0]["subject_type"] == "none"  # 旧/异常提供方漏字段时安全回落
    assert generation["responseSchema"]["properties"]["options"]["items"]["properties"][
        "subject_type"
    ]["enum"] == ["none", "person", "pet", "both"]


def test_omakase_router_does_not_call_deepseek_when_gemini_succeeds(monkeypatch):
    monkeypatch.setattr(api, "call_gemini_scenes", lambda *a, **k: (OPTIONS, None))

    def unexpected(*args, **kwargs):
        raise AssertionError("DeepSeek must not be called after Gemini succeeds")

    monkeypatch.setattr(api, "call_deepseek_scenes", unexpected)
    result = api.call_omakase_scenes(
        "idea", gemini_api_key="g", gemini_model="gemini-3.6-flash",
        deepseek_api_key="d")

    assert result == (OPTIONS, None, "gemini", False)


def test_omakase_router_falls_back_to_deepseek(monkeypatch):
    monkeypatch.setattr(api, "call_gemini_scenes",
                        lambda *a, **k: ([], "Omakase Gemini HTTP 503"))
    monkeypatch.setattr(api, "call_deepseek_scenes", lambda *a, **k: (OPTIONS, None))

    result = api.call_omakase_scenes(
        "idea", gemini_api_key="g", gemini_model="gemini-3.6-flash",
        deepseek_api_key="d")

    assert result == (OPTIONS, None, "deepseek", True)


def test_omakase_router_preserves_deepseek_only_config(monkeypatch):
    monkeypatch.setattr(api, "call_deepseek_scenes", lambda *a, **k: (OPTIONS, None))

    result = api.call_omakase_scenes(
        "idea", gemini_api_key="", gemini_model="gemini-3.6-flash",
        deepseek_api_key="d")

    assert result == (OPTIONS, None, "deepseek", True)


def test_omakase_router_reports_both_failures(monkeypatch):
    monkeypatch.setattr(api, "call_gemini_scenes",
                        lambda *a, **k: ([], "Omakase Gemini HTTP 503"))
    monkeypatch.setattr(api, "call_deepseek_scenes",
                        lambda *a, **k: ([], "DeepSeek HTTP 429"))

    options, err, provider, fallback = api.call_omakase_scenes(
        "idea", gemini_api_key="g", gemini_model="gemini-3.6-flash",
        deepseek_api_key="d")

    assert options == [] and provider == "deepseek" and fallback is True
    assert "Gemini 主线路失败" in err and "DeepSeek 备用线路失败" in err
    assert classify_failure(err)["key"] == "omakase_both_failed"


def test_omakase_endpoint_exposes_fallback_notice(monkeypatch):
    async def immediate(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(routes_config.asyncio, "to_thread", immediate)
    monkeypatch.setattr(routes_config, "get_omakase_enabled", lambda: True)
    monkeypatch.setattr(routes_config, "load_config", lambda: {"gemini_api_key": "g"})
    monkeypatch.setattr(routes_config, "get_deepseek_api_key", lambda: "d")
    monkeypatch.setattr(routes_config, "get_omakase_gemini_model", lambda: "gemini-3.6-flash")
    monkeypatch.setattr(routes_config, "get_deepseek_base_url", lambda: "https://api.deepseek.com")
    monkeypatch.setattr(routes_config, "get_deepseek_model", lambda: "deepseek-chat")
    monkeypatch.setattr(
        routes_config, "call_omakase_scenes",
        lambda *a, **k: (OPTIONS, None, "deepseek", True),
    )

    response = asyncio.run(routes_config.omakase_scenes(server_schemas.OmakaseRequest(idea="idea")))

    assert response["provider"] == "deepseek"
    assert response["fallback_used"] is True
    assert "DeepSeek 备用线路" in response["notice"]


def test_omakase_endpoint_rejects_when_no_text_provider_key(monkeypatch):
    monkeypatch.setattr(routes_config, "get_omakase_enabled", lambda: True)
    monkeypatch.setattr(routes_config, "load_config", lambda: {})
    monkeypatch.setattr(routes_config, "get_deepseek_api_key", lambda: "")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_config.omakase_scenes(server_schemas.OmakaseRequest(idea="idea")))

    assert exc.value.status_code == 400
    assert "Gemini API Key" in exc.value.detail


def test_text_model_defaults_and_legacy_omakase_value_are_current(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {})
    assert config.get_omakase_gemini_model() == "gemini-3.6-flash"
    assert config.get_text_models() == [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
    ]
    assert config.get_ping_model() == "gemini-3.5-flash-lite"

    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"omakase_gemini_model": "gemini-2.5-flash"},
    )
    assert config.get_omakase_gemini_model() == "gemini-3.6-flash"


def test_image_model_defaults_use_stable_ids():
    assert config.GEMINI_MODEL_MAP == {
        "Nano Banana 2": "gemini-3.1-flash-image",
        "Nano Banana Pro": "gemini-3-pro-image",
    }
    assert config.FAL_MODEL_MAP["gemini-3.1-flash-image"].endswith(
        "/nano-banana-2/edit"
    )
    assert config.FAL_MODEL_MAP["gemini-3-pro-image"].endswith(
        "/nano-banana-pro/edit"
    )


def test_gemini_failure_is_logged_even_without_deepseek(monkeypatch, caplog):
    monkeypatch.setattr(
        api,
        "call_gemini_scenes",
        lambda *a, **k: (
            [],
            "Omakase Gemini HTTP 404: model unavailable?key=super-secret",
        ),
    )

    result = api.call_omakase_scenes(
        "idea",
        gemini_api_key="g",
        gemini_model="gemini-3.6-flash",
        deepseek_api_key="",
    )

    assert result[1].startswith("Omakase Gemini HTTP 404")
    assert "Gemini 主线路失败" in caplog.text
    assert "gemini-3.6-flash" in caplog.text
    assert "super-secret" not in caplog.text


def test_model_unavailable_failure_has_specific_guidance():
    info = classify_failure(
        "Omakase Gemini HTTP 404: This model is no longer available to new users"
    )
    assert info["key"] == "omakase_model_unavailable"
    assert "无需重新申请" in info["action"]


def test_zero_cost_model_probe_accepts_expected_empty_contents_error(monkeypatch):
    monkeypatch.setattr(
        api._req,
        "post",
        lambda *a, **k: FakeResponse(
            status_code=400,
            payload={
                "error": {
                    "message": "* GenerateContentRequest.contents: contents is not specified"
                }
            },
        ),
    )

    result = api._probe_gemini_model_endpoint("key", "gemini-3.6-flash")

    assert result == "✅ 端点可用（未生成）"


def test_zero_cost_model_probe_reports_model_404(monkeypatch):
    monkeypatch.setattr(
        api._req,
        "post",
        lambda *a, **k: FakeResponse(
            status_code=404,
            payload={
                "error": {
                    "message": "This model is no longer available to new users"
                }
            },
        ),
    )

    result = api._probe_gemini_model_endpoint("key", "gemini-2.5-flash")

    assert "模型不可用 (HTTP 404)" in result


def test_zero_cost_model_probe_retries_one_network_reset(monkeypatch):
    calls = {"count": 0}

    def flaky_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise api._req.exceptions.ConnectionError("connection reset")
        return FakeResponse(
            status_code=400,
            payload={
                "error": {
                    "message": "* GenerateContentRequest.contents: contents is not specified"
                }
            },
        )

    monkeypatch.setattr(api._req, "post", flaky_post)

    result = api._probe_gemini_model_endpoint("key", "gemini-3-pro-image")

    assert calls["count"] == 2
    assert result == "✅ 端点可用（未生成）"


def test_connection_check_includes_all_production_model_endpoints(monkeypatch):
    captured = {}
    get_calls = {"count": 0}

    def fake_get(url, **kwargs):
        get_calls["count"] += 1
        if get_calls["count"] == 1:
            raise api._req.exceptions.ConnectionError("connection reset")
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return FakeResponse(status_code=200, payload={"models": []})

    probed = []
    monkeypatch.setattr(api._req, "get", fake_get)
    monkeypatch.setattr(api, "_verify_arg", lambda *a, **k: True)
    monkeypatch.setattr(
        api,
        "_probe_gemini_model_endpoint",
        lambda _key, model, **kwargs: (
            probed.append(model) or "✅ 端点可用（未生成）"
        ),
    )
    monkeypatch.setattr(
        api,
        "get_omakase_gemini_model",
        lambda: "gemini-3.6-flash",
    )

    result = api.test_connection("gemini-secret")

    assert captured["url"].endswith("/v1beta/models")
    assert get_calls["count"] == 2
    assert "key=" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "gemini-secret"
    assert probed == [
        "gemini-3.6-flash",
        "gemini-3.1-flash-image",
        "gemini-3-pro-image",
        "gemini-3.1-flash-lite-image",
    ]
    assert "Omakase/电影规划 [gemini-3.6-flash]" in result
    assert "Pro [gemini-3-pro-image]" in result


def test_style_analysis_uses_current_text_model_without_temperature(
    monkeypatch, swatch_image
):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            payload={
                "candidates": [
                    {"content": {"parts": [{"text": "SIGNATURE: natural room"}]}}
                ]
            }
        )

    monkeypatch.setattr(
        api,
        "load_config",
        lambda: {"style_analysis_cache": False, "tls_verify": True},
    )
    monkeypatch.setattr(api, "get_text_models", lambda: ["gemini-3.6-flash"])
    monkeypatch.setattr(api._req, "post", fake_post)

    text, err = api.analyze_style_image("key", swatch_image)

    assert err is None
    assert text == "SIGNATURE: natural room"
    assert captured["url"].endswith("/gemini-3.6-flash:generateContent?key=key")
    assert "temperature" not in captured["json"]["generationConfig"]
