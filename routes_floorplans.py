# -*- coding: utf-8 -*-
"""户型图解析与整屋套图：上传 → 视觉解析 → 人工确认 → 父子任务生成 → 自动复核。"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import os
import re
import time
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image

from .api import call_image_generate, analyze_style_image
from .config import (
    GEMINI_MODEL_MAP, MAIN_OUTPUT_DIR, UPLOAD_DIR, logger, load_config,
    get_auto_color_match_enabled, get_image_provider, get_usage_prices,
)
from .floorplan_engine import (
    analyze_floorplan_image, analyze_spatial_plan, analysis_view, build_floor_material_prompt,
    build_room_prompt, compile_spatial_plan,
    evaluate_candidate, list_analyses, list_suites, load_analysis, load_suite, new_floorplan_id,
    normalize_spatial_plan, render_room_annotation, render_spatial_plan_overlay,
    save_analysis, save_suite, suite_view,
)
from .floorplan_annotations import (
    accepted_openings, annotation_dir, append_operations, dataset_summary, ensure_annotation_v2, export_dataset_zip,
    load_operations, prepare_annotation_source, render_annotation_overlay,
    save_ai_initial, save_revision_snapshot, validate_annotation, view_proxy_source_hash,
)
from .records import save_api_result_jpg, save_api_result_png
from .server_helpers import (
    IMAGE_EXTS, MAX_UPLOAD_BYTES, MAX_UPLOAD_PIXELS, require_ref_image_path,
    require_upload_image_path, save_upload, thumb_url, to_url,
)
from .server_schemas import (
    FloorplanAnalysisRequest, FloorplanAnchorRequest, FloorplanConfirmRequest,
    FloorplanConsentRequest, FloorplanDraftRequest, FloorplanManualRequest,
    FloorplanSpatialPlanGenerateRequest, FloorplanSpatialPlanUpdateRequest,
    FloorplanSuiteRequest, FloorplanVerifyRequest, FloorplanViewProxyConfirmRequest,
    SuiteCandidateReviewRequest,
    SuiteColorMatchRequest,
)
from .usage_stats import record_usage
from . import server_state as state
from .whole_home_cad import CadError, save_cad_upload

router = APIRouter()

_ACTIVE_ANALYSES: dict[str, dict] = {}
_ACTIVE_SUITES: dict[str, dict] = {}
_CANCELLED_SUITES: set[str] = set()
_SUITE_KEYS: dict[str, str] = {}  # 仅内存；绝不写入套图 JSON。


def _analysis_entry(analysis_id: str) -> dict | None:
    return _ACTIVE_ANALYSES.get(analysis_id) or load_analysis(analysis_id)


def _suite_entry(suite_id: str) -> dict | None:
    return _ACTIVE_SUITES.get(suite_id) or load_suite(suite_id)


def _persist_analysis(entry: dict) -> None:
    entry['updated_at'] = time.time()
    save_analysis(entry)


def _persist_suite(entry: dict) -> None:
    save_suite(entry)


def _safe_pdf_name(filename: str) -> str:
    stem = re.sub(r'[^\w.-]+', '_', os.path.splitext(os.path.basename(filename))[0], flags=re.UNICODE)
    stem = stem.strip('._')[:80] or 'floorplan'
    return os.path.join(UPLOAD_DIR, f'plan_{stem}_{uuid.uuid4().hex[:10]}.pdf')


def _write_limited_upload(file: UploadFile, destination: str) -> None:
    tmp = f'{destination}.{uuid.uuid4().hex}.upload'
    total = 0
    try:
        with open(tmp, 'xb') as handle:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, '户型文件超过 50 MiB 上限')
                handle.write(chunk)
        with open(tmp, 'rb') as handle:
            if handle.read(5) != b'%PDF-':
                raise HTTPException(400, '扩展名为 PDF，但文件内容不是有效 PDF')
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _render_pdf_pages(pdf_path: str) -> list[dict]:
    try:
        import fitz
    except ImportError:
        raise HTTPException(500, '缺少 PDF 组件 PyMuPDF，请重新运行依赖安装脚本')
    pages = []
    try:
        document = fitz.open(pdf_path)
        if document.page_count < 1:
            raise HTTPException(400, 'PDF 没有可用页面')
        if document.page_count > 20:
            raise HTTPException(400, 'PDF 页数超过 20 页，请拆分后上传')
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        for index in range(document.page_count):
            page = document.load_page(index)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            if pix.width * pix.height > MAX_UPLOAD_PIXELS:
                raise HTTPException(413, f'PDF 第 {index + 1} 页像素超过 8000 万上限')
            path = os.path.join(UPLOAD_DIR, f'{base}_p{index + 1}.png')
            pix.save(path)
            pages.append({
                'page': index + 1, 'path': path, 'url': to_url(path),
                'thumb': thumb_url(path), 'name': os.path.basename(path),
            })
        document.close()
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(400, f'PDF 解析失败: {ex}')
    return pages


@router.post('/api/uploads/floorplan')
def upload_floorplan(file: UploadFile = File(...)):
    extension = os.path.splitext(file.filename or '')[1].lower()
    if extension == '.pdf':
        destination = _safe_pdf_name(file.filename or 'floorplan.pdf')
        _write_limited_upload(file, destination)
        try:
            pages = _render_pdf_pages(destination)
        except Exception:
            try:
                os.remove(destination)
            except OSError:
                pass
            raise
        return {**pages[0], 'source_path': destination, 'page_count': len(pages), 'pages': pages}
    if extension not in IMAGE_EXTS:
        raise HTTPException(400, '户型图仅支持 PNG/JPG/WebP/PDF')
    result = save_upload(file, 'plan_')
    return {**result, 'source_path': result['path'], 'page_count': 1,
            'pages': [{**result, 'page': 1}]}


@router.post('/api/uploads/cad')
def upload_cad(file: UploadFile = File(...)):
    """Store a CAD source without weakening the image-upload allowlist."""
    try:
        result = save_cad_upload(file)
    except CadError as ex:
        raise HTTPException(ex.status_code, ex.to_dict()) from ex
    return {**result, 'url': to_url(result['path'])}


async def _analyze_bg(entry: dict, api_key: str) -> None:
    try:
        entry.update(status='analyzing', stage='正在识别房间、墙体与门窗', error='')
        _persist_analysis(entry)
        parsed, error = await asyncio.to_thread(analyze_floorplan_image, api_key, entry['floorplan_path'])
        if error or not parsed:
            entry.update(status='failed', stage='', error=error or '户型解析失败')
        else:
            ai_raw = parsed.pop('_ai_raw', {})
            ai_model = parsed.pop('_ai_model', '')
            entry.update(status='done', stage='等待客户确认', error='', confirmed=False, **parsed)
            entry.update(ai_model=ai_model, annotation_status='draft', revision=0,
                         schema_version=3, openings_review_status='pending', view_proxies={})
            entry['geometry_report'] = validate_annotation(entry)
            save_ai_initial(entry, ai_raw)
            try:
                render_annotation_overlay(entry, 'ai')
            except Exception as ex:
                logger.warning(f"[户型解析] AI 标注叠加图保存失败: {ex}")
    except Exception as ex:
        logger.exception('[户型解析] 未处理异常')
        entry.update(status='failed', stage='', error=str(ex))
    finally:
        _persist_analysis(entry)
        _ACTIVE_ANALYSES.pop(entry['analysis_id'], None)


@router.post('/api/floorplans/analyze')
async def create_floorplan_analysis(req: FloorplanAnalysisRequest):
    floorplan_path = require_upload_image_path(req.floorplan_path, '户型图', required=True)
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    analysis_id = new_floorplan_id('analysis')
    source = prepare_annotation_source(analysis_id, floorplan_path)
    entry = {
        'analysis_id': analysis_id, 'status': 'queued', 'stage': '等待解析', 'error': '',
        'created_at': time.time(), 'updated_at': time.time(), 'floorplan_path': source['path'],
        'summary': '', 'orientation': '', 'entrance': None, 'warnings': [],
        'rooms': [], 'openings': [], 'confirmed': False, 'schema_version': 3,
        'revision': 0, 'annotation_status': 'draft', 'training_consent': False,
        'training_eligible': False, 'source': source, 'annotator_id': 'local-user',
        'spatial_plans': {}, 'view_proxies': {}, 'openings_review_status': 'pending',
    }
    _ACTIVE_ANALYSES[analysis_id] = entry
    _persist_analysis(entry)
    state.spawn(_analyze_bg(entry, api_key))
    return analysis_view(entry)


@router.post('/api/floorplans/manual')
def create_manual_floorplan(req: FloorplanManualRequest):
    floorplan_path = require_upload_image_path(req.floorplan_path, '户型图', required=True)
    analysis_id = new_floorplan_id('analysis')
    source = prepare_annotation_source(analysis_id, floorplan_path)
    entry = {
        'analysis_id': analysis_id, 'status': 'done', 'stage': '人工标注草稿', 'error': '',
        'created_at': time.time(), 'updated_at': time.time(), 'floorplan_path': source['path'],
        'summary': '人工创建的户型标注', 'orientation': '', 'entrance': None, 'warnings': [],
        'rooms': [], 'openings': [], 'confirmed': False, 'schema_version': 3,
        'revision': 0, 'annotation_status': 'draft', 'training_consent': False,
        'training_eligible': False, 'source': source, 'annotator_id': 'local-user',
        'ai_initial_path': '', 'ai_model': '', 'prompt_version': 'manual-v1',
        'spatial_plans': {}, 'view_proxies': {}, 'openings_review_status': 'pending',
    }
    ensure_annotation_v2(entry)
    _persist_analysis(entry)
    append_operations(analysis_id, 0, [{'type': 'create_manual_annotation', 'payload': {}}], 'local-user')
    save_revision_snapshot(entry, ['create_manual_annotation'])
    return analysis_view(entry)


@router.get('/api/floorplans')
def get_floorplan_analyses(limit: int = 50):
    disk = {entry['analysis_id']: entry for entry in list_analyses(limit)}
    disk.update(_ACTIVE_ANALYSES)
    rows = sorted(disk.values(), key=lambda entry: entry.get('updated_at', 0), reverse=True)
    return [analysis_view(entry) for entry in rows[:max(1, min(limit, 200))]]


@router.get('/api/floorplans/{analysis_id}')
def get_floorplan_analysis(analysis_id: str):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型解析记录不存在')
    ensure_annotation_v2(entry)
    if entry.get('rooms') and not (entry.get('geometry_report') or {}).get('checked_at'):
        entry['geometry_report'] = validate_annotation(entry)
        _persist_analysis(entry)
    return analysis_view(entry)


def _rooms_for_storage(models) -> list[dict]:
    rooms = [room.model_dump() for room in models]
    for room in rooms:
        cameras = room.get('cameras') or []
        legacy = room.get('camera')
        if not cameras and legacy:
            cameras = [{
                **legacy, 'id': legacy.get('id') or f"camera_{uuid.uuid4().hex[:12]}",
                'name': legacy.get('name') or '机位 1', 'source': legacy.get('source') or 'manual',
                'confirmed': True, 'enabled_for_generation': True,
            }]
        seen = set()
        for index, camera in enumerate(cameras):
            camera['id'] = camera.get('id') or f"camera_{uuid.uuid4().hex[:12]}"
            if camera['id'] in seen:
                camera['id'] = f"camera_{uuid.uuid4().hex[:12]}"
            seen.add(camera['id'])
            camera['name'] = camera.get('name') or f'机位 {index + 1}'
        room['cameras'] = cameras
        if room.get('primary_camera_id') not in seen:
            room['primary_camera_id'] = cameras[0]['id'] if cameras else ''
        primary = next((camera for camera in cameras if camera['id'] == room['primary_camera_id']), None)
        room['camera'] = primary
    return rooms


def _revision_conflict(entry: dict, base_revision: int) -> None:
    if int(entry.get('revision') or 0) != int(base_revision):
        raise HTTPException(409, {
            'message': '标注已在其他页面更新，请刷新后重试',
            'current_revision': int(entry.get('revision') or 0),
        })


def _ensure_annotation_source(entry: dict) -> None:
    if (entry.get('source') or {}).get('path'):
        return
    source = prepare_annotation_source(entry['analysis_id'], entry['floorplan_path'])
    entry['source'] = source
    entry['floorplan_path'] = source['path']


@router.put('/api/floorplans/{analysis_id}/draft')
def save_floorplan_draft(analysis_id: str, req: FloorplanDraftRequest):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型标注不存在')
    ensure_annotation_v2(entry)
    _ensure_annotation_source(entry)
    _revision_conflict(entry, req.base_revision)
    rooms = _rooms_for_storage(req.rooms)
    edited_ids = {operation.room_id for operation in req.operations if operation.room_id}
    for room in rooms:
        if room['id'] in edited_ids and room.get('source') == 'ai':
            room['source'] = 'ai_edited'
    ids = [room['id'] for room in rooms]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, '房间 ID 重复')
    id_set = set(ids)
    openings = [opening.model_dump() for opening in req.openings]
    for opening in openings:
        opening['room_ids'] = [room_id for room_id in opening['room_ids'] if room_id in id_set]
    revision = int(entry.get('revision') or 0) + 1
    entry.update(
        rooms=rooms, openings=openings, openings_review_status=req.openings_review_status,
        entrance=req.entrance.model_dump() if req.entrance else None,
        orientation=req.orientation, revision=revision, annotator_id=req.annotator_id,
        annotation_status='draft', confirmed=False, status='done', stage='人工标注草稿',
        training_eligible=False, verified_at=None, verified_by='', error='', spatial_plans={},
        view_proxies={}, schema_version=3,
    )
    entry['geometry_report'] = validate_annotation(entry)
    operations = [operation.model_dump() for operation in req.operations] or \
        [{'type': 'autosave_snapshot', 'payload': {}}]
    append_operations(
        analysis_id, revision, operations,
        req.annotator_id,
    )
    save_revision_snapshot(entry, [operation.get('type', '') for operation in operations])
    _persist_analysis(entry)
    return analysis_view(entry)


@router.get('/api/floorplans/{analysis_id}/history')
def get_floorplan_history(analysis_id: str, limit: int = 500):
    if not _analysis_entry(analysis_id):
        raise HTTPException(404, '户型标注不存在')
    return {'analysis_id': analysis_id, 'operations': load_operations(analysis_id, limit)}


@router.post('/api/floorplans/{analysis_id}/verify')
def verify_floorplan(analysis_id: str, req: FloorplanVerifyRequest):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型标注不存在')
    ensure_annotation_v2(entry)
    _ensure_annotation_source(entry)
    _revision_conflict(entry, req.base_revision)
    report = validate_annotation(entry)
    if report['hard_errors']:
        entry['geometry_report'] = report
        _persist_analysis(entry)
        raise HTTPException(400, {'message': '存在必须修正的户型或机位错误', **report})
    warning_codes = {warning.get('code') for warning in report['warnings']}
    missing_ack = sorted(code for code in warning_codes if code and code not in req.acknowledged_warning_codes)
    if missing_ack:
        raise HTTPException(400, {'message': '请确认标注警告后再验收', 'warning_codes': missing_ack, **report})
    revision = int(entry.get('revision') or 0) + 1
    now = time.time()
    entry.update(
        revision=revision, geometry_report=report, annotation_status='verified',
        confirmed=True, status='confirmed', stage='人工验收通过', training_consent=req.training_consent,
        training_eligible=bool(req.training_consent), verified_at=now, verified_by=req.annotator_id,
        annotator_id=req.annotator_id, error='',
        verified_revision=revision,
    )
    append_operations(analysis_id, revision, [{
        'type': 'verify_annotation', 'payload': {
            'training_consent': req.training_consent,
            'acknowledged_warning_codes': req.acknowledged_warning_codes,
        },
    }], req.annotator_id)
    save_revision_snapshot(entry, ['verify_annotation'])
    try:
        render_annotation_overlay(entry, 'verified')
    except Exception as ex:
        logger.warning(f'[户型标注] 验收叠加图保存失败: {ex}')
    _persist_analysis(entry)
    return analysis_view(entry)


def _annotation_room_camera(entry: dict, room_id: str, camera_id: str) -> tuple[dict, dict]:
    room = next((item for item in entry.get('rooms') or [] if item.get('id') == room_id), None)
    if not room:
        raise HTTPException(404, '房间不存在')
    camera = next((item for item in room.get('cameras') or [] if item.get('id') == camera_id), None)
    if not camera:
        raise HTTPException(404, '机位不存在')
    if not room.get('selected') or not camera.get('confirmed') or not camera.get('enabled_for_generation', True):
        raise HTTPException(400, '只有参与生成且人工确认的机位可以创建空间规划')
    return room, camera


def _spatial_plan_overlay(entry: dict, room: dict, camera: dict, plan: dict) -> str:
    unit = json.loads(json.dumps(room, ensure_ascii=False))
    unit['camera'] = json.loads(json.dumps(camera, ensure_ascii=False))
    unit['annotation_room_id'] = room['id']
    plan_folder = f"spatial_plan_{entry['analysis_id']}"
    annotated = render_room_annotation(
        entry['floorplan_path'], unit, accepted_openings(entry, room.get('id') or ''), plan_folder)
    return render_spatial_plan_overlay(annotated, plan, plan_folder, f"{room['id']}_{camera['id']}")


@router.post('/api/floorplans/{analysis_id}/spatial-plans/generate')
async def generate_floorplan_spatial_plan(analysis_id: str, req: FloorplanSpatialPlanGenerateRequest):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型标注不存在')
    ensure_annotation_v2(entry)
    if not entry.get('confirmed') or entry.get('annotation_status') != 'verified':
        raise HTTPException(409, '请先完成人工验收，再让 Gemini 生成空间约束')
    room, camera = _annotation_room_camera(entry, req.room_id, req.camera_id)
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    unit = json.loads(json.dumps(room, ensure_ascii=False))
    unit['camera'] = json.loads(json.dumps(camera, ensure_ascii=False))
    unit['annotation_room_id'] = room['id']
    plan_folder = f"spatial_plan_{analysis_id}"
    annotated_path = await asyncio.to_thread(
        render_room_annotation, entry['floorplan_path'], unit,
        accepted_openings(entry, room.get('id') or ''), plan_folder)
    plan, error = await asyncio.to_thread(
        analyze_spatial_plan, api_key, entry['floorplan_path'], annotated_path, unit,
        accepted_openings(entry, room.get('id') or ''), entry.get('orientation') or '')
    if error or not plan:
        raise HTTPException(502, f'Gemini 空间规划失败: {error or "返回为空"}')
    revision = int(entry.get('verified_revision') or entry.get('revision') or 0)
    now = time.time()
    plan.update(
        spatial_plan_id=f"spatial_{uuid.uuid4().hex[:16]}", analysis_id=analysis_id,
        room_id=room['id'], camera_id=camera['id'], annotation_revision=revision,
        camera_snapshot=json.loads(json.dumps(camera, ensure_ascii=False)),
        status='draft', created_at=now, updated_at=now, locked_at=None, locked_by='',
    )
    plan['overlay_path'] = await asyncio.to_thread(_spatial_plan_overlay, entry, room, camera, plan)
    entry.setdefault('spatial_plans', {})[camera['id']] = plan
    append_operations(analysis_id, int(entry.get('revision') or 0), [{
        'type': 'generate_spatial_plan', 'room_id': room['id'], 'camera_id': camera['id'],
        'payload': {'spatial_plan_id': plan['spatial_plan_id'], 'planner_model': plan.get('planner_model', '')},
    }], 'gemini-planner')
    _persist_analysis(entry)
    return analysis_view(entry)


@router.put('/api/floorplans/{analysis_id}/spatial-plans/{camera_id}')
def update_floorplan_spatial_plan(analysis_id: str, camera_id: str,
                                  req: FloorplanSpatialPlanUpdateRequest):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型标注不存在')
    ensure_annotation_v2(entry)
    current = (entry.get('spatial_plans') or {}).get(camera_id)
    if not current:
        raise HTTPException(404, '该机位尚未生成空间规划')
    room, camera = _annotation_room_camera(entry, current.get('room_id') or '', camera_id)
    verified_revision = int(entry.get('verified_revision') or entry.get('revision') or 0)
    if int(current.get('annotation_revision') or -1) != verified_revision:
        raise HTTPException(409, '户型标注已变化，请重新生成空间规划')
    normalized = normalize_spatial_plan(req.model_dump())
    allowed_opening_ids = {
        opening.get('id') for opening in accepted_openings(entry, room.get('id') or '')
    }
    required_opening_ids = set((normalized.get('architecture') or {}).get('required_opening_ids') or [])
    unknown_opening_ids = sorted(opening_id for opening_id in required_opening_ids
                                 if opening_id not in allowed_opening_ids)
    if unknown_opening_ids:
        raise HTTPException(400, f"空间约束引用了未经人工确认的门窗: {', '.join(unknown_opening_ids[:8])}")
    if req.status == 'locked' and (not normalized['space_summary'].strip()
                                   or not normalized['hard_constraints']):
        raise HTTPException(400, '锁定前必须填写空间总结，并保留至少一条可验证的硬约束')
    now = time.time()
    current.update(normalized, status=req.status, updated_at=now)
    if req.status == 'locked':
        current.update(locked_at=now, locked_by=req.annotator_id)
    else:
        current.update(locked_at=None, locked_by='')
    current['overlay_path'] = _spatial_plan_overlay(entry, room, camera, current)
    entry.setdefault('spatial_plans', {})[camera_id] = current
    if camera_id in (entry.get('view_proxies') or {}):
        entry['view_proxies'][camera_id]['status'] = 'stale'
    append_operations(analysis_id, int(entry.get('revision') or 0), [{
        'type': 'lock_spatial_plan' if req.status == 'locked' else 'edit_spatial_plan',
        'room_id': room['id'], 'camera_id': camera_id,
        'payload': {'spatial_plan_id': current.get('spatial_plan_id'), 'status': req.status},
    }], req.annotator_id)
    _persist_analysis(entry)
    return analysis_view(entry)


@router.post('/api/floorplans/{analysis_id}/view-proxies/{camera_id}/confirm')
def confirm_floorplan_view_proxy(analysis_id: str, camera_id: str,
                                 req: FloorplanViewProxyConfirmRequest):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型标注不存在')
    ensure_annotation_v2(entry)
    plan = (entry.get('spatial_plans') or {}).get(camera_id)
    if not plan or plan.get('status') != 'locked':
        raise HTTPException(409, '请先锁定该机位的空间约束')
    room, camera = _annotation_room_camera(entry, plan.get('room_id') or '', camera_id)
    verified_revision = int(entry.get('verified_revision') or entry.get('revision') or 0)
    if int(plan.get('annotation_revision') or -1) != verified_revision:
        raise HTTPException(409, '户型标注已变化，请重新生成空间规划')
    if entry.get('openings_review_status') != 'confirmed':
        raise HTTPException(409, '请先完成人工门窗审核')

    prefix = 'data:image/png;base64,'
    if not req.image_data_url.startswith(prefix):
        raise HTTPException(400, '灰模必须是 PNG data URL')
    try:
        raw = base64.b64decode(req.image_data_url[len(prefix):], validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, '灰模 PNG 数据无效')
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(413, '灰模图片超过 12 MiB 上限')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 16_000_000:
                raise HTTPException(413, '灰模像素超过 1600 万上限')
            image = source.convert('RGB')
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(400, f'灰模图片无效: {ex}')

    safe_camera = re.sub(r'[^a-zA-Z0-9_-]+', '_', camera_id)[:80] or 'camera'
    path = os.path.join(annotation_dir(analysis_id),
                        f'view_proxy_{safe_camera}_{uuid.uuid4().hex[:8]}.png')
    image.save(path, 'PNG', optimize=True)
    def number(name: str, fallback: float, minimum: float, maximum: float) -> float:
        try:
            value = float(req.render_config.get(name) or fallback)
        except (TypeError, ValueError):
            value = fallback
        if not (minimum <= value <= maximum):
            value = fallback
        return value

    config = {
        'camera_height_m': round(number('camera_height_m', float(camera.get('height_m') or 1.55), .3, 2.5), 3),
        'focal_length_mm': round(number('focal_length_mm', float(camera.get('focal_length_mm') or 24), 12, 120), 2),
        'wall_height_m': round(number('wall_height_m', 2.8, 2.0, 5.0), 2),
        'room_long_side_m': round(number('room_long_side_m', 8.0, 2.0, 30.0), 2),
        'renderer': 'threejs-clay-v1',
    }
    source_hash = view_proxy_source_hash(entry, room, camera, plan, req.aspect_ratio)
    proxy = {
        'view_proxy_id': f'proxy_{uuid.uuid4().hex[:16]}', 'status': 'confirmed',
        'path': path, 'source_hash': source_hash, 'aspect_ratio': req.aspect_ratio,
        'render_config': config, 'annotation_revision': verified_revision,
        'spatial_plan_id': plan.get('spatial_plan_id'),
        'confirmed_at': time.time(), 'confirmed_by': req.annotator_id,
    }
    entry.setdefault('view_proxies', {})[camera_id] = proxy
    append_operations(analysis_id, int(entry.get('revision') or 0), [{
        'type': 'confirm_view_proxy', 'room_id': room.get('id'), 'camera_id': camera_id,
        'payload': {'view_proxy_id': proxy['view_proxy_id'], 'source_hash': source_hash,
                    'aspect_ratio': req.aspect_ratio},
    }], req.annotator_id)
    _persist_analysis(entry)
    return analysis_view(entry)


@router.post('/api/floorplans/{analysis_id}/training-consent')
def set_floorplan_training_consent(analysis_id: str, req: FloorplanConsentRequest):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型标注不存在')
    ensure_annotation_v2(entry)
    _ensure_annotation_source(entry)
    revision = int(entry.get('revision') or 0) + 1
    entry.update(
        revision=revision, training_consent=req.allowed,
        training_eligible=bool(req.allowed and entry.get('annotation_status') == 'verified'),
    )
    append_operations(analysis_id, revision, [{
        'type': 'set_training_consent', 'payload': {'allowed': req.allowed},
    }], req.annotator_id)
    save_revision_snapshot(entry, ['set_training_consent'])
    _persist_analysis(entry)
    return analysis_view(entry)


@router.get('/api/floorplan-dataset/summary')
def get_floorplan_dataset_summary():
    return dataset_summary()


@router.get('/api/floorplan-dataset/export')
def export_floorplan_dataset():
    path, summary = export_dataset_zip()
    return FileResponse(
        path, media_type='application/zip', filename=os.path.basename(path),
        headers={'X-Floorplan-Count': str(summary['floorplans']), 'X-Camera-Count': str(summary['cameras'])},
    )


@router.put('/api/floorplans/{analysis_id}')
def confirm_floorplan(analysis_id: str, req: FloorplanConfirmRequest):
    entry = _analysis_entry(analysis_id)
    if not entry:
        raise HTTPException(404, '户型解析记录不存在')
    if entry.get('status') not in ('done', 'confirmed'):
        raise HTTPException(409, '户型尚未解析完成')
    ensure_annotation_v2(entry)
    _ensure_annotation_source(entry)
    rooms = _rooms_for_storage(req.rooms)
    ids = [room['id'] for room in rooms]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, '房间 ID 重复，请重新分析或修改')
    selected = [room for room in rooms if room['selected']]
    if not selected:
        raise HTTPException(400, '至少选择一个要生成的房间')
    missing_camera = [room['label'] for room in selected if not any(
        camera.get('confirmed') for camera in room.get('cameras') or [])]
    if missing_camera:
        raise HTTPException(400, f"以下房间尚未标记机位: {', '.join(missing_camera[:6])}")
    id_set = set(ids)
    openings = [opening.model_dump() for opening in req.openings]
    for opening in openings:
        opening['room_ids'] = [rid for rid in opening['room_ids'] if rid in id_set]
    revision = int(entry.get('revision') or 0) + 1
    entry.update(
        rooms=rooms, openings=openings, openings_review_status=req.openings_review_status,
        entrance=req.entrance.model_dump() if req.entrance else None,
        orientation=req.orientation, confirmed=True, status='confirmed', stage='户型已确认', error='',
        revision=revision, annotation_status='verified', training_consent=False,
        training_eligible=False, verified_at=time.time(), verified_by='legacy-client',
        verified_revision=revision, view_proxies={}, schema_version=3,
    )
    report = validate_annotation(entry)
    if report['hard_errors']:
        raise HTTPException(400, {'message': '户型存在必须修正的几何错误', **report})
    entry['geometry_report'] = report
    append_operations(analysis_id, revision, [{'type': 'snapshot_replace', 'payload': {'legacy': True}}], 'legacy-client')
    save_revision_snapshot(entry, ['snapshot_replace'])
    try:
        render_annotation_overlay(entry, 'verified')
    except Exception as ex:
        logger.warning(f'[户型标注] 兼容确认叠加图保存失败: {ex}')
    _persist_analysis(entry)
    return analysis_view(entry)


def _find_room(suite: dict, room_id: str) -> dict | None:
    return next((room for room in suite.get('rooms') or [] if room.get('id') == room_id), None)


def _find_candidate(suite: dict, result_id: str) -> tuple[dict | None, dict | None]:
    for room in suite.get('rooms') or []:
        for candidate in room.get('candidates') or []:
            if candidate.get('result_id') == result_id:
                return room, candidate
    return None, None


def _room_finalize(room: dict) -> None:
    done = [candidate for candidate in room.get('candidates') or [] if candidate.get('status') == 'done']
    failed = [candidate for candidate in room.get('candidates') or [] if candidate.get('status') == 'failed']
    room['status'] = 'done' if done and not failed else ('partial' if done else 'failed')
    eligible = [candidate for candidate in done if (candidate.get('evaluation') or {}).get('eligible_for_recommendation')]
    room['recommended_result_id'] = max(
        eligible, key=lambda value: (value.get('evaluation') or {}).get('total') or -1,
    ).get('result_id') if eligible else ''


def _suite_finalize(suite: dict) -> None:
    selected = [room for room in suite.get('rooms') or [] if room.get('selected')]
    for room in selected:
        _room_finalize(room)
    done = [room for room in selected if room.get('status') == 'done']
    partial = [room for room in selected if room.get('status') == 'partial']
    if len(done) == len(selected) and not partial:
        status = 'done'
    elif done or partial:
        status = 'partial'
    else:
        status = 'failed'
    suite.update(status=status, stage='', error='' if status == 'done' else '部分房间或候选生成失败')
    _persist_suite(suite)


def _candidate_records(room: dict, req: FloorplanSuiteRequest, analysis: dict) -> list[dict]:
    """Create one candidate group per selected model for a room/camera unit."""
    if not room.get('selected'):
        return []
    candidates = []
    revision = int(analysis.get('verified_revision') or analysis.get('revision') or 0)
    for model_key in req.model_keys:
        for model_index in range(1, req.candidates_per_room + 1):
            candidates.append({
                'result_id': f"{room['id']}_{model_key}_{model_index}_{uuid.uuid4().hex[:8]}",
                'index': len(candidates) + 1, 'model_index': model_index,
                'model_key': model_key, 'status': 'queued', 'stage': '', 'error': '', 'path': '',
                'evaluation': None, 'analysis_id': req.analysis_id,
                'annotation_revision': revision,
                'annotation_room_id': room.get('annotation_room_id') or room['id'],
                'camera_id': room.get('camera_id') or '', 'review_status': 'unreviewed',
                'review_tags': [], 'review_note': '', 'best': False,
            })
    return candidates


async def _generate_candidate(suite: dict, room: dict, candidate: dict, api_key: str) -> None:
    if suite['suite_id'] in _CANCELLED_SUITES:
        candidate.update(status='failed', stage='', error='已取消')
        return
    # New suites can mix models. Older persisted suites only have suite.model_key.
    key = candidate.get('model_key') or suite.get('model_key', 'pro')
    model_name = 'Nano Banana 2' if key == 'b2' else 'Nano Banana Pro'
    model_id = GEMINI_MODEL_MAP[model_name]
    try:
        structure_prompt, structure_inputs = build_room_prompt(suite, room, room.get('annotated_path', ''))
    except Exception as ex:
        candidate.update(status='failed', stage='', error=str(ex), model=model_name)
        _persist_suite(suite)
        return
    candidate.update(
        status='running', stage='第一轮：结构写实化', error='', model=model_name,
        generation_trace=[], view_proxy_id=(room.get('view_proxy') or {}).get('view_proxy_id', ''),
        view_proxy_hash=(room.get('view_proxy') or {}).get('source_hash', ''),
    )
    _persist_suite(suite)

    def on_stage(text):
        candidate['stage'] = str(text or '')
        _persist_suite(suite)

    started = time.time()
    structure_image = structure_error = structure_provider = None
    sem = state.model_semaphores[key]
    try:
        if sem.locked():
            on_stage('排队中')
        async with sem:
            pass_started = time.time()
            structure_image, structure_error, structure_provider = await asyncio.to_thread(
                call_image_generate, api_key, model_id, structure_prompt, room['view_proxy_path'],
                suite['resolution'], suite['aspect_ratio'], None, None, on_stage,
                lambda: suite['suite_id'] in _CANCELLED_SUITES, None, structure_inputs, False,
            )
            candidate['generation_trace'].append({
                'pass': 'structure', 'provider': structure_provider or '',
                'seconds': round(time.time() - pass_started, 1),
                'success': structure_image is not None, 'error': structure_error or '',
            })
    except Exception as ex:
        structure_error = str(ex)
        structure_provider = get_image_provider()
    if structure_image is None:
        candidate.update(status='failed', stage='', error=structure_error or '结构写实化未返回图片',
                         seconds=round(time.time() - started, 1), provider=structure_provider or '')
        record_usage('户型套图', model_name, structure_provider, False, 'generate')
        _persist_suite(suite)
        return
    record_usage('户型套图', model_name, structure_provider, True, 'generate')
    structure_path = await asyncio.to_thread(
        save_api_result_jpg, structure_image,
        f"户型套图_{room['label']}_{model_name}_结构", room['view_proxy_path'])
    if not structure_path:
        candidate.update(status='failed', stage='', error='结构图已生成，但保存失败')
        _persist_suite(suite)
        return
    candidate.update(structure_path=structure_path, path=structure_path,
                     provider=structure_provider or '', material_pass_status='pending')

    if suite['suite_id'] in _CANCELLED_SUITES:
        candidate.update(status='done', stage='', error='已取消，第一轮结构图已保留',
                         seconds=round(time.time() - started, 1), material_pass_status='skipped')
        _persist_suite(suite)
        return

    on_stage('第一轮 QA：核对灰模结构')
    compiled_plan = compile_spatial_plan(room.get('spatial_plan') or {}, room)
    structure_evaluation, structure_eval_error = await asyncio.to_thread(
        evaluate_candidate, api_key, room['view_proxy_path'], suite['floor_path'], structure_path,
        None, None, compiled_plan, None, 'structure')
    candidate.update(structure_evaluation=structure_evaluation,
                     structure_evaluation_error=structure_eval_error or '')
    if structure_evaluation.get('status') == 'done' and structure_evaluation.get('hard_fail'):
        candidate.update(
            status='done', stage='', error='第一轮结构未通过，已停止地板调用以避免继续计费',
            evaluation=structure_evaluation, evaluation_error=structure_eval_error or '',
            material_pass_status='skipped', seconds=round(time.time() - started, 1),
        )
        _persist_suite(suite)
        return

    material_prompt, material_inputs = build_floor_material_prompt(suite, room, structure_path)
    material_image = material_error = material_provider = None
    on_stage('第二轮：只应用地板小样')
    try:
        async with sem:
            pass_started = time.time()
            material_image, material_error, material_provider = await asyncio.to_thread(
                call_image_generate, api_key, model_id, material_prompt, structure_path,
                suite['resolution'], suite['aspect_ratio'], None, None, on_stage,
                lambda: suite['suite_id'] in _CANCELLED_SUITES, None, material_inputs, False,
            )
            candidate['generation_trace'].append({
                'pass': 'material', 'provider': material_provider or '',
                'seconds': round(time.time() - pass_started, 1),
                'success': material_image is not None, 'error': material_error or '',
                'continuation_mode': 'image_edit',
            })
    except Exception as ex:
        material_error = str(ex)
        material_provider = get_image_provider()
    if material_image is None:
        record_usage('户型套图', model_name, material_provider, False, 'generate')
        candidate.update(
            status='done', stage='', error=f'地板应用失败，已保留结构图: {material_error or "模型未返回图片"}',
            evaluation=structure_evaluation, evaluation_error=structure_eval_error or '',
            material_pass_status='failed', seconds=round(time.time() - started, 1),
        )
        _persist_suite(suite)
        return
    record_usage('户型套图', model_name, material_provider, True, 'generate')
    material_path = await asyncio.to_thread(
        save_api_result_jpg, material_image,
        f"户型套图_{room['label']}_{model_name}_地板", suite['floor_path'])
    if not material_path:
        candidate.update(status='done', stage='', error='地板图已生成但保存失败，已保留结构图',
                         material_pass_status='failed', seconds=round(time.time() - started, 1))
        _persist_suite(suite)
        return
    candidate.update(api_original_path=material_path, material_path=material_path,
                     material_pass_status='done', provider=material_provider or structure_provider or '')
    final_path = material_path
    if get_auto_color_match_enabled() and room.get('apply_floor') and suite['suite_id'] not in _CANCELLED_SUITES:
        candidate['stage'] = '自动识别地板并校色'
        _persist_suite(suite)
        try:
            from .routes_jobs import _auto_color_match_generated
            corrected, metadata, color_error = await asyncio.to_thread(
                _auto_color_match_generated, material_image, material_path, suite['floor_path'])
            if corrected is not None:
                corrected_path = await asyncio.to_thread(
                    save_api_result_png, corrected, f"户型套图_{room['label']}_自动校色", material_path, metadata)
                if corrected_path:
                    final_path = corrected_path
                    candidate['auto_color_status'] = 'done'
                    candidate['auto_color_metadata'] = metadata
                else:
                    candidate['auto_color_status'] = 'failed'
                    candidate['auto_color_error'] = '自动校色结果保存失败'
            else:
                candidate['auto_color_status'] = 'failed'
                candidate['auto_color_error'] = color_error
        except Exception as ex:
            candidate['auto_color_status'] = 'failed'
            candidate['auto_color_error'] = str(ex)
    else:
        candidate['auto_color_status'] = 'disabled'
    candidate['path'] = final_path
    candidate['final_path'] = final_path
    candidate['seconds'] = round(time.time() - started, 1)
    if suite['suite_id'] in _CANCELLED_SUITES:
        candidate.update(status='done', stage='', error='已取消，但已出图已保留')
        _persist_suite(suite)
        return
    candidate['stage'] = '正在复核户型、机位与地板'
    _persist_suite(suite)
    evaluation, eval_error = await asyncio.to_thread(
        evaluate_candidate, api_key, room['view_proxy_path'], suite['floor_path'],
        final_path, suite.get('anchor_path'), None, compiled_plan, structure_path, 'final')
    candidate.update(status='done', stage='', error='', evaluation=evaluation,
                     evaluation_error=eval_error or '')
    _persist_suite(suite)


async def _generate_rooms(suite: dict, rooms: list[dict], api_key: str, *, failed_only: bool = False) -> None:
    tasks = []
    for room in rooms:
        room['status'] = 'running'
        for candidate in room.get('candidates') or []:
            if failed_only and candidate.get('status') == 'done':
                continue
            candidate.update(status='queued', stage='等待生成', error='')
            tasks.append(_generate_candidate(suite, room, candidate, api_key))
    _persist_suite(suite)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for room in rooms:
        _room_finalize(room)
    _persist_suite(suite)


async def _run_suite_bg(suite: dict, api_key: str) -> None:
    suite_id = suite['suite_id']
    try:
        suite.update(
            status='running',
            stage='准备整屋风格蓝图' if suite.get('generation_mode') == 'consistent' else '准备各房间生成参数',
            error='',
        )
        if suite.get('style_ref_path'):
            style_text, style_error = await asyncio.to_thread(
                analyze_style_image, api_key, suite['style_ref_path'])
            if style_text:
                suite['style_brief'] = style_text
            elif style_error:
                suite.setdefault('warnings', []).append(f'风格图分析失败，仍会直接参考图片: {style_error}')
        _persist_suite(suite)
        selected = [room for room in suite['rooms'] if room.get('selected')]
        if suite['generation_mode'] == 'consistent':
            anchor = _find_room(suite, suite['anchor_room_id'])
            suite['stage'] = f"先生成主空间：{anchor['label']}"
            _persist_suite(suite)
            await _generate_rooms(suite, [anchor], api_key)
            if suite_id in _CANCELLED_SUITES:
                _suite_finalize(suite)
            elif any(candidate.get('status') == 'done' for candidate in anchor.get('candidates') or []):
                suite.update(status='waiting_anchor', stage='请选择主空间候选后继续生成', error='')
                _persist_suite(suite)
            else:
                suite.update(status='failed', stage='', error='主空间没有生成成功，无法继续一致性模式')
                _persist_suite(suite)
        else:
            suite['stage'] = '并行生成所有已选房间'
            _persist_suite(suite)
            await _generate_rooms(suite, selected, api_key)
            _suite_finalize(suite)
    except Exception as ex:
        logger.exception(f'[户型套图] 主任务异常 suite={suite_id}')
        has_result = any(c.get('path') for r in suite.get('rooms') or [] for c in r.get('candidates') or [])
        suite.update(status='partial' if has_result else 'failed', stage='', error=str(ex))
        _persist_suite(suite)
    finally:
        if suite.get('status') != 'waiting_anchor':
            _ACTIVE_SUITES.pop(suite_id, None)
            _CANCELLED_SUITES.discard(suite_id)
            _SUITE_KEYS.pop(suite_id, None)


@router.post('/api/floorplan-suites')
async def create_floorplan_suite(req: FloorplanSuiteRequest):
    analysis = _analysis_entry(req.analysis_id)
    if not analysis or not analysis.get('confirmed'):
        raise HTTPException(400, '户型尚未完成解析确认')
    ensure_annotation_v2(analysis)
    floor_path = require_upload_image_path(req.floor_path, '地板小样', required=True)
    style_ref_path = require_ref_image_path(req.style_ref_path) if req.style_ref_path else None
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    annotation_rooms = json.loads(json.dumps(analysis['rooms'], ensure_ascii=False))
    selected_sources = [room for room in annotation_rooms if room.get('selected')]
    if not selected_sources:
        raise HTTPException(400, '至少选择一个要生成的房间')
    rooms = []
    for source_room in selected_sources:
        cameras = [camera for camera in source_room.get('cameras') or []
                   if camera.get('confirmed') and camera.get('enabled_for_generation', True)]
        requested_ids = set(req.camera_ids_by_room.get(source_room['id']) or [])
        if requested_ids:
            cameras = [camera for camera in cameras if camera.get('id') in requested_ids]
        if not cameras:
            raise HTTPException(400, f"{source_room['label']} 没有可用于生成的人工确认机位")
        for camera_index, camera in enumerate(cameras):
            spatial_plan = (analysis.get('spatial_plans') or {}).get(camera['id'])
            verified_revision = int(analysis.get('verified_revision') or analysis.get('revision') or 0)
            if not spatial_plan or spatial_plan.get('status') != 'locked':
                raise HTTPException(400, f"{source_room['label']} · {camera.get('name') or camera['id']} 尚未锁定空间约束")
            if int(spatial_plan.get('annotation_revision') or -1) != verified_revision:
                raise HTTPException(409, f"{source_room['label']} · {camera.get('name') or camera['id']} 的空间约束已过期，请重新生成")
            view_proxy = (analysis.get('view_proxies') or {}).get(camera['id'])
            if not view_proxy or view_proxy.get('status') != 'confirmed':
                raise HTTPException(400, f"{source_room['label']} · {camera.get('name') or camera['id']} 尚未确认机位灰模")
            if view_proxy.get('aspect_ratio') != req.aspect_ratio:
                raise HTTPException(409, f"{source_room['label']} · {camera.get('name') or camera['id']} 的灰模画幅不是 {req.aspect_ratio}，请重新确认")
            expected_hash = view_proxy_source_hash(
                analysis, source_room, camera, spatial_plan, req.aspect_ratio)
            if view_proxy.get('source_hash') != expected_hash or not os.path.isfile(view_proxy.get('path') or ''):
                raise HTTPException(409, f"{source_room['label']} · {camera.get('name') or camera['id']} 的灰模已过期，请重新确认")
            unit = json.loads(json.dumps(source_room, ensure_ascii=False))
            unit['annotation_room_id'] = source_room['id']
            unit['camera_id'] = camera['id']
            unit['camera'] = camera
            unit['spatial_plan'] = json.loads(json.dumps(spatial_plan, ensure_ascii=False))
            unit['view_proxy'] = json.loads(json.dumps(view_proxy, ensure_ascii=False))
            unit['view_proxy_path'] = view_proxy['path']
            unit['id'] = source_room['id'] if len(cameras) == 1 else f"{source_room['id']}__cam__{camera['id']}"
            if len(cameras) > 1:
                unit['label'] = f"{source_room['label']} · {camera.get('name') or f'机位 {camera_index + 1}'}"
            rooms.append(unit)
    selected = rooms
    suite_id = new_floorplan_id('suite')
    model_keys = req.model_keys
    for room in rooms:
        room['status'] = 'queued' if room.get('selected') else 'skipped'
        room['annotated_path'] = render_room_annotation(
            analysis['floorplan_path'], room,
            accepted_openings(analysis, room.get('annotation_room_id') or room.get('id') or ''), suite_id)
        room['constraint_overlay_path'] = render_spatial_plan_overlay(
            room['annotated_path'], room.get('spatial_plan') or {}, suite_id, room['id'])
        room['candidates'] = _candidate_records(room, req, analysis)
    prices = get_usage_prices()
    total_images = len(selected) * req.candidates_per_room * len(model_keys)
    total_model_calls = total_images * 2
    try:
        per_room_cost = sum(
            float(prices.get('B2' if key == 'b2' else 'Pro', 0) or 0)
            for key in model_keys
        ) * req.candidates_per_room
        estimate = round(len(selected) * per_room_cost * 2, 4)
    except (TypeError, ValueError):
        estimate = 0
    suite = {
        'suite_id': suite_id, 'analysis_id': req.analysis_id, 'status': 'queued',
        'stage': '等待生成', 'error': '', 'warnings': [], 'created_at': time.time(),
        'updated_at': time.time(), 'floorplan_path': analysis['floorplan_path'],
        'floor_path': floor_path, 'style_ref_path': style_ref_path or '',
        'prompt': req.prompt, 'style': req.style, 'lighting': req.lighting,
        'style_brief': f"Use one coherent {req.style} design language under {req.lighting} across every room.",
        # Whole-home anchor mode is paused for newly submitted tasks. Historical
        # consistent suites remain readable and resumable through the old branch.
        'generation_mode': 'fast', 'model_key': model_keys[0], 'model_keys': model_keys,
        'candidates_per_room': req.candidates_per_room, 'aspect_ratio': req.aspect_ratio,
        'resolution': req.resolution, 'rooms': rooms, 'annotation_rooms': annotation_rooms,
        'openings': accepted_openings(analysis), 'orientation': analysis.get('orientation') or '',
        'annotation_revision': int(analysis.get('verified_revision') or analysis.get('revision') or 0),
        'anchor_room_id': '', 'anchor_result_id': '', 'anchor_path': '',
        'estimated_images': total_images, 'estimated_model_calls': total_model_calls,
        'estimated_cost': estimate, 'currency': 'USD',
    }
    _ACTIVE_SUITES[suite_id] = suite
    _SUITE_KEYS[suite_id] = api_key
    _persist_suite(suite)
    state.spawn(_run_suite_bg(suite, api_key))
    return suite_view(suite)


@router.get('/api/floorplan-suites')
def get_floorplan_suites(limit: int = 30):
    disk = {entry['suite_id']: entry for entry in list_suites(limit)}
    disk.update(_ACTIVE_SUITES)
    rows = sorted(disk.values(), key=lambda entry: entry.get('created_at', 0), reverse=True)
    return [suite_view(entry) for entry in rows[:max(1, min(limit, 100))]]


@router.get('/api/floorplan-suites/{suite_id}')
def get_floorplan_suite(suite_id: str):
    suite = _suite_entry(suite_id)
    if not suite:
        raise HTTPException(404, '整屋套图任务不存在')
    return suite_view(suite)


@router.get('/api/floorplan-suites/{suite_id}/stream')
async def stream_floorplan_suite(suite_id: str, request: Request):
    async def generate():
        while True:
            if await request.is_disconnected():
                break
            suite = _suite_entry(suite_id)
            if not suite:
                yield f"event: error\ndata: {json.dumps({'error': 'suite not found'})}\n\n"
                break
            data = json.dumps(suite_view(suite), ensure_ascii=False)
            yield f'data: {data}\n\n'
            if suite.get('status') in ('done', 'partial', 'failed', 'waiting_anchor'):
                yield f'event: done\ndata: {data}\n\n'
                break
            await asyncio.sleep(1)
    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/api/floorplan-suites/{suite_id}/anchor')
async def select_floorplan_anchor(suite_id: str, req: FloorplanAnchorRequest):
    suite = _suite_entry(suite_id)
    if not suite:
        raise HTTPException(404, '整屋套图任务不存在')
    if suite.get('generation_mode') != 'consistent' or suite.get('status') != 'waiting_anchor':
        raise HTTPException(409, '任务当前不在等待主空间确认')
    room, candidate = _find_candidate(suite, req.result_id)
    if not candidate or not candidate.get('path') or room.get('id') != suite.get('anchor_room_id'):
        raise HTTPException(400, '请选择已完成的主空间候选')
    suite.update(
        anchor_result_id=req.result_id, anchor_path=candidate['path'], status='running',
        stage='以主空间为锚点生成其余房间', error='',
    )
    api_key = _SUITE_KEYS.get(suite_id) or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    _ACTIVE_SUITES[suite_id] = suite
    _persist_suite(suite)

    async def continue_bg():
        try:
            remaining = [room for room in suite['rooms']
                         if room.get('selected') and room.get('id') != suite['anchor_room_id']]
            await _generate_rooms(suite, remaining, api_key)
            _suite_finalize(suite)
        except Exception as ex:
            logger.exception(f'[户型套图] 锚点后续生成失败 suite={suite_id}')
            suite.update(status='partial', stage='', error=str(ex))
            _persist_suite(suite)
        finally:
            _ACTIVE_SUITES.pop(suite_id, None)
            _SUITE_KEYS.pop(suite_id, None)
    state.spawn(continue_bg())
    return suite_view(suite)


@router.post('/api/floorplan-suites/{suite_id}/cancel')
def cancel_floorplan_suite(suite_id: str):
    suite = _suite_entry(suite_id)
    if not suite:
        raise HTTPException(404, '整屋套图任务不存在')
    if suite.get('status') not in ('queued', 'running', 'evaluating'):
        raise HTTPException(409, '任务当前不在运行')
    _CANCELLED_SUITES.add(suite_id)
    suite['error'] = '已请求停止；已经生成并计费的图片仍会保留'
    _persist_suite(suite)
    return {'cancelled': True}


@router.post('/api/floorplan-suites/{suite_id}/rooms/{room_id}/retry')
async def retry_floorplan_room(suite_id: str, room_id: str):
    suite = _suite_entry(suite_id)
    if not suite:
        raise HTTPException(404, '整屋套图任务不存在')
    if suite.get('status') not in ('partial', 'failed'):
        raise HTTPException(409, '只有部分失败或失败任务可以重试')
    room = _find_room(suite, room_id)
    if not room or not room.get('selected'):
        raise HTTPException(404, '房间不存在或未选中')
    if all(candidate.get('status') == 'done' for candidate in room.get('candidates') or []):
        return suite_view(suite)
    api_key = _SUITE_KEYS.get(suite_id) or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    suite.update(status='running', stage=f"重试 {room['label']}", error='')
    _ACTIVE_SUITES[suite_id] = suite
    _persist_suite(suite)

    async def retry_bg():
        try:
            await _generate_rooms(suite, [room], api_key, failed_only=True)
            _suite_finalize(suite)
        finally:
            _ACTIVE_SUITES.pop(suite_id, None)
    state.spawn(retry_bg())
    return suite_view(suite)


@router.post('/api/floorplan-suites/{suite_id}/color-match')
async def color_match_floorplan_candidate(suite_id: str, req: SuiteColorMatchRequest):
    """把手动校色结果作为同房间的新候选保留，不覆盖原图。"""
    if req.suite_id != suite_id:
        raise HTTPException(400, '套图任务 ID 不一致')
    suite = _suite_entry(suite_id)
    if not suite:
        raise HTTPException(404, '整屋套图任务不存在')
    if suite.get('status') in ('queued', 'running'):
        raise HTTPException(409, '套图任务进行中，请稍后校色')
    room = _find_room(suite, req.room_id)
    source_room, source = _find_candidate(suite, req.result_id)
    if not room or source_room is not room or not source or not source.get('path'):
        raise HTTPException(400, '该图不属于指定套图房间')
    from .routes_tools import _run_color_match, _save_color_mask
    from .server_helpers import require_output_image_rel
    abs_src = require_output_image_rel(req.image_rel)
    if os.path.realpath(abs_src) != os.path.realpath(source['path']):
        raise HTTPException(400, '校色源图与候选不一致')
    ref_path = require_ref_image_path(req.ref_path or suite['floor_path'])
    result = await asyncio.to_thread(
        _run_color_match, abs_src, ref_path, req.rect, req.strength, req.feather,
        adjustments=req.adjustments, adjustment_mode=req.adjustment_mode,
        scope=req.scope, mask_b64=req.mask_b64, mask_feather=req.mask_feather,
        algorithm=req.algorithm, illumination_mode=req.illumination_mode,
        return_quality_report=True,
    )
    output, quality_report = result
    metadata = {
        'operation': 'color_match', 'scope': req.scope,
        'adjustment_mode': req.adjustment_mode, 'strength': req.strength,
        'mask_feather': req.mask_feather, 'algorithm': req.algorithm,
        'illumination_mode': req.illumination_mode,
        'quality_report': quality_report.to_dict() if quality_report else None,
        'source_result_id': req.result_id,
    }
    if req.scope == 'floor_mask':
        path = await asyncio.to_thread(save_api_result_png, output, '户型套图_局部校色', abs_src, metadata)
        if path:
            try:
                metadata['mask_file'] = await asyncio.to_thread(_save_color_mask, req.mask_b64, path, output.size)
            except Exception as ex:
                logger.warning(f'[户型套图校色] 蒙版留档失败: {ex}')
    else:
        path = await asyncio.to_thread(save_api_result_jpg, output, '户型套图_手动校色', abs_src)
    if not path:
        raise HTTPException(500, '校色结果保存失败')
    api_key = _SUITE_KEYS.get(suite_id) or (load_config().get('gemini_api_key') or '').strip()
    evaluation, eval_error = await asyncio.to_thread(
        evaluate_candidate, api_key, room['annotated_path'], suite['floor_path'], path,
        suite.get('anchor_path')) if api_key else ({
            'status': 'unavailable', 'total': None, 'warnings': ['未配置 Gemini Key，未重新评分'], 'summary': '',
        }, '未配置 Gemini Key')
    candidate = {
        'result_id': f"{room['id']}_color_{uuid.uuid4().hex[:10]}",
        'index': len(room.get('candidates') or []) + 1, 'status': 'done', 'stage': '',
        'error': '', 'path': path, 'model': '手动校色', 'source_result_id': req.result_id,
        'evaluation': evaluation, 'evaluation_error': eval_error or '',
        'auto_color_status': 'manual', 'color_metadata': metadata,
        'analysis_id': suite.get('analysis_id'),
        'annotation_revision': suite.get('annotation_revision', 0),
        'annotation_room_id': room.get('annotation_room_id') or room.get('id'),
        'camera_id': room.get('camera_id') or '', 'review_status': 'unreviewed',
        'review_tags': [], 'review_note': '', 'best': False,
    }
    room.setdefault('candidates', []).append(candidate)
    _room_finalize(room)
    _persist_suite(suite)
    return suite_view(suite)


@router.post('/api/floorplan-suites/{suite_id}/results/{result_id}/review')
def review_floorplan_candidate(suite_id: str, result_id: str, req: SuiteCandidateReviewRequest):
    if req.result_id != result_id:
        raise HTTPException(400, '结果 ID 不一致')
    suite = _suite_entry(suite_id)
    if not suite:
        raise HTTPException(404, '整屋套图任务不存在')
    room = _find_room(suite, req.room_id)
    source_room, candidate = _find_candidate(suite, result_id)
    if not room or source_room is not room or not candidate:
        raise HTTPException(404, '未找到指定房间候选')
    tags = []
    for tag in req.review_tags:
        clean = str(tag).strip()[:40]
        if clean and clean not in tags:
            tags.append(clean)
    if req.best:
        for other_room in suite.get('rooms') or []:
            if (other_room.get('annotation_room_id') or other_room.get('id')) != \
                    (room.get('annotation_room_id') or room.get('id')):
                continue
            for other in other_room.get('candidates') or []:
                other['best'] = False
    candidate.update(
        review_status=req.review_status, review_tags=tags, review_note=req.review_note,
        best=bool(req.best), reviewed_at=time.time(),
    )
    _persist_suite(suite)
    return suite_view(suite)
