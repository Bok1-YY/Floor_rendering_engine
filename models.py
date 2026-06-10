# ==========================================
# 数据模型 — 纯数据，无 UI/IO 依赖
# ==========================================
"""Dataclasses for job records and task parameters."""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class JobRecord:
    """A single rendering job in the queue."""
    job_id: str
    display_name: str
    ts: str
    status: str = 'queued'
    model_filter: str = 'both'  # "b2" | "pro" | "both"
    b2_path: Optional[str] = None
    pro_path: Optional[str] = None
    error: str = ''
    json_path: str = ''
    record_id: str = ''
    png_path: str = ''
    started_at: Optional[float] = None       # epoch timestamp when generation started
    b2_secs: Optional[float] = None          # B2 model generation time in seconds
    pro_secs: Optional[float] = None         # Pro model generation time in seconds
    pro_polish_path: Optional[str] = None    # seam-polished Pro result image path
    pro_polishing: bool = False              # True while polish is in progress
    b2_stage: str = ''                       # live status text for B2 (思考标题/渲染中/重试) — worker thread writes, UI timer reads
    pro_stage: str = ''                      # live status text for Pro
    retry_ctx: Optional[dict] = None         # 生成上下文快照，供失败后「重试」按钮重放(只重跑未成图的模型)


@dataclass
class TaskParams:
    """All parameters for a prompt generation task.

    This documents the 35+ parameters that save_task_files_html accepts.
    Use task_params_to_kwargs() to convert back to the legacy kwargs dict.
    """
    # ── Required ──
    workflow_mode: str
    model_choice: str
    image_path: str

    # ── Location ──
    continent: str = '欧洲'
    country: str = ''
    city: str = ''
    neighborhood: str = ''

    # ── Property & Room ──
    property_type: str = '现代别墅'
    room_type: str = '客餐厅一体'
    view: str = '自然通透景观'

    # ── Style ──
    style_type: str = ''
    lighting: str = ''
    floor_tone: str = ''

    # ── Floor ──
    floor_size: str = ''
    seam_type: str = '无缝拼接 (SPC/LVT专用)'
    glossiness: str = '哑光 (3-5°)'

    # ── Camera ──
    angle: str = '28mm lens (Wide)'
    aspect_ratio: str = '4:3'
    resolution: str = '4K'

    # ── Avoid / Custom ──
    avoid_items: List[str] = field(default_factory=list)
    custom_addition: str = ''

    # ── Pet (only for 宠物友好 mode) ──
    pet_type: str = ''
    pet_action: str = ''
    pet_focus: str = ''

    # ── Furniture ──
    market_furniture: str = ''

    # ── Internal state ──
    last_image_path: str = ''

    # ── CN mode ──
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

    # ── Reference mode ──
    style_ref_correction: str = ''
    style_analysis_text: str = ''


def task_params_to_kwargs(p: TaskParams) -> dict:
    """Convert a TaskParams instance to the legacy kwargs dict.

    This allows calling save_task_files_html(**task_params_to_kwargs(params))
    without changing the function signature.
    """
    return {
        'workflow_mode': p.workflow_mode,
        'model_choice': p.model_choice,
        'image_path': p.image_path,
        'continent': p.continent,
        'country': p.country,
        'city': p.city,
        'neighborhood': p.neighborhood,
        'property_type': p.property_type,
        'style_type': p.style_type,
        'room_type': p.room_type,
        'view': p.view,
        'lighting': p.lighting,
        'pet_type': p.pet_type,
        'pet_action': p.pet_action,
        'pet_focus': p.pet_focus,
        'angle': p.angle,
        'aspect_ratio': p.aspect_ratio,
        'resolution': p.resolution,
        'glossiness': p.glossiness,
        'seam_type': p.seam_type,
        'avoid_items': p.avoid_items,
        'floor_size': p.floor_size,
        'custom_addition': p.custom_addition,
        'floor_tone': p.floor_tone,
        'market_furniture': p.market_furniture,
        'last_image_path': p.last_image_path,
        'cn_mode': p.cn_mode,
        'cn_developer': p.cn_developer,
        'cn_city': p.cn_city,
        'cn_tier': p.cn_tier,
        'cn_unit_type': p.cn_unit_type,
        'cn_delivery': p.cn_delivery,
        'cn_room_type': p.cn_room_type,
        'cn_view': p.cn_view,
        'cn_space_features': p.cn_space_features,
        'cn_facilities': p.cn_facilities,
        'style_ref_correction': p.style_ref_correction,
        'style_analysis_text': p.style_analysis_text,
    }
