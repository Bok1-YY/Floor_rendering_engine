# Floor Rendering Engine 开发指南

本指南描述当前源码与维护边界，更新于 2026-09-13。版本读取 [VERSION](./VERSION)。产品介绍见 [README](./README.md)，发布流程见 [Windows 发布说明](./docs/WINDOWS_RELEASE.md)，历史改动见 [验证索引](./docs/VALIDATION_CURRENT.md)。

## 1. 环境与启动

验证使用 Windows x64、Python 3.12，前端要求 Node.js 20.9+。Python 依赖见 [requirements.txt](./requirements.txt)，测试依赖见 [requirements-dev.txt](./requirements-dev.txt)。Blender 5.2 为本地研究建模的外部依赖；IfcOpenShell 随 Python 依赖安装。没有模型 API Key 仍可打开界面和使用本地功能。

Windows 安装当前仓库及开发测试依赖：

```powershell
.\Install_Project_Dependencies.bat -Development
.\start-windows.bat
```

安装脚本只操作所在仓库，不再安装其他项目。要求事先安装 Python 3.12 和 Node；开发模式额外安装 Chromium 测试浏览器。`start-windows.bat` 检查静态站是否缺失或过期，必要时构建；`dev-windows.bat` 开启 FastAPI 7870 和 Next dev 3000。两种模式都将数据放在当前仓库的 data 目录。

手动安装与源码运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm --prefix web ci
npm --prefix web run build
.\.venv\Scripts\python.exe serve.py
```

Linux/macOS 可使用对应的 venv/bin/python 和 npm 命令；本轮发布验证不覆盖这两个系统。前端开发在 web 目录执行 npm run dev；其开发 API 地址由 web 下的环境文件提供。静态发布使用同源 API，**不使用 next start 或 Vercel 服务端部署模式**。

| 设置 | 含义 |
|---|---|
| FLOOR_API_PORT | 服务端口，默认 7870 |
| FLOOR_API_HOST | 仅本机监听，默认 127.0.0.1 |
| FLOOR_NO_BROWSER=1 | 不自动打开浏览器 |
| FLOOR_DATA_DIR | 显式数据根目录；隔离测试必须指定 |
| FLOOR_API_CORS | 开发跨域来源；默认 localhost:3000 与 127.0.0.1:3000 |
| BLENDER_EXECUTABLE | 外部 Blender 可执行文件路径 |

启动后检查 `/api/healthz`。终止源码服务使用 Ctrl+C。应用会等待后台收尾，但已进入底层阻塞网络调用的线程可能继续等待超时；不能将收尾等待期限当成进程退出硬保证。

## 2. 架构与模块地图

```text
Next.js 静态前端 / HTTP + SSE
  -> server_api.py 组装、生命周期、静态资源与同源保护
  -> routes_* HTTP 输入校验与响应
  -> job_service / whole_home_design / 图像领域服务
  -> providers / 本地计算 / Blender 外部进程
  -> records / result_commits / usage_stats
