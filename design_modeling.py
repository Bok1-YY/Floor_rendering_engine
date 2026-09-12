"""Whole-home design: modeling."""

import json
import os
import time
from copy import deepcopy
from pathlib import Path


from .config import logger



from .design_provider import (
    evaluate_structure
)
from .design_schema import (
    QA_PROMPT_VERSION
)
from .design_store import (
    MODEL_ROOT,
    _project_lock,
    file_sha256,
    list_projects,
    load_project,
    new_id,
    save_project
)

def recover_interrupted_projects() -> list[tuple[str, str]]:
    resumable: list[tuple[str, str]] = []
    for project in list_projects(200):
        changed = False
        project_has_resume = False
        for candidate in project.get("candidates") or []:
            if candidate.get("status") in ("queued", "running"):
                handle = candidate.get("queue_handle") or {}
                if candidate.get("provider") == "fal" and handle.get("request_id"):
                    candidate["status"] = "interrupted"
                    candidate["stage"] = "等待恢复已有 Fal 队列任务"
                    resumable.append((project["project_id"], candidate["candidate_id"]))
                    project_has_resume = True
                else:
                    candidate["status"] = "failed"
                    candidate["error"] = "程序重启时调用状态未知；未自动重新付费提交"
                changed = True
        for model_run in project.get("model_runs") or []:
            if model_run.get("status") in {"queued", "building"}:
                model_run["status"] = "interrupted"
                model_run["stage"] = "程序重启中断了本地 Blender 子进程；可以重新生成，不会重复付费"
                model_run["error"] = ""
                model_run["stale"] = True
                model_run["stale_reason"] = "程序重启中断了本地建模子进程"
                model_run["updated_at"] = time.time()
                changed = True
        if project.get("status") in ("analyzing_plan", "verifying_plan"):
            project.update(status="needs_anchor_review", stage="识别已中断，请重新发起识别", analysis_operation_id="")
            changed = True
        if project.get("status") in ("generating_drafts", "refining"):
            project["status"] = "interrupted" if project_has_resume else "failed"
            changed = True
        if changed:
            save_project(project)
    return resumable

def create_model_run_record(project: dict) -> dict:
    review = project.get("structure_review") or {}
    if review.get("status") != "verified" or not review.get("structure_bundle") or not review.get("structure_hash"):
        raise ValueError("九问和技术结构图尚未通过研究建模门")
    structure_hash = str(review["structure_hash"])
    existing = next((row for row in reversed(project.get("model_runs") or [])
                     if row.get("structure_hash") == structure_hash and not row.get("stale")
                     and row.get("status") not in {"failed_product", "cancelled", "interrupted"}), None)
    if existing:
        return existing
    run_id = new_id("model")
    now = time.time()
    row = {
        "run_id": run_id,
        "status": "queued",
        "stage": "等待本地 Blender 研究建模",
        "error": "",
        "structure_hash": structure_hash,
        "structure_bundle": deepcopy(review["structure_bundle"]),
        "output_root": os.path.join(MODEL_ROOT, run_id[-6:]),
        "score": None,
        "artifacts": [],
        "unresolved": list(review.get("unresolved") or []),
        "mechanical_report": {},
        "gemini_review": {"version": QA_PROMPT_VERSION, "status": "not_run", "hard_fail": False, "summary": "", "checks": [], "provider": ""},
        "stale": False,
        "created_at": now,
        "updated_at": now,
    }
    project.setdefault("model_runs", []).append(row)
    return row

def _model_row(project: dict, run_id: str) -> dict:
    row = next((value for value in project.get("model_runs") or [] if value.get("run_id") == run_id), None)
    if not row:
        raise ValueError("研究建模任务不存在")
    return row

def _normalize_model_artifacts(result: dict) -> list[dict]:
    raw = result.get("artifacts") or {}
    rows: list[dict] = []
    if isinstance(raw, dict):
        iterable = [({"kind": key, **(value if isinstance(value, dict) else {"path": value})}) for key, value in raw.items()]
    elif isinstance(raw, list):
        iterable = raw
    else:
        iterable = []
    for item in iterable:
        path = str(item.get("path") or "")
        if not path or not os.path.isfile(path):
            continue
        filename = os.path.basename(path)
        kind_map = {
            "scene.blend": "blend", "scene.glb": "glb", "research.ifc": "ifc",
            "top.png": "top", "north-east.png": "north_east", "north-west.png": "north_west",
            "mechanical-report.json": "mechanical_report", "model-report.json": "model_report",
            "ifc-report.json": "ifc_report", "unresolved-issues.json": "unresolved_report",
        }
        if filename not in kind_map:
            continue
        rows.append({
            "kind": kind_map[filename],
            "filename": filename,
            "path": path,
            "bytes": int(item.get("bytes") or os.path.getsize(path)),
            "sha256": str(item.get("sha256") or file_sha256(path)),
        })
    return rows

