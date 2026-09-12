"""Fal provider implementation; no dependency on the public API facade."""
import os
import time
import random
import base64
import hashlib
import io as _io_mod
import re
import math
from typing import Optional, Tuple

from PIL import Image

import requests as _req
import urllib3
from urllib3.exceptions import ProtocolError as _ProtocolError

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ..config import FAL_MODEL_MAP, LEGACY_IMAGE_MODEL_ALIASES, logger, short_text, load_config
from .common import (
    EXPLICIT_RETRYABLE_HTTP, ambiguous_provider_error, attempt_event,
    is_safe_pre_submit_exception, safe_provider_error,
)

from .shared import _notify_stage, _read_image_b64, _redact_api_key, _retry_plan, _verify_arg

_FAL_RESOLUTIONS = {"1K", "2K", "4K"}

_FAL_ASPECT_RATIOS = {"auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"}

SD35_ENDPOINT = "fal-ai/stable-diffusion-v35-large"

SD35_IP_ADAPTER_PATH = "InstantX/SD3.5-Large-IP-Adapter"

SD35_IP_ADAPTER_WEIGHT = "ip-adapter.bin"

SD35_IMAGE_ENCODER = "google/siglip-so400m-patch14-384"

AURA_SR_ENDPOINT = "fal-ai/aura-sr"

def _file_to_data_uri(path: str) -> Optional[str]:
    """把本地图片读成 data URI(base64),用作 Fal 的 image_urls 输入。"""
    b64, mime = _read_image_b64(path)
    if b64 is None:
        return None
    return f"data:{mime};base64,{b64}"

def _pil_to_data_uri(image, fmt: str = "PNG", quality: int = 95) -> str:
    buf = _io_mod.BytesIO()
    if fmt.upper() == "PNG":
        image.convert("RGB").save(buf, format="PNG")
        mime = "image/png"
    else:
        image.convert("RGB").save(buf, format="JPEG", quality=quality)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"

def sd35_base_size(aspect_ratio: str) -> dict:
    """约 1MP、64 对齐的 SD3.5 基础画布；避免直接高分辨率扩散。"""
    try:
        a, b = (float(x) for x in str(aspect_ratio or "4:3").split(":", 1))
        ratio = a / b if a > 0 and b > 0 else 4 / 3
    except Exception:
        ratio = 4 / 3
    pixels = 1024 * 1024
    width = int(round(math.sqrt(pixels * ratio) / 64) * 64)
    height = int(round(math.sqrt(pixels / ratio) / 64) * 64)
    width = max(512, min(1536, width))
    height = max(512, min(1536, height))
    return {"width": width, "height": height}

def _call_fal_json(api_key: str, endpoint: str, payload: dict, *, on_stage=None,
                   should_cancel=None) -> Tuple[Optional[dict], Optional[str]]:
    """Fal 同步 JSON 端点薄封装；沿用本项目代理/TLS/有限重试语义。"""
    cfg = load_config()
    proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}
    max_attempts, backoffs = _retry_plan()
    try:
        max_attempts = max(1, min(max_attempts, int(cfg.get("fal_retry_attempts", 3))))
    except Exception:
        max_attempts = min(max_attempts, 3)
    last_err = ""
    for attempt in range(max_attempts):
        if should_cancel and should_cancel():
            return None, ambiguous_provider_error("已取消，服务端计费状态待确认", failure_code="fal_cancel_unconfirmed", attempts=[])
        if on_stage:
            _notify_stage(on_stage, "📡 连接中…" if attempt == 0 else f"🔁 网络重试 {attempt}/{max_attempts - 1}")
        try:
            response = _req.post(
                f"https://fal.run/{endpoint}", json=payload, headers=headers,
                timeout=(30, 600), proxies=proxies, verify=_verify_arg(cfg),
            )
            if response.status_code == 200:
                return response.json(), None
            try:
                detail = response.json()
                if isinstance(detail, dict):
                    detail = detail.get("detail") or detail.get("error") or detail.get("message") or detail
            except Exception:
                detail = response.text[:600]
            last_err = f"HTTP {response.status_code}: {short_text(detail, 600)}"
            if response.status_code not in (408, 409, 425, 429, 500, 502, 503, 504):
                return None, last_err
        except (_req.exceptions.Timeout, _req.exceptions.ConnectionError,
                _req.exceptions.ChunkedEncodingError, _ProtocolError) as ex:
            last_err = f"网络错误: {_redact_api_key(ex)}"
        except Exception as ex:
            logger.exception(f"[Fal] 未预期错误 endpoint={endpoint}")
            return None, f"网络错误: {_redact_api_key(ex)}"
        if attempt < max_attempts - 1:
            end = time.time() + backoffs[min(attempt, len(backoffs) - 1)] + random.uniform(0, 1.5)
            while time.time() < end:
                if should_cancel and should_cancel():
                    return None, ambiguous_provider_error("已取消，服务端计费状态待确认", failure_code="fal_cancel_unconfirmed", attempts=[])
                time.sleep(0.5)
    return None, last_err or "Fal 请求失败"

