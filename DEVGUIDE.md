# Floor AI 生图引擎 · 开发手册（DEVGUIDE）

> 地板行业**效果图生成**引擎的商业版。上传地板小样 → 自动识色/智能配方 → 配参数 → 调 Gemini/Fal 出图（B2 快出 + Pro 精修，支持 4K）。
> 本仓库 `Floor_engine_server/` 是从原型 `test/floor_engine/` fork 出来的**商业主线**：把界面从 NiceGUI 迁到「FastAPI 无头后端 + Next.js 真前端」，引擎逻辑原样复用。
> 本手册按当前真实代码（2026-06）重写，**开头就是启动**。读完「零」即可跑起来；要改代码再往下看。

---

## 零、快速启动 ⭐（先看这里）

### 0.1 一键启动（推荐）
双击仓库**上一级** `test/` 目录下的一键启动脚本（脚本在 `test/`，不在本仓库内）：
- **Windows**：`test/一键启动.bat`
- **Linux / macOS**：`test/一键启动.sh`

它会：
1. 起**后端** FastAPI（端口 **7870**）—— 独立终端窗口；
2. 起**前端** Next.js dev（端口 **3000**）—— 独立终端窗口；
3. 等 8 秒让前端编译完，自动打开浏览器 `http://localhost:3000`。

关掉某个终端窗口 = 停掉对应服务。

### 0.2 手动启动（开发时常用）
路径以仓库上一级的 `test/` 目录为基准（下面 `<项目根>` 代表你本地放 `test/` 的位置）。
```bash
# 后端（务必在 test/ 目录下运行，单 worker）
cd  <项目根>/test
python -m Floor_engine_server.server_api          # → http://127.0.0.1:7870

# 前端（另开一个终端）
cd  <项目根>/test/Floor_engine_server/web
npm run dev                                        # → http://localhost:3000
```
浏览器开 **http://localhost:3000**。后端健康检查：`GET http://127.0.0.1:7870/api/healthz` → `{"ok":true}`。

### 0.3 首次准备（只做一次）
```bash
# 引擎/后端 Python 依赖（建议先建虚拟环境后再装）
pip install -r Floor_engine_server/requirements-dev.txt
# 前端 Node 依赖（需 Node 20.9+）
cd Floor_engine_server/web && npm install
```
然后在前端「**设置**」页填 Gemini API Key（或直接写 `test/engine_config.json`）。没有 key 也能开界面，但出图会失败。

### 0.4 端口与进程

| 进程 | 端口 | 启动命令 | 说明 |
|---|---|---|---|
| **前端** Next.js dev | **3000** | `npm run dev`（在 `web/`） | 浏览器入口，开发用 |
| **后端** FastAPI（无头） | **7870** | `python -m Floor_engine_server.server_api`（在 `test/`） | 主线 API + SSE + 静态图 |
| 旧版 NiceGUI（过渡 fallback） | 7869 | 见 §八 | 老界面，**不加新功能**，留作兜底 |

### 0.5 启动逻辑（一键启动脚本到底做了什么）
两个平台的脚本逻辑一致，只是语法不同（Windows 用 `.bat`，Linux/macOS 用 `.sh`）：
- 先切到脚本所在的 `test/`（后端必须在 `test/` 跑，原因见 §四：数据目录靠相对路径解析到这里）。Windows 用 `cd /d "%~dp0"`，Linux/macOS 用 `cd "$(dirname "$0")"`。
- 各起一个**独立终端窗口**分别跑后端 `python -m Floor_engine_server.server_api` 和前端 `npm run dev`（跑完不关，方便看日志）。
- 等前端首次编译（约 8 秒），再打开浏览器 `http://localhost:3000`。
- **为什么两个窗口**：前后端是两个独立进程（解耦，互不阻塞）；这正是新架构相对老 NiceGUI「一锅炖」的核心改进——后端忙着出 4K 图时前端照样丝滑。
- **（Windows 专属）为什么 .bat 必须纯 ASCII**：中文 Windows 的 cmd 用 GBK 解析 .bat，文件里有中文会字节错位、命令报错，故 .bat 保持纯英文 + CRLF 换行。`.sh` 无此限制（UTF-8 + LF）。

---

## 一、整体架构（绞杀者式迁移）

