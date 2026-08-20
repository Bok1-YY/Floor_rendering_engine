"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock3, Copy, ExternalLink, GitBranch, History, Images, LoaderCircle, Play, RefreshCw, Sparkles, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { GeometryAuditModelViewer } from "@/components/GeometryAuditModelViewer";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type {
  RecordEntry,
  RecordFile,
  WholeHomeProject,
  WholeHomeProjectHistory,
  WholeHomeRunReplay,
  WholeHomeVariantBatch,
} from "@/lib/types";

type AspectRatio = "4:3" | "16:9" | "3:4" | "9:16";

interface Props {
  project: WholeHomeProject | null;
  style: string;
  lighting: string;
  prompt: string;
  floorPath: string;
  styleRefPath: string;
  aspectRatio: AspectRatio;
  paidEnabled: boolean;
  onReplayLoaded: (replay: WholeHomeRunReplay) => void;
  onBranchCreated: (project: WholeHomeProject, replay: WholeHomeRunReplay) => void;
  onProjectSelected: (project: WholeHomeProject) => void;
}

const terminalBatch = new Set(["done", "partial", "failed", "cancelled"]);

function geometryAuditStatusTone(status: string) {
  if (status === "passed") return "bg-emerald-100 text-emerald-800";
  if (status === "pending") return "bg-sky-100 text-sky-800";
  if (status === "invalidated") return "bg-amber-100 text-amber-900";
  return "bg-red-100 text-red-800";
}

function geometryAuditStatusLabel(status: string, detailed = false) {
  if (status === "passed") return detailed ? "自动验收通过" : "通过";
  if (status === "pending") return detailed ? "待独立真值核对" : "待核对";
  if (status === "invalidated") return "旧坐标验收已撤销";
  return detailed ? "自动验收失败" : "失败";
}

