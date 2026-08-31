# Floor Engine 代码审计报告（2026-08-28）

## 结论

本次以当前工作树为运行态、以 `6058da5` 为提交基线，审查全部第一方 Python、TypeScript/TSX、启动脚本、API 契约、测试与依赖清单。第三方源码、虚拟环境、`node_modules`、静态构建产物、生成图片和缓存不作为代码审查对象。

- 未发现可直接远程利用的 P0：服务默认仅绑定环回地址，上传和输出路由均有目录边界及格式验证，也未使用 `eval`、`exec`、`shell=True` 或不安全反序列化。
- 两轮已整改全部确认的 P1/P2：除存储/PDF/依赖问题外，API Key 已迁入系统密钥环、付费断流改为人工确认、孤儿文件进入30天隔离、队列升级为 model_runs-only v3。
- 当前自动化门禁：后端 291 个测试（其中 1 个按环境跳过，`-W error` 零警告）、前端 15 个行为测试、ESLint、TypeScript、Next.js 静态生产构建及 `pip check`。`npm audit` 为 0。
- 当前未提交的场景上下文改动与存储缺陷无因果关系；存储问题来自既有基线。现有用户改动已保留。

## 审计覆盖

| 子系统 | 主要代码 | 审查重点 | 结果 |
|---|---|---|---|
| 启动与配置 | `serve.py`、`config.py`、`logging_setup.py`、BAT 脚本 | 数据根目录、冻结运行、密钥、原子配置写入 | 数据根目录统一；密钥进入系统 keyring |
| 记录与文件 | `records.py`、`exports.py`、`routes_library.py` | 记录锁、迁移、引用、删除、导出、备份 | 重复与物理删除语义已整改 |
| 任务编排 | `routes_jobs.py`、`server_state.py`、`task_registry.py`、`models.py` | 并发、取消、重试、重启恢复、候选一致性 | 任务卡清理语义明确；删除结果会同步修正任务候选 |
| 模型调用 | `api.py`、`providers/`、`failure_kb.py` | 超时、下载限制、TLS、重试、密钥脱敏、计费 | safe/ambiguous/fatal 分级，提交后断流不自动重提 |
| 图像处理 | `image_prep.py`、`image_ops.py`、`color_match.py`、地板分割/渲染模块 | 像素边界、文件句柄、色彩空间、临时文件 | PyMuPDF 1.28.2 正式导入；warnings-as-errors 通过 |
| 修补与工具 | `routes_inpaint.py`、`routes_tools.py` | 路径、mask 大小、临时候选、引擎切换 | 临时候选有容量和回收机制；未发现越界删除 |
| 全屋设计 | `whole_home_design.py`、`routes_whole_home_design.py` | PDF、图片派生、付费预览/提交、结构审核、bundle | 新增 PDF 页面/总像素上限和失败回滚 |
| HTTP 安全 | `server_api.py`、`server_helpers.py`、`server_schemas.py` | CORS、同源、路径穿越、输入上限、静态文件 | 本机部署模型下总体合理；补充配置字段长度限制 |
| 前端 | `web/src` 全部页面、组件、hooks、API 和类型 | API 契约、SSE/轮询、删除反馈、草稿、构建 | 存储 UI 已接入；构建根目录警告已消除 |
| 测试与构建 | `tests`、`web/tests`、依赖锁文件 | 契约、回归、依赖、静态导出 | 新增存储并发/回滚/删除/PDF 测试；依赖审计为 0 |

## 已整改发现

### STOR-001 · P1 · 同一小样被逐记录复制

证据：36 个 `*_sample_*` 文件只有 2 个 SHA-256；其中一组 31 份、另一组 5 份。根因是新记录内联 `sample_image_b64`，随后迁移按记录生成随机文件名。

整改：新记录直接写 `_samples/<sha256>.jpg`；历史 Base64 和旧文件迁移复用同一内容寻址入口；并发写使用原子临时文件、哈希验证和进程锁。

### STOR-002 · P1 · 删除只删 JSON 引用

证据：旧 `delete_result_image`/`delete_record_entry` 只执行 `pop`/过滤后保存 JSON，未检查或删除磁盘文件。

整改：删除后重建全库引用；无引用才物理删除原图和对应缩略图，共享文件保留；任务卡中的失效候选同步移除。任务卡“清除已完成”仍只清 UI 状态，文案明确不会删图片。

### STOR-003 · P2 · 缩略图重复且无法按源回收

证据：248 个缓存只有 133 种内容；旧 key 把完整路径、mtime、尺寸整体 MD5，无法按源路径枚举清理。

整改：v2 key 分离源路径摘要、源版本和尺寸；源更新/删除可清掉对应缓存；存储维护可安全清空全部可再生缓存。

