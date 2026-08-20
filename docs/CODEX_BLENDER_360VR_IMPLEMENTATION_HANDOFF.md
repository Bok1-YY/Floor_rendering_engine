# Codex + Blender 真实 3D 全屋设计与 360° VR 改造实施交接

> 文档状态：可执行交接稿 v1.0  
> 编写日期：2026-08-19  
> 适用项目：`Floor_engine_Linux`  
> 目标读者：在另一台 Windows 开发机上接手本项目的 Codex / 开发人员  
> 核心目标：停止把正式 360° VR 当作二维图片修补问题，改为由共享三维场景、真实物理地板和 Blender Cycles 对最终几何与球面渲染负责。

---

## 0. 接手者先读结论

### 0.1 最终技术判断

正式产品路线应改为：

1. CAD、户型图、已确认尺寸或现有 `WholeHomeModel` 是空间结构权威；
2. `GeometryManifest` 是所有渲染端共用的几何合同；
3. 原厂彩膜、毫米尺寸、切板相位和铺装方向是地板材质权威；
4. Blender 是墙、地、顶、门窗、家具、灯光、相机、遮挡与最终 ERP 的渲染权威；
5. Codex 负责把数据合同转换为 Blender 场景、调用渲染、检查结果并迭代；
6. AI 图像模型负责设计探索、风格板、纹理候选和资产建议，不再自由改写正式 ERP；
7. 正式 360° 图必须由同一个 `.blend` / `SceneRecipe`、同一个光心和同一个 scene hash 确定性渲染；
8. 当前“单张效果图转 AI 全景”保留为创意预览轨，不再冒充保证型真实 3D VR。

### 0.2 不要继续做的事

- 不要继续用更长提示词期待图片模型自动建立真实共享三维空间；
- 不要分别生成六个面后把拼接通过当作空间正确；
- 不要在最终 ERP 上依赖语义蒙版重新贴正式地板；
- 不要让 AI 在 Blender 渲染完成后再次自由 image-to-image 整张全景；
- 不要把“尺寸正确、文件可解码、首尾像素连续、单元测试通过”叫作肉眼成功；
- 不要把 MCP 中的任意 Python 执行接口直接暴露给生产 Web 请求；
- 不要让每个热点各自重建或随机化一套场景；
- 不要在没有真实资产库的情况下承诺全屋高质量家具可以凭空自动生成。

### 0.3 推荐产品双轨

| 产品轨 | 输入 | 几何保证 | AI 权限 | 最终渲染 | 对外承诺 |
|---|---|---|---|---|---|
| AI 创意 VR | 一张普通效果图，可选彩膜 | 不保证不可见空间真实 | 可生成房间外观与不可见区域 | AI ERP / cubemap | 仅创意预览、实验性 |
| 真实 3D VR | CAD / 户型 / 尺寸 + 彩膜 + 设计需求 | `WholeHomeModel` + Blender 共享场景 | 提设计和素材，不直接改终稿 | Blender Cycles ERP | 地板连续、墙体稳定、跨视角一致 |

---

## 1. 为什么旧路线反复失败

当前两条 360 路线看似不同：

1. 透视效果图扩展成 2:1 ERP；
2. AI 生成 3×2 cubemap atlas，再本地转换成 ERP。

但底层仍然是同一条路线：让图片模型在像素域里隐式猜测一个三维房间，然后再用本地算法修接缝、墙线、地板蒙版和纹理。

旧链路把多个不确定因素串在一起：

```text
单张透视图
  → AI 猜不可见空间
  → AI 猜六面共同几何
  → ERP / cubemap 转换
  → 相机高度与地平线推测
  → 地板语义蒙版
  → 相对深度边界
  → 彩膜二维投影
  → 原图亮度融合
  → VR Viewer
```

任何一层出错，后面都只能修像素，不能恢复真实三维事实。

### 1.1 地板问题的根因

当前球面射线与水平平面求交的数学本身没有错；错误在于它依赖的相机高度、地平线、地板边界和 AI 房间几何并非真实测量。

因此会出现：

- 数学上连续，但与房间透视不匹配；
- 正确彩膜贴在错误空间上，违和感更明显；
- 家具边界由蒙版估计，产生漏贴、光边和残留旧地板；
- 原图地板已经含有错误木纹和噪点，亮度迁移会把污染带回真实彩膜；
- 只有 RGB 合成，没有真实粗糙度、法线、倒角、反射和光线追踪。

### 1.2 墙体球形畸变的根因

ERP 平铺时线条弯曲并不等于错误；真正的错误是把 ERP 转成普通 60°–75° 透视视角后，墙体仍然弯曲、鼓包或改变位置。

这种错误已经被 AI 写进像素内容，局部 2D warp 只能修轻微 roll 或少量曲线，无法重建：

- 一面已经画弯的真实直墙；
- 两个相邻面冲突的房间尺寸；
- 错误门洞后的空间；
- 跨视角不一致的同一件家具。

### 1.3 验收口径曾经错误

旧门禁主要验证：

- 2:1 尺寸；
- ERP 首尾连续；
- 地平线和极点；
- 若干方向的线条偏差；
- 蒙版覆盖率；
- 蒙版外像素是否保持不变。

这些只能证明文件和部分算法不变量成立，不能证明房间、家具、材质、尺度、遮挡和照明真实。

新路线必须明确区分：

```text
程序执行成功 ≠ 几何正确 ≠ 材质真实 ≠ 用户肉眼验收通过
```

---

## 2. 当前项目可直接复用的基础

本项目不需要推倒重写。现有全屋模块已经提供了 Blender 所需的大部分上游数据。

### 2.1 现有能力

- DWG / DXF / IFC / PDF / PNG / JPG 输入分级；
- `WholeHomeModel`；
- CAD 与普通户型图配准；
- 房间、物理空间、墙体、开口、门窗和标高；
- `whole_home_geometry_kernel.compile_geometry_manifest()`；
- 墙、地、顶三角网格；
- 几何 hash 和历史；
- 相机与热点规划；
- CPU 参考光栅器；
- semantic、depth、normal、subject-ID 等审计通道；
- ERP / cubemap 转换和 Viewer；
- 原厂彩膜周期分析、切板状态和本地物理采样；
- 全屋人工复核、几何锁定和严格门禁基础。

### 2.2 现有几何坐标系

当前 GeometryManifest 使用：

