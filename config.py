# ==========================================
# 地板 AI 智能提示词引擎 — 核心配置
# 版本: v6.0.1
# ==========================================
"""Core configuration: paths, API key persistence, utility functions.

Theme definitions → themes.py
Logging setup     → logging_setup.py
"""

import os
import sys
import json
import re
import time
import logging
from typing import Dict, Optional, Tuple, Union

# ── 路径常量 ────────────────────────────────────────────────────
# 打包(PyInstaller)后 __file__ 位于临时解压目录(_MEIxxx)，必须改用 exe 所在目录，
# 否则 output_files/engine_config.json 会写进临时目录、程序一关就丢。
# 开发(源码)运行时仍用包的上级目录。webui.py 的 UPLOAD_DIR 用的是同款写法。
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_OUTPUT_DIR = os.path.join(BASE_DIR, "output_files")
os.makedirs(MAIN_OUTPUT_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(BASE_DIR, "engine_config.json")

# 上传原图 + 缩略图缓存目录（webui 上传/历史小样扫描、records 小样扫描共用，故放配置层）。
# BASE_DIR 已是 frozen-aware（打包时 = exe 所在目录），与旧 webui 里的 sys.frozen 写法等价。
UPLOAD_DIR = os.path.join(BASE_DIR, "_ng_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
THUMB_DIR = os.path.join(os.path.dirname(UPLOAD_DIR), "_ng_thumbs")  # 缩略图缓存，与 _ng_uploads 同级
os.makedirs(THUMB_DIR, exist_ok=True)

# ── 日志（委托给 logging_setup，这里保持向后兼容的 logger 导出）──
from .logging_setup import logger  # noqa: E402

# ── 翻译模块 ────────────────────────────────────────────────────
# 在线翻译后端 = deep-translator 的 GoogleTranslator（原 MyMemory 国外端点常被软路由
# reset，且不吃下面的代理配置，已弃用）。GoogleTranslator 支持传 proxies，复用生图侧
# 同一套代理逻辑（见 prompt_data.translate_zh_to_en）。
try:
    from deep_translator import GoogleTranslator  # noqa: F401
    TRANSLATOR_AVAILABLE = True
except ImportError:
    print("⚠️ 未检测到 deep-translator 库！请运行: pip install deep-translator")
    TRANSLATOR_AVAILABLE = False

print("--- 系统启动 ---")

# ── 主题系统（向后兼容：从 themes.py 重导出）──────────────────
from .themes import THEMES, build_theme_css  # noqa: E402

# 旧名称别名（兼容 webui 中的 _build_theme_css 调用）
_build_theme_css = build_theme_css

# ── Gemini 模型映射 ─────────────────────────────────────────────
GEMINI_MODEL_MAP = {
    "Nano Banana 2":  "gemini-3.1-flash-image-preview",
    "Nano Banana Pro": "gemini-3-pro-image-preview",
}

# ── Fal 路由模型映射 ────────────────────────────────────────────
# 把同一批 Nano Banana 模型改走 Fal 的图生图(/edit)端点：同模型、保真/4K 不变，
# 只换更稳的线路(国内→Fal→Google)。key = 上面 GEMINI_MODEL_MAP 里的 Gemini model_id，
# value = Fal endpoint id。可在 engine_config.json 的 "fal_model_map" 里覆盖。
FAL_MODEL_MAP = {
    "gemini-3.1-flash-image-preview": "fal-ai/nano-banana-2/edit",   # Nano Banana 2
    "gemini-3-pro-image-preview":     "fal-ai/nano-banana-pro/edit",  # Nano Banana Pro
}

# 生图线路：'google' = 直连 Google AI Studio(默认)；'fal' = 走 Fal 路由
DEFAULT_IMAGE_PROVIDER = "google"


# ── 工具函数 ────────────────────────────────────────────────────

def _short_text(text: str, limit: int = 500) -> str:
    """截断文本用于日志显示，去除多余空白。"""
    text = "" if text is None else str(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def is_seamless_herringbone(floor_size: str, seam_type: str) -> bool:
    """统一判断：无缝人字拼。"""
    return ('人字拼' in (floor_size or '')) and ('无缝' in (seam_type or ''))


# ── 配置文件管理 ────────────────────────────────────────────────

def _load_config() -> Dict[str, str]:
    """加载 engine_config.json，不存在则返回默认空配置。"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"gemini_api_key": ""}


# ── 圆弧倒角内置参考图 ──────────────────────────────────────────
# 选「圆弧倒角」时自动把这张压圆弧倒角实拍当作"板边形状参考"喂给生图模型(B2/Pro 都加)。
# 模型只参考它的【倒角凹槽形状】,颜色/木纹/光线仍取地板小样。
# 默认用 clean_a:软凹槽可辨但不发黑,贴合"无缝底+圆边过渡、近无缝以高光为主"的现行方向
# (旧 bevel_ref.jpg 凹槽偏深、易被渲成黑线,已退役但保留)。备选 clean_b / clean_c 也在
# assets/ 里——可在 engine_config.json 的 "bevel_ref_image" 字段换成任意自定义路径。
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
BEVEL_REF_IMAGE_DEFAULT = os.path.join(_PKG_DIR, "assets", "bevel_ref_clean_a.jpg")

# 浏览器标签页图标（favicon）。SVG 现代浏览器原生支持；ASCII 文件名避免非 ASCII 路径坑。
FAVICON_PATH = os.path.join(_PKG_DIR, "assets", "logo.svg")

def get_bevel_ref_image() -> str:
    """返回当前圆弧倒角参考图路径(配置可覆盖);文件不存在则返回空串(自动降级为纯文字)。"""
    cfg = _load_config()
    p = (cfg.get("bevel_ref_image") or "").strip() or BEVEL_REF_IMAGE_DEFAULT
    return p if os.path.exists(p) else ""


def _save_config(config: Dict[str, str]) -> bool:
    """持久化配置到 engine_config.json。"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"配置保存失败: {e}")
        return False


def save_api_key(api_key_val: str, proxy_val: str = "") -> None:
    """保存 Gemini API key 和代理设置。"""
    key = (api_key_val or "").strip()
    proxy = (proxy_val or "").strip()
    cfg = _load_config()
    cfg["gemini_api_key"] = key
    cfg["proxy"] = proxy
    _save_config(cfg)


def get_proxy() -> str:
    """读取本地代理地址（engine_config.json 的 proxy 字段）；为空 → 默认走软路由(透明代理)。
    生图侧到处用 cfg.get("proxy","").strip() 这同一个值；翻译也复用它。"""
    return (_load_config().get("proxy") or "").strip()


def save_provider_settings(fal_api_key_val: Optional[str] = None,
                           image_provider_val: Optional[str] = None) -> None:
    """保存 Fal API key 和生图线路选择(google / fal)。传 None 的字段不改动。"""
    cfg = _load_config()
    if fal_api_key_val is not None:
        cfg["fal_api_key"] = (fal_api_key_val or "").strip()
    if image_provider_val is not None:
        prov = (image_provider_val or "").strip().lower()
        cfg["image_provider"] = prov if prov in ("google", "fal") else "google"
    _save_config(cfg)


def get_image_provider() -> str:
    """读取当前生图线路；非法值回落到 google。"""
    prov = (_load_config().get("image_provider") or DEFAULT_IMAGE_PROVIDER).strip().lower()
    return prov if prov in ("google", "fal") else "google"


# ── HTTPS 证书校验（可配置；2026-06-18 连通性自检证实本网络 verify=True 能通过，默认改为开启）──
# 历史上走 verify=False 是怕软路由(透明代理)做 TLS 解密导致证书错；实测本机软路由不拦 TLS、校验能过，
# 故默认开启更安全（尤其保护用户自费的 Fal key）。若换到会拦 HTTPS 的网络报证书错（见 failure_kb 的 tls_cert）：
# 在 engine_config.json 设 tls_verify=false 关闭，或把代理根证书路径填到 tls_ca_bundle。
def get_tls_verify() -> bool:
    """是否启用 HTTPS 证书校验。默认 True（已实测本网络可过）；坏网络可设 false 关闭。"""
    return bool(_load_config().get("tls_verify", True))


def get_tls_ca_bundle() -> str:
    """自定义 CA 证书路径（如软路由/代理的根证书）；为空 → 用系统/requests 默认 CA。"""
    return (_load_config().get("tls_ca_bundle") or "").strip()


# ── 上传文件名安全化 ────────────────────────────────────────────────────
# 用户上传的原始文件名不可信：可能带路径(../、C:\)、危险字符、或与已有文件同名导致静默覆盖。
_UPLOAD_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def safe_upload_path(orig_name: str, prefix: str = "") -> Optional[str]:
    """把上传文件名安全化成 UPLOAD_DIR 内的唯一绝对路径。

    - 去目录(防 ../ 与 C:\\ 逃逸：先把反斜杠归一再取 basename)
    - 扩展名白名单(仅图片)，不在白名单 → 返回 None(调用方提示拒绝)
    - 清洗文件名危险字符，空格转下划线
    - 同名自动加序号，绝不覆盖已有文件
    - 兜底校验最终路径仍落在 UPLOAD_DIR 内，越界 → None
    """
    base = os.path.basename((orig_name or "").replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in _UPLOAD_ALLOWED_EXT:
        return None
    stem = re.sub("[^A-Za-z0-9._一-鿿-]", "_", stem.replace(" ", "_")).strip("._") or "img"
    final = os.path.join(UPLOAD_DIR, f"{prefix}{stem}{ext}")
    i = 1
    while os.path.exists(final):
        final = os.path.join(UPLOAD_DIR, f"{prefix}{stem}_{i}{ext}")
        i += 1
    try:
        real_base = os.path.realpath(UPLOAD_DIR)
        if os.path.commonpath([os.path.realpath(final), real_base]) != real_base:
            return None
    except Exception:
        return None
    return final


# ── 出图重试策略（极速 / 韧性）一键切换 ─────────────────────────
# 传输内核(非流式 generateContent)两种 profile 完全一样，网络好时出图一样快；
# profile 只决定【失败时】的反应：
#   fast(极速,默认)   —— 少重试 + 短退避 + 短总时限：要么快出图、要么几十秒内麻利报错，
#                        复刻最早单体版本(app_28)的灵敏体感；坏网络下需手动多重提几次。
#   resilient(韧性)   —— 多重试 + 长退避 + 长总时限：坏节点上自动死磕(最长~10min)、尽量自愈。
# 关键约束：两个 profile 的 gen_idle_deadline 都保持 240s 不缩短——合法 4K 渲染静默期实测
# 最长 ~190s，砍 idle 会误杀"慢但正在正常出图"的请求。fast 的"快"只来自少重试/短退避/短总时限。
DEFAULT_SPEED_PROFILE = "fast"
SPEED_PROFILES = {
    "fast":      {"retry_attempts": 3, "retry_backoffs": [1, 2, 4],
                  "gen_idle_deadline": 240, "gen_total_deadline": 300},
    "resilient": {"retry_attempts": 8, "retry_backoffs": [2, 4, 7, 10, 15],
                  "gen_idle_deadline": 240, "gen_total_deadline": 600},
}


def get_speed_profile() -> str:
    """读取当前重试策略名；非法值回落到默认(fast)。"""
    p = (_load_config().get("speed_profile") or DEFAULT_SPEED_PROFILE).strip().lower()
    return p if p in SPEED_PROFILES else DEFAULT_SPEED_PROFILE


def get_speed_profile_params(cfg: Optional[Dict] = None) -> Dict:
    """返回当前 profile 的一组基础参数(retry_attempts/retry_backoffs/idle/total)。
    传入 cfg 可复用同一次 _load_config，避免重复读盘。"""
    cfg = cfg if cfg is not None else _load_config()
    name = (cfg.get("speed_profile") or DEFAULT_SPEED_PROFILE).strip().lower()
    return dict(SPEED_PROFILES.get(name, SPEED_PROFILES[DEFAULT_SPEED_PROFILE]))


def save_speed_profile(profile_val: str) -> None:
    """保存重试策略(fast / resilient)；非法值回落到默认。"""
    cfg = _load_config()
    p = (profile_val or "").strip().lower()
    cfg["speed_profile"] = p if p in SPEED_PROFILES else DEFAULT_SPEED_PROFILE
    _save_config(cfg)


# ── 直连失败自动转 Fal 备用线路（开关，默认关）─────────────────
# Google 直连(公司 key)被软路由/透明代理重置等【网络类失败】重试耗尽后，
# 若本开关开启且配了 Fal Key，则自动改走 Fal(用户自己的 key)再跑一次。
# 内容/请求级错误(安全拦截、HTTP 400/403)不转线——换线也会失败，白烧 Fal 钱。
def get_auto_failover() -> bool:
    """读取『直连失败自动转 Fal』开关；缺省关闭。"""
    return bool(_load_config().get("auto_failover", False))


def save_auto_failover(enabled) -> None:
    """保存『直连失败自动转 Fal』开关。"""
    cfg = _load_config()
    cfg["auto_failover"] = bool(enabled)
    _save_config(cfg)


# ── 图像生成采样旋钮（opt-in，缺省不传 → 行为与现状逐字节一致）─────────
# engine_config.json 里显式写 "gen_temperature"/"gen_seed" 才生效，注入 Gemini
# generationConfig。用途：A/B 测「降低 temperature / 固定 seed 能否提高单张命中率」。
# 风险：Gemini 图像 endpoint 可能不认这俩字段(静默忽略或 HTTP 400)，故做成 opt-in，
# 先验证不报错再看是否影响输出。Fal 路径不接（保持 Fal 现状）。
def get_gen_sampling() -> dict:
    """返回要并入 generationConfig 的采样字段；键缺省 → 返回 {} (不改任何行为)。"""
    cfg = _load_config()
    out: dict = {}
    t = cfg.get("gen_temperature", None)
    if t is not None:
        try: out["temperature"] = max(0.0, min(2.0, float(t)))
        except (TypeError, ValueError): pass
    s = cfg.get("gen_seed", None)
    if s is not None:
        try: out["seed"] = int(s)
        except (TypeError, ValueError): pass
    return out


# ── 文字/视觉模型列表（参照模式风格分析、连通性自检用）配置化 ─────────
# 硬编码改为可在 engine_config.json 覆盖：换模型不必改源码。
#   text_models : 参照模式风格分析按优先级依次尝试的视觉文字模型列表
#   ping_model  : 连通性自检(test_connection)用的便宜纯文字模型
DEFAULT_TEXT_MODELS = [
    "gemini-3.1-flash-image-preview",   # 该 API Key 确认可用，优先尝试
    "gemini-3-pro-image-preview",        # 该 API Key 确认可用
    "gemini-2.0-flash",                  # 标准 key 可用
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-001",
]


def get_text_models() -> list:
    """风格分析备选模型列表；engine_config.json 的 text_models 可覆盖，非法/缺省回落默认。"""
    v = _load_config().get("text_models")
    if isinstance(v, list) and v:
        return [str(x) for x in v if str(x).strip()]
    return list(DEFAULT_TEXT_MODELS)


def get_ping_model() -> str:
    """连通性自检用的便宜文字模型；engine_config.json 的 ping_model 可覆盖。"""
    return (_load_config().get("ping_model") or "gemini-2.0-flash").strip() or "gemini-2.0-flash"


def extract_clean_prompt(prompt_combined: str) -> str:
    """从组合提示词中提取纯净的英文 prompt（去掉 UI 装饰文本）。"""
    if not prompt_combined:
        return ""
    lines = prompt_combined.split('\n')
    start_idx, end_idx = 0, len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith('Help me make a photo:'):
            start_idx = i
            break
    for i, line in enumerate(lines):
        if '━' in line or '⚠️  重要提示' in line:
            end_idx = i
            break
    return '\n'.join(lines[start_idx:end_idx]).strip()
