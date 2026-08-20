import type {
  MetricXZ,
  WholeHomeCadPhysicalSpace,
  WholeHomeCadRawFace,
  WholeHomeCadSemanticZone,
  WholeHomeCadSpaceDraft,
  WholeHomeCadSpaceDraftPut,
  WholeHomeCadTextAnchor,
} from "./types";

const SPACE_COLORS = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#dc2626"];

export function cadFaceId(face: WholeHomeCadRawFace, index = 0): string {
  return String(face.face_id || face.id || `face-${index + 1}`);
}

export function cadFacePolygon(face: WholeHomeCadRawFace): MetricXZ[] {
  return (Array.isArray(face.polygon) ? face.polygon : Array.isArray(face.points) ? face.points : [])
    .filter((point): point is MetricXZ => Number.isFinite(point?.x) && Number.isFinite(point?.z));
}

export function cadAnchorPoint(anchor: WholeHomeCadTextAnchor): MetricXZ | null {
  const point = anchor.point || anchor.position;
  return point && Number.isFinite(point.x) && Number.isFinite(point.z) ? point : null;
}

export function cadSpaceColor(id: string): string {
  let hash = 0;
  for (const char of id) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return SPACE_COLORS[Math.abs(hash) % SPACE_COLORS.length];
}

function geometryPoints(zone: WholeHomeCadSemanticZone): MetricXZ[] {
  const geometry = zone.geometry || { kind: "polygon" };
  if (Array.isArray(geometry.polygon)) return geometry.polygon;
  if (Array.isArray(geometry.points)) return geometry.points;
  if ([geometry.min_x, geometry.min_z, geometry.max_x, geometry.max_z].every(Number.isFinite)) {
    const minX = Number(geometry.min_x);
    const minZ = Number(geometry.min_z);
    const maxX = Number(geometry.max_x);
    const maxZ = Number(geometry.max_z);
    return [{ x: minX, z: minZ }, { x: maxX, z: minZ }, { x: maxX, z: maxZ }, { x: minX, z: maxZ }];
  }
  return [geometry.start, geometry.end].filter((point): point is MetricXZ => Boolean(point && Number.isFinite(point.x) && Number.isFinite(point.z)));
}

export function cadZonePoints(zone: WholeHomeCadSemanticZone): MetricXZ[] {
  return geometryPoints(zone);
}

export interface CadDraftBounds { minX: number; minZ: number; maxX: number; maxZ: number; width: number; height: number }

export function cadDraftBounds(draft: WholeHomeCadSpaceDraft): CadDraftBounds {
  const points = [
    ...draft.raw_faces.flatMap(cadFacePolygon),
    ...draft.physical_spaces.flatMap((space) => space.polygon || []),
    ...draft.semantic_zones.flatMap(geometryPoints),
    ...draft.text_anchors.map(cadAnchorPoint).filter((point): point is MetricXZ => point !== null),
  ];
  if (!points.length) return { minX: 0, minZ: 0, maxX: 10, maxZ: 10, width: 10, height: 10 };
  const minX = Math.min(...points.map((point) => point.x));
  const minZ = Math.min(...points.map((point) => point.z));
  const maxX = Math.max(...points.map((point) => point.x));
  const maxZ = Math.max(...points.map((point) => point.z));
  const width = Math.max(maxX - minX, 0.1);
  const height = Math.max(maxZ - minZ, 0.1);
  const padding = Math.max(width, height) * 0.04;
  return {
    minX: minX - padding,
    minZ: minZ - padding,
    maxX: maxX + padding,
    maxZ: maxZ + padding,
    width: width + padding * 2,
    height: height + padding * 2,
  };
}

export function updateSpaceSelection(
  draft: WholeHomeCadSpaceDraft,
  spaceId: string,
  selected: boolean,
): WholeHomeCadSpaceDraft {
  const target = draft.physical_spaces.find((space) => space.id === spaceId);
  if (!target) return draft;
  const targetFaces = new Set(target.face_ids);
  const excluded = new Set(draft.excluded_face_ids);
  for (const faceId of targetFaces) {
    if (selected) excluded.delete(faceId);
    else excluded.add(faceId);
  }
  if (!selected) {
    return {
      ...draft,
      physical_spaces: draft.physical_spaces.filter((space) => space.id !== spaceId),
      semantic_zones: draft.semantic_zones.filter((zone) => zone.physical_space_id !== spaceId),
      excluded_face_ids: [...excluded].sort(),
    };
  }
  return {
    ...draft,
    physical_spaces: draft.physical_spaces.map((space) => space.id === spaceId ? { ...space, selected } : space),
    excluded_face_ids: [...excluded].sort(),
  };
}

