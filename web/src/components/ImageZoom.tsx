/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useRef, useState } from "react";

/** 全屏无边框看图：滚轮缩放 · 拖动平移 · 双击复位 · Esc/点背景/✕ 关闭。props 不变。 */
export function ImageZoom({
  url,
  onClose,
}: {
  url: string | null;
  onClose: () => void;
}) {
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(
    null,
  );
  const moved = useRef(false);

  // url 变化时复位缩放/位移
  useEffect(() => {
    setScale(1);
    setPos({ x: 0, y: 0 });
  }, [url]);

  // Esc 关闭
  useEffect(() => {
    if (!url) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [url, onClose]);

  if (!url) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex select-none items-center justify-center overflow-hidden bg-black/95"
      onWheel={(e) => {
        e.preventDefault();
        setScale((s) =>
          Math.min(8, Math.max(1, s * (e.deltaY < 0 ? 1.15 : 1 / 1.15))),
        );
      }}
      onDoubleClick={() => {
        setScale(1);
        setPos({ x: 0, y: 0 });
      }}
      onPointerDown={(e) => {
        moved.current = false;
        drag.current = { x: e.clientX, y: e.clientY, ox: pos.x, oy: pos.y };
        setDragging(true);
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        const dx = e.clientX - drag.current.x;
        const dy = e.clientY - drag.current.y;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved.current = true;
        setPos({ x: drag.current.ox + dx, y: drag.current.oy + dy });
      }}
      onPointerUp={() => {
        drag.current = null;
        setDragging(false);
      }}
      onClick={(e) => {
        // 点背景（非图片）且不是拖动结束 → 关闭
        if (e.target === e.currentTarget && !moved.current) onClose();
      }}
    >
      <img
        src={url}
        alt="zoom"
        draggable={false}
        className="max-h-[96vh] max-w-[96vw] object-contain"
        style={{
          transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})`,
          cursor: scale > 1 ? "grab" : "default",
          transition: dragging ? "none" : "transform 0.05s",
        }}
      />

      <button
        onClick={onClose}
        title="关闭 (Esc)"
        className="absolute right-4 top-4 flex h-9 w-9 items-center justify-center rounded-full bg-white/15 text-lg text-white backdrop-blur hover:bg-white/25"
      >
        ✕
      </button>

      <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-black/55 px-3 py-1 text-xs text-white/90">
        滚轮缩放 · 拖动平移 · 双击复位 · Esc/点背景关闭 · {Math.round(scale * 100)}%
      </div>
    </div>
  );
}
