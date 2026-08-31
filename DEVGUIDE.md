# Floor AI 生图引擎 · 开发手册（DEVGUIDE）

> 地板行业**效果图生成**引擎的商业版。上传地板小样 → 自动识色/智能配方 → 配参数 → 调 Gemini/Fal 出图（B2 + Pro + 可选 SD 3.5，支持 4K）。
> 本仓库 `Floor_engine_server/` 是从原型 `test/floor_engine/` fork 出来的**商业主线**：把界面从 NiceGUI 迁到「FastAPI 无头后端 + Next.js 真前端」，引擎逻辑原样复用。
> 本手册按当前真实代码（2026-08）维护，**开头就是启动**。读完「零」即可跑起来；要改代码再往下看。

面向招聘与产品评审的阅读入口：[中文产品案例](./docs/PRODUCT_CASE_STUDY.zh-CN.md) / [English case study](./docs/PRODUCT_CASE_STUDY.en.md)。README 负责说明用户价值与业务结果，本手册聚焦实现边界、数据流和开发约定。

只做新旧样品照片对色时，不必启动完整服务：`standalone_color_calibrator/` 是从现有 LAB 校色核心抽出的独立 Pillow / NumPy / OpenCV 桌面与命令行工具，入口和参数见其 [README](./standalone_color_calibrator/README.md)。

---

## 零、快速启动 ⭐（先看这里）

### 0.1 一键启动（推荐）
Windows 直接双击仓库内脚本：
- `start-windows.bat`：生产静态前端与 FastAPI 合一运行在 **7870**；
- `dev-windows.bat`：FastAPI **7870** + Next.js dev **3000** 两个进程。

Linux / macOS 仍可使用原有上级目录启动脚本，或按 0.2 的命令手动启动。

Windows 开发脚本会：
1. 起**后端** FastAPI（端口 **7870**）—— 独立终端窗口；
2. 起**前端** Next.js dev（端口 **3000**）—— 独立终端窗口；
3. 等 8 秒让前端编译完，自动打开浏览器 `http://localhost:3000`。

关掉某个终端窗口 = 停掉对应服务。Windows 一体运行只保留一个后端窗口，前端由 FastAPI 静态托管。

### 0.2 手动启动（开发时常用）
当前 runnable 可直接从仓库根目录启动；不要求外层目录名必须是 `test/`。
```bash
# 一体化生产前端 + 后端（单 worker）
cd <runnable目录>
python serve.py                                    # → http://127.0.0.1:7870

# 前端（另开一个终端）
cd <runnable目录>/web
npm run dev                                        # → http://localhost:3000
```
浏览器开 **http://localhost:3000**。后端健康检查：`GET http://127.0.0.1:7870/api/healthz` → `{"ok":true}`。

### 0.3 首次准备（只做一次）
Windows 新电脑直接运行桌面的 `Install_Project_Dependencies.bat`，它会创建 `.venv`、安装 Python/Node
依赖并构建前端。手动方式如下：

```bash
# 引擎/后端 Python 依赖（建议先建虚拟环境后再装）
pip install -r Floor_engine_server/requirements-dev.txt
# 前端 Node 依赖（需 Node 20.9+）
cd Floor_engine_server/web && npm install
```
然后在前端「**设置**」页填写 Gemini/Fal API Key。密钥进入当前用户的系统密钥环，不写入 `engine_config.json`；没有 Key 仍可打开界面和使用本地功能，但外部生图与 Gemini 审查不可用。

独立样品对色不需要上述前后端依赖或 API Key。Windows 双击 `standalone_color_calibrator/启动校色工具.bat`；独立复制该目录后只需执行 `pip install -r requirements.txt`。

### 0.4 端口与进程

| 进程 | 端口 | 启动命令 | 说明 |
|---|---|---|---|
| **前端** Next.js dev | **3000** | `npm run dev`（在 `web/`） | 浏览器入口，开发用 |
| **后端** FastAPI（无头） | **7870** | `python serve.py`（在 runnable 根目录） | 主线 API + SSE + 静态图 |
| 旧版 NiceGUI（过渡 fallback） | 7869 | 见 §八 | 老界面，**不加新功能**，留作兜底 |

### 0.5 启动逻辑（一键启动脚本到底做了什么）
生产与开发脚本职责不同：
- `start-windows.bat` 先切到仓库根目录、检查 `.venv`，再比较 `web/src`、前端配置文件与 `web/out/index.html` 的修改时间。只有静态产物缺失或过期时才运行 `npm run build`；`node_modules` 缺失时才运行 `npm ci`，避免源码已经更新却继续启动旧界面。
- 生产启动把 `FLOOR_DATA_DIR` 固定到仓库内 `data/`，然后执行 `python serve.py`。FastAPI 与 `web/out` 使用同一个 **7870** 端口，`serve.py` 默认自动打开浏览器；设 `FLOOR_NO_BROWSER=1` 可禁用。
- `dev-windows.bat` 才使用两个独立进程：FastAPI **7870** + Next.js dev **3000**。前后端解耦，后端生成 4K 图时不会阻塞前端 HMR。
- Linux / macOS 当前使用手动命令：先 `npm run build`，再从仓库根目录运行 `python serve.py`。
- **（Windows 专属）为什么 .bat 必须纯 ASCII**：中文 Windows 的 cmd 用 GBK 解析 `.bat`，文件里有中文可能字节错位，因此脚本保持英文命令；Git 检出时允许转换为 CRLF。

---

## 一、整体架构（绞杀者式迁移）

