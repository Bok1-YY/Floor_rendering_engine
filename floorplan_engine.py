# -*- coding: utf-8 -*-
"""户型图解析、标注、套图提示词与持久化纯服务层。

本模块不注册 FastAPI 路由，也不持有 asyncio 任务；routes_floorplans 负责调度。
分析和套图各自按 JSON 文件落盘，使单 worker 服务重启后仍能查看已完成结果并重试。
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import threading
import time
import uuid
from typing import Any, Optional

import requests
from PIL import Image, ImageDraw, ImageFont

from .config import (
    MAIN_OUTPUT_DIR, UPLOAD_DIR, logger, load_config, get_text_models,
    get_proxy, get_tls_verify, get_tls_ca_bundle,
)
from .server_helpers import to_url, result_thumb_url
from .floorplan_annotations import accepted_openings, ensure_annotation_v2, load_operations


FLOORPLAN_ROOT = os.path.join(MAIN_OUTPUT_DIR, '_floorplan_suites')
ANALYSIS_DIR = os.path.join(FLOORPLAN_ROOT, 'analyses')
SUITE_DIR = os.path.join(FLOORPLAN_ROOT, 'suites')
ASSET_DIR = os.path.join(FLOORPLAN_ROOT, 'assets')
for _directory in (FLOORPLAN_ROOT, ANALYSIS_DIR, SUITE_DIR, ASSET_DIR):
    os.makedirs(_directory, exist_ok=True)

_io_lock = threading.RLock()
_WET_ROOM_WORDS = ('卫生间', '浴室', '淋浴', '阳台', '露台', 'bath', 'toilet', 'shower', 'balcony')
_COMMON_ROOM_TYPES = {
    'living': '客厅', 'dining': '餐厅', 'kitchen': '厨房', 'bedroom': '卧室',
    'study': '书房', 'bathroom': '卫生间', 'balcony': '阳台', 'hallway': '走廊',
    'entry': '玄关', 'utility': '家政间', 'closet': '衣帽间', 'other': '其他',
}


def new_floorplan_id(prefix: str) -> str:
    return f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:10]}'


def _atomic_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{uuid.uuid4().hex}.tmp'
    with _io_lock:
        try:
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _read_json(path: str) -> Optional[dict]:
    try:
        with _io_lock, open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except Exception as ex:
        logger.warning(f'[户型套图] 状态读取失败 {path}: {ex}')
        return None


def analysis_path(analysis_id: str) -> str:
    return os.path.join(ANALYSIS_DIR, f'{os.path.basename(analysis_id)}.json')


def suite_path(suite_id: str) -> str:
    return os.path.join(SUITE_DIR, f'{os.path.basename(suite_id)}.json')


def save_analysis(entry: dict) -> None:
    ensure_annotation_v2(entry)
    _atomic_json(analysis_path(entry['analysis_id']), entry)


def load_analysis(analysis_id: str) -> Optional[dict]:
    entry = _read_json(analysis_path(analysis_id))
    return ensure_annotation_v2(entry) if entry else None


def list_analyses(limit: int = 50) -> list[dict]:
    rows = []
    try:
        files = [os.path.join(ANALYSIS_DIR, name) for name in os.listdir(ANALYSIS_DIR)
                 if name.startswith('analysis_') and name.endswith('.json')]
        files.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        for path in files[:max(1, min(int(limit), 200))]:
            value = _read_json(path)
            if value:
                rows.append(ensure_annotation_v2(value))
    except OSError:
        pass
    return rows


def save_suite(entry: dict) -> None:
    entry['updated_at'] = time.time()
    _atomic_json(suite_path(entry['suite_id']), entry)


def load_suite(suite_id: str) -> Optional[dict]:
    return _read_json(suite_path(suite_id))


def list_suites(limit: int = 30) -> list[dict]:
    rows = []
    try:
        files = [os.path.join(SUITE_DIR, name) for name in os.listdir(SUITE_DIR) if name.endswith('.json')]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for path in files[:max(1, min(int(limit), 100))]:
            value = _read_json(path)
            if value:
                rows.append(value)
    except OSError:
        pass
    return rows


def recover_interrupted_floorplan_state() -> tuple[int, int]:
    """把重启时的执行中状态改为可重试终态；waiting_anchor 可安全保留。"""
    analyses = suites = 0
    for folder, kind in ((ANALYSIS_DIR, 'analysis'), (SUITE_DIR, 'suite')):
        try:
            names = [name for name in os.listdir(folder) if name.endswith('.json')]
        except OSError:
            names = []
        for name in names:
            path = os.path.join(folder, name)
            entry = _read_json(path)
            if not entry:
                continue
            status = entry.get('status')
            if kind == 'analysis' and status in ('queued', 'analyzing'):
                entry.update(status='failed', stage='', error='服务重启中断了户型解析，请重新分析')
                _atomic_json(path, entry)
                analyses += 1
            elif kind == 'suite' and status in ('queued', 'running', 'evaluating'):
                has_result = any(
                    candidate.get('path')
                    for room in entry.get('rooms') or []
                    for candidate in room.get('candidates') or []
                )
                entry.update(
                    status='partial' if has_result else 'failed', stage='',
                    error='服务重启中断了整屋生成，可重试未完成房间', updated_at=time.time(),
                )
                _atomic_json(path, entry)
                suites += 1
    return analyses, suites


def analysis_view(entry: dict) -> dict:
    ensure_annotation_v2(entry)
    out = json.loads(json.dumps(entry, ensure_ascii=False))
    out['floorplan_url'] = to_url(out.get('floorplan_path'))
    for camera_id, plan in (out.get('spatial_plans') or {}).items():
        plan['overlay_url'] = to_url(plan.get('overlay_path'))
        for room in out.get('rooms') or []:
            camera = next((item for item in room.get('cameras') or [] if item.get('id') == camera_id), None)
            if camera:
                compiled = compile_spatial_plan(plan, {'camera': camera})
                plan['zones'] = compiled.get('zones') or []
                plan['furniture'] = compiled.get('furniture') or []
                plan['camera_math'] = compiled.get('camera_math') or {}
                break
    for proxy in (out.get('view_proxies') or {}).values():
        proxy['url'] = to_url(proxy.get('path'))
    out['operation_count'] = len(load_operations(out['analysis_id'], 5000))
    return out


def suite_view(entry: dict) -> dict:
    out = json.loads(json.dumps(entry, ensure_ascii=False))
    out['floorplan_url'] = to_url(out.get('floorplan_path'))
    out['floor_url'] = to_url(out.get('floor_path'))
    out['style_ref_url'] = to_url(out.get('style_ref_path'))
    out['anchor_url'] = to_url(out.get('anchor_path'))
    for room in out.get('rooms') or []:
        room['view_proxy_url'] = to_url(room.get('view_proxy_path'))
        for candidate in room.get('candidates') or []:
            candidate['url'] = to_url(candidate.get('path'))
            candidate['thumb'] = result_thumb_url(candidate.get('path'))
            candidate['structure_url'] = to_url(candidate.get('structure_path'))
            candidate['material_url'] = to_url(candidate.get('material_path'))
            candidate['final_url'] = to_url(candidate.get('final_path') or candidate.get('path'))
    return out


def _verify_arg() -> Any:
    if not get_tls_verify():
        return False
    bundle = (get_tls_ca_bundle() or '').strip()
    return bundle or True


def _read_image_part(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    mime = {'.png': 'image/png', '.webp': 'image/webp'}.get(ext, 'image/jpeg')
    with open(path, 'rb') as handle:
        data = base64.b64encode(handle.read()).decode('ascii')
    return {'inlineData': {'mimeType': mime, 'data': data}}


def call_gemini_json(api_key: str, prompt: str, image_paths: list[str], schema: dict,
                     *, max_output_tokens: int = 6000) -> tuple[Optional[dict], Optional[str]]:
    if not api_key:
        return None, '未配置 Gemini API Key'
    parts = [{'text': prompt}]
    try:
        for path in image_paths:
            if not path or not os.path.isfile(path):
                return None, f'输入图片不存在: {path}'
            parts.append(_read_image_part(path))
    except Exception as ex:
        return None, f'读取输入图片失败: {ex}'
    payload = {
        'contents': [{'parts': parts}],
        'generationConfig': {
            'maxOutputTokens': max_output_tokens,
            'responseMimeType': 'application/json',
            'responseSchema': schema,
        },
    }
    proxy = get_proxy()
    proxies = {'http': proxy, 'https': proxy} if proxy else None
    last_error = ''
    for model in get_text_models():
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'
        try:
            response = requests.post(url, json=payload, timeout=(15, 120), proxies=proxies, verify=_verify_arg())
            if response.status_code == 200:
                response_payload = response.json()
                text = response_payload['candidates'][0]['content']['parts'][0]['text']
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    parsed['_floor_engine_model'] = model
                    usage = response_payload.get('usageMetadata')
                    if isinstance(usage, dict):
                        parsed['_floor_engine_usage_metadata'] = usage
                return parsed if isinstance(parsed, dict) else None, None
            body = re.sub(r'key=[^&\s]+', 'key=***', response.text[:300])
            last_error = f'HTTP {response.status_code} on {model}: {body}'
            if response.status_code not in (404, 429, 500, 502, 503, 504):
                break
        except Exception as ex:
            last_error = re.sub(r'key=[^&\s]+', 'key=***', str(ex))
    return None, last_error or '所有 Gemini 视觉模型均不可用'


_POINT_SCHEMA = {
    'type': 'OBJECT',
    'properties': {'x': {'type': 'NUMBER'}, 'y': {'type': 'NUMBER'}},
    'required': ['x', 'y'],
}
_FLOORPLAN_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'summary': {'type': 'STRING'},
        'orientation': {'type': 'STRING'},
        'entrance': _POINT_SCHEMA,
        'warnings': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'rooms': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'id': {'type': 'STRING'}, 'label': {'type': 'STRING'},
                    'room_type': {'type': 'STRING'},
                    'polygon': {'type': 'ARRAY', 'items': _POINT_SCHEMA},
                    'adjacent_room_ids': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                    'dimensions_text': {'type': 'STRING'}, 'confidence': {'type': 'NUMBER'},
                },
                'required': ['id', 'label', 'room_type', 'polygon', 'adjacent_room_ids',
                             'dimensions_text', 'confidence'],
            },
        },
        'openings': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'id': {'type': 'STRING'}, 'kind': {'type': 'STRING', 'enum': ['door', 'window', 'open_connection']},
                    'points': {'type': 'ARRAY', 'items': _POINT_SCHEMA},
                    'room_ids': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                    'confidence': {'type': 'NUMBER'},
                },
                'required': ['id', 'kind', 'points', 'room_ids', 'confidence'],
            },
        },
    },
    'required': ['summary', 'orientation', 'warnings', 'rooms', 'openings'],
}

_SPATIAL_PLAN_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'space_summary': {'type': 'STRING'},
        'camera_view': {
            'type': 'OBJECT',
            'properties': {
                'direction': {'type': 'STRING'},
                'expected_composition': {'type': 'STRING'},
                'foreground_left': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'foreground_center': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'foreground_right': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'midground_left': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'midground_center': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'midground_right': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'background_left': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'background_center': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'background_right': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'hidden_behind_camera': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
            },
            'required': [
                'direction', 'expected_composition', 'foreground_left', 'foreground_center',
                'foreground_right', 'midground_left', 'midground_center', 'midground_right',
                'background_left', 'background_center', 'background_right', 'hidden_behind_camera',
            ],
        },
        'architecture': {
            'type': 'OBJECT',
            'properties': {
                'visible_walls': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'required_opening_ids': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'open_connections': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'fixed_boundaries': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'forbidden_openings': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
            },
            'required': ['visible_walls', 'required_opening_ids', 'open_connections',
                         'fixed_boundaries', 'forbidden_openings'],
        },
        'zones': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'name': {'type': 'STRING'}, 'function': {'type': 'STRING'},
                    'plan_position': _POINT_SCHEMA, 'frame_position': {'type': 'STRING'},
                    'depth': {'type': 'STRING'}, 'required_visible': {'type': 'BOOLEAN'},
                },
                'required': ['name', 'function', 'plan_position', 'frame_position', 'depth', 'required_visible'],
            },
        },
        'furniture': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'item': {'type': 'STRING'}, 'plan_position': _POINT_SCHEMA,
                    'frame_position': {'type': 'STRING'}, 'depth': {'type': 'STRING'},
                    'orientation': {'type': 'STRING'}, 'required_visible': {'type': 'BOOLEAN'},
                    'confidence': {'type': 'NUMBER'},
                },
                'required': ['item', 'plan_position', 'frame_position', 'depth', 'orientation',
                             'required_visible', 'confidence'],
            },
        },
        'hard_constraints': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'must_not_appear': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'uncertainties': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
    },
    'required': ['space_summary', 'camera_view', 'architecture', 'zones', 'furniture',
                 'hard_constraints', 'must_not_appear', 'uncertainties'],
}


def _point(value: Any) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    try:
        x, y = float(value.get('x')), float(value.get('y'))
    except (TypeError, ValueError):
        return None
    # 兼容模型偶尔返回 0..1000 坐标。
    if x > 1.0 or y > 1.0:
        x, y = x / 1000.0, y / 1000.0
    return {'x': round(max(0.0, min(1.0, x)), 6), 'y': round(max(0.0, min(1.0, y)), 6)}


def normalize_floorplan_payload(payload: dict) -> dict:
    rooms = []
    seen = set()
    for index, source in enumerate(payload.get('rooms') or []):
        if not isinstance(source, dict):
            continue
        raw_id = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(source.get('id') or f'room_{index + 1}')).strip('_')
        room_id = raw_id or f'room_{index + 1}'
        while room_id in seen:
            room_id = f'{raw_id}_{index + 1}'
        polygon = [point for point in (_point(v) for v in source.get('polygon') or []) if point]
        if len(polygon) < 3:
            continue
        seen.add(room_id)
        label = str(source.get('label') or f'房间 {index + 1}').strip()[:100]
        room_type_raw = str(source.get('room_type') or 'other').strip().lower()
        room_type = _COMMON_ROOM_TYPES.get(room_type_raw, str(source.get('room_type') or '其他')[:100])
        wet = any(word.lower() in f'{label} {room_type}'.lower() for word in _WET_ROOM_WORDS)
        rooms.append({
            'id': room_id, 'label': label, 'room_type': room_type, 'polygon': polygon,
            'adjacent_room_ids': [str(v)[:80] for v in source.get('adjacent_room_ids') or []],
            'dimensions_text': str(source.get('dimensions_text') or '')[:200],
            'confidence': round(max(0.0, min(1.0, float(source.get('confidence') or 0.0))), 4),
            'space_kind': ('wet_area' if wet else ('balcony' if '阳台' in label or 'balcony' in label.lower()
                           else ('circulation' if any(v in label.lower() for v in ('玄关', '走廊', '过道', 'foyer', 'corridor'))
                                 else ('open_zone' if any(v in f'{label} {room_type}'.lower() for v in ('厨房', '餐厅', '客厅', 'kitchen', 'dining', 'living'))
                                       else 'enclosed_room')))),
            'source': 'ai', 'selected': not wet, 'apply_floor': not wet,
            'cameras': [], 'primary_camera_id': '', 'camera': None,
        })
    openings = []
    for index, source in enumerate(payload.get('openings') or []):
        if not isinstance(source, dict) or source.get('kind') not in ('door', 'window', 'open_connection'):
            continue
        points = [point for point in (_point(v) for v in source.get('points') or []) if point]
        if len(points) != 2:
            continue
        openings.append({
            'id': str(source.get('id') or f'opening_{index + 1}')[:80],
            'kind': source['kind'], 'points': points,
            'room_ids': [str(v)[:80] for v in source.get('room_ids') or []][:2],
            'confidence': round(max(0.0, min(1.0, float(source.get('confidence') or 0.0))), 4),
            'source': 'ai_suggested', 'review_status': 'pending',
        })
    entrance = _point(payload.get('entrance'))
    return {
        'summary': str(payload.get('summary') or '')[:1000],
        'orientation': str(payload.get('orientation') or '')[:100],
        'entrance': entrance,
        'warnings': [str(v)[:300] for v in payload.get('warnings') or []][:20],
        'rooms': rooms, 'openings': openings,
    }


def analyze_floorplan_image(api_key: str, image_path: str) -> tuple[Optional[dict], Optional[str]]:
    prompt = """You are a conservative architectural floor-plan parser. Analyze the attached clear residential floor plan.