```text
right-handed-y-up-x-east-z-south-v2
```

Blender 使用右手 Z-up。第一版固定采用以下变换：

```text
blender_x = project_x
blender_y = -project_z
blender_z = project_y
```

矩阵：

```text
[Bx]   [1  0  0] [Px]
[By] = [0  0 -1] [Py]
[Bz]   [0  1  0] [Pz]
```

注意：相机位置、目标、法线、切线、门窗方向和家具朝向必须全部使用同一变换，禁止不同模块各自解释轴向。

### 2.3 现有文件入口

优先阅读：

- `docs/WHOLE_HOME_3D_PIPELINE.md`
- `docs/PLAN_TO_3D_CORRESPONDENCE_LOCK_V1.md`
- `docs/全屋自动建模_多点一致性与Image2质感_全流程开发计划.md`
- `whole_home_geometry_kernel.py`
- `whole_home_geometry.py`
- `whole_home_engine.py`
- `whole_home_pano_render.py`
- `whole_home_pano_gate.py`
- `film_repeat_floor.py`
- `spherical_floor_renderer.py`
- `routes_whole_home.py`
- `web/src/components/WholeHomeStudio.tsx`
- `web/src/components/PanoViewer.tsx`

---

## 3. 高星 Blender 接管项目研究结论

## 3.1 主要研究对象：ahujasid/blender-mcp

仓库：<https://github.com/ahujasid/blender-mcp>  
研究时约：26k stars、2.5k forks  
许可证：MIT  
研究快照 commit：`c69b90153616f2d767fe2e825d3310efbf6fcab5`  
快照日期：2026-08-16

这是用户提到的高星项目，也是 Blender + AI/MCP 方向最有影响力的公开原型之一。

### 3.1.1 它的核心架构

```text
Codex / Claude / Cursor
        │ MCP stdio
        ▼
外部 Python MCP Server
        │ localhost TCP + JSON
        ▼
Blender Add-on
        │ bpy API
        ▼
当前打开的 Blender 场景
```

关键文件：

- `addon.py`：Blender 内部 add-on 和 TCP server；
- `src/blender_mcp/server.py`：外部 MCP server；
- `tests/test_server_threading.py`：Blender 主线程队列回归测试；
- `TERMS_AND_CONDITIONS.md`：遥测和数据条款；
- `src/blender_mcp/trajectory.py`：观察、操作和反馈轨迹。

### 3.1.2 值得吸收的经验

#### A. 外部控制层与 Blender 内部执行层分离

`bpy` 只能在 Blender 自身 Python 环境中可靠运行，因此外部 MCP 不直接 `import bpy`，而是通过 add-on 把命令送入 Blender。

这个分层适合交互开发，也方便：

- Codex 获取场景状态；
- 创建、修改和删除对象；
- 设置材质；
- 截取 viewport；
- 执行小段 `bpy`；
- 在操作前后形成视觉闭环。

#### B. 所有 bpy 操作必须回到 Blender 主线程

该项目当前实现使用 `queue.Queue` 接收 socket 线程的请求，再由 `bpy.app.timers` 注册的主线程回调执行。

这是必须保留的原则。Blender 官方明确说明 Python threading 不是线程安全的，错误使用可能在 Cycles 渲染或绘图时产生难以定位的崩溃：

<https://docs.blender.org/api/5.0/info_gotchas_threading.html>

#### C. 命令必须串行并有明确超时

外部 server 用锁保护一次完整的 send + receive，避免两个命令在同一 socket 上交错并拿错响应。

我们的交互桥也必须：

- 每个请求有 request ID；
- 写操作串行；
- 有 startup timeout、tool timeout；
- 超时后废弃连接，不能继续使用已失步的流；
- 不在同一 Blender 文件中并发修改场景。

#### D. 操作前后都要观察

项目把 `get_scene_info` 和 `get_viewport_screenshot` 作为重要工作流：

```text
观察 → 修改 → 再观察 → 接受 / 修正 / 撤销
```

这比“执行完 bpy 没报错就算完成”可靠。我们的版本要进一步升级为：

```text
场景结构快照 + 低清多视角渲染 + 数值门禁 + 视觉门禁
```

#### E. 不要一次生成整个复杂场景

其资产策略明确建议把复杂任务拆小，单个生成式 3D 服务适合单件资产，不适合一次生成整个房间、地面或多个零件后强行拼成一致场景。

这与本项目的结论一致：

- 房间结构来自 CAD；
- 地板来自确定性物理材质；
- AI 3D 只作为单件候选资产；
- 最终位置、尺寸和碰撞仍由本地规则校验。

### 3.1.3 不能直接照搬的部分

#### A. 任意 `exec(code, namespace)` 风险过高

原项目的 `execute_code` 本质是：

```python
namespace = {"bpy": bpy}
exec(code, namespace)
```

这相当于让 MCP 客户端拥有 Blender 进程权限和用户文件权限。

正确边界：

- 仅在本机、可信开发会话使用；
- 只监听 `127.0.0.1`；
- 默认对写操作请求审批；
- 不向 Floor Engine 的 Web API 暴露；
- 生产 worker 不启 MCP，不开放任意代码；
- 正式渲染只执行版本化、审查过的入口脚本；
- 输入只允许 JSON recipe 和工作区内白名单路径。

#### B. GUI MCP 不适合正式长时间渲染

该项目明确依赖 Blender GUI 和 `bpy.app.timers`；背景模式下 timer 不运行，因此它不适合直接承担无人值守生产渲染。

正式 Cycles 渲染应使用独立进程：

```powershell
blender.exe --background --factory-startup `
  --python-exit-code 23 `
  --python blender_pipeline/scripts/render_entry.py `
  -- --recipe <scene_recipe.json> --attempt-dir <dir>
