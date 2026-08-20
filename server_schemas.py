# -*- coding: utf-8 -*-
"""HTTP 请求/响应 Pydantic 模型 —— 全部端点的请求契约集中于此。

原先散落在 server_api.py 各处;字段与校验逐字未动。前端(web/)按这些契约发请求,
改字段前先确认前端同步。
"""
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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
    film_path: str = ''
    film_width_mm: Optional[float] = Field(default=None, ge=100, le=10000)
    film_repeat_length_mm: Optional[float] = Field(default=None, ge=100, le=20000)
    film_repeat_axis: Literal['long_edge'] = 'long_edge'
    film_slit_origin_mm: Optional[float] = Field(default=None, ge=0, le=10000)
    floor_coverage_min: int = Field(default=40, ge=10, le=80)
    floor_coverage_max: int = Field(default=50, ge=10, le=80)
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
    cinematic_enabled: bool = False  # 电影真实感：宠物/含生命主体 Omakase 可由前端智能开启
    panel_submode: str = '再设计'   # 墙板模式子行为：再设计 / 替换 / 纯原创(仅墙板模式生效)
    panel_size: str = ''            # 墙板尺寸/板型(预设或自定义；仅墙板再设计/纯原创生效)

    @model_validator(mode='after')
    def floor_coverage_range_is_ordered(self):
        if self.floor_coverage_min > self.floor_coverage_max:
            raise ValueError('floor_coverage_min must not exceed floor_coverage_max')
        if self.film_path and (self.film_width_mm is None or self.film_repeat_length_mm is None):
            raise ValueError('原厂彩膜需要填写彩膜宽度和纵向重复周期')
        return self


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


class PanoramaPaidPreviewRequest(BaseModel):
    """纯效果图候选 → 单点 ERP，或对已有 ERP 做唯一一次接缝修复。"""
    model_config = {'protected_namespaces': ()}

    action: Literal['generate', 'repair']
    source_model: Optional[Literal['b2', 'pro', 'sd35']] = None
    source_index: int = Field(default=0, ge=0)
    panorama_index: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode='after')
    def validate_panorama_source(self):
        if self.action == 'generate' and self.source_model is None:
            raise ValueError('generate requires source_model')
        if self.action == 'repair' and self.panorama_index is None:
            raise ValueError('repair requires panorama_index')
        return self


class PanoramaCommitRequest(BaseModel):
    preview_id: str = Field(min_length=8, max_length=120)
    preview_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


class DirectPanoramaPreviewRequest(BaseModel):
    """地板小样 + 场景参数 → B2/GPT Image 2 两张六面图集付费预览。"""
    model_config = {'protected_namespaces': ()}

    image_path: str
    room_reference_path: str = ''
    params: GenParams


class DirectPanoramaCommitRequest(BaseModel):
    preview_id: str = Field(min_length=8, max_length=120)
    preview_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


class FilmAnalyzeRequest(BaseModel):
    model_config = {'protected_namespaces': ()}

    film_path: str = Field(min_length=1, max_length=2000)
    film_width_mm: float = Field(ge=100, le=10000)
    film_repeat_length_mm: float = Field(ge=100, le=20000)
    floor_size: str = Field(min_length=1, max_length=300)
    seam_type: str = Field(default='无缝拼接 (SPC/LVT专用)', max_length=300)
    film_slit_origin_mm: Optional[float] = Field(default=None, ge=0, le=10000)


class PanoramaReviewChecklist(BaseModel):
    wrap_seam: Literal['pass', 'fail', 'uncertain']
    horizon_and_lines: Literal['pass', 'fail', 'uncertain']
    object_integrity: Literal['pass', 'fail', 'uncertain']
    floor_and_material: Literal['pass', 'fail', 'uncertain']
    lighting_continuity: Literal['pass', 'fail', 'uncertain']
    poles: Literal['pass', 'fail', 'uncertain']


class PanoramaReviewRequest(BaseModel):
    panorama_index: int = Field(ge=0)
    checklist: PanoramaReviewChecklist


class PanoramaFloorPrepareRequest(BaseModel):
    """Prepare local five-view floor masks for one ERP candidate."""
    panorama_index: int = Field(ge=0)


class PanoramaFloorViewMask(BaseModel):
    id: Literal['front', 'right', 'back', 'left', 'nadir']
    mask_b64: str = Field(min_length=8, max_length=8_000_000)


class SphericalFloorRecipeRequest(BaseModel):
    camera_height_m: float = Field(default=1.55, ge=0.5, le=2.5)
    rotation_deg: float = Field(default=90.0, ge=-180, le=180)
    scale: float = Field(default=1.0, ge=0.15, le=4.0)
    offset_x: float = Field(default=0.0, ge=-10, le=10)
    offset_z: float = Field(default=0.0, ge=-10, le=10)
    texture_width_mm: float = Field(default=1900.0, ge=50, le=50000)
    texture_height_mm: float = Field(default=1268.0, ge=50, le=50000)
    plank_width_mm: Optional[float] = Field(default=None, ge=20, le=3000)
    plank_length_mm: Optional[float] = Field(default=None, ge=100, le=10000)
    illumination_strength: float = Field(default=0.65, ge=0, le=1.5)
    shadow_strength: float = Field(default=0.85, ge=0, le=1.5)
    contact_shadow_strength: float = Field(default=0.35, ge=0, le=1.5)
    feather: float = Field(default=0.006, ge=0, le=0.08)


class PanoramaFloorRenderRequest(BaseModel):
    panorama_index: int = Field(ge=0)
    source_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    view_masks: list[PanoramaFloorViewMask] = Field(min_length=1, max_length=5)
    recipe: SphericalFloorRecipeRequest


class PanoramaFloorRecordPrepareRequest(BaseModel):
    json_path: str = Field(min_length=1, max_length=4096)
    record_id: str = Field(min_length=1, max_length=120)
    result_id: str = Field(min_length=1, max_length=120)
    texture_path: str = Field(min_length=1, max_length=4096)


class PanoramaFloorRecordRenderRequest(PanoramaFloorRecordPrepareRequest):
    source_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    view_masks: list[PanoramaFloorViewMask] = Field(min_length=1, max_length=5)
    recipe: SphericalFloorRecipeRequest


