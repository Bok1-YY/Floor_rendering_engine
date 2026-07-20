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

from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR, CONFIG_FILE,
    GEMINI_MODEL_MAP, FAL_MODEL_MAP, DEFAULT_IMAGE_PROVIDER,
    logger, short_text, load_config, save_config,
    get_speed_profile_params,
    get_text_models, get_gen_sampling,
    get_inpaint_provider, get_comfyui_settings, get_inpaint_remove_prompt,
    get_inpaint_models,
)
from .records import (
    img_to_b64, b64_to_pil, save_api_result_jpg, api_write_to_record,
)


def _notify_stage(on_stage, txt) -> None:
    """进度回调守护:回调只是 UI 装饰性通知,它抛任何异常都不允许拖垮付费生图主流程。"""
    if on_stage:
        try:
            on_stage(txt)
        except Exception as ex:
            logger.debug(f"[进度回调] 忽略回调异常: {ex}")


_IMAGE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

# 上传前压缩默认参数：大图转 JPEG 并限制长边，避免走代理时上传体积过大导致写超时
# （如 20MB PNG → ~3.5MB JPEG）。可在 engine_config.json 覆盖：
#   upload_max_side(默认4096) / upload_jpeg_quality(默认92) / upload_compress_threshold_mb(默认4)
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


def call_gemini_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                         image_size: str = "4K", aspect_ratio: str = "4:3",
                         room_image_path: Optional[str] = None,
                         style_ref_image_path: Optional[str] = None,
                         on_stage=None, should_cancel=None,
                         bevel_ref_image_path: Optional[str] = None,
                         input_image_paths: Optional[list[str]] = None) -> Tuple[Optional[object], Optional[str]]:
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
                    logger.error(f"[API生成] HTTP失败 model={model_id}, status={resp.status_code}, err={short_text(err_msg, 800)}")
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
SD35_ENDPOINT = "fal-ai/stable-diffusion-v35-large"
SD35_IP_ADAPTER_PATH = "InstantX/SD3.5-Large-IP-Adapter"
# InstantX 当前主分支提供的是官方推理示例使用的 ip-adapter.bin；历史 safetensors 已删除。
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
            return None, "已取消"
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
                    return None, "已取消"
                time.sleep(0.5)
    return None, last_err or "Fal 请求失败"


def _call_fal_queue_json(api_key: str, endpoint: str, payload: dict, *, on_stage=None,
                         should_cancel=None, resume_handle: Optional[dict] = None,
                         on_submitted=None) -> Tuple[Optional[dict], Optional[str]]:
    """Fal 持久队列：只提交一次，随后轮询同一 request_id，避免长连接断线后重复计费。"""
    cfg = load_config()
    # Google 代理常会破坏 FAL 的大 POST/长轮询；SD 队列默认直连，确有需要再单配 fal_queue_proxy。
    proxy = str(cfg.get("fal_queue_proxy") or "").strip()
    session = _req.Session()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    else:
        session.trust_env = False
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
            # 提交响应丢失时无法判断服务器是否已接单，因此绝不自动重交；由用户显式重试。
            response = session.post(
                f"https://queue.fal.run/{endpoint}", json=payload, headers=headers,
                timeout=(30, 120), verify=verify,
            )
            if response.status_code not in (200, 201, 202):
                return None, f"队列提交 HTTP {response.status_code}: {_detail(response)}"
            queued = response.json()
        except Exception as ex:
            return None, f"队列提交网络错误（未自动重交）: {_redact_api_key(ex)}"

    status_url = str(queued.get("status_url") or "")
    response_url = str(queued.get("response_url") or "")
    cancel_url = str(queued.get("cancel_url") or "")
    if not status_url.startswith("https://queue.fal.run/") or not response_url.startswith("https://queue.fal.run/"):
        return None, "Fal 队列响应缺少有效状态地址"
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
            return None, "已取消"
        try:
            status_response = session.get(
                status_url, params={"logs": 1}, headers=headers,
                timeout=(15, 45), verify=verify,
            )
            # 排队/推理中 REST 状态接口使用 202；完成后使用 200。
            if status_response.status_code not in (200, 202):
                return None, f"队列状态 HTTP {status_response.status_code}: {_detail(status_response)}"
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
                    return None, f"队列取结果 HTTP {result_response.status_code}: {_detail(result_response)}"
                return result_response.json(), None
            if status in ("FAILED", "CANCELLED"):
                return None, f"Fal 队列任务{status}: {short_text(status_data, 600)}"
        except Exception as ex:
            poll_errors += 1
            if poll_errors >= 5:
                return None, f"队列状态网络错误: {_redact_api_key(ex)}"
        time.sleep(1.5)
    return None, "Fal 队列等待超时；任务可能仍在服务端运行，请稍后按原任务重试"


def _fal_image_from_result(data: dict, *, plural: bool = True, direct: bool = False):
    item = ((data.get("images") or [None])[0] if plural else data.get("image")) if isinstance(data, dict) else None
    url = item.get("url") if isinstance(item, dict) else None
    if not url:
        return None, "API 未返回图片"
    try:
        if url.startswith("data:"):
            raw = base64.b64decode(url.split(",", 1)[1])
        else:
            cfg = load_config()
            raw_proxy = cfg.get("fal_queue_proxy") if direct else cfg.get("proxy")
            proxy = str(raw_proxy or "").strip()
            session = _req.Session()
            if proxy:
                session.proxies.update({"http": proxy, "https": proxy})
            elif direct:
                session.trust_env = False
            resp = session.get(url, timeout=(30, 300), verify=_verify_arg(cfg))
            resp.raise_for_status()
            raw = resp.content
        image = Image.open(_io_mod.BytesIO(raw)); image.load()
        return image, None
    except Exception as ex:
        return None, f"解码失败: {_redact_api_key(ex)}"


