import type {
  CadRuntimeStatus,
  WholeHomeCapture,
  WholeHomeCaptureGroup,
  WholeHomeProject,
  WholeHomeReferenceContract,
  WholeHomeReferenceSlot,
} from "./types";

export const DEFAULT_JUSTEASY_REFERENCE_URL =
  "https://vr.justeasy.cn/view/16770314h7u0u192-1773594850.html";

export type WholeHomeSourceMode = "image" | "cad";
export type WholeHomeMaterialMode = "floor_sample" | "reference" | "style_pack";

export function switchWholeHomeSource<TPlan, TCad>(
  mode: WholeHomeSourceMode,
  current: { plan: TPlan | null; cad: TCad | null },
): { mode: WholeHomeSourceMode; plan: TPlan | null; cad: TCad | null } {
  return mode === "cad"
    ? { mode, plan: null, cad: current.cad }
    : { mode, plan: current.plan, cad: null };
}

export function cadFormatReadiness(
  status: CadRuntimeStatus | null | undefined,
  format: "dwg" | "dxf" | "",
): { ready: boolean; code: string; message: string } {
  if (!status) return { ready: false, code: "cad_status_loading", message: "正在读取本机 CAD 运行环境" };
  if (!format) return { ready: false, code: "cad_file_missing", message: "请先上传 DWG 或 DXF" };
  if (!status.ready_for_dxf) {
    return {
      ready: false,
      code: "cad_parser_missing",
      message: "本地 CAD 解析器尚未就绪，需要 ezdxf 与 Shapely。",
    };
  }
  if (format === "dxf") {
    return { ready: true, code: "ready_for_dxf", message: "DXF 可直接进入本地解析" };
  }
  if (!status.converter_available) {
    return {
      ready: false,
      code: "cad_converter_missing",
      message: "未检测到 DWG 转换器；请使用已获授权的 CAD 软件导出 DXF 后上传。",
    };
  }
  if (!status.commercial_use_authorized) {
    return {
      ready: false,
      code: "cad_commercial_authorization_missing",
      message: "已检测到 DWG 转换器，但未声明商业使用授权；请使用已获授权的 CAD 软件导出 DXF 后上传。",
    };
  }
  if (!status.ready_for_dwg) {
    return {
      ready: false,
      code: "cad_dwg_not_ready",
      message: "DWG 链路尚未就绪，请改用已获授权软件导出的 DXF。",
    };
  }
  return { ready: true, code: "ready_for_dwg", message: "DWG 转换、商业授权与本地解析器均已就绪" };
}

export function canMutateWholeHomeGeometry(sourceType: WholeHomeProject["source_type"]): boolean {
  return sourceType !== "cad";
}

function roomForCapture(project: WholeHomeProject, capture: WholeHomeCapture) {
  const roomId = capture.room_id || capture.camera.room_id;
  return project.model?.rooms?.find((room) => room.id === roomId);
}

function hasCompleteMustShowBounds(
  contract: WholeHomeReferenceContract,
  slot: WholeHomeReferenceSlot,
  capture: WholeHomeCapture,
) {
  const evidence = capture.camera.reference_contract_validation;
  if (evidence?.pixel_gate_version !== "whole-home-subject-pixel-gate-v2"
    || capture.camera.render_gate?.version !== "whole-home-reference-render-gate-v3-software") return false;
  if (!evidence
    || evidence.projection_method !== "backend_2d_fov_los"
    || evidence.slot_id !== slot.slot_id
    || evidence.pixel_origin !== "top-left"
    || !evidence.buffer_sha
    || !evidence.proposal_id
    || !evidence.proposal_hash
    || evidence.safe_frame_status !== "pass"
    || evidence.safe_frame_pass !== true) return false;
  const viewpoint = slot.reference_viewpoint;
  if (!viewpoint
    || (viewpoint.point_mapping?.status || viewpoint.point_mapping_status) !== "not_available"
    || viewpoint.landing_policy?.mode !== "cad_semantic_relative_region"
    || evidence.scene_id !== String(viewpoint.scene_id)
    || evidence.landing_policy_mode !== "cad_semantic_relative_region"
    || evidence.landing_source !== "inferred_from_reference_visual_and_cad_anchors"
    || evidence.cad_position_pass !== true
    || evidence.collision_pass !== true
    || evidence.visibility_pass !== true) return false;
  const required = (evidence.must_show_subjects || [])
    .map((row) => row.subject)
    .filter(Boolean)
    .sort();
  const bounds = evidence.must_show_bounds || [];
  const observed = bounds.map((row) => row.subject).sort();
  if (!required.length || required.length !== observed.length
    || required.some((value, index) => value !== observed[index])) return false;
  const safe = contract.camera.safe_frame;
  return bounds.every((bound) => {
    const frame = slot.subject_safe_frame_overrides?.[bound.subject] || safe;
    return bound.x_min >= frame.x_min
      && bound.x_max <= frame.x_max
      && bound.y_min >= frame.y_min
      && bound.y_max <= frame.y_max
      && bound.x_min < bound.x_max
      && bound.y_min < bound.y_max;
  });
}

