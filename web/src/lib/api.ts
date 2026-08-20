// server_api.py 的 typed 客户端。base 走 NEXT_PUBLIC_API_BASE（.env.local）。
import type {
  JobView,
  JobSubmit,
  FreeJobSubmit,
  EditRequest,
  Swatch,
  ConfigView,
  ConfigPatch,
  ModelsView,
  OptionsView,
  Recipe,
  RecordEntry,
  RecordFile,
  ResultReviewPatch,
  FailureKB,
  UsageSummary,
  FloorAnalyze,
  RecordEditRequest,
  PreviewRequest,
  PreviewView,
  OmakaseScenesResponse,
  CustomRecipe,
  GenParams,
  ReviewSummary,
  ReviewGalleryItem,
  ColorMatchPreviewRequest,
  ColorMatchPreviewView,
  JobColorMatchRequest,
  RecordColorMatchRequest,
  ColorMatchSegmentRequest,
  ColorMatchSegmentView,
  ModelKey,
  JobSlotKey,
  ModelRunCandidate,
  PanoramaPaidPreview,
  DirectPanoramaPaidPreview,
  FilmRepeatContract,
  PureRenderPanoramaReviewChecklist,
  PanoramaFloorPrepareResponse,
  PanoramaFloorPreviewResponse,
  PanoramaFloorRenderRequest,
  PanoramaFloorRecordRenderRequest,
  PanoramaFloorRecordTarget,
  GenericInpaintRequest,
  InpaintSegmentRequest,
  InpaintSegmentView,
  InpaintSubmitView,
  InpaintStatusView,
  InpaintApplyResponse,
  ComfyUIPingView,
  FloorVisualizeRequest,
  FloorVisualizePreview,
  FloorVisualizeApplyResponse,
  FloorplanUpload,
  FloorplanAnalysis,
  FloorplanRoom,
  FloorplanOpening,
  FloorplanSuite,
  FloorplanSuiteSubmit,
  SuiteColorMatchRequest,
  FloorplanDatasetSummary,
  FloorplanOperation,
  FloorplanSpatialPlan,
  WholeHomeProject,
  WholeHomeAutoCameraPlan,
  WholeHomeCameraCandidate,
  WholeHomeCameraCandidateProposal,
  WholeHomeCameraRoomPool,
  WholeHomeModel,
  WholeHomeCamera,
  WholeHomeCaptureGroup,
  WholeHomeHumanReviewStatus,
  WholeHomeLearningSummary,
  WholeHomeReviewMutationResponse,
  WholeHomeReviewState,
  WholeHomeRun,
  WholeHomeManualCapabilities,
  WholeHomeManualRunPreview,
  WholeHomeTrainingConsent,
  CadRuntimeStatus,
  CadUpload,
  WholeHomeCadReparseOperation,
  WholeHomeCadAiAdvisory,
  WholeHomeCadSpaceDraft,
  WholeHomeCadSpaceDraftPut,
  WholeHomeCadSpaceDraftPutResponse,
  WholeHomePanoGate,
  WholeHomePanoPaidPreview,
  WholeHomeGeometryContract,
  WholeHomeGeometryManifest,
  WholeHomeGenerationDraft,
  WholeHomeProjectHistory,
  WholeHomeRunReplay,
  WholeHomeVariantBatch,
  WholeHomeProfessionalCapabilities,
  WholeHomeFloorplanGraph,
  WholeHomeConstructionProfile,
  WholeHomeSceneRecipe,
  WholeHomeMarketingProposal,
} from "./types";

// dev（next dev 在 :3000）用 .env.local 里的 NEXT_PUBLIC_API_BASE 指到后端 :7870；
// 生产静态导出由后端同源托管，走空 base → 相对地址（window.location.origin），
// 这样无论客户从 localhost 还是局域网 IP 打开都能命中后端。
export const API =
  process.env.NEXT_PUBLIC_API_BASE ||
  (typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:7870");

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      const raw = j && (j.detail ?? j);
      detail = typeof raw === "string" ? raw : JSON.stringify(raw);
    } catch {
      /* 非 JSON 错误体，保留状态码 */
    }
    throw new Error(detail);
  }
  return (await r.json()) as T;
}

const jget = <T>(p: string) => fetch(API + p).then((r) => handle<T>(r));

const jsend = <T>(p: string, method: "POST" | "PUT", body?: unknown, signal?: AbortSignal) =>
  fetch(API + p, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  }).then((r) => handle<T>(r));

async function upload<T = Swatch>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(API + path, { method: "POST", body: fd }).then((r) =>
    handle<T>(r),
  );
}

