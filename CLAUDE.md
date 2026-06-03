# 项目架构与 AI 编码规范 (AI Coding Guidelines)

> **⚠️ 核心性能指令 (Core Directives for AI):**
> 代码位于 `D:\test\floor_engine`，是一个 Python 包。为了极大节省 Token 消耗并避免上下文污染，你必须严格遵守以下规矩：
> 1. **严禁在收到任务时通读全部文件。**
> 2. 执行任务前，先阅读下方的【模块职责地图】定位相关模块。
> 3. 必须优先使用搜索工具 (如 `grep`) 查找目标变量或函数的签名，确认位置后再针对性地读取局部代码。
> 4. 底层模块绝对不能反向引用上层界面的逻辑。

> **▶️ 运行与导入须知 (Run / Import):**
> *   启动：从上一级目录 `D:\test` 执行 `python -m floor_engine`（NiceGUI Web 应用，默认端口 `7869`，可用环境变量 `FLOOR_AI_PORT` 改）。
> *   **各模块用相对导入** (`from .config import *`)，因此**不能**单独 `import prompt_data` 或在 `floor_engine\` 内直接跑单文件——会报 `ImportError: attempted relative import`。做校验/单测请从 `D:\test` 用 `from floor_engine import xxx`。

---

## 🗺️ 模块职责地图 (Module Map)

项目共拆分为 8 个核心模块，呈严格的单向金字塔依赖（从 1 到 8）：

### 1. 基础设施与数据层 (底层，无依赖)
*   **`config.py`**: 全局配置、路径管理、基础常量 (`GEMINI_MODEL_MAP`)、UI 主题 CSS 样式、通用小工具。
*   **`models.py`**: 纯数据类定义 (仅包含 `JobRecord`)，可独立单测。

### 2. 核心业务逻辑层
*   **`records.py`**: 历史记录 JSON 的增删改查、图片 Base64 编解码、提示词安全混淆。**（纯存储层，不含外部 API 和 UI）**
*   **`api.py`**: 负责调用外部 Gemini大模型 (`call_gemini_generate`, `call_gemini_edit`)、参照模式风格分析 (`analyze_style_image`)、图像色彩对齐 (`_match_color_to_reference`)，以及 Pro 出图后**磨缝/去缝**的编辑指令常量 `FLOOR_DESEAM_INSTRUCTION`（拼法中性：保持原拼法+锁色，**勿写死成某种拼法**）。

### 3. 提示词工程层 (高度解耦)
*   **`prompt_data.py`**: 静态数据源、文案映射表、翻译引擎。包含海量下拉框选项的底层中英字典（如风格、地区等）。**（只存数据和短文本逻辑）**
*   **`prompts.py`**: 核心提示词拼装器 (`save_task_files_html`)。调度 `prompt_data` 和 `records`，拼装最终英文提示词。**（支持脱离 UI 独立测试）**
    *   **无缝地板 = B2/Pro 双版本策略**（`is_seamless_clean` 分支）：B2 用温和原版；Pro 用"整块浇筑大板+表面仅印花木纹"的**对抗版**（更能压住缝隙）。Pro 版靠 `CORE_MATERIAL_INSTRUCTION_PRO` / `en_floor_sz_seamless_pro` / `en_surface_direction_pro` / `SEAMLESS_NEGATIVE_PRO(_MOTIF)` 四组变量；仅当它们被赋值时，末尾 `final_prompt_en.replace(...)` 才把 B2 段替换为 Pro 段。新增拼法的 Pro 版照此设这四个变量即可。

### 4. 表现与入口层 (顶层)
*   **`webui.py`**: 前端界面构建、参数表单、任务队列管理 (`_new_job`)、调度底层执行。依赖下方所有模块。
*   **`__main__.py`**: 程序的唯一启动入口，负责设置端口和拉起 UI。

---

## 🛠️ 修改导航指南 (Navigation Guide)
*   **改界面/加按钮/调队列** $\rightarrow$ 去 `webui.py`
*   **改文案/加选项字典/改翻译** $\rightarrow$ 去 `prompt_data.py` (若需界面联动再看 `webui.py`)
*   **改提示词组合顺序/逻辑** $\rightarrow$ 去 `prompts.py`
*   **改大模型调用参数** $\rightarrow$ 去 `api.py`
*   **改存储格式/图片处理** $\rightarrow$ 去 `records.py`
*   **改磨缝/去缝逻辑** $\rightarrow$ 指令在 `api.py` 的 `FLOOR_DESEAM_INSTRUCTION`，调用流程在 `webui.py` 的 `_polish_pro`（磨缝按钮）
*   **改无缝/拼法（直拼·人字拼·正方形拼）逻辑** $\rightarrow$ 去 `prompts.py` 的 `is_seamless_clean` 分支（注意 B2/Pro 双版本）
*   **加/改装修风格** $\rightarrow$ `prompt_data.py` 的 `STYLES`(顶部 Style 下拉列表) + `STYLE_ATMOSPHERE_MAP`(atm/must/ban)，两者 key 须**逐字符匹配**（括号内英文名对应 map key）
