# Floor Engine Server（地板AI生图引擎 · 商业版）— 开发者手册

> 本仓库是从老 `floor_engine`(NiceGUI 单体) **fork 出的商业版**，架构已拆成
> **FastAPI 无头后端 (`server_api.py`) + Next.js 前端 (`web/`)**，引擎模块(api/prompts/records/...)原样复用、零改动。
> 老 `webui.py` 暂留作过渡基线，待功能验收无误后(STEP 3)删除。

---

## 零、快速启动（先看这里）⭐

### TL;DR — 一键启动
双击 **`test\一键启动.bat`**（在本仓库的**上一级** `test\` 目录里）。它会:
1. 开「后端」窗口 → FastAPI 跑在 **http://127.0.0.1:7870**
2. 开「前端」窗口 → Next.js dev 跑在 **http://localhost:3000**
3. 等 ~8 秒前端编译完，自动打开浏览器到 `localhost:3000`

**停止**：关掉对应的「后端/前端」窗口即可。
**注意**：`一键启动.bat` 必须是 **纯 ASCII + 无 BOM + CRLF** 换行，否则中文版 Windows 的 cmd 会乱码崩(踩过坑)。

### 手动启动（调试时分开跑）
```bash
# 后端（在 test/ 目录下，把本仓库当包导入）——必须单 worker
python -m Floor_engine_server.server_api
# 等价：uvicorn Floor_engine_server.server_api:app --host 127.0.0.1 --port 7870 --workers 1

# 前端（在 Floor_engine_server/web/ 目录下）
npm run dev        # → http://localhost:3000
```
环境变量（可选）：后端 `FLOOR_API_HOST` / `FLOOR_API_PORT`(默认 127.0.0.1:7870) / `FLOOR_API_CORS`(放行的前端源，默认含 localhost:3000)；前端 `web/.env.local` 的 `NEXT_PUBLIC_API_BASE`(默认 `http://127.0.0.1:7870`)。

### 首次准备（装依赖，只做一次）
```bash
pip install -r Floor_engine_server/requirements.txt     # 后端(含 fastapi/uvicorn/python-multipart)
cd Floor_engine_server/web && npm install               # 前端
```

### 启动逻辑（发生了什么）
- **后端 `server_api.py`**：FastAPI app。**lifespan 启动钩子**里——按 `engine_config.json` 的 `max_concurrent_per_model`(默认1) 在**本进程事件循环**上创建 B2/Pro 并发信号量 + prep 锁，并 `load_persisted_jobs()` 从 `.queue_state.json` 恢复历史任务卡。**必须单 worker**：`_job_history`、信号量都是进程内状态，多 worker 不共享。
- **生图不阻塞 HTTP**：`POST /api/jobs` 秒回 `job_id`，真正生成在后台 task 跑；前端用 **SSE**(`GET /api/jobs/{id}/stream`，`EventSource`) 看实时进度。这绕开了"长 4K 请求把连接撑爆被软路由 reset"的老毛病。
- **前端 `web/`**：Next.js 16(App Router, Turbopack)。页面全是 `'use client'` + `useEffect` 取数，通过 `lib/api.ts`(读 `NEXT_PUBLIC_API_BASE`) 调后端;图片用普通 `<img>`(避开 Next16 对本地 IP 的 `next/image` 拦截)。
- **数据位置**：`config.BASE_DIR = dirname(dirname(__file__))`——本仓库在 `test/` 下时解析到 **`test/`**，故与老 app **共享** `output_files/` / `engine_config.json`(含 API key) / `_ng_uploads/` / `.queue_state.json`。把本仓库迁出 `test/` 即自动获得独立数据目录，无需改码。

### 端口 / 进程
| 进程 | 端口 | 命令 | 说明 |
|---|---|---|---|
| 后端 FastAPI | 7870 | `python -m Floor_engine_server.server_api` | 无头 API + SSE，单 worker |
| 前端 Next.js | 3000 | `cd web && npm run dev` | 商业版界面 |
| (旧)NiceGUI | 7869 | `python -m Floor_engine_server`（即 webui） | 过渡基线，STEP3 删 |

