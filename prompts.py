import os
import re
import time

from PIL import Image

from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR,
    logger, _short_text, is_seamless_herringbone,
)
from .prompt_data import (
    translate_zh_to_en, extract_zh, extract_en,
    TECH_DICT, PROPERTY_TYPE_DICT,
    STYLES, STYLE_ATMOSPHERE_MAP,
    LIGHTINGS, LIGHTING_INSTRUCTION_MAP,
    FLOOR_TONES, FLOOR_TONE_CONTRAST_MAP,
    MARKET_FURNITURE_MAP,
    CN_DEVELOPER_MAP, CN_TIER_MAP, CN_UNIT_TYPE_MAP,
    CN_ROOM_TYPE_MAP, CN_DELIVERY_MAP,
    CN_SPACE_FEATURES, CN_FACILITIES,
    build_overseas_realism_layer, build_cn_layout_guidance,
    _convert_to_srgb, _get_srgb_save_kwargs,
)
from .records import (
    _get_json_path, _load_records, _save_records,
    _img_to_b64, _obfuscate, record_file_lock,
)

# 地板整体颜色锁定指令：强制生成图地板的整体颜色与上传小样完全一致。
# 取代旧的"提取色码注入 + 生成后自动校色"方案，改由提示词直接、人眼可校地约束模型。
FLOOR_COLOR_MATCH_INSTRUCTION = (
    "**[FLOOR COLOR — MANDATORY EXACT MATCH]** "
    "The overall color of the wooden floor in the final image MUST be IDENTICAL to the uploaded floor swatch — "
    "the same hue, the same warm/cool register, the same lightness/darkness, and the same saturation. "
    "The uploaded swatch is the SINGLE source of truth for the floor color: read the floor's base color directly "
    "from it and reproduce that exact color consistently across the entire floor. "
    "Do NOT shift the floor warmer, cooler, lighter, darker, more golden, more orange, more grey, or more/less "
    "saturated to suit the room's style, furniture or ambient lighting. "
    "Only natural light falloff may change brightness (slightly brighter near windows, gradually softer away from "
    "them) — the underlying material color stays exactly the swatch's color everywhere. "
    "The finished floor must be instantly recognizable as the very same product as the uploaded swatch."
)

# 圆弧倒角(Pressed Bevel)性格补丁：人字拼/正方形拼的 en_seam 会被几何描述整段覆盖，
# 倒角性格会丢，故选了圆弧倒角时把"截面性格"追加到几何描述后面。
# 本质=无缝拼接，只把板边直角磨成圆弧：接缝几乎不可见，以圆边高光为主、几乎无暗线，曲线平缓。
# 直拼无需此补丁(直拼直接用 TECH_DICT 里的完整圆弧倒角块)。
PRESSED_BEVEL_CHARACTER = (
    " EDGE PROFILE — SOFT ROUNDED, NEAR-SEAMLESS: each plank edge is softly rounded over into a gentle pressed/eased convex edge, "
    "so the grain and color flow continuously across every joint and the planks meet almost seamlessly. Every joint on the whole "
    "floor is the SAME barely-there soft line — side joints and end joints, near and far, shaded and sunlit all identical — showing "
    "only a faint soft highlight on the rounded lip, matte and flush even under direct sunlight, like a high-end Coretec floor where "
    "the joints almost disappear."
)

# 圆弧倒角·直拼【B2 专属】：B2 用 Pro 那套"near-seamless / joints almost disappear"会抽风——
# 要么把缝打成生硬细黑线、要么强光区吃掉。改成对症的"可见但柔和"目标(对标用户给的浅木参考图)：
# 缝清楚可见但是一条暖调软细缝(深 ~10-15%、错缝端头更淡),不再追求隐形。Pro 仍用 TECH_DICT 原块。
# 注:这是直拼场景 en_seam 的完整替换块(直拼直接拿 en_seam 当整段缝描述)；不含颜色断言,颜色交给小样。
PRESSED_BEVEL_B2 = (
    "Every plank edge is lightly eased / rounded into a soft micro-bevel, so each joint reads as "
    "a CLEARLY VISIBLE but gentle, fine line — present and easy to see, never hidden, never "
    "seamless. A joint is a shallow soft groove: the rounded shoulder on the lit side catches a "
    "faint pale sheen, and the groove holds a thin, warm, soft-edged shadow only about 10-15% "
    "darker than the plank surface beside it — warm and neutral in tone, taken from the wood "
    "itself, never black, never cool grey, never a hard crisp stroke. "
    "Long side joints run as continuous fine soft lines along the planks; short end (butt) joints "
    "are even finer and fainter, staggered in a brick-bond offset so they never line up into one "
    "continuous cross-line. Keep every joint soft-edged and low-contrast, the same warm fine "
    "character from foreground to far background; in the bright sunlit area near a window the "
    "joints stay the same soft warm line — they must NOT darken into black lines and must NOT wash "
    "out and disappear. Matte finish with no glossy sheen on the joints. The floor reads as one "
    "calm continuous wood surface with a quiet, regular rhythm of soft, fine, lightly-rounded "
    "plank joints."
)

# 圆弧倒角【参考图指令】：选圆弧倒角时,生图会自动附带一张压圆弧倒角实拍(config.get_bevel_ref_image)。
# 这段文字告诉模型"那张实拍只用来抄板边的圆弧倒角凹槽形状,颜色/木纹/光线一概不要从它身上取"。
# 放进 extra_cmd_en,B2/Pro 都带;附图与文字由同一个 is_pressed_bevel 条件一起触发。
BEVEL_REF_IMAGE_INSTRUCTION = (
    "\n\n**[ROUNDED PRESSED-BEVEL EDGE — COPY THE EDGE SHAPE FROM THE BEVEL REFERENCE PHOTO]** "
    "One of the provided images is a close-up angled photo of a real wood floor whose planks have rounded "
    "pressed-bevel edges. Use that photo ONLY as the geometric reference for the edge shape: copy how every plank "
    "edge is rounded and chamfered DOWN into a soft, wide, U-shaped micro-bevel — the plank surface curves gently "
    "downward into the joint across a few millimetres of smooth graduated shading, the rounded shoulders on each "
    "side catch a little light, and the bottom of the groove is soft, with NO sharp thin drawn line and NO hard "
    "black hairline. Reproduce that exact rounded, three-dimensional eased-edge profile on EVERY joint of this "
    "floor, so the edges look gently rounded rather than sharply cut. "
    "Take ONLY the edge geometry from that photo — completely ignore its color, wood species, grain pattern, "
    "brightness and lighting, which all come exclusively from the floor material swatch."
)