```
                 ┌─────────────────────────────────────────────┐
  浏览器  ──────▶│  Next.js 前端 (web/, :3000)                  │  纯前端渲染(React)
                 │   └─ api.ts ──HTTP/SSE──┐                    │
                 └─────────────────────────┼───────────────────┘
                                           ▼
                 ┌─────────────────────────────────────────────┐
                 │  FastAPI 无头后端 (server_api.py, :7870) ★    │  唯一新增的服务端源码
                 │   作业队列 / SSE 进度 / 静态图 / 缩略图        │
                 └─────────────────────────┬───────────────────┘
                                           ▼ 复用（零改动）
                 ┌─────────────────────────────────────────────┐
                 │  引擎模块（headless，新旧版共用）             │
                 │  config·models·prompt_data·prompts·recipes   │
                 │  ·api·records·failure_kb·themes·logging_setup │
                 └─────────────────────────────────────────────┘
                                           ▲ 同样复用
                 ┌─────────────────────────────────────────────┐
                 │  webui.py (NiceGUI, :7869) ❌ 2026-06-29 退役 │
                 └─────────────────────────────────────────────┘
```

三条铁律：
1. **引擎零改**：所有重逻辑（提示词组装、模型调用、记录持久化、识色、配方、导出…）都在引擎模块里，新旧 UI 都只是「调引擎」。改业务逻辑优先落引擎模块（新仓库这份）。
2. **新功能只往 `server_api.py` + `web/` 加**。旧 `webui.py` 已于 2026-06-29 退役删除，不再有第二套 UI 需要维护。
3. **长请求改异步作业**：出 4K 图很慢、还会被公司软路由 reset 长连接。新设计是 `POST /api/jobs` **秒回 `job_id`** → `GET /api/jobs/{id}/stream`（SSE）看进度。触发请求立刻返回，根治 reset。

**迁移进度**：STEP1（FastAPI 无头层）/ STEP2（Next.js 前端）/ STEP2.5（webui 功能全量 parity）/ 视觉重设计（Claude Design 整站换皮 + 侧栏外壳）**均已完成**。**STEP3（退役 webui + 去 nicegui 依赖）已于 2026-06-29 执行**：删除 `webui.py`、`requirements.txt` 去掉 `nicegui`、`__main__.py` 改为退役提示桩。日常兜底改由冻结原型 `test/floor_engine/` 承担（同源、共享数据，见 §八）。

---

## 二、目录与模块地图

```
Floor_engine_server/
├── server_api.py        ★ 新增：无头 FastAPI 层（端点 + 作业队列 + SSE + 静态/缩略图）
├── __main__.py          `python -m Floor_engine_server` → 退役提示桩（旧 webui 入口已退役；打印新启动方式后 exit 1）
├── __init__.py          包说明 / 公共导出指引
│
│   ── 引擎模块（全部 headless，import 不会拉起 nicegui，新旧共用）──
├── config.py            路径/配置中心：BASE_DIR、目录常量、engine_config.json 读写、key/proxy/provider/速度档/failover/TLS/Omakase
├── models.py            纯数据：JobRecord(作业)、TaskParams(参数)、compute_final_status、候选图导航(add/nav_candidate)
├── prompt_data.py       海量选项表（STYLES/LIGHTINGS/ANGLES/FLOOR_TONES/ROOM_TYPES/CN_*…）+ 识色 analyze_floor_tone + 中英翻译
├── prompts.py           提示词组装：save_task_files_html(35+ 参数 → 英文 prompt + 落 JSON/PNG)，4 种工作流各一个 builder
├── recipes.py           智能配方：recommend_recipes(按色调推荐) + pick_option_key(关键词→具体选项)
├── api.py               模型调用：Google/Fal 生图、Gemini→DeepSeek Omakase 主备路由、磨缝二改、连通性自检
├── records.py           持久化：队列 persist/load、记录 JSON 读写、收藏/删除/人工评审、解密 reveal、用量 load_usage_summary、导出 HTML/PPTX
├── failure_kb.py        失败知识库：FAILURE_RULES + classify_failure(错误串→{title,cause,action})
├── themes.py            旧 UI 主题 CSS 生成（曾供 NiceGUI；webui 退役后已无运行期消费者，留待后续清理）
├── logging_setup.py     logger（输出到 test/app_local_save.log）
│
├── web/                 ★ Next.js 前端（见 §六）
├── tests/               pytest：golden 提示词回归 + 安全硬化（路径逃逸/TLS/failover）
├── assets/              bevel_ref*.jpg（倒角参考图）、logo.svg —— 入库
├── requirements.txt     Python 依赖
├── 开发日志.md          每次会话改了啥、为什么（最新在最上，接手前先读）
├── README.md / DEVGUIDE.md
└── .gitignore           忽略 __pycache__/*.log/*.bak/_ng_thumbs + 运行期产物(output_files/engine_config.json/.queue_state.json…)
```

