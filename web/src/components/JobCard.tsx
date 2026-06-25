/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useJobStream } from "@/hooks/useJobStream";
import type { JobView } from "@/lib/types";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";
const STATUS: Record<string, { label: string; variant: BadgeVariant }> = {
  queued: { label: "排队", variant: "secondary" },
  running: { label: "生成中", variant: "default" },
  done: { label: "完成", variant: "default" },
  partial: { label: "部分完成", variant: "outline" },
  failed: { label: "失败", variant: "destructive" },
};

export function JobCard({ initial }: { initial: JobView }) {
  const [job, setJob] = useState<JobView>(initial);
  const [zoom, setZoom] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState("");

  const onUpdate = useCallback((j: JobView) => setJob(j), []);
  const active =
    job.status === "queued" || job.status === "running" || job.pro_polishing;
  useJobStream(active ? job.job_id : null, onUpdate);

  async function act(fn: () => Promise<JobView>, okMsg?: string) {
    try {
      const j = await fn();
      setJob(j);
      if (okMsg) toast.success(okMsg);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const st = STATUS[job.status] ?? STATUS.queued;

  const models: {
    key: "b2" | "pro";
    name: string;
    url: string;
    thumb: string;
    idx: number;
    total: number;
  }[] = [];
  if (job.b2_url)
    models.push({
      key: "b2",
      name: "B2",
      url: job.b2_url,
      thumb: job.b2_thumb,
      idx: job.b2_idx,
      total: job.b2_total,
    });
  if (job.pro_url)
    models.push({
      key: "pro",
      name: "Pro",
      url: job.pro_url,
      thumb: job.pro_thumb,
      idx: job.pro_idx,
      total: job.pro_total,
    });

  const stageLine =
    [job.b2_stage && `B2 ${job.b2_stage}`, job.pro_stage && `Pro ${job.pro_stage}`]
      .filter(Boolean)
      .join(" · ") || "处理中…";

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

      {models.length > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {models.map((m) => (
            <div key={m.key} className="space-y-1">
              <img
                src={api.imgUrl(m.thumb)}
                alt={m.name}
                onClick={() => setZoom(api.imgUrl(m.url))}
                className="aspect-[4/3] w-full cursor-zoom-in rounded-lg border object-cover"
              />
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {m.name}
                  {m.total > 1 ? ` ‹${m.idx + 1}/${m.total}›` : ""}
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

      <div className="mt-2 flex flex-wrap gap-1.5">
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
        {(job.status === "failed" || job.status === "partial") && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => act(() => api.retryJob(job.job_id), "已重试")}
          >
            重试
          </Button>
        )}
        {!active && job.pro_url && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => act(() => api.polishJob(job.job_id), "已提交磨缝")}
          >
            🪄 磨缝
          </Button>
        )}
        {!active && (job.pro_url || job.b2_url) && (
          <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
            ✏️ 二改
          </Button>
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
            <div className="text-sm font-medium">
              二改（对成图做图生图编辑）
            </div>
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
