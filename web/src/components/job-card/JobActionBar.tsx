"use client";

import { Columns2, Pencil, RefreshCw, Sparkles, Trash2 } from "lucide-react";

import { cn } from "@/lib/utils";

const REGEN_NS = [1, 2, 4, 6];
const actBtn =
  "h-8 rounded-lg border border-border bg-card px-3 text-[12px] font-semibold text-secondary-foreground transition-colors hover:bg-accent";

import type { JobCardModel } from "./useJobCard";
export function JobActionBar({
  job,
  active,
  setEditOpen,
  setCompareOpen,
  regenN,
  setRegenN,
  terminal,
  compareAfter,
  isFree,
  hasAmbiguousBilling,
  retryFailedRuns,
  retryUpscale,
  remove,
  cancel,
  restore,
  polish,
  regen
}: Pick<JobCardModel, "job" | "active" | "setEditOpen" | "setCompareOpen" | "regenN" | "setRegenN" | "terminal" | "compareAfter" | "isFree" | "hasAmbiguousBilling" | "retryFailedRuns" | "retryUpscale" | "remove" | "cancel" | "restore" | "polish" | "regen">) {
  return <>       {terminal && hasAmbiguousBilling && <p className="mt-2 text-xs text-muted-foreground">结果状态不确定：再次生成可能产生重复费用，请先核对已有结果。</p>}
    <div className="mt-[11px] flex flex-wrap items-center gap-1.5">
      {active && (
        <button
          className={actBtn}
          onClick={cancel}
        >
          停止
        </button>
      )}
      {terminal &&
        job.has_retry &&
        (job.status === "failed" || job.status === "partial") && (
          <button
            className={actBtn}
            onClick={retryFailedRuns}
          >
            {hasAmbiguousBilling ? "再次生成（可能重复计费）" : "重试失败线路"}
          </button>
        )}
      {terminal && job.pending_result_commit && (
        <button className={actBtn} onClick={restore}>图片已保留 · 恢复本地写入</button>
      )}
      {terminal && job.pro_url && !isFree && (
        <button
          className={actBtn}
          onClick={polish}
        >
          <span className="inline-flex items-center gap-1.5"><Sparkles size={13} />磨缝</span>
        </button>
      )}
      {terminal && !job.pending_result_commit && job.model_runs?.sd35?.delivery_status === "upscale_failed" && (
        <button
          className={actBtn}
          onClick={retryUpscale}
        >
          <span className="inline-flex items-center gap-1.5"><RefreshCw size={13} />{job.model_runs?.sd35?.recovery_action === "resume" ? "恢复已有超分请求" : job.model_runs?.sd35?.recovery_action === "confirm" ? "重新超分（可能重复计费）" : "重试超分"}</span>
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
            onClick={() => regen(regenN)}
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

  </>;
}