def _call_fal_queue_json(api_key: str, endpoint: str, payload: dict, *, on_stage=None,
                         should_cancel=None, resume_handle: Optional[dict] = None,
                         on_submitted=None) -> Tuple[Optional[dict], Optional[str]]:
    """Fal 持久队列：只提交一次，随后轮询同一 request_id，避免长连接断线后重复计费。"""
    if should_cancel and should_cancel() and not resume_handle:
        return None, safe_provider_error('已取消', failure_code='cancelled_before_submit', attempts=[])
    cfg = load_config()
    # Google 代理常会破坏 FAL 的大 POST/长轮询；SD 队列默认直连，确有需要再单配 fal_queue_proxy。
    proxy = str(cfg.get("fal_queue_proxy") or "").strip()
    session = _req.Session()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    else:
        session.trust_env = False
    try:
        headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}
        verify = _verify_arg(cfg)

        def _detail(response) -> str:
            try:
                value = response.json()
                if isinstance(value, dict):
                    value = value.get("detail") or value.get("error") or value.get("message") or value
                return short_text(value, 600)
            except Exception:
                return short_text(response.text, 600)

        queued = dict(resume_handle or {})
        if queued:
            _notify_stage(on_stage, "🔄 恢复已有 Fal 队列任务…")
        else:
            try:
                # Multi-channel 4K ERP edits carry several lossless PNG data URIs.
                # ``requests`` applies the connect timeout while writing the request
                # body as well, so the former 30 s value aborted a valid 10-20 MB
                # upload before Fal could return a durable request_id.  This remains
                # a single submit: a timeout is never retried automatically.
                try:
                    submit_timeout = max(
                        30, min(600, int(cfg.get('fal_queue_submit_timeout', 180))))
                except Exception:
                    submit_timeout = 180
                # 提交响应丢失时无法判断服务器是否已接单，因此绝不自动重交；由用户显式重试。
                response = session.post(
                    f"https://queue.fal.run/{endpoint}", json=payload, headers=headers,
                    timeout=(submit_timeout, max(120, submit_timeout)), verify=verify,
                )
                if response.status_code not in (200, 201, 202):
                    return None, f"队列提交 HTTP {response.status_code}: {_detail(response)}"
                queued = response.json()
            except Exception as ex:
                make_error = safe_provider_error if is_safe_pre_submit_exception(ex) else ambiguous_provider_error
                return None, make_error(f"队列提交网络错误（未自动重交）: {_redact_api_key(ex)}",
                                        failure_code='fal_queue_submit_unknown', attempts=[])

        status_url = str(queued.get("status_url") or "")
        response_url = str(queued.get("response_url") or "")
        cancel_url = str(queued.get("cancel_url") or "")
        if not status_url.startswith("https://queue.fal.run/") or not response_url.startswith("https://queue.fal.run/"):
            return None, ambiguous_provider_error("Fal 队列响应缺少有效状态地址", failure_code="fal_queue_handle_invalid", attempts=[])
        if not resume_handle and on_submitted:
            handle = {
                "endpoint": endpoint,
                "request_id": str(queued.get("request_id") or ""),
                "status_url": status_url,
                "response_url": response_url,
                "cancel_url": cancel_url,
                "submitted_at": time.time(),
            }
            try:
                on_submitted(handle)
            except Exception as ex:
                logger.warning(f"[Fal队列] 持久化请求句柄失败 endpoint={endpoint}: {ex}")
        try:
            deadline = time.time() + max(60, min(3600, int(cfg.get("fal_queue_timeout", 900))))
        except Exception:
            deadline = time.time() + 900
        last_status = ""
        poll_errors = 0
        while time.time() < deadline:
            if should_cancel and should_cancel():
                if cancel_url.startswith("https://queue.fal.run/"):
                    try:
                        session.post(cancel_url, headers=headers, timeout=(10, 30), verify=verify)
                    except Exception as ex:
                        logger.debug(f"[Fal队列] 取消请求发送失败(尽力而为): {ex}")
                return None, ambiguous_provider_error("已取消，服务端计费状态待确认", failure_code="fal_cancel_unconfirmed", attempts=[])
            try:
                status_response = session.get(
                    status_url, params={"logs": 1}, headers=headers,
                    timeout=(15, 45), verify=verify,
                )
                # 排队/推理中 REST 状态接口使用 202；完成后使用 200。
                if status_response.status_code not in (200, 202):
                    return None, ambiguous_provider_error(f"队列状态 HTTP {status_response.status_code}: {_detail(status_response)}", failure_code="fal_queue_poll_failed", attempts=[])
                status_data = status_response.json()
                status = str(status_data.get("status") or "").upper()
                poll_errors = 0
                if status != last_status and on_stage:
                    label = {"IN_QUEUE": "⏳ Fal 排队中…", "IN_PROGRESS": "🎨 Fal 推理中…"}.get(status)
                    if label:
                        _notify_stage(on_stage, label)
                last_status = status
                if status == "COMPLETED":
                    result_response = session.get(
                        response_url, headers=headers, timeout=(30, 180), verify=verify,
                    )
                    if result_response.status_code != 200:
                        return None, ambiguous_provider_error(f"队列取结果 HTTP {result_response.status_code}: {_detail(result_response)}", failure_code="fal_queue_result_failed", attempts=[])
                    return result_response.json(), None
                if status in ("FAILED", "CANCELLED"):
                    return None, f"Fal 队列任务{status}: {short_text(status_data, 600)}"
            except Exception as ex:
                poll_errors += 1
                if poll_errors >= 5:
                    return None, ambiguous_provider_error(f"队列状态网络错误: {_redact_api_key(ex)}", failure_code="fal_queue_poll_failed", attempts=[])
            time.sleep(1.5)
        return None, ambiguous_provider_error("Fal 队列等待超时；任务可能仍在服务端运行，请稍后按原任务重试", failure_code="fal_queue_timeout", attempts=[])
    finally:
        close = getattr(session, 'close', None)
        if close:
            try:
                close()
            except Exception as error:
                logger.debug('[Fal队列] 连接清理失败: %s', _redact_api_key(error))


