# 定点球面全景：3ds Max 原理、Nano Banana / GPT Image 2 评估与项目落地方案

> 调研与项目审阅日期：2026-08-12  
> 项目现场：`Floor_engine_Linux`，HEAD `54c9e51` 加当前工作区  
> 目标：从整屋 CAD/灰模生成可在固定热点中任意转头查看的单目 360°×180° 球面全景，并尽量保证墙体、门窗、家具、材质、光线和相邻方向不漂移。

## 1. 先给结论

1. **3ds Max 的“定点球面全景”不是让六台相机自由拍摄再猜着拼。**它固定一个投影中心，从同一光心向完整球面发射射线，直接输出球面投影；或者从完全相同的 XYZ 渲染六个 90° 立方体面，再按已知投影关系转换。没有相机平移，就没有近远物体之间的拼接视差。
2. **提示词、seed、同一模型都不能保证 AI 生成的六张图几何一致。**Nano Banana 2、Nano Banana Pro、GPT Image 2 都是生成/编辑模型，不是带有刚性相机模型和跨视图对应约束的 3D 渲染器。官方能力再强，也不能把“请勿移动相机”变成数学保证。
3. **严格交付方案只有一条：完整 3D 场景是几何权威，AI 只负责低自由度外观。**家具/固定物、墙体、门窗和材质必须落回共享 3D 场景或共享表面纹理，再由同一场景确定性渲染最终 ERP。这样同一热点无论朝哪看，看到的都是同一球面上的确定像素；多个热点也共享同一套物体和材质。
4. **如果先做实用 MVP，GPT Image 2 比 Nano Banana 更适合直接处理整张球面图。**它的 Image API 允许符合约束的任意尺寸，`3840×1920` 是合法 2:1 输出；所有输入图会自动以高保真方式处理。它适合“整张 ERP 一次编辑 → 首尾缝修补 → 自动硬门禁”的试验路线。不过 OpenAI 官方也明确提示：模型仍可能在结构化构图和跨生成一致性上失败，因此它只能成为候选生成器，不能成为几何担保人。
5. **Nano Banana 2/Pro 官方输出比例没有列出 2:1。**不要赌未文档化比例。若必须使用现有 B2/Pro，推荐把同光心六面体及其约束缓冲排成一个 3×2 图集，在一次请求中整体编辑，再确定性拆分和转回 ERP；这比六次独立生成好，但依然必须经过接缝和几何门禁。
6. **“全屋”应是多个独立球面热点组成的 tour，而不是把不同房间位置拍到的图片拼成一张。**不同热点本来就有正确的视差。如果要求像游戏一样连续走动，就超出了单张全景的能力，需要真实网格、NeRF 或 Gaussian Splatting；普通项目没必要先走这条重路线。

建议实施顺序：

```text
第一阶段（最快验证）
现有 WholeHomeModel → 同光心六面体/ERP 六通道 → GPT Image 2 整张 ERP 编辑
→ 环形移位修缝 → 球面硬门禁 → 360 Viewer 人工验收

第二阶段（复用现有 B2/Pro）
同光心六面体五通道 → 3×2 图集单次 B2/Pro 编辑
→ 确定性拆分 → cube→ERP → 环形修缝 → 同一套硬门禁

正式阶段（要求“保证”）
AI 设计/材质候选 → 物体与材质落回共享 3D 场景/纹理图集
→ 3D 几何渲染最终 ERP → AI 只允许小范围、带 mask 的外观修饰
```

## 2. 需求要分成三种“一致”

### 2.1 同一热点内的方向一致性

固定点 `C=(x,y,z)` 不变，只改变射线方向。最终交付是一张完整 2:1 ERP；Viewer 的每个透视窗口只是从这张图重投影。只要首尾闭环和极点正常，同一方向永远取到同一批像素。

这一项可以严格实现。

### 2.2 同一热点内的结构正确性

球面虽然连续，但 AI 可能把门移位、重复一扇窗、弯曲墙线或在背面创造新房间。接缝无痕不等于几何正确，所以必须同时比较 CAD/灰模的 depth、normal、edge、semantic 和 opening/object ID。

这一项只有在最终几何来自 3D 渲染时才能严格保证；AI 直接生成只能通过门禁提高合格率。

### 2.3 不同热点之间的整屋一致性

客厅热点和走廊热点应该看到同一张沙发、同一扇门、同一块地板，但投影位置必须因相机移动而改变。这不是“像素一样”，而是**同一 3D 实体在不同相机下产生正确视差**。

多个热点分别调用生图 API，即便复用提示词和参考图，也无法严格保证物体背面、遮挡关系、尺寸和材质完全一致。共享 3D 场景/共享纹理才是正确的数据结构。

