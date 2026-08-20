"use client";

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import {
  Box,
  Camera,
  CheckCircle2,
  Cuboid,
  Eye,
  History,
  ImagePlus,
  LoaderCircle,
  LockKeyhole,
  Play,
  RefreshCw,
  ScanLine,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { toast } from "sonner";
import { WholeHomeStudio, type WholeHomeStudioHandle } from "@/components/WholeHomeStudio";
import PanoViewer from "@/components/PanoViewer";
import { WholeHomeHumanReview } from "@/components/WholeHomeHumanReview";
import { WholeHomeProfessionalProposal } from "@/components/WholeHomeProfessionalProposal";
import { GeometryAuditHistoryStrip, WholeHomeHistoryPanel } from "@/components/WholeHomeHistoryPanel";
import { CadSpaceDraftEditor } from "@/components/CadSpaceDraftEditor";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type {
  FloorplanAnalysis,
  FloorplanUpload,
  CadRuntimeStatus,
  CadUpload,
  WholeHomeCadReparseOperation,
  WholeHomeCadAiAdvisory,
  Swatch,
  WholeHomeAutoCameraPlan,
  WholeHomeCamera,
  WholeHomeCameraCandidate,
  WholeHomeCameraCandidateProposal,
  WholeHomeCameraRoomPool,
  WholeHomeCapture,
  WholeHomeCaptureGroup,
  WholeHomeGeometryManifest,
  WholeHomeModel,
  WholeHomeProject,
  WholeHomeLearningSummary,
  WholeHomeManualCapabilities,
  WholeHomeManualRunPreview,
  WholeHomePanoCapture,
  WholeHomePanoGate,
  WholeHomePanoPaidPreview,
  WholeHomeReviewState,
  WholeHomeReferenceContract,
  WholeHomeRun,
  WholeHomeRunReplay,
  WholeHomeSceneRecipe,
} from "@/lib/types";
import type { PanoChecklistResult } from "@/lib/wholeHomePano";
import { isWholeHomeGenerationLocked } from "@/lib/wholeHomeHumanReview";
import { newCadOperationId } from "@/lib/wholeHomeCadSpace";
import { WHOLE_HOME_SEMANTIC_COLORS } from "@/lib/wholeHomeRenderGate";
import {
  buildReferenceCaptureGate,
  buildReferencePreflightGate,
  cadFormatReadiness,
  canMutateWholeHomeGeometry,
  DEFAULT_JUSTEASY_REFERENCE_URL,
  materialModeGate,
  switchWholeHomeSource,
  type WholeHomeMaterialMode,
  type WholeHomeSourceMode,
} from "@/lib/wholeHomeCad";

type AspectRatio = "4:3" | "16:9" | "3:4" | "9:16";
const terminalProject = new Set(["needs_review", "done", "geometry_accepted", "verified", "failed"]);
const terminalRun = new Set(["done", "partial", "failed"]);
const inputClass = "h-9 rounded-lg border border-border bg-card px-3 text-sm outline-none focus:border-primary";

function UploadBox({
  label,
  hint,
  value,
  accept,
  busy,
  onFile,
}: {
  label: string;
  hint: string;
  value: Swatch | null;
  accept: string;
  busy: boolean;
  onFile: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <button type="button" onClick={() => input.current?.click()} className="group min-h-36 overflow-hidden rounded-xl border border-dashed border-border bg-card text-left transition hover:border-primary/60">
      <input ref={input} hidden type="file" accept={accept} onChange={(event) => { const file = event.target.files?.[0]; if (file) onFile(file); event.currentTarget.value = ""; }} />
      {value?.url ? (
        <div className="flex h-36 items-center gap-3 p-3">
          <img src={api.imgUrl(value.thumb || value.url)} alt={label} className="h-full w-36 rounded-lg object-cover" />
          <div className="min-w-0"><b>{label}</b><div className="mt-1 truncate text-xs text-muted-foreground">{value.name}</div><div className="mt-2 text-[11px] text-primary">点击替换</div></div>
        </div>
      ) : (
        <div className="flex h-36 flex-col items-center justify-center p-4 text-center">
          {busy ? <LoaderCircle className="animate-spin text-primary" /> : <UploadCloud className="text-primary" />}
          <b className="mt-2 text-sm">{label}</b><span className="mt-1 text-xs text-muted-foreground">{hint}</span>
        </div>
      )}
    </button>
  );
}

type RasterMeasureAnchor = {
  points: [number, number][];
  length: string;
};

function RasterScaleRegistration({
  floorplanUrl,
  busy,
  onRegister,
}: {
  floorplanUrl: string;
  busy: boolean;
  onRegister: (
    anchors: Array<{ id: string; start_px: [number, number]; end_px: [number, number]; length_m: number }>,
    origin: [number, number],
  ) => Promise<void>;
}) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [naturalSize, setNaturalSize] = useState<[number, number]>([1, 1]);
  const [anchors, setAnchors] = useState<RasterMeasureAnchor[]>([
    { points: [], length: "" }, { points: [], length: "" },
  ]);
  const [origin, setOrigin] = useState<[number, number] | null>(null);
  const [active, setActive] = useState<"origin" | 0 | 1>(0);

  function pickPoint(event: MouseEvent<HTMLImageElement>) {
    const image = imageRef.current;
    if (!image) return;
    const bounds = image.getBoundingClientRect();
    const point: [number, number] = [
      Math.max(0, Math.min(image.naturalWidth - 1, (event.clientX - bounds.left) * image.naturalWidth / bounds.width)),
      Math.max(0, Math.min(image.naturalHeight - 1, (event.clientY - bounds.top) * image.naturalHeight / bounds.height)),
    ];
    if (active === "origin") {
      setOrigin(point);
      return;
    }
    setAnchors((rows) => rows.map((row, index) => index === active
      ? { ...row, points: row.points.length >= 2 ? [point] : [...row.points, point] }
      : row));
  }

  const ready = Boolean(origin) && anchors.every((row) => (
    row.points.length === 2 && Number(row.length) > 0
  ));
  const colors = ["#2563eb", "#7c3aed"];
  return (
    <div className="mt-3 rounded-xl border border-amber-300 bg-white/70 p-3 text-xs text-foreground dark:bg-black/20">
      <div className="font-bold">普通户型图米制配准</div>
      <div className="mt-1 text-muted-foreground">依次在原图上点两条已知尺寸线的两个端点，输入真实米数，再点模型坐标 (0,0) 在图上的位置。两条尺寸相差超过 2% 会被服务端拒绝。</div>
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_280px] gap-3 max-[850px]:grid-cols-1">
        <div className="relative overflow-hidden rounded-lg border border-border bg-white">
          <img
            ref={imageRef}
            src={floorplanUrl}
            alt="待配准户型原图"
            className="block h-auto w-full cursor-crosshair select-none"
            draggable={false}
            onLoad={(event) => setNaturalSize([
              event.currentTarget.naturalWidth || 1, event.currentTarget.naturalHeight || 1,
            ])}
            onClick={pickPoint}
          />
          <svg className="pointer-events-none absolute inset-0 size-full" viewBox={`0 0 ${naturalSize[0]} ${naturalSize[1]}`} preserveAspectRatio="none">
            {anchors.map((row, index) => row.points.length === 2 ? (
              <line key={`line-${index}`} x1={row.points[0][0]} y1={row.points[0][1]} x2={row.points[1][0]} y2={row.points[1][1]} stroke={colors[index]} strokeWidth={Math.max(3, naturalSize[0] / 350)} />
            ) : null)}
            {anchors.flatMap((row, index) => row.points.map((point, pointIndex) => (
              <circle key={`${index}-${pointIndex}`} cx={point[0]} cy={point[1]} r={Math.max(5, naturalSize[0] / 180)} fill={colors[index]} stroke="white" strokeWidth={2} />
            )))}
            {origin && <g><circle cx={origin[0]} cy={origin[1]} r={Math.max(7, naturalSize[0] / 150)} fill="#dc2626" stroke="white" strokeWidth={2} /><path d={`M ${origin[0] - 12} ${origin[1]} H ${origin[0] + 12} M ${origin[0]} ${origin[1] - 12} V ${origin[1] + 12}`} stroke="white" strokeWidth={2} /></g>}
          </svg>
        </div>
        <div className="space-y-2">
          {anchors.map((row, index) => (
            <div key={index} className={`rounded-lg border p-2 ${active === index ? "border-primary bg-primary/5" : "border-border"}`}>
              <button type="button" className="w-full text-left font-bold" onClick={() => setActive(index as 0 | 1)}>尺寸线 {index + 1} · {row.points.length}/2 点</button>
              <div className="mt-2 flex gap-2">
                <Input type="number" min="0.01" step="0.01" value={row.length} placeholder="真实长度（米）" onChange={(event) => setAnchors((rows) => rows.map((item, rowIndex) => rowIndex === index ? { ...item, length: event.target.value } : item))} />
                <Button type="button" size="sm" variant="outline" onClick={() => setAnchors((rows) => rows.map((item, rowIndex) => rowIndex === index ? { ...item, points: [] } : item))}>重画</Button>
              </div>
            </div>
          ))}
          <button type="button" className={`w-full rounded-lg border p-2 text-left ${active === "origin" ? "border-red-500 bg-red-50 dark:bg-red-950/20" : "border-border"}`} onClick={() => setActive("origin")}>
            <b>模型原点 (0,0)</b><div className="mt-1 text-muted-foreground">通常点户型有效区域的左上角；当前：{origin ? `${origin[0].toFixed(1)}, ${origin[1].toFixed(1)} px` : "未选择"}</div>
          </button>
          <Button className="w-full" disabled={!ready || busy} onClick={() => onRegister(
            anchors.map((row, index) => ({
              id: `dimension-${index + 1}`,
              start_px: row.points[0], end_px: row.points[1], length_m: Number(row.length),
            })),
            origin as [number, number],
          )}>
            {busy ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}保存配准并量测墙线
          </Button>
        </div>
      </div>
    </div>
  );
}

