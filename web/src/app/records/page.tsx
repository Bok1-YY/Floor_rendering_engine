/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type {
  GeometryAuditMetadata,
  RecordEntry,
  RecordFile,
  RecordResult,
  ReviewStatus,
  PanoramaGateStatus,
} from "@/lib/types";
import { saveReuseRequest } from "@/lib/draft";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { ImageZoom } from "@/components/ImageZoom";
import { CompareSlider } from "@/components/CompareSlider";
import { ColorMatchDialog } from "@/components/ColorMatchDialog";
import { InpaintDialog } from "@/components/InpaintDialog";
import { FloorVisualizeDialog } from "@/components/FloorVisualizeDialog";
import PanoramaFloorDialog from "@/components/PanoramaFloorDialog";
import PanoViewer from "@/components/PanoViewer";
import { panoramaGateLabel, panoramaGateTone } from "@/lib/pureRenderPano";
import { cn } from "@/lib/utils";

const toolBtn =
  "h-8 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent";
const resultToolBtn =
  "inline-flex h-[30px] items-center justify-center rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold text-secondary-foreground transition-colors hover:bg-accent hover:text-foreground";

const REVIEW_TAGS = [
  "色偏",
  "缝太黑",
  "缝消失",
  "纹理不准",
  "空间太假",
  "地板占比低",
  "风格不对",
  "构图好",
  "客户可用",
];

const REVIEW_STATUS: { value: ReviewStatus; label: string; color: string }[] = [
  { value: "unreviewed", label: "未评", color: "var(--muted-foreground)" },
  { value: "pass", label: "通过", color: "var(--success)" },
  { value: "backup", label: "备选", color: "var(--warn)" },
  { value: "rejected", label: "淘汰", color: "var(--destructive)" },
];

