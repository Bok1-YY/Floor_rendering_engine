import os
import time
import json
import random
import threading
import base64
import hashlib
import io as _io_mod
import re
from typing import Optional, Tuple

from PIL import Image

import requests as _req
import urllib3
from urllib3.exceptions import ProtocolError as _ProtocolError

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR, CONFIG_FILE,
    GEMINI_MODEL_MAP, FAL_MODEL_MAP, DEFAULT_IMAGE_PROVIDER,
    logger, _short_text, _load_config, _save_config,
    get_speed_profile_params,
    get_text_models, get_gen_sampling,
)
from .records import (
    _img_to_b64, _b64_to_pil, _save_api_result_jpg, _api_write_to_record,
)

_IMAGE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

def _read_image_b64(path: Optional[str]) -> Tuple[Optional[str], str]:
    """读本地图片为 (base64, mime)；路径为空或文件不存在返回 (None, "image/jpeg")。"""
    if not path or not os.path.exists(path):
        return None, "image/jpeg"
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    mime = _IMAGE_MIME.get(ext, "image/jpeg")
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    return b64, mime

def _redact_api_key(text):
    return re.sub(r'([?&]key=)[^&\s)]+', r'\1***', str(text or ""))


def _verify_arg(cfg=None):
    """返回传给 requests 的 verify 值（HTTPS 证书校验）。

    - tls_verify 显式为 False → False（坏网络/会拦 HTTPS 的代理上关掉校验）
    - 否则（默认 True）配了存在的 tls_ca_bundle → 返回该 CA 路径
    - 否则 → True：用系统/requests 默认 CA
    传入已加载的 cfg 可复用，避免热路径重复读盘。
    """
    cfg = cfg if cfg is not None else _load_config()
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
    cfg = _load_config()
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


def call_gemini_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                         image_size: str = "4K", aspect_ratio: str = "4:3",
                         room_image_path: Optional[str] = None,
                         style_ref_image_path: Optional[str] = None,
                         on_stage=None, should_cancel=None,
                         bevel_ref_image_path: Optional[str] = None) -> Tuple[Optional[object], Optional[str]]:
    """流式文生图/图生图。

    - Pro 模型(model_id 含 'pro')额外请求 includeThoughts，实时回传思考标题。
    - on_stage(text): 可选回调，在「本(worker)线程」内被调用，用于把实时状态
      （📡连接中 / 🧠思考标题 / 🎨渲染中 / 🔁网络重试 N/M）写回 UI。必须自身吞异常。
    - 网络中断（含流式中途 IncompleteRead）按指数退避重试。
    - should_cancel(): 可选回调，返回 True 表示任务已取消 → 立即停止后续重试，不再发起新请求。
    - 返回 (PIL.Image, None) 或 (None, 错误字符串)，契约与旧版一致。
    """
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
    floor_b64, _ = _read_image_b64(image_path)
    room_b64, room_mime = _read_image_b64(room_image_path)
    sref_b64, sref_mime = _read_image_b64(style_ref_image_path)
    # 圆弧倒角参考图：只供模型参考板边倒角形状(颜色/木纹仍取地板小样)。放在地板小样之前。
    bevel_b64, bevel_mime = _read_image_b64(bevel_ref_image_path)

    parts = [{"text": prompt_text}]
    if sref_b64: parts.append({"inlineData": {"mimeType": sref_mime, "data": sref_b64}})
    if room_b64: parts.append({"inlineData": {"mimeType": room_mime, "data": room_b64}})
    if bevel_b64: parts.append({"inlineData": {"mimeType": bevel_mime, "data": bevel_b64}})
    parts.append({"inlineData": {"mimeType": "image/png", "data": floor_b64}})

    cfg = _load_config(); proxy = cfg.get("proxy", "").strip()
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
        if wants_thoughts:
            gen_cfg["thinkingConfig"] = {"includeThoughts": True}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model_id}:streamGenerateContent?alt=sse&key={api_key}")
    else:
        gen_cfg = {"responseModalities": ["IMAGE"],
                   "imageConfig": {"imageSize": image_size, "aspectRatio": aspect_ratio}}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model_id}:generateContent?key={api_key}")
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
                resp = _req.post(url, json=payload, timeout=(30, 300), proxies=proxies, verify=_verify_arg(cfg))
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
                             proxies=proxies, verify=_verify_arg(cfg))
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
    b64, mime = _read_image_b64(path)
    if b64 is None:
        return None
    return f"data:{mime};base64,{b64}"


