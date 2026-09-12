"use client";
import { normalizedPoint } from "@/lib/editor/canvas";
import { useCallback, useRef } from "react";
import { reportCommitError } from "@/lib/result-commit";
import { api } from "@/lib/api";
import type { ColorMatchAlgorithm, ColorIlluminationMode, ColorMatchRect } from "@/lib/types";
import { toast } from "sonner";




import { ADJUSTMENT_CONTROLS, DEFAULT_ADJUSTMENTS, algorithmLabel, illuminationLabel, scaleAutoAdjustments } from "@/lib/color-match/config";
import type { AdjustmentKey, AdjustmentMode, ColorScope } from "@/lib/color-match/config";
import { useMemo } from "react";
import type { ColorMatchDialogProps } from "./types";
import { useColorMatchState } from "./useColorMatchState";
import { useColorCanvas } from "./useColorCanvas";
import { useColorPreview } from "./useColorPreview";
import { useAsyncScope } from "@/lib/editor/async-scope";
export function useColorMatchSession(props: ColorMatchDialogProps) {
  const { open, onOpenChange, srcUrl, imageRel, refUrl, refPath, target, onDone } = props;

  const session = useColorMatchState(refUrl, refPath);
  const { state, actions, store } = session;
  const { rect, strength, scope, maskB64, maskFeather, maskBusy, ref, adjustments, adjustmentMode, advancedOpen, previewing, analyzing, analysis, quality, algorithm, illuminationMode, appliedAlgorithm, appliedIlluminationMode, appliedFallbackReason, previewEngineError, hasPreview, ready, saving, zoom, showRefPatch } = state;
  const { setRect, setStrength, setScope, setMaskB64, setMaskFeather, setMaskBusy, setRef, setAdjustments, setAdjustmentMode, setAdvancedOpen, setPreviewing, setAnalyzing, setAnalysis, setQuality, setAlgorithm, setIlluminationMode, setHasPreview, setReady, setSaving, setZoom, setShowRefPatch } = actions;
  const lifetime = useAsyncScope();
  const uploads = useAsyncScope();
  const box = useRef<HTMLDivElement>(null);

  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const rectRef = useMemo(() => store.field("rect"), [store]);
  // 实时管线：原图 / 服务端 strength=1.0 完整结果；请求序号防乱序；防抖计时器







  const strengthRef = useMemo(() => store.field("strength"), [store]);
  const lastAutoStrengthRef = useRef(0.8);
  const adjustmentModeRef = useMemo(() => store.field("adjustmentMode"), [store]);
  const scopeRef = useMemo(() => store.field("scope"), [store]);
  const maskRef = useMemo(() => store.field("maskB64"), [store]);
  const algorithmRef = useMemo(() => store.field("algorithm"), [store]);
  const illuminationModeRef = useMemo(() => store.field("illuminationMode"), [store]);

  const canvas = useColorCanvas(store, srcUrl);
  const { canvasRef, fullPreviewRef, autoPreviewRef, autoAdjustmentsRef, redraw } = canvas;
  const { schedulePreview, invalidatePreview } = useColorPreview(session, canvas, imageRel);
  const onLocalMaskChange = useCallback((nextMask: string, bounds: ColorMatchRect) => {
    invalidatePreview();
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
    setQuality(null);
    autoPreviewRef.current = null;
    schedulePreview(bounds, ref.path, DEFAULT_ADJUSTMENTS, "auto", 80, true);
  }, [ref.path, schedulePreview, invalidatePreview, maskRef, rectRef, strengthRef, adjustmentModeRef, autoPreviewRef, setMaskB64, setRect, setReady, setHasPreview, setStrength, setAdjustmentMode, setAnalysis, setQuality]);

  function changeScope(nextScope: ColorScope) {
    if (nextScope === scopeRef.current) return;
    scopeRef.current = nextScope;
    setScope(nextScope);
    autoPreviewRef.current = null;
    fullPreviewRef.current = null;
    setHasPreview(false);
    setReady(false);
    setAnalysis(null);
    setQuality(null);
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

  function norm(e: React.PointerEvent) {
    return box.current ? normalizedPoint(e, box.current) : null;
  }

  function onDown(e: React.PointerEvent) {
    const p = norm(e);
    if (!p) return;
    dragStart.current = p;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const next = { x: p.x, y: p.y, w: 0.001, h: 0.001 };
    rectRef.current = next;
    setRect(next);
    invalidatePreview();
    autoPreviewRef.current = null;
    setAnalysis(null);
    setQuality(null);
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
    setQuality(null);
    schedulePreview(rectRef.current, ref.path, next, "manual", 350, true);
  }

  async function doSave() {
    const current = store.getSnapshot();
    if (!current.ready || current.saving || current.maskBusy || (current.scope === "floor_mask" && !current.maskB64)) return;
    const token = lifetime.token();
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
          algorithm,
          illumination_mode: illuminationMode,
          stage: target.stage,
        });
        if (!lifetime.valid(token)) return;
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
          algorithm,
          illumination_mode: illuminationMode,
        });
        if (!lifetime.valid(token)) return;
        toast.success("校色结果已追加到该记录");
        onDone?.();
      }
      onOpenChange(false);
    } catch (e) {
      if (lifetime.valid(token)) reportCommitError(e, (result) => { if (lifetime.valid(token)) { onDone?.(result.job); onOpenChange(false); } });
    } finally {
      if (lifetime.valid(token)) setSaving(false);
    }
  }

  async function pickRef(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    uploads.invalidate();
    const token = uploads.token();
    invalidatePreview();
    try {
      const s = await api.uploadRef(f);
      if (!uploads.valid(token)) return;
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
      setQuality(null);
      if (scopeRef.current === "global" || maskRef.current) {
        schedulePreview(rect, s.path, next, nextMode, 350, true);
      }
      toast.success("已更换参照图");
    } catch (err) {
      if (uploads.valid(token)) toast.error((err as Error).message);
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

    invalidatePreview();
    if (autoPreviewRef.current) {
      fullPreviewRef.current = autoPreviewRef.current;
      setReady(true);
      setPreviewing(false);
      redraw();
    } else {
      schedulePreview(rect, ref.path, DEFAULT_ADJUSTMENTS, "auto");
    }
  }

  function changeMaskFeather(next: number) {
    setMaskFeather(next);
    const current = store.getSnapshot();
    schedulePreview(current.rect, current.ref.path, current.adjustments, current.adjustmentMode);
  }

  function zoomResult() {
    const image = fullPreviewRef.current;
    if (!image) return;
    const current = store.getSnapshot();
    const isAuto = current.adjustmentMode === "auto";
    setZoom({ url: image.src, baseUrl: isAuto ? api.imgUrl(srcUrl) : undefined,
      overlayOpacity: isAuto ? current.strength : 1 });
  }

  function changeAlgorithm(next: ColorMatchAlgorithm) {
    if (next === algorithmRef.current
      && (next !== "classic" || illuminationModeRef.current === "off")) return;
    algorithmRef.current = next;
    setAlgorithm(next);
    if (next === "classic") {
      illuminationModeRef.current = "off";
      setIlluminationMode("off");
    }
    autoPreviewRef.current = null;
    setQuality(null);
    adjustmentModeRef.current = "auto";
    setAdjustmentMode("auto");
    toast.info(`正在切换到${algorithmLabel(next)}，完成前画布保留原预览`);
    schedulePreview(rectRef.current, ref.path, DEFAULT_ADJUSTMENTS, "auto", 80, true);
  }

  function changeIlluminationMode(next: ColorIlluminationMode) {
    if (next === illuminationModeRef.current) return;
    illuminationModeRef.current = next;
    setIlluminationMode(next);
    if (next !== "off") {
      algorithmRef.current = "distribution";
      setAlgorithm("distribution");
    }
    autoPreviewRef.current = null;
    setQuality(null);
    adjustmentModeRef.current = "auto";
    setAdjustmentMode("auto");
    toast.info(`正在应用${illuminationLabel(next)}，完成前画布保留原预览`);
    schedulePreview(rectRef.current, ref.path, DEFAULT_ADJUSTMENTS, "auto", 80, true);
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
  const selectedAlgorithmLabel = algorithmLabel(algorithm);
  const appliedAlgorithmLabel = appliedAlgorithm ? algorithmLabel(appliedAlgorithm) : "尚未生成";
  const modeSwitchPending = previewing && appliedAlgorithm !== null
    && (algorithm !== appliedAlgorithm || illuminationMode !== appliedIlluminationMode);
  const previewEngineStatus = previewEngineError
    ? appliedAlgorithm
      ? `更新失败：当前画面仍是${appliedAlgorithmLabel}`
      : `预览生成失败：${previewEngineError}`
    : !ref.path
      ? `等待参照图 · 目标${selectedAlgorithmLabel}`
      : scope === "floor_mask" && !maskB64
        ? `等待地板蒙版 · 目标${selectedAlgorithmLabel}`
        : appliedAlgorithm === null
          ? `${previewing ? "正在生成" : "等待生成"}${selectedAlgorithmLabel}预览…`
          : modeSwitchPending
            ? `切换中：当前画面 ${appliedAlgorithmLabel} → ${selectedAlgorithmLabel}`
            : algorithm !== appliedAlgorithm
              ? `当前画面：${appliedAlgorithmLabel} · ${selectedAlgorithmLabel}已回退`
              : illuminationMode !== appliedIlluminationMode
                ? `当前画面：${appliedAlgorithmLabel} · 光照校正未应用`
                : `当前画面：${appliedAlgorithmLabel} · ${illuminationLabel(appliedIlluminationMode)}`;


  return {
    state: { scope, algorithm, advancedOpen, rect, adjustmentMode, previewing, appliedFallbackReason, previewEngineError, appliedAlgorithm, illuminationMode, appliedIlluminationMode, hasPreview, ref, showRefPatch, analyzing, analysis, quality, strength, maskB64, ready, saving, maskBusy, maskFeather, adjustments, zoom },
    actions: { changeScope, changeAlgorithm, setZoom, setMaskBusy, onDown, onMove, onUp, setShowRefPatch, applySuggestedAdjustments, restoreAuto, setAdvancedOpen, doSave, changeIlluminationMode, restoreOriginal, changeMaskFeather, updateAdjustment, pickRef, onLocalMaskChange, zoomResult },
    canvas: { box, canvasRef },
    display: { paneTitle, pct, hasAdjustments, modeSwitchPending, previewEngineStatus, appliedAlgorithmLabel, suggestionValues },
    input: { open, onOpenChange, srcUrl, imageRel, target }
  };
}