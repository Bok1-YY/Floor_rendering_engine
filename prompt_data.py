import os
import re
import time
import logging
from typing import List, Tuple, Dict, Optional

import numpy as np
from PIL import Image

from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR,
    logger, short_text, TRANSLATOR_AVAILABLE, get_proxy,
    is_seamless_herringbone,
)

try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None

FALLBACK_DICT = {
    "通用现代都市": "modern urban area", "通用自然郊区": "natural suburban area", "通用海滨小镇": "coastal town",
    "现代高尚社区": "modern upscale neighborhood", "山林木屋": "mountain forest cabin", "沙漠绿洲社区": "desert oasis community",
    "沉浸在自然中的独栋别墅": "detached villa surrounded by nature",
    "自然通透景观": "natural open view",
    "带泳池的阳光后院": "sun-drenched private backyard with a swimming pool, blue water shimmering in the light",
    "充满园艺绿植的私家小院": "lush private garden courtyard filled with layered greenery, potted plants, climbing vines, and manicured hedges",
    "带修剪整齐草坪的私家后院": "private backyard with manicured lawn",
    "宁静干净的现代社区街道": "quiet clean modern neighborhood street",
    "自然绿植与树木": "natural greenery and trees", "无明显窗外景观": "no prominent outdoor view",
    "伦敦": "London", "爱丁堡": "Edinburgh", "利物浦": "Liverpool", "曼彻斯特": "Manchester",
    "科茨沃尔德": "Cotswolds", "巴斯": "Bath", "牛津": "Oxford", "剑桥": "Cambridge",
    "格拉斯哥": "Glasgow", "卡迪夫": "Cardiff",
    "巴黎": "Paris", "里昂": "Lyon", "波尔多": "Bordeaux", "普罗旺斯": "Provence",
    "蔚蓝海岸": "French Riviera", "马赛": "Marseille", "斯特拉斯堡": "Strasbourg",
    "图卢兹": "Toulouse", "里尔": "Lille",
    "柏林": "Berlin", "慕尼黑": "Munich", "法兰克福": "Frankfurt", "汉堡": "Hamburg",
    "科隆": "Cologne", "斯图加特": "Stuttgart", "杜塞尔多夫": "Düsseldorf", "德累斯顿": "Dresden",
    "阿姆斯特丹": "Amsterdam", "鹿特丹": "Rotterdam", "海牙": "The Hague",
    "乌得勒支": "Utrecht", "埃因霍温": "Eindhoven", "马斯特里赫特": "Maastricht",
    "布鲁塞尔": "Brussels", "安特卫普": "Antwerp", "布鲁日": "Bruges", "根特": "Ghent",
    "苏黎世": "Zurich", "日内瓦": "Geneva", "卢塞恩": "Lucerne", "伯尔尼": "Bern", "巴塞尔": "Basel",
    "维也纳": "Vienna", "萨尔茨堡": "Salzburg", "因斯布鲁克": "Innsbruck", "格拉茨": "Graz",
    "都柏林": "Dublin", "高威": "Galway", "科克": "Cork",
    "斯德哥尔摩": "Stockholm", "哥德堡": "Gothenburg", "马尔默": "Malmö", "乌普萨拉": "Uppsala",
    "奥斯陆": "Oslo", "卑尔根": "Bergen", "特罗姆瑟": "Tromsø", "斯塔万格": "Stavanger",
    "哥本哈根": "Copenhagen", "奥胡斯": "Aarhus", "欧登塞": "Odense",
    "罗马": "Rome", "米兰": "Milan", "佛罗伦萨": "Florence", "威尼斯": "Venice",
    "那不勒斯": "Naples", "都灵": "Turin", "博洛尼亚": "Bologna",
    "阿马尔菲海岸": "Amalfi Coast", "西西里岛": "Sicily",
    "马德里": "Madrid", "巴塞罗那": "Barcelona", "塞维利亚": "Seville",
    "瓦伦西亚": "Valencia", "毕尔巴鄂": "Bilbao", "格拉纳达": "Granada", "马略卡岛": "Mallorca",
    "里斯本": "Lisbon", "波尔图": "Porto", "法鲁": "Faro", "辛特拉": "Sintra",
    "雅典": "Athens", "圣托里尼": "Santorini", "米克诺斯": "Mykonos", "克里特岛": "Crete", "罗德岛": "Rhodes",
    "布拉格": "Prague", "布尔诺": "Brno", "布达佩斯": "Budapest",
    "华沙": "Warsaw", "克拉科夫": "Krakow", "格但斯克": "Gdańsk", "弗罗茨瓦夫": "Wrocław",
    "杜布罗夫尼克": "Dubrovnik", "斯普利特": "Split", "萨格勒布": "Zagreb",
    "悉尼": "Sydney", "墨尔本": "Melbourne", "布里斯班": "Brisbane",
    "黄金海岸": "Gold Coast", "珀斯": "Perth", "阿德莱德": "Adelaide",
    "奥克兰": "Auckland", "惠灵顿": "Wellington", "皇后镇": "Queenstown", "基督城": "Christchurch",
    "纽约曼哈顿": "Manhattan, New York", "加州洛杉矶": "Los Angeles, California",
    "芝加哥": "Chicago", "德州奥斯汀": "Austin, Texas", "西雅图": "Seattle",
    "迈阿密": "Miami", "旧金山": "San Francisco", "波士顿": "Boston",
    "华盛顿特区": "Washington D.C.", "拉斯维加斯": "Las Vegas", "丹佛": "Denver",
    "亚特兰大": "Atlanta", "达拉斯": "Dallas", "圣地亚哥": "San Diego", "夏威夷檀香山": "Honolulu, Hawaii",
    "温哥华": "Vancouver", "多伦多": "Toronto", "蒙特利尔": "Montreal",
    "卡尔加里": "Calgary", "渥太华": "Ottawa", "魁北克市": "Quebec City", "哈利法克斯": "Halifax",
    "墨西哥城": "Mexico City", "坎昆": "Cancún", "瓜达拉哈拉": "Guadalajara",
    "上海": "Shanghai", "北京": "Beijing", "深圳": "Shenzhen", "广州": "Guangzhou",
    "成都": "Chengdu", "杭州": "Hangzhou", "苏州": "Suzhou", "重庆": "Chongqing",
    "东京": "Tokyo", "京都": "Kyoto", "大阪": "Osaka", "北海道": "Hokkaido", "冲绳": "Okinawa", "福冈": "Fukuoka",
    "首尔": "Seoul", "釜山": "Busan", "济州岛": "Jeju Island",
    "滨海湾核心区": "Marina Bay, Singapore", "圣淘沙高级住宅区": "Sentosa Cove, Singapore", "东海岸": "East Coast, Singapore",
    "曼谷": "Bangkok", "清迈": "Chiang Mai", "普吉岛": "Phuket", "苏梅岛": "Koh Samui",
    "胡志明市": "Ho Chi Minh City", "河内": "Hanoi", "岘港": "Da Nang",
    "孟买": "Mumbai", "新德里": "New Delhi", "班加罗尔": "Bangalore",
    "英国": "United Kingdom", "法国": "France", "德国": "Germany",
    "荷兰": "Netherlands", "比利时": "Belgium", "瑞士": "Switzerland",
    "奥地利": "Austria", "爱尔兰": "Ireland", "瑞典": "Sweden", "挪威": "Norway", "丹麦": "Denmark",
    "意大利": "Italy", "西班牙": "Spain", "葡萄牙": "Portugal", "希腊": "Greece",
    "捷克": "Czech Republic", "匈牙利": "Hungary", "波兰": "Poland", "克罗地亚": "Croatia",
    "美国": "United States", "加拿大": "Canada", "墨西哥": "Mexico",
    "澳大利亚": "Australia", "新西兰": "New Zealand",
    "中国": "China", "日本": "Japan", "韩国": "South Korea",
    "新加坡": "Singapore", "泰国": "Thailand", "越南": "Vietnam", "印度": "India",
    # —— 宠物品种(key 为 extract_zh 去 emoji/英文括号后的形式；猫类带 "cat" 利于生图区分)——
    # 让所有预设品种走查表，在线翻译只兜真正的手动输入(自定义补充等)。
    "金毛犬": "Golden Retriever", "柯基犬": "Welsh Corgi", "柴犬": "Shiba Inu",
    "腊肠犬": "Dachshund", "边境牧羊犬": "Border Collie", "哈士奇": "Siberian Husky",
    "拉布拉多": "Labrador Retriever", "法国斗牛犬": "French Bulldog",
    "贵宾犬/泰迪": "Poodle (Teddy cut)", "萨摩耶": "Samoyed", "比格犬": "Beagle",
    "杜宾犬": "Doberman Pinscher", "马尔济斯犬": "Maltese",
    "布偶猫": "Ragdoll cat", "橘猫": "orange tabby cat", "英国短毛猫": "British Shorthair cat",
    "缅因猫": "Maine Coon cat", "暹罗猫": "Siamese cat", "孟加拉豹猫": "Bengal cat",
    "斯芬克斯无毛猫": "Sphynx hairless cat", "波斯猫": "Persian cat",
    "俄罗斯蓝猫": "Russian Blue cat", "加菲猫": "Exotic Shorthair cat",
    "宠物兔": "pet rabbit", "鹦鹉": "parrot",
    "无特定国家": "", "通用": "",
}

TECH_DICT = {
    "客餐厅一体": "living-dining combo", "客厅": "living room", "餐厅": "dining room",
    "厨房": "kitchen", "餐厨一体": "kitchen-dining combo", "独立书房": "study room",
    "入户区": "entrance foyer", "玄关": "entryway",
    "主卧室": "master bedroom", "儿童房": "kids' room", "健身区": "home gym",
    "衣帽间": "walk-in closet", "浴室 (带浴缸)": "bathroom with bathtub",
    "横厅": "wide horizontal living hall", "竖厅": "depth-oriented vertical living hall",
    "独立客厅": "separate living room", "独立餐厅": "separate dining room",
    "LDK 客餐厨一体": "open-plan LDK living-dining-kitchen",
    "开放式厨房": "open kitchen", "封闭式厨房": "enclosed kitchen",
    "玄关 / 入户区": "entrance foyer", "入户花园": "entry garden",
    "主卧套房": "primary bedroom suite", "次卧": "secondary bedroom",
    "老人房": "elderly parents' bedroom", "书房": "home study",
    "多功能房": "multipurpose room", "榻榻米房": "tatami platform room",
    "生活阳台": "service balcony", "景观阳台": "scenic balcony",
    "飘窗卧室": "bedroom with bay window", "卫生间": "bathroom",
    # —— 直拼 straight plank（按板宽从窄到宽的完整阶梯；接近的尺寸已取中位数合并）——
    "窄板直拼：910 x 122 mm": "narrow strip plank pattern (910 x 122 mm)",
    "短板直拼：808 x 130 mm": "short plank pattern (808 x 130 mm)",
    "标准直拼：1200 x 150 mm": "standard plank pattern (1200 x 150 mm)",
    "常规直拼：1220 x 180 mm": "standard plank pattern (1220 x 180 mm)",
    "经典长板直拼：1524 x 187 mm": "classic long plank pattern (1524 x 187 mm)",
    "宽板直拼：1830 x 230 mm": "wide plank pattern (1830 x 230 mm)",
    "超宽大板直拼：2200 x 260 mm": "extra-wide oversized plank pattern (2200 x 260 mm)",
    "巨幅大板直拼：2400 x 300 mm": "grand-format oversized plank pattern (2400 x 300 mm)",
    # —— 人字拼 herringbone ——
    "小人字拼：90 x 450 mm": "small herringbone pattern (90 x 450 mm)",
    "常规人字拼：125 x 625 mm": "standard herringbone pattern (125 x 625 mm)",
    "大人字拼：155 x 775 mm": "large herringbone pattern (155 x 775 mm)",
    # —— 正方形拼 square ——
    "正方形拼：609 x 609 mm": "square tile staggered layout (609 x 609 mm)",
    "大正方形拼：900 x 900 mm": "large square tile layout (900 x 900 mm)",
    "超哑光 (0-3°)": "ultra-matte", "哑光 (3-5°)": "matte", "高光 (High Gloss)": "high gloss",
    "无缝拼接 (SPC/LVT专用)": (
        "A high-end continuous floor surface engineered with optical continuity. "
        "The organic wood grain pattern spans seamlessly across the entire room with zero structural interruption. "
        "Visual rhythm is achieved purely through natural wood tone variations and grain flow, completely replacing the need for geometric division lines. "
        "Individual planks are visually perceptible, but their boundaries reveal themselves ONLY through the most subtle organic cues: "
        "a slight grain direction shift where one plank meets the next, a micro-tonal variation at the transition, "
        "or the natural material discontinuity of wood grain patterns at the seam. "
        "These boundaries read as material transitions — not as drawn lines, not as shadow grooves, not as filled gaps, not as chamfer bevels. "
        "Absolute requirement: plank boundaries are organic material transitions only — "
        "no joint lines, no edge shadows, no grooves, no hairline gaps, no chamfer marks anywhere on the surface."
    ),
    "常规倒角缝 (如强化/木地板)": (
        "Every plank-to-plank boundary MUST show a visible V-groove shadow line — "
        "this is a mandatory physical feature of this flooring product. "
        "Joint lines are NOT drawn lines or black spaces — they are shallow micro-bevels. "
        "The shadow color of every joint is strictly derived from the floor's own wood tone: only 10% darker than the immediately adjacent plank surface. "
        "No absolute black, no dark grey, no lines darker than the floor's own shadow range anywhere on the floor surface. "
        "In the foreground, joint lines must be unmistakably visible — at least as prominent as a fine pen stroke, warm-toned not dark. "
        "A floor rendered without any visible joint lines is incorrect and commercially unusable."
    ),
    "圆弧倒角 (Pressed Bevel)": (
        # 正向、精简、锚定真实产品；刻意少用"black/dark"等否定词以免模型反被"招黑"。
        "The floor is a wide-plank matte oiled European oak, laid as a near-seamless surface: the wood grain and color flow "
        "continuously from one plank to the next, and each plank edge is softly rounded over (a gentle pressed / eased edge), "
        "so neighbouring planks meet in the faintest possible soft transition — exactly like a high-end Coretec / luxury-vinyl "
        "plank where the joints almost disappear. "
        "Every joint on the whole floor is the SAME barely-there soft line: the long side joints and the short end (butt) joints, "
        "the foreground and the far background, the shaded areas and the brightly sunlit areas all share one identical, gentle, "
        "low-contrast rounded edge. A joint shows only as a soft, very slightly lighter rounded lip catching the light, with at "
        "most a whisper of soft self-shadow; its tone comes from the wood itself and stays within a hair of the surrounding surface. "
        "The finish is matte, so even under direct window sunlight every joint stays soft, flush and continuous — the side joints "
        "and the end joints look the same, and the floor reads as one calm continuous oak surface rather than a grid of grooves. "
        "Keep the plank joints subtle, uniform and seamless-looking across the entire floor."
    ),
}

# 在线翻译结果缓存：同一中文词每个任务都会重查，未命中词典时一次在线请求 1~2 秒，
# 缓存后整个进程生命周期内只查一次。只缓存成功结果——失败兜底不缓存，下次还有机会成功。
# 后端为 GoogleTranslator：构造极廉（仅存参数、无网络），故每次按当前代理新建，
# 让 engine_config.json 改代理后即时生效；真正的省钱在结果缓存上，不缓存翻译器本身。
_translation_cache: dict = {}

