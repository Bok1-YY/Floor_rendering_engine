# -*- coding: utf-8 -*-
"""Whole-home metric model, project persistence, capture storage, and QA helpers.

This is the v2 floor-plan authority.  Unlike the legacy room pipeline, every wall,
opening, room, fixed object, and camera lives in one shared metre-based coordinate
system.  The browser renders that single model and uploads deterministic camera
buffers; image models only consume those approved buffers.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .config import MAIN_OUTPUT_DIR, logger
from .floorplan_engine import call_gemini_json
from .server_helpers import result_thumb_url, to_url
from .whole_home_dev_lock import data_root_lock, durable_atomic_json
from .whole_home_geometry import migrate_legacy_project_geometry


ROOT = os.path.join(MAIN_OUTPUT_DIR, '_whole_home')
PROJECT_DIR = os.path.join(ROOT, 'projects')
RUN_DIR = os.path.join(ROOT, 'runs')
ASSET_DIR = os.path.join(ROOT, 'assets')
REVIEW_DIR = os.path.join(ROOT, 'reviews')
for _path in (ROOT, PROJECT_DIR, RUN_DIR, ASSET_DIR, REVIEW_DIR):
    os.makedirs(_path, exist_ok=True)

_io_lock = threading.RLock()
_ATOMIC_REPLACE_DELAYS = (0.04, 0.08, 0.12, 0.16)


_ROOM_PROFILE_RULES = (
    ('kitchen', ('kitchen', '厨')),
    ('bathroom', ('bathroom', 'toilet', 'washroom', '卫生', '洗手')),
    ('bedroom', ('bedroom', 'masterbed', 'secbed', '卧')),
    ('living_room', ('living_room', 'living', '客厅', '起居')),
    ('foyer', ('foyer', 'entry', 'entrance', '玄关', '门厅')),
    ('balcony', ('balcony', 'terrace', '阳台', '露台')),
)

_ROLE_RULES = (
    ('kitchen_run', ('kitchen_run', 'kitchen counter', 'counter', '橱柜', '操作台', '厨房台面')),
    ('sink', ('sink', '水槽', '洗菜盆')),
    ('hob', ('hob', 'cooktop', 'stove', '灶', '炉')),
    ('fridge', ('fridge', 'refrigerator', '冰箱')),
    ('basin', ('basin', 'vanity', 'wash basin', '洗手盆', '台盆', '浴室柜')),
    ('toilet', ('toilet', 'wc', '马桶', '坐便')),
    ('shower_zone', ('shower_zone', 'shower', '淋浴')),
    ('bed', ('bed', '床')),
    ('wardrobe', ('wardrobe', 'closet', '衣柜')),
    ('sofa', ('sofa', 'couch', '沙发')),
    ('tv', ('television', 'tv', '电视')),
    ('dining_table', ('dining_table', 'dining table', '餐桌')),
    ('entry_storage', ('entry_storage', 'shoe cabinet', '鞋柜', '玄关柜')),
    ('washing_machine', ('washing_machine', 'washing machine', 'washer', 'laundry machine', '洗衣机', '洗衣')),
    ('balcony_rail', ('balcony_rail', 'balcony rail', 'railing', 'rail', 'parapet', '栏杆', '栏板', '女儿墙')),
)

_ROOM_CONTRACTS = {
    'kitchen': {
        'required_role_groups': [['kitchen_run'], ['sink', 'hob', 'fridge']],
        'preferred_roles': ['sink', 'hob', 'fridge'],
        'min_visible_groups': 2,
    },
    'bathroom': {
        'required_role_groups': [['basin'], ['toilet'], ['shower_zone']],
        'preferred_roles': [],
        'min_visible_groups': 2,
    },
    'bedroom': {
        'required_role_groups': [['bed']],
        'preferred_roles': ['wardrobe'],
        'min_visible_groups': 1,
    },
    'living_room': {
        'required_role_groups': [['sofa'], ['tv']],
        'preferred_roles': ['dining_table'],
        'min_visible_groups': 2,
    },
    'foyer': {
        'required_role_groups': [],
        'preferred_roles': ['entry_storage'],
        'min_visible_groups': 0,
    },
    'balcony': {
        'required_role_groups': [],
        'preferred_roles': ['balcony_rail', 'washing_machine'],
        'min_visible_groups': 0,
    },
    'other': {
        'required_role_groups': [],
        'preferred_roles': [],
        'min_visible_groups': 0,
    },
}

_ALLOWED_OBJECT_OVERLAPS = {
    frozenset(('kitchen_run', 'sink')),
    frozenset(('kitchen_run', 'hob')),
}

# The semantic pass only fills the minimum room contract.  If one of these
# roles is already an observed architectural fact, a second inferred copy is
# evidence loss rather than a useful alternative placement.
_SINGLETON_PROXY_ROLES = {
    'kitchen_run', 'sink', 'hob', 'fridge', 'basin', 'toilet',
    'shower_zone', 'bed', 'tv', 'entry_storage', 'balcony_rail', 'washing_machine',
}


def new_id(prefix: str) -> str:
    return f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:10]}'


def _atomic_json_unlocked(path: str, payload: dict) -> None:
    durable_atomic_json(path, payload)


def _atomic_json(path: str, payload: dict) -> None:
    folder = os.path.dirname(os.path.realpath(path))
    namespace = f'json-{os.path.basename(path)}'
    with _io_lock, data_root_lock(folder, namespace):
        _atomic_json_unlocked(path, payload)


def _read_json(path: str) -> Optional[dict]:
    try:
        with _io_lock, open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except Exception as ex:
        logger.warning(f'[整屋建模] 读取状态失败 {path}: {ex}')
        return None


def project_path(project_id: str) -> str:
    return os.path.join(PROJECT_DIR, f'{os.path.basename(project_id)}.json')


def run_path(run_id: str) -> str:
    return os.path.join(RUN_DIR, f'{os.path.basename(run_id)}.json')


def save_project(project: dict) -> None:
    project['updated_at'] = time.time()
    _atomic_json(project_path(project['project_id']), project)


def state_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _cas_update(path: str, mutator: Callable[[dict], dict], *,
                expected_state_hash: str = '') -> tuple[dict, str, str]:
    """Cross-process read/modify/write with optional compare-and-swap hash."""
    folder = os.path.dirname(os.path.realpath(path))
    namespace = f'json-{os.path.basename(path)}'
    with _io_lock, data_root_lock(folder, namespace):
        current = _read_json(path)
        if current is None:
            raise FileNotFoundError(path)
        before_hash = state_hash(current)
        if expected_state_hash and before_hash != expected_state_hash:
            raise RuntimeError(
                f'whole_home_state_conflict: expected {expected_state_hash}, actual {before_hash}')
        updated = mutator(copy.deepcopy(current))
        if not isinstance(updated, dict):
            raise TypeError('whole-home CAS mutator must return a dict')
        updated['updated_at'] = time.time()
        after_hash = state_hash(updated)
        if after_hash != before_hash:
            _atomic_json_unlocked(path, updated)
        return copy.deepcopy(updated), before_hash, after_hash


def cas_update_project(project_id: str, mutator: Callable[[dict], dict], *,
                       expected_state_hash: str = '') -> tuple[dict, str, str]:
    return _cas_update(
        project_path(project_id), mutator, expected_state_hash=expected_state_hash)


def cas_update_run(run_id: str, mutator: Callable[[dict], dict], *,
                   expected_state_hash: str = '') -> tuple[dict, str, str]:
    return _cas_update(run_path(run_id), mutator, expected_state_hash=expected_state_hash)


def load_project(project_id: str) -> Optional[dict]:
    return _read_json(project_path(project_id))


def list_projects(limit: int = 30) -> list[dict]:
    return _list_records(PROJECT_DIR, limit)


def save_reference_camera_proposal(project_id: str, proposal: dict) -> str:
    """Persist a full proposal outside the hot project JSON.

    Reference proposals can contain dozens of audited candidates and rejection
    counters.  Keeping five full copies inside every project made each capture
    rewrite a ~20 MiB JSON file.  The relative storage key keeps every proposal
    permanently while the project stores only an index entry.
    """
    safe_project = _safe_asset_name(project_id, 'project')
    proposal_id = _safe_asset_name(str(proposal.get('proposal_id') or ''), 'proposal')
    folder = os.path.join(ASSET_DIR, safe_project, 'reference_camera_proposals')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{proposal_id}.json')
    _atomic_json(path, copy.deepcopy(proposal))
    return os.path.relpath(path, ASSET_DIR).replace('\\', '/')


def load_reference_camera_proposal(record: dict) -> dict:
    if isinstance(record, dict) and record.get('candidates'):
        return copy.deepcopy(record)
    key = str((record or {}).get('storage_key') or '')
    if not key:
        return {}
    root = os.path.realpath(ASSET_DIR)
    path = os.path.realpath(os.path.join(root, key.replace('/', os.sep)))
    try:
        if os.path.commonpath([root, path]) != root:
            return {}
    except ValueError:
        return {}
    return _read_json(path) or {}


def save_run(run: dict) -> None:
    run['updated_at'] = time.time()
    _atomic_json(run_path(run['run_id']), run)


def review_manifest_path(run_id: str) -> str:
    return os.path.join(REVIEW_DIR, f'{os.path.basename(run_id)}_review_manifest.json')


_MANIFEST_SECRET_KEY = re.compile(
    r'(api[_-]?key|apikey|authorization|credential|password|secret|access[_-]?token|refresh[_-]?token)',
    re.I,
)
_MANIFEST_SECRET_TEXT = (
    (re.compile(r'(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+'), r'\1=***'),
    (re.compile(r'(?i)([?&](?:key|api[_-]?key|token)=)[^&\s]+'), r'\1***'),
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+'), r'\1***'),
    (re.compile(r'AIza[0-9A-Za-z_-]{20,}'), '***'),
)


def _redact_manifest_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_manifest_secrets(item)
            for key, item in value.items() if not _MANIFEST_SECRET_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_redact_manifest_secrets(item) for item in value]
    if isinstance(value, str):
        output = value
        for pattern, replacement in _MANIFEST_SECRET_TEXT:
            output = pattern.sub(replacement, output)
        return output
    return value


def _public_reference_artifacts(value: Any, contract: Optional[dict] = None) -> Any:
    """Remove local reference paths while retaining audited IDs and hashes."""
    reference = contract or (
        value.get('reference_contract_snapshot') if isinstance(value, dict) else {}) or {}
    replacements: dict[str, str] = {}
    for slot in reference.get('slots') or []:
        asset = slot.get('reference_asset') or {}
        local_path = str(asset.get('local_path') or '')
        if local_path:
            replacements[os.path.normcase(os.path.realpath(local_path))] = (
                f"reference-asset:{asset.get('asset_id') or slot.get('slot_id') or 'audited'}")

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            output = {}
            for key, child in item.items():
                if str(key) == 'local_path':
                    continue
                if str(key) in {'public_thumb_url', 'pano_resource'} and isinstance(child, str):
                    output[str(key)] = child.split('?', 1)[0].split('#', 1)[0]
                    continue
                output[str(key)] = clean(child)
            return output
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        # Only absolute filesystem paths can match the audited local reference
        # replacements.  Resolving every ID, prompt word and hash through
        # Windows _getfinalpathname made an eight-result replay spend ~20s on
        # nearly 29,000 strings that could never be paths.
        if isinstance(item, str) and item and replacements and os.path.isabs(item):
            try:
                match = replacements.get(os.path.normcase(os.path.realpath(item)))
            except (OSError, ValueError):
                match = None
            return match or item
        return item

    return clean(copy.deepcopy(value))


def save_review_manifest(run: dict) -> str:
    """Persist an immutable-artifact index for out-of-process development review.

    The manifest intentionally contains paths, prompts/hashes, attempts and QA,
    but never credentials.  It is a derived append-preserving view of the run;
    generated files and failed attempts remain addressable even when no final
    candidate is deliverable.
    """
    path = review_manifest_path(str(run.get('run_id') or 'run'))
    results = []
    for result in run.get('results') or []:
        results.append({
            key: copy.deepcopy(result.get(key))
            for key in (
                'result_id', 'room_id', 'model_key', 'candidate_index', 'status',
                'outcome', 'deliverable', 'selected_attempt_id', 'capture_ids',
                'attempts', 'structure_path', 'api_original_path', 'material_path',
                'corrected_path', 'final_path', 'evaluation', 'trace',
            )
        })
    payload = {
        'schema_version': 1,
        'run_id': run.get('run_id'),
        'project_id': run.get('project_id'),
        'created_at': run.get('created_at'),
        'updated_at': run.get('updated_at'),
        'floorplan_path': run.get('floorplan_path'),
        'floor_path': run.get('floor_path'),
        'style_ref_path': run.get('style_ref_path'),
        'material_mode': run.get('material_mode') or 'floor_sample',
        'scene_recipe_id': run.get('scene_recipe_id') or '',
        'scene_hash': run.get('scene_hash') or '',
        'scene_recipe_snapshot': copy.deepcopy(run.get('scene_recipe_snapshot') or {}),
        'reference_contract_id': run.get('reference_contract_id') or '',
        'reference_contract_snapshot': copy.deepcopy(run.get('reference_contract_snapshot') or {}),
        'reference_asset_snapshots': copy.deepcopy(run.get('reference_asset_snapshots') or []),
        'benchmark_batch_id': run.get('benchmark_batch_id') or '',
        'cad_source_snapshot': copy.deepcopy(run.get('cad_source_snapshot') or {}),
        'cad_import_snapshot': copy.deepcopy(run.get('cad_import_snapshot') or {}),
        'cad_parse_report_snapshot': copy.deepcopy(run.get('cad_parse_report_snapshot') or {}),
        'request_prompt_sha256': run.get('request_prompt_sha256'),
        'input_manifest': copy.deepcopy(run.get('input_manifest') or []),
        'model_snapshot': copy.deepcopy(run.get('model_snapshot') or {}),
        'capture_snapshots': copy.deepcopy(run.get('capture_snapshots') or []),
        'capture_groups': copy.deepcopy(run.get('capture_groups') or []),
        'room_contract_snapshots': copy.deepcopy(run.get('room_contract_snapshots') or []),
        'camera_plan_snapshot': copy.deepcopy(run.get('camera_plan_snapshot') or {}),
        'call_ledger': copy.deepcopy(run.get('call_ledger') or []),
        'results': results,
    }
    _atomic_json(path, _redact_manifest_secrets(
        _public_reference_artifacts(payload, run.get('reference_contract_snapshot') or {})))
    return path


def load_run(run_id: str) -> Optional[dict]:
    return _read_json(run_path(run_id))


def list_runs(limit: int = 30) -> list[dict]:
    return _list_records(RUN_DIR, limit)


def _list_records(folder: str, limit: int) -> list[dict]:
    try:
        paths = [os.path.join(folder, name) for name in os.listdir(folder) if name.endswith('.json')]
        paths.sort(key=os.path.getmtime, reverse=True)
    except OSError:
        return []
    rows = []
    for path in paths[:max(1, min(int(limit), 100))]:
        row = _read_json(path)
        if row:
            rows.append(row)
    return rows


def recover_interrupted_whole_home_state() -> tuple[int, int]:
    projects = runs = 0
    for path in _json_paths(PROJECT_DIR):
        entry = _read_json(path)
        if entry and entry.get('status') in ('queued', 'analyzing'):
            entry.update(status='failed', stage='', error='服务重启中断了整屋识别，请重新创建项目')
            _atomic_json(path, entry)
            projects += 1
    for path in _json_paths(RUN_DIR):
        entry = _read_json(path)
        if entry and entry.get('status') in ('queued', 'running', 'evaluating'):
            has_result = run_has_viewable_artifact(entry)
            entry.update(status='partial' if has_result else 'failed', stage='', error='服务重启中断了生成任务')
            _atomic_json(path, entry)
            runs += 1
    return projects, runs


def run_has_viewable_artifact(run: dict) -> bool:
    """Nested attempts are evidence even when no candidate became deliverable."""
    for result in run.get('results') or []:
        if any(result.get(key) for key in (
                'path', 'final_path', 'corrected_path', 'material_path', 'api_original_path')):
            return True
        for attempt in result.get('attempts') or []:
            if attempt.get('structure_path'):
                return True
            for material in attempt.get('material_attempts') or []:
                if any(material.get(key) for key in (
                        'final_path', 'corrected_path', 'material_path', 'api_original_path')):
                    return True
    return False


def _json_paths(folder: str) -> list[str]:
    try:
        return [os.path.join(folder, name) for name in os.listdir(folder) if name.endswith('.json')]
    except OSError:
        return []


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError):
        return fallback


def _clamp(value: Any, low: float, high: float, fallback: float = 0.0) -> float:
    return round(max(low, min(high, _number(value, fallback))), 5)


def _canonical_room_profile(room: dict) -> str:
    raw = f"{room.get('room_type') or ''} {room.get('label') or ''}".lower()
    for profile, tokens in _ROOM_PROFILE_RULES:
        if any(token in raw for token in tokens):
            return profile
    return 'other'


def _canonical_object_role(row: dict, room_profile: str = '') -> str:
    explicit = str(row.get('semantic_role') or '').strip().lower()
    name_raw = str(row.get('name') or '').lower()
    raw = f"{row.get('kind') or ''} {name_raw}".lower()
    profile = str(room_profile or row.get('room_profile') or '').strip().lower()
    if profile == 'bathroom':
        basin_evidence = (
            'sink', 'basin', 'vanity', 'wash basin', 'bathroom sink',
            '洗手盆', '洗面盆', '台盆', '浴室柜',
        )
        if explicit in ('sink', 'basin') or any(token in raw for token in basin_evidence):
            return 'basin'
    washer_evidence = ('washing machine', 'washer', 'laundry machine', '洗衣机', '洗衣')
    rail_evidence = ('balcony_rail', 'balcony rail', 'railing', 'rail', 'parapet', '栏杆', '栏板', '女儿墙')
    if any(token in name_raw for token in washer_evidence):
        return 'washing_machine'
    if explicit == 'balcony_rail' and not any(token in name_raw for token in rail_evidence):
        explicit = ''
    if explicit:
        for role, tokens in _ROLE_RULES:
            if explicit == role or explicit in tokens:
                return role
    for role, tokens in _ROLE_RULES:
        if role == 'balcony_rail':
            # kind/explicit are vision guesses and may conflict with the
            # stronger object name (the real project labelled a washer rail).
            if any(token in name_raw for token in tokens):
                return role
            continue
        if any(token in raw for token in tokens):
            return role
    return re.sub(r'[^a-z0-9_]+', '_', explicit or str(row.get('kind') or 'other').lower()).strip('_')[:80] or 'other'


def _singleton_role_key(role: str, room_profile: str) -> str:
    if room_profile == 'bathroom' and role in ('sink', 'basin'):
        return 'basin'
    return role


def _room_contracts(rooms: list[dict]) -> list[dict]:
    contracts = []
    for room in rooms:
        profile = str(room.get('semantic_profile') or _canonical_room_profile(room))
        if profile not in _ROOM_CONTRACTS:
            profile = 'other'
        template = _ROOM_CONTRACTS[profile]
        contracts.append({
            'room_id': room.get('id'), 'profile': profile,
            'required_role_groups': copy.deepcopy(template['required_role_groups']),
            'preferred_roles': list(template['preferred_roles']),
            'min_visible_groups': int(template['min_visible_groups']),
            'source': 'system-v1', 'status': 'pending', 'assumptions': [],
        })
    return contracts


def _rotated_footprint(row: dict) -> list[dict]:
    position = row.get('position') if isinstance(row.get('position'), dict) else {}
    size = row.get('size') if isinstance(row.get('size'), dict) else {}
    half_x = max(.05, _number(size.get('x'), 1) / 2)
    half_z = max(.05, _number(size.get('z'), 1) / 2)
    angle = math.radians(_number(row.get('rotation_y_deg')))
    cosine, sine = math.cos(angle), math.sin(angle)
    points = []
    for local_x, local_z in ((-half_x, -half_z), (half_x, -half_z), (half_x, half_z), (-half_x, half_z)):
        points.append({
            'x': _number(position.get('x')) + local_x * cosine - local_z * sine,
            'z': _number(position.get('z')) + local_x * sine + local_z * cosine,
        })
    return points


def _aabb(points: list[dict]) -> tuple[float, float, float, float]:
    return (
        min(_number(point.get('x')) for point in points),
        min(_number(point.get('z')) for point in points),
        max(_number(point.get('x')) for point in points),
        max(_number(point.get('z')) for point in points),
    )


def _aabb_overlap_ratio(left: list[dict], right: list[dict]) -> float:
    l_min_x, l_min_z, l_max_x, l_max_z = _aabb(left)
    r_min_x, r_min_z, r_max_x, r_max_z = _aabb(right)
    overlap_x = max(0.0, min(l_max_x, r_max_x) - max(l_min_x, r_min_x))
    overlap_z = max(0.0, min(l_max_z, r_max_z) - max(l_min_z, r_min_z))
    overlap = overlap_x * overlap_z
    left_area = max(.001, (l_max_x - l_min_x) * (l_max_z - l_min_z))
    right_area = max(.001, (r_max_x - r_min_x) * (r_max_z - r_min_z))
    return overlap / min(left_area, right_area)


def _point_within_polygon_tolerance(point: dict, polygon: list[dict], tolerance: float) -> bool:
    if _point_in_polygon(point, polygon):
        return True
    if tolerance <= 0 or len(polygon) < 2:
        return False
    return any(
        _point_segment(point, polygon[index], polygon[(index + 1) % len(polygon)])[0] <= tolerance
        for index in range(len(polygon))
    )


def _metric_point(value: Any, width_m: float, depth_m: float, *, normalized: bool = False) -> dict:
    value = value if isinstance(value, dict) else {}
    x = _number(value.get('x'), 0.0)
    z = _number(value.get('z', value.get('y')), 0.0)
    if normalized:
        x, z = x * width_m, z * depth_m
    return {'x': _clamp(x, 0, width_m), 'z': _clamp(z, 0, depth_m)}


def _normalized_point(value: Any) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    x = _number(value.get('x'), -1)
    z = _number(value.get('z', value.get('y')), -1)
    if x > 1.0 or z > 1.0:
        x, z = x / 1000.0, z / 1000.0
    if x < 0 or z < 0:
        return None
    return {'x': _clamp(x, 0, 1), 'z': _clamp(z, 0, 1)}


def _distance(a: dict, b: dict) -> float:
    return math.hypot(_number(a.get('x')) - _number(b.get('x')), _number(a.get('z')) - _number(b.get('z')))


def _polygon_area(points: list[dict]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(sum(
        _number(points[index].get('x')) * _number(points[(index + 1) % len(points)].get('z'))
        - _number(points[(index + 1) % len(points)].get('x')) * _number(points[index].get('z'))
        for index in range(len(points))
    )) / 2


def _point_in_polygon(point: dict, polygon: list[dict]) -> bool:
    inside = False
    x, z = _number(point.get('x')), _number(point.get('z'))
    previous = len(polygon) - 1
    for current in range(len(polygon)):
        a, b = polygon[current], polygon[previous]
        ax, az, bx, bz = _number(a.get('x')), _number(a.get('z')), _number(b.get('x')), _number(b.get('z'))
        if (az > z) != (bz > z):
            crossing_x = (bx - ax) * (z - az) / max(abs(bz - az), 1e-12) * (1 if bz >= az else -1) + ax
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _wall_separates_rooms(wall: dict, rooms: list[dict]) -> bool:
    start, end = wall.get('start') or {}, wall.get('end') or {}
    dx, dz = _number(end.get('x')) - _number(start.get('x')), _number(end.get('z')) - _number(start.get('z'))
    wall_length = math.hypot(dx, dz)
    if wall_length < .1:
        return False
    midpoint = {'x': (_number(start.get('x')) + _number(end.get('x'))) / 2,
                'z': (_number(start.get('z')) + _number(end.get('z'))) / 2}
    distance = max(.12, _number(wall.get('thickness_m'), .12))
    normal = {'x': -dz / wall_length * distance, 'z': dx / wall_length * distance}
    left = {'x': midpoint['x'] + normal['x'], 'z': midpoint['z'] + normal['z']}
    right = {'x': midpoint['x'] - normal['x'], 'z': midpoint['z'] - normal['z']}
    has_left = any(_point_in_polygon(left, room.get('polygon') or []) for room in rooms)
    has_right = any(_point_in_polygon(right, room.get('polygon') or []) for room in rooms)
    return has_left and has_right


def _point_segment(point: dict, start: dict, end: dict) -> tuple[float, float]:
    dx, dz = end['x'] - start['x'], end['z'] - start['z']
    length_sq = max(dx * dx + dz * dz, 1e-9)
    t = max(0.0, min(1.0, ((point['x'] - start['x']) * dx + (point['z'] - start['z']) * dz) / length_sq))
    projected = {'x': start['x'] + t * dx, 'z': start['z'] + t * dz}
    return _distance(point, projected), t


def _footprint_within_polygon(points: list[dict], polygon: list[dict], tolerance: float) -> bool:
    """Check rectangle edges as well as corners against a possibly concave room."""
    if len(points) < 4 or len(polygon) < 3:
        return False
    samples = list(points)
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        edge_length = _distance(start, end)
        divisions = max(1, int(math.ceil(edge_length / .08)))
        for step in range(1, divisions):
            ratio = step / divisions
            samples.append({
                'x': _number(start.get('x')) + (_number(end.get('x')) - _number(start.get('x'))) * ratio,
                'z': _number(start.get('z')) + (_number(end.get('z')) - _number(start.get('z'))) * ratio,
            })
    samples.append({
        'x': sum(_number(point.get('x')) for point in points) / len(points),
        'z': sum(_number(point.get('z')) for point in points) / len(points),
    })
    return all(_point_within_polygon_tolerance(point, polygon, tolerance) for point in samples)


def _footprint_at_position(row: dict, position: dict) -> list[dict]:
    candidate = copy.deepcopy(row)
    candidate['position'] = {
        'x': _number(position.get('x')), 'y': _number((row.get('position') or {}).get('y')),
        'z': _number(position.get('z')),
    }
    return _rotated_footprint(candidate)


def _axis_samples(low: float, high: float, anchor: float, step_m: float) -> list[float]:
    if high < low:
        return []
    span = high - low
    divisions = max(1, int(math.ceil(span / max(.01, step_m))))
    values = {round(low, 6), round(high, 6), round(max(low, min(high, anchor)), 6)}
    for index in range(divisions + 1):
        values.add(round(low + span * index / divisions, 6))
    return sorted(values)


def _minimal_room_translation(row: dict, polygon: list[dict]) -> tuple[Optional[dict], dict]:
    """Find a small deterministic translation that keeps the full footprint indoors.

    The axis-aligned clamp is the exact minimum for rectangular rooms.  A bounded
    grid plus local refinement handles ordinary L-shaped rooms without pretending
    that an oversized object fits a narrow or non-convex polygon.
    """
    original = row.get('position') if isinstance(row.get('position'), dict) else {}
    origin = {'x': _number(original.get('x')), 'z': _number(original.get('z'))}
    if _footprint_within_polygon(_rotated_footprint(row), polygon, .005):
        return origin, {'status': 'not_needed'}

    if len(polygon) < 3:
        return None, {'status': 'failed', 'reason': 'invalid_room_polygon'}
    room_min_x, room_min_z, room_max_x, room_max_z = _aabb(polygon)
    footprint = _rotated_footprint({**row, 'position': {'x': 0, 'y': 0, 'z': 0}})
    object_min_x, object_min_z, object_max_x, object_max_z = _aabb(footprint)
    low_x, high_x = room_min_x - object_min_x, room_max_x - object_max_x
    low_z, high_z = room_min_z - object_min_z, room_max_z - object_max_z
    if high_x < low_x or high_z < low_z:
        return None, {'status': 'failed', 'reason': 'object_larger_than_room_bounds'}

    span_x, span_z = high_x - low_x, high_z - low_z
    coarse_step = max(.025, max(span_x, span_z) / 80)
    xs = _axis_samples(low_x, high_x, origin['x'], coarse_step)
    zs = _axis_samples(low_z, high_z, origin['z'], coarse_step)
    candidates = sorted(
        ({'x': x, 'z': z} for x in xs for z in zs),
        key=lambda point: (round(_distance(point, origin), 8), point['x'], point['z']),
    )
    best = next((
        point for point in candidates
        if _footprint_within_polygon(_footprint_at_position(row, point), polygon, .005)
    ), None)
    if best is None:
        return None, {'status': 'failed', 'reason': 'no_feasible_translation_in_room_polygon'}

    resolution = coarse_step
    for resolution in (max(.01, coarse_step / 4), .005, .001):
        offsets = range(-4, 5)
        refinements = sorted((
            {'x': max(low_x, min(high_x, best['x'] + dx * resolution)),
             'z': max(low_z, min(high_z, best['z'] + dz * resolution))}
            for dx in offsets for dz in offsets
        ), key=lambda point: (round(_distance(point, origin), 8), point['x'], point['z']))
        refined = next((
            point for point in refinements
            if _footprint_within_polygon(_footprint_at_position(row, point), polygon, .005)
        ), None)
        if refined is not None and _distance(refined, origin) <= _distance(best, origin) + 1e-9:
            best = refined

    best = {'x': round(best['x'], 5), 'z': round(best['z'], 5)}
    distance_m = _distance(best, origin)
    size = row.get('size') if isinstance(row.get('size'), dict) else {}
    max_translation_m = max(.75, min(1.0, max(_number(size.get('x')), _number(size.get('z'))) / 2))
    if distance_m > max_translation_m + .001:
        return None, {
            'status': 'failed', 'reason': 'translation_exceeds_safe_limit',
            'candidate_distance_m': round(distance_m, 5),
            'max_translation_m': round(max_translation_m, 5),
        }
    return best, {
        'status': 'translated', 'distance_m': round(distance_m, 5),
        'translation_m': {
            'x': round(best['x'] - origin['x'], 5),
            'z': round(best['z'] - origin['z'], 5),
        },
        'resolution_m': round(resolution, 5),
        'max_translation_m': round(max_translation_m, 5),
    }


def repair_ai_observed_architecture(model: dict) -> dict:
    """Repair only AI topology facts, preserving evidence and human coordinates."""
    value = upgrade_model_v2(model)
    rooms = {str(room.get('id') or ''): room for room in value.get('rooms') or []}
    notices = value.setdefault('uncertainties', [])
    for row in value.get('fixed_objects') or []:
        if (row.get('source') != 'ai' or row.get('purpose') != 'observed_architecture'
                or not row.get('observed', True)):
            continue
        room_id = str(row.get('room_id') or '')
        room = rooms.get(room_id)
        if not room:
            continue
        polygon = room.get('polygon') or []
        if _footprint_within_polygon(_rotated_footprint(row), polygon, .005):
            continue
        original = copy.deepcopy(row.get('original_position') or row.get('position') or {})
        repaired, evidence = _minimal_room_translation(row, polygon)
        row['original_position'] = {
            'x': round(_number(original.get('x')), 5),
            'y': round(_number(original.get('y')), 5),
            'z': round(_number(original.get('z')), 5),
        }
        evidence.update({
            'method': 'deterministic_room_projection_v1',
            'trigger': 'observed_architecture_outside_bound_room',
            'room_id': room_id,
        })
        if repaired is not None:
            row['position'] = {
                'x': repaired['x'], 'y': _number((row.get('position') or {}).get('y')),
                'z': repaired['z'],
            }
            evidence['result_position'] = copy.deepcopy(row['position'])
            message = (
                f"本地几何规则将 AI 识别物体 {row.get('name') or row.get('id')} "
                f"最小平移 {evidence.get('distance_m', 0):.3f}m 到绑定房间内；原坐标保留在 original_position"
            )
        else:
            message = (
                f"AI 识别物体 {row.get('name') or row.get('id')} 无法安全投影到绑定房间内："
                f"{evidence.get('reason') or 'unknown'}；保留原坐标并阻断自动锁定"
            )
        row['geometry_repair'] = evidence
        if message not in notices:
            notices.append(message)
    return upgrade_model_v2(value)


def _nearest_wall(point: dict, walls: list[dict]) -> tuple[Optional[dict], float, float]:
    best = (None, float('inf'), 0.0)
    for wall in walls:
        distance, t = _point_segment(point, wall['start'], wall['end'])
        if distance < best[1]:
            best = (wall, distance, t)
    return best


def _edge_uncovered_intervals(start: dict, end: dict, walls: list[dict], *, tolerance: float = 0.18) -> list[tuple[float, float]]:
    """Return metre intervals of a room edge that are not backed by a wall.

    Room polygons and wall centre lines are produced independently by the vision
    model, so a small half-wall offset is expected.  We therefore compare
    parallel projected intervals instead of requiring identical coordinates.
    """
    dx, dz = end['x'] - start['x'], end['z'] - start['z']
    edge_length = math.hypot(dx, dz)
    if edge_length < 0.05:
        return []
    ux, uz = dx / edge_length, dz / edge_length
    covered: list[tuple[float, float]] = []
    for wall in walls:
        wall_start, wall_end = wall.get('start') or {}, wall.get('end') or {}
        wx = _number(wall_end.get('x')) - _number(wall_start.get('x'))
        wz = _number(wall_end.get('z')) - _number(wall_start.get('z'))
        wall_length = math.hypot(wx, wz)
        if wall_length < 0.05 or abs((wx * ux + wz * uz) / wall_length) < 0.985:
            continue
        perpendicular = max(
            abs((_number(wall_start.get('x')) - start['x']) * uz - (_number(wall_start.get('z')) - start['z']) * ux),
            abs((_number(wall_end.get('x')) - start['x']) * uz - (_number(wall_end.get('z')) - start['z']) * ux),
        )
        allowed = tolerance + _number(wall.get('thickness_m'), 0.12) / 2
        if perpendicular > allowed:
            continue
        first = (_number(wall_start.get('x')) - start['x']) * ux + (_number(wall_start.get('z')) - start['z']) * uz
        second = (_number(wall_end.get('x')) - start['x']) * ux + (_number(wall_end.get('z')) - start['z']) * uz
        low, high = max(0.0, min(first, second)), min(edge_length, max(first, second))
        if high - low > 0.03:
            covered.append((low, high))
    if not covered:
        return [(0.0, edge_length)]
    covered.sort()
    merged: list[list[float]] = []
    for low, high in covered:
        if not merged or low > merged[-1][1] + tolerance:
            merged.append([low, high])
        else:
            merged[-1][1] = max(merged[-1][1], high)
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for low, high in merged:
        if low - cursor > tolerance:
            gaps.append((cursor, low))
        cursor = max(cursor, high)
    if edge_length - cursor > tolerance:
        gaps.append((cursor, edge_length))
    return gaps


def _point_on_other_wall(point: dict, wall_id: str, walls: list[dict], *, kind: Optional[str] = None,
                         tolerance: float = 0.12) -> bool:
    for other in walls:
        if other.get('id') == wall_id or (kind and other.get('kind') != kind):
            continue
        distance, _ = _point_segment(point, other.get('start') or {}, other.get('end') or {})
        if distance <= tolerance:
            return True
    return False


def _point_along(start: dict, end: dict, distance: float) -> dict:
    edge_length = max(_distance(start, end), 1e-9)
    ratio = max(0.0, min(1.0, distance / edge_length))
    return {
        'x': round(start['x'] + (end['x'] - start['x']) * ratio, 5),
        'z': round(start['z'] + (end['z'] - start['z']) * ratio, 5),
    }


def plan_alignment_score(model: dict, floorplan_path: str) -> float:
    """Score how closely proposed wall centre lines follow dark plan ink.

    The score is deliberately only a candidate-ranking signal.  A wall
    centreline often runs through the white middle of a double-line wall, so
    each sample searches by half the proposed wall thickness plus a few pixels.
    The sample set is deliberately independent of opening candidates, otherwise
    adding more doors/windows could artificially improve the wall score.
    """
    try:
        with Image.open(floorplan_path) as source:
            gray = ImageOps.grayscale(source).copy()
    except Exception:
        return 0.0
    image_width, image_height = gray.size
    if image_width < 10 or image_height < 10:
        return 0.0
    pixels = gray.load()
    width_m = max(_number(model.get('width_m'), 1), 0.1)
    depth_m = max(_number(model.get('depth_m'), 1), 0.1)
    total = matched = 0.0
    for wall in model.get('walls') or []:
        start, end = wall.get('start') or {}, wall.get('end') or {}
        wall_length = _distance(start, end)
        if wall_length < 0.05:
            continue
        x1, y1 = _number(start.get('x')) / width_m * (image_width - 1), _number(start.get('z')) / depth_m * (image_height - 1)
        x2, y2 = _number(end.get('x')) / width_m * (image_width - 1), _number(end.get('z')) / depth_m * (image_height - 1)
        pixel_length = math.hypot(x2 - x1, y2 - y1)
        samples = max(6, min(180, int(pixel_length / 7)))
        horizontal = abs(x2 - x1) >= abs(y2 - y1)
        thickness_pixels = _number(wall.get('thickness_m'), .12) / (depth_m if horizontal else width_m) * (image_height if horizontal else image_width)
        radius = max(5, min(22, int(thickness_pixels / 2 + 5)))
        for index in range(samples + 1):
            ratio = index / samples
            px, py = int(round(x1 + (x2 - x1) * ratio)), int(round(y1 + (y2 - y1) * ratio))
            nearest = radius + 1
            for dy in range(-radius, radius + 1):
                yy = py + dy
                if yy < 0 or yy >= image_height:
                    continue
                for dx in range(-radius, radius + 1):
                    xx = px + dx
                    if xx < 0 or xx >= image_width or dx * dx + dy * dy >= nearest * nearest:
                        continue
                    if pixels[xx, yy] < 105:
                        nearest = int(math.ceil(math.hypot(dx, dy)))
            total += 1
            if nearest <= radius:
                matched += max(0.0, 1.0 - nearest / (radius + 1))
    return round(100 * matched / total, 2) if total else 0.0


def _render_topology_audit_overlay(model: dict, floorplan_path: str) -> str:
    with Image.open(floorplan_path) as source:
        image = source.convert('RGBA')
    draw = ImageDraw.Draw(image, 'RGBA')
    image_width, image_height = image.size
    width_m = max(_number(model.get('width_m'), 1), .1)
    depth_m = max(_number(model.get('depth_m'), 1), .1)

    def pixel(point: dict) -> tuple[int, int]:
        return (
            int(round(_number(point.get('x')) / width_m * (image_width - 1))),
            int(round(_number(point.get('z')) / depth_m * (image_height - 1))),
        )

    line_width = max(3, min(image_width, image_height) // 170)
    for room in model.get('rooms') or []:
        points = [pixel(point) for point in room.get('polygon') or []]
        if len(points) >= 3:
            draw.line(points + [points[0]], fill=(245, 158, 11, 130), width=max(2, line_width // 2), joint='curve')
    for wall in model.get('walls') or []:
        color = (220, 38, 38, 210) if wall.get('kind') == 'exterior' else (37, 99, 235, 210)
        start, end = pixel(wall.get('start') or {}), pixel(wall.get('end') or {})
        draw.line([start, end], fill=color, width=line_width)
        radius = line_width + 2
        for x, y in (start, end):
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(255, 255, 255, 230), outline=color, width=max(2, line_width // 2))
        midpoint_x, midpoint_y = (start[0] + end[0]) // 2, (start[1] + end[1]) // 2
        draw.text((midpoint_x + 3, midpoint_y + 3), str(wall.get('id') or '')[:32], fill=(15, 23, 42, 255), stroke_width=2, stroke_fill=(255, 255, 255, 230))
    for opening in model.get('openings') or []:
        wall = next((item for item in model.get('walls') or [] if item.get('id') == opening.get('wall_id')), None)
        if not wall or opening.get('review_status') == 'rejected':
            continue
        wall_length = max(_distance(wall['start'], wall['end']), 1e-9)
        first = _point_along(wall['start'], wall['end'], _number(opening.get('offset_m')))
        second = _point_along(wall['start'], wall['end'], _number(opening.get('offset_m')) + _number(opening.get('width_m')))
        draw.line([pixel(first), pixel(second)], fill=(22, 163, 74, 255), width=line_width + 3)
    folder = os.path.join(ASSET_DIR, '_topology_audits')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'audit_{uuid.uuid4().hex}.png')
    image.convert('RGB').save(path, 'PNG')
    return path


def _render_semantic_layout_overlay(model: dict, floorplan_path: str) -> str:
    """Render semantic footprints over the already audited shell for one repair pass."""
    path = _render_topology_audit_overlay(model, floorplan_path)
    with Image.open(path) as source:
        image = source.convert('RGBA')
    draw = ImageDraw.Draw(image, 'RGBA')
    width_m = max(_number(model.get('width_m'), 1), .1)
    depth_m = max(_number(model.get('depth_m'), 1), .1)
    image_width, image_height = image.size

    def pixel(point: dict) -> tuple[int, int]:
        return (
            int(round(_number(point.get('x')) / width_m * (image_width - 1))),
            int(round(_number(point.get('z')) / depth_m * (image_height - 1))),
        )

    line_width = max(2, min(image_width, image_height) // 220)
    for row in model.get('fixed_objects') or []:
        if row.get('review_status') == 'rejected':
            continue
        points = [pixel(point) for point in _rotated_footprint(row)]
        if len(points) != 4:
            continue
        role = _canonical_object_role(row)
        color = (124, 58, 237, 190) if row.get('purpose') == 'layout_proxy' else (2, 132, 199, 210)
        draw.polygon(points, fill=(*color[:3], 45), outline=color)
        draw.line(points + [points[0]], fill=color, width=line_width, joint='curve')
        label_at = pixel(row.get('position') or {})
        draw.text((label_at[0] + 3, label_at[1] + 3), role[:28], fill=(15, 23, 42, 255),
                  stroke_width=2, stroke_fill=(255, 255, 255, 230))
    image.convert('RGB').save(path, 'PNG')
    return path


def infer_camera_room_id(model: dict, camera: dict) -> str:
    explicit = str(camera.get('room_id') or '')
    if any(str(room.get('id') or '') == explicit for room in model.get('rooms') or []):
        return explicit
    position = camera.get('position') if isinstance(camera.get('position'), dict) else {}
    room = next((
        row for row in model.get('rooms') or []
        if _point_in_polygon(position, row.get('polygon') or [])
    ), None)
    return str((room or {}).get('id') or '')


def save_camera_plan_overlay(project_id: str, capture_id: str, floorplan_path: str,
                             model: dict, camera: dict) -> str:
    """Persist a floor-plan audit image with the active room and immutable camera arrow."""
    with Image.open(floorplan_path) as source:
        image = source.convert('RGBA')
    draw = ImageDraw.Draw(image, 'RGBA')
    image_width, image_height = image.size
    width_m = max(_number(model.get('width_m'), 1), .1)
    depth_m = max(_number(model.get('depth_m'), 1), .1)

    def pixel(point: dict) -> tuple[int, int]:
        return (
            int(round(_number(point.get('x')) / width_m * (image_width - 1))),
            int(round(_number(point.get('z')) / depth_m * (image_height - 1))),
        )

    room_id = infer_camera_room_id(model, camera)
    room = next((row for row in model.get('rooms') or [] if str(row.get('id') or '') == room_id), None)
    if room:
        points = [pixel(point) for point in room.get('polygon') or []]
        if len(points) >= 3:
            draw.polygon(points, fill=(250, 204, 21, 70), outline=(202, 138, 4, 230))
            draw.line(points + [points[0]], fill=(202, 138, 4, 230), width=max(3, min(image_width, image_height) // 180), joint='curve')
    start = pixel(camera.get('position') or {})
    end = pixel(camera.get('target') or {})
    line_width = max(4, min(image_width, image_height) // 140)
    draw.line([start, end], fill=(220, 38, 38, 245), width=line_width)
    radius = line_width * 2
    draw.ellipse([start[0] - radius, start[1] - radius, start[0] + radius, start[1] + radius],
                 fill=(220, 38, 38, 245), outline=(255, 255, 255, 255), width=max(2, line_width // 2))
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    arrow = max(12, line_width * 4)
    left = (int(end[0] - arrow * math.cos(angle - .55)), int(end[1] - arrow * math.sin(angle - .55)))
    right = (int(end[0] - arrow * math.cos(angle + .55)), int(end[1] - arrow * math.sin(angle + .55)))
    draw.polygon([end, left, right], fill=(220, 38, 38, 245))
    scope = str(camera.get('origin_scope') or 'inside_room')
    portal = str(camera.get('portal_opening_id') or '')
    entry = str(camera.get('entry_opening_id') or '')
    label = (
        f"{(room or {}).get('label') or room_id or 'room'} | "
        f"{camera.get('candidate_id') or camera.get('id') or 'camera'} | {scope}"
        f"{(' via ' + portal) if portal else ((' inside ' + entry) if entry else '')}"
    )
    draw.text((start[0] + radius + 4, start[1] + 4), label[:80], fill=(15, 23, 42, 255),
              stroke_width=2, stroke_fill=(255, 255, 255, 230))
    folder = os.path.join(ASSET_DIR, _safe_asset_name(project_id, 'project'), 'captures')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{_safe_asset_name(capture_id, "capture")}_plan.png')
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    image.convert('RGB').save(temporary, 'PNG')
    os.replace(temporary, path)
    return path


def _unique_id(raw: Any, prefix: str, seen: set[str]) -> str:
    base = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(raw or '')).strip('_')[:70] or f'{prefix}_{len(seen) + 1}'
    value = base
    index = 2
    while value in seen:
        value = f'{base}_{index}'
        index += 1
    seen.add(value)
    return value


def _semantic_door_clearances(model: dict) -> list[dict]:
    walls = {str(wall.get('id') or ''): wall for wall in model.get('walls') or []}
    clearances = []
    for opening in model.get('openings') or []:
        if opening.get('kind') != 'door' or opening.get('review_status') == 'rejected':
            continue
        wall = walls.get(str(opening.get('wall_id') or ''))
        if not wall:
            continue
        clearances.append({
            'opening_id': opening.get('id'),
            'center': _point_along(
                wall.get('start') or {}, wall.get('end') or {},
                _number(opening.get('offset_m')) + _number(opening.get('width_m')) / 2,
            ),
        })
    return clearances


def _blocking_door_id(row: dict, position: dict, clearances: list[dict]) -> str:
    size = row.get('size') if isinstance(row.get('size'), dict) else {}
    threshold = max(
        .35,
        min(_number(size.get('x'), 1), _number(size.get('z'), 1)) / 2
        + _number(row.get('clearance_m'), .25),
    )
    conflict = next((
        item for item in clearances
        if _distance(item.get('center') or {}, position) < threshold
    ), None)
    return str((conflict or {}).get('opening_id') or '')


def validate_semantic_layout(model: dict) -> dict:
    """Validate room-function contracts without trusting the layout model's prose."""
    hard_errors: list[dict] = []
    warnings: list[dict] = []
    rooms = model.get('rooms') or []
    room_map = {str(room.get('id') or ''): room for room in rooms}
    contracts = model.get('room_contracts') or _room_contracts(rooms)
    active_objects = [
        row for row in model.get('fixed_objects') or []
        if isinstance(row, dict) and row.get('review_status') != 'rejected'
    ]
    cad_roles_are_generation_targets = bool(
        model.get('cad_facts_hash')
        and str(model.get('input_grade') or '') == 'vector_authoritative'
        and str((model.get('space_confirmation') or {}).get('status') or '') == 'confirmed'
    )
    objects_by_room: dict[str, list[dict]] = {}
    for row in active_objects:
        room_id = str(row.get('room_id') or '')
        room = room_map.get(room_id)
        if not room:
            hard_errors.append({
                'code': 'semantic_object_orphan_room', 'object_id': row.get('id'), 'room_id': room_id,
                'message': f"语义物体 {row.get('name') or row.get('id')} 未绑定有效房间",
            })
            continue
        footprint = _rotated_footprint(row)
        if row.get('purpose') == 'layout_proxy' and row.get('source') in ('ai', 'ai_edited'):
            # Gemini's normalized coordinates are quantized; tolerate centimetre
            # rounding, but not a visibly misplaced proxy.
            tolerance = .05
        elif row.get('observed') and row.get('source') != 'ai':
            # Preserve the existing hand/import tolerance.  These objects are
            # never auto-moved, and visibly larger overruns still remain hard.
            tolerance = .15
        else:
            tolerance = .02 if row.get('observed') else .03
        if not _footprint_within_polygon(footprint, room.get('polygon') or [], tolerance):
            hard_errors.append({
                'code': 'semantic_object_outside_room', 'object_id': row.get('id'), 'room_id': room_id,
                'message': f"{room.get('label') or room_id} 的 {row.get('name') or row.get('id')} 占地越出房间边界",
            })
        if row.get('review_status') == 'pending':
            warnings.append({
                'code': 'semantic_object_pending', 'object_id': row.get('id'), 'room_id': room_id,
                'message': f"{room.get('label') or room_id} 的 {row.get('name') or row.get('id')} 仍待人工确认",
            })
        objects_by_room.setdefault(room_id, []).append(row)

    for contract in contracts:
        room_id = str(contract.get('room_id') or '')
        room = room_map.get(room_id)
        if not room or not room.get('selected', True):
            continue
        profile = str(room.get('semantic_profile') or _canonical_room_profile(room))
        roles = {
            _singleton_role_key(_canonical_object_role(row, profile), profile)
            for row in objects_by_room.get(room_id, [])
        }
        for group_index, raw_group in enumerate(contract.get('required_role_groups') or []):
            group = [
                _singleton_role_key(str(role), profile) for role in raw_group if role
            ]
            if group and not roles.intersection(group):
                issue = {
                    'code': 'semantic_required_role_missing', 'room_id': room_id,
                    'role_group': group, 'group_index': group_index,
                    'message': f"{room.get('label') or room_id} 缺少必需语义角色：{' / '.join(group)}",
                }
                if cad_roles_are_generation_targets:
                    warnings.append({
                        **issue,
                        'code': 'cad_generation_role_unobserved',
                        'message': (
                            f"{room.get('label') or room_id} 的 {' / '.join(group)} 未从结构 CAD "
                            '中作为权威家具提取；已记为效果图生成目标'
                        ),
                    })
                else:
                    hard_errors.append(issue)

    for room_id, rows in objects_by_room.items():
        room = room_map.get(room_id) or {}
        profile = str(room.get('semantic_profile') or _canonical_room_profile(room))
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                roles = frozenset((
                    _canonical_object_role(left, profile),
                    _canonical_object_role(right, profile),
                ))
                if roles in _ALLOWED_OBJECT_OVERLAPS:
                    continue
                if _aabb_overlap_ratio(_rotated_footprint(left), _rotated_footprint(right)) > .45:
                    hard_errors.append({
                        'code': 'semantic_object_overlap', 'room_id': room_id,
                        'object_id': left.get('id'), 'other_object_id': right.get('id'),
                        'message': f"语义物体 {left.get('name') or left.get('id')} 与 {right.get('name') or right.get('id')} 严重重叠",
                    })

    door_clearances = _semantic_door_clearances(model)
    for row in active_objects:
        for clearance in door_clearances:
            opening_id = _blocking_door_id(row, row.get('position') or {}, [clearance])
            if opening_id:
                hard_errors.append({
                    'code': 'semantic_object_blocks_door', 'object_id': row.get('id'),
                    'opening_id': opening_id, 'room_id': row.get('room_id'),
                    'message': f"语义物体 {row.get('name') or row.get('id')} 占用了门洞通行净空",
                })

    return {
        'status': 'complete' if not hard_errors else 'needs_review',
        'hard_errors': hard_errors, 'warnings': warnings, 'checked_at': time.time(),
    }


