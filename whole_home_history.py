# -*- coding: utf-8 -*-
"""Project-centred whole-home history, replay snapshots and style variants.

The existing project/run JSON files remain authoritative.  This module adds
content-addressed replay evidence, immutable lineage and resumable batch state
without rewriting legacy records merely to make them visible.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Iterable, Mapping, Optional

from .config import MAIN_OUTPUT_DIR, logger
from .whole_home_dev_lock import data_root_lock, durable_atomic_json


ROOT = os.path.join(MAIN_OUTPUT_DIR, '_whole_home')
SNAPSHOT_DIR = os.path.join(ROOT, 'replay_snapshots')
BATCH_DIR = os.path.join(ROOT, 'variant_batches')
for _folder in (SNAPSHOT_DIR, BATCH_DIR):
    os.makedirs(_folder, exist_ok=True)

SNAPSHOT_SCHEMA_VERSION = 2
BATCH_SCHEMA_VERSION = 1
BATCH_PREVIEW_TTL_SECONDS = 30 * 60
TERMINAL_RUN_STATUSES = frozenset({'done', 'partial', 'failed', 'cancelled'})
TERMINAL_BATCH_STATUSES = frozenset({'done', 'partial', 'failed', 'cancelled'})

_SECRET_KEY_PARTS = (
    'api_key', 'apikey', 'authorization', 'credential', 'password', 'secret',
    'access_token', 'refresh_token',
)


class WholeHomeHistoryError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409,
                 details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = copy.deepcopy(details or {})

    def to_dict(self) -> dict:
        return {'code': self.code, 'message': self.message, **copy.deepcopy(self.details)}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _safe_id(value: str) -> str:
    return os.path.basename(str(value or '')).replace('..', '')


def _atomic_json(path: str, payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.realpath(path))
    os.makedirs(folder, exist_ok=True)
    with data_root_lock(folder, f'history-{os.path.basename(path)}'):
        durable_atomic_json(path, copy.deepcopy(dict(payload)))


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None
    except Exception as ex:
        logger.warning(f'[整屋历史] 读取失败 {path}: {ex}')
        return None


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            normalized = str(key).lower().replace('-', '_')
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                continue
            out[str(key)] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return copy.deepcopy(value)


def _managed_relative_path(path: str) -> str:
    candidate = os.path.realpath(str(path or ''))
    root = os.path.realpath(MAIN_OUTPUT_DIR)
    if not candidate or not os.path.isfile(candidate):
        return ''
    try:
        if os.path.commonpath([root, candidate]) != root:
            return ''
    except ValueError:
        return ''
    return os.path.relpath(candidate, root).replace('\\', '/')


def _resolve_managed_path(relative_path: str) -> str:
    relative = str(relative_path or '').replace('/', os.sep)
    root = os.path.realpath(MAIN_OUTPUT_DIR)
    candidate = os.path.realpath(os.path.join(root, relative))
    try:
        if os.path.commonpath([root, candidate]) != root:
            return ''
    except ValueError:
        return ''
    return candidate


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _asset_evidence(path: str, role: str, *, required: bool = False,
                    hash_content: bool = True) -> dict:
    relative = _managed_relative_path(path)
    if not relative:
        if required:
            raise WholeHomeHistoryError(
                'history_asset_missing', f'历史资产不存在或不在托管目录：{role}',
                details={'role': role})
        return {'role': role, 'managed_relative_path': '', 'sha256': '',
                'byte_length': 0, 'available': False}
    absolute = _resolve_managed_path(relative)
    stat = os.stat(absolute)
    return {
        'role': str(role), 'managed_relative_path': relative,
        'sha256': _file_sha256(absolute) if hash_content else '',
        'byte_length': int(stat.st_size),
        'available': True,
    }


def _snapshot_project_state(project: Mapping[str, Any], run: Mapping[str, Any]) -> dict:
    keys = (
        'source_type', 'status', 'stage', 'summary', 'floorplan_path',
        'source_analysis_id', 'cad_path', 'reference_url', 'reference_contract',
        'source_registration', 'registration_hash', 'geometry_acceptance',
        'geometry_schema_version', 'geometry_acceptance_required', 'input_grade',
        'cad_source', 'cad_import', 'parse_report', 'raster_alignment_metrics',
        'raster_evidence', 'ai_model', 'semantic_ai_model', 'prompt_version',
        'verified', 'verified_revision',
    )
    state = {key: copy.deepcopy(project.get(key)) for key in keys if key in project}
    state['model'] = copy.deepcopy(
        run.get('model_snapshot') or project.get('model') or {})
    return _redact(state)


def build_replay_snapshot(project: Mapping[str, Any], run: Mapping[str, Any], *,
                          hash_assets: bool = True) -> dict:
    captures = copy.deepcopy(
        run.get('capture_snapshots') or [
            capture for capture in project.get('captures') or []
            if str(capture.get('capture_id') or '') in set(run.get('capture_ids') or [])
        ])
    assets: dict[tuple[str, str], dict] = {}

    def add(path: str, role: str) -> None:
        evidence = _asset_evidence(path, role, hash_content=hash_assets)
        key = (evidence['managed_relative_path'], role)
        assets[key] = evidence

    for role, path in (
        ('floorplan', str(project.get('floorplan_path') or run.get('floorplan_path') or '')),
        ('cad_source', str(project.get('cad_path') or (run.get('cad_source_snapshot') or {}).get('path') or '')),
        ('floor_sample', str(run.get('floor_path') or '')),
        ('style_reference', str(run.get('style_ref_path') or '')),
    ):
        if path:
            add(path, role)
    for capture in captures:
        capture_id = str(capture.get('capture_id') or 'capture')
        for key in ('rgb_path', 'depth_path', 'normal_path', 'edge_path',
                    'semantic_path', 'plan_overlay_path'):
            if capture.get(key):
                add(str(capture[key]), f'capture:{capture_id}:{key}')
    for entry in run.get('input_manifest') or []:
        if not isinstance(entry, Mapping):
            continue
        path = str(entry.get('path') or '')
        role = str(entry.get('label') or entry.get('role') or 'run_input')
        if path:
            add(path, role)

    payload = {
        'schema_version': SNAPSHOT_SCHEMA_VERSION,
        'captured_at': float(run.get('created_at') or time.time()),
        'source_project_id': str(run.get('project_id') or project.get('project_id') or ''),
        'source_revision': int(run.get('model_revision') or project.get('verified_revision')
                               or project.get('revision') or 0),
        'source_model_hash': str(run.get('model_hash') or ''),
        'source_run_id': str(run.get('run_id') or ''),
        'asset_validation': 'sha256' if hash_assets else 'deferred_until_fork',
        'project_state': _snapshot_project_state(project, run),
        'captures': _redact(captures),
        'asset_manifest': sorted(assets.values(), key=lambda row: (
            str(row.get('role') or ''), str(row.get('managed_relative_path') or ''))),
    }
    content_hash = canonical_hash(payload)
    payload['snapshot_id'] = f'whsnap_{content_hash[:24]}'
    payload['snapshot_hash'] = content_hash
    return payload


def validate_replay_snapshot(snapshot: Mapping[str, Any], *, verify_assets: bool = False) -> dict:
    value = copy.deepcopy(dict(snapshot or {}))
    if int(value.get('schema_version') or 0) != SNAPSHOT_SCHEMA_VERSION:
        raise WholeHomeHistoryError('history_snapshot_version_unsupported',
                                    '历史快照版本不受支持')
    supplied = str(value.pop('snapshot_hash', '') or '')
    expected_id = str(value.pop('snapshot_id', '') or '')
    actual = canonical_hash(value)
    value['snapshot_id'] = expected_id
    value['snapshot_hash'] = supplied
    if not supplied or not hmac.compare_digest(supplied, actual):
        raise WholeHomeHistoryError('history_snapshot_tampered', '历史快照哈希不匹配')
    if expected_id != f'whsnap_{actual[:24]}':
        raise WholeHomeHistoryError('history_snapshot_id_mismatch', '历史快照 ID 不匹配')
    if verify_assets:
        blockers = verify_snapshot_assets(value)
        if blockers:
            raise WholeHomeHistoryError(
                'history_asset_verification_failed', '历史快照存在缺失或变化的资产',
                details={'blockers': blockers})
    return value


def replay_snapshot_path(snapshot_id: str) -> str:
    return os.path.join(SNAPSHOT_DIR, f'{_safe_id(snapshot_id)}.json')


def save_replay_snapshot(snapshot: Mapping[str, Any]) -> dict:
    value = validate_replay_snapshot(snapshot)
    path = replay_snapshot_path(str(value['snapshot_id']))
    existing = _read_json(path)
    if existing:
        if canonical_json(existing) != canonical_json(value):
            raise WholeHomeHistoryError('history_snapshot_collision',
                                        '同 ID 历史快照内容不一致')
        return value
    _atomic_json(path, value)
    return value


def load_replay_snapshot(reference: Mapping[str, Any] | str) -> Optional[dict]:
    snapshot_id = (str(reference) if isinstance(reference, str)
                   else str((reference or {}).get('snapshot_id') or ''))
    value = _read_json(replay_snapshot_path(snapshot_id)) if snapshot_id else None
    if not value:
        return None
    return validate_replay_snapshot(value)


def ensure_replay_snapshot(project: Mapping[str, Any], run: Mapping[str, Any]) -> tuple[dict, dict]:
    reference = run.get('replay_snapshot_ref') if isinstance(run.get('replay_snapshot_ref'), Mapping) else {}
    existing = load_replay_snapshot(reference) if reference else None
    snapshot = existing or save_replay_snapshot(build_replay_snapshot(project, run))
    return snapshot, {
        'schema_version': SNAPSHOT_SCHEMA_VERSION,
        'snapshot_id': snapshot['snapshot_id'],
        'snapshot_hash': snapshot['snapshot_hash'],
    }


def transient_replay_snapshot(project: Mapping[str, Any], run: Mapping[str, Any]) -> dict:
    reference = run.get('replay_snapshot_ref') if isinstance(run.get('replay_snapshot_ref'), Mapping) else {}
    # Legacy runs did not persist a snapshot before dispatch.  Their GET view
    # must remain fast even when RGB/depth/semantic assets total gigabytes;
    # content hashing is deferred to the explicit copy-on-write fork action.
    return load_replay_snapshot(reference) or build_replay_snapshot(
        project, run, hash_assets=False)


def verify_snapshot_assets(snapshot: Mapping[str, Any]) -> list[dict]:
    blockers = []
    for entry in snapshot.get('asset_manifest') or []:
        if not isinstance(entry, Mapping) or not entry.get('managed_relative_path'):
            continue
        path = _resolve_managed_path(str(entry.get('managed_relative_path') or ''))
        role = str(entry.get('role') or 'asset')
        if not path or not os.path.isfile(path):
            blockers.append({'code': 'history_asset_missing', 'role': role})
            continue
        expected = str(entry.get('sha256') or '')
        if not expected:
            blockers.append({
                'code': 'history_asset_verification_deferred', 'role': role,
                'message': '旧任务资产将在复制为新方案时执行完整 SHA-256 校验',
            })
            continue
        actual = _file_sha256(path)
        if not hmac.compare_digest(actual, expected):
            blockers.append({'code': 'history_asset_hash_mismatch', 'role': role})
    return blockers


def replay_capability(snapshot: Mapping[str, Any], current_project: Optional[Mapping[str, Any]] = None,
                      *, current_model_hash: str = '') -> dict:
    blockers = verify_snapshot_assets(snapshot)
    hard_asset_blockers = [row for row in blockers
                           if row.get('code') != 'history_asset_verification_deferred']
    state = snapshot.get('project_state') if isinstance(snapshot.get('project_state'), Mapping) else {}
    registration = state.get('source_registration') if isinstance(state.get('source_registration'), Mapping) else {}
    acceptance = state.get('geometry_acceptance') if isinstance(state.get('geometry_acceptance'), Mapping) else {}
    required = bool(state.get('geometry_acceptance_required'))
    if hard_asset_blockers:
        status = 'read_only_only'
    elif required and registration and acceptance:
        status = 'exact_requires_rebind'
    elif required:
        status = 'exact_requires_human_revalidation'
        blockers.append({'code': 'history_geometry_revalidation_required',
                         'message': '旧任务缺少新版几何验收快照'})
    else:
        status = 'exact_ready'
    if (status == 'exact_ready'
            and any(row.get('code') == 'history_asset_verification_deferred'
                    for row in blockers)):
        status = 'exact_requires_rebind'
    source_hash = str(snapshot.get('source_model_hash') or '')
    if (current_project and source_hash and current_model_hash
            and int(current_project.get('revision') or 0) == int(snapshot.get('source_revision') or 0)
            and hmac.compare_digest(source_hash, current_model_hash)):
        if status == 'exact_requires_human_revalidation':
            status = 'exact_requires_rebind'
            blockers = [row for row in blockers
                        if row.get('code') != 'history_geometry_revalidation_required']
    return {'status': status, 'can_view': True, 'can_fork': not hard_asset_blockers,
            'blockers': blockers}


def snapshot_project(snapshot: Mapping[str, Any]) -> dict:
    state = copy.deepcopy(snapshot.get('project_state') or {})
    state.update({
        'project_id': str(snapshot.get('source_project_id') or ''),
        'revision': int(snapshot.get('source_revision') or 0),
        'verified_revision': int(state.get('verified_revision') or snapshot.get('source_revision') or 0),
        'captures': copy.deepcopy(snapshot.get('captures') or []),
        'history_read_only': True,
        'history_snapshot_id': str(snapshot.get('snapshot_id') or ''),
    })
    return state


def prepare_branch_project(snapshot: Mapping[str, Any], *, project_id: str,
                           branch_name: str, idempotency_key: str) -> dict:
    state = copy.deepcopy(snapshot.get('project_state') or {})
    now = time.time()
    source_project_id = str(snapshot.get('source_project_id') or '')
    root_project_id = source_project_id
    source_lineage = state.get('lineage') if isinstance(state.get('lineage'), Mapping) else {}
    if source_lineage.get('root_project_id'):
        root_project_id = str(source_lineage['root_project_id'])
    state.update({
        'project_id': project_id,
        'summary': str(branch_name or state.get('summary') or '历史方案分支')[:200],
        'created_at': now, 'updated_at': now,
        'revision': 1, 'verified_revision': 0, 'verified': False,
        'status': 'history_restored', 'stage': '历史模型已精确恢复，正在重建几何生产锁',
        'error': '', 'captures': copy.deepcopy(snapshot.get('captures') or []),
        'operations': [{
            'type': 'fork_from_history', 'at': now, 'revision': 1,
            'actor': 'whole-home-history',
            'payload': {
                'source_project_id': source_project_id,
                'source_run_id': str(snapshot.get('source_run_id') or ''),
                'source_snapshot_id': str(snapshot.get('snapshot_id') or ''),
                'source_revision': int(snapshot.get('source_revision') or 0),
                'source_model_hash': str(snapshot.get('source_model_hash') or ''),
            },
        }],
        'lineage': {
            'root_project_id': root_project_id,
            'parent_project_id': source_project_id,
            'source_project_id': source_project_id,
            'source_run_id': str(snapshot.get('source_run_id') or ''),
            'source_snapshot_id': str(snapshot.get('snapshot_id') or ''),
            'source_revision': int(snapshot.get('source_revision') or 0),
            'source_model_hash': str(snapshot.get('source_model_hash') or ''),
            'branch_kind': 'history_fork', 'branch_name': str(branch_name or '')[:200],
            'idempotency_key': str(idempotency_key or '')[:160], 'created_at': now,
        },
    })
    # Old hashes/reports bind the source project id.  The route recompiles them.
    state.pop('geometry_acceptance', None)
    model = state.get('model') if isinstance(state.get('model'), dict) else {}
    model.pop('geometry_manifest', None)
    model.pop('model_facts_hash', None)
    state['model'] = model
    return state


def _event(event_type: str, occurred_at: float, project_id: str, title: str,
           **fields: Any) -> dict:
    seed = {'type': event_type, 'occurred_at': float(occurred_at or 0),
            'project_id': project_id, **fields}
    event_id = f'whe_{canonical_hash(seed)[:20]}'
    return {'event_id': event_id, 'type': event_type,
            'occurred_at': float(occurred_at or 0), 'project_id': project_id,
            'title': title, **copy.deepcopy(fields)}


def build_history(project_id: str, projects: Iterable[Mapping[str, Any]],
                  runs: Iterable[Mapping[str, Any]], batches: Iterable[Mapping[str, Any]],
                  *, limit: int = 100, cursor: str = '') -> dict:
    project_rows = [copy.deepcopy(dict(row)) for row in projects if isinstance(row, Mapping)]
    selected = next((row for row in project_rows
                     if str(row.get('project_id') or '') == project_id), None)
    if not selected:
        raise WholeHomeHistoryError('whole_home_project_not_found', '整屋项目不存在', 404)
    lineage = selected.get('lineage') if isinstance(selected.get('lineage'), Mapping) else {}
    root_id = str(lineage.get('root_project_id') or selected.get('project_id') or '')
    family_ids = {
        str(row.get('project_id') or '') for row in project_rows
        if str(((row.get('lineage') or {}).get('root_project_id')
                if isinstance(row.get('lineage'), Mapping) else '') or row.get('project_id') or '') == root_id
    }
    events = []
    branches = []
    for project in project_rows:
        pid = str(project.get('project_id') or '')
        if pid not in family_ids:
            continue
        branches.append({
            'project_id': pid, 'summary': str(project.get('summary') or ''),
            'status': str(project.get('status') or ''),
            'revision': int(project.get('revision') or 0),
            'verified': bool(project.get('verified')),
            'updated_at': float(project.get('updated_at') or 0),
            'lineage': copy.deepcopy(project.get('lineage') or {}),
        })
        events.append(_event(
            'project_created', float(project.get('created_at') or project.get('updated_at') or 0),
            pid, '创建整屋项目', root_project_id=root_id,
            status=str(project.get('status') or ''), summary=str(project.get('summary') or ''),
            model_revision=int(project.get('revision') or 0),
        ))
        for index, operation in enumerate(project.get('operations') or []):
            if not isinstance(operation, Mapping):
                continue
            op_type = str(operation.get('type') or 'project_operation')
            events.append(_event(
                op_type, float(operation.get('at') or project.get('updated_at') or 0),
                pid, operation_title(op_type), root_project_id=root_id,
                status='done', summary='', model_revision=int(operation.get('revision') or 0),
                operation_index=index,
            ))
    for run in runs:
        if not isinstance(run, Mapping) or str(run.get('project_id') or '') not in family_ids:
            continue
        results = run.get('results') or []
        thumbs = []
        for result in results:
            if not isinstance(result, Mapping):
                continue
            path = str(result.get('path') or result.get('final_path') or '')
            relative = _managed_relative_path(path)
            if relative:
                thumbs.append('/outputs/' + relative.replace('\\', '/'))
            if len(thumbs) >= 4:
                break
        events.append(_event(
            'generation_run', float(run.get('created_at') or run.get('updated_at') or 0),
            str(run.get('project_id') or ''), '整屋效果图生成', root_project_id=root_id,
            run_id=str(run.get('run_id') or ''), status=str(run.get('status') or ''),
            summary=str(run.get('stage') or run.get('error') or ''),
            model_revision=int(run.get('model_revision') or 0),
            model_hash=str(run.get('model_hash') or ''),
            style=str(run.get('style') or ''), lighting=str(run.get('lighting') or ''),
            prompt=str(run.get('prompt') or '')[:240], thumbnail_urls=thumbs,
            counts={
                'results': len(results),
                'deliverable': int((run.get('summary_counts') or {}).get('deliverable') or 0),
                'generation_calls': int(run.get('actual_generation_calls') or 0),
                'qa_calls': int(run.get('actual_qa_calls') or 0),
            },
            variant_of_run_id=str(run.get('variant_of_run_id') or ''),
            variant_batch_id=str(run.get('variant_batch_id') or ''),
        ))
    for batch in batches:
        if not isinstance(batch, Mapping) or str(batch.get('project_id') or '') not in family_ids:
            continue
        events.append(_event(
            'variant_batch', float(batch.get('created_at') or batch.get('updated_at') or 0),
            str(batch.get('project_id') or ''), '整套风格变体', root_project_id=root_id,
            variant_batch_id=str(batch.get('variant_batch_id') or ''),
            run_id=str(batch.get('source_run_id') or ''),
            status=str(batch.get('status') or ''),
            summary=str((batch.get('style_spec') or {}).get('style') or ''),
            style=str((batch.get('style_spec') or {}).get('style') or ''),
            lighting=str((batch.get('style_spec') or {}).get('lighting') or ''),
            counts=batch_counts(batch),
        ))
    events.sort(key=lambda row: (float(row.get('occurred_at') or 0), row['event_id']), reverse=True)
    if cursor:
        try:
            cursor_time, cursor_id = cursor.split(':', 1)
            point = (float(cursor_time), cursor_id)
            events = [row for row in events
                      if (float(row.get('occurred_at') or 0), row['event_id']) < point]
        except (ValueError, TypeError):
            raise WholeHomeHistoryError('history_cursor_invalid', '历史游标无效', 422)
    page = events[:max(1, min(int(limit or 100), 200))]
    next_cursor = ''
    if len(events) > len(page) and page:
        last = page[-1]
        next_cursor = f"{float(last.get('occurred_at') or 0)}:{last['event_id']}"
    branches.sort(key=lambda row: float(row.get('updated_at') or 0), reverse=True)
    return {'root_project_id': root_id, 'selected_project_id': project_id,
            'branches': branches, 'events': page, 'next_cursor': next_cursor}


def operation_title(operation_type: str) -> str:
    names = {
        'cad_import_local': '导入 CAD 并建立灰模',
        'cad_import_needs_review': 'CAD 3D 草稿已保存，等待几何复核',
        'cad_import_failed': 'CAD 导入失败并保存诊断',
        'cad_reparse_local': '重新解析 CAD 并更新灰模',
        'cad_reparse_needs_manual_space_review': 'CAD 重解析草稿已保存，等待空间复核',
        'commit_geometry_acceptance': '图纸与 3D 对应验收通过',
        'verify_whole_home': '锁定整屋几何',
        'save_model': '保存模型修改',
        'save_capture': '保存 3D 机位',
        'fork_from_history': '从历史创建方案分支',
        'source_registration': '锁定图纸坐标配准',
    }
    return names.get(operation_type, operation_type.replace('_', ' '))


def batch_path(batch_id: str) -> str:
    return os.path.join(BATCH_DIR, f'{_safe_id(batch_id)}.json')


def save_variant_batch(batch: Mapping[str, Any]) -> dict:
    value = copy.deepcopy(dict(batch))
    value['updated_at'] = time.time()
    _atomic_json(batch_path(str(value.get('variant_batch_id') or '')), value)
    return value


def load_variant_batch(batch_id: str) -> Optional[dict]:
    return _read_json(batch_path(batch_id))


def list_variant_batches(project_id: str = '') -> list[dict]:
    rows = []
    try:
        names = [name for name in os.listdir(BATCH_DIR) if name.endswith('.json')]
    except OSError:
        return rows
    for name in names:
        row = _read_json(os.path.join(BATCH_DIR, name))
        if row and (not project_id or str(row.get('project_id') or '') == project_id):
            rows.append(row)
    return sorted(rows, key=lambda row: float(row.get('updated_at') or 0), reverse=True)


def batch_counts(batch: Mapping[str, Any]) -> dict:
    counts = {'total': 0, 'pending': 0, 'running': 0, 'done': 0,
              'failed': 0, 'cancelled': 0, 'needs_reconcile': 0}
    for item in batch.get('items') or []:
        counts['total'] += 1
        status = str(item.get('status') or 'pending')
        if status in counts:
            counts[status] += 1
    return counts


def public_variant_batch(batch: Mapping[str, Any]) -> dict:
    out = _redact(batch)
    out.pop('confirmation_phrase_hash', None)
    out['counts'] = batch_counts(out)
    return out


def _result_items(source_run: Mapping[str, Any], project: Mapping[str, Any],
                  excluded_artifact_ids: Iterable[str]) -> list[dict]:
    excluded = {str(value) for value in excluded_artifact_ids}
    captures = {str(row.get('capture_id') or ''): row
                for row in project.get('captures') or [] if isinstance(row, Mapping)}
    items = []
    seen = set()
    for index, result in enumerate(source_run.get('results') or []):
        if not isinstance(result, Mapping):
            continue
        artifact_id = str(result.get('result_id') or f'result_{index}')
        if artifact_id in excluded:
            continue
        capture_id = str(result.get('capture_id') or '')
        model_key = str(result.get('model_key') or '')
        key = (artifact_id, capture_id, model_key)
        if key in seen or capture_id not in captures or model_key not in {'b2', 'pro'}:
            continue
        capture = captures[capture_id]
        if str(capture.get('status') or '') != 'confirmed':
            raise WholeHomeHistoryError(
                'variant_capture_not_confirmed', f'历史机位 {capture_id} 未确认或已失效',
                details={'capture_id': capture_id, 'artifact_id': artifact_id})
        seen.add(key)
        items.append({
            'item_id': f'vitem_{canonical_hash(key)[:16]}',
            'source_artifact_id': artifact_id,
            'capture_id': capture_id, 'camera_name': str(result.get('camera_name') or capture.get('name') or ''),
            'room_id': str(result.get('room_id') or capture.get('room_id') or ''),
            'model_key': model_key, 'status': 'pending', 'child_run_id': '',
            'error': '', 'claimed_at': 0, 'completed_at': 0,
        })
    if not items:
        raise WholeHomeHistoryError('variant_batch_empty', '原任务没有可重新生成的有效结果', 409)
    return items


def create_variant_preview(*, batch_id: str, project: Mapping[str, Any],
                           source_run: Mapping[str, Any], style_spec: Mapping[str, Any],
                           excluded_artifact_ids: Iterable[str], project_state_hash: str,
                           image_call_cap: int, qa_call_cap: int) -> tuple[dict, str]:
    if str(source_run.get('project_id') or '') == str(project.get('project_id') or ''):
        lineage_source = str((project.get('lineage') or {}).get('source_run_id') or '')
    else:
        lineage_source = str((project.get('lineage') or {}).get('source_run_id') or '')
        if lineage_source != str(source_run.get('run_id') or ''):
            raise WholeHomeHistoryError('variant_source_lineage_mismatch',
                                        '当前分支并非从该历史任务创建')
    items = _result_items(source_run, project, excluded_artifact_ids)
    normalized_style = {
        'style': str(style_spec.get('style') or '现代自然')[:200],
        'lighting': str(style_spec.get('lighting') or '自然日光')[:200],
        'prompt': str(style_spec.get('prompt') or '')[:5000],
        'floor_path': str(style_spec.get('floor_path') or ''),
        'style_ref_path': str(style_spec.get('style_ref_path') or ''),
        'aspect_ratio': str(style_spec.get('aspect_ratio') or '4:3'),
        'resolution': '2K', 'material_mode': 'floor_sample',
    }
    evidence = [_asset_evidence(normalized_style['floor_path'], 'floor_sample', required=True)]
    if normalized_style['style_ref_path']:
        evidence.append(_asset_evidence(normalized_style['style_ref_path'], 'style_reference', required=True))
    snapshot = {
        'schema_version': BATCH_SCHEMA_VERSION, 'variant_batch_id': batch_id,
        'project_id': str(project.get('project_id') or ''),
        'project_revision': int(project.get('revision') or 0),
        'project_state_hash': project_state_hash,
        'source_run_id': str(source_run.get('run_id') or ''),
        'source_snapshot_ref': copy.deepcopy(source_run.get('replay_snapshot_ref') or {}),
        'style_spec': normalized_style, 'asset_manifest': evidence,
        'items': [{key: value for key, value in item.items()
                   if key not in {'status', 'child_run_id', 'error', 'claimed_at', 'completed_at'}}
                  for item in items],
        'aggregate_caps': {
            'image_calls': len(items) * int(image_call_cap),
            'qa_calls': len(items) * int(qa_call_cap),
            'items': len(items), 'concurrency': 1,
        },
    }
    preview_hash = canonical_hash(snapshot)
    phrase = f'确认整批付费 {batch_id[-8:]} {preview_hash[:8]} {len(items)}项'
    now = time.time()
    batch = {
        **snapshot, 'preview_hash': preview_hash,
        'confirmation_phrase_hash': hashlib.sha256(phrase.encode('utf-8')).hexdigest(),
        'status': 'previewed', 'items': items, 'child_run_ids': [],
        'created_at': now, 'updated_at': now, 'committed_at': 0,
        'expires_at': now + BATCH_PREVIEW_TTL_SECONDS,
        'cancel_requested_at': 0, 'error': '',
    }
    return save_variant_batch(batch), phrase


def claim_variant_batch(batch: Mapping[str, Any], *, preview_hash: str,
                        confirmation_phrase: str, current_project_state_hash: str) -> dict:
    value = copy.deepcopy(dict(batch))
    if value.get('status') in {'queued', 'running', *TERMINAL_BATCH_STATUSES}:
        if hmac.compare_digest(str(value.get('preview_hash') or ''), str(preview_hash or '')):
            return value
        raise WholeHomeHistoryError('variant_batch_already_committed', '批次已经提交')
    if value.get('status') != 'previewed':
        raise WholeHomeHistoryError('variant_batch_not_previewed', '批次不在可提交状态')
    if float(value.get('expires_at') or 0) <= time.time():
        value['status'] = 'expired'
        save_variant_batch(value)
        raise WholeHomeHistoryError('variant_preview_expired', '整批预览已过期，请重新预览')
    if not hmac.compare_digest(str(value.get('preview_hash') or ''), str(preview_hash or '')):
        raise WholeHomeHistoryError('variant_preview_hash_mismatch', '整批预览哈希不匹配')
    phrase_hash = hashlib.sha256(str(confirmation_phrase or '').encode('utf-8')).hexdigest()
    if not hmac.compare_digest(str(value.get('confirmation_phrase_hash') or ''), phrase_hash):
        raise WholeHomeHistoryError('variant_confirmation_mismatch', '整批动态确认短语不匹配')
    if not hmac.compare_digest(str(value.get('project_state_hash') or ''),
                               str(current_project_state_hash or '')):
        raise WholeHomeHistoryError('variant_preview_inputs_changed',
                                    '项目、机位或生成草稿已变化，请重新预览')
    value['status'] = 'queued'
    value['committed_at'] = time.time()
    return save_variant_batch(value)


def request_variant_cancel(batch: Mapping[str, Any]) -> dict:
    value = copy.deepcopy(dict(batch))
    if value.get('status') in TERMINAL_BATCH_STATUSES:
        return value
    value['cancel_requested_at'] = time.time()
    for item in value.get('items') or []:
        if item.get('status') == 'pending':
            item['status'] = 'cancelled'
    if not any(item.get('status') in {'claimed', 'running'} for item in value.get('items') or []):
        value['status'] = 'cancelled' if not any(
            item.get('status') == 'done' for item in value.get('items') or []) else 'partial'
    return save_variant_batch(value)


__all__ = [
    'BATCH_PREVIEW_TTL_SECONDS', 'TERMINAL_BATCH_STATUSES', 'WholeHomeHistoryError',
    'batch_counts', 'build_history', 'build_replay_snapshot', 'canonical_hash',
    'claim_variant_batch', 'create_variant_preview', 'ensure_replay_snapshot',
    'list_variant_batches', 'load_replay_snapshot', 'load_variant_batch',
    'prepare_branch_project', 'public_variant_batch', 'replay_capability',
    'request_variant_cancel', 'save_replay_snapshot', 'save_variant_batch',
    'snapshot_project',
    'transient_replay_snapshot', 'validate_replay_snapshot', 'verify_snapshot_assets',
]
