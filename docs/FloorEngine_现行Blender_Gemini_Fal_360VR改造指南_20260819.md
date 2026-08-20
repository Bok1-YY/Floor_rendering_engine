# Floor Engine 现行 Blender + Gemini Nano Banana + fal.ai 360° VR 改造指南

> 文档状态：现行执行稿 v2.0  
> 日期：2026-08-19  
> 适用项目：`Floor_engine_Linux`  
> 优先级：本文件在“插件验证、神经渲染、EEVEE 正式终稿”方面高于旧版 `CODEX_BLENDER_360VR_IMPLEMENTATION_HANDOFF.md`；旧版关于 GeometryManifest、真实地板、SceneRecipe、AOV、安全和 Cycles 精品终稿的原则仍然有效。  
> 核心变化：不再先开发完整 Blender/Cycles 生产线，而是先直接使用现成 Gemini/Nanode/fal 插件验证灰模神经渲染；根据实测结果决定集成深度，再由 EEVEE 承担大部分保证型 360° 终稿，Cycles 降为精品档。

---

## 0. 接手者先读结论

### 0.1 现行路线

现在推荐的主线是：

```text
户型图 / CAD / 确认尺寸
  → Floor Engine 本地解析
  → GeometryManifest
  → Blender 自动建立墙、地、顶、门窗和代理家具
  → Blender 真实板片 + 原厂彩膜
  → 直接安装现成 Gemini/Nanode/fal 插件进行单视角神经渲染赛马
  → 验证 Gemini 对墙体、家具、地板和相机的实际保持能力
  → 将通过的设计回写为材质、资产和 SceneRecipe
  → Blender EEVEE 原生 2:1 ERP 正式 360° VR
  → 少量高端热点可选 Cycles 精品渲染
```

### 0.2 为什么不再把 Cycles 放在第一步

旧路线认为必须先完成 Blender/Cycles 全链路，才能解决墙体和地板正确性。这个底层原则没有错，但实施顺序不够经济。

现行插件已经能做到：

- Blender viewport / EEVEE / Workbench 作为输入；
- Gemini Nano Banana 快速写实；
- 多参考图；
- Mist/Depth pass；
- style reference；
- AI texturing；
- fal ControlNet depth/edge/sketch/refine；
- 1K、2K、4K 神经效果图。

因此最合理的顺序是先用现成插件验证效果，不先造轮子。只有插件确实不能满足的部分才进入自研。

### 0.3 现在不做的事

- 不先开发完整 Cycles 8K 渲染农场；
- 不先开发自己的 Gemini Blender Add-on；
- 不把户型图直接交给 Meshy/Tripo，当作全屋建筑建模；
- 不把 Hunyuan World 的 world file 当作 GeometryManifest；
- 不分别让 Gemini 生成六个 cube face 后直接正式交付；
- 不再修旧 AI ERP 上的二维地板蒙版作为主线；
- 不让 Gemini 重画正式地板 Base Color；
- 不以插件宣传视频代替我们自己的真机验收。

### 0.4 最终产品分四档

| 产品档 | 核心技术 | 速度 | 几何保证 | 用途 |
|---|---|---:|---:|---|
| 即时 Gemini 效果图 | Blender viewport/EEVEE → Nano Banana | 最快 | 单图软约束 | 客户沟通、风格探索 |
| AI 神经 VR 预览 | Blender 多视角 passes → Gemini/ControlNet | 快 | 有门禁但不承诺严格一致 | 设计确认 |
| EEVEE 保证型 VR | 共享 Blender 场景 → EEVEE ERP | 快到中等 | 严格 | 大多数正式交付 |
| Cycles 精品终稿 | 共享 Blender 场景 → Cycles ERP/透视图 | 慢 | 严格 | 高端 Hero 图、复杂光学 |

---

## 1. 本指南相对旧指南的变化

| 项目 | 旧指南 | 现行指南 |
|---|---|---|
| 第一动作 | 安装 Blender，开发 headless scene builder | 安装 Blender 和现成神经渲染插件，先跑固定 PoC |
| AI 角色 | 设计候选，正式终稿不用 AI | 单图和预览直接用 AI；正式终稿仍回到 3D |
| 主正式渲染器 | Cycles | EEVEE 为主，Cycles 为精品档 |
| Gemini 接入 | 后期自研 | 先直接使用 XYZ360 / Nanode / fal 插件 |
| 深度约束 | 后续自建 AOV | 先试 Nanode Mist/Depth 和 fal ControlNet |
| 研发方式 | 按架构里程碑推进 | 同场景 A/B/C/D/E 赛马，数据决定集成 |
| 户型建模 | 本地 GeometryManifest | 不变，继续本地权威 |
| 热点规划 | 本地几何算法 | 不变，fal/Gemini 只做审美评分 |
| 地板 | Blender 真实板片 | 不变，且禁止 Gemini 成为正式 Base Color |
| 360 正式图 | Blender ERP | 不变，但优先 EEVEE |

旧指南仍然正确的部分：