def _floor_spec_block(core_material, spec_line, visibility, quality, avoid_en, extra_cmd_en,
                      furniture_contrast=None):
    """四套工作流共享的提示词尾巴：地板规格 → 可见性 →(家具对比)→ 质量 → 负向。
    furniture_contrast=None 时不含该槽（直拼/纯效果图）；非 None 时始终保留该槽（宠物/参照，
    即使为空字符串也保留空行，以与原模板逐字节一致）。"""
    mid = f"{furniture_contrast}\n\n" if furniture_contrast is not None else ""
    return (
        f"**[Floor Specification]**\n{core_material}\n{spec_line}\n\n"
        f"{visibility}\n\n{mid}{quality}\n\n"
        f"**[Negative Prompt]** Strictly avoid: {avoid_en}.{extra_cmd_en}"
    )

def save_task_files_html(workflow_mode, model_choice, image_path, continent, country, city, neighborhood, property_type, style_type, room_type, view, lighting, pet_type, pet_action, pet_focus, angle, aspect_ratio, resolution, glossiness, seam_type, avoid_items, floor_size, custom_addition, floor_tone, market_furniture, last_image_path,
                         cn_mode=False, cn_developer="── 不指定 ──", cn_city="上海",
                         cn_tier="── 不指定 ──", cn_unit_type="── 不指定 ──",
                         cn_delivery="🏆 样板间 / 展示单位",
                         cn_room_type="客餐厅一体", cn_view="自然通透景观",
                         cn_space_features=None, cn_facilities=None,
                         style_ref_correction="", style_analysis_text=""):
    if not image_path: return None, "⚠️ 请上传图片", "", "", "", "", ""
    json_path, base_name, target_dir = _get_json_path(image_path)
    png_path = os.path.join(target_dir, f"{base_name}_优化图.png")

    # ── 图片预处理（含 ICC 色彩空间转换）──────────────────────────────
    if image_path != last_image_path or not os.path.exists(png_path):
        try:
            img = Image.open(image_path)
            icc_profile = img.info.get('icc_profile')
            if img.mode in ('RGBA', 'LA'):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else img.split()[1]); img = bg
            elif img.mode == 'P':
                img = img.convert('RGBA')
                bg = Image.new('RGB', img.size, (255, 255, 255)); bg.paste(img, mask=img.split()[3]); img = bg
            elif img.mode not in ('RGB', 'L', 'CMYK'):
                img = img.convert('RGB')
            img = _convert_to_srgb(img, icc_profile)
            # 兜底：PNG 不支持 CMYK 等模式，保存前强制转为 RGB（无 ICC 的 CMYK 图会走到这里）
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            img.save(png_path, **_get_srgb_save_kwargs())
            processed_img = img; msg_prefix = "✅ 新图片已处理并"
        except Exception as e:
            return None, f"❌ 图片处理失败: {e}", "", last_image_path, "", "", ""
    else:
        processed_img = Image.open(png_path); msg_prefix = "⚡ 图片未改变，秒速"

    # ── 动态翻译与映射 ────────────────────────────────────────────────
    # 国内模式：覆盖位置、房间参数；风格始终使用顶部全局 Style
    if cn_mode:
        effective_room_type = cn_room_type
        effective_view      = cn_view
        en_room      = TECH_DICT.get(effective_room_type, translate_zh_to_en(extract_zh(effective_room_type)))
        en_view_trans = translate_zh_to_en(effective_view)
        en_location  = translate_zh_to_en(cn_city) if cn_city else "China"
        en_env       = "Chinese residential community"
        en_property  = CN_TIER_MAP.get(cn_tier, "")
    else:
        effective_room_type = room_type
        effective_view      = view
        en_loc_parts = []
        if city and city != "通用": en_loc_parts.append(translate_zh_to_en(city))
        if country and country not in ["通用", "无特定国家"]: en_loc_parts.append(translate_zh_to_en(country))
        en_location = ", ".join(filter(None, en_loc_parts)) if en_loc_parts else "unspecified location"
        en_env = translate_zh_to_en(neighborhood) if neighborhood else "modern neighborhood"
        en_view_trans = translate_zh_to_en(view)
        en_room = TECH_DICT.get(room_type, translate_zh_to_en(extract_zh(room_type)))
        en_property = PROPERTY_TYPE_DICT.get(property_type, translate_zh_to_en(property_type))

    # 兜底：上游可能传入 None，统一归一为空串，避免 `in`/翻译对 None 报错
    floor_size = floor_size or ""
    seam_type  = seam_type or ""
    glossiness = glossiness or ""
    floor_tone = floor_tone or ""

    en_floor_sz = TECH_DICT.get(floor_size, translate_zh_to_en(floor_size))
    en_gloss = TECH_DICT.get(glossiness, translate_zh_to_en(glossiness))
    en_seam = TECH_DICT.get(seam_type, translate_zh_to_en(seam_type))

    # 人字拼缝隙几何描述（仅在非无缝模式覆盖）
    if "人字拼" in floor_size and "无缝" not in seam_type:
        en_seam = (
            "The floor is laid in a true herringbone pattern: rectangular planks arranged so that each plank meets "
            "its neighbor at a precise 90° internal angle, with the overall direction of the pattern running at 45° to the room walls. "
            "The result is a continuous, interlocking zigzag V-formation that reads clearly from foreground to background. "
            "Joint shadow lines share the same warm mid-tone, fine-line character as any quality wood floor — always subordinate to grain, never dominant. "
            "Direction: two alternating diagonal axes, each at exactly 45° to the room walls — no horizontal lines, no vertical lines. "
            "Perspective: joint lines on both diagonal axes converge correctly toward their respective vanishing points. "
            "JOINT DENSITY COMPENSATION: Herringbone has twice the joint line density of straight plank. "
            "Each individual joint line must be rendered significantly finer and lighter than for a straight plank floor. "
            "The herringbone floor must read first and foremost as a unified woven surface — a diagonal textile pattern in wood."
        )
    elif "正方形拼" in floor_size and "无缝" not in seam_type:
        en_seam = (
            "Square-format planks installed in a clean grid pattern aligned with the room walls. "
            "Joint lines appear as extremely fine, low-contrast tonal separations — consistent on all four sides of each square unit. "
            "Shadow tone is warm and subtle, not black or dark grey. "
            "The grid reads as a quiet, regular background rhythm — never as a dominant geometric overlay."
        )

    # 圆弧倒角(直拼直接用 TECH_DICT 完整块；人字拼/正方形拼的 en_seam 已被几何描述覆盖，故在此补回倒角性格)
    if "圆弧倒角" in seam_type and "无缝" not in seam_type and ("人字拼" in floor_size or "正方形拼" in floor_size):
        en_seam += PRESSED_BEVEL_CHARACTER

    # 圆弧倒角·直拼：B2 走专属"可见但柔和的暖调软细缝"词；Pro 保留 TECH_DICT 原块(靠抽卡命中)。
    # base 提示词(final_prompt_en=B2)用 B2 版；末尾 Pro 派生时把缝换回原块(en_seam_bevel_pro)。
    is_pressed_bevel = ("圆弧倒角" in seam_type and "无缝" not in seam_type)  # 任意拼法的圆弧倒角(触发参考图)
    en_seam_bevel_pro = None
    if (is_pressed_bevel
            and "人字拼" not in floor_size and "正方形拼" not in floor_size):
        en_seam_bevel_pro = en_seam      # 原 TECH_DICT 圆弧倒角块 = Pro 版
        en_seam = PRESSED_BEVEL_B2       # B2 专属软细缝版

    # 地板色调 → 家具对比色指令
    en_furniture_contrast = ""
    for tone_key, contrast_desc in FLOOR_TONE_CONTRAST_MAP.items():
        if tone_key in floor_tone:
            en_furniture_contrast = contrast_desc; break

    if not cn_mode:
        en_property = PROPERTY_TYPE_DICT.get(property_type, translate_zh_to_en(property_type))

    en_style_raw = extract_en(style_type)
    if en_style_raw == style_type:
        en_style_raw = translate_zh_to_en(style_type)
    _style_data = {}
    for style_key, style_val in STYLE_ATMOSPHERE_MAP.items():
        if style_key in style_type:
            _style_data = style_val
            break

    if isinstance(_style_data, dict) and _style_data:
        en_atmosphere = _style_data.get("atm", "Authentic and inviting atmosphere.")
        _style_must = _style_data.get("must", [])
        _style_ban  = _style_data.get("ban", [])
        _style_may  = _style_data.get("may", [])
    else:
        en_atmosphere = _style_data if isinstance(_style_data, str) and _style_data else "Authentic and inviting atmosphere."
        _style_must = []
        _style_ban  = []
        _style_may  = []

    # ── SCHEMA 强化块：MANDATORY & OPTIONAL & PROHIBITIONS ────────────────
    _mandatory_block = ""
    if _style_must:
        _mandatory_block = (
            "**[MANDATORY STYLE ELEMENTS]** Include ALL of the following without exception:\n"
            + "\n".join(f"• {item}" for item in _style_must)
        )
    # 可选点缀：仅在自然契合时出现，不强制、不得喧宾夺主（如绿植不应被当作画面中心）
    _optional_block = ""
    if _style_may:
        _optional_block = (
            "**[OPTIONAL STYLE ACCENTS]** You MAY include one or two of the following ONLY if they "
            "naturally suit the composition. They are NOT required — the scene must look complete and "
            "intentional without them — and none of them may become the visual centre of the image:\n"
            + "\n".join(f"◦ {item}" for item in _style_may)
        )
    _prohibitions_block = ""
    if _style_ban:
        _prohibitions_block = (
            "**[STYLE PROHIBITIONS]** Never include any of the following:\n"
            + "\n".join(f"✗ {item}" for item in _style_ban)
        )

    # 家具地区注入层
    _market_desc = MARKET_FURNITURE_MAP.get(market_furniture, "")
    en_market_furniture = f"\n\n**[Furniture Character]** {_market_desc}" if _market_desc else ""
    en_overseas_realism = ""
    if not cn_mode:
        en_overseas_realism = build_overseas_realism_layer(country, city, property_type, room_type, en_style_raw)

    # 🇨🇳 国内市场专项上下文块
    en_cn_context = ""
    if cn_mode:
        _cn_parts = []
        _dev_desc = CN_DEVELOPER_MAP.get(cn_developer, "")
        if _dev_desc:
            _cn_parts.append(f"**Developer template**: {_dev_desc}")
        _tier_desc = CN_TIER_MAP.get(cn_tier, "")
        if _tier_desc:
            _cn_parts.append(f"**Community tier**: {_tier_desc}")
        if cn_unit_type and "不指定" not in cn_unit_type:
            _unit_desc = CN_UNIT_TYPE_MAP.get(cn_unit_type, cn_unit_type)
            _cn_parts.append(f"**Chinese unit type**: {cn_unit_type}. {_unit_desc}")
        _room_desc = CN_ROOM_TYPE_MAP.get(cn_room_type, "")
        if _room_desc:
            _cn_parts.append(f"**Chinese room layout**: {cn_room_type}. {_room_desc}")
        _layout_guidance = build_cn_layout_guidance(cn_unit_type, cn_room_type)
        if _layout_guidance:
            _cn_parts.append(f"**Unit-room modeling guidance**: {_layout_guidance}")
        _del_desc = CN_DELIVERY_MAP.get(cn_delivery, "")
        if _del_desc:
            _cn_parts.append(_del_desc)
        # 空间特征
        sf_descs = [CN_SPACE_FEATURES[f] for f in (cn_space_features or []) if f in CN_SPACE_FEATURES]
        if sf_descs:
            _cn_parts.append("**Spatial features**: " + "; ".join(sf_descs))
        # 标配设施
        fac_descs = [CN_FACILITIES[f] for f in (cn_facilities or []) if f in CN_FACILITIES]
        if fac_descs:
            _cn_parts.append("\n".join(fac_descs))
        if _cn_parts:
            en_cn_context = "\n\n**[Chinese Domestic Market Context]**\n" + "\n\n".join(_cn_parts)

    # 同色调组合检测 → 切换为 accent-based contrast 策略
    PALE_PALETTE_STYLES = ["IG Influencer Home", "Coastal Hamptons", "Japandi Minimal", "Lazy Sunday Morning"]
    DARK_PALETTE_STYLES = ["Dark Moody Luxury", "English Country Estate", "Cinematic Wabi-Sabi", "Alpine Chalet"]
    PALE_FLOOR_KEYS = ["暖色浅调", "冷色浅调", "中性灰米", "近白", "极浅", "奶油/洞石"]
    DARK_FLOOR_KEYS = ["暖色深调", "冷色深调", "深灰/炭灰"]
    is_tonally_aligned = (
        (any(s in style_type for s in PALE_PALETTE_STYLES) and any(f in floor_tone for f in PALE_FLOOR_KEYS))
        or (any(s in style_type for s in DARK_PALETTE_STYLES) and any(f in floor_tone for f in DARK_FLOOR_KEYS))
    )
    if is_tonally_aligned:
        en_furniture_contrast = (
            "**[Contrast Strategy — Tonally-Aligned Composition]** "
            "Style aesthetic and floor tone share the same tonal register by deliberate design. "
            "Base furniture stays within the chosen style's natural palette — do NOT force opposing furniture colors. "
            "Floor visibility comes from ACCENT contrast: "
            "(1) metallic accents (brass / aged bronze / brushed nickel) on hardware, frames, fixtures, "
            "(2) ONE deliberate accent piece in opposing tonal value, "
            "(3) artwork, sculptural objects, or plant foliage as concentrated color anchors, "
            "(4) strategic lighting reveals 20-30% of the floor surface to showcase grain, texture, and material depth."
        )

    # 光影指令
    en_light_key = ""
    for light_key in LIGHTING_INSTRUCTION_MAP.keys():
        if light_key in lighting: en_light_key = light_key; break
    if not en_light_key:
        extracted = extract_en(lighting.split(' - ')[0] if ' - ' in lighting else lighting)
        for light_key in LIGHTING_INSTRUCTION_MAP.keys():
            if light_key in extracted: en_light_key = light_key; break
    en_light_detail = LIGHTING_INSTRUCTION_MAP.get(en_light_key, "Natural balanced indoor lighting.")
    en_light_name = en_light_key if en_light_key else extract_en(lighting.split(' - ')[0] if ' - ' in lighting else lighting)
    en_light_inst = f"**Lighting — {en_light_name}**: {en_light_detail}"
    if "Curtain-Filtered" in en_light_key and "无缝" in seam_type:
        en_light_inst += (
            " COMBINATION SAFEGUARD: seamless flooring + curtain-filtered light — "
            "the curtain fabric pattern must not cast or suggest any linear shadow pattern on the floor surface."
        )

    # 镜头角度与透视描述
    if '(' in angle:
        raw_angle = angle.split(' (')[0].strip()
        en_angle = raw_angle.replace(' lens', '').strip() + ' lens'
    else:
        en_angle = translate_zh_to_en(angle)
    if any(w in en_angle for w in ['24mm', '28mm', '35mm']):
        en_perspective = "Wide-angle perspective, emphasizing spaciousness and room scale. Strong floor perspective lines converging into the distance."
    elif any(w in en_angle for w in ['85mm', '135mm']):
        en_perspective = "Telephoto compression flattening spatial layers. Intimate, editorial framing. Floor texture fills foreground with shallow depth-of-field falloff."
    else:
        en_perspective = "Natural eye-level perspective with balanced spatial depth. True-to-life proportions, neither distorted nor compressed."

    en_floor_visibility = "**Floor Coverage**: The floor must occupy a minimum of 40-50% of the total image area. Furniture placement should deliberately reveal large unobstructed floor zones. Camera angle must maximize visible floor surface."
    en_composition = f"**Composition**: {en_perspective} Floor texture must be the absolute visual anchor of the image."

    no_sofa_rooms = ["厨房", "餐厨一体", "餐厅", "独立餐厅", "开放式厨房", "封闭式厨房", "衣帽间", "生活阳台", "卫生间", "浴室 (带浴缸)"]
    no_sofa_note = f" (This is a {en_room}: strictly NO sofas, armchairs, or coffee tables in the layout.)" if effective_room_type in no_sofa_rooms else ""
    en_floor_extra = " with staggered/offset bond layout (strictly NO checkerboard/basketweave/grid pattern)" if "609" in en_floor_sz else ""

    # 铺设方向
    if "人字拼" in floor_size:
        en_plank_direction = "classic 45° herringbone installation — planks interlock at right angles, creating a bidirectional zigzag pattern symmetrical about the room's central axis."
    elif any(w in effective_room_type for w in ["客厅", "客餐厅", "横厅", "竖厅", "餐厅", "LDK", "书房"]):
        en_plank_direction = "planks running parallel to the longest wall to maximize perceived room depth"
    elif any(w in effective_room_type for w in ["卧室", "儿童房", "老人房", "主卧", "次卧", "飘窗卧室"]):
        en_plank_direction = "planks running toward the main window or entry point for natural visual flow"
    else:
        en_plank_direction = "planks oriented to maximize visible floor area from camera angle"

    # 避免项翻译。
    #   硬性排除(人物/宠物/地毯/顺色家具)→ 留负向(无好正向写法, Gemini 执行得好)。
    #   地板表面项(过曝/反光/色偏)→ 改正向描述(生图模型对"别出X"易招X, 正向陈述想要的样子更稳)。
    avoid_en_list = []
    surface_pos_list = []
    for item in avoid_items:
        if "地毯" in item: avoid_en_list.append("rugs, carpets of any size or style")
        elif "人物" in item: avoid_en_list.append("any people or human figures")
        elif "宠物" in item: avoid_en_list.append("any animals or pets")
        elif "顺色" in item: avoid_en_list.append("furniture in similar color to floor (must have clear contrast)")
        elif "过曝" in item: surface_pos_list.append("keep the floor evenly and softly lit with its full surface texture and wood grain clearly visible everywhere, including the sunlit areas, which stay gently bright and richly detailed")
        elif "反光" in item: surface_pos_list.append("render the floor finish as matte, scattering light softly and evenly so the wood grain reads clearly across the whole surface")
        elif "粉红" in item: surface_pos_list.append("hold a true, neutral, accurately white-balanced wood color faithful to the uploaded swatch")
        else: avoid_en_list.append(translate_zh_to_en(item))
    avoid_en = ", ".join(filter(None, avoid_en_list)) or "none"
    ar_value = aspect_ratio.split(" ")[0]
    extra_cmd_en = f"\n**Additional Requirements**: {translate_zh_to_en(custom_addition)}" if custom_addition else ""
    if surface_pos_list:
        extra_cmd_en += "\n**[Floor Surface]** " + "; ".join(surface_pos_list) + "."

    en_quality = "**[Quality Requirements]** Ultra-sharp texture details at pixel level. Physically accurate, true-to-swatch colors with clean, faithful white balance and natural, consistent tone across the entire floor."

    # 核心材质指令
    CORE_MATERIAL_INSTRUCTION = (
        "🔥 CRITICAL MATERIAL INSTRUCTION 🔥 "
        "The uploaded reference image IS the floor material sample/swatch. "
        "Your task is to reproduce its EXACT physical characteristics: "
        "(1) Grain direction and flow pattern, "
        "(2) Knot positions, mineral streaks, and surface variation map, "
        "(3) Color tone, warmth/coolness balance, and color variation across planks, "
        "(4) Micro-surface texture (smooth vs. wire-brushed vs. embossed), "
        f"(5) Gloss level: {en_gloss}. "
        "Treat the reference as a material specification — every plank must look like it came from the same production batch. "
        "DIMENSION ISOLATION: The reference image is a material texture swatch only — "
        "its visible plank proportions and physical dimensions must be completely disregarded. "
        "Plank dimensions are defined exclusively by the text specification below."
    )

    # 无缝模式处理
    is_seamless_clean = "无缝" in seam_type
    # Pro 模型专属变量：仅"无缝人字拼"时被赋值，其余情况保持 None（Pro 与 B2 共用同一套词）
    CORE_MATERIAL_INSTRUCTION_PRO = None
    en_floor_sz_seamless_pro = None
    en_surface_direction_pro = None
    SEAMLESS_NEGATIVE_PRO = None
    SEAMLESS_NEGATIVE_PRO_MOTIF = None  # Pro 收尾约束里"表面印花图案"的措辞，按拼法切换
    if is_seamless_clean:
        if "人字拼" in floor_size:
            # ── 无缝人字拼（B2 词·对抗版）：把"整块"当不容置疑的物理事实强灌 ──
            # 思路：温和正向压不住 "herringbone=拼板" 的先验，改为前置强声明 + 末尾覆盖，
            # 用"抛光混凝土/整张大板/印花地胶"这类一体成型物做物理类比，断掉"拼装"语义。
            CORE_MATERIAL_INSTRUCTION = (
                "⛔ NON-NEGOTIABLE PHYSICAL FACTS ABOUT THIS FLOOR — render them as reality, not as a goal: ⛔ "
                "(1) This floor is ONE single solid poured sheet of material spanning the entire room — a single "
                "continuous slab, exactly like a polished concrete floor or one giant printed ceramic slab: it is "
                "physically one piece, so there is simply nothing on it that could be split, divided or assembled. "
                "(2) The herringbone chevron is ONLY a grain-direction print baked onto the TOP of that one solid "
                "sheet — pure surface decoration, exactly like a chevron printed on a single sheet of vinyl or woven "
                "into one bolt of fabric. "
                "(3) The surface is therefore perfectly continuous and uniform everywhere: the grain merely changes "
                "direction to draw the chevrons, and nothing else about the surface changes. "
                "Reproduce the EXACT wood grain, color tone, color variation and matte micro-texture of the uploaded "
                f"swatch (gloss level: {en_gloss}); treat the swatch as a grain-and-color sample only and let the "
                "finished floor flow as one solid piece."
            )
            en_floor_sz_seamless = (
                f"{en_floor_sz}. "
                "The chevron is a printed surface motif running along two diagonal axes at 45° to the walls; the "
                "ONLY thing that changes at each V is the direction the printed grain flows. Everything else — the "
                "surface itself, its color, its finish — stays one uninterrupted solid sheet from wall to wall."
            )
            en_surface_direction = (
                "printed herringbone chevron grain motif, the grain turning 90° back and forth along two diagonal "
                "axes at 45° to the walls, sitting on one solid continuous surface"
            )
            # ── Pro 专属（终极版）：完全不出现 "herringbone / chevron" 等带拼缝先验或会被误判为鱼骨的词，
            #    纯用几何描述 + "印花在整块大板上"。明确"交错错位锯齿(人字)"≠"连续尖角V(鱼骨)"。──
            CORE_MATERIAL_INSTRUCTION_PRO = (
                "⛔ NON-NEGOTIABLE PHYSICAL FACTS ABOUT THIS FLOOR — render them as reality, not as a goal: ⛔ "
                "(1) This floor is ONE single solid poured sheet of material spanning the entire room — a single "
                "continuous slab, exactly like a polished concrete floor or one giant printed ceramic slab: it is "
                "physically one piece, so there is simply nothing on it that could be split, divided or assembled. "
                "(2) On top of that one solid sheet there is ONLY a printed wood-grain motif: short rectangular grain "
                "zones whose grain runs at 90° to one another and are STAGGERED / offset row to row, forming an "
                "interlocking, broken zigzag (the staggered parquet look). Make sure adjacent zigzag rows are OFFSET, "
                "NOT meeting point-to-point in continuous unbroken V's — this is the staggered offset layout, not a "
                "pointed mitred V-pattern. "
                "(3) This zigzag is pure printed surface decoration baked into the slab, like a pattern printed on one "
                "sheet of vinyl or woven into one bolt of fabric. The grain merely changes direction to draw the "
                "zigzag; nothing else about the surface changes. "
                "Reproduce the EXACT wood grain, color tone, color variation and matte micro-texture of the uploaded "
                f"swatch (gloss level: {en_gloss}); treat the swatch as a grain-and-color sample only and let the "
                "finished floor flow as one solid piece."
            )
            en_floor_sz_seamless_pro = (
                f"{en_floor_sz}. "
                "The pattern is a printed staggered-zigzag grain motif running along two diagonal axes at 45° to the "
                "walls, the rows offset from each other (NOT continuous point-to-point V's). The ONLY thing that "
                "changes across the floor is the printed grain direction; the surface, its color and its finish stay "
                "one uninterrupted solid sheet from wall to wall."
            )
            en_surface_direction_pro = (
                "printed staggered-zigzag grain motif, short grain zones at 90° to one another, offset row to row "
                "(interlocking parquet look, not pointed mitred V's), sitting on one solid continuous surface"
            )
            SEAMLESS_NEGATIVE_PRO_MOTIF = (
                "a printed staggered-zigzag grain motif on top (rows offset from each other, not continuous pointed V's)"
            )
        elif "正方形拼" in floor_size:
            # ── 无缝正方形拼：图案由微色调差异定义，而非接缝线 ──
            CORE_MATERIAL_INSTRUCTION = (
                "🔥 CRITICAL MATERIAL INSTRUCTION 🔥 "
                "The uploaded reference image IS the floor material sample/swatch — "
                "it shows the wood grain pattern, color palette, and surface texture you must reproduce. "
                "However, the reference image's individual plank boundaries and joint lines are NOT part of the material spec — "
                "they are photographic artifacts of the sample board and must be completely ignored. "
                "Your task: reproduce the material's EXACT grain pattern, color tone, color variation, and micro-texture "
                f"across the entire floor as ONE CONTINUOUS UNIFIED SURFACE. Gloss level: {en_gloss}. "
                "FOR SQUARE GRID: The square grid pattern is a subtle VISUAL TEXTURE within "
                "the single continuous floor surface — NOT an assembly of separate tiles. "
                "The grid is created purely by subtle alternating grain direction or micro-tonal variation "
                "between adjacent square zones. Where square zones meet, there is ONLY a subtle tonal or "
                "grain-direction shift — ZERO joint lines, ZERO grout, ZERO gaps, ZERO chamfer marks."
            )
            en_floor_sz_seamless = (
                f"{en_floor_sz}. "
                "PATTERN TYPE: Seamless square grid — the check pattern is a surface-level visual rhythm, "
                "NOT a physical assembly of tiles. There are ZERO tile boundaries or grout lines anywhere. "
                "The grid is defined exclusively by subtle alternating grain direction or micro-tonal variation "
                "between adjacent square zones. Every zone transition is a smooth, continuous tonal shift — "
                "no line, no groove, no gap, no grout, no shadow."
            )
            en_surface_direction = (
                "grain alternating subtly between adjacent square zones in a checkerboard rhythm, "
                "with every zone transition being a seamless tonal or grain-direction shift, zero hard lines anywhere"
            )
        else:
            # ── 无缝直拼：app_19 原版 —— 板材存在但边界仅为有机纹理过渡 ──
            CORE_MATERIAL_INSTRUCTION = (
                "🔥 CRITICAL MATERIAL INSTRUCTION 🔥 "
                "The uploaded reference image IS the floor material sample/swatch. "
                "Your task is to reproduce its EXACT physical characteristics: "
                "(1) Grain direction and flow pattern, "
                "(2) Knot positions, mineral streaks, and surface variation map, "
                "(3) Color tone and color variation that naturally varies from plank to plank, "
                "(4) Micro-surface texture, "
                f"(5) Gloss level: {en_gloss}. "
                "Plank boundaries in the final render must be perceptible ONLY as organic material transitions "
                "(grain direction shift, micro-tonal step) — strictly NO joint lines, NO edge shadows, NO grooves, NO gaps, NO chamfer marks."
            )
            en_floor_sz_seamless = (
                f"{en_floor_sz}. "
                "IMPORTANT: planks are laid edge-to-edge in a precision click-lock installation. "
                "Plank boundaries are strictly organic material transitions — grain shift, tonal micro-step — "
                "never drawn lines, never shadow grooves, never filled gaps, never chamfer bevels."
            )
            en_surface_direction = (
                en_plank_direction
                .replace("planks running", "wood grain flowing")
                .replace("planks oriented", "wood grain oriented")
                .replace("planks", "the surface")
            )
            # ── Pro 专属（无缝直拼·对抗版）：与无缝人字拼 Pro 同款思路 ──
            #    把地板当一整块浇筑大板，直纹仅是印在表面上的木纹图案，彻底断掉"拼板=有缝"先验。
            CORE_MATERIAL_INSTRUCTION_PRO = (
                "⛔ NON-NEGOTIABLE PHYSICAL FACTS ABOUT THIS FLOOR — render them as reality, not as a goal: ⛔ "
                "(1) This floor is ONE single solid poured sheet of material spanning the entire room — a single "
                "continuous slab, exactly like a polished concrete floor or one giant printed ceramic slab: it is "
                "physically one piece, so there is simply nothing on it that could be split, divided or assembled. "
                "(2) On top of that one solid sheet there is ONLY a printed wood-grain motif: long, continuous, "
                "parallel grain lines all flowing in the SAME single direction across the entire floor (the straight-"
                "plank look). There are NO plank divisions, NO rows, NO end-joints, NO edges of any kind — just one "
                "uninterrupted field of parallel wood grain. "
                "(3) This straight grain is pure printed surface decoration baked into the slab, exactly like a pattern "
                "printed on one sheet of vinyl or woven into one bolt of fabric. The grain merely gives direction; "
                "nothing else about the surface changes. "
                "Reproduce the EXACT wood grain, color tone, color variation and matte micro-texture of the uploaded "
                f"swatch (gloss level: {en_gloss}); treat the swatch as a grain-and-color sample only and let the "
                "finished floor flow as one solid piece."
            )
            en_floor_sz_seamless_pro = (
                f"{en_floor_sz}. "
                "The pattern is a printed straight wood-grain motif: long parallel grain lines all running in one "
                "single direction across the entire floor, with NO plank boundaries of any kind. The ONLY visual "
                "feature is the printed grain; the surface, its color and its finish stay one uninterrupted solid "
                "sheet from wall to wall."
            )
            en_surface_direction_pro = (
                "printed straight wood-grain motif, long parallel grain lines all flowing in one single direction, "
                "no plank divisions of any kind, sitting on one solid continuous surface"
            )
            SEAMLESS_NEGATIVE_PRO_MOTIF = (
                "a printed straight wood-grain motif on top (long parallel grain all flowing in one single direction, "
                "no plank divisions)"
            )

        en_floor_spec_line = f"Format: {en_floor_sz_seamless}. Grain orientation: {en_surface_direction}. {en_seam}"
        en_floor_spec_line_inpaint = (
            f"Format: {en_floor_sz_seamless}. Grain orientation: {en_surface_direction}. {en_seam} "
            "Recalculate floor perspective to perfectly match the existing room's vanishing points."
        )
        # Pro 专属 spec 行（仅无缝人字拼时与 B2 不同）
        if en_floor_sz_seamless_pro:
            en_floor_spec_line_pro = f"Format: {en_floor_sz_seamless_pro}. Grain orientation: {en_surface_direction_pro}. {en_seam}"
            en_floor_spec_line_inpaint_pro = (
                f"Format: {en_floor_sz_seamless_pro}. Grain orientation: {en_surface_direction_pro}. {en_seam} "
                "Recalculate floor perspective to perfectly match the existing room's vanishing points."
            )
        else:
            en_floor_spec_line_pro = en_floor_spec_line
            en_floor_spec_line_inpaint_pro = en_floor_spec_line_inpaint

        # 无缝模式收尾约束（对抗版）：放在 prompt 最末尾覆盖默认建造逻辑。
        # 直接告诉模型"别按常规木地板那样建"，把它当一整块印花大板渲染。
        SEAMLESS_NEGATIVE = (
            "\n\n**[FLOOR — READ THIS LAST, IT OVERRIDES ANY DEFAULT BEHAVIOUR]** "
            "Do NOT render this floor the way wood floors are normally built. This floor is NOT laid, NOT installed, "
            "NOT assembled from pieces — it is one solid printed sheet, like a single ceramic slab. "
            "Render the entire floor as ONE flawless continuous surface carrying only a printed chevron grain motif on "
            "top; its colour, tone and finish stay perfectly uniform and uninterrupted from wall to wall."
        )
        # Pro 专属收尾（无 "chevron" 字样，措辞随拼法切换：人字拼=交错锯齿印花，直拼=单向平行直纹印花）
        if CORE_MATERIAL_INSTRUCTION_PRO:
            SEAMLESS_NEGATIVE_PRO = (
                "\n\n**[FLOOR — READ THIS LAST, IT OVERRIDES ANY DEFAULT BEHAVIOUR]** "
                "Do NOT render this floor the way wood floors are normally built. This floor is NOT laid, NOT installed, "
                "NOT assembled from pieces — it is one solid printed sheet, like a single ceramic slab. "
                f"Render the entire floor as ONE flawless continuous surface carrying only {SEAMLESS_NEGATIVE_PRO_MOTIF}; "
                "its colour, tone and finish stay perfectly uniform and uninterrupted from wall to wall."
            )
        else:
            SEAMLESS_NEGATIVE_PRO = SEAMLESS_NEGATIVE
        extra_cmd_en += SEAMLESS_NEGATIVE
    else:
        en_floor_spec_line = f"Laid in {en_floor_sz}{en_floor_extra}, {en_plank_direction}. Joints: {en_seam}"
        en_floor_spec_line_inpaint = (
            f"Laid in {en_floor_sz}{en_floor_extra}, {en_plank_direction}. "
            f"Apply {en_seam} between planks. "
            "Recalculate floor perspective to perfectly match the existing room's vanishing points."
        )
        # 圆弧倒角·直拼：Pro 版 spec 行(把 B2 软细缝缝描述换回 TECH_DICT 原块)
        if en_seam_bevel_pro is not None:
            en_floor_spec_line_pro = f"Laid in {en_floor_sz}{en_floor_extra}, {en_plank_direction}. Joints: {en_seam_bevel_pro}"
            en_floor_spec_line_inpaint_pro = (
                f"Laid in {en_floor_sz}{en_floor_extra}, {en_plank_direction}. "
                f"Apply {en_seam_bevel_pro} between planks. "
                "Recalculate floor perspective to perfectly match the existing room's vanishing points."
            )

    # 圆弧倒角(任意拼法)：附倒角参考图 + 文字指令(B2/Pro 同带)。附图由 webui 据同条件挂载。
    if is_pressed_bevel:
        extra_cmd_en += BEVEL_REF_IMAGE_INSTRUCTION

    # ── 地板整体颜色锁定：强制与上传小样一致（无条件追加到 CORE_MATERIAL_INSTRUCTION）──
    CORE_MATERIAL_INSTRUCTION = CORE_MATERIAL_INSTRUCTION + "\n\n" + FLOOR_COLOR_MATCH_INSTRUCTION
    if CORE_MATERIAL_INSTRUCTION_PRO is not None:
        CORE_MATERIAL_INSTRUCTION_PRO = CORE_MATERIAL_INSTRUCTION_PRO + "\n\n" + FLOOR_COLOR_MATCH_INSTRUCTION

    # ── 四模式提示词组装 (SCHEMA 顺序: Style→Composition→Lighting→Floor→Output) ──
    if "地板替换" in workflow_mode:
        final_prompt_en = f"""Help me make a photo:

Task: Inpainting / Floor Material Replacement. Aspect Ratio: {ar_value} | Resolution: {resolution}

**[Scene Preservation]** Keep ALL original elements COMPLETELY UNCHANGED: room structure, walls, windows, ceiling, furniture styles, furniture positions, composition, camera angle, and lighting logic. The ONLY modification is replacing the floor material.

{_floor_spec_block(CORE_MATERIAL_INSTRUCTION, en_floor_spec_line_inpaint, en_floor_visibility, en_quality, avoid_en, extra_cmd_en)}"""

    elif "宠物友好" in workflow_mode:
        en_pet = translate_zh_to_en(extract_zh(pet_type))
        if pet_action == "主宠互动":
            en_subj = f"Scene captures an owner naturally interacting with a cute {en_pet}."
        elif "玩耍" in pet_action:
            en_subj = f"Two or more pets (including a cute {en_pet}) playing together spontaneously."
        else:
            en_subj = f"A cute {en_pet} relaxing naturally and comfortably in the space."
        if "低角度" in pet_focus:
            en_focus = "Low-angle pet-eye-level shot. Subject fills lower 1/3 of frame. Shallow depth-of-field with background furniture softly blurred. Floor texture dramatically revealed in foreground."
        else:
            en_focus = "Standard eye-level interior photography perspective."

        final_prompt_en = f"""Help me make a photo:

Professional photorealistic pet-friendly interior photography. {en_property}, {en_room}. Location: {en_location}. Neighborhood: {en_env}. Aspect ratio {ar_value}, {resolution} resolution.

**[Style & Atmosphere]** {en_style_raw} aesthetic. {en_atmosphere}{no_sofa_note}{en_market_furniture}{en_cn_context}

{en_overseas_realism}

{_mandatory_block}

{_optional_block}

{_prohibitions_block}

**[Camera & Composition]** {en_angle}. {en_focus}
{en_composition}

**[Subject]** 🐾 {en_subj}

{en_light_inst}

{_floor_spec_block(CORE_MATERIAL_INSTRUCTION, en_floor_spec_line + ' Natural cast shadows from pet and furniture create realistic interaction with floor surface.', en_floor_visibility, en_quality, avoid_en, extra_cmd_en, furniture_contrast=en_furniture_contrast)}"""

    elif "参照模式" in workflow_mode:
        _ref_extra = f"\n**Additional direction from user**: {style_ref_correction}" if style_ref_correction else ""
        if style_analysis_text:
            # Robust field parser: handles **FIELD:**, FIELD :, leading whitespace, etc.
            def _pf(field: str) -> str:
                # Regex: optional bold markers, optional spaces around colon, capture until next ALL-CAPS field
                m = re.search(
                    r'\*{0,2}' + re.escape(field) + r'\*{0,2}\s*:\s*(.*?)(?=\n\s*\*{0,2}[A-Z_]{3,}\*{0,2}\s*:|$)',
                    style_analysis_text, re.IGNORECASE | re.DOTALL
                )
                if m:
                    return m.group(1).strip().strip('[]').strip()
                # Fallback: line-by-line
                for ln in style_analysis_text.splitlines():
                    clean = ln.strip().strip('*').strip()
                    if re.match(r'(?i)' + re.escape(field) + r'\s*:', clean):
                        return re.split(r'\s*:\s*', clean, maxsplit=1)[-1].strip().strip('[]')
                return ""

            _sig_line      = _pf("SIGNATURE")
            _prohib_line   = _pf("PROHIBITIONS")
            _materials_line = _pf("MATERIALS")
            _mood_line     = _pf("MOOD")
            _realism_line  = _pf("REALISM_CUES")

            # 图已直接喂模型(sref)，文字降级为「辅助强调」——只补图里不易一眼读出的要点，不再整段转述。
            _realism_ref = (
                f"**[REAL PHOTOGRAPHY CUES from reference]** Preserve this kind of real-room imperfection and photographic believability:\n• {_realism_line}"
                if _realism_line else ""
            )
            _style_block = (
                "**[STYLE SUMMARY — supports the reference image, does not replace it]** "
                "Use the attached reference image as the primary guide for style; the notes below only reinforce it."
            )
            if any([_sig_line, _materials_line, _mood_line, _prohib_line]):
                _style_block += (
                    (f"\nSignature elements to echo: {_sig_line}." if _sig_line else "")
                    + (f"\nMaterial/finish language: {_materials_line}." if _materials_line else "")
                    + (f"\nMood to reproduce: {_mood_line}." if _mood_line else "")
                    + (f"\nAvoid: {_prohib_line}." if _prohib_line else "")
                )
            else:
                # 分析文字没有标准字段(少见)：附上精简原文，避免丢失唯一的风格信息
                _style_block += f"\n{style_analysis_text.strip()}"
            _style_block += _ref_extra
        else:
            _realism_ref = ""
            _style_block = (
                "**[STYLE SUMMARY]** Use the attached reference image as the primary guide for the "
                "overall decoration style, materials, palette, lighting and mood." + _ref_extra
            )

        # 语境锚定：海外用海外实拍层(强抗样板间/CGI)，国内用国内市场语境；
        # 海外层已覆盖写实，参照图的 REALISM_CUES 只在国内语境下补进来避免重复。
        _context_block = en_overseas_realism if not cn_mode else en_cn_context
        _realism_block = "" if not cn_mode else _realism_ref

        final_prompt_en = f"""Help me make a photo:

Professional photorealistic interior architectural photography. {en_room}. Aspect ratio {ar_value}, {resolution} resolution.

**[REFERENCE IMAGE — STYLE / MATERIAL / PALETTE / MOOD]** One of the attached images is a REFERENCE PHOTO of an existing room. Study it and EMULATE its overall decoration style: its material language, color palette, lighting character, furniture taste-level, and emotional mood. This reference is for STYLE ONLY — do NOT copy its geometry, camera angle, wall layout, window positions, or furniture arrangement. INVENT A BRAND NEW room: an original composition and layout that merely FEELS like it belongs to the same home and designer as the reference. Keep believable, natural residential proportions — realistic ceiling height, true-to-life furniture scale, and a coherent single-room floor plan. Do NOT reproduce the reference's exact scene.

{_style_block}

{_context_block}

{_realism_block}

**[Camera & Composition]** {en_angle}.
{en_composition}

{en_light_inst}

{_floor_spec_block(CORE_MATERIAL_INSTRUCTION, en_floor_spec_line, en_floor_visibility, en_quality, avoid_en, extra_cmd_en, furniture_contrast=en_furniture_contrast)}"""

    else:  # 纯效果图 — SCHEMA order: Style → Composition → Lighting → Floor → Output
        final_prompt_en = f"""Help me make a photo:

Professional photorealistic interior architectural photography. {en_property}, {en_room}. Location: {en_location}. Neighborhood: {en_env}. View outside: {en_view_trans}. Aspect ratio {ar_value}, {resolution} resolution.

**[Style & Atmosphere]** {en_style_raw} aesthetic. {en_atmosphere}{no_sofa_note}{en_market_furniture}{en_cn_context}

{_mandatory_block}

{_optional_block}

{_prohibitions_block}

{en_furniture_contrast}

**[Camera & Composition]** {en_angle}.
{en_composition}

{en_light_inst}

{_floor_spec_block(CORE_MATERIAL_INSTRUCTION, en_floor_spec_line, en_floor_visibility, en_quality, avoid_en, extra_cmd_en)}"""

    final_prompt_combined = (
        f"====== 🇬🇧 英文专业版 (发给AI模型的最终提示词) ======\n\n"
        f"{final_prompt_en}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️  重要提示：每次生成请开启全新对话窗口后再粘贴此提示词。\n"
        f"    在同一对话内连续生成会导致模型受上次结果影响，质量下滑。\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # ── Pro 模型专属提示词：仅"无缝人字拼"时与 B2 不同；其余情况完全一致 ──
    # 把 B2 版地板三段替换成 Pro 版（终极版，去 herringbone/chevron 词、纯几何描述）。
    if CORE_MATERIAL_INSTRUCTION_PRO:
        final_prompt_en_pro = (
            final_prompt_en
            .replace(CORE_MATERIAL_INSTRUCTION, CORE_MATERIAL_INSTRUCTION_PRO)
            .replace(en_floor_spec_line_inpaint, en_floor_spec_line_inpaint_pro)
            .replace(en_floor_spec_line, en_floor_spec_line_pro)
            .replace(SEAMLESS_NEGATIVE, SEAMLESS_NEGATIVE_PRO)
        )
    elif en_seam_bevel_pro is not None:
        # 圆弧倒角·直拼：B2 base 用专属软细缝词，Pro 换回 TECH_DICT 原块
        final_prompt_en_pro = (
            final_prompt_en
            .replace(en_floor_spec_line_inpaint, en_floor_spec_line_inpaint_pro)
            .replace(en_floor_spec_line, en_floor_spec_line_pro)
        )
    else:
        final_prompt_en_pro = final_prompt_en

    # ── 写入 JSON 记录 ────────────────────────────────────────────────
    # 附毫秒后缀防撞：批量并发(同款地板的多个场景在同一秒落记录)时，秒级 record_id 会重复，
    # 导致 _api_write_to_record 按 id 匹配到同一条、出图串记录。id 仅做相等匹配+文件名前缀，加后缀向后兼容。
    record_id = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    _loc = (city if city and city not in ("通用", "") else
            country if country and country not in ("通用", "") else continent)
    params_summary = " · ".join(filter(None, [
        workflow_mode.split(" ")[0], effective_room_type,
        (style_type[:14] if style_type else ""),
        floor_tone, floor_size,
        glossiness.split("(")[0].strip(),
        (seam_type.split("(")[0].strip() if seam_type else ""),
        _loc,
        aspect_ratio, resolution,
    ]))
    new_record = {
        "id": record_id,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "workflow_mode": workflow_mode,
        "room_type": effective_room_type,
        "property_type": property_type,
        "style": style_type or "",
        "location": _loc,
        "seam": seam_type or "",
        "params_summary": params_summary,
        "prompt_en": final_prompt_en,
        "prompt_en_pro": final_prompt_en_pro,
        "_pe": _obfuscate(final_prompt_en),
        "sample_image_b64": _img_to_b64(processed_img, max_width=400),
        "results": []
    }
    with record_file_lock(json_path):
        records = _load_records(json_path)
        records.append(new_record)
        _save_records(json_path, records)
    return processed_img, f"{msg_prefix}生成成功！当前模式：{workflow_mode}", final_prompt_combined, image_path, json_path, record_id, png_path, final_prompt_en_pro




__all__ = [n for n in dir() if not n.startswith('__')]
