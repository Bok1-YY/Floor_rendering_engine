# ==========================================
# 数据模型 — 纯数据，无 UI/IO 依赖
# ==========================================
"""Dataclasses for job records and task parameters."""

import time
import uuid
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
    workflow_mode: str = ''     # 提交时的工作流模式(纯效果图/地板替换/宠物友好/参照模式)，用量统计按它归类
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
    favorite: bool = False                   # 队列卡收藏标记（仅内存态，供「⭐仅看收藏」过滤；不持久化）
    # ── 重抽候选累积（同卡内左右切换对比）──
    # 每个图槽保存历次重抽产出的全部候选路径；*_path 始终 = *_paths[*_idx]（当前展示的那张）。
    # 老任务/老持久化只有 *_path、无列表 → ensure_candidate_lists() 用单路径回填，向后兼容。
    b2_paths: List[str] = field(default_factory=list)
    pro_paths: List[str] = field(default_factory=list)
    pro_polish_paths: List[str] = field(default_factory=list)
    b2_idx: int = 0
    pro_idx: int = 0
    pro_polish_idx: int = 0


# ── JobRecord 相关纯逻辑（从 webui 下沉，无 UI/IO 依赖）─────────────────────

def new_job(display_name: str, ts: str, model_filter: str = 'both') -> JobRecord:
    """工厂：构造一个新的排队 JobRecord。入队由调用方负责。
    job_id 用 uuid4（不再用毫秒时间戳）——同毫秒创建的两个 job 会撞 id，
    而 job_id 是 _job_ui 的 key，撞 id 会串掉彼此卡片的按钮。排序另用 ts 字段。"""
    return JobRecord(job_id=f"job_{uuid.uuid4().hex}",
                     display_name=display_name, ts=ts, model_filter=model_filter)


def update_job(job: JobRecord, **kw) -> None:
    """批量 setattr 更新 job 字段（原 webui._update_job）。"""
    for k, v in kw.items():
        setattr(job, k, v)


def compute_final_status(model_filter: str, b2_path, pro_path) -> str:
    """按 model_filter + 两模型成图路径算最终状态字符串。"""
    if model_filter == 'b2':  return 'done' if b2_path else 'failed'
    if model_filter == 'pro': return 'done' if pro_path else 'failed'
    return 'done' if (b2_path and pro_path) else ('partial' if (b2_path or pro_path) else 'failed')


def job_time_text(job: JobRecord) -> str:
    """队列卡片耗时文案：运行中显示已用秒数 + 各模型实时阶段（🧠思考/🎨渲染/🔁重试）；
    完成后按模型分别显示用时。纯函数，不碰 UI。"""
    def _fmt(s):
        return f'{s:.1f}s' if s is not None else '—'
    # 运行中：已用秒数 + 各模型实时阶段
    if job.status == 'running' and job.started_at:
        base = f'⏱ 已用 {time.time() - job.started_at:.0f}s'
        stages = []
        if job.model_filter in ('b2', 'both') and job.b2_stage:
            stages.append(job.b2_stage if job.model_filter != 'both' else f'B2 {job.b2_stage}')
        if job.model_filter in ('pro', 'both') and job.pro_stage:
            stages.append(job.pro_stage if job.model_filter != 'both' else f'Pro {job.pro_stage}')
        return base + ('　·　' + '　·　'.join(stages) if stages else '')
    has_secs = (job.b2_secs is not None) or (job.pro_secs is not None)
    if has_secs:
        if job.model_filter == 'both':
            return f'⏱ B2 {_fmt(job.b2_secs)} · Pro {_fmt(job.pro_secs)}'
        elif job.model_filter == 'b2':
            return f'⏱ B2 {_fmt(job.b2_secs)}'
        else:
            return f'⏱ Pro {_fmt(job.pro_secs)}'
    return ''


def running_model_status_text(job) -> str:
    """运行中任务的【分模型】状态串，给卡片右上角徽章用：
    某模型已出图(`{m}_path` 有值)→✓；其阶段文案含「排队」(在等模型并发槽)→排队中；否则→生成中。
    按 model_filter 只显示相关模型。例：'B2 生成中 · Pro 排队中'、'B2 ✓ · Pro 生成中'。纯函数、不碰 UI。"""
    parts = []
    for m, name in (('b2', 'B2'), ('pro', 'Pro')):
        if job.model_filter not in (m, 'both'):
            continue
        if getattr(job, f'{m}_path', None):
            s = '✓'
        elif '排队' in (getattr(job, f'{m}_stage', '') or ''):
            s = '排队中'
        else:
            s = '生成中'
        parts.append(f'{name} {s}')
    return ' · '.join(parts)


