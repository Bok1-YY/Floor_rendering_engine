# 第一阶段执行报告：Plan-to-3D Correspondence Lock v1

> 状态：已实现并通过 L1–L5 同源金标准  
> 执行日期：2026-08-13 至 2026-08-14  
> 适用仓库：`Floor_engine_Linux`  
> 上位计划：[全屋自动建模、多点位一致性与 Image2 质感——全流程开发计划](./全屋自动建模_多点一致性与Image2质感_全流程开发计划.md)

## 1. 结论

第一阶段已经形成可执行闭环。当前最高效且风险最低的方法不是先训练“户型图直接出 3D”的新模型，而是把输入分成两条证据等级不同的链路：

1. DWG/DXF 使用本地确定性解析，`$INSUNITS` 提供米制比例，CAD 实体 handle 和 INSERT 变换提供事实溯源；
2. PNG/JPG/PDF 使用可逆像素配准、至少一条真实尺寸锚点（网页默认要求两条）、服务端墙线反投影量测和逐项人工终验；
3. 两条链路最终都编译为同一 `GeometryManifest`；网页 3D、服务端透视/球面渲染和后续相机规划只能读取这份锁定网格；
4. 没有通过对应验收的 v3 项目，不能创建机位、球面热点、参考 capture、自动计划或发起付费图像生成。

这套方案把 AI 放在“提出普通户型图结构草稿”的位置，而不让 AI 自己证明自己正确。正式几何权威始终来自原始 CAD，或来自带真实尺寸、可逆配准和人工逐项确认的普通户型图。

## 2. 已落地的系统闭环

~~~text
DWG / DXF
  → 本地单位、候选平面、墙线与 INSERT 解析
  → WallAssembly（墙 footprint + 中心轴 + 真实厚度 + CAD handle）
  → SourceRegistration（CAD 坐标 → 米制模型）

PNG / JPG / PDF
  → 原图 SHA-256 + EXIF/PDF 页处理
  → crop / rotation / perspective 可逆矩阵
  → 两条尺寸锚点 + 模型原点
  → SourceRegistration（像素 → 米制模型）
  → 服务端把模型墙轴反投影到原图墨迹并量测 p95
  → 人工逐房、逐门窗、逐不确定项确认

两条链路
  → WholeHomeModel revision
  → 同一几何内核切墙、切洞、建地板/天花/固定物
  → GeometryManifest（全局 vertices + semantic indexed parts）
  → GeometryAcceptanceReport
  → production_readiness
  → 网页 3D / CPU renderer / cubemap / ERP 共用同一 mesh hash
~~~

### 2.1 SourceRegistration v1

`SourceRegistration` 保存并校验：

- 原始文件 SHA-256；
- 输入等级：`vector_authoritative`、`raster_draft` 或 `raster_human_locked`；
- `source_to_canonical`、`canonical_to_source`；
- `canonical_to_model`、`model_to_canonical`；
- CAD 单位，或普通户型图的真实尺寸锚点；
- 独立尺寸锚点之间的比例差；
- 正反变换 round-trip 误差；
- 覆盖所有几何变换和比例事实的稳定 `registration_hash`。

硬规则如下：

- CAD 仅接受明确的 mm、cm、m，不猜单位；
- 普通户型图正式锁定必须至少一条真实尺寸，网页工作流默认要求两条；
- 多条尺寸换算的比例差不能超过 2%；
- 普通户型图配准 round-trip 不能超过 0.25 px；
- `canonical_to_model` 必须是可逆、等比例的二维相似变换；
- 原图、变换、比例或锚点改变会生成新 hash；
- 文件在磁盘中的保存路径不参与 hash，内容 SHA-256 参与，因此移动项目目录不会误报几何变化。

### 2.2 WallAssembly v1

CAD 原始线段不再一律套用 120 mm 默认厚度。墙体装配支持：

- `paired_faces`：双墙面自动配对；
- `closed_footprint`：闭合墙带直接成为墙 footprint；
- `centerline`：中心线必须有明确厚度来源；
- `human_confirmed_ambiguous`：歧义墙必须人工确认。

自动配对阈值：

- 平行角误差不超过 1°；
- 双面间距 0.06–0.60 m；
- 有效重叠不低于 80%；
- 端点吸附不超过 20 mm；21 mm 不允许吸附；
- 吸附簇中任意两点都必须满足 20 mm，禁止通过传递链把远点间接合并。

