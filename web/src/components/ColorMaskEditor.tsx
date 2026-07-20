/* eslint-disable @next/next/no-img-element */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ColorMatchRect } from "@/lib/types";
import { Slider } from "@/components/ui/slider";

type Tool = "include" | "exclude";

type Props = {
  imageUrl: string;
  imageRel: string;
  compact?: boolean;
  onMaskChange: (maskB64: string, rect: ColorMatchRect) => void;
  onBusyChange?: (busy: boolean) => void;
};

function blankCanvas(width: number, height: number) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function exportMask(canvas: HTMLCanvasElement | null, active = true) {
  if (!canvas || !active) return "";
  return canvas.toDataURL("image/png").split(",", 2)[1] || "";
}

function maskBounds(canvas: HTMLCanvasElement): ColorMatchRect {
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return { x: 0, y: 0, w: 1, h: 1 };
  const { width, height } = canvas;
  const data = ctx.getImageData(0, 0, width, height).data;
  let left = width;
  let top = height;
  let right = -1;
  let bottom = -1;
  for (let y = 0; y < height; y += 2) {
    for (let x = 0; x < width; x += 2) {
      if (data[(y * width + x) * 4 + 3] < 128) continue;
      left = Math.min(left, x);
      top = Math.min(top, y);
      right = Math.max(right, x);
      bottom = Math.max(bottom, y);
    }
  }
  if (right < left || bottom < top) return { x: 0, y: 0, w: 1, h: 1 };
  return {
    x: left / width,
    y: top / height,
    w: Math.max(0.02, (right - left + 1) / width),
    h: Math.max(0.02, (bottom - top + 1) / height),
  };
}

