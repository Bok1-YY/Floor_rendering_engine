# -*- coding: utf-8 -*-
"""路由共享工具 —— URL 映射、任务卡序列化、路径守卫、上传落盘、导出响应。

无编排状态(注册表/信号量在 server_state);函数体与拆分前逐字一致,仅更名转正。
路径守卫(require_*)全部 realpath+commonpath 归属校验,拒绝 ../ 逃逸。
测试重定向目录时 patch 本模块的 MAIN_OUTPUT_DIR / UPLOAD_DIR。
"""
import os
import uuid
from typing import Optional

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image

from .config import (
    MAIN_OUTPUT_DIR, UPLOAD_DIR, logger,
    load_config, safe_upload_path,
    get_image_provider, get_speed_profile, get_auto_failover, get_auto_color_match_enabled, get_proxy,
    get_tls_verify, get_tls_ca_bundle, get_speed_profile_params,
    get_deepseek_base_url, get_deepseek_model, get_omakase_enabled,
    get_omakase_gemini_model, get_usage_prices, get_pptx_branding,
    get_inpaint_provider, get_comfyui_settings, get_inpaint_models,
)
from .failure_kb import classify_failure
from .models import (
    JobRecord, ensure_model_runs, ensure_candidate_lists,
    job_time_text, running_model_status_text,
)
from .records import safe_output_path


# ── URL 工具（移植自 webui，realpath+commonpath 归属校验，拒绝 ../ 逃逸）──
def to_url(p) -> str:
    if not p:
        return ''
    try:
        rp = os.path.realpath(str(p))
    except Exception:
        return ''
    for base, prefix in ((MAIN_OUTPUT_DIR, '/outputs/'), (UPLOAD_DIR, '/uploads/')):
        try:
            rbase = os.path.realpath(base)
            if os.path.commonpath([rp, rbase]) == rbase:
                return prefix + os.path.relpath(rp, rbase).replace('\\', '/')
        except Exception:
            continue
    return ''


def thumb_url(p: str, s: int = 320) -> str:
    return f'/thumb/uploads/{os.path.basename(p)}?s={s}'


def result_thumb_url(p, s: int = 480) -> str:
    full = to_url(p)
    if not full.startswith('/outputs/'):
        return full
    return f'/thumb/outputs/{full[len("/outputs/"):]}?s={s}'


def job_view(job: JobRecord) -> dict:
    """把 JobRecord 序列化成前端友好的 JSON（含实时阶段、耗时文案、结果图 URL/缩略图、候选下标）。"""
    ensure_model_runs(job)
    ensure_candidate_lists(job)   # 向后兼容 + 同步 *_path = *_paths[*_idx]
    effective_error = job.operation_error or job.error
    runs = {}
    for key in job.model_targets:
        run = job.model_runs.get(key) or {}
        paths = list(run.get('paths') or [])
        idx = max(0, min(int(run.get('index') or 0), len(paths) - 1)) if paths else 0
        current = paths[idx] if paths else ''
        settings = run.get('settings') or {}
        api_original = run.get('base_path') if settings.get('auto_color_match_enabled') else ''
        runs[key] = {
            'key': key,
            'label': run.get('label') or key,
            'provider': run.get('provider') or '',
            'model_id': run.get('model_id') or '',
            'status': run.get('status') or 'queued',
            'stage': run.get('stage') or '',
            'seconds': run.get('seconds'),
            'error': run.get('error') or '',
            'url': to_url(current),
            'thumb': result_thumb_url(current),
            'idx': idx,
            'total': len(paths),
            'base_url': to_url(run.get('base_path')),
            'api_original_url': to_url(api_original),
            'api_original_thumb': result_thumb_url(api_original),
            'auto_color_status': settings.get('auto_color_status') or '',
            'auto_color_error': settings.get('auto_color_error') or '',
            'delivery_status': run.get('delivery_status') or '',
            'seed': run.get('seed'),
            'settings': settings,
        }
    return {
        'job_id': job.job_id,
        'display_name': job.display_name,
        'ts': job.ts,
        'status': job.status,
        'model_filter': job.model_filter,
        'model_targets': job.model_targets,
        'model_runs': runs,
        'workflow_mode': job.workflow_mode,
        'error': job.error,
        'error_kb': classify_failure(effective_error) if (effective_error and '取消' not in effective_error) else None,
        'b2_stage': job.b2_stage,
        'pro_stage': job.pro_stage,
        'b2_secs': job.b2_secs,
        'pro_secs': job.pro_secs,
        'time_text': job_time_text(job),
        'model_status': running_model_status_text(job),
        'pro_polishing': job.pro_polishing,
        'operation': job.operation,
        'operation_status': job.operation_status,
        'operation_error': job.operation_error,
        'has_retry': bool(job.retry_ctx),
        'record_id': job.record_id,
        'json_path': job.json_path,
        # 房间原图 URL（替换类工作流才有 rp）：供前端「前后对比」滑块
        'room_url': to_url((job.retry_ctx or {}).get('rp')),
        # 地板小样（优化图，png_path 随队列持久化）：供前端「手动校色」参照
        'floor_url': to_url(job.png_path),
        'floor_path': job.png_path or '',
        # 结果图：原图走 /outputs，缩略图走 /thumb/outputs（4K 原图大，列表用小图）
        'b2_url': to_url(job.b2_path),
        'b2_thumb': result_thumb_url(job.b2_path),
        'b2_idx': job.b2_idx,
        'b2_total': len(job.b2_paths),
        'pro_url': to_url(job.pro_path),
        'pro_thumb': result_thumb_url(job.pro_path),
        'pro_idx': job.pro_idx,
        'pro_total': len(job.pro_paths),
    }