# ── 户型图解析 / 整屋套图 ──────────────────────────────────────
class NormalizedPoint(BaseModel):
    """相对户型图宽高的归一化坐标，避免前端预览尺寸影响持久化几何。"""
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class FloorplanCamera(BaseModel):
    id: str = Field(default='', max_length=80)
    name: str = Field(default='机位', max_length=100)
    position: NormalizedPoint
    target: NormalizedPoint
    height_m: Optional[float] = Field(default=None, ge=0.3, le=2.5)
    focal_length_mm: Optional[float] = Field(default=None, ge=12, le=120)
    purpose: Literal['hero', 'wide', 'detail', 'transition', 'custom'] = 'wide'
    source: Literal['ai_suggested', 'ai_edited', 'manual', 'legacy'] = 'manual'
    confirmed: bool = True
    enabled_for_generation: bool = True


class FloorplanOpening(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal['door', 'window', 'open_connection']
    points: List[NormalizedPoint] = Field(min_length=2, max_length=2)
    room_ids: List[str] = Field(default_factory=list, max_length=2)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal['ai_suggested', 'manual', 'ai_edited', 'legacy'] = 'manual'
    review_status: Literal['pending', 'accepted', 'rejected'] = 'accepted'


class FloorplanRoom(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=100)
    room_type: str = Field(default='其他', max_length=100)
    polygon: List[NormalizedPoint] = Field(min_length=3, max_length=64)
    adjacent_room_ids: List[str] = Field(default_factory=list, max_length=20)
    dimensions_text: str = Field(default='', max_length=200)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    space_kind: Literal['enclosed_room', 'open_zone', 'circulation', 'wet_area', 'balcony', 'other'] = 'enclosed_room'
    source: Literal['ai', 'human', 'ai_edited', 'legacy'] = 'human'
    selected: bool = True
    apply_floor: bool = True
    cameras: List[FloorplanCamera] = Field(default_factory=list, max_length=20)
    primary_camera_id: str = Field(default='', max_length=80)
    # 旧前端兼容；新流程使用 cameras + primary_camera_id。
    camera: Optional[FloorplanCamera] = None


class FloorplanAnalysisRequest(BaseModel):
    floorplan_path: str
    api_key: str = ''


class FloorplanManualRequest(BaseModel):
    floorplan_path: str


class FloorplanOperation(BaseModel):
    type: str = Field(min_length=1, max_length=80)
    room_id: str = Field(default='', max_length=80)
    camera_id: str = Field(default='', max_length=80)
    payload: dict = Field(default_factory=dict)


class FloorplanDraftRequest(BaseModel):
    base_revision: int = Field(ge=0)
    rooms: List[FloorplanRoom] = Field(default_factory=list, max_length=40)
    openings: List[FloorplanOpening] = Field(default_factory=list, max_length=160)
    openings_review_status: Literal['pending', 'confirmed'] = 'pending'
    entrance: Optional[NormalizedPoint] = None
    orientation: str = Field(default='', max_length=100)
    operations: List[FloorplanOperation] = Field(default_factory=list, max_length=100)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class FloorplanVerifyRequest(BaseModel):
    base_revision: int = Field(ge=0)
    training_consent: bool = False
    acknowledged_warning_codes: List[str] = Field(default_factory=list, max_length=100)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class FloorplanConsentRequest(BaseModel):
    allowed: bool
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class FloorplanSpatialPlanGenerateRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=80)
    camera_id: str = Field(min_length=1, max_length=80)
    api_key: str = ''


class FloorplanSpatialPlanUpdateRequest(BaseModel):
    space_summary: str = Field(default='', max_length=2000)
    camera_view: dict = Field(default_factory=dict)
    architecture: dict = Field(default_factory=dict)
    zones: List[dict] = Field(default_factory=list, max_length=30)
    furniture: List[dict] = Field(default_factory=list, max_length=80)
    hard_constraints: List[str] = Field(default_factory=list, max_length=40)
    must_not_appear: List[str] = Field(default_factory=list, max_length=40)
    uncertainties: List[str] = Field(default_factory=list, max_length=40)
    status: Literal['draft', 'locked'] = 'draft'
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class FloorplanViewProxyConfirmRequest(BaseModel):
    image_data_url: str = Field(min_length=32, max_length=16_000_000)
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    render_config: dict = Field(default_factory=dict)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class FloorplanConfirmRequest(BaseModel):
    rooms: List[FloorplanRoom] = Field(min_length=1, max_length=40)
    openings: List[FloorplanOpening] = Field(default_factory=list, max_length=160)
    openings_review_status: Literal['pending', 'confirmed'] = 'pending'
    entrance: Optional[NormalizedPoint] = None
    orientation: str = Field(default='', max_length=100)


class FloorplanSuiteRequest(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=80)
    floor_path: str
    style_ref_path: Optional[str] = None
    prompt: str = Field(default='', max_length=5000)
    style: str = Field(default='现代自然', max_length=200)
    lighting: str = Field(default='自然日光', max_length=200)
    generation_mode: Literal['fast', 'consistent'] = 'fast'
    model_key: Literal['b2', 'pro'] = 'pro'
    model_keys: List[Literal['b2', 'pro']] = Field(default_factory=list, max_length=2)
    candidates_per_room: int = Field(default=2, ge=2, le=3)
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    resolution: Literal['2K', '4K'] = '4K'
    camera_ids_by_room: dict[str, List[str]] = Field(default_factory=dict)
    api_key: str = ''

    @model_validator(mode='after')
    def normalize_model_keys(self):
        # model_key is retained for older clients and persisted task compatibility.
        self.model_keys = list(dict.fromkeys(self.model_keys or [self.model_key]))
        self.model_key = self.model_keys[0]
        return self


class FloorplanAnchorRequest(BaseModel):
    result_id: str = Field(min_length=1, max_length=100)


class SuiteCandidateReviewRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=160)
    result_id: str = Field(min_length=1, max_length=120)
    review_status: Literal['unreviewed', 'pass', 'backup', 'reject'] = 'unreviewed'
    review_tags: List[str] = Field(default_factory=list, max_length=20)
    review_note: str = Field(default='', max_length=2000)
    best: bool = False


