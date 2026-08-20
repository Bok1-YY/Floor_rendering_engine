# -*- coding: utf-8 -*-
"""资料库路由 —— 上传/历史小样、记录列表与揭示、收藏/评审、导出、用量统计。"""
import hashlib
import os
import time

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .config import MAIN_OUTPUT_DIR, logger, update_config, get_usage_prices, get_pptx_branding
from .exports import export_html_from_json, export_pptx_from_json, export_favorites_pptx
from .records import (
    scan_json_files, load_records_file, get_record_labels, reveal_prompt_fn,
    list_recent_floor_swatches, delete_record_entry, delete_result_image,
    toggle_result_favorite, update_result_review, safe_output_path,
    load_review_summary, collect_review_gallery,
    migrate_record_file, record_file_lock, save_records_file,
)
from .usage_stats import load_usage_summary
from .server_helpers import (
    to_url, thumb_url, result_thumb_url, serve_export, PPTX_MIME,
    require_record_json_path, save_upload, remove_managed_logo,
    record_color_match_ref_path, require_upload_image_path,
)
from .server_schemas import (
    FilmAnalyzeRequest, GeometryAuditReviewRequest, RecordRef, ResultRef, ResultReviewRequest, RevealRequest,
)
from .film_repeat_floor import build_film_contract

router = APIRouter()



@router.post('/api/uploads/floor')
def upload_floor(file: UploadFile = File(...)):
    return save_upload(file, '')


@router.post('/api/uploads/film')
def upload_film(file: UploadFile = File(...)):
    return save_upload(file, 'film_')


@router.post('/api/film/analyze')
def analyze_repeat_film(req: FilmAnalyzeRequest):
    path = require_upload_image_path(req.film_path, '原厂彩膜', required=True)
    params = req.model_dump()
    contract, _ = build_film_contract(path, params, guide_size=512)
    return {
        **contract,
        'guide': 'data:image/png;base64,' + contract['guide_b64'],
    }


@router.post('/api/uploads/room')
def upload_room(file: UploadFile = File(...)):
    return save_upload(file, 'room_')


@router.post('/api/uploads/ref')
def upload_ref(file: UploadFile = File(...)):
    return save_upload(file, 'ref_')



@router.post('/api/uploads/logo')
def upload_logo(file: UploadFile = File(...)):
    """PPTX 品牌 logo：存上传目录（logo_ 前缀已加入小样扫描排除名单）并写入配置。"""
    old_path = get_pptx_branding()['logo_path']
    out = save_upload(file, 'logo_')
    if not update_config({'pptx_logo_path': out['path']}):
        remove_managed_logo(out['path'])
        raise HTTPException(500, 'logo 已上传但配置保存失败，请检查写权限')
    if old_path and os.path.realpath(old_path) != os.path.realpath(out['path']):
        remove_managed_logo(old_path)
    return out


@router.post('/api/uploads/logo/clear')
def clear_logo():
    """清除 PPTX logo 配置，并回收本程序管理的旧 logo 文件。"""
    old_path = get_pptx_branding()['logo_path']
    if not update_config({'pptx_logo_path': ''}):
        raise HTTPException(500, 'logo 配置清除失败，请检查写权限')
    remove_managed_logo(old_path)
    return {'ok': True}


@router.get('/api/swatches/recent')
def recent_swatches(limit: int = 24):
    out = []
    for p in list_recent_floor_swatches(limit):
        out.append({'path': p, 'url': to_url(p), 'name': os.path.basename(p), 'thumb': thumb_url(p)})
    return out


# ── 记录 ──
@router.get('/api/records')
def list_records():
    out = []
    for jp in scan_json_files():
        recs = load_records_file(jp)
        favorite_count = sum(
            1 for rec in recs if isinstance(rec, dict)
            for res in (rec.get('results') or []) if isinstance(res, dict) and res.get('favorite')
        )
        out.append({
            'json_path': jp,
            'labels': get_record_labels(jp, recs),
            'favorite_count': favorite_count,
        })
    return out