```

| 维护内容 | 实际位置与职责 |
|---|---|
| 服务入口 | `serve.py` 注册历史包名；不依赖检出目录叫 Floor_engine_server |
| 应用组装 | `server_api.py`，七组路由注册、lifespan、静态前端与缩略图 |
| HTTP 契约 | `routes_jobs.py`、`routes_previews.py`、`routes_library.py`、`routes_config.py`、`routes_tools.py`、`routes_inpaint.py`、`routes_whole_home_design.py`；请求模型在 `server_schemas.py` |
| 主生成编排 | `job_service.py`；routes_jobs 不再持有全部后台生成实现 |
| Provider 实现 | `providers/` 按 Gemini、Fal、ComfyUI、修补、分析、诊断与调度分开；`api.py` 为兼容导出入口，旧 legacy 文件不再是实现主体 |
| 生命周期和共享状态 | `server_state.py`、`task_registry.py`、`models.py`；主状态通过规定锁与更新入口修改 |
| 付费阶段与计数 | `billing_phases.py`、`usage_stats.py`，SD 与超分分别记录风险与请求身份 |
| 本地写入恢复 | `result_commits.py`，图片、记录、任务快照的可恢复分步提交，不是数据库事务 |
| 记录与导出 | `records.py`、`exports.py`、`custom_recipes.py`、`reveal_security.py` |
| 提示词与素材 | `prompt_data.py`、`prompts.py`、`sd_prompts.py`、`image_prep.py`、`cinematic_planner.py` |
| 本地图像处理 | `color_match.py`、`floor_segmentation.py`、`floor_renderer.py`、`image_ops.py` |
| 研究建模 | `whole_home_design.py` 编排；`design_store.py`、`design_schema.py`、`design_images.py`、`design_provider.py`、`design_structure.py`、`design_modeling.py`、`design_exports.py` 分工；`tools/fastloop_research/` 执行建模与校验 |

引擎保持无 UI 框架依赖。NiceGUI 已退役，不应将仓库外的旧原型当作本项目可用的兜底环境。

## 3. 数据、持久化与恢复

`runtime_paths.py` 是数据根解析入口：源码默认项目下 data，冻结程序默认可执行文件旁；FLOOR_DATA_DIR 可覆盖。资源读取使用打包资源路径，运行数据不得写回临时解包目录。

| 相对数据根 | 内容 |
|---|---|
| output_files | 图片、记录 JSON、任务状态、研究模型与导出内容 |
| output_files/.queue_state.json | 活动任务及最多 60 条终态历史的持久化快照 |
| output_files/.result_commits | 本地分步提交日志与恢复回执 |
| output_files/_samples | 内容寻址的记录小样 |
| output_files/_whole_home_design | 全屋项目、素材、模型运行与导出 |
| engine_config.json | 线路、网络、输出等非敏感配置 |
| custom_recipes.json | 自定义配方，位于数据根，不是固定上级仓库 |
| _ng_uploads、_ng_thumbs | 上传素材与缩略图 |
| storage_backups、storage_quarantine | 清理前备份和可恢复隔离 |
| app_local_save.log | 本地日志 |

API Key 优先读取环境配置或当前用户系统密钥环，不写入配置 JSON。更换机器或用户后重新配置；测试不调用真实密钥，也不清空用户密钥环。

主生成最多 60 个活动任务。预览和本地建模使用各自有限容量，不能因后台队列无限增长而持续产生请求。队列快照深复制并原子写入，写入失败可观察；不能只以文件名存在判断持久化成功。

SD 与超分各自保存请求身份、重试风险和不确定计数。有效 Fal 请求恢复走已有句柄；未知结果不自动提交新付费请求。本地补写通过 GET /api/result-commits 与 POST /api/result-commits/{commit_id}/retry，只补写已保存结果。持续磁盘故障仍会阻止恢复，未落盘图片不能凭日志还原。

任务卡或记录已主动删除时，不擅自恢复它们。回执和请求对账索引随历史增长；归档与保留策略仍是后续工作。严格跨刷新/重启的创建请求幂等协议尚未实现。

## 4. 后端修改约定

- 服务必须单 worker；进程内注册表、信号量与锁不提供多 worker 或多用户隔离。
- HTTP 路由负责输入、目标归属、权限边界与响应。重处理进入服务模块，由后台任务管理器拥有生命周期。
- 网络或 Blender 等外部操作不在全局状态锁内等待；保存时复核操作 ID、revision 与输入 hash，拒绝过期结果。
- 全屋分析中九问答案、独立问题来源和结构合同保持可追溯；本地模型外部进程由任务拥有并支持取消。
- 新接口同步 `server_schemas.py`、`web/src/lib/api.ts`、`web/src/lib/types.ts` 和 `tests/test_route_contract.py`。路由全集由契约测试确认，不在多个文档复制端点数量。
- 测试替身注入实际实现模块，不能继续针对已退役 legacy 别名打补丁。已有 helpers 位于 tests 下的 provider_fakes、job_fakes、design_fakes 模块。

## 5. 前端职责与数据流

实际前端技术版本以 [package.json](./web/package.json) 和锁文件为准；当前 Next.js 16 / React 19、Tailwind 4、Base UI。修改前端代码前遵守 [web/AGENTS.md](./web/AGENTS.md)，阅读已安装版本的相关 Next 文档。

| 区域 | 维护入口 |
|---|---|
| 生成工作台 | `web/src/components/generation/`：参数与草稿、识色、配方、正式提交、快速预览、任务列表分别管理 |
| 校色 | `web/src/components/color-match/`：状态、预览请求、图片解码、画布、局部蒙版与展示分别管理 |
| 修补 | `web/src/components/inpaint/`：参数、蒙版、智能选区、任务阶段、会话组装 |
| 记录库 | `web/src/components/records/`：目录/内容读取、固定目标操作、会话草稿、筛选和展示 |
| 任务卡 | `web/src/components/job-card/`：任务快照、候选缓存、结果定位、评审、付费动作与弹窗 |
| 状态和资源工具 | `web/src/lib/editor/`：reducer 快照与 AsyncScope；DOM、图片、取消句柄不另存为业务状态镜像 |
| 结果身份 | `web/src/lib/results/`：文件/记录/结果组成的稳定身份、唯一定位与操作锁 |
| 设计系统 | `web/src/app/globals.css`、`web/src/components/AppShell.tsx`、`web/src/components/dc-ui.tsx` 和 ui 基础组件；不依赖仓库外设计文件 |

生成参数与草稿只使用一份规范快照。历史复用优先于普通草稿，默认值补齐；初始化期间已编辑字段保留。提交使用点击时的独立快照。批量成功项移出选择，未确认项再次提交前提示核对任务，不自动重试。

SSE、父级列表及任务动作回执统一进入卡片快照处理器，带时间戳的旧快照不能覆盖新值。候选读取每张卡片最多 4 个在途请求，同请求合并、模型切回复用、候选增加补缺项。评审先按所属记录内完整路径匹配，旧文件名回退必须唯一。

记录库切换文件立即移除旧内容。操作捕获完整目标，旧回执不能重新选择旧文件。评审与二改草稿按目标保留于当前页面会话，刷新不保证恢复；失败保留输入，成功回执不清除新编辑。解密密码和文本关闭即清除。

图像编辑器身份包括操作目标与源图。关闭、目标变更和新参数使旧请求/解码失效；修补的迟到创建 ID 补发取消。本地补写恢复不重新生成。前端取消不能保证已经开始的云模型调用不计费。

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


## 6. 完整验证

```powershell
.\.venv\Scripts\python.exe tools/verify.py --integration
.\.venv\Scripts\python.exe tools/check_docs.py
```

默认 tools/verify.py 不运行 integration 标记项；--integration 包含真实本地 Blender/IFC；--backend-only 仅执行后端。测试浏览器首次安装可在 web 下执行 npx playwright install chromium。

前端 npm run check 包含 TypeScript、ESLint、Node 测试、静态构建和 Playwright。浏览器测试用真实静态前端与 Python 托管，接口由替身响应；后端测试禁止未替换的 Requests 网络调用，使用隔离数据根。不要让测试服务共享真实 data 或任务队列。

测试计数与日期见 [当前验证索引](./docs/VALIDATION_CURRENT.md)。测试通过不等于验证云模型质量、实际账单、全新 Windows 系统或全部客户素材。当前仍缺少一项操作员彩膜回归素材。

## 7. 新功能与修复流程

1. 根据模块职责选择实际实现位置；新增 provider 逻辑不塞回 api 兼容入口。
2. 确认 API、目标身份、失败与恢复行为。涉及可能重复计费时保留明确确认。
3. 前端通过所属状态和动作模块修改，不让展示层直接发起业务请求，不复制另一份参数 ref。
4. 优先添加能复现行为的测试，不以源码字符串匹配替代实际执行验证。
5. 运行相关检查；发布前运行完整验证。更新主文档和必要的历史说明，不只增加重构日志。
6. 提交范围清晰、可回退的变更；只有明确发布任务才执行完整可执行文件构建。

## 8. 退役内容与剩余边界

NiceGUI 与原 webui 已退役；`__main__.py` 为退役提示入口。旧 360° ERP 仅只读，不恢复新建、修补或球面地板生成。

仍需按需推进：持久化创建请求幂等、回执归档、阻塞网络退出、Windows Proactor 断连清理日志、大记录集性能与其他大模块审阅。未提供多租户、公网认证、多 worker 或跨客户端即时一致性。

## 9. 构建与正式发布

参见 [Windows 发布说明](./docs/WINDOWS_RELEASE.md)。`build_windows.bat` 调用 `tools/build_windows.py`，从干净 Git 提交建立独立源码快照、构建环境、静态前端与 Nuitka onefile，版本来自 VERSION。Blender 的单独解释器使用打包的审计源码资源；不尝试导入 Nuitka 编译模块。

发布需要验证同一份最终可执行文件、生成 SHA-256、上传完整附件并重新下载核验。构建中间文件仅在本轮标记工作目录内，不能误删正常环境或运行数据。正式发布是否已完成、包体与测试详情，以对应 Release 附件为准。

## 10. 全屋设计合同

户型图/PDF、人工空间与入口、唯一两点比例尺、九问及结构合同共同确定墙体权威。概念草稿仅描述家具、材料与氛围；固定两张 B2/2K，不把概念图当结构真值。

通过合同后生成 Blend、GLB、研究 IFC 与视图。机械验证、Blender 冷开/导入与 IFC 回读由本地流程执行；Gemini 外部复审不可用时保留产物并标 external_review_pending。模型是研究灰模，不是施工级 BIM；不能将本机机械通过等同于外部专业审查通过。