def call_fal_sd35_generate(api_key: str, positive_prompt: str, negative_prompt: str,
                           floor_image_path: str, aspect_ratio: str = "4:3", *,
                           seed=None, steps: int = 28, guidance_scale: float = 3.5,
                           reference_strength: float = 0.5, on_stage=None,
                           should_cancel=None, queue_handle=None, on_queue_submitted=None):
    """Fal SD3.5 Large + InstantX IP-Adapter。返回 (PIL, error, seed)。"""
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
    image, decode_err = _fal_image_from_result(data, plural=True, direct=True)
    return image, decode_err, data.get("seed", seed) if data else seed


def call_fal_aura_upscale(api_key: str, image, *, on_stage=None, should_cancel=None,
                          queue_handle=None, on_queue_submitted=None):
    """AuraSR 4× 保守超分。返回 (PIL, error)。"""
    _notify_stage(on_stage, "🔎 4K 超分中…")
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


# ── 生成式修补（inpaint：画笔选区内移除/添加）──────────────────────────────
FLUX_FILL_ENDPOINT = "fal-ai/flux-pro/v1/fill"
# FLUX Fill 的 prompt 为必填；『生成式移除』留空时注入这句“延续周边背景”的替补描述
DEFAULT_INPAINT_REMOVE_PROMPT = (
    "Remove every foreground object inside the mask, including its complete shadow and reflection. "
    "Reconstruct only the background that logically continues from the surrounding unmasked floor, "
    "wall or interior surface. Keep the same material pattern, perspective, lighting and geometry. "
    "Leave the area empty; do not add furniture, decorations, people, text or watermarks."
)

DEFAULT_INPAINT_ADD_SUFFIX = (
    "Place it only inside the masked area and integrate it naturally into the existing interior. "
    "Match the scene's perspective, realistic scale, lighting, color temperature and contact shadow. "
    "Preserve the surrounding architecture, floor material and all unmasked content. "
    "Do not add text or watermarks."
)

# ── 裁剪回贴（Lightroom 式"选区级处理"，对 4K 图是清晰度的决定性提升）────────
# 云端生成模型的有效工作分辨率普遍 ≤2K：整图送入会让选区内细节被压缩摧毁。
# 做法：围绕选区裁一个含上下文的窗口送引擎 → 结果缩回窗口尺寸贴回原图 →
# 再走独立 blend mask 合成（blend_mask 为 0 的像素严格不变）。
_INPAINT_CROP_MAX_SIDE = 2048


def _crop_inpaint_context(image, mask, *, max_side: int = _INPAINT_CROP_MAX_SIDE,
                          mode: str = "remove"):
    """围绕 mask bbox 裁上下文窗口。返回 (crop_img, crop_mask, box)。

    移除上下文外扩 = max(bbox 长边 × 0.75, 256px)；添加为获得全局透视，
    使用 max(bbox 长边, 512px)。
    裁剪区长边 > max_side 时等比缩小（引擎侧工作分辨率），贴回时由
    _stitch_inpaint_result 缩回。mask 全空时退化为整图（调用方已校验非空）。
    """
    w, h = image.size
    bbox = mask.convert("L").point(lambda v: 255 if v >= 8 else 0).getbbox()
    if not bbox:
        return image, mask, (0, 0, w, h)
    bl, bt, br, bb = bbox
    longest = max(br - bl, bb - bt)
    # 添加物体需要看到更多全局透视/尺度；移除则优先保住局部纹理分辨率。
    pad = max(int(longest * (1.0 if mode == "add" else 0.75)), 512 if mode == "add" else 256)
    box = (max(0, bl - pad), max(0, bt - pad), min(w, br + pad), min(h, bb + pad))
    crop_img = image.crop(box)
    crop_mask = mask.crop(box)
    cw, ch = crop_img.size
    if max(cw, ch) > max_side:
        scale = max_side / max(cw, ch)
        work = (max(8, round(cw * scale)), max(8, round(ch * scale)))
        crop_img = crop_img.resize(work, Image.LANCZOS)
        # 发给模型的是二值 engine mask，缩放时不能用 LANCZOS 引入灰边。
        crop_mask = crop_mask.resize(work, Image.NEAREST)
    return crop_img, crop_mask, box


def _stitch_inpaint_result(original, result_crop, box):
    """引擎输出缩回裁剪区像素尺寸后贴回原图副本，返回全尺寸图。"""
    left, top, right, bottom = box
    target = (right - left, bottom - top)
    res = result_crop.convert("RGB")
    if res.size != target:
        res = res.resize(target, Image.LANCZOS)
    out = original.convert("RGB").copy()
    out.paste(res, (left, top))
    return out
# 专职移除模型（无 prompt/seed，涂哪擦哪并重建背景）：FLUX Fill 做移除会脑补新物体，
# Lightroom 式移除要用这类 eraser。三个模型共用 call_fal_mask_eraser 薄封装。
BRIA_ERASER_ENDPOINT = "fal-ai/bria/eraser"           # $0.04/次，输出单数 image
FINEGRAIN_ERASER_ENDPOINT = "fal-ai/finegrain-eraser/mask"  # 连阴影/反射一起移除
LAMA_ENDPOINT = "fal-ai/lama"                          # 传统修复，廉价备选
# model_key → (endpoint, mask 字段名, 额外 payload)
_FAL_ERASER_MODELS = {
    "bria-eraser": (BRIA_ERASER_ENDPOINT, "mask_url", {"mask_type": "manual"}),
    "finegrain-eraser": (FINEGRAIN_ERASER_ENDPOINT, "mask_url", {"mode": "express"}),
    "lama": (LAMA_ENDPOINT, "mask_image_url", {}),
}
# 指令语义模型：mask 语义是"编辑此处"而非"往洞里填东西"，移除时不易脑补新物体
QWEN_INPAINT_ENDPOINT = "fal-ai/qwen-image-edit/inpaint"
_INPAINT_USAGE_LABELS = {
    "bria-eraser": "BriaEraser",
    "finegrain-eraser": "FinegrainEraser",
    "lama": "LaMa",
    "flux-fill": "FluxFill",
    "qwen-inpaint": "QwenInpaint",
    "gemini-mark": "GeminiMark",
    "comfyui": "ComfyUI",
}


