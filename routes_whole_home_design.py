"""FastAPI routes for raster-first whole-home concept design."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from typing import Literal, Optional

import fitz
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .config import GEMINI_MODEL_MAP, UPLOAD_DIR, get_usage_prices, load_config, logger
from .server_helpers import require_upload_image_path, result_thumb_url, to_url
from .usage_stats import record_usage
from .whole_home_design import (
    STRUCTURE_REVIEW_ITEMS,
    _brief_hash,
    _project_lock,
    _stable_hash,
    analyze_plan,
    build_design_prompt,
    build_modeling_bundle,
    call_design_image,
    create_project,
    evaluate_structure,
    file_sha256,
    list_projects,
    load_project,
    mark_candidates_stale,
    new_id,
    public_project,
    recover_interrupted_projects,
    save_project,
)

router = APIRouter()
_TASKS: set[asyncio.Task] = set()


def _track(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


class ProjectCreateRequest(BaseModel):
    floorplan_path: str = Field(min_length=1, max_length=2000)
    source_name: str = Field(default="", max_length=300)


class PlanSummaryPutRequest(BaseModel):
    base_revision: int = Field(ge=1)
    room_count: int = Field(ge=0, le=80)
    rooms: list[dict] = Field(default_factory=list, max_length=80)
    declared_layout: dict = Field(default_factory=dict)
    declared_area_m2: float = Field(default=0.0, ge=0.0, le=10000.0)
    overall_dimensions_mm: dict = Field(default_factory=dict)
    summary_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    review_items: list[dict] = Field(default_factory=list, max_length=100)
    annotation_boxes: list[dict] = Field(default_factory=list, max_length=200)
    entrances: list[str] = Field(default_factory=list, max_length=20)
    openings_summary: list[str] = Field(default_factory=list, max_length=120)
    wet_zones: list[str] = Field(default_factory=list, max_length=40)
    balconies: list[str] = Field(default_factory=list, max_length=20)
    dimension_evidence: list[str] = Field(default_factory=list, max_length=80)
    must_preserve: list[str] = Field(default_factory=list, max_length=80)
    uncertainties: list[str] = Field(default_factory=list, max_length=80)
    confirmed: bool = True


class BriefPutRequest(BaseModel):
    base_revision: int = Field(ge=1)
    requirements_text: str = Field(min_length=1, max_length=5000)
    reference_paths: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("requirements_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("设计要求不能为空")
        return value


class PreviewRequest(BaseModel):
    base_revision: int = Field(ge=1)


class CommitRequest(BaseModel):
    base_revision: int = Field(ge=1)
    preview_id: str = Field(min_length=1, max_length=120)
    preview_hash: str = Field(min_length=32, max_length=128)
    confirmation_phrase: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=160)


class RefinePreviewRequest(PreviewRequest):
    refinement_text: str = Field(default="", max_length=2000)


class StructureReviewRequest(BaseModel):
    base_revision: int = Field(ge=1)
    checks: dict[str, bool]
    decision: Literal["pass", "fail"] = "pass"
    reviewer: str = Field(default="local-user", min_length=1, max_length=100)
    note: str = Field(default="", max_length=2000)


class CandidateActionRequest(BaseModel):
    base_revision: int = Field(ge=1)


def _project_or_404(project_id: str) -> dict:
    project = load_project(project_id)
    if not project:
        raise HTTPException(404, "全屋设计项目不存在")
    return project


def _assert_revision(project: dict, revision: int) -> None:
    current = int(project.get("revision") or 0)
    if current != int(revision):
        raise HTTPException(409, {
            "code": "design_revision_conflict",
            "message": "项目已在其他页面更新，请刷新后重试",
            "current_revision": current,
        })


def _validate_plan_summary_payload(summary: dict, confirmed: bool) -> None:
    if not confirmed:
        return
    rooms = list(summary.get("rooms") or [])
    room_count = int(summary.get("room_count") or 0)
    if room_count < 1 or not rooms:
        raise HTTPException(422, {
            "code": "empty_plan_summary",
            "message": "不能确认空户型摘要；请先自动识别或至少添加一个空间",
        })
    if room_count != len(rooms):
        raise HTTPException(422, {
            "code": "room_count_mismatch", "room_count": room_count, "room_rows": len(rooms),
        })
    identifiers: list[str] = []
    for index, room in enumerate(rooms):
        room_id = str(room.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", room_id):
            raise HTTPException(422, {"code": "invalid_room_id", "index": index, "room_id": room_id})
        if not str(room.get("label") or "").strip() or not str(room.get("room_type") or "").strip():
            raise HTTPException(422, {"code": "incomplete_room", "room_id": room_id})
        identifiers.append(room_id)
    if len(set(identifiers)) != len(identifiers):
        raise HTTPException(422, {"code": "duplicate_room_id"})
    known = set(identifiers)
    unknown = sorted({
        str(adjacent) for room in rooms for adjacent in (room.get("adjacent_room_ids") or [])
        if str(adjacent) not in known
    })
    if unknown:
        raise HTTPException(422, {"code": "unknown_adjacent_room_ids", "unknown": unknown})


def _candidate(project: dict, candidate_id: str) -> dict:
    row = next((item for item in project.get("candidates") or []
                if item.get("candidate_id") == candidate_id), None)
    if not row:
        raise HTTPException(404, "设计候选不存在")
    return row


def _safe_upload_name(filename: str, prefix: str, ext: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or prefix))[0]
    stem = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", stem).strip("._")[:80] or prefix
    return f"{prefix}{stem}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"


def _write_upload(file: UploadFile, destination: str, max_bytes: int = 50 * 1024 * 1024) -> None:
    written = 0
    try:
        with open(destination, "xb") as handle:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(413, "户型或参考图超过 50 MiB 上限")
                handle.write(chunk)
    except Exception:
        if os.path.exists(destination):
            try:
                os.unlink(destination)
            except OSError:
                pass
        raise


@router.post("/api/uploads/design-floorplan")
def upload_design_floorplan(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".pdf"):
        raise HTTPException(400, "户型图仅支持 PNG/JPG/WebP/PDF")
    name = _safe_upload_name(file.filename or "floorplan", "design_plan_", ext)
    destination = os.path.join(UPLOAD_DIR, name)
    _write_upload(file, destination)
    if ext != ".pdf":
        path = require_upload_image_path(destination, "户型图", required=True)
        return {"kind": "image", "name": file.filename or name, "path": path,
                "url": to_url(path), "thumb": result_thumb_url(path), "pages": []}
    pages = []
    try:
        document = fitz.open(destination)
        if document.page_count < 1 or document.page_count > 60:
            raise HTTPException(400, "PDF 页数必须在 1–60 页之间")
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            page_name = _safe_upload_name(f"{os.path.splitext(name)[0]}_p{index + 1}", "", ".png")
            page_path = os.path.join(UPLOAD_DIR, page_name)
            pixmap.save(page_path)
            pages.append({"page": index + 1, "path": page_path, "url": to_url(page_path),
                          "thumb": result_thumb_url(page_path), "width": pixmap.width, "height": pixmap.height})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"PDF 解析失败: {exc}") from exc
    return {"kind": "pdf", "name": file.filename or name, "path": destination, "pages": pages}


@router.post("/api/uploads/design-reference")
def upload_design_reference(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "参考图仅支持 PNG/JPG/WebP")
    name = _safe_upload_name(file.filename or "reference", "design_ref_", ext)
    destination = os.path.join(UPLOAD_DIR, name)
    _write_upload(file, destination)
    path = require_upload_image_path(destination, "设计参考图", required=True)
    return {"name": file.filename or name, "path": path, "url": to_url(path),
            "thumb": result_thumb_url(path), "sha256": file_sha256(path)}


@router.post("/api/whole-home-design/projects")
async def create_design_project(req: ProjectCreateRequest):
    source = require_upload_image_path(req.floorplan_path, "户型图", required=True)
    project = await asyncio.to_thread(create_project, source, req.source_name)
    if str(load_config().get("gemini_api_key") or "").strip():
        project["status"] = "analyzing_plan"
        project["stage"] = "Gemini 正在生成轻量户型摘要"
        save_project(project)
        _track(asyncio.to_thread(analyze_plan, project["project_id"]))
    return public_project(project)


@router.get("/api/whole-home-design/projects")
def get_design_projects(limit: int = 50):
    return [public_project(row, list_mode=True) for row in list_projects(limit)]


@router.get("/api/whole-home-design/projects/{project_id}")
def get_design_project(project_id: str):
    return public_project(_project_or_404(project_id))


@router.post("/api/whole-home-design/projects/{project_id}/analyze-plan")
async def retry_design_plan_analysis(project_id: str, req: PreviewRequest):
    if not str(load_config().get("gemini_api_key") or "").strip():
        raise HTTPException(409, {
            "code": "gemini_key_required",
            "message": "自动户型摘要需要先在设置页配置 Gemini API Key",
        })
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        project["revision"] += 1
        project["plan_summary_confirmed"] = False
        mark_candidates_stale(project, "重新自动识别户型摘要")
        project["status"] = "analyzing_plan"
        project["stage"] = "Gemini 正在重新生成户型摘要"
        project["error"] = ""
        save_project(project)
        revision = project["revision"]
    _track(asyncio.to_thread(analyze_plan, project_id))
    response = public_project(project)
    response["analysis_revision"] = revision
    return response


@router.put("/api/whole-home-design/projects/{project_id}/plan-summary")
def save_plan_summary(project_id: str, req: PlanSummaryPutRequest):
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        summary = req.model_dump(exclude={"base_revision", "confirmed"})
        _validate_plan_summary_payload(summary, req.confirmed)
        previous_source = str((project.get("plan_summary") or {}).get("source") or "human")
        source = "human_confirmed_ai" if previous_source == "gemini" else "human"
        summary.update(version="plan-summary-v1", source=source,
                       prompt_version="whole-home-plan-summary-v1")
        project["plan_summary"] = summary
        project["plan_summary_confirmed"] = bool(req.confirmed)
        project["revision"] += 1
        mark_candidates_stale(project, "户型摘要已更新")
        project["brief_hash"] = _brief_hash(project)
        project["status"] = "ready" if req.confirmed and project.get("brief", {}).get("requirements_text") else "needs_brief"
        project["stage"] = "户型摘要已确认" if req.confirmed else "等待确认户型摘要"
        project["error"] = ""
        save_project(project)
        return public_project(project)


@router.put("/api/whole-home-design/projects/{project_id}/brief")
def save_design_brief(project_id: str, req: BriefPutRequest):
    references = [require_upload_image_path(path, "设计参考图", required=True)
                  for path in req.reference_paths]
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        project["brief"] = {
            "requirements_text": req.requirements_text,
            "reference_paths": references,
            "reference_hashes": [file_sha256(path) for path in references],
        }
        project["revision"] += 1
        mark_candidates_stale(project, "设计要求或参考图已更新")
        project["brief_hash"] = _brief_hash(project)
        project["status"] = "ready" if project.get("plan_summary_confirmed") else "needs_plan_review"
        project["stage"] = "可以生成两张设计草稿" if project["status"] == "ready" else "请先确认户型摘要"
        project["error"] = ""
        save_project(project)
        return public_project(project)


def _price(model_label: str, count: int) -> Optional[float]:
    try:
        value = get_usage_prices().get(model_label)
        return round(float(value) * count, 4) if value is not None else None
    except Exception:
        return None


def _create_preview(project: dict, *, kind: str, candidate_id: str = "",
                    refinement_text: str = "") -> dict:
    provider = str(load_config().get("image_provider") or "google").strip().lower()
    if kind == "drafts":
        model_label, calls, resolution, phrase = "Nano Banana 2", 2, "2K", "生成2张全屋设计草稿"
    else:
        model_label, calls, resolution, phrase = "Nano Banana Pro", 1, "4K", "精修1张全屋设计成稿"
    model_id = GEMINI_MODEL_MAP[model_label]
    preview_id = new_id("design_preview")
    payload = {
        "preview_id": preview_id,
        "kind": kind,
        "project_id": project["project_id"],
        "project_revision": project["revision"],
        "source_hash": project["source_hash"],
        "generation_hash": project.get("generation_hash") or "",
        "brief_hash": project["brief_hash"],
        "candidate_id": candidate_id,
        "refinement_text": refinement_text.strip(),
        "provider": provider,
        "model_label": model_label,
        "model_id": model_id,
        "call_count": calls,
        "resolution": resolution,
        "aspect_ratio": project["normalization"]["aspect_ratio"],
        "estimated_cost": _price("B2" if kind == "drafts" else "Pro", calls),
        "confirmation_phrase": phrase,
        "created_at": time.time(),
        "expires_at": time.time() + 30 * 60,
        "status": "previewed",
        "idempotency_key": "",
    }
    payload["preview_hash"] = _stable_hash({key: value for key, value in payload.items()
                                             if key not in ("confirmation_phrase", "preview_hash", "status")})
    project.setdefault("paid_previews", {})[preview_id] = payload
    project["status"] = "draft_previewed" if kind == "drafts" else "refine_previewed"
    project["stage"] = "等待付费确认"
    save_project(project)
    return payload


@router.post("/api/whole-home-design/projects/{project_id}/drafts/preview")
def preview_design_drafts(project_id: str, req: PreviewRequest):
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        if not project.get("plan_summary_confirmed") or not project.get("brief", {}).get("requirements_text"):
            raise HTTPException(409, "请先确认户型摘要并填写设计要求")
        return _create_preview(project, kind="drafts")


def _validate_commit(project: dict, req: CommitRequest, kind: str) -> dict:
    _assert_revision(project, req.base_revision)
    preview = (project.get("paid_previews") or {}).get(req.preview_id)
    if not preview or preview.get("kind") != kind:
        raise HTTPException(404, "付费预览不存在")
    if preview.get("preview_hash") != req.preview_hash:
        raise HTTPException(409, "付费预览哈希不匹配")
    if preview.get("confirmation_phrase") != req.confirmation_phrase:
        raise HTTPException(400, "确认短语不正确")
    if time.time() > float(preview.get("expires_at") or 0):
        raise HTTPException(409, "付费预览已过期，请重新预览")
    if (preview.get("project_revision") != project.get("revision")
            or preview.get("source_hash") != project.get("source_hash")
            or preview.get("generation_hash") != project.get("generation_hash")
            or preview.get("brief_hash") != project.get("brief_hash")):
        raise HTTPException(409, "项目输入已变化，请重新生成付费预览")
    if preview.get("status") == "committed":
        if preview.get("idempotency_key") == req.idempotency_key:
            return preview
        raise HTTPException(409, "该付费预览已经提交")
    preview["status"] = "committed"
    preview["idempotency_key"] = req.idempotency_key
    preview["committed_at"] = time.time()
    return preview


def _new_candidate(project: dict, preview: dict, *, phase: Literal["draft", "final"],
                   direction_index: int = 1, parent_id: str = "") -> dict:
    candidate_id = new_id("draft" if phase == "draft" else "final")
    return {
        "candidate_id": candidate_id,
        "phase": phase,
        "direction_index": direction_index,
        "parent_candidate_id": parent_id,
        "status": "queued",
        "stage": "等待生成",
        "error": "",
        "path": "",
        "provider": preview["provider"],
        "model_label": preview["model_label"],
        "model_id": preview["model_id"],
        "endpoint": "",
        "resolution": preview["resolution"],
        "aspect_ratio": preview["aspect_ratio"],
        "source_hash": project["source_hash"],
        "generation_hash": project.get("generation_hash") or "",
        "brief_hash": project["brief_hash"],
        "project_revision": project["revision"],
        "prompt_version": "whole-home-birdseye-v1",
        "prompt": "",
        "result_hash": "",
        "image_size": [],
        "structure_qa": {"status": "not_run", "hard_fail": False, "checks": []},
        "human_review": {"status": "pending", "checks": {}, "reviewer": "", "note": ""},
        "stale": False,
        "created_at": time.time(),
    }


@router.post("/api/whole-home-design/projects/{project_id}/drafts/commit")
async def commit_design_drafts(project_id: str, req: CommitRequest):
    should_start = False
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        preview = _validate_commit(project, req, "drafts")
        existing_ids = list(preview.get("candidate_ids") or [])
        if not existing_ids:
            rows = [_new_candidate(project, preview, phase="draft", direction_index=index)
                    for index in (1, 2)]
            project.setdefault("candidates", []).extend(rows)
            existing_ids = [row["candidate_id"] for row in rows]
            preview["candidate_ids"] = existing_ids
        queued = [row for row in project.get("candidates") or []
                  if row.get("candidate_id") in existing_ids and row.get("status") == "queued"]
        if queued and not preview.get("background_started_at"):
            preview["background_started_at"] = time.time()
            should_start = True
        project.update(status="generating_drafts", stage="正在生成两张 2K 全屋设计草稿", error="")
        save_project(project)
    if should_start:
        _track(_run_candidate_batch(project_id, existing_ids, preview))
    return public_project(project)


@router.post("/api/whole-home-design/projects/{project_id}/candidates/{candidate_id}/refine/preview")
def preview_design_refine(project_id: str, candidate_id: str, req: RefinePreviewRequest):
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        row = _candidate(project, candidate_id)
        if row.get("phase") != "draft" or row.get("status") != "done" or row.get("stale"):
            raise HTTPException(409, "只能精修当前版本已完成的草稿")
        qa = row.get("structure_qa") or {}
        human = row.get("human_review") or {}
        if qa.get("hard_fail") or (qa.get("status") != "passed" and human.get("status") != "passed"):
            raise HTTPException(409, "草稿尚未通过结构检查")
        return _create_preview(project, kind="refine", candidate_id=candidate_id,
                               refinement_text=req.refinement_text)


@router.post("/api/whole-home-design/projects/{project_id}/candidates/{candidate_id}/refine/commit")
async def commit_design_refine(project_id: str, candidate_id: str, req: CommitRequest):
    should_start = False
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        preview = _validate_commit(project, req, "refine")
        if preview.get("candidate_id") != candidate_id:
            raise HTTPException(409, "精修预览与草稿不匹配")
        existing_ids = list(preview.get("candidate_ids") or [])
        if not existing_ids:
            row = _new_candidate(project, preview, phase="final", parent_id=candidate_id)
            project.setdefault("candidates", []).append(row)
            existing_ids = [row["candidate_id"]]
            preview["candidate_ids"] = existing_ids
        queued = [row for row in project.get("candidates") or []
                  if row.get("candidate_id") in existing_ids and row.get("status") == "queued"]
        if queued and not preview.get("background_started_at"):
            preview["background_started_at"] = time.time()
            should_start = True
        project.update(status="refining", stage="正在精修 4K 全屋设计成稿", error="")
        save_project(project)
    if should_start:
        _track(_run_candidate_batch(project_id, existing_ids, preview))
    return public_project(project)


async def _run_candidate_batch(project_id: str, candidate_ids: list[str], preview: dict) -> None:
    await asyncio.gather(*[
        asyncio.to_thread(_generate_one_candidate, project_id, candidate_id, preview, False)
        for candidate_id in candidate_ids
    ])
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            return
        rows = [_candidate(project, candidate_id) for candidate_id in candidate_ids]
        done = [row for row in rows if row.get("status") == "done"]
        if preview["kind"] == "drafts":
            project["status"] = "needs_draft_selection" if done else "failed"
            project["stage"] = "请选择结构检查通过的草稿" if done else "两张草稿均生成失败"
        else:
            project["status"] = "needs_structure_review" if done else "failed"
            project["stage"] = "请完成成稿结构核对" if done else "4K 精修失败"
        project["error"] = "" if done else "本次设计生成没有可用结果"
        save_project(project)


def _generate_one_candidate(project_id: str, candidate_id: str, preview: dict,
                            resume: bool) -> None:
    started = time.time()
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            return
        row = _candidate(project, candidate_id)
        parent = next((item for item in project.get("candidates") or []
                       if item.get("candidate_id") == row.get("parent_candidate_id")), None)
        phase = row["phase"]
        prompt = build_design_prompt(
            project, phase=phase, direction_index=int(row.get("direction_index") or 1),
            refinement_text=str(preview.get("refinement_text") or ""),
        )
        image_paths = [project.get("generation_path") or project["normalized_path"]]
        if parent and parent.get("path"):
            image_paths.append(parent["path"])
        image_paths.extend(project.get("brief", {}).get("reference_paths") or [])
        row.update(status="running", stage="正在连接图像模型", error="", prompt=prompt,
                   started_at=started)
        save_project(project)

    def cancelled() -> bool:
        current = load_project(project_id) or {}
        return bool(current.get("cancel_requested"))

    def stage(text: str) -> None:
        with _project_lock(project_id):
            current = load_project(project_id)
            if current:
                current_row = _candidate(current, candidate_id)
                current_row["stage"] = str(text or "")
                save_project(current)

    def submitted(handle: dict) -> None:
        with _project_lock(project_id):
            current = load_project(project_id)
            if current:
                current_row = _candidate(current, candidate_id)
                current_row["queue_handle"] = dict(handle)
                current_row["provider_request_id"] = str(handle.get("request_id") or "")
                save_project(current)

    resume_handle = row.get("queue_handle") if resume else None
    image, error, provider, endpoint = call_design_image(
        model_id=row["model_id"], prompt=prompt, image_paths=image_paths,
        resolution=row["resolution"], aspect_ratio=row["aspect_ratio"],
        should_cancel=cancelled, on_stage=stage, on_submitted=submitted,
        resume_handle=resume_handle,
    )
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            return
        row = _candidate(project, candidate_id)
        row["provider"] = provider
        row["endpoint"] = endpoint
        row["seconds"] = round(time.time() - started, 2)
        if image is None:
            row.update(status="cancelled" if cancelled() else "failed", stage="", error=error or "模型未返回图片")
            record_usage("全屋设计", row["model_label"], provider, False, "generate")
            save_project(project)
            return
        minimum_long_edge = 1800 if row["resolution"] == "2K" else 3500
        if max(image.size) < minimum_long_edge:
            row.update(status="failed", stage="", error=f"模型返回 {image.width}×{image.height}，未达到 {row['resolution']} 合同")
            record_usage("全屋设计", row["model_label"], provider, False, "generate")
            save_project(project)
            return
        from .whole_home_design import _save_candidate_image
        path = _save_candidate_image(project_id, candidate_id, image)
        row.update(path=path, result_hash=file_sha256(path), image_size=list(image.size),
                   status="qa_running", stage="正在核对户型结构", error="")
        record_usage("全屋设计", row["model_label"], provider, True, "generate")
        save_project(project)
    qa = evaluate_structure(project, path)
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            return
        row = _candidate(project, candidate_id)
        row["structure_qa"] = qa
        row["status"] = "done"
        row["stage"] = "结构检查失败" if qa.get("hard_fail") else "等待人工选择或核对"
        save_project(project)


@router.put("/api/whole-home-design/projects/{project_id}/candidates/{candidate_id}/structure-review")
def review_candidate_structure(project_id: str, candidate_id: str, req: StructureReviewRequest):
    missing = [item for item in STRUCTURE_REVIEW_ITEMS if req.checks.get(item) is not True]
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        row = _candidate(project, candidate_id)
        if row.get("status") != "done" or row.get("stale"):
            raise HTTPException(409, "只能核对当前版本已完成的候选")
        if (row.get("structure_qa") or {}).get("hard_fail"):
            raise HTTPException(409, {
                "code": "automated_structure_hard_fail",
                "message": "自动结构 QA 已发现硬错误；不能人工覆写，请重新生成",
                "qa": row.get("structure_qa"),
            })
        if req.decision == "fail":
            if not req.note.strip():
                raise HTTPException(422, {"code": "structure_failure_note_required"})
            row["human_review"] = {
                "status": "failed", "checks": dict(req.checks),
                "reviewer": req.reviewer.strip(), "note": req.note.strip(),
                "reviewed_at": time.time(),
            }
            row["structure_qa"] = {
                **(row.get("structure_qa") or {}),
                "status": "failed", "hard_fail": True, "provider": "human",
                "summary": req.note.strip(),
            }
            row["stage"] = "人工结构核对失败"
            save_project(project)
            return public_project(project)
        if missing:
            raise HTTPException(409, {"code": "structure_review_incomplete", "missing": missing})
        row["human_review"] = {
            "status": "passed",
            "checks": {item: True for item in STRUCTURE_REVIEW_ITEMS},
            "reviewer": req.reviewer.strip(),
            "note": req.note.strip(),
            "reviewed_at": time.time(),
        }
        if (row.get("structure_qa") or {}).get("status") in ("manual_required", "not_run"):
            row["structure_qa"]["status"] = "passed"
            row["structure_qa"]["provider"] = "human"
        save_project(project)
        return public_project(project)


@router.post("/api/whole-home-design/projects/{project_id}/candidates/{candidate_id}/lock")
def lock_design_candidate(project_id: str, candidate_id: str, req: CandidateActionRequest):
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        row = _candidate(project, candidate_id)
        qa = row.get("structure_qa") or {}
        human = row.get("human_review") or {}
        if row.get("phase") != "final" or row.get("status") != "done" or row.get("stale"):
            raise HTTPException(409, "只能锁定当前版本已完成的 4K 成稿")
        if (row.get("source_hash") != project.get("source_hash")
                or row.get("generation_hash") != project.get("generation_hash")
                or row.get("brief_hash") != project.get("brief_hash")):
            raise HTTPException(409, "成稿输入哈希已过期")
        if qa.get("status") != "passed" or qa.get("hard_fail") or human.get("status") != "passed":
            raise HTTPException(409, "自动/人工结构核对尚未全部通过")
        if max(row.get("image_size") or [0]) < 3500:
            raise HTTPException(409, "成稿未达到 4K 合同")
        project["locked_candidate_id"] = candidate_id
        project["status"] = "locked"
        project["stage"] = "全屋设计方案已锁定"
        row["locked_at"] = time.time()
        row["locked_revision"] = project["revision"]
        save_project(project)
        return public_project(project)


@router.post("/api/whole-home-design/projects/{project_id}/modeling-bundle")
def create_modeling_bundle(project_id: str, req: CandidateActionRequest):
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        _assert_revision(project, req.base_revision)
        locked_id = str(project.get("locked_candidate_id") or "")
        if not locked_id or project.get("status") != "locked":
            raise HTTPException(409, "请先锁定通过全部结构核对的 4K 成稿")
        candidate = _candidate(project, locked_id)
        existing = next((row for row in reversed(project.get("bundles") or [])
                         if not row.get("stale") and row.get("candidate_id") == locked_id
                         and row.get("project_revision") == project.get("revision")), None)
        if existing:
            return public_project(project)
        bundle = build_modeling_bundle(project, candidate)
        project.setdefault("bundles", []).append(bundle)
        save_project(project)
        return public_project(project)


@router.get("/api/whole-home-design/projects/{project_id}/bundles/{bundle_id}")
def download_modeling_bundle(project_id: str, bundle_id: str):
    project = _project_or_404(project_id)
    bundle = next((row for row in project.get("bundles") or []
                   if row.get("bundle_id") == bundle_id), None)
    if not bundle or not os.path.isfile(str(bundle.get("path") or "")):
        raise HTTPException(404, "建模任务包不存在")
    return FileResponse(bundle["path"], media_type="application/zip",
                        filename=f"{project_id}_{bundle_id}_blender-task.zip")


@router.post("/api/whole-home-design/projects/{project_id}/cancel")
def cancel_design_project(project_id: str):
    with _project_lock(project_id):
        project = _project_or_404(project_id)
        project["cancel_requested"] = True
        project["status"] = "cancelled"
        project["stage"] = "已取消；不会提交新的付费调用"
        save_project(project)
        return {"cancelled": True, "status": project["status"]}


async def _resume_candidate(project_id: str, candidate_id: str) -> None:
    project = load_project(project_id)
    if not project:
        return
    row = _candidate(project, candidate_id)
    preview = next((value for value in (project.get("paid_previews") or {}).values()
                    if candidate_id in (value.get("candidate_ids") or [])), None)
    if not preview:
        return
    await asyncio.to_thread(_generate_one_candidate, project_id, candidate_id, preview, True)


def recover_background_tasks() -> int:
    resumable = recover_interrupted_projects()
    for project_id, candidate_id in resumable:
        _track(_resume_candidate(project_id, candidate_id))
    if resumable:
        logger.info("[全屋设计] 恢复 %s 个已有 Fal 队列任务", len(resumable))
    return len(resumable)