_FAL_MEDIA_MAX_BYTES = 128 * 1024 * 1024

def _fal_media_routes(cfg: dict, *, direct: bool, attempts: int) -> list[tuple[str, str]]:
    """Build a deterministic download route plan without consulting ambient proxy env vars."""
    general_proxy = str(cfg.get("proxy") or "").strip()
    queue_proxy = str(cfg.get("fal_queue_proxy") or "").strip()
    primary_proxy = queue_proxy if direct else general_proxy
    primary = (("fal_queue_proxy" if direct else "proxy"), primary_proxy) if primary_proxy else ("direct", "")
    alternatives: list[tuple[str, str]] = []
    for candidate in (
        ("proxy", general_proxy),
        ("fal_queue_proxy", queue_proxy),
        ("direct", ""),
    ):
        if candidate != primary and candidate not in alternatives and (candidate[1] or candidate[0] == "direct"):
            alternatives.append(candidate)
    # Give the configured primary route one immediate retry before switching;
    # after all alternatives, return to the primary because transient tunnels
    # often recover within a few seconds.
    plan = [primary, primary, *alternatives, primary]
    if not plan:
        plan = [("direct", "")]
    while len(plan) < attempts:
        plan.extend([primary, *alternatives])
    return plan[:attempts]

def _download_fal_media(url: str, *, direct: bool = False, on_stage=None,
                        should_cancel=None) -> bytes:
    """Reliably read one already-generated Fal asset without another paid submit.

    The byte buffer survives transport failures.  When Fal advertises byte
    ranges, the next attempt resumes from the last complete chunk; otherwise a
    200 response safely restarts the same URL from byte zero.  Attempts may
    alternate the configured proxy and direct routes, but they never call a Fal
    generation endpoint.
    """
    cfg = load_config()
    try:
        attempts = max(2, min(6, int(cfg.get("fal_media_download_attempts", 4))))
    except Exception:
        attempts = 4
    try:
        chunk_size = max(64 * 1024, min(1024 * 1024,
                         int(cfg.get("fal_media_chunk_bytes", 256 * 1024))))
    except Exception:
        chunk_size = 256 * 1024
    configured_backoffs = cfg.get("fal_media_retry_backoffs")
    retry_backoffs = (
        [max(0.0, min(30.0, float(value))) for value in configured_backoffs[:5]]
        if isinstance(configured_backoffs, list) and configured_backoffs else
        [0.75, 1.5, 3.0, 5.0, 8.0]
    )
    routes = _fal_media_routes(cfg, direct=direct, attempts=attempts)
    verify = _verify_arg(cfg)
    payload = bytearray()
    expected_total: Optional[int] = None
    errors: list[str] = []

    for attempt, (route_label, proxy) in enumerate(routes, 1):
        if should_cancel and should_cancel():
            raise RuntimeError("已取消")
        if attempt > 1:
            _notify_stage(on_stage, f"🔄 恢复下载 Fal 结果 {attempt}/{attempts}…")
        session = _req.Session()
        session.trust_env = False
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        response = None
        offset = len(payload)
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            response = session.get(
                url, headers=headers, stream=True,
                timeout=(30, 300), verify=verify,
            )
            response.raise_for_status()
            if offset and response.status_code == 206:
                content_range = str(response.headers.get("Content-Range") or "")
                match = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range, re.I)
                if not match or int(match.group(1)) != offset:
                    raise _req.exceptions.ChunkedEncodingError(
                        f"Fal Range 响应起点不匹配: expected={offset}, got={content_range or 'missing'}")
                if match.group(3) != "*":
                    expected_total = int(match.group(3))
            elif response.status_code == 200:
                # The host ignored Range. Restarting the download is safe: this
                # is the same immutable result URL and creates no provider call.
                if offset:
                    payload.clear()
                    offset = 0
                content_length = response.headers.get("Content-Length")
                expected_total = int(content_length) if content_length else None
            elif response.status_code == 206:
                content_range = str(response.headers.get("Content-Range") or "")
                match = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range, re.I)
                if match and match.group(3) != "*":
                    expected_total = int(match.group(3))

            for chunk in response.iter_content(chunk_size=chunk_size):
                if should_cancel and should_cancel():
                    raise RuntimeError("已取消")
                if not chunk:
                    continue
                payload.extend(chunk)
                if len(payload) > _FAL_MEDIA_MAX_BYTES:
                    raise ValueError("Fal 结果图片超过 128 MiB 安全上限")

            if expected_total is not None and len(payload) != expected_total:
                raise _req.exceptions.ChunkedEncodingError(
                    f"Fal 结果下载不完整: {len(payload)}/{expected_total} bytes")
            if not payload:
                raise _req.exceptions.ChunkedEncodingError("Fal 结果下载为空")
            logger.info(
                "[Fal结果下载] success route=%s attempt=%s/%s bytes=%s resumed_from=%s",
                route_label, attempt, attempts, len(payload), offset,
            )
            return bytes(payload)
        except Exception as ex:
            error = f"{route_label}: {_redact_api_key(ex)}"
            errors.append(error)
            logger.warning(
                "[Fal结果下载] failed route=%s attempt=%s/%s received=%s expected=%s error=%s",
                route_label, attempt, attempts, len(payload), expected_total,
                _redact_api_key(ex),
            )
            if str(ex) == "已取消":
                raise
            if attempt < attempts:
                delay = retry_backoffs[min(attempt - 1, len(retry_backoffs) - 1)]
                deadline = time.time() + delay
                while time.time() < deadline:
                    if should_cancel and should_cancel():
                        raise RuntimeError("已取消")
                    time.sleep(min(0.25, max(0.0, deadline - time.time())))
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            try:
                session.close()
            except Exception:
                pass
    raise RuntimeError("；".join(errors[-attempts:]) or "Fal 结果下载失败")