```
                 ┌─────────────────────────────────────────────┐
  浏览器  ──────▶│  Next.js 前端 (web/, :3000)                  │  纯前端渲染(React)
                 │   └─ api.ts ──HTTP/SSE──┐                    │
                 └─────────────────────────┼───────────────────┘
                                           ▼
                 ┌─────────────────────────────────────────────┐
                 │  FastAPI 无头后端 (:7870)                     │
                 │   server_api.py＝组装器；业务在 routes_* ×6   │
                 │   状态 server_state · 工具 server_helpers     │
                 │   请求模型 server_schemas · 图像 image_ops    │
                 └─────────────────────────┬───────────────────┘
                                           ▼ 复用（零改动）
                 ┌─────────────────────────────────────────────┐
                 │  引擎模块（headless）                         │
                 │  config·models·task_registry·prompt_data     │
                 │  ·prompts·image_prep·sd_prompts·recipes      │
                 │  ·api·color_match·records·usage_stats        │
                 │  ·exports·reveal_security·custom_recipes     │
                 │  ·failure_kb·floor_renderer                  │
                 └─────────────────────────────────────────────┘
                                           ▲ 同样复用
                 ┌─────────────────────────────────────────────┐
                 │  webui.py (NiceGUI, :7869) ❌ 2026-06-29 退役 │
                 └─────────────────────────────────────────────┘
```

三条铁律：
1. **引擎保持 headless**：所有重逻辑（提示词组装、模型调用、记录持久化、识色、配方、校色、导出…）都在无 UI 的引擎模块里。改业务逻辑优先落引擎模块（新仓库这份）。
2. **新功能只往后端模块群（`routes_*` 等，见 §4.4）+ `web/` 加**。旧 `webui.py` 已于 2026-06-29 退役删除，不再有第二套 UI 需要维护。
3. **长请求改异步作业**：出 4K 图很慢、还会被公司软路由 reset 长连接。新设计是 `POST /api/jobs` **秒回 `job_id`** → `GET /api/jobs/{id}/stream`（SSE）看进度。触发请求立刻返回，根治 reset。

**迁移进度**：STEP1（FastAPI 无头层）/ STEP2（Next.js 前端）/ STEP2.5（webui 功能全量 parity）/ 视觉重设计（Claude Design 整站换皮 + 侧栏外壳）**均已完成**。**STEP3（退役 webui + 去 nicegui 依赖）已于 2026-06-29 执行**：删除 `webui.py`、`requirements.txt` 去掉 `nicegui`、`__main__.py` 改为退役提示桩。日常兜底改由冻结原型 `test/floor_engine/` 承担（同源、共享数据，见 §八）。

---

## 二、目录与模块地图

```
Floor_engine_server/
│   ── Web 层（2026-07 拆包：server_api 只组装，业务在 routes_*）──
├── server_api.py        FastAPI app 组装器：lifespan、CORS/同源守卫、healthz、include_router ×6、静态/缩略图、前端挂载
├── routes_jobs.py       任务队列端点 + 全部生图后台协程（4K 主编排 _run_job_bg/_edit_bg 在此，改动务必人工冒烟）
├── routes_previews.py   快速预览（NB2 Lite 1K，不进队列不写记录）
├── routes_library.py    上传/历史小样、记录列表与揭示、收藏/评审、导出、用量
├── routes_config.py     配方/失败库/连通性/配置/Omakase/模型与选项词表
├── routes_tools.py      识色 + 地板可视化渲染 + AI 蒙版/局部与兼容全图校色
├── routes_inpaint.py    生成式修补（AI 智能选区 + 两段式抽卡）
├── server_state.py      全部可变运行时状态：JOBS/PREVIEWS/INPAINTS 注册表、按模型信号量、spawn；路由经 `state.X` 访问（测试注入唯一入口）
├── server_schemas.py    31 个 Pydantic 请求模型（前端契约，改字段先确认前端）
├── server_helpers.py    路由共享工具：URL 映射、job_view、路径守卫(require_*)、上传落盘、导出响应
├── image_ops.py         纯 PIL：mask 解码/羽化、EXIF 归一化、修补候选落盘
├── task_registry.py     泛型 TaskRegistry：dict+锁+取消集合+trim 四件套的统一容器（jobs/previews/inpaints 共用）
├── __main__.py          `python -m Floor_engine_server` → 退役提示桩（旧 webui 入口已退役；打印新启动方式后 exit 1）
├── __init__.py          包说明 / 公共导出指引
│
│   ── 引擎模块（全部 headless，import 不会拉起任何 UI 框架）──
├── config.py            路径/配置中心：BASE_DIR、目录常量、engine_config.json 读写、key/proxy/provider/速度档/failover/TLS/Omakase
├── models.py            纯数据：JobRecord(作业)、TaskParams(参数)、compute_final_status、候选图导航(add/nav_candidate)
├── prompt_data.py       纯数据层：海量选项表（STYLES/LIGHTINGS/ANGLES/FLOOR_TONES/ROOM_TYPES/CN_*/工作流词表…）+ 中英翻译
├── prompts.py           提示词组装：save_task_files_html(兼容签名) → 五阶段流水线（翻译推导→材质指令→四模式组装→Pro 派生→落盘）
├── image_prep.py        提示词管线图像预处理：小样 ICC→sRGB 落盘 + 识色 analyze_floor_tone
├── sd_prompts.py        SD 3.5 专属正/负提示词编译器；只读 TaskParams，不读取/改写 Gemini prompt
├── recipes.py           智能配方：recommend_recipes(按色调推荐) + pick_option_key(关键词→具体选项)
├── custom_recipes.py    “我的配方”：运行期 JSON 存储及增删改查
├── api.py               外部模型客户端：Google/Fal 生图、FAL 持久队列、SD3.5 IP-Adapter、AuraSR、磨缝二改、生成式修补调度
├── color_match.py       本地色彩算法：全图 Reinhard、区域诊断、蒙版内稳健 a/b 校正及 4K 分片变体
├── floor_segmentation.py 离线 MobileSAM ONNX、地板正负点提示、通用物件扫描、单点区域提示、RLE 与严格手绘降级
├── records.py           记录持久化核心：队列状态、记录 CRUD、结果图落盘、收藏/评审、复盘聚合、迁移/揭示
├── usage_stats.py       用量统计：模式×模型×线路计数 + 成本估算（全程吞异常，绝不拖垮生图）
├── exports.py           导出层：记录 HTML 对照文档、客户提案 PPTX（单记录/收藏夹）
├── reveal_security.py   提示词混淆(XOR，非加密) + 揭示密码校验；records 单向依赖它
├── failure_kb.py        失败知识库：FAILURE_RULES + classify_failure(错误串→{title,cause,action})
├── floor_renderer.py    本地地板透视渲染引擎（OpenCV，供 /api/floor-visualize）
├── themes.py            旧 UI 主题 CSS 生成（曾供 NiceGUI；webui 退役后已无运行期消费者，留待后续清理）
├── logging_setup.py     logger（输出到 test/app_local_save.log）
│
├── web/                 ★ Next.js 前端（见 §五）
├── tests/               pytest：golden 提示词、69 端点路由契约快照、AI 智能选区、TaskRegistry/用量直测、安全硬化、校色回归
├── assets/              倒角参考图、logo、MobileSAM ONNX 与模型许可证 —— 入库
├── requirements.txt     Python 依赖
├── 开发日志.md          每次会话改了啥、为什么（最新在最上，接手前先读）
├── README.md / DEVGUIDE.md
└── .gitignore           忽略 __pycache__/*.log/*.bak/_ng_thumbs + 运行期产物(output_files/engine_config.json/.queue_state.json…)
```

