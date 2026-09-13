# 当前验证索引

更新日期：2026-09-13。源版本见 [VERSION](../VERSION)。本页区分源码回归与可执行文件验证；历史报告不自动代表当前发布产物。

## 源码基线

第五轮结束后的完整验证：后端 **344 passed、1 skipped**；Node **47 passed**；浏览器 **42 passed**；TypeScript、ESLint、Next 静态构建通过。跳过项是缺少操作员彩膜素材，包含真实本地 Blender/IFC 集成。

第六轮增加打包资源回归后，源码完整验证为 **347 passed、1 skipped**；Node **47 passed**、浏览器 **42 passed**，类型、ESLint、静态构建与 7 份维护文档检查通过。API 契约覆盖 101 个路径/方法条目。可执行文件是否已构建、是否通过隔离验证及是否发布，必须以 Release 附件报告和真实链接为准。

```powershell
.\.venv\Scripts\python.exe tools/verify.py --integration
.\.venv\Scripts\python.exe tools/check_docs.py
```

构建、隔离和清理流程见 [Windows 发布说明](./WINDOWS_RELEASE.md)。离线接口替身不验证供应商账单或真实云模型质量。本轮没有全新 Windows 系统验证。

## 历史依据

- [第一轮：审阅与基础修复](./REFACTOR_AUDIT_20260912.md)
- [第二轮：可靠性与本地恢复](./RELIABILITY_ROUND_2_20260912.md)
- [第三轮：编辑器](./EDITOR_REFACTOR_ROUND_3_20260912.md)
- [第四轮：生成工作台](./GENERATION_REFACTOR_ROUND_4_20260913.md)
- [第五轮：记录库与任务卡](./RECORD_JOB_REFACTOR_ROUND_5_20260913.md)
- [2026-08-31 Runnable 历史基线](../RUNNABLE_BASELINE.json)

旧 RUNNABLE_BASELINE.json 保持其原始日期、提交和计数，不回填新结果以伪装成连续已验证记录。
