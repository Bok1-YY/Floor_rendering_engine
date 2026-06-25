import os
import time
import base64
import hashlib
import io as _io_mod
import threading as _threading
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List

from PIL import Image

from .config import (
    MAIN_OUTPUT_DIR, UPLOAD_DIR, THUMB_DIR,
    GEMINI_MODEL_MAP,
    _build_theme_css,
    logger, _short_text, _load_config, save_api_key,
    save_provider_settings, get_speed_profile, save_speed_profile,
    save_auto_failover, get_image_provider,
    extract_clean_prompt,
    get_bevel_ref_image, safe_upload_path,
)
from .api import (
    call_gemini_edit, call_image_generate,
    analyze_style_image, test_connection,
    FLOOR_DESEAM_INSTRUCTION,
    _match_color_to_reference,
    _infer_aspect_ratio_from_b64,
)
from .prompt_data import (
    FLOOR_TONES, FLOOR_SIZES,
    LIGHTINGS, ANGLES,
    ROOM_TYPES, PROPERTY_TYPES, VIEWS,
    CONTINENTS, LOCATION_MAP,
    PET_TYPES, PET_ACTIONS, PET_FOCUS_OPTIONS,
    MARKET_FURNITURE_CHOICES,
    AVOID_LIST,
    CN_DEVELOPERS, CN_CITIES, CN_TIERS,
    CN_UNIT_TYPES, CN_ROOM_TYPES,
    CN_DELIVERY_CHOICES,
    CN_SPACE_FEATURES, CN_FACILITIES,
    analyze_floor_tone, get_style_choices,
)
from .prompts import (
    save_task_files_html,
)
from .records import (
    _b64_to_pil, _load_records,
    _delete_record, _delete_result_image,
    scan_json_files, get_record_labels, export_html_from_json, migrate_record_file,
    append_result_to_log, append_edited_result_to_record,
    _save_api_result_jpg, _api_write_to_record,
    reveal_prompt_fn,
    toggle_result_favorite, collect_favorites,
    export_pptx_from_json, export_favorites_pptx,
    record_usage, load_usage_summary,
    persist_jobs, load_persisted_jobs, _list_recent_floor_swatches,
    room_type_counts, _safe_output_path,
)
from .models import (JobRecord, new_job, update_job, compute_final_status, job_time_text,
                     running_model_status_text,
                     CANDIDATE_SLOTS, add_candidate, nav_candidate, ensure_candidate_lists)
from .failure_kb import classify_failure, FAILURE_RULES
from .recipes import FLOOR_RECIPES, recommend_recipes, pick_option_key

from nicegui import ui, app, events as ng_events, background_tasks, context
from fastapi.responses import FileResponse, Response

# UPLOAD_DIR / THUMB_DIR 已下沉到 config.py（webui/records 共用），此处直接 import 使用。
app.add_static_files('/outputs', MAIN_OUTPUT_DIR)
app.add_static_files('/uploads', UPLOAD_DIR)


@app.get('/thumb/uploads/{name}')
def _serve_upload_thumb(name: str, s: int = 320):
    """懒生成并缓存 _ng_uploads/<name> 的小缩略图(JPEG)。
    历史小样/地板预览只显示几十~几百 px，没必要拉几十 MB 的 8K 原图。
    同步 def → Starlette 自动丢线程池，不阻塞事件循环。缓存名带源图 mtime，源图换了自动失效。"""
    name = os.path.basename(name)  # 挡路径穿越
    src = os.path.join(UPLOAD_DIR, name)
    if not os.path.isfile(src):
        return Response(status_code=404)
    s = max(64, min(int(s), 1600))  # 夹到合理区间
    try:
        mtime = int(os.path.getmtime(src))
    except OSError:
        return Response(status_code=404)
    stem = os.path.splitext(name)[0]
    cache = os.path.join(THUMB_DIR, f'{stem}__{mtime}__{s}.jpg')
    if not os.path.exists(cache):
        try:
            im = Image.open(src)
            im.draft('RGB', (s, s))  # ★ 让 JPEG 解码阶段就降采样，8K 图解码瞬间完成
            im = im.convert('RGB')
            im.thumbnail((s, s), Image.Resampling.LANCZOS)
            tmp = cache + '.tmp'
            im.save(tmp, 'JPEG', quality=82)
            os.replace(tmp, cache)  # 原子写，防并发半成品
        except Exception as ex:
            logger.warning(f"[缩略图] 生成失败，回退原图 {name}: {ex}")
            return FileResponse(src)  # 永不白屏
    return FileResponse(cache, media_type='image/jpeg')


def _thumb_url(p: str, s: int = 320) -> str:
    """把一张 _ng_uploads 里的图转成缩略图 URL(只用于预览展示，原图路径另存)。"""
    return f'/thumb/uploads/{os.path.basename(p)}?s={s}'


@app.get('/thumb/outputs/{relpath:path}')
def _serve_output_thumb(relpath: str, s: int = 480):
    """懒生成并缓存结果图(output_files 下)的缩略图(JPEG)。
    卡片/记录列表只显示 ≤260px，没必要让浏览器把整张 4K 解成 ~67MB 位图——一屏多张就吃爆内存。
    原图仍走 /outputs/ 供放大/下载。_safe_output_path 复用 records 的越界校验，挡路径穿越。"""
    src = _safe_output_path(relpath)   # 越界/不存在 → None
    if not src:
        return Response(status_code=404)
    s = max(64, min(int(s), 1600))
    try:
        mtime = int(os.path.getmtime(src))
    except OSError:
        return Response(status_code=404)
    # 缓存名用 realpath+mtime+s 的 md5：结果图在子目录里、名字可能含中文/重名，扁平 THUMB_DIR 下须避免碰撞。
    key = hashlib.md5(f'{os.path.realpath(src)}__{mtime}__{s}'.encode('utf-8')).hexdigest()
    cache = os.path.join(THUMB_DIR, f'out_{key}.jpg')
    if not os.path.exists(cache):
        try:
            im = Image.open(src)
            im.draft('RGB', (s, s))  # JPEG 解码阶段就降采样，4K 图解码瞬间完成、不进内存
            im = im.convert('RGB')
            im.thumbnail((s, s), Image.Resampling.LANCZOS)
            tmp = cache + '.tmp'
            im.save(tmp, 'JPEG', quality=82)
            os.replace(tmp, cache)
        except Exception as ex:
            logger.warning(f"[结果缩略图] 生成失败，回退原图 {relpath}: {ex}")
            return FileResponse(src)  # 永不白屏
    return FileResponse(cache, media_type='image/jpeg')


def _result_thumb_url(p: str, s: int = 480) -> str:
    """结果图(output_files 下)绝对路径 → 缩略图 URL；非 output 路径/空值回退原图 URL。"""
    full = _to_url(p)   # '/outputs/<relpath>' 或 ''（已做越界校验）
    if not full.startswith('/outputs/'):
        return full
    return f'/thumb/outputs/{full[len("/outputs/"):]}?s={s}'

def _to_url(p: str) -> str:
    # 用 realpath+commonpath 判断路径归属（替代脆弱的子串匹配）：路径必须真正落在
    # output_files / _ng_uploads 目录内才生成 URL，拒绝 ../ 逃逸到目录外的路径。
    if not p: return ''
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


# _list_recent_floor_swatches 已下沉到 records.py（纯目录扫描），此处直接 import 使用。

_job_history: List[JobRecord] = []
_job_lock = _threading.Lock()
# 常驻卡片上限：会话里跑得越多，_job_ui(NiceGUI 元素引用)+ 浏览器 DOM/解码的大图越积越多。
# 超出时自动删最旧的「已完成」卡(running/pending 永不删)，被删任务仍在磁盘记录里可查。
_MAX_RESIDENT_CARDS = 30
# 并发：每个模型最多 max_concurrent_per_model(默认 1) 个进行中任务（B2、Pro 各一把信号量）。
# 占槽已下沉到 _generate_one_model 内【按模型】各自获取/释放：B2 跑完即放 B2 槽，不等 Pro →
# 下一个队列任务的同名模型立刻起跑（跨任务流水线）。任意时刻仍 ≤1 B2 + ≤1 Pro 在生成(≤2 张)，
# 只是这两张可来自不同任务。磨缝(仅 Pro)也占 Pro 槽 → 与 Pro 生成互斥、自动排队。
_b2_semaphore: Optional[asyncio.Semaphore] = None
_pro_semaphore: Optional[asyncio.Semaphore] = None
# 任务 prep 串行锁：去掉「整任务占两槽」后多个队列任务的 prep 会并发，而 save_task_files_html 按
# 小样路径派生 png/json 输出路径(同张小样的任务共享同一路径) → 并发首次处理同图会抢写同一个 png。
# 故 _run_job 的 save_task_files_html 调用串行化(同图处理过即命中缓存秒回，开销近 0)。
_task_prep_lock: Optional[asyncio.Lock] = None


@asynccontextmanager
async def _acquire_model_slots(run_b2: bool, run_pro: bool):
    """[结构占位] 模型并发槽已下沉到 _generate_one_model 内【按模型】获取/释放(B2 跑完即放、不等 Pro)，
    本上下文不再获取任何槽——保留它只为不重排 _run_job/_retry_job/_regen_job 三处调用的缩进。
    若在此再获取槽，会与 _generate_one_model 内的获取双重占用同一信号量(Semaphore(1)) → 自锁死。"""
    yield
# 每个 job 的所有 UI 元素引用合并到一个 dict（原先散在 7 个并行 dict，加键时要同步 7 处、易错位）。
# job_id → {card, status_lbl, err_lbl, err_row, time_lbl, img_row, b2_img, b2_dl, pro_img, pro_dl,
#           pro_polish_btn, single_model,
#           stop_btn, retry_btn, regen_btn, regenN_sel, regen_pod, batch_btn}
_job_ui: dict = {}
_regen_batches: dict = {}   # src_job_id → {'cancel':bool,'i':int,'n':int,'running':bool,'gen0':int} 逐张多抽批次的易失运行态（运行态，非 UI 引用，单独保留）

# ── 队列持久化：序列化/恢复逻辑已下沉到 records.py（纯文件 IO）──────────────
# 这里只保留薄包装：加锁取 _job_history 快照后交给 records.persist_jobs。
def _persist_jobs():
    with _job_lock:
        jobs = list(_job_history)
    persist_jobs(jobs)

_job_history.extend(load_persisted_jobs())

def _new_job(display_name, ts, model_filter='both') -> JobRecord:
    job = new_job(display_name, ts, model_filter)   # JobRecord 构造 + job_id 生成已下沉到 models.new_job
    with _job_lock: _job_history.insert(0, job)     # 入队仍由 webui 持有（_job_history 所有权不动）
    # UI card is created by _add_job_card() called from the event loop in _run_job
    return job


def _auto_trim_cards():
    """会话内自动收口：常驻卡片超 _MAX_RESIDENT_CARDS 时，删最旧的「已完成」卡。
    复用 _clear_done 的删卡步骤(取消多抽批次 → pop _regen_batches → pop _job_ui + card.delete())。
    只删 done/partial/failed，running/pending 永不删；被删任务仍在磁盘记录里(记录管理可查)。
    须在有 client 上下文时调用(card.delete() 是 UI 操作)——调用点在 _validate_and_submit(新卡创建后)。"""
    try:
        if len(_job_history) <= _MAX_RESIDENT_CARDS:
            return
        surplus = _job_history[_MAX_RESIDENT_CARDS:]        # 最旧的越界部分(newest-first，尾部最旧)
        evict_ids = {j.job_id for j in surplus if j.status in ('done', 'partial', 'failed')}
        if not evict_ids:
            return
        for jid in evict_ids:
            _b = _regen_batches.get(jid)
            if _b: _b['cancel'] = True
            _regen_batches.pop(jid, None)
            refs = _job_ui.pop(jid, None)
            if refs:
                try: refs['card'].delete()
                except Exception as ex: logger.warning(f"自动收口删除卡片失败 job={jid}: {ex}")
        with _job_lock:
            _job_history[:] = [j for j in _job_history if j.job_id not in evict_ids]
        _persist_jobs()
        logger.info(f"[自动收口] 清理 {len(evict_ids)} 张最旧已完成卡，常驻保留 ≤{_MAX_RESIDENT_CARDS}")
    except Exception as ex:
        logger.warning(f"[自动收口] 异常(忽略): {ex}")

# _update_job 下沉到 models.update_job；保留同名别名，调用点(18 处)不变。
_update_job = update_job

# 🔍 安全解包新旧 NiceGUI 版本里的上传数据
async def _extract_upload_data(e: ng_events.UploadEventArguments):
    try:
        if hasattr(e, 'file'):
            name = getattr(e.file, 'name', f"upload_{int(time.time())}.jpg")
            content = e.file.read()
            if asyncio.iscoroutine(content): content = await content
            return name, content
        name = getattr(e, 'name', f"upload_{int(time.time())}.jpg")
        if hasattr(e, 'content'):
            content = e.content.read()
            if asyncio.iscoroutine(content): content = await content
            return name, content
        return name, b""
    except Exception as ex:
        logger.exception(f"提取上传文件失败: {ex}")
        return f"upload_{int(time.time())}.jpg", b""

