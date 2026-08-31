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
业务路由(routes_*)、静态/缩略图服务与前端静态站挂载。
业务逻辑分布:
  routes_jobs      任务队列 + 全部生图后台协程(4K 主编排在此)
  routes_previews  快速预览
  routes_library   上传/记录/收藏/评审/导出/用量
  routes_config    配方/失败库/连通性/配置/Omakase/选项
  routes_tools     识色/地板可视化/全图校色
  routes_inpaint   生成式修补
  routes_whole_home_design 户型图 → 正俯视 2.5D 全屋设计 → Blender 任务包
  server_state     注册表/信号量等全部可变状态;server_helpers 共享工具;
  server_schemas   请求模型;image_ops 纯图像处理。
"""
import mimetypes
import os
import re
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import server_state as state
from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR, UPLOAD_DIR, THUMB_DIR, logger, load_config,
    migrate_plaintext_secrets,
)
from .records import migrate_all_record_storage, load_persisted_jobs, safe_output_path
from .server_helpers import IMAGE_EXTS
from .storage_assets import output_thumb_cache_path, purge_output_thumbnails
from . import (
    routes_jobs, routes_previews, routes_library, routes_config, routes_tools,
    routes_inpaint, routes_whole_home_design,
)


# ============================================================
# FastAPI app + 生命周期
# ============================================================
@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate_plaintext_secrets()
    try:
        lim = max(1, int(load_config().get('max_concurrent_per_model', 1)))
    except Exception as ex:
        logger.warning(f"读取 max_concurrent_per_model 失败，用默认 1: {ex}")
        lim = 1
    state.init_runtime(lim)
    migrated = migrate_all_record_storage()
    state.JOBS.replace((j.job_id, j) for j in load_persisted_jobs())
    resumed = routes_whole_home_design.recover_background_tasks()
    logger.info(
        f"[server_api] 启动完成：迁移 {migrated} 个记录文件，恢复 {len(state.JOBS)} 条历史任务，"
        f"恢复 {resumed} 个已有 Fal 全屋设计请求，每模型并发 {lim}，数据目录={BASE_DIR}")
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
    return await call_next(request)


# ── 健康检查 ──
@app.get('/api/healthz')
def healthz():
    return {'ok': True}


# ── 业务路由 ──
app.include_router(routes_jobs.router)
app.include_router(routes_previews.router)
app.include_router(routes_library.router)
app.include_router(routes_config.router)
app.include_router(routes_tools.router)
app.include_router(routes_inpaint.router)
app.include_router(routes_whole_home_design.router)
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
            fd, tmp = tempfile.mkstemp(prefix='.thumb_', suffix='.jpg', dir=THUMB_DIR)
            os.close(fd)
            try:
                im.save(tmp, 'JPEG', quality=82)
                os.replace(tmp, cache)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
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
        cache = output_thumb_cache_path(src, s, THUMB_DIR)
    except OSError:
        return Response(status_code=404)
    if not os.path.exists(cache):
        try:
            im = Image.open(src)
            im.draft('RGB', (s, s))
            im = im.convert('RGB')
            im.thumbnail((s, s), Image.Resampling.LANCZOS)
            fd, tmp = tempfile.mkstemp(prefix='.thumb_', suffix='.jpg', dir=THUMB_DIR)
            os.close(fd)
            try:
                im.save(tmp, 'JPEG', quality=82)
                os.replace(tmp, cache)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            purge_output_thumbnails(src, THUMB_DIR, keep=cache)
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
