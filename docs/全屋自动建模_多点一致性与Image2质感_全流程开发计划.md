# 全屋自动建模、多点位一致性与 GPT Image 2 质感提升——全流程开发计划

> 文档状态：评审稿 v0.1  
> 编写日期：2026-08-13  
> 适用项目：Floor_engine_Linux  
> 关联文档：[整屋 3D 约束链路](./WHOLE_HOME_3D_PIPELINE.md)、[定点球面全景 AI 生成与一致性方案](./定点球面全景_AI生成与一致性方案.md)  
> 执行边界：本文只是一份开发与验收计划，不代表立即实施。任何代码、依赖、数据迁移、付费 API 测试和 Blender 运行时安装，都应按里程碑单独确认后再执行。

> 2026-08-13 执行状态：用户随后已明确批准第一阶段实施。Plan-to-3D Correspondence Lock v1、CAD/普通户型图配准、共享 GeometryManifest、生产硬门禁、渐进公开测试集与 FZK House L1 同源金标准已完成；实测结果、复现命令和剩余边界见 [第一阶段执行报告](./PLAN_TO_3D_CORRESPONDENCE_LOCK_V1.md)。本文其余阶段仍保持计划状态，不因第一阶段完成而自动启动。

## 1. 文档目的

本计划解决三个连续但职责不同的问题：

1. 从 DWG、DXF、IFC、PDF 或普通户型图中，自动建立可审计的整屋 3D 模型；
2. 从该模型自动规划全景热点并提取球面约束视图，供 GPT Image 2 生成高质感装修预览；
3. 让同一套房在不同热点中保持墙体、门窗、家具、材质和光线一致，并形成可验证的正式 VR 交付物。

目标不是“让提示词尽量像同一套房”，而是建立一条能够明确区分以下两种产品的双轨链路：

- **AI 创意预览**：利用 GPT Image 2 快速探索装修设计、材质语言和画面氛围。强调速度与效果，但不对跨点位的严格几何一致性作承诺。
- **共享 3D 保证终稿**：把确认后的资产、材质和灯光落到同一份 3D 场景中，再由确定性渲染器输出所有热点。正式对客户承诺的一致性来自共享场景，而不是来自提示词。

最终推荐总链路：

~~~text
DWG / DXF / IFC / PDF / PNG / JPG
  → 输入分级与本地解析
  → WholeHomeModel（唯一结构权威）
  → 人工复核与几何锁定
  → 自动热点规划
  → 同光心六通道 cubemap / ERP
  → SceneRecipe（共享资产、材质、灯光）
  ├─ GPT Image 2 → AI 创意预览 → 球面门禁 → 设计确认
  └─ Blender Cycles → 正式 ERP → 跨热点重投影门禁
       → VR Tour → 人工终验 → 历史与审计
~~~

## 2. 顶层路线评估

### 2.1 总体判断

当前顶层路线方向正确，但需要补上一个关键闭环：

> GPT Image 2 不能直接成为多点位正式图的几何权威。它应当负责提出装修外观候选；最终被确认的设计必须转换成共享的资产、材质和灯光配置，再从同一个 3D 场景中确定性渲染全部热点。

原因如下：

- GPT Image 2 支持高质量图像编辑、多参考图和符合约束的灵活输出尺寸，适合把灰模球面图转成写实装修预览。
- OpenAI 官方同时明确列出：模型仍可能在跨多次生成的一致性以及结构化、布局敏感的构图控制上失败。
- GPT Image 的 mask 是提示性指导，不是像素级硬约束。
- 相同提示词、相同模型、相同参考图甚至相同 seed，都不会建立两个相机位置之间的三维对应关系。
- 多热点的一致性要求“同一个三维实体在不同相机下产生正确视差”，这只能由共享几何、共享资产、共享材质和共享灯光严格保证。

参考：

