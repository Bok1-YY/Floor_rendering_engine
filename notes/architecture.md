# 架构解密 · Architecture

> 基于当前真实代码（`server_api.py` + 引擎模块 + `web/`）梳理的**模块调用依赖关系**。
> 回首页 → [[index]]｜为什么长成这样 → [[mental-model]]｜实操坑位 → [[pitfalls-and-conventions]]

---

## 一、三层总览

```
                 ┌──────────────────────────────────────────────┐
  浏览器  ─────▶ │  Next.js 前端 (web/, :3000)                   │  纯渲染(React)
                 │    └─ lib/api.ts ──HTTP / SSE──┐              │
                 └───────────────────────────────┼──────────────┘
                                                 ▼
                 ┌──────────────────────────────────────────────┐
                 │  FastAPI 无头后端 server_api.py (:7870) ★     │  作业队列 · SSE
                 │    40+ API 路由 · 进程内编排 · 静态/缩略图     │  唯一新增服务端源码
                 └───────────────────────────────┬──────────────┘
                                                 ▼  复用（零改动）
                 ┌──────────────────────────────────────────────┐
                 │  引擎模块（headless，纯 Python，不引 nicegui） │
                 │  config·models·prompt_data·prompts·recipes     │
                 │  ·api·records·failure_kb·themes·logging_setup  │
                 └──────────────────────────────────────────────┘
```

- **前端**只渲染交互，所有业务经 HTTP/SSE 调后端。
- **`server_api.py`** 是唯一新增的服务端源码，把引擎能力暴露为 REST + SSE，并持有全部**进程内编排状态**。
- **引擎模块** headless、可独立复用——这是能做[[mental-model|绞杀者迁移]]的前提。
- **启动入口**：`serve.py`（`main()` → uvicorn 单进程单 worker）；旧 `__main__.py` 已是[[mental-model|退役桩]]。

---

## 二、后端模块依赖图（有向：A → B 表示「A import B」）

```
  logging_setup   themes   models   failure_kb        ← 基础层（不依赖任何兄弟模块）
        │           │
        └─────┬─────┘
              ▼
           config                                     ← 配置基石（被 5 个模块依赖）
        ┌─────┼───────────────┐
        ▼     ▼               ▼
  prompt_data  records(→models)                       ← 数据/记录层
        │        │
        │        ├──────────────┐
        ▼        ▼              ▼
     recipes   api       prompts · sd_prompts          ← 业务逻辑层
   (惰性import  (→config,     (→config,
    prompt_data) records)     prompt_data, records)
        │        │              │
        └────────┴──────┬───────┘
                        ▼
                   server_api                          ← FastAPI 聚合顶点（import 8 个兄弟模块）
                        ▲
                        │
                     serve.py                          ← 启动入口（用绝对包名 import app）
```

**邻接表（精确）：**

| 模块 | import 的兄弟模块 |
|---|---|
| `logging_setup` | —（刻意独立算 `BASE_DIR`，**打破与 config 的潜在环**） |
| `themes` | — |
| `models` | —（纯数据 dataclass，被依赖但不依赖别人） |
| `failure_kb` | — |
| `config` | `logging_setup`, `themes` |
| `prompt_data` | `config` |
| `records` | `config`, `models` |
| `recipes` | `prompt_data`（**惰性**：函数体内 import，规避加载顺序） |
| `api` | `config`, `records` |
| `prompts` | `config`, `prompt_data`, `records` |
| `sd_prompts` | `models`, `prompt_data` |
| `server_api` | `config`, `api`, `prompts`, `sd_prompts`, `records`, `models`, `failure_kb`, `recipes`, `prompt_data` |
| `serve` | `server_api`（绝对包名，单一 exe 编译入口） |

> **分层清晰、无循环依赖。** `logging_setup` 注释明说独立计算 `BASE_DIR` 就是为打破与 `config` 的环；`recipes → prompt_data` 用惰性 import 规避加载顺序问题。

**核心度（被多少模块依赖）：**
- `config`（5）—— **最底层基石**：路径常量 / 配置读写 / 各 getter / `safe_upload_path` 等。
- `records`（3）、`prompt_data`（3）—— 记录持久化 / 提示词数据与翻译。
- `models`（2）—— 任务与候选数据模型。
- `api` / `prompts` / `recipes` / `failure_kb`（各 1）—— 仅被 `server_api` 消费。
- `server_api`（被 `serve` 依赖）—— 聚合顶点。

