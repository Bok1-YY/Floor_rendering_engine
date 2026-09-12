"""Whole-home design: schema."""








PROMPT_VERSION = "whole-home-birdseye-v1"

PLAN_PROMPT_VERSION = "whole-home-anchor-plan-v2"

PLAN_VERIFY_VERSION = "whole-home-anchor-verify-v1"

QA_PROMPT_VERSION = "whole-home-structure-qa-v1"

SUPPORTED_RATIOS: tuple[tuple[str, float], ...] = (
    ("9:16", 9 / 16), ("2:3", 2 / 3), ("3:4", 3 / 4),
    ("4:5", 4 / 5), ("1:1", 1.0), ("5:4", 5 / 4),
    ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9),
)

STRUCTURE_REVIEW_ITEMS = (
    "orientation_and_crop",
    "outer_footprint",
    "room_count_and_positions",
    "partitions_and_adjacencies",
    "entrance_balcony_and_openings",
    "kitchen_bathroom_wet_zones",
    "no_added_or_missing_spaces",
    "orthographic_topdown_view",
    "no_labels_dimensions_or_watermarks",
)

STRUCTURE_GUIDANCE_QUESTIONS = (
    {
        "id": "Q01_ANNOTATIONS", "title": "尺寸数字、虚线和说明文字",
        "prompt": "图中的尺寸数字、H/W、虚线和引线是否都只是说明，不需要建成墙？",
        "hint": "通常这些内容只用于读图。若其中确有结构，请选择不确定并在备注中说明。",
        "choices": (("annotations", "对，只是说明"), ("has_structure", "其中有真实结构"), ("unsure", "看不清")),
    },
    {
        "id": "Q02_PARALLEL_LINES", "title": "卫生间和高差区域的平行线",
        "prompt": "湿区里的多条平行线主要是地面高差/下沉边线，而不是全高墙吗？",
        "hint": "只判断它是否应该升到墙高，不需要判断施工做法。",
        "choices": (("floor_feature", "主要是地面高差"), ("wall", "其中有全高墙"), ("unsure", "看不清")),
    },
    {
        "id": "Q03_GLAZING", "title": "外墙细长双线",
        "prompt": "外墙上的细长双线大部分是窗、玻璃或阳台界面吗？",
        "hint": "有采光或可开启界面即可选择门窗/玻璃。",
        "choices": (("mostly_openings", "大部分是门窗/玻璃"), ("contains_walls", "其中有实墙"), ("unsure", "看不清")),
    },
    {
        "id": "Q04_ENTRANCE", "title": "入户门",
        "prompt": "红色人工锚点标出的入口就是本户入户门吗？",
        "hint": "只确认位置；门扇方向看不清时可保留 not_shown。",
        "choices": (("yes", "是入户门"), ("no", "位置不对"), ("unsure", "看不清")),
    },
    {
        "id": "Q05_LOW_FEATURES", "title": "低柜、电视柜和展示柜",
        "prompt": "DISPLAY、TV UNIT、LOW HEIGHT STORAGE 等标注都不是全高墙吗？",
        "hint": "这些通常属于家具或低构造，不应封堵公共空间。",
        "choices": (("not_walls", "都不是全高墙"), ("some_walls", "其中有真实墙体"), ("unsure", "看不清")),
    },
    {
        "id": "Q06_BALCONIES", "title": "阳台与室内连接",
        "prompt": "图上标出的阳台与相邻室内空间之间存在门或玻璃开口吗？",
        "hint": "只确认是否连通；栏杆和窗框细节交给后续模型。",
        "choices": (("connected", "存在门/玻璃开口"), ("closed", "是封闭实墙"), ("unsure", "看不清")),
    },
    {
        "id": "Q07_MISSING_OPENINGS", "title": "遗漏门窗",
        "prompt": "人工锚点之外，图上是否还有明显但未标记的门、窗或开放通道？",
        "hint": "有遗漏时选择有；Gemini 会列出候选，必要时进入专业校正。",
        "choices": (("none", "没有明显遗漏"), ("has_missing", "还有遗漏"), ("unsure", "看不清")),
    },
    {
        "id": "Q08_ROOM_LIST", "title": "空间清单",
        "prompt": "当前人工标记的空间名称和大致位置是否完整？",
        "hint": "储物间、生活阳台、楼梯间和卫生间也算空间。",
        "choices": (("complete", "名称和位置完整"), ("incomplete", "仍有空间缺失"), ("unsure", "不确定")),
    },
    {
        "id": "Q09_READY", "title": "允许生成研究灰模",
        "prompt": "是否允许系统按已确认比例和上述答案生成研究灰模？",
        "hint": "研究灰模不是施工图；所有默认墙高和未知项都会写进假设清单。",
        "choices": (("yes", "允许生成研究灰模"), ("no", "暂不生成"), ("unsure", "仍需专业复核")),
    },
)