**谁 import nicegui**：webui 退役后**已无任何业务文件 import nicegui**，整包纯 headless。改引擎时维持这一点（可用 `python -c "import sys,Floor_engine_server.server_api; print('nicegui' in sys.modules)"` 自检，应为 False）。

---

## 三、数据与配置（重要，别踩）

`runtime_paths.py` 是唯一数据根解析器：源码运行默认 `BASE_DIR=<项目>/data`，`FLOOR_DATA_DIR` 可显式覆盖；Nuitka onefile 使用用户双击的 exe 所在目录。

| 路径（相对 `BASE_DIR`） | 内容 |
|---|---|
| `output_files/` | 出图、每个素材的 `*_记录.json`、优化图、磨缝候选；源码运行时位于 `data/` 下 |
| `output_files/_samples/` | 400px 记录小样，按完整 SHA-256 内容寻址，跨记录复用 |
| `output_files/.queue_state.json` | 任务队列持久化（最多 60 条，重启恢复） |
| `engine_config.json` | proxy、provider、speed_profile、auto_failover、tls_verify/ca、并发等非敏感配置；API Key 不再落 JSON |
| `custom_recipes.json` | 用户保存的“我的配方”（位于仓库上级，不进入本仓库） |
| `_ng_uploads/logo_*` | PPTX 提案使用的品牌 Logo（由程序上传和清理） |
| `_ng_uploads/` | 上传的小样/参照图 |
| `_ng_thumbs/` | 懒生成缩略图缓存（可随时重建，gitignore） |
| `storage_backups/` | 存储清理前的记录备份、旧→新引用映射与清理 manifest |
| `storage_quarantine/` | 无引用文件的30天可恢复隔离区；到期仍需人工确认才永久删除 |
| `app_local_save.log` | 运行日志（gitignore） |

- **API Key**：优先环境变量，其次系统 keyring（Windows Credential Locker/macOS Keychain/Linux Secret Service）；安全 backend 不可用时禁止新写明文。
- **迁移项目**：移动源码目录会连同 `data/` 移动非敏感数据；系统 keyring 绑定当前用户/机器，换电脑后必须重新填 Key。
- engine_config.json 关键字段：`gemini_api_key`/`proxy`/`fal_api_key`/`fal_queue_proxy`（SD/AuraSR 专用，默认空=忽略系统代理直连）/`image_provider`(google|fal)/`sd_enabled`（SD 实验线路，默认关）/`speed_profile`(fast|resilient)/`auto_failover`/`tls_verify`/`tls_ca_bundle`/`max_concurrent_per_model`，生成式修补组 `inpaint_provider`(fal|comfyui)/`inpaint_remove_model`/`inpaint_add_model`/`comfyui_base_url`/`comfyui_workflow_path`/`comfyui_timeout`/`inpaint_remove_prompt`，以及成本估算 `usage_prices`、PPTX 品牌字段 `pptx_company`/`pptx_contact`。前端「设置」页读写这些（经 `GET/PUT /api/config`，返回时密钥脱敏）；品牌 Logo 走独立上传端点。

---

## 四、后端：`server_api.py` 组装的模块群

### 4.1 运行约束
- **必须单 worker**：`server_state` 里的 JOBS 注册表、并发信号量都是**进程内**状态，多 worker 不共享。直接 `uvicorn.run(app, ...)`（不传 workers）即单进程。
- 端口可用 `FLOOR_API_PORT` 覆盖；host 仅支持本机 `127.0.0.1`，本版本不提供远程认证。
- **CORS**：`FLOOR_API_CORS` 默认放行 `http://localhost:3000,http://127.0.0.1:3000`。**换前端端口必须改这个环境变量**，否则浏览器跨域被拦（后端能 200，但 JS fetch 拿不到）。

