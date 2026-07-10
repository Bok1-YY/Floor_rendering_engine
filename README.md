# Floor AI · 地板效果图生成引擎

**在线展示：** [bokiframe.com](https://bokiframe.com)

> 面向**地板行业**的 AI 效果图生成工具：上传地板小样 → 自动识色 + 智能配方 → 配置场景参数 → 调用 Gemini / Fal 图像模型出图，支持 **4K**、快出（B2）与精修（Pro）双模式。
>
> 后端 **FastAPI 无头服务** + 前端 **Next.js**，前后端解耦、异步作业队列、SSE 实时进度。

---

## ✨ 功能特性

- **地板小样识色**：上传地板图，自动分析主色调，驱动后续提示词与配方。
- **智能配方推荐**：按色调/风格自动推荐场景搭配，一键套用。
- **35+ 参数化提示词**：风格、灯光、机位、房型、地区市场等多维选项，组装为高质量英文提示词。
- **多工作流**：纯出图 / 参照图 / 替换 / 宠物友好 / Omakase / 墙板模式等多种生成工作流。
- **双模型双档位**：B2 快出预览 + Pro 精修成图；可选 Google Gemini 或 Fal 提供方，支持失败自动切换（failover）。
- **4K 出图**：面向商用提案的高分辨率输出。
- **磨缝 / 二次编辑**：对已出图做局部修整与再生成，支持多候选切换。
- **异步作业队列**：`POST` 秒回 `job_id`，`SSE` 推送实时进度；长请求不再被网络中间层 reset。
- **记录与导出**：历史记录持久化、收藏、最佳图、人工评审标签/备注，导出 HTML / PPTX 提案 deck。
- **用量统计**：出图数量、成功率、明细一览。

---

## 🏗️ 架构

```
                 ┌──────────────────────────────────────────────┐
  浏览器  ─────▶ │  Next.js 前端 (web/, :3000)                   │  React 渲染
                 │    └─ api.ts ──HTTP / SSE──┐                  │
                 └────────────────────────────┼─────────────────┘
                                              ▼
                 ┌──────────────────────────────────────────────┐
                 │  FastAPI 无头后端 (server_api.py, :7870)       │  作业队列 / SSE
                 │  作业队列 · SSE 进度 · 静态图 · 缩略图           │  静态图 / 缩略图
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
| 图像/数据 | Pillow · NumPy · python-pptx（导出 deck） |

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
| `image_provider` | `google`（默认）或 `fal` |
| `proxy` | HTTP 代理，如 `http://127.0.0.1:7897/`；网络无法直连 Google 时填写 |
| `speed_profile` | `fast`（快速失败）/ `resilient`（死磕重试） |
| `auto_failover` | Google 失败时是否自动切到 Fal |
| `tls_verify` / `tls_ca_bundle` | HTTPS 证书校验开关 / 自定义 CA |
| `max_concurrent_per_model` | 每个模型的并发上限 |

> ⚠️ `engine_config.json` 含密钥，已被 `.gitignore` 排除，**不会进入版本库**。请勿把密钥提交到仓库。

前端开发默认由 `web/.env.development` 指向 `http://127.0.0.1:7870`；生产静态站由 `web/.env.production` 使用同源 API。
后端可用环境变量：`FLOOR_API_PORT`(7870) / `FLOOR_API_HOST`(仅支持 127.0.0.1) / `FLOOR_API_CORS`（放行的开发前端源）。本版本是客户本机程序，不提供无认证的局域网或公网部署。

### 📁 数据目录
后端会把**配置与运行期数据**写在仓库目录的**上一级**（`config.py` 中 `BASE_DIR = 仓库上级目录`）。因此运行时会在上级目录生成：

| 路径 | 内容 |
|---|---|
| `output_files/` | 出图、每个素材的 `*_记录.json`、优化图、候选图 |
| `output_files/.queue_state.json` | 作业队列持久化（重启后恢复） |
| `engine_config.json` | 密钥与线路配置（含敏感信息，已 gitignore） |
| `_ng_uploads/` · `_ng_thumbs/` | 上传素材 · 缩略图缓存 |
| `app_local_save.log` | 运行日志 |

把仓库整体挪到别处，`BASE_DIR` 会自动指向新位置，得到**独立**的数据目录，无需改代码。

---

## 📖 使用流程

1. 在「**生成**」页上传一张地板小样（或从最近小样里选）。
2. 系统自动识色；如需可点「智能配方」套用推荐搭配。
3. 选择工作流、市场地区、模型与档位，按需展开高级参数。
4. 点击生成 → 任务进入右侧队列，SSE 实时显示进度；出图后可预览、切换候选。
5. 对结果可**磨缝 / 二次编辑 / 重抽**；满意的可**收藏**或标为**最佳图**。
6. 到「**记录**」页按房间/评审状态筛选，人工标注**通过 / 备选 / 淘汰**、问题标签与备注，并导出 HTML / PPTX 提案；「**用量**」页看统计。

---

## 🗂️ 项目结构

```
Floor_engine_server/            # 后端 Python 包（= 本仓库根）
├── server_api.py               # FastAPI 无头服务：端点 + 作业队列 + SSE + 静态/缩略图
├── config.py                   # 路径/配置中心（BASE_DIR、engine_config.json 读写）
├── models.py                   # 数据模型：作业、参数、状态
├── prompt_data.py              # 选项表（风格/灯光/机位/房型/色调…）+ 识色 + 中英翻译
├── prompts.py                  # 提示词组装（参数 → 英文 prompt + 落 JSON/PNG）
├── recipes.py                  # 智能配方推荐
├── api.py                      # 图像模型调用（Gemini / Fal / 磨缝二改 / 连通测试）
├── records.py                  # 记录持久化、收藏、人工评审、导出 HTML/PPTX
├── failure_kb.py               # 失败知识库（错误分类与建议）
├── web/                        # Next.js 前端
├── tests/                      # pytest（提示词回归 + 安全硬化）
└── requirements.txt
```

---

## 🛠️ 开发

深入的开发文档见 [`DEVGUIDE.md`](./DEVGUIDE.md)：架构原理、后端端点目录、前端结构与设计系统、关键约定与坑、开发工作流等。

常用命令：
```bash
# 后端测试（引擎层回归 + 安全硬化）
python -m pytest

# 前端类型检查 + 生产构建
cd web && npx tsc --noEmit && npm run build
```

---

## 📄 许可证

> 尚未选择开源许可证。正式开源前，请在此处声明许可证（如 MIT / Apache-2.0），并在仓库根添加对应 `LICENSE` 文件。
