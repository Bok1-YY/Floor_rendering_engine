/* eslint-disable @next/next/no-img-element */
"use client";

import { HINT_STYLES } from "@/lib/color-match/config";
import type { useColorMatchSession } from "./useColorMatchSession";
export function ColorDiagnostics(vm: ReturnType<typeof useColorMatchSession>) {
  const { scope, previewing, analyzing, analysis } = vm.state;
  const { setZoom, applySuggestedAdjustments } = vm.actions;

  const { suggestionValues } = vm.display;

  return (<div className="rounded-[12px] border border-border bg-panel/45 p-3">
    <div className="mb-2.5 flex items-center gap-2">
      <div className="text-[12.5px] font-bold text-secondary-foreground">地板光照三区诊断</div>
      {analyzing && (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" className="animate-dc-spin text-primary">
          <path d="M21 12a9 9 0 1 1-6.2-8.6" />
        </svg>
      )}
      <span className="ml-auto text-[10.5px] text-muted-foreground">截图来自未调色原图</span>
    </div>

    {analyzing && !analysis ? (
      <div className="grid grid-cols-3 gap-2 max-[850px]:grid-cols-1">
        {[0, 1, 2].map((item) => (
          <div key={item} className="h-[112px] animate-pulse rounded-[9px] border border-border bg-card/70" />
        ))}
      </div>
    ) : analysis ? (
      <>
        <div className="grid grid-cols-3 gap-2 max-[850px]:grid-cols-1">
          {analysis.zones.map((zone) => (
            <div key={zone.zone} className="flex min-w-0 gap-2 rounded-[9px] border border-border bg-card p-2">
              {zone.preview ? (
                <button
                  type="button"
                  title={`放大${zone.label}截图`}
                  onClick={() => setZoom({ url: zone.preview! })}
                  className="h-[82px] w-[110px] flex-none overflow-hidden rounded-[7px] border border-border bg-black/5"
                >
                  <img src={zone.preview} alt={`${zone.label}地板截图`} className="h-full w-full object-cover" />
                </button>
              ) : (
                <div className="flex h-[82px] w-[110px] flex-none items-center justify-center rounded-[7px] border border-dashed border-border bg-panel px-2 text-center text-[10px] text-muted-foreground">
                  未提取到明显区域
                </div>
              )}
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-[11.5px] font-bold text-secondary-foreground">
                  {zone.label}
                  {zone.luminance !== null && (
                    <span className="text-[9.5px] font-medium text-muted-foreground">L {zone.luminance}</span>
                  )}
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {zone.hints.map((hint, index) => (
                    <span
                      key={`${hint.code}:${index}`}
                      className={`rounded border px-1.5 py-0.5 text-[9.5px] leading-4 ${HINT_STYLES[hint.code]}`}
                    >
                      {hint.text}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-[9px] border border-border bg-card px-3 py-2">
          <div className="min-w-[240px] flex-1">
            <div className="text-[11px] font-bold text-secondary-foreground">综合建议</div>
            <div className="mt-0.5 text-[10.5px] text-muted-foreground">{analysis.summary}</div>
          </div>
          <div className="flex flex-wrap items-center gap-1">
            {suggestionValues.length ? suggestionValues.map((value) => (
              <span key={value} className="rounded-md bg-accent px-2 py-1 text-[10.5px] font-bold text-accent-foreground">{value}</span>
            )) : (
              <span className="text-[10.5px] font-semibold text-muted-foreground">建议参数均为 0</span>
            )}
          </div>
          <button
            type="button"
            onClick={applySuggestedAdjustments}
            disabled={analysis.status === "insufficient_region" || suggestionValues.length === 0 || previewing}
            className="h-8 flex-none rounded-[8px] bg-primary px-3 text-[11.5px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-45"
          >
            应用建议参数
          </button>
        </div>
      </>
    ) : (
      <div className="rounded-[9px] border border-dashed border-border py-7 text-center text-[11px] text-muted-foreground">
        {scope === "floor_mask" ? "生成有效地板蒙版后自动诊断" : "框选地板后自动生成三区截图和调色建议"}
      </div>
    )}
  </div>);
}
