# 当前验证索引

更新日期：2026-09-23。源版本见 [VERSION](../VERSION)。本页区分源码回归与可执行文件验证；历史报告不自动代表当前发布产物。

## 源码基线

第七轮提交恢复实现：[协议与行为报告](./SUBMISSION_RELIABILITY_ROUND_7.md)。当前后端全套 **371 passed、1 skipped**（含本地 Blender/IFC 集成），Node **52 passed**，浏览器 **55 passed**，类型、ESLint、静态构建与 7 份文档检查通过。路由契约为 102 个路径/方法条目。独立构建环境的实际依赖也通过后端全套 371 项、跳过 1 项。跳过项仍是缺少操作员彩膜素材。新增测试不调用付费模型；断响应浏览器用例连接真实隔离提交 API 和离线 worker。包含查询途中清卡后释放迟到响应的回归。

## v7.1.2 正式发布结果

[v7.1.2 正式 Release](https://github.com/Bok1-YY/Floor_rendering_engine/releases/tag/v7.1.2) 已发布，非 Draft、非 Prerelease。标签指向已验证源提交 e30662876c3ca11d3ee00ac839f1451dc28780fe。Windows ZIP 为 168,231,907 字节，SHA-256 为 6cb2580619ff80ead5fcaca76ab966fb17f2c2e0c611e925bcc009f401b8a104；exe 为 167,486,976 字节，SHA-256 为 cbbd24dea9e736b3950751ba0107a52105c7682608e7edd74779bc4c6fd614c9。

同一份 exe 在仓库外中文及空格路径、独立数据根和仅系统目录 PATH 下通过 18 组检查：提交实例与凭据重放、参数和实例冲突、损坏凭据、清卡后查询、正常关闭重启、默认 exe 旁数据根、真实页面及图片解码、PDF、校色与 MobileSAM、记录评审/收藏/HTML/PPTX、本地补写，以及 Blender 冷开、GLB 导入和 IFC 写出回读。第三方模块路径来自 onefile 临时运行目录。源码浏览器用例验证首次受理与响应丢失，exe 使用预置隔离凭据重放；没有调用付费模型。

本机隔离验证不等于全新 Windows 验证。包未签名；Microsoft Visual C++ v14 x64 运行库及 Blender 仍为外部依赖，包内提供微软运行库下载入口。完整机器可读结果见 [v7.1.2 验证记录](./validation/v7.1.2.json)。

三份公开附件均已匿名重新下载，哈希全部与已验证本地文件一致。清理例外：自动审批同时拒绝了经所有权核对的批量清理和单个临时目录的直接删除，只返回 blocked by policy，没有具体原因，删除命令没有执行。本机 .tools/release-round7 工作目录及 12 个外部隔离测试目录仍保留；该工作目录内已准备 cleanup-round7.ps1 手动清理脚本及所有权清单，脚本只做语法检查，未代为执行。正常开发环境、静态前端和用户数据保留。

第五轮结束后的完整验证：后端 **344 passed、1 skipped**；Node **47 passed**；浏览器 **42 passed**；TypeScript、ESLint、Next 静态构建通过。跳过项是缺少操作员彩膜素材，包含真实本地 Blender/IFC 集成。

第六轮增加打包资源回归后，源码完整验证为 **349 passed、1 skipped**；Node **47 passed**、浏览器 **42 passed**，类型、ESLint、静态构建与 7 份维护文档检查通过。API 契约覆盖 101 个路径/方法条目。可执行文件是否已构建、是否通过隔离验证及是否发布，必须以 Release 附件报告和真实链接为准。

```powershell
.\.venv\Scripts\python.exe tools/verify.py --integration
.\.venv\Scripts\python.exe tools/check_docs.py
```

构建、隔离和清理流程见 [Windows 发布说明](./WINDOWS_RELEASE.md)。离线接口替身不验证供应商账单或真实云模型质量。本轮没有全新 Windows 系统验证。

## v7.1.1 正式发布结果

[正式 Release](https://github.com/Bok1-YY/Floor_rendering_engine/releases/tag/v7.1.1) 已发布。构建源提交为 d125182f08ccb25d1b96a0843f3424f9511008b9；发布附件已重新下载，三项 SHA-256 均与本地已验证文件一致。

Windows ZIP 为 168,111,001 字节；exe SHA-256 为 3e3ef1a97068227aa602a707093bf361ca3475b5a5b7507b72ff7b3974b95478。机器可读结果见 [v7.1.1 验证记录](./validation/v7.1.1.json)。

实际 exe 通过真实页面与图片、静态资源、PDF 上传渲染、上传/配置、校色与 MobileSAM、记录评审与 HTML/PPTX、正常关闭重启、幂等本地补写、默认 exe 旁数据根，以及 Blender 冷开、GLB 导入和 IFC 写出回读。也检查了第三方模块来自单文件临时解包目录，而非构建环境。

发布附件回下载校验后，本轮构建环境、编译缓存、中间文件、五个隔离测试目录、本地发布包及下载副本均已清理；正常 .venv、web/node_modules、web/out 和 data 保留。

收尾例外：本机 .tools 目录中的 round6-cleanup.ps1 与 round6-cleanup-manifest.json 两份临时辅助文件仍保留，不属于仓库源码。自动审批拒绝了删除操作，仅返回 blocked by policy；大型构建和测试目录已确认删除。

本机隔离验证不等于全新 Windows 验证；程序未签名，未执行真实付费模型调用。Microsoft Visual C++ v14 x64 运行库和外部 Blender 的前提在随包说明中列明。

## 历史依据

- [第一轮：审阅与基础修复](./REFACTOR_AUDIT_20260912.md)
- [第二轮：可靠性与本地恢复](./RELIABILITY_ROUND_2_20260912.md)
- [第三轮：编辑器](./EDITOR_REFACTOR_ROUND_3_20260912.md)
- [第四轮：生成工作台](./GENERATION_REFACTOR_ROUND_4_20260913.md)
- [第五轮：记录库与任务卡](./RECORD_JOB_REFACTOR_ROUND_5_20260913.md)
- [2026-08-31 Runnable 历史基线](../RUNNABLE_BASELINE.json)

旧 RUNNABLE_BASELINE.json 保持其原始日期、提交和计数，不回填新结果以伪装成连续已验证记录。