```

MCP 用于开发和可视化调试；生产使用无头 CLI。

#### C. 180 秒 socket 超时不等于可恢复渲染任务

长渲染不能通过一个阻塞 MCP tool 调用等待。正确方式是：

1. 创建 render attempt；
2. 启动独立 Blender 子进程；
3. 记录 PID、日志、心跳和阶段；
4. API 轮询 attempt；
5. 成功后原子发布产物；
6. 失败后保留日志和中间件，不把半成品标成功。

#### D. 遥测默认行为不适合客户项目

研究快照的条款说明，启用遥测时可能采集提示词、生成代码、场景元数据、viewport 截图和操作轨迹，并可能用于研究或训练。

因此：

- 开发机若安装原版，必须设置 `DISABLE_TELEMETRY=true`；
- 不在 Blender add-on preferences 中保存项目 API 密钥；
- 不把客户 CAD、彩膜、场景截图或设计数据发给第三方遥测；
- 生产不依赖该第三方 add-on。

### 3.1.4 许可证结论

`ahujasid/blender-mcp` 代码为 MIT，可以学习和在遵守许可证时复用；但第一阶段建议把它作为开发工具安装，不把源码直接 vendor 到商业主线。

如果未来复制了实质代码，必须：

- 保留 MIT 版权和许可文本；
- 更新 `THIRD_PARTY_NOTICES.md`；
- 固定来源 commit；
- 做安全审计；
- 关闭遥测；
- 维护自己的协议版本与测试。

## 3.2 对照研究：RFingAdam/mcp-blender

仓库：<https://github.com/RFingAdam/mcp-blender>  
研究快照 commit：`c3bfcd19c1fd3058baa0f3ed70ed6f09ba47cec7`  
快照日期：2026-08-17  
许可证：AGPL-3.0-or-later，商业闭源嵌入需要额外许可评估。

该项目 star 不如前者高，但提供了一些更接近生产形态的经验：

- newline-delimited JSON / JSON-RPC；
- 非阻塞 socket；
- `bpy.app.timers` 主线程调度；
- 按 Blender 工作阶段拆分结构化 tools；
- 输入类型、枚举和路径校验；
- 多角度渲染；
- render → analyze → refine 闭环；
- measurement、mesh quality、PBR baking 和 Geometry Nodes 等专用工具。

应吸收它的工程模式，但不要复制其 AGPL 源码进入闭源商业项目，除非完成许可证评审或取得商业许可。

## 3.3 对 Floor Engine 的最终借鉴

| 开源经验 | 本项目采用方式 |
|---|---|
| MCP server + Blender add-on 双层桥 | 仅用于 Codex 交互开发和人工可视化调试 |
| bpy 主线程队列 | 交互桥强制执行；生产改用单一无头 Blender 进程 |
| scene info + screenshot | 升级为结构快照 + 多角度审计 + ERP/AOV |
| 任意 Python | 仅可信开发使用，生产禁用 |
| 资产搜索和下载 | 只进入候选库，必须校验尺寸、许可证、pivot、材质和 hash |
| AI 3D 生成 | 只生成单件候选，不生成地面和完整房间 |
| render-analyze-refine | 采用，但评分必须包含硬几何和地板物理指标 |
| 遥测 | 完全关闭 |
| GUI 内长渲染 | 不采用，正式渲染走后台 attempt worker |

---

## 4. 目标架构：开发控制面与生产执行面分离

## 4.1 总体架构

```text
                         ┌────────────────────────────┐
                         │  Codex / 人工设计确认      │
                         └─────────────┬──────────────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    │                                     │
         交互开发控制面                              产品生产执行面
                    │                                     │
     Codex ↔ MCP ↔ Blender GUI              Floor Engine Job Queue
     场景检查 / 小步修改 / 截图                         │
                    │                          Headless Blender Process
                    │                                     │
                    └──────── SceneRecipe ────────────────┘
                                                          │
                                      .blend + EXR/AOV + ERP + manifest
                                                          │
                                               VR Viewer / 历史 / 导出
```

## 4.2 为什么必须分两面

交互 MCP 的优势：

- Codex 可以直接查看 Blender 当前状态；
- 适合试验 `bpy`、材质节点、模型摆位和灯光；
- 人可以在 GUI 中观察和纠正；
- 能快速形成操作前后截图。

生产 headless 的优势：

- 没有 GUI、焦点、timer 和手工状态依赖；
- 可固定 Blender 版本、启动参数和环境；
- 每个 attempt 独立进程，可超时、终止和重试；
- 输入输出均可 hash；
- 适合队列、服务端和独立 GPU 机器；
- 可以严格禁止任意 Python 和外部路径。

## 4.3 正式渲染不使用 MCP 的原则

正式渲染过程只允许：

```text
validated GeometryManifest
+ validated SceneRecipe
+ validated local assets
+ versioned Blender scripts
+ pinned Blender runtime
= deterministic render attempt
```

任何 MCP 临时操作若要进入正式结果，必须先回写成版本化 `SceneRecipe` 或受审查代码，再走完整后台渲染。

---

## 5. Blender Runtime 设计

## 5.1 版本策略

截至 2026-08-19，Blender 5.2 LTS 已发布并支持到 2028 年 7 月：

<https://www.blender.org/download/lts/>

第一版建议：

- 新实现以 Blender 5.2 LTS 为目标；
- 固定到一个具体补丁版本，不使用 `latest`；
- Windows 使用官方 Portable ZIP，应用管理，不依赖系统 PATH；
- Linux worker 使用对应官方 tar 包或受控镜像；
- runtime 记录 SHA-256；
- 禁止自动升级；
- 每次升级先跑 Gold Set 和 `.blend` 兼容测试。

如果某个关键 add-on 或 GPU 驱动只在 4.5 LTS 稳定，则允许先固定 4.5.12，但必须由 capability probe 和真实渲染基准决定，不能凭版本号猜测。

## 5.2 建议目录

```text
Floor_engine_Linux/
  .runtime/                         # gitignore，不进入源码包；另一台电脑安装
    blender/
      5.2.x-windows-x64/
        blender.exe
  blender_pipeline/
    __init__.py
    contracts.py                    # 非 bpy，可由主 Python 单测
    runtime.py                      # runtime 探测、版本、设备和 smoke
    attempts.py                     # attempt 生命周期、日志、超时和发布
    scene_recipe.py                 # recipe schema、hash、迁移
    coordinate_system.py            # Y-up → Blender Z-up 唯一变换
    floor_contract.py               # 地板铺装计划，不导入 bpy
    asset_registry.py               # 本地资产清单和许可证
    scripts/                        # 仅由 Blender Python 执行
      render_entry.py
      build_scene.py
      build_architecture.py
      build_floor.py
      build_materials.py
      import_assets.py
      build_lighting.py
      build_cameras.py
      configure_cycles.py
      export_aov.py
      audit_scene.py
    schemas/
      scene_recipe_v1.schema.json
      render_attempt_v1.schema.json
      asset_manifest_v1.schema.json
    templates/
      README.md