def resolve_inpaint_engine(mode: str):
    """按 (inpaint_provider, mode) 解析实际引擎。返回 (provider, model_key, usage_label)。
    api 与 server_api 共用，保证调用与记账一致。comfyui@fal 扩展位见 config.get_inpaint_provider。
    gemini-mark 走 Google 直连（用 gemini_api_key），其余云模型走 Fal。"""
    provider = get_inpaint_provider()
    if provider == "comfyui":
        return "comfyui", "comfyui", _INPAINT_USAGE_LABELS["comfyui"]
    model_key = get_inpaint_models()["remove" if mode == "remove" else "add"]
    engine_provider = "google" if model_key == "gemini-mark" else "fal"
    return engine_provider, model_key, _INPAINT_USAGE_LABELS.get(model_key, "FluxFill")


DEFAULT_QWEN_REMOVE_INSTRUCTION = (
    "Remove the masked objects completely, together with their shadows and reflections. "
    "Seamlessly continue the surrounding floor, wall and background textures. "
    "Do not add any new object, person, text or watermark."
)


def call_fal_qwen_inpaint(api_key: str, image, mask, prompt: str, *, mode: str = "remove",
                          seed=None, on_stage=None, should_cancel=None):
    """Qwen-Image-Edit inpaint（指令式，木纹等写实纹理保留好）。返回 (PIL, error, seed)。

    schema 已经 OpenAPI 核实：prompt/image_url/mask_url 必填，输出复数 images[]。
    remove 模式注入移除指令（用户 prompt 作补充说明拼在后面）。
    """
    _notify_stage(on_stage, "🖌️ Qwen 修补中…")
    text = (prompt or "").strip()
    if mode == "remove":
        text = DEFAULT_QWEN_REMOVE_INSTRUCTION + (f" Additional guidance: {text}" if text else "")
    else:
        text = f"Edit only the masked area. Add: {text}. {DEFAULT_INPAINT_ADD_SUFFIX}"
    binary_mask = mask.convert("L").point(lambda v: 255 if v >= 128 else 0)
    payload = {
        "prompt": text,
        "image_url": _pil_to_data_uri(image, fmt="JPEG"),
        "mask_url": _pil_to_data_uri(binary_mask, fmt="PNG"),
        "num_images": 1,
        "output_format": "png",
    }
    if seed is not None:
        payload["seed"] = int(seed)
    data, err = _call_fal_queue_json(api_key, QWEN_INPAINT_ENDPOINT, payload,
                                     on_stage=on_stage, should_cancel=should_cancel)
    if err:
        return None, err, seed
    image_out, decode_err = _fal_image_from_result(data, plural=True, direct=True)
    return image_out, decode_err, data.get("seed", seed) if data else seed


def call_gemini_mark_inpaint(api_key: str, image, mask, prompt: str, *, mode: str = "remove",
                             on_stage=None, should_cancel=None):
    """Gemini『红色标记引导』局部编辑：mask 区域叠半透明红标发给 Nano Banana Pro + 指令。

    红框/红色标记法是 Nano Banana 社区验证的精确局部编辑玩法（Google 官方 Markup 同理）。
    输出是整图重生成——选区外漂移由调度器的羽化合成回贴消除，两者恰好互补。
    复用 call_gemini_edit 的完整重试/取消机制。返回 (PIL, error)。
    """
    _notify_stage(on_stage, "🖌️ Gemini 标记修补中…")
    # 标记图：mask≥128 处叠 α≈0.45 的红色
    overlay = Image.new("RGB", image.size, (255, 40, 40))
    alpha = mask.convert("L").point(lambda v: 115 if v >= 128 else 0)
    marked = Image.composite(overlay, image.convert("RGB"), alpha)
    buf = _io_mod.BytesIO()
    marked.save(buf, format="JPEG", quality=95)
    b64 = base64.b64encode(buf.getvalue()).decode()
    ar = infer_aspect_ratio_from_b64(b64)

    extra = (prompt or "").strip()
    if mode == "remove":
        instruction = (
            "Some areas in this photo are covered with a translucent red marking. "
            "Completely remove the objects under the red marking, together with their shadows and reflections. "
            "Reconstruct the floor, wall and background behind them so the scene looks naturally empty there. "
            + (f"Additional guidance: {extra}. " if extra else "")
            + "Do not add any new objects. The red marking itself must not appear in the output. "
              "Everything outside the red marking must remain unchanged."
        )
    else:
        instruction = (
            f"Replace the area covered by the translucent red marking with: {extra}. "
            "Blend it naturally with the scene's lighting, perspective and scale. "
            "Add realistic contact shadows where appropriate. The red marking itself must not appear in the output. "
            "Everything outside the red marking must remain unchanged."
        )
    model_id = GEMINI_MODEL_MAP.get("Nano Banana Pro") or next(iter(GEMINI_MODEL_MAP.values()))
    # 裁剪窗口 ≤2048，2K 输出足够且比 4K 档便宜一半
    return call_gemini_edit(api_key, model_id, instruction, b64, "2K", ar, True,
                            on_stage, should_cancel)


