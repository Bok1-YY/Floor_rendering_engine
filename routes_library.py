# -*- coding: utf-8 -*-
"""资料库路由 —— 上传/历史小样、记录列表与揭示、收藏/评审、导出、用量统计。"""
import os

from fastapi import APIRouter, File, HTTPException, UploadFile

from .config import MAIN_OUTPUT_DIR, logger, update_config, get_usage_prices, get_pptx_branding
from .exports import export_html_from_json, export_pptx_from_json, export_favorites_pptx
from .records import (
    scan_json_files, load_records_file, get_record_labels, reveal_prompt_fn,
    list_recent_floor_swatches, delete_record_entry, delete_result_image,
    toggle_result_favorite, update_result_review, safe_output_path,
    load_review_summary, collect_review_gallery,
    migrate_record_file,
)
from .usage_stats import load_usage_summary
from .server_helpers import (
    to_url, thumb_url, result_thumb_url, serve_export, PPTX_MIME,
    require_record_json_path, save_upload, remove_managed_logo,
    record_color_match_ref_path, require_upload_image_path,
)
from .server_schemas import FilmAnalyzeRequest, RecordRef, ResultRef, ResultReviewRequest, RevealRequest
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
    contract, _ = build_film_contract(path, req.model_dump(), guide_size=512)
    return {**contract, 'guide': 'data:image/png;base64,' + contract['guide_b64']}


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
    for r in recs:
        for secret in ('prompt_en', 'prompt_en_pro', '_pe', '_pe_pro', 'sample_image_b64'):
            r.pop(secret, None)
        pano_audit = r.get('pano_audit') if isinstance(r, dict) else None
        if isinstance(pano_audit, dict):
            pano_audit.setdefault('projection', 'equirectangular')
            pano_audit.setdefault('erp_width', 3840)
            pano_audit.setdefault('erp_height', 1920)
        gc = r.get('gen_context')
        if isinstance(gc, dict):
            if gc.get('room_path'):
                gc['room_url'] = to_url(gc['room_path'])
            if gc.get('image_path'):
                gc['image_url'] = to_url(gc['image_path'])
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
            'message': '历史全景审计记录只允许查看和下载',
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
    _reject_immutable_audit_mutation(json_path, req.record_id)
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