function referenceCaptureMatches(
  project: WholeHomeProject,
  contract: WholeHomeReferenceContract,
  slot: WholeHomeReferenceSlot,
  capture: WholeHomeCapture,
) {
  if (capture.status !== "confirmed" || capture.aspect_ratio !== contract.output.aspect_ratio) return false;
  const captureSlot = capture.reference_slot_id || capture.camera.reference_slot_id || "";
  if (captureSlot !== slot.slot_id) return false;
  const room = roomForCapture(project, capture);
  if (!room || room.reference_room_profile !== slot.room_profile) return false;
  const focal = capture.camera.focal_length_mm;
  if (focal < slot.focal_length_mm.min || focal > slot.focal_length_mm.max) return false;
  const eye = capture.camera.position.y;
  if (eye < contract.camera.eye_height_m.min || eye > contract.camera.eye_height_m.max) return false;
  const dx = capture.camera.target.x - capture.camera.position.x;
  const dz = capture.camera.target.z - capture.camera.position.z;
  const horizontal = Math.max(1e-9, Math.hypot(dx, dz));
  const vertical = Math.abs(Math.atan2(capture.camera.target.y - eye, horizontal) * 180 / Math.PI);
  if (vertical > contract.camera.vertical_deviation_deg_max) return false;
  return hasCompleteMustShowBounds(contract, slot, capture);
}

export interface ReferenceCaptureGate {
  ready: boolean;
  code: "ready" | "reference_contract_missing" | "reference_slot_asset_missing" | "reference_viewpoint_landing_missing" | "reference_slot_camera_missing";
  message: string;
  activeSlots: number;
  estimatedResults: number;
  missingSlotIds: string[];
  missingAssetSlotIds: string[];
  missingViewpointSlotIds: string[];
  captureGroups: WholeHomeCaptureGroup[];
}

export interface ReferencePreflightGate {
  ready: boolean;
  code: "ready" | "reference_contract_missing" | "reference_project_unverified" | "reference_slot_asset_missing" | "reference_viewpoint_landing_missing" | "reference_cad_anchor_blocked";
  message: string;
  missingAssetSlotIds: string[];
  missingViewpointSlotIds: string[];
  anchorErrors: Array<Record<string, unknown> & { code?: string; message?: string }>;
}

export function buildReferencePreflightGate(project: WholeHomeProject | null | undefined): ReferencePreflightGate {
  const contract = project?.reference_contract;
  const fallback = { missingAssetSlotIds: [], missingViewpointSlotIds: [], anchorErrors: [] };
  if (!project || project.source_type !== "cad" || !contract?.contract_id || contract.slots.length !== 9) {
    return { ready: false, code: "reference_contract_missing", message: "当前项目没有完整的 9-slot CAD reference contract", ...fallback };
  }
  if (!project.verified) {
    return { ready: false, code: "reference_project_unverified", message: "请先锁定当前 CAD 几何与语义事实", ...fallback };
  }
  const missingAssets = contract.slots.filter((slot) => {
    const asset = slot.reference_asset;
    return !asset || asset.status !== "verified" || !asset.url || !(slot.reference_asset_hash || asset.sha256 || asset.hash);
  }).map((slot) => slot.slot_id);
  const missingViewpoints = contract.slots.filter((slot) => {
    const viewpoint = slot.reference_viewpoint;
    return !viewpoint || !viewpoint.scene_id
      || (viewpoint.point_mapping?.status || viewpoint.point_mapping_status) !== "not_available"
      || viewpoint.landing_policy?.mode !== "cad_semantic_relative_region"
      || viewpoint.landing_policy?.source !== "inferred_from_reference_visual_and_cad_anchors";
  }).map((slot) => slot.slot_id);
  const anchorReport = project.model.reference_anchor_report;
  const anchorErrors = anchorReport?.hard_errors || [];
  const ready = !missingAssets.length && !missingViewpoints.length
    && anchorReport?.status === "ready" && !anchorErrors.length;
  const code = missingAssets.length ? "reference_slot_asset_missing"
    : missingViewpoints.length ? "reference_viewpoint_landing_missing"
      : anchorReport?.status !== "ready" || anchorErrors.length ? "reference_cad_anchor_blocked" : "ready";
  return {
    ready, code,
    message: ready ? "Reference 本地预检通过，可自动生成 9-slot CPU 灰模证据"
      : missingAssets.length ? `缺少已校验 reference 资产：${missingAssets.join("、")}`
        : missingViewpoints.length ? `缺少合法 scene/relative landing：${missingViewpoints.join("、")}`
          : `CAD anchor 未就绪：${anchorErrors.map((row) => row.code || row.message).join("、") || "reference_anchor_report blocked"}`,
    missingAssetSlotIds: missingAssets, missingViewpointSlotIds: missingViewpoints, anchorErrors,
  };
}