export const api = {
  /** 把后端给的相对 URL(/outputs.. /thumb..)拼成可用的绝对地址 */
  imgUrl: (p: string) => (!p ? "" : p.startsWith("http") ? p : API + p),
  health: () => jget<{ ok: boolean }>("/api/healthz"),

  uploadFloor: (f: File) => upload("/api/uploads/floor", f),
  uploadFilm: (f: File) => upload("/api/uploads/film", f),
  analyzeFilm: (body: {
    film_path: string;
    film_width_mm: number;
    film_repeat_length_mm: number;
    floor_size: string;
    seam_type: string;
    film_slit_origin_mm?: number | null;
  }) => jsend<FilmRepeatContract & { guide: string }>("/api/film/analyze", "POST", body),
  uploadRoom: (f: File) => upload("/api/uploads/room", f),
  uploadRef: (f: File) => upload("/api/uploads/ref", f),
  uploadFloorplan: (f: File) => upload("/api/uploads/floorplan", f) as Promise<FloorplanUpload>,
  uploadCad: (f: File) => upload<CadUpload>("/api/uploads/cad", f),

  analyzeFloorplan: (floorplan_path: string) =>
    jsend<FloorplanAnalysis>("/api/floorplans/analyze", "POST", { floorplan_path }),
  createManualFloorplan: (floorplan_path: string) =>
    jsend<FloorplanAnalysis>("/api/floorplans/manual", "POST", { floorplan_path }),
  getFloorplanAnalysis: (id: string) =>
    jget<FloorplanAnalysis>(`/api/floorplans/${id}`),
  listFloorplanAnalyses: (limit = 50) =>
    jget<FloorplanAnalysis[]>(`/api/floorplans?limit=${limit}`),
  confirmFloorplan: (
    id: string,
    body: { rooms: FloorplanRoom[]; openings: FloorplanOpening[]; entrance: { x: number; y: number } | null; orientation: string },
  ) => jsend<FloorplanAnalysis>(`/api/floorplans/${id}`, "PUT", body),
  saveFloorplanDraft: (
    id: string,
    body: {
      base_revision: number;
      rooms: FloorplanRoom[];
      openings: FloorplanOpening[];
      openings_review_status: "pending" | "confirmed";
      entrance: { x: number; y: number } | null;
      orientation: string;
      operations: FloorplanOperation[];
      annotator_id: string;
    },
  ) => jsend<FloorplanAnalysis>(`/api/floorplans/${id}/draft`, "PUT", body),
  verifyFloorplan: (
    id: string,
    body: { base_revision: number; training_consent: boolean; acknowledged_warning_codes: string[]; annotator_id: string },
  ) => jsend<FloorplanAnalysis>(`/api/floorplans/${id}/verify`, "POST", body),
  generateFloorplanSpatialPlan: (id: string, room_id: string, camera_id: string) =>
    jsend<FloorplanAnalysis>(`/api/floorplans/${id}/spatial-plans/generate`, "POST", { room_id, camera_id }),
  updateFloorplanSpatialPlan: (
    id: string,
    cameraId: string,
    plan: FloorplanSpatialPlan,
    status: "draft" | "locked",
  ) => jsend<FloorplanAnalysis>(`/api/floorplans/${id}/spatial-plans/${encodeURIComponent(cameraId)}`, "PUT", {
    space_summary: plan.space_summary,
    camera_view: plan.camera_view,
    architecture: plan.architecture,
    zones: plan.zones,
    furniture: plan.furniture,
    hard_constraints: plan.hard_constraints,
    must_not_appear: plan.must_not_appear,
    uncertainties: plan.uncertainties,
    status,
    annotator_id: "local-user",
  }),
  confirmFloorplanViewProxy: (
    id: string,
    cameraId: string,
    body: {
      image_data_url: string;
      aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
      render_config: Record<string, number | string>;
      annotator_id: string;
    },
  ) => jsend<FloorplanAnalysis>(`/api/floorplans/${id}/view-proxies/${encodeURIComponent(cameraId)}/confirm`, "POST", body),
  floorplanHistory: (id: string, limit = 500) =>
    jget<{ analysis_id: string; operations: Array<Record<string, unknown>> }>(`/api/floorplans/${id}/history?limit=${limit}`),
  setFloorplanTrainingConsent: (id: string, allowed: boolean, annotator_id = "local-user") =>
    jsend<FloorplanAnalysis>(`/api/floorplans/${id}/training-consent`, "POST", { allowed, annotator_id }),
  floorplanDatasetSummary: () => jget<FloorplanDatasetSummary>("/api/floorplan-dataset/summary"),
  floorplanDatasetExportUrl: () => `${process.env.NEXT_PUBLIC_API_BASE || ""}/api/floorplan-dataset/export`,
  createFloorplanSuite: (body: FloorplanSuiteSubmit) =>
    jsend<FloorplanSuite>("/api/floorplan-suites", "POST", body),
  listFloorplanSuites: (limit = 30) =>
    jget<FloorplanSuite[]>(`/api/floorplan-suites?limit=${limit}`),
  getFloorplanSuite: (id: string) =>
    jget<FloorplanSuite>(`/api/floorplan-suites/${id}`),
  selectFloorplanAnchor: (id: string, result_id: string) =>
    jsend<FloorplanSuite>(`/api/floorplan-suites/${id}/anchor`, "POST", { result_id }),
  cancelFloorplanSuite: (id: string) =>
    jsend<{ cancelled: boolean }>(`/api/floorplan-suites/${id}/cancel`, "POST"),
  retryFloorplanRoom: (id: string, roomId: string) =>
    jsend<FloorplanSuite>(`/api/floorplan-suites/${id}/rooms/${encodeURIComponent(roomId)}/retry`, "POST"),
  reviewFloorplanCandidate: (
    suiteId: string,
    body: { room_id: string; result_id: string; review_status: "unreviewed" | "pass" | "backup" | "reject"; review_tags: string[]; review_note: string; best: boolean },
  ) => jsend<FloorplanSuite>(`/api/floorplan-suites/${suiteId}/results/${body.result_id}/review`, "POST", body),

  // ── Whole-home v2: shared metric shell -> 3D captures -> constrained renders ──
  getWholeHomeCadStatus: () => jget<CadRuntimeStatus>("/api/whole-home/cad/status"),
  createWholeHomeProject: (body: {
    floorplan_path?: string;
    import_analysis_id?: string;
    cad_path?: string;
    reference_url?: string;
    width_m?: number;
  }) =>
    jsend<WholeHomeProject>("/api/whole-home/projects", "POST", body),
  listWholeHomeProjects: (limit = 30) =>
    jget<WholeHomeProject[]>(`/api/whole-home/projects?limit=${limit}`),
  getWholeHomeProject: (id: string) =>
    jget<WholeHomeProject>(`/api/whole-home/projects/${encodeURIComponent(id)}`),
  getWholeHomeProfessionalCapabilities: () =>
    jget<WholeHomeProfessionalCapabilities>("/api/whole-home/professional/capabilities"),
  getWholeHomeFloorplanGraph: (id: string) =>
    jget<WholeHomeFloorplanGraph>(`/api/whole-home/projects/${encodeURIComponent(id)}/floorplan-graph`),
  getWholeHomeConstructionProfile: (id: string) =>
    jget<WholeHomeConstructionProfile>(`/api/whole-home/projects/${encodeURIComponent(id)}/construction-profile`),
  confirmWholeHomeConstructionProfile: (id: string, body: {
    base_revision: number; base_state_hash?: string; operation_id: string;
    reviewer: string; values: Record<string, number>;
  }) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/construction-profile`, "PUT", body),
  listWholeHomeSceneRecipes: (id: string) =>
    jget<{ project_id: string; professional_revision: number; active_scene_recipe_id: string; recipes: WholeHomeSceneRecipe[] }>(
      `/api/whole-home/projects/${encodeURIComponent(id)}/scene-recipes`),
  previewWholeHomeSceneRecipe: (id: string, variant_index: 1 | 2 | 3) =>
    jsend<WholeHomeSceneRecipe>(
      `/api/whole-home/projects/${encodeURIComponent(id)}/scene-recipes/preview`, "POST", { variant_index }),
  createWholeHomeSceneRecipe: (id: string, body: {
    base_revision: number; base_state_hash?: string; operation_id: string;
    reviewer: string; variant_index: 1 | 2 | 3;
  }) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/scene-recipes`, "POST", body),
  reviewWholeHomeSceneRecipe: (id: string, recipeId: string, body: {
    base_revision: number; base_state_hash?: string; operation_id: string;
    reviewer: string; note: string; action: "review" | "lock";
  }) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/scene-recipes/${encodeURIComponent(recipeId)}/review`, "POST", body),
  getWholeHomeMarketingProposal: (id: string) =>
    jget<WholeHomeMarketingProposal>(`/api/whole-home/projects/${encodeURIComponent(id)}/marketing-proposal`),
  getWholeHomeGeometryContract: (id: string) =>
    jget<WholeHomeGeometryContract>(`/api/whole-home/projects/${encodeURIComponent(id)}/geometry-acceptance`),
  getWholeHomeGeometryManifest: (id: string) =>
    jget<WholeHomeGeometryManifest>(`/api/whole-home/projects/${encodeURIComponent(id)}/geometry-manifest`),
  saveWholeHomeSourceRegistration: (id: string, body: {
    base_revision: number; base_state_hash?: string; operation_id: string;
    reviewer: string; registration: Record<string, unknown>;
  }) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/source-registration`, "PUT", body),
  prepareWholeHomeRasterRegistration: (id: string, body: {
    base_revision: number; base_state_hash?: string; operation_id: string;
    reviewer: string;
    scale_anchors: Array<{
      id: string; start_px: [number, number]; end_px: [number, number]; length_m: number;
    }>;
    origin_px: [number, number];
  }) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/source-registration/raster`, "POST", body),
  evaluateWholeHomeGeometry: (id: string, body: {
    base_revision: number; base_state_hash?: string; operation_id: string;
    reviewer: string; review_note: string; assumptions_confirmed: boolean;
    raster_metrics?: Record<string, number>; commit: boolean;
  }) => jsend<{
    committed: boolean;
    report: Record<string, unknown> & { status: string; issues?: Array<Record<string, unknown>> };
    metrics: Record<string, unknown>;
    manifest_summary: Record<string, unknown>;
    project?: WholeHomeProject;
  }>(`/api/whole-home/projects/${encodeURIComponent(id)}/geometry-acceptance`, "POST", body),
  reparseWholeHomeCad: (id: string, base_revision: number, candidate_id = "", operation_id = "") =>
    jsend<WholeHomeCadReparseOperation>(`/api/whole-home/projects/${encodeURIComponent(id)}/cad/reparse`, "POST", {
      base_revision,
      annotator_id: "local-user",
      candidate_id,
      operation_id,
    }),
  getWholeHomeCadReparseOperation: (id: string, operationId: string) =>
    jget<WholeHomeCadReparseOperation>(
      `/api/whole-home/projects/${encodeURIComponent(id)}/cad/reparse/${encodeURIComponent(operationId)}`,
    ),
  getWholeHomeCadSpaceDraft: (id: string) =>
    jget<WholeHomeCadSpaceDraft>(`/api/whole-home/projects/${encodeURIComponent(id)}/cad/space-draft`),
  saveWholeHomeCadSpaceDraft: (id: string, body: WholeHomeCadSpaceDraftPut) =>
    jsend<WholeHomeCadSpaceDraftPutResponse>(
      `/api/whole-home/projects/${encodeURIComponent(id)}/cad/space-draft`,
      "PUT",
      body,
    ),
  reconstructWholeHomeCadSemantics: (id: string, base_revision: number) =>
    jsend<WholeHomeProject>(`/api/whole-home/projects/${encodeURIComponent(id)}/cad/semantic-reconstruct`, "POST", {
      base_revision,
      annotator_id: "local-user",
    }),
  reviewWholeHomeCadWithAi: (id: string, base_revision: number, review_passes: 1 | 2 = 1) =>
    jsend<WholeHomeCadAiAdvisory>(
      `/api/whole-home/projects/${encodeURIComponent(id)}/cad/ai-assist`,
      "POST",
      {
        base_revision,
        review_passes,
        operation_id: `cadai_${crypto.randomUUID().replaceAll("-", "")}`,
        annotator_id: "local-user",
      },
    ),
  saveWholeHomeModel: (
    id: string,
    body: { base_revision: number; model: WholeHomeModel; operations: Array<Record<string, unknown>>; annotator_id: string },
  ) => jsend<WholeHomeProject>(`/api/whole-home/projects/${encodeURIComponent(id)}/model`, "PUT", body),
  verifyWholeHomeModel: (
    id: string,
    body: { base_revision: number; acknowledged_warning_codes: string[]; annotator_id: string },
  ) => jsend<WholeHomeProject>(`/api/whole-home/projects/${encodeURIComponent(id)}/verify`, "POST", body),
  rebuildWholeHomeSemanticLayout: (id: string, base_revision: number) =>
    jsend<WholeHomeProject>(`/api/whole-home/projects/${encodeURIComponent(id)}/semantic-layout`, "POST", {
      base_revision, annotator_id: "local-user",
    }),
  createWholeHomeCameraCandidates: (
    id: string,
    body: { aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16"; max_per_room: number; mode?: "room" | "reference"; contract_id?: string },
  ) => jsend<WholeHomeCameraCandidateProposal>(`/api/whole-home/projects/${encodeURIComponent(id)}/camera-candidates`, "POST", body),
  renderWholeHomeReferenceCaptures: (
    id: string,
    body: { reference_proposal_id: string; reference_proposal_hash: string; width?: number; height?: number; annotator_id?: string },
  ) => jsend<import("./types").WholeHomeReferenceCaptureBatchResponse>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/reference-captures`, "POST", body),
  saveWholeHomeCapture: (
    id: string,
    body: {
      camera: WholeHomeCamera;
      aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
      rgb_data_url: string;
      depth_data_url: string;
      normal_data_url: string;
      edge_data_url: string;
      semantic_data_url: string;
      semantic_legend: Record<string, string>;
      subject_id_data_url?: string;
      subject_id_legend?: import("./types").WholeHomeSubjectIdLegend;
      room_id: string;
      plan_id: string;
      candidate_id: string;
      reference_slot_id?: string;
      reference_proposal_id?: string;
      reference_proposal_hash?: string;
      scene_recipe_id?: string;
      scene_hash?: string;
      pool_rank: 1 | 2 | 3;
      is_primary: boolean;
      annotator_id: string;
    },
  ) => jsend<WholeHomeProject>(`/api/whole-home/projects/${encodeURIComponent(id)}/captures`, "POST", body),
  rankWholeHomeCameras: (
    id: string,
    body: {
      aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
      shots_per_room: 1 | 2;
      candidates: WholeHomeCameraCandidate[];
      room_pools: WholeHomeCameraRoomPool[];
      annotator_id: string;
    },
  ) => jsend<WholeHomeAutoCameraPlan>(`/api/whole-home/projects/${encodeURIComponent(id)}/camera-plans`, "POST", body),
  saveWholeHomePanoCapture: (
    id: string,
    body: {
      pano_id: string;
      camera: WholeHomeCamera;
      projection: "equirectangular";
      coordinate_system: "right-handed-y-up";
      camera_center_m: import("./types").MetricXYZ;
      canonical_forward: string;
      heading_deg: number;
      pitch_deg: number;
      roll_deg: number;
      erp_width: number;
      erp_height: number;
      cube_face_size: number;
      cube_face_order: import("./types").PanoCubeFaceOrder;
      near_m: number;
      far_m: number;
      depth_encoding: "linear_metric_global_range";
      normal_encoding: "world_space_xyz_to_rgb";
      rgb_atlas_data_url: string;
      depth_atlas_data_url: string;
      normal_atlas_data_url: string;
      edge_atlas_data_url: string;
      semantic_atlas_data_url: string;
      subject_id_atlas_data_url: string;
      semantic_legend: Record<string, string>;
      subject_id_legend: import("./types").WholeHomeSubjectIdLegend;
      render_contract: { materials: Record<string, unknown>; lighting: Record<string, unknown> };
      source_hash?: string;
      scene_recipe_id?: string;
      scene_hash?: string;
      room_id: string;
      annotator_id: string;
    },
  ) => jsend<WholeHomeProject>(`/api/whole-home/projects/${encodeURIComponent(id)}/pano-captures`, "POST", body),
  previewWholeHomePanoEdit: (
    id: string,
    panoId: string,
    body: {
      source_hash: string;
      provider: "fal" | "openai";
      engine: "gpt-image-2" | "flux-canny";
      model_id: string;
      edit_instruction: string;
      style_description: string;
      repair_band_deg: number;
      annotator_id: string;
    },
  ) => jsend<WholeHomePanoPaidPreview>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/panos/${encodeURIComponent(panoId)}/paid-preview`,
    "POST", body,
  ),
  materializeWholeHomePano: (
    id: string,
    panoId: string,
    body: { source_hash: string; preset: "warm-contemporary"; annotator_id: string },
  ) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/panos/${encodeURIComponent(panoId)}/materialize`,
    "POST", body,
  ),
  editWholeHomePano: (
    id: string,
    panoId: string,
    body: { pano_id: string; source_hash: string; preview_id: string; confirmation_phrase: string; annotator_id: string },
  ) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/panos/${encodeURIComponent(panoId)}/edit`,
    "POST", body,
  ),
  repairWholeHomePano: (
    id: string,
    panoId: string,
    body: { pano_id: string; source_hash: string; preview_id: string; confirmation_phrase: string; annotator_id: string },
  ) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/panos/${encodeURIComponent(panoId)}/repair`,
    "POST", body,
  ),
  gateWholeHomePano: (
    id: string,
    panoId: string,
    body: { source_hash: string; face_size: number; annotator_id: string },
  ) => jsend<{ gate: WholeHomePanoGate; pano_id: string }>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/panos/${encodeURIComponent(panoId)}/gate`,
    "POST", body,
  ),
  reviewWholeHomePano: (
    id: string,
    panoId: string,
    body: {
      source_hash: string;
      gate_version: string;
      checklist: Record<string, "pass" | "uncertain">;
      annotator_id: string;
    },
  ) => jsend<WholeHomeProject>(
    `/api/whole-home/projects/${encodeURIComponent(id)}/panos/${encodeURIComponent(panoId)}/review`,
    "POST", body,
  ),
  createWholeHomeRun: (body: {
    project_id: string;
    capture_ids?: string[];
    capture_groups?: WholeHomeCaptureGroup[];
    floor_path?: string;
    material_mode?: "floor_sample" | "reference" | "style_pack";
    scene_recipe_id?: string;
    reference_contract_id?: string;
    benchmark_batch_id?: string;
    style_ref_path?: string | null;
    prompt: string;
    style: string;
    lighting: string;
    model_keys: ("b2" | "pro")[];
    candidates_per_camera: 1 | 2;
    aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
    resolution: "2K" | "4K";
    idempotency_key?: string;
  }) => jsend<WholeHomeRun>("/api/whole-home/runs", "POST", body),
  getWholeHomeManualCapabilities: () =>
    jget<WholeHomeManualCapabilities>("/api/whole-home/manual/capabilities"),
  previewWholeHomeManualRun: (body: {
    project_id: string;
    capture_ids: string[];
    capture_groups: [];
    floor_path: string;
    material_mode: "floor_sample" | "style_pack";
    scene_recipe_id?: string;
    reference_contract_id: "";
    benchmark_batch_id: "";
    style_ref_path?: string | null;
    prompt: string;
    style: string;
    lighting: string;
    model_keys: ("b2" | "pro")[];
    candidates_per_camera: 1;
    aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
    resolution: "2K";
    idempotency_key: string;
  }) => jsend<WholeHomeManualRunPreview>(
    "/api/whole-home/manual/runs/preview", "POST", body),
  commitWholeHomeManualRun: (body: {
    preview_id: string;
    preview_sha256: string;
    confirmation_phrase: string;
  }) => jsend<WholeHomeRun>(
    "/api/whole-home/manual/runs/commit", "POST", body),
  listWholeHomeRuns: (limit = 30) => jget<WholeHomeRun[]>(`/api/whole-home/runs?limit=${limit}`),
  getWholeHomeRun: (id: string) => jget<WholeHomeRun>(`/api/whole-home/runs/${encodeURIComponent(id)}`),
  getWholeHomeProjectHistory: (id: string, limit = 100, cursor = "") =>
    jget<WholeHomeProjectHistory>(
      `/api/whole-home/projects/${encodeURIComponent(id)}/history?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    ),
  getWholeHomeRunReplay: (id: string) =>
    jget<WholeHomeRunReplay>(`/api/whole-home/runs/${encodeURIComponent(id)}/replay`),
  forkWholeHomeRun: (id: string, body: { branch_name: string; source_snapshot_hash: string; idempotency_key: string }) =>
    jsend<WholeHomeProject>(`/api/whole-home/runs/${encodeURIComponent(id)}/fork`, "POST", body),
  getWholeHomeGenerationDraft: (id: string) =>
    jget<WholeHomeGenerationDraft>(`/api/whole-home/projects/${encodeURIComponent(id)}/generation-draft`),
  saveWholeHomeGenerationDraft: (
    id: string,
    body: Omit<WholeHomeGenerationDraft, "draft_version" | "updated_at" | "last_committed_batch_id" | "floor_url" | "style_ref_url"> & {
      expected_draft_version: number;
    },
  ) =>
    jsend<WholeHomeGenerationDraft>(`/api/whole-home/projects/${encodeURIComponent(id)}/generation-draft`, "PUT", body),
  previewWholeHomeVariantBatch: (body: {
    project_id: string;
    source_run_id: string;
    style: string;
    lighting: string;
    prompt: string;
    floor_path: string;
    style_ref_path: string;
    aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
    resolution: "2K";
    excluded_artifact_ids: string[];
    idempotency_key: string;
  }) => jsend<WholeHomeVariantBatch>("/api/whole-home/variant-batches/preview", "POST", body),
  getWholeHomeVariantBatch: (id: string) =>
    jget<WholeHomeVariantBatch>(`/api/whole-home/variant-batches/${encodeURIComponent(id)}`),
  commitWholeHomeVariantBatch: (id: string, body: { preview_hash: string; confirmation_phrase: string }) =>
    jsend<WholeHomeVariantBatch>(`/api/whole-home/variant-batches/${encodeURIComponent(id)}/commit`, "POST", body),
  cancelWholeHomeVariantBatch: (id: string) =>
    jsend<WholeHomeVariantBatch>(`/api/whole-home/variant-batches/${encodeURIComponent(id)}/cancel`, "POST", {}),
  getWholeHomeReviewState: (id: string) =>
    jget<WholeHomeReviewState>(`/api/whole-home/runs/${encodeURIComponent(id)}/review-state`),
  reviewWholeHomeArtifact: (
    runId: string,
    resultId: string,
    body: {
      artifact_id: string;
      review_status: WholeHomeHumanReviewStatus;
      review_tags: string[];
      review_note: string;
      reviewer_id: string;
      expected_review_version: number;
      idempotency_key: string;
    },
  ) => jsend<WholeHomeReviewMutationResponse>(
    `/api/whole-home/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(resultId)}/review`,
    "POST",
    body,
  ),
  completeWholeHomeReview: (
    runId: string,
    body: { reviewer_id: string; expected_review_version: number; idempotency_key: string },
  ) => jsend<WholeHomeReviewState>(
    `/api/whole-home/runs/${encodeURIComponent(runId)}/review-complete`,
    "POST",
    body,
  ),
  continueWholeHomeRun: (
    runId: string,
    body: {
      expected_review_version: number;
      continuation_completion_event_id: string;
      idempotency_key: string;
      api_key?: string;
    },
  ) => jsend<WholeHomeRun>(
    `/api/whole-home/runs/${encodeURIComponent(runId)}/continue`,
    "POST",
    body,
  ),
  setWholeHomeTrainingConsent: (projectId: string, allowed: boolean) =>
    jsend<WholeHomeTrainingConsent>(
      `/api/whole-home/projects/${encodeURIComponent(projectId)}/training-consent`,
      "POST",
      { allowed, reviewer_id: "local-user" },
    ),
  getWholeHomeLearningSummary: (projectId = "") =>
    jget<WholeHomeLearningSummary>(
      `/api/whole-home-learning/summary${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    ),
  wholeHomeLearningExportUrl: (projectId = "") =>
    `${process.env.NEXT_PUBLIC_API_BASE || ""}/api/whole-home-learning/export${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
  retryWholeHomeQa: (id: string, resultIds: string[] = []) =>
    jsend<WholeHomeRun>(`/api/whole-home/runs/${encodeURIComponent(id)}/qa/retry`, "POST", { result_ids: resultIds }),
  cancelWholeHomeRun: (id: string) =>
    jsend<{ cancelled: boolean; status: string }>(`/api/whole-home/runs/${encodeURIComponent(id)}/cancel`, "POST"),

  createJob: (req: JobSubmit) => jsend<JobView>("/api/jobs", "POST", req),
  createFreeJob: (req: FreeJobSubmit) => jsend<JobView>("/api/jobs/free", "POST", req),
  previewDirectPanorama: (body: { image_path: string; room_reference_path?: string; params: GenParams }) =>
    jsend<DirectPanoramaPaidPreview>("/api/jobs/panorama-direct/preview", "POST", body),
  commitDirectPanorama: (body: { preview_id: string; preview_hash: string }) =>
    jsend<JobView>("/api/jobs/panorama-direct/commit", "POST", body),
  listJobs: (limit = 50) => jget<JobView[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => jget<JobView>(`/api/jobs/${id}`),
  cancelJob: (id: string) =>
    jsend<{ cancelled: boolean }>(`/api/jobs/${id}/cancel`, "POST"),
  cancelAll: () => jsend<{ stopped: number }>(`/api/jobs/cancel-all`, "POST"),
  clearCompleted: () =>
    jsend<{ cleared: number }>(`/api/jobs/clear-completed`, "POST"),
  deleteJob: (id: string) =>
    jsend<{ deleted: number }>(`/api/jobs/${id}/delete`, "POST"),
  retryJob: (id: string) => jsend<JobView>(`/api/jobs/${id}/retry`, "POST"),
  retrySdUpscale: (id: string) =>
    jsend<JobView>(`/api/jobs/${id}/sd-upscale`, "POST"),
  jobResult: (id: string, model: JobSlotKey, idx: number) =>
    jget<ModelRunCandidate & { model: string; total: number }>(
      `/api/jobs/${id}/result?model=${model}&idx=${idx}`,
    ),
  previewJobPanorama: (
    id: string,
    body:
      | { action: "generate"; source_model: ModelKey; source_index: number }
      | { action: "repair"; panorama_index: number },
  ) => jsend<PanoramaPaidPreview>(`/api/jobs/${id}/panorama/preview`, "POST", body),
  commitJobPanorama: (id: string, body: { preview_id: string; preview_hash: string }) =>
    jsend<JobView>(`/api/jobs/${id}/panorama/commit`, "POST", body),
  reviewJobPanorama: (
    id: string,
    body: { panorama_index: number; checklist: PureRenderPanoramaReviewChecklist },
  ) => jsend<JobView>(`/api/jobs/${id}/panorama/review`, "POST", body),
  preparePanoramaFloor: (id: string, panoramaIndex: number) =>
    jsend<PanoramaFloorPrepareResponse>(`/api/jobs/${id}/panorama/floor/prepare`, "POST", {
      panorama_index: panoramaIndex,
    }),
  previewPanoramaFloor: (id: string, body: PanoramaFloorRenderRequest) =>
    jsend<PanoramaFloorPreviewResponse>(`/api/jobs/${id}/panorama/floor/preview`, "POST", body),
  applyPanoramaFloor: (id: string, body: PanoramaFloorRenderRequest) =>
    jsend<{ ok: boolean; job: JobView; candidate_index: number; warnings: string[] }>(
      `/api/jobs/${id}/panorama/floor/apply`, "POST", body,
    ),
  prepareRecordPanoramaFloor: (body: PanoramaFloorRecordTarget) =>
    jsend<PanoramaFloorPrepareResponse>("/api/records/panorama/floor/prepare", "POST", body),
  previewRecordPanoramaFloor: (body: PanoramaFloorRecordRenderRequest) =>
    jsend<PanoramaFloorPreviewResponse>("/api/records/panorama/floor/preview", "POST", body),
  applyRecordPanoramaFloor: (body: PanoramaFloorRecordRenderRequest) =>
    jsend<{ ok: boolean; result_id: string; result_url: string; warnings: string[] }>(
      "/api/records/panorama/floor/apply", "POST", body,
    ),
  polishJob: (id: string) => jsend<JobView>(`/api/jobs/${id}/polish`, "POST"),
  editJob: (id: string, body: EditRequest) =>
    jsend<JobView>(`/api/jobs/${id}/edit`, "POST", body),

  // ── 快速预览（Nano Banana 2 Lite · 1K，与 4K 队列解耦；短轮询状态）──
  createPreview: (req: PreviewRequest) =>
    jsend<{ preview_id: string; status: string }>("/api/preview", "POST", req),
  previewStatus: (pid: string) => jget<PreviewView>(`/api/preview/${pid}`),
  cancelPreview: (pid: string) =>
    jsend<{ cancelled: boolean }>(`/api/preview/${pid}/cancel`, "POST"),

  recentSwatches: (limit = 24) =>
    jget<Swatch[]>(`/api/swatches/recent?limit=${limit}`),

  listRecords: () => jget<RecordFile[]>(`/api/records`),
  listGeometryAudits: (limit = 40) =>
    jget<Array<{ file: RecordFile; entry: RecordEntry }>>(
      `/api/records/geometry-audits?limit=${limit}`,
    ),
  loadRecord: (jsonPath: string) =>
    jget<RecordEntry[]>(
      `/api/records/load?json_path=${encodeURIComponent(jsonPath)}`,
    ),
  geometryAuditArtifactUrl: (jsonPath: string, recordId: string, artifactId: string) =>
    `${API}/api/records/geometry-audit/artifact?json_path=${encodeURIComponent(jsonPath)}&record_id=${encodeURIComponent(recordId)}&artifact_id=${encodeURIComponent(artifactId)}`,
  reviewGeometryAudit: (body: {
    json_path: string;
    record_id: string;
    checked_metric_ids: string[];
    reviewer: string;
    note: string;
  }) => jsend<{
    checked_metric_ids: string[];
    reviewer: string;
    note: string;
    reviewed_at: string;
    checked_count: number;
    metric_count: number;
    complete: boolean;
  }>(`/api/records/geometry-audit/review`, "POST", body),
  reveal: (json_path: string, record_id: string, password: string) =>
    jsend<{ text: string; ok: boolean }>(`/api/records/reveal`, "POST", {
      json_path,
      record_id,
      password,
    }),

  recipes: (tone = "", limit = 6) =>
    jget<Recipe[]>(
      `/api/recipes?tone=${encodeURIComponent(tone)}&limit=${limit}`,
    ),

  // ── 自定义配方（我的配方）──
  listCustomRecipes: () => jget<CustomRecipe[]>(`/api/recipes/custom`),
  addCustomRecipe: (name: string, params: GenParams) =>
    jsend<CustomRecipe>(`/api/recipes/custom`, "POST", { name, params }),
  updateCustomRecipe: (rid: string, patch: { name?: string; params?: GenParams }) =>
    jsend<CustomRecipe>(`/api/recipes/custom/${rid}/update`, "POST", patch),
  deleteCustomRecipe: (rid: string) =>
    jsend<{ ok: boolean }>(`/api/recipes/custom/${rid}/delete`, "POST"),
  classifyFailure: (err: string) =>
    jsend<FailureKB>(`/api/failure/classify`, "POST", { err }),
  connectionTest: () => jget<{ result: string }>(`/api/connection/test`),

  // ── Omakase：Gemini 主线路生成场景，DeepSeek 配置后自动备用 ──
  omakaseScenes: (idea: string) =>
    jsend<OmakaseScenesResponse>(`/api/omakase/scenes`, "POST", { idea }),

  getConfig: () => jget<ConfigView>(`/api/config`),
  putConfig: (patch: ConfigPatch) =>
    jsend<ConfigView>(`/api/config`, "PUT", patch),
  getModels: () => jget<ModelsView>(`/api/models`),
  getOptions: () => jget<OptionsView>(`/api/options`),

  // ── STEP 2.5 迁移补齐 ──
  regenJob: (id: string, n: number) =>
    jsend<JobView>(`/api/jobs/${id}/regen?n=${n}`, "POST"),
  recordEdit: (body: RecordEditRequest) =>
    jsend<JobView>(`/api/records/edit`, "POST", body),
  deleteResult: (json_path: string, record_id: string, result_id: string) =>
    jsend<{ ok: boolean }>(`/api/records/result/delete`, "POST", {
      json_path,
      record_id,
      result_id,
    }),
  favoriteResult: (json_path: string, record_id: string, result_id: string) =>
    jsend<{ favorite: boolean }>(`/api/records/result/favorite`, "POST", {
      json_path,
      record_id,
      result_id,
    }),
  reviewResult: (body: ResultReviewPatch) =>
    jsend<{
      review_status: string;
      review_tags: string[];
      review_note: string;
      best: boolean;
      reviewed_at: string;
    }>(`/api/records/result/review`, "POST", body),
  deleteRecord: (json_path: string, record_id: string) =>
    jsend<{ ok: boolean }>(`/api/records/delete`, "POST", {
      json_path,
      record_id,
    }),
  exportHtmlUrl: (json_path: string) =>
    `${API}/api/records/export/html?json_path=${encodeURIComponent(json_path)}`,
  exportPptxUrl: (json_path: string) =>
    `${API}/api/records/export/pptx?json_path=${encodeURIComponent(json_path)}`,
  exportFavoritesUrl: () => `${API}/api/records/export/favorites-pptx`,
  floorAnalyze: (path: string) =>
    jget<FloorAnalyze>(`/api/floor/analyze?path=${encodeURIComponent(path)}`),
  getUsage: () => jget<UsageSummary>(`/api/usage`),
  getFailureRules: () => jget<FailureKB[]>(`/api/failure/rules`),

  // ── 评审复盘 ──
  getReviewSummary: () => jget<ReviewSummary>(`/api/review/summary`),
  getReviewGallery: (filter: "pass" | "best" = "pass", limit = 60) =>
    jget<ReviewGalleryItem[]>(`/api/review/gallery?filter=${filter}&limit=${limit}`),

  // ── PPTX 品牌 logo ──
  uploadLogo: (f: File) => upload("/api/uploads/logo", f),
  clearLogo: () => jsend<{ ok: boolean }>("/api/uploads/logo/clear", "POST"),

  // ── 手动校色（区域化 Reinhard）──
  colorMatchPreview: (b: ColorMatchPreviewRequest, signal?: AbortSignal) =>
    jsend<ColorMatchPreviewView>(`/api/color-match/preview`, "POST", b, signal),
  colorMatchSegment: (b: ColorMatchSegmentRequest, signal?: AbortSignal) =>
    jsend<ColorMatchSegmentView>(`/api/color-match/segment`, "POST", b, signal),
  jobColorMatch: (id: string, b: JobColorMatchRequest) =>
    jsend<JobView>(`/api/jobs/${id}/color-match`, "POST", b),
  recordColorMatch: (b: RecordColorMatchRequest) =>
    jsend<{ ok: boolean; result_url: string }>(`/api/records/color-match`, "POST", b),
  suiteColorMatch: (id: string, b: SuiteColorMatchRequest) =>
    jsend<FloorplanSuite>(`/api/floorplan-suites/${id}/color-match`, "POST", b),

  // ── 生成式修补（画笔选区 → 并发 n 候选抽卡 → 挑选提交；引擎可切换）──
  inpaintSegment: (b: InpaintSegmentRequest, signal?: AbortSignal) =>
    jsend<InpaintSegmentView>(`/api/inpaint/segment`, "POST", b, signal),
  submitInpaint: (b: GenericInpaintRequest) =>
    jsend<InpaintSubmitView>(`/api/inpaint`, "POST", b),
  inpaintStatus: (iid: string) => jget<InpaintStatusView>(`/api/inpaint/${iid}`),
  applyInpaint: (iid: string, index: number) =>
    jsend<InpaintApplyResponse>(`/api/inpaint/${iid}/apply`, "POST", { index }),
  cancelInpaint: (iid: string) =>
    jsend<{ cancelled: boolean }>(`/api/inpaint/${iid}/cancel`, "POST"),
  comfyuiPing: (url = "") =>
    jget<ComfyUIPingView>(`/api/inpaint/comfyui/ping${url ? `?url=${encodeURIComponent(url)}` : ""}`),

  // ── 真实纹理投影（无外部 API 费用）──
  previewFloorVisualize: (b: FloorVisualizeRequest, signal?: AbortSignal) =>
    jsend<FloorVisualizePreview>("/api/floor-visualize/preview", "POST", b, signal),
  applyFloorVisualize: (b: FloorVisualizeRequest) =>
    jsend<FloorVisualizeApplyResponse>("/api/floor-visualize/apply", "POST", b),
};
