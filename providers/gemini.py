"""Gemini provider implementation; no dependency on the public API facade."""
import os
import time
import json
import random
import threading
import base64
import hashlib
import io as _io_mod
from typing import Optional, Tuple

from PIL import Image

import requests as _req
import urllib3
from urllib3.exceptions import ProtocolError as _ProtocolError

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ..config import logger, short_text, load_config, get_speed_profile_params, get_gen_sampling
from .common import (
    EXPLICIT_RETRYABLE_HTTP, ambiguous_provider_error, attempt_event,
    is_safe_pre_submit_exception, safe_provider_error,
)

from .shared import _extract_thought_title, _image_thinking_config, _notify_stage, _read_image_b64, _redact_api_key, _retry_plan, _verify_arg

def call_gemini_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                         image_size: str = "4K", aspect_ratio: str = "4:3",
                         room_image_path: Optional[str] = None,
                         style_ref_image_path: Optional[str] = None,
                         on_stage=None, should_cancel=None,
                         bevel_ref_image_path: Optional[str] = None,
                         input_image_paths: Optional[list[str]] = None,
                         cinematic_mode: bool = False) -> Tuple[Optional[object], Optional[str]]:
    """流式文生图/图生图。

    - Pro 模型(model_id 含 'pro')额外请求 includeThoughts，实时回传思考标题。
    - on_stage(text): 可选回调，在「本(worker)线程」内被调用，用于把实时状态
      （📡连接中 / 🧠思考标题 / 🎨渲染中 / 🔁网络重试 N/M）写回 UI。必须自身吞异常。
    - 网络中断（含流式中途 IncompleteRead）按指数退避重试。
    - should_cancel(): 可选回调，返回 True 表示任务已取消 → 立即停止后续重试，不再发起新请求。
    - 返回 (PIL.Image, None) 或 (None, 错误字符串)，契约与旧版一致。
    """
    def _stage(txt):
        _notify_stage(on_stage, txt)

    logger.info(
        f"[API生成] start model={model_id}, size={image_size}, ar={aspect_ratio}, "
        f"floor={image_path}, room_ref={bool(room_image_path)}, style_ref={bool(style_ref_image_path)}, "
        f"prompt_len={len(prompt_text or '')}, prompt_sha256={hashlib.sha256((prompt_text or '').encode()).hexdigest()[:12]}"
    )
    parts = [{"text": prompt_text}]
    if input_image_paths is not None:
        ordered = list(input_image_paths)
        missing = [p for p in ordered if not p or not os.path.exists(p)]
        if missing:
            logger.error(f"[API生成] 自由素材图不存在: {missing[0]}")
            return None, f"素材图不存在: {missing[0]}"
        for path in ordered:
            data, mime = _read_image_b64(path)
            if not data:
                return None, f"素材图读取失败: {path}"
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
    else:
        if not os.path.exists(image_path):
            logger.error(f"[API生成] 素材图不存在: {image_path}")
            return None, f"素材图不存在: {image_path}"
        floor_b64, floor_mime = _read_image_b64(image_path)
        room_b64, room_mime = _read_image_b64(room_image_path)
        sref_b64, sref_mime = _read_image_b64(style_ref_image_path)
        # 圆弧倒角参考图：只供模型参考板边倒角形状。放在地板小样之前。
        bevel_b64, bevel_mime = _read_image_b64(bevel_ref_image_path)
        if sref_b64: parts.append({"inlineData": {"mimeType": sref_mime, "data": sref_b64}})
        if room_b64: parts.append({"inlineData": {"mimeType": room_mime, "data": room_b64}})
        if bevel_b64: parts.append({"inlineData": {"mimeType": bevel_mime, "data": bevel_b64}})
        parts.append({"inlineData": {"mimeType": floor_mime, "data": floor_b64}})

    cfg = load_config(); proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None

    # 默认走【非流式】generateContent —— 实测在软路由/透明代理下又快又稳(单图 ~48s)。
    # 流式 streamGenerateContent 能拿实时思考，但该网络下慢 ~9 倍且长连接易被重置，
    # 故设为可选：engine_config.json 里 "use_streaming": true 才启用流式 + 思考显示。
    use_streaming = bool(cfg.get("use_streaming", False))
    wants_thoughts = use_streaming and ('pro' in (model_id or '').lower())
    if use_streaming:
        gen_cfg = {
            "responseModalities": ["TEXT", "IMAGE"] if wants_thoughts else ["IMAGE"],
            "imageConfig": {"imageSize": image_size, "aspectRatio": aspect_ratio},
        }
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model_id}:streamGenerateContent?alt=sse&key={api_key}")
    else:
        gen_cfg = {"responseModalities": ["IMAGE"],
                   "imageConfig": {"imageSize": image_size, "aspectRatio": aspect_ratio}}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model_id}:generateContent?key={api_key}")
    # B2 默认 minimal thinking；电影真实感任务显式提高构图推理。
    # Pro 自带深度图像思考，不发送可能不兼容的 thinkingLevel，只保留原有 thoughts 展示开关。
    thinking_cfg = _image_thinking_config(
        model_id,
        cinematic_mode=cinematic_mode,
        include_thoughts=wants_thoughts,
    )
    if thinking_cfg:
        gen_cfg["thinkingConfig"] = thinking_cfg
    # 采样旋钮(opt-in)：engine_config.json 显式配了 gen_temperature/gen_seed 才注入；
    # 缺省返回 {} → gen_cfg 一字不变。流式/非流式共用，重试原样重发自动带上。
    _samp = get_gen_sampling()
    if _samp:
        gen_cfg.update(_samp)
        logger.info(f"[API生成] 采样旋钮生效 generationConfig+={_samp}")
    payload = {"contents": [{"parts": parts}], "generationConfig": gen_cfg}

    max_attempts, backoffs = _retry_plan()
    RETRYABLE = (_req.exceptions.SSLError, _req.exceptions.ConnectionError,
                 _req.exceptions.ChunkedEncodingError, _ProtocolError)

    def _sleep_backoff(attempt):
        # 取消感知:退避期间每 0.5s 检查一次,取消则立即返回(不再触发下一次请求)
        d = backoffs[min(attempt, len(backoffs) - 1)] + random.uniform(0, 1.5)
        end = time.time() + d
        while time.time() < end:
            if should_cancel and should_cancel():
                return
            time.sleep(0.5)

    # 硬性时限：防"半死连接"无限挂起。基础值取自当前 speed_profile(可被同名显式键覆盖)。
    #   idle_deadline: 连接 N 秒收不到任何新数据(行)即判假死、强制关闭。
    #     合法渲染的静默期实测最长 ~190s，故 fast/resilient 都保持 240s，留足余量不误杀。
    #   total_deadline: 整次调用(含所有重试)的墙钟上限，到点放弃、释放队列槽位。
    #     fast=300s(只够 1 次完整 4K 渲染 + 几次快速失败，让坏网络快点报错)；resilient=600s(死磕自愈)。
    _base = get_speed_profile_params(cfg)
    try: idle_deadline = float(cfg.get("gen_idle_deadline", _base["gen_idle_deadline"]))
    except Exception: idle_deadline = float(_base["gen_idle_deadline"])
    try: total_deadline = float(cfg.get("gen_total_deadline", _base["gen_total_deadline"]))
    except Exception: total_deadline = float(_base["gen_total_deadline"])
    call_t0 = time.time()

    last_err = None
    attempt_events = []
    for attempt in range(max_attempts):
        if should_cancel and should_cancel():
            logger.info(f"[API生成] 任务已取消,停止重试(不再发起新请求) model={model_id}")
            last_err = last_err or "已取消"
            break
        if time.time() - call_t0 >= total_deadline:
            logger.error(f"[API生成] 总时限 {total_deadline:.0f}s 到，放弃 model={model_id}")
            last_err = last_err or "超过总时限"
            break
        _stage("📡 连接中…" if attempt == 0 else f"🔁 网络重试 {attempt}/{max_attempts - 1}")
        attempt_started = time.time()
        resp = None
        _wd_stop = threading.Event()
        _wd_fired = [False]
        _last_data = [time.time()]
        try:
            if not use_streaming:
                # ── 非流式（默认）：一次性拿全图，软路由/透明代理下又快又稳 ──
                _stage("🎨 生成中…")  # post 会阻塞至整图返回(~60s)，先把卡片置为生成中
                resp = _req.post(url, json=payload, timeout=(30, 300), proxies=proxies, verify=_verify_arg(cfg))
                if resp.status_code != 200:
                    try:
                        err_info = resp.json()
                        err_msg = err_info.get('error', {}).get('message', resp.text[:400]) if isinstance(err_info, dict) and 'error' in err_info else resp.text[:400]
                    except Exception:
                        err_msg = resp.text[:400]
                    if resp.status_code in EXPLICIT_RETRYABLE_HTTP:
                        last_err = f"HTTP {resp.status_code}: {err_msg}"
                        attempt_events.append(attempt_event(
                            attempt + 1, 'response', attempt_started,
                            outcome='safe_failure', http_status=resp.status_code))
                        if attempt < max_attempts - 1:
                            logger.warning(f"[API生成] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
                            _sleep_backoff(attempt); continue
                        return None, safe_provider_error(
                            f"HTTP {resp.status_code}: {err_msg}",
                            failure_code='google_retryable_http_exhausted', attempts=attempt_events)
                    logger.error(f"[API生成] HTTP失败 model={model_id}, status={resp.status_code}, err={short_text(err_msg, 800)}")
                    return None, f"HTTP {resp.status_code}: {err_msg}"
                _stage("🎨 渲染中…")
                try:
                    data = resp.json()
                except Exception as exc:
                    attempt_events.append(attempt_event(
                        attempt + 1, 'response', attempt_started, outcome='ambiguous_failure', http_status=200))
                    return None, ambiguous_provider_error(
                        f"结果状态不确定：HTTP 200 响应解析失败: {_redact_api_key(exc)}",
                        failure_code='google_response_truncated', attempts=attempt_events)
                img_bytes = None; safety_blocks = []
                for cand in data.get('candidates', []):
                    for part in cand.get('content', {}).get('parts', []):
                        if 'inlineData' in part and part['inlineData'].get('data'):
                            img_bytes = base64.b64decode(part['inlineData']['data'])
                    for r in cand.get('safetyRatings', []):
                        if r.get('blocked'): safety_blocks.append(r.get('category', ''))
                if img_bytes is not None:
                    try:
                        pil_img = Image.open(_io_mod.BytesIO(img_bytes)); pil_img.load()
                    except Exception as e:
                        logger.exception(f"[API生成] 图片解码失败 model={model_id}")
                        attempt_events.append(attempt_event(
                            attempt + 1, 'decode', attempt_started, outcome='ambiguous_failure', http_status=200))
                        return None, ambiguous_provider_error(
                            f"结果状态不确定：图片解码失败: {_redact_api_key(e)}",
                            failure_code='google_image_decode_failed', attempts=attempt_events)
                    logger.info(f"[API生成] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                    return pil_img, None
                if safety_blocks:
                    logger.error(f"[API生成] 安全拦截 model={model_id}: {', '.join(safety_blocks)}")
                    return None, f"安全拦截: {', '.join(safety_blocks)}"
                logger.error(f"[API生成] API未返回图片 model={model_id}")
                attempt_events.append(attempt_event(
                    attempt + 1, 'response', attempt_started, outcome='ambiguous_failure', http_status=200))
                return None, ambiguous_provider_error(
                    "结果状态不确定：API 返回成功状态但没有可用图片",
                    failure_code='google_empty_success_response', attempts=attempt_events)

            # ── 流式（可选）：实时思考 + 看门狗防假死 ──
            resp = _req.post(url, json=payload, stream=True, timeout=(30, 300),
                             proxies=proxies, verify=_verify_arg(cfg))
            if resp.status_code != 200:
                try:
                    err_info = resp.json()
                    err_msg = err_info.get('error', {}).get('message', resp.text[:400]) if isinstance(err_info, dict) and 'error' in err_info else resp.text[:400]
                except Exception:
                    err_msg = resp.text[:400]
                # 5xx / 429 视为暂时性，可重试；其它(如 400/403)直接失败
                if resp.status_code in EXPLICIT_RETRYABLE_HTTP:
                    last_err = f"HTTP {resp.status_code}: {err_msg}"
                    attempt_events.append(attempt_event(
                        attempt + 1, 'response', attempt_started,
                        outcome='safe_failure', http_status=resp.status_code))
                    if attempt < max_attempts - 1:
                        logger.warning(f"[API生成] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
                        _sleep_backoff(attempt); continue
                    return None, safe_provider_error(
                        f"HTTP {resp.status_code}: {err_msg}",
                        failure_code='google_retryable_http_exhausted', attempts=attempt_events)
                logger.error(f"[API生成] HTTP失败 model={model_id}, status={resp.status_code}, err={short_text(err_msg, 800)}")
                return None, f"HTTP {resp.status_code}: {err_msg}"

            # 看门狗：每 5s 检查一次；连接 idle_deadline 秒无新数据、或整次超总时限 → 强关
            def _watchdog(_r=resp, _ev=_wd_stop, _fired=_wd_fired, _last=_last_data):
                while not _ev.wait(5):
                    idle = time.time() - _last[0]
                    over_total = (time.time() - call_t0) >= total_deadline
                    if idle > idle_deadline or over_total:
                        _fired[0] = True
                        why = f"假死{idle:.0f}s无数据" if idle > idle_deadline else "超总时限"
                        logger.warning(f"[API生成] 看门狗触发({why})，强制关闭连接 model={model_id}")
                        try: _r.close()
                        except Exception: pass
                        return
            threading.Thread(target=_watchdog, daemon=True).start()

            # flash 无思考摘要，连上即进入渲染；pro 先吐思考标题，最后才是图片
            if not wants_thoughts:
                _stage("🎨 渲染中…")

            img_bytes = None
            safety_blocks = []
            for raw in resp.iter_lines(decode_unicode=True):
                _last_data[0] = time.time()   # 收到任何一行就刷新活性时钟
                if not raw:
                    continue
                line = raw[5:].strip() if raw.startswith("data:") else raw.strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                for cand in obj.get('candidates', []):
                    for part in cand.get('content', {}).get('parts', []):
                        if part.get('thought') is True and part.get('text'):
                            title = _extract_thought_title(part['text'])
                            if title: _stage(f"🧠 {title}")
                        elif 'inlineData' in part and part['inlineData'].get('data'):
                            img_bytes = base64.b64decode(part['inlineData']['data'])
                            _stage("🎨 渲染中…")
                    for r in cand.get('safetyRatings', []):
                        if r.get('blocked'):
                            safety_blocks.append(r.get('category', ''))

            if img_bytes is not None:
                try:
                    pil_img = Image.open(_io_mod.BytesIO(img_bytes)); pil_img.load()
                except Exception as e:
                    logger.exception(f"[API生成] 图片解码失败 model={model_id}")
                    attempt_events.append(attempt_event(
                        attempt + 1, 'decode', attempt_started, outcome='ambiguous_failure', http_status=200))
                    return None, ambiguous_provider_error(
                        f"结果状态不确定：图片解码失败: {_redact_api_key(e)}",
                        failure_code='google_image_decode_failed', attempts=attempt_events)
                logger.info(f"[API生成] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                return pil_img, None
            if safety_blocks:
                logger.error(f"[API生成] 安全拦截 model={model_id}: {', '.join(safety_blocks)}")
                return None, f"安全拦截: {', '.join(safety_blocks)}"
            # 200 但流正常结束却没拿到图片：可能是被看门狗掐断的半截流 → 当作可重试
            if _wd_fired[0]:
                last_err = "连接假死被看门狗中断"
                logger.warning(f"[API生成] 看门狗中断(流空) attempt={attempt+1}/{max_attempts} model={model_id}")
                attempt_events.append(attempt_event(
                    attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure', http_status=200))
                return None, ambiguous_provider_error(
                    "结果状态不确定：已建立生成响应后连接被看门狗中断，系统未自动重提",
                    failure_code='google_stream_watchdog_unknown', attempts=attempt_events)
            else:
                logger.error(f"[API生成] API未返回图片 model={model_id}")
                return None, "API 未返回图片"

        except _req.exceptions.ConnectTimeout as exc:
            last_err = "连接超时"
            attempt_events.append(attempt_event(
                attempt + 1, 'connect', attempt_started, outcome='safe_failure'))
            logger.warning(f"[API生成] 连接超时(确认未建立请求) attempt={attempt+1}/{max_attempts} model={model_id}")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt); continue
        except _req.exceptions.Timeout as exc:
            attempt_events.append(attempt_event(
                attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure'))
            logger.warning(f"[API生成] 提交后读超时，不自动重提 model={model_id}")
            return None, ambiguous_provider_error(
                f"结果状态不确定：请求提交后超时，系统未自动重提: {_redact_api_key(exc)}",
                failure_code='google_read_timeout_unknown', attempts=attempt_events)
        except RETRYABLE as e:
            last_err = e
            logger.warning(f"[API生成] 网络异常 attempt={attempt+1}/{max_attempts} model={model_id}: {_redact_api_key(e)}")
            if is_safe_pre_submit_exception(e):
                attempt_events.append(attempt_event(
                    attempt + 1, 'connect', attempt_started, outcome='safe_failure'))
                if attempt < max_attempts - 1:
                    _sleep_backoff(attempt); continue
            else:
                attempt_events.append(attempt_event(
                    attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure'))
                return None, ambiguous_provider_error(
                    f"结果状态不确定：请求提交后连接中断，系统未自动重提: {_redact_api_key(e)}",
                    failure_code='google_connection_lost_unknown', attempts=attempt_events)
        except Exception as e:
            # 看门狗强关连接会让阻塞的 iter_lines 抛出各种异常，归类为可重试
            if _wd_fired[0]:
                last_err = "连接假死被看门狗中断"
                logger.warning(f"[API生成] 看门狗中断 attempt={attempt+1}/{max_attempts} model={model_id}")
                attempt_events.append(attempt_event(
                    attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure'))
                return None, ambiguous_provider_error(
                    "结果状态不确定：已提交生成请求被看门狗中断，系统未自动重提",
                    failure_code='google_watchdog_unknown', attempts=attempt_events)
            else:
                logger.exception(f"[API生成] 未预期错误 model={model_id}")
                return None, f"网络错误: {_redact_api_key(e)}"
        finally:
            _wd_stop.set()
            if resp is not None:
                try: resp.close()
                except Exception: pass

    logger.error(f"[API生成] 网络重试失败 model={model_id}: {_redact_api_key(last_err)}")
    return None, safe_provider_error(
        f"网络错误: {_redact_api_key(last_err)}",
        failure_code='google_safe_retries_exhausted', attempts=attempt_events)

def call_gemini_edit(api_key: str, model_id: str, edit_instruction: str, source_image_b64: str,
                     image_size: str = "4K", aspect_ratio: str = "4:3", preserve_floor_geometry: bool = True,
                     on_stage=None, should_cancel=None):
    """Use Gemini image generation as an image-to-image editor for one existing result.

    on_stage(text) / should_cancel() 与 call_gemini_generate 同契约（均可选、各自吞异常）：
    - on_stage：在【本 worker 线程】内回传实时状态（📡连接中 / 🔁网络重试 N/M），供 UI 显示，
      让磨缝/二改在软路由重置导致的重试期间不再像卡死。回调自身须吞异常。
    - should_cancel()：返回 True 表示任务已取消 → 立即停止后续重试，不再发起新的计费请求。
    """
    def _stage(txt):
        _notify_stage(on_stage, txt)
    logger.info(
        f"[API二改] start model={model_id}, size={image_size}, ar={aspect_ratio}, "
        f"source_b64_len={len(source_image_b64 or '')}, instruction_len={len(edit_instruction or '')}"
    )
    if not source_image_b64:
        logger.error("[API二改] 缺少待修改图片")
        return None, "缺少待修改图片"
    instruction = (edit_instruction or "").strip()
    if not instruction:
        logger.error("[API二改] 缺少修改建议")
        return None, "缺少修改建议"

    _preserve_line = (
        "- Preserve the same camera angle, perspective, room scale, lighting direction, floor material, floor plank geometry, and photorealistic camera quality unless the request explicitly says otherwise."
        if preserve_floor_geometry else
        "- Preserve the same camera angle, perspective, room scale, lighting direction, furniture, and photorealistic camera quality. You MAY change the floor's joint/seam geometry as the request asks; keep the floor's wood color and material."
    )
    edit_prompt = f"""Edit the provided interior image according to the user's revision request.

USER REVISION REQUEST:
{instruction}

EDITING RULES:
{_preserve_line}
- Make the smallest sufficient visual change. Do not redesign the whole room.
- If removing an object, realistically reconstruct the hidden wall, floor, shadow, furniture edge, or background behind it.
- Keep the image believable as a real photographed interior, with natural object placement and imperfect lived-in detail.
- Do not add text, watermark, labels, UI overlays, people, or distorted artifacts."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
    parts = [
        {"text": edit_prompt},
        {"inlineData": {"mimeType": "image/jpeg", "data": source_image_b64}},
    ]
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"imageSize": image_size, "aspectRatio": aspect_ratio}
        }
    }
    cfg = load_config(); proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    # 重试策略与主生成对齐：用配置的次数/退避表；429/5xx 与超时也重试，400/403 等立即失败
    max_attempts, backoffs = _retry_plan()
    RETRYABLE = (_req.exceptions.SSLError, _req.exceptions.ConnectionError,
                 _req.exceptions.ChunkedEncodingError, _ProtocolError)
    def _sleep_backoff(attempt):
        d = backoffs[min(attempt, len(backoffs) - 1)] + random.uniform(0, 1.5)
        end = time.time() + d
        while time.time() < end:
            if should_cancel and should_cancel():
                return
            time.sleep(0.5)
    last_err = None
    resp = None
    attempt_events = []
    for attempt in range(max_attempts):
        # 任务已取消 → 不再发起新请求(避免白白计费)，与主生成路径一致
        if should_cancel and should_cancel():
            logger.info(f"[API二改] 任务已取消，停止重试 model={model_id}")
            return None, "已取消"
        _stage("📡 连接中…" if attempt == 0 else f"🔁 网络重试 {attempt}/{max_attempts - 1}")
        attempt_started = time.time()
        try:
            resp = _req.post(url, json=payload, timeout=300, proxies=proxies, verify=_verify_arg(cfg))
        except _req.exceptions.ConnectTimeout:
            resp = None; last_err = "连接超时"
            attempt_events.append(attempt_event(
                attempt + 1, 'connect', attempt_started, outcome='safe_failure'))
            logger.warning(f"[API二改] 连接超时(确认未建立请求) attempt={attempt+1}/{max_attempts} model={model_id}")
            if attempt < max_attempts - 1: _sleep_backoff(attempt)
            continue
        except _req.exceptions.Timeout as e:
            attempt_events.append(attempt_event(
                attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure'))
            return None, ambiguous_provider_error(
                f"结果状态不确定：二改请求提交后超时，系统未自动重提: {_redact_api_key(e)}",
                failure_code='google_edit_read_timeout_unknown', attempts=attempt_events)
        except RETRYABLE as e:
            resp = None; last_err = e
            logger.warning(f"[API二改] 网络异常 attempt={attempt+1}/{max_attempts} model={model_id}: {_redact_api_key(e)}")
            if is_safe_pre_submit_exception(e):
                attempt_events.append(attempt_event(
                    attempt + 1, 'connect', attempt_started, outcome='safe_failure'))
                if attempt < max_attempts - 1: _sleep_backoff(attempt)
                continue
            attempt_events.append(attempt_event(
                attempt + 1, 'submitted', attempt_started, outcome='ambiguous_failure'))
            return None, ambiguous_provider_error(
                f"结果状态不确定：二改请求提交后连接中断，系统未自动重提: {_redact_api_key(e)}",
                failure_code='google_edit_connection_lost_unknown', attempts=attempt_events)
        except Exception as e:
            logger.exception(f"[API二改] 未预期网络错误 model={model_id}")
            return None, f"网络错误: {_redact_api_key(e)}"
        if resp.status_code in EXPLICIT_RETRYABLE_HTTP:
            last_err = f"HTTP {resp.status_code}"
            attempt_events.append(attempt_event(
                attempt + 1, 'response', attempt_started,
                outcome='safe_failure', http_status=resp.status_code))
            if attempt < max_attempts - 1:
                logger.warning(f"[API二改] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
                _sleep_backoff(attempt)
                continue
            return None, safe_provider_error(
                f"HTTP {resp.status_code}",
                failure_code='google_edit_retryable_http_exhausted', attempts=attempt_events)
        break
    if resp is None:
        logger.error(f"[API二改] 网络重试失败 model={model_id}: {_redact_api_key(last_err)}")
        return None, safe_provider_error(
            f"网络错误: {_redact_api_key(last_err)}",
            failure_code='google_edit_safe_retries_exhausted', attempts=attempt_events)
    if resp.status_code != 200:
        try:
            err_info = resp.json()
            err_msg = err_info.get('error', {}).get('message', resp.text[:400]) if 'error' in err_info else resp.text[:400]
        except Exception:
            err_msg = resp.text[:400]
        logger.error(f"[API二改] HTTP失败 model={model_id}, status={resp.status_code}, err={short_text(err_msg, 800)}")
        return None, f"HTTP {resp.status_code}: {err_msg}"
    try:
        data = resp.json()
    except Exception as e:
        # 透明代理劫持/半截响应可能 200 但 body 非 JSON——不能让 JSONDecodeError 冒出破坏 (img, err) 契约
        logger.error(f"[API二改] 响应 JSON 解析失败 model={model_id}: {_redact_api_key(e)}")
        attempt_events.append(attempt_event(
            max(1, len(attempt_events) + 1), 'response', time.time(), outcome='ambiguous_failure', http_status=200))
        return None, ambiguous_provider_error(
            f"结果状态不确定：二改响应解析失败: {_redact_api_key(e)}",
            failure_code='google_edit_response_truncated', attempts=attempt_events)
    for candidate in data.get('candidates', []):
        for part in candidate.get('content', {}).get('parts', []):
            if 'inlineData' in part:
                try:
                    img_bytes = base64.b64decode(part['inlineData']['data'])
                    pil_img = Image.open(_io_mod.BytesIO(img_bytes)); pil_img.load()
                    logger.info(f"[API二改] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                    return pil_img, None
                except Exception as e:
                    logger.exception(f"[API二改] 图片解码失败 model={model_id}")
                    attempt_events.append(attempt_event(
                        max(1, len(attempt_events) + 1), 'decode', time.time(), outcome='ambiguous_failure', http_status=200))
                    return None, ambiguous_provider_error(
                        f"结果状态不确定：二改图片解码失败: {_redact_api_key(e)}",
                        failure_code='google_edit_image_decode_failed', attempts=attempt_events)
    safety_blocks = [r.get('category', '') for c in data.get('candidates', []) for r in c.get('safetyRatings', []) if r.get('blocked')]
    if safety_blocks:
        logger.error(f"[API二改] 安全拦截 model={model_id}: {', '.join(safety_blocks)}")
        return None, f"安全拦截: {', '.join(safety_blocks)}"
    logger.error(f"[API二改] API未返回图片 model={model_id}, response={short_text(data, 1000)}")
    attempt_events.append(attempt_event(
        max(1, len(attempt_events) + 1), 'response', time.time(), outcome='ambiguous_failure', http_status=200))
    return None, ambiguous_provider_error(
        "结果状态不确定：二改接口返回成功状态但没有可用图片",
        failure_code='google_edit_empty_success_response', attempts=attempt_events)

generate = call_gemini_generate
edit = call_gemini_edit

# Preserve the historical provider convenience entrypoint.
from .analysis import analyze_style_image as analyze_style