def _fal_image_from_result(data: dict, *, plural: bool = True, direct: bool = False,
                           on_stage=None, should_cancel=None):
    item = ((data.get("images") or [None])[0] if plural else data.get("image")) if isinstance(data, dict) else None
    url = item.get("url") if isinstance(item, dict) else None
    if not url:
        return None, ambiguous_provider_error("API 未返回图片", failure_code="fal_result_missing", attempts=[])
    try:
        if url.startswith("data:"):
            raw = base64.b64decode(url.split(",", 1)[1])
        else:
            raw = _download_fal_media(
                url, direct=direct, on_stage=on_stage,
                should_cancel=should_cancel,
            )
        image = Image.open(_io_mod.BytesIO(raw)); image.load()
        return image, None
    except Exception as ex:
        return None, ambiguous_provider_error(f"解码失败: {_redact_api_key(ex)}", failure_code="fal_result_decode_failed", attempts=[])

def call_fal_sd35_generate(api_key: str, positive_prompt: str, negative_prompt: str,
                           floor_image_path: str, aspect_ratio: str = "4:3", *,
                           seed=None, steps: int = 28, guidance_scale: float = 3.5,
                           reference_strength: float = 0.5, on_stage=None,
                           should_cancel=None, queue_handle=None, on_queue_submitted=None):
    """Fal SD3.5 Large + InstantX IP-Adapter。返回 (PIL, error, seed)。"""
    if queue_handle:
        payload = {}  # Recovery does not need the original input files.
    else:
        ref_uri = _file_to_data_uri(floor_image_path)
        if not ref_uri:
            return None, "地板小样不存在或无法读取", seed
        payload = {
            "prompt": positive_prompt,
            "negative_prompt": negative_prompt,
            "image_size": sd35_base_size(aspect_ratio),
            "num_inference_steps": max(10, min(50, int(steps))),
            "guidance_scale": max(1.0, min(10.0, float(guidance_scale))),
            "num_images": 1,
            "enable_safety_checker": True,
            "output_format": "png",
            "ip_adapter": {
                "path": SD35_IP_ADAPTER_PATH,
                "weight_name": SD35_IP_ADAPTER_WEIGHT,
                "image_encoder_path": SD35_IMAGE_ENCODER,
                "image_url": ref_uri,
                "scale": max(0.1, min(1.0, float(reference_strength))),
            },
        }
    if seed is not None:
        payload["seed"] = int(seed)
    _notify_stage(on_stage, "🎨 SD 3.5 生成中…")
    data, err = _call_fal_queue_json(
        api_key, SD35_ENDPOINT, payload, on_stage=on_stage, should_cancel=should_cancel,
        resume_handle=queue_handle, on_submitted=on_queue_submitted)
    if err:
        return None, err, seed
    image, decode_err = _fal_image_from_result(data, plural=True, direct=True, on_stage=on_stage, should_cancel=should_cancel)
    return image, decode_err, data.get("seed", seed) if data else seed

