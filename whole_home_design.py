"""Whole-home design: whole home design."""

import json
import os
import time
from copy import deepcopy


from .config import logger
from .server_helpers import result_thumb_url, to_url



from .design_exports import (
    _fixed_zip_write,
    _json_bytes,
    build_modeling_bundle
)
from .design_images import (
    _anchor_font,
    anchor_summary_conflicts,
    clean_generation_annotations,
    extract_generation_plan,
    normalize_floorplan,
    normalize_plan_rooms,
    render_anchor_overlay,
    validate_anchor_set
)
from .design_modeling import (
    _model_row,
    _normalize_model_artifacts,
    create_model_run_record,
    recover_interrupted_projects,
    retry_model_review,
    run_model_job
)
from .design_provider import (
    _fal_endpoint,
    _image_part,
    call_design_image,
    call_gemini_json,
    evaluate_structure
)
from .design_schema import (
    SCHEMA_VERSION,
    PLAN_PROMPT_VERSION,
    PLAN_VERIFY_VERSION,
    PROMPT_VERSION,
    QA_PROMPT_VERSION,
    STRUCTURE_GUIDANCE_QUESTIONS,
    STRUCTURE_REVIEW_ITEMS,
    SUPPORTED_RATIOS,
    _PLAN_SCHEMA,
    _QA_SCHEMA,
    _STRUCTURE_GRAPH_SCHEMA,
    empty_anchor_set,
    empty_plan_summary,
    empty_structure_review
)
from .design_store import (
    ASSET_ROOT,
    BUNDLE_ROOT,
    MODEL_ROOT,
    PROJECT_ROOT,
    ROOT,
    _LOCKS,
    _LOCKS_GUARD,
    _atomic_json,
    _brief_hash,
    _project_lock,
    _save_candidate_image,
    _stable_hash,
    file_sha256,
    list_projects,
    load_project,
    mark_candidates_stale,
    new_id,
    project_path,
    save_project
)
from .design_structure import (
    _compile_structure_bundle,
    _guidance_questions,
    _norm_point,
    _scale_calibration,
    prepare_structure_review,
    submit_structure_review
)

def create_project(source_path: str, original_name: str = "") -> dict:
    source_hash = file_sha256(source_path)
    project_id = new_id("design")
    normalized_path, normalization = normalize_floorplan(source_path, project_id)
    generation_path, generation_crop = extract_generation_plan(source_path, project_id)
    generation_hash = file_sha256(generation_path)
    now = time.time()
    project = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "revision": 1,
        "status": "needs_anchor_review",
        "stage": "请先点选全部空间和入户门",
        "error": "",
        "source_path": source_path,
        "source_name": original_name or os.path.basename(source_path),
        "source_hash": source_hash,
        "normalized_path": normalized_path,
        "normalization": normalization,
        "generation_path": generation_path,
        "generation_raw_path": generation_path,
        "generation_crop": generation_crop,
        "generation_cleanup": {"version": "annotation-cleanup-v1", "applied_count": 0, "boxes": []},
        "generation_hash": generation_hash,
        "anchor_set": empty_anchor_set(source_hash, file_sha256(normalized_path)),
        "anchor_overlay_path": "",
        "anchor_overlay_hash": "",
        "anchor_verification": {"status": "not_run", "conflicts": [], "changes": [], "inferred_anchor_gaps": []},
        "structure_review": empty_structure_review(),
        "plan_summary": empty_plan_summary("human"),
        "plan_summary_confirmed": False,
        "brief": {"requirements_text": "", "reference_paths": [], "reference_hashes": []},
        "brief_hash": "",
        "paid_previews": {},
        "candidates": [],
        "bundles": [],
        "model_runs": [],
        "locked_candidate_id": "",
        "cancel_requested": False,
        "created_at": now,
        "updated_at": now,
    }
    save_project(project)
    return project