def translate_zh_to_en(text):
    if not text or str(text).strip() in ["", "通用", "无", "无特定国家"]: return ""
    text = str(text).strip()
    if all(ord(c) < 128 for c in text): return text
    if text in TECH_DICT: return TECH_DICT[text]
    if text in FALLBACK_DICT: return FALLBACK_DICT[text]
    cached = _translation_cache.get(text)
    if cached is not None: return cached
    if TRANSLATOR_AVAILABLE and GoogleTranslator is not None:
        # 配了本地代理 → 强制翻译走它；没配 → proxies=None，默认走软路由(透明代理)。
        # 与生图侧 api.py 同一套：proxies = {"http": p, "https": p} if p else None。
        proxy = get_proxy()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        for attempt in range(2):
            try:
                translated = GoogleTranslator(
                    source='zh-CN', target='en', proxies=proxies,
                ).translate(text)
                if translated and all(ord(c) < 128 for c in translated):
                    _translation_cache[text] = translated
                    return translated
            except Exception as e:
                logger.warning(f"翻译第{attempt+1}次失败: {e}"); time.sleep(1)
    logger.error(f"翻译彻底失败: '{text}'")
    # 兜底回退裸原文：翻译走软路由/代理，代理重置时会失败。带方括号的 [原文] 会被
    # Gemini 当成格式/指令噪音，比裸 CJK 更糟；裸中文模型尚可识别，至少不污染语义。
    return text

LOCATION_MAP = {
    "欧洲 - 西欧 & 北欧": {
        "英国": ["伦敦", "爱丁堡", "利物浦", "曼彻斯特", "科茨沃尔德", "巴斯", "牛津", "剑桥", "格拉斯哥", "卡迪夫"],
        "法国": ["巴黎", "里昂", "波尔多", "普罗旺斯", "蔚蓝海岸", "马赛", "斯特拉斯堡", "图卢兹", "里尔"],
        "德国": ["柏林", "慕尼黑", "法兰克福", "汉堡", "科隆", "斯图加特", "杜塞尔多夫", "德累斯顿"],
        "荷兰": ["阿姆斯特丹", "鹿特丹", "海牙", "乌得勒支", "埃因霍温", "马斯特里赫特"],
        "比利时": ["布鲁塞尔", "安特卫普", "布鲁日", "根特"],
        "瑞士": ["苏黎世", "日内瓦", "卢塞恩", "伯尔尼", "巴塞尔"],
        "奥地利": ["维也纳", "萨尔茨堡", "因斯布鲁克", "格拉茨"],
        "爱尔兰": ["都柏林", "高威", "科克"],
        "瑞典": ["斯德哥尔摩", "哥德堡", "马尔默", "乌普萨拉"],
        "挪威": ["奥斯陆", "卑尔根", "特罗姆瑟", "斯塔万格"],
        "丹麦": ["哥本哈根", "奥胡斯", "欧登塞"],
    },
    "欧洲 - 南欧 & 东欧": {
        "意大利": ["罗马", "米兰", "佛罗伦萨", "威尼斯", "那不勒斯", "都灵", "博洛尼亚", "阿马尔菲海岸", "西西里岛"],
        "西班牙": ["马德里", "巴塞罗那", "塞维利亚", "瓦伦西亚", "毕尔巴鄂", "格拉纳达", "马略卡岛"],
        "葡萄牙": ["里斯本", "波尔图", "法鲁", "辛特拉"],
        "希腊": ["雅典", "圣托里尼", "米克诺斯", "克里特岛", "罗德岛"],
        "捷克": ["布拉格", "布尔诺"], "匈牙利": ["布达佩斯"],
        "波兰": ["华沙", "克拉科夫", "格但斯克", "弗罗茨瓦夫"],
        "克罗地亚": ["杜布罗夫尼克", "斯普利特", "萨格勒布"],
    },
    "北美洲": {
        "美国": ["纽约曼哈顿", "加州洛杉矶", "芝加哥", "德州奥斯汀", "西雅图", "迈阿密", "旧金山", "波士顿", "华盛顿特区", "拉斯维加斯", "丹佛", "亚特兰大", "达拉斯", "圣地亚哥", "夏威夷檀香山"],
        "加拿大": ["温哥华", "多伦多", "蒙特利尔", "卡尔加里", "渥太华", "魁北克市", "哈利法克斯"],
        "墨西哥": ["墨西哥城", "坎昆", "瓜达拉哈拉"],
    },
    "大洋洲": {
        "澳大利亚": ["悉尼", "墨尔本", "布里斯班", "黄金海岸", "珀斯", "阿德莱德"],
        "新西兰": ["奥克兰", "惠灵顿", "皇后镇", "基督城"],
    },
    "亚洲 - 东亚": {
        "中国": ["上海", "北京", "深圳", "广州", "成都", "杭州", "苏州", "重庆"],
        "日本": ["东京", "京都", "大阪", "北海道", "冲绳", "福冈"],
        "韩国": ["首尔", "釜山", "济州岛"],
    },
    "亚洲 - 东南亚 & 南亚": {
        "新加坡": ["滨海湾核心区", "圣淘沙高级住宅区", "东海岸"],
        "泰国": ["曼谷", "清迈", "普吉岛", "苏梅岛"],
        "越南": ["胡志明市", "河内", "岘港"],
        "印度": ["孟买", "新德里", "班加罗尔"],
    },
    "通用/其他": {
        "无特定国家": ["通用现代都市", "通用自然郊区", "通用海滨小镇", "现代高尚社区", "山林木屋", "沙漠绿洲社区"],
    },
}
CONTINENTS = list(LOCATION_MAP.keys())

PROPERTY_TYPES = ["现代别墅", "普通独立住宅", "联排别墅", "普通公寓", "豪华大平层"]
PROPERTY_TYPE_DICT = {
    "现代别墅": "modern detached villa", "普通独立住宅": "standard detached house",
    "联排别墅": "townhouse", "普通公寓": "standard apartment", "豪华大平层": "luxury open-plan apartment",
}

ROOM_TYPES = ["客餐厅一体", "客厅", "餐厅", "厨房", "餐厨一体", "入户区", "玄关", "独立书房", "主卧室", "儿童房", "健身区", "衣帽间", "浴室 (带浴缸)"]
VIEWS = ["自然通透景观", "带修剪整齐草坪的私家后院", "带泳池的阳光后院", "充满园艺绿植的私家小院", "宁静干净的现代社区街道", "自然绿植与树木", "无明显窗外景观"]

STYLES = [
    "📸 Instagram 居家博主风 (IG Influencer Home) - 暖色调，极具网感。有精心布置的随意感。",
    "📌 Pinterest 高品质灵感风 (Pinterest Editorial) - 高端家居杂志内页感，注重材质微观对比。",
    "🌿 极致自然融合风 (Biophilic Oasis) - 模糊室内外边界，大量引入原生绿植。",
    "🎬 电影感侘寂风 (Cinematic Wabi-Sabi) - 偏暗调，强调岁月痕迹与哑光材质。",
    "☕ 慵懒周末早晨风 (Lazy Sunday Morning) - 充满阳光的清晨氛围，明亮慵懒。",
    "🏡 英式乡村庄园风 (English Country Estate) - 古朴温暖，皮革软装，深色木材，书架壁炉。",
    "🏔️ 阿尔卑斯山地小屋风 (Alpine Chalet) - 厚重原木、羊毛毯、蜡烛灯，山区冬季度假感。",
    "🌊 澳洲 Hamptons 海岸风 (Coastal Hamptons) - 浅色调，白橡木地板，自然麻布，通透明亮。",
    "🖤 暗黑摩登奢华风 (Dark Moody Luxury) - 深色墙面，金属配件，戏剧性灯光，欧洲高端都市感。",
    "🪵 日式北欧极简风 (Japandi Minimal) - 克制留白，原木色，亚麻棉麻，绝对平静。",
    # ── 扩充风格 22 款（国内外市场通用，括号内英文名 = STYLE_ATMOSPHERE_MAP 的 key）──
    "🪵 原木风 (Raw Wood) - 原木暖调，自然留白，地板即主角。",
    "🎭 中古风 (Mid-Century Vintage) - 胡桃木+藤编，复古杏灰，岁月感。",
    "🤍 奶油风 (Cream Tone) - 奶油色一统，圆润治愈，零对比。",
    "🌿 极简日式 (Minimal Japanese) - 克制留白，原木低矮，禅意安静。",
    "🌸 法式奶油 (French Cream) - 巴黎公寓感，轻复古，奶油浪漫。",
    "🏮 新中式 (Neo-Chinese) - 水墨留白，乌木格栅，东方底蕴。",
    "🏡 现代美式 (Modern American) - 温暖耐住，石材原木，自在不做作。",
    "🇮🇹 意式极简 (Italian Minimalist) - 建筑感留白，隐形饰面，材质至上。",
    "🌿 现代简约 (Modern Minimalist) - 干净中性，百搭基本款，大面留白。",
    "💎 轻奢风 (Light Luxury) - 香槟金+大理石，酒店套房精致感。",
    "🎞️ 港式复古 (Hong Kong Retro) - 墨绿藤编，黄铜玻璃，港片故事感。",
    "🪨 侘寂风 (Wabi-Sabi Raw) - 原始素材，岁月痕迹，禅意极致。",
    "🟤 美拉德风 (Maillard) - 焦糖可可暖褐，威士忌般包裹感。",
    "🖤 静奢风 (Quiet Luxury) - 老钱风，材质与比例的安静高级。",
    "🔩 工业风 (Industrial Loft) - 裸砖管线，黑钢做旧，LOFT 硬核。",
    "🌊 地中海风 (Mediterranean) - 灰泥拱门，赤陶海蓝，度假通透。",
    "⛺ 山系风 (Mountain Camp) - 大地色机能，露营基地感，自然质朴。",
    "🌈 多巴胺风 (Dopamine) - 高饱和撞色，圆润俏皮，解压快乐。",
    "❄️ 北欧风 (Nordic Scandi) - 浅木+白，hygge 温暖，明亮百搭。",
    "📐 包豪斯 (Bauhaus) - 三原色块，几何机能，形随功能。",
    "🏰 复古洋房 (Shanghai Deco) - 老上海 Art Deco，东西混搭，摩登复古。",
    "🍵 禅意茶室 (Zen Tea Room) - 东方留白，茶仪式空间，侘寂极静。",
]