# ── Whole-home v2: one metric model -> 3D cameras -> constrained renders ──
class WholeHomeProjectRequest(BaseModel):
    floorplan_path: str = ''
    import_analysis_id: str = Field(default='', max_length=100)
    cad_path: str = Field(default='', max_length=2000)
    reference_url: str = Field(default='', max_length=2000)
    width_m: Optional[float] = Field(default=None, ge=2, le=80)
    api_key: str = ''

    @model_validator(mode='after')
    def exactly_one_geometry_source(self):
        self.floorplan_path = self.floorplan_path.strip()
        self.import_analysis_id = self.import_analysis_id.strip()
        self.cad_path = self.cad_path.strip()
        self.reference_url = self.reference_url.strip()
        if sum(bool(value) for value in (
                self.floorplan_path, self.import_analysis_id, self.cad_path)) != 1:
            raise ValueError('exactly one of floorplan_path, import_analysis_id or cad_path is required')
        return self


class WholeHomeCadReparseRequest(BaseModel):
    base_revision: int = Field(ge=0)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)
    candidate_id: str = Field(default='', max_length=100)
    operation_id: str = Field(default='', max_length=120)

    @field_validator('annotator_id')
    @classmethod
    def normalize_cad_annotator_id(cls, value: str) -> str:
        return value.strip()

    @field_validator('candidate_id')
    @classmethod
    def normalize_cad_candidate_id(cls, value: str) -> str:
        return value.strip()

    @field_validator('operation_id')
    @classmethod
    def normalize_cad_reparse_operation_id(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{8,120}', value):
            raise ValueError('operation_id must contain only letters, digits, underscore or dash')
        return value


class WholeHomeCadPoint(BaseModel):
    model_config = {'extra': 'forbid'}
    x: float = Field(ge=-10000, le=10000)
    z: float = Field(ge=-10000, le=10000)


class WholeHomeCadPhysicalSpace(BaseModel):
    model_config = {'extra': 'forbid'}
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=100)
    space_type: Literal[
        'enclosed_room', 'open_plan', 'circulation', 'wet_suite', 'balcony',
        'service', 'other'] = 'other'
    face_ids: List[str] = Field(min_length=1, max_length=100)
    polygon: List[WholeHomeCadPoint] = Field(default_factory=list, max_length=500)
    selected: bool = True


class WholeHomeCadSemanticGeometry(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['polygon', 'rectangle', 'split_halfplane'] = 'polygon'
    points: List[WholeHomeCadPoint] = Field(default_factory=list, max_length=500)
    min_x: Optional[float] = Field(default=None, ge=-10000, le=10000)
    min_z: Optional[float] = Field(default=None, ge=-10000, le=10000)
    max_x: Optional[float] = Field(default=None, ge=-10000, le=10000)
    max_z: Optional[float] = Field(default=None, ge=-10000, le=10000)
    start: Optional[WholeHomeCadPoint] = None
    end: Optional[WholeHomeCadPoint] = None
    side: Optional[Literal['left', 'right']] = None

    @model_validator(mode='after')
    def validate_geometry_fields(self):
        if self.kind == 'polygon' and len(self.points) < 3:
            raise ValueError('polygon geometry requires at least 3 points')
        if self.kind == 'rectangle' and any(value is None for value in (
                self.min_x, self.min_z, self.max_x, self.max_z)):
            raise ValueError('rectangle geometry requires min_x/min_z/max_x/max_z')
        if self.kind == 'split_halfplane' and (not self.start or not self.end or not self.side):
            raise ValueError('split_halfplane geometry requires start/end/side')
        return self


class WholeHomeCadSemanticZone(BaseModel):
    model_config = {'extra': 'forbid'}
    id: str = Field(min_length=1, max_length=80)
    physical_space_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=100)
    zone_type: Literal[
        'living_room', 'dining_room', 'foyer', 'kitchen', 'bedroom',
        'primary_bedroom', 'secondary_bedroom', 'bathroom', 'balcony',
        'circulation', 'storage', 'utility', 'other'] = 'other'
    geometry: WholeHomeCadSemanticGeometry


class WholeHomeCadSpaceDraftRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(default='', max_length=120)
    editor_id: str = Field(default='local-user', min_length=1, max_length=100)
    physical_spaces: List[WholeHomeCadPhysicalSpace] = Field(min_length=1, max_length=100)
    semantic_zones: List[WholeHomeCadSemanticZone] = Field(min_length=1, max_length=300)
    excluded_face_ids: List[str] = Field(default_factory=list, max_length=500)

    @field_validator('operation_id')
    @classmethod
    def normalize_cad_space_operation_id(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{8,120}', value):
            raise ValueError('operation_id must contain only letters, digits, underscore or dash')
        return value


class WholeHomeCadSemanticReconstructRequest(BaseModel):
    base_revision: int = Field(ge=0)
    api_key: str = ''
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)

    @field_validator('annotator_id')
    @classmethod
    def normalize_cad_semantic_annotator_id(cls, value: str) -> str:
        return value.strip()


class WholeHomeCadAiAssistRequest(BaseModel):
    """Bounded, advisory-only Gemini review of one immutable CAD parse."""
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    operation_id: str = Field(default='', max_length=120)
    api_key: str = Field(default='', max_length=500)
    review_passes: int = Field(default=1, ge=1, le=2)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)

    @field_validator('operation_id')
    @classmethod
    def normalize_cad_ai_operation_id(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{8,120}', value):
            raise ValueError('operation_id must contain only letters, digits, underscore or dash')
        return value

    @field_validator('annotator_id')
    @classmethod
    def normalize_cad_ai_annotator_id(cls, value: str) -> str:
        return value.strip()


class WholeHomeModelSaveRequest(BaseModel):
    base_revision: int = Field(ge=0)
    model: dict
    operations: List[dict] = Field(default_factory=list, max_length=200)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomeVerifyRequest(BaseModel):
    base_revision: int = Field(ge=0)
    acknowledged_warning_codes: List[str] = Field(default_factory=list, max_length=100)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomeSourceRegistrationRequest(BaseModel):
    """Bind source pixels/CAD coordinates to the canonical metre model."""
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(default='local-user', min_length=1, max_length=100)
    registration: dict


class WholeHomeRasterRegistrationPrepareRequest(BaseModel):
    """Create a checksum-bound raster registration from clicked dimensions."""
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(default='local-user', min_length=1, max_length=100)
    scale_anchors: List[dict] = Field(min_length=1, max_length=8)
    origin_px: List[float] = Field(default_factory=lambda: [0.0, 0.0],
                                   min_length=2, max_length=2)


class WholeHomeGeometryAcceptanceRequest(BaseModel):
    """Measure a correspondence report; commit only a fully passed report."""
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(min_length=1, max_length=100)
    review_note: str = Field(min_length=3, max_length=2000)
    assumptions_confirmed: bool = False
    raster_metrics: dict = Field(default_factory=dict)
    commit: bool = False


# ── Raster-first renovation-sales proposal contracts ────────────────────────
class WholeHomeConstructionProfileRequest(BaseModel):
    """Confirm every vertical assumption that cannot be read from a 2D plan."""
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(default='local-user', min_length=1, max_length=100)
    values: dict


class WholeHomeSceneRecipePreviewRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    variant_index: Literal[1, 2, 3] = 1


class WholeHomeSceneRecipeCommitRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(default='local-user', min_length=1, max_length=100)
    variant_index: Literal[1, 2, 3] = 1


class WholeHomeSceneRecipeReviewRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(default='local-user', min_length=1, max_length=100)
    note: str = Field(min_length=3, max_length=2000)
    action: Literal['review', 'lock'] = 'review'


class WholeHomeCadWallAssemblyConfirmRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)
    thickness_m: float = Field(ge=.05, le=.80)
    height_m: float = Field(default=2.8, ge=2.0, le=6.0)


class WholeHomeCadOpeningAnnotationsRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    base_revision: int = Field(ge=0)
    base_state_hash: str = Field(default='', max_length=128)
    operation_id: str = Field(min_length=8, max_length=120,
                              pattern=r'^[A-Za-z0-9_-]{8,120}$')
    reviewer: str = Field(min_length=1, max_length=100)
    annotations: List[dict] = Field(min_length=1, max_length=100)


class WholeHomeSemanticLayoutRequest(BaseModel):
    base_revision: int = Field(ge=0)
    api_key: str = ''
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomeCaptureRequest(BaseModel):
    camera: dict
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    rgb_data_url: str = Field(min_length=32, max_length=24_000_000)
    depth_data_url: str = Field(min_length=32, max_length=24_000_000)
    normal_data_url: str = Field(min_length=32, max_length=24_000_000)
    edge_data_url: str = Field(default='', max_length=24_000_000)
    semantic_data_url: str = Field(min_length=32, max_length=24_000_000)
    semantic_legend: dict = Field(default_factory=dict)
    subject_id_data_url: str = Field(default='', max_length=24_000_000)
    subject_id_legend: dict = Field(default_factory=dict)
    room_id: str = Field(default='', max_length=80)
    plan_id: str = Field(default='', max_length=100)
    candidate_id: str = Field(default='', max_length=100)
    reference_slot_id: str = Field(default='', max_length=100)
    reference_proposal_id: str = Field(default='', max_length=120)
    reference_proposal_hash: str = Field(default='', max_length=128)
    scene_recipe_id: str = Field(default='', max_length=160)
    scene_hash: str = Field(default='', max_length=128)
    pool_rank: int = Field(default=1, ge=1, le=3)
    is_primary: bool = True
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomePanoCaptureRequest(BaseModel):
    """定点球面全景 capture 上传契约(文档:docs/定点球面全景_AI生成与一致性方案.md §10)。

    六面必须共享同一投影中心(同光心),face order 为项目级固定顺序;ERP 严格 2:1。
    全景通道不混用 perspective 的 aspect_ratio('2:1' 不得出现在
    WholeHomeCaptureRequest.aspect_ratio);depth 为整组 metric near/far,
    normal 为 world-space XYZ→RGB。
    """
    pano_id: str = Field(min_length=1, max_length=80)
    camera: dict
    projection: Literal['equirectangular'] = 'equirectangular'
    coordinate_system: Literal['right-handed-y-up'] = 'right-handed-y-up'
    camera_center_m: dict
    canonical_forward: str = Field(default='+Z', max_length=8)
    heading_deg: float = Field(default=0, ge=-180, le=180)
    pitch_deg: float = Field(default=0, ge=-90, le=90)
    roll_deg: float = Field(default=0, ge=-180, le=180)
    horizontal_fov_deg: float = Field(default=360, ge=360, le=360)
    vertical_fov_deg: float = Field(default=180, ge=180, le=180)
    erp_width: int = Field(ge=64, le=8192)
    erp_height: int = Field(ge=32, le=4096)
    cube_face_size: int = Field(ge=64, le=2048)
    cube_face_order: List[str] = Field(
        default_factory=lambda: ['+X', '-X', '+Y', '-Y', '+Z', '-Z'])
    near_m: float = Field(default=0.05, gt=0, le=2.0)
    far_m: float = Field(default=30.0, gt=0, le=500.0)
    depth_encoding: Literal['linear_metric_global_range'] = 'linear_metric_global_range'
    normal_encoding: Literal['world_space_xyz_to_rgb'] = 'world_space_xyz_to_rgb'
    rgb_atlas_data_url: str = Field(min_length=32, max_length=60_000_000)
    depth_atlas_data_url: str = Field(min_length=32, max_length=60_000_000)
    normal_atlas_data_url: str = Field(min_length=32, max_length=60_000_000)
    edge_atlas_data_url: str = Field(default='', max_length=60_000_000)
    semantic_atlas_data_url: str = Field(min_length=32, max_length=60_000_000)
    semantic_legend: dict = Field(default_factory=dict)
    subject_id_atlas_data_url: str = Field(min_length=32, max_length=60_000_000)
    subject_id_legend: dict = Field(default_factory=dict)
    render_contract: dict = Field(default_factory=dict)
    model_facts_hash: str = Field(default='', max_length=128)
    material_graph_hash: str = Field(default='', max_length=128)
    lighting_hash: str = Field(default='', max_length=128)
    source_hash: str = Field(default='', max_length=128)
    scene_recipe_id: str = Field(default='', max_length=160)
    scene_hash: str = Field(default='', max_length=128)
    room_id: str = Field(default='', max_length=80)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)

    @model_validator(mode='after')
    def _check_erp_layout(self):
        if self.erp_width != self.erp_height * 2:
            raise ValueError('ERP 必须严格 2:1(width == height * 2),不得裁切或加边框')
        if self.cube_face_order != ['+X', '-X', '+Y', '-Y', '+Z', '-Z']:
            raise ValueError('cube_face_order 必须为项目级固定顺序 [+X, -X, +Y, -Y, +Z, -Z]')
        return self

    @model_validator(mode='after')
    def _check_camera_center(self):
        position = (self.camera or {}).get('position') or {}
        center = self.camera_center_m or {}
        for axis in ('x', 'y', 'z'):
            cam_value = position.get(axis)
            center_value = center.get(axis)
            if cam_value is not None and center_value is not None \
                    and abs(float(cam_value) - float(center_value)) > 1e-6:
                raise ValueError(
                    f'camera_center_m.{axis} 必须与 camera.position.{axis} 一致(六面共享同一光心)')
        return self

    @model_validator(mode='after')
    def _check_render_contract(self):
        materials = self.render_contract.get('materials') if isinstance(self.render_contract, dict) else None
        lighting = self.render_contract.get('lighting') if isinstance(self.render_contract, dict) else None
        if not isinstance(materials, dict) or not materials:
            raise ValueError('render_contract.materials 必须记录本次 clay/语义材质合同')
        if not isinstance(lighting, dict) or not lighting:
            raise ValueError('render_contract.lighting 必须记录本次灯光合同')
        return self


