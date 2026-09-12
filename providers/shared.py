"""Shared provider implementation; no dependency on the public API facade."""
import os
import time
import json
import random
import threading
import base64
import hashlib
import io as _io_mod
import re
import math
import uuid
from typing import Optional, Tuple

from PIL import Image

import requests as _req
import urllib3
from urllib3.exceptions import ProtocolError as _ProtocolError

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ..config import (
    BASE_DIR, MAIN_OUTPUT_DIR, CONFIG_FILE,
    GEMINI_MODEL_MAP, FAL_MODEL_MAP, LEGACY_IMAGE_MODEL_ALIASES,
    LITE_PREVIEW_MODEL, DEFAULT_IMAGE_PROVIDER,
    logger, short_text, load_config, save_config,
    get_speed_profile_params,
    get_text_models, get_gen_sampling, get_omakase_gemini_model,
    get_inpaint_provider, get_comfyui_settings, get_inpaint_remove_prompt,
    get_inpaint_models,
)
from ..records import (
    img_to_b64, b64_to_pil, save_api_result_jpg, api_write_to_record,
)
from .common import (
    EXPLICIT_RETRYABLE_HTTP, ambiguous_provider_error, attempt_event,
    is_safe_pre_submit_exception, safe_provider_error,
)



def _notify_stage(on_stage, txt) -> None:
    """进度回调守护:回调只是 UI 装饰性通知,它抛任何异常都不允许拖垮付费生图主流程。"""
    if on_stage:
        try:
            on_stage(txt)
        except Exception as ex:
            logger.debug(f"[进度回调] 忽略回调异常: {ex}")

_IMAGE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

_UPLOAD_MAX_SIDE_DEFAULT = 4096

_UPLOAD_JPEG_QUALITY_DEFAULT = 92

_UPLOAD_THRESHOLD_MB_DEFAULT = 4.0

def _read_image_b64(path: Optional[str]) -> Tuple[Optional[str], str]:
    """读本地图片为 (base64, mime)；路径为空或文件不存在返回 (None, "image/jpeg")。

    体积超阈值(默认4MB)的图会先转成 JPEG、长边限到上限(默认4096)再编码，显著减小
    上传体积，避免走代理时上传写超时；小图原样返回(不重压、不丢质量、保留原 mime)。
    """
    if not path or not os.path.exists(path):
        return None, "image/jpeg"
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    mime = _IMAGE_MIME.get(ext, "image/jpeg")
    with open(path, 'rb') as f:
        raw = f.read()

    try:
        cfg = load_config()
        max_side = int(cfg.get("upload_max_side", _UPLOAD_MAX_SIDE_DEFAULT))
        quality = int(cfg.get("upload_jpeg_quality", _UPLOAD_JPEG_QUALITY_DEFAULT))
        threshold = float(cfg.get("upload_compress_threshold_mb", _UPLOAD_THRESHOLD_MB_DEFAULT)) * 1024 * 1024
    except Exception:
        max_side, quality, threshold = (_UPLOAD_MAX_SIDE_DEFAULT, _UPLOAD_JPEG_QUALITY_DEFAULT,
                                        _UPLOAD_THRESHOLD_MB_DEFAULT * 1024 * 1024)

    if len(raw) > threshold:
        try:
            im = Image.open(_io_mod.BytesIO(raw))
            if im.mode in ("RGBA", "LA", "P"):  # JPEG 不支持透明通道 → 拍平到白底
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side))
            buf = _io_mod.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            out = buf.getvalue()
            logger.info(f"[上传压缩] {os.path.basename(path)}: {len(raw)/1024/1024:.1f}MB "
                        f"→ {len(out)/1024/1024:.1f}MB JPEG({im.width}x{im.height})")
            return base64.b64encode(out).decode('utf-8'), "image/jpeg"
        except Exception:
            logger.exception(f"[上传压缩] 失败，回退原图 path={path}")
    return base64.b64encode(raw).decode('utf-8'), mime

def _redact_api_key(text):
    return re.sub(r'([?&]key=)[^&\s)]+', r'\1***', str(text or ""))

def _verify_arg(cfg=None):
    """返回传给 requests 的 verify 值（HTTPS 证书校验）。

    - tls_verify 显式为 False → False（坏网络/会拦 HTTPS 的代理上关掉校验）
    - 否则（默认 True）配了存在的 tls_ca_bundle → 返回该 CA 路径
    - 否则 → True：用系统/requests 默认 CA
    传入已加载的 cfg 可复用，避免热路径重复读盘。
    """
    cfg = cfg if cfg is not None else load_config()
    if not bool(cfg.get("tls_verify", True)):
        return False
    ca = (cfg.get("tls_ca_bundle") or "").strip()
    if ca and os.path.exists(ca):
        return ca
    return True

def _extract_thought_title(text: str) -> str:
    """从思考文本里抽取 **加粗标题** 作为简短状态；没有标题则取前 40 字。"""
    if not text:
        return ""
    m = re.search(r'\*\*(.+?)\*\*', text)
    if m:
        return m.group(1).strip()
    t = re.sub(r'\s+', ' ', text).strip()
    return t[:40]

def _retry_plan() -> Tuple[int, list]:
    """重试参数：基础值取自当前 speed_profile(fast=3次/[1,2,4]，resilient=8次/[2,4,7,10,15])。
    engine_config.json 里若显式写了 retry_backoffs / retry_attempts 则覆盖 profile(高级微调)。
    (本函数同时被 Gemini 与 Fal 路径复用；Fal 另有自己的 fal_attempts 上限。)"""
    cfg = load_config()
    base = get_speed_profile_params(cfg)
    backoffs = cfg.get("retry_backoffs") or base["retry_backoffs"]
    try:
        backoffs = [float(x) for x in backoffs]
    except Exception:
        backoffs = [float(x) for x in base["retry_backoffs"]]
    try:
        attempts = int(cfg.get("retry_attempts", base["retry_attempts"]))
    except Exception:
        attempts = base["retry_attempts"]
    return max(1, attempts), backoffs

def _image_thinking_config(model_id: str, *, cinematic_mode: bool, include_thoughts: bool) -> dict:
    """构造兼容当前模型族的 thinkingConfig；独立成纯函数便于契约测试。"""
    out = {}
    mid = (model_id or '').lower()
    if cinematic_mode and '3.1-flash-image' in mid and 'lite' not in mid:
        out["thinkingLevel"] = "HIGH"
    if include_thoughts:
        out["includeThoughts"] = True
    return out


