/* eslint-disable @next/next/no-img-element */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type {
  FloorPoint,
  FloorVisualizeRequest,
  FloorVisualizeTargetPayload,
  JobView,
} from "@/lib/types";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { CompareSlider } from "@/components/CompareSlider";
import { cn } from "@/lib/utils";

export type FloorVisualizeTarget =
  | { kind: "job"; jobId: string; stage: "b2" | "pro" | "sd35"; imageRel: string }
  | { kind: "record"; jsonPath: string; recordId: string; resultId: string }
  | { kind: "room"; roomPath: string };

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  srcUrl: string;
  textureUrl: string;
  texturePath: string;
  target: FloorVisualizeTarget;
  onDone?: (job?: JobView) => void;
  onRoomDone?: (path: string, url: string, thumb: string) => void;
};

const MAX_MASK_SIDE = 1600;
const DEFAULT_QUAD: FloorPoint[] = [
  { x: 0.08, y: 0.46 },
  { x: 0.92, y: 0.46 },
  { x: 0.99, y: 0.99 },
  { x: 0.01, y: 0.99 },
];

function targetPayload(target: FloorVisualizeTarget): FloorVisualizeTargetPayload {
  if (target.kind === "job") {
    return { kind: "job", jid: target.jobId, stage: target.stage, image_rel: target.imageRel };
  }
  if (target.kind === "record") {
    return {
      kind: "record",
      json_path: target.jsonPath,
      record_id: target.recordId,
      result_id: target.resultId,
    };
  }
  return { kind: "room", room_path: target.roomPath };
}

export function FloorVisualizeDialog(props: Props) {
  if (!props.open) return null;
  return <FloorVisualizeSession key={`${props.srcUrl}|${props.texturePath}`} {...props} />;
}