def empty_plan_summary(source: str = "human") -> dict:
    return {
        "version": "plan-summary-v1",
        "room_count": 0,
        "rooms": [],
        "declared_layout": {
            "bedrooms": 0, "halls": 0, "bathrooms": 0,
            "source_text": "", "confidence": 0.0,
        },
        "declared_area_m2": 0.0,
        "overall_dimensions_mm": {
            "width": 0, "depth": 0, "evidence": [], "confidence": 0.0,
        },
        "summary_confidence": 0.0,
        "review_items": [],
        "annotation_boxes": [],
        "entrances": [],
        "openings_summary": [],
        "wet_zones": [],
        "balconies": [],
        "dimension_evidence": [],
        "must_preserve": [],
        "uncertainties": [],
        "source": source,
        "prompt_version": PLAN_PROMPT_VERSION,
        "verification": {"status": "not_run", "conflicts": [], "changes": [], "inferred_anchor_gaps": []},
    }

def empty_anchor_set(source_hash: str = "", normalized_hash: str = "") -> dict:
    return {
        "version": "floorplan-anchors-v1",
        "coordinate_space": "normalized-evidence-1000-v1",
        "source_hash": source_hash,
        "normalized_hash": normalized_hash,
        "confirmed_complete": False,
        "anchors": [],
        "updated_at": 0.0,
    }

def empty_structure_review() -> dict:
    return {
        "version": "whole-home-structure-review-v1",
        "status": "not_run",
        "questions": [],
        "answers": {},
        "scale_calibration": None,
        "seed_graph": None,
        "structure_bundle": None,
        "structure_hash": "",
        "unresolved": [],
        "provider": "",
        "error": "",
        "updated_at": 0.0,
    }

_STRUCTURE_GRAPH_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "outer_boundary": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
        }, "required": ["x", "y"]}},
        "walls": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"},
            "a": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "b": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "thickness_m": {"type": "NUMBER"}, "height_m": {"type": "NUMBER"},
            "left_space_id": {"type": "STRING"}, "right_space_id": {"type": "STRING"},
            "confidence": {"type": "NUMBER"}, "evidence": {"type": "STRING"},
        }, "required": ["id", "a", "b", "thickness_m", "height_m", "left_space_id", "right_space_id", "confidence", "evidence"]}},
        "openings": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "kind": {"type": "STRING", "enum": ["entrance", "door", "window"]},
            "a": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "b": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]},
            "owning_wall_id": {"type": "STRING"}, "sill_m": {"type": "NUMBER"}, "head_m": {"type": "NUMBER"},
            "side_a_space_id": {"type": "STRING"}, "side_b_space_id": {"type": "STRING"},
            "swing_direction": {"type": "STRING", "enum": ["hinge_left", "hinge_right", "sliding", "double", "not_shown", "none"]},
            "confidence": {"type": "NUMBER"}, "evidence": {"type": "STRING"},
        }, "required": ["id", "kind", "a", "b", "owning_wall_id", "sill_m", "head_m", "side_a_space_id", "side_b_space_id", "swing_direction", "confidence", "evidence"]}},
        "adjacencies": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "space_a_id": {"type": "STRING"}, "space_b_id": {"type": "STRING"},
            "kind": {"type": "STRING", "enum": ["door", "open_passage"]}, "opening_id": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
        }, "required": ["id", "space_a_id", "space_b_id", "kind", "opening_id", "confidence"]}},
        "unresolved": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["outer_boundary", "walls", "openings", "adjacencies", "unresolved"],
}