def call_fal_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                      image_size: str = "4K", aspect_ratio: str = "4:3",
                      room_image_path: Optional[str] = None,
                      style_ref_image_path: Optional[str] = None,
                      on_stage=None, should_cancel=None,
                      bevel_ref_image_path: Optional[str] = None) -> Tuple[Optional[object], Optional[str]]:
    """经 Fal 路由调用 Nano Banana 系列(图生图 /edit 端点)。

    与 call_gemini_generate 同契约:返回 (PIL.Image, None) 或 (None, 错误字符串),并支持 on_stage 回调。
    同一个 Gemini 模型,只换更稳的线路(国内→Fal→Google),保真/4K 不变。
    - model_id 仍用 Gemini 的 id,内部经 FAL_MODEL_MAP 映射到 Fal endpoint。
    - 用 sync_mode=true:响应内联返回 data URI 图,整次生图只需一次请求(软路由下最稳)。
    - should_cancel(): 可选回调,返回 True 表示任务已被用户取消 → 立刻停止后续重试,
      不再发起新的(会计费的)Fal 请求。已在途的那一次无法召回,但本次若已拿到图仍会正常返回。
    """
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

    # image_urls 顺序与 Gemini 直连保持一致:风格参考 → 房间参考 → 倒角参考 → 地板小样(地板最后/最关键)
    image_urls = []
    for p in (style_ref_image_path, room_image_path, bevel_ref_image_path, image_path):
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
                             timeout=(30, 300), proxies=proxies, verify=_verify_arg(cfg))
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


def _is_network_class_error(err) -> bool:
    """判断错误是否属于『网络/传输类失败』——只有这类才值得换线重试。

    网络类(代理重置/超时/连接假死/可重试的 5xx/429)在另一条线路上可能成功；
    内容/请求级错误(安全拦截、HTTP 400/403、API 未返回图片、解码失败、素材图不存在)
    换线一样会失败，转线只会白烧 Fal 的钱，故一律不转。

    例外:HTTP 400 "User location is not supported"——这是出口线路落地的地区被 Gemini
    封锁(本质是线路问题,不是内容/请求问题),Fal 不走这套地区封锁,转线往往能直接出图,故纳入可转线。
    """
    e = str(err or "")
    if any(m in e for m in ("网络错误", "请求超时", "超过总时限", "连接假死", "看门狗")):
        return True
    if "location is not supported" in e:
        return True
    return bool(re.search(r'HTTP (429|500|502|503|504)', e))