def _semantic_proxy_position_feasible(row: dict, position: dict, room: dict,
                                      active_objects: list[dict], door_clearances: list[dict],
                                      *, boundary_tolerance: float = .005) -> bool:
    footprint = _footprint_at_position(row, position)
    if not _footprint_within_polygon(footprint, room.get('polygon') or [], boundary_tolerance):
        return False
    if _blocking_door_id(row, position, door_clearances):
        return False
    row_id = str(row.get('id') or '')
    room_id = str(row.get('room_id') or '')
    role = _canonical_object_role(row)
    for other in active_objects:
        if (str(other.get('id') or '') == row_id or other.get('review_status') == 'rejected'
                or str(other.get('room_id') or '') != room_id):
            continue
        roles = frozenset((role, _canonical_object_role(other)))
        if roles in _ALLOWED_OBJECT_OVERLAPS:
            continue
        if _aabb_overlap_ratio(footprint, _rotated_footprint(other)) > .45:
            return False
    return True


def _minimal_semantic_proxy_translation(row: dict, room: dict, active_objects: list[dict],
                                        door_clearances: list[dict]) -> tuple[Optional[dict], dict]:
    polygon = room.get('polygon') or []
    origin_row = row.get('position') if isinstance(row.get('position'), dict) else {}
    origin = {'x': _number(origin_row.get('x')), 'z': _number(origin_row.get('z'))}
    if len(polygon) < 3:
        return None, {'status': 'failed', 'reason': 'invalid_room_polygon'}

    room_min_x, room_min_z, room_max_x, room_max_z = _aabb(polygon)
    footprint = _rotated_footprint({**row, 'position': {'x': 0, 'y': 0, 'z': 0}})
    object_min_x, object_min_z, object_max_x, object_max_z = _aabb(footprint)
    low_x, high_x = room_min_x - object_min_x, room_max_x - object_max_x
    low_z, high_z = room_min_z - object_min_z, room_max_z - object_max_z
    if high_x < low_x or high_z < low_z:
        return None, {'status': 'failed', 'reason': 'object_larger_than_room_bounds'}

    span_x, span_z = high_x - low_x, high_z - low_z
    coarse_step = max(.025, max(span_x, span_z) / 80)
    xs = _axis_samples(low_x, high_x, origin['x'], coarse_step)
    zs = _axis_samples(low_z, high_z, origin['z'], coarse_step)
    candidates = sorted(
        ({'x': x, 'z': z} for x in xs for z in zs),
        key=lambda point: (round(_distance(point, origin), 8), point['x'], point['z']),
    )
    best = next((
        point for point in candidates
        if _semantic_proxy_position_feasible(
            row, point, room, active_objects, door_clearances,
        )
    ), None)
    if best is None:
        return None, {
            'status': 'failed',
            'reason': 'no_feasible_position_avoiding_room_door_overlap',
        }

    resolution = coarse_step
    for resolution in (max(.01, coarse_step / 4), .005, .001):
        offsets = range(-4, 5)
        refinements = sorted((
            {
                'x': max(low_x, min(high_x, best['x'] + dx * resolution)),
                'z': max(low_z, min(high_z, best['z'] + dz * resolution)),
            }
            for dx in offsets for dz in offsets
        ), key=lambda point: (round(_distance(point, origin), 8), point['x'], point['z']))
        refined = next((
            point for point in refinements
            if _semantic_proxy_position_feasible(
                row, point, room, active_objects, door_clearances,
            )
        ), None)
        if refined is not None and _distance(refined, origin) <= _distance(best, origin) + 1e-9:
            best = refined

    rounded = {'x': round(best['x'], 5), 'z': round(best['z'], 5)}
    if not _semantic_proxy_position_feasible(row, rounded, room, active_objects, door_clearances):
        return None, {
            'status': 'failed',
            'reason': 'rounded_position_failed_semantic_constraints',
        }
    return rounded, {
        'status': 'translated',
        'distance_m': round(_distance(rounded, origin), 5),
        'translation_m': {
            'x': round(rounded['x'] - origin['x'], 5),
            'z': round(rounded['z'] - origin['z'], 5),
        },
        'resolution_m': round(resolution, 5),
    }


def _object_semantic_issue_codes(report: dict, object_id: str) -> list[str]:
    return sorted({
        str(issue.get('code') or '')
        for issue in report.get('hard_errors') or []
        if object_id in {
            str(issue.get('object_id') or ''), str(issue.get('other_object_id') or ''),
        }
        and issue.get('code')
    })


def repair_ai_semantic_proxies(model: dict, *, max_passes: int = 4) -> dict:
    """Deterministically relocate invalid, unreviewed AI proxies without new AI calls."""
    value = upgrade_model_v2(model)
    rooms = {str(room.get('id') or ''): room for room in value.get('rooms') or []}
    active_objects = [
        row for row in value.get('fixed_objects') or []
        if isinstance(row, dict) and row.get('review_status') != 'rejected'
    ]
    door_clearances = _semantic_door_clearances(value)
    notices = value.setdefault('uncertainties', [])
    pass_limit = max(1, min(8, int(max_passes)))

    for pass_index in range(1, pass_limit + 1):
        report = validate_semantic_layout(value)
        candidates = []
        for row in active_objects:
            object_id = str(row.get('id') or '')
            issue_codes = _object_semantic_issue_codes(report, object_id)
            if (issue_codes and row.get('source') == 'ai'
                    and row.get('purpose') == 'layout_proxy'
                    and row.get('review_status') == 'pending'):
                candidates.append((object_id, row, issue_codes))
        if not candidates:
            break

        moved = False
        for object_id, row, stale_codes in sorted(candidates, key=lambda item: item[0]):
            current_report = validate_semantic_layout(value)
            issue_codes = _object_semantic_issue_codes(current_report, object_id)
            if not issue_codes:
                continue
            original = copy.deepcopy(row.get('original_position') or row.get('position') or {})
            row['original_position'] = {
                'x': round(_number(original.get('x')), 5),
                'y': round(_number(original.get('y')), 5),
                'z': round(_number(original.get('z')), 5),
            }
            room_id = str(row.get('room_id') or '')
            room = rooms.get(room_id)
            if room:
                repaired, evidence = _minimal_semantic_proxy_translation(
                    row, room, active_objects, door_clearances,
                )
            else:
                repaired, evidence = None, {'status': 'failed', 'reason': 'invalid_bound_room'}
            evidence.update({
                'method': 'deterministic_semantic_proxy_projection_v1',
                'trigger': 'invalid_ai_layout_proxy_placement',
                'trigger_codes': issue_codes or stale_codes,
                'room_id': room_id,
                'pass_index': pass_index,
            })
            if repaired is not None:
                row['position'] = {
                    'x': repaired['x'], 'y': _number((row.get('position') or {}).get('y')),
                    'z': repaired['z'],
                }
                evidence['result_position'] = copy.deepcopy(row['position'])
                evidence['distance_m'] = round(_distance(row['original_position'], row['position']), 5)
                evidence['translation_m'] = {
                    'x': round(row['position']['x'] - row['original_position']['x'], 5),
                    'z': round(row['position']['z'] - row['original_position']['z'], 5),
                }
                message = (
                    f"本地语义规则将 AI 布局代理 {row.get('name') or object_id} "
                    f"平移 {evidence['distance_m']:.3f}m，避开房间边界、门洞净空和物体重叠；"
                    f"触发规则：{', '.join(evidence['trigger_codes'])}"
                )
                moved = True
            else:
                message = (
                    f"AI 布局代理 {row.get('name') or object_id} 无法在绑定房间内找到同时避开门洞与重叠的可行位置："
                    f"{evidence.get('reason') or 'unknown'}；保留原坐标并阻断自动锁定"
                )
            row['geometry_repair'] = evidence
            if message not in notices:
                notices.append(message)
        if not moved:
            break

    return upgrade_model_v2(value)


def upgrade_model_v2(model: dict) -> dict:
    """Return an in-memory v2 copy.  This function never writes old project files."""
    if not isinstance(model, dict) or not (model.get('walls') or model.get('rooms')):
        return copy.deepcopy(model) if isinstance(model, dict) else {}
    value = copy.deepcopy(model)
    previous_schema = int(_number(value.get('schema_version'), 1))
    rooms = value.get('rooms') or []
    for room in rooms:
        room['semantic_profile'] = _canonical_room_profile(room)
        room.setdefault('semantic_status', 'pending')
    opening_notices = _audit_ai_openings(value)
    for notice in opening_notices:
        if notice not in value.setdefault('uncertainties', []):
            value['uncertainties'].append(notice)
    contracts = _room_contracts(rooms)
    previous_contracts = {
        str(row.get('room_id') or ''): row for row in value.get('room_contracts') or [] if isinstance(row, dict)
    }
    for contract in contracts:
        previous = previous_contracts.get(str(contract.get('room_id') or '')) or {}
        if str(previous.get('source') or '').startswith('cad_'):
            contract.update(copy.deepcopy(previous))
        else:
            contract['assumptions'] = [str(item)[:300] for item in previous.get('assumptions') or []][:20]
    value['room_contracts'] = contracts
    room_map = {str(room.get('id') or ''): room for room in rooms}
    normalized_objects = []
    for row in value.get('fixed_objects') or []:
        if not isinstance(row, dict):
            continue
        item = copy.deepcopy(row)
        if not item.get('room_id'):
            point = item.get('position') or {}
            containing = next((room for room in rooms if _point_in_polygon(point, room.get('polygon') or [])), None)
            if containing:
                item['room_id'] = containing.get('id')
        bound_room = room_map.get(str(item.get('room_id') or '')) or {}
        room_profile = str(bound_room.get('semantic_profile') or _canonical_room_profile(bound_room))
        role = _canonical_object_role(item, room_profile)
        item.update(
            semantic_role=role,
            purpose=item.get('purpose') if item.get('purpose') in ('observed_architecture', 'layout_proxy') else 'observed_architecture',
            observed=bool(item.get('observed', item.get('purpose') != 'layout_proxy')),
            review_status=item.get('review_status') if item.get('review_status') in ('pending', 'accepted', 'rejected') else ('accepted' if item.get('source') in ('human', 'imported') else 'pending'),
            blocks_camera=bool(item.get('blocks_camera', role != 'shower_zone')),
            clearance_m=_clamp(item.get('clearance_m'), 0, 2, .25),
        )
        room_contract = next((contract for contract in contracts if contract.get('room_id') == item.get('room_id')), None)
        required_roles = {
            role_name for group in (room_contract or {}).get('required_role_groups') or [] for role_name in group
        }
        item['required_for_camera'] = role in required_roles
        normalized_objects.append(item)
    protected_singletons: dict[tuple[str, str], dict] = {}
    for item in normalized_objects:
        if item.get('review_status') == 'rejected':
            continue
        room_id = str(item.get('room_id') or '')
        room = room_map.get(room_id) or {}
        profile = str(room.get('semantic_profile') or _canonical_room_profile(room))
        role_key = _singleton_role_key(str(item.get('semantic_role') or ''), profile)
        is_auto_proxy = (
            item.get('source') == 'ai' and item.get('purpose') == 'layout_proxy'
            and not item.get('observed')
        )
        if role_key in _SINGLETON_PROXY_ROLES and not is_auto_proxy:
            protected_singletons.setdefault((room_id, role_key), item)
    dedup_notices = []
    for item in normalized_objects:
        if item.get('review_status') == 'rejected':
            continue
        room_id = str(item.get('room_id') or '')
        room = room_map.get(room_id) or {}
        profile = str(room.get('semantic_profile') or _canonical_room_profile(room))
        role_key = _singleton_role_key(str(item.get('semantic_role') or ''), profile)
        is_auto_proxy = (
            item.get('source') == 'ai' and item.get('purpose') == 'layout_proxy'
            and not item.get('observed')
        )
        if not is_auto_proxy or role_key not in _SINGLETON_PROXY_ROLES:
            continue
        duplicate = protected_singletons.get((room_id, role_key))
        if duplicate:
            item['review_status'] = 'rejected'
            item['semantic_deduplication'] = {
                'method': 'deterministic_singleton_alias_dedup_v1',
                'canonical_role': role_key,
                'duplicate_of': str(duplicate.get('id') or ''),
                'room_profile': profile,
                'action': 'rejected_ai_layout_proxy',
                'reason': 'active room singleton already satisfies the canonical role alias group',
            }
            dedup_notices.append(
                f"本地语义去重停用 AI 代理 {item.get('name') or item.get('id')}："
                f"{profile}/{role_key} 已由 {duplicate.get('name') or duplicate.get('id')} 满足"
            )
            continue
        protected_singletons[(room_id, role_key)] = item
    value['fixed_objects'] = normalized_objects
    for notice in dedup_notices:
        if notice not in value.setdefault('uncertainties', []):
            value['uncertainties'].append(notice)
    value['schema_version'] = 2
    value['geometry_report'] = validate_model(value)
    if previous_schema < 2:
        value['migrated_from_schema_version'] = previous_schema
    report = validate_semantic_layout(value)
    existing_report = value.get('semantic_report') if isinstance(value.get('semantic_report'), dict) else {}
    report['audit_passes'] = int(_number(existing_report.get('audit_passes'), 0))
    value['semantic_report'] = report
    failed_rooms = {str(row.get('room_id') or '') for row in report['hard_errors'] if row.get('room_id')}
    for room in value.get('rooms') or []:
        room['semantic_status'] = 'needs_review' if str(room.get('id') or '') in failed_rooms else 'complete'
    for contract in value.get('room_contracts') or []:
        contract['status'] = 'needs_review' if str(contract.get('room_id') or '') in failed_rooms else 'complete'
    return value