### PATH-001 · P1 · 启动方式导致数据目录分裂

证据：BAT 设置 `FLOOR_DATA_DIR=<project>/data`，直接运行源码则取项目上级目录，实际产生过桌面 `output_files`。

整改：新增统一运行时路径模块；源码默认 `<project>/data`，环境变量显式覆盖，冻结包仍使用可执行文件目录；启动日志输出解析后的数据目录。

### API-001 · P2 · GET `/api/records/load` 隐式迁移写盘

证据：读取记录时调用 `migrate_record_file`，任务卡为读取评审状态也会触发文件生成。

整改：GET 恢复为只读；旧记录文件迁移放到启动生命周期或显式存储维护操作。新记录不再产生内联小样。

### PDF-001 · P1 · PDF 解码资源和失败残留

证据：上传只限制 50 MiB/60 页，未限制渲染像素；解析或中途失败会遗留 PDF/已生成页面，Windows 下打开文件还会阻止删除。

整改：增加单页 4,000 万像素、总计 2.4 亿像素上限；错误路径先关闭 PyMuPDF document，再删除源 PDF 和所有已派生页面。

### DEP-001 · P1 · 前端已知依赖漏洞

证据：初始 `npm audit` 为 11 项（9 high、2 moderate），部分来自 Next.js，部分经 `shadcn` 构建链引入。

整改：Next.js/`eslint-config-next` 升到 16.3.3，`shadcn` 升到 4.19，随后使用非强制 `npm audit fix` 更新可兼容传递依赖；最终为 0 项。构建与 lint 已复验。

### BUILD-001 · P2 · Turbopack workspace 根目录漂移

证据：Next.js 因用户目录另有 `package-lock.json`，曾把 `C:\Users\1_1` 判作 workspace root。

整改：按项目本地 Next.js 16.2/16.3 文档在 `next.config.ts` 显式设置 `turbopack.root`；构建警告消失。

## 真实数据迁移结果

- 记录文件：2 个；记录条目：36 条；结果引用：130 条。
- 小样引用：36 条，迁移后只指向 2 个内容寻址文件。
- 旧时间戳小样：36 个已删除；活跃小样文件从 36 降到 2，净减少 34 个。
- 缩略图缓存：248 个已清空，后续按需重建。
- 净释放：9,586,662 字节（约 9.14 MiB）。
- 缺失引用：0；结果引用变更：0；规范文件名和文件 SHA-256 不一致：0。
- 备份与映射清单：`data/storage_backups/20260828_143812_807922800/manifest.json`。
- 一张约 23.47 MiB 的旧优化图已移入可恢复隔离区；2026-09-27 前可恢复，到期后仍需人工确认才会永久删除。

## 第二轮整改状态

### SEC-001 · P1 · API Key 明文存储 — 已整改

Gemini/Fal Key 已迁入 Windows Credential Locker，`engine_config.json` 不再含三种密钥字段。运行时优先读取环境变量，其次系统 keyring；安全 backend 不可用时禁止新写明文。DeepSeek 原本未配置。

### COST-001 · P1 · 提交后断流可能重复计费 — 已整改

Google/Fal 付费请求现在区分 safe/ambiguous/fatal。提交后读超时、断流、协议错误和看门狗中止不再自动重提或自动 failover；任务卡要求用户确认“可能重复计费”，用量页显示成本上下限。

### ORPHAN-001 · P2 · 未引用优化图 — 已隔离

该文件已按新鲜快照、全库引用和 SHA-256 验证后移入 `storage_quarantine`。30天内可恢复；到期也不会自动删除。

### MAINT-001 · P2 · 超大模块与兼容双写 — 第一阶段完成

已建立 provider/job 边界、存储/色彩/修补领域模块和前端 hooks；队列持久化升为 schema v3，只保存 `model_runs`，旧 B2/Pro 字段由兼容视图派生。大型 UI 后续继续按行为不变原则拆视觉子组件，不再承担状态真相。

### WARN-001 · P3 · SWIG/PyMuPDF 弃用警告 — 已整改

PyMuPDF 已从 1.26.4 升至 1.28.2，并从 legacy `fitz` 改为正式 `pymupdf` import。全量测试使用 `-W error` 通过，没有增加 warning 过滤。

## 验收命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd web
npm.cmd run test:design
npm.cmd run test:scene
npm.cmd run test:storage
npm.cmd run test:security
npm.cmd run lint
npm.cmd run build
npm.cmd audit --omit=dev --audit-level=moderate
```

任何后续存储变更都必须继续满足：扫描只读、清理要求新鲜快照、活跃任务时拒绝、记录先备份和验证、文件最后删除、重复执行零变更。
