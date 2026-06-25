# ==========================================
# 智能配方（预设）— 纯数据 + 纯逻辑，无 UI/IO 依赖
# ==========================================
"""按**地板色调**出"成套方案"的智能配方。

设计原则（用户要求：基于现有地板颜色 + 联网搜索更好的搭配）：
- **不另造一套"色↔风格"知识**：直接复用 prompt_data 里已策展好的
  `FLOOR_TONE_STYLE_RECOMMEND_MAP`（地板色调 → 推荐风格 ✨✨/✨/🟡）。
- 这里只补**每个国际风格的"拍摄/工艺画像"**（光线/镜头/比例/板型/拼缝/光泽），
  取自通用室内设计搭配常识 + 联网搜索佐证（浅暖橡=北欧/海岸/极简漫反射柔光；
  深胡桃=暗奢/静奢/中古暖调侧光；冷灰/水泥=工业/意式极简硬光或均匀光…）。
- `recommend_recipes(tone)` = 取该色调推荐分最高的风格 ∩ 有画像的风格，按分排序取前 6。
  → 配方真正"随地板色调变"，且和 App 自己给的风格推荐一致。
- **故意不给明确"国内/东亚"风格（新中式/港式/老上海/禅意茶室）建画像** →
  它们不会作为自动配方冒出来（仍可在③场景参数手选），让配方默认保持国际中性。

关键词束（而非写死整串选项值）由 UI 用 `pick_option_key(选项真键, 关键词)` 就近匹配，
选项文案小改不失效。约束：所有画像 res 固定 4K（2K 不达标）；`圆弧倒角=Pressed Bevel`。

Sources（联网搜索）：flooringstores.com / parkayfloors.com（wood floor × wall color 2025）、
solidshape.com（walnut flooring best styles）、stylebyemilyhenderson.com / hunker.com（grey floor palettes）、
jamesandjamesfurniture.com（oak floor furniture colors）。
"""

