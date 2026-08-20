# ==========================================
# Floor Engine — FastAPI app 组装器
# 开发期启动（backend only, 端口 7870）:
#   cd /home/boki/桌面/test
#   python -m Floor_engine_server.server_api
#   或  uvicorn Floor_engine_server.server_api:app --host 127.0.0.1 --port 7870 --workers 1
# 必须单 worker：JOBS 注册表 / 信号量是【进程内】状态，多 worker 不共享。
# ==========================================
"""Headless FastAPI layer over the floor_engine package (no NiceGUI).

本模块只负责组装:lifespan(运行时初始化+启动恢复)、CORS/同源守卫、健康检查、
七个业务路由(routes_*)、静态/缩略图服务与前端静态站挂载。
业务逻辑分布:
  routes_jobs      任务队列 + 全部生图后台协程(4K 主编排在此)
  routes_panorama  纯效果图候选派生单点 360° ERP / 全景复核修缝
  routes_panorama_direct  球面效果图六面图集直出 ERP
  routes_previews  快速预览
  routes_library   上传/记录/收藏/评审/导出/用量
  routes_config    配方/失败库/连通性/配置/Omakase/选项
  routes_tools     识色/地板可视化/全图校色
  routes_inpaint   生成式修补
  routes_floorplans 旧版房间标注兼容接口
  routes_whole_home 整屋统一建模 + 3D 机位 + 约束生成主链路
  floorplan_annotations 人工标注版本/几何验收/训练数据导出
  server_state     注册表/信号量等全部可变状态;server_helpers 共享工具;
  server_schemas   请求模型;image_ops 纯图像处理。
"""
import hashlib
import json
import mimetypes
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import server_state as state
from .config import MAIN_OUTPUT_DIR, UPLOAD_DIR, THUMB_DIR, logger, load_config
from .records import migrate_all_record_storage, load_persisted_jobs, safe_output_path
from .server_helpers import IMAGE_EXTS
from . import (
    routes_jobs, routes_panorama, routes_panorama_direct, routes_panorama_floor, routes_previews, routes_library, routes_config, routes_tools,
    routes_inpaint, routes_floorplans, routes_whole_home,
)
from .floorplan_engine import recover_interrupted_floorplan_state
from .whole_home_engine import load_run, recover_interrupted_whole_home_state
from .whole_home_autopilot import (
    DevelopmentAutopilotError,
    development_autopilot_enabled,
    get_development_session,
    list_development_session_ids,
    reconcile_development_session,
)
from .whole_home_manual import (
    manual_safe_enabled,
    request_feature_for_path,
    service_owner,
)


# ============================================================
# FastAPI app + 生命周期
# ============================================================
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Every service mode owns the authoritative data root before config reads,
    # migrations or recovery.  Manual-safe no longer nests a second owner lock.
    with service_owner(MAIN_OUTPUT_DIR, timeout=0.1):
        try:
            lim = max(1, int(load_config().get('max_concurrent_per_model', 1)))
        except Exception as ex:
            logger.warning(f"读取 max_concurrent_per_model 失败，用默认 1: {ex}")
            lim = 1
        state.init_runtime(lim)   # 按模型信号量 + prep 锁,必须绑定本服务事件循环
        if manual_safe_enabled():
            state.JOBS.replace([])
            logger.info(
                '[server_api] 手动安全模式启动：未迁移、未恢复、未对账；'
                '付费提交需独立 -AllowPaid 开关')
            yield
            return
        migrated = migrate_all_record_storage()
        state.JOBS.replace((j.job_id, j) for j in load_persisted_jobs())
        fp_analyses, fp_suites = recover_interrupted_floorplan_state()
        home_projects, home_runs = recover_interrupted_whole_home_state()
        reconciliation_actions = 0
        if development_autopilot_enabled():
            # Startup is intentionally preview-only.  A controller must present the
            # state_version from this plan to the explicit apply endpoint.
            for session_id in list_development_session_ids():
                try:
                    session = get_development_session(session_id)
                    run_records = {
                        str(run_id): load_run(str(run_id))
                        for run_id in session.get('runs') or []
                    }
                    preview = reconcile_development_session(
                        session_id, run_records, apply=False,
                        expected_state_version=int(session.get('state_version') or 0))
                    reconciliation_actions += len(preview.get('actions') or [])
                    if preview.get('actions'):
                        logger.warning(
                            f'[development_autopilot] {session_id} 启动恢复仅 dry-run: '
                            f'{len(preview.get("actions") or [])} 个待对账动作，未写入真实 session')
                except DevelopmentAutopilotError as ex:
                    logger.warning(f'[development_autopilot] 启动 dry-run 失败 {session_id}: {ex}')
        logger.info(f"[server_api] 启动完成：迁移 {migrated} 个记录文件，恢复 {len(state.JOBS)} 条历史任务，"
                    f"修正旧户型解析 {fp_analyses} / 套图 {fp_suites}，整屋项目 {home_projects} / 任务 {home_runs}，每模型并发 {lim}")
        if reconciliation_actions:
            logger.info(f'[development_autopilot] 共发现 {reconciliation_actions} 个待人工确认的恢复动作')
        yield