export function GeometryAuditHistoryStrip() {
  const [audits, setAudits] = useState<Array<{ file: RecordFile; entry: RecordEntry }>>([]);
  const [loading, setLoading] = useState(false);
  const [selectedAudit, setSelectedAudit] = useState<{ file: RecordFile; entry: RecordEntry } | null>(null);

  const loadAudits = useCallback(async () => {
    setLoading(true);
    try {
      setAudits(await api.listGeometryAudits(40));
    } catch (error) {
      toast.error(`Plan-to-3D 验收历史加载失败：${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadAudits(); }, [loadAudits]);

  const selectedMetadata = selectedAudit?.entry.geometry_audit;
  const selectedRecordId = selectedAudit?.entry.id || "";
  const selectedModel = selectedMetadata?.artifacts.find((artifact) =>
    artifact.artifact_id === "truth_gray_model.obj" && artifact.available,
  );
  const selectedPreviews = (selectedAudit?.entry.results || []).filter((row) => row.result_thumb || row.result_url);

  return <>
    <section className="rounded-xl border border-border bg-panel p-4" data-testid="geometry-audit-history-strip">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2"><History size={19} className="text-primary" /><div><h2 className="font-extrabold">Plan-to-3D 阶段验收</h2><p className="text-xs text-muted-foreground">独立只读测试案例；点击卡片查看该案例自己的户型图、3D 灰模、指标和证据，不会覆盖当前用户项目。</p></div></div>
        <div className="flex gap-2"><Button size="sm" variant="outline" disabled={loading} onClick={() => void loadAudits()}>{loading ? <LoaderCircle className="animate-spin" /> : <RefreshCw size={14} />}刷新阶段记录</Button><Button size="sm" variant="outline" onClick={() => { window.location.href = "/records"; }}><Images size={14} />全部验收记录</Button></div>
      </div>
      {audits.length ? <div className="mt-3 flex gap-3 overflow-x-auto pb-1">
        {audits.map(({ file, entry }) => {
          const audit = entry.geometry_audit!;
          const preview = entry.results?.find((row) => row.result_thumb || row.result_url);
          const metrics = audit.channels.reduce((sum, channel) => sum + channel.metrics.length, 0);
          const recoveryNotes = audit.source.extraction_warnings?.length || 0;
          return <button
            key={`${file.json_path}:${entry.id}`}
            type="button"
            data-testid={`geometry-audit-card-${entry.id}`}
            aria-label={`打开 ${audit.title} 验收详情`}
            onClick={() => setSelectedAudit({ file, entry })}
            className="group min-w-72 max-w-80 overflow-hidden rounded-lg border border-border bg-card text-left transition hover:-translate-y-0.5 hover:border-primary hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            {preview?.result_thumb || preview?.result_url ? <img src={api.imgUrl(preview.result_thumb || preview.result_url || "")} alt={`${audit.title} 验收预览`} className="h-28 w-full bg-muted object-cover" /> : null}
            <div className="p-3 text-xs"><div className="flex items-center justify-between gap-2"><b className="truncate text-sm">{audit.title}</b><span className={`rounded-full px-2 py-0.5 ${geometryAuditStatusTone(audit.status)}`}>{audit.level} · {geometryAuditStatusLabel(audit.status)}</span></div><div className="mt-1 text-muted-foreground">{audit.source.storey?.name || "楼层自动选择"} · {metrics} 项指标 · {audit.integrity.checked_count} 个证据{recoveryNotes ? ` · ${recoveryNotes} 条恢复依据` : ""}</div><div className="mt-2 flex items-center justify-between gap-2"><span className="truncate font-mono text-[10px] text-muted-foreground" title={audit.audit_hash}>{audit.audit_hash.slice(0, 20)}</span><span className="flex items-center gap-1 font-bold text-primary"><Play size={12} />点击查看</span></div></div>
          </button>;
        })}
      </div> : <div className="mt-3 rounded-lg border border-dashed border-border p-5 text-center text-xs text-muted-foreground">{loading ? "正在读取阶段记录…" : "暂无 Plan-to-3D 阶段记录"}</div>}
    </section>

    <Dialog open={Boolean(selectedAudit)} onOpenChange={(open) => { if (!open) setSelectedAudit(null); }}>
      <DialogContent className="max-h-[96vh] max-w-[98vw] overflow-y-auto sm:max-w-[min(96vw,1480px)]">
        {selectedAudit && selectedMetadata ? <div className="space-y-4" data-testid="geometry-audit-detail-dialog">
          <div className="flex flex-wrap items-start justify-between gap-3 pr-8">
            <div>
              <div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-2.5 py-1 text-xs font-black ${geometryAuditStatusTone(selectedMetadata.status)}`}>{selectedMetadata.level} · {geometryAuditStatusLabel(selectedMetadata.status, true)}</span><span className="text-xs text-muted-foreground">{selectedMetadata.source.dataset} · {selectedMetadata.source.license}</span></div>
              <h2 className="mt-2 text-xl font-black">{selectedMetadata.title}</h2>
              <p className="mt-1 text-xs text-muted-foreground">独立测试案例 · {selectedMetadata.source.storey?.name || "楼层自动选择"} · 不会替换或修改当前用户 CAD 项目</p>
              {selectedMetadata.invalidation?.message && <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900">{selectedMetadata.invalidation.message}</p>}
            </div>
            <Button variant="outline" disabled={!selectedRecordId} onClick={() => { window.location.href = `/records?json_path=${encodeURIComponent(selectedAudit.file.json_path)}&record_id=${encodeURIComponent(selectedRecordId)}`; }}><ExternalLink size={14} />打开完整逐项核对</Button>
          </div>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.55fr)]">
            <div>
              {selectedModel && selectedRecordId ? <GeometryAuditModelViewer
                src={api.geometryAuditArtifactUrl(selectedAudit.file.json_path, selectedRecordId, selectedModel.artifact_id)}
                label={selectedMetadata.title}
              /> : <div className="flex min-h-80 items-center justify-center rounded-xl border border-dashed border-border bg-muted text-sm text-muted-foreground">这个旧案例没有可回放的 OBJ 灰模，只能查看已归档预览。</div>}
            </div>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                {selectedPreviews.map((preview) => {
                  const url = preview.result_thumb || preview.result_url || "";
                  return <a key={preview.result_id} href={api.imgUrl(preview.result_url || url)} target="_blank" rel="noreferrer" className="overflow-hidden rounded-lg border border-border bg-card hover:border-primary"><img src={api.imgUrl(url)} alt={preview.model_label || "验收证据"} className="aspect-[4/3] w-full object-cover" /><div className="truncate px-2 py-1.5 text-[11px] font-bold">{preview.model_label || "验收证据"}</div></a>;
                })}
              </div>
              {selectedMetadata.channels.map((channel) => <section key={channel.channel_id} className="rounded-lg border border-border bg-card p-3 text-xs"><div className="flex items-center justify-between gap-2"><b>{channel.label}</b><span className={`font-black ${channel.status === "passed" ? "text-emerald-700" : channel.status === "pending" ? "text-sky-700" : "text-red-700"}`}>{channel.status === "pending" ? "待核对" : channel.status.toUpperCase()}</span></div><div className="mt-2 space-y-1">{channel.metrics.map((metric) => <div key={metric.metric_id} className="flex items-center justify-between gap-3"><span className="truncate text-muted-foreground">{metric.label}</span><span className={`font-mono font-bold ${metric.status === "passed" ? "text-emerald-700" : metric.status === "pending" ? "text-sky-700" : "text-red-700"}`}>{metric.actual_display} / {metric.operator} {metric.threshold_display}</span></div>)}</div></section>)}
              <div className="rounded-lg border border-border bg-card p-3 text-xs"><div className="flex items-center justify-between"><span>证据完整性</span><b className={selectedMetadata.integrity.status === "passed" ? "text-emerald-700" : "text-red-700"}>{selectedMetadata.integrity.status === "passed" ? `${selectedMetadata.integrity.checked_count} 个文件全部校验通过` : "校验失败"}</b></div><div className="mt-2 truncate font-mono text-[10px] text-muted-foreground" title={selectedMetadata.audit_hash}>audit {selectedMetadata.audit_hash}</div></div>
            </div>
          </div>
        </div> : null}
      </DialogContent>
    </Dialog>
  </>;
}

function localTime(value: number) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(new Date(value * 1000));
}

