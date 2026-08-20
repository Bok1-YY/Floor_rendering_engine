import type { WholeHomeSubjectIdLegend, WholeHomeSubjectIdLegendEntry } from "@/lib/types";

export const WHOLE_HOME_SUBJECT_ID_VERSION = "whole-home-subject-id-v1";

export interface SubjectIdBounds {
  subject: string;
  anchor_id: string;
  pixel_count: number;
  x_min: number;
  x_max: number;
  y_min: number;
  y_max: number;
}

export interface SubjectIdPixelEvidence {
  pass: boolean;
  width: number;
  height: number;
  pixel_origin: "top-left";
  must_show_bounds: SubjectIdBounds[];
  reasons: string[];
}

export function subjectIdColor(index: number): [number, number, number] {
  const value = Math.max(1, Math.min(0xffffff, Math.floor(index) + 1));
  return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
}

export function buildSubjectIdLegend(
  subjects: Array<Omit<WholeHomeSubjectIdLegendEntry, "color">>,
): WholeHomeSubjectIdLegend {
  return {
    version: WHOLE_HOME_SUBJECT_ID_VERSION,
    pixel_origin: "top-left",
    subjects: subjects.map((row, index) => ({ ...row, color: subjectIdColor(index) })),
  };
}

export function analyzeSubjectIdPixels(
  rgba: ArrayLike<number>,
  width: number,
  height: number,
  legend: WholeHomeSubjectIdLegend,
  safeFrame: { x_min: number; x_max: number; y_min: number; y_max: number },
  sourceOrigin: "top-left" | "bottom-left" = "bottom-left",
): SubjectIdPixelEvidence {
  const pixelCount = Math.max(0, Math.floor(width)) * Math.max(0, Math.floor(height));
  if (!pixelCount || rgba.length < pixelCount * 4) throw new Error("subject ID buffer 像素尺寸不完整");
  const colors = new Set<string>();
  const subjects = new Set<string>();
  const reasons: string[] = [];
  for (const entry of legend.subjects) {
    const colorKey = entry.color.join(",");
    if (colors.has(colorKey) || entry.color.every((value) => value === 0)) reasons.push(`${entry.subject}: subject ID 颜色不唯一`);
    if (subjects.has(entry.subject)) reasons.push(`${entry.subject}: subject 在 legend 中重复`);
    colors.add(colorKey);
    subjects.add(entry.subject);
  }
  const bounds = legend.subjects.map((entry) => {
    let minX = width; let maxX = -1; let minY = height; let maxY = -1; let count = 0;
    for (let sourceY = 0; sourceY < height; sourceY += 1) {
      const y = sourceOrigin === "bottom-left" ? height - 1 - sourceY : sourceY;
      for (let x = 0; x < width; x += 1) {
        const offset = (sourceY * width + x) * 4;
        if (Number(rgba[offset]) === entry.color[0]
          && Number(rgba[offset + 1]) === entry.color[1]
          && Number(rgba[offset + 2]) === entry.color[2]
          && Number(rgba[offset + 3]) > 0) {
          minX = Math.min(minX, x); maxX = Math.max(maxX, x);
          minY = Math.min(minY, y); maxY = Math.max(maxY, y); count += 1;
        }
      }
    }
    if (!count) reasons.push(`${entry.subject}: 被遮挡或不在画面内`);
    const row = {
      subject: entry.subject, anchor_id: entry.anchor_id, pixel_count: count,
      x_min: count ? minX / width : 0, x_max: count ? (maxX + 1) / width : 0,
      y_min: count ? minY / height : 0, y_max: count ? (maxY + 1) / height : 0,
    };
    if (count && !(row.x_min >= safeFrame.x_min && row.x_max <= safeFrame.x_max
      && row.y_min >= safeFrame.y_min && row.y_max <= safeFrame.y_max)) {
      reasons.push(`${entry.subject}: 超出 safe frame`);
    }
    return row;
  });
  return {
    pass: reasons.length === 0 && bounds.length > 0 && bounds.every((row) => row.pixel_count > 0),
    width, height, pixel_origin: "top-left", must_show_bounds: bounds, reasons,
  };
}