Return only the requested JSON. Coordinates must be normalized to 0..1 relative to image width and height.
For every visible room, trace a simple polygon inside its wall boundary, label its actual printed room name, classify its room type, list adjacent room ids, and copy visible dimensions verbatim. Detect doors and windows as two-point line segments. Never invent dimensions, openings, rooms, orientation, or adjacency that are not visible. Use an empty string/list and add a warning when uncertain. Confidence is 0..1. Use stable ASCII ids such as living_1, bedroom_1. The entrance field may be omitted when unknown."""
    payload, error = call_gemini_json(api_key, prompt, [image_path], _FLOORPLAN_SCHEMA)
    if error or payload is None:
        return None, error or '户型解析返回为空'
    model = str(payload.pop('_floor_engine_model', '') or '')
    raw_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    normalized = normalize_floorplan_payload(payload)
    if not normalized['rooms']:
        return None, '未识别到可用房间，请上传更清晰的规整户型图'
    normalized['_ai_model'] = model
    normalized['_ai_raw'] = raw_payload
    return normalized, None


def _text_list(value: Any, limit: int = 40, width: int = 300) -> list[str]:
    return [str(item).strip()[:width] for item in (value or []) if str(item).strip()][:limit]


def _normalize_plan_items(rows: Any, *, furniture: bool = False) -> list[dict]:
    normalized = []
    for source in rows or []:
        if not isinstance(source, dict):
            continue
        point = _point(source.get('plan_position'))
        name_key = 'item' if furniture else 'name'
        name = str(source.get(name_key) or '').strip()[:120]
        if not point or not name:
            continue
        row = {
            name_key: name, 'plan_position': point,
            'frame_position': str(source.get('frame_position') or 'unknown')[:80],
            'depth': str(source.get('depth') or 'unknown')[:40],
            'required_visible': bool(source.get('required_visible')),
        }
        if furniture:
            row['orientation'] = str(source.get('orientation') or 'unknown')[:120]
            try:
                row['confidence'] = round(max(0.0, min(1.0, float(source.get('confidence') or 0))), 4)
            except (TypeError, ValueError):
                row['confidence'] = 0.0
        else:
            row['function'] = str(source.get('function') or '')[:160]
        normalized.append(row)
    return normalized[:80 if furniture else 30]


def normalize_spatial_plan(payload: dict) -> dict:
    view = payload.get('camera_view') if isinstance(payload.get('camera_view'), dict) else {}
    architecture = payload.get('architecture') if isinstance(payload.get('architecture'), dict) else {}
    frame_keys = (
        'foreground_left', 'foreground_center', 'foreground_right',
        'midground_left', 'midground_center', 'midground_right',
        'background_left', 'background_center', 'background_right', 'hidden_behind_camera',
    )
    return {
        'space_summary': str(payload.get('space_summary') or '')[:2000],
        'camera_view': {
            'direction': str(view.get('direction') or '')[:300],
            'expected_composition': str(view.get('expected_composition') or '')[:1200],
            **{key: _text_list(view.get(key), 20, 160) for key in frame_keys},
        },
        'architecture': {
            key: _text_list(architecture.get(key), 30, 220)
            for key in ('visible_walls', 'required_opening_ids', 'required_openings', 'open_connections',
                        'fixed_boundaries', 'forbidden_openings')
        },
        'zones': _normalize_plan_items(payload.get('zones')),
        'furniture': _normalize_plan_items(payload.get('furniture'), furniture=True),
        'hard_constraints': _text_list(payload.get('hard_constraints'), 40),
        'must_not_appear': _text_list(payload.get('must_not_appear'), 40),
        'uncertainties': _text_list(payload.get('uncertainties'), 40),
    }


def analyze_spatial_plan(api_key: str, floorplan_path: str, annotated_path: str, room: dict,
                         openings: list[dict], orientation: str = '') -> tuple[Optional[dict], Optional[str]]:
    camera = room.get('camera') or {}
    context = {
        'room_id': room.get('annotation_room_id') or room.get('id'),
        'room_label': room.get('label'), 'room_type': room.get('room_type'),
        'space_kind': room.get('space_kind'), 'polygon': room.get('polygon'),
        'dimensions_text': room.get('dimensions_text'), 'camera': camera,
        'confirmed_openings': openings, 'orientation': orientation,
    }
    prompt = f"""You are the architectural planning stage before a paid interior image render.