def _hydrate_public_records(json_path: str, recs: list[dict]):
    """Attach browser URLs and remove server-only paths/secrets from record payloads."""
    for r in recs:
        audit = r.get('pano_audit') if isinstance(r, dict) else None
        if isinstance(audit, dict):
            # 兼容首批全景审计记录：当时已有 pano_audit，但尚未显式写 projection。
            audit.setdefault('projection', 'equirectangular')
        geometry_audit = r.get('geometry_audit') if isinstance(r, dict) else None
        if isinstance(geometry_audit, dict):
            if (geometry_audit.get('audit_kind') == 'dwg_live_geometry'
                    and (int(geometry_audit.get('schema_version') or 0) < 2
                         or not isinstance((geometry_audit.get('run') or {}).get('camera_contract'), dict))):
                geometry_audit['archived_status'] = geometry_audit.get('status')
                geometry_audit['status'] = 'invalidated'
                geometry_audit['invalidation'] = {
                    'code': 'cad_topdown_coordinate_contract_v1_invalidated',
                    'message': '旧记录未证明天空向地面的 V2 相机方向，保留证据但撤销通过状态',
                    'required_schema': 'DwgLiveGeometryAuditV2',
                }
            for artifact in geometry_audit.get('artifacts') or []:
                if isinstance(artifact, dict):
                    artifact.pop('source_path', None)
        for secret in ('prompt_en', 'prompt_en_pro', '_pe', '_pe_pro', 'sample_image_b64'):
            r.pop(secret, None)
        gc = r.get('gen_context')
        if isinstance(gc, dict):
            if gc.get('room_path'):
                gc['room_url'] = to_url(gc['room_path'])
            if gc.get('image_path'):
                gc['image_url'] = to_url(gc['image_path'])
            if gc.get('floor_path'):
                gc['floor_url'] = to_url(gc['floor_path'])
            if isinstance(gc.get('free_image_paths'), list):
                gc['free_image_urls'] = [to_url(path) for path in gc['free_image_paths']]
        color_ref = record_color_match_ref_path(json_path, r)
        r['color_match_ref_path'] = color_ref
        r['color_match_ref_url'] = to_url(color_ref)
        for res in r.get('results', []) if isinstance(r, dict) else []:
            rel = res.get('result_image_file')
            if rel:
                ap = safe_output_path(rel)
                res['result_url'] = to_url(ap) if ap else ''
                res['result_thumb'] = result_thumb_url(ap) if ap else ''
            res['has_inline'] = bool(res.pop('result_image_b64', None))
    return recs


@router.get('/api/records/geometry-audits')
def list_geometry_audits(limit: int = 40):
    """Return the Plan-to-3D cards in one request instead of an HTTP N+1 fan-out."""
    safe_limit = min(100, max(1, int(limit or 40)))
    rows = []
    for json_path in scan_json_files():
        if not os.path.basename(json_path).startswith('Plan-to-3D_'):
            continue
        recs = load_records_file(json_path)
        audit_entries = [
            row for row in recs
            if isinstance(row, dict) and isinstance(row.get('geometry_audit'), dict)
        ]
        if not audit_entries:
            continue
        _hydrate_public_records(json_path, audit_entries)
        file_view = {
            'json_path': json_path,
            'labels': get_record_labels(json_path, recs),
            'favorite_count': sum(
                1 for record in recs if isinstance(record, dict)
                for result in (record.get('results') or [])
                if isinstance(result, dict) and result.get('favorite')
            ),
        }
        rows.extend({'file': file_view, 'entry': entry} for entry in audit_entries)
    rows.sort(
        key=lambda row: str(row['entry']['geometry_audit'].get('executed_at') or ''),
        reverse=True,
    )
    return rows[:safe_limit]



