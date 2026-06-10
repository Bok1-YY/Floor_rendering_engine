import os
import time
import json
import random
import threading
import base64
import io as _io_mod
import re
from typing import Optional, Tuple

from PIL import Image

from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR, CONFIG_FILE,
    GEMINI_MODEL_MAP, FAL_MODEL_MAP, DEFAULT_IMAGE_PROVIDER,
    logger, _short_text, _load_config, _save_config,
)
from .records import (
    _img_to_b64, _b64_to_pil, _save_api_result_jpg, _api_write_to_record,
)

def _redact_api_key(text):
    return re.sub(r'([?&]key=)[^&\s)]+', r'\1***', str(text or ""))

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
    """从 engine_config.json 读取重试参数；缺省 6 次尝试、退避 2/5/12/25/45s(+抖动)。"""
    cfg = _load_config()
    backoffs = cfg.get("retry_backoffs") or [2, 5, 12, 25, 45]
    try:
        backoffs = [float(x) for x in backoffs]
    except Exception:
        backoffs = [2, 5, 12, 25, 45]
    try:
        attempts = int(cfg.get("retry_attempts", len(backoffs) + 1))
    except Exception:
        attempts = len(backoffs) + 1
    return max(1, attempts), backoffs


def call_gemini_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                         image_size: str = "4K", aspect_ratio: str = "4:3",
                         room_image_path: Optional[str] = None,
                         style_ref_image_path: Optional[str] = None,
                         on_stage=None, should_cancel=None) -> Tuple[Optional[object], Optional[str]]:
    """流式文生图/图生图。

    - Pro 模型(model_id 含 'pro')额外请求 includeThoughts，实时回传思考标题。
    - on_stage(text): 可选回调，在「本(worker)线程」内被调用，用于把实时状态
      （📡连接中 / 🧠思考标题 / 🎨渲染中 / 🔁网络重试 N/M）写回 UI。必须自身吞异常。
    - 网络中断（含流式中途 IncompleteRead）按指数退避重试。
    - should_cancel(): 可选回调，返回 True 表示任务已取消 → 立即停止后续重试，不再发起新请求。
    - 返回 (PIL.Image, None) 或 (None, 错误字符串)，契约与旧版一致。
    """
    import requests as _req
    from urllib3.exceptions import ProtocolError as _ProtocolError

    def _stage(txt):
        if on_stage:
            try: on_stage(txt)
            except Exception: pass

    logger.info(
        f"[API生成] start model={model_id}, size={image_size}, ar={aspect_ratio}, "
        f"floor={image_path}, room_ref={bool(room_image_path)}, style_ref={bool(style_ref_image_path)}, "
        f"prompt={_short_text(prompt_text, 240)}"
    )
    if not os.path.exists(image_path):
        logger.error(f"[API生成] 素材图不存在: {image_path}")
        return None, f"素材图不存在: {image_path}"
    with open(image_path, 'rb') as f: floor_b64 = base64.b64encode(f.read()).decode('utf-8')
    room_b64 = None; room_mime = "image/jpeg"
    if room_image_path and os.path.exists(room_image_path):
        ext = os.path.splitext(room_image_path)[1].lower()
        room_mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext.lstrip('.'), "image/jpeg")
        with open(room_image_path, 'rb') as f: room_b64 = base64.b64encode(f.read()).decode('utf-8')
    sref_b64 = None; sref_mime = "image/jpeg"
    if style_ref_image_path and os.path.exists(style_ref_image_path):
        ext = os.path.splitext(style_ref_image_path)[1].lower()
        sref_mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext.lstrip('.'), "image/jpeg")
        with open(style_ref_image_path, 'rb') as f: sref_b64 = base64.b64encode(f.read()).decode('utf-8')

    parts = [{"text": prompt_text}]
    if sref_b64: parts.append({"inlineData": {"mimeType": sref_mime, "data": sref_b64}})
    if room_b64: parts.append({"inlineData": {"mimeType": room_mime, "data": room_b64}})
    parts.append({"inlineData": {"mimeType": "image/png", "data": floor_b64}})

    cfg = _load_config(); proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        if wants_thoughts:
            gen_cfg["thinkingConfig"] = {"includeThoughts": True}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model_id}:streamGenerateContent?alt=sse&key={api_key}")
    else:
        gen_cfg = {"responseModalities": ["IMAGE"],
                   "imageConfig": {"imageSize": image_size, "aspectRatio": aspect_ratio}}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model_id}:generateContent?key={api_key}")
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

    # 硬性时限：防"半死连接"无限挂起。
    #   idle_deadline: 连接 N 秒收不到任何新数据(行)即判假死、强制关闭。
    #     合法渲染的静默期实测最长 ~190s，故默认 240s，留足余量不误杀。
    #   total_deadline: 整次调用(含所有重试)的墙钟上限，到点放弃、释放队列槽位。
    try: idle_deadline = float(cfg.get("gen_idle_deadline", 240))
    except Exception: idle_deadline = 240.0
    try: total_deadline = float(cfg.get("gen_total_deadline", 600))
    except Exception: total_deadline = 600.0
    call_t0 = time.time()

    last_err = None
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
        resp = None
        _wd_stop = threading.Event()
        _wd_fired = [False]
        _last_data = [time.time()]
        try:
            if not use_streaming:
                # ── 非流式（默认）：一次性拿全图，软路由/透明代理下又快又稳 ──
                _stage("🎨 生成中…")  # post 会阻塞至整图返回(~60s)，先把卡片置为生成中
                resp = _req.post(url, json=payload, timeout=(30, 300), proxies=proxies, verify=False)
                if resp.status_code != 200:
                    try:
                        err_info = resp.json()
                        err_msg = err_info.get('error', {}).get('message', resp.text[:400]) if isinstance(err_info, dict) and 'error' in err_info else resp.text[:400]
                    except Exception:
                        err_msg = resp.text[:400]
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                        last_err = f"HTTP {resp.status_code}: {err_msg}"
                        logger.warning(f"[API生成] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
                        _sleep_backoff(attempt); continue
                    logger.error(f"[API生成] HTTP失败 model={model_id}, status={resp.status_code}, err={_short_text(err_msg, 800)}")
                    return None, f"HTTP {resp.status_code}: {err_msg}"
                _stage("🎨 渲染中…")
                data = resp.json()
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
                        return None, f"解码失败: {e}"
                    logger.info(f"[API生成] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                    return pil_img, None
                if safety_blocks:
                    logger.error(f"[API生成] 安全拦截 model={model_id}: {', '.join(safety_blocks)}")
                    return None, f"安全拦截: {', '.join(safety_blocks)}"
                logger.error(f"[API生成] API未返回图片 model={model_id}")
                return None, "API 未返回图片"

            # ── 流式（可选）：实时思考 + 看门狗防假死 ──
            resp = _req.post(url, json=payload, stream=True, timeout=(30, 300),
                             proxies=proxies, verify=False)
            if resp.status_code != 200:
                try:
                    err_info = resp.json()
                    err_msg = err_info.get('error', {}).get('message', resp.text[:400]) if isinstance(err_info, dict) and 'error' in err_info else resp.text[:400]
                except Exception:
                    err_msg = resp.text[:400]
                # 5xx / 429 视为暂时性，可重试；其它(如 400/403)直接失败
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                    last_err = f"HTTP {resp.status_code}: {err_msg}"
                    logger.warning(f"[API生成] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
                    _sleep_backoff(attempt); continue
                logger.error(f"[API生成] HTTP失败 model={model_id}, status={resp.status_code}, err={_short_text(err_msg, 800)}")
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
                    return None, f"解码失败: {e}"
                logger.info(f"[API生成] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                return pil_img, None
            if safety_blocks:
                logger.error(f"[API生成] 安全拦截 model={model_id}: {', '.join(safety_blocks)}")
                return None, f"安全拦截: {', '.join(safety_blocks)}"
            # 200 但流正常结束却没拿到图片：可能是被看门狗掐断的半截流 → 当作可重试
            if _wd_fired[0]:
                last_err = "连接假死被看门狗中断"
                logger.warning(f"[API生成] 看门狗中断(流空) attempt={attempt+1}/{max_attempts} model={model_id}")
                if attempt < max_attempts - 1:
                    _sleep_backoff(attempt); continue
            else:
                logger.error(f"[API生成] API未返回图片 model={model_id}")
                return None, "API 未返回图片"

        except _req.exceptions.Timeout:
            last_err = "请求超时"
            logger.warning(f"[API生成] 请求超时 attempt={attempt+1}/{max_attempts} model={model_id}")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt); continue
        except RETRYABLE as e:
            last_err = e
            logger.warning(f"[API生成] 网络异常 attempt={attempt+1}/{max_attempts} model={model_id}: {_redact_api_key(e)}")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt); continue
        except Exception as e:
            # 看门狗强关连接会让阻塞的 iter_lines 抛出各种异常，归类为可重试
            if _wd_fired[0]:
                last_err = "连接假死被看门狗中断"
                logger.warning(f"[API生成] 看门狗中断 attempt={attempt+1}/{max_attempts} model={model_id}")
                if attempt < max_attempts - 1:
                    _sleep_backoff(attempt); continue
            else:
                logger.exception(f"[API生成] 未预期错误 model={model_id}")
                return None, f"网络错误: {_redact_api_key(e)}"
        finally:
            _wd_stop.set()
            if resp is not None:
                try: resp.close()
                except Exception: pass

    logger.error(f"[API生成] 网络重试失败 model={model_id}: {_redact_api_key(last_err)}")
    return None, f"网络错误: {_redact_api_key(last_err)}"


# ── Fal 路由 ────────────────────────────────────────────────────────────────
_FAL_RESOLUTIONS = {"1K", "2K", "4K"}
_FAL_ASPECT_RATIOS = {"auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"}


def _file_to_data_uri(path: str) -> Optional[str]:
    """把本地图片读成 data URI(base64),用作 Fal 的 image_urls 输入。"""
    if not path or not os.path.exists(path):
        return None
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    return f"data:{mime};base64,{b64}"


def call_fal_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                      image_size: str = "4K", aspect_ratio: str = "4:3",
                      room_image_path: Optional[str] = None,
                      style_ref_image_path: Optional[str] = None,
                      on_stage=None, should_cancel=None) -> Tuple[Optional[object], Optional[str]]:
    """经 Fal 路由调用 Nano Banana 系列(图生图 /edit 端点)。

    与 call_gemini_generate 同契约:返回 (PIL.Image, None) 或 (None, 错误字符串),并支持 on_stage 回调。
    同一个 Gemini 模型,只换更稳的线路(国内→Fal→Google),保真/4K 不变。
    - model_id 仍用 Gemini 的 id,内部经 FAL_MODEL_MAP 映射到 Fal endpoint。
    - 用 sync_mode=true:响应内联返回 data URI 图,整次生图只需一次请求(软路由下最稳)。
    - should_cancel(): 可选回调,返回 True 表示任务已被用户取消 → 立刻停止后续重试,
      不再发起新的(会计费的)Fal 请求。已在途的那一次无法召回,但本次若已拿到图仍会正常返回。
    """
    import requests as _req
    from urllib3.exceptions import ProtocolError as _ProtocolError

    def _stage(txt):
        if on_stage:
            try: on_stage(txt)
            except Exception: pass

    cfg = _load_config()
    fal_map = cfg.get("fal_model_map") or FAL_MODEL_MAP
    endpoint = fal_map.get(model_id) or FAL_MODEL_MAP.get(model_id)
    if not endpoint:
        logger.error(f"[Fal生成] 未知模型,无 Fal 端点映射: model={model_id}")
        return None, f"该模型未配置 Fal 端点: {model_id}"

    logger.info(
        f"[Fal生成] start model={model_id} -> {endpoint}, size={image_size}, ar={aspect_ratio}, "
        f"floor={image_path}, room_ref={bool(room_image_path)}, style_ref={bool(style_ref_image_path)}, "
        f"prompt={_short_text(prompt_text, 240)}"
    )
    if not os.path.exists(image_path):
        logger.error(f"[Fal生成] 素材图不存在: {image_path}")
        return None, f"素材图不存在: {image_path}"

    # image_urls 顺序与 Gemini 直连保持一致:风格参考 → 房间参考 → 地板小样(地板最后/最关键)
    image_urls = []
    for p in (style_ref_image_path, room_image_path, image_path):
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
    import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
            r = _req.get(url_or_uri, timeout=(30, 300), proxies=proxies, verify=False)
            r.raise_for_status()
            raw = r.content
        img = Image.open(_io_mod.BytesIO(raw)); img.load()
        return img

    call_t0 = time.time()
    last_err = None
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
        try:
            _stage("🎨 生成中…")
            resp = _req.post(url, json=payload, headers=headers,
                             timeout=(30, 300), proxies=proxies, verify=False)
            if resp.status_code != 200:
                try:
                    err_info = resp.json()
                    err_msg = (err_info.get('detail') or err_info.get('error') or err_info.get('message')
                               or resp.text[:400]) if isinstance(err_info, dict) else resp.text[:400]
                except Exception:
                    err_msg = resp.text[:400]
                if resp.status_code in (408, 409, 425, 429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                    last_err = f"HTTP {resp.status_code}: {err_msg}"
                    logger.warning(f"[Fal生成] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
                    _sleep_backoff(attempt); continue
                logger.error(f"[Fal生成] HTTP失败 model={model_id}, status={resp.status_code}, err={_short_text(err_msg, 800)}")
                return None, f"HTTP {resp.status_code}: {err_msg}"

            _stage("🎨 渲染中…")
            data = resp.json()
            images = data.get("images") or []
            if images and images[0].get("url"):
                try:
                    pil_img = _decode_image(images[0]["url"])
                except Exception as e:
                    logger.exception(f"[Fal生成] 图片解码/下载失败 model={model_id}")
                    return None, f"解码失败: {_redact_api_key(e)}"
                logger.info(f"[Fal生成] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                return pil_img, None
            logger.error(f"[Fal生成] API未返回图片 model={model_id}, resp={_short_text(data, 600)}")
            return None, "API 未返回图片"

        except _req.exceptions.Timeout:
            last_err = "请求超时"
            logger.warning(f"[Fal生成] 请求超时 attempt={attempt+1}/{max_attempts} model={model_id}")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt); continue
        except RETRYABLE as e:
            last_err = e
            logger.warning(f"[Fal生成] 网络异常 attempt={attempt+1}/{max_attempts} model={model_id}: {_redact_api_key(e)}")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt); continue
        except Exception as e:
            logger.exception(f"[Fal生成] 未预期错误 model={model_id}")
            return None, f"网络错误: {_redact_api_key(e)}"

    logger.error(f"[Fal生成] 网络重试失败 model={model_id}: {_redact_api_key(last_err)}")
    return None, f"网络错误: {_redact_api_key(last_err)}"


def call_image_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                        image_size: str = "4K", aspect_ratio: str = "4:3",
                        room_image_path: Optional[str] = None,
                        style_ref_image_path: Optional[str] = None,
                        on_stage=None, should_cancel=None) -> Tuple[Optional[object], Optional[str]]:
    """生图调度器:按 engine_config.json 的 image_provider 选线路,两条线路同契约。

    - 'google'(默认):直连 Google AI Studio,沿用传入的 Gemini api_key。
    - 'fal':走 Fal 路由,改用 config 里的 fal_api_key(忽略传入的 Gemini key)。
    - should_cancel(): 透传给底层,任务取消后立即停止重试,不再产生新的计费请求。
    """
    cfg = _load_config()
    provider = (cfg.get("image_provider") or DEFAULT_IMAGE_PROVIDER).strip().lower()
    if provider == "fal":
        fal_key = (cfg.get("fal_api_key") or "").strip()
        if not fal_key:
            logger.error("[生图调度] 线路=fal 但未配置 Fal API Key")
            return None, "未配置 Fal API Key(请在 API 设置里填写 Fal Key)"
        return call_fal_generate(fal_key, model_id, prompt_text, image_path, image_size,
                                 aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel)
    return call_gemini_generate(api_key, model_id, prompt_text, image_path, image_size,
                                aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel)


def call_gemini_edit(api_key: str, model_id: str, edit_instruction: str, source_image_b64: str,
                     image_size: str = "4K", aspect_ratio: str = "4:3", preserve_floor_geometry: bool = True):
    """Use Gemini image generation as an image-to-image editor for one existing result."""
    import requests as _req
    logger.info(
        f"[API二改] start model={model_id}, size={image_size}, ar={aspect_ratio}, "
        f"source_b64_len={len(source_image_b64 or '')}, instruction={_short_text(edit_instruction, 300)}"
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
    cfg = _load_config(); proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    last_err = None
    for attempt in range(3):
        try:
            resp = _req.post(url, json=payload, timeout=300, proxies=proxies, verify=False)
            last_err = None; break
        except (_req.exceptions.SSLError, _req.exceptions.ConnectionError, _req.exceptions.ChunkedEncodingError) as e:
            last_err = e
            logger.warning(f"[API二改] 网络异常 attempt={attempt+1}/3 model={model_id}: {_redact_api_key(e)}")
            if attempt < 2: time.sleep(2 ** attempt)
        except _req.exceptions.Timeout:
            logger.error(f"[API二改] 请求超时 model={model_id}")
            return None, "请求超时"
        except Exception as e:
            logger.exception(f"[API二改] 未预期网络错误 model={model_id}")
            return None, f"网络错误: {e}"
    if last_err is not None:
        logger.error(f"[API二改] 网络重试失败 model={model_id}: {_redact_api_key(last_err)}")
        return None, f"网络错误: {_redact_api_key(last_err)}"
    if resp.status_code != 200:
        try:
            err_info = resp.json()
            err_msg = err_info.get('error', {}).get('message', resp.text[:400]) if 'error' in err_info else resp.text[:400]
        except Exception:
            err_msg = resp.text[:400]
        logger.error(f"[API二改] HTTP失败 model={model_id}, status={resp.status_code}, err={_short_text(err_msg, 800)}")
        return None, f"HTTP {resp.status_code}: {err_msg}"
    data = resp.json()
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
                    return None, f"解码失败: {e}"
    safety_blocks = [r.get('category', '') for c in data.get('candidates', []) for r in c.get('safetyRatings', []) if r.get('blocked')]
    if safety_blocks:
        logger.error(f"[API二改] 安全拦截 model={model_id}: {', '.join(safety_blocks)}")
        return None, f"安全拦截: {', '.join(safety_blocks)}"
    logger.error(f"[API二改] API未返回图片 model={model_id}, response={_short_text(data, 1000)}")
    return None, "API 未返回图片"

# Pro 出图后自动磨缝用的编辑指令（仅修地板接缝，其余像素级保留）
FLOOR_DESEAM_INSTRUCTION = (
    "Smooth away and remove ALL the joint lines, seam lines and plank-edge grooves on the WOODEN FLOOR only, "
    "so the floor becomes ONE single continuous, seamless surface — like one solid printed sheet. "
    "CRITICAL — KEEP THE FLOOR'S EXISTING LAYOUT EXACTLY AS IT IS: whatever plank pattern the input floor already "
    "shows (straight parallel planks, herringbone, square grid, etc.) MUST stay the SAME pattern. Do NOT convert it to "
    "another layout — do NOT turn straight planks into herringbone, and do NOT turn herringbone into straight planks. "
    "Express that SAME pattern PURELY through wood-grain direction: only the grain direction follows the original "
    "layout, with no joint line, no groove and no gap anywhere on the floor. "
    "Keep the EXACT same wood color, wood tone, brightness and saturation as the input floor — do NOT lighten, darken, "
    "warm up, cool down or otherwise shift the floor's color in any way. "
    "Do NOT change anything else in the image: keep every piece of furniture, every plant, the walls, windows, "
    "ceiling, lighting, sunlight, shadows, camera angle and perspective exactly as they already are. "
    "Preserve ultra-sharp, pixel-level detail everywhere — do NOT blur, soften, smear or denoise any part of the "
    "image; keep fabrics, plants, wood grain and all edges exactly as crisp and high-resolution as the original. "
    "This is a minimal floor-only retouch — only the floor seams are smoothed away; everything else stays the same."
)

def analyze_style_image(api_key: str, image_path: str) -> str:
    """Step-1 of 参照模式: call Gemini text API to extract a precise style blueprint from a reference room photo."""
    import requests as _req
    if not image_path or not os.path.exists(image_path):
        return ""
    with open(image_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode('utf-8')
    ext = os.path.splitext(image_path)[1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext.lstrip('.'), "image/jpeg")
    analysis_prompt = (
        "You are a precision interior design analyst. Your output is used DIRECTLY as a brief for an AI image generator — vagueness causes generation failure.\n\n"
        "PRECISION RULE — name EXACT objects, never categories:\n"
        "  ✗ WRONG: 'stylish shelving unit'  ✓ RIGHT: 'floor-to-ceiling shelving with brushed aluminum uprights and birch veneer shelves'\n"
        "  ✗ WRONG: 'nice warm lighting'     ✓ RIGHT: 'warm arc floor lamp (≈3000K), single drum pendant, no ceiling fixtures'\n"
        "  ✗ WRONG: 'beige walls'            ✓ RIGHT: 'warm greige plaster walls, matte finish'\n\n"
        "ANCHOR STEP (do this mentally before filling fields): identify the ONE object that would DISAPPEAR if this room became a generic hotel lobby. "
        "That is your extraction anchor — all descriptions must be as specific as that anchor.\n\n"
        "Return ONLY the 9 labeled fields below — no preamble, no commentary:\n\n"
        "SIGNATURE: [1–3 elements that would DISAPPEAR if this room turned generic. Exact object type + material + finish. "
        "Brand aesthetic if recognizable (Vitsœ, HAY, String, MUJI, etc). Separate items with semicolons.]\n\n"
        "MATERIALS: [Every major surface → exact material + finish. "
        "Format: object → material finish; object → material finish. "
        "Example: sofa → dusty-rose boucle; coffee table → smoked tempered glass on matte black steel; floor → pale oak herringbone parquet matte]\n\n"
        "PALETTE: [3 precise color names (not 'beige' — say 'warm greige'; not 'blue' — say 'dusty sky blue'). "
        "Then one sentence: warm/cool/neutral register, high/medium/low contrast.]\n\n"
        "LIGHTING: [Source: natural/artificial/mixed. Direction. Color temp in Kelvin (2700K / 3000K / 4000K / 6500K). "
        "Shadow quality: hard/soft/diffuse. Mood in one adjective.]\n\n"
        "PLANTS_DECOR: [Exact plant species + count + vessel material. Each decorative object: exact type + material. "
        "Density: sparse/moderate/dense.]\n\n"
        "MOOD: [Exactly 5 sensory/emotional adjectives capturing how this space FEELS. Pure sensory language — no design jargon. "
        "Example: airy, mineral, unhurried, intentional, restrained]\n\n"
        "REALISM_CUES: [Specific photographic cues that make this image feel real rather than CGI/showroom: partial furniture cropping, asymmetry, object density, small lived-in props, imperfect sunlight patches, shadows, casual placement, non-matching furniture. Be concrete.]\n\n"
        "PROHIBITIONS: [4 things you would NEVER find here — design-precise. "
        "'no upholstered sofa with valance skirt' not 'no old sofa'. "
        "'no warm Edison filament bulbs' not 'no bad lighting'.]\n\n"
        "STYLE_SENTENCE: [One sentence: design movement + decade reference + the single defining material or furniture piece.]"
    )
    payload = {
        "contents": [{"parts": [{"text": analysis_prompt}, {"inlineData": {"mimeType": mime, "data": img_b64}}]}],
        "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.1}
    }
    cfg = _load_config(); proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    # 按优先级依次尝试可用的文字视觉模型
    _text_models = [
        "gemini-3.1-flash-image-preview",   # 该 API Key 确认可用，优先尝试
        "gemini-3-pro-image-preview",        # 该 API Key 确认可用
        "gemini-2.0-flash",                  # 标准 key 可用
        "gemini-2.0-flash-001",
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash-001",
    ]
    for model_name in _text_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = _req.post(url, json=payload, timeout=60, proxies=proxies, verify=False)
            if resp.status_code == 200:
                for candidate in resp.json().get('candidates', []):
                    for part in candidate.get('content', {}).get('parts', []):
                        if 'text' in part:
                            logger.info(f"[参照模式] 风格分析使用模型: {model_name}")
                            return part['text'].strip()
            elif resp.status_code == 404:
                logger.warning(f"[参照模式] 模型 {model_name} 不可用(404)，尝试下一个...")
                continue
            else:
                return f"(Style analysis failed: HTTP {resp.status_code} on {model_name})"
        except Exception as e:
            logger.warning(f"[参照模式] 模型 {model_name} 请求异常: {_redact_api_key(e)}")
            continue
    return "(Style analysis failed: 所有备选模型均不可用，请检查 API Key 和网络)"

def _match_color_to_reference(src_img, ref_img, strength=1.0):
    """把 src 的整体色彩统计对齐到 ref（LAB 空间均值/方差迁移，Reinhard 色彩迁移）。

    用于消除 img2img 磨缝带来的全局偏色——内容几乎一致，只把色温/饱和度拉回原图。

    Args:
        src_img: 待校色的 PIL Image
        ref_img: 颜色参考 PIL Image
        strength: 0.0~1.0，迁移强度。1.0=完全迁移(原行为)，0.0=保持原图不变
    """
    import numpy as np
    src = src_img.convert('LAB'); ref = ref_img.convert('LAB')
    if ref.size != src.size:
        ref = ref.resize(src.size)
    s = np.asarray(src, dtype=np.float32); r = np.asarray(ref, dtype=np.float32)
    out = np.empty_like(s)
    for c in range(3):
        s_mean, s_std = s[..., c].mean(), s[..., c].std()
        r_mean, r_std = r[..., c].mean(), r[..., c].std()
        if s_std < 1e-5:
            out[..., c] = s[..., c] - s_mean + r_mean
        else:
            out[..., c] = (s[..., c] - s_mean) * (r_std / s_std) + r_mean
    out = np.clip(out, 0, 255).astype(np.uint8)
    transferred = Image.fromarray(out, mode='LAB').convert('RGB')

    # 按强度与原图混合（strength=1.0 时等价于原行为）
    if strength < 1.0:
        src_rgb = src_img.convert('RGB')
        return Image.blend(src_rgb, transferred, strength)
    return transferred


def create_blurred_reference(swatch_path):
    """生成用于颜色迁移的模糊参考图。

    对地板小样图施加大半径高斯模糊以消除木纹/矿物线等纹理噪声，
    保留纯粹的"感知颜色"统计量用于 Reinhard 色彩迁移。

    Args:
        swatch_path: 地板小样图路径

    Returns:
        PIL.Image 或 None（失败时）
    """
    try:
        from PIL import ImageFilter
        img = Image.open(swatch_path).convert('RGB')
        return img.filter(ImageFilter.GaussianBlur(radius=30))
    except Exception:
        return None


def _infer_aspect_ratio_from_b64(b64_str: str) -> str:
    img = _b64_to_pil(b64_str)
    if img is None or not img.width or not img.height:
        return "4:3"
    ratio = img.width / img.height
    candidates = [("1:1", 1.0), ("4:3", 4/3), ("3:4", 3/4), ("16:9", 16/9), ("9:16", 9/16)]
    return min(candidates, key=lambda x: abs(x[1] - ratio))[0]



__all__ = [n for n in dir() if not n.startswith('__')]
