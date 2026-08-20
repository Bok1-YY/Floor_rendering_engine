/* eslint-disable @next/next/no-img-element */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LoaderCircle, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import PanoViewer from "@/components/PanoViewer";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type {
  JobView,
  PanoramaFloorMaskView,
  PanoramaFloorPrepareResponse,
  PanoramaFloorRecordTarget,
  PanoramaFloorRenderRequest,
  SphericalFloorRecipe,
} from "@/lib/types";
import { cn } from "@/lib/utils";


type MaskTool = "add" | "erase";


function dataUrl(value: string) {
  return value.startsWith("data:") ? value : `data:image/png;base64,${value}`;
}


function stripDataUrl(value: string) {
  return value.includes(",") ? value.split(",", 2)[1] || "" : value;
}


function ViewMaskEditor({
  view,
  maskB64,
  tool,
  brush,
  onChange,
}: {
  view: PanoramaFloorMaskView;
  maskB64: string;
  tool: MaskTool;
  brush: number;
  onChange: (maskB64: string) => void;
}) {
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const maskRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const lastRef = useRef<{ x: number; y: number } | null>(null);

  const redraw = useCallback(() => {
    const overlay = overlayRef.current;
    const mask = maskRef.current;
    if (!overlay || !mask) return;
    const ctx = overlay.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    ctx.save();
    ctx.drawImage(mask, 0, 0);
    ctx.globalCompositeOperation = "source-in";
    ctx.fillStyle = "rgba(255,62,35,.42)";
    ctx.fillRect(0, 0, overlay.width, overlay.height);
    ctx.restore();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled) return;
      const width = view.width;
      const height = view.height;
      const source = document.createElement("canvas");
      source.width = width;
      source.height = height;
      const sourceCtx = source.getContext("2d", { willReadFrequently: true });
      const mask = document.createElement("canvas");
      mask.width = width;
      mask.height = height;
      const maskCtx = mask.getContext("2d");
      const overlay = overlayRef.current;
      if (!sourceCtx || !maskCtx || !overlay) return;
      sourceCtx.drawImage(image, 0, 0, width, height);
      const pixels = sourceCtx.getImageData(0, 0, width, height).data;
      const binary = maskCtx.createImageData(width, height);
      for (let index = 0; index < pixels.length; index += 4) {
        const selected = pixels[index] >= 128;
        binary.data[index] = 255;
        binary.data[index + 1] = 255;
        binary.data[index + 2] = 255;
        binary.data[index + 3] = selected ? 255 : 0;
      }
      maskCtx.putImageData(binary, 0, 0);
      maskRef.current = mask;
      overlay.width = width;
      overlay.height = height;
      redraw();
    };
    image.src = dataUrl(maskB64);
    return () => {
      cancelled = true;
    };
  }, [maskB64, redraw, view.height, view.width]);

  function point(event: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = overlayRef.current;
    if (!canvas) return null;
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return null;
    return {
      x: (event.clientX - bounds.left) * canvas.width / bounds.width,
      y: (event.clientY - bounds.top) * canvas.height / bounds.height,
      scale: canvas.width / bounds.width,
    };
  }

  function paint(event: React.PointerEvent<HTMLCanvasElement>) {
    const next = point(event);
    const mask = maskRef.current;
    const ctx = mask?.getContext("2d");
    if (!next || !mask || !ctx) return;
    const previous = lastRef.current || next;
    ctx.save();
    ctx.globalCompositeOperation = tool === "erase" ? "destination-out" : "source-over";
    ctx.strokeStyle = "white";
    ctx.lineWidth = Math.max(2, brush * next.scale);
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(previous.x, previous.y);
    ctx.lineTo(next.x + (next.x === previous.x ? 0.01 : 0), next.y);
    ctx.stroke();
    ctx.restore();
    lastRef.current = next;
    redraw();
  }

  function exportMask() {
    const mask = maskRef.current;
    if (!mask) return;
    const output = document.createElement("canvas");
    output.width = mask.width;
    output.height = mask.height;
    const ctx = output.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "black";
    ctx.fillRect(0, 0, output.width, output.height);
    ctx.drawImage(mask, 0, 0);
    onChange(stripDataUrl(output.toDataURL("image/png")));
  }

  return (
    <div className="relative overflow-hidden rounded-xl border border-border bg-black/5">
      <img src={dataUrl(view.image_b64)} alt={`${view.label}地板遮罩`}
        className="block max-h-[57vh] w-full object-contain" draggable={false} />
      <canvas ref={overlayRef}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          drawingRef.current = true;
          lastRef.current = null;
          paint(event);
        }}
        onPointerMove={(event) => drawingRef.current && paint(event)}
        onPointerUp={() => {
          drawingRef.current = false;
          lastRef.current = null;
          exportMask();
        }}
        onPointerCancel={() => {
          drawingRef.current = false;
          lastRef.current = null;
          exportMask();
        }}
        className="absolute inset-0 h-full w-full touch-none cursor-crosshair" />
    </div>
  );
}


