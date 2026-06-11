import os
import sys
import time
import base64
import json
import io as _io_mod
import threading as _threading
import asyncio
import logging
import traceback
from typing import Optional, List, Dict

from PIL import Image

from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR, CONFIG_FILE,
    GEMINI_MODEL_MAP,
    _build_theme_css,
    logger, _short_text, _load_config, _save_config, save_api_key,
    save_provider_settings,
    extract_clean_prompt, is_seamless_herringbone,
    get_bevel_ref_image,
)
from .api import (
    call_gemini_generate, call_gemini_edit, call_image_generate,
    analyze_style_image,
    FLOOR_DESEAM_INSTRUCTION,
    _match_color_to_reference,
    _infer_aspect_ratio_from_b64,
)
from .prompt_data import (
    FLOOR_TONES, FLOOR_SIZES,
    LIGHTINGS, ANGLES,
    ROOM_TYPES, PROPERTY_TYPES, VIEWS,
    STYLES, STYLE_ATMOSPHERE_MAP,
    CONTINENTS, LOCATION_MAP,
    PET_TYPES, PET_ACTIONS, PET_FOCUS_OPTIONS,
    MARKET_FURNITURE_CHOICES, MARKET_FURNITURE_MAP,
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
    _img_to_b64, _b64_to_pil, _load_records, _save_records,
    _delete_record, _delete_result_image,
    scan_json_files, get_record_labels, export_html_from_json,
    append_result_to_log, append_edited_result_to_record,
    _save_api_result_jpg, _api_write_to_record,
    reveal_prompt_fn,
)
from .models import JobRecord

from nicegui import ui, app, events as ng_events

UPLOAD_DIR = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else BASE_DIR, '_ng_uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.add_static_files('/outputs', MAIN_OUTPUT_DIR)
app.add_static_files('/uploads', UPLOAD_DIR)

def _to_url(p: str) -> str:
    if not p: return ''
    p = str(p)
    if MAIN_OUTPUT_DIR in p: return '/outputs/' + p.replace(MAIN_OUTPUT_DIR,'').replace('\\','/').lstrip('/')
    if UPLOAD_DIR in p: return '/uploads/' + p.replace(UPLOAD_DIR,'').replace('\\','/').lstrip('/')
    return ''

_job_history: List[JobRecord] = []
_job_lock = _threading.Lock()
_gen_semaphore: Optional[asyncio.Semaphore] = None
_job_ui_refs: dict = {}   # job_id → {card, status_lbl, err_lbl, img_row, b2_img, b2_dl, pro_img, pro_dl}
_job_stop_btns: dict = {}  # job_id → stop button element
_job_retry_btns: dict = {} # job_id → retry button element (失败/部分完成时显示)

def _new_job(display_name, ts, model_filter='both') -> JobRecord:
    job = JobRecord(job_id=f"job_{int(time.time()*1000)}", display_name=display_name, ts=ts, model_filter=model_filter)
    with _job_lock: _job_history.insert(0, job)
    # UI card is created by _add_job_card() called from the event loop in _run_job
    return job

