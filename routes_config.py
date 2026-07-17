# -*- coding: utf-8 -*-
"""配置/选项路由 —— 配方、失败知识库、连通性、引擎配置、Omakase 场景、模型与词表选项。"""
import asyncio
import math

from fastapi import APIRouter, HTTPException

from .api import call_omakase_scenes, test_connection
from .config import (
    logger, load_config, update_config, GEMINI_MODEL_MAP, FAL_MODEL_MAP,
    get_image_provider, get_proxy,
    get_deepseek_api_key, get_deepseek_base_url, get_deepseek_model,
    get_omakase_enabled, get_omakase_gemini_model,
)
from .custom_recipes import (
    list_custom_recipes, add_custom_recipe, update_custom_recipe, delete_custom_recipe,
)
from .failure_kb import classify_failure, FAILURE_RULES
from .prompt_data import (
    ROOM_TYPES, CN_ROOM_TYPES, FLOOR_TONES, CONTINENTS, PROPERTY_TYPES,
    VIEWS, STYLES, LOCATION_MAP, PET_TYPES, PET_ACTIONS, PET_FOCUS_OPTIONS,
    LIGHTINGS, ANGLES, FLOOR_SIZES, PANEL_SIZES, MARKET_FURNITURE_CHOICES, AVOID_LIST,
    CN_DEVELOPERS, CN_UNIT_TYPES, CN_TIERS, CN_DELIVERY_CHOICES,
    CN_SPACE_FEATURES, CN_FACILITIES, CN_CITIES,
    WORKFLOW_MODES, SEAM_TYPES, GLOSSINESS, RESOLUTIONS, ASPECT_RATIOS,
)
from .recipes import recommend_recipes, FLOOR_RECIPES
from .server_helpers import config_view
from .server_schemas import ConfigPatch, CustomRecipeCreate, CustomRecipeUpdate, ErrRequest, OmakaseRequest

router = APIRouter()


# ── 配方 / 失败知识库 / 连通性 / 配置 / 模型 ──
@router.get('/api/recipes')
def recipes(tone: str = '', limit: int = 6):
    if tone:
        return recommend_recipes(tone, limit)
    return FLOOR_RECIPES[:limit] if limit else FLOOR_RECIPES


# ── 自定义配方（我的配方）：CRUD，沿项目 POST-mutation 惯例 ──

@router.get('/api/recipes/custom')
def custom_recipes_list():
    return list_custom_recipes()


@router.post('/api/recipes/custom')
def custom_recipes_add(req: CustomRecipeCreate):
    return add_custom_recipe(req.name, req.params)


@router.post('/api/recipes/custom/{rid}/update')
def custom_recipes_update(rid: str, req: CustomRecipeUpdate):
    r = update_custom_recipe(rid, name=req.name, params=req.params)
    if r is None:
        raise HTTPException(404, '配方不存在')
    return r


@router.post('/api/recipes/custom/{rid}/delete')
def custom_recipes_delete(rid: str):
    if not delete_custom_recipe(rid):
        raise HTTPException(404, '配方不存在')
    return {'ok': True}


@router.post('/api/failure/classify')
def classify(req: ErrRequest):
    return classify_failure(req.err)


@router.get('/api/connection/test')
def connection_test(gemini: str = '', fal: str = '', proxy: str = ''):
    cfg = load_config()
    g = gemini or cfg.get('gemini_api_key', '')
    f = fal or cfg.get('fal_api_key', '')
    p = proxy or get_proxy()
    return {'result': test_connection(g, f, p)}


@router.get('/api/config')
def get_config():
    return config_view()


@router.put('/api/config')
def put_config(req: ConfigPatch):
    patch = req.model_dump(exclude_none=True)
    for key in ('gemini_api_key', 'fal_api_key', 'proxy', 'fal_queue_proxy', 'tls_ca_bundle',
                'deepseek_api_key', 'deepseek_base_url', 'deepseek_model',
                'omakase_gemini_model', 'pptx_company', 'pptx_contact',
                'comfyui_base_url', 'comfyui_workflow_path', 'inpaint_remove_prompt'):
        if key in patch:
            patch[key] = str(patch[key] or '').strip()
    if 'usage_prices' in patch:
        clean = {}
        for k, v in (patch['usage_prices'] or {}).items():
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f'单价必须是数字：{k}')
            if not math.isfinite(f):
                raise HTTPException(400, f'单价必须是有限数字：{k}')
            if f < 0:
                raise HTTPException(400, f'单价不能为负：{k}')
            if str(k).strip():
                clean[str(k).strip()] = f
        patch['usage_prices'] = clean
    if not update_config(patch):
        raise HTTPException(500, '配置保存失败，请检查程序目录写权限或磁盘空间')
    return config_view()