```

不要把 `.blend` 二进制模板当成唯一逻辑。所有关键场景都必须能由 JSON + 脚本从空场景重建。

## 5.3 Capability 合同

建议 API：

```json
{
  "available": true,
  "runtime_version": "5.2.x",
  "runtime_sha256": "...",
  "executable": ".../blender.exe",
  "python_version": "...",
  "cycles_devices": [
    {"backend": "OPTIX", "name": "...", "type": "GPU"}
  ],
  "gpu_ready": true,
  "cpu_ready": true,
  "panorama_ready": true,
  "exr_ready": true,
  "smoke_status": "passed",
  "smoke_duration_ms": 0,
  "warnings": []
}
```

启动顺序：

1. 找配置的 Blender 路径；
2. 找项目 `.runtime/blender/.../blender.exe`；
3. 校验版本和 SHA-256；
4. 运行 `--version`；
5. 运行最小 `--background --factory-startup --python-expr`；
6. 探测 Cycles devices；
7. 渲染 64×32 全景 smoke；
8. 校验输出 2:1 和可解码；
9. 缓存 capability，但 runtime 变化时失效。

## 5.4 当前机器已知限制

原机器审计结果：

- 未安装 Blender；
- 显卡为 Intel Iris Xe 集成显卡；
- 无 NVIDIA GPU；
- Blender 5.2 Cycles oneAPI 正式支持的是 Intel Arc A/B 系列，不应假定 Iris Xe 可作为 Cycles GPU；
- 原机器只适合 CPU smoke、低清 Workbench/EEVEE 预览；
- 4K/8K Cycles 终稿应在带受支持独立 GPU 的另一台电脑或渲染 worker 上运行。

官方 GPU 支持参考：

<https://docs.blender.org/manual/id/5.2/render/cycles/gpu_rendering.html>

---

## 6. 数据合同

## 6.1 SceneRecipe v1

`SceneRecipe` 是设计与渲染的唯一可审计入口，禁止把设计状态只留在聊天或 `.blend` 中。

建议最小结构：

```json
{
  "schema_version": 1,
  "scene_id": "scene_...",
  "geometry_manifest_hash": "...",
  "coordinate_system": "right-handed-y-up-x-east-z-south-v2",
  "blender_coordinate_system": "right-handed-z-up-x-east-y-north-v1",
  "unit": "metre",
  "design": {
    "style_id": "modern_warm_minimal",
    "palette": [],
    "locked": false
  },
  "surfaces": [],
  "floor_installations": [],
  "assets": [],
  "lights": [],
  "cameras": [],
  "render_profile": "draft_cycles_2k",
  "random_seed": 0,
  "source_hashes": {},
  "recipe_hash": "..."
}
```

所有数组排序、浮点取整和路径规范化后再计算 hash，保证同一语义得到同一 recipe hash。

## 6.2 FloorInstallation v1

建议字段：

```json
{
  "installation_id": "floor_living_01",
  "surface_ids": ["floor:living"],
  "film_asset_id": "film_vl88238xl_006",
  "film_sha256": "2396B1A4...",
  "film_width_mm": 984.0,
  "repeat_length_mm": 1890.0,
  "repeat_axis": "long_edge",
  "plank_width_mm": 0.0,
  "plank_length_mm": 0.0,
  "plank_thickness_mm": 0.0,
  "laying_pattern": "straight_random_stagger",
  "direction_deg_project": 90.0,
  "world_origin_m": {"x": 0.0, "z": 0.0},
  "phase_seed": 0,
  "joint_width_mm": 1.0,
  "bevel_width_mm": 0.5,
  "roughness": 0.0,
  "coat_weight": 0.0,
  "albedo_authority": "manufacturer_film_exact",
  "ai_texture_edit_allowed": false
}
```

`0.0` 表示必须由用户或产品资料补齐，不能静默假设。

## 6.3 AssetManifest v1

每个家具和装饰资产必须记录：

- `asset_id`；
- 原始文件 hash；
- 格式和导入器版本；
- 商业许可证和来源 URL；
- 尺寸；
- pivot；
- forward/up 轴；
- 可替换材质槽；
- bounding box；
- collision proxy；
- room role；
- style tags；
- LOD；
- 允许的缩放范围；
- 预览图；
- 是否允许正式交付。

下载或 AI 生成的 GLB 只能先进入 quarantine，完成校验后才能进入正式资产库。

## 6.4 RenderAttempt v1

每次渲染必须独立记录：

```json
{
  "attempt_id": "render_...",
  "scene_recipe_hash": "...",
  "geometry_manifest_hash": "...",
  "blender_runtime_sha256": "...",
  "script_bundle_hash": "...",
  "device": {},
  "status": "queued|running|passed|failed|cancelled",
  "stage": "...",
  "pid": 0,
  "started_at": "...",
  "finished_at": "...",
  "outputs": {},
  "aov": {},
  "gate": {},
  "error": null
}
```

不允许覆盖旧 attempt，不允许失败后在同一记录里静默改设备、分辨率或 samples。

---

## 7. Blender 场景构建

## 7.1 建筑几何

输入只使用已锁定 GeometryManifest：

1. 创建整屋 collection；
2. 按 semantic kind 创建 `Architecture/Floor`、`Architecture/Wall`、`Architecture/Ceiling`、`Architecture/Opening`；
3. 每个对象写入 source entity ID 和 geometry hash；
4. 使用唯一坐标变换；
5. 保留米制尺寸；
6. 门窗开口必须来自几何内核，不通过图像模型猜；
7. 法线方向统一；
8. 非流形、重复面、零面积面直接 hard fail；
9. 保存 architecture manifest；
10. 低清渲染前先验证 mesh bounds 与源户型 bounds。

## 7.2 地板几何与原厂彩膜

### 7.2.1 原则

地板最终不再依赖 ERP 蒙版，而是实际三维表面。家具、墙体和门洞的遮挡由 Blender 的光线与深度缓冲自然产生。

### 7.2.2 彩膜预处理

主 Python 在调用 Blender 前完成：

1. 读取原厂彩膜；
2. EXIF 归一；
3. 检测并排除右下角标签、文字和 logo；
4. 验证物理宽度和长边周期；
5. 验证长边拼接误差；
6. 生成清洁 albedo 资产；
7. 生成 `film_manifest.json`；
8. 记录原图和清洁图 SHA-256；
9. Base Color 保留真实 sRGB，不交给 AI 改写。

现有 `film_repeat_floor.py` 的周期、lane、phase 和切板分配逻辑应成为权威。不要在 Blender 脚本中重新发明第二套随机铺装算法。

推荐做法：主进程预计算每块板的：

- 世界位置；
- 长宽厚；
- lane；
- longitudinal phase；
- rotation；
- UV transform；
- board tone 微差；
- source validity。

Blender 只按合同创建实例和材质。

### 7.2.3 板片实现

第一版采用混合方案：

- 房间轮廓是一个真实 floor surface；
- 板片用 Geometry Nodes / linked mesh instances；
- 近处有真实 joint 与微 bevel；
- 远处由 normal / bump 表示细节；
- 不用高强度 displacement 模拟木纹；
- 不对每块板复制一份 4K 纹理；
- 使用同一个 image datablock 和 per-instance UV 参数。

### 7.2.4 PBR 材质

最少提供：

- Base Color：原厂彩膜；
- Roughness：产品参数或保守标定；
- Normal：弱木纹微表面，非颜色数据；
- Joint/Bevel：几何或独立程序节点；
- Coat：依据产品涂层；
- 可选 AO：只用于微结构，不烘焙家具阴影。

禁止：

- 从旧 AI 地板提取高频纹理叠到原厂彩膜；
- 让图像模型重新生成 Base Color；
- 用随机 noise 改变产品主色和真实花纹；
- 让相邻板使用完全相同 lane 与相位；
- 每个房间重新定义互不相干的世界原点。

## 7.3 墙面装修

AI 可以决定：

- 墙漆色号候选；
- 艺术漆、墙纸、木饰面、石材等类别；
- 分缝尺寸；
- 背景墙构图；
- 装饰画内容；
- 灯槽与洗墙灯方向。

但必须转换为：

- Blender surface material；
- tileable PBR texture；
- 有尺寸的墙板 mesh；
- decal / frame object；
- 真实灯具和光源。

AI 不直接修改正式全景像素。

## 7.4 家具和软装

自主全屋设计依赖正式资产库。Codex 可以：

- 根据风格、预算、房间功能筛选资产；
- 按真实尺寸摆放；
- 检查碰撞、门扇、通道和窗户遮挡；
- 调整朝向和材质槽；
- 生成低清视图并迭代。

Codex 不能凭空保证每个自由生成的家具都具备商业建模质量。第一阶段优先使用：

1. 自有或采购的已授权 GLB；
2. Poly Haven 等许可证明确的素材；
3. 已审核的供应商模型；
4. AI 3D 只作为单件候选，经人工和自动清理后入库。

## 7.5 灯光

SceneRecipe 必须明确：

- 日照方向和强度；
- 环境 HDRI hash；
- 窗口面积光；
- 灯具位置、色温、功率和 IES；
- 世界曝光、白平衡和 AgX 设置；
- 是否允许 emissive fixture；
- 同一整屋所有热点共用相同光照状态。

---

## 8. AI 的正确职责

## 8.1 AI 设计导演

AI 输出结构化 `DesignIntent`，例如：

```json
{
  "style": "modern_warm_minimal",
  "wall_materials": [],
  "furniture_roles": [],
  "lighting_intent": {},
  "palette": [],
  "risk_flags": [],
  "questions": []
}
```

之后由规则和资产解析器把它转换为 SceneRecipe。

## 8.2 允许 AI 生成的内容

- 风格板和概念图；
- 墙纸和装饰画候选；
- tileable PBR 表面候选；
- 单件家具概念；
- 资产搜索关键词；
- 配色和灯光建议；
- 低清渲染的审美评分和问题列表。

## 8.3 禁止 AI 直接生成的正式权威

- 墙、地、顶真实几何；
- 门窗位置；
- 相机光心；
- 地板 Base Color；
- 板宽、板长、相位和世界坐标；
- 最终正式 ERP；
- 已有家具在不同热点的独立版本。

## 8.4 若必须使用 AI 图像编辑

只允许作为预览，或满足全部条件后回写三维表面：

1. 输入 rectilinear view + depth + normal + material/subject ID；
2. 只编辑指定 surface；
3. 输出反投影到该 surface 的 texture/decal；
4. 做多视角可见性和接缝检查；
5. 回到 Blender 正式重渲染；
6. 正式 ERP 不直接采用 AI 返回图。

---

## 9. 相机与 360° 输出

## 9.1 正式相机

Cycles 原生支持从同一相机位置渲染完整 360°×180° equirectangular：

<https://docs.blender.org/manual/de/latest/render/cycles/object_settings/cameras.html>

第一版：

```text
camera.type = PANO
panorama_type = EQUIRECTANGULAR
resolution = 4096×2048 draft / 8192×4096 final
single optical center
level horizon
motion blur off
fixed seed
```

不再要求 AI 输出 3×2 atlas，也不再需要 AI 修 cubemap face seam。

## 9.2 AOV 与产物

每个热点至少输出：

- `beauty.exr`；
- `beauty_web.png` 或 JPEG；
- `depth.exr`；
- `world_normal.exr`；
- `object_id.exr`；
- `material_id.exr`；
- `albedo.exr`；
- 可选 direct / indirect / shadow；
- `scene_manifest.json`；
- `render_attempt.json`；
- `render.log`；
- 八方向 rectilinear QA 图。

## 9.3 Floor Engine 中的“合成”边界

允许：

- EXR 色彩管理；
- 固定曝光和白平衡；
- AOV 确定性组合；
- 水印；
- 缩略图；
- 非生成式调色；
- Viewer 元数据和 GPano metadata。

禁止：

- 正式 ERP 上的自由生成式修墙；
- 正式 ERP 上的语义蒙版铺地板；
- 重新 image-to-image 整张终稿；
- 把局部 AI 修复冒充共享三维保证。

---

## 10. 后端改造清单

## 10.1 新模块

建议新增：

```text
blender_pipeline/
routes_blender.py
tests/test_blender_contracts.py
tests/test_blender_runtime.py
tests/test_blender_floor_contract.py
tests/test_blender_attempts.py
tests/test_blender_routes.py
tests/integration/test_blender_smoke.py
```

## 10.2 配置项

`config.py` / `engine_config.json` 增加：

```json
{
  "blender_enabled": false,
  "blender_executable": "",
  "blender_expected_version": "5.2.x",
  "blender_runtime_sha256": "",
  "blender_render_root": "",
  "blender_draft_profile": "draft_cycles_2k",
  "blender_final_profile": "final_cycles_8k",
  "blender_max_concurrent_attempts": 1,
  "blender_gpu_required_for_final": true
}
```

密钥仍留在本机 `data/engine_config.json`，不进入源码包和 `.blend`。

## 10.3 API 建议

```text
GET  /api/blender/capabilities
POST /api/blender/capabilities/refresh
POST /api/whole-home/{project_id}/scene-recipe/preview
POST /api/whole-home/{project_id}/scene-recipe/lock
POST /api/whole-home/{project_id}/blender/render-preview
POST /api/whole-home/{project_id}/blender/render-final
GET  /api/blender/attempts/{attempt_id}
POST /api/blender/attempts/{attempt_id}/cancel
GET  /api/blender/attempts/{attempt_id}/artifacts
```

正式 render endpoint 必须拒绝：

- 未锁定 GeometryManifest；
- 缺失真实地板尺寸；
- 缺失或许可不明的资产；
- runtime hash 不匹配；
- GPU required 但不可用；
- recipe hash 与预览不一致；
- 相机不在安全空间；
- 同项目已有冲突 attempt。

## 10.4 子进程安全

- 使用参数数组调用 Blender，不拼接 shell 字符串；
- 路径必须解析并验证在项目 render root / asset root 内；
- `--factory-startup`；
- `--disable-autoexec`，除非我们明确需要受信任 driver；
- `--python-exit-code`；
- 限制环境变量；
- 独立 attempt 目录；
- stdout/stderr 写入日志；
- Windows 超时终止整个进程树；
- 成功产物先写临时文件，再原子 rename；
- 不删除失败 attempt，保留诊断。

---

## 11. 前端改造清单

## 11.1 模式命名

建议在 UI 中清晰分开：

- `AI 创意 360°`；
- `真实 3D 360°（Blender）`。

禁止让用户误以为二者具有相同保证。

## 11.2 真实 3D 模式所需输入

- 几何来源和锁定状态；
- 房间 / 全屋选择；
- 热点列表；
- 原厂彩膜；
- 彩膜宽度和周期；
- 板宽、板长、厚度；
- 铺装方向、方式、起铺原点；
- 墙面风格；
- 家具资产方案；
- 灯光方案；
- draft / final 渲染档；
- runtime / GPU 状态。

## 11.3 结果卡

显示：

- scene recipe hash；
- geometry manifest hash；
- Blender 版本和 runtime hash；
- 渲染设备；
- 分辨率和 samples；
- 地板 film hash 与物理参数；
- AOV 可用性；
- 八方向 QA；
- 自动门禁与人工验收；
- `AI preview` 或 `Blender guaranteed` 明确标签。

---

## 12. Codex 与 Blender MCP 的开发机接线

## 12.1 官方 Codex MCP 配置依据

OpenAI 官方文档确认：

- Codex 支持本地 STDIO MCP server；
- 默认配置位于 `~/.codex/config.toml`；
- 可信项目可使用项目级 `.codex/config.toml`；
- CLI 可用 `codex mcp add`、`codex mcp list`；
- 可设置 tool timeout、enabled tools 和 approval mode。

文档：<https://developers.openai.com/codex/mcp>

## 12.2 安装建议

仅用于交互开发：

1. 安装固定 Blender LTS；
2. 安装 `uv`；
3. 找到 `uvx.exe` 绝对路径；
4. 安装 Blender MCP add-on；
5. 禁用遥测；
6. 在 Blender GUI 中启动本机 MCP server；
7. 将 MCP STDIO server 注册到 Codex；
8. 用 `/mcp` 或 `codex mcp list` 检查。

示例命令，路径需要按新电脑实际值替换：

```powershell
where.exe uvx

