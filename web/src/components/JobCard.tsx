/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useJobStream } from "@/hooks/useJobStream";
import { notifyJobEnd } from "@/lib/notify";
import type { JobView } from "@/lib/types";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ImageZoom } from "@/components/ImageZoom";
import { cn } from "@/lib/utils";

const BADGE: Record<string, { label: string; color: string; bg: string }> = {
  queued: { label: "排队", color: "#857c6e", bg: "#efe9dc" },
  running: { label: "生成中", color: "#fff", bg: "#c15f3c" },
  done: { label: "完成", color: "#fff", bg: "#2e8c7e" },
  partial: { label: "部分完成", color: "#8a6d2e", bg: "#f3e4c4" },
  failed: { label: "失败", color: "#fff", bg: "#b5503a" },
};

const REGEN_NS = [1, 2, 4, 6];
const actBtn =
  "h-[28px] rounded-lg border border-border bg-card px-[11px] text-[11.5px] font-semibold text-[#6b6356] transition-colors hover:bg-[#f2e9e0]";

type SlotView = { idx: number; url: string; thumb: string };

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
  const [regenN, setRegenN] = useState(1);
  // 候选切换的本地覆盖（不影响后端"当前下标"，仅前端浏览）
  const [view, setView] = useState<{ b2?: SlotView; pro?: SlotView }>({});

  const prevStatus = useRef(initial.status);
  const prevOperationStatus = useRef(initial.operation_status);
  const totalsRef = useRef({ b2: initial.b2_total, pro: initial.pro_total });
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
    if (
      j.b2_total !== totalsRef.current.b2 ||
      j.pro_total !== totalsRef.current.pro
    ) {
      totalsRef.current = { b2: j.b2_total, pro: j.pro_total };
      setView({});
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

  async function remove() {
    try {
      await api.deleteJob(job.job_id);
      onRemove?.(job.job_id); // 即时从队列移除；后端已删，轮询不会复活
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function nav(model: "b2" | "pro", delta: number) {
    const total = model === "b2" ? job.b2_total : job.pro_total;
    if (total <= 1) return;
    const cur = view[model]?.idx ?? (model === "b2" ? job.b2_idx : job.pro_idx);
    const next = Math.max(0, Math.min(cur + delta, total - 1));
    if (next === cur && view[model]) return;
    try {
      const r = await api.jobResult(job.job_id, model, next);
      setView((v) => ({ ...v, [model]: { idx: r.idx, url: r.url, thumb: r.thumb } }));
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const b = BADGE[job.status] ?? BADGE.queued;

  const slots: {
    key: "b2" | "pro";
    name: string;
    url: string;
    thumb: string;
    idx: number;
    total: number;
  }[] = [];
  for (const key of ["b2", "pro"] as const) {
    const baseUrl = key === "b2" ? job.b2_url : job.pro_url;
    if (!baseUrl) continue;
    const ov = view[key];
    slots.push({
      key,
      name: key === "b2" ? "B2" : "Pro",
      url: ov?.url ?? baseUrl,
      thumb: ov?.thumb ?? (key === "b2" ? job.b2_thumb : job.pro_thumb),
      idx: ov?.idx ?? (key === "b2" ? job.b2_idx : job.pro_idx),
      total: key === "b2" ? job.b2_total : job.pro_total,
    });
  }

  const stageLine =
    [job.b2_stage && `B2 ${job.b2_stage}`, job.pro_stage && `Pro ${job.pro_stage}`]
      .filter(Boolean)
      .join(" · ") || "处理中…";

  const terminal =
    !active &&
    (job.status === "done" || job.status === "partial" || job.status === "failed");

  return (
    <div className="animate-scfade rounded-[14px] border border-border bg-card p-[13px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-bold text-[#2a241f]">
            {job.display_name}
          </div>
          <div className="mt-0.5 text-[11px] text-[#9a9082]">
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
          <div className="h-[5px] w-full overflow-hidden rounded-md bg-[#efe9dc]">
            <div className="h-full w-2/5 animate-pulse rounded-md bg-primary" />
          </div>
        </div>
      )}

      {(job.operation_error || job.error) && (
        <div className="mt-2.5 rounded-[9px] bg-[#f9e7e2] px-[11px] py-[9px] text-[11.5px] leading-relaxed text-[#9a3b29]">
          {job.error_kb ? (
            <span className="font-semibold">{job.error_kb.title} · </span>
          ) : null}
          {job.operation_error || job.error}
        </div>
      )}

      {slots.length > 0 && (
        <div className="mt-[11px] grid grid-cols-1 gap-[9px]">
          {slots.map((m) => (
            <div key={m.key}>
              <div className="relative aspect-[4/3] cursor-zoom-in overflow-hidden rounded-[10px] border border-border">
                <img
                  src={api.imgUrl(m.thumb)}
                  alt={m.name}
                  onClick={() => setZoom(api.imgUrl(m.url))}
                  className="absolute inset-0 h-full w-full object-cover"
                />
                <span className="absolute left-[7px] top-[7px] rounded-md bg-[rgba(26,24,21,.55)] px-[7px] py-[2px] text-[10px] font-bold text-white backdrop-blur-[2px]">
                  {m.name}
                </span>
              </div>
              <div className="mt-[5px] flex items-center justify-between text-[11px] text-[#9a9082]">
                <span className="flex items-center gap-1">
                  {m.name}
                  {m.total > 1 && (
                    <>
                      <button
                        onClick={() => nav(m.key, -1)}
                        disabled={m.idx <= 0}
                        className="rounded px-1 hover:bg-[#f2e9e0] disabled:opacity-30"
                      >
                        ‹
                      </button>
                      <span>
                        {m.idx + 1}/{m.total}
                      </span>
                      <button
                        onClick={() => nav(m.key, 1)}
                        disabled={m.idx >= m.total - 1}
                        className="rounded px-1 hover:bg-[#f2e9e0] disabled:opacity-30"
                      >
                        ›
                      </button>
                    </>
                  )}
                </span>
                <a
                  href={api.imgUrl(m.url)}
                  target="_blank"
                  rel="noreferrer"
                  className="hover:text-[#2a241f]"
                >
                  ↗ 原图
                </a>
              </div>
            </div>
          ))}
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
              重试
            </button>
          )}
        {terminal && job.pro_url && (
          <button
            className={actBtn}
            onClick={() => act(() => api.polishJob(job.job_id), "已提交磨缝")}
          >
            🪄 磨缝
          </button>
        )}
        {terminal && (job.pro_url || job.b2_url) && (
          <button className={actBtn} onClick={() => setEditOpen(true)}>
            ✏️ 二改
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
                    : "text-[#857c6e] hover:bg-[#f2e9e0]",
                )}
              >
                ×{n}
              </button>
            ))}
            <button
              className="ml-0.5 h-[24px] rounded-md border border-border bg-card px-2 text-[11.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
              onClick={() =>
                act(() => api.regenJob(job.job_id, regenN), `已开始重抽 ×${regenN}`)
              }
            >
              🔄 重抽
            </button>
          </div>
        )}

        {terminal && (
          <button
            className={cn(
              actBtn,
              "hover:border-[#e7c7bf] hover:bg-[#f9e7e2] hover:text-[#b5503a]",
            )}
            onClick={remove}
            title="从队列移除此卡（不影响出图与记录）"
          >
            ✕ 清除
          </button>
        )}
      </div>

      <ImageZoom url={zoom} onClose={() => setZoom(null)} />

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <div className="space-y-3">
            <div className="text-[15px] font-bold">二改（对成图做图生图编辑）</div>
            <Input
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              placeholder="编辑指令，例如：把墙换成米白色"
              className="h-10 rounded-[10px] bg-[#faf7f0]"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEditOpen(false)}
                className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
              >
                取消
              </button>
              <button
                onClick={() => {
                  const t = editText.trim();
                  if (!t) return;
                  act(() => api.editJob(job.job_id, { instruction: t }), "已提交二改");
                  setEditOpen(false);
                  setEditText("");
                }}
                className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-[#a8472a]"
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
