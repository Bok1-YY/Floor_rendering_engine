// 定点球面全景:项目级固定约定(文档 §7.3/§7.6)。
// face order 与 basis 约定和后端 whole_home_pano_render.face_basis 完全一致;
// 前端渲染与后端 ERP 转换共用同一套常量,任何改动必须两端同步并更新测试。
export const PANO_FACE_ORDER = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"] as const;

export type PanoFace = (typeof PANO_FACE_ORDER)[number];

export interface PanoAtlasCell {
  row: 0 | 1;
  col: 0 | 1 | 2;
}

/** P0 付费编辑合同固定输出尺寸；cubemap face 分辨率与最终 ERP 解耦。 */
export const PANO_P0_ERP_SIZE = { width: 3840, height: 1920 } as const;

// Public viewer FOV is horizontal. Three.js accepts vertical FOV, so the
// component converts through the live canvas aspect ratio. This prevents a
// 60° value from silently becoming ~116° horizontally in a very wide dialog.
export const PANO_VIEW_DEFAULT_FOV_DEG = 90;
export const PANO_VIEW_WIDE_FOV_DEG = 105;
export const PANO_VIEW_MIN_FOV_DEG = 45;
export const PANO_VIEW_MAX_FOV_DEG = 105;
export const PANO_VIEW_MAX_PITCH_DEG = 85;

export function clampPanoPitch(pitchDeg: number): number {
  return Math.max(-PANO_VIEW_MAX_PITCH_DEG, Math.min(PANO_VIEW_MAX_PITCH_DEG, pitchDeg));
}

export function clampPanoFov(fovDeg: number): number {
  return Math.max(PANO_VIEW_MIN_FOV_DEG, Math.min(PANO_VIEW_MAX_FOV_DEG, fovDeg));
}

export function horizontalPanoFovToVertical(horizontalFovDeg: number, aspect: number): number {
  const safeAspect = Math.max(0.1, Number.isFinite(aspect) ? aspect : 1);
  const horizontal = clampPanoFov(horizontalFovDeg) * Math.PI / 180;
  return 2 * Math.atan(Math.tan(horizontal / 2) / safeAspect) * 180 / Math.PI;
}

/** Viewer 只接受完整的 360×180 ERP；允许 1% 编码/元数据误差。 */
export function isEquirectangularSize(width: number, height: number): boolean {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return false;
  return Math.abs(width / height - 2) <= 0.02;
}

// 3×2 图集布局:row0 = +X|-X|+Y,row1 = -Y|+Z|-Z(文档 §7.6)。
export const PANO_ATLAS_CELL: Record<PanoFace, PanoAtlasCell> = {
  "+X": { row: 0, col: 0 },
  "-X": { row: 0, col: 1 },
  "+Y": { row: 0, col: 2 },
  "-Y": { row: 1, col: 0 },
  "+Z": { row: 1, col: 1 },
  "-Z": { row: 1, col: 2 },
};

/** 3×2 图集尺寸(六面均为正方形)。 */
export function panoAtlasSize(faceSize: number): { width: number; height: number } {
  return { width: faceSize * 3, height: faceSize * 2 };
}

/** ERP 必须严格 2:1;cube face N → ERP 4N×2N。 */
export function panoErpSize(faceSize: number): { width: number; height: number } {
  return { width: faceSize * 4, height: faceSize * 2 };
}

/** 校验 atlas/ERP 尺寸契约;不合法返回错误信息。 */
export function panoSizeError(cubeFaceSize: number, atlasWidth: number, atlasHeight: number): string {
  const atlas = panoAtlasSize(cubeFaceSize);
  if (atlasWidth !== atlas.width || atlasHeight !== atlas.height) {
    return `atlas ${atlasWidth}x${atlasHeight} 不符合 3×2 布局(face=${cubeFaceSize}, 期望 ${atlas.width}x${atlas.height})`;
  }
  return "";
}

// ── 人工验收检查序列(文档 §9.3)──────────────────────────
export interface PanoChecklistStep {
  yawDeg: number;
  pitchDeg: number;
  label: string;
}

export const PANO_CHECKLIST_ITEMS = [
  "wall_openings",
  "duplicates",
  "material_continuity",
  "lighting_continuity",
  "poles",
  "cross_hotspot_same_object",
] as const;

export type PanoChecklistItem = (typeof PANO_CHECKLIST_ITEMS)[number];

export type PanoChecklistResult = Record<PanoChecklistItem, "pass" | "uncertain" | "unchecked">;

/** 固定检查序列:yaw 0→90→180→270→360(pitch 0),然后每 45° 俯仰 ±60°,
 *  最后停留在左右接缝方向(λ=±180°)与天顶/地底(文档 §9.3)。 */
export function panoChecklistSequence(): PanoChecklistStep[] {
  const steps: PanoChecklistStep[] = [];
  for (const yaw of [0, 90, 180, 270, 360]) {
    steps.push({ yawDeg: yaw, pitchDeg: 0, label: `yaw ${yaw}° · pitch 0°` });
  }
  for (let yaw = 0; yaw < 360; yaw += 45) {
    steps.push({ yawDeg: yaw, pitchDeg: 60, label: `yaw ${yaw}° · pitch +60°` });
    steps.push({ yawDeg: yaw, pitchDeg: -60, label: `yaw ${yaw}° · pitch -60°` });
  }
  steps.push({ yawDeg: 180, pitchDeg: 0, label: "接缝方向(λ=±180°)" });
  steps.push({ yawDeg: 0, pitchDeg: 90, label: "天顶(+90°)" });
  steps.push({ yawDeg: 0, pitchDeg: -90, label: "地底(-90°)" });
  return steps;
}

export function emptyPanoChecklistResult(): PanoChecklistResult {
  const result = {} as PanoChecklistResult;
  for (const item of PANO_CHECKLIST_ITEMS) result[item] = "unchecked";
  return result;
}

/** 六项全部回答后验收才有效;任一 uncertain 按失败处理(文档 §9.3)。 */
export function panoChecklistComplete(result: PanoChecklistResult): boolean {
  return PANO_CHECKLIST_ITEMS.every((item) => result[item] !== "unchecked");
}

export function panoChecklistPassed(result: PanoChecklistResult): boolean {
  return panoChecklistComplete(result)
    && PANO_CHECKLIST_ITEMS.every((item) => result[item] === "pass");
}
