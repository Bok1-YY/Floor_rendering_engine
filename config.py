# ==========================================
# 地板 AI 智能提示词引擎 — 核心配置
# 版本: v5.3.6
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
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_OUTPUT_DIR = os.path.join(BASE_DIR, "output_files")
os.makedirs(MAIN_OUTPUT_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(BASE_DIR, "engine_config.json")

# ── 日志（委托给 logging_setup，这里保持向后兼容的 logger 导出）──
from .logging_setup import logger  # noqa: E402

# ── 翻译模块 ────────────────────────────────────────────────────
try:
    from deep_translator import MyMemoryTranslator  # noqa: F401
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