### 4.2 作业生命周期
```
POST /api/jobs ──秒回 job_id──▶ 后台 asyncio task(routes_jobs._run_job_bg)
                                  │  state.task_prep_lock 内 save_task_files_html 组装提示词
                                  │  state.model_semaphores 按 b2/pro/sd35 分模型限并发
                                  │  B2/Pro → call_image_generate；SD → FAL queue + IP-Adapter + AuraSR
                                  │  出一张 api_write_to_record 落盘一张；stage 文本实时更新
前端 GET /api/jobs/{id}/stream ◀─ SSE 每秒推 job_view 快照，进终态推 done 事件并关闭
```
自由创作走独立 `POST /api/jobs/free`：`prompt` 原样透传，`image_paths` 保持 Slot 1–3 顺序，
且只允许 B2 / Pro。它复用同一任务注册表、SSE、取消、重试和重抽，但不进入 `prompts.py` 的地板提示词管线。

进程内状态全部在 `server_state.py`：`JOBS`/`PREVIEWS`/`INPAINTS` 三个 `TaskRegistry` 实例（成员管理内部加锁；单任务取消集合 + 全局取消代次是 JOBS 的方法）、`model_semaphores`(b2/pro/sd35/inpaint 各一把，lifespan 里 `init_runtime()` 建)。新任务以 `model_targets` + `model_runs` 为真源，旧 `model_filter`/B2/Pro 固定字段仅作兼容；终态由 `compute_runs_final_status` 汇总。

### 4.3 端点目录（当前契约共 99 条 API 路由）
- **作业** `/api/jobs`：`POST`建（`model_targets` 可多选 b2/pro/sd35）· `POST /free` 建自由多图任务 · `GET`列 · `GET {id}` · `GET {id}/stream`(SSE) · `POST {id}/cancel` · `POST cancel-all` · `POST clear-completed`(只清任务卡，保留图片/记录) · `POST {id}/delete`(删单条任务卡) · `POST {id}/retry`（ambiguous 必须 `confirm_possible_duplicate_charge=true`）· `POST {id}/sd-upscale` · `GET {id}/result?model=&idx=` · `POST {id}/polish` · `POST {id}/edit` · `POST {id}/regen?n=`。
- **预览** `/api/preview`：`POST` 创建轻量预览 · `GET {pid}` 查询 · `POST {pid}/cancel` 取消。
- **记录** `/api/records`：`GET`列文件 · `GET load`（只读）· `POST reveal`(解密) · `POST edit`(记录内二改) · `POST result/delete`（无共享引用才物理删图）· `POST result/favorite` · `POST result/review`(人工评审：通过/备选/淘汰、标签、备注、最佳图) · `POST delete`(删整条并回收无引用文件) · `GET export/{html,pptx,favorites-pptx}`(FileResponse 下载)。
- **存储维护**：`GET /api/storage/audit` 只读扫描；`POST /api/storage/cleanup` 备份并清小样/缩略图；`POST /api/storage/orphans/quarantine` 隔离孤儿；`GET /api/storage/quarantine` 列隔离项；`POST .../{id}/restore|purge` 恢复或到期确认删除。
- **上传** `POST /api/uploads/{floor,room,ref}`；品牌 Logo 为 `POST /api/uploads/logo` 与 `POST /api/uploads/logo/clear`。
- **小样与配方**：`GET /api/swatches/recent` · `GET /api/recipes` · `/api/recipes/custom` 列表/新增/更新/删除。
- **识色与校色**：`GET /api/floor/analyze`；`POST /api/color-match/segment` 用离线 MobileSAM 自动生成地板蒙版，并接受正/负画笔与上一版蒙版做增量细化；`POST /api/color-match/preview` 支持 `scope=floor_mask|global`。默认局部模式只在 mask 内校正 LAB 的 a/b 色度、保留 L 明暗并向内羽化，区外逐像素不变；旧 `global` 模式仍以 `rect` 取样并作用全图。`POST /api/jobs/{id}/color-match` 与 `/api/records/color-match` 以 PNG 落局部结果，并在旁边留存 `_mask.png` 及记录元数据。
- **生成式修补** `/api/inpaint`：`POST /api/inpaint/segment` 复用与修补一致的三种 `target`，`strategy=scan_objects|point` 分别返回自动物件候选或点击位置区域（行优先 RLE）；两段式生成仍为 `POST /api/inpaint`（mask + n=1~3；响应含 requested_n/effective_n/notice，专职 Eraser 强制 effective_n=1）· `GET {iid}`(轮询候选) · `POST {iid}/apply`(挑中才落目标) · `POST {iid}/cancel` · `GET comfyui/ping`(后端代理探测本地实例)。
- **评审复盘**：`GET /api/review/summary` 聚合维度统计 · `GET /api/review/gallery?filter=pass|best` 好图样本库。
- **失败** `POST /api/failure/classify` · `GET /api/failure/rules`；**连通** `GET /api/connection/test`。
- **配置** `GET/PUT /api/config`；`DELETE /api/config/secrets/{gemini|fal|deepseek}` 清系统密钥；**模型** `GET /api/models`；**选项** `GET /api/options`；**用量** `GET /api/usage`（含 uncertain 与成本上下限）；**健康** `GET /api/healthz`。
- **全屋研究快线**：`PUT /api/whole-home-design/projects/{id}/anchors` 保存人工比例尺/空间/入口/门窗；`POST|PUT .../structure-review` 准备九问并编译结构合同；`POST .../model-runs` 启动 Blender/IFC；`POST .../model-runs/{run_id}/review` 重试 Gemini 复审；`GET .../artifacts/{kind}` 下载经过白名单过滤的产物。旧 `refine/preview|commit` 4K 全屋精修端点已删除。
- **静态/缩略图**：`GET /thumb/{uploads,outputs}`(懒生成 JPEG)；`/outputs`、`/uploads` 挂目录服原图。

