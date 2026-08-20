import type { WholeHomeGeometryManifest, WholeHomeModel } from "@/lib/types";

export const ORTHOGRAPHIC_AUDIT_PADDING = 0.05;
export const ORTHOGRAPHIC_AUDIT_EXPORT_SIZE = 1600;
export const ORTHOGRAPHIC_CAMERA_CONTRACT_VERSION = 2;
export const ORTHOGRAPHIC_CAMERA_COORDINATE_SYSTEM = "right-handed-y-up-x-east-z-south-v2";

export interface OrthographicAuditBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  minZ: number;
  maxZ: number;
}

export interface OrthographicAuditFrame {
  centerX: number;
  centerY: number;
  centerZ: number;
  cameraY: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
  near: number;
  far: number;
}

export interface OrthographicCameraContractV2 {
  schema_version: 2;
  contract: "whole_home_sky_down_orthographic_v2";
  coordinate_system: typeof ORTHOGRAPHIC_CAMERA_COORDINATE_SYSTEM;
  model_coordinate_contract_version: 2;
  projection: "orthographic";
  renderer: "threejs_webgl";
  webgl_capture: true;
  view_direction: [0, -1, 0];
  camera_up: [0, 0, -1];
  screen_right: [1, 0, 0];
  cad_axis_mapping: { cad_x: "+screen_right"; cad_y: "+screen_up" };
  eye: [number, number, number];
  target: [number, number, number];
  frustum: Pick<OrthographicAuditFrame, "left" | "right" | "top" | "bottom" | "near" | "far">;
  padding_per_side: number;
  viewport: [1600, 1600];
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function normalizedBounds(bounds: OrthographicAuditBounds): OrthographicAuditBounds {
  const minX = Math.min(bounds.minX, bounds.maxX);
  const maxX = Math.max(bounds.minX, bounds.maxX);
  const minY = Math.min(bounds.minY, bounds.maxY);
  const maxY = Math.max(bounds.minY, bounds.maxY);
  const minZ = Math.min(bounds.minZ, bounds.maxZ);
  const maxZ = Math.max(bounds.minZ, bounds.maxZ);
  return {
    minX,
    maxX: maxX > minX ? maxX : minX + 0.1,
    minY,
    maxY: maxY > minY ? maxY : minY + 0.1,
    minZ,
    maxZ: maxZ > minZ ? maxZ : minZ + 0.1,
  };
}

/**
 * Computes the canonical audit frame. Five percent padding is applied on every
 * side before the frame is expanded to match the viewport aspect ratio.
 */
export function createOrthographicAuditFrame(
  rawBounds: OrthographicAuditBounds,
  rawAspect = 1,
  padding = ORTHOGRAPHIC_AUDIT_PADDING,
): OrthographicAuditFrame {
  const bounds = normalizedBounds(rawBounds);
  const aspect = finite(rawAspect) && rawAspect > 0 ? rawAspect : 1;
  const safePadding = finite(padding) && padding >= 0 ? padding : ORTHOGRAPHIC_AUDIT_PADDING;
  const centerX = (bounds.minX + bounds.maxX) / 2;
  const centerY = (bounds.minY + bounds.maxY) / 2;
  const centerZ = (bounds.minZ + bounds.maxZ) / 2;
  const paddedWidth = Math.max(0.1, bounds.maxX - bounds.minX) * (1 + safePadding * 2);
  const paddedDepth = Math.max(0.1, bounds.maxZ - bounds.minZ) * (1 + safePadding * 2);
  let viewWidth = paddedWidth;
  let viewHeight = paddedDepth;
  if (viewWidth / viewHeight > aspect) viewHeight = viewWidth / aspect;
  else viewWidth = viewHeight * aspect;
  const horizontalSpan = Math.max(bounds.maxX - bounds.minX, bounds.maxZ - bounds.minZ, 0.1);
  const cameraClearance = Math.max(horizontalSpan, bounds.maxY - bounds.minY, 1);
  const cameraY = bounds.maxY + cameraClearance;
  return {
    centerX,
    centerY,
    centerZ,
    cameraY,
    left: -viewWidth / 2,
    right: viewWidth / 2,
    top: viewHeight / 2,
    bottom: -viewHeight / 2,
    near: 0.01,
    far: Math.max(10, cameraY - bounds.minY + cameraClearance),
  };
}

/**
 * The audit camera is always above +Y and looks toward the floor.  CAD +Y is
 * stored as model -Z by the coordinate v2 import, therefore camera-up -Z puts
 * CAD north at screen-up without swapping top/bottom or reflecting projection.
 */
export function createOrthographicCameraContractV2(
  bounds: OrthographicAuditBounds,
  aspect = 1,
): OrthographicCameraContractV2 {
  const frame = createOrthographicAuditFrame(bounds, aspect);
  return {
    schema_version: 2,
    contract: "whole_home_sky_down_orthographic_v2",
    coordinate_system: ORTHOGRAPHIC_CAMERA_COORDINATE_SYSTEM,
    model_coordinate_contract_version: 2,
    projection: "orthographic",
    renderer: "threejs_webgl",
    webgl_capture: true,
    view_direction: [0, -1, 0],
    camera_up: [0, 0, -1],
    screen_right: [1, 0, 0],
    cad_axis_mapping: { cad_x: "+screen_right", cad_y: "+screen_up" },
    eye: [frame.centerX, frame.cameraY, frame.centerZ],
    target: [frame.centerX, frame.centerY, frame.centerZ],
    frustum: {
      left: frame.left, right: frame.right, top: frame.top, bottom: frame.bottom,
      near: frame.near, far: frame.far,
    },
    padding_per_side: ORTHOGRAPHIC_AUDIT_PADDING,
    viewport: [ORTHOGRAPHIC_AUDIT_EXPORT_SIZE, ORTHOGRAPHIC_AUDIT_EXPORT_SIZE],
  };
}

function boundsFromPoints(points: Array<[number, number, number]>): OrthographicAuditBounds | null {
  const valid = points.filter((point) => point.length >= 3 && point.every(finite));
  if (!valid.length) return null;
  return normalizedBounds({
    minX: Math.min(...valid.map((point) => point[0])),
    maxX: Math.max(...valid.map((point) => point[0])),
    minY: Math.min(...valid.map((point) => point[1])),
    maxY: Math.max(...valid.map((point) => point[1])),
    minZ: Math.min(...valid.map((point) => point[2])),
    maxZ: Math.max(...valid.map((point) => point[2])),
  });
}

/** Uses only structural manifest parts so a remote furniture proxy cannot widen the audit frame. */
export function geometryManifestAuditBounds(manifest?: WholeHomeGeometryManifest | null): OrthographicAuditBounds | null {
  if (!manifest?.vertices?.length) return null;
  const structuralParts = [
    ...(manifest.wall_parts || []),
    ...(manifest.floor_parts || []),
    ...(manifest.ceiling_parts || []),
  ];
  const indices = new Set<number>();
  structuralParts.forEach((part) => part.indices.forEach((index) => {
    if (Number.isInteger(index) && index >= 0 && index < manifest.vertices.length) indices.add(index);
  }));
  const vertices = (indices.size ? [...indices].map((index) => manifest.vertices[index]) : manifest.vertices)
    .filter((point): point is [number, number, number] => Boolean(point));
  return boundsFromPoints(vertices);
}

/** Fallback for editable/manual models that do not have a locked GeometryManifest yet. */
export function wholeHomeModelAuditBounds(model: WholeHomeModel): OrthographicAuditBounds {
  const points: Array<[number, number, number]> = [];
  const addPlanPoint = (x: number, z: number, minY = 0, maxY = model.wall_height_m) => {
    if (!finite(x) || !finite(z)) return;
    points.push([x, minY, z], [x, maxY, z]);
  };
  (model.global_wall_footprints || []).forEach((footprint) => {
    const minY = footprint.floor_elevation_m || 0;
    const maxY = minY + Math.max(0.1, footprint.height_m || model.wall_height_m);
    footprint.points.forEach((point) => addPlanPoint(point.x, point.z, minY, maxY));
    footprint.interior_rings.forEach((ring) => ring.forEach((point) => addPlanPoint(point.x, point.z, minY, maxY)));
  });
  model.walls.forEach((wall) => {
    const halfThickness = Math.max(0, wall.thickness_m || model.wall_thickness_m) / 2;
    [wall.start, wall.end].forEach((point) => {
      addPlanPoint(point.x - halfThickness, point.z - halfThickness, 0, wall.height_m);
      addPlanPoint(point.x + halfThickness, point.z + halfThickness, 0, wall.height_m);
    });
  });
  model.rooms.forEach((room) => room.polygon.forEach((point) => {
    addPlanPoint(point.x, point.z, room.floor_elevation_m, room.floor_elevation_m + room.ceiling_height_m);
  }));
  return boundsFromPoints(points) || normalizedBounds({
    minX: 0,
    maxX: Math.max(0.1, model.width_m),
    minY: 0,
    maxY: Math.max(0.1, model.wall_height_m),
    minZ: 0,
    maxZ: Math.max(0.1, model.depth_m),
  });
}

export function resolveWholeHomeAuditBounds(
  model: WholeHomeModel,
  manifest?: WholeHomeGeometryManifest | null,
): OrthographicAuditBounds {
  return geometryManifestAuditBounds(manifest) || wholeHomeModelAuditBounds(model);
}