export function ColorMaskEditor({ imageUrl, imageRel, compact, onMaskChange, onBusyChange }: Props) {
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const resultRef = useRef<HTMLCanvasElement | null>(null);
  const includeRef = useRef<HTMLCanvasElement | null>(null);
  const excludeRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const lastPointRef = useRef<{ x: number; y: number } | null>(null);
  const requestSeq = useRef(0);
  const hasIncludeRef = useRef(false);
  const hasExcludeRef = useRef(false);
  const hasResultRef = useRef(false);
  const snapshotsRef = useRef<Array<{ include: string; exclude: string; result: string }>>([]);
  const [tool, setTool] = useState<Tool>("include");
  const [brush, setBrush] = useState(32);
  const [busy, setBusy] = useState(false);
  const [initialized, setInitialized] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [message, setMessage] = useState("正在自动识别地板…");

  const setBusyState = useCallback((value: boolean) => {
    setBusy(value);
    onBusyChange?.(value);
  }, [onBusyChange]);

  const redraw = useCallback(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const ctx = overlay.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    if (resultRef.current) {
      ctx.save();
      ctx.globalAlpha = 0.38;
      ctx.drawImage(resultRef.current, 0, 0);
      ctx.globalCompositeOperation = "source-in";
      ctx.fillStyle = "#22c55e";
      ctx.fillRect(0, 0, overlay.width, overlay.height);
      ctx.restore();
    }
    if (includeRef.current) {
      ctx.save();
      ctx.globalAlpha = 0.75;
      ctx.drawImage(includeRef.current, 0, 0);
      ctx.globalCompositeOperation = "source-in";
      ctx.fillStyle = "#16a34a";
      ctx.fillRect(0, 0, overlay.width, overlay.height);
      ctx.restore();
    }
    if (excludeRef.current) {
      ctx.save();
      ctx.globalAlpha = 0.8;
      ctx.drawImage(excludeRef.current, 0, 0);
      ctx.globalCompositeOperation = "source-in";
      ctx.fillStyle = "#ef4444";
      ctx.fillRect(0, 0, overlay.width, overlay.height);
      ctx.restore();
    }
  }, []);

  const loadMask = useCallback((base64: string) => new Promise<HTMLCanvasElement>((resolve, reject) => {
    const overlay = overlayRef.current;
    if (!overlay) return reject(new Error("画布尚未就绪"));
    const image = new Image();
    image.onload = () => {
      const canvas = blankCanvas(overlay.width, overlay.height);
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      // Backend masks are opaque grayscale PNGs. Canvas overlays need the
      // grayscale value in alpha, otherwise black background would also look
      // selected. Keep RGB black outside so re-exporting still decodes as L.
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
      for (let i = 0; i < pixels.data.length; i += 4) {
        const value = pixels.data[i];
        pixels.data[i] = value ? 255 : 0;
        pixels.data[i + 1] = value ? 255 : 0;
        pixels.data[i + 2] = value ? 255 : 0;
        pixels.data[i + 3] = value;
      }
      ctx.putImageData(pixels, 0, 0);
      resolve(canvas);
    };
    image.onerror = () => reject(new Error("蒙版读取失败"));
    image.src = `data:image/png;base64,${base64}`;
  }), []);

  const segment = useCallback(async (autoSeed: boolean) => {
    const overlay = overlayRef.current;
    if (!overlay || busy) return;
    const seq = ++requestSeq.current;
    setBusyState(true);
    setMessage(autoSeed ? "正在自动识别地板…" : "正在按笔触细化边缘…");
    try {
      const response = await api.colorMatchSegment({
        image_rel: imageRel,
        positive_mask_b64: exportMask(includeRef.current, hasIncludeRef.current),
        negative_mask_b64: exportMask(excludeRef.current, hasExcludeRef.current),
        previous_mask_b64: exportMask(resultRef.current, hasResultRef.current),
        auto_seed: autoSeed,
      });
      if (seq !== requestSeq.current) return;
      if (!response.mask_b64) {
        setMessage(response.warnings[0] || "未识别到地板，请用绿色笔涂几笔地板区域");
        return;
      }
      const result = await loadMask(response.mask_b64);
      if (seq !== requestSeq.current) return;
      resultRef.current = result;
      hasResultRef.current = true;
      redraw();
      onMaskChange(response.mask_b64, maskBounds(result));
      const confidence = response.confidence > 0 ? ` · 置信度 ${Math.round(response.confidence * 100)}%` : "";
      setMessage(response.warnings[0] || `绿色区域将被校色${confidence}`);
    } catch (error) {
      setMessage(`蒙版生成失败：${(error as Error).message}`);
    } finally {
      if (seq === requestSeq.current) setBusyState(false);
    }
  }, [busy, imageRel, loadMask, onMaskChange, redraw, setBusyState]);

  function initialize(width: number, height: number) {
    const scale = Math.min(1, 1600 / Math.max(width, height));
    const w = Math.max(1, Math.round(width * scale));
    const h = Math.max(1, Math.round(height * scale));
    const overlay = overlayRef.current;
    if (!overlay) return;
    overlay.width = w;
    overlay.height = h;
    includeRef.current = blankCanvas(w, h);
    excludeRef.current = blankCanvas(w, h);
    resultRef.current = blankCanvas(w, h);
    hasIncludeRef.current = false;
    hasExcludeRef.current = false;
    hasResultRef.current = false;
    snapshotsRef.current = [];
    setInitialized(true);
    setCanUndo(false);
    redraw();
    void segment(true);
  }

  function pointer(e: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = overlayRef.current;
    if (!canvas) return null;
    const bounds = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - bounds.left) * canvas.width / bounds.width,
      y: (e.clientY - bounds.top) * canvas.height / bounds.height,
    };
  }

  function paint(point: { x: number; y: number }) {
    const target = tool === "include" ? includeRef.current : excludeRef.current;
    const opposite = tool === "include" ? excludeRef.current : includeRef.current;
    if (!target || !opposite) return;
    if (tool === "include") hasIncludeRef.current = true;
    else hasExcludeRef.current = true;
    const radius = brush * target.width / Math.max(1, overlayRef.current?.getBoundingClientRect().width || target.width);
    const start = lastPointRef.current || point;
    for (const [canvas, mode] of [[target, "source-over"], [opposite, "destination-out"]] as const) {
      const ctx = canvas.getContext("2d")!;
      ctx.globalCompositeOperation = mode;
      ctx.strokeStyle = "white";
      ctx.fillStyle = "white";
      ctx.lineWidth = Math.max(2, radius);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      ctx.moveTo(start.x, start.y);
      ctx.lineTo(point.x, point.y);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(point.x, point.y, ctx.lineWidth / 2, 0, Math.PI * 2);
      ctx.fill();
    }
    lastPointRef.current = point;
    redraw();
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (busy) return;
    const point = pointer(e);
    if (!point) return;
    snapshotsRef.current.push({
      include: exportMask(includeRef.current, hasIncludeRef.current),
      exclude: exportMask(excludeRef.current, hasExcludeRef.current),
      result: exportMask(resultRef.current, hasResultRef.current),
    });
    if (snapshotsRef.current.length > 12) snapshotsRef.current.shift();
    setCanUndo(true);
    drawingRef.current = true;
    lastPointRef.current = point;
    e.currentTarget.setPointerCapture(e.pointerId);
    paint(point);
  }

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return;
    const point = pointer(e);
    if (point) paint(point);
  }

  function onPointerUp() {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    lastPointRef.current = null;
    void segment(false);
  }

  async function undo() {
    const snapshot = snapshotsRef.current.pop();
    if (!snapshot) return;
    setCanUndo(snapshotsRef.current.length > 0);
    const overlay = overlayRef.current;
    if (!overlay) return;
    const loadOrBlank = (value: string) => value
      ? loadMask(value)
      : Promise.resolve(blankCanvas(overlay.width, overlay.height));
    const [include, exclude, result] = await Promise.all([
      loadOrBlank(snapshot.include), loadOrBlank(snapshot.exclude), loadOrBlank(snapshot.result),
    ]);
    includeRef.current = include;
    excludeRef.current = exclude;
    resultRef.current = result;
    hasIncludeRef.current = Boolean(snapshot.include);
    hasExcludeRef.current = Boolean(snapshot.exclude);
    hasResultRef.current = Boolean(snapshot.result);
    redraw();
    const resultB64 = exportMask(result, hasResultRef.current);
    onMaskChange(resultB64, maskBounds(result));
    setMessage("已撤销上一次笔触");
  }

  function clear() {
    const overlay = overlayRef.current;
    if (!overlay) return;
    requestSeq.current++;
    includeRef.current = blankCanvas(overlay.width, overlay.height);
    excludeRef.current = blankCanvas(overlay.width, overlay.height);
    resultRef.current = blankCanvas(overlay.width, overlay.height);
    hasIncludeRef.current = false;
    hasExcludeRef.current = false;
    hasResultRef.current = false;
    snapshotsRef.current = [];
    setCanUndo(false);
    setBusyState(false);
    redraw();
    onMaskChange("", { x: 0, y: 0, w: 1, h: 1 });
    setMessage("蒙版已清空；绿色笔涂地板，红色笔排除墙面或家具");
  }

  useEffect(() => () => {
    requestSeq.current++;
    onBusyChange?.(false);
  }, [onBusyChange]);

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