def call_image_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                        image_size: str = "4K", aspect_ratio: str = "4:3",
                        room_image_path: Optional[str] = None,
                        style_ref_image_path: Optional[str] = None,
                        on_stage=None, should_cancel=None,
                        bevel_ref_image_path: Optional[str] = None) -> Tuple[Optional[object], Optional[str], str]:
    """生图调度器:按 engine_config.json 的 image_provider 选线路,两条线路同契约。

    返回 (PIL.Image|None, 错误字符串|None, provider)——provider∈{'google','fal'} 是【实际】出图/尝试
    的线路(自动转 Fal 后即 'fal')，供用量统计准确归账，不再靠读配置猜测。

    - 'google'(默认):直连 Google AI Studio,沿用传入的 Gemini api_key。
    - 'fal':走 Fal 路由,改用 config 里的 fal_api_key(忽略传入的 Gemini key)。
    - 自动转线(auto_failover):线路=google 时,若直连因【网络类失败】重试耗尽且本开关开启、
      已配 Fal Key、任务未取消,则自动改走 Fal 再跑一次(用户自己的 key)。内容/请求级错误不转。
    - should_cancel(): 透传给底层,任务取消后立即停止重试,不再产生新的计费请求。
    """
    cfg = _load_config()
    provider = (cfg.get("image_provider") or DEFAULT_IMAGE_PROVIDER).strip().lower()
    if provider == "fal":
        fal_key = (cfg.get("fal_api_key") or "").strip()
        if not fal_key:
            logger.error("[生图调度] 线路=fal 但未配置 Fal API Key")
            return None, "未配置 Fal API Key(请在 API 设置里填写 Fal Key)", "fal"
        img, err = call_fal_generate(fal_key, model_id, prompt_text, image_path, image_size,
                                     aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel,
                                     bevel_ref_image_path=bevel_ref_image_path)
        return img, err, "fal"

    # ── 线路=google：先走直连 ──
    img, err = call_gemini_generate(api_key, model_id, prompt_text, image_path, image_size,
                                    aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel,
                                    bevel_ref_image_path=bevel_ref_image_path)
    if img is not None:
        return img, err, "google"

    # ── 直连失败 → 评估是否自动转 Fal 备用线路 ──
    auto = bool(cfg.get("auto_failover", False))
    fal_key = (cfg.get("fal_api_key") or "").strip()
    cancelled = bool(should_cancel and should_cancel())
    if auto and fal_key and not cancelled and _is_network_class_error(err):
        logger.warning(f"[生图调度] Google 直连网络类失败，自动转 Fal 备用线路 model={model_id}: {_redact_api_key(err)}")
        if on_stage:
            try: on_stage("🔁 直连失败，转 Fal 备用线路…")
            except Exception: pass
        fb_img, fb_err = call_fal_generate(fal_key, model_id, prompt_text, image_path, image_size,
                                           aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel,
                                           bevel_ref_image_path=bevel_ref_image_path)
        if fb_img is not None:
            logger.info(f"[生图调度] Fal 备用线路出图成功 model={model_id}")
            return fb_img, fb_err, "fal"   # 实际出图线路=Fal，用量记 Fal
        return None, f"直连失败({err})；Fal 备用也失败({fb_err})", "google"  # 失败归主线 google
    return None, err, "google"


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
        if on_stage:
            try: on_stage(txt)
            except Exception: pass
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
    for attempt in range(max_attempts):
        # 任务已取消 → 不再发起新请求(避免白白计费)，与主生成路径一致
        if should_cancel and should_cancel():
            logger.info(f"[API二改] 任务已取消，停止重试 model={model_id}")
            return None, "已取消"
        _stage("📡 连接中…" if attempt == 0 else f"🔁 网络重试 {attempt}/{max_attempts - 1}")
        try:
            resp = _req.post(url, json=payload, timeout=300, proxies=proxies, verify=_verify_arg(cfg))
        except _req.exceptions.Timeout:
            resp = None; last_err = "请求超时"
            logger.warning(f"[API二改] 请求超时 attempt={attempt+1}/{max_attempts} model={model_id}")
            if attempt < max_attempts - 1: _sleep_backoff(attempt)
            continue
        except RETRYABLE as e:
            resp = None; last_err = e
            logger.warning(f"[API二改] 网络异常 attempt={attempt+1}/{max_attempts} model={model_id}: {_redact_api_key(e)}")
            if attempt < max_attempts - 1: _sleep_backoff(attempt)
            continue
        except Exception as e:
            logger.exception(f"[API二改] 未预期网络错误 model={model_id}")
            return None, f"网络错误: {_redact_api_key(e)}"
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
            last_err = f"HTTP {resp.status_code}"
            logger.warning(f"[API二改] HTTP可重试 attempt={attempt+1}/{max_attempts} model={model_id}, status={resp.status_code}")
            _sleep_backoff(attempt)
            continue
        break
    if resp is None:
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

_STYLE_ANALYZE_TIMEOUT = 60  # 单个文字模型的请求超时（秒）

# ── 参照模式风格分析缓存（按图片内容 sha256）─────────────────────────
# 同一张参照图重复分析既费钱又费时(批量参照=N 次)，缓存到磁盘，命中即免请求、免计费、秒回。
# 版本前缀随分析 prompt 走：改了 prompt 就 bump _STYLE_CACHE_VERSION，旧缓存自动失效。
_STYLE_CACHE_FILE = os.path.join(MAIN_OUTPUT_DIR, ".style_analysis_cache.json")
_STYLE_CACHE_VERSION = "v2"  # v2: 分析 prompt 改"如实描述照片本身"(去高端国际词偏置)+参照图改直接喂模型
_style_cache_lock = threading.Lock()
_style_cache = None  # 懒加载