每个正式墙体保留源实体 handle、根 INSERT handle、图层、嵌套变换、原始 segment/polygon、厚度来源、墙高来源和复核状态。复杂闭合轮廓无法证明唯一中心轴时保持待复核，不伪造中心线。

### 2.3 GeometryManifest v1

锁定模型由单一几何内核编译为：

- 全局 `vertices`；
- `wall_parts`、`floor_parts`、`ceiling_parts`、`object_parts`；
- 每个 part 使用全局三角形索引并保留 `entity_id` 和语义类型；
- `opening_voids` 保存门窗在墙轴上的区间、宽高和窗台高度；
- `model_facts_hash`、`registration_hash`、内核版本和 `manifest_hash`。

网页 Three.js 不再根据 2D 墙数据自行猜一套立方体；服务端软件渲染器也不再走另一套墙逻辑。两者优先展开同一 GeometryManifest。模型、配准或几何内核任意变化后，旧报告和旧 manifest 自动失效。

## 3. 可量化验收门槛

### 3.1 CAD → 模型

| 指标 | 正式门槛 |
|---|---:|
| 权威 CAD 几何 provenance 覆盖率 | 100% |
| 正式墙 WallAssembly 覆盖率 | 100% |
| 墙边界 p95 | ≤ 0.05 m |
| 墙边界最大误差 | ≤ 0.10 m |
| 房间面积最大相对误差 | ≤ 1% |
| 合格房间覆盖率 | ≥ 98% |
| 房间真实重叠面积 | ≤ 1e-6 m² |
| 外围最大裂缝 | ≤ 0.02 m |
| 门窗中心/宽度 p95 | ≤ 0.05 m |
| 孤立、越界、重叠开口 | 0 |
| 未解决墙体 | 0 |

### 3.2 普通户型图 → 模型

| 指标 | 正式门槛 | 证据来源 |
|---|---:|---|
| 真实尺寸锚点 | ≥ 1；网页默认 2 | 用户在原图点击并输入实测长度 |
| 锚点比例差 | ≤ 2% | 服务端重算 |
| 配准 round-trip | ≤ 0.25 px | 服务端矩阵计算 |
| 模型墙轴对原图墨迹 p95 | ≤ 0.10 m | 服务端反投影和 OpenCV 距离场 |
| 房间 IoU | ≥ 0.95 | 叠加复核 / 金标准自动量测 |
| 已确认门窗 precision / recall | 1.0 / 1.0 | 逐门窗人工确认 / 金标准自动量测 |
| 人工复核完成度 | 1.0 | 显式 checklist 与 reviewer/note |
| 未处理复核项 | 0 | 显式 checklist |

客户端不能覆盖服务端测得的墙线 p95。普通户型图最终确认时，网页要求同时确认：全部房间、全部门窗、没有遗留不确定结构，以及 2D 图纸没有证明的墙高/楼板等竖向假设。

### 3.3 模型 → 3D 三角网格

| 指标 | 正式门槛 |
|---|---:|
| 地板 footprint IoU | ≥ 0.999 |
| 墙 footprint 对称差 | 面积 ≤ 1e-4 m²，或相对差 ≤ 0.001 |
| 门窗区间误差 | ≤ 1e-6 m |
| 2D 模型与 3D 顶投影 IoU | ≥ 0.995 |
| manifest 孤立开口 | 0 |

## 4. 网页工作流

在“校准并复核完整整屋模型”中会显示 `Plan-to-3D Correspondence Lock v1` 状态。

CAD 项目：

1. 上传 DWG/DXF；
2. 本地解析器自动生成 CAD SourceRegistration；
3. 歧义墙在墙装配确认接口中填写实测厚度和理由；
4. 没有可靠 INSERT 的门窗使用开口标注接口补充，但必须绑定 WallAssembly；
5. 填写复核说明并确认竖向工程假设；
6. 运行对应验收，通过后再执行整屋几何锁定。

普通户型图项目：

1. 先保存 AI/人工修正后的 2D 结构草稿；
2. 在原图上点击两条已知尺寸的端点并输入真实米数；
3. 点击模型 `(0,0)` 对应的原图位置；
4. 服务端重新读取原图、校验 SHA-256、生成可逆矩阵并把模型墙轴反投影到图纸墨迹；
5. 逐项确认房间、门窗和无遗留问题；
6. 填写复核说明并确认墙高/楼板假设；
7. 运行对应验收。