# ── 重抽候选累积：纯逻辑（无 UI/IO）。slot ∈ CANDIDATE_SLOTS ─────────────────
# 约定：每个 slot 同时有三个属性 f'{slot}_paths'(列表) / f'{slot}_idx'(当前下标) / f'{slot}_path'(当前路径)。
# 注：磨缝结果已并入 'pro' 槽（不再单独成槽/单独图框）——磨缝图作为 Pro 的一张候选，用 ‹n/N› 与原 Pro 切换对比。
# 老持久化里的 pro_polish_* 字段保留(供反序列化)，由 ensure_candidate_lists 迁移并入 pro_paths。
CANDIDATE_SLOTS = ('b2', 'pro')

# 每个 slot 的候选上限：长时间反复重抽会把候选列表无限撑大(浏览器要 hold 的图也随之变多)。
# 超出时丢最旧——磁盘文件不动(记录管理仍可查)，只收窄 ‹n/N› 可翻范围。
MAX_CANDIDATES_PER_SLOT = 12


def ensure_candidate_lists(job: JobRecord) -> None:
    """向后兼容 + 一致性维护：列表空但有单路径(老任务/老持久化)→用单路径回填；
    然后把 *_idx 钳进合法区间，并把 *_path 同步成 *_paths[*_idx]。多次调用幂等。"""
    # 老记录迁移：磨缝结果旧版单独存 pro_polish_*，现已并入 Pro 候选 → 折进 pro_paths 后清空，幂等。
    _legacy = list(job.pro_polish_paths) if job.pro_polish_paths else ([job.pro_polish_path] if job.pro_polish_path else [])
    if _legacy:
        for _p in _legacy:
            if _p and _p not in job.pro_paths:
                job.pro_paths.append(_p)
        job.pro_polish_paths = []
        job.pro_polish_path = None
        job.pro_polish_idx = 0
    for slot in CANDIDATE_SLOTS:
        lst = getattr(job, f'{slot}_paths')
        single = getattr(job, f'{slot}_path')
        if not lst and single:
            lst = [single]
            setattr(job, f'{slot}_paths', lst)
        if lst:
            idx = max(0, min(getattr(job, f'{slot}_idx'), len(lst) - 1))
            setattr(job, f'{slot}_idx', idx)
            setattr(job, f'{slot}_path', lst[idx])


def add_candidate(job: JobRecord, slot: str, path: str) -> int:
    """追加一张新候选到 job.{slot}_paths，并把当前下标/当前路径指向它(最新)。返回新下标。
    生成出口(主生图/重试/重抽/磨缝)统一走这里，使每次成图自动并入候选列表。"""
    lst = getattr(job, f'{slot}_paths')
    lst.append(path)
    if len(lst) > MAX_CANDIDATES_PER_SLOT:        # 超上限丢最旧，列表稳定在 MAX 长度
        del lst[:len(lst) - MAX_CANDIDATES_PER_SLOT]
    setattr(job, f'{slot}_idx', len(lst) - 1)
    setattr(job, f'{slot}_path', path)
    return len(lst) - 1


def nav_candidate(job: JobRecord, slot: str, delta: int) -> tuple:
    """左右切换：把 {slot}_idx 移动 delta(钳到边界)，同步 *_path。返回 (新下标, 总数)。"""
    lst = getattr(job, f'{slot}_paths')
    if not lst:
        return (0, 0)
    idx = max(0, min(getattr(job, f'{slot}_idx') + delta, len(lst) - 1))
    setattr(job, f'{slot}_idx', idx)
    setattr(job, f'{slot}_path', lst[idx])
    return (idx, len(lst))


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

    # ── Omakase mode (自由场景层：AI 原创散文，作为 Omakase 工作流的场景段；仅 Omakase 工作流生效) ──
    scene_override: str = ''


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
        'scene_override': p.scene_override,
    }