STYLE_ATMOSPHERE_MAP = {
    "IG Influencer Home": {
        "atm": "Aspirational domesticity — the home that makes people stop scrolling. Styled but never staged, curated but never cold.",
        "must": [
            "neutral base palette: white, cream, or greige walls",
            "2–3 coffee-table books stacked with spines facing outward",
            "bouclé or waffle-knit throw casually displaced from seating",
            "dried pampas grass or eucalyptus in a tall sculptural vase",
            "one rattan or wicker accent piece",
        ],
        "may": [
            "trailing pothos or fiddle-leaf fig in an oversized terracotta or matte ceramic pot, placed off to one side",
        ],
        "ban": [
            "personal clutter or storage visible anywhere",
            "bold saturated accent colors",
            "dark or black walls",
            "heavy ornate furniture",
        ],
    },
    "Pinterest Editorial": {
        "atm": "Architecturally precise composition with zero visual noise — every object is a considered design decision, maximum material tension.",
        "must": [
            "deliberate material contrast: cool marble or stone surface against warm wood grain",
            "smooth plaster wall against rough-woven linen textile",
            "geometric sculptural objects as sole decorative props",
            "color-blocked arrangements with maximum visual tension between surfaces",
        ],
        "ban": [
            "personal clutter or everyday objects",
            "warm cozy soft-styling: throws, scatter cushions, books",
            "plants or biophilic elements",
            "multiple competing focal points",
        ],
    },
    "Biophilic Oasis": {
        "atm": "The interior merges deliberately with the natural world — green growth at every level, raw natural materials, unfiltered organic life.",
        "must": [
            "low trailing plants at floor level: pothos or string-of-pearls",
            "mid-height leafy specimens on plant stands: monstera or philodendron",
            "one tall statement tree reaching toward ceiling: fiddle-leaf fig or olive",
            "raw travertine or unpolished stone accent surface",
            "linen curtains pooling gently on the floor",
        ],
        "ban": [
            "synthetic materials or plastic-look surfaces",
            "cool grey or cold-toned dominant palette",
            "polished chrome or glossy metal dominating",
            "heavy window treatments blocking natural light",
        ],
    },
    "Cinematic Wabi-Sabi": {
        "atm": "Imperfection as the highest form of beauty — aged, weathered, handmade, asymmetric. Nothing is perfectly aligned or symmetrically placed.",
        "must": [
            "handthrown ceramic vessels with irregular drip glazes and uneven rims",
            "undyed hemp or raw linen textiles in natural unbleached tone",
            "single dried branch or aged stem placed asymmetrically in a simple vessel",
            "weathered wood surfaces where grain, cracks, and history are the decoration",
            "aged patina on all metal objects — zero polish, zero shine",
        ],
        "ban": [
            "anything symmetrically placed or perfectly aligned",
            "polished or glossy metal hardware",
            "fresh flowers or brightly colored botanicals",
            "modern geometric or minimalist-clean aesthetics",
        ],
    },
    "Lazy Sunday Morning": {
        "atm": "Objects exactly where life left them — the floor generously exposed, genuinely inhabited, deeply comfortable and completely unpretentious.",
        "must": [
            "rumpled linen throw casually bunched on sofa, one corner trailing over arm",
            "paperback left open face-down on cushion",
            "ceramic mug on low side table beside sofa",
            "cushions slightly displaced from original positions",
            "large expanse of floor clearly visible and unobstructed",
        ],
        "ban": [
            "deliberate or staged styling of any kind",
            "matched or symmetrical cushion arrangement",
            "architectural drama or statement lighting",
            "any props that look deliberately placed for a photo",
        ],
    },
    "English Country Estate": {
        "atm": "Grand but genuinely lived-in — layers of heritage built from generations of accumulated taste.",
        "must": [
            "pair of aged Chesterfield leather armchairs with visible patina and brass nail-head trim",
            "floor-to-ceiling bookshelves packed with worn mismatched spines",
            "stone fireplace as the room's architectural anchor",
            "tartan or herringbone wool throw draped over one armchair",
            "dark mahogany or walnut furniture with aged brass hardware",
        ],
        "ban": [
            "modern minimalist or Scandinavian furniture",
            "cool or neutral grey dominant palette",
            "track or recessed ceiling lighting",
            "plastic or synthetic materials",
        ],
    },
    "Alpine Chalet": {
        "atm": "Mountain refuge built to outlast winters — heavy, tactile, deeply material, forged from raw natural elements.",
        "must": [
            "massive hand-hewn timber ceiling beams with visible adze marks",
            "chunky cable-knit and felted wool throws in oatmeal, rust, forest green",
            "iron lanterns and pillar candles arranged on surfaces",
            "rough-cut stone accent wall as backdrop",
            "plaid wool cushions and cast-iron decorative detail",
        ],
        "ban": [
            "light or airy minimal aesthetics",
            "tropical or indoor plant elements",
            "chrome or modern metal hardware",
            "contemporary Scandinavian furniture silhouettes",
        ],
    },
    "Coastal Hamptons": {
        "atm": "Effortlessly upscale coastal living — never nautical-kitsch, always open and luminous with generous floor exposure.",
        "must": [
            "white-painted shiplap or board-and-batten wall panelling",
            "linen or cotton slipcover upholstery in white, sand, or pale blue",
            "woven seagrass or rattan accent furniture",
            "driftwood sculpture or large bleached coral piece as decorative anchor",
            "open sightlines with minimal furniture revealing large floor areas",
        ],
        "ban": [
            "anchor, rope, or nautical kitsch props",
            "dark walls or heavy window treatments",
            "warm amber or terracotta dominant tones",
            "ornate or antique furniture",
        ],
    },
    "Dark Moody Luxury": {
        "atm": "Dramatic European urban opulence that commands rather than welcomes — the room is a statement of power and cultivated taste.",
        "must": [
            "deep charcoal, forest green, or midnight navy walls as dominant surface",
            "velvet upholstery in jewel tones: emerald, sapphire, or deep burgundy",
            "brushed-brass or aged-gold hardware and fixtures throughout",
            "sculptural statement pendant as ceiling architectural element",
            "marble surface with strong expressive veining",
        ],
        "ban": [
            "light or airy aesthetics",
            "white or cream walls",
            "casual or informal styling elements",
            "cool industrial grey materials dominating",
        ],
    },
    "Japandi Minimal": {
        "atm": "Absolute visual silence through disciplined subtraction — the floor is the primary design plane, honoured by everything above it.",
        "must": [
            "warm neutral palette: cream, ash, undyed linen, pale oak — no cool greys, no stark whites",
            "one intentional handmade ceramic or turned wooden object per surface, nothing more",
            "low-profile furniture sitting close to floor to honour floor as primary plane",
            "single bonsai or dried stem in handmade vessel as sole organic element",
            "visible wood grain and textile weave as the only decoration",
        ],
        "ban": [
            "saturated colors of any kind",
            "more than one decorative object per surface",
            "ornate or decorative hardware",
            "patterned textiles or wallpapers",
        ],
    },

    # ── 扩充风格 22 款 atm/must/ban（国内外市场通用，key 与 STYLES 括号内英文名一致）──
    "Raw Wood": {
        "atm": "Unforced naturalness, quiet warmth — the floor IS the design statement, all other elements subordinate to it.",
        "must": [
            "pale-to-medium warm-toned wood floor occupying 50–60% of image area, grain clearly visible",
            "low-profile furniture in light natural oak or ash: sofa ≤70cm tall, coffee table near floor level",
            "recessed or minimal ceiling track lighting",
            "clean white or warm beige walls, zero decorative mouldings, TV flush-mounted on plain wall",
            "cotton and linen soft furnishings in undyed cream and off-white, concealed storage throughout",
        ],
        "may": [
            "one indoor plant in a natural clay pot (monstera, fiddle-leaf fig, or olive tree) as a quiet side accent",
        ],
        "ban": [
            "chandelier, decorative ceiling light, or dropped-ceiling cove lighting",
            "marble TV wall or ornate wall panelling",
            "dark walnut or espresso-tone furniture",
            "any visible clutter or open storage",
        ],
    },
    "Mid-Century Vintage": {
        "atm": "Nostalgic warmth, vintage confidence — 'found over time' rather than 'bought all at once'. 1950s–70s mid-century modern fused with warm vintage sensibility.",
        "must": [
            "dark walnut or teak-tone furniture with tapered solid wood legs: sofa, sideboard, coffee table",
            "rattan or cane accent element: woven cabinet door, rattan pendant light, or cane-back chair",
            "vintage color accent: olive green velvet cushion, mustard throw, rust-orange ceramic vase, or aged brass side table",
            "wall color: warm cream, apricot-grey, or oat — NOT bright white",
            "one statement vintage object: geometric brass floor lamp, rounded arch mirror, or abstract ceramic sculpture",
        ],
        "ban": [
            "cool white or grey walls",
            "modern minimalist furniture with no visible wood grain",
            "chrome or brushed silver metal hardware",
            "bright saturated accent colors",
        ],
    },
    "Cream Tone": {
        "atm": "Stepping into a marshmallow — all pressure dissolves immediately. Monochromatic cream-to-warm-beige with zero color contrast anywhere in the architecture.",
        "must": [
            "monochromatic cream-to-warm-beige palette: walls, ceiling, large furniture ALL within the same creamy family",
            "curved rounded furniture everywhere: barrel-round sofa arms, egg-shaped occasional chair, circular pedestal coffee table",
            "bouclé, chunky linen, or waffle-weave fabric on all upholstery",
            "round or globe-form pendant lamp in cream, oat, or matte white",
            "abundant diffused soft lighting — no hard shadows anywhere",
        ],
        "ban": [
            "sharp corners on any furniture piece",
            "bold color contrast anywhere in the room",
            "dark or cool-toned walls",
            "strong directional or dramatic lighting",
        ],
    },
    "Minimal Japanese": {
        "atm": "Profound interior silence — a room that asks nothing of you. Disciplined negative space; floor clearly visible across ≥60% of frame.",
        "must": [
            "maximum 30% of visible wall surface has any object — rest is pure uninterrupted wall plane",
            "low floor-level furniture: platform sofa 30–40cm off ground, floor cushions, low rectangular coffee table",
            "palette strictly: warm white, ash grey, pale birch, undyed linen — zero saturated color",
            "one single intentional object per surface: one ceramic bowl, one dried branch, one folded cloth",
            "completely concealed storage — all doors flush, all handles invisible or recessed",
        ],
        "ban": [
            "any saturated color anywhere in the room",
            "more than one object per surface",
            "chandelier or decorative ceiling light",
            "patterned textiles or wallpapers",
        ],
    },
    "French Cream": {
        "atm": "Sunday morning Paris apartment — effortless, romantic, deeply comfortable. Light Parisian softness, not Baroque grandeur.",
        "must": [
            "warm cream or antique white walls with ONE subtle detail: simple panel moulding or single arched doorway",
            "curved-silhouette sofa in cream linen or ivory textured fabric",
            "delicate aged-brass or antique-gold pendant lamp with fabric shade",
            "dried botanicals in stone or unglazed ceramic vase: lavender, pampas grass, or eucalyptus",
            "one oval or arched mirror with aged gold frame as focal accent",
        ],
        "ban": [
            "elaborate ornate plasterwork or Baroque decorative details",
            "cool grey or blue-toned dominant palette",
            "chrome or silver hardware",
            "modern minimalist or industrial aesthetics",
        ],
    },
    "Neo-Chinese": {
        "atm": "Dignified cultural restraint, quiet depth, contemporary livability — cultural identity expressed through refined material choices.",
        "must": [
            "ink-wash color palette: warm off-white, aged gold, dark ebony-stained timber, muted sage",
            "low-profile platform furniture with clean rectilinear silhouettes referencing Ming-dynasty joinery",
            "lattice-screen dividers or door panels in dark walnut or black steel",
            "one large-format Chinese ink painting or calligraphy scroll as the single focal wall element",
            "ceramic vessels in celadon, blanc de chine, or tea-glazed finishes as the only decorative objects",
        ],
        "ban": [
            "Western ornate or Baroque furniture",
            "bright saturated colors",
            "chandelier or non-Chinese lighting forms",
            "patterned Western textiles or wallpapers",
        ],
    },
    "Modern American": {
        "atm": "American ease without formality — capable, warm, genuinely inhabitable. This style feels lived-in, not showroom-staged.",
        "must": [
            "natural stone surface, warm timber, aged brass or brushed nickel hardware and fixture materials",
            "deep-seated comfortable sofa in warm linen or performance fabric, clean leg profile",
            "board-and-batten or shiplap wall panelling on one accent wall in warm white",
            "statement pendant light: large cage, lantern, or industrial-feel fixture in black or brass",
            "layered casual styling: stacked books, woven basket, ceramic vessel, framed art on the accent wall",
        ],
        "may": [
            "a green plant or fresh botanicals as a secondary accent — never the focal centre of the room",
        ],
        "ban": [
            "French or European ornate styling",
            "cool grey minimalist aesthetics",
            "chrome or polished silver surfaces dominating",
            "sparse or over-minimal layouts",
        ],
    },
    "Italian Minimalist": {
        "atm": "The quiet confidence of good taste that never needs to announce itself — architectural rigour, material quality over quantity.",
        "must": [
            "architectural palette: dove grey, warm stone, raw linen, warm white — zero pattern anywhere",
            "furniture with architectural rigour: clean geometric profiles, visible material quality",
            "one statement material surface: honed travertine or stone slab side table — not marble-look wallpaper",
            "completely flush-face cabinetry with push-to-open or recessed grip, zero visible hardware",
            "maximum two decorative objects in entire room: one sculptural ceramic, one art print",
        ],
        "ban": [
            "any visible pattern or texture on walls",
            "ornate or decorative hardware",
            "plants or biophilic elements",
            "more than two decorative objects total",
        ],
    },
    "Modern Minimalist": {
        "atm": "Calm, ordered, effortlessly livable — the universally reliable base style. Clean surfaces, large unobstructed floor areas.",
        "must": [
            "clean uncluttered surfaces, neutral palette of white, light grey, warm beige",
            "slim-profile furniture with straight clean lines",
            "concealed storage — nothing visible on surfaces",
            "natural materials: light oak, concrete, linen",
            "controlled even lighting with no drama",
        ],
        "ban": [
            "decorative clutter or ornamental objects",
            "bold accent colors",
            "ornate or traditional furniture styles",
            "patterned textiles or wallpapers",
        ],
    },
    "Light Luxury": {
        "atm": "Polished, aspirational — boutique-hotel-suite glamour rather than an everyday home. Use when the client wants an upscale, glossy, magazine-cover look.",
        "must": [
            "champagne gold or brushed brass metal accents on handles and light fixtures",
            "grey, warm white, or greige walls with light fluted or ribbed wall panels",
            "marble or stone-look surfaces with soft veining on coffee tables",
            "velvet or bouclé upholstery in dusty rose, deep teal, or warm grey",
            "geometric pendant lights in metal and glass",
        ],
        "ban": [
            "casual or informal styling elements",
            "raw or unfinished natural materials",
            "visible clutter or personal objects",
            "bold color contrasts",
        ],
    },
    "Hong Kong Retro": {
        "atm": "Nostalgic warmth, cultured eclecticism, urban storytelling — 1970s–1990s Hong Kong domestic nostalgia.",
        "must": [
            "forest green, mustard, or coral accents against warm cream walls",
            "rattan and cane furniture throughout",
            "aged brass hardware on all fixtures",
            "vintage-look ribbed glass panels or doors",
            "warm incandescent-temperature lighting",
        ],
        "ban": [
            "cool Scandinavian or minimalist aesthetics",
            "chrome or silver hardware",
            "white or cool-grey walls",
            "contemporary designer furniture",
        ],
    },
    "Wabi-Sabi Raw": {
        "atm": "Profound stillness, acceptance of transience, material honesty — visible material history as the highest decoration.",
        "must": [
            "raw undyed unfinished natural materials: linen, unpolished stone, hand-thrown ceramics, weathered timber",
            "muted earth palette: ash, raw umber, clay, stone grey, bone white",
            "visible material history: cracks in plaster, patina on metal, grain irregularity in wood",
            "minimal asymmetrically arranged objects",
            "indirect low-temperature warm lighting",
        ],
        "ban": [
            "polished or glossy surfaces of any kind",
            "saturated colors",
            "symmetrical arrangements",
            "modern or industrial clean aesthetics",
        ],
    },
    "Maillard": {
        "atm": "Like sinking into a glass of whisky by firelight — warm, enveloping, quietly indulgent. A tonal study in caramel, cocoa, coffee and toasted brown, named after the Maillard browning reaction. 2025 autumn-winter breakout.",
        "must": [
            "tone-on-tone brown palette: caramel, toffee, espresso, cocoa, toasted almond — every surface a different shade of warm brown",
            "rich tactile textures stacked together: chocolate-brown bouclé sofa, cognac leather armchair, chunky brown wool throw",
            "warm walnut or teak timber furniture with visible grain",
            "earthy ceramic and aged-brass accents in burnt-orange, rust and amber tones",
            "warm low-Kelvin (≈2700K) ambient lighting with soft pools of glow, no cool light anywhere",
        ],
        "ban": [
            "cool white, grey or blue tones anywhere in the room",
            "high-contrast black-and-white pairings",
            "bright saturated non-earth colors",
            "sterile minimalist emptiness with no texture",
        ],
    },
    "Quiet Luxury": {
        "atm": "Old-money confidence that whispers instead of shouts — wealth expressed through impeccable material and proportion, never logos or sparkle. Quiet luxury / stealth wealth.",
        "must": [
            "restrained tonal palette: greige, taupe, oat, charcoal, ivory — depth from texture, not color",
            "top-tier natural materials understated in form: honed marble, full-grain leather, cashmere, brushed solid timber",
            "impeccable proportion and symmetry, generous negative space, nothing crowded",
            "matte metal accents in bronze or blackened steel — never shiny gold or chrome",
            "one or two museum-quality understated objects: a single sculptural lamp, one large abstract canvas",
        ],
        "ban": [
            "visible brand logos or ostentatious decoration",
            "champagne-gold, glossy or glittery 'bling' surfaces",
            "saturated or trend-driven accent colors",
            "cluttered surfaces or excessive ornamentation",
        ],
    },
    "Industrial Loft": {
        "atm": "Raw, honest, unpolished — a converted warehouse where structure IS the decoration. Confident, masculine, urban edge.",
        "must": [
            "exposed structural elements: bare brick or micro-cement wall, visible ceiling ducts/pipes, raw concrete surfaces",
            "black steel framing: metal-framed glass partitions, black iron shelving, factory-style window grids",
            "aged leather and distressed wood: cognac leather sofa, reclaimed-timber table with metal legs",
            "exposed Edison-bulb or cage pendant lighting in black metal",
            "neutral industrial palette: concrete grey, black, rust, weathered brown",
        ],
        "ban": [
            "soft pastel or cream feminine palettes",
            "ornate, classical or French decorative detailing",
            "glossy polished or precious luxury finishes",
            "fully concealed, seamless built-in cabinetry",
        ],
    },
    "Mediterranean": {
        "atm": "A whitewashed seaside villa where sunlight bounces off lime-plaster walls — breezy, relaxed, perpetually on holiday.",
        "must": [
            "hand-troweled lime-plaster walls in warm white or sand, with soft rounded arched doorways or niches",
            "palette of whitewashed white, terracotta, olive green and sea/sky blue accents",
            "natural textures: rattan, jute rug, rough linen, unglazed terracotta pots",
            "rustic warm-toned wood or terracotta-tile flooring and ceiling beams",
            "sun-bleached natural styling: rough linen drapery, woven baskets, handmade ceramic vessels",
        ],
        "may": [
            "Mediterranean greenery (olive tree, potted citrus, or trailing greenery in clay vessels) as a relaxed side accent",
        ],
        "ban": [
            "cool grey minimalist or high-gloss modern surfaces",
            "sharp all-rectilinear forms with zero curves or arches",
            "dark moody or industrial palettes",
            "ornate gilded or formal European styling",
        ],
    },
    "Mountain Camp": {
        "atm": "Bring the trail indoors — a basecamp aesthetic of muted earth tones, functional gear and unfussy natural materials. Calm, grounded, quietly outdoorsy.",
        "must": [
            "muted outdoor palette: olive, khaki, sand, clay, charcoal, faded forest green",
            "functional gear-inspired textures: canvas, raw wood, matte metal, woven jute, technical fabric throws",
            "low solid-wood or log furniture with an unpolished, handcrafted feel",
            "camp-style accents: folding stool, canvas storage, a single framed mountain/landscape print, dried grasses",
            "soft diffuse daylight with warm low-key supplementary lighting",
        ],
        "ban": [
            "glossy, refined or luxury showroom finishes",
            "bright saturated or pastel colors",
            "ornate classical or glam decorative detailing",
            "cool sterile minimalism with no natural texture",
        ],
    },
    "Dopamine": {
        "atm": "An instant mood lift — joyful, playful, unapologetically colorful. Saturated color blocking designed to spark happiness.",
        "must": [
            "bold high-saturation color blocking: 2–3 vivid hues clashing happily (e.g. cobalt + coral + lemon, or grass-green + pink)",
            "a clean light base (white or pale wall) so the saturated furniture and decor pop",
            "playful rounded furniture shapes: curvy sofa, blob/kidney coffee table, wavy mirror",
            "graphic patterns and fun objects: checkerboard rug, abstract art, quirky ceramics",
            "bright, even, cheerful lighting that keeps colors vivid",
        ],
        "ban": [
            "muted, beige, greige or all-neutral palettes",
            "moody dark or somber atmospheres",
            "heavy ornate traditional furniture",
            "minimalist emptiness with no color",
        ],
    },
    "Nordic Scandi": {
        "atm": "Bright, airy, hygge warmth — Scandinavian functionalism that makes long winters feel cozy. Light, clean and genuinely comfortable.",
        "must": [
            "bright base of crisp white or pale-grey walls maximizing the sense of daylight",
            "light wood everywhere: pale ash, birch or white-oak floor and furniture with clean simple lines",
            "cozy hygge layering: chunky knit throw, sheepskin, soft wool cushions in muted tones",
            "one gentle accent color: dusty blue, sage, soft mustard or pale terracotta",
            "simple iconic Scandinavian forms: a sculptural pendant lamp and a clean-lined lounge chair",
        ],
        "may": [
            "a few green plants in simple pots as light, optional greenery",
        ],
        "ban": [
            "dark heavy or ornate traditional furniture",
            "glossy luxury or glam metallic finishes",
            "cluttered surfaces or busy patterns",
            "warm-dim moody low-light atmospheres",
        ],
    },
    "Bauhaus": {
        "atm": "Form follows function — rational, geometric, honest. Early-modernist design where primary colors and pure shapes do all the talking.",
        "must": [
            "primary-color accents (red, yellow, blue) used as bold geometric blocks against a neutral white/grey/black base",
            "pure geometric forms: tubular chrome/steel-framed furniture, cantilever chair, cube and cylinder volumes",
            "honest industrial materials shown plainly: tubular steel, glass, leather, smooth lacquer",
            "clean functional layout with zero superfluous ornament, everything purposeful",
            "one iconic modernist piece: Wassily-style chair, Barcelona-style bench, or a geometric grid artwork",
        ],
        "ban": [
            "decorative ornamentation, mouldings or carved detail",
            "soft pastel, beige or muted earthy palettes",
            "rustic, distressed or handcraft-rough textures",
            "curvy romantic or classical furniture",
        ],
    },
    "Shanghai Deco": {
        "atm": "1930s Shanghai concession-era glamour — Art Deco elegance meets East-West eclecticism. Cultured, nostalgic, quietly opulent.",
        "must": [
            "Art Deco lines: geometric inlay, fluted panels, stepped forms, rounded corners in dark lacquered wood",
            "rich jewel-and-earth palette: emerald green, deep teal, oxblood, mustard against warm cream walls",
            "period materials: terrazzo or mosaic-tile floor, brass and frosted-glass fixtures, velvet upholstery",
            "East-West eclectic mix: a Chinese ceramic or screen alongside a Western leather club chair",
            "vintage details: arched windows, herringbone wood floor, a gramophone-era or rotary-style decorative object",
        ],
        "ban": [
            "cool minimalist or Scandinavian plainness",
            "raw industrial or unfinished surfaces",
            "bright contemporary saturated 'pop' colors",
            "flat-pack modern furniture with no craftsmanship",
        ],
    },
    "Zen Tea Room": {
        "atm": "A breath held in stillness — an Eastern tea space built for slow ritual. Profound calm, emptiness as luxury, nature brought indoors.",
        "must": [
            "disciplined negative space: large empty floor and wall planes, only a few intentional objects",
            "natural raw materials: undyed linen, raw timber, stone, bamboo, hand-thrown clay teaware",
            "muted earthen palette: oatmeal, ash grey, tea-brown, moss green, ink black",
            "a defined tea-ritual zone: low tea table with floor cushions or a raised tatami/platform seating area",
            "a single natural focal element: one ikebana branch, a bonsai, a stone, or a hanging scroll",
        ],
        "ban": [
            "saturated or bright colors",
            "glossy, glam or luxury metallic finishes",
            "crowded surfaces or symmetrical formal arrangements",
            "Western ornate or industrial styling",
        ],
    },
}

