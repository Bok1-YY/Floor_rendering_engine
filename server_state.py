# -*- coding: utf-8 -*-
"""进程内编排状态 —— 三个任务注册表 + 并发信号量 + 后台任务簿记。

所有可变运行时状态集中于此(单 worker 假设,与 serve.py 打包约束一致)。
路由模块一律以 `from . import server_state as state` 引用,经模块属性访问
(state.JOBS / state.model_semaphores / ...),这样测试替换与 lifespan 重建都有唯一注入点。
"""
import asyncio
import os
from typing import Optional

from .config import MAIN_OUTPUT_DIR, logger
from .models import JobRecord
from .records import persist_jobs
from .task_registry import TaskRegistry

# ── 并发槽/锁(在 lifespan 里、于本服务事件循环上创建;绝不在 import 期建,否则绑错 loop)──
# 每个模型最多 max_concurrent_per_model(默认 1) 个进行中任务;B2 / Pro 各一把信号量。
model_semaphores: dict = {}
# prep 串行锁:save_task_files_html 按小样路径派生 png/json 输出路径,同图并发首处理会抢写同一 png。
task_prep_lock: Optional[asyncio.Lock] = None


def init_runtime(concurrency_limit: int) -> None:
    """lifespan 启动钩子调用:创建按模型信号量与 prep 锁(必须绑定服务事件循环)。"""
    global model_semaphores, task_prep_lock
    model_semaphores = {key: asyncio.Semaphore(concurrency_limit) for key in ('b2', 'pro', 'sd35')}
    # 生成式修补独立信号量(恒 1):修补与主生成互不阻塞、也不占 b2/pro 槽
    model_semaphores['inpaint'] = asyncio.Semaphore(1)
    task_prep_lock = asyncio.Lock()


# 后台任务强引用:asyncio 事件循环只对 task 持弱引用,无强引用者可能在完成前被 GC。
# 所有后台生图/重试/重抽/磨缝/二改 task 统一经 spawn() 排程并收进此集合,done 回调里自动清理。
_bg_tasks: set = set()


def spawn(coro):
    """asyncio.create_task + 持强引用直到完成——避免事件循环仅持弱引用导致后台任务被 GC。"""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    def _done(task):
        _bg_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error(f"[后台任务] 未处理异常: {task.exception()}")
    t.add_done_callback(_done)
    return t


# ── 任务队列 ─────────────────────────────────────────────────
# 内存里最多保留 N 条任务卡(与磁盘 QUEUE_PERSIST_MAX 对齐);超出丢最旧的【终态】卡,
# in-flight(queued/running/磨缝中) 永不删。
MAX_RESIDENT_JOBS = 60


def job_is_terminal(j: JobRecord) -> bool:
    """终态 = 已出结果且不在磨缝;queued/running/磨缝中 in-flight 永不被 trim。"""
    return j.status in ('done', 'partial', 'failed') and not j.pro_polishing


# 取消语义与 webui 同义 —— 单任务用取消集合(stop this one);
# 全局用单调代次(stop all:in-flight 任务捕获的旧代次 < 新代次即自行退出)。
# persist 经 records.persist_jobs 落盘(内部会剥掉 retry_ctx 里的 api_key,不存明文)。
JOBS = TaskRegistry('jobs', max_entries=MAX_RESIDENT_JOBS, is_terminal=job_is_terminal,
                    on_persist=persist_jobs, newest_first=True)


def prune_job_output_paths(paths) -> int:
    """Remove explicitly deleted result paths from transient job-card candidates."""
    targets = {
        os.path.normcase(os.path.realpath(str(path)))
        for path in (paths or []) if path
    }
    if not targets:
        return 0
    changed = 0
    with JOBS.locked() as entries:
        for job in entries.values():
            job_changed = False
            for slot in ('b2', 'pro', 'pro_polish'):
                list_name = f'{slot}_paths'
                idx_name = f'{slot}_idx'
                path_name = f'{slot}_path'
                values = list(getattr(job, list_name, []) or [])
                kept = [value for value in values
                        if os.path.normcase(os.path.realpath(str(value))) not in targets]
                current = getattr(job, path_name, None)
                current_deleted = bool(current and os.path.normcase(os.path.realpath(str(current))) in targets)
                if kept != values or current_deleted:
                    idx = max(0, min(int(getattr(job, idx_name, 0) or 0), len(kept) - 1)) if kept else 0
                    setattr(job, list_name, kept)
                    setattr(job, idx_name, idx)
                    setattr(job, path_name, kept[idx] if kept else None)
                    job_changed = True
            for run in (job.model_runs or {}).values():
                if not isinstance(run, dict):
                    continue
                values = list(run.get('paths') or [])
                metas = list(run.get('candidate_meta') or [])
                pairs = [
                    (value, metas[index] if index < len(metas) else {})
                    for index, value in enumerate(values)
                    if os.path.normcase(os.path.realpath(str(value))) not in targets
                ]
                base_path = str(run.get('base_path') or '')
                base_deleted = bool(base_path and os.path.normcase(os.path.realpath(base_path)) in targets)
                if len(pairs) != len(values) or base_deleted:
                    run['paths'] = [value for value, _meta in pairs]
                    run['candidate_meta'] = [meta for _value, meta in pairs]
                    run['index'] = max(0, min(int(run.get('index') or 0), len(pairs) - 1)) if pairs else 0
                    if base_deleted:
                        run['base_path'] = ''
                    job_changed = True
            if job_changed:
                changed += 1
    if changed:
        JOBS.persist()
    return changed


# ── 快速预览(Nano Banana 2 Lite · 1K · 仅 Google 直连)──────────────────
# 与 4K 队列完全解耦:不进 JOBS、不占 b2/pro 信号量、不写记录。pid → 状态快照,前端短轮询。
MAX_PREVIEWS = 20            # 预览是临时草稿,只留最近 N 条终态


def preview_is_terminal(v: dict) -> bool:
    return v.get('status') in ('done', 'failed')


PREVIEWS = TaskRegistry('previews', max_entries=MAX_PREVIEWS, is_terminal=preview_is_terminal)


# ── 生成式修补会话表 ─────────────────────────────────────────
MAX_INPAINTS = 20
MAX_ACTIVE_INPAINTS = 3       # 1 个执行 + 最多 2 个等待,避免付费任务无限堆积
INPAINT_TMP_DIR = os.path.join(MAIN_OUTPUT_DIR, '_inpaint_candidates')


def delete_inpaint_files(entry) -> None:
    for cand in (entry or {}).get('candidates') or []:
        try:
            p = cand.get('path')
            if p and os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


def inpaint_is_terminal(v: dict) -> bool:
    return v.get('status') in ('done', 'failed', 'cancelled')


# iid → {status, stage, candidates, error, ts, target, mode, prompt};trim 连带删临时候选文件
INPAINTS = TaskRegistry('inpaints', max_entries=MAX_INPAINTS,
                        is_terminal=inpaint_is_terminal, on_evict=delete_inpaint_files)


def inpaint_queue_is_full(entries) -> bool:
    """在 INPAINTS.locked() 块内检查运行中背压与会话表硬上限。"""
    active = sum(v.get('status') in ('running', 'applying') for v in entries.values())
    return active >= MAX_ACTIVE_INPAINTS or len(entries) >= MAX_INPAINTS