- 单张图片不包含完整真实三维空间；
- GeometryManifest 必须是建筑几何权威；
- 真实地板必须使用毫米尺寸和原厂彩膜；
- 热点必须基于可行走空间和几何可见性；
- 正式多热点必须共享 scene hash；
- 最终 ERP 应由同一 Blender 场景和同一相机光心产生；
- 正式结果不能依赖六次彼此无状态的 AI 调用；
- 自动测试不能替代用户肉眼验收。

---

## 2. 已验证事实与待真机验证假设

## 2.1 已通过文档或源码验证的事实

### XYZ360 Gemini Nano Banana for Blender

文档：<https://www.xyz360.nl/gemini-nano-banana-for-blender-documentation/>

已确认：

- 最低 Blender 4.0；
- 使用当前 viewport render 作为主要图像输入；
- 支持额外本地参考图片；
- 支持常见宽高比和 scene custom resolution；
- 调用 Google Gemini API；
- 输出回到 Blender Image Editor；
- 提供 prompt 和 image history；
- `Keep Light Direction` 与 `Keep Camera Perspective & DOF` 通过追加提示词实现；
- 当前公开文档未说明使用 depth、normal、object ID、material ID 或 camera matrix；
- 当前公开文档未说明 AI 结果会被烘焙回 3D 材质；
- 属于付费插件，商用和二次分发要看购买许可。

### Nanode AI Render Engine

仓库：<https://github.com/Kovname/nano-banana-render>

已确认：

- GPL-3.0 开源；
- 原生 Blender render engine / editor panels；
- 支持 Gemini Nano Banana 2 Lite、Nano Banana 2、Nano Banana Pro；
- 支持 EEVEE 或 Workbench source capture；
- 支持 depth/mist 到写实图；
- 支持 EEVEE enhance；
- 支持 style reference；
- 支持 1K、2K、4K；
- 支持 AI texturing；
- 支持多角度投影相机；
- 支持 Render History；
- 支持个人 Google API 模式，也有自身 credits 模式；
- README 宣称保持几何与构图，但仍必须由本项目实测验证。

### fal.ai Blender Extension

仓库：<https://github.com/fal-ai/fal-blender-extension>

研究快照：`5135b62d8fa280302e77beb357c4d19997203a14`  
许可证：GPL-3.0-or-later

已从源码确认：

- Blender 4.2+；
- Controller 与 Model endpoint 分离；
- 后台线程调用 fal，`bpy` 回主线程；
- EEVEE Mist pass 可生成 depth；
- depth 范围根据实际 scene geometry near/far 计算；
- depth-to-image 使用 Z-Image Turbo ControlNet 或 FLUX ControlNet；
- Nano Banana 用于 Sketch-to-Image 和 Render-to-Image/Refine；
- 支持 Nano Banana `image_urls` 数组，但当前 render operator 主路径通常只传一个主图；
- 支持 edge、sketch、normal beauty/refine；
- 支持 PBR 材质估计和材质生成；
- 支持 Meshy/Hunyuan3D/Tripo 等单件 3D 模型生成；
- 当前项目较新，提交和 star 数较少，不能未经验证直接成为正式依赖。

### Gemini 官方图像能力

官方文档：<https://ai.google.dev/gemini-api/docs/image-generation>

已确认：

- 支持图片生成和图片编辑；
- 支持多轮编辑；
- 支持多张参考图；
- Gemini 3 系列可使用最多 14 张参考图，具体高保真对象/人物/风格数量按模型不同；
- 支持 1K、2K、4K，Flash 路线偏速度和高吞吐；
- 官方输入合同将参考图作为普通 image inputs；
- 当前公开文档未提供类型化的 depth、normal、camera matrix 或 ControlNet 字段；
- 公开宽高比列表不以严格 2:1 ERP 为主要标准输出；
- 官方说明模型不总是严格遵守用户要求的输出图片数量。

### fal.ai 3D / World 能力

已确认：

- Meshy/Hunyuan3D/Tripo/Trellis/Pixal3D 主要生成单件 3D 资产；
- Multi-image-to-3D 主要要求同一物体的多角度图片；
- Meshy 6 等端点输出 GLB/FBX/OBJ 等单体模型；
- Hunyuan World 可从普通场景图生成 panorama/world；
- Hunyuan World 更偏交互世界/DRC，不是传统可编辑建筑 mesh；
- fal 当前未提供“户型图 → 带房间/墙/门窗语义的毫米级全屋模型”标准端点。

## 2.2 必须等新电脑实测的假设

- Nanode 是否真的能在我们的室内场景中保住墙角和门洞；
- Gemini 是否会重画原厂地板；
- XYZ360 自定义分辨率能否可靠输出目标比例；
- 同一 prompt 连续生成的稳定性；
- 相邻两个视角中的家具身份是否一致；
- Nanode 多角度 AI texturing 是否能无缝烘焙整屋墙面；
- 插件是否暴露可供 Codex/脚本调用的 Blender operator；
- 插件能否 headless 运行；
- 插件的 API key 存储是否安全；
- 插件能否在目标 GPU 和 Blender LTS 上稳定运行；
- EEVEE 原生 4K/8K ERP 的实际耗时；
- Blender + 插件 + Floor Engine 同时运行时的显存峰值；
- 六/八方向经过 Gemini 后的跨视角误差；
- 真实彩膜在神经预览中的视觉保持率。

