"use client";
import { useEffect, useRef } from "react";


import type { SmartMaskCandidate } from "@/lib/types";





import { canvasDataUrl, clearCanvas, forEachMaskRun, makeCanvas } from "@/lib/inpaint/mask";
import type { MaskLayers, MaskMode, MaskSnapshot } from "@/lib/inpaint/mask";
import { useMemo } from "react";
import { useAsyncScope } from "@/lib/editor/async-scope";
import { exportBinaryMask } from "@/lib/editor/canvas";
import type { InpaintSessionState } from "./useInpaintState";
const MASK_MAX_SIDE = 2048, UNDO_LIMIT = 20;
export function useInpaintMask(session: InpaintSessionState) {
  const { store, actions } = session;
  const { setHasMask, setCanUndo, setSelectedCandidateIds } = actions;
  const scope = useAsyncScope();
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const candidateCanvasRef = useRef<HTMLCanvasElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const drawing = useRef(false);
  const lastPt = useRef<{ x: number; y: number } | null>(null);
  const layersRef = useRef<Record<MaskMode, MaskLayers> | null>(null);
  const undoStacksRef = useRef<Record<MaskMode, MaskSnapshot[]>>({ remove: [], add: [] });
  const ownerMapRef = useRef<Int16Array | null>(null);
  const scanSizeRef = useRef({ width: 0, height: 0 });
  const composeFrameRef = useRef<number | null>(null);
  const modeRef = useMemo(() => store.field("mode"), [store]);
  const toolRef = useMemo(() => store.field("tool"), [store]);
  const brushRef = useMemo(() => store.field("brush"), [store]);
  const scanCandidatesRef = useMemo(() => store.field("scanCandidates"), [store]);
  const selectedIdsRef = useMemo(() => ({ get current() { return new Set(store.getSnapshot().selectedCandidateIds) } }), [store]);
  function activeLayers(which: MaskMode = modeRef.current) {
    return layersRef.current?.[which] || null;
  }

  function setSelectedIds(ids: Set<string>) {
    setSelectedCandidateIds(Array.from(ids));
  }

  function recompose(which: MaskMode = modeRef.current, updateMaskState = true) {
    if (which !== modeRef.current) return;
    const canvas = canvasRef.current;
    const layers = activeLayers(which);
    const ctx = canvas?.getContext("2d", { willReadFrequently: true });
    if (!canvas || !ctx || !layers) return;
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(layers.smart, 0, 0);
    ctx.drawImage(layers.include, 0, 0);
    ctx.globalCompositeOperation = "destination-out";
    ctx.drawImage(layers.exclude, 0, 0);
    ctx.globalCompositeOperation = "source-in";
    ctx.fillStyle = "rgba(255,60,60,0.92)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.globalCompositeOperation = "source-over";
    if (updateMaskState) setHasMask(maskNotEmpty());
  }

  function scheduleRecompose() {
    if (composeFrameRef.current !== null) return;
    const which = modeRef.current;
    composeFrameRef.current = scope.frame(() => {
      composeFrameRef.current = null;
      recompose(which, false);
    });
  }

  function pushUndo(which: MaskMode = modeRef.current) {
    const layers = activeLayers(which);
    if (!layers) return;
    const stack = undoStacksRef.current[which];
    stack.push({
      smart: canvasDataUrl(layers.smart),
      include: canvasDataUrl(layers.include),
      exclude: canvasDataUrl(layers.exclude),
      selected: which === "remove" ? Array.from(selectedIdsRef.current) : [],
    });
    if (stack.length > UNDO_LIMIT) stack.shift();
    if (which === modeRef.current) setCanUndo(true);
  }

  async function loadCanvas(canvas: HTMLCanvasElement, value: string) {
    const image = await scope.image(value); if (!image) return; clearCanvas(canvas); canvas.getContext("2d")?.drawImage(image, 0, 0, canvas.width, canvas.height);
  }

  function drawRleMask(targetCanvas: HTMLCanvasElement, candidate: SmartMaskCandidate,
    width: number, height: number) {
    if (!width || !height) return;
    const source = makeCanvas(width, height);
    const ctx = source.getContext("2d");
    if (!ctx) return;
    const pixels = ctx.createImageData(width, height);
    forEachMaskRun(candidate, (start, end) => {
      for (let index = start;index < end;index++) {
        const p = index * 4;
        pixels.data[p] = pixels.data[p + 1] = pixels.data[p + 2] = 255;
        pixels.data[p + 3] = 255;
      }
    });
    ctx.putImageData(pixels, 0, 0);
    const target = targetCanvas.getContext("2d");
    if (!target) return;
    target.imageSmoothingEnabled = false;
    target.drawImage(source, 0, 0, targetCanvas.width, targetCanvas.height);
  }

  function rebuildRemoveSmartLayer() {
    const layers = activeLayers("remove");
    if (!layers) return;
    clearCanvas(layers.smart);
    const { width, height } = scanSizeRef.current;
    for (const candidate of scanCandidatesRef.current) {
      if (selectedIdsRef.current.has(candidate.id)) {
        drawRleMask(layers.smart, candidate, width, height);
      }
    }
    recompose("remove");
  }

  function drawCandidateOverlay() {
    const canvas = candidateCanvasRef.current;
    const candidates = scanCandidatesRef.current;
    const { width, height } = scanSizeRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (modeRef.current !== "remove" || !width || !height || !candidates.length) {
      ownerMapRef.current = null;
      return;
    }
    const owner = new Int16Array(width * height);
    owner.fill(-1);
    const ordered = candidates.map((candidate, index) => ({ candidate, index }))
      .sort((a, b) => b.candidate.area - a.candidate.area);
    for (const { candidate, index } of ordered) {
      forEachMaskRun(candidate, (start, end) => owner.fill(index, start, end));
    }
    ownerMapRef.current = owner;
    const preview = makeCanvas(width, height);
    const previewCtx = preview.getContext("2d");
    if (!previewCtx) return;
    const pixels = previewCtx.createImageData(width, height);
    const selectedIds = selectedIdsRef.current;
    for (let index = 0;index < owner.length;index++) {
      const candidateIndex = owner[index];
      if (candidateIndex < 0) continue;
      const x = index % width;
      const y = Math.floor(index / width);
      const edge = x === 0 || y === 0 || x === width - 1 || y === height - 1 ||
        owner[index - 1] !== candidateIndex || owner[index + 1] !== candidateIndex ||
        owner[index - width] !== candidateIndex || owner[index + width] !== candidateIndex;
      const selected = selectedIds.has(candidates[candidateIndex].id);
      const p = index * 4;
      pixels.data[p] = selected ? 255 : 20;
      pixels.data[p + 1] = selected ? 70 : 210;
      pixels.data[p + 2] = selected ? 70 : 255;
      pixels.data[p + 3] = edge ? 220 : selected ? 45 : 12;
    }
    previewCtx.putImageData(pixels, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(preview, 0, 0, canvas.width, canvas.height);
  }

  function onImgLoad(e: React.SyntheticEvent<HTMLImageElement>) {
    const img = e.currentTarget;
    const canvas = canvasRef.current;
    if (!canvas || !img.naturalWidth) return;
    const scale = Math.min(1, MASK_MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    if (candidateCanvasRef.current) {
      candidateCanvasRef.current.width = canvas.width;
      candidateCanvasRef.current.height = canvas.height;
    }
    const existing = layersRef.current;
    if (!existing || existing.remove.smart.width !== canvas.width || existing.remove.smart.height !== canvas.height) {
      layersRef.current = {
        remove: {
          smart: makeCanvas(canvas.width, canvas.height),
          include: makeCanvas(canvas.width, canvas.height),
          exclude: makeCanvas(canvas.width, canvas.height),
        },
        add: {
          smart: makeCanvas(canvas.width, canvas.height),
          include: makeCanvas(canvas.width, canvas.height),
          exclude: makeCanvas(canvas.width, canvas.height),
        },
      };
    }
    recompose();
    drawCandidateOverlay();

  }

  function toCanvas(e: React.PointerEvent): { x: number; y: number; ratio: number } | null {
    const el = boxRef.current;
    const canvas = canvasRef.current;
    if (!el || !canvas || !canvas.width) return null;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return null;
    const ratio = canvas.width / r.width;
    return { x: (e.clientX - r.left) * ratio, y: (e.clientY - r.top) * ratio, ratio };
  }

  function strokeTo(p: { x: number; y: number; ratio: number }) {
    const layers = activeLayers();
    if (!layers || toolRef.current === "smart") return;
    const primary = toolRef.current === "erase" ? layers.exclude : layers.include;
    const opposite = toolRef.current === "erase" ? layers.include : layers.exclude;
    const from = lastPt.current ?? { x: p.x, y: p.y };
    for (const [layer, operation] of [[primary, "source-over"], [opposite, "destination-out"]] as const) {
      const ctx = layer.getContext("2d");
      if (!ctx) continue;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.lineWidth = Math.max(2, brushRef.current * p.ratio);
      ctx.globalCompositeOperation = operation;
      ctx.strokeStyle = "white";
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(p.x + (from.x === p.x ? 0.01 : 0), p.y);
      ctx.stroke();
    }
    lastPt.current = { x: p.x, y: p.y };
    scheduleRecompose();
  }

  function onDown(e: React.PointerEvent, onSmart: (p: { x: number; y: number }) => void) {
    const p = toCanvas(e);
    if (!p || !canvasRef.current) return;
    if (toolRef.current === "smart") {
      onSmart(p);
      return;
    }
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    scope.invalidate();
    pushUndo();
    drawing.current = true;
    lastPt.current = null;
    strokeTo(p);
  }

  function onMove(e: React.PointerEvent) {
    const cursor = cursorRef.current;
    const el = boxRef.current;
    if (cursor && el && toolRef.current !== "smart") {
      const r = el.getBoundingClientRect();
      cursor.style.left = `${e.clientX - r.left}px`;
      cursor.style.top = `${e.clientY - r.top}px`;
      cursor.style.display = "block";
    } else if (cursor) cursor.style.display = "none";
    if (!drawing.current) return;
    const p = toCanvas(e);
    if (p) strokeTo(p);
  }

  function onUp() {
    if (!drawing.current) return;
    drawing.current = false;
    lastPt.current = null;
    if (composeFrameRef.current !== null) {
      cancelAnimationFrame(composeFrameRef.current);
      composeFrameRef.current = null;
    }
    recompose();
  }

  function maskNotEmpty(): boolean {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !canvas.width) return false;
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 3;i < data.length;i += 4) if (data[i] > 0) return true;
    return false;
  }

  function undo() {
    scope.invalidate(); const token = scope.token();
    const which = modeRef.current;
    const stack = undoStacksRef.current[which];
    const snap = stack.pop();
    const layers = activeLayers(which);
    if (!snap || !layers) return;
    setCanUndo(stack.length > 0);
    void Promise.all([
      loadCanvas(layers.smart, snap.smart),
      loadCanvas(layers.include, snap.include),
      loadCanvas(layers.exclude, snap.exclude),
    ]).then(() => {
      if (!scope.valid(token)) return;
      if (which === "remove") setSelectedIds(new Set(snap.selected));
      recompose(which);
      drawCandidateOverlay();
    });
  }

  function clearMask() {
    scope.invalidate();
    const which = modeRef.current;
    const layers = activeLayers(which);
    if (!layers) return;
    pushUndo(which);
    clearCanvas(layers.smart);
    clearCanvas(layers.include);
    clearCanvas(layers.exclude);
    if (which === "remove") setSelectedIds(new Set());
    recompose(which);
    drawCandidateOverlay();
  }

  function exportMask() { return exportBinaryMask(canvasRef.current); }

  useEffect(() => () => { layersRef.current = null; ownerMapRef.current = null; undoStacksRef.current = { remove: [], add: [] }; }, []);
  return { refs: { boxRef, canvasRef, candidateCanvasRef, cursorRef, drawing, lastPt, layersRef, undoStacksRef, ownerMapRef, scanSizeRef, composeFrameRef, modeRef, toolRef, brushRef, scanCandidatesRef, selectedIdsRef }, actions: { activeLayers, setSelectedIds, recompose, scheduleRecompose, pushUndo, loadCanvas, drawRleMask, rebuildRemoveSmartLayer, drawCandidateOverlay, onImgLoad, toCanvas, strokeTo, onDown, onMove, onUp, maskNotEmpty, undo, clearMask, exportMask, frame: (callback: () => void) => scope.frame(callback), queueOverlay: () => scope.frame(drawCandidateOverlay), invalidate: () => scope.invalidate() } };
}
export type InpaintMask = ReturnType<typeof useInpaintMask>;
