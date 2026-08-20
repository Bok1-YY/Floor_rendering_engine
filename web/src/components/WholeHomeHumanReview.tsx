"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  BellRing,
  Check,
  CircleOff,
  Database,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  ThumbsUp,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type {
  WholeHomeHumanReviewStatus,
  WholeHomeLearningSummary,
  WholeHomeProject,
  WholeHomeReviewState,
  WholeHomeReviewableArtifact,
  WholeHomeRun,
} from "@/lib/types";
import {
  canCompleteHumanReview,
  canContinueHumanReview,
  groupWholeHomeReviewables,
  reviewValidationMessage,
  WHOLE_HOME_REJECT_TAGS,
} from "@/lib/wholeHomeHumanReview";

const REVIEWER_ID = "local-user";

const statusMeta: Record<WholeHomeHumanReviewStatus, { label: string; tone: string }> = {
  pass: { label: "人工通过", tone: "bg-emerald-100 text-emerald-800" },
  backup: { label: "人工备选", tone: "bg-sky-100 text-sky-800" },
  reject: { label: "人工拒绝", tone: "bg-red-100 text-red-800" },
  unreviewed: { label: "未评审", tone: "bg-amber-100 text-amber-800" },
};

function stableRequestKey(scope: string): string {
  const storageKey = `whole-home-human-loop:${scope}`;
  const id = typeof window.crypto?.randomUUID === "function"
    ? window.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  try {
    const existing = window.sessionStorage.getItem(storageKey);
    if (existing) return existing;
    window.sessionStorage.setItem(storageKey, id);
  } catch {
    // Privacy modes may reject sessionStorage; the per-click busy lock still prevents double submission.
  }
  return id;
}

function modelLabel(key: string) {
  return key === "b2" ? "Nano Banana 2（B2）" : key === "pro" ? "Nano Banana Pro" : key;
}