app = FastAPI(title="Floor Engine API", version="step1", lifespan=lifespan)

# CORS：开给前端 dev origin。绑 127.0.0.1，本机自用，不放公网。
_ALLOWED_ORIGINS = [o.strip() for o in
                    os.environ.get('FLOOR_API_CORS', 'http://localhost:3000,http://127.0.0.1:3000').split(',')
                    if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def reject_cross_origin_mutations(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
        # 同源豁免：浏览器对一切 POST 都带 Origin 头。生产静态站由本后端同源托管，
        # 同源 mutation 不是跨域攻击面；不豁免会把同源部署的所有写操作全部 403。
        same_origin = origin == f'{request.url.scheme}://{request.url.netloc}'
        if not same_origin and origin not in _ALLOWED_ORIGINS:
            return Response('Forbidden origin', status_code=403)
    request_body = None
    if (request.method not in ('GET', 'HEAD', 'OPTIONS')
            and request.url.path.endswith(
                ('/camera-candidates', '/captures'))):
        try:
            request_body = json.loads((await request.body()).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            request_body = None
    disabled = request_feature_for_path(
        request.url.path, request.method, body=request_body)
    if disabled:
        status_code = 409 if disabled in {
            'ordinary_paid_run', 'continuation', 'qa_retry',
        } else 404
        return JSONResponse({
            'detail': {
                'code': 'whole_home_feature_disabled',
                'feature': disabled,
                'manual_safe': manual_safe_enabled(),
            }
        }, status_code=status_code)
    return await call_next(request)


# ── 健康检查 ──
@app.get('/api/healthz')
def healthz():
    return {'ok': True}


# ── 业务路由 ──
app.include_router(routes_jobs.router)
app.include_router(routes_panorama.router)
app.include_router(routes_panorama_direct.router)
app.include_router(routes_panorama_floor.router)
app.include_router(routes_previews.router)
app.include_router(routes_library.router)
app.include_router(routes_config.router)
app.include_router(routes_tools.router)
app.include_router(routes_inpaint.router)
app.include_router(routes_floorplans.router)
app.include_router(routes_whole_home.router)
# ============================================================
# 静态/缩略图（移植自 webui：懒生成缩略图 + safe_output_path 越界防护）
# ============================================================
@app.get('/thumb/uploads/{name}')
def serve_upload_thumb(name: str, s: int = 320):
    name = os.path.basename(name)   # 挡路径穿越
    src = os.path.join(UPLOAD_DIR, name)
    if os.path.splitext(name)[1].lower() not in IMAGE_EXTS or not os.path.isfile(src):
        return Response(status_code=404)
    s = max(64, min(int(s), 1600))
    try:
        mtime = int(os.path.getmtime(src))
    except OSError:
        return Response(status_code=404)
    stem = os.path.splitext(name)[0]
    cache = os.path.join(THUMB_DIR, f'{stem}__{mtime}__{s}.jpg')
    if not os.path.exists(cache):
        try:
            im = Image.open(src)
            im.draft('RGB', (s, s))
            im = im.convert('RGB')
            im.thumbnail((s, s), Image.Resampling.LANCZOS)
            tmp = cache + '.tmp'
            im.save(tmp, 'JPEG', quality=82)
            os.replace(tmp, cache)
        except Exception as ex:
            logger.warning(f"[缩略图] 生成失败 {name}: {ex}")
            return Response(status_code=415)
    return FileResponse(cache, media_type='image/jpeg')


@app.get('/thumb/outputs/{relpath:path}')
def serve_output_thumb(relpath: str, s: int = 480):
    src = safe_output_path(relpath)   # 越界/不存在 → None
    if not src or os.path.splitext(src)[1].lower() not in IMAGE_EXTS:
        return Response(status_code=404)
    s = max(64, min(int(s), 1600))
    try:
        mtime = int(os.path.getmtime(src))
    except OSError:
        return Response(status_code=404)
    key = hashlib.md5(f'{os.path.realpath(src)}__{mtime}__{s}'.encode('utf-8')).hexdigest()
    cache = os.path.join(THUMB_DIR, f'out_{key}.jpg')
    if not os.path.exists(cache):
        try:
            im = Image.open(src)
            im.draft('RGB', (s, s))
            im = im.convert('RGB')
            im.thumbnail((s, s), Image.Resampling.LANCZOS)
            tmp = cache + '.tmp'
            im.save(tmp, 'JPEG', quality=82)
            os.replace(tmp, cache)
        except Exception as ex:
            logger.warning(f"[结果缩略图] 生成失败 {relpath}: {ex}")
            return Response(status_code=415)
    return FileResponse(cache, media_type='image/jpeg')


@app.get('/outputs/{relpath:path}')
def serve_output_image(relpath: str):
    path = safe_output_path(relpath)
    if not path or os.path.splitext(path)[1].lower() not in IMAGE_EXTS:
        return Response(status_code=404)
    return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')


@app.get('/uploads/{name}')
def serve_upload_image(name: str):
    name = os.path.basename(name)
    path = os.path.realpath(os.path.join(UPLOAD_DIR, name))
    if (os.path.splitext(path)[1].lower() not in IMAGE_EXTS
            or os.path.commonpath([os.path.realpath(UPLOAD_DIR), path]) != os.path.realpath(UPLOAD_DIR)
            or not os.path.isfile(path)):
        return Response(status_code=404)
    return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')


# ============================================================
# 前端静态站（Next.js 静态导出 web/out）——「单一程序」用：
# 后端直接把前端整站挂在 /，做到一个进程、一个端口。
# 本 mount 必须放在最后：/ 是贪婪匹配，注册在最后才不会盖住上面的
# /api、/thumb、/outputs、/uploads。找不到 out/（纯后端开发）时自动跳过。
# ============================================================
def _find_frontend_dir():
    """依次探测前端静态目录，兼容源码运行与 Nuitka onefile 冻结后。"""
    import sys
    cands = []
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, 'web', 'out'))          # 源码/dev 布局
    # Nuitka onefile：数据被 --include-data-dir 释放到解包目录；exe 同级或其内
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0] or sys.executable))
    cands.append(os.path.join(exe_dir, 'web', 'out'))       # out/ 与 exe 同级（非内嵌时）
    cands.append(os.path.join(here, '..', 'web', 'out'))    # 包在子目录时的兜底
    for d in cands:
        if os.path.isfile(os.path.join(d, 'index.html')):
            return os.path.abspath(d)
    return None