def runtime_project_copy(project: dict) -> dict:
    """Upgrade a project for API/runtime use while leaving its JSON file untouched."""
    out = copy.deepcopy(project)
    legacy_geometry_contract = int(out.get('geometry_schema_version') or 0) < 3
    raw_model = out.get('model') if isinstance(out.get('model'), dict) else {}
    if not (raw_model.get('walls') or raw_model.get('rooms')):
        return out
    old_schema = int(_number(raw_model.get('schema_version'), 1))
    out['model'] = upgrade_model_v2(raw_model)
    if old_schema < 2:
        for capture in out.get('captures') or []:
            capture['status'] = 'stale'
            capture.setdefault('stale_reason', 'schema_v2_semantic_layout_required')
    if legacy_geometry_contract:
        out = migrate_legacy_project_geometry(out)
    return out


def _normalize_render_gate(value: Any) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    role_fractions = value.get('semantic_role_fractions')
    return {
        'version': str(value.get('version') or '')[:100],
        'pass': value.get('pass') is True,
        'status': 'pass' if value.get('pass') is True else 'blocked',
        'profile': str(value.get('profile') or 'other')[:40],
        'denominator_pixels': max(0, int(_number(value.get('denominator_pixels'), 0))),
        'matched_pixels': max(0, int(_number(value.get('matched_pixels'), 0))),
        'unmatched_pixels': max(0, int(_number(value.get('unmatched_pixels'), 0))),
        'floor_fraction': round(_clamp(value.get('floor_fraction'), 0, 1, 0), 8),
        'wall_fraction': round(_clamp(value.get('wall_fraction'), 0, 1, 0), 8),
        'peak_semantic_role': str(value.get('peak_semantic_role') or '')[:80],
        'peak_semantic_role_fraction': round(
            _clamp(value.get('peak_semantic_role_fraction'), 0, 1, 0), 8),
        'semantic_role_fractions': {
            str(role)[:80]: round(_clamp(amount, 0, 1, 0), 8)
            for role, amount in (role_fractions.items() if isinstance(role_fractions, dict) else [])
        },
        'required_groups': copy.deepcopy(value.get('required_groups') or [])[:20],
        'reasons': [str(reason)[:500] for reason in value.get('reasons') or []][:20],
    }


def _normalize_cad_provenance(value: Any) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    transform = value.get('transform') if isinstance(value.get('transform'), list) else []
    chain = value.get('insert_chain') if isinstance(value.get('insert_chain'), list) else []
    normalized = {
        'handle': str(value.get('handle') or '')[:100],
        'root_handle': str(value.get('root_handle') or value.get('handle') or '')[:100],
        'source_handle': str(value.get('source_handle') or '')[:100],
        'layer': str(value.get('effective_layer') or value.get('layer') or '')[:200],
        'raw_layer': str(value.get('raw_layer') or value.get('layer') or '')[:200],
        'effective_layer': str(value.get('effective_layer') or value.get('layer') or '')[:200],
        'block': str(value.get('block') or '')[:200],
        'source_kind': str(value.get('source_kind') or '')[:80],
        'confidence': _clamp(value.get('confidence'), 0, 1, 1),
        'transform': copy.deepcopy(transform[:16]),
        'insert_chain': copy.deepcopy(chain[:16]),
    }
    if value.get('segment_index') is not None:
        normalized['segment_index'] = max(0, int(_number(value.get('segment_index'), 0)))
    if isinstance(value.get('source_segment_m'), list):
        points = []
        for point in value['source_segment_m'][:2]:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                points.append([round(_number(point[0]), 8), round(_number(point[1]), 8)])
        if len(points) == 2:
            normalized['source_segment_m'] = points
    if isinstance(value.get('source_polygon_m'), list):
        polygon = []
        for point in value['source_polygon_m'][:512]:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                polygon.append([round(_number(point[0]), 8), round(_number(point[1]), 8)])
        if len(polygon) >= 3:
            normalized['source_polygon_m'] = polygon
    if isinstance(value.get('boundary_sources'), list):
        normalized['boundary_sources'] = [
            {
                'root_handle': str(row.get('root_handle') or '')[:100],
                'source_handle': str(row.get('source_handle') or '')[:100],
                'layer': str(row.get('effective_layer') or row.get('layer') or '')[:200],
                'raw_layer': str(row.get('raw_layer') or row.get('layer') or '')[:200],
                'effective_layer': str(row.get('effective_layer') or row.get('layer') or '')[:200],
                'block': str(row.get('block') or '')[:200],
                'transform': copy.deepcopy(row.get('transform') or [])[:16],
            }
            for row in value['boundary_sources'][:512] if isinstance(row, dict)
        ]
    for key in ('derivation', 'semantic_derivation', 'semantic_preview_sha256', 'ai_opening_id'):
        if value.get(key):
            normalized[key] = str(value.get(key))[:160]
    if value.get('ai_to_cad_wall_distance_m') is not None:
        normalized['ai_to_cad_wall_distance_m'] = round(
            _number(value.get('ai_to_cad_wall_distance_m')), 8)
    if isinstance(value.get('open_plan_overlap_repair'), dict):
        normalized['open_plan_overlap_repair'] = {
            'method': str(value['open_plan_overlap_repair'].get('method') or '')[:120],
            'other_room_id': str(value['open_plan_overlap_repair'].get('other_room_id') or '')[:100],
        }
    if isinstance(value.get('semantic_overlap_repair'), dict):
        normalized['semantic_overlap_repair'] = {
            'method': str(value['semantic_overlap_repair'].get('method') or '')[:120],
            'removed_area_m2': round(
                _number(value['semantic_overlap_repair'].get('removed_area_m2')), 8),
        }
    if isinstance(value.get('semantic_polygon_repair'), dict):
        normalized['semantic_polygon_repair'] = {
            'method': str(value['semantic_polygon_repair'].get('method') or '')[:120],
            'original_area_m2': round(
                _number(value['semantic_polygon_repair'].get('original_area_m2')), 8),
            'repaired_area_m2': round(
                _number(value['semantic_polygon_repair'].get('repaired_area_m2')), 8),
            'component_count': max(1, int(_number(
                value['semantic_polygon_repair'].get('component_count'), 1))),
        }
    return normalized


def normalize_model(payload: dict, *, source_width: int = 1, source_height: int = 1,
                    source: str = 'human') -> dict:
    """Validate loose AI/browser input and return the canonical metre model."""
    width_m = _clamp(payload.get('width_m'), 2.0, 80.0, 12.0)
    default_depth = width_m * max(0.25, min(3.0, source_height / max(source_width, 1)))
    depth_m = _clamp(payload.get('depth_m'), 2.0, 80.0, default_depth)
    wall_height = _clamp(payload.get('wall_height_m'), 2.0, 6.0, 2.8)
    wall_thickness = _clamp(payload.get('wall_thickness_m'), 0.05, 0.8, 0.12)
    input_normalized = bool(payload.get('_coordinates_normalized', False))
    walls: list[dict] = []
    seen: set[str] = set()
    for row in payload.get('walls') or []:
        if not isinstance(row, dict):
            continue
        start = _metric_point(row.get('start'), width_m, depth_m, normalized=input_normalized)
        end = _metric_point(row.get('end'), width_m, depth_m, normalized=input_normalized)
        if _distance(start, end) < 0.05:
            continue
        wall_id = _unique_id(row.get('id'), 'wall', seen)
        cad_provenance = _normalize_cad_provenance(row.get('cad_provenance'))
        walls.append({
            'id': wall_id, 'start': start, 'end': end,
            'thickness_m': _clamp(row.get('thickness_m'), 0.05, 0.8, wall_thickness),
            'height_m': _clamp(row.get('height_m'), 2.0, 6.0, wall_height),
            'kind': row.get('kind') if row.get('kind') in ('exterior', 'interior', 'partition') else 'interior',
            'source': row.get('source') if row.get('source') in ('ai', 'human', 'ai_edited', 'imported', 'cad') else source,
            'confidence': _clamp(row.get('confidence'), 0, 1, 1 if source == 'human' else 0.5),
            **({'boundary_kind': str(row.get('boundary_kind') or '')[:80]} if row.get('boundary_kind') else {}),
            **({'cad_provenance': cad_provenance} if cad_provenance else {}),
        })

    rooms: list[dict] = []
    seen_rooms: set[str] = set()
    for row in payload.get('rooms') or []:
        if not isinstance(row, dict):
            continue
        polygon = [_metric_point(point, width_m, depth_m, normalized=input_normalized) for point in row.get('polygon') or []]
        if len(polygon) < 3 or _polygon_area(polygon) < 0.05:
            continue
        room_id = _unique_id(row.get('id'), 'room', seen_rooms)
        cad_provenance = _normalize_cad_provenance(row.get('cad_provenance'))
        rooms.append({
            'id': room_id, 'label': str(row.get('label') or f'房间 {len(rooms) + 1}')[:100],
            'room_type': str(row.get('room_type') or '其他')[:80], 'polygon': polygon,
            'area_m2': round(_polygon_area(polygon), 2),
            'floor_elevation_m': _clamp(row.get('floor_elevation_m'), -1, 2, 0),
            'ceiling_height_m': _clamp(row.get('ceiling_height_m'), 2, 6, wall_height),
            'selected': bool(row.get('selected', True)),
            'source': row.get('source') if row.get('source') in ('ai', 'human', 'ai_edited', 'imported', 'cad') else source,
            'confidence': _clamp(row.get('confidence'), 0, 1, 1 if source == 'human' else 0.5),
            'semantic_profile': str(row.get('semantic_profile') or '')[:80],
            **({'reference_room_profile': str(row.get('reference_room_profile') or '')[:80]}
               if row.get('reference_room_profile') else {}),
            'semantic_status': row.get('semantic_status') if row.get('semantic_status') in ('pending', 'complete', 'needs_review') else 'pending',
            **({'cad_provenance': cad_provenance} if cad_provenance else {}),
        })

    reclassified_walls = []
    for wall in walls:
        if wall.get('source') == 'ai' and wall.get('kind') == 'exterior' and _wall_separates_rooms(wall, rooms):
            wall['kind'] = 'interior'
            wall['source'] = 'ai_edited'
            wall['confidence'] = round(_number(wall.get('confidence'), .5) * .9, 5)
            reclassified_walls.append(wall['id'])

    openings: list[dict] = []
    seen_openings: set[str] = set()
    wall_map = {wall['id']: wall for wall in walls}
    for row in payload.get('openings') or []:
        if not isinstance(row, dict):
            continue
        kind = row.get('kind') if row.get('kind') in ('door', 'window', 'open_connection') else 'door'
        wall = wall_map.get(str(row.get('wall_id') or ''))
        raw_center = row.get('center')
        if not wall and raw_center:
            center = _metric_point(raw_center, width_m, depth_m, normalized=input_normalized)
            wall = _nearest_wall(center, walls)[0]
        if not wall and isinstance(row.get('points'), list) and row['points']:
            points = [_metric_point(value, width_m, depth_m, normalized=input_normalized) for value in row['points'][:2]]
            center = {'x': sum(p['x'] for p in points) / len(points), 'z': sum(p['z'] for p in points) / len(points)}
            wall = _nearest_wall(center, walls)[0]
        if not wall:
            continue
        length = _distance(wall['start'], wall['end'])
        width = _clamp(row.get('width_m'), 0.25, max(0.25, length - 0.1), 0.9 if kind == 'door' else 1.4)
        if raw_center:
            center = _metric_point(raw_center, width_m, depth_m, normalized=input_normalized)
            _, t = _point_segment(center, wall['start'], wall['end'])
            offset = t * length - width / 2
        elif isinstance(row.get('points'), list) and row['points']:
            point = _metric_point(row['points'][0], width_m, depth_m, normalized=input_normalized)
            _, t = _point_segment(point, wall['start'], wall['end'])
            offset = t * length
        else:
            offset = _number(row.get('offset_m'), (length - width) / 2)
        offset = _clamp(offset, 0, max(0, length - width), 0)
        opening_id = _unique_id(row.get('id'), 'opening', seen_openings)
        cad_provenance = _normalize_cad_provenance(row.get('cad_provenance'))
        openings.append({
            'id': opening_id, 'wall_id': wall['id'], 'kind': kind,
            'offset_m': offset, 'width_m': width,
            'height_m': _clamp(row.get('height_m'), 0.3, wall['height_m'], 2.1 if kind != 'window' else 1.2),
            'sill_height_m': _clamp(row.get('sill_height_m'), 0, wall['height_m'] - 0.2, 0.9 if kind == 'window' else 0),
            'source': row.get('source') if row.get('source') in ('ai', 'human', 'ai_edited', 'imported', 'cad') else source,
            'confidence': _clamp(row.get('confidence'), 0, 1, 1 if source == 'human' else 0.5),
            'review_status': row.get('review_status') if row.get('review_status') in ('pending', 'accepted', 'rejected') else ('accepted' if source == 'human' else 'pending'),
            **({'width_source': str(row.get('width_source') or '')[:80]} if row.get('width_source') else {}),
            **({'height_source': str(row.get('height_source') or '')[:80]} if row.get('height_source') else {}),
            **({'sill_height_source': str(row.get('sill_height_source') or '')[:80]} if row.get('sill_height_source') else {}),
            **({'reference_anchor_ready': row.get('reference_anchor_ready') is True}
               if row.get('reference_anchor_ready') is not None else {}),
            **({'reference_anchor_blockers': [str(value)[:120] for value in row.get('reference_anchor_blockers') or []][:20]}
               if isinstance(row.get('reference_anchor_blockers'), list) else {}),
            **({'rotation_y_deg': round(_number(row.get('rotation_y_deg')), 8)}
               if row.get('rotation_y_deg') is not None else {}),
            **({'insert_scale': copy.deepcopy(row.get('insert_scale'))}
               if isinstance(row.get('insert_scale'), dict) else {}),
            **({'cad_provenance': cad_provenance} if cad_provenance else {}),
            **({'duplicate_of': str(row.get('duplicate_of'))[:100]} if row.get('duplicate_of') else {}),
            **({'opening_deduplication': {
                'method': str(row['opening_deduplication'].get('method') or '')[:100],
                'duplicate_of': str(row['opening_deduplication'].get('duplicate_of') or '')[:100],
                'overlap_m': round(_number(row['opening_deduplication'].get('overlap_m')), 5),
                'action': str(row['opening_deduplication'].get('action') or '')[:80],
                'reason': str(row['opening_deduplication'].get('reason') or '')[:300],
            }} if isinstance(row.get('opening_deduplication'), dict) else {}),
            **({'opening_topology_review': {
                'method': str(row['opening_topology_review'].get('method') or '')[:100],
                'status': str(row['opening_topology_review'].get('status') or '')[:80],
                'code': str(row['opening_topology_review'].get('code') or '')[:100],
                'room_ids': [str(value)[:80] for value in row['opening_topology_review'].get('room_ids') or []][:8],
                'room_profiles': [str(value)[:40] for value in row['opening_topology_review'].get('room_profiles') or []][:8],
                'samples': [{
                    'label': str(sample.get('label') or '')[:20],
                    'along_m': round(_number(sample.get('along_m')), 5),
                    'negative_room_ids': [str(value)[:80] for value in sample.get('negative_room_ids') or []][:8],
                    'positive_room_ids': [str(value)[:80] for value in sample.get('positive_room_ids') or []][:8],
                    'point': {
                        'x': round(_number((sample.get('point') or {}).get('x')), 5),
                        'z': round(_number((sample.get('point') or {}).get('z')), 5),
                    },
                } for sample in row['opening_topology_review'].get('samples') or []
                  if isinstance(sample, dict)][:5],
                'reason': str(row['opening_topology_review'].get('reason') or '')[:300],
            }} if isinstance(row.get('opening_topology_review'), dict) else {}),
        })

    objects: list[dict] = []
    seen_objects: set[str] = set()
    room_profiles = {
        str(room.get('id') or ''): str(room.get('semantic_profile') or _canonical_room_profile(room))
        for room in rooms
    }
    for row in payload.get('fixed_objects') or payload.get('objects') or []:
        if not isinstance(row, dict):
            continue
        object_id = _unique_id(row.get('id'), 'object', seen_objects)
        position = _metric_point(row.get('position') or row.get('center'), width_m, depth_m, normalized=input_normalized)
        size = row.get('size') if isinstance(row.get('size'), dict) else {}
        unknown_cad_extent = (str(row.get('size_source') or '') == 'unknown'
                              and (row.get('source') == 'cad' or isinstance(row.get('cad_provenance'), dict)))
        object_row = {
            'id': object_id, 'name': str(row.get('name') or row.get('kind') or '固定物')[:100],
            'kind': str(row.get('kind') or 'fixed_furniture')[:80],
            'position': {'x': position['x'], 'y': _clamp((row.get('position') or {}).get('y'), 0, 5, 0), 'z': position['z']},
            'size': {'x': 0.0 if unknown_cad_extent else _clamp(size.get('x'), 0.1, 10, 1),
                     'y': _clamp(size.get('y'), 0.1, 6, 0.8),
                     'z': 0.0 if unknown_cad_extent else _clamp(size.get('z'), 0.1, 10, 0.7)},
            'rotation_y_deg': _clamp(row.get('rotation_y_deg'), -360, 360, 0),
            'room_id': str(row.get('room_id') or '')[:80],
            'source': row.get('source') if row.get('source') in ('ai', 'human', 'ai_edited', 'imported', 'cad') else source,
            'confidence': _clamp(row.get('confidence'), 0, 1, 0.5),
            'purpose': row.get('purpose') if row.get('purpose') in ('observed_architecture', 'layout_proxy') else 'observed_architecture',
            'observed': bool(row.get('observed', row.get('purpose') != 'layout_proxy')),
            'review_status': row.get('review_status') if row.get('review_status') in ('pending', 'accepted', 'rejected') else ('accepted' if source == 'human' else 'pending'),
            'blocks_camera': bool(row.get('blocks_camera', str(row.get('semantic_role') or '') != 'shower_zone')),
            'required_for_camera': bool(row.get('required_for_camera', False)),
            'clearance_m': _clamp(row.get('clearance_m'), 0, 2, .25),
            **({'size_source': str(row.get('size_source') or '')[:100]} if row.get('size_source') else {}),
            **({'height_source': str(row.get('height_source') or '')[:120]} if row.get('height_source') else {}),
            **({'insert_position': copy.deepcopy(row.get('insert_position'))}
               if isinstance(row.get('insert_position'), dict) else {}),
            **({'insert_scale': copy.deepcopy(row.get('insert_scale'))}
               if isinstance(row.get('insert_scale'), dict) else {}),
            **({'cad_world_bbox_m': copy.deepcopy(row.get('cad_world_bbox_m'))}
               if isinstance(row.get('cad_world_bbox_m'), list) else {}),
            **({'cad_local_bbox_m': copy.deepcopy(row.get('cad_local_bbox_m'))}
               if isinstance(row.get('cad_local_bbox_m'), list) else {}),
            **({'rotation_source': str(row.get('rotation_source') or '')[:100]}
               if row.get('rotation_source') else {}),
            **({'reference_anchor_ready': row.get('reference_anchor_ready') is True}
               if row.get('reference_anchor_ready') is not None else {}),
            **({'reference_anchor_blockers': [str(value)[:120] for value in row.get('reference_anchor_blockers') or []][:20]}
               if isinstance(row.get('reference_anchor_blockers'), list) else {}),
            **({'room_match_ids': [str(value)[:80] for value in row.get('room_match_ids') or []][:8]}
               if isinstance(row.get('room_match_ids'), list) else {}),
        }
        cad_provenance = _normalize_cad_provenance(row.get('cad_provenance'))
        if cad_provenance:
            object_row['cad_provenance'] = cad_provenance
        if isinstance(row.get('semantic_acceptance'), dict):
            object_row['semantic_acceptance'] = {
                'method': str(row['semantic_acceptance'].get('method') or '')[:100],
                'status': str(row['semantic_acceptance'].get('status') or '')[:40],
                'scope': str(row['semantic_acceptance'].get('scope') or '')[:100],
                'accepted_at': _number(row['semantic_acceptance'].get('accepted_at'), 0),
            }
        if isinstance(row.get('semantic_deduplication'), dict):
            evidence = row['semantic_deduplication']
            object_row['semantic_deduplication'] = {
                'method': str(evidence.get('method') or '')[:100],
                'canonical_role': str(evidence.get('canonical_role') or '')[:80],
                'duplicate_of': str(evidence.get('duplicate_of') or '')[:100],
                'room_profile': str(evidence.get('room_profile') or '')[:40],
                'action': str(evidence.get('action') or '')[:80],
                'reason': str(evidence.get('reason') or '')[:300],
            }
        if isinstance(row.get('original_position'), dict):
            original = _metric_point(row['original_position'], width_m, depth_m, normalized=input_normalized)
            object_row['original_position'] = {
                'x': original['x'],
                'y': _clamp(row['original_position'].get('y'), 0, 5, 0),
                'z': original['z'],
            }
        if isinstance(row.get('geometry_repair'), dict):
            raw_repair = row['geometry_repair']
            translation = raw_repair.get('translation_m') if isinstance(raw_repair.get('translation_m'), dict) else {}
            object_row['geometry_repair'] = {
                'method': str(raw_repair.get('method') or '')[:100],
                'status': str(raw_repair.get('status') or '')[:40],
                'trigger': str(raw_repair.get('trigger') or '')[:120],
                'room_id': str(raw_repair.get('room_id') or '')[:80],
                **({'reason': str(raw_repair.get('reason'))[:160]} if raw_repair.get('reason') else {}),
                **({'trigger_codes': sorted({
                    str(code)[:100] for code in raw_repair.get('trigger_codes') or [] if code
                })} if isinstance(raw_repair.get('trigger_codes'), list) else {}),
                **({'pass_index': int(_clamp(raw_repair.get('pass_index'), 1, 8, 1))} if raw_repair.get('pass_index') is not None else {}),
                **({'distance_m': round(_number(raw_repair.get('distance_m')), 5)} if raw_repair.get('distance_m') is not None else {}),
                **({'candidate_distance_m': round(_number(raw_repair.get('candidate_distance_m')), 5)} if raw_repair.get('candidate_distance_m') is not None else {}),
                **({'resolution_m': round(_number(raw_repair.get('resolution_m')), 5)} if raw_repair.get('resolution_m') is not None else {}),
                **({'max_translation_m': round(_number(raw_repair.get('max_translation_m')), 5)} if raw_repair.get('max_translation_m') is not None else {}),
                **({'translation_m': {
                    'x': round(_number(translation.get('x')), 5),
                    'z': round(_number(translation.get('z')), 5),
                }} if translation else {}),
            }
            if isinstance(raw_repair.get('result_position'), dict):
                result_position = _metric_point(
                    raw_repair['result_position'], width_m, depth_m, normalized=input_normalized)
                object_row['geometry_repair']['result_position'] = {
                    'x': result_position['x'],
                    'y': _clamp(raw_repair['result_position'].get('y'), 0, 5, 0),
                    'z': result_position['z'],
                }
        object_row['semantic_role'] = _canonical_object_role(
            {**row, **object_row}, room_profiles.get(object_row['room_id'], ''))
        objects.append(object_row)

    cameras: list[dict] = []
    seen_cameras: set[str] = set()
    for row in payload.get('cameras') or []:
        if not isinstance(row, dict):
            continue
        camera_id = _unique_id(row.get('id'), 'camera', seen_cameras)
        p2 = _metric_point(row.get('position'), width_m, depth_m, normalized=input_normalized)
        t2 = _metric_point(row.get('target'), width_m, depth_m, normalized=input_normalized)
        position = row.get('position') if isinstance(row.get('position'), dict) else {}
        target = row.get('target') if isinstance(row.get('target'), dict) else {}
        render_gate = _normalize_render_gate(row.get('render_gate'))
        cameras.append({
            'id': camera_id, 'name': str(row.get('name') or f'机位 {len(cameras) + 1}')[:100],
            'position': {'x': p2['x'], 'y': _clamp(position.get('y'), 0.3, 3.0, 1.55), 'z': p2['z']},
            'target': {'x': t2['x'], 'y': _clamp(target.get('y'), 0, 3.0, 1.2), 'z': t2['z']},
            'focal_length_mm': _clamp(row.get('focal_length_mm'), 12, 120, 24),
            'room_id': str(row.get('room_id') or '')[:80], 'enabled': bool(row.get('enabled', True)),
            'source': row.get('source') if row.get('source') in ('human_3d', 'imported', 'manual', 'auto_geometry', 'ai_selected') else 'manual',
            **({'auto_plan_id': str(row.get('auto_plan_id'))[:100]} if row.get('auto_plan_id') else {}),
            **({'candidate_id': str(row.get('candidate_id'))[:100]} if row.get('candidate_id') else {}),
            **({'local_score': round(_clamp(row.get('local_score'), 0, 100, 0), 2)} if row.get('local_score') is not None else {}),
            **({'selection_score': round(_clamp(row.get('selection_score'), 0, 100, 0), 2)} if row.get('selection_score') is not None else {}),
            **({'selection_reason': str(row.get('selection_reason'))[:1000]} if row.get('selection_reason') else {}),
            **({'pool_rank': int(_clamp(row.get('pool_rank'), 1, 3, 1))} if row.get('pool_rank') is not None else {}),
            **({'is_primary': bool(row.get('is_primary'))} if row.get('is_primary') is not None else {}),
            **({'origin_scope': row.get('origin_scope')} if row.get('origin_scope') in (
                'inside_room', 'adjacent_portal', 'doorway_inside',
                'cad_semantic_adjacent_free_space') else {}),
            **({'portal_opening_id': str(row.get('portal_opening_id'))[:100]} if row.get('portal_opening_id') else {}),
            **({'entry_opening_id': str(row.get('entry_opening_id'))[:100]} if row.get('entry_opening_id') else {}),
            **({'reference_slot_id': str(row.get('reference_slot_id'))[:100]} if row.get('reference_slot_id') else {}),
            **({'reference_contract_validation': {
                'version': int(_clamp((row.get('reference_contract_validation') or {}).get('version'), 1, 100, 1)),
                'slot_id': str((row.get('reference_contract_validation') or {}).get('slot_id') or '')[:100],
                'scene_id': str((row.get('reference_contract_validation') or {}).get('scene_id') or '')[:100],
                'room_id': str((row.get('reference_contract_validation') or {}).get('room_id') or '')[:80],
                'landing_policy_mode': str((row.get('reference_contract_validation') or {}).get('landing_policy_mode') or '')[:100],
                'landing_source': str((row.get('reference_contract_validation') or {}).get('landing_source') or '')[:160],
                'yaw_source': str((row.get('reference_contract_validation') or {}).get('yaw_source') or '')[:100],
                'cad_position_pass': (row.get('reference_contract_validation') or {}).get('cad_position_pass') is True,
                'collision_pass': (row.get('reference_contract_validation') or {}).get('collision_pass') is True,
                'visibility_pass': (row.get('reference_contract_validation') or {}).get('visibility_pass') is True,
                'safe_frame_status': str((row.get('reference_contract_validation') or {}).get('safe_frame_status') or '')[:40],
                'safe_frame_pass': ((row.get('reference_contract_validation') or {}).get('safe_frame_pass')
                                    if (row.get('reference_contract_validation') or {}).get('safe_frame_pass') in (True, False)
                                    else None),
                'projection_method': str((row.get('reference_contract_validation') or {}).get('projection_method') or '')[:100],
                'width': max(0, int(_number((row.get('reference_contract_validation') or {}).get('width'), 0))),
                'height': max(0, int(_number((row.get('reference_contract_validation') or {}).get('height'), 0))),
                'pixel_origin': str((row.get('reference_contract_validation') or {}).get('pixel_origin') or '')[:40],
                'buffer_sha': str((row.get('reference_contract_validation') or {}).get('buffer_sha') or '')[:128],
                'proposal_id': str((row.get('reference_contract_validation') or {}).get('proposal_id') or '')[:120],
                'proposal_hash': str((row.get('reference_contract_validation') or {}).get('proposal_hash') or '')[:128],
                'must_show_subjects': copy.deepcopy((row.get('reference_contract_validation') or {}).get('must_show_subjects') or []),
                'must_validate': copy.deepcopy((row.get('reference_contract_validation') or {}).get('must_validate') or {}),
                'must_show_bounds': [
                    {
                        'subject': str(bound.get('subject') or '')[:160],
                        'x_min': round(_clamp(bound.get('x_min'), 0, 1, 0), 6),
                        'x_max': round(_clamp(bound.get('x_max'), 0, 1, 1), 6),
                        'y_min': round(_clamp(bound.get('y_min'), 0, 1, 0), 6),
                        'y_max': round(_clamp(bound.get('y_max'), 0, 1, 1), 6),
                    }
                    for bound in (row.get('reference_contract_validation') or {}).get('must_show_bounds') or []
                    if isinstance(bound, dict)
                ][:20],
            }} if isinstance(row.get('reference_contract_validation'), dict) else {}),
            **({'reference_proposal_id': str(row.get('reference_proposal_id'))[:120]}
               if row.get('reference_proposal_id') else {}),
            **({'reference_proposal_hash': str(row.get('reference_proposal_hash'))[:128]}
               if row.get('reference_proposal_hash') else {}),
            **({'origin_room_ids': [str(value)[:80] for value in row.get('origin_room_ids') or []][:8]}
               if isinstance(row.get('origin_room_ids'), list) else {}),
            **({'render_gate': render_gate} if render_gate is not None else {}),
        })

    model = {
        'schema_version': 2, 'model_id': str(payload.get('model_id') or new_id('model'))[:100],
        'coordinate_system': 'metres-y-up', 'width_m': width_m, 'depth_m': depth_m,
        'wall_height_m': wall_height, 'wall_thickness_m': wall_thickness,
        'scale': copy.deepcopy(payload.get('scale') or {
            'status': 'estimated', 'method': 'plan_extent', 'reference_length_m': width_m,
        }),
        'walls': walls, 'openings': openings, 'rooms': rooms,
        'fixed_objects': objects, 'cameras': cameras,
        'uncertainties': [str(value)[:300] for value in payload.get('uncertainties') or []][:40]
        + ([f'本地根据墙体两侧房间关系把 {len(reclassified_walls)} 段 AI 外墙候选改为内墙：{", ".join(reclassified_walls[:6])}'] if reclassified_walls else []),
        **({'cad_facts_hash': str(payload.get('cad_facts_hash') or '')[:128]} if payload.get('cad_facts_hash') else {}),
        **({'cad_to_model': copy.deepcopy(payload.get('cad_to_model'))} if isinstance(payload.get('cad_to_model'), dict) else {}),
        **({'model_to_cad': copy.deepcopy(payload.get('model_to_cad'))} if isinstance(payload.get('model_to_cad'), dict) else {}),
        **({'room_contracts': copy.deepcopy(payload.get('room_contracts'))}
           if isinstance(payload.get('room_contracts'), list) else {}),
        **({'reference_anchor_report': copy.deepcopy(payload.get('reference_anchor_report'))}
           if isinstance(payload.get('reference_anchor_report'), dict) else {}),
        **({'cad_semantic_derivation': copy.deepcopy(payload.get('cad_semantic_derivation'))}
           if isinstance(payload.get('cad_semantic_derivation'), dict) else {}),
    }
    model['geometry_report'] = validate_model(model)
    return upgrade_model_v2(model)


