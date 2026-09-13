# Windows 构建与正式 Release

版本来源为根目录 [VERSION](../VERSION)，构建入口是 [build_windows.bat](../build_windows.bat) / [tools/build_windows.py](../tools/build_windows.py)。仅 Windows x64、Python 3.12 执行此构建；Node.js 20.9+，Nuitka 等工具固定在 [requirements-build.txt](../requirements-build.txt)。

## 准备与构建

1. 完成源码回归与文档检查，提交全部发布变更。构建器拒绝脏工作区。
2. 检查远端标签和 Release，选择未使用的版本。版本从 7.1.1 起，冲突时顺延补丁号，不覆盖旧版。
3. 执行 build_windows.bat --non-interactive；也可添加 --work-dir 指定本轮专属工作目录。已有目录必须包含构建器识别的 ownership.json，不使用未标记目录。
4. 构建器使用 git archive 固定 HEAD，安装独立构建依赖，在快照内 npm ci/build，编译使用 2 个任务及低内存模式，并先运行小型编译器预检。输出位置见工作目录的 latest-build.json。

发布程序包含前端、必要本地图像资源、IfcOpenShell 及其属性模板数据、Python 运行依赖。产品未使用的 EXPRESS 规则集合和 ONNX 训练/量化工具不进入发行程序，保留推理和本地 IFC 构建所需模块。Blender 为外部依赖。Blender 单独解释器所需的源码存入打包资源 ZIP，在程序的临时资源目录中释放；只允许预期的 Python 文件路径。构建版本元数据不再使用固定 7.1.0 或虚构公司名。

固定版本 MinGW 的头文件搜索问题由编译器预检处理：仅从该工具链复制对应头文件到本轮 include 覆盖目录，记录其 SHA-256；不改系统 SDK。应用自身模块保持 C 编译；第三方 Python 模块主要以 Nuitka 内置字节码分发（AnyIO 为兼容当前 Nuitka 的插件生成代码保留原生编译）（清单记入构建报告），保留其原生扩展。该方式避免大型 SWIG 绑定的 C 编译内存溢出，并减少无必要的依赖重编译；PDF、IFC 和图像库须经实际运行验证。

构建报告记录源 SHA、工具与依赖版本、可执行文件 SHA-256、文件大小及编译命令。构建不删除旧 dist、开发环境或用户数据。失败产物由明确的收尾步骤处理。

## 本轮可执行文件验证

将最终 exe 放到仓库之外、含中文与空格的隔离目录。使用独立端口、独立数据根和受控 PATH，不把项目 Python/Node/虚拟环境加入运行路径；从非源码目录启动，禁止使用真实 API Key 发起请求。

可通过 FloorEngine.exe --verify-local-model 输入结构包.json --output 独立目录 验证冻结程序中的本地建模资源；该命令仅执行结构合同、Blender 和 IFC，不调用生成 API。

检查健康接口、静态首页与子页面、配置、上传、记录、评审、导出、本地图像处理、模型资产、本地研究建模或依赖缺失提示，以及停止、重启和已保存结果恢复。测试使用合成数据，不对真实记录或素材做破坏性操作。验证的是将要发布的同一份 exe，不能在验证后换成未经验证的新编译文件。

**本轮由用户选择本机隔离验证后发布正式版。** 这不等于全新 Windows 系统验证，也不证明所有外部模型可达或所有素材质量通过。没有代码签名时明确标注 unsigned；Blender 外部复审不可用与机械失败分开记录。

本轮 Nuitka 报告部分 Visual C++ DLL 未内置。本机已有运行库；发布包提供微软官方下载入口，不额外分发或自动安装该组件，不将其视为无系统前提的产物。参见[微软运行库文档](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。

## Release 附件与发布

- Windows x64 便携 ZIP：FloorEngine.exe、使用说明、LICENSE、第三方声明及可获得的依赖许可证。
- SHA256SUMS.txt：可下载附件的 SHA-256。
- 验证报告：版本、源提交、测试环境、通过项、跳过项与限制；公开报告去除不必要的个人机器路径。

推送当前分支与指向已验证源提交的标签，不强推、不改其他分支。先建立 Draft Release 并上传附件，完成核对后设为正式版。发布后重新下载附件核对校验和。构建未通过、关键验证失败或附件不完整时不发布。

## 清理

本轮创建的构建环境、源码快照、编译缓存、中间文件、临时验证数据、下载核验副本及本地发布包均列入所有权清单。删除前检查绝对路径属于已确认的本轮目录，停止对应测试进程，使用同一种 shell 的原生路径操作完成清理。

保留正常 .venv、web/node_modules、web/out、真实 data、配置、历史图片、源代码和验证摘要。不清理其他构建轮次、用户目录或现有 Release。

若已验证但上传受阻，清理中间物，仅保留一个明确标识的待发布包和校验文件，报告其位置和原因。上传成功且下载核验通过后再删除本地发布副本。

[Nuitka 官方手册](https://nuitka.net/user-documentation/user-manual.html)说明独立部署需要 standalone/onefile 模式；本项目使用 onefile，使用独立源码快照和构建报告记录实际产物。