### 前端页面
`/` 生成（上传+参数+队列+SSE）· `/records` 记录管理（搜索/筛选/二改/删除/收藏/解密/导出）· `/usage` 用量 · `/settings` 设置（key/线路/网络/TLS/并发/连通自检）。

---

## 一、项目概述

**地板 AI 智能提示词引擎** — 上传地板小样图 → 组装专业英文 prompt → 调 Gemini API 生成 photorealistic 室内效果图。

- **目标客户**：地板品牌的社媒/网站部门
- **卖点**：视觉真实度（不是预览精度），核心壁垒在提示词工程 + 中国市场参数系统
- **技术栈（商业版）**：后端 Python 3.10+ / **FastAPI + uvicorn**（无头 API + SSE）；前端 **Next.js 16 (App Router) + React 19 + Tailwind v4 + shadcn/ui**；生图 Google Gemini API（备线 Fal）/ PIL / numpy。
  - 旧版 UI 为 NiceGUI(Quasar/Vue3)，即 `webui.py`，过渡期保留。

## 二、模块地图

```
Floor_engine_server/          # 商业版独立仓库（本仓库）
├── server_api.py         # ★ FastAPI 无头服务层：把引擎暴露为 REST+SSE，前端对接。引擎零改
├── __init__.py           # 包标记，公共 API 文档
├── __main__.py           # 旧 NiceGUI 入口：python -m Floor_engine_server（即跑 webui，7869）
├── config.py             # 核心配置：路径、API key 持久化、工具函数
├── themes.py             # （旧 NiceGUI 主题；新前端主题在 web/src/app/globals.css）
├── logging_setup.py      # 日志初始化（文件 + 控制台）
├── models.py             # 纯数据类 + 任务生命周期/候选累积纯逻辑：JobRecord, TaskParams
├── records.py            # JSON CRUD、文件化图片存储、队列持久化、并发锁、用量统计、加密、导出(HTML/PPTX)
├── prompt_data.py        # 提示词数据字典：翻译表、风格/色调/CN市场参数、宠物、地区级联、识色
├── prompts.py            # 提示词组装引擎：4 套工作流的 prompt 拼接逻辑
├── api.py                # Gemini/Fal 客户端：生图、编辑、风格分析、色彩迁移、连通自检
├── webui.py              # ⚠️旧 NiceGUI 界面（过渡基线，STEP3 删；新功能不要往这里加）
├── recipes.py / failure_kb.py
├── requirements.txt      # Python 依赖（已含 fastapi/uvicorn/python-multipart）
└── web/                  # ★ Next.js 16 前端
    ├── src/app/          # 页面：page(生成)/records/usage/settings + layout + globals.css(品牌主题)
    ├── src/components/   # FloorUploader / ParamsForm / JobCard / ImageZoom / Nav / ui(shadcn)
    ├── src/lib/          # api.ts(端点封装)/types.ts/notify.ts
    └── src/hooks/        # useJobStream.ts(SSE)
```
> **改后端能力** → `server_api.py` 加端点(引擎模块尽量不改)。**改界面** → `web/`。**勿动 `webui.py`**。

### 2.1 各模块职责

#### `config.py` — 全局配置中心
- **职责**：路径常量 (`BASE_DIR`, `MAIN_OUTPUT_DIR`, `CONFIG_FILE`)、API key 读写 (`_load_config`, `_save_config`, `save_api_key`)、Gemini 模型名映射 (`GEMINI_MODEL_MAP`)、工具函数 (`_short_text`, `is_seamless_herringbone`, `extract_clean_prompt`)
- **多供应商**：`FAL_MODEL_MAP`（Gemini model_id → Fal endpoint）、`DEFAULT_IMAGE_PROVIDER`、`save_provider_settings()` / `get_image_provider()`（google / fal 线路选择 + Fal key）；直连失败自动转 Fal 的开关（默认关）
- **圆弧倒角参考图**：`BEVEL_REF_IMAGE_DEFAULT`（默认 `assets/bevel_ref_clean_a.jpg`）、`get_bevel_ref_image()`（可被 `engine_config.json` 的 `bevel_ref_image` 覆盖；备选 clean_b/c 同在 assets/）
- **依赖**：`themes.py`（导入 THEMES + build_theme_css）、`logging_setup.py`（导入 logger）
- **被引用**：所有其他模块都依赖它