按钮在 registration 缺失、checklist 未完成或报告未通过时保持禁用。即使绕过前端直接请求，后端生产门禁仍会阻止机位、capture、全景和付费调用。

## 5. API

| 方法与路径 | 用途 |
|---|---|
| `PUT /api/whole-home/projects/{id}/source-registration` | 保存合同格式配准；CAD 会与服务端 `$INSUNITS` 事实重新比对 |
| `POST /api/whole-home/projects/{id}/source-registration/raster` | 原图服务端哈希、尺寸锁定、弱结构证据和墙线反投影量测 |
| `POST /api/whole-home/projects/{id}/geometry-acceptance` | 预览或提交对应验收；只有 `passed` 才能 commit |
| `GET /api/whole-home/projects/{id}/geometry-acceptance` | 读取 registration、报告、manifest 摘要和 readiness |
| `GET /api/whole-home/projects/{id}/geometry-manifest` | 单独读取正式网格，避免塞入热项目响应 |
| `POST /api/whole-home/projects/{id}/cad/wall-assemblies/{assembly_id}/confirm` | 人工确认歧义墙的中心轴、厚度和墙高 |
| `PUT /api/whole-home/projects/{id}/cad/opening-annotations` | 为缺少可靠 INSERT 的门窗补充可审计区间 |

所有写接口都带 `base_revision` 和 `operation_id`；旧 revision 写入会失败。提交报告绑定 source/model/registration/kernel/manifest hash，同一个模型改一个坐标也会使旧锁失效。

## 6. 渐进公开测试集

测试集目录只提交元数据、许可分类、来源 revision、文件大小和 SHA-256。原始 IFC、派生 CAD、图片和 3D 真值保留在已忽略的：

~~~text
data/external_datasets/whole_home_geometry
~~~

目录中已经登记 L1–L5 难度规则和开发/验证/封闭 holdout 分组，并按 building group 检查数据泄漏。自动下载只允许代码中的官方 HTTPS 前缀；协议要求申请、token、非商业或数据许可不明确的集合不会被脚本自动同意条款或寻找镜像。

L1–L5 已按难度递进执行。L1 使用 IFC-Bench FZK House；它不是四墙玩具案例，选定地面层包含：

- 9 面正式墙；
- 6 个 IFC 空间；
- 5 扇门；
- 9 扇窗；
- 楼梯和楼板；
- 外墙、内墙、多厚度、内外开口和二层关系。

固定源 IFC：

~~~text
size:   2,570,803 bytes
sha256: 70cc8ff245fc0894201d96496c031005a5cbd7a96b22d8a1b87c5a883fb77994
~~~

同一 IFC storey 被确定性派生为：

- `input_double_line.dxf`：真实米制、墙 footprint、门窗块、房名和尺寸；
- `input_dimensioned.png`：普通户型图输入；
- `truth_geometry.json`：独立 IFC 平面事实；
- `truth_geometry_manifest.json`：独立 IFC 三角网格；
- `truth_gray_model.obj`：可用通用 3D 工具查看的灰模；
- `truth_gray_preview.png`：快速人工检查图；
- `cad_gold_result.json`、`raster_gold_result.json`：机器判定结果。

两次独立派生必须逐文件 SHA-256 完全一致。DXF 的 writer 时间戳会被固定值规范化；回归测试专门检查这件事，避免“同一源文件每次生成不同测试集”。

## 7. L1 实测结果

### 7.1 CAD 生产解析器 vs 独立 IFC 真值

| 指标 | 结果 | L1 门槛 |
|---|---:|---:|
| 墙 footprint IoU | 1.0000 | ≥ 0.98 |
| 墙边界 p95 | 0.000 m | ≤ 0.05 m |
| 房间 footprint IoU | 1.0000 | ≥ 0.95 |
| 门窗 precision | 1.0000 | ≥ 0.90 |
| 门窗 recall | 1.0000 | ≥ 0.90 |
| 门窗中心误差 p95 | 0.150 m | ≤ 0.20 m |
| 门窗宽度误差 p95 | 0.000 m | ≤ 0.05 m |
| 墙装配覆盖率 | 1.0000 | 1.00 |
| 生产解析 hard errors | 0 | 0 |