PET_TYPES = [
    "🐶 金毛犬", "🐶 柯基犬", "🐶 柴犬", "🐶 腊肠犬", "🐶 边境牧羊犬", "🐶 哈士奇", "🐶 拉布拉多",
    "🐶 法国斗牛犬", "🐶 贵宾犬/泰迪", "🐶 萨摩耶", "🐶 比格犬", "🐶 杜宾犬", "🐶 马尔济斯犬",
    "🐱 布偶猫", "🐱 橘猫", "🐱 英国短毛猫", "🐱 缅因猫", "🐱 暹罗猫", "🐱 孟加拉豹猫",
    "🐱 斯芬克斯无毛猫", "🐱 波斯猫", "🐱 俄罗斯蓝猫", "🐱 加菲猫",
    "🐰 宠物兔", "🐦 鹦鹉",
]
PET_ACTIONS = ["主宠互动", "宠物独处", "宠物独自玩耍", "宠物和宠物玩耍"]
PET_FOCUS_OPTIONS = ["常规场景视角 (人眼高度)", "低角度宠物特写 (背景虚化，极致展示地面)"]

LIGHTINGS = [
    "☀️ 局部硬光 (Hard Light) - 阳光直射，约20%地面被阳光光斑点亮，光影边缘锐利，极致凸显地板凹凸纹理。",
    "🌤️ 漫反射柔光 (Diffused Light) - 光线均匀柔和，无直射高光，完美还原地板色彩。",
    "☁️ 无直射自然光 / 阴天 (Overcast) - 偏冷均匀光线，极度平静。",
    "🌙 纯室内氛围灯 (Ambient Only) - 无自然光，纯靠暖色氛围灯照明。",
    "🌅 黄金时刻逆光 (Golden Hour) - 傍晚低角度暖光，拉长阴影。",
    "🪟 帘光柔照 (Curtain-Filtered) - 薄纱窗帘全拉，室内充满柔和漫射光，整体透亮，地板均匀受光无光带无反光。",
]
LIGHTING_INSTRUCTION_MAP = {
    "Hard Light":         "Strong directional sunlight with crisp, razor-sharp shadow edges cast by furniture across the floor. Approximately 20% of the visible floor area is touched by direct sunlit patches — bright, warm pools of light that dramatically reveal the floor's micro-texture, grain direction, and 3D surface relief. The remaining ~80% of the floor sits in rich, detailed shadow contrast, ensuring overexposure is strictly avoided.",
    "Diffused Light":     "Perfectly even illumination with NO direct sunlight patches on floor. Soft shadows only. Ideal for true-to-life color reproduction of the floor material.",
    "Overcast":           "Cool, flat, shadowless ambient daylight from windows. No highlights, no harsh shadows. Floor color appears most neutral and accurate.",
    "Ambient Only":       "Exclusively warm tungsten/LED ambient light sources (floor lamps, ceiling lights, pendants). No natural light. Floor shows rich warm tones under point-source lighting.",
    "Golden Hour":        "Low-angle late-afternoon sunlight streaming through windows at a raking angle. Long, dramatic furniture shadows sweep across the floor, emphasizing grain and texture depth.",
    "Curtain-Filtered":   "Sheer curtains are fully drawn across all windows. The room is filled with soft, even, directionless ambient light — bright, luminous, and airy. Zero light bands, zero shadow stripes, zero directional washes on the floor at any point. The floor receives perfectly uniform illumination across its entire visible surface: consistent color field, consistent brightness, zero hotspots, zero reflections, zero tonal variation caused by lighting.",
}

ANGLES = ["24mm lens (Wide)", "28mm lens (Wide)", "35mm lens (Standard Wide)", "50mm lens (Standard)", "85mm lens (Telephoto)", "135mm lens (Telephoto)"]

FLOOR_SIZES = [
    # 直拼 straight plank（窄→宽完整阶梯；接近尺寸取中位数合并）
    "常规直拼：1220 x 180 mm",        # 默认：市面最通用住宅板
    "窄板直拼：910 x 122 mm",
    "短板直拼：808 x 130 mm",
    "标准直拼：1200 x 150 mm",
    "经典长板直拼：1524 x 187 mm",
    "宽板直拼：1830 x 230 mm",
    "超宽大板直拼：2200 x 260 mm",
    "巨幅大板直拼：2400 x 300 mm",
    # 人字拼 herringbone
    "小人字拼：90 x 450 mm",
    "常规人字拼：125 x 625 mm",
    "大人字拼：155 x 775 mm",
    # 正方形拼 square
    "正方形拼：609 x 609 mm",
    "大正方形拼：900 x 900 mm",
]

# 墙板尺寸/板型（墙板模式专用；地板的直拼/人字拼不适用墙板，故独立一张表）
PANEL_SIZES = [
    "通高整板（无横向分缝）", "竖向长条密拼（细密竖缝）", "标准竖板 约200mm宽",
    "宽板竖拼 约300–400mm宽", "窄板密拼 约100–150mm宽", "细木格栅/线条板",
]

FLOOR_TONES = [
    "🌿 木纹·暖色浅调 (米白/浅橡木)", "🟤 木纹·暖色中深调 (蜂蜜橡/棕色)",
    "⬛ 木纹·暖色深调 (深胡桃/深棕)", "🪵 木纹·中性灰米 (烟熏橡/雾灰橡)",
    "🩶 木纹·冷色浅调 (浅灰/白橡)", "🖤 木纹·冷色深调 (深灰/炭木)",
    "☁️ 木纹·近白/漂白色",
    "🤍 石纹·奶油/洞石 (米黄暖调)", "🩶 石纹·暖灰/沙灰",
    "☁️ 石纹·冷灰/水泥灰", "⬛ 石纹·深灰/炭灰", "🤍 石纹·近白/极浅",
]

FLOOR_TONE_CONTRAST_MAP = {
    "木纹·暖色浅调": ("**[Furniture Contrast Requirement]** The floor is a warm, light-toned wood (beige/sandy/light oak family). All furniture upholstery and dominant surface materials must be in clear visual contrast: select from deep charcoal, slate grey, forest green, navy blue, or dark espresso tones. Warm blonde, natural oak, or honey-toned furniture is strictly forbidden — it dissolves into the floor."),
    "木纹·暖色中深调": ("**[Furniture Contrast Requirement]** The floor is a warm, medium-to-deep wood tone (honey oak/caramel/tan family). Furniture must contrast clearly: select off-white, cream, pale grey, or matte white upholstery and surfaces. Avoid any mid-brown, golden, or amber-toned furniture — it will merge with the floor."),
    "木纹·暖色深调": ("**[Furniture Contrast Requirement]** The floor is a deep, dark warm wood (walnut/espresso/dark brown family). Furniture must be clearly lighter: cream, ivory, warm white, pale sand, or light greige. No dark or mid-brown furniture of any kind — it disappears against the dark floor."),
    "木纹·中性灰米": ("**[Furniture Contrast Requirement]** The floor is a neutral, desaturated greige wood (smoked oak / fumed oak / mushroom oak family). Because the floor is the universal neutral, contrast can travel in either direction — pick ONE consistent path: (A) Deep saturated warm: cognac leather, terracotta, cinnamon, rust, oxblood; (B) Deep saturated cool: charcoal, deep slate, navy, forest green; (C) High-value contrast: crisp ivory, chalk white, or pure cream. STRICTLY AVOID any mid-tone greige, taupe, mushroom, driftwood, oatmeal, or camel furniture."),
    "木纹·冷色浅调": ("**[Furniture Contrast Requirement]** The floor is a cool, light-toned wood (pale grey/white oak/ash family). Furniture must use warm contrast tones: terracotta, warm cream, sandy beige, cognac leather, mustard, or warm sage. Avoid cool grey or white furniture — insufficient contrast against the cool light floor."),
    "木纹·冷色深调": ("**[Furniture Contrast Requirement]** The floor is a cool, dark-toned wood (dark grey/charcoal wood family). Furniture must be clearly lighter and warmer: cream, ivory, warm white, pale sand, or warm linen. No dark grey or cool-toned furniture — it vanishes against the dark floor."),
    "木纹·近白": ("**[Furniture Contrast Requirement]** The floor is near-white or heavily bleached wood. Furniture must provide strong value contrast: deep olive green, charcoal, dark navy, cognac, or rich mid-tones. Strictly avoid white, cream, or any very pale furniture — zero contrast against a near-white floor."),
    "石纹·奶油": ("**[Furniture Contrast Requirement]** The floor is a warm cream/travertine/ivory stone tone. Furniture must contrast clearly: deep forest green, charcoal grey, dark leather brown, or slate blue. Avoid warm beige, ivory, or cream furniture — it blends into the travertine floor."),
    "石纹·暖灰": ("**[Furniture Contrast Requirement]** The floor is a warm grey/sand grey stone tone. Furniture must use contrasting tones: deep navy, forest green, charcoal, or rich warm-toned dark wood. Avoid mid-grey or sand-toned furniture — insufficient contrast against the warm grey stone."),
    "石纹·冷灰": ("**[Furniture Contrast Requirement]** The floor is a cool grey/concrete stone tone. Furniture must introduce warm contrast: terracotta, warm cream, cognac leather, mustard, or warm walnut. Avoid cool grey or white furniture — it blends with the concrete-grey floor."),
    "石纹·深灰": ("**[Furniture Contrast Requirement]** The floor is dark grey/charcoal stone. Furniture must be clearly lighter: cream, ivory, warm white, pale sand, or light linen. No dark furniture of any tone — it disappears against the dark stone floor."),
    "石纹·近白": ("**[Furniture Contrast Requirement]** The floor is near-white or very pale polished stone. Furniture must provide strong contrast: deep charcoal, olive green, dark navy, rich burgundy, or strong mid-tones. Strictly avoid white, cream, or any pale furniture — zero contrast against the near-white stone floor."),
}