def config_view() -> dict:
    cfg = load_config()
    brand = get_pptx_branding()
    return {
        'has_gemini_key': bool((cfg.get('gemini_api_key') or '').strip()),
        'has_fal_key': bool((cfg.get('fal_api_key') or '').strip()),
        'image_provider': get_image_provider(),
        'speed_profile': get_speed_profile(),
        'auto_failover': get_auto_failover(),
        'auto_color_match_enabled': get_auto_color_match_enabled(),
        'tls_verify': get_tls_verify(),
        'tls_ca_bundle': get_tls_ca_bundle(),
        'proxy': get_proxy(),
        'fal_queue_proxy': str(cfg.get('fal_queue_proxy') or ''),
        'max_concurrent_per_model': int(cfg.get('max_concurrent_per_model', 1) or 1),
        'sd_enabled': bool(cfg.get('sd_enabled', False)),
        'inpaint_provider': get_inpaint_provider(),
        'inpaint_remove_model': get_inpaint_models()['remove'],
        'inpaint_add_model': get_inpaint_models()['add'],
        'comfyui_base_url': get_comfyui_settings()['base_url'],
        'comfyui_workflow_path': get_comfyui_settings()['workflow_path'],
        'comfyui_timeout': get_comfyui_settings()['timeout'],
        'inpaint_remove_prompt': str(cfg.get('inpaint_remove_prompt') or ''),
        'speed_params': get_speed_profile_params(cfg),
        # Omakase：Gemini 复用主 Key，DeepSeek 只回是否已配置，绝不回明文 key
        'has_deepseek_key': bool((cfg.get('deepseek_api_key') or '').strip()),
        'omakase_enabled': get_omakase_enabled(),
        'omakase_gemini_model': get_omakase_gemini_model(),
        'deepseek_model': get_deepseek_model(),
        'deepseek_base_url': get_deepseek_base_url(),
        'usage_prices': get_usage_prices(),
        'pptx_company': brand['company'],
        'pptx_contact': brand['contact'],
        'pptx_logo_url': to_url(brand['logo_path']),
    }



def require_record_json_path(json_path: str) -> str:
    """Resolve a client-provided record path and keep it inside output_files."""
    if not json_path:
        raise HTTPException(400, '记录路径为空')
    try:
        base = os.path.realpath(MAIN_OUTPUT_DIR)
        path = os.path.realpath(json_path)
        if os.path.commonpath([base, path]) != base:
            raise HTTPException(400, '记录路径不在 output_files 内')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, '记录路径无效')
    if not os.path.basename(path).endswith('_记录.json'):
        raise HTTPException(400, '记录文件名无效')
    if not os.path.exists(path):
        raise HTTPException(404, '记录文件不存在')
    return path


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_PIXELS = 80_000_000


def require_upload_image_path(path: Optional[str], label: str, *, required: bool = False) -> Optional[str]:
    if not path:
        if required:
            raise HTTPException(400, f'{label}不存在，请先上传')
        return None
    try:
        base = os.path.realpath(UPLOAD_DIR)
        resolved = os.path.realpath(path)
        if os.path.commonpath([base, resolved]) != base:
            raise HTTPException(400, f'{label}路径无效，请重新上传')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, f'{label}路径无效，请重新上传')
    if os.path.splitext(resolved)[1].lower() not in IMAGE_EXTS or not os.path.isfile(resolved):
        raise HTTPException(400, f'{label}文件已失效，请重新上传')
    return resolved