def call_fal_mask_eraser(api_key: str, image, mask, *, model_key: str = "bria-eraser",
                         on_stage=None, should_cancel=None):
    """专职移除模型薄封装（BRIA / Finegrain / LaMa 共用）。返回 (PIL, error)。

    这些模型没有 prompt/seed；mask 语义白=移除区。BRIA 硬性要求二值 mask（255/0），
    这里统一 point 二值化（阈值 128 恰是羽化坡中点，与 grow 语义一致）——
    羽化灰度 mask 只用于本地合成回贴，不发给 eraser。
    """
    endpoint, mask_field, extra = _FAL_ERASER_MODELS.get(model_key, _FAL_ERASER_MODELS["bria-eraser"])
    _notify_stage(on_stage, "🧹 生成式移除中…")
    binary_mask = mask.convert("L").point(lambda v: 255 if v >= 128 else 0)
    payload = {
        "image_url": _pil_to_data_uri(image, fmt="JPEG"),
        mask_field: _pil_to_data_uri(binary_mask, fmt="PNG"),
        **extra,
    }
    data, err = _call_fal_queue_json(api_key, endpoint, payload,
                                     on_stage=on_stage, should_cancel=should_cancel)
    if err:
        return None, err
    return _fal_image_from_result(data, plural=False, direct=True)


def call_fal_inpaint(api_key: str, image, mask, prompt: str, *, seed=None,
                     guidance_scale: float = 3.5, on_stage=None, should_cancel=None):
    """FLUX Fill 真 inpainting：mask 白=重绘区，选区外由调度层合成兜底。返回 (PIL, error, seed)。

    image/mask 均为 PIL 且尺寸一致（FLUX Fill 硬性要求，由调用方 _prepare_inpaint_mask 保证）。
    image 走 JPEG q95 data URI 控制 POST 体积（4K PNG data URI 太大）；mask 黑白 PNG 压缩后极小。
    """
    _notify_stage(on_stage, "🖌️ 生成式修补中…")
    payload = {
        "prompt": prompt,
        "image_url": _pil_to_data_uri(image, fmt="JPEG"),
        "mask_url": _pil_to_data_uri(mask, fmt="PNG"),
        "num_images": 1,
        "output_format": "png",
        "safety_tolerance": "2",
        "guidance_scale": max(1.0, min(10.0, float(guidance_scale))),
    }
    if seed is not None:
        payload["seed"] = int(seed)
    data, err = _call_fal_queue_json(api_key, FLUX_FILL_ENDPOINT, payload,
                                     on_stage=on_stage, should_cancel=should_cancel)
    if err:
        return None, err, seed
    image_out, decode_err = _fal_image_from_result(data, plural=True, direct=True)
    return image_out, decode_err, data.get("seed", seed) if data else seed


# ComfyUI workflow 模板占位符：引擎只做字符串替换、不关心节点拓扑，
# 用户可在设置里指定任意自定义 workflow(API 格式)——只要写上这些占位符即可。
_COMFY_PLACEHOLDER_IMAGE = "__INPAINT_IMAGE__"
_COMFY_PLACEHOLDER_MASK = "__INPAINT_MASK__"
_COMFY_PLACEHOLDER_PROMPT = "__INPAINT_PROMPT__"
_COMFY_PLACEHOLDER_NEGATIVE = "__INPAINT_NEGATIVE__"
_COMFY_PLACEHOLDER_SEED = "__INPAINT_SEED__"
_COMFY_DEFAULT_WORKFLOW = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "comfy_workflows", "inpaint_default.json")


def _comfy_fill_workflow(node, replacements: dict):
    """深遍历 workflow JSON，替换占位符。字符串值精确等于占位符时用原始类型替换
    （seed 要求 int），否则做子串替换（prompt 可嵌进模板自带的修饰词里）。"""
    if isinstance(node, dict):
        return {k: _comfy_fill_workflow(v, replacements) for k, v in node.items()}
    if isinstance(node, list):
        return [_comfy_fill_workflow(v, replacements) for v in node]
    if isinstance(node, str):
        if node in replacements:
            return replacements[node]
        out = node
        for key, val in replacements.items():
            if key in out:
                out = out.replace(key, str(val))
        return out
    return node