- [OpenAI GPT Image 2 模型页](https://developers.openai.com/api/docs/models/gpt-image-2)
- [OpenAI Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
- [OpenAI GPT Image 提示词指南](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide)

### 2.2 三条路线对比

| 路线 | 单热点球面连续 | CAD 结构保真 | 跨热点同一物体 | 画面质感 | 正式承诺 |
|---|---:|---:|---:|---:|---|
| 每个热点独立调用 GPT Image 2 | 可提高，不能保证 | 只能靠门禁 | 不能保证 | 高 | 不允许 |
| GPT Image 2 预览 + 人工挑图 | 可提高，不能保证 | 只能靠门禁 | 只能人工判断 | 高 | 仅创意预览 |
| GPT Image 2 设计候选 + 共享 3D/Cycles 终稿 | 可严格保证 | 可严格保证 | 可严格保证 | 高 | 推荐 |

### 2.3 不建议优先投入的路线

以下路线可作为研究储备，但不应成为当前产品主线：

- 六个方向分别调用生图模型后再拼接；
- 只靠更长提示词或 seed 锁定相机；
- 从多个独立 AI 全景反推一套完整 3D 家具和遮挡关系；
- 直接用 NeRF 或 Gaussian Splatting 代替已有 CAD 权威模型；
- 在没有真实评测集的情况下训练或集成专用 360 diffusion。

MVDiffusion、PanFusion 等研究证明“显式对应关系”和“球面感知结构”能够提高多视图或全景一致性，但对已有 CAD 的室内项目，仍不如共享 3D 场景直接可靠：

- [MVDiffusion](https://mvdiffusion.github.io/index.html)
- [PanFusion / CVPR 2024](https://openaccess.thecvf.com/content/CVPR2024/papers/Zhang_Taming_Stable_Diffusion_for_Text_to_360_Panorama_Image_Generation_CVPR_2024_paper.pdf)

## 3. 项目现状审计

### 3.1 已有能力

项目已经具备很好的底座，不需要推倒重做。

#### CAD 与户型建模

- 已支持 DWG/DXF 上传和格式校验；
- 当前机器能够检测到 ACadSharp MIT 转换器、ezdxf 1.4.3 和 Shapely 2.1.2；
- DWG 可先本地转换为 ASCII DXF，再进入统一解析；
- 墙线、开口、房间、CAD INSERT、变换矩阵和源 handle 能够保存 provenance；
- CAD 几何已有事实 hash；
- 已有墙/房间反投影误差、覆盖率、外围闭合、房间非重叠、开口绑定和 provenance 覆盖硬门禁；
- 普通户型图已有多轮视觉模型提取、拓扑复核、开口专项复核和人工编辑入口。

#### 共享 3D 与相机

- WholeHomeModel 已使用统一米制、Y-up 坐标；
- 墙、门窗、房间、固定物、语义布局和相机使用同一模型；
- 已有普通透视机位的自动候选生成和语义构图门禁；
- 相机和 capture 都具有来源、hash 和失效机制。

#### 定点球面

- 已实现同光心六面体渲染；
- 已实现 RGB、metric depth、world normal、edge、semantic、subject-ID 六通道；
- 已实现 3×2 atlas、cubemap 与 2:1 ERP 的确定性转换；
- 已固定 3840×1920 GPT Image 2 P0 输入；
- 已接 fal.ai 的 openai/gpt-image-2/edit；
- 已有一次 edit、一次条件式 repair 和付费确认保护；
- 已有 wrap seam、cube edge、结构视图、极点和 opening identity 门禁；
- 已有 VR Viewer、人工 checklist、历史记录和不可变审计字段。

#### 当前验证状态

本计划编写前执行了以下相关测试：

~~~text
tests/test_whole_home_cad.py
tests/test_whole_home_engine.py
tests/test_whole_home_pano.py
tests/test_whole_home_pano_edit.py
tests/test_whole_home_pano_gate.py
tests/test_whole_home_pano_integration.py

结果：164 passed
~~~

### 3.2 真实样例揭示的问题

已有一次 fal.ai GPT Image 2 真实付费测试：

- 输出尺寸正确，为 3840×1920；
- 视觉效果和室内质感明显提升；
- 但球面门禁失败：
  - wrap seam 失败；
  - cube edges 失败；
  - 36 个透视结构检查视图中 34 个失败；
  - opening identity 失败；
  - depth order 当前不可评估；
- 画面中出现了家具、装饰画、电视和墙体关系的创造性重绘。

该记录保存在：

~~~text
data/output_files/定点球面全景/定点球面全景_记录.json
~~~

这一结果证明两件事：

1. GPT Image 2 确实有能力快速提高画面观感；
2. 仅靠当前六通道参考和禁止项提示词，无法把生成模型变成刚性 3D 渲染器。

### 3.3 当前主要缺口

1. 普通 PNG/JPG 户型图仍缺乏可量化的“人工锁定后才生产”分级合同；
2. 尚未支持 IFC；
3. 全景热点需要人工放置，尚无整屋自动热点计划；
4. fixed_objects 中的大量对象是语义代理体，并不是可从背面查看的完整家具资产；
5. 缺少项目级 StyleBible；
6. 缺少共享 SceneRecipe；
7. 当前 GPT Image 2 提示词是单模板，尚未建立消融实验、版本注册和回归评测；
8. 当前球面 gate 是 P0 RGB/结构级，depth order 被明确标记为不可评估；
9. 尚无跨热点三维重投影门禁；
10. 尚无正式写实渲染后端；
11. 当前系统没有 Blender 运行时，且 blender 不在 PATH 中；
12. 历史记录尚未在产品层明确区分“AI 创意预览”和“保证终稿”。

## 4. 产品定义与验收等级

### 4.1 输入等级

所有项目必须记录 input_grade。

#### vector_authoritative

适用于：

- DWG；
- DXF；
- 满足子集要求的 IFC。

含义：

- 几何来自矢量或 BIM 权威数据；
- 通过本地硬门禁后可进入生产；
- AI 只能补语义，不得改变已冻结结构事实。

#### raster_assisted

适用于：

- PNG；
- JPG；
- 扫描 PDF；
- 只含光栅页面的 PDF；
- 低质量或缺失尺寸的户型图。

含义：

- AI 与本地图像算法只生成草稿；
- 必须人工确认比例、墙、房间、门窗和异常区域；
- 人工锁定后才允许进入热点、付费生图和正式渲染；
- 历史中永久保留 raster_assisted 标签，不能伪装成施工级模型。

### 4.2 输出等级

#### creative_preview

用于：

- 快速装修方向探索；
- 材质和色调提案；
- 客户早期沟通；
- 选定资产与风格。

允许：

- GPT Image 2 整张 ERP 编辑；
- 一次受控接缝修补；
- 多个候选；
- 失败后重新选择设计方向。

不得宣称：

- 多热点是严格同一套三维家具；
- 遮挡和背面一定正确；
- 可作为测量或施工依据；
- mask 外绝不改变。

#### guaranteed_cycles

用于：

- 正式 VR Tour；
- 多点位交付；
- 对客户承诺一致性的结果。

要求：

- 所有热点来自同一 SceneRecipe；
- 所有资产、材质、灯光和模型 hash 相同；
- 最终 ERP 由共享 3D 场景确定性渲染；
- 正式文件不再经过 GPT Image 2 修改；
- 跨热点几何、实例和材质重投影门禁全部通过。

## 5. 目标数据架构

### 5.1 WholeHomeModel v3

保留现有字段并新增以下概念：

~~~json
{
  "schema_version": 3,
  "model_id": "model_xxx",
  "input_grade": "vector_authoritative",
  "coordinate_system": "metres-y-up",
  "stories": [],
  "walls": [],
  "openings": [],
  "rooms": [],
  "surfaces": [],
  "fixed_objects": [],
  "asset_instances": [],
  "material_assignments": [],
  "cameras": [],
  "source_refs": {},
  "model_facts_hash": ""
}
~~~

#### stories

v1 只允许一个 active story：

~~~json
{
  "story_id": "story_ground",
  "name": "一层",
  "elevation_m": 0,
  "height_m": 2.8,
  "active": true
}
~~~

多层、跃层、复杂楼梯、错层超过容差时必须 fail closed，留待后续专项版本。

#### surfaces

为每个可见表面建立稳定身份：

~~~json
{
  "surface_id": "surface_wall_001_inside_a",
  "owner_type": "wall",
  "owner_id": "wall_001",
  "surface_role": "interior_finish",
  "room_ids": ["room_living"],
  "material_id": "mat_wall_warm_white"
}
~~~

需要区分：

- 墙体两侧；
- 地面；
- 天花；
- 柜体面；
- 台面；
- 门窗框；
- 玻璃。

#### asset_instances

~~~json
{
  "instance_id": "instance_sofa_living_01",
  "asset_id": "asset_sofa_modular_2300",
  "semantic_role": "sofa",
  "room_id": "room_living",
  "position_m": {"x": 4.2, "y": 0, "z": 3.1},
  "rotation_y_deg": 180,
  "scale": {"x": 1, "y": 1, "z": 1},
  "bounds_m": {"x": 2.3, "y": 0.85, "z": 0.95},
  "source": "human_selected",
  "license_status": "approved"
}
~~~

### 5.2 SceneRecipe

SceneRecipe 是装修与渲染的唯一外观权威，不和结构模型混成一个 hash。

~~~json
{
  "schema_version": 1,
  "recipe_id": "recipe_xxx",
  "recipe_revision": 1,
  "project_id": "home_xxx",
  "model_facts_hash": "",
  "style_bible": {},
  "asset_instances": [],
  "material_assignments": [],
  "lighting_rig": {},
  "asset_manifest_hash": "",
  "material_graph_hash": "",
  "lighting_hash": "",
  "scene_hash": "",
  "status": "draft"
}
~~~

状态：

- draft：允许编辑；
- reviewed：设计已人工确认，但仍可创建新 revision；
- locked：用于正式渲染，不允许原地修改；
- superseded：存在更新 revision，但历史仍可重建。

### 5.3 StyleBible

StyleBible 不是一段散文，而是结构化合同：

~~~json
{
  "style_id": "modern_warm_v1",
  "design_language": "现代温暖、克制、真实居住感",
  "palette": {
    "wall": "#E9E2D8",
    "wood": "#9A7657",
    "stone": "#C6C1B8",
    "metal": "#2F3032",
    "accent": "#7C8872"
  },
  "materials": [],
  "furniture_family": [],
  "lighting": {
    "daylight": "soft_overcast",
    "artificial_temperature_k": 3000,
    "white_balance_k": 4800
  },
  "realism": {
    "wear_level": "subtle",
    "clutter_level": "low",
    "surface_perfection": "natural_imperfections"
  },
  "forbidden": []
}
~~~

原则：

- 公共地板、公共木作和主墙漆只能有一个项目级定义；
- 房间可以覆盖软装或局部重点色，但不能自行发明另一套公共材质；
- 每个字段变化都会改变 material_graph_hash 或 lighting_hash；
- 提示词只是 StyleBible 的一个消费者，Cycles 同样消费它。

### 5.4 资产库

正式资产使用 GLB/glTF 2.0。

每个资产必须记录：

- asset_id；
- 原始文件与 SHA-256；
- 来源和许可证；
- 商业使用许可状态；
- 实际尺寸；
- 坐标轴和 pivot；
- 包围盒；
- 语义角色；
- 推荐房型；
- mesh 数量和三角形数量；
- PBR 纹理列表；
- 是否包含背面和底面；
- LOD；
- 缩略图；
- 校验报告。

PBR 最小合同：

- baseColor；
- roughness；
- metallic；
- normal；
- ambient occlusion；
- 可选 transmission、clearcoat、sheen。

参考：

- [Khronos glTF 2.0 Registry](https://registry.khronos.org/glTF/)
- [Khronos glTF PBR](https://www.khronos.org/gltf/pbr)
- [Khronos Asset Creation Guidelines 2.0](https://www.khronos.org/blog/introducing-asset-creation-guidelines-2.0-siggraph-2025)

## 6. 自动建模实施方案

### 6.1 DWG/DXF 路线

保留当前实现，只做合同升级和测试扩展：

1. 上传文件和魔数校验；
2. DWG 通过 ACadSharp 本地转换为 DXF；
3. ezdxf 展开实体与 INSERT；
4. Shapely 构建墙、房间和空间；
5. 多套平面候选明确选择；
6. 冻结 CAD handle、INSERT 链、单位和变换；
7. Gemini 仅补语义；
8. 本地 hard gate；
9. 人工复核；
10. 锁定模型。

必须保持的门禁：

- 单位明确；
- cad_derivation_coverage = 1.0；
- provenance coverage = 100%；
- 墙边界反投影 p95 ≤ 0.05m；
- 房间边界反投影 p95 ≤ 0.05m；
- room coverage ≥ 0.98；
- outer wall closed = true；
- room overlap area ≤ 1e-6m²；
- 所有 opening 绑定有效 wall；
- facts hash 在语义补全前后不变。

### 6.2 IFC 路线

新增 IFC 为独立权威输入，不先转成二维图再识别。

建议使用 IfcOpenShell：

- [buildingSMART IFC 4.3](https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/index.html)
- [IfcSpace 定义](https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcSpace.htm)
- [IfcRelSpaceBoundary 定义](https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcRelSpaceBoundary.htm)
- [IfcOpenShell 文档](https://docs.ifcopenshell.org/)

v1 支持子集：

- IfcProject；
- IfcSite；
- IfcBuilding；
- IfcBuildingStorey；
- IfcSpace；
- IfcWall / IfcWallStandardCase；
- IfcSlab；
- IfcDoor；
- IfcWindow；
- IfcOpeningElement；
- IfcColumn；
- IfcFurniture；
- IfcRelSpaceBoundary；
- IfcRelVoidsElement；
- IfcRelFillsElement。

导入规则：

1. 读取项目单位；
2. 计算完整 placement chain；
3. 选择一个 storey；
4. 提取空间边界和元素几何；
5. 转换到项目米制 Y-up；
6. 保留 IFC GUID；
7. 对门窗优先使用关系图而不是只靠空间相交；
8. 生成 WholeHomeModel；
9. 运行和 CAD 相同的拓扑门禁；
10. 生成 IFC→Model 对照报告。

阻断条件：

- 多楼层未选择；
- 单位缺失或异常；
- storey elevation 冲突；
- space 大面积重叠；
- opening 关系断裂；
- mesh 无法生成；
- curved/NURBS 几何超出 v1 支持；
- 全局坐标过大但没有可靠本地转换；
- 模型只含展示 mesh、缺乏可关联的建筑元素。

### 6.3 PNG/JPG/PDF 路线

目标不是宣称全自动施工级建模，而是提高草稿效率并形成可验证的人机闭环。

#### 预处理

1. PDF 页面枚举和缩略图；
2. 用户选择目标页；
3. 页面或图像自动裁边；
4. 透视纠偏；
5. 旋转归正；
6. 二值/灰度/彩色多版本；
7. OCR 提取尺寸和房间文字；
8. 检测比例尺和已知尺寸；
9. 保存所有预处理版本与 hash。

#### 多候选提取

至少生成三类独立证据：

- 本地线条、轮廓和形态学结果；
- 视觉模型结构 JSON；
- OCR 尺寸和文字证据。

视觉模型继续执行：

1. 整屋墙图提取；
2. 拓扑叠图复核；
3. 门窗专项复核；
4. 语义对象补全。

#### 人工锁定

页面必须逐项确认：

- 总体尺度；
- 外围墙；
- 共墙；
- 每个房间 polygon；
- 每个门、窗和 open connection；
- 柱和楼梯；
- 固定物；
- 无法识别区域。

未经确认：

- verified=false；
- 不允许自动热点 commit；
- 不允许付费生图；
- 不允许正式 Cycles render。

#### 关于专用 floorplan 模型

RoomFormer 等研究可作为离线候选生成器研究，但不能直接假设适配普通房产户型图。RoomFormer 原任务以 3D 扫描生成的 top-down density map 为主，需要先用自有数据进行域评测。[RoomFormer](https://github.com/ywyue/roomformer)

CubiCasa5K 数据集可用于理解任务和设计评测标签，但其 CC BY-NC 4.0 许可不适合直接打包进商业产品。[CubiCasa5K](https://github.com/CubiCasa/CubiCasa5k)

因此第一版不引入新训练模型作为硬依赖，先用现有视觉模型 + 本地几何 + 人工锁定建立可靠基线。

## 7. 自动热点规划

### 7.1 目标

输入一个已锁定 WholeHomeModel，自动输出：

- 每个房间的候选热点；
- 推荐热点；
- 淘汰理由；
- 可见开口和对象；
- 整屋覆盖率；
- 需要人工处理的房间。

### 7.2 HotspotPlan 合同

~~~json
{
  "schema_version": 1,
  "plan_id": "panoplan_xxx",
  "project_id": "home_xxx",
  "model_facts_hash": "",
  "algorithm_version": "whole-home-pano-hotspot-v1",
  "candidates": [],
  "selected": [],
  "coverage_report": {},
  "status": "preview"
}
~~~

候选字段：

~~~json
{
  "candidate_id": "hotspot_candidate_xxx",
  "room_id": "room_living",
  "camera_center_m": {"x": 4.2, "y": 1.55, "z": 3.8},
  "heading_deg": 15,
  "safety": {},
  "visibility": {},
  "score": 87.2,
  "reasons": []
}
~~~

### 7.3 候选生成算法

1. 获取房间 polygon；
2. 按墙厚、相机中心球和安全余量向内收缩；
3. 减去 fixed object footprint、asset footprint、门扇摆动区；
4. 计算 medial axis 或近似最大内接圆中心；
5. 追加自适应网格和房间质心候选；
6. 眼高默认 1.55m，可按项目覆盖；
7. 对每个候选运行 360° 快速语义渲染；
8. 统计墙、地、天花、opening、required object 和未知区域的角度覆盖；
9. 运行碰撞和离墙门禁；
10. 排序并用 set-cover 选择最少热点覆盖必要实体。

### 7.4 数量规则

- 普通封闭房间：一个主热点；
- 面积大于 30m²的开放区：最多两个；
- 长宽比大于 2.5且单点覆盖不足：允许两个；
- 同房间热点距离至少 1.5m；
- 只有在增加第二热点能显著提高必要对象/开口覆盖时才添加；
- 狭小卫生间若无安全点，明确 blocked，不把相机放进墙或洁具。

### 7.5 评分建议

~~~text
总分 =
  30% 必要对象覆盖
  20% 开口覆盖
  15% 最小墙距
  10% 地面可见比例
  10% 深度层次
  10% 视点居中与可读性
   5% 与其他热点的覆盖互补
~~~

硬失败不能被总分抵消。

### 7.6 人工确认

自动计划先 preview：

- 展示平面位置；
- 展示 360 semantic 预览；
- 展示覆盖对象和缺失对象；
- 允许移动、替换或取消；
- commit 后生成不可变 plan revision。

## 8. 球面约束提取

### 8.1 服务端与浏览器职责

浏览器：

- 交互式放置和预览；
- 人工调整热点；
- 快速 WebGL 检查；
- Viewer 验收。

服务端：

- 从已 commit 热点批量生成确定性参考；
- 统一 face basis；
- 统一 near/far；
- 保存通道、manifest 和 hash；
- 作为 CI 和无浏览器环境的可复现基线。

### 8.2 通道合同

每个热点输出：

1. RGB clay；
2. linear metric depth；
3. world-space normal；
4. edge；
5. semantic；
6. subject-ID；
7. 可选 material-ID；
8. 可选 surface-ID。

所有通道必须：

- 使用同一个 camera center；
- 使用固定 face order；
- 使用相同几何 revision；
- 使用相同 near/far；
- 使用相同 ERP 映射；
- 单独保存 SHA-256；
- 由 source_hash 总体绑定。

### 8.3 分辨率层级

| 用途 | Cube face | ERP | 说明 |
|---|---:|---:|---|
| 热点评分 | 256–512 | 1024×512 / 2048×1024 | 快速、本地 |
| AI 预览 | 1024 左右 | 3840×1920 | GPT Image 2 |
| Cycles 草稿 | 1024 | 4096×2048 | 设计确认 |
| 正式交付 | 2048–4096 | 8192×4096 | 视硬件和材质决定 |

### 8.4 PanoManifest v2

在现有 v1 基础上增加：

- hotspot_plan_id；
- scene_recipe_id；
- scene_hash；
- renderer；
- renderer_version；
- render_settings_hash；
- color_management；
- asset_manifest_hash；
- material-ID/surface-ID 通道；
- input_grade；
- output_grade。

任何相关 hash 改变，旧 capture 自动 stale。

## 9. GPT Image 2 提示词与预览实验系统

### 9.1 能力边界

GPT Image 2 适合：

- 灰模写实化；
- 材质语言探索；
- 灯光氛围；
- 产品参考合成；
- 风格保持；
- 快速多候选。

不适合承担：

- 相机矩阵的数学锁定；
- 多热点实体背面推理；
- 精确三维遮挡；
- 施工级几何；
- 跨请求绝对一致性。

官方规格与当前 fal 线路：

- GPT Image 2 支持多参考图和自定义合规尺寸；
- 3840×1920 满足 2:1、边长、16 像素倍数和总像素约束；
- 超过常规 2K 的输出仍应作为实验性高分辨率评测；
- fal edit 端点允许最多 16 张参考图；
- fal 线路当前使用 alias，历史中必须记录 snapshot_locked=false；
- OpenAI 固定快照只在直连且实际指定 gpt-image-2-2026-04-21 时记录。

参考：[fal GPT Image 2 Edit API](https://fal.ai/models/openai/gpt-image-2/edit/api)

### 9.2 Prompt Registry

新增版本化提示词注册表：

~~~json
{
  "prompt_id": "image2-interior-erp-v2",
  "version": 2,
  "status": "challenger",
  "sections": [],
  "input_bundle": "bundle_b",
  "model_family": "gpt-image-2",
  "created_at": "",
  "evaluation_summary": {}
}
~~~

状态：

- draft；
- challenger；
- champion；
- retired。

发布 champion 不能原地改文案，只能创建新版本。

### 9.3 提示词固定结构

提示词采用短标签分段，顺序固定。

#### TASK_AND_PROJECTION

说明：

- 只编辑 Image 1；
- 完整单目 equirectangular；
- 水平 360°、垂直 180°；
- 左右边界相邻；
- 输出一张完整 2:1 图。

#### INPUT_ROLES

逐图声明：

- Image 1：唯一编辑画布；
- Image 2：metric depth；
- Image 3：world normal；
- Image 4：edge；
- Image 5：semantic；
- Image 6：subject-ID；
- 其他图：只作为批准的材质或产品参考。

#### PROJECT_STYLE_BIBLE

从 StyleBible 编译：

- 全屋风格；
- 公共色板；
- 地板；
- 木作；
- 金属；
- 石材；
- 织物；
- 家具系列；
- 灯光；
- 真实瑕疵。

#### ROOM_INTENT

只写当前房间差异：

- 房间功能；
- 重点对象；
- 局部软装；
- 局部颜色；
- 不覆盖项目级公共材质。

#### MATERIAL_REALISM

使用物理可观察描述：

- 木纹尺度符合真实板材；
- 粗糙度有轻微变化；
- 石材不是无穷重复纹理；
- 织物有纤维和自然折痕；
- 玻璃有正确反射和透射；
- 金属高光不溢出；
- 墙漆保留轻微橘皮和不完全均匀；
- 地板拼缝连续且比例真实。

#### LIGHTING

固定：

- 主要窗光方向；
- 日光条件；
- 人工灯色温；
- 曝光；
- 白平衡；
- 阴影软硬；
- 全球面同一时间状态。

#### PRESERVE

明确重复：

- 保留相机位置；
- 保留投影；
- 保留所有墙、门窗和柱；
- 保留对象数量、位置、轮廓和遮挡；
- 保留直线建筑边；
- 保留地面标高；
- 保留左右闭环。

#### FORBID

禁止：

- 新增、删除、移动结构；
- 复制家具；
- 镜像开口；
- 创造隐藏房间；
- 改变相机高度；
- 画框、标签、文字、网格、分栏；
- 过度 HDR；
- 过度锐化；
- 塑料材质；
- 无尺度的噪声纹理；
- 不同方向出现不同时间或光源。

#### OUTPUT_CONTRACT

要求：

- 3840×1920；
- PNG；
- 单张；
- 无透明背景；
- 无额外说明。

### 9.4 输入消融

不能默认“输入越多越好”，需要固定三组：

| 组 | 输入 | 目的 |
|---|---|---|
| A | RGB | 最低干扰基线 |
| B | RGB + edge + semantic + subject-ID | 结构身份基线 |
| C | RGB + depth + normal + edge + semantic + subject-ID | 完整约束 |

每组可增加不超过四张批准参考：

- 地板产品；
- 木作；
- 主要家具；
- 整体风格。

全部输入最多 10 张，以免信息角色竞争。每个输入在 prompt 中必须有编号和用途。

### 9.5 生成与修复政策

每个候选：

- 一次主 edit；
- 本地 gate；
- 只有失败集合完全属于 wrap_seam/cube_edges 时允许一次 repair；
- structure_views、opening identity 或 object identity 失败时不得用接缝 repair 掩盖；
- repair 后重新运行全部 gate；
- 仍失败就淘汰；
- 不允许无限迭代。

### 9.6 质感评测维度

结构门禁通过后才评质感：

- 材质真实尺度；
- 表面粗糙度层次；
- 光线方向一致；
- 高光与阴影可信；
- 色彩和白平衡；
- 家具产品保真；
- 地板与墙面的接缝质量；
- 塑料感；
- 过度磨皮；
- 重复纹理；
- 生活感；
- 整体设计完成度。

## 10. SceneRecipe 与 AI 设计回写

### 10.1 原则

AI 预览不直接成为正式场景。设计确认后需要完成“设计解析”：

~~~text
AI 预览
  → 人工确认设计意图
  → 解析公共材质
  → 选择真实 PBR 材质
  → 选择完整 GLB 家具资产
  → 确认摆位与尺寸
  → 设置灯光
  → SceneRecipe reviewed
  → SceneRecipe locked
~~~

### 10.2 自动与人工边界

可以自动建议：

- 色板；
- 材质类别；
- 资产搜索关键词；
- 灯光色温；
- 相似资产候选；
- 房间软装清单。

必须人工确认：

- 具体产品资产；
- 真实尺寸；
- 资产摆位；
- 公共材质；
- 玻璃、镜面和金属；
- 版权和商业许可；
- 最终灯光方向。

### 10.3 回写验证

每次 SceneRecipe 更新后：

- 重新计算 asset bounds；
- 运行房间内约束；
- 运行对象碰撞；
- 运行门扇净空；
- 运行开口遮挡；
- 运行热点安全；
- 生成低分辨率全景；
- 运行 subject-ID 覆盖；
- 生成差异报告。

## 11. Blender Cycles 正式渲染

### 11.1 为什么选择 Cycles

Three.js 继续用于交互和快速预览；Cycles 用于正式终稿，原因：

- 原生支持 equirectangular 360×180 相机；
- PBR 与全局光照能力成熟；
- 可输出 EXR 和多种 AOV；
- 可无头运行；
- 可锁定版本和渲染设置；
- 更适合 8K 离线终稿。

Blender 官方说明 Cycles 全景相机可从相机位置渲染完整 360×180 equirectangular 视图：[Blender Panoramic Cameras](https://docs.blender.org/manual/sr/4.2/render/cycles/object_settings/cameras.html)

### 11.2 运行时管理

不能依赖用户系统 PATH。

计划引入：

- 应用管理的 Blender 4.5 LTS 固定补丁版本；
- Windows 与 Linux 分别配置 runtime；
- runtime SHA-256；
- 启动时 capability probe；
- 明确的许可证与第三方声明；
- 可配置外部 Blender 路径，但必须通过版本检查。

当前环境没有检测到 blender，因此实施该阶段前要单独完成安装、体积、更新和打包评审。

capability 返回：

~~~json
{
  "available": false,
  "version": "",
  "path": "",
  "cycles_devices": [],
  "gpu_ready": false,
  "cpu_ready": false,
  "reason": "runtime_missing"
}
~~~

### 11.3 场景构建

从 WholeHomeModel 和 SceneRecipe 创建一次整屋场景：

1. 建立墙、地板、天花和开口 mesh；
2. 为 surface 绑定 material；
3. 导入 GLB 资产；
4. 应用统一单位、坐标轴、pivot 和变换；
5. 建立灯具与窗光；
6. 建立每个热点相机；
7. 保存 scene manifest；
8. 计算 scene hash；
9. 先渲染低分辨率审计；
10. 通过后才运行正式分辨率。

禁止为每个热点分别重建或重新随机化场景。

### 11.4 渲染设置

第一版默认：

- renderer：Cycles；
- projection：equirectangular；
- 正式尺寸：8192×4096；
- 草稿尺寸：4096×2048；
- samples：按基准确定，不在代码中静默改变；
- adaptive sampling：固定合同；
- denoise：固定合同；
- seed：固定；
- motion blur：关闭；
- 动态纹理：关闭；
- scene time：固定；
- 光源：固定；
- transparent background：关闭；
- EXR：half-float scene-linear；
- Web 交付：sRGB PNG/JPEG；
- view transform：AgX；
- exposure、white balance：来自 SceneRecipe。

Blender 建议中间生产结果使用 scene-linear OpenEXR，普通显示文件再应用显示变换：[Blender Color Management](https://docs.blender.org/manual/en/latest/render/color_management.html)

### 11.5 AOV

每个热点至少保存：

- beauty；
- Z/depth；
- world normal；
- object/subject ID；
- material ID；
- diffuse color/albedo；
- optional direct/indirect light。

这些通道同时用于：

- 跨热点一致性；
- 问题定位；
- 材质回归；
- 后续非生成式调色；
- 可追溯交付。

### 11.6 GPU 与 CPU 回退

运行顺序：

1. 探测支持的 Cycles GPU；
2. 小场景 smoke；
3. 显存预算评估；
4. GPU 正式渲染；
5. GPU OOM 时允许创建新的 CPU fallback attempt；
6. 不在同一个 attempt 中静默切换；
7. 每次 attempt 单独记录设备、耗时和 hash。

若 CPU 预计超出配置超时：

- 返回 blocked；
- 保留已完成热点；
- 允许用户降低草稿分辨率；
- 正式 8K 不自动降级成较低分辨率并冒充成功。

## 12. 多点位一致性保证

### 12.1 保证对象

多点位一致性分成四层：

1. **结构一致**：墙、门、窗、柱和层高；
2. **实体一致**：同一个 sofa instance、柜体和灯具；
3. **材质一致**：同一个 surface/material 在所有热点一致；
4. **光照一致**：同一光源、时间、曝光和阴影关系。

### 12.2 Hash 门禁

同一正式 render run 内：

- model_facts_hash 必须相同；
- scene_hash 必须相同；
- asset_manifest_hash 必须相同；
- material_graph_hash 必须相同；
- lighting_hash 必须相同；
- render_settings_hash 必须相同；
- Blender runtime hash 必须相同。

任意一个不同，不能组成同一套正式 VR Tour。

### 12.3 跨热点重投影

对热点 A 的每个有效像素：

1. 读取 ERP 射线方向；
2. 用 metric depth 恢复世界点；
3. 用热点 B 的中心计算新方向；
4. 投影到 B 的 ERP；
5. 比较 B 的 depth；
6. 过滤被 B 中更近表面遮挡的点；
7. 比较 subject-ID；
8. 比较 material-ID；
9. 统计角度和深度误差。

排除：

- 透明/半透明材质；
- 镜面和折射对象；
- 对象轮廓边缘两像素或等效角度带；
- B 中不可见或被遮挡的点；
- 极点采样不稳定带。

### 12.4 初始门槛

这些阈值必须通过 Gold Set 标定并版本化，第一版建议：

- subject-ID 一致率 ≥ 99.5%；
- material-ID 一致率 ≥ 99.5%；
- depth p95 ≤ 0.05m；
- angular reprojection p95 ≤ 0.25°；
- missing/extra required object = 0；
- scene/hash mismatch = 0。

阈值变更必须升级 gate version，不得改写旧历史结论。

### 12.5 AI 预览的跨热点检查

AI 预览无法输出可信 depth 和 ID，因此只能做：

- 人工 side-by-side；
- 图像特征和语义辅助；
- 公共对象参考匹配；
- 材质色差；
- 灯光方向；
- duplicated/missing object；
- 结构 gate。

结果只能命名为 preview_consistency_score，不能命名为 guaranteed_consistency。

## 13. API 草案

以下只定义计划接口，实施时仍需按阶段评审。

### 13.1 项目输入

扩展：

~~~text
POST /api/whole-home/projects
~~~

几何来源只允许一个：

- floorplan_path；
- import_analysis_id；
- cad_path；
- ifc_path。

### 13.2 热点计划

~~~text
POST /api/whole-home/projects/{project_id}/pano-hotspot-plans/preview
GET  /api/whole-home/projects/{project_id}/pano-hotspot-plans/{plan_id}
POST /api/whole-home/projects/{project_id}/pano-hotspot-plans/{plan_id}/commit
~~~

preview 不产生付费调用。

### 13.3 SceneRecipe

~~~text
POST /api/whole-home/projects/{project_id}/scene-recipes
GET  /api/whole-home/projects/{project_id}/scene-recipes/{recipe_id}
PUT  /api/whole-home/projects/{project_id}/scene-recipes/{recipe_id}
POST /api/whole-home/projects/{project_id}/scene-recipes/{recipe_id}/review
POST /api/whole-home/projects/{project_id}/scene-recipes/{recipe_id}/lock
~~~

锁定后不能原地修改，只能 fork revision。

### 13.4 资产

~~~text
POST /api/whole-home/assets
GET  /api/whole-home/assets
GET  /api/whole-home/assets/{asset_id}
POST /api/whole-home/assets/{asset_id}/validate
~~~

### 13.5 Prompt Lab

~~~text
GET  /api/whole-home/image2-prompts
POST /api/whole-home/image2-prompts
POST /api/whole-home/image2-evals/preview
POST /api/whole-home/image2-evals/commit
GET  /api/whole-home/image2-evals/{eval_id}
~~~

commit 必须沿用现有付费确认、调用上限和不可重放政策。

### 13.6 双轨 render run

~~~text
POST /api/whole-home/projects/{project_id}/pano-render-runs
GET  /api/whole-home/projects/{project_id}/pano-render-runs/{run_id}
POST /api/whole-home/projects/{project_id}/pano-render-runs/{run_id}/cancel
GET  /api/whole-home/projects/{project_id}/pano-render-runs/{run_id}/consistency-report
~~~

请求：

~~~json
{
  "mode": "creative_preview",
  "scene_recipe_id": "recipe_xxx",
  "hotspot_plan_id": "panoplan_xxx",
  "prompt_id": "image2-interior-erp-v2",
  "resolution": "3840x1920"
}
~~~

或：

~~~json
{
  "mode": "guaranteed_cycles",
  "scene_recipe_id": "recipe_xxx",
  "hotspot_plan_id": "panoplan_xxx",
  "resolution": "8192x4096"
}
~~~

## 14. 前端计划

### 14.1 项目向导

步骤：

1. 选择输入类型；
2. 显示 input_grade；
3. 解析与诊断；
4. 几何编辑；
5. 人工锁定；
6. 自动热点；
7. SceneRecipe；
8. AI 预览或 Cycles 终稿；
9. VR Tour 验收。

### 14.2 自动热点页面

展示：

- 平面图热点；
- 每个候选分数；
- 安全门禁；
- 360 semantic 小预览；
- opening/object 覆盖；
- 被阻断房间；
- 人工替换。

### 14.3 SceneRecipe 页面

包含：

- 全屋 StyleBible；
- 材质板；
- 资产库；
- 2D/3D 摆位；
- 碰撞和门扇警告；
- 灯光设置；
- revision 对比；
- review/lock。

### 14.4 双轨生成页面

必须显著区分：

- 紫色或其他固定标识：AI 创意预览；
- 绿色或其他固定标识：共享 3D 保证终稿。

AI 预览显示：

- provider；
- model ID；
- snapshot 是否锁定；
- prompt version；
- gate level；
- 不可评估项目。

Cycles 终稿显示：

- scene hash；
- Blender 版本；
- device；
- samples；
- AOV；
- consistency report；
- gate version。

### 14.5 历史记录

新增：

- output_grade；
- input_grade；
- model/scene/hash；
- prompt version；
- renderer/runtime；
- gate level；
- consistency report；
- 设计预览与正式终稿的父子关系。

历史中不得：

- 把 AI 门禁通过写成“多点位保证通过”；
- 把 fal alias 写成 snapshot locked；
- 把降低分辨率的 fallback 写成 8K 成功；
- 覆盖旧记录。

## 15. 评测体系

### 15.1 Gold Set v1

建立自有或已获得商业许可的数据集：

- 8 个住宅项目；
- 32 个热点；
- 4 个矢量项目；
- 4 个栅格/PDF 项目；
- 每个项目至少包含：
  - 卧室；
  - 客餐厅；
  - 厨房或卫生间；
  - 走廊/玄关；
  - 开放空间交界；
  - 多个门窗；
  - 至少一个跨热点公共对象。

标注：

- 墙；
- 房间；
- 开口；
- 固定物；
- 资产实例；
- 材质；
- 热点；
- 人工质感评分；
- 预期失败项。

数据必须保存：

- 来源；
- 许可证；
- 是否允许训练；
- 是否允许商业评测；
- 是否允许发布截图。

### 15.2 自动建模指标

矢量：

- provenance coverage；
- wall boundary p95；
- room boundary p95；
- opening precision/recall；
- room coverage；
- topology hard-pass；
- 人工修正次数；
- 自动锁定率。

栅格：

- wall F1；
- room polygon IoU；
- opening precision/recall；
- 尺度误差；
- hard error 数；
- 人工操作数量；
- 锁定耗时。

栅格不追求虚假的 100% 自动率，优先降低漏墙和错门窗。

### 15.3 热点指标

- 每房成功规划率；
- 必要对象覆盖率；
- opening 覆盖率；
- 安全门禁失败率；
- 人工替换率；
- 每项目热点数量；
- 计划耗时。

### 15.4 GPT Image 2 评测

#### 快速 smoke

- 12 个热点；
- 每个 prompt/input bundle 一次；
- 目的：淘汰明显失败模板。

#### Challenger

- 24 个热点；
- 每模板两次；
- 目的：比较结构通过率、成本和质感。

#### 发布复验

- 32 个热点；
- 胜出模板每热点三次；
- 目的：评估稳定性而不是最好样例。

记录：

- 首轮 hard-pass；
- repair 后 hard-pass；
- structure/opening/object 错误；
- seam/pole 错误；
- 人工质感盲评；
- 调用次数；
- 耗时；
- token/费用；
- provider request ID；
- 模型 ID；
- prompt version；
- 输入 bundle。

发布目标建议：

- 简单/中等组首轮 hard-pass ≥ 70%；
- 一次允许 repair 后 ≥ 80%；
- 困难组最终 hard-pass ≥ 50%；
- 任何结构失败候选不得进入质感冠军评选；
- 未达到目标时继续作为实验功能，不能成为默认交付。

### 15.5 Cycles 与一致性指标

- 同 scene 重渲染结构/AOV hash 稳定；
- 所有热点 scene hash 相同；
- subject-ID 一致率；
- material-ID 一致率；
- depth p95；
- angular p95；
- missing/extra object；
- seam/pole；
- 8K 渲染耗时；
- GPU 显存；
- CPU fallback 耗时；
- Viewer 加载时间。

正式发布要求所有硬门禁 100% 通过，不采用平均分掩盖单个坏热点。

## 16. 测试计划

### 16.1 模型与迁移

- v2 项目加载为 v3 runtime copy；
- 不静默改写旧 JSON；
- 新增字段 hash 稳定；
- v1/v2 pano 历史仍可查看；
- v2 项目修改材质不会改变 CAD facts hash；
- 修改结构会使相关 capture 和 render stale。

### 16.2 IFC

- IFC2X3、IFC4、IFC4X3；
- mm、cm、m；
- nested placement；
- rotated storey；
- door/window void/fill；
- missing space；
- duplicate GUID；
- multi-storey；
- curved wall；
- broken geometry；
- unsupported element；
- 文件过大和路径安全。

### 16.3 栅格

- 清晰 PNG；
- 手机拍照透视；
- 扫描 PDF；
- 多页 PDF；
- 多套平面同页；
- 中文/英文标注；
- 无尺寸；
- 比例尺；
- 异形墙；
- 楼梯；
- 柱；
- 门窗符号不清；
- 人工锁定前禁止后续流程。

### 16.4 热点

- 普通方形房间；
- 狭窄卫生间；
- 家具占满；
- 门口交界；
- 开放客餐厅；
- 长走廊；
- 多热点最小距离；
- set-cover；
- required object 不可见；
- 人工修改后重新 commit。

### 16.5 提示词与付费保护

- prompt snapshot；
- 输入角色顺序；
- bundle A/B/C；
- 超过参考图上限；
- fal alias；
- OpenAI snapshot；
- preview 不付费；
- confirmation 绑定 source hash；
- 一次 edit；
- 一次 repair；
- 进程重启不重复付费；
- queue 状态未知时不重交；
- provider 429/5xx；
- moderation error 不盲目重试。

### 16.6 Cycles

- runtime 缺失；
- 版本不符；
- runtime hash 不符；
- GPU 无设备；
- GPU smoke 失败；
- OOM；
- CPU fallback；
- cancel；
- crash recovery；
- EXR/AOV 完整；
- 4096/8192 分辨率；
- equirectangular 轴向；
- AgX；
- 随机种子和动态纹理冻结。

### 16.7 一致性门禁

构造 synthetic scenes：

- 两热点看到同一箱体；
- 遮挡；
- 透明玻璃；
- 镜子；
- 对象边界；
- 极点；
- seam 跨越对象；
- 故意移动对象；
- 故意换材质；
- 故意改变灯光；
- depth 错误；
- subject-ID 错误。

### 16.8 前端与 E2E

- 项目向导；
- input_grade 标签；
- 人工锁定；
- 热点计划；
- SceneRecipe；
- AI/Cycles 双轨；
- 历史；
- VR Tour 跳转；
- Viewer 拖动、缩放、移动端；
- 重启服务恢复；
- 错误与阻断提示。

## 17. 分阶段实施建议

### M0：基线冻结与文档收口

预计：0.5 周。

工作：

- 冻结当前 164 项相关测试；
- 保存真实 fal 失败样例；
- 固定当前 gate v1 输出；
- 补充现状性能和成本；
- 将本评审稿确认成 v1.0；
- 不新增产品功能。

完成条件：

- 现状可重复；
- 失败样例不会被覆盖；
- 每个后续阶段都有独立 feature flag；
- 用户确认下一阶段范围。

### M1：模型 v3、输入分级与 IFC

预计：2–3 周。

工作：

- v3 runtime migration；
- input_grade；
- surface/asset/material 基础合同；
- IFC 导入；
- 栅格人工锁定门禁；
- API 和前端输入标签。

完成条件：

- DWG/DXF 现有门禁无回归；
- IFC Gold Set 通过；
- 栅格未锁定不能进入付费/正式流程；
- 旧项目不被改写。

阶段出口评审：

- 是否继续 IFC 曲墙/多楼层；
- 是否需要新增专用栅格模型；
- 是否进入自动热点。

### M2：自动热点与批量球面 capture

预计：1–1.5 周。

工作：

- HotspotPlan；
- 安全候选；
- 360 semantic 评分；
- set-cover；
- preview/commit；
- 服务端批量六通道。

完成条件：

- 目标房间规划成功或明确 blocked；
- necessary object/opening 覆盖可量化；
- 人工替换后可追溯；
- capture 可复现。

### M3：SceneRecipe 与资产/PBR

预计：2–4 周。

工作：

- StyleBible；
- GLB 资产库；
- PBR 材质库；
- 许可证合同；
- 资产摆位和碰撞；
- recipe review/lock；
- Three.js 预览。

完成条件：

- 正式对象全部解析到完整资产；
- 公共材质只有一个权威定义；
- scene hash 可复建；
- 无许可资产不能锁定。

这是整体质量的关键路径。

### M4：GPT Image 2 Prompt Lab

预计：1.5–2 周。

工作：

- prompt registry；
- v2 编译器；
- input bundles；
- Gold Set；
- 付费 smoke/challenger；
- 盲评和看板；
- champion 发布。

完成条件：

- 达到预览通过率目标；
- 结构失败不会参与质感排名；
- 成本和失败率可预测；
- 未达目标时功能保持 experimental。

M3 和 M4 可以部分并行，但不能让 AI 图片绕过 SceneRecipe。

### M5：Cycles 正式渲染与跨热点门禁

预计：2–3 周。

工作：

- Blender runtime；
- capability probe；
- scene builder；
- equirectangular render；
- EXR/AOV；
- GPU/CPU attempts；
- cross-hotspot reprojection；
- consistency report。

完成条件：

- 所有热点共享同一 scene hash；
- 一致性门禁全部通过；
- 中断可恢复；
- 8K 不静默降级；
- 正式输出不经过生成式修改。

### M6：产品收口

预计：1–1.5 周。

工作：

- 双轨 UI；
- VR Tour；
- 历史标签；
- 项目迁移；
- 安装器；
- 浏览器 E2E；
- 交付文档。

完成条件：

- AI 预览与保证终稿不可能混淆；
- Windows/Linux 部署通过；
- 新旧项目兼容；
- 正式验收 checklist 完成。

### 总投入

单工程师配合兼职 3D 资产制作，预计 10–15 个工程周。

最大不确定性不是 GPT Image 2 API，而是：

- 可商用高质量家具资产；
- PBR 材质库；
- 真实项目的栅格户型差异；
- 8K Cycles 的硬件与渲染耗时。

## 18. Feature Flags 与发布

建议：

- ifc_import_v1；
- whole_home_model_v3；
- auto_hotspot_v1；
- scene_recipe_v1；
- image2_prompt_lab_v2；
- cycles_final_v1；
- cross_hotspot_gate_v1。

策略：

- 默认关闭未完成模块；
- 每阶段只开启一个受控入口；
- 历史合同先行；
- 失败 fail closed；
- 不用自动 fallback 扩大付费；
- 不用旧结果假装新合同通过。

## 19. 风险与应对

### 19.1 栅格户型泛化

风险：视觉风格、符号、扫描质量和标注语言差异巨大。

应对：

- input_grade；
- 多证据；
- 人工锁定；
- 自有 Gold Set；
- 不承诺全自动施工级。

### 19.2 GPT Image 2 结构漂移

风险：即使提示词更好，模型仍重画结构。

应对：

- 明确 creative_preview；
- prompt 消融；
- hard gate；
- 限制重试；
- 正式终稿回到共享 3D。

### 19.3 资产库成为瓶颈

风险：完整家具资产比语义代理体成本高。

应对：

- 先做高频 30–50 个资产；
- 建立风格系列；
- 允许人工导入；
- 严格尺寸/许可校验；
- 不追求第一版覆盖所有软装。

### 19.4 Blender 部署

风险：运行时体积、GPU 驱动、显存和跨平台。

应对：

- 固定 LTS；
- capability probe；
- GPU smoke；
- attempt 级 CPU fallback；
- 渲染进程隔离；
- 分辨率不静默降级。

### 19.5 评测成本

风险：GPT Image 2 多模板、多次调用会产生费用。

应对：

- smoke→challenger→release 三层；
- 本地 gate 先淘汰；
- 固定调用上限；
- 付费预览；
- 不用最好的一张图作结论。

### 19.6 历史兼容

风险：v3 schema 使旧项目失效。

应对：

- runtime copy migration；
- 不改写旧 JSON；
- 旧 manifest 只读；
- 新 render 要求新合同；
- 历史版本显式展示。

## 20. 最终验收定义

### 20.1 可以承诺

当 guaranteed_cycles 全部门禁通过时，可以承诺：

- 模型结构来自已锁定 WholeHomeModel；
- 一个热点只有一个投影中心；
- 每张全景是完整 2:1、360×180 ERP；
- 所有热点共享同一资产、材质和灯光；
- 同一实体在不同热点产生正确视差；
- 左右接缝、极点、结构和开口经过自动门禁；
- 跨热点 depth、subject-ID 和 material-ID 经过重投影验证；
- 输入、模型、场景、运行时和验收记录可追溯。

### 20.2 不能承诺

即使系统完成，也不能宣称：

- 栅格户型图无需人工就达到施工精度；
- GPT Image 2 预览天然跨热点一致；
- mask 外绝不发生变化；
- 相同 seed 等于共享三维世界；
- 8K 等于几何正确；
- VR 营销效果图可作为施工放样或 BIM 交付。

## 21. 建议的下一步评审顺序

本文件确认后，不建议直接全量实施。建议逐次只批准一个阶段：

1. 先审 M0：确认现状、术语、数据等级和正式承诺；
2. 再审 M1：是否先做 IFC，还是只做 model v3/input_grade；
3. 再审 M2：自动热点的产品交互；
4. 再审 M3：首批资产范围和资产来源；
5. 再审 M4：批准具体 fal 付费测试预算；
6. 再审 M5：批准 Blender 运行时、安装体积与硬件策略；
7. 最后做 M6 产品收口。

每个阶段开始前应单独输出：

- 精确改动文件；
- 数据迁移影响；
- API 变更；
- 预计测试；
- 付费预算；
- 回滚方式；
- 完成门槛。

只有阶段完成并通过门槛后，才进入下一个阶段。

## 22. 评审待确认项

在正式执行前，建议重点评审：

1. creative_preview 与 guaranteed_cycles 两级产品名称是否合适；
2. IFC 是否进入第一阶段，还是排在自动热点之后；
3. 首批资产库是自建、采购还是允许用户导入；
4. 第一版正式终稿是否一定要求 8K；
5. Blender 运行时是否随安装包分发；
6. Gold Set 的项目来源和商业许可；
7. GPT Image 2 付费评测预算；
8. 跨热点门禁阈值是否先作为观察指标，再用 Gold Set 标定后升级成硬门禁；
9. 是否需要把正式 Cycles 终稿与 AI 预览放在同一个历史分类下；
10. 多楼层、楼梯、曲墙和镜面是否全部延期到 v2。

---

本评审稿的核心原则可压缩为一句话：

> 用 CAD/户型图建立唯一几何权威，用 GPT Image 2 提高设计探索速度和画面质感，用共享 SceneRecipe 与 Cycles 对多点位一致性负责，并让每一级结果都能被自动门禁、人工验收和历史 hash 验证。
