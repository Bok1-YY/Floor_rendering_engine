# Floor Engine 桌面可运行副本

这是基于 `433c16e2dab99c32c1f2b89c187f5b36481889a0` 持续维护的独立 runnable。它保留自己的 Python 环境、Node 依赖、模型资产和 `data/`，不依赖 handoff 开发目录。当前分支在原有可用性修复上，继续收录场景提示词、系统密钥环、计费安全重试、存储生命周期，以及全屋设计本地研究建模快线。

## 启动

双击桌面的 **Floor Engine - 可运行副本** 快捷方式，或直接双击本目录的 `start-windows.bat`。保留弹出的服务窗口；浏览器会自动打开 `http://127.0.0.1:7870/`，全屋设计入口为 `http://127.0.0.1:7870/design/`。

如果 7870 已有本副本服务，只刷新浏览器，不要重复启动。停止服务时在服务窗口按 `Ctrl+C`，或关闭该窗口。

## 当前全屋设计流程

1. 上传户型图片或 PDF。
2. 在原图上确认空间、主入口和且仅一条两点比例尺；比例尺输入实际毫米值。
3. 补充容易识别错误的门窗或固定结构锚点。
4. 让 Gemini 提议外轮廓、墙、门窗、空间和邻接图，再回答九个普通结构问题。
5. 严格结构合同通过后，直接生成 Blend、GLB、研究 IFC、正上方和两张轴测图。
6. 两张 2K 概念图可以并行生成，但只负责家具、材料和氛围，不能改变结构。

本地研究灰模需要 Blender 5.2。程序会读取 `BLENDER_EXECUTABLE`、PATH 和 Blender 标准 Windows 安装路径；IfcOpenShell 由 `requirements.txt` 安装。缺少 Blender 时，其他 Floor Engine 功能仍可用，模型任务会明确显示缺少本地依赖。

## 保留的 Runnable 修复

- 项目列表会先加载详情，避免缺少 `plan_summary` 导致页面崩溃。
- 全屋页面拥有独立纵向滚动容器。
- 两张 2K 候选的文本状态按 `candidate_id` 隔离。
- 2K 候选支持全屏放大、滚轮缩放和拖动。
- API Key 保存到当前用户系统密钥环，不写入 `engine_config.json`。
- 不确定是否扣费的请求不会自动重复提交。
- 存储清理先审计、备份或隔离，不直接永久删除共享资产。

## 2026-08-31 验证

- Python 全量：`315 passed, 1 skipped`。
- 前端：design `9/9`、scene `3/3`、storage `2/2`、security `3/3`。
- ESLint 与 Next.js production build 通过，生成 7 个页面路由。
- 源码服务在独立端口 7898 启动，health、`/design/`、OpenAPI、户型上传、项目创建和项目详情均返回 200。
- 使用 1308 户型图创建了真实临时项目，正确进入“等待人工锚点确认”。
- 产品适配器实际调用 Blender 5.2 与 IfcOpenShell；Blend 冷开、GLB 冷导入、IFC4 回读及三张结构视图通过。
- 没有触发 Gemini/Fal 付费生图。

日常 commit/push 不再运行 Nuitka onefile；只有明确要求发布 exe 时才执行打包。此前中断的 `.buildenv`、`.nuitka_stage` 和 `dist` 已移入 Windows 回收站，`web/out` 作为正常 runnable 前端保留。

## 能力边界

当前输出是非商业研究和流程验证用途的结构灰模，不是施工图或施工级 BIM。1308 与 121㎡复杂样本仍需继续通过最终 Goal 0；两轮自动纠错、逐顶点机械评分和复杂墙体 junction 也仍是后续门槛。Gemini 不可用时，本地产物会保留并显示“等待 Gemini 复审”，不会伪装成正式通过。

机器可读验证记录见 `RUNNABLE_BASELINE.json`。
