# ==========================================
# 地板 AI 智能提示词引擎
# 版本: v5.3.6 (精确色码注入 + 地板颜色置信度分析 CIE DE2000)
# ==========================================
import os
import sys
import tempfile
import json
from PIL import Image
import html
import time
import logging
import re
import base64
import io as _io_mod
import threading as _threading
import hashlib
import asyncio
import traceback

# 项目根目录（floor_engine 的上级目录）——保持与单文件版相同的输出/上传/配置位置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ==========================================
# 0. 🛠️ 翻译模块配置
# ==========================================
try:
    from deep_translator import MyMemoryTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    print("⚠️ 未检测到 deep-translator 库！请运行: pip install deep-translator")
    TRANSLATOR_AVAILABLE = False

MAIN_OUTPUT_DIR = os.path.join(BASE_DIR, "output_files")
os.makedirs(MAIN_OUTPUT_DIR, exist_ok=True)
print(f"--- 系统启动 ---")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "app_local_save.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def _short_text(text, limit=500):
    text = "" if text is None else str(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")

# ==========================================
# 配置文件管理 & Gemini API
# ==========================================
CONFIG_FILE = os.path.join(BASE_DIR, "engine_config.json")

GEMINI_MODEL_MAP = {
    "Nano Banana 2":  "gemini-3.1-flash-image-preview",
    "Nano Banana Pro": "gemini-3-pro-image-preview",
}

def is_seamless_herringbone(floor_size, seam_type) -> bool:
    """统一判断：无缝人字拼（散落各处的 '人字拼'+'无缝' 判断集中到这里）。"""
    return ('人字拼' in (floor_size or '')) and ('无缝' in (seam_type or ''))

# ════════════════════════════════════════════════════════════════
# 主题系统
# ════════════════════════════════════════════════════════════════
THEMES = {
    "暗黑工业风": {
        "bg_page": "#0d0d0a", "bg_header": "#030712", "bg_panel_left": "#12120e",
        "bg_panel_right": "#0a0a0a", "bg_card": "#1c1c16", "bg_card_border": "#3a3a22",
        "border_panel": "#27272a", "text_primary": "#fef3c7", "text_secondary": "#6b7280",
        "text_accent": "#fcd34d", "text_label": "#fbbf24",
        "border_success": "#27ae60", "border_failed": "#e74c3c",
        "border_running": "#f39c12", "border_partial": "#e67e22",
        "dl_btn_bg": "rgba(0,0,0,0.65)", "dl_btn_text": "#ffffff",
        "dl_btn_hover": "rgba(232,135,74,0.9)", "dl_btn_border": "rgba(255,255,255,0.2)",
        "scrollbar_thumb": "#3a3a22", "is_dark": True,
    },
    "明亮简约": {
        "bg_page": "#f8f9fa", "bg_header": "#ffffff", "bg_panel_left": "#f0f2f5",
        "bg_panel_right": "#ffffff", "bg_card": "#ffffff", "bg_card_border": "#dee2e6",
        "border_panel": "#dee2e6", "text_primary": "#212529", "text_secondary": "#6c757d",
        "text_accent": "#e8874a", "text_label": "#495057",
        "border_success": "#27ae60", "border_failed": "#e74c3c",
        "border_running": "#f39c12", "border_partial": "#e67e22",
        "dl_btn_bg": "rgba(0,0,0,0.55)", "dl_btn_text": "#ffffff",
        "dl_btn_hover": "rgba(232,135,74,0.9)", "dl_btn_border": "rgba(0,0,0,0.15)",
        "scrollbar_thumb": "#ced4da", "is_dark": False,
    },
    "暖棕木系": {
        "bg_page": "#1a1410", "bg_header": "#0d0a07", "bg_panel_left": "#1e1812",
        "bg_panel_right": "#15110d", "bg_card": "#241e17", "bg_card_border": "#3d3024",
        "border_panel": "#3d3024", "text_primary": "#f5e6d3", "text_secondary": "#8b7355",
        "text_accent": "#d4a853", "text_label": "#c9983e",
        "border_success": "#5a8f4a", "border_failed": "#c0392b",
        "border_running": "#d48b3a", "border_partial": "#c07830",
        "dl_btn_bg": "rgba(0,0,0,0.6)", "dl_btn_text": "#f5e6d3",
        "dl_btn_hover": "rgba(180,130,60,0.85)", "dl_btn_border": "rgba(200,160,100,0.25)",
        "scrollbar_thumb": "#3d3024", "is_dark": True,
    },
    "森林自然": {
        "bg_page": "#0d1410", "bg_header": "#060a07", "bg_panel_left": "#111a14",
        "bg_panel_right": "#0a100c", "bg_card": "#182018", "bg_card_border": "#243828",
        "border_panel": "#243828", "text_primary": "#dce8dc", "text_secondary": "#6b8f6b",
        "text_accent": "#7ec87e", "text_label": "#5aad5a",
        "border_success": "#4a9a4a", "border_failed": "#d44a4a",
        "border_running": "#b0a030", "border_partial": "#a08030",
        "dl_btn_bg": "rgba(0,0,0,0.6)", "dl_btn_text": "#dce8dc",
        "dl_btn_hover": "rgba(80,160,80,0.8)", "dl_btn_border": "rgba(120,180,120,0.25)",
        "scrollbar_thumb": "#243828", "is_dark": True,
    },
    "深海蓝调": {
        "bg_page": "#0a1018", "bg_header": "#050810", "bg_panel_left": "#0e1520",
        "bg_panel_right": "#080d14", "bg_card": "#141c28", "bg_card_border": "#1e3045",
        "border_panel": "#1e3045", "text_primary": "#d0dce8", "text_secondary": "#5a7a9a",
        "text_accent": "#5ca0d8", "text_label": "#4a90c8",
        "border_success": "#3a8a6a", "border_failed": "#c04a4a",
        "border_running": "#c09040", "border_partial": "#b0803a",
        "dl_btn_bg": "rgba(0,0,0,0.6)", "dl_btn_text": "#d0dce8",
        "dl_btn_hover": "rgba(60,130,200,0.8)", "dl_btn_border": "rgba(80,150,210,0.25)",
        "scrollbar_thumb": "#1e3045", "is_dark": True,
    },
}

def _build_theme_css(theme_name: str) -> str:
    """根据主题名生成完整的 <style> 内容"""
    t = THEMES.get(theme_name, THEMES["暗黑工业风"])
    return f"""/* ── 主题：{theme_name} ── */
:root {{
    --bg-page: {t['bg_page']}; --bg-header: {t['bg_header']};
    --bg-panel-left: {t['bg_panel_left']}; --bg-panel-right: {t['bg_panel_right']};
    --bg-card: {t['bg_card']}; --bg-card-border: {t['bg_card_border']};
    --border-panel: {t['border_panel']}; --text-primary: {t['text_primary']};
    --text-secondary: {t['text_secondary']}; --text-accent: {t['text_accent']};
    --text-label: {t['text_label']};
    --border-success: {t['border_success']}; --border-failed: {t['border_failed']};
    --border-running: {t['border_running']}; --border-partial: {t['border_partial']};
    --dl-btn-bg: {t['dl_btn_bg']}; --dl-btn-text: {t['dl_btn_text']};
    --dl-btn-hover: {t['dl_btn_hover']}; --dl-btn-border: {t['dl_btn_border']};
    --scrollbar-thumb: {t['scrollbar_thumb']};
}}
body, .q-page {{ background: var(--bg-page) !important; color: var(--text-primary); }}
/* ── 分栏布局 ── */
.split-left  {{ flex: 0 0 35%; min-width: 0; height: calc(100vh - 50px); overflow: hidden; }}
.split-right {{ flex: 0 0 65%; min-width: 0; height: calc(100vh - 50px); overflow: hidden; }}
/* ── select 宽度约束 ── */
.q-field        {{ min-width: 0 !important; max-width: 100% !important; }}
.q-field__control {{ min-width: 0 !important; }}
.q-field__input,
.q-select__input {{ min-width: 0 !important; width: 0 !important; }}
.q-field__native {{
    min-width: 0 !important; white-space: normal !important;
    word-break: break-word !important; padding-left: 8% !important; padding-right: 8% !important;
}}
.q-field__native > span {{ white-space: normal !important; word-break: break-word !important; }}
.q-menu .q-item__label {{ white-space: normal !important; word-break: break-word !important; }}
/* ── 队列卡片 ── */
.job-card {{ background: var(--bg-card); border:1px solid var(--bg-card-border); border-radius:8px; margin-bottom:12px; }}
.job-card-done {{ border-left:4px solid var(--border-success) !important; }}
.job-card-failed {{ border-left:4px solid var(--border-failed) !important; }}
.job-card-running {{ border-left:4px solid var(--border-running) !important; }}
/* ── 下载按钮 ── */
.dl-btn {{
    position: absolute; bottom: 6px; right: 6px;
    background: var(--dl-btn-bg); color: var(--dl-btn-text) !important;
    padding: 4px 12px; border-radius: 6px;
    font-size: 0.85em; font-weight: bold; text-decoration: none;
    backdrop-filter: blur(4px); transition: all 0.2s;
    border: 1px solid var(--dl-btn-border); z-index: 10;
}}
.dl-btn:hover {{ background: var(--dl-btn-hover); border-color: var(--text-accent); }}
/* ── 磨缝按钮（图片右上角，仿下载按钮）── */
.polish-btn {{
    position: absolute !important; top: 34px; right: 6px;
    background: var(--dl-btn-bg) !important; color: var(--dl-btn-text) !important;
    padding: 4px 12px !important; border-radius: 6px !important;
    font-size: 0.85em; font-weight: bold;
    border: 1px solid var(--dl-btn-border) !important; z-index: 10;
    min-height: 0 !important; text-transform: none !important;
    backdrop-filter: blur(4px);
}}
.polish-btn:hover {{ background: var(--dl-btn-hover) !important; border-color: var(--text-accent) !important; }}
/* ── 滚动条 ── */
::-webkit-scrollbar {{ width: 6px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: var(--scrollbar-thumb); border-radius: 3px; }}
"""

def _load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"gemini_api_key": ""}

def _save_config(config: dict) -> bool:
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"配置保存失败: {e}")
        return False

def save_api_key(api_key_val: str, proxy_val: str = ""):
    key   = (api_key_val or "").strip()
    proxy = (proxy_val   or "").strip()
    cfg   = _load_config()
    cfg["gemini_api_key"] = key
    cfg["proxy"]          = proxy
    _save_config(cfg)

def extract_clean_prompt(prompt_combined: str) -> str:
    if not prompt_combined: return ""
    lines = prompt_combined.split('\n')
    start_idx, end_idx = 0, len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith('Help me make a photo:'):
            start_idx = i; break
    for i, line in enumerate(lines):
        if '━' in line or '⚠️  重要提示' in line:
            end_idx = i; break
    return '\n'.join(lines[start_idx:end_idx]).strip()



__all__ = [n for n in dir() if not n.startswith('__')]
