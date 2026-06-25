/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useJobStream } from "@/hooks/useJobStream";
import { notifyJobEnd } from "@/lib/notify";
import type { JobView } from "@/lib/types";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";
const STATUS: Record<string, { label: string; variant: BadgeVariant }> = {
  queued: { label: "排队", variant: "secondary" },
  running: { label: "生成中", variant: "default" },
  done: { label: "完成", variant: "default" },
  partial: { label: "部分完成", variant: "outline" },
  failed: { label: "失败", variant: "destructive" },
};

const REGEN_NS = [1, 2, 4, 6];

type SlotView = { idx: number; url: string; thumb: string };

export function JobCard({ initial }: { initial: JobView }) {
  const [job, setJob] = useState<JobView>(initial);
  const [zoom, setZoom] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState("");
  const [regenN, setRegenN] = useState(1);
  // 候选切换的本地覆盖（不影响后端"当前下标"，仅前端浏览）
  const [view, setView] = useState<{ b2?: SlotView; pro?: SlotView }>({});

  const prevStatus = useRef(initial.status);
  // SSE 更新：刷新 job、清候选覆盖；非终态→终态时触发完成提醒(系统通知+提示音)
  const onUpdate = useCallback((j: JobView) => {
    const was = prevStatus.current;
    const wasActive = was === "queued" || was === "running";
    const nowTerminal =
      j.status === "done" || j.status === "partial" || j.status === "failed";
    if (wasActive && nowTerminal) {
      notifyJobEnd(j.status, j.display_name, j.error);
    }
    prevStatus.current = j.status;
    setJob(j);
    setView({});
  }, []);
  const active =
    job.status === "queued" || job.status === "running" || job.pro_polishing;
  useJobStream(active ? job.job_id : null, onUpdate);

  async function act(fn: () => Promise<JobView>, okMsg?: string) {
    try {
      const j = await fn();
      setJob(j);
      setView({});
      if (okMsg) toast.success(okMsg);
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

  const st = STATUS[job.status] ?? STATUS.queued;

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
    job.status === "done" || job.status === "partial" || job.status === "failed";

  return (
    <div className="rounded-xl border bg-background p-3 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{job.display_name}</div>
          <div className="text-xs text-muted-foreground">
            {job.ts}
            {job.time_text ? ` · ${job.time_text}` : ""}
          </div>
        </div>
        <Badge variant={st.variant}>{st.label}</Badge>
      </div>

      {active && <div className="mt-1 text-xs text-primary">{stageLine}</div>}

      {job.error && (
        <div className="mt-2 rounded-md bg-destructive/10 p-2 text-xs text-destructive">
          {job.error_kb ? (
            <span className="font-medium">{job.error_kb.title} · </span>
          ) : null}
          {job.error}
        </div>
      )}

      {slots.length > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {slots.map((m) => (
            <div key={m.key} className="space-y-1">
              <img
                src={api.imgUrl(m.thumb)}
                alt={m.name}
                onClick={() => setZoom(api.imgUrl(m.url))}
                className="aspect-[4/3] w-full cursor-zoom-in rounded-lg border object-cover"
              />
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  {m.name}
                  {m.total > 1 && (
                    <>
                      <button
                        onClick={() => nav(m.key, -1)}
                        disabled={m.idx <= 0}
                        className="rounded px-1 hover:bg-muted disabled:opacity-30"
                      >
                        ‹
                      </button>
                      <span>
                        {m.idx + 1}/{m.total}
                      </span>
                      <button
                        onClick={() => nav(m.key, 1)}
                        disabled={m.idx >= m.total - 1}
                        className="rounded px-1 hover:bg-muted disabled:opacity-30"
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
                  className="hover:underline"
                >
                  原图↗
                </a>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {active && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              act(
                () =>
                  api.cancelJob(job.job_id).then(() => api.getJob(job.job_id)),
                "已请求停止",
              )
            }
          >
            停止
          </Button>
        )}
        {terminal && job.has_retry && (job.status === "failed" || job.status === "partial") && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => act(() => api.retryJob(job.job_id), "已重试")}
          >
            重试
          </Button>
        )}
        {terminal && job.pro_url && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => act(() => api.polishJob(job.job_id), "已提交磨缝")}
          >
            🪄 磨缝
          </Button>
        )}
        {terminal && (job.pro_url || job.b2_url) && (
          <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
            ✏️ 二改
          </Button>
        )}

        {/* 重抽 / 多抽 */}
        {terminal && job.has_retry && (
          <div className="flex items-center gap-1 rounded-md border px-1.5 py-0.5">
            {REGEN_NS.map((n) => (
              <button
                key={n}
                onClick={() => setRegenN(n)}
                className={cn(
                  "rounded px-1.5 text-xs",
                  regenN === n
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-muted",
                )}
              >
                ×{n}
              </button>
            ))}
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2"
              onClick={() =>
                act(() => api.regenJob(job.job_id, regenN), `已开始重抽 ×${regenN}`)
              }
            >
              🔄 重抽
            </Button>
          </div>
        )}
      </div>

      <Dialog open={!!zoom} onOpenChange={(o) => !o && setZoom(null)}>
        <DialogContent className="max-w-4xl p-2">
          {zoom && (
            <img
              src={zoom}
              alt="zoom"
              className="max-h-[80vh] w-full object-contain"
            />
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <div className="space-y-3">
            <div className="text-sm font-medium">二改（对成图做图生图编辑）</div>
            <Input
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              placeholder="编辑指令，例如：把墙换成米白色"
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditOpen(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  const t = editText.trim();
                  if (!t) return;
                  act(() => api.editJob(job.job_id, { instruction: t }), "已提交二改");
                  setEditOpen(false);
                  setEditText("");
                }}
              >
                提交
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