外墙 IFC opening 的中心位于原墙厚范围内，而生产模型使用墙中心轴，因此外侧开口存在最多约半墙厚的中心偏移；这由外部真值报告为 0.15 m，没有被伪装成 0。墙 footprint、门窗类型和宽度均保持一致。

### 7.2 普通户型图注册与像素证据 vs 独立 IFC 真值

| 指标 | 结果 | L1 门槛 |
|---|---:|---:|
| 独立尺寸锚点 | 2 | ≥ 1 |
| 锚点比例差 | 0.0000 | ≤ 0.02 |
| 配准 round-trip | 0.000 px | ≤ 0.25 px |
| 墙中心轴 p95 | 0.000 m | ≤ 0.05 m（金标准） |
| 墙墨迹支持率 | 1.0000 | ≥ 0.95 |
| 房间像素 IoU | 0.9834 | ≥ 0.95 |
| 门窗 precision / recall | 1.0000 / 1.0000 | ≥ 0.90 / 0.90 |

## 8. L2–L5 真机执行结果（2026-08-14）

L2–L5 沿用 L1 的正式阈值，没有为了让高级案例通过而放宽门槛。每个案例都真实下载公开 IFC 和官方快照，从选定楼层派生 DXF、普通户型图、独立 GeometryManifest、OBJ 灰模和预览；随后分别经过生产 CAD 解析器与普通户型图配准/像素量测链，并发布到程序历史记录。失败尝试也保留在历史时间线，便于看到问题如何被修正，而不是只展示最后结果。

### 8.1 递进案例与规模

| 等级 | IFC-Bench 案例 | 选定楼层 | 墙 | 空间 | 开口 | 主要挑战 | 最终状态 |
|---|---|---|---:|---:|---:|---|---|
| L2 | City House Munich | 2. Erdgeschoss | 11 | 4 | 7 | 单位/楼层筛选、局部构件分解 | passed |
| L2 | Duplex | Level 2 | 25 | 10 | 多房间、24 个门窗洞 | passed |
| L3 | Fantasy Residential Building 1 | Ground Floor | 36 | 19 | 55 个 IFC opening、泛型洞口语义 | passed |
| L3 | Samuel Macalister Sample House | Level 2 | 17 | 8 | 毫米 IFC 元数据、分解构件 | passed |
| L4 | Smiley West | EG | 81 | 30 | 70 个开口、干净图与压缩图双通道 | passed |
| L5 | Schependomlaan | Storey-1 | 175 | 47 | 源 IFC 无有效 IfcSpace/IfcOpeningElement，物理楼层切片恢复 | passed |
| L5 | Sixty5 | 03 derde verdieping | 428 | 39 | 342 MB 高层 IFC、占位空间、多单元核心筒、158 个候选开口 | passed |

### 8.2 CAD 与普通户型图量测

| 等级 / 案例 | CAD 墙 IoU | CAD 房间 IoU | CAD 开口 P/R | Raster 房间 IoU | Raster 开口 P/R |
|---|---:|---:|---:|---:|---:|
| L2 City House Munich | 0.999997 | 0.999984 | 1.000 / 1.000 | 0.997828 | 1.000 / 1.000 |
| L2 Duplex | 1.000000 | 1.000000 | 1.000 / 1.000 | 0.996585 | 1.000 / 1.000 |
| L3 Fantasy Residential | 0.999999 | 0.978457 | 1.000 / 1.000 | 0.992842 | 1.000 / 1.000 |
| L3 Samuel Macalister | 0.999998 | 0.999997 | 1.000 / 1.000 | 0.992008 | 1.000 / 1.000 |
| L4 Smiley West（原图） | 1.000000 | 0.999999 | 1.000 / 1.000 | 0.962639 | 1.000 / 1.000 |
| L4 Smiley West（确定性 JPEG 压缩） | — | — | — | 0.950176 | 1.000 / 1.000 |
| L5 Schependomlaan | 0.998596 | 0.989668 | 1.000 / 1.000 | 0.995094 | 1.000 / 1.000 |
| L5 Sixty5 | 0.999998 | 0.992648 | 1.000 / 1.000 | 0.987120 | 1.000 / 0.949367 |