def call_fal_aura_upscale(api_key: str, image, *, on_stage=None, should_cancel=None,
                          queue_handle=None, on_queue_submitted=None):
    """AuraSR 4× 保守超分。返回 (PIL, error)。"""
    _notify_stage(on_stage, "🔎 4K 超分中…")
    if queue_handle:
        payload = {}
    else:
        payload = {
            "image_url": _pil_to_data_uri(image),
            "upscale_factor": 4,
            "overlapping_tiles": True,
            "checkpoint": "v2",
        }
    data, err = _call_fal_queue_json(
        api_key, AURA_SR_ENDPOINT, payload, on_stage=on_stage, should_cancel=should_cancel,
        resume_handle=queue_handle, on_submitted=on_queue_submitted)
    if err:
        return None, err
    return _fal_image_from_result(data, plural=False, direct=True)

def call_fal_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                      image_size: str = "4K", aspect_ratio: str = "4:3",
                      room_image_path: Optional[str] = None,
                      style_ref_image_path: Optional[str] = None,
                      on_stage=None, should_cancel=None,
                      bevel_ref_image_path: Optional[str] = None,
                      input_image_paths: Optional[list[str]] = None) -> Tuple[Optional[object], Optional[str]]:
    """经 Fal 路由调用 Nano Banana 系列(图生图 /edit 端点)。

    与 call_gemini_generate 同契约:返回 (PIL.Image, None) 或 (None, 错误字符串),并支持 on_stage 回调。
    同一个 Gemini 模型,只换更稳的线路(国内→Fal→Google),保真/4K 不变。
    - model_id 仍用 Gemini 的 id,内部经 FAL_MODEL_MAP 映射到 Fal endpoint。
    - 用 sync_mode=true:响应内联返回 data URI 图,整次生图只需一次请求(软路由下最稳)。
    - should_cancel(): 可选回调,返回 True 表示任务已被用户取消 → 立刻停止后续重试,
      不再发起新的(会计费的)Fal 请求。已在途的那一次无法召回,但本次若已拿到图仍会正常返回。
    """
    def _stage(txt):
        _notify_stage(on_stage, txt)

    cfg = load_config()
    custom_fal_map = cfg.get("fal_model_map")
    custom_fal_map = custom_fal_map if isinstance(custom_fal_map, dict) else {}
    # 用户覆盖优先；若覆盖仍以旧 Preview ID 为键，也要能作用于新 Stable ID。
    legacy_id = next(
        (old for old, stable in LEGACY_IMAGE_MODEL_ALIASES.items()
         if stable == model_id),
        None,
    )
    endpoint = custom_fal_map.get(model_id)
    if not endpoint and legacy_id:
        endpoint = custom_fal_map.get(legacy_id)
    if not endpoint:
        endpoint = FAL_MODEL_MAP.get(model_id)
    if not endpoint and model_id in LEGACY_IMAGE_MODEL_ALIASES:
        stable_id = LEGACY_IMAGE_MODEL_ALIASES[model_id]
        endpoint = custom_fal_map.get(stable_id) or FAL_MODEL_MAP.get(stable_id)
    if not endpoint:
        logger.error(f"[Fal生成] 未知模型,无 Fal 端点映射: model={model_id}")
        return None, f"该模型未配置 Fal 端点: {model_id}"

    logger.info(
        f"[Fal生成] start model={model_id} -> {endpoint}, size={image_size}, ar={aspect_ratio}, "
        f"floor={image_path}, room_ref={bool(room_image_path)}, style_ref={bool(style_ref_image_path)}, "
        f"prompt_len={len(prompt_text or '')}, prompt_sha256={hashlib.sha256((prompt_text or '').encode()).hexdigest()[:12]}"
    )
    if input_image_paths is None and not os.path.exists(image_path):
        logger.error(f"[Fal生成] 素材图不存在: {image_path}")
        return None, f"素材图不存在: {image_path}"

    # 自由模式严格按 Slot 顺序；旧模式保持既有顺序。
    image_urls = []
    ordered_paths = (list(input_image_paths) if input_image_paths is not None else
                     [style_ref_image_path, room_image_path, bevel_ref_image_path, image_path])
    if input_image_paths is not None:
        missing = [p for p in ordered_paths if not p or not os.path.exists(p)]
        if missing:
            return None, f"素材图不存在: {missing[0]}"
    for p in ordered_paths:
        uri = _file_to_data_uri(p)
        if uri:
            image_urls.append(uri)
    if not image_urls:
        return None, "无可用的输入图片"

    resolution = image_size if image_size in _FAL_RESOLUTIONS else "1K"
    ar = aspect_ratio if aspect_ratio in _FAL_ASPECT_RATIOS else "auto"
    payload = {
        "prompt": prompt_text,
        "image_urls": image_urls,
        "num_images": 1,
        "output_format": "png",
        "aspect_ratio": ar,
        "resolution": resolution,
        "sync_mode": True,   # 内联返回 data URI,只需一次请求
    }
    url = f"https://fal.run/{endpoint}"
    headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}

    proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try: total_deadline = float(cfg.get("gen_total_deadline", 600))
    except Exception: total_deadline = 600.0

    # Fal 单独的重试上限(默认 3,可经 engine_config.json 的 fal_retry_attempts 调)。
    # 比 Google 直连(默认 6)更克制:软路由把连接掐断时 Fal 服务端往往已经收到并在跑,
    # 盲目重发会让同一张图被重复计费,故少重试以省钱。
    max_attempts, backoffs = _retry_plan()
    try: fal_attempts = int(cfg.get("fal_retry_attempts", 3))
    except Exception: fal_attempts = 3
    max_attempts = max(1, min(max_attempts, fal_attempts))
    RETRYABLE = (_req.exceptions.SSLError, _req.exceptions.ConnectionError,
                 _req.exceptions.ChunkedEncodingError, _ProtocolError)

    def _sleep_backoff(attempt):
        # 取消感知:退避期间每 0.5s 检查一次,取消则立即返回(避免白等 + 不再触发下一次请求)
        d = backoffs[min(attempt, len(backoffs) - 1)] + random.uniform(0, 1.5)
        end = time.time() + d
        while time.time() < end:
            if should_cancel and should_cancel():
                return
            time.sleep(0.5)

    def _decode_image(url_or_uri):
        """把 Fal 返回的 images[].url(sync 下是 data URI,否则是 http URL)解成 PIL。"""
        if isinstance(url_or_uri, str) and url_or_uri.startswith("data:"):
            b64 = url_or_uri.split(",", 1)[1] if "," in url_or_uri else ""
            raw = base64.b64decode(b64)
        else:
            r = _req.get(url_or_uri, timeout=(30, 300), proxies=proxies, verify=_verify_arg(cfg))
            r.raise_for_status()
            raw = r.content
        img = Image.open(_io_mod.BytesIO(raw)); img.load()
        return img

    call_t0 = time.time()
    last_err = None
    attempt_events = []
    for attempt in range(max_attempts):
        if should_cancel and should_cancel():
            logger.info(f"[Fal生成] 任务已取消,停止重试(不再发起新请求) model={model_id}")
            last_err = last_err or "已取消"
            break
        if time.time() - call_t0 >= total_deadline:
            logger.error(f"[Fal生成] 总时限 {total_deadline:.0f}s 到,放弃 model={model_id}")
            last_err = last_err or "超过总时限"
            break
        _stage("📡 连接中…" if attempt == 0 else f"🔁 网络重试 {attempt}/{max_attempts - 1}")
        attempt_started = time.time()
        try:
            _stage("🎨 生成中…")
            resp = _req.post(url, json=payload, headers=headers,
                             timeout=(30, 300), proxies=proxies, verify=_verify_arg(cfg))
            if resp.status_code != 200:
                try:
                    err_info = resp.json()
                    err_msg = (err_info.get('detail') or err_info.get('error') or err_info.get('message')
                               or resp.text[:400]) if isinstance(err_info, dict) else resp.text[:400]
                except Exception:
                    err_msg = resp.text[:400]
                if resp.status_code in EXPLICIT_RETRYABLE_HTTP:
                    last_err = f"HTTP {resp.status_code}: {err_msg}"
                    attempt_events.append(attempt_event(
                        attempt + 1, 'response', attempt_started,
                        outcome='safe_failure', http_status=resp.status_code))
                    if attempt < max_attempts - 1:
                        logger.warning(f"[Fal生成] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
                        _sleep_backoff(attempt); continue
                    return None, safe_provider_error(
                        f"HTTP {resp.status_code}: {err_msg}",
                        failure_code='fal_retryable_http_exhausted', attempts=attempt_events)
                logger.error(f"[Fal生成] HTTP失败 model={model_id}, status={resp.status_code}, err={short_text(err_msg, 800)}")
                return None, f"HTTP {resp.status_code}: {err_msg}"

            _stage("🎨 渲染中…")
            try:
                data = resp.json()
            except Exception as exc:
                attempt_events.append(attempt_event(
                    attempt + 1, 'response', attempt_started, outcome='ambiguous_failure', http_status=200))
                return None, ambiguous_provider_error(
                    f"结果状态不确定：Fal 成功响应解析失败: {_redact_api_key(exc)}",
                    failure_code='fal_response_truncated', attempts=attempt_events)
            images = data.get("images") or []
            if images and images[0].get("url"):
                try:
                    pil_img = _decode_image(images[0]["url"])
                except Exception as e:
                    logger.exception(f"[Fal生成] 图片解码/下载失败 model={model_id}")
                    attempt_events.append(attempt_event(
                        attempt + 1, 'decode', attempt_started, outcome='ambiguous_failure', http_status=200))
                    return None, ambiguous_provider_error(
                        f"结果状态不确定：Fal 图片下载或解码失败: {_redact_api_key(e)}",
                        failure_code='fal_image_decode_failed', attempts=attempt_events)
                logger.info(f"[Fal生成] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                return pil_img, None
            logger.error(f"[Fal生成] API未返回图片 model={model_id}, resp={short_text(data, 600)}")
            attempt_events.append(attempt_event(
                attempt + 1, 'response', attempt_started, outcome='ambiguous_failure', http_status=200))
            return None, ambiguous_provider_error(
                "结果状态不确定：Fal 返回成功状态但没有可用图片",
                failure_code='fal_empty_success_response', attempts=attempt_events)

        except _req.exceptions.ConnectTimeout:
            last_err = "连接超时"
            attempt_events.append(attempt_event(
                attempt + 1, 'connect', attempt_started, outcome='safe_failure'))
            logger.warning(f"[Fal生成] 连接超时(确认未建立请求) attempt={attempt+1}/{max_attempts} model={model_id}")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt); continue
        except _req.exceptions.Timeout as exc:
            attempt_events.append(attempt_event(
                attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure'))
            return None, ambiguous_provider_error(
                f"结果状态不确定：Fal 请求提交后超时，系统未自动重提: {_redact_api_key(exc)}",
                failure_code='fal_read_timeout_unknown', attempts=attempt_events)
        except RETRYABLE as e:
            last_err = e
            logger.warning(f"[Fal生成] 网络异常 attempt={attempt+1}/{max_attempts} model={model_id}: {_redact_api_key(e)}")
            if is_safe_pre_submit_exception(e):
                attempt_events.append(attempt_event(
                    attempt + 1, 'connect', attempt_started, outcome='safe_failure'))
                if attempt < max_attempts - 1:
                    _sleep_backoff(attempt); continue
            else:
                attempt_events.append(attempt_event(
                    attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure'))
                return None, ambiguous_provider_error(
                    f"结果状态不确定：Fal 请求提交后连接中断，系统未自动重提: {_redact_api_key(e)}",
                    failure_code='fal_connection_lost_unknown', attempts=attempt_events)
        except Exception as e:
            logger.exception(f"[Fal生成] 未预期错误 model={model_id}")
            return None, f"网络错误: {_redact_api_key(e)}"

    logger.error(f"[Fal生成] 网络重试失败 model={model_id}: {_redact_api_key(last_err)}")
    return None, safe_provider_error(
        f"网络错误: {_redact_api_key(last_err)}",
        failure_code='fal_safe_retries_exhausted', attempts=attempt_events)

generate = call_fal_generate
queue = _call_fal_queue_json