#### `themes.py` — UI 主题系统
- **职责**：单一主题「Anthropic 暖陶米色」的色板定义 (`THEMES` 字典)、`build_theme_css()` 生成完整 `<style>` 块；字体走系统微软雅黑、完全离线（见 `_FONT_STACK`）。设计规范见项目根 `DESIGN.md`
- **依赖**：无内部依赖
- **修改主题**：改 `THEMES["Anthropic 暖陶米色"]` 的令牌即可
- **添加新主题**：在 `THEMES` 中新增一个键，格式参照现有主题；webui.py 的固定调用也需同步改名

#### `logging_setup.py` — 日志
- **职责**：初始化 logger，输出到 `app_local_save.log` + 控制台
- **依赖**：无内部依赖（独立计算 BASE_DIR）

#### `models.py` — 数据模型 + 纯逻辑
- **职责**：
  - `JobRecord`（队列任务状态）、`TaskParams`（save_task_files_html 的 35 个参数的结构化文档）、`task_params_to_kwargs()`（转换回旧式 kwargs）
  - **任务生命周期纯函数**（从 webui 下沉）：`new_job` / `update_job` / `compute_final_status` / `job_time_text`
  - **候选累积纯逻辑**：`CANDIDATE_SLOTS`（b2/pro/pro_polish）、`add_candidate`（成图并入候选，超 `MAX_CANDIDATES_PER_SLOT=12` 丢最旧）、`nav_candidate`（‹n/N› 左右切换）、`ensure_candidate_lists`（向后兼容回填）
- **依赖**：仅 Python 标准库 `dataclasses`
- **添加新参数**：在 `TaskParams` 中加字段，同步更新 `task_params_to_kwargs()`

#### `records.py` — 持久化层
- **职责**：
  - JSON 文件 CRUD（`_load_records`, `_save_records`, `_delete_record`, `_delete_result_image`）；写盘走"临时文件 + 原子替换"防截断
  - 图片 ↔ base64 转换（`_img_to_b64`, `_b64_to_pil`）
  - **文件化图片存储**：结果图落盘后只在记录里存相对路径 `result_image_file`（不再内联大 base64）；读取时优先文件、回退 base64。`_rel_result_path()` 生成相对路径（挡 `..`），`_safe_output_path()` 在读取侧再做 realpath+commonpath 越界二次校验
  - **旧记录迁移**：`migrate_record_file()` — 把历史内联 base64 抽成独立文件，迁移前自动 `.bak` 备份、幂等、单张失败保留其 base64 容错
  - API 结果落盘 + 写入记录（`_save_api_result_jpg`, `_api_write_to_record`）
  - **队列持久化**：`persist_jobs()` / `load_persisted_jobs()` — 把 `_job_history` 序列化到 `.queue_state.json`（最多 60 条），重启后恢复任务卡片
  - **并发锁**：`record_file_lock()` — 按文件路径取锁，双模型 B2/Pro 在两个 worker 线程并发 append 同一记录 JSON 时防丢
  - **用量统计**：`record_usage()` / `load_usage_summary()` — 按模型/工作流累计调用量
  - **辅助扫描**：`_list_recent_floor_swatches()`（最近素材）、`room_type_counts()`（记录管理按房型筛选计数，纯函数）
  - **收藏**：`toggle_result_favorite()` / `collect_favorites()`（按 `favorite` 标记跨材料汇总）
  - **PPTX 提案导出**：`export_pptx_from_json()`（单材料）、`export_favorites_pptx()`（全部⭐合一份）、`_build_pptx()`（16:9，标题页 + 每图一页）
  - 提示词加密/解密（`_obfuscate` / `_deobfuscate`，XOR 密码）
  - 记录浏览/导出（`scan_json_files`, `get_record_labels`, `export_html_from_json`）
  - 手动追加/二次修改写入（`append_result_to_log`, `append_edited_result_to_record`）