所有 CAD 墙边界 p95 都远低于 0.05 m，所有 WallAssembly 覆盖率都是 1.0；所有栅格配准 round-trip 都为 0 px、墙墨迹支持率均达到正式门槛。Sixty5 栅格漏掉 8/158 个极密集小开口，但 0.949367 的召回仍高于事先锁定的 0.90 门槛，报告没有把它伪装成满分。

### 8.3 L5 恢复路径与确定性验证

对于缺失真实空间语义、或把 `IfcSpace.LongName` 标成 `kavel/parcel` 占位地块的 IFC：

1. 从薄楼板的主导顶面标高确定物理楼层基准，在其上方 1.20 m 做可审计切片；
2. 只保留穿过切片且能证明为墙带的几何，方形幕墙板和不明确组件不会被强行当墙；
3. 从同楼层完成面板恢复房间，排除 `vloerstort` 等整体结构浇筑板；
4. 从门/窗填充构件恢复开口，限定 0.40–3.00 m 的住宅尺度，并要求离最近墙不超过 0.35 m；
5. 把基准标高、切片标高、源实体 ID、排除项、恢复数量和容差写入 `extraction_warnings`，在历史详情页可展开逐项核对。

Sixty5 使用两个独立 Python 进程连续派生，6 个锁定产物的 SHA-256 逐项完全一致。过程中发现 ezdxf 的无序 `CLASSES` 表会随进程状态交换记录顺序；现在只对完整 CLASS 元数据记录按类名规范排序，不修改几何、实体或 handle，并已有字节级回归测试。

### 8.4 多模态与网页真机核验

- 人工查看了每级官方快照、带尺寸户型图和独立灰模预览；Sixty5 确认为多单元、重复核心筒、密集门窗的复杂高层楼层，不是为了过测试而选的简单矩形；
- `/records` 保存每次 passed/failed 时间序列、16 个正式指标、证据 SHA-256、下载副本和人工勾选进度；
- `/floorplan` 的整屋 3D 页面新增横向“Plan-to-3D 阶段验收”记录带，可直接查看 L1–L5 状态并跳到逐项核对；
- L5 历史详情额外显示 IFC 恢复与取舍依据，因此第二天回看时可以知道空间/开口是源语义还是确定性恢复所得。

本阶段没有调用 fal.ai 或 Image2。L2–L5 的目标是证明 CAD/户型图与 3D 几何对应，随机付费生图不能增加这项证明力；付费模型留给几何锁通过后的多点一致性和质感阶段。

## 9. 复现命令

从仓库根目录运行：

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools\whole_home_dataset.py audit
.\.venv\Scripts\python.exe tools\whole_home_dataset.py download --levels L1,L2,L3,L4,L5
.\.venv\Scripts\python.exe tools\whole_home_dataset.py verify-checksums --levels L1,L2,L3,L4,L5 --require-installed
.\.venv\Scripts\python.exe tools\whole_home_dataset.py prepare --levels L1,L2,L3,L4,L5
.\.venv\Scripts\python.exe tools\whole_home_geometry_gold.py all
~~~

最后一条命令只在 CAD 和普通户型图两个金标准都通过时返回退出码 0。详细结果位于：

~~~text
data/external_datasets/whole_home_geometry/prepared/
  ifcbench_<case>/same_source_gold_v2/
~~~