Image 1 is the original top-down floor plan. Image 2 is the human-confirmed room polygon, openings and red camera arrow.
Convert only visible floor-plan evidence plus the supplied human annotations into the requested structured shot plan.

ANNOTATION JSON:
{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}

Rules:
- Architecture is factual, not a design suggestion. Never invent a wall, door, window, column, room or dimension.
- A large polygon may contain several open functional zones. Keep each zone separate and preserve their plan order.
- plan_position coordinates are normalized 0..1 positions on the original plan.
- Derive what is foreground/midground/background and left/center/right from the red camera arrow.
- Furniture printed in the plan is fixed evidence. Record its location, orientation and confidence.
- required_opening_ids may contain only exact ids from confirmed_openings. Never create a new opening name or id.
- A visible but unconfirmed opening belongs in uncertainties, never in architecture or hard_constraints.
- hard_constraints must be concrete, view-specific and testable after rendering.
- must_not_appear includes rooms behind the camera and any unconfirmed extra openings/architecture.
- Do not discuss style, materials or decoration. Return JSON only."""
    payload, error = call_gemini_json(
        api_key, prompt, [floorplan_path, annotated_path], _SPATIAL_PLAN_SCHEMA,
        max_output_tokens=6000,
    )
    if error or not payload:
        return None, error or '空间规划返回为空'
    model = str(payload.pop('_floor_engine_model', '') or '')
    normalized = normalize_spatial_plan(payload)
    accepted_ids = {str(opening.get('id') or '') for opening in openings}
    requested_ids = normalized['architecture'].get('required_opening_ids') or []
    invalid_ids = [opening_id for opening_id in requested_ids if opening_id not in accepted_ids]
    normalized['architecture']['required_opening_ids'] = [
        opening_id for opening_id in requested_ids if opening_id in accepted_ids
    ]
    if invalid_ids:
        normalized['uncertainties'].append(
            f"AI observed unconfirmed openings that were excluded from hard constraints: {', '.join(invalid_ids)}"
        )
    normalized['planner_model'] = model
    return normalized, None


def _camera_relation(camera: dict, point: dict) -> tuple[str, str, float, float]:
    position, target = camera.get('position') or {}, camera.get('target') or {}
    try:
        fx, fy = float(target['x']) - float(position['x']), float(target['y']) - float(position['y'])
        px, py = float(point['x']) - float(position['x']), float(point['y']) - float(position['y'])
    except (KeyError, TypeError, ValueError):
        return 'unknown', 'unknown', 0.0, 0.0
    length = math.hypot(fx, fy) or 1.0
    fx, fy = fx / length, fy / length
    depth = px * fx + py * fy
    lateral = px * (-fy) + py * fx
    if depth <= 0:
        return 'behind_camera', 'hidden', depth, lateral
    ratio = lateral / max(depth, 1e-6)
    horizontal = 'left' if ratio < -0.22 else ('right' if ratio > 0.22 else 'center')
    distance = 'foreground' if depth < 0.16 else ('midground' if depth < 0.38 else 'background')
    return horizontal, distance, depth, lateral


def compile_spatial_plan(plan: dict, room: dict) -> dict:
    """Compile Gemini observations plus deterministic camera math into renderer constraints."""
    compiled = json.loads(json.dumps(plan, ensure_ascii=False))
    camera = room.get('camera') or {}
    for key in ('zones', 'furniture'):
        for item in compiled.get(key) or []:
            horizontal, distance, depth, lateral = _camera_relation(camera, item.get('plan_position') or {})
            item['computed_frame_position'] = f'{distance}_{horizontal}' if distance != 'hidden' else 'behind_camera'
            item['camera_depth'] = round(depth, 4)
            item['camera_lateral'] = round(lateral, 4)
    compiled['camera_math'] = {
        'camera': camera_text(camera),
        'focal_length_mm': camera.get('focal_length_mm'),
        'height_m': camera.get('height_m'),
        'rule': 'computed_frame_position is deterministic from plan coordinates and overrides conflicting AI prose',
    }
    return compiled


def _font(size: int) -> ImageFont.ImageFont:
    for path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def render_room_annotation(floorplan_path: str, room: dict, openings: list[dict], suite_id: str) -> str:
    with Image.open(floorplan_path) as source:
        base = source.convert('RGBA')
    width, height = base.size
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    polygon = [(round(p['x'] * width), round(p['y'] * height)) for p in room['polygon']]
    draw.polygon(polygon, fill=(193, 95, 60, 70), outline=(193, 95, 60, 255), width=max(3, width // 400))
    source_room_id = room.get('annotation_room_id') or room['id']
    for opening in openings:
        if source_room_id not in (opening.get('room_ids') or []):
            continue
        points = [(round(p['x'] * width), round(p['y'] * height)) for p in opening['points']]
        color = (28, 130, 80, 255) if opening['kind'] == 'door' else (31, 111, 235, 255)
        draw.line(points, fill=color, width=max(5, width // 250))
    camera = room.get('camera') or {}
    if camera.get('position') and camera.get('target'):
        start = (round(camera['position']['x'] * width), round(camera['position']['y'] * height))
        end = (round(camera['target']['x'] * width), round(camera['target']['y'] * height))
        draw.line([start, end], fill=(220, 38, 38, 255), width=max(5, width // 250))
        radius = max(8, width // 120)
        draw.ellipse([start[0] - radius, start[1] - radius, start[0] + radius, start[1] + radius],
                     fill=(220, 38, 38, 255))
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        wing = max(16, width // 55)
        for delta in (2.55, -2.55):
            point = (end[0] + wing * math.cos(angle + delta), end[1] + wing * math.sin(angle + delta))
            draw.line([end, point], fill=(220, 38, 38, 255), width=max(4, width // 300))
    label = f"CURRENT ROOM: {room.get('label', '')}"
    font = _font(max(18, width // 45))
    draw.rounded_rectangle((12, 12, min(width - 12, 28 + len(label) * max(12, width // 75)), 62 + width // 75),
                           radius=10, fill=(15, 23, 42, 220))
    draw.text((24, 24), label, font=font, fill='white')
    composed = Image.alpha_composite(base, overlay).convert('RGB')
    out_dir = os.path.join(ASSET_DIR, os.path.basename(suite_id))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{re.sub(r'[^a-zA-Z0-9_-]', '_', room['id'])}_annotated.jpg")
    composed.save(out, 'JPEG', quality=92)
    return out


def render_spatial_plan_overlay(annotated_path: str, spatial_plan: dict, suite_id: str,
                                room_id: str) -> str:
    """Render the locked planner facts as a second visual constraint for the image model."""
    with Image.open(annotated_path) as source:
        image = source.convert('RGBA')
    width, height = image.size
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font(max(14, width // 70))
    small = _font(max(12, width // 85))
    colors = {'zones': (8, 145, 178, 235), 'furniture': (126, 34, 206, 235)}
    legend = []
    for kind, prefix, name_key in (('zones', 'Z', 'name'), ('furniture', 'F', 'item')):
        for index, item in enumerate(spatial_plan.get(kind) or [], start=1):
            point = item.get('plan_position') or {}
            try:
                x, y = round(float(point['x']) * width), round(float(point['y']) * height)
            except (KeyError, TypeError, ValueError):
                continue
            color = colors[kind]
            radius = max(10, width // 100)
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color, outline='white', width=2)
            marker = f'{prefix}{index}'
            draw.text((x + radius + 3, y - radius), marker, font=font, fill=color,
                      stroke_width=2, stroke_fill='white')
            legend.append(f'{marker} {str(item.get(name_key) or "")[:32]}')
    if legend:
        shown = legend[:12]
        line_height = max(18, width // 48)
        panel_width = min(width - 24, max(230, width // 3))
        panel_height = 16 + line_height * len(shown)
        left, top = 12, max(80, height - panel_height - 12)
        draw.rounded_rectangle((left, top, left + panel_width, top + panel_height), radius=8,
                               fill=(15, 23, 42, 220))
        for index, line in enumerate(shown):
            draw.text((left + 10, top + 7 + index * line_height), line, font=small, fill='white')
    composed = Image.alpha_composite(image, overlay).convert('RGB')
    out_dir = os.path.join(ASSET_DIR, os.path.basename(suite_id))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{re.sub(r'[^a-zA-Z0-9_-]', '_', room_id)}_constraints.jpg")
    composed.save(out, 'JPEG', quality=94)
    return out


def camera_text(camera: Optional[dict]) -> str:
    if not camera:
        return 'camera not specified'
    p, t = camera['position'], camera['target']
    dx, dy = t['x'] - p['x'], t['y'] - p['y']
    return f"camera normalized position ({p['x']:.3f},{p['y']:.3f}), looking vector ({dx:+.3f},{dy:+.3f})"


def build_room_prompt(suite: dict, room: dict, annotated_path: str = '') -> tuple[str, list[str]]:
    """第一轮：只把人工确认灰模写实化，隔离标注图、图例和产品小样。"""
    source_room_id = room.get('annotation_room_id') or room['id']
    source_rooms = suite.get('annotation_rooms') or suite.get('rooms') or []
    selected = [row for row in source_rooms if row.get('selected')]
    adjacent = [row.get('label') for row in selected
                if row.get('id') in (room.get('adjacent_room_ids') or [])]
    related_openings = [opening for opening in suite.get('openings') or []
                        if opening.get('review_status', 'accepted') == 'accepted'
                        and source_room_id in (opening.get('room_ids') or [])]
    opening_text = ', '.join(
        f"{opening.get('id')}({opening.get('kind')})" for opening in related_openings
    ) or 'none confirmed'
    compiled = compile_spatial_plan(room.get('spatial_plan') or {}, room)
    proxy_path = room.get('view_proxy_path') or (room.get('view_proxy') or {}).get('path') or ''
    if not proxy_path:
        raise ValueError('该机位尚未确认结构灰模')

    image_roles = [
        'Image 1 is the approved clay-render camera proxy. Its camera, walls, openings and object silhouettes are immutable.'
    ]
    paths = [proxy_path]
    next_index = 2
    if suite.get('style_ref_path'):
        image_roles.append(
            f'Image {next_index} is an optional style reference. Copy finish language only, never its geometry.'
        )
        paths.append(suite['style_ref_path'])
        next_index += 1
    if suite.get('anchor_path'):
        image_roles.append(
            f'Image {next_index} is an approved home reference. Copy design language only, never its geometry.'
        )
        paths.append(suite['anchor_path'])

    required_items = []
    for item in (compiled.get('furniture') or []) + (compiled.get('zones') or []):
        if item.get('required_visible'):
            name = item.get('item') or item.get('name')
            required_items.append(
                f"{name}@{item.get('computed_frame_position') or item.get('frame_position')}"
            )
    hard_text = '; '.join(_text_list(compiled.get('hard_constraints'), 12, 240)) \
        or 'preserve the approved proxy'
    forbidden_text = '; '.join(_text_list(compiled.get('must_not_appear'), 12, 160)) \
        or 'no unapproved rooms or openings'
    prompt = f"""Create one photorealistic residential interior photograph.

