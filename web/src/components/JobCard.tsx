/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useJobStream } from "@/hooks/useJobStream";
import { notifyJobEnd } from "@/lib/notify";
import type { JobView, ModelKey, ModelRunView } from "@/lib/types";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ImageZoom } from "@/components/ImageZoom";
import { CompareSlider } from "@/components/CompareSlider";
import { ColorMatchDialog } from "@/components/ColorMatchDialog";
import { InpaintDialog } from "@/components/InpaintDialog";
import { FloorVisualizeDialog } from "@/components/FloorVisualizeDialog";
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
  "h-[28px] rounded-lg border border-border bg-card px-[11px] text-[11.5px] font-semibold text-secondary-foreground transition-colors hover:bg-accent";

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
  // 候选切换的本地覆盖（不影响后端"当前下标"，仅前端浏览）
  const [view, setView] = useState<Partial<Record<ModelKey, SlotView>>>({});

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
    const totals = JSON.stringify(
      Object.fromEntries(
        Object.entries(j.model_runs || {}).map(([key, run]) => [key, run?.total || 0]),
      ),
    );
    if (totals !== totalsRef.current) {
      totalsRef.current = totals;
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

  async function nav(model: ModelKey, delta: number) {
    const run = job.model_runs?.[model];
    const total = run?.total || 0;
    if (total <= 1) return;
    const cur = view[model]?.idx ?? run?.idx ?? 0;
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
    key: ModelKey;
    name: string;
    url: string;
    thumb: string;
    idx: number;
    total: number;
    run: ModelRunView;
  }[] = [];
  for (const key of job.model_targets || (["b2", "pro"] as ModelKey[])) {
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
    });
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

  // 前后对比：仅替换类工作流有房间原图（room_url）；效果图优先取当前浏览中的 Pro 候选，无 Pro 用 B2
  const compareAfter =
    (slots.find((s) => s.key === "pro") ?? slots.find((s) => s.key === "b2") ?? slots[0])?.url || "";

  return (
    <div className="animate-scfade rounded-[14px] border border-border bg-card p-[13px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-bold text-foreground">
            {job.display_name}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
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
        <div className="mt-2.5 rounded-[9px] bg-destructive-soft px-[11px] py-[9px] text-[11.5px] leading-relaxed text-destructive-ink">
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
              <div className="mt-[5px] flex items-center justify-between text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1">
                  {m.name}
                  {m.total > 1 && (
                    <>
                      <button
                        onClick={() => nav(m.key, -1)}
                        disabled={m.idx <= 0}
                        className="rounded px-1 hover:bg-accent disabled:opacity-30"
                      >
                        ‹
                      </button>
                      <span>
                        {m.idx + 1}/{m.total}
                      </span>
                      <button
                        onClick={() => nav(m.key, 1)}
                        disabled={m.idx >= m.total - 1}
                        className="rounded px-1 hover:bg-accent disabled:opacity-30"
                      >
                        ›
                      </button>
                    </>
                  )}
                </span>
                <span className="flex items-center gap-2">
                  {terminal && job.floor_path && m.url.startsWith("/outputs/") && (
                    <button
                      title={`把原始地板小样确定性投影到这张 ${m.name} 图`}
                      onClick={() =>
                        setFloorVisualize({
                          stage: m.key,
                          srcUrl: m.url,
                          imageRel: m.url.slice("/outputs/".length),
                        })
                      }
                      className="hover:text-foreground"
                    >
                      🪵 真实贴地板
                    </button>
                  )}
                  {terminal && m.url.startsWith("/outputs/") && (
                    <button
                      title={`对这张 ${m.name} 图做生成式修补（画笔涂抹移除/添加物体）`}
                      onClick={() =>
                        setInpaint({
                          stage: m.key,
                          srcUrl: m.url,
                          imageRel: m.url.slice("/outputs/".length),
                        })
                      }
                      className="hover:text-foreground"
                    >
                      🖌️ 修补
                    </button>
                  )}
                  {terminal && job.floor_url && m.url.startsWith("/outputs/") && (
                    <button
                      title={`对这张 ${m.name} 图做手动校色（以地板小样为参照）`}
                      onClick={() =>
                        setColorMatch({
                          stage: m.key,
                          srcUrl: m.url,
                          imageRel: m.url.slice("/outputs/".length),
                        })
                      }
                      className="hover:text-foreground"
                    >
                      🎯 校色
                    </button>
                  )}
                  <a
                    href={api.imgUrl(m.url)}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:text-foreground"
                  >
                    ↗ 原图
                  </a>
                </span>
              </div>
              {m.key === "sd35" && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-muted-foreground">
                  {m.run.seed != null && <span>Seed {m.run.seed}</span>}
                  {m.run.delivery_status && <span>交付：{m.run.delivery_status}</span>}
                  {m.run.base_url && m.run.base_url !== m.url && (
                    <a href={api.imgUrl(m.run.base_url)} target="_blank" rel="noreferrer" className="hover:text-foreground">
                      ↗ 1MP 基础图
                    </a>
                  )}
                </div>
              )}
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
        {terminal && job.model_runs?.sd35?.delivery_status === "upscale_failed" && (
          <button
            className={actBtn}
            onClick={() => act(() => api.retrySdUpscale(job.job_id), "已重试 SD 超分")}
          >
            ⤴ 重试超分
          </button>
        )}
        {terminal && (job.pro_url || job.b2_url) && (
          <button className={actBtn} onClick={() => setEditOpen(true)}>
            ✏️ 二改
          </button>
        )}
        {terminal && job.room_url && compareAfter && (
          <button className={actBtn} onClick={() => setCompareOpen(true)}>
            ⇔ 对比
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
              🔄 重抽
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
            ✕ 清除
          </button>
        )}
      </div>

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