**谁 import nicegui**：webui 退役后**已无任何业务文件 import nicegui**，整包纯 headless。改引擎时维持这一点（可用 `python -c "import sys,Floor_engine_server.server_api; print('nicegui' in sys.modules)"` 自检，应为 False）。

---

## 三、数据与配置（重要，别踩）

`config.py` 里：`BASE_DIR = dirname(dirname(__file__))`。本仓库在 `test/` 下时，`BASE_DIR` 解析到 **`test/`**，于是所有运行期数据落在 `test/` 下、**与原型 `test/floor_engine/` 共享**：

| 路径（相对 `test/`） | 内容 |
|---|---|
| `output_files/` | 出图、每个素材的 `*_记录.json`、优化图、磨缝候选 |
| `output_files/.queue_state.json` | 任务队列持久化（最多 60 条，重启恢复） |
| `engine_config.json` | **密钥**(gemini/fal)、proxy、provider、speed_profile、auto_failover、tls_verify/ca、并发等。**含敏感信息，已 gitignore，且在 `test/` 不在本仓库** |
| `_ng_uploads/` | 上传的小样/参照图 |
| `_ng_thumbs/` | 懒生成缩略图缓存（可随时重建，gitignore） |
| `app_local_save.log` | 运行日志（gitignore） |

- **新旧版共享同一份数据**：旧 NiceGUI(7869) 和新后端(7870) 同时跑也互不冲突，看到的是同一批历史与配置。
- **将来迁出 `test/`**：把本仓库挪到别处，`BASE_DIR` 自动指向新位置 → 自动获得**独立**数据目录，无需改一行代码。`.gitignore` 已提前忽略这些运行期产物，防止迁出后误入库。
- engine_config.json 关键字段：`gemini_api_key`/`proxy`/`fal_api_key`/`image_provider`(google|fal)/`speed_profile`(fast|resilient)/`auto_failover`/`tls_verify`/`tls_ca_bundle`/`max_concurrent_per_model`。前端「设置」页就是读写这些（经 `GET/PUT /api/config`，返回时密钥脱敏）。

---

## 四、后端：`server_api.py`

### 4.1 运行约束
- **必须单 worker**：`_job_history`、并发信号量都是**进程内**状态，多 worker 不共享。直接 `uvicorn.run(app, ...)`（不传 workers）即单进程。
- 端口可用 `FLOOR_API_PORT` 覆盖；host 仅支持本机 `127.0.0.1`，本版本不提供远程认证。
- **CORS**：`FLOOR_API_CORS` 默认放行 `http://localhost:3000,http://127.0.0.1:3000`。**换前端端口必须改这个环境变量**，否则浏览器跨域被拦（后端能 200，但 JS fetch 拿不到）。

### 4.2 作业生命周期
```
POST /api/jobs ──秒回 job_id──▶ 后台 asyncio task(_run_job_bg)
                                  │  _task_prep_lock 内 save_task_files_html 组装提示词
                                  │  _b2_semaphore / _pro_semaphore 限并发，分别调 call_image_generate
                                  │  出一张 _api_write_to_record 落盘一张；stage 文本实时更新
前端 GET /api/jobs/{id}/stream ◀─ SSE 每秒推 _job_view 快照，进终态推 done 事件并关闭
```
进程内状态（都在 `_job_lock` 下读写）：`_job_history`(列表，新在前)、`_b2_semaphore`/`_pro_semaphore`(并发，lifespan 里按配置建)、`_cancel_jobs`(单作业取消集合)、`_cancel_generation`(全局取消计数器)。终态：`done`/`partial`/`failed`；`compute_final_status` 据 model_filter + 出图情况判定。

### 4.3 端点目录（40+ API 路由）
- **作业** `/api/jobs`：`POST`建 · `GET`列 · `GET {id}` · `GET {id}/stream`(SSE) · `POST {id}/cancel` · `POST cancel-all` · `POST clear-completed`(清完成) · `POST {id}/delete`(删单条) · `POST {id}/retry` · `GET {id}/result?model=&idx=`(候选切换) · `POST {id}/polish`(磨缝) · `POST {id}/edit`(二改) · `POST {id}/regen?n=`(重抽/多抽)。
- **记录** `/api/records`：`GET`列文件 · `GET load` · `POST reveal`(解密) · `POST edit`(记录内二改) · `POST result/delete` · `POST result/favorite` · `POST result/review`(人工评审：通过/备选/淘汰、标签、备注、最佳图) · `POST delete`(删整条) · `GET export/{html,pptx,favorites-pptx}`(FileResponse 下载)。
- **上传** `POST /api/uploads/{floor,room,ref}`；**小样** `GET /api/swatches/recent`；**配方** `GET /api/recipes`；**识色** `GET /api/floor/analyze`。
- **失败** `POST /api/failure/classify` · `GET /api/failure/rules`；**连通** `GET /api/connection/test`。
- **配置** `GET/PUT /api/config`；**模型** `GET /api/models`；**选项** `GET /api/options`(前端下拉真源)；**用量** `GET /api/usage`；**健康** `GET /api/healthz`。
- **静态/缩略图**：`GET /thumb/{uploads,outputs}`(懒生成 JPEG)；`/outputs`、`/uploads` 挂目录服原图。

