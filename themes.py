# ==========================================
# 主题系统 — 从 config.py 抽出
# ==========================================
"""UI theme definitions and CSS builder for floor_engine.

Provides THEMES (dict of theme_name → color tokens) and build_theme_css()
which generates a complete <style> block from a theme definition.

当前仅保留单一主题「Anthropic 暖陶米色」（源自 awesome-design-md / Claude 设计语言）。
字体全部使用系统自带的微软雅黑，不加载任何联网字体，完全离线可用。
"""

import html
from typing import Dict

# 全局统一字体栈：微软雅黑优先（系统自带，完全离线，不依赖联网字体）。
_FONT_STACK = ("'Microsoft YaHei', '微软雅黑', -apple-system, 'PingFang SC', "
               "'Segoe UI', sans-serif")

# ════════════════════════════════════════════════════════════════
# 主题定义（单一主题）
# ════════════════════════════════════════════════════════════════
# 温暖人文：暖米白画布 + 白色柔影卡片 + 珊瑚陶土橙稀缺 CTA。详见项目根目录 DESIGN.md。
THEMES: Dict[str, Dict[str, str]] = {
    "Anthropic 暖陶米色": {
        "bg_page": "#f5f1e8", "bg_header": "#faf7f0", "bg_panel_left": "#f5f1e8",
        "bg_panel_right": "#efe9dc", "bg_card": "#ffffff", "bg_card_border": "#e3ddd0",
        "border_panel": "#e3ddd0", "text_primary": "#1a1815", "text_secondary": "#6b6862",
        "text_accent": "#a8472a", "text_label": "#3d3a35",
        "border_success": "#5a8f5a", "border_failed": "#c0392b",
        "border_running": "#cc785c", "border_partial": "#d4915a",
        "dl_btn_bg": "rgba(26,24,21,0.72)", "dl_btn_text": "#ffffff",
        "dl_btn_hover": "rgba(201,100,66,0.92)", "dl_btn_border": "rgba(255,255,255,0.18)",
        "scrollbar_thumb": "#d8cfbd", "is_dark": False,
        # ── 扩展令牌（Anthropic 设计语言）──
        "accent_strong": "#c15f3c", "accent_text": "#ffffff", "bg_input": "#ffffff",
        "shadow_card": "0 1px 2px rgba(26,24,21,0.04), 0 4px 16px rgba(26,24,21,0.06)",
        "shadow_card_hover": "0 2px 4px rgba(26,24,21,0.06), 0 8px 28px rgba(26,24,21,0.10)",
        "radius_card": "14px", "radius_btn": "10px",
        "font_display": _FONT_STACK,
    },
}


