# 任务数据模型（从 webui 抽出，便于复用与单测；纯数据，无 UI 依赖）
from dataclasses import dataclass
from typing import Optional


@dataclass
class JobRecord:
    job_id: str; display_name: str; ts: str; status: str = 'queued'
    model_filter: str = 'both'  # "b2" | "pro" | "both"
    b2_path: Optional[str] = None; pro_path: Optional[str] = None; error: str = ''
    json_path: str = ''; record_id: str = ''; png_path: str = ''
    started_at: Optional[float] = None  # 开始生成的 epoch 时间
    b2_secs: Optional[float] = None; pro_secs: Optional[float] = None  # 各模型耗时（秒）
    pro_polish_path: Optional[str] = None  # 手动磨缝后的图（按需，仅 Pro）
    pro_polishing: bool = False            # 磨缝进行中标志（防重复点击）