export function buildReferenceCaptureGate(project: WholeHomeProject | null | undefined): ReferenceCaptureGate {
  const contract = project?.reference_contract;
  if (!project || project.source_type !== "cad" || !contract?.contract_id || !contract.slots?.length) {
    return {
      ready: false,
      code: "reference_contract_missing",
      message: "当前项目没有已审计的 CAD reference contract",
      activeSlots: contract?.slots?.length || 0,
      estimatedResults: (contract?.slots?.length || 0) * 2,
      missingSlotIds: contract?.slots?.map((slot) => slot.slot_id) || [],
      missingAssetSlotIds: contract?.slots?.map((slot) => slot.slot_id) || [],
      missingViewpointSlotIds: contract?.slots?.map((slot) => slot.slot_id) || [],
      captureGroups: [],
    };
  }
  const groups: WholeHomeCaptureGroup[] = [];
  const missing: string[] = [];
  const missingAssets = contract.slots.filter((slot) => {
    const asset = slot.reference_asset;
    const hash = slot.reference_asset_hash || asset?.sha256 || asset?.hash || "";
    return !asset || asset.status !== "verified" || !asset.url || !hash;
  }).map((slot) => slot.slot_id);
  const missingViewpoints = contract.slots.filter((slot) => (
    !slot.reference_viewpoint
    || (slot.reference_viewpoint.point_mapping?.status || slot.reference_viewpoint.point_mapping_status) !== "not_available"
    || !slot.reference_viewpoint.scene_id
    || !(slot.reference_viewpoint.point_mapping?.evidence?.length || slot.reference_viewpoint.evidence)
    || slot.reference_viewpoint.landing_policy?.mode !== "cad_semantic_relative_region"
    || slot.reference_viewpoint.landing_policy?.source !== "inferred_from_reference_visual_and_cad_anchors"
  )).map((slot) => slot.slot_id);
  for (const slot of contract.slots) {
    const matches = (project.captures || [])
      .filter((capture) => referenceCaptureMatches(project, contract, slot, capture))
      .sort((left, right) => Number(right.is_primary) - Number(left.is_primary) || left.pool_rank - right.pool_rank);
    const primary = matches.find((capture) => capture.is_primary) || matches[0];
    if (!primary) {
      missing.push(slot.slot_id);
      continue;
    }
    groups.push({
      room_id: primary.room_id || primary.camera.room_id,
      slot_id: slot.slot_id,
      primary_capture_id: primary.capture_id,
      fallback_capture_ids: matches
        .filter((capture) => capture.capture_id !== primary.capture_id)
        .slice(0, 2)
        .map((capture) => capture.capture_id),
    });
  }
  const ready = missing.length === 0 && missingAssets.length === 0 && missingViewpoints.length === 0 && groups.length === contract.slots.length;
  const code = missingAssets.length ? "reference_slot_asset_missing"
    : missingViewpoints.length ? "reference_viewpoint_landing_missing"
    : ready ? "ready" : "reference_slot_camera_missing";
  return {
    ready,
    code,
    message: ready
      ? `9-slot 本地机位证据完整，可提交 ${contract.slots.length * 2} 个 B2 / Pro 逻辑结果`
      : missingAssets.length
        ? `reference_slot_asset_missing：${missingAssets.join("、")} 的视觉参考资产待审计绑定`
        : missingViewpoints.length
          ? `reference_viewpoint_landing_missing：${missingViewpoints.join("、")} 缺少 CAD 语义相对落点策略`
        : `reference_slot_camera_missing：${missing.join("、") || "slot 绑定不完整"}`,
    activeSlots: contract.slots.length,
    estimatedResults: contract.slots.length * 2,
    missingSlotIds: missing,
    missingAssetSlotIds: missingAssets,
    missingViewpointSlotIds: missingViewpoints,
    captureGroups: groups,
  };
}

export function materialModeGate(args: {
  mode: WholeHomeMaterialMode;
  floorPath?: string;
  referenceGate: ReferenceCaptureGate;
  sceneReady?: boolean;
}) {
  if (args.mode === "floor_sample" && !args.floorPath) {
    return { ready: false, code: "floor_sample_missing", message: "地板小样模式必须先上传产品图片" };
  }
  if (args.mode === "reference" && !args.referenceGate.ready) {
    return { ready: false, code: args.referenceGate.code, message: args.referenceGate.message };
  }
  if (args.mode === "style_pack" && !args.sceneReady) {
    return { ready: false, code: "locked_scene_recipe_required", message: "请先复核并锁定一个整屋 SceneRecipe" };
  }
  return { ready: true, code: "ready", message: "生成输入已就绪" };
}