核心回归：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_whole_home_dataset.py `
  tests\test_whole_home_ifc_gold.py `
  tests\test_whole_home_wall_assembly.py `
  tests\test_whole_home_raster_registration.py `
  tests\test_whole_home_geometry_acceptance.py `
  tests\test_whole_home_geometry_acceptance_measurement.py `
  tests\test_whole_home_geometry_kernel.py `
  tests\test_whole_home_geometry_routes.py `
  tests\test_whole_home_cad.py `
  tests\test_whole_home_engine.py

Set-Location web
npm run build
~~~

### 9.1 2026-08-14 验收快照

- L2–L5 最终受影响范围回归：`211 passed, 7 warnings in 15.32s`；7 条 warning 均为 `ezdxf` 内部调用 `pyparsing` 旧接口产生的弃用提示；
- 数据集审计：13 个递进案例、8 个公开来源、8 个自动案例，`errors=[]`；尚未补齐的 validation/sealed holdout 只作为显式 warning 保留，不拿同一建筑变体伪装独立测试集；
- L1–L5 已安装的 8 个 IFC 与 8 张官方快照全部 checksum 验证，`missing=[]`、`corrupt=[]`；
- 七个 L2–L5 同源案例的 CAD 和普通户型图两条生产解析链均通过；Sixty5 跨独立进程派生产物 SHA-256 一致；
- 前端：Next.js 16.2.9 production build、TypeScript 检查和 `/floorplan` 静态页面生成通过；
- Python 静态编译与 `git diff --check` 通过（仅有 Windows 工作区的 LF→CRLF 提示）；
- 整仓 `pytest -q` 曾给出 300 秒完整运行窗口，但两次都在没有输出断言失败的情况下超时。因此本报告只声明上面明确列出的 289 项阶段一/受影响范围回归通过，不把整仓状态误写成通过。

### 9.2 产品内只读历史记录

L1–L5 金标准已经发布到程序的“记录”页；以下 L1 记录仍永久保留，最新记录会按执行时间默认打开：

~~~text
http://127.0.0.1:7870/records/
记录：Plan-to-3D_L1_ifcbench_fzk_house
record_id：geometry_audit_l1_bd975df6b2f6c424
audit_hash：bd975df6b2f6c424f47359031debce16f755101228b3b1f49122aa3cbb8d2ab6
状态：passed
~~~

这条记录包含 16 个可逐项勾选的 CAD/raster 验收指标、11 个带 SHA-256 的证据文件，以及官方快照、带尺寸户型图和独立 3D 灰模三张可视证据。证据本身只读；人工复核勾选、复核人和备注单独保存，不会改变机器验收结论或 `audit_hash`。证据下载端点会现场重算 SHA-256，副本被改动时返回冲突并拒绝下载。

以后 `tools\whole_home_geometry_gold.py run/all` 会默认归档 L1–L5 的成功或失败结果；同一 `audit_hash` 重跑幂等，不会制造重复历史。新证据或指标发生变化时会产生新的 record ID，从而保留真正的时间序列。

## 10. 主要代码位置

- `whole_home_geometry.py`：合同、阈值、hash、staleness 和 production readiness；
- `whole_home_wall_assembly.py`：CAD 双墙面/闭合墙带/歧义墙解析；
- `whole_home_raster_registration.py`：可逆图像配准、真实尺寸和弱结构证据；
- `whole_home_geometry_kernel.py`：唯一模型到三角网格编译器；
- `whole_home_geometry_acceptance.py`：服务端量测和验收报告；
- `whole_home_ifc_gold.py`：独立 IFC 同源派生与 CAD/raster 金标准；
- `whole_home_geometry_history.py`：金标准证据校验、只读历史归档与幂等 record ID；
- `whole_home_dataset.py`：公开数据、许可、checksum、难度与 split 框架；
- `routes_whole_home.py`：API、revision 写入、生产硬门禁；
- `web/src/app/floorplan/page.tsx`：普通户型图点击配准、逐项复核和验收状态；
- `web/src/components/WholeHomeStudio.tsx`：直接加载 GeometryManifest；
- `whole_home_software_renderer.py`：服务端读取同一 manifest。

## 11. 已知边界与下一步

1. L1–L5 开发集已经通过，但公开 validation/sealed holdout 仍有空槽，不能把当前结果表述成对任意事务所 DWG/任意扫描户型图的泛化证明。引入新来源时必须继续校验建筑组隔离和许可，不能用同一建筑变体填充 holdout。
2. 普通户型图的 AI 识别仍是候选生成器。当前正式路径依靠真实尺寸、服务器墙线墨迹量测和人工逐项确认；在积累足够金标准之前，不应把 AI confidence 当验收分数。
3. 当前二维图纸不能证明墙高、梁底、楼板厚度和吊顶构造。网页要求把这些作为工程假设显式确认；未来 IFC/BIM 可直接提供时，应切换为来源事实。
4. FZK 的 CAD 和 PNG 是从同一 IFC 确定性派生，适合验证对应关系与回归，不代表已经覆盖真实事务所 DWG 中的 XREF、XCLIP、动态匿名块、远原点和多图框；这些已放入更高难度规则。
5. 本阶段没有调用 fal.ai 或 GPT Image 2。它解决的是几何权威和可验证性，付费生成不能帮助证明 CAD/户型图对应，反而会把随机性引入金标准。Image2 提示词和质感实验应在对应锁通过后作为独立阶段执行。
