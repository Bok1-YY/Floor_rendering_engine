/* eslint-disable @next/next/no-img-element */
"use client";
import { api } from "@/lib/api";

import type { useColorMatchSession } from "./useColorMatchSession";
export function ColorResultPanel(vm: ReturnType<typeof useColorMatchSession>) {
  const { scope, algorithm, advancedOpen, adjustmentMode, previewing, appliedFallbackReason, previewEngineError, appliedAlgorithm, illuminationMode, appliedIlluminationMode, hasPreview, ref, showRefPatch } = vm.state;
  const { setZoom, setShowRefPatch, zoomResult } = vm.actions;
  const { canvasRef } = vm.canvas;
  const { paneTitle, hasAdjustments, modeSwitchPending, previewEngineStatus, appliedAlgorithmLabel } = vm.display;
  return (<div className="min-w-0">
    <div className={paneTitle}>
      预览 · {adjustmentMode === "auto" ? "自动校准" : hasAdjustments ? "手动参数" : "原图"}
      {previewing && (
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.6"
          strokeLinecap="round"
          className="animate-dc-spin text-primary"
        >
          <path d="M21 12a9 9 0 1 1-6.2-8.6" />
        </svg>
      )}
      <span className="ml-auto text-[11px] font-semibold text-muted-foreground">
        点击放大看细节
      </span>
    </div>
    <div
      title={appliedFallbackReason || undefined}
      className={`mb-1.5 rounded-md border px-2 py-1 text-[10.5px] font-bold ${previewEngineError ? "border-amber-300/60 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/35 dark:text-amber-200" : modeSwitchPending || appliedAlgorithm === null ? "border-sky-300/60 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950/35 dark:text-sky-200" : algorithm !== appliedAlgorithm || illuminationMode !== appliedIlluminationMode ? "border-amber-300/60 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/35 dark:text-amber-200" : "border-emerald-300/60 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/35 dark:text-emerald-200"}`}
    >
      {previewEngineStatus}
      {adjustmentMode === "manual" && appliedAlgorithm && ` · 手动参数基于${appliedAlgorithmLabel}自动基准`}
    </div>
    <div className="relative overflow-hidden rounded-[10px] border border-border bg-black/5">
      <canvas
        ref={canvasRef}
        onClick={zoomResult}
        className={`block w-full cursor-zoom-in object-contain ${advancedOpen ? "max-h-[44vh]" : "max-h-[62vh]"}`}
      />
      {!hasPreview && (
        <div className="absolute inset-0 flex items-center justify-center text-[12.5px] text-muted-foreground">
          {previewing
            ? "首次预览生成中…"
            : !ref.path
              ? "请先在下方选择参照小样"
              : scope === "floor_mask"
                ? "等待有效地板蒙版"
                : "框选地板区域后自动出结果"}
        </div>
      )}
      {/* 小样参照贴片：校色的目标色，贴着结果图对比 */}
      {ref.url && showRefPatch && (
        <div className="absolute right-2 top-2 overflow-hidden rounded-[10px] border-2 border-white/90 shadow-[0_4px_14px_rgba(0,0,0,.35)]">
          <img
            src={api.imgUrl(ref.url)}
            alt="小样参照"
            title="点击放大小样"
            onClick={(e) => {
              e.stopPropagation();
              setZoom({ url: api.imgUrl(ref.url) });
            }}
            className="block h-[clamp(100px,14vw,180px)] w-[clamp(100px,14vw,180px)] cursor-zoom-in object-cover"
          />
          <span className="pointer-events-none absolute left-0 top-0 rounded-br-md bg-[rgba(26,24,21,.6)] px-[6px] py-[2px] text-[10px] font-bold text-white">
            小样
          </span>
          <button
            title="收起小样"
            onClick={(e) => {
              e.stopPropagation();
              setShowRefPatch(false);
            }}
            className="absolute right-0 top-0 flex h-[18px] w-[18px] items-center justify-center rounded-bl-md bg-[rgba(26,24,21,.6)] text-[10px] font-bold text-white hover:bg-[rgba(26,24,21,.85)]"
          >
            ✕
          </button>
        </div>
      )}
      {ref.url && !showRefPatch && (
        <button
          title="显示小样参照"
          onClick={(e) => {
            e.stopPropagation();
            setShowRefPatch(true);
          }}
          className="absolute right-2 top-2 rounded-lg bg-[rgba(26,24,21,.6)] px-2 py-1 text-[11px] font-bold text-white hover:bg-[rgba(26,24,21,.85)]"
        >
          小样
        </button>
      )}
    </div>
  </div>);
}