def validate_model(model: dict, floorplan_path: Optional[str] = None) -> dict:
    hard_errors: list[dict] = []
    warnings: list[dict] = []
    walls = model.get('walls') or []
    cad_manifest_locked = bool(
        model.get('cad_facts_hash')
        and isinstance(model.get('geometry_manifest'), dict)
        and (model.get('geometry_manifest') or {}).get('manifest_hash')
    )
    wall_ids = [str(wall.get('id') or '') for wall in walls]
    if len(walls) < 3:
        hard_errors.append({'code': 'too_few_walls', 'message': '整屋模型至少需要 3 段墙体'})
    if len(wall_ids) != len(set(wall_ids)):
        hard_errors.append({'code': 'duplicate_wall_id', 'message': '存在重复墙体 ID'})
    wall_map = {wall.get('id'): wall for wall in walls}
    for wall in walls:
        wall_length = _distance(wall.get('start') or {}, wall.get('end') or {})
        if wall_length < 0.08:
            hard_errors.append({'code': 'short_wall', 'wall_id': wall.get('id'), 'message': f"墙体 {wall.get('id')} 长度不足 0.08m"})
        elif wall_length < 0.2:
            warnings.append({
                'code': 'short_wall_review', 'wall_id': wall.get('id'),
                'message': f"墙体 {wall.get('id')} 只有 {wall_length:.2f}m；可能是外墙短折边，也可能是坐标误差，请对照原图复核",
            })

    # Openings are holes in wall solids, never missing graph edges.  Every
    # exterior wall endpoint must therefore meet another exterior segment.  A
    # dangling endpoint is a genuinely open building shell and cannot safely be
    # turned into a 3D model.
    for wall in walls:
        if wall.get('kind') != 'exterior':
            continue
        for endpoint_name in ('start', 'end'):
            point = wall.get(endpoint_name) or {}
            if not _point_on_other_wall(point, str(wall.get('id') or ''), walls, kind='exterior'):
                hard_errors.append({
                    'code': 'open_exterior_endpoint', 'wall_id': wall.get('id'),
                    'point': copy.deepcopy(point),
                    'message': f"外墙 {wall.get('id')} 的{('起点' if endpoint_name == 'start' else '终点')}悬空；外墙轮廓必须闭合，门窗应作为墙上开口而不是断墙",
                })
    for opening in model.get('openings') or []:
        wall = wall_map.get(opening.get('wall_id'))
        if not wall:
            hard_errors.append({'code': 'orphan_opening', 'opening_id': opening.get('id'), 'message': f"开口 {opening.get('id')} 未绑定墙体"})
            continue
        wall_length = _distance(wall['start'], wall['end'])
        if _number(opening.get('offset_m')) < 0 or _number(opening.get('offset_m')) + _number(opening.get('width_m')) > wall_length + 0.01:
            hard_errors.append({'code': 'opening_outside_wall', 'opening_id': opening.get('id'), 'message': f"开口 {opening.get('id')} 超出墙体范围"})
        if opening.get('review_status') == 'pending':
            warnings.append({'code': 'opening_pending', 'opening_id': opening.get('id'), 'message': f"开口 {opening.get('id')} 仍待确认"})
        if opening.get('kind') != 'open_connection':
            remaining = wall_length - _number(opening.get('offset_m')) - _number(opening.get('width_m'))
            if _number(opening.get('offset_m')) < 0.12 or remaining < 0.12:
                warnings.append({
                    'code': 'opening_near_wall_end', 'opening_id': opening.get('id'), 'wall_id': wall.get('id'),
                    'message': f"开口 {opening.get('id')} 几乎贴住墙体端点；请确认 AI 是否在门窗处错误截断了墙体",
                })
        for topology_issue in _opening_topology_issues(model, opening):
            if topology_issue['code'] == 'opening_spans_room_junction':
                message = (
                    f"开口 {opening.get('id')} 的起点、中点和终点对应不同房间邻接；"
                    "开口横跨房间交点，必须拆分、移动或拒绝"
                )
            else:
                message = (
                    f"开放连接 {opening.get('id')} 直接连通厨房与卧室；"
                    "必须人工改成正确门型或拒绝该候选"
                )
            hard_errors.append({
                'code': topology_issue['code'],
                'opening_id': opening.get('id'), 'wall_id': opening.get('wall_id'),
                'room_ids': topology_issue.get('room_ids') or [],
                **({'room_profiles': topology_issue.get('room_profiles') or []}
                   if topology_issue.get('room_profiles') else {}),
                **({'samples': copy.deepcopy(topology_issue.get('samples') or [])}
                   if topology_issue.get('samples') else {}),
                'message': message,
            })
    accepted_openings = [
        row for row in model.get('openings') or []
        if row.get('review_status') == 'accepted'
    ]
    for index, left in enumerate(accepted_openings):
        for right in accepted_openings[index + 1:]:
            if left.get('wall_id') != right.get('wall_id'):
                continue
            overlap = min(
                _number(left.get('offset_m')) + _number(left.get('width_m')),
                _number(right.get('offset_m')) + _number(right.get('width_m')),
            ) - max(_number(left.get('offset_m')), _number(right.get('offset_m')))
            if overlap <= .02:
                continue
            hard_errors.append({
                'code': 'overlapping_accepted_openings_same_wall',
                'wall_id': left.get('wall_id'), 'opening_id': left.get('id'),
                'other_opening_id': right.get('id'), 'overlap_m': round(overlap, 5),
                'message': (
                    f"同墙已接受开口 {left.get('id')} 与 {right.get('id')} "
                    f"重叠 {overlap:.3f}m；必须归并或拒绝其一"
                ),
            })
    if not model.get('rooms'):
        warnings.append({'code': 'no_rooms', 'message': '尚未定义房间地面；3D 中只会显示墙体'})
    if not model.get('openings'):
        warnings.append({'code': 'no_openings', 'message': '尚未定义任何门窗，整屋模型可能是封闭盒子'})
    if not model.get('cameras'):
        warnings.append({'code': 'no_cameras', 'message': '尚未从 3D 灰模保存机位'})
    for room in model.get('rooms') or []:
        if _polygon_area(room.get('polygon') or []) < 0.5:
            warnings.append({'code': 'small_room', 'room_id': room.get('id'), 'message': f"{room.get('label') or room.get('id')} 面积小于 0.5㎡"})
        room_semantics = f"{room.get('room_type') or ''} {room.get('label') or ''}".lower()
        enclosed = any(token in room_semantics for token in (
            'bed', 'sleep', '卧', 'bath', 'toilet', '卫生', '洗手', 'kitchen', '厨',
            'balcony', '阳台', 'storage', '储藏', 'closet', '衣帽',
        ))
        polygon = room.get('polygon') or []
        if not enclosed or len(polygon) < 3:
            continue
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            for gap_start, gap_end in _edge_uncovered_intervals(start, end, walls):
                gap_length = gap_end - gap_start
                if gap_length < 0.3:
                    continue
                gap_a, gap_b = _point_along(start, end, gap_start), _point_along(start, end, gap_end)
                issue = {
                    'code': 'enclosed_room_boundary_gap', 'room_id': room.get('id'),
                    'start': gap_a, 'end': gap_b, 'length_m': round(gap_length, 3),
                    'message': f"{room.get('label') or room.get('id')} 有约 {gap_length:.2f}m 边界没有对应墙体；门窗必须挂在连续墙上，不能用缺墙代替",
                }
                if cad_manifest_locked:
                    warnings.append({
                        **issue,
                        'code': 'cad_room_boundary_uses_wall_face_not_centerline',
                        'message': (
                            f"{room.get('label') or room.get('id')} 边界沿 CAD 墙面而非旧中心线；"
                            '已由同 revision GeometryManifest 的墙体投影与楼板覆盖门禁复核'
                        ),
                    })
                else:
                    hard_errors.append(issue)
    cad_derivation = model.get('cad_semantic_derivation') or {}
    if (model.get('cad_facts_hash')
            and cad_derivation.get('method') == 'gemini_room_polygon_on_audited_cad_raster_v1'):
        # CAD construction drawings often encode wall faces rather than one
        # centerline.  A semantic room partition can therefore sit between the
        # two proven faces or cross an intentional open-plan boundary.  The CAD
        # route separately runs validate_cad_model() and its immutable hash;
        # keep these gaps visible, but do not make the generic image validator
        # veto an otherwise handle-backed CAD shell.
        retained, cad_partition_warnings = [], []
        for issue in hard_errors:
            if issue.get('code') == 'enclosed_room_boundary_gap':
                cad_partition_warnings.append({
                    **copy.deepcopy(issue), 'code': 'cad_semantic_partition_without_wall',
                    'message': f"{issue.get('message')}（CAD 双线墙已由独立硬门禁复核，此处作为语义分区证据保留）",
                })
            else:
                retained.append(issue)
        hard_errors = retained
        warnings.extend(cad_partition_warnings)
    report = {'hard_errors': hard_errors, 'warnings': warnings, 'checked_at': time.time()}
    if floorplan_path:
        report['image_alignment_score'] = plan_alignment_score(model, floorplan_path)
    return report


def model_hash(model: dict) -> str:
    stable = upgrade_model_v2(model)
    stable.pop('geometry_report', None)
    stable.pop('semantic_report', None)
    # Camera captures are invalidated by shell/object changes, not by adding a
    # second camera.  The individual camera is hashed separately by the route.
    stable.pop('cameras', None)
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def project_view(project: dict) -> dict:
    out = runtime_project_copy(project)
    # 内部预览包含完整 prompt 与动态确认短语，仅用于服务重启恢复，绝不出现在 API view。
    out.pop('pano_paid_previews', None)
    project_id = str(out.get('project_id') or '')
    out['floorplan_url'] = to_url(out.get('floorplan_path'))
    out['cad_geometry_read_only'] = out.get('source_type') == 'cad'
    parse_report = out.get('parse_report') if isinstance(out.get('parse_report'), dict) else {}
    for candidate in parse_report.get('candidate_plans') or []:
        candidate_id = str(candidate.get('candidate_id') or '')
        candidate['preview_url'] = (
            f'/api/whole-home/projects/{project_id}/cad/candidates/{candidate_id}/preview'
            if project_id and candidate_id and candidate.get('preview_path') else '')
    if parse_report:
        parse_report['report_url'] = to_url(parse_report.get('report_path'))
    for capture in out.get('captures') or []:
        for key in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'subject_id', 'plan_overlay'):
            capture[f'{key}_url'] = to_url(capture.get(f'{key}_path'))
    for pano in out.get('pano_captures') or []:
        channels = (pano.get('manifest') or {}).get('channels') or {}
        pano['channel_urls'] = {
            key: to_url(value) for key, value in channels.items() if value
        }
        for key in ('edited_rgb', 'repaired_rgb'):
            pano[f'{key}_url'] = to_url(pano.get(f'{key}_path'))
    for plan in out.get('auto_camera_plans') or []:
        for candidate in plan.get('candidates') or []:
            candidate['preview_url'] = to_url(candidate.get('preview_path'))
        for sheet in plan.get('contact_sheets') or []:
            sheet['url'] = to_url(sheet.get('path'))
    reference_contract = out.get('reference_contract') if isinstance(out.get('reference_contract'), dict) else {}
    for slot in reference_contract.get('slots') or []:
        asset = slot.get('reference_asset') if isinstance(slot.get('reference_asset'), dict) else {}
        slot_id = str(slot.get('slot_id') or '')
        if asset.get('status') == 'verified' and project_id and slot_id:
            asset['url'] = f'/api/whole-home/projects/{project_id}/reference-assets/{slot_id}'
    try:
        from .whole_home_learning import project_learning_projection
        out['learning'] = project_learning_projection(project)
    except Exception as ex:
        logger.warning(f'[整屋学习] 项目学习状态关联失败: {ex}')
        out['learning'] = {
            'training_consent': {'project_id': out.get('project_id'), 'allowed': False},
            'counts': {'pass': 0, 'backup': 0, 'reject': 0, 'unreviewed': 0},
            'covered_room_ids': [], 'uncovered_room_ids': [],
            'covered_room_count': 0, 'selected_room_count': 0,
        }
    return _public_reference_artifacts(out, project.get('reference_contract') or {})


def run_view(run: dict, *, include_learning: bool = True) -> dict:
    out = copy.deepcopy(run)
    out['floorplan_url'] = to_url(out.get('floorplan_path'))
    out['floor_url'] = to_url(out.get('floor_path'))
    out['style_ref_url'] = to_url(out.get('style_ref_path'))
    for result in out.get('results') or []:
        result['url'] = to_url(result.get('path'))
        result['thumb'] = result_thumb_url(result.get('path'))
        result['structure_url'] = to_url(result.get('structure_path'))
        result['api_original_url'] = to_url(result.get('api_original_path'))
        result['material_url'] = to_url(result.get('material_path'))
        result['corrected_url'] = to_url(result.get('corrected_path'))
        result['final_url'] = to_url(result.get('final_path'))
        for attempt in result.get('attempts') or []:
            for key in ('structure', 'api_original', 'material', 'corrected', 'final'):
                attempt[f'{key}_url'] = to_url(attempt.get(f'{key}_path'))
            structure_gate = attempt.get('structure_local_gate') or {}
            if isinstance(structure_gate, dict):
                structure_gate['overlay_url'] = to_url(structure_gate.get('overlay_path'))
            for material_attempt in attempt.get('material_attempts') or []:
                for key in ('api_original', 'material', 'corrected', 'final'):
                    material_attempt[f'{key}_url'] = to_url(material_attempt.get(f'{key}_path'))
                final_gate = material_attempt.get('final_local_gate') or {}
                if isinstance(final_gate, dict):
                    final_gate['overlay_url'] = to_url(final_gate.get('overlay_path'))
    try:
        if not include_learning:
            terminal = str(run.get('status') or '') in ('done', 'partial', 'failed', 'cancelled')
            out['human_review'] = copy.deepcopy(run.get('human_review') or {
                'round_status': 'awaiting_human_review' if terminal and out.get('results') else 'working',
                'reviewable_count': 0, 'pending_count': 0, 'reviewables': [],
            })
        else:
            from .whole_home_learning import get_run_review_state
            out['human_review'] = get_run_review_state(run)
    except Exception as ex:
        logger.warning(f'[整屋学习] 任务人工评审状态关联失败: {ex}')
        out['human_review'] = {
            'round_status': 'working' if run.get('status') not in ('done', 'partial', 'failed')
            else 'awaiting_human_review',
            'reviewable_count': 0, 'pending_count': 0, 'reviewables': [],
        }
    return _public_reference_artifacts(out, run.get('reference_contract_snapshot') or {})


def save_capture_data(project_id: str, capture_id: str, kind: str, data_url: str) -> str:
    return _save_image_data_url(project_id, capture_id, kind, data_url, max_bytes=16_000_000)


def save_pano_data(project_id: str, pano_id: str, kind: str, data_url: str, *,
                   capture_id: str = '') -> str:
    """球面全景通道落盘:ERP/atlas PNG 上限放宽到 48MB(3840×1920 档)。"""
    folder_key = os.path.join('panos', pano_id, capture_id) if capture_id else os.path.join('panos', pano_id)
    return _save_image_data_url(
        project_id, folder_key, kind, data_url, max_bytes=48_000_000)


def save_pano_image_file(project_id: str, pano_id: str, kind: str, image, *,
                         capture_id: str = '') -> str:
    """后端生成的 ERP 通道以 PNG 落盘(与上传的 atlas 同目录)。"""
    parts = [ASSET_DIR, os.path.basename(project_id), 'panos', os.path.basename(pano_id)]
    if capture_id:
        parts.append(os.path.basename(capture_id))
    folder = os.path.join(*parts)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{kind}.png')
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    image.save(temporary, 'PNG')
    os.replace(temporary, path)
    return path


def pano_file_sha256(path: str) -> str:
    """计算 manifest 通道文件的字节 hash，防止同路径内容被替换而不失效。"""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


_PANO_MANIFEST_HASH_FIELDS = (
    'schema_version', 'capture_id', 'capture_revision', 'pano_id', 'projection',
    'coordinate_system', 'camera_center_m',
    'canonical_forward', 'heading_deg', 'pitch_deg', 'roll_deg', 'horizontal_fov_deg',
    'vertical_fov_deg', 'erp_width', 'erp_height', 'cube_face_size', 'cube_face_order',
    'near_m', 'far_m', 'depth_encoding', 'normal_encoding', 'model_facts_hash',
    'material_graph_hash', 'lighting_hash', 'scene_recipe_id', 'scene_hash',
    'render_contract', 'channels', 'channel_hashes',
)


def pano_manifest_hash(manifest: dict) -> str:
    """source_hash 覆盖除生成结果与 QA 以外的全部 manifest 字段(文档 §10)。

    结构、机位、projection、face order、near/far、材质/灯光 hash 任一变化都会
    使旧 pano capture 失效,与 perspective 的 _capture_hash 思路一致。
    """
    payload = {key: manifest.get(key) for key in _PANO_MANIFEST_HASH_FIELDS}
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _save_image_data_url(project_id: str, folder_key: str, kind: str, data_url: str,
                         *, max_bytes: int) -> str:
    match = re.match(r'^data:image/(png|jpeg|webp);base64,(.+)$', data_url or '', flags=re.I | re.S)
    if not match:
        raise ValueError(f'{kind} 不是有效的图片 Data URL')
    extension = {'jpeg': 'jpg'}.get(match.group(1).lower(), match.group(1).lower())
    raw = base64.b64decode(match.group(2), validate=True)
    if len(raw) > max_bytes:
        raise ValueError(f'{kind} 图片超过 {max_bytes // 1_000_000}MB')
    folder = os.path.join(ASSET_DIR, os.path.basename(project_id), os.path.basename(folder_key))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{kind}.{extension}')
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    with open(temporary, 'xb') as handle:
        handle.write(raw)
    try:
        with Image.open(temporary) as image:
            image.verify()
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
    return path


_AUTO_CAMERA_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'summary': {'type': 'STRING'},
        'selections': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'candidate_id': {'type': 'STRING'}, 'room_id': {'type': 'STRING'},
            'rank': {'type': 'INTEGER'}, 'visual_score': {'type': 'NUMBER'},
            'reason': {'type': 'STRING'},
            'strengths': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
            'risks': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        }, 'required': ['candidate_id', 'room_id', 'rank', 'visual_score', 'reason', 'strengths', 'risks']}},
    },
    'required': ['summary', 'selections'],
}


def _safe_asset_name(value: Any, fallback: str) -> str:
    cleaned = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(value or '')).strip('_')[:80]
    return cleaned or fallback


def _save_camera_contact_sheet(project_id: str, plan_id: str, room_id: str,
                               candidates: list[dict]) -> str:
    cell_width, cell_height = 320, 240
    label_height = 30
    columns = min(3, max(1, len(candidates)))
    rows = int(math.ceil(len(candidates) / columns))
    sheet = Image.new('RGB', (columns * cell_width, rows * (cell_height + label_height)), '#ede9e1')
    draw = ImageDraw.Draw(sheet)
    for index, candidate in enumerate(candidates):
        x = (index % columns) * cell_width
        y = (index // columns) * (cell_height + label_height)
        with Image.open(candidate['preview_path']) as source:
            preview = ImageOps.fit(source.convert('RGB'), (cell_width, cell_height), Image.Resampling.LANCZOS)
        sheet.paste(preview, (x, y))
        label = f"{candidate['candidate_id']} | local {candidate.get('local_score', 0):.1f}"
        draw.rectangle((x, y + cell_height, x + cell_width, y + cell_height + label_height), fill='#171717')
        draw.text((x + 8, y + cell_height + 8), label, fill='white')
    folder = os.path.join(
        ASSET_DIR, _safe_asset_name(project_id, 'project'), 'auto_camera_plans',
        _safe_asset_name(plan_id, 'plan'),
    )
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"sheet_{_safe_asset_name(room_id, 'room')}.jpg")
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    sheet.save(temporary, 'JPEG', quality=90)
    os.replace(temporary, path)
    return path


def _segment_crossing(first_start: dict, first_end: dict, second_start: dict,
                      second_end: dict) -> Optional[tuple[float, float]]:
    ax, az = _number(first_start.get('x')), _number(first_start.get('z'))
    bx, bz = _number(first_end.get('x')), _number(first_end.get('z'))
    cx, cz = _number(second_start.get('x')), _number(second_start.get('z'))
    dx, dz = _number(second_end.get('x')), _number(second_end.get('z'))
    rx, rz, sx, sz = bx - ax, bz - az, dx - cx, dz - cz
    denominator = rx * sz - rz * sx
    if abs(denominator) < 1e-9:
        return None
    qx, qz = cx - ax, cz - az
    first_t = (qx * sz - qz * sx) / denominator
    second_t = (qx * rz - qz * rx) / denominator
    if 0 <= first_t <= 1 and 0 <= second_t <= 1:
        return first_t, second_t
    return None


def _line_of_sight(model: dict, start: dict, end: dict) -> bool:
    for wall in model.get('walls') or []:
        crossing = _segment_crossing(start, end, wall.get('start') or {}, wall.get('end') or {})
        if crossing and .025 < crossing[0] < .965:
            return False
    return True


def _line_of_sight_via_opening(model: dict, start: dict, end: dict, opening: dict) -> bool:
    """Require the sight ray to cross exactly the nominated door/open connection."""
    portal_wall_id = str(opening.get('wall_id') or '')
    crossed_portal = False
    for wall in model.get('walls') or []:
        crossing = _segment_crossing(start, end, wall.get('start') or {}, wall.get('end') or {})
        if not crossing or not (.025 < crossing[0] < .965):
            continue
        if str(wall.get('id') or '') == portal_wall_id:
            wall_length = max(.001, _distance(wall.get('start') or {}, wall.get('end') or {}))
            crossing_m = crossing[1] * wall_length
            opening_start = _number(opening.get('offset_m'))
            opening_end = opening_start + _number(opening.get('width_m'))
            if opening_start - .02 <= crossing_m <= opening_end + .02:
                crossed_portal = True
                continue
        return False
    return crossed_portal


def _portal_origin_samples(model: dict, room: dict, opening: dict) -> list[dict]:
    if opening.get('kind') not in ('door', 'open_connection'):
        return []
    wall = next((
        row for row in model.get('walls') or []
        if str(row.get('id') or '') == str(opening.get('wall_id') or '')
    ), None)
    center = _opening_center(model, opening)
    if not wall or not center:
        return []
    start, end = wall.get('start') or {}, wall.get('end') or {}
    dx = _number(end.get('x')) - _number(start.get('x'))
    dz = _number(end.get('z')) - _number(start.get('z'))
    length = math.hypot(dx, dz)
    if length < .2 or _number(opening.get('width_m')) < .45:
        return []
    tangent = {'x': dx / length, 'z': dz / length}
    normal = {'x': -dz / length, 'z': dx / length}
    plus = {'x': center['x'] + normal['x'] * .12, 'z': center['z'] + normal['z'] * .12}
    minus = {'x': center['x'] - normal['x'] * .12, 'z': center['z'] - normal['z'] * .12}
    plus_inside = _point_in_polygon(plus, room.get('polygon') or [])
    minus_inside = _point_in_polygon(minus, room.get('polygon') or [])
    if plus_inside != minus_inside:
        adjacent = {'x': -normal['x'], 'z': -normal['z']} if plus_inside else normal
    else:
        polygon = room.get('polygon') or []
        room_centre = {
            'x': sum(_number(point.get('x')) for point in polygon) / max(1, len(polygon)),
            'z': sum(_number(point.get('z')) for point in polygon) / max(1, len(polygon)),
        }
        dot = ((room_centre['x'] - center['x']) * normal['x']
               + (room_centre['z'] - center['z']) * normal['z'])
        adjacent = {'x': -normal['x'], 'z': -normal['z']} if dot >= 0 else normal
    # Stay inside the actual clear opening width while exploring enough of the
    # adjacent 2D space to route around a dining table or TV cabinet.  The 8cm
    # jamb reserve also keeps the eventual sight ray away from opening edges.
    clear_half_width = max(0.0, _number(opening.get('width_m')) / 2 - .08)
    tangent_offsets = sorted({
        round(clear_half_width * ratio, 5)
        for ratio in (-1, -.5, 0, .5, 1)
    })
    samples = []
    for distance_m in (.35, .5, .65, .8, 1.0, 1.2):
        for tangent_offset_m in tangent_offsets:
            samples.append({
                'position': {
                    'x': round(
                        center['x'] + adjacent['x'] * distance_m
                        + tangent['x'] * tangent_offset_m, 5),
                    'z': round(
                        center['z'] + adjacent['z'] * distance_m
                        + tangent['z'] * tangent_offset_m, 5),
                },
                'distance_m': distance_m,
                'tangent_offset_m': tangent_offset_m,
            })
    return samples


def _doorway_inside_samples(model: dict, room: dict, opening: dict) -> list[dict]:
    """Sample only the target-room side of a stable, accepted doorway."""
    if (opening.get('kind') not in ('door', 'open_connection')
            or opening.get('review_status') != 'accepted'
            or _opening_topology_issues(model, opening)):
        return []
    target_room_id = str(room.get('id') or '')
    adjacency = _opening_adjacency_samples(model, opening)
    if not adjacency:
        return []
    target_sides = []
    for row in adjacency:
        negative = target_room_id in set(row.get('negative') or [])
        positive = target_room_id in set(row.get('positive') or [])
        if negative == positive:
            return []
        target_sides.append('negative' if negative else 'positive')
    if len(set(target_sides)) != 1:
        return []
    wall = next((
        row for row in model.get('walls') or []
        if str(row.get('id') or '') == str(opening.get('wall_id') or '')
    ), None)
    center = _opening_center(model, opening)
    if not wall or not center:
        return []
    start, end = wall.get('start') or {}, wall.get('end') or {}
    dx = _number(end.get('x')) - _number(start.get('x'))
    dz = _number(end.get('z')) - _number(start.get('z'))
    length = math.hypot(dx, dz)
    if length < .2 or _number(opening.get('width_m')) < .25:
        return []
    tangent = {'x': dx / length, 'z': dz / length}
    normal = {'x': -dz / length, 'z': dx / length}
    inward_sign = -1 if target_sides[0] == 'negative' else 1
    inward = {'x': normal['x'] * inward_sign, 'z': normal['z'] * inward_sign}
    clear_half_width = max(0.0, _number(opening.get('width_m')) / 2 - .08)
    tangent_offsets = sorted({
        round(clear_half_width * ratio, 5)
        for ratio in (0, -.45, .45, -.9, .9)
    }, key=lambda value: (abs(value), value))
    samples = []
    for inset_m in (.10, .18, .25, .32):
        for tangent_offset_m in tangent_offsets:
            samples.append({
                'position': {
                    'x': round(
                        center['x'] + inward['x'] * inset_m
                        + tangent['x'] * tangent_offset_m, 5),
                    'z': round(
                        center['z'] + inward['z'] * inset_m
                        + tangent['z'] * tangent_offset_m, 5),
                },
                'inset_m': inset_m,
                'tangent_offset_m': tangent_offset_m,
            })
    return samples


def _point_blocked_by_object(point: dict, objects: list[dict], margin: float = .28) -> bool:
    for row in objects:
        if not row.get('blocks_camera', True) or row.get('review_status') == 'rejected':
            continue
        min_x, min_z, max_x, max_z = _aabb(_rotated_footprint(row))
        if min_x - margin <= _number(point.get('x')) <= max_x + margin \
                and min_z - margin <= _number(point.get('z')) <= max_z + margin:
            return True
    return False


def _segment_object_entry(start: dict, end: dict, row: dict, *, margin: float = .06) -> Optional[float]:
    """Return where a 2D sight segment first enters an object's expanded AABB."""
    min_x, min_z, max_x, max_z = _aabb(_rotated_footprint(row))
    bounds = ((min_x - margin, max_x + margin), (min_z - margin, max_z + margin))
    origins = (_number(start.get('x')), _number(start.get('z')))
    directions = (
        _number(end.get('x')) - origins[0],
        _number(end.get('z')) - origins[1],
    )
    entry, leave = 0.0, 1.0
    for origin, direction, (low, high) in zip(origins, directions, bounds):
        if abs(direction) < 1e-9:
            if origin < low or origin > high:
                return None
            continue
        first, second = (low - origin) / direction, (high - origin) / direction
        if first > second:
            first, second = second, first
        entry, leave = max(entry, first), min(leave, second)
        if entry > leave:
            return None
    return entry if .02 < entry < .96 else None


def _camera_horizontal_fov(focal_length_mm: float, aspect_ratio: str) -> float:
    width, height = (int(value) for value in aspect_ratio.split(':'))
    aspect = width / max(height, 1)
    vertical = 2 * math.atan(24 / (2 * max(focal_length_mm, 1)))
    return 2 * math.atan(math.tan(vertical / 2) * aspect)


def _visible_camera_roles(model: dict, room: dict, position: dict, target: dict,
                          focal_length_mm: float, aspect_ratio: str,
                          objects: list[dict], openings: list[dict],
                          portal_opening: Optional[dict] = None) -> tuple[set[str], set[str]]:
    direction = math.atan2(_number(target.get('z')) - _number(position.get('z')),
                           _number(target.get('x')) - _number(position.get('x')))
    half_fov = _camera_horizontal_fov(focal_length_mm, aspect_ratio) / 2
    visible: set[str] = set()
    occluded: set[str] = set()
    focus_rows = [
        (_canonical_object_role(row), row.get('position') or {}, str(row.get('id') or '')) for row in objects
        if row.get('review_status') != 'rejected'
    ]
    focus_rows.extend((
        str(row.get('kind') or 'opening'), _opening_center(model, row) or {}, ''
    ) for row in openings)
    for role, point, target_object_id in focus_rows:
        if not point:
            continue
        angle = math.atan2(_number(point.get('z')) - _number(position.get('z')),
                           _number(point.get('x')) - _number(position.get('x')))
        difference = abs(math.atan2(math.sin(angle - direction), math.cos(angle - direction)))
        line_clear = (
            _line_of_sight_via_opening(model, position, point, portal_opening)
            if portal_opening else _line_of_sight(model, position, point)
        )
        if difference > half_fov * .92 or not line_clear:
            continue
        blocked = False
        for blocker in objects:
            blocker_id = str(blocker.get('id') or '')
            if (blocker_id == target_object_id or blocker.get('review_status') == 'rejected'
                    or not blocker.get('blocks_camera', True)):
                continue
            if target_object_id and frozenset((role, _canonical_object_role(blocker))) in _ALLOWED_OBJECT_OVERLAPS:
                continue
            if _segment_object_entry(position, point, blocker) is not None:
                blocked = True
                break
        if blocked:
            occluded.add(role)
        else:
            visible.add(role)
    return visible, occluded - visible


def _semantic_camera_gate(profile: str, visible_roles: set[str], *,
                          origin_scope: str = 'inside_room') -> tuple[bool, list[str]]:
    missing = []
    if profile == 'bedroom' and 'bed' not in visible_roles:
        missing.append('bed')
    elif profile == 'bathroom':
        fixtures = visible_roles.intersection({'basin', 'toilet', 'shower_zone'})
        if len(fixtures) < 2:
            missing.append('at_least_two:bathroom_fixtures')
    elif profile == 'kitchen':
        if 'kitchen_run' not in visible_roles:
            missing.append('kitchen_run')
        if not visible_roles.intersection({'sink', 'hob', 'fridge'}):
            missing.append('sink_or_hob_or_fridge')
    elif profile == 'living_room':
        if 'sofa' not in visible_roles:
            missing.append('sofa')
        if not visible_roles.intersection({'tv', 'open_connection', 'window'}):
            missing.append('tv_or_main_opening')
    elif profile == 'foyer' and not visible_roles.intersection({'door', 'open_connection'}):
        missing.append('entrance_or_main_connection')
    elif profile == 'balcony':
        if origin_scope != 'adjacent_portal' and 'open_connection' not in visible_roles:
            missing.append('balcony_connection')
        if not visible_roles.intersection({'window', 'balcony_rail', 'washing_machine'}):
            missing.append('window_rail_or_balcony_object')
    elif profile == 'other' and not visible_roles:
        missing.append('semantic_focus')
    return not missing, missing


_CAMERA_TARGET_HEIGHTS_M = {
    'bedroom': .65,
    'living_room': .75,
    'kitchen': .75,
    'bathroom': .72,
    'foyer': .72,
    'balcony': .72,
    'other': .75,
}


def _camera_target_height(profile: str, focus_kind: str) -> float:
    """Keep the floor in frame while retaining the audited semantic focus."""
    base = _CAMERA_TARGET_HEIGHTS_M.get(profile, .75)
    if profile == 'bedroom' and focus_kind == 'object:bed':
        return .62
    return base