function humanStatusPill(status: WholeHomeHumanReviewStatus) {
  const meta = statusMeta[status];
  return <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${meta.tone}`}>{meta.label}</span>;
}

function conflictMessage(error: unknown) {
  const message = (error as Error).message || "请求失败";
  return /版本|刷新|幂等|不存在|不属于|409/.test(message)
    ? `评审状态刚刚发生了变化，已为你刷新。${message}`
    : message;
}

interface ReviewDraft {
  artifactId: string;
  status: "backup" | "reject";
  tags: string[];
  note: string;
}

function ArtifactCard({
  artifact,
  run,
  reviewVersion,
  busy,
  onBusy,
  onState,
  onRefresh,
}: {
  artifact: WholeHomeReviewableArtifact;
  run: WholeHomeRun;
  reviewVersion: number;
  busy: boolean;
  onBusy: (value: boolean) => void;
  onState: (value: WholeHomeReviewState) => void;
  onRefresh: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<ReviewDraft | null>(null);
  const review = artifact.human_review;
  const result = run.results.find((row) => row.result_id === artifact.result_id);

  function openDraft(status: "backup" | "reject") {
    setDraft({
      artifactId: artifact.artifact_id,
      status,
      tags: artifact.review_status === status ? [...(review?.review_tags || [])] : [],
      note: artifact.review_status === status ? review?.review_note || "" : "",
    });
  }

  async function submit(
    status: WholeHomeHumanReviewStatus,
    tags: string[] = [],
    note = "",
  ) {
    const validation = reviewValidationMessage(status, tags);
    if (validation) {
      toast.warning(validation);
      return;
    }
    onBusy(true);
    const requestScope = [
      "review",
      run.run_id,
      artifact.artifact_id,
      reviewVersion,
      status,
      [...tags].sort().join("|"),
      note,
    ].join(":");
    try {
      const response = await api.reviewWholeHomeArtifact(run.run_id, artifact.result_id, {
        artifact_id: artifact.artifact_id,
        review_status: status,
        review_tags: tags,
        review_note: note,
        reviewer_id: REVIEWER_ID,
        expected_review_version: reviewVersion,
        idempotency_key: stableRequestKey(requestScope),
      });
      onState(response.review_state);
      setDraft(null);
      toast.success(status === "pass" ? "已记为人工通过" : status === "backup" ? "已记为备选" : status === "reject" ? "已记录拒绝原因" : "已重置为未评审");
      await onRefresh();
    } catch (error) {
      toast.error(conflictMessage(error));
      await onRefresh();
    } finally {
      onBusy(false);
    }
  }

  const editing = draft?.artifactId === artifact.artifact_id ? draft : null;
  return (
    <article className={`overflow-hidden rounded-xl border bg-card ${artifact.review_status === "pass" ? "border-emerald-400" : artifact.review_status === "reject" ? "border-red-300" : "border-border"}`}>
      <a href={api.imgUrl(artifact.url)} target="_blank" rel="noreferrer" className="block bg-[#ebe7df]">
        <img src={api.imgUrl(artifact.thumb || artifact.url)} alt={`${result?.camera_name || artifact.room_id} 人工评审图`} className="aspect-[4/3] w-full object-contain" />
      </a>
      <div className="space-y-2.5 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <b className="text-sm">{modelLabel(artifact.model_key)} · 材质尝试</b>
          {humanStatusPill(artifact.review_status)}
        </div>
        <div className="rounded-lg bg-muted/60 px-2.5 py-2 text-[11px] leading-relaxed text-muted-foreground">
          自动结论：{artifact.auto_deliverable ? "QA 建议可交付" : `QA 未放行（${artifact.auto_outcome || artifact.material_status || "无结论"}）`}。<b className="text-foreground">这只是弱信号，最终以你的人工裁决为准。</b>
        </div>
        {review && artifact.review_status !== "unreviewed" && (review.review_tags.length > 0 || review.review_note) && (
          <div className="text-[11px] text-muted-foreground">
            {review.review_tags.length > 0 && <div>{review.review_tags.join(" · ")}</div>}
            {review.review_note && <div className="mt-1 whitespace-pre-wrap">{review.review_note}</div>}
          </div>
        )}
        <div className="grid grid-cols-3 gap-2">
          <Button size="sm" disabled={busy || artifact.review_status === "pass"} onClick={() => void submit("pass")}><ThumbsUp />通过</Button>
          <Button size="sm" variant="outline" disabled={busy || artifact.review_status === "backup"} onClick={() => void submit("backup")}><ShieldCheck />备选</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => openDraft("reject")}><CircleOff />拒绝</Button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
          <span className="text-muted-foreground" title={artifact.artifact_id}>工件 {artifact.artifact_id.slice(0, 18)}</span>
          <div className="flex gap-1">
            {artifact.review_status === "backup" && <button className="text-primary underline" disabled={busy} onClick={() => openDraft("backup")}>编辑备选说明</button>}
            {artifact.review_status !== "unreviewed" && <button className="inline-flex items-center gap-1 text-muted-foreground underline" disabled={busy} onClick={() => void submit("unreviewed")}><RotateCcw size={12} />重置</button>}
          </div>
        </div>
        {editing && (
          <div className={`rounded-lg border p-3 ${editing.status === "reject" ? "border-red-200 bg-red-50/70 dark:border-red-900 dark:bg-red-950/20" : "border-sky-200 bg-sky-50/70 dark:border-sky-900 dark:bg-sky-950/20"}`}>
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-bold">
              <span>{editing.status === "reject" ? "选择失败原因（至少 1 项）" : "备选标签与说明（可选）"}</span>
              <button aria-label="关闭编辑" onClick={() => setDraft(null)}><X size={14} /></button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {WHOLE_HOME_REJECT_TAGS.map((tag) => {
                const active = editing.tags.includes(tag);
                return <button key={tag} type="button" className={`rounded-full border px-2 py-1 text-[11px] ${active ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card"}`} onClick={() => setDraft((current) => current ? { ...current, tags: active ? current.tags.filter((value) => value !== tag) : [...current.tags, tag] } : current)}>{tag}</button>;
              })}
            </div>
            <Textarea className="mt-2 min-h-20 bg-card text-xs" maxLength={2000} value={editing.note} onChange={(event) => setDraft((current) => current ? { ...current, note: event.target.value } : current)} placeholder="补充你看到的具体问题（可选）" />
            <Button className="mt-2 w-full" size="sm" disabled={busy || Boolean(reviewValidationMessage(editing.status, editing.tags))} onClick={() => void submit(editing.status, editing.tags, editing.note)}>
              {editing.status === "reject" ? "确认拒绝并记录原因" : "保存备选说明"}
            </Button>
          </div>
        )}
      </div>
    </article>
  );
}

