# -*- coding: utf-8 -*-
"""Durable, provider-free CAD reparse operation records."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid

from .whole_home_dev_lock import data_root_lock, durable_atomic_json


PROCESS_INSTANCE_ID = uuid.uuid4().hex


class CadReparseOperationError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
        self.details = copy.deepcopy(details or {})


def _safe(value: str) -> str:
    text = str(value or "")
    if not text or os.path.basename(text) != text or not all(c.isalnum() or c in "_-" for c in text):
        raise CadReparseOperationError("cad_reparse_operation_id_invalid", "重解析 operation_id 非法", 422)
    return text[:120]


def operation_dir(cad_root: str, project_id: str) -> str:
    return os.path.join(os.path.realpath(cad_root), _safe(project_id), "reparse_operations")


def operation_path(cad_root: str, project_id: str, operation_id: str) -> str:
    return os.path.join(operation_dir(cad_root, project_id), f"{_safe(operation_id)}.json")


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}


def request_fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def create_operation(cad_root: str, *, project_id: str, operation_id: str,
                     base_revision: int, base_state_hash: str, source_path: str,
                     source_sha256: str, candidate_id: str, actor: str) -> tuple[dict, bool]:
    operation_id = _safe(operation_id or f"cadreparse_{uuid.uuid4().hex}")
    folder = operation_dir(cad_root, project_id)
    path = operation_path(cad_root, project_id, operation_id)
    payload = {
        "project_id": project_id, "operation_id": operation_id,
        "base_revision": int(base_revision), "base_state_hash": base_state_hash,
        "source_path": os.path.realpath(source_path), "source_sha256": source_sha256,
        "candidate_id": candidate_id, "actor": actor,
    }
    fingerprint = request_fingerprint(payload)
    with data_root_lock(folder, "json-cad-reparse-index"):
        existing = _read(path)
        if existing:
            if existing.get("request_fingerprint") != fingerprint:
                raise CadReparseOperationError(
                    "cad_reparse_idempotency_conflict", "operation_id 已绑定其他重解析输入")
            return existing, False
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if not name.endswith(".json"):
                    continue
                active = _read(os.path.join(folder, name))
                if active.get("status") in {"queued", "running"}:
                    if active.get("worker_instance_id") != PROCESS_INSTANCE_ID:
                        active.update(status="interrupted", stage="interrupted", progress=100,
                                      finished_at=time.time(),
                                      error_code="cad_reparse_process_restarted",
                                      error="服务重启中断了 CAD 重解析；不会自动续跑")
                        durable_atomic_json(os.path.join(folder, name), active)
                    else:
                        raise CadReparseOperationError(
                            "cad_reparse_already_active", "同一项目已有重解析任务在运行", details={
                                "operation_id": active.get("operation_id")})
        now = time.time()
        record = {
            **payload, "request_fingerprint": fingerprint,
            "status": "queued", "created_at": now, "updated_at": now,
            "stage": "queued", "progress": 0,
            "started_at": None, "finished_at": None,
            "worker_instance_id": PROCESS_INSTANCE_ID,
            "result_revision": None, "result_state_hash": "",
            "error_code": "", "error": "", "failure_evidence": {},
        }
        durable_atomic_json(path, record)
        return record, True


def get_operation(cad_root: str, project_id: str, operation_id: str) -> dict:
    path = operation_path(cad_root, project_id, operation_id)
    folder = os.path.dirname(path)
    with data_root_lock(folder, f"json-{os.path.basename(path)}"):
        record = _read(path)
        if (record.get("status") in {"queued", "running"}
                and record.get("worker_instance_id") != PROCESS_INSTANCE_ID):
            record.update(status="interrupted", stage="interrupted", progress=100,
                          finished_at=time.time(), updated_at=time.time(),
                          error_code="cad_reparse_process_restarted",
                          error="服务重启中断了 CAD 重解析；不会自动续跑")
            durable_atomic_json(path, record)
        return record


def update_operation(cad_root: str, project_id: str, operation_id: str, **changes) -> dict:
    path = operation_path(cad_root, project_id, operation_id)
    folder = os.path.dirname(path)
    with data_root_lock(folder, f"json-{os.path.basename(path)}"):
        record = _read(path)
        if not record:
            raise CadReparseOperationError("cad_reparse_operation_not_found", "重解析任务不存在", 404)
        terminal = {"done", "needs_review", "failed", "conflict", "interrupted"}
        if record.get("status") in terminal:
            return record
        record.update(copy.deepcopy(changes), updated_at=time.time())
        if record.get("status") in terminal:
            record["finished_at"] = record.get("finished_at") or time.time()
        durable_atomic_json(path, record)
        return record


def public_operation(record: dict) -> dict:
    result = {
        key: copy.deepcopy(record.get(key)) for key in (
            "operation_id", "project_id", "status", "base_revision", "base_state_hash",
            "candidate_id", "created_at", "started_at", "finished_at", "result_revision",
            "result_state_hash", "error_code", "error", "stage", "progress")
    }
    evidence = record.get("failure_evidence") or {}
    result["failure_evidence"] = {
        "report_sha256": str(evidence.get("report_sha256") or ""),
        "hard_error_summary": copy.deepcopy(evidence.get("hard_error_summary") or [])[:50],
    }
    if result.get("operation_id") and result.get("project_id") and evidence:
        result["failure_evidence"]["report_url"] = (
            f"/api/whole-home/projects/{result['project_id']}/cad/report")
    return result


def latest_operation_summary(cad_root: str, project_id: str) -> dict:
    folder = operation_dir(cad_root, project_id)
    if not os.path.isdir(folder):
        return {}
    entries = sorted(
        (entry for entry in os.scandir(folder) if entry.is_file() and entry.name.endswith(".json")),
        key=lambda entry: entry.stat().st_mtime_ns, reverse=True)[:500]
    records = [_read(entry.path) for entry in entries]
    records = [row for row in records if row]
    if not records:
        return {}
    latest = max(records, key=lambda row: float(row.get("updated_at") or row.get("created_at") or 0))
    failure = next((row for row in sorted(
        records, key=lambda row: float(row.get("updated_at") or 0), reverse=True)
        if row.get("status") in {"failed", "needs_review"}), {})
    status = latest.get("status") or ""
    error = str(latest.get("error") or "")[:300]
    return {
        "last_operation_id": latest.get("operation_id") or "",
        "status": status, "last_status": status,
        "stage": latest.get("stage") or latest.get("status") or "",
        "progress": int(latest.get("progress") or 0),
        "candidate_id": latest.get("candidate_id") or "",
        "last_candidate_id": latest.get("candidate_id") or "",
        "error_code": latest.get("error_code") or "",
        "error": error, "last_error": error,
        "failure_count": sum(
            row.get("status") in {"failed", "needs_review"} for row in records),
        "last_failure": ({
            "operation_id": failure.get("operation_id") or "",
            "error_code": failure.get("error_code") or "",
            "error": str(failure.get("error") or "")[:300],
            "candidate_id": failure.get("candidate_id") or "",
            "finished_at": failure.get("finished_at"),
        } if failure else {}),
    }