def generate_semantic_camera_candidates(model: dict, *, aspect_ratio: str = '4:3',
                                        max_per_room: int = 8) -> dict:
    """Generate strict in-room poses, then an audited portal fallback when necessary."""
    if aspect_ratio not in ('4:3', '16:9', '3:4', '9:16'):
        raise ValueError('不支持的机位画幅')
    value = upgrade_model_v2(model)
    max_per_room = max(1, min(int(max_per_room), 8))
    semantic_report = validate_semantic_layout(value)
    room_errors: dict[str, list[str]] = {}
    global_errors = []
    for issue in semantic_report.get('hard_errors') or []:
        room_id = str(issue.get('room_id') or '')
        message = str(issue.get('message') or issue.get('code'))
        (room_errors.setdefault(room_id, []) if room_id else global_errors).append(message)
    contract_map = {str(row.get('room_id') or ''): row for row in value.get('room_contracts') or []}
    active_objects = [
        row for row in value.get('fixed_objects') or []
        if row.get('review_status') != 'rejected'
    ]
    all_rooms = [room for room in value.get('rooms') or [] if len(room.get('polygon') or []) >= 3]
    candidates: list[dict] = []
    room_pools, blocked_rooms = [], []
    rejection_by_room: dict[str, dict] = {}
    fractions = (.12, .24, .38, .5, .62, .76, .88)

    def add_missing(summary: dict, missing_roles: list[str], *, portal: bool = False,
                    doorway: bool = False) -> None:
        key = ('portal_semantic_missing' if portal else
               'doorway_semantic_missing' if doorway else 'semantic_missing')
        roles_key = ('portal_semantic_missing_roles' if portal else
                     'doorway_semantic_missing_roles' if doorway else 'semantic_missing_roles')
        summary[key] += 1
        for role in missing_roles:
            summary[roles_key][role] = summary[roles_key].get(role, 0) + 1

    def make_candidate(room_id: str, room_label: str, profile: str, required_roles: set[str],
                       room_area: float, diagonal: float, centre: dict, position: dict,
                       focus: dict, focus_kind: str, focal: int, clearance: float,
                       visible_roles: set[str], occluded_roles: set[str], origin_scope: str,
                       portal_opening: Optional[dict] = None,
                       origin_room_ids: Optional[list[str]] = None,
                       entry_opening: Optional[dict] = None,
                       doorway_sample: Optional[dict] = None) -> dict:
        view_depth = _distance(position, focus)
        semantic_denominator = max(1, len(required_roles))
        semantic_coverage = min(1, len(visible_roles.intersection(required_roles)) / semantic_denominator)
        depth_score = min(1, view_depth / (diagonal * .72))
        clearance_score = min(1, clearance / .9)
        edge_score = min(1, _distance(position, centre) / (diagonal * .45))
        focal_score = 1 if focal in (24, 28) else .45
        raw_score = min(
            100, 40 * semantic_coverage + 20 * depth_score + 15 * clearance_score
            + 15 * edge_score + 10 * focal_score,
        )
        occlusion_penalty = min(18, len(occluded_roles) * 8)
        focal_penalty = 0 if focal in (24, 28) else 12
        local_score = max(0, raw_score - occlusion_penalty - focal_penalty)
        portal_id = str((portal_opening or {}).get('id') or '')
        entry_id = str((entry_opening or {}).get('id') or '')
        target_height = _camera_target_height(profile, focus_kind)
        return {
            'room_id': room_id, 'room_label': room_label, 'origin_scope': origin_scope,
            **({'portal_opening_id': portal_id} if portal_id else {}),
            **({'entry_opening_id': entry_id} if entry_id else {}),
            **({'origin_room_ids': origin_room_ids or []} if origin_scope == 'adjacent_portal' else {}),
            'local_score': round(local_score, 2),
            'metrics': {
                'semantic_gate': True, 'safety_gate': True, 'origin_scope': origin_scope,
                'room_profile': profile, 'visible_roles': sorted(visible_roles),
                'occluded_roles': sorted(occluded_roles),
                'occlusion_penalty': occlusion_penalty,
                'required_roles': sorted(required_roles), 'missing_roles': [],
                'wall_clearance_m': round(clearance, 3), 'view_depth_m': round(view_depth, 3),
                'focus_kind': focus_kind, 'room_area_m2': round(room_area, 2),
                'focal_fallback': focal == 20, 'deferred_focal': focal == 20,
                'focal_penalty': focal_penalty,
                'target_height_m': target_height,
                **({'portal_opening_id': portal_id} if portal_id else {}),
                **({'entry_opening_id': entry_id} if entry_id else {}),
                **({'doorway_inset_m': doorway_sample.get('inset_m'),
                    'doorway_tangent_offset_m': doorway_sample.get('tangent_offset_m')}
                   if doorway_sample else {}),
            },
            'camera': {
                'position': {'x': round(position['x'], 5), 'y': 1.55, 'z': round(position['z'], 5)},
                'target': {'x': round(_number(focus.get('x')), 5), 'y': target_height,
                           'z': round(_number(focus.get('z')), 5)},
                'focal_length_mm': focal, 'room_id': room_id, 'enabled': True,
                'source': 'auto_geometry', 'origin_scope': origin_scope,
                **({'portal_opening_id': portal_id, 'origin_room_ids': origin_room_ids or []}
                   if portal_id else {}),
                **({'entry_opening_id': entry_id} if entry_id else {}),
            },
        }

    for room_index, room in enumerate(value.get('rooms') or []):
        if not room.get('selected', True) or len(room.get('polygon') or []) < 3:
            continue
        room_id = str(room.get('id') or '')
        room_label = str(room.get('label') or room_id)
        profile = str(room.get('semantic_profile') or _canonical_room_profile(room))
        summary = {
            'position_samples': 0, 'position_outside_room': 0,
            'position_inset_rejected': 0, 'position_object_collision': 0,
            'focus_points': 0, 'view_too_short': 0, 'los_blocked': 0,
            'semantic_missing': 0, 'semantic_missing_roles': {},
            'occluded_role_hits': 0, 'occlusion_gate_rejected': 0, 'raw_accepted': 0,
            'diversity_redundant': 0, 'portal_openings': 0, 'portal_samples': 0,
            'portal_outside_whole_home': 0, 'portal_inside_target_room': 0,
            'portal_topology_rejected': 0, 'portal_nonadjacent_origin': 0,
            'portal_wall_clearance': 0, 'portal_object_collision': 0,
            'portal_view_too_short': 0, 'portal_los_blocked': 0,
            'portal_semantic_missing': 0, 'portal_semantic_missing_roles': {},
            'portal_occluded_role_hits': 0, 'portal_occlusion_gate_rejected': 0,
            'portal_accepted_raw': 0,
            'doorway_openings': 0, 'doorway_samples': 0,
            'doorway_inside_room': 0, 'doorway_outside_room': 0,
            'doorway_other_room': 0, 'doorway_other_wall_clearance': 0,
            'doorway_object_collision': 0, 'doorway_view_too_short': 0,
            'doorway_los_blocked': 0, 'doorway_semantic_missing': 0,
            'doorway_semantic_missing_roles': {}, 'doorway_occluded_role_hits': 0,
            'doorway_occlusion_gate_rejected': 0, 'doorway_accepted_raw': 0,
            'base_focal_accepted_raw': 0, 'deferred_20mm_accepted_raw': 0,
            'hard_error_blocked': 0,
        }
        rejection_by_room[room_id] = summary
        blocked_reasons = list(global_errors) + list(room_errors.get(room_id) or [])
        room_objects = [row for row in active_objects if str(row.get('room_id') or '') == room_id]
        opening_rows = []
        for opening in value.get('openings') or []:
            if (opening.get('review_status') != 'accepted'
                    or _opening_topology_issues(value, opening)):
                continue
            center = _opening_center(value, opening)
            if center and _point_within_polygon_tolerance(center, room.get('polygon') or [], .3):
                opening_rows.append(opening)
        if blocked_reasons:
            summary['hard_error_blocked'] = len(blocked_reasons)
            blocked = {
                'room_id': room_id, 'room_label': room_label, 'status': 'blocked',
                'reasons': blocked_reasons, 'candidate_ids': [],
                'rejection_summary': copy.deepcopy(summary),
            }
            room_pools.append(blocked)
            blocked_rooms.append(blocked)
            continue

        xs = [_number(point.get('x')) for point in room['polygon']]
        zs = [_number(point.get('z')) for point in room['polygon']]
        min_x, max_x, min_z, max_z = min(xs), max(xs), min(zs), max(zs)
        centre = {'x': sum(xs) / len(xs), 'z': sum(zs) / len(zs)}
        diagonal = max(.5, math.hypot(max_x - min_x, max_z - min_z))
        area = _number(room.get('area_m2'), _polygon_area(room['polygon']))
        base_focal = 24 if area < 8 else 28
        positions = []
        for fx in fractions:
            for fz in fractions:
                summary['position_samples'] += 1
                point = {'x': min_x + (max_x - min_x) * fx,
                         'z': min_z + (max_z - min_z) * fz}
                if not _point_in_polygon(point, room['polygon']):
                    summary['position_outside_room'] += 1
                    continue
                boundary_clearance = min(
                    _point_segment(point, room['polygon'][index], room['polygon'][(index + 1) % len(room['polygon'])])[0]
                    for index in range(len(room['polygon']))
                )
                wall_clearance = min((
                    _point_segment(point, wall.get('start') or {}, wall.get('end') or {})[0]
                    for wall in value.get('walls') or []
                ), default=boundary_clearance)
                clearance = min(boundary_clearance, wall_clearance)
                if clearance < .35:
                    summary['position_inset_rejected'] += 1
                    continue
                if _point_blocked_by_object(point, room_objects):
                    summary['position_object_collision'] += 1
                    continue
                positions.append((point, clearance))

        required_roles = {
            role for group in (contract_map.get(room_id) or {}).get('required_role_groups') or []
            for role in group
        }
        semantic_points = [
            (row.get('position') or {}, _canonical_object_role(row)) for row in room_objects
            if row.get('position')
        ]
        focus_points = []
        required_points = [point for point, role in semantic_points if role in required_roles]
        if required_points:
            focus_points.append(({
                'x': sum(_number(point.get('x')) for point in required_points) / len(required_points),
                'z': sum(_number(point.get('z')) for point in required_points) / len(required_points),
            }, 'required_group'))
        focus_points.extend((point, f'object:{role}') for point, role in semantic_points)
        focus_points.extend((
            _opening_center(value, row) or {}, f"opening:{row.get('kind')}"
        ) for row in opening_rows)
        focus_points = [(point, kind) for point, kind in focus_points if point]
        summary['focus_points'] = len(focus_points)

        raw_by_focal: dict[int, list[dict]] = {}
        for focal in (base_focal, 20):
            focal_rows = []
            for position, clearance in positions:
                for focus, focus_kind in focus_points:
                    view_depth = _distance(position, focus)
                    if view_depth < .9:
                        summary['view_too_short'] += 1
                        continue
                    if not _line_of_sight(value, position, focus):
                        summary['los_blocked'] += 1
                        continue
                    visible_roles, occluded_roles = _visible_camera_roles(
                        value, room, position, focus, focal, aspect_ratio, room_objects, opening_rows)
                    summary['occluded_role_hits'] += len(occluded_roles)
                    gate, missing_roles = _semantic_camera_gate(profile, visible_roles)
                    if not gate:
                        if occluded_roles:
                            summary['occlusion_gate_rejected'] += 1
                        add_missing(summary, missing_roles)
                        continue
                    focal_rows.append(make_candidate(
                        room_id, room_label, profile, required_roles, area, diagonal, centre,
                        position, focus, focus_kind, focal, clearance,
                        visible_roles, occluded_roles, 'inside_room',
                    ))
            raw_by_focal[focal] = focal_rows

        summary['base_focal_accepted_raw'] = len(raw_by_focal.get(base_focal) or [])
        summary['deferred_20mm_accepted_raw'] = len(raw_by_focal.get(20) or [])
        summary['raw_accepted'] = sum(len(rows) for rows in raw_by_focal.values())

        # A portal pose is considered only if the strict room-interior pool has
        # no candidate at either preferred focal length or the deferred 20mm pool.
        if not any(raw_by_focal.values()):
            portal_openings = [
                opening for opening in opening_rows
                if opening.get('kind') in ('door', 'open_connection')
            ]
            summary['portal_openings'] = len(portal_openings)
            portal_by_focal: dict[int, list[dict]] = {}
            for focal in (base_focal, 20):
                portal_rows = []
                for portal in portal_openings:
                    direct_origin_room_ids = _portal_direct_origin_room_ids(
                        value, room_id, portal)
                    if not direct_origin_room_ids:
                        summary['portal_topology_rejected'] += 1
                        continue
                    for sample in _portal_origin_samples(value, room, portal):
                        summary['portal_samples'] += 1
                        position = sample['position']
                        origin_rooms = [
                            str(other.get('id') or '') for other in all_rooms
                            if str(other.get('id') or '') != room_id
                            and _point_in_polygon(position, other.get('polygon') or [])
                        ]
                        if not origin_rooms:
                            summary['portal_outside_whole_home'] += 1
                            continue
                        # The origin must be wholly in the room directly across this
                        # opening.  A tangent sample that slips into a third room is
                        # auditable evidence of an invalid portal, not a usable pose.
                        if (not set(origin_rooms).intersection(direct_origin_room_ids)
                                or any(room_id not in direct_origin_room_ids for room_id in origin_rooms)):
                            summary['portal_nonadjacent_origin'] += 1
                            continue
                        origin_rooms = sorted(
                            room_id for room_id in origin_rooms
                            if room_id in direct_origin_room_ids
                        )
                        if _point_in_polygon(position, room.get('polygon') or []):
                            summary['portal_inside_target_room'] += 1
                            continue
                        wall_clearance = min((
                            _point_segment(position, wall.get('start') or {}, wall.get('end') or {})[0]
                            for wall in value.get('walls') or []
                        ), default=1.0)
                        if wall_clearance < .18:
                            summary['portal_wall_clearance'] += 1
                            continue
                        if _point_blocked_by_object(position, active_objects):
                            summary['portal_object_collision'] += 1
                            continue
                        for focus, focus_kind in focus_points:
                            view_depth = _distance(position, focus)
                            if view_depth < .9:
                                summary['portal_view_too_short'] += 1
                                continue
                            if not _line_of_sight_via_opening(value, position, focus, portal):
                                summary['portal_los_blocked'] += 1
                                continue
                            visible_roles, occluded_roles = _visible_camera_roles(
                                value, room, position, focus, focal, aspect_ratio,
                                room_objects, opening_rows, portal,
                            )
                            summary['portal_occluded_role_hits'] += len(occluded_roles)
                            gate, missing_roles = _semantic_camera_gate(
                                profile, visible_roles, origin_scope='adjacent_portal')
                            if not gate:
                                if occluded_roles:
                                    summary['portal_occlusion_gate_rejected'] += 1
                                add_missing(summary, missing_roles, portal=True)
                                continue
                            portal_rows.append(make_candidate(
                                room_id, room_label, profile, required_roles, area, diagonal, centre,
                                position, focus, focus_kind, focal, wall_clearance, visible_roles,
                                occluded_roles, 'adjacent_portal', portal, sorted(origin_rooms),
                            ))
                portal_by_focal[focal] = portal_rows
            raw_by_focal = portal_by_focal
            summary['base_focal_accepted_raw'] = len(raw_by_focal.get(base_focal) or [])
            summary['deferred_20mm_accepted_raw'] = len(raw_by_focal.get(20) or [])
            summary['portal_accepted_raw'] = sum(len(rows) for rows in raw_by_focal.values())

        # Last safe geometry fallback: stand just inside a stable doorway.  It
        # is deliberately attempted only after both the strict interior pool
        # and the true adjacent-room portal pool have no raw candidate.
        if not any(raw_by_focal.values()):
            legal_entry_openings = [
                opening for opening in opening_rows
                if opening.get('kind') in ('door', 'open_connection')
                and not _opening_topology_issues(value, opening)
            ]
            summary['doorway_openings'] = len(legal_entry_openings)
            doorway_by_focal: dict[int, list[dict]] = {}
            for preferred_kind in ('door', 'open_connection'):
                kind_openings = [
                    opening for opening in legal_entry_openings
                    if opening.get('kind') == preferred_kind
                ]
                if not kind_openings:
                    continue
                attempted_by_focal: dict[int, list[dict]] = {}
                for focal in (base_focal, 20):
                    doorway_rows = []
                    for entry_opening in kind_openings:
                        nominated_wall_id = str(entry_opening.get('wall_id') or '')
                        for sample in _doorway_inside_samples(value, room, entry_opening):
                            summary['doorway_samples'] += 1
                            position = sample['position']
                            if not _point_in_polygon(position, room.get('polygon') or []):
                                summary['doorway_outside_room'] += 1
                                continue
                            summary['doorway_inside_room'] += 1
                            other_room_ids = [
                                str(other.get('id') or '') for other in all_rooms
                                if str(other.get('id') or '') != room_id
                                and _point_in_polygon(position, other.get('polygon') or [])
                            ]
                            if other_room_ids:
                                summary['doorway_other_room'] += 1
                                continue
                            other_wall_clearance = min((
                                _point_segment(
                                    position, wall.get('start') or {}, wall.get('end') or {})[0]
                                for wall in value.get('walls') or []
                                if str(wall.get('id') or '') != nominated_wall_id
                            ), default=1.0)
                            if other_wall_clearance < .18:
                                summary['doorway_other_wall_clearance'] += 1
                                continue
                            if _point_blocked_by_object(position, room_objects):
                                summary['doorway_object_collision'] += 1
                                continue
                            for focus, focus_kind in focus_points:
                                view_depth = _distance(position, focus)
                                if view_depth < .9:
                                    summary['doorway_view_too_short'] += 1
                                    continue
                                if not _line_of_sight(value, position, focus):
                                    summary['doorway_los_blocked'] += 1
                                    continue
                                visible_roles, occluded_roles = _visible_camera_roles(
                                    value, room, position, focus, focal, aspect_ratio,
                                    room_objects, opening_rows,
                                )
                                summary['doorway_occluded_role_hits'] += len(occluded_roles)
                                gate, missing_roles = _semantic_camera_gate(
                                    profile, visible_roles, origin_scope='doorway_inside')
                                if not gate:
                                    if occluded_roles:
                                        summary['doorway_occlusion_gate_rejected'] += 1
                                    add_missing(summary, missing_roles, doorway=True)
                                    continue
                                doorway_rows.append(make_candidate(
                                    room_id, room_label, profile, required_roles, area, diagonal,
                                    centre, position, focus, focus_kind, focal,
                                    min(sample['inset_m'], other_wall_clearance),
                                    visible_roles, occluded_roles, 'doorway_inside',
                                    entry_opening=entry_opening, doorway_sample=sample,
                                ))
                    attempted_by_focal[focal] = doorway_rows
                doorway_by_focal = attempted_by_focal
                if any(doorway_by_focal.values()):
                    break
            raw_by_focal = doorway_by_focal
            summary['base_focal_accepted_raw'] = len(raw_by_focal.get(base_focal) or [])
            summary['deferred_20mm_accepted_raw'] = len(raw_by_focal.get(20) or [])
            summary['doorway_accepted_raw'] = sum(len(rows) for rows in raw_by_focal.values())

        def pick_diverse(rows: list[dict], limit: int) -> list[dict]:
            rows.sort(key=lambda row: (
                -_number(row.get('local_score')), str(row['camera']['position']),
                str(row['camera']['target'])
            ))
            picked: list[dict] = []
            for row in rows:
                camera = row['camera']
                angle = math.atan2(
                    camera['target']['z'] - camera['position']['z'],
                    camera['target']['x'] - camera['position']['x'],
                )
                redundant = False
                for existing in picked:
                    other = existing['camera']
                    other_angle = math.atan2(
                        other['target']['z'] - other['position']['z'],
                        other['target']['x'] - other['position']['x'],
                    )
                    if (_distance(camera['position'], other['position']) < .75
                            and abs(math.atan2(math.sin(angle - other_angle),
                                               math.cos(angle - other_angle))) < math.radians(30)):
                        redundant = True
                        break
                if redundant:
                    summary['diversity_redundant'] += 1
                    continue
                picked.append(row)
                if len(picked) >= limit:
                    break
            return picked

        base_rows = raw_by_focal.get(base_focal) or []
        deferred_rows = raw_by_focal.get(20) or []
        if base_rows and deferred_rows and max_per_room > 1:
            deferred_limit = min(max(2, max_per_room // 2), len(deferred_rows), max_per_room - 1)
            diverse = pick_diverse(base_rows, max_per_room - deferred_limit)
            diverse.extend(pick_diverse(deferred_rows, max_per_room - len(diverse)))
        elif base_rows:
            diverse = pick_diverse(base_rows, max_per_room)
        else:
            diverse = pick_diverse(deferred_rows, max_per_room)

        for rank, row in enumerate(diverse, 1):
            camera = row['camera']
            candidate_id = f'camera_{_safe_asset_name(room_id, f"room_{room_index + 1}")}_{rank}'
            row['candidate_id'] = candidate_id
            label = (
                '相邻门口机位' if row.get('origin_scope') == 'adjacent_portal'
                else '门洞内侧机位' if row.get('origin_scope') == 'doorway_inside'
                else '语义机位'
            )
            row['camera'].update(
                id=f'camera_{candidate_id}', name=f'{room_label} · {label} {rank}',
                candidate_id=candidate_id,
            )

        for row in diverse:
            row['metrics']['rejection_summary'] = copy.deepcopy(summary)
        if not diverse:
            reasons = [f'{room_label} 严格房内机位无解，且未找到合法的相邻门口机位']
            if not focus_points:
                reasons.append('请补充该房间的语义主对象或有效开口焦点')
            if summary['position_inset_rejected'] or summary['position_object_collision']:
                reasons.append(
                    f"房内采样：{summary['position_inset_rejected']} 个未满足 0.35m 内缩，"
                    f"{summary['position_object_collision']} 个与物体安全区碰撞"
                )
            if (summary['semantic_missing'] or summary['portal_semantic_missing']
                    or summary['doorway_semantic_missing']):
                combined_missing = dict(summary['semantic_missing_roles'])
                for role, count in summary['portal_semantic_missing_roles'].items():
                    combined_missing[role] = combined_missing.get(role, 0) + count
                for role, count in summary['doorway_semantic_missing_roles'].items():
                    combined_missing[role] = combined_missing.get(role, 0) + count
                missing = ', '.join(
                    f'{role}×{count}' for role, count in sorted(combined_missing.items())
                )
                reasons.append(f'语义视锥缺失：{missing or "未识别主对象"}')
            if not summary['portal_openings']:
                reasons.append('房间边界没有可用于相邻空间拍摄的 door/open_connection')
            elif not summary['portal_accepted_raw']:
                reasons.append(
                    f"门口采样：整屋外 {summary['portal_outside_whole_home']}、墙距不足 "
                    f"{summary['portal_wall_clearance']}、物体碰撞 {summary['portal_object_collision']}、"
                    f"穿洞视线失败 {summary['portal_los_blocked']}"
                )
            if summary['doorway_openings'] and not summary['doorway_accepted_raw']:
                reasons.append(
                    f"门洞内侧采样：总计 {summary['doorway_samples']}、房外 "
                    f"{summary['doorway_outside_room']}、落入其他房间 {summary['doorway_other_room']}、"
                    f"其他墙距不足 {summary['doorway_other_wall_clearance']}、物体碰撞 "
                    f"{summary['doorway_object_collision']}、房内视线失败 "
                    f"{summary['doorway_los_blocked']}"
                )
            blocked = {
                'room_id': room_id, 'room_label': room_label, 'status': 'blocked',
                'reasons': reasons, 'candidate_ids': [],
                'rejection_summary': copy.deepcopy(summary),
            }
            room_pools.append(blocked)
            blocked_rooms.append(blocked)
            continue

        candidates.extend(diverse)
        room_pools.append({
            'room_id': room_id, 'room_label': room_label, 'status': 'ready', 'reasons': [],
            'candidate_ids': [row['candidate_id'] for row in diverse[:3]],
            'rejection_summary': copy.deepcopy(summary),
        })
    return {
        'status': 'ready' if not blocked_rooms else ('partial' if candidates else 'blocked'),
        'aspect_ratio': aspect_ratio, 'candidates': candidates,
        'room_pools': room_pools, 'blocked_rooms': blocked_rooms,
        'rejection_summary': copy.deepcopy(rejection_by_room),
    }


def rank_auto_camera_plan(api_key: str, project: dict, raw_candidates: list[dict],
                          *, shots_per_room: int, aspect_ratio: str,
                          annotator_id: str = 'local-user',
                          requested_room_pools: Optional[list[dict]] = None) -> dict:
    """Persist browser-rendered candidates and let Gemini rank, never invent, camera poses."""
    plan_id = new_id('camera_plan')
    model = project.get('model') or {}
    room_map = {str(room.get('id') or ''): room for room in model.get('rooms') or []}
    render_valid_by_room: dict[str, list[dict]] = {}
    for raw in raw_candidates[:100]:
        if not isinstance(raw, dict):
            continue
        room_id = str(raw.get('room_id') or '')[:80]
        metrics = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
        render_gate = metrics.get('render_gate') if isinstance(metrics.get('render_gate'), dict) else {}
        if (room_id in room_map and metrics.get('semantic_gate') is True
                and metrics.get('safety_gate') is True and render_gate.get('pass') is True):
            render_valid_by_room.setdefault(room_id, []).append(raw)
    allowed_candidate_ids: set[str] = set()
    for rows in render_valid_by_room.values():
        base = [row for row in rows if _number((row.get('camera') or {}).get('focal_length_mm')) != 20]
        eligible = base if base else [
            row for row in rows if _number((row.get('camera') or {}).get('focal_length_mm')) == 20
        ]
        allowed_candidate_ids.update(
            _safe_asset_name(row.get('candidate_id'), '') for row in eligible[:8]
        )
    candidates: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_candidates[:100]):
        if not isinstance(raw, dict):
            continue
        candidate_id = _safe_asset_name(raw.get('candidate_id'), f'candidate_{index + 1}')
        if candidate_id in seen or candidate_id not in allowed_candidate_ids:
            continue
        room_id = str(raw.get('room_id') or '')[:80]
        if room_id not in room_map:
            continue
        metrics = copy.deepcopy(raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {})
        render_gate = metrics.get('render_gate') if isinstance(metrics.get('render_gate'), dict) else {}
        if (metrics.get('semantic_gate') is not True or metrics.get('safety_gate') is not True
                or render_gate.get('pass') is not True):
            continue
        preview_data_url = str(raw.get('preview_data_url') or '')
        camera_payload = raw.get('camera') if isinstance(raw.get('camera'), dict) else {}
        normalized = normalize_model({**model, 'cameras': [camera_payload]}, source='human')
        if not normalized.get('cameras'):
            continue
        camera = normalized['cameras'][0]
        camera.update(
            id=str(camera_payload.get('id') or f'camera_{candidate_id}')[:100],
            room_id=room_id, source='auto_geometry', render_gate=copy.deepcopy(render_gate),
        )
        preview_path = save_capture_data(project['project_id'], f'{plan_id}_{candidate_id}', 'preview', preview_data_url)
        candidates.append({
            'candidate_id': candidate_id, 'room_id': room_id,
            'room_label': str(raw.get('room_label') or room_map[room_id].get('label') or room_id)[:100],
            'local_score': round(_clamp(raw.get('local_score'), 0, 100, 0), 2),
            'metrics': metrics,
            'camera': camera, 'preview_path': preview_path,
        })
        seen.add(candidate_id)
    if not candidates:
        raise ValueError('没有可用的自动机位候选')

    grouped: dict[str, list[dict]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate['room_id'], []).append(candidate)
    contact_sheets = []
    for room_id, rows in grouped.items():
        rows.sort(key=lambda item: item['local_score'], reverse=True)
        path = _save_camera_contact_sheet(project['project_id'], plan_id, room_id, rows)
        contact_sheets.append({
            'room_id': room_id, 'room_label': rows[0]['room_label'], 'path': path,
            'candidate_ids': [row['candidate_id'] for row in rows],
        })

    contract = [{
        'candidate_id': row['candidate_id'], 'room_id': row['room_id'],
        'room_label': row['room_label'], 'local_score': row['local_score'],
        'metrics': row['metrics'], 'camera': row['camera'],
    } for row in candidates]
    sheet_order = [
        {'image_number': index + 2, 'room_id': sheet['room_id'],
         'room_label': sheet['room_label'], 'candidate_ids': sheet['candidate_ids']}
        for index, sheet in enumerate(contact_sheets)
    ]
    prompt = f"""You are the final camera editor for residential interior marketing images.
Image 1 is the original top-down floor plan. Images 2 onward are browser-rendered contact sheets from the already approved metric 3D shell. Each cell has an immutable ASCII candidate_id label.

Choose exactly one PRIMARY candidate per room. Rank only existing candidate_ids with semantic_gate=true, safety_gate=true and render_gate.pass=true; never invent or modify coordinates. The system has already removed deferred 20mm candidates whenever a render-valid 24/28mm candidate exists, and will retain up to two additional valid local candidates as backups. Prefer a believable eye-level architectural photograph with useful room depth, visible floor, meaningful openings or semantic proxy volumes, balanced foreground/midground/background, and no wall-dominated or confusing crop. Avoid near-wall obstruction, excessive empty wall, tunnel views, and redundant choices. Local scores and the browser pixel fractions are hard evidence, not suggestions.

Contact-sheet order: {json.dumps(sheet_order, ensure_ascii=False, separators=(',', ':'))}
Candidate contract: {json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}
Return concise evidence for each selection. Output only the requested JSON."""

    ai_payload: Optional[dict] = None
    ai_error: Optional[str] = None
    ai_model = ''
    # Gemini image inputs are deliberately capped; unreviewed rooms fall back
    # to deterministic local ranking instead of losing the whole workflow.
    ai_sheets = contact_sheets[:12]
    if api_key and ai_sheets:
        ai_payload, ai_error = call_gemini_json(
            api_key, prompt,
            [project.get('floorplan_path') or ''] + [sheet['path'] for sheet in ai_sheets],
            _AUTO_CAMERA_SCHEMA, max_output_tokens=6000,
        )
        if ai_payload:
            ai_model = str(ai_payload.pop('_floor_engine_model', '') or '')

    candidate_map = {row['candidate_id']: row for row in candidates}
    selected_rows: list[dict] = []
    selected_ids: set[str] = set()
    ai_selections = ai_payload.get('selections') if isinstance(ai_payload, dict) else []
    for selection in ai_selections if isinstance(ai_selections, list) else []:
        if not isinstance(selection, dict):
            continue
        candidate = candidate_map.get(str(selection.get('candidate_id') or ''))
        if not candidate or candidate['candidate_id'] in selected_ids:
            continue
        room_selected = sum(row['room_id'] == candidate['room_id'] for row in selected_rows)
        if room_selected >= 1:
            continue
        selected_ids.add(candidate['candidate_id'])
        selected_rows.append({
            'candidate_id': candidate['candidate_id'], 'room_id': candidate['room_id'],
            'rank': room_selected + 1,
            'visual_score': round(_clamp(selection.get('visual_score'), 0, 100, candidate['local_score']), 2),
            'reason': str(selection.get('reason') or 'Gemini 视觉复排')[:1000],
            'strengths': [str(value)[:200] for value in selection.get('strengths') or []][:8],
            'risks': [str(value)[:200] for value in selection.get('risks') or []][:8],
            'selection_source': 'gemini',
        })
    for room_id, rows in grouped.items():
        chosen = sum(row['room_id'] == room_id for row in selected_rows)
        for candidate in sorted(rows, key=lambda item: item['local_score'], reverse=True):
            if chosen >= 1:
                break
            if candidate['candidate_id'] in selected_ids:
                continue
            chosen += 1
            selected_ids.add(candidate['candidate_id'])
            selected_rows.append({
                'candidate_id': candidate['candidate_id'], 'room_id': room_id, 'rank': chosen,
                'visual_score': candidate['local_score'],
                'reason': 'AI 未返回该房间的完整选择，使用本地几何与构图综合最高候选。',
                'strengths': ['通过本地墙体、固定物和室内位置安全检查'],
                'risks': ['未获得 Gemini 视觉复排意见'], 'selection_source': 'local_fallback',
            })

    selected_cameras = []
    selection_map = {row['candidate_id']: row for row in selected_rows}
    candidate_map = {row['candidate_id']: row for row in candidates}
    requested_pool_map = {
        str(row.get('room_id') or ''): copy.deepcopy(row)
        for row in requested_room_pools or [] if isinstance(row, dict)
    }
    room_pools = []
    for room in model.get('rooms') or []:
        if not room.get('selected', True):
            continue
        room_id = str(room.get('id') or '')
        rows = sorted(grouped.get(room_id) or [], key=lambda item: item['local_score'], reverse=True)
        requested = requested_pool_map.get(room_id) or {}
        if not rows:
            room_pools.append({
                'room_id': room_id, 'room_label': str(room.get('label') or room_id),
                'status': 'blocked', 'reasons': [str(item)[:500] for item in requested.get('reasons') or ['没有通过语义与安全门槛的候选']],
                'candidate_ids': [], 'primary_candidate_id': '',
                'rejection_summary': copy.deepcopy(requested.get('rejection_summary') or {}),
            })
            continue
        primary_selection = next((row for row in selected_rows if row['room_id'] == room_id), None)
        primary_id = str((primary_selection or {}).get('candidate_id') or rows[0]['candidate_id'])
        ordered_ids = [primary_id] + [row['candidate_id'] for row in rows if row['candidate_id'] != primary_id]
        ordered_ids = ordered_ids[:3]
        room_pools.append({
            'room_id': room_id, 'room_label': str(room.get('label') or room_id),
            'status': 'ready', 'reasons': [], 'candidate_ids': ordered_ids,
            'primary_candidate_id': primary_id,
            'rejection_summary': copy.deepcopy(requested.get('rejection_summary') or {}),
        })
        for pool_index, candidate_id in enumerate(ordered_ids):
            candidate = candidate_map[candidate_id]
            selection = selection_map.get(candidate_id)
            camera = copy.deepcopy(candidate['camera'])
            is_primary = pool_index == 0
            camera.update(
                source=('ai_selected' if is_primary and selection and selection['selection_source'] == 'gemini' else 'auto_geometry'),
                auto_plan_id=plan_id, candidate_id=candidate_id,
                local_score=candidate['local_score'],
                selection_score=(selection['visual_score'] if selection else candidate['local_score']),
                selection_reason=(selection['reason'] if selection else '语义与安全门槛通过的本地备用机位'),
                pool_rank=pool_index + 1, is_primary=is_primary,
                render_gate=copy.deepcopy(candidate['metrics']['render_gate']),
            )
            selected_cameras.append(camera)

    return {
        'plan_id': plan_id, 'project_id': project['project_id'], 'status': 'done',
        'aspect_ratio': aspect_ratio, 'shots_per_room': shots_per_room,
        'created_at': time.time(), 'created_by': annotator_id,
        'summary': str((ai_payload or {}).get('summary') or f'为 {len(grouped)} 个房间选择主机位并保留最多两个备用机位')[:2000],
        'ai_model': ai_model, 'ai_error': ai_error or '',
        'prompt_version': 'auto-camera-v2-render-gate',
        'candidates': candidates, 'contact_sheets': contact_sheets, 'room_pools': room_pools,
        'selections': selected_rows, 'selected_cameras': selected_cameras,
    }


def legacy_model_from_analysis(analysis: dict, *, width_m: float = 12.0) -> dict:
    """One-time bridge: import verified room polygons into one shared wall graph."""
    source = analysis.get('source') or {}
    source_width = int(source.get('width') or 1)
    source_height = int(source.get('height') or 1)
    depth_m = width_m * source_height / max(source_width, 1)
    rooms = []
    raw_walls = []
    wall_keys: dict[tuple, str] = {}
    for source_room in analysis.get('rooms') or []:
        polygon = [
            {'x': _number(point.get('x')) * width_m, 'z': _number(point.get('y')) * depth_m}
            for point in source_room.get('polygon') or []
        ]
        if len(polygon) < 3:
            continue
        rooms.append({
            'id': source_room.get('id'), 'label': source_room.get('label'),
            'room_type': source_room.get('room_type'), 'polygon': polygon,
            'selected': source_room.get('selected', True), 'source': 'imported', 'confidence': source_room.get('confidence', 1),
        })
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            a = (round(start['x'], 2), round(start['z'], 2))
            b = (round(end['x'], 2), round(end['z'], 2))
            key = tuple(sorted((a, b)))
            if key in wall_keys:
                continue
            wall_id = f'wall_{len(raw_walls) + 1}'
            wall_keys[key] = wall_id
            raw_walls.append({'id': wall_id, 'start': start, 'end': end, 'kind': 'interior', 'source': 'imported', 'confidence': 1})
    payload = {
        'model_id': new_id('model'), 'width_m': width_m, 'depth_m': depth_m,
        'wall_height_m': 2.8, 'wall_thickness_m': 0.12,
        'walls': raw_walls, 'rooms': rooms, 'openings': [], 'fixed_objects': [], 'cameras': [],
        'scale': {'status': 'estimated', 'method': 'legacy_import_width', 'reference_length_m': width_m},
        'uncertainties': ['从旧房间标注导入；请在整屋编辑器中复核共墙、门窗与真实尺寸。'],
    }
    model = normalize_model(payload, source_width=source_width, source_height=source_height, source='imported')
    # Attach legacy openings to the nearest wall in the shared model.
    opening_rows = []
    for opening in analysis.get('openings') or []:
        points = [
            {'x': _number(point.get('x')) * width_m, 'z': _number(point.get('y')) * depth_m}
            for point in opening.get('points') or []
        ]
        if len(points) != 2:
            continue
        center = {'x': (points[0]['x'] + points[1]['x']) / 2, 'z': (points[0]['z'] + points[1]['z']) / 2}
        wall = _nearest_wall(center, model['walls'])[0]
        if not wall:
            continue
        opening_rows.append({
            'id': opening.get('id'), 'kind': opening.get('kind'), 'wall_id': wall['id'],
            'center': center, 'width_m': max(0.5, _distance(points[0], points[1])),
            'height_m': 1.2 if opening.get('kind') == 'window' else 2.1,
            'sill_height_m': 0.9 if opening.get('kind') == 'window' else 0,
            'source': 'imported', 'confidence': opening.get('confidence', 0.5),
            'review_status': opening.get('review_status', 'pending'),
        })
    payload = {**model, 'openings': opening_rows}
    model = normalize_model(payload, source_width=source_width, source_height=source_height, source='imported')

    # Carry over fixed furniture observations, never the old fake cameras.
    object_rows = []
    seen_objects = set()
    for plan in (analysis.get('spatial_plans') or {}).values():
        for item in plan.get('furniture') or []:
            point = item.get('plan_position') or {}
            name = str(item.get('item') or '').strip()
            key = (name.lower(), round(_number(point.get('x')), 2), round(_number(point.get('y')), 2))
            if not name or key in seen_objects:
                continue
            seen_objects.add(key)
            object_rows.append({
                'id': f'object_{len(object_rows) + 1}', 'name': name, 'kind': 'fixed_furniture',
                'position': {'x': _number(point.get('x')) * width_m, 'y': 0, 'z': _number(point.get('y')) * depth_m},
                'size': _legacy_object_size(name), 'rotation_y_deg': 0, 'source': 'imported',
                'confidence': item.get('confidence', 0.5),
            })
    model['fixed_objects'] = object_rows
    model['geometry_report'] = validate_model(model)
    return model


def _legacy_object_size(name: str) -> dict:
    value = name.lower()
    if re.search(r'bed|床', value):
        return {'x': 2.0, 'y': 0.55, 'z': 1.65}
    if re.search(r'sofa|沙发', value):
        return {'x': 2.2, 'y': 0.85, 'z': 0.9}
    if re.search(r'table|桌', value):
        return {'x': 1.4, 'y': 0.76, 'z': 0.8}
    if re.search(r'cabinet|柜', value):
        return {'x': 1.5, 'y': 1.8, 'z': 0.5}
    return {'x': 1.0, 'y': 0.8, 'z': 0.7}


_NORM_POINT_SCHEMA = {
    'type': 'OBJECT', 'properties': {'x': {'type': 'NUMBER'}, 'z': {'type': 'NUMBER'}},
    'required': ['x', 'z'],
}
_WHOLE_HOME_AI_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'summary': {'type': 'STRING'},
        'estimated_width_m': {'type': 'NUMBER'}, 'estimated_depth_m': {'type': 'NUMBER'},
        'scale_evidence': {'type': 'STRING'},
        'walls': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'id': {'type': 'STRING'}, 'start': _NORM_POINT_SCHEMA, 'end': _NORM_POINT_SCHEMA,
            'kind': {'type': 'STRING', 'enum': ['exterior', 'interior', 'partition']},
            'thickness_m': {'type': 'NUMBER'}, 'confidence': {'type': 'NUMBER'},
        }, 'required': ['id', 'start', 'end', 'kind', 'thickness_m', 'confidence']}},
        'rooms': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'id': {'type': 'STRING'}, 'label': {'type': 'STRING'}, 'room_type': {'type': 'STRING'},
            'polygon': {'type': 'ARRAY', 'items': _NORM_POINT_SCHEMA}, 'confidence': {'type': 'NUMBER'},
        }, 'required': ['id', 'label', 'room_type', 'polygon', 'confidence']}},
        'openings': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'id': {'type': 'STRING'}, 'kind': {'type': 'STRING', 'enum': ['door', 'window', 'open_connection']},
            'wall_id': {'type': 'STRING'}, 'center': _NORM_POINT_SCHEMA,
            'width_m': {'type': 'NUMBER'}, 'height_m': {'type': 'NUMBER'},
            'sill_height_m': {'type': 'NUMBER'}, 'confidence': {'type': 'NUMBER'},
        }, 'required': ['id', 'kind', 'wall_id', 'center', 'width_m', 'height_m', 'sill_height_m', 'confidence']}},
        'fixed_objects': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'id': {'type': 'STRING'}, 'name': {'type': 'STRING'}, 'kind': {'type': 'STRING'},
            'center': _NORM_POINT_SCHEMA, 'width_m': {'type': 'NUMBER'}, 'depth_m': {'type': 'NUMBER'},
            'height_m': {'type': 'NUMBER'}, 'rotation_y_deg': {'type': 'NUMBER'},
            'room_id': {'type': 'STRING'}, 'confidence': {'type': 'NUMBER'},
        }, 'required': ['id', 'name', 'kind', 'center', 'width_m', 'depth_m', 'height_m', 'rotation_y_deg', 'room_id', 'confidence']}},
        'uncertainties': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
    },
    'required': ['summary', 'estimated_width_m', 'estimated_depth_m', 'scale_evidence', 'walls', 'rooms', 'openings', 'fixed_objects', 'uncertainties'],
}