---

## 三、各模块职责一句话

| 模块 | 职责 | 关键导出 |
|---|---|---|
| `logging_setup` | 全局 logger（输出到上级 `app_local_save.log`） | `logger` |
| `themes` | 旧 UI 主题 CSS（webui 退役后无运行期消费者，待清理） | `THEMES`, `build_theme_css` |
| `models` | 纯数据：作业 / 参数 / 状态 / 候选导航 | `JobRecord`, `TaskParams`, `new_job`, `compute_final_status`, `add_candidate/nav_candidate/ensure_candidate_lists`, `task_params_to_kwargs` |
| `failure_kb` | 失败知识库：错误串 → {title, cause, action} | `classify_failure`, `FAILURE_RULES` |
| `config` | 路径/配置中心：`BASE_DIR`、engine_config.json 读写、key/proxy/provider/速度档/failover/TLS/Omakase | `BASE_DIR`, `_load_config/_save_config`, `get_*` getter 群, `safe_upload_path` |
| `prompt_data` | 海量选项表 + 识色 + 中英翻译 + 提示词层构建 | `translate_zh_to_en`, `analyze_floor_tone`, `build_overseas_realism_layer`, `STYLES/FLOOR_TONES/TECH_DICT/CN_*…` |
| `records` | 持久化：队列 persist/load、记录读写、收藏/删除、人工评审/最佳图、解密、用量、导出 HTML/PPTX | `persist_jobs/load_persisted_jobs`, `update_result_review`, `export_*`, `record_usage/load_usage_summary`, `record_file_lock` |
| `recipes` | 智能配方：按色调推荐 + 关键词→选项键 | `recommend_recipes`, `pick_option_key`, `FLOOR_RECIPES` |
| `api` | 模型调用：Google/Fal 生图、二改、参照分析、Omakase 文本主备路由、连通测试 | `call_image_generate`, `call_gemini_generate/call_fal_generate`, `call_gemini_edit`, `analyze_style_image`, `call_omakase_scenes`, `test_connection` |
| `prompts` | 提示词组装：35+ 参数 → 英文 prompt + 落 JSON/PNG（多工作流：地板、Omakase、墙板） | `save_task_files_html` |
| `sd_prompts` | SD 3.5 独立正/负提示词编译；与 Gemini 对抗式提示词隔离 | `compile_sd35_prompt` |
| `server_api` | FastAPI 无头层：端点 + 作业队列 + SSE + 静态/缩略图 + 进程内编排 | `app` |
| `serve` | 启动入口：uvicorn 单进程单 worker | `main()` |

---

## 四、关键调用链（端点 → 引擎）

### 1. 生成任务（主链路）
```
POST /api/jobs
  → task_params_to_kwargs (models)              # 组参
  → asyncio.to_thread(save_task_files_html)      # prompts：组装提示词 + 写 json
        ├─ translate_zh_to_en / extract_zh (prompt_data)
        ├─ TECH_DICT / PROPERTY_TYPE_DICT 查表 (prompt_data)
        ├─ build_overseas_realism_layer / build_cn_layout_guidance (prompt_data)
        ├─ _convert_to_srgb (prompt_data)         # 图像 sRGB 归一
        └─ _get_json_path → record_file_lock → _load/_save_records + _img_to_b64 (records)
  → call_image_generate (api)                     # 按 provider 路由
        └─ call_gemini_generate / call_fal_generate
  ↘ [选择 sd35] compile_sd35_prompt
        → FAL 持久队列 → SD3.5 + IP-Adapter → AuraSR
  → _save_api_result_jpg + _api_write_to_record (records)   # 出一张落一张
  → record_usage (records)
  → _persist_jobs → persist_jobs (records)
前端 GET /api/jobs/{id}/stream ◀─ SSE 每秒推 _job_view 快照
```