function bytesLabel(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "—";
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MiB`;
  return `${Math.ceil(value / 1024)} KiB`;
}

function CadUploadBox({ value, busy, onFile }: {
  value: CadUpload | null;
  busy: boolean;
  onFile: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <button type="button" onClick={() => input.current?.click()} className="min-h-36 rounded-xl border border-dashed border-border bg-card p-4 text-left transition hover:border-primary/60">
      <input ref={input} hidden type="file" accept=".dwg,.dxf" onChange={(event) => { const file = event.target.files?.[0]; if (file) onFile(file); event.currentTarget.value = ""; }} />
      <div className="flex h-full items-center gap-3">
        {busy ? <LoaderCircle className="animate-spin text-primary" /> : <Cuboid className="text-primary" />}
        {value ? <div className="min-w-0">
          <b className="block truncate">{value.name}</b>
          <div className="mt-1 text-xs text-muted-foreground">{value.format.toUpperCase()} · {value.version || value.encoding || "未知版本"} · {bytesLabel(value.size_bytes)}</div>
          <div className="mt-2 break-all font-mono text-[10px] text-muted-foreground">SHA256 {value.sha256}</div>
          <div className="mt-2 text-[11px] text-primary">点击替换 CAD 文件</div>
        </div> : <div><b>上传权威 CAD</b><div className="mt-1 text-xs text-muted-foreground">DWG / DXF，最大 100 MiB；先本地硬门禁，失败时不会调用 AI</div></div>}
      </div>
    </button>
  );
}

function CadRuntimePanel({ status, error }: { status: CadRuntimeStatus | null; error: string }) {
  const usesMitReader = status?.converter_adapter === "acadsharp_mit_v1";
  const rows = [
    ["DXF 解析器", status?.ready_for_dxf, status ? `ezdxf ${status.ezdxf_version || "—"} · Shapely ${status.shapely_version || "—"}` : "读取中"],
    ["DWG 本地读取器", status?.converter_available, status?.converter_available ? `${status.converter_adapter || "已检测"}${status.converter_license ? ` · ${status.converter_license}` : ""}` : "未检测到"],
    ["商业使用许可", status?.commercial_use_authorized, status?.commercial_use_authorized ? (usesMitReader ? "ACadSharp MIT 许可" : "ODA 授权已显式确认") : "未声明，不能处理 DWG"],
  ] as const;
  return <div className="rounded-xl border border-border bg-card p-3">
    <div className="mb-2 flex items-center justify-between gap-2"><b className="text-sm">本机 CAD 运行诊断</b><span className="text-[10px] text-muted-foreground">检测与授权彼此独立</span></div>
    <div className="grid grid-cols-3 gap-2 max-[760px]:grid-cols-1">{rows.map(([label, ok, detail]) => <div key={label} className="rounded-lg border border-border p-2 text-xs"><div className="flex items-center justify-between gap-2"><b>{label}</b><span className={ok ? "text-emerald-700" : "text-amber-700"}>{ok ? "就绪" : "未就绪"}</span></div><div className="mt-1 text-[11px] text-muted-foreground">{detail}</div></div>)}</div>
    {error && <div className="mt-2 text-xs text-red-700">诊断读取失败：{error}</div>}
    {status && !status.commercial_use_authorized && <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-800">检测到程序文件不等于拥有商业授权。未声明授权时，DWG 必须使用已获授权的 CAD 软件导出 DXF 后再上传；前端不会伪装为 ready。</div>}
  </div>;
}

function CadDiagnostics({ project, operation, onReparse, reparsing, advisory, aiReviewing, onAiReview }: {
  project: WholeHomeProject;
  operation: WholeHomeCadReparseOperation | null;
  onReparse: (candidateId?: string) => void;
  reparsing: boolean;
  advisory: WholeHomeCadAiAdvisory | null;
  aiReviewing: boolean;
  onAiReview: () => void;
}) {
  const report = project.parse_report;
  const candidates = report?.candidate_plans || [];
  const reportHardErrors = report?.hard_errors || report?.hard_error_summary || [];
  const reportWarnings = report?.warnings || report?.warning_summary || [];
  const issues = [...reportHardErrors, ...reportWarnings];
  const roleSummary = report?.selected_entity_role_summary;
  const openingSummary = report?.raw_opening_summary;
  const globalTopology = report?.global_wall_topology;
  const cadError = project.cad_error;
  const summary = project.cad_reparse_summary;
  const operationStatus = String(operation?.status || "").toLowerCase();
  const operationSucceeded = ["succeeded", "success", "done", "completed"].includes(operationStatus);
  const operationNeedsReview = operationStatus === "needs_review";
  const operationFailed = ["failed", "conflict", "interrupted"].includes(operationStatus);
  const summaryStatus = String(summary?.last_status || summary?.status || "").toLowerCase();
  const successfulCandidateId = operationSucceeded
    ? operation?.candidate_id
    : ["succeeded", "success", "done", "completed"].includes(summaryStatus) ? summary?.last_candidate_id : undefined;
  const recentCandidateId = operation?.candidate_id || summary?.last_candidate_id;
  const lastFailure = summary?.last_failure;
  const hasLastFailure = Boolean(lastFailure && (
    typeof lastFailure !== "object" || Object.keys(lastFailure).length > 0
  ));
  const permanentFailure = hasLastFailure ? lastFailure : summary?.last_error || cadError;
  const failureMessage = permanentFailure && typeof permanentFailure === "object"
    ? String(permanentFailure.message || permanentFailure.error || JSON.stringify(permanentFailure))
    : String(permanentFailure || "");
  const failureCode = permanentFailure && typeof permanentFailure === "object"
    ? String(permanentFailure.code || permanentFailure.error_code || "cad_parse_failed")
    : "cad_parse_failed";
  const draftBlocked = project.status === "needs_review";
  return <section className="rounded-xl border border-border bg-panel p-4">
    <div className="flex flex-wrap items-center gap-2"><ScanLine size={18} className="text-primary" /><h2 className="font-extrabold">CAD 本地解析与硬门禁</h2><span className={`rounded-full px-2 py-1 text-xs font-bold ${project.status === "done" || project.status === "verified" ? "bg-emerald-100 text-emerald-700" : project.status === "failed" ? "bg-red-100 text-red-700" : draftBlocked ? "bg-amber-100 text-amber-900" : "bg-sky-100 text-sky-700"}`}>{draftBlocked ? "3D 草稿待复核" : project.status}</span><span className="text-[11px] font-bold text-emerald-700">本地解析阶段零 AI / 零生图</span></div>
    {project.stage && <div className="mt-2 text-xs text-muted-foreground">{project.stage}</div>}
    {operation && <div className={`mt-3 rounded-lg border p-3 text-xs ${operationFailed ? "border-red-200 bg-red-50 text-red-800" : operationNeedsReview ? "border-amber-200 bg-amber-50 text-amber-900" : operationSucceeded ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-sky-200 bg-sky-50 text-sky-800"}`}>
      <div className="flex items-center gap-2">{!operationSucceeded && !operationFailed && !operationNeedsReview && <LoaderCircle size={14} className="animate-spin" />}<b>重解析任务 {operation.operation_id}</b><span className="ml-auto">{operationNeedsReview ? "草稿已更新 · 待复核" : operation.status}</span></div>
      <div className="mt-1 flex items-center gap-2"><span>{operation.stage || "等待本地 CAD worker"}</span>{Number.isFinite(operation.progress) && <><div className="h-1.5 min-w-28 flex-1 overflow-hidden rounded bg-sky-100"><div className="h-full bg-sky-600 transition-[width]" style={{ width: `${Math.max(0, Math.min(100, Number(operation.progress) <= 1 ? Number(operation.progress) * 100 : Number(operation.progress)))}%` }} /></div><span>{Math.round(Number(operation.progress) <= 1 ? Number(operation.progress) * 100 : Number(operation.progress))}%</span></>}</div>
    </div>}
    {(project.error || failureMessage) && <div className={`mt-3 rounded-lg border p-3 text-xs ${draftBlocked ? "border-amber-200 bg-amber-50 text-amber-900" : "border-red-200 bg-red-50 text-red-800"}`}><b>{draftBlocked ? "CAD 硬门禁常驻摘要" : "CAD 失败常驻摘要"} · {failureCode}</b>{summary?.failure_count != null && <span className="ml-2">累计 {summary.failure_count} 次</span>}<div className="mt-1">{failureMessage || project.error}</div><div className="mt-1 text-[11px]">{draftBlocked ? "3D 草稿与诊断已保存在项目历史中，可继续检查和修正；硬门禁解除前仍禁止锁模与生图。" : "即使后续候选成功，此摘要仍保留最近失败证据，不会被成功状态覆盖。"}</div></div>}
    {report && <>
      <div className="mt-3 grid grid-cols-5 gap-2 max-[900px]:grid-cols-2">
        <div className="rounded-lg bg-card p-2 text-xs"><span className="text-muted-foreground">$INSUNITS</span><div className="font-bold">{report.insunits ?? "—"}</div></div>
        <div className="rounded-lg bg-card p-2 text-xs"><span className="text-muted-foreground">米/单位</span><div className="font-bold">{report.unit_scale_to_m ?? "—"}</div></div>
        <div className="rounded-lg bg-card p-2 text-xs"><span className="text-muted-foreground">结构实体</span><div className="font-bold">{report.selected_structural_entity_count ?? report.structural_entity_count ?? "—"}</div></div>
        <div className="rounded-lg bg-card p-2 text-xs"><span className="text-muted-foreground">图层 / 块</span><div className="font-bold">{report.layer_count ?? Object.keys(report.layers || {}).length} / {report.block_count ?? Object.keys(report.blocks || {}).length}</div></div>
        <div className="rounded-lg bg-card p-2 text-xs"><span className="text-muted-foreground">忽略非结构</span><div className="font-bold">{report.ignored_nonstructural_count ?? "—"}</div></div>
      </div>
      {globalTopology && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3" data-testid="cad-global-wall-topology-summary">
        <div className="flex flex-wrap items-center justify-between gap-2"><b className="text-xs text-emerald-900">整屋全局墙体拓扑</b><span className="text-[10px] text-emerald-800">{globalTopology.method} · 结构几何只来自筛选后的 CAD 墙线</span></div>
        <div className="mt-2 grid grid-cols-5 gap-2 text-xs max-[900px]:grid-cols-2">
          {[
            ["源结构线", globalTopology.source_segment_count],
            ["源线覆盖", globalTopology.source_coverage_ratio != null ? `${(globalTopology.source_coverage_ratio * 100).toFixed(1)}%` : "—"],
            ["连通墙壳", globalTopology.wall_component_count],
            ["墙体面积", globalTopology.wall_area_m2 != null ? `${globalTopology.wall_area_m2.toFixed(2)}㎡` : "—"],
            ["物理空间候选", globalTopology.space_candidate_count],
          ].map(([label, value]) => <div key={String(label)} className="rounded-lg bg-white/80 p-2"><span className="text-emerald-700">{label}</span><div className="mt-0.5 text-base font-extrabold text-emerald-950">{value ?? "—"}</div></div>)}
        </div>
      </div>}
      {roleSummary && <div className="mt-3 rounded-xl border border-border bg-card p-3" data-testid="cad-entity-role-summary">
        <div className="flex flex-wrap items-center justify-between gap-2"><b className="text-xs">CAD 实体角色分解</b><span className="text-[10px] text-muted-foreground">{roleSummary.method || "cad_geometry_role_decomposition"} · 不按单一图层名直接建墙</span></div>
        <div className="mt-2 grid grid-cols-5 gap-2 text-xs max-[900px]:grid-cols-2">
          {[
            ["候选输入", roleSummary.input_entity_count],
            ["保留墙体", roleSummary.retained_wall_entity_count],
            ["开口证据", roleSummary.opening_evidence_entity_count],
            ["家具/上下文", roleSummary.context_entity_count],
            ["待复核", roleSummary.review_entity_count],
          ].map(([label, value]) => <div key={String(label)} className="rounded-lg bg-muted/60 p-2"><span className="text-muted-foreground">{label}</span><div className="mt-0.5 text-base font-extrabold">{value ?? "—"}</div></div>)}
        </div>
        {roleSummary.reason_counts && Object.keys(roleSummary.reason_counts).length > 0 && <details className="mt-2 rounded-lg border border-border px-3 py-2 text-[11px]"><summary className="cursor-pointer font-bold">查看分类理由统计</summary><div className="mt-2 flex flex-wrap gap-1">{Object.entries(roleSummary.reason_counts).sort((left, right) => right[1] - left[1]).map(([reason, count]) => <span key={reason} className="rounded-full bg-muted px-2 py-1">{reason} · {count}</span>)}</div></details>}
      </div>}
      {openingSummary && <div className="mt-3 rounded-xl border border-border bg-card p-3" data-testid="cad-raw-opening-summary">
        <div className="flex flex-wrap items-center gap-2"><b className="text-xs">原始几何门窗识别</b><span className="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-bold text-sky-800">候选 {openingSummary.candidate_count ?? 0}</span><span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">自动确认 {openingSummary.accepted_count ?? 0}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${(openingSummary.review_count || 0) > 0 ? "bg-amber-100 text-amber-900" : "bg-muted text-muted-foreground"}`}>待复核 {openingSummary.review_count ?? 0}</span><span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">排除 {openingSummary.rejected_count ?? 0}</span></div>
        <div className="mt-1 text-[11px] text-muted-foreground">门窗由门扇圆弧、墙体缺口、窗框平行线和宿主墙共同证明；没有足够证据时不会猜造洞口。</div>
      </div>}
      {report.selection_explanation && <div className="mt-3 rounded-lg bg-muted/60 px-3 py-2 text-xs"><b>候选选择依据：</b>{report.selection_explanation}</div>}
      {candidates.length > 0 && <div className="mt-3 grid grid-cols-3 gap-3 max-[900px]:grid-cols-1">{candidates.map((candidate) => <article key={candidate.candidate_id} className={`overflow-hidden rounded-xl border bg-card ${candidate.candidate_id === report.selected_candidate_id ? "border-primary" : candidate.candidate_id === recentCandidateId ? "border-sky-400" : candidate.candidate_id === successfulCandidateId ? "border-emerald-400" : "border-border"}`}>
        {candidate.preview_url ? <a href={api.imgUrl(candidate.preview_url)} target="_blank" rel="noreferrer"><img src={api.imgUrl(candidate.preview_url)} alt={candidate.candidate_id} className="aspect-[4/3] w-full object-contain" /></a> : <div className="flex aspect-[4/3] items-center justify-center bg-muted text-xs text-muted-foreground">预览 URL 不可用</div>}
        <div className="p-2 text-[11px]"><div className="flex justify-between gap-2"><b>{candidate.candidate_id}</b><div className="flex flex-wrap justify-end gap-1">{candidate.candidate_id === report.selected_candidate_id && <span className="rounded-full bg-primary/10 px-1.5 py-0.5 font-bold text-primary">当前选中</span>}{candidate.candidate_id === recentCandidateId && <span className="rounded-full bg-sky-100 px-1.5 py-0.5 font-bold text-sky-700">最近尝试</span>}{candidate.candidate_id === successfulCandidateId && <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 font-bold text-emerald-700">最近成功</span>}{candidate.candidate_id !== report.selected_candidate_id && candidate.candidate_id !== recentCandidateId && candidate.candidate_id !== successfulCandidateId && <span>诊断候选</span>}</div></div><div className="mt-1 text-muted-foreground">闭合区 {candidate.closed_region_count} · 家具/门窗块 {candidate.context_insert_count ?? "—"} · 综合分 {candidate.selection_score ?? candidate.score}</div><Button className="mt-2 w-full" size="sm" variant={candidate.candidate_id === report.selected_candidate_id ? "secondary" : "outline"} disabled={reparsing} onClick={() => onReparse(candidate.candidate_id)}>选择此平面并重新解析</Button></div>
      </article>)}</div>}
      {issues.length > 0 && <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900"><b>解析证据：{issues.length} 项</b>{issues.slice(0, 20).map((issue, index) => <div key={`${String(issue.code)}-${index}`} className="mt-1">• {String(issue.code || "cad_issue")}：{String(issue.message || JSON.stringify(issue))}</div>)}</div>}
      {report.report_url && <a className="mt-3 inline-block text-xs text-primary underline" href={api.imgUrl(report.report_url)} target="_blank" rel="noreferrer">打开 CAD 诊断摘要</a>}
    </>}
    <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50 p-3" data-testid="cad-gemini-advisory-panel">
      <div className="flex flex-wrap items-center gap-2"><Sparkles size={16} className="text-violet-700" /><b className="text-xs text-violet-950">Gemini 辅助复核（只提建议）</b><span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-bold text-violet-800">永不直接改墙 / 门窗 / 尺寸</span><Button className="ml-auto" size="sm" variant="outline" data-testid="cad-gemini-advisory-button" disabled={aiReviewing || project.model?.coordinate_contract_version !== 2} onClick={onAiReview}>{aiReviewing ? <LoaderCircle className="animate-spin" /> : <Sparkles />}付费复核一次</Button></div>
      <div className="mt-1 text-[11px] text-violet-900/75">Gemini 只引用现有 evidence_id / candidate_id 给出家具误判、开口和房间标签建议；最终几何仍由确定性算法与人工确认决定。</div>
      {project.model?.coordinate_contract_version !== 2 && <div className="mt-2 text-[11px] font-bold text-amber-800">旧坐标项目必须先重新解析为 V2，禁止拿镜像俯视图交给 AI 审核。</div>}
      {advisory && <details className="mt-2 rounded-lg bg-white/80 px-3 py-2 text-[11px]" open><summary className="cursor-pointer font-bold">最近建议 · {advisory.advisory_id} · {advisory.call_count} 次调用 · geometry_mutated=false</summary><div className="mt-2">{String(advisory.proposal?.summary || "Gemini 未提供摘要")}</div><div className="mt-1 text-muted-foreground">墙角色建议 {advisory.proposal?.wall_role_reviews?.length || 0} · 开口建议 {advisory.proposal?.opening_reviews?.length || 0} · 房间标签建议 {advisory.proposal?.room_label_proposals?.length || 0} · 引用校验 {advisory.reference_validation.status}</div></details>}
    </div>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3"><span className="text-[11px] text-muted-foreground">多张平面并排时，选择会记录 candidate_id 和全部原始证据；不删除其他候选。重解析只运行本地 CAD 管线。</span><Button size="sm" variant="outline" disabled={reparsing} onClick={() => onReparse()}>{reparsing ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}重新自动评分</Button></div>
  </section>;
}

function ReferenceContractPanel({ contract, gate }: {
  contract: WholeHomeReferenceContract | undefined;
  gate: ReturnType<typeof buildReferenceCaptureGate>;
}) {
  if (!contract?.contract_id) return <section className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"><b>未匹配已审计 reference contract</b><div className="mt-1 text-xs">参考模式保持禁用。请使用项目指定的 Justeasy 官方链接重新创建 CAD 项目。</div></section>;
  return <section className="rounded-xl border border-border bg-panel p-4">
    <div className="flex flex-wrap items-center gap-2"><Eye size={18} className="text-primary" /><h2 className="font-extrabold">参考合同 · {contract.title}</h2><span className="rounded-full bg-emerald-100 px-2 py-1 text-[11px] font-bold text-emerald-800">geometry authority: {contract.geometry_authority}</span><span className="rounded-full bg-card px-2 py-1 text-[11px]">静态 {contract.output.aspect_ratio} · {contract.output.resolution} · 非全景</span></div>
    <p className="mt-2 text-xs text-muted-foreground">参考链接只约束设计语言与构图；墙、门窗、固定物、尺度和房间身份始终以 CAD 为准。</p>
    <div className="mt-3 grid grid-cols-3 gap-2 max-[900px]:grid-cols-1">{contract.slots.map((slot, index) => {
      const missing = gate.missingSlotIds.includes(slot.slot_id);
      const assetMissing = gate.missingAssetSlotIds.includes(slot.slot_id);
      const viewpointMissing = gate.missingViewpointSlotIds.includes(slot.slot_id);
      return <details key={slot.slot_id} className={`rounded-lg border p-3 text-xs ${missing || assetMissing || viewpointMissing ? "border-amber-300 bg-amber-50" : "border-emerald-300 bg-emerald-50"}`}>
        <summary className="cursor-pointer font-bold">{index + 1}. {slot.slot_id} · {assetMissing ? "视觉参考资产待审计绑定" : viewpointMissing ? "CAD 相对落点策略待生成" : missing ? "reference_slot_camera_missing" : "资产、节点与机位证据完整"}</summary>
        <div className="mt-2 space-y-1 text-[11px] text-muted-foreground"><div>房型：{slot.room_profile} · 焦距 {slot.focal_length_mm.min}–{slot.focal_length_mm.max}mm</div><div>视觉资产：{assetMissing ? "待审计绑定" : `已解析 · hash ${slot.reference_asset_hash || slot.reference_asset?.sha256 || slot.reference_asset?.hash}`}</div><div>参考节点：{slot.reference_viewpoint ? `${slot.reference_viewpoint.scene_name || slot.reference_viewpoint.name || "未命名"} / ${slot.reference_viewpoint.scene_id}` : "待绑定"}</div><div>绝对点位：公开页未提供（is_showmap=0）；不虚构 x/z/yaw/FOV</div><div>落点策略：{slot.reference_viewpoint?.landing_policy?.mode === "cad_semantic_relative_region" ? "按 CAD 房间语义 + must_show 锚点推断相对合法区域；角度可在同一区域优化" : "待生成 CAD 语义相对落点策略"}</div>{slot.reference_viewpoint?.evidence && <div className="break-all">公开页证据：{typeof slot.reference_viewpoint.evidence === "string" ? slot.reference_viewpoint.evidence : JSON.stringify(slot.reference_viewpoint.evidence)}</div>}<div>必须入镜：{slot.must_show.join("、")}</div>{slot.hard_constraints.map((value) => <div key={value}>• {value}</div>)}</div>
      </details>;
    })}</div>
    <div className={`mt-3 rounded-lg px-3 py-2 text-xs font-bold ${gate.ready ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>{gate.message}</div>
  </section>;
}

function modelLabel(key: "b2" | "pro") {
  return key === "b2" ? "Nano Banana 2（B2）" : "Nano Banana Pro";
}

function scoreTone(value?: number) {
  if (value == null) return "bg-muted text-muted-foreground";
  if (value >= 85) return "bg-emerald-100 text-emerald-700";
  if (value >= 70) return "bg-amber-100 text-amber-800";
  return "bg-red-100 text-red-700";
}

function Results({ run, project, onRetryQa, retryingQa, reviewLocked, manualSafe }: { run: WholeHomeRun; project: WholeHomeProject | null; onRetryQa: () => void; retryingQa: boolean; reviewLocked: boolean; manualSafe: boolean }) {
  const captureMap = new Map((project?.captures || []).map((capture) => [capture.capture_id, capture]));
  const unavailableQa = run.results.filter((result) => result.evaluation?.status === "unavailable").length;
  return (
    <section className="rounded-xl border border-border bg-panel p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <ImagePlus size={18} className="text-primary" /><h2 className="font-extrabold">生成结果与自动结构验收</h2>
        <span className="rounded-full bg-card px-2 py-1 text-xs">{run.status}</span>
        <span className="rounded-full bg-card px-2 py-1 text-xs">自动门禁放行 {run.summary_counts?.deliverable ?? run.results.filter((result) => result.deliverable).length}/{run.results.length}</span>
        <span className="text-[11px] text-muted-foreground">实际生图 {run.actual_generation_calls ?? 0} · 本地门禁 {run.actual_local_gate_calls ?? 0} · Gemini QA {run.actual_qa_calls ?? 0}</span>
        {run.stage && <span className="text-xs text-muted-foreground">{run.stage}</span>}
        {!manualSafe && unavailableQa > 0 && terminalRun.has(run.status) && <Button className="ml-auto" size="sm" variant="outline" disabled={retryingQa || reviewLocked} title={reviewLocked ? "请先完成人工评审并明确放行本轮" : undefined} onClick={onRetryQa}>{retryingQa ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}补评 {unavailableQa} 个网络失败 QA</Button>}
      </div>
      {run.error && <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{run.error}</div>}
      <div className="grid grid-cols-2 gap-4 max-[920px]:grid-cols-1">
        {run.results.map((result) => {
          const capture = captureMap.get(result.capture_id);
          const qa = result.evaluation;
          const attempts = result.attempts || [];
          const latestAttempt = attempts[attempts.length - 1];
          const latestMaterial = latestAttempt?.material_attempts?.[latestAttempt.material_attempts.length - 1];
          const displayUrl = result.url || latestMaterial?.final_url || latestAttempt?.structure_url || "";
          return (
            <article key={result.result_id} className={`overflow-hidden rounded-xl border bg-card ${result.deliverable ? "border-emerald-400" : qa?.hard_fail ? "border-red-400" : "border-border"}`}>
              <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 text-xs">
                <b>{result.camera_name} · {modelLabel(result.model_key)} · 候选 {result.candidate_index}</b>
                <div className="flex items-center gap-2"><span className={result.deliverable ? "font-bold text-emerald-700" : result.status === "failed" ? "text-red-600" : "text-amber-700"}>{result.deliverable ? "自动建议可交付" : result.outcome || result.status}</span><span className="text-muted-foreground">{attempts.length} 次结构尝试</span></div>
              </div>
              {displayUrl ? (
                <div className="grid grid-cols-[120px_1fr] bg-[#eeeae2] max-[580px]:grid-cols-1">
                  <div className="border-r border-border p-2 max-[580px]:border-b max-[580px]:border-r-0">
                    <div className="mb-1 text-[10px] font-bold text-muted-foreground">3D 几何基准</div>
                    {capture?.rgb_url && <img src={api.imgUrl(capture.rgb_url)} alt="灰模基准" className="w-full rounded object-cover" />}
                  </div>
                  <a href={api.imgUrl(displayUrl)} target="_blank" rel="noreferrer"><img src={api.imgUrl(displayUrl)} alt="整屋生成结果或最后保留的中间图" className="block w-full" /></a>
                </div>
              ) : (
                <div className="flex aspect-[4/3] items-center justify-center bg-muted text-sm text-muted-foreground">
                  {result.status === "failed" ? result.error : <><LoaderCircle className="mr-2 animate-spin" />{result.stage || "等待生成"}</>}
                </div>
              )}
              {attempts.length > 0 && (
                <details className="mx-3 mt-3 rounded-lg border border-border bg-muted/30 p-2 text-[11px]">
                  <summary className="cursor-pointer font-bold">结构门禁与回退轨迹 · {attempts.length} 次</summary>
                  <div className="mt-2 space-y-2">
                    {attempts.map((attempt) => (
                      <div key={attempt.attempt_id} className="rounded border border-border bg-card p-2">
                        <div className="flex flex-wrap gap-2"><b>#{attempt.attempt_index} {attempt.trigger}</b><span>{attempt.camera_name}</span><span className={attempt.status === "accepted" || attempt.status === "structure_accepted" ? "text-emerald-700" : "text-amber-700"}>{attempt.status}</span></div>
                        <div className="mt-1 text-muted-foreground">本地结构对齐：{attempt.structure_local_gate?.gate_pass ? "通过" : attempt.structure_local_gate ? "未通过" : "旧记录无数据"} · Gemini 结构 QA：{attempt.structure_evaluation?.gate_pass ? "通过" : attempt.structure_evaluation?.status === "unavailable" ? "不可用，已阻断" : attempt.structure_evaluation ? "未通过" : "未调用"} · QA {attempt.structure_qa_attempts?.length || 0} 次 · 地板 {attempt.material_attempts?.length || 0} 次</div>
                        {attempt.structure_local_gate && <div className="mt-1 text-muted-foreground">本地指标：semantic coverage@12 {attempt.structure_local_gate.semantic_coverage_12 ?? "—"} / mean {attempt.structure_local_gate.semantic_mean_distance ?? "—"} · normal coverage@12 {attempt.structure_local_gate.normal_coverage_12 ?? "—"}{attempt.structure_local_gate.overlay_url && <> · <a className="text-primary underline" href={api.imgUrl(attempt.structure_local_gate.overlay_url)} target="_blank" rel="noreferrer">红/青对齐证据</a></>}</div>}
                        {attempt.structure_evaluation?.checks?.filter((check) => check.status !== "pass").slice(0, 2).map((check) => <div key={check.constraint_id || check.constraint} className="mt-1 text-amber-800">• {check.constraint_id} {check.constraint}：{check.evidence}</div>)}
                        {attempt.material_attempts?.map((materialAttempt) => <div key={materialAttempt.material_attempt_id} className="mt-1 border-t border-border pt-1 text-muted-foreground">地板 #{materialAttempt.attempt_index} · 本地最终几何 {materialAttempt.final_local_gate?.gate_pass ? "通过" : materialAttempt.final_local_gate ? "未通过" : "旧记录无数据"} · Gemini QA {materialAttempt.evaluation?.gate_pass ? "通过" : materialAttempt.evaluation ? "未通过" : "未调用"}{materialAttempt.final_local_gate && <> · coverage@12 {materialAttempt.final_local_gate.structure_coverage_12 ?? "—"} / mean {materialAttempt.final_local_gate.structure_mean_distance ?? "—"}</>}{materialAttempt.final_local_gate?.overlay_url && <> · <a className="text-primary underline" href={api.imgUrl(materialAttempt.final_local_gate.overlay_url)} target="_blank" rel="noreferrer">红/青对齐证据</a></>}</div>)}
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {qa && (
                <div className="space-y-2 p-3">
                  <div className="flex flex-wrap gap-1 text-[11px]">
                    {[['总分', qa.total ?? undefined], ['几何', qa.geometry_score], ['机位', qa.camera_score], ['门窗', qa.opening_score], ['地板', qa.material_score]].map(([label, value]) => (
                      <span key={String(label)} className={`rounded-full px-2 py-1 ${scoreTone(value as number | undefined)}`}>{label} {value ?? "—"}</span>
                    ))}
                    {qa.hard_fail && <span className="rounded-full bg-red-600 px-2 py-1 font-bold text-white">最终硬门禁未通过</span>}
                    {qa.gate_pass && <span className="rounded-full bg-emerald-600 px-2 py-1 font-bold text-white">最终门禁通过</span>}
                  </div>
                  <div className="text-xs text-muted-foreground">{qa.summary}</div>
                  {qa.checks.filter((check) => check.status !== "pass").slice(0, 4).map((check, index) => <div key={index} className="text-[11px] text-amber-800">• {check.constraint}：{check.evidence}</div>)}
                  {Boolean(result.qa_history?.length) && (
                    <details className="rounded-lg border border-sky-200 bg-sky-50/70 px-2.5 py-2 text-[11px] text-sky-950 dark:border-sky-900 dark:bg-sky-950/20 dark:text-sky-100">
                      <summary className="cursor-pointer font-bold">QA 补评轨迹 · {result.qa_history?.length} 批 · 共 {result.qa_history?.reduce((sum, entry) => sum + (entry.attempts?.length || 0), 0)} 次尝试</summary>
                      <div className="mt-2 space-y-2">
                        {result.qa_history?.map((entry, batchIndex) => (
                          <div key={entry.batch_id || batchIndex} className="rounded border border-sky-200/80 bg-white/60 p-2 dark:border-sky-900 dark:bg-black/10">
                            <div className="font-semibold">第 {batchIndex + 1} 批补评前：{entry.previous_error || entry.previous_evaluation?.summary || "无错误摘要"}</div>
                            {(entry.attempts || []).map((attempt) => (
                              <div key={`${entry.batch_id}-${attempt.attempt}`} className={attempt.status === "done" ? "mt-1 text-emerald-700 dark:text-emerald-300" : "mt-1 text-red-700 dark:text-red-300"}>
                                尝试 {attempt.attempt} · {attempt.status} · {attempt.seconds.toFixed(1)} 秒{attempt.error ? ` · ${attempt.error}` : ""}
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export default function FloorplanPage() {
  const studioRef = useRef<WholeHomeStudioHandle>(null);
  const autoCaptureInProgressRef = useRef(false);
  const initialRunOpenedRef = useRef(false);
  const historySelectionVersionRef = useRef(0);
  const generationDraftVersionRef = useRef(0);
  const cadReparseSequenceRef = useRef(0);
  const cadReparseProjectIdRef = useRef("");
  const [sourceMode, setSourceMode] = useState<WholeHomeSourceMode>("image");
  const [plan, setPlan] = useState<FloorplanUpload | null>(null);
  const [planPage, setPlanPage] = useState<(Swatch & { page?: number }) | null>(null);
  const [cadUpload, setCadUpload] = useState<CadUpload | null>(null);
  const [cadStatus, setCadStatus] = useState<CadRuntimeStatus | null>(null);
  const [cadStatusError, setCadStatusError] = useState("");
  const [referenceUrl, setReferenceUrl] = useState("");
  const [floor, setFloor] = useState<Swatch | null>(null);
  const [styleRef, setStyleRef] = useState<Swatch | null>(null);
  const [project, setProject] = useState<WholeHomeProject | null>(null);
  const [activeSceneRecipe, setActiveSceneRecipe] = useState<WholeHomeSceneRecipe | null>(null);
  const [draft, setDraft] = useState<WholeHomeModel | null>(null);
  const [geometryManifest, setGeometryManifest] = useState<WholeHomeGeometryManifest | null>(null);
  const [geometryReviewNote, setGeometryReviewNote] = useState("");
  const [geometryAssumptionsConfirmed, setGeometryAssumptionsConfirmed] = useState(false);
  const [rasterRoomsReviewed, setRasterRoomsReviewed] = useState(false);
  const [rasterOpeningsReviewed, setRasterOpeningsReviewed] = useState(false);
  const [rasterNoUnresolved, setRasterNoUnresolved] = useState(false);
  const [panoViewerUrl, setPanoViewerUrl] = useState("");
  const [panoViewerId, setPanoViewerId] = useState("");
  const [panoViewerCaptureId, setPanoViewerCaptureId] = useState("");
  const [panoPaidPreview, setPanoPaidPreview] = useState<WholeHomePanoPaidPreview | null>(null);
  const [panoPaidCaptureId, setPanoPaidCaptureId] = useState("");
  const [panoConfirmation, setPanoConfirmation] = useState("");
  const [panoBusy, setPanoBusy] = useState("");
  const [projects, setProjects] = useState<WholeHomeProject[]>([]);
  const [legacy, setLegacy] = useState<FloorplanAnalysis[]>([]);
  const [run, setRun] = useState<WholeHomeRun | null>(null);
  const [runs, setRuns] = useState<WholeHomeRun[]>([]);
  const [reviewState, setReviewState] = useState<WholeHomeReviewState | null>(null);
  const [learningSummary, setLearningSummary] = useState<WholeHomeLearningSummary | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [manualCapabilities, setManualCapabilities] = useState<WholeHomeManualCapabilities | null>(null);
  const [manualPreview, setManualPreview] = useState<WholeHomeManualRunPreview | null>(null);
  const [cadReparseOperation, setCadReparseOperation] = useState<WholeHomeCadReparseOperation | null>(null);
  const [cadAiAdvisory, setCadAiAdvisory] = useState<WholeHomeCadAiAdvisory | null>(null);
  const [manualConfirmation, setManualConfirmation] = useState("");
  const [dirty, setDirty] = useState(false);
  const [operations, setOperations] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState("");
  const [selectedCaptures, setSelectedCaptures] = useState<string[]>([]);
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>("4:3");
  const [resolution, setResolution] = useState<"2K" | "4K">("2K");
  const [materialMode, setMaterialMode] = useState<WholeHomeMaterialMode>("style_pack");
  const [modelKeys, setModelKeys] = useState<("b2" | "pro")[]>([]);
  const [candidates, setCandidates] = useState<1 | 2>(1);
  const shotsPerRoom = 1 as const;
  const [style, setStyle] = useState("现代暖木自然 · 米白墙面 · 浅暖木 · 燕麦织物 · 克制黑色金属");
  const [lighting, setLighting] = useState("柔和自然日光 + 3200K 暖中性补光 · AgX");
  const [prompt, setPrompt] = useState("");
  const recentFloorRun = runs.find((item) => item.floor_path && item.floor_url);
  const manualSafe = manualCapabilities?.manual_safe ?? true;
  const invalidateManualPreview = () => {
    setManualPreview(null);
    setManualConfirmation("");
  };

  useEffect(() => () => { cadReparseSequenceRef.current += 1; }, []);

  useEffect(() => {
    if (cadReparseProjectIdRef.current && cadReparseProjectIdRef.current !== project?.project_id) {
      const timer = window.setTimeout(() => {
        cadReparseSequenceRef.current += 1;
        cadReparseProjectIdRef.current = "";
        setCadReparseOperation(null);
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [project?.project_id]);

  const loadHistory = useCallback(async () => {
    // Publish each history source as soon as it arrives.  Run summaries can
    // legitimately take longer because old runs include large review ledgers;
    // they must not hold the lightweight project list (and therefore the
    // project history panel) behind one shared Promise.allSettled barrier.
    await Promise.allSettled([
      api.listWholeHomeProjects(30).then(setProjects),
      api.listWholeHomeRuns(30).then(setRuns),
      api.listFloorplanAnalyses(20).then(setLegacy),
    ]);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadHistory(); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory]);

  useEffect(() => {
    let cancelled = false;
    void api.getWholeHomeManualCapabilities().then((value) => {
      if (!cancelled) setManualCapabilities(value);
    }).catch((error) => {
      if (!cancelled) toast.error(`手动安全能力读取失败：${(error as Error).message}`);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void api.getWholeHomeCadStatus().then((value) => {
      if (!cancelled) {
        setCadStatus(value);
        setCadStatusError("");
      }
    }).catch((error) => {
      if (!cancelled) setCadStatusError((error as Error).message);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!project?.project_id) return;
    let cancelled = false;
    void api.getWholeHomeLearningSummary(project.project_id).then((value) => {
      if (!cancelled) setLearningSummary(value);
    }).catch(() => { /* 历史版本可能尚未启用学习汇总，不阻断整屋编辑。 */ });
    return () => { cancelled = true; };
  }, [project?.project_id]);

  const refreshHumanReview = useCallback(async (runId: string, projectId: string) => {
    setReviewLoading(true);
    try {
      const [stateResult, summaryResult] = await Promise.allSettled([
        api.getWholeHomeReviewState(runId),
        api.getWholeHomeLearningSummary(projectId),
      ]);
      if (stateResult.status === "fulfilled") setReviewState(stateResult.value);
      else throw stateResult.reason;
      if (summaryResult.status === "fulfilled") setLearningSummary(summaryResult.value);
    } catch (error) {
      toast.error(`人工评审状态刷新失败：${(error as Error).message}`);
    } finally {
      setReviewLoading(false);
    }
  }, []);

  const applyProject = useCallback((value: WholeHomeProject) => {
    const cadProject = value.source_type === "cad";
    setSourceMode(cadProject ? "cad" : "image");
    setMaterialMode(manualSafe ? "floor_sample" : cadProject && value.reference_contract?.contract_id ? "reference" : "floor_sample");
    if (cadProject && value.reference_url) setReferenceUrl(value.reference_url);
    if (cadProject && value.cad_source?.sha256) {
      setCadUpload({
        path: value.cad_source.path || "",
        name: value.cad_source.name || "历史 CAD 源文件",
        url: "",
        format: value.cad_source.format === "dxf" ? "dxf" : "dwg",
        version: value.cad_source.version || "",
        version_name: "",
        sha256: value.cad_source.sha256,
        size_bytes: value.cad_source.size_bytes || 0,
      });
    } else if (!cadProject) {
      setCadUpload(null);
    }
    setProject(value);
    setDraft(value.model?.schema_version ? structuredClone(value.model) : null);
    setGeometryManifest(null);
    setDirty(false);
    setOperations([]);
    setManualPreview(null);
    setManualConfirmation("");
    setCadAiAdvisory(value.cad_ai_advisories?.at(-1) || null);
    setSelectedCaptures(manualSafe ? [] : value.captures.filter((capture) => capture.status === "confirmed" && capture.aspect_ratio === aspectRatio && capture.is_primary !== false).map((capture) => capture.capture_id));
    setProjects((rows) => [value, ...rows.filter((item) => item.project_id !== value.project_id)].sort((a, b) => b.updated_at - a.updated_at));
  }, [aspectRatio, manualSafe]);

  useEffect(() => {
    const projectId = project?.project_id;
    const manifestHash = project?.geometry_contract?.manifest?.manifest_hash;
    if (!projectId || !manifestHash) {
      setGeometryManifest(null);
      return;
    }
    let cancelled = false;
    void api.getWholeHomeGeometryManifest(projectId).then((manifest) => {
      if (!cancelled && manifest.manifest_hash === manifestHash) setGeometryManifest(manifest);
    }).catch(() => {
      if (!cancelled) setGeometryManifest(null);
    });
    return () => { cancelled = true; };
  }, [project?.geometry_contract?.manifest?.manifest_hash, project?.project_id]);

  useEffect(() => {
    setGeometryAssumptionsConfirmed(false);
    setGeometryReviewNote("");
    setRasterRoomsReviewed(false);
    setRasterOpeningsReviewed(false);
    setRasterNoUnresolved(false);
  }, [project?.project_id]);

  const watchProject = useCallback((id: string) => {
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      try {
        const value = await api.getWholeHomeProject(id);
        applyProject(value);
        if (!terminalProject.has(value.status)) window.setTimeout(tick, 1200);
        else void loadHistory();
      } catch (error) {
        toast.error((error as Error).message);
      }
    };
    void tick();
    return () => { stopped = true; };
  }, [applyProject, loadHistory]);

  const watchRun = useCallback((id: string, selectionVersion = historySelectionVersionRef.current) => {
    let stopped = false;
    const tick = async () => {
      if (stopped || selectionVersion !== historySelectionVersionRef.current) return;
      try {
        const [value, nextReviewState] = await Promise.all([
          api.getWholeHomeRun(id),
          api.getWholeHomeReviewState(id),
        ]);
        if (stopped || selectionVersion !== historySelectionVersionRef.current) return;
        setRun(value);
        setReviewState(nextReviewState);
        if (!terminalRun.has(value.status)) window.setTimeout(tick, 1500);
        else {
          void loadHistory();
          void api.getWholeHomeLearningSummary(value.project_id).then(setLearningSummary).catch(() => {});
        }
      } catch (error) {
        toast.error((error as Error).message);
      }
    };
    void tick();
    return () => { stopped = true; };
  }, [loadHistory]);

  const openHistoryRun = useCallback(async (item: WholeHomeRun) => {
    const selectionVersion = ++historySelectionVersionRef.current;
    setReviewLoading(true);
    try {
      const replay = await api.getWholeHomeRunReplay(item.run_id);
      if (selectionVersion !== historySelectionVersionRef.current) return;
      const value = replay.run;
      applyProject(replay.history_project);
      setRun(value);
      setReviewState(value.human_review || null);
      setLearningSummary(null);
      // Full learning/review projections for legacy runs are intentionally
      // loaded after the immutable model and results are already visible.
      // They can scan large historical ledgers and must not block replay.
      void Promise.allSettled([
        api.getWholeHomeReviewState(value.run_id),
        api.getWholeHomeLearningSummary(value.project_id),
      ]).then(([reviewResult, summaryResult]) => {
        if (selectionVersion !== historySelectionVersionRef.current) return;
        if (reviewResult.status === "fulfilled") setReviewState(reviewResult.value);
        if (summaryResult.status === "fulfilled") setLearningSummary(summaryResult.value);
      });
      setStyle(value.style || "现代自然");
      setLighting(value.lighting || "自然日光");
      setPrompt(value.prompt || "");
      setAspectRatio(value.aspect_ratio);
      setModelKeys(value.model_keys || []);
      if (value.floor_path) setFloor({ path: value.floor_path, url: value.floor_url, thumb: value.floor_url, name: value.floor_path.split(/[\\/]/).pop() || "历史地板小样" });
      if (value.style_ref_path) setStyleRef({ path: value.style_ref_path, url: value.style_ref_url, thumb: value.style_ref_url, name: value.style_ref_path.split(/[\\/]/).pop() || "历史风格参考" });
      window.history.replaceState(null, "", `/floorplan?project=${encodeURIComponent(item.project_id)}&run=${encodeURIComponent(item.run_id)}&mode=history`);
      window.localStorage.setItem("whole-home-last-project", item.project_id);
      if (!terminalRun.has(value.status)) watchRun(value.run_id, selectionVersion);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      if (selectionVersion === historySelectionVersionRef.current) setReviewLoading(false);
    }
  }, [applyProject, watchRun]);

  const openReplaySnapshot = useCallback((replay: WholeHomeRunReplay) => {
    applyProject(replay.history_project);
    setRun(replay.run);
    setReviewState(replay.run.human_review || null);
    setStyle(replay.run.style || "现代自然");
    setLighting(replay.run.lighting || "自然日光");
    setPrompt(replay.run.prompt || "");
    setAspectRatio(replay.run.aspect_ratio);
    setModelKeys(replay.run.model_keys || []);
    if (replay.run.floor_path) setFloor({ path: replay.run.floor_path, url: replay.run.floor_url, thumb: replay.run.floor_url, name: replay.run.floor_path.split(/[\\/]/).pop() || "历史地板小样" });
    if (replay.run.style_ref_path) setStyleRef({ path: replay.run.style_ref_path, url: replay.run.style_ref_url, thumb: replay.run.style_ref_url, name: replay.run.style_ref_path.split(/[\\/]/).pop() || "历史风格参考" });
  }, [applyProject]);

  const applyHistoryBranch = useCallback((value: WholeHomeProject, replay: WholeHomeRunReplay) => {
    applyProject(value);
    setRun(null);
    setReviewState(null);
    setStyle(replay.run.style || "现代自然");
    setLighting(replay.run.lighting || "自然日光");
    setPrompt(replay.run.prompt || "");
    setAspectRatio(replay.run.aspect_ratio);
    setModelKeys(replay.run.model_keys || []);
    generationDraftVersionRef.current = value.generation_draft?.draft_version || 0;
    if (replay.run.floor_path) setFloor({ path: replay.run.floor_path, url: replay.run.floor_url, thumb: replay.run.floor_url, name: replay.run.floor_path.split(/[\\/]/).pop() || "历史地板小样" });
    if (replay.run.style_ref_path) setStyleRef({ path: replay.run.style_ref_path, url: replay.run.style_ref_url, thumb: replay.run.style_ref_url, name: replay.run.style_ref_path.split(/[\\/]/).pop() || "历史风格参考" });
  }, [applyProject]);

  const openHistoryProject = useCallback(async (item: WholeHomeProject) => {
    // A deliberate project selection wins over the delayed "open latest run"
    // bootstrap.  Without this latch, a slow historical run request can race
    // the click and silently replace a newly selected CAD model with an older
    // image-plan project.
    initialRunOpenedRef.current = true;
    const selectionVersion = ++historySelectionVersionRef.current;
    setRun(null);
    setReviewState(null);
    setLearningSummary(null);
    try {
      const selectedProject = await api.getWholeHomeProject(item.project_id);
      if (selectionVersion !== historySelectionVersionRef.current) return;
      applyProject(selectedProject);
      window.history.replaceState(null, "", `/floorplan?project=${encodeURIComponent(selectedProject.project_id)}&mode=branch`);
      window.localStorage.setItem("whole-home-last-project", selectedProject.project_id);
      if (selectedProject.lineage) {
        const saved = await api.getWholeHomeGenerationDraft(selectedProject.project_id);
        if (selectionVersion !== historySelectionVersionRef.current) return;
        generationDraftVersionRef.current = saved.draft_version || 0;
        setStyle(saved.style || "现代自然");
        setLighting(saved.lighting || "自然日光");
        setPrompt(saved.prompt || "");
        setAspectRatio(saved.aspect_ratio || "4:3");
        setModelKeys(saved.model_keys || []);
        if (saved.floor_path) setFloor({ path: saved.floor_path, url: saved.floor_url || "", thumb: saved.floor_url || "", name: saved.floor_path.split(/[\\/]/).pop() || "历史地板小样" });
        if (saved.style_ref_path) setStyleRef({ path: saved.style_ref_path, url: saved.style_ref_url || "", thumb: saved.style_ref_url || "", name: saved.style_ref_path.split(/[\\/]/).pop() || "历史风格参考" });
      }
    } catch (error) {
      toast.error((error as Error).message);
    }
  }, [applyProject]);

  useEffect(() => {
    if (initialRunOpenedRef.current || run || (!runs.length && !projects.length)) return;
    const params = new URLSearchParams(window.location.search);
    const requestedRun = params.get("run") || "";
    const requestedProject = params.get("project") || window.localStorage.getItem("whole-home-last-project") || "";
    const targetRun = runs.find((item) => item.run_id === requestedRun);
    const targetProject = projects.find((item) => item.project_id === requestedProject);
    // An explicit/local remembered deep link wins over whichever lightweight
    // list happens to resolve first.  Waiting for that requested row avoids
    // opening the latest unrelated run during the project/run list race.
    if (requestedRun && !targetRun) return;
    if (!requestedRun && requestedProject && !targetProject) return;
    initialRunOpenedRef.current = true;
    const timer = window.setTimeout(() => {
      if (targetRun) void openHistoryRun(targetRun);
      else if (targetProject) void openHistoryProject(targetProject);
      else if (runs[0]) void openHistoryRun(runs[0]);
      else if (projects[0]) void openHistoryProject(projects[0]);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [openHistoryProject, openHistoryRun, project, projects, run, runs]);

  useEffect(() => {
    const projectId = project?.project_id;
    const sourceRunId = project?.lineage?.source_run_id;
    if (!projectId || !sourceRunId || project?.history_read_only) return;
    const timer = window.setTimeout(() => {
      void api.saveWholeHomeGenerationDraft(projectId, {
        expected_draft_version: generationDraftVersionRef.current,
        source_run_id: sourceRunId,
        variant_label: style || "新风格",
        style, lighting, prompt, material_mode: "floor_sample",
        floor_path: floor?.path || "", style_ref_path: styleRef?.path || "",
        model_keys: modelKeys, selected_artifact_ids: [],
        aspect_ratio: aspectRatio, resolution: "2K",
      }).then((saved) => {
        generationDraftVersionRef.current = saved.draft_version;
        setProject((current) => current?.project_id === projectId ? { ...current, generation_draft: saved } : current);
      }).catch((error) => {
        toast.error(`方案草稿保存失败：${(error as Error).message}`);
      });
    }, 800);
    return () => window.clearTimeout(timer);
  }, [aspectRatio, floor?.path, lighting, modelKeys, project?.history_read_only, project?.lineage?.source_run_id, project?.project_id, prompt, style, styleRef?.path]);

  function changeSourceMode(next: WholeHomeSourceMode) {
    if (next === sourceMode) return;
    const selected = switchWholeHomeSource(next, { plan, cad: cadUpload });
    setSourceMode(selected.mode);
    setPlan(selected.plan);
    setPlanPage(next === "image" ? selected.plan?.pages?.[0] || selected.plan : null);
    setCadUpload(selected.cad);
    setMaterialMode(next === "cad" ? "reference" : "floor_sample");
    setProject(null); setDraft(null); setRun(null); setReviewState(null); setLearningSummary(null);
    setDirty(false); setOperations([]); setSelectedCaptures([]);
  }

  async function uploadPlan(file: File) {
    setBusy("plan");
    try {
      const value = await api.uploadFloorplan(file);
      setPlan(value);
      setPlanPage(value.pages?.[0] || value);
      setProject(null); setDraft(null); setRun(null); setReviewState(null); setLearningSummary(null);
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function uploadCad(file: File) {
    setBusy("cad-upload");
    try {
      const value = await api.uploadCad(file);
      setCadUpload(value);
      setProject(null); setDraft(null); setRun(null); setReviewState(null); setLearningSummary(null);
      toast.success(`${value.format.toUpperCase()} 已安全上传并记录 SHA256；尚未开始解析`);
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function uploadAsset(kind: "floor" | "style", file: File) {
    setBusy(kind);
    try {
      const value = kind === "floor" ? await api.uploadFloor(file) : await api.uploadRef(file);
      if (kind === "floor") setFloor(value); else setStyleRef(value);
      invalidateManualPreview();
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function analyzeWholeHome() {
    if (!planPage) return;
    setBusy("analysis");
    try {
      const value = await api.createWholeHomeProject({ floorplan_path: planPage.path });
      applyProject(value);
      setRun(null); setReviewState(null);
      toast.success("已启动整屋共墙建模；Gemini 不再生成机位");
      watchProject(value.project_id);
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function analyzeCadWholeHome() {
    if (!cadUpload) return;
    const readiness = cadFormatReadiness(cadStatus, cadUpload.format);
    if (!readiness.ready) return toast.error(readiness.message);
    setBusy("analysis");
    try {
      const value = await api.createWholeHomeProject({
        cad_path: cadUpload.path,
        reference_url: referenceUrl.trim(),
      });
      applyProject(value);
      setRun(null); setReviewState(null);
      if (value.status === "failed") toast.error(value.error || "CAD 本地解析失败");
      else if (value.status === "needs_review") toast.warning("CAD 已生成可检查的 3D 草稿；请按页面硬门禁证据复核，当前仍禁止锁定和生图");
      else toast.success("CAD 已完成本地解析；整个阶段没有调用 Gemini 或生图 API");
      if (!terminalProject.has(value.status)) watchProject(value.project_id);
      void loadHistory();
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function reparseCadProject(candidateId = "") {
    if (!project || project.source_type !== "cad") return;
    const projectId = project.project_id;
    const sequence = ++cadReparseSequenceRef.current;
    cadReparseProjectIdRef.current = projectId;
    setBusy("cad-reparse");
    try {
      const operationId = newCadOperationId("cad-reparse");
      let operation = await api.reparseWholeHomeCad(projectId, project.revision, candidateId, operationId);
      if (!operation.operation_id) throw new Error("cad_reparse_contract_mismatch：202 响应缺少 operation_id");
      if (!operation.candidate_id && candidateId) operation = { ...operation, candidate_id: candidateId };
      setCadReparseOperation(operation);
      for (;;) {
        const status = String(operation.status || "").toLowerCase();
        if (["succeeded", "success", "done", "completed", "needs_review", "failed", "conflict", "interrupted"].includes(status)) break;
        await new Promise((resolve) => window.setTimeout(resolve, 900));
        if (sequence !== cadReparseSequenceRef.current) return;
        const nextOperation = await api.getWholeHomeCadReparseOperation(projectId, operation.operation_id);
        operation = { ...nextOperation, candidate_id: nextOperation.candidate_id || operation.candidate_id || candidateId };
        if (sequence !== cadReparseSequenceRef.current) return;
        setCadReparseOperation(operation);
      }
      if (sequence !== cadReparseSequenceRef.current) return;
      const value = await api.getWholeHomeProject(projectId);
      if (sequence !== cadReparseSequenceRef.current) return;
      applyProject(value);
      if (["failed", "conflict", "interrupted"].includes(String(operation.status).toLowerCase()) || value.status === "failed") {
        const operationError = typeof operation.error === "object" ? operation.error?.message || JSON.stringify(operation.error) : operation.error;
        toast.error(String(operationError || value.error || "CAD 重新解析仍未通过硬门禁"));
      } else if (String(operation.status).toLowerCase() === "needs_review" || value.status === "needs_review") {
        toast.warning("重解析草稿已更新并保留在历史中；仍有硬门禁项，当前禁止锁定和生图");
      } else toast.success(candidateId
        ? `已按候选平面 ${candidateId} 重新解析；历史模型和失败证据均保留`
        : "已从同一 CAD 源重新自动评分；历史模型和失败证据均保留");
      void loadHistory();
    } catch (error) { toast.error((error as Error).message); }
    finally { if (sequence === cadReparseSequenceRef.current) setBusy(""); }
  }

  async function reviewCadWithGemini() {
    if (!project || project.source_type !== "cad") return;
    setBusy("cad-ai-assist");
    try {
      const advisory = await api.reviewWholeHomeCadWithAi(project.project_id, project.revision, 1);
      setCadAiAdvisory(advisory);
      const latest = await api.getWholeHomeProject(project.project_id);
      applyProject(latest);
      toast.success("Gemini 建议已归档；CAD 几何和 revision 均未改动");
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function reanalyzeCurrentWholeHome() {
    if (project?.source_type === "cad") return toast.warning("CAD 项目禁止 AI 拓扑重建；请修正源 DWG/DXF 后使用“重新解析同一 CAD”");
    if (!project?.floorplan_path) return;
    setBusy("analysis");
    try {
      const value = await api.createWholeHomeProject({ floorplan_path: project.floorplan_path });
      applyProject(value);
      setRun(null); setReviewState(null);
      toast.success("已创建新的 AI 拓扑复核版本；原模型和机位仍保留在历史中");
      watchProject(value.project_id);
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function rebuildSemanticLayout() {
    if (!project) return;
    if (project.source_type === "cad") return toast.warning("CAD v1 的房型与固定物来自本地可追溯解析，禁止通用 AI 语义重建覆盖 CAD 事实");
    setBusy("semantic");
    try {
      const current = dirty ? await saveDraft() : project;
      const value = await api.rebuildWholeHomeSemanticLayout(current.project_id, current.revision);
      applyProject(value);
      toast.success(value.model.semantic_report.hard_errors.length ? "语义灰模已重建，仍有必须修正项" : "语义灰模已重建并通过本地规则");
      void loadHistory();
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function importLegacy(analysis: FloorplanAnalysis) {
    setBusy("analysis");
    try {
      const value = await api.createWholeHomeProject({ import_analysis_id: analysis.analysis_id, width_m: 12 });
      applyProject(value);
      toast.success("旧标注已合并成一套整屋模型；请重点复核共墙与尺度");
      void loadHistory();
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  function editModel(value: WholeHomeModel, operation: string) {
    if (project?.history_read_only) {
      toast.warning("当前是历史只读快照；请先复制为新方案再修改模型");
      return;
    }
    if (project?.source_type === "cad" || project?.cad_geometry_read_only) {
      toast.error("CAD 原始墙、门、窗不能在 3D 视图中修改；物理空间与语义区请在上方人工确认面板编辑");
      return;
    }
    setDraft(value);
    setDirty(true);
    setOperations((rows) => [...rows.slice(-99), { type: operation, payload: {} }]);
  }

  async function saveDraft(baseProject = project) {
    if (!baseProject || !draft) throw new Error("没有可保存的整屋模型");
    if (baseProject.history_read_only) throw new Error("历史快照永久只读；请先复制为新方案");
    if (baseProject.source_type === "cad" || baseProject.cad_geometry_read_only) throw new Error("CAD 权威模型禁止通用保存；请回源修正后重新解析");
    const value = await api.saveWholeHomeModel(baseProject.project_id, {
      base_revision: baseProject.revision, model: draft, operations, annotator_id: "local-user",
    });
    applyProject(value);
    return value;
  }

  async function saveModelOnly() {
    setBusy("save");
    try { await saveDraft(); toast.success("整屋模型草稿已保存"); }
    catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function registerRasterSource(
    anchors: Array<{ id: string; start_px: [number, number]; end_px: [number, number]; length_m: number }>,
    origin: [number, number],
  ) {
    if (!project) return;
    setBusy("raster-registration");
    try {
      const current = dirty ? await saveDraft() : project;
      const value = await api.prepareWholeHomeRasterRegistration(current.project_id, {
        base_revision: current.revision,
        operation_id: newCadOperationId("raster-registration"),
        reviewer: "local-user",
        scale_anchors: anchors,
        origin_px: origin,
      });
      applyProject(value);
      toast.success("原图哈希、两条真实尺寸和模型墙线已保存为可逆配准");
      void loadHistory();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function commitGeometryAcceptance() {
    if (!project || !draft) return;
    if (!geometryAssumptionsConfirmed || geometryReviewNote.trim().length < 3) {
      toast.warning("请先确认已逐项对照图纸，并填写本次复核说明");
      return;
    }
    setBusy("geometry-acceptance");
    try {
      const current = dirty ? await saveDraft() : project;
      const rasterProject = current.source_type !== "cad";
      if (rasterProject && !(rasterRoomsReviewed && rasterOpeningsReviewed && rasterNoUnresolved)) {
        throw new Error("普通户型图必须先逐项确认房间、门窗和无遗留问题");
      }
      const result = await api.evaluateWholeHomeGeometry(current.project_id, {
        base_revision: current.revision,
        operation_id: newCadOperationId("geometry-lock"),
        reviewer: "local-user",
        review_note: geometryReviewNote.trim(),
        assumptions_confirmed: geometryAssumptionsConfirmed,
        raster_metrics: rasterProject ? {
          room_iou: 1,
          opening_precision: 1,
          opening_recall: 1,
          human_review_completion: 1,
          unresolved_review_count: 0,
        } : {},
        commit: true,
      });
      if (!result.project) throw new Error("对应验收返回缺少已提交项目");
      applyProject(result.project);
      toast.success("2D 图纸、米制模型与服务端三角网格已绑定到同一 revision");
      void loadHistory();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function lockModel() {
    if (!project || !draft) return;
    if (project.history_read_only) return toast.warning("历史快照永久只读；请先复制为新方案");
    if (project.geometry_contract?.required
        && !project.geometry_contract.production_readiness.ready) {
      toast.warning("请先运行并提交“图纸 ↔ 3D 对应验收”，通过后才能锁定与生成");
      return;
    }
    setBusy("verify");
    try {
      const current = dirty ? await saveDraft() : project;
      const warningCodes = (current.model.geometry_report?.warnings || []).map((issue) => issue.code);
      const value = await api.verifyWholeHomeModel(current.project_id, {
        base_revision: current.revision, acknowledged_warning_codes: warningCodes, annotator_id: "local-user",
      });
      applyProject(value);
      toast.success(manualSafe
        ? "整屋几何已锁定；请在 3D 灰模中人工布置并保存一个机位"
        : "整屋几何已锁定；现在可以一键自动选择机位并生成");
      void loadHistory();
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  async function saveCapture(camera: WholeHomeCamera, buffers: { rgb: string; depth: string; normal: string; edge: string; semantic: string; subjectId?: string; subjectIdLegend?: import("@/lib/types").WholeHomeSubjectIdLegend; proposalId?: string; proposalHash?: string }, plan?: WholeHomeAutoCameraPlan): Promise<WholeHomeCapture> {
    if (!project) throw new Error("没有可用的整屋项目");
    if (project.history_read_only) throw new Error("历史快照永久只读；请先复制为新方案");
    const automatic = autoCaptureInProgressRef.current;
    if (!automatic) setBusy("capture");
    try {
      const value = await api.saveWholeHomeCapture(project.project_id, {
        camera, aspect_ratio: aspectRatio, rgb_data_url: buffers.rgb, depth_data_url: buffers.depth,
        normal_data_url: buffers.normal, edge_data_url: buffers.edge,
         semantic_data_url: buffers.semantic,
         semantic_legend: Object.fromEntries(Object.entries(WHOLE_HOME_SEMANTIC_COLORS)),
         subject_id_data_url: buffers.subjectId || "",
         subject_id_legend: buffers.subjectIdLegend,
         room_id: camera.room_id, plan_id: plan?.plan_id || camera.auto_plan_id || "",
         candidate_id: camera.candidate_id || "", pool_rank: camera.pool_rank || 1,
         reference_slot_id: camera.reference_slot_id || "",
         reference_proposal_id: buffers.proposalId || camera.reference_proposal_id || "",
         reference_proposal_hash: buffers.proposalHash || camera.reference_proposal_hash || "",
         scene_recipe_id: activeSceneRecipe?.status === "locked" ? activeSceneRecipe.recipe_id : "",
         scene_hash: activeSceneRecipe?.status === "locked" ? activeSceneRecipe.scene_hash : "",
        is_primary: camera.is_primary ?? true, annotator_id: "local-user",
      });
      const capture = [...value.captures].reverse().find((item) => item.camera_id === camera.id) || value.captures[value.captures.length - 1];
      if (!capture) throw new Error(`${camera.name} 保存后没有返回机位记录`);
      if (!automatic) {
        applyProject(value);
        setSelectedCaptures((rows) => Array.from(new Set([...rows, capture.capture_id])));
        toast.success(`已保存 ${camera.name}：RGB、深度、法线、边线和语义缓冲`);
      }
      return capture;
    } catch (error) {
      if (!automatic) toast.error((error as Error).message);
      throw error;
    } finally {
      if (!automatic) setBusy("");
    }
  }

  async function savePanoCapture(camera: WholeHomeCamera, payload: {
    pano_id: string; camera_center_m: import("@/lib/types").MetricXYZ; cube_face_size: number;
    erp_width: number; erp_height: number; near_m: number; far_m: number;
    heading_deg: number; pitch_deg: number; roll_deg: number;
    atlases: { rgb: string; depth: string; normal: string; edge: string; semantic: string; subject_id: string };
    subject_id_legend: import("@/lib/types").WholeHomeSubjectIdLegend;
    render_contract: { materials: Record<string, unknown>; lighting: Record<string, unknown> };
  }) {
    if (!project) throw new Error("没有可用的整屋项目");
    if (project.history_read_only) throw new Error("历史快照永久只读；请先复制为新方案");
    setBusy("capture");
    try {
      const value = await api.saveWholeHomePanoCapture(project.project_id, {
        pano_id: payload.pano_id, camera,
        projection: "equirectangular", coordinate_system: "right-handed-y-up",
        camera_center_m: payload.camera_center_m, canonical_forward: "+Z",
        heading_deg: payload.heading_deg, pitch_deg: payload.pitch_deg, roll_deg: payload.roll_deg,
        erp_width: payload.erp_width, erp_height: payload.erp_height,
        cube_face_size: payload.cube_face_size,
        cube_face_order: ["+X", "-X", "+Y", "-Y", "+Z", "-Z"],
        near_m: payload.near_m, far_m: payload.far_m,
        depth_encoding: "linear_metric_global_range", normal_encoding: "world_space_xyz_to_rgb",
        rgb_atlas_data_url: payload.atlases.rgb, depth_atlas_data_url: payload.atlases.depth,
        normal_atlas_data_url: payload.atlases.normal, edge_atlas_data_url: payload.atlases.edge,
        semantic_atlas_data_url: payload.atlases.semantic,
        subject_id_atlas_data_url: payload.atlases.subject_id,
        semantic_legend: Object.fromEntries(Object.entries(WHOLE_HOME_SEMANTIC_COLORS)),
        subject_id_legend: payload.subject_id_legend,
        render_contract: payload.render_contract,
        scene_recipe_id: activeSceneRecipe?.status === "locked" ? activeSceneRecipe.recipe_id : "",
        scene_hash: activeSceneRecipe?.status === "locked" ? activeSceneRecipe.scene_hash : "",
        room_id: camera.room_id, annotator_id: "local-user",
      });
      applyProject(value);
      toast.success(`已保存全景热点 ${payload.pano_id}：六面六通道图集，固定输出 3840×1920`);
    } catch (error) {
      toast.error((error as Error).message);
      throw error;
    } finally {
      setBusy("");
    }
  }

  function panoSourceHash(pano: WholeHomePanoCapture): string {
    return String(pano.manifest?.source_hash || "");
  }

  async function preparePanoPaidEdit(pano: WholeHomePanoCapture) {
    if (!project) throw new Error("没有可用的整屋项目");
    setPanoBusy("preview");
    try {
      const preview = await api.previewWholeHomePanoEdit(project.project_id, pano.pano_id, {
        source_hash: panoSourceHash(pano), provider: "fal", engine: "flux-canny",
        model_id: "flux-control-lora-canny",
        edit_instruction: prompt.trim(),
        style_description: `${style.trim()}；${lighting.trim()}`,
        repair_band_deg: 12, annotator_id: "local-user",
      });
      setPanoPaidPreview(preview);
      setPanoPaidCaptureId(pano.capture_id || pano.manifest.capture_id || "");
      setPanoConfirmation("");
      toast.success(preview.resume_only
        ? `已恢复原 Fal 请求 ${preview.resume_request_id || ""}；只取结果，不会再次付费`
        : pano.edited_rgb_url
          ? "已免费恢复原付费确认；尚未执行 repair"
          : "全景付费预览已创建；尚未调用 fal.ai");
    } catch (error) {
      toast.error(`全景付费预览失败：${(error as Error).message}`);
    } finally {
      setPanoBusy("");
    }
  }

  async function materializePanoLocally(pano: WholeHomePanoCapture) {
    if (!project) throw new Error("没有可用的整屋项目");
    setPanoBusy("materialize");
    try {
      const value = await api.materializeWholeHomePano(project.project_id, pano.pano_id, {
        source_hash: panoSourceHash(pano), preset: "warm-contemporary",
        annotator_id: "local-user",
      });
      applyProject(value);
      toast.success("已完成本地几何锁定材质化：0 元、无 provider 调用；请继续运行 P0 门禁");
    } catch (error) {
      toast.error(`本地材质化失败：${(error as Error).message}`);
    } finally {
      setPanoBusy("");
    }
  }

  async function commitPanoEdit(pano: WholeHomePanoCapture) {
    if (!project || !panoPaidPreview) throw new Error("请先创建付费预览");
    setPanoBusy("edit");
    try {
      const value = await api.editWholeHomePano(project.project_id, pano.pano_id, {
        pano_id: pano.pano_id, source_hash: panoSourceHash(pano),
        preview_id: panoPaidPreview.preview_id, confirmation_phrase: panoConfirmation,
        annotator_id: "local-user",
      });
      applyProject(value);
      toast.success("唯一一次全景 edit 已完成；请先运行本地 P0 门禁");
    } catch (error) {
      toast.error(`全景 edit 失败（本次额度不会自动重放）：${(error as Error).message}`);
    } finally {
      setPanoBusy("");
    }
  }

  async function runPanoGate(pano: WholeHomePanoCapture) {
    if (!project) throw new Error("没有可用的整屋项目");
    setPanoBusy("gate");
    try {
      const result = await api.gateWholeHomePano(project.project_id, pano.pano_id, {
        source_hash: panoSourceHash(pano), face_size: 256, annotator_id: "local-user",
      });
      const value = await api.getWholeHomeProject(project.project_id);
      applyProject(value);
      toast[result.gate.gate_pass ? "success" : "error"](
        `P0 RGB/结构门禁${result.gate.gate_pass ? "通过" : "未通过"}；完整合同通过=${result.gate.full_contract_pass}`,
      );
    } catch (error) {
      toast.error(`全景门禁失败：${(error as Error).message}`);
    } finally {
      setPanoBusy("");
    }
  }

  async function repairPanoSeam(pano: WholeHomePanoCapture) {
    if (!project || !panoPaidPreview) throw new Error("原付费预览已丢失，不能执行 repair");
    setPanoBusy("repair");
    try {
      const value = await api.repairWholeHomePano(project.project_id, pano.pano_id, {
        pano_id: pano.pano_id, source_hash: panoSourceHash(pano),
        preview_id: panoPaidPreview.preview_id, confirmation_phrase: panoConfirmation,
        annotator_id: "local-user",
      });
      applyProject(value);
      toast.success("唯一一次 seam repair 已完成；请重新运行本地门禁");
    } catch (error) {
      toast.error(`全景修缝失败（本次额度不会自动重放）：${(error as Error).message}`);
    } finally {
      setPanoBusy("");
    }
  }

  async function submitPanoReview(result: PanoChecklistResult) {
    if (!project) throw new Error("没有可用的整屋项目");
    const pano = (project.pano_captures || []).find((row) =>
      (row.capture_id || row.manifest.capture_id) === panoViewerCaptureId);
    const gate = pano?.gate as WholeHomePanoGate | undefined;
    if (!pano || !gate?.gate_pass) throw new Error("只有 P0 门禁通过的候选可以提交验收");
    const checklist = Object.fromEntries(
      Object.entries(result).map(([key, value]) => [key, value]),
    ) as Record<string, "pass" | "uncertain">;
    setPanoBusy("review");
    try {
      const value = await api.reviewWholeHomePano(project.project_id, pano.pano_id, {
        source_hash: panoSourceHash(pano), gate_version: gate.version,
        checklist, annotator_id: "local-user",
      });
      applyProject(value);
      const accepted = Object.values(checklist).every((value) => value === "pass");
      toast[accepted ? "success" : "error"](
        accepted ? "全景人工验收已持久化并接受" : "人工验收含不确定项，候选已标记失败",
      );
    } catch (error) {
      toast.error(`全景验收保存失败：${(error as Error).message}`);
    } finally {
      setPanoBusy("");
    }
  }

  async function generateCameraCandidates(): Promise<WholeHomeCameraCandidateProposal> {
    if (!project) throw new Error("没有可用的整屋项目");
    const reference = materialMode === "reference";
    return api.createWholeHomeCameraCandidates(project.project_id, {
      aspect_ratio: reference ? "4:3" : aspectRatio, max_per_room: 8,
      mode: reference ? "reference" : "room",
      contract_id: reference ? project.reference_contract?.contract_id || "" : "",
    });
  }

  async function rankAutoCameras(rows: WholeHomeCameraCandidate[], roomPools: WholeHomeCameraRoomPool[]) {
    if (!project) throw new Error("没有可用的整屋项目");
    return api.rankWholeHomeCameras(project.project_id, {
      aspect_ratio: aspectRatio, shots_per_room: shotsPerRoom,
      candidates: rows, room_pools: roomPools, annotator_id: "local-user",
    });
  }

  function toggleModel(key: "b2" | "pro", checked: boolean) {
    setManualPreview(null);
    setManualConfirmation("");
    setModelKeys((current) => manualSafe
      ? (checked ? [key] : [])
      : checked ? Array.from(new Set([...current, key])) : current.length > 1 ? current.filter((item) => item !== key) : current);
  }

  async function submitRun(captureIds: string[], captureGroups: WholeHomeCaptureGroup[] = []) {
    if (!project) throw new Error("没有可用的整屋项目");
    if (manualSafe) {
      if (materialMode === "floor_sample" && !floor) throw new Error("地板产品模式必须上传地板小样");
      if (materialMode === "reference") throw new Error("手动安全模式不开放 Reference benchmark");
      if (materialMode === "style_pack" && activeSceneRecipe?.status !== "locked") throw new Error("请先锁定整屋 SceneRecipe");
      if (captureGroups.length || captureIds.length !== 1) throw new Error("手动安全模式每次必须且只能选择一个机位");
      if (modelKeys.length !== 1) throw new Error("手动安全模式每次必须且只能选择一个模型");
      setBusy("preview");
      try {
        const idempotencyKey = `manual_${Date.now().toString(36)}_${window.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 12) || Math.random().toString(36).slice(2, 14)}`;
        const value = await api.previewWholeHomeManualRun({
          project_id: project.project_id,
          capture_ids: captureIds,
          capture_groups: [],
          floor_path: materialMode === "floor_sample" ? floor?.path || "" : "",
          material_mode: materialMode,
          scene_recipe_id: materialMode === "style_pack" ? activeSceneRecipe?.recipe_id || "" : "",
          reference_contract_id: "",
          benchmark_batch_id: "",
          style_ref_path: materialMode === "floor_sample" ? styleRef?.path || null : null,
          prompt, style, lighting,
          model_keys: modelKeys,
          candidates_per_camera: 1,
          aspect_ratio: aspectRatio,
          resolution: "2K",
          idempotency_key: idempotencyKey,
        });
        setManualPreview(value);
        setManualConfirmation("");
        toast.success("只读预览已生成；尚未调用生图或 QA。核对 hash 后输入动态短语才能提交。");
        return null;
      } finally {
        setBusy("");
      }
    }
    const modeGate = materialModeGate({
      mode: materialMode, floorPath: floor?.path, referenceGate,
      sceneReady: activeSceneRecipe?.status === "locked",
    });
    if (!modeGate.ready) throw new Error(modeGate.message);
    if (reviewGenerationLocked) throw new Error("请先完成当前轮全部人工评审并点击“本轮评审完成”，再启动新的生图任务");
    const runCaptureGroups = materialMode === "reference"
      ? (captureGroups.length === project.reference_contract?.slots.length ? captureGroups : referenceGate.captureGroups)
      : captureGroups;
    const runCaptureIds = materialMode === "reference" ? [] : captureIds;
    if (!runCaptureIds.length && !runCaptureGroups.length) throw new Error(`没有可用的 ${aspectRatio} 机位`);
    const runModels = materialMode === "reference" ? (["b2", "pro"] as const) : modelKeys;
    const runAspect = materialMode === "reference" ? "4:3" as const : aspectRatio;
    const runResolution = materialMode === "reference" ? "4K" as const : resolution;
    const benchmarkBatchId = materialMode === "reference"
      ? `reference_${Date.now().toString(36)}_${window.crypto?.randomUUID?.().slice(0, 8) || Math.random().toString(36).slice(2, 10)}`
      : "";
    setBusy("generate");
    try {
      const value = await api.createWholeHomeRun({
        project_id: project.project_id, capture_ids: runCaptureIds, capture_groups: runCaptureGroups,
        floor_path: materialMode === "floor_sample" ? floor?.path || "" : "",
        material_mode: materialMode,
        scene_recipe_id: materialMode === "style_pack" ? activeSceneRecipe?.recipe_id || "" : "",
        reference_contract_id: materialMode === "reference" ? project.reference_contract?.contract_id || "" : "",
        benchmark_batch_id: benchmarkBatchId,
        // A generic style image is never a substitute for the nine audited slot assets.
        style_ref_path: materialMode === "floor_sample" ? styleRef?.path || null : null,
        prompt, style, lighting,
        model_keys: [...runModels], candidates_per_camera: candidates,
        aspect_ratio: runAspect, resolution: runResolution,
        idempotency_key: benchmarkBatchId,
      });
      setRun(value);
      setReviewState(value.human_review || null);
      toast.success(runCaptureGroups.length
        ? `已提交 ${runCaptureGroups.length} 个 ${materialMode === "reference" ? "reference slots" : "房间"}的主/备用机位池，${runModels.map(modelLabel).join(" + ")}`
        : `已提交 ${runCaptureIds.length} 个 3D 机位，${runModels.map(modelLabel).join(" + ")}`);
      watchRun(value.run_id);
      return value;
    } finally { setBusy(""); }
  }

  async function generate() {
    if (!project) return;
    if (project.history_read_only) return toast.warning("历史快照不会触发付费生成；请先复制为新方案");
    if (materialMode === "reference") {
      try { await submitRun([], referenceGate.captureGroups); }
      catch (error) { toast.error((error as Error).message); }
      return;
    }
    if (materialMode === "floor_sample" && !floor) return;
    const lockedScene = activeSceneRecipe?.status === "locked" ? activeSceneRecipe : null;
    if (materialMode === "style_pack" && !lockedScene) return toast.warning("请先锁定整屋方案");
    const valid = project.captures.filter((capture) => selectedCaptures.includes(capture.capture_id)
      && capture.status === "confirmed" && capture.aspect_ratio === aspectRatio
      && (materialMode !== "style_pack" || (
        capture.scene_recipe_id === lockedScene?.recipe_id
        && capture.scene_hash === lockedScene?.scene_hash)));
    if (manualSafe && valid.length !== 1) return toast.warning(`手动安全模式请只勾选一个 ${aspectRatio} 机位`);
    if (!valid.length) return toast.warning(`请先保存并勾选至少一个 ${aspectRatio} 机位`);
    try { await submitRun(valid.map((capture) => capture.capture_id)); }
    catch (error) { toast.error((error as Error).message); }
  }

  async function finishAutoCapture(captures: WholeHomeCapture[], plan: WholeHomeAutoCameraPlan) {
    if (!project) throw new Error("没有可用的整屋项目");
    const latest = await api.getWholeHomeProject(project.project_id);
    const completedCaptures = materialMode === "reference" ? latest.captures : captures;
    const captureGroups: WholeHomeCaptureGroup[] = plan.room_pools
      .filter((pool) => pool.status === "ready")
      .map((pool) => {
        const rows = completedCaptures
          .filter((capture) => materialMode === "reference"
            ? (capture.reference_slot_id || capture.camera.reference_slot_id) === pool.slot_id
            : capture.room_id === pool.room_id)
          .sort((left, right) => left.pool_rank - right.pool_rank);
        const primary = rows.find((capture) => capture.is_primary) || rows[0];
        if (!primary) throw new Error(`${pool.room_label} 没有固化主机位`);
        return {
          room_id: pool.room_id,
          ...(pool.slot_id ? { slot_id: pool.slot_id } : {}),
          primary_capture_id: primary.capture_id,
          fallback_capture_ids: rows.filter((capture) => capture.capture_id !== primary.capture_id).slice(0, 2).map((capture) => capture.capture_id),
        };
      });
    const ids = captureGroups.map((group) => group.primary_capture_id);
    applyProject(latest);
    setSelectedCaptures(ids);
    toast.success(`${plan.summary}；本次新增 ${captures.length} 个机位，已有记录全部保留`);
    if (materialMode === "reference") {
      const paidGate = buildReferenceCaptureGate(latest);
      if (!paidGate.ready) throw new Error(`Reference 保存后 paid gate 仍未通过：${paidGate.message}`);
      toast.success("9-slot subject-ID 证据已完整落盘；请点击付费生成按钮启动 B2 + Pro");
      return;
    }
    toast.info("机位已保存，但不会自动提交生图。请手动只选一个机位、一个模型并生成预览。");
  }

  async function commitManualPreview() {
    if (!manualPreview) return;
    if (manualConfirmation !== manualPreview.confirmation_phrase) {
      return toast.error("动态确认短语不一致");
    }
    setBusy("commit");
    try {
      const value = await api.commitWholeHomeManualRun({
        preview_id: manualPreview.preview_id,
        preview_sha256: manualPreview.preview_sha256,
        confirmation_phrase: manualConfirmation,
      });
      setManualPreview(null);
      setManualConfirmation("");
      setRun(value);
      setReviewState(value.human_review || null);
      toast.success("手动受限任务已提交：1 机位 × 1 模型 × 1 候选 · 2K");
      watchRun(value.run_id);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function autoGenerate() {
    if (materialMode === "reference" && !referencePreflight.ready) return toast.error(referencePreflight.message);
    if (materialMode === "floor_sample" && !floor) return toast.warning("请先上传地板小样");
    if (materialMode === "style_pack" && activeSceneRecipe?.status !== "locked") return toast.warning("请先复核并锁定整屋方案");
    if (materialMode === "reference") {
      if (!project) return;
      setBusy("auto");
      try {
        const proposal = await generateCameraCandidates();
        if (proposal.status !== "ready" || !proposal.proposal_id || !proposal.proposal_hash) {
          const blocked = (proposal.slot_pools || [])
            .filter((pool) => pool.status !== "ready")
            .map((pool) => pool.slot_id || pool.room_label)
            .join("、");
          throw new Error(`CAD reference 机位未完全就绪：${blocked || "请查看候选诊断"}`);
        }
        const result = await api.renderWholeHomeReferenceCaptures(project.project_id, {
          reference_proposal_id: proposal.proposal_id,
          reference_proposal_hash: proposal.proposal_hash,
          width: 192, height: 144, annotator_id: "local-software-renderer",
        });
        applyProject(result.project);
        if (result.batch.status !== "ready") {
          const blocked = result.batch.blocked.map((row) => row.slot_id).join("、");
          toast.warning(`本地 CPU 证据已保存 ${result.batch.saved.length} 个；仍阻断：${blocked}`);
          return;
        }
        toast.success("9-slot CPU 灰模与 subject-ID 证据已完整落盘；未产生 API 费用，请再次点击开始 B2 + Pro");
      } catch (error) {
        toast.error((error as Error).message);
      } finally {
        setBusy("");
      }
      return;
    }
    if (!studioRef.current) return toast.error("3D 灰模尚未就绪");
    autoCaptureInProgressRef.current = true;
    setBusy("auto");
    try {
      await studioRef.current.autoSelectAndCapture();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      autoCaptureInProgressRef.current = false;
      setBusy("");
    }
  }

  async function retryUnavailableQa() {
    if (!run) return;
    if (reviewGenerationLocked) return toast.warning("当前轮正在等待人工评审；完成并放行前不会调用 Gemini QA");
    setBusy("qa");
    try {
      const value = await api.retryWholeHomeQa(run.run_id);
      setRun(value);
      setReviewState(value.human_review || reviewState);
      const remaining = value.results.filter((result) => result.evaluation?.status === "unavailable").length;
      if (remaining) toast.warning(`QA 补评完成，仍有 ${remaining} 个结果受网络影响；历次错误均已保留`);
      else toast.success("网络失败的 QA 已全部补评，原错误与重试记录均已保留");
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(""); }
  }

  const referenceGate = buildReferenceCaptureGate(project);
  const referencePreflight = buildReferencePreflightGate(project);
  const activeAspectRatio = materialMode === "reference" ? "4:3" : aspectRatio;
  const validCaptures = project?.captures.filter((capture) => capture.status === "confirmed" && capture.aspect_ratio === activeAspectRatio) || [];
  const hardErrors = draft?.geometry_report?.hard_errors || [];
  const warnings = draft?.geometry_report?.warnings || [];
  const semanticHardErrors = draft?.semantic_report?.hard_errors || [];
  const selectedRoomCount = draft?.rooms.filter((room) => room.selected).length || 0;
  const estimatedAutoCaptures = selectedRoomCount;
  const estimatedResults = materialMode === "reference"
    ? referenceGate.estimatedResults
    : (selectedCaptures.length || estimatedAutoCaptures) * modelKeys.length;
  const activeMaterialGate = materialModeGate({
    mode: materialMode, floorPath: floor?.path, referenceGate,
    sceneReady: activeSceneRecipe?.status === "locked",
  });
  const activeCadReadiness = cadFormatReadiness(cadStatus, cadUpload?.format || "");
  const historyReadOnly = project?.history_read_only === true;
  const cadReadOnly = historyReadOnly || !canMutateWholeHomeGeometry(project?.source_type)
    || project?.cad_geometry_read_only === true;
  const geometryContract = project?.geometry_contract;
  const geometryReady = geometryContract?.production_readiness.ready === true;
  const rasterGeometryProject = Boolean(project && project.source_type !== "cad");
  const rasterReviewComplete = !rasterGeometryProject
    || (rasterRoomsReviewed && rasterOpeningsReviewed && rasterNoUnresolved);
  const activeReviewState = reviewState || run?.human_review || null;
  const reviewGenerationLocked = Boolean(
    run && project && run.project_id === project.project_id && isWholeHomeGenerationLocked(activeReviewState),
  );
  const steps = [
    { label: sourceMode === "cad" ? "高级 CAD 输入" : "上传空户型图", done: Boolean((sourceMode === "cad" ? cadUpload : planPage) || project) },
    { label: "校正并锁定户型", done: Boolean(project?.verified) },
    { label: "选择整屋方案", done: Boolean(project?.active_scene_recipe_id) },
    { label: manualSafe ? "记录全景点位" : materialMode === "reference" ? "9-slot 机位" : "规划全景点位", done: materialMode === "reference" ? referenceGate.ready : validCaptures.length > 0 },
    { label: "生成认证母版", done: Boolean(project?.pano_captures?.some((pano) => pano.gate?.gate_pass === true)) },
    { label: "营销提案包", done: project?.professional?.marketing_proposal_status === "ready" },
  ];

  return (
    <div className="h-full overflow-y-auto p-5 max-[900px]:p-3">
      <div className="mx-auto max-w-[1580px] space-y-4 pb-16">
        <header className="rounded-2xl border border-primary/25 bg-gradient-to-r from-primary/10 via-card to-card p-5">
          <div className="flex items-start gap-3"><ImagePlus className="mt-1 text-primary" /><div><h1 className="text-xl font-extrabold">空户型图 → 全屋装修提案与 360° VR</h1><p className="mt-1 max-w-5xl text-sm leading-relaxed text-muted-foreground">上传 JPG / PNG / PDF 户型图，用 5–15 分钟校正结构与立面假设，选择一个现代暖木整屋方案，再生成 3–8 个相互一致的全景点位。CAD 仍保留为高级输入，但不再是默认工作流。</p><div className="mt-2 flex flex-wrap gap-2 text-[11px] font-bold"><span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-800">装修销售获客</span><span className="rounded-full bg-amber-100 px-2 py-1 text-amber-900">营销概念 · 非施工图</span><span className="rounded-full bg-sky-100 px-2 py-1 text-sky-800">认证母版 + 可选 AI 美化版</span></div></div></div>
        </header>

        {historyReadOnly && <div className="rounded-xl border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-950 dark:border-sky-900 dark:bg-sky-950/20 dark:text-sky-100"><b>历史只读快照</b> · 当前 3D 模型、机位、参数和效果图来自当时任务，不是项目最新版。历史证据不可修改；请在“历史与方案”中复制为新方案后再调整。</div>}

        <div className="grid grid-cols-6 gap-2 max-[1100px]:grid-cols-3 max-[620px]:grid-cols-2">
          {steps.map((step, index) => <div key={step.label} className={`rounded-xl border px-3 py-2.5 ${step.done ? "border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30" : "border-border bg-card"}`}><div className="text-[10px] font-bold text-muted-foreground">STEP {index + 1}</div><div className="mt-0.5 flex items-center gap-2 text-sm font-bold">{step.done ? <CheckCircle2 size={15} className="text-emerald-600" /> : <span className="h-3.5 w-3.5 rounded-full border" />}{step.label}</div></div>)}
        </div>

        <GeometryAuditHistoryStrip />

        <WholeHomeHistoryPanel
          project={project}
          style={style}
          lighting={lighting}
          prompt={prompt}
          floorPath={floor?.path || ""}
          styleRefPath={styleRef?.path || ""}
          aspectRatio={aspectRatio}
          paidEnabled={Boolean(manualCapabilities?.manual_paid)}
          onReplayLoaded={openReplaySnapshot}
          onBranchCreated={applyHistoryBranch}
          onProjectSelected={(value) => { void openHistoryProject(value); }}
        />

        {run && project && !historyReadOnly && run.project_id === project.project_id
          && (!manualSafe || activeReviewState?.round_status !== "review_complete") && (
          <WholeHomeHumanReview
            run={run}
            project={project}
            reviewState={activeReviewState}
            summary={learningSummary?.project_id === project.project_id ? learningSummary : null}
            loading={reviewLoading}
            onState={setReviewState}
            onRefresh={() => refreshHumanReview(run.run_id, run.project_id)}
            onRunStarted={(value) => {
              setRun(value);
              setReviewState(value.human_review || null);
              watchRun(value.run_id);
            }}
            onOpenRerunSettings={() => {
              document.getElementById("whole-home-generation-settings")?.scrollIntoView({ behavior: "smooth", block: "start" });
              toast.info("可勾选上方已保存机位后按机位重跑，或使用自动机位按钮全量重跑；两种方式都只会在你点击后开始。");
            }}
          />
        )}

        <section className="rounded-xl border border-border bg-panel p-4">
          <div className="mb-3 flex items-center gap-2"><UploadCloud size={18} className="text-primary" /><h2 className="font-extrabold">1. 输入户型与产品素材</h2></div>
          <div className="mb-3 grid grid-cols-2 gap-2 rounded-xl bg-muted p-1 max-[620px]:grid-cols-1">
            <button data-testid="whole-home-image-source-tab" className={`rounded-lg px-3 py-2 text-left text-sm ${sourceMode === "image" ? "bg-card font-bold shadow-sm" : "text-muted-foreground"}`} onClick={() => changeSourceMode("image")}><ImagePlus className="mr-2 inline" size={16} />空户型图（推荐主线）<div className="mt-0.5 text-[11px] font-normal">JPG / PNG / PDF → 自动草稿 → 5–15 分钟校正 → 整屋 VR 提案</div></button>
            <button data-testid="whole-home-cad-source-tab" className={`rounded-lg px-3 py-2 text-left text-sm ${sourceMode === "cad" ? "bg-card font-bold shadow-sm" : "text-muted-foreground"}`} onClick={() => changeSourceMode("cad")}><Cuboid className="mr-2 inline" size={16} />高级 CAD 输入<div className="mt-0.5 text-[11px] font-normal">已有 DWG / DXF 项目兼容入口；复杂施工 CAD 可能需要人工选择结构</div></button>
          </div>
          {sourceMode === "cad" ? <div className="space-y-3">
            <CadRuntimePanel status={cadStatus} error={cadStatusError} />
            <div className="grid grid-cols-3 gap-3 max-[900px]:grid-cols-1">
              <CadUploadBox value={cadUpload} busy={busy === "cad-upload"} onFile={uploadCad} />
              <UploadBox label="地板小样（产品阶段）" hint="reference 首轮不需要；后续 floor_sample 模式使用" value={floor} accept="image/png,image/jpeg,image/webp" busy={busy === "floor"} onFile={(file) => uploadAsset("floor", file)} />
              <UploadBox label="通用风格图（仅产品阶段可选）" hint="不能替代 9 个已审计 reference slot 资产" value={styleRef} accept="image/png,image/jpeg,image/webp" busy={busy === "style"} onFile={(file) => uploadAsset("style", file)} />
            </div>
            {!manualSafe && <label className="block space-y-1 text-xs font-semibold">官方效果参考 URL<Input value={referenceUrl} onChange={(event) => setReferenceUrl(event.target.value)} placeholder={DEFAULT_JUSTEASY_REFERENCE_URL} /></label>}
            <div className={`rounded-lg px-3 py-2 text-xs ${activeCadReadiness.ready ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}><b>{activeCadReadiness.code}</b> · {activeCadReadiness.message}</div>
            <div className="flex flex-wrap items-center gap-2"><Button disabled={!cadUpload?.path || !activeCadReadiness.ready || busy === "analysis"} onClick={analyzeCadWholeHome}>{busy === "analysis" ? <LoaderCircle className="animate-spin" /> : <ScanLine />}本地解析 CAD（零 AI 调用）</Button><span className="text-xs text-muted-foreground">{cadUpload && !cadUpload.path ? "当前显示历史项目绑定的源 CAD；点击上方文件卡可上传新文件并开始新的解析。" : "解析失败会保存 hash、实体清单、候选预览和完整报告，不会继续到机位或生图。"}</span></div>
          </div> : <>
            <div className="grid grid-cols-[minmax(0,2fr)_minmax(260px,1fr)] gap-3 max-[800px]:grid-cols-1">
              <UploadBox label="上传空户型图" hint="JPG / PNG / WebP / 单页或多页 PDF；建议短边 ≥1500px，并至少有一条已知尺寸" value={planPage} accept=".png,.jpg,.jpeg,.webp,.pdf" busy={busy === "plan"} onFile={uploadPlan} />
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-xs text-emerald-950"><b>适合第一版自动处理</b><div className="mt-2 space-y-1 leading-relaxed"><div>• 一居、两居、三居住宅平面</div><div>• 毛坯或空户型，墙门窗清晰</div><div>• 有尺寸标注或可信建筑面积</div><div>• 单层、天空向地面俯视</div></div><div className="mt-2 text-[11px] text-emerald-800">家具很多、严重透视、多户型混排会进入人工复核，不会直接假装成功。</div></div>
            </div>
            {plan && plan.page_count > 1 && <div className="mt-3 flex gap-2 overflow-x-auto">{plan.pages.map((page) => <button key={page.page} className={`rounded-lg border p-1 ${planPage?.path === page.path ? "border-primary" : "border-border"}`} onClick={() => setPlanPage(page)}><img src={api.imgUrl(page.thumb || page.url)} className="h-20 w-24 rounded object-cover" alt={`第 ${page.page} 页`} /><div className="text-[10px]">第 {page.page} 页</div></button>)}</div>}
            <div className="mt-3 flex flex-wrap items-center gap-2"><Button data-testid="whole-home-image-analyze" disabled={!planPage || busy === "analysis"} onClick={analyzeWholeHome}>{busy === "analysis" ? <LoaderCircle className="animate-spin" /> : <ScanLine />}识别并创建待校正户型</Button><span className="text-xs text-muted-foreground">识别只创建草稿，不产生效果图费用。结构、尺寸和门窗必须在下一步人工确认。</span></div>
            <details className="mt-3 rounded-lg border border-border bg-card p-3 text-xs"><summary className="cursor-pointer font-bold">旧版产品地板/风格参考（兼容入口）</summary><div className="mt-3 grid grid-cols-2 gap-3 max-[700px]:grid-cols-1"><UploadBox label="地板小样" hint="仅旧版产品替换任务使用" value={floor} accept="image/png,image/jpeg,image/webp" busy={busy === "floor"} onFile={(file) => uploadAsset("floor", file)} /><UploadBox label="风格参考" hint="仅旧版约束生图使用" value={styleRef} accept="image/png,image/jpeg,image/webp" busy={busy === "style"} onFile={(file) => uploadAsset("style", file)} /></div></details>
          </>}
          {!floor && recentFloorRun && <div className="mt-2 flex justify-end"><Button size="sm" variant="outline" onClick={() => { setFloor({ path: recentFloorRun.floor_path, url: recentFloorRun.floor_url, thumb: recentFloorRun.floor_url, name: recentFloorRun.floor_path.split(/[\\/]/).pop() || "最近地板小样" }); invalidateManualPreview(); }}><History />复用最近任务地板 · {recentFloorRun.floor_path.split(/[\\/]/).pop()}</Button></div>}
          {project && ["queued", "analyzing", "parsing_cad"].includes(project.status) && <div className="mt-3 flex items-center gap-2 rounded-lg bg-primary/5 px-3 py-2 text-xs text-primary"><LoaderCircle className="animate-spin" size={15} />{project.stage}</div>}
          {project?.status === "failed" && project.source_type !== "cad" && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{project.error}</div>}
        </section>

        {project?.source_type === "cad" && <CadDiagnostics project={project} operation={cadReparseOperation} onReparse={reparseCadProject} reparsing={busy === "cad-reparse"} advisory={cadAiAdvisory || project.cad_ai_advisories?.at(-1) || null} aiReviewing={busy === "cad-ai-assist"} onAiReview={reviewCadWithGemini} />}
        {project?.source_type === "cad" && !["queued", "analyzing", "parsing_cad"].includes(project.status) && <CadSpaceDraftEditor project={project} onSaved={async () => {
          const latest = await api.getWholeHomeProject(project.project_id);
          applyProject(latest);
          void loadHistory();
        }} />}
        {!manualSafe && project?.source_type === "cad" && <ReferenceContractPanel contract={project.reference_contract} gate={referenceGate} />}

        {!project && legacy.length > 0 && (
          <details className="rounded-xl border border-border bg-card p-4 text-xs">
            <summary className="cursor-pointer font-bold">历史兼容导入（仅用于把旧房间标注迁移到新整屋模型）</summary>
            <div className="mt-3 flex flex-wrap gap-2">{legacy.slice(0, 5).map((item) => <Button key={item.analysis_id} size="sm" variant="outline" disabled={busy === "analysis"} onClick={() => importLegacy(item)}>导入 {item.summary || item.analysis_id} · {item.rooms.length} 房间</Button>)}</div>
          </details>
        )}

        {project && draft?.schema_version === 2 && (
          <section className="rounded-xl border border-border bg-panel p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Box size={18} className="text-primary" /><h2 className="font-extrabold">2. 校准并复核完整整屋模型</h2>
              <span className="text-xs text-muted-foreground">版本 {project.revision} · {draft.walls.length} 墙 · {draft.rooms.length} 房间 · {draft.openings.length} 门窗 · {draft.fixed_objects.length} 固定物</span>
              {draft.geometry_report?.image_alignment_score != null && <span className="rounded-full bg-sky-100 px-2 py-1 text-[11px] font-bold text-sky-800">图纸对齐 {draft.geometry_report.image_alignment_score}/100</span>}
              <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${draft.semantic_report?.hard_errors?.length ? "bg-red-100 text-red-700" : "bg-emerald-100 text-emerald-700"}`}>{draft.semantic_report?.hard_errors?.length ? `语义待修正 ${draft.semantic_report.hard_errors.length}` : "语义规则通过"}</span>
              {!manualSafe && !cadReadOnly && <Button size="sm" variant="outline" disabled={busy === "analysis" || reviewGenerationLocked} title={reviewGenerationLocked ? "请先完成当前轮人工评审" : undefined} onClick={reanalyzeCurrentWholeHome}><ScanLine />AI 拓扑重建</Button>}
              {!manualSafe && !cadReadOnly && <Button size="sm" variant="outline" disabled={busy === "semantic" || reviewGenerationLocked} title={reviewGenerationLocked ? "请先完成当前轮人工评审" : undefined} onClick={rebuildSemanticLayout}>{busy === "semantic" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}重建语义灰模</Button>}
              {cadReadOnly && <span className="rounded-full bg-amber-100 px-2 py-1 text-[11px] font-bold text-amber-800">CAD 原始墙/门/窗只读 · 空间与语义在上方人工确认</span>}
              {project.verified && !dirty && <span className="ml-auto rounded-full bg-emerald-100 px-2 py-1 text-xs font-bold text-emerald-700">整屋几何已锁定</span>}
            </div>
            {geometryContract?.required && (
              <div className={`mb-3 rounded-xl border p-3 text-xs ${geometryReady ? "border-emerald-300 bg-emerald-50/70 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100" : "border-amber-300 bg-amber-50/70 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <b>Plan-to-3D Correspondence Lock v1</b>
                  <span className="rounded-full bg-white/70 px-2 py-0.5 font-mono dark:bg-black/20">{geometryContract.input_grade}</span>
                  <span className={`rounded-full px-2 py-0.5 font-bold ${geometryReady ? "bg-emerald-200 text-emerald-900" : "bg-amber-200 text-amber-950"}`}>
                    {geometryReady ? "对应验收通过" : `阻断：${geometryContract.production_readiness.code}`}
                  </span>
                  {geometryContract.manifest.manifest_hash && <span className="font-mono text-[10px] opacity-75">mesh {geometryContract.manifest.manifest_hash.slice(0, 12)}</span>}
                  {geometryContract.acceptance.report_hash && <span className="font-mono text-[10px] opacity-75">report {geometryContract.acceptance.report_hash.slice(0, 12)}</span>}
                </div>
                {!geometryReady && (
                  <>
                    <div className="mt-2 space-y-1">
                      {geometryContract.production_readiness.reasons.slice(0, 6).map((reason, index) => (
                        <div key={`${reason.code}-${index}`}>• {reason.code}：{reason.message}</div>
                      ))}
                    </div>
                    {rasterGeometryProject && !geometryContract.registration.registration_hash && project?.floorplan_url && (
                      <RasterScaleRegistration
                        floorplanUrl={api.imgUrl(project.floorplan_url)}
                        busy={busy === "raster-registration"}
                        onRegister={registerRasterSource}
                      />
                    )}
                    {rasterGeometryProject && geometryContract.registration.registration_hash && (
                      <div className="mt-3 rounded-lg border border-current/20 bg-white/50 p-3 dark:bg-black/10">
                        <div className="font-bold">普通户型图逐项复核</div>
                        <div className="mt-1 text-muted-foreground">
                          服务端墙线反投影 p95：{geometryContract.raster_alignment_metrics?.wall_centerline_p95_m != null
                            ? `${Number(geometryContract.raster_alignment_metrics.wall_centerline_p95_m).toFixed(3)} m`
                            : "待量测"}
                          {geometryContract.raster_alignment_metrics?.wall_ink_support_ratio != null
                            ? ` · 墙线墨迹支持 ${(Number(geometryContract.raster_alignment_metrics.wall_ink_support_ratio) * 100).toFixed(1)}%`
                            : ""}
                        </div>
                        <div className="mt-2 grid gap-2 md:grid-cols-3">
                          <label className="flex items-start gap-2 rounded-lg border border-border p-2"><Switch checked={rasterRoomsReviewed} onCheckedChange={setRasterRoomsReviewed} /><span>已逐房叠加检查，房间边界与原图一致，无漏房或多余房间。</span></label>
                          <label className="flex items-start gap-2 rounded-lg border border-border p-2"><Switch checked={rasterOpeningsReviewed} onCheckedChange={setRasterOpeningsReviewed} /><span>已逐个核对门窗，模型无漏项、错项或额外开口。</span></label>
                          <label className="flex items-start gap-2 rounded-lg border border-border p-2"><Switch checked={rasterNoUnresolved} onCheckedChange={setRasterNoUnresolved} /><span>所有红色不确定项均已处理，本轮没有遗留待复核结构。</span></label>
                        </div>
                      </div>
                    )}
                    <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] gap-2 max-[720px]:grid-cols-1">
                      <Textarea
                        value={geometryReviewNote}
                        onChange={(event) => setGeometryReviewNote(event.target.value)}
                        placeholder="复核说明，例如：已对照原 CAD 的墙双线、房间边界、门窗位置，并确认 2.8m 墙高为本项目建模假设。"
                        className="min-h-20 bg-white/80 text-xs dark:bg-black/20"
                      />
                      <div className="flex min-w-64 flex-col justify-between gap-2 rounded-lg border border-current/20 bg-white/50 p-2 dark:bg-black/10">
                        <label className="flex items-start gap-2 leading-relaxed">
                          <Switch checked={geometryAssumptionsConfirmed} onCheckedChange={setGeometryAssumptionsConfirmed} />
                          <span>我已逐项对照源图，并确认当前墙高、楼板和未由 2D 图纸证明的竖向假设。</span>
                        </label>
                        <Button
                          size="sm"
                          disabled={busy === "geometry-acceptance" || !geometryAssumptionsConfirmed || !rasterReviewComplete || geometryReviewNote.trim().length < 3 || !geometryContract.registration.registration_hash}
                          onClick={commitGeometryAcceptance}
                        >
                          {busy === "geometry-acceptance" ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}
                          运行并提交对应验收
                        </Button>
                      </div>
                    </div>
                  </>
                )}
                {geometryReady && <div className="mt-2">浏览器灰模与服务端球面渲染均读取同一 GeometryManifest；模型、配准或内核变化后本锁会自动失效。</div>}
              </div>
            )}
            {(draft.uncertainties?.length || 0) > 0 && <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">AI 不确定项：{draft.uncertainties?.join("；")}</div>}
            <WholeHomeStudio
              ref={studioRef}
              model={draft} floorplanUrl={api.imgUrl(project.floorplan_url)} aspectRatio={activeAspectRatio}
              verified={project.verified && !dirty} busy={busy === "capture"}
              geometryManifest={geometryManifest}
              sceneInstances={activeSceneRecipe?.instances || []}
              cadGeometryReadOnly={cadReadOnly}
              manualSafe={manualSafe}
              referenceMode={!manualSafe && materialMode === "reference"}
              referenceContract={project.reference_contract}
              completedReferenceSlotIds={referenceGate.captureGroups.map((group) => group.slot_id || "").filter(Boolean)}
              onChange={editModel} onSaveCapture={saveCapture} onSavePanoCapture={savePanoCapture}
              onGenerateCameraCandidates={generateCameraCandidates}
              onRankAutoCameras={rankAutoCameras} onAutoCaptureComplete={finishAutoCapture}
            />
            {(project.pano_captures?.length || 0) > 0 && (
              <div className="mt-3 rounded-xl border border-border bg-card p-3 text-xs">
                <div className="mb-2 flex items-center gap-2 font-bold"><Camera />球面全景热点（360°）</div>
                <div className="space-y-2">
                  {(project.pano_captures || []).map((pano) => {
                    const captureId = pano.capture_id || pano.manifest.capture_id || pano.pano_id;
                    const gate = pano.gate as WholeHomePanoGate | undefined;
                    const failures = gate?.failures || [];
                    const repairEligible = !pano.repaired_rgb_url
                      && pano.edit_engine !== "flux-canny"
                      && gate?.gate_pass === false && failures.length > 0
                      && failures.every((item) => item === "wrap_seam" || item === "cube_edges");
                    const paidPreviewMatches = panoPaidCaptureId === captureId
                      && panoPaidPreview?.source_hash === panoSourceHash(pano);
                    const url = pano.repaired_rgb_url || pano.edited_rgb_url
                      || pano.channel_urls?.rgb_erp || pano.rgb_url;
                    return (
                      <div key={captureId} className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border p-2 ${pano.active === false ? "border-border bg-muted/40 opacity-70" : "border-border"}`}>
                        <div>
                          <b>{pano.pano_id}</b>
                          <span className="ml-2 font-mono text-[10px] text-muted-foreground">rev {pano.capture_revision || pano.manifest.capture_revision || 1}</span>
                          <div className="text-muted-foreground">
                            状态：{pano.status}
                            {gate ? ` · ${gate.gate_level || "P0"} ${gate.gate_pass ? "通过" : "未通过"}` : ""}
                            {gate?.not_evaluable?.length ? ` · 不可评估 ${gate.not_evaluable.join(",")}` : ""}
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-1">
                          <Button size="sm" variant="outline" disabled={!url}
                            onClick={() => {
                              setPanoViewerId(pano.pano_id); setPanoViewerCaptureId(captureId);
                              setPanoViewerUrl(url || "");
                            }}>
                            查看全景
                          </Button>
                          {pano.active !== false && !pano.edited_rgb_url && (
                            <>
                              <Button size="sm" variant="outline" disabled={Boolean(panoBusy)}
                                onClick={() => void materializePanoLocally(pano)}>
                                {panoBusy === "materialize" ? <LoaderCircle className="animate-spin" /> : <ScanLine />}本地材质化（0 元）
                              </Button>
                              <Button size="sm" variant="outline" disabled={Boolean(panoBusy)}
                                onClick={() => void preparePanoPaidEdit(pano)}>
                                {panoBusy === "preview" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}付费 AI 预览
                              </Button>
                            </>
                          )}
                          {(pano.edited_rgb_url || pano.repaired_rgb_url) && (
                            <Button size="sm" variant="outline" disabled={Boolean(panoBusy)}
                              onClick={() => void runPanoGate(pano)}>
                              {panoBusy === "gate" ? <LoaderCircle className="animate-spin" /> : <ScanLine />}本地 P0 门禁
                            </Button>
                          )}
                          {repairEligible && !paidPreviewMatches && (
                            <Button size="sm" variant="outline" disabled={Boolean(panoBusy)}
                              title="免费恢复执行 edit 时的原确认合同，不调用 provider"
                              onClick={() => void preparePanoPaidEdit(pano)}>
                              {panoBusy === "preview" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}恢复修缝确认
                            </Button>
                          )}
                          {repairEligible && paidPreviewMatches && (
                            <Button size="sm" variant="outline"
                              disabled={Boolean(panoBusy)
                                || panoConfirmation !== panoPaidPreview?.confirmation_phrase}
                              onClick={() => void repairPanoSeam(pano)}>
                              {panoBusy === "repair" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}唯一一次修缝
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
                {panoPaidPreview && (() => {
                  const capture = (project.pano_captures || []).find((row) =>
                    (row.capture_id || row.manifest.capture_id || row.pano_id) === panoPaidCaptureId);
                  if (!capture) return null;
                  return (
                    <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-amber-950">
                      <div className="font-extrabold">{capture.edited_rgb_url
                        ? "修缝付费确认 · 原 edit 合同已恢复，本次恢复未调用 provider"
                        : "全景付费预览 · 尚未调用 provider"}</div>
                      <div className="mt-1 break-all font-mono text-[10px]">
                        {panoPaidPreview.provider} / {panoPaidPreview.endpoint}<br />
                        engine {panoPaidPreview.engine} · model {panoPaidPreview.model_id} · snapshot_locked={String(panoPaidPreview.snapshot_locked)}<br />
                        output {panoPaidPreview.output_size} · prompt SHA256 {panoPaidPreview.edit_prompt_sha256}
                      </div>
                      <div className="mt-2">硬上限：1 次 edit；本引擎 repair 上限 {panoPaidPreview.caps.repair_calls} 次。FLUX Canny 使用环形上下文，不混入 GPT 修缝链路；调用失败也消耗对应额度。</div>
                      <div className="mt-2 rounded-lg bg-white/70 p-2 font-mono">{panoPaidPreview.confirmation_phrase}</div>
                      <div className="mt-2 flex flex-wrap items-end gap-2">
                        <label className="min-w-[320px] flex-1 space-y-1 font-semibold">逐字输入动态短语
                          <Input value={panoConfirmation} onChange={(event) => setPanoConfirmation(event.target.value)} />
                        </label>
                        {!capture.edited_rgb_url && (
                          <Button disabled={Boolean(panoBusy) || panoConfirmation !== panoPaidPreview.confirmation_phrase}
                            onClick={() => void commitPanoEdit(capture)}>
                            {panoBusy === "edit" ? <LoaderCircle className="animate-spin" /> : <Play />}确认并执行唯一一次 edit
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
            {(hardErrors.length > 0 || warnings.length > 0) && <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900"><b>本地几何检查：{hardErrors.length} 个必须修正 · {warnings.length} 个锁定时确认</b>{[...hardErrors, ...warnings].slice(0, 10).map((issue, index) => <div key={`${issue.code}-${index}`} className="mt-1">• {issue.message}</div>)}</div>}
            {semanticHardErrors.length > 0 && <div className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-xs text-red-800"><b>本地语义检查：{semanticHardErrors.length} 个必须修正</b>{semanticHardErrors.slice(0, 10).map((issue, index) => <div key={`${issue.code}-${index}`} className="mt-1">• {issue.message}</div>)}</div>}
            <div className="mt-4 flex flex-wrap justify-end gap-2">{!cadReadOnly && <Button variant="outline" disabled={!dirty || busy === "save"} onClick={saveModelOnly}>{busy === "save" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}保存草稿</Button>}<Button disabled={historyReadOnly || busy === "verify" || hardErrors.length > 0 || semanticHardErrors.length > 0} onClick={lockModel}>{busy === "verify" ? <LoaderCircle className="animate-spin" /> : <LockKeyhole />}{historyReadOnly ? "历史快照只读" : cadReadOnly ? project.verified ? "CAD 硬门禁已锁定" : "锁定已通过的 CAD 事实" : dirty ? "保存并锁定整屋几何" : project.verified ? "重新锁定当前版本" : "锁定整屋几何与语义"}</Button></div>
          </section>
        )}

        {project && draft?.schema_version === 2 && !historyReadOnly && (
        <WholeHomeProfessionalProposal project={project} onProjectUpdate={applyProject}
          onActiveRecipeChange={setActiveSceneRecipe} />
        )}

        {project?.verified && !dirty && (
          <section className="rounded-xl border border-border bg-panel p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2"><Camera size={18} className="text-primary" /><h2 className="font-extrabold">3. {manualSafe ? "人工机位记录" : "自动机位记录与人工复核"}</h2><span className="text-xs text-muted-foreground">{manualSafe ? "灰模中保存的每个机位都会永久记录 RGB、深度、法线、边线和语义五通道；保存绝不触发生图。" : "一键流程会永久保存候选预览、AI 入选理由及每个机位的 RGB、深度、法线、边线、语义五通道"}</span></div>
            {validCaptures.length ? <div className="grid grid-cols-4 gap-2 max-[1000px]:grid-cols-2 max-[620px]:grid-cols-1">{validCaptures.map((capture) => <label key={capture.capture_id} className={`overflow-hidden rounded-xl border ${selectedCaptures.includes(capture.capture_id) ? "border-primary bg-primary/5" : "border-border bg-card"}`}><img src={api.imgUrl(capture.rgb_url)} alt={capture.camera.name} className="aspect-[4/3] w-full object-cover" /><div className="flex items-center gap-2 p-2 text-xs"><Switch disabled={materialMode === "reference"} checked={materialMode === "reference" ? Boolean(capture.reference_slot_id || capture.camera.reference_slot_id) : selectedCaptures.includes(capture.capture_id)} onCheckedChange={(checked) => { setManualPreview(null); setManualConfirmation(""); setSelectedCaptures((rows) => manualSafe ? (checked ? [capture.capture_id] : []) : checked ? [...rows, capture.capture_id] : rows.filter((id) => id !== capture.capture_id)); }} /><b>{capture.camera.name}</b><span className="ml-auto text-muted-foreground">{capture.camera.focal_length_mm}mm · {capture.reference_slot_id || capture.camera.reference_slot_id || (capture.is_primary ? "主机位" : `备用 ${capture.pool_rank}`)}</span></div></label>)}</div> : <div className="rounded-lg border border-dashed border-border p-5 text-center text-sm text-muted-foreground">{materialMode === "reference" ? "尚无通过 slot 资产、参考scene身份、CAD语义相对落点、碰撞/可见性、焦距、画框与 must-show 全量验证的 4:3 机位；不会用旧 24/28mm 机位凑数。" : manualSafe ? `尚无 ${aspectRatio} 机位。请在灰模中手动选择视角并保存。` : `尚无 ${aspectRatio} 机位。直接在下方点击“一键自动选机位并生成”；如需特殊构图，也可在上方灰模手动保存机位。`}</div>}
          </section>
        )}

        {project?.verified && !dirty && (
          <section id="whole-home-generation-settings" className="scroll-mt-4 rounded-xl border border-border bg-panel p-4">
            <div className="mb-3 flex items-center gap-2"><Sparkles size={18} className="text-primary" /><h2 className="font-extrabold">4. 设置约束生成</h2></div>
            <div className="mb-3 grid grid-cols-3 gap-2 rounded-xl bg-muted p-1 max-[900px]:grid-cols-1">
              <button data-testid="whole-home-style-pack-mode" className={`rounded-lg px-3 py-2 text-left text-sm ${materialMode === "style_pack" ? "bg-card font-bold shadow-sm" : "text-muted-foreground"}`} onClick={() => { setMaterialMode("style_pack"); setSelectedCaptures([]); invalidateManualPreview(); }}><Sparkles className="mr-2 inline" size={16} />固定整屋风格（推荐）<div className="mt-0.5 text-[11px] font-normal">无需地板样品；必须绑定已锁定 SceneRecipe，固定现代暖木自然与全屋一致性</div></button>
              <button className={`rounded-lg px-3 py-2 text-left text-sm ${materialMode === "floor_sample" ? "bg-card font-bold shadow-sm" : "text-muted-foreground"}`} onClick={() => { setMaterialMode("floor_sample"); setSelectedCaptures([]); invalidateManualPreview(); }}><ImagePlus className="mr-2 inline" size={16} />指定产品地板<div className="mt-0.5 text-[11px] font-normal">仅当要展示某款真实地板产品时上传小样；不会改变已锁定布局</div></button>
              {!manualSafe && project.source_type === "cad" ? <button className={`rounded-lg px-3 py-2 text-left text-sm ${materialMode === "reference" ? "bg-card font-bold shadow-sm" : "text-muted-foreground"}`} onClick={() => { setMaterialMode("reference"); setAspectRatio("4:3"); setResolution("4K"); setModelKeys(["b2", "pro"]); setSelectedCaptures([]); }}><Eye className="mr-2 inline" size={16} />Reference benchmark<div className="mt-0.5 text-[11px] font-normal">高级审计模式：固定 9 slots × B2/Pro × 4:3/4K</div></button> : <div className="rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground"><b>当前产品边界</b><div className="mt-1">空户型图主线不需要参考样板间；风格由版本化 StylePack 管理。</div></div>}
            </div>
            <div className="grid grid-cols-5 gap-3 max-[1200px]:grid-cols-3 max-[800px]:grid-cols-2 max-[620px]:grid-cols-1">
              <label className="space-y-1 text-xs font-semibold">统一风格<Input value={style} onChange={(event) => { setStyle(event.target.value); invalidateManualPreview(); }} /></label>
              <label className="space-y-1 text-xs font-semibold">统一光照<Input value={lighting} onChange={(event) => { setLighting(event.target.value); invalidateManualPreview(); }} /></label>
              <label className="space-y-1 text-xs font-semibold">画幅<select disabled={materialMode === "reference"} className={`${inputClass} w-full`} value={activeAspectRatio} onChange={(event) => { setAspectRatio(event.target.value as AspectRatio); setSelectedCaptures([]); invalidateManualPreview(); }}><option>4:3</option><option>16:9</option><option>3:4</option><option>9:16</option></select></label>
              <label className="space-y-1 text-xs font-semibold">分辨率<select disabled={manualSafe || materialMode === "reference"} className={`${inputClass} w-full`} value={manualSafe ? "2K" : materialMode === "reference" ? "4K" : resolution} onChange={(event) => setResolution(event.target.value as "2K" | "4K")}><option>2K</option>{!manualSafe && <option>4K</option>}</select></label>
              <div className="rounded-lg border border-border bg-card p-3 text-xs"><b>{manualSafe ? "人工单机位合同" : materialMode === "reference" ? "9-slot 机位硬门禁" : "自动机位策略"}</b><div className="mt-1 text-muted-foreground">{manualSafe ? `请在灰模中明确选择视角；本轮只允许 1 个已确认机位。${materialMode === "style_pack" ? "机位必须来自当前锁定方案。" : ""}` : materialMode === "reference" ? "每个 slot 必须绑定已审计视觉资产与scene身份，再按 CAD 房间语义 + must-show 锚点推断相对落点。" : "每房 1 个主机位 + 最多 2 个完整备用；全部机位绑定同一 scene_hash。"}</div></div>
              <div className="col-span-2 space-y-1 text-xs font-semibold max-[620px]:col-span-1"><span>生成模型{manualSafe ? "（每次只选一个）" : materialMode === "reference" ? "（合同固定双模型）" : "（可同时选择）"}</span><div className="grid grid-cols-2 gap-2">{(["b2", "pro"] as const).map((key) => <label key={key} className={`flex items-center gap-2 rounded-lg border px-3 py-2 ${materialMode === "reference" || modelKeys.includes(key) ? "border-primary bg-primary/5" : "border-border bg-card"}`}><Switch disabled={materialMode === "reference"} checked={materialMode === "reference" || modelKeys.includes(key)} onCheckedChange={(checked) => toggleModel(key, checked)} />{modelLabel(key)}</label>)}</div></div>
              <label className="space-y-1 text-xs font-semibold">候选数<select disabled={manualSafe || materialMode === "reference"} className={`${inputClass} w-full`} value={manualSafe ? 1 : candidates} onChange={(event) => setCandidates(Number(event.target.value) as 1 | 2)}><option value={1}>1 张</option>{!manualSafe && <option value={2}>2 张（仅人工旧模式）</option>}</select></label>
              <div className="rounded-lg border border-border bg-card p-3 text-xs"><div className="text-muted-foreground">调用预估</div><div className="mt-1 text-lg font-extrabold">{estimatedResults} 个逻辑结果</div><div className="text-muted-foreground">{materialMode === "reference" ? `${referenceGate.activeSlots} active slots × 2 models；合同资产/节点/机位缺一即零调用。` : `理想 ${estimatedResults * 2} 次生图；重试与 QA 上限由后端账本记录。`}</div></div>
            </div>
            <label className="mt-3 block space-y-1 text-xs font-semibold">客户需求 / 额外提示词<Textarea className="min-h-24" value={prompt} onChange={(event) => { setPrompt(event.target.value); invalidateManualPreview(); }} placeholder="例如：年轻家庭、克制真实、保持开阔动线；不改变任何墙体和门窗。" /></label>
            <div className="mt-3 rounded-lg border border-primary/20 bg-primary/5 p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="max-w-4xl text-xs leading-relaxed text-muted-foreground"><b className="text-foreground">{manualSafe ? "手动安全提交：" : materialMode === "reference" ? "Reference fail-closed：" : "推荐主流程："}</b>{manualSafe ? "保存灰模机位不会生图。每次只选 1 个机位、1 个模型和 1 个候选，固定 2K；先生成只读 hash 预览，再输入动态短语提交。" : materialMode === "reference" ? "九张通用缩略图、单张 style_ref 或模型自评都不能替代已哈希的 slot 资产、scene身份与CAD相对落点验证。公开页没有绝对坐标，系统不会虚构。当前开发期无人循环由外部 Codex 编排；此页面不会调用不存在的自动循环接口。" : "本地几何先排除墙外、贴墙与固定物碰撞机位；Gemini 只能从候选中复排构图，不能篡改坐标。候选预览、评分、理由和最终五通道都会保留。"}</div>
                {manualSafe ? <Button disabled={historyReadOnly || Boolean(busy) || reviewGenerationLocked || !activeMaterialGate.ready || selectedCaptures.length !== 1 || modelKeys.length !== 1} title={historyReadOnly ? "请先复制为新方案" : reviewGenerationLocked ? "请先完成当前轮人工评审" : !activeMaterialGate.ready ? activeMaterialGate.message : undefined} onClick={generate}>{busy === "preview" ? <LoaderCircle className="animate-spin" /> : <ScanLine />}生成只读付费预览</Button> : <Button disabled={historyReadOnly || Boolean(busy) || reviewGenerationLocked || (materialMode === "reference" ? (!referenceGate.ready && !referencePreflight.ready) : !activeMaterialGate.ready)} title={historyReadOnly ? "请先复制为新方案" : materialMode === "reference" ? (referenceGate.ready ? undefined : referencePreflight.message) : !activeMaterialGate.ready ? activeMaterialGate.message : reviewGenerationLocked ? "请先完成并放行当前轮人工评审" : undefined} onClick={materialMode === "reference" ? (referenceGate.ready ? generate : autoGenerate) : autoGenerate}>{busy === "auto" || busy === "capture" || busy === "generate" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}{materialMode === "reference" ? referenceGate.ready ? "Paid gate 已通过：生成 B2 + Pro" : referencePreflight.ready ? "自动生成 9-slot CPU 灰模证据" : "Reference 本地预检未通过" : `一键记录当前方案机位 ${modelKeys.map(modelLabel).join(" + ")}`}</Button>}
              </div>
              {!activeMaterialGate.ready && <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900">{activeMaterialGate.code} · {activeMaterialGate.message}</div>}
              {reviewGenerationLocked && <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">当前轮等待人工评审：所有生图与 QA 调用均已锁定。完成逐图标记并点击“本轮评审完成”后才会解锁。</div>}
              {!manualSafe && materialMode === "floor_sample" && <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-primary/10 pt-2 text-[11px] text-muted-foreground"><span>人工高级纠偏：可在上方灰模保存特殊机位，再在此勾选生成。</span><Button size="sm" variant="outline" disabled={!floor || !selectedCaptures.length || Boolean(busy) || reviewGenerationLocked} title={reviewGenerationLocked ? "请先完成并放行当前轮人工评审" : undefined} onClick={generate}>{busy === "generate" ? <LoaderCircle className="animate-spin" /> : <Play />}按已勾选机位生成</Button></div>}
              {materialMode === "style_pack" && selectedCaptures.length > 0 && <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-primary/10 pt-2 text-[11px] text-muted-foreground"><span>当前已选择 {selectedCaptures.length} 个绑定方案的机位；scene {activeSceneRecipe?.scene_hash.slice(0, 12) || "未锁定"}</span>{!manualSafe && <Button size="sm" variant="outline" disabled={!activeMaterialGate.ready || Boolean(busy) || reviewGenerationLocked} onClick={generate}><Play />按已勾选机位生成</Button>}</div>}
            </div>
            {manualSafe && manualPreview && <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-xs text-amber-950">
              <div className="font-extrabold">提交前预览 · 尚未调用 provider</div>
              <div className="mt-1 break-all font-mono text-[10px]">preview {manualPreview.preview_id}<br />SHA256 {manualPreview.preview_sha256}</div>
              <div className="mt-2">上限：{manualPreview.caps.image_calls} 次生图 / {manualPreview.caps.qa_calls} 次 QA；实际输入文件 hash 已由后端绑定，提交时会全部复算。</div>
              <div className="mt-2 rounded-lg bg-white/70 p-2 font-mono">{manualPreview.confirmation_phrase}</div>
              <div className="mt-2 flex flex-wrap items-end gap-2"><label className="min-w-[320px] flex-1 space-y-1 font-semibold">逐字输入上方动态短语<Input value={manualConfirmation} onChange={(event) => setManualConfirmation(event.target.value)} /></label><Button disabled={!manualCapabilities?.manual_paid || busy === "commit" || manualConfirmation !== manualPreview.confirmation_phrase} onClick={commitManualPreview}>{busy === "commit" ? <LoaderCircle className="animate-spin" /> : <Play />}确认并提交受限任务</Button></div>
              {!manualCapabilities?.manual_paid && <div className="mt-2 font-bold text-amber-800">当前服务未使用 -AllowPaid 启动，因此提交按钮保持关闭；预览本身不会产生费用。</div>}
            </div>}
          </section>
        )}

        {run && <Results run={run} project={project} onRetryQa={retryUnavailableQa} retryingQa={busy === "qa"} reviewLocked={reviewGenerationLocked} manualSafe={manualSafe} />}

      </div>

      <Dialog open={!!panoViewerUrl} onOpenChange={(open) => { if (!open) setPanoViewerUrl(""); }}>
        <DialogContent className="max-h-[96vh] max-w-[98vw] overflow-y-auto sm:max-w-[min(96vw,1100px)]">
          <div className="mb-2 text-sm font-bold">球面全景验收 · {panoViewerId}</div>
          {panoViewerUrl && (() => {
            const capture = (project?.pano_captures || []).find((row) =>
              (row.capture_id || row.manifest.capture_id || row.pano_id) === panoViewerCaptureId);
            const gate = capture?.gate as WholeHomePanoGate | undefined;
            return <PanoViewer erpUrl={api.imgUrl(panoViewerUrl)} mode="review"
              initialYawDeg={Number(capture?.manifest?.heading_deg || 0)}
              onChecklistResult={gate?.gate_pass ? (result) => void submitPanoReview(result) : undefined}
              reviewBlockedReason={gate?.gate_pass
                ? ""
                : "仅可浏览；请先让候选通过本地 P0 RGB/结构门禁，再提交人工验收。"} />;
          })()}
        </DialogContent>
      </Dialog>
    </div>
  );
}