_OPENING_AUDIT_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'openings': _WHOLE_HOME_AI_SCHEMA['properties']['openings'],
        'uncertainties': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
    },
    'required': ['openings', 'uncertainties'],
}

_SEMANTIC_LAYOUT_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'summary': {'type': 'STRING'},
        'objects': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'id': {'type': 'STRING'}, 'room_id': {'type': 'STRING'},
            'semantic_role': {'type': 'STRING'}, 'name': {'type': 'STRING'},
            'center': _NORM_POINT_SCHEMA,
            'width_m': {'type': 'NUMBER'}, 'depth_m': {'type': 'NUMBER'},
            'height_m': {'type': 'NUMBER'}, 'rotation_y_deg': {'type': 'NUMBER'},
            'confidence': {'type': 'NUMBER'}, 'assumption': {'type': 'STRING'},
        }, 'required': [
            'id', 'room_id', 'semantic_role', 'name', 'center', 'width_m', 'depth_m',
            'height_m', 'rotation_y_deg', 'confidence', 'assumption',
        ]}},
        'room_assumptions': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'room_id': {'type': 'STRING'},
            'assumptions': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        }, 'required': ['room_id', 'assumptions']}},
        'uncertainties': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
    },
    'required': ['summary', 'objects', 'room_assumptions', 'uncertainties'],
}


def _model_from_ai_payload(payload: dict, source_width: int, source_height: int) -> dict:
    width_m = _clamp(payload.get('estimated_width_m'), 2, 80, 12)
    depth_m = _clamp(payload.get('estimated_depth_m'), 2, 80, width_m * source_height / max(source_width, 1))
    objects = []
    for item in payload.get('fixed_objects') or []:
        objects.append({
            **item, 'position': {**(item.get('center') or {}), 'y': 0},
            'size': {'x': item.get('width_m'), 'y': item.get('height_m'), 'z': item.get('depth_m')},
            'source': 'ai',
        })
    raw = {
        '_coordinates_normalized': True,
        'width_m': width_m, 'depth_m': depth_m, 'wall_height_m': 2.8, 'wall_thickness_m': 0.12,
        'walls': [{**item, 'source': 'ai'} for item in payload.get('walls') or []],
        'rooms': [{**item, 'source': 'ai', 'selected': True} for item in payload.get('rooms') or []],
        'openings': [{**item, 'source': 'ai', 'review_status': 'pending'} for item in payload.get('openings') or []],
        'fixed_objects': objects, 'cameras': [], 'uncertainties': payload.get('uncertainties') or [],
        'scale': {'status': 'estimated', 'method': 'ai_dimensions', 'reference_length_m': width_m,
                  'evidence': str(payload.get('scale_evidence') or '')[:500]},
    }
    return normalize_model(raw, source_width=source_width, source_height=source_height, source='ai')


def _semantic_prompt_contract(model: dict) -> dict:
    width_m = max(_number(model.get('width_m'), 1), .1)
    depth_m = max(_number(model.get('depth_m'), 1), .1)
    room_profiles = {
        str(room.get('id') or ''): str(
            room.get('semantic_profile') or _canonical_room_profile(room))
        for room in model.get('rooms') or []
    }

    def normalized(point: dict) -> dict:
        return {
            'x': round(_number(point.get('x')) / width_m, 5),
            'z': round(_number(point.get('z')) / depth_m, 5),
        }

    def observed_fact(row: dict) -> dict:
        fact = {
            'id': row.get('id'), 'room_id': row.get('room_id'),
            'semantic_role': _canonical_object_role(
                row, room_profiles.get(str(row.get('room_id') or ''), '')),
            'name': row.get('name'),
            'center': normalized(row.get('position') or {}), 'size': row.get('size'),
            'rotation_y_deg': row.get('rotation_y_deg'),
            'source': row.get('source'), 'observed': bool(row.get('observed', True)),
            'purpose': row.get('purpose'), 'review_status': row.get('review_status'),
        }
        if isinstance(row.get('original_position'), dict):
            fact['original_center'] = normalized(row['original_position'])
        if isinstance(row.get('geometry_repair'), dict):
            fact['geometry_repair'] = copy.deepcopy(row['geometry_repair'])
        return fact

    return {
        'rooms': [{
            'id': room.get('id'), 'label': room.get('label'),
            'profile': room.get('semantic_profile') or _canonical_room_profile(room),
            'polygon': [normalized(point) for point in room.get('polygon') or []],
        } for room in model.get('rooms') or []],
        'room_contracts': copy.deepcopy(model.get('room_contracts') or _room_contracts(model.get('rooms') or [])),
        'observed_objects': [
            observed_fact(row) for row in model.get('fixed_objects') or []
            if row.get('purpose') != 'layout_proxy'
        ],
        'existing_layout_proxies': [
            observed_fact(row) for row in model.get('fixed_objects') or []
            if row.get('purpose') == 'layout_proxy' and row.get('review_status') != 'rejected'
        ],
    }


def _model_with_semantic_payload(base_model: dict, payload: dict) -> dict:
    base = upgrade_model_v2(base_model)
    width_m = max(_number(base.get('width_m'), 1), .1)
    depth_m = max(_number(base.get('depth_m'), 1), .1)
    preserved = [
        copy.deepcopy(row) for row in base.get('fixed_objects') or []
        if row.get('purpose') != 'layout_proxy' or row.get('source') in ('human', 'imported')
    ]
    room_ids = {str(room.get('id') or '') for room in base.get('rooms') or []}
    room_profiles = {
        str(room.get('id') or ''): str(
            room.get('semantic_profile') or _canonical_room_profile(room))
        for room in base.get('rooms') or []
    }
    seen_ids = {str(row.get('id') or '') for row in preserved}
    rows = list(preserved)
    for index, raw in enumerate(payload.get('objects') or []):
        if not isinstance(raw, dict):
            continue
        room_id = str(raw.get('room_id') or '')[:80]
        if room_id not in room_ids:
            continue
        profile = room_profiles.get(room_id, '')
        role = _singleton_role_key(_canonical_object_role(raw, profile), profile)
        center = raw.get('center') if isinstance(raw.get('center'), dict) else {}
        position = {
            'x': _clamp(_number(center.get('x')) * width_m, 0, width_m, width_m / 2),
            'y': 0,
            'z': _clamp(_number(center.get('z', center.get('y'))) * depth_m, 0, depth_m, depth_m / 2),
        }
        duplicate = next((
            row for row in rows
            if row.get('room_id') == room_id
            and _singleton_role_key(_canonical_object_role(row, profile), profile) == role
            and (
                role in _SINGLETON_PROXY_ROLES
                or _distance(row.get('position') or {}, position) < .55
            )
        ), None)
        if duplicate:
            continue
        object_id = _unique_id(raw.get('id'), f'layout_{index + 1}', seen_ids)
        rows.append({
            'id': object_id, 'name': str(raw.get('name') or role)[:100],
            'kind': role, 'semantic_role': role, 'position': position,
            'size': {
                'x': _clamp(raw.get('width_m'), .1, 10, 1),
                'y': _clamp(raw.get('height_m'), .1, 6, .8),
                'z': _clamp(raw.get('depth_m'), .1, 10, .7),
            },
            'rotation_y_deg': _clamp(raw.get('rotation_y_deg'), -360, 360, 0),
            'room_id': room_id, 'source': 'ai',
            'confidence': _clamp(raw.get('confidence'), 0, 1, .5),
            'purpose': 'layout_proxy', 'observed': False, 'review_status': 'pending',
            'blocks_camera': role != 'shower_zone', 'required_for_camera': True,
            'clearance_m': .25, 'assumption': str(raw.get('assumption') or '')[:500],
        })
    value = normalize_model({**base, 'fixed_objects': rows}, source='ai')
    assumptions = {
        str(row.get('room_id') or ''): [str(item)[:300] for item in row.get('assumptions') or []][:20]
        for row in payload.get('room_assumptions') or [] if isinstance(row, dict)
    }
    for contract in value.get('room_contracts') or []:
        contract['assumptions'] = assumptions.get(str(contract.get('room_id') or ''), [])
    value.setdefault('uncertainties', []).extend(
        str(item)[:300] for item in payload.get('uncertainties') or [])
    value['semantic_report'] = validate_semantic_layout(value)
    return upgrade_model_v2(value)


def _semantic_quality(model: dict) -> tuple[int, int, float]:
    report = model.get('semantic_report') or validate_semantic_layout(model)
    confidences = [
        _number(row.get('confidence'), 0) for row in model.get('fixed_objects') or []
        if row.get('purpose') == 'layout_proxy' and row.get('review_status') != 'rejected'
    ]
    average_confidence = sum(confidences) / len(confidences) if confidences else 0
    return (len(report.get('hard_errors') or []), len(report.get('warnings') or []), -average_confidence)


def _accept_locally_valid_ai_layout(model: dict) -> dict:
    """Accept final AI placements after hard rules; provenance still records what was inferred."""
    value = upgrade_model_v2(model)
    report = validate_semantic_layout(value)
    hard_errors = report.get('hard_errors') or []
    invalid_object_ids = {
        str(issue.get(key) or '')
        for issue in hard_errors
        for key in ('object_id', 'other_object_id')
        if issue.get(key)
    }
    accepted_at = time.time()
    for row in value.get('fixed_objects') or []:
        if (row.get('source') == 'ai' and row.get('review_status') == 'pending'
                and str(row.get('id') or '') not in invalid_object_ids):
            row['review_status'] = 'accepted'
            row['semantic_acceptance'] = {
                'method': 'local_semantic_rules_v1',
                'status': 'accepted' if not hard_errors else 'accepted_object_only',
                'scope': 'placement_and_contract_only' if not hard_errors else 'object_placement_only',
                'accepted_at': accepted_at,
            }
    value['semantic_report'] = validate_semantic_layout(value)
    return value


def analyze_semantic_layout(api_key: str, floorplan_path: str, model: dict) -> tuple[dict, Optional[str], str]:
    """Complete room-function proxies, then perform exactly one visual repair audit."""
    # Topology extraction and room segmentation are independent vision passes.
    # Reconcile only AI-observed facts before exposing them as immutable prompt
    # evidence; human/imported coordinates are never moved here.
    base = repair_ai_observed_architecture(model)
    contract = _semantic_prompt_contract(base)
    topology_overlay = ''
    try:
        topology_overlay = _render_topology_audit_overlay(base, floorplan_path)
        prompt = f"""Create a semantic gray-box layout inside this already approved residential shell.
Image 1 is the untouched source floor plan. Image 2 is the immutable audited shell overlay: red/blue walls, green openings, orange room polygons.

IMMUTABLE CONTRACT:
- Never add, delete, move, or rename any room, wall, door, window, or opening.
- Use exactly the listed room_id values and normalized x,z coordinates over the whole plan.
- The observed_objects are facts. Do not duplicate or relocate them.
- existing_layout_proxies are prior auditable assumptions, not facts. Return a complete replacement list and improve them when needed.
- Add only the minimum layout proxies required by each room_contract. These inferred proxies are planning assumptions, not observed plan facts.
- Keep every footprint fully inside its room, preserve door clearance and circulation, and avoid unrelated object overlap.
- Bedrooms require a buildable bed position; bathrooms require basin, toilet, and shower zone; kitchens require a kitchen run plus at least one sink/hob/fridge role; living rooms require sofa and TV.

Approved contract: {json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}
Return a COMPLETE list of inferred layout proxy objects only; do not repeat observed_objects. Output only the requested JSON."""
        payload, error = call_gemini_json(
            api_key, prompt, [floorplan_path, topology_overlay],
            _SEMANTIC_LAYOUT_SCHEMA, max_output_tokens=9000,
        )
        if error or not payload:
            base['semantic_report'] = validate_semantic_layout(base)
            base['semantic_report']['audit_passes'] = 0
            return base, error or '语义布局返回为空', ''
        model_name = str(payload.pop('_floor_engine_model', '') or '')
        initial = _model_with_semantic_payload(base, payload)
        initial['semantic_report']['audit_passes'] = 1
        layout_overlay = _render_semantic_layout_overlay(initial, floorplan_path)
        try:
            repair_prompt = f"""Audit and repair the semantic layout without changing the approved shell.
Image 1 is the untouched source plan. Image 2 overlays the shell and first semantic proposal: purple boxes are inferred layout proxies, blue boxes are observed facts.

Return a COMPLETE replacement list of inferred proxies only. Use existing room IDs. Do not repeat observed objects and never modify walls, rooms, doors, or windows.
Local semantic failures: {json.dumps(initial['semantic_report'].get('hard_errors') or [], ensure_ascii=False, separators=(',', ':'))}
First inferred proposal: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
Approved contract: {json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}
Fix missing required roles, out-of-room footprints, blocked doors, and unrelated overlaps. Output only the requested JSON."""
            repaired_payload, repair_error = call_gemini_json(
                api_key, repair_prompt, [floorplan_path, layout_overlay],
                _SEMANTIC_LAYOUT_SCHEMA, max_output_tokens=9000,
            )
            if repaired_payload and not repair_error:
                repair_model_name = str(repaired_payload.pop('_floor_engine_model', '') or '')
                repaired = _model_with_semantic_payload(base, repaired_payload)
                repaired['semantic_report']['audit_passes'] = 2
                if _semantic_quality(repaired) < _semantic_quality(initial):
                    initial = repaired
                    if repair_model_name:
                        model_name = f'{model_name} + {repair_model_name} semantic repair'.strip(' +')
            elif repair_error:
                initial.setdefault('uncertainties', []).append(f'AI 语义布局修复失败：{repair_error}')
            initial = repair_ai_semantic_proxies(initial)
            initial = _accept_locally_valid_ai_layout(initial)
            initial['semantic_report']['audit_passes'] = 2
            return initial, None, model_name
        finally:
            if os.path.isfile(layout_overlay):
                try:
                    os.remove(layout_overlay)
                except OSError:
                    pass
    except Exception as ex:
        logger.warning(f'[整屋建模] 语义布局识别失败，保留已审核外壳: {ex}')
        base['semantic_report'] = validate_semantic_layout(base)
        base['semantic_report']['audit_passes'] = 0
        return base, str(ex), ''
    finally:
        if topology_overlay and os.path.isfile(topology_overlay):
            try:
                os.remove(topology_overlay)
            except OSError:
                pass


def _opening_center(model: dict, opening: dict) -> Optional[dict]:
    wall = next((item for item in model.get('walls') or [] if item.get('id') == opening.get('wall_id')), None)
    if not wall:
        return None
    return _point_along(
        wall['start'], wall['end'],
        _number(opening.get('offset_m')) + _number(opening.get('width_m')) / 2,
    )


def _opening_side_room_ids_at(model: dict, opening: dict, along_m: float,
                              *, epsilon: float = .08) -> dict:
    wall = next((
        item for item in model.get('walls') or []
        if str(item.get('id') or '') == str(opening.get('wall_id') or '')
    ), None)
    if not wall:
        return {'negative': [], 'positive': [], 'point': None, 'along_m': along_m}
    dx = _number((wall.get('end') or {}).get('x')) - _number((wall.get('start') or {}).get('x'))
    dz = _number((wall.get('end') or {}).get('z')) - _number((wall.get('start') or {}).get('z'))
    length = math.hypot(dx, dz)
    if length < .05:
        return {'negative': [], 'positive': [], 'point': None, 'along_m': along_m}
    point = _point_along(wall['start'], wall['end'], max(0, min(length, along_m)))
    normal = {'x': -dz / length, 'z': dx / length}
    samples = {
        'negative': {'x': point['x'] - normal['x'] * epsilon,
                     'z': point['z'] - normal['z'] * epsilon},
        'positive': {'x': point['x'] + normal['x'] * epsilon,
                     'z': point['z'] + normal['z'] * epsilon},
    }
    result = {
        'negative': [], 'positive': [], 'point': point,
        'along_m': round(max(0, min(length, along_m)), 5),
    }
    for side, point in samples.items():
        result[side] = sorted({
            str(room.get('id') or '') for room in model.get('rooms') or []
            if _point_in_polygon(point, room.get('polygon') or [])
        })
    return result


def _opening_side_room_ids(model: dict, opening: dict, *, epsilon: float = .08) -> dict:
    distance = _number(opening.get('offset_m')) + _number(opening.get('width_m')) / 2
    result = _opening_side_room_ids_at(model, opening, distance, epsilon=epsilon)
    return {**result, 'center': result.get('point')}


def _opening_adjacency_samples(model: dict, opening: dict, *, epsilon: float = .08) -> list[dict]:
    """Audit both sides near the clear-span start, centre and end.

    Looking only at the opening centre misses an opening that straddles a room
    junction: one half may connect kitchen/bedroom while the other half connects
    entry/bedroom.  The margin avoids classifying the exact jamb endpoint.
    """
    start = _number(opening.get('offset_m'))
    width = max(0, _number(opening.get('width_m')))
    if width <= .02:
        return []
    margin = min(max(.05, width * .08), max(.01, width / 2 - .01))
    positions = [start + margin, start + width / 2, start + width - margin]
    unique_positions = []
    for position in positions:
        if not unique_positions or abs(position - unique_positions[-1]) > .005:
            unique_positions.append(position)
    labels = ('start', 'center', 'end') if len(unique_positions) == 3 else tuple(
        f'sample_{index + 1}' for index in range(len(unique_positions)))
    return [
        {
            'label': labels[index],
            **_opening_side_room_ids_at(model, opening, position, epsilon=epsilon),
        }
        for index, position in enumerate(unique_positions)
    ]


def _opening_spans_room_junction(model: dict, opening: dict) -> Optional[dict]:
    samples = _opening_adjacency_samples(model, opening)
    signatures = {
        (tuple(row.get('negative') or []), tuple(row.get('positive') or []))
        for row in samples
    }
    if len(signatures) <= 1:
        return None
    evidence = [{
        'label': row.get('label'), 'along_m': row.get('along_m'),
        'negative_room_ids': list(row.get('negative') or []),
        'positive_room_ids': list(row.get('positive') or []),
        'point': copy.deepcopy(row.get('point')),
    } for row in samples]
    return {
        'code': 'opening_spans_room_junction',
        'room_ids': sorted({
            room_id for row in samples
            for room_id in list(row.get('negative') or []) + list(row.get('positive') or [])
        }),
        'samples': evidence,
        'reason': 'opening adjacency changes across its clear span and crosses a room junction',
    }


def _portal_direct_origin_room_ids(model: dict, target_room_id: str, opening: dict) -> list[str]:
    """Return only rooms immediately opposite the target across the opening wall.

    Sampling far along an opening tangent can land in a third room near a wall end.
    Those points are not legitimate portal origins even if a broad wall LOS test happens
    to pass, so adjacency is determined once at the physical opening centre.
    """
    if _opening_spans_room_junction(model, opening):
        return []
    sides = _opening_side_room_ids(model, opening)
    negative = set(sides.get('negative') or [])
    positive = set(sides.get('positive') or [])
    target_negative = target_room_id in negative
    target_positive = target_room_id in positive
    if target_negative == target_positive:
        return []
    opposite = positive if target_negative else negative
    return sorted(room_id for room_id in opposite if room_id and room_id != target_room_id)


def _semantic_invalid_open_connection(model: dict, opening: dict) -> Optional[dict]:
    if opening.get('kind') != 'open_connection' or opening.get('review_status') == 'rejected':
        return None
    rooms = {str(room.get('id') or ''): room for room in model.get('rooms') or []}
    sides = _opening_side_room_ids(model, opening)
    adjacent_ids = sorted(set(sides['negative'] + sides['positive']))
    profiles = sorted({
        _canonical_room_profile(rooms[room_id]) for room_id in adjacent_ids if room_id in rooms
    })
    if 'bedroom' in profiles and 'kitchen' in profiles:
        return {
            'room_ids': adjacent_ids,
            'room_profiles': profiles,
            'reason': 'bedroom and kitchen cannot use a direct leafless open_connection without manual review',
        }
    return None


def _opening_topology_issues(model: dict, opening: dict) -> list[dict]:
    issues = []
    junction = _opening_spans_room_junction(model, opening)
    if junction:
        issues.append(junction)
    semantic = _semantic_invalid_open_connection(model, opening)
    if semantic:
        issues.append({'code': 'semantic_open_connection_room_mismatch', **semantic})
    return issues


def _is_ai_origin(row: dict) -> bool:
    return row.get('source') in ('ai', 'ai_edited')


def _audit_ai_openings(model: dict) -> list[str]:
    """Reject only deterministic AI duplicates; flag implausible AI portals for review."""
    notices: list[str] = []
    openings = model.get('openings') or []
    indexed = list(enumerate(openings))
    active = [
        (index, row) for index, row in indexed
        if isinstance(row, dict) and row.get('review_status') == 'accepted'
    ]
    for left_pos, (left_index, left) in enumerate(active):
        if left.get('review_status') != 'accepted':
            continue
        for right_index, right in active[left_pos + 1:]:
            if (right.get('review_status') != 'accepted'
                    or left.get('wall_id') != right.get('wall_id')
                    or left.get('kind') != right.get('kind')):
                continue
            overlap = min(
                _number(left.get('offset_m')) + _number(left.get('width_m')),
                _number(right.get('offset_m')) + _number(right.get('width_m')),
            ) - max(_number(left.get('offset_m')), _number(right.get('offset_m')))
            if overlap <= .05:
                continue
            if not _is_ai_origin(left) or not _is_ai_origin(right):
                continue
            ranked = sorted(
                ((left_index, left), (right_index, right)),
                key=lambda pair: (-_number(pair[1].get('confidence'), 0), pair[0]),
            )
            winner, duplicate = ranked[0][1], ranked[1][1]
            duplicate['review_status'] = 'rejected'
            duplicate['duplicate_of'] = str(winner.get('id') or '')
            duplicate['opening_deduplication'] = {
                'method': 'deterministic_same_wall_opening_dedup_v1',
                'duplicate_of': str(winner.get('id') or ''),
                'overlap_m': round(overlap, 5),
                'action': 'rejected_ai_duplicate',
                'reason': 'same-kind accepted AI openings overlap on the same wall',
            }
            notices.append(
                f"本地开口去重停用 {duplicate.get('id')}：与 {winner.get('id')} "
                f"在同墙重叠 {overlap:.3f}m"
            )
            if duplicate is left:
                break
    for opening in openings:
        if opening.get('review_status') != 'accepted':
            continue
        issues = _opening_topology_issues(model, opening)
        if not issues or not _is_ai_origin(opening):
            continue
        issue = issues[0]
        opening['review_status'] = 'pending'
        opening['opening_topology_review'] = {
            'method': 'deterministic_opening_span_topology_v2',
            'status': 'manual_review_required',
            'code': issue['code'],
            'room_ids': issue.get('room_ids') or [],
            'room_profiles': issue.get('room_profiles') or [],
            'samples': copy.deepcopy(issue.get('samples') or []),
            'reason': issue['reason'],
        }
        if issue['code'] == 'opening_spans_room_junction':
            notices.append(
                f"开口 {opening.get('id')} 横跨房间交点、整段邻接关系不稳定，已退回人工复核"
            )
        else:
            notices.append(
                f"开口 {opening.get('id')} 连接厨房与卧室且标为 open_connection，已退回人工复核"
            )
    return notices