def build_theme_css(theme_name: str) -> str:
    """根据主题名生成完整的 <style> 内容块。

    组件规则与令牌驱动；扩展令牌用 .get() 容错。当前只有单一主题，
    未知主题名回退到唯一主题。

    Args:
        theme_name: 主题名称（应为 THEMES 中的键）。

    Returns:
        CSS 字符串，包含 :root 变量和全局样式规则。
    """
    t = THEMES.get(theme_name) or next(iter(THEMES.values()))
    # Escape theme name for safe CSS comment injection
    safe_name = html.escape(str(theme_name), quote=False)
    _accent_strong = t.get('accent_strong', t['text_accent'])
    _accent_text   = t.get('accent_text', '#ffffff')
    _bg_input      = t.get('bg_input', t['bg_card'])
    _shadow_card   = t.get('shadow_card', 'none')
    _shadow_hover  = t.get('shadow_card_hover', _shadow_card)
    _radius_card   = t.get('radius_card', '8px')
    _radius_btn    = t.get('radius_btn', '6px')
    _font_display  = t.get('font_display', _FONT_STACK)
    return f"""/* ── 主题：{safe_name} ── */
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
    /* ── 扩展令牌（Anthropic 设计语言）── */
    --accent-strong: {_accent_strong}; --accent-text: {_accent_text};
    --bg-input: {_bg_input}; --shadow-card: {_shadow_card}; --shadow-card-hover: {_shadow_hover};
    --radius-card: {_radius_card}; --radius-btn: {_radius_btn}; --font-display: {_font_display};
}}
body, .q-page {{
    background: var(--bg-page) !important; color: var(--text-primary);
    font-family: {_FONT_STACK};
}}
/* ── 排版：标题与正文同族（微软雅黑），仅以字重区分层级 ── */
.text-h6, .text-h5, .text-h4 {{
    font-family: var(--font-display) !important;
    letter-spacing: normal; font-weight: 700;
}}
/* ── 分栏布局 ── */
.split-left  {{ flex: 0 0 35%; min-width: 0; height: calc(100vh - 50px); overflow: hidden; }}
.split-right {{ flex: 0 0 65%; min-width: 0; height: calc(100vh - 50px); overflow: hidden; }}
/* ── 顶栏 ── */
.q-header, .q-tab {{ font-family: {_FONT_STACK}; }}
/* 未选中 Tab 在浅米底上必须清晰可读（Quasar 默认半透明会看不清）*/
.q-tab {{ color: var(--text-primary) !important; opacity: 1 !important; font-weight: 600; }}
.q-tab--active {{ color: var(--accent-strong) !important; }}
.q-tabs__content .q-tab__indicator {{ background: var(--accent-strong) !important; height: 2px; }}
/* ── select 宽度约束 ── */
.q-field        {{ min-width: 0 !important; max-width: 100% !important; }}
.q-field__control {{ min-width: 0 !important; }}
.q-field__input,
.q-select__input {{ min-width: 0 !important; width: 0 !important; }}
/* 折叠态取值单行 + 省略号（避免长选项折成 2-3 行；展开浮层仍可看全文）*/
.q-field__native {{
    min-width: 0 !important; white-space: nowrap !important;
    overflow: hidden !important; text-overflow: ellipsis !important;
    word-break: normal !important; padding-left: 6px !important; padding-right: 6px !important;
}}
.q-field__native > span {{
    white-space: nowrap !important; overflow: hidden !important;
    text-overflow: ellipsis !important; word-break: normal !important; max-width: 100%;
}}
.q-menu .q-item__label {{ white-space: normal !important; word-break: break-word !important; }}
/* ── 输入/下拉：干净描边卡片 + 标签外置（标签放到框外上方，不压在边框里）── */
/* 控件本体：白底 + hairline 全边框 + 圆角；抹掉 Material 下划线/灰填充 */
.q-field__control {{
    background: var(--bg-input) !important;
    border: 1px solid var(--border-panel) !important;
    border-radius: var(--radius-btn) !important;
}}
.q-field__control::before, .q-field__control::after {{ display: none !important; }}
.q-field--focused .q-field__control {{ border-color: var(--accent-strong) !important; }}
.q-field__native, .q-field__input {{ outline: none !important; box-shadow: none !important; border: 0 !important; }}
/* 标签移到框外正上方：bottom:100% 贴控件上沿，无论控件多高都精准在框外，绝不压线 */
.q-field {{ margin-top: 1.4rem; }}
.q-field__label {{
    position: absolute !important;
    bottom: 100% !important; top: auto !important; left: 2px !important;
    transform: none !important;          /* 取消 Quasar 浮动缩放/位移 */
    margin-bottom: 4px;
    font-size: 0.78rem !important; line-height: 1.2 !important;
    color: var(--text-label) !important;
    max-width: 100%; overflow: visible;
}}
/* 标签外置后，框内取值垂直居中（单行/多行都居中，不再顶对齐）*/
.q-field__control-container {{
    padding-top: 0 !important; padding-bottom: 0 !important;
    display: flex !important; align-items: center !important;
}}
.q-field__native, .q-field__input {{ padding-top: 0 !important; padding-bottom: 0 !important; }}
/* use-input 搜索型下拉：未聚焦时折叠空搜索框，避免它空出一行把多行取值顶上去 */
.q-select:not(.q-field--focused) .q-field__input {{
    height: 0 !important; min-height: 0 !important; padding: 0 !important; margin: 0 !important;
}}
/* ── 按钮：统一圆角；amber-8 主 CTA 映射为珊瑚强调色（其余语义色保留）── */
.q-btn {{ border-radius: var(--radius-btn) !important; text-transform: none !important; font-weight: 500; }}
.q-btn.bg-amber-8 {{ background: var(--accent-strong) !important; color: var(--accent-text) !important; }}
.q-btn.bg-amber-8 .q-icon, .q-btn.bg-amber-8 .block {{ color: var(--accent-text) !important; }}
.q-btn.text-amber-8 {{ color: var(--text-accent) !important; }}
.q-btn.bg-amber-8:hover {{ filter: brightness(1.05); }}
/* ── 队列卡片：白底柔影、圆角、hover 抬升 ── */
.job-card {{
    background: var(--bg-card); border:1px solid var(--bg-card-border);
    border-radius: var(--radius-card); margin-bottom:12px;
    box-shadow: var(--shadow-card); transition: box-shadow .2s ease, transform .2s ease;
}}
.job-card:hover {{ box-shadow: var(--shadow-card-hover); }}
.job-card-done {{ border-left:4px solid var(--border-success) !important; }}
.job-card-failed {{ border-left:4px solid var(--border-failed) !important; }}
.job-card-running {{ border-left:4px solid var(--border-running) !important; }}
/* ── expansion / separator 细节 ── */
.q-expansion-item {{ border:1px solid var(--border-panel); border-radius: var(--radius-btn); overflow:hidden; }}
.q-separator {{ background: var(--border-panel) !important; }}
/* ── 下载按钮 ── */
.dl-btn {{
    position: absolute; bottom: 6px; right: 6px;
    background: var(--dl-btn-bg); color: var(--dl-btn-text) !important;
    padding: 4px 12px; border-radius: var(--radius-btn);
    font-size: 0.85em; font-weight: bold; text-decoration: none;
    backdrop-filter: blur(4px); transition: all 0.2s;
    border: 1px solid var(--dl-btn-border); z-index: 10;
}}
.dl-btn:hover {{ background: var(--dl-btn-hover); border-color: var(--text-accent); }}
/* ── 磨缝按钮（图片右上角，仿下载按钮）── */
.polish-btn {{
    position: absolute !important; top: 34px; right: 6px;
    background: var(--dl-btn-bg) !important; color: var(--dl-btn-text) !important;
    padding: 4px 12px !important; border-radius: var(--radius-btn) !important;
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