任何上述项目在实测前都不得写成“已解决”。

---

## 3. 现行总体架构

```text
                    用户输入
          户型图 / CAD / 尺寸 / 彩膜 / 风格图
                          │
                          ▼
              Floor Engine 本地几何内核
                          │
                    GeometryManifest
                          │
                          ▼
                 Blender 共享三维场景
       墙 / 地 / 顶 / 门窗 / 代理家具 / 热点 / 相机
                          │
       ┌──────────────────┼────────────────────┐
       │                  │                    │
   真实地板          技术通道              普通预览
原厂彩膜+板片   Depth/Normal/ID/Edge     EEVEE/Workbench
       │                  │                    │
       └──────────────────┼────────────────────┘
                          │
              插件神经渲染赛马层
       XYZ360 / Nanode / fal / Gemini direct
                          │
        ┌─────────────────┴───────────────────┐
        │                                     │
   AI 单图/VR 预览                       设计回写
 快速、高美感、软约束       材质/资产/灯光/SceneRecipe/PBR
                                              │
                                              ▼
                                  Blender EEVEE 原生 ERP
                                              │
                                  保证型正式 360° VR
                                              │
                                      可选 Cycles 精品档
```

---

## 4. 各组件的最终职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| Floor Engine | 户型解析、几何合同、任务、门禁、历史、Viewer | 不做正式 AI 像素几何猜测 |
| Blender | 真实几何、相机、地板、遮挡、技术通道、ERP | 不负责风格推理 |
| XYZ360 插件 | 最快单视角 Gemini PoC | 不作为正式多热点/球面保证 |
| Nanode | depth/mist/EEVEE 神经渲染、AI texturing | 几何正确性仍需本地门禁 |
| fal Extension | ControlNet、Gemini、多模型、PBR、单件资产 | 不负责户型拓扑权威 |
| Gemini | 审美、装修候选、墙面/家具/灯光观感 | 不负责毫米级建筑几何 |
| fal 3D 模型 | 单件家具/装饰候选 | 不直接从户型图恢复整屋 CAD |
| EEVEE | 大多数正式 ERP | 极复杂全局光照精品效果 |
| Cycles | 精品终稿、复杂玻璃/镜面/间接光 | 不作为所有普通预览的默认 |

---

## 5. 产品模式

## 5.1 模式 A：即时 Gemini 效果图

输入：

- 当前 Blender viewport；
- 文字 prompt；
- 风格参考图；
- 可选家具/墙面参考。

输出：

- 单张 1K/2K/4K AI 图；
- 明确标记 `gemini_neural_single_view`。

适用：

- 客户即时沟通；
- 风格筛选；
- 单张 Hero 视图；
- 不要求跨视角严格一致的宣传图。

首选工具：

1. XYZ360 插件；
2. Nanode EEVEE Enhance；
3. fal Render Refine。

## 5.2 模式 B：AI 神经 VR 预览

输入：

- Blender 同光心多方向视图；
- 每个方向的 beauty/depth/normal/ID/edge；
- 同一 SceneRecipe 和风格参考。

输出：

- 多方向神经渲染；
- 经本地重投影的预览 ERP；
- 明确标记 `neural_vr_preview`；
- 只有通过跨视角门禁才显示给用户。

限制：

- 不承诺家具身份逐像素一致；
- 不承诺地板商品最终真实性；
- 不作为保证型正式交付。

## 5.3 模式 C：EEVEE 保证型正式 VR

输入：

- 锁定 GeometryManifest；
- 锁定 SceneRecipe；
- 真实彩膜和板片；
- 审核后的 GLB 资产；
- 审核后的墙面/家具材质；
- 固定灯光和热点。

输出：

- Blender EEVEE 原生 2:1 ERP；
- Depth/Normal/Object ID/Material ID；
- scene hash、recipe hash、film hash；
- 正式 VR Tour。

这是大多数正式订单的目标产品。

## 5.4 模式 D：Cycles 精品终稿

仅用于：

- 主视觉；
- 镜面、玻璃、金属和复杂间接光；
- 高端客户指定热点；
- 材质近景；
- 宣传海报。

不要让整屋每个热点默认走 8K 高采样 Cycles。

---

## 6. 新电脑上的插件优先级

## 6.1 第一优先：Nanode 开源插件

原因：

- 开源，可读源码；
- depth/mist；
- EEVEE/Workbench；
- AI texturing；
- style reference；
- 原生 Blender render engine；
- 更接近正式产品结构。

必须检查：

- GitHub release 与 Blender 目标版本；
- Personal Google API 模式是否可用；
- 是否产生额外 credits；
- operator 名称和可脚本化程度；
- key 存储方式；
- 是否能关闭所有非必要遥测；
- 是否能导出生成结果路径和 metadata；
- depth/mist 是如何组织给 Gemini 的；
- AI texturing 是否真的烘焙到 UV。

## 6.2 第二优先：XYZ360 Gemini Nano Banana

原因：