class NextStaticExportFiles(StaticFiles):
    """Serve Next static-export RSC page payloads under the URL it requests.

    Next 16 exports ``page/__next.page/__PAGE__.txt`` on disk but client-side
    navigation prefetches ``page/__next.page.__PAGE__.txt``.  A conventional
    file server therefore emits a noisy 404 for every sidebar link even though
    the full HTML page works.  Translate only that exact, same-page pattern;
    arbitrary paths still use StaticFiles' normal traversal protections.
    """

    _FLAT_PAGE_PAYLOAD = re.compile(
        r'^(?P<page>[A-Za-z0-9_-]+)/__next\.(?P=page)\.__PAGE__\.txt$')

    async def get_response(self, path: str, scope):
        original_404_response = None
        original_404_exception = None
        try:
            response = await super().get_response(path, scope)
            if response.status_code != 404:
                return response
            original_404_response = response
        except StarletteHTTPException as ex:
            if ex.status_code != 404:
                raise
            original_404_exception = ex
        # StaticFiles.get_path() uses the host separator; on Windows the live
        # value is ``settings\__next...`` although the request URL uses '/'.
        normalized_path = path.replace(os.sep, '/')
        match = self._FLAT_PAGE_PAYLOAD.fullmatch(normalized_path)
        if not match:
            if original_404_response is not None:
                return original_404_response
            raise original_404_exception
        page = match.group('page')
        return await super().get_response(
            f'{page}/__next.{page}/__PAGE__.txt', scope)


_FRONTEND_DIR = _find_frontend_dir()
if _FRONTEND_DIR:
    # html=True：目录请求回退 index.html，支持前端深链接刷新
    app.mount('/', NextStaticExportFiles(directory=_FRONTEND_DIR, html=True), name='frontend')
    logger.info(f"[前端] 已挂载静态站: {_FRONTEND_DIR}")
else:
    logger.warning("[前端] 未找到 web/out（未构建前端？），仅提供 /api 后端服务")


# ============================================================
# 直接运行入口：python -m Floor_engine_server.server_api （在 test/ 目录下）
# ============================================================
if __name__ == '__main__':
    import uvicorn
    host = os.environ.get('FLOOR_API_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('Floor Engine 当前仅支持本机监听，请使用 FLOOR_API_HOST=127.0.0.1')
    port = int(os.environ.get('FLOOR_API_PORT', '7870'))
    # 传 app 对象 = 单进程单 worker（JOBS 注册表/信号量是进程内状态，必须单 worker）
    uvicorn.run(app, host=host, port=port)