IMAGE ROLES:
{' '.join(image_roles)}

EDIT IMAGE 1 IN PLACE. STRUCTURE IS IMMUTABLE:
- Render only {room.get('label')} ({room.get('room_type')}).
- Preserve the exact perspective, crop, wall edges, floor boundary, openings and major object silhouettes from image 1.
- Camera: {camera_text(room.get('camera'))}.
- Adjacent visible spaces: {', '.join(adjacent) or 'none specified'}.
- Confirmed openings: {opening_text}.
- Required visible order: {'; '.join(required_items) or 'as modeled in image 1'}.
- Hard facts: {hard_text}.
- Forbidden: {forbidden_text}.
- Never add, remove or move a wall, door, window, column, platform or room.
- Use a neutral temporary floor material; the exact product floor will be applied in a separate edit.

DESIGN:
- Style: {suite.get('style') or 'modern natural'}.
- Lighting: {suite.get('lighting') or 'natural daylight'}.
- Shared brief: {suite.get('style_brief') or ''}
- Customer request: {suite.get('prompt') or 'Create a believable, restrained, lived-in residential interior.'}

QUALITY:
Real camera perspective, coherent scale, explainable light sources, contact shadows and subtle lived-in detail.
No labels, arrows, plan graphics, legends, swatches, insets, collages or split screens. Return one finished interior photograph."""
    return prompt, paths


def build_floor_material_prompt(suite: dict, room: dict, structure_path: str) -> tuple[str, list[str]]:
    """第二轮：仅应用地板产品，避免小样干扰结构写实化。"""
    if not room.get('apply_floor'):
        return 'Keep this approved interior photograph exactly unchanged.', [structure_path]
    prompt = """Edit image 1 in place. Replace only the visible floor finish with the floor product shown in image 2.