class WholeHomePanoPaidPreviewRequest(BaseModel):
    """不产生费用的全景编辑预览；返回一次性确认短语。"""
    source_hash: str = Field(min_length=32, max_length=128)
    provider: Literal['fal', 'openai'] = 'fal'
    engine: Literal['gpt-image-2', 'flux-canny'] = 'gpt-image-2'
    model_id: str = Field(default='gpt-image-2-2026-04-21', max_length=160)
    edit_instruction: str = Field(default='', max_length=20_000)
    style_description: str = Field(default='', max_length=2_000)
    repair_band_deg: float = Field(default=12, ge=1, le=30)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomePanoEditRequest(BaseModel):
    """执行已绑定付费预览的 edit/repair 阶段。"""
    pano_id: str = Field(min_length=1, max_length=80)
    source_hash: str = Field(min_length=32, max_length=128)
    preview_id: str = Field(min_length=16, max_length=100)
    confirmation_phrase: str = Field(min_length=8, max_length=200)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomePanoMaterializeRequest(BaseModel):
    """本地几何锁定材质化；不调用 provider、不产生费用。"""
    source_hash: str = Field(min_length=32, max_length=128)
    preset: Literal['warm-contemporary'] = 'warm-contemporary'
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomePanoGateRequest(BaseModel):
    """球面硬门禁请求(本地计算,不产生付费调用)。"""
    source_hash: str = Field(min_length=32, max_length=128)
    face_size: int = Field(default=256, ge=64, le=1024)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomePanoReviewRequest(BaseModel):
    """自动门禁通过后的 Viewer 人工验收。"""
    source_hash: str = Field(min_length=32, max_length=128)
    gate_version: str = Field(min_length=1, max_length=100)
    checklist: dict[str, Literal['pass', 'uncertain']]
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)

    @field_validator('checklist')
    @classmethod
    def _check_pano_review_items(cls, value):
        expected = {
            'wall_openings', 'duplicates', 'material_continuity',
            'lighting_continuity', 'poles', 'cross_hotspot_same_object',
        }
        if set(value) != expected:
            raise ValueError(f'checklist 必须且只能包含 {sorted(expected)}')
        return value


class WholeHomePanoHotspotRequest(BaseModel):
    """新增/校验一个球面热点:一个投影中心 + canonical heading。"""
    pano_id: str = Field(min_length=1, max_length=80)
    camera: dict
    camera_center_m: dict
    heading_deg: float = Field(default=0, ge=-180, le=180)
    pitch_deg: float = Field(default=0, ge=-90, le=90)
    roll_deg: float = Field(default=0, ge=-180, le=180)
    room_id: str = Field(default='', max_length=80)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)


class WholeHomePanoManifest(BaseModel):
    """球面全景 manifest 落盘视图(文档 §10 数据合同)。

    channels 保存落盘后的文件相对路径;source_hash 覆盖除生成结果与 QA 外的
    全部字段(结构/机位/projection/near-far/face order/材质灯光 hash 变化都会
    使旧 capture 失效)。
    """
    schema_version: int = 1
    capture_id: str = ''
    capture_revision: int = 1
    pano_id: str
    projection: Literal['equirectangular'] = 'equirectangular'
    coordinate_system: Literal['right-handed-y-up'] = 'right-handed-y-up'
    camera_center_m: dict
    canonical_forward: str = '+Z'
    heading_deg: float = 0
    pitch_deg: float = 0
    roll_deg: float = 0
    horizontal_fov_deg: float = 360
    vertical_fov_deg: float = 180
    erp_width: int
    erp_height: int
    cube_face_size: int
    cube_face_order: List[str] = Field(
        default_factory=lambda: ['+X', '-X', '+Y', '-Y', '+Z', '-Z'])
    near_m: float = 0.05
    far_m: float = 30.0
    depth_encoding: Literal['linear_metric_global_range'] = 'linear_metric_global_range'
    normal_encoding: Literal['world_space_xyz_to_rgb'] = 'world_space_xyz_to_rgb'
    model_facts_hash: str = ''
    material_graph_hash: str = ''
    lighting_hash: str = ''
    render_contract: dict = Field(default_factory=dict)
    channels: dict = Field(default_factory=dict)
    channel_hashes: dict[str, str] = Field(default_factory=dict)
    source_hash: str = ''


class WholeHomeCameraCandidatesRequest(BaseModel):
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    max_per_room: int = Field(default=8, ge=1, le=8)
    mode: Literal['room', 'reference'] = 'room'
    contract_id: str = Field(default='', max_length=160)


class WholeHomeReferenceCaptureBatchRequest(BaseModel):
    reference_proposal_id: str = Field(min_length=1, max_length=120)
    reference_proposal_hash: str = Field(min_length=32, max_length=128)
    width: int = Field(default=192, ge=192, le=1024)
    height: int = Field(default=144, ge=144, le=768)
    annotator_id: str = Field(
        default='local-software-renderer', min_length=1, max_length=100)


class WholeHomeAutoCameraRequest(BaseModel):
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    shots_per_room: int = Field(default=1, ge=1, le=2)
    candidates: List[dict] = Field(min_length=1, max_length=100)
    room_pools: List[dict] = Field(default_factory=list, max_length=50)
    annotator_id: str = Field(default='local-user', min_length=1, max_length=100)
    api_key: str = ''


