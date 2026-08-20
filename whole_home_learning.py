# -*- coding: utf-8 -*-
"""Append-only human feedback and consent-gated whole-home learning exports.

Generation records stay immutable after reaching a terminal state.  This module
joins a separate event log at read time so human decisions never overwrite
automatic gates, QA outcomes, attempts, or historical run evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from typing import Any, Optional

from PIL import Image

from .config import MAIN_OUTPUT_DIR, UPLOAD_DIR, logger
from .server_helpers import result_thumb_url, to_url
from .whole_home_engine import ROOT, RUN_DIR, load_project, load_run, model_hash
from .whole_home_cad import public_reference_contract


LEARNING_DIR = os.path.join(ROOT, 'learning')
FEEDBACK_DIR = os.path.join(LEARNING_DIR, 'feedback')
RECIPE_DIR = os.path.join(LEARNING_DIR, 'recipes')
CONSENT_DIR = os.path.join(LEARNING_DIR, 'consents')
EXPORT_DIR = os.path.join(LEARNING_DIR, 'exports')
for _folder in (LEARNING_DIR, FEEDBACK_DIR, RECIPE_DIR, CONSENT_DIR, EXPORT_DIR):
    os.makedirs(_folder, exist_ok=True)

_LOCK = threading.RLock()
_TERMINAL_RUN = frozenset(('done', 'partial', 'failed'))
_TERMINAL_RESULT = frozenset(('done', 'failed'))
_SECRET_KEY = re.compile(
    r'(api[_-]?key|apikey|authorization|credential|password|secret|access[_-]?token|refresh[_-]?token)',
    re.I,
)
_SECRET_TEXT = (
    (re.compile(r'(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+'), r'\1=***'),
    (re.compile(r'(?i)([?&](?:key|api[_-]?key|token)=)[^&\s]+'), r'\1***'),
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+'), r'\1***'),
    (re.compile(r'AIza[0-9A-Za-z_-]{20,}'), '***'),
)
_ABSOLUTE_WINDOWS_PATH = re.compile(
    r'(?i)\b[A-Z]:\\(?:[^\s<>:"|?*]+\\)*[^\s<>:"|?*]*')


class WholeHomeLearningError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _basename(value: str) -> str:
    return os.path.basename(str(value or '')).replace(':', '_')


def _feedback_path(run_id: str) -> str:
    return os.path.join(FEEDBACK_DIR, f'{_basename(run_id)}.json')


def _consent_path(project_id: str) -> str:
    return os.path.join(CONSENT_DIR, f'{_basename(project_id)}.json')


def _recipe_path(run_id: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]
    return os.path.join(RECIPE_DIR, _basename(run_id), f'{digest}.json')


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None
    except Exception as ex:
        logger.warning(f'[整屋学习] 读取失败 {path}: {ex}')
        return None


def _atomic_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    try:
        with open(temporary, 'x', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def _sha256_file(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ''
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _image_dimensions(path: str) -> dict:
    try:
        with Image.open(path) as image:
            return {'width': image.width, 'height': image.height, 'format': image.format or ''}
    except Exception:
        return {'width': None, 'height': None, 'format': ''}


def _file_ref(path: str) -> dict:
    value = os.path.realpath(str(path or '')) if path else ''
    return {
        'path': value,
        'exists': bool(value and os.path.isfile(value)),
        'sha256': _sha256_file(value),
    }


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not _SECRET_KEY.search(str(key))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        output = value
        for pattern, replacement in _SECRET_TEXT:
            output = pattern.sub(replacement, output)
        return output
    return value


def _public_reference_value(value: Any) -> Any:
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if str(key) == 'local_path':
                continue
            if str(key) == 'path' and value.get('role') == 'current_slot_reference':
                continue
            if str(key) in {'public_thumb_url', 'pano_resource'} and isinstance(item, str):
                output[str(key)] = item.split('?', 1)[0].split('#', 1)[0]
                continue
            output[str(key)] = _public_reference_value(item)
        return output
    if isinstance(value, list):
        return [_public_reference_value(item) for item in value]
    return copy.deepcopy(value)


def _preferred_material_path(material: dict) -> str:
    for key in ('final_path', 'corrected_path', 'material_path', 'api_original_path'):
        path = str(material.get(key) or '')
        if path and os.path.isfile(path):
            return os.path.realpath(path)
    return ''


def enumerate_reviewable_artifacts(run: dict) -> list[dict]:
    """Return one stable, user-visible candidate per material attempt."""
    artifacts: list[dict] = []
    seen_paths: set[str] = set()
    for result in run.get('results') or []:
        result_id = str(result.get('result_id') or '')
        for attempt in result.get('attempts') or []:
            for material in attempt.get('material_attempts') or []:
                path = _preferred_material_path(material)
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                material_id = str(material.get('material_attempt_id') or '')
                artifact_id = material_id or 'artifact_' + hashlib.sha256(
                    f'{result_id}|{path}'.encode('utf-8')).hexdigest()[:24]
                artifacts.append({
                    'artifact_id': artifact_id,
                    'result_id': result_id,
                    'room_id': str(result.get('room_id') or ''),
                    'slot_id': str(result.get('slot_id') or ''),
                    'model_key': str(result.get('model_key') or ''),
                    'attempt_id': str(attempt.get('attempt_id') or ''),
                    'material_attempt_id': material_id,
                    'path': path,
                    'url': to_url(path),
                    'thumb': result_thumb_url(path),
                    'auto_outcome': str(result.get('outcome') or ''),
                    'auto_deliverable': bool(result.get('deliverable')),
                    'material_status': str(material.get('status') or ''),
                })
        # Compatibility for old runs that persisted only a result-level final.
        result_path = next((
            os.path.realpath(str(result.get(key) or ''))
            for key in ('final_path', 'corrected_path', 'material_path', 'api_original_path', 'path')
            if result.get(key) and os.path.isfile(str(result.get(key)))
        ), '')
        if result_path and result_path not in seen_paths:
            seen_paths.add(result_path)
            artifacts.append({
                'artifact_id': f'{result_id}:final',
                'result_id': result_id,
                'room_id': str(result.get('room_id') or ''),
                'slot_id': str(result.get('slot_id') or ''),
                'model_key': str(result.get('model_key') or ''),
                'attempt_id': str(result.get('selected_attempt_id') or ''),
                'material_attempt_id': '',
                'path': result_path,
                'url': to_url(result_path),
                'thumb': result_thumb_url(result_path),
                'auto_outcome': str(result.get('outcome') or ''),
                'auto_deliverable': bool(result.get('deliverable')),
                'material_status': '',
            })
    return artifacts


def _empty_feedback(run: dict) -> dict:
    return {
        'schema_version': 1,
        'run_id': str(run.get('run_id') or ''),
        'project_id': str(run.get('project_id') or ''),
        'created_at': time.time(),
        'updated_at': time.time(),
        'events': [],
        'current': {'artifact_reviews': {}, 'result_reviews': {}},
    }


def _projection(events: list[dict]) -> dict:
    artifacts: dict[str, dict] = {}
    results: dict[str, dict] = {}
    for event in events:
        if event.get('event_type') != 'review':
            continue
        current = {
            key: copy.deepcopy(event.get(key))
            for key in ('event_id', 'seq', 'at', 'run_id', 'project_id', 'result_id',
                        'artifact_id', 'review_status', 'review_tags', 'review_note',
                        'reviewer_id', 'recipe_path')
        }
        artifact_id = str(event.get('artifact_id') or '')
        if artifact_id:
            artifacts[artifact_id] = current
        else:
            results[str(event.get('result_id') or '')] = current
    return {'artifact_reviews': artifacts, 'result_reviews': results}


def _feedback(run: dict) -> dict:
    payload = _read_json(_feedback_path(str(run.get('run_id') or ''))) or _empty_feedback(run)
    events = payload.get('events') if isinstance(payload.get('events'), list) else []
    payload['events'] = events
    payload['current'] = _projection(events)
    return payload


def _review_state(run: dict, feedback: Optional[dict] = None) -> dict:
    terminal = str(run.get('status') or '') in _TERMINAL_RUN
    artifacts = enumerate_reviewable_artifacts(run) if terminal else []
    feedback = feedback or _feedback(run)
    current = (feedback.get('current') or {}).get('artifact_reviews') or {}
    reviewables = []
    pending_ids = []
    for artifact in artifacts:
        review = copy.deepcopy(current.get(artifact['artifact_id']) or {})
        status = str(review.get('review_status') or 'unreviewed')
        row = {**artifact, 'review_status': status, 'human_review': review or None}
        reviewables.append(row)
        if status == 'unreviewed':
            pending_ids.append(artifact['artifact_id'])
    events = feedback.get('events') or []
    completion_events = [row for row in events if row.get('event_type') == 'review_complete']
    last_completion = completion_events[-1] if completion_events else None
    artifact_ids = [row['artifact_id'] for row in reviewables]
    artifact_set_digest = hashlib.sha256(
        json.dumps(sorted(artifact_ids), separators=(',', ':')).encode('utf-8')).hexdigest()
    latest_review_event_ids = {
        artifact_id: str((current.get(artifact_id) or {}).get('event_id') or '')
        for artifact_id in artifact_ids
    }
    completion_valid = bool(
        last_completion
        and last_completion.get('artifact_set_digest') == artifact_set_digest
        and (last_completion.get('latest_review_event_ids') or {}) == latest_review_event_ids
        and not pending_ids
    )
    if not terminal:
        round_status = 'working'
    elif completion_valid:
        round_status = 'review_complete'
    elif not reviewables:
        round_status = 'review_not_required'
    else:
        round_status = 'awaiting_human_review'
    counts = Counter(row['review_status'] for row in reviewables)
    return {
        'run_id': str(run.get('run_id') or ''),
        'project_id': str(run.get('project_id') or ''),
        'run_status': str(run.get('status') or ''),
        'round_status': round_status,
        'requires_human_review': bool(terminal and reviewables),
        'reviewable_count': len(reviewables),
        'pending_count': len(pending_ids),
        'pending_artifact_ids': pending_ids,
        'can_complete': bool(terminal and not pending_ids and not completion_valid),
        'completed_at': (last_completion or {}).get('at') if round_status == 'review_complete' else None,
        'completion_event_id': (last_completion or {}).get('event_id') if completion_valid else '',
        'artifact_set_digest': artifact_set_digest,
        'latest_review_event_ids': latest_review_event_ids,
        'review_version': len(events),
        'counts': {key: int(counts.get(key, 0)) for key in ('pass', 'backup', 'reject', 'unreviewed')},
        'reviewables': reviewables,
        'result_reviews': copy.deepcopy((feedback.get('current') or {}).get('result_reviews') or {}),
        'event_count': len(events),
    }


def get_run_review_state(run: dict, *, ensure_missing_recipes: bool = False) -> dict:
    """Return review projection without mutating storage by default.

    Recipe materialization belongs to terminal generation and explicit review
    mutations.  A GET request must remain observational in manual-safe mode.
    """
    if (ensure_missing_recipes
            and str(run.get('status') or '') in _TERMINAL_RUN):
        ensure_run_recipes(run)
    return _review_state(run)


def _find_result(run: dict, result_id: str) -> dict:
    result = next((
        row for row in run.get('results') or []
        if str(row.get('result_id') or '') == result_id
    ), None)
    if not result:
        raise WholeHomeLearningError(404, '整屋生成结果不存在')
    return result


def _artifact_context(run: dict, result: dict, artifact: Optional[dict]) -> tuple[dict, dict, dict]:
    attempt: dict = {}
    material: dict = {}
    if artifact:
        for candidate in result.get('attempts') or []:
            if str(candidate.get('attempt_id') or '') == artifact.get('attempt_id'):
                attempt = candidate
                break
        for candidate in attempt.get('material_attempts') or []:
            if str(candidate.get('material_attempt_id') or '') == artifact.get('material_attempt_id'):
                material = candidate
                break
    if not attempt and result.get('selected_attempt_id'):
        attempt = next((
            row for row in result.get('attempts') or []
            if row.get('attempt_id') == result.get('selected_attempt_id')
        ), {})
    return result, attempt, material


def _trace_refs(attempt: dict, material: dict) -> list[dict]:
    rows = []
    for traces in (attempt.get('trace') or [], material.get('trace') or []):
        for source in traces:
            if isinstance(source, dict):
                rows.append({
                    key: copy.deepcopy(source.get(key))
                    for key in ('call_id', 'pass', 'provider', 'model_id', 'resolution',
                                'aspect_ratio', 'prompt_version', 'prompt_sha256', 'success')
                })
    unique = []
    seen = set()
    for row in rows:
        identity = (row.get('call_id'), row.get('pass'), row.get('prompt_sha256'))
        if identity not in seen:
            seen.add(identity)
            unique.append(row)
    return unique


def build_recipe_snapshot(run: dict, result: dict, artifact: Optional[dict]) -> dict:
    result, attempt, material = _artifact_context(run, result, artifact)
    capture_id = str((artifact or {}).get('material_attempt_id') and attempt.get('capture_id')
                     or result.get('capture_id') or attempt.get('capture_id') or '')
    capture = next((
        row for row in run.get('capture_snapshots') or []
        if str(row.get('capture_id') or '') == capture_id
    ), {})
    room_id = str(result.get('room_id') or '')
    slot_id = str(result.get('slot_id') or '')
    contract = next((
        row for row in run.get('room_contract_snapshots') or []
        if str(row.get('room_id') or '') == room_id
        and (not slot_id or str((row.get('reference_slot') or {}).get('slot_id') or '') == slot_id)
    ), {})
    camera = copy.deepcopy(capture.get('camera') or contract.get('camera') or {})
    prompt_records = _trace_refs(attempt, material)
    provider = str(material.get('provider') or attempt.get('provider') or result.get('provider') or '')
    model_id = next((str(row.get('model_id') or '') for row in reversed(prompt_records) if row.get('model_id')), '')
    buffers = {
        key: _file_ref(str(capture.get(f'{key}_path') or ''))
        for key in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'plan_overlay')
    }
    artifact_path = str((artifact or {}).get('path') or '')
    recipe = {
        'schema_version': 1,
        'created_at': (
            material.get('created_at') or attempt.get('created_at')
            or run.get('updated_at') or run.get('created_at')
        ),
        'project_id': str(run.get('project_id') or ''),
        'project_revision': run.get('model_revision'),
        'model_hash': str(run.get('model_hash') or model_hash(run.get('model_snapshot') or {})),
        'run_id': str(run.get('run_id') or ''),
        'result_id': str(result.get('result_id') or ''),
        'artifact_id': str((artifact or {}).get('artifact_id') or ''),
        'attempt_id': str(attempt.get('attempt_id') or ''),
        'material_attempt_id': str(material.get('material_attempt_id') or ''),
        'room': {
            'room_id': room_id,
            'slot_id': slot_id,
            'room_label': contract.get('room_label') or result.get('camera_name') or room_id,
            'profile': contract.get('profile') or '',
            'contract': copy.deepcopy(contract),
        },
        'generation': {
            'model_key': str(result.get('model_key') or ''),
            'provider': provider,
            'model_id': model_id,
            'aspect_ratio': str(run.get('aspect_ratio') or ''),
            'resolution': str(run.get('resolution') or ''),
            'style': str(run.get('style') or ''),
            'lighting': str(run.get('lighting') or ''),
            'user_prompt': str(run.get('prompt') or ''),
            'user_prompt_sha256': str(run.get('request_prompt_sha256') or hashlib.sha256(
                str(run.get('prompt') or '').encode('utf-8')).hexdigest()),
            'prompt_records': prompt_records,
            'material_mode': str(run.get('material_mode') or 'floor_sample'),
            'reference_contract_id': str(run.get('reference_contract_id') or ''),
            'benchmark_batch_id': str(run.get('benchmark_batch_id') or ''),
        },
        'camera': {
            'capture_id': capture_id,
            'camera_id': capture.get('camera_id') or camera.get('id') or '',
            'position': copy.deepcopy(camera.get('position') or {}),
            'target': copy.deepcopy(camera.get('target') or {}),
            'focal_length_mm': camera.get('focal_length_mm'),
            'source': camera.get('source') or '',
            'origin_scope': camera.get('origin_scope') or '',
            'origin_room_ids': copy.deepcopy(camera.get('origin_room_ids') or []),
            'entry_opening_id': camera.get('entry_opening_id') or '',
            'portal_preservation': copy.deepcopy(contract.get('portal_preservation') or {}),
            'render_gate': copy.deepcopy(camera.get('render_gate') or {}),
        },
        'inputs': {
            'floorplan': _file_ref(str(run.get('floorplan_path') or '')),
            'floor_sample': _file_ref(str(run.get('floor_path') or '')),
            'style_reference': _file_ref(str(run.get('style_ref_path') or '')),
            'capture_buffers': buffers,
            'request_input_manifest': _public_reference_value(run.get('input_manifest') or []),
            'cad_source_snapshot': copy.deepcopy(run.get('cad_source_snapshot') or {}),
            'cad_import_snapshot': copy.deepcopy(run.get('cad_import_snapshot') or {}),
            'reference_contract_snapshot': public_reference_contract(
                run.get('reference_contract_snapshot') or {}),
            'reference_asset_snapshots': _public_reference_value(
                run.get('reference_asset_snapshots') or []),
        },
        'automatic_evidence': {
            'outcome': str(result.get('outcome') or ''),
            'deliverable': bool(result.get('deliverable')),
            'result_evaluation': copy.deepcopy(result.get('evaluation')),
            'structure_local_gate': copy.deepcopy(attempt.get('structure_local_gate')),
            'structure_evaluation': copy.deepcopy(attempt.get('structure_evaluation')),
            'final_local_gate': copy.deepcopy(material.get('final_local_gate')),
            'final_evaluation': copy.deepcopy(material.get('evaluation')),
        },
        'artifact': {
            **_file_ref(artifact_path),
            **(_image_dimensions(artifact_path) if artifact_path else {
                'width': None, 'height': None, 'format': ''
            }),
        },
    }
    return _sanitize(_public_reference_value(recipe))


def _save_recipe_once(run: dict, result: dict, artifact: Optional[dict]) -> str:
    identity = str((artifact or {}).get('artifact_id') or f'result:{result.get("result_id")}')
    path = _recipe_path(str(run.get('run_id') or ''), identity)
    if not os.path.isfile(path):
        _atomic_json(path, build_recipe_snapshot(run, result, artifact))
    return path


def ensure_run_recipes(run: dict) -> list[str]:
    """Materialize deterministic per-artifact recipes independently of review."""
    if str(run.get('status') or '') not in _TERMINAL_RUN:
        return []
    result_map = {
        str(row.get('result_id') or ''): row for row in run.get('results') or []
    }
    paths = []
    with _LOCK:
        for artifact in enumerate_reviewable_artifacts(run):
            result = result_map.get(artifact['result_id'])
            if result:
                paths.append(_save_recipe_once(run, result, artifact))
    return paths


def _idempotent_event(feedback: dict, idempotency_key: str, event_type: str,
                      fingerprint: str) -> Optional[dict]:
    found = next((
        row for row in feedback.get('events') or []
        if row.get('idempotency_key') == idempotency_key
    ), None)
    if not found:
        return None
    if found.get('event_type') != event_type or found.get('request_fingerprint') != fingerprint:
        raise WholeHomeLearningError(409, '幂等键已用于不同的人工评审请求')
    return found


def review_result(run: dict, result_id: str, *, artifact_id: str, review_status: str,
                  review_tags: list[str], review_note: str, reviewer_id: str,
                  expected_review_version: int, idempotency_key: str) -> dict:
    if str(run.get('status') or '') not in _TERMINAL_RUN:
        raise WholeHomeLearningError(409, '任务仍在生成，终态后才能人工评审')
    result = _find_result(run, result_id)
    if review_status not in ('unreviewed', 'pass', 'backup', 'reject'):
        raise WholeHomeLearningError(422, '未知的人工评审状态')
    tags = list(dict.fromkeys(str(value).strip() for value in review_tags if str(value).strip()))[:20]
    if review_status == 'reject' and not tags:
        raise WholeHomeLearningError(422, '拒绝图片时至少选择一个失败原因')
    artifacts = {
        row['artifact_id']: row for row in enumerate_reviewable_artifacts(run)
        if row['result_id'] == result_id
    }
    artifact = artifacts.get(artifact_id) if artifact_id else None
    if artifact_id and not artifact:
        raise WholeHomeLearningError(409, '图片工件不存在、已不可打开或不属于该结果')
    if review_status in ('pass', 'backup', 'unreviewed') and not artifact:
        raise WholeHomeLearningError(422, '通过、备选或重置评审必须绑定仍可打开的图片工件')
    # A result-level reject is deliberately allowed for terminal failures that
    # produced no final/material image.  It never counts as room coverage.
    if review_status == 'reject' and not artifact and artifacts:
        raise WholeHomeLearningError(422, '该结果存在可查看图片，请绑定具体图片后拒绝')
    with _LOCK:
        feedback = _feedback(run)
        fingerprint = hashlib.sha256(json.dumps({
            'result_id': result_id, 'artifact_id': artifact_id,
            'review_status': review_status, 'review_tags': tags,
            'review_note': str(review_note or '')[:2000],
            'reviewer_id': str(reviewer_id or 'local-user')[:100],
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        existing = _idempotent_event(feedback, idempotency_key, 'review', fingerprint)
        if existing:
            return {'event': copy.deepcopy(existing), 'review_state': _review_state(run, feedback)}
        if int(expected_review_version) != len(feedback['events']):
            raise WholeHomeLearningError(409, '人工评审版本已更新，请刷新本轮状态后重试')
        recipe_path = _save_recipe_once(run, result, artifact)
        events = feedback['events']
        current_key = artifact_id or result_id
        current_bucket = ('artifact_reviews' if artifact_id else 'result_reviews')
        previous = ((feedback.get('current') or {}).get(current_bucket) or {}).get(current_key) or {}
        event = {
            'event_id': f'review_{uuid.uuid4().hex}',
            'seq': len(events) + 1,
            'event_type': 'review',
            'at': time.time(),
            'run_id': str(run.get('run_id') or ''),
            'project_id': str(run.get('project_id') or ''),
            'result_id': result_id,
            'artifact_id': artifact_id,
            'review_status': review_status,
            'review_tags': tags,
            'review_note': str(review_note or '')[:2000],
            'reviewer_id': str(reviewer_id or 'local-user')[:100],
            'recipe_path': recipe_path,
            'previous_event_id': str(previous.get('event_id') or ''),
            'idempotency_key': idempotency_key,
            'request_fingerprint': fingerprint,
        }
        events.append(event)
        feedback['updated_at'] = event['at']
        feedback['current'] = _projection(events)
        feedback['review_state'] = _review_state(run, feedback)
        _atomic_json(_feedback_path(str(run.get('run_id') or '')), feedback)
    return {
        'event': copy.deepcopy(event),
        'review_state': _review_state(run, feedback),
    }


def complete_run_review(run: dict, reviewer_id: str, *, expected_review_version: int,
                        idempotency_key: str) -> dict:
    if str(run.get('status') or '') not in _TERMINAL_RUN:
        raise WholeHomeLearningError(409, '任务仍在生成，不能完成人工评审')
    with _LOCK:
        feedback = _feedback(run)
        fingerprint = hashlib.sha256(json.dumps({
            'run_id': run.get('run_id'), 'reviewer_id': str(reviewer_id or 'local-user')[:100],
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        existing = _idempotent_event(feedback, idempotency_key, 'review_complete', fingerprint)
        if existing:
            return _review_state(run, feedback)
        if int(expected_review_version) != len(feedback['events']):
            raise WholeHomeLearningError(409, '人工评审版本已更新，请刷新本轮状态后重试')
        state = _review_state(run, feedback)
        if state['pending_count']:
            raise WholeHomeLearningError(
                409, f'仍有 {state["pending_count"]} 张图片未人工标记')
        events = feedback['events']
        event = {
            'event_id': f'review_complete_{uuid.uuid4().hex}',
            'seq': len(events) + 1,
            'event_type': 'review_complete',
            'at': time.time(),
            'run_id': str(run.get('run_id') or ''),
            'project_id': str(run.get('project_id') or ''),
            'reviewer_id': str(reviewer_id or 'local-user')[:100],
            'reviewed_artifact_ids': [row['artifact_id'] for row in state['reviewables']],
            'artifact_set_digest': state['artifact_set_digest'],
            'latest_review_event_ids': copy.deepcopy(state['latest_review_event_ids']),
            'idempotency_key': idempotency_key,
            'request_fingerprint': fingerprint,
        }
        events.append(event)
        feedback['updated_at'] = event['at']
        feedback['current'] = _projection(events)
        feedback['review_state'] = _review_state(run, feedback)
        _atomic_json(_feedback_path(str(run.get('run_id') or '')), feedback)
    return _review_state(run, feedback)


def get_training_consent(project_id: str) -> dict:
    payload = _read_json(_consent_path(project_id))
    if not payload:
        return {
            'schema_version': 1, 'project_id': project_id, 'allowed': False,
            'updated_at': None, 'updated_by': '', 'events': [],
        }
    payload['allowed'] = bool(payload.get('allowed'))
    return payload


def set_training_consent(project: dict, allowed: bool, reviewer_id: str) -> dict:
    project_id = str(project.get('project_id') or '')
    with _LOCK:
        payload = get_training_consent(project_id)
        events = payload.get('events') if isinstance(payload.get('events'), list) else []
        event = {
            'event_id': f'consent_{uuid.uuid4().hex}',
            'seq': len(events) + 1,
            'at': time.time(),
            'allowed': bool(allowed),
            'reviewer_id': str(reviewer_id or 'local-user')[:100],
        }
        events.append(event)
        payload.update(
            schema_version=1, project_id=project_id, allowed=bool(allowed),
            updated_at=event['at'], updated_by=event['reviewer_id'], events=events,
        )
        _atomic_json(_consent_path(project_id), payload)
    return copy.deepcopy(payload)


def list_learning_runs(project_id: str = '') -> list[dict]:
    try:
        names = sorted(
            (name for name in os.listdir(RUN_DIR) if name.endswith('.json')),
            key=lambda name: os.path.getmtime(os.path.join(RUN_DIR, name)),
        )
    except OSError:
        return []
    rows = []
    for name in names:
        run = load_run(os.path.splitext(name)[0])
        if run and (not project_id or str(run.get('project_id') or '') == project_id):
            rows.append(run)
    return rows


def generation_spec_hash(run: dict) -> str:
    reference_assets = sorted((
        {
            'asset_id': str((slot.get('reference_asset') or {}).get('asset_id') or ''),
            'sha256': str((slot.get('reference_asset') or {}).get('sha256') or ''),
        }
        for slot in (run.get('reference_contract_snapshot') or {}).get('slots') or []
        if isinstance(slot, dict)
    ), key=lambda row: (row['asset_id'], row['sha256']))
    payload = {
        'model_hash': str(run.get('model_hash') or ''),
        'floor_sha256': _sha256_file(str(run.get('floor_path') or '')),
        'style_ref_sha256': _sha256_file(str(run.get('style_ref_path') or '')),
        'prompt_sha256': str(run.get('request_prompt_sha256') or hashlib.sha256(
            str(run.get('prompt') or '').encode('utf-8')).hexdigest()),
        'style': str(run.get('style') or ''),
        'lighting': str(run.get('lighting') or ''),
        'aspect_ratio': str(run.get('aspect_ratio') or ''),
        'resolution': str(run.get('resolution') or ''),
        'material_mode': str(run.get('material_mode') or 'floor_sample'),
        'scene_recipe_id': str(run.get('scene_recipe_id') or ''),
        'scene_hash': str(run.get('scene_hash') or ''),
        'scene_recipe_hash': str((run.get('scene_recipe_snapshot') or {}).get('recipe_hash') or ''),
        'reference_contract_id': str(run.get('reference_contract_id') or ''),
        'reference_assets': reference_assets,
        'benchmark_batch_id': str(run.get('benchmark_batch_id') or ''),
        'cad_facts_hash': str((run.get('cad_import_snapshot') or {}).get('cad_facts_hash') or ''),
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')).hexdigest()


def workflow_identity(run: dict) -> tuple[str, str]:
    return (
        str(run.get('workflow_id') or run.get('run_id') or ''),
        str(run.get('generation_spec_hash') or generation_spec_hash(run)),
    )


def workflow_covered_room_ids(run: dict) -> list[str]:
    workflow_id, spec_hash = workflow_identity(run)
    covered: set[str] = set()
    for candidate in list_learning_runs(str(run.get('project_id') or '')):
        if workflow_identity(candidate) != (workflow_id, spec_hash):
            continue
        current = (_feedback(candidate).get('current') or {}).get('artifact_reviews') or {}
        for artifact in enumerate_reviewable_artifacts(candidate):
            review = current.get(artifact['artifact_id']) or {}
            target_id = (artifact.get('slot_id') if candidate.get('material_mode') == 'reference'
                         else artifact.get('room_id'))
            if review.get('review_status') == 'pass' and target_id:
                covered.add(str(target_id))
    return sorted(covered)


def _selected_room_ids(project: Optional[dict]) -> list[str]:
    if not project or not project.get('verified'):
        return []
    rooms = (project.get('model') or {}).get('rooms') or []
    explicit = [row for row in rooms if row.get('selected') is True]
    selected = explicit if explicit else [row for row in rooms if row.get('selected') is not False]
    return [str(row.get('id') or '') for row in selected if row.get('id')]


def learning_summary(project_id: str = '') -> dict:
    runs = list_learning_runs(project_id)
    total = Counter()
    auto = Counter()
    room_rows: dict[str, Counter] = defaultdict(Counter)
    model_rows: dict[str, Counter] = defaultdict(Counter)
    grouped_covered: dict[tuple[str, str], set[str]] = defaultdict(set)
    grouped_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    grouped_scope: dict[tuple[str, str], str] = {}
    group_created_at: dict[tuple[str, str], float] = {}
    weak_no_image = 0
    for run in runs:
        group = workflow_identity(run)
        reference_scope = run.get('material_mode') == 'reference'
        grouped_scope[group] = 'reference_slot' if reference_scope else 'room'
        grouped_targets[group].update(
            str((row.get('slot_id') if reference_scope else row.get('room_id')) or '')
            for row in run.get('capture_groups') or []
            if str((row.get('slot_id') if reference_scope else row.get('room_id')) or '')
        )
        group_created_at[group] = max(group_created_at.get(group, 0), float(run.get('created_at') or 0))
        feedback = _feedback(run)
        artifact_reviews = (feedback.get('current') or {}).get('artifact_reviews') or {}
        result_reviews = (feedback.get('current') or {}).get('result_reviews') or {}
        by_result: dict[str, list[dict]] = defaultdict(list)
        for artifact in enumerate_reviewable_artifacts(run):
            by_result[artifact['result_id']].append(artifact)
            review = artifact_reviews.get(artifact['artifact_id']) or {}
            status = str(review.get('review_status') or 'unreviewed')
            total[status] += 1
            room_key = str(artifact.get('room_id') or '')
            model_key = str(artifact.get('model_key') or '')
            room_rows[room_key][status] += 1
            model_rows[model_key][status] += 1
            coverage_key = str((artifact.get('slot_id') if reference_scope else room_key) or '')
            if status == 'pass' and coverage_key:
                grouped_covered[group].add(coverage_key)
        for result in run.get('results') or []:
            outcome = str(result.get('outcome') or 'unknown')
            auto[f'outcome:{outcome}'] += 1
            auto['deliverable:true' if result.get('deliverable') else 'deliverable:false'] += 1
            evaluation = result.get('evaluation') if isinstance(result.get('evaluation'), dict) else {}
            qa_status = ('pass' if evaluation.get('gate_pass') else
                         'unavailable' if evaluation.get('status') == 'unavailable' else 'fail')
            auto[f'qa:{qa_status}'] += 1
            result_id = str(result.get('result_id') or '')
            if not by_result.get(result_id):
                explicit = result_reviews.get(result_id) or {}
                if explicit.get('review_status') == 'reject':
                    total['reject'] += 1
                    room_rows[str(result.get('room_id') or '')]['reject'] += 1
                    model_rows[str(result.get('model_key') or '')]['reject'] += 1
                else:
                    weak_no_image += 1
    project = load_project(project_id) if project_id else None
    latest_group = max(group_created_at, key=group_created_at.get) if group_created_at else ('', '')
    selected = (sorted(grouped_targets.get(latest_group, set()))
                if grouped_scope.get(latest_group) == 'reference_slot'
                else _selected_room_ids(project))
    covered = grouped_covered.get(latest_group, set())
    uncovered = [room_id for room_id in selected if room_id not in covered]
    workflow_summaries = [{
        'workflow_id': key[0], 'generation_spec_hash': key[1],
        'coverage_scope': grouped_scope.get(key, 'room'),
        'target_ids': sorted(grouped_targets.get(key, set())),
        'covered_target_ids': sorted(grouped_covered.get(key, set())),
        'covered_room_ids': sorted(grouped_covered.get(key, set())),
        'covered_room_count': len(grouped_covered.get(key, set()) & (
            grouped_targets.get(key, set()) or set(selected))),
        'latest_created_at': group_created_at[key],
    } for key in sorted(group_created_at, key=group_created_at.get, reverse=True)]
    return {
        'project_id': project_id or None,
        'counts': {key: int(total.get(key, 0)) for key in ('pass', 'backup', 'reject', 'unreviewed')},
        'strong_label_count': int(total['pass'] + total['backup'] + total['reject']),
        'weak_unreviewed_result_count': weak_no_image,
        'auto_signals': dict(sorted(auto.items())),
        'by_room': {
            key: {status: int(values.get(status, 0)) for status in ('pass', 'backup', 'reject', 'unreviewed')}
            for key, values in sorted(room_rows.items()) if key
        },
        'by_model': {
            key: {status: int(values.get(status, 0)) for status in ('pass', 'backup', 'reject', 'unreviewed')}
            for key, values in sorted(model_rows.items()) if key
        },
        'covered_room_ids': sorted(covered),
        'selected_room_ids': selected,
        'uncovered_room_ids': uncovered,
        'coverage_scope': grouped_scope.get(latest_group, 'room'),
        'covered_target_ids': sorted(covered),
        'selected_target_ids': selected,
        'uncovered_target_ids': uncovered,
        'covered_room_count': len(set(selected) & covered) if selected else len(covered),
        'selected_room_count': len(selected),
        'training_consent': get_training_consent(project_id) if project_id else None,
        'run_count': len(runs),
        'workflow_summaries': workflow_summaries,
        'active_workflow_id': latest_group[0] or None,
        'active_generation_spec_hash': latest_group[1] or None,
    }


def project_learning_projection(project: dict) -> dict:
    project_id = str(project.get('project_id') or '')
    summary = learning_summary(project_id)
    return {
        'training_consent': summary['training_consent'],
        'counts': summary['counts'],
        'covered_room_ids': summary['covered_room_ids'],
        'uncovered_room_ids': summary['uncovered_room_ids'],
        'covered_room_count': summary['covered_room_count'],
        'selected_room_count': summary['selected_room_count'],
    }


def _safe_export_file(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ''
    real = os.path.realpath(path)
    for root in (MAIN_OUTPUT_DIR, UPLOAD_DIR):
        try:
            if os.path.commonpath([real, os.path.realpath(root)]) == os.path.realpath(root):
                return real
        except (OSError, ValueError):
            continue
    return ''


def _evidence_paths(run: dict, result: dict, artifact: Optional[dict]) -> list[tuple[str, str]]:
    rows = [
        ('floorplan', str(run.get('floorplan_path') or '')),
        ('floor_sample', str(run.get('floor_path') or '')),
        ('style_reference', str(run.get('style_ref_path') or '')),
    ]
    _, attempt, material = _artifact_context(run, result, artifact)
    capture_id = str(attempt.get('capture_id') or result.get('capture_id') or '')
    capture = next((row for row in run.get('capture_snapshots') or []
                    if str(row.get('capture_id') or '') == capture_id), {})
    rows.extend((f'capture_{key}', str(capture.get(f'{key}_path') or ''))
                for key in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'plan_overlay'))
    rows.append(('structure', str(attempt.get('structure_path') or result.get('structure_path') or '')))
    for key in ('api_original', 'material', 'corrected', 'final'):
        rows.append((key, str(material.get(f'{key}_path') or '')))
    rows.append(('structure_gate_overlay', str((attempt.get('structure_local_gate') or {}).get('overlay_path') or '')))
    rows.append(('final_gate_overlay', str((material.get('final_local_gate') or {}).get('overlay_path') or '')))
    if artifact:
        rows.append(('review_artifact', str(artifact.get('path') or '')))
    unique = []
    seen = set()
    for role, path in rows:
        real = _safe_export_file(path)
        if real and real not in seen:
            seen.add(real)
            unique.append((role, real))
    return unique


def _export_recipe(run: dict, result: dict, artifact: Optional[dict], review: dict) -> dict:
    persisted = str(review.get('recipe_path') or '')
    recipe = _read_json(persisted) if persisted else None
    return _sanitize(recipe or build_recipe_snapshot(run, result, artifact))


def _relative_export_recipe(value: Any, source_to_archive: dict[str, str], key: str = '') -> Any:
    if isinstance(value, dict):
        return {
            str(child_key): _relative_export_recipe(child, source_to_archive, str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_relative_export_recipe(child, source_to_archive, key) for child in value]
    if isinstance(value, str):
        if key == 'path' or key.endswith('_path'):
            real = _safe_export_file(value)
            return source_to_archive.get(real, '') if real else ''
        return _ABSOLUTE_WINDOWS_PATH.sub('[local-path]', value)
    return value


def build_learning_export(project_id: str = '') -> str:
    if project_id:
        project = load_project(project_id)
        if not project:
            raise WholeHomeLearningError(404, '整屋项目不存在')
        if not get_training_consent(project_id).get('allowed'):
            raise WholeHomeLearningError(403, '该项目尚未授权进入本地优化数据集')
        allowed_projects = {project_id}
    else:
        allowed_projects = {
            str(run.get('project_id') or '') for run in list_learning_runs()
            if get_training_consent(str(run.get('project_id') or '')).get('allowed')
        }
    runs = [run for run in list_learning_runs() if str(run.get('project_id') or '') in allowed_projects]
    os.makedirs(EXPORT_DIR, exist_ok=True)
    export_name = f'whole_home_learning_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:8]}.zip'
    path = os.path.join(EXPORT_DIR, export_name)
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    manifest: list[dict] = []
    written_files: dict[str, str] = {}
    with _LOCK:
        try:
            with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for run in runs:
                    feedback = _feedback(run)
                    artifact_reviews = (feedback.get('current') or {}).get('artifact_reviews') or {}
                    result_reviews = (feedback.get('current') or {}).get('result_reviews') or {}
                    artifacts_by_result: dict[str, list[dict]] = defaultdict(list)
                    for artifact in enumerate_reviewable_artifacts(run):
                        artifacts_by_result[artifact['result_id']].append(artifact)
                    for result in run.get('results') or []:
                        result_id = str(result.get('result_id') or '')
                        artifacts: list[Optional[dict]] = artifacts_by_result.get(result_id) or [None]
                        for artifact in artifacts:
                            review = (artifact_reviews.get(artifact['artifact_id']) if artifact
                                      else result_reviews.get(result_id)) or {}
                            manual_status = str(review.get('review_status') or 'unreviewed')
                            strong = manual_status in ('pass', 'backup', 'reject')
                            identity = str((artifact or {}).get('artifact_id') or f'result:{result_id}')
                            files = []
                            source_to_archive = {}
                            for role, source in _evidence_paths(run, result, artifact):
                                sha256 = _sha256_file(source)
                                extension = os.path.splitext(source)[1].lower()[:12]
                                arcname = written_files.get(sha256)
                                if not arcname:
                                    arcname = f'artifacts/{sha256[:2]}/{sha256}{extension}'
                                    archive.write(source, arcname)
                                    written_files[sha256] = arcname
                                source_to_archive[source] = arcname
                                files.append({'role': role, 'archive_path': arcname, 'sha256': sha256})
                            recipe = _relative_export_recipe(
                                _export_recipe(run, result, artifact, review), source_to_archive)
                            recipe = _sanitize(recipe)
                            recipe_arc = f'recipes/{_basename(run.get("run_id"))}/{hashlib.sha256(identity.encode()).hexdigest()[:20]}.json'
                            archive.writestr(recipe_arc, json.dumps(recipe, ensure_ascii=False, indent=2))
                            evaluation = result.get('evaluation') if isinstance(result.get('evaluation'), dict) else {}
                            manifest.append(_sanitize({
                                'schema_version': 1,
                                'project_id': run.get('project_id'),
                                'run_id': run.get('run_id'),
                                'result_id': result_id,
                                'artifact_id': (artifact or {}).get('artifact_id') or '',
                                'room_id': result.get('room_id') or '',
                                'model_key': result.get('model_key') or '',
                                'label_strength': 'strong' if strong else 'weak',
                                'manual_status': manual_status,
                                'review_tags': copy.deepcopy(review.get('review_tags') or []),
                                'review_note': str(review.get('review_note') or ''),
                                'auto_outcome': result.get('outcome') or '',
                                'auto_deliverable': bool(result.get('deliverable')),
                                'auto_qa_status': evaluation.get('status') or '',
                                'auto_qa_gate_pass': bool(evaluation.get('gate_pass')),
                                'recipe_path': recipe_arc,
                                'files': files,
                            }))
                archive.writestr(
                    'manifest.jsonl',
                    ''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n'
                            for row in manifest),
                )
                archive.writestr('export.json', json.dumps({
                    'schema_version': 1,
                    'created_at': time.time(),
                    'project_filter': project_id or None,
                    'consented_project_ids': sorted(allowed_projects),
                    'sample_count': len(manifest),
                    'strong_label_count': sum(row['label_strength'] == 'strong' for row in manifest),
                    'weak_label_count': sum(row['label_strength'] == 'weak' for row in manifest),
                }, ensure_ascii=False, indent=2))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
    return path
