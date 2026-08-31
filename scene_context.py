# -*- coding: utf-8 -*-
"""Structured residential scene catalog and deterministic prompt compiler.

This module deliberately contains no provider calls and no online translation.  The same
catalog is exposed to the web client and consumed by the backend, so UI compatibility rules
and generated prompts cannot silently drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SCENE_CATALOG_VERSION = "scene-v1"
CUSTOM_PRESET = "自定义组合"
LEGACY_PRESET = "历史参数 / 自定义组合"


def _option(value: str, description: str, prompt: str, *, noun: str = "", markets=("overseas", "cn"), **compatibility):
    return {
        "value": value,
        "label": value,
        "description": description,
        "prompt": prompt,
        "noun": noun or prompt.split(".", 1)[0],
        "markets": list(markets),
        "compatibility": compatibility,
    }


PROPERTY_OPTIONS = [
    _option("核心城区高层公寓", "高密城市核心的高层住宅", "A high-rise urban apartment in a dense city core, with apartment-scaled depth, shared-building logic, no private ground garden, and believable vertical-city proportions.", noun="high-rise urban apartment", markets=("overseas",), kind="apartment"),
    _option("标准城市公寓", "成熟城区中的普通公寓", "A standard city apartment with realistic medium-scale rooms, efficient circulation, shared-building proportions, practical storage, and no detached-house scale.", noun="standard city apartment", markets=("overseas",), kind="apartment"),
    _option("豪华大平层", "宽阔、横向展开的高级公寓", "A luxury single-level apartment with a broad window frontage, generous but believable circulation, premium built-ins, larger furniture spacing, and no detached-villa exterior cues.", noun="luxury open-plan apartment", markets=("overseas",), kind="apartment"),
    _option("顶层公寓", "位于高层建筑顶部的高级住宅", "A penthouse apartment at the top of a high-rise, with panoramic glazing, long-distance views, gallery-like spacing and premium residential scale; never a ground-level garden house.", noun="penthouse apartment", markets=("overseas",), kind="apartment"),
    _option("Loft / 仓库改造住宅", "高挑、开放、带工业建筑骨架", "A converted urban loft residence with an open plan, tall ceiling, large industrial-scale windows, exposed structural character and believable adaptive-reuse proportions.", noun="converted urban loft", markets=("overseas",), kind="loft"),
    _option("历史街区公寓", "位于老城建筑中的住宅", "An apartment inside a historic urban building, with solid wall depth, vertically proportioned windows, mature street context and carefully renovated residential interiors.", noun="historic-district apartment", markets=("overseas",), kind="apartment"),
    _option("联排住宅", "狭长、多层、与相邻住宅相连", "A multi-level townhouse with a narrower but deeper plan, stair and entry cues, family-scale storage, shared side walls and a plausible rear garden or terrace connection.", noun="townhouse", markets=("overseas",), kind="house"),
    _option("半独立住宅", "与一户相连的低层家庭住宅", "A semi-detached family house with low-rise proportions, windows on several elevations, practical room scale and a believable private side or rear garden.", noun="semi-detached house", markets=("overseas",), kind="house"),
    _option("普通独立住宅", "真实、克制的独栋家庭住宅", "A detached family house with honest room proportions, clear entry and garden relationships, ordinary residential ceiling height and no exaggerated sales-villa scale.", noun="detached family house", markets=("overseas",), kind="house"),
    _option("现代花园别墅", "强调室内外连接的现代低层别墅", "A modern garden villa with generous but credible rooms, strong indoor-outdoor continuity, broad glazing or terrace doors and a private landscaped garden.", noun="modern garden villa", markets=("overseas",), kind="villa"),
    _option("乡村住宅 / 农舍", "乡野环境中的低层住宅", "A lived-in country house or renovated farmhouse with tactile construction, garden or field relationships, layered domestic objects and relaxed low-rise proportions.", noun="country house", markets=("overseas",), kind="house"),
    _option("山地林间木屋", "山林环境中的木屋或度假住宅", "A mountain or forest cabin with substantial timber construction, sheltered openings, close natural terrain and a convincing relationship to trees, slope and weather.", noun="mountain forest cabin", markets=("overseas",), kind="cabin"),
    _option("海滨住宅", "靠近海岸、面向海景的住宅", "A coastal home designed around salt-air daylight, broad sea-facing openings, sheltered terraces and a credible coastal landscape rather than generic tropical decoration.", noun="coastal house", markets=("overseas",), kind="house"),
    _option("湖畔 / 河岸住宅", "紧邻湖泊或河流的住宅", "A waterfront home with a physically plausible relationship to a lake or river, framed water views, riparian vegetation and calm residential scale.", noun="lakefront or riverside home", markets=("overseas",), kind="house"),
    _option("庭院住宅", "围绕私密内院组织的低层住宅", "A low-rise courtyard house organized around a private enclosed patio, with rooms opening toward the courtyard and no high-rise skyline or shared-building cues.", noun="courtyard house", markets=("overseas",), kind="courtyard"),
]


ROOM_OPTIONS = [
    _option("客餐厅一体", "客厅和餐厅共享连续空间", "An integrated living-dining room. Include a readable sofa group and a dining table as two distinct but connected zones, preserve a clear circulation route, and keep both functions at residential scale; no bed or kitchen-only composition.", noun="living-dining room"),
    _option("横厅", "沿窗面横向展开的宽厅", "A wide horizontal living hall with living and dining or lounge zones arranged side by side along a broad window frontage. Emphasize lateral width and open floor span; never turn it into a narrow tunnel.", noun="wide horizontal living hall"),
    _option("竖厅", "沿进深方向组织的客厅", "A depth-oriented living hall with sofa, media wall and dining arranged along a clear front-to-back circulation axis toward the main window; do not use a side-by-side horizontal-hall composition.", noun="depth-oriented living hall"),
    _option("独立客厅", "独立的家庭会客和休闲空间", "A separate living room centered on a coherent sofa, coffee-table and media or conversation grouping, with practical clearances; dining and bedroom furniture must not dominate.", noun="separate living room"),
    _option("家庭起居室", "比正式客厅更松弛的家庭活动空间", "A relaxed family room with comfortable seating, books, media or games and believable everyday objects. It should feel inhabited and informal, not like a formal lobby or sterile showroom.", noun="family room"),
    _option("独立餐厅", "以餐桌为核心的独立空间", "A separate dining room with the dining table as the main anchor, correct chair clearances, a sideboard or dining storage and focused lighting; no sofa-centered layout.", noun="separate dining room"),
    _option("LDK 客餐厨一体", "客厅、餐厅和厨房连续开放", "An open LDK combining living, dining and kitchen in a readable sequence. Include functional cabinetry, a dining connection and a distinct seating zone; an island appears only when the selected scale can support it.", noun="open-plan LDK"),
    _option("开放式厨房", "与餐厅或客厅连通的厨房", "A functional open kitchen with coherent cabinetry, sink, cooktop, appliances and usable work surfaces, visibly connected to dining or living; no decorative empty show-kitchen logic.", noun="open kitchen"),
    _option("封闭式厨房", "由墙或门分隔的独立厨房", "An enclosed residential kitchen with a believable work triangle, cabinetry, sink, cooktop, extraction and storage, separated by a door or wall; no sofa or oversized show island.", noun="enclosed kitchen"),
    _option("玄关 / 入户区", "住宅入口的过渡和收纳空间", "A residential entrance foyer with an entry-door cue, shoe or coat storage, mirror or console and a clear threshold into the home; no living-room furniture set.", noun="entrance foyer"),
    _option("Mudroom / 后门厅", "连接户外、承担鞋服收纳的过渡空间", "A practical mudroom with bench, hooks, closed storage and durable entry logic connecting the home to a garden, garage or exterior door; keep circulation clear and utility-focused.", noun="mudroom"),
    _option("主卧套房", "带更衣或套卫关系的主卧", "A primary bedroom suite with a large bed, bedside tables, wardrobe or dressing cues and a believable connection to an ensuite; it must read as a private suite, not a lounge.", noun="primary bedroom suite"),
    _option("主卧室", "住宅中的主要卧室", "A primary bedroom with a queen or king bed, bedside tables, wardrobe or dresser and calm circulation around the bed; no sofa-centered living-room arrangement.", noun="primary bedroom"),
    _option("次卧", "尺度小于主卧的次要卧室", "A secondary bedroom with a realistically smaller bed, wardrobe and restrained bedside or desk storage, using efficient wall placement and no oversized luxury-suite proportions.", noun="secondary bedroom"),
    _option("客卧", "供客人短期使用的完整卧室", "A welcoming guest bedroom with bed, bedside lighting, compact wardrobe or luggage surface and uncluttered circulation; comfortable but clearly less elaborate than a primary suite.", noun="guest bedroom"),
    _option("儿童房", "兼顾睡眠、学习和收纳", "A children's bedroom with an age-appropriate bed, study desk, wardrobe and toy or book storage, using controlled playful accents and safe circulation; avoid theme-park clutter.", noun="children's bedroom"),
    _option("书房 / 家庭办公室", "以办公和阅读为主", "A home study with desk, ergonomic chair, task light and book or document storage, arranged around a practical work position; no sofa or bed as the main subject.", noun="home study"),
    _option("家庭图书室", "以书架和阅读座位为核心", "A residential library with substantial book storage, one or two reading seats, side table and layered task lighting, retaining domestic scale rather than institutional rows.", noun="home library"),
    _option("多功能房", "可在书房、客房和爱好空间间切换", "A flexible multipurpose room with modular desk, compact daybed or hobby storage as appropriate. Its flexible use must be readable without overfilling the space.", noun="multipurpose room"),
    _option("健身区", "住宅内的小型健身空间", "A home gym with a restrained set of usable equipment, open movement clearance, mirror or storage where appropriate and no living-room furniture grouping.", noun="home gym"),
    _option("影音室", "以屏幕和观看座位为核心", "A residential media room with screen or projection wall, correctly oriented seating, controlled ambient light and acoustic cues; avoid commercial cinema scale.", noun="media room"),
    _option("阳光房 / Conservatory", "玻璃围护的休闲空间", "A sunroom or conservatory with extensive glazing, garden-facing seating, plants and a strong indoor-outdoor transition, while retaining physically plausible frames and roof support.", noun="sunroom"),
    _option("衣帽间", "以衣柜和更衣动线为主", "A walk-in closet with wardrobes, shelves, drawers and mirror around a clear central aisle; a dressing island appears only at generous scale, and no sofa or bed dominates.", noun="walk-in closet"),
    _option("洗衣房", "洗衣、清洁和储物空间", "A practical laundry room with washer, dryer, utility counter, sink or closed storage and durable surfaces, kept tidy but visibly functional.", noun="laundry room"),
    _option("景观阳台 / 露台", "面向景观的户外或半户外休闲区", "A scenic balcony or terrace with a clear railing or edge, restrained outdoor seating and planters, physically connected to the selected residence and floor level.", noun="scenic balcony or terrace"),
    _option("飘窗卧室", "带深窗台或凸窗阅读区的卧室", "A bedroom with a clearly recognizable bay-window alcove and deep window seat, plus bed and wardrobe arranged around it; the bay window cannot disappear from the composition.", noun="bedroom with bay window"),
    _option("盥洗室 / Powder Room", "不带完整淋浴的小型客用卫生间", "A compact powder room with vanity, mirror, toilet and believable door clearance, using residential finishes; no bathtub or living furniture.", noun="powder room"),
    _option("卫生间", "带淋浴的完整住宅卫生间", "A residential bathroom with vanity, mirror, toilet and a physically planned shower or wet zone, including practical storage and drainage logic.", noun="bathroom"),
    _option("浴室 (带浴缸)", "以浴缸为明确设施的浴室", "A residential bathroom where the bathtub is clearly visible alongside vanity and other believable wet-room functions; premium but not an impractical fantasy spa.", noun="bathroom with bathtub"),
    _option("家庭酒吧 / 品酒区", "住宅中的小型饮品和社交区", "A compact home bar or wine lounge with counter or cabinet, bottle and glass storage, two to four seats and intimate residential scale; never a commercial nightclub.", noun="home bar or wine lounge"),
]


SITE_CONTEXT_OPTIONS = [
    _option("城市核心高密区", "高层建筑密集、街道活动丰富", "A dense central-city setting with layered neighboring towers, active streets and limited ground-level privacy.", markets=("overseas", "cn")),
    _option("成熟城市住宅区", "尺度稳定、生活化的城市社区", "An established urban residential district with mature trees, coherent neighboring buildings and a lived-in streetscape.", markets=("overseas", "cn")),
    _option("历史街区", "保留传统街道和老建筑尺度", "A historic city quarter with preserved facades, narrower streets, mature masonry and human-scale urban depth.", markets=("overseas", "cn")),
    _option("高端低密社区", "建筑间距大、绿化和私密性较好", "An upscale low-density residential neighborhood with generous setbacks, mature landscaping, low-rise neighbors and strong privacy.", markets=("overseas", "cn")),
    _option("新建综合社区", "新建筑、公共绿地和配套混合", "A contemporary mixed-use residential development with new architecture, planned public landscaping and orderly shared amenities.", markets=("overseas", "cn")),
    _option("郊区家庭社区", "以独栋或半独栋住宅为主", "A suburban family neighborhood with detached or semi-detached homes, front gardens, local streets and moderate tree cover.", markets=("overseas",)),
    _option("私密庄园社区", "大地块、门控和高私密性", "A private estate setting with large landscaped plots, controlled access, deep setbacks and minimal visual intrusion from neighbors.", markets=("overseas", "cn")),
    _option("滨海度假区", "面向海岸的低密度休闲地段", "A coastal resort district with sea-oriented buildings, salt-tolerant planting, open sky and relaxed low-density development.", markets=("overseas", "cn")),
    _option("河湖滨水区", "沿河或湖岸展开的住宅地段", "A riverfront or lakeside residential district with open water, riparian planting, promenades or private shoreline relationships.", markets=("overseas", "cn")),
    _option("港口 / 码头区", "游艇、码头和滨水城市建筑并存", "A harbor or marina district with boats, piers, water infrastructure and layered urban waterfront buildings.", markets=("overseas", "cn")),
    _option("乡村村落", "农田、草地和低密住宅构成", "A rural village or countryside setting with fields, hedges, scattered low-rise homes and long landscape depth.", markets=("overseas",)),
    _option("山地森林", "树木、坡地和山体主导", "A mountain-forest setting with close trees, sloping terrain, layered ridges and minimal urban construction.", markets=("overseas", "cn")),
    _option("热带花园社区", "高密度热带植物和湿润环境", "A tropical garden district with dense layered foliage, palms and lush planted boundaries around low- to mid-rise homes.", markets=("overseas",)),
    _option("沙漠绿洲社区", "干旱地貌与人工绿化并置", "A desert-oasis residential setting with arid terrain, pale mineral ground, restrained drought-tolerant planting and irrigated green pockets.", markets=("overseas",)),
    _option("国际中性住宅区", "不强调特定文化符号的通用住宅环境", "A neutral contemporary international residential setting with believable neighboring homes, restrained landscaping and no conflicting regional symbolism.", markets=("overseas", "cn")),
]


FLOOR_LEVEL_OPTIONS = [
    _option("庭院 / 首层", "与地面、庭院或街道直接相连", "Ground or garden level: the eye line is close to exterior terrain, with direct garden, patio or street relationships and no elevated skyline horizon."),
    _option("低层 2–5F", "略高于街道和树冠下部", "Low floor, approximately levels 2 to 5: nearby facades, streets and trees remain prominent, with only modest downward viewing angle."),
    _option("中层 6–15F", "高于多数树木但仍接近周边建筑", "Mid floor, approximately levels 6 to 15: rooftops and neighboring buildings sit near eye level, with moderate urban depth and a restrained downward angle."),
    _option("高层 16–30F", "明显高于街道，适合远景", "High floor, approximately levels 16 to 30: a clearly elevated horizon, layered rooftops or distant landscape and a believable but not aerial downward view."),
    _option("超高层 31F+", "大范围城市或水景远眺", "Very high floor above level 31: long-distance panorama, lower surrounding roofs and a high horizon, while still reading as a window view rather than a drone image."),
    _option("独栋住宅内部楼层", "低层住宅的一层或二层，不按公寓楼层表达", "A room within a low-rise house, typically ground or first upper level, with human-scale garden, street or landscape relationships and no high-rise elevation cues."),
]


ROOM_SCALE_OPTIONS = [
    _option("紧凑", "小户型尺度，家具必须克制", "Compact residential scale with slim furniture, efficient wall use, tight but usable circulation and no oversized statement pieces."),
    _option("标准", "日常住宅的正常尺度", "Standard residential scale with true-to-life furniture, ordinary ceiling height and comfortable but not excessive clearances."),
    _option("宽敞", "改善型住宅尺度和更大留白", "Generous residential scale with broader circulation, larger window frontage, more negative space and premium but believable furniture spacing."),
    _option("宏大 / 挑高", "显著挑高或双层空间", "Grand or double-height residential scale with clearly visible vertical volume, appropriately larger furniture grouping and architectural structure that proves the extra height."),
]


ROOM_LAYOUT_OPTIONS = [
    _option("方正布局", "接近正方形、关系均衡", "A balanced rectilinear room with clear orthogonal walls, centered circulation and no arbitrary warped corners."),
    _option("横向宽厅", "沿主要窗面横向展开", "A laterally wide room organized parallel to the main window frontage, emphasizing breadth rather than deep tunnel perspective."),
    _option("纵深布局", "从入口向窗面形成长轴", "A depth-oriented room with a clear long axis from entry toward the main window and furniture arranged without cutting that axis."),
    _option("狭长布局", "宽度有限、进深较长", "A narrow elongated room using slim furniture and wall-aligned circulation, without pretending to have broad villa proportions."),
    _option("开放一体布局", "多个功能区连续开放", "An open-plan layout where functions connect visibly but remain legible through furniture grouping and circulation rather than arbitrary partitions."),
    _option("转角 / L 型布局", "空间或窗面在转角处延伸", "An L-shaped or corner room with two related spatial arms, a coherent turning circulation path and physically consistent corner geometry."),
    _option("挑高 / 复式布局", "包含楼梯、夹层或双层关系", "A vertical duplex or double-height layout with a visible stair, mezzanine edge or upper-level relationship; it must not read as a flat single-level room."),
]


WINDOW_TYPE_OPTIONS = [
    _option("普通窗", "住宅常见的单组窗", "One or more conventionally sized residential windows with believable sill, frame depth and wall area around them."),
    _option("宽幅景观窗", "低分隔、横向展开的大窗", "A broad picture window with restrained framing, a wide horizontal view and physically plausible wall support."),
    _option("整墙落地窗", "从地面到顶的大面积玻璃", "A floor-to-ceiling glazed wall with real mullions, thickness, reflections and structural edges, strongly connecting the room to the selected view."),
    _option("转角玻璃窗", "两面玻璃在建筑转角相接", "Corner glazing wrapping across two elevations, with consistent exterior parallax and a credible structural corner or glass joint."),
    _option("飘窗", "带深窗台的凸窗", "A projecting bay window with a deep sill or window seat and multiple framed panes, clearly modeled as an alcove rather than a flat image on the wall."),
    _option("推拉露台门", "通向露台或庭院的玻璃推拉门", "Large sliding glazed doors opening toward a terrace, balcony or garden, with visible tracks, frame depth and a believable threshold."),
    _option("法式双开门", "成对开启、通向庭院或阳台", "Paired glazed French doors with residential framing and a believable exterior threshold, suitable for a low-rise garden, terrace or balcony connection."),
    _option("高侧窗 / 天窗", "高位采光或屋顶采光", "Clerestory windows or skylights providing daylight from above eye level; the exterior view is limited and must not become a wall-sized panorama."),
    _option("不强调窗 / 弱化窗景", "窗被裁切、遮挡或不作为画面重点", "Windows are absent from the main composition, cropped, screened or visually subdued; do not invent a dominant panoramic exterior view."),
]


def _view(value, description, prompt, group, *, kinds=(), floors=(), preferred_property="", preferred_cn_unit="", preferred_floor="", preferred_window="", preferred_site=""):
    return _option(
        value, description, prompt, group=group, allowed_property_kinds=list(kinds),
        allowed_floor_levels=list(floors), preferred_property=preferred_property,
        preferred_cn_unit=preferred_cn_unit, preferred_floor=preferred_floor,
        preferred_window=preferred_window, preferred_site=preferred_site,
    )


_GROUND = ("庭院 / 首层", "独栋住宅内部楼层")
_LOW_MID = ("庭院 / 首层", "低层 2–5F", "中层 6–15F", "独栋住宅内部楼层")
_MID_HIGH = ("中层 6–15F", "高层 16–30F", "超高层 31F+")
_HOUSE_KINDS = ("house", "villa", "cabin", "courtyard")
_APT_KINDS = ("apartment", "loft")

VIEW_OPTIONS = [
    _view("近距离城市街道", "窗外可见附近街道、行道树和城市立面", "A nearby city street seen through the glazing, with readable sidewalks, trees, vehicles or street furniture in the foreground and urban facades behind; no distant aerial skyline.", "城市与街区", floors=_LOW_MID, preferred_property="标准城市公寓", preferred_floor="低层 2–5F", preferred_window="普通窗", preferred_site="成熟城市住宅区"),
    _view("绿树成荫的住宅街道", "安静街道、树冠和低层邻居", "A quiet leafy residential street with mature tree canopies, sidewalks and low-rise neighboring homes, layered from near garden edge to the street and houses beyond.", "城市与街区", floors=_LOW_MID, preferred_property="普通独立住宅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="成熟城市住宅区"),
    _view("历史街区建筑立面", "老城街道与传统建筑近中景", "A historic streetscape with vertically proportioned masonry facades, mature material patina and a human-scale street visible at a plausible near-to-middle distance.", "城市与街区", floors=("低层 2–5F", "中层 6–15F"), preferred_property="历史街区公寓", preferred_floor="低层 2–5F", preferred_window="普通窗", preferred_site="历史街区"),
    _view("中层城市屋顶景观", "周边屋顶和中层建筑接近视线高度", "A mid-level urban view across neighboring rooftops, terraces and medium-height buildings, with a restrained downward angle and no extreme aerial perspective.", "城市与街区", kinds=_APT_KINDS, floors=("中层 6–15F", "高层 16–30F"), preferred_property="标准城市公寓", preferred_floor="中层 6–15F", preferred_window="宽幅景观窗", preferred_site="成熟城市住宅区"),
    _view("高层城市天际线", "高层住宅的大范围城市远景", "A layered high-floor city skyline with lower nearby roofs, middle-distance towers and a distant horizon, seen only through the windows with elevation-consistent perspective.", "城市与街区", kinds=_APT_KINDS, floors=_MID_HIGH, preferred_property="核心城区高层公寓", preferred_floor="高层 16–30F", preferred_window="整墙落地窗", preferred_site="城市核心高密区"),
    _view("城市地标远景", "远处地标作为次要视觉锚点", "A recognizable but not oversized urban landmark in the far distance, supported by layered ordinary city buildings in foreground and middle distance; the landmark never appears pasted onto the glass.", "城市与街区", kinds=_APT_KINDS, floors=_MID_HIGH, preferred_property="豪华大平层", preferred_floor="高层 16–30F", preferred_window="宽幅景观窗", preferred_site="城市核心高密区"),
    _view("港口城市景观", "城市建筑、码头和水面同时出现", "An urban harbor view with near waterfront buildings or piers, middle-distance boats and calm water, and a layered city horizon beyond; keep scale and shoreline geometry coherent.", "城市与街区", floors=("中层 6–15F", "高层 16–30F", "超高层 31F+"), preferred_property="豪华大平层", preferred_floor="高层 16–30F", preferred_window="整墙落地窗", preferred_site="港口 / 码头区"),
    _view("高层露台城市景观", "露台边缘与城市天际线共同入镜", "A high-rise terrace or balcony edge in the immediate foreground, layered rooftops and towers in the middle distance, and a distant city horizon; no ground garden or lawn.", "城市与街区", kinds=_APT_KINDS, floors=_MID_HIGH, preferred_property="顶层公寓", preferred_floor="超高层 31F+", preferred_window="推拉露台门", preferred_site="城市核心高密区"),
    _view("修剪整齐的私家草坪后院", "首层住宅连接整洁草坪和树篱", "A private ground-level backyard with a manicured lawn in the near ground, hedges or small trees in the middle ground and neighboring low-rise roofs softly screened beyond.", "私家庭院与露台", kinds=_HOUSE_KINDS, floors=_GROUND, preferred_property="普通独立住宅", preferred_cn_unit="独栋别墅", preferred_floor="庭院 / 首层", preferred_window="推拉露台门", preferred_site="郊区家庭社区"),
    _view("带泳池的阳光后院", "首层别墅庭院内有可信泳池", "A private ground-level pool garden with a physically scaled pool and deck immediately beyond the glazing, landscaped planting around it and low-rise privacy boundaries behind.", "私家庭院与露台", kinds=("villa", "house", "courtyard"), floors=_GROUND, preferred_property="现代花园别墅", preferred_cn_unit="独栋别墅", preferred_floor="庭院 / 首层", preferred_window="推拉露台门", preferred_site="高端低密社区"),
    _view("层次丰富的花园庭院", "近景花草、中景灌木和远景树木", "A lush private garden with layered planting: flowers or paving close to the opening, shrubs and small trees in the middle ground, and taller screening trees beyond.", "私家庭院与露台", kinds=_HOUSE_KINDS, floors=_GROUND, preferred_property="现代花园别墅", preferred_cn_unit="独栋别墅", preferred_floor="庭院 / 首层", preferred_window="推拉露台门", preferred_site="高端低密社区"),
    _view("围合式地中海庭院", "石墙、铺地和耐旱植物构成私密内院", "An enclosed Mediterranean-style courtyard with warm mineral paving, rendered or stone boundary walls, restrained drought-tolerant planting and no distant skyline.", "私家庭院与露台", kinds=("courtyard", "house", "villa"), floors=_GROUND, preferred_property="庭院住宅", preferred_cn_unit="独栋别墅", preferred_floor="庭院 / 首层", preferred_window="法式双开门", preferred_site="高端低密社区"),
    _view("木平台 / 庭院露台", "与室内齐平的户外平台和花园边界", "A believable deck or patio directly outside the doors, with outdoor furniture kept secondary, planted edges in the middle ground and a private low-rise boundary beyond.", "私家庭院与露台", kinds=_HOUSE_KINDS, floors=_GROUND, preferred_property="联排住宅", preferred_cn_unit="联排别墅", preferred_floor="庭院 / 首层", preferred_window="推拉露台门", preferred_site="郊区家庭社区"),
    _view("低密别墅花园群", "越过自家庭院看到邻近低层住宅和绿化", "A low-density garden neighborhood with near private planting, middle-distance hedges and neighboring villas set well apart among mature trees.", "私家庭院与露台", kinds=_HOUSE_KINDS, floors=_LOW_MID, preferred_property="现代花园别墅", preferred_cn_unit="叠墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="高端低密社区"),
    _view("带绿植的社区内院", "公寓面向共享庭院而非远景", "A shared landscaped residential courtyard below or beyond the window, with paths and planting in the near and middle ground and surrounding apartment facades enclosing the view.", "私家庭院与露台", kinds=_APT_KINDS, floors=("低层 2–5F", "中层 6–15F"), preferred_property="标准城市公寓", preferred_floor="低层 2–5F", preferred_window="宽幅景观窗", preferred_site="新建综合社区"),
    _view("开阔海平线", "无遮拦的远海和清晰水平线", "An open sea view with a small amount of near coastal or terrace context, a broad middle-distance water field and a straight distant horizon at the correct eye level.", "水景与海岸", floors=("中层 6–15F", "高层 16–30F", "超高层 31F+", "独栋住宅内部楼层"), preferred_property="海滨住宅", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="整墙落地窗", preferred_site="滨海度假区"),
    _view("海湾景观", "弧形海岸、海面和对岸地形", "A coastal bay with near shoreline vegetation or terrace edge, curved water and beach in the middle distance, and layered headlands or buildings across the bay.", "水景与海岸", preferred_property="海滨住宅", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="滨海度假区"),
    _view("悬崖海岸", "岩岸、海面和远处海平线", "A dramatic but physically plausible cliff-coast view with nearby rock or coastal planting, breaking shoreline in the middle ground and open sea beyond.", "水景与海岸", kinds=_HOUSE_KINDS, floors=("独栋住宅内部楼层", "庭院 / 首层", "低层 2–5F"), preferred_property="海滨住宅", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="滨海度假区"),
    _view("宁静湖景", "平静湖面、近岸植物和远岸", "A calm lake view with reeds, garden or shoreline in the near ground, reflective water in the middle distance and a visible opposite shore or hills beyond.", "水景与海岸", preferred_property="湖畔 / 河岸住宅", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="河湖滨水区"),
    _view("城市河景", "河道、两岸绿化和城市建筑", "A city river view with a coherent near embankment, flowing water through the middle distance and layered buildings or bridges on the opposite bank.", "水景与海岸", floors=("中层 6–15F", "高层 16–30F", "超高层 31F+"), preferred_property="豪华大平层", preferred_cn_unit="改善大平层 (160-220㎡)", preferred_floor="高层 16–30F", preferred_window="整墙落地窗", preferred_site="河湖滨水区"),
    _view("运河街景", "窄水道、桥梁和连续街屋", "A canal-side urban view with water close to the building, small bridges and coherent historic or contemporary facades continuing along both banks.", "水景与海岸", floors=("低层 2–5F", "中层 6–15F"), preferred_property="历史街区公寓", preferred_floor="低层 2–5F", preferred_window="普通窗", preferred_site="历史街区"),
    _view("游艇码头", "停泊游艇、栈桥和受保护水面", "A marina view with nearby piers and moored boats, calm protected water in the middle ground and waterfront buildings or open horizon beyond; boats remain correctly scaled.", "水景与海岸", preferred_property="海滨住宅", preferred_cn_unit="改善大平层 (160-220㎡)", preferred_floor="中层 6–15F", preferred_window="宽幅景观窗", preferred_site="港口 / 码头区"),
    _view("森林树海", "不同距离的树干和树冠形成层次", "A layered forest view with nearby trunks or branches, dense middle-ground foliage and deeper tree canopy fading into the distance; no city buildings.", "自然景观", preferred_property="山地林间木屋", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="山地森林"),
    _view("山脊远景", "近景植被、中景坡地和远处山脊", "A mountain-ridge view composed of near vegetation, middle-distance slopes and a clear distant ridge line, with the horizon placed according to the selected floor level.", "自然景观", preferred_property="山地林间木屋", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="山地森林"),
    _view("阿尔卑斯山谷", "山谷、草地和高山形成纵深", "An alpine valley with meadow or village detail nearby, a descending valley in the middle distance and high mountain mass beyond, all at believable geographic scale.", "自然景观", preferred_property="山地林间木屋", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="整墙落地窗", preferred_site="山地森林"),
    _view("起伏乡野", "树篱、道路和丘陵逐层展开", "Rolling countryside with a near garden or hedgerow, fields and winding lanes in the middle ground and softly layered hills on the horizon.", "自然景观", kinds=_HOUSE_KINDS, preferred_property="乡村住宅 / 农舍", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="乡村村落"),
    _view("草甸与农田", "开阔草地、农田和远处树线", "Open meadow or farmland with grasses or fence detail near the home, field patterns in the middle distance and a distant tree line; no urban skyline.", "自然景观", kinds=_HOUSE_KINDS, preferred_property="乡村住宅 / 农舍", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="普通窗", preferred_site="乡村村落"),
    _view("热带花园", "棕榈和多层次热带植物靠近建筑", "A lush tropical garden with close broad-leaf planting, palms and layered dense foliage, screened for privacy with no implausible distant alpine or city view.", "自然景观", kinds=_HOUSE_KINDS, floors=_GROUND, preferred_property="现代花园别墅", preferred_cn_unit="独栋别墅", preferred_floor="庭院 / 首层", preferred_window="推拉露台门", preferred_site="热带花园社区"),
    _view("沙漠绿洲", "干旱地貌、耐旱植物和局部水绿", "A desert-oasis landscape with pale arid terrain, rocks and drought-tolerant planting nearby, sparse development and a restrained irrigated green area in the middle distance.", "自然景观", kinds=_HOUSE_KINDS, preferred_property="现代花园别墅", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="沙漠绿洲社区"),
    _view("雪山 / 雪林", "积雪树林、山坡和远处雪峰", "A winter mountain landscape with near snow-covered trees or ground, layered slopes in the middle distance and a restrained distant snowy ridge; keep indoor glazing and exterior scale realistic.", "自然景观", preferred_property="山地林间木屋", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="宽幅景观窗", preferred_site="山地森林"),
    _view("葡萄园 / 果园", "规则种植带和乡野远景", "A vineyard or orchard with near garden edge, orderly rows of vines or fruit trees receding through the middle ground and low countryside hills beyond.", "自然景观", kinds=_HOUSE_KINDS, preferred_property="乡村住宅 / 农舍", preferred_cn_unit="独栋别墅", preferred_floor="独栋住宅内部楼层", preferred_window="法式双开门", preferred_site="乡村村落"),
    _view("树木遮挡的局部景观", "树叶和枝干过滤远处内容", "A partially screened view where nearby branches and foliage obscure much of the exterior, allowing only restrained glimpses of neighboring landscape beyond.", "受限与弱化景观", preferred_property="普通独立住宅", preferred_floor="独栋住宅内部楼层", preferred_window="普通窗", preferred_site="成熟城市住宅区"),
    _view("近距离邻楼立面", "邻近建筑占据大部分窗景", "A realistic close neighboring facade occupying most of the view, with windows, masonry or cladding at a plausible distance and only a narrow slice of sky.", "受限与弱化景观", kinds=_APT_KINDS, floors=("低层 2–5F", "中层 6–15F"), preferred_property="标准城市公寓", preferred_floor="低层 2–5F", preferred_window="普通窗", preferred_site="城市核心高密区"),
    _view("内院 / 采光井", "围合建筑中的小尺度采光空间", "A compact internal courtyard or lightwell with close surrounding walls, limited sky and restrained planting or paving; no distant panorama.", "受限与弱化景观", kinds=("apartment", "loft", "courtyard"), floors=("庭院 / 首层", "低层 2–5F", "中层 6–15F"), preferred_property="历史街区公寓", preferred_floor="低层 2–5F", preferred_window="普通窗", preferred_site="历史街区"),
    _view("纱帘 / 磨砂玻璃弱化景观", "只保留透光和模糊色块", "The exterior is intentionally obscured by sheer curtains or translucent glazing: show soft daylight and indistinct muted shapes only, with no sharp landmark or detailed landscape.", "受限与弱化景观", preferred_property="标准城市公寓", preferred_floor="中层 6–15F", preferred_window="不强调窗 / 弱化窗景", preferred_site="国际中性住宅区"),
    _view("无明显窗外景观", "构图不展示或不强调室外", "No prominent outdoor view: windows are absent, cropped, screened or visually secondary, and the model must not invent a dominant skyline, garden, sea or mountain panorama.", "受限与弱化景观", preferred_property="标准城市公寓", preferred_floor="中层 6–15F", preferred_window="不强调窗 / 弱化窗景", preferred_site="国际中性住宅区"),
]


PRESETS = [
    ("核心城区高层公寓", "海外", "高密城市核心、落地窗和天际线", dict(property_type="核心城区高层公寓", site_context="城市核心高密区", floor_level="高层 16–30F", room_scale="标准", room_layout="开放一体布局", window_type="整墙落地窗", view="高层城市天际线")),
    ("城市景观豪华大平层", "海外", "宽阔高级公寓和城市远景", dict(property_type="豪华大平层", site_context="城市核心高密区", floor_level="高层 16–30F", room_scale="宽敞", room_layout="横向宽厅", window_type="整墙落地窗", view="城市地标远景")),
    ("顶层公寓", "海外", "超高层、露台和长距离城市景观", dict(property_type="顶层公寓", site_context="城市核心高密区", floor_level="超高层 31F+", room_scale="宽敞", room_layout="开放一体布局", window_type="推拉露台门", view="高层露台城市景观")),
    ("成熟街区标准公寓", "海外", "生活化街区中的真实城市公寓", dict(property_type="标准城市公寓", site_context="成熟城市住宅区", floor_level="低层 2–5F", room_scale="标准", room_layout="方正布局", window_type="普通窗", view="近距离城市街道")),
    ("历史街区公寓", "海外", "老建筑、传统窗型和街区立面", dict(property_type="历史街区公寓", site_context="历史街区", floor_level="低层 2–5F", room_scale="标准", room_layout="纵深布局", window_type="普通窗", view="历史街区建筑立面")),
    ("都市 Loft / 仓库改造", "海外", "挑高开放空间和工业大窗", dict(property_type="Loft / 仓库改造住宅", site_context="成熟城市住宅区", floor_level="低层 2–5F", room_scale="宏大 / 挑高", room_layout="开放一体布局", window_type="宽幅景观窗", view="近距离城市街道")),
    ("历史联排住宅", "海外", "狭长多层住宅和后院联系", dict(property_type="联排住宅", site_context="历史街区", floor_level="独栋住宅内部楼层", room_scale="标准", room_layout="纵深布局", window_type="法式双开门", view="木平台 / 庭院露台")),
    ("低密郊区独立住宅", "海外", "成熟绿化、正常尺度和家庭后院", dict(property_type="普通独立住宅", site_context="郊区家庭社区", floor_level="独栋住宅内部楼层", room_scale="标准", room_layout="方正布局", window_type="宽幅景观窗", view="绿树成荫的住宅街道")),
    ("现代花园别墅", "海外", "宽敞现代空间与私家庭院", dict(property_type="现代花园别墅", site_context="高端低密社区", floor_level="庭院 / 首层", room_scale="宽敞", room_layout="开放一体布局", window_type="推拉露台门", view="层次丰富的花园庭院")),
    ("海滨住宅", "海外", "低密海岸地段和开阔海景", dict(property_type="海滨住宅", site_context="滨海度假区", floor_level="独栋住宅内部楼层", room_scale="宽敞", room_layout="横向宽厅", window_type="整墙落地窗", view="开阔海平线")),
    ("湖畔 / 河岸住宅", "海外", "滨水住宅和自然岸线", dict(property_type="湖畔 / 河岸住宅", site_context="河湖滨水区", floor_level="独栋住宅内部楼层", room_scale="宽敞", room_layout="横向宽厅", window_type="宽幅景观窗", view="宁静湖景")),
    ("山地林间木屋", "海外", "木构住宅、森林和山脊", dict(property_type="山地林间木屋", site_context="山地森林", floor_level="独栋住宅内部楼层", room_scale="标准", room_layout="方正布局", window_type="宽幅景观窗", view="森林树海")),
    ("乡村住宅 / 农舍", "海外", "真实乡野住宅和起伏田园", dict(property_type="乡村住宅 / 农舍", site_context="乡村村落", floor_level="独栋住宅内部楼层", room_scale="标准", room_layout="纵深布局", window_type="法式双开门", view="起伏乡野")),
    ("地中海庭院住宅", "海外", "围合内院和矿物质户外材料", dict(property_type="庭院住宅", site_context="高端低密社区", floor_level="庭院 / 首层", room_scale="标准", room_layout="转角 / L 型布局", window_type="法式双开门", view="围合式地中海庭院")),
    ("一线城市核心区高层改善", "国内", "核心城区改善公寓和城市远景", dict(cn_tier="💎 高端改善", cn_unit_type="改善三房 (100-130㎡)", site_context="城市核心高密区", floor_level="高层 16–30F", room_scale="宽敞", room_layout="横向宽厅", window_type="整墙落地窗", cn_view="高层城市天际线")),
    ("滨水大平层", "国内", "高端大平层和城市滨水景观", dict(cn_tier="🏙️ 顶豪 / 超豪华", cn_unit_type="改善大平层 (160-220㎡)", site_context="河湖滨水区", floor_level="高层 16–30F", room_scale="宽敞", room_layout="横向宽厅", window_type="整墙落地窗", cn_view="城市河景")),
    ("成熟高端社区改善住宅", "国内", "成熟绿化社区中的改善户型", dict(cn_tier="💎 高端改善", cn_unit_type="四房两厅 (130-160㎡)", site_context="成熟城市住宅区", floor_level="中层 6–15F", room_scale="宽敞", room_layout="横向宽厅", window_type="宽幅景观窗", cn_view="带绿植的社区内院")),
    ("城市近郊联排 / 叠墅", "国内", "低密社区、首层花园和多层住宅", dict(cn_tier="💎 高端改善", cn_unit_type="叠墅", site_context="高端低密社区", floor_level="庭院 / 首层", room_scale="宽敞", room_layout="挑高 / 复式布局", window_type="推拉露台门", cn_view="低密别墅花园群")),
    ("独栋花园别墅", "国内", "私密大地块和现代庭院", dict(cn_tier="🏙️ 顶豪 / 超豪华", cn_unit_type="独栋别墅", site_context="私密庄园社区", floor_level="庭院 / 首层", room_scale="宏大 / 挑高", room_layout="开放一体布局", window_type="推拉露台门", cn_view="层次丰富的花园庭院")),
    ("普通成熟社区刚需公寓", "国内", "真实紧凑户型和成熟社区环境", dict(cn_tier="🏠 刚需标准", cn_unit_type="刚需两房 (60-85㎡)", site_context="成熟城市住宅区", floor_level="中层 6–15F", room_scale="紧凑", room_layout="纵深布局", window_type="普通窗", cn_view="近距离邻楼立面")),
]


PROPERTY_BY_VALUE = {item["value"]: item for item in PROPERTY_OPTIONS}
ROOM_BY_VALUE = {item["value"]: item for item in ROOM_OPTIONS}
SITE_BY_VALUE = {item["value"]: item for item in SITE_CONTEXT_OPTIONS}
FLOOR_BY_VALUE = {item["value"]: item for item in FLOOR_LEVEL_OPTIONS}
SCALE_BY_VALUE = {item["value"]: item for item in ROOM_SCALE_OPTIONS}
LAYOUT_BY_VALUE = {item["value"]: item for item in ROOM_LAYOUT_OPTIONS}
WINDOW_BY_VALUE = {item["value"]: item for item in WINDOW_TYPE_OPTIONS}
VIEW_BY_VALUE = {item["value"]: item for item in VIEW_OPTIONS}
PRESET_BY_VALUE = {name: {"value": name, "label": name, "market": market, "description": desc, "defaults": defaults} for name, market, desc, defaults in PRESETS}


LEGACY_VIEW_ALIASES = {
    "自然通透景观": "树木遮挡的局部景观",
    "带修剪整齐草坪的私家后院": "修剪整齐的私家草坪后院",
    "带泳池的阳光后院": "带泳池的阳光后院",
    "充满园艺绿植的私家小院": "层次丰富的花园庭院",
    "宁静干净的现代社区街道": "绿树成荫的住宅街道",
    "自然绿植与树木": "树木遮挡的局部景观",
    "无明显窗外景观": "无明显窗外景观",
}


def _public_option(item: Mapping[str, Any]) -> dict:
    return {
        "value": item["value"],
        "label": item["label"],
        "description": item["description"],
        "markets": list(item.get("markets") or []),
        "compatibility": dict(item.get("compatibility") or {}),
    }


def scene_catalog() -> dict:
    groups: dict[str, list[dict]] = {}
    for item in VIEW_OPTIONS:
        group = item["compatibility"]["group"]
        groups.setdefault(group, []).append(_public_option(item))
    presets = [
        {"value": CUSTOM_PRESET, "label": CUSTOM_PRESET, "market": "all", "description": "保留当前高级项的自定义组合", "defaults": {}},
        *[dict(PRESET_BY_VALUE[name]) for name, *_ in PRESETS],
    ]
    return {
        "version": SCENE_CATALOG_VERSION,
        "presets": presets,
        "property_options": [_public_option(x) for x in PROPERTY_OPTIONS],
        "room_options": [_public_option(x) for x in ROOM_OPTIONS],
        "site_contexts": [_public_option(x) for x in SITE_CONTEXT_OPTIONS],
        "floor_levels": [_public_option(x) for x in FLOOR_LEVEL_OPTIONS],
        "room_scales": [_public_option(x) for x in ROOM_SCALE_OPTIONS],
        "room_layouts": [_public_option(x) for x in ROOM_LAYOUT_OPTIONS],
        "window_types": [_public_option(x) for x in WINDOW_TYPE_OPTIONS],
        "view_options": [{"group": group, "options": options} for group, options in groups.items()],
        "compatibility_rules": {
            "policy": "latest-selection-wins",
            "custom_preset": CUSTOM_PRESET,
            "legacy_preset": LEGACY_PRESET,
            "scene_fields": ["property_type", "cn_unit_type", "site_context", "floor_level", "room_scale", "room_layout", "window_type", "view", "cn_view"],
        },
    }


def _raw(values: Any) -> dict:
    if isinstance(values, Mapping):
        return dict(values)
    return {name: getattr(values, name, None) for name in (
        "cn_mode", "country", "city", "cn_city", "neighborhood", "property_type", "cn_unit_type",
        "room_type", "cn_room_type", "view", "cn_view", "scene_preset", "site_context",
        "floor_level", "room_scale", "room_layout", "window_type", "scene_notes", "scene_anchor",
    )}


def _property_kind(property_type: str, cn_unit_type: str, cn_mode: bool) -> str:
    if cn_mode:
        if any(k in (cn_unit_type or "") for k in ("别墅", "叠墅")):
            return "villa"
        if "Loft" in (cn_unit_type or ""):
            return "loft"
        return "apartment"
    return (PROPERTY_BY_VALUE.get(property_type, {}).get("compatibility") or {}).get("kind", "")


def _fallback_view(kind: str, floor_level: str, site_context: str) -> str:
    if floor_level in ("高层 16–30F", "超高层 31F+") or kind == "apartment" and site_context == "城市核心高密区":
        return "高层城市天际线"
    if site_context == "河湖滨水区":
        return "城市河景" if kind in ("apartment", "loft") else "宁静湖景"
    if site_context == "滨海度假区":
        return "开阔海平线"
    if site_context == "山地森林":
        return "森林树海"
    if kind in _HOUSE_KINDS:
        return "层次丰富的花园庭院"
    return "树木遮挡的局部景观"


_CORRECTION_LABELS = {
    "property_type": "物业", "cn_unit_type": "户型", "site_context": "地段",
    "floor_level": "楼层", "room_scale": "尺度", "room_layout": "布局",
    "window_type": "窗型", "view": "窗景", "cn_view": "窗景",
}


def _set_correction(out: dict, corrections: list[str], key: str, value: str) -> None:
    if not value or out.get(key) == value:
        return
    old = out.get(key) or "未指定"
    out[key] = value
    corrections.append(f"{_CORRECTION_LABELS.get(key, key)}：{old} → {value}")


def _preset_defaults_for_anchor(out: dict, anchor: str, cn_mode: bool) -> dict:
    """Choose deterministic dependent defaults for a newly selected scene dimension."""
    if anchor == "property_type" and not cn_mode:
        for preset in PRESET_BY_VALUE.values():
            if preset["market"] == "海外" and preset["defaults"].get("property_type") == out["property_type"]:
                return preset["defaults"]
        fallback = {
            "半独立住宅": "低密郊区独立住宅",
            "普通独立住宅": "低密郊区独立住宅",
        }.get(out["property_type"])
        return PRESET_BY_VALUE.get(fallback, {}).get("defaults", {})
    if anchor == "cn_unit_type" and cn_mode:
        for preset in PRESET_BY_VALUE.values():
            if preset["market"] == "国内" and preset["defaults"].get("cn_unit_type") == out["cn_unit_type"]:
                return preset["defaults"]
        unit = out["cn_unit_type"]
        if any(key in unit for key in ("别墅", "叠墅")):
            return PRESET_BY_VALUE["城市近郊联排 / 叠墅"]["defaults"]
        if any(key in unit for key in ("大平层", "四房", "复式", "跃层")):
            return PRESET_BY_VALUE["成熟高端社区改善住宅"]["defaults"]
        return PRESET_BY_VALUE["普通成熟社区刚需公寓"]["defaults"]
    if anchor == "site_context":
        overseas = {
            "城市核心高密区": "核心城区高层公寓", "成熟城市住宅区": "成熟街区标准公寓",
            "历史街区": "历史街区公寓", "高端低密社区": "现代花园别墅",
            "新建综合社区": "成熟街区标准公寓", "郊区家庭社区": "低密郊区独立住宅",
            "私密庄园社区": "现代花园别墅", "滨海度假区": "海滨住宅",
            "河湖滨水区": "湖畔 / 河岸住宅", "港口 / 码头区": "城市景观豪华大平层",
            "乡村村落": "乡村住宅 / 农舍", "山地森林": "山地林间木屋",
            "热带花园社区": "现代花园别墅", "沙漠绿洲社区": "现代花园别墅",
            "国际中性住宅区": "低密郊区独立住宅",
        }
        domestic = {
            "城市核心高密区": "一线城市核心区高层改善", "河湖滨水区": "滨水大平层",
            "成熟城市住宅区": "成熟高端社区改善住宅", "新建综合社区": "普通成熟社区刚需公寓",
            "高端低密社区": "城市近郊联排 / 叠墅", "私密庄园社区": "独栋花园别墅",
            "山地森林": "独栋花园别墅", "滨海度假区": "独栋花园别墅",
        }
        name = (domestic if cn_mode else overseas).get(out["site_context"])
        return PRESET_BY_VALUE.get(name, {}).get("defaults", {})
    return {}


def normalize_scene_values(values: Any, *, anchor: str | None = None) -> tuple[dict, list[str]]:
    """Return a normalized scene patch and human-readable corrections.

    Known catalog values are corrected deterministically. Unknown legacy/custom strings are preserved and
    deliberately bypass compatibility correction instead of being rejected.
    """
    src = _raw(values)
    cn_mode = bool(src.get("cn_mode"))
    anchor = anchor or str(src.get("scene_anchor") or "")
    out = {
        "scene_preset": str(src.get("scene_preset") or ""),
        "site_context": str(src.get("site_context") or ""),
        "floor_level": str(src.get("floor_level") or ""),
        "room_scale": str(src.get("room_scale") or ""),
        "room_layout": str(src.get("room_layout") or ""),
        "window_type": str(src.get("window_type") or ""),
        "property_type": str(src.get("property_type") or ""),
        "cn_unit_type": str(src.get("cn_unit_type") or ""),
        "view": str(src.get("view") or ""),
        "cn_view": str(src.get("cn_view") or ""),
        "scene_anchor": anchor,
    }
    corrections: list[str] = []

    preset = PRESET_BY_VALUE.get(out["scene_preset"])
    if preset:
        for key, value in preset["defaults"].items():
            if not out.get(key):
                out[key] = value

    # Rich defaults for legacy requests; preserve their old property/view values.
    kind = _property_kind(out["property_type"], out["cn_unit_type"], cn_mode)
    out["site_context"] = out["site_context"] or ("成熟城市住宅区" if kind in ("apartment", "loft") else "国际中性住宅区")
    out["floor_level"] = out["floor_level"] or ("中层 6–15F" if kind in ("apartment", "loft") else "独栋住宅内部楼层")
    out["room_scale"] = out["room_scale"] or "标准"
    out["room_layout"] = out["room_layout"] or "开放一体布局"
    out["window_type"] = out["window_type"] or "宽幅景观窗"

    anchor_defaults = _preset_defaults_for_anchor(out, anchor, cn_mode)
    for key, value in anchor_defaults.items():
        if key != anchor:
            _set_correction(out, corrections, key, value)

    # Floor and vertical-layout choices are explicit physical facts and may require a different
    # building type.  Keep the latest choice, then repair its dependent residence/view fields.
    kind = _property_kind(out["property_type"], out["cn_unit_type"], cn_mode)
    if anchor == "floor_level":
        if out["floor_level"] in ("高层 16–30F", "超高层 31F+") and kind in _HOUSE_KINDS:
            _set_correction(out, corrections, "cn_unit_type" if cn_mode else "property_type",
                            "改善大平层 (160-220㎡)" if cn_mode else "核心城区高层公寓")
        elif out["floor_level"] == "独栋住宅内部楼层" and kind in _APT_KINDS:
            _set_correction(out, corrections, "cn_unit_type" if cn_mode else "property_type",
                            "独栋别墅" if cn_mode else "普通独立住宅")
    if anchor == "room_layout" and out["room_layout"] == "挑高 / 复式布局":
        _set_correction(out, corrections, "cn_unit_type" if cn_mode else "property_type",
                        "复式 / 跃层" if cn_mode else "Loft / 仓库改造住宅")
        _set_correction(out, corrections, "floor_level", "低层 2–5F")
    if anchor == "window_type" and out["window_type"] in ("高侧窗 / 天窗", "不强调窗 / 弱化窗景"):
        _set_correction(out, corrections, "cn_view" if cn_mode else "view", "无明显窗外景观")

    view_key = "cn_view" if cn_mode else "view"
    selected_view = out.get(view_key) or ""
    canonical_view = LEGACY_VIEW_ALIASES.get(selected_view, selected_view)
    view_item = VIEW_BY_VALUE.get(canonical_view)
    if not view_item:
        if anchor in ("property_type", "cn_unit_type", "site_context", "floor_level", "room_scale", "room_layout", "window_type", "view", "cn_view") and out["scene_preset"] not in ("", LEGACY_PRESET):
            out["scene_preset"] = CUSTOM_PRESET
        return out, corrections

    compat = view_item["compatibility"]
    kind = _property_kind(out["property_type"], out["cn_unit_type"], cn_mode)
    allowed_kinds = compat.get("allowed_property_kinds") or []
    allowed_floors = compat.get("allowed_floor_levels") or []
    incompatible_kind = bool(allowed_kinds and kind and kind not in allowed_kinds)
    incompatible_floor = bool(allowed_floors and out["floor_level"] not in allowed_floors)
    view_is_anchor = anchor in ("view", "cn_view")

    if view_is_anchor and (incompatible_kind or incompatible_floor):
        if incompatible_kind:
            if cn_mode and compat.get("preferred_cn_unit"):
                _set_correction(out, corrections, "cn_unit_type", compat["preferred_cn_unit"])
            elif not cn_mode and compat.get("preferred_property"):
                _set_correction(out, corrections, "property_type", compat["preferred_property"])
        if incompatible_floor and compat.get("preferred_floor"):
            _set_correction(out, corrections, "floor_level", compat["preferred_floor"])
        for key, label in (("window_type", "窗型"), ("site_context", "地段")):
            preferred = compat.get("preferred_window" if key == "window_type" else "preferred_site")
            if preferred:
                _set_correction(out, corrections, key, preferred)
    elif incompatible_kind or incompatible_floor:
        replacement = _fallback_view(kind, out["floor_level"], out["site_context"])
        if replacement != canonical_view:
            _set_correction(out, corrections, view_key, replacement)

    if (
        corrections
        or anchor in ("property_type", "cn_unit_type", "site_context", "floor_level", "room_scale", "room_layout", "window_type", "view", "cn_view")
    ) and out["scene_preset"] not in ("", LEGACY_PRESET):
        out["scene_preset"] = CUSTOM_PRESET
    return out, corrections


def _prompt(item_map: Mapping[str, Mapping[str, Any]], value: str, fallback: str) -> str:
    item = item_map.get(value)
    return str(item.get("prompt")) if item else fallback


@dataclass(frozen=True)
class CompiledScene:
    normalized: dict
    corrections: tuple[str, ...]
    summary: str
    block: str
    sd_positive: str
    sd_negative: str


def compile_scene_context(
    values: Any,
    *,
    location_text: str = "",
    property_noun: str = "",
    room_noun: str = "",
) -> CompiledScene:
    src = _raw(values)
    normalized, corrections = normalize_scene_values(src)
    cn_mode = bool(src.get("cn_mode"))
    room_value = str(src.get("cn_room_type") if cn_mode else src.get("room_type") or "")
    property_value = str(normalized.get("cn_unit_type") if cn_mode else normalized.get("property_type") or "")
    view_value = str(normalized.get("cn_view") if cn_mode else normalized.get("view") or "")
    canonical_view = LEGACY_VIEW_ALIASES.get(view_value, view_value)

    if cn_mode:
        residence = property_noun or f"Chinese residential unit: {property_value or 'credible urban residence'}"
    else:
        residence = _prompt(PROPERTY_BY_VALUE, property_value, property_noun or property_value or "credible international residence")
    room = _prompt(ROOM_BY_VALUE, room_value, room_noun or room_value or "believable residential room")
    site = _prompt(SITE_BY_VALUE, normalized["site_context"], normalized["site_context"])
    floor = _prompt(FLOOR_BY_VALUE, normalized["floor_level"], normalized["floor_level"])
    scale = _prompt(SCALE_BY_VALUE, normalized["room_scale"], normalized["room_scale"])
    layout = _prompt(LAYOUT_BY_VALUE, normalized["room_layout"], normalized["room_layout"])
    window = _prompt(WINDOW_BY_VALUE, normalized["window_type"], normalized["window_type"])
    view = _prompt(VIEW_BY_VALUE, canonical_view, view_value or "No prominent outdoor view.")
    notes = " ".join(str(src.get("scene_notes") or "").split())
    loc = location_text or str(src.get("cn_city") if cn_mode else src.get("city") or src.get("country") or "selected location")

    note_line = f"\n**Additional scene facts**: {notes} These facts may add detail but MUST NOT override the structured residence, floor, room, window or view identity above." if notes else ""
    block = f"""**[SCENE IDENTITY — MANDATORY]**