def merge_audit_opening_candidates(initial: dict, repaired: dict) -> tuple[dict, int]:
    """Keep openings seen by either visual pass, attached to repaired walls.

    The audit pass is allowed to correct wall geometry but should not silently
    delete a visible window/door.  Dropped first-pass candidates are retained as
    pending, with reduced confidence, so the human review remains authoritative.
    """
    result = copy.deepcopy(repaired)
    repaired_centres = [
        (opening.get('kind'), _opening_center(result, opening))
        for opening in result.get('openings') or []
    ]
    seen_ids = {str(item.get('id') or '') for item in result.get('openings') or []}
    added = 0
    for opening in initial.get('openings') or []:
        center = _opening_center(initial, opening)
        if not center:
            continue
        already_present = any(
            kind == opening.get('kind') and other_center and _distance(center, other_center) <= .55
            for kind, other_center in repaired_centres
        )
        if already_present:
            continue
        wall, distance, ratio = _nearest_wall(center, result.get('walls') or [])
        if not wall or distance > .45:
            continue
        wall_length = _distance(wall['start'], wall['end'])
        width = min(_number(opening.get('width_m'), .9), max(.25, wall_length - .1))
        offset = max(0.0, min(max(0.0, wall_length - width), ratio * wall_length - width / 2))
        candidate = {
            **copy.deepcopy(opening),
            'id': _unique_id(opening.get('id'), 'opening', seen_ids),
            'wall_id': wall['id'], 'offset_m': round(offset, 5), 'width_m': round(width, 5),
            'source': 'ai', 'confidence': round(_number(opening.get('confidence'), .5) * .8, 5),
            'review_status': 'pending',
        }
        result.setdefault('openings', []).append(candidate)
        repaired_centres.append((candidate.get('kind'), _opening_center(result, candidate)))
        added += 1
    result['geometry_report'] = validate_model(result)
    if added:
        result.setdefault('uncertainties', []).append(
            f'拓扑复核保留了 {added} 个仅首轮识别到的门窗候选；已重新挂到修正墙体，需人工接受或排除')
    return result, added


def model_with_audited_openings(base: dict, opening_rows: list[dict]) -> dict:
    result = copy.deepcopy(base)
    result['openings'] = []
    wall_map = {str(wall.get('id') or ''): wall for wall in result.get('walls') or []}
    seen: set[str] = set()
    for row in opening_rows:
        if not isinstance(row, dict):
            continue
        kind = row.get('kind') if row.get('kind') in ('door', 'window', 'open_connection') else 'door'
        center = _metric_point(row.get('center'), result['width_m'], result['depth_m'], normalized=True)
        wall = wall_map.get(str(row.get('wall_id') or ''))
        if not wall:
            wall, distance, _ = _nearest_wall(center, result.get('walls') or [])
            if not wall or distance > .5:
                continue
        wall_length = _distance(wall['start'], wall['end'])
        width = _clamp(row.get('width_m'), .25, max(.25, wall_length - .1), .9 if kind == 'door' else 1.4)
        _, ratio = _point_segment(center, wall['start'], wall['end'])
        offset = max(0.0, min(max(0.0, wall_length - width), ratio * wall_length - width / 2))
        result['openings'].append({
            'id': _unique_id(row.get('id'), 'opening', seen), 'wall_id': wall['id'], 'kind': kind,
            'offset_m': round(offset, 5), 'width_m': width,
            'height_m': _clamp(row.get('height_m'), .3, wall['height_m'], 1.2 if kind == 'window' else 2.1),
            'sill_height_m': _clamp(row.get('sill_height_m'), 0, wall['height_m'] - .2, .9 if kind == 'window' else 0),
            'source': 'ai', 'confidence': _clamp(row.get('confidence'), 0, 1, .5), 'review_status': 'pending',
        })
    result['geometry_report'] = validate_model(result)
    return result


def prefer_historical_geometry(current: dict, projects: list[dict], floorplan_path: str) -> tuple[dict, str]:
    """Prevent a stochastic re-analysis from replacing a better same-plan shell."""
    current_errors = len(current.get('geometry_report', {}).get('hard_errors') or [])
    current_score = plan_alignment_score(current, floorplan_path)
    current.setdefault('geometry_report', {})['image_alignment_score'] = current_score
    best_model, best_project_id, best_errors, best_score = current, '', current_errors, current_score
    source_real = os.path.realpath(floorplan_path)
    for project in projects:
        if os.path.realpath(str(project.get('floorplan_path') or '')) != source_real:
            continue
        candidate = copy.deepcopy(project.get('model') or {})
        if int(_number(candidate.get('schema_version'), 1)) not in (1, 2) or project.get('verified') or candidate.get('cameras'):
            continue
        report = candidate.get('geometry_report') or {}
        errors = len(report.get('hard_errors') or [])
        score = plan_alignment_score(candidate, floorplan_path)
        candidate.setdefault('geometry_report', {})['image_alignment_score'] = score
        if score <= 0:
            continue
        if errors < best_errors or (errors == best_errors and score > best_score + .5):
            best_model, best_project_id, best_errors, best_score = candidate, str(project.get('project_id') or ''), errors, score
    if not best_project_id:
        return current, ''
    # The historical candidate contributes only its better wall/room geometry.
    # Current three-pass opening observations are unioned onto those walls.
    result, added = merge_audit_opening_candidates(current, best_model)
    result['fixed_objects'] = copy.deepcopy(current.get('fixed_objects') or result.get('fixed_objects') or [])
    result['geometry_report'] = validate_model(result, floorplan_path)
    result['geometry_report']['audit_passes'] = 3
    result.setdefault('uncertainties', []).append(
        f'本次 AI 墙图对齐 {current_score:.2f}，自动沿用同户型历史更优墙图 {best_score:.2f}（{best_project_id}），并合并 {added} 个新门窗候选')
    return result, best_project_id


_TOPOLOGY_REPAIR_CODES = {'open_exterior_endpoint', 'enclosed_room_boundary_gap'}


def _topology_issue_digest(report: dict) -> list[dict]:
    keys = ('code', 'wall_id', 'room_id', 'opening_id', 'point', 'start', 'end', 'length_m', 'message')
    return [
        {key: issue[key] for key in keys if key in issue}
        for issue in report.get('hard_errors') or []
        if issue.get('code') in _TOPOLOGY_REPAIR_CODES
    ][:40]


def analyze_whole_home(api_key: str, floorplan_path: str) -> tuple[Optional[dict], Optional[str], str]:
    prompt = """You are reconstructing ONE complete residential shell from a top-down architectural floor plan.
Return only the requested JSON. This is geometry extraction, not interior design and not camera planning.

Coordinate rules:
- x,z are normalized 0..1 over the ENTIRE input image: x left-to-right, z top-to-bottom.
- Emit a shared wall graph. Every physical wall segment appears once. Shared room walls must not be duplicated.
- Split a wall only at corners, intersections, or real thickness/direction changes.
- A wall centreline MUST continue through every door, window, and open connection. An opening cuts the 3D wall solid by wall_id + position; it is never represented by ending or omitting the wall. Include the wall portions on both sides of every opening.
- Trace the visible wall centerline, not an arbitrary rectangle inside a room.
- Exterior/interior classification must follow visible line weight. Never invent hidden walls.
- Trace every exterior step, recess, bay, balcony return, and short connector. Exterior wall segments must form closed polygonal cycles with no dangling endpoint, including around entrance doors and windows.
- Attach every visible door, window, or full-height open connection to its wall_id.
- Room polygons describe finished floor boundaries in the same global coordinates.
- Before answering, audit every enclosed room polygon edge against the wall graph and audit every exterior-wall endpoint. Do not return a rectangular room polygon when the visible exterior boundary steps in or out.
- Fixed objects include only architectural or clearly fixed items visible on plan (kitchen runs, sanitary fixtures, wardrobes, platforms). Do not invent loose furniture.
- Use printed dimensions to estimate overall metric width/depth. If scale is uncertain, give a conservative estimate and explain scale_evidence and uncertainties.
- Confidence is 0..1. Stable ASCII ids only. Never propose cameras or perspectives.
"""
    payload, error = call_gemini_json(api_key, prompt, [floorplan_path], _WHOLE_HOME_AI_SCHEMA, max_output_tokens=12000)
    if error or not payload:
        return None, error or '整屋识别返回为空', ''
    model_name = str(payload.pop('_floor_engine_model', '') or '')
    try:
        with Image.open(floorplan_path) as image:
            source_width, source_height = image.size
    except Exception as ex:
        return None, f'读取户型图尺寸失败: {ex}', model_name
    model = _model_from_ai_payload(payload, source_width, source_height)
    initial_alignment = plan_alignment_score(model, floorplan_path)
    model['geometry_report']['image_alignment_score'] = initial_alignment
    issues = _topology_issue_digest(model.get('geometry_report') or {})
    # A second visual audit is intentional even when the graph is technically
    # closed: a closed polygon can still be shifted onto the wrong printed line.
    audit_overlay = ''
    try:
        audit_overlay = _render_topology_audit_overlay(model, floorplan_path)
        repair_prompt = f"""{prompt}

SECOND-PASS TOPOLOGY AUDIT:
Image 1 is the untouched source plan. Image 2 is the first extraction drawn back over that plan: red = exterior walls, blue = interior walls, green = openings, orange = room polygons, white circles = wall junctions.
Re-read both images and return a COMPLETE corrected JSON model, not a patch. A closed graph is not sufficient: every colored segment must sit on the corresponding architectural wall centreline in Image 1.

First extraction:
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}

Detected graph failures (metric coordinates are diagnostic only; your output remains normalized 0..1):
{json.dumps(issues, ensure_ascii=False, separators=(',', ':'))}

Initial local wall-to-plan alignment score: {initial_alignment}/100.

Repair priorities:
1. Follow the black architectural wall lines, including every short stepped exterior return.
2. Make each exterior contour a closed graph. Do not bridge across white space unless a wall line is visibly present.
3. Restore continuous collinear walls across doors/windows, then attach each opening to that full wall.
4. Make each enclosed-room polygon follow the same corners and wall runs; preserve intentional open-plan boundaries only for genuinely open living/entry/dining zones.
5. Correct visibly shifted coordinates from Image 2, especially short exterior steps and wall endpoints. Do not merely copy the first JSON.
6. Re-audit endpoints, T-junctions, door/window placement, and room polygons before returning JSON. Do not silently drop a first-pass door/window that is visible in Image 1; correct its wall attachment instead.
"""
        repaired, repair_error = call_gemini_json(
            api_key, repair_prompt, [floorplan_path, audit_overlay], _WHOLE_HOME_AI_SCHEMA, max_output_tokens=14000)
        if repaired and not repair_error:
            repair_model_name = str(repaired.pop('_floor_engine_model', '') or '')
            repaired_model = _model_from_ai_payload(repaired, source_width, source_height)
            old_errors = len(model.get('geometry_report', {}).get('hard_errors') or [])
            new_errors = len(repaired_model.get('geometry_report', {}).get('hard_errors') or [])
            repaired_alignment = plan_alignment_score(repaired_model, floorplan_path)
            repaired_model['geometry_report']['image_alignment_score'] = repaired_alignment
            if new_errors < old_errors or (new_errors == old_errors and repaired_alignment > initial_alignment + .5):
                model, _ = merge_audit_opening_candidates(model, repaired_model)
                model['geometry_report']['image_alignment_score'] = repaired_alignment
                if repair_model_name:
                    model_name = f'{model_name} + {repair_model_name} topology audit'.strip(' +')
            else:
                if issues:
                    model.setdefault('uncertainties', []).append(
                        f'AI 二次拓扑复核未改善候选（硬错误 {old_errors} → {new_errors}，图像对齐 {initial_alignment} → {repaired_alignment}），请按红色缺口人工修正')
        else:
            model.setdefault('uncertainties', []).append(f'AI 二次拓扑复核失败：{repair_error or "返回为空"}')
    except Exception as ex:
        logger.warning(f'[整屋建模] 拓扑叠图复核失败，保留首轮模型: {ex}')
        model.setdefault('uncertainties', []).append(f'拓扑叠图复核失败：{ex}')
    finally:
        if audit_overlay and os.path.isfile(audit_overlay):
            try:
                os.remove(audit_overlay)
            except OSError:
                pass

    opening_overlay = ''
    try:
        opening_overlay = _render_topology_audit_overlay(model, floorplan_path)
        width_m, depth_m = max(_number(model.get('width_m'), 1), .1), max(_number(model.get('depth_m'), 1), .1)
        wall_contract = [{
            'id': wall.get('id'), 'kind': wall.get('kind'),
            'start': {'x': round(_number(wall.get('start', {}).get('x')) / width_m, 5),
                      'z': round(_number(wall.get('start', {}).get('z')) / depth_m, 5)},
            'end': {'x': round(_number(wall.get('end', {}).get('x')) / width_m, 5),
                    'z': round(_number(wall.get('end', {}).get('z')) / depth_m, 5)},
        } for wall in model.get('walls') or []]
        current_candidates = []
        for opening in model.get('openings') or []:
            center = _opening_center(model, opening) or {'x': 0, 'z': 0}
            current_candidates.append({
                'id': opening.get('id'), 'kind': opening.get('kind'), 'wall_id': opening.get('wall_id'),
                'center': {'x': round(center['x'] / width_m, 5), 'z': round(center['z'] / depth_m, 5)},
                'width_m': opening.get('width_m'),
            })
        opening_prompt = f"""Audit ONLY architectural openings in this complete residential floor plan.
Image 1 is the untouched source plan. Image 2 is the accepted wall graph overlay: red exterior walls, blue interior walls, green current opening candidates.

The wall graph below is immutable. Return every visible door, window, and full-height open connection, using exactly one listed wall_id. Coordinates x,z are normalized 0..1 over the entire image.
Walls: {json.dumps(wall_contract, ensure_ascii=False, separators=(',', ':'))}
Current candidates are hints, not ground truth: {json.dumps(current_candidates, ensure_ascii=False, separators=(',', ':'))}

Inspect the entire perimeter and every room boundary systematically. Count swing-door symbols, sliding/open balcony connections, and windows separately. Do not omit a visible master-bedroom or balcony exterior window. Do not invent an opening from a dimension line or furniture symbol. A door/window is a cut inside a continuous wall, never a missing wall segment. Return only the requested JSON.
"""
        opening_payload, opening_error = call_gemini_json(
            api_key, opening_prompt, [floorplan_path, opening_overlay], _OPENING_AUDIT_SCHEMA, max_output_tokens=7000)
        if opening_payload and not opening_error:
            opening_model_name = str(opening_payload.pop('_floor_engine_model', '') or '')
            audited_openings = model_with_audited_openings(model, opening_payload.get('openings') or [])
            model, _ = merge_audit_opening_candidates(model, audited_openings)
            model.setdefault('uncertainties', []).extend(
                str(value)[:300] for value in opening_payload.get('uncertainties') or [])
            model['geometry_report']['image_alignment_score'] = plan_alignment_score(model, floorplan_path)
            if opening_model_name:
                model_name = f'{model_name} + {opening_model_name} opening audit'.strip(' +')
        else:
            model.setdefault('uncertainties', []).append(f'AI 门窗专项复核失败：{opening_error or "返回为空"}')
    except Exception as ex:
        logger.warning(f'[整屋建模] 门窗专项复核失败，保留前两轮候选: {ex}')
        model.setdefault('uncertainties', []).append(f'门窗专项复核失败：{ex}')
    finally:
        if opening_overlay and os.path.isfile(opening_overlay):
            try:
                os.remove(opening_overlay)
            except OSError:
                pass
    model['geometry_report']['audit_passes'] = 3
    return model, None, model_name


def build_room_generation_contract(project: dict, capture: dict) -> dict:
    """Resolve the single authoritative room/semantic contract for one capture."""
    model = project.get('model') or {}
    camera = capture.get('camera') or {}
    room_id = str(capture.get('room_id') or camera.get('room_id') or infer_camera_room_id(model, camera))
    room = next((row for row in model.get('rooms') or [] if str(row.get('id') or '') == room_id), None)
    if not room:
        raise ValueError('机位没有对应的当前房间，禁止使用整屋通用提示词继续生成')
    semantic = next((
        row for row in model.get('room_contracts') or []
        if str(row.get('room_id') or '') == room_id
    ), None) or {
        'room_id': room_id,
        'profile': room.get('semantic_profile') or _canonical_room_profile(room),
        **copy.deepcopy(_ROOM_CONTRACTS.get(_canonical_room_profile(room), _ROOM_CONTRACTS['other'])),
        'status': 'needs_review',
    }
    objects = []
    for row in model.get('fixed_objects') or []:
        if str(row.get('room_id') or '') != room_id or row.get('review_status') == 'rejected':
            continue
        objects.append({
            key: copy.deepcopy(row.get(key))
            for key in ('id', 'name', 'kind', 'semantic_role', 'purpose', 'position', 'size',
                        'rotation_y_deg', 'required_for_camera', 'observed', 'review_status')
        })
    opening_fields = ('id', 'kind', 'wall_id', 'offset_m', 'width_m', 'height_m', 'sill_height_m')
    openings = []
    polygon = room.get('polygon') or []
    for row in model.get('openings') or []:
        if (row.get('review_status') != 'accepted'
                or _opening_topology_issues(model, row)):
            continue
        center = _opening_center(model, row)
        if center and _point_within_polygon_tolerance(center, polygon, .35):
            openings.append({
                key: copy.deepcopy(row.get(key))
                for key in opening_fields
            })
    origin_scope = str(camera.get('origin_scope') or 'inside_room')
    portal_opening_id = str(camera.get('portal_opening_id') or '')
    entry_opening_id = str(camera.get('entry_opening_id') or '')
    portal_contract = None
    if origin_scope == 'adjacent_portal' or portal_opening_id:
        if not portal_opening_id:
            raise ValueError('相邻空间机位缺少 portal_opening_id，禁止生成')
        portal = next((
            row for row in model.get('openings') or []
            if str(row.get('id') or '') == portal_opening_id
            and row.get('review_status') == 'accepted'
            and not _opening_topology_issues(model, row)
        ), None)
        if not portal:
            raise ValueError(
                f'相邻空间机位引用的开口 {portal_opening_id} 不存在、未接受或语义拓扑无效，禁止生成')
        direct_origin_room_ids = _portal_direct_origin_room_ids(model, room_id, portal)
        reported_origin_room_ids = sorted({
            str(value)[:80] for value in camera.get('origin_room_ids') or [] if str(value)
        })
        actual_origin_room_ids = sorted({
            str(other.get('id') or '') for other in model.get('rooms') or []
            if str(other.get('id') or '') != room_id
            and _point_in_polygon(camera.get('position') or {}, other.get('polygon') or [])
        })
        if (not direct_origin_room_ids or not actual_origin_room_ids
                or any(value not in direct_origin_room_ids for value in actual_origin_room_ids)
                or (reported_origin_room_ids
                    and reported_origin_room_ids != actual_origin_room_ids)):
            raise ValueError(
                f'相邻空间机位未位于开口 {portal_opening_id} 的直接相邻房间，禁止生成')
        portal_opening = {key: copy.deepcopy(portal.get(key)) for key in opening_fields}
        if not any(str(row.get('id') or '') == portal_opening_id for row in openings):
            openings.append(copy.deepcopy(portal_opening))
        portal_contract = {
            'origin_scope': origin_scope,
            'portal_opening_id': portal_opening_id,
            'origin_room_ids': actual_origin_room_ids[:8],
            'direct_origin_room_ids': direct_origin_room_ids[:8],
            'opening': portal_opening,
            'must_preserve_kind': True,
            'leafless_pass_through': portal.get('kind') == 'open_connection',
        }
    entry_contract = None
    if origin_scope == 'doorway_inside' or entry_opening_id:
        if origin_scope != 'doorway_inside' or not entry_opening_id:
            raise ValueError('门洞内侧机位缺少 doorway_inside/entry_opening_id 一致性，禁止生成')
        entry = next((
            row for row in model.get('openings') or []
            if str(row.get('id') or '') == entry_opening_id
            and row.get('review_status') == 'accepted'
            and row.get('kind') in ('door', 'open_connection')
            and not _opening_topology_issues(model, row)
        ), None)
        if not entry or not _doorway_inside_samples(model, room, entry):
            raise ValueError(
                f'门洞内侧机位引用的开口 {entry_opening_id} 不存在、未接受或拓扑无效，禁止生成')
        position = camera.get('position') or {}
        other_room_ids = [
            str(other.get('id') or '') for other in model.get('rooms') or []
            if str(other.get('id') or '') != room_id
            and _point_in_polygon(position, other.get('polygon') or [])
        ]
        if not _point_in_polygon(position, polygon) or other_room_ids:
            raise ValueError(
                f'门洞内侧机位未严格位于目标房间 {room_id}，禁止生成')
        entry_opening = {key: copy.deepcopy(entry.get(key)) for key in opening_fields}
        if not any(str(row.get('id') or '') == entry_opening_id for row in openings):
            openings.append(copy.deepcopy(entry_opening))
        entry_contract = {
            'origin_scope': 'doorway_inside',
            'entry_opening_id': entry_opening_id,
            'opening': entry_opening,
            'camera_inside_target_room': True,
            'traverses_opening': False,
        }
    semantic_free_space_contract = None
    if origin_scope == 'cad_semantic_adjacent_free_space':
        if str(capture.get('material_mode') or '') != 'reference':
            raise ValueError('CAD semantic free-space camera is restricted to audited reference runs')
        evidence = camera.get('reference_contract_validation') or {}
        if (camera.get('source') != 'auto_geometry'
                or evidence.get('landing_source')
                != 'inferred_from_reference_visual_and_cad_anchors'
                or evidence.get('cad_position_pass') is not True
                or evidence.get('collision_pass') is not True
                or evidence.get('visibility_pass') is not True
                or evidence.get('safe_frame_pass') is not True):
            raise ValueError('CAD semantic free-space camera lacks immutable local validation evidence')
        passage_id = str(camera.get('reference_passage_opening_id') or '')
        passage = next((
            row for row in model.get('openings') or []
            if str(row.get('id') or '') == passage_id
            and row.get('review_status') == 'accepted'
            and not _opening_topology_issues(model, row)
        ), None) if passage_id else None
        if passage_id and not passage:
            raise ValueError(
                f'CAD semantic free-space camera references invalid opening {passage_id}')
        passage_opening = ({key: copy.deepcopy(passage.get(key)) for key in opening_fields}
                           if passage else None)
        if passage_opening and not any(
                str(row.get('id') or '') == passage_id for row in openings):
            openings.append(copy.deepcopy(passage_opening))
        semantic_free_space_contract = {
            'origin_scope': origin_scope,
            'inferred_circulation_void': bool(
                camera.get('inferred_circulation_void')),
            'reference_passage_opening_id': passage_id,
            'opening': passage_opening,
            'geometry_authority': 'cad',
            'evidence_method': 'local_reference_camera_and_subject_pixel_gate',
            'camera_not_assigned_to_invented_room': True,
        }
    visible_roles: list[str] = []
    candidate_id = str(capture.get('candidate_id') or camera.get('candidate_id') or '')
    for plan in project.get('auto_camera_plans') or []:
        candidate = next((row for row in plan.get('candidates') or [] if row.get('candidate_id') == candidate_id), None)
        if candidate:
            visible_roles = [str(value) for value in (candidate.get('metrics') or {}).get('visible_roles') or []]
            break
    contract = {
        'room_id': room_id,
        'room_label': str(room.get('label') or room_id),
        'room_type': str(room.get('room_type') or ''),
        'profile': str(semantic.get('profile') or room.get('semantic_profile') or _canonical_room_profile(room)),
        'semantic_status': str(semantic.get('status') or room.get('semantic_status') or 'needs_review'),
        'required_role_groups': copy.deepcopy(semantic.get('required_role_groups') or []),
        'preferred_roles': copy.deepcopy(semantic.get('preferred_roles') or []),
        'min_visible_groups': int(_number(semantic.get('min_visible_groups'), 0)),
        'visible_roles': sorted(set(visible_roles)),
        'fixed_objects': objects,
        'accepted_openings': openings,
        'portal_preservation': portal_contract,
        'entry_opening_audit': entry_contract,
        'cad_semantic_free_space_audit': semantic_free_space_contract,
        'camera': copy.deepcopy(camera),
    }
    scene_recipe = (capture.get('scene_recipe_snapshot')
                    if isinstance(capture.get('scene_recipe_snapshot'), dict) else {})
    if scene_recipe:
        scene_instances = [
            copy.deepcopy(row) for row in scene_recipe.get('instances') or []
            if str(row.get('room_id') or '') == room_id
        ]
        contract['scene_recipe'] = {
            'recipe_id': str(scene_recipe.get('recipe_id') or ''),
            'scene_hash': str(scene_recipe.get('scene_hash') or ''),
            'style_pack_id': str(scene_recipe.get('style_pack_id') or ''),
            'delivery_scope': str(scene_recipe.get('delivery_scope') or ''),
            'materials': copy.deepcopy(scene_recipe.get('materials') or {}),
            'lighting': copy.deepcopy(scene_recipe.get('lighting') or {}),
            'instances': scene_instances,
        }
        contract['fixed_objects'].extend({
            'id': str(row.get('instance_id') or ''),
            'name': str(row.get('semantic_role') or ''),
            'kind': 'scene_recipe_object',
            'semantic_role': str(row.get('semantic_role') or ''),
            'position': copy.deepcopy((row.get('transform') or {}).get('position_m') or {}),
            'rotation_y_deg': (row.get('transform') or {}).get('rotation_y_deg'),
            'size_m': copy.deepcopy(row.get('size_m') or {}),
            'asset_id': str(row.get('asset_id') or ''),
            'scene_hash': str(scene_recipe.get('scene_hash') or ''),
        } for row in scene_instances)
    reference = project.get('reference_contract') if isinstance(project.get('reference_contract'), dict) else {}
    if reference:
        from .whole_home_cad import reference_slot_for_room
        strict_reference = str(capture.get('material_mode') or '') == 'reference'
        explicit_slot_id = str(
            capture.get('reference_slot_id') or camera.get('reference_slot_id') or '').strip()
        contract['reference_contract_id'] = str(reference.get('contract_id') or '')
        contract['reference_role'] = str(reference.get('reference_role') or '')
        contract['geometry_authority'] = str(reference.get('geometry_authority') or '')
        contract['reference_global_hard_constraints'] = copy.deepcopy(
            reference.get('global_hard_constraints') or [])
        contract['reference_style_contract'] = copy.deepcopy(reference.get('style_contract') or {})
        contract['reference_slot'] = reference_slot_for_room(
            reference, room, camera,
            reference_slot_id=explicit_slot_id,
            require_explicit=strict_reference,
        )
        if strict_reference and not contract['reference_slot']:
            raise ValueError('reference_slot_camera_missing: reference 模式禁止房型启发式 slot fallback')
        if strict_reference:
            actual_profile = str(room.get('reference_room_profile') or '')
            expected_profile = str(contract['reference_slot'].get('room_profile') or '')
            bathroom_profiles = {
                'bathroom', 'bathroom_master', 'bathroom_secondary', 'dry_vanity',
            }
            compatible = bool(
                actual_profile and (
                    actual_profile == expected_profile
                    or (actual_profile in bathroom_profiles
                        and expected_profile in bathroom_profiles)))
            if not compatible:
                raise ValueError(
                    'reference_slot_camera_missing: current slot 与 CAD room reference profile 不匹配')
            contract['reference_slot']['cad_room_binding'] = {
                'room_id': room_id,
                'cad_room_profile': actual_profile,
                'reference_composition_profile': expected_profile,
                'binding_mode': (
                    'exact_profile' if actual_profile == expected_profile
                    else 'shared_cad_wet_dry_suite'),
                'geometry_authority': 'cad',
                'instruction': (
                    'This is an alternate composition of the same CAD wet/dry suite; '
                    'do not invent a separate bathroom.'
                    if actual_profile != expected_profile
                    else 'Use this exact CAD room profile.'),
            }
    return contract


def _resolved_reference_asset(contract: dict) -> tuple[dict, str]:
    slot = contract.get('reference_slot') if isinstance(contract.get('reference_slot'), dict) else {}
    asset = slot.get('reference_asset') if isinstance(slot.get('reference_asset'), dict) else {}
    path = str(asset.get('local_path') or '')
    if (not slot or asset.get('status') != 'verified' or not path or not os.path.isfile(path)):
        raise ValueError('reference_assets_unavailable: current slot reference asset is not locally verified')
    return asset, path


def _portal_preservation_instruction(contract: dict) -> str:
    portal = contract.get('portal_preservation')
    if not isinstance(portal, dict):
        return ''
    opening = portal.get('opening') if isinstance(portal.get('opening'), dict) else {}
    identity = (
        f"id={portal.get('portal_opening_id')}, kind={opening.get('kind')}, "
        f"width_m={opening.get('width_m')}, height_m={opening.get('height_m')}, "
        f"sill_height_m={opening.get('sill_height_m')}"
    )
    if opening.get('kind') == 'open_connection':
        return (
            f"MANDATORY PORTAL PRESERVATION — {identity}: this is a leafless pass-through. "
            "It must remain open and must not gain a door slab, hinged or sliding leaf, hinges, "
            "door frame reinterpretation, threshold, floor step, narrowing, closure, or any change of kind."
        )
    return (
        f"MANDATORY PORTAL PRESERVATION — {identity}: preserve the exact opening kind, width, "
        "height, sill, location and camera traversal; do not reinterpret or close it."
    )


def _entry_opening_instruction(contract: dict) -> str:
    entry = contract.get('entry_opening_audit')
    if not isinstance(entry, dict):
        return ''
    opening = entry.get('opening') if isinstance(entry.get('opening'), dict) else {}
    return (
        "DOORWAY-INSIDE CAMERA AUDIT — "
        f"id={entry.get('entry_opening_id')}, kind={opening.get('kind')}, "
        f"width_m={opening.get('width_m')}: the camera is inside the target room near this opening; "
        "it does not traverse an adjacent-room portal. Preserve the opening geometry and keep the "
        "camera on the target-room side."
    )