FLOOR_TONE_STYLE_RECOMMEND_MAP = {
    "木纹·暖色浅调": {"IG Influencer Home": "✨✨", "Coastal Hamptons": "✨✨", "Biophilic Oasis": "✨✨", "Japandi Minimal": "✨", "Lazy Sunday Morning": "✨", "Pinterest Editorial": "✨", "Cinematic Wabi-Sabi": "🟡", "English Country Estate": "🟡", "Alpine Chalet": "", "Dark Moody Luxury": ""},
    "木纹·暖色中深调": {"English Country Estate": "✨✨", "Alpine Chalet": "✨✨", "Biophilic Oasis": "✨✨", "Cinematic Wabi-Sabi": "✨", "Lazy Sunday Morning": "✨", "IG Influencer Home": "✨", "Pinterest Editorial": "🟡", "Coastal Hamptons": "🟡", "Dark Moody Luxury": "", "Japandi Minimal": ""},
    "木纹·暖色深调": {"Dark Moody Luxury": "✨✨", "English Country Estate": "✨✨", "Cinematic Wabi-Sabi": "✨✨", "Pinterest Editorial": "✨", "Alpine Chalet": "✨", "Biophilic Oasis": "🟡", "IG Influencer Home": "", "Coastal Hamptons": "", "Japandi Minimal": "", "Lazy Sunday Morning": ""},
    "木纹·中性灰米": {"Japandi Minimal": "✨✨", "Pinterest Editorial": "✨✨", "Cinematic Wabi-Sabi": "✨✨", "IG Influencer Home": "✨✨", "Lazy Sunday Morning": "✨", "Biophilic Oasis": "✨", "Coastal Hamptons": "✨", "Dark Moody Luxury": "✨", "English Country Estate": "🟡", "Alpine Chalet": "🟡"},
    "木纹·冷色浅调": {"Japandi Minimal": "✨✨", "Pinterest Editorial": "✨✨", "Coastal Hamptons": "✨✨", "IG Influencer Home": "✨", "Biophilic Oasis": "✨", "Lazy Sunday Morning": "🟡", "Cinematic Wabi-Sabi": "🟡", "Alpine Chalet": "", "English Country Estate": "", "Dark Moody Luxury": ""},
    "木纹·冷色深调": {"Dark Moody Luxury": "✨✨", "Pinterest Editorial": "✨✨", "Cinematic Wabi-Sabi": "✨", "English Country Estate": "✨", "Japandi Minimal": "🟡", "IG Influencer Home": "", "Coastal Hamptons": "", "Biophilic Oasis": "", "Alpine Chalet": "", "Lazy Sunday Morning": ""},
    "木纹·近白/漂白色": {"Coastal Hamptons": "✨✨", "IG Influencer Home": "✨✨", "Japandi Minimal": "✨✨", "Biophilic Oasis": "✨", "Pinterest Editorial": "✨", "Lazy Sunday Morning": "🟡", "Dark Moody Luxury": "", "Alpine Chalet": "", "English Country Estate": "", "Cinematic Wabi-Sabi": ""},
    "石纹·奶油/洞石": {"Pinterest Editorial": "✨✨", "Biophilic Oasis": "✨✨", "Coastal Hamptons": "✨", "IG Influencer Home": "✨", "Japandi Minimal": "✨", "English Country Estate": "🟡", "Dark Moody Luxury": "", "Alpine Chalet": "", "Cinematic Wabi-Sabi": "", "Lazy Sunday Morning": ""},
    "石纹·暖灰/沙灰": {"Pinterest Editorial": "✨✨", "Japandi Minimal": "✨✨", "Biophilic Oasis": "✨", "Coastal Hamptons": "✨", "Dark Moody Luxury": "🟡", "IG Influencer Home": "🟡", "Cinematic Wabi-Sabi": "", "English Country Estate": "", "Alpine Chalet": "", "Lazy Sunday Morning": ""},
    "石纹·冷灰/水泥灰": {"Pinterest Editorial": "✨✨", "Dark Moody Luxury": "✨✨", "Japandi Minimal": "✨", "Cinematic Wabi-Sabi": "✨", "IG Influencer Home": "🟡", "Biophilic Oasis": "", "Coastal Hamptons": "", "English Country Estate": "", "Alpine Chalet": "", "Lazy Sunday Morning": ""},
    "石纹·深灰/炭灰": {"Dark Moody Luxury": "✨✨", "Pinterest Editorial": "✨✨", "Cinematic Wabi-Sabi": "✨", "Japandi Minimal": "✨", "English Country Estate": "🟡", "IG Influencer Home": "", "Coastal Hamptons": "", "Alpine Chalet": "", "Biophilic Oasis": "", "Lazy Sunday Morning": ""},
    "石纹·近白/极浅": {"Pinterest Editorial": "✨✨", "Coastal Hamptons": "✨✨", "Japandi Minimal": "✨✨", "IG Influencer Home": "✨", "Biophilic Oasis": "✨", "Lazy Sunday Morning": "🟡", "Dark Moody Luxury": "", "Alpine Chalet": "", "English Country Estate": "", "Cinematic Wabi-Sabi": ""},
}

# 扩充风格 × 花色推荐度（国内外市场通用；与上方海外 10 款合并进 FLOOR_TONE_STYLE_RECOMMEND_MAP）
_EXTENDED_STYLE_RECOMMEND_MAP = {
    # 暖色浅木：暖系轻盈 + 自然留白；新增 中古(浅柚木)/美拉德/山系，禅意升 ✨
    "木纹·暖色浅调": {
        "Raw Wood": "✨✨", "Cream Tone": "✨✨", "French Cream": "✨✨",
        "Modern Minimalist": "✨", "Minimal Japanese": "✨", "Nordic Scandi": "✨",
        "Modern American": "✨", "Mid-Century Vintage": "✨", "Zen Tea Room": "✨",
        "Neo-Chinese": "🟡", "Light Luxury": "🟡", "Dopamine": "🟡",
        "Maillard": "🟡", "Mountain Camp": "🟡",
    },
    # 暖色中深木：暖系核心场；山系升 ✨，补 原木
    "木纹·暖色中深调": {
        "Mid-Century Vintage": "✨✨", "Modern American": "✨✨", "Maillard": "✨✨",
        "Neo-Chinese": "✨", "Quiet Luxury": "✨", "Hong Kong Retro": "✨",
        "Mountain Camp": "✨", "Raw Wood": "✨",
        "Shanghai Deco": "🟡", "Wabi-Sabi Raw": "🟡",
    },
    # 深胡桃：中古经典木种升 ✨✨，港式暗调升 ✨✨，补 山系
    "木纹·暖色深调": {
        "Maillard": "✨✨", "Quiet Luxury": "✨✨", "Neo-Chinese": "✨✨",
        "Mid-Century Vintage": "✨✨", "Hong Kong Retro": "✨✨",
        "Shanghai Deco": "✨",
        "Modern American": "🟡", "Industrial Loft": "🟡", "Light Luxury": "🟡",
        "Mountain Camp": "🟡",
    },
    # 中性灰米：万能底，承载面最广；禅意/侘寂升 ✨✨，补 美拉德/山系/中古/轻奢
    "木纹·中性灰米": {
        "Italian Minimalist": "✨✨", "Modern Minimalist": "✨✨", "Quiet Luxury": "✨✨",
        "Wabi-Sabi Raw": "✨✨", "Zen Tea Room": "✨✨",
        "Minimal Japanese": "✨", "Cream Tone": "✨",
        "Raw Wood": "🟡", "French Cream": "🟡", "Bauhaus": "🟡",
        "Maillard": "🟡", "Mountain Camp": "🟡", "Mid-Century Vintage": "🟡",
        "Light Luxury": "🟡",
    },
    # 冷色浅木：冷调极简；补 侘寂
    "木纹·冷色浅调": {
        "Italian Minimalist": "✨✨", "Minimal Japanese": "✨✨", "Nordic Scandi": "✨✨",
        "Modern Minimalist": "✨", "Bauhaus": "✨",
        "Raw Wood": "🟡", "Dopamine": "🟡", "Zen Tea Room": "🟡", "Wabi-Sabi Raw": "🟡",
    },
    # 冷色深木：暗调高级；补 港式/现代简约
    "木纹·冷色深调": {
        "Quiet Luxury": "✨✨", "Industrial Loft": "✨✨",
        "Shanghai Deco": "✨", "Italian Minimalist": "✨", "Bauhaus": "✨",
        "Neo-Chinese": "🟡", "Wabi-Sabi Raw": "🟡",
        "Hong Kong Retro": "🟡", "Modern Minimalist": "🟡",
    },
    # 近白木：明亮通透；补 奶油/禅意
    "木纹·近白/漂白色": {
        "Nordic Scandi": "✨✨", "Raw Wood": "✨✨", "Modern Minimalist": "✨✨",
        "Italian Minimalist": "✨", "Dopamine": "✨", "Bauhaus": "✨", "Cream Tone": "✨",
        "Minimal Japanese": "🟡", "Mediterranean": "🟡", "Zen Tea Room": "🟡",
    },
    # 奶油/洞石：地中海招牌升 ✨✨，奶油升 ✨，补 现代美式
    "石纹·奶油/洞石": {
        "Italian Minimalist": "✨✨", "French Cream": "✨✨", "Mediterranean": "✨✨",
        "Cream Tone": "✨", "Light Luxury": "✨", "Quiet Luxury": "✨",
        "Modern Minimalist": "🟡", "Modern American": "🟡",
    },
    # 暖灰/沙灰石：暖中性硬装；补 侘寂/地中海
    "石纹·暖灰/沙灰": {
        "Italian Minimalist": "✨✨", "Quiet Luxury": "✨✨",
        "Modern Minimalist": "✨", "Light Luxury": "✨",
        "Neo-Chinese": "🟡", "French Cream": "🟡", "Bauhaus": "🟡",
        "Wabi-Sabi Raw": "🟡", "Mediterranean": "🟡",
    },
    # 冷灰/水泥灰石：工业冷调；补 轻奢
    "石纹·冷灰/水泥灰": {
        "Industrial Loft": "✨✨", "Italian Minimalist": "✨✨",
        "Bauhaus": "✨", "Modern Minimalist": "✨", "Quiet Luxury": "✨",
        "Wabi-Sabi Raw": "🟡", "Dopamine": "🟡", "Light Luxury": "🟡",
    },
    # 深灰/炭灰石：暗调奢华；补 港式/现代简约
    "石纹·深灰/炭灰": {
        "Quiet Luxury": "✨✨", "Industrial Loft": "✨✨",
        "Shanghai Deco": "✨", "Italian Minimalist": "✨",
        "Light Luxury": "🟡", "Neo-Chinese": "🟡", "Bauhaus": "🟡",
        "Hong Kong Retro": "🟡", "Modern Minimalist": "🟡",
    },
    # 近白/极浅石：极简通透；补 奶油/静奢
    "石纹·近白/极浅": {
        "Italian Minimalist": "✨✨", "Modern Minimalist": "✨✨",
        "Nordic Scandi": "✨", "Bauhaus": "✨", "Dopamine": "✨",
        "Mediterranean": "🟡", "French Cream": "🟡", "Light Luxury": "🟡",
        "Cream Tone": "🟡", "Quiet Luxury": "🟡",
    },
}

for _tone_key, _recs in _EXTENDED_STYLE_RECOMMEND_MAP.items():
    FLOOR_TONE_STYLE_RECOMMEND_MAP.setdefault(_tone_key, {}).update(_recs)

MARKET_FURNITURE_CHOICES = [
    "🤖 智能匹配 (Auto-Match by Region & Style)",
    "🌐 通用国际 (Generic International)",
    "🌏 澳洲本土 (Early Settler-Inspired)",
    "🥐 法式当代 (Roche Bobois-Inspired)",
    "🌿 北欧精品 (HAY / Muuto-Inspired)",
    "🫙 南欧地中海 (Mediterranean Artisan)",
    "🛋️ 真皮质感 (Full-Grain Leather)",
    "🪑 宜家北欧 (IKEA-Inspired)",
    "🇺🇸 美式舒适 (Pottery Barn-Inspired)",
    "🏭 工业复古 (Industrial Vintage)",
    "🇯🇵 日式原木 (Muji-Inspired)",
    "🏮 新中式家具 (Ming-Inspired)",
    "💎 意式轻奢 (Minotti-Inspired)",
]
MARKET_FURNITURE_MAP = {
    # 默认：不锁定单一品牌/国家，要求模型按"地区 + 装修风格"自行匹配出最合理的家具
    "🤖 智能匹配 (Auto-Match by Region & Style)": ("Do NOT lock the furniture to any single national design house or brand. Instead, choose furniture that authentically belongs to BOTH this home's geographic region/country AND its stated interior décor style described above — exactly the pieces a real, design-aware local resident with this décor would actually own. Match silhouettes, materials, wood tones, upholstery fabrics and metal finishes to that combined context: regional design conventions, climate and lifestyle on one side, and the room's style palette and level of formality on the other. Keep brands and forms locally plausible — clean Scandinavian forms in Northern Europe, deep slipcovered comfort in North America, low solid-wood minimalism in East Asia, sun-bleached artisan pieces around the Mediterranean, and so on — never a mismatched import. The furniture must read as coherent with both the location and the décor, like a genuine home assembled by its owner, not a generic showroom set."),
    "🌐 通用国际 (Generic International)": "",
    "🌏 澳洲本土 (Early Settler-Inspired)": ("Furniture is distinctly Australian in character, inspired by brands like Early Settler. Key pieces: a large deep-seated modular sofa in natural linen weave or cream bouclé on low eucalyptus timber legs; a live-edge solid timber slab or travertine plinth coffee table; woven rattan-back dining chairs with linen-upholstered seats; oversized terracotta or stone-look ceramic planters holding monstera or fiddle-leaf fig; a timber ceiling fan or rattan pendant light overhead; a flat-woven jute rug in warm sand or natural cream. All timber is natural-finish light oak or recycled/reclaimed wood. The space feels warm, breezy, and genuinely lived-in."),
    "🥐 法式当代 (Roche Bobois-Inspired)": ("Furniture has a distinctly Parisian contemporary character, inspired by French design houses like Roche Bobois. Key pieces: a curved velvet sofa or settee in a jewel tone — deep sapphire, bottle green, or burgundy — with rounded arms and slender brass legs; a marble-top or travertine side table with aged-brass legs; upholstered dining chairs in velvet or bouclé with carved wooden frames; a statement brass arc floor lamp or articulated wall sconce; a gallery wall of art prints and mirrors in gilt or dark-patinated frames. Walls are warm cream with ornate plaster mouldings or panelling. Brass, aged gold, and dark-patinated bronze metalwork throughout. The atmosphere is cultivated, quietly opulent, and unmistakably Parisian."),
    "🌿 北欧精品 (HAY / Muuto-Inspired)": ("Furniture has a distinctly premium Scandinavian character, inspired by brands like HAY and Muuto. Key pieces: a clean-lined sofa in warm grey, dusty rose, or deep teal wool or bouclé on slender solid ash or beech legs; a round or oval dining table in natural ash or lacquered oak with tapered legs; mixed dining chairs — some upholstered shell, some bentwood — in complementary tones; a geometric pendant light in matte black, brass, or terracotta; open shelving in solid timber displaying books and curated ceramics. One strong accent colour — mustard, brick red, deep pine green, or cobalt — used across two or three objects only. No visual clutter — every object earns its place with intention."),
    "🫙 南欧地中海 (Mediterranean Artisan)": ("Furniture has a distinctly Southern European artisan character. Key pieces: a low-slung sofa in natural undyed linen or sand cotton slipcover; a wrought-iron or hand-hammered metal coffee table with a travertine or terracotta top; dining chairs in woven rush seat with solid timber frame; handmade ceramic vessels and tableware in earthy glazes — olive green, burnt sienna, salt white. Walls are textured lime plaster in warm white or sandy cream. Dried botanicals — lavender bundles, pampas grass, olive branches — in aged clay or stone vessels. Natural, imperfect, sun-bleached — as if the home has been slowly assembled over decades."),
    "🛋️ 真皮质感 (Full-Grain Leather)": ("Furniture is led by genuine full-grain leather as the hero material, with a tailored, heirloom quality inspired by makers like Poltrona Frau, Natuzzi and the classic Chesterfield. Key pieces: a deep-seated leather sofa in cognac tan, chocolate brown, or oxblood, the hide showing natural grain, soft creasing and a developing patina; one accent armchair — a buttoned Chesterfield wing or a mid-century leather lounge on a wood or polished-steel frame; a solid timber or smoked-glass coffee table; aged-brass or blackened-steel details on legs, lamps and hardware; a wool or kilim throw and one or two structured leather cushions. Surrounding timber is warm walnut or oak. The atmosphere is grounded, quietly luxurious and built to last — the leather is the centrepiece and ages beautifully."),
    "🪑 宜家北欧 (IKEA-Inspired)": ("Furniture has an accessible, flat-pack Scandinavian character inspired by IKEA — practical, friendly and budget-smart, the look of a young first home. Key pieces: a clean-lined fabric sofa in light grey, beige or soft blue with simple removable covers and slim wooden or plastic legs; an open bookshelf or modular cube storage holding books, woven baskets and a few plants; a compact light-birch or white-laminate coffee table and matching TV bench; simple spindle or moulded dining chairs around a small birch table; a paper or fabric pendant shade; affordable textiles in cheerful but restrained tones. Everything is light, airy and space-efficient, with a slightly mixed, self-assembled charm — a real young couple's or student's home rather than a designer showroom."),
    "🇺🇸 美式舒适 (Pottery Barn-Inspired)": ("Furniture has a relaxed, generous North-American character inspired by Pottery Barn and Restoration Hardware. Key pieces: an oversized deep slipcovered sofa in washed linen or cotton — sand, oatmeal or soft white — with loose back cushions you can sink into; a large reclaimed-wood or weathered-oak coffee table; a comfortable upholstered or worn-leather club chair; layered textiles — a chunky knit throw, mixed cushions, a soft jute or wool rug; woven storage baskets, stacked books and framed photos casually arranged. Timber is warm and lightly distressed; metal accents are oil-rubbed bronze or aged nickel. The atmosphere is warm, family-centred, durable and inviting — comfort and lived-in ease over sharp design statements."),
    "🏭 工业复古 (Industrial Vintage)": ("Furniture has a raw industrial-loft character — reclaimed materials and honest hardware, as if salvaged from an old factory or warehouse conversion. Key pieces: a distressed leather sofa in worn tan or near-black on a low frame; a coffee table or media unit built from reclaimed timber and black steel, with visible bolts and castor wheels; metal-framed shelving or a riveted locker cabinet; a cognac-leather and iron accent chair; cage or Edison-bulb pendants and articulated task lamps in blackened or gunmetal steel. Materials lean to weathered wood, raw or waxed steel, leather and a touch of concrete. The palette is charcoal, rust, tobacco and steel grey. The atmosphere is utilitarian and characterful, celebrating patina and mechanical detail."),
    "🇯🇵 日式原木 (Muji-Inspired)": ("Furniture has a calm, low-slung Japanese character inspired by Muji and Nitori — honest solid wood and quiet restraint. Key pieces: a low fabric sofa in oatmeal or warm grey sitting close to the floor; light oak, ash or beech tables and storage with clean rounded edges and visible grain; simple spindle or soft-cornered dining chairs; open low shelving with neatly spaced books, a single ceramic and a small plant; unbleached cotton and linen textiles; a paper-shade or simple wood-and-linen pendant. Everything is light natural timber with minimal hardware and no clutter — every object intentional. The atmosphere is serene, modest and orderly, honouring negative space and the warmth of natural wood."),
    "🏮 新中式家具 (Ming-Inspired)": ("Furniture has a refined contemporary-Chinese character, reinterpreting Ming-dynasty joinery for modern living. Key pieces: a low sofa with clean rectilinear lines in warm grey or tea-brown fabric; dark walnut, wenge or ebony-stained hardwood pieces showing slender frames, exposed joinery and gently curved aprons; a pair of Ming-line armchairs or a round-back chair; a lattice-screen cabinet or low media console; one celadon, blanc-de-chine or tea-glazed ceramic vessel; an ink-painting scroll or single piece of calligraphy as art. Metals are aged brass or blackened bronze, used sparingly. The palette is warm off-white, aged gold, dark timber and muted ink tones. The atmosphere is dignified, culturally rooted and quietly contemporary."),
    "💎 意式轻奢 (Minotti-Inspired)": ("Furniture has a sophisticated Italian designer character inspired by Minotti and B&B Italia — architectural, modular and impeccably made. Key pieces: a low, wide modular sofa in taupe, dove grey or warm sand with crisp tailoring and slim metal feet; a sculptural lounge chair in leather or bouclé; layered low coffee tables in marble, smoked glass and bronzed metal; a clean-lined sideboard in matte lacquer or smoked oak; a single statement floor lamp on a thin brushed-bronze stem; restrained accessories — one large art piece, a sculptural object, a coffee-table book. Materials are premium and quietly expensive: full-grain leather, fine wool, honed stone, bronzed steel. The atmosphere is calm, confident and architecturally precise — luxury that never shouts."),
}