**Residence**: {residence}
**Location and site**: {loc}. {site}

**[ROOM PROGRAM & GEOMETRY]**
**Room**: {room}
**Scale**: {scale}
**Layout**: {layout}

**[WINDOW & OUTDOOR VIEW]**
**Floor relationship**: {floor}
**Window / opening**: {window}
**View outside**: {view}{note_line}

**[SPATIAL CONSISTENCY — NON-NEGOTIABLE]**
The exterior exists ONLY beyond physically modeled windows or glazed doors — never printed on a wall, never floating inside the room. Match the horizon height, downward viewing angle, object scale, neighboring-building distance and privacy level to the stated floor and site. Keep window mullions, glass thickness, reflections, exterior perspective and the room's vanishing points mutually consistent. The selected lighting controls time, weather impression and color temperature; do not invent a second conflicting outdoor lighting condition.

**[SCENE EXCLUSIONS]**
No contradictory property scale, no impossible ground garden at an elevated apartment, no aerial/drone viewpoint from an interior camera, no unrelated skyline or landscape outside the selected view, and no furniture program belonging to a different room type."""

    sd_positive = " ".join((
        f"Residence: {residence}", f"Location: {loc}; {site}", f"Room: {room}",
        f"Scale and layout: {scale} {layout}", f"Floor and window: {floor} {window}",
        f"Exterior view: {view}",
        "The exterior is visible only through physically modeled glazing, with horizon, perspective and scale matching the floor level.",
        notes,
    )).strip()
    sd_negative = "impossible floor-to-view relationship, ground garden outside a high-rise window, aerial drone view, exterior landscape painted on an interior wall, conflicting skyline, wrong room furniture program"
    summary = " · ".join(filter(None, (
        normalized.get("scene_preset") if normalized.get("scene_preset") not in (CUSTOM_PRESET, LEGACY_PRESET) else "",
        property_value, normalized.get("site_context"), normalized.get("floor_level"),
        normalized.get("room_scale"), normalized.get("room_layout"), normalized.get("window_type"), view_value,
    )))
    return CompiledScene(normalized, tuple(corrections), summary, block, sd_positive, sd_negative)


def option_values(options: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(item["value"]) for item in options]


__all__ = [
    "SCENE_CATALOG_VERSION", "CUSTOM_PRESET", "LEGACY_PRESET", "PROPERTY_OPTIONS", "ROOM_OPTIONS",
    "SITE_CONTEXT_OPTIONS", "FLOOR_LEVEL_OPTIONS", "ROOM_SCALE_OPTIONS", "ROOM_LAYOUT_OPTIONS",
    "WINDOW_TYPE_OPTIONS", "VIEW_OPTIONS", "PRESETS", "PROPERTY_BY_VALUE", "ROOM_BY_VALUE",
    "VIEW_BY_VALUE", "scene_catalog", "normalize_scene_values", "compile_scene_context", "option_values",
    "CompiledScene",
]
