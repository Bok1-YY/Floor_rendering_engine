"use client";
import { JobEditDialog } from './JobEditDialog';
import { JobCompareDialog } from './JobCompareDialog';
import { JobImageDialogs } from './JobImageDialogs';
import { JobActionBar } from './JobActionBar';
import { JobCandidatePanel } from './JobCandidatePanel';

import { LoaderCircle } from "lucide-react";

const BADGE: Record<string, { label: string; color: string; bg: string }> = {
  queued: { label: "排队", color: "var(--muted-foreground)", bg: "var(--muted)" },
  running: { label: "生成中", color: "var(--primary-foreground)", bg: "var(--primary)" },
  done: { label: "完成", color: "#fff", bg: "var(--success)" },
  partial: { label: "部分完成", color: "var(--warn)", bg: "var(--warn-soft)" },
  failed: { label: "失败", color: "#fff", bg: "var(--destructive)" },
};

import type { JobCardModel } from "./useJobCard";
export function JobCardView({ model }: { model: JobCardModel }) {
  const {
    job,
    active,
    zoom,
    setZoom,
    editOpen,
    setEditOpen,
    editText,
    setEditText,
    editColorMatch,
    setEditColorMatch,
    compareOpen,
    setCompareOpen,
    colorMatch,
    setColorMatch,
    inpaint,
    setInpaint,
    floorVisualize,
    setFloorVisualize,
    regenN,
    setRegenN,
    setActiveModel,
    slots,
    activeSlot,
    activeCandidates,
    activeReview,
    reviewBusy,
    setView,
    stageLine,
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
    regen,
    doEditSubmit,
    onEditorDone,
    setReviewStatus,
    toggleFavorite
  } = model;
  const b = BADGE[job.status] ?? BADGE.queued;
  return (
    <div
      className="animate-scfade rounded-[16px] border border-border bg-card p-[15px] shadow-[0_6px_22px_rgba(120,90,60,.07)] dark:shadow-[0_6px_22px_rgba(0,0,0,.3)]"
    >
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
        <div
          className="mt-2.5 line-clamp-2 rounded-[9px] bg-destructive-soft px-[11px] py-[9px] text-[11.5px] leading-relaxed text-destructive-ink"
          title={job.operation_error || job.error}
        >
          {job.error_kb ? (
            <span className="font-semibold">{job.error_kb.title} · </span>
          ) : null}
          {job.operation_error || job.error}
        </div>
      )}

      {active && slots.length === 0 && (
        <div className="mt-[11px] grid grid-cols-2 gap-2.5">
          {(job.model_targets || ["b2", "pro"]).map((key) => {
            const run = job.model_runs?.[key];
            return (
              <div
                key={key}
                className="flex aspect-[4/3] flex-col items-center justify-center gap-2 rounded-[11px] border border-dashed border-border-strong bg-panel text-[11.5px] font-semibold text-muted-foreground"
              >
                {run?.status === "running" || (!run?.stage?.includes("排队") && key === job.model_targets?.[0]) ? (
                  <LoaderCircle size={16} className="animate-dc-spin text-primary" />
                ) : null}
                <span>{run?.label || key.toUpperCase()} · {run?.stage || "排队中"}</span>
              </div>
            );
          })}
        </div>
      )}

      <JobCandidatePanel
        job={job}
        setZoom={setZoom}
        setColorMatch={setColorMatch}
        setInpaint={setInpaint}
        setFloorVisualize={setFloorVisualize}
        setActiveModel={setActiveModel}
        slots={slots}
        activeSlot={activeSlot}
        activeCandidates={activeCandidates}
        activeReview={activeReview}
        reviewBusy={reviewBusy}
        setView={setView}
        terminal={terminal}
        setReviewStatus={setReviewStatus}
        toggleFavorite={toggleFavorite}
      />
      <JobActionBar
        job={job}
        active={active}
        setEditOpen={setEditOpen}
        setCompareOpen={setCompareOpen}
        regenN={regenN}
        setRegenN={setRegenN}
        terminal={terminal}
        compareAfter={compareAfter}
        isFree={isFree}
        hasAmbiguousBilling={hasAmbiguousBilling}
        retryFailedRuns={retryFailedRuns}
        retryUpscale={retryUpscale}
        remove={remove}
        cancel={cancel}
        restore={restore}
        polish={polish}
        regen={regen}
      />
      <JobImageDialogs
        job={job}
        zoom={zoom}
        setZoom={setZoom}
        colorMatch={colorMatch}
        setColorMatch={setColorMatch}
        inpaint={inpaint}
        setInpaint={setInpaint}
        floorVisualize={floorVisualize}
        setFloorVisualize={setFloorVisualize}
        onEditorDone={onEditorDone}
      />
      <JobCompareDialog job={job} compareOpen={compareOpen} setCompareOpen={setCompareOpen} compareAfter={compareAfter} />
      <JobEditDialog
        editOpen={editOpen}
        setEditOpen={setEditOpen}
        editText={editText}
        setEditText={setEditText}
        editColorMatch={editColorMatch}
        setEditColorMatch={setEditColorMatch}
        doEditSubmit={doEditSubmit}
      />
    </div>
  );
}
