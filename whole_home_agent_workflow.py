# -*- coding: utf-8 -*-
"""Local, append-audited control plane for multi-agent development work."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import unicodedata
import uuid
from typing import Optional

from .config import MAIN_OUTPUT_DIR
from .whole_home_autopilot import DevelopmentAutopilotError, require_development_autopilot_enabled
from .whole_home_dev_lock import (
    data_root_lock, durable_atomic_bytes, durable_atomic_json)


WORKFLOW_ROOT = os.path.join(
    os.path.dirname(MAIN_OUTPUT_DIR), '_dev_audits', 'agent_workflows')
_SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
_TERMINAL = {'pass', 'fail', 'blocked', 'cancelled'}
_WRITER_MODES = {'write', 'single_writer'}
_FAILURE_TRIAGE_THRESHOLD = 3
FAILURE_SIGNATURE_VERSION = 'failure-signature-v1'
SOURCE_SNAPSHOT_SUFFIX = '.source_snapshot'
DEFAULT_TEST_COLLECTION_CONTRACT = {
    'pytest_roots': ['tests'],
    'immutable_evidence_roots': ['data/_dev_audits'],
    'root_discovery_allowed': False,
    'source_snapshot_suffix': SOURCE_SNAPSHOT_SUFFIX,
    'reason': (
        'Immutable audit evidence may contain historical test_*.py paths; '
        'test agents must collect only the live tests root.'),
}


def _safe(value: str, label: str) -> str:
    result = str(value or '').strip()
    if not _SAFE_ID.fullmatch(result):
        raise DevelopmentAutopilotError(
            'invalid_agent_workflow_id', f'{label} 格式无效', 422)
    return result


def _folder(workflow_id: str) -> str:
    return os.path.join(WORKFLOW_ROOT, _safe(workflow_id, 'workflow_id'))


def _projection_path(workflow_id: str) -> str:
    return os.path.join(_folder(workflow_id), 'workflow.json')


def _read(path: str) -> Optional[dict]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None


def _atomic(path: str, payload: dict) -> None:
    durable_atomic_json(path, payload)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


_VOLATILE_FAILURE_TEXT = (
    re.compile(r'(?i)[a-z]:[\\/](?:[^\s,;]+)'),
    re.compile(r'(?i)/(?:tmp|var/tmp|private/tmp)/(?:[^\s,;]+)'),
    re.compile(r'(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b'),
    re.compile(r'(?i)\b(?:run|task|attempt)[_:-](?:[0-9a-f]{8,}|[a-z0-9_-]*\d[a-z0-9_-]{5,})\b'),
    re.compile(r'(?i)\bpid[_:= -]*\d+\b'),
    re.compile(r'(?i)\b(?:line|ln)[_:# -]*\d+\b'),
    re.compile(r'(?i)(?:行号|第)\s*\d+\s*(?:行)?'),
    re.compile(r'(?i)\b\d{4}-\d{2}-\d{2}[t _]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?\b'),
    re.compile(r'(?i)\b(?:1[6-9]\d{8}|2\d{9}|\d{13})\b'),
    re.compile(r'(?i)\b\d+(?:\.\d+)?(?:ms|msec|s|sec|seconds|毫秒|秒|分钟)\b'),
    re.compile(r'(?i)\b[0-9a-f]{16,}\b'),
)


def _normalize_failure_identifier(value: str) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).strip().lower()
    for pattern in _VOLATILE_FAILURE_TEXT:
        text = pattern.sub(' ', text)
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '_', text).strip('_')


def _normalize_report_sha256(value: str) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).strip().lower()
    if text.startswith('sha256:'):
        text = text[7:]
    return re.sub(r'\s+', '', text)


def normalize_failure_facts(value: dict) -> dict:
    """Return only stable causal identifiers used by failure-signature v1."""
    source = value if isinstance(value, dict) else {}
    dimension = _normalize_failure_identifier(
        str(source.get('dimension') or source.get('failure_dimension') or ''))
    pipeline_stage = _normalize_failure_identifier(source.get('pipeline_stage') or '')
    failure_code = _normalize_failure_identifier(source.get('failure_code') or '')
    causal_component = _normalize_failure_identifier(source.get('causal_component') or '')
    subjects = sorted(set(filter(None, (
        _normalize_failure_identifier(item)
        for item in source.get('affected_subjects') or []
    ))))
    facts = {
        'signature_version': FAILURE_SIGNATURE_VERSION,
        'dimension': dimension,
        'pipeline_stage': pipeline_stage,
        'failure_code': failure_code,
        'affected_subjects': subjects,
        'causal_component': causal_component,
    }
    missing = [key for key in (
        'dimension', 'pipeline_stage', 'failure_code',
        'affected_subjects', 'causal_component') if not facts[key]]
    if missing:
        raise DevelopmentAutopilotError(
            'agent_failure_signature_fields_required',
            'fail 结果缺少稳定根因签名字段', 422,
            {'missing_fields': missing,
             'signature_version': FAILURE_SIGNATURE_VERSION})
    return facts


def canonical_failure_signature(value: dict) -> str:
    """Hash only canonical causal facts, never task/report/prose identifiers."""
    facts = normalize_failure_facts(value)
    return _hash(facts)


def _event(workflow: dict, event_type: str, payload: dict) -> None:
    sequence = int(workflow.get('event_sequence') or 0) + 1
    event_id = f'event_{sequence:08d}_{uuid.uuid4().hex[:12]}'
    event_at = time.time()
    workflow['event_sequence'] = sequence
    workflow['last_event_id'] = event_id
    event = {
        'schema_version': 1,
        'workflow_id': workflow['workflow_id'],
        'sequence': sequence,
        'event_id': event_id,
        'type': event_type,
        'at': event_at,
        'payload': copy.deepcopy(payload),
        'previous_projection_hash': workflow.get('projection_hash') or '',
        # This snapshot makes the append-only log sufficient to rebuild a
        # missing/corrupt projection without guessing task or lease state.
        'projection_after_event': copy.deepcopy(workflow),
    }
    event['event_hash'] = _hash(event)
    folder = os.path.join(_folder(workflow['workflow_id']), 'events')
    event_path = os.path.join(folder, f'{sequence:08d}_{event_type}.json')
    if os.path.exists(event_path):
        raise DevelopmentAutopilotError(
            'agent_workflow_event_conflict', 'append-only event sequence 已存在', 409)
    _atomic(event_path, event)


def _save(workflow: dict) -> dict:
    workflow['version'] = int(workflow.get('version') or 0) + 1
    workflow['updated_at'] = time.time()
    projection = copy.deepcopy(workflow)
    projection.pop('projection_hash', None)
    workflow['projection_hash'] = _hash(projection)
    _atomic(_projection_path(workflow['workflow_id']), workflow)
    return copy.deepcopy(workflow)


def _project_defaults(workflow: dict) -> dict:
    workflow.setdefault(
        'test_collection_contract', copy.deepcopy(DEFAULT_TEST_COLLECTION_CONTRACT))
    workflow.setdefault('source_snapshot_archives', [])
    workflow.setdefault('next_route', {})
    # Never backfill these counters from legacy task-local/report-based
    # signatures.  Old records remain readable audit evidence but cannot
    # falsely trip failure-signature-v1.
    workflow.setdefault('failure_signature_version', FAILURE_SIGNATURE_VERSION)
    workflow.setdefault('failure_signature_counts', {})
    workflow.setdefault('failure_history', [])
    for task in workflow.get('tasks') or []:
        task.setdefault('consecutive_failure_signature', '')
        task.setdefault('consecutive_failure_count', 0)
        task.setdefault('failure_history', [])
        lease = task.get('lease') if isinstance(task.get('lease'), dict) else {}
        legacy_token = str(lease.pop('lease_token', '') or '')
        if legacy_token:
            # Plaintext leases from schema v1 are rotated fail-closed on read.
            # No durable/read projection ever returns the old bearer token.
            lease.update(
                lease_token_salt=secrets.token_hex(16),
                lease_token_hash=_hash({'revoked_legacy_token': secrets.token_hex(32)}),
                expires_at=0,
                legacy_plaintext_token_revoked=True,
            )
        task['lease'] = lease
    return workflow


def _load(workflow_id: str) -> dict:
    workflow = _read(_projection_path(workflow_id))
    if not workflow:
        raise DevelopmentAutopilotError(
            'agent_workflow_not_found', '多 Agent 工作流不存在', 404)
    return _project_defaults(workflow)


def _guard(workflow_id: str):
    return data_root_lock(_folder(workflow_id), 'workflow-projection')


def _expect_version(workflow: dict, expected_version: int) -> None:
    actual = int(workflow.get('version') or 0)
    if int(expected_version) != actual:
        raise DevelopmentAutopilotError(
            'agent_workflow_version_conflict', '工作流版本已变化', 409,
            {'expected_version': int(expected_version), 'actual_version': actual})


def create_workflow(*, workflow_id: str, title: str, source_digest: str,
                    tasks: list[dict], idempotency_key: str) -> dict:
    require_development_autopilot_enabled()
    workflow_id = _safe(workflow_id, 'workflow_id')
    key = _safe(idempotency_key, 'idempotency_key')
    normalized_tasks = []
    seen = set()
    for raw in tasks:
        task_id = _safe(str(raw.get('task_id') or ''), 'task_id')
        if task_id in seen:
            raise DevelopmentAutopilotError(
                'duplicate_agent_task_id', f'重复 task_id: {task_id}', 422)
        seen.add(task_id)
        mode = str(raw.get('mode') or 'read_only')
        if mode not in {'read_only', 'write', 'single_writer'}:
            raise DevelopmentAutopilotError(
                'invalid_agent_task_mode', f'{task_id}: mode 无效', 422)
        normalized_tasks.append({
            'task_id': task_id,
            'role': str(raw.get('role') or '')[:100],
            'mode': mode,
            'status': 'pending',
            'depends_on': list(dict.fromkeys(
                str(value) for value in raw.get('depends_on') or [])),
            'input_artifact_hashes': copy.deepcopy(raw.get('input_artifact_hashes') or {}),
            'allowed_paths': list(dict.fromkeys(
                str(value) for value in raw.get('allowed_paths') or [])),
            'lease': {}, 'result': {}, 'claim_history': [],
        })
    now = time.time()
    with _guard(workflow_id):
        existing = _read(_projection_path(workflow_id))
        if existing:
            if existing.get('create_idempotency_key') != key:
                raise DevelopmentAutopilotError(
                    'agent_workflow_conflict', 'workflow_id 已被不同请求占用', 409)
            return existing
        workflow = {
            'schema_version': 1, 'workflow_id': workflow_id,
            'title': str(title or '')[:300], 'source_digest': str(source_digest or '')[:128],
            'status': 'created', 'version': 0, 'event_sequence': 0,
            'last_event_id': '', 'projection_hash': '',
            'create_idempotency_key': key, 'tasks': normalized_tasks,
            'test_collection_contract': copy.deepcopy(
                DEFAULT_TEST_COLLECTION_CONTRACT),
            'source_snapshot_archives': [],
            'stop_reason': '', 'created_at': now, 'updated_at': now,
        }
        _event(workflow, 'workflow_created', {
            'title': workflow['title'], 'source_digest': workflow['source_digest'],
            'task_ids': [row['task_id'] for row in normalized_tasks],
        })
        return _save(workflow)


def archive_source_snapshot(*, workflow_id: str, source_root: str,
                            relative_paths: list[str], expected_version: int,
                            idempotency_key: str) -> dict:
    """Archive sources without a collectable ``.py`` suffix.

    The manifest retains each logical path and hash while the physical filename
    ends in ``.source_snapshot``.  This prevents future immutable development
    evidence from becoming an accidental pytest package.
    """
    require_development_autopilot_enabled()
    key = _safe(idempotency_key, 'idempotency_key')
    root = os.path.realpath(str(source_root or ''))
    if not os.path.isdir(root):
        raise DevelopmentAutopilotError(
            'agent_snapshot_source_root_missing', 'source_root 不存在', 422)
    logical_paths = []
    for raw_path in relative_paths:
        raw = str(raw_path or '').replace('\\', '/')
        if not raw.strip():
            continue
        drive, _ = os.path.splitdrive(raw)
        parts = raw.split('/')
        if (drive or raw.startswith('/') or raw.startswith('//')
                or any(part in {'', '.', '..'} for part in parts)):
            raise DevelopmentAutopilotError(
                'agent_snapshot_source_invalid',
                f'源码路径必须是规范相对路径: {raw}', 422)
        logical_paths.append('/'.join(parts))
    logical_paths = list(dict.fromkeys(logical_paths))
    if not logical_paths:
        raise DevelopmentAutopilotError(
            'agent_snapshot_paths_required', '至少需要一个源码路径', 422)
    workflow_id = _safe(workflow_id, 'workflow_id')
    with _guard(workflow_id):
        workflow = _load(workflow_id)
        existing = next((
            row for row in workflow.get('source_snapshot_archives') or []
            if row.get('idempotency_key') == key
        ), None)
        if existing:
            return copy.deepcopy(workflow)
        _expect_version(workflow, expected_version)
        archive_id = 'source_snapshot_' + hashlib.sha256(
            key.encode('utf-8')).hexdigest()[:20]
        archive_root = os.path.join(
            _folder(workflow_id), 'source_snapshots', archive_id)
        records = []
        for logical_path in sorted(logical_paths):
            source = os.path.realpath(os.path.join(root, logical_path.replace('/', os.sep)))
            try:
                inside = os.path.commonpath([root, source]) == root
            except ValueError:
                inside = False
            if not inside or not os.path.isfile(source):
                raise DevelopmentAutopilotError(
                    'agent_snapshot_source_invalid',
                    f'源码路径越界或不存在: {logical_path}', 422)
            with open(source, 'rb') as handle:
                payload = handle.read()
            source_hash = hashlib.sha256(payload).hexdigest()
            destination = os.path.join(
                archive_root, *logical_path.split('/')) + SOURCE_SNAPSHOT_SUFFIX
            destination = os.path.realpath(destination)
            try:
                destination_inside = (
                    os.path.commonpath([os.path.realpath(archive_root), destination])
                    == os.path.realpath(archive_root))
            except ValueError:
                destination_inside = False
            if not destination_inside:
                raise DevelopmentAutopilotError(
                    'agent_snapshot_destination_invalid',
                    f'归档目标路径越界: {logical_path}', 422)
            if os.path.exists(destination):
                with open(destination, 'rb') as handle:
                    existing_payload = handle.read()
                if hashlib.sha256(existing_payload).hexdigest() != source_hash:
                    raise DevelopmentAutopilotError(
                        'agent_snapshot_archive_conflict',
                        f'归档路径已存在不同内容: {logical_path}', 409)
            else:
                durable_atomic_bytes(destination, payload)
            records.append({
                'logical_path': logical_path,
                'snapshot_path': os.path.relpath(
                    destination, _folder(workflow_id)).replace('\\', '/'),
                'sha256': source_hash, 'length': len(payload),
            })
        manifest = {
            'schema_version': 1, 'archive_id': archive_id,
            'idempotency_key': key, 'created_at': time.time(),
            'source_root_hash': hashlib.sha256(
                os.path.normcase(root).encode('utf-8')).hexdigest(),
            'records': records,
            'pytest_collectable': False,
            'physical_suffix': SOURCE_SNAPSHOT_SUFFIX,
        }
        manifest['manifest_sha256'] = _hash(manifest)
        manifest_path = os.path.join(archive_root, 'archive_manifest.json')
        if not os.path.exists(manifest_path):
            _atomic(manifest_path, manifest)
        workflow.setdefault('source_snapshot_archives', []).append({
            'archive_id': archive_id,
            'idempotency_key': key,
            'manifest_path': os.path.relpath(
                manifest_path, _folder(workflow_id)).replace('\\', '/'),
            'manifest_sha256': manifest['manifest_sha256'],
            'record_count': len(records),
            'created_at': manifest['created_at'],
        })
        _event(workflow, 'source_snapshot_archived', {
            'archive_id': archive_id,
            'manifest_sha256': manifest['manifest_sha256'],
            'record_count': len(records),
            'physical_suffix': SOURCE_SNAPSHOT_SUFFIX,
        })
        return _save(workflow)


def get_workflow(workflow_id: str) -> dict:
    require_development_autopilot_enabled()
    with _guard(workflow_id):
        return copy.deepcopy(_load(workflow_id))


def _lease_digest(token: str, salt: str) -> str:
    return hashlib.sha256(
        (str(salt or '') + ':' + str(token or '')).encode('utf-8')).hexdigest()


def _lease_matches(lease: dict, token: str) -> bool:
    expected = str(lease.get('lease_token_hash') or '')
    supplied = _lease_digest(token, str(lease.get('lease_token_salt') or ''))
    return bool(expected) and hmac.compare_digest(expected, supplied)


def _expire_running_lease(workflow: dict, task: dict, *, now: float,
                          reason: str) -> dict:
    task_id = str(task.get('task_id') or '')
    writer = str(task.get('mode') or '') in _WRITER_MODES
    task.update(
        status='lease_expired' if writer else 'pending',
        lease={},
    )
    if writer:
        workflow.update(status='recovery_audit', stop_reason=reason)
    _event(workflow, 'writer_lease_expired' if writer else 'task_lease_expired', {
        'task_id': task_id, 'mode': task.get('mode'), 'expired_at': now,
        'reason': reason,
    })
    return _save(workflow)


def _assert_active_lease(task: dict, token: str, *, now: float) -> None:
    if task.get('status') != 'running':
        raise DevelopmentAutopilotError(
            'agent_task_not_running', '任务未运行', 409)
    lease = task.get('lease') or {}
    if float(lease.get('expires_at') or 0) <= float(now):
        raise DevelopmentAutopilotError(
            'agent_task_lease_expired', '任务 lease 已过期', 409)
    if not _lease_matches(lease, token):
        raise DevelopmentAutopilotError(
            'agent_task_lease_mismatch', 'lease token 不匹配', 409)


def authorize_review_lease(*, workflow_id: str, task_id: str,
                           lease_token: str) -> dict:
    """Derive external-review identity from one live review-task lease."""
    require_development_autopilot_enabled()
    task_id = _safe(task_id, 'task_id')
    with _guard(workflow_id):
        workflow = _load(workflow_id)
        task = next((row for row in workflow.get('tasks') or []
                     if row.get('task_id') == task_id), None)
        if not task:
            raise DevelopmentAutopilotError(
                'agent_task_not_found', 'Agent 任务不存在', 404)
        now = time.time()
        if (task.get('status') == 'running'
                and float((task.get('lease') or {}).get('expires_at') or 0) <= now):
            _expire_running_lease(
                workflow, task, now=now, reason='expired_review_lease')
            raise DevelopmentAutopilotError(
                'agent_task_lease_expired', '评审 lease 已过期', 409)
        _assert_active_lease(task, lease_token, now=now)
        role = str(task.get('role') or '').strip()
        normalized_role = role.lower().replace('-', '_').replace(' ', '_')
        if not any(marker in normalized_role for marker in (
                'review', '评审', 'adjudicat')):
            raise DevelopmentAutopilotError(
                'agent_task_not_external_reviewer',
                '外部评审必须使用 active review-role lease', 409)
        lease = task.get('lease') or {}
        agent_id = str(lease.get('agent_id') or '')
        return {
            'reviewer_id': f'agent:{agent_id}',
            'workflow_id': str(workflow.get('workflow_id') or ''),
            'task_id': task_id,
            'task_role': role,
            'lease_token_hash': str(lease.get('lease_token_hash') or ''),
            'lease_expires_at': float(lease.get('expires_at') or 0),
            'workflow_version': int(workflow.get('version') or 0),
        }


def claim_task(*, workflow_id: str, task_id: str, agent_id: str,
               expected_version: int, lease_seconds: int,
               idempotency_key: str) -> dict:
    require_development_autopilot_enabled()
    task_id = _safe(task_id, 'task_id')
    agent_id = _safe(agent_id, 'agent_id')
    key = _safe(idempotency_key, 'idempotency_key')
    with _guard(workflow_id):
        workflow = _load(workflow_id)
        task = next((row for row in workflow.get('tasks') or []
                     if row.get('task_id') == task_id), None)
        if not task:
            raise DevelopmentAutopilotError('agent_task_not_found', 'Agent 任务不存在', 404)
        lease = task.get('lease') or {}
        if lease.get('idempotency_key') == key and lease.get('agent_id') == agent_id:
            return copy.deepcopy(workflow)
        _expect_version(workflow, expected_version)
        if workflow.get('status') in {
                'paused', 'cancelled', 'blocked', 'completed',
                'failure_triage', 'recovery_audit'}:
            raise DevelopmentAutopilotError(
                'agent_workflow_not_claimable', f'工作流状态为 {workflow.get("status")}', 409)
        now = time.time()
        expired_writer = next((
            row for row in workflow.get('tasks') or []
            if row.get('status') == 'running'
            and str(row.get('mode') or '') in _WRITER_MODES
            and float((row.get('lease') or {}).get('expires_at') or 0) <= now
        ), None)
        if expired_writer:
            _expire_running_lease(
                workflow, expired_writer, now=now,
                reason='expired_global_writer_lease')
            raise DevelopmentAutopilotError(
                'agent_writer_recovery_audit_required',
                '检测到过期 writer lease，必须先完成 recovery audit', 409)
        if task.get('status') == 'running' and float(lease.get('expires_at') or 0) > now:
            raise DevelopmentAutopilotError('agent_task_already_claimed', '任务 lease 尚未过期', 409)
        if str(task.get('mode') or '') in _WRITER_MODES:
            active_writer = next((
                row for row in workflow.get('tasks') or []
                if row is not task and row.get('status') == 'running'
                and str(row.get('mode') or '') in _WRITER_MODES
                and float((row.get('lease') or {}).get('expires_at') or 0) > now
            ), None)
            if active_writer:
                raise DevelopmentAutopilotError(
                    'agent_global_writer_lease_busy',
                    'workflow 已有 active writer lease', 409,
                    {'active_task_id': active_writer.get('task_id')})
        dependencies = {
            row.get('task_id'): row.get('status') for row in workflow.get('tasks') or []}
        unmet = [value for value in task.get('depends_on') or []
                 if dependencies.get(value) != 'pass']
        if unmet:
            raise DevelopmentAutopilotError(
                'agent_task_dependencies_unmet', '前置 Agent 任务尚未通过', 409,
                {'unmet_task_ids': unmet})
        token = uuid.uuid4().hex
        salt = secrets.token_hex(16)
        duration = max(30, min(int(lease_seconds), 3600))
        task['lease'] = {
            'lease_token_hash': _lease_digest(token, salt),
            'lease_token_salt': salt, 'agent_id': agent_id,
            'idempotency_key': key, 'claimed_at': now,
            'heartbeat_at': now, 'expires_at': now + duration,
        }
        task['status'] = 'running'
        task.setdefault('claim_history', []).append(copy.deepcopy(task['lease']))
        workflow['status'] = 'running'
        _event(workflow, 'task_claimed', {
            'task_id': task_id, 'agent_id': agent_id,
            'lease_token_hash': task['lease']['lease_token_hash'],
        })
        saved = _save(workflow)
        saved['claim'] = {
            'task_id': task_id, 'agent_id': agent_id,
            'lease_token': token,
            'expires_at': task['lease']['expires_at'],
        }
        return saved


def heartbeat_task(*, workflow_id: str, task_id: str, lease_token: str,
                   expected_version: int, idempotency_key: str,
                   lease_seconds: int = 300) -> dict:
    require_development_autopilot_enabled()
    key = _safe(idempotency_key, 'idempotency_key')
    with _guard(workflow_id):
        workflow = _load(workflow_id)
        task = next((row for row in workflow.get('tasks') or []
                     if row.get('task_id') == task_id), None)
        if not task or task.get('status') != 'running':
            raise DevelopmentAutopilotError('agent_task_not_running', '任务未运行', 409)
        now = time.time()
        if float((task.get('lease') or {}).get('expires_at') or 0) <= now:
            _expire_running_lease(
                workflow, task, now=now, reason='expired_heartbeat_lease')
            raise DevelopmentAutopilotError(
                'agent_task_lease_expired', '过期 lease 不能 heartbeat', 409)
        _assert_active_lease(task, lease_token, now=now)
        if (task.get('lease') or {}).get('heartbeat_idempotency_key') == key:
            return copy.deepcopy(workflow)
        _expect_version(workflow, expected_version)
        task['lease']['heartbeat_at'] = now
        task['lease']['expires_at'] = now + max(30, min(int(lease_seconds), 3600))
        task['lease']['heartbeat_idempotency_key'] = key
        _event(workflow, 'task_heartbeat', {'task_id': task_id})
        return _save(workflow)


def complete_task(*, workflow_id: str, task_id: str, lease_token: str,
                  expected_version: int, result: dict,
                  idempotency_key: str) -> dict:
    require_development_autopilot_enabled()
    key = _safe(idempotency_key, 'idempotency_key')
    status = str(result.get('status') or '')
    if status not in _TERMINAL:
        raise DevelopmentAutopilotError(
            'invalid_agent_task_result', '结果必须为 pass/fail/blocked/cancelled', 422)
    with _guard(workflow_id):
        workflow = _load(workflow_id)
        task = next((row for row in workflow.get('tasks') or []
                     if row.get('task_id') == task_id), None)
        if not task:
            raise DevelopmentAutopilotError('agent_task_not_found', 'Agent 任务不存在', 404)
        previous = task.get('result') or {}
        if previous.get('idempotency_key') == key:
            if not hmac.compare_digest(
                    str(previous.get('completion_lease_token_hash') or ''),
                    _hash({'completion_token': str(lease_token or '')})):
                raise DevelopmentAutopilotError(
                    'agent_task_lease_mismatch', '完成重放 lease token 不匹配', 409)
            return copy.deepcopy(workflow)
        _expect_version(workflow, expected_version)
        now = time.time()
        if (task.get('status') == 'running'
                and float((task.get('lease') or {}).get('expires_at') or 0) <= now):
            _expire_running_lease(
                workflow, task, now=now, reason='expired_completion_lease')
            raise DevelopmentAutopilotError(
                'agent_task_lease_expired', '过期 lease 不能完成任务', 409)
        _assert_active_lease(task, lease_token, now=now)
        normalized = {
            'status': status,
            'summary': str(result.get('summary') or '')[:4000],
            'report_path': str(result.get('report_path') or '')[:1000],
            'report_sha256': _normalize_report_sha256(
                str(result.get('report_sha256') or ''))[:128],
            'confidence': max(0.0, min(float(result.get('confidence') or 0), 1.0)),
            'input_artifact_hashes': copy.deepcopy(result.get('input_artifact_hashes') or {}),
            'before_source_digest': str(result.get('before_source_digest') or '')[:128],
            'after_source_digest': str(result.get('after_source_digest') or '')[:128],
            'idempotency_key': key, 'completed_at': now,
            'completion_lease_token_hash': _hash({
                'completion_token': str(lease_token or '')}),
        }
        failure_facts = normalize_failure_facts(result) if status == 'fail' else {}
        signature = canonical_failure_signature(failure_facts) if failure_facts else ''
        normalized['failure_facts'] = failure_facts
        # Compatibility projection only.  Signature v1 reads failure_facts and
        # never this alias, task_id, report SHA, paths, or prose.
        normalized['failure_dimension'] = str(failure_facts.get('dimension') or '')
        normalized['failure_signature'] = signature
        task.update(status=status, result=normalized, lease={})
        next_route = {}
        if str(task.get('mode') or '') in _WRITER_MODES and status == 'fail' and signature:
            counts = workflow.setdefault('failure_signature_counts', {})
            cumulative = int(counts.get(signature) or 0) + 1
            counts[signature] = cumulative
            task.update(
                consecutive_failure_signature=signature,
                consecutive_failure_count=cumulative,
            )
            occurrence = {
                'signature_version': FAILURE_SIGNATURE_VERSION,
                'failure_signature': signature,
                'failure_facts': copy.deepcopy(failure_facts),
                'workflow_occurrence_count': cumulative,
                # Task/report/prose/time are explicit audit sidecars and never
                # inputs to canonical_failure_signature().
                'audit_context': {
                    'task_id': task_id,
                    'report_sha256': normalized['report_sha256'],
                    'summary': normalized['summary'],
                    'completed_at': normalized['completed_at'],
                },
            }
            task.setdefault('failure_history', []).append(copy.deepcopy(occurrence))
            task['failure_history'] = task['failure_history'][-50:]
            workflow.setdefault('failure_history', []).append(occurrence)
            workflow['failure_history'] = workflow['failure_history'][-200:]
            if cumulative >= _FAILURE_TRIAGE_THRESHOLD:
                next_route = {
                    'stage': 'failure_triage',
                    'reason': 'repeated_canonical_failure_signature',
                    'signature_version': FAILURE_SIGNATURE_VERSION,
                    'failure_signature': signature,
                    'workflow_occurrence_count': cumulative,
                    'micro_tweak_retry_allowed': False,
                    'required_route': [
                        'evidence', 'solution', 'challenge', 'decision'],
                }
                workflow.update(
                    status='failure_triage',
                    stop_reason=(
                        f'repeated_failure_signature:{signature}:'
                        f'{cumulative}'),
                    next_route=next_route,
                )
        elif status != 'fail' or not signature:
            task.update(
                consecutive_failure_signature='',
                consecutive_failure_count=0,
            )
        if status == 'blocked':
            workflow.update(status='blocked', stop_reason=normalized['summary'])
        elif all(row.get('status') == 'pass' for row in workflow.get('tasks') or []):
            workflow.update(status='completed', stop_reason='')
        _event(workflow, 'task_completed', {
            'task_id': task_id, 'status': status,
            'report_sha256': normalized['report_sha256'],
            'failure_dimension': normalized['failure_dimension'],
            'failure_facts': copy.deepcopy(failure_facts),
            'failure_signature': signature,
            'workflow_occurrence_count': int(
                (workflow.get('failure_signature_counts') or {}).get(signature) or 0),
            'next_route': copy.deepcopy(next_route),
        })
        return _save(workflow)


def transition_workflow(*, workflow_id: str, action: str,
                        expected_version: int, idempotency_key: str,
                        reason: str = '') -> dict:
    require_development_autopilot_enabled()
    key = _safe(idempotency_key, 'idempotency_key')
    if action not in {'pause', 'resume', 'cancel'}:
        raise DevelopmentAutopilotError('invalid_agent_workflow_action', 'action 无效', 422)
    with _guard(workflow_id):
        workflow = _load(workflow_id)
        if any(row.get('idempotency_key') == key
               for row in workflow.get('transition_history') or []):
            return copy.deepcopy(workflow)
        _expect_version(workflow, expected_version)
        if action == 'pause':
            workflow.update(status='paused', stop_reason=str(reason or 'controller_paused')[:500])
        elif action == 'resume':
            if workflow.get('status') not in {'paused', 'blocked', 'recovery_audit'}:
                raise DevelopmentAutopilotError(
                    'agent_workflow_not_resumable', '当前状态不可 resume', 409)
            workflow.update(status='running', stop_reason='')
        else:
            workflow.update(status='cancelled', stop_reason=str(reason or 'controller_cancelled')[:500])
            for task in workflow.get('tasks') or []:
                if task.get('status') in {'pending', 'running'}:
                    task.update(status='cancelled', lease={})
        workflow.setdefault('transition_history', []).append({
            'idempotency_key': key, 'action': action, 'at': time.time(),
        })
        event_type = {
            'pause': 'workflow_paused',
            'resume': 'workflow_resumed',
            'cancel': 'workflow_cancelled',
        }[action]
        _event(workflow, event_type, {
            'reason': workflow.get('stop_reason') or '',
        })
        return _save(workflow)


def recover_workflow_projection(workflow_id: str) -> dict:
    """Rebuild workflow.json from the last verified immutable event snapshot."""
    require_development_autopilot_enabled()
    workflow_id = _safe(workflow_id, 'workflow_id')
    with _guard(workflow_id):
        event_folder = os.path.join(_folder(workflow_id), 'events')
        try:
            paths = sorted(
                os.path.join(event_folder, name) for name in os.listdir(event_folder)
                if name.endswith('.json'))
        except FileNotFoundError as ex:
            raise DevelopmentAutopilotError(
                'agent_workflow_events_missing', '不可从空事件日志恢复', 409) from ex
        previous_sequence = 0
        last_event = None
        for path in paths:
            try:
                event = _read(path)
            except Exception as ex:
                raise DevelopmentAutopilotError(
                    'agent_workflow_event_corrupt',
                    f'事件无法解析: {os.path.basename(path)}', 409) from ex
            if not event:
                raise DevelopmentAutopilotError(
                    'agent_workflow_event_corrupt', f'事件无法读取: {os.path.basename(path)}', 409)
            stored_hash = str(event.get('event_hash') or '')
            unsigned = copy.deepcopy(event)
            unsigned.pop('event_hash', None)
            sequence = int(event.get('sequence') or 0)
            if (_hash(unsigned) != stored_hash or sequence != previous_sequence + 1
                    or str(event.get('workflow_id') or '') != workflow_id):
                raise DevelopmentAutopilotError(
                    'agent_workflow_event_chain_invalid',
                    f'事件链校验失败: {os.path.basename(path)}', 409)
            previous_sequence = sequence
            last_event = event
        if not last_event or not isinstance(last_event.get('projection_after_event'), dict):
            raise DevelopmentAutopilotError(
                'agent_workflow_projection_snapshot_missing', '事件不含可恢复投影', 409)
        projection = copy.deepcopy(last_event['projection_after_event'])
        projection['event_sequence'] = previous_sequence
        projection['last_event_id'] = last_event.get('event_id') or ''
        projection['version'] = previous_sequence
        projection['updated_at'] = last_event.get('at') or time.time()
        _project_defaults(projection)
        unsigned_projection = copy.deepcopy(projection)
        unsigned_projection.pop('projection_hash', None)
        projection['projection_hash'] = _hash(unsigned_projection)
        _atomic(_projection_path(workflow_id), projection)
        return copy.deepcopy(projection)


__all__ = [
    'DEFAULT_TEST_COLLECTION_CONTRACT', 'FAILURE_SIGNATURE_VERSION',
    'SOURCE_SNAPSHOT_SUFFIX', 'WORKFLOW_ROOT',
    'archive_source_snapshot', 'authorize_review_lease', 'claim_task',
    'complete_task', 'create_workflow',
    'canonical_failure_signature', 'get_workflow', 'heartbeat_task',
    'normalize_failure_facts',
    'recover_workflow_projection',
    'transition_workflow',
]
