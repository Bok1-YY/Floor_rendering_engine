"""Structured scene catalog, normalization and cross-model prompt contracts."""
from Floor_engine_server.models import TaskParams, task_params_to_kwargs
from Floor_engine_server.prompt_data import PROPERTY_TYPES, ROOM_TYPES, VIEWS
from Floor_engine_server.prompts import save_task_files_html
from Floor_engine_server.routes_config import options
from Floor_engine_server.scene_context import (
    PRESETS,
    PROPERTY_BY_VALUE,
    ROOM_BY_VALUE,
    SCENE_CATALOG_VERSION,
    VIEW_BY_VALUE,
    compile_scene_context,
    normalize_scene_values,
    scene_catalog,
)
from Floor_engine_server.sd_prompts import compile_sd35_prompt


def _params(**overrides):
    base = dict(
        workflow_mode="纯效果图 (生成全新空间)",
        model_choice="Pro",
        image_path="unused.png",
        country="英国",
        city="伦敦",
        property_type="核心城区高层公寓",
        room_type="客餐厅一体",
        scene_preset="核心城区高层公寓",
        site_context="城市核心高密区",
        floor_level="高层 16–30F",
        room_scale="标准",
        room_layout="开放一体布局",
        window_type="整墙落地窗",
        view="高层城市天际线",
    )
    base.update(overrides)
    return TaskParams(**base)


def test_catalog_has_expected_breadth_and_local_prompt_text():
    catalog = scene_catalog()
    assert catalog["version"] == SCENE_CATALOG_VERSION
    assert len(PROPERTY_TYPES) == 15
    assert len(ROOM_TYPES) == 30
    assert len(VIEWS) == 36
    assert len(PRESETS) == 20
    assert len(catalog["view_options"]) == 5
    for collection in (PROPERTY_BY_VALUE, ROOM_BY_VALUE, VIEW_BY_VALUE):
        assert len(collection) == len(set(collection))
        assert all(item["prompt"].isascii() for item in collection.values())
        assert all(item["noun"].isascii() for item in collection.values())


def test_every_preset_references_existing_scene_values():
    catalog = scene_catalog()
    valid = {
        "property_type": {item["value"] for item in catalog["property_options"]},
        "site_context": {item["value"] for item in catalog["site_contexts"]},
        "floor_level": {item["value"] for item in catalog["floor_levels"]},
        "room_scale": {item["value"] for item in catalog["room_scales"]},
        "room_layout": {item["value"] for item in catalog["room_layouts"]},
        "window_type": {item["value"] for item in catalog["window_types"]},
        "view": set(VIEWS),
        "cn_view": set(VIEWS),
    }
    for name, _market, _description, defaults in PRESETS:
        assert name
        for key, value in defaults.items():
            if key in valid:
                assert value in valid[key], f"{name}: invalid {key}={value}"


def test_all_presets_compile_complete_offline_scene_matrix():
    for name, market, _description, defaults in PRESETS:
        params = _params(
            **defaults,
            cn_mode=market == "国内",
            scene_preset=name,
            scene_anchor="scene_preset",
        )
        compiled = compile_scene_context(params, location_text="Selected location")
        for heading in (
            "[SCENE IDENTITY", "[ROOM PROGRAM & GEOMETRY]", "[WINDOW & OUTDOOR VIEW]",
            "[SPATIAL CONSISTENCY", "[SCENE EXCLUSIONS]",
        ):
            assert heading in compiled.block, f"{name}: missing {heading}"
        assert compiled.summary
        sd = compile_sd35_prompt(params)
        assert "Exterior view:" in sd.positive, name
        assert "Floor and window:" in sd.positive, name


def test_latest_view_wins_and_repairs_highrise_backyard_conflict():
    patch, corrections = normalize_scene_values(
        _params(view="修剪整齐的私家草坪后院", scene_anchor="view"), anchor="view"
    )
    assert patch["view"] == "修剪整齐的私家草坪后院"
    assert patch["property_type"] == "普通独立住宅"
    assert patch["floor_level"] == "庭院 / 首层"
    assert patch["window_type"] == "推拉露台门"
    assert patch["site_context"] == "郊区家庭社区"
    assert len(corrections) >= 4


def test_latest_property_wins_and_replaces_incompatible_backyard():
    patch, corrections = normalize_scene_values(
        _params(view="修剪整齐的私家草坪后院", scene_anchor="property_type"),
        anchor="property_type",
    )
    assert patch["property_type"] == "核心城区高层公寓"
    assert patch["view"] == "高层城市天际线"
    assert corrections == ["窗景：修剪整齐的私家草坪后院 → 高层城市天际线"]


