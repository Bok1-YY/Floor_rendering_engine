# Floor AI · 地板效果图生成引擎

**在线展示：** [bokiframe.com](https://bokiframe.com)

> 面向**地板行业**的 AI 效果图生成工具：上传地板小样 → 自动识色 + 智能配方 → 配置场景参数 → 调用 Gemini / Fal 图像模型出图，支持 **4K**、快出（B2）与精修（Pro）双模式。
>
> 后端 **FastAPI 无头服务** + 前端 **Next.js**，前后端解耦、异步作业队列、SSE 实时进度。

---

## ✨ 功能特性

- **地板小样识色**：上传地板图，自动分析主色调，驱动后续提示词与配方。
- **智能配方与我的配方**：按色调/风格自动推荐场景搭配，也可保存、更新和删除自己的参数配方。
- **35+ 参数化提示词**：风格、灯光、机位、房型、地区市场等多维选项，组装为高质量英文提示词。
- **多工作流**：纯出图 / 参照图 / 替换 / 宠物友好 / Omakase / 墙板模式等多种生成工作流；Omakase 复用 Gemini Key 生成场景，可配 DeepSeek 自动备用。
- **自由创作**：用户可原样输入中/英文完整指令，按 Slot 1–3 的顺序上传多张参考图，并行交给 B2 / Pro；不经过地板提示词模板。
- **多模型并行**：B2 快出 + Pro 精修，并可启用独立的 SD 3.5 Large 实验线路；多模型可同时选择，统一进入同一任务卡。
- **SD 地板参考与超分**：SD 3.5 使用专属正/负提示词和 InstantX IP-Adapter 强制参考地板小样，约 1MP 扩散后由 AuraSR 交付 2K/4K；超分失败保留基础图并可单独重试。
- **4K 出图**：面向商用提案的高分辨率输出。
- **批量生成**：同一地板批量套用多个房间，或同一参数批量处理多个地板小样。
- **AI 地板局部校色 / 兼容全图校色**：默认由离线 MobileSAM 自动识别地板，可用绿色/红色画笔补选或排除复杂边界；稳健 LAB 算法只修正蒙版内色度、保留地板明暗，墙面与家具逐像素保持原样。旧的框选取样 + 全图校准仍可切换使用。
- **生成式移除（已可用）/添加（实验）**：类 Lightroom 画笔涂抹局部处理——移除自动适度外扩以覆盖边缘和阴影，已通过本地真实工作图验收；添加默认严格限制在涂抹区。推理二值 mask 与最终羽化 mask 分离，支持无损 PNG 候选挑选后写回；不支持可控变体的专职 Eraser 自动降为 1 张，避免重复计费。任务结果、历史记录、房间图三处入口均可使用；超大选区或生成式添加的质量仍取决于模型与本地显存。
- **异步作业队列**：`POST` 秒回 `job_id`，`SSE` 推送实时进度；长请求不再被网络中间层 reset。
- **记录复用与对比**：生成入参随记录落盘，可一键复用参数；替换类工作流支持前后拖动对比。
- **记录、评审与导出**：历史记录持久化、收藏、最佳图、评审标签/备注；独立复盘页聚合通过率与好图样本，导出 HTML / 带品牌信息的 PPTX 提案 deck。
- **用量与成本**：统计出图数量、成功率和明细，可按模型配置单价并估算成本。
- **深色模式**：支持浅色、深色和跟随系统主题。

---

## 🏗️ 架构

```
                 ┌──────────────────────────────────────────────┐
  浏览器  ─────▶ │  Next.js 前端 (web/, :3000)                   │  React 渲染
                 │    └─ api.ts ──HTTP / SSE──┐                  │
                 └────────────────────────────┼─────────────────┘
                                              ▼
                 ┌──────────────────────────────────────────────┐
                 │  FastAPI 无头后端 (:7870, server_api.py 组装)   │  作业队列 / SSE
                 │  业务路由 routes_* · 作业队列 · SSE · 缩略图     │  静态图 / 缩略图
                 └────────────────────────────┬─────────────────┘
                                              ▼
                 ┌──────────────────────────────────────────────┐
                 │  引擎模块（headless）                          │
                 │  提示词组装 · 模型调用 · 识色 · 配方 ·          │
                 │  记录持久化 · 人工评审 · 导出                    │
                 └──────────────────────────────────────────────┘
```

- **前端**只负责渲染与交互，所有业务经 HTTP/SSE 调后端。
- **后端**是唯一服务端，把耗时的出图封装成**异步作业**（秒回 `job_id`，SSE 推进度）。
- **引擎模块**是 headless 的核心逻辑（提示词、模型调用、识色、配方、记录、人工评审、导出），可独立复用。

---

## 🧰 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Next.js 16（App Router + Turbopack）· React 19 · Tailwind v4 · shadcn/ui · lucide · sonner |
| 后端 | Python · FastAPI · Uvicorn · SSE |
| 图像模型 | Google Gemini（image）· Fal（可选） |
| 图像/数据 | Pillow · NumPy · OpenCV · ONNX Runtime / MobileSAM（离线分割）· python-pptx（导出 deck） |

---

## 🚀 快速开始

### 环境要求
- **Python** 3.10+（推荐 3.12）
- **Node.js** 20.9+
- 一个 **Google Gemini API Key**（出图必需；[申请入口](https://aistudio.google.com/apikey)）
- 若你所在网络无法直连 Google，需准备可用**代理**（见 [配置说明](#️-配置说明)）

### 1. 获取代码
后端以 Python 包 `Floor_engine_server` 方式运行，请把仓库克隆为该名字的目录：
```bash
git clone <本仓库地址> Floor_engine_server
cd Floor_engine_server
```

### 2. 安装后端依赖（建议用虚拟环境）
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 开发与测试：pip install -r requirements-dev.txt
```

### 3. 安装前端依赖
```bash
cd web && npm install && cd ..
```

### 4. 启动
> 后端需在**仓库目录的上一级**运行（详见 [数据目录](#-数据目录)）。

```bash
# 后端（在 Floor_engine_server 的【上一级】目录）
cd ..
python -m Floor_engine_server.server_api        # → http://127.0.0.1:7870

# 前端（另开一个终端，在 web/ 下）
cd Floor_engine_server/web && npm run dev        # → http://localhost:3000
```

浏览器打开 **http://localhost:3000**。后端健康检查：`GET http://127.0.0.1:7870/api/healthz` → `{"ok":true}`。

首次使用请到前端「**设置**」页填入 Gemini API Key（没有 key 也能打开界面，但无法出图）。

---

## ⚙️ 配置说明

配置存于 `engine_config.json`，可在前端「设置」页可视化编辑，或直接写文件。常用字段：

| 字段 | 说明 |
|---|---|
| `gemini_api_key` | Google Gemini 密钥（出图必需） |
| `fal_api_key` | Fal 密钥（选用 Fal 提供方时需要） |
| `deepseek_api_key` | 可选：Omakase 的 DeepSeek 备用线路 Key |
| `omakase_enabled` | 是否启用 AI 场景代笔（Gemini 主线路） |
| `sd_enabled` | 是否启用 SD 3.5 实验线路（默认关闭，仅纯效果图） |
| `omakase_gemini_model` | Omakase 文本模型，默认 `gemini-2.5-flash` |
| `image_provider` | `google`（默认）或 `fal` |
| `inpaint_provider` 等 | 生成式修补引擎组：提供方（`fal`/`comfyui`）、移除模型 `inpaint_remove_model`、添加模型 `inpaint_add_model`、ComfyUI 地址/超时/自定义 workflow；均可在设置页可视化配置 |
| `proxy` | HTTP 代理，如 `http://127.0.0.1:7897/`；网络无法直连 Google 时填写 |
| `fal_queue_proxy` | SD/AuraSR 队列专用代理；默认留空并忽略系统代理直接连接 FAL |
| `speed_profile` | `fast`（快速失败）/ `resilient`（死磕重试） |
| `auto_failover` | Google 失败时是否自动切到 Fal |
| `tls_verify` / `tls_ca_bundle` | HTTPS 证书校验开关 / 自定义 CA |
| `max_concurrent_per_model` | 每个模型的并发上限 |
| `usage_prices` | 各模型成功出图的单价，用于用量页估算成本 |
| `pptx_company` / `pptx_contact` | PPTX 提案中的公司名与联系方式 |

> ⚠️ `engine_config.json` 含密钥，已被 `.gitignore` 排除，**不会进入版本库**。请勿把密钥提交到仓库。

前端开发默认由 `web/.env.development` 指向 `http://127.0.0.1:7870`；生产静态站由 `web/.env.production` 使用同源 API。
后端可用环境变量：`FLOOR_API_PORT`(7870) / `FLOOR_API_HOST`(仅支持 127.0.0.1) / `FLOOR_API_CORS`（放行的开发前端源）。本版本是客户本机程序，不提供无认证的局域网或公网部署。

### 📁 数据目录
后端会把**配置与运行期数据**写在仓库目录的**上一级**（`config.py` 中 `BASE_DIR = 仓库上级目录`）。因此运行时会在上级目录生成：

| 路径 | 内容 |
|---|---|
| `output_files/` | 出图、每个素材的 `*_记录.json`、优化图、候选图 |
| `output_files/.queue_state.json` | 作业队列及 SD/AuraSR Fal 请求句柄持久化（重启后按原 request 恢复） |
| `engine_config.json` | 密钥与线路配置（含敏感信息，已 gitignore） |
| `custom_recipes.json` | 用户保存的“我的配方”（位于仓库上级，不进入本仓库） |
| `_ng_uploads/logo_*` | PPTX 提案品牌 Logo（由程序上传和清理） |
| `_ng_uploads/` · `_ng_thumbs/` | 上传素材 · 缩略图缓存 |
| `app_local_save.log` | 运行日志 |

把仓库整体挪到别处，`BASE_DIR` 会自动指向新位置，得到**独立**的数据目录，无需改代码。

---

## 📖 使用流程

1. 在「**生成**」页上传一张地板小样（或从最近小样里选）。
2. 系统自动识色；可套用智能推荐，或保存和复用「我的配方」。
3. 选择工作流、市场地区、模型与档位，按需展开高级参数；也可选“自由创作”原样输入指令并上传 1–3 张有序参考图。
4. 点击生成 → 任务进入右侧队列，SSE 实时显示进度；出图后可预览、切换候选和查看前后对比。
5. 对结果可**磨缝 / 二次编辑 / 重抽 / AI 地板局部校色**；复杂地板边界可用正负画笔修蒙版，满意的可**收藏**或标为**最佳图**。
6. 到「**记录**」页筛选、复用参数、人工标注**通过 / 备选 / 淘汰**并导出提案；「**评审复盘**」看聚合表现和好图样本，「**用量**」页看数量与估算成本。

---

## 🗂️ 项目结构

```
Floor_engine_server/            # 后端 Python 包（= 本仓库根）
│  ── Web 层 ──
├── server_api.py               # FastAPI app 组装器（lifespan/CORS/静态图/前端挂载 + include_router）
├── routes_*.py                 # 六个业务路由：jobs(队列+生图协程)/previews/library/config/tools/inpaint
├── server_state.py             # 进程内状态：任务注册表 ×3、按模型并发信号量
├── server_schemas.py           # 全部 HTTP 请求模型（前端契约）
├── server_helpers.py           # 路由共享工具：URL 映射、路径守卫、上传落盘
├── image_ops.py / task_registry.py  # 纯 PIL mask 处理 / 泛型任务注册表容器
│  ── 引擎层（headless）──
├── config.py                   # 路径/配置中心（BASE_DIR、engine_config.json 读写）
├── models.py                   # 数据模型：作业、参数、状态
├── prompt_data.py              # 纯数据：选项表（风格/灯光/机位/房型/色调…）+ 中英翻译
├── prompts.py                  # 提示词组装：五阶段流水线（参数 → 英文 prompt + 落 JSON/PNG）
├── image_prep.py               # 小样 ICC→sRGB 预处理 + 识色 analyze_floor_tone
├── sd_prompts.py               # SD 3.5 独立正/负提示词编译器（不改 Gemini 资产）
├── recipes.py                  # 智能配方推荐
├── custom_recipes.py           # “我的配方”运行期 CRUD
├── api.py                      # 外部模型调用（Gemini / Fal / 磨缝二改 / 生成式修补 / 连通测试）
├── color_match.py              # 本地色彩算法（全图兼容模式 / 蒙版内稳健 LAB 色度校正）
├── floor_segmentation.py       # 离线 MobileSAM + 正负笔触 + GrabCut 地板蒙版
├── records.py                  # 记录持久化核心：队列状态、记录 CRUD、收藏/评审
├── usage_stats.py / exports.py / reveal_security.py  # 用量统计 / HTML·PPTX 导出 / 提示词混淆与揭示
├── failure_kb.py               # 失败知识库（错误分类与建议）
├── floor_renderer.py           # 本地地板透视渲染（OpenCV）
├── web/                        # Next.js 前端
├── tests/                      # pytest（golden 提示词 + 68 端点契约快照 + 安全硬化 + 回归）
└── requirements.txt
```

---

## 🛠️ 开发

深入的开发文档见：

- [`DEVGUIDE.md`](./DEVGUIDE.md)：当前单机版架构、后端端点、前端结构、关键约定与开发工作流。
- [`SAAS_ARCHITECTURE.md`](./SAAS_ARCHITECTURE.md)：未来在线网页订阅版的完整目标架构、计费、任务调度、多供应商容灾与迁移路线。
- [`SD35_INTEGRATION.md`](./SD35_INTEGRATION.md)：SD 3.5 + IP-Adapter + AuraSR 的实现、接口、失败语义与校准说明。

常用命令：
```bash
# 后端测试（当前 178 项）
.venv/bin/python -m pytest

# 前端 lint + 类型/生产构建
cd web && npm run lint && npm run build
```

---

## 📄 许可证

> 尚未选择开源许可证。正式开源前，请在此处声明许可证（如 MIT / Apache-2.0），并在仓库根添加对应 `LICENSE` 文件。