class WholeHomeCaptureGroup(BaseModel):
    room_id: str = Field(min_length=1, max_length=80)
    slot_id: str = Field(default='', max_length=100)
    primary_capture_id: str = Field(min_length=1, max_length=100)
    fallback_capture_ids: List[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def normalize_capture_ids(self):
        self.slot_id = self.slot_id.strip()
        self.fallback_capture_ids = [
            value for value in dict.fromkeys(self.fallback_capture_ids)
            if value != self.primary_capture_id
        ][:2]
        return self


class WholeHomeRunRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    # capture_ids is the legacy flat-camera contract. New automatic runs use
    # capture_groups so one logical room/model result can move through the
    # primary camera and two fully persisted fallback captures.
    capture_ids: List[str] = Field(default_factory=list, max_length=30)
    capture_groups: List[WholeHomeCaptureGroup] = Field(default_factory=list, max_length=30)
    floor_path: str = ''
    material_mode: Literal['floor_sample', 'reference', 'style_pack'] = 'floor_sample'
    scene_recipe_id: str = Field(default='', max_length=160)
    reference_contract_id: str = Field(default='', max_length=160)
    benchmark_batch_id: str = Field(default='', max_length=160)
    style_ref_path: Optional[str] = None
    prompt: str = Field(default='', max_length=5000)
    style: str = Field(default='现代自然', max_length=200)
    lighting: str = Field(default='自然日光', max_length=200)
    model_keys: List[Literal['b2', 'pro']] = Field(default_factory=lambda: ['b2', 'pro'], min_length=1, max_length=2)
    candidates_per_camera: int = Field(default=1, ge=1, le=2)
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    resolution: Literal['2K', '4K'] = '4K'
    idempotency_key: str = Field(default='', max_length=160)
    api_key: str = ''

    @model_validator(mode='after')
    def normalize_targets(self):
        self.model_keys = list(dict.fromkeys(self.model_keys))
        self.capture_ids = list(dict.fromkeys(self.capture_ids))
        self.idempotency_key = self.idempotency_key.strip()
        self.floor_path = self.floor_path.strip()
        self.reference_contract_id = self.reference_contract_id.strip()
        self.scene_recipe_id = self.scene_recipe_id.strip()
        self.benchmark_batch_id = self.benchmark_batch_id.strip()
        if self.material_mode == 'floor_sample' and not self.floor_path:
            raise ValueError('floor_path is required for floor_sample material mode')
        if self.material_mode == 'style_pack' and not self.scene_recipe_id:
            raise ValueError('scene_recipe_id is required for style_pack material mode')
        if self.material_mode == 'reference':
            if any(not group.slot_id for group in self.capture_groups):
                raise ValueError('reference material mode requires slot_id for every capture group')
            slot_ids = [group.slot_id for group in self.capture_groups]
            if len(slot_ids) != len(set(slot_ids)):
                raise ValueError('reference capture_groups must contain at most one group per slot')
        else:
            room_ids = [group.room_id for group in self.capture_groups]
            if len(room_ids) != len(set(room_ids)):
                raise ValueError('capture_groups must contain at most one group per room')
        if not self.capture_ids and not self.capture_groups:
            raise ValueError('capture_ids or capture_groups is required')
        return self


class WholeHomeManualRunPreviewRequest(BaseModel):
    """One explicitly selected, low-cap manual-safe generation request."""
    model_config = {'extra': 'forbid'}

    project_id: str = Field(min_length=1, max_length=100)
    capture_ids: List[str] = Field(default_factory=list, max_length=1)
    capture_groups: List[WholeHomeCaptureGroup] = Field(default_factory=list, max_length=0)
    floor_path: str = ''
    material_mode: Literal['floor_sample', 'style_pack'] = 'floor_sample'
    scene_recipe_id: str = Field(default='', max_length=160)
    reference_contract_id: str = Field(default='', max_length=0)
    benchmark_batch_id: str = Field(default='', max_length=0)
    style_ref_path: Optional[str] = None
    prompt: str = Field(default='', max_length=5000)
    style: str = Field(default='现代自然', max_length=200)
    lighting: str = Field(default='自然日光', max_length=200)
    model_keys: List[Literal['b2', 'pro']] = Field(default_factory=list, max_length=1)
    candidates_per_camera: Literal[1] = 1
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    resolution: Literal['2K'] = '2K'
    idempotency_key: str = Field(
        min_length=1, max_length=160,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')

    @model_validator(mode='after')
    def enforce_manual_run_contract(self):
        self.capture_ids = list(dict.fromkeys(
            value.strip() for value in self.capture_ids if value.strip()))
        self.model_keys = list(dict.fromkeys(self.model_keys))
        self.floor_path = self.floor_path.strip()
        self.scene_recipe_id = self.scene_recipe_id.strip()
        if len(self.capture_ids) != 1:
            raise ValueError('manual run requires exactly one capture')
        if len(self.model_keys) != 1:
            raise ValueError('manual run requires exactly one model')
        if self.material_mode == 'floor_sample' and not self.floor_path:
            raise ValueError('manual run requires floor sample')
        if self.material_mode == 'style_pack' and not self.scene_recipe_id:
            raise ValueError('manual style-pack run requires a locked scene recipe')
        if self.material_mode == 'style_pack' and (self.floor_path or self.style_ref_path):
            raise ValueError('manual style-pack run cannot use floor or generic style images')
        return self


class WholeHomeManualRunCommitRequest(BaseModel):
    model_config = {'extra': 'forbid'}

    preview_id: str = Field(
        min_length=1, max_length=200,
        pattern=r'^manual_preview_[A-Za-z0-9]+$')
    preview_sha256: str = Field(
        min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    confirmation_phrase: str = Field(min_length=10, max_length=200)
    api_key: str = ''


class WholeHomeHistoryForkRequest(BaseModel):
    """Create an editable project branch from one immutable generation run."""
    model_config = {'extra': 'forbid'}

    branch_name: str = Field(default='', max_length=200)
    source_snapshot_hash: str = Field(default='', max_length=64,
                                      pattern=r'^(|[0-9a-f]{64})$')
    idempotency_key: str = Field(min_length=1, max_length=160)


class WholeHomeGenerationDraftRequest(BaseModel):
    """Persist style inputs without authorising a paid provider call."""
    model_config = {'extra': 'forbid'}

    expected_draft_version: int = Field(default=0, ge=0)
    source_run_id: str = Field(default='', max_length=100)
    variant_label: str = Field(default='', max_length=200)
    style: str = Field(default='现代自然', max_length=200)
    lighting: str = Field(default='自然日光', max_length=200)
    prompt: str = Field(default='', max_length=5000)
    material_mode: Literal['floor_sample'] = 'floor_sample'
    floor_path: str = Field(default='', max_length=2000)
    style_ref_path: str = Field(default='', max_length=2000)
    model_keys: List[Literal['b2', 'pro']] = Field(default_factory=list, max_length=2)
    selected_artifact_ids: List[str] = Field(default_factory=list, max_length=100)
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    resolution: Literal['2K'] = '2K'

    @model_validator(mode='after')
    def normalize_generation_draft(self):
        self.model_keys = list(dict.fromkeys(self.model_keys))
        self.selected_artifact_ids = list(dict.fromkeys(
            value.strip() for value in self.selected_artifact_ids if value.strip()))
        self.floor_path = self.floor_path.strip()
        self.style_ref_path = self.style_ref_path.strip()
        return self


class WholeHomeVariantBatchPreviewRequest(BaseModel):
    model_config = {'extra': 'forbid'}

    project_id: str = Field(min_length=1, max_length=100)
    source_run_id: str = Field(min_length=1, max_length=100)
    style: str = Field(default='现代自然', max_length=200)
    lighting: str = Field(default='自然日光', max_length=200)
    prompt: str = Field(default='', max_length=5000)
    floor_path: str = Field(min_length=1, max_length=2000)
    style_ref_path: str = Field(default='', max_length=2000)
    aspect_ratio: Literal['4:3', '16:9', '3:4', '9:16'] = '4:3'
    resolution: Literal['2K'] = '2K'
    excluded_artifact_ids: List[str] = Field(default_factory=list, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def normalize_variant_preview(self):
        self.floor_path = self.floor_path.strip()
        self.style_ref_path = self.style_ref_path.strip()
        self.excluded_artifact_ids = list(dict.fromkeys(
            value.strip() for value in self.excluded_artifact_ids if value.strip()))
        self.idempotency_key = self.idempotency_key.strip()
        return self


class WholeHomeVariantBatchCommitRequest(BaseModel):
    model_config = {'extra': 'forbid'}

    preview_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    confirmation_phrase: str = Field(min_length=10, max_length=240)
    api_key: str = ''


class WholeHomeDevelopmentBudgetLimits(BaseModel):
    """User-authorized ceilings; callers may lower but never raise them."""
    paid_batches: int = Field(default=6, ge=1, le=6)
    image_calls: int = Field(default=140, ge=1, le=140)
    qa_calls: int = Field(default=280, ge=1, le=280)


class WholeHomeDevelopmentAutopilotRunRequest(WholeHomeRunRequest):
    development_session_id: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
    batch_index: int = Field(ge=1, le=6)
    parent_run_id: str = Field(default='', max_length=100)
    limits: WholeHomeDevelopmentBudgetLimits = Field(
        default_factory=WholeHomeDevelopmentBudgetLimits)

    @model_validator(mode='after')
    def normalize_development_request(self):
        self.development_session_id = self.development_session_id.strip()
        self.parent_run_id = self.parent_run_id.strip()
        if not self.idempotency_key:
            raise ValueError('development_autopilot requires idempotency_key')
        if self.batch_index > self.limits.paid_batches:
            raise ValueError('batch_index cannot exceed the paid_batches limit')
        if set(self.model_keys) != {'b2', 'pro'}:
            raise ValueError('development_autopilot requires B2 and Pro for every active target')
        return self


class WholeHomeDevelopmentReconcileRequest(BaseModel):
    apply: bool = False
    expected_state_version: Optional[int] = Field(default=None, ge=0)
    idempotency_key: str = Field(default='', max_length=160)

    @model_validator(mode='after')
    def require_apply_idempotency(self):
        self.idempotency_key = self.idempotency_key.strip()
        if self.apply and not self.idempotency_key:
            raise ValueError('apply reconciliation requires idempotency_key')
        return self


class WholeHomeAgentTaskDefinition(BaseModel):
    task_id: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=100)
    mode: Literal['read_only', 'write', 'single_writer'] = 'read_only'
    depends_on: List[str] = Field(default_factory=list, max_length=50)
    input_artifact_hashes: dict = Field(default_factory=dict)
    allowed_paths: List[str] = Field(default_factory=list, max_length=100)


class WholeHomeAgentWorkflowCreateRequest(BaseModel):
    workflow_id: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
    title: str = Field(default='', max_length=300)
    source_digest: str = Field(default='', max_length=128)
    tasks: List[WholeHomeAgentTaskDefinition] = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')


class WholeHomeAgentTaskClaimRequest(BaseModel):
    agent_id: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
    expected_version: int = Field(ge=0)
    lease_seconds: int = Field(default=300, ge=30, le=3600)
    idempotency_key: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')


class WholeHomeAgentTaskHeartbeatRequest(BaseModel):
    lease_token: str = Field(min_length=16, max_length=160)
    expected_version: int = Field(ge=0)
    lease_seconds: int = Field(default=300, ge=30, le=3600)
    idempotency_key: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')


class WholeHomeAgentTaskResult(BaseModel):
    status: Literal['pass', 'fail', 'blocked', 'cancelled']
    summary: str = Field(default='', max_length=4000)
    report_path: str = Field(default='', max_length=1000)
    report_sha256: str = Field(default='', max_length=128)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    input_artifact_hashes: dict = Field(default_factory=dict)
    before_source_digest: str = Field(default='', max_length=128)
    after_source_digest: str = Field(default='', max_length=128)
    # Stable causal identifiers for failure-signature-v1.  Free-form prose,
    # report hashes, run/task IDs, paths and timestamps are not signature input.
    dimension: str = Field(default='', max_length=100)
    pipeline_stage: str = Field(default='', max_length=100)
    failure_code: str = Field(default='', max_length=160)
    affected_subjects: List[str] = Field(default_factory=list, max_length=100)
    causal_component: str = Field(default='', max_length=160)

    @model_validator(mode='after')
    def require_stable_failure_fields(self):
        for key in ('dimension', 'pipeline_stage', 'failure_code', 'causal_component'):
            setattr(self, key, str(getattr(self, key) or '').strip())
        self.affected_subjects = sorted(set(
            str(value or '').strip() for value in self.affected_subjects
            if str(value or '').strip()))
        if self.status == 'fail':
            missing = [key for key in (
                'dimension', 'pipeline_stage', 'failure_code',
                'affected_subjects', 'causal_component') if not getattr(self, key)]
            if missing:
                raise ValueError(
                    'fail result requires stable failure-signature-v1 fields: '
                    + ', '.join(missing))
            stable_id = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')
            unstable = [key for key in (
                'dimension', 'pipeline_stage', 'failure_code', 'causal_component')
                if not stable_id.fullmatch(str(getattr(self, key) or ''))]
            unstable.extend(
                f'affected_subjects[{index}]'
                for index, value in enumerate(self.affected_subjects)
                if not stable_id.fullmatch(value))
            if unstable:
                raise ValueError(
                    'failure-signature-v1 fields must be stable identifiers, '
                    'not paths, IDs, timestamps, or free prose: '
                    + ', '.join(unstable))
        return self


class WholeHomeAgentTaskCompleteRequest(BaseModel):
    lease_token: str = Field(min_length=16, max_length=160)
    expected_version: int = Field(ge=0)
    result: WholeHomeAgentTaskResult
    idempotency_key: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')


class WholeHomeAgentWorkflowTransitionRequest(BaseModel):
    expected_version: int = Field(ge=0)
    reason: str = Field(default='', max_length=500)
    idempotency_key: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')


class WholeHomeExternalReviewRequest(BaseModel):
    model_config = {'extra': 'forbid'}

    workflow_id: str = Field(
        min_length=1, max_length=160,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
    task_id: str = Field(
        min_length=1, max_length=160,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
    lease_token: str = Field(min_length=16, max_length=160)
    result_id: str = Field(min_length=1, max_length=200)
    artifact_id: str = Field(min_length=1, max_length=200)
    review_status: Literal['pass', 'backup', 'reject']
    review_tags: List[str] = Field(default_factory=list, max_length=20)
    review_note: str = Field(default='', max_length=4000)
    failure_dimension: str = Field(default='', max_length=100)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_review_version: int = Field(ge=0)
    idempotency_key: str = Field(
        min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')

    @model_validator(mode='after')
    def validate_external_review(self):
        self.review_tags = list(dict.fromkeys(
            value.strip() for value in self.review_tags if value.strip()))
        if self.review_status == 'reject' and not self.review_tags:
            raise ValueError('reject external review requires at least one tag')
        return self


class WholeHomeQaRetryRequest(BaseModel):
    result_ids: List[str] = Field(default_factory=list, max_length=30)
    api_key: str = ''


class WholeHomeResultReviewRequest(BaseModel):
    artifact_id: str = Field(default='', max_length=200)
    review_status: Literal['unreviewed', 'pass', 'backup', 'reject']
    review_tags: List[str] = Field(default_factory=list, max_length=20)
    review_note: str = Field(default='', max_length=2000)
    reviewer_id: str = Field(default='local-user', min_length=1, max_length=100)
    expected_review_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @model_validator(mode='after')
    def normalize_review(self):
        self.artifact_id = self.artifact_id.strip()
        self.reviewer_id = self.reviewer_id.strip()
        self.idempotency_key = self.idempotency_key.strip()
        self.review_note = self.review_note.strip()
        self.review_tags = list(dict.fromkeys(
            value.strip() for value in self.review_tags if value.strip()
        ))
        if self.review_status == 'reject' and not self.review_tags:
            raise ValueError('reject review requires at least one review tag')
        return self


class WholeHomeReviewCompleteRequest(BaseModel):
    reviewer_id: str = Field(default='local-user', min_length=1, max_length=100)
    expected_review_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator('reviewer_id')
    @classmethod
    def normalize_reviewer_id(cls, value: str) -> str:
        return value.strip()

    @field_validator('idempotency_key')
    @classmethod
    def normalize_completion_idempotency_key(cls, value: str) -> str:
        return value.strip()


class WholeHomeTrainingConsentRequest(BaseModel):
    allowed: bool
    reviewer_id: str = Field(default='local-user', min_length=1, max_length=100)

    @field_validator('reviewer_id')
    @classmethod
    def normalize_consent_reviewer_id(cls, value: str) -> str:
        return value.strip()


class WholeHomeContinueRequest(BaseModel):
    expected_review_version: int = Field(ge=0)
    continuation_completion_event_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    api_key: str = ''

    @model_validator(mode='after')
    def normalize_continue(self):
        self.continuation_completion_event_id = self.continuation_completion_event_id.strip()
        self.idempotency_key = self.idempotency_key.strip()
        return self


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
    auto_color_match_enabled: Optional[bool] = None
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


class GeometryAuditReviewRequest(RecordRef):
    checked_metric_ids: List[str] = Field(default_factory=list, max_length=100)
    reviewer: str = Field(default='', max_length=100)
    note: str = Field(default='', max_length=2000)


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
    algorithm: Literal['classic', 'distribution'] = 'classic'
    illumination_mode: Literal['off', 'chroma', 'full'] = 'off'

    @model_validator(mode='after')
    def enable_distribution_for_illumination(self):
        if self.illumination_mode != 'off':
            self.algorithm = 'distribution'
        return self



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
    algorithm: Literal['classic', 'distribution'] = 'classic'
    illumination_mode: Literal['off', 'chroma', 'full'] = 'off'

    @model_validator(mode='after')
    def enable_distribution_for_illumination(self):
        if self.illumination_mode != 'off':
            self.algorithm = 'distribution'
        return self


class SuiteColorMatchRequest(ColorMatchPreviewRequest):
    suite_id: str = Field(min_length=1, max_length=80)
    room_id: str = Field(min_length=1, max_length=80)
    result_id: str = Field(min_length=1, max_length=100)
    ref_path: str = ''



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
    """修补目标：job 候选 / 记录结果 / 房间图 / 整屋套图。字段按 kind 选用。"""
    kind: Literal['job', 'record', 'room', 'suite']
    jid: str = ''
    stage: Literal['b2', 'pro', 'sd35'] = 'pro'
    image_rel: str = ''
    json_path: str = ''
    record_id: str = ''
    result_id: str = Field(default='', max_length=120)
    room_path: str = ''
    suite_id: str = Field(default='', max_length=80)
    room_id: str = Field(default='', max_length=80)


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