@router.post('/api/omakase/scenes')
async def omakase_scenes(req: OmakaseRequest):
    if not get_omakase_enabled():
        raise HTTPException(400, 'Omakase 模式未启用，请先在设置里开启')
    idea = (req.idea or '').strip()
    if not idea:
        raise HTTPException(400, '请先描述你想要的画面/氛围')
    cfg = load_config()
    gemini_key = (cfg.get('gemini_api_key') or '').strip()
    deepseek_key = get_deepseek_api_key()
    if not gemini_key and not deepseek_key:
        raise HTTPException(400, '缺少 Gemini API Key，且未配置 DeepSeek 备用 Key')
    options, err, provider, fallback_used = await asyncio.to_thread(
        call_omakase_scenes, idea,
        gemini_api_key=gemini_key,
        gemini_model=get_omakase_gemini_model(),
        deepseek_api_key=deepseek_key,
        deepseek_base_url=get_deepseek_base_url(),
        deepseek_model=get_deepseek_model())
    if err:
        info = classify_failure(err)
        detail = f"{info.get('title', 'Omakase 生成失败')}：{info.get('action', '')}".rstrip('：')
        raise HTTPException(502, detail)
    notice = ''
    if fallback_used:
        notice = ('Gemini 暂不可用，已自动使用 DeepSeek 备用线路生成。'
                  if gemini_key else
                  '未配置 Gemini API Key，已使用 DeepSeek 备用线路生成。')
    return {
        'options': options,
        'provider': provider,
        'fallback_used': fallback_used,
        'notice': notice,
    }


@router.get('/api/models')
def models_endpoint():
    return {'gemini': GEMINI_MODEL_MAP, 'fal': FAL_MODEL_MAP, 'provider': get_image_provider()}


# 下拉选项：从 prompt_data 取真源，避免前端硬编码与引擎漂移（workflow/seam/gloss/res 是 UI 级常量）



@router.get('/api/options')
def options():
    """前端表单全量下拉/多选项；除少数 UI 级常量外均取自 prompt_data 真源，避免与引擎漂移。"""
    return {
        'workflow_modes': WORKFLOW_MODES,
        'model_filters': [
            {'value': 'b2', 'label': '⚡ B2'},
            {'value': 'pro', 'label': '⚡ Pro'},
            {'value': 'both', 'label': '⚡ 双模型'},
        ],
        'resolutions': RESOLUTIONS,
        'aspect_ratios': ASPECT_RATIOS,
        'seam_types': SEAM_TYPES,
        'glossiness': GLOSSINESS,
        # ── 通用 ──
        'room_types': ROOM_TYPES,
        'property_types': PROPERTY_TYPES,
        'views': VIEWS,
        'floor_tones': FLOOR_TONES,
        'styles': STYLES,
        'lightings': LIGHTINGS,
        'angles': ANGLES,
        'floor_sizes': FLOOR_SIZES,
        'panel_sizes': PANEL_SIZES,
        'market_furniture': MARKET_FURNITURE_CHOICES,
        'avoid_items': AVOID_LIST,
        # ── 地区级联：大洲→国家→城市 ──
        'continents': CONTINENTS,
        'location_map': LOCATION_MAP,
        # ── 宠物友好 ──
        'pet_types': PET_TYPES,
        'pet_actions': PET_ACTIONS,
        'pet_focus': PET_FOCUS_OPTIONS,
        # ── 国内市场 ──
        'cn_room_types': CN_ROOM_TYPES,
        'cn_developers': CN_DEVELOPERS,
        'cn_cities': CN_CITIES,
        'cn_tiers': CN_TIERS,
        'cn_unit_types': CN_UNIT_TYPES,
        'cn_delivery_choices': CN_DELIVERY_CHOICES,
        'cn_space_features': list(CN_SPACE_FEATURES.keys()),
        'cn_facilities': list(CN_FACILITIES.keys()),
    }



# ── 失败知识库：常见失败参考 ──
@router.get('/api/failure/rules')
def failure_rules():
    out = []
    for r in FAILURE_RULES:
        d = r if isinstance(r, dict) else {}
        out.append({
            'key': d.get('key', ''),
            'title': d.get('title', ''),
            'cause': d.get('cause', ''),
            'action': d.get('action', ''),
        })
    return [r for r in out if r['title']]