uvx blender-mcp install-addon

codex mcp add blender `
  --env BLENDER_HOST=127.0.0.1 `
  --env BLENDER_PORT=9876 `
  --env DISABLE_TELEMETRY=true `
  -- "C:\Users\<USER>\.local\bin\uvx.exe" --python 3.11 blender-mcp

codex mcp list
```

如果 CLI 参数与当前安装版本不同，以 `codex mcp --help` 和官方文档为准。

## 12.3 推荐 config.toml 策略

不要把带用户名的绝对路径提交进 Git。可在本机用户配置中写：

```toml
[mcp_servers.blender]
command = "C:/Users/<USER>/.local/bin/uvx.exe"
args = ["--python", "3.11", "blender-mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 300
enabled = true
required = false
default_tools_approval_mode = "writes"

[mcp_servers.blender.env]
BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = "9876"
DISABLE_TELEMETRY = "true"
```

第一阶段不要把 `default_tools_approval_mode` 设成无条件批准。

## 12.4 MCP 开发操作 SOP

每次任务：

1. 保存 checkpoint `.blend`；
2. `get_scene_info`；
3. viewport 截图；
4. 一次只做一个逻辑变更；
5. 再取场景结构；
6. 多角度低清渲染；
7. 运行尺寸、碰撞、材质和相机检查；
8. 视觉检查；
9. 将接受结果回写 SceneRecipe / 正式脚本；
10. 使用 headless pipeline 复建并对比；
11. 只有 headless 重建一致才进入主线。

---

## 13. 测试与验收

## 13.1 无 Blender 单元测试

必须在普通 pytest 环境测试：

- schema 验证；
- recipe canonicalization 和 hash；
- 坐标变换往返；
- GeometryManifest → SceneRecipe 映射；
- 地板 board plan；
- lane / phase 确定性；
- 资产路径和许可证；
- attempt 状态机；
- 命令参数构建；
- API contract；
- 旧项目不启 Blender 时不受影响。

## 13.2 Blender smoke

最小 smoke：

1. 空场景建立 4m×5m 房间；
2. 创建真实地板；
3. 创建一面墙、门洞和几个 box 代理家具；
4. 创建 equirectangular 相机；
5. 渲染 1024×512；
6. 输出 depth / normal / ID；
7. 校验文件；
8. 退出码为 0；
9. 第二次运行 hash 一致。

## 13.3 地板硬门禁

- 使用的 Base Color hash 等于确认彩膜清洁资产 hash；
- 世界单位与毫米合同一致；
- 板宽、板长误差低于合同阈值；
- 所有相邻房间共用正确安装原点；
- 没有 board overlap；
- 没有穿墙；
- lane / phase 分配确定；
- 相邻板重复率低于阈值；
- 门洞连续；
- 不存在旧 AI 地板像素；
- 正式 ERP 不经过生成式地板后处理。

## 13.4 几何硬门禁

- scene bounds 与 GeometryManifest 一致；
- mesh hash 一致；
- 墙、地、顶和开口数量一致；
- 相机在可行走空间内；
- 所有热点共享 scene hash；
- 八方向 rectilinear view 的建筑竖线和直线通过；
- 深度和 ID 不存在空洞；
- 没有非流形、零面积和错误法线；
- 没有对象碰撞和门扇净空冲突。

## 13.5 肉眼 Gold Set

至少建立：

- 标准客厅；
- 卧室；
- 开放式客餐厅；
- 有门洞的相邻空间；
- 大面积地板；
- 窗边强光；
- 深色与浅色彩膜；
- 近距离板缝；
- 远距离防摩尔纹；
- 现有失败案例 `VL88238XL(EIR)-006`。

每个案例保存：

- 输入；
- geometry hash；
- film hash；
- recipe；
- draft；
- final；
- 八方向截图；
- 人工评分；
- 已知失败说明。

---

## 14. 分阶段实施顺序

### M0：环境和最小闭环

工作：

- 固定 Blender runtime；
- capability probe；
- 建立 `blender_pipeline` 骨架；
- 从空场景构建一个房间；
- 原厂彩膜真实板片；
- equirectangular 1024×512；
- AOV；
- 接入 Viewer；
- 两次重复渲染比较。

完成门槛：

- 墙体在八方向无球形畸变；
- 地板无跨面错缝；
- 彩膜 identity 正确；
- 家具遮挡由真实深度产生；
- 无 AI 后处理；
- 复建 hash 一致。

### M1：正式 GeometryManifest 场景

工作：

- 全部建筑几何映射；
- 门窗和开口；
- 热点相机；
- scene manifest；
- `.blend` 和脚本重建对照。

完成门槛：

- Blender bounds、surface 数量和 geometry hash 与主系统一致；
- CPU 参考渲染与 Blender ID/depth 可重投影比较。

### M2：地板产品材质

工作：

- `film_manifest`；
- board plan；
- Geometry Nodes / instances；
- joint、bevel、normal、roughness；
- 多房间连续铺装；
- 纹理内存和 LOD。

完成门槛：

- `VL88238XL(EIR)-006` 通过近、中、远和 360° 检查；
- 板片物理尺寸可从 scene 反测；
- 不存在标签污染和明显重复。

### M3：SceneRecipe 与 AI 设计导演

工作：

- 结构化 DesignIntent；
- 材质和资产解析；
- 低清 preview；
- recipe review / lock；
- AI 只生成候选素材。

完成门槛：

- AI 图片不能绕过 recipe；
- 正式结果只由已锁定 recipe 产生。

### M4：家具资产库和自动摆位

工作：

- GLB registry；
- 许可证；
- pivot、尺寸和轴向；
- 房间规则；
- 碰撞、净空和门窗遮挡；
- LOD 和材质替换。

完成门槛：

- 所有正式对象都有 asset ID 和 hash；
- 不合规资产无法进入 final。

### M5：生产 Cycles Worker

工作：

- attempt queue；
- GPU/CPU probe；
- 4K/8K profile；
- EXR/AOV；
- 超时、取消、恢复和日志；
- 独立 GPU 机器部署；
- 原子发布。

完成门槛：

- 8K 不静默降级；
- 同一 recipe 可跨机器复建；
- 失败不会发布为成功；
- 正式输出不经过生成式修改。

### M6：产品双轨收口

工作：

- AI 创意 VR 与真实 3D VR 分栏；
- 结果标签；
- 历史、导出、迁移；
- Gold Set 看板；
- 人工终验。

---

## 15. 另一台电脑接手第一天操作

## 15.1 解包与完整性

1. 将交接 ZIP 复制到本地 SSD；
2. 用随包 SHA-256 校验 ZIP；
3. 解压到短路径，例如 `D:\Floor_engine_Linux`；
4. 不要执行 `git reset --hard`；
5. 查看随包 `HANDOFF/GIT_STATUS.txt` 和 `HANDOFF/WORKTREE_DIFF.patch`；
6. 确认 `docs/CODEX_BLENDER_360VR_IMPLEMENTATION_HANDOFF.md` 存在；
7. 确认 `assets/clipseg`、`assets/mobile_sam`、`assets/depth_anything_v2` 存在；
8. 确认 `.tools/dotnet/dotnet.exe` 存在；
9. `data/engine_config.json` 不在包内是正常安全策略。

## 15.2 基础环境

建议安装：

- Git；
- Python 3.12 x64；
- Node.js 20.9+，优先当前 LTS；
- Blender 固定 LTS Portable；
- Codex；
- `uv`；
- GPU 官方驱动。

项目自身的便携 .NET / ACadSharp 已在交接包中保留，无需依赖原机器的系统 .NET。

## 15.3 恢复 Python 与 Web

```powershell
cd D:\Floor_engine_Linux

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

cd web
npm ci
npm run build
cd ..
```

或先使用现有 `Install_Project_Dependencies.bat`，但仍要检查它是否与新电脑 Python 路径一致。

## 15.4 密钥恢复

交接包故意不包含：

- `data/engine_config.json`；
- API keys；
- 浏览器 profile；
- 第三方登录会话。

在新电脑启动项目后，通过设置页重新配置，或者从受控密码管理器安全迁移。不要把密钥写进 Git、Markdown、`.blend`、截图或 MCP 配置。

## 15.5 基线测试

先运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q

cd web
npm run test:whole-home-render
npm run build
cd ..
```

如果全量测试耗时过长，至少先跑：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_film_repeat_floor.py `
  tests/test_spherical_floor_renderer.py `
  tests/test_whole_home_geometry_kernel.py `
  tests/test_whole_home_pano.py `
  tests/test_whole_home_pano_gate.py
```

## 15.6 Blender 接线

1. 配置 Blender executable；
2. 运行 capability probe；
3. 安装开发用 Blender MCP；
4. 设置 `DISABLE_TELEMETRY=true`；
5. 注册 Codex MCP；
6. 只用 MCP 做交互开发；
7. M0 正式结果必须由 headless script 重建。

---

## 16. 交接包包含与排除

## 16.1 应包含

- 当前工作树全部源码，包括未提交新文件；
- `.git`，保留历史和当前 dirty 状态；
- `docs` 和本交接文档；
- `tests`、`dwg_test`；
- `web/src`、Web 配置、lockfile、测试和已构建 `web/out`；
- 本地 ONNX 模型 `assets`；
- `.tools/dotnet`、NuGet/ACadSharp 必要运行资源；
- `data/external_datasets`；
- 原厂彩膜参考；
- 用户录制的旧路线失败视频；
- 选取的 VR360 回归输入与失败/改进结果；
- Git 状态、diff、文件 hash 清单和 ZIP SHA-256。

## 16.2 故意排除

- `.venv`；
- `web/node_modules`；
- `web/.next`；
- `__pycache__`、`.pytest_cache`；
- `.pt_*`、`.pytest_tmp*`；
- `.browser_profiles`；
- `.tools/cache` 和临时日志；
- 绝大部分 `data/output_files`；
- `data/_ng_uploads`、缩略图和浏览器运行目录；
- `data/engine_config.json`；
- API keys、浏览器 cookie 和登录状态；
- 旧机器特定虚拟环境和依赖缓存；
- Blender runtime，因为原机器尚未安装。

这些排除项不是漏打包，而是为了让另一台电脑得到干净、可复现且不泄露密钥的开发环境。

---

## 17. 风险登记

| 风险 | 影响 | 处理 |
|---|---|---|
| 只有一张透视效果图 | 无法还原唯一真实背面空间 | 创意轨；真实轨要求 CAD/尺寸/多视角 |
| Blender runtime 升级 | bpy、材质、渲染结果变化 | 固定 LTS patch + hash + Gold Set |
| MCP 任意代码 | 本地代码执行和文件风险 | 仅开发、loopback、审批、生产禁用 |
| Blender Python 线程 | 随机崩溃 | bpy 只在主线程；生产单进程 |
| 长 Cycles 渲染 | GUI/MCP 阻塞和超时 | 独立 attempt worker |
| 资产许可证 | 商业交付风险 | AssetManifest + quarantine + license gate |
| AGPL 参考项目 | 闭源传播义务 | 只学习模式，不复制代码；需许可再集成 |
| 遥测 | 客户场景和提示泄露 | `DISABLE_TELEMETRY=true`，生产不用第三方桥 |
| 集成显卡性能 | 8K 不可用 | 新电脑独显或专用 GPU worker |
| AI 再编辑终稿 | 重新破坏几何和地板 | 正式 ERP 禁止生成式后处理 |
| 地板参数缺失 | 伪物理尺度 | 缺板宽/长/厚直接阻止正式渲染 |
| 重复彩膜 | 大面积克隆感 | lane/phase/rotation 确定性分配 + Gold Set |

---

## 18. 最小成功定义

第一阶段只有同时达到以下条件，才可以对用户说“Blender 新路线成功”：

1. 真实三维房间由 GeometryManifest 建成；
2. 墙在八个标准透视方向保持直线，没有球形鼓包；
3. 地板来自指定原厂彩膜 hash；
4. 板宽、板长和周期能从三维场景反测；
5. 家具遮挡由真实几何产生，无蒙版切割；
6. 360° ERP 由一个 Cycles 全景相机直接输出；
7. 没有 cubemap AI 拼缝；
8. 没有正式 ERP 的 AI 自由后处理；
9. 同一 SceneRecipe 连续运行两次，几何、相机和地板 hash 一致；
10. 真实 VR Viewer 实机检查通过；
11. 用户肉眼认可，不以测试数量替代视觉结果。

---

## 19. 研究与官方资料

### 高星项目

- ahujasid/blender-mcp：<https://github.com/ahujasid/blender-mcp>
- RFingAdam/mcp-blender：<https://github.com/RFingAdam/mcp-blender>

### OpenAI / Codex

- Codex MCP 官方文档：<https://developers.openai.com/codex/mcp>
- Codex config reference：<https://developers.openai.com/codex/config-reference>

### Blender

- Blender LTS：<https://www.blender.org/download/lts/>
- Blender Python API：<https://docs.blender.org/api/current/>
- Python threads gotcha：<https://docs.blender.org/api/5.0/info_gotchas_threading.html>
- Cycles panoramic camera：<https://docs.blender.org/manual/de/latest/render/cycles/object_settings/cameras.html>
- Cycles GPU rendering：<https://docs.blender.org/manual/id/5.2/render/cycles/gpu_rendering.html>
- Command line：<https://docs.blender.org/manual/id/4.2/advanced/command_line/arguments.html>

---

## 20. 给下一位 Codex 的启动指令

可以把下面内容作为新会话的第一条任务：

```text
请先完整阅读 docs/CODEX_BLENDER_360VR_IMPLEMENTATION_HANDOFF.md，
再阅读 docs/WHOLE_HOME_3D_PIPELINE.md、
docs/PLAN_TO_3D_CORRESPONDENCE_LOCK_V1.md、
whole_home_geometry_kernel.py、film_repeat_floor.py。

不要继续修旧 AI ERP 的地板蒙版。先执行文档 M0：
1. 探测并固定 Blender LTS runtime；
2. 新建 blender_pipeline 的无 bpy 合同层和 Blender scripts 层；
3. 使用现有 GeometryManifest 构建一个最小真实房间；
4. 使用 HANDOFF_ASSETS 中 VL88238XL(EIR)-006 彩膜建立真实板片；
5. 由 Cycles equirectangular 相机输出 1024×512 ERP 和 AOV；
6. 接入现有 PanoViewer；
7. 生成八方向截图并用肉眼与硬门禁验收；
8. 不允许任何 AI 对最终 ERP 再编辑；
9. 改动后运行 targeted tests、全量 pytest、Web tests 和 build；
10. 未实机看到通过结果前，不得声称成功。
```

---

## 21. 最后原则

> AI 负责提出设计；Codex 负责把设计变成可审计的数据和 Blender 场景；Blender 负责真实几何、物理材质、光照、遮挡和球面终稿；Floor Engine 负责工作流、门禁、历史和 VR 交付。任何正式像素都必须能回溯到共享 SceneRecipe、GeometryManifest、资产 hash、彩膜 hash 和确定性渲染 attempt。
