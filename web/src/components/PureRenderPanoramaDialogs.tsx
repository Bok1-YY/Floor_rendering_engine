/* eslint-disable @next/next/no-img-element */
"use client";

import { useEffect, useState } from "react";
import { Columns2, Cuboid, ExternalLink, Globe2, LoaderCircle, Sparkles, TriangleAlert, Wrench } from "lucide-react";
import { toast } from "sonner";

import PanoViewer from "@/components/PanoViewer";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { panoramaGateLabel, panoramaGateTone } from "@/lib/pureRenderPano";
import { cn } from "@/lib/utils";
import type {
  JobView,
  DirectPanoramaPaidPreview,
  FilmRepeatContract,
  GenParams,
  LocalPanoramaGeometryContract,
  ModelKey,
  PanoramaPaidPreview,
  PanoramaQualityPlan,
  PureRenderPanoramaMeta,
  PureRenderPanoramaReviewChecklist,
} from "@/lib/types";

export type PanoramaPaidRequest =
  | { action: "generate"; source_model: ModelKey; source_index: number }
  | { action: "repair"; panorama_index: number };

function PanoramaQualityPlanPanel({ plan }: { plan?: PanoramaQualityPlan | null }) {
  if (!plan) return null;
  const isFallback = plan.status === "local_fallback";
  const viewContracts = plan.sector_contract.length > 0
    ? plan.sector_contract
    : plan.cube_face_contract;
  return (
    <div className="space-y-3 rounded-xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-[13px] font-bold">
            <Sparkles size={14} />360° VR 质量导演规划
          </div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            先观察输入并回答空间连续性问题，再把锁定答案交给图像引擎执行；本页不能编辑，确认后也不会重新规划。
          </div>
        </div>
        <span className={cn(
          "rounded-full px-2.5 py-1 text-[10.5px] font-bold",
          isFallback ? "bg-amber-100 text-amber-900" : "bg-emerald-100 text-emerald-900",
        )}>
          {isFallback ? "本地规则回退" : plan.cache_hit ? "Gemini 规划 · 缓存命中" : "Gemini 规划完成"}
        </span>
      </div>

      {isFallback && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-900">
          {plan.error || "Gemini 规划不可用，已使用版本化本地全景合同；仍可继续生成。"}
        </div>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        {plan.display_answers.map((row) => (
          <div key={row.id} className="rounded-lg border border-border bg-panel px-3 py-2.5">
            <div className="text-[11px] font-bold">{row.question}</div>
            <div className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">{row.answer}</div>
          </div>
        ))}
      </div>

      {viewContracts.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] font-bold">
            {plan.route === "direct_cubemap_atlas" ? "六面方向合同" : "八方向扇区合同"}
          </div>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {viewContracts.map((row) => (
              <div key={row.id} className="rounded-lg bg-muted px-2.5 py-2 text-[10.5px] leading-relaxed">
                <span className="font-bold">{row.id} · {row.label}</span>
                <span className="ml-1 text-muted-foreground">{row.contract}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {plan.object_registry.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] font-bold">唯一物体登记表</div>
          <div className="space-y-1.5">
            {plan.object_registry.map((row) => (
              <div key={row.id} className="rounded-lg border border-border px-2.5 py-2 text-[10.5px] leading-relaxed">
                <span className="font-bold">{row.id} · {row.identity}</span>
                <span className="text-muted-foreground"> · {row.location} · {row.visibility}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {plan.risk_flags.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-[10.5px] leading-relaxed text-amber-950">
          <div className="font-bold">生成前风险</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {plan.risk_flags.map((risk) => <li key={risk}>{risk}</li>)}
          </ul>
        </div>
      )}

      <details className="rounded-lg border border-border bg-panel px-3 py-2.5">
        <summary className="cursor-pointer text-[11px] font-bold">查看交给 Fal 的英文导演指令</summary>
        <div className="mt-2 whitespace-pre-wrap text-[10.5px] leading-relaxed text-muted-foreground">
          {plan.final_direction}
        </div>
      </details>

      <div className="flex flex-wrap justify-between gap-2 text-[10px] text-muted-foreground">
        <span>
          规划调用：{plan.planner_call_count === 0
            ? "本次 0 次（缓存、复用或本地回退）"
            : `本次最多 ${plan.planner_call_count} 次 Gemini`}
        </span>
        <span>Plan {plan.plan_hash.slice(0, 12)} · {plan.planner_model}</span>
      </div>
    </div>
  );
}

function FilmRepeatContractPanel({ contract }: { contract?: FilmRepeatContract | null }) {
  if (!contract) return null;
  const manifest = contract.manifest;
  return (
    <div className="space-y-3 rounded-xl border border-emerald-200 bg-emerald-50/60 p-3 text-emerald-950">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[13px] font-bold">原厂彩膜物理分切合同</div>
          <div className="mt-1 text-[10.5px] leading-relaxed text-emerald-900/75">
            彩膜是权威材质源；不生成新木纹、不循环整张照片，按真实板宽分带并沿长边周期连续切板。
          </div>
        </div>
        <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-[10.5px] font-bold">
          {manifest.status === "ready" ? "周期验证通过" : "周期验证失败"}
        </span>
      </div>
      <div className="grid gap-3 sm:grid-cols-[150px_1fr]">
        <img
          src={`data:image/png;base64,${contract.guide_b64}`}
          alt="彩膜物理铺贴预览"
          className="aspect-square w-full rounded-lg border border-emerald-200 object-cover"
        />
        <dl className="grid grid-cols-[auto_1fr] content-start gap-x-3 gap-y-1.5 text-[10.5px]">
          <dt className="text-emerald-900/65">彩膜物理尺寸</dt><dd className="font-bold">{manifest.film_width_mm} × {manifest.repeat_length_mm} mm</dd>
          <dt className="text-emerald-900/65">目标板材</dt><dd className="font-bold">{manifest.plank_length_mm} × {manifest.plank_width_mm} mm</dd>
          <dt className="text-emerald-900/65">横向分切</dt><dd className="font-bold">{manifest.slitting.lane_count} 条 · 起点 {manifest.slitting.slit_origin_mm} mm</dd>
          <dt className="text-emerald-900/65">纵向换相</dt><dd className="font-bold">每批 {manifest.phase_advance_mm} mm · {manifest.phase_state_count} 个相位</dd>
          <dt className="text-emerald-900/65">有效板状态</dt><dd className="font-bold">{manifest.effective_board_states}</dd>
          <dt className="text-emerald-900/65">首尾配准</dt><dd className="font-bold">X {manifest.repeat_registration.translation_px_x}px</dd>
          <dt className="text-emerald-900/65">禁用标签</dt><dd className="font-bold">{manifest.exclusion_rects.length} 区 · 自动避让</dd>
        </dl>
      </div>
      <div className="text-[10px] text-emerald-900/65">Contract {manifest.manifest_hash.slice(0, 12)} · 原厂彩膜路线材质扩展费用 0 次</div>
    </div>
  );
}

function LocalGeometryContractPanel({ contract }: { contract?: LocalPanoramaGeometryContract | null }) {
  if (!contract) return null;
  const ready = contract.status === "ready";
  return (
    <div className="space-y-2 rounded-xl border border-sky-200 bg-sky-50/70 p-3 text-sky-950">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-[13px] font-bold"><Cuboid size={14} />本地相机与地板几何合同</div>
          <div className="mt-1 text-[10.5px] leading-relaxed text-sky-900/75">
            本地线段、消失点和空间参考共同确定投影；最终彩膜不会沿用模型生成的错缝木纹。
          </div>
        </div>
        <span className={cn("rounded-full px-2.5 py-1 text-[10.5px] font-bold",
          ready ? "bg-emerald-100 text-emerald-900" : "bg-amber-100 text-amber-900")}>
          {ready ? "自动标定通过" : "生成后需本地校准"}
        </span>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[10.5px]">
        <dt className="text-sky-900/65">水平视场</dt><dd className="font-bold">{contract.camera.horizontal_fov_deg.toFixed(1)}°</dd>
        <dt className="text-sky-900/65">俯仰 / 横滚</dt><dd className="font-bold">{contract.camera.pitch_deg.toFixed(1)}° / {contract.camera.roll_deg.toFixed(1)}°</dd>
        <dt className="text-sky-900/65">相机高度</dt><dd className="font-bold">{contract.camera.camera_height_m.toFixed(2)} m · {contract.camera.camera_height_source === "request" ? "用户标定" : "视觉假设"}</dd>
        <dt className="text-sky-900/65">铺装方向</dt><dd className="font-bold">{contract.floor_frame.plank_direction_deg.toFixed(0)}°</dd>
        <dt className="text-sky-900/65">结构线</dt><dd className="font-bold">{contract.manhattan.line_count} 条 · 置信度 {(contract.confidence * 100).toFixed(0)}%</dd>
      </dl>
      {contract.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[10.5px] leading-relaxed text-amber-900">
          {contract.warnings.join("；")}
        </div>
      )}
      <div className="text-[10px] text-sky-900/65">Contract {contract.contract_hash.slice(0, 12)} · 本地分析费用 0 次</div>
    </div>
  );
}

export function PanoramaPaidDialog({
  open,
  onOpenChange,
  jobId,
  request,
  onCommitted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string;
  request: PanoramaPaidRequest;
  onCommitted: (job: JobView) => void;
}) {
  const [preview, setPreview] = useState<PanoramaPaidPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let alive = true;
    api.previewJobPanorama(jobId, request)
      .then((value) => {
        if (alive) setPreview(value);
      })
      .catch((reason) => {
        if (alive) setError((reason as Error).message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [jobId, open, request]);

  async function commit() {
    if (!preview || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const job = await api.commitJobPanorama(jobId, {
        preview_id: preview.preview_id,
        preview_hash: preview.preview_hash,
      });
      onCommitted(job);
      onOpenChange(false);
      toast.success(preview.action === "repair"
        ? preview.repair_kind === "architecture" ? "已提交一次墙体结构修复" : "已提交一次全景边界修复"
        : "已提交 360° VR 生成");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !submitting && onOpenChange(next)}>
      <DialogContent className="max-h-[94vh] max-w-[min(94vw,720px)] overflow-y-auto">
        <div className="space-y-4">
          <div>
            <div className="flex items-center gap-2 text-[16px] font-bold">
              {request?.action === "repair" ? <Wrench size={17} /> : <Sparkles size={17} />}
              {request?.action === "repair" ? "确认付费修复全景结构/边界" : "确认生成单点 360° VR"}
            </div>
            <div className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
              本窗口先建立可缓存的导演规划并核对费用；点击确认后才会向 Fal 提交图像请求。
            </div>
          </div>

          {loading && (
            <div className="flex min-h-44 items-center justify-center gap-2 rounded-xl border border-dashed border-border text-sm text-muted-foreground">
              <LoaderCircle size={17} className="animate-dc-spin" />正在观察源图、建立全景合同并核对费用…
            </div>
          )}

          {error && (
            <div className="rounded-xl bg-destructive-soft px-3 py-2 text-xs font-semibold text-destructive-ink">
              {error}
            </div>
          )}

          {preview && (
            <>
              <div className="grid gap-3 rounded-xl border border-border bg-panel p-3 sm:grid-cols-[180px_1fr]">
                <div className="aspect-[4/3] overflow-hidden rounded-lg border border-border bg-muted">
                  {preview.source.thumb ? (
                    <img
                      src={api.imgUrl(preview.source.thumb)}
                      alt="全景生成源图"
                      className="h-full w-full object-cover"
                    />
                  ) : null}
                </div>
                <dl className="grid grid-cols-[auto_1fr] content-start gap-x-3 gap-y-2 text-[12px]">
                  <dt className="text-muted-foreground">源候选</dt>
                  <dd className="font-semibold">{preview.source.label} · 第 {preview.source.index + 1} 张</dd>
                  <dt className="text-muted-foreground">线路</dt>
                  <dd className="font-semibold">Fal · {preview.endpoint}</dd>
                  <dt className="text-muted-foreground">引擎</dt>
                  <dd className="font-semibold">GPT Image 2</dd>
                  <dt className="text-muted-foreground">输出</dt>
                  <dd className="font-semibold">{preview.output_size.width} × {preview.output_size.height} · 2:1 PNG</dd>
                  <dt className="text-muted-foreground">调用上限</dt>
                  <dd className="font-semibold">本次确认最多 1 次供应商调用</dd>
                  {preview.estimated_cost != null && (
                    <>
                      <dt className="text-muted-foreground">预估费用</dt>
                      <dd className="font-semibold">¥{preview.estimated_cost.toFixed(2)}</dd>
                    </>
                  )}
                </dl>
              </div>

              <div className="flex gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-900">
                <TriangleAlert size={17} className="mt-0.5 flex-none" />
                <span>
                  {preview.warning} 这是单观察点、单目全景，不是可行走三维空间；不能用于量尺或施工判断。
                </span>
              </div>

              <PanoramaQualityPlanPanel plan={preview.quality_plan} />
              <FilmRepeatContractPanel contract={preview.film_contract} />
              <LocalGeometryContractPanel contract={preview.geometry_contract} />

              <div className="flex justify-end gap-2">
                <Button variant="outline" disabled={submitting} onClick={() => onOpenChange(false)}>
                  取消
                </Button>
                <Button disabled={submitting} onClick={() => void commit()}>
                  {submitting && <LoaderCircle className="animate-dc-spin" />}
                  {preview.action === "repair"
                    ? preview.repair_kind === "architecture" ? "确认并调用一次结构修复" : "确认并调用一次边界修复"
                    : "确认并调用一次生成"}
                </Button>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function DirectPanoramaPaidDialog({
  open,
  onOpenChange,
  imagePath,
  roomReferencePath,
  params,
  onCommitted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  imagePath: string;
  roomReferencePath?: string;
  params: GenParams;
  onCommitted: (job: JobView) => void;
}) {
  const [preview, setPreview] = useState<DirectPanoramaPaidPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let alive = true;
    api.previewDirectPanorama({
      image_path: imagePath,
      room_reference_path: roomReferencePath || "",
      params,
    })
      .then((value) => {
        if (alive) setPreview(value);
      })
      .catch((reason) => {
        if (alive) setError((reason as Error).message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [imagePath, open, params, roomReferencePath]);

  async function commit() {
    if (!preview || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const job = await api.commitDirectPanorama({
        preview_id: preview.preview_id,
        preview_hash: preview.preview_hash,
      });
      onCommitted(job);
      onOpenChange(false);
      toast.success("已提交 B2 + GPT Image 2 两条球面候选");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !submitting && onOpenChange(next)}>
      <DialogContent className="max-h-[94vh] max-w-[min(94vw,760px)] overflow-y-auto">
        <div className="space-y-4">
          <div>
            <div className="flex items-center gap-2 text-[16px] font-bold">
              <Globe2 size={18} />确认生成球面效果图
            </div>
            <div className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
              打开本窗口会先运行一次可缓存的全景导演规划；点击最后的确认按钮后，才会并行提交两个 Fal 图像请求。
            </div>
          </div>

          {loading && (
            <div className="flex min-h-44 items-center justify-center gap-2 rounded-xl border border-dashed border-border text-sm text-muted-foreground">
              <LoaderCircle size={17} className="animate-dc-spin" />正在建立六面图集合同与费用预览…
            </div>
          )}

          {error && (
            <div className="rounded-xl bg-destructive-soft px-3 py-2 text-xs font-semibold text-destructive-ink">
              {error}
            </div>
          )}

          {preview && (
            <>
              <div className="grid gap-3 rounded-xl border border-border bg-panel p-3 sm:grid-cols-[180px_1fr]">
                <div className="aspect-square overflow-hidden rounded-lg border border-border bg-muted">
                  {preview.source.thumb ? (
                    <img
                      src={api.imgUrl(preview.source.thumb)}
                      alt="地板小样"
                      className="h-full w-full object-cover"
                    />
                  ) : null}
                </div>
                <div className="space-y-2.5">
                  {preview.engines.map((engine) => (
                    <div key={engine.key} className="rounded-lg border border-border bg-card px-3 py-2.5">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="text-[12.5px] font-bold">{engine.label}</div>
                          <div className="mt-1 break-all text-[10.5px] text-muted-foreground">
                            Fal · {engine.endpoint}
                          </div>
                        </div>
                        <div className="flex-none text-[11.5px] font-bold">
                          {engine.estimated_cost == null ? "费用按供应商账单" : `¥${engine.estimated_cost.toFixed(2)}`}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="grid gap-2 sm:grid-cols-3">
                <div className="rounded-xl border border-border bg-card px-3 py-2.5 text-[11.5px]">
                  <div className="flex items-center gap-1.5 font-bold"><Cuboid size={13} />图集合同</div>
                  <div className="mt-1 text-muted-foreground">3×2 · +X/-X/+Y/-Y/+Z/-Z</div>
                </div>
                <div className="rounded-xl border border-border bg-card px-3 py-2.5 text-[11.5px]">
                  <div className="flex items-center gap-1.5 font-bold"><Globe2 size={13} />最终全景</div>
                  <div className="mt-1 text-muted-foreground">{preview.output_size.width} × {preview.output_size.height} · ERP</div>
                </div>
                <div className="rounded-xl border border-border bg-card px-3 py-2.5 text-[11.5px]">
                  <div className="flex items-center gap-1.5 font-bold"><Sparkles size={13} />调用上限</div>
                  <div className="mt-1 text-muted-foreground">本次确认最多 {preview.max_provider_calls} 次</div>
                </div>
              </div>

              <div className="flex gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-900">
                <TriangleAlert size={17} className="mt-0.5 flex-none" />
                <span>{preview.warning}</span>
              </div>

              <PanoramaQualityPlanPanel plan={preview.quality_plan} />
              <FilmRepeatContractPanel contract={preview.film_contract} />
              <LocalGeometryContractPanel contract={preview.geometry_contract} />

              <div className="flex items-center justify-between gap-3">
                <div className="text-[12px] text-muted-foreground">
                  {preview.estimated_cost == null
                    ? "总费用以两条供应商实际账单为准"
                    : `预估合计 ¥${preview.estimated_cost.toFixed(2)}`}
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" disabled={submitting} onClick={() => onOpenChange(false)}>
                    取消
                  </Button>
                  <Button disabled={submitting} onClick={() => void commit()}>
                    {submitting && <LoaderCircle className="animate-dc-spin" />}
                    确认并提交两条候选
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function PureRenderPanoramaViewerDialog({
  open,
  onOpenChange,
  jobId,
  panoramaIndex,
  erpUrl,
  metadata,
  onSnapshot,
  onRequestRepair,
  onRequestFloorCorrection,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string;
  panoramaIndex: number;
  erpUrl: string;
  metadata: PureRenderPanoramaMeta;
  onSnapshot: (job: JobView) => void;
  onRequestRepair: (panoramaIndex: number) => void;
  onRequestFloorCorrection?: (panoramaIndex: number) => void;
}) {
  const [reviewing, setReviewing] = useState(false);
  const gate = metadata.gate;
  const isDirectAtlas = metadata.generation_route === "direct_cubemap_atlas";
  const hasArchitectureFailure = gate?.failures?.includes("architecture_views");
  const accepted = metadata.review?.status === "accepted";
  const repairAvailable =
    gate?.status === "repair_recommended" &&
    !metadata.repair_claimed &&
    metadata.repair_result_index == null;
  const reviewBlockedReason = gate?.status === "passed"
    ? ""
    : gate?.status === "repair_recommended"
      ? isDirectAtlas
        ? "自动检查发现六面边界或 ERP 环缝异常：可以查看和下载，但融合修复并重新通过前不能标记为已验收。"
        : hasArchitectureFailure
          ? "自动检查在一个或多个自然视角发现墙体竖线/结构异常：可以查看，但结构修复并重新通过前不能标记为已验收。"
          : "自动检查发现左右环缝异常：可以查看和下载，但修复并重新通过前不能标记为已验收。"
      : "自动检查未通过，不能标记为已验收。";

  async function submitReview(checklist: PureRenderPanoramaReviewChecklist) {
    if (reviewing) return;
    setReviewing(true);
    try {
      const job = await api.reviewJobPanorama(jobId, {
        panorama_index: panoramaIndex,
        checklist,
      });
      onSnapshot(job);
      const next = job.model_runs.vr360?.candidates?.find((candidate) => candidate.idx === panoramaIndex)
        ?.metadata?.panorama?.review?.status;
      toast.success(next === "accepted" ? "全景已验收" : "复核结果已保存；当前未标记为已验收");
    } catch (reason) {
      toast.error((reason as Error).message);
    } finally {
      setReviewing(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[97vh] max-w-[98vw] overflow-y-auto sm:max-w-[min(96vw,1240px)]">
        <div className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-2 pr-8">
            <div>
              <div className="text-[15px] font-bold">
                单点 360° VR · 第 {panoramaIndex + 1} 张
                {metadata.engine_label ? ` · ${String(metadata.engine_label)}` : ""}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {isDirectAtlas
                  ? "同球心六面图集直出 · 确定性合成 ERP · 非几何锁定"
                  : "AI 扩展单目 ERP · 非几何锁定 · 初始视角对准源效果图正前方"}
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <span className={cn("rounded-full px-2.5 py-1 text-[11px] font-bold", panoramaGateTone(gate?.status))}>
                {panoramaGateLabel(gate?.status)}
              </span>
              <span className={cn(
                "rounded-full px-2.5 py-1 text-[11px] font-bold",
                accepted ? "bg-emerald-50 text-emerald-800" : "bg-muted text-muted-foreground",
              )}>
                {accepted ? "已人工验收" : "待人工验收"}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-panel px-3 py-2 text-xs">
            <span className="text-muted-foreground">
              隐藏区域由 AI 补全，不代表真实户型、尺寸或施工几何。
            </span>
            <div className="flex gap-1.5">
              {onRequestFloorCorrection && (
                <Button size="sm" variant="outline" onClick={() => {
                  onOpenChange(false);
                  onRequestFloorCorrection(panoramaIndex);
                }}>
                  <Columns2 />球面地板校正
                </Button>
              )}
              {repairAvailable && (
                <Button size="sm" variant="outline" onClick={() => {
                  onOpenChange(false);
                  onRequestRepair(panoramaIndex);
                }}>
                  <Wrench />{isDirectAtlas ? "融合修复边界" : hasArchitectureFailure ? "修复墙体结构" : "修复接缝"}
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={() => {
                window.open(api.imgUrl(erpUrl), "_blank", "noopener,noreferrer");
              }}>
                <ExternalLink />原始 2:1
              </Button>
            </div>
          </div>

          {reviewing && (
            <div className="flex items-center gap-2 rounded-lg bg-primary-soft px-3 py-2 text-xs font-semibold text-primary">
              <LoaderCircle size={14} className="animate-dc-spin" />正在保存复核结果…
            </div>
          )}

          <PanoViewer
            erpUrl={api.imgUrl(erpUrl)}
            mode="review"
            reviewProfile="pure_render"
            initialYawDeg={metadata.viewer_initial_yaw_deg ?? 90}
            reviewBlockedReason={reviewBlockedReason}
            onPureChecklistResult={(checklist) => void submitReview(checklist)}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
