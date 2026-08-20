# -*- coding: utf-8 -*-
"""Manual-safe release controls for the whole-home pipeline.

This module is intentionally provider-free.  It owns feature flags, service
ownership, immutable manual-run previews and the guarded legacy-ledger repair
contract.  Provider dispatch remains in ``routes_whole_home`` and is reachable
only after a separate paid opt-in and a one-time preview confirmation.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import MAIN_OUTPUT_DIR
from .whole_home_autopilot import (
    DevelopmentAutopilotError,
    reconcile_development_session,
)
from .whole_home_dev_lock import (
    data_root_lock,
    fsync_directory,
)


MANUAL_SAFE_ENV = 'FLOOR_WHOLE_HOME_MANUAL_SAFE'
MANUAL_PAID_ENV = 'FLOOR_WHOLE_HOME_MANUAL_ALLOW_PAID'
WORKFLOW_ENV = 'FLOOR_WHOLE_HOME_ENABLE_AGENT_WORKFLOW'
EXTERNAL_REVIEW_ENV = 'FLOOR_WHOLE_HOME_ENABLE_EXTERNAL_REVIEW'
REFERENCE_ENV = 'FLOOR_WHOLE_HOME_ENABLE_REFERENCE_WIP'
DEVELOPMENT_PAID_ENV = 'FLOOR_WHOLE_HOME_ENABLE_DEVELOPMENT_PAID'
MANUAL_POLICY = 'manual_safe_v1'
MANUAL_IMAGE_CALL_CAP = 4
MANUAL_QA_CALL_CAP = 8
MANUAL_PREVIEW_TTL_SECONDS = 15 * 60
_SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$')
_PREVIEW_LOCK = threading.RLock()
_PREVIEWS: dict[str, dict] = {}


def _enabled(name: str, *, default: bool = False,
             environ: Optional[dict[str, str]] = None) -> bool:
    source = os.environ if environ is None else environ
    raw = source.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def manual_safe_enabled(environ: Optional[dict[str, str]] = None) -> bool:
    return _enabled(MANUAL_SAFE_ENV, default=True, environ=environ)


def manual_paid_enabled(environ: Optional[dict[str, str]] = None) -> bool:
    return _enabled(MANUAL_PAID_ENV, environ=environ)


def feature_enabled(feature: str,
                    environ: Optional[dict[str, str]] = None) -> bool:
    mapping = {
        'agent_workflow': WORKFLOW_ENV,
        'external_review': EXTERNAL_REVIEW_ENV,
        'reference_wip': REFERENCE_ENV,
        'development_paid': DEVELOPMENT_PAID_ENV,
        'manual_paid': MANUAL_PAID_ENV,
    }
    if feature not in mapping:
        return False
    return _enabled(mapping[feature], environ=environ)


def capabilities(environ: Optional[dict[str, str]] = None) -> dict:
    return {
        'schema_version': 1,
        'mode': MANUAL_POLICY if manual_safe_enabled(environ) else 'legacy_unsafe',
        'manual_safe': manual_safe_enabled(environ),
        'manual_paid': manual_paid_enabled(environ),
        'feature_flags': {
            key: feature_enabled(key, environ)
            for key in (
                'agent_workflow', 'external_review', 'reference_wip',
                'development_paid')
        },
        'startup': {
            'authoritative_migration': False,
            'interruption_recovery': False,
            'autopilot_reconciliation': False,
            'single_data_root_owner': True,
        },
        'manual_run_contract': {
            'material_modes': ['floor_sample', 'style_pack'],
            'capture_count': 1,
            'fallback_capture_count': 0,
            'model_count': 1,
            'candidates_per_camera': 1,
            'resolution': '2K',
            'image_call_cap': MANUAL_IMAGE_CALL_CAP,
            'qa_call_cap': MANUAL_QA_CALL_CAP,
            'requires_preview': True,
            'requires_dynamic_confirmation': True,
        },
    }


def request_feature_for_path(path: str, method: str,
                             *, body: Optional[dict] = None,
                             environ: Optional[dict[str, str]] = None) -> str:
    """Return the disabled feature name for an HTTP request, or ``''``."""
    route = str(path or '')
    verb = str(method or '').upper()
    if '/api/whole-home/development-workflows' in route:
        return '' if feature_enabled('agent_workflow', environ) else 'agent_workflow'
    if '/api/whole-home/development-reviews' in route:
        return '' if feature_enabled('external_review', environ) else 'external_review'
    if ('/api/whole-home/projects/' in route
            and ('/reference-' in route or '/reference/' in route)):
        return '' if feature_enabled('reference_wip', environ) else 'reference_wip'
    if route.endswith('/camera-candidates') and str((body or {}).get('mode') or '') == 'reference':
        return '' if feature_enabled('reference_wip', environ) else 'reference_wip'
    if (route.endswith('/captures')
            and ((body or {}).get('reference_proposal_id')
                 or (body or {}).get('reference_slot_id'))):
        return '' if feature_enabled('reference_wip', environ) else 'reference_wip'
    if '/api/whole-home/development-autopilot' in route:
        return '' if feature_enabled('development_paid', environ) else 'development_paid'
    if manual_safe_enabled(environ) and verb == 'POST':
        if route == '/api/whole-home/runs':
            return 'ordinary_paid_run'
        if route.endswith('/continue'):
            return 'continuation'
        if route.endswith('/qa/retry'):
            return 'qa_retry'
    return ''


@contextmanager
def service_owner(data_root: str, *, timeout: float = 0.1) -> Iterator[str]:
    """Fence a second service process for the same data root for its lifetime."""
    with data_root_lock(
            data_root, 'manual-service-owner', timeout=timeout) as path:
        yield path


def _canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(path: str, label: str, *, required: bool = True) -> dict:
    resolved = os.path.realpath(str(path or ''))
    if not resolved or not os.path.isfile(resolved):
        if required:
            raise DevelopmentAutopilotError(
                'manual_input_file_missing', f'{label} 文件不存在', 409)
        return {'label': label, 'path': '', 'sha256': '', 'byte_length': 0}
    stat = os.stat(resolved)
    return {
        'label': label, 'path': resolved,
        'sha256': file_sha256(resolved),
        'byte_length': int(stat.st_size),
        'mtime_ns': int(stat.st_mtime_ns),
    }


def normalize_manual_run_request(value: dict) -> dict:
    source = copy.deepcopy(value or {})
    capture_ids = list(dict.fromkeys(
        str(item or '').strip() for item in source.get('capture_ids') or []
        if str(item or '').strip()))
    model_keys = list(dict.fromkeys(
        str(item or '').strip() for item in source.get('model_keys') or []
        if str(item or '').strip()))
    violations = []
    if len(capture_ids) != 1:
        violations.append('exactly_one_capture_required')
    if source.get('capture_groups'):
        violations.append('capture_groups_and_fallbacks_forbidden')
    if len(model_keys) != 1 or model_keys[0] not in {'b2', 'pro'}:
        violations.append('exactly_one_supported_model_required')
    material_mode = str(source.get('material_mode') or '')
    if material_mode not in {'floor_sample', 'style_pack'}:
        violations.append('supported_material_mode_required')
    if int(source.get('candidates_per_camera') or 0) != 1:
        violations.append('one_candidate_required')
    if str(source.get('resolution') or '') != '2K':
        violations.append('2k_resolution_required')
    if str(source.get('reference_contract_id') or ''):
        violations.append('reference_contract_forbidden')
    if str(source.get('benchmark_batch_id') or ''):
        violations.append('benchmark_batch_forbidden')
    if material_mode == 'floor_sample' and not str(source.get('floor_path') or '').strip():
        violations.append('floor_sample_path_required')
    if material_mode == 'style_pack' and not str(source.get('scene_recipe_id') or '').strip():
        violations.append('locked_scene_recipe_required')
    if material_mode == 'style_pack' and (
            str(source.get('floor_path') or '').strip()
            or str(source.get('style_ref_path') or '').strip()):
        violations.append('style_pack_external_material_forbidden')
    if violations:
        raise DevelopmentAutopilotError(
            'manual_run_contract_invalid',
            '手动安全提交不满足最小付费合同', 422,
            {'violations': violations})
    return {
        'project_id': str(source.get('project_id') or '').strip(),
        'capture_ids': capture_ids,
        'capture_groups': [],
        'floor_path': str(source.get('floor_path') or '').strip(),
        'material_mode': material_mode,
        'scene_recipe_id': str(source.get('scene_recipe_id') or '').strip(),
        'reference_contract_id': '', 'benchmark_batch_id': '',
        'style_ref_path': str(source.get('style_ref_path') or '').strip() or None,
        'prompt': str(source.get('prompt') or '')[:5000],
        'style': str(source.get('style') or '现代自然')[:200],
        'lighting': str(source.get('lighting') or '自然日光')[:200],
        'model_keys': model_keys,
        'candidates_per_camera': 1,
        'aspect_ratio': str(source.get('aspect_ratio') or '4:3'),
        'resolution': '2K',
        'idempotency_key': str(source.get('idempotency_key') or '').strip(),
    }


def _manual_input_manifest(project: dict, request: dict) -> list[dict]:
    capture_id = request['capture_ids'][0]
    capture = next((row for row in project.get('captures') or []
                    if str(row.get('capture_id') or '') == capture_id), None)
    if not capture or capture.get('status') != 'confirmed':
        raise DevelopmentAutopilotError(
            'manual_capture_not_confirmed', '指定机位不存在或未确认', 409)
    if str(capture.get('aspect_ratio') or '') != request['aspect_ratio']:
        raise DevelopmentAutopilotError(
            'manual_capture_aspect_ratio_mismatch', '机位画幅与请求不一致', 409)
    if request['material_mode'] == 'style_pack':
        recipe_id = str(request.get('scene_recipe_id') or '')
        recipe = next((row for row in project.get('scene_recipes') or []
                       if str(row.get('recipe_id') or '') == recipe_id), None)
        if (not recipe or recipe.get('status') != 'locked'
                or str(project.get('active_scene_recipe_id') or '') != recipe_id):
            raise DevelopmentAutopilotError(
                'manual_scene_recipe_not_locked', '固定风格生成必须绑定当前已锁定方案', 409)
        if (str(capture.get('scene_recipe_id') or '') != recipe_id
                or str(capture.get('scene_hash') or '') != str(recipe.get('scene_hash') or '')):
            raise DevelopmentAutopilotError(
                'manual_capture_scene_mismatch', '机位不是从当前锁定方案生成，请重新保存机位', 409)
    evidence = [_file_evidence(str(project.get('floorplan_path') or ''), 'floorplan')]
    if request['material_mode'] == 'floor_sample':
        evidence.append(_file_evidence(request['floor_path'], 'floor_sample'))
    if request.get('style_ref_path'):
        evidence.append(_file_evidence(request['style_ref_path'], 'style_ref'))
    for key in ('rgb_path', 'depth_path', 'normal_path', 'edge_path', 'semantic_path'):
        evidence.append(_file_evidence(
            str(capture.get(key) or ''), f'capture:{capture_id}:{key}',
            required=key != 'edge_path'))
    return sorted(evidence, key=lambda row: row['label'])


def _preview_snapshot(project: dict, request: dict) -> dict:
    return {
        'project_id': str(project.get('project_id') or ''),
        'project_revision': int(project.get('revision') or 0),
        'verified_revision': int(project.get('verified_revision') or 0),
        'project_state_sha256': _canonical_hash(project),
        'request': copy.deepcopy(request),
        'request_sha256': _canonical_hash(request),
        'input_manifest': _manual_input_manifest(project, request),
        'caps': {
            'image_calls': MANUAL_IMAGE_CALL_CAP,
            'qa_calls': MANUAL_QA_CALL_CAP,
        },
    }


def create_manual_run_preview(*, project: dict, request: dict) -> dict:
    normalized = normalize_manual_run_request(request)
    if not project.get('verified'):
        raise DevelopmentAutopilotError(
            'manual_project_not_verified', '整屋项目尚未锁定', 409)
    snapshot = _preview_snapshot(project, normalized)
    snapshot_sha256 = _canonical_hash(snapshot)
    preview_id = 'manual_preview_' + secrets.token_hex(12)
    phrase = f'确认付费 {preview_id[-8:]} {snapshot_sha256[:8]}'
    now = time.time()
    row = {
        'preview_id': preview_id,
        'snapshot': snapshot,
        'snapshot_sha256': snapshot_sha256,
        'confirmation_phrase': phrase,
        'status': 'previewed',
        'created_at': now,
        'expires_at': now + MANUAL_PREVIEW_TTL_SECONDS,
    }
    with _PREVIEW_LOCK:
        _PREVIEWS[preview_id] = row
    return {
        'schema_version': 1,
        'preview_id': preview_id,
        'preview_sha256': snapshot_sha256,
        'confirmation_phrase': phrase,
        'expires_at': row['expires_at'],
        'request': copy.deepcopy(normalized),
        'input_manifest': copy.deepcopy(snapshot['input_manifest']),
        'caps': copy.deepcopy(snapshot['caps']),
        'paid_enabled': manual_paid_enabled(),
    }


def claim_manual_run_commit(*, preview_id: str, preview_sha256: str,
                            confirmation_phrase: str,
                            project: dict) -> dict:
    if not manual_paid_enabled():
        raise DevelopmentAutopilotError(
            'manual_paid_not_enabled',
            '本服务未使用 -AllowPaid 启动，手动付费提交保持关闭', 409)
    with _PREVIEW_LOCK:
        row = _PREVIEWS.get(str(preview_id or ''))
        if not row:
            raise DevelopmentAutopilotError(
                'manual_preview_not_found', '手动预览不存在或服务已重启', 409)
        if row.get('status') != 'previewed':
            raise DevelopmentAutopilotError(
                'manual_preview_already_consumed', '手动预览已被提交或占用', 409)
        if float(row.get('expires_at') or 0) <= time.time():
            raise DevelopmentAutopilotError(
                'manual_preview_expired', '手动预览已过期，请重新预览', 409)
        if not hmac.compare_digest(
                str(row.get('snapshot_sha256') or ''), str(preview_sha256 or '')):
            raise DevelopmentAutopilotError(
                'manual_preview_hash_mismatch', '手动预览 hash 不匹配', 409)
        if not hmac.compare_digest(
                str(row.get('confirmation_phrase') or '').encode('utf-8'),
                str(confirmation_phrase or '').encode('utf-8')):
            raise DevelopmentAutopilotError(
                'manual_confirmation_mismatch', '动态确认短语不匹配', 409)
        request = copy.deepcopy((row.get('snapshot') or {}).get('request') or {})
        current = _preview_snapshot(project, request)
        current_sha = _canonical_hash(current)
        if not hmac.compare_digest(current_sha, str(row.get('snapshot_sha256') or '')):
            raise DevelopmentAutopilotError(
                'manual_preview_inputs_changed',
                '项目、机位或输入文件已变化，请重新预览', 409,
                {'expected_preview_sha256': row.get('snapshot_sha256'),
                 'actual_preview_sha256': current_sha})
        row['status'] = 'committing'
        row['commit_claimed_at'] = time.time()
        return {
            'preview_id': preview_id,
            'preview_sha256': current_sha,
            'request': request,
            'caps': copy.deepcopy(current['caps']),
        }


def get_manual_preview_project_id(preview_id: str) -> str:
    """Resolve only the server-held project binding for a commit request."""
    with _PREVIEW_LOCK:
        row = _PREVIEWS.get(str(preview_id or ''))
        if not row:
            raise DevelopmentAutopilotError(
                'manual_preview_not_found', '手动预览不存在或服务已重启', 409)
        return str(((row.get('snapshot') or {}).get('project_id') or ''))


def finish_manual_run_commit(preview_id: str, *, success: bool,
                             run_id: str = '') -> None:
    with _PREVIEW_LOCK:
        row = _PREVIEWS.get(str(preview_id or ''))
        if not row or row.get('status') != 'committing':
            return
        if success:
            row.update(
                status='committed', committed_at=time.time(),
                run_id=str(run_id or ''))
        else:
            row.update(status='previewed', commit_claimed_at=None)


def _run_manifest(run_paths: list[str]) -> tuple[list[dict], str]:
    rows = []
    for path in sorted(set(os.path.realpath(value) for value in run_paths)):
        if not os.path.isfile(path):
            raise DevelopmentAutopilotError(
                'manual_reconcile_run_missing', f'run 文件不存在: {path}', 422)
        with open(path, 'r', encoding='utf-8') as handle:
            document = json.load(handle)
        if not isinstance(document, dict):
            raise DevelopmentAutopilotError(
                'manual_reconcile_run_corrupt', f'run 文件不是对象: {path}', 409)
        rows.append({
            'path': path, 'sha256': file_sha256(path),
            'byte_length': os.path.getsize(path),
            'run_id': str(document.get('run_id') or ''),
            'document': document,
        })
    digest_rows = [{key: row[key] for key in (
        'path', 'sha256', 'byte_length', 'run_id')} for row in rows]
    return rows, _canonical_hash(digest_rows)


def preview_legacy_reconciliation(*, session_path: str,
                                  run_paths: list[str]) -> dict:
    session_path = os.path.realpath(session_path)
    if not os.path.isfile(session_path):
        raise DevelopmentAutopilotError(
            'manual_reconcile_session_missing', 'session 文件不存在', 422)
    session_sha = file_sha256(session_path)
    with open(session_path, 'r', encoding='utf-8') as handle:
        raw_session = json.load(handle)
    if not isinstance(raw_session, dict):
        raise DevelopmentAutopilotError(
            'manual_reconcile_session_corrupt', 'session 文件不是对象', 409)
    session_id = str(raw_session.get('session_id') or '')
    if not _SAFE_ID.fullmatch(session_id):
        raise DevelopmentAutopilotError(
            'manual_reconcile_session_id_invalid', 'session_id 无效', 422)
    rows, run_manifest_sha = _run_manifest(run_paths)
    expected_run_ids = sorted(set(
        str(value or '').strip()
        for value in [
            *(raw_session.get('runs') or []),
            *(row.get('run_id') for row in raw_session.get('batches') or []
              if isinstance(row, dict)),
        ]
        if str(value or '').strip()))
    actual_run_ids = sorted(
        str(row.get('run_id') or '').strip() for row in rows
        if str(row.get('run_id') or '').strip())
    if (len(actual_run_ids) != len(set(actual_run_ids))
            or actual_run_ids != expected_run_ids):
        raise DevelopmentAutopilotError(
            'manual_reconcile_run_manifest_incomplete',
            'run 文件集合必须与 session/batch 绑定的 run_id 完全一致', 409,
            {'expected_run_ids': expected_run_ids,
             'actual_run_ids': actual_run_ids})
    # The existing reconciler is file-root scoped and provider-free.  Point it
    # at the explicitly named session only for this local CLI operation.
    from . import whole_home_autopilot
    previous_root = whole_home_autopilot.SESSION_DIR
    previous_opt_in = os.environ.get('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT')
    try:
        whole_home_autopilot.SESSION_DIR = os.path.dirname(session_path)
        os.environ['FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT'] = 'true'
        plan = reconcile_development_session(
            session_id, [row['document'] for row in rows], apply=False,
            expected_state_version=int(raw_session.get('state_version') or 0))
    finally:
        whole_home_autopilot.SESSION_DIR = previous_root
        if previous_opt_in is None:
            os.environ.pop('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT', None)
        else:
            os.environ['FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT'] = previous_opt_in
    phrase = f'APPLY MANUAL RECONCILIATION {session_id} {plan["plan_hash"][:12]}'
    return {
        'schema_version': 1,
        'mode': 'dry_run',
        'session_path': session_path,
        'session_id': session_id,
        'session_sha256': session_sha,
        'state_version': int(raw_session.get('state_version') or 0),
        'run_manifest_sha256': run_manifest_sha,
        'run_manifest': [{key: row[key] for key in (
            'path', 'sha256', 'byte_length', 'run_id')} for row in rows],
        'plan_hash': str(plan.get('plan_hash') or ''),
        'confirmation_phrase': phrase,
        'plan': plan,
        'provider_imported': False,
        'provider_calls': 0,
    }


def _apply_legacy_reconciliation_owned(*, session_path: str,
                                       run_paths: list[str],
                                       expected_session_sha256: str,
                                       expected_run_manifest_sha256: str,
                                       expected_state_version: int,
                                       expected_plan_hash: str,
                                       idempotency_key: str,
                                       confirmation_phrase: str,
                                       backup_dir: str) -> dict:
    preview = preview_legacy_reconciliation(
        session_path=session_path, run_paths=run_paths)
    expected = {
        'session_sha256': expected_session_sha256,
        'run_manifest_sha256': expected_run_manifest_sha256,
        'state_version': int(expected_state_version),
        'plan_hash': expected_plan_hash,
        'confirmation_phrase': confirmation_phrase,
    }
    actual = {key: preview[key] for key in expected}
    if any(str(actual[key]) != str(expected[key]) for key in expected):
        raise DevelopmentAutopilotError(
            'manual_reconcile_precondition_changed',
            'session/run/state/plan/确认短语与 dry-run 不一致', 409,
            {'expected': expected, 'actual': actual})
    key = str(idempotency_key or '').strip()
    if not _SAFE_ID.fullmatch(key):
        raise DevelopmentAutopilotError(
            'manual_reconcile_idempotency_invalid', '幂等键无效', 422)
    backup_root = os.path.realpath(backup_dir)
    os.makedirs(backup_root, exist_ok=True)
    backup_path = os.path.join(
        backup_root,
        f'{preview["session_id"]}.{preview["session_sha256"]}.pre-manual-reconcile.json')
    if os.path.exists(backup_path):
        raise DevelopmentAutopilotError(
            'manual_reconcile_backup_exists',
            '不可覆盖备份已存在；拒绝再次 apply', 409,
            {'backup_path': backup_path})
    with open(preview['session_path'], 'rb') as handle:
        original_bytes = handle.read()
    try:
        descriptor = os.open(
            backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as ex:
        raise DevelopmentAutopilotError(
            'manual_reconcile_backup_exists',
            '不可覆盖备份已存在；拒绝再次 apply', 409,
            {'backup_path': backup_path}) from ex
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(original_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(backup_root)
    if file_sha256(backup_path) != preview['session_sha256']:
        raise DevelopmentAutopilotError(
            'manual_reconcile_backup_verify_failed', '备份校验失败，拒绝 apply', 500)
    from . import whole_home_autopilot
    previous_root = whole_home_autopilot.SESSION_DIR
    previous_opt_in = os.environ.get('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT')
    try:
        whole_home_autopilot.SESSION_DIR = os.path.dirname(preview['session_path'])
        os.environ['FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT'] = 'true'
        result = reconcile_development_session(
            preview['session_id'],
            [row['document'] for row in _run_manifest(run_paths)[0]],
            apply=True, expected_state_version=expected_state_version,
            idempotency_key=key)
    finally:
        whole_home_autopilot.SESSION_DIR = previous_root
        if previous_opt_in is None:
            os.environ.pop('FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT', None)
        else:
            os.environ['FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT'] = previous_opt_in
    return {
        'schema_version': 1, 'mode': 'apply',
        'backup_path': backup_path,
        'backup_sha256': file_sha256(backup_path),
        'result': result,
        'provider_imported': False, 'provider_calls': 0,
    }


def apply_legacy_reconciliation(*, session_path: str, run_paths: list[str],
                                expected_session_sha256: str,
                                expected_run_manifest_sha256: str,
                                expected_state_version: int,
                                expected_plan_hash: str,
                                idempotency_key: str,
                                confirmation_phrase: str,
                                backup_dir: str) -> dict:
    """Apply only while no manual-safe API owns the authoritative data root."""
    with service_owner(MAIN_OUTPUT_DIR, timeout=0.1):
        return _apply_legacy_reconciliation_owned(
            session_path=session_path, run_paths=run_paths,
            expected_session_sha256=expected_session_sha256,
            expected_run_manifest_sha256=expected_run_manifest_sha256,
            expected_state_version=expected_state_version,
            expected_plan_hash=expected_plan_hash,
            idempotency_key=idempotency_key,
            confirmation_phrase=confirmation_phrase,
            backup_dir=backup_dir)


__all__ = [
    'DEVELOPMENT_PAID_ENV', 'EXTERNAL_REVIEW_ENV', 'MANUAL_PAID_ENV',
    'MANUAL_POLICY', 'MANUAL_QA_CALL_CAP', 'MANUAL_SAFE_ENV',
    'REFERENCE_ENV', 'WORKFLOW_ENV',
    'apply_legacy_reconciliation', 'capabilities',
    'claim_manual_run_commit', 'create_manual_run_preview',
    'feature_enabled', 'finish_manual_run_commit',
    'get_manual_preview_project_id', 'manual_paid_enabled',
    'manual_safe_enabled', 'normalize_manual_run_request',
    'preview_legacy_reconciliation', 'request_feature_for_path',
    'service_owner',
]