## 3. 3ds Max 的定点球面全景是怎样做出来的

### 3.1 Autodesk 原生 Panorama Exporter

Autodesk 官方流程是：场景中放置一台相机，打开 `Rendering → Panorama Exporter`，设置渲染条件并渲染。3ds Max 会渲染一系列视图并构建 360° 球面图，官方建议草稿以外至少 `2048×1024`。官方还特别说明，Panorama Exporter 不应使用 Physical Camera，而应使用 Free 或 Target Camera。[Autodesk：To Create a Panorama](https://help.autodesk.com/view/3DSMAX/2025/ENU/?guid=GUID-432745B8-66E9-416C-8BCA-EF762CD3495F)

渲染后可从 Viewer 选择 `Sphere` 导出。[Autodesk：To Export a Rendered Panorama](https://help.autodesk.com/cloudhelp/2024/ENU/3DSMax-Rendering/files/GUID-C3F0576D-AB46-45AB-97F1-D05ECF1BA9BE.htm)

关键不是按钮，而是以下不变量：

- 所有方向共享完全相同的相机位置；
- 世界几何、灯光、材质、曝光和时间帧完全不变；
- 只改变射线方向，不改变投影中心；
- 最终投影覆盖水平 360°、垂直 180°；
- 完整 ERP 为 2:1，左边界和右边界在球面上是同一条经线。

### 3.2 3ds Max + V-Ray 的常用生产方式

V-Ray 在 Camera rollout 中直接提供 `Spherical panorama` 和 `Cube 6x1`。`Spherical panorama` 可独立设置水平/垂直 FOV，用于球面 VR 的 latitude-longitude 图；`Cube 6x1` 将六个立方体面排成一行。Chaos 文档也说明这些相机类型定义了从图像像素向场景发出的射线。[Chaos：V-Ray Camera](https://docs.chaos.com/display/VMAX/Camera)

不要把 Autodesk Panorama Exporter 和 V-Ray 的相机 override 混成一套设置。使用 V-Ray Camera rollout 的球面类型时优先用标准 3ds Max 相机；Chaos 文档说明，场景使用 VRayPhysicalCamera 时，该 rollout 的大部分参数会被忽略。正式出图前必须用一张带方向文字或彩色轴的测试场景确认投影类型确实生效。

推荐设置：

- Camera center：房间内真实可站立点，常见眼高 `1.45–1.60m`；同一项目固定一套规则；
- Roll：`0°`，世界 Y 轴保持竖直；
- Monoscopic：单目全景，不要误开立体 ODS；
- Projection：`Spherical panorama`；
- Horizontal FOV：`360°`；Vertical FOV：`180°`；
- Output：严格 2:1；草稿 `4096×2048`，交付建议 `8192×4096`，更高分辨率按 Viewer 目标决定；
- Exposure / white balance：先计算并锁死，不能每个方向自动重算；
- 关闭 DOF、motion blur、镜头畸变和会依赖屏幕空间的随机后效；
- 固定随机种子和时间帧，静态灯光、材质和动态纹理不随面变化；
- 同时输出 Beauty、linear depth、world normal、object/semantic ID、edge/cryptomatte 等校验层。

如果使用 `Cube 6x1`，六个面仍必须共享一个相机中心。它不是六个机位，而是同一个机位的 `+X/-X/+Y/-Y/+Z/-Z` 六组 90° 射线。

### 3.3 为什么同光心能消灭“拼接位移”

真实相机拼全景时，相机必须绕镜头的 no-parallax point（入瞳中心）旋转；随便绕三脚架螺丝旋转会让前景和背景相对移动，软件无法完美对齐。[Hugin：No-parallax point](https://hugin.sourceforge.io/docs/manual/No-parallax_point.html)；[PTGui FAQ](https://www.ptgui.com/faq/what-is-good-overlap.html)

虚拟相机更简单：所有射线天然从数学点 `C` 出发。六个面只使用不同旋转矩阵 `R_i`：

```text
C_i = C                      # 六个面完全相同
d_i(u,v) = R_i · normalize(x, y, 1)
P = C + t · d_i(u,v)         # t 为射线命中深度
```

只要 `C_i` 没变，软件就不需要根据图片特征“找位置”。所谓拼接只是已知方向之间的重采样，不应再让 PTGui/OpenCV 优化 homography 或移动相机。

### 3.4 标准 ERP 映射

Google Street View 的自定义全景使用 equirectangular（Plate Carrée）投影：水平 360°、垂直 180°、完整图 2:1，并强调源图应共享单一 camera locus。[Google：Custom panoramas](https://developers.google.com/maps/documentation/javascript/streetview#creating_custom_panoramas)

本项目为右手系、Y 向上，可固定以下约定：

```text
u ∈ [0, W), v ∈ [0, H)
longitude λ = 2π(u/W - 1/2)
latitude  φ = π(1/2 - v/H)

direction d = (
  cos(φ) · sin(λ),
  sin(φ),
  cos(φ) · cos(λ)
)
```

这里 `λ=0` 朝 `+Z`。项目也可选择其他 forward，但必须将 `forward/up/face_order/handedness` 写入 manifest，不能靠前后端各自猜。

最终写入 GPano XMP：`ProjectionType=equirectangular`、完整宽高、heading、pitch、roll 等。Google 给出了这些字段和欧拉角顺序。[Google：Photo Sphere XMP Metadata](https://developers.google.com/streetview/spherical-metadata)

## 4. 为什么普通生图模型会漂

### 4.1 六次请求不是六面相机

文本里的“yaw=90°、同一位置”不是 API 的相机矩阵。每次生成都有新的隐变量，模型可能改变：

- 墙角位置和房间长宽；
- 门窗数量、门扇方向和开口宽度；
- 家具尺寸、款式、朝向和数量；
- 地板拼缝相位；
- 光源方向、阴影和曝光；
- 背面本来不可见的物体形态。

最后用拼接器强行对齐，只会在一处隐藏错误，并把位移推到另一处。

### 4.2 普通 2D 模型不了解球面拓扑

ERP 的左右边是邻居，顶部整行压缩到天顶，底部整行压缩到地底。普通 2D 卷积/注意力不会天然把左右端当邻居，也不会天然理解极点畸变。研究工作之所以专门加入环形混合、投影感知或跨视图 correspondence attention，正说明普通文生图管线不具备这个保证：

- MVDiffusion 同时处理多视图并通过已知像素对应做跨视图交互，避免逐张生成的误差累积。[MVDiffusion 项目页](https://mvdiffusion.github.io/index.html)
- Diffusion360 在去噪和 VAE 解码阶段做 circular blending，专门解决 ERP 左右连续性。[Diffusion360](https://arxiv.org/abs/2311.13141)
- PanFusion 使用透视分支、全景分支和 projection-aware attention 处理球面畸变。[PanFusion / CVPR 2024](https://openaccess.thecvf.com/content/CVPR2024/papers/Zhang_Taming_Stable_Diffusion_for_Text_to_360_Panorama_Image_Generation_CVPR_2024_paper.pdf)

这类研究可以作为后备 R&D，但对已有 CAD 的室内项目，仍然不如“共享 3D 几何后确定性渲染”可靠。

### 4.3 seed 不是几何锁

seed 只能在模型、服务实现、输入顺序和采样器都保持一致时帮助复现随机过程；它不会建立跨方向像素对应，也不会阻止模型重画墙体。API 切换模型版本、供应线路或内部推理过程后，也不能把 seed 当长期合同。

## 5. 本项目已经具备什么，还缺什么

### 5.1 已有基础很好

项目当前主链路已经包含正确的基本思想：

- [`whole_home_engine.py`](../whole_home_engine.py) 的 `WholeHomeModel` 以统一米制右手系保存墙、开口、房间、固定物和相机；
- 相机保存 XYZ、target、焦距、房间和来源；
- RGB、linear depth、normal、edge、semantic、subject-ID 六通道来自同一机位；
- `source_hash` 已绑定 `model_hash + camera + aspect_ratio`，结构或机位变化会使旧 capture 失效；
- 生图提示词已经把 CAD/灰模定义为几何权威；
- 本地 OpenCV gate 会比较 normal、semantic 和最终结构轮廓；
- Gemini QA 逐条检查 camera、wall、opening、depth order 和 fixed object；
- [`config.py`](../config.py) 已使用稳定模型 ID：`gemini-3.1-flash-image`（B2）和 `gemini-3-pro-image`（Pro）。

这些逻辑详见现有的 [`WHOLE_HOME_3D_PIPELINE.md`](./WHOLE_HOME_3D_PIPELINE.md)。全景功能应扩展这条链路，而不是另建一个只靠提示词的新系统。

### 5.2 当前不能直接输出球面全景

需要补的明确差距：

1. API schema 和候选算法只允许 `4:3 / 16:9 / 3:4 / 9:16`，没有 `2:1` 和 projection 类型。
2. 现有相机是 `position + target + focal_length_mm` 的透视相机，没有 `projection=equirectangular/cubemap`、canonical heading、face order 和 360 manifest。
3. [`whole_home_software_renderer.py`](../whole_home_software_renderer.py) 当前使用标准透视除法把三角形投到屏幕，不是球面或立方体渲染。
4. 现有软件 depth 每张图按自身 1%/99% 分位归一。六个面若各自归一，灰度相同不再代表相同距离；全景必须使用整组统一的 metric near/far。
5. 现有 normal 是 camera-space normal。六个面相机基不同，面边界上的颜色会跳；全景约束应保存 world-space normal，或保存明确定义的球面局部坐标 normal。
6. 当前 QA 是单张透视图 gate，没有左右 wrap seam、cubemap edge、极点和跨热点重投影检查。
7. 目前每个房间结果是独立 4:3 生图。它能约束单张构图，但不能证明房间背面或另一个热点仍是同一个物体。

## 6. 三条生成路线的能力评估

| 路线 | 单热点首尾闭环 | 精确 CAD 结构 | 跨热点同一物体 | 工程量 | 结论 |
|---|---:|---:|---:|---:|---|
| 完整共享 3D 场景直接渲染 | 可严格保证 | 可严格保证 | 可严格保证 | 中 | 正式交付唯一推荐 |
| GPT Image 2 整张 2:1 ERP 编辑 | 比分面生成好，可修缝 | 不能保证，必须 gate | 不能保证 | 低到中 | 最优 API MVP |
| Nano Banana 3×2 cubemap 图集单次编辑 | 可提高，仍需修缝 | 不能保证，必须 gate | 不能保证 | 中 | 复用现有 B2/Pro 的 POC |
| 六个方向独立调用任意生图模型 | 差 | 差 | 差 | 表面低、返工高 | 禁止作为生产路线 |
| 专用 360 diffusion | 通常比通用模型好 | 对 CAD 硬约束仍有限 | 有限 | 中到高 | 后备研发，不先做 |

### 6.1 Nano Banana 2 / Pro

Google 官方将 Nano Banana 2（`gemini-3.1-flash-image`）定位为速度、成本和能力均衡的通用模型，擅长多参考图和一致性；Pro（`gemini-3-pro-image`）面向复杂专业资产和精细控制。两者都支持 1K/2K/4K，Gemini 3 图像模型最多可混合 14 张参考图。官方细分上，B2 可高保真处理最多 10 个对象参考；Pro 可高保真处理最多 6 个对象参考、最多 5 个角色、最多 3 个风格参考。[Google Gemini image generation guide](https://ai.google.dev/gemini-api/docs/image-generation)

与本任务相关的限制：

- 官方输出比例表未列出 2:1；Pro 只列出常见比例至 21:9，B2 虽增加 4:1/8:1，也没有 2:1；
- 官方写明模型不一定严格遵循要求的输出图片数量；
- “camera control”仍是用摄影语言控制构图，不是传入外参/内参矩阵；
- 多参考图高保真描述的是对象/角色/风格保真，不等价于多视图 epipolar 或球面像素一致性；
- multi-turn 适合迭代，但连续编辑仍可能累积几何变化。

因此 Nano Banana **可以做受约束的全景外观候选，不能承诺无漂移**。

对本项目的最佳使用法不是六次独立生成，而是：

```text
RGB cubemap 3×2 atlas          ┐
world-depth 3×2 atlas          │
world-normal 3×2 atlas         ├─ 一次请求 → edited RGB 3×2 atlas
semantic/instance 3×2 atlas    │
可选 style/material reference  ┘
```

图集只使用受支持的 3:2 比例；六格必须带固定顺序和机器可读 manifest。输出后不相信格线位置，先用注册标记和结构 edge 找回每格，再裁切、重投影和 gate。若一格失败，整张 atlas 失败，不能偷偷替换单格后继续交付。

### 6.2 GPT Image 2

OpenAI 官方将 GPT Image 2 定义为当前高质量图像生成和编辑模型，支持灵活尺寸和高保真图像输入；还提供固定快照 `gpt-image-2-2026-04-21`。[OpenAI：GPT Image 2 model](https://developers.openai.com/api/docs/models/gpt-image-2)

它对这个任务的优势：

- `size` 可使用满足约束的任意分辨率；长边不超过 3840、长短边比不超过 3:1、两边为 16 的倍数、总像素不超过 8,294,400，因此 `3840×1920` 完整 2:1 ERP 合法；
- 图像编辑、多个参考图、mask、multi-turn 均受支持；
- GPT Image 2 对所有输入图自动使用 high fidelity，不需要也不能降低 `input_fidelity`；
- 整张 ERP 在一次请求中生成，至少避免六次独立随机采样。

官方同时明确提示：

- mask 是提示性指导，不保证严格按 mask 像素边界执行；
- 模型有时难以在结构化、对布局敏感的构图中精确放置元素；
- 跨多次生成仍可能难以维持视觉一致；
- 超过常规 2K 的输出目前属于 experimental。

以上均来自 [OpenAI Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)。

所以评估结论是：

> **GPT Image 2 能实现“生成一张可旋转浏览、经过修缝后视觉连续的球面候选”，而且比 Nano Banana 更适合直接做 2:1 ERP；但它不能单独实现“CAD 结构和多个热点绝对不漂移”的保证。**

建议固定 `gpt-image-2-2026-04-21` 做基准，避免 alias 更新影响 A/B 测试。固定快照能锁住模型版本，不等于每次输出逐像素确定。

## 7. 推荐生产 SOP

### 7.1 冻结整屋几何和语义

在花生图费用前完成：

1. 锁定 `WholeHomeModel`；所有墙、开口、固定物都通过现有 geometry/semantic gate。
2. 给每个持久实体分配稳定 ID：`wall_id/opening_id/object_id/material_id`。
3. 门窗必须绑定墙和 offset，不能只保存在一张参考图中。
4. 对会从多个热点看到的家具补齐完整体块，而不只是正面广告牌。
5. 生成 `model_facts_hash`、`material_graph_hash`、`lighting_hash`。
6. 任何结构、物体、材质或灯光变化都使相关 panorama capture 过期。

### 7.2 选择热点，而不是给每个朝向选机位

每个热点只保存一个中心和一个 canonical heading：

```json
{
  "pano_id": "pano_living_01",
  "position_m": {"x": 4.25, "y": 1.55, "z": 3.80},
  "forward": {"x": 0.0, "y": 0.0, "z": 1.0},
  "up": {"x": 0.0, "y": 1.0, "z": 0.0},
  "projection": "equirectangular",
  "horizontal_fov_deg": 360,
  "vertical_fov_deg": 180,
  "heading_deg": 0,
  "roll_deg": 0
}
```

热点安全门禁沿用现有相机规则，并新增：

- 球体半径 `0.18–0.25m` 内不能穿墙或家具；
- 眼高在同一楼层规则内；
- 不落在门扇摆动区；
- 不靠墙太近，否则 ERP 中墙面会占据巨大角度；
- 开放空间可多个热点，但距离至少约 `1.5–2.5m`，避免重复；
- 每个房间 1 个主热点，超大开放区再加 1 个；不要为了“全”而密铺。

### 7.3 从同一个光心渲染六面约束

项目已经使用 Three.js。官方 `CubeCamera` 就是“在 3D 空间某一点渲染周围环境到 cube render target”的相机；一次 `update` 渲染六个面。[Three.js：CubeCamera](https://threejs.org/docs/pages/CubeCamera.html)

建议浏览器实现：

1. 使用 `WebGLCubeRenderTarget` + `CubeCamera`；near/far 固定；
2. 六个面强制正方形、FOV 90°，共享位置；
3. 对 RGB、metric depth、world normal、edge、semantic、instance ID 各做一次完整六面 capture；
4. 所有通道使用同一 face order 和 orientation；
5. readback 后保存无损 PNG/EXR，以及 3×2 atlas；
6. 用确定性 shader 或离线转换生成 ERP，不做 feature matching；
7. 用彩色轴测试图验证 `+X/-X/+Y/-Y/+Z/-Z` 没有翻转或对调。

建议规格：

| 用途 | cube face | ERP | 说明 |
|---|---:|---:|---|
| 候选/QA | 512 或 1024 | 2048×1024 / 4096×2048 | 快速检查 |
| GPT Image 2 MVP | 960 左右的等效采样 | 3840×1920 | 满足 GPT Image 2 最大边 |
| 最终 3D 渲染 | 2048 | 8192×4096 | 室内交付常用起点 |

注意：AI 输入可用 8-bit PNG，但 QA 应保留 float metric depth。不要把每个面分别拉伸到 0–255。

### 7.4 路线 A：正式交付——AI 外观回写 3D，再确定性渲染

这是唯一可对“不会偏移”作强承诺的路线。

1. 先从热点渲染灰模球面和若干普通透视检查视图。
2. B2/Pro/GPT Image 2 生成装修和材质候选，供设计确认；不直接把它当最终空间事实。
3. 把确认内容拆成共享资产：墙漆、木地板、柜门、台面、软装材质和完整家具 mesh/asset。
4. 每个 surface/object 绑定稳定 `material_id/asset_id`；同一对象在所有热点只引用一个资产。
5. 若从 AI 图回投纹理，使用批准的 depth + camera pose 回投到 UV atlas；遮挡区不得凭另一个热点偷偷补出不同结构。
6. 完整场景再次跑 collision、opening 和跨热点可见性检查。
7. 最终 ERP 全部由 3D renderer 输出；可允许 AI 只在不接触结构边、门窗、家具轮廓的 mask 内做颜色、微纹理、去噪等修饰。
8. 修饰前后重新跑球面几何 gate；失败就交付未修饰的确定性渲染，不得带病放行。

这条路线的成本主要是把“看起来像家具的代理体”升级为可从背后看的完整资产。这个工作不能靠拼接算法省掉，因为 360° 本来就会看到物体背面。

### 7.5 路线 B：GPT Image 2 整张 ERP MVP

#### 输入

1. Image 1：完整 `3840×1920` RGB clay ERP，唯一编辑画布；
2. Image 2：同球面、固定 near/far 的 depth ERP；
3. Image 3：world normal ERP；
4. Image 4：edge ERP；
5. Image 5：semantic ERP；
6. Image 6：稳定实例颜色的 subject-ID ERP；
7. 风格或材料方向在 P0 作为文字合同传入，不占用六张几何权威图的编号。

不要一次塞入户型图、多个普通视角、六通道和大量风格图，让模型猜谁是几何权威。每张输入角色必须在 prompt 中逐项编号。

#### 请求建议

```text
model: gpt-image-2-2026-04-21
endpoint: /v1/images/edits
size: 3840x1920
quality: high
format: png
```

#### 提示词骨架

```text
Edit Image 1 only. It is a complete monoscopic equirectangular panorama:
360 degrees horizontally, 180 degrees vertically, aspect ratio 2:1.
The left and right edges are adjacent and must join continuously.

Geometry authority:
- Image 1 fixes every camera ray, crop, wall, ceiling, floor, opening,
  object silhouette and occlusion.
- Image 2 fixes metric depth order.
- Image 3 fixes world-space surface orientation.
- Image 4 fixes architectural and object edge boundaries.
- Image 5 fixes semantic role identity; never copy its false colors.
- Image 6 fixes exact opening/object instance identity; never copy its false colors.

Allowed change: convert the approved clay scene into [style/material/lighting].
Forbidden: move the viewpoint; change projection; add/delete/move walls,
doors, windows, columns or objects; reveal hidden rooms; duplicate an object;
change floor elevation; bend straight architectural edges.

Panorama contract:
- Preserve exact 2:1 equirectangular layout.
- Make longitude -180° and +180° continuous in geometry, texture, lighting
  and floor seams.
- Preserve a level horizon and coherent ceiling/floor at both poles.
- Return one image only, with no frame, labels, grid, split panels or text.
```

#### 首尾缝修补

即使一次生成整张 ERP，也要检查左右边。失败时只允许一次受控修补：

1. 将 ERP 水平 circular shift `W/2`，原左右接缝移动到图像中央；
2. depth/normal/semantic/mask 同步 shift；
3. 只给中央窄带（建议按角度定义，不按固定像素）做 mask edit；
4. prompt 要求衔接两侧纹理，禁止改变结构；
5. inverse shift 回原 heading；
6. 重新跑全部 gate。

由于 OpenAI 官方说明 mask 不是像素级硬边界，修补前后必须比较 mask 外结构。一次仍失败就换原始候选，不要无限多轮编辑。

### 7.6 路线 C：Nano Banana 3×2 cubemap 图集 POC

Google 输出没有文档化 2:1，因此使用受支持的 3:2：

```text
+X | -X | +Y
---+----+---
-Y | +Z | -Z
```

实际 face order 可以不同，但只能有一个项目级标准。每个输入通道都使用完全相同的图集布局，并在四角放置小型机器注册标记；标记位于后续裁掉的 gutter，不能覆盖画面。

建议一次请求输入：

- RGB atlas；
- global metric depth atlas；
- world normal atlas；
- semantic + instance atlas；
- 一张风格/材料参考。

这 5 张输入不超过两种模型的 14 张总参考图额度，也给 Pro 声明的对象/风格高保真额度留下余量；但 depth/normal 仍只是模型看到的参考图，不会自动变成 ControlNet 式硬约束。不要再把六个 face 分别作为六张输入、同时重复加入五种分面缓冲，让模型在 11 张角色相近的图片中猜对应关系。

输出后：

1. 检测注册标记和 3×2 单元格，不按“理论像素位置”盲切；
2. 每格反向注册到原始 face，禁止自由 homography，只允许全局微小缩放/平移校正图集边框；
3. 转换到 ERP；
4. 检查 12 条 cube edge；
5. ERP 左右移位修缝；
6. 重新投影为 12 个 yaw 检查视图做结构 QA；
7. 任意 face 新增/删除开口或物体，整张结果 hard fail。

如果 B2 与 Pro 都做，保留两者完整 atlas，不要把 B2 的五个好面和 Pro 的一个好面拼成“混血”交付；那会重新引入跨模型接缝。

## 8. 拼接规则：只做投影，不做相机求解

### 必须做

- cube→ERP 使用固定射线公式；
- face 边缘使用 seam-aware sampling 和至少 2–4 像素 gutter；
- 所有颜色处理在线性空间完成，最后统一转 sRGB；
- tone mapping、曝光、白平衡一次性应用于整组；
- ERP 的滤波在 X 方向使用 wrap；
- 对极点使用球面/立方体重投影检查，不能只看平面缩略图。

### 禁止做

- 让 PTGui/OpenCV 自动估计 AI face 之间的相机位置；
- 对每个 face 单独自动曝光或自动白平衡；
- 独立锐化/超分六个 face 后直接拼；
- 为了接上一条地板缝而扭曲整面墙；
- 裁掉接不上的区域后仍声称是完整 360×180；
- 将两个不同热点拼成一个球面。

## 9. 球面自动验收

现有 `coverage@12 + distance transform + Gemini adversarial QA` 可以复用，但度量要变成球面/角度无关形式。

### 9.1 硬门禁

1. **Manifest/Hash**：`model + hotspot + projection + face order + near/far + channel files + lighting/material version` 全部匹配。
2. **尺寸**：完整 ERP 必须严格 2:1；不能被裁切或加边框。
3. **Wrap seam**：比较左右各一条球面带的颜色梯度、edge、semantic 和 depth 连续性；将图 circular shift 后接缝处不能出现新直线。
4. **Cube edges**：ERP 重投影回 cubemap 后检查 12 条边。两边按相同球面方向采样，而不是比较未经旋转的边缘数组。
5. **结构 edge**：在 12 个 yaw（每 30°）和至少 `pitch=0°/+45°/-45°` 的透视检查视图上，与批准的 edge/normal/semantic 比较。
6. **Opening identity**：每个 accepted `opening_id` 的数量、方向和角范围一致；新增或缺失一项即 hard fail。
7. **Object identity**：每个 required `object_id` 只出现一次，不能在 ±180° 接缝重复。
8. **Depth order**：主要遮挡顺序和灰模一致；墙后空间不能被显露。
9. **Poles**：天顶/地底不能出现放射状重复、洞、拉丝或不同材质汇聚错误。
10. **跨热点重投影**：利用两个热点的 pose + depth，把共同可见的墙/地/固定物投到另一个热点；非遮挡区域的结构边和 material/instance ID 必须一致。

### 9.2 用角度而不是固定像素设阈值

平面边缘误差转成角度：

```text
horizontal_error_deg = pixel_error × 360 / ERP_width
vertical_error_deg   = pixel_error × 180 / ERP_height
```

这样 2K、4K、8K 使用同一物理阈值。首轮可从以下保守门槛起步，再用真实项目标定：

- 主墙/门窗轮廓中位误差 `≤0.35°`；
- 任一 accepted opening 的中心误差 `≤0.5°`，数量必须完全一致；
- mask 外发生可见结构变化：直接失败；
- 左右 seam 出现贯穿性亮度跳变、断线或重复物体：直接失败；
- 跨热点同一平面的 reprojection 不能通过改变相机 pose 来“优化到通过”。

阈值应像现有 render gate 一样版本化，保存原始 metrics，不能修改阈值后重写历史结果。

### 9.3 Viewer 人工验收路径

自动 gate 通过后，Viewer 自动播放固定检查序列：

```text
yaw: 0 → 90 → 180 → 270 → 360
pitch: 0
然后 yaw 每 45°，pitch: +60 / -60
最后停在原始左右接缝方向和天顶/地底
```

检查人员只需回答：墙/开口、重复物、材质连续、光线连续、极点、跨热点同一对象六项。任何一项 uncertain 按失败处理或转人工修复，不能自动通过。

## 10. 建议新增的数据合同

```json
{
  "schema_version": 1,
  "pano_id": "pano_living_01",
  "projection": "equirectangular",
  "coordinate_system": "right-handed-y-up",
  "camera_center_m": {"x": 4.25, "y": 1.55, "z": 3.80},
  "canonical_forward": "+Z",
  "heading_deg": 0,
  "pitch_deg": 0,
  "roll_deg": 0,
  "horizontal_fov_deg": 360,
  "vertical_fov_deg": 180,
  "erp_size": {"width": 4096, "height": 2048},
  "cube_face_size": 1024,
  "cube_face_order": ["+X", "-X", "+Y", "-Y", "+Z", "-Z"],
  "near_m": 0.05,
  "far_m": 30.0,
  "depth_encoding": "linear_metric_global_range",
  "normal_encoding": "world_space_xyz_to_rgb",
  "model_facts_hash": "...",
  "material_graph_hash": "...",
  "lighting_hash": "...",
  "channels": {
    "rgb_erp": "...",
    "depth_erp": "...",
    "normal_erp": "...",
    "edge_erp": "...",
    "semantic_erp": "...",
    "subject_id_erp": "..."
  },
  "source_hash": "..."
}
```

`source_hash` 应覆盖这里除生成结果和 QA 以外的全部字段。当前 `_capture_hash` 的思路正确，只需扩大输入范围。

## 11. 对项目的实施清单

### P0：可验证的最小闭环

1. 新增 `projection` 和 `PanoCapture` schema，不要把 2:1 硬塞进普通 perspective `aspect_ratio`。
2. 在 `WholeHomeStudio.tsx` 增加同光心、固定 face basis 的逐面六通道 capture。
3. 实现 canonical cubemap↔ERP 转换和轴向单元测试。
4. depth 改为全组六面固定 near/far；normal 改为 world-space。
5. 生成 3×2 atlas 和 2:1 ERP，manifest + hash 落盘。
6. 先接 GPT Image 2 snapshot，整张 `3840×1920` 编辑。
7. 增加 wrap seam、12 cube edges、12 yaw views 的本地 gate。
8. 用 Three.js ERP Viewer 做人工检查；Three.js 支持直接把 equirectangular texture 设为背景。[Three.js：Backgrounds and Skyboxes](https://threejs.org/manual/en/backgrounds.html)

### P1：复用 B2/Pro

1. 增加 3×2 atlas prompt/input contract；
2. B2/Pro 各自单次生成完整 atlas；
3. atlas 注册、拆分、cube→ERP；
4. 复用与 GPT 路线完全相同的 gate，不为任何模型降低门槛；
5. 统计 B2/Pro/GPT 的首轮通过率、修补通过率、平均调用次数和成本。

### P2：真正的全屋一致

1. 把常见家具/固定物从语义代理升级为完整 3D asset；
2. 建立 surface/material/asset 稳定 ID；
3. AI 结果转材质或资产选择，而不是永久保留为独立房间照片；
4. 增加跨热点 depth reprojection gate；
5. 最终 8K ERP 从共享场景渲染。

## 12. 必做基准测试

不要凭两张好图决定模型。建立 12 个热点的小基准：

- 4 个简单：规整卧室/书房；
- 4 个中等：客餐厅、厨房、走廊，有多个开口；
- 4 个困难：狭小卫生间、贴近柜体、开放空间交界、强反射/大窗。

每个热点固定同一份输入和 prompt，对 B2、Pro、GPT Image 2 各跑至少 3 次，记录：

- 未修补首轮 hard-pass rate；
- 一次接缝修补后的 pass rate；
- opening/object 错误率；
- seam/pole 错误率；
- 跨热点一致率；
- 平均调用次数、耗时和成本；
- 人工盲评结果。

推荐的停止规则：

- 如果 GPT Image 2 首轮+一次修补仍不能在困难组达到可接受的 hard-pass rate，就停止把通用 API 当终稿，转 P2 共享 3D 资产；
- 如果 Nano atlas 明显低于 GPT ERP，不再继续微调六面独立提示词；
- 任一模型只能以“通过同一 gate 的比例”比较，不能用最好的一张宣传图比较。

## 13. 最终验收定义

### 可以承诺

- 一个热点只有一个相机中心；
- 最终文件是完整 2:1、360×180 ERP；
- Viewer 任意方向都来自同一个已验收球面文件；
- 左右闭环、cube edge、极点、开口和主要结构经过自动硬门禁；
- 多热点引用同一 3D 实体和材质时，其几何身份一致；
- 输入、模型版本、manifest、hash、生成调用和修补历史可追溯。

### 不能只靠 Nano Banana 或 GPT Image 2 承诺

- 提示词能锁定真实相机矩阵；
- 六次独立生成会天然无缝；
- 相同 seed 会让不同方向成为同一 3D 世界；
- mask 外绝不变化；
- 多个热点独立生成后家具背面和遮挡必然一致；
- 一次看起来不错就代表全屋所有方向正确。

## 14. 最短决策

如果当前目标是尽快做出能看的 360 样片：

> 先实现同光心六通道 ERP，接 `gpt-image-2-2026-04-21` 做整张 3840×1920 编辑和一次环形修缝；Nano Banana 用 3×2 atlas 做对照，不做六张独立图。

如果当前目标是对客户说“不会偏移、所有热点都是同一套房子”：

> AI 只能做设计和材质候选，最终必须回到共享 3D 场景并由 renderer 输出球面图。项目现有的 WholeHomeModel、六通道 capture、source hash 和三层 gate 已经是这条路线的正确底座。
