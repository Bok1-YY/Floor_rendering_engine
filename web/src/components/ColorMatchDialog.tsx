/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  ColorMatchAdjustments,
  ColorMatchAnalysis,
  ColorMatchHintCode,
  ColorMatchRect,
  JobView,
  ModelKey,
} from "@/lib/types";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Slider } from "@/components/ui/slider";
import { ImageZoom } from "@/components/ImageZoom";
import { ColorMaskEditor } from "@/components/ColorMaskEditor";

/** 校色目标：任务候选（并入该 stage 候选列表）或记录结果（append 回记录）。 */
export type ColorMatchTarget =
  | { kind: "job"; jobId: string; stage: ModelKey }
  | { kind: "record"; jsonPath: string; recordId: string; resultId: string };

const DEFAULT_RECT: ColorMatchRect = { x: 0.05, y: 0.45, w: 0.9, h: 0.5 };
const DEFAULT_ADJUSTMENTS: ColorMatchAdjustments = {
  temperature: 0,
  tint: 0,
  exposure: 0,
  contrast: 0,
  highlights: 0,
  shadows: 0,
  whites: 0,
  blacks: 0,
  midtones: 0,
  saturation: 0,
};

type AdjustmentKey = keyof ColorMatchAdjustments;
type AdjustmentMode = "auto" | "manual";
type ColorScope = "floor_mask" | "global";
type AdjustmentControl = {
  key: AdjustmentKey;
  label: string;
  min: number;
  max: number;
  step: number;
  hint: string;
};

const ADJUSTMENT_CONTROLS: AdjustmentControl[] = [
  { key: "temperature", label: "色温", min: -100, max: 100, step: 1, hint: "冷色 ↔ 暖色" },
  { key: "tint", label: "色调", min: -100, max: 100, step: 1, hint: "绿色 ↔ 洋红" },
  { key: "exposure", label: "曝光", min: -2, max: 2, step: 0.1, hint: "曝光补偿（EV）" },
  { key: "contrast", label: "对比度", min: -100, max: 100, step: 1, hint: "柔和 ↔ 强烈" },
  { key: "highlights", label: "高光", min: -100, max: 100, step: 1, hint: "调整较亮区域" },
  { key: "shadows", label: "阴影", min: -100, max: 100, step: 1, hint: "调整较暗区域" },
  { key: "whites", label: "白色色阶", min: -100, max: 100, step: 1, hint: "调整最亮区域" },
  { key: "blacks", label: "黑色色阶", min: -100, max: 100, step: 1, hint: "调整最暗区域" },
  { key: "midtones", label: "中间调", min: -100, max: 100, step: 1, hint: "调整灰色与中间亮度" },
  { key: "saturation", label: "饱和度", min: -100, max: 100, step: 1, hint: "灰度 ↔ 鲜艳" },
];

const HINT_STYLES: Record<ColorMatchHintCode, string> = {
  warm: "border-orange-200 bg-orange-50 text-orange-800 dark:border-orange-900 dark:bg-orange-950/40 dark:text-orange-200",
  cool: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200",
  green: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
  magenta: "border-pink-200 bg-pink-50 text-pink-800 dark:border-pink-900 dark:bg-pink-950/40 dark:text-pink-200",
  gray: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200",
  saturated: "border-violet-200 bg-violet-50 text-violet-800 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-200",
  matched: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
  unavailable: "border-border bg-panel text-muted-foreground",
};

function scaleAutoAdjustments(
  profile: ColorMatchAdjustments,
  strength: number,
): ColorMatchAdjustments {
  return Object.fromEntries(
    ADJUSTMENT_CONTROLS.map((control) => {
      const scaled = profile[control.key] * strength;
      const value = control.key === "exposure"
        ? Math.round(scaled * 10) / 10
        : Math.round(scaled);
      return [control.key, Math.min(control.max, Math.max(control.min, value))];
    }),
  ) as unknown as ColorMatchAdjustments;
}

type ColorMatchDialogProps = {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  srcUrl: string;
  imageRel: string;
  refUrl: string;
  refPath: string;
  target: ColorMatchTarget;
  onDone?: (jobView?: JobView) => void;
};