- 最简单；
- 最快验证单张图；
- 多参考图；
- 适合先判断 Gemini 美感上限。

定位：

- 只做单视角基线；
- 不以其宣传的 keep camera/light 作为硬几何保证；
- 需要确认购买许可是否允许团队、自动化和商业交付；
- 不把插件二进制直接打进 Floor Engine 安装包，除非取得明确分发许可。

## 6.3 第三优先：fal.ai 官方 Blender Extension

原因：

- 同时测试 depth ControlNet、Gemini refine、PBR 和单件 3D；
- 模型 endpoint 可替换；
- 源码结构清晰；
- 适合做 A/B 赛马。

定位：

- 主要作为对照和能力来源；
- 不直接把整个插件作为产品核心；
- 若复制源码，必须履行 GPL；
- 可根据其 Model/Controller 分层自研后端适配器。

## 6.4 可选：Pallaidium

用途：

- 研究多参考图；
- Scene strip；
- Mist/Depth；
- 队列、取消和 metadata；
- ComfyUI/fal/custom backend；
- 视频和动画扩展。

不作为第一轮建筑 VR 必装项，避免同时安装太多 Blender add-on 导致环境混乱。

---

## 7. M0：不改 Floor Engine，先做插件真机赛马

## 7.1 固定测试场景

建立一个唯一客厅场景，禁止每条路线使用不同输入：

- 房间：约 4m × 5m；
- 墙高：使用真实值或明确记录值；
- 四面墙；
- 一扇门；
- 一扇窗；
- 沙发代理；
- 茶几代理；
- 柜体代理；
- 一盏吊灯或吸顶灯；
- 一个固定相机；
- 同一光源；
- 同一 EEVEE 配置；
- 真实板片；
- 原厂 `VL88238XL(EIR)-006 Full Layout.jpg`；
- 彩膜宽 984mm、长边周期 1890mm；
- 板宽/板长/厚度必须从产品资料补齐，不能猜。

## 7.2 固定相机

单图 PoC：

- 16:9 或 4:3；
- 固定焦距；
- 固定 camera transform；
- 记录 camera matrix；
- 固定 seed（如果插件支持）。

多视角 PoC：

- 同一光心；
- yaw：0°、45°、90°、135°、180°、225°、270°、315°；
- pitch：0°，另加 nadir 检查地板；
- FOV 固定；
- 禁止自动重构相机。

## 7.3 赛马路线

### A：EEVEE 基线

```text
Blender EEVEE → PNG
```

记录：

- 渲染耗时；
- 分辨率；
- 显存；
- 墙体线条；
- 地板真实性。

### B：XYZ360 Viewport → Gemini

```text
Viewport Render
→ Keep Light Direction
→ Keep Camera Perspective & DOF
→ 原厂彩膜参考 + 风格参考
→ Nano Banana
```

目的：验证最快单图效果。

### C：Nanode EEVEE Enhance

```text
EEVEE Beauty
→ Nanode Nano Banana Render Engine
→ 写实增强
```

目的：验证普通 beauty refine。

### D：Nanode Depth/Mist → Gemini

```text
Blender Depth/Mist
→ Nanode Nano Banana
→ 写实图
```

目的：验证插件所谓 geometry retention。

### E：fal Depth ControlNet

```text
Blender EEVEE Mist
→ Z-Image Turbo ControlNet / FLUX ControlNet
→ 写实图
```

目的：建立真正 depth-conditioned 对照组。

### F：fal Depth ControlNet → Gemini Refine

```text
Depth ControlNet 先锁结构
→ Nano Banana 低强度美化
```

目的：测试两阶段是否兼顾结构和 Gemini 美感。

### G：Gemini Hero Style → ControlNet/IPAdapter

```text
Gemini 先生成一张最高美感 Hero 图
→ 作为 Style Reference
→ Blender depth/normal + ControlNet/IPAdapter 生成其他视角
```

目的：避免每个视角独立依赖 Gemini 自由编辑。

## 7.4 固定 prompt

所有插件使用同一核心 prompt，只根据各插件参数合同调整格式。

建议英文核心：

```text
Transform this exact Blender interior blockout into a photorealistic warm modern living room.

GEOMETRY IS LOCKED:
- Preserve the exact camera position, orientation, focal perspective and composition.
- Preserve every wall corner, wall length, doorway, window, ceiling line and furniture silhouette.
- Do not curve, bulge, stretch, widen, narrow, move, add or remove architectural geometry.
- Treat the provided depth, normal, edge and semantic references as the same scene from the same camera.

FLOOR IS A LOCKED COMMERCIAL PRODUCT:
- Preserve every floor plank boundary, physical plank scale, laying direction, joint position, wood grain and film color.
- Do not repaint, regenerate, stylize, blur, replace or reinterpret the floor.
- The floor reference is the authoritative manufacturer film.

DESIGN ONLY THE ALLOWED APPEARANCE:
- Improve wall finishes, ceiling finish, furniture materials, decoration, lighting realism, reflections and atmosphere.
- Use a warm modern residential style with physically plausible materials and natural lighting.
- Keep object identity and location unchanged.
```

注意：prompt 不是门禁。结果仍必须做像素和几何检测。