- **依赖**：`config.py`（路径 + logger）、`python-pptx`（仅导出 PPTX 时）
- **⚠️ 图片质量关键代码**：`_img_to_b64()` 的 `max_width` 参数和 `quality=85`；`_save_api_result_jpg()` 的 `quality=95`

#### `prompt_data.py` — 提示词数据层
- **职责**：所有静态数据字典和翻译/分析逻辑
  - `FALLBACK_DICT` / `TECH_DICT` / `LOCATION_MAP` — 中英翻译表（已覆盖全部城市/国家/房型/景观/规格/宠物品种等预设选项）
  - `translate_zh_to_en()` — **先查 `TECH_DICT`/`FALLBACK_DICT` 命中即返回，未命中才走在线翻译**（GoogleTranslator 走代理）；彻底失败回退裸原文（不再包 `[ ]` 方括号）。预设选项全在表里 → 在线翻译只兜"自定义补充"等手动输入
  - `STYLES` / `STYLE_ATMOSPHERE_MAP` — 32 种风格（10 海外 + 22 扩充）的 must/ban/atm 定义
  - `FLOOR_TONES` / `FLOOR_TONE_CONTRAST_MAP` / `FLOOR_TONE_STYLE_RECOMMEND_MAP` — 12 种地板色调 + 家具对比色指令 + 色调×风格推荐度
  - `LIGHTINGS` / `LIGHTING_INSTRUCTION_MAP` — 6 种光线模式
  - `PET_TYPES` / `PET_ACTIONS` / `PET_FOCUS_OPTIONS` — 宠物模式选项（品种英文已进 `FALLBACK_DICT`）
  - `MARKET_FURNITURE_CHOICES` / `MARKET_FURNITURE_MAP` — 家具市场风格（按品牌画像锁定家具）
  - `CN_*` — 中国市场完整参数系统（开发商/户型/空间/交付/标配设施）
  - `analyze_floor_tone()` — HSV 色调自动分析 + 方向性检测
  - `get_style_choices()` — 按色调推荐排序风格列表
  - `build_overseas_realism_layer()` — 海外市场真实感增强层
  - `build_cn_layout_guidance()` — 国内户型规模感知建模指导
- **依赖**：`config.py`（logger + TRANSLATOR_AVAILABLE）、`deep-translator`（GoogleTranslator，可选）、`numpy`（`analyze_floor_tone`）
- **添加新风格**：在 `STYLES` 列表中加条目 + 在 `STYLE_ATMOSPHERE_MAP` 中加对应的 must/ban/atm 定义

#### `prompts.py` — 提示词组装引擎（核心业务逻辑）
- **职责**：`save_task_files_html()` — 接收所有参数，组装最终的英文 prompt。4 套工作流：
  1. **纯效果图** — 生成全新空间
  2. **地板替换** — Inpainting，只换地板不动其他
  3. **宠物友好** — 带宠物的室内场景
  4. **参照模式** — **图+文混合**：参照图直接当 `style_ref_image_path` 喂模型（`webui.py` `_sref_api = ref_path['v']`），`analyze_style_image()` 提取的文字风格降级为辅助强调；语境按全局市场开关走海外/国内分支
- **关键常量**：`FLOOR_COLOR_MATCH_INSTRUCTION` — 地板颜色锁定指令（强制模型不偏色）
- **依赖**：`config.py`、`prompt_data.py`、`records.py`
- **⚠️ 修改 prompt 逻辑时**：先跑 `tests/test_prompts_golden.py` 确认输出变化是否符合预期