function recipePayload(value: SphericalFloorRecipe): SphericalFloorRecipe {
  return {
    ...value,
    plank_width_mm: value.plank_width_mm || null,
    plank_length_mm: value.plank_length_mm || null,
  };
}


export default function PanoramaFloorDialog({
  open,
  onOpenChange,
  jobId,
  recordTarget,
  panoramaIndex,
  erpUrl,
  textureUrl,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId?: string;
  recordTarget?: PanoramaFloorRecordTarget;
  panoramaIndex: number;
  erpUrl: string;
  textureUrl: string;
  onDone: (job?: JobView, candidateIndex?: number) => void;
}) {
  const [prepared, setPrepared] = useState<PanoramaFloorPrepareResponse | null>(null);
  const [viewMasks, setViewMasks] = useState<Record<string, string>>({});
  const [activeView, setActiveView] = useState("front");
  const [recipe, setRecipe] = useState<SphericalFloorRecipe | null>(null);
  const [preview, setPreview] = useState("");
  const [showOriginal, setShowOriginal] = useState(false);
  const [tool, setTool] = useState<MaskTool>("erase");
  const [brush, setBrush] = useState(46);
  const [loading, setLoading] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);

  const makeRequest = useCallback((source: PanoramaFloorPrepareResponse,
    masks: Record<string, string>, values: SphericalFloorRecipe): PanoramaFloorRenderRequest => ({
    panorama_index: panoramaIndex,
    source_sha256: source.source_sha256,
    view_masks: source.views.map((view) => ({
      id: view.id,
      mask_b64: masks[view.id] || view.mask_b64,
    })),
    recipe: recipePayload(values),
  }), [panoramaIndex]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const prepareCall = jobId
      ? api.preparePanoramaFloor(jobId, panoramaIndex)
      : recordTarget
        ? api.prepareRecordPanoramaFloor(recordTarget)
        : Promise.reject(new Error("缺少球面地板目标"));
    prepareCall
      .then(async (result) => {
        if (cancelled) return;
        const masks = Object.fromEntries(result.views.map((view) => [view.id, view.mask_b64]));
        setPrepared(result);
        setViewMasks(masks);
        setRecipe(result.defaults);
        setWarnings(result.warnings || []);
        setLoading(false);
        setPreviewing(true);
        try {
          const request = makeRequest(result, masks, result.defaults);
          const first = jobId
            ? await api.previewPanoramaFloor(jobId, request)
            : await api.previewRecordPanoramaFloor({
              ...(recordTarget as PanoramaFloorRecordTarget),
              source_sha256: request.source_sha256,
              view_masks: request.view_masks,
              recipe: request.recipe,
            });
          if (!cancelled) {
            setPreview(first.preview);
            setWarnings((current) => [...current, ...(first.warnings || [])]);
          }
        } catch (reason) {
          if (!cancelled) toast.error((reason as Error).message);
        } finally {
          if (!cancelled) setPreviewing(false);
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setLoading(false);
          toast.error((reason as Error).message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, makeRequest, open, panoramaIndex, recordTarget]);

  async function runPreview() {
    if (!prepared || !recipe || previewing) return;
    setPreviewing(true);
    try {
      const request = makeRequest(prepared, viewMasks, recipe);
      const result = jobId
        ? await api.previewPanoramaFloor(jobId, request)
        : await api.previewRecordPanoramaFloor({
          ...(recordTarget as PanoramaFloorRecordTarget),
          source_sha256: request.source_sha256,
          view_masks: request.view_masks,
          recipe: request.recipe,
        });
      setPreview(result.preview);
      setShowOriginal(false);
      setWarnings((current) => [...prepared.warnings, ...current, ...(result.warnings || [])]);
    } catch (reason) {
      toast.error((reason as Error).message);
    } finally {
      setPreviewing(false);
    }
  }

  async function apply() {
    if (!prepared || !recipe || applying) return;
    setApplying(true);
    try {
      const request = makeRequest(prepared, viewMasks, recipe);
      const result = jobId
        ? await api.applyPanoramaFloor(jobId, request)
        : await api.applyRecordPanoramaFloor({
          ...(recordTarget as PanoramaFloorRecordTarget),
          source_sha256: request.source_sha256,
          view_masks: request.view_masks,
          recipe: request.recipe,
        });
      toast.success("球面地板校正已保存为新的 360° 候选，原始全景仍可回退");
      if ("job" in result) onDone(result.job, result.candidate_index);
      else onDone();
      onOpenChange(false);
    } catch (reason) {
      toast.error((reason as Error).message);
    } finally {
      setApplying(false);
    }
  }

  const currentView = prepared?.views.find((view) => view.id === activeView) || prepared?.views[0];
  const setNumber = (key: keyof SphericalFloorRecipe, value: number) => {
    setRecipe((current) => current ? { ...current, [key]: value } : current);
  };
  const slider = (label: string, key: keyof SphericalFloorRecipe,
    min: number, max: number, step: number, suffix = "") => recipe && (
    <label className="block text-[11px] text-muted-foreground">
      <span className="mb-1 flex justify-between"><span>{label}</span>
        <b className="text-foreground">{Number(recipe[key] || 0).toFixed(step < 0.01 ? 3 : step < 1 ? 2 : 0)}{suffix}</b>
      </span>
      <input type="range" min={min} max={max} step={step} value={Number(recipe[key] || 0)}
        onChange={(event) => setNumber(key, Number(event.target.value))}
        className="w-full accent-[var(--primary)]" />
    </label>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[97vh] max-w-[98vw] overflow-y-auto sm:max-w-[min(97vw,1480px)]">
        <div className="space-y-3">
          <div className="pr-8">
            <div className="text-[16px] font-bold">本地几何与地板校准 · 统一 360° 铺装坐标</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              红色区域会被替换。MobileSAM 与本地深度只识别边界，彩膜按世界坐标投射到同一水平地板平面，全程不调用生图 API。
            </div>
          </div>

          {loading || !prepared || !recipe ? (
            <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="animate-dc-spin" />正在生成前、右、后、左、脚下五个本地遮罩…
            </div>
          ) : (
            <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_330px]">
              <div className="space-y-2">
                <div className="flex flex-wrap gap-1.5">
                  {prepared.views.map((view) => (
                    <button key={view.id} onClick={() => setActiveView(view.id)}
                      className={cn("h-8 rounded-lg border px-3 text-xs font-semibold",
                        activeView === view.id ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card")}>
                      {view.label} · {Math.round(view.confidence * 100)}%
                    </button>
                  ))}
                </div>
                {currentView && (
                  <ViewMaskEditor key={currentView.id} view={currentView}
                    maskB64={viewMasks[currentView.id] || currentView.mask_b64}
                    tool={tool} brush={brush}
                    onChange={(value) => setViewMasks((current) => ({ ...current, [currentView.id]: value }))} />
                )}
                <div className="flex flex-wrap items-center gap-1.5">
                  <button onClick={() => setTool("erase")}
                    className={cn("h-8 rounded-lg border px-3 text-xs", tool === "erase" && "border-primary text-primary")}>擦除家具/墙体</button>
                  <button onClick={() => setTool("add")}
                    className={cn("h-8 rounded-lg border px-3 text-xs", tool === "add" && "border-primary text-primary")}>补画地板</button>
                  <label className="flex h-8 items-center gap-2 rounded-lg border border-border px-2 text-xs">
                    画笔 <input type="range" min={10} max={120} value={brush}
                      onChange={(event) => setBrush(Number(event.target.value))} />
                  </label>
                  <button onClick={() => {
                    setViewMasks(Object.fromEntries(prepared.views.map((view) => [view.id, view.mask_b64])));
                    setPreview("");
                  }} className="inline-flex h-8 items-center gap-1 rounded-lg border border-border px-3 text-xs">
                    <RotateCcw size={13} />恢复自动遮罩
                  </button>
                </div>
              </div>

              <div className="space-y-3 rounded-xl border border-border bg-panel p-3">
                <div className="flex items-center gap-2">
                  <img src={api.imgUrl(textureUrl)} alt="地板小样" className="h-16 w-24 rounded-lg border border-border object-cover" />
                  <div><div className="text-xs font-bold">原始地板小样</div>
                    <div className="text-[10.5px] text-muted-foreground">本地像素投影，不由生成模型重画</div></div>
                </div>
                {slider("铺装方向", "rotation_deg", -180, 180, 1, "°")}
                {slider("纹理比例", "scale", 0.15, 4, 0.05)}
                {slider("横向偏移", "offset_x", -1, 1, 0.01)}
                {slider("纵向偏移", "offset_z", -1, 1, 0.01)}
                {slider("亮部跟随", "illumination_strength", 0, 1.5, 0.05)}
                {slider("阴影跟随", "shadow_strength", 0, 1.5, 0.05)}
                {slider("接触阴影", "contact_shadow_strength", 0, 1.5, 0.05)}
                {slider("内侧羽化", "feather", 0, 0.04, 0.001)}
                <details>
                  <summary className="cursor-pointer text-xs font-semibold">物理尺寸与相机</summary>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground">
                    {([[
                      "整图宽 mm", "texture_width_mm"], ["整图高 mm", "texture_height_mm"],
                      ["单板宽 mm", "plank_width_mm"], ["单板长 mm", "plank_length_mm"],
                      ["相机高度 m", "camera_height_m"],
                    ] as Array<[string, keyof SphericalFloorRecipe]>).map(([label, key]) => (
                      <label key={key}>{label}<input type="number" value={recipe[key] ?? ""}
                        onChange={(event) => setNumber(key, Number(event.target.value))}
                        className="mt-1 h-8 w-full rounded-lg border border-border bg-card px-2 text-foreground" /></label>
                    ))}
                  </div>
                </details>
                {prepared.parent_gate_status === "repair_recommended" && (
                  <div className="rounded-lg bg-warn-soft p-2 text-[11px] text-warn">
                    当前原始全景仍有结构或立方体边界警告；建议先完成付费结构修复，再重新校正地板。
                  </div>
                )}
                <div className="flex gap-2">
                  <Button variant="outline" className="flex-1" disabled={previewing || applying}
                    onClick={() => void runPreview()}>
                    {previewing ? <><LoaderCircle className="animate-dc-spin" />预览中</> : "刷新球面预览"}
                  </Button>
                  <Button className="flex-1" disabled={previewing || applying}
                    onClick={() => void apply()}>
                    {applying ? <><LoaderCircle className="animate-dc-spin" />4K 保存中</> : "使用这个结果"}
                  </Button>
                </div>
              </div>
            </div>
          )}

          {preview && (
            <div className="space-y-2 rounded-xl border border-border p-2">
              <div className="flex items-center justify-between gap-2 px-1 text-xs">
                <b>{showOriginal ? "原始 AI 全景" : "球面地板校正预览"}</b>
                <Button size="sm" variant="outline" onClick={() => setShowOriginal((value) => !value)}>
                  {showOriginal ? "查看校正后" : "对比原始图"}
                </Button>
              </div>
              <PanoViewer erpUrl={showOriginal ? api.imgUrl(erpUrl) : preview}
                mode="view" initialYawDeg={90} />
            </div>
          )}

          {warnings.length > 0 && (
            <div className="space-y-1 rounded-xl bg-warn-soft p-3 text-[11px] leading-relaxed text-warn">
              {[...new Set(warnings)].map((warning) => <div key={warning}>⚠ {warning}</div>)}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
