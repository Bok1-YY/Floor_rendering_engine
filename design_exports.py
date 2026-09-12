"""Whole-home design: exports."""

import hashlib
import json
import os
import tempfile
import time
import zipfile
from typing import Any





from .design_store import (
    BUNDLE_ROOT,
    file_sha256,
    new_id
)

def _fixed_zip_write(archive: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname.replace("\\", "/"), date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)

def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

def build_modeling_bundle(project: dict, candidate: dict) -> dict:
    bundle_id = new_id("bundle")
    folder = os.path.join(BUNDLE_ROOT, os.path.basename(project["project_id"]))
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, f"{bundle_id}.zip")
    manifest = {
        "schema_version": "whole-home-design-bundle-v1",
        "bundle_id": bundle_id,
        "target_profile": "blender-mcp-v1",
        "project_id": project["project_id"],
        "project_revision": project["revision"],
        "source_hash": project["source_hash"],
        "generation_hash": project.get("generation_hash") or "",
        "brief_hash": project["brief_hash"],
        "locked_candidate_id": candidate["candidate_id"],
        "candidate_hash": candidate["result_hash"],
        "anchor_overlay_hash": project.get("anchor_overlay_hash") or "",
        "units": "metres",
        "coordinate_system": "blender-z-up",
        "geometry_authority": ["source/floorplan-original", "qa/plan-summary.json"],
        "appearance_authority": ["design/locked-concept-2k.png", "references/*"],
        "blocked_when_scale_missing": True,
        "created_at": time.time(),
    }
    acceptance = {
        "version": "blender-model-acceptance-v1",
        "required_outputs": [
            "scene.blend", "scene.glb", "model_report.json", "audit/top.png",
            "audit/front.png", "audit/right.png", "audit/perspective.png",
        ],
        "model_report_required_fields": [
            "blender_version", "units", "wall_count", "door_count", "window_count",
            "room_count", "asset_inventory", "unresolved_issues", "input_bundle_sha256",
            "mcp_tool_summary",
        ],
        "hard_rules": [
            "Use original floor-plan dimensions and confirmed PlanSummary for structure.",
            "Use the locked concept only for furniture, materials, colour, lighting and atmosphere.",
            "If source and concept conflict, source wins.",
            "If scale evidence is missing or contradictory, stop with blocked_missing_scale; never guess metres.",
            "Do not create a 360 panorama or claim construction/BIM accuracy.",
        ],
    }
    task = """# Blender MCP modelling task

Build one editable Blender model from this bundle. Geometry authority is the original floor plan and
confirmed plan summary. The generated design image is only an appearance and furnishing reference.
Never copy a wall, opening or room change introduced by the generated image. Use metres and Blender Z-up.
If no trustworthy scale/dimension evidence exists, stop and report `blocked_missing_scale`.

Deliver exactly the files listed in `blender/acceptance.json`. Save checkpoints during MCP work, inspect
the scene before and after each logical change, and include unresolved issues in `model_report.json`.
This task does not request a 360 panorama or a construction/BIM claim.
"""
    brief_md = f"""# Whole-home design brief

## User requirements

{project.get('brief', {}).get('requirements_text') or ''}

## Structural authority

Use `source/floorplan-original{os.path.splitext(project['source_path'])[1].lower()}` and
`qa/plan-summary.json`. The concept image is not dimension authority.
"""
    file_rows: list[tuple[str, str]] = [
        (f"source/floorplan-original{os.path.splitext(project['source_path'])[1].lower()}", project["source_path"]),
        ("source/floorplan-normalized.png", project["normalized_path"]),
        ("source/floorplan-generation-raw.png", project.get("generation_raw_path") or project.get("generation_path") or project["normalized_path"]),
        ("source/floorplan-generation.png", project.get("generation_path") or project["normalized_path"]),
        ("design/locked-concept-2k.png", candidate["path"]),
    ]
    if project.get("anchor_overlay_path") and os.path.isfile(project["anchor_overlay_path"]):
        file_rows.append(("source/floorplan-anchor-overlay.png", project["anchor_overlay_path"]))
    brief_snapshot = {
        "requirements_text": project.get("brief", {}).get("requirements_text") or "",
        "references": [
            {
                "bundle_name": f"references/{index:02d}{os.path.splitext(path)[1].lower()}",
                "sha256": file_sha256(path),
            }
            for index, path in enumerate(project.get("brief", {}).get("reference_paths") or [], 1)
        ],
        "brief_hash": project.get("brief_hash") or "",
    }
    for index, path in enumerate(project.get("brief", {}).get("reference_paths") or [], 1):
        file_rows.append((f"references/{index:02d}{os.path.splitext(path)[1].lower()}", path))
    text_rows: list[tuple[str, bytes]] = [
        ("manifest.json", _json_bytes(manifest)),
        ("AGENT_TASK.md", task.encode("utf-8")),
        ("design/design-brief.md", brief_md.encode("utf-8")),
        ("design/design-spec.json", _json_bytes(brief_snapshot)),
        ("qa/plan-summary.json", _json_bytes(project.get("plan_summary") or {})),
        ("qa/human-anchors.json", _json_bytes(project.get("anchor_set") or {})),
        ("qa/anchor-verification.json", _json_bytes(project.get("anchor_verification") or {})),
        ("qa/automated-structure-qa.json", _json_bytes(candidate.get("structure_qa") or {})),
        ("qa/human-structure-review.json", _json_bytes(candidate.get("human_review") or {})),
        ("prompts/concept-prompt-snapshot.json", _json_bytes({
            "candidate_id": candidate.get("candidate_id"),
            "prompt_version": candidate.get("prompt_version"),
            "prompt": candidate.get("prompt"),
            "provider": candidate.get("provider"),
            "model_id": candidate.get("model_id"),
            "endpoint": candidate.get("endpoint"),
        })),
        ("blender/acceptance.json", _json_bytes(acceptance)),
        ("blender/expected-output-layout.txt", ("\n".join(acceptance["required_outputs"]) + "\n").encode("utf-8")),
    ]
    checksums: list[str] = []
    fd, temporary = tempfile.mkstemp(prefix=".bundle_", suffix=".zip", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for arcname, data in sorted(text_rows):
                _fixed_zip_write(archive, arcname, data)
                checksums.append(f"{hashlib.sha256(data).hexdigest()}  {arcname}")
            for arcname, path in sorted(file_rows):
                with open(path, "rb") as handle:
                    data = handle.read()
                _fixed_zip_write(archive, arcname, data)
                checksums.append(f"{hashlib.sha256(data).hexdigest()}  {arcname}")
            checksum_bytes = ("\n".join(sorted(checksums)) + "\n").encode("utf-8")
            _fixed_zip_write(archive, "SHA256SUMS", checksum_bytes)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return {
        "bundle_id": bundle_id,
        "path": destination,
        "sha256": file_sha256(destination),
        "candidate_id": candidate["candidate_id"],
        "project_revision": project["revision"],
        "stale": False,
        "created_at": time.time(),
    }