Preserve every camera pixel relationship, wall, opening, platform, ceiling, furniture item, lighting direction and object position from image 1.
Use image 2 only as a surface-material sample: match its hue, grain species, plank character, scale and matte/gloss finish.
Do not paste the sample as an inset, border, swatch, collage or separate panel. Do not add text, labels, arrows, plan graphics or watermarks.
Keep realistic perspective foreshortening, plank joins, contact shadows and occlusion. Return one finished photorealistic interior photograph."""
    return prompt, [structure_path, suite['floor_path']]


_EVAL_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'layout_fidelity': {'type': 'INTEGER'}, 'material_fidelity': {'type': 'INTEGER'},
        'camera_match': {'type': 'INTEGER'}, 'visual_quality': {'type': 'INTEGER'},
        'suite_consistency': {'type': 'INTEGER'},
        'hard_fail': {'type': 'BOOLEAN'},
        'checks': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'constraint_id': {'type': 'STRING'},
                    'constraint': {'type': 'STRING'},
                    'status': {'type': 'STRING', 'enum': ['pass', 'fail', 'uncertain']},
                    'severity': {'type': 'STRING', 'enum': ['hard', 'soft']},
                    'evidence': {'type': 'STRING'},
                },
                'required': ['constraint_id', 'constraint', 'status', 'severity', 'evidence'],
            },
        },
        'warnings': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'summary': {'type': 'STRING'},
    },
    'required': ['layout_fidelity', 'material_fidelity', 'camera_match', 'visual_quality',
                 'suite_consistency', 'hard_fail', 'checks', 'warnings', 'summary'],
}


def _qa_constraints(spatial_plan: dict) -> list[dict]:
    """Build a complete, stable checklist so evaluator omissions cannot silently pass."""
    rows: list[dict] = []
    seen: set[str] = set()

    def add(category: str, text: Any) -> None:
        value = str(text or '').strip()
        identity = re.sub(r'\s+', ' ', value).casefold()
        if not value or identity in seen:
            return
        seen.add(identity)
        rows.append({
            'constraint_id': f'C{len(rows) + 1:03d}', 'category': category,
            'constraint': value[:400], 'severity': 'hard',
        })

    for value in spatial_plan.get('hard_constraints') or []:
        add('hard_constraint', value)
    for value in spatial_plan.get('must_not_appear') or []:
        add('forbidden_content', f'Must not appear: {value}')
    architecture = spatial_plan.get('architecture') or {}
    for key, prefix in (
        ('visible_walls', 'Visible wall must match'),
        ('required_opening_ids', 'Confirmed opening id must remain'),
        ('required_openings', 'Required opening must remain'),
        ('open_connections', 'Open connection must remain'),
        ('fixed_boundaries', 'Fixed boundary must match'),
        ('forbidden_openings', 'Forbidden opening must not appear'),
    ):
        for value in architecture.get(key) or []:
            add(key, f'{prefix}: {value}')
    for value in (spatial_plan.get('camera_view') or {}).get('hidden_behind_camera') or []:
        add('camera_visibility', f'Must remain behind camera and out of frame: {value}')
    for item in spatial_plan.get('zones') or []:
        if item.get('required_visible'):
            position = item.get('computed_frame_position') or item.get('frame_position') or 'specified position'
            add('required_zone', f"Required zone {item.get('name') or 'unnamed'} must be visible at {position}")
    for item in spatial_plan.get('furniture') or []:
        if item.get('required_visible'):
            position = item.get('computed_frame_position') or item.get('frame_position') or 'specified position'
            orientation = item.get('orientation') or 'specified orientation'
            add('required_furniture', f"Required furniture {item.get('item') or 'unnamed'} must be visible at {position}, {orientation}")
    add('artifact', 'Must not contain floor-plan graphics, camera arrows, labels, numbered markers or legends')
    add('artifact', 'Must not contain a pasted floor-sample inset, swatch, border, collage or split screen')
    return rows[:160]


def evaluate_candidate(api_key: str, annotated_path: str, floor_path: str, candidate_path: str,
                       anchor_path: Optional[str] = None, constraint_overlay_path: Optional[str] = None,
                       spatial_plan: Optional[dict] = None,
                       structure_path: Optional[str] = None,
                       phase: str = 'final') -> tuple[dict, Optional[str]]:
    roles = 'Image 1 is the approved clay-render camera proxy and authoritative geometry.'
    paths = [annotated_path]
    next_index = 2
    if structure_path and os.path.realpath(structure_path) != os.path.realpath(candidate_path):
        roles += f' Image {next_index} is the approved first-pass structure render.'
        paths.append(structure_path)
        next_index += 1
    if phase == 'final':
        roles += f' Image {next_index} is the required floor sample.'
        paths.append(floor_path)
        next_index += 1
    roles += f' Image {next_index} is the generated candidate being evaluated.'
    paths.append(candidate_path)
    next_index += 1
    if anchor_path:
        roles += f' Image {next_index} is the approved anchor room used for whole-home consistency.'
        paths.append(anchor_path)
    constraints = spatial_plan or {}
    expected_checks = _qa_constraints(constraints)
    prompt = f"""You are an adversarial architectural QA evaluator. {roles}
