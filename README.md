# Floor Rendering Engine

**Release:** 2026.08

把一块真实地板色板，转译成可批量生成、可锁定产品颜色、可局部修补、可评审交付的空间内容。

这不是给图片套一个“家居效果图”模板。它关心的是：产品色是否准确，地板在画面里占多少，空间是否可信，模型失败后怎么修，以及团队最终能不能直接把结果用于社媒生产。

> 不是让 AI 多生成几张，而是让真实产品可靠地走到可发布内容。

[English](./README.en.md) · [产品案例](./docs/PRODUCT_CASE_STUDY.zh-CN.md) · [在线演示](https://bokiframe.com) · [开发手册](./DEVGUIDE.md)

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

- 从色板自动识别色调，并给出适合地板营销的场景配方；
- 批量生成多房间或多块地板，多模型使用独立并发槽；
- 打开“电影真实感”后，在 B2 / Pro 生图前规划可信机位、动作、视线关系和现实光源；
- 用双端滑块控制地板约占画面的 **10–80%**，默认 **40–50%**，松手后才提交参数；
- 用本机 MobileSAM 找地板、物件和承载区域，再由画笔精修；
- 在记录页完成通过、备选、淘汰、收藏、问题标注和 HTML / PPTX 导出。

## Product Gallery

### Swatch → planning → production

生成页不是一个巨大的提示词输入框。色板、工作流、地域、房间、材质、镜头、电影真实感和地板面积都被组织成可复用参数；右侧任务区同时展示多模型候选与实时状态。

![从色板规划到批量生成、校色和智能修补](./docs/media/feature-tour.webp)

### Color-locked output

生成模型经常把地板颜色带偏。系统先分割地板，再在 LAB 色彩空间内把结果拉回色板目标；修改限制在蒙版内，尽量保留墙面、家具和环境光。

![API 原图与自动校色结果对比](./docs/media/quality-before-after.webp)

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
        |-- MobileSAM / OpenCV / LAB
        `-- records / review / usage / export
```

- B2、Pro 与 SD 使用独立并发槽；服务默认只监听 `127.0.0.1`。
- 配置、输出、日志和任务恢复状态保存在本机数据目录。
- 当前回归集覆盖提示词黄金样本、路由契约、路径安全、队列恢复、电影规划、地板占比、校色、智能选区和记录链路；本次发布为 **211 tests passed**。

## My Role

我独立负责核心产品从问题定义到工程落地：

- 观察社媒生产流程，把“生成好看图片”改写为效率、通过率、品牌色一致性和可交付性；
- 设计色板、场景、生成、校色、修补、评审和导出的完整用户旅程；
- 选择并整合 Gemini、Fal、MobileSAM、OpenCV 与本地/云端降级路径；
- 实现 FastAPI + Next.js 应用、任务编排、持久化、用量统计和 Windows 一键启动；
- 根据真实用户反馈做浏览器真机复现、并发锁分析和回归验证。

更完整的背景、取舍、指标口径和职责边界见[产品案例](./docs/PRODUCT_CASE_STUDY.zh-CN.md)。

## Quick Start

要求：Python 3.10+、Node.js 20+，并至少配置一个可用图像模型 API。MobileSAM 模型资产已包含在仓库中。

### Windows

```text
Install_Project_Dependencies.bat   # 首次安装
start-windows.bat                  # http://127.0.0.1:7870
dev-windows.bat                    # FastAPI 7870 + Next.js 3000
```

`start-windows.bat` 会检查前端源码是否比 `web/out` 更新；只有产物缺失或过期时才重新构建，避免启动旧界面，也避免每次重复安装依赖。

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd web && npm ci && npm run build && cd ..
python serve.py
```

首次启动后在“设置”页填写 API Key。开发端口、配置和模块说明见 [DEVGUIDE.md](./DEVGUIDE.md)。

## Repository Guide

- [产品案例（中文）](./docs/PRODUCT_CASE_STUDY.zh-CN.md) / [Product case study (English)](./docs/PRODUCT_CASE_STUDY.en.md)
- [开发手册](./DEVGUIDE.md)
- [开发日志](./开发日志.md)
- [SaaS 架构路线](./SAAS_ARCHITECTURE.md)
- [第三方组件与模型声明](./THIRD_PARTY_NOTICES.md)
- [商业授权说明](./COMMERCIAL_LICENSING.md)

## Security And License

`engine_config.json`、`data/`、输出图和日志均被忽略。若改造成多人或公网服务，需要先补充身份认证、租户隔离、对象存储和生产级任务调度。

原创代码采用 [GNU AGPL-3.0-only](./LICENSE)。闭源商业使用请参考[商业授权说明](./COMMERCIAL_LICENSING.md)；第三方模型和组件遵循各自许可证。

## Design Principle

真正可用的 AI 产品不靠一次惊艳结果成立。

它需要把正确的产品约束放在生成之前，把人的判断保留在生成之后。