---

## 8. M0 数据记录表

每个候选必须记录：

```json
{
  "candidate_id": "...",
  "plugin": "xyz360|nanode|fal_extension|direct_gemini",
  "plugin_version": "...",
  "plugin_commit_or_package_hash": "...",
  "blender_version": "...",
  "model": "...",
  "mode": "viewport|eevee|workbench|depth|mist|refine|controlnet",
  "prompt_hash": "...",
  "reference_hashes": [],
  "camera_hash": "...",
  "geometry_hash": "...",
  "film_hash": "...",
  "seed": null,
  "resolution": [0, 0],
  "elapsed_seconds": 0,
  "provider_cost": null,
  "output_hash": "...",
  "geometry_gate": {},
  "floor_gate": {},
  "human_score": {}
}
```

不要只保存最终最好看的图。失败图、参数和成本也必须保留，否则无法判断路线。

---

## 9. 单图验收门禁

## 9.1 建筑几何

从 Blender 基线导出 edge / semantic / depth，对 AI 结果检测：

- 墙角像素偏移；
- 门洞四角偏移；
- 窗户轮廓偏移；
- 竖线角度；
- 水平线曲率；
- 家具轮廓 IoU；
- 消失/新增对象数量；
- 地平线位置；
- 透视消失方向。

第一轮可以先作为观察指标，不要拍脑袋设最终阈值。用 20–50 张 Gold Set 标定后再升级为 hard gate。

## 9.2 地板

必须检测：

- 原厂彩膜颜色差；
- 板缝方向差；
- 板宽像素序列；
- 板长与端缝；
- 木纹局部特征匹配；
- 是否出现地毯/砂砾/石材语义；
- 家具附近地板是否被重画；
- 远处是否出现摩尔纹或融合成沙面。

第一轮直接生成三组：

1. 完全允许 Gemini 修改地板；
2. prompt 要求锁地板；
3. 使用 Material ID 将 Gemini 地板区域替换回 Blender 地板。

比较三组，决定正式神经预览策略。

## 9.3 肉眼评分

每张图 1–5 分：

- 写实感；
- 材质感；
- 光照；
- 家具审美；
- 墙面设计；
- 地板商品真实性；
- 空间比例；
- 是否愿意展示给客户。

几何 hard fail 的图不参与美感冠军评选。

---

## 10. 多视角和 360° 验收

## 10.1 为什么不能直接六次 IMAGINE 后拼接

六次 Gemini 调用没有共享 latent 或三维状态，可能导致：

- 家具跨视角变形；
- 墙板数量变化；
- 灯具位置变化；
- 光照方向变化；
- 地板方向/尺度变化；
- cube edge 纹理断裂；
- 顶面和侧面不一致。

## 10.2 Neural VR Preview 路线

```text
Blender 同光心 8 个重叠透视视图
→ 每视图 beauty/depth/normal/ID/edge
→ 使用同一 style reference、prompt、seed 和 SceneRecipe
→ 神经渲染
→ 重投影回 ERP
→ overlap 区比较和融合
→ 跨视角门禁
→ 只作为预览
```

重叠视图比裸六面好，因为有 overlap 可以测出差异，而 cube face 只有边界接触，发现问题更晚。

## 10.3 Guaranteed VR 路线

```text
插件生成风格/材质候选
→ 审核后回写 Blender surface / asset / lighting
→ Blender EEVEE 原生 equirectangular camera
→ 4096×2048 draft / 8192×4096 final
→ ERP + AOV + scene hash
```

正式 360° 不需要 AI 拼面。

## 10.4 AI Texturing 回写

Nanode / DiffusedTexture 类路线值得重点验证：

```text
AI 多角度结果
→ 按相机和深度反投影到 mesh
→ UV 空间融合
→ 空洞 inpaint
→ seam detection
→ texture bake
→ Blender 重渲染所有视角
```

只要 AI 结果最终成为共享三维表面材质，多热点一致性就比独立视图直接拼接可靠。

---

## 11. 户型、建模和 fal 的现行分工

## 11.1 户型图不直接交给普通 Image-to-3D

Meshy、Tripo、Trellis、Pixal3D 等更适合单件物体。普通俯视户型图直接转 3D，可能产生整体沙盘/浮雕，而不是房间、墙、门窗等建筑语义。

正式路线：

```text
户型图 / CAD
→ Floor Engine 本地解析
→ 人工确认疑点
→ GeometryManifest
→ Blender 建筑 mesh
```

## 11.2 fal 适合承担的内容

- 单件家具 GLB；
- 灯具和装饰候选；
- PBR normal/roughness/height 辅助图；
- 墙纸、装饰画、材质候选；
- depth/edge/sketch 神经效果图；
- Gemini refine；
- Hunyuan World 创意空间预览。

## 11.3 本地必须保留的内容

- 墙体和开口；
- 真实尺寸；
- 房间拓扑；
- 热点安全；
- 地板板片；
- 原厂彩膜；
- camera matrix；
- ERP projection；
- scene hash；
- 正式多热点一致性。

---

## 12. 热点规划

热点候选由本地算法确定：