LOCKED CONSTRAINT JSON:
{json.dumps(constraints, ensure_ascii=False, separators=(',', ':'))}
MANDATORY CHECKLIST:
{json.dumps(expected_checks, ensure_ascii=False, separators=(',', ':'))}

Return exactly one checks entry for every MANDATORY CHECKLIST row and copy its constraint_id unchanged. Do not omit, merge or invent checklist rows.
Mark hard_fail=true when the candidate adds/removes/moves a major wall, door, window, column or room; reverses required zone order; shows something behind the camera; contains plan/arrow/legend/sample-inset artifacts; or violates any hard constraint. A beautiful but structurally wrong image must fail.
Use uncertain rather than pass when evidence is occluded. Never award 100 merely because the image is plausible.
Then score 0..100. Layout fidelity checks topology and relative spatial order. Material fidelity checks hue, grain and finish. Camera match checks viewpoint and visible ordering. Visual quality checks realism. Suite consistency is 100 only when no anchor is supplied. Return concrete evidence in every failed or uncertain check. Return JSON."""
    payload, error = call_gemini_json(api_key, prompt, paths, _EVAL_SCHEMA, max_output_tokens=6000)
    if error or not payload:
        return {'status': 'unavailable', 'total': None, 'warnings': [error or '复核返回为空'], 'summary': ''}, error
    scores = {}
    for key in ('layout_fidelity', 'material_fidelity', 'camera_match', 'visual_quality', 'suite_consistency'):
        try:
            scores[key] = max(0, min(100, int(payload.get(key, 0))))
        except (TypeError, ValueError):
            scores[key] = 0
    checks = []
    expected_by_id = {item['constraint_id']: item for item in expected_checks}
    expected_by_text = {item['constraint'].casefold(): item for item in expected_checks}
    answered: set[str] = set()
    for source in payload.get('checks') or []:
        if not isinstance(source, dict):
            continue
        constraint = str(source.get('constraint') or '')[:400]
        constraint_id = str(source.get('constraint_id') or '')[:20]
        expected = expected_by_id.get(constraint_id) or expected_by_text.get(constraint.casefold())
        if expected:
            constraint_id = expected['constraint_id']
            constraint = expected['constraint']
            answered.add(constraint_id)
        checks.append({
            'constraint_id': constraint_id,
            'constraint': constraint[:400],
            'status': source.get('status') if source.get('status') in ('pass', 'fail', 'uncertain') else 'uncertain',
            'severity': expected['severity'] if expected else (
                source.get('severity') if source.get('severity') in ('hard', 'soft') else 'soft'),
            'evidence': str(source.get('evidence') or '')[:500],
        })
    for expected in expected_checks:
        if expected['constraint_id'] not in answered:
            checks.append({
                'constraint_id': expected['constraint_id'], 'constraint': expected['constraint'],
                'status': 'uncertain', 'severity': expected['severity'],
                'evidence': 'The evaluator omitted this mandatory checklist item.',
            })
    hard_fail = bool(payload.get('hard_fail')) or any(
        check['severity'] == 'hard' and check['status'] == 'fail' for check in checks)
    verification_incomplete = any(
        check['severity'] == 'hard' and check['status'] == 'uncertain' for check in checks)
    if hard_fail:
        scores['layout_fidelity'] = min(scores['layout_fidelity'], 55)
        scores['camera_match'] = min(scores['camera_match'], 60)
    total = round(scores['layout_fidelity'] * .35 + scores['material_fidelity'] * .35
                  + scores['camera_match'] * .15 + scores['visual_quality'] * .15)
    warnings = [str(v)[:300] for v in payload.get('warnings') or []][:12]
    for check in checks:
        if check['status'] in ('fail', 'uncertain'):
            warnings.append(f"{check['status'].upper()}: {check['constraint']} — {check['evidence']}")
    warnings = warnings[:20]
    if hard_fail:
        warnings.insert(0, '发现墙体、门窗、空间顺序或机位硬约束冲突，禁止系统推荐')
    if verification_incomplete:
        warnings.insert(0, '存在未确认或漏评的结构硬约束，复核不完整，暂停系统推荐')
    if total < 80:
        warnings.insert(0, '总分低于 80，请人工检查或重生成')
    if scores['layout_fidelity'] < 80:
        warnings.insert(0, '户型结构遵循度低于 80')
    if phase == 'final' and scores['material_fidelity'] < 80:
        warnings.insert(0, '地板材料一致性低于 80')
    return {
        'status': 'done', **scores, 'total': total, 'hard_fail': hard_fail,
        'verification_incomplete': verification_incomplete,
        'checks': checks, 'warnings': warnings,
        'summary': str(payload.get('summary') or '')[:600],
        'eligible_for_recommendation': not hard_fail and not verification_incomplete
        and scores['layout_fidelity'] >= 80 and scores['camera_match'] >= 75
        and (phase != 'final' or scores['material_fidelity'] >= 80),
    }, None


def choose_anchor_room(rooms: list[dict]) -> Optional[dict]:
    selected = [room for room in rooms if room.get('selected')]
    if not selected:
        return None
    priority = ('客餐厅', '客厅', 'living', '餐厅', 'dining')
    for word in priority:
        match = next((room for room in selected if word.lower() in f"{room.get('label')} {room.get('room_type')}".lower()), None)
        if match:
            return match
    return max(selected, key=lambda room: abs(sum(
        room['polygon'][i]['x'] * room['polygon'][(i + 1) % len(room['polygon'])]['y']
        - room['polygon'][(i + 1) % len(room['polygon'])]['x'] * room['polygon'][i]['y']
        for i in range(len(room['polygon'])))))