function shortHash(value?: string) {
  if (!value) return "—";
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function auditUnit(unit: string) {
  if (unit === "ratio") return "";
  if (unit === "count") return " 项";
  return unit ? ` ${unit}` : "";
}

function bytesLabel(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(2)} MB`;
}

const extractionWarningLabels: Record<string, string> = {
  ifc_physical_storey_slice_recovered: "从物理楼层切片恢复空间与墙体",
  ifc_openings_recovered_from_fill_elements: "从门窗填充构件恢复开口",
  ifc_container_geometry_from_decomposition: "从 IFC 分解子构件恢复几何",
  ifc_optional_entity_has_no_geometry: "可选 IFC 构件没有可用几何",
  ifc_unrelated_wall_component_excluded: "排除与本楼层空间无关的墙体",
  ifc_spaces_are_overlapping_unit_containers: "源空间是重叠单元容器",
};

function extractionWarningSummary(warning: Record<string, unknown>) {
  const code = String(warning.code || "ifc_extraction_note");
  const facts = [
    typeof warning.base_elevation_m === "number" ? `基准 ${warning.base_elevation_m} m` : "",
    typeof warning.slice_elevation_m === "number" ? `切片 ${Number(warning.slice_elevation_m).toFixed(2)} m` : "",
    typeof warning.selected_wall_count === "number" ? `墙 ${warning.selected_wall_count}` : "",
    typeof warning.recovered_space_count === "number" ? `空间 ${warning.recovered_space_count}` : "",
    typeof warning.associated_opening_count === "number" ? `开口 ${warning.associated_opening_count}` : "",
    typeof warning.maximum_host_distance_m === "number" ? `宿主容差 ${warning.maximum_host_distance_m} m` : "",
  ].filter(Boolean);
  return {
    code,
    label: extractionWarningLabels[code] || code,
    facts: facts.join(" · "),
  };
}

function GeometryAuditDetail({
  audit,
  jsonPath,
  recordId,
  onSaved,
}: {
  audit: GeometryAuditMetadata;
  jsonPath: string;
  recordId: string;
  onSaved: () => void;
}) {
  const [checked, setChecked] = useState<string[]>(audit.review?.checked_metric_ids || []);
  const [reviewer, setReviewer] = useState(audit.review?.reviewer || "");
  const [note, setNote] = useState(audit.review?.note || "");
  const [saving, setSaving] = useState(false);
  const metrics = audit.channels.flatMap((channel) => channel.metrics || []);

  async function saveReview(nextChecked = checked) {
    setSaving(true);
    try {
      await api.reviewGeometryAudit({
        json_path: jsonPath,
        record_id: recordId,
        checked_metric_ids: nextChecked,
        reviewer,
        note,
      });
      toast.success(`复核进度已保存：${nextChecked.length}/${metrics.length}`);
      onSaved();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function toggleMetric(metricId: string) {
    const next = checked.includes(metricId)
      ? checked.filter((id) => id !== metricId)
      : [...checked, metricId];
    setChecked(next);
    await saveReview(next);
  }

  const statusPassed = audit.status === "passed";
  const progress = metrics.length ? Math.round((checked.length / metrics.length) * 100) : 0;
  const counts = Object.entries(audit.source.counts || {});
  const extractionWarnings = audit.source.extraction_warnings || [];
  return (
    <div className="mb-4 space-y-4 rounded-xl border border-border bg-panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn(
              "rounded-full px-2.5 py-1 text-[11px] font-black",
              statusPassed ? "bg-success-soft text-success" : "bg-destructive-soft text-destructive",
            )}>
              {audit.level} · {statusPassed ? "自动验收通过" : "自动验收失败"}
            </span>
            <span className="text-[12px] font-semibold text-secondary-foreground">
              {audit.source.dataset} · {audit.source.license}
            </span>
          </div>
          <h3 className="mt-2 text-[16px] font-black text-foreground">{audit.title}</h3>
          <p className="mt-1 text-[11.5px] text-muted-foreground">
            执行时间 {audit.executed_at} · runner {audit.runner_version}
          </p>
        </div>
        <div className="min-w-[220px] rounded-lg border border-border bg-card px-3 py-2.5">
          <div className="flex items-center justify-between text-[11px] font-bold">
            <span>人工逐项复核</span>
            <span className={progress === 100 ? "text-success" : "text-muted-foreground"}>
              {checked.length}/{metrics.length} · {progress}%
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-[11.5px] lg:grid-cols-4">
        <div className="rounded-lg border border-border bg-card p-2.5">
          <div className="text-muted-foreground">楼层</div>
          <div className="mt-1 font-bold">{audit.source.storey?.name || "—"}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-2.5">
          <div className="text-muted-foreground">证据完整性</div>
          <div className={cn("mt-1 font-bold", audit.integrity.status === "passed" ? "text-success" : "text-destructive")}>
            {audit.integrity.status === "passed" ? `通过 · ${audit.integrity.checked_count} 个文件` : "失败"}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-2.5">
          <div className="text-muted-foreground">源 IFC SHA-256</div>
          <div className="mt-1 font-mono font-bold" title={audit.source.source_sha256}>
            {shortHash(audit.source.source_sha256)}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-2.5">
          <div className="text-muted-foreground">审计报告 hash</div>
          <div className="mt-1 font-mono font-bold" title={audit.audit_hash}>{shortHash(audit.audit_hash)}</div>
        </div>
      </div>

      {counts.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {counts.map(([key, value]) => (
            <span key={key} className="rounded-md border border-border bg-card px-2 py-1 text-[11px]">
              <span className="text-muted-foreground">{key}</span> <b>{value}</b>
            </span>
          ))}
        </div>
      )}

      {extractionWarnings.length > 0 && (
        <details className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-[11.5px] text-amber-950 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100">
          <summary className="cursor-pointer font-black">
            IFC 恢复与取舍依据 · {extractionWarnings.length} 条（展开逐项核对）
          </summary>
          <div className="mt-3 space-y-2">
            {extractionWarnings.map((warning, index) => {
              const summary = extractionWarningSummary(warning);
              return <div key={`${summary.code}-${index}`} className="rounded-md border border-amber-300/70 bg-white/60 p-2 dark:bg-black/10">
                <div className="font-bold">{summary.label}</div>
                <div className="mt-0.5 font-mono text-[10px] opacity-75">{summary.code}</div>
                {summary.facts ? <div className="mt-1">{summary.facts}</div> : null}
                {typeof warning.reason === "string" ? <div className="mt-1 text-[10.5px] opacity-80">{warning.reason}</div> : null}
              </div>;
            })}
          </div>
        </details>
      )}

      {audit.channels.map((channel) => (
        <section key={channel.channel_id} className="overflow-hidden rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
            <div className="text-[12.5px] font-black">{channel.label}</div>
            <span className={cn(
              "rounded-md px-2 py-0.5 text-[10.5px] font-black",
              channel.status === "passed" ? "bg-success-soft text-success" : "bg-destructive-soft text-destructive",
            )}>
              {channel.status === "passed" ? "PASSED" : channel.status.toUpperCase()}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[650px] border-collapse text-left text-[11.5px]">
              <thead className="bg-muted/60 text-muted-foreground">
                <tr>
                  <th className="w-16 px-3 py-2">核对</th>
                  <th className="px-3 py-2">指标</th>
                  <th className="px-3 py-2">实测值</th>
                  <th className="px-3 py-2">通过阈值</th>
                  <th className="px-3 py-2">机器判定</th>
                </tr>
              </thead>
              <tbody>
                {channel.metrics.map((metric) => (
                  <tr key={metric.metric_id} className="border-t border-border first:border-t-0">
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        checked={checked.includes(metric.metric_id)}
                        disabled={saving}
                        aria-label={`核对 ${metric.label}`}
                        onChange={() => void toggleMetric(metric.metric_id)}
                        className="h-4 w-4 accent-primary"
                      />
                    </td>
                    <td className="px-3 py-2.5 font-semibold">{metric.label}</td>
                    <td className="px-3 py-2.5 font-mono font-bold">
                      {metric.actual_display}{auditUnit(metric.unit)}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-muted-foreground">
                      {metric.operator} {metric.threshold_display}{auditUnit(metric.unit)}
                    </td>
                    <td className={cn("px-3 py-2.5 font-black", metric.status === "passed" ? "text-success" : "text-destructive")}>
                      {metric.status === "passed" ? "✓ 通过" : "✕ 失败"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}

      <section className="rounded-lg border border-border bg-card p-3">
        <div className="mb-2 text-[12.5px] font-black">证据文件与校验和</div>
        <div className="grid gap-2 lg:grid-cols-2">
          {audit.artifacts.map((artifact) => (
            <div key={artifact.artifact_id} className="flex min-w-0 items-center justify-between gap-2 rounded-md border border-border px-2.5 py-2">
              <div className="min-w-0">
                <div className="truncate text-[11.5px] font-bold" title={artifact.file_name}>{artifact.label}</div>
                <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground" title={artifact.sha256}>
                  {bytesLabel(artifact.size_bytes)} · {shortHash(artifact.sha256)}
                </div>
              </div>
              <div className="flex flex-none items-center gap-1.5">
                <span className={cn("text-[10.5px] font-black", artifact.integrity_status === "passed" ? "text-success" : "text-destructive")}>
                  {artifact.integrity_status === "passed" ? "SHA ✓" : "校验失败"}
                </span>
                {artifact.available && (
                  <button
                    className={resultToolBtn}
                    onClick={() => window.open(api.geometryAuditArtifactUrl(jsonPath, recordId, artifact.artifact_id), "_blank")}
                  >
                    下载
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-border bg-card p-3">
        <div className="mb-2 text-[12.5px] font-black">我的复核记录</div>
        <div className="grid gap-2 lg:grid-cols-[220px_1fr_auto]">
          <Input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="复核人（可选）" className="h-9" />
          <Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="核对备注（可选）" className="h-9" />
          <button disabled={saving} onClick={() => void saveReview()} className={cn(toolBtn, "disabled:opacity-50")}>
            {saving ? "保存中…" : "保存备注"}
          </button>
        </div>
        {audit.review?.reviewed_at && (
          <div className="mt-2 text-[10.5px] text-muted-foreground">
            上次保存：{audit.review.reviewed_at}{audit.review.reviewer ? ` · ${audit.review.reviewer}` : ""}
          </div>
        )}
      </section>
    </div>
  );
}

export default function RecordsPage() {
  const router = useRouter();
  const [files, setFiles] = useState<RecordFile[]>([]);
  const [search, setSearch] = useState("");
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const [records, setRecords] = useState<RecordEntry[]>([]);
  const [roomFilter, setRoomFilter] = useState("__all__");
  const [reviewFilter, setReviewFilter] = useState<"__all__" | ReviewStatus | "__best__">("__all__");
  const [loading, setLoading] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);
  const [panoView, setPanoView] = useState<{
    url: string;
    label: string;
    kind: "whole_home" | "pure_render";
    gatePass?: boolean;
    gateStatus?: PanoramaGateStatus;
    failures: string[];
    initialYawDeg: number;
  } | null>(null);
  // 前后对比（替换类工作流的记录才有 gen_context.room_url）
  const [compare, setCompare] = useState<{ before: string; after: string } | null>(null);
  // 手动校色（新旧记录均由后端解析参照小样）
  const [colorMatch, setColorMatch] = useState<{
    open: boolean;
    srcUrl: string;
    imageRel: string;
    refUrl: string;
    refPath: string;
    recordId: string;
    resultId: string;
  } | null>(null);

  // 生成式修补（画笔涂抹选区，结果追加回记录）
  const [inpaint, setInpaint] = useState<{
    open: boolean;
    srcUrl: string;
    recordId: string;
    resultId: string;
  } | null>(null);
  const [floorVisualize, setFloorVisualize] = useState<{
    open: boolean;
    srcUrl: string;
    textureUrl: string;
    texturePath: string;
    recordId: string;
    resultId: string;
  } | null>(null);
  const [panoramaFloor, setPanoramaFloor] = useState<{
    srcUrl: string;
    textureUrl: string;
    texturePath: string;
    recordId: string;
    resultId: string;
  } | null>(null);

  // 解密弹窗
  const [reveal, setReveal] = useState<{
    open: boolean;
    rid: string;
    pw: string;
    text: string;
  }>({ open: false, rid: "", pw: "", text: "" });

  // 记录内二改弹窗
  const [edit, setEdit] = useState<{
    open: boolean;
    rid: string;
    resultId: string;
    instruction: string;
    colorMatch: boolean;
  }>({ open: false, rid: "", resultId: "", instruction: "", colorMatch: true });

  const [review, setReview] = useState<{
    open: boolean;
    rid: string;
    resultId: string;
    status: ReviewStatus;
    tags: string[];
    note: string;
    best: boolean;
  }>({
    open: false,
    rid: "",
    resultId: "",
    status: "unreviewed",
    tags: [],
    note: "",
    best: false,
  });

  // 加载乱序防护：快速连点两个记录文件时，先发的响应后到会覆盖后选文件的内容（左侧高亮与右侧内容错位）
  const openSeq = useRef(0);

  useEffect(() => {
    const seq = ++openSeq.current;
    void (async () => {
      try {
        const next = await api.listRecords();
        if (seq !== openSeq.current) return;
        setFiles(next);
        const searchParams = new URLSearchParams(window.location.search);
        const requestedPath = searchParams.get("json_path") || "";
        const requestedRecordId = searchParams.get("record_id") || "";
        const targetPath = next.some((file) => file.json_path === requestedPath)
          ? requestedPath
          : next[0]?.json_path;
        if (!targetPath) return;
        setActive(targetPath);
        setLoading(true);
        const recs = await api.loadRecord(targetPath);
        if (seq === openSeq.current) {
          setRecords(recs);
          if (requestedRecordId && recs.some((record) => record.id === requestedRecordId)) {
            window.requestAnimationFrame(() => {
              window.requestAnimationFrame(() => {
                document.getElementById(`record-${requestedRecordId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
              });
            });
          }
        }
      } catch (e) {
        if (seq === openSeq.current) toast.error((e as Error).message);
      } finally {
        if (seq === openSeq.current) setLoading(false);
      }
    })();
    return () => {
      openSeq.current += 1;
    };
  }, []);

  async function open(jsonPath: string) {
    const seq = ++openSeq.current;
    setActive(jsonPath);
    setRoomFilter("__all__");
    setReviewFilter("__all__");
    setLoading(true);
    try {
      const recs = await api.loadRecord(jsonPath);
      if (seq !== openSeq.current) return;
      setRecords(recs);
    } catch (e) {
      if (seq === openSeq.current) toast.error((e as Error).message);
    } finally {
      if (seq === openSeq.current) setLoading(false);
    }
  }

  async function reload() {
    if (!active) return;
    const seq = ++openSeq.current;
    try {
      const recs = await api.loadRecord(active);
      if (seq !== openSeq.current) return;
      setRecords(recs);
    } catch (e) {
      if (seq === openSeq.current) toast.error((e as Error).message);
    }
  }

  async function reloadFiles() {
    const next = await api.listRecords();
    setFiles(next);
  }

  const visibleFiles = files.filter((f) => {
    if (favoriteOnly && f.favorite_count === 0) return false;
    return search.trim()
      ? (f.json_path.split(/[\\/]/).pop() || "")
          .toLowerCase()
          .includes(search.trim().toLowerCase())
      : true;
  });
  const totalFavorites = files.reduce((sum, f) => sum + f.favorite_count, 0);

  const roomCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of records) {
      const rt = (r.room_type || "").trim();
      if (rt) c[rt] = (c[rt] || 0) + 1;
    }
    return c;
  }, [records]);

  function resultVisible(res: RecordResult) {
    if (favoriteOnly && !res.favorite) return false;
    if (reviewFilter === "__all__") return true;
    if (reviewFilter === "__best__") return !!res.best;
    return (res.review_status || "unreviewed") === reviewFilter;
  }

  const shownRecords = records
    .filter((r) => roomFilter === "__all__" || (r.room_type || "") === roomFilter)
    .map((r) => ({
      ...r,
      results: (r.results || [])
        .map((res, idx) => ({ ...res, __idx: idx }))
        .filter(resultVisible),
    }))
    .filter((r) => Boolean(r.geometry_audit) || (r.results || []).length > 0);

  function download(url: string) {
    window.open(url, "_blank");
  }

  async function doDeleteResult(rid: string, resultId: string) {
    if (!active || !window.confirm("确认删除这张效果图？")) return;
    try {
      await api.deleteResult(active, rid, resultId);
      toast.success("已删除");
      await Promise.all([reload(), reloadFiles()]);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doFav(rid: string, resultId: string) {
    if (!active) return;
    try {
      const r = await api.favoriteResult(active, rid, resultId);
      toast.success(r.favorite ? "已收藏" : "已取消收藏");
      await Promise.all([reload(), reloadFiles()]);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doReview(
    rid: string,
    resultId: string,
    patch: Partial<{
      status: ReviewStatus;
      tags: string[];
      note: string;
      best: boolean;
    }>,
    source?: RecordResult,
  ) {
    if (!active) return;
    const src = source || records
      .find((r) => r.id === rid)
      ?.results?.find((item) => item.result_id === resultId);
    try {
      await api.reviewResult({
        json_path: active,
        record_id: rid,
        result_id: resultId,
        review_status: patch.status ?? src?.review_status ?? "unreviewed",
        review_tags: patch.tags ?? src?.review_tags ?? [],
        review_note: patch.note ?? src?.review_note ?? "",
        best: patch.best ?? !!src?.best,
      });
      toast.success("已保存标注");
      reload();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  function openReviewDialog(rid: string, resultId: string, res: RecordResult) {
    setReview({
      open: true,
      rid,
      resultId,
      status: res.review_status || "unreviewed",
      tags: [...(res.review_tags || [])],
      note: res.review_note || "",
      best: !!res.best,
    });
  }

  async function doReviewSubmit() {
    await doReview(review.rid, review.resultId, {
      status: review.status,
      tags: review.tags,
      note: review.note,
      best: review.best,
    });
    setReview((s) => ({ ...s, open: false }));
  }

  // 替换类工作流（地板替换 / 墙板·替换）且有房间原图 → 结果图可做前后对比
  function compareBeforeUrl(r: RecordEntry): string {
    const gc = r.gen_context;
    if (r.pano_audit && gc?.room_url) return gc.room_url;
    if (!gc?.room_url || !gc.params) return "";
    const wf = gc.params.workflow_mode || "";
    const isReplace =
      wf.includes("地板替换") ||
      (wf.includes("墙板") && (gc.params.panel_submode || "").includes("替换"));
    return isReplace ? gc.room_url : "";
  }

  // 复用参数：把这条记录的 gen_context 快照写进一次性回填请求，跳到生成页。
  // 老记录（无 gen_context）不显示入口。floor_tone 沿用快照值，不重新识色。
  function doReuse(r: RecordEntry) {
    const gc = r.gen_context;
    if (r.user_prompt && gc?.free_image_paths?.length) {
      saveReuseRequest({
        params: {
          workflow_mode: "自由创作 (自定义提示词/多图)",
          aspect_ratio: gc.free_options?.aspect_ratio,
          resolution: gc.free_options?.resolution,
        },
        modelFilter: gc.model_filter,
        modelTargets: gc.model_targets,
        freePrompt: r.user_prompt,
        freeImagePaths: gc.free_image_paths,
        freeOptions: gc.free_options,
      });
      toast.success("自由提示词与 Slot 顺序已载入生成页");
      router.push("/");
      return;
    }
    if (!gc?.params) return;
    saveReuseRequest({
      params: gc.params,
      modelFilter: gc.model_filter,
      modelTargets: gc.model_targets,
      sdOptions: gc.sd_options,
      floorPath: gc.image_path,
      roomPath: gc.room_path || undefined,
      refPath: gc.ref_path || undefined,
    });
    toast.success("参数已载入生成页");
    router.push("/");
  }

  async function doDeleteRecord(rid: string) {
    if (!active || !window.confirm("确认删除整条记录（含其所有效果图引用）？")) return;
    try {
      await api.deleteRecord(active, rid);
      toast.success("已删除记录");
      await Promise.all([reload(), reloadFiles()]);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doReveal() {
    if (!active) return;
    try {
      const r = await api.reveal(active, reveal.rid, reveal.pw);
      setReveal((s) => ({ ...s, text: r.text }));
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doEditSubmit() {
    if (!active) return;
    const t = edit.instruction.trim();
    if (!t) return;
    try {
      await api.recordEdit({
        json_path: active,
        record_id: edit.rid,
        result_id: edit.resultId,
        instruction: t,
        color_match: edit.colorMatch,
      });
      toast.success("已提交二改（在「生成」页可看进度，完成后回此刷新）");
      setEdit({ open: false, rid: "", resultId: "", instruction: "", colorMatch: true });
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const roomChip = (active_: boolean) =>
    cn(
      "rounded-lg border px-[13px] py-1.5 text-[12.5px] font-semibold transition-colors",
      active_
        ? "border-primary bg-primary-soft text-accent-foreground"
        : "border-border bg-card text-secondary-foreground hover:bg-accent",
    );

  const reviewChip = (active_: boolean) =>
    cn(
      "rounded-lg border px-[11px] py-1.5 text-[12px] font-semibold transition-colors",
      active_
        ? "border-primary bg-primary-soft text-accent-foreground"
        : "border-border bg-card text-secondary-foreground hover:bg-accent",
    );

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左栏：文件列表 + 搜索 + 收藏筛选/导出 */}
      <aside className="flex w-[280px] flex-none flex-col border-r border-border bg-panel px-[14px] py-[16px] max-[1320px]:w-[232px] max-[1080px]:w-[210px]">
        <div className="relative mb-[9px]">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="absolute left-[11px] top-[11px] text-muted-foreground">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4-4" />
          </svg>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索历史记录…"
            className="h-9 rounded-[9px] bg-card pl-8 text-[13px]"
          />
        </div>
        <button
          type="button"
          aria-pressed={favoriteOnly}
          onClick={() => setFavoriteOnly((on) => !on)}
          className={cn(
            "mb-2 flex h-9 w-full items-center justify-between rounded-[9px] border px-3 text-[12.5px] font-bold transition-colors",
            favoriteOnly
              ? "border-primary bg-primary-soft text-accent-foreground"
              : "border-border bg-card text-secondary-foreground hover:bg-accent",
          )}
        >
          <span>⭐ 只看收藏</span>
          <span className="text-[11px] tabular-nums text-muted-foreground">{totalFavorites}</span>
        </button>
        <button
          onClick={() => download(api.exportFavoritesUrl())}
          className="mb-[11px] flex h-9 w-full items-center justify-center gap-1.5 rounded-[9px] border border-border bg-card text-[12.5px] font-bold text-accent-foreground hover:bg-accent"
        >
          ⭐ 导出收藏夹 PPTX
        </button>
        <div className="px-1 pb-1.5 text-[11px] font-semibold text-muted-foreground">
          历史记录
        </div>
        <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto">
          {visibleFiles.length === 0 && (
            <div className="px-2 py-1 text-xs text-muted-foreground">
              {favoriteOnly ? "没有收藏记录" : "无记录"}
            </div>
          )}
          {visibleFiles.map((f) => {
            const on = active === f.json_path;
            return (
              <button
                key={f.json_path}
                onClick={() => open(f.json_path)}
                title={f.json_path}
                className={cn(
                  "block w-full whitespace-normal break-words rounded-lg px-[10px] py-2 text-left text-[12.5px] leading-snug",
                  on
                    ? "bg-accent font-bold text-accent-foreground"
                    : "font-medium text-secondary-foreground hover:bg-accent",
                )}
              >
                {f.json_path.split(/[\\/]/).pop()?.replace("_记录.json", "")}{" "}
                <span className="text-muted-foreground">
                  ({f.labels.length}{f.favorite_count ? ` · ⭐${f.favorite_count}` : ""})
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      {/* 右栏 */}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        {!active ? (
          <div className="flex flex-1 items-center justify-center">
            <div className="text-center text-muted-foreground">
              <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" className="mx-auto mb-3 text-border-strong">
                <rect x="3" y="4" width="18" height="6" rx="1.6" />
                <rect x="3" y="14" width="18" height="6" rx="1.6" />
              </svg>
              <div className="text-[13.5px] font-semibold">
                从左侧选择一条历史记录查看
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-none flex-wrap items-center justify-between gap-2.5 border-b border-border px-[22px] py-[14px]">
              <div className="flex min-w-0 flex-1 flex-col gap-2">
                <div className="flex flex-wrap gap-[7px]">
                  <button
                    onClick={() => setRoomFilter("__all__")}
                    className={roomChip(roomFilter === "__all__")}
                  >
                    全部房间
                  </button>
                  {Object.entries(roomCounts).map(([rt, n]) => (
                    <button
                      key={rt}
                      onClick={() => setRoomFilter(rt)}
                      className={roomChip(roomFilter === rt)}
                    >
                      {rt} ({n})
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap gap-[7px]">
                  <button
                    onClick={() => setReviewFilter("__all__")}
                    className={reviewChip(reviewFilter === "__all__")}
                  >
                    全部评审
                  </button>
                  <button
                    onClick={() => setReviewFilter("__best__")}
                    className={reviewChip(reviewFilter === "__best__")}
                  >
                    最佳
                  </button>
                  {REVIEW_STATUS.map((s) => (
                    <button
                      key={s.value}
                      onClick={() => setReviewFilter(s.value)}
                      className={reviewChip(reviewFilter === s.value)}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={reload} className={toolBtn}>
                  刷新
                </button>
                <button onClick={() => download(api.exportHtmlUrl(active))} className={toolBtn}>
                  导出 HTML
                </button>
                <button onClick={() => download(api.exportPptxUrl(active))} className={toolBtn}>
                  导出 PPTX
                </button>
              </div>
            </div>

            <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-[22px] py-[18px]">
              {loading && <div className="text-sm text-muted-foreground">加载中…</div>}
              {!loading && shownRecords.length === 0 && (
                <div className="flex flex-1 items-center justify-center rounded-[14px] border border-dashed border-border py-16 text-[13px] text-muted-foreground">
                  {favoriteOnly ? "当前筛选下没有收藏结果" : "当前筛选下没有结果"}
                </div>
              )}
              {!loading &&
                shownRecords.map((r, i) => {
                  const rid = r.id || "";
                  const immutableAudit = Boolean(r.immutable_audit);
                  const panoAudit = r.pano_audit;
                  const geometryAudit = r.geometry_audit;
                  const isLegacyPanoRecord = Boolean(
                    immutableAudit && panoAudit &&
                    (!panoAudit.projection || panoAudit.projection === "equirectangular"),
                  );
                  return (
                    <div
                      key={rid || i}
                      id={rid ? `record-${rid}` : undefined}
                      data-record-id={rid || undefined}
                      className="rounded-[14px] border border-border bg-card p-[15px] shadow-[0_2px_8px_rgba(120,90,60,.05)]"
                    >
                      <div className="mb-3 flex items-start justify-between gap-2">
                        <span className="min-w-0 flex-1 break-words text-[13.5px] font-bold leading-snug text-foreground">
                          {geometryAudit?.title || rid || `记录 ${i + 1}`}
                          {geometryAudit ? ` · ${geometryAudit.level}` : ""}
                          {!geometryAudit && r.room_type ? ` · ${r.room_type}` : ""}
                          {!geometryAudit && r.workflow_mode ? ` · ${String(r.workflow_mode)}` : ""}
                        </span>
                        <div className="flex flex-none flex-wrap justify-end gap-1.5 text-muted-foreground">
                          {!immutableAudit && (r.gen_context?.params || (r.user_prompt && r.gen_context?.free_image_paths?.length)) && (
                            <button
                              title="用这套参数再生成"
                              onClick={() => doReuse(r)}
                              className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold hover:bg-accent hover:text-accent-foreground"
                            >
                              ⟳ 复用
                            </button>
                          )}
                          {!immutableAudit && !r.user_prompt && (
                            <button
                              title="解密提示词"
                              onClick={() => setReveal({ open: true, rid, pw: "", text: "" })}
                              className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold hover:bg-accent hover:text-foreground"
                            >
                              🔑 提示词
                            </button>
                          )}
                          {immutableAudit ? (
                            <span className="rounded-md bg-muted px-2 py-1 text-[11px] font-bold">
                              {geometryAudit ? "只读几何证据" : "只读审计记录"}
                            </span>
                          ) : (
                            <button
                              title="删除记录"
                              onClick={() => doDeleteRecord(rid)}
                              className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold hover:bg-destructive-soft hover:text-destructive"
                            >
                              删除
                            </button>
                          )}
                        </div>
                      </div>

                      {r.user_prompt && (
                        <div className="mb-3 rounded-[10px] border border-border bg-panel px-3 py-2.5">
                          <div className="mb-1.5 flex items-center justify-between gap-3">
                            <span className="text-[11px] font-bold text-secondary-foreground">自由指令词</span>
                            <button
                              onClick={() => {
                                navigator.clipboard.writeText(r.user_prompt || "");
                                toast.success("指令词已复制");
                              }}
                              className="text-[11px] font-semibold text-muted-foreground hover:text-foreground"
                            >
                              复制
                            </button>
                          </div>
                          <p className="whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-secondary-foreground">
                            {r.user_prompt}
                          </p>
                        </div>
                      )}

                      {geometryAudit && active && (
                        <GeometryAuditDetail
                          key={`${geometryAudit.audit_hash}:${geometryAudit.review?.reviewed_at || ""}`}
                          audit={geometryAudit}
                          jsonPath={active}
                          recordId={rid}
                          onSaved={() => void reload()}
                        />
                      )}

                      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-[14px]">
                        {(r.results || []).map((res, j) => {
                          const resultId = res.result_id;
                          const url = res.result_url || "";
                          const thumb = res.result_thumb || url;
                          const status = res.review_status || "unreviewed";
                          const statusMeta =
                            REVIEW_STATUS.find((s) => s.value === status) || REVIEW_STATUS[0];
                          const purePanorama = res.generation_metadata?.panorama;
                          const isPanoResult = Boolean(
                            purePanorama?.projection === "equirectangular" || isLegacyPanoRecord,
                          );
                          const openPano = () => setPanoView({
                            url: api.imgUrl(url),
                            label: `${rid} · ${res.model_label || "球面全景"}`,
                            kind: purePanorama ? "pure_render" : "whole_home",
                            gatePass: purePanorama
                              ? purePanorama.gate?.status === "passed"
                              : panoAudit?.gate?.gate_pass,
                            gateStatus: purePanorama?.gate?.status,
                            failures: purePanorama?.gate?.failures || panoAudit?.gate?.failures || [],
                            initialYawDeg: purePanorama?.viewer_initial_yaw_deg ?? 0,
                          });
                          return (
                            <div key={resultId || j}>
                              <div className={cn(
                                "relative overflow-hidden rounded-[10px] border border-border",
                                isPanoResult ? "aspect-[2/1]" : "aspect-[4/3]",
                              )}>
                                {url ? (
                                  <>
                                    <img
                                      src={api.imgUrl(thumb)}
                                      alt={res.model_label || "result"}
                                      onClick={() => isPanoResult ? openPano() : setZoom(api.imgUrl(url))}
                                      className="absolute inset-0 h-full w-full cursor-zoom-in object-cover"
                                    />
                                    {res.model_label && (
                                      <span className="absolute left-[7px] top-[7px] rounded-md bg-[rgba(26,24,21,.55)] px-[7px] py-[2px] text-[10px] font-bold text-white">
                                        {res.model_label}
                                      </span>
                                    )}
                                    {res.best && (
                                      <span className="absolute right-[7px] top-[7px] rounded-md bg-primary px-[7px] py-[2px] text-[10px] font-bold text-white">
                                        最佳
                                      </span>
                                    )}
                                    {isPanoResult && (
                                      <span className="absolute bottom-[7px] right-[7px] rounded-md bg-sky-600 px-[7px] py-[2px] text-[10px] font-bold text-white">
                                        360°
                                      </span>
                                    )}
                                  </>
                                ) : (
                                  <div className="flex h-full items-center justify-center bg-muted text-[11px] text-muted-foreground">
                                    {res.has_inline ? "内联图(旧)" : "无图"}
                                  </div>
                                )}
                              </div>
                              <div className="mt-2 flex flex-col gap-2 text-[11.5px] text-muted-foreground">
                                <span className="truncate font-semibold">{res.model_label || "候选图"}</span>
                                <span className="flex flex-wrap items-center gap-1.5">
                                  <button
                                    title="收藏"
                                    onClick={() => doFav(rid, resultId)}
                                    className={cn(resultToolBtn, res.favorite && "border-primary/30 bg-primary-soft text-primary")}
                                  >
                                    {res.favorite ? "★ 已收藏" : "☆ 收藏"}
                                  </button>
                                  {!immutableAudit && !isPanoResult && <button
                                    title="二改"
                                    onClick={() =>
                                      setEdit({ open: true, rid, resultId, instruction: "", colorMatch: true })
                                    }
                                    className={resultToolBtn}
                                  >
                                    ✎ 二改
                                  </button>}
                                  {!immutableAudit && !isPanoResult && url && url.startsWith("/outputs/") && (
                                    <button
                                      title="用原始小样像素重新投影地板（无生成模型费用）"
                                      onClick={() =>
                                        setFloorVisualize({
                                          open: true,
                                          srcUrl: url,
                                          textureUrl: r.gen_context?.image_url || "",
                                          texturePath: r.gen_context?.image_path || "",
                                          recordId: rid,
                                          resultId,
                                        })
                                      }
                                      disabled={!r.gen_context?.image_path}
                                      className={cn(resultToolBtn, "disabled:hidden")}
                                    >
                                      🪵 贴地板
                                    </button>
                                  )}
                                  {!immutableAudit && !isPanoResult && url && url.startsWith("/outputs/") && (
                                    <button
                                      title="生成式修补（画笔涂抹移除/添加物体）"
                                      onClick={() =>
                                        setInpaint({ open: true, srcUrl: url, recordId: rid, resultId })
                                      }
                                      className={resultToolBtn}
                                    >
                                      🖌️ 智能修补
                                    </button>
                                  )}
                                  {!immutableAudit && !isPanoResult && url && url.startsWith("/outputs/") && (
                                    <button
                                      title="手动校色（以地板小样为参照，框选地板区域）"
                                      onClick={() =>
                                        setColorMatch({
                                          open: true,
                                          srcUrl: url,
                                          imageRel: url.slice("/outputs/".length),
                                          refUrl: r.color_match_ref_url || "",
                                          refPath: r.color_match_ref_path || "",
                                          recordId: rid,
                                          resultId,
                                        })
                                      }
                                      className={resultToolBtn}
                                    >
                                      🎯 校色
                                    </button>
                                  )}
                                  {url && !isPanoResult && compareBeforeUrl(r) && (
                                    <button
                                      title="前后对比"
                                      onClick={() =>
                                        setCompare({
                                          before: compareBeforeUrl(r),
                                          after: url,
                                        })
                                      }
                                      className={resultToolBtn}
                                    >
                                      ⇔ 对比
                                    </button>
                                  )}
                                  {url && isPanoResult && (
                                    <button
                                      title="用鼠标拖动查看 360° 球面全景"
                                      onClick={openPano}
                                      className={resultToolBtn}
                                    >
                                      ◉ 360°查看
                                    </button>
                                  )}
                                  {!immutableAudit && url && isPanoResult && purePanorama && (r.gen_context?.image_path || r.gen_context?.floor_path) && (
                                    <button
                                      title="把地板小样投影到同一球面地板平面，修复 360° 错铺"
                                      onClick={() => setPanoramaFloor({
                                        srcUrl: url,
                                        textureUrl: r.gen_context?.image_url || r.gen_context?.floor_url || "",
                                        texturePath: r.gen_context?.image_path || r.gen_context?.floor_path || "",
                                        recordId: rid,
                                        resultId,
                                      })}
                                      className={resultToolBtn}
                                    >
                                      🪵 本地几何/地板校准
                                    </button>
                                  )}
                                  {url && isPanoResult && (
                                    <button
                                      title="查看原始 2:1 ERP 图片"
                                      onClick={() => setZoom(api.imgUrl(url))}
                                      className={resultToolBtn}
                                    >
                                      ▣ 原始2:1
                                    </button>
                                  )}
                                  {url && (
                                    <button
                                      title="下载"
                                      onClick={() => download(api.imgUrl(url))}
                                      className={resultToolBtn}
                                    >
                                      ↓ 下载
                                    </button>
                                  )}
                                  {!immutableAudit && <button
                                    title="删除"
                                    onClick={() => doDeleteResult(rid, resultId)}
                                    className={cn(resultToolBtn, "hover:bg-destructive-soft hover:text-destructive")}
                                  >
                                    删除
                                  </button>}
                                </span>
                              </div>
                              {!immutableAudit && !isPanoResult && <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                                {REVIEW_STATUS.slice(1).map((s) => (
                                  <button
                                    key={s.value}
                                    onClick={() =>
                                      doReview(
                                        rid,
                                        resultId,
                                        { status: status === s.value ? "unreviewed" : s.value },
                                        res,
                                      )
                                    }
                                    className={cn(
                                      "h-6 rounded-md border px-2 text-[11px] font-semibold",
                                      status === s.value
                                        ? "border-transparent text-white"
                                        : "border-border bg-card text-secondary-foreground hover:bg-accent",
                                    )}
                                    style={status === s.value ? { background: s.color } : undefined}
                                  >
                                    {s.label}
                                  </button>
                                ))}
                                <button
                                  onClick={() =>
                                    doReview(rid, resultId, { best: !res.best }, res)
                                  }
                                  className={cn(
                                    "h-6 rounded-md border px-2 text-[11px] font-semibold",
                                    res.best
                                      ? "border-primary bg-primary text-white"
                                      : "border-border bg-card text-secondary-foreground hover:bg-accent",
                                  )}
                                >
                                  最佳
                                </button>
                                <button
                                  onClick={() => openReviewDialog(rid, resultId, res)}
                                  className="h-6 rounded-md border border-border bg-card px-2 text-[11px] font-semibold text-secondary-foreground hover:bg-accent"
                                >
                                  标注
                                </button>
                              </div>}
                              <div className="mt-1 flex items-center gap-1.5 text-[11px]">
                                <span style={{ color: statusMeta.color }} className="font-bold">
                                  {statusMeta.label}
                                </span>
                                {(res.review_tags || []).length > 0 && (
                                  <span className="truncate text-muted-foreground">
                                    {(res.review_tags || []).join("、")}
                                  </span>
                                )}
                              </div>
                              {res.review_note ? (
                                <div className="mt-1 text-[11px] leading-snug text-success">
                                  评审：{res.review_note}
                                </div>
                              ) : null}
                              {res.comment ? (
                                <div className="mt-1 text-[11px] leading-snug text-muted-foreground">
                                  💬 {res.comment}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
            </div>
          </>
        )}
      </section>

      {/* 放大 */}
      <ImageZoom url={zoom} onClose={() => setZoom(null)} />

      {/* 历史记录中的 2:1 ERP 球面浏览 */}
      <Dialog open={Boolean(panoView)} onOpenChange={(open) => { if (!open) setPanoView(null); }}>
        <DialogContent className="max-h-[96vh] max-w-[98vw] overflow-y-auto sm:max-w-[min(96vw,1200px)]">
          {panoView && (
            <>
              <div className="pr-10 text-sm font-bold">360° 球面全景 · {panoView.label}</div>
              <div className={cn(
                "rounded-lg px-3 py-2 text-xs font-bold",
                panoView.kind === "pure_render"
                  ? panoramaGateTone(panoView.gateStatus)
                  : panoView.gatePass
                    ? "bg-emerald-50 text-emerald-800"
                    : "bg-red-50 text-red-800",
              )}>
                {panoView.kind === "pure_render"
                  ? `视觉门禁：${panoramaGateLabel(panoView.gateStatus)} · AI 扩展、非几何锁定`
                  : `P0 门禁：${panoView.gatePass ? "通过" : "未通过"}`}
                {panoView.failures.length ? ` · ${panoView.failures.join(", ")}` : ""}
              </div>
              <PanoViewer erpUrl={panoView.url} mode="view"
                initialYawDeg={panoView.initialYawDeg}
              />
            </>
          )}
        </DialogContent>
      </Dialog>

      {panoramaFloor && active && (
        <PanoramaFloorDialog
          open
          onOpenChange={(open) => !open && setPanoramaFloor(null)}
          panoramaIndex={0}
          erpUrl={panoramaFloor.srcUrl}
          textureUrl={panoramaFloor.textureUrl}
          recordTarget={{
            json_path: active,
            record_id: panoramaFloor.recordId,
            result_id: panoramaFloor.resultId,
            texture_path: panoramaFloor.texturePath,
          }}
          onDone={() => void reload()}
        />
      )}

      {/* 生成式修补 */}
      {inpaint && active && (
        <InpaintDialog
          open={inpaint.open}
          onOpenChange={(o) => !o && setInpaint(null)}
          srcUrl={inpaint.srcUrl}
          target={{
            kind: "record",
            jsonPath: active,
            recordId: inpaint.recordId,
            resultId: inpaint.resultId,
          }}
          onDone={() => reload()}
        />
      )}

      {/* 真实纹理投影 */}
      {floorVisualize && active && (
        <FloorVisualizeDialog
          open={floorVisualize.open}
          onOpenChange={(o) => !o && setFloorVisualize(null)}
          srcUrl={floorVisualize.srcUrl}
          textureUrl={floorVisualize.textureUrl}
          texturePath={floorVisualize.texturePath}
          target={{
            kind: "record",
            jsonPath: active,
            recordId: floorVisualize.recordId,
            resultId: floorVisualize.resultId,
          }}
          onDone={() => reload()}
        />
      )}

      {/* 手动校色 */}
      {colorMatch && active && (
        <ColorMatchDialog
          open={colorMatch.open}
          onOpenChange={(o) => !o && setColorMatch(null)}
          srcUrl={colorMatch.srcUrl}
          imageRel={colorMatch.imageRel}
          refUrl={colorMatch.refUrl}
          refPath={colorMatch.refPath}
          target={{
            kind: "record",
            jsonPath: active,
            recordId: colorMatch.recordId,
            resultId: colorMatch.resultId,
          }}
          onDone={() => reload()}
        />
      )}

      {/* 前后对比 */}
      <Dialog open={!!compare} onOpenChange={(o) => !o && setCompare(null)}>
        <DialogContent className="max-w-[96vw] sm:max-w-[min(92vw,1100px)]">
          <div className="space-y-3">
            <div>
              <div className="text-[15px] font-bold">前后对比</div>
              <div className="mt-0.5 text-[12px] text-muted-foreground">
                拖动中缝滑块对比原图与效果图
              </div>
            </div>
            {compare && (
              <CompareSlider
                before={api.imgUrl(compare.before)}
                after={api.imgUrl(compare.after)}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* 解密 */}
      <Dialog
        open={reveal.open}
        onOpenChange={(o) => setReveal((s) => ({ ...s, open: o }))}
      >
        <DialogContent>
          <div className="space-y-3">
            <div className="text-[15px] font-bold">解密原始提示词</div>
            <Input
              type="password"
              value={reveal.pw}
              onChange={(e) => setReveal((s) => ({ ...s, pw: e.target.value }))}
              placeholder="输入密码"
              className="h-10 rounded-[10px] bg-panel"
            />
            <div className="flex justify-end">
              <button
                onClick={doReveal}
                className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover"
              >
                🔓 解密
              </button>
            </div>
            {reveal.text && (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-[10px] bg-accent p-3 text-xs text-foreground">
                {reveal.text}
              </pre>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* 人工评审标注 */}
      <Dialog
        open={review.open}
        onOpenChange={(o) => setReview((s) => ({ ...s, open: o }))}
      >
        <DialogContent>
          <div className="space-y-3">
            <div className="text-[15px] font-bold">人工评审标注</div>
            <div className="flex flex-wrap gap-2">
              {REVIEW_STATUS.map((s) => (
                <button
                  key={s.value}
                  onClick={() => setReview((v) => ({ ...v, status: s.value }))}
                  className={cn(
                    "h-8 rounded-lg border px-3 text-[12.5px] font-semibold",
                    review.status === s.value
                      ? "border-transparent text-white"
                      : "border-border bg-card text-secondary-foreground hover:bg-accent",
                  )}
                  style={review.status === s.value ? { background: s.color } : undefined}
                >
                  {s.label}
                </button>
              ))}
              <button
                onClick={() => setReview((v) => ({ ...v, best: !v.best }))}
                className={cn(
                  "h-8 rounded-lg border px-3 text-[12.5px] font-semibold",
                  review.best
                    ? "border-primary bg-primary text-white"
                    : "border-border bg-card text-secondary-foreground hover:bg-accent",
                )}
              >
                最佳图
              </button>
            </div>
            <div>
              <div className="mb-1.5 text-[12px] font-semibold text-muted-foreground">
                问题标签
              </div>
              <div className="flex flex-wrap gap-1.5">
                {REVIEW_TAGS.map((tag) => {
                  const on = review.tags.includes(tag);
                  return (
                    <button
                      key={tag}
                      onClick={() =>
                        setReview((v) => ({
                          ...v,
                          tags: on
                            ? v.tags.filter((x) => x !== tag)
                            : [...v.tags, tag],
                        }))
                      }
                      className={cn(
                        "rounded-full border px-3 py-[5px] text-[12px] font-semibold",
                        on
                          ? "border-primary bg-primary text-white"
                          : "border-border bg-card text-secondary-foreground hover:bg-accent",
                      )}
                    >
                      {tag}
                    </button>
                  );
                })}
              </div>
            </div>
            <textarea
              value={review.note}
              onChange={(e) => setReview((s) => ({ ...s, note: e.target.value }))}
              placeholder="人工备注，例如：颜色准但空间略假，适合做备选"
              className="min-h-[90px] w-full resize-none rounded-[10px] border border-border bg-panel px-3 py-2 text-[13px] outline-none focus:ring-2 focus:ring-primary/20"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setReview((s) => ({ ...s, open: false }))}
                className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
              >
                取消
              </button>
              <button
                onClick={doReviewSubmit}
                className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover"
              >
                保存
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 记录内二改 */}
      <Dialog
        open={edit.open}
        onOpenChange={(o) => setEdit((s) => ({ ...s, open: o }))}
      >
        <DialogContent>
          <div className="space-y-3">
            <div className="text-[15px] font-bold">二改（对这张结果图做图生图编辑）</div>
            <Input
              value={edit.instruction}
              onChange={(e) =>
                setEdit((s) => ({ ...s, instruction: e.target.value }))
              }
              placeholder="编辑指令，例如：把沙发换成米白色布艺"
              className="h-10 rounded-[10px] bg-panel"
            />
            <label className="flex cursor-pointer items-start gap-2 text-[12.5px]">
              <input
                type="checkbox"
                checked={edit.colorMatch}
                onChange={(e) =>
                  setEdit((s) => ({ ...s, colorMatch: e.target.checked }))
                }
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
                onClick={() => setEdit((s) => ({ ...s, open: false }))}
                className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
              >
                取消
              </button>
              <button
                onClick={doEditSubmit}
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
