# Floor Rendering Engine

![Release](https://img.shields.io/badge/release-2026.08-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![Node](https://img.shields.io/badge/node-20%2B-339933)
![Tests](https://img.shields.io/badge/tests-315%20passed-brightgreen)

把一块真实地板色板，转译成可批量生成、可锁定产品颜色、可局部修补、可评审交付的空间内容。

这不是给图片套一个“家居效果图”模板。它关心的是：产品色是否准确，地板在画面里占多少，空间是否可信，模型失败后怎么修，以及团队最终能不能直接把结果用于社媒生产。

> 不是让 AI 多生成几张，而是让真实产品可靠地走到可发布内容。

[English](./README.en.md) · [产品案例](./docs/PRODUCT_CASE_STUDY.zh-CN.md) · [在线演示](https://www.bokiframe.com) · [开发手册](./DEVGUIDE.md)

![从色板到批量生成、自动校色与智能修补的完整工作流](./docs/media/hero-demo.gif)

## 2026.08 最新更新

- **实验性全屋设计加入本地研究建模快线**：上传户型图片或 PDF，先用人工空间、入口和唯一两点比例尺约束 Gemini，再回答九个普通结构问题。通过严格 wall/opening/adjacency 合同后，产品可直接调用 Blender 5.2 和 IfcOpenShell 输出 Blend、GLB、研究 IFC 与三视图；2K 概念图并行生成且永远不是墙体权威。Gemini 线路不可用时状态为 `external_review_pending`，不会冒充正式 BIM 或计作产品失败。
- **生成工作台重构**：用“产品 → 场景 → 输出”三步手风琴替代长参数列，常用的房间、风格、光线和镜头保持在核心层；结果区新增 B2 / Pro 页签、候选缩略条，并可直接完成通过、备选、淘汰和收藏。
- **对色算法 2.0 + 独立样品对色工具**：不用启动 Floor Engine，也不调用网络或 AI 接口，就能把新拍大样对齐历史小样。除经典 LAB 统计校色外，新增可靠像素筛选、精细颜色分布迁移、可选空间光照校正，以及带四色诊断图的 0–100 可信度报告。Floor Engine 首次预览默认精细 2.0，顶部常驻显示 1.0/2.0 选择和画布实际生效版本；独立工具与旧 API 继续保持经典模式默认，兼顾质量与兼容性。

> **让不同时间拍摄的新旧样品图回到同一产品色基准。** Windows 可直接双击启动，Linux 也可独立运行：[立即使用独立样品对色工具](./standalone_color_calibrator/README.md)。

## Why

旧流程并不只是“写一句提示词”。社媒人员需要理解色板、想象空间、选择机位、反复调整提示词、从大量候选里挑图，再处理偏色和局部瑕疵。

一组 5 张图通常需要约 1 小时；可用率大约是 10 张选 1 张。真正消耗时间的不是 API 请求本身，而是请求前后的判断、试错和返工。

Floor Rendering Engine 把这套隐性的人工流程改成一组可执行、可回退的产品规则：

```text
色板识别
-> 场景与电影真实感规划
-> 多模型批量生成
-> 地板分割与自动校色
-> AI 选区与生成式修补
-> 评审、复盘与交付
```

## What It Does

一次正式任务可以同时运行 B2、Pro 与 SD 路线，保留 API 原图和后处理候选，并把场景参数、模型、耗时、评审状态和问题标签写入同一条记录。

当前生成工作台支持：

- 在“全屋设计”中完成户型 PDF 选页、人工锚点/比例尺、九问结构确认、本地 Blender/GLB/研究 IFC，以及并行的两张 2K 概念草稿；
- 用“产品 → 场景 → 输出”三步手风琴组织任务，核心参数常驻，其余参数按需展开；
- 从色板自动识别色调，并给出适合地板营销的场景配方；
- 批量生成多房间或多块地板，多模型使用独立并发槽；
- 打开“电影真实感”后，在 B2 / Pro 生图前规划可信机位、动作、视线关系和现实光源；
- 用双端滑块控制地板约占画面的 **10–80%**，默认 **40–50%**，松手后才提交参数；
- 用本机 MobileSAM 找地板、物件和承载区域，再由画笔精修；
- 用离线独立对色工具把新拍大面积样品图对齐历史小样，保留分辨率、纹理与光影；
- 在生成结果卡直接切换 B2 / Pro 与候选图，并完成通过、备选、淘汰和收藏；记录页继续负责问题标注与 HTML / PPTX 导出。

## Product Gallery

### Swatch → planning → production

生成页不是一个巨大的提示词输入框。色板、场景和输出被组织成三步手风琴，房间、风格、光线、镜头保持在核心层，地域、材质和地板面积按需展开；右侧以模型页签和候选缩略条承载实时结果与评审。

![从色板规划到批量生成、校色和智能修补](./docs/media/feature-tour.webp)

### Color-locked output

生成模型经常把地板颜色带偏。系统先分割地板，再在 LAB 色彩空间内把结果拉回色板目标；修改限制在蒙版内，尽量保留墙面、家具和环境光。

![API 原图与自动校色结果对比](./docs/media/quality-before-after.webp)

### 新拍大样，也能对齐历史小样

仓库同时提供一个可脱离主系统运行的[独立样品对色工具](./standalone_color_calibrator/README.md)。它在新拍大图中框选材料区域，以旧小样为参考，把确定性的校正应用到整张高分辨率图片。默认经典模式仅迁移 LAB 色彩通道，木纹、压纹、倒角、受光和阴影仍来自新照片；需要处理复杂、多峰颜色分布时可切换精细模式，需要消除大面积渐变偏色时可开启空间光照校正。

分析时会自动排除反光、过曝/欠曝、深阴影和异色离群点，并返回 0–100 可信度、CIEDE2000 预计色差、色域风险和四色诊断图。低可信度只警告、不阻止导出。这让设计、样册、电商和社媒团队无需启动整套生图服务，也能快速得到产品色更一致的素材底图。全程本地处理，不上传图片，不产生模型 API 费用。

### Click an object, then refine the mask

移除模式会后台扫描可分割物件；添加模式会根据点击位置识别地面、墙面或桌面等承载区域。AI 选区、画笔补选和橡皮排除最终合成为同一张可编辑蒙版。

![AI 智能选区从候选识别到选中物件](./docs/media/smart-mask.gif)

### Review is part of generation

结果不会散落在下载目录里。原图、校色图、修补图和不同模型候选保留在同一条记录中，团队可以筛选房间、标记最佳图、复盘失败原因并导出提案。

![记录、评审与导出工作区](./docs/media/product-overview.webp)

## Core Product Rules

### 1. Product color is a constraint

地板不是场景里的普通材质。提示词、参考图、自动校色和局部编辑都围绕色板保真工作；后处理只允许修改明确的地板区域。

### 2. Composition is controllable

地板占比不再绑死在提示词中。`floor_coverage_min` / `floor_coverage_max` 从前端双滑块贯穿 Gemini、电影规划和 SD 提示词管线；后端校验范围并保证最小值不大于最大值。

电影真实感也不是简单追加 `cinematic`。系统先判断机位、人物或宠物动作、视线和实景光源，规划失败时使用本地保底方向，不阻断正式生图。

### 3. AI proposes; the user decides

MobileSAM 可以给出地板蒙版、物件候选和单点区域，但不会替用户做不可逆决定。画笔、橡皮、撤销和清空始终保留；模型不可用时仍能继续手绘。

### 4. Batch is the default unit

真实团队需要的是一批可选结果，而不是一次漂亮调用。多模型队列、批量房间、多色板、取消、重试、重抽和失败恢复都属于主流程。

### 5. Every output remains traceable

API 原图、自动校色、局部修补、生成参数、模型路线、耗时和人工评审共同构成结果链路。好图可以复用，失败也能沉淀成下一轮规则。

## Production Evidence

这些数据来自公司社媒组的实际使用，不是离线 benchmark：

- 约 **10 名**内部用户；
- 累计生成近 **2,000 张**图片；
- 旧流程约 **1 小时 / 5 张图**，现在可以直接批量提交，产能下限主要由上游 API 速度决定；
- 过去大约 **10 张选 1 张**，现在普通图片多数首轮可用，特殊图片平均约 **2 张内**得到可用结果；
- 综合 API 重试、人工构思、提示词和后处理时间，内部估算生产成本降低约 **80%**；
- 团队月度运营统计中，Pinterest 点赞用户到下单约 **4%**。

<sub>Pinterest 数据是团队整体运营观测，受内容、投放、产品和渠道等多因素影响，不归因于本系统单一因素。</sub>

<details>
<summary><strong>AI 智能选区如何工作</strong></summary>

1. **本机编码**：源图最长边缩到 1280 像素后进入 MobileSAM ONNX；同图 embedding 与最近 3 张整图扫描结果使用 LRU 缓存。分割过程不上传第三方。
2. **自动发现候选**：移除模式在 `8 × 6` 网格上做多尺度点提示，按置信度、稳定度和面积过滤，只保留采样点所在连通域，再按 IoU / 包含率去重，最多返回 24 个轮廓。
3. **点击优先**：未命中候选或扫描尚未完成时立即发起单点分割。完整扫描在网格点之间释放推理锁，使显式点击不必等待整轮扫描。
4. **可编辑合成**：AI 区域先求并集，再叠加画笔补选并减去橡皮排除，最后导出二值 PNG mask。推理蒙版与羽化合成蒙版分离，范围外保持原图。
5. **可控降级**：反射、透明或遮挡边缘识别不完整时，界面解释当前状态并继续允许手绘；智能选区不产生额外分割 API 费用。

实现细节见 [DEVGUIDE：生成式修补智能选区数据流](./DEVGUIDE.md#56-生成式修补智能选区数据流)。

</details>

## Engineering

```text
Next.js 16 / React 19
        | HTTP + SSE
FastAPI / task orchestration / queue recovery
        |-- Gemini / Fal / ComfyUI
        |-- whole-home anchors / structure contract / Blender + GLB + research IFC
        |-- MobileSAM / OpenCV / LAB
        `-- records / review / usage / export
```

- B2、Pro 与 SD 使用独立并发槽；服务默认只监听 `127.0.0.1`。
- 配置、输出、日志和任务恢复状态保存在本机数据目录。
- 当前回归集覆盖提示词黄金样本、路由契约、路径安全、队列恢复、系统密钥环、计费安全重试、存储生命周期、电影规划、全屋设计人工锚点/九问结构图/revision/Blender/GLB/IFC研究快线、地板占比、校色、独立样品对色、智能选区和记录链路；当前为 **315 tests passed，1 skipped**。

## My Role

我独立负责核心产品从问题定义到工程落地：

- 观察社媒生产流程，把“生成好看图片”改写为效率、通过率、品牌色一致性和可交付性；
- 设计色板、场景、生成、校色、修补、评审和导出的完整用户旅程；
- 选择并整合 Gemini、Fal、MobileSAM、OpenCV 与本地/云端降级路径；
- 实现 FastAPI + Next.js 应用、任务编排、持久化、用量统计和 Windows 一键启动；
- 根据真实用户反馈做浏览器真机复现、并发锁分析和回归验证。

更完整的背景、取舍、指标口径和职责边界见[产品案例](./docs/PRODUCT_CASE_STUDY.zh-CN.md)。

## Quick Start

要求：Python 3.10+、Node.js 20+，并至少配置一个可用图像模型 API——[Google AI Studio](https://aistudio.google.com/)（Gemini）或 [fal.ai](https://fal.ai/) 任一即可；也可连接自备的 ComfyUI 实例，本地算力零 API 费用。MobileSAM 模型资产已包含在仓库中。

### 只需要样品对色

Windows 双击 [`standalone_color_calibrator/启动校色工具.bat`](./standalone_color_calibrator/启动校色工具.bat)，依次载入新拍大图和旧小样即可。它只依赖 Pillow、NumPy 与 OpenCV，不需要 Node.js、API Key 或 Floor Engine 服务。命令行也可直接运行：

```powershell
python standalone_color_calibrator/app.py --source 新大图.jpg --reference 旧小样.jpg --output 对色结果.jpg
```

完整图形界面、框选建议和参数说明见[独立样品对色工具文档](./standalone_color_calibrator/README.md)。

### Windows

```text
Install_Project_Dependencies.bat   # 首次安装
start-windows.bat                  # http://127.0.0.1:7870
dev-windows.bat                    # FastAPI 7870 + Next.js 3000
```

`start-windows.bat` 会检查前端源码是否比 `web/out` 更新；只有产物缺失或过期时才重新构建，避免启动旧界面，也避免每次重复安装依赖。

全屋研究灰模需要本机安装 Blender 5.2；程序会依次读取 `BLENDER_EXECUTABLE`、系统 PATH 和 Blender 的标准 Windows 安装路径。IfcOpenShell 随 Python 依赖安装。未安装 Blender 时，普通效果图、记录、对色和 2K 全屋概念图仍可使用，但本地 Blend/GLB/IFC 会明确显示“缺少本地依赖”。

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd web && npm ci && npm run build && cd ..
python serve.py
```

首次启动后在“设置”页填写 API Key。Key 保存到当前用户的系统密钥环，不写入 `engine_config.json`；把程序复制到新电脑后需要重新填写。开发端口、配置和模块说明见 [DEVGUIDE.md](./DEVGUIDE.md)。

## Repository Guide

- [产品案例（中文）](./docs/PRODUCT_CASE_STUDY.zh-CN.md) / [Product case study (English)](./docs/PRODUCT_CASE_STUDY.en.md)
- [开发手册](./DEVGUIDE.md)
- [开发日志](./开发日志.md)
- [独立样品对色工具](./standalone_color_calibrator/README.md)
- [SaaS 架构路线](./SAAS_ARCHITECTURE.md)
- [第三方组件与模型声明](./THIRD_PARTY_NOTICES.md)
- [商业授权说明](./COMMERCIAL_LICENSING.md)

## Security And License

`engine_config.json`、`data/`、输出图和日志均被忽略。若改造成多人或公网服务，需要先补充身份认证、租户隔离、对象存储和生产级任务调度。

原创代码采用 [GNU AGPL-3.0-only](./LICENSE)。闭源商业使用请参考[商业授权说明](./COMMERCIAL_LICENSING.md)；第三方模型和组件遵循各自许可证。

## Design Principle

真正可用的 AI 产品不靠一次惊艳结果成立。

它需要把正确的产品约束放在生成之前，把人的判断保留在生成之后。
