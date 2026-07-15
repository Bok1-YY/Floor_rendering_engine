"""SD 3.5 独立线路的离线契约测试（不发网络请求、不产生 API 费用）。"""

from PIL import Image

from Floor_engine_server import api
from Floor_engine_server.models import (
    JobRecord,
    TaskParams,
    add_model_candidate,
    compute_runs_final_status,
    ensure_model_runs,
)
from Floor_engine_server.sd_prompts import compile_sd35_prompt
from Floor_engine_server.server_api import _job_view


def _params(**overrides):
    base = dict(
        image_path="unused.png",
        model_choice="Nano Banana 2",
        workflow_mode="纯效果图 (生成全新空间)",
        country="Germany",
        city="Berlin",
        property_type="现代别墅",
        room_type="客餐厅一体",
        style_type="现代极简",
        lighting="自然光",
        floor_tone="暖橡木色",
        floor_size="常规直拼",
        seam_type="无缝拼接 (SPC/LVT专用)",
        glossiness="哑光 (3-5°)",
        angle="28mm lens (Wide)",
        avoid_items=["镜面反光"],
    )
    base.update(overrides)
    return TaskParams(**base)


def test_sd_prompt_is_separate_positive_and_negative():
    bundle = compile_sd35_prompt(
        _params(),
        positive_addition="editorial composition",
        negative_addition="watermark, signature",
    )
    assert "uploaded floor swatch is the mandatory material" in bundle.positive
    assert "editorial composition" in bundle.positive
    assert "watermark, signature" not in bundle.positive
    assert "watermark, signature" in bundle.negative
    assert "dark grout" in bundle.negative
    assert bundle.compiler_version == "sd35-v1"


def test_sd_prompt_seam_rules_are_conditional():
    seamless = compile_sd35_prompt(_params(seam_type="无缝拼接 (SPC/LVT专用)"))
    bevel = compile_sd35_prompt(_params(seam_type="圆弧倒角 (Pressed Bevel)"))
    assert "near-continuous tightly fitted floor" in seamless.positive
    assert "soft rounded pressed micro-bevel edges" not in seamless.positive
    assert "soft rounded pressed micro-bevel edges" in bevel.positive
    assert "completely invisible board rhythm" in bevel.negative


def test_sd_base_canvas_is_near_one_megapixel_and_64_aligned():
    for ratio in ("4:3", "16:9", "3:4", "9:16", "bad"):
        size = api.sd35_base_size(ratio)
        assert size["width"] % 64 == 0
        assert size["height"] % 64 == 0
        assert 800_000 <= size["width"] * size["height"] <= 1_250_000


def test_sd_fal_payload_requires_ip_adapter(monkeypatch, swatch_image):
    captured = {}

    def fake_call(key, endpoint, payload, **kwargs):
        captured.update(key=key, endpoint=endpoint, payload=payload)
        return {"images": [{"url": "mock://image"}], "seed": 1234}, None

    monkeypatch.setattr(api, "_call_fal_queue_json", fake_call)
    monkeypatch.setattr(
        api, "_fal_image_from_result",
        lambda data, plural=True, direct=False: (Image.new("RGB", (64, 64)), None),
    )
    image, err, seed = api.call_fal_sd35_generate(
        "secret", "positive", "negative", swatch_image,
        seed=1234, steps=30, guidance_scale=4.0, reference_strength=0.55,
    )
    assert image is not None and err is None and seed == 1234
    assert captured["endpoint"] == api.SD35_ENDPOINT
    payload = captured["payload"]
    assert payload["negative_prompt"] == "negative"
    assert payload["seed"] == 1234
    assert payload["ip_adapter"]["path"] == api.SD35_IP_ADAPTER_PATH
    assert payload["ip_adapter"]["weight_name"] == "ip-adapter.bin"
    assert payload["ip_adapter"]["image_url"].startswith("data:image/")
    assert payload["ip_adapter"]["scale"] == 0.55


class _Response:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._data


class _Session:
    def __init__(self, post, get=None):
        self._post = post
        self._get = get
        self.proxies = {}
        self.trust_env = True

    def post(self, url, **kwargs):
        return self._post(url, **kwargs)

    def get(self, url, **kwargs):
        return self._get(url, **kwargs)


def test_fal_queue_submits_once_then_polls(monkeypatch):
    calls = {"post": 0, "status": 0, "result": 0}
    base = "https://queue.fal.run/fal-ai/demo/requests/r1"

    def post(url, **kwargs):
        calls["post"] += 1
        return _Response({
            "request_id": "r1",
            "status_url": base + "/status",
            "response_url": base + "/response",
            "cancel_url": base + "/cancel",
        })

    def get(url, **kwargs):
        if url.endswith("/status"):
            calls["status"] += 1
            queued = calls["status"] == 1
            return _Response({"status": "IN_QUEUE" if queued else "COMPLETED"}, 202 if queued else 200)
        calls["result"] += 1
        return _Response({"images": [{"url": "mock://image"}]})

    monkeypatch.setattr(api, "_load_config", lambda: {"proxy": "", "fal_queue_timeout": 60})
    session = _Session(post, get)
    monkeypatch.setattr(api._req, "Session", lambda: session)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    data, err = api._call_fal_queue_json("secret", "fal-ai/demo", {"prompt": "x"})
    assert err is None and data["images"]
    assert calls == {"post": 1, "status": 2, "result": 1}
    assert session.trust_env is False


def test_fal_queue_never_resubmits_after_unknown_network_failure(monkeypatch):
    calls = {"post": 0}

    def post(url, **kwargs):
        calls["post"] += 1
        raise api._req.exceptions.ConnectionError("lost after submit")

    monkeypatch.setattr(api, "_load_config", lambda: {"proxy": ""})
    monkeypatch.setattr(api._req, "Session", lambda: _Session(post))
    data, err = api._call_fal_queue_json("secret", "fal-ai/demo", {"prompt": "x"})
    assert data is None
    assert "未自动重交" in err
    assert calls["post"] == 1


def test_generic_model_runs_migrate_legacy_and_support_sd(tmp_path):
    b2 = str(tmp_path / "b2.jpg")
    sd = str(tmp_path / "sd.png")
    job = JobRecord("j", "demo", "00:00:00", model_filter="both", b2_path=b2)
    ensure_model_runs(job)
    assert job.model_targets == ["b2", "pro"]
    assert job.model_runs["b2"]["paths"] == [b2]
    assert compute_runs_final_status(job) == "partial"

    job.model_targets.append("sd35")
    add_model_candidate(job, "sd35", sd)
    assert job.model_runs["sd35"]["paths"] == [sd]
    assert compute_runs_final_status(job) == "partial"


def test_job_view_exposes_model_runs_as_keyed_object(tmp_path):
    sd = str(tmp_path / "sd.png")
    job = JobRecord("j", "demo", "00:00:00", model_filter="sd35")
    job.model_targets = ["sd35"]
    add_model_candidate(job, "sd35", sd)
    view = _job_view(job)
    assert view["model_targets"] == ["sd35"]
    assert isinstance(view["model_runs"], dict)
    assert view["model_runs"]["sd35"]["total"] == 1
