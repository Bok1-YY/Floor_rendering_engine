"use client";

import { LoaderCircle, RefreshCw, Sparkles, Upload } from "lucide-react";

import { JobCard } from "@/components/JobCard";

import type { GenerationWorkspaceModel } from "./useGenerationWorkspace";
export function GenerationResults({
  floor,
  jobs,
  refreshJobs,
  clearCompleted,
  cancelAll,
  removeJob,
  setOpenStep,
  floorUploaderRef,
  total,
  activeCount,
  doneCount,
  pct,
  isFreeMode
}: Pick<GenerationWorkspaceModel, "floor" | "jobs" | "refreshJobs" | "clearCompleted" | "cancelAll" | "removeJob" | "setOpenStep" | "floorUploaderRef" | "total" | "activeCount" | "doneCount" | "pct" | "isFreeMode">) {
  return <>      {/* ── 右：任务队列 ── */}
    <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
      <div className="flex flex-none flex-wrap items-center justify-between gap-2 border-b border-border px-[22px] py-[14px] max-[1080px]:px-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2.5">
          <div className="text-[14.5px] font-bold">结果工作区</div>
          <div className="inline-flex items-center gap-1.5 rounded-full bg-card px-2.5 py-1 text-[11px] text-muted-foreground shadow-sm ring-1 ring-border">
            {activeCount > 0 && <LoaderCircle size={12} className="animate-dc-spin text-primary" />}
            进行中 {activeCount} · 完成 {doneCount}/{total}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={refreshJobs}
            title="刷新任务"
            aria-label="刷新任务"
            className="flex size-[30px] items-center justify-center rounded-lg border border-border bg-card text-secondary-foreground hover:bg-accent"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={clearCompleted}
            className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[12px] font-semibold text-secondary-foreground hover:bg-accent"
          >
            清除已完成任务卡
          </button>
          <button
            onClick={cancelAll}
            className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[12px] font-semibold text-destructive hover:bg-destructive-soft"
          >
            全部停止
          </button>
        </div>
      </div>

      {activeCount > 0 && (
        <div className="flex-none border-b border-border bg-card px-[22px] py-[11px] max-[1080px]:px-4">
          <div className="mb-1.5 flex justify-between text-[11.5px] text-muted-foreground">
            <span>
              进行中 {activeCount} · 完成 {doneCount} / 共 {total}
            </span>
            <span className="font-bold text-foreground">{pct}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-md bg-muted">
            <div
              className="h-full rounded-md bg-primary transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-[22px] py-[18px] max-[1080px]:px-4 max-[1080px]:py-4">
        {jobs.length === 0 ? (
          <div className="flex min-h-full flex-col items-center justify-center gap-5 px-6 py-8 text-center">
            <div className="flex size-16 items-center justify-center rounded-[20px] bg-accent text-accent-foreground">
              <Sparkles size={29} strokeWidth={1.8} />
            </div>
            <div>
              <div className="text-[16px] font-extrabold text-foreground">
                {!floor ? "从一块地板小样开始" : "参数已就绪，开始第一批出图"}
              </div>
              <p className="mt-2 max-w-[430px] text-[12.5px] leading-[1.8] text-muted-foreground">
                {!floor
                  ? "上传产品小样后，系统会自动识别色调并推荐场景配方。三步完成配置，结果会实时出现在这里。"
                  : "确认左侧场景和输出参数，点击生成效果图；B2 与 Pro 可以并行提交。"}
              </p>
            </div>
            <div className="flex max-w-full flex-wrap justify-center gap-2.5">
              {["上传产品小样", "选择场景参数", "生成并评审"].map((label, index) => (
                <div key={label} className="w-[150px] rounded-xl border border-border bg-card px-3 py-3 text-left shadow-sm">
                  <div className="text-[10.5px] font-extrabold tracking-[0.1em] text-primary">0{index + 1}</div>
                  <div className="mt-1 text-[12px] font-bold text-foreground">{label}</div>
                </div>
              ))}
            </div>
            {!floor && !isFreeMode && (
              <button
                type="button"
                onClick={() => {
                  setOpenStep(1);
                  window.setTimeout(() => floorUploaderRef.current?.open(), 0);
                }}
                className="inline-flex h-10 items-center gap-2 rounded-[10px] bg-primary px-5 text-[13px] font-bold text-primary-foreground shadow-[0_6px_16px_rgba(193,95,60,.25)] hover:bg-primary-hover"
              >
                <Upload size={15} />
                上传地板小样
              </button>
            )}
          </div>
        ) : (
          <div className="grid items-start gap-4 [grid-template-columns:repeat(auto-fill,minmax(320px,1fr))] max-[1100px]:[grid-template-columns:1fr]">
            {jobs.map((j) => (
              <JobCard
                key={j.job_id}
                initial={j}
                onRemove={removeJob}
              />
            ))}
          </div>
        )}
      </div>
    </section>

  </>;
}