# 每个**国际/中性**风格的拍摄·工艺画像。键 = STYLE_ATMOSPHERE_MAP / 推荐图里的英文风格 key。
# label/sub 仅供配方卡显示；其余字段是匹配下拉选项的关键词束。
STYLE_LOOK_PROFILE = {
    # ── 浅暖 / 通透系 ──
    "Nordic Scandi":       {"label": "❄️ 北欧风",   "sub": "漫反射柔光 · 浅木宽板 · 通透明亮",
                            "light_kw": ["漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["16:9"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Coastal Hamptons":    {"label": "🌊 海岸风",   "sub": "帘光通透 · 浅色海岸 · 开阔明亮",
                            "light_kw": ["帘光", "漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["16:9"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Raw Wood":            {"label": "🪵 原木风",   "sub": "自然漫光 · 原木宽板 · 地板即主角",
                            "light_kw": ["漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["16:9"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Lazy Sunday Morning": {"label": "☕ 慵懒清晨",  "sub": "清晨柔光 · 慵懒通透 · 大面露地",
                            "light_kw": ["帘光", "漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["16:9"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    # ── 极简 / 大板系 ──
    "Modern Minimalist":   {"label": "🌿 现代简约", "sub": "均匀柔光 · 大板无缝 · 极简留白",
                            "light_kw": ["漫反射"], "angle_kw": ["28mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["超宽大板", "宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["超哑光"]},
    "Italian Minimalist":  {"label": "🇮🇹 意式极简", "sub": "建筑光感 · 大板无缝 · 材质至上",
                            "light_kw": ["阴天", "漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["超宽大板", "宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["哑光 (3-5"]},
    "Pinterest Editorial": {"label": "📌 杂志编辑感", "sub": "硬光材质对比 · 大板 · 张力构图",
                            "light_kw": ["局部硬光", "硬光"], "angle_kw": ["50mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["超宽大板", "宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["哑光 (3-5"]},
    "Bauhaus":             {"label": "📐 包豪斯",   "sub": "均匀光 · 几何 · 形随功能",
                            "light_kw": ["漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["正方形拼", "宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["哑光 (3-5"]},
    "Dopamine":            {"label": "🌈 多巴胺",   "sub": "明亮匀光 · 撞色俏皮 · 圆润",
                            "light_kw": ["漫反射"], "angle_kw": ["28mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["正方形拼", "宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    # ── 日式 / 侘寂（全球中性，非国内卖场）──
    "Japandi Minimal":     {"label": "🪵 日式北欧", "sub": "柔和静光 · 原木宽板 · 克制留白",
                            "light_kw": ["帘光", "漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["超哑光"]},
    "Minimal Japanese":    {"label": "🌿 极简日式", "sub": "帘光柔照 · 低饱和 · 留白安静",
                            "light_kw": ["帘光"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["超哑光"]},
    "Wabi-Sabi Raw":       {"label": "🪨 侘寂风",   "sub": "低温侧光 · 原始素材 · 圆弧倒角",
                            "light_kw": ["氛围灯", "黄金时刻"], "angle_kw": ["50mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["圆弧倒角"], "gloss_kw": ["超哑光"]},
    "Cinematic Wabi-Sabi": {"label": "🎬 电影侘寂", "sub": "电影暗调 · 岁月哑光 · 圆弧倒角",
                            "light_kw": ["黄金时刻", "氛围灯"], "angle_kw": ["50mm"], "aspect_kw": ["3:4"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["圆弧倒角"], "gloss_kw": ["超哑光"]},
    # ── 暖木 / 复古系 ──
    "Mid-Century Vintage": {"label": "🎭 中古风",   "sub": "暖调侧光 · 胡桃人字 · 复古",
                            "light_kw": ["黄金时刻", "氛围灯"], "angle_kw": ["50mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["常规人字拼", "宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Modern American":     {"label": "🏡 现代美式", "sub": "自然暖光 · 宽板 · 自在耐住",
                            "light_kw": ["漫反射", "黄金时刻"], "angle_kw": ["35mm"], "aspect_kw": ["16:9"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Maillard":            {"label": "🟤 美拉德",   "sub": "低色温氛围 · 焦糖暖褐 · 包裹感",
                            "light_kw": ["氛围灯"], "angle_kw": ["50mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "IG Influencer Home":  {"label": "📸 居家博主", "sub": "暖调网感 · 宽板 · 精致随意",
                            "light_kw": ["黄金时刻", "漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Biophilic Oasis":     {"label": "🌿 自然融合", "sub": "自然漫光 · 绿植通透 · 室内外融合",
                            "light_kw": ["漫反射"], "angle_kw": ["28mm"], "aspect_kw": ["16:9"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "English Country Estate": {"label": "🏡 英式庄园", "sub": "壁炉暖光 · 人字拼 · 古朴厚重",
                            "light_kw": ["氛围灯", "黄金时刻"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["常规人字拼", "宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Alpine Chalet":       {"label": "🏔️ 山地小屋", "sub": "暖灯木屋 · 厚重原木 · 质朴",
                            "light_kw": ["氛围灯"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Mountain Camp":       {"label": "⛺ 山系风",   "sub": "自然柔光 · 大地色 · 质朴机能",
                            "light_kw": ["漫反射"], "angle_kw": ["28mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["超哑光"]},
    # ── 奶油 / 法式 / 地中海 ──
    "Cream Tone":          {"label": "🤍 奶油风",   "sub": "柔和漫光 · 奶油同调 · 圆润治愈",
                            "light_kw": ["漫反射"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["超哑光"]},
    "French Cream":        {"label": "🌸 法式奶油", "sub": "巴黎柔光 · 人字拼 · 奶油浪漫",
                            "light_kw": ["帘光", "黄金时刻"], "angle_kw": ["50mm"], "aspect_kw": ["3:4"],
                            "size_kw": ["常规人字拼", "宽板直拼"], "seam_kw": ["圆弧倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Mediterranean":       {"label": "🌊 地中海",   "sub": "地中海阳光 · 暖调 · 度假通透",
                            "light_kw": ["黄金时刻", "局部硬光"], "angle_kw": ["35mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["常规人字拼", "宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    # ── 冷调 / 暗调 / 高级感 ──
    "Industrial Loft":     {"label": "🔩 工业风",   "sub": "局部硬光 · 人字/宽板 · 硬朗工业",
                            "light_kw": ["局部硬光", "硬光"], "angle_kw": ["24mm"], "aspect_kw": ["16:9"],
                            "size_kw": ["常规人字拼", "宽板直拼"], "seam_kw": ["常规倒角"], "gloss_kw": ["哑光 (3-5"]},
    "Quiet Luxury":        {"label": "🖤 静奢风",   "sub": "克制柔光 · 宽板 · 安静高级",
                            "light_kw": ["氛围灯", "漫反射"], "angle_kw": ["50mm"], "aspect_kw": ["4:3"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["无缝"], "gloss_kw": ["哑光 (3-5"]},
    "Dark Moody Luxury":   {"label": "🖤 暗奢风",   "sub": "戏剧暖光 · 深木 · 暗调奢华",
                            "light_kw": ["黄金时刻", "氛围灯"], "angle_kw": ["50mm"], "aspect_kw": ["3:4"],
                            "size_kw": ["宽板直拼"], "seam_kw": ["圆弧倒角"], "gloss_kw": ["高光"]},
}

# 仅东亚/国内专属风格不在上表 → 不会作为自动配方出现：
#   Neo-Chinese(新中式) / Hong Kong Retro(港式) / Shanghai Deco(老上海) / Zen Tea Room(禅意茶室) / Light Luxury(国内样板间感)

_SCORE_ORDER = {"✨✨": 0, "✨": 1, "🟡": 2}


def _make_recipe(en_key, prof):
    """把"风格英文 key + 拍摄画像"包装成 UI 兼容的配方 dict（形状与旧版一致，webui 零改动）。"""
    return {
        "key": en_key,                 # 同色调内稳定（用于卡片高亮保持）
        "label": prof["label"],
        "sub": prof["sub"],
        "style_kw": [en_key],          # 英文 key 是 style 选项(整串)的唯一子串 → pick_option_key 命中
        "light_kw": prof["light_kw"],
        "angle_kw": prof["angle_kw"],
        "aspect_kw": prof["aspect_kw"],
        "res_kw": ["4K"],              # 4K 是硬需求
        "size_kw": prof["size_kw"],
        "seam_kw": prof["seam_kw"],
        "gloss_kw": prof["gloss_kw"],
    }


def recommend_recipes(tone: str, limit: int = 6):
    """按地板色调返回"成套配方"列表（最多 limit 条）。

    复用 prompt_data.FLOOR_TONE_STYLE_RECOMMEND_MAP：取该色调推荐分（✨✨>✨>🟡）最高、
    且有拍摄画像的国际风格，按分排序。纯函数。
    """
    # 延迟 import 避免与 prompt_data 潜在循环依赖
    from .prompt_data import FLOOR_TONE_STYLE_RECOMMEND_MAP

    t = tone or ""
    tone_key = next((k for k in FLOOR_TONE_STYLE_RECOMMEND_MAP if k in t), None)
    rec_map = FLOOR_TONE_STYLE_RECOMMEND_MAP.get(tone_key, {}) if tone_key else {}

    scored = []
    for en_key, prof in STYLE_LOOK_PROFILE.items():
        rec = rec_map.get(en_key, "")
        if rec in _SCORE_ORDER:
            scored.append((_SCORE_ORDER[rec], en_key, prof))
    scored.sort(key=lambda x: x[0])

    out = [_make_recipe(en_key, prof) for _, en_key, prof in scored[:limit]]
    if out:
        return out
    # 兜底：色调没命中推荐图（理论上不会）→ 给一组百搭中性风格
    fallback = ["Modern Minimalist", "Nordic Scandi", "Japandi Minimal",
                "Modern American", "Quiet Luxury", "Raw Wood"]
    return [_make_recipe(k, STYLE_LOOK_PROFILE[k]) for k in fallback if k in STYLE_LOOK_PROFILE][:limit]


# 向后兼容：webui 仍 `from .recipes import FLOOR_RECIPES`（导入即可，运行时走 recommend_recipes）。
FLOOR_RECIPES = [_make_recipe(k, p) for k, p in STYLE_LOOK_PROFILE.items()]


def pick_option_key(keys, keywords):
    """在 keys（某下拉的真实选项值列表）里，找第一个**包含**任一 keyword 的项。
    keys 顺序即匹配优先级；keywords 顺序即关键词优先级。找不到返回 None。"""
    if not keys:
        return None
    for kw in (keywords or []):
        for k in keys:
            if kw in str(k):
                return k
    return None
