# -*- coding: utf-8 -*-
"""Independent development-agent verdicts, isolated from human learning labels."""
from __future__ import annotations

import copy
import hashlib
import hmac
import io
import json
import os
import re
import time
import uuid
from typing import Optional

from PIL import Image

from .config import MAIN_OUTPUT_DIR
from .whole_home_autopilot import DevelopmentAutopilotError, require_development_autopilot_enabled
from .whole_home_dev_lock import data_root_lock, durable_atomic_json


EXTERNAL_REVIEW_DIR = os.path.join(
    MAIN_OUTPUT_DIR, '_whole_home', 'development_external_reviews')
_SAFE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$')
MANAGED_ARTIFACT_ROOT = MAIN_OUTPUT_DIR


def _safe(value: str, label: str) -> str:
    result = str(value or '').strip()
    if not _SAFE.fullmatch(result):
        raise DevelopmentAutopilotError(
            'invalid_external_review_identity', f'{label} 格式无效', 422)
    return result


def _path(run_id: str) -> str:
    return os.path.join(EXTERNAL_REVIEW_DIR, f'{_safe(run_id, "run_id")}.json')


def _read(path: str) -> Optional[dict]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None


def _atomic(path: str, value: dict) -> None:
    durable_atomic_json(path, value)


def get_external_reviews(run_id: str) -> dict:
    require_development_autopilot_enabled()
    path = _path(run_id)
    with data_root_lock(EXTERNAL_REVIEW_DIR, f'external-review-{run_id}'):
        value = _read(path)
        return copy.deepcopy(value or {
            'schema_version': 1, 'run_id': run_id, 'review_version': 0,
            'reviews': [], 'events': [],
            'label_scope': 'development_external_review',
            'excluded_from_human_learning': True,
        })


def _canonical_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _artifact_candidates(result: dict) -> dict[str, list[dict]]:
    candidates: dict[str, list[dict]] = {}

    def add(identifier: str, path: str, kind: str, attempt_id: str = '',
            material_attempt_id: str = '') -> None:
        identifier = str(identifier or '').strip()
        path = str(path or '').strip()
        if identifier and path:
            candidates.setdefault(identifier, []).append({
                'artifact_id': identifier, 'storage_path': path,
                'artifact_kind': kind, 'attempt_id': str(attempt_id or ''),
                'material_attempt_id': str(material_attempt_id or ''),
            })

    for attempt in result.get('attempts') or []:
        attempt_id = str(attempt.get('attempt_id') or '')
        add(
            str(attempt.get('structure_artifact_id') or attempt_id),
            str(attempt.get('structure_path') or ''),
            'structure', attempt_id)
        for material in attempt.get('material_attempts') or []:
            material_id = str(material.get('material_attempt_id') or '')
            artifact_id = str(material.get('artifact_id') or material_id)
            path = next((
                str(material.get(key) or '')
                for key in (
                    'final_path', 'corrected_path', 'material_path',
                    'api_original_path')
                if str(material.get(key) or '').strip()
            ), '')
            add(artifact_id, path, 'material', attempt_id, material_id)
    return candidates


def resolve_review_artifact(*, run: dict, result_id: str,
                            artifact_id: str) -> dict:
    """Resolve and hash one server-enumerated immutable review artifact."""
    result = next((row for row in run.get('results') or []
                   if str(row.get('result_id') or '') == str(result_id or '')), None)
    if not result:
        raise DevelopmentAutopilotError(
            'development_external_result_not_found', 'result 不属于开发 run', 409)
    rows = _artifact_candidates(result).get(str(artifact_id or ''), [])
    unique = {
        (os.path.realpath(str(row.get('storage_path') or '')),
         str(row.get('artifact_kind') or ''))
        for row in rows
    }
    if len(unique) != 1:
        raise DevelopmentAutopilotError(
            'development_external_artifact_not_found',
            'artifact_id 未唯一解析到可评审文件', 409)
    row = copy.deepcopy(rows[0])
    path = os.path.realpath(str(row.pop('storage_path') or ''))
    root = os.path.realpath(MANAGED_ARTIFACT_ROOT)
    try:
        managed = os.path.commonpath([root, path]) == root
    except ValueError:
        managed = False
    if not managed or not os.path.isfile(path):
        raise DevelopmentAutopilotError(
            'development_external_artifact_missing',
            '评审文件不存在或不在 server managed root', 409)
    before = os.stat(path)
    with open(path, 'rb') as handle:
        payload = handle.read()
        descriptor = os.fstat(handle.fileno())
    after = os.stat(path)
    stable = (
        before.st_dev == descriptor.st_dev == after.st_dev
        and before.st_ino == descriptor.st_ino == after.st_ino
        and before.st_size == descriptor.st_size == after.st_size == len(payload)
        and before.st_mtime_ns == descriptor.st_mtime_ns == after.st_mtime_ns
    )
    if not stable:
        raise DevelopmentAutopilotError(
            'development_external_artifact_changed',
            '评审文件在绑定过程中被替换', 409)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            image_format = str(image.format or '').lower()
            image.verify()
    except Exception as ex:
        raise DevelopmentAutopilotError(
            'development_external_artifact_invalid',
            '评审 artifact 不是可验证图片', 409) from ex
    row.update(
        result_id=str(result_id or ''),
        storage_path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        width=int(width), height=int(height),
        image_format=image_format,
        file_mtime_ns=int(after.st_mtime_ns),
    )
    return row