#### `api.py` — 生图 API 客户端（多供应商）
- **职责**：
  - `call_image_generate()` — **统一生图入口**：按 `get_image_provider()` 路由到 Google 或 Fal；网络类失败（`_is_network_class_error`）且开关开启时自动转 Fal 备线
  - `call_gemini_generate()` — Google 直连文生图 / 图生图（重试 + 指数退避，次数读 `engine_config.json`）
  - `call_fal_generate()` — 走 Fal 路由调同款 Nano Banana（`FAL_MODEL_MAP`，保真/4K 不变，仅换线路）
  - `call_gemini_edit()` — 图生图编辑（二次修改 / 磨缝）
  - `analyze_style_image()` — 参照模式 Step-1：文字模型提取风格描述；带磁盘缓存（`_style_cache_*`，按图片内容 hash）
  - `_match_color_to_reference()` — Reinhard 色彩迁移（LAB 空间，消除 img2img 偏色）
  - `test_connection()` — 同时测 Gemini + Fal 连通性
  - `FLOOR_DESEAM_INSTRUCTION` — 磨缝编辑指令
- **依赖**：`config.py`、`records.py`
- **⚠️ 仅本地运行**：`verify=False` 仅适用于本地代理场景

#### `webui.py` — Web 界面
- **职责**：
  - 2-Tab 布局：工作台（生成 + 队列）+ 记录管理
  - 文件上传、参数选择、任务提交、实时队列刷新
  - **双模型生成 + 批量生成（多场景）**：批量任务跑在独立 asyncio task 里，UI 更新须 `background_tasks.create` + `with client` 保住 slot 上下文
  - **逐张多抽（重抽 regen）**：按张数一张一张追加到**同一张卡**累积候选，‹n/N› 左右切换（`models.add_candidate`/`nav_candidate`），可中途"停止多抽"
  - 停止/取消（单任务 + 全部停止）、失败重试
  - 磨缝、二次修改、记录删除
  - **收藏⭐ + 筛选**："只看收藏"开关、收藏夹一键导出 PPTX
  - 导出：HTML / PPTX（单材料）、记录文件迁移入口
  - **结果图缩略图**：卡片/记录列表显示走 `/thumb/outputs` 端点（`_result_thumb_url`，懒生成+磁盘缓存），点击放大/下载才用原图——避免浏览器为列表解整张 4K
  - **内存自动收口**：常驻卡片超 `_MAX_RESIDENT_CARDS`(30) 时 `_auto_trim_cards()` 删最旧的已完成卡（running/pending 永不删，磁盘记录仍可查）；候选每槽上限 12
- **依赖**：所有其他模块 + NiceGUI
- **UI 框架**：NiceGUI (Quasar/Vue3)，单页应用，WebSocket 实时更新

#### `__main__.py` — 启动器
- **职责**：`python -m floor_engine` → 启动 NiceGUI 服务 + 自动打开浏览器
- **环境变量**：`FLOOR_AI_PORT`（端口，默认 7869）、`FLOOR_AI_RELOAD`（热重载，默认关闭）

## 三、未来功能开发指南

### 3.1 按需求找文件