### 4.4 加新端点的范式
仿 `cancel_all` / `clear_completed`：改 `_job_history` 要持 `_job_lock`；**`_persist_jobs()` 必须在锁外调**（它内部会再取同一把锁，threading.Lock 不可重入，锁内调会死锁）。业务逻辑能复用引擎就复用（导出→`records.export_*`、识色→`prompt_data.analyze_floor_tone`、配方→`recipes.*`）。

---

## 五、前端：`web/`

### 5.1 技术栈
Next.js **16.2.9**（App Router + Turbopack）· React **19.2** · Tailwind **v4** · shadcn/ui（基于 `@base-ui/react`）· sonner（toast）· lucide（图标）。脚本：`npm run dev`(3000) / `npm run build` / `npm run start`。

### 5.2 结构
```
web/src/
├── app/
│   ├── layout.tsx        根布局：引 Hanken Grotesk 字体 + 全高 body + <AppShell> 包壳 + <Toaster>
│   ├── globals.css       ★ 设计令牌(珊瑚陶土 #c15f3c + 奶油米色) + 滚动条 + keyframes —— 改主题改这里
│   ├── page.tsx          生成页（左 600px 参数列 + 右任务队列，两栏全高）
│   ├── records/page.tsx  记录页（左 280px 文件列表 + 右记录卡；房间/评审筛选、收藏、最佳图、人工标注）
│   ├── usage/page.tsx    用量页（stat 卡 + 成功率条 + 明细表）
│   └── settings/page.tsx 设置页（密钥卡 + 线路/网络卡）
├── components/
│   ├── AppShell.tsx      ★ 应用外壳：奶油侧边栏(236) + 顶栏(56,路由映射标题) + 内容槽
│   ├── ParamsForm.tsx    参数表单（工作流 2×2 卡 / 市场·模型 segmented / 字段 Select / 胶囊 / 高级折叠）
│   ├── JobCard.tsx       任务卡（SSE 实时 / 状态徽章 / 候选‹n/N› / 停止·重试·磨缝·二改·重抽·清除）
│   ├── FloorUploader.tsx 地板上传 + 最近小样网格 + 历史弹窗
│   ├── ImageZoom.tsx     全屏无边框看图（滚轮缩放/拖动/双击复位/Esc）
│   ├── dc-ui.tsx         设计基元：SectionHeader(点头标)·Segmented(分段)·Pill(胶囊)
│   └── ui/               shadcn 基础组件（button/select/dialog/switch/input…，令牌驱动）
├── lib/
│   ├── api.ts            ★ typed 客户端，开发指向 7870、生产走同源
│   ├── types.ts          JobView/GenParams/OptionsView/ConfigView/UsageSummary/RecordEntry… 类型
│   └── notify.ts         完成通知（浏览器 Notification + Web Audio 提示音）
└── hooks/useJobStream.ts EventSource 封装（终态 close，防自动重连风暴）
```

### 5.3 设计系统
全站颜色/圆角/卡片底色等是 **Tailwind v4 设计令牌**，集中在 `globals.css` 的 `:root`（`--primary #c15f3c`、`--background #f7f3ec`、`--card #fffdf9`、`--panel #faf7f0`、`--border #e3ded1`…）。shadcn 组件全部引用这些令牌——**改一处令牌即整站换肤**，不用动组件。重复的视觉模式抽进了 `dc-ui.tsx`。

### 5.4 数据流
页面 `useEffect` 调 `api.*` 取数；活动作业用 `useJobStream`(EventSource) 看 SSE 实时进度；生成页另有 2.5s 轮询 `listJobs` 做队列聚合进度。`api.imgUrl()` 把后端相对图 URL 拼成绝对地址。

---

## 六、关键约定 & 坑（务必读）