### 4.4 加新端点的范式
1. 选对应的 `routes_*.py` 加 `@router.xxx` 端点（server_api.py 不加业务端点）;请求模型放 `server_schemas.py`。
2. 队列状态经 `state.JOBS` 的方法走（add/get/pop/snapshot/request_cancel…);复合操作（检查+改状态必须原子）用 `state.JOBS.locked()`，**块内禁止再调 persist()/add() 等加锁方法**（threading.Lock 不可重入，会死锁）——`JOBS.persist()` 一律在 locked() 外调。
3. 新端点会让 `tests/test_route_contract.py` 报"未入册"，把 (path, method) 加进 EXPECTED_ROUTES 即完成登记。
4. 业务逻辑能复用引擎就复用（导出→`exports.export_*`、识色→`image_prep.analyze_floor_tone`、配方→`recipes.*`、校色→`color_match.*`）。

### 4.5 SD 3.5 独立线路
- 仅“纯效果图”，必须开启 `sd_enabled` 且配置 Fal Key。
- `sd_prompts.py` 独立编译正负提示词，禁止把 Gemini 的对抗式 prompt 机械清洗后共用。
- `api._call_fal_queue_json` 使用 `queue.fal.run` 持久队列；提交成功后立即把 SD/AuraSR 的 `request_id/status_url/response_url/cancel_url` 写入 `model_runs.settings` 并随 `.queue_state.json` 落盘，重启后重试继续轮询同一请求。状态接口的 HTTP 202 表示正常排队/推理。提交响应未知时不自动重交，防重复计费。新队列默认忽略系统/Google 代理直连，只有 `fal_queue_proxy` 非空时才走专用代理。
- SD 基础图约 1MP、64 对齐；InstantX IP-Adapter 强制使用地板小样；2K/4K 再走 AuraSR 4×。超分失败保留基础图并标 partial。
- 完整契约与校准说明见 [`SD35_INTEGRATION.md`](SD35_INTEGRATION.md)。

### 4.6 生成式修补（移除已可用；添加仍为实验能力）
- **能力**：AI 智能选区与原有画笔共同生成 mask。移除模式后台扫描可分割物件，点击青色轮廓多选；添加模式点击地面、墙面、桌面等承载区域取得初始选区；画笔补选、橡皮排除、撤销和清空继续保留。移除自动外扩覆盖边缘/阴影；添加默认 grow=0，内部羽化确保选区外逐像素不变。三处入口：任务结果、历史记录、房间图生成前清理家具；房间 JPEG 先按 EXIF 方向归一化，避免选区错位。
- **智能分割**（`floor_segmentation.py`）：源图工作长边 1280；`scan_object_masks` 用 `8×6` 规则网格取得多尺度候选，经 predicted IoU ≥ 0.70、稳定度 ≥ 0.82、面积、点击连通域过滤，再用 IoU ≥ 0.80 / containment ≥ 0.92 去重，最多返回 24 个候选；`segment_mask_at_point` 对归一化点击坐标选取置信度与稳定度综合最优区域。图像 embedding 复用，完整扫描另有 3 项 LRU 缓存。
- **点击优先级**：完整扫描每处理一个网格点便释放共享推理锁；前端若点到尚无候选的位置，立即请求 `strategy=point` 并显示“正在识别点击的物件…”，因此点击无需静默等待整图扫描。点选结果会先加入当前候选，后台扫描结束后按 id 合并，不覆盖用户已选内容。
- **前端合成与传输**：候选 mask 以行优先、0/1 交替计数的无压缩 RLE 返回。`InpaintDialog` 分别维护 AI 选区、手绘包含层和手绘排除层，最终规则为 `union(AI selections, manual include) - manual exclude`；移除/添加各自保存独立编辑状态，提交前导出二值 PNG。
- **管线**（`api.call_image_inpaint` 统一调度）：`image_ops.prepare_inpaint_masks` 拆出二值 `engine_mask` 与最终 `blend_mask` → 按模式裁上下文（移除偏局部清晰度，添加扩大透视上下文，长边上限 2048）→ 引擎 → `_stitch_inpaint_result` 贴回 → 独立 blend mask 合成。默认 ComfyUI 模板不再重复 GrowMask；自定义 workflow 收到的也是已处理二值 mask。
- **引擎**：移除默认 `bria-eraser`（可选 finegrain/lama/flux-fill/gemini-mark）；添加默认 `flux-fill`（可选 qwen-inpaint/gemini-mark）。**qwen-inpaint 实测无法做移除**（指令编辑类模型强条件于原图，mask 对模型不可见），只保留在添加列表。`inpaint_provider=comfyui` 时全部走本地 ComfyUI 实例（workflow 模板占位符注入，内置模板在 `assets/comfy_workflows/`）。
- **提示词与计费**：FLUX/Qwen/Gemini/ComfyUI 使用按模式编译的边界、透视、光照和材质保持指令；BRIA/Finegrain/LaMa 不读取提示词且没有可控 seed，服务端把多候选请求降为 1 次。usage 按实际候选调用数记录，取消不记失败、已出图仍记成功，本地 ComfyUI 归 `local` 且默认 API 成本为 0；apply 不计费。候选以无损 PNG 存 `output_files/_inpaint_candidates/`，apply/cancel/trim/关闭弹窗时清理；同时最多 3 个 running/applying 会话，总表最多 20 条，超限返回 429。
- **恢复边界**：修补会话仍是进程内临时状态，不承诺服务重启恢复；需要持久恢复的是主作业与 SD/AuraSR Fal 队列句柄。
- **降级边界**：MobileSAM 不输出类别名称，自动扫描属于 best-effort；模型缺失、加载失败、无稳定候选或阴影/反射边界不完整时，必须提示用户用画笔补选，不能阻断原手绘流程，也不能擅自扩成全图。
- **成熟度与验收**：2026-07-15 经用户在本地真实工作图上操作确认，当前生成式移除已经达到可用程度，可作为任务结果、历史记录和房间图预处理的正式工具使用。生成式添加仍依赖所选模型对物体尺度、透视和光照的理解，继续标为实验能力；超大选区被缩到 2048 工作分辨率时，局部细节仍可能比原图略软。

