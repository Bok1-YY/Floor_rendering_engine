/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useRef, useState } from "react";
import { Dialog, DialogContent } from "@/components/ui/dialog";

/** 全屏放大：滚轮缩放 + 拖动平移 + 双击复位。url 为 null 时关闭。 */
export function ImageZoom({
  url,
  onClose,
}: {
  url: string | null;
  onClose: () => void;
}) {
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(
    null,
  );
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (url) {
      setScale(1);
      setPos({ x: 0, y: 0 });
    }
  }, [url]);

  return (
    <Dialog open={!!url} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-5xl overflow-hidden p-0">
        {url && (
          <div
            className="flex h-[82vh] w-full select-none items-center justify-center overflow-hidden bg-black/90"
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
              drag.current = { x: e.clientX, y: e.clientY, ox: pos.x, oy: pos.y };
              setDragging(true);
              (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
            }}
            onPointerMove={(e) => {
              if (!drag.current) return;
              setPos({
                x: drag.current.ox + (e.clientX - drag.current.x),
                y: drag.current.oy + (e.clientY - drag.current.y),
              });
            }}
            onPointerUp={() => {
              drag.current = null;
              setDragging(false);
            }}
          >
            <img
              src={url}
              alt="zoom"
              draggable={false}
              className="max-h-full max-w-full object-contain"
              style={{
                transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})`,
                cursor: scale > 1 ? "grab" : "default",
                transition: dragging ? "none" : "transform 0.05s",
              }}
            />
            <div className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded bg-black/60 px-2 py-0.5 text-xs text-white">
              滚轮缩放 · 拖动平移 · 双击复位 · {Math.round(scale * 100)}%
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