def call_comfyui_inpaint(base_url: str, image, mask, prompt: str, *, negative_prompt: str = "",
                         seed=None, workflow_path: str = "", timeout: int = 600,
                         on_stage=None, should_cancel=None):
    """经外部 ComfyUI 实例做 inpaint：上传图/mask → 注入 workflow → /prompt → 轮询 /history。

    返回 (PIL, error, seed)。base_url 是可信内网地址(ComfyUI 无鉴权)；
    session.trust_env=False 防内网请求被系统代理劫持（同 Fal 队列的处理）。
    """
    def _stage(txt):
        _notify_stage(on_stage, txt)

    base = (base_url or "").strip().rstrip("/")
    if not base:
        return None, "未配置 ComfyUI 地址(请在设置里填写，如 http://127.0.0.1:8188)", seed
    session = _req.Session()
    session.trust_env = False

    # 1) 读 workflow 模板（自定义路径优先，空/失败给引导性错误）
    wf_path = (workflow_path or "").strip() or _COMFY_DEFAULT_WORKFLOW
    try:
        with open(wf_path, "r", encoding="utf-8") as f:
            template = json.load(f)
    except FileNotFoundError:
        return None, f"ComfyUI workflow 模板不存在: {wf_path}", seed
    except Exception as ex:
        return None, f"ComfyUI workflow 模板解析失败({wf_path}): {ex}", seed

    # 2) 上传 image / mask（uuid 前缀防撞名；overwrite 兜底）
    _stage("📤 上传到 ComfyUI…")
    uploaded = {}
    for tag, pil_img, pil_mode in (("image", image, "RGB"), ("mask", mask, "L")):
        buf = _io_mod.BytesIO()
        pil_img.convert(pil_mode).save(buf, format="PNG")
        name = f"floor_inpaint_{uuid.uuid4().hex[:12]}_{tag}.png"
        try:
            resp = session.post(
                f"{base}/upload/image",
                files={"image": (name, buf.getvalue(), "image/png")},
                data={"type": "input", "overwrite": "true"},
                timeout=(15, 120),
            )
            if resp.status_code != 200:
                return None, f"ComfyUI 上传失败 HTTP {resp.status_code}: {short_text(resp.text, 300)}", seed
            info = resp.json()
            sub = (info.get("subfolder") or "").strip()
            uploaded[tag] = f"{sub}/{info.get('name', name)}" if sub else info.get("name", name)
        except Exception as ex:
            return None, f"ComfyUI 连接失败({base}): {_redact_api_key(ex)}", seed

    # 3) 注入占位符并提交
    the_seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)
    workflow = _comfy_fill_workflow(template, {
        _COMFY_PLACEHOLDER_IMAGE: uploaded["image"],
        _COMFY_PLACEHOLDER_MASK: uploaded["mask"],
        _COMFY_PLACEHOLDER_PROMPT: prompt,
        _COMFY_PLACEHOLDER_NEGATIVE: negative_prompt or "",
        _COMFY_PLACEHOLDER_SEED: the_seed,
    })
    client_id = uuid.uuid4().hex
    try:
        resp = session.post(f"{base}/prompt", json={"prompt": workflow, "client_id": client_id},
                            timeout=(15, 60))
    except Exception as ex:
        return None, f"ComfyUI 提交失败: {_redact_api_key(ex)}", the_seed
    if resp.status_code != 200:
        try:
            detail = resp.json()
            node_errors = detail.get("node_errors") or {}
            if node_errors:
                first = next(iter(node_errors.values()))
                errs = first.get("errors") or []
                msg = errs[0].get("message") if errs else str(first)
                return None, f"ComfyUI workflow 校验失败: {short_text(msg, 300)}（常见原因：模板里的 checkpoint 在 ComfyUI 里不存在，请改模板或换自定义 workflow）", the_seed
            detail = detail.get("error") or detail
        except Exception:
            detail = resp.text[:300]
        return None, f"ComfyUI 提交 HTTP {resp.status_code}: {short_text(detail, 300)}", the_seed
    prompt_id = str(resp.json().get("prompt_id") or "")
    if not prompt_id:
        return None, "ComfyUI 未返回 prompt_id", the_seed

    # 4) 轮询 /history（节奏与容错对齐 _call_fal_queue_json：1.5s、连续 5 次网络错误才报错）
    deadline = time.time() + max(60, min(3600, int(timeout)))
    poll_errors = 0
    last_stage = ""
    while time.time() < deadline:
        if should_cancel and should_cancel():
            try:
                session.post(f"{base}/interrupt", timeout=(10, 30))
                session.post(f"{base}/queue", json={"delete": [prompt_id]}, timeout=(10, 30))
            except Exception as ex:
                logger.debug(f"[ComfyUI] 取消请求发送失败(尽力而为): {ex}")
            return None, "已取消", the_seed
        try:
            hist = session.get(f"{base}/history/{prompt_id}", timeout=(15, 45))
            hist.raise_for_status()
            entry = (hist.json() or {}).get(prompt_id)
            poll_errors = 0
            if entry:
                status = entry.get("status") or {}
                if str(status.get("status_str") or "").lower() == "error":
                    msgs = status.get("messages") or []
                    detail = next((m[1].get("exception_message") for m in msgs
                                   if isinstance(m, (list, tuple)) and len(m) > 1
                                   and isinstance(m[1], dict) and m[1].get("exception_message")), "")
                    return None, f"ComfyUI 执行失败: {short_text(detail or status, 300)}", the_seed
                for node_output in (entry.get("outputs") or {}).values():
                    images = node_output.get("images") or []
                    if images:
                        img_info = images[0]
                        view = session.get(f"{base}/view", params={
                            "filename": img_info.get("filename", ""),
                            "subfolder": img_info.get("subfolder", ""),
                            "type": img_info.get("type", "output"),
                        }, timeout=(30, 180))
                        view.raise_for_status()
                        out = Image.open(_io_mod.BytesIO(view.content)); out.load()
                        return out, None, the_seed
                return None, "ComfyUI 执行完成但未产出图片(请检查 workflow 是否含 SaveImage 节点)", the_seed
            # 尚未进 history → 区分排队/推理中
            try:
                queue = session.get(f"{base}/queue", timeout=(10, 30)).json()
                running = any(item[1] == prompt_id for item in (queue.get("queue_running") or []) if len(item) > 1)
                stage = "🎨 ComfyUI 推理中…" if running else "⏳ ComfyUI 排队中…"
                if stage != last_stage:
                    _stage(stage); last_stage = stage
            except Exception:
                pass
        except Exception as ex:
            poll_errors += 1
            if poll_errors >= 5:
                return None, f"ComfyUI 状态轮询网络错误: {_redact_api_key(ex)}", the_seed
        time.sleep(1.5)
    return None, "ComfyUI 等待超时；任务可能仍在执行，可稍后重试或调大超时", the_seed


def _composite_inpaint_result(original, result, mask):
    """Lightroom 语义兜底：引擎整图 VAE 往返会让选区外像素轻微漂移，
    这里用羽化 mask 把结果贴回原图——选区外严格保持原像素。"""
    try:
        res = result.convert("RGB")
        base_img = original.convert("RGB")
        if res.size != base_img.size:
            res = res.resize(base_img.size, Image.LANCZOS)
        m = mask.convert("L")
        if m.size != base_img.size:
            m = m.resize(base_img.size, Image.LANCZOS)
        return Image.composite(res, base_img, m)
    except Exception:
        logger.exception("[生成式修补] 合成回贴失败，退回引擎原始输出")
        return result


def effective_inpaint_candidate_count(mode: str, requested: int, *, resolved_engine=None) -> Tuple[int, str]:
    """专职 eraser 无 seed/变体参数，重复调用通常只会重复计费；服务端强制一次。"""
    provider, model_key, _ = resolved_engine or resolve_inpaint_engine(mode)
    count = max(1, min(3, int(requested)))
    if provider == "fal" and model_key in _FAL_ERASER_MODELS and count > 1:
        return 1, "当前专职移除模型不支持可控变体，已只生成 1 张以避免重复计费"
    return count, ""