def _load_style_cache() -> dict:
    global _style_cache
    if _style_cache is None:
        try:
            with open(_STYLE_CACHE_FILE, "r", encoding="utf-8") as f:
                _style_cache = json.load(f)
            if not isinstance(_style_cache, dict):
                _style_cache = {}
        except Exception:
            _style_cache = {}
    return _style_cache


def _style_cache_key(raw_bytes: bytes) -> str:
    return f"{_STYLE_CACHE_VERSION}:{hashlib.sha256(raw_bytes).hexdigest()}"


def _style_cache_get(key: str) -> Optional[str]:
    with _style_cache_lock:
        return _load_style_cache().get(key)


def _style_cache_put(key: str, text: str) -> None:
    with _style_cache_lock:
        c = _load_style_cache(); c[key] = text
        try:
            tmp = _STYLE_CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False)
            os.replace(tmp, _STYLE_CACHE_FILE)
        except Exception as e:
            logger.warning(f"[参照模式] 风格分析缓存写入失败: {e}")


def analyze_style_image(api_key: str, image_path: str) -> Tuple[str, Optional[str]]:
    """Step-1 of 参照模式: call Gemini text API to extract a precise style blueprint from a reference room photo.

    返回 (风格描述文本, None) 或 ("", 错误信息)。调用方必须检查错误并中止生图——
    错误文本绝不能混进生图提示词（曾因此把 "(Style analysis failed...)" 拼进计费请求）。
    """
    if not image_path or not os.path.exists(image_path):
        return "", "参照图不存在"
    img_b64, mime = _read_image_b64(image_path)
    # 缓存：同一张参照图(内容哈希)命中即免请求、免计费、秒回
    cache_on = bool(_load_config().get("style_analysis_cache", True))
    cache_key = None
    if cache_on and img_b64:
        try:
            with open(image_path, "rb") as _f:
                cache_key = _style_cache_key(_f.read())
            _hit = _style_cache_get(cache_key)
            if _hit:
                logger.info("[参照模式] 命中风格分析缓存，跳过请求")
                return _hit, None
        except Exception as _ex:
            logger.debug(f"[参照模式] 读取风格缓存失败(忽略): {_ex}")
    # 图本身已作为风格参照直接喂给生图模型，这段文字只是「简短强化 brief」，不是唯一描述。
    # 故要求：如实描述照片里【真实存在】的风格(含国内普通住宅)，不拔高、不套高端设计词/品牌/流派。
    analysis_prompt = (
        "You are an interior-style analyst. The room PHOTO itself is given to the image generator as the "
        "primary style reference; your text is only a SHORT reinforcement brief, not the sole description.\n\n"
        "FAITHFULNESS RULE — describe the actual style, materials, palette, lighting and mood PRESENT IN THIS "
        "PHOTO, whatever it is: modern, traditional, budget, high-end, Chinese domestic, or international. "
        "Do NOT upgrade, glamorize, or impose a design movement, brand, or trend that is not visibly present. "
        "If it is an ordinary lived-in home, say so plainly.\n\n"
        "PRECISION RULE — name concrete objects and finishes, not vague categories "
        "(e.g. 'warm oak laminate floor, matte' not 'nice flooring'; 'beige fabric sofa' not 'stylish sofa').\n\n"
        "Return ONLY the labeled fields below — no preamble, no commentary:\n\n"
        "SIGNATURE: [1–3 elements that most define this room's look as they ACTUALLY appear. Exact object + material + finish. Separate items with semicolons.]\n\n"
        "MATERIALS: [Major surfaces → exact material + finish. Format: object → material finish; object → material finish.]\n\n"
        "MOOD: [Exactly 5 plain sensory adjectives for how this space feels. No design jargon.]\n\n"
        "REALISM_CUES: [Concrete photographic cues that make it feel real rather than CGI/showroom: partial furniture cropping, asymmetry, lived-in props, imperfect sunlight patches, casual placement.]\n\n"
        "PROHIBITIONS: [3–4 things that clearly do NOT belong in this specific style, stated concretely.]"
    )
    payload = {
        "contents": [{"parts": [{"text": analysis_prompt}, {"inlineData": {"mimeType": mime, "data": img_b64}}]}],
        "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.1}
    }
    cfg = _load_config(); proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    # 按优先级依次尝试可用的文字视觉模型（列表来自配置，可在 engine_config.json 的 text_models 覆盖）
    _text_models = get_text_models()
    last_err = None
    for model_name in _text_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = _req.post(url, json=payload, timeout=_STYLE_ANALYZE_TIMEOUT, proxies=proxies, verify=_verify_arg(cfg))
            if resp.status_code == 200:
                for candidate in resp.json().get('candidates', []):
                    for part in candidate.get('content', {}).get('parts', []):
                        if 'text' in part:
                            logger.info(f"[参照模式] 风格分析使用模型: {model_name}")
                            _txt = part['text'].strip()
                            if cache_key:
                                _style_cache_put(cache_key, _txt)
                            return _txt, None
            elif resp.status_code in (404, 429, 500, 502, 503, 504):
                # 模型不可用或暂时性错误 → 尝试下一个备选模型
                last_err = f"HTTP {resp.status_code} on {model_name}"
                logger.warning(f"[参照模式] 模型 {model_name} 暂不可用({resp.status_code})，尝试下一个...")
                continue
            else:
                # 400/401/403 等密钥/请求级错误：换模型也没用，直接失败
                logger.error(f"[参照模式] 风格分析失败 HTTP {resp.status_code} on {model_name}")
                return "", f"HTTP {resp.status_code} on {model_name}"
        except Exception as e:
            last_err = _redact_api_key(e)
            logger.warning(f"[参照模式] 模型 {model_name} 请求异常: {last_err}")
            continue
    return "", f"所有备选模型均不可用，请检查 API Key 和网络（最后错误: {last_err}）"

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
    # ref 只用于取每通道全局均值/方差(Reinhard 统计量)，不参与逐像素运算。
    # 保持 uint8(4K≈50MB，而非 float32 的 ~192MB)，mean()/std() 以 float64 归约——
    # 统计量精确、与原算法数值等价，却省下一份大数组。
    r = np.asarray(ref, dtype=np.uint8)
    # s 用可写 float32 副本，逐通道原地变换 + 原地 clip，避免再开一份 out(~192MB)。
    # 峰值由 ~576MB(s+r+out 三份 float32) 降到 ~292MB(s float32 + r/out uint8)。
    s = np.array(src, dtype=np.float32)
    for c in range(3):
        s_mean, s_std = s[..., c].mean(), s[..., c].std()
        r_mean, r_std = float(r[..., c].mean()), float(r[..., c].std())
        if s_std < 1e-5:
            s[..., c] += (r_mean - s_mean)
        else:
            s[..., c] = (s[..., c] - s_mean) * (r_std / s_std) + r_mean
    np.clip(s, 0, 255, out=s)
    out = s.astype(np.uint8)
    del s, r
    transferred = Image.fromarray(out, mode='LAB').convert('RGB')

    # 按强度与原图混合（strength=1.0 时等价于原行为）
    if strength < 1.0:
        src_rgb = src_img.convert('RGB')
        return Image.blend(src_rgb, transferred, strength)
    return transferred