---

## 五、前端：`web/`

### 5.1 技术栈
Next.js **16.2.9**（App Router + Turbopack）· React **19.2** · Tailwind **v4** · shadcn/ui（基于 `@base-ui/react`）· sonner（toast）· lucide（图标）。常用脚本：`npm run dev`(3000) / `npm run lint` / `npm run build`；生产输出是 `web/out` 静态站，不运行 `next start`。

### 5.2 结构
```
web/src/
├── app/
│   ├── layout.tsx        根布局：字体 + ThemeProvider + <AppShell> + <Toaster>
│   ├── globals.css       ★ 浅色/深色设计令牌 + 滚动条 + keyframes —— 改主题改这里
│   ├── page.tsx          生成页（三步手风琴状态编排 + 固定操作条 + 结果队列 + 批量/预览弹窗）
│   ├── records/page.tsx  记录页（材料检索 + 房间/评审筛选 + 候选对比、收藏、最佳图、人工标注）
│   ├── review/page.tsx   评审复盘（通过率、质量分布、问题标签、近期洞察和好图样本库）
│   ├── usage/page.tsx    用量页（stat 卡 + 成功率 + 估算成本 + 明细表）
│   └── settings/page.tsx 设置页（密钥、线路网络、生成式修补引擎、模型单价、PPTX 品牌）
├── components/
│   ├── AppShell.tsx      ★ 应用外壳：奶油侧边栏(236) + 顶栏(56,路由映射标题) + 内容槽
│   ├── GenerateStepCard.tsx 产品/场景/输出单开手风琴卡与完成状态
│   ├── ParamsForm.tsx    场景表单（工作流 / 核心四项 / 电影模式 / 更多场景参数 / 地板占比）
│   ├── OutputForm.tsx    输出表单（多模型 / 比例 / 画质 / SD 高级参数）
│   ├── JobCard.tsx       模型页签 + 单大图 + 候选缩略条 + 评审/收藏 + 既有编辑操作
│   ├── FloorUploader.tsx 地板上传 + 识色摘要 + 最近小样网格 + 历史弹窗
│   ├── CompareSlider.tsx 前后图拖动对比
│   ├── ColorMatchDialog.tsx 局部/全图模式、1% 自动强度、高级参数与竞态安全预览
│   ├── ColorMaskEditor.tsx  MobileSAM 初始蒙版、正负画笔、撤销/清空/重新识别
│   ├── InpaintDialog.tsx 生成式修补（AI 物件扫描/单点选区、画笔/橡皮/撤销、候选抽卡、对比与 apply）
│   ├── ThemeProvider.tsx next-themes 接线（浅色/深色/跟随系统）
│   ├── ImageZoom.tsx     全屏看图；支持原图+结果透明图层，滚轮缩放/拖动/双击复位/Esc
│   ├── dc-ui.tsx         设计基元：SectionHeader(点头标)·Segmented(分段)·Pill(胶囊)
│   └── ui/               shadcn/Base UI 基础组件（button/select/dialog/switch/双端 slider…）
├── lib/
│   ├── api.ts            ★ typed 客户端，开发指向 7870、生产走同源
│   ├── types.ts          JobView/GenParams/OptionsView/ConfigView/UsageSummary/RecordEntry… 类型
│   ├── draft.ts          表单草稿及记录参数一次性复用请求
│   └── notify.ts         完成通知（浏览器 Notification + Web Audio 提示音）
└── hooks/useJobStream.ts EventSource 封装（终态 close，防自动重连风暴）
```

### 5.3 设计系统
全站颜色/圆角/卡片底色等是 **Tailwind v4 设计令牌**，集中在 `globals.css` 的 `:root` 与 `.dark`。`ThemeProvider` 通过 `next-themes` 切换浅色、深色或跟随系统；shadcn 组件统一引用令牌。重复的视觉模式抽进了 `dc-ui.tsx`。完整视觉规范以仓库上级 `test/DESIGN.md` 为准；其中旧 NiceGUI 路径已经废弃，真实实现源是 `web/src/app/globals.css`、`AppShell.tsx` 与 `dc-ui.tsx`。

### 5.4 数据流
页面 `useEffect` 调 `api.*` 取数；活动作业用 `useJobStream`(EventSource) 看 SSE 实时进度；生成页另有 2.5s 轮询 `listJobs` 做队列聚合进度。左栏由 `openStep: 0|1|2|3` 保证同一时刻最多展开一张步骤卡，`ParamsForm` 与 `OutputForm` 分别只负责场景/输出字段。任务卡通过 `jobResult` 读取候选缩略图，并以当前图片文件名在 `json_path + record_id` 中解析 `result_id`，再复用记录页的评审/收藏接口。每次生成把不含密钥的 `gen_context` 写入记录，记录页通过 `draft.ts` 将参数一次性回填到生成页。`api.imgUrl()` 把后端相对图 URL 拼成绝对地址。