def build_generation_prompt(project: dict, capture: dict, run: dict, *, pass_name: str,
                            feedback: str = '') -> tuple[str, list[str]]:
    material_mode = str(run.get('material_mode') or 'floor_sample')
    capture = {
        **capture,
        'scene_recipe_snapshot': copy.deepcopy(run.get('scene_recipe_snapshot') or {}),
    }
    contract = build_room_generation_contract(
        project, {**capture, 'material_mode': material_mode})
    prompt_contract = _public_reference_artifacts(
        contract, project.get('reference_contract') or {})
    camera = capture.get('camera') or {}
    portal_instruction = _portal_preservation_instruction(contract)
    entry_instruction = _entry_opening_instruction(contract)
    feedback_text = str(feedback or '').strip()[:3000]
    if pass_name == 'structure':
        paths: list[str] = []
        roles: list[str] = []

        if material_mode == 'reference':
            missing = [
                key for key in ('rgb_path', 'depth_path', 'normal_path', 'edge_path', 'semantic_path')
                if not str(capture.get(key) or '')
            ]
            if missing:
                raise ValueError(
                    f"reference_assets_unavailable: current capture missing ordered buffers {','.join(missing)}")

        def add(path_key: str, role: str) -> None:
            path = str(capture.get(path_key) or '')
            if path:
                paths.append(path)
                roles.append(f'Image {len(paths)} = {role}')

        add('rgb_path', 'approved photometric clay edit canvas from the exact camera.')
        add('depth_path', 'grayscale depth buffer from the exact camera; geometry data only.')
        add('normal_path', 'surface-normal buffer from the exact camera; geometry data only.')
        add('edge_path', 'architectural edge buffer from the exact camera; boundary data only.')
        add('semantic_path', 'semantic-role buffer from the exact camera; use its legend and contract, never copy its colors.')
        reference_asset: dict = {}
        if material_mode == 'reference':
            reference_asset, reference_path = _resolved_reference_asset(contract)
            paths.append(reference_path)
            roles.append(
                f"Image {len(paths)} = audited CURRENT-SLOT 1:1 reference thumbnail "
                f"({reference_asset.get('asset_id')}); style/composition evidence only, never geometry.")
        elif material_mode != 'style_pack' and run.get('style_ref_path'):
            paths.append(run['style_ref_path'])
            roles.append(f'Image {len(paths)} = optional style reference; design language only, never geometry.')
        prompt = f"""Edit the approved clay canvas into one photorealistic photograph of exactly one room.

CURRENT ROOM — THIS IS NOT OPTIONAL:
- Room: {contract['room_label']} ({contract['room_type']}); canonical profile: {contract['profile']}.
- The result must unambiguously read as this room type. A generic corridor, console corner, lounge or another room type is a hard failure.
- Authoritative room contract JSON: {json.dumps(prompt_contract, ensure_ascii=False, separators=(',', ':'))}

VISIBLE INPUT ROLES:
{' '.join(roles)}

IMMUTABLE GEOMETRY AND SEMANTICS:
- Image 1 is the edit canvas. Preserve its exact camera pose, projection, crop, wall/ceiling/floor silhouettes, openings, occlusions, steps and every modeled object volume.
- Only inputs explicitly labelled depth, normal, edge or semantic buffer are buffers. Never treat the current-slot reference thumbnail as a geometry buffer.
- Camera position {camera.get('position')}, target {camera.get('target')}, focal length {camera.get('focal_length_mm', 24)}mm.
- Every fixed object keeps the id/name/semantic_role given in the JSON. A basin cannot become a console; a kitchen run cannot become a sideboard; a bed cannot disappear.
- Do not add, remove, widen, close, relocate or reinterpret any wall, opening, fixed object, room or camera.
- {portal_instruction or 'No adjacent-portal camera contract applies to this capture.'}
- {entry_instruction or 'No doorway-inside camera audit applies to this capture.'}
- Do not reveal geometry behind the camera or invent an unmodeled adjacent room.
- {('Keep the CAD-authentic neutral floor; reference mode may refine style and composition later but must never substitute a flooring product.' if material_mode == 'reference' else 'Use the exact locked SceneRecipe materials, including its light warm-oak floor; do not substitute a product SKU.' if material_mode == 'style_pack' else 'Keep a neutral temporary floor; the exact flooring product is applied only after this structure passes QA.')}
{('- The audited reference is a square 1:1 thumbnail. Translate only its style and compositional intent into the native horizontal 4:3 CAD-valid camera. Never crop, stretch, pad or reproduce the reference geometry to force its aspect ratio.' if material_mode == 'reference' else '')}
{('- The locked SceneRecipe JSON is the sole furnishing, palette and lighting authority. Every listed scene instance must remain at its modeled volume and position.' if material_mode == 'style_pack' else '')}

DESIGN:
- Style: {run.get('style') or 'modern natural'}.
- Lighting: {run.get('lighting') or 'natural daylight'}.
- Customer brief: {run.get('prompt') or 'realistic, restrained, buildable residential interior'}.
{('- CORRECTION FROM THE PREVIOUS FAILED ATTEMPT: ' + feedback_text) if feedback_text else ''}

Output one photograph only. No labels, diagrams, legends, buffers, swatches, borders, collages or text."""
        return prompt, paths

    structure_path = str(capture.get('structure_path') or '')
    if material_mode == 'reference':
        missing = [
            key for key in ('structure_path', 'rgb_path', 'depth_path', 'semantic_path')
            if not str(capture.get(key) or '')
        ]
        if missing:
            raise ValueError(
                f"reference_assets_unavailable: current material pass missing ordered inputs {','.join(missing)}")
        reference_asset, reference_path = _resolved_reference_asset(contract)
        paths = [structure_path, capture['rgb_path'], capture['depth_path']]
        roles = [
            'Image 1 = structure photograph that already passed the structure gate.',
            'Image 2 = approved clay canvas for immutable camera and CAD boundaries.',
            'Image 3 = depth buffer for immutable depth order.',
        ]
        if capture.get('semantic_path'):
            paths.append(capture['semantic_path'])
            roles.append(f'Image {len(paths)} = semantic buffer; never copy its colors.')
        paths.append(reference_path)
        roles.append(
            f"Image {len(paths)} = audited CURRENT-SLOT 1:1 reference thumbnail "
            f"({reference_asset.get('asset_id')}); style/composition only, never geometry.")
        prompt = f"""Edit Image 1 in place only to refine its materials and lighting under the audited reference contract.

CURRENT ROOM CONTRACT: {json.dumps(prompt_contract, ensure_ascii=False, separators=(',', ':'))}
INPUT ROLES: {' '.join(roles)}

This is reference mode, not flooring-product mode. Do not replace, recolor or advertise the floor as a product sample. Preserve the existing floor region, scale and structural relationship from Image 1. The reference contract controls style and composition only; CAD remains the sole geometry authority. Keep every camera pixel relationship, wall, ceiling, opening, column, fixture, observed object, shadow direction and depth order. Never invent a step, threshold, wall, door, window, mirror opening, fixture or adjacent room.
The audited reference is a square 1:1 thumbnail. Translate only its style and compositional intent into the native horizontal 4:3 CAD-valid camera. Never crop, stretch, pad or reproduce its geometry to force the aspect ratio.
{portal_instruction or 'No adjacent-portal camera contract applies to this capture.'}
{entry_instruction or 'No doorway-inside camera audit applies to this capture.'}
{('CORRECT THESE FAILURES FROM THE PREVIOUS MATERIAL ATTEMPT: ' + feedback_text) if feedback_text else ''}
Output the edited photograph only."""
        return prompt, paths

    if material_mode == 'style_pack':
        paths = [structure_path, capture['rgb_path'], capture['depth_path']]
        roles = [
            'Image 1 = structure photograph that already passed the structure gate.',
            'Image 2 = approved scene-recipe clay canvas for immutable camera, objects and boundaries.',
            'Image 3 = depth buffer for immutable depth order.',
        ]
        if capture.get('semantic_path'):
            paths.append(capture['semantic_path'])
            roles.append(f'Image {len(paths)} = semantic buffer; never copy its colors.')
        prompt = f"""Edit Image 1 in place only to finish the locked modern-warm-natural SceneRecipe.

CURRENT ROOM CONTRACT: {json.dumps(prompt_contract, ensure_ascii=False, separators=(',', ':'))}
INPUT ROLES: {' '.join(roles)}

Use the exact locked palette: warm off-white matte walls, light natural warm-oak floor, oatmeal and warm-grey textiles, restrained walnut and charcoal accents, soft daylight with 3200K warm-neutral fill, residential-balanced AgX exposure. This is a marketing concept, not a product-SKU or construction claim. Preserve every camera pixel relationship, wall, ceiling, opening, scene-recipe object, fixture, occlusion, shadow direction and depth order from Image 1. Do not add, delete, move, resize, reinterpret or replace any modeled object. Do not introduce labels, borders, moodboards, logos or text.
{portal_instruction or 'No adjacent-portal camera contract applies to this capture.'}
{entry_instruction or 'No doorway-inside camera audit applies to this capture.'}
{('CORRECT THESE FAILURES FROM THE PREVIOUS MATERIAL ATTEMPT: ' + feedback_text) if feedback_text else ''}
Output the edited photograph only."""
        return prompt, paths

    paths = [structure_path, run['floor_path'], capture['rgb_path'], capture['depth_path']]
    roles = [
        'Image 1 = structure photograph that already passed the structure gate.',
        'Image 2 = exact flooring product sample.',
        'Image 3 = approved clay canvas for immutable camera and boundaries.',
        'Image 4 = depth buffer for immutable depth order.',
    ]
    if capture.get('semantic_path'):
        paths.append(capture['semantic_path'])
        roles.append(f'Image {len(paths)} = semantic buffer; never copy its colors.')
    prompt = f"""Edit Image 1 in place and replace ONLY its visible interior floor finish with Image 2.

CURRENT ROOM CONTRACT: {json.dumps(prompt_contract, ensure_ascii=False, separators=(',', ':'))}
INPUT ROLES: {' '.join(roles)}

Keep every architectural boundary, camera pixel relationship, crop, wall, ceiling, opening, fixture, furnishing, shadow direction and depth order from Image 1. Apply Image 2 hue, undertone, grain family, plank character, gloss and realistic scale only to visible interior floor pixels. Preserve perspective, occlusion and contact shadows. Never apply the product to walls, cabinetry, ceilings, furniture, rugs, glazing or outdoor areas. Do not redesign or restyle anything.
{portal_instruction or 'No adjacent-portal camera contract applies to this capture.'}
{entry_instruction or 'No doorway-inside camera audit applies to this capture.'}
{('CORRECT THESE FAILURES FROM THE PREVIOUS MATERIAL ATTEMPT: ' + feedback_text) if feedback_text else ''}
Output the edited photograph only."""
    return prompt, paths


_LOCAL_GATE_SIZE = (512, 384)
_STRUCTURE_LOCAL_GATE_THRESHOLDS = {
    'semantic_coverage_12_min': 0.80,
    'semantic_mean_distance_max': 9.0,
    'normal_coverage_12_min': 0.62,
}
_FINAL_LOCAL_GATE_THRESHOLDS = {
    'structure_coverage_12_min': 0.78,
    'structure_mean_distance_max': 10.5,
}


def _load_gate_image(path: str) -> Optional[np.ndarray]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with Image.open(path) as source:
            rgb = ImageOps.fit(
                source.convert('RGB'), _LOCAL_GATE_SIZE, Image.Resampling.LANCZOS,
            )
            return np.asarray(rgb, dtype=np.uint8)
    except Exception:
        return None


def _gate_edge_mask(image: np.ndarray) -> np.ndarray:
    """Extract stable, coarse geometry edges from render buffers or photographs."""
    blurred = cv2.GaussianBlur(image, (7, 7), 1.4)
    gray = cv2.cvtColor(blurred, cv2.COLOR_RGB2GRAY)
    canny = cv2.Canny(gray, 42, 118, L2gradient=True) > 0
    channel_magnitudes = []
    for channel in cv2.split(blurred):
        sx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
        sy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
        channel_magnitudes.append(cv2.magnitude(sx, sy))
    magnitude = np.maximum.reduce(channel_magnitudes)
    positive = magnitude[magnitude > 0]
    adaptive = float(np.percentile(positive, 68)) if positive.size else 255.0
    sobel = magnitude >= max(22.0, min(72.0, adaptive))
    edges = np.asarray(canny | sobel, dtype=np.uint8)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
    edges[:2, :] = False
    edges[-2:, :] = False
    edges[:, :2] = False
    edges[:, -2:] = False
    return edges


def _edge_alignment(reference_edges: np.ndarray, candidate_edges: np.ndarray,
                    radius: float = 12.0) -> Optional[dict]:
    if int(reference_edges.sum()) < 24 or int(candidate_edges.sum()) < 24:
        return None
    distance = cv2.distanceTransform((~candidate_edges).astype(np.uint8), cv2.DIST_L2, 5)
    samples = distance[reference_edges]
    return {
        'coverage_12': round(float(np.mean(samples <= radius)), 4),
        'mean_distance': round(float(np.mean(samples)), 3),
        'reference_edge_pixels': int(reference_edges.sum()),
        'candidate_edge_pixels': int(candidate_edges.sum()),
    }


def _save_local_gate_overlay(project: dict, capture: dict, artifact_id: str, phase: str,
                             candidate: np.ndarray, reference_edges: np.ndarray,
                             candidate_edges: np.ndarray) -> str:
    project_id = _safe_asset_name(project.get('project_id'), 'project')
    capture_id = _safe_asset_name(capture.get('capture_id'), 'capture')
    folder = os.path.join(ASSET_DIR, project_id, capture_id, 'local_gates')
    os.makedirs(folder, exist_ok=True)
    stem = _safe_asset_name(artifact_id, new_id('gate'))
    path = os.path.join(folder, f'{stem}_{phase}_alignment.png')
    if os.path.exists(path):
        path = os.path.join(folder, f'{stem}_{phase}_{uuid.uuid4().hex[:10]}_alignment.png')
    gray = cv2.cvtColor(candidate, cv2.COLOR_RGB2GRAY)
    overlay = np.repeat(gray[:, :, None], 3, axis=2)
    overlay = (overlay.astype(np.float32) * 0.55).astype(np.uint8)
    overlay[candidate_edges] = np.array([255, 48, 48], dtype=np.uint8)
    overlay[reference_edges] = np.array([0, 235, 255], dtype=np.uint8)
    overlap = reference_edges & candidate_edges
    overlay[overlap] = np.array([80, 255, 80], dtype=np.uint8)
    Image.fromarray(overlay, mode='RGB').save(path, format='PNG')
    return path


def evaluate_structure_local_gate(project: dict, capture: dict, candidate_path: str,
                                  artifact_id: str) -> dict:
    """Fail-closed deterministic preflight before the structure Gemini reviewer."""
    candidate = _load_gate_image(candidate_path)
    normal = _load_gate_image(str(capture.get('normal_path') or ''))
    semantic = _load_gate_image(str(capture.get('semantic_path') or ''))
    missing = []
    if candidate is None:
        missing.append('candidate')
    if normal is None:
        missing.append('normal')
    if semantic is None:
        missing.append('semantic')
    result = {
        'version': 'structure-local-alignment-v1', 'phase': 'structure',
        'status': 'done', 'verdict': 'fail', 'gate_pass': False,
        'thresholds': copy.deepcopy(_STRUCTURE_LOCAL_GATE_THRESHOLDS),
        'resize': {'width': _LOCAL_GATE_SIZE[0], 'height': _LOCAL_GATE_SIZE[1]},
        'methods': ['gaussian_blur_7', 'canny_42_118', 'sobel_rgb', 'distance_transform_l2'],
        'normal_coverage_12': None, 'normal_mean_distance': None,
        'semantic_coverage_12': None, 'semantic_mean_distance': None,
        'missing_buffers': missing, 'invalid_buffers': [], 'overlay_path': '',
    }
    if missing:
        result['summary'] = f"本地图像对齐门禁已阻断：缺少 {', '.join(missing)} 缓冲"
        return result
    candidate_edges = _gate_edge_mask(candidate)
    normal_edges = _gate_edge_mask(normal)
    semantic_edges = _gate_edge_mask(semantic)
    normal_metrics = _edge_alignment(normal_edges, candidate_edges)
    semantic_metrics = _edge_alignment(semantic_edges, candidate_edges)
    if normal_metrics is None:
        result['invalid_buffers'].append('normal:no_detectable_edges')
    if semantic_metrics is None:
        result['invalid_buffers'].append('semantic:no_detectable_edges')
    if normal_metrics:
        result.update(
            normal_coverage_12=normal_metrics['coverage_12'],
            normal_mean_distance=normal_metrics['mean_distance'],
            normal_reference_edge_pixels=normal_metrics['reference_edge_pixels'],
        )
    if semantic_metrics:
        result.update(
            semantic_coverage_12=semantic_metrics['coverage_12'],
            semantic_mean_distance=semantic_metrics['mean_distance'],
            semantic_reference_edge_pixels=semantic_metrics['reference_edge_pixels'],
            candidate_edge_pixels=semantic_metrics['candidate_edge_pixels'],
        )
    combined_reference = normal_edges | semantic_edges
    try:
        result['overlay_path'] = _save_local_gate_overlay(
            project, capture, artifact_id, 'structure', candidate, combined_reference, candidate_edges)
    except Exception as ex:
        result['artifact_error'] = str(ex)
    thresholds = result['thresholds']
    passed = bool(not result.get('artifact_error') and normal_metrics and semantic_metrics
                  and semantic_metrics['coverage_12'] >= thresholds['semantic_coverage_12_min']
                  and semantic_metrics['mean_distance'] <= thresholds['semantic_mean_distance_max']
                  and normal_metrics['coverage_12'] >= thresholds['normal_coverage_12_min'])
    result.update(
        verdict='pass' if passed else 'fail', gate_pass=passed,
        summary=(
            f"本地图像对齐{'通过' if passed else '失败'}：semantic coverage@12="
            f"{result['semantic_coverage_12']}、mean={result['semantic_mean_distance']}；"
            f"normal coverage@12={result['normal_coverage_12']}"
            + (f"；证据图保存失败：{result['artifact_error']}" if result.get('artifact_error') else '')
        ),
    )
    return result


def evaluate_final_local_gate(project: dict, capture: dict, structure_path: str,
                              candidate_path: str, artifact_id: str) -> dict:
    """Reject material edits that redraw geometry before invoking Gemini final QA."""
    structure = _load_gate_image(structure_path)
    candidate = _load_gate_image(candidate_path)
    missing = []
    if structure is None:
        missing.append('structure')
    if candidate is None:
        missing.append('candidate')
    result = {
        'version': 'material-local-geometry-v1', 'phase': 'final',
        'status': 'done', 'verdict': 'fail', 'gate_pass': False,
        'thresholds': copy.deepcopy(_FINAL_LOCAL_GATE_THRESHOLDS),
        'resize': {'width': _LOCAL_GATE_SIZE[0], 'height': _LOCAL_GATE_SIZE[1]},
        'methods': ['gaussian_blur_7', 'canny_42_118', 'sobel_rgb', 'distance_transform_l2'],
        'structure_coverage_12': None, 'structure_mean_distance': None,
        'missing_buffers': missing, 'invalid_buffers': [], 'overlay_path': '',
    }
    if missing:
        result['summary'] = f"本地材质几何门禁已阻断：缺少 {', '.join(missing)} 图像"
        return result
    structure_edges = _gate_edge_mask(structure)
    candidate_edges = _gate_edge_mask(candidate)
    metrics = _edge_alignment(structure_edges, candidate_edges)
    if metrics is None:
        result['invalid_buffers'].append('structure_or_candidate:no_detectable_edges')
    else:
        result.update(
            structure_coverage_12=metrics['coverage_12'],
            structure_mean_distance=metrics['mean_distance'],
            structure_edge_pixels=metrics['reference_edge_pixels'],
            candidate_edge_pixels=metrics['candidate_edge_pixels'],
        )
    try:
        result['overlay_path'] = _save_local_gate_overlay(
            project, capture, artifact_id, 'final', candidate, structure_edges, candidate_edges)
    except Exception as ex:
        result['artifact_error'] = str(ex)
    thresholds = result['thresholds']
    passed = bool(not result.get('artifact_error') and metrics
                  and metrics['coverage_12'] >= thresholds['structure_coverage_12_min']
                  and metrics['mean_distance'] <= thresholds['structure_mean_distance_max'])
    result.update(
        verdict='pass' if passed else 'fail', gate_pass=passed,
        summary=(
            f"本地材质几何{'通过' if passed else '失败'}：structure coverage@12="
            f"{result['structure_coverage_12']}、mean={result['structure_mean_distance']}"
            + (f"；证据图保存失败：{result['artifact_error']}" if result.get('artifact_error') else '')
        ),
    )
    return result


_QA_SCHEMA = {
    'type': 'OBJECT', 'properties': {
        'geometry_score': {'type': 'NUMBER'}, 'camera_score': {'type': 'NUMBER'},
        'opening_score': {'type': 'NUMBER'}, 'material_score': {'type': 'NUMBER'},
        'room_identity_score': {'type': 'NUMBER'}, 'fixed_object_score': {'type': 'NUMBER'},
        'hard_fail': {'type': 'BOOLEAN'}, 'summary': {'type': 'STRING'},
        'checks': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'constraint_id': {'type': 'STRING'}, 'constraint': {'type': 'STRING'},
            'status': {'type': 'STRING', 'enum': ['pass', 'fail', 'uncertain']},
            'severity': {'type': 'STRING', 'enum': ['hard', 'soft']}, 'evidence': {'type': 'STRING'},
        }, 'required': ['constraint_id', 'constraint', 'status', 'severity', 'evidence']}},
    }, 'required': ['geometry_score', 'camera_score', 'opening_score', 'material_score',
                    'room_identity_score', 'fixed_object_score', 'hard_fail', 'summary', 'checks'],
}


def _whole_home_qa_constraints(contract: dict, phase: str,
                               material_mode: str = 'floor_sample') -> list[dict]:
    rows = [
        {'constraint_id': 'C001', 'category': 'room_identity', 'severity': 'hard',
         'constraint': f"The candidate must unambiguously be {contract.get('room_label')} ({contract.get('profile')})"},
        {'constraint_id': 'C002', 'category': 'camera', 'severity': 'hard',
         'constraint': 'Camera pose, projection, crop and visible depth order must match the approved buffers'},
        {'constraint_id': 'C003', 'category': 'geometry', 'severity': 'hard',
         'constraint': 'All visible wall, ceiling, floor, column and step silhouettes must remain unchanged'},
        {'constraint_id': 'C004', 'category': 'openings', 'severity': 'hard',
         'constraint': f"Opening count, kind, location and size must match: {json.dumps(contract.get('accepted_openings') or [], ensure_ascii=False, separators=(',', ':'))}"},
        {'constraint_id': 'C005', 'category': 'fixed_objects', 'severity': 'hard',
         'constraint': f"Every modeled fixed object must retain identity, position and volume: {json.dumps(contract.get('fixed_objects') or [], ensure_ascii=False, separators=(',', ':'))}"},
        {'constraint_id': 'C006', 'category': 'visibility', 'severity': 'hard',
         'constraint': 'No room or object behind the camera may become visible and no unmodeled adjacent space may be invented'},
        {'constraint_id': 'C007', 'category': 'artifacts', 'severity': 'hard',
         'constraint': 'No plan graphics, arrows, labels, semantic colors, legends, swatches, borders, collages or text may appear'},
    ]
    portal_instruction = _portal_preservation_instruction(contract)
    if portal_instruction:
        rows.append({
            'constraint_id': 'C008', 'category': 'portal_preservation', 'severity': 'hard',
            'constraint': (
                f'{portal_instruction} The evidence must directly show preservation; '
                'if the portal is absent, occluded, ambiguous or cannot be compared, report uncertain.'
            ),
        })
    if material_mode == 'reference':
        validation = ((contract.get('camera') or {})
                      .get('reference_contract_validation') or {})
        subjects = validation.get('must_show_subjects') or []
        for index, subject_row in enumerate(subjects, 1):
            if not isinstance(subject_row, dict):
                continue
            subject = str(subject_row.get('subject') or '').strip()
            anchor_id = str(subject_row.get('anchor_id') or '').strip()
            if not subject:
                continue
            rows.append({
                'constraint_id': f'C2{index:02d}',
                'category': 'reference_required_subject',
                'severity': 'hard',
                'constraint': (
                    f'The current-slot candidate must visibly and unambiguously show '
                    f'"{subject}" (approved anchor_id={anchor_id or "unknown"}). '
                    'Verify this subject itself in the candidate against the subject-ID/semantic '
                    'evidence; another fixture, a reflection, or a plausible room identity does '
                    'not count. Report fail if absent and uncertain if occluded or not directly '
                    'comparable.'
                ),
            })
    if phase == 'final':
        material_rows = ([
            {'constraint_id': 'C101', 'category': 'structure_preservation', 'severity': 'hard',
             'constraint': 'The final image must preserve the already-approved structure image without architectural, fixture or furniture drift'},
            {'constraint_id': 'C102', 'category': 'floor_scope', 'severity': 'hard',
             'constraint': 'The flooring product may appear only on visible interior floor surfaces'},
            {'constraint_id': 'C103', 'category': 'material', 'severity': 'hard',
             'constraint': 'Floor hue, undertone, grain family, layout, plank scale and finish must faithfully match the product sample'},
        ] if material_mode == 'floor_sample' else ([
            {'constraint_id': 'C101', 'category': 'structure_preservation', 'severity': 'hard',
             'constraint': 'The final image must preserve the CAD-authoritative structure, fixed objects and camera without drift'},
            {'constraint_id': 'C102', 'category': 'reference_scope', 'severity': 'hard',
             'constraint': 'The reference may control style and composition only; it must not replace the floor as a product or alter CAD geometry'},
            {'constraint_id': 'C103', 'category': 'reference_slot', 'severity': 'hard',
             'constraint': f"The image must satisfy this exact audited slot and no other: {json.dumps(contract.get('reference_slot') or {}, ensure_ascii=False, separators=(',', ':'))}"},
        ] if material_mode == 'reference' else [
            {'constraint_id': 'C101', 'category': 'structure_preservation', 'severity': 'hard',
             'constraint': 'The final image must preserve the approved structure, camera and every locked SceneRecipe instance without drift'},
            {'constraint_id': 'C102', 'category': 'style_pack_scope', 'severity': 'hard',
             'constraint': 'Use only the locked modern-warm-natural palette and lighting; do not introduce product-SKU, price or construction claims'},
            {'constraint_id': 'C103', 'category': 'scene_recipe', 'severity': 'hard',
             'constraint': f"Every instance, material and light must match the locked SceneRecipe: {json.dumps(contract.get('scene_recipe') or {}, ensure_ascii=False, separators=(',', ':'))}"},
        ]))
        rows.extend(material_rows)
    return rows


def evaluate_whole_home_phase(api_key: str, project: dict, capture: dict, candidate_path: str,
                              floor_path: str, *, phase: str,
                              structure_path: str = '', material_path: str = '') -> tuple[dict, Optional[str]]:
    if phase not in ('structure', 'final'):
        raise ValueError('phase must be structure or final')
    contract = build_room_generation_contract(project, capture)
    material_mode = str(capture.get('material_mode') or 'floor_sample')
    prompt_contract = _public_reference_artifacts(
        contract, project.get('reference_contract') or {})
    expected = _whole_home_qa_constraints(prompt_contract, phase, material_mode)
    paths: list[str] = []
    roles: list[str] = []

    def add(path: str, role: str) -> None:
        if path and path not in paths:
            paths.append(path)
            roles.append(f'Image {len(paths)} = {role}')

    add(str(capture.get('plan_overlay_path') or ''), 'original plan with the current room and exact camera highlighted; QA ground truth only.')
    add(str(capture.get('rgb_path') or ''), 'approved clay RGB canvas.')
    add(str(capture.get('depth_path') or ''), 'approved depth buffer.')
    add(str(capture.get('normal_path') or ''), 'approved normal buffer.')
    add(str(capture.get('edge_path') or ''), 'approved architectural edge buffer.')
    add(str(capture.get('semantic_path') or ''), 'approved semantic-role buffer; colors identify roles only.')
    reference_asset: dict = {}
    if material_mode == 'reference':
        reference_asset, reference_path = _resolved_reference_asset(contract)
        add(
            reference_path,
            f"audited CURRENT-SLOT reference thumbnail ({reference_asset.get('asset_id')}); "
            "style/composition comparison only, never geometry.",
        )
    final_is_raw_material = False
    if phase == 'final':
        add(structure_path, 'first-pass structure image that passed the structure gate.')
        final_is_raw_material = bool(
            material_path and candidate_path
            and os.path.realpath(material_path) == os.path.realpath(candidate_path)
        )
        add(material_path, 'raw flooring edit and final candidate.' if final_is_raw_material
            else 'raw flooring edit before optional local color correction.')
    if not final_is_raw_material:
        add(candidate_path, 'candidate being evaluated.')
    if phase == 'final' and material_mode == 'floor_sample':
        add(floor_path, 'authoritative flooring product sample.')
    reference_snapshot = ({
        'asset_id': reference_asset.get('asset_id') or '',
        'sha256': reference_asset.get('sha256') or '',
        'width': reference_asset.get('width') or reference_asset.get('expected_width'),
        'height': reference_asset.get('height') or reference_asset.get('expected_height'),
        'scene_id': ((contract.get('reference_slot') or {}).get('reference_viewpoint') or {}).get('scene_id'),
    } if reference_asset else {})
    prompt = f"""You are an adversarial architectural regression gate. Do not reward beauty or plausibility.

PHASE: {phase}
CURRENT ROOM CONTRACT JSON: {json.dumps(prompt_contract, ensure_ascii=False, separators=(',', ':'))}
IMAGE ROLES: {' '.join(roles)}
MANDATORY CHECKLIST: {json.dumps(expected, ensure_ascii=False, separators=(',', ':'))}

Return exactly one checks entry for every mandatory constraint_id, copying the id unchanged. Do not merge or omit rows. Compare the current-room overlay, geometry buffers and semantic contract against the candidate. In final phase, compare the approved structure image and apply the declared material mode ({material_mode}); only floor_sample mode has a flooring product sample. The current-room plan overlay and its buffers are the only geometry authority. In reference mode inspect only the explicitly labelled CURRENT-SLOT thumbnail and do not use it as geometry evidence; no other slot is available. In style_pack mode compare every locked SceneRecipe instance and the fixed modern-warm-natural material/lighting contract. Every C2xx row is a separate required-subject visibility check: name where that exact subject appears in the candidate and compare it to its approved subject/semantic evidence. Never pass a C2xx row merely because the room type is plausible or because a different fixture is present. Use uncertain whenever evidence is occluded or insufficient; never infer pass from a plausible-looking image. Any changed room identity, camera, wall, opening, fixed-object identity, missing current-slot subject, impossible visibility, forbidden product-floor replacement, non-floor material spill or omitted hard evidence is a hard failure. Never award 100 merely because the image looks attractive. Score 0..100 and return JSON only."""
    payload, error = call_gemini_json(api_key, prompt, paths, _QA_SCHEMA, max_output_tokens=6000)
    if error or not payload:
        return {
            'status': 'unavailable', 'phase': phase, 'hard_fail': True,
            'verification_incomplete': True, 'gate_pass': False,
            'eligible_for_recommendation': False, 'total': None,
            'summary': error or 'QA 无返回', 'checks': [],
            'reference_asset': reference_snapshot,
        }, error
    evaluator_model = str(payload.pop('_floor_engine_model', '') or '')
    expected_by_id = {row['constraint_id']: row for row in expected}
    answers: dict[str, dict] = {}
    for source in payload.get('checks') or []:
        if not isinstance(source, dict):
            continue
        constraint_id = str(source.get('constraint_id') or '')
        expected_row = expected_by_id.get(constraint_id)
        if not expected_row or constraint_id in answers:
            continue
        answers[constraint_id] = {
            'constraint_id': constraint_id,
            'category': expected_row['category'],
            'constraint': expected_row['constraint'],
            'status': source.get('status') if source.get('status') in ('pass', 'fail', 'uncertain') else 'uncertain',
            'severity': expected_row['severity'],
            'evidence': str(source.get('evidence') or '')[:800],
        }
    checks = []
    for expected_row in expected:
        answer = answers.get(expected_row['constraint_id'])
        checks.append(answer or {
            **expected_row, 'status': 'uncertain',
            'evidence': 'Evaluator omitted this mandatory checklist item.',
        })
    hard_failure = any(row['severity'] == 'hard' and row['status'] != 'pass' for row in checks)
    verification_incomplete = any(row['severity'] == 'hard' and row['status'] == 'uncertain' for row in checks)
    score_keys = ('geometry_score', 'camera_score', 'opening_score', 'material_score',
                  'room_identity_score', 'fixed_object_score')
    scores = {key: _clamp(payload.get(key), 0, 100, 0) for key in score_keys}
    if phase == 'structure':
        total = round(scores['geometry_score'] * .25 + scores['camera_score'] * .2
                      + scores['opening_score'] * .15 + scores['room_identity_score'] * .2
                      + scores['fixed_object_score'] * .2, 1)
    else:
        total = round(scores['geometry_score'] * .2 + scores['camera_score'] * .15
                      + scores['opening_score'] * .1 + scores['material_score'] * .2
                      + scores['room_identity_score'] * .15 + scores['fixed_object_score'] * .2, 1)
    gate_pass = not bool(payload.get('hard_fail')) and not hard_failure
    return {
        'status': 'done', 'phase': phase, **scores, 'total': total,
        'hard_fail': not gate_pass, 'verification_incomplete': verification_incomplete,
        'gate_pass': gate_pass, 'eligible_for_recommendation': gate_pass,
        'summary': str(payload.get('summary') or '')[:1000], 'checks': checks,
        'evaluator_model': evaluator_model,
        'reference_asset': reference_snapshot,
    }, None


def evaluate_whole_home_result(api_key: str, project: dict, capture: dict, result_path: str,
                               floor_path: str) -> tuple[dict, Optional[str]]:
    """Compatibility wrapper for persisted runs and the public QA retry route."""
    return evaluate_whole_home_phase(
        api_key, project, capture, result_path, floor_path,
        phase='final', structure_path=str(capture.get('structure_path') or ''),
        material_path=str(capture.get('material_path') or ''),
    )
