"""Whole-home design: store."""

import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Optional

from PIL import Image

from .config import MAIN_OUTPUT_DIR



from .design_schema import (
    PROMPT_VERSION
)

ROOT = os.path.join(MAIN_OUTPUT_DIR, "_whole_home_design")

PROJECT_ROOT = os.path.join(ROOT, "projects")

ASSET_ROOT = os.path.join(ROOT, "assets")

BUNDLE_ROOT = os.path.join(ROOT, "bundles")

MODEL_ROOT = os.path.join(ROOT, "model-runs")

for _folder in (PROJECT_ROOT, ASSET_ROOT, BUNDLE_ROOT, MODEL_ROOT):
    os.makedirs(_folder, exist_ok=True)

_LOCKS_GUARD = threading.RLock()

_LOCKS: dict[str, threading.RLock] = {}

def _project_lock(project_id: str) -> threading.RLock:
    safe = os.path.basename(str(project_id or ""))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(safe, threading.RLock())

def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _atomic_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".design_", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass

def project_path(project_id: str) -> str:
    safe = os.path.basename(str(project_id or ""))
    return os.path.join(PROJECT_ROOT, f"{safe}.json")

def load_project(project_id: str) -> Optional[dict]:
    path = project_path(project_id)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return None

def save_project(project: dict) -> None:
    project["updated_at"] = time.time()
    _atomic_json(project_path(project["project_id"]), project)

def list_projects(limit: int = 50) -> list[dict]:
    rows: list[dict] = []
    try:
        names = sorted(os.listdir(PROJECT_ROOT), reverse=True)
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        row = load_project(name[:-5])
        if row:
            rows.append(row)
        if len(rows) >= max(1, min(int(limit), 200)):
            break
    return rows

def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"

def _brief_hash(project: dict) -> str:
    brief = project.get("brief") or {}
    return _stable_hash({
        "requirements_text": brief.get("requirements_text") or "",
        "reference_hashes": brief.get("reference_hashes") or [],
        "plan_summary": project.get("plan_summary") or {},
        "source_hash": project.get("source_hash") or "",
        "generation_hash": project.get("generation_hash") or "",
        "anchor_overlay_hash": project.get("anchor_overlay_hash") or "",
        "prompt_version": PROMPT_VERSION,
    })

def mark_candidates_stale(project: dict, reason: str) -> None:
    for candidate in project.get("candidates") or []:
        candidate["stale"] = True
        candidate["stale_reason"] = reason
    for bundle in project.get("bundles") or []:
        bundle["stale"] = True
        bundle["stale_reason"] = reason
    for model_run in project.get("model_runs") or []:
        model_run["stale"] = True
        model_run["stale_reason"] = reason
    project["locked_candidate_id"] = ""

def _save_candidate_image(project_id: str, candidate_id: str, image: Image.Image) -> str:
    folder = os.path.join(ASSET_ROOT, os.path.basename(project_id), "candidates")
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, f"{os.path.basename(candidate_id)}.png")
    fd, temporary = tempfile.mkstemp(prefix=".candidate_", suffix=".png", dir=folder)
    os.close(fd)
    try:
        image.convert("RGB").save(temporary, "PNG", optimize=True)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return destination
