# Stable Diffusion 3.5 接入说明

> 状态：已实现的实验线路；默认关闭，仅支持“纯效果图”工作流  
> 端点：`fal-ai/stable-diffusion-v35-large` + `fal-ai/aura-sr`  
> 更新日期：2026-07-14

## 1. 设计边界

SD 3.5 是独立的第三个生图模型，不替换 B2/Pro，也不复用 Gemini 提示词文本。

- B2、Pro、SD 3.5 可多选并行生成。
- 首期只允许“纯效果图”，不支持参照模式、地板替换、宠物、Omakase 或墙板工作流。
- 地板小样必须通过 IP-Adapter 进入模型；适配器失败时任务失败，绝不静默降级为纯文本生图。
- Gemini 的长期对抗式提示词资产仍由 `prompts.py` 维护，黄金快照不变。
- SD 使用 `sd_prompts.py` 独立编译正向/负向提示词，只消费 `TaskParams` 的语义字段。

## 2. 生成链路

```text
TaskParams
  ├─ prompts.py ───────────────▶ B2 / Pro（Gemini 既有提示词）
  └─ sd_prompts.py
       ├─ positive prompt
       └─ negative prompt
             │
             ▼
       SD 3.5 Large（约 1MP 扩散）
             │  InstantX SD3.5 IP-Adapter
             │  地板小样 = mandatory image reference
             ▼
       基础 PNG
             │
             ├─ AuraSR 成功 ─▶ 4× 超分 ─▶ 按 2K/4K 目标长边缩放交付
             └─ AuraSR 失败 ─▶ 保留基础 PNG，任务标 partial，可单独重试超分
```

FAL 调用使用持久队列：短连接提交到 `queue.fal.run`，随后轮询 `status_url`，完成后读取 `response_url`。排队/推理期间状态接口可能返回 HTTP 202，这是正常响应。提交响应未知时客户端不会自动重交，避免同一个请求重复计费。

SD/AuraSR 队列默认忽略系统环境变量和通用 Google `proxy`，直接连接 FAL；本次真实校准证明通用代理会在大 POST/长轮询中产生 TLS EOF。只有显式配置 `fal_queue_proxy` 时才使用专用代理。

## 3. IP-Adapter 配置

| 字段 | 当前值 |
|---|---|
| `path` | `InstantX/SD3.5-Large-IP-Adapter` |
| `weight_name` | `ip-adapter.bin` |
| `image_encoder_path` | `google/siglip-so400m-patch14-384` |
| 默认 `scale` | `0.5` |

InstantX 当前主分支已经删除历史 `ip-adapter.safetensors`，官方推理示例使用 `ip-adapter.bin`，不要改回旧文件名。

## 4. 用户参数

前端“SD 3.5 高级参数”默认折叠：

| 参数 | 默认 | 后端范围 | 说明 |
|---|---:|---:|---|
| Seed | 随机 | `>= 0` | 固定后可复现实验；重抽会强制新 Seed |
| Steps | 28 | 10–50 | 扩散步数 |
| CFG | 3.5 | 1–10 | 提示词遵循强度 |
| 地板参考强度 | 0.5 | 0.1–1 | IP-Adapter scale |
| 正向追加 | 空 | 最多 1000 字 | 只追加，不覆盖内置结构 |
| 负向追加 | 空 | 最多 1000 字 | 只追加到独立负向提示词 |

SD 内置提示词包含场景、机位、灯光、地板材质/色彩/纹理/铺法/缝型和画质结构。无缝、常规倒角、圆弧倒角分别编译，避免互相冲突。

## 5. 作业与接口契约

提交任务新增：

```json
{
  "model_filter": "both",
  "model_targets": ["b2", "pro", "sd35"],
  "sd_options": {
    "seed": null,
    "steps": 28,
    "guidance_scale": 3.5,
    "reference_strength": 0.5,
    "positive_addition": "",
    "negative_addition": ""
  }
}
```

`model_filter` 只为旧客户端兼容，新代码以 `model_targets` 和键控的 `model_runs` 为准：

```json
{
  "model_targets": ["b2", "sd35"],
  "model_runs": {
    "b2": { "status": "done", "url": "/outputs/..." },
    "sd35": {
      "status": "partial",
      "base_url": "/outputs/...",
      "delivery_status": "upscale_failed",
      "seed": 1234
    }
  }
}
```

- `POST /api/jobs/{id}/sd-upscale`：仅重试已有 SD 基础图的 AuraSR，不重新生成 SD 图。
- `GET /api/jobs/{id}/result?model=sd35&idx=N`：浏览 SD 候选。
- `POST /api/jobs/{id}/color-match` 的 `stage` 支持 `sd35`。
- 记录保存 `generation_metadata`：模型、Seed、Steps、CFG、参考强度、基础图文件和正负提示词 SHA-256；不把明文提示词暴露给前端。

## 6. 配置与成本

在“设置”页：

1. 配置 Fal API Key。
2. 打开“启用 SD 3.5 实验线路”。
3. 可选配置 `SD35` 与 `AuraSR` 每次成功调用的估算单价。

对应 `engine_config.json` 字段为 `sd_enabled`、`fal_api_key`、`fal_queue_proxy` 和 `usage_prices.SD35/AuraSR`。实验开关默认关闭，避免未校准环境误触发付费任务。

## 7. 失败与重试语义

- SD 失败：该模型 run 为 `failed`；其他模型成功时整单为 `partial`。
- SD 成功、AuraSR 失败：基础图进入候选，run 为 `partial`、`delivery_status=upscale_failed`。
- “重试”只补尚无结果的模型；已有 SD 基础结果不会重新计费生成。
- “重试超分”只调用 AuraSR。
- “重抽”会为所有已选模型增加候选；SD 清空固定 Seed 以产生新结果。
- 程序重启会把中断的 queued/running run 修正为 partial/failed，已有候选不会丢失。

## 8. 验证

离线回归覆盖：提示词正负分离、缝型条件编译、约 1MP/64 对齐画布、IP-Adapter 请求、FAL 队列 202 状态、未知提交响应不重交、通用 model run 迁移与前端生产构建。

真实环境校准必须记录两类结论：接口链路是否成功，以及地板颜色/纹理/铺法的人眼保真结果。前者不能代替后者；视觉质量仍需在记录页人工评审。

2026-07-14 实测：使用 `Deep_Saddle.jpg`、Seed `42035`、Steps 24、CFG 4.0、IP-Adapter 0.5，队列状态完整经过 `IN_QUEUE → IN_PROGRESS → COMPLETED`；SD 返回 `1152×896` PNG，AuraSR 返回 `4608×3584`。接口与超分链路通过。目检能保持深棕木地板、直拼和大尺度木纹方向，空间几何正常；成图地板比小样略亮、细纹有所软化，说明 0.5 是安全起点但还不能宣称色纹完全一致，后续应以 0.45/0.6/0.7 小批量评审确定默认值。已知成功 SD 成本按 FAL 页面约 `$0.065/MP`，AuraSR 另按计算秒计费；网络失败请求是否产生费用以 FAL 控制台账单为准。
