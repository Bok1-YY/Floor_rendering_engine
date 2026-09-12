/* eslint-disable @next/next/no-img-element */
"use client";
import { ColorSourcePanel } from "./ColorSourcePanel";
import { ColorResultPanel } from "./ColorResultPanel";
import { ColorDiagnostics } from "./ColorDiagnostics";
import { ColorControls } from "./ColorControls";

import { api } from "@/lib/api";

import { Dialog, DialogContent } from "@/components/ui/dialog";

import { ImageZoom } from "@/components/ImageZoom";

import type { useColorMatchSession } from "./useColorMatchSession";
export function ColorMatchView(vm: ReturnType<typeof useColorMatchSession>) {
  const { scope, algorithm, quality, zoom } = vm.state;
  const { changeScope, changeAlgorithm, setZoom } = vm.actions;

  const { open, onOpenChange, srcUrl } = vm.input;
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        {/* sm:前缀必须带：DialogContent 默认 sm:max-w-sm，无前缀的 max-w 在 sm+ 会被它覆盖 */}
        <DialogContent className="max-h-[94vh] max-w-[96vw] overflow-y-auto sm:max-w-[min(96vw,1500px)]">
          <div className="space-y-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="text-[15.5px] font-bold">地板校色</div>
                <div className="inline-flex rounded-lg border border-border bg-panel p-0.5">
                  <button type="button" onClick={() => changeScope("floor_mask")} className={`rounded-md px-2.5 py-1 text-[11px] font-bold ${scope === "floor_mask" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}>地板局部（默认）</button>
                  <button type="button" onClick={() => changeScope("global")} className={`rounded-md px-2.5 py-1 text-[11px] font-bold ${scope === "global" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}>全图校准（兼容）</button>
                </div>
                <div className="ml-auto flex items-center gap-1.5 max-[720px]:ml-0">
                  <span className="text-[10.5px] font-bold text-muted-foreground">对色引擎</span>
                  <div className="inline-flex rounded-lg border border-border bg-panel p-0.5">
                    <button type="button" onClick={() => changeAlgorithm("classic")} className={`rounded-md px-2.5 py-1 text-[11px] font-bold ${algorithm === "classic" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}>经典 1.0</button>
                    <button type="button" onClick={() => changeAlgorithm("distribution")} className={`rounded-md px-2.5 py-1 text-[11px] font-bold ${algorithm === "distribution" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}>精细 2.0</button>
                  </div>
                </div>
              </div>
              <div className="mt-0.5 text-[12px] text-muted-foreground">
                {scope === "floor_mask"
                  ? "AI 先识别地板；绿色笔补选、红色笔排除。校色严格限制在绿色蒙版内，墙面和家具保持原样。"
                  : "兼容旧方式：左图框选地板作为取样，校色参数作用于整张效果图。"}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 max-[1000px]:grid-cols-1">
              {/* 左栏：原图 + 框选 */}
              <ColorSourcePanel {...vm} />

              {/* 右栏：实时校色结果（canvas） */}
              <ColorResultPanel {...vm} />
            </div>

            <ColorDiagnostics {...vm} />

            {quality && (
              <div className={`rounded-[12px] border p-3 ${quality.level === "low" ? "border-amber-400/60 bg-amber-50/70 dark:bg-amber-950/20" : "border-border bg-panel/45"}`}>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex h-12 w-12 flex-none items-center justify-center rounded-full border-4 border-primary/25 bg-card text-[16px] font-black text-secondary-foreground">
                    {quality.score}
                  </div>
                  <div className="min-w-[240px] flex-1">
                    <div className="text-[12.5px] font-bold text-secondary-foreground">校色可信度 · {quality.summary}</div>
                    <div className="mt-0.5 text-[10.5px] text-muted-foreground">
                      可用像素 {Math.round(quality.source_usable_ratio * 100)}% · 自动算法预计 ΔE00 {quality.initial_delta_e00.toFixed(1)} → {quality.estimated_delta_e00.toFixed(1)} ·
                      {quality.applied_illumination_mode === "off" ? " 空间光照校正关闭" : ` 已应用 ${quality.applied_illumination_mode} 光照校正`}
                    </div>
                    {!!quality.warnings.length && (
                      <div className="mt-1 text-[10.5px] font-semibold text-amber-700 dark:text-amber-300">{quality.warnings.join("；")}</div>
                    )}
                  </div>
                  {quality.diagnostic_overlay && (
                    <button type="button" onClick={() => setZoom({ url: quality.diagnostic_overlay!, baseUrl: api.imgUrl(srcUrl), overlayOpacity: 1 })} className="relative h-[72px] w-[112px] overflow-hidden rounded-lg border border-border bg-black/5" title="放大问题像素诊断图">
                      <img src={api.imgUrl(srcUrl)} alt="" className="absolute inset-0 h-full w-full object-contain" />
                      <img src={quality.diagnostic_overlay} alt="问题像素诊断" className="absolute inset-0 h-full w-full object-contain" />
                    </button>
                  )}
                </div>
                <div className="mt-2 text-[9.5px] text-muted-foreground">诊断图：绿色可用，红色反光/过曝，蓝色深阴影，黄色异色离群点。低可信度只提示，不阻止保存。</div>
              </div>
            )}

            <ColorControls {...vm} />
          </div>
        </DialogContent>
      </Dialog>
      <ImageZoom
        url={zoom?.url || null}
        baseUrl={zoom?.baseUrl}
        overlayOpacity={zoom?.overlayOpacity}
        onClose={() => setZoom(null)}
      />
    </>
  );
}
