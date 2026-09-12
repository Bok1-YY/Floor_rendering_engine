"use client";

import type { ColorMatchAdjustments, ColorMatchAlgorithm, ColorMatchAnalysis, ColorIlluminationMode, ColorQualityReport, ColorMatchRect } from "@/lib/types";

import { DEFAULT_ADJUSTMENTS, DEFAULT_RECT } from "@/lib/color-match/config";
import type { AdjustmentMode, ColorScope } from "@/lib/color-match/config";
import { useMemo } from "react";
import { useSessionStore } from "@/lib/editor/session-store";
export function useColorMatchState(refUrl: string, refPath: string) {
  const context = useSessionStore(() => ({
    rect: (DEFAULT_RECT) as ColorMatchRect,
    strength: 0.7,
    scope: ("floor_mask") as ColorScope,
    maskB64: "",
    maskFeather: 0.003,
    maskBusy: true,
    ref: ({ url: refUrl, path: refPath }) as { url: string; path: string },
    adjustments: ((() => ({ ...DEFAULT_ADJUSTMENTS }))()) as ColorMatchAdjustments,
    adjustmentMode: ("manual") as AdjustmentMode,
    advancedOpen: false,
    previewing: Boolean(refPath),
    analyzing: Boolean(refPath),
    analysis: (null) as ColorMatchAnalysis | null,
    quality: (null) as ColorQualityReport | null,
    algorithm: ("distribution") as ColorMatchAlgorithm,
    illuminationMode: ("off") as ColorIlluminationMode,
    appliedAlgorithm: (null) as ColorMatchAlgorithm | null,
    appliedIlluminationMode: ("off") as ColorIlluminationMode,
    appliedFallbackReason: "",
    previewEngineError: "",
    hasPreview: false,
    ready: false,
    saving: false,
    zoom: (null) as {
      url: string;
      baseUrl?: string;
      overlayOpacity?: number;
    } | null,
    showRefPatch: true
  }));
  const { store } = context;
  const actions = useMemo(() => ({
    setRect: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["rect"]>) => store.set("rect", value),
    setStrength: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["strength"]>) => store.set("strength", value),
    setScope: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["scope"]>) => store.set("scope", value),
    setMaskB64: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["maskB64"]>) => store.set("maskB64", value),
    setMaskFeather: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["maskFeather"]>) => store.set("maskFeather", value),
    setMaskBusy: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["maskBusy"]>) => store.set("maskBusy", value),
    setRef: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["ref"]>) => store.set("ref", value),
    setAdjustments: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["adjustments"]>) => store.set("adjustments", value),
    setAdjustmentMode: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["adjustmentMode"]>) => store.set("adjustmentMode", value),
    setAdvancedOpen: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["advancedOpen"]>) => store.set("advancedOpen", value),
    setPreviewing: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["previewing"]>) => store.set("previewing", value),
    setAnalyzing: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["analyzing"]>) => store.set("analyzing", value),
    setAnalysis: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["analysis"]>) => store.set("analysis", value),
    setQuality: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["quality"]>) => store.set("quality", value),
    setAlgorithm: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["algorithm"]>) => store.set("algorithm", value),
    setIlluminationMode: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["illuminationMode"]>) => store.set("illuminationMode", value),
    setAppliedAlgorithm: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["appliedAlgorithm"]>) => store.set("appliedAlgorithm", value),
    setAppliedIlluminationMode: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["appliedIlluminationMode"]>) => store.set("appliedIlluminationMode", value),
    setAppliedFallbackReason: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["appliedFallbackReason"]>) => store.set("appliedFallbackReason", value),
    setPreviewEngineError: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["previewEngineError"]>) => store.set("previewEngineError", value),
    setHasPreview: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["hasPreview"]>) => store.set("hasPreview", value),
    setReady: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["ready"]>) => store.set("ready", value),
    setSaving: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["saving"]>) => store.set("saving", value),
    setZoom: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["zoom"]>) => store.set("zoom", value),
    setShowRefPatch: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["showRefPatch"]>) => store.set("showRefPatch", value)
  }), [store]);
  return { ...context, actions };
}
export type ColorSession = ReturnType<typeof useColorMatchState>;
