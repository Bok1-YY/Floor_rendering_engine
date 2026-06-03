from .config import *
from .records import *

def _redact_api_key(text):
    return re.sub(r'([?&]key=)[^&\s)]+', r'\1***', str(text or ""))

def call_gemini_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                         image_size: str = "4K", aspect_ratio: str = "4:3",
                         room_image_path: str = None, style_ref_image_path: str = None):
    import requests as _req
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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
    parts = [{"text": prompt_text}]
    if sref_b64: parts.append({"inlineData": {"mimeType": sref_mime, "data": sref_b64}})
    if room_b64: parts.append({"inlineData": {"mimeType": room_mime, "data": room_b64}})
    parts.append({"inlineData": {"mimeType": "image/png", "data": floor_b64}})
    payload = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"imageSize": image_size, "aspectRatio": aspect_ratio}}}
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
            logger.warning(f"[API生成] 网络异常 attempt={attempt+1}/3 model={model_id}: {_redact_api_key(e)}")
            if attempt < 2: time.sleep(2 ** attempt)
        except _req.exceptions.Timeout:
            logger.error(f"[API生成] 请求超时 model={model_id}")
            return None, "请求超时"
        except Exception as e:
            logger.exception(f"[API生成] 未预期网络错误 model={model_id}")
            return None, f"网络错误: {e}"
    if last_err is not None:
        logger.error(f"[API生成] 网络重试失败 model={model_id}: {_redact_api_key(last_err)}")
        return None, f"网络错误: {_redact_api_key(last_err)}"
    if resp.status_code != 200:
        try:
            err_info = resp.json()
            err_msg = err_info.get('error', {}).get('message', resp.text[:400]) if 'error' in err_info else resp.text[:400]
        except Exception:
            err_msg = resp.text[:400]
        logger.error(f"[API生成] HTTP失败 model={model_id}, status={resp.status_code}, err={_short_text(err_msg, 800)}")
        return None, f"HTTP {resp.status_code}: {err_msg}"
    data = resp.json()
    for candidate in data.get('candidates', []):
        for part in candidate.get('content', {}).get('parts', []):
            if 'inlineData' in part:
                try:
                    img_bytes = base64.b64decode(part['inlineData']['data'])
                    pil_img = Image.open(_io_mod.BytesIO(img_bytes)); pil_img.load()
                    logger.info(f"[API生成] success model={model_id}, image={pil_img.width}x{pil_img.height}")
                    return pil_img, None
                except Exception as e:
                    logger.exception(f"[API生成] 图片解码失败 model={model_id}")
                    return None, f"解码失败: {e}"
    safety_blocks = [r.get('category', '') for c in data.get('candidates', []) for r in c.get('safetyRatings', []) if r.get('blocked')]
    if safety_blocks:
        logger.error(f"[API生成] 安全拦截 model={model_id}: {', '.join(safety_blocks)}")
        return None, f"安全拦截: {', '.join(safety_blocks)}"
    logger.error(f"[API生成] API未返回图片 model={model_id}, response={_short_text(data, 1000)}")
    return None, "API 未返回图片"

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