- 点在房间/可行走区域内；
- 不穿墙；
- 不在家具内部；
- 离墙、门、柜体有安全距离；
- 覆盖主要功能区；
- 多热点不重复；
- 相机高度合理；
- 可以看到关键墙面和地板。

Gemini 可选职责：

- 对本地候选的构图和营销价值打分；
- 解释为何某个热点更适合客户查看；
- 不得凭空修改 camera transform。

---

## 13. 地板现行实现

## 13.1 权威输入

- 原厂彩膜文件；
- SHA-256；
- 彩膜宽度；
- 长边周期；
- 板宽；
- 板长；
- 厚度；
- 铺装方式；
- 铺装方向；
- 起铺原点；
- 端缝/错缝规则；
- joint 和 bevel。

## 13.2 Blender 中的地板

- 房间真实 floor polygon；
- Geometry Nodes 或 instance board；
- 世界坐标 UV；
- lane / longitudinal phase 来自现有 `film_repeat_floor.py`；
- Base Color 只来自彩膜；
- normal/roughness 可由本地或 fal PBR 辅助生成；
- 板缝和倒角由几何/程序节点生成；
- 相邻房间可共享同一个安装坐标系。

## 13.3 Gemini 边界

Gemini 可以：

- 看见地板，理解整体风格；
- 对地板周围灯光提出建议；
- 生成非正式预览。

Gemini 不可以作为正式权威修改：

- Base Color；
- 板宽和板长；
- 方向；
- 相位；
- 接缝；
- 木纹；
- 产品颜色。

正式结果优先让 EEVEE 渲染真实地板。神经预览如需保留地板，使用 Blender Material ID 做确定性保护。

---

## 14. 新电脑环境检查

## 14.1 软件

- Windows；
- Git；
- Python 3.12；
- Node.js 20.9+；
- Blender LTS；
- Codex；
- GPU 官方驱动；
- 可选 `uv` 和 Blender MCP。

## 14.2 GPU

记录：

```powershell
nvidia-smi
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion
```

必须记录：

- GPU 型号；
- 显存；
- 驱动；
- Blender EEVEE 可用；
- Cycles backend；
- 插件生成期间显存峰值；
- Floor Engine/Web 同时运行是否稳定。

## 14.3 Blender

固定：

- 具体 LTS patch 版本；
- Blender executable SHA-256；
- 插件版本/hash；
- 不自动升级；
- 每次插件升级重新跑 M0 Gold Scene。

---

## 15. 插件安全与许可

## 15.1 XYZ360

- 付费插件；
- 使用前阅读 Solo/Studio 商业许可；
- 确认是否允许自动化调用；
- 确认是否允许随产品分发；
- 默认只装在内部开发机；
- 不把插件文件打包给客户，除非得到许可。

## 15.2 Nanode

- GPL-3.0；
- 如果修改/分发 add-on，履行 GPL；
- 可作为开发依赖或独立插件；
- 若产品闭源部分与插件边界不清晰，做许可证审查；
- Personal API 和 credits 模式分开记录成本与数据流。

## 15.3 fal Extension

- GPL-3.0-or-later；
- API key 不进入 `.blend`、Git、截图和交接包；
- 后台线程不得调用 `bpy`；
- 只允许本地/工作区白名单路径；
- 第三方模型生成的资产仍需单独审核许可证。

## 15.4 Google Gemini

- 使用 Google AI Studio/Cloud API key；
- 密钥只保存在本机安全配置；
- 记录请求模型、费用和输出 hash；
- 不默认上传客户敏感 CAD；
- 技术通道只上传必要视图，不上传整个项目目录；
- 关注 Google 数据使用和地区政策；
- 生成图包含的模型标记/水印政策按官方文档执行。

---

## 16. 插件赛马后的决策树

```text
单图 Gemini 是否明显优于 EEVEE？
  ├─ 否 → 优先改善 Blender 材质/灯光，不继续堆 AI
  └─ 是
      ↓
墙体/门洞/家具轮廓是否通过？
  ├─ 否 → 改用 depth ControlNet 或只让 Gemini做 style reference
  └─ 是
      ↓
地板是否保持真实？
  ├─ 否 → Material ID 保护地板 / Gemini不参与正式地板
  └─ 是
      ↓
相邻视角是否一致？
  ├─ 否 → 只进入单图效果图；多视角改为 AI texturing + Blender重渲染
  └─ 是
      ↓
能否脚本化、批处理和记录 metadata？
  ├─ 否 → 只作为人工设计工具
  └─ 是 → 接入 Floor Engine NeuralRenderAttempt
```

---

## 17. M1：只有 M0 通过后才开始项目集成

## 17.1 建议新增目录

```text
blender_neural/
  contracts.py
  plugin_capabilities.py
  pass_bundle.py
  neural_attempts.py
  geometry_gate.py
  floor_gate.py
  panorama_reprojection.py
  surface_bake.py
  adapters/
    nanode.py
    fal_extension.py
    direct_gemini.py
    xyz360_manual.py
  schemas/
    blender_pass_bundle_v1.schema.json
    neural_render_attempt_v1.schema.json
    plugin_capability_v1.schema.json
```