def _instruction_inpaint_prompt(mode: str, text: str) -> str:
    if mode == "remove":
        guidance = text or get_inpaint_remove_prompt()
        return DEFAULT_INPAINT_REMOVE_PROMPT + (f" Additional guidance: {guidance}" if guidance else "")
    return f"Add the following requested content: {text}. {DEFAULT_INPAINT_ADD_SUFFIX}"


def call_image_inpaint(image, mask, prompt: str, *, blend_mask=None, mode: str = "remove", seed=None,
                       on_stage=None, should_cancel=None,
                       resolved_engine=None) -> Tuple[Optional[object], Optional[str], str, str]:
    """生成式修补调度器：按 (inpaint_provider, mode) 分派。

    返回 (PIL|None, 错误|None, provider, usage_label)——供用量归账（模型标签 + 线路）。
    - comfyui：remove/add 都走本地实例（模型由 workflow 模板自带）
    - fal + remove：专职 eraser（BRIA/Finegrain/LaMa，无 prompt/seed）；配成 flux-fill 时走旧路径
    - fal + add：FLUX Fill（prompt 必填由上游校验）
    不做自动 failover：各引擎出图风格差异大，静默切换会困惑用户。
    成功后用独立 blend mask 合成回原图；add 默认保证涂抹区外像素严格不变，
    remove 则以自动外扩后的有效处理范围为边界。
    """
    # 提交时可传入引擎快照，避免排队期间修改设置导致候选数判断与实际模型不一致。
    provider, model_key, usage_label = resolved_engine or resolve_inpaint_engine(mode)
    text = (prompt or "").strip()
    # Lightroom 式选区级处理：所有引擎都只看围绕选区的上下文窗口（等效原生分辨率）
    crop_img, crop_mask, box = _crop_inpaint_context(image, mask, mode=mode)
    logger.info(f"[生成式修补] start provider={provider} model={model_key} mode={mode} "
                f"size={getattr(image, 'size', '?')} crop={crop_img.size}@{box} prompt_len={len(text)}")
    if provider == "comfyui":
        text = _instruction_inpaint_prompt(mode, text)
        comfy = get_comfyui_settings()
        img, err, _ = call_comfyui_inpaint(
            comfy["base_url"], crop_img, crop_mask, text,
            negative_prompt=comfy["negative_prompt"], seed=seed,
            workflow_path=comfy["workflow_path"], timeout=comfy["timeout"],
            on_stage=on_stage, should_cancel=should_cancel)
    elif model_key == "gemini-mark":
        gemini_key = (load_config().get("gemini_api_key") or "").strip()
        if not gemini_key:
            return None, "未配置 Gemini API Key(Gemini 标记法需要它；或在设置里换一个修补模型)", provider, usage_label
        if mode == "remove" and not text:
            text = get_inpaint_remove_prompt()
        img, err = call_gemini_mark_inpaint(gemini_key, crop_img, crop_mask, text, mode=mode,
                                            on_stage=on_stage, should_cancel=should_cancel)
    else:
        fal_key = (load_config().get("fal_api_key") or "").strip()
        if not fal_key:
            return None, "未配置 Fal API Key(请在设置里填写，或把修补引擎切到 ComfyUI)", provider, usage_label
        if model_key == "qwen-inpaint":
            img, err, _ = call_fal_qwen_inpaint(fal_key, crop_img, crop_mask, text, mode=mode,
                                                seed=seed, on_stage=on_stage, should_cancel=should_cancel)
        elif model_key in _FAL_ERASER_MODELS:
            # 专职移除模型：无 prompt/seed，用户输入的描述在此路径被忽略
            img, err = call_fal_mask_eraser(fal_key, crop_img, crop_mask, model_key=model_key,
                                            on_stage=on_stage, should_cancel=should_cancel)
        else:
            text = _instruction_inpaint_prompt(mode, text)
            img, err, _ = call_fal_inpaint(fal_key, crop_img, crop_mask, text, seed=seed,
                                           on_stage=on_stage, should_cancel=should_cancel)
    if img is not None:
        full = _stitch_inpaint_result(image, img, box)
        img = _composite_inpaint_result(image, full, blend_mask if blend_mask is not None else mask)
    return img, err, provider, usage_label


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
    fal_map = cfg.get("fal_model_map") or FAL_MODEL_MAP
    endpoint = fal_map.get(model_id) or FAL_MODEL_MAP.get(model_id)
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
                logger.error(f"[Fal生成] HTTP失败 model={model_id}, status={resp.status_code}, err={short_text(err_msg, 800)}")
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
            logger.error(f"[Fal生成] API未返回图片 model={model_id}, resp={short_text(data, 600)}")
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
                        bevel_ref_image_path: Optional[str] = None,
                        input_image_paths: Optional[list[str]] = None) -> Tuple[Optional[object], Optional[str], str]:
    """生图调度器:按 engine_config.json 的 image_provider 选线路,两条线路同契约。

    返回 (PIL.Image|None, 错误字符串|None, provider)——provider∈{'google','fal'} 是【实际】出图/尝试
    的线路(自动转 Fal 后即 'fal')，供用量统计准确归账，不再靠读配置猜测。

    - 'google'(默认):直连 Google AI Studio,沿用传入的 Gemini api_key。
    - 'fal':走 Fal 路由,改用 config 里的 fal_api_key(忽略传入的 Gemini key)。
    - 自动转线(auto_failover):线路=google 时,若直连因【网络类失败】重试耗尽且本开关开启、
      已配 Fal Key、任务未取消,则自动改走 Fal 再跑一次(用户自己的 key)。内容/请求级错误不转。
    - should_cancel(): 透传给底层,任务取消后立即停止重试,不再产生新的计费请求。
    """
    cfg = load_config()
    provider = (cfg.get("image_provider") or DEFAULT_IMAGE_PROVIDER).strip().lower()
    if provider == "fal":
        fal_key = (cfg.get("fal_api_key") or "").strip()
        if not fal_key:
            logger.error("[生图调度] 线路=fal 但未配置 Fal API Key")
            return None, "未配置 Fal API Key(请在 API 设置里填写 Fal Key)", "fal"
        img, err = call_fal_generate(fal_key, model_id, prompt_text, image_path, image_size,
                                     aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel,
                                     bevel_ref_image_path=bevel_ref_image_path,
                                     input_image_paths=input_image_paths)
        return img, err, "fal"

    # ── 线路=google：先走直连 ──
    img, err = call_gemini_generate(api_key, model_id, prompt_text, image_path, image_size,
                                    aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel,
                                    bevel_ref_image_path=bevel_ref_image_path,
                                    input_image_paths=input_image_paths)
    if img is not None:
        return img, err, "google"

    # ── 直连失败 → 评估是否自动转 Fal 备用线路 ──
    auto = bool(cfg.get("auto_failover", False))
    fal_key = (cfg.get("fal_api_key") or "").strip()
    cancelled = bool(should_cancel and should_cancel())
    if auto and fal_key and not cancelled and _is_network_class_error(err):
        logger.warning(f"[生图调度] Google 直连网络类失败，自动转 Fal 备用线路 model={model_id}: {_redact_api_key(err)}")
        _notify_stage(on_stage, "🔁 直连失败，转 Fal 备用线路…")
        fb_img, fb_err = call_fal_generate(fal_key, model_id, prompt_text, image_path, image_size,
                                           aspect_ratio, room_image_path, style_ref_image_path, on_stage, should_cancel,
                                           bevel_ref_image_path=bevel_ref_image_path,
                                           input_image_paths=input_image_paths)
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
        logger.error(f"[API二改] HTTP失败 model={model_id}, status={resp.status_code}, err={short_text(err_msg, 800)}")
        return None, f"HTTP {resp.status_code}: {err_msg}"
    try:
        data = resp.json()
    except Exception as e:
        # 透明代理劫持/半截响应可能 200 但 body 非 JSON——不能让 JSONDecodeError 冒出破坏 (img, err) 契约
        logger.error(f"[API二改] 响应 JSON 解析失败 model={model_id}: {_redact_api_key(e)}")
        return None, f"响应解析失败: {_redact_api_key(e)}"
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
    logger.error(f"[API二改] API未返回图片 model={model_id}, response={short_text(data, 1000)}")
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