### 5.5 地板校色数据流
1. 弹窗默认 `floor_mask`：图片加载后请求 `/api/color-match/segment`，MobileSAM 在本机 CPU 生成初稿；绿色笔作为前景约束、红色笔作为背景约束，随后用 GrabCut 贴合边缘。模型不可用时只采用明确的绿色笔触，不扩散、不回退成全图修改。
2. 有效蒙版触发 `/api/color-match/preview`。经典模式保持原受限 LAB 统计迁移；精细模式先排除裁切、反光、深阴影与异色离群像素，再做受限协方差预对齐和固定旋转的一维分位数迭代，处理偏斜/多峰颜色分布。局部模式默认只迁移 a/b，L 通道保持原场景光影；合成 mask 只向内部羽化，区外像素不变。
3. 前端缓存满强度预览，自动强度 `0%~100%` 以 1% 步进在 canvas 即时混合；笔触、参照、高级参数或羽化变化才防抖请求后端，序号机制丢弃过期响应。
4. 切到 `global` 即恢复旧流程：矩形只作取样/诊断，自动与手动参数作用整张图。局部结果无损 PNG 落盘并保存配套 mask；保存始终是独立动作。
5. API schema 与独立工具的 `algorithm=classic|distribution` 默认仍是 `classic`，Floor Engine 的 `ColorMatchDialog` 首次预览则默认请求 `distribution`（精细 2.0）；`illumination_mode=off|chroma|full` 默认 `off`。请求空间光照校正时 schema 自动切换到精细算法；二次曲面采用分块中位数与 Huber IRLS 拟合，并设置色度/亮度幅度上限。分片执行必须传递全图 y 坐标，保证预览与全分辨率输出一致。
6. 前端必须分开保存“已选择模式”和“画布已应用模式”：切换请求完成前继续标注旧画布版本，只有新预览图片 `onload` 后才能更新“当前画面”。1.0/2.0 切换常驻在弹窗顶部；若精细算法或光照拟合回退，状态条显示实际生效模式，不能只依赖高级选项按钮的选中态。
7. `standalone_color_calibrator/advanced.py` 是主系统与独立工具共享的无 UI 核心。质量报告包含可用像素比例、排除原因、空间色偏跨度、初始/预计 ΔE00、预估色域裁切率和 0–100 分；诊断覆盖图绿色=可用、红色=反光/裁切、蓝色=深阴影、黄色=离群。低分只警告，不阻止保存；算法、光照模式和报告会写入结果 metadata。

### 5.6 生成式修补智能选区数据流
1. 打开 `InpaintDialog` 后，前端把原 `target` 传给 `/api/inpaint/segment`；后端继续使用 `_resolve_inpaint_source` 做 job/record/room 路径归属校验和 EXIF 归一化，不接受前端直接指定任意文件。
2. 移除模式异步请求 `scan_objects` 并绘制青色轮廓；添加模式等待用户点击。若移除模式在扫描结束前点击，或点击位置未命中已有候选，则立即请求 `point`，UI 必须显示忙碌反馈。
3. 前端将 RLE 解码为本地 mask 与 owner map：轮廓层只负责命中测试和提示，红色层表示最终被选区域；点击候选可切换，多候选重叠时按 owner 命中，后台返回不得清空点击期间加入的候选。
4. AI 选区、画笔包含层、画笔排除层只在前端合成，沿用原有 ≤2048 长边 mask 画布；`POST /api/inpaint` 的两段式生成/计费/候选/apply 契约不变，因此智能选区不接触生成模型选择和落盘逻辑。
5. 弹窗每次切换移除/添加模式都恢复该模式自己的蒙版状态；AI 失败时保留画笔工具并显示 warnings，禁止无反馈 return。

### 5.7 电影真实感与地板占比控制
1. `GenParams.cinematic_enabled` 控制正式 B2 / Pro 任务是否先运行 `cinematic_planner.py`。支持的工作流会根据房间、风格、人物/宠物和现实光源生成导演规划；调用失败时写入 `cinematic_error` 并使用本地 fallback，不阻断付费生图。SD 3.5 不消费这段规划。
2. `floor_coverage_min` / `floor_coverage_max` 的服务端范围均为 `10..80`，默认 `40/50`，Pydantic 校验 `min <= max`。字段经 `TaskParams` 同时进入 `prompts.py`、`cinematic_planner.py` 与 `sd_prompts.py`，禁止再在独立风格文案里写死另一组百分比。
3. 高级选项使用 Base UI 双端 Slider。拖动过程只更新 `coverageDraft` 与可见数字，`onValueCommitted` 在松手后一次写回表单，避免受控数字框在用户清空输入的瞬间强行回填。
4. 前端载入旧草稿时会把越界值归一化到 `10..80` 并保证最大值不小于最小值；两个 thumb 有独立 ARIA 名称，可用方向键微调。
5. 修改提示词覆盖范围时必须保留默认 `40–50%` 的黄金快照，并为自定义值补 Gemini、电影规划、SD 及 schema 契约回归。

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
8. **`webui.py` 已退役删除**：UI 只有 `web/` 一套。2026-07 重构后新端点加对应 `routes_*.py`（server_api.py 只做组装），共享状态进 `server_state.py`、请求模型进 `server_schemas.py`。
10. **TODO（技术债记账）**：records/prompt_data 等逻辑层仍直接返回带 emoji 的 UI 文案（`✅/❌/⚠️` 前缀是前端契约的一部分）；彻底"逻辑层去 UI 化"需要前端同步改判定逻辑，未排期。`_compose_prompt`（prompts.py，~330 行）保留为单函数是刻意决定：按工作流分支再拆会给每个分支挂大量解包样板，收益为负。
9. **Canvas 跨源污染**：开发前端 `:3000` 画入后端 `:7870` 图片后，Canvas 可能能显示却不能 `toDataURL()`/`getImageData()`；结果放大必须复用 `ImageZoom` 的原图+结果图层，不要重新引入 Canvas 导出。生产同源也要保持这条，避免 `file://` 调试再次崩溃。

---

## 七、开发工作流