@router.get('/api/records/load')
def load_records(json_path: str):
    json_path = require_record_json_path(json_path)
    # 旧版 NiceGUI 在选中文件时会执行这个幂等迁移；Next 记录页迁移时漏了。
    # 恢复后，仅内联 base64 的旧图会转为文件引用，从而可以浏览和校色。
    try:
        migrate_record_file(json_path)
    except Exception as ex:
        logger.warning(f'[记录] 旧图文件化失败，继续读取原记录: {json_path} / {ex}')
    recs = load_records_file(json_path)
    # 结果图引用改写成 URL；内联 base64(老记录)不回传大 blob，仅标记 has_inline。
    return _hydrate_public_records(json_path, recs)


def _geometry_audit_record(json_path: str, record_id: str):
    record = next((row for row in load_records_file(json_path)
                   if isinstance(row, dict) and row.get('id') == record_id), None)
    audit = record.get('geometry_audit') if isinstance(record, dict) else None
    if not isinstance(audit, dict):
        raise HTTPException(404, '未找到几何验收历史记录')
    return record, audit


@router.get('/api/records/geometry-audit/artifact')
def geometry_audit_artifact(json_path: str, record_id: str, artifact_id: str):
    """Download one checksum-verified file registered by an immutable audit record."""
    json_path = require_record_json_path(json_path)
    _, audit = _geometry_audit_record(json_path, record_id)
    artifact = next((row for row in audit.get('artifacts', [])
                     if isinstance(row, dict) and row.get('artifact_id') == artifact_id), None)
    if not artifact or not artifact.get('available') or not artifact.get('relative_path'):
        raise HTTPException(404, '验收证据文件不可用')
    path = safe_output_path(str(artifact['relative_path']))
    if not path:
        raise HTTPException(404, '验收证据文件不存在')
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != str(artifact.get('sha256') or ''):
        raise HTTPException(409, {
            'code': 'geometry_audit_artifact_hash_mismatch',
            'message': '验收证据文件校验失败，禁止下载',
        })
    return FileResponse(
        path, media_type=str(artifact.get('media_type') or 'application/octet-stream'),
        filename=str(artifact.get('file_name') or os.path.basename(path)),
    )


