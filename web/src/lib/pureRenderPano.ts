import type {
  PanoramaGateStatus,
  PureRenderPanoramaReviewChecklist,
  PanoramaReviewValue,
} from "@/lib/types";

export const PURE_RENDER_PANO_REVIEW_ITEMS = [
  "wrap_seam",
  "horizon_and_lines",
  "object_integrity",
  "floor_and_material",
  "lighting_continuity",
  "poles",
] as const;

export type PureRenderPanoReviewItem = (typeof PURE_RENDER_PANO_REVIEW_ITEMS)[number];
export type PureRenderPanoChecklistDraft = Record<
  PureRenderPanoReviewItem,
  PanoramaReviewValue | "unchecked"
>;

export const PURE_RENDER_PANO_REVIEW_LABELS: Record<PureRenderPanoReviewItem, string> = {
  wrap_seam: "左右环缝连续",
  horizon_and_lines: "地平线与墙/门窗线合理",
  object_integrity: "无重复、缺失、半截或漂浮物体",
  floor_and_material: "地板与主要材质连续",
  lighting_continuity: "光照、阴影与色温连续",
  poles: "天顶/地底无黑洞、旋涡或强拉伸",
};

export function emptyPureRenderPanoChecklist(): PureRenderPanoChecklistDraft {
  return Object.fromEntries(
    PURE_RENDER_PANO_REVIEW_ITEMS.map((item) => [item, "unchecked"]),
  ) as PureRenderPanoChecklistDraft;
}

export function pureRenderPanoChecklistComplete(
  result: PureRenderPanoChecklistDraft,
): result is PureRenderPanoramaReviewChecklist {
  return PURE_RENDER_PANO_REVIEW_ITEMS.every((item) => result[item] !== "unchecked");
}

export function pureRenderPanoChecklistPassed(result: PureRenderPanoChecklistDraft): boolean {
  return PURE_RENDER_PANO_REVIEW_ITEMS.every((item) => result[item] === "pass");
}

export function panoramaGateLabel(status?: PanoramaGateStatus): string {
  if (status === "passed") return "自动检查通过";
  if (status === "repair_recommended") return "建议修复结构/接缝";
  if (status === "failed") return "自动检查失败";
  return "待自动检查";
}

export function panoramaGateTone(status?: PanoramaGateStatus): string {
  if (status === "passed") return "bg-emerald-50 text-emerald-800";
  if (status === "repair_recommended") return "bg-amber-50 text-amber-800";
  return "bg-red-50 text-red-800";
}