function dayLabel(value: number) {
  const date = new Date(value * 1000);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((start - target) / 86_400_000);
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

function eventTone(status: string) {
  if (["done", "verified", "review_complete"].includes(status)) return "bg-emerald-100 text-emerald-800";
  if (["failed", "cancelled"].includes(status)) return "bg-red-100 text-red-800";
  if (["partial", "history_revalidation_required"].includes(status)) return "bg-amber-100 text-amber-900";
  return "bg-sky-100 text-sky-800";
}

export function WholeHomeHistoryPanel({
  project, style, lighting, prompt, floorPath, styleRefPath, aspectRatio,
  paidEnabled, onReplayLoaded, onBranchCreated, onProjectSelected,
}: Props) {
  const [history, setHistory] = useState<WholeHomeProjectHistory | null>(null);
  const [selectedReplay, setSelectedReplay] = useState<WholeHomeRunReplay | null>(null);
  const [loading, setLoading] = useState("");
  const [branchName, setBranchName] = useState("");
  const [batch, setBatch] = useState<WholeHomeVariantBatch | null>(null);
  const [batchConfirmation, setBatchConfirmation] = useState("");

  const projectId = project?.project_id || "";
  const loadHistory = useCallback(async (targetProjectId: string) => {
    if (!targetProjectId) return;
    try {
      const value = await api.getWholeHomeProjectHistory(targetProjectId, 200);
      setHistory(value);
    } catch (error) {
      toast.error(`整屋历史加载失败：${(error as Error).message}`);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadHistory(projectId); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory, projectId]);

  const openRun = useCallback(async (runId: string) => {
    setLoading(`run:${runId}`);
    try {
      const replay = await api.getWholeHomeRunReplay(runId);
      setSelectedReplay(replay);
      setBranchName(`${replay.history_project.summary || "整屋模型"} · 新方案 · ${new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date())}`);
      onReplayLoaded(replay);
      window.history.replaceState(null, "", `/floorplan?project=${encodeURIComponent(replay.snapshot.source_project_id)}&run=${encodeURIComponent(runId)}&mode=history`);
      window.localStorage.setItem("whole-home-last-project", replay.snapshot.source_project_id);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setLoading("");
    }
  }, [onReplayLoaded]);

  async function forkReplay() {
    if (!selectedReplay) return;
    setLoading("fork");
    try {
      const value = await api.forkWholeHomeRun(selectedReplay.run.run_id, {
        branch_name: branchName || `${selectedReplay.history_project.summary || "整屋模型"} · 新方案`,
        source_snapshot_hash: selectedReplay.snapshot.snapshot_hash,
        idempotency_key: `history-fork-${selectedReplay.run.run_id}-${selectedReplay.snapshot.snapshot_hash.slice(0, 12)}`,
      });
      onBranchCreated(value, selectedReplay);
      window.history.replaceState(null, "", `/floorplan?project=${encodeURIComponent(value.project_id)}&mode=branch`);
      window.localStorage.setItem("whole-home-last-project", value.project_id);
      toast.success(value.verified ? "历史版本已复制为独立方案，可调整风格" : "历史模型已恢复；需要先完成新版几何验收");
      setSelectedReplay(null);
      await loadHistory(projectId);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setLoading("");
    }
  }

  async function selectBranch(projectId: string) {
    setLoading(`project:${projectId}`);
    try {
      const value = await api.getWholeHomeProject(projectId);
      onProjectSelected(value);
      window.history.replaceState(null, "", `/floorplan?project=${encodeURIComponent(projectId)}&mode=branch`);
      window.localStorage.setItem("whole-home-last-project", projectId);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setLoading("");
    }
  }

  async function previewBatch() {
    const sourceRunId = project?.lineage?.source_run_id;
    if (!project || !sourceRunId) return;
    setLoading("batch-preview");
    try {
      const value = await api.previewWholeHomeVariantBatch({
        project_id: project.project_id,
        source_run_id: sourceRunId,
        style, lighting, prompt, floor_path: floorPath, style_ref_path: styleRefPath,
        aspect_ratio: aspectRatio, resolution: "2K", excluded_artifact_ids: [],
        idempotency_key: `variant-preview-${project.project_id}-${Date.now()}`,
      });
      setBatch(value);
      setBatchConfirmation("");
      toast.success("整套付费清单已锁定；目前仍是零调用");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setLoading("");
    }
  }

  const watchBatch = useCallback((batchId: string) => {
    let stopped = false;
    const tick = async () => {
      try {
        const value = await api.getWholeHomeVariantBatch(batchId);
        if (stopped) return;
        setBatch(value);
        if (!terminalBatch.has(value.status)) window.setTimeout(tick, 1200);
        else void loadHistory(projectId);
      } catch (error) {
        if (!stopped) toast.error((error as Error).message);
      }
    };
    void tick();
    return () => { stopped = true; };
  }, [loadHistory, projectId]);

  async function commitBatch() {
    if (!batch) return;
    setLoading("batch-commit");
    try {
      const value = await api.commitWholeHomeVariantBatch(batch.variant_batch_id, {
        preview_hash: batch.preview_hash, confirmation_phrase: batchConfirmation,
      });
      setBatch(value);
      watchBatch(value.variant_batch_id);
      toast.success("整批任务已提交，将按清单串行执行");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setLoading("");
    }
  }

  async function cancelBatch() {
    if (!batch) return;
    try {
      setBatch(await api.cancelWholeHomeVariantBatch(batch.variant_batch_id));
      toast.success("已取消尚未开始的批次项");
    } catch (error) {
      toast.error((error as Error).message);
    }
  }

  const grouped = useMemo(() => {
    const groups = new Map<string, NonNullable<WholeHomeProjectHistory["events"]>>();
    for (const event of history?.events || []) {
      const label = dayLabel(event.occurred_at);
      groups.set(label, [...(groups.get(label) || []), event]);
    }
    return [...groups.entries()];
  }, [history]);

  const compareEvents = (history?.events || []).filter((event) => event.type === "generation_run" && event.thumbnail_urls?.length);
  const replayBlockers = useMemo(() => {
    const groupedBlockers = new Map<string, { code: string; message: string; count: number }>();
    for (const blocker of selectedReplay?.replay_capability.blockers || []) {
      const code = String(blocker.code || "history_replay_blocked");
      const message = String(blocker.message || blocker.role || code);
      const key = `${code}:${message}`;
      const existing = groupedBlockers.get(key);
      groupedBlockers.set(key, existing ? { ...existing, count: existing.count + 1 } : { code, message, count: 1 });
    }
    return [...groupedBlockers.values()];
  }, [selectedReplay]);
  const canPreviewBatch = Boolean(project?.lineage?.source_run_id && project.verified && floorPath && !project.history_read_only);

  if (!project) return null;
  return (
    <section className="rounded-xl border border-border bg-panel p-4" data-testid="whole-home-history-panel">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2"><History size={19} className="text-primary" /><div><h2 className="font-extrabold">历史与方案</h2><p className="text-xs text-muted-foreground">按整屋项目回看模型、机位、参数、结果和审核；旧记录永久只读。</p></div></div>
        <Button size="sm" variant="outline" onClick={() => void loadHistory(projectId)} disabled={Boolean(loading)}><RefreshCw size={14} />刷新历史</Button>
      </div>

      {history?.branches?.length ? <div className="mb-4 flex gap-2 overflow-x-auto pb-1">
        {history.branches.map((branch) => <button key={branch.project_id} type="button" onClick={() => void selectBranch(branch.project_id)} className={`min-w-56 rounded-lg border p-3 text-left ${branch.project_id === project.project_id ? "border-primary bg-primary/5" : "border-border bg-card"}`}>
          <div className="flex items-center justify-between gap-2"><b className="truncate">{branch.summary || "整屋模型"}</b>{branch.lineage ? <GitBranch size={14} className="text-primary" /> : <History size={14} />}</div>
          <div className="mt-1 text-[11px] text-muted-foreground">revision {branch.revision} · {branch.verified ? "已锁定" : branch.status}</div>
        </button>)}
      </div> : null}

      <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(320px,0.8fr)] gap-4 max-[960px]:grid-cols-1">
        <div className="max-h-[680px] space-y-5 overflow-y-auto pr-1">
          {grouped.length ? grouped.map(([day, events]) => <div key={day}>
            <div className="sticky top-0 z-10 mb-2 flex items-center gap-2 bg-panel/95 py-1 text-sm font-extrabold backdrop-blur"><Clock3 size={14} />{day}</div>
            <div className="space-y-2 border-l-2 border-border pl-3">
              {events.map((event) => <article key={event.event_id} className="rounded-lg border border-border bg-card p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div><b>{event.title}</b><div className="mt-0.5 text-[11px] text-muted-foreground">{localTime(event.occurred_at)}{event.model_revision ? ` · revision ${event.model_revision}` : ""}{event.model_hash ? ` · ${event.model_hash.slice(0, 10)}` : ""}</div></div>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${eventTone(event.status)}`}>{event.status || "done"}</span>
                </div>
                {(event.style || event.lighting) && <div className="mt-2 text-xs"><b>{event.style || "未命名风格"}</b>{event.lighting ? ` · ${event.lighting}` : ""}</div>}
                {event.thumbnail_urls?.length ? <div className="mt-2 grid grid-cols-4 gap-1">{event.thumbnail_urls.map((url, index) => <img key={`${url}-${index}`} src={api.imgUrl(url)} alt="历史效果图" className="aspect-[4/3] w-full rounded object-cover" />)}</div> : null}
                {event.counts && <div className="mt-2 text-[11px] text-muted-foreground">结果 {event.counts.results ?? event.counts.total ?? 0} · 可交付 {event.counts.deliverable ?? event.counts.done ?? 0} · 生图 {event.counts.generation_calls ?? 0} · QA {event.counts.qa_calls ?? 0}</div>}
                {event.run_id && event.type === "generation_run" ? <Button className="mt-2" size="sm" variant="outline" disabled={loading === `run:${event.run_id}`} onClick={() => void openRun(event.run_id!)}>{loading === `run:${event.run_id}` ? <LoaderCircle className="animate-spin" /> : <Images />}只读回看模型与结果</Button> : null}
              </article>)}
            </div>
          </div>) : <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">这个项目还没有可展示的历史事件。</div>}
        </div>

        <div className="space-y-3">
          {selectedReplay ? <div className="rounded-lg border border-sky-300 bg-sky-50 p-3 text-xs text-sky-950 dark:border-sky-900 dark:bg-sky-950/20 dark:text-sky-100">
            <div className="flex items-center gap-2"><History size={15} /><b>历史只读 · revision {selectedReplay.snapshot.source_revision}</b></div>
            <div className="mt-2">任务：{selectedReplay.run.run_id}</div>
            <div>模型：{selectedReplay.snapshot.source_model_hash?.slice(0, 16) || "旧记录未保存"}</div>
            <div>快照：{selectedReplay.snapshot.snapshot_hash.slice(0, 16)}</div>
            <div className="mt-2">恢复能力：<b>{selectedReplay.replay_capability.status}</b></div>
            {replayBlockers.map((blocker) => <div key={`${blocker.code}-${blocker.message}`} className="mt-1 text-amber-800">• {blocker.message}{blocker.count > 1 ? `（${blocker.count} 项资产）` : ""}</div>)}
            <label className="mt-3 block space-y-1 font-semibold">新分支名称<Input value={branchName} onChange={(event) => setBranchName(event.target.value)} /></label>
            <Button className="mt-3 w-full" disabled={!selectedReplay.replay_capability.can_fork || loading === "fork"} onClick={() => void forkReplay()}>{loading === "fork" ? <LoaderCircle className="animate-spin" /> : <Copy />}复制为新方案</Button>
          </div> : <div className="rounded-lg border border-dashed border-border p-4 text-xs text-muted-foreground">从左侧时间线打开一次生成，即可在 3D 中回看当时模型，并复制成新的可编辑方案。</div>}

          {project.lineage && !project.history_read_only ? <div className="rounded-lg border border-primary/25 bg-primary/5 p-3 text-xs">
            <div className="flex items-center gap-2"><Sparkles size={15} className="text-primary" /><b>整套换风格</b></div>
            <div className="mt-1 text-muted-foreground">来源任务 {project.lineage.source_run_id}；默认重跑原任务全部有效结果，每次只执行一个子任务。</div>
            {!project.verified && <div className="mt-2 rounded bg-amber-100 p-2 text-amber-900">该历史模型尚未通过新版几何生产锁，先完成上方验收才能创建付费预览。</div>}
            {!floorPath && <div className="mt-2 rounded bg-amber-100 p-2 text-amber-900">请先在生成区选择或上传地板小样。</div>}
            <Button className="mt-3 w-full" variant="outline" disabled={!canPreviewBatch || loading === "batch-preview"} onClick={() => void previewBatch()}>{loading === "batch-preview" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}预览整套付费清单（零调用）</Button>
          </div> : null}

          {batch ? <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-950 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100">
            <div className="flex items-center justify-between gap-2"><b>批次 {batch.variant_batch_id}</b><span>{batch.status}</span></div>
            <div className="mt-2">{batch.aggregate_caps.items} 项 · 生图硬上限 {batch.aggregate_caps.image_calls} · QA 硬上限 {batch.aggregate_caps.qa_calls} · 并发 1</div>
            <div className="mt-2 max-h-36 space-y-1 overflow-y-auto">{batch.items.map((item) => <div key={item.item_id} className="flex items-center justify-between rounded bg-white/60 px-2 py-1 dark:bg-black/10"><span>{item.camera_name || item.room_id || item.capture_id} · {item.model_key}</span><span>{item.status === "done" ? <CheckCircle2 size={13} className="text-emerald-700" /> : item.status === "failed" ? <XCircle size={13} className="text-red-700" /> : item.status}</span></div>)}</div>
            {batch.status === "previewed" && <>
              <div className="mt-2 rounded bg-white/70 p-2 font-mono dark:bg-black/10">{batch.confirmation_phrase}</div>
              <label className="mt-2 block space-y-1 font-semibold">逐字输入上方整批动态短语<Input value={batchConfirmation} onChange={(event) => setBatchConfirmation(event.target.value)} /></label>
              <Button className="mt-2 w-full" disabled={!paidEnabled || batchConfirmation !== batch.confirmation_phrase || loading === "batch-commit"} onClick={() => void commitBatch()}>{loading === "batch-commit" ? <LoaderCircle className="animate-spin" /> : <Play />}一次确认并串行执行整套</Button>
              {!paidEnabled && <div className="mt-2 font-bold text-amber-800">服务未以 AllowPaid 启动，提交保持关闭；本预览没有产生费用。</div>}
            </>}
            {["queued", "running"].includes(batch.status) && <Button className="mt-2 w-full" variant="outline" onClick={() => void cancelBatch()}><XCircle />取消尚未开始的项</Button>}
          </div> : null}

          {compareEvents.length > 1 ? <details className="rounded-lg border border-border bg-card p-3 text-xs">
            <summary className="cursor-pointer font-bold">多风格结果对比 · {compareEvents.length} 组</summary>
            <div className="mt-3 grid grid-cols-2 gap-2">{compareEvents.slice(0, 8).map((event) => <div key={event.event_id}><div className="mb-1 truncate font-semibold">{event.style || localTime(event.occurred_at)}</div>{event.thumbnail_urls?.[0] ? <img src={api.imgUrl(event.thumbnail_urls[0])} alt={event.style || "风格效果"} className="aspect-[4/3] w-full rounded object-cover" /> : null}</div>)}</div>
          </details> : null}
        </div>
      </div>
    </section>
  );
}