AVOID_LIST = ["任何形式和尺寸的地毯", "任何人物出镜", "任何宠物出镜", "太阳光直射在地板上产生的高光过曝", "与地板颜色相近的顺色家具", "任何地板表面的反光、倒影或光斑", "任何粉红色偏 (pink tint) 或 洋红色色块 (magenta artifacts)"]

# ══════════════════════════════════════════════════════════════════════════
# 🇨🇳 国内市场专项参数
# ══════════════════════════════════════════════════════════════════════════

CN_DEVELOPERS = [
    "── 不指定 ──", "万科 Vanke", "碧桂园 Country Garden", "恒大 Evergrande",
    "融创 Sunac", "保利 Poly", "华润置地 CR Land", "龙湖 Longfor",
    "中海 CIFI", "招商蛇口 CMB", "金地 Gemdale", "绿城 Greentown",
]
CN_DEVELOPER_MAP = {
    "── 不指定 ──": "",
    "万科 Vanke": "Vanke-standard residential development: understated quality, warm practical elegance, middle-class aspirational tone. Clean finishes, coordinated colour palette, reliable premium specification without ostentation.",
    "碧桂园 Country Garden": "Country Garden-style large-scale suburban residential complex: grand ornate European-inspired architecture, palatial lobby spaces, manicured landscaping. Interior spaces tend toward classical European styling with rich warm tones.",
    "恒大 Evergrande": "Evergrande-style palatial residential: neo-classical European grandeur, symmetrical layouts, gilt accents, marble surfaces, high-ceiling entrance halls. The aesthetic is deliberately impressive and monumental.",
    "融创 Sunac": "Sunac-quality premium urban residential: contemporary luxury, sophisticated material palette, high-specification finishes. The interior targets discerning upper-middle-class buyers.",
    "保利 Poly": "Poly-standard residential: balanced quality, clean contemporary aesthetic, reliable fit-out. The space feels complete and properly considered without flashiness.",
    "华润置地 CR Land": "China Resources Place quality: refined urban elegance, premium craft, understated luxury. Known for thoughtful detailing and high-quality material selection.",
    "龙湖 Longfor": "Longfor-quality community residential: warm livability, human-scale proportions, thoughtful layout. Creates a genuine sense of home rather than showroom staging.",
    "中海 CIFI": "CIFI-standard residential: clean commercial precision, contemporary minimalist aesthetic, business-class quality. The interior is orderly, precise and efficiently planned.",
    "招商蛇口 CMB": "CMB Shekou-quality coastal/urban residential: fresh contemporary design, light-toned materials, connection to outdoor context. Often features harbour or waterfront lifestyle references.",
    "金地 Gemdale": "Gemdale-standard residential: modern design sensibility, quality materials, livable proportions. The space balances practicality with tasteful contemporary aesthetics.",
    "绿城 Greentown": "Greentown-quality residential: classical Chinese garden aesthetic sensibility, refined cultural tone, high material specification. Often features traditional courtyard references and refined craftsmanship.",
}

CN_UNIT_TYPES = [
    "── 不指定 ──",
    "刚需一居 / 单身公寓 (35-60㎡)",
    "刚需两房 (60-85㎡)",
    "小三房 (85-99㎡)",
    "改善三房 (100-130㎡)",
    "四房两厅 (130-160㎡)",
    "改善大平层 (160-220㎡)",
    "顶豪大平层 (220㎡+)",
    "复式 / 跃层",
    "Loft 公寓",
    "叠墅",
    "联排别墅",
    "独栋别墅",
]

CN_UNIT_TYPE_MAP = {
    "── 不指定 ──": "",
    "刚需一居 / 单身公寓 (35-60㎡)": "compact one-bedroom or studio apartment for first-time buyers or singles. Spatial logic: one open living-sleeping or compact one-bedroom plan, very efficient circulation, every centimeter planned for storage. Furniture scale: slim two-seat sofa or lounge chair, 2-person dining table or wall-mounted bar table, narrow shoe cabinet, compact wardrobe, no bulky sectional sofa, no luxury villa-scale furniture. Modeling priority: make the room believable as a small Chinese urban apartment, with integrated storage, tight but comfortable clearances, and a strong sense of efficient living.",
    "刚需两房 (60-85㎡)": "entry-level two-bedroom Chinese apartment for a couple or young family. Spatial logic: compact living-dining room, enclosed or semi-open kitchen, one primary bedroom and one secondary bedroom. Furniture scale: slim 2-3 seat sofa, small rectangular coffee table, 4-person dining table, full-height wardrobes, compact TV wall, no oversized island, no grand foyer. Modeling priority: practical proportions, clean circulation, useful storage, furniture kept apartment-scaled.",
    "小三房 (85-99㎡)": "compact three-bedroom Chinese apartment with high space efficiency. Spatial logic: one modest living-dining area, one primary bedroom, one secondary bedroom, one smaller room used as child room, study or tatami room. Furniture scale: 3-seat sofa, compact coffee table, 4-person dining table, wardrobes built into bedrooms, one small desk or tatami platform in the smallest room. Modeling priority: show an optimized developer floor plan, controlled furniture size, no wasted hallway, no luxury-scale open space.",
    "改善三房 (100-130㎡)": "upgrade-oriented three-bedroom apartment for a family. Spatial logic: more comfortable living-dining area, better daylight, clearer entrance storage, primary bedroom can feel more refined, secondary rooms are functional rather than cramped. Furniture scale: 3-seat or small L-shaped sofa, 4-6 person dining table, sideboard or display cabinet, larger wardrobe wall, occasional accent chair only if space allows. Modeling priority: family comfort, refined built-in cabinetry, tasteful materials, larger but still realistic apartment proportions.",
    "四房两厅 (130-160㎡)": "four-bedroom family apartment with stronger functional zoning. Spatial logic: living and dining can be more independent, entrance storage is more complete, bedrooms serve parents, children, guests or study, balcony and service areas may be visible. Furniture scale: larger sofa group, 6-person dining table, sideboard, bookcase or display storage, more generous wardrobes. Modeling priority: mature family lifestyle, clear zoning, layered storage, premium but not top-luxury scale.",
    "改善大平层 (160-220㎡)": "large premium flat for upgrade buyers. Spatial logic: expansive living-dining hall, wider circulation, stronger window wall, more generous bedrooms, possible separate study, dressing room or western-kitchen island. Furniture scale: L-shaped or modular sofa, larger coffee table, 6-8 person dining table, console, sideboard, lounge chair, high-quality built-in cabinetry. Modeling priority: broad floor span, visible open floor area, refined material transitions, premium apartment calm rather than villa exaggeration.",
    "顶豪大平层 (220㎡+)": "ultra-luxury large flat. Spatial logic: grand open-plan living-dining area, panoramic windows, gallery-like circulation, primary suite, independent study, dressing room, western kitchen or island bar may appear. Furniture scale: generous sectional sofa, sculptural lounge chairs, 8-person dining table, statement sideboard, large artwork, bespoke cabinetry. Modeling priority: expansive scale, negative space between furniture, premium materials, panoramic daylight, no compact-apartment crowding.",
    "复式 / 跃层": "duplex apartment with two-level spatial relationship. Spatial logic: stair, mezzanine edge, double-height living room or split-level transition should appear when appropriate; public functions on lower level, bedrooms or study above. Furniture scale depends on area but should respect vertical openness. Modeling priority: visible stair connection, vertical depth, layered sightlines, no flat single-level apartment feeling.",
    "Loft 公寓": "urban loft apartment with compact vertical layout. Spatial logic: open lower level for living-dining-kitchen, elevated sleeping zone or mezzanine where appropriate, higher ceiling impression, exposed stair or ladder-like stair. Furniture scale: compact sofa, small dining/bar counter, integrated storage under stair or mezzanine, no large family-apartment furniture. Modeling priority: vertical efficiency, young urban lifestyle, open-plan compactness.",
    "叠墅": "stacked villa residence with villa-like apartment quality. Spatial logic: private-entry feeling, larger windows, terrace, garden-facing living room or stair cue where appropriate, more generous rooms than standard apartments. Furniture scale: premium family furniture, larger sofa, 6-person dining, refined storage. Modeling priority: villa-like calm without detached-villa exaggeration, indoor-outdoor connection, layered residential depth.",
    "联排别墅": "Chinese townhouse residence. Spatial logic: multi-level family home, private garden or courtyard connection, stair and larger living room possible, stronger separation between public and private floors. Furniture scale: larger sofa group, dining for 6-8, entry console, possible fireplace-like feature wall only if style supports it. Modeling priority: townhouse depth, garden-facing living, vertical family-home structure.",
    "独栋别墅": "detached villa residence. Spatial logic: standalone private home, generous room scale, garden context, possible double-height living, broad stair, large kitchen or dining room, primary suite and leisure rooms. Furniture scale: large but not chaotic, premium sofa group, 8-person dining, sculptural lighting, more negative space. Modeling priority: private estate atmosphere, architectural depth, indoor-outdoor continuity, premium residential detailing.",
}

CN_ROOM_TYPES = [
    "客餐厅一体",
    "横厅",
    "竖厅",
    "独立客厅",
    "独立餐厅",
    "LDK 客餐厨一体",
    "开放式厨房",
    "封闭式厨房",
    "玄关 / 入户区",
    "入户花园",
    "主卧套房",
    "主卧室",
    "次卧",
    "儿童房",
    "老人房",
    "书房",
    "多功能房",
    "榻榻米房",
    "衣帽间",
    "生活阳台",
    "景观阳台",
    "飘窗卧室",
    "卫生间",
    "浴室 (带浴缸)",
]

