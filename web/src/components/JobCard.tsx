/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bookmark,
  Check,
  Columns2,
  Globe2,
  LoaderCircle,
  Maximize2,
  Paintbrush,
  Palette,
  Pencil,
  RefreshCw,
  Sparkles,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { useJobStream } from "@/hooks/useJobStream";
import { notifyJobEnd } from "@/lib/notify";
import type {
  CandidateGenerationMetadata,
  JobSlotKey,
  JobView,
  ModelKey,
  ModelRunView,
  PureRenderPanoramaMeta,
  RecordResult,
  ReviewStatus,
} from "@/lib/types";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ImageZoom } from "@/components/ImageZoom";
import { CompareSlider } from "@/components/CompareSlider";
import { ColorMatchDialog } from "@/components/ColorMatchDialog";
import { InpaintDialog } from "@/components/InpaintDialog";
import { FloorVisualizeDialog } from "@/components/FloorVisualizeDialog";
import PanoramaFloorDialog from "@/components/PanoramaFloorDialog";
import {
  PanoramaPaidDialog,
  PureRenderPanoramaViewerDialog,
  type PanoramaPaidRequest,
} from "@/components/PureRenderPanoramaDialogs";
import { panoramaGateLabel, panoramaGateTone } from "@/lib/pureRenderPano";
import { cn } from "@/lib/utils";

const BADGE: Record<string, { label: string; color: string; bg: string }> = {
  queued: { label: "排队", color: "var(--muted-foreground)", bg: "var(--muted)" },
  running: { label: "生成中", color: "var(--primary-foreground)", bg: "var(--primary)" },
  done: { label: "完成", color: "#fff", bg: "var(--success)" },
  partial: { label: "部分完成", color: "var(--warn)", bg: "var(--warn-soft)" },
  failed: { label: "失败", color: "#fff", bg: "var(--destructive)" },
};

const REGEN_NS = [1, 2, 4, 6];
const actBtn =
  "h-8 rounded-lg border border-border bg-card px-3 text-[12px] font-semibold text-secondary-foreground transition-colors hover:bg-accent";
const imageToolBtn =
  "inline-flex h-[30px] items-center justify-center gap-1.5 rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold text-secondary-foreground transition-colors hover:border-primary/35 hover:bg-primary-soft hover:text-accent-foreground";

type SlotView = {
  idx: number;
  url: string;
  thumb: string;
  metadata?: CandidateGenerationMetadata;
};

const isGenerationModel = (key: JobSlotKey | undefined): key is ModelKey =>
  key === "b2" || key === "pro" || key === "sd35";

const resultFileName = (value?: string) => {
  if (!value) return "";
  try {
    return decodeURIComponent(value).split(/[\\/]/).pop() || "";
  } catch {
    return value.split(/[\\/]/).pop() || "";
  }
};

