/* eslint-disable @next/next/no-img-element */
"use client";
import { useRef, useState } from "react";

/**
 * 前后对比滑块：before（原房间图）在下层完整显示，after（出图）在上层被 clip-path
 * 从左侧裁掉 pos%，拖动中缝把手左右滑动对比。纯 pointer events，无第三方依赖。
 */
export function CompareSlider({
  before,
  after,
  beforeLabel = "原图",
  afterLabel = "效果图",
}: {
  before: string;
  after: string;
  beforeLabel?: string;
  afterLabel?: string;
}) {
  const [pos, setPos] = useState(50);
  const [beforeRatio, setBeforeRatio] = useState<number | null>(null);
  const [afterRatio, setAfterRatio] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const ratioMismatch =
    beforeRatio != null &&
    afterRatio != null &&
    Math.abs(beforeRatio - afterRatio) / beforeRatio > 0.01;

  function setFromClientX(x: number) {
    const el = box.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return;
    setPos(Math.max(0, Math.min(100, ((x - r.left) / r.width) * 100)));
  }

  if (ratioMismatch) {
    return (
      <div className="space-y-2.5">
        <div className="rounded-lg bg-warn-soft px-3 py-2 text-[12px] font-semibold text-warn">
          两张图片比例不同，无法精确叠加，已改为双栏完整对照。
        </div>
        <div className="grid grid-cols-2 gap-3 max-[760px]:grid-cols-1">
          {[
            { src: before, label: beforeLabel },
            { src: after, label: afterLabel },
          ].map((item) => (
            <div key={item.label} className="min-w-0">
              <div className="mb-1.5 text-[11px] font-bold text-muted-foreground">
                {item.label}
              </div>
              <div className="flex max-h-[70vh] items-center justify-center overflow-hidden rounded-[10px] border border-border bg-black/5">
                <img
                  src={item.src}
                  alt={item.label}
                  className="max-h-[70vh] w-full object-contain"
                  draggable={false}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div
      ref={box}
      className="relative select-none overflow-hidden rounded-[10px] border border-border"
      style={{ touchAction: "none" }}
      onPointerDown={(e) => {
        dragging.current = true;
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        setFromClientX(e.clientX);
      }}
      onPointerMove={(e) => dragging.current && setFromClientX(e.clientX)}
      onPointerUp={() => (dragging.current = false)}
      onPointerCancel={() => (dragging.current = false)}
    >
      {/* 底层：原图（定义容器高度） */}
      <img
        src={before}
        alt={beforeLabel}
        className="block max-h-[70vh] w-full object-contain"
        draggable={false}
        onLoad={(e) =>
          setBeforeRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)
        }
      />
      {/* 上层：效果图，左侧被裁掉 pos%（右半边显示效果图） */}
      <img
        src={after}
        alt={afterLabel}
        draggable={false}
        className="absolute inset-0 h-full w-full object-contain"
        style={{ clipPath: `inset(0 0 0 ${pos}%)` }}
        onLoad={(e) =>
          setAfterRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)
        }
      />
      {/* 中缝把手 */}
      <div
        className="absolute bottom-0 top-0 w-[2px] bg-white shadow-[0_0_6px_rgba(0,0,0,.4)]"
        style={{ left: `${pos}%` }}
      >
        <span className="absolute left-1/2 top-1/2 flex h-[30px] w-[30px] -translate-x-1/2 -translate-y-1/2 cursor-ew-resize items-center justify-center rounded-full bg-white text-[12px] font-bold text-secondary-foreground shadow-[0_2px_8px_rgba(0,0,0,.3)]">
          ⇔
        </span>
      </div>
      {/* 角标 */}
      <span className="absolute left-[9px] top-[9px] rounded-md bg-[rgba(26,24,21,.55)] px-[8px] py-[3px] text-[11px] font-bold text-white">
        {beforeLabel}
      </span>
      <span className="absolute right-[9px] top-[9px] rounded-md bg-[rgba(26,24,21,.55)] px-[8px] py-[3px] text-[11px] font-bold text-white">
        {afterLabel}
      </span>
    </div>
  );
}