CN_ROOM_TYPE_MAP = {
    "客餐厅一体": "integrated living-dining room typical of Chinese apartments. Required objects: sofa facing a TV/media wall, coffee table, dining table near kitchen or entry circulation, sideboard or storage if space allows. Layout: sofa zone and dining zone share one continuous hall but remain visually distinct; leave a clear walking path from entrance to balcony/window. Avoid: hotel lobby furniture, isolated dining table floating randomly, oversized sectional in compact units.",
    "横厅": "wide horizontal living hall. Required objects: sofa group, TV/media wall or low media cabinet, dining table or lounge zone arranged side-by-side along the broad window wall. Layout: emphasize lateral width, large window frontage and broad visible floor span; sofa and dining should read as parallel lifestyle zones, not a narrow corridor. Avoid: deep tunnel-like composition, cramped vertical hall layout, blocking the window wall with tall cabinets.",
    "竖厅": "depth-oriented vertical living hall. Required objects: sofa facing TV wall, coffee table, dining table placed along the circulation axis or near kitchen entrance. Layout: emphasize room depth toward balcony/window, with furniture arranged front-to-back; keep a clear central route. Avoid: side-by-side horizontal hall composition, furniture that cuts the long axis.",
    "独立客厅": "separate living room. Required objects: sofa, coffee table, TV/media wall, floor lamp or side table where appropriate. Layout: focused family lounge, furniture centered on conversation and TV viewing, dining should not dominate. Avoid: dining-room composition, kitchen island, random bed-like lounge furniture.",
    "独立餐厅": "separate dining room or clearly independent dining zone. Required objects: dining table as main subject, dining chairs, sideboard or dining storage, pendant or focused ceiling light above table. Layout: table aligned with room geometry, enough clearance around chairs, storage against wall. Avoid: sofa, coffee table, TV-centered layout.",
    "LDK 客餐厨一体": "open LDK layout combining living room, dining area and kitchen. Required objects: sofa zone, dining table adjacent to kitchen, visible kitchen cabinetry, island or peninsula only when unit scale allows. Layout: continuous open-plan family interaction, kitchen-dining-living arranged in a readable sequence; flooring should visually connect all zones. Avoid: closed kitchen feeling, isolated kitchen with no relation to dining, oversized island in compact units.",
    "开放式厨房": "open kitchen connected to dining or living area. Required objects: base and wall cabinets, countertop, sink/cooktop zone, dining bar, peninsula or island if suitable, nearby dining table. Layout: cabinetry lines should be clear and functional, with open sightline to dining/living. Avoid: sofa-centered composition, impractical decorative kitchen, empty countertop with no working kitchen logic.",
    "封闭式厨房": "enclosed Chinese kitchen. Required objects: L-shaped or U-shaped cabinets, countertop, sink, cooktop, range hood, storage, sliding glass door or doorway separation where appropriate. Layout: compact work triangle, durable surfaces, practical cooking clearance. Avoid: living-room furniture, large island, open Western kitchen composition unless explicitly requested.",
    "玄关 / 入户区": "Chinese apartment entrance foyer. Required objects: shoe cabinet, bench or stool if space allows, mirror or hook rail, small drop zone, entry door cue. Layout: transitional threshold from entry door into home, storage-focused, compact but intentional. Avoid: sofa group, dining table, bedroom furniture.",
    "入户花园": "entry garden or semi-open entrance garden zone. Required objects: shoe/storage cabinet, greenery, small bench or console, transitional flooring or daylight cue. Layout: combine entry function with a semi-outdoor garden feeling; circulation into the home must remain clear. Avoid: full living-room furniture set, random balcony furniture without entry storage.",
    "主卧套房": "primary bedroom suite. Required objects: larger bed, bedside tables, wardrobe wall or walk-in closet cue, ensuite bathroom doorway or dressing zone if visible. Layout: bed as calm anchor, storage integrated along one wall, circulation to dressing/bathroom remains elegant. Avoid: child-room furniture, compact guest-room scale, sofa-centered living layout.",
    "主卧室": "primary bedroom in a Chinese apartment. Required objects: queen or king bed, two bedside tables if space allows, wardrobe or built-in closet, soft lighting. Layout: bed centered or aligned to main wall, wardrobe placed along side wall, clear walking path around bed. Avoid: desk dominating the room unless unit is very compact.",
    "次卧": "secondary bedroom. Required objects: bed, wardrobe, small desk or bedside storage depending on room size. Layout: smaller than primary bedroom, efficient wall use, restrained furniture scale. Avoid: oversized bed and luxury suite proportions.",
    "儿童房": "children's bedroom. Required objects: child bed or single bed, study desk, wardrobe, toy/book storage. Layout: safe rounded details, desk near window if possible, playful but controlled color accents. Avoid: clutter overload, cartoon theme park feeling, adult master-bedroom furniture.",
    "老人房": "elderly parents' bedroom. Required objects: comfortable bed, bedside storage, wardrobe, chair or small reading corner if space allows. Layout: accessible walking clearance, calm warm lighting, no low difficult-to-use furniture, no sharp cluttered circulation. Avoid: bunk beds, loft beds, overly trendy fragile furniture.",
    "书房": "home study room. Required objects: desk, ergonomic chair, bookcase or wall storage, task lighting. Layout: desk facing wall or window, storage kept orderly, floor area still visible. Avoid: sofa-centered living room layout, bed as main subject unless it is a combined guest study.",
    "多功能房": "flexible spare room used as study, guest room or hobby room. Required objects: modular desk, sofa bed or compact daybed if appropriate, wall storage, foldable or movable furniture. Layout: flexible use must be readable; keep circulation clear and avoid overfilling. Avoid: pretending it is a full master suite or grand living room.",
    "榻榻米房": "tatami platform room common in Chinese apartments. Required objects: raised timber platform with integrated drawers or lift-up storage, low table or mattress option, wall cabinet or bookcase. Layout: platform defines the room, often used as study/guest/child room; show storage logic clearly. Avoid: Western sofa set, random floor mattress without platform storage.",
    "衣帽间": "walk-in closet or dressing room. Required objects: wardrobes, open shelves or glass-front cabinets, drawers, mirror, dressing stool or island only in large units. Layout: storage dominates both sides or one main wall, clear aisle down the middle, floor remains visible as a walkway. Avoid: sofa, coffee table, bed as main subject.",
    "生活阳台": "service balcony for laundry and utility functions. Required objects: washing machine cabinet, dryer or stacked laundry, utility sink, storage cabinet, cleaning tools concealed or neatly placed. Layout: narrow practical service strip with durable finishes and daylight. Avoid: lounge chairs, sofa, decorative living balcony styling.",
    "景观阳台": "scenic balcony or enclosed leisure balcony. Required objects: window wall or railing cue, small casual chair/table, plants, tea or reading corner if space allows. Layout: daylight and outdoor view are the focus; connect visually to living room or bedroom. Avoid: washing machine utility setup unless it is a service balcony.",
    "飘窗卧室": "bedroom with a bay window alcove and deep window seat platform. Required objects: bed, wardrobe, bay window cushion or low platform, small side table or reading object. Layout: daylight focused around bay window, bed and wardrobe arranged around the alcove, bay window should be clearly recognizable. Avoid: treating the room like a normal bedroom with no bay-window cue.",
    "卫生间": "Chinese apartment bathroom. Required objects: vanity, mirror cabinet, toilet, shower area or glass partition, floor drain logic. Layout: compact wet-zone planning, practical storage, durable surfaces. Avoid: sofa, armchair, coffee table, bedroom furniture.",
    "浴室 (带浴缸)": "bathroom with bathtub. Required objects: bathtub clearly visible, vanity, mirror, toilet or shower zone as appropriate. Layout: premium but practical wet-room planning, tub placed along wall or near window only if believable, clear floor drainage logic. Avoid: living-room furniture, purely hotel-spa fantasy with no residential utility.",
}

def build_cn_layout_guidance(cn_unit_type: str, cn_room_type: str) -> str:
    """Return scale-aware domestic layout guidance for Chinese unit + room combinations."""
    unit = cn_unit_type or ""
    room = cn_room_type or ""
    guidance = []

    if any(k in unit for k in ["刚需一居", "刚需两房", "小三房"]):
        guidance.append(
            "Scale rule: compact Chinese apartment proportions. Use slim furniture, shallow cabinets, tight but believable circulation, strong storage integration, and avoid oversized luxury pieces."
        )
        if any(k in room for k in ["客餐厅", "横厅", "竖厅", "独立客厅", "LDK"]):
            guidance.append(
                "Living area rule: sofa should be 2-seat to compact 3-seat, coffee table small, dining table 2-4 seats, TV wall compact, furniture placed against walls where possible to preserve open floor."
            )
        if any(k in room for k in ["主卧", "次卧", "儿童房", "老人房", "飘窗卧室"]):
            guidance.append(
                "Bedroom rule: bed, wardrobe and desk/bedside storage must fit tightly; avoid suite-like open space, large lounge chair, or hotel-scale dressing area."
            )
        if any(k in room for k in ["开放式厨房", "封闭式厨房", "LDK"]):
            guidance.append(
                "Kitchen rule: prefer compact L-shaped or galley cabinetry; use a peninsula or small bar only if the room is LDK/open kitchen; avoid a large freestanding island."
            )

    elif any(k in unit for k in ["改善三房", "四房两厅"]):
        guidance.append(
            "Scale rule: comfortable family-apartment proportions. Use more complete storage, better daylight, 4-6 seat dining, refined cabinetry, and family-oriented circulation without exaggerating into villa scale."
        )
        if any(k in room for k in ["客餐厅", "横厅", "竖厅", "独立客厅", "LDK"]):
            guidance.append(
                "Living area rule: 3-seat or small L-shaped sofa is appropriate, with sideboard or display storage; dining can seat 4-6; keep zones clear and family-practical."
            )
        if any(k in room for k in ["主卧套房", "主卧室"]):
            guidance.append(
                "Primary bedroom rule: wardrobe wall, larger bed, bedside tables and a refined private atmosphere are appropriate; ensuite/dressing cues only if the room type is primary suite."
            )
        if any(k in room for k in ["儿童房", "书房", "多功能房", "榻榻米房"]):
            guidance.append(
                "Secondary room rule: include study, storage or guest-use logic; these rooms should feel useful and planned, not decorative leftovers."
            )

    elif any(k in unit for k in ["改善大平层", "顶豪大平层"]):
        guidance.append(
            "Scale rule: large premium flat proportions. Use wider circulation, larger window walls, more negative space between furniture, premium built-ins, and broader visible floor areas."
        )
        if any(k in room for k in ["客餐厅", "横厅", "独立客厅", "LDK"]):
            guidance.append(
                "Large-flat living rule: larger sofa group, lounge chair, sideboard, sculptural coffee table and 6-8 seat dining are appropriate; preserve gallery-like spacing and strong floor visibility."
            )
        if any(k in room for k in ["主卧套房", "衣帽间"]):
            guidance.append(
                "Suite rule: dressing-room or walk-in closet cues are appropriate, with bespoke wardrobes, vanity or dressing island only when the selected room is a suite or closet."
            )
        if any(k in room for k in ["开放式厨房", "LDK"]):
            guidance.append(
                "Premium kitchen rule: a western-style island, tall appliance wall or bar counter can appear, but it must connect logically to dining and not block floor visibility."
            )

    elif any(k in unit for k in ["复式", "跃层", "Loft"]):
        guidance.append(
            "Scale rule: vertical residence. Include stair, mezzanine, split-level edge or double-height cue where appropriate; the room should not read as a normal flat apartment."
        )
        if any(k in room for k in ["客餐厅", "LDK", "开放式厨房"]):
            guidance.append(
                "Vertical public-zone rule: stair or mezzanine should be visible if possible; lower level can combine living, dining and kitchen, with furniture arranged to preserve vertical sightlines."
            )
        if any(k in room for k in ["主卧", "次卧", "书房", "多功能房"]):
            guidance.append(
                "Upper/private-zone rule: use compact but layered private-room planning; avoid placing large public living furniture in an upstairs bedroom or study."
            )

    elif any(k in unit for k in ["叠墅", "联排别墅", "独栋别墅"]):
        guidance.append(
            "Scale rule: villa or villa-like family residence. Use stronger indoor-outdoor connection, more generous room depth, private-entry or garden-facing cues, and a more architectural sense of space."
        )
        if any(k in room for k in ["客餐厅", "横厅", "独立客厅", "LDK"]):
            guidance.append(
                "Villa living rule: larger sofa group, 6-8 seat dining, garden/terrace-facing windows or doors, and possibly a stair or courtyard cue are appropriate; avoid compact apartment crowding."
            )
        if any(k in room for k in ["玄关", "入户花园"]):
            guidance.append(
                "Villa entry rule: entry can feel more ceremonial, with console, larger shoe/storage cabinet, garden or courtyard transition, and clearer separation from the main living space."
            )
        if any(k in room for k in ["主卧套房", "主卧室", "衣帽间", "浴室"]):
            guidance.append(
                "Villa private-zone rule: larger suite proportions, walk-in closet, ensuite bath or dressing cues are believable when the selected room supports them."
            )

    return " ".join(guidance)

def build_overseas_realism_layer(country: str, city: str, property_type: str, room_type: str, style_raw: str) -> str:
    """Lightweight overseas realism layer: keep good overseas output, reduce CGI/showroom stiffness."""
    place = f"{city}, {country}".strip(", ") if city and country else (city or country or "the selected overseas location")
    prop = PROPERTY_TYPE_DICT.get(property_type, translate_zh_to_en(property_type))
    room = TECH_DICT.get(room_type, translate_zh_to_en(extract_zh(room_type)))

    cues = [
        f"The room should feel like a photographed lived-in {prop} in {place}, not a sales-gallery render.",
        "Composition may include slight asymmetry, partial furniture cropping, natural object offsets, and non-matching furniture pieces that still belong to the same taste level.",
        "Include 2-4 believable small domestic objects appropriate to the style: books, ceramic vase, folded throw, tray, lamp, plant, framed print, side table objects, or casual textiles.",
        "Keep object density moderate: enough detail to feel real, but not cluttered or messy.",
        "Natural light should create imperfect shadows or soft tonal falloff; avoid perfectly even CGI illumination.",
        "Furniture should have practical residential spacing, with some legs or small objects naturally overlapping the floor while leaving meaningful floor areas visible."
    ]

    # 国际中性锚点：不给地点/文化线索时，模型会回落到训练里最常见的"国内样板间"先验，
    # 导致很多国际风格出图都"像国内"。显式锚定为国际/西式真实住宅；东亚风格则自动让路。
    _asian_style = any(k in (style_raw or "") for k in
                       ("Neo-Chinese", "Hong Kong Retro", "Shanghai Deco", "Zen Tea Room",
                        "Japandi", "Minimal Japanese", "Wabi-Sabi"))
    if _asian_style:
        cues.insert(0, "Stay true to the chosen East-Asian interior style and render an authentic, well-resolved version of it — not a generic developer show-flat.")
    else:
        cues.insert(0, "Render this as an authentic, internationally-styled lived-in home in a contemporary Western / European / international residential context. Unless the interior style is explicitly East-Asian, avoid Chinese or East-Asian developer-apartment, sample-flat (样板间) or show-home styling, signage, furniture conventions, or built-ins.")

    if any(k in property_type for k in ["普通公寓", "豪华大平层"]):
        cues.append("Apartment realism: use balcony/window cues, built-in storage, compact-to-generous but believable room depth, and avoid detached-villa scale.")
    elif any(k in property_type for k in ["现代别墅", "普通独立住宅"]):
        cues.append("House realism: a believable single-family home with honest, not exaggerated, room proportions and natural indoor light; avoid sales-villa showpiece scale.")
    elif "联排" in property_type:
        cues.append("Townhouse realism: use narrower-but-deeper room proportions, stair or entry cues where appropriate, and family-scale storage.")

    if any(k in room_type for k in ["厨房", "餐厨一体"]):
        cues.append("Kitchen realism: show usable countertops, cabinetry, appliance logic, and a few restrained daily-use objects; avoid decorative empty show-kitchen sterility.")
    elif any(k in room_type for k in ["卧室", "儿童房"]):
        cues.append("Bedroom realism: use bedding folds, bedside objects, wardrobe or dresser cues, and soft imperfect textile placement.")
    elif any(k in room_type for k in ["客厅", "客餐厅", "餐厅", "书房"]):
        cues.append("Living/dining realism: show a readable furniture grouping, side objects, books or art, and casual but intentional surface styling.")

    if any(k in style_raw for k in ["Instagram", "Pinterest", "Lazy Sunday"]):
        cues.append("Social-photo realism: styled but not sterile, with one casual object placement that feels photographed in a real home.")
    elif any(k in style_raw for k in ["Japandi", "Wabi-Sabi"]):
        cues.append("Minimal realism: keep sparse composition, but preserve tactile imperfection in ceramics, textiles, wood grain, shadows, and asymmetry.")
    elif any(k in style_raw for k in ["English Country", "Alpine", "Hamptons"]):
        cues.append("Lifestyle realism: allow layered textiles, books, lamps, baskets, plants or heritage objects consistent with the regional style.")

    return "\n\n**[Overseas Real-Home Layer]**\n" + "\n".join(f"• {c}" for c in cues)