def _infer_aspect_ratio_from_b64(b64_str: str) -> str:
    img = _b64_to_pil(b64_str)
    if img is None or not img.width or not img.height:
        return "4:3"
    ratio = img.width / img.height
    candidates = [("1:1", 1.0), ("4:3", 4/3), ("3:4", 3/4), ("16:9", 16/9), ("9:16", 9/16)]
    return min(candidates, key=lambda x: abs(x[1] - ratio))[0]


def test_connection(gemini_api_key: str, fal_api_key: str = "", proxy: str = "") -> str:
    """提交前的轻量连通性自检（不生图、零成本），返回两行人类可读汇总。

    - Google 直连(公司线，最易被代理重置)：打 ListModels 接口（不挑模型名、不生成），
      只验「线路 + Key」。比 ping 具体模型稳——模型退役/改名都不会再误报 404。
    - Fal 线路(用户自费，无免费生成 ping)：仅对 fal.run 做可达性探测，不触发任何计费请求。
    """
    proxies = {"http": proxy.strip(), "https": proxy.strip()} if (proxy and proxy.strip()) else None
    _verify = _verify_arg()
    lines = []

    # ── Google 直连：ListModels 探测（不挑模型名、零成本、不生成）──
    # 用「列模型」而非 ping 具体模型：任何模型退役/改名都不会误报 404，
    # 只验证「线路 + Key」。200=好，401/403=Key 问题，超时/重置=线路真不通。
    gk = (gemini_api_key or "").strip()
    if not gk:
        lines.append("Google 直连：⚠️ 未填 Key")
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gk}"
        try:
            r = _req.get(url, timeout=15, proxies=proxies, verify=_verify)
            if r.status_code == 200:
                lines.append("Google 直连：✅ 正常")
            else:
                # 读真实报错体区分原因：地区封锁 vs Key 无效 vs 其它（400 不能一律算 Key 问题）
                try:
                    msg = ((r.json().get('error', {}) or {}).get('message', '')) or r.text[:200]
                except Exception:
                    msg = r.text[:200]
                low = msg.lower()
                if 'location is not supported' in low:
                    lines.append("Google 直连：❌ 落地地区不支持（geo-block，非 Key 问题；需换海外节点）")
                elif 'api key not valid' in low or 'api_key_invalid' in low or r.status_code in (401, 403):
                    lines.append(f"Google 直连：❌ Key 无效/无权限 (HTTP {r.status_code})")
                else:
                    lines.append(f"Google 直连：⚠️ HTTP {r.status_code}：{_short_text(msg, 120)}")
        except _req.exceptions.SSLError as e:
            lines.append(f"Google 直连：❌ 证书校验失败（网络在拦 HTTPS；设 tls_verify=false 或配 CA）：{_short_text(_redact_api_key(e), 100)}")
        except _req.exceptions.Timeout:
            lines.append("Google 直连：❌ 超时（代理/网络不通）")
        except Exception as e:
            lines.append(f"Google 直连：❌ 不通（{_redact_api_key(e)}）")

        # ── 证书校验状态（默认已开启；被显式关闭时探一下本网络能否安全开回）──
        eff = _verify_arg()
        if eff:
            lines.append("证书校验：✅ 已开启" + ("（自定义 CA）" if isinstance(eff, str) else ""))
        else:
            try:
                _req.get(url, timeout=15, proxies=proxies, verify=True)
                lines.append("证书校验：⚠️ 当前关闭，但本网络可开启（建议设 tls_verify=true）")
            except _req.exceptions.SSLError:
                lines.append("证书校验：当前关闭（开启会失败：网络在拦 HTTPS，需装 CA）")
            except Exception:
                lines.append("证书校验：当前关闭（暂无法判定能否开启）")

    # ── Fal 线路：可达性探测（任何 HTTP 响应都算可达；不计费）──
    fk = (fal_api_key or "").strip()
    if not fk:
        lines.append("Fal 线路：⚠️ 未配置 Key")
    else:
        try:
            _req.get("https://fal.run", timeout=10, proxies=proxies, verify=_verify)
            lines.append("Fal 线路：✅ 可达（未做计费校验）")
        except _req.exceptions.Timeout:
            lines.append("Fal 线路：❌ 超时（代理/网络不通）")
        except Exception as e:
            lines.append(f"Fal 线路：❌ 不可达（{_redact_api_key(e)}）")

    return "\n".join(lines)



__all__ = [
    'call_gemini_generate', 'call_fal_generate', 'call_image_generate',
    'call_gemini_edit', 'analyze_style_image', 'test_connection',
    'FLOOR_DESEAM_INSTRUCTION',
    '_match_color_to_reference', '_infer_aspect_ratio_from_b64',
]