**加一个功能**（典型全链路）：
1. （如需）引擎逻辑落 `records.py`/`prompts.py`/`api.py`/`color_match.py` 等（headless，别引 UI 框架）。
2. 对应 `routes_*.py` 加端点（范式见 §4.4：schemas 进 `server_schemas.py`、队列走 `state.JOBS`、契约测试登记新端点）。
3. `web/src/lib/api.ts` 加封装 + `types.ts` 加类型。
4. 在对应页面/组件接 UI（沿用 `dc-ui` 基元与 `globals.css` 令牌）。
5. **验证**：`cd web && npm run lint && npm run build`；后端 `.venv/bin/python -m pytest`；必要时再做 `python -c "import Floor_engine_server.server_api"` 冒烟与无头截图比对（§六.6）。注意 `next build` 不替你跑 ESLint。
6. 在 **`开发日志.md` 顶部追加一条**（改了啥、为什么）。
7. 提交（见 §九）。

**测试**：在 runnable 根目录运行 `.venv\Scripts\python.exe -m pytest -q`（引擎层 golden 提示词、安全硬化、系统密钥环、计费安全重试、存储生命周期、全屋设计自动摘要/人工锚点/九问结构图/revision/本地 Blender/GLB/IFC研究快线、人工评审元数据、AI/手绘蒙版与局部/全图校色、独立样品对色、SD 提示词/IP-Adapter/FAL 队列恢复、生成式修补、路由契约及非校色功能回归；当前 **315 passed，1 skipped**）。前端依次运行 `npm run test:design`、`test:scene`、`test:storage`、`test:security`、`lint` 与 `build`。`tests/golden/` 基准入库，缓存不入。

---

## 八、旧版 NiceGUI（已退役）

- **`webui.py` 已于 2026-06-29 删除**（STEP3）。`python -m Floor_engine_server` 不再起界面，只打印退役提示并 `exit 1`（见 `__main__.py`）；`requirements.txt` 也去掉了 `nicegui`，整包不再依赖 NiceGUI。
- **日常兜底改由冻结原型 `test/floor_engine/` 承担**（同源、共享数据，仍是完整可跑的 NiceGUI 版）。它**只为兜底、不再加新功能**；本仓库 `Floor_engine_server/` 已是唯一在维护的代码。
- 退役理由与时点见 `开发日志.md`（2026-06-29 条）。如需回滚，`git revert` 本次退役提交即可恢复 `webui.py` 与 `nicegui` 依赖。

---

## 九、构建 / 打包 / Git

- **前端生产静态站**：`cd web && npm run build` 生成 `web/out/`；本项目配置了 `output: "export"`，不使用 `next start`。从 `test/` 运行 `python Floor_engine_server/serve.py`，FastAPI 会把 `web/out` 挂到 `/`，前后端同源跑在 7870。
- **Windows onefile（按需执行，不是日常 push 门）**：运行 `build_windows.bat`。脚本以 `--jobs=4` 限制 Nuitka 并发，打入 Web UI、MobileSAM/ONNX Runtime、IfcOpenShell、keyring 和 `tools.fastloop_research`；Blender 5.2 仍是外部依赖。产物为 `dist/FloorEngine.exe`。只有明确发布 exe 时才执行完整打包；普通代码 push 以全量测试、前端 build、HTTP 和相关真实 Blender/IFC 冒烟为门。
- **不要误用旧脚本**：上级 `test/build.bat`、`floor_engine.spec`、`run_app.py` 是冻结 NiceGUI 原型 `floor_engine/` 的 PyInstaller/pywebview 打包链，不包含当前 Next.js 商业前端和最新功能。
- **Git**：本仓库是独立 git 仓。运行期产物、系统密钥、`data/`、虚拟环境和构建目录均不入库；提交信息沿用 `feat/fix/docs(scope): 说明`。日常 push 不自动触发 Nuitka 打包，除非发布任务明确要求 exe。

---

## 十、全屋设计 v1 合同

- 页面：`/design`；后端：`routes_whole_home_design.py`；领域与 ZIP：`whole_home_design.py`。
- 存储：`output_files/_whole_home_design/{projects,assets,bundles,model-runs}`；项目 JSON 原子替换，所有模型输出按 project/structure hash 版本化且不覆盖。
- 结构入口：必须有空间、入户门和且仅有一条人工两点比例尺；九问答案、技术结构包、source/normalized/project/revision/hash全部绑定。错误或不完整结构进入 `needs_professional_review`，不计产品失败。
- 本地建模：`tools/fastloop_research` 通过 Blender CLI 参数数组生成 Blend/GLB/三视图，通过 IfcOpenShell 生成并回读研究 IFC；不用 PowerShell拼接、MCP、网络或全屋 Boolean。研究模型不等于施工 BIM。
- 调用：概念草稿只保留固定 2 次 B2/2K；Google 与 Fal 不自动付费切线。概念图与研究灰模并行，不能改变墙体、门窗或邻接图。
- QA：研究模型机械门通过后，Gemini 同时读取原图、人工叠图、顶视、东北和西北视图。Gemini 不可用时保留本地产物并标 `external_review_pending`；IfcOpenShell 缺失必须标 `blocked_dependency_missing`，不能由视觉通过晋级。
- 高级任务包仍保留；360° 新建/恢复/修补继续退役，旧 ERP 只读。

---

## 十一、一句话回顾

**改业务** → 引擎模块（headless，新仓库这份）；**改接口** → `routes_*.py`（server_api.py 只组装）；**改界面** → `web/`；**换主题** → `web/.../globals.css` + `ThemeProvider.tsx`。旧 `webui.py` 已退役，UI 只有 `web/` 一套。启动就一句：跑 `test/` 下的一键启动脚本（Windows `.bat` / Linux·macOS `.sh`）。