function FloorVisualizeSession({
  open,
  onOpenChange,
  srcUrl,
  textureUrl,
  texturePath,
  target,
  onDone,
  onRoomDone,
}: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const maskRef = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);
  const draggingPoint = useRef<number | null>(null);
  const last = useRef<{ x: number; y: number } | null>(null);
  const previewAbort = useRef<AbortController | null>(null);
  const previewSeq = useRef(0);

  const [tool, setTool] = useState<"mask" | "points">("mask");
  const [erase, setErase] = useState(false);
  const [brush, setBrush] = useState(42);
  const [quad, setQuad] = useState<FloorPoint[]>(DEFAULT_QUAD);
  const quadRef = useRef(quad);
  const [revision, setRevision] = useState(0);
  const [preview, setPreview] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);

  const [scale, setScale] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [offsetX, setOffsetX] = useState(0);
  const [offsetY, setOffsetY] = useState(0);
  const [illumination, setIllumination] = useState(0.65);
  const [shadow, setShadow] = useState(0.85);
  const [feather, setFeather] = useState(0.008);
  const [textureWidth, setTextureWidth] = useState("");
  const [textureHeight, setTextureHeight] = useState("");
  const [plankWidth, setPlankWidth] = useState("");
  const [plankLength, setPlankLength] = useState("");

  useEffect(() => {
    quadRef.current = quad;
  }, [quad]);

  const drawOverlay = useCallback(() => {
    const canvas = overlayRef.current;
    const mask = maskRef.current;
    if (!canvas || !mask || !canvas.width) return;
    const ctx = canvas.getContext("2d");
    const mctx = mask.getContext("2d");
    if (!ctx || !mctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const pixels = mctx.getImageData(0, 0, mask.width, mask.height).data;
    const tint = ctx.createImageData(canvas.width, canvas.height);
    for (let i = 0; i < pixels.length; i += 4) {
      tint.data[i] = 255;
      tint.data[i + 1] = 75;
      tint.data[i + 2] = 45;
      tint.data[i + 3] = pixels[i] > 127 ? 78 : 0;
    }
    ctx.putImageData(tint, 0, 0);
    const q = quadRef.current;
    ctx.strokeStyle = "#34d399";
    ctx.lineWidth = Math.max(2, canvas.width / 500);
    ctx.beginPath();
    q.forEach((p, i) => {
      const x = p.x * canvas.width;
      const y = p.y * canvas.height;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.stroke();
    q.forEach((p, i) => {
      const x = p.x * canvas.width;
      const y = p.y * canvas.height;
      ctx.beginPath();
      ctx.fillStyle = "#ffffff";
      ctx.arc(x, y, Math.max(6, canvas.width / 150), 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#059669";
      ctx.stroke();
      ctx.fillStyle = "#064e3b";
      ctx.font = `bold ${Math.max(10, canvas.width / 100)}px sans-serif`;
      ctx.fillText(String(i + 1), x + 9, y - 9);
    });
  }, []);

  const initializeCanvas = useCallback(
    (event: React.SyntheticEvent<HTMLImageElement>) => {
      const image = event.currentTarget;
      if (!image.naturalWidth) return;
      const factor = Math.min(1, MAX_MASK_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
      const width = Math.max(1, Math.round(image.naturalWidth * factor));
      const height = Math.max(1, Math.round(image.naturalHeight * factor));
      const overlay = overlayRef.current;
      if (!overlay) return;
      overlay.width = width;
      overlay.height = height;
      const mask = document.createElement("canvas");
      mask.width = width;
      mask.height = height;
      const ctx = mask.getContext("2d");
      if (!ctx) return;
      ctx.fillStyle = "white";
      ctx.beginPath();
      DEFAULT_QUAD.forEach((p, i) => {
        if (i === 0) ctx.moveTo(p.x * width, p.y * height);
        else ctx.lineTo(p.x * width, p.y * height);
      });
      ctx.closePath();
      ctx.fill();
      maskRef.current = mask;
      drawOverlay();
      setRevision((r) => r + 1);
    },
    [drawOverlay],
  );

  function canvasPoint(event: React.PointerEvent) {
    const canvas = overlayRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
  }

  function paint(point: { x: number; y: number }) {
    const mask = maskRef.current;
    const ctx = mask?.getContext("2d");
    if (!mask || !ctx) return;
    const box = boxRef.current?.getBoundingClientRect();
    const diameter = brush * (box?.width ? mask.width / box.width : 1);
    ctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
    ctx.strokeStyle = "white";
    ctx.lineWidth = Math.max(2, diameter);
    ctx.lineCap = "round";
    ctx.beginPath();
    const from = last.current || point;
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(point.x + (from.x === point.x ? 0.01 : 0), point.y);
    ctx.stroke();
    last.current = point;
    drawOverlay();
  }

  function pointerDown(event: React.PointerEvent) {
    const point = canvasPoint(event);
    const canvas = overlayRef.current;
    if (!point || !canvas) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === "points") {
      const threshold = Math.max(18, canvas.width / 30);
      let best = -1;
      let distance = Infinity;
      quadRef.current.forEach((p, i) => {
        const d = Math.hypot(point.x - p.x * canvas.width, point.y - p.y * canvas.height);
        if (d < distance && d <= threshold) {
          distance = d;
          best = i;
        }
      });
      draggingPoint.current = best >= 0 ? best : null;
      return;
    }
    drawing.current = true;
    last.current = null;
    paint(point);
  }

  function pointerMove(event: React.PointerEvent) {
    const point = canvasPoint(event);
    const canvas = overlayRef.current;
    if (!point || !canvas) return;
    if (tool === "points" && draggingPoint.current !== null) {
      const index = draggingPoint.current;
      setQuad((current) => {
        const next = current.map((p) => ({ ...p }));
        next[index] = {
          x: Math.max(0, Math.min(1, point.x / canvas.width)),
          y: Math.max(0, Math.min(1, point.y / canvas.height)),
        };
        quadRef.current = next;
        return next;
      });
      drawOverlay();
    } else if (drawing.current) {
      paint(point);
    }
  }

  function pointerUp() {
    const changed = drawing.current || draggingPoint.current !== null;
    drawing.current = false;
    draggingPoint.current = null;
    last.current = null;
    if (changed) setRevision((r) => r + 1);
  }

  function clearMask() {
    const mask = maskRef.current;
    const ctx = mask?.getContext("2d");
    if (!mask || !ctx) return;
    ctx.clearRect(0, 0, mask.width, mask.height);
    drawOverlay();
    setRevision((r) => r + 1);
  }

  function resetMask() {
    const mask = maskRef.current;
    const ctx = mask?.getContext("2d");
    if (!mask || !ctx) return;
    setQuad(DEFAULT_QUAD);
    quadRef.current = DEFAULT_QUAD;
    ctx.clearRect(0, 0, mask.width, mask.height);
    ctx.fillStyle = "white";
    ctx.beginPath();
    DEFAULT_QUAD.forEach((p, i) => {
      if (i === 0) ctx.moveTo(p.x * mask.width, p.y * mask.height);
      else ctx.lineTo(p.x * mask.width, p.y * mask.height);
    });
    ctx.closePath();
    ctx.fill();
    drawOverlay();
    setRevision((r) => r + 1);
  }

  function exportMask() {
    const mask = maskRef.current;
    if (!mask) return "";
    return mask.toDataURL("image/png").split(",", 2)[1] || "";
  }

  const requestPayload = useCallback((): FloorVisualizeRequest | null => {
    const mask = exportMask();
    if (!mask) return null;
    const optionalNumber = (value: string) => {
      const number = Number(value);
      return value.trim() && Number.isFinite(number) && number > 0 ? number : undefined;
    };
    return {
      target: targetPayload(target),
      texture_path: texturePath,
      mask_b64: mask,
      calibration_quad: quadRef.current,
      scale,
      rotation,
      offset_x: offsetX,
      offset_y: offsetY,
      illumination_strength: illumination,
      shadow_strength: shadow,
      feather,
      texture_width_mm: optionalNumber(textureWidth),
      texture_height_mm: optionalNumber(textureHeight),
      plank_width_mm: optionalNumber(plankWidth),
      plank_length_mm: optionalNumber(plankLength),
    };
  }, [target, texturePath, scale, rotation, offsetX, offsetY, illumination, shadow, feather,
    textureWidth, textureHeight, plankWidth, plankLength]);

  const runPreview = useCallback(async () => {
    const payload = requestPayload();
    if (!payload) {
      toast.error("遮罩尚未就绪");
      return;
    }
    previewAbort.current?.abort();
    const controller = new AbortController();
    previewAbort.current = controller;
    const seq = ++previewSeq.current;
    setPreviewing(true);
    try {
      const result = await api.previewFloorVisualize(payload, controller.signal);
      if (seq !== previewSeq.current) return;
      setPreview(result.preview);
      setWarnings(result.warnings || []);
    } catch (error) {
      if ((error as Error).name !== "AbortError") toast.error("预览失败：" + (error as Error).message);
    } finally {
      if (seq === previewSeq.current) setPreviewing(false);
    }
  }, [requestPayload]);

  const hasPreview = preview.length > 0;

  // 首次点击预览后，参数/遮罩改动自动刷新；避免初次打开就发大图请求。
  useEffect(() => {
    if (!hasPreview) return;
    const timer = window.setTimeout(() => void runPreview(), 550);
    return () => window.clearTimeout(timer);
  }, [hasPreview, revision, scale, rotation, offsetX, offsetY, illumination, shadow, feather, runPreview]);

  useEffect(() => () => previewAbort.current?.abort(), []);

  async function apply() {
    const payload = requestPayload();
    if (!payload) return;
    setApplying(true);
    try {
      const result = await api.applyFloorVisualize(payload);
      setWarnings(result.warnings || []);
      if (target.kind === "job") {
        toast.success("已保存为新候选，可用 ‹n/N› 切回原图");
        onDone?.(result.job);
      } else if (target.kind === "record") {
        toast.success("真实纹理结果已追加到记录");
        onDone?.();
      } else {
        onRoomDone?.(result.path || "", result.url || "", result.thumb || result.url || "");
        toast.success("已生成贴地板的房间图");
      }
      onOpenChange(false);
    } catch (error) {
      toast.error("保存失败：" + (error as Error).message);
    } finally {
      setApplying(false);
    }
  }

  const range = (label: string, value: number, set: (n: number) => void, min: number, max: number, step: number) => (
    <label className="block text-[11.5px] text-muted-foreground">
      <span className="mb-1 flex justify-between"><span>{label}</span><b className="text-foreground">{value}</b></span>
      <input className="w-full accent-[var(--primary)]" type="range" min={min} max={max} step={step}
        value={value} onChange={(event) => set(Number(event.target.value))} />
    </label>
  );
  const field = (label: string, value: string, set: (s: string) => void, placeholder: string) => (
    <label className="text-[11px] text-muted-foreground">{label}
      <input value={value} onChange={(event) => set(event.target.value)} placeholder={placeholder} inputMode="decimal"
        className="mt-1 h-8 w-full rounded-lg border border-border bg-panel px-2 text-[12px] text-foreground" />
    </label>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[96vh] max-w-[98vw] overflow-y-auto sm:max-w-[min(96vw,1380px)]">
        <div className="space-y-3">
          <div>
            <div className="text-[16px] font-bold">🪵 真实贴地板</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              红色为地面遮罩；绿色四边形按 1左上 → 2右上 → 3右下 → 4左下标定透视。本功能不调用 Gemini/SD。
            </div>
          </div>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div>
              {preview && <CompareSlider before={api.imgUrl(srcUrl)} after={preview} />}
              <div className={preview ? "hidden" : "block"}>
                <div ref={boxRef} className="relative overflow-hidden rounded-xl border border-border bg-black/5">
                  <img src={api.imgUrl(srcUrl)} onLoad={initializeCanvas} alt="待贴地板的房间"
                    className="block max-h-[68vh] w-full object-contain" draggable={false} />
                  <canvas ref={overlayRef} onPointerDown={pointerDown} onPointerMove={pointerMove}
                    onPointerUp={pointerUp} onPointerCancel={pointerUp}
                    className="absolute inset-0 h-full w-full touch-none cursor-crosshair" />
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <button onClick={() => { setTool("mask"); setPreview(""); }}
                  className={cn("h-8 rounded-lg px-3 text-[12px] font-semibold", tool === "mask" ? "bg-primary text-primary-foreground" : "border border-border bg-card")}>1. 画地面</button>
                <button onClick={() => { setTool("points"); setPreview(""); }}
                  className={cn("h-8 rounded-lg px-3 text-[12px] font-semibold", tool === "points" ? "bg-primary text-primary-foreground" : "border border-border bg-card")}>2. 拖四点</button>
                {tool === "mask" && <>
                  <button onClick={() => setErase(false)} className={cn("h-8 rounded-lg border px-3 text-[12px]", !erase && "border-primary text-primary")}>添加</button>
                  <button onClick={() => setErase(true)} className={cn("h-8 rounded-lg border px-3 text-[12px]", erase && "border-primary text-primary")}>擦除家具</button>
                  <label className="flex h-8 items-center gap-2 rounded-lg border border-border px-2 text-[11px]">画笔
                    <input type="range" min={10} max={120} value={brush} onChange={(e) => setBrush(Number(e.target.value))} />
                  </label>
                </>}
                <button onClick={clearMask} className="h-8 rounded-lg border border-border px-3 text-[12px]">清空遮罩</button>
                <button onClick={resetMask} className="h-8 rounded-lg border border-border px-3 text-[12px]">恢复默认</button>
                {preview && <button onClick={() => setPreview("")} className="h-8 rounded-lg border border-border px-3 text-[12px]">返回标定</button>}
              </div>
            </div>
            <div className="space-y-3 rounded-xl border border-border bg-panel p-3">
              <div className="flex items-center gap-2">
                <img src={api.imgUrl(textureUrl)} alt="地板小样" className="h-16 w-20 rounded-lg border border-border object-cover" />
                <div><div className="text-[12px] font-bold">原始地板小样</div><div className="text-[10.5px] text-muted-foreground">像素会直接投影，不会被模型重画</div></div>
              </div>
              {range("纹理比例", scale, setScale, 0.2, 3, 0.05)}
              {range("旋转角度", rotation, setRotation, -90, 90, 1)}
              {range("水平偏移", offsetX, setOffsetX, -1, 1, 0.02)}
              {range("垂直偏移", offsetY, setOffsetY, -1, 1, 0.02)}
              {range("亮部跟随", illumination, setIllumination, 0, 1.5, 0.05)}
              {range("阴影跟随", shadow, setShadow, 0, 1.5, 0.05)}
              {range("内侧羽化", feather, setFeather, 0, 0.04, 0.001)}
              <details>
                <summary className="cursor-pointer text-[11.5px] font-semibold">物理尺寸（可选，mm）</summary>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {field("整张宽", textureWidth, setTextureWidth, "如 1800")}
                  {field("整张高", textureHeight, setTextureHeight, "如 2400")}
                  {field("单片宽", plankWidth, setPlankWidth, "如 190")}
                  {field("单片长", plankLength, setPlankLength, "如 1900")}
                </div>
              </details>
              {warnings.length > 0 && <div className="space-y-1 rounded-lg bg-warn-soft p-2 text-[11px] leading-relaxed text-warn">
                {warnings.map((warning) => <div key={warning}>⚠ {warning}</div>)}
              </div>}
              <div className="flex gap-2 pt-1">
                <button disabled={previewing || applying} onClick={() => void runPreview()}
                  className="h-9 flex-1 rounded-lg border border-border bg-card text-[12px] font-bold disabled:opacity-50">
                  {previewing ? "预览渲染中…" : "生成预览"}
                </button>
                <button disabled={applying || previewing} onClick={() => void apply()}
                  className="h-9 flex-1 rounded-lg bg-primary text-[12px] font-bold text-primary-foreground disabled:opacity-50">
                  {applying ? "4K 保存中…" : "使用这个结果"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