def test_selecting_property_applies_its_coherent_dependent_defaults():
    patch, corrections = normalize_scene_values(
        _params(
            property_type="现代花园别墅",
            floor_level="庭院 / 首层",
            site_context="高端低密社区",
            room_scale="宽敞",
            window_type="推拉露台门",
            view="层次丰富的花园庭院",
            scene_anchor="property_type",
        ),
        anchor="property_type",
    )
    # The chosen villa wins and its exact preset is already coherent, so nothing is changed.
    assert patch["property_type"] == "现代花园别墅"
    assert patch["floor_level"] == "庭院 / 首层"
    assert patch["view"] == "层次丰富的花园庭院"
    assert corrections == []

    switched, corrections = normalize_scene_values(
        {**patch, "property_type": "核心城区高层公寓", "scene_anchor": "property_type"},
        anchor="property_type",
    )
    assert switched["property_type"] == "核心城区高层公寓"
    assert switched["floor_level"] == "高层 16–30F"
    assert switched["window_type"] == "整墙落地窗"
    assert switched["view"] == "高层城市天际线"
    assert any(item.startswith("楼层：") for item in corrections)


def test_selecting_mountain_site_changes_only_scene_dependents():
    patch, corrections = normalize_scene_values(
        _params(site_context="山地森林", scene_anchor="site_context"), anchor="site_context"
    )
    assert patch["site_context"] == "山地森林"
    assert patch["property_type"] == "山地林间木屋"
    assert patch["floor_level"] == "独栋住宅内部楼层"
    assert patch["view"] == "森林树海"
    assert corrections


def test_unknown_legacy_scene_values_are_preserved():
    patch, corrections = normalize_scene_values(
        _params(property_type="用户自定义树屋", view="用户自定义峡谷", scene_anchor="view")
    )
    assert patch["property_type"] == "用户自定义树屋"
    assert patch["view"] == "用户自定义峡谷"
    assert corrections == []


def test_compiled_scene_is_structured_and_notes_appear_once():
    compiled = compile_scene_context(
        _params(scene_notes="窗外树冠遮挡约三分之一，不出现地标建筑"),
        location_text="London, United Kingdom",
        room_noun="living-dining room",
    )
    assert "[SCENE IDENTITY" in compiled.block
    assert "[ROOM PROGRAM & GEOMETRY]" in compiled.block
    assert "[WINDOW & OUTDOOR VIEW]" in compiled.block
    assert "[SPATIAL CONSISTENCY" in compiled.block
    assert compiled.block.count("窗外树冠遮挡约三分之一") == 1
    assert "high-rise urban apartment" in compiled.block
    assert "high-floor city skyline" in compiled.block


def test_cn_sd_prompt_uses_cn_view_not_overseas_view():
    params = _params(
        cn_mode=True,
        cn_city="上海",
        cn_unit_type="改善大平层 (160-220㎡)",
        cn_room_type="横厅",
        cn_view="城市河景",
        view="森林树海",
        site_context="河湖滨水区",
        scene_anchor="cn_view",
    )
    bundle = compile_sd35_prompt(params)
    assert "city river view" in bundle.positive
    assert "layered forest view" not in bundle.positive
    assert bundle.compiler_version == "sd35-v2"


def test_parameterized_new_scene_workflows_share_scene_block(swatch_image):
    for workflow, extra in (
        ("纯效果图 (生成全新空间)", {}),
        ("宠物友好 (动物独处/主宠互动)", {"pet_type": "金毛犬"}),
        ("参照模式 (风格参照图生新图)", {"style_analysis_text": "Warm restrained interior."}),
        ("墙板模式 (护墙板/木饰面：再设计/替换/原创)", {"panel_submode": "纯原创"}),
    ):
        params = _params(workflow_mode=workflow, image_path=swatch_image, **extra)
        result = save_task_files_html(**task_params_to_kwargs(params))
        assert "[SCENE IDENTITY" in result[2]
        assert "high-rise urban apartment" in result[2]
        assert "high-floor city skyline" in result[2]


def test_preservation_and_ai_authored_workflows_do_not_receive_scene_block(swatch_image):
    for workflow, extra in (
        ("地板替换 (保持原图换地板)", {}),
        ("Omakase (AI 代笔场景)", {"scene_override": "A calm original room."}),
        ("墙板模式 (护墙板/木饰面：再设计/替换/原创)", {"panel_submode": "替换"}),
    ):
        params = _params(workflow_mode=workflow, image_path=swatch_image, **extra)
        result = save_task_files_html(**task_params_to_kwargs(params))
        assert "[SCENE IDENTITY" not in result[2]


def test_options_endpoint_exposes_backward_compatible_lists_and_catalog():
    payload = options()
    assert payload["room_types"] == ROOM_TYPES
    assert payload["property_types"] == PROPERTY_TYPES
    assert payload["views"] == VIEWS
    assert payload["scene_catalog"]["version"] == SCENE_CATALOG_VERSION
