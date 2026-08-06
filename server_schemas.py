# -*- coding: utf-8 -*-
"""HTTP 请求/响应 Pydantic 模型 —— 全部端点的请求契约集中于此。

原先散落在 server_api.py 各处;字段与校验逐字未动。前端(web/)按这些契约发请求,
改字段前先确认前端同步。
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ── 任务提交/预览/二改 ──────────────────────────────────────
class GenParams(BaseModel):
    """镜像 models.TaskParams（除 image_path / style_analysis_text 由服务端填）。"""
    model_config = {'protected_namespaces': ()}   # 允许 model_choice 等 model_* 字段（避开 pydantic v2 保留命名空间）

    workflow_mode: str
    model_choice: str = 'Pro'
    continent: str = '欧洲'
    country: str = ''
    city: str = ''
    neighborhood: str = ''
    property_type: str = '现代别墅'
    room_type: str = '客餐厅一体'
    view: str = '自然通透景观'
    style_type: str = ''
    lighting: str = ''
    floor_tone: str = ''
    floor_size: str = ''
    seam_type: str = '无缝拼接 (SPC/LVT专用)'
    glossiness: str = '哑光 (3-5°)'
    angle: str = '28mm lens (Wide)'
    aspect_ratio: str = '4:3'
    resolution: str = '4K'
    avoid_items: List[str] = []
    custom_addition: str = ''
    pet_type: str = ''
    pet_action: str = ''
    pet_focus: str = ''
    market_furniture: str = ''
    last_image_path: str = ''
    cn_mode: bool = False
    cn_developer: str = '── 不指定 ──'
    cn_city: str = '上海'
    cn_tier: str = '── 不指定 ──'
    cn_unit_type: str = '── 不指定 ──'
    cn_delivery: str = '🏆 样板间 / 展示单位'
    cn_room_type: str = '客餐厅一体'
    cn_view: str = '自然通透景观'
    cn_space_features: Optional[List[str]] = None
    cn_facilities: Optional[List[str]] = None
    style_ref_correction: str = ''
    scene_override: str = ''   # Omakase 模式：AI 原创场景散文，接管整个场景层(仅 Omakase 工作流生效)
    panel_submode: str = '再设计'   # 墙板模式子行为：再设计 / 替换 / 纯原创(仅墙板模式生效)
    panel_size: str = ''            # 墙板尺寸/板型(预设或自定义；仅墙板再设计/纯原创生效)


class SDOptions(BaseModel):
    seed: Optional[int] = Field(default=None, ge=0)
    steps: int = Field(default=28, ge=10, le=50)
    guidance_scale: float = Field(default=3.5, ge=1.0, le=10.0)
    reference_strength: float = Field(default=0.5, ge=0.1, le=1.0)
    positive_addition: str = Field(default='', max_length=1000)
    negative_addition: str = Field(default='', max_length=1000)


class JobSubmitRequest(BaseModel):
    model_config = {'protected_namespaces': ()}   # 允许 model_filter

    image_path: str                       # /api/uploads/floor 返回的绝对路径
    model_filter: Literal['b2', 'pro', 'both'] = 'both'
    model_targets: Optional[List[Literal['b2', 'pro', 'sd35']]] = None
    sd_options: SDOptions = Field(default_factory=SDOptions)
    api_key: str = ''                     # 缺省回退 engine_config.json 里的 gemini_api_key
    room_path: Optional[str] = None       # 房间替换图（地板替换流程）
    ref_path: Optional[str] = None        # 参照模式参考图
    params: GenParams


class FreeJobSubmitRequest(BaseModel):
    """自由创作任务：用户提示词原样透传，图片按列表顺序交给模型。"""
    model_config = {'protected_namespaces': ()}

    prompt: str = Field(min_length=1, max_length=10_000)
    image_paths: List[str] = Field(min_length=1, max_length=3)
    model_targets: List[Literal['b2', 'pro']] = Field(default_factory=lambda: ['b2', 'pro'])
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    resolution: Literal['2K', '4K'] = '4K'
    api_key: str = ''

    @field_validator('prompt')
    @classmethod
    def prompt_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('自由提示词不能为空')
        return value


class PreviewRequest(BaseModel):
    """快速预览（NB2 Lite · 1K）：字段同 JobSubmit 但无 model_filter（预览恒用 Lite）。"""
    model_config = {'protected_namespaces': ()}

    image_path: str
    api_key: str = ''
    room_path: Optional[str] = None
    ref_path: Optional[str] = None
    params: GenParams


class EditRequest(BaseModel):
    model_config = {'protected_namespaces': ()}

    instruction: str = Field(min_length=1, max_length=2000)
    api_key: str = ''
    image_size: Literal['2K', '4K'] = '4K'
    preserve_floor_geometry: bool = True
    model_choice: Literal['Nano Banana 2', 'Nano Banana Pro'] = 'Nano Banana Pro'
    # 保持原图色彩（防偏色）：二改后把整体色温/饱和度拉回原图。默认开=对齐旧 NiceGUI 语义
    color_match: bool = True


class RevealRequest(BaseModel):
    json_path: str
    record_id: str
    password: str


class ErrRequest(BaseModel):
    err: str


class ConfigPatch(BaseModel):
    gemini_api_key: Optional[str] = Field(default=None, max_length=500)
    fal_api_key: Optional[str] = Field(default=None, max_length=500)
    image_provider: Optional[Literal['google', 'fal']] = None
    speed_profile: Optional[Literal['fast', 'resilient']] = None
    auto_failover: Optional[bool] = None
    proxy: Optional[str] = Field(default=None, max_length=1000)
    fal_queue_proxy: Optional[str] = Field(default=None, max_length=1000)
    tls_verify: Optional[bool] = None
    tls_ca_bundle: Optional[str] = Field(default=None, max_length=2000)
    max_concurrent_per_model: Optional[int] = Field(default=None, ge=1, le=8)
    sd_enabled: Optional[bool] = None
    # 生成式修补引擎：fal=云 API（remove/add 分模型）；comfyui=用户自备 ComfyUI 实例
    inpaint_provider: Optional[Literal['fal', 'comfyui']] = None
    inpaint_remove_model: Optional[Literal['bria-eraser', 'finegrain-eraser', 'lama',
                                           'flux-fill', 'gemini-mark']] = None
    inpaint_add_model: Optional[Literal['flux-fill', 'qwen-inpaint', 'gemini-mark']] = None
    comfyui_base_url: Optional[str] = Field(default=None, max_length=500)
    comfyui_workflow_path: Optional[str] = Field(default=None, max_length=1000)
    comfyui_timeout: Optional[int] = Field(default=None, ge=60, le=3600)
    inpaint_remove_prompt: Optional[str] = Field(default=None, max_length=1000)
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: Optional[str] = None
    deepseek_model: Optional[str] = None
    omakase_gemini_model: Optional[str] = Field(default=None, max_length=200)
    omakase_enabled: Optional[bool] = None
    # 成本估算单价：{'B2': 0.1, 'Pro': 0.5, 'B2:fal': 0.12,...}（元/张成功图）；负数拒绝
    usage_prices: Optional[dict] = None
    # PPTX 导出品牌（logo 走 POST /api/uploads/logo，不在此 patch）
    pptx_company: Optional[str] = Field(default=None, max_length=200)
    pptx_contact: Optional[str] = Field(default=None, max_length=200)


# ── 自定义配方 ──────────────────────────────────────────
class CustomRecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    params: dict


class CustomRecipeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=40)
    params: Optional[dict] = None



# ── Omakase 场景生成 ─────────────────────────────────────
class OmakaseRequest(BaseModel):
    idea: str = Field(default='', max_length=2000)



# ── 记录操作 ─────────────────────────────────────────────
class ResultRef(BaseModel):
    json_path: str
    record_id: str
    result_id: str = Field(min_length=1, max_length=80)


class ResultReviewRequest(ResultRef):
    review_status: str = 'unreviewed'
    review_tags: List[str] = Field(default_factory=list, max_length=20)
    review_note: str = Field(default='', max_length=2000)
    best: bool = False


class RecordRef(BaseModel):
    json_path: str
    record_id: str


class RecordEditRequest(BaseModel):
    model_config = {'protected_namespaces': ()}
    json_path: str
    record_id: str
    result_id: str = Field(min_length=1, max_length=80)
    instruction: str = Field(min_length=1, max_length=2000)
    api_key: str = ''
    image_size: Literal['2K', '4K'] = '4K'
    preserve_floor_geometry: bool = True
    model_choice: Literal['Nano Banana 2', 'Nano Banana Pro'] = 'Nano Banana Pro'
    # 保持原图色彩（防偏色）：同 EditRequest.color_match
    color_match: bool = True


# ── 地板可视化 ───────────────────────────────────────────
class FloorPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class FloorVisualizeTarget(BaseModel):
    kind: Literal['job', 'record', 'room']
    jid: str = ''
    stage: Literal['b2', 'pro', 'sd35'] = 'pro'
    image_rel: str = ''
    json_path: str = ''
    record_id: str = ''
    result_id: str = Field(default='', max_length=80)
    room_path: str = ''


class FloorVisualizeRequest(BaseModel):
    target: FloorVisualizeTarget
    texture_path: str = Field(min_length=1)
    mask_b64: str = Field(min_length=8)
    calibration_quad: List[FloorPoint] = Field(min_length=4, max_length=4)
    scale: float = Field(default=1.0, ge=0.15, le=4.0)
    rotation: float = Field(default=0.0, ge=-180, le=180)
    offset_x: float = Field(default=0.0, ge=-4, le=4)
    offset_y: float = Field(default=0.0, ge=-4, le=4)
    illumination_strength: float = Field(default=0.65, ge=0, le=1.5)
    shadow_strength: float = Field(default=0.85, ge=0, le=1.5)
    feather: float = Field(default=0.008, ge=0, le=0.08)
    texture_width_mm: Optional[float] = Field(default=None, gt=0, le=100000)
    texture_height_mm: Optional[float] = Field(default=None, gt=0, le=100000)
    plank_width_mm: Optional[float] = Field(default=None, gt=0, le=5000)
    plank_length_mm: Optional[float] = Field(default=None, gt=0, le=20000)


# ── 校色 ────────────────────────────────────────────────
class ColorMatchRect(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class ColorMatchAdjustments(BaseModel):
    """以 Gemini 原图为零点的全图专业调整。"""
    temperature: float = Field(default=0, ge=-100, le=100)
    tint: float = Field(default=0, ge=-100, le=100)
    exposure: float = Field(default=0, ge=-2, le=2)
    contrast: float = Field(default=0, ge=-100, le=100)
    highlights: float = Field(default=0, ge=-100, le=100)
    shadows: float = Field(default=0, ge=-100, le=100)
    whites: float = Field(default=0, ge=-100, le=100)
    blacks: float = Field(default=0, ge=-100, le=100)
    midtones: float = Field(default=0, ge=-100, le=100)
    saturation: float = Field(default=0, ge=-100, le=100)


class ColorMatchSegmentRequest(BaseModel):
    image_rel: str = Field(min_length=1)
    positive_mask_b64: str = Field(default='', max_length=12_000_000)
    negative_mask_b64: str = Field(default='', max_length=12_000_000)
    previous_mask_b64: str = Field(default='', max_length=12_000_000)
    auto_seed: bool = True


class ColorMatchPreviewRequest(BaseModel):
    image_rel: str = Field(min_length=1)   # 成图相对 /outputs 路径
    ref_path: str = Field(min_length=1)    # 参照小样绝对路径
    rect: ColorMatchRect                 # 只用于地板统计和三区诊断
    strength: float = Field(default=0.8, ge=0, le=1)
    feather: float = Field(default=0.05, ge=0, le=0.3)  # 兼容字段，全图校色忽略
    adjustments: ColorMatchAdjustments = Field(default_factory=ColorMatchAdjustments)
    adjustment_mode: Literal['auto', 'manual'] = 'auto'
    include_analysis: bool = False
    scope: Literal['global', 'floor_mask'] = 'global'
    mask_b64: str = Field(default='', max_length=12_000_000)
    mask_feather: float = Field(default=0.003, ge=0, le=0.02)



class JobColorMatchRequest(ColorMatchPreviewRequest):
    ref_path: str = ''                       # 空 → 回退本任务地板小样(job.png_path)
    stage: Literal['b2', 'pro', 'sd35'] = 'pro'



class RecordColorMatchRequest(BaseModel):
    json_path: str
    record_id: str
    result_id: str = Field(min_length=1, max_length=80)
    ref_path: str = ''                       # 空 → 回退记录同目录优化图/历史小样
    rect: ColorMatchRect                 # 只用于地板统计
    strength: float = Field(default=0.8, ge=0, le=1)
    feather: float = Field(default=0.05, ge=0, le=0.3)  # 兼容字段，全图校色忽略
    adjustments: ColorMatchAdjustments = Field(default_factory=ColorMatchAdjustments)
    adjustment_mode: Literal['auto', 'manual'] = 'auto'
    scope: Literal['global', 'floor_mask'] = 'global'
    mask_b64: str = Field(default='', max_length=12_000_000)
    mask_feather: float = Field(default=0.003, ge=0, le=0.02)



# ── 生成式修补 ───────────────────────────────────────────
class InpaintPayload(BaseModel):
    mask_b64: str = Field(min_length=1, max_length=12_000_000)  # 纯 base64 PNG，白=重绘区
    prompt: str = Field(default='', max_length=2000)
    mode: Literal['remove', 'add'] = 'remove'
    grow: Optional[int] = Field(default=None, ge=0, le=64)  # 空值：remove=8，add=0
    feather: float = Field(default=0.01, ge=0, le=0.1)   # 羽化半径 / 短边比例
    seed: Optional[int] = None
    n: int = Field(default=3, ge=1, le=3)        # 候选数（Lightroom 式抽卡；n 张记 n 次费用）



class InpaintTarget(BaseModel):
    """修补目标：job 候选 / 记录结果 / 房间图。字段按 kind 选用，校验在 _resolve_inpaint_source。"""
    kind: Literal['job', 'record', 'room']
    jid: str = ''
    stage: Literal['b2', 'pro', 'sd35'] = 'pro'
    image_rel: str = ''
    json_path: str = ''
    record_id: str = ''
    result_id: str = Field(default='', max_length=80)
    room_path: str = ''


class GenericInpaintRequest(InpaintPayload):
    target: InpaintTarget


class InpaintSegmentPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class InpaintSegmentRequest(BaseModel):
    target: InpaintTarget
    strategy: Literal['scan_objects', 'point']
    point: Optional[InpaintSegmentPoint] = None


class InpaintApplyRequest(BaseModel):
    index: int = Field(ge=0, le=2)