export function JobCard({
  initial,
  onRemove,
}: {
  initial: JobView;
  onRemove?: (id: string) => void;
}) {
  const [job, setJob] = useState<JobView>(initial);
  const [zoom, setZoom] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState("");
  const [editColorMatch, setEditColorMatch] = useState(true);
  const [compareOpen, setCompareOpen] = useState(false);
  // 手动校色：记录点的是哪个图槽（B2/Pro 各自的当前浏览候选）
  const [colorMatch, setColorMatch] = useState<{
    stage: ModelKey;
    srcUrl: string;
    imageRel: string;
  } | null>(null);
  // 生成式修补：同样记录所点图槽的当前浏览候选
  const [inpaint, setInpaint] = useState<{
    stage: ModelKey;
    srcUrl: string;
    imageRel: string;
  } | null>(null);
  const [floorVisualize, setFloorVisualize] = useState<{
    stage: ModelKey;
    srcUrl: string;
    imageRel: string;
  } | null>(null);
  const [regenN, setRegenN] = useState(1);
  const [activeModel, setActiveModel] = useState<JobSlotKey>(initial.model_targets?.[0] || "b2");
  const [candidateCache, setCandidateCache] = useState<Partial<Record<JobSlotKey, SlotView[]>>>({});
  const [reviewByUrl, setReviewByUrl] = useState<Record<string, { status: ReviewStatus; favorite: boolean; best: boolean }>>({});
  const [reviewBusy, setReviewBusy] = useState(false);
  // 候选切换的本地覆盖（不影响后端"当前下标"，仅前端浏览）
  const [view, setView] = useState<Partial<Record<JobSlotKey, SlotView>>>({});
  const [panoramaPaidRequest, setPanoramaPaidRequest] = useState<PanoramaPaidRequest | null>(null);
  const [panoramaView, setPanoramaView] = useState<{
    index: number;
    url: string;
    metadata: PureRenderPanoramaMeta;
  } | null>(null);
  const [panoramaFloor, setPanoramaFloor] = useState<{
    index: number;
    url: string;
  } | null>(null);
  const [panoramaResuming, setPanoramaResuming] = useState(false);

  const prevStatus = useRef(initial.status);
  const prevOperationStatus = useRef(initial.operation_status);
  const totalsRef = useRef(
    JSON.stringify(
      Object.fromEntries(
        Object.entries(initial.model_runs || {}).map(([key, run]) => [key, run?.total || 0]),
      ),
    ),
  );
  // 快照统一入口（SSE / 父级 2.5s 轮询 / 操作回执三路共用）：刷新 job；
  // 仅当候选总数变化(新图落地)才清候选浏览覆盖，避免每秒把用户正在翻的 ‹n/N› 重置回去；
  // 非终态→终态时触发完成提醒(系统通知+提示音)——放在共用路径上，SSE 断流时轮询也能补上通知。
  const applySnapshot = useCallback((j: JobView) => {
    const was = prevStatus.current;
    const wasActive = was === "queued" || was === "running";
    const nowTerminal =
      j.status === "done" || j.status === "partial" || j.status === "failed";
    if (wasActive && nowTerminal) {
      notifyJobEnd(j.status, j.display_name, j.error);
    }
    if (
      !wasActive &&
      prevOperationStatus.current === "running" &&
      (j.operation_status === "done" || j.operation_status === "failed")
    ) {
      notifyJobEnd(
        j.operation_status === "done" ? "done" : "failed",
        `${j.display_name} · ${j.operation}`,
        j.operation_error,
      );
    }
    prevStatus.current = j.status;
    prevOperationStatus.current = j.operation_status;
    const previousTotals = JSON.parse(totalsRef.current) as Record<string, number>;
    const totals = JSON.stringify(
      Object.fromEntries(
        Object.entries(j.model_runs || {}).map(([key, run]) => [key, run?.total || 0]),
      ),
    );
    if (totals !== totalsRef.current) {
      totalsRef.current = totals;
      setView({});
      if ((j.model_runs.vr360?.total || 0) > (previousTotals.vr360 || 0)) {
        setActiveModel("vr360");
      }
    }
    setJob(j);
  }, []);
  const active =
    job.status === "queued" ||
    job.status === "running" ||
    job.pro_polishing ||
    job.operation_status === "running";
  useJobStream(active ? job.job_id : null, applySnapshot);
  // 轮询兜底：SSE 断流(后端重启/流异常关闭)时，父级轮询的新快照仍能解冻卡片。
  // SSE 与轮询同源同结构，last-writer-wins，1s 周期的 SSE 会覆盖偶尔旧一拍的轮询数据。
  useEffect(() => {
    let alive = true;
    queueMicrotask(() => {
      if (alive) applySnapshot(initial);
    });
    return () => {
      alive = false;
    };
  }, [initial, applySnapshot]);

  async function act(fn: () => Promise<JobView>, okMsg?: string) {
    try {
      const j = await fn();
      applySnapshot(j);
      setView({}); // 用户主动操作后总是重置候选浏览
      if (okMsg) toast.success(okMsg);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function resumePanoramaResult() {
    const recovery = job.panorama_resume;
    if (!recovery || panoramaResuming) return;
    setPanoramaResuming(true);
    try {
      const restored = recovery.route === "direct_cubemap_atlas"
        ? await api.commitDirectPanorama({
            preview_id: recovery.preview_id,
            preview_hash: recovery.preview_hash,
          })
        : await api.commitJobPanorama(job.job_id, {
            preview_id: recovery.preview_id,
            preview_hash: recovery.preview_hash,
          });
      applySnapshot(restored);
      toast.success("正在恢复已有 Fal 结果，不会重新提交生图");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setPanoramaResuming(false);
    }
  }

  async function remove() {
    try {
      await api.deleteJob(job.job_id);
      onRemove?.(job.job_id); // 即时从队列移除；后端已删，轮询不会复活
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const b = BADGE[job.status] ?? BADGE.queued;

  const slots: {
    key: JobSlotKey;
    name: string;
    url: string;
    thumb: string;
    idx: number;
    total: number;
    run: ModelRunView;
    metadata?: CandidateGenerationMetadata;
  }[] = [];
  for (const key of job.model_targets || (["b2", "pro"] as JobSlotKey[])) {
    const run = job.model_runs?.[key];
    if (!run?.url) continue;
    const ov = view[key];
    slots.push({
      key,
      name: run.label,
      url: ov?.url ?? run.url,
      thumb: ov?.thumb ?? run.thumb,
      idx: ov?.idx ?? run.idx,
      total: run.total,
      run,
      metadata: ov?.metadata ?? run.candidates?.find((candidate) => candidate.idx === (ov?.idx ?? run.idx))?.metadata,
    });
  }

  const activeSlot = slots.find((slot) => slot.key === activeModel) ?? slots[0];
  const activeKey = activeSlot?.key;
  const activeUrl = activeSlot?.url || "";
  const activeTotal = activeSlot?.total || 0;

  useEffect(() => {
    if (!activeKey || activeTotal <= 0) return;
    let alive = true;
    Promise.all(
      Array.from({ length: activeTotal }, (_, idx) =>
        api.jobResult(job.job_id, activeKey, idx).then((result) => ({
          idx: result.idx,
          url: result.url,
          thumb: result.thumb,
          metadata: result.metadata,
        })),
      ),
    ).then((items) => {
      if (alive) setCandidateCache((cache) => ({ ...cache, [activeKey]: items }));
    }).catch(() => {});
    return () => { alive = false; };
  }, [activeKey, activeTotal, job.job_id]);

  const findRecordResult = useCallback(async (slot: { url: string } | undefined): Promise<RecordResult | null> => {
    if (!slot || !job.json_path || !job.record_id) return null;
    const records = await api.loadRecord(job.json_path);
    const record = records.find((entry) => entry.id === job.record_id);
    const targetName = resultFileName(slot.url);
    return record?.results?.find((result) => resultFileName(result.result_url) === targetName) ?? null;
  }, [job.json_path, job.record_id]);

  useEffect(() => {
    if (!activeUrl || !job.json_path || !job.record_id || reviewByUrl[activeUrl]) return;
    let alive = true;
    findRecordResult({ url: activeUrl }).then((result) => {
      if (!alive || !result) return;
      setReviewByUrl((state) => ({
        ...state,
        [activeUrl]: {
          status: result.review_status || "unreviewed",
          favorite: !!result.favorite,
          best: !!result.best,
        },
      }));
    }).catch(() => {});
    return () => { alive = false; };
  }, [activeUrl, findRecordResult, job.json_path, job.record_id, reviewByUrl]);

  async function setReviewStatus(status: ReviewStatus) {
    if (!activeSlot || reviewBusy) return;
    setReviewBusy(true);
    try {
      const result = await findRecordResult(activeSlot);
      if (!result) throw new Error("当前候选尚未写入记录，请稍后再试");
      const current = reviewByUrl[activeSlot.url] || { status: result.review_status || "unreviewed", favorite: !!result.favorite, best: !!result.best };
      const nextStatus = current.status === status ? "unreviewed" : status;
      await api.reviewResult({
        json_path: job.json_path,
        record_id: job.record_id,
        result_id: result.result_id,
        review_status: nextStatus,
        review_tags: result.review_tags || [],
        review_note: result.review_note || "",
        best: current.best,
      });
      setReviewByUrl((state) => ({ ...state, [activeSlot.url]: { ...current, status: nextStatus } }));
      toast.success(nextStatus === "unreviewed" ? "已取消评审" : "评审已保存");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setReviewBusy(false);
    }
  }

  async function toggleFavorite() {
    if (!activeSlot || reviewBusy) return;
    setReviewBusy(true);
    try {
      const result = await findRecordResult(activeSlot);
      if (!result) throw new Error("当前候选尚未写入记录，请稍后再试");
      const response = await api.favoriteResult(job.json_path, job.record_id, result.result_id);
      const current = reviewByUrl[activeSlot.url] || { status: result.review_status || "unreviewed", favorite: !!result.favorite, best: !!result.best };
      setReviewByUrl((state) => ({ ...state, [activeSlot.url]: { ...current, favorite: response.favorite } }));
      toast.success(response.favorite ? "已收藏" : "已取消收藏");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setReviewBusy(false);
    }
  }

  const stageLine =
    (job.model_targets || []).map((key) => {
      const run = job.model_runs?.[key];
      return run?.stage ? `${run.label} ${run.stage}` : "";
    })
      .filter(Boolean)
      .join(" · ") || "处理中…";

  const terminal =
    !active &&
    (job.status === "done" || job.status === "partial" || job.status === "failed");
  const isFree = job.workflow_mode.includes("自由创作");
  const isPureRender = job.workflow_mode.includes("纯效果图");
  const activePanorama = activeSlot?.metadata?.panorama;
  const activeEngineLabel = activePanorama?.engine_label
    ? String(activePanorama.engine_label)
    : activeSlot?.metadata?.engine_label
      ? String(activeSlot.metadata.engine_label)
      : "";
  const activeGenerationKey = isGenerationModel(activeSlot?.key) ? activeSlot.key : null;

  // 前后对比：仅替换类工作流有房间原图（room_url）；效果图优先取当前浏览中的 Pro 候选，无 Pro 用 B2
  const compareAfter =
    (slots.find((s) => s.key === "pro") ?? slots.find((s) => s.key === "b2") ?? slots[0])?.url || "";
  const activeReview = activeUrl
    ? reviewByUrl[activeUrl] || { status: "unreviewed" as ReviewStatus, favorite: false, best: false }
    : { status: "unreviewed" as ReviewStatus, favorite: false, best: false };
  const activeCandidates = activeKey ? candidateCache[activeKey] || [] : [];

  function openActivePanorama() {
    if (!activeSlot || activeSlot.key !== "vr360" || !activePanorama) return;
    setPanoramaView({
      index: activeSlot.idx,
      url: activeSlot.url,
      metadata: activePanorama,
    });
  }

  return (
    <div className="animate-scfade rounded-[16px] border border-border bg-card p-[15px] shadow-[0_6px_22px_rgba(120,90,60,.07)] dark:shadow-[0_6px_22px_rgba(0,0,0,.3)]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[14.5px] font-bold text-foreground">
            {job.display_name}
          </div>
          <div className="mt-0.5 text-[11.5px] text-muted-foreground">
            {job.ts}
            {job.time_text ? ` · ${job.time_text}` : ""}
          </div>
        </div>
        <span
          className="flex-none rounded-full px-[10px] py-[3px] text-[11px] font-bold"
          style={{ color: b.color, background: b.bg }}
        >
          {b.label}
        </span>
      </div>

      {active && (
        <div className="mt-2.5">
          <div className="mb-1.5 flex items-center gap-1.5 text-[11.5px] font-semibold text-primary">
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              className="animate-dc-spin"
            >
              <path d="M21 12a9 9 0 1 1-6.2-8.6" />
            </svg>
            {stageLine}
          </div>
          <div className="h-[5px] w-full overflow-hidden rounded-md bg-muted">
            <div className="h-full w-2/5 animate-pulse rounded-md bg-primary" />
          </div>
        </div>
      )}

      {(job.operation_error || job.error) && (
        <div className="mt-2.5 line-clamp-2 rounded-[9px] bg-destructive-soft px-[11px] py-[9px] text-[11.5px] leading-relaxed text-destructive-ink" title={job.operation_error || job.error}>
          {job.error_kb ? (
            <span className="font-semibold">{job.error_kb.title} · </span>
          ) : null}
          {job.operation_error || job.error}
        </div>
      )}

      {!active && job.panorama_resume && (
        <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-[9px] border border-amber-200 bg-amber-50 px-[11px] py-[9px] text-[11.5px] text-amber-950">
          <span>
            {(job.model_runs.vr360?.total || 0) > 0
              ? "另有一个已付费 Fal 结果可以恢复；恢复不会创建新的生图请求。"
              : "Fal 已有请求句柄，可重新下载并继续本地全景处理；不会创建新的付费生成。"}
          </span>
          <button
            type="button"
            disabled={panoramaResuming}
            onClick={() => void resumePanoramaResult()}
            className="inline-flex h-8 flex-none items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 font-bold text-amber-950 hover:bg-amber-100 disabled:opacity-60"
          >
            {panoramaResuming ? <LoaderCircle size={13} className="animate-dc-spin" /> : <RefreshCw size={13} />}
            {(job.model_runs.vr360?.total || 0) > 0 ? "恢复另一个 Fal 结果" : "恢复已有 Fal 结果"}
          </button>
        </div>
      )}

      {active && slots.length === 0 && (
        <div className="mt-[11px] grid grid-cols-2 gap-2.5">
          {(job.model_targets || ["b2", "pro"]).map((key) => {
            const run = job.model_runs?.[key];
            return (
              <div key={key} className="flex aspect-[4/3] flex-col items-center justify-center gap-2 rounded-[11px] border border-dashed border-border-strong bg-panel text-[11.5px] font-semibold text-muted-foreground">
                {run?.status === "running" || (!run?.stage?.includes("排队") && key === job.model_targets?.[0]) ? (
                  <LoaderCircle size={16} className="animate-dc-spin text-primary" />
                ) : null}
                <span>{run?.label || key.toUpperCase()} · {run?.stage || "排队中"}</span>
              </div>
            );
          })}
        </div>
      )}

      {slots.length > 0 && activeSlot && (
        <div className="mt-[11px]">
          <div className="flex rounded-[10px] bg-muted p-[3px]">
            {slots.map((slot) => (
              <button
                key={slot.key}
                type="button"
                onClick={() => setActiveModel(slot.key)}
                className={cn(
                  "h-8 flex-1 rounded-lg text-[12px] font-bold transition-colors",
                  activeSlot.key === slot.key
                    ? "bg-card text-accent-foreground shadow-sm"
                    : "text-muted-foreground hover:text-secondary-foreground",
                )}
              >
                {slot.name} · {slot.total} 张
              </button>
            ))}
          </div>

          <div className={cn(
            "relative mt-2.5 cursor-zoom-in overflow-hidden rounded-[11px] border border-border bg-muted",
            activeSlot.key === "vr360" ? "aspect-[2/1]" : "aspect-[4/3]",
          )}>
            <img
              src={api.imgUrl(activeSlot.thumb)}
              alt={activeSlot.name}
              onClick={() => activeSlot.key === "vr360"
                ? openActivePanorama()
                : setZoom(api.imgUrl(activeSlot.url))}
              className="absolute inset-0 h-full w-full object-cover"
            />
            {activeSlot.run.auto_color_status === "done" && (
              <span className="absolute left-2 top-2 rounded-md bg-[rgba(26,24,21,.55)] px-2 py-1 text-[10.5px] font-bold text-white backdrop-blur-[2px]">
                已自动校色
              </span>
            )}
            {activeReview.status !== "unreviewed" && (
              <span className={cn(
                "absolute right-2 top-2 rounded-full px-2.5 py-1 text-[10.5px] font-bold text-white",
                activeReview.status === "pass" && "bg-success",
                activeReview.status === "backup" && "bg-warn",
                activeReview.status === "rejected" && "bg-destructive",
              )}>
                {activeReview.status === "pass" ? "通过" : activeReview.status === "backup" ? "备选" : "淘汰"}
              </span>
            )}
            {activeSlot.key === "vr360" && activePanorama && (
              <span className={cn(
                "absolute bottom-2 right-2 rounded-full px-2.5 py-1 text-[10.5px] font-bold",
                panoramaGateTone(activePanorama.gate?.status),
              )}>
                {panoramaGateLabel(activePanorama.gate?.status)}
              </span>
            )}
          </div>

          <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span>
              {activeSlot.idx + 1} / {activeSlot.total}
              {activeEngineLabel ? ` · ${activeEngineLabel}` : ""}
            </span>
            {activeSlot.key === "vr360" ? (
              <button type="button" onClick={openActivePanorama} className="inline-flex items-center gap-1 font-semibold hover:text-foreground">
                <Globe2 size={12} />360°查看
              </button>
            ) : (
              <a href={api.imgUrl(activeSlot.url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold hover:text-foreground">
                <Maximize2 size={12} />大图
              </a>
            )}
          </div>

          {activeCandidates.length > 1 && (
            <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
              {activeCandidates.map((candidate) => (
                <button
                  key={`${activeSlot.key}-${candidate.idx}`}
                  type="button"
                  onClick={() => setView((state) => ({ ...state, [activeSlot.key]: candidate }))}
                  title={candidate.metadata?.engine_label
                    ? String(candidate.metadata.engine_label)
                    : `${activeSlot.name} 候选 ${candidate.idx + 1}`}
                  className={cn(
                    "relative size-[52px] flex-none overflow-hidden rounded-lg border-2",
                    candidate.idx === activeSlot.idx ? "border-primary" : "border-transparent opacity-75 hover:opacity-100",
                  )}
                >
                  <img src={api.imgUrl(candidate.thumb)} alt={`${activeSlot.name} 候选 ${candidate.idx + 1}`} className="h-full w-full object-cover" />
                </button>
              ))}
            </div>
          )}

          {terminal && activeSlot.key !== "vr360" && (
            <div className="mt-2.5 grid grid-cols-4 gap-1.5 border-t border-border/70 pt-2.5">
              {([
                ["pass", "通过", Check],
                ["backup", "备选", Bookmark],
                ["rejected", "淘汰", X],
              ] as const).map(([status, label, Icon]) => (
                <button
                  key={status}
                  type="button"
                  disabled={reviewBusy}
                  onClick={() => setReviewStatus(status)}
                  className={cn(
                    "inline-flex h-8 items-center justify-center gap-1 rounded-lg border text-[11.5px] font-bold transition-colors",
                    activeReview.status === status
                      ? status === "pass"
                        ? "border-success bg-success text-white"
                        : status === "backup"
                          ? "border-warn bg-warn text-white"
                          : "border-destructive bg-destructive text-white"
                      : "border-border bg-card text-secondary-foreground hover:bg-accent",
                  )}
                >
                  <Icon size={13} />{label}
                </button>
              ))}
              <button
                type="button"
                disabled={reviewBusy}
                onClick={toggleFavorite}
                className={cn(
                  "inline-flex h-8 items-center justify-center gap-1 rounded-lg border text-[11.5px] font-bold transition-colors",
                  activeReview.favorite
                    ? "border-primary bg-primary-soft text-accent-foreground"
                    : "border-border bg-card text-secondary-foreground hover:bg-accent",
                )}
              >
                <Star size={13} fill={activeReview.favorite ? "currentColor" : "none"} />收藏
              </button>
            </div>
          )}

          <div className="mt-2 flex flex-wrap gap-1.5">
            {terminal && activeGenerationKey && job.floor_path && activeSlot.url.startsWith("/outputs/") && (
              <button className={imageToolBtn} onClick={() => setFloorVisualize({ stage: activeGenerationKey, srcUrl: activeSlot.url, imageRel: activeSlot.url.slice("/outputs/".length) })}>
                <Columns2 size={13} />贴地板
              </button>
            )}
            {terminal && activeGenerationKey && activeSlot.url.startsWith("/outputs/") && (
              <button className={imageToolBtn} onClick={() => setInpaint({ stage: activeGenerationKey, srcUrl: activeSlot.url, imageRel: activeSlot.url.slice("/outputs/".length) })}>
                <Paintbrush size={13} />修补
              </button>
            )}
            {terminal && activeGenerationKey && job.floor_url && activeSlot.url.startsWith("/outputs/") && (
              <button className={imageToolBtn} onClick={() => setColorMatch({ stage: activeGenerationKey, srcUrl: activeSlot.url, imageRel: activeSlot.url.slice("/outputs/".length) })}>
                <Palette size={13} />校色
              </button>
            )}
            {terminal && isPureRender && activeGenerationKey && activeSlot.url.startsWith("/outputs/") && (
              <button
                className={imageToolBtn}
                onClick={() => setPanoramaPaidRequest({
                  action: "generate",
                  source_model: activeGenerationKey,
                  source_index: activeSlot.idx,
                })}
              >
                <Globe2 size={13} />生成 360° VR
              </button>
            )}
            {terminal && activeSlot.key === "vr360" && activePanorama && (
              <>
                <button className={imageToolBtn} onClick={openActivePanorama}>
                  <Globe2 size={13} />360°查看
                </button>
                {job.floor_path && job.floor_url && (
                  <button className={imageToolBtn}
                    onClick={() => setPanoramaFloor({ index: activeSlot.idx, url: activeSlot.url })}>
                    <Columns2 size={13} />本地几何/地板校准
                  </button>
                )}
                <a href={api.imgUrl(activeSlot.url)} target="_blank" rel="noreferrer" className={imageToolBtn}>
                  <Maximize2 size={13} />原始 2:1
                </a>
              </>
            )}
          </div>
        </div>
      )}

      <div className="mt-[11px] flex flex-wrap items-center gap-1.5">
        {active && (
          <button
            className={actBtn}
            onClick={() =>
              act(
                () =>
                  api.cancelJob(job.job_id).then(() => api.getJob(job.job_id)),
                "已请求停止",
              )
            }
          >
            停止
          </button>
        )}
        {terminal &&
          job.has_retry &&
          (job.status === "failed" || job.status === "partial") && (
            <button
              className={actBtn}
              onClick={() => act(() => api.retryJob(job.job_id), "已重试")}
            >
              重试失败线路
            </button>
          )}
        {terminal && job.pro_url && !isFree && (
          <button
            className={actBtn}
            onClick={() => act(() => api.polishJob(job.job_id), "已提交磨缝")}
          >
            <span className="inline-flex items-center gap-1.5"><Sparkles size={13} />磨缝</span>
          </button>
        )}
        {terminal && job.model_runs?.sd35?.delivery_status === "upscale_failed" && (
          <button
            className={actBtn}
            onClick={() => act(() => api.retrySdUpscale(job.job_id), "已重试 SD 超分")}
          >
            <span className="inline-flex items-center gap-1.5"><RefreshCw size={13} />重试超分</span>
          </button>
        )}
        {terminal && (job.pro_url || job.b2_url) && (
          <button className={actBtn} onClick={() => setEditOpen(true)}>
            <span className="inline-flex items-center gap-1.5"><Pencil size={13} />二改</span>
          </button>
        )}
        {terminal && job.room_url && compareAfter && (
          <button className={actBtn} onClick={() => setCompareOpen(true)}>
            <span className="inline-flex items-center gap-1.5"><Columns2 size={13} />对比</span>
          </button>
        )}

        {/* 重抽 / 多抽 */}
        {terminal && job.has_retry && (
          <div className="flex items-center gap-1 rounded-lg border border-border px-1.5 py-0.5">
            {REGEN_NS.map((n) => (
              <button
                key={n}
                onClick={() => setRegenN(n)}
                className={cn(
                  "rounded px-1.5 text-[11.5px] font-semibold",
                  regenN === n
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent",
                )}
              >
                ×{n}
              </button>
            ))}
            <button
              className="ml-0.5 h-[24px] rounded-md border border-border bg-card px-2 text-[11.5px] font-semibold text-secondary-foreground hover:bg-accent"
              onClick={() =>
                act(() => api.regenJob(job.job_id, regenN), `已开始重抽 ×${regenN}`)
              }
            >
              <span className="inline-flex items-center gap-1.5"><RefreshCw size={13} />重抽</span>
            </button>
          </div>
        )}

        {terminal && (
          <button
            className={cn(
              actBtn,
              "hover:border-destructive/30 hover:bg-destructive-soft hover:text-destructive",
            )}
            onClick={remove}
            title="从队列移除此卡（不影响出图与记录）"
          >
            <span className="inline-flex items-center gap-1.5"><Trash2 size={13} />清除</span>
          </button>
        )}
      </div>

      {panoramaPaidRequest && (
        <PanoramaPaidDialog
          open
          onOpenChange={(open) => !open && setPanoramaPaidRequest(null)}
          jobId={job.job_id}
          request={panoramaPaidRequest}
          onCommitted={(snapshot) => {
            applySnapshot(snapshot);
            setView({});
          }}
        />
      )}

      {panoramaView && (
        <PureRenderPanoramaViewerDialog
          open={!!panoramaView}
          onOpenChange={(open) => !open && setPanoramaView(null)}
          jobId={job.job_id}
          panoramaIndex={panoramaView.index}
          erpUrl={panoramaView.url}
          metadata={panoramaView.metadata}
          onSnapshot={(snapshot) => {
            applySnapshot(snapshot);
            const candidate = snapshot.model_runs.vr360?.candidates?.find(
              (item) => item.idx === panoramaView.index,
            );
            if (candidate?.metadata?.panorama) {
              setPanoramaView({
                index: candidate.idx,
                url: candidate.url,
                metadata: candidate.metadata.panorama,
              });
            }
          }}
          onRequestRepair={(panoramaIndex) => {
            setPanoramaView(null);
            setPanoramaPaidRequest({ action: "repair", panorama_index: panoramaIndex });
          }}
          onRequestFloorCorrection={(panoramaIndex) => {
            setPanoramaView(null);
            setPanoramaFloor({ index: panoramaIndex, url: panoramaView.url });
          }}
        />
      )}

      {panoramaFloor && (
        <PanoramaFloorDialog
          open
          onOpenChange={(next) => !next && setPanoramaFloor(null)}
          jobId={job.job_id}
          panoramaIndex={panoramaFloor.index}
          erpUrl={panoramaFloor.url}
          textureUrl={job.floor_url}
          onDone={(snapshot) => {
            if (snapshot) {
              applySnapshot(snapshot);
              setView({});
            }
          }}
        />
      )}

      <ImageZoom url={zoom} onClose={() => setZoom(null)} />

      {/* 生成式修补（画笔涂抹选区，引擎局部重绘，结果并入所点图槽的候选） */}
      {inpaint && (
        <InpaintDialog
          open={!!inpaint}
          onOpenChange={(o) => !o && setInpaint(null)}
          srcUrl={inpaint.srcUrl}
          target={{ kind: "job", jobId: job.job_id, stage: inpaint.stage, imageRel: inpaint.imageRel }}
          onDone={(jv) => {
            if (jv) {
              applySnapshot(jv);
              setView({});
            }
          }}
        />
      )}

      {/* 本地确定性纹理投影（不调用生成模型） */}
      {floorVisualize && (
        <FloorVisualizeDialog
          open={!!floorVisualize}
          onOpenChange={(o) => !o && setFloorVisualize(null)}
          srcUrl={floorVisualize.srcUrl}
          textureUrl={job.floor_url}
          texturePath={job.floor_path}
          target={{
            kind: "job",
            jobId: job.job_id,
            stage: floorVisualize.stage,
            imageRel: floorVisualize.imageRel,
          }}
          onDone={(jv) => {
            if (jv) {
              applySnapshot(jv);
              setView({});
            }
          }}
        />
      )}

      {/* 手动校色（区域化 Reinhard，结果并入所点图槽的候选） */}
      {colorMatch && (
        <ColorMatchDialog
          open={!!colorMatch}
          onOpenChange={(o) => !o && setColorMatch(null)}
          srcUrl={colorMatch.srcUrl}
          imageRel={colorMatch.imageRel}
          refUrl={job.floor_url}
          refPath={job.floor_path}
          target={{ kind: "job", jobId: job.job_id, stage: colorMatch.stage }}
          onDone={(jv) => {
            if (jv) {
              applySnapshot(jv);
              setView({});
            }
          }}
        />
      )}

      {/* 前后对比（原房间图 vs 出图，拖动滑块） */}
      <Dialog open={compareOpen} onOpenChange={setCompareOpen}>
        <DialogContent className="max-w-[96vw] sm:max-w-[min(92vw,1100px)]">
          <div className="space-y-3">
            <div>
              <div className="text-[15.5px] font-bold">前后对比</div>
              <div className="mt-0.5 text-[12px] text-muted-foreground">
                拖动中缝滑块对比原图与效果图 · 切换 ‹n/N› 候选后重开可对比其他张
              </div>
            </div>
            {compareOpen && (
              <CompareSlider
                before={api.imgUrl(job.room_url)}
                after={api.imgUrl(compareAfter)}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <div className="space-y-3">
            <div className="text-[15px] font-bold">二改（对成图做图生图编辑）</div>
            <Input
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              placeholder="编辑指令，例如：把墙换成米白色"
              className="h-10 rounded-[10px] bg-panel"
            />
            <label className="flex cursor-pointer items-start gap-2 text-[12.5px]">
              <input
                type="checkbox"
                checked={editColorMatch}
                onChange={(e) => setEditColorMatch(e.target.checked)}
                className="mt-[3px] accent-[var(--primary)]"
              />
              <span>
                <span className="font-semibold text-foreground">保持原图色彩（防偏色）</span>
                <span className="mt-0.5 block leading-snug text-muted-foreground">
                  二改后自动把整体色温/饱和度拉回原图，消除偏色。若本次就是想改颜色，请取消勾选。
                </span>
              </span>
            </label>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEditOpen(false)}
                className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
              >
                取消
              </button>
              <button
                onClick={() => {
                  const t = editText.trim();
                  if (!t) return;
                  act(
                    () =>
                      api.editJob(job.job_id, {
                        instruction: t,
                        color_match: editColorMatch,
                      }),
                    "已提交二改",
                  );
                  setEditOpen(false);
                  setEditText("");
                }}
                className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover"
              >
                提交
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
