# -*- coding: utf-8 -*-
"""Durable, development-only budget sessions for whole-home experiments.

This module deliberately does not know about Codex verdicts or product human
feedback.  It provides only a local orchestration identity and a conservative
logical-call budget shared by every run in one development session.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from typing import Optional

from .config import MAIN_OUTPUT_DIR
from .whole_home_dev_lock import data_root_lock, durable_atomic_json


SESSION_DIR = os.path.join(
    MAIN_OUTPUT_DIR, '_whole_home', 'development_autopilot', 'sessions')
ENV_NAME = 'FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT'
BUDGET_ACCOUNTING_SCOPE = 'logical_provider_dispatch'
DEFAULT_LIMITS = {
    'paid_batches': 6,
    'image_calls': 140,
    'qa_calls': 280,
}
_SESSION_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
_LOCK = threading.RLock()
_RESERVED_STATUSES = {'reserved', 'dispatched'}
_USED_STATUSES = {'done', 'failed', 'uncertain_after_restart'}
_TERMINAL_RUN_STATUSES = {'done', 'partial', 'failed', 'cancelled'}
_BOUND_RUN_STATUSES = {'queued', 'running', 'paid_running'}
SESSION_SCHEMA_VERSION = 2


class DevelopmentAutopilotError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409,
                 details: Optional[dict] = None):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = copy.deepcopy(details or {})

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'message': self.message,
            **copy.deepcopy(self.details),
        }


def development_autopilot_enabled(environ: Optional[dict[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(ENV_NAME) or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def require_development_autopilot_enabled() -> None:
    if not development_autopilot_enabled():
        # Hide development-only routes when the explicit opt-in is absent.
        raise DevelopmentAutopilotError(
            'development_autopilot_disabled',
            'development_autopilot 未启用',
            status_code=404,
        )


def _safe_session_id(session_id: str) -> str:
    value = str(session_id or '').strip()
    if not _SESSION_NAME.fullmatch(value):
        raise DevelopmentAutopilotError(
            'invalid_development_session_id', 'development_session_id 格式无效', 422)
    return value


def _session_path(session_id: str) -> str:
    return os.path.join(SESSION_DIR, f'{_safe_session_id(session_id)}.json')


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None
    except Exception as ex:
        raise DevelopmentAutopilotError(
            'development_session_corrupt', f'开发编排会话无法读取: {ex}', 500) from ex


def _atomic_json(path: str, payload: dict) -> None:
    durable_atomic_json(path, payload)


def _canonical_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _session_guard(session_id: str):
    safe_id = _safe_session_id(session_id)
    return data_root_lock(SESSION_DIR, f'autopilot-session-{safe_id}')


def _normalize_limits(value: Optional[dict]) -> dict:
    source = value or DEFAULT_LIMITS
    limits = {}
    for key, maximum in DEFAULT_LIMITS.items():
        try:
            current = int(source.get(key, maximum))
        except Exception as ex:
            raise DevelopmentAutopilotError(
                'invalid_development_budget', f'{key} 必须为整数', 422) from ex
        if current < 1 or current > maximum:
            raise DevelopmentAutopilotError(
                'invalid_development_budget',
                f'{key} 必须在 1..{maximum}，不能扩大已授权预算',
                422,
            )
        limits[key] = current
    return limits


def _batch(session: dict, batch_index: int) -> Optional[dict]:
    return next((
        row for row in session.get('batches') or []
        if int(row.get('batch_index') or 0) == int(batch_index)
    ), None)


def _migrate_session(value: dict) -> dict:
    """Return the v2 projection without silently rewriting a read-only load."""
    session = copy.deepcopy(value)
    previous = int(session.get('schema_version') or 1)
    session['schema_version'] = SESSION_SCHEMA_VERSION
    session.setdefault('state_version', 0)
    session.setdefault('batches', [])
    session.setdefault('runs', [])
    session.setdefault('reservations', [])
    session.setdefault('reconciliations', [])
    session.setdefault('migration_history', [])
    if previous < SESSION_SCHEMA_VERSION and not any(
            int(row.get('to_schema_version') or 0) == SESSION_SCHEMA_VERSION
            for row in session['migration_history'] if isinstance(row, dict)):
        session['migration_history'].append({
            'from_schema_version': previous,
            'to_schema_version': SESSION_SCHEMA_VERSION,
            'mode': 'read_compatible_write_on_next_mutation',
        })
    for row in session['batches']:
        row.setdefault('run_claim_id', '')
        legacy_token = str(row.pop('run_claim_token', '') or '')
        row.setdefault(
            'run_claim_token_hash',
            hashlib.sha256(legacy_token.encode('utf-8')).hexdigest()
            if legacy_token else '')
        row.setdefault('claim_generation', 0)
        row.setdefault('claim_revoked', False)
        row.setdefault('claim_revoked_at', None)
        row.setdefault('claim_revocation_reason', '')
        row.setdefault('budget_envelope', {})
        row.setdefault('claim_request_fingerprint', '')
        if str(row.get('status') or '') in _TERMINAL_RUN_STATUSES:
            row['claim_revoked'] = True
            row['run_claim_token_hash'] = ''
    for row in session['reservations']:
        row.setdefault('reconciled_at', None)
        row.setdefault('reconciliation_reason', '')
    return session


def _refresh_summary(session: dict, *, touch: bool = False) -> dict:
    reserved = {'image_calls': 0, 'qa_calls': 0}
    used = {'paid_batches': 0, 'image_calls': 0, 'qa_calls': 0}
    for row in session.get('batches') or []:
        if row.get('paid_started_at'):
            used['paid_batches'] += 1
    for row in session.get('reservations') or []:
        kind = str(row.get('kind') or '')
        key = 'image_calls' if kind == 'generation' else 'qa_calls' if kind == 'qa' else ''
        if not key:
            continue
        status = str(row.get('status') or '')
        if status in _RESERVED_STATUSES:
            reserved[key] += 1
        elif status in _USED_STATUSES:
            used[key] += 1
    limits = session['limits']
    remaining = {
        'paid_batches': max(0, int(limits['paid_batches']) - used['paid_batches']),
        'image_calls': max(
            0, int(limits['image_calls']) - used['image_calls'] - reserved['image_calls']),
        'qa_calls': max(
            0, int(limits['qa_calls']) - used['qa_calls'] - reserved['qa_calls']),
    }
    session.update(
        reserved=reserved,
        used=used,
        remaining=remaining,
        budget_accounting_scope=BUDGET_ACCOUNTING_SCOPE,
    )
    if touch:
        session['updated_at'] = time.time()
    return session


def _save_session(session: dict) -> dict:
    session['schema_version'] = SESSION_SCHEMA_VERSION
    session['state_version'] = int(session.get('state_version') or 0) + 1
    _refresh_summary(session, touch=True)
    _atomic_json(_session_path(str(session.get('session_id') or '')), session)
    return copy.deepcopy(session)


def _load_session(session_id: str) -> dict:
    value = _read_json(_session_path(session_id))
    if not value:
        raise DevelopmentAutopilotError(
            'development_session_not_found', '开发编排会话不存在', 404)
    value = _migrate_session(value)
    value['limits'] = _normalize_limits(value.get('limits'))
    return _refresh_summary(value)


def get_development_session(session_id: str) -> dict:
    require_development_autopilot_enabled()
    with _LOCK, _session_guard(session_id):
        return copy.deepcopy(_load_session(session_id))


def prepare_development_batch(*, session_id: str, project_id: str,
                              batch_index: int, parent_run_id: str,
                              limits: Optional[dict], idempotency_key: str,
                              request_fingerprint: str) -> dict:
    """Create or claim one idempotent, unpaid preflight batch."""
    require_development_autopilot_enabled()
    safe_id = _safe_session_id(session_id)
    key = str(idempotency_key or '').strip()
    fingerprint = str(request_fingerprint or '').strip()
    if not key or not fingerprint:
        raise DevelopmentAutopilotError(
            'development_idempotency_required',
            'development_autopilot 每批必须提供稳定的 idempotency_key', 422)
    normalized_limits = _normalize_limits(limits)
    if int(batch_index) < 1 or int(batch_index) > normalized_limits['paid_batches']:
        raise DevelopmentAutopilotError(
            'invalid_development_batch_index',
            f'batch_index 必须在 1..{normalized_limits["paid_batches"]}', 422)
    now = time.time()
    with _LOCK, _session_guard(safe_id):
        path = _session_path(safe_id)
        session = _read_json(path)
        if session is None:
            session = {
                'schema_version': SESSION_SCHEMA_VERSION,
                'state_version': 0,
                'session_id': safe_id,
                'project_id': str(project_id or ''),
                'status': 'active',
                'limits': normalized_limits,
                'budget_accounting_scope': BUDGET_ACCOUNTING_SCOPE,
                'reserved': {'image_calls': 0, 'qa_calls': 0},
                'used': {'paid_batches': 0, 'image_calls': 0, 'qa_calls': 0},
                'remaining': copy.deepcopy(normalized_limits),
                'runs': [],
                'batches': [],
                'reservations': [],
                'reconciliations': [],
                'migration_history': [],
                'stop_reason': '',
                'created_at': now,
                'updated_at': now,
            }
        else:
            session = _load_session(safe_id)
            if str(session.get('project_id') or '') != str(project_id or ''):
                raise DevelopmentAutopilotError(
                    'development_session_project_mismatch',
                    'development_session 已绑定其他整屋项目', 409)
            if session.get('limits') != normalized_limits:
                raise DevelopmentAutopilotError(
                    'development_session_limits_immutable',
                    'development_session 的预算上限创建后不可变更', 409,
                    {'limits': copy.deepcopy(session.get('limits') or {})},
                )
        existing = _batch(session, batch_index)
        if existing:
            if (existing.get('idempotency_key') != key
                    or existing.get('request_fingerprint') != fingerprint):
                raise DevelopmentAutopilotError(
                    'development_batch_conflict',
                    'batch_index 已被不同的开发请求占用', 409,
                )
            if str(existing.get('status') or '') in _TERMINAL_RUN_STATUSES | {
                    'preflight_failed'}:
                raise DevelopmentAutopilotError(
                    'development_batch_already_terminal',
                    '该开发批次已经终结；重试必须使用新的 batch_index 和幂等键', 409)
            return copy.deepcopy(session)
        if session.get('status') != 'active':
            raise DevelopmentAutopilotError(
                'development_session_stopped',
                f'开发编排会话已停止: {session.get("stop_reason") or session.get("status")}',
                409,
            )
        session['batches'].append({
            'batch_index': int(batch_index),
            'idempotency_key': key,
            'request_fingerprint': fingerprint,
            'parent_run_id': str(parent_run_id or ''),
            'run_id': '',
            'status': 'preflight_pending',
            'paid_started_at': None,
            'created_at': now,
            'updated_at': now,
            'error': '',
        })
        return _save_session(session)


def _normalize_budget_envelope(value: dict) -> dict:
    source = value if isinstance(value, dict) else {}
    normalized = {}
    for key in ('image_calls_min', 'image_calls_max', 'qa_calls_min', 'qa_calls_max'):
        try:
            normalized[key] = int(source.get(key) or 0)
        except Exception as ex:
            raise DevelopmentAutopilotError(
                'invalid_development_budget_envelope', f'{key} 必须为整数', 422) from ex
        if normalized[key] < 0:
            raise DevelopmentAutopilotError(
                'invalid_development_budget_envelope', f'{key} 不能为负数', 422)
    if (normalized['image_calls_min'] > normalized['image_calls_max']
            or normalized['qa_calls_min'] > normalized['qa_calls_max']):
        raise DevelopmentAutopilotError(
            'invalid_development_budget_envelope', '预算 envelope 的 min 不能大于 max', 422)
    normalized.update(
        paid_batches=1,
        result_count=max(0, int(source.get('result_count') or 0)),
        model_keys=sorted(set(str(value) for value in source.get('model_keys') or [])),
        candidates_per_camera=max(1, int(source.get('candidates_per_camera') or 1)),
        accounting_scope=BUDGET_ACCOUNTING_SCOPE,
    )
    normalized['envelope_hash'] = _canonical_hash(normalized)
    if set(normalized['model_keys']) != {'b2', 'pro'}:
        raise DevelopmentAutopilotError(
            'development_budget_envelope_model_coverage',
            'development_autopilot 必须完整覆盖 B2 + Pro', 422)
    return normalized


def _claim_token_hash(token: str) -> str:
    return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()


def _fence_claim(row: dict, reason: str, *, now: Optional[float] = None) -> None:
    timestamp = time.time() if now is None else float(now)
    if not row.get('claim_revoked'):
        row['claim_generation'] = max(0, int(row.get('claim_generation') or 0)) + 1
    row.update(
        claim_revoked=True,
        claim_revoked_at=row.get('claim_revoked_at') or timestamp,
        claim_revocation_reason=str(reason or 'claim_fenced')[:500],
        run_claim_token_hash='',
        updated_at=timestamp,
    )


def _assert_claim_proof(row: dict, *, run_claim_id: str,
                        claim_generation: int, claim_token: str,
                        request_fingerprint: str,
                        require_bound_run_id: str = '') -> None:
    if row.get('claim_revoked'):
        raise DevelopmentAutopilotError(
            'development_run_claim_fenced', '开发 run claim 已永久撤销', 409,
            {'claim_generation': int(row.get('claim_generation') or 0),
             'reason': str(row.get('claim_revocation_reason') or '')})
    expected_hash = str(row.get('run_claim_token_hash') or '')
    supplied_hash = _claim_token_hash(claim_token)
    valid = (
        bool(expected_hash)
        and hmac.compare_digest(expected_hash, supplied_hash)
        and str(row.get('run_claim_id') or '') == str(run_claim_id or '')
        and int(row.get('claim_generation') or 0) == int(claim_generation)
        and str(row.get('claim_request_fingerprint') or '')
        == str(request_fingerprint or '')
        and str(row.get('request_fingerprint') or '')
        == str(request_fingerprint or '')
    )
    if not valid:
        raise DevelopmentAutopilotError(
            'development_run_claim_mismatch', '开发 run claim fencing proof 不匹配', 409)
    if require_bound_run_id:
        bound = str(row.get('run_id') or '')
        if not bound or bound != str(require_bound_run_id or ''):
            raise DevelopmentAutopilotError(
                'development_batch_run_conflict', '调用不属于已绑定的开发 run', 409)


def claim_development_run(*, session_id: str, batch_index: int,
                          request_fingerprint: str, budget_envelope: dict) -> dict:
    """Atomically claim the only active dev run and its complete call envelope."""
    require_development_autopilot_enabled()
    fingerprint = str(request_fingerprint or '').strip()
    if not fingerprint:
        raise DevelopmentAutopilotError(
            'development_idempotency_required', 'run claim 需要请求指纹', 422)
    envelope = _normalize_budget_envelope(budget_envelope)
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        row = _batch(session, batch_index)
        if not row:
            raise DevelopmentAutopilotError(
                'development_batch_not_found', '开发批次不存在', 409)
        if row.get('claim_revoked'):
            raise DevelopmentAutopilotError(
                'development_run_claim_fenced', '该批次 claim 已永久撤销', 409)
        if fingerprint != str(row.get('request_fingerprint') or ''):
            raise DevelopmentAutopilotError(
                'development_run_claim_fingerprint_mismatch',
                'run claim 指纹与预建批次不一致', 409)
        existing_fingerprint = str(row.get('claim_request_fingerprint') or '')
        if row.get('run_claim_token_hash'):
            if (existing_fingerprint != fingerprint
                    or (row.get('budget_envelope') or {}).get('envelope_hash')
                    != envelope['envelope_hash']):
                raise DevelopmentAutopilotError(
                    'development_run_claim_conflict', '该批次已由不同 run envelope 占用', 409)
            return {
                'run_claim_id': row.get('run_claim_id'),
                # A bearer proof is returned exactly once.  Idempotent replay
                # exposes only its durable identity, never the live token.
                'claim_token': '',
                'claim_replayed': True,
                'claim_generation': int(row.get('claim_generation') or 0),
                'budget_envelope': copy.deepcopy(row.get('budget_envelope') or {}),
                'session': copy.deepcopy(session),
            }
        if session.get('status') != 'active':
            raise DevelopmentAutopilotError(
                'development_session_stopped',
                f'开发编排会话已停止: {session.get("stop_reason") or session.get("status")}', 409)
        if str(row.get('status') or '') != 'preflight_pending':
            raise DevelopmentAutopilotError(
                'development_batch_not_claimable',
                f'批次状态不可 claim: {row.get("status") or "unknown"}', 409)
        active = [
            item for item in session.get('batches') or []
            if item is not row and str(item.get('status') or '') in {
                'run_claimed', 'queued', 'running', 'paid_running'
            }
        ]
        if active:
            raise DevelopmentAutopilotError(
                'development_run_claim_busy', '已有未终结开发 run，必须先恢复对账', 409,
                {'active_batch_indexes': [item.get('batch_index') for item in active]})
        remaining = session.get('remaining') or {}
        shortages = {}
        for envelope_key, remaining_key in (
                ('image_calls_max', 'image_calls'), ('qa_calls_max', 'qa_calls')):
            required = int(envelope.get(envelope_key) or 0)
            available = int(remaining.get(remaining_key) or 0)
            if required > available:
                shortages[remaining_key] = {'required': required, 'available': available}
        if int(remaining.get('paid_batches') or 0) < 1:
            shortages['paid_batches'] = {'required': 1, 'available': 0}
        if shortages:
            raise DevelopmentAutopilotError(
                'development_budget_envelope_unavailable',
                '剩余预算不足以覆盖本轮最坏情况，未创建 run', 409,
                {'shortages': shortages, 'remaining': copy.deepcopy(remaining)},
            )
        now = time.time()
        token = uuid.uuid4().hex
        generation = max(0, int(row.get('claim_generation') or 0)) + 1
        row.update(
            run_claim_id=f'run_claim_{uuid.uuid4().hex}',
            run_claim_token_hash=_claim_token_hash(token),
            claim_generation=generation,
            claim_revoked=False,
            claim_revoked_at=None,
            claim_revocation_reason='',
            claim_request_fingerprint=fingerprint,
            budget_envelope=envelope,
            status='run_claimed',
            claimed_at=now,
            updated_at=now,
        )
        saved = _save_session(session)
        saved_row = _batch(saved, batch_index) or {}
        return {
            'run_claim_id': saved_row.get('run_claim_id'),
            'claim_token': token,
            'claim_replayed': False,
            'claim_generation': int(saved_row.get('claim_generation') or generation),
            'budget_envelope': copy.deepcopy(saved_row.get('budget_envelope') or {}),
            'session': saved,
        }


def bind_development_run(session_id: str, batch_index: int, run_id: str,
                         run_status: str = 'queued', *, run_claim_id: str,
                         claim_generation: int, claim_token: str,
                         request_fingerprint: str) -> dict:
    require_development_autopilot_enabled()
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        row = _batch(session, batch_index)
        if not row:
            raise DevelopmentAutopilotError(
                'development_batch_not_found', '开发批次不存在', 409)
        if not str(run_id or '').strip():
            raise DevelopmentAutopilotError(
                'development_run_id_required', '绑定开发批次需要非空 run_id', 422)
        if str(run_status or '') not in {'queued', 'running'}:
            raise DevelopmentAutopilotError(
                'development_run_status_invalid', 'bind 仅接受 queued/running', 422)
        if str(row.get('status') or '') not in {'run_claimed', 'queued', 'running'}:
            raise DevelopmentAutopilotError(
                'development_batch_not_bindable',
                f'批次状态不可 bind: {row.get("status") or "unknown"}', 409)
        _assert_claim_proof(
            row, run_claim_id=run_claim_id,
            claim_generation=claim_generation, claim_token=claim_token,
            request_fingerprint=request_fingerprint)
        existing = str(row.get('run_id') or '')
        if existing and existing != str(run_id or ''):
            raise DevelopmentAutopilotError(
                'development_batch_run_conflict', '开发批次已绑定其他 run', 409)
        row.update(
            run_id=str(run_id or ''),
            status=str(run_status or row.get('status') or 'queued'),
            updated_at=time.time(), error='',
        )
        if run_id and run_id not in session.setdefault('runs', []):
            session['runs'].append(run_id)
        return _save_session(session)


def mark_development_run_terminal(session_id: str, batch_index: int,
                                  run_id: str, status: str,
                                  error: str = '', *, run_claim_id: str,
                                  claim_generation: int, claim_token: str,
                                  request_fingerprint: str) -> dict:
    require_development_autopilot_enabled()
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        row = _batch(session, batch_index)
        if not row:
            raise DevelopmentAutopilotError(
                'development_batch_not_found', '开发批次不存在', 409)
        _assert_claim_proof(
            row, run_claim_id=run_claim_id,
            claim_generation=claim_generation, claim_token=claim_token,
            request_fingerprint=request_fingerprint,
            require_bound_run_id=run_id)
        existing = str(row.get('run_id') or '')
        if existing and existing != str(run_id or ''):
            raise DevelopmentAutopilotError(
                'development_batch_run_conflict', '开发批次已绑定其他 run', 409)
        if session.get('status') != 'cancelled':
            row.update(
                run_id=str(run_id or existing),
                status=str(status or 'failed'),
                error=str(error or '')[:2000],
                completed_at=time.time(),
                updated_at=time.time(),
            )
            _fence_claim(row, f'terminal:{status}')
        return _save_session(session)


def mark_development_preflight_failed(session_id: str, batch_index: int,
                                      error: str, *, run_claim_id: str,
                                      claim_generation: int, claim_token: str,
                                      request_fingerprint: str) -> dict:
    require_development_autopilot_enabled()
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        row = _batch(session, batch_index)
        if row and not row.get('run_id') and not row.get('paid_started_at'):
            _assert_claim_proof(
                row, run_claim_id=run_claim_id,
                claim_generation=claim_generation, claim_token=claim_token,
                request_fingerprint=request_fingerprint)
            row.update(status='preflight_failed', error=str(error or '')[:2000],
                       updated_at=time.time())
            _fence_claim(row, 'preflight_failed')
        return _save_session(session)


def reserve_logical_call(*, session_id: str, batch_index: int, run_id: str,
                         call_id: str, kind: str, phase: str,
                         result_id: str = '', attempt_id: str = '',
                         run_claim_id: str, claim_generation: int,
                         claim_token: str, request_fingerprint: str) -> dict:
    """Atomically consume capacity before a logical image/QA provider dispatch."""
    require_development_autopilot_enabled()
    if kind not in ('generation', 'qa'):
        raise DevelopmentAutopilotError(
            'invalid_development_call_kind', '预算只接受 generation 或 qa', 422)
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        row = _batch(session, batch_index)
        if not row:
            raise DevelopmentAutopilotError(
                'development_batch_not_found', '开发批次不存在', 409)
        _assert_claim_proof(
            row, run_claim_id=run_claim_id,
            claim_generation=claim_generation, claim_token=claim_token,
            request_fingerprint=request_fingerprint,
            require_bound_run_id=run_id)
        if str(row.get('status') or '') not in _BOUND_RUN_STATUSES:
            raise DevelopmentAutopilotError(
                'development_batch_not_reservable',
                f'批次状态不可预占调用: {row.get("status") or "unknown"}', 409)
        if session.get('status') != 'active':
            raise DevelopmentAutopilotError(
                'development_session_stopped',
                f'开发编排会话已停止: {session.get("stop_reason") or session.get("status")}',
                409,
            )
        existing = next((
            item for item in session.get('reservations') or []
            if str(item.get('call_id') or '') == str(call_id or '')
        ), None)
        if existing:
            return copy.deepcopy(existing)
        call_key = 'image_calls' if kind == 'generation' else 'qa_calls'
        envelope = row.get('budget_envelope') or {}
        envelope_key = 'image_calls_max' if kind == 'generation' else 'qa_calls_max'
        if envelope and envelope_key in envelope:
            claimed = sum(
                1 for item in session.get('reservations') or []
                if int(item.get('batch_index') or 0) == int(batch_index)
                and str(item.get('kind') or '') == kind
                and str(item.get('status') or '') != 'cancelled_before_dispatch'
            )
            if claimed >= int(envelope.get(envelope_key) or 0):
                raise DevelopmentAutopilotError(
                    'development_budget_envelope_exhausted',
                    f'本 run 已达到 {envelope_key} envelope，未 dispatch provider', 409,
                    {'kind': kind, 'budget_envelope': copy.deepcopy(envelope)},
                )
        remaining = session.get('remaining') or {}
        if int(remaining.get(call_key) or 0) <= 0:
            session.update(
                status='budget_exhausted',
                stop_reason=f'development_budget_exhausted:{call_key}',
            )
            _save_session(session)
            raise DevelopmentAutopilotError(
                'development_budget_exhausted',
                f'{call_key} 逻辑调用预算已用尽，未 dispatch provider', 409,
                {'kind': kind, 'remaining': copy.deepcopy(remaining)},
            )
        if not row.get('paid_started_at') and int(remaining.get('paid_batches') or 0) <= 0:
            session.update(
                status='budget_exhausted',
                stop_reason='development_budget_exhausted:paid_batches',
            )
            _save_session(session)
            raise DevelopmentAutopilotError(
                'development_budget_exhausted',
                'paid_batches 预算已用尽，未 dispatch provider', 409,
                {'kind': kind, 'remaining': copy.deepcopy(remaining)},
            )
        now = time.time()
        if not row.get('paid_started_at'):
            row['paid_started_at'] = now
        row.update(status='paid_running', run_id=str(run_id or row.get('run_id') or ''),
                   updated_at=now)
        if run_id and run_id not in session.setdefault('runs', []):
            session['runs'].append(run_id)
        reservation = {
            'reservation_id': f'reservation_{uuid.uuid4().hex}',
            'call_id': str(call_id or ''),
            'kind': kind,
            'phase': str(phase or ''),
            'batch_index': int(batch_index),
            'run_id': str(run_id or ''),
            'result_id': str(result_id or ''),
            'attempt_id': str(attempt_id or ''),
            'run_claim_id': str(run_claim_id or ''),
            'claim_generation': int(claim_generation),
            'request_fingerprint': str(request_fingerprint or ''),
            'status': 'reserved',
            'reserved_at': now,
            'dispatched_at': None,
            'finished_at': None,
            'error': '',
        }
        session.setdefault('reservations', []).append(reservation)
        _save_session(session)
        return copy.deepcopy(reservation)


def mark_logical_call_dispatched(session_id: str, reservation_id: str, *,
                                 run_claim_id: str, claim_generation: int,
                                 claim_token: str,
                                 request_fingerprint: str) -> dict:
    require_development_autopilot_enabled()
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        reservation = next((
            row for row in session.get('reservations') or []
            if row.get('reservation_id') == reservation_id
        ), None)
        if not reservation:
            raise DevelopmentAutopilotError(
                'development_reservation_not_found', '逻辑调用预算预占不存在', 409)
        batch = _batch(session, int(reservation.get('batch_index') or 0))
        if not batch:
            raise DevelopmentAutopilotError(
                'development_batch_not_found', '开发批次不存在', 409)
        _assert_claim_proof(
            batch, run_claim_id=run_claim_id,
            claim_generation=claim_generation, claim_token=claim_token,
            request_fingerprint=request_fingerprint,
            require_bound_run_id=str(reservation.get('run_id') or ''))
        if reservation.get('status') == 'cancelled_before_dispatch':
            raise DevelopmentAutopilotError(
                'development_session_cancelled', '开发编排已取消，未 dispatch provider', 409)
        if session.get('status') == 'cancelled' and reservation.get('status') == 'reserved':
            reservation.update(
                status='cancelled_before_dispatch', finished_at=time.time(),
                error='development_session_cancelled',
            )
            _save_session(session)
            raise DevelopmentAutopilotError(
                'development_session_cancelled', '开发编排已取消，未 dispatch provider', 409)
        if reservation.get('status') == 'reserved':
            reservation.update(status='dispatched', dispatched_at=time.time())
            _save_session(session)
        return copy.deepcopy(reservation)


def finish_logical_call(session_id: str, reservation_id: str, *,
                        success: bool, error: str = '', run_claim_id: str,
                        claim_generation: int, claim_token: str,
                        request_fingerprint: str) -> dict:
    require_development_autopilot_enabled()
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        reservation = next((
            row for row in session.get('reservations') or []
            if row.get('reservation_id') == reservation_id
        ), None)
        if not reservation:
            raise DevelopmentAutopilotError(
                'development_reservation_not_found', '逻辑调用预算预占不存在', 409)
        batch = _batch(session, int(reservation.get('batch_index') or 0))
        if not batch:
            raise DevelopmentAutopilotError(
                'development_batch_not_found', '开发批次不存在', 409)
        _assert_claim_proof(
            batch, run_claim_id=run_claim_id,
            claim_generation=claim_generation, claim_token=claim_token,
            request_fingerprint=request_fingerprint,
            require_bound_run_id=str(reservation.get('run_id') or ''))
        if reservation.get('status') in _USED_STATUSES:
            return copy.deepcopy(reservation)
        if reservation.get('status') == 'cancelled_before_dispatch':
            return copy.deepcopy(reservation)
        reservation.update(
            status='done' if success else 'failed',
            finished_at=time.time(),
            error=str(error or '')[:2000],
        )
        if batch:
            batch.update(status='paid_running', updated_at=time.time())
        _save_session(session)
        return copy.deepcopy(reservation)


def cancel_development_session(session_id: str, reason: str = 'controller_cancelled') -> dict:
    require_development_autopilot_enabled()
    with _LOCK, _session_guard(session_id):
        session = _load_session(session_id)
        if session.get('status') != 'cancelled':
            now = time.time()
            session.update(status='cancelled', stop_reason=str(reason or 'controller_cancelled')[:500])
            for reservation in session.get('reservations') or []:
                if reservation.get('status') == 'reserved':
                    reservation.update(
                        status='cancelled_before_dispatch', finished_at=now,
                        error='development_session_cancelled',
                    )
            for row in session.get('batches') or []:
                if row.get('status') in ('preflight_pending', 'queued', 'paid_running'):
                    row.update(status='cancelled', updated_at=now)
                    _fence_claim(row, 'session_cancelled', now=now)
        return _save_session(session)


def _run_map(run_records) -> dict[str, dict]:
    values = run_records.values() if isinstance(run_records, dict) else (run_records or [])
    return {
        str(row.get('run_id') or ''): row
        for row in values if isinstance(row, dict) and row.get('run_id')
    }


def _ledger_row(run: dict, reservation: dict) -> Optional[dict]:
    reservation_id = str(reservation.get('reservation_id') or '')
    call_id = str(reservation.get('call_id') or '')
    return next((
        row for row in run.get('call_ledger') or []
        if ((reservation_id and str(row.get('reservation_id') or '') == reservation_id)
            or (call_id and str(row.get('call_id') or '') == call_id))
    ), None)


def reconcile_development_session(session_id: str, run_records, *,
                                  apply: bool = False,
                                  expected_state_version: Optional[int] = None,
                                  idempotency_key: str = '') -> dict:
    """Preview or apply conservative restart reconciliation.

    Manual Release v1 treats every nonterminal legacy reservation as uncertain.
    Older writers did not persist a sufficiently strong dispatch fence, so even
    a row labelled ``reserved`` cannot safely be refunded.  The conservative
    projection consumes it once and pauses the session for explicit operator
    review; no startup path applies this plan automatically.
    """
    require_development_autopilot_enabled()
    key = str(idempotency_key or '').strip()
    if apply and not key:
        raise DevelopmentAutopilotError(
            'development_reconciliation_idempotency_required',
            'apply reconciliation 需要 idempotency_key', 422)
    with _LOCK, _session_guard(session_id):
        original = _load_session(session_id)
        current_version = int(original.get('state_version') or 0)
        replay = next((
            row for row in original.get('reconciliations') or []
            if key and row.get('idempotency_key') == key
        ), None)
        if replay:
            return {
                'schema_version': 1, 'session_id': session_id,
                'mode': 'apply', 'applied': False, 'idempotent_noop': True,
                'idempotent_replay': True,
                'expected_state_version': replay.get('expected_state_version'),
                'result_state_version': current_version,
                'before_hash': replay.get('before_hash') or '',
                'projected_hash': _canonical_hash(original),
                'plan_hash': replay.get('plan_hash') or '',
                'actions': copy.deepcopy(replay.get('actions') or []),
                'summary': copy.deepcopy(replay.get('summary') or {}),
                'projected_session': copy.deepcopy(original),
            }
        if expected_state_version is not None and int(expected_state_version) != current_version:
            raise DevelopmentAutopilotError(
                'development_session_version_conflict', '会话已变化，请重新 dry-run', 409,
                {'expected_state_version': int(expected_state_version),
                 'actual_state_version': current_version},
            )
        candidate = copy.deepcopy(original)
        runs = _run_map(run_records)
        actions = []
        now = time.time()
        for reservation in candidate.get('reservations') or []:
            old_status = str(reservation.get('status') or '')
            if old_status in _USED_STATUSES or old_status == 'cancelled_before_dispatch':
                continue
            run = runs.get(str(reservation.get('run_id') or '')) or {}
            ledger = _ledger_row(run, reservation) if run else None
            ledger_budget = str((ledger or {}).get('budget_status') or '')
            ledger_status = str((ledger or {}).get('status') or '')
            if ledger_budget == 'done' or ledger_status == 'done':
                new_status, reason = 'done', 'terminal_run_ledger_success'
            elif ledger_budget == 'failed' or ledger_status == 'failed':
                new_status, reason = 'failed', 'terminal_run_ledger_failure'
            elif old_status in {'reserved', 'dispatched'}:
                new_status, reason = (
                    'uncertain_after_restart',
                    'legacy_dispatch_fence_insufficient_no_refund')
            elif old_status == 'uncertain_after_restart':
                continue
            else:
                continue
            if new_status == old_status:
                continue
            reservation.update(
                status=new_status,
                reconciled_at=now,
                reconciliation_reason=reason,
            )
            if new_status in _USED_STATUSES | {'cancelled_before_dispatch'}:
                reservation['finished_at'] = reservation.get('finished_at') or now
            actions.append({
                'type': 'reservation_status',
                'reservation_id': reservation.get('reservation_id'),
                'from': old_status,
                'to': new_status,
                'reason': reason,
            })
        for batch in candidate.get('batches') or []:
            run_id = str(batch.get('run_id') or '')
            run = runs.get(run_id) or {}
            run_status = str(run.get('status') or '')
            old_status = str(batch.get('status') or '')
            if run_status in _TERMINAL_RUN_STATUSES and old_status != run_status:
                batch.update(
                    status=run_status,
                    completed_at=batch.get('completed_at') or now,
                    updated_at=now,
                    error=str(run.get('error') or batch.get('error') or '')[:2000],
                )
                _fence_claim(batch, f'reconciliation_terminal:{run_status}', now=now)
                actions.append({
                    'type': 'batch_terminal_sync', 'batch_index': batch.get('batch_index'),
                    'run_id': run_id, 'from': old_status, 'to': run_status,
                })
            elif old_status == 'run_claimed' and not run_id:
                batch.update(
                    status='preflight_failed', updated_at=now,
                    error='recovery: run claim was never bound',
                )
                _fence_claim(batch, 'reconciliation_unbound_claim', now=now)
                actions.append({
                    'type': 'release_unbound_run_claim',
                    'batch_index': batch.get('batch_index'),
                    'from': old_status, 'to': 'preflight_failed',
                })
        if candidate.get('status') != 'paused':
            actions.append({
                'type': 'session_pause',
                'from': candidate.get('status') or '', 'to': 'paused',
                'reason': 'manual_reconciliation_requires_operator_review',
            })
            candidate.update(
                status='paused',
                stop_reason='manual_reconciliation_requires_operator_review')
        _refresh_summary(candidate)
        before_hash = _canonical_hash(original)
        projected_hash = _canonical_hash(candidate)
        plan_core = {
            'session_id': session_id,
            'before_hash': before_hash,
            'expected_state_version': current_version,
            'actions': actions,
            # Wall-clock reconciliation timestamps are evidence written by an
            # apply, not operator intent.  Keep the confirmation hash stable
            # across a dry-run and a later apply while binding the immutable
            # source hash, CAS version, exact actions and resulting budget.
            'projected_budget': {
                'status': candidate.get('status') or '',
                'reserved': copy.deepcopy(candidate.get('reserved') or {}),
                'used': copy.deepcopy(candidate.get('used') or {}),
                'remaining': copy.deepcopy(candidate.get('remaining') or {}),
            },
        }
        plan_hash = _canonical_hash(plan_core)
        applied = False
        summary = {
            'released_before_dispatch': sum(
                row.get('to') == 'cancelled_before_dispatch' for row in actions),
            'uncertain_after_restart': sum(
                row.get('to') == 'uncertain_after_restart' for row in actions),
            'terminal_reservations': sum(
                row.get('to') in _USED_STATUSES for row in actions),
            'terminal_batches': sum(row.get('type') == 'batch_terminal_sync' for row in actions),
        }
        if apply:
            candidate.setdefault('reconciliations', []).append({
                'reconciliation_id': f'reconcile_{uuid.uuid4().hex}',
                'idempotency_key': key,
                'plan_hash': plan_hash,
                'before_hash': before_hash,
                'expected_state_version': current_version,
                'actions': copy.deepcopy(actions),
                'summary': copy.deepcopy(summary),
                'action_count': len(actions),
                'applied_at': now,
            })
            candidate = _save_session(candidate)
            projected_hash = _canonical_hash(candidate)
            applied = True
        return {
            'schema_version': 1,
            'session_id': session_id,
            'mode': 'apply' if apply else 'dry_run',
            'applied': applied,
            'idempotent_noop': not actions,
            'expected_state_version': current_version,
            'result_state_version': int(candidate.get('state_version') or current_version),
            'before_hash': before_hash,
            'projected_hash': projected_hash,
            'plan_hash': plan_hash,
            'actions': actions,
            'summary': summary,
            'projected_session': copy.deepcopy(candidate),
        }


def list_development_session_ids() -> list[str]:
    require_development_autopilot_enabled()
    try:
        names = os.listdir(SESSION_DIR)
    except FileNotFoundError:
        return []
    return sorted(
        name[:-5] for name in names
        if name.endswith('.json') and _SESSION_NAME.fullmatch(name[:-5]))


__all__ = [
    'BUDGET_ACCOUNTING_SCOPE',
    'DEFAULT_LIMITS',
    'DevelopmentAutopilotError',
    'bind_development_run',
    'cancel_development_session',
    'claim_development_run',
    'development_autopilot_enabled',
    'finish_logical_call',
    'get_development_session',
    'list_development_session_ids',
    'mark_development_preflight_failed',
    'mark_development_run_terminal',
    'mark_logical_call_dispatched',
    'prepare_development_batch',
    'reconcile_development_session',
    'require_development_autopilot_enabled',
    'reserve_logical_call',
]