def _update_job(job: JobRecord, **kw):
    for k, v in kw.items(): setattr(job, k, v)
    # _refresh_job_card() is called explicitly from _run_job after this

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
    global _gen_semaphore
    if _gen_semaphore is None:
        try: _max_jobs = max(1, int(_load_config().get('max_concurrent_jobs', 5)))
        except Exception as ex:
            logger.warning(f"读取 max_concurrent_jobs 配置失败，用默认 5: {ex}")
            _max_jobs = 5
        _gen_semaphore = asyncio.Semaphore(_max_jobs)

    # ── 主题：固定「Anthropic 暖陶米色」（唯一主题，浅色；字体走系统微软雅黑、完全离线）──
    ui.add_head_html(f'<style id="theme-style">{_build_theme_css("Anthropic 暖陶米色")}</style>')
    ui.dark_mode().disable()

    floor_path = {'v': ''}; room_path = {'v': ''}; ref_path = {'v': ''}; last_img = {'v': ''}
    _cancel_generation = [0]; _cancel_jobs = set()
    def _is_cancelled(job_id: str, generation: int = None) -> bool:
        return job_id in _cancel_jobs or (generation is not None and generation < _cancel_generation[0])

    with ui.header().classes('q-py-xs q-px-md items-center justify-between').style('height:50px; background: var(--bg-header);'):
        with ui.row().classes('items-center'):
            ui.label('🪵 地板 AI 提示词引擎 v5.3.6').classes('text-h6').style('color: var(--text-accent);')
        with ui.tabs().classes('flex-grow') as main_tabs:
            t_workspace = ui.tab('workspace', label='🎨 工作台 (生成 & 队列)')
            t_records   = ui.tab('records', label='📋 记录管理')

    with ui.tab_panels(main_tabs, value='workspace').classes('w-full').style('height:calc(100vh - 50px); overflow:hidden;'):

        # ==================================
        # TAB 1: 工作台 (左 35% 生成，右 65% 队列)
        # ==================================
        with ui.tab_panel('workspace').classes('q-pa-none'):
            # 用 inline style 保证优先级最高，彻底防止被 Quasar / 浏览器默认样式覆盖
            with ui.row().classes('w-full no-wrap').style('overflow:hidden; height:calc(100vh - 50px);'):

                # 【左侧 35%】：生成参数栏（inline 限宽 + min-width:0 双保险）
                with ui.scroll_area().classes('q-pa-md border-r').style(
                        'flex:0 0 35%; min-width:0; width:35%; height:100%; overflow:hidden;'
                        'border-color: var(--border-panel); background: var(--bg-panel-left);'):
                    with ui.column().classes('w-full q-gutter-y-sm'):
                        
                        workflow_radio = ui.radio(
                            ['纯效果图 (生成全新空间)', '地板替换 (保持原图换地板)', '宠物友好 (动物独处/主宠互动)', '参照模式 (风格参照图生新图)'],
                            value='纯效果图 (生成全新空间)'
                        ).classes('w-full')

                        ui.label('📎 地板素材图').classes('text-caption').style('color: var(--text-label);')
                        floor_prev = ui.image('').classes('w-full rounded'); floor_prev.visible = False
                        tone_analysis_lbl = ui.html('').classes('w-full')

                        async def on_floor_up(e: ng_events.UploadEventArguments):
                            fn, content = await _extract_upload_data(e)
                            if not content: ui.notify('读取失败', type='negative'); return
                            fn = fn.replace(' ', '_'); p = os.path.join(UPLOAD_DIR, fn)
                            with open(p, 'wb') as f: f.write(content)
                            floor_path['v'] = p; floor_prev.set_source(f'/uploads/{fn}'); floor_prev.visible = True
                            # 色调分析
                            try:
                                matched_tone, analysis_html = await asyncio.to_thread(analyze_floor_tone, p)
                                tone_analysis_lbl.content = analysis_html
                                floor_tone_sel.value = matched_tone
                                # 更新风格推荐
                                _isc2 = get_style_choices(matched_tone)
                                _rebuild_style_sel(_isc2)
                            except Exception as ex:
                                tone_analysis_lbl.content = f'<span style="color:#e74c3c;font-size:0.8em;">分析失败: {ex}</span>'

                        ui.upload(on_upload=on_floor_up, auto_upload=True, max_files=1).props('accept=".jpg,.jpeg,.png" flat color=amber-8 dense').classes('w-full')

                        room_sec = ui.column().classes('w-full')
                        room_sec.visible = False
                        with room_sec:
                            ui.label('🏠 待替换房间原图').classes('text-caption').style('color: var(--text-label);')
                            async def on_room_up(e: ng_events.UploadEventArguments):
                                fn, content = await _extract_upload_data(e)
                                fn = 'room_' + fn.replace(' ', '_'); p = os.path.join(UPLOAD_DIR, fn)
                                with open(p, 'wb') as f: f.write(content)
                                room_path['v'] = p; ui.notify('✅ 已上传', type='positive')
                            ui.upload(on_upload=on_room_up, auto_upload=True, max_files=1).props('accept=".jpg,.jpeg,.png" flat color=blue-7 dense').classes('w-full')

                        ref_sec = ui.column().classes('w-full q-gutter-y-xs')
                        ref_sec.visible = False
                        with ref_sec:
                            ui.label('🖼️ 风格参照图').classes('text-caption').style('color: var(--text-label);')
                            ui.label('上传一张参照房间照片，AI将复制其整体风格').classes('text-caption').style('color: var(--text-secondary); font-size:0.75em;')
                            ref_prev = ui.image('').classes('w-full rounded'); ref_prev.visible = False
                            async def on_ref_up(e: ng_events.UploadEventArguments):
                                fn, content = await _extract_upload_data(e)
                                fn = 'ref_' + fn.replace(' ', '_'); p = os.path.join(UPLOAD_DIR, fn)
                                with open(p, 'wb') as f: f.write(content)
                                ref_path['v'] = p; ref_prev.set_source(f'/uploads/{fn}'); ref_prev.visible = True; ui.notify('✅ 参照图已上传', type='positive')
                            ui.upload(on_upload=on_ref_up, auto_upload=True, max_files=1).props('accept=".jpg,.jpeg,.png" flat color=green-7 dense').classes('w-full')
                            ref_correction_inp = ui.textarea(label='附加风格说明 (可选)', placeholder='例：保持参照图的暖色调，但换成更简洁的布局...').classes('w-full').props('rows=2')

                        _editable = 'use-input input-debounce=0 clearable'
                        aspect_sel = ui.select(['4:3 (横向)','16:9 (超宽)','3:4 (竖向)','9:16 (手机)'], value='4:3 (横向)', label='📐 比例').classes('w-full').props(_editable)
                        res_sel = ui.select(['4K','2K'], value='4K', label='🔍 画质').classes('w-full').props(_editable)

                        with ui.expansion('🎨 场景 / 风格 / 地板').classes('w-full').props('dense'):
                            with ui.column().classes('w-full q-gutter-y-sm'):
                                floor_tone_sel = ui.select(FLOOR_TONES, value=FLOOR_TONES[0], label='🎨 色调').classes('w-full').props(_editable)

                                # 风格选择器容器（支持重建）
                                _style_sel_box = ui.element('div').classes('w-full')
                                style_sel_ref = {'w': None}

                                def _rebuild_style_sel(isc):
                                    _style_sel_box.clear()
                                    with _style_sel_box:
                                        opts = {v: l for l, v in isc}
                                        style_sel_ref['w'] = ui.select(
                                            opts, value=isc[0][1] if isc else None, label='✨ 风格 (按色调推荐排序)'
                                        ).classes('w-full').props(_editable)

                                _isc = get_style_choices(FLOOR_TONES[0])
                                _rebuild_style_sel(_isc)
                                style_sel = style_sel_ref  # dict ref
                                floor_tone_sel.on_value_change(lambda e: _rebuild_style_sel(get_style_choices(e.value or FLOOR_TONES[0])))

                                ui.separator()

                                # ── 🌍 海外 / 🇨🇳 国内 Tab ────────────────────
                                _market_tabs = ui.tabs().props('dense align=left').classes('w-full')
                                with _market_tabs:
                                    _tab_intl = ui.tab('intl', label='🌍 海外市场')
                                    _tab_cn   = ui.tab('cn',   label='🇨🇳 国内专属')
                                _market_mode = {'v': 'intl'}  # 追踪当前 tab

                                with ui.tab_panels(_market_tabs, value='intl').classes('w-full').style('padding:0'):

                                    # ══ Tab 海外 ══════════════════════════════
                                    with ui.tab_panel('intl').classes('q-pa-none'):
                                        with ui.column().classes('w-full q-gutter-y-sm q-pt-xs'):
                                            room_type_sel = ui.select(ROOM_TYPES, value=ROOM_TYPES[0], label='🚪 房间类型').classes('w-full').props(_editable)
                                            prop_sel = ui.select(PROPERTY_TYPES, value=PROPERTY_TYPES[0], label='🏠 物业类型').classes('w-full').props(_editable)
                                            view_sel = ui.select(VIEWS, value=VIEWS[0], label='🪟 视野').classes('w-full').props(_editable)

                                            # 大洲→国家→城市
                                            _init_cont = CONTINENTS[-1]
                                            continent_sel = ui.select(CONTINENTS, value=_init_cont, label='🌍 大洲').classes('w-full').props(_editable)
                                            _country_box = ui.element('div').classes('w-full')
                                            _city_box    = ui.element('div').classes('w-full')
                                            _country_ref = {'w': None}
                                            _city_ref    = {'w': None}

                                            def _rebuild_city_sel(countries_map, country):
                                                cities = countries_map.get(country, [])
                                                _city_box.clear()
                                                with _city_box:
                                                    _city_ref['w'] = ui.select(cities, value=cities[0] if cities else None, label='🏙️ 城市/地区').classes('w-full').props(_editable)

                                            def _rebuild_country_sel(continent):
                                                countries_map = LOCATION_MAP.get(continent, {"通用": ["通用现代都市"]})
                                                countries = list(countries_map.keys())
                                                _country_box.clear()
                                                with _country_box:
                                                    def _on_country_pick(e):
                                                        _rebuild_city_sel(countries_map, e.value or countries[0])
                                                    _country_ref['w'] = ui.select(countries, value=countries[0], label='🗺️ 国家/地区', on_change=_on_country_pick).classes('w-full').props(_editable)
                                                _rebuild_city_sel(countries_map, countries[0])

                                            continent_sel.on_value_change(lambda e: _rebuild_country_sel(e.value or CONTINENTS[-1]))
                                            _rebuild_country_sel(_init_cont)

                                            hood_inp = ui.input(label='小区/地段 (可选)', placeholder='自由填写...').classes('w-full')
                                            market_sel = ui.select(MARKET_FURNITURE_CHOICES, value=MARKET_FURNITURE_CHOICES[0], label='🛋️ 家具地区风格').classes('w-full').props(_editable)

                                    # ══ Tab 国内 ══════════════════════════════
                                    with ui.tab_panel('cn').classes('q-pa-none'):
                                        with ui.column().classes('w-full q-gutter-y-sm q-pt-xs'):
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

                                # ── 共用参数（两个 Tab 均适用）────────────────
                                ui.separator()
                                light_sel = ui.select(LIGHTINGS, value=LIGHTINGS[0], label='💡 光线').classes('w-full').props(_editable)
                                angle_sel = ui.select(ANGLES, value=ANGLES[0], label='📷 镜头').classes('w-full').props(_editable)
                                floor_size_sel = ui.select(FLOOR_SIZES, value=FLOOR_SIZES[0], label='📏 板材尺寸').classes('w-full').props(_editable)
                                seam_sel = ui.select(['无缝拼接 (SPC/LVT专用)','常规倒角缝 (如强化/木地板)','圆弧倒角 (Pressed Bevel)'], value='无缝拼接 (SPC/LVT专用)', label='🔩 拼缝').classes('w-full').props(_editable)
                                gloss_sel = ui.select(['超哑光 (0-3°)','哑光 (3-5°)','高光 (High Gloss)'], value='哑光 (3-5°)', label='✨ 光泽度').classes('w-full').props(_editable)

                                pet_sec = ui.column().classes('w-full q-gutter-y-sm'); pet_sec.visible = False
                                with pet_sec:
                                    ui.label('🐾 宠物设置').classes('text-caption').style('color: var(--text-label);')
                                    pet_type_sel = ui.select(PET_TYPES, value=PET_TYPES[0], label='种类').classes('w-full').props(_editable)
                                    pet_action_sel = ui.select(PET_ACTIONS, value=PET_ACTIONS[0], label='动作').classes('w-full').props(_editable)
                                    pet_focus_sel = ui.select(PET_FOCUS_OPTIONS, value=PET_FOCUS_OPTIONS[0], label='焦点').classes('w-full').props(_editable)

                                ui.label('🚫 避免出现').classes('text-caption q-mt-sm').style('color: var(--text-label);')
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

                                def _save_api_settings():
                                    save_api_key(api_key_inp.value, api_proxy_inp.value)
                                    save_provider_settings(fal_key_inp.value, provider_sel.value)
                                    _route = 'Fal 路由' if provider_sel.value == 'fal' else 'Google 直连'
                                    ui.notify(f'保存成功 · 当前线路: {_route}', type='positive')
                                ui.button('💾 保存', on_click=_save_api_settings).classes('w-full').props('outline color=amber-8')

                        ui.separator()
                        gen_status_lbl = ui.label('就绪').classes('text-caption w-full text-center').style('color: var(--text-secondary);')
                        with ui.row().classes('w-full no-wrap').style('gap:4px'):
                            gen_b2_btn = ui.button('⚡ B2 生成', icon='bolt').classes('flex-1').props('color=blue-8 text-color=white dense')
                            gen_pro_btn = ui.button('⚡ Pro 生成', icon='bolt').classes('flex-1').props('color=deep-purple-8 text-color=white dense')
                        gen_both_btn = ui.button('⚡ 双模型生成', icon='bolt').classes('w-full').props('color=amber-8 text-color=black')
                        stop_all_btn = ui.button('⏹ 全部停止', icon='stop').classes('w-full').props('color=red-8 flat dense')

                # 【右侧 50%】：实时队列栏，带下载按钮
                # 【右侧 65%】：实时队列栏（inline 限宽 + min-width:0 双保险）
                with ui.scroll_area().classes('q-pa-md').style(
                        'flex:0 0 65%; min-width:0; width:65%; height:100%; overflow:hidden;'
                        'background: var(--bg-panel-right);'):
                    with ui.row().classes('w-full items-center q-mb-md justify-between'):
                        ui.label('⚡ 实时渲染队列').classes('text-h6').style('color: var(--text-accent);')
                        ui.button('清除已完成', icon='clear_all', on_click=lambda: (_clear_done(), ui.notify('已清理'))).props('flat color=grey-6 dense')
                    
                    queue_container = ui.column().classes('w-full')
                    _empty_lbl = ui.label('暂无渲染任务，请在左侧调整参数后点击生成').classes('q-pa-xl w-full text-center').style('color: var(--text-secondary);')

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
                        def _img_box(caption):
                            box = ui.element('div').classes('relative rounded bg-black').style(box_style)
                            with box:
                                ui.label(caption).classes('text-xs text-center w-full q-pa-xs').style('color: var(--text-secondary);')
                                _im = ui.image('').classes('rounded w-full').style('max-height:260px;object-fit:contain;'); _im.visible = False
                                _dl = ui.html('')
                            return box, _im, _dl
                        with queue_container:
                            card = ui.element('div').classes('job-card w-full q-pa-sm q-mb-sm')
                            with card:
                                with ui.row().classes('w-full justify-between items-center q-mb-xs'):
                                    ui.label(job.display_name).classes('text-sm font-bold').style('color: var(--text-primary);')
                                    with ui.row().classes('items-center').style('gap:6px'):
                                        status_lbl = ui.label(f'{icon} {txt}').classes('text-xs')
                                        stop_single_btn = ui.button('⏹', on_click=lambda j=job: _request_stop_job(j)).props('flat color=red-8 dense round size=sm').tooltip('停止此任务')
                                        stop_single_btn.visible = False  # 排队中或运行中才显示
                                        _job_stop_btns[job.job_id] = stop_single_btn
                                        retry_btn = ui.button('🔁', on_click=lambda j=job: _retry_job(j)).props('flat color=primary dense round size=sm').tooltip('重新生成失败的部分')
                                        retry_btn.visible = False  # 失败/部分完成才显示
                                        _job_retry_btns[job.job_id] = retry_btn
                                    ui.label(f'🕐 {job.ts}').classes('text-xs').style('color: var(--text-secondary);')
                                time_lbl = ui.label('').classes('text-xs').style('color: var(--text-secondary);'); time_lbl.visible = False
                                err_lbl = ui.label('').classes('text-xs text-red-400'); err_lbl.visible = False
                                img_row = ui.row().classes('w-full q-mt-xs').style('gap:6px; flex-wrap:wrap;'); img_row.visible = False
                                pro_polish_btn = None; pro_polish_img = None; pro_polish_dl = None
                                with img_row:
                                    if job.model_filter in ('b2', 'both'):
                                        _, b2_img, b2_dl = _img_box('B2')
                                    else:
                                        b2_img = None; b2_dl = None
                                    if job.model_filter in ('pro', 'both'):
                                        pro_box = ui.element('div').classes('relative rounded bg-black').style(box_style)
                                        with pro_box:
                                            ui.label('Pro').classes('text-xs text-center w-full q-pa-xs').style('color: var(--text-secondary);')
                                            pro_img = ui.image('').classes('rounded w-full').style('max-height:260px;object-fit:contain;'); pro_img.visible = False
                                            pro_dl  = ui.html('')
                                            pro_polish_btn = ui.button('🪄 磨缝', on_click=lambda j=job: _polish_pro(j)).props('flat dense no-caps').classes('polish-btn'); pro_polish_btn.visible = False
                                        # Pro 磨缝结果槽（按需显示）
                                        _, pro_polish_img, pro_polish_dl = _img_box('Pro 磨缝')
                                    else:
                                        pro_img = None; pro_dl = None
                            _job_ui_refs[job.job_id] = dict(
                                card=card, status_lbl=status_lbl, err_lbl=err_lbl, time_lbl=time_lbl,
                                img_row=img_row, b2_img=b2_img, b2_dl=b2_dl,
                                pro_img=pro_img, pro_dl=pro_dl,
                                pro_polish_btn=pro_polish_btn, pro_polish_img=pro_polish_img, pro_polish_dl=pro_polish_dl,
                                single_model=single_model
                            )

                    # ── 耗时文案：完成后按模型分别显示；运行中显示已用秒数 ──────
                    def _job_time_text(job: JobRecord) -> str:
                        def _fmt(s):
                            return f'{s:.1f}s' if s is not None else '—'
                        # 运行中：已用秒数 + 各模型实时阶段（🧠思考标题 / 🎨渲染中 / 🔁重试）
                        if job.status == 'running' and job.started_at:
                            base = f'⏱ 已用 {time.time() - job.started_at:.0f}s'
                            stages = []
                            if job.model_filter in ('b2', 'both') and job.b2_stage:
                                stages.append(job.b2_stage if job.model_filter != 'both' else f'B2 {job.b2_stage}')
                            if job.model_filter in ('pro', 'both') and job.pro_stage:
                                stages.append(job.pro_stage if job.model_filter != 'both' else f'Pro {job.pro_stage}')
                            return base + ('　·　' + '　·　'.join(stages) if stages else '')
                        has_secs = (job.b2_secs is not None) or (job.pro_secs is not None)
                        if has_secs:
                            if job.model_filter == 'both':
                                return f'⏱ B2 {_fmt(job.b2_secs)} · Pro {_fmt(job.pro_secs)}'
                            elif job.model_filter == 'b2':
                                return f'⏱ B2 {_fmt(job.b2_secs)}'
                            else:
                                return f'⏱ Pro {_fmt(job.pro_secs)}'
                        return ''

                    # 每秒刷新"运行中"任务的已用时间
                    def _tick_job_times():
                        for j in list(_job_history):
                            if j.status == 'running':
                                refs = _job_ui_refs.get(j.job_id)
                                tl = refs.get('time_lbl') if refs else None
                                if tl is not None:
                                    try:
                                        tl.text = _job_time_text(j); tl.visible = True
                                    except Exception as ex: logger.debug(f"刷新耗时标签失败 job={j.job_id}: {ex}")

                    # ── 原地更新（改属性，不销毁 DOM，不闪烁） ───────────────
                    def _refresh_job_card(job: JobRecord):
                        refs = _job_ui_refs.get(job.job_id)
                        if not refs: return
                        _STATUS = {
                            'queued':  ('⏳', '排队中'),
                            'running': ('⚡', '生成中…'),
                            'done':    ('✅', '完成'),
                            'partial': ('⚠️', '部分完成'),
                            'failed':  ('❌', '失败'),
                        }
                        icon, txt = _STATUS.get(job.status, ('?', job.status))
                        refs['status_lbl'].text = f'{icon} {txt}'
                        # 停止按钮：排队中或运行中显示
                        stop_btn = _job_stop_btns.get(job.job_id)
                        if stop_btn:
                            try: stop_btn.visible = (job.status in ('queued', 'running'))
                            except Exception as ex: logger.warning(f"更新停止按钮状态失败: {ex}")
                        # 重试按钮：失败/部分完成 且 存在重试上下文 才显示
                        retry_btn = _job_retry_btns.get(job.job_id)
                        if retry_btn:
                            try: retry_btn.visible = (job.status in ('failed', 'partial')) and bool(job.retry_ctx)
                            except Exception as ex: logger.warning(f"更新重试按钮状态失败: {ex}")
                        border_var = {'done':'var(--border-success)','partial':'var(--border-partial)','failed':'var(--border-failed)','running':'var(--border-running)'}.get(job.status,'')
                        if border_var: refs['card'].style(f'border-left:4px solid {border_var};')
                        # 耗时显示
                        tl = refs.get('time_lbl')
                        if tl is not None:
                            txt2 = _job_time_text(job)
                            if txt2:
                                tl.text = txt2; tl.visible = True
                            else:
                                tl.visible = False
                        if job.error:
                            refs['err_lbl'].text = job.error[:120]; refs['err_lbl'].visible = True
                        has_img = False
                        if job.b2_path and os.path.exists(str(job.b2_path)) and refs.get('b2_img') is not None:
                            url = _to_url(job.b2_path)
                            refs['b2_img'].set_source(url); refs['b2_img'].visible = True
                            refs['b2_dl'].content = f'<a href="{url}" download="{os.path.basename(str(job.b2_path))}" class="dl-btn">⬇️ B2</a>'
                            has_img = True
                        if job.pro_path and os.path.exists(str(job.pro_path)) and refs.get('pro_img') is not None:
                            url = _to_url(job.pro_path)
                            refs['pro_img'].set_source(url); refs['pro_img'].visible = True
                            refs['pro_dl'].content = f'<a href="{url}" download="{os.path.basename(str(job.pro_path))}" class="dl-btn">⬇️ Pro</a>'
                            has_img = True
                            # 磨缝按钮：Pro 出图后显示；磨缝中禁用；已磨缝则隐藏
                            btn = refs.get('pro_polish_btn')
                            if btn is not None:
                                try:
                                    if job.pro_polish_path:
                                        btn.visible = False
                                    elif job.pro_polishing:
                                        btn.visible = True; btn.text = '磨缝中…'; btn.props('loading')
                                    else:
                                        btn.visible = True; btn.text = '🪄 磨缝'; btn.props(remove='loading')
                                except Exception as ex: logger.warning(f"更新磨缝按钮失败: {ex}")
                        # Pro 磨缝结果（按需）
                        if job.pro_polish_path and os.path.exists(str(job.pro_polish_path)) and refs.get('pro_polish_img') is not None:
                            url = _to_url(job.pro_polish_path)
                            refs['pro_polish_img'].set_source(url); refs['pro_polish_img'].visible = True
                            refs['pro_polish_dl'].content = f'<a href="{url}" download="{os.path.basename(str(job.pro_polish_path))}" class="dl-btn">⬇️ Pro磨缝</a>'
                            has_img = True
                        if has_img: refs['img_row'].visible = True

                    # 按需磨缝：对某个任务的 Pro 成图做一次图生图去缝 + 色彩对齐
                    async def _polish_pro(job: JobRecord):
                        if not job.pro_path or not os.path.exists(str(job.pro_path)):
                            ui.notify('⚠️ 没有可磨缝的 Pro 图', type='warning'); return
                        if job.pro_polishing or job.pro_polish_path:
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
                            polished, perr = await asyncio.to_thread(
                                call_gemini_edit, api_key, GEMINI_MODEL_MAP['Nano Banana Pro'],
                                FLOOR_DESEAM_INSTRUCTION, _b64, '4K', _ar, False
                            )
                            if polished is None:
                                ui.notify(f'❌ 磨缝失败：{perr}', type='negative'); return
                            # 色彩对齐回原图，消除 img2img 偏色
                            try:
                                polished = await asyncio.to_thread(_match_color_to_reference, polished, src_pil)
                            except Exception as ex:
                                logger.warning(f"[磨缝] 色彩对齐失败(用未对齐图) job={job.job_id}: {ex}")
                            ppath = _save_api_result_jpg(polished, 'Nano Banana Pro_磨缝', job.png_path or job.pro_path)
                            _update_job(job, pro_polish_path=ppath)
                            if job.json_path and job.record_id:
                                try:
                                    await asyncio.to_thread(_api_write_to_record, polished, 'Nano Banana Pro 磨缝', job.json_path, job.record_id)
                                except Exception as ex:
                                    logger.warning(f"[磨缝] 写入记录失败 job={job.job_id}: {ex}")
                            logger.info(f"[磨缝] 完成 job={job.job_id}, +{time.time()-_t0:.1f}s, path={ppath}")
                            ui.notify('✅ 磨缝完成', type='positive')
                        except Exception as e:
                            logger.exception(f"[磨缝] 异常 job={job.job_id}")
                            ui.notify(f'❌ 磨缝异常：{e}', type='negative')
                        finally:
                            _update_job(job, pro_polishing=False)
                            try: _refresh_job_card(job)
                            except Exception as ex: logger.debug(f"刷新磨缝卡片失败 job={job.job_id}: {ex}")

                    # 清除按钮同时清理 refs
                    def _clear_done():
                        done_ids = {j.job_id for j in _job_history if j.status in ('done','partial','failed')}
                        for jid in done_ids:
                            refs = _job_ui_refs.pop(jid, None)
                            if refs:
                                try: refs['card'].delete()
                                except Exception as ex: logger.warning(f"删除任务卡片失败: {ex}")
                        _job_history[:] = [j for j in _job_history if j.job_id not in done_ids]
                        if not _job_history: _empty_lbl.visible = True

                    # ── 页面加载/刷新时恢复已有任务 ──────────────────────────
                    # _job_history 跨 session 保留（模块级），但 DOM 元素绑定旧
                    # session。新 session 打开时重建所有卡片，覆盖 _job_ui_refs，
                    # 后续的 _refresh_job_card 调用自动打到新页面的元素上。
                    for _existing_job in list(_job_history):
                        _add_job_card(_existing_job)
                        _refresh_job_card(_existing_job)


        # ==================================
        # TAB 2: 记录管理
        # ==================================
        with ui.tab_panel('records').classes('q-pa-sm'):
            rec_state = {'json_path': '', 'record_id': ''}
            _loaded_results = []
            _rec_sel_ref = {'w': None}
            _thumb_selected = [0]  # 当前选中缩略图索引
            _thumb_container = {'w': None}  # 缩略图条容器引用

            # ── 顶部控件行 ──
            with ui.row().classes('w-full items-center no-wrap q-mb-xs').style('gap:6px'):
                mgr_json_sel = ui.select(
                    scan_json_files() or [],
                    label='📂 记录文件'
                ).classes('flex-1').style('min-width:0; max-width:48%').props(
                    'virtual-scroll popup-content-style="max-height:400px;overflow-y:auto"'
                )
                _rec_sel_box = ui.element('div').classes('flex-1').style('min-width:0; max-width:48%; display:flex;')
                ui.button(icon='refresh', on_click=lambda: _do_refresh()
                          ).props('flat color=grey-6 dense').tooltip('刷新')
                ui.button(icon='html', on_click=lambda: _do_export()
                          ).props('flat color=blue-7 dense').tooltip('导出 HTML')

            mgr_status = ui.label('请先选择记录文件').classes('text-xs q-mb-xs').style('color: var(--text-secondary);')

            # ── 主内容区 ──
            with ui.row().classes('w-full no-wrap').style('height:calc(100vh - 148px); gap:8px; overflow:hidden'):

                # 【左列 25%】
                with ui.scroll_area().style(
                        'width:25%; flex-shrink:0; height:100%;'
                        'border:1px solid var(--border-panel); background: var(--bg-panel-left);'
                        ).classes('q-pa-sm rounded'):
                    with ui.column().classes('w-full q-gutter-y-sm'):

                        mgr_ts_lbl = ui.label('').classes('font-bold text-sm').style('color: var(--text-accent);')

                        mgr_sample_img = ui.image('').classes('w-full rounded')
                        mgr_sample_img.visible = False

                        mgr_params_box = ui.textarea(
                            label='参数摘要', value=''
                        ).classes('w-full').props('rows=4 readonly outlined dense')

                        ui.separator().style('margin:4px 0')

                        # ── 内联操作按钮行 ──
                        with ui.row().classes('w-full').style('gap:4px'):
                            ui.button('📥 追加', on_click=lambda: _toggle_append_section()
                                      ).props('flat color=amber-8 dense size=sm').classes('flex-1').tooltip('追加效果图到此记录')
                            ui.button('🔑 解密', on_click=lambda: _toggle_reveal_section()
                                      ).props('flat color=amber dense size=sm').classes('flex-1').tooltip('查看原始提示词')
                            ui.button('🗑️ 删除', on_click=lambda: _do_delete_record()
                                      ).props('flat color=red-8 dense size=sm').classes('flex-1').tooltip('删除当前记录')

                        # ── 追加区域（默认隐藏）──
                        _append_section = ui.element('div').classes('w-full'); _append_section.visible = False
                        with _append_section:
                            with ui.column().classes('w-full q-gutter-y-xs q-pa-xs').style(
                                    'border:1px dashed var(--border-panel); border-radius:4px;'):
                                ui.label('📥 追加效果图').classes('text-xs').style('color: var(--text-label);')
                                append_a_path = {'v': ''}; append_b_path = {'v': ''}
                                async def _on_append_a(e: ng_events.UploadEventArguments):
                                    fn, content = await _extract_upload_data(e)
                                    p = os.path.join(UPLOAD_DIR, 'mgr_a_' + fn.replace(' ', '_'))
                                    with open(p, 'wb') as f_: f_.write(content)
                                    append_a_path['v'] = p; ui.notify('A 已上传', type='positive')
                                ui.upload(on_upload=_on_append_a, auto_upload=True, max_files=1
                                          ).props('accept=".jpg,.jpeg,.png" flat color=amber-8 dense').classes('w-full')
                                append_comment_a = ui.input(label='备注 A').classes('w-full')
                                async def _on_append_b(e: ng_events.UploadEventArguments):
                                    fn, content = await _extract_upload_data(e)
                                    p = os.path.join(UPLOAD_DIR, 'mgr_b_' + fn.replace(' ', '_'))
                                    with open(p, 'wb') as f_: f_.write(content)
                                    append_b_path['v'] = p; ui.notify('B 已上传', type='positive')
                                ui.upload(on_upload=_on_append_b, auto_upload=True, max_files=1
                                          ).props('accept=".jpg,.jpeg,.png" flat color=blue-7 dense').classes('w-full')
                                append_comment_b = ui.input(label='备注 B').classes('w-full')
                                def _do_append():
                                    jp = rec_state['json_path']; rid = rec_state['record_id']
                                    msg = append_result_to_log(
                                        append_a_path['v'] or None, append_b_path['v'] or None,
                                        jp, rid, append_comment_a.value, append_comment_b.value
                                    )
                                    ui.notify(msg, type='positive' if msg.startswith('✅') else 'warning')
                                    if msg.startswith('✅'):
                                        append_a_path['v'] = ''; append_b_path['v'] = ''
                                        append_comment_a.value = ''; append_comment_b.value = ''
                                        _append_section.visible = False
                                        _load_and_show(jp, rid, rebuild_thumbs=False)
                                        _build_json_gallery(jp)
                                ui.button('📥 确认追加', on_click=_do_append
                                          ).props('color=amber-8 dense').classes('w-full')

                        # ── 解密区域（默认隐藏）──
                        _reveal_section = ui.element('div').classes('w-full'); _reveal_section.visible = False
                        with _reveal_section:
                            with ui.column().classes('w-full q-gutter-y-xs q-pa-xs').style(
                                    'border:1px dashed var(--border-panel); border-radius:4px;'):
                                reveal_key_inp = ui.input('密钥', password=True).classes('w-full')
                                reveal_result_box = ui.textarea(
                                    label='原始英文提示词', value=''
                                ).classes('w-full').props('rows=5 readonly outlined dense')
                                def _do_reveal():
                                    jp = rec_state['json_path']; rid = rec_state['record_id']
                                    reveal_result_box.value = reveal_prompt_fn(jp, rid, reveal_key_inp.value)
                                ui.button('🔓 解密', on_click=_do_reveal
                                          ).props('flat color=amber dense').classes('w-full')

                # 【右列 75%】
                with ui.column().style(
                        'width:75%; flex-shrink:0; height:100%; gap:6px;'):
                    # ── 缩略图条 ──
                    with ui.element('div').classes('w-full').style(
                            'height:118px; overflow-x:auto; overflow-y:hidden; white-space:nowrap;'
                            'border:1px solid var(--border-panel); border-radius:6px;'
                            'background: var(--bg-panel-left); padding:6px;'):
                        _thumb_row = ui.row().classes('no-wrap').style('gap:6px; height:100%; align-items:center;')
                        _thumb_container['w'] = _thumb_row
                        _thumb_empty = ui.label('无效果图').classes('text-xs').style(
                            'color: var(--text-secondary); padding:20px;')

                    # ── 大图预览 ──
                    with ui.element('div').style(
                            'flex:1; min-height:0; border:1px solid var(--border-panel);'
                            'border-radius:6px; background: var(--bg-panel-right);'
                            'display:flex; align-items:center; justify-content:center; overflow:hidden;'):
                        mgr_result_holder = ui.element('div').classes('w-full h-full').style(
                            'display:flex; align-items:center; justify-content:center; overflow:hidden;')
                        mgr_no_result_lbl = ui.label(
                            '选择一条记录查看效果图').classes('text-center q-pa-xl').style('color: var(--text-secondary);')

                    # ── 底部信息栏 ──
                    with ui.row().classes('w-full items-center').style('gap:8px; min-height:40px;'):
                        mgr_comment_box = ui.textarea(
                            label='备注', value=''
                        ).classes('flex-grow').props('rows=1 readonly outlined dense').style('max-width:60%;')
                        mgr_dl_holder = ui.element('div')
                        with ui.row().style('gap:4px; margin-left:auto;'):
                            ui.button('✏️ 二改此图', on_click=lambda: _toggle_edit_section()
                                      ).props('flat color=amber-8 dense size=sm')
                            def _do_delete_result():
                                jp = rec_state['json_path']; rid = rec_state['record_id']
                                idx = _thumb_selected[0]
                                if jp is None or rid is None or idx is None:
                                    ui.notify('⚠️ 请先选择效果图', type='warning'); return
                                if not _delete_result_image(jp, rid, idx):
                                    ui.notify('❌ 删除失败', type='negative'); return
                                _load_and_show(jp, rid, rebuild_thumbs=False)
                                _build_json_gallery(jp)
                                ui.notify('✅ 效果图已删除', type='positive')
                            ui.button('🗑️ 删此图', on_click=_do_delete_result
                                      ).props('flat color=red-8 dense size=sm')

                    _edit_section = ui.element('div').classes('w-full')
                    _edit_section.visible = False
                    with _edit_section:
                        with ui.column().classes('w-full q-gutter-y-xs q-pa-sm').style(
                                'border:1px dashed var(--border-panel); border-radius:6px;'
                                'background: var(--bg-panel-left);'):
                            edit_source_lbl = ui.label('当前修改对象：未选择效果图').classes('text-xs').style('color: var(--text-label);')
                            edit_prompt_box = ui.textarea(
                                label='二次修改建议',
                                placeholder='例如：移除地毯，补全被遮挡的地板；去掉蓝色茶壶；把红色边桌换成胡桃木边几；保持其他构图、光线和地板不变。'
                            ).classes('w-full').props('rows=3 outlined dense')
                            with ui.row().classes('w-full items-center').style('gap:6px;'):
                                edit_model_sel = ui.select(
                                    ['Nano Banana Pro', 'Nano Banana 2'],
                                    value='Nano Banana Pro',
                                    label='编辑模型'
                                ).classes('flex-1').props('outlined dense')
                                edit_status_lbl = ui.label('').classes('text-xs').style('color: var(--text-secondary);')
                            edit_keep_color = ui.checkbox('保持原图色彩（防止二改偏色）', value=True).classes('text-xs').tooltip(
                                '二改后自动把整体色温/饱和度拉回原图，消除偏色。若本次就是想改颜色，请取消勾选。')

                            async def _do_edit_current_result():
                                jp = rec_state['json_path']; rid = rec_state['record_id']
                                idx = _thumb_selected[0]
                                if not jp or not rid:
                                    ui.notify('⚠️ 请先加载一条记录', type='warning'); return
                                if not _loaded_results or idx is None or idx >= len(_loaded_results):
                                    ui.notify('⚠️ 请先选择一张效果图', type='warning'); return
                                edit_text = (edit_prompt_box.value or '').strip()
                                if not edit_text:
                                    ui.notify('⚠️ 请填写二次修改建议', type='warning'); return
                                api_key = api_key_inp.value.strip() or _load_config().get('gemini_api_key', '').strip()
                                if not api_key:
                                    ui.notify('⚠️ 缺少 API Key', type='warning'); return
                                source_b64 = _loaded_results[idx].get('result_image_b64', '')
                                if not source_b64:
                                    ui.notify('⚠️ 当前效果图没有图片数据', type='warning'); return

                                model_key = edit_model_sel.value or 'Nano Banana Pro'
                                model_id = GEMINI_MODEL_MAP.get(model_key, GEMINI_MODEL_MAP['Nano Banana Pro'])
                                ar = _infer_aspect_ratio_from_b64(source_b64)
                                edit_status_lbl.text = f'正在二次修改第 {idx + 1} 张图...'
                                ui.notify('已提交二次修改，请稍候', type='info')
                                pil_img, err = await asyncio.to_thread(
                                    call_gemini_edit, api_key, model_id, edit_text, source_b64, '4K', ar
                                )
                                if err or pil_img is None:
                                    edit_status_lbl.text = f'❌ 二次修改失败：{err}'
                                    ui.notify(f'❌ 二次修改失败：{err}', type='negative')
                                    return
                                # 防偏色：把二改图整体色彩拉回原图（可在界面关闭）
                                if edit_keep_color.value:
                                    try:
                                        _ref_img = _b64_to_pil(source_b64)
                                        if _ref_img is not None:
                                            pil_img = await asyncio.to_thread(_match_color_to_reference, pil_img, _ref_img)
                                    except Exception as ex:
                                        logger.warning(f"二次修改色彩对齐失败(用未对齐图): {ex}")
                                try:
                                    _save_api_result_jpg(pil_img, f'{model_key}_Edit', f'{rid}_edit_优化图.png')
                                except Exception as ex:
                                    logger.warning(f"二次修改图片落盘失败: {ex}")
                                msg = append_edited_result_to_record(jp, rid, idx, pil_img, edit_text, model_key)
                                if msg.startswith('✅'):
                                    fresh = _load_records(jp)
                                    target = next((r for r in fresh if r.get('id') == rid), None)
                                    if target:
                                        _thumb_selected[0] = max(0, len(target.get('results', [])) - 1)
                                    edit_prompt_box.value = ''
                                    _edit_section.visible = False
                                    _load_and_show(jp, rid, rebuild_thumbs=False)
                                    _build_json_gallery(jp)
                                    edit_status_lbl.text = ''
                                    ui.notify(msg, type='positive')
                                else:
                                    edit_status_lbl.text = msg
                                    ui.notify(msg, type='negative' if msg.startswith('❌') else 'warning')

                            ui.button('🚀 生成二次修改图', on_click=_do_edit_current_result
                                      ).props('color=amber-8 dense').classes('w-full')

            # ════════════════════════════════
            # 内部逻辑
            # ════════════════════════════════

            def _toggle_append_section():
                _append_section.visible = not _append_section.visible
                if _append_section.visible:
                    _reveal_section.visible = False
                    _edit_section.visible = False

            def _toggle_reveal_section():
                _reveal_section.visible = not _reveal_section.visible
                if _reveal_section.visible:
                    _append_section.visible = False
                    _edit_section.visible = False

            def _toggle_edit_section():
                _edit_section.visible = not _edit_section.visible
                if _edit_section.visible:
                    _append_section.visible = False
                    _reveal_section.visible = False
                    edit_source_lbl.text = f'当前修改对象：第 {_thumb_selected[0] + 1} 张效果图' if _loaded_results else '当前修改对象：未选择效果图'

            def _build_thumbnail_strip(results, rid):
                """根据效果图列表重建缩略图条"""
                row = _thumb_container['w']
                if row is None: return
                row.clear()
                if not results:
                    with row: ui.label('无效果图').classes('text-xs').style('color: var(--text-secondary);')
                    return
                for i, res in enumerate(results):
                    b64 = res.get('result_image_b64', '')
                    label_text = res.get('model_label', '?')
                    is_sel = (i == _thumb_selected[0])
                    border = '2px solid var(--text-accent)' if is_sel else '1px solid var(--border-panel)'
                    with row:
                        thumb_box = ui.element('div').style(
                            f'width:96px; height:96px; border:{border}; border-radius:4px;'
                            'overflow:hidden; cursor:pointer; flex-shrink:0; position:relative;'
                            'background:#000;'
                        )
                        with thumb_box:
                            if b64:
                                ui.image(f'data:image/jpeg;base64,{b64}').classes('w-full h-full').style('object-fit:cover;')
                            ui.label(label_text).style(
                                'position:absolute; bottom:0; left:0; right:0;'
                                'background:rgba(0,0,0,0.7); color:#fff; font-size:9px;'
                                'text-align:center; padding:1px 2px;')
                        thumb_box.on('click', lambda _, idx=i: _select_result(idx, results, rid))

            def _build_json_gallery(jp):
                """选择记录文件后，展示该 JSON 内所有结果图缩略图。"""
                row = _thumb_container['w']
                if row is None: return
                row.clear()
                if not jp:
                    with row: ui.label('请选择记录文件').classes('text-xs').style('color: var(--text-secondary);')
                    return
                records = _load_records(jp)
                entries = []
                for rec in records:
                    rid = rec.get('id', '')
                    ts = rec.get('timestamp', '')
                    room = rec.get('room_type', '')
                    for idx, res in enumerate(rec.get('results', [])):
                        if res.get('result_image_b64'):
                            entries.append((rid, idx, res, ts, room))
                if not entries:
                    with row: ui.label('该记录文件暂无效果图').classes('text-xs').style('color: var(--text-secondary);')
                    return
                for rid, idx, res, ts, room in entries:
                    b64 = res.get('result_image_b64', '')
                    label_text = res.get('model_label', '?')
                    is_sel = (rec_state.get('record_id') == rid and idx == _thumb_selected[0])
                    border = '2px solid var(--text-accent)' if is_sel else '1px solid var(--border-panel)'
                    with row:
                        thumb_box = ui.element('div').style(
                            f'width:96px; height:96px; border:{border}; border-radius:4px;'
                            'overflow:hidden; cursor:pointer; flex-shrink:0; position:relative;'
                            'background:#000;'
                        ).tooltip(f'{ts} | {room} | 第 {idx + 1} 张 | {label_text}')
                        with thumb_box:
                            ui.image(f'data:image/jpeg;base64,{b64}').classes('w-full h-full').style('object-fit:cover;')
                            ui.label(f'{idx + 1} · {label_text}').style(
                                'position:absolute; bottom:0; left:0; right:0;'
                                'background:rgba(0,0,0,0.72); color:#fff; font-size:9px;'
                                'text-align:center; padding:1px 2px;')
                        thumb_box.on('click', lambda _, r=rid, i=idx: _select_gallery_result(jp, r, i))

            def _select_gallery_result(jp, rid, idx):
                """点击 JSON 全图库中的缩略图，加载对应记录并显示大图。"""
                _load_and_show(jp, rid, result_idx=idx, rebuild_thumbs=False, notify=False)
                _build_json_gallery(jp)

            def _select_result(idx, results, rid):
                """点击缩略图选中并显示对应效果图"""
                _thumb_selected[0] = idx
                _display_result(results, idx, rid)
                _build_thumbnail_strip(results, rid)

            def _display_result(results, idx, rid):
                """把第 idx 张结果显示到大预览区"""
                if not results or idx is None or idx >= len(results): return
                res = results[idx]
                b64 = res.get('result_image_b64', '')
                mgr_result_holder.clear()
                if b64:
                    mgr_no_result_lbl.visible = False
                    with mgr_result_holder:
                        ui.html(
                            f'<img src="data:image/jpeg;base64,{b64}" '
                            'style="max-width:100%;max-height:100%;width:auto;height:auto;'
                            'object-fit:contain;border-radius:6px;display:block;" />'
                        ).classes('w-full h-full').style(
                            'display:flex; align-items:center; justify-content:center; overflow:hidden;')
                else:
                    mgr_no_result_lbl.visible = True
                mgr_comment_box.value = res.get('comment', '')
                try:
                    edit_source_lbl.text = f'当前修改对象：第 {idx + 1} 张效果图'
                except Exception as ex:
                    logger.debug(f"更新修改对象标签失败: {ex}")
                mgr_dl_holder.clear()
                if b64:
                    with mgr_dl_holder:
                        dl_name = f"result_{rid}_{idx+1}.jpg"
                        ui.html(f'<a href="data:image/jpeg;base64,{b64}" download="{dl_name}" class="dl-btn" '
                                f'style="position:static;display:inline-block;padding:4px 12px;">⬇️ 下载</a>')

            def _load_and_show(jp, rid, result_idx=None, rebuild_thumbs=True, notify=True):
                """读取并展示一条记录"""
                nonlocal _loaded_results
                records = _load_records(jp)
                target = next((r for r in records if r.get('id') == rid), None)
                if not target:
                    mgr_status.text = '❌ 未找到该记录'; return

                rec_state['json_path'] = jp
                rec_state['record_id'] = rid

                # 左侧
                mgr_ts_lbl.text = target.get('timestamp', '')
                sb64 = target.get('sample_image_b64', '')
                if sb64:
                    mgr_sample_img.set_source(f'data:image/jpeg;base64,{sb64}')
                    mgr_sample_img.visible = True
                else:
                    mgr_sample_img.visible = False
                mgr_params_box.value = target.get('params_summary', '') or target.get('prompt_en', '')

                # 右侧
                results = target.get('results', [])
                _loaded_results = results
                if results:
                    mgr_no_result_lbl.visible = False
                    if result_idx is not None:
                        _thumb_selected[0] = max(0, min(int(result_idx), len(results) - 1))
                    else:
                        _thumb_selected[0] = min(_thumb_selected[0], len(results) - 1)
                    _display_result(results, _thumb_selected[0], rid)
                    if rebuild_thumbs:
                        _build_thumbnail_strip(results, rid)
                else:
                    _thumb_selected[0] = 0
                    mgr_result_holder.clear()
                    mgr_no_result_lbl.visible = True
                    mgr_comment_box.value = ''
                    mgr_dl_holder.clear()
                    if rebuild_thumbs:
                        _build_thumbnail_strip([], rid)

                mgr_status.text = f'✅ {target.get("timestamp","")} | {len(results)} 张效果图'
                if notify:
                    ui.notify(f'✅ 已加载，{len(results)} 张效果图', type='positive')

            def _rebuild_rec_sel(jp):
                """销毁并重建记录条目选择器"""
                _rec_sel_box.clear()
                with _rec_sel_box:
                    labels = get_record_labels(jp) if jp else []
                    _scroll_props = 'virtual-scroll popup-content-style="max-height:400px;overflow-y:auto"'
                    if not labels:
                        _rec_sel_ref['w'] = ui.select(
                            [], label='📄 先选记录文件' if jp else '📄 先选记录文件'
                        ).classes('w-full').props(f'disable {_scroll_props}')
                        mgr_status.text = '⚠️ 该文件暂无记录' if jp else '请先选择记录文件'
                        return
                    opts = {v: l for l, v in labels}
                    def _on_rec_pick(e):
                        rid = e.value
                        if rid:
                            _load_and_show(jp, rid, result_idx=0, rebuild_thumbs=False)
                            _build_json_gallery(jp)
                    _rec_sel_ref['w'] = ui.select(
                        opts, value=None,
                        label=f'📄 选择条目 ({len(labels)}条)',
                        on_change=_on_rec_pick
                    ).classes('w-full').props(_scroll_props)
                    mgr_status.text = f'找到 {len(labels)} 条记录，点击条目自动加载'

            # 初始渲染
            _rebuild_rec_sel(None)

            # 文件选择
            def _on_json_pick(e):
                jp = e.value or ''
                rec_state['json_path'] = jp
                rec_state['record_id'] = ''
                _rebuild_rec_sel(jp)
                _build_json_gallery(jp)
                records = _load_records(jp) if jp else []
                first = next(
                    ((r.get('id', ''), i) for r in records for i, res in enumerate(r.get('results', [])) if res.get('result_image_b64')),
                    None
                )
                if first:
                    _load_and_show(jp, first[0], result_idx=first[1], rebuild_thumbs=False, notify=False)
                    _build_json_gallery(jp)
                else:
                    mgr_result_holder.clear()
                    mgr_no_result_lbl.visible = True
                    mgr_comment_box.value = ''
                    mgr_dl_holder.clear()
            mgr_json_sel.on_value_change(_on_json_pick)

            def _do_refresh():
                files = scan_json_files()
                mgr_json_sel.set_options(files)
                ui.notify(f'已刷新，{len(files)} 个文件', type='positive')

            def _do_export():
                jp = rec_state['json_path'] or mgr_json_sel.value
                if not jp: ui.notify('⚠️ 请先选择记录文件', type='warning'); return
                ui.notify(export_html_from_json(jp), type='positive')

            def _do_delete_record():
                jp = rec_state['json_path']; rid = rec_state['record_id']
                if not jp or not rid:
                    ui.notify('⚠️ 请先选择一条记录', type='warning'); return
                if not _delete_record(jp, rid):
                    ui.notify('❌ 删除失败', type='negative'); return
                rec_state['record_id'] = ''
                nonlocal _loaded_results; _loaded_results = []
                _thumb_selected[0] = 0
                mgr_result_holder.clear()
                mgr_no_result_lbl.visible = True
                mgr_comment_box.value = ''
                mgr_dl_holder.clear()
                _build_thumbnail_strip([], '')
                _build_json_gallery(jp)
                _rebuild_rec_sel(jp)
                ui.notify('✅ 记录已删除', type='positive')


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
        try:
            img, err = await asyncio.to_thread(
                call_image_generate, api_key, model_id, prompt_text,
                pnp, ims, ar, rp, sref, _on_stage, should_cancel, bevel_ref)
        except Exception as e:
            img, err = None, str(e)
        finally:
            try: setattr(job, f'{stage_key}_stage', '')
            except Exception as ex: logger.debug(f"清空阶段状态失败 job={job.job_id}: {ex}")
        setattr(job, f'{stage_key}_secs', round(time.time() - _t0, 1))
        path = None
        if img is not None:
            path = _save_api_result_jpg(img, model_name, pnp)
            setattr(job, f'{stage_key}_path', path)
            try: _refresh_job_card(job)   # ← 这张图立刻单独显示，不等另一个模型
            except Exception as ex: logger.warning(f"刷新单模型卡片失败: {ex}")
            try: await asyncio.to_thread(_api_write_to_record, img, model_name, jpt, rid)
            except Exception as ex: logger.warning(f"写记录失败 job={job.job_id} {model_name}: {ex}")
        return path, (err or '')

    def _compute_final_status(model_filter: str, b2_path, pro_path) -> str:
        if model_filter == 'b2':  return 'done' if b2_path else 'failed'
        if model_filter == 'pro': return 'done' if pro_path else 'failed'
        return 'done' if (b2_path and pro_path) else ('partial' if (b2_path or pro_path) else 'failed')

    async def _retry_job(job: JobRecord):
        """重试失败/部分完成的任务：用提交时存下的生成上下文，只重跑还没出图的模型。"""
        ctx = job.retry_ctx
        if not ctx:
            ui.notify('该任务缺少重试信息，请重新提交', type='warning'); return
        if job.status not in ('failed', 'partial'):
            return
        api_key = ctx.get('api_key', '')
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
        _update_job(job, status='running', started_at=time.time(), error='', b2_stage='', pro_stage='')
        try: _refresh_job_card(job)
        except Exception as ex: logger.debug(f"刷新重试卡片失败 job={job.job_id}: {ex}")
        logger.info(f"[任务] retry job={job.job_id}, need_b2={need_b2}, need_pro={need_pro}")
        async with _gen_semaphore:
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
            final = _compute_final_status(mf, b2j, proj)
            _update_job(job, status=final, b2_path=b2j, pro_path=proj, error=err_msg.strip())
            logger.info(f"[任务] retry_finished job={job.job_id}, status={final}, b2={bool(b2j)}, pro={bool(proj)}")
            try: _refresh_job_card(job)
            except Exception as ex: logger.warning(f"刷新重试卡片失败: {ex}")
            _notify_job_end(job)

    async def _run_job(model_filter='both'):
        api_key = api_key_inp.value.strip()
        if not api_key:
            logger.warning("[任务] 提交失败：缺少 API Key")
            ui.notify('⚠️ 缺少 API Key', type='warning'); return
        if not floor_path['v']:
            logger.warning("[任务] 提交失败：未上传地板图")
            ui.notify('⚠️ 请上传地板图', type='warning'); return
        if not os.path.exists(str(floor_path['v'])):
            logger.error(f"[任务] 提交失败：地板图文件不存在 path={floor_path['v']}")
            ui.notify('⚠️ 地板图文件不存在，请重新上传', type='warning'); return

        model_label = {'b2': '[B2]', 'pro': '[Pro]', 'both': '[双模型]'}.get(model_filter, '')
        display_room_type = cn_room_type_sel.value if _market_mode['v'] == 'cn' else room_type_sel.value
        dname = f"{os.path.splitext(os.path.basename(floor_path['v']))[0]} · {display_room_type} {model_label}"
        job = _new_job(dname, time.strftime('%H:%M:%S'), model_filter)
        _add_job_card(job)
        logger.info(
            f"[任务] submitted job={job.job_id}, name={dname}, model_filter={model_filter}, "
            f"workflow={workflow_radio.value}, market={_market_mode['v']}, floor={floor_path['v']}, "
            f"room_type={display_room_type}, ref_mode={'参照模式' in workflow_radio.value}, ref_path={ref_path['v'] or ''}"
        )
        ui.notify('任务已提交到队列', type='positive')

        jid = job.job_id
        cancel_generation = _cancel_generation[0]

        # 等待信号量前先检查取消标志
        if _is_cancelled(jid, cancel_generation):
            _update_job(job, status='failed', error='已取消（用户停止）')
            try: _refresh_job_card(job)
            except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
            return

        async with _gen_semaphore:
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
                _is_ref_mode = '参照模式' in workflow_radio.value
                _sref_text = ref_correction_inp.value.strip() if _is_ref_mode else ""

                # 参照模式 Step-1: 用文字模型提取风格描述
                _style_analysis = ""
                if _is_ref_mode and ref_path['v']:
                    _update_job(job, status='running')
                    try: _refresh_job_card(job)
                    except Exception as ex: logger.debug(f"刷新任务卡片失败: {ex}")
                    _style_analysis, _sa_err = await asyncio.to_thread(analyze_style_image, api_key, ref_path['v'])
                    if _sa_err or not _style_analysis:
                        # 分析失败必须中止：错误文本一旦混进提示词，会照常发起计费生成、产出废图
                        _update_job(job, status='failed',
                                    error=f'风格分析失败，已中止（未发起生图）: {_sa_err or "返回为空"}')
                        try: _refresh_job_card(job)
                        except Exception as ex: logger.warning(f"刷新任务卡片失败: {ex}")
                        logger.error(f"[参照模式] 风格分析失败，任务中止 job={job.job_id}: {_sa_err}")
                        _notify_job_end(job)
                        return
                    logger.info(f"[参照模式] 风格提取完成: {_style_analysis[:120]}...")

                _, sms, prt, saved_image_path, jpt, rid, pnp, prt_pro = await asyncio.to_thread(
                    save_task_files_html, workflow_radio.value, 'Pro', floor_path['v'],
                    continent_sel.value, (_country_ref['w'].value if _country_ref['w'] else ''), (_city_ref['w'].value if _city_ref['w'] else ''), hood_inp.value, prop_sel.value,
                    (style_sel_ref['w'].value if style_sel_ref['w'] else None), room_type_sel.value, view_sel.value, light_sel.value,
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
                    cn_room_type=cn_room_type_sel.value if _is_cn else room_type_sel.value,
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
                # 参照模式：生图 API 只收地板图，不传风格参照图
                rp = None if _is_ref_mode else (room_path['v'] or None)
                _sref_api = None  # 参照模式风格已转成文字，无需图片注入
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

                # 根据 model_filter 决定运行哪些模型
                run_b2 = model_filter in ('b2', 'both')
                run_pro = model_filter in ('pro', 'both')

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

                final_status = _compute_final_status(model_filter, b2j, proj)

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
                    files = scan_json_files()
                    mgr_json_sel.set_options(files)
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

    gen_b2_btn.on_click(lambda: _run_job('b2'))
    gen_pro_btn.on_click(lambda: _run_job('pro'))
    gen_both_btn.on_click(lambda: _run_job('both'))
    stop_all_btn.on_click(lambda: _request_stop_all())

    # 已用时间计时器：建在页面根槽位（最稳），避免卡片容器销毁后计时器报 parent slot 错误
    ui.timer(1.0, _tick_job_times)

    # 进页面后请求一次浏览器通知权限（任务完成/失败时弹系统通知用），用户点“允许”即可
    def _request_notify_perm():
        try:
            ui.run_javascript("try{if(window.Notification&&Notification.permission==='default'){Notification.requestPermission();}}catch(e){}")
        except Exception as ex:
            logger.warning(f"请求通知权限失败: {ex}")
    ui.timer(1.5, _request_notify_perm, once=True)