def analyze_plan(project_id: str, operation_id: str | None = None) -> dict:
    with _project_lock(project_id):
        project = load_project(project_id)
        if not project:
            raise KeyError(project_id)
        if project.get("cancel_requested") or (operation_id and project.get("analysis_operation_id") != operation_id):
            return project
        operation_id = operation_id or new_id("analysis")
        project["analysis_operation_id"] = operation_id
        revision = project["revision"]
        input_hash = _stable_hash({"anchors": project.get("anchor_set"), "source": project.get("source_hash")})
        def valid(current):
            return (current.get("analysis_operation_id") == operation_id
                    and current.get("revision") == revision
                    and not current.get("cancel_requested")
                    and _stable_hash({"anchors": current.get("anchor_set"), "source": current.get("source_hash")}) == input_hash)
        anchors = project.get("anchor_set") or {}
        if not anchors.get("confirmed_complete") or not project.get("anchor_overlay_path"):
            project.update(status="needs_anchor_review", stage="请先完成全部空间和入户门锚点", error="锚点尚未确认")
            save_project(project)
            return project
        project.update(status="analyzing_plan", stage="Gemini 正在根据人工锚点读取户型", error="")
        save_project(project)
        normalized = project["normalized_path"]
        generation_raw = project.get("generation_raw_path") or project.get("generation_path") or normalized
        overlay = project["anchor_overlay_path"]
        anchor_json = json.dumps(anchors, ensure_ascii=False, sort_keys=True)
    prompt = f"""Read this residential floor plan using human anchors as hard facts.
Image 1 is the complete clean evidence sheet. Image 2 is the same evidence with numbered human anchors and a legend.
Image 3 is the cropped generation plan. Anchor JSON follows:
{anchor_json}

Rules: preserve every human label verbatim. Attach space anchor_ids to matching room rows; use entrance/opening/
fixed_feature/ignore anchors in their corresponding summary fields and never turn them into fake rooms. Never move,
rename, or silently discard a human anchor. Infer walls and room extents from pixels, not marker radius. You may add clearly
visible unmarked spaces with source=gemini_inferred; anchored rooms use source=human_anchor. Return conflicts when
an anchor appears outside the plan or contradicts visible evidence. Read title, area, exact dimensions, entrance,
openings, balconies, wet zones and level differences conservatively. Do not invent geometry. annotation_boxes use
0..1000 coordinates against Image 3 and may cover safe non-structural text only. Set verification.status=draft."""
    first, first_error = call_gemini_json(prompt, [normalized, overlay, generation_raw], _PLAN_SCHEMA)
    if first:
        with _project_lock(project_id):
            current = load_project(project_id) or {}
            if not valid(current):
                return current
            current.update(status="verifying_plan", stage="Gemini 正在独立复核锚点与户型摘要")
            save_project(current)
        verify_prompt = f"""Act as an independent adversarial floor-plan verifier.
Images 1-3 and anchor JSON are authoritative exactly as in the extraction pass. Audit the draft JSON below, correct
wrong room roles, missing/invalid adjacency, title-area-dimension contradictions and unsupported inferred spaces.
Human anchor labels and coordinates are immutable. Keep source=human_anchor for anchored rooms and
source=gemini_inferred for additions. List every correction in verification.changes, every human-anchor problem in
verification.conflicts, and likely missing manual anchors in verification.inferred_anchor_gaps. Return the complete
corrected schema with verification.status=verified when no conflicts, otherwise conflict.

ANCHORS:
{anchor_json}

DRAFT:
{json.dumps(first, ensure_ascii=False)}"""
        payload, verify_error = call_gemini_json(verify_prompt, [normalized, overlay, generation_raw], _PLAN_SCHEMA)
    else:
        payload, verify_error = None, None
    with _project_lock(project_id):
        project = load_project(project_id) or {}
        if not valid(project):
            return project
        if payload:
            summary = empty_plan_summary("gemini_verified")
            for key in summary:
                if key in payload:
                    summary[key] = payload[key]
            summary["room_count"] = max(0, min(80, int(summary.get("room_count") or 0)))
            summary["rooms"] = normalize_plan_rooms(list(summary.get("rooms") or []))
            summary["room_count"] = len(summary["rooms"])
            summary["summary_confidence"] = max(
                0.0, min(1.0, float(summary.get("summary_confidence") or 0.0)))
            for room in summary["rooms"]:
                room["confidence"] = max(0.0, min(1.0, float(room.get("confidence") or 0.0)))
                room["needs_confirmation"] = bool(room.get("needs_confirmation"))
            review_items = list(summary.get("review_items") or [])[:100]
            reviewed_room_ids = {
                str(item.get("room_id") or "") for item in review_items if isinstance(item, dict)
            }
            for room in summary["rooms"]:
                room_id = str(room.get("id") or "")
                if room.get("needs_confirmation") and room_id not in reviewed_room_ids:
                    review_items.append({
                        "id": f"room_{room_id}", "kind": "room_role",
                        "room_id": room_id,
                        "label": f"确认空间角色：{room.get('label') or room_id}",
                        "evidence": str(room.get("evidence") or "模型将该空间标记为待确认"),
                        "confidence": room.get("confidence") or 0.0,
                        "status": "needs_confirmation",
                    })
            summary["review_items"] = review_items[:100]
            summary["annotation_boxes"] = list(summary.get("annotation_boxes") or [])[:200]
            verification = dict(summary.get("verification") or {})
            verification["conflicts"] = list(verification.get("conflicts") or []) + anchor_summary_conflicts(anchors, summary)
            verification["status"] = "conflict" if verification.get("conflicts") else "verified"
            verification["conflicts"] = [str(v) for v in verification.get("conflicts") or []][:100]
            verification["changes"] = [str(v) for v in verification.get("changes") or []][:100]
            verification["inferred_anchor_gaps"] = [str(v) for v in verification.get("inferred_anchor_gaps") or []][:100]
            summary["verification"] = verification
            previous_generation_hash = str(project.get("generation_hash") or "")
            try:
                clean_path, cleanup = clean_generation_annotations(
                    generation_raw, project_id, summary["annotation_boxes"])
                project["generation_raw_path"] = generation_raw
                project["generation_path"] = clean_path
                project["generation_cleanup"] = cleanup
                project["generation_hash"] = cleanup["clean_hash"]
                if previous_generation_hash and previous_generation_hash != project["generation_hash"]:
                    mark_candidates_stale(project, "生成结构图文字清理结果已更新")
            except Exception as exc:
                logger.warning("[全屋设计] 结构图文字清理失败 project=%s: %s", project_id, exc)
                project["generation_path"] = generation_raw
                project["generation_hash"] = file_sha256(generation_raw)
                project["generation_cleanup"] = {
                    "version": "annotation-cleanup-v1", "applied_count": 0,
                    "boxes": [], "error": type(exc).__name__,
                }
            project["brief_hash"] = _brief_hash(project)
            project["plan_summary"] = summary
            project["anchor_verification"] = verification
            project["stage"] = "请修正锚点冲突" if verification["conflicts"] else "请确认 Gemini 双重验证摘要"
            project["status"] = "needs_anchor_review" if verification["conflicts"] else "needs_plan_review"
            project["error"] = "；".join(verification["conflicts"][:3])
        else:
            project["plan_summary"] = empty_plan_summary("human")
            project["anchor_verification"] = {"status": "unverified", "conflicts": [], "changes": [], "inferred_anchor_gaps": []}
            project["stage"] = "Gemini 双重验证失败，请重试"
            project["status"] = "needs_anchor_review"
            project["error"] = verify_error or first_error or "Gemini 双重验证不可用"
        save_project(project)
        return project