### 2. 其它主要链路
- **参照图风格**：建任务/预览前 `asyncio.to_thread(analyze_style_image)` (api)，结果并入提示词。
- **二改/磨缝**：`/api/jobs/{id}/edit`、`/polish` → `call_gemini_edit` (api) → 落盘；记录页 `/api/records/edit` → `append_edited_result_to_record` (records)。
- **快速预览**：`POST /api/preview` → `save_task_files_html(persist=False)` → 直连 `call_gemini_generate(LITE_PREVIEW_MODEL)`（**绕过 provider 路由**）。见 [[mental-model|Lite 预览通道]]。
- **识色→配方**：`GET /api/floor/analyze` → `analyze_floor_tone` (prompt_data) → `recommend_recipes` (recipes)。
- **Omakase 场景**：`POST /api/omakase/scenes` → `call_omakase_scenes` (api)，Gemini 主线路失败时自动转已配置的 DeepSeek；失败经 `classify_failure` (failure_kb)。
- **人工评审**：`POST /api/records/result/review` → `update_result_review` (records) → 原子写回记录 JSON；`best=True` 时同记录内其它结果自动取消最佳。
- **导出**：`/api/records/export/{html,pptx,favorites-pptx}` → `export_*` (records) → FileResponse。

---

## 五、进程内编排状态（都在 `server_api.py`，都靠单 worker 保证）

> ⚠️ 这些是**进程内**状态，多 worker 不共享 → **必须单 worker**（见 [[pitfalls-and-conventions]]）。

- `_job_history`（列表，新在前，`_MAX_RESIDENT_JOBS=60` 自动收口，只删最旧终态）
- `_model_semaphores`（b2/pro/sd35 按模型信号量，lifespan 里按配置建）—— 见[[mental-model|并发演进]]
- `_cancel_jobs`（单作业取消集合）/ `_cancel_generation`（全局取消计数器）
- `_bg_tasks` + `_spawn(coro)`（持后台 task 强引用，防被 GC）
- `_task_prep_lock`（同小样并发抢写 png 的串行锁）
- `_previews` 注册表（Lite 预览，与 4K 队列解耦）

**加端点范式**：改 `_job_history` 要持 `_job_lock`；`_persist_jobs()` **必须在锁外调**（内部会再取同一把锁，threading.Lock 不可重入，锁内调会死锁）。业务能复用引擎就复用。

---

## 六、前端结构（web/src）

```
web/src/
├── app/                        Next.js App Router 页面
│   ├── layout.tsx              RootLayout（引 Hanken Grotesk + <AppShell> + <Toaster>）
│   ├── globals.css             ★ 设计令牌（珊瑚陶土 #c15f3c + 奶油米色）—— 改主题改这里
│   ├── page.tsx                GeneratePage（左参数列 + 右任务队列）
│   ├── records/page.tsx        RecordsPage
│   ├── usage/page.tsx          UsagePage
│   └── settings/page.tsx       SettingsPage
├── components/
│   ├── AppShell.tsx            ★ 应用外壳：奶油侧边栏 + 顶栏 + 内容槽
│   ├── ParamsForm.tsx          参数表单（最大；按市场/工作流条件显隐）
│   ├── JobCard.tsx             任务卡（SSE 实时 / 候选 ‹n/N› / 停止·重试·磨缝·二改·重抽）
│   ├── FloorUploader.tsx       地板上传 + 最近小样网格
│   ├── ImageZoom.tsx           全屏无边框看图
│   ├── dc-ui.tsx               设计基元：SectionHeader / Segmented / Pill
│   └── ui/                     shadcn 基础组件（令牌驱动）
├── lib/
│   ├── api.ts                  ★ typed 客户端，base = NEXT_PUBLIC_API_BASE
│   ├── types.ts                全部 TS 类型（被 api.ts import）
│   ├── draft.ts                localStorage 草稿（见 mental-model 切页丢失）
│   └── notify.ts               完成通知（Notification + Web Audio）
└── hooks/useJobStream.ts       EventSource 封装（终态 close，防重连风暴）
```

**`lib/api.ts` 单一 `api` 对象**，方法覆盖：上传 / 任务全生命周期 / 预览 / 小样 / 记录 / 人工评审 / 导出 URL / 配置 / 选项 / 识色 / 用量 / 失败规则 / `imgUrl`。

**数据流**：页面 `useEffect` 调 `api.*` 取数；活动作业用 `useJobStream`(SSE) 看实时进度；生成页另有 2.5s 轮询 `listJobs` 做队列聚合兜底。

> ⚠️ Next 16 破坏性变更（`params` 变 Promise、`next/image` 拦本地 IP、build 不跑 ESLint）→ 见 [[pitfalls-and-conventions]]。

---

## 相关笔记
- [[index]] — 首页与 MOC 导航
- [[mental-model]] — 这套架构是怎么演进出来的
- [[pitfalls-and-conventions]] — 改这套架构前必读的坑
- [[bevel-saga]] — 提示词层最深的一次调试
