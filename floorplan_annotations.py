# -*- coding: utf-8 -*-
"""户型人工标注数据层：版本、操作日志、几何验收与训练数据导出。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import threading
import time
import uuid
import zipfile
from typing import Any, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .config import MAIN_OUTPUT_DIR, logger


FLOORPLAN_ROOT = os.path.join(MAIN_OUTPUT_DIR, '_floorplan_suites')
ANALYSIS_DIR = os.path.join(FLOORPLAN_ROOT, 'analyses')
SUITE_DIR = os.path.join(FLOORPLAN_ROOT, 'suites')
ANNOTATION_DIR = os.path.join(FLOORPLAN_ROOT, 'annotations')
EXPORT_DIR = os.path.join(FLOORPLAN_ROOT, 'exports')
for _folder in (ANNOTATION_DIR, EXPORT_DIR):
    os.makedirs(_folder, exist_ok=True)

_log_lock = threading.RLock()


def _atomic_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{uuid.uuid4().hex}.tmp'
    try:
        with open(tmp, 'x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def annotation_dir(analysis_id: str) -> str:
    safe_id = os.path.basename(analysis_id)
    path = os.path.join(ANNOTATION_DIR, safe_id)
    os.makedirs(path, exist_ok=True)
    return path


def operations_path(analysis_id: str) -> str:
    return os.path.join(annotation_dir(analysis_id), 'operations.jsonl')


def prepare_annotation_source(analysis_id: str, source_path: str) -> dict:
    """复制不可变标注原图并记录哈希/尺寸；不依赖上传目录生命周期。"""
    extension = os.path.splitext(source_path)[1].lower()
    extension = extension if extension in ('.png', '.jpg', '.jpeg', '.webp') else '.png'
    destination = os.path.join(annotation_dir(analysis_id), f'source{extension}')
    if os.path.realpath(source_path) != os.path.realpath(destination):
        shutil.copy2(source_path, destination)
    digest = hashlib.sha256()
    with open(destination, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    with Image.open(destination) as image:
        width, height = image.size
    return {
        'path': destination, 'sha256': digest.hexdigest(), 'width': width, 'height': height,
        'original_name': os.path.basename(source_path),
    }


def ensure_annotation_v2(entry: dict) -> dict:
    """惰性适配旧分析到 schema v3；保留旧函数名避免破坏调用方。"""
    previous_schema = int(entry.get('schema_version') or 2)
    entry['schema_version'] = 3
    entry.setdefault('revision', 0)
    entry.setdefault('annotation_status', 'draft')
    entry.setdefault('training_consent', False)
    entry.setdefault('training_eligible', False)
    entry.setdefault('verified_at', None)
    entry.setdefault('verified_revision', 0)
    entry.setdefault('verified_by', '')
    entry.setdefault('annotator_id', 'local-user')
    entry.setdefault('geometry_report', {'hard_errors': [], 'warnings': []})
    entry.setdefault('ai_initial_path', '')
    entry.setdefault('ai_model', '')
    entry.setdefault('prompt_version', 'floorplan-v1')
    entry.setdefault('source', {})
    entry.setdefault('spatial_plans', {})
    entry.setdefault('view_proxies', {})
    openings = entry.setdefault('openings', [])
    for opening in openings:
        if previous_schema < 3:
            opening.setdefault('source', 'legacy')
            opening.setdefault('review_status', 'accepted')
        else:
            opening.setdefault('source', 'manual')
            opening.setdefault('review_status', 'accepted')
    if 'openings_review_status' not in entry:
        # 历史空列表无法证明用户确实确认过“无门窗”，新生成前需补一次确认。
        entry['openings_review_status'] = 'confirmed' if previous_schema < 3 and openings else 'pending'
    for room in entry.get('rooms') or []:
        room.setdefault('space_kind', _infer_space_kind(room))
        room.setdefault('source', 'legacy' if entry.get('revision', 0) == 0 else 'human')
        cameras = room.setdefault('cameras', [])
        legacy = room.get('camera')
        if not cameras and legacy:
            cameras.append({
                'id': f"camera_{uuid.uuid4().hex[:12]}", 'name': '机位 1',
                'position': legacy.get('position'), 'target': legacy.get('target'),
                'height_m': None, 'focal_length_mm': None, 'purpose': 'wide',
                'source': 'legacy', 'confirmed': True, 'enabled_for_generation': True,
            })
        room.setdefault('primary_camera_id', cameras[0].get('id', '') if cameras else '')
    return entry


def accepted_openings(entry: dict, room_id: str = '') -> list[dict]:
    """返回可进入规划、灰模和生图硬约束的人工确认开口。"""
    rows = []
    for opening in entry.get('openings') or []:
        if opening.get('review_status', 'accepted') != 'accepted':
            continue
        if room_id and room_id not in (opening.get('room_ids') or []):
            continue
        rows.append(opening)
    return rows


def view_proxy_source_hash(entry: dict, room: dict, camera: dict, plan: dict,
                           aspect_ratio: str) -> str:
    """灰模来源指纹：任何结构、机位、锁定规划或画幅变化都会使旧灰模失效。"""
    plan_copy = copy.deepcopy(plan or {})
    for key in ('overlay_path', 'overlay_url', 'created_at', 'updated_at', 'locked_at', 'locked_by',
                'view_proxy', 'camera_math'):
        plan_copy.pop(key, None)
    payload = {
        'schema_version': 3,
        'analysis_id': entry.get('analysis_id'),
        'annotation_revision': int(entry.get('verified_revision') or entry.get('revision') or 0),
        'source_size': [(entry.get('source') or {}).get('width'), (entry.get('source') or {}).get('height')],
        'room': {
            'id': room.get('id'), 'polygon': room.get('polygon') or [],
            'dimensions_text': room.get('dimensions_text') or '',
        },
        'camera': camera,
        'openings': accepted_openings(entry, room.get('id') or ''),
        'spatial_plan': plan_copy,
        'aspect_ratio': aspect_ratio,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _infer_space_kind(room: dict) -> str:
    text = f"{room.get('label', '')} {room.get('room_type', '')}".lower()
    if any(word in text for word in ('卫生', '浴', '洗衣', 'bath', 'toilet', 'laundry')):
        return 'wet_area'
    if any(word in text for word in ('阳台', 'balcony')):
        return 'balcony'
    if any(word in text for word in ('走廊', '过道', '玄关', 'corridor', 'foyer', 'hall')):
        return 'circulation'
    if any(word in text for word in ('客厅', '餐厅', '厨房', 'living', 'dining', 'kitchen')):
        return 'open_zone'
    return 'enclosed_room'


def save_ai_initial(entry: dict, raw_payload: Optional[dict] = None) -> str:
    snapshot = {
        'schema_version': 3, 'analysis_id': entry['analysis_id'],
        'created_at': time.time(), 'model': entry.get('ai_model', ''),
        'prompt_version': entry.get('prompt_version', 'floorplan-v1'),
        'rooms': copy.deepcopy(entry.get('rooms') or []),
        'openings': copy.deepcopy(entry.get('openings') or []),
        'entrance': copy.deepcopy(entry.get('entrance')),
        'orientation': entry.get('orientation', ''), 'warnings': copy.deepcopy(entry.get('warnings') or []),
        'raw_response': raw_payload or {},
    }
    path = os.path.join(annotation_dir(entry['analysis_id']), 'ai_initial.json')
    _atomic_json(path, snapshot)
    entry['ai_initial_path'] = path
    return path


def save_revision_snapshot(entry: dict, operation_types: Optional[list[str]] = None) -> str:
    """保存可重放的人工版本快照；不写 API Key 与运行期绝对输出。"""
    revision = int(entry.get('revision') or 0)
    snapshot = {
        'schema_version': 3, 'analysis_id': entry['analysis_id'], 'revision': revision,
        'created_at': time.time(), 'operation_types': operation_types or [],
        'annotation_status': entry.get('annotation_status', 'draft'),
        'training_consent': bool(entry.get('training_consent')),
        'rooms': copy.deepcopy(entry.get('rooms') or []),
        'openings': copy.deepcopy(entry.get('openings') or []),
        'openings_review_status': entry.get('openings_review_status', 'pending'),
        'entrance': copy.deepcopy(entry.get('entrance')),
        'orientation': entry.get('orientation', ''),
        'spatial_plans': copy.deepcopy(entry.get('spatial_plans') or {}),
        'view_proxies': copy.deepcopy(entry.get('view_proxies') or {}),
        'geometry_report': copy.deepcopy(entry.get('geometry_report') or {}),
    }
    folder = os.path.join(annotation_dir(entry['analysis_id']), 'revisions')
    path = os.path.join(folder, f'revision_{revision:06d}.json')
    _atomic_json(path, snapshot)
    return path


def append_operations(analysis_id: str, revision: int, operations: list[dict], actor: str) -> None:
    if not operations:
        return
    path = operations_path(analysis_id)
    now = time.time()
    with _log_lock, open(path, 'a', encoding='utf-8') as handle:
        for operation in operations:
            row = {
                'operation_id': f'op_{uuid.uuid4().hex}', 'analysis_id': analysis_id,
                'revision': revision, 'created_at': now, 'actor': actor or 'local-user',
                'type': str(operation.get('type') or 'snapshot_replace')[:80],
                'room_id': str(operation.get('room_id') or '')[:80],
                'camera_id': str(operation.get('camera_id') or '')[:80],
                'payload': operation.get('payload') if isinstance(operation.get('payload'), dict) else {},
            }
            handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def load_operations(analysis_id: str, limit: int = 500) -> list[dict]:
    path = operations_path(analysis_id)
    if not os.path.isfile(path):
        return []
    rows = []
    try:
        with _log_lock, open(path, 'r', encoding='utf-8') as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-max(1, min(int(limit), 5000)):]


def _segments_intersect(a: dict, b: dict, c: dict, d: dict) -> bool:
    def orient(p, q, r):
        return (q['x'] - p['x']) * (r['y'] - p['y']) - (q['y'] - p['y']) * (r['x'] - p['x'])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return ((o1 > 1e-9 and o2 < -1e-9) or (o1 < -1e-9 and o2 > 1e-9)) and \
           ((o3 > 1e-9 and o4 < -1e-9) or (o3 < -1e-9 and o4 > 1e-9))


def _self_intersects(points: list[dict]) -> bool:
    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j in (i, (i + 1) % count) or (j + 1) % count in (i, (i + 1) % count):
                continue
            if _segments_intersect(a, b, points[j], points[(j + 1) % count]):
                return True
    return False


def _point_inside(point: dict, polygon: list[dict]) -> bool:
    inside = False
    x, y = point['x'], point['y']
    j = len(polygon) - 1
    for i, current in enumerate(polygon):
        previous = polygon[j]
        if ((current['y'] > y) != (previous['y'] > y)) and \
                x < (previous['x'] - current['x']) * (y - current['y']) / \
                ((previous['y'] - current['y']) or 1e-12) + current['x']:
            inside = not inside
        j = i
    return inside


def validate_annotation(entry: dict) -> dict:
    hard_errors: list[dict] = []
    warnings: list[dict] = []
    selected = [room for room in entry.get('rooms') or [] if room.get('selected')]
    if not selected:
        hard_errors.append({'code': 'no_selected_rooms', 'message': '至少需要一个参与生成的房间'})
    pending_openings = [opening for opening in entry.get('openings') or []
                        if opening.get('review_status', 'accepted') == 'pending']
    if entry.get('openings_review_status') != 'confirmed' or pending_openings:
        hard_errors.append({
            'code': 'openings_unreviewed',
            'message': '请接受、拒绝或补画 AI 门窗候选，并确认门窗审核完成',
        })
    masks: list[tuple[dict, Image.Image, int]] = []
    for room in entry.get('rooms') or []:
        points = room.get('polygon') or []
        if len(points) < 3:
            hard_errors.append({'code': 'invalid_polygon', 'room_ids': [room.get('id')], 'message': f"{room.get('label')} 少于 3 个顶点"})
            continue
        if _self_intersects(points):
            hard_errors.append({'code': 'self_intersection', 'room_ids': [room.get('id')], 'message': f"{room.get('label')} 的边界发生自交"})
        mask = Image.new('1', (512, 512), 0)
        ImageDraw.Draw(mask).polygon([(round(p['x'] * 511), round(p['y'] * 511)) for p in points], fill=1)
        mask = mask.filter(ImageFilter.MinFilter(3))
        area = sum(mask.histogram()[1:])
        if area < 40:
            hard_errors.append({'code': 'tiny_room', 'room_ids': [room.get('id')], 'message': f"{room.get('label')} 的面积过小"})
        masks.append((room, mask, area))
        cameras = room.get('cameras') or []
        confirmed = [camera for camera in cameras if camera.get('confirmed')]
        if room.get('selected') and not confirmed:
            hard_errors.append({'code': 'missing_camera', 'room_ids': [room.get('id')], 'message': f"{room.get('label')} 尚无人工确认机位"})
        elif room.get('selected') and not any(camera.get('enabled_for_generation', True) for camera in confirmed):
            hard_errors.append({'code': 'no_generation_camera', 'room_ids': [room.get('id')], 'message': f"{room.get('label')} 没有启用参与生成的机位"})
        for camera in confirmed:
            if not _point_inside(camera.get('position') or {}, points):
                hard_errors.append({'code': 'camera_outside', 'room_ids': [room.get('id')], 'camera_id': camera.get('id'), 'message': f"{room.get('label')} 的机位不在房间内部"})
                continue
            position, target = camera['position'], camera['target']
            if math.hypot(target['x'] - position['x'], target['y'] - position['y']) < .02:
                hard_errors.append({'code': 'camera_direction_short', 'room_ids': [room.get('id')], 'camera_id': camera.get('id'), 'message': f"{room.get('label')} 的机位方向过短"})
            if not _point_inside(target, points):
                warnings.append({'code': 'camera_target_outside', 'room_ids': [room.get('id')], 'camera_id': camera.get('id'), 'message': f"{room.get('label')} 的观察目标位于房间外，请确认是否拍向相邻空间"})
    for index, (left, left_mask, left_area) in enumerate(masks):
        for right, right_mask, right_area in masks[index + 1:]:
            overlap = sum(ImageChops.logical_and(left_mask, right_mask).histogram()[1:])
            ratio = overlap / max(1, min(left_area, right_area))
            if ratio > .01:
                hard_errors.append({
                    'code': 'room_overlap', 'room_ids': [left.get('id'), right.get('id')],
                    'overlap_ratio': round(ratio, 4),
                    'message': f"{left.get('label')} 与 {right.get('label')} 重叠 {ratio:.1%}",
                })
    return {'hard_errors': hard_errors, 'warnings': warnings, 'checked_at': time.time()}


def render_annotation_overlay(entry: dict, suffix: str) -> str:
    source_path = (entry.get('source') or {}).get('path') or entry.get('floorplan_path')
    with Image.open(source_path) as original:
        image = original.convert('RGBA')
    draw = ImageDraw.Draw(image, 'RGBA')
    width, height = image.size
    colors = [(22, 163, 74), (37, 99, 235), (217, 119, 6), (190, 24, 93), (124, 58, 237)]
    for index, room in enumerate(entry.get('rooms') or []):
        color = colors[index % len(colors)]
        points = [(round(p['x'] * width), round(p['y'] * height)) for p in room.get('polygon') or []]
        if len(points) >= 3:
            draw.polygon(points, fill=(*color, 32), outline=(*color, 230), width=max(2, width // 500))
        for camera in room.get('cameras') or []:
            if not camera.get('confirmed'):
                continue
            p, t = camera['position'], camera['target']
            start, end = (round(p['x'] * width), round(p['y'] * height)), (round(t['x'] * width), round(t['y'] * height))
            draw.line([start, end], fill=(220, 38, 38, 255), width=max(3, width // 350))
            radius = max(5, width // 180)
            draw.ellipse([start[0]-radius, start[1]-radius, start[0]+radius, start[1]+radius], fill=(220, 38, 38, 255))
    path = os.path.join(annotation_dir(entry['analysis_id']), f'overlay_{suffix}.png')
    image.convert('RGB').save(path, 'PNG', optimize=True)
    return path


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def dataset_entries() -> list[dict]:
    entries = []
    try:
        names = [name for name in os.listdir(ANALYSIS_DIR) if name.startswith('analysis_') and name.endswith('.json')]
    except OSError:
        names = []
    for name in names:
        value = _read_json(os.path.join(ANALYSIS_DIR, name))
        if value:
            ensure_annotation_v2(value)
            if value.get('training_eligible') and value.get('training_consent') and value.get('annotation_status') == 'verified':
                entries.append(value)
    return sorted(entries, key=lambda item: item.get('verified_at') or 0)


def dataset_summary() -> dict:
    eligible = dataset_entries()
    cameras = sum(len([c for c in room.get('cameras') or [] if c.get('confirmed')]) for entry in eligible for room in entry.get('rooms') or [])
    return {'eligible_floorplans': len(eligible), 'eligible_rooms': sum(len(e.get('rooms') or []) for e in eligible), 'eligible_cameras': cameras}


def _suite_outcomes(analysis_id: str, revision: int) -> dict[tuple[str, str], list[dict]]:
    outcomes: dict[tuple[str, str], list[dict]] = {}
    try:
        names = [name for name in os.listdir(SUITE_DIR) if name.endswith('.json')]
    except OSError:
        names = []
    for name in names:
        suite = _read_json(os.path.join(SUITE_DIR, name))
        if not suite or suite.get('analysis_id') != analysis_id or int(suite.get('annotation_revision') or 0) != revision:
            continue
        for room in suite.get('rooms') or []:
            key = (room.get('annotation_room_id') or room.get('id', ''), room.get('camera_id') or '')
            for candidate in room.get('candidates') or []:
                if not candidate.get('path'):
                    continue
                outcomes.setdefault(key, []).append({
                    'suite_id': suite.get('suite_id'), 'result_id': candidate.get('result_id'),
                    'model_key': candidate.get('model_key'),
                    'view_proxy_id': candidate.get('view_proxy_id'),
                    'view_proxy_hash': candidate.get('view_proxy_hash'),
                    'generation_trace': candidate.get('generation_trace') or [],
                    '_structure_path': candidate.get('structure_path'),
                    '_material_path': candidate.get('material_path'),
                    '_final_path': candidate.get('final_path') or candidate.get('path'),
                    'review_status': candidate.get('review_status', 'unreviewed'),
                    'review_tags': candidate.get('review_tags') or [], 'review_note': candidate.get('review_note', ''),
                    'best': bool(candidate.get('best')), 'evaluation': candidate.get('evaluation'),
                })
    return outcomes


def export_dataset_zip() -> tuple[str, dict]:
    entries = dataset_entries()
    stamp = time.strftime('%Y%m%d_%H%M%S')
    destination = os.path.join(EXPORT_DIR, f'floorplan_dataset_{stamp}_{uuid.uuid4().hex[:6]}.zip')
    manifest_rows, camera_rows = [], []
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            analysis_id = entry['analysis_id']
            source = entry.get('source') or {}
            source_path = source.get('path') or entry.get('floorplan_path')
            extension = os.path.splitext(source_path)[1].lower() or '.png'
            image_rel = f'images/{source.get("sha256") or analysis_id}{extension}'
            archive.write(source_path, image_rel)
            final = {
                'schema_version': 3, 'analysis_id': analysis_id, 'revision': entry.get('revision'),
                'image': image_rel, 'image_sha256': source.get('sha256'),
                'image_width': source.get('width'), 'image_height': source.get('height'),
                'rooms': entry.get('rooms') or [], 'openings': entry.get('openings') or [],
                'openings_review_status': entry.get('openings_review_status', 'pending'),
                'entrance': entry.get('entrance'), 'orientation': entry.get('orientation', ''),
                'spatial_plans': entry.get('spatial_plans') or {},
                'view_proxies': entry.get('view_proxies') or {},
                'verified_at': entry.get('verified_at'), 'verified_by': entry.get('verified_by'),
                'geometry_report': entry.get('geometry_report'),
            }
            final_rel = f'annotations/{analysis_id}/verified.json'
            archive.writestr(final_rel, json.dumps(final, ensure_ascii=False, indent=2))
            ai_rel = ''
            if entry.get('ai_initial_path') and os.path.isfile(entry['ai_initial_path']):
                ai_rel = f'annotations/{analysis_id}/ai_initial.json'
                archive.write(entry['ai_initial_path'], ai_rel)
            log_path = operations_path(analysis_id)
            if os.path.isfile(log_path):
                archive.write(log_path, f'annotations/{analysis_id}/operations.jsonl')
            revisions_dir = os.path.join(annotation_dir(analysis_id), 'revisions')
            if os.path.isdir(revisions_dir):
                for name in sorted(os.listdir(revisions_dir)):
                    if name.endswith('.json'):
                        archive.write(os.path.join(revisions_dir, name), f'annotations/{analysis_id}/revisions/{name}')
            for kind in ('ai', 'verified'):
                overlay = os.path.join(annotation_dir(analysis_id), f'overlay_{kind}.png')
                if os.path.isfile(overlay):
                    archive.write(overlay, f'overlays/{analysis_id}_{kind}.png')
            for camera_id, proxy in (entry.get('view_proxies') or {}).items():
                proxy_path = proxy.get('path')
                if proxy_path and os.path.isfile(proxy_path):
                    archive.write(proxy_path, f'view_proxies/{analysis_id}_{camera_id}.png')
            outcomes = _suite_outcomes(analysis_id, int(entry.get('verified_revision') or entry.get('revision') or 0))
            for rows in outcomes.values():
                for outcome in rows:
                    written: dict[str, str] = {}
                    suite_token = ''.join(char for char in str(outcome.get('suite_id') or '') if char.isalnum() or char in '-_')[:100]
                    result_token = ''.join(char for char in str(outcome.get('result_id') or '') if char.isalnum() or char in '-_')[:100]
                    for phase in ('structure', 'material', 'final'):
                        source_path = outcome.pop(f'_{phase}_path', '') or ''
                        if not source_path or not os.path.isfile(source_path):
                            continue
                        canonical = os.path.realpath(source_path)
                        if canonical in written:
                            outcome[f'{phase}_image'] = written[canonical]
                            continue
                        extension = os.path.splitext(source_path)[1].lower() or '.png'
                        output_rel = f'outcomes/{analysis_id}/{suite_token}_{result_token}_{phase}{extension}'
                        archive.write(source_path, output_rel)
                        written[canonical] = output_rel
                        outcome[f'{phase}_image'] = output_rel
            manifest_rows.append({
                'sample_id': analysis_id, 'image': image_rel, 'annotation': final_rel,
                'ai_initial': ai_rel, 'revision': entry.get('revision'),
                'room_count': len(entry.get('rooms') or []),
            })
            for room in entry.get('rooms') or []:
                for camera in room.get('cameras') or []:
                    if not camera.get('confirmed'):
                        continue
                    camera_rows.append({
                        'sample_id': f"{analysis_id}:{room.get('id')}:{camera.get('id')}",
                        'floorplan_sample_id': analysis_id, 'image': image_rel,
                        'room_id': room.get('id'), 'room_type': room.get('room_type'),
                        'space_kind': room.get('space_kind'), 'polygon': room.get('polygon'),
                        'camera': camera,
                        'spatial_plan': (entry.get('spatial_plans') or {}).get(camera.get('id', '')),
                        'view_proxy': (entry.get('view_proxies') or {}).get(camera.get('id', '')),
                        'outcomes': outcomes.get((room.get('id', ''), camera.get('id', '')), []),
                    })
        archive.writestr('manifest.jsonl', ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in manifest_rows))
        archive.writestr('camera_samples.jsonl', ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in camera_rows))
        archive.writestr('dataset_info.json', json.dumps({
            'schema_version': 3, 'exported_at': time.time(),
            'floorplan_count': len(manifest_rows), 'camera_count': len(camera_rows),
        }, ensure_ascii=False, indent=2))
    summary = {'floorplans': len(manifest_rows), 'cameras': len(camera_rows), 'path': destination}
    logger.info(f'[户型数据集] 已导出 {summary}')
    return destination, summary