1. **Next 16 ≠ 你训练里的 Next**（`web/AGENTS.md` 明确警告；改前端前先翻 `web/node_modules/next/dist/docs/`）：
   - `params`/`searchParams` 变成 **Promise** → 本项目**所有页面用 `'use client'` + `useEffect` 取数**绕开。
   - `next/image` 默认**拦本地 IP**（127.0.0.1:7870/outputs）→ 图片一律用普通 `<img>`（已加 eslint-disable）。
   - `next build` **不再跑 ESLint**，只有 `tsc` 类型错会拦构建。
2. **后端单 worker**（同 §4.1）。
3. **CORS 只放 3000**（同 §4.1）；本地用别的端口调试要设 `FLOOR_API_CORS`。
4. **（Windows 专属）`.bat` 必须纯 ASCII + CRLF**（同 §0.5；`.sh` 无此限制）。
5. **环境文件**：`.env.development` 默认指向后端 7870，`.env.production` 留空以使用同源 API。
6. **无头截图自检法**（验证视觉时用；实测）：dev 服务器在无头浏览器里因 HMR 握手失败**不 hydrate**，截不到数据态；要截带数据的页面需 **prod build + 隔离后端(改 `FLOOR_API_CORS` 放行测试端口) + Node（v22+，内置 WebSocket）走 CDP 真等几秒再 captureScreenshot**。隔离后端与正式实例共享 `.queue_state.json`，测试时**只读、别触发清除/删除**，免得误删真实任务。
7. **设计稿落地**：照 mockup 的**实际视觉**还原（配色/胶囊/卡片/分段），别只搬报告文字；落地后无头截图比对设计稿。
8. **`webui.py` 已退役删除**：UI 只有 `web/` 一套，新功能只加 `server_api.py` + `web/`。

---

## 七、开发工作流

**加一个功能**（典型全链路）：
1. （如需）引擎逻辑落 `records.py`/`prompts.py`/`api.py` 等（headless，别引 nicegui）。
2. `server_api.py` 加端点（复用引擎；改队列持锁、`_persist_jobs` 在锁外）。
3. `web/src/lib/api.ts` 加封装 + `types.ts` 加类型。
4. 在对应页面/组件接 UI（沿用 `dc-ui` 基元与 `globals.css` 令牌）。
5. **验证**：`cd web && npx tsc --noEmit`(0 错) + `npm run build`(过)；后端 `python -c "import Floor_engine_server.server_api"` 不报错、不拉 nicegui；必要时无头截图比对（§六.6）。
6. 在 **`开发日志.md` 顶部追加一条**（改了啥、为什么）。
7. 提交（见 §九）。

**测试**：`cd Floor_engine_server && python -m pytest`（引擎层 golden 提示词回归 + 安全硬化 + 人工评审元数据回归；当前 43 项）。本机若系统 `python` 无 pytest，可用项目虚拟环境：`.venv/bin/python -m pytest`。`tests/golden/` 基准入库，缓存不入。

---

## 八、旧版 NiceGUI（已退役）

- **`webui.py` 已于 2026-06-29 删除**（STEP3）。`python -m Floor_engine_server` 不再起界面，只打印退役提示并 `exit 1`（见 `__main__.py`）；`requirements.txt` 也去掉了 `nicegui`，整包不再依赖 NiceGUI。
- **日常兜底改由冻结原型 `test/floor_engine/` 承担**（同源、共享数据，仍是完整可跑的 NiceGUI 版）。它**只为兜底、不再加新功能**；本仓库 `Floor_engine_server/` 已是唯一在维护的代码。
- 退役理由与时点见 `开发日志.md`（2026-06-29 条）。如需回滚，`git revert` 本次退役提交即可恢复 `webui.py` 与 `nicegui` 依赖。

---

## 九、构建 / 打包 / Git

- **前端生产模式**：`cd web && npm run build && npm run start`（dev 模式未优化，prod 更快）。
- **打包 exe**（Windows 专属）：`test/build.bat`（由用户自己跑；Claude 只改打包文件 + 给命令，不替跑构建）。
- **Git**：本仓库是独立 git 仓（分支 `main`，提交后 push 到远程 `origin`）。运行期产物与密钥已被 `.gitignore` + `web/.gitignore`（node_modules/.next）排除；`engine_config.json` 在 `test/` 不入库。提交信息沿用 `feat/fix/docs(scope): 说明` 风格，并保持「改完追加 `开发日志.md`」的习惯。

---

## 十、一句话回顾

**改业务** → 引擎模块（headless，新仓库这份）；**改接口** → `server_api.py`；**改界面** → `web/`；**换主题** → `web/.../globals.css`。旧 `webui.py` 已退役，UI 只有 `web/` 一套。启动就一句：跑 `test/` 下的一键启动脚本（Windows `.bat` / Linux·macOS `.sh`）。
