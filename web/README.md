# Floor Engine 前端

这是 Floor Rendering Engine 的 Next.js 静态前端，不是通用 Next.js/Vercel 服务端模板。主说明见[项目 README](../README.md)和[开发指南](../DEVGUIDE.md)。

## 环境与运行

Node.js 20.9+；实际包版本由 [package.json](./package.json) 与 [package-lock.json](./package-lock.json)确定。先从仓库根目录启动 Python 后端，再在本目录运行：

```shell
npm ci
npm run dev
```

开发入口为 http://localhost:3000，开发 API 默认为 http://127.0.0.1:7870。静态生产构建的 API 使用浏览器同源地址。

```shell
npm run build
```

产物位于 out，由项目 Python 服务托管；不使用 npm start/next start。源码启动器会检查产物是否过期。

## 模块边界

- `src/components/generation/`：参数、草稿、识色、配方、生成、预览和任务列表。
- `src/components/color-match/`、`src/components/inpaint/`：图像编辑状态、请求、画布资源与展示。
- `src/components/records/`：文件查询、目标操作、评审/二改草稿、筛选与展示。
- `src/components/job-card/`：快照、候选缓存、评审身份、动作与展示。
- `src/lib/editor/`：规范快照及异步资源生命周期；`src/lib/results/`：结果身份与冲突操作锁。
- `src/lib/api.ts`：typed HTTP 客户端；`src/lib/types.ts`：接口类型；`src/lib/draft.ts`：生成草稿与一次性参数复用。

新增业务动作进入相应模块；展示组件不直接发起业务网络请求。保持目标身份、旧请求失效、保存期间新输入保护，以及可能重复计费的确认边界。前端修改先阅读 [AGENTS.md](./AGENTS.md) 指定的本地 Next 文档。

## 提交身份与恢复

生成模块的 submission-store 使用 IndexedDB 事务保存每条提交的 UUID、数据实例、固定请求和批次信息。写入完成才允许发送；不能复用允许静默丢弃写入失败的普通表单草稿。submission-client 先检查健康接口的协议 v1，再查询原凭据，最后只在用户操作中 POST。useSubmissionRecovery 管理查询退避、焦点/联网恢复和页面卸载；SubmissionRecovery 展示待确认项与原任务定位。

普通生成、自由创作、批量房间和批量地板统一使用该流程。批量最多四个并发条目，迟到识色不能在页面离开后触发提交。未知结果保留原快照；已确认后移除快照、保留轻量回执 30 天。自动恢复只查询，隐藏不删除。新前端遇到旧后端必须提示更新，不能退回无身份提交。

浏览器故障回归包含 IndexedDB 损坏/写入失败、响应丢失、刷新、跨标签页、数据实例变化和清卡。隔离测试服务器提供真实提交路由与离线 worker，测试控制端点仅存在于 tests 目录，正式服务不注册。

## 验证

```shell
npx playwright install chromium
npm run check
```

check 依次运行类型、ESLint、Node、静态构建及浏览器测试。Playwright 配置见 [playwright.config.ts](./playwright.config.ts)，用隔离 Python 静态宿主和接口替身，不访问真实付费模型。后端测试依赖需先在项目根目录安装 requirements-dev.txt。完整项目验证入口为根目录 tools/verify.py，结果见[验证索引](../docs/VALIDATION_CURRENT.md)。

记录评审与二改草稿仅在当前页面会话中保留，不承诺浏览器刷新或跨客户端同步；正式任务创建也未提供跨刷新/重启的严格幂等协议。