def build_design_prompt(project: dict, *, phase: str, direction_index: int = 1,
                        refinement_text: str = "") -> str:
    plan = project.get("plan_summary") or {}
    brief = project.get("brief") or {}
    anchors = project.get("anchor_set") or {}
    room_lines = [
        f"- {row.get('id')}: {row.get('label')} / {row.get('room_type')} / "
        f"{row.get('coarse_location')} / adjacent={','.join(row.get('adjacent_room_ids') or [])}"
        for row in plan.get("rooms") or []
    ]
    phase_rule = (
        f"Create design direction {direction_index} as a distinct but faithful interpretation of the same brief."
        if phase == "draft" else
        "Refine the selected draft into a polished final image. Improve material realism, furniture detail, "
        "lighting and edge quality only; do not redesign or move architectural structure."
    )
    return f"""Create a clean, photorealistic whole-home interior design concept from the supplied floor plan.

OUTPUT CONTRACT:
- Strict vertical overhead orthographic view, roof removed, 2.5D walls with restrained height and natural shadows.
- Preserve the exact plan orientation, exterior footprint, partitions, room count, adjacency, entrance,
  balconies, kitchen/bath wet zones and all major doors/windows from Image 1.
- Never crop, rotate, mirror, widen, narrow, add or remove any architectural space.
- Furnish every usable room plausibly while keeping doors, circulation and fixed wet zones clear.
- Clean neutral background. No labels, room names, dimensions, legends, UI, watermark or inset source plan.
- This is a marketing concept image, not a new floor plan and not a construction drawing.

CONFIRMED LIGHTWEIGHT PLAN SUMMARY:
declared_layout={plan.get('declared_layout') or {}}
declared_area_m2={plan.get('declared_area_m2') or 0}
overall_dimensions_mm={plan.get('overall_dimensions_mm') or {}}
room_count={plan.get('room_count') or 0}
rooms:
{chr(10).join(room_lines) or '- Refer to Image 1; human summary contains no room rows.'}
entrances={plan.get('entrances') or []}
balconies={plan.get('balconies') or []}
wet_zones={plan.get('wet_zones') or []}
openings={plan.get('openings_summary') or []}
must_preserve={plan.get('must_preserve') or []}

HUMAN ANCHOR CONTRACT (0..1000 on the clean normalized evidence image):
{json.dumps(anchors.get('anchors') or [], ensure_ascii=False)}
Image 1 is the clean structural generation plan. Image 2 is the numbered human-anchor semantic guide.
Use Image 2 to understand human-confirmed labels and locations, but NEVER copy marker circles, IDs, lines,
legend text, coordinates, or annotations into the output.

USER DESIGN REQUIREMENTS:
{brief.get('requirements_text') or ''}

PHASE:
{phase_rule}
{('ADDITIONAL REFINEMENT REQUEST: ' + refinement_text) if refinement_text else ''}

Image 1 is always the structural authority. Image 2 is semantic guidance; later images are appearance references."""