| 想做什么 | 去哪个文件 | 为什么 |
|----------|-----------|--------|
| **加新的地板风格** | `prompt_data.py` | `STYLES` 列表 + `STYLE_ATMOSPHERE_MAP` |
| **加新的地板色调** | `prompt_data.py` | `FLOOR_TONES` + `FLOOR_TONE_CONTRAST_MAP` + `FLOOR_TONE_STYLE_RECOMMEND_MAP` |
| **加新的光线模式** | `prompt_data.py` | `LIGHTINGS` + `LIGHTING_INSTRUCTION_MAP` |
| **加新的中国市场参数**（开发商/户型/空间特征） | `prompt_data.py` | `CN_*` 系列字典 |
| **修改 prompt 措辞/结构** | `prompts.py` | `save_task_files_html()` 里的 4 个 f-string 模板 |
| **修改地板颜色锁定的强度** | `prompts.py` | `FLOOR_COLOR_MATCH_INSTRUCTION` 常量 |
| **无缝/磨缝策略调整** | `prompts.py` | `save_task_files_html()` 里 `is_seamless_clean` 分支（~200 行） |
| **换 AI 模型**（比如换成 GPT-4o 生图） | `api.py` | `call_gemini_generate()` + `GEMINI_MODEL_MAP` in `config.py` |
| **改 API 重试策略** | `api.py` + `engine_config.json` | `_retry_plan()` 读 `retry_attempts` / `retry_backoffs`；Fal 另有 `fal_retry_attempts` |
| **改图片存储质量** | `records.py` | `_img_to_b64()` 的 `quality=85`、`_save_api_result_jpg()` 的 `quality=95` |
| **改 UI 主题/配色** | `themes.py` | `THEMES` 字典 |
| **加新的 UI 控件/按钮** | `webui.py` | NiceGUI 组件树 |
| **加新的工作流模式**（比如"商业空间"） | `prompts.py` + `webui.py` | prompt 模板 + UI 参数面板 |
| **加用户认证系统** | `webui.py` + `config.py` | NiceGUI 登录页 + 配置文件 |
| **改数据加密方式** | `records.py` | `_obfuscate` / `_deobfuscate` + `_load_reveal_hash` |
| **加批量生成** | `webui.py` | `_run_job()` 的循环调用 |
| **加导出格式**（PDF/PPTX） | `records.py` | 新建 `export_xxx_from_json()` |
| **加任务队列持久化**（重启不丢队列） | `records.py` + `webui.py` | JSON 序列化 `_job_history` |
| **改用 SQLite 替代 JSON** | `records.py` | 重写所有 `_load_records` / `_save_records` 调用 |
| **加单元测试 / 黄金对比** | `tests/`（已建：`tests/test_prompts_golden.py` + `tests/golden/`） | pytest；prompt 文本快照比对，纯本地零 API |
| **改密码** | 环境变量 `FLOOR_ENGINE_REVEAL_HASH` 或 `engine_config.json` 的 `reveal_hash` 字段 | 不再硬编码在源码里 |

### 3.2 开发工作流

**商业版（首选）——见「零、快速启动」**：双击 `test\一键启动.bat`，或手动两个进程：
```bash
python -m Floor_engine_server.server_api      # 后端 7870（在 test/ 下）
cd Floor_engine_server/web && npm run dev      # 前端 3000
# 改后端：server_api.py(加端点) / 引擎模块。uvicorn 不自动重载 → 重启后端生效。
# 改前端：web/ 下文件，npm run dev 自动热重载，刷新浏览器即可。
# 前端验类型/构建：cd web && npx tsc --noEmit && npm run build
```

<details><summary>旧 NiceGUI 单体启动方式（过渡期保留，STEP3 后废弃）</summary>

```bash
python -m Floor_engine_server          # 旧 webui，端口 7869
set FLOOR_AI_PORT=7890 && python -m Floor_engine_server
```
</details>

> ✅ **测试基建已建立（prompt 黄金回归）**：`tests/test_prompts_golden.py` 固定参数跑
> `save_task_files_html()` 的 4 套工作流，把返回的 combined/Pro 两段 prompt 与 `tests/golden/*.txt`
> 基准做字符串比对。纯本地、不联网、不调 API、不写真实 `output_files/`（见 `tests/conftest.py`：
> 隔离输出目录 + 强制离线翻译 `TRANSLATOR_AVAILABLE=False`）。
>
> ```bash
> python -m pytest floor_engine/tests/ -q              # 改 prompts.py 后跑回归
> UPDATE_GOLDEN=1 python -m pytest floor_engine/tests/ # 有意改了 prompt 后刷新基准（务必人工核对 diff！）
> ```
>
> 加新工作流/改 prompt 措辞时：先跑回归看 diff；确认是预期改动后用 `UPDATE_GOLDEN=1` 刷新并提交新基准。

### 3.3 代码规范（新贡献者必读）