/**
 * 手动校色弹窗（大窗双栏 + 实时预览）：
 * 左栏原图上拖框住地板作为分析样本，右栏 canvas 实时显示全图校色结果。
 * 首帧以手动全零显示原图，同时返回受光/半阴影/阴影诊断；用户点击后才套用建议参数。
 * 实时原理：强度是线性混合（src·(1−m·s)+t·m·s ≡ 先按 s=1 出完整结果 F，再以 alpha=s 混合 src 与 F），
 * 故服务端只在分析框、参照或高级参数变化时按 strength=1.0 重算一次（防抖自动请求），
 * 强度滑杆纯客户端 canvas 混合、零网络即时生效；提交仍带真实 strength，结果与预览一致。
 */
export function ColorMatchDialog(props: ColorMatchDialogProps) {
  if (!props.open) return null;
  return <ColorMatchSession key={`${props.imageRel}:${props.refPath}`} {...props} />;
}

function ColorMatchSession({
  open,
  onOpenChange,
  srcUrl,
  imageRel,
  refUrl,
  refPath,
  target,
  onDone,
}: ColorMatchDialogProps) {
  const [rect, setRect] = useState<ColorMatchRect>(DEFAULT_RECT);
  const [strength, setStrength] = useState(0.7);
  const [scope, setScope] = useState<ColorScope>("floor_mask");
  const [maskB64, setMaskB64] = useState("");
  const [maskFeather, setMaskFeather] = useState(0.003);
  const [maskBusy, setMaskBusy] = useState(true);
  const [ref, setRef] = useState<{ url: string; path: string }>({ url: refUrl, path: refPath });
  const [adjustments, setAdjustments] = useState<ColorMatchAdjustments>(() => ({ ...DEFAULT_ADJUSTMENTS }));
  const [adjustmentMode, setAdjustmentMode] = useState<AdjustmentMode>("manual");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [previewing, setPreviewing] = useState(Boolean(refPath));
  const [analyzing, setAnalyzing] = useState(Boolean(refPath));
  const [analysis, setAnalysis] = useState<ColorMatchAnalysis | null>(null);
  const [hasPreview, setHasPreview] = useState(false);
  const [ready, setReady] = useState(false); // fullPreview 与当前 rect/ref 对应（可保存）
  const [saving, setSaving] = useState(false);
  const [zoom, setZoom] = useState<{
    url: string;
    baseUrl?: string;
    overlayOpacity?: number;
  } | null>(null);
  // 小样参照贴片：叠在右栏结果图上，紧挨结果方便比色；可收起、可点开全屏
  const [showRefPatch, setShowRefPatch] = useState(true);

  const box = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const rectRef = useRef<ColorMatchRect>(DEFAULT_RECT);
  // 实时管线：原图 / 服务端 strength=1.0 完整结果；请求序号防乱序；防抖计时器
  const srcImgRef = useRef<HTMLImageElement | null>(null);
  const fullPreviewRef = useRef<HTMLImageElement | null>(null);
  const autoPreviewRef = useRef<HTMLImageElement | null>(null);
  const autoAdjustmentsRef = useRef<ColorMatchAdjustments>({ ...DEFAULT_ADJUSTMENTS });
  const previewSeq = useRef(0);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortPreview = useRef<AbortController | null>(null);
  const strengthRef = useRef(0.7);
  const lastAutoStrengthRef = useRef(0.8);
  const adjustmentModeRef = useRef<AdjustmentMode>("manual");
  const scopeRef = useRef<ColorScope>("floor_mask");
  const maskRef = useRef("");
  const maskFeatherRef = useRef(0.003);

  // canvas 重绘：原图打底 + fullPreview 按当前强度 alpha 叠加（即时，无网络）
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const full = fullPreviewRef.current;
    const src = srcImgRef.current;
    if (!canvas || !full) return;
    if (canvas.width !== full.naturalWidth || canvas.height !== full.naturalHeight) {
      canvas.width = full.naturalWidth;
      canvas.height = full.naturalHeight;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.globalAlpha = 1;
    if (src && src.complete && src.naturalWidth > 0) {
      ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
      ctx.globalAlpha = adjustmentModeRef.current === "auto" ? strengthRef.current : 1;
    }
    ctx.drawImage(full, 0, 0, canvas.width, canvas.height);
    ctx.globalAlpha = 1;
  }, []);

  // 服务端全图预览（strength 固定 1.0；参数/分析框/参照变化时防抖调用）
  const requestPreview = useCallback(
    (
      r: ColorMatchRect,
      refPathNow: string,
      nextAdjustments: ColorMatchAdjustments,
      nextMode: AdjustmentMode,
      seq: number,
      includeAnalysis = false,
    ) => {
      if (!refPathNow || r.w < 0.02 || r.h < 0.02) return;
      if (scopeRef.current === "floor_mask" && !maskRef.current) return;
      const controller = new AbortController();
      abortPreview.current = controller;
      api
        .colorMatchPreview(
          {
            image_rel: imageRel,
            ref_path: refPathNow,
            rect: r,
            strength: 1.0,
            adjustments: nextAdjustments,
            adjustment_mode: nextMode,
            include_analysis: includeAnalysis,
            scope: scopeRef.current,
            mask_b64: maskRef.current,
            mask_feather: maskFeatherRef.current,
          },
          controller.signal,
        )
        .then((res) => {
          if (seq !== previewSeq.current) return; // 过期响应丢弃
          autoAdjustmentsRef.current = res.auto_adjustments;
          if (includeAnalysis) {
            setAnalysis(res.analysis ?? null);
            setAnalyzing(false);
          }
          if (nextMode === "auto") {
            setAdjustments(scaleAutoAdjustments(res.auto_adjustments, strengthRef.current));
          }
          const img = new Image();
          img.onload = () => {
            if (seq !== previewSeq.current) return;
            fullPreviewRef.current = img;
            if (nextMode === "auto") autoPreviewRef.current = img;
            adjustmentModeRef.current = nextMode;
            setAdjustmentMode(nextMode);
            setHasPreview(true);
            setReady(true);
            setPreviewing(false);
            redraw();
          };
          img.onerror = () => {
            if (seq !== previewSeq.current) return;
            setPreviewing(false);
            if (includeAnalysis) setAnalyzing(false);
            toast.error("预览图片读取失败");
          };
          img.src = res.preview;
        })
        .catch((e) => {
          if (seq !== previewSeq.current) return;
          if ((e as Error).name === "AbortError") return;
          setPreviewing(false);
          if (includeAnalysis) setAnalyzing(false);
          toast.error("预览失败：" + (e as Error).message);
        });
    },
    [imageRel, redraw],
  );

  const schedulePreview = useCallback(
    (
      r: ColorMatchRect,
      refPathNow: string,
      nextAdjustments: ColorMatchAdjustments,
      nextMode: AdjustmentMode,
      delay = 180,
      includeAnalysis = false,
    ) => {
      setReady(false);
      abortPreview.current?.abort();
      const seq = ++previewSeq.current;
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
      if (!refPathNow || r.w < 0.02 || r.h < 0.02) {
        setPreviewing(false);
        if (includeAnalysis) setAnalyzing(false);
        return;
      }
      setPreviewing(true);
      if (includeAnalysis) setAnalyzing(true);
      debounceTimer.current = setTimeout(
        () => requestPreview(r, refPathNow, nextAdjustments, nextMode, seq, includeAnalysis),
        delay,
      );
    },
    [requestPreview],
  );

  // Session 每次打开都会重新挂载：初始化原图并自动请求首帧，卸载时作废在途请求。
  useEffect(() => {
    const seqRef = previewSeq;
    const timerRef = debounceTimer;
    const controllerRef = abortPreview;
    const src = new Image();
    src.src = api.imgUrl(srcUrl);
    src.onload = () => redraw();
    srcImgRef.current = src;
    seqRef.current++;
    // 局部模式先等待自动蒙版；旧全图模式切换后再主动请求预览。
    return () => {
      seqRef.current++;
      if (timerRef.current) clearTimeout(timerRef.current);
      controllerRef.current?.abort();
      src.onload = null;
    };
  }, [refPath, requestPreview, redraw, srcUrl]);

  const onLocalMaskChange = useCallback((nextMask: string, bounds: ColorMatchRect) => {
    maskRef.current = nextMask;
    setMaskB64(nextMask);
    rectRef.current = bounds;
    setRect(bounds);
    if (!nextMask || !ref.path) {
      setReady(false);
      setHasPreview(false);
      return;
    }
    const nextStrength = strengthRef.current || 0.7;
    strengthRef.current = nextStrength;
    setStrength(nextStrength);
    adjustmentModeRef.current = "auto";
    setAdjustmentMode("auto");
    setAnalysis(null);
    autoPreviewRef.current = null;
    schedulePreview(bounds, ref.path, DEFAULT_ADJUSTMENTS, "auto", 80, true);
  }, [ref.path, schedulePreview]);

  function changeScope(nextScope: ColorScope) {
    if (nextScope === scopeRef.current) return;
    scopeRef.current = nextScope;
    setScope(nextScope);
    autoPreviewRef.current = null;
    fullPreviewRef.current = null;
    setHasPreview(false);
    setReady(false);
    setAnalysis(null);
    if (nextScope === "global") {
      strengthRef.current = 0;
      setStrength(0);
      adjustmentModeRef.current = "manual";
      setAdjustmentMode("manual");
      setAdjustments({ ...DEFAULT_ADJUSTMENTS });
      schedulePreview(rectRef.current, ref.path, DEFAULT_ADJUSTMENTS, "manual", 50, true);
    } else if (maskRef.current) {
      strengthRef.current = 0.7;
      setStrength(0.7);
      adjustmentModeRef.current = "auto";
      setAdjustmentMode("auto");
      schedulePreview(rectRef.current, ref.path, DEFAULT_ADJUSTMENTS, "auto", 50, true);
    }
  }

  function norm(e: React.PointerEvent): { x: number; y: number } | null {
    const el = box.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return null;
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    };
  }

  function onDown(e: React.PointerEvent) {
    const p = norm(e);
    if (!p) return;
    dragStart.current = p;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const next = { x: p.x, y: p.y, w: 0.001, h: 0.001 };
    rectRef.current = next;
    setRect(next);
    previewSeq.current++;
    abortPreview.current?.abort();
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    autoPreviewRef.current = null;
    setAnalysis(null);
    setAnalyzing(false);
    setReady(false);
  }
  function onMove(e: React.PointerEvent) {
    const s = dragStart.current;
    if (!s) return;
    const p = norm(e);
    if (!p) return;
    const next = {
      x: Math.min(s.x, p.x),
      y: Math.min(s.y, p.y),
      w: Math.max(0.001, Math.abs(p.x - s.x)),
      h: Math.max(0.001, Math.abs(p.y - s.y)),
    };
    rectRef.current = next;
    setRect(next);
  }
  function onUp() {
    if (!dragStart.current) return;
    dragStart.current = null;
    // 新选区先回到原图，只做三区诊断；用户点击后才应用建议。
    const next = { ...DEFAULT_ADJUSTMENTS };
    strengthRef.current = 0;
    setStrength(0);
    setAdjustments(next);
    adjustmentModeRef.current = "manual";
    setAdjustmentMode("manual");
    setAnalysis(null);
    schedulePreview(rectRef.current, ref.path, next, "manual", 350, true);
  }

  async function doSave() {
    setSaving(true);
    try {
      if (target.kind === "job") {
        const jv = await api.jobColorMatch(target.jobId, {
          image_rel: imageRel,
          ref_path: ref.path,
          rect,
          strength,
          adjustments,
          adjustment_mode: adjustmentMode,
          scope,
          mask_b64: maskB64,
          mask_feather: maskFeather,
          stage: target.stage,
        });
        toast.success("已保存为新候选（‹n/N› 可切回原图对比）");
        onDone?.(jv);
      } else {
        await api.recordColorMatch({
          json_path: target.jsonPath,
          record_id: target.recordId,
          result_id: target.resultId,
          ref_path: ref.path,
          rect,
          strength,
          adjustments,
          adjustment_mode: adjustmentMode,
          scope,
          mask_b64: maskB64,
          mask_feather: maskFeather,
        });
        toast.success("校色结果已追加到该记录");
        onDone?.();
      }
      onOpenChange(false);
    } catch (e) {
      toast.error("保存失败：" + (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function pickRef(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    try {
      const s = await api.uploadRef(f);
      setRef({ url: s.url, path: s.path }); // 用原图 URL：贴片点开放大要看全尺寸小样
      autoPreviewRef.current = null;
      const next = { ...DEFAULT_ADJUSTMENTS };
      const nextStrength = scopeRef.current === "floor_mask" ? 0.7 : 0;
      strengthRef.current = nextStrength;
      setStrength(nextStrength);
      setAdjustments(next);
      const nextMode: AdjustmentMode = scopeRef.current === "floor_mask" ? "auto" : "manual";
      adjustmentModeRef.current = nextMode;
      setAdjustmentMode(nextMode);
      setAnalysis(null);
      if (scopeRef.current === "global" || maskRef.current) {
        schedulePreview(rect, s.path, next, nextMode, 350, true);
      }
      toast.success("已更换参照图");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  function updateAdjustment(key: AdjustmentKey, value: number) {
    const next = { ...adjustments, [key]: value };
    setAdjustments(next);
    adjustmentModeRef.current = "manual";
    setAdjustmentMode("manual");
    schedulePreview(rect, ref.path, next, "manual");
  }

  function restoreOriginal() {
    const next = { ...DEFAULT_ADJUSTMENTS };
    setStrength(0);
    strengthRef.current = 0;
    setAdjustments(next);
    adjustmentModeRef.current = "manual";
    setAdjustmentMode("manual");
    schedulePreview(rect, ref.path, next, "manual");
  }

  function restoreAuto(strength = lastAutoStrengthRef.current) {
    const nextStrength = Math.max(0, Math.min(1, strength));
    if (nextStrength > 0) lastAutoStrengthRef.current = nextStrength;
    strengthRef.current = nextStrength;
    setStrength(nextStrength);
    adjustmentModeRef.current = "auto";
    setAdjustmentMode("auto");
    setAdjustments(scaleAutoAdjustments(autoAdjustmentsRef.current, nextStrength));

    previewSeq.current++;
    abortPreview.current?.abort();
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    if (autoPreviewRef.current) {
      fullPreviewRef.current = autoPreviewRef.current;
      setReady(true);
      setPreviewing(false);
      redraw();
    } else {
      schedulePreview(rect, ref.path, DEFAULT_ADJUSTMENTS, "auto");
    }
  }

  function applySuggestedAdjustments() {
    if (!analysis || analysis.status === "insufficient_region") return;
    const next = { ...analysis.recommended_adjustments };
    strengthRef.current = 0;
    setStrength(0);
    setAdjustments(next);
    adjustmentModeRef.current = "manual";
    setAdjustmentMode("manual");
    setAdvancedOpen(true);
    schedulePreview(rect, ref.path, next, "manual");
  }

  const pct = (v: number) => `${v * 100}%`;
  const hasAdjustments = Object.values(adjustments).some((value) => value !== 0);
  const suggestionValues = analysis
    ? (["temperature", "tint", "saturation"] as AdjustmentKey[])
        .filter((key) => analysis.recommended_adjustments[key] !== 0)
        .map((key) => {
          const label = ADJUSTMENT_CONTROLS.find((item) => item.key === key)?.label ?? key;
          const value = analysis.recommended_adjustments[key];
          return `${label} ${value > 0 ? "+" : ""}${Math.round(value)}`;
        })
    : [];
  const paneTitle =
    "mb-1.5 flex items-center gap-1.5 text-[11px] font-extrabold tracking-[0.08em] text-accent-foreground";

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* sm:前缀必须带：DialogContent 默认 sm:max-w-sm，无前缀的 max-w 在 sm+ 会被它覆盖 */}
      <DialogContent className="max-h-[94vh] max-w-[96vw] overflow-y-auto sm:max-w-[min(96vw,1500px)]">
        <div className="space-y-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="text-[15.5px] font-bold">地板校色</div>
              <div className="inline-flex rounded-lg border border-border bg-panel p-0.5">
                <button type="button" onClick={() => changeScope("floor_mask")} className={`rounded-md px-2.5 py-1 text-[11px] font-bold ${scope === "floor_mask" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}>地板局部（默认）</button>
                <button type="button" onClick={() => changeScope("global")} className={`rounded-md px-2.5 py-1 text-[11px] font-bold ${scope === "global" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}>全图校准（兼容）</button>
              </div>
            </div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              {scope === "floor_mask"
                ? "AI 先识别地板；绿色笔补选、红色笔排除。校色严格限制在绿色蒙版内，墙面和家具保持原样。"
                : "兼容旧方式：左图框选地板作为取样，校色参数作用于整张效果图。"}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 max-[1000px]:grid-cols-1">
            {/* 左栏：原图 + 框选 */}
            <div className="min-w-0">
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
            </div>

            {/* 右栏：实时校色结果（canvas） */}
            <div className="min-w-0">
              <div className={paneTitle}>
                预览 · {adjustmentMode === "auto" ? "自动校准" : hasAdjustments ? "手动参数" : "原图"}
                {previewing && (
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.6"
                    strokeLinecap="round"
                    className="animate-dc-spin text-primary"
                  >
                    <path d="M21 12a9 9 0 1 1-6.2-8.6" />
                  </svg>
                )}
                <span className="ml-auto text-[11px] font-semibold text-muted-foreground">
                  点击放大看细节
                </span>
              </div>
              <div className="relative overflow-hidden rounded-[10px] border border-border bg-black/5">
                <canvas
                  ref={canvasRef}
                  onClick={() => {
                    const full = fullPreviewRef.current;
                    if (!full) return;
                    const isAuto = adjustmentModeRef.current === "auto";
                    setZoom({
                      url: full.src,
                      baseUrl: isAuto ? api.imgUrl(srcUrl) : undefined,
                      overlayOpacity: isAuto ? strengthRef.current : 1,
                    });
                  }}
                  className={`block w-full cursor-zoom-in object-contain ${advancedOpen ? "max-h-[44vh]" : "max-h-[62vh]"}`}
                />
                {!hasPreview && (
                  <div className="absolute inset-0 flex items-center justify-center text-[12.5px] text-muted-foreground">
                    {previewing
                      ? "首次预览生成中…"
                      : !ref.path
                      ? "请先在下方选择参照小样"
                      : scope === "floor_mask"
                      ? "等待有效地板蒙版"
                      : "框选地板区域后自动出结果"}
                  </div>
                )}
                {/* 小样参照贴片：校色的目标色，贴着结果图对比 */}
                {ref.url && showRefPatch && (
                  <div className="absolute right-2 top-2 overflow-hidden rounded-[10px] border-2 border-white/90 shadow-[0_4px_14px_rgba(0,0,0,.35)]">
                    <img
                      src={api.imgUrl(ref.url)}
                      alt="小样参照"
                      title="点击放大小样"
                      onClick={(e) => {
                        e.stopPropagation();
                        setZoom({ url: api.imgUrl(ref.url) });
                      }}
                      className="block h-[clamp(100px,14vw,180px)] w-[clamp(100px,14vw,180px)] cursor-zoom-in object-cover"
                    />
                    <span className="pointer-events-none absolute left-0 top-0 rounded-br-md bg-[rgba(26,24,21,.6)] px-[6px] py-[2px] text-[10px] font-bold text-white">
                      小样
                    </span>
                    <button
                      title="收起小样"
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowRefPatch(false);
                      }}
                      className="absolute right-0 top-0 flex h-[18px] w-[18px] items-center justify-center rounded-bl-md bg-[rgba(26,24,21,.6)] text-[10px] font-bold text-white hover:bg-[rgba(26,24,21,.85)]"
                    >
                      ✕
                    </button>
                  </div>
                )}
                {ref.url && !showRefPatch && (
                  <button
                    title="显示小样参照"
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowRefPatch(true);
                    }}
                    className="absolute right-2 top-2 rounded-lg bg-[rgba(26,24,21,.6)] px-2 py-1 text-[11px] font-bold text-white hover:bg-[rgba(26,24,21,.85)]"
                  >
                    小样
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="rounded-[12px] border border-border bg-panel/45 p-3">
            <div className="mb-2.5 flex items-center gap-2">
              <div className="text-[12.5px] font-bold text-secondary-foreground">地板光照三区诊断</div>
              {analyzing && (
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" className="animate-dc-spin text-primary">
                  <path d="M21 12a9 9 0 1 1-6.2-8.6" />
                </svg>
              )}
              <span className="ml-auto text-[10.5px] text-muted-foreground">截图来自未调色原图</span>
            </div>

            {analyzing && !analysis ? (
              <div className="grid grid-cols-3 gap-2 max-[850px]:grid-cols-1">
                {[0, 1, 2].map((item) => (
                  <div key={item} className="h-[112px] animate-pulse rounded-[9px] border border-border bg-card/70" />
                ))}
              </div>
            ) : analysis ? (
              <>
                <div className="grid grid-cols-3 gap-2 max-[850px]:grid-cols-1">
                  {analysis.zones.map((zone) => (
                    <div key={zone.zone} className="flex min-w-0 gap-2 rounded-[9px] border border-border bg-card p-2">
                      {zone.preview ? (
                        <button
                          type="button"
                          title={`放大${zone.label}截图`}
                          onClick={() => setZoom({ url: zone.preview! })}
                          className="h-[82px] w-[110px] flex-none overflow-hidden rounded-[7px] border border-border bg-black/5"
                        >
                          <img src={zone.preview} alt={`${zone.label}地板截图`} className="h-full w-full object-cover" />
                        </button>
                      ) : (
                        <div className="flex h-[82px] w-[110px] flex-none items-center justify-center rounded-[7px] border border-dashed border-border bg-panel px-2 text-center text-[10px] text-muted-foreground">
                          未提取到明显区域
                        </div>
                      )}
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 text-[11.5px] font-bold text-secondary-foreground">
                          {zone.label}
                          {zone.luminance !== null && (
                            <span className="text-[9.5px] font-medium text-muted-foreground">L {zone.luminance}</span>
                          )}
                        </div>
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {zone.hints.map((hint, index) => (
                            <span
                              key={`${hint.code}:${index}`}
                              className={`rounded border px-1.5 py-0.5 text-[9.5px] leading-4 ${HINT_STYLES[hint.code]}`}
                            >
                              {hint.text}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-[9px] border border-border bg-card px-3 py-2">
                  <div className="min-w-[240px] flex-1">
                    <div className="text-[11px] font-bold text-secondary-foreground">综合建议</div>
                    <div className="mt-0.5 text-[10.5px] text-muted-foreground">{analysis.summary}</div>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    {suggestionValues.length ? suggestionValues.map((value) => (
                      <span key={value} className="rounded-md bg-accent px-2 py-1 text-[10.5px] font-bold text-accent-foreground">{value}</span>
                    )) : (
                      <span className="text-[10.5px] font-semibold text-muted-foreground">建议参数均为 0</span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={applySuggestedAdjustments}
                    disabled={analysis.status === "insufficient_region" || suggestionValues.length === 0 || previewing}
                    className="h-8 flex-none rounded-[8px] bg-primary px-3 text-[11.5px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-45"
                  >
                    应用建议参数
                  </button>
                </div>
              </>
            ) : (
              <div className="rounded-[9px] border border-dashed border-border py-7 text-center text-[11px] text-muted-foreground">
                {scope === "floor_mask" ? "生成有效地板蒙版后自动诊断" : "框选地板后自动生成三区截图和调色建议"}
              </div>
            )}
          </div>

          <div className="space-y-2.5">
            <div className="flex flex-wrap items-center gap-3 lg:flex-nowrap">
              {/* 参照小样 */}
              <div className="flex flex-none items-center gap-2">
                <div className="h-[44px] w-[44px] overflow-hidden rounded-[8px] border border-border bg-panel">
                  {ref.url ? (
                    <img src={api.imgUrl(ref.url)} alt="参照" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-[10px] text-muted-foreground">无</div>
                  )}
                </div>
                <label className="cursor-pointer text-[11.5px] font-semibold text-secondary-foreground hover:text-accent-foreground">
                  {ref.path ? "换参照" : "选择参照"}
                  <input type="file" accept="image/*" className="hidden" onChange={pickRef} />
                </label>
              </div>

              {/* 自动校准强度（客户端即时混合，1% 步进） */}
              <div className="flex min-w-[260px] flex-1 items-center gap-3">
                <span className="flex-none text-[12px] font-semibold text-secondary-foreground" title={scope === "floor_mask" ? "仅在地板蒙版内做稳健色度校准" : "使用框选地板计算偏差，对整张图做自动校准"}>{scope === "floor_mask" ? "地板自动校准" : "全图自动校准"}</span>
                <Slider
                  value={strength}
                  min={0}
                  max={1}
                  step={0.01}
                  disabled={!ref.path || (scope === "floor_mask" && !maskB64)}
                  onValueChange={(v) => {
                    const s = Array.isArray(v) ? v[0] : (v as number);
                    restoreAuto(s);
                  }}
                />
                <span className="w-10 flex-none text-right text-[12px] tabular-nums text-muted-foreground">
                  {Math.round(strength * 100)}%
                </span>
              </div>

              <button
                type="button"
                aria-expanded={advancedOpen}
                onClick={() => setAdvancedOpen((value) => !value)}
                className="h-9 flex-none rounded-[9px] border border-border bg-panel px-3 text-[12px] font-bold text-secondary-foreground hover:bg-accent hover:text-accent-foreground"
              >
                高级选项 · {adjustmentMode === "auto" ? "自动基准" : "手动"} {advancedOpen ? "收起" : "展开"}
              </button>

              <button
                onClick={doSave}
                disabled={!ready || saving || maskBusy || (scope === "floor_mask" && !maskB64)}
                title={!ref.path ? "请先选择参照图" : !ready ? "预览更新中…" : undefined}
                className="h-9 flex-none rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
              >
                {saving
                  ? "保存中…"
                  : !ready
                  ? "更新预览中…"
                  : target.kind === "job"
                  ? "保存为新候选"
                  : "保存到记录"}
              </button>
            </div>

            {advancedOpen && (
              <div className="rounded-[10px] border border-border bg-panel/60 p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[12px] font-bold text-secondary-foreground">
                      以 Gemini 原图为零点的专业调色
                    </div>
                    <div className="text-[10.5px] text-muted-foreground">
                      {scope === "floor_mask"
                        ? "自动校准与手动滑块都只作用于地板蒙版；地板明暗保留，只修正偏色"
                        : "框选地板只用于计算偏差；自动校准、建议参数和手动滑块均作用于整张效果图"}
                    </div>
                  </div>
                  <div className="flex flex-none items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => restoreAuto()}
                      className="rounded-md px-2 py-1 text-[11px] font-semibold text-accent-foreground hover:bg-accent"
                    >
                      恢复自动校准
                    </button>
                    <button
                      type="button"
                      onClick={restoreOriginal}
                      disabled={adjustmentMode === "manual" && !hasAdjustments}
                      className="rounded-md px-2 py-1 text-[11px] font-semibold text-accent-foreground hover:bg-accent disabled:opacity-40"
                    >
                      恢复 Gemini 原图
                    </button>
                  </div>
                </div>
                {scope === "floor_mask" && (
                  <div className="mb-3 flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2">
                    <span className="w-[74px] flex-none text-[11.5px] font-semibold text-secondary-foreground">蒙版内羽化</span>
                    <Slider
                      value={maskFeather}
                      min={0}
                      max={0.02}
                      step={0.001}
                      onValueChange={(value) => {
                        const next = Array.isArray(value) ? value[0] : value as number;
                        maskFeatherRef.current = next;
                        setMaskFeather(next);
                        schedulePreview(rectRef.current, ref.path, adjustments, adjustmentMode);
                      }}
                    />
                    <span className="w-12 flex-none text-right text-[11px] tabular-nums text-muted-foreground">{(maskFeather * 100).toFixed(1)}%</span>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 max-[850px]:grid-cols-1">
                  {ADJUSTMENT_CONTROLS.map((control) => {
                    const value = adjustments[control.key];
                    const display = control.key === "exposure"
                      ? `${value > 0 ? "+" : ""}${value.toFixed(1)} EV`
                      : `${value > 0 ? "+" : ""}${Math.round(value)}`;
                    return (
                      <div key={control.key} className="flex min-w-0 items-center gap-2" title={control.hint}>
                        <span className="w-[62px] flex-none text-[11.5px] font-semibold text-secondary-foreground">
                          {control.label}
                        </span>
                        <Slider
                          value={value}
                          min={control.min}
                          max={control.max}
                          step={control.step}
                          onValueChange={(v) => {
                            const next = Array.isArray(v) ? v[0] : (v as number);
                            updateAdjustment(control.key, next);
                          }}
                        />
                        <span className="w-[58px] flex-none text-right text-[11px] tabular-nums text-muted-foreground">
                          {display}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
    <ImageZoom
      url={zoom?.url || null}
      baseUrl={zoom?.baseUrl}
      overlayOpacity={zoom?.overlayOpacity}
      onClose={() => setZoom(null)}
    />
    </>
  );
}