CN_TIERS = ["── 不指定 ──", "🏙️ 顶豪 / 超豪华", "💎 高端改善", "🌿 刚改精品", "🏠 刚需标准"]
CN_TIER_MAP = {
    "── 不指定 ──": "",
    "🏙️ 顶豪 / 超豪华": "ultra-luxury top-tier residential development. The overall quality register is the absolute pinnacle: the space communicates architectural ambition, bespoke material specification, and aspirational living at the highest level.",
    "💎 高端改善": "premium upgrade-tier residential development. Quality-oriented families trading up — the interior is well-appointed, tasteful and clearly premium without being gaudy.",
    "🌿 刚改精品": "mid-tier quality-focused residential. Thoughtful design on a practical budget — complete, coordinated and inviting.",
    "🏠 刚需标准": "entry-level standard residential apartment. Practical, efficiently planned, clean and functional. Unpretentious but complete.",
}

CN_DELIVERY_CHOICES = [
    "🏆 样板间 / 展示单位", "✨ 精装房 (开发商精装)", "🎊 婚房", "🏗️ 全装修交付", "── 不指定 ──",
]
CN_DELIVERY_MAP = {
    "── 不指定 ──": "",
    "🏆 样板间 / 展示单位": "This is a professionally staged model apartment / show unit. The interior must have showroom-quality presentation: aspirational lifestyle staging with curated premium furniture, intentional prop arrangement, museum-quality cleanliness, and aspirational domestic perfection. Every surface and arrangement communicates 'this is exactly how you will live here'.",
    "✨ 精装房 (开发商精装)": "Fully fitted premium residential apartment as delivered by the developer. The space features complete high-end fixtures, fittings, and built-in cabinetry. Warm, inhabited but immaculate — the look of someone who has just moved in to an excellent home.",
    "🎊 婚房": "Bridal home / wedding room setup. Warm romantic residential interior: rich warm tones, soft romantic lighting, fresh floral accents, tasteful celebratory elements without kitsch. Conveys new beginnings and domestic happiness.",
    "🏗️ 全装修交付": "Full fit-out delivery standard residential unit. Complete developer fit-out package with coordinated finishes — a complete, move-in-ready home that clearly communicates quality.",
}

CN_SPACE_FEATURES = {
    "南北通透": "cross-ventilated apartment layout with windows on both north and south elevations, excellent natural light penetration from multiple directions simultaneously",
    "落地窗 / 超大采光面": "dramatic floor-to-ceiling windows spanning the full wall height, flooding the interior with natural light and framing outdoor views",
    "飘窗 (凸窗台)": "bay window alcove with a deep window seat platform — a defining feature of Chinese residential design, creating a transitional daylit relaxation zone",
    "入户玄关": "dedicated entrance foyer / entryway vestibule with shoe storage cabinetry and a clearly defined transitional zone between exterior and interior",
    "榻榻米区域": "Japanese-inspired tatami platform zone integrated into the room layout — a raised timber-framed platform with cushioned seating defining a relaxation alcove",
}

CN_FACILITIES = {
    "吊顶 (天花板灯槽造型)": "The ceiling features a multi-level decorative dropped ceiling (吊顶) with recessed LED cove lighting strips running along the perimeter. The cove emits warm ambient light that washes softly upward and outward, creating a luminous halo effect around the ceiling perimeter. This is a MANDATORY architectural element — it must be clearly and prominently visible in the image.",
    "电视背景墙": "A prominent feature TV wall / media wall occupies the main focal wall of the living room. This is a decorative architectural backdrop panel — in stone slab, fluted panel, marble or textured wall treatment — framing the television as the room's visual centrepiece. This is a MANDATORY design element that must be clearly visible.",
    "石膏线 (天花板装饰)": "Ornate plaster crown mouldings and ceiling roses adorn the ceiling perimeter and ceiling-to-wall junction, adding classical decorative depth and refinement to the space.",
    "推拉门 / 阳台隔断": "Floor-to-ceiling sliding glass partition doors connecting the living room to the balcony or sunroom — a defining feature of Chinese apartment layouts, visible as a design element.",
}

CN_CITIES = LOCATION_MAP.get("亚洲 - 东亚", {}).get("中国", ["上海", "北京", "深圳", "广州", "成都", "杭州"])



def get_style_choices(floor_tone):
    """返回 [(显示标签, 原始值), ...] 按推荐度排序"""
    tone_key = next((k for k in FLOOR_TONE_STYLE_RECOMMEND_MAP if k in floor_tone), None)
    rec_map = FLOOR_TONE_STYLE_RECOMMEND_MAP.get(tone_key, {})
    def get_rec(style_full):
        return next((rec for sk, rec in rec_map.items() if sk in style_full), "")
    def sort_order(rec):
        return {"✨✨": 0, "✨": 1, "🟡": 2, "": 3}.get(rec, 3)
    scored = []
    for style_full in STYLES:
        rec = get_rec(style_full)
        short = style_full.split(" - ")[0].strip()
        label = f"{rec} {short}" if rec else f"   {short}"
        scored.append((sort_order(rec), label, style_full))
    scored.sort(key=lambda x: x[0])
    return [(label, val) for _, label, val in scored]


# ── 色调自动分析 ──────────────────────────────────────────
def _compute_tone_confidence(med_h, med_s, med_v, directionality, is_wood, tone_key):
    scores = []
    scores.append(min(100, int(directionality * 550)) if is_wood else min(100, int((0.22 - min(directionality, 0.22)) * 550)))
    if med_v > 85: scores.append(min(100, int(70 + (med_v - 85) * 4)))
    elif med_v > 62: scores.append(85 if 68 < med_v < 83 else 70)
    elif med_v > 42: scores.append(85 if 48 < med_v < 60 else 68)
    else: scores.append(85 if med_v < 36 else 68)
    if "灰米" in tone_key: scores.append(90 if 9 <= med_s <= 21 else 62)
    elif "暖色" in tone_key or "奶油" in tone_key: scores.append(88 if med_s > 26 else 68)
    elif "冷色" in tone_key or "冷灰" in tone_key: scores.append(85 if (med_h > 155 or med_s < 12) else 68)
    elif "近白" in tone_key or "漂白" in tone_key: scores.append(90 if med_s < 12 else 65)
    else: scores.append(78)
    return int(sum(scores) / len(scores))

def analyze_floor_tone(image_path):
    """分析地板素材图，返回最匹配的 FLOOR_TONES 档位和分析 HTML。"""
    if not image_path: return FLOOR_TONES[0], ""
    try:
        import numpy as np
        img = Image.open(image_path).convert('RGB')
        w, h = img.size
        cx, cy = int(w * 0.2), int(h * 0.2)
        img_small = img.crop((cx, cy, w - cx, h - cy)).resize((160, 160), Image.Resampling.LANCZOS)
        arr = np.array(img_small, dtype=np.float32) / 255.0
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        cmax = np.maximum(np.maximum(r, g), b)
        delta = cmax - np.minimum(np.minimum(r, g), b)
        eps = 1e-7
        v_arr = cmax
        s_arr = np.where(cmax < 0.01, 0.0, delta / (cmax + eps))
        h_arr = np.zeros_like(r)
        m_r = (cmax == r) & (delta > eps); m_g = (cmax == g) & (delta > eps); m_b = (cmax == b) & (delta > eps)
        h_arr[m_r] = (60 * ((g[m_r] - b[m_r]) / (delta[m_r] + eps))) % 360
        h_arr[m_g] = 60 * ((b[m_g] - r[m_g]) / (delta[m_g] + eps)) + 120
        h_arr[m_b] = 60 * ((r[m_b] - g[m_b]) / (delta[m_b] + eps)) + 240
        sat_mask = s_arr > 0.06
        med_h = float(np.median(h_arr[sat_mask])) if sat_mask.sum() > 40 else 32.0
        med_s = float(np.median(s_arr)) * 100
        med_v = float(np.median(v_arr)) * 100
        gray = 0.299 * r + 0.587 * g + 0.114 * b
        p = np.pad(gray, 1, mode='edge')
        sx = (-p[:-2,:-2] - 2*p[1:-1,:-2] - p[2:,:-2] + p[:-2,2:] + 2*p[1:-1,2:] + p[2:,2:])
        sy = (-p[:-2,:-2] - 2*p[:-2,1:-1] - p[:-2,2:] + p[2:,:-2] + 2*p[2:,1:-1] + p[2:,2:])
        mag = np.sqrt(sx**2 + sy**2).flatten()
        ang = (np.degrees(np.arctan2(sy, sx)) % 180).flatten()
        hist, _ = np.histogram(ang, bins=18, range=(0, 180), weights=mag)
        directionality = float(hist.max() / mag.sum()) if mag.sum() > 0 else 0.0
        IS_WOOD = directionality > 0.12
        NEAR_WHITE = med_v > 84 and med_s < 18
        LIGHT = 62 <= med_v <= 85; MID_DEEP = 42 <= med_v < 62; DEEP = med_v < 42
        if med_v > 85 and not NEAR_WHITE: LIGHT = True
        WARM = 12 <= med_h <= 55 and med_s >= 22
        GREIGE = 12 <= med_h <= 55 and 8 <= med_s < 22
        COOL = (med_h > 155 or med_h < 12) or (med_s >= 12 and 110 <= med_h <= 265)
        NEUTRAL = med_s < 8
        if IS_WOOD:
            if NEAR_WHITE or (NEUTRAL and med_v > 82): tone_key = "木纹·近白"
            elif LIGHT and WARM: tone_key = "木纹·暖色浅调"
            elif LIGHT and (GREIGE or NEUTRAL): tone_key = "木纹·中性灰米"
            elif LIGHT and COOL: tone_key = "木纹·冷色浅调"
            elif MID_DEEP and WARM: tone_key = "木纹·暖色中深调"
            elif MID_DEEP and (COOL or GREIGE or NEUTRAL): tone_key = "木纹·冷色浅调"
            elif DEEP and WARM: tone_key = "木纹·暖色深调"
            else: tone_key = "木纹·冷色深调"
        else:
            if NEAR_WHITE or (NEUTRAL and med_v > 82): tone_key = "石纹·近白"
            elif (LIGHT or MID_DEEP) and WARM: tone_key = "石纹·奶油/洞石"
            elif (LIGHT or MID_DEEP) and (GREIGE or NEUTRAL): tone_key = "石纹·暖灰/沙灰"
            elif (LIGHT or MID_DEEP) and COOL: tone_key = "石纹·冷灰/水泥灰"
            else: tone_key = "石纹·深灰/炭灰"
        matched = next((t for t in FLOOR_TONES if tone_key.replace("·近白", "") in t or tone_key in t), FLOOR_TONES[0])
        conf = _compute_tone_confidence(med_h, med_s, med_v, directionality, IS_WOOD, tone_key)
        mat_icon = "🪵 木纹" if IS_WOOD else "🪨 石纹"
        info_html = (f'<div style="margin-top:4px;padding:6px 10px;background:#1a1a12;border-left:3px solid #a0826d;'
                     f'border-radius:0 4px 4px 0;font-size:0.8em;color:#c8a87a;line-height:1.5;">'
                     f'🔍 自动识别：<b>{matched}</b> &nbsp;·&nbsp; 置信度 <b>{conf}%</b><br>'
                     f'{mat_icon} &nbsp; H {med_h:.0f}° · S {med_s:.0f}% · V {med_v:.0f}% · 方向性 {directionality:.2f}<br>'
                     f'<span style="color:#7a6040;font-size:0.88em;">💡 如识别有误可在下方手动修改</span></div>')
        return matched, info_html
    except Exception as e:
        return FLOOR_TONES[0], f'<span style="color:#c0392b;font-size:0.8em;">⚠️ 色调分析失败：{e}</span>'



def extract_en(text):
    match = re.search(r'\((.*?)\)', text); return match.group(1).strip() if match else text
def extract_zh(text):
    text_no_emoji = re.sub(r'[^\w\s\(\)（）\-:\/。，！]', '', text).strip()
    return re.sub(r'\(.*?\)', '', text_no_emoji).split(' - ')[0].strip()
def convert_to_srgb(img, icc_profile):
    try:
        from PIL import ImageCms; srgb_profile = ImageCms.createProfile('sRGB')
        if icc_profile: return ImageCms.profileToProfile(img, icc_profile, srgb_profile, outputMode='RGB')
        return img
    except Exception: return img
def get_srgb_save_kwargs():
    try:
        from PIL import ImageCms; return {"format": "PNG", "icc_profile": ImageCms.createProfile('sRGB').tobytes()}
    except Exception: return {"format": "PNG"}


# ── 数据一致性自检（模块加载时执行一次）──────────────────────────────
# STYLES 标签与 STYLE_ATMOSPHERE_MAP 是两份手工维护的数据，靠「map 的 key 是标签
# 子串」隐式对应（见 prompts.py 的匹配逻辑）。新增风格漏配 map 时只会静默退化成
# 通用氛围，这里在启动时点名报出来；只记日志，不抛异常、不影响运行。
def _validate_style_data():
    for label in STYLES:
        if not any(key in label for key in STYLE_ATMOSPHERE_MAP):
            logger.error(f"[数据自检] STYLES 条目在 STYLE_ATMOSPHERE_MAP 中无匹配 key，将退化为通用氛围: {label}")

_validate_style_data()


# 显式导出清单 = 当前被 prompts / sd_prompts / recipes / server_api / tests 实际消费的名字。
# 新增数据表时:确有外部消费才加进来,别回退成 dir() 全量导出。
__all__ = [
    # 翻译与文本工具
    'translate_zh_to_en', 'extract_zh', 'extract_en', 'TRANSLATOR_AVAILABLE',
    # 基础词表/映射
    'TECH_DICT', 'PROPERTY_TYPE_DICT', 'LOCATION_MAP',
    'STYLES', 'STYLE_ATMOSPHERE_MAP',
    'LIGHTINGS', 'LIGHTING_INSTRUCTION_MAP',
    'FLOOR_TONES', 'FLOOR_TONE_CONTRAST_MAP', 'FLOOR_TONE_STYLE_RECOMMEND_MAP',
    'MARKET_FURNITURE_MAP', 'MARKET_FURNITURE_CHOICES',
    'ROOM_TYPES', 'CONTINENTS', 'PROPERTY_TYPES', 'VIEWS', 'ANGLES',
    'FLOOR_SIZES', 'PANEL_SIZES', 'AVOID_LIST',
    'PET_TYPES', 'PET_ACTIONS', 'PET_FOCUS_OPTIONS',
    # 国内模式词表
    'CN_ROOM_TYPES', 'CN_DEVELOPERS', 'CN_UNIT_TYPES', 'CN_TIERS',
    'CN_DELIVERY_CHOICES', 'CN_CITIES',
    'CN_DEVELOPER_MAP', 'CN_TIER_MAP', 'CN_UNIT_TYPE_MAP',
    'CN_ROOM_TYPE_MAP', 'CN_DELIVERY_MAP',
    'CN_SPACE_FEATURES', 'CN_FACILITIES',
    # 组装辅助与图像工具
    'build_overseas_realism_layer', 'build_cn_layout_guidance',
    'convert_to_srgb', 'get_srgb_save_kwargs', 'analyze_floor_tone',
]