1. **禁止 `import *`** — 所有模块已改为显式导入，新代码请保持一致
2. **加类型标注** — 新函数签名必须有 type hints
3. **改 prompts.py 前/后跑黄金回归** — `python -m pytest floor_engine/tests/ -q`；预期内的改动用 `UPDATE_GOLDEN=1` 刷新基准并人工核对 diff（见 3.2）
4. **图片质量相关改动要谨慎** — `_img_to_b64(quality=85)` 和 `_save_api_result_jpg(quality=95)` 直接影响客户交付质量
5. **API key 不能出现在日志里** — 使用 `_redact_api_key()` 包裹

## 四、数据流与架构图

### 4.1 模块依赖图

```
                    ┌─────────────┐
                    │  __main__.py │  (启动器)
                    └──────┬──────┘
                           │ from .webui import *
                           ▼
                    ┌─────────────┐
                    │   webui.py  │  (UI + 任务调度)
                    └──────┬──────┘
                           │
            ┌──────────────┼──────────────────┐
            │              │                  │
            ▼              ▼                  ▼
     ┌──────────┐   ┌──────────┐      ┌──────────┐
     │  api.py  │   │prompts.py│      │records.py│
     │(Gemini)  │   │(prompt)  │      │(持久化)  │
     └────┬─────┘   └────┬─────┘      └────┬─────┘
          │              │                  │
          │         ┌────┴─────┐            │
          │         │          │            │
          │         ▼          ▼            │
          │  ┌──────────┐ ┌──────────┐      │
          │  │prompt_   │ │ records  │      │
          │  │data.py   │ │  .py     │      │
          │  │(数据字典) │ │(JSON CRUD)│     │
          │  └────┬─────┘ └────┬─────┘      │
          │       │            │            │
          │       ▼            ▼            │
          │  ┌──────────────────────┐       │
          │  │     config.py        │◄──────┘
          │  │  (路径/配置/工具)     │
          │  └──────────┬───────────┘
          │             │
          │      ┌──────┴──────┐
          │      │             │
          │      ▼             ▼
          │  ┌────────┐  ┌──────────┐
          │  │themes  │  │logging   │
          │  │.py     │  │_setup.py │
          │  └────────┘  └──────────┘
          │
          ▼
    ┌───────────┐
    │ models.py │  (纯数据，无依赖)
    └───────────┘
```

### 4.2 一次"双模型生成"请求的完整数据流

```
用户点击 "⚡ 双模型生成"
        │
        ▼
 webui._run_job('both')
        │
        ├─ 1. 校验 API key + 地板图存在
        ├─ 2. 创建 JobRecord，添加到 _job_history
        ├─ 3. 调用 _add_job_card() 在 UI 插入卡片
        │
        ├─ 4. 获取信号量 (asyncio.Semaphore(5))
        │
        ├─ 5. [参照模式] analyze_style_image() ─── Gemini 文字 API
        │      └─ 返回风格描述文本（辅助）；同时参照图本身会作为 style_ref 直接喂给第 8 步生图
        │
        ├─ 6. save_task_files_html() ─── prompts.py
        │      │
        │      ├─ 图片预处理 (RGBA→RGB, ICC→sRGB, thumbnail 4096)
        │      ├─ 翻译所有中文参数 → 英文 (prompt_data.translate_zh_to_en)
        │      ├─ 匹配风格 must/ban (STYLE_ATMOSPHERE_MAP)
        │      ├─ 匹配色调→家具对比色指令 (FLOOR_TONE_CONTRAST_MAP)
        │      ├─ 构建 CN 市场上下文块 (如开启)
        │      ├─ 构建海外真实感层 (如非 CN)
        │      ├─ 无缝模式处理 (替换 CORE_MATERIAL_INSTRUCTION)
        │      ├─ 拼接 4 套工作流之一的 f-string 模板
        │      ├─ 追加 FLOOR_COLOR_MATCH_INSTRUCTION
        │      ├─ 生成 B2 版 + Pro 版两个 prompt
        │      ├─ 写入 JSON 记录 (_save_records)
        │      └─ 返回 (processed_img, msg, combined_prompt, json_path, record_id, png_path, pro_prompt)
        │
        ├─ 7. extract_clean_prompt() ─── 去掉 UI 装饰文本，取纯英文 prompt
        │
        ├─ 8. asyncio.gather(
        │        call_gemini_generate(B2 model,  cpt)  ─── Gemini 生图 API
        │        call_gemini_generate(Pro model, cpt)  ─── Gemini 生图 API
        │    )
        │      │
        │      └─ 各 3 次重试 + 指数退避
        │
        ├─ 9. _save_api_result_jpg() ─── 落盘到 output_files/ (JPEG quality=95)
        │
        ├─ 10. _api_write_to_record() ─── 写入 JSON 记录 (存相对路径 result_image_file，非内联 base64)
        │
        └─ 11. _refresh_job_card() ─── 更新 UI 卡片 (走 /thumb/outputs 缩略图 + 下载链接[原图] + 耗时)
```

