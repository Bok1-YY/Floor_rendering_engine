"""Analysis provider implementation; no dependency on the public API facade."""
import os
import time
import json
import threading
import hashlib
from typing import Optional, Tuple


import requests as _req
import urllib3

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ..config import MAIN_OUTPUT_DIR, logger, short_text, load_config, get_text_models
from .common import ambiguous_provider_error, attempt_event, is_safe_pre_submit_exception

from .shared import _read_image_b64, _redact_api_key, _verify_arg

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
        "generationConfig": {"maxOutputTokens": 1000}
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
            if is_safe_pre_submit_exception(e):
                logger.warning(f"[参照模式] 模型 {model_name} 连接前失败，尝试下一个: {last_err}")
                continue
            logger.warning(f"[参照模式] 模型 {model_name} 提交后连接中断，不自动换模型: {last_err}")
            return "", ambiguous_provider_error(
                f"结果状态不确定：风格分析请求提交后连接中断，系统未自动重提: {last_err}",
                failure_code='google_style_analysis_unknown',
                attempts=[attempt_event(1, 'submitted', time.time(), outcome='ambiguous_failure')])
    return "", f"所有备选模型均不可用，请检查 API Key 和网络（最后错误: {last_err}）"

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
    "5. 当场景含人物或宠物时，要写一个正在发生但尚未完成的自然动作，说明摄影机所处的现实观察位置；"
    "拒绝看镜头摆拍、夸张表情、漂浮姿势和商业广告式互动。\n"
    "6. 光必须来自窗户、灯具或其他画面内可解释来源；皮肤、毛发、衣料、接触阴影和家具尺度必须真实。\n"
    "7. 电影感来自机位、动作、视线关系和现实光源，不靠黑边、青橙滤镜、重颗粒、烟雾、轮廓光或过度虚化。\n"
    "8. subject_type 必须准确标注场景生命主体：none/person/pet/both。\n\n"
    "只返回 JSON，格式：{\"options\":[{\"text\":\"场景散文\",\"why\":\"一句话说明为什么这么拍能体现客户诉求\","
    "\"recommended\":true,\"subject_type\":\"person\"}]}。"
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
                    "subject_type": {
                        "type": "STRING",
                        "enum": ["none", "person", "pet", "both"],
                    },
                },
                "required": ["text", "why", "recommended", "subject_type"],
            },
        },
    },
    "required": ["options"],
}

def _clean_omakase_options(options):
    """Normalize provider output and enforce exactly one recommended option."""
    clean = []
    allowed_subjects = {"none", "person", "pet", "both"}
    for option in options or []:
        if isinstance(option, dict) and (option.get("text") or "").strip():
            subject_type = str(option.get("subject_type") or "none").strip().lower()
            if subject_type not in allowed_subjects:
                subject_type = "none"
            clean.append({
                "text": str(option.get("text")).strip(),
                "why": str(option.get("why") or "").strip(),
                "recommended": bool(option.get("recommended", False)),
                "subject_type": subject_type,
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
        logger.warning(
            "[Omakase] Gemini 主线路失败 model=%s fallback=%s err=%s",
            gemini_model,
            bool((deepseek_api_key or "").strip()),
            short_text(_redact_api_key(gemini_error), 500),
        )

    if (deepseek_api_key or "").strip():
        options, deepseek_error = call_deepseek_scenes(
            idea, api_key=deepseek_api_key, base_url=deepseek_base_url,
            model=deepseek_model)
        if not deepseek_error:
            return options, None, "deepseek", True
        logger.warning(
            "[Omakase] DeepSeek 线路失败 model=%s err=%s",
            deepseek_model,
            short_text(_redact_api_key(deepseek_error), 500),
        )
        if gemini_error:
            return ([], f"Omakase Gemini 主线路失败: {gemini_error}; "
                    f"DeepSeek 备用线路失败: {deepseek_error}", "deepseek", True)
        return [], deepseek_error, "deepseek", True

    if gemini_error:
        return [], gemini_error, "gemini", False
    return [], "Omakase 未配置 Gemini 或 DeepSeek API Key", "", False

omakase = call_omakase_scenes