class _StyleCache:
    """磁盘风格分析缓存:懒加载 + 锁保护 + 原子写(收敛原先的模块级 global 状态)。"""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data = None  # 懒加载

    def _load(self) -> dict:
        if self._data is None:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                if not isinstance(self._data, dict):
                    self._data = {}
            except Exception:
                self._data = {}
        return self._data

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._load().get(key)

    def put(self, key: str, text: str) -> None:
        with self._lock:
            c = self._load(); c[key] = text
            try:
                tmp = self._path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(c, f, ensure_ascii=False)
                os.replace(tmp, self._path)
            except Exception as e:
                logger.warning(f"[参照模式] 风格分析缓存写入失败: {e}")


_style_cache = _StyleCache(_STYLE_CACHE_FILE)


def _style_cache_key(raw_bytes: bytes) -> str:
    return f"{_STYLE_CACHE_VERSION}:{hashlib.sha256(raw_bytes).hexdigest()}"


def _style_cache_get(key: str) -> Optional[str]:
    return _style_cache.get(key)


def _style_cache_put(key: str, text: str) -> None:
    return _style_cache.put(key, text)


def analyze_style_image(api_key: str, image_path: str) -> Tuple[str, Optional[str]]:
    """Step-1 of 参照模式: call Gemini text API to extract a precise style blueprint from a reference room photo.

    返回 (风格描述文本, None) 或 ("", 错误信息)。调用方必须检查错误并中止生图——
    错误文本绝不能混进生图提示词（曾因此把 "(Style analysis failed...)" 拼进计费请求）。
    """
    if not image_path or not os.path.exists(image_path):
        return "", "参照图不存在"
    img_b64, mime = _read_image_b64(image_path)
    # 缓存：同一张参照图(内容哈希)命中即免请求、免计费、秒回
    cache_on = bool(load_config().get("style_analysis_cache", True))
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
    cfg = load_config(); proxy = cfg.get("proxy", "").strip()
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


# ── Omakase 模式：Gemini 主线路 + DeepSeek 备用线路────
# 只产候选、不写最终提示词；地板技术层永远由 prompts.py 焊接(见 save_task_files_html 的 scene_override)。
_OMAKASE_TIMEOUT = (10, 40)  # (连接, 读取) 秒；纯文本调用短平快，比生图/风格分析更快

_OMAKASE_SYSTEM_PROMPT = (
    "你是地板营销摄影的美术指导。客户会给你一句关于想要什么照片的诉求(可能很抽象，比如只说一种功能卖点或一种情绪)。"
    "你的任务：把它转成 2-3 段【互不雷同、具体可拍】的居家室内场景中文散文，每段 2-4 句，用来拍一张突出【地板】的室内实景照。\n\n"
    "硬性规则：\n"
    "1. 地板必须是画面视觉重点；场景里的家具/人物/道具都不能把地板埋掉或遮挡主要地面。\n"
    "2. 只写真实、可拍的居家场景；不堆奢华辞藻，不出现任何品牌名。\n"
    "3. 绝对不要描写地板的划痕/磨损/损坏，也不要任何『前后对比/好坏对比』画面——"
    "要体现耐用等功能，就写『高强度使用但地板依然完好如新』的正向场景。\n"
    "4. 每段只写场景/氛围/光线/人物活动；不要写相机参数，也不要写地板的物理规格(尺寸/拼缝/光泽/颜色)——那些由系统另行控制。\n\n"
    "只返回 JSON，格式：{\"options\":[{\"text\":\"场景散文\",\"why\":\"一句话说明为什么这么拍能体现客户诉求\",\"recommended\":true}]}。"
    "恰好把最稳妥的一段标 recommended:true、其余为 false。不要输出 JSON 以外的任何内容。"
)


