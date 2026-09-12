/* eslint-disable @next/next/no-img-element */
"use client";
import { api } from "@/lib/api";

import { ColorMaskEditor } from "@/components/ColorMaskEditor";

import type { useColorMatchSession } from "./useColorMatchSession";
export function ColorSourcePanel(vm: ReturnType<typeof useColorMatchSession>) {
  const { scope, advancedOpen, rect } = vm.state;
  const { setZoom, setMaskBusy, onDown, onMove, onUp, onLocalMaskChange } = vm.actions;
  const { box } = vm.canvas;
  const { paneTitle, pct } = vm.display;
  const { srcUrl, imageRel } = vm.input;
  return (<div className="min-w-0">
    <div className={paneTitle}>
      {scope === "floor_mask" ? "原图 · AI 蒙版与画笔修正" : "原图 · 拖动框选地板"}
      <button
        title="放大原图"
        onClick={() => setZoom({ url: api.imgUrl(srcUrl) })}
        className="ml-auto rounded px-1.5 text-[12px] font-semibold text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        🔍
      </button>
    </div>
    {scope === "floor_mask" ? (
      <ColorMaskEditor
        imageUrl={srcUrl}
        imageRel={imageRel}
        compact={advancedOpen}
        onMaskChange={onLocalMaskChange}
        onBusyChange={setMaskBusy}
      />
    ) : (
      <div ref={box} className="relative cursor-crosshair select-none overflow-hidden rounded-[10px] border border-border bg-black/5" style={{ touchAction: "none" }} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
        <img
          src={api.imgUrl(srcUrl)}
          alt="原图"
          className={`block w-full object-contain ${advancedOpen ? "max-h-[44vh]" : "max-h-[62vh]"}`}
          draggable={false}
        />
        <div className="pointer-events-none absolute inset-x-0 top-0 bg-black/45" style={{ height: pct(rect.y) }} />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-black/45" style={{ height: pct(Math.max(0, 1 - rect.y - rect.h)) }} />
        <div className="pointer-events-none absolute bg-black/45" style={{ top: pct(rect.y), height: pct(rect.h), left: 0, width: pct(rect.x) }} />
        <div className="pointer-events-none absolute bg-black/45" style={{ top: pct(rect.y), height: pct(rect.h), right: 0, width: pct(Math.max(0, 1 - rect.x - rect.w)) }} />
        <div
          className="pointer-events-none absolute border-2 border-primary shadow-[0_0_0_1px_rgba(255,255,255,.5)]"
          style={{ left: pct(rect.x), top: pct(rect.y), width: pct(rect.w), height: pct(rect.h) }}
        />
      </div>
    )}
  </div>);
}