_PLAN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "room_count": {"type": "INTEGER"},
        "rooms": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "label": {"type": "STRING"},
            "room_type": {"type": "STRING"}, "coarse_location": {"type": "STRING"},
            "adjacent_room_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "anchor_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "source": {"type": "STRING", "enum": ["human_anchor", "gemini_inferred"]},
            "confidence": {"type": "NUMBER"}, "evidence": {"type": "STRING"},
            "needs_confirmation": {"type": "BOOLEAN"},
        }, "required": ["id", "label", "room_type", "coarse_location", "adjacent_room_ids",
                         "anchor_ids", "source", "confidence", "evidence", "needs_confirmation"]}},
        "declared_layout": {"type": "OBJECT", "properties": {
            "bedrooms": {"type": "INTEGER"}, "halls": {"type": "INTEGER"},
            "bathrooms": {"type": "INTEGER"}, "source_text": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
        }, "required": ["bedrooms", "halls", "bathrooms", "source_text", "confidence"]},
        "declared_area_m2": {"type": "NUMBER"},
        "overall_dimensions_mm": {"type": "OBJECT", "properties": {
            "width": {"type": "INTEGER"}, "depth": {"type": "INTEGER"},
            "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
            "confidence": {"type": "NUMBER"},
        }, "required": ["width", "depth", "evidence", "confidence"]},
        "summary_confidence": {"type": "NUMBER"},
        "review_items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "kind": {"type": "STRING"},
            "label": {"type": "STRING"}, "evidence": {"type": "STRING"},
            "confidence": {"type": "NUMBER"}, "status": {"type": "STRING"},
        }, "required": ["id", "kind", "label", "evidence", "confidence", "status"]}},
        "annotation_boxes": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "label": {"type": "STRING"}, "kind": {"type": "STRING"},
            "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            "confidence": {"type": "NUMBER"}, "safe_to_erase": {"type": "BOOLEAN"},
        }, "required": ["label", "kind", "box_2d", "confidence", "safe_to_erase"]}},
        "entrances": {"type": "ARRAY", "items": {"type": "STRING"}},
        "openings_summary": {"type": "ARRAY", "items": {"type": "STRING"}},
        "wet_zones": {"type": "ARRAY", "items": {"type": "STRING"}},
        "balconies": {"type": "ARRAY", "items": {"type": "STRING"}},
        "dimension_evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
        "must_preserve": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "verification": {"type": "OBJECT", "properties": {
            "status": {"type": "STRING"},
            "conflicts": {"type": "ARRAY", "items": {"type": "STRING"}},
            "changes": {"type": "ARRAY", "items": {"type": "STRING"}},
            "inferred_anchor_gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": ["status", "conflicts", "changes", "inferred_anchor_gaps"]},
    },
    "required": ["room_count", "rooms", "declared_layout", "declared_area_m2",
                 "overall_dimensions_mm", "summary_confidence", "review_items", "annotation_boxes", "entrances",
                 "openings_summary", "wet_zones", "balconies", "dimension_evidence",
                 "must_preserve", "uncertainties", "verification"],
}

_QA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hard_fail": {"type": "BOOLEAN"},
        "summary": {"type": "STRING"},
        "checks": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "check_id": {"type": "STRING"},
            "status": {"type": "STRING", "enum": ["pass", "fail", "uncertain"]},
            "evidence": {"type": "STRING"},
        }, "required": ["check_id", "status", "evidence"]}},
    },
    "required": ["hard_fail", "summary", "checks"],
}

SCHEMA_VERSION = "whole-home-design-project-v1"