_OMAKASE_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "options": {
            "type": "ARRAY",
            "minItems": 2,
            "maxItems": 3,
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "why": {"type": "STRING"},
                    "recommended": {"type": "BOOLEAN"},
                },
                "required": ["text", "why", "recommended"],
            },
        },
    },
    "required": ["options"],
}


def _clean_omakase_options(options):
    """Normalize provider output and enforce exactly one recommended option."""
    clean = []
    for option in options or []:
        if isinstance(option, dict) and (option.get("text") or "").strip():
            clean.append({
                "text": str(option.get("text")).strip(),
                "why": str(option.get("why") or "").strip(),
                "recommended": bool(option.get("recommended", False)),
            })
            if len(clean) == 3:
                break
    if not clean:
        return []
    recommended_index = next((i for i, option in enumerate(clean) if option["recommended"]), 0)
    for i, option in enumerate(clean):
        option["recommended"] = i == recommended_index
    return clean


def call_gemini_scenes(idea, *, api_key, model):
    """Generate Omakase scene candidates with the existing Gemini API key."""
    idea = (idea or "").strip()
    if not idea:
        return [], "场景诉求为空"
    if not api_key:
        return [], "未配置 Gemini API Key"
    cfg = load_config()
    proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": _OMAKASE_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": idea}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2500,
            "responseMimeType": "application/json",
            "responseSchema": _OMAKASE_RESPONSE_SCHEMA,
        },
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    try:
        resp = _req.post(url, headers=headers, json=payload, timeout=_OMAKASE_TIMEOUT,
                         proxies=proxies, verify=_verify_arg(cfg))
    except Exception as e:
        return [], f"Omakase Gemini 请求异常: {_redact_api_key(e)}"
    if resp.status_code != 200:
        body = short_text(getattr(resp, "text", ""), 200)
        return [], f"Omakase Gemini HTTP {resp.status_code}: {body}"
    try:
        content = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        options = _clean_omakase_options((json.loads(content) or {}).get("options"))
    except Exception as e:
        return [], f"Omakase Gemini 返回解析失败: {_redact_api_key(e)}"
    if not options:
        return [], "Omakase Gemini 未返回可用场景"
    return options, None


def call_deepseek_scenes(idea, *, api_key, base_url, model):
    """Omakase：把客户诉求 idea 交给 DeepSeek，返回 (options, None) 或 ([], 错误信息)。

    options 是 [{text, why, recommended}]，供前端做选择题给客户选/改。
    错误绝不冒泡——调用方按 (结果, 错误) 元组处理(照 analyze_style_image 约定)。
    """
    idea = (idea or "").strip()
    if not idea:
        return [], "场景诉求为空"
    if not api_key:
        return [], "未配置 DeepSeek API Key"
    cfg = load_config()
    proxy = cfg.get("proxy", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _OMAKASE_SYSTEM_PROMPT},
            {"role": "user", "content": idea},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = _req.post(url, headers=headers, json=payload,
                         timeout=_OMAKASE_TIMEOUT, proxies=proxies, verify=_verify_arg(cfg))
    except Exception as e:
        return [], f"DeepSeek 请求异常: {_redact_api_key(e)}"
    if resp.status_code != 200:
        # 401 / 402(余额不足) / 429(限流) 等交给 failure_kb 分类
        body = short_text(getattr(resp, "text", ""), 200)
        return [], f"DeepSeek HTTP {resp.status_code}: {body}"
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        options = (json.loads(content) or {}).get("options") or []
    except Exception as e:
        return [], f"DeepSeek 返回解析失败: {_redact_api_key(e)}"
    clean = _clean_omakase_options(options)
    if not clean:
        return [], "DeepSeek 未返回可用场景"
    return clean, None


def call_omakase_scenes(idea, *, gemini_api_key, gemini_model,
                        deepseek_api_key="", deepseek_base_url="https://api.deepseek.com",
                        deepseek_model="deepseek-chat"):
    """Route Omakase through Gemini first, then DeepSeek when configured."""
    gemini_error = None
    if (gemini_api_key or "").strip():
        options, gemini_error = call_gemini_scenes(
            idea, api_key=gemini_api_key, model=gemini_model)
        if not gemini_error:
            return options, None, "gemini", False

    if (deepseek_api_key or "").strip():
        if gemini_error:
            logger.warning(f"[Omakase] Gemini 主线路失败，自动转 DeepSeek: "
                           f"{short_text(gemini_error, 300)}")
        options, deepseek_error = call_deepseek_scenes(
            idea, api_key=deepseek_api_key, base_url=deepseek_base_url,
            model=deepseek_model)
        if not deepseek_error:
            return options, None, "deepseek", True
        if gemini_error:
            return ([], f"Omakase Gemini 主线路失败: {gemini_error}; "
                    f"DeepSeek 备用线路失败: {deepseek_error}", "deepseek", True)
        return [], deepseek_error, "deepseek", True

    if gemini_error:
        return [], gemini_error, "gemini", False
    return [], "Omakase 未配置 Gemini 或 DeepSeek API Key", "", False


def infer_aspect_ratio_from_b64(b64_str: str) -> str:
    img = b64_to_pil(b64_str)
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
                    lines.append(f"Google 直连：⚠️ HTTP {r.status_code}：{short_text(msg, 120)}")
        except _req.exceptions.SSLError as e:
            lines.append(f"Google 直连：❌ 证书校验失败（网络在拦 HTTPS；设 tls_verify=false 或配 CA）：{short_text(_redact_api_key(e), 100)}")
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
    'call_gemini_scenes', 'call_deepseek_scenes', 'call_omakase_scenes',
    'FLOOR_DESEAM_INSTRUCTION',
    'infer_aspect_ratio_from_b64',
]
