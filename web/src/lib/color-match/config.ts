import type {
  ColorMatchAdjustments,
  ColorMatchAlgorithm,
  ColorMatchHintCode,
  ColorIlluminationMode,
  ColorMatchRect,
} from "@/lib/types";

export const DEFAULT_RECT: ColorMatchRect = { x: 0.05, y: 0.45, w: 0.9, h: 0.5 };
export const DEFAULT_ADJUSTMENTS: ColorMatchAdjustments = {
  temperature: 0, tint: 0, exposure: 0, contrast: 0, highlights: 0,
  shadows: 0, whites: 0, blacks: 0, midtones: 0, saturation: 0,
};

export type AdjustmentKey = keyof ColorMatchAdjustments;
export type AdjustmentMode = "auto" | "manual";
export type ColorScope = "floor_mask" | "global";
export type AdjustmentControl = {
  key: AdjustmentKey; label: string; min: number; max: number; step: number; hint: string;
};

export const ADJUSTMENT_CONTROLS: AdjustmentControl[] = [
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

export const HINT_STYLES: Record<ColorMatchHintCode, string> = {
  warm: "border-orange-200 bg-orange-50 text-orange-800 dark:border-orange-900 dark:bg-orange-950/40 dark:text-orange-200",
  cool: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200",
  green: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
  magenta: "border-pink-200 bg-pink-50 text-pink-800 dark:border-pink-900 dark:bg-pink-950/40 dark:text-pink-200",
  gray: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200",
  saturated: "border-violet-200 bg-violet-50 text-violet-800 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-200",
  matched: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
  unavailable: "border-border bg-panel text-muted-foreground",
};

export const algorithmLabel = (value: ColorMatchAlgorithm) => value === "distribution" ? "精细 2.0" : "经典 1.0";
export const illuminationLabel = (value: ColorIlluminationMode) => value === "chroma" ? "仅色偏" : value === "full" ? "色偏 + 明暗" : "光照关闭";

export function scaleAutoAdjustments(profile: ColorMatchAdjustments, strength: number): ColorMatchAdjustments {
  return Object.fromEntries(ADJUSTMENT_CONTROLS.map((control) => {
    const scaled = profile[control.key] * strength;
    const value = control.key === "exposure" ? Math.round(scaled * 10) / 10 : Math.round(scaled);
    return [control.key, Math.min(control.max, Math.max(control.min, value))];
  })) as unknown as ColorMatchAdjustments;
}