### 4.3 关键数据结构

```
engine_config.json          # API key + proxy + theme + reveal_hash
output_files/               # 所有生成结果
  ├── {素材名}/
  │   ├── {素材名}_记录.json   # 单素材的所有记录
  │   └── {素材名}_优化图.png   # 预处理后的素材图
  ├── {素材名}_Nano_Banana_2_{时间戳}.jpg   # B2 生成结果
  └── {素材名}_Nano_Banana_Pro_{时间戳}.jpg # Pro 生成结果

JSON 记录结构:
{
  "id": "20260604_143022",
  "timestamp": "2026-06-04 14:30:22",
  "workflow_mode": "纯效果图 (生成全新空间)",
  "room_type": "客餐厅一体",
  "params_summary": "纯效果图 · 客餐厅一体 · 原木风 ...",
  "prompt_en": "Help me make a photo:\n...",     # B2 版英文 prompt
  "prompt_en_pro": "Help me make a photo:\n...",  # Pro 版英文 prompt
  "_pe": "base64加密的prompt",                    # 加密后的 prompt
  "sample_image_b64": "base64缩略图(max_width=400)",
  "results": [
    {
      "result_timestamp": "2026-06-04 14:31:45",
      "result_image_file": "客餐厅一体/客餐厅一体_优化图_xxx.jpg",  # 相对 output_files 的路径(quality=95)
      # 注：旧记录未迁移时这里可能仍是 result_image_b64(内联 base64 回退)，migrate_record_file() 可批量抽成文件
      "comment": "API 自动生成 (3840×2160)",
      "model_label": "Nano Banana 2"
    },
    ...
  ]
}
```

### 4.4 色调分析流程

```
用户上传地板图
       │
       ▼
analyze_floor_tone() ─── prompt_data.py
       │
       ├─ 裁掉边缘 20%（去边框干扰）
       ├─ 缩放到 160×160（加速）
       ├─ RGB → HSV
       ├─ 计算中位数 H, S, V
       ├─ Sobel 边缘检测 → 方向性直方图 → 方向性分数
       ├─ 判断木纹/石纹 (directionality > 0.12 → 木纹)
       ├─ 分档：浅/中/深 × 暖/冷/中性/近白
       ├─ 匹配 FLOOR_TONES 中的最佳档位
       ├─ 计算置信度
       └─ 返回 (matched_tone, analysis_html)
              │
              ▼
       get_style_choices(matched_tone) ─── 按推荐度排序风格
```

---

*最后更新：2026-06-23 | 版本 v7（增量：参照模式图+文混合重做、在线翻译换 GoogleTranslator 走代理 + 预设选项全量进词典[含宠物品种]、翻译失败回退裸原文、候选 ‹n/N› 同卡累积、工作台左栏重排、结果图缩略图 /thumb/outputs + 磨缝校色内存减半 + 卡片/候选自动收口、路径越界二次校验、prompt 黄金回归测试）*
*更早（v6.0.1 起）：Fal 多供应商路由 + 自动备线、批量生成、逐张多抽、收藏⭐、PPTX 提案导出、记录文件化存储 + 旧记录迁移、圆弧倒角参考图换 clean_a*
