/* eslint-disable @next/next/no-img-element */
"use client";
import { api } from "@/lib/api";
import type { ColorIlluminationMode } from "@/lib/types";

import { Slider } from "@/components/ui/slider";

import { ADJUSTMENT_CONTROLS } from "@/lib/color-match/config";
import type { useColorMatchSession } from "./useColorMatchSession";
export function ColorControls(vm: ReturnType<typeof useColorMatchSession>) {
  const { scope, advancedOpen, adjustmentMode, illuminationMode, ref, strength, maskB64, ready, saving, maskBusy, maskFeather, adjustments } = vm.state;
  const { restoreAuto, setAdvancedOpen, doSave, changeIlluminationMode, restoreOriginal, changeMaskFeather, updateAdjustment, pickRef } = vm.actions;
  const { hasAdjustments } = vm.display;
  const { target } = vm.input;
  return (<div className="space-y-2.5">
    <div className="flex flex-wrap items-center gap-3 lg:flex-nowrap">
      {/* 参照小样 */}
      <div className="flex flex-none items-center gap-2">
        <div className="h-[44px] w-[44px] overflow-hidden rounded-[8px] border border-border bg-panel">
          {ref.url ? (
            <img src={api.imgUrl(ref.url)} alt="参照" className="h-full w-full object-cover" />
          ) : (
            <div className="flex h-full items-center justify-center text-[10px] text-muted-foreground">无</div>
          )}
        </div>
        <label className="cursor-pointer text-[11.5px] font-semibold text-secondary-foreground hover:text-accent-foreground">
          {ref.path ? "换参照" : "选择参照"}
          <input type="file" accept="image/*" className="hidden" onChange={pickRef} />
        </label>
      </div>

      {/* 自动校准强度（客户端即时混合，1% 步进） */}
      <div className="flex min-w-[260px] flex-1 items-center gap-3">
        <span className="flex-none text-[12px] font-semibold text-secondary-foreground" title={scope === "floor_mask" ? "仅在地板蒙版内做稳健色度校准" : "使用框选地板计算偏差，对整张图做自动校准"}>{scope === "floor_mask" ? "地板自动校准" : "全图自动校准"}</span>
        <Slider
          value={strength}
          min={0}
          max={1}
          step={0.01}
          disabled={!ref.path || (scope === "floor_mask" && !maskB64)}
          onValueChange={(v) => {
            const s = Array.isArray(v) ? v[0] : (v as number);
            restoreAuto(s);
          }}
        />
        <span className="w-10 flex-none text-right text-[12px] tabular-nums text-muted-foreground">
          {Math.round(strength * 100)}%
        </span>
      </div>

      <button
        type="button"
        aria-expanded={advancedOpen}
        onClick={() => setAdvancedOpen((value) => !value)}
        className="h-9 flex-none rounded-[9px] border border-border bg-panel px-3 text-[12px] font-bold text-secondary-foreground hover:bg-accent hover:text-accent-foreground"
      >
        高级选项 · {adjustmentMode === "auto" ? "自动基准" : "手动"} {advancedOpen ? "收起" : "展开"}
      </button>

      <button
        onClick={doSave}
        disabled={!ready || saving || maskBusy || (scope === "floor_mask" && !maskB64)}
        title={!ref.path ? "请先选择参照图" : !ready ? "预览更新中…" : undefined}
        className="h-9 flex-none rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
      >
        {saving
          ? "保存中…"
          : !ready
            ? "更新预览中…"
            : target.kind === "job"
              ? "保存为新候选"
              : "保存到记录"}
      </button>
    </div>

    {advancedOpen && (
      <div className="rounded-[10px] border border-border bg-panel/60 p-3">
        <div className="mb-3 rounded-lg border border-border bg-card px-3 py-2">
          <div className="mb-1.5 text-[11.5px] font-bold text-secondary-foreground">空间光照校正</div>
          <div className="flex flex-wrap gap-1.5">
            {(["off", "chroma", "full"] as ColorIlluminationMode[]).map((value) => (
              <button key={value} type="button" onClick={() => changeIlluminationMode(value)} className={`rounded-md px-2.5 py-1.5 text-[11px] font-bold ${illuminationMode === value ? "bg-primary text-primary-foreground" : "bg-panel text-muted-foreground hover:bg-accent"}`}>
                {value === "off" ? "关闭" : value === "chroma" ? "仅色偏" : "色偏 + 明暗"}
              </button>
            ))}
          </div>
          <div className="mt-1.5 text-[10px] text-muted-foreground">仅在大样存在渐变色偏或混合光时开启；开启会自动使用精细算法。</div>
        </div>
        <div className="mb-2 flex items-center justify-between gap-3">
          <div>
            <div className="text-[12px] font-bold text-secondary-foreground">
              以 Gemini 原图为零点的专业调色
            </div>
            <div className="text-[10.5px] text-muted-foreground">
              {scope === "floor_mask"
                ? "自动校准与手动滑块都只作用于地板蒙版；地板明暗保留，只修正偏色"
                : "框选地板只用于计算偏差；自动校准、建议参数和手动滑块均作用于整张效果图"}
            </div>
          </div>
          <div className="flex flex-none items-center gap-1.5">
            <button
              type="button"
              onClick={() => restoreAuto()}
              className="rounded-md px-2 py-1 text-[11px] font-semibold text-accent-foreground hover:bg-accent"
            >
              恢复自动校准
            </button>
            <button
              type="button"
              onClick={restoreOriginal}
              disabled={adjustmentMode === "manual" && !hasAdjustments}
              className="rounded-md px-2 py-1 text-[11px] font-semibold text-accent-foreground hover:bg-accent disabled:opacity-40"
            >
              恢复 Gemini 原图
            </button>
          </div>
        </div>
        {scope === "floor_mask" && (
          <div className="mb-3 flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2">
            <span className="w-[74px] flex-none text-[11.5px] font-semibold text-secondary-foreground">蒙版内羽化</span>
            <Slider
              value={maskFeather}
              min={0}
              max={0.02}
              step={0.001}
              onValueChange={(value) => changeMaskFeather(Array.isArray(value) ? value[0] : value as number)}
            />
            <span className="w-12 flex-none text-right text-[11px] tabular-nums text-muted-foreground">{(maskFeather * 100).toFixed(1)}%</span>
          </div>
        )}
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 max-[850px]:grid-cols-1">
          {ADJUSTMENT_CONTROLS.map((control) => {
            const value = adjustments[control.key];
            const display = control.key === "exposure"
              ? `${value > 0 ? "+" : ""}${value.toFixed(1)} EV`
              : `${value > 0 ? "+" : ""}${Math.round(value)}`;
            return (
              <div key={control.key} className="flex min-w-0 items-center gap-2" title={control.hint}>
                <span className="w-[62px] flex-none text-[11.5px] font-semibold text-secondary-foreground">
                  {control.label}
                </span>
                <Slider
                  value={value}
                  min={control.min}
                  max={control.max}
                  step={control.step}
                  onValueChange={(v) => {
                    const next = Array.isArray(v) ? v[0] : (v as number);
                    updateAdjustment(control.key, next);
                  }}
                />
                <span className="w-[58px] flex-none text-right text-[11px] tabular-nums text-muted-foreground">
                  {display}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    )}
  </div>);
}