export function WholeHomeHumanReview({
  run,
  project,
  reviewState,
  summary,
  loading,
  onState,
  onRefresh,
  onRunStarted,
  onOpenRerunSettings,
}: {
  run: WholeHomeRun;
  project: WholeHomeProject | null;
  reviewState: WholeHomeReviewState | null;
  summary: WholeHomeLearningSummary | null;
  loading: boolean;
  onState: (value: WholeHomeReviewState) => void;
  onRefresh: () => Promise<void>;
  onRunStarted: (value: WholeHomeRun) => void;
  onOpenRerunSettings: () => void;
}) {
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [mutationBusy, setMutationBusy] = useState(false);
  const [completionBusy, setCompletionBusy] = useState(false);
  const [continuationBusy, setContinuationBusy] = useState(false);
  const [consentBusy, setConsentBusy] = useState(false);
  const notifiedRunRef = useRef("");
  const state = reviewState || run.human_review || null;
  const roomLabels = useMemo(() => new Map((project?.model.rooms || []).map((room) => [room.id, room.label])), [project]);
  const groups = useMemo(() => groupWholeHomeReviewables(state?.reviewables || []), [state?.reviewables]);
  const visibleGroups = useMemo(() => groups.map((group) => ({
    ...group,
    artifacts: filter === "pending" ? group.artifacts.filter((row) => row.review_status === "unreviewed") : group.artifacts,
  })).filter((group) => group.artifacts.length > 0), [filter, groups]);
  const firstPendingRoom = groups.find((group) => group.counts.unreviewed > 0)?.room_id;
  const consentAllowed = summary?.training_consent?.allowed ?? project?.learning?.training_consent?.allowed ?? false;
  const covered = summary?.covered_room_count ?? project?.learning?.covered_room_count ?? 0;
  const selected = summary?.selected_room_count ?? project?.learning?.selected_room_count ?? 0;
  const counts = summary?.counts || state?.counts || { pass: 0, backup: 0, reject: 0, unreviewed: 0 };

  useEffect(() => {
    if (!state || !["awaiting_human_review", "review_not_required"].includes(state.round_status)) return;
    const notificationKey = `whole-home-review-notified:${run.run_id}`;
    if (notifiedRunRef.current === run.run_id) return;
    try {
      if (window.sessionStorage.getItem(notificationKey)) return;
      window.sessionStorage.setItem(notificationKey, "1");
    } catch {
      // A component-local guard still prevents repeats when storage is unavailable.
    }
    notifiedRunRef.current = run.run_id;
    if ("Notification" in window && window.Notification.permission === "granted") {
      try {
        new window.Notification(`整屋第 ${run.round_index || 1} 轮等待人工评审`, {
          body: state.round_status === "review_not_required" ? "本轮没有可评图片，请回到页面确认继续。" : `有 ${state.pending_count} 张新图片等待你的最终裁决。`,
        });
      } catch {
        // The persistent in-page banner remains the source of truth if OS notifications fail.
      }
    }
  }, [run.round_index, run.run_id, state]);

  async function completeReview() {
    if (!state || !canCompleteHumanReview(state)) return;
    setCompletionBusy(true);
    try {
      const next = await api.completeWholeHomeReview(run.run_id, {
        reviewer_id: REVIEWER_ID,
        expected_review_version: state.review_version,
        idempotency_key: stableRequestKey(`complete:${run.run_id}:${state.review_version}`),
      });
      onState(next);
      toast.success("本轮人工评审已完成。流程已暂停，不会自动调用任何生图 API。");
      await onRefresh();
    } catch (error) {
      toast.error(conflictMessage(error));
      await onRefresh();
    } finally {
      setCompletionBusy(false);
    }
  }

  async function continueOptimization() {
    if (!state || !canContinueHumanReview(state)) return;
    setContinuationBusy(true);
    try {
      const nextRun = await api.continueWholeHomeRun(run.run_id, {
        expected_review_version: state.review_version,
        continuation_completion_event_id: state.completion_event_id,
        idempotency_key: stableRequestKey(`continue:${run.run_id}:${state.completion_event_id}`),
      });
      toast.success(`已由你手动启动第 ${nextRun.round_index || (run.round_index || 1) + 1} 轮；默认只补跑尚无人工通过图的房间。`);
      onRunStarted(nextRun);
    } catch (error) {
      toast.error(conflictMessage(error));
      await onRefresh();
    } finally {
      setContinuationBusy(false);
    }
  }

  async function updateConsent(allowed: boolean) {
    if (!project) return;
    setConsentBusy(true);
    try {
      await api.setWholeHomeTrainingConsent(project.project_id, allowed);
      toast.success(allowed ? "已允许把本项目的人工标签纳入本机优化数据集" : "已关闭训练授权；历史评审记录仍会完整保留");
      await onRefresh();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setConsentBusy(false);
    }
  }

  if (!state) {
    return <section className="rounded-xl border border-border bg-panel p-4 text-sm text-muted-foreground">{loading ? <><LoaderCircle className="mr-2 inline animate-spin" />正在读取人工评审状态…</> : "旧任务暂无人工评审状态，请刷新后重试。"}</section>;
  }
  if (state.round_status === "working") return null;

  const awaiting = state.round_status === "awaiting_human_review";
  const noReviewables = state.round_status === "review_not_required";
  const complete = state.round_status === "review_complete";
  const remaining = summary?.uncovered_room_ids.length ?? Math.max(0, selected - covered);
  return (
    <section className="space-y-3" aria-label="整屋人工评审闭环">
      <div className={`sticky top-2 z-30 rounded-xl border p-3 shadow-lg backdrop-blur ${complete ? "border-emerald-300 bg-emerald-50/95 dark:border-emerald-900 dark:bg-emerald-950/95" : "border-amber-300 bg-amber-50/95 dark:border-amber-900 dark:bg-amber-950/95"}`}>
          <div className="flex flex-wrap items-center gap-3">
            {complete ? <Check className="text-emerald-700" /> : <BellRing className="text-amber-700" />}
            <div className="min-w-0 flex-1">
              <b>{complete ? `第 ${run.round_index || 1} 轮评审完成，当前已暂停` : `第 ${run.round_index || 1} 轮完成，等待人工评审`}</b>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {complete ? "不会自动开始下一轮，也不会自动调用生图 API。请由你决定继续补跑或进入自选/全量重跑。" : noReviewables ? "本轮无可评图片，确认后才能继续；程序现在不会自动调用生图 API。" : `还有 ${state.pending_count}/${state.reviewable_count} 张未裁决。自动 QA 仅供参考，人工标签才是最终结论。`}
              </div>
            </div>
            <Button size="sm" variant="outline" disabled={loading || mutationBusy || completionBusy || continuationBusy} onClick={() => void onRefresh()}>{loading ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}刷新状态</Button>
          </div>
      </div>

      <div className="rounded-xl border border-border bg-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-extrabold">5. 人工逐图裁决与增量优化</h2>
            <p className="mt-1 text-xs text-muted-foreground">每个材质尝试都独立保留、独立标记；通过才覆盖房间，备选不计覆盖。任何自动通过或自动拒绝都不会替你作最终决定。</p>
          </div>
          <div className="flex gap-1 rounded-lg bg-muted p-1 text-xs">
            <button className={`rounded-md px-2.5 py-1.5 ${filter === "pending" ? "bg-card font-bold shadow-sm" : "text-muted-foreground"}`} onClick={() => setFilter("pending")}>只看未评审 {state.pending_count}</button>
            <button className={`rounded-md px-2.5 py-1.5 ${filter === "all" ? "bg-card font-bold shadow-sm" : "text-muted-foreground"}`} onClick={() => setFilter("all")}>查看全部 {state.reviewable_count}</button>
          </div>
        </div>

        <div className="mt-3 text-[11px] font-bold text-muted-foreground">当前项目累计人工标签</div>
        <div className="mt-1 grid grid-cols-5 gap-2 max-[760px]:grid-cols-2">
          {(["pass", "backup", "reject", "unreviewed"] as const).map((status) => <div key={status} className="rounded-lg border border-border bg-card p-2 text-center"><div className="text-[10px] text-muted-foreground">{statusMeta[status].label}</div><div className="text-lg font-extrabold">{counts[status] || 0}</div></div>)}
          <div className="rounded-lg border border-primary/30 bg-primary/5 p-2 text-center"><div className="text-[10px] text-muted-foreground">人工通过覆盖</div><div className="text-lg font-extrabold text-primary">{covered}/{selected || "—"}</div></div>
        </div>

        {state.reviewable_count > 0 ? (
          <div className="mt-3 space-y-2">
            {visibleGroups.length > 0 ? visibleGroups.map((group) => (
              <details key={group.room_id} className="rounded-xl border border-border bg-muted/20" open={filter === "pending" && group.room_id === firstPendingRoom ? true : undefined}>
                <summary className="cursor-pointer list-none px-3 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <b>{roomLabels.get(group.room_id) || group.room_id}</b>
                    <span className="text-xs text-muted-foreground">{group.artifacts.length} 张当前列表</span>
                    <span className="ml-auto text-[11px] text-emerald-700">通过 {group.counts.pass}</span>
                    <span className="text-[11px] text-sky-700">备选 {group.counts.backup}</span>
                    <span className="text-[11px] text-red-700">拒绝 {group.counts.reject}</span>
                    <span className="text-[11px] text-amber-700">待评 {group.counts.unreviewed}</span>
                  </div>
                </summary>
                <div className="grid grid-cols-2 gap-3 border-t border-border p-3 max-[820px]:grid-cols-1">
                  {group.artifacts.map((artifact) => <ArtifactCard key={artifact.artifact_id} artifact={artifact} run={run} reviewVersion={state.review_version} busy={mutationBusy || completionBusy || continuationBusy} onBusy={setMutationBusy} onState={onState} onRefresh={onRefresh} />)}
                </div>
              </details>
            )) : <div className="rounded-lg border border-dashed border-emerald-300 bg-emerald-50 p-5 text-center text-sm text-emerald-800">当前没有未评审图片。切换“查看全部”可以修改已提交的人工标签。</div>}
          </div>
        ) : (
          <div className="mt-3 rounded-lg border border-dashed border-amber-300 bg-amber-50 p-5 text-center text-sm text-amber-800">本轮没有成功保存的可查看材质图片。仍需由你确认本轮结束，程序不会自行跳过。</div>
        )}

        <div className="mt-4 rounded-xl border border-border bg-card p-3">
          {!complete ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-muted-foreground">{awaiting && state.pending_count > 0 ? `请先完成剩余 ${state.pending_count} 张图片的人工裁决。` : noReviewables ? "本轮无可评图片，可以人工确认继续。" : "所有图片已标记，可以完成人工评审。"}</div>
              <Button disabled={!canCompleteHumanReview(state) || mutationBusy || completionBusy} onClick={() => void completeReview()}>{completionBusy ? <LoaderCircle className="animate-spin" /> : <Check />}{noReviewables ? "确认本轮无可评图片" : "本轮评审完成，暂停在这里"}</Button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200"><b>已安全暂停：</b>只有你点击下面按钮才会开始新一轮。刷新或双击会复用同一个请求标识，不会重复创建任务。</div>
              <div className="flex flex-wrap justify-end gap-2">
                <Button variant="outline" onClick={onOpenRerunSettings}><Sparkles />新一轮自选 / 全量重跑</Button>
                <Button disabled={!canContinueHumanReview(state) || continuationBusy || remaining === 0} onClick={() => void continueOptimization()}>{continuationBusy ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{remaining === 0 ? "全部房间已有人工通过图" : `继续优化未覆盖房间（${remaining}）`}</Button>
              </div>
            </div>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-3">
          <div className="flex min-w-0 items-start gap-2">
            <Database size={18} className="mt-0.5 shrink-0 text-primary" />
            <div><b className="text-sm">允许本项目用于本机优化数据集</b><div className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">默认关闭。开启后才能把本项目人工标签和配方导出用于优化；关闭后不进入优化数据集，但已经产生的图片、失败尝试和人工评审历史仍完整保留。</div></div>
          </div>
          <Switch aria-label="本项目训练数据授权" checked={consentAllowed} disabled={!project || consentBusy} onCheckedChange={(checked) => void updateConsent(checked)} />
        </div>
      </div>
    </section>
  );
}