## 17.2 BlenderPassBundle

```json
{
  "schema_version": 1,
  "scene_hash": "...",
  "camera_hash": "...",
  "camera": {},
  "resolution": [0, 0],
  "beauty": {"path": "...", "sha256": "..."},
  "clay": {"path": "...", "sha256": "..."},
  "depth": {"path": "...", "sha256": "...", "near": 0, "far": 0},
  "normal": {"path": "...", "sha256": "...", "space": "world"},
  "object_id": {"path": "...", "sha256": "..."},
  "material_id": {"path": "...", "sha256": "..."},
  "edge": {"path": "...", "sha256": "..."},
  "film_hash": "..."
}
```

## 17.3 NeuralRenderAttempt

```json
{
  "attempt_id": "...",
  "adapter": "nanode|fal_extension|direct_gemini|xyz360_manual",
  "plugin_version": "...",
  "model": "...",
  "pass_bundle_hash": "...",
  "prompt_hash": "...",
  "reference_hashes": [],
  "status": "queued|running|passed|failed|rejected",
  "cost": null,
  "elapsed_seconds": 0,
  "output": {},
  "geometry_gate": {},
  "floor_gate": {},
  "human_review": {}
}
```

## 17.4 API 建议

```text
GET  /api/blender-neural/capabilities
POST /api/blender-neural/pass-bundles
POST /api/blender-neural/attempts/preview
GET  /api/blender-neural/attempts/{id}
POST /api/blender-neural/attempts/{id}/review
POST /api/blender-neural/attempts/{id}/promote-style
POST /api/blender-neural/surface-bake
POST /api/blender-neural/eevee-panorama
```

`xyz360_manual` 第一阶段可以只做导入结果和登记 metadata，不必强行自动控制付费插件。

---

## 18. 前端改造

在当前双轨基础上新增明确的结果类型：

```text
AI 即时效果图
AI 神经 VR 预览
EEVEE 保证型 VR
Cycles 精品终稿
```

结果卡必须显示：

- 来源场景 hash；
- 插件/模型；
- 单图或多视角；
- 是否使用 depth/normal/ID；
- 地板是否受保护；
- 几何门禁；
- 地板门禁；
- API 成本；
- 生成耗时；
- 是否可正式交付。

禁止所有结果都显示成同一个 `360° VR` 标签。

---

## 19. 里程碑

### M0：插件单场景赛马

完成条件：

- 至少跑通 XYZ360 或 Nanode；
- 跑通 EEVEE baseline；
- 跑通一个 depth-conditioned 对照；
- 保存全部输入、输出、时间、成本；
- 用户看到真实结果；
- 选出单图冠军；
- 明确地板是否被修改。

### M1：多视角验证

完成条件：

- 固定同光心 8 个方向；
- 相同风格和参考；
- 跨视角对象身份检查；
- overlap 误差；
- 重投影 ERP；
- 明确神经 VR 是否只能作为预览。

### M2：AI Texturing / Surface Bake

完成条件：

- 至少一面墙 AI 材质回投；
- 多角度融合；
- UV seam 检测；
- 从另一个热点重渲染仍一致；
- 正式地板不经过 AI Base Color。

### M3：EEVEE 正式 ERP

完成条件：

- Blender 原生 2:1 ERP；
- 真实地板；
- 墙体无球形畸变；
- 多热点共享 scene hash；
- Viewer 实机通过；
- 用户肉眼认可。

### M4：Floor Engine 集成

完成条件：

- 插件 capability；
- attempt 状态机；
- 成本确认；
- 结果分类；
- 门禁；
- 历史和恢复；
- 不影响旧功能测试。

### M5：Cycles 精品档

只有当 EEVEE 无法满足特定高端效果时再做：

- 复杂玻璃；
- 镜面；
- 金属；
- 真实间接光；
- 近景微表面；
- Hero 图。

---

## 20. 新电脑第一天执行顺序

1. 校验并解压 `Floor_engine_Linux_DEV_HANDOFF_20260819.tar.gz`；
2. 阅读本文件；
3. 阅读旧版 `docs/CODEX_BLENDER_360VR_IMPLEMENTATION_HANDOFF.md` 的 GeometryManifest 与地板章节；
4. 安装固定 Blender LTS；
5. 记录 GPU/驱动；
6. 创建 M0 固定客厅 `.blend`；
7. 使用原厂彩膜建立真实地板；
8. 安装 Nanode；
9. 如已购买，安装 XYZ360 插件；
10. 安装 fal Extension 作为 ControlNet 对照；
11. 禁止同时安装大量无关插件；
12. 先只跑 16:9 单视角；
13. 保存 A–G 路线结果；
14. 用户肉眼评审；
15. 单图未通过前，不开发 360；
16. 单图通过后再跑 8 方向；
17. 多视角未通过前，不接 Floor Engine 正式 UI；
18. 确认 AI texturing/回投可行后再做 EEVEE ERP 自动化。

---

## 21. 停止条件与换路条件

### XYZ360 停止条件

- 连续多个结果墙体明显漂移；
- 地板无法通过提示词锁定；
- 无法输出 metadata；
- 无脚本化接口且人工成本过高；
- 许可不允许产品化。