def panel_require_second_image(req):
    """墙板模式子行为的第二张图校验：再设计需场景参照图(ref)，替换需原墙板场景图(room)。
    纯原创只需 image_path(已在上游校验)。缺图直接 400，避免静默丢图照常计费。"""
    wf = req.params.workflow_mode or ''
    if '墙板' not in wf:
        return
    sub = req.params.panel_submode or '再设计'
    if '替换' in sub and not (req.room_path and os.path.exists(req.room_path)):
        raise HTTPException(400, '墙板替换：请先上传原墙板场景图')
    if ('再设计' in sub or sub == '') and not (req.ref_path and os.path.exists(req.ref_path)):
        raise HTTPException(400, '墙板再设计：请先上传场景参照图')


def save_upload(file: UploadFile, prefix: str) -> dict:
    dest = safe_upload_path(file.filename or 'upload.jpg', prefix)
    if not dest:
        raise HTTPException(400, '不支持的文件类型（仅 jpg/jpeg/png/webp）')
    tmp = f'{dest}.{uuid.uuid4().hex}.upload'
    total = 0
    try:
        with open(tmp, 'xb') as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, '图片超过 50 MiB 上限')
                out.write(chunk)
        try:
            with Image.open(tmp) as image:
                image.verify()
            with Image.open(tmp) as image:
                if (image.format or '').upper() not in {'JPEG', 'PNG', 'WEBP'}:
                    raise HTTPException(400, '图片真实格式不受支持（仅 JPEG/PNG/WebP）')
                expected = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP'}[
                    os.path.splitext(dest)[1].lower()
                ]
                if (image.format or '').upper() != expected:
                    raise HTTPException(400, '图片扩展名与真实格式不一致')
                if image.width * image.height > MAX_UPLOAD_PIXELS:
                    raise HTTPException(413, '图片像素超过 8000 万上限')
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, '文件不是有效图片或图片已损坏')
        if os.path.exists(dest):
            dest = safe_upload_path(file.filename or 'upload.jpg', prefix)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
    return {'path': dest, 'url': to_url(dest), 'name': os.path.basename(dest), 'thumb': thumb_url(dest)}



def remove_managed_logo(path: str) -> None:
    """Best-effort cleanup for logo uploads owned by this app; never delete arbitrary paths."""
    if not path:
        return
    try:
        rp = os.path.realpath(path)
        base = os.path.realpath(UPLOAD_DIR)
        if (os.path.commonpath([rp, base]) == base
                and os.path.basename(rp).startswith('logo_')
                and os.path.isfile(rp)):
            os.remove(rp)
    except Exception as ex:
        logger.warning(f"[品牌] 清理旧 logo 失败(忽略): {ex}")



def record_color_match_ref_path(json_path: str, record: dict) -> str:
    """解析记录校色的默认小样。

    优先使用队列校色同源的 *_优化图.png；然后兼容新版 gen_context
    和旧版 sample_image_file。所有候选都经参照图白名单校验。
    """
    gc = record.get('gen_context') if isinstance(record, dict) else None
    sample_rel = record.get('sample_image_file') if isinstance(record, dict) else ''
    sample_path = safe_output_path(sample_rel) if sample_rel else ''
    candidates = [
        json_path.replace('_记录.json', '_优化图.png'),
        str((gc or {}).get('image_path') or ''),
        sample_path or '',
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return require_ref_image_path(candidate)
        except HTTPException:
            continue
    return ''



def serve_export(result_msg: str, out_dir: str, media_type: str):
    """records.export_* 写文件并返回 '✅ 已导出：<basename>'；据此定位文件流式下载。"""
    if not result_msg.startswith('✅'):
        raise HTTPException(400, result_msg)
    base = result_msg.split('：', 1)[1].strip() if '：' in result_msg else result_msg
    path = os.path.join(out_dir, base)
    if not os.path.exists(path):
        raise HTTPException(500, f'导出文件未找到: {base}')
    return FileResponse(path, media_type=media_type, filename=base)


PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'


def require_output_image_rel(rel: str) -> str:
    """成图相对路径 → outputs 内绝对路径；越界/不存在 → 400。"""
    ap = safe_output_path(rel or '')
    if not ap or not os.path.isfile(ap):
        raise HTTPException(400, '成图路径无效')
    return ap


def require_ref_image_path(path: str) -> str:
    """参照图绝对路径：realpath 后须落在上传目录或输出目录内（仿 to_url 反逃逸）。"""
    try:
        rp = os.path.realpath(str(path or ''))
    except Exception:
        raise HTTPException(400, '参照图路径无效')
    for base in (UPLOAD_DIR, MAIN_OUTPUT_DIR):
        try:
            rbase = os.path.realpath(base)
            if os.path.commonpath([rp, rbase]) == rbase and os.path.isfile(rp):
                return rp
        except Exception:
            continue
    raise HTTPException(400, '参照图必须是已上传的小样或本程序的输出图')