@ui.page('/')
async def main_page():
    global _b2_semaphore, _pro_semaphore, _task_prep_lock
    if _b2_semaphore is None:
        try: _lim = max(1, int(_load_config().get('max_concurrent_per_model', 1)))
        except Exception as ex:
            logger.warning(f"读取 max_concurrent_per_model 配置失败，用默认 1: {ex}")
            _lim = 1
        _b2_semaphore = asyncio.Semaphore(_lim)
        _pro_semaphore = asyncio.Semaphore(_lim)
    if _task_prep_lock is None:   # 与信号量一样惰性建于事件循环内，避免绑错 loop
        _task_prep_lock = asyncio.Lock()

    # ── 主题：固定「Anthropic 暖陶米色」（唯一主题，浅色；字体走系统微软雅黑、完全离线）──
    ui.add_head_html(f'<style id="theme-style">{_build_theme_css("Anthropic 暖陶米色")}</style>')
    ui.dark_mode().disable()
    # Quasar 原生配件强调色 → 陶土/teal/绿/红（一举消灭"满屏橙"：select 聚焦/checkbox/toggle/tab/uploader 全跟随）
    ui.colors(primary='#c15f3c', secondary='#2e8c7e', accent='#a8472a', positive='#5b9b6b', negative='#b5503a')
    # ── 设计稿（图2）专属视觉：胶囊 / 分段 / hero / 上传 / 生成钮 / 上下文卡 ──
    ui.add_head_html('''<style id="design-style">
.sec-head { font-size:11px; font-weight:700; color:#A8472A; letter-spacing:0.04em; }
/* 工作流胶囊（applied to ui.button）*/
.wf-pill.q-btn { border-radius:9px !important; border:1px solid #E3DED1 !important; background:#ffffff !important; color:#6B6356 !important; box-shadow:none !important; min-height:44px; text-transform:none; font-weight:600; }
.wf-pill.q-btn .q-btn__content { font-size:12.5px; color:#6B6356; }
.wf-pill--on.q-btn { background:#C15F3C !important; border-color:#C15F3C !important; box-shadow:0 1px 3px rgba(193,95,60,0.25) !important; }
.wf-pill--on.q-btn .q-btn__content { color:#ffffff !important; }
/* 市场分段（ui.tabs → 填充胶囊）*/
.market-seg.q-tabs { background:#EDE8DC; border-radius:9px; padding:3px; min-height:0; }
.market-seg .q-tabs__content { gap:3px; }
.market-seg .q-tab { flex:1 1 0; border-radius:7px; min-height:32px; padding:4px 8px; color:#6B6356 !important; font-weight:600; font-size:12.5px; text-transform:none; opacity:1 !important; }
.market-seg .q-tab--active { background:#C15F3C; box-shadow:0 1px 3px rgba(193,95,60,0.25); }
.market-seg .q-tab--active .q-tab__label, .market-seg .q-tab--active { color:#ffffff !important; }
.market-seg .q-tab__indicator { display:none !important; }
/* 市场 tab-panels 透明（去掉白卡，字段直接落在面板底上）*/
.market-panels.q-tab-panels, .market-panels .q-tab-panel { background:transparent !important; }
.market-panels .q-tab-panel { padding:0 !important; }
/* 模型分段（ui.toggle → q-btn-toggle 轨道；等分铺满）*/
.seg-toggle.q-btn-group { background:#EDE8DC; border-radius:9px; padding:3px; box-shadow:none !important; display:flex !important; width:100%; }
.seg-toggle.q-btn-group > .q-btn { flex:1 1 0 !important; border-radius:7px !important; color:#6B6356 !important; font-weight:600; min-height:32px; text-transform:none; }
.seg-toggle.q-btn-group > .q-btn .q-btn__content { font-size:12.5px; }
.seg-toggle.q-btn-group > .q-btn.q-btn--active, .seg-toggle.q-btn-group > .q-btn[aria-pressed="true"] { background:#C15F3C !important; box-shadow:0 1px 3px rgba(193,95,60,0.25) !important; }
.seg-toggle.q-btn-group > .q-btn.q-btn--active .q-btn__content, .seg-toggle.q-btn-group > .q-btn[aria-pressed="true"] .q-btn__content { color:#ffffff !important; }
/* 地板 hero 卡片 + 识别 chip */
.hero-card { display:flex; gap:10px; padding:10px; background:#ffffff; border:1px solid #E3DED1; border-radius:11px; align-items:center; }
.hero-card .q-img { flex:0 0 88px !important; width:88px !important; height:66px !important; border-radius:8px; }
.hero-card img { object-fit:cover; }
.tone-chip { display:block; }
/* 地板上传：驯化成一条虚线"更换/拖拽上传" */
.floor-upload.q-uploader { box-shadow:none !important; background:#FBF6EF !important; border:1.5px dashed #C9A88F; border-radius:9px; width:100%; max-height:none; }
.floor-upload .q-uploader__list { display:none !important; }
.floor-upload .q-uploader__header { background:transparent !important; min-height:42px; }
.floor-upload .q-uploader__header .q-btn, .floor-upload .q-uploader__title { color:#A8472A !important; font-weight:600; font-size:12px; }
.floor-upload .q-uploader__subtitle { display:none !important; }
.floor-upload .q-uploader__header .q-uploader__spinner { display:none; }
/* 上下文上传（待替换房间 / 参照图）：同款虚线区，跟随卡片配色 */
.ctx-upload.q-uploader { box-shadow:none !important; background:#ffffff !important; border:1.5px dashed #B7C9D6; border-radius:9px; width:100%; max-height:none; }
.ctx-upload .q-uploader__list { display:none !important; }
.ctx-upload .q-uploader__header { background:transparent !important; min-height:46px; }
.ctx-upload .q-uploader__subtitle { display:none !important; }
.ctx-upload .q-uploader__header .q-uploader__spinner { display:none; }
.ctx-upload .q-uploader__header .q-btn, .ctx-upload .q-uploader__title { font-weight:600; font-size:12px; }
.room-upload.q-uploader { border-color:#A9C6D8; }
.room-upload .q-uploader__header .q-btn, .room-upload .q-uploader__title { color:#3F6E8C !important; }
.ref-upload.q-uploader { border-color:#A6CBB0; }
.ref-upload .q-uploader__header .q-btn, .ref-upload .q-uploader__title { color:#3F7E55 !important; }
/* 生成大按钮 */
.gen-cta.q-btn { background:#C15F3C !important; border-radius:10px !important; min-height:50px; box-shadow:0 3px 10px rgba(193,95,60,0.28) !important; text-transform:none; }
.gen-cta.q-btn .q-btn__content { font-size:15px; font-weight:700; color:#ffffff !important; }
.gen-cta.q-btn .q-icon { color:#ffffff !important; }
/* 上下文卡（随工作流出现的三色块）*/
.ctx-room { background:#EEF3F6; border:1px solid #D6E2EA; border-radius:11px; padding:11px; }
.ctx-ref  { background:#EDF4EE; border:1px solid #D2E4D6; border-radius:11px; padding:11px; }
.ctx-pet  { background:#F6F0EE; border:1px solid #E7D8D2; border-radius:11px; padding:11px; }
/* 顺手重排：板材规格暖卡（贴地板小样下方）+ 客户与地区蓝卡（primary 字段蓝描边强调）*/
.spec-card   { background:#FBF6EF; border:1px solid #EAD9C4; border-radius:11px; padding:10px; display:flex; flex-direction:column; gap:6px; }
.region-card { background:#EEF2F5; border:1px solid #D8E2EA; border-radius:11px; padding:11px; display:flex; flex-direction:column; gap:8px; }
.region-primary .q-field__control:before { border-color:#BBD2E0 !important; }
/* 重抽控件：🔄重抽 │ ×N 连体胶囊（teal 描边）；批次运行时换成红橙「停止多抽」药丸 */
.regen-pod { display:flex; align-items:center; height:28px; border:1px solid var(--border-panel); border-radius:var(--radius-btn); background:#fff; overflow:hidden; }
.regen-pod .q-btn { min-height:28px !important; height:28px !important; color:#2e8c7e !important; font-weight:600; font-size:12px; border-radius:0 !important; padding:0 8px !important; box-shadow:none !important; }
.regen-pod .sepv { width:1px; background:var(--border-panel); height:18px; flex:0 0 1px; }
/* ×N select：压掉全局 .q-field 的 margin-top:1.4rem，与圆按钮垂直对齐 */
.regen-n.q-field { margin-top:0 !important; align-self:center; }
.regen-pod .regen-n .q-field__control { border:0 !important; height:28px !important; min-height:28px !important; background:transparent !important; }
.regen-pod .regen-n .q-field__native { color:#2e8c7e !important; font-weight:600; padding-top:0 !important; padding-bottom:0 !important; }
.regen-batch-stop.q-btn { height:28px !important; min-height:28px !important; border:1px solid var(--accent-strong) !important; border-radius:var(--radius-btn) !important; color:#fff !important; background:var(--accent-strong) !important; font-size:12px; font-weight:700; padding:0 10px !important; box-shadow:0 1px 3px rgba(193,95,60,0.25) !important; text-transform:none; }
/* 图槽候选导航条：‹ n/N ›（同槽历次重抽左右切换）。半透明黑底覆于图片底部，不挤占图高 */
.cand-nav { position:absolute; left:0; right:0; bottom:4px; padding:2px 6px; z-index:8; pointer-events:none; }
.cand-nav .cand-arrow.q-btn { pointer-events:auto; min-height:24px !important; height:24px !important; width:24px !important; color:#fff !important; background:rgba(26,24,21,0.62) !important; box-shadow:none !important; font-size:15px; font-weight:700; }
.cand-nav .cand-arrow.q-btn:disabled, .cand-nav .cand-arrow.q-btn[disabled] { opacity:0.32 !important; }
.cand-nav .cand-cnt { pointer-events:auto; color:#fff; background:rgba(26,24,21,0.62); border-radius:8px; padding:1px 8px; font-weight:600; min-width:40px; text-align:center; }
/* ── v7 体验优化：步骤条 / 智能配方 / 校验清单 / 引导卡 / 队列汇总 ── */
.step-bar { display:flex; align-items:center; padding:11px 16px 9px; border-bottom:1px solid #ECE6DA; background:#F4F1E9; }
.step-dot { width:20px; height:20px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex:0 0 auto; }
.step-dot--done { background:#5B9B6B; color:#fff; }
.step-dot--active { background:#C15F3C; color:#fff; box-shadow:0 1px 3px rgba(193,95,60,0.3); }
.step-dot--idle { background:#E3DED1; color:#9C9486; }
.step-txt { font-size:11.5px; font-weight:600; margin-left:6px; white-space:nowrap; }
.step-line { flex:1 1 auto; height:2px; margin:0 8px; border-radius:2px; background:#E3DED1; min-width:14px; }
.step-line--done { background:#5B9B6B; }
/* 智能配方卡 */
.recipe-card { display:flex; flex-direction:column; gap:3px; padding:9px 10px; border:1px solid #E3DED1; border-radius:10px; background:#ffffff; cursor:pointer; transition:all .15s ease; }
.recipe-card:hover { border-color:#C9A88F; box-shadow:0 1px 4px rgba(120,90,60,0.1); }
.recipe-card--on { border-color:#C15F3C; background:#FBF1EB; box-shadow:0 1px 3px rgba(193,95,60,0.22); }
.recipe-card .rc-label { font-size:12px; font-weight:700; color:#1F1B16; }
.recipe-card .rc-sub { font-size:9.5px; line-height:1.4; color:#9C8A66; }
.recipe-card--on .rc-sub { color:#B5713A; }
/* 校验清单 chip */
.chk-chip { display:inline-flex; align-items:center; gap:4px; font-size:10.5px; font-weight:600; padding:2px 9px; border-radius:20px; }
.chk-chip--ok { background:#EAF3EC; color:#4E9163; }
.chk-chip--idle { background:#EFEBE1; color:#9C9486; }
.chk-chip--miss { background:#FBEEE9; color:#B5503A; }
/* 引导卡（空状态：地板图优先）*/
.guide-zone { display:flex; flex-direction:column; align-items:center; gap:10px; padding:30px 18px; border:2px dashed #C9A88F; border-radius:14px; background:#FBF6EF; text-align:center; }
.guide-zone.q-uploader { box-shadow:none !important; width:100%; max-height:none; cursor:pointer; }
.guide-zone .q-uploader__list { display:none !important; }
.guide-zone .q-uploader__header { background:transparent !important; border:none; min-height:120px; display:flex; align-items:center; justify-content:center; }
.guide-zone .q-uploader__header .q-btn, .guide-zone .q-uploader__title { color:#A8472A !important; font-weight:700; font-size:14px; }
.guide-zone .q-uploader__subtitle, .guide-zone .q-uploader__spinner { display:none !important; }
/* 队列汇总条 */
.qsum-track { flex:1 1 auto; height:6px; border-radius:6px; background:#EDE6DA; overflow:hidden; }
.qsum-fill { height:100%; border-radius:6px; background:linear-gradient(90deg,#D9A05B,#C15F3C); transition:width .5s ease; }
.qchip { display:inline-flex; align-items:center; gap:4px; font-size:12px; cursor:pointer; border:1px solid #E3DED1; border-radius:8px; padding:5px 11px; background:#fff; color:#8A8175; }
.qchip--on { background:#FBF1EB; border-color:#C15F3C; color:#A8472A; font-weight:600; }
</style>''')

    floor_path = {'v': ''}; room_path = {'v': ''}; ref_path = {'v': ''}; last_img = {'v': ''}
    _cancel_generation = [0]; _cancel_jobs = set()
    def _is_cancelled(job_id: str, generation: int = None) -> bool:
        return job_id in _cancel_jobs or (generation is not None and generation < _cancel_generation[0])

    with ui.header().classes('q-py-xs q-px-md items-center justify-between').style('height:50px; background: var(--bg-header);'):
        with ui.row().classes('items-center'):
            ui.label('🪵 地板 AI 提示词引擎 v7').classes('text-h6').style('color: var(--text-accent);')
        with ui.tabs().classes('flex-grow justify-end') as main_tabs:
            t_workspace = ui.tab('workspace', label='🎨 工作台 (生成 & 队列)')
            t_records   = ui.tab('records', label='📋 记录管理')

    with ui.tab_panels(main_tabs, value='workspace').classes('w-full').style('height:calc(100vh - 50px); overflow:hidden;'):

        # ==================================
        # TAB 1: 工作台 (左 35% 生成，右 65% 队列)
        # ==================================
        with ui.tab_panel('workspace').classes('q-pa-none'):
            # ── 失败详情弹窗（建一次，点失败卡的 info 图标时 set 内容再 open）──
            with ui.dialog() as _fail_dlg, ui.card().classes('q-pa-md').style('min-width:460px; max-width:680px;'):
                _fail_title = ui.label('').classes('text-h6').style('color:#B5713A;')
                _fail_expl = ui.html('').classes('text-sm').style('color: var(--text-primary); line-height:1.6;')
                ui.separator().classes('q-my-sm')
                ui.label('完整原始报错').classes('text-xs').style('color: var(--text-secondary);')
                _fail_raw = ui.label('').classes('text-xs').style(
                    'white-space:pre-wrap; word-break:break-all; user-select:text;'
                    'max-height:38vh; overflow:auto; width:100%;'
                    'background:#FBF7F0; border:1px solid var(--border-panel); border-radius:8px;'
                    'padding:8px 10px; color:#6B6356; font-family:ui-monospace,Consolas,monospace;')
                with ui.row().classes('w-full justify-end q-mt-sm'):
                    ui.button('关闭', on_click=lambda: _fail_dlg.close()).props('flat dense')

            def _open_fail_detail(err):
                info = classify_failure(err)
                _fail_title.text = info['title']
                _fail_expl.content = (
                    f'<div style="margin-bottom:6px;">📋 <b>可能原因</b>：{info["cause"]}</div>'
                    f'<div>🛠 <b>处理建议</b>：{info["action"]}</div>'
                )
                _fail_raw.text = str(err or '（无原始报错文本）')
                _fail_dlg.open()

            # ── 常见失败原因「大汇总」弹窗（建一次，左下角 ℹ️ 打开）──
            with ui.dialog() as _fail_summary_dlg, ui.card().classes('q-pa-md').style('min-width:480px; max-width:680px;'):
                ui.label('📖 常见生成失败原因汇总').classes('text-h6').style('color: var(--text-accent);')
                ui.label('失败时卡片上会显示对应的暖橙 ⓘ 图标，点开即是下面这套说明。')\
                    .classes('text-xs q-mb-sm').style('color: var(--text-secondary);')
                with ui.column().classes('w-full q-gutter-y-sm').style('max-height:62vh; overflow:auto;'):
                    for _rule in FAILURE_RULES:
                        with ui.element('div').classes('w-full').style(
                                'border-left:3px solid #E0852E; padding:6px 10px;'
                                'background:#FBF7F0; border-radius:6px;'):
                            ui.label(_rule['title']).classes('text-sm').style('color:#B5713A; font-weight:700;')
                            ui.html(
                                f'<div style="font-size:12px;color:var(--text-primary);line-height:1.55;">'
                                f'📋 {_rule["cause"]}<br>🛠 {_rule["action"]}</div>')
                with ui.row().classes('w-full justify-end q-mt-sm'):
                    ui.button('关闭', on_click=lambda: _fail_summary_dlg.close()).props('flat dense')

            # 用 inline style 保证优先级最高，彻底防止被 Quasar / 浏览器默认样式覆盖
            with ui.row().classes('w-full no-wrap').style('overflow:hidden; height:calc(100vh - 50px);'):

                # 【左侧 35%】：参数区(可滚动) + 吸底操作栏（flex 纵向 column）
                with ui.column().classes('no-wrap border-r').style(
                        'flex:0 0 36%; min-width:0; width:36%; height:100%;'
                        'display:flex; flex-direction:column;'
                        'border-color: var(--border-panel); background: var(--bg-panel-left);'):

                  # 顶部步骤进度条（① 地板图 → ② 配方·参数 → ③ 生成）；_render_steps() 按状态重绘
                  step_bar = ui.row().classes('step-bar w-full no-wrap items-center').style('flex:0 0 auto;')

                  # ① 可滚动参数区（flex:1 + min-height:0 否则吸底栏会塌）
                  with ui.scroll_area().classes('q-pa-md').style('flex:1 1 auto; min-height:0;'):
                    with ui.column().classes('w-full q-gutter-y-sm'):

                        _editable = 'use-input input-debounce=0'

                        def _short_opt(s):
                            """下拉显示用的精简标签：去掉 ' - 描述' 和括号补充，保留核心名（取值仍用原全串）。"""
                            s = str(s).split(' - ')[0]
                            s = s.split(' (')[0].split(' （')[0]
                            return s.strip()

                        def _opts(lst):
                            """把一串长选项转成 {原值: 精简标签} 字典，.value 仍返回原全值。"""
                            return {v: _short_opt(v) for v in lst}

                        # ── 工作流（隐藏 radio 作取值真源 + 2×2 胶囊）──────────
                        ui.label('工作流').classes('sec-head')
                        workflow_radio = ui.radio(
                            ['纯效果图 (生成全新空间)', '地板替换 (保持原图换地板)', '宠物友好 (动物独处/主宠互动)', '参照模式 (风格参照图生新图)'],
                            value='纯效果图 (生成全新空间)'
                        )
                        workflow_radio.style('display:none')
                        _WF_PILLS = [
                            ('🎨 纯效果图', '纯效果图 (生成全新空间)'),
                            ('🔄 地板替换', '地板替换 (保持原图换地板)'),
                            ('🐾 宠物友好', '宠物友好 (动物独处/主宠互动)'),
                            ('🖼️ 参照模式', '参照模式 (风格参照图生新图)'),
                        ]
                        _wf_pill_btns = []
                        def _set_wf(val):
                            workflow_radio.value = val
                            for _b, (_, _v) in zip(_wf_pill_btns, _WF_PILLS):
                                if _v == val:
                                    _b.classes(add='wf-pill--on')
                                else:
                                    _b.classes(remove='wf-pill--on')
                            _on_mode()
                            _render_steps(); _render_checklist()
                        with ui.grid(columns=2).classes('w-full').style('gap:7px'):
                            for _wl, _wv in _WF_PILLS:
                                _pb = ui.button(_wl, on_click=lambda v=_wv: _set_wf(v)).props('flat no-caps').classes('wf-pill w-full')
                                if _wv == '纯效果图 (生成全新空间)':
                                    _pb.classes(add='wf-pill--on')
                                _wf_pill_btns.append(_pb)

                        # ── 地板素材图：hero 卡片 + 驯化上传 + 历史小样 ──────────
                        _floor_lbl = ui.label('📎 地板素材图').classes('sec-head'); _floor_lbl.visible = False
                        floor_hero = ui.element('div').classes('hero-card w-full'); floor_hero.visible = False
                        with floor_hero:
                            floor_prev = ui.image('').props('fit=cover').style('flex:0 0 88px; width:88px; height:66px; border-radius:8px;')
                            with ui.column().classes('q-gutter-y-xs').style('flex:1 1 auto; min-width:0; overflow:hidden;'):
                                _floor_name_lbl = ui.label('').style('font-size:12.5px; font-weight:600; color:#1F1B16; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%;')
                                tone_analysis_lbl = ui.html('').classes('tone-chip')

                        async def _use_floor_image(p):
                            """选用一张地板图(新上传或历史小样共用)：设路径+预览，跑色调分析与风格推荐。"""
                            floor_path['v'] = p
                            floor_prev.set_source(_thumb_url(p, 320))  # 预览走缩略图，原图存在 floor_path['v']
                            _floor_name_lbl.text = os.path.basename(p)
                            floor_hero.visible = True
                            try:
                                matched_tone, _analysis_html = await asyncio.to_thread(analyze_floor_tone, p)
                                _styles = get_style_choices(matched_tone)
                                tone_analysis_lbl.content = (
                                    '<span style="display:inline-block;'
                                    'background:#F3EBDD;color:#9A6A2E;font-size:10.5px;padding:2px 8px;border-radius:20px;">'
                                    f'🎨 已识别：{_short_opt(matched_tone)}</span>'
                                )
                                floor_tone_sel.value = matched_tone
                                _rebuild_style_sel(_styles)
                            except Exception as ex:
                                tone_analysis_lbl.content = f'<span style="color:#e74c3c;font-size:0.8em;">分析失败: {ex}</span>'
                            # 地板就位 → 展开下游参数、按识别色调出配方、刷新步骤条/校验清单
                            _set_left_gate(True)
                            _render_recipes(floor_tone_sel.value or '')
                            _render_steps(); _render_checklist()

                        async def on_floor_up(e: ng_events.UploadEventArguments):
                            fn, content = await _extract_upload_data(e)
                            if not content: ui.notify('读取失败', type='negative'); return
                            p = safe_upload_path(fn)
                            if not p: ui.notify('不支持的文件类型（仅 png/jpg/jpeg/webp/bmp）', type='negative'); return
                            with open(p, 'wb') as f: f.write(content)
                            await _use_floor_image(p)

                        # ── 历史小样选择器：直接复用磁盘里已上传的地板图，免重传 ──
                        with ui.dialog() as _hist_dlg, ui.card().classes('q-pa-md').style('min-width:520px; max-width:780px;'):
                            ui.label('📁 历史小样（点击直接使用，无需重传）').classes('text-h6').style('color: var(--text-accent);')
                            _hist_grid = ui.row().classes('w-full q-mt-xs').style('gap:8px; flex-wrap:wrap; max-height:60vh; overflow-y:auto;')
                            with ui.row().classes('w-full justify-end q-mt-sm'):
                                ui.button('关闭', on_click=lambda: _hist_dlg.close()).props('flat dense')

                        async def _pick_history_floor(p):
                            _hist_dlg.close()
                            await _use_floor_image(p)
                            ui.notify(f'✅ 已选用：{os.path.basename(p)}', type='positive')

                        def _open_history_dialog():
                            _hist_grid.clear()
                            paths = _list_recent_floor_swatches()
                            with _hist_grid:
                                if not paths:
                                    ui.label('暂无历史小样（_ng_uploads 为空）').classes('text-xs q-pa-md').style('color: var(--text-secondary);')
                                else:
                                    for p in paths:
                                        _cell = ui.element('div').classes('column items-center').style('width:120px; cursor:pointer;')
                                        with _cell:
                                            ui.image(_thumb_url(p, 320)).props('loading=lazy').classes('rounded').style('width:120px; height:90px; object-fit:cover;')
                                            ui.label(os.path.basename(p)).classes('text-xs text-center').style('word-break:break-all; line-height:1.1; color: var(--text-secondary);')
                                        _cell.on('click', lambda _, pp=p: _pick_history_floor(pp))
                            _hist_dlg.open()

                        _floor_change_row = ui.row().classes('w-full no-wrap items-stretch').style('gap:7px'); _floor_change_row.visible = False
                        with _floor_change_row:
                            ui.upload(on_upload=on_floor_up, auto_upload=True, max_files=1).props('accept=".jpg,.jpeg,.png" flat label="⬆️ 更换 / 拖拽上传"').classes('floor-upload').style('flex:1 1 0; min-width:0;')
                            ui.button('📁 历史小样', on_click=_open_history_dialog).props('flat no-caps').style('flex:0 0 auto; border:1px solid #E3DED1; background:#fff; color:#6B6356; border-radius:9px; white-space:nowrap;')

                        # ── 空状态引导卡（地板图优先：未上传时显示，上传后由 _set_left_gate 收起）──
                        floor_empty_guide = ui.column().classes('w-full q-gutter-y-sm')
                        with floor_empty_guide:
                            ui.label('① 从地板素材图开始').classes('sec-head')
                            ui.upload(on_upload=on_floor_up, auto_upload=True, max_files=1).props(
                                'accept=".jpg,.jpeg,.png" flat label="🪵 拖拽或点击上传地板小样"').classes('guide-zone')
                            ui.html('<div style="font-size:11.5px;color:#9C8A66;line-height:1.6;">'
                                    '上传后将<b>自动识别木种与色调</b>，并为你推荐成套的风格 / 光线配方——不用一个个手填。'
                                    '&nbsp;支持 jpg / png / webp。</div>')
                            with ui.row().classes('w-full'):
                                ui.button('📁 从历史小样选', on_click=_open_history_dialog).props('flat no-caps').style(
                                    'border:1px solid #E3DED1; background:#fff; color:#A8472A; border-radius:9px; white-space:nowrap;')
                            with ui.element('div').style(
                                    'display:flex;flex-direction:column;gap:10px;padding:16px;border-radius:12px;'
                                    'background:#EFEBE1;border:1px dashed #DDD6C8;opacity:0.85;'):
                                ui.html('<div style="font-size:11.5px;font-weight:600;color:#9C9486;">上传地板图后，这里会出现 ——</div>')
                                ui.html('<div style="display:flex;flex-wrap:wrap;gap:7px;">'
                                        '<span style="font-size:11px;color:#9C9486;background:#F4F1E9;padding:4px 10px;border-radius:7px;">✨ 智能配方推荐</span>'
                                        '<span style="font-size:11px;color:#9C9486;background:#F4F1E9;padding:4px 10px;border-radius:7px;">🎨 色调识别结果</span>'
                                        '<span style="font-size:11px;color:#9C9486;background:#F4F1E9;padding:4px 10px;border-radius:7px;">🚪 场景核心参数</span>'
                                        '</div>')

                        # ── 🪚 板材规格（跟着地板小样一起定，置顶；地板上传后才显）──
                        spec_card = ui.column().classes('w-full spec-card'); spec_card.visible = False
                        with spec_card:
                            ui.html('<div class="sec-head" style="color:#9A6A2E">🪵 板材规格 '
                                    '<span style="font-weight:600;color:#B9A488;">· 跟着地板小样一起定</span></div>')
                            with ui.grid(columns=3).classes('w-full').style('gap:6px'):
                                floor_size_sel = ui.select(FLOOR_SIZES, value=FLOOR_SIZES[0], label='📏 板材尺寸').classes('w-full').props(_editable)
                                seam_sel = ui.select(['无缝拼接 (SPC/LVT专用)','常规倒角缝 (如强化/木地板)','圆弧倒角 (Pressed Bevel)'], value='无缝拼接 (SPC/LVT专用)', label='🔩 拼缝').classes('w-full').props(_editable)
                                gloss_sel = ui.select(['超哑光 (0-3°)','哑光 (3-5°)','高光 (High Gloss)'], value='哑光 (3-5°)', label='✨ 光泽度').classes('w-full').props(_editable)

                        # ── ② ✨ 智能配方（地板上传后按识别色调排序展示；点卡一键填好场景参数）──
                        recipe_wrap = ui.column().classes('w-full q-gutter-y-xs'); recipe_wrap.visible = False
                        with recipe_wrap:
                            ui.html('<div class="sec-head">✨ 智能配方 '
                                    '<span style="font-weight:600;color:#B5AC9C;">· 一键套用，再微调</span></div>')
                            recipe_box = ui.grid(columns=3).classes('w-full').style('gap:7px')

                        # ── 上下文区（跟随工作流，统一固定槽位；三色卡）──────────
                        room_sec = ui.column().classes('w-full q-gutter-y-xs ctx-room')
                        room_sec.visible = False
                        with room_sec:
                            ui.label('🏠 待替换房间原图').classes('text-caption').style('color:#3F6E8C; font-weight:700;')
                            async def on_room_up(e: ng_events.UploadEventArguments):
                                fn, content = await _extract_upload_data(e)
                                if not content: ui.notify('读取失败', type='negative'); return
                                p = safe_upload_path(fn, prefix='room_')
                                if not p: ui.notify('不支持的文件类型（仅 png/jpg/jpeg/webp/bmp）', type='negative'); return
                                with open(p, 'wb') as f: f.write(content)
                                room_path['v'] = p; ui.notify('✅ 已上传', type='positive')
                                _render_steps(); _render_checklist()
                            ui.upload(on_upload=on_room_up, auto_upload=True, max_files=1).props('accept=".jpg,.jpeg,.png" flat label="⬆️ 拖拽或点击上传房间照片"').classes('ctx-upload room-upload w-full')

                        ref_sec = ui.column().classes('w-full q-gutter-y-xs ctx-ref')
                        ref_sec.visible = False
                        with ref_sec:
                            ui.label('🖼️ 风格参照图').classes('text-caption').style('color:#3F7E55; font-weight:700;')
                            ui.label('上传一张参照房间照片，AI将复制其整体风格').classes('text-caption').style('color:#6E8C77; font-size:0.75em;')
                            ref_prev = ui.image('').classes('w-full rounded'); ref_prev.visible = False
                            async def on_ref_up(e: ng_events.UploadEventArguments):
                                fn, content = await _extract_upload_data(e)
                                if not content: ui.notify('读取失败', type='negative'); return
                                p = safe_upload_path(fn, prefix='ref_')
                                if not p: ui.notify('不支持的文件类型（仅 png/jpg/jpeg/webp/bmp）', type='negative'); return
                                with open(p, 'wb') as f: f.write(content)
                                ref_path['v'] = p; ref_prev.set_source(_thumb_url(p, 960)); ref_prev.visible = True; ui.notify('✅ 参照图已上传', type='positive')
                                _render_steps(); _render_checklist()
                            ui.upload(on_upload=on_ref_up, auto_upload=True, max_files=1).props('accept=".jpg,.jpeg,.png" flat label="⬆️ 拖拽或点击上传参照图"').classes('ctx-upload ref-upload w-full')
                            ref_correction_inp = ui.textarea(label='附加风格说明 (可选)', placeholder='例：保持参照图的暖色调，但换成更简洁的布局...').classes('w-full').props('rows=2')

                        pet_sec = ui.column().classes('w-full q-gutter-y-sm ctx-pet'); pet_sec.visible = False
                        with pet_sec:
                            ui.label('🐾 宠物设置').classes('text-caption').style('color:#9A5B45; font-weight:700;')
                            with ui.grid(columns=3).classes('w-full').style('gap:7px'):
                                pet_type_sel = ui.select(_opts(PET_TYPES), value=PET_TYPES[0], label='种类').classes('w-full').props(_editable)
                                pet_action_sel = ui.select(_opts(PET_ACTIONS), value=PET_ACTIONS[0], label='动作').classes('w-full').props(_editable)
                                pet_focus_sel = ui.select(_opts(PET_FOCUS_OPTIONS), value=PET_FOCUS_OPTIONS[0], label='焦点').classes('w-full').props(_editable)

                        # ── 🌍 客户与地区（决定建筑/家具风格；上移到风格之前，独立蓝卡）──
                        region_card = ui.column().classes('w-full region-card'); region_card.visible = False
                        with region_card:
                            ui.html('<div class="sec-head" style="color:#3F6E8C">🌍 客户与地区 '
                                    '<span style="font-weight:600;color:#9FB3C0;">· 决定建筑/家具风格</span></div>')
                            _market_tabs = ui.tabs().props('dense').classes('w-full market-seg')
                            with _market_tabs:
                                _tab_intl = ui.tab('intl', label='🌍 海外市场')
                                _tab_cn   = ui.tab('cn',   label='🇨🇳 国内专属')
                            _market_mode = {'v': 'intl'}  # 追踪当前 tab

                            _market_panels = ui.tab_panels(_market_tabs, value='intl').classes('w-full market-panels').style('padding:0')
                            with _market_panels:

                                # ══ Tab 海外 ══════════════════════════════
                                with ui.tab_panel('intl').classes('q-pa-none'):
                                    with ui.column().classes('w-full q-gutter-y-sm q-pt-xs'):
                                        # ── primary：大洲 - 国家 - 城市（提到最前，蓝描边强调）──
                                        _init_cont = CONTINENTS[-1]
                                        with ui.grid(columns=3).classes('w-full region-primary').style('gap:7px'):
                                            continent_sel = ui.select(CONTINENTS, value=_init_cont, label='🌍 大洲').classes('w-full').props(_editable)
                                            _country_box = ui.element('div').classes('w-full')
                                            _city_box    = ui.element('div').classes('w-full')
                                        _country_ref = {'w': None}
                                        _city_ref    = {'w': None}

                                        def _rebuild_city_sel(countries_map, country):
                                            cities = countries_map.get(country, [])
                                            _city_box.clear()
                                            with _city_box:
                                                _city_ref['w'] = ui.select(cities, value=cities[0] if cities else None, label='🏙️ 城市').classes('w-full').props(_editable)

                                        def _rebuild_country_sel(continent):
                                            countries_map = LOCATION_MAP.get(continent, {"通用": ["通用现代都市"]})
                                            countries = list(countries_map.keys())
                                            _country_box.clear()
                                            with _country_box:
                                                def _on_country_pick(e):
                                                    _rebuild_city_sel(countries_map, e.value or countries[0])
                                                _country_ref['w'] = ui.select(countries, value=countries[0], label='🗺️ 国家', on_change=_on_country_pick).classes('w-full').props(_editable)
                                            _rebuild_city_sel(countries_map, countries[0])

                                        continent_sel.on_value_change(lambda e: _rebuild_country_sel(e.value or CONTINENTS[-1]))
                                        _rebuild_country_sel(_init_cont)

                                        # ── secondary：房间/物业/视野/家具 + 小区 ──
                                        with ui.grid(columns=2).classes('w-full').style('gap:8px'):
                                            room_type_sel = ui.select(ROOM_TYPES, value=ROOM_TYPES[0], label='🚪 房间类型').classes('w-full').props(_editable)
                                            prop_sel = ui.select(PROPERTY_TYPES, value=PROPERTY_TYPES[0], label='🏠 物业类型').classes('w-full').props(_editable)
                                            view_sel = ui.select(VIEWS, value=VIEWS[0], label='🪟 视野').classes('w-full').props(_editable)
                                            market_sel = ui.select(_opts(MARKET_FURNITURE_CHOICES), value=MARKET_FURNITURE_CHOICES[0], label='🛋️ 家具地区风格').classes('w-full').props(_editable)
                                        hood_inp = ui.input(label='小区/地段 (可选)', placeholder='自由填写...').classes('w-full')

                                # ══ Tab 国内 ══════════════════════════════
                                with ui.tab_panel('cn').classes('q-pa-none'):
                                    with ui.column().classes('w-full q-gutter-y-sm q-pt-xs'):
                                        with ui.grid(columns=2).classes('w-full').style('gap:8px'):
                                            cn_delivery_sel   = ui.select(CN_DELIVERY_CHOICES, value=CN_DELIVERY_CHOICES[0], label='🏆 交付/装修状态').classes('w-full').props(_editable)
                                            cn_developer_sel  = ui.select(CN_DEVELOPERS, value=CN_DEVELOPERS[0], label='🏢 开发商').classes('w-full').props(_editable)
                                            cn_city_sel       = ui.select(CN_CITIES, value=CN_CITIES[0] if CN_CITIES else '上海', label='🏙️ 城市').classes('w-full').props(_editable)
                                            cn_tier_sel       = ui.select(CN_TIERS, value=CN_TIERS[0], label='📊 楼盘定位').classes('w-full').props(_editable)
                                            cn_unit_sel       = ui.select(CN_UNIT_TYPES, value=CN_UNIT_TYPES[0], label='🏠 户型').classes('w-full').props(_editable)
                                            cn_room_type_sel  = ui.select(CN_ROOM_TYPES, value=CN_ROOM_TYPES[0], label='🚪 国内空间类型').classes('w-full').props(_editable)
                                            cn_view_sel       = ui.select(VIEWS, value=VIEWS[0], label='🪟 视野').classes('w-full').props(_editable)

                                        ui.label('🏗️ 空间特征 (可多选)').classes('text-caption q-mt-xs').style('color: var(--text-label);')
                                        cn_space_checks = {}
                                        with ui.column().classes('w-full q-gutter-y-xs q-pl-sm'):
                                            for _sf in CN_SPACE_FEATURES.keys():
                                                cn_space_checks[_sf] = ui.checkbox(_sf, value=False).classes('w-full text-sm')

                                        ui.label('🔌 标配设施 (可多选)').classes('text-caption q-mt-xs').style('color: var(--text-label);')
                                        cn_fac_checks = {}
                                        with ui.column().classes('w-full q-gutter-y-xs q-pl-sm'):
                                            for _fac in CN_FACILITIES.keys():
                                                cn_fac_checks[_fac] = ui.checkbox(_fac, value=False).classes('w-full text-sm')

                            _market_tabs.on_value_change(lambda e: _market_mode.__setitem__('v', e.value))

                        # ── 🎨 风格与画面（配方已填好，可微调；2 列网格）──────────────
                        _core_lbl = ui.row().classes('w-full items-center justify-between q-mt-sm')
                        with _core_lbl:
                            ui.html('<div class="sec-head">🎨 风格与画面 '
                                    '<span style="font-weight:600;color:#B5AC9C;">· 配方已填好</span></div>')
                            recipe_badge = ui.label('未套用配方').style(
                                'font-size:10px;color:#9C9486;background:#EFEBE1;padding:2px 8px;border-radius:20px;white-space:nowrap;')
                        core_grid = ui.grid(columns=2).classes('w-full').style('gap:8px')
                        with core_grid:
                            floor_tone_sel = ui.select(_opts(FLOOR_TONES), value=FLOOR_TONES[0], label='🎨 色调').classes('w-full').props(_editable)

                            # 风格选择器容器（支持重建）
                            _style_sel_box = ui.element('div').classes('w-full')
                            style_sel_ref = {'w': None}

                            def _rebuild_style_sel(isc):
                                _style_sel_box.clear()
                                with _style_sel_box:
                                    opts = {v: _short_opt(l) for l, v in isc}
                                    style_sel_ref['w'] = ui.select(
                                        opts, value=isc[0][1] if isc else None, label='✨ 风格 (按色调排序)'
                                    ).classes('w-full').props(_editable)

                            _isc = get_style_choices(FLOOR_TONES[0])
                            _rebuild_style_sel(_isc)
                            style_sel = style_sel_ref  # dict ref
                            floor_tone_sel.on_value_change(lambda e: _rebuild_style_sel(get_style_choices(e.value or FLOOR_TONES[0])))

                            light_sel = ui.select(_opts(LIGHTINGS), value=LIGHTINGS[0], label='💡 光线').classes('w-full').props(_editable)
                            angle_sel = ui.select(_opts(ANGLES), value=ANGLES[0], label='📷 镜头').classes('w-full').props(_editable)
                            aspect_sel = ui.select(['4:3 (横向)','16:9 (超宽)','3:4 (竖向)','9:16 (手机)'], value='4:3 (横向)', label='📐 比例').classes('w-full').props(_editable)
                            res_sel = ui.select(['4K','2K'], value='4K', label='🔍 画质').classes('w-full').props(_editable)

                        # ── ⚙️ 更多参数（低频，默认折叠）─────────────────────
                        more_exp = ui.expansion('⚙️ 更多 · 避免清单 / 自定义补充').classes('w-full').props('dense')
                        with more_exp:
                            with ui.column().classes('w-full q-gutter-y-sm'):
                                ui.label('🚫 避免出现').classes('text-caption').style('color: var(--text-label);')
                                avoid_checks = {}
                                with ui.column().classes('w-full q-gutter-y-xs q-pl-sm'):
                                    for _item in AVOID_LIST:
                                        _cb = ui.checkbox(_item, value=True).classes('w-full text-sm')
                                        avoid_checks[_item] = _cb
                                custom_inp = ui.textarea(label='自定义补充指令', placeholder='可在此追加任何英文或中文补充说明...').classes('w-full').props('rows=2')

                        def _on_mode():
                            m = workflow_radio.value
                            _is_ref = '参照模式' in m
                            room_sec.visible = '地板替换' in m
                            pet_sec.visible = '宠物友好' in m
                            ref_sec.visible = _is_ref
                            _style_sel_box.visible = not _is_ref
                        workflow_radio.on('update:modelValue', lambda e: _on_mode())

                        with ui.expansion('🔑 API 设置').classes('w-full').props('dense'):
                            with ui.column().classes('w-full q-gutter-y-sm'):
                                _cfg0 = _load_config()
                                with ui.row().classes('w-full no-wrap items-center').style('gap:6px'):
                                    provider_sel = ui.select(
                                        {'google': 'Google 直连', 'fal': 'Fal 路由'},
                                        value=(_cfg0.get('image_provider') or 'google'),
                                        label='生图线路',
                                    ).classes('flex-1')
                                    with ui.icon('info', size='sm').classes('cursor-pointer').style('color: var(--text-secondary);'):
                                        ui.tooltip(
                                            '⚠️ Fal 路由开销较大：同 4K 比 Google 直连贵约 40–50%'
                                            '（双模型每次约 $0.46 vs $0.30，Fal 对 flash/B2 与 4K 加价明显）。'
                                            '建议日常用 Google 直连，仅在直连被重置/卡住时切到 Fal 应急。'
                                        ).style('max-width:260px; font-size:12px; line-height:1.5;')
                                api_key_inp = ui.input('Gemini API Key', value=_cfg0.get('gemini_api_key',''), password=True).classes('w-full')
                                fal_key_inp = ui.input('Fal API Key', value=_cfg0.get('fal_api_key',''), password=True).classes('w-full')
                                api_proxy_inp = ui.input('本地代理', value=_cfg0.get('proxy','')).classes('w-full')
                                with ui.row().classes('w-full no-wrap items-center').style('gap:6px'):
                                    profile_sel = ui.select(
                                        {'fast': '⚡ 极速 (快速失败·灵敏)', 'resilient': '🛡 韧性 (死磕重试)'},
                                        value=get_speed_profile(),
                                        label='出图重试策略',
                                    ).classes('flex-1')
                                    with ui.icon('info', size='sm').classes('cursor-pointer').style('color: var(--text-secondary);'):
                                        ui.tooltip(
                                            '⚡ 极速：网络好则正常出图(速度与韧性完全一样)，网络不通则几十秒内麻利报错——'
                                            '复刻老版本灵敏体感，坏线路下需手动多重提几次。\n'
                                            '🛡 韧性：坏节点上自动死磕重试(最长~10min)、尽量自愈，代价是任务可能长时间转圈。\n'
                                            '两种成功出图的速度相同，只影响"失败时"的反应。'
                                        ).style('max-width:280px; font-size:12px; line-height:1.5; white-space:pre-line;')

                                with ui.row().classes('w-full no-wrap items-center').style('gap:6px'):
                                    failover_chk = ui.checkbox(
                                        '直连失败自动转 Fal 备用线路',
                                        value=bool(_cfg0.get('auto_failover', False)),
                                    ).classes('flex-1')
                                    with ui.icon('info', size='sm').classes('cursor-pointer').style('color: var(--text-secondary);'):
                                        ui.tooltip(
                                            '开启后：Google 直连因【网络类失败】(代理重置/超时/连接假死)重试耗尽时，'
                                            '自动改走 Fal 备用线路再跑一次——用你自己的 Fal Key(=你自己的钱)。\n'
                                            '安全拦截、HTTP 400/403 等换线也会失败的错误不会转线，避免白烧钱。\n'
                                            '默认关闭：直连失败即报错，不动用 Fal。'
                                        ).style('max-width:280px; font-size:12px; line-height:1.5; white-space:pre-line;')

                                def _save_api_settings():
                                    save_api_key(api_key_inp.value, api_proxy_inp.value)
                                    save_provider_settings(fal_key_inp.value, provider_sel.value)
                                    save_speed_profile(profile_sel.value)
                                    save_auto_failover(failover_chk.value)
                                    _route = 'Fal 路由' if provider_sel.value == 'fal' else 'Google 直连'
                                    _mode = '⚡ 极速' if profile_sel.value == 'fast' else '🛡 韧性'
                                    _fo = '开' if failover_chk.value else '关'
                                    ui.notify(f'保存成功 · 线路: {_route} · 策略: {_mode} · 自动转线: {_fo}', type='positive')

                                # 下拉/开关即选即存：避免"选了线路却没点💾保存就去提交、仍按旧线路跑"的坑。
                                # (Key/代理 仍只在点💾保存时写盘，不宜每敲一键就存。)
                                provider_sel.on_value_change(lambda e: (
                                    save_provider_settings(None, e.value),  # fal_key 传 None：只改线路，不动半填的 Fal Key
                                    ui.notify(f"✅ 生图线路已切到 {'Fal 路由' if e.value == 'fal' else 'Google 直连'}（已保存）", type='positive'),
                                ))
                                profile_sel.on_value_change(lambda e: (
                                    save_speed_profile(e.value),
                                    ui.notify(f"✅ 重试策略已切到 {'⚡ 极速' if e.value == 'fast' else '🛡 韧性'}（已保存）", type='positive'),
                                ))
                                failover_chk.on_value_change(lambda e: (
                                    save_auto_failover(bool(e.value)),
                                    ui.notify(f"✅ 自动转 Fal 已{'开启' if e.value else '关闭'}（已保存）", type='positive'),
                                ))

                                _conn_test_lbl = ui.label('').classes('text-xs w-full').style(
                                    'color: var(--text-secondary); white-space:pre-line;')
                                _conn_test_lbl.visible = False

                                async def _do_test_connection():
                                    _conn_test_lbl.visible = True
                                    _conn_test_lbl.text = '正在测试连接…'
                                    try:
                                        summary = await asyncio.to_thread(
                                            test_connection, api_key_inp.value, fal_key_inp.value, api_proxy_inp.value)
                                    except Exception as ex:
                                        logger.warning(f"连通性测试异常: {ex}")
                                        summary = f'测试异常：{ex}'
                                    _conn_test_lbl.text = summary
                                    ui.notify('连通性测试完成', type='info')

                                with ui.row().classes('w-full no-wrap').style('gap:6px'):
                                    ui.button('💾 保存', on_click=_save_api_settings).classes('flex-1').props('outline color=amber-8')
                                    ui.button('🔌 测试连接', on_click=_do_test_connection).classes('flex-1').props('outline color=blue-7')

                        # ── 批量生成（多场景）弹窗 + 处理器（入口按钮在底部操作栏）──
                        # 勾选多个房间类型，其余参数沿用左侧当前设置；每个房间并发提交一套图。
                        with ui.dialog() as _batch_dlg, ui.card().classes('q-pa-md').style('min-width:420px; max-width:560px;'):
                            ui.label('🗂 批量生成（多场景）').classes('text-h6').style('color: var(--text-accent);')
                            ui.label('勾选多个房间类型——其余参数（地板/风格/色调/光线…）沿用左侧当前设置，每个房间各出一套图。'
                                     ).classes('text-xs').style('color: var(--text-secondary);')
                            _batch_model_sel = ui.select(
                                {'both': '双模型 (B2+Pro)', 'pro': '仅 Pro', 'b2': '仅 B2'},
                                value='both', label='出图模型',
                            ).classes('w-full').props('outlined dense')
                            with ui.row().classes('w-full items-center justify-between q-mt-xs'):
                                ui.label('🚪 选择场景（房间类型）').classes('text-caption').style('color: var(--text-label);')
                                with ui.row().style('gap:4px;'):
                                    ui.button('全选', on_click=lambda: _batch_set_all(True)).props('flat dense size=sm')
                                    ui.button('清空', on_click=lambda: _batch_set_all(False)).props('flat dense size=sm')
                            _batch_rooms_box = ui.column().classes('w-full q-gutter-y-xs q-pl-sm').style('max-height:300px; overflow-y:auto;')
                            _batch_room_checks = {}
                            with ui.row().classes('w-full justify-end q-mt-sm').style('gap:6px;'):
                                ui.button('取消', on_click=lambda: _batch_dlg.close()).props('flat dense')
                                ui.button('🚀 提交批量', on_click=lambda: _run_batch()).props('color=amber-8 dense')

                        def _batch_set_all(val):
                            for cb in _batch_room_checks.values():
                                cb.value = val

                        def _open_batch_dialog():
                            if not floor_path['v']:
                                ui.notify('⚠️ 请先上传地板图', type='warning'); return
                            # 按当前 海外/国内 标签重建房间清单（海外 ROOM_TYPES / 国内 CN_ROOM_TYPES）
                            rooms = CN_ROOM_TYPES if _market_mode['v'] == 'cn' else ROOM_TYPES
                            _batch_rooms_box.clear(); _batch_room_checks.clear()
                            with _batch_rooms_box:
                                for rt in rooms:
                                    _batch_room_checks[rt] = ui.checkbox(rt, value=False).classes('w-full text-sm')
                            _batch_dlg.open()

                        async def _batch_one(_client, _mf, _rt):
                            # 关键：批量任务跑在独立 asyncio task 里，slot/client 上下文按 task 存，
                            # 裸 create_task 的新 task slot 栈为空 → _run_job 里的 ui.notify 会抛
                            # "current slot cannot be determined"。用 with _client 显式进上下文修复。
                            with _client:
                                await _run_job(_mf, room_override=_rt)

                        def _run_batch():
                            if not api_key_inp.value.strip():
                                ui.notify('⚠️ 缺少 API Key', type='warning'); return
                            if not floor_path['v'] or not os.path.exists(str(floor_path['v'])):
                                ui.notify('⚠️ 请先上传地板图', type='warning'); return
                            selected = [rt for rt, cb in _batch_room_checks.items() if cb.value]
                            if not selected:
                                ui.notify('⚠️ 请至少勾选一个场景', type='warning'); return
                            mf = _batch_model_sel.value or 'both'
                            _client = context.client   # 此处在按钮 handler 上下文内，client 有效，先捕获
                            for rt in selected:
                                background_tasks.create(_batch_one(_client, mf, rt), name=f'batch:{rt}')
                            _batch_dlg.close()
                            logger.info(f"[批量] 提交 {len(selected)} 个场景, model_filter={mf}, rooms={selected}")
                            ui.notify(f'已提交 {len(selected)} 个场景到队列', type='positive')

                  # ② 吸底操作栏（脱离滚动，常驻可见）
                  with ui.column().classes('w-full q-pa-md').style(
                          'flex:0 0 auto; gap:9px; border-top:1px solid var(--border-panel);'
                          'background:#ffffff; box-shadow:0 -5px 16px rgba(120,90,60,0.06);'):
                        # 校验清单 chips（地板图 / 上下文图 / 参数就绪）；_render_checklist() 按状态重绘
                        checklist_row = ui.row().classes('w-full items-center').style('gap:6px; flex-wrap:wrap;')
                        with ui.row().classes('w-full items-center justify-between'):
                            gen_status_lbl = ui.label('就绪').classes('text-caption').style('color:#8A8175;')
                            with ui.row().classes('items-center no-wrap').style('gap:4px'):
                                ui.html('<span style="font-size:11px;color:#5B9B6B;">● 线路正常</span>')
                                _net_info_icon = ui.icon('info', size='xs').classes('cursor-pointer').style('color:#E0852E;')
                                _net_info_icon.on('click', lambda: _fail_summary_dlg.open())
                                with _net_info_icon:
                                    ui.tooltip('常见生成失败原因 / 网络说明').style('font-size:12px;')
                        model_toggle = ui.toggle({'b2': '⚡ B2', 'pro': '⚡ Pro', 'both': '⚡ 双模型'}, value='both').props('no-caps').classes('w-full seg-toggle')
                        _MODEL_NAMES = {'b2': 'B2', 'pro': 'Pro', 'both': '双模型'}
                        gen_btn = ui.button('⚡ 生成 · 双模型', on_click=lambda: _run_job(model_toggle.value)).classes('w-full gen-cta')
                        model_toggle.on_value_change(lambda e: gen_btn.set_text(f"⚡ 生成 · {_MODEL_NAMES.get(e.value, '')}"))
                        with ui.row().classes('w-full no-wrap').style('gap:7px'):
                            batch_btn = ui.button('🗂 批量生成', icon='grid_view', on_click=_open_batch_dialog).props('outline no-caps color=secondary').style('flex:1 1 0;')
                            stop_all_btn = ui.button('⏹ 全部停止', icon='stop', on_click=lambda: _request_stop_all()).props('flat no-caps color=negative').style('flex:0 0 auto;')

                  # ── v7 体验优化：步骤条 / 渐进展开 / 智能配方 / 校验清单 渲染逻辑 ──
                  # 全在左栏构建完成后定义；仅在运行时(上传/切流/点配方)被调用，故可引用上方已建控件。
                  def _opt_keys(sel):
                      o = getattr(sel, 'options', None)
                      if isinstance(o, dict):
                          return list(o.keys())
                      if isinstance(o, (list, tuple)):
                          return list(o)
                      return []

                  def _ctx_need():
                      """当前工作流需要哪张上下文图 + 是否已就绪。返回 (标签|None, ok)。"""
                      m = workflow_radio.value or ''
                      if '地板替换' in m:
                          return '房间原图', bool(room_path['v'])
                      if '参照模式' in m:
                          return '参照图', bool(ref_path['v'])
                      return None, True

                  def _set_left_gate(on):
                      """地板图就位后展开下游参数；未上传只露引导卡（地板图优先）。"""
                      _floor_lbl.visible = on
                      _floor_change_row.visible = on
                      spec_card.visible = on
                      recipe_wrap.visible = on
                      region_card.visible = on
                      _core_lbl.visible = on
                      core_grid.visible = on
                      more_exp.visible = on
                      floor_empty_guide.visible = not on
                      if on:
                          _on_mode()   # 恢复上下文卡按工作流显隐
                      else:
                          room_sec.visible = ref_sec.visible = pet_sec.visible = False

                  def _render_steps():
                      step_bar.clear()
                      floor_ok = bool(floor_path['v'])
                      _need, ctx_ok = _ctx_need()
                      ready = floor_ok and ctx_ok
                      s1 = 'done' if floor_ok else 'active'
                      s2 = 'done' if ready else ('active' if floor_ok else 'idle')
                      s3 = 'active' if ready else 'idle'
                      steps = [('1', '地板图', s1), ('2', '配方·参数', s2), ('3', '生成', s3)]
                      with step_bar:
                          for i, (num, label, st) in enumerate(steps):
                              dot = {'done': 'step-dot--done', 'active': 'step-dot--active', 'idle': 'step-dot--idle'}[st]
                              ui.html(f'<div class="step-dot {dot}">{"✓" if st == "done" else num}</div>')
                              _tc = '#1F1B16' if st != 'idle' else '#B5AC9C'
                              ui.html(f'<span class="step-txt" style="color:{_tc};">{label}</span>')
                              if i < len(steps) - 1:
                                  _ln = 'step-line--done' if steps[i + 1][2] != 'idle' else ''
                                  ui.html(f'<div class="step-line {_ln}"></div>')

                  def _render_checklist():
                      checklist_row.clear()
                      floor_ok = bool(floor_path['v'])
                      _need, ctx_ok = _ctx_need()
                      chips = [('地板图', 'ok' if floor_ok else 'idle', '✓' if floor_ok else '○')]
                      if _need is not None:
                          chips.append((_need, 'ok' if ctx_ok else 'miss', '✓' if ctx_ok else '!'))
                      chips.append(('参数就绪', 'ok' if floor_ok else 'idle', '✓' if floor_ok else '○'))
                      with checklist_row:
                          for label, kind, icon in chips:
                              ui.html(f'<span class="chk-chip chk-chip--{kind}">{icon} {label}</span>')

                  _recipe_cards = {}
                  _recipe_state = {'key': None}

                  def _apply_recipe(r):
                      def _set(sel, kws):
                          if sel is None:
                              return
                          k = pick_option_key(_opt_keys(sel), kws)
                          if k is not None:
                              sel.value = k
                      _set(light_sel, r['light_kw'])
                      _set(angle_sel, r['angle_kw'])
                      _set(aspect_sel, r['aspect_kw'])
                      _set(res_sel, r['res_kw'])
                      # 板材尺寸/拼缝/光泽度 跟着地板小样一起定，交由用户手动控制——配方不再覆盖（r 里仍带 size_kw/seam_kw/gloss_kw，只是不应用）
                      _set(style_sel_ref['w'], r['style_kw'])
                      _recipe_state['key'] = r['key']
                      for _k, _c in _recipe_cards.items():
                          (_c.classes(add='recipe-card--on') if _k == r['key']
                           else _c.classes(remove='recipe-card--on'))
                      recipe_badge.text = f"✨ {r['label']}"
                      recipe_badge.style('font-size:10px;color:#5B9B6B;background:#EAF3EC;'
                                         'padding:2px 8px;border-radius:20px;white-space:nowrap;')
                      ui.notify(f"已套用配方：{r['label']}（可继续微调）", type='positive')
                      _render_steps(); _render_checklist()

                  def _render_recipes(tone):
                      recipe_box.clear()
                      _recipe_cards.clear()
                      with recipe_box:
                          for r in recommend_recipes(tone):
                              card = ui.element('div').classes('recipe-card')
                              with card:
                                  ui.html(f'<div class="rc-label">{r["label"]}</div>')
                                  ui.html(f'<div class="rc-sub">{r["sub"]}</div>')
                              card.on('click', lambda _=None, rr=r: _apply_recipe(rr))
                              _recipe_cards[r['key']] = card
                      if _recipe_state['key'] in _recipe_cards:
                          _recipe_cards[_recipe_state['key']].classes(add='recipe-card--on')

                  # 初始：空状态（地板图优先），画步骤条与校验清单
                  _set_left_gate(False)
                  _render_steps(); _render_checklist()

                # 【右侧 50%】：实时队列栏，带下载按钮
                # 【右侧 65%】：实时队列栏（inline 限宽 + min-width:0 双保险）
                with ui.scroll_area().classes('q-pa-md').style(
                        'flex:0 0 64%; min-width:0; width:64%; height:100%; overflow:hidden;'
                        'background: var(--bg-panel-right);'):
                    with ui.row().classes('w-full items-center q-mb-sm justify-between'):
                        with ui.row().classes('items-baseline').style('gap:10px;'):
                            ui.label('⚡ 实时渲染队列').classes('text-h6').style('color: var(--text-accent);')
                            _queue_sum_lbl = ui.label('').classes('text-xs').style('color:#8A8175;')
                        with ui.row().classes('items-center').style('gap:7px;'):
                            _fav_filter = {'on': False}
                            _fav_chip = ui.element('div').classes('qchip')
                            with _fav_chip:
                                ui.html('⭐ 仅看收藏')
                            _fav_chip.on('click', lambda: _toggle_fav_filter())
                            ui.button('🧹 清除已完成', on_click=lambda: (_clear_done(), ui.notify('已清理'))).props('flat color=grey-6 dense no-caps')
                    # 整体进度 + ETA（有活跃任务才显示）
                    _qsum_row = ui.row().classes('w-full items-center q-mb-md').style('gap:10px;'); _qsum_row.visible = False
                    with _qsum_row:
                        _qsum_track = ui.element('div').classes('qsum-track')
                        with _qsum_track:
                            _qsum_fill = ui.element('div').classes('qsum-fill').style('width:0%;')
                        _qsum_eta = ui.label('').classes('text-xs').style('color:#8A8175;white-space:nowrap;')

                    queue_container = ui.column().classes('w-full')
                    _empty_lbl = ui.column().classes('w-full items-center q-pa-xl').style('gap:12px;')
                    with _empty_lbl:
                        ui.html('<div style="font-size:46px;opacity:0.5;">🖼️</div>')
                        ui.label('还没有渲染任务').classes('text-subtitle1').style('color:#9C9486; font-weight:700;')
                        ui.html('<div style="font-size:12.5px;color:#B5AC9C;line-height:1.7;max-width:300px;text-align:center;">'
                                '在左侧上传地板图、套用一个配方，点击 <b style="color:#C15F3C;">⚡ 生成</b>，'
                                '结果会实时出现在这里，B2 / Pro 并排对比。</div>')

                    # ── 单卡创建（只调用一次，之后原地更新） ──────────────────
                    def _add_job_card(job: JobRecord):
                        """在队列顶部插入一张新卡，记录所有元素引用。"""
                        _empty_lbl.visible = False
                        _STATUS = {
                            'queued':  ('⏳', '排队中',   ''),
                            'running': ('⚡', '生成中…', 'color:#f39c12'),
                            'done':    ('✅', '完成',     'color:#27ae60'),
                            'partial': ('⚠️', '部分完成', 'color:#e67e22'),
                            'failed':  ('❌', '失败',     'color:#e74c3c'),
                        }
                        icon, txt, _ = _STATUS.get(job.status, ('?', job.status, ''))
                        single_model = job.model_filter in ('b2', 'pro')
                        # 弹性宽度 + 自动换行：槽位可按需显示/隐藏而不破坏布局
                        box_style = 'flex:1 1 240px; min-width:200px;'
                        def _nav_bar(slot):
                            """图槽底部候选导航条：‹ n/N ›（左右切换该槽历次重抽候选）。默认隐藏，>1 张才显示。
                            _nav_slot 为前向引用(同 main_page 闭包，点击时才解析)；lambda 捕获 job/slot 避免闭包陷阱。"""
                            nav = ui.row().classes('items-center justify-center w-full no-wrap cand-nav').style('gap:4px;'); nav.visible = False
                            with nav:
                                _pv = ui.button('‹', on_click=lambda j=job, s=slot: _nav_slot(j, s, -1)).props('flat dense round size=sm').classes('cand-arrow').tooltip('上一张候选')
                                _ct = ui.label('1/1').classes('text-xs cand-cnt')
                                _nx = ui.button('›', on_click=lambda j=job, s=slot: _nav_slot(j, s, 1)).props('flat dense round size=sm').classes('cand-arrow').tooltip('下一张候选')
                            return nav, _pv, _ct, _nx
                        def _img_box(caption, slot):
                            box = ui.element('div').classes('relative rounded bg-black').style(box_style)
                            with box:
                                ui.label(caption).classes('text-xs text-center w-full q-pa-xs').style('color: var(--text-secondary);')
                                _im = ui.image('').classes('rounded w-full').style('max-height:260px;object-fit:contain;cursor:zoom-in;'); _im.visible = False
                                _nav, _pv, _ct, _nx = _nav_bar(slot)
                                _dl = ui.html('')
                            return box, _im, _dl, _nav, _pv, _ct, _nx
                        with queue_container:
                            card = ui.element('div').classes('job-card w-full q-pa-sm q-mb-sm')
                            with card:
                                with ui.row().classes('w-full justify-between items-center q-mb-xs'):
                                    ui.label(job.display_name).classes('text-sm font-bold').style('color: var(--text-primary);')
                                    with ui.row().classes('items-center').style('gap:6px'):
                                        status_lbl = ui.label(f'{icon} {txt}').classes('text-xs')
                                        fav_btn = ui.button('⭐' if job.favorite else '☆', on_click=lambda j=job: _toggle_job_fav(j)).props('flat dense round size=sm').style('color:#C9A06A;').tooltip('收藏（用右上「⭐ 仅看收藏」筛选）')
                                        stop_single_btn = ui.button('⏹', on_click=lambda j=job: _request_stop_job(j)).props('flat color=red-8 dense round size=sm').tooltip('停止此任务')
                                        stop_single_btn.visible = False  # 排队中或运行中才显示
                                        retry_btn = ui.button('🔁', on_click=lambda j=job: _retry_job(j)).props('flat color=primary dense round size=sm').tooltip('重新生成失败的部分')
                                        retry_btn.visible = False  # 失败/部分完成才显示
                                        # 重抽胶囊：🔄重抽 │ ×N（连体；逐张串行多抽）
                                        regen_pod = ui.element('div').classes('regen-pod')
                                        with regen_pod:
                                            regen_btn = ui.button('🔄 重抽', on_click=lambda j=job: _regen_job_n(j, (_job_ui[j.job_id]['regenN_sel'].value if _job_ui.get(j.job_id, {}).get('regenN_sel') else 6))).props('flat dense no-caps').tooltip('逐张多抽：按右侧张数一张一张重出，全部追加到同一记录；满意了点「停止多抽」停掉剩余')
                                            ui.element('div').classes('sepv')
                                            regenN_sel = ui.select({1:'×1', 2:'×2', 4:'×4', 6:'×6'}, value=6).props('dense options-dense borderless').classes('regen-n').style('min-width:44px').tooltip('多抽张数：一张一张串行重出 N 张候选挑一张（圆弧倒角建议 ×4/×6）')
                                        regen_pod.visible = False  # 完成/部分完成才显示
                                        # 批次运行时显示「停止多抽 (i/n)」红橙药丸（与胶囊互斥）
                                        batch_stop_btn = ui.button('⏹ 停止多抽', on_click=lambda j=job: _request_stop_batch(j)).props('flat dense no-caps').classes('regen-batch-stop').tooltip('停掉尚未开始的剩余候选（正在跑的那张会跑完）')
                                        batch_stop_btn.visible = False
                                    ui.label(f'🕐 {job.ts}').classes('text-xs').style('color: var(--text-secondary);')
                                time_lbl = ui.label('').classes('text-xs').style('color: var(--text-secondary);'); time_lbl.visible = False
                                # 失败提示：暖橙 ⓘ 图标(区别于设置里灰色 ℹ️)+短原因标题；点图标弹完整详情
                                err_row = ui.row().classes('items-center no-wrap q-mt-xs').style('gap:5px'); err_row.visible = False
                                with err_row:
                                    err_info = ui.icon('info', size='sm').classes('cursor-pointer').style('color:#E0852E;')
                                    with err_info:
                                        ui.tooltip('点击查看失败原因与完整报错').style('font-size:12px;')
                                    err_info.on('click', lambda _=None, j=job: _open_fail_detail(j.error))
                                    err_lbl = ui.label('').classes('text-xs').style('color:#B5713A; font-weight:600;')
                                img_row = ui.row().classes('w-full q-mt-xs').style('gap:6px; flex-wrap:wrap;'); img_row.visible = False
                                pro_polish_btn = None
                                b2_nav = b2_prev = b2_cnt = b2_next = None
                                pro_nav = pro_prev = pro_cnt = pro_next = None
                                with img_row:
                                    if job.model_filter in ('b2', 'both'):
                                        _, b2_img, b2_dl, b2_nav, b2_prev, b2_cnt, b2_next = _img_box('B2', 'b2')
                                        b2_img.on('click', lambda _=None, j=job: _open_zoom_list([_to_url(p) for p in j.b2_paths], j.b2_idx) if j.b2_paths else None)
                                    else:
                                        b2_img = None; b2_dl = None
                                    if job.model_filter in ('pro', 'both'):
                                        pro_box = ui.element('div').classes('relative rounded bg-black').style(box_style)
                                        with pro_box:
                                            ui.label('Pro').classes('text-xs text-center w-full q-pa-xs').style('color: var(--text-secondary);')
                                            pro_img = ui.image('').classes('rounded w-full').style('max-height:260px;object-fit:contain;cursor:zoom-in;'); pro_img.visible = False
                                            pro_img.on('click', lambda _=None, j=job: _open_zoom_list([_to_url(p) for p in j.pro_paths], j.pro_idx) if j.pro_paths else None)
                                            pro_nav, pro_prev, pro_cnt, pro_next = _nav_bar('pro')
                                            pro_dl  = ui.html('')
                                            pro_polish_btn = ui.button('🪄 磨缝', on_click=lambda j=job: _polish_pro(j)).props('flat dense no-caps').classes('polish-btn'); pro_polish_btn.visible = False
                                        # 磨缝结果不再单独成槽：直接并入 Pro 候选（add_candidate(job,'pro',...)），用 Pro 槽 ‹n/N› 切换
                                    else:
                                        pro_img = None; pro_dl = None
                            _job_ui[job.job_id] = dict(
                                card=card, status_lbl=status_lbl, fav_btn=fav_btn, err_lbl=err_lbl, err_row=err_row, time_lbl=time_lbl,
                                img_row=img_row, b2_img=b2_img, b2_dl=b2_dl,
                                pro_img=pro_img, pro_dl=pro_dl,
                                pro_polish_btn=pro_polish_btn,
                                single_model=single_model,
                                stop_btn=stop_single_btn, retry_btn=retry_btn, regen_btn=regen_btn,
                                regenN_sel=regenN_sel, regen_pod=regen_pod, batch_btn=batch_stop_btn,
                                # 候选导航条（每槽各自翻）：键名 = f'{slot}_nav/_prev/_cnt/_next'，供 _apply_slot_nav 复用
                                b2_nav=b2_nav, b2_prev=b2_prev, b2_cnt=b2_cnt, b2_next=b2_next,
                                pro_nav=pro_nav, pro_prev=pro_prev, pro_cnt=pro_cnt, pro_next=pro_next,
                            )
                            card.move(target_index=0)   # 新卡插到队列顶部（newest-first，与 _job_history 一致）

                    # 耗时文案 _job_time_text 已下沉到 models.job_time_text（纯函数）；调用点直接用 job_time_text。

                    # ── v7：队列收藏过滤 + 整体进度/ETA 汇总 ──
                    def _apply_fav_filter():
                        on = _fav_filter['on']
                        for j in list(_job_history):
                            refs = _job_ui.get(j.job_id)
                            card = refs.get('card') if refs else None
                            if card is not None:
                                card.visible = (j.favorite or not on)

                    def _toggle_fav_filter():
                        _fav_filter['on'] = not _fav_filter['on']
                        (_fav_chip.classes(add='qchip--on') if _fav_filter['on']
                         else _fav_chip.classes(remove='qchip--on'))
                        _apply_fav_filter()

                    def _toggle_job_fav(j):
                        j.favorite = not j.favorite
                        refs = _job_ui.get(j.job_id)
                        if refs and refs.get('fav_btn'):
                            refs['fav_btn'].set_text('⭐' if j.favorite else '☆')
                        _apply_fav_filter()

                    def _render_queue_summary():
                        active = [j for j in _job_history if j.status in ('queued', 'running')]
                        if not active:
                            _qsum_row.visible = False
                            _queue_sum_lbl.text = ''
                            return
                        total = done = remaining = 0
                        for j in _job_history:
                            slots = 2 if j.model_filter == 'both' else 1
                            d = ((1 if (j.model_filter in ('b2', 'both') and j.b2_path) else 0)
                                 + (1 if (j.model_filter in ('pro', 'both') and j.pro_path) else 0))
                            total += slots; done += min(d, slots)
                        for j in active:
                            slots = 2 if j.model_filter == 'both' else 1
                            d = (1 if j.b2_path else 0) + (1 if j.pro_path else 0)
                            remaining += max(0, slots - d)
                        pct = int(done * 100 / total) if total else 0
                        # ETA：最近若干张实测出图秒数均值 × 剩余槽（_job_history 为 newest-first）
                        secs = []
                        for j in _job_history:
                            if j.b2_secs: secs.append(j.b2_secs)
                            if j.pro_secs: secs.append(j.pro_secs)
                            if len(secs) >= 12: break
                        avg = (sum(secs) / len(secs)) if secs else None
                        _qsum_row.visible = True
                        _qsum_fill.style(f'width:{pct}%;')
                        _qsum_eta.text = (f'整体 {pct}% · 预计还 ~{int(avg * remaining)}s'
                                          if (avg and remaining) else f'整体 {pct}%')
                        _queue_sum_lbl.text = f'{len(active)} 个进行中'

                    # 每秒刷新"运行中"任务的已用时间
                    def _tick_job_times():
                        for j in list(_job_history):
                            if j.status == 'running':
                                refs = _job_ui.get(j.job_id)
                                tl = refs.get('time_lbl') if refs else None
                                if tl is not None:
                                    try:
                                        tl.text = job_time_text(j); tl.visible = True
                                    except Exception as ex: logger.debug(f"刷新耗时标签失败 job={j.job_id}: {ex}")
                                # 徽章也随秒刷：分模型态随「排队中→生成中→✓」实时变(设同值 no-op、无闪烁)
                                if refs is not None:
                                    try: _apply_status_badge(refs, j)
                                    except Exception as ex: logger.debug(f"刷新分模型徽章失败 job={j.job_id}: {ex}")
                        try:
                            _render_queue_summary()
                        except Exception as ex:
                            logger.debug(f"刷新队列汇总失败: {ex}")

                    # ── 原地更新（改属性，不销毁 DOM，不闪烁）→ 拆成若干 _apply_* 小步，逐块独立 ──
                    # 各 _apply_* 只读模块级全局 + 入参 (refs, job)，不捕获 main_page 局部，便于阅读/将来外迁。
                    _STATUS_REFRESH = {
                        'queued':  ('⏳', '排队中'),
                        'running': ('⚡', '生成中…'),
                        'done':    ('✅', '完成'),
                        'partial': ('⚠️', '部分完成'),
                        'failed':  ('❌', '失败'),
                    }

                    def _apply_status_badge(refs, job):
                        # 运行中：按模型分开显示「生成中 / 排队中 / ✓」(排队中=在等模型并发槽，跨任务流水线时常见)；
                        # 其余状态(排队前/完成/部分/失败)仍走 job 级单徽章。
                        if job.status == 'running':
                            _txt = running_model_status_text(job)
                            refs['status_lbl'].text = f'⚡ {_txt}' if _txt else '⚡ 生成中…'
                        else:
                            icon, txt = _STATUS_REFRESH.get(job.status, ('?', job.status))
                            refs['status_lbl'].text = f'{icon} {txt}'

                    def _apply_action_buttons(refs, job):
                        # 停止按钮：排队中或运行中显示
                        stop_btn = refs.get('stop_btn')
                        if stop_btn:
                            try: stop_btn.visible = (job.status in ('queued', 'running'))
                            except Exception as ex: logger.warning(f"更新停止按钮状态失败: {ex}")
                        # 重试按钮：失败/部分完成 且 存在重试上下文 才显示
                        retry_btn = refs.get('retry_btn')
                        if retry_btn:
                            try: retry_btn.visible = (job.status in ('failed', 'partial')) and bool(job.retry_ctx)
                            except Exception as ex: logger.warning(f"更新重试按钮状态失败: {ex}")
                        # 再生成胶囊：完成/部分完成 且 有上下文 才显示；批次运行时让位给「停止多抽」药丸（互斥）
                        _regen_vis = (job.status in ('done', 'partial')) and bool(job.retry_ctx)
                        _batch = _regen_batches.get(job.job_id)
                        _batch_running = bool(_batch and _batch.get('running'))
                        regen_pod = refs.get('regen_pod')
                        if regen_pod:
                            try: regen_pod.visible = _regen_vis and not _batch_running
                            except Exception as ex: logger.warning(f"更新重抽胶囊状态失败: {ex}")
                        batch_stop_btn = refs.get('batch_btn')
                        if batch_stop_btn:
                            try:
                                if _batch:
                                    batch_stop_btn.text = (f'⏳ 停止中·当前跑完即止 ({_batch.get("i",0)}/{_batch.get("n",0)})'
                                                           if _batch.get('cancel') else
                                                           f'⏹ 停止多抽 ({_batch.get("i",0)}/{_batch.get("n",0)})')
                                batch_stop_btn.visible = _batch_running
                            except Exception as ex: logger.warning(f"更新停止多抽按钮状态失败: {ex}")

                    def _apply_border_and_time(refs, job):
                        border_var = {'done':'var(--border-success)','partial':'var(--border-partial)','failed':'var(--border-failed)','running':'var(--border-running)'}.get(job.status,'')
                        if border_var: refs['card'].style(f'border-left:4px solid {border_var};')
                        tl = refs.get('time_lbl')
                        if tl is not None:
                            txt2 = job_time_text(job)
                            if txt2:
                                tl.text = txt2; tl.visible = True
                            else:
                                tl.visible = False

                    def _apply_error(refs, job):
                        if job.error:
                            refs['err_lbl'].text = classify_failure(job.error)['title']
                            if refs.get('err_row') is not None: refs['err_row'].visible = True
                        elif refs.get('err_row') is not None:
                            refs['err_row'].visible = False

                    def _apply_slot_nav(refs, job, slot):
                        """更新某槽候选导航条 ‹n/N›：>1 张才显示；到两端禁用对应箭头。"""
                        nav = refs.get(f'{slot}_nav')
                        if nav is None: return
                        paths = getattr(job, f'{slot}_paths', None) or []
                        n = len(paths)
                        try:
                            if n > 1:
                                idx = max(0, min(getattr(job, f'{slot}_idx', 0), n - 1))
                                refs[f'{slot}_cnt'].text = f'{idx + 1}/{n}'
                                nav.visible = True
                                pv = refs.get(f'{slot}_prev'); nx = refs.get(f'{slot}_next')
                                if pv is not None: pv.set_enabled(idx > 0)
                                if nx is not None: nx.set_enabled(idx < n - 1)
                            else:
                                nav.visible = False
                        except Exception as ex:
                            logger.debug(f"更新候选导航失败 job={job.job_id} slot={slot}: {ex}")

                    def _apply_images(refs, job):
                        has_img = False
                        if job.b2_path and os.path.exists(str(job.b2_path)) and refs.get('b2_img') is not None:
                            url = _to_url(job.b2_path)
                            refs['b2_img'].set_source(_result_thumb_url(job.b2_path)); refs['b2_img'].visible = True
                            refs['b2_dl'].content = f'<a href="{url}" download="{os.path.basename(str(job.b2_path))}" class="dl-btn">⬇️ B2</a>'
                            has_img = True
                        if job.pro_path and os.path.exists(str(job.pro_path)) and refs.get('pro_img') is not None:
                            url = _to_url(job.pro_path)
                            refs['pro_img'].set_source(_result_thumb_url(job.pro_path)); refs['pro_img'].visible = True
                            refs['pro_dl'].content = f'<a href="{url}" download="{os.path.basename(str(job.pro_path))}" class="dl-btn">⬇️ Pro</a>'
                            has_img = True
                            # 磨缝按钮：有 Pro 图即显示（磨缝结果并入 Pro 候选，可对当前显示的那张 Pro 反复磨）；磨缝中显示「磨缝中…」
                            btn = refs.get('pro_polish_btn')
                            if btn is not None:
                                try:
                                    btn.visible = True
                                    if job.pro_polishing:
                                        btn.text = '磨缝中…'; btn.props('loading')
                                    else:
                                        btn.text = '🪄 磨缝'; btn.props(remove='loading')
                                except Exception as ex: logger.warning(f"更新磨缝按钮失败: {ex}")
                        if has_img: refs['img_row'].visible = True
                        # 候选导航条（每槽各自翻；>1 张才显示）
                        for _slot in CANDIDATE_SLOTS:
                            _apply_slot_nav(refs, job, _slot)

                    def _refresh_job_card(job: JobRecord):
                        # 终态时落盘一次（低频），重启后可恢复卡片
                        if job.status in ('done', 'partial', 'failed'):
                            _persist_jobs()
                        refs = _job_ui.get(job.job_id)
                        if not refs: return
                        _apply_status_badge(refs, job)
                        _apply_action_buttons(refs, job)
                        _apply_border_and_time(refs, job)
                        _apply_error(refs, job)
                        _apply_images(refs, job)

                    def _nav_slot(job: JobRecord, slot: str, delta: int):
                        """左右切换某槽当前候选：移动下标 → 同步 *_path → 重渲染该卡。"""
                        nav_candidate(job, slot, delta)
                        try: _refresh_job_card(job)
                        except Exception as ex: logger.debug(f"切换候选刷新失败 job={job.job_id} slot={slot}: {ex}")

                    # 按需磨缝：对某个任务的 Pro 成图做一次图生图去缝 + 色彩对齐
                    async def _polish_pro(job: JobRecord):
                        if not job.pro_path or not os.path.exists(str(job.pro_path)):
                            ui.notify('⚠️ 没有可磨缝的 Pro 图', type='warning'); return
                        if job.pro_polishing:
                            return
                        api_key = api_key_inp.value.strip() or _load_config().get('gemini_api_key', '').strip()
                        if not api_key:
                            ui.notify('⚠️ 缺少 API Key', type='warning'); return
                        _update_job(job, pro_polishing=True)
                        try: _refresh_job_card(job)
                        except Exception as ex: logger.debug(f"刷新磨缝卡片失败 job={job.job_id}: {ex}")
                        ui.notify('已提交磨缝，请稍候…', type='info')
                        try:
                            src_pil = Image.open(job.pro_path); src_pil.load()
                            _buf = _io_mod.BytesIO(); src_pil.convert('RGB').save(_buf, format='JPEG', quality=95)
                            _b64 = base64.b64encode(_buf.getvalue()).decode()
                            _ar = _infer_aspect_ratio_from_b64(_b64)
                            _t0 = time.time()
                            # on_stage 在 worker 线程只写 job.pro_stage(字符串)，由 1 秒 ui.timer 读 → 磨缝重试期间显示「网络重试 N/M」而非卡死。
                            def _polish_on_stage(text, _j=job):
                                try: _j.pro_stage = text
                                except Exception as ex: logger.debug(f"写入磨缝阶段失败 job={_j.job_id}: {ex}")
                            # 占 Pro 并发槽再发请求：有任务在跑时磨缝排队等空槽（与生成同一把锁，每模型≤1 进行中）
                            async with _pro_semaphore:
                                polished, perr = await asyncio.to_thread(
                                    call_gemini_edit, api_key, GEMINI_MODEL_MAP['Nano Banana Pro'],
                                    FLOOR_DESEAM_INSTRUCTION, _b64, '4K', _ar, False,
                                    _polish_on_stage, (lambda: _is_cancelled(job.job_id))
                                )
                            if polished is None:
                                ui.notify(f'❌ 磨缝失败：{perr}', type='negative'); return
                            # 色彩对齐回原图，消除 img2img 偏色
                            try:
                                polished = await asyncio.to_thread(_match_color_to_reference, polished, src_pil)
                            except Exception as ex:
                                logger.warning(f"[磨缝] 色彩对齐失败(用未对齐图) job={job.job_id}: {ex}")
                            ppath = _save_api_result_jpg(polished, 'Nano Banana Pro_磨缝', job.png_path or job.pro_path)
                            add_candidate(job, 'pro', ppath)   # 磨缝结果并入 Pro 候选→跳到磨缝图，‹n/N› 可切回原 Pro 对比
                            if job.json_path and job.record_id:
                                try:
                                    await asyncio.to_thread(_api_write_to_record, polished, 'Nano Banana Pro 磨缝', job.json_path, job.record_id, ppath)
                                except Exception as ex:
                                    logger.warning(f"[磨缝] 写入记录失败 job={job.job_id}: {ex}")
                            logger.info(f"[磨缝] 完成 job={job.job_id}, +{time.time()-_t0:.1f}s, path={ppath}")
                            ui.notify('✅ 磨缝完成', type='positive')
                        except Exception as e:
                            logger.exception(f"[磨缝] 异常 job={job.job_id}")
                            ui.notify(f'❌ 磨缝异常：{e}', type='negative')
                        finally:
                            _update_job(job, pro_polishing=False, pro_stage='')
                            try: _refresh_job_card(job)
                            except Exception as ex: logger.debug(f"刷新磨缝卡片失败 job={job.job_id}: {ex}")

                    # 清除按钮同时清理 refs
                    def _clear_done():
                        done_ids = {j.job_id for j in _job_history if j.status in ('done','partial','failed')}
                        for jid in done_ids:
                            # 源卡被清除前，若它有进行中的多抽批次 → 通知 driver 停掉后续
                            _b = _regen_batches.get(jid)
                            if _b: _b['cancel'] = True
                            _regen_batches.pop(jid, None)
                            refs = _job_ui.pop(jid, None)   # 所有 UI 引用已并入 _job_ui，一次性清掉
                            if refs:
                                try: refs['card'].delete()
                                except Exception as ex: logger.warning(f"删除任务卡片失败: {ex}")
                        _job_history[:] = [j for j in _job_history if j.job_id not in done_ids]
                        if not _job_history: _empty_lbl.visible = True
                        _persist_jobs()

                    # ── 页面加载/刷新时恢复已有任务 ──────────────────────────
                    # _job_history 跨 session 保留（模块级），但 DOM 元素绑定旧
                    # session。新 session 打开时重建所有卡片，覆盖 _job_ui，
                    # 后续的 _refresh_job_card 调用自动打到新页面的元素上。
                    # 只恢复最新 _MAX_RESIDENT_CARDS 张卡：更旧的任务仍在记录管理里可查，
                    # 不必一启动就把几十张全建出来(DOM + 解码大图)。_job_history[:N] 是最新的(newest-first)。
                    for _existing_job in reversed(list(_job_history)[:_MAX_RESIDENT_CARDS]):
                        _add_job_card(_existing_job)
                        _refresh_job_card(_existing_job)


        # ==================================
        # TAB 2: 记录管理
        # ==================================
        with ui.tab_panel('records').classes('q-pa-sm'):
            _rec_state = {'json_path': '', 'query': '', 'fav_only': False, 'room_filter': ''}
            _op_ctx = {}  # 共享 dialog 的操作上下文 {jp, rid, idx, b64}

            # ── 全屏放大 dialog（滚轮缩放 + 拖动平移；构建一次，点背景关闭）──
            # 用原生 <img> 而非 ui.image(q-img)：q-img 的 ratio 盒会把图按默认比例(常见 16:9)撑开，
            # 4:3/竖图就会被显示成错误比例；原生 img + max-w/max-h 永远按图片自身比例显示。
            # 缩放/平移全在前端 JS：滚轮以光标为中心缩放(1~8x)、放大后可拖动、双击复位。
            with ui.dialog().props('maximized') as _zoom_dlg:
                _zoom_stage = ui.element('div').style(
                    'position:fixed; inset:0; background:rgba(0,0,0,0.92); overflow:hidden; '
                    'display:flex; align-items:center; justify-content:center; cursor:zoom-out;')
                with _zoom_stage:
                    ui.html(
                        '<img id="floorZoomImg" draggable="false" onclick="event.stopPropagation()" '
                        'style="max-width:96vw; max-height:96vh; object-fit:contain; '
                        'transform-origin:center center; cursor:zoom-out; '
                        'user-select:none; -webkit-user-drag:none;">'
                        # 左右翻候选箭头 + 计数浮层（单张时隐藏；onclick stopPropagation 防误触背景关闭）
                        '<button id="floorZoomPrev" onclick="event.stopPropagation();window.floorZoomNav&&floorZoomNav(-1)" '
                        'style="display:none; position:fixed; left:18px; top:50%; transform:translateY(-50%); z-index:10; '
                        'width:52px; height:52px; border:none; border-radius:50%; background:rgba(26,24,21,0.55); color:#fff; '
                        'font-size:30px; line-height:52px; cursor:pointer;">‹</button>'
                        '<button id="floorZoomNext" onclick="event.stopPropagation();window.floorZoomNav&&floorZoomNav(1)" '
                        'style="display:none; position:fixed; right:18px; top:50%; transform:translateY(-50%); z-index:10; '
                        'width:52px; height:52px; border:none; border-radius:50%; background:rgba(26,24,21,0.55); color:#fff; '
                        'font-size:30px; line-height:52px; cursor:pointer;">›</button>'
                        '<div id="floorZoomCnt" onclick="event.stopPropagation()" '
                        'style="display:none; position:fixed; top:18px; left:50%; transform:translateX(-50%); z-index:10; '
                        'background:rgba(26,24,21,0.6); color:#fff; padding:4px 14px; border-radius:12px; '
                        'font-size:14px; font-weight:600;"></div>')
                _zoom_stage.on('click', lambda _=None: _zoom_dlg.close())
            # 缩放/平移交互一次性注入（lazy 绑定：首次 show 时挂监听，避免依赖 DOM-ready 时序）
            ui.add_body_html('''
<script>
// 复位缩放/平移到 1x（切换候选或首次显示时调用）
window._floorZoomReset = function(im) {
  im._scale = 1; im._tx = 0; im._ty = 0;
  im.style.transform = 'translate(0px,0px) scale(1)';
  im.style.cursor = 'zoom-out';
};
// 跳到第 i 张候选：换 src、复位缩放、刷新计数与箭头显隐
window._floorZoomAt = function(i) {
  const im = document.getElementById('floorZoomImg');
  if (!im || !im._urls || !im._urls.length) return;
  const n = im._urls.length;
  im._idx = Math.max(0, Math.min(i, n - 1));
  im.src = im._urls[im._idx];
  window._floorZoomReset(im);
  const cnt = document.getElementById('floorZoomCnt');
  const pv = document.getElementById('floorZoomPrev');
  const nx = document.getElementById('floorZoomNext');
  const multi = n > 1;
  if (cnt) { cnt.style.display = multi ? 'block' : 'none'; cnt.textContent = (im._idx + 1) + ' / ' + n; }
  if (pv) { pv.style.display = multi ? 'block' : 'none'; pv.style.opacity = im._idx > 0 ? '1' : '0.35'; }
  if (nx) { nx.style.display = multi ? 'block' : 'none'; nx.style.opacity = im._idx < n - 1 ? '1' : '0.35'; }
};
window.floorZoomNav = function(d) {
  const im = document.getElementById('floorZoomImg');
  if (!im || !im._urls) return;
  window._floorZoomAt((im._idx || 0) + d);
};
// urls 可为字符串(单张)或数组(候选列表)；startIdx 起始下标
window.floorZoomShow = function(urls, startIdx) {
  const im = document.getElementById('floorZoomImg');
  if (!im) return;
  im._urls = Array.isArray(urls) ? urls : [urls];
  window._floorZoomAt(startIdx || 0);
  // 键盘 ←/→ 翻候选（仅弹窗可见时）；一次性绑定
  if (!window._floorZoomKeyWired) {
    window._floorZoomKeyWired = true;
    window.addEventListener('keydown', function(e) {
      const el = document.getElementById('floorZoomImg');
      if (!el || !el._urls || el.offsetParent === null) return;  // 弹窗未显示时不拦截
      if (e.key === 'ArrowLeft') { e.preventDefault(); window.floorZoomNav(-1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); window.floorZoomNav(1); }
    });
  }
  if (im._wired) return;
  im._wired = true;
  const apply = function() {
    im.style.transform = 'translate(' + im._tx + 'px,' + im._ty + 'px) scale(' + im._scale + ')';
    im.style.cursor = im._scale > 1 ? 'grab' : 'zoom-out';
  };
  im.addEventListener('wheel', function(e) {
    e.preventDefault();
    const r = im.getBoundingClientRect();
    const offX = e.clientX - (r.left + r.width / 2);
    const offY = e.clientY - (r.top + r.height / 2);
    const prev = im._scale || 1;
    const next = Math.min(8, Math.max(1, prev * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    const k = next / prev;
    if (next === 1) { im._tx = 0; im._ty = 0; }
    else { im._tx += offX * (1 - k); im._ty += offY * (1 - k); }
    im._scale = next;
    apply();
  }, { passive: false });
  let drag = false, sx = 0, sy = 0, bx = 0, by = 0;
  im.addEventListener('mousedown', function(e) {
    if ((im._scale || 1) <= 1) return;
    e.preventDefault();
    drag = true; sx = e.clientX; sy = e.clientY; bx = im._tx; by = im._ty;
    im.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', function(e) {
    if (!drag) return;
    im._tx = bx + (e.clientX - sx); im._ty = by + (e.clientY - sy); apply();
  });
  window.addEventListener('mouseup', function() { if (drag) { drag = false; apply(); } });
  im.addEventListener('dblclick', function(e) {
    e.preventDefault(); im._scale = 1; im._tx = 0; im._ty = 0; apply();
  });
};
</script>''')

            def _open_zoom_list(urls, start=0):
                """全屏放大一组候选(可左右切换/方向键翻)。urls: URL 列表；start: 起始下标。"""
                import json as _json
                urls = [u for u in (urls or []) if u]
                if not urls: return
                start = max(0, min(int(start or 0), len(urls) - 1))
                _zoom_dlg.open()
                ui.run_javascript(f'window.floorZoomShow && window.floorZoomShow({_json.dumps(urls)}, {start})')

            def _open_zoom(data_url):
                """单图放大（记录管理 tab 等调用方）：等价于只有一张候选的列表。"""
                if not data_url: return
                _open_zoom_list([data_url], 0)

            # ── 二改 dialog（构建一次，复用）──
            with ui.dialog() as _edit_dlg, ui.card().classes('q-pa-md').style('min-width:440px; max-width:600px;'):
                ui.label('✏️ 二次修改').classes('text-h6').style('color: var(--text-accent);')
                _edit_src_lbl = ui.label('').classes('text-xs').style('color: var(--text-secondary);')
                _edit_prompt = ui.textarea(
                    label='二次修改建议',
                    placeholder='例如：移除地毯，补全被遮挡的地板；去掉蓝色茶壶；保持其他构图、光线和地板不变。'
                ).classes('w-full').props('rows=3 outlined dense')
                with ui.row().classes('w-full items-center').style('gap:6px;'):
                    _edit_model = ui.select(
                        ['Nano Banana Pro', 'Nano Banana 2'], value='Nano Banana Pro', label='编辑模型'
                    ).classes('flex-1').props('outlined dense')
                    _edit_status = ui.label('').classes('text-xs').style('color: var(--text-secondary);')
                _edit_keep = ui.checkbox('保持原图色彩（防偏色）', value=True).classes('text-xs').tooltip(
                    '二改后自动把整体色温/饱和度拉回原图，消除偏色。若本次就是想改颜色，请取消勾选。')
                async def _do_edit():
                    jp = _op_ctx.get('jp'); rid = _op_ctx.get('rid'); idx = _op_ctx.get('idx')
                    src = _op_ctx.get('b64', '')
                    if not src:
                        _fr = _op_ctx.get('file_rel', '')
                        if _fr:
                            try:
                                with open(os.path.join(MAIN_OUTPUT_DIR, _fr), 'rb') as _f:
                                    src = base64.b64encode(_f.read()).decode()
                            except Exception as ex:
                                logger.warning(f"二改读取源图失败: {ex}")
                    txt = (_edit_prompt.value or '').strip()
                    if not txt:
                        ui.notify('⚠️ 请填写修改建议', type='warning'); return
                    api_key = api_key_inp.value.strip() or _load_config().get('gemini_api_key', '').strip()
                    if not api_key:
                        ui.notify('⚠️ 缺少 API Key', type='warning'); return
                    if not src:
                        ui.notify('⚠️ 该图无图片数据', type='warning'); return
                    model_key = _edit_model.value or 'Nano Banana Pro'
                    model_id = GEMINI_MODEL_MAP.get(model_key, GEMINI_MODEL_MAP['Nano Banana Pro'])
                    ar = _infer_aspect_ratio_from_b64(src)
                    _edit_status.text = '正在二次修改…'
                    ui.notify('已提交二次修改，请稍候', type='info')
                    # 二改也吃软路由重置 → 开 timer 把 worker 线程回传的「网络重试 N/M」显示到 _edit_status，
                    # 不再让用户对着不动的对话框干等。on_stage 只写普通 dict，UI 由 timer 读（线程安全）。
                    _edit_stage['t'] = ''; _edit_stage_timer.active = True
                    try:
                        pil_img, err = await asyncio.to_thread(
                            call_gemini_edit, api_key, model_id, txt, src, '4K', ar, True, _edit_on_stage)
                    finally:
                        _edit_stage_timer.active = False; _edit_stage['t'] = ''
                    if err or pil_img is None:
                        _edit_status.text = f'❌ 失败：{err}'
                        ui.notify(f'❌ 二次修改失败：{err}', type='negative'); return
                    if _edit_keep.value:
                        try:
                            _ref = _b64_to_pil(src)
                            if _ref is not None:
                                pil_img = await asyncio.to_thread(_match_color_to_reference, pil_img, _ref)
                        except Exception as ex:
                            logger.warning(f"二次修改色彩对齐失败(用未对齐图): {ex}")
                    _saved_path = None
                    try:
                        _saved_path = _save_api_result_jpg(pil_img, f'{model_key}_Edit', f'{rid}_edit_优化图.png')
                    except Exception as ex:
                        logger.warning(f"二次修改图片落盘失败: {ex}")
                    msg = append_edited_result_to_record(jp, rid, idx, pil_img, txt, model_key, _saved_path)
                    if msg.startswith('✅'):
                        _edit_prompt.value = ''; _edit_status.text = ''
                        _edit_dlg.close(); ui.notify(msg, type='positive')
                        _render_entries(jp)
                    else:
                        _edit_status.text = msg
                        ui.notify(msg, type='negative' if msg.startswith('❌') else 'warning')
                with ui.row().classes('w-full justify-end').style('gap:6px;'):
                    ui.button('取消', on_click=lambda: _edit_dlg.close()).props('flat dense')
                    ui.button('🚀 生成', on_click=_do_edit).props('color=amber-8 dense')
            # 二改实时状态：worker 线程经 _edit_on_stage 只写 _edit_stage（普通 dict），
            # 由 0.5 秒 timer（默认停，二改进行时才开）读进 _edit_status，避免跨线程碰 UI 元素。
            _edit_stage = {'t': ''}
            def _edit_on_stage(text):
                _edit_stage['t'] = text
            def _edit_stage_tick():
                if _edit_stage['t']: _edit_status.text = _edit_stage['t']
            _edit_stage_timer = ui.timer(0.5, _edit_stage_tick, active=False)
            def _open_edit(jp, rid, idx, b64, file_rel=''):
                _op_ctx.update(jp=jp, rid=rid, idx=idx, b64=b64, file_rel=file_rel)
                _edit_src_lbl.text = f'修改对象：{rid} · 第 {idx + 1} 张'
                _edit_prompt.value = ''; _edit_status.text = ''
                _edit_dlg.open()

            # ── 解密提示词 dialog ──
            with ui.dialog() as _reveal_dlg, ui.card().classes('q-pa-md').style('min-width:440px;'):
                ui.label('🔑 查看原始提示词').classes('text-h6').style('color: var(--text-accent);')
                _reveal_key = ui.input('密钥', password=True).classes('w-full')
                _reveal_out = ui.textarea(label='原始英文提示词', value='').classes('w-full').props('rows=6 readonly outlined dense')
                def _do_reveal():
                    _reveal_out.value = reveal_prompt_fn(_op_ctx.get('jp'), _op_ctx.get('rid'), _reveal_key.value)
                with ui.row().classes('w-full justify-end').style('gap:6px;'):
                    ui.button('关闭', on_click=lambda: _reveal_dlg.close()).props('flat dense')
                    ui.button('🔓 解密', on_click=_do_reveal).props('color=amber dense')
            def _open_reveal(jp, rid):
                _op_ctx.update(jp=jp, rid=rid)
                _reveal_key.value = ''; _reveal_out.value = ''
                _reveal_dlg.open()

            # ── 追加效果图 dialog ──
            with ui.dialog() as _append_dlg, ui.card().classes('q-pa-md').style('min-width:480px;'):
                ui.label('📥 追加效果图').classes('text-h6').style('color: var(--text-accent);')
                _append_rec_box = ui.element('div').classes('w-full')  # 动态放目标记录选择器
                _ap = {'a': '', 'b': '', 'rid': None}
                async def _on_ap_a(e: ng_events.UploadEventArguments):
                    fn, content = await _extract_upload_data(e)
                    p = os.path.join(UPLOAD_DIR, 'mgr_a_' + fn.replace(' ', '_'))
                    with open(p, 'wb') as f_: f_.write(content)
                    _ap['a'] = p; ui.notify('A 已上传', type='positive')
                ui.upload(on_upload=_on_ap_a, auto_upload=True, max_files=1
                          ).props('accept=".jpg,.jpeg,.png" flat color=amber-8 dense').classes('w-full')
                _ap_ca = ui.input(label='备注 A').classes('w-full')
                async def _on_ap_b(e: ng_events.UploadEventArguments):
                    fn, content = await _extract_upload_data(e)
                    p = os.path.join(UPLOAD_DIR, 'mgr_b_' + fn.replace(' ', '_'))
                    with open(p, 'wb') as f_: f_.write(content)
                    _ap['b'] = p; ui.notify('B 已上传', type='positive')
                ui.upload(on_upload=_on_ap_b, auto_upload=True, max_files=1
                          ).props('accept=".jpg,.jpeg,.png" flat color=blue-7 dense').classes('w-full')
                _ap_cb = ui.input(label='备注 B').classes('w-full')
                def _do_append():
                    jp = _op_ctx.get('jp'); rid = _ap['rid']
                    if not rid:
                        ui.notify('⚠️ 请先选择目标记录', type='warning'); return
                    msg = append_result_to_log(_ap['a'] or None, _ap['b'] or None, jp, rid, _ap_ca.value, _ap_cb.value)
                    ui.notify(msg, type='positive' if msg.startswith('✅') else 'warning')
                    if msg.startswith('✅'):
                        _ap['a'] = ''; _ap['b'] = ''; _ap_ca.value = ''; _ap_cb.value = ''
                        _append_dlg.close(); _render_entries(jp)
                with ui.row().classes('w-full justify-end').style('gap:6px;'):
                    ui.button('取消', on_click=lambda: _append_dlg.close()).props('flat dense')
                    ui.button('📥 确认追加', on_click=_do_append).props('color=amber-8 dense')
            def _open_append(jp):
                if not jp:
                    ui.notify('⚠️ 请先选择左侧记录文件', type='warning'); return
                _op_ctx.update(jp=jp)
                _ap['a'] = ''; _ap['b'] = ''; _ap['rid'] = None
                _ap_ca.value = ''; _ap_cb.value = ''
                _append_rec_box.clear()
                with _append_rec_box:
                    labels = get_record_labels(jp) or []
                    opts = {v: l for l, v in labels}
                    def _pick(e): _ap['rid'] = e.value
                    ui.select(opts, value=None, label='追加到哪条记录', on_change=_pick).classes('w-full').props('outlined dense')
                _append_dlg.open()

            # ── 通用删除确认 dialog ──
            _confirm_cb = {'fn': None}
            with ui.dialog() as _confirm_dlg, ui.card().classes('q-pa-md'):
                _confirm_lbl = ui.label('').classes('text-sm').style('white-space:normal;')
                with ui.row().classes('w-full justify-end').style('gap:6px;'):
                    ui.button('取消', on_click=lambda: _confirm_dlg.close()).props('flat dense')
                    def _confirm_do():
                        _confirm_dlg.close()
                        if _confirm_cb['fn']: _confirm_cb['fn']()
                    ui.button('删除', on_click=_confirm_do).props('color=red-8 dense')
            def _ask_confirm(text, fn):
                _confirm_lbl.text = text; _confirm_cb['fn'] = fn; _confirm_dlg.open()

            def _del_result(jp, rid, idx):
                def _go():
                    if _delete_result_image(jp, rid, idx):
                        ui.notify('✅ 效果图已删除', type='positive'); _render_entries(jp)
                    else:
                        ui.notify('❌ 删除失败', type='negative')
                _ask_confirm(f'确认删除这张效果图（{rid} · 第 {idx + 1} 张）？', _go)
            def _del_record(jp, rid):
                def _go():
                    if _delete_record(jp, rid):
                        ui.notify('✅ 记录已删除', type='positive')
                        _refresh_room_options()   # 可能删掉了某房间类型的最后一条 → 选项同步
                        _render_entries(jp)
                    else:
                        ui.notify('❌ 删除失败', type='negative')
                _ask_confirm(f'确认删除整条记录 {rid}（含其所有效果图）？', _go)

            # ── 用量统计（可折叠）：不同模式 × 模型 × 线路 各出图/失败计数 ──
            with ui.expansion('📊 用量统计', icon='insights').classes('w-full').style(
                    'border:1px solid var(--border-panel); border-radius:8px; margin-bottom:6px;'):
                with ui.row().classes('w-full items-center').style('gap:8px;'):
                    _usage_total_lbl = ui.label('').classes('text-xs font-bold').style('color: var(--text-accent);')
                    ui.button(icon='refresh', on_click=lambda: _render_usage()).props(
                        'flat color=grey-6 dense size=sm').tooltip('刷新用量统计')
                _usage_container = ui.column().classes('w-full q-pa-xs').style('gap:2px;')

            # ── 主布局：左文件列表 / 右条目卡片 ──
            with ui.row().classes('w-full no-wrap').style('height:calc(100vh - 120px); gap:8px; overflow:hidden;'):
                # 左栏 30%
                with ui.column().style('width:30%; flex-shrink:0; height:100%; gap:6px;'):
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('📂 记录文件').classes('text-sm font-bold').style('color: var(--text-accent);')
                        ui.button(icon='refresh', on_click=lambda: (_render_file_list(), _refresh_room_options())).props('flat color=grey-6 dense').tooltip('刷新文件列表 / 房间筛选选项')
                    _rec_search = ui.input(placeholder='🔍 搜索材料名…').props('dense clearable outlined').classes('w-full')
                    _rec_search.on_value_change(lambda e: (_rec_state.update(query=(e.value or '')), _render_file_list()))
                    with ui.row().classes('w-full items-center justify-between'):
                        _fav_only_sw = ui.switch('⭐ 只看收藏', value=False).props('dense').classes('text-xs')
                        _fav_only_sw.on_value_change(lambda e: (_rec_state.update(fav_only=bool(e.value)),
                                                                _render_file_list(),
                                                                _render_entries(_rec_state['json_path']) if _rec_state['json_path'] else None))
                        ui.button('⭐ 导出收藏夹', on_click=lambda: _do_export_favorites_pptx()).props('flat color=amber-8 dense size=sm').tooltip('把所有⭐收藏合成一份 PPTX 提案')
                    with ui.scroll_area().classes('w-full').style(
                            'flex:1; min-height:0; border:1px solid var(--border-panel); border-radius:8px; background: var(--bg-panel-left);'):
                        _files_container = ui.column().classes('w-full q-pa-xs').style('gap:6px;')
                # 右栏 70%
                with ui.column().style('width:70%; flex-shrink:0; height:100%; gap:6px;'):
                    with ui.row().classes('w-full items-center').style('gap:8px;'):
                        _cur_file_lbl = ui.label('← 请选择左侧记录文件').classes('text-sm font-bold').style('color: var(--text-accent);')
                        # 房间类型筛选：下拉选项=全库出现过的房间类型(全局)，筛选只作用于当前选中文件
                        _room_filter_sel = ui.select({'': '🏠 全部房间'}, value='').props('outlined dense options-dense').style('min-width:160px')
                        _room_filter_sel.on_value_change(lambda e: (_rec_state.update(room_filter=(e.value or '')),
                                                                    _render_entries(_rec_state['json_path'])))
                        _room_filter_sel.set_visibility(False)  # 有房间类型选项时才显示(由 _refresh_room_options 控制)
                        with ui.row().style('gap:4px; margin-left:auto;'):
                            ui.button('📥 追加', on_click=lambda: _open_append(_rec_state['json_path'])).props('flat color=amber-8 dense size=sm').style('white-space:nowrap').tooltip('追加效果图到本文件某条记录')
                            ui.button('🌐 HTML', on_click=lambda: _do_export()).props('flat color=blue-7 dense size=sm').style('white-space:nowrap').tooltip('导出当前文件为 HTML')
                            ui.button('📊 PPTX', on_click=lambda: _do_export_pptx()).props('flat color=teal-8 dense size=sm').style('white-space:nowrap').tooltip('导出当前文件为 PPTX 提案')
                    with ui.scroll_area().classes('w-full').style(
                            'flex:1; min-height:0; border:1px solid var(--border-panel); border-radius:8px; background: var(--bg-panel-right);'):
                        _entries_container = ui.column().classes('w-full q-pa-sm').style('gap:10px;')

            # ════ 渲染与操作函数 ════
            def _do_export():
                jp = _rec_state['json_path']
                if not jp:
                    ui.notify('⚠️ 请先选择记录文件', type='warning'); return
                ui.notify(export_html_from_json(jp), type='positive')

            async def _do_export_pptx():
                jp = _rec_state['json_path']
                if not jp:
                    ui.notify('⚠️ 请先选择记录文件', type='warning'); return
                ui.notify('正在生成 PPTX…', type='info')
                msg = await asyncio.to_thread(export_pptx_from_json, jp)
                ui.notify(msg, type='positive' if msg.startswith('✅') else 'warning')

            async def _do_export_favorites_pptx():
                ui.notify('正在生成收藏夹 PPTX…', type='info')
                msg = await asyncio.to_thread(export_favorites_pptx)
                ui.notify(msg, type='positive' if msg.startswith('✅') else 'warning')

            def _render_usage():
                """渲染用量统计小表：模式 × 模型(B2/Pro) × 线路(google/fal) 的 成功/失败 计数。
                各维度值均来自固定枚举(工作流/模型/线路)，无自由文本，故直接拼表无需转义。"""
                summary = load_usage_summary()
                rows = summary['rows']; tot = summary['totals']
                _usage_total_lbl.text = f"累计 {tot['total']} 张 · 成功 {tot['ok']} · 失败 {tot['fail']}"
                _usage_container.clear()
                with _usage_container:
                    if not rows:
                        ui.label('暂无数据（生成几张图后点刷新）').classes('text-xs').style('color: var(--text-secondary);')
                        return
                    th = ("<tr style='text-align:left; color:var(--text-secondary); border-bottom:1px solid var(--border-panel);'>"
                          "<th>模式</th><th>模型</th><th>线路</th><th style='text-align:right;'>成功</th><th style='text-align:right;'>失败</th></tr>")
                    trs = "".join(
                        f"<tr style='border-bottom:1px solid rgba(0,0,0,0.05);'>"
                        f"<td>{r['mode']}</td><td>{r['model']}</td><td>{r['provider']}</td>"
                        f"<td style='text-align:right; color:#3a8a4f;'>{r['ok']}</td>"
                        f"<td style='text-align:right; color:#b5563a;'>{r['fail']}</td></tr>"
                        for r in rows)
                    ui.html(f"<table style='width:100%; font-size:12px; border-collapse:collapse;'>{th}{trs}</table>")

            def _render_file_list():
                _files_container.clear()
                files = scan_json_files()
                q = (_rec_state.get('query') or '').strip().lower()
                fav_only = bool(_rec_state.get('fav_only'))
                shown = 0
                for jp in files:
                    name = os.path.basename(jp).replace('_记录.json', '').replace('.json', '')
                    if q and q not in name.lower():
                        continue
                    try:
                        mt = time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(jp)))
                    except Exception:
                        mt = ''
                    try:
                        _recs = _load_records(jp)
                        _nrec = len(_recs)
                        _nimg = sum(len(r.get('results', [])) for r in _recs)
                        _nfav = sum(1 for r in _recs for res in r.get('results', []) if res.get('favorite'))
                    except Exception:
                        _nrec = _nimg = _nfav = 0
                    if fav_only and _nfav == 0:
                        continue
                    shown += 1
                    sel = (jp == _rec_state['json_path'])
                    with _files_container:
                        card = ui.element('div').classes('job-card w-full q-pa-sm').style(
                            'cursor:pointer;' + ('border-left:4px solid var(--text-accent);' if sel else ''))
                        with card:
                            ui.label(name).classes('text-sm font-bold').style('color: var(--text-primary); word-break:break-all;')
                            _meta = f'📸 {_nimg} 张 · {_nrec} 条' + (f' · ⭐{_nfav}' if _nfav else '') + (f' · 🕐 {mt}' if mt else '')
                            ui.label(_meta).classes('text-xs').style('color: var(--text-secondary);')
                        card.on('click', lambda _, p=jp: _select_file(p))
                if shown == 0:
                    with _files_container:
                        _tip = '没有匹配的记录文件' if (q or fav_only) else '暂无记录文件'
                        ui.label(_tip).classes('text-xs q-pa-md').style('color: var(--text-secondary);')

            def _refresh_room_options(recs=None):
                """刷新「房间类型」下拉：选项与计数都只来自【当前选中文件】(不是全库)。
                recs 给定时直接用(避免重复读盘)，否则按当前 json_path 现读。
                当前选中的类型若已不在该文件里(被删空/换文件)则复位为「全部」。"""
                jp = _rec_state.get('json_path') or ''
                if recs is None:
                    try: recs = _load_records(jp) if jp else []
                    except Exception as ex:
                        logger.debug(f"读取当前文件房间类型失败: {ex}"); recs = []
                pairs = sorted(room_type_counts(recs).items(), key=lambda kv: (-kv[1], kv[0]))
                opts = {'': '🏠 全部房间'}
                for rt, n in pairs:
                    opts[rt] = f'{rt} ({n})'
                cur = _rec_state.get('room_filter') or ''
                if cur not in opts:
                    cur = ''; _rec_state['room_filter'] = ''
                try:
                    _room_filter_sel.set_options(opts, value=cur)
                    _room_filter_sel.set_visibility(len(pairs) > 0)
                except Exception as ex:
                    logger.debug(f"更新房间筛选下拉失败: {ex}")

            async def _select_file(jp):
                _rec_state['json_path'] = jp
                _render_file_list()  # 刷新高亮
                _cur_file_lbl.text = '正在打开…'
                try:
                    if await asyncio.to_thread(migrate_record_file, jp):
                        ui.notify('已优化此记录(图片转文件存储，后续更快)', type='positive')
                except Exception as ex:
                    logger.warning(f"记录迁移失败(用原数据): {ex}")
                recs = await asyncio.to_thread(_load_records, jp)
                _cur_file_lbl.text = f"{os.path.basename(jp).replace('_记录.json', '')} · {len(recs)} 条记录"
                _refresh_room_options(recs)
                _render_entries(jp, recs)

            def _render_entries(jp, records=None):
                _entries_container.clear()
                if records is None:
                    records = _load_records(jp) if jp else []
                # 房间类型筛选(只作用于当前文件)：与 ⭐只看收藏 AND 组合(fav 仍在 _render_entry_card 按结果级筛)
                rf = _rec_state.get('room_filter') or ''
                if rf:
                    records = [r for r in records if (r.get('room_type', '') or '') == rf]
                if not records:
                    if not jp:
                        _msg = '← 请选择左侧记录文件'
                    elif rf:
                        _msg = f'该文件没有「{rf}」的记录（换个房间类型或选「全部房间」）'
                    else:
                        _msg = '该文件暂无记录'
                    with _entries_container:
                        ui.label(_msg).classes('q-pa-xl w-full text-center').style('color: var(--text-secondary);')
                    return
                for rec in records:
                    _render_entry_card(jp, rec)

            def _render_entry_card(jp, rec):
                rid = rec.get('id', '')
                ts = rec.get('timestamp', '')
                wf = (rec.get('workflow_mode', '') or '').split('(')[0].split(' ')[0]
                room = rec.get('room_type', '')
                results = rec.get('results', [])
                fav_only = bool(_rec_state.get('fav_only'))
                # 保留原始索引(收藏切换/删除要按原 index 定位)
                vis = [(i, r) for i, r in enumerate(results) if (not fav_only or r.get('favorite'))]
                if fav_only and not vis:
                    return  # 这条记录没有收藏，整条跳过
                with _entries_container:
                    card = ui.element('div').classes('job-card w-full q-pa-sm')
                    _style = (rec.get('style', '') or '').split('(')[0].strip()[:18]
                    _eloc = rec.get('location', '') or ''
                    _seam = (rec.get('seam', '') or '').split('(')[0].strip()[:14]
                    _sub = ' · '.join(p for p in [_style, _eloc, _seam] if p) or (rec.get('params_summary', '') or '')
                    with card:
                        with ui.row().classes('w-full justify-between items-center q-mb-xs'):
                            ui.label(f'{ts} · {wf} · {room}').classes('text-sm font-bold').style('color: var(--text-primary);')
                            with ui.row().classes('items-center').style('gap:4px;'):
                                ui.label(f'{len(vis)} 张').classes('text-xs').style('color: var(--text-secondary);')
                                ui.button('🔑', on_click=lambda j=jp, r=rid: _open_reveal(j, r)).props('flat dense round size=sm').tooltip('查看原始提示词')
                                ui.button('🗑', on_click=lambda j=jp, r=rid: _del_record(j, r)).props('flat color=red-8 dense round size=sm').tooltip('删除此记录')
                        if _sub:
                            ui.label(_sub).classes('text-xs q-mb-xs').style('color: var(--text-secondary); word-break:break-all;')
                        if not vis:
                            ui.label('（无效果图）').classes('text-xs q-pa-sm').style('color: var(--text-secondary);')
                        else:
                            with ui.row().classes('w-full').style('gap:6px; flex-wrap:wrap;'):
                                for idx, res in vis:
                                    _render_result_box(jp, rid, idx, res)

            def _toggle_fav(jp, rid, idx):
                new = toggle_result_favorite(jp, rid, idx)
                if new is None:
                    ui.notify('❌ 收藏失败', type='negative'); return
                ui.notify('⭐ 已收藏' if new else '已取消收藏', type='positive')
                _render_entries(jp)
                _render_file_list()   # 同步刷新左栏 ⭐ 角标

            def _render_result_box(jp, rid, idx, res):
                file_rel = res.get('result_image_file', '')
                b64 = res.get('result_image_b64', '')
                model_label = res.get('model_label', '?')
                comment = res.get('comment', '')
                # 文件优先(走 HTTP /outputs/，可缓存+懒加载，不卡)；无文件再回退内联 base64(兼容未迁移)
                if file_rel:
                    src = _to_url(os.path.join(MAIN_OUTPUT_DIR, file_rel))
                elif b64:
                    src = f'data:image/jpeg;base64,{b64}'
                else:
                    src = ''
                box = ui.element('div').classes('relative rounded bg-black').style('flex:1 1 240px; min-width:200px;')
                with box:
                    ui.label(f'{idx + 1} · {model_label}').classes('text-xs text-center w-full q-pa-xs').style('color: var(--text-secondary);')
                    if src:
                        # 列表里显示缩略图(省浏览器解 4K)；放大/下载仍用原图 src。base64 回退无缩略，直接用 src。
                        thumb = _result_thumb_url(os.path.join(MAIN_OUTPUT_DIR, file_rel)) if file_rel else src
                        img = ui.image(thumb).classes('rounded w-full').style('max-height:260px; object-fit:contain; cursor:zoom-in;')
                        img.props('loading=lazy')  # HTTP URL 才有效：滚动到才加载
                        img.on('click', lambda _, u=src: _open_zoom(u))
                        ui.html(f'<a href="{src}" download="result_{rid}_{idx + 1}.jpg" class="dl-btn">⬇️</a>')
                        ui.button('✏️ 二改', on_click=lambda j=jp, r=rid, i=idx, b=b64, fr=file_rel: _open_edit(j, r, i, b, fr)
                                  ).props('flat dense no-caps').style(
                            'position:absolute; bottom:6px; left:6px; background:rgba(26,24,21,0.72); color:#fff;'
                            'z-index:10; padding:2px 10px; border-radius:8px; min-height:0; font-size:0.8em;')
                        ui.button('🗑', on_click=lambda j=jp, r=rid, i=idx: _del_result(j, r, i)
                                  ).props('flat dense round size=sm').style(
                            'position:absolute; top:4px; right:4px; background:rgba(192,57,43,0.85); color:#fff; z-index:10;')
                        _is_fav = bool(res.get('favorite'))
                        ui.button('⭐' if _is_fav else '☆', on_click=lambda j=jp, r=rid, i=idx: _toggle_fav(j, r, i)
                                  ).props('flat dense round size=sm').style(
                            'position:absolute; top:4px; left:4px; z-index:10; background:rgba(26,24,21,0.55);'
                            + ('color:#f5c518;' if _is_fav else 'color:#fff;')).tooltip('收藏 / 取消收藏')
                    else:
                        ui.label('无图片数据').classes('text-xs q-pa-md').style('color:#999;')
                    if comment:
                        ui.label(comment).classes('text-xs q-pa-xs').style(
                            'color:#bbb; white-space:normal; word-break:break-all;')

            # 初始渲染左栏文件列表 + 用量统计 + 房间筛选选项
            _render_file_list()
            _refresh_room_options()
            _render_usage()


    # ==================================
    # 生成任务核心驱动逻辑
    def _request_stop_all():
        _cancel_generation[0] += 1
        stopped = 0
        for job in list(_job_history):
            if job.status in ('queued', 'running'):
                _update_job(job, status='failed', error='已取消（全部停止）')
                try: _refresh_job_card(job)
                except Exception as ex: logger.warning(f"刷新取消任务卡片失败: {ex}")
                stopped += 1
        ui.notify(f'已请求停止 {stopped} 个任务', type='warning')

    def _request_stop_job(job: JobRecord):
        _cancel_jobs.add(job.job_id)
        _update_job(job, status='failed', error='已取消（用户停止）')
        try: _refresh_job_card(job)
        except Exception as ex: logger.warning(f"刷新取消任务卡片失败: {ex}")
        ui.notify(f'已停止：{job.display_name}', type='warning')

    def _notify_job_end(job: JobRecord):
        """任务进入终态(完成/部分完成/失败)时：系统通知 + 提示音 + 页内横幅。
        用户主动停止(已取消)的不打扰。"""
        if job.error and '已取消' in job.error:
            return
        import json as _json
        label = {'done': '✅ 生成成功', 'partial': '⚠️ 部分完成', 'failed': '❌ 生成失败'}.get(job.status, job.status)
        ntype = {'done': 'positive', 'partial': 'warning', 'failed': 'negative'}.get(job.status, 'info')
        try:
            ui.notify(f'{label}：{job.display_name}', type=ntype, position='top', timeout=6000, close_button='✕')
        except Exception as ex: logger.debug(f"页内通知失败 job={job.job_id}: {ex}")
        title = _json.dumps(label, ensure_ascii=False)
        body = _json.dumps(job.display_name + (f'  ·  {job.error[:80]}' if job.error else ''), ensure_ascii=False)
        good = 'true' if job.status in ('done', 'partial') else 'false'
        js = f"""(function(){{
  try {{ if (window.Notification && Notification.permission==='granted') new Notification({title}, {{body:{body}}}); }} catch(e) {{}}
  try {{
    var c=new (window.AudioContext||window.webkitAudioContext)(), o=c.createOscillator(), g=c.createGain();
    o.connect(g); g.connect(c.destination); o.type='sine'; o.frequency.value={good}?880:300;
    g.gain.setValueAtTime(0.0001,c.currentTime); g.gain.exponentialRampToValueAtTime(0.3,c.currentTime+0.02);
    g.gain.exponentialRampToValueAtTime(0.0001,c.currentTime+0.45); o.start(); o.stop(c.currentTime+0.45);
  }} catch(e) {{}}
}})();"""
        try: ui.run_javascript(js)
        except Exception as ex: logger.warning(f"通知JS执行失败 job={job.job_id}: {ex}")

    # 单模型生成（主流程与重试流程共用）：调 API → 存盘 → 立刻刷新卡片(谁先好谁先显示) → 写记录。
    # stage_key ∈ {'b2','pro'}：on_stage 在 worker 线程只写 job.{key}_stage，UI 的 1 秒 timer 读。
    # 图已生成 = 已计费，即便任务已取消也存盘，避免白花钱。返回 (jpg路径或None, 错误字符串)。
    async def _generate_one_model(job: JobRecord, model_id, prompt_text, stage_key, model_name, *,
                                  api_key, pnp, ims, ar, rp, sref, bevel_ref, jpt, rid, should_cancel):
        _t0 = time.time()
        def _on_stage(text):
            try: setattr(job, f'{stage_key}_stage', text)
            except Exception as ex: logger.debug(f"写入阶段状态失败 job={job.job_id}: {ex}")
        # 按模型各自占槽：本模型跑完(API 一返回)即释放，不等另一模型 → 下一个队列任务的同名模型立刻起跑。
        # 仍 ≤max_concurrent_per_model 张同模型并发；磨缝走同一 _pro_semaphore，与 Pro 生成互斥。
        sem = _b2_semaphore if stage_key == 'b2' else _pro_semaphore
        try:
            if sem.locked():
                _on_stage('排队中')   # 槽被别的任务占着 → 徽章/耗时行显示「{B2/Pro} 排队中」
            async with sem:
                _t0 = time.time()      # 计时从真正开跑算起，不含排队等待
                img, err, provider = await asyncio.to_thread(
                    call_image_generate, api_key, model_id, prompt_text,
                    pnp, ims, ar, rp, sref, _on_stage, should_cancel, bevel_ref)
        except Exception as e:
            img, err, provider = None, str(e), get_image_provider()
        finally:
            try: setattr(job, f'{stage_key}_stage', '')
            except Exception as ex: logger.debug(f"清空阶段状态失败 job={job.job_id}: {ex}")
        setattr(job, f'{stage_key}_secs', round(time.time() - _t0, 1))
        path = None
        if img is not None:
            path = _save_api_result_jpg(img, model_name, pnp)
            add_candidate(job, stage_key, path)   # 追加进候选列表 + 当前下标指向最新 + 同步 *_path
            try: _refresh_job_card(job)   # ← 这张图立刻单独显示，不等另一个模型
            except Exception as ex: logger.warning(f"刷新单模型卡片失败: {ex}")
            try: await asyncio.to_thread(_api_write_to_record, img, model_name, jpt, rid, path)
            except Exception as ex: logger.warning(f"写记录失败 job={job.job_id} {model_name}: {ex}")
        # 用量统计：出图=ok，没出图且非取消=fail（取消不计失败）。
        # 线路用 call_image_generate 返回的【实际】provider，自动转 Fal 后能正确记到 Fal 名下。
        _err = err or ''
        if img is not None or '取消' not in _err:
            try: record_usage(job.workflow_mode, model_name, provider, img is not None)
            except Exception as ex: logger.debug(f"记录用量失败 job={job.job_id}: {ex}")
        return path, _err

    # _compute_final_status 已下沉到 models.compute_final_status；调用点直接用 compute_final_status。

    async def _retry_job(job: JobRecord):
        """重试失败/部分完成的任务：用提交时存下的生成上下文，只重跑还没出图的模型(补缺)。
        主动「重抽」走 _regen_job(同卡追加全部模型)，不再复用这里。"""
        ctx = job.retry_ctx
        if not ctx:
            ui.notify('该任务缺少重试信息，请重新提交', type='warning'); return
        if job.status not in ('failed', 'partial'):
            return
        # 恢复的任务 ctx 里没有 api_key(持久化时已剥除)，回退当前输入/配置里的 key
        api_key = ctx.get('api_key', '') or api_key_inp.value.strip() or _load_config().get('gemini_api_key', '').strip()
        if not api_key:
            ui.notify('⚠️ 缺少 API Key', type='warning'); return
        mf = ctx['model_filter']
        need_b2  = (mf in ('b2', 'both'))  and not (job.b2_path  and os.path.exists(str(job.b2_path)))
        need_pro = (mf in ('pro', 'both')) and not (job.pro_path and os.path.exists(str(job.pro_path)))
        if not (need_b2 or need_pro):
            _update_job(job, status='done')
            try: _refresh_job_card(job)
            except Exception as ex: logger.debug(f"刷新重试卡片失败 job={job.job_id}: {ex}")
            return
        # 点「重试」即用户明确要求重跑此任务：清除可能残留的取消标记。否则一旦该 job 被单独「停止」过，
        # 标记会永久留在 _cancel_jobs（重试流程不像主流程 cleanup 那样 discard），导致 should_cancel 恒为 True，
        # 重试一进 call_image_generate 就被静默取消——不发任何请求，日志只剩一条"任务已取消"。
        _cancel_jobs.discard(job.job_id)
        _update_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='')
        try: _refresh_job_card(job)
        except Exception as ex: logger.debug(f"刷新重试卡片失败 job={job.job_id}: {ex}")
        logger.info(f"[任务] retry job={job.job_id}, need_b2={need_b2}, need_pro={need_pro}")
        async with _acquire_model_slots(need_b2, need_pro):
            pnp = ctx['pnp']; ims = ctx['ims']; ar = ctx['ar']; rp = ctx['rp']; sref = ctx['sref']
            jpt = ctx['jpt']; rid = ctx['rid']; cpt = ctx['cpt']; cpt_pro = ctx['cpt_pro']
            bevel_ref = ctx.get('bevel_ref')  # 圆弧倒角参考图(老任务 ctx 没有此键 → None)

            _should_cancel = lambda: _is_cancelled(job.job_id)
            def _retry_one(model_id, prompt_text, stage_key, model_name):
                return _generate_one_model(job, model_id, prompt_text, stage_key, model_name,
                                           api_key=api_key, pnp=pnp, ims=ims, ar=ar, rp=rp, sref=sref,
                                           bevel_ref=bevel_ref, jpt=jpt, rid=rid, should_cancel=_should_cancel)

            b2_err = ''; pro_err = ''
            _tasks = []
            if need_b2:  _tasks.append(('b2',  _retry_one(GEMINI_MODEL_MAP['Nano Banana 2'], cpt, 'b2', 'Nano Banana 2')))
            if need_pro: _tasks.append(('pro', _retry_one(GEMINI_MODEL_MAP['Nano Banana Pro'], cpt_pro, 'pro', 'Nano Banana Pro')))
            _results = await asyncio.gather(*[c for _, c in _tasks], return_exceptions=True)
            for (_k, _), _res in zip(_tasks, _results):
                _e = str(_res) if isinstance(_res, Exception) else _res[1]
                if _k == 'b2': b2_err = _e
                else: pro_err = _e

            b2j = job.b2_path; proj = job.pro_path   # _generate_one_model 里已写入成图路径；失败的保留原值
            err_msg = ('B2: ' + b2_err if b2_err else '') + (' Pro: ' + pro_err if pro_err else '')
            final = compute_final_status(mf, b2j, proj)
            _update_job(job, status=final, b2_path=b2j, pro_path=proj, error=err_msg.strip())
            logger.info(f"[任务] retry_finished job={job.job_id}, status={final}, b2={bool(b2j)}, pro={bool(proj)}")
            try: _refresh_job_card(job)
            except Exception as ex: logger.warning(f"刷新重试卡片失败: {ex}")
            _notify_job_end(job)

    async def _regen_job(src_job: JobRecord):
        """重抽一张：复用任务已存的生成上下文，在【同一张卡】内重跑全部模型并把新图追加进候选列表
        （不再新建卡）。每个图槽各自累积候选，卡片下方 ‹n/N› 可左右切换对比；同图也追加进同一条记录。"""
        ctx = src_job.retry_ctx
        if not ctx:
            ui.notify('该任务缺少再生成信息，请重新提交', type='warning'); return
        api_key = ctx.get('api_key', '') or api_key_inp.value.strip() or _load_config().get('gemini_api_key', '').strip()
        if not api_key:
            ui.notify('⚠️ 缺少 API Key', type='warning'); return
        mf = ctx.get('model_filter', 'both')
        # 主动重抽：清掉可能残留的取消标记（否则 should_cancel 恒 True，一进 call_image_generate 就被静默取消）
        _cancel_jobs.discard(src_job.job_id)
        _update_job(src_job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='')
        try: _refresh_job_card(src_job)   # 旧候选仍可见(_apply_images 不隐藏已显示的图)，状态转「生成中」
        except Exception as ex: logger.debug(f"刷新重抽卡片失败 job={src_job.job_id}: {ex}")
        logger.info(f"[任务] regen(同卡追加) job={src_job.job_id}, mf={mf}, record={ctx.get('rid')}")
        run_b2 = mf in ('b2', 'both'); run_pro = mf in ('pro', 'both')
        async with _acquire_model_slots(run_b2, run_pro):
            pnp = ctx['pnp']; ims = ctx['ims']; ar = ctx['ar']; rp = ctx['rp']; sref = ctx['sref']
            jpt = ctx['jpt']; rid = ctx['rid']; cpt = ctx['cpt']; cpt_pro = ctx['cpt_pro']
            bevel_ref = ctx.get('bevel_ref')
            _should_cancel = lambda: _is_cancelled(src_job.job_id)
            def _regen_one(model_id, prompt_text, stage_key, model_name):
                # 重抽 = 故意再出一张，每个模型都跑(不像重试只补缺)；_generate_one_model 内 add_candidate 自动追加+跳最新
                return _generate_one_model(src_job, model_id, prompt_text, stage_key, model_name,
                                           api_key=api_key, pnp=pnp, ims=ims, ar=ar, rp=rp, sref=sref,
                                           bevel_ref=bevel_ref, jpt=jpt, rid=rid, should_cancel=_should_cancel)
            _tasks = []
            if mf in ('b2', 'both'):  _tasks.append(('b2',  _regen_one(GEMINI_MODEL_MAP['Nano Banana 2'],  cpt,     'b2',  'Nano Banana 2')))
            if mf in ('pro', 'both'): _tasks.append(('pro', _regen_one(GEMINI_MODEL_MAP['Nano Banana Pro'], cpt_pro, 'pro', 'Nano Banana Pro')))
            _results = await asyncio.gather(*[c for _, c in _tasks], return_exceptions=True)
            errs = []
            for (_k, _), _res in zip(_tasks, _results):
                _e = str(_res) if isinstance(_res, Exception) else _res[1]
                if _e and '取消' not in _e: errs.append(f'{_k.upper()}: {_e}')
            # 状态：只要任一槽有候选(本次或历史)就 done/partial；全失败且无任何候选才 failed
            final = compute_final_status(mf, src_job.b2_path, src_job.pro_path)
            _update_job(src_job, status=final, error=('；'.join(errs) if (final == 'failed' and errs) else ''))
            logger.info(f"[任务] regen_finished job={src_job.job_id}, status={final}, errs={len(errs)}")
            try: _refresh_job_card(src_job)
            except Exception as ex: logger.warning(f"刷新重抽卡片失败: {ex}")
            if final == 'failed':
                _notify_job_end(src_job)
            elif errs:   # 部分失败但仍有候选：提示一下、不给已成功的卡硬挂错误图标
                ui.notify('本次重抽部分失败（已保留之前的候选）：' + '；'.join(errs)[:80], type='warning')

    def _refresh_batch_ui(src_job: JobRecord):
        """刷新源卡上「重抽胶囊 / 停止多抽」的显隐与进度（复用 _apply_action_buttons，逻辑与主刷新一致）。"""
        refs = _job_ui.get(src_job.job_id)
        if not refs: return
        try:
            _apply_action_buttons(refs, src_job)   # 批次运行中 status 恒为 done/partial → stop/retry 同值再设、无副作用
        except Exception as ex:
            logger.debug(f"刷新批次UI失败 {src_job.job_id}: {ex}")

    def _request_stop_batch(src_job: JobRecord):
        """停止多抽：置批次取消标志，driver 跑完当前这张后不再起下一张（已出的都保留）。"""
        b = _regen_batches.get(src_job.job_id)
        if b:
            b['cancel'] = True
            ui.notify('已停止多抽：当前这张跑完后不再继续（已出的都保留）', type='warning')
        _refresh_batch_ui(src_job)

    def _regen_job_n(src_job: JobRecord, n: int):
        """一键多抽：逐张串行重出 N 张候选（一张跑完才起下一张），全部追加进同一条记录供并排挑。
        串行规避预览模型 429 限流 + 软路由突发并发；可中途「停止多抽」停掉尚未开始的剩余候选。
        命中率 P(至少一张好)=1-(1-p)^N。"""
        if not src_job.retry_ctx:
            ui.notify('该任务缺少再生成信息，请重新提交', type='warning'); return
        try: n = max(1, int(n))
        except Exception: n = 1
        sid = src_job.job_id
        # 在按钮 handler 上下文内先捕获 client；后台任务里操作 UI 必须 with client 进 slot
        # (否则 _regen_job 内的 ui.notify/建卡会抛 "current slot cannot be determined")。
        _client = context.client
        if n == 1:                                   # ×1 退化：单张，不建批次态/按钮
            async def _single():
                with _client:
                    await _regen_job(src_job)
            background_tasks.create(_single(), name=f'regen1:{sid}')
            logger.info(f"[任务] 多抽 ×1 from={sid}, record={src_job.retry_ctx.get('rid')}")
            return
        b = _regen_batches.get(sid)
        if b and b.get('running'):                   # 重复触发守卫
            ui.notify('该任务的多抽仍在进行，请先停止或等待', type='info'); return
        _regen_batches[sid] = {'cancel': False, 'i': 0, 'n': n,
                               'running': True, 'gen0': _cancel_generation[0]}
        _refresh_batch_ui(src_job)                   # 立即：胶囊让位、显示停止药丸
        async def _driver():
            with _client:
                try:
                    for i in range(n):
                        bb = _regen_batches.get(sid)                      # 每轮重取，勿跨 await 缓存
                        if bb is None or bb.get('cancel'): break          # 取消/源卡被删
                        if _cancel_generation[0] > bb.get('gen0', 0): break  # stop-all 顺带停后续
                        bb['i'] = i + 1
                        _refresh_batch_ui(src_job)
                        try:
                            await _regen_job(src_job)                     # 串行支点：阻塞到这张跑完
                        except Exception as ex:                           # 单张出错 → 记日志续跑下一张
                            logger.warning(f"[多抽] 第{i+1}/{n}张异常(跳过续跑): {ex}")
                            continue
                finally:
                    _regen_batches.pop(sid, None)
                    try: _refresh_batch_ui(src_job)
                    except Exception as ex: logger.debug(f"复原批次UI失败: {ex}")
        background_tasks.create(_driver(), name=f'regenN:{sid}')
        logger.info(f"[任务] 多抽 ×{n}(逐张串行) from={sid}, record={src_job.retry_ctx.get('rid')}")
        ui.notify(f'开始逐张多抽 ×{n}（满意了点"停止多抽"停掉剩余）', type='positive')

    # ── _run_job 的前两段抽成小步（其余生成核心因局部状态耦合强、且无 API Key 无法离线验证，暂不再拆）──
    def _validate_and_submit(model_filter, room_override):
        """校验输入 + 建 job + 建卡 + 日志/通知。返回 (job, api_key)；校验未过返回 (None, '')。"""
        api_key = api_key_inp.value.strip()
        if not api_key:
            logger.warning("[任务] 提交失败：缺少 API Key")
            ui.notify('⚠️ 缺少 API Key', type='warning'); return None, ''
        if not floor_path['v']:
            logger.warning("[任务] 提交失败：未上传地板图")
            ui.notify('⚠️ 请上传地板图', type='warning'); return None, ''
        if not os.path.exists(str(floor_path['v'])):
            logger.error(f"[任务] 提交失败：地板图文件不存在 path={floor_path['v']}")
            ui.notify('⚠️ 地板图文件不存在，请重新上传', type='warning'); return None, ''
        model_label = {'b2': '[B2]', 'pro': '[Pro]', 'both': '[双模型]'}.get(model_filter, '')
        display_room_type = room_override or (cn_room_type_sel.value if _market_mode['v'] == 'cn' else room_type_sel.value)
        dname = f"{os.path.splitext(os.path.basename(floor_path['v']))[0]} · {display_room_type} {model_label}"
        job = _new_job(dname, time.strftime('%H:%M:%S'), model_filter)
        job.workflow_mode = workflow_radio.value   # 用量统计按工作流模式归类
        _add_job_card(job)
        _auto_trim_cards()   # 新卡入队后顺手收口：超上限则删最旧的已完成卡(此处有 client 上下文)
        logger.info(
            f"[任务] submitted job={job.job_id}, name={dname}, model_filter={model_filter}, "
            f"workflow={workflow_radio.value}, market={_market_mode['v']}, floor={floor_path['v']}, "
            f"room_type={display_room_type}, ref_mode={'参照模式' in workflow_radio.value}, ref_path={ref_path['v'] or ''}"
        )
        ui.notify('任务已提交到队列', type='positive')
        return job, api_key

    async def _maybe_analyze_style(job, api_key, is_ref_mode):
        """参照模式 Step-1：用文字模型提取风格描述。返回 (style_text, ok)。
        分析失败已就地标错+通知，ok=False；调用方据此 return——错误文本若混进提示词会照常计费生成、产出废图，必须中止。"""
        if not (is_ref_mode and ref_path['v']):
            return "", True
        _update_job(job, status='running')
        try: _refresh_job_card(job)
        except Exception as ex: logger.debug(f"刷新任务卡片失败: {ex}")
        style_text, sa_err = await asyncio.to_thread(analyze_style_image, api_key, ref_path['v'])
        if sa_err or not style_text:
            _update_job(job, status='failed',
                        error=f'风格分析失败，已中止（未发起生图）: {sa_err or "返回为空"}')
            try: _refresh_job_card(job)
            except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
            logger.error(f"[参照模式] 风格分析失败，任务中止 job={job.job_id}: {sa_err}")
            _notify_job_end(job)
            return "", False
        logger.info(f"[参照模式] 风格提取完成: {style_text[:120]}...")
        return style_text, True

    async def _run_job(model_filter='both', *, room_override=None):
        job, api_key = _validate_and_submit(model_filter, room_override)
        if job is None:
            return

        jid = job.job_id
        cancel_generation = _cancel_generation[0]
        run_b2 = model_filter in ('b2', 'both'); run_pro = model_filter in ('pro', 'both')

        # 等待信号量前先检查取消标志
        if _is_cancelled(jid, cancel_generation):
            _update_job(job, status='failed', error='已取消（用户停止）')
            try: _refresh_job_card(job)
            except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
            return

        async with _acquire_model_slots(run_b2, run_pro):
            if _is_cancelled(jid, cancel_generation):
                _update_job(job, status='failed', error='已取消（用户停止）')
                try: _refresh_job_card(job)
                except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
                return

            _update_job(job, status='running', started_at=time.time())
            logger.info(f"[任务] running job={job.job_id}, name={job.display_name}")
            try: _refresh_job_card(job)
            except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")

            b2j = None; proj = None
            try:
                _is_cn = (_market_mode['v'] == 'cn')
                # 批量场景覆盖：room_override 替换当前标签页对应的房间类型，其余参数照旧读左侧 widget
                room_for_call = room_type_sel.value
                cn_room_for_call = cn_room_type_sel.value if _is_cn else room_type_sel.value
                if room_override:
                    if _is_cn: cn_room_for_call = room_override
                    else:      room_for_call = room_override
                _is_ref_mode = '参照模式' in workflow_radio.value
                _sref_text = ref_correction_inp.value.strip() if _is_ref_mode else ""

                # 参照模式 Step-1: 提取风格描述（失败即中止，不发起计费生图）
                _style_analysis, _ok = await _maybe_analyze_style(job, api_key, _is_ref_mode)
                if not _ok:
                    return

                # prep 串行：同张小样的并发队列任务共享同一 png/json 输出路径，串行化避免抢写同一文件
                # （同图处理过一次即命中缓存秒回，几乎不增加排队时延；真正的生成在锁外按模型各自占槽并发）。
                async with _task_prep_lock:
                    _, sms, prt, saved_image_path, jpt, rid, pnp, prt_pro = await asyncio.to_thread(
                        save_task_files_html, workflow_radio.value, 'Pro', floor_path['v'],
                        continent_sel.value, (_country_ref['w'].value if _country_ref['w'] else ''), (_city_ref['w'].value if _city_ref['w'] else ''), hood_inp.value, prop_sel.value,
                        (style_sel_ref['w'].value if style_sel_ref['w'] else None), room_for_call, view_sel.value, light_sel.value,
                        pet_type_sel.value, pet_action_sel.value, pet_focus_sel.value, angle_sel.value, aspect_sel.value, res_sel.value,
                        gloss_sel.value, seam_sel.value, [item for item, cb in avoid_checks.items() if cb.value], floor_size_sel.value, custom_inp.value, floor_tone_sel.value,
                        market_sel.value, last_img['v'],
                        # 国内专属参数
                        cn_mode=_is_cn,
                        cn_developer=cn_developer_sel.value if _is_cn else "── 不指定 ──",
                        cn_city=cn_city_sel.value if _is_cn else "上海",
                        cn_tier=cn_tier_sel.value if _is_cn else "── 不指定 ──",
                        cn_unit_type=cn_unit_sel.value if _is_cn else "── 不指定 ──",
                        cn_delivery=cn_delivery_sel.value if _is_cn else "── 不指定 ──",
                        cn_room_type=cn_room_for_call,
                        cn_view=cn_view_sel.value if _is_cn else view_sel.value,
                        cn_space_features=[k for k, cb in cn_space_checks.items() if cb.value] if _is_cn else [],
                        cn_facilities=[k for k, cb in cn_fac_checks.items() if cb.value] if _is_cn else [],
                        style_ref_correction=_sref_text,
                        style_analysis_text=_style_analysis,
                    )
                    last_img['v'] = saved_image_path or floor_path['v']
                logger.info(
                    f"[任务] prompt_saved job={job.job_id}, record={rid}, json={jpt}, png={pnp}, "
                    f"processed={saved_image_path}, msg={_short_text(sms, 240)}"
                )

                cpt = extract_clean_prompt(prt); ar = aspect_sel.value.split(' ')[0]
                # Pro 模型专属提示词（无缝人字拼时与 B2 不同；否则与 cpt 相同）
                cpt_pro = extract_clean_prompt(prt_pro) if prt_pro else cpt
                # 让 B2 也使用 Pro 的终极指令（实测 B2 用此词无缝效果最佳）；
                # 但圆弧倒角·直拼例外——B2 用自己的专属软细缝词(cpt 保持 B2 版不动)。
                _seam_v = seam_sel.value or ''
                _size_v = floor_size_sel.value or ''
                _is_straight_bevel = ('圆弧倒角' in _seam_v and '无缝' not in _seam_v
                                      and '人字拼' not in _size_v and '正方形拼' not in _size_v)
                if not _is_straight_bevel:
                    cpt = cpt_pro
                ims = res_sel.value.split(' ')[0]
                # 参照模式：参照图直接当风格参照图(sref)喂给生图模型(图+文混合)，不发房间替换图(rp)。
                # sref 通道 api.py 已端到端打通且 google/fal 对称(地板小样仍排最后/最关键)。
                rp = None if _is_ref_mode else (room_path['v'] or None)
                _sref_api = (ref_path['v'] or None) if _is_ref_mode else None
                # 圆弧倒角(任意拼法)：自动附内置倒角参考图(只供模型抄板边圆弧形状)；B2/Pro 同带。
                _is_pressed_bevel = ('圆弧倒角' in _seam_v and '无缝' not in _seam_v)
                _bevel_ref = get_bevel_ref_image() if _is_pressed_bevel else None

                # 存下生成上下文：失败后「重试」按钮据此只重跑未成图的模型(不依赖当前表单)
                job.retry_ctx = dict(api_key=api_key, pnp=pnp, cpt=cpt, cpt_pro=cpt_pro,
                                     ims=ims, ar=ar, rp=rp, sref=_sref_api,
                                     bevel_ref=_bevel_ref,
                                     jpt=jpt, rid=rid, model_filter=model_filter)

                # 耗时操作后再次检查取消标志
                if _is_cancelled(jid, cancel_generation):
                    _update_job(job, status='failed', error='已取消（用户停止）')
                    try: _refresh_job_card(job)
                    except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
                    return

                # 运行哪些模型由 run_b2/run_pro 决定（已在函数早段算出并据此取模型并发槽）
                b2_err = ''; pro_err = ''; b2j = None; proj = None

                _should_cancel = lambda: _is_cancelled(jid, cancel_generation)
                def _gen_one(model_id, prompt_text, stage_key, model_name):
                    return _generate_one_model(job, model_id, prompt_text, stage_key, model_name,
                                               api_key=api_key, pnp=pnp, ims=ims, ar=ar, rp=rp, sref=_sref_api,
                                               bevel_ref=_bevel_ref, jpt=jpt, rid=rid, should_cancel=_should_cancel)

                _tasks = []
                if run_b2:  _tasks.append(('b2',  _gen_one(GEMINI_MODEL_MAP['Nano Banana 2'],  cpt,     'b2',  'Nano Banana 2')))
                if run_pro: _tasks.append(('pro', _gen_one(GEMINI_MODEL_MAP['Nano Banana Pro'], cpt_pro, 'pro', 'Nano Banana Pro')))
                _results = await asyncio.gather(*[c for _, c in _tasks], return_exceptions=True)
                for (_k, _), _res in zip(_tasks, _results):
                    if isinstance(_res, Exception):
                        if _k == 'b2': b2_err = str(_res)
                        else: pro_err = str(_res)
                    else:
                        _path, _err = _res
                        if _k == 'b2': b2j = _path; b2_err = _err
                        else: proj = _path; pro_err = _err

                if _is_cancelled(jid, cancel_generation):
                    # 取消后:已返回的图(已计费)已在 _gen_one 里存盘,这里据实标注，不再丢弃
                    if b2j or proj:
                        _final = 'done' if (b2j and proj) else 'partial'
                        _update_job(job, status=_final, b2_path=b2j, pro_path=proj,
                                    error='已取消，但已出图已保留（已付费）')
                    else:
                        _update_job(job, status='failed', error='已取消（无结果）')
                    try: _refresh_job_card(job)
                    except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
                    return

                err_msg = ('B2: ' + b2_err if b2_err else '') + (' Pro: ' + pro_err if pro_err else '')
                if b2_err:
                    logger.error(f"[任务] B2失败 job={job.job_id}, record={rid}, err={_short_text(b2_err, 1000)}")
                if pro_err:
                    logger.error(f"[任务] Pro失败 job={job.job_id}, record={rid}, err={_short_text(pro_err, 1000)}")

                final_status = compute_final_status(model_filter, b2j, proj)

                _update_job(job, status=final_status, b2_path=b2j, pro_path=proj, error=err_msg.strip(),
                            json_path=jpt, record_id=rid, png_path=pnp)
                logger.info(
                    f"[任务] finished job={job.job_id}, status={final_status}, record={rid}, "
                    f"b2_path={b2j}, pro_path={proj}, error={_short_text(err_msg, 1000)}"
                )
                try: _refresh_job_card(job)
                except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
                _notify_job_end(job)

                try:
                    _render_file_list()
                    _refresh_room_options()   # 新出图可能带来新房间类型 → 同步筛选选项
                except Exception as ex: logger.warning(f"刷新记录文件列表失败: {ex}")

            except Exception as e:
                logger.exception(f"[任务] unhandled_exception job={job.job_id}, name={job.display_name}")
                _update_job(job, status='failed', b2_path=b2j, pro_path=proj, error=str(e))
                try: _refresh_job_card(job)
                except Exception as ex: logger.warning(f"刷新失败任务卡片失败: {ex}")
                _notify_job_end(job)
            finally:
                logger.info(f"[任务] cleanup job={job.job_id}, cancelled={jid in _cancel_jobs}")
                _cancel_jobs.discard(jid)

    # 生成/停止绑定已内联到底部操作栏（模型分段 + ⚡ 生成 + 全部停止），此处无需再绑定。

    # 已用时间计时器：建在页面根槽位（最稳），避免卡片容器销毁后计时器报 parent slot 错误
    ui.timer(1.0, _tick_job_times)

    # 进页面后请求一次浏览器通知权限（任务完成/失败时弹系统通知用），用户点“允许”即可
    def _request_notify_perm():
        try:
            ui.run_javascript("try{if(window.Notification&&Notification.permission==='default'){Notification.requestPermission();}}catch(e){}")
        except Exception as ex:
            logger.warning(f"请求通知权限失败: {ex}")
    ui.timer(1.5, _request_notify_perm, once=True)