停后去向：Nanode 或 direct Gemini adapter。

### Nanode 停止条件

- depth/mist 实际没有提高结构保持；
- 插件不兼容目标 Blender；
- credits/API 成本不可控；
- AI texturing 不能稳定回投；
- 无法关闭不必要数据流。

停后去向：fal ControlNet + 自研 pass bundle。

### Gemini 多视角停止条件

- 家具身份跨视角持续变化；
- overlap 无法融合；
- 地板持续漂移；
- 独立调用无法稳定。

停后去向：Gemini 仅做 Hero Style；多视角交给 ControlNet/MVDiffusion/AI texturing，然后 Blender 重渲染。

### EEVEE 停止条件

不是因为一张图不够“电影感”就停。只有：

- 目标材质/光学确实超出 EEVEE；
- 已完成材质和灯光优化；
- 确认 Cycles 带来肉眼显著收益；
- 客户愿意承担时间和成本。

才升级 Cycles。

---

## 22. 最小成功定义

M0 只有满足以下条件，才能说插件路线成功：

1. 同一个 Blender 场景可重复生成；
2. 单图美感明显高于 EEVEE baseline；
3. 墙角、门窗和家具轮廓没有不可接受漂移；
4. 地板花色、板缝、尺度和方向可被保护或可靠覆盖回来；
5. 生成耗时和成本可记录；
6. 插件/API 稳定；
7. 用户肉眼认可；
8. 失败结果没有被门禁误报成成功。

M1 只有满足以下条件，才能说 Neural VR 预览成功：

1. 同一对象跨 8 方向身份稳定；
2. overlap 区无严重结构跳变；
3. 墙面和地板方向连续；
4. 重投影 ERP 无明显断裂；
5. 结果明确标记预览，不冒充保证型正式图。

正式 VR 只有满足以下条件，才能说最终路线成功：

1. GeometryManifest 锁定；
2. 真实地板由 Blender 渲染；
3. 所有热点共享 scene hash；
4. Blender 原生 ERP；
5. 墙体在 Viewer 中无球形畸变；
6. 用户肉眼认可；
7. 不依赖独立 AI 面拼接。

---

## 23. 参考资料

### Gemini / Blender 插件

- XYZ360 Gemini Nano Banana for Blender：<https://www.xyz360.nl/gemini-nano-banana-for-blender-documentation/>
- Nanode AI Render Engine：<https://github.com/Kovname/nano-banana-render>
- fal.ai Blender Extension：<https://github.com/fal-ai/fal-blender-extension>
- Pallaidium：<https://github.com/tin2tin/Pallaidium>
- Dream Textures：<https://github.com/carson-katri/dream-textures>
- DiffusedTexture：<https://github.com/FrederikHasecke/diffused-texture-addon>
- ComfyUI BlenderAI Node：<https://github.com/AIGODLIKE/ComfyUI-BlenderAI-node>

### 多视角与 360

- MVDiffusion：<https://github.com/Tangshitao/MVDiffusion>
- PanFusion：<https://chengzhag.github.io/publication/panfusion/>

### 官方模型/API

- Gemini Image API：<https://ai.google.dev/gemini-api/docs/image-generation>
- fal Hunyuan World：<https://fal.ai/models/fal-ai/hunyuan_world/image-to-world>
- fal Meshy 6 Image-to-3D：<https://fal.ai/models/fal-ai/meshy/v6/image-to-3d/api>
- Blender LTS：<https://www.blender.org/download/lts/>

---

## 24. 给下一位 Codex 的开工指令

```text
请先完整阅读桌面文件：
FloorEngine_现行Blender_Gemini_Fal_360VR改造指南_20260819.md

本轮不要先改 Floor Engine 主项目，也不要开发完整 Cycles worker。

先执行 M0：
1. 检查新电脑 GPU、驱动和 Blender LTS；
2. 建立固定客厅测试场景；
3. 用 VL88238XL(EIR)-006 原厂彩膜做真实地板；
4. 安装并测试 Nanode；
5. 如用户已购买，测试 XYZ360 Gemini Nano Banana；
6. 安装 fal Blender Extension 做 depth ControlNet 对照；
7. 跑 A–G 赛马；
8. 保存所有输入、输出、时间、成本和插件版本；
9. 做墙体、门窗、家具轮廓和地板门禁；
10. 给用户看真实结果；
11. 单图未通过前不要做 360；
12. 多视角未通过前不要声称 VR 成功；
13. 只有实测证明某插件值得产品化，才实现对应 adapter。
```

---

## 25. 最终原则

> 现在不是先决定“全部用 Blender”或“全部用 Gemini”，而是用一个固定真实场景让 XYZ360、Nanode、fal ControlNet、Gemini Refine 和 EEVEE 公平赛马。Gemini负责最快的审美提升，Blender负责真实几何、地板、热点和球面，EEVEE负责大多数正式终稿，Cycles只负责确实需要的精品图。所有路线都必须通过真实画面、成本、耗时、几何和地板数据证明自己，不能再用宣传图、提示词或测试数量代替最终结果。
