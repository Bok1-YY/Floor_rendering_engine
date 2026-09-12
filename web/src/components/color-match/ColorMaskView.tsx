/* eslint-disable @next/next/no-img-element */
"use client";

import { api } from "@/lib/api";

import { Slider } from "@/components/ui/slider";
import type { useColorMask } from "./useColorMask";
export function ColorMaskView(vm: ReturnType<typeof useColorMask>) {
  const { tool, brush, busy, initialized, canUndo, message } = vm.state;
  const { setTool, setBrush, undo, clear, segment, initialize, onPointerDown, onPointerMove, onPointerUp } = vm.actions;
  const { overlayRef } = vm.canvas; const { imageUrl, compact } = vm.input;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" onClick={() => setTool("include")} className={`rounded-md border px-2.5 py-1 text-[11px] font-bold ${tool === "include" ? "border-green-600 bg-green-600 text-white" : "border-border bg-panel"}`}>绿色笔 · 补地板</button>
        <button type="button" onClick={() => setTool("exclude")} className={`rounded-md border px-2.5 py-1 text-[11px] font-bold ${tool === "exclude" ? "border-red-500 bg-red-500 text-white" : "border-border bg-panel"}`}>红色笔 · 排除</button>
        <span className="ml-1 text-[10.5px] text-muted-foreground">笔刷</span>
        <div className="w-24"><Slider value={brush} min={8} max={96} step={2} onValueChange={(value) => setBrush(Array.isArray(value) ? value[0] : value as number)} /></div>
        <button type="button" disabled={busy || !canUndo} onClick={() => void undo()} className="rounded-md px-2 py-1 text-[11px] font-semibold hover:bg-accent disabled:opacity-40">撤销</button>
        <button type="button" disabled={busy} onClick={clear} className="rounded-md px-2 py-1 text-[11px] font-semibold hover:bg-accent disabled:opacity-40">清空</button>
        <button type="button" disabled={busy || !initialized} onClick={() => void segment(true)} className="ml-auto rounded-md border border-border bg-panel px-2 py-1 text-[11px] font-semibold hover:bg-accent disabled:opacity-40">重新自动识别</button>
      </div>
      <div className="relative mx-auto w-fit max-w-full select-none overflow-hidden rounded-[10px] border border-border bg-black/5" style={{ touchAction: "none" }}>
        <img src={api.imgUrl(imageUrl)} alt="原图" draggable={false} onLoad={(event) => initialize(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)} className={`block h-auto w-auto max-w-full object-contain ${compact ? "max-h-[44vh]" : "max-h-[62vh]"}`} />
        <canvas ref={overlayRef} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} className="absolute inset-0 h-full w-full cursor-crosshair" />
        {busy && <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/20 text-[12px] font-bold text-white">AI 正在细化蒙版…</div>}
      </div>
      <div className="min-h-4 text-[10.5px] text-muted-foreground">{message}</div>
    </div>
  );
}