def record_external_review(*, run_id: str, result_id: str, artifact_id: str,
                           review_status: str, review_tags: list[str],
                           review_note: str, reviewer_context: dict,
                           artifact_evidence: dict,
                           failure_dimension: str, confidence: float,
                           expected_review_version: int,
                           idempotency_key: str) -> dict:
    require_development_autopilot_enabled()
    run_id = _safe(run_id, 'run_id')
    result_id = _safe(result_id, 'result_id')
    artifact_id = _safe(artifact_id, 'artifact_id')
    reviewer_id = _safe(
        str((reviewer_context or {}).get('reviewer_id') or ''), 'reviewer_id')
    key = _safe(idempotency_key, 'idempotency_key')
    if not reviewer_id.startswith('agent:'):
        raise DevelopmentAutopilotError(
            'external_reviewer_must_be_agent', '外部开发评审 reviewer_id 必须以 agent: 开头', 422)
    if review_status not in {'pass', 'backup', 'reject'}:
        raise DevelopmentAutopilotError(
            'invalid_external_review_status', '评审必须为 pass/backup/reject', 422)
    tags = list(dict.fromkeys(
        str(value).strip() for value in review_tags if str(value).strip()))[:20]
    if review_status == 'reject' and not tags:
        raise DevelopmentAutopilotError(
            'external_reject_requires_tags', 'reject 必须包含原因标签', 422)
    evidence = copy.deepcopy(artifact_evidence or {})
    if (str(evidence.get('result_id') or '') != result_id
            or str(evidence.get('artifact_id') or '') != artifact_id
            or not re.fullmatch(r'[0-9a-f]{64}', str(evidence.get('sha256') or ''))
            or int(evidence.get('byte_length') or 0) <= 0):
        raise DevelopmentAutopilotError(
            'development_external_artifact_binding_invalid',
            'server artifact binding 不完整', 409)
    reviewer_binding = {
        binding_key: copy.deepcopy((reviewer_context or {}).get(binding_key))
        for binding_key in (
            'reviewer_id', 'workflow_id', 'task_id', 'task_role',
            'lease_token_hash', 'lease_expires_at', 'workflow_version')
    }
    request_core = {
        'run_id': run_id, 'result_id': result_id,
        'artifact_id': artifact_id,
        'artifact_sha256': evidence['sha256'],
        'artifact_byte_length': int(evidence['byte_length']),
        'review_status': review_status, 'review_tags': tags,
        'review_note': str(review_note or '')[:4000],
        'failure_dimension': str(failure_dimension or '')[:100],
        'confidence': max(0.0, min(float(confidence), 1.0)),
        'reviewer_binding': reviewer_binding,
    }
    request_sha256 = _canonical_hash(request_core)
    path = _path(run_id)
    with data_root_lock(EXTERNAL_REVIEW_DIR, f'external-review-{run_id}'):
        document = _read(path) or {
            'schema_version': 1, 'run_id': run_id, 'review_version': 0,
            'reviews': [], 'events': [],
            'label_scope': 'development_external_review',
            'excluded_from_human_learning': True,
        }
        actual_version = int(document.get('review_version') or 0)
        existing_event = next((
            row for row in document.get('events') or []
            if row.get('idempotency_key') == key
        ), None)
        if existing_event:
            if not hmac.compare_digest(
                    str(existing_event.get('request_sha256') or ''),
                    request_sha256):
                raise DevelopmentAutopilotError(
                    'external_review_idempotency_conflict',
                    '幂等键已绑定不同 reviewer/artifact/verdict', 409)
            return copy.deepcopy(document)
        if int(expected_review_version) != actual_version:
            raise DevelopmentAutopilotError(
                'external_review_version_conflict', '外部评审版本已变化', 409,
                {'expected_review_version': int(expected_review_version),
                 'actual_review_version': actual_version})
        now = time.time()
        review = {
            'review_id': f'external_review_{uuid.uuid4().hex}',
            'run_id': run_id, 'result_id': result_id, 'artifact_id': artifact_id,
            'review_status': review_status, 'review_tags': tags,
            'review_note': str(review_note or '')[:4000],
            'reviewer_id': reviewer_id,
            'reviewer_binding': reviewer_binding,
            'artifact_evidence': evidence,
            'request_sha256': request_sha256,
            'failure_dimension': str(failure_dimension or '')[:100],
            'confidence': max(0.0, min(float(confidence), 1.0)),
            'label_scope': 'development_external_review',
            'created_at': now,
        }
        document['reviews'] = [
            row for row in document.get('reviews') or []
            if not (row.get('result_id') == result_id
                    and row.get('artifact_id') == artifact_id
                    and row.get('reviewer_id') == reviewer_id)
        ] + [review]
        document.setdefault('events', []).append({
            'event_id': f'external_review_event_{uuid.uuid4().hex}',
            'idempotency_key': key, 'at': now,
            'request_sha256': request_sha256,
            'reviewer_binding': copy.deepcopy(reviewer_binding),
            'artifact_evidence': copy.deepcopy(evidence),
            'review': copy.deepcopy(review),
        })
        document['review_version'] = actual_version + 1
        document['updated_at'] = now
        _atomic(path, document)
        return copy.deepcopy(document)


__all__ = [
    'EXTERNAL_REVIEW_DIR', 'MANAGED_ARTIFACT_ROOT', 'get_external_reviews',
    'record_external_review', 'resolve_review_artifact',
]