def public_project(project: dict, *, list_mode: bool = False) -> dict:
    row = deepcopy(project)
    row["anchor_set"] = row.get("anchor_set") or empty_anchor_set(
        str(project.get("source_hash") or ""),
        file_sha256(project["normalized_path"]) if project.get("normalized_path") and os.path.isfile(project["normalized_path"]) else "",
    )
    row["anchor_verification"] = row.get("anchor_verification") or {
        "status": "not_run", "conflicts": [], "changes": [], "inferred_anchor_gaps": [],
    }
    row["structure_review"] = row.get("structure_review") or empty_structure_review()
    row["structure_review"].pop("seed_graph", None)
    row["structure_review"].pop("structure_bundle", None)
    row["model_runs"] = row.get("model_runs") or []
    row["source_url"] = to_url(project.get("source_path"))
    row["normalized_url"] = to_url(project.get("normalized_path"))
    row["generation_url"] = to_url(project.get("generation_path"))
    row["anchor_overlay_url"] = to_url(project.get("anchor_overlay_path"))
    row["legacy_unanchored"] = not bool((project.get("anchor_set") or {}).get("confirmed_complete"))
    for candidate in row.get("candidates") or []:
        candidate["url"] = to_url(candidate.get("path"))
        candidate["thumb"] = result_thumb_url(candidate.get("path")) if candidate.get("path") else ""
        candidate.pop("queue_handle", None)
        candidate.pop("prompt", None)
    if isinstance(row.get("brief"), dict):
        row["brief"]["reference_paths"] = []
    for bundle in row.get("bundles") or []:
        bundle["download_url"] = (
            f"/api/whole-home-design/projects/{project['project_id']}/bundles/{bundle['bundle_id']}"
        )
        bundle.pop("path", None)
    for model_run in row.get("model_runs") or []:
        for artifact in model_run.get("artifacts") or []:
            artifact["download_url"] = (
                f"/api/whole-home-design/projects/{project['project_id']}/model-runs/"
                f"{model_run['run_id']}/artifacts/{artifact['kind']}"
            )
            artifact.pop("path", None)
        model_run.pop("output_root", None)
        model_run.pop("structure_bundle", None)
        model_run.pop("idempotency_key", None)
        model_run.pop("background_started_at", None)
        report = model_run.get("mechanical_report") or {}
        model_run["mechanical_report"] = {
            "schema": report.get("schema"), "status": report.get("status"),
            "structure_hash": report.get("structure_hash"), "checks": list(report.get("checks") or []),
            "blend_status": ((report.get("blender") or {}).get("blend") or {}).get("status"),
            "glb_status": ((report.get("blender") or {}).get("glb") or {}).get("status"),
            "ifc_status": (report.get("ifc") or {}).get("status"),
        }
    for preview in (row.get("paid_previews") or {}).values():
        preview.pop("confirmation_phrase", None)
    if list_mode:
        for key in ("paid_previews", "brief", "plan_summary", "anchor_set", "anchor_verification", "structure_review", "model_runs"):
            row.pop(key, None)
        row["candidates"] = [candidate for candidate in row.get("candidates") or [] if candidate.get("path")]
    row.pop("source_path", None)
    row.pop("normalized_path", None)
    row.pop("generation_path", None)
    row.pop("generation_raw_path", None)
    row.pop("anchor_overlay_path", None)
    return row
