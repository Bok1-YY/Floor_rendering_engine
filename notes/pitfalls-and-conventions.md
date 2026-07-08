# 关键约定 & 坑位速查 · Pitfalls & Conventions

> 改这套系统前必读的「地雷图」。多数来自 [[开发日志]] 里被真金白银踩出来的坑。
> 首页 → [[index]]｜为什么这么设计 → [[mental-model]]｜依赖图 → [[architecture]]

---

## 后端

### 后端必须单 worker
`_job_history`、按模型信号量、取消计数器都是**进程内**状态，多 worker 不共享。直接 uvicorn 单进程（不传 workers）。`serve.py` 的 `main()` 已保证。→ 见 [[architecture|进程内编排状态]]。

### `_persist_jobs()` 必须在锁外调
改 `_job_history` 要持 `_job_lock`，但 `_persist_jobs()` **内部会再取同一把锁**，`threading.Lock` 不可重入 → **锁内调会死锁**。加端点时仿 `cancel_all` / `clear_completed`。

### CORS 只放 3000
`FLOOR_API_CORS` 默认放行 `http://localhost:3000,http://127.0.0.1:3000`。换前端端口**必须改这个环境变量**，否则后端能 200，但浏览器跨域被拦、JS fetch 拿不到。无头截图自检时也靠改它放行测试端口。

### 端点回执的状态契约
终态卡的操作端点（重试/重抽/磨缝/二改）若「先 `create_task`、再同步 `return _job_view`」，回执 status 会是**终态**，导致前端不开 SSE → 卡片冻结。**必须在 `create_task` 前同步预置状态转移**。这是踩过的真 bug，详见 [[mental-model|前端冻结 bug]]。

### 配置/记录写盘要原子 + 重试
`_save_config`/`_save_records` 用 tmp + `os.replace` 原子写；Windows 撞无锁读者会 PermissionError → 短重试；损坏时改名 `.corrupt_<ts>` 备份而非静默清零。→ [[mental-model|静默清零 bug]]。

### 400 一般不转线，但 geo-block 例外
`_is_network_class_error` 原只认 429/5xx + 网络关键词，HTTP 400 一律不转 Fal。但 `User location is not supported`（[[mental-model|地区封锁]]）本质是线路落地问题，**破例**纳入自动转 Fal。

### 整包必须纯 headless（不引 nicegui）
webui 退役后无任何业务文件 import nicegui。改引擎时维持这一点，自检：
```
python -c "import sys,Floor_engine_server.server_api; print('nicegui' in sys.modules)"  # 应为 False
```

---

## 提示词层（保真红线）

### 改 `prompts.py` 必跑 golden + 字节比对
`tests/golden/*.txt` 锁 4 套工作流输出。凡「应一致」的路径必须逐字节相等，「应改变」的人工核对 diff 后再 `UPDATE_GOLDEN=1` 刷新。这是保真的唯一自动护栏。→ [[mental-model|Golden 护栏]]。

### 测试导入必须指向本仓库包
历史坑：`tests/` 曾全部 `from floor_engine.*`（旧同级包）= **对本仓库零覆盖**、「N passed」是假保护。现已重指 `Floor_engine_server.*`，勿再指回。→ [[mental-model|假保护]]。

### 地板技术层逐字节锁死
任何创意/场景改动（尤其 [[mental-model|Omakase]]）都不得碰地板技术层（规格/占比 40-50%/无缝/倒角/色锁/负面词/画质）。改前后必须证明 `_floor_spec_block` 逐字节一致。

### 翻译兜底不要加方括号
翻译彻底失败返回**裸原文**，不是 `[原文]`——方括号中文进 Gemini 被当格式噪音，比裸 CJK 更糟。翻译走 `GoogleTranslator` + 强制本地代理。→ [[mental-model|翻译崩溃]]。

### 无缝负面词的 motif 按拼法分流
`SEAMLESS_NEGATIVE_MOTIF` 必须随拼法变（人字=chevron / 直拼=直纹 / 方格=微色差方格），别硬编码 chevron，否则[[mental-model|正方形拼被串味成人字纹]]。

---

## 前端（web/）

### Next 16 ≠ 你训练里的 Next
`web/AGENTS.md` 明确警告，改前端前先翻 `web/node_modules/next/dist/docs/`：
- `params`/`searchParams` 变 **Promise** → 本项目**所有页面用 `'use client'` + `useEffect` 取数**绕开。
- `next/image` 默认**拦本地 IP**（`127.0.0.1:7870/outputs`）→ 图片一律用普通 `<img>`。
- `next build` **不再跑 ESLint**，只有 `tsc` 类型错会拦构建。

### effect 里别同步 setState
React19/Next16 新 lint `react-hooks/set-state-in-effect` 会报级联渲染错。恢复类逻辑（如 [[mental-model|草稿恢复]]）**合并进异步回调**（`getOptions().then`）而非单开 effect。

### 草稿持久化的 clobber 防护
生成页配置存 localStorage，恢复未完成前**不写回**（`if (!options) return` 守门），防「仅含 workflow_mode 的空 params」覆盖好草稿。

### `.env.local` 改了要重 build
`NEXT_PUBLIC_API_BASE` 构建期内联，改了要重 build / 重启 dev。

### 无头截图自检法
dev 服务器在无头浏览器里因 HMR 握手失败**不 hydrate**、截不到数据态。要截带数据的页面需 **prod build + 隔离后端（改 `FLOOR_API_CORS` 放行测试端口）+ Node（v22+ 内置 WebSocket）走 CDP 真等几秒再 captureScreenshot**。隔离后端与正式实例**共享 `.queue_state.json`**，测试时**只读、别触发清除/删除**，免得误删真实任务。

---

## 通用心法

- **本机自用工具校准**：拒绝把通用 Web 规范硬套上来，只做真有价值的。→ [[mental-model]]。
- **诚实标注验证边界**：区分「已自动验证」与「待真机人眼确认」；地板保真长期人工确认。
- **不 push cutover**：退役老代码要等新代码完全 parity、老代码在生产链路已死，才动手。

---

## 相关笔记
- [[index]] — 首页
- [[mental-model]] — 每条坑背后的故事
- [[architecture]] — 模块依赖与进程内状态
- [[bevel-saga]] — 保真调试的极致案例
