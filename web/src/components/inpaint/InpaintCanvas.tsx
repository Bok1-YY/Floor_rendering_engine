/* eslint-disable @next/next/no-img-element */
"use client";
import { api } from "@/lib/api";

import type { useInpaintSession } from "./useInpaintSession";
export function InpaintCanvas(vm: ReturnType<typeof useInpaintSession>) {
  const { nCount, tool, brush, task } = vm.state;
  const { onDown, onMove, onUp, onImgLoad, cancelRunning } = vm.actions;
  const { boxRef, cursorRef, candidateCanvasRef, canvasRef } = vm.canvas;

  const { srcUrl } = vm.input;
  return (<div className="flex justify-center overflow-hidden rounded-[10px] border border-border bg-black/5">
    <div
      ref={boxRef}
      className="relative select-none"
      style={{ touchAction: "none", cursor: tool === "smart" ? "crosshair" : "none" }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
      onPointerLeave={() => {
        if (cursorRef.current) cursorRef.current.style.display = "none";
      }}
    >
      <img
        src={api.imgUrl(srcUrl)}
        alt="待修补图"
        draggable={false}
        onLoad={onImgLoad}
        className="block max-h-[58vh] max-w-full"
      />
      <canvas
        ref={candidateCanvasRef}
        className="pointer-events-none absolute inset-0 h-full w-full"
      />
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute inset-0 h-full w-full opacity-60"
      />
      {/* 笔刷光标 */}
      <div
        ref={cursorRef}
        className="pointer-events-none absolute hidden -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow-[0_0_0_1px_rgba(0,0,0,.6)]"
        style={{ width: brush, height: brush }}
      />
      {task && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/45 text-white">
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinecap="round"
            className="animate-dc-spin"
          >
            <path d="M21 12a9 9 0 1 1-6.2-8.6" />
          </svg>
          <span className="text-[12.5px] font-semibold">
            {task.stage || `生成 ${nCount} 个候选中…`}
          </span>
          <button
            onClick={cancelRunning}
            className="mt-1 rounded-md border border-white/60 px-2.5 py-0.5 text-[11.5px] font-semibold hover:bg-white/15"
          >
            取消
          </button>
        </div>
      )}
    </div>
  </div>);
}
