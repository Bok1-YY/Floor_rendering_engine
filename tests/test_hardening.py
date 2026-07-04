# -*- coding: utf-8 -*-
"""风险修复的回归测试：job_id 唯一性、上传路径安全、TLS verify 取值、用量线路归账。"""
import os

import pytest

from Floor_engine_server.models import new_job
from Floor_engine_server import config as cfg_mod
from Floor_engine_server.config import safe_upload_path, get_tls_verify
from Floor_engine_server import api as api_mod
from Floor_engine_server.api import _verify_arg, call_image_generate


# ── 1. job_id 唯一性（毫秒时间戳会撞，uuid 不会）──────────────────────────
def test_job_id_unique_10000():
    ids = {new_job("x", "t").job_id for _ in range(10000)}
    assert len(ids) == 10000
    assert all(i.startswith("job_") for i in ids)


# ── 2. 上传文件名安全化 ───────────────────────────────────────────────────
@pytest.mark.parametrize("evil", [
    "../x.png", "../../etc/passwd.png", r"C:\Windows\evil.png",
    "a/b/c.png", "  spaced name .JPG", "木地板.png",
])
def test_safe_upload_stays_inside(evil, tmp_path, monkeypatch):
    monkeypatch.setattr("Floor_engine_server.config.UPLOAD_DIR", str(tmp_path))
    p = safe_upload_path(evil, prefix="ref_")
    assert p is not None
    real_base = os.path.realpath(str(tmp_path))
    assert os.path.commonpath([os.path.realpath(p), real_base]) == real_base
    # 文件名不得含任何目录分隔符
    assert os.path.basename(p) == os.path.relpath(p, tmp_path)


@pytest.mark.parametrize("bad", ["hack.exe", "shell.php", "note.txt", "noext", "a.svg"])
def test_safe_upload_rejects_non_image(bad, tmp_path, monkeypatch):
    monkeypatch.setattr("Floor_engine_server.config.UPLOAD_DIR", str(tmp_path))
    assert safe_upload_path(bad) is None


def test_safe_upload_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr("Floor_engine_server.config.UPLOAD_DIR", str(tmp_path))
    p1 = safe_upload_path("pic.png")
    open(p1, "wb").close()
    p2 = safe_upload_path("pic.png")
    assert p1 != p2 and not os.path.exists(p2)  # 同名不覆盖，给了新名字


# ── 3. TLS verify 取值 ────────────────────────────────────────────────────
def test_verify_arg_default_true():
    assert _verify_arg({}) is True            # 默认开启校验（已实测本网络可过）

def test_verify_arg_off_when_disabled():
    assert _verify_arg({"tls_verify": False}) is False   # 显式关闭才不校验

def test_verify_arg_true_when_enabled_no_ca():
    assert _verify_arg({"tls_verify": True}) is True

def test_verify_arg_returns_ca_path_when_exists(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    assert _verify_arg({"tls_verify": True, "tls_ca_bundle": str(ca)}) == str(ca)

def test_verify_arg_true_when_ca_missing():
    assert _verify_arg({"tls_verify": True, "tls_ca_bundle": "/no/such/ca.pem"}) is True


# ── 4. 用量线路归账：自动转 Fal 后必须记 'fal'，不是配置里的 'google' ──────────
def test_call_image_generate_reports_actual_provider_on_failover(monkeypatch):
    fake_img = object()
    monkeypatch.setattr(api_mod, "_load_config",
                        lambda: {"image_provider": "google", "auto_failover": True, "fal_api_key": "k"})
    # google 直连返回网络类失败 → 触发自动转线
    monkeypatch.setattr(api_mod, "call_gemini_generate",
                        lambda *a, **k: (None, "网络错误: connection reset"))
    monkeypatch.setattr(api_mod, "call_fal_generate",
                        lambda *a, **k: (fake_img, None))
    img, err, provider = call_image_generate("key", "m", "p", "img.png")
    assert img is fake_img and provider == "fal"

def test_call_image_generate_google_success_is_google(monkeypatch):
    fake_img = object()
    monkeypatch.setattr(api_mod, "_load_config", lambda: {"image_provider": "google"})
    monkeypatch.setattr(api_mod, "call_gemini_generate", lambda *a, **k: (fake_img, None))
    img, err, provider = call_image_generate("key", "m", "p", "img.png")
    assert img is fake_img and provider == "google"

def test_call_image_generate_fal_route_is_fal(monkeypatch):
    fake_img = object()
    monkeypatch.setattr(api_mod, "_load_config", lambda: {"image_provider": "fal", "fal_api_key": "k"})
    monkeypatch.setattr(api_mod, "call_fal_generate", lambda *a, **k: (fake_img, None))
    img, err, provider = call_image_generate("key", "m", "p", "img.png")
    assert img is fake_img and provider == "fal"

def test_call_image_generate_google_fail_no_failover_is_google(monkeypatch):
    monkeypatch.setattr(api_mod, "_load_config",
                        lambda: {"image_provider": "google", "auto_failover": False})
    monkeypatch.setattr(api_mod, "call_gemini_generate",
                        lambda *a, **k: (None, "网络错误: reset"))
    img, err, provider = call_image_generate("key", "m", "p", "img.png")
    assert img is None and provider == "google"