export function retainCadFace(draft: WholeHomeCadSpaceDraft, faceId: string): WholeHomeCadSpaceDraft {
  const faceIndex = draft.raw_faces.findIndex((face, index) => cadFaceId(face, index) === faceId);
  const face = draft.raw_faces[faceIndex];
  if (!face || face.manual_eligible !== true) return draft;
  if (draft.physical_spaces.some((space) => space.face_ids.includes(faceId))) return draft;
  const polygon = cadFacePolygon(face);
  if (polygon.length < 3) return draft;
  const suffix = faceId.replace(/[^a-zA-Z0-9_-]/g, "-").slice(-48);
  let spaceId = `physical_${suffix}`;
  let sequence = 2;
  while (draft.physical_spaces.some((space) => space.id === spaceId)) spaceId = `physical_${suffix}_${sequence++}`;
  let zoneId = `zone_${suffix}`;
  sequence = 2;
  while (draft.semantic_zones.some((zone) => zone.id === zoneId)) zoneId = `zone_${suffix}_${sequence++}`;
  const excluded = new Set(draft.excluded_face_ids);
  excluded.delete(faceId);
  return {
    ...draft,
    physical_spaces: [...draft.physical_spaces, {
      id: spaceId, label: `保留空间 ${draft.physical_spaces.length + 1}`, space_type: "enclosed_room",
      face_ids: [faceId], polygon, selected: true,
    }],
    semantic_zones: [...draft.semantic_zones, {
      id: zoneId, physical_space_id: spaceId, label: "待确认房间", zone_type: "other",
      geometry: { kind: "polygon", points: polygon },
    }],
    excluded_face_ids: [...excluded].sort(),
  };
}

/** Merge only changes face ownership; the server remains authoritative for polygon normalization. */
export function mergePhysicalSpaces(draft: WholeHomeCadSpaceDraft, spaceIds: string[]): WholeHomeCadSpaceDraft {
  const wanted = new Set(spaceIds);
  const spaces = draft.physical_spaces.filter((space) => wanted.has(space.id));
  if (spaces.length < 2) return draft;
  const leader = spaces[0];
  const mergedFaces = [...new Set(spaces.flatMap((space) => space.face_ids))].sort();
  const merged: WholeHomeCadPhysicalSpace = {
    ...leader,
    label: spaces.map((space) => space.label).filter(Boolean).join(" + ") || leader.label,
    face_ids: mergedFaces,
    // Empty means "derive the authoritative union from face_ids".  Sending
    // the leader's old polygon would falsely claim it represents the merge.
    polygon: [],
    selected: true,
  };
  const excluded = new Set(draft.excluded_face_ids);
  mergedFaces.forEach((faceId) => excluded.delete(faceId));
  return {
    ...draft,
    physical_spaces: [merged, ...draft.physical_spaces.filter((space) => !wanted.has(space.id))],
    semantic_zones: draft.semantic_zones.map((zone) => wanted.has(zone.physical_space_id) ? { ...zone, physical_space_id: leader.id } : zone),
    excluded_face_ids: [...excluded].sort(),
  };
}

export function newCadOperationId(prefix = "cad-space"): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

/** Strip GET-only provenance fields because the PUT schema forbids extras. */
export function buildCadSpaceDraftPut(
  draft: WholeHomeCadSpaceDraft,
  operationId: string,
  editorId = "local-user",
): WholeHomeCadSpaceDraftPut {
  return {
    base_revision: draft.revision,
    base_state_hash: draft.state_hash,
    operation_id: operationId,
    editor_id: editorId,
    physical_spaces: draft.physical_spaces.map((space) => ({
      id: space.id,
      label: space.label,
      space_type: space.space_type,
      face_ids: [...space.face_ids],
      polygon: (space.polygon || []).map((point) => ({ x: point.x, z: point.z })),
      selected: space.selected,
    })),
    semantic_zones: draft.semantic_zones.map((zone) => {
      const geometry = zone.geometry;
      const exactGeometry: WholeHomeCadSemanticZone["geometry"] = geometry.kind === "rectangle"
        ? {
          kind: "rectangle", min_x: geometry.min_x, min_z: geometry.min_z,
          max_x: geometry.max_x, max_z: geometry.max_z,
        }
        : geometry.kind === "split_halfplane"
          ? { kind: "split_halfplane", start: geometry.start, end: geometry.end, side: geometry.side }
          : { kind: "polygon", points: (geometry.points || geometry.polygon || []).map((point) => ({ x: point.x, z: point.z })) };
      return {
        id: zone.id,
        physical_space_id: zone.physical_space_id,
        label: zone.label,
        zone_type: zone.zone_type,
        geometry: exactGeometry,
      };
    }),
    excluded_face_ids: [...draft.excluded_face_ids],
  };
}

export function describeCadApiError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error || "未知错误");
  try {
    const parsed = JSON.parse(message) as Record<string, unknown>;
    const detail = typeof parsed.detail === "object" && parsed.detail ? parsed.detail as Record<string, unknown> : parsed;
    const code = String(detail.code || detail.error || "请求失败");
    const text = String(detail.message || detail.detail || "");
    const revision = detail.current_revision == null ? "" : `；服务器当前版本 ${detail.current_revision}`;
    return `${code}${text ? `：${text}` : ""}${revision}`;
  } catch {
    return message;
  }
}