def run_model_job(project_id: str, run_id: str) -> dict:
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise ValueError("全屋设计项目不存在")
        row = _model_row(project, run_id)
        if project.get("cancel_requested") or row.get("stale") or row.get("status") not in {"queued", "interrupted"}:
            return row
        row.update(status="building", stage="正在生成 Blender、GLB 和研究 IFC", error="", updated_at=time.time())
        bundle = deepcopy(row["structure_bundle"])
        output_root = row["output_root"]
        source_revision = project["revision"]
        save_project(project)
    try:
        from .tools.fastloop_research import run_research_model
        from .tools.fastloop_research.engine import cancellation_scope
        from . import server_state as state
        def cancelled():
            current = load_project(project_id) or {}
            return state.background.stopping.is_set() or bool(current.get("cancel_requested"))
        with cancellation_scope(cancelled):
            result = run_research_model(bundle, Path(output_root))
    except Exception as exc:
        logger.exception("全屋研究建模失败 project=%s run=%s", project_id, run_id)
        result = {"status": "failed_product", "error": f"{type(exc).__name__}: {exc}", "artifacts": {}}
    artifacts = _normalize_model_artifacts(result)
    result_status = str(result.get("status") or "failed_product")
    top_path = next((item["path"] for item in artifacts if item["kind"] == "top"), "")
    axon_paths = [item["path"] for item in artifacts if item["kind"] in {"north_east", "north_west"}]
    with _project_lock(project_id):
        current = load_project(project_id) or {}
        eligible = (not current.get("cancel_requested") and current.get("revision") == source_revision
                    and not _model_row(current, run_id).get("stale"))
    qa = None
    if eligible and top_path and result_status in {"mechanical_verified", "blocked_dependency_missing"}:
        qa = evaluate_structure(project, top_path, axon_paths)
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise ValueError("全屋设计项目不存在")
        row = _model_row(project, run_id)
        artifacts = _normalize_model_artifacts(result)
        row["artifacts"] = artifacts
        if project.get("cancel_requested") or project.get("revision") != source_revision or row.get("stale"):
            row.update(status="cancelled" if project.get("cancel_requested") else "interrupted",
                       stale=True, stale_reason="建模期间项目已更新或取消", stage="已停止更新当前项目")
            save_project(project)
            return row
        mechanical_path = next((item["path"] for item in artifacts if item["kind"] == "mechanical_report"), "")
        if mechanical_path:
            try:
                with open(mechanical_path, "r", encoding="utf-8") as handle:
                    row["mechanical_report"] = json.load(handle)
            except (OSError, ValueError, TypeError):
                row["mechanical_report"] = {}
        row["unresolved"] = list(dict.fromkeys([*(row.get("unresolved") or []), *(result.get("unresolved") or [])]))
        result_status = str(result.get("status") or "failed_product")
        if result_status in {"mechanical_verified", "blocked_dependency_missing"}:
            top_path = next((item["path"] for item in artifacts if item["kind"] == "top"), "")
            axon_paths = [item["path"] for item in artifacts if item["kind"] in {"north_east", "north_west"}]
            if result_status == "blocked_dependency_missing" and not top_path:
                row.update(status="blocked_dependency_missing", stage="本地建模依赖缺失", error=str(result.get("message") or "缺少 Blender"))
                row["updated_at"] = time.time()
                project["stage"] = row["stage"]
                save_project(project)
                return row
            qa = qa or {
                "version": QA_PROMPT_VERSION, "status": "manual_required", "hard_fail": False,
                "summary": "顶视图缺失，不能运行复合审查", "checks": [], "provider": "local_missing_artifact",
            }
            row["gemini_review"] = qa
            if result_status == "blocked_dependency_missing":
                row.update(status="blocked_dependency_missing", stage="Blender 研究模型已生成；研究 IFC 依赖缺失", error=str(result.get("message") or "缺少 IfcOpenShell"))
            elif qa.get("status") == "passed":
                row.update(status="ready_research", stage="研究灰模已通过机械与 Gemini 审查", error="")
            elif qa.get("status") == "failed" or qa.get("hard_fail"):
                row.update(status="needs_correction", stage="Gemini 发现结构差异，需要校正", error=str(qa.get("summary") or ""))
            else:
                row.update(status="external_review_pending", stage="本地研究模型可用；等待 Gemini 复合审查", error=str(qa.get("summary") or ""))
        else:
            row.update(status="failed_product", stage="研究建模失败", error=str(result.get("error") or "本地建模内核失败"))
        row["updated_at"] = time.time()
        project["status"] = "locked" if project.get("locked_candidate_id") else project.get("status")
        project["stage"] = row["stage"]
        save_project(project)
        return row

def retry_model_review(project_id: str, run_id: str) -> dict:
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise ValueError("全屋设计项目不存在")
        row = _model_row(project, run_id)
        revision = project["revision"]
        top_path = next((item.get("path") for item in row.get("artifacts") or [] if item.get("kind") == "top"), "")
        axon_paths = [item.get("path") for item in row.get("artifacts") or [] if item.get("kind") in {"north_east", "north_west"}]
    if not top_path or not os.path.isfile(top_path):
        raise ValueError("研究模型顶视图不存在")
    qa = evaluate_structure(project, top_path, axon_paths)
    with _project_lock(project_id):
        project = load_project(project_id)
        row = _model_row(project, run_id)
        if project.get("revision") != revision or project.get("cancel_requested") or row.get("stale"):
            return row
        row["gemini_review"] = qa
        if qa.get("status") == "passed":
            row.update(status="ready_research", stage="研究灰模已通过 Gemini 复合审查", error="")
        elif qa.get("status") == "failed" or qa.get("hard_fail"):
            row.update(status="needs_correction", stage="Gemini 发现结构差异，需要校正", error=str(qa.get("summary") or ""))
        else:
            row.update(status="external_review_pending", stage="本地研究模型可用；等待 Gemini 复合审查", error=str(qa.get("summary") or ""))
        row["updated_at"] = time.time()
        save_project(project)
        return row