@router.post('/api/records/geometry-audit/review')
def review_geometry_audit(req: GeometryAuditReviewRequest):
    """Persist human checklist progress without mutating immutable evidence/hash fields."""
    json_path = require_record_json_path(req.json_path)
    with record_file_lock(json_path):
        records = load_records_file(json_path)
        record = next((row for row in records
                       if isinstance(row, dict) and row.get('id') == req.record_id), None)
        audit = record.get('geometry_audit') if isinstance(record, dict) else None
        if not isinstance(audit, dict):
            raise HTTPException(404, '未找到几何验收历史记录')
        allowed = {
            str(metric.get('metric_id'))
            for channel in audit.get('channels', []) if isinstance(channel, dict)
            for metric in channel.get('metrics', []) if isinstance(metric, dict) and metric.get('metric_id')
        }
        checked = []
        for metric_id in req.checked_metric_ids:
            metric_id = str(metric_id).strip()
            if metric_id not in allowed:
                raise HTTPException(400, f'未知验收指标: {metric_id}')
            if metric_id not in checked:
                checked.append(metric_id)
        review = {
            'checked_metric_ids': checked,
            'reviewer': req.reviewer.strip(),
            'note': req.note.strip(),
            'reviewed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        audit['review'] = review
        save_records_file(json_path, records)
    return {
        **review, 'checked_count': len(checked), 'metric_count': len(allowed),
        'complete': bool(allowed) and len(checked) == len(allowed),
    }


@router.post('/api/records/reveal')
def reveal(req: RevealRequest):
    json_path = require_record_json_path(req.json_path)
    text = reveal_prompt_fn(json_path, req.record_id, req.password)
    ok = not (text.startswith('🔒') or text.startswith('❌'))
    return {'text': text, 'ok': ok}


# ── 记录管理：删除结果 / 删除记录 / 收藏结果 ──
def _reject_immutable_audit_mutation(json_path: str, record_id: str) -> None:
    record = next((row for row in load_records_file(json_path)
                   if isinstance(row, dict) and row.get('id') == record_id), None)
    if record and record.get('immutable_audit'):
        raise HTTPException(409, {
            'code': 'immutable_audit_record',
            'message': '全景门禁审计记录只允许查看、下载和收藏',
        })


@router.post('/api/records/result/delete')
def delete_result(req: ResultRef):
    json_path = require_record_json_path(req.json_path)
    _reject_immutable_audit_mutation(json_path, req.record_id)
    if not delete_result_image(json_path, req.record_id, req.result_id):
        raise HTTPException(404, '未找到该效果图')
    return {'ok': True}


@router.post('/api/records/result/favorite')
def favorite_result(req: ResultRef):
    json_path = require_record_json_path(req.json_path)
    new = toggle_result_favorite(json_path, req.record_id, req.result_id)
    if new is None:
        raise HTTPException(404, '未找到该效果图')
    return {'favorite': new}


@router.post('/api/records/result/review')
def review_result(req: ResultReviewRequest):
    json_path = require_record_json_path(req.json_path)
    _reject_immutable_audit_mutation(json_path, req.record_id)
    new = update_result_review(
        json_path, req.record_id, req.result_id,
        review_status=req.review_status,
        review_tags=req.review_tags,
        review_note=req.review_note,
        best=req.best,
    )
    if new is None:
        raise HTTPException(404, '未找到该效果图')
    return new


@router.post('/api/records/delete')
def delete_record(req: RecordRef):
    json_path = require_record_json_path(req.json_path)
    _reject_immutable_audit_mutation(json_path, req.record_id)
    if not delete_record_entry(json_path, req.record_id):
        raise HTTPException(404, '未找到该记录')
    return {'ok': True}


# ── 导出：HTML / PPTX / 收藏夹PPTX ──
@router.get('/api/records/export/html')
def export_html(json_path: str):
    json_path = require_record_json_path(json_path)
    return serve_export(export_html_from_json(json_path), os.path.dirname(json_path), 'text/html')


@router.get('/api/records/export/pptx')
def export_pptx(json_path: str):
    json_path = require_record_json_path(json_path)
    return serve_export(export_pptx_from_json(json_path), os.path.dirname(json_path), PPTX_MIME)


@router.get('/api/records/export/favorites-pptx')
def export_favorites():
    return serve_export(export_favorites_pptx(), MAIN_OUTPUT_DIR, PPTX_MIME)



# ── 用量统计（cost=按配置单价 × 成功张数的估算，未配单价的行为 None）──
@router.get('/api/usage')
def usage():
    return load_usage_summary(get_usage_prices())


# ── 评审复盘：聚合统计 + 好图样本库（均只读，不碰 JOBS 队列）──
@router.get('/api/review/summary')
def review_summary():
    return load_review_summary()


@router.get('/api/review/gallery')
def review_gallery(filter: str = 'pass', limit: int = 60):
    if filter not in ('pass', 'best'):
        raise HTTPException(400, "filter 仅支持 pass / best")
    out = []
    for it in collect_review_gallery(filter, limit):
        res = it['res']
        rel = res.get('result_image_file')
        ap = safe_output_path(rel) if rel else ''
        out.append({
            'json_path': it['json_path'],
            'material': it['material'],
            'record_id': it['record_id'],
            'result_id': it['result_id'],
            'style': it['style'],
            'room_type': it['room_type'],
            'workflow_mode': it['workflow_mode'],
            'model_label': res.get('model_label', ''),
            'result_timestamp': res.get('result_timestamp', ''),
            'review_status': res.get('review_status', 'unreviewed'),
            'review_tags': res.get('review_tags') or [],
            'review_note': res.get('review_note', ''),
            'best': bool(res.get('best')),
            'result_url': to_url(ap) if ap else '',
            'result_thumb': result_thumb_url(ap) if ap else '',
        })
    return out
