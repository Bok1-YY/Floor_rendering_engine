// 后端 server_api.py 返回结构的 TS 镜像（手动对齐；后端改字段时同步这里）

export type JobStatus = "queued" | "running" | "done" | "partial" | "failed";
export type ModelFilter = "b2" | "pro" | "both" | "sd35" | "custom";
export type GenerationModelKey = "b2" | "pro" | "sd35";
/** Backward-compatible name used by the ordinary generation/editing UI. */
export type ModelKey = GenerationModelKey;
/** Job runs may also contain a derived panorama slot that cannot be submitted initially. */
export type JobSlotKey = GenerationModelKey | "vr360";

export type PanoramaGateStatus = "passed" | "repair_recommended" | "failed";
export type PanoramaReviewValue = "pass" | "fail" | "uncertain";

export interface PureRenderPanoramaGateCheck {
  check_id: string;
  status: "pass" | "fail" | "warn";
  metric: string;
  value: string | number;
  threshold: string | number;
  detail?: string;
  [key: string]: unknown;
}

export interface PureRenderPanoramaGate {
  version: "visual_pano_v1" | string;
  status: PanoramaGateStatus;
  gate_pass: boolean;
  hard_fail: boolean;
  geometry_locked?: false;
  delivery_scope?: "ai_expanded_single_hotspot" | "ai_generated_single_center_cubemap" | string;
  checks: PureRenderPanoramaGateCheck[];
  failures: string[];
  warnings: string[];
  summary: string;
}

export interface PureRenderPanoramaReviewChecklist {
  wrap_seam: PanoramaReviewValue;
  horizon_and_lines: PanoramaReviewValue;
  object_integrity: PanoramaReviewValue;
  floor_and_material: PanoramaReviewValue;
  lighting_continuity: PanoramaReviewValue;
  poles: PanoramaReviewValue;
}

export interface PureRenderPanoramaMeta {
  schema_version: 1 | 2;
  projection: "equirectangular";
  width: 3840;
  height: 1920;
  source_model: GenerationModelKey | "b2_atlas" | "gpt_atlas" | string;
  source_index: number;
  source_sha256: string;
  provider: "fal";
  endpoint: string;
  model_id: string;
  snapshot_locked: false;
  geometry_locked: false;
  delivery_scope: "ai_expanded_single_hotspot" | "ai_generated_single_center_cubemap" | string;
  viewer_initial_yaw_deg: number;
  gate: PureRenderPanoramaGate;
  review?: {
    status: "accepted" | "rejected" | "needs_review";
    checklist: Partial<PureRenderPanoramaReviewChecklist>;
    reviewed_at: string;
  };
  repair_of_index?: number;
  repair_claimed?: boolean;
  repair_result_index?: number;
  repair_kind?: "wrap_seam" | "cube_boundaries" | "architecture" | string;
  parent_panorama_index?: number;
  floor_correction_result_index?: number;
  floor_correction?: {
    model: "spherical-floor-render-v1" | string;
    status: "needs_review" | "accepted" | "rejected" | string;
    mask_coverage: number;
    outside_mask_byte_identical: boolean;
    recipe: SphericalFloorRecipe;
    warnings?: string[];
    [key: string]: unknown;
  };
  record_result_id?: string;
  generated_at?: string;
  [key: string]: unknown;
}

export interface CandidateGenerationMetadata {
  projection?: "perspective" | "equirectangular";
  panorama?: PureRenderPanoramaMeta;
  [key: string]: unknown;
}

export interface ModelRunCandidate {
  idx: number;
  url: string;
  thumb: string;
  metadata?: CandidateGenerationMetadata;
}

export interface SDOptions {
  seed?: number | null;
  steps: number;
  guidance_scale: number;
  reference_strength: number;
  positive_addition: string;
  negative_addition: string;
}

export interface ModelRunView {
  key: JobSlotKey;
  label: string;
  status: JobStatus | "idle";
  stage: string;
  seconds: number | null;
  error: string;
  url: string;
  thumb: string;
  idx: number;
  total: number;
  base_url: string;
  api_original_url: string;
  api_original_thumb: string;
  auto_color_status: string;
  auto_color_error: string;
  delivery_status: string;
  seed: number | null;
  settings: Record<string, unknown>;
  candidates?: ModelRunCandidate[];
}

export interface FailureKB {
  key: string;
  title: string;
  cause: string;
  action: string;
}

/** /api/jobs 系列返回的任务视图（_job_view 的 JSON） */
export interface JobView {
  job_id: string;
  display_name: string;
  ts: string;
  status: JobStatus;
  model_filter: ModelFilter;
  model_targets: JobSlotKey[];
  model_runs: Partial<Record<JobSlotKey, ModelRunView>>;
  workflow_mode: string;
  delivery_mode?: "perspective" | "direct_cubemap_atlas" | string;
  error: string;
  error_kb: FailureKB | null;
  b2_stage: string;
  pro_stage: string;
  b2_secs: number | null;
  pro_secs: number | null;
  time_text: string;
  model_status: string;
  pro_polishing: boolean;
  operation: string;
  operation_status: "idle" | "running" | "done" | "failed" | "cancelled";
  operation_error: string;
  panorama_resume?: {
    preview_id: string;
    preview_hash: string;
    route: "perspective_to_erp" | "direct_cubemap_atlas" | string;
    request_ids: string[];
    source_model: string;
    source_index: number;
    created_at_epoch: number;
    reason: string;
  } | null;
  has_retry: boolean;
  record_id: string;
  json_path: string;
  room_url: string; // 房间原图 URL（替换类工作流才非空），供前后对比
  floor_url: string; // 地板小样（优化图）URL，供手动校色参照
  floor_path: string;

  b2_url: string;
  b2_thumb: string;
  b2_idx: number;
  b2_total: number;
  pro_url: string;
  pro_thumb: string;
  pro_idx: number;
  pro_total: number;
}

/** 生图参数（镜像后端 GenParams / models.TaskParams，全部可选除 workflow_mode 外用后端默认） */
export interface GenParams {
  workflow_mode: string;
  model_choice?: string;
  continent?: string;
  country?: string;
  city?: string;
  neighborhood?: string;
  property_type?: string;
  room_type?: string;
  view?: string;
  style_type?: string;
  lighting?: string;
  floor_tone?: string;
  floor_size?: string;
  seam_type?: string;
  glossiness?: string;
  film_path?: string;
  film_width_mm?: number | null;
  film_repeat_length_mm?: number | null;
  film_repeat_axis?: "long_edge";
  film_slit_origin_mm?: number | null;
  floor_coverage_min?: number;
  floor_coverage_max?: number;
  angle?: string;
  aspect_ratio?: string;
  resolution?: string;
  avoid_items?: string[];
  custom_addition?: string;
  pet_type?: string;
  pet_action?: string;
  pet_focus?: string;
  market_furniture?: string;
  last_image_path?: string;
  cn_mode?: boolean;
  cn_developer?: string;
  cn_city?: string;
  cn_tier?: string;
  cn_unit_type?: string;
  cn_delivery?: string;
  cn_room_type?: string;
  cn_view?: string;
  cn_space_features?: string[] | null;
  cn_facilities?: string[] | null;
  style_ref_correction?: string;
  scene_override?: string;   // Omakase：AI 原创场景散文，接管 Omakase 工作流的场景层（仅 Omakase 生效，其他工作流忽略）
  cinematic_enabled?: boolean; // Gemini 电影真实感导演规划；只作用于生成新场景的 B2/Pro
  panel_submode?: string;    // 墙板模式子行为：再设计 / 替换 / 纯原创（仅墙板模式生效，其他工作流忽略）
  panel_size?: string;       // 墙板尺寸/板型（预设或自定义；仅墙板再设计/纯原创生效）
}

export interface JobSubmit {
  image_path: string;
  model_filter: "b2" | "pro" | "both";
  model_targets?: GenerationModelKey[];
  sd_options?: SDOptions;
  api_key?: string;
  room_path?: string | null;
  ref_path?: string | null;
  params: GenParams;
}

export interface FreeJobSubmit {
  prompt: string;
  image_paths: string[];
  model_targets: Array<"b2" | "pro">;
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  resolution: "2K" | "4K";
  api_key?: string;
}

export interface EditRequest {
  instruction: string;
  api_key?: string;
  image_size?: string;
  preserve_floor_geometry?: boolean;
  model_choice?: string;
  color_match?: boolean; // 保持原图色彩（防偏色），后端默认 true
}

/** 快速预览（Nano Banana 2 Lite · 1K）请求；字段同 JobSubmit 但无 model_filter。 */
export interface PreviewRequest {
  image_path: string;
  api_key?: string;
  room_path?: string | null;
  ref_path?: string | null;
  params: GenParams;
}

/** 快速预览状态快照（/api/preview 系列返回）。 */
export interface PreviewView {
  preview_id: string;
  status: "running" | "done" | "failed";
  stage: string;
  url: string;
  thumb: string;
  error: string;
}

export interface PanoramaPaidPreview {
  policy: "pure_render_pano_paid_preview_v1" | string;
  preview_id: string;
  preview_hash: string;
  expires_at: string;
  action: "generate" | "repair";
  source: {
    model: JobSlotKey;
    index: number;
    thumb: string;
    sha256: string;
    label: string;
  };
  panorama_index?: number | null;
  repair_kind?: "wrap_seam" | "cube_boundaries" | "architecture" | null;
  provider: "fal";
  endpoint: string;
  engine: "gpt-image-2";
  model_id: string;
  snapshot_locked: false;
  output_size: { width: 3840; height: 1920 };
  max_provider_calls: 1;
  estimated_cost: number | null;
  quality_plan?: PanoramaQualityPlan | null;
  film_contract?: FilmRepeatContract | null;
  geometry_contract?: LocalPanoramaGeometryContract | null;
  warning: string;
}

export interface FilmRepeatContract {
  manifest: {
    version: string;
    manifest_hash: string;
    status: "ready" | "repeat_invalid" | string;
    image_size: [number, number];
    film_width_mm: number;
    repeat_length_mm: number;
    pixels_per_mm_x: number;
    pixels_per_mm_y: number;
    plank_width_mm: number;
    plank_length_mm: number;
    phase_state_count: number;
    effective_board_states: number;
    phase_advance_mm: number;
    slitting: {
      lane_count: number;
      slit_origin_mm: number;
      remaining_mm: number;
      left_margin_mm: number;
      right_margin_mm: number;
    };
    repeat_registration: {
      translation_px_x: number;
      boundary_mean_abs_diff: number;
      threshold: number;
      status: string;
    };
    exclusion_rects: Array<{
      kind: string;
      pixel_rect: [number, number, number, number];
      physical_rect_mm: [number, number, number, number];
      confidence: number;
    }>;
  };
  guide_b64: string;
  guide_sha256: string;
  guide_size: [number, number];
}

export interface LocalPanoramaGeometryContract {
  version: "local-panorama-geometry-v2" | string;
  status: "ready" | "needs_calibration" | "rejected" | string;
  reference_role: string;
  source_sha256: string;
  source_size: [number, number];
  contract_hash: string;
  confidence: number;
  camera: {
    horizontal_fov_deg: number;
    fov_source: string;
    pitch_deg: number;
    roll_deg: number;
    source_yaw_deg: number;
    camera_height_m: number;
    camera_height_source: string;
  };
  floor_frame: {
    normal: [number, number, number];
    plank_direction_deg: number;
    origin_x_m: number;
    origin_z_m: number;
    scale_source: string;
  };
  manhattan: {
    line_count: number;
    vertical_line_count: number;
    horizon_status: string;
    confidence: number;
  };
  warnings: string[];
}

export interface PanoramaQualityPlan {
  version: "panorama_quality_director_v1" | string;
  route: "perspective_to_erp" | "direct_cubemap_atlas" | string;
  status: "planned" | "local_fallback";
  planner_model: string;
  cache_hit: boolean;
  planner_call_count: number;
  display_answers: Array<{
    id: string;
    question: string;
    answer: string;
  }>;
  sector_contract: Array<{
    id: string;
    label: string;
    contract: string;
  }>;
  cube_face_contract: Array<{
    id: string;
    label: string;
    contract: string;
  }>;
  object_registry: Array<{
    id: string;
    identity: string;
    location: string;
    visibility: string;
  }>;
  risk_flags: string[];
  final_direction: string;
  plan_hash: string;
  validation: {
    schema_valid: boolean;
    route_contract_valid: boolean;
    floor_material_rewrite: false;
  };
  error: string;
}

export interface SphericalFloorRecipe {
  camera_height_m: number;
  rotation_deg: number;
  scale: number;
  offset_x: number;
  offset_z: number;
  texture_width_mm: number;
  texture_height_mm: number;
  plank_width_mm?: number | null;
  plank_length_mm?: number | null;
  illumination_strength: number;
  shadow_strength: number;
  contact_shadow_strength: number;
  feather: number;
}

export interface PanoramaFloorMaskView {
  id: "front" | "right" | "back" | "left" | "nadir";
  label: string;
  yaw_deg: number;
  pitch_deg: number;
  fov_deg: number;
  width: number;
  height: number;
  image_b64: string;
  mask_b64: string;
  confidence: number;
  status: string;
  warnings: string[];
}

export interface PanoramaFloorPrepareResponse {
  projection: "equirectangular";
  mask_version: string;
  panorama_index: number;
  source_sha256: string;
  texture_size: [number, number];
  parent_gate_status?: PanoramaGateStatus;
  views: PanoramaFloorMaskView[];
  defaults: SphericalFloorRecipe;
  warnings: string[];
}

export interface PanoramaFloorRenderRequest {
  panorama_index: number;
  source_sha256: string;
  view_masks: Array<{ id: PanoramaFloorMaskView["id"]; mask_b64: string }>;
  recipe: SphericalFloorRecipe;
}

export interface PanoramaFloorRecordTarget {
  json_path: string;
  record_id: string;
  result_id: string;
  texture_path: string;
}

export type PanoramaFloorRecordRenderRequest = PanoramaFloorRecordTarget &
  Omit<PanoramaFloorRenderRequest, "panorama_index">;

export interface PanoramaFloorPreviewResponse {
  preview: string;
  mask_b64: string;
  width: number;
  height: number;
  warnings: string[];
  metadata: Record<string, unknown>;
}

export interface DirectPanoramaPaidPreview {
  policy: "direct_cubemap_atlas_paid_preview_v1" | string;
  preview_id: string;
  preview_hash: string;
  expires_at: string;
  source: {
    thumb: string;
    sha256: string;
    label: string;
  };
  engines: Array<{
    key: "b2_atlas" | "gpt_atlas" | string;
    label: string;
    provider: "fal";
    endpoint: string;
    model_id: string;
    estimated_cost: number | null;
  }>;
  atlas: {
    width: 3072;
    height: 2048;
    face_size: 1024;
    layout: string[][];
  };
  output_size: { width: 3840; height: 1920 };
  max_provider_calls: 2;
  estimated_cost: number | null;
  quality_plan?: PanoramaQualityPlan | null;
  film_contract?: FilmRepeatContract | null;
  geometry_contract?: LocalPanoramaGeometryContract | null;
  room_reference?: { sha256: string; label: string } | null;
  warning: string;
}

// ── 户型图解析 / 整屋套图 ──────────────────────────────────────
export interface NormalizedPoint { x: number; y: number }

export interface FloorplanCamera {
  id: string;
  name: string;
  position: NormalizedPoint;
  target: NormalizedPoint;
  height_m: number | null;
  focal_length_mm: number | null;
  purpose: "hero" | "wide" | "detail" | "transition" | "custom";
  source: "ai_suggested" | "ai_edited" | "manual" | "legacy";
  confirmed: boolean;
  enabled_for_generation: boolean;
}

export interface FloorplanOpening {
  id: string;
  kind: "door" | "window" | "open_connection";
  points: [NormalizedPoint, NormalizedPoint];
  room_ids: string[];
  confidence: number;
  source: "ai_suggested" | "manual" | "ai_edited" | "legacy";
  review_status: "pending" | "accepted" | "rejected";
}

export interface FloorplanRoom {
  id: string;
  label: string;
  room_type: string;
  polygon: NormalizedPoint[];
  adjacent_room_ids: string[];
  dimensions_text: string;
  confidence: number;
  space_kind: "enclosed_room" | "open_zone" | "circulation" | "wet_area" | "balcony" | "other";
  source: "ai" | "human" | "ai_edited" | "legacy";
  selected: boolean;
  apply_floor: boolean;
  cameras: FloorplanCamera[];
  primary_camera_id: string;
  camera?: FloorplanCamera | null;
}

export interface FloorplanGeometryIssue {
  code: string;
  message: string;
  room_ids?: string[];
  camera_id?: string;
  overlap_ratio?: number;
}

export interface FloorplanGeometryReport {
  hard_errors: FloorplanGeometryIssue[];
  warnings: FloorplanGeometryIssue[];
  checked_at?: number;
}

export interface FloorplanOperation {
  type: string;
  room_id?: string;
  camera_id?: string;
  payload?: Record<string, unknown>;
}

export interface FloorplanSpatialPlanItem {
  name?: string;
  item?: string;
  function?: string;
  plan_position: NormalizedPoint;
  frame_position: string;
  depth: string;
  required_visible: boolean;
  orientation?: string;
  confidence?: number;
  computed_frame_position?: string;
  camera_depth?: number;
  camera_lateral?: number;
}

export interface FloorplanSpatialPlan {
  spatial_plan_id: string;
  analysis_id: string;
  room_id: string;
  camera_id: string;
  annotation_revision: number;
  status: "draft" | "locked";
  space_summary: string;
  camera_view: {
    direction: string;
    expected_composition: string;
    foreground_left: string[];
    foreground_center: string[];
    foreground_right: string[];
    midground_left: string[];
    midground_center: string[];
    midground_right: string[];
    background_left: string[];
    background_center: string[];
    background_right: string[];
    hidden_behind_camera: string[];
  };
  architecture: {
    visible_walls: string[];
    required_opening_ids: string[];
    required_openings: string[];
    open_connections: string[];
    fixed_boundaries: string[];
    forbidden_openings: string[];
  };
  zones: FloorplanSpatialPlanItem[];
  furniture: FloorplanSpatialPlanItem[];
  hard_constraints: string[];
  must_not_appear: string[];
  uncertainties: string[];
  planner_model?: string;
  overlay_path?: string;
  overlay_url?: string;
  camera_math?: { camera?: string; focal_length_mm?: number | null; height_m?: number | null; rule?: string };
  created_at?: number;
  updated_at?: number;
  locked_at?: number | null;
  locked_by?: string;
}

export interface FloorplanViewProxy {
  view_proxy_id: string;
  status: "confirmed" | "stale";
  path: string;
  url?: string;
  source_hash: string;
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  render_config: {
    camera_height_m: number;
    focal_length_mm: number;
    wall_height_m: number;
    room_long_side_m: number;
    renderer: string;
  };
  annotation_revision: number;
  spatial_plan_id: string;
  confirmed_at: number;
  confirmed_by: string;
}

export interface FloorplanUpload extends Swatch {
  source_path: string;
  page: number;
  page_count: number;
  pages: Array<Swatch & { page: number }>;
}

export interface FloorplanAnalysis {
  analysis_id: string;
  status: "queued" | "analyzing" | "done" | "confirmed" | "failed";
  stage: string;
  error: string;
  floorplan_path: string;
  floorplan_url: string;
  summary: string;
  orientation: string;
  entrance: NormalizedPoint | null;
  warnings: string[];
  rooms: FloorplanRoom[];
  openings: FloorplanOpening[];
  openings_review_status: "pending" | "confirmed";
  confirmed: boolean;
  schema_version: number;
  revision: number;
  verified_revision: number;
  annotation_status: "draft" | "verified";
  training_consent: boolean;
  training_eligible: boolean;
  verified_at: number | null;
  verified_by: string;
  annotator_id: string;
  geometry_report: FloorplanGeometryReport;
  operation_count: number;
  ai_model: string;
  spatial_plans: Record<string, FloorplanSpatialPlan>;
  view_proxies: Record<string, FloorplanViewProxy>;
  source: { path?: string; sha256?: string; width?: number; height?: number; original_name?: string };
}

export interface FloorplanEvaluation {
  status: "done" | "unavailable";
  layout_fidelity?: number;
  material_fidelity?: number;
  camera_match?: number;
  visual_quality?: number;
  suite_consistency?: number;
  total: number | null;
  warnings: string[];
  summary: string;
  eligible_for_recommendation?: boolean;
  hard_fail?: boolean;
  verification_incomplete?: boolean;
  checks?: Array<{
    constraint_id?: string;
    constraint: string;
    status: "pass" | "fail" | "uncertain";
    severity: "hard" | "soft";
    evidence: string;
  }>;
}

export interface FloorplanCandidate {
  result_id: string;
  index: number;
  model_index?: number;
  model_key?: "b2" | "pro";
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  error: string;
  path: string;
  url: string;
  thumb: string;
  model?: string;
  seconds?: number;
  structure_path?: string;
  structure_url?: string;
  material_path?: string;
  material_url?: string;
  final_path?: string;
  final_url?: string;
  material_pass_status?: "pending" | "done" | "failed" | "skipped";
  structure_evaluation?: FloorplanEvaluation;
  generation_trace?: Array<{ pass: string; provider: string; seconds: number; success: boolean; error: string; continuation_mode?: string }>;
  auto_color_status?: string;
  auto_color_error?: string;
  evaluation: FloorplanEvaluation | null;
  review_status?: "unreviewed" | "pass" | "backup" | "reject";
  review_tags?: string[];
  review_note?: string;
  best?: boolean;
  camera_id?: string;
  annotation_room_id?: string;
}

export interface FloorplanSuiteRoom extends FloorplanRoom {
  status: "queued" | "running" | "done" | "partial" | "failed" | "skipped";
  candidates: FloorplanCandidate[];
  recommended_result_id: string;
  annotation_room_id?: string;
  camera_id?: string;
  spatial_plan?: FloorplanSpatialPlan;
  constraint_overlay_path?: string;
  view_proxy_path?: string;
  view_proxy_url?: string;
  view_proxy?: FloorplanViewProxy;
}

export interface FloorplanSuite {
  suite_id: string;
  analysis_id: string;
  status: "queued" | "running" | "waiting_anchor" | "done" | "partial" | "failed";
  stage: string;
  error: string;
  warnings: string[];
  floorplan_url: string;
  floor_url: string;
  floor_path: string;
  style_ref_url: string;
  anchor_room_id: string;
  anchor_result_id: string;
  anchor_url: string;
  generation_mode: "fast" | "consistent";
  model_key: "b2" | "pro";
  model_keys?: ("b2" | "pro")[];
  candidates_per_room: 2 | 3;
  estimated_images: number;
  estimated_model_calls?: number;
  estimated_cost: number;
  currency: string;
  rooms: FloorplanSuiteRoom[];
  annotation_revision: number;
}

export interface FloorplanSuiteSubmit {
  analysis_id: string;
  floor_path: string;
  style_ref_path?: string | null;
  prompt: string;
  style: string;
  lighting: string;
  generation_mode: "fast" | "consistent";
  model_key?: "b2" | "pro";
  model_keys: ("b2" | "pro")[];
  candidates_per_room: 2 | 3;
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  resolution: "2K" | "4K";
  camera_ids_by_room?: Record<string, string[]>;
}

export interface FloorplanDatasetSummary {
  eligible_floorplans: number;
  eligible_rooms: number;
  eligible_cameras: number;
}

// ── Whole-home v2: one metric model, interactive 3D cameras, G-buffer renders ──
export interface MetricXZ { x: number; z: number }
export interface MetricXYZ { x: number; y: number; z: number }

export interface CadRuntimeStatus {
  ready_for_dxf: boolean;
  ready_for_dwg: boolean;
  ezdxf_available: boolean;
  ezdxf_version: string;
  shapely_available: boolean;
  shapely_version: string;
  converter_available: boolean;
  commercial_use_authorized: boolean;
  converter_adapter: string;
  converter_license?: string;
  acadsharp_available?: boolean;
  oda_available?: boolean;
  converter_configuration: {
    path_env_names: string[];
    commercial_authorization_env: string;
  };
}

export interface CadUpload {
  path: string;
  name: string;
  url: string;
  format: "dwg" | "dxf";
  version: string;
  version_name: string;
  sha256: string;
  size_bytes: number;
  encoding?: "ascii" | "binary" | string;
}

export interface CadProvenance extends Record<string, unknown> {
  handle?: string;
  layer?: string;
  entity_type?: string;
  block_name?: string;
  insert_handle?: string;
  segment_index?: number;
}

export interface WholeHomeCadCandidatePlan {
  candidate_id: string;
  preview_path: string;
  preview_url: string;
  bbox_m: number[];
  length_m: number;
  bbox_area_m2: number;
  closed_region_count: number;
  score: number;
  selection_score?: number;
  context_insert_count?: number;
  semantic_anchor_count?: number;
  context_text_count?: number;
}

export interface WholeHomeCadEntityRoleSummary extends Record<string, unknown> {
  schema_version?: number;
  method?: string;
  input_entity_count?: number;
  retained_wall_entity_count?: number;
  opening_evidence_entity_count?: number;
  context_entity_count?: number;
  review_entity_count?: number;
  source_root_count?: number;
  role_counts?: Record<string, number>;
  confidence_counts?: Record<string, number>;
  reason_counts?: Record<string, number>;
}

export interface WholeHomeCadRawOpeningSummary extends Record<string, unknown> {
  schema_version?: number;
  method?: string;
  candidate_count?: number;
  accepted_count?: number;
  review_count?: number;
  rejected_count?: number;
  kind_counts?: Record<string, number>;
  reason_counts?: Record<string, number>;
}

export interface WholeHomeCadParseReport {
  schema_version?: number;
  source_path?: string;
  source_sha256?: string;
  report_path?: string;
  report_url?: string;
  insunits?: number;
  unit_scale_to_m?: number;
  chord_error_m?: number;
  inventory?: Record<string, number>;
  layers?: Record<string, number>;
  blocks?: Record<string, number>;
  layer_count?: number;
  block_count?: number;
  structural_entity_count?: number;
  selected_structural_entity_count?: number;
  ignored_nonstructural_count?: number;
  candidate_plans?: WholeHomeCadCandidatePlan[];
  selected_candidate_id?: string;
  selection_explanation?: string;
  alignment_metrics?: Record<string, unknown>;
  selected_entity_role_summary?: WholeHomeCadEntityRoleSummary;
  raw_opening_summary?: WholeHomeCadRawOpeningSummary;
  global_wall_topology?: WholeHomeGlobalWallTopology;
  hard_errors?: Array<Record<string, unknown> & { code?: string; message?: string }>;
  warnings?: Array<Record<string, unknown> & { code?: string; message?: string }>;
  hard_error_summary?: Array<Record<string, unknown> & { code?: string; message?: string }>;
  warning_summary?: Array<Record<string, unknown> & { code?: string; message?: string }>;
  validation?: {
    hard_errors?: Array<Record<string, unknown> & { code?: string; message?: string }>;
    warnings?: Array<Record<string, unknown> & { code?: string; message?: string }>;
    metrics?: Record<string, unknown>;
  };
}

export interface WholeHomeCadRawFace extends Record<string, unknown> {
  id?: string;
  face_id?: string;
  polygon?: MetricXZ[];
  points?: MetricXZ[];
  layer?: string;
  source_handles?: string[];
  disposition?: "physical_space_candidate" | "excluded" | string;
  manual_eligible?: boolean;
  filter_reasons?: string[];
}

export interface WholeHomeCadTextAnchor extends Record<string, unknown> {
  id?: string;
  anchor_id?: string;
  text: string;
  point?: MetricXZ;
  position?: MetricXZ;
  layer?: string;
}

export interface WholeHomeCadPhysicalSpace extends Record<string, unknown> {
  id: string;
  label: string;
  space_type: string;
  face_ids: string[];
  polygon: MetricXZ[];
  selected: boolean;
  floor_elevation_m?: number;
  ceiling_height_m?: number;
}

export interface WholeHomeCadSemanticZone extends Record<string, unknown> {
  id: string;
  physical_space_id: string;
  label: string;
  zone_type: string;
  geometry: {
    kind: "polygon" | "rectangle" | "split_halfplane" | string;
    points?: MetricXZ[];
    polygon?: MetricXZ[];
    start?: MetricXZ;
    end?: MetricXZ;
    side?: "left" | "right";
    min_x?: number;
    min_z?: number;
    max_x?: number;
    max_z?: number;
    [key: string]: unknown;
  };
}

/** Editable semantic interpretation of immutable CAD faces, with project-revision CAS. */
export interface WholeHomeCadSpaceDraft {
  project_id: string;
  revision: number;
  state_hash: string;
  physical_spaces: WholeHomeCadPhysicalSpace[];
  semantic_zones: WholeHomeCadSemanticZone[];
  excluded_face_ids: string[];
  raw_faces: WholeHomeCadRawFace[];
  text_anchors: WholeHomeCadTextAnchor[];
  space_confirmation?: WholeHomeCadSpaceConfirmation;
}

export interface WholeHomeCadSpaceDraftPut {
  base_revision: number;
  base_state_hash: string;
  editor_id: string;
  physical_spaces: WholeHomeCadPhysicalSpace[];
  semantic_zones: WholeHomeCadSemanticZone[];
  excluded_face_ids: string[];
  operation_id?: string;
}

export interface WholeHomeCadSpaceDraftPutResponse {
  project_id: string;
  revision: number;
  status: string;
  space_confirmation?: WholeHomeCadSpaceConfirmation;
  model_summary?: Record<string, unknown>;
}

export type WholeHomeCadReparseStatus = "queued" | "running" | "done" | "needs_review" | "failed" | "conflict" | "interrupted";

export interface WholeHomeCadReparseOperation extends Record<string, unknown> {
  operation_id: string;
  project_id?: string;
  status: WholeHomeCadReparseStatus | string;
  stage?: string;
  progress?: number;
  candidate_id?: string;
  error?: string | (Record<string, unknown> & { code?: string; message?: string });
  error_code?: string;
  failure_evidence?: Record<string, unknown>;
  result_revision?: number;
  created_at?: number;
  updated_at?: number;
}

export interface WholeHomeCadAiAdvisory extends Record<string, unknown> {
  schema_version: 1;
  advisory_id: string;
  project_id: string;
  base_revision: number;
  input_hash: string;
  authority: "advisory_only";
  geometry_mutated: false;
  revision_unchanged: true;
  call_cap: 2;
  call_count: number;
  passes: Array<Record<string, unknown>>;
  proposal: {
    summary?: string;
    orientation_assessment?: Record<string, unknown>;
    room_label_proposals?: Array<Record<string, unknown>>;
    wall_role_reviews?: Array<Record<string, unknown>>;
    opening_reviews?: Array<Record<string, unknown>>;
    risks?: Array<Record<string, unknown>>;
  };
  reference_validation: { status: "passed" | "needs_review"; issue_count: number };
  created_at: number;
}

export interface WholeHomeCadReparseSummary extends Record<string, unknown> {
  last_operation_id?: string;
  last_candidate_id?: string;
  last_status?: string;
  status?: string;
  stage?: string;
  progress?: number;
  candidate_id?: string;
  error_code?: string;
  error?: string;
  last_error?: string | (Record<string, unknown> & { code?: string; message?: string });
  last_failure?: string | (Record<string, unknown> & { code?: string; message?: string });
  failure_count?: number;
  updated_at?: number;
}

export interface WholeHomeCadSpaceConfirmation extends Record<string, unknown> {
  status?: string;
  revision?: number;
  editor_id?: string;
  physical_space_count?: number;
  semantic_zone_count?: number;
  updated_at?: number;
}

export interface WholeHomeCadSpaceDraftSummary extends Record<string, unknown> {
  revision?: number;
  state_hash?: string;
  status?: string;
  space_confirmation?: WholeHomeCadSpaceConfirmation;
  physical_space_count?: number;
  semantic_zone_count?: number;
  reason_codes?: string[];
  physical_facts_hash?: string;
  semantic_overlay_hash?: string;
  space_model_schema_version?: number;
}

export interface WholeHomeModelSummary extends Record<string, unknown> {
  wall_count?: number;
  opening_count?: number;
  physical_space_count?: number;
  semantic_zone_count?: number;
  room_count?: number;
  capture_count?: number;
  reference_contract_id?: string;
}

export interface WholeHomeReferenceSlot {
  slot_id: string;
  reference_image_id: string;
  room_profile: string;
  focal_length_mm: { min: number; max: number };
  must_show: string[];
  hard_constraints: string[];
  subject_safe_frame_overrides?: Record<string, {
    x_min: number; x_max: number; y_min: number; y_max: number;
  }>;
  reference_asset?: {
    asset_id?: string;
    filename?: string;
    url?: string;
    sha256?: string;
    hash?: string;
    resolved?: boolean;
    status?: "verified" | "unresolved" | "error" | string;
    width?: number;
    height?: number;
    mime?: string;
  };
  reference_asset_hash?: string;
  reference_viewpoint?: {
    scene_id: string;
    scene_name?: string;
    name?: string;
    point_mapping_status?: "not_available" | string;
    point_mapping?: {
      status: "not_available" | string;
      coordinate_system?: string;
      evidence?: string[];
      uncertainty?: string;
    };
    evidence?: Record<string, unknown> | string;
    landing_policy?: {
      mode: "cad_semantic_relative_region" | string;
      anchors?: string[];
      description?: string;
      source?: string;
      relative_landing_rule?: string;
      yaw?: string;
    };
  };
  must_show_text?: string[];
  anchor_groups?: Array<{
    subject: string;
    roles?: string[];
    opening_kinds?: string[];
    exact_count?: number;
  }>;
  must_validate?: string[];
}

export interface WholeHomeReferenceContract {
  schema_version: number;
  contract_id: string;
  title: string;
  reference_role: string;
  geometry_authority: "cad" | string;
  output: {
    mode: "static" | string;
    aspect_ratio: "4:3" | string;
    resolution: "4K" | string;
    panorama: boolean;
  };
  camera: {
    eye_height_m: { min: number; max: number };
    focal_length_mm: { min: number; max: number };
    vertical_deviation_deg_max: number;
    safe_frame: { x_min: number; x_max: number; y_min: number; y_max: number };
  };
  global_hard_constraints: string[];
  style_contract: Record<string, unknown>;
  slots: WholeHomeReferenceSlot[];
}

export interface WholeHomeWall {
  id: string;
  wall_assembly_id?: string;
  start: MetricXZ;
  end: MetricXZ;
  thickness_m: number;
  height_m: number;
  kind: "exterior" | "interior" | "partition";
  source: "ai" | "human" | "ai_edited" | "imported" | "cad" | "cad_review_evidence";
  confidence: number;
  boundary_kind?: string;
  review_status?: "needs_review" | "accepted" | "rejected";
  display_mode?: "review_floor_trace";
  cad_provenance?: CadProvenance;
}

export interface WholeHomeGlobalWallFootprint {
  id: string;
  points: MetricXZ[];
  interior_rings: MetricXZ[][];
  floor_elevation_m: number;
  height_m: number;
  source: "cad_global_topology" | string;
  review_status: "needs_review" | "accepted" | "rejected";
  source_representation: "global_wall_footprint" | string;
  source_entity_handles?: string[];
  cad_provenance?: Record<string, unknown>;
}

export interface WholeHomeGlobalWallTopology {
  schema_version: number;
  method: string;
  status: string;
  source_segment_count: number;
  source_entity_count: number;
  source_length_m: number;
  source_coverage_ratio: number;
  wall_close_radius_m: number;
  inferred_single_run_width_m: number;
  topology_close_radius_m: number;
  wall_footprint_count: number;
  wall_area_m2: number;
  wall_component_count: number;
  wall_interior_ring_count: number;
  space_candidate_count: number;
  [key: string]: unknown;
}

export interface WholeHomeOpening {
  id: string;
  wall_id: string;
  wall_assembly_id?: string;
  kind: "door" | "window" | "open_connection";
  offset_m: number;
  width_m: number;
  height_m: number;
  sill_height_m: number;
  source: "ai" | "human" | "ai_edited" | "imported" | "cad";
  confidence: number;
  review_status: "pending" | "accepted" | "rejected";
  width_source?: string;
  height_source?: string;
  sill_height_source?: string;
  reference_anchor_ready?: boolean;
  reference_anchor_blockers?: string[];
  rotation_y_deg?: number;
  insert_scale?: { x: number; y: number };
  cad_provenance?: CadProvenance;
  duplicate_of?: string;
  opening_deduplication?: {
    method: string;
    duplicate_of: string;
    overlap_m: number;
    action: string;
    reason: string;
  };
  opening_topology_review?: {
    method: string;
    status: "manual_review_required" | string;
    code?: string;
    room_ids: string[];
    room_profiles: string[];
    samples?: Array<{
      label: string;
      along_m: number;
      negative_room_ids: string[];
      positive_room_ids: string[];
      point: MetricXZ;
    }>;
    reason: string;
  };
}

export interface WholeHomeRoom {
  id: string;
  label: string;
  room_type: string;
  polygon: MetricXZ[];
  area_m2: number;
  floor_elevation_m: number;
  ceiling_height_m: number;
  selected: boolean;
  source: "ai" | "human" | "ai_edited" | "imported" | "cad";
  confidence: number;
  semantic_profile: "kitchen" | "bathroom" | "bedroom" | "living_room" | "foyer" | "balcony" | "other";
  semantic_status: "pending" | "complete" | "needs_review";
  reference_room_profile?: string;
  cad_provenance?: CadProvenance;
}

export interface WholeHomeObject {
  id: string;
  name: string;
  kind: string;
  position: MetricXYZ;
  size: MetricXYZ;
  rotation_y_deg: number;
  room_id: string;
  source: "ai" | "human" | "ai_edited" | "imported" | "cad";
  confidence: number;
  semantic_role: string;
  purpose: "observed_architecture" | "layout_proxy";
  observed: boolean;
  review_status: "pending" | "accepted" | "rejected";
  blocks_camera: boolean;
  required_for_camera: boolean;
  clearance_m: number;
  semantic_acceptance?: { method: string; status: string; scope: string; accepted_at: number };
  cad_provenance?: CadProvenance;
  size_source?: string;
  height_source?: string;
  insert_position?: MetricXYZ;
  insert_scale?: { x: number; y: number };
  cad_world_bbox_m?: number[];
  cad_local_bbox_m?: number[];
  rotation_source?: string;
  reference_anchor_ready?: boolean;
  reference_anchor_blockers?: string[];
  room_match_ids?: string[];
}

export interface WholeHomeCamera {
  id: string;
  name: string;
  position: MetricXYZ;
  target: MetricXYZ;
  focal_length_mm: number;
  /** 透视相机缺省;球面热点 capture 时后端按 projection 区分渲染路径。 */
  projection?: "equirectangular" | "perspective";
  room_id: string;
  enabled: boolean;
  source: "human_3d" | "imported" | "manual" | "auto_geometry" | "ai_selected";
  auto_plan_id?: string;
  candidate_id?: string;
  local_score?: number;
  selection_score?: number;
  selection_reason?: string;
  pool_rank?: 1 | 2 | 3;
  is_primary?: boolean;
  origin_scope?: "inside_room" | "adjacent_portal" | "doorway_inside" | "cad_semantic_adjacent_free_space";
  portal_opening_id?: string;
  entry_opening_id?: string;
  reference_slot_id?: string;
  reference_proposal_id?: string;
  reference_proposal_hash?: string;
  scene_recipe_id?: string;
  scene_hash?: string;
  reference_contract_validation?: {
    version: number;
    slot_id: string;
    scene_id: string;
    room_id: string;
    landing_policy_mode: string;
    landing_source?: string;
    yaw_source: string;
    cad_position_pass: boolean;
    collision_pass: boolean;
    visibility_pass: boolean;
    projection_method: string;
    width: number;
    height: number;
    pixel_origin: "top-left" | string;
    buffer_sha: string;
    pixel_gate_version?: string;
    safe_frame_status: "pending_browser" | "pass" | "blocked" | string;
    safe_frame_pass: boolean | null;
    proposal_id?: string;
    proposal_hash?: string;
    must_show_subjects: Array<{
      subject: string;
      anchor_id: string;
      anchor_kind: "fixed_object" | "opening" | string;
      role: string;
      position?: { x: number; z: number };
    }>;
    must_validate: Record<string, boolean>;
    must_show_bounds: Array<{
      subject: string;
      anchor_id?: string;
      pixel_count?: number;
      x_min: number;
      x_max: number;
      y_min: number;
      y_max: number;
    }>;
  };
  origin_room_ids?: string[];
  render_gate?: {
    version: string;
    pass: boolean;
    status: "pass" | "blocked";
    profile: "kitchen" | "bathroom" | "bedroom" | "living_room" | "foyer" | "balcony" | "other";
    floor_fraction: number;
    wall_fraction: number;
    peak_semantic_role: string;
    peak_semantic_role_fraction: number;
    semantic_role_fractions: Record<string, number>;
    required_groups: Array<{
      key: string;
      passed: boolean;
      roles: string[];
      minimum_fraction?: number;
      minimum_count?: number;
      passing_roles: string[];
    }>;
    reasons: string[];
    denominator_pixels: number;
    matched_pixels: number;
    unmatched_pixels: number;
  };
}

export interface WholeHomeSubjectIdLegendEntry {
  subject: string;
  anchor_id: string;
  anchor_kind: "fixed_object" | "opening" | string;
  role: string;
  color: [number, number, number];
}

export interface WholeHomeSubjectIdLegend {
  version: "whole-home-subject-id-v1" | string;
  pixel_origin: "top-left";
  subjects: WholeHomeSubjectIdLegendEntry[];
}

export type WholeHomeCameraRejectionSummary = Record<string, unknown>;

export interface WholeHomeCameraCandidate {
  candidate_id: string;
  room_id: string;
  room_label: string;
  local_score: number;
  origin_scope: "inside_room" | "adjacent_portal" | "doorway_inside" | "cad_semantic_adjacent_free_space";
  portal_opening_id?: string;
  entry_opening_id?: string;
  origin_room_ids?: string[];
  metrics: Record<string, unknown> & { render_gate?: WholeHomeCamera["render_gate"] };
  camera: WholeHomeCamera;
  preview_data_url?: string;
  preview_path?: string;
  preview_url?: string;
  slot_id?: string;
  reference_slot_id?: string;
  pool_scope?: "reference_slot" | string;
  proposal_id?: string;
  proposal_hash?: string;
  reference_contract_validation?: WholeHomeCamera["reference_contract_validation"];
}

export interface WholeHomeAutoCameraSelection {
  candidate_id: string;
  room_id: string;
  rank: number;
  visual_score: number;
  reason: string;
  strengths: string[];
  risks: string[];
  selection_source: "gemini" | "local_fallback";
}

export interface WholeHomeAutoCameraPlan {
  plan_id: string;
  project_id: string;
  status: "done";
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  shots_per_room: 1 | 2;
  summary: string;
  ai_model: string;
  ai_error: string;
  candidates: WholeHomeCameraCandidate[];
  contact_sheets: Array<{ room_id: string; room_label: string; path: string; url: string; candidate_ids: string[] }>;
  selections: WholeHomeAutoCameraSelection[];
  selected_cameras: WholeHomeCamera[];
  room_pools: WholeHomeCameraRoomPool[];
  created_at: number;
}

export interface WholeHomeCameraRoomPool {
  room_id: string;
  room_label: string;
  status: "ready" | "blocked";
  reasons: string[];
  candidate_ids: string[];
  primary_candidate_id?: string;
  rejection_summary?: WholeHomeCameraRejectionSummary;
  slot_id?: string;
  pool_scope?: "reference_slot" | string;
  candidates?: WholeHomeCameraCandidate[];
  hard_errors?: Array<Record<string, unknown> & { code?: string; message?: string }>;
  focal_samples_mm?: number[];
  inset_m?: number;
}

export interface WholeHomeCameraCandidateProposal {
  status: "ready" | "partial" | "blocked";
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  candidates: WholeHomeCameraCandidate[];
  room_pools: WholeHomeCameraRoomPool[];
  blocked_rooms: WholeHomeCameraRoomPool[];
  rejection_summary: Record<string, WholeHomeCameraRejectionSummary>;
  mode?: "room" | "reference";
  pool_scope?: "reference_slot";
  contract_id?: string;
  proposal_id?: string;
  proposal_hash?: string;
  project_revision?: number;
  cad_facts_hash?: string;
  model_facts_hash?: string;
  slot_pools?: WholeHomeCameraRoomPool[];
  hard_errors?: Array<Record<string, unknown> & { code?: string; message?: string }>;
}

export interface WholeHomeReferenceCaptureBatch {
  batch_id: string;
  proposal_id: string;
  proposal_hash: string;
  renderer: "numpy_zbuffer_v1" | string;
  width: number;
  height: number;
  saved: Array<{ slot_id: string; candidate_id: string; capture_id: string }>;
  skipped: Array<{ slot_id: string; reason: string }>;
  blocked: Array<{
    slot_id: string;
    reason: string;
    attempts: Array<Record<string, unknown> & { candidate_id?: string; pass?: boolean }>;
  }>;
  status: "ready" | "partial" | "blocked";
  paid_calls: 0;
  created_at: number;
}

export interface WholeHomeReferenceCaptureBatchResponse {
  batch: WholeHomeReferenceCaptureBatch;
  project: WholeHomeProject;
}

export interface WholeHomeGeometryIssue {
  code: string;
  message: string;
  wall_id?: string;
  opening_id?: string;
  room_id?: string;
  point?: MetricXZ;
  start?: MetricXZ;
  end?: MetricXZ;
  length_m?: number;
}

export interface WholeHomeGeometryMeshPart {
  id: string;
  indices: number[];
  entity_id?: string;
  semantic_kind?: string;
  render_role?: string;
  source_kind?: string;
  [key: string]: unknown;
}

export interface WholeHomeGeometryManifest {
  version: 1;
  project_id: string;
  model_revision: number;
  model_facts_hash: string;
  registration_hash: string;
  geometry_kernel_version: string;
  units: "meter";
  coordinate_system: string;
  vertices: [number, number, number][];
  wall_parts: WholeHomeGeometryMeshPart[];
  floor_parts: WholeHomeGeometryMeshPart[];
  ceiling_parts?: WholeHomeGeometryMeshPart[];
  object_parts?: WholeHomeGeometryMeshPart[];
  opening_voids: Array<Record<string, unknown> & { id: string }>;
  manifest_hash: string;
}

export interface WholeHomeGeometryContract {
  required: boolean;
  input_grade: "vector_authoritative" | "raster_draft" | "raster_human_locked" | "legacy_unproven";
  registration: Record<string, unknown> & { registration_hash?: string; source_hash?: string };
  raster_alignment_metrics?: {
    wall_axis_count?: number;
    wall_sample_count?: number;
    wall_ink_support_ratio?: number;
    wall_centerline_p95_px?: number;
    wall_centerline_p95_m?: number;
  };
  manifest: {
    manifest_hash: string;
    model_facts_hash: string;
    geometry_kernel_version: string;
    vertex_count: number;
    wall_part_count: number;
    floor_part_count: number;
    opening_void_count: number;
  };
  acceptance: Record<string, unknown> & {
    report_id?: string;
    report_hash?: string;
    status?: "passed" | "needs_human_review" | "blocked" | "stale";
    issues?: Array<Record<string, unknown> & { code: string; message: string; severity: string }>;
  };
  production_readiness: {
    ready: boolean;
    code: string;
    input_grade: string;
    report_status: string;
    reasons: Array<Record<string, unknown> & { code: string; message: string; severity: string }>;
  };
}

export interface WholeHomeModel {
  schema_version: 2;
  model_id: string;
  coordinate_system: "metres-y-up" | "right-handed-y-up-x-east-z-south-v2";
  coordinate_contract_version?: 2;
  width_m: number;
  depth_m: number;
  wall_height_m: number;
  wall_thickness_m: number;
  scale: { status: string; method: string; reference_length_m?: number; evidence?: string };
  walls: WholeHomeWall[];
  openings: WholeHomeOpening[];
  rooms: WholeHomeRoom[];
  physical_spaces?: WholeHomeCadPhysicalSpace[];
  semantic_zones?: WholeHomeCadSemanticZone[];
  fixed_objects: WholeHomeObject[];
  cameras: WholeHomeCamera[];
  uncertainties: string[];
  room_contracts: Array<{
    room_id: string;
    profile: string;
    required_role_groups: string[][];
    preferred_roles: string[];
    min_visible_groups: number;
    source: string;
    status: "pending" | "complete" | "needs_review" | "blocked";
    assumptions?: string[];
    reference_room_profile?: string;
    missing_role_groups?: string[][];
  }>;
  semantic_report: {
    status: "complete" | "needs_review";
    hard_errors: Array<{ code: string; message: string; room_id?: string; object_id?: string; opening_id?: string; role_group?: string[] }>;
    warnings: Array<{ code: string; message: string; room_id?: string; object_id?: string }>;
    checked_at?: number;
    audit_passes?: number;
  };
  geometry_report: { hard_errors: WholeHomeGeometryIssue[]; warnings: WholeHomeGeometryIssue[]; checked_at?: number; image_alignment_score?: number; audit_passes?: number };
  cad_facts_hash?: string;
  cad_to_model?: Record<string, unknown>;
  model_to_cad?: Record<string, unknown>;
  reference_anchor_report?: {
    status: "ready" | "blocked" | string;
    hard_errors: Array<Record<string, unknown> & { code?: string; message?: string }>;
    source?: string;
  };
  geometry_schema_version?: 3;
  input_grade?: WholeHomeGeometryContract["input_grade"];
  model_facts_hash?: string;
  geometry_manifest?: WholeHomeGeometryManifest;
  source_registration?: Record<string, unknown>;
  wall_assemblies?: Array<Record<string, unknown> & { id: string }>;
  global_wall_footprints?: WholeHomeGlobalWallFootprint[];
  global_wall_topology?: WholeHomeGlobalWallTopology;
}

export interface WholeHomeCapture {
  capture_id: string;
  camera_id: string;
  camera: WholeHomeCamera;
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  rgb_path: string;
  rgb_url: string;
  depth_path: string;
  depth_url: string;
  normal_path: string;
  normal_url: string;
  edge_path: string;
  edge_url: string;
  semantic_path: string;
  semantic_url: string;
  semantic_legend: Record<string, string>;
  subject_id_path?: string;
  subject_id_url?: string;
  subject_id_legend?: WholeHomeSubjectIdLegend;
  plan_overlay_path: string;
  plan_overlay_url: string;
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
  source_hash: string;
  status: "confirmed" | "stale";
  created_at: number;
}

// ── 定点球面全景(equirectangular / cubemap,文档 §10 数据合同)──────────
export type PanoProjection = "equirectangular";
export type PanoCubeFaceOrder = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"];

export interface WholeHomePanoHotspot {
  pano_id: string;
  camera: WholeHomeCamera;
  camera_center_m: MetricXYZ;
  projection: PanoProjection;
  canonical_forward: string;
  heading_deg: number;
  pitch_deg: number;
  roll_deg: number;
  room_id: string;
  status: "confirmed" | "stale";
}

export interface WholeHomePanoChannelFiles {
  rgb_erp?: string;
  depth_erp?: string;
  normal_erp?: string;
  edge_erp?: string;
  semantic_erp?: string;
  subject_id_erp?: string;
  [channel: string]: string | undefined;
}

export interface WholeHomePanoManifest {
  schema_version: number;
  capture_id: string;
  capture_revision: number;
  pano_id: string;
  projection: PanoProjection;
  coordinate_system: "right-handed-y-up";
  camera_center_m: MetricXYZ;
  canonical_forward: string;
  heading_deg: number;
  pitch_deg: number;
  roll_deg: number;
  horizontal_fov_deg: number;
  vertical_fov_deg: number;
  erp_width: number;
  erp_height: number;
  cube_face_size: number;
  cube_face_order: PanoCubeFaceOrder;
  near_m: number;
  far_m: number;
  depth_encoding: "linear_metric_global_range";
  normal_encoding: "world_space_xyz_to_rgb";
  model_facts_hash: string;
  material_graph_hash: string;
  lighting_hash: string;
  render_contract: Record<string, unknown>;
  channels: WholeHomePanoChannelFiles;
  channel_hashes: Record<string, string>;
  source_hash: string;
  scene_recipe_id?: string;
  scene_hash?: string;
}

export interface WholeHomePanoCapture {
  capture_id?: string;
  capture_revision?: number;
  active?: boolean;
  pano_id: string;
  scene_recipe_id?: string;
  scene_hash?: string;
  manifest: WholeHomePanoManifest;
  rgb_url: string;
  depth_url: string;
  normal_url: string;
  edge_url: string;
  semantic_url: string;
  subject_id_url?: string;
  channel_urls?: Record<string, string>;
  edited_rgb_url?: string;
  repaired_rgb_url?: string;
  edit_engine?: "gpt-image-2" | "flux-canny" | string;
  status: "confirmed" | "stale" | "superseded" | "editing" | "edited" | "repaired" | "gated" | "gate_failed" | "accepted" | "review_failed";
  gate?: Record<string, unknown>;
  edited_at?: number;
  repaired_at?: number;
  gated_at?: number;
  created_at: number;
}

export interface WholeHomePanoPaidPreview {
  policy: "pano_paid_preview_v1" | string;
  preview_id: string;
  preview_sha256: string;
  confirmation_phrase: string;
  project_id: string;
  pano_id: string;
  source_hash: string;
  provider: "fal" | "openai";
  engine: "gpt-image-2" | "flux-canny" | string;
  endpoint: string;
  model_id: string;
  snapshot_locked: boolean;
  output_size: string;
  edit_prompt_sha256: string;
  repair_band_deg: number;
  created_at: number;
  expires_at: number;
  generation_params?: Record<string, number | string | boolean>;
  resume_only?: boolean;
  resume_request_id?: string;
  caps: { edit_calls: number; repair_calls: number };
}

export interface WholeHomePanoGate {
  gate_pass: boolean;
  full_contract_pass: boolean;
  gate_level: "p0_rgb_structural" | string;
  not_evaluable: string[];
  version: string;
  hard_fail: boolean;
  summary: string;
  failures: string[];
  checks: Array<Record<string, unknown> & { check_id: string; status: "pass" | "fail" | "skipped" }>;
}

export interface WholeHomeProfessionalCapabilities {
  version: 1;
  product_mode: "raster_first_renovation_sales_proposal";
  primary_inputs: string[];
  advanced_inputs: string[];
  delivery_scope: "marketing_concept_only";
  human_review_target_minutes: { median: number; p90: number };
  scene_variants: 3;
  panorama_target: { minimum: number; maximum: number; width: number; height: number };
  output_grades: Array<"certified_master" | "ai_enhanced_derivative">;
  cost_budget_cny: { minimum: number; maximum: number };
  construction_or_pricing_authority: false;
  style_packs: Array<Record<string, unknown> & { style_pack_id: string; title: string }>;
}

export interface WholeHomeFloorplanGraph {
  version: "floorplan-graph-v1";
  project_id: string;
  project_revision: number;
  coordinate_system: "metres-y-up";
  plan_plane: "x-z";
  topdown_camera_contract: { position_axis: "+Y"; view_direction: "-Y"; screen_up: "-Z" };
  source: { source_type: string; input_grade: string; registration_hash: string; source_hash: string; model_facts_hash: string };
  extent_m: { width: number; depth: number };
  walls: Array<{ id: string; start: MetricXZ; end: MetricXZ; thickness_m: number; height_m: number; kind: string }>;
  rooms: Array<{ id: string; label: string; room_type: string; polygon: MetricXZ[]; area_m2: number; selected: boolean }>;
  openings: Array<{ id: string; wall_id: string; kind: string; offset_m: number; width_m: number }>;
  review: { status: "draft" | "reviewed" | "locked"; scale_locked: boolean; geometry_verified: boolean; unresolved_ids: string[]; uncertainties: string[] };
  graph_hash: string;
}

export interface WholeHomeConstructionProfile {
  version: "construction-profile-v1";
  project_id: string;
  project_revision: number;
  status: "assumptions_pending" | "confirmed";
  fields: Record<string, { value: number; source: string; confirmed: boolean }>;
  reviewer: string;
  confirmed_at: number | null;
  profile_hash: string;
}

export interface WholeHomeSceneInstance {
  instance_id: string;
  asset_id: string;
  room_id: string;
  semantic_role: string;
  transform: {
    position_m: MetricXYZ;
    rotation_y_deg: number;
    scale: [number, number, number];
  };
  size_m: { width: number; depth: number; height: number };
  footprint_m: { width: number; depth: number };
  placement_source: string;
}

export interface WholeHomeSceneRecipe {
  version: "scene-recipe-v1";
  recipe_id: string;
  recipe_hash: string;
  scene_hash: string;
  project_id: string;
  project_revision: number;
  floorplan_graph_hash: string;
  construction_profile_hash: string;
  style_pack_id: "modern_warm_natural_v1";
  variant_index: 1 | 2 | 3;
  status: "draft" | "reviewed" | "locked" | "superseded";
  delivery_scope: "marketing_concept_only";
  instances: WholeHomeSceneInstance[];
  rooms: Array<{ room_id: string; room_type: string; instance_count: number; blocking_issue_count: number }>;
  materials: Record<string, string>;
  lighting: Record<string, string | number>;
  quality: {
    status: "passed" | "needs_review";
    score: number;
    blocking_issues: Array<Record<string, unknown> & { code: string }>;
    warnings: Array<Record<string, unknown> & { code: string }>;
    instance_count: number;
  };
  created_at: number;
  review: { reviewer: string; note: string; reviewed_at: number | null };
}

export interface WholeHomeMarketingProposal {
  version: "marketing-proposal-v1";
  project_id: string;
  project_revision: number;
  scene_recipe_id: string;
  scene_hash: string;
  status: "draft" | "ready";
  audience: "renovation_sales_lead";
  deliverables: {
    certified_master_panoramas: string[];
    ai_enhanced_derivatives: string[];
    required_panorama_count: { minimum: number; maximum: number };
    hero_stills: string[];
    tour_manifest: string | null;
    share_link: string | null;
    qr_code: string | null;
  };
  blockers: string[];
  disclaimers: string[];
  proposal_hash: string;
}

export interface WholeHomeProject {
  project_id: string;
  source_type?: "floorplan" | "import" | "cad";
  status: "queued" | "analyzing" | "parsing_cad" | "needs_review" | "done" | "geometry_accepted" | "verified" | "failed" | "history_restored" | "history_revalidation_required";
  stage: string;
  error: string;
  summary: string;
  floorplan_path: string;
  floorplan_url: string;
  source_analysis_id: string;
  pano_captures?: WholeHomePanoCapture[];
  pano_hotspots?: WholeHomePanoHotspot[];
  cad_path?: string;
  cad_geometry_read_only?: boolean;
  cad_source?: {
    path?: string;
    name?: string;
    sha256: string;
    format: string;
    version: string;
    size_bytes?: number;
    converted_dxf_path?: string;
  };
  cad_import?: {
    schema_version: number;
    cad_facts_hash: string;
    cad_to_model: Record<string, unknown>;
    model_to_cad: Record<string, unknown>;
    provenance_required: boolean;
    derivation_coverage_required: number;
  };
  cad_ai_advisories?: WholeHomeCadAiAdvisory[];
  cad_error?: Record<string, unknown> & { code?: string; message?: string };
  cad_reparse_summary?: WholeHomeCadReparseSummary;
  cad_space_confirmation?: WholeHomeCadSpaceConfirmation;
  cad_space_draft?: WholeHomeCadSpaceDraftSummary;
  model_summary?: WholeHomeModelSummary;
  cad_candidate_model?: WholeHomeModel;
  parse_report?: WholeHomeCadParseReport;
  reference_url?: string;
  reference_contract?: WholeHomeReferenceContract;
  model: WholeHomeModel;
  revision: number;
  verified: boolean;
  verified_revision: number;
  geometry_contract?: WholeHomeGeometryContract;
  captures: WholeHomeCapture[];
  auto_camera_plans?: WholeHomeAutoCameraPlan[];
  reference_camera_proposals?: WholeHomeCameraCandidateProposal[];
  reference_software_capture_batches?: WholeHomeReferenceCaptureBatch[];
  learning?: WholeHomeProjectLearning;
  ai_model: string;
  created_at: number;
  updated_at: number;
  history_read_only?: boolean;
  history_snapshot_id?: string;
  lineage?: WholeHomeProjectLineage;
  generation_draft?: WholeHomeGenerationDraft;
  construction_profile?: WholeHomeConstructionProfile;
  scene_recipes?: WholeHomeSceneRecipe[];
  active_scene_recipe_id?: string;
  professional_revision?: number;
  professional?: {
    product_mode: "raster_first_renovation_sales_proposal";
    professional_revision: number;
    construction_profile_status: string;
    scene_recipe_count: number;
    active_scene_recipe_id: string;
    active_scene_status: string;
    marketing_proposal_status: string;
  };
}

export interface WholeHomeProjectLineage {
  root_project_id: string;
  parent_project_id: string;
  source_project_id: string;
  source_run_id: string;
  source_snapshot_id: string;
  source_revision: number;
  source_model_hash: string;
  branch_kind: "history_fork";
  branch_name: string;
  created_at: number;
}

export interface WholeHomeGenerationDraft {
  draft_version: number;
  source_run_id: string;
  variant_label: string;
  style: string;
  lighting: string;
  prompt: string;
  material_mode: "floor_sample";
  floor_path: string;
  floor_url?: string;
  style_ref_path: string;
  style_ref_url?: string;
  model_keys: ("b2" | "pro")[];
  selected_artifact_ids: string[];
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  resolution: "2K";
  updated_at: number;
  last_committed_batch_id: string;
}

export type WholeHomeHumanReviewStatus = "unreviewed" | "pass" | "backup" | "reject";
export type WholeHomeRoundStatus = "working" | "awaiting_human_review" | "review_complete" | "review_not_required";

export interface WholeHomeHumanReviewEvent {
  event_id: string;
  seq: number;
  at: number;
  run_id: string;
  project_id: string;
  result_id: string;
  artifact_id: string;
  review_status: WholeHomeHumanReviewStatus;
  review_tags: string[];
  review_note: string;
  reviewer_id: string;
  recipe_path?: string;
}

export interface WholeHomeReviewableArtifact {
  artifact_id: string;
  result_id: string;
  room_id: string;
  model_key: "b2" | "pro" | string;
  attempt_id: string;
  material_attempt_id: string;
  path: string;
  url: string;
  thumb: string;
  auto_outcome: string;
  auto_deliverable: boolean;
  material_status: string;
  review_status: WholeHomeHumanReviewStatus;
  human_review: WholeHomeHumanReviewEvent | null;
}

export interface WholeHomeReviewState {
  run_id: string;
  project_id: string;
  run_status: string;
  round_status: WholeHomeRoundStatus;
  requires_human_review: boolean;
  reviewable_count: number;
  pending_count: number;
  pending_artifact_ids: string[];
  can_complete: boolean;
  completed_at: number | null;
  completion_event_id: string;
  review_version: number;
  counts: Record<WholeHomeHumanReviewStatus, number>;
  reviewables: WholeHomeReviewableArtifact[];
  event_count: number;
}

export interface WholeHomeTrainingConsent {
  schema_version: number;
  project_id: string;
  allowed: boolean;
  updated_at: number | null;
  updated_by: string;
  events?: Array<Record<string, unknown>>;
}

export interface WholeHomeProjectLearning {
  training_consent: WholeHomeTrainingConsent;
  counts: Record<WholeHomeHumanReviewStatus, number>;
  covered_room_ids: string[];
  uncovered_room_ids: string[];
  covered_room_count: number;
  selected_room_count: number;
}

export interface WholeHomeLearningSummary extends WholeHomeProjectLearning {
  project_id: string | null;
  strong_label_count: number;
  weak_unreviewed_result_count: number;
  auto_signals: Record<string, number>;
  by_room: Record<string, Record<WholeHomeHumanReviewStatus, number>>;
  by_model: Record<string, Record<WholeHomeHumanReviewStatus, number>>;
  selected_room_ids: string[];
  run_count: number;
  workflow_summaries: Array<{
    workflow_id: string;
    generation_spec_hash: string;
    covered_room_ids: string[];
    covered_room_count: number;
    latest_created_at: number;
  }>;
  active_workflow_id: string | null;
  active_generation_spec_hash: string | null;
}

export interface WholeHomeReviewMutationResponse {
  event: WholeHomeHumanReviewEvent;
  review_state: WholeHomeReviewState;
}

export interface WholeHomeEvaluation {
  status: "done" | "unavailable";
  phase?: "structure" | "final";
  geometry_score?: number;
  camera_score?: number;
  opening_score?: number;
  material_score?: number;
  room_identity_score?: number;
  fixed_object_score?: number;
  total: number | null;
  hard_fail: boolean;
  verification_incomplete?: boolean;
  gate_pass?: boolean;
  eligible_for_recommendation?: boolean;
  summary: string;
  checks: Array<{ constraint_id?: string; category?: string; constraint: string; status: "pass" | "fail" | "uncertain"; severity: "hard" | "soft"; evidence: string }>;
}

export interface WholeHomeQaAttempt {
  attempt: number;
  at: number;
  seconds: number;
  status: string;
  error: string;
}

export interface WholeHomeQaHistoryEntry {
  batch_id: string;
  at: number;
  previous_evaluation?: WholeHomeEvaluation | null;
  previous_error?: string;
  attempts?: WholeHomeQaAttempt[];
}

export interface WholeHomeGenerationTrace {
  call_id?: string;
  pass: "structure" | "material" | string;
  attempt_id?: string;
  provider: string;
  model_id?: string;
  resolution?: string;
  prompt_sha256?: string;
  seconds: number;
  success: boolean;
  error: string;
}

export interface WholeHomeLocalGate {
  version: string;
  phase: "structure" | "final";
  status: "done" | "unavailable" | string;
  verdict: "pass" | "fail";
  gate_pass: boolean;
  summary: string;
  thresholds: Record<string, number>;
  missing_buffers: string[];
  invalid_buffers: string[];
  overlay_path: string;
  overlay_url?: string;
  normal_coverage_12?: number | null;
  normal_mean_distance?: number | null;
  semantic_coverage_12?: number | null;
  semantic_mean_distance?: number | null;
  structure_coverage_12?: number | null;
  structure_mean_distance?: number | null;
}

export interface WholeHomeMaterialAttempt {
  material_attempt_id: string;
  attempt_index: number;
  trigger: string;
  status: string;
  api_original_path: string;
  api_original_url?: string;
  material_path: string;
  material_url?: string;
  corrected_path: string;
  corrected_url?: string;
  final_path: string;
  final_url?: string;
  final_local_gate?: WholeHomeLocalGate | null;
  evaluation: WholeHomeEvaluation | null;
  evaluation_error?: string;
  qa_attempts?: WholeHomeQaAttempt[];
  trace: WholeHomeGenerationTrace[];
}

export interface WholeHomeGenerationAttempt {
  attempt_id: string;
  attempt_index: number;
  trigger: "primary" | "primary_repair" | "backup_1" | "backup_2" | string;
  capture_id: string;
  camera_id: string;
  camera_name: string;
  status: string;
  structure_path: string;
  structure_url?: string;
  structure_local_gate?: WholeHomeLocalGate | null;
  structure_evaluation: WholeHomeEvaluation | null;
  structure_evaluation_error?: string;
  structure_qa_attempts?: WholeHomeQaAttempt[];
  material_attempts: WholeHomeMaterialAttempt[];
  trace: WholeHomeGenerationTrace[];
}

export interface WholeHomeResult {
  result_id: string;
  room_id?: string;
  capture_ids?: string[];
  capture_id: string;
  camera_id: string;
  camera_name: string;
  model_key: "b2" | "pro";
  candidate_index: number;
  status: "queued" | "running" | "done" | "failed";
  outcome?: "queued" | "structure_running" | "material_running" | "accepted" | "structure_rejected" | "material_rejected" | "qa_unavailable" | "cancelled" | "failed" | string;
  deliverable?: boolean;
  selected_attempt_id?: string;
  stage: string;
  error: string;
  path: string;
  url: string;
  thumb: string;
  structure_path: string;
  structure_url: string;
  api_original_path?: string;
  api_original_url?: string;
  material_path?: string;
  material_url?: string;
  corrected_path?: string;
  corrected_url?: string;
  final_path: string;
  final_url: string;
  evaluation: WholeHomeEvaluation | null;
  evaluation_error?: string;
  qa_attempts?: WholeHomeQaAttempt[];
  qa_history?: WholeHomeQaHistoryEntry[];
  attempts?: WholeHomeGenerationAttempt[];
  trace?: WholeHomeGenerationTrace[];
  seconds?: number;
}

export interface WholeHomeCaptureGroup {
  room_id: string;
  slot_id?: string;
  primary_capture_id: string;
  fallback_capture_ids: string[];
}

export interface WholeHomeRun {
  run_id: string;
  project_id: string;
  status: "queued" | "running" | "done" | "partial" | "failed";
  stage: string;
  error: string;
  floorplan_url: string;
  floor_url: string;
  floor_path: string;
  material_mode?: "floor_sample" | "reference" | "style_pack";
  scene_recipe_id?: string;
  scene_hash?: string;
  scene_recipe_snapshot?: WholeHomeSceneRecipe;
  reference_contract_id?: string;
  reference_contract_snapshot?: WholeHomeReferenceContract;
  benchmark_batch_id?: string;
  style_ref_url: string;
  style_ref_path?: string;
  prompt: string;
  style: string;
  lighting: string;
  model_keys: ("b2" | "pro")[];
  candidates_per_camera: 1 | 2;
  capture_groups?: WholeHomeCaptureGroup[];
  aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
  resolution: "2K" | "4K";
  estimated_model_calls: number;
  estimated_minimum_model_calls?: number;
  estimated_qa_calls?: number;
  actual_generation_calls?: number;
  actual_qa_calls?: number;
  actual_local_gate_calls?: number;
  successful_generation_calls?: number;
  review_manifest_path?: string;
  workflow_id?: string;
  parent_run_id?: string;
  variant_group_id?: string;
  variant_of_run_id?: string;
  variant_batch_id?: string;
  variant_label?: string;
  variant_index?: number;
  replay_snapshot_ref?: { schema_version: number; snapshot_id: string; snapshot_hash: string };
  model_revision?: number;
  model_hash?: string;
  round_index?: number;
  human_review?: WholeHomeReviewState;
  summary_counts?: { processed: number; deliverable: number; rejected: number; failed: number };
  results: WholeHomeResult[];
  created_at: number;
  updated_at: number;
}

export interface WholeHomeReplayCapability {
  status: "exact_ready" | "exact_requires_rebind" | "exact_requires_human_revalidation" | "read_only_only";
  can_view: boolean;
  can_fork: boolean;
  blockers: Array<{ code: string; message?: string; role?: string }>;
}

export interface WholeHomeRunReplay {
  run: WholeHomeRun;
  snapshot: {
    schema_version: number;
    snapshot_id: string;
    snapshot_hash: string;
    source_project_id: string;
    source_revision: number;
    source_model_hash: string;
    source_run_id: string;
    captured_at: number;
    asset_manifest: Array<{ role: string; managed_relative_path: string; sha256: string; byte_length: number; available: boolean }>;
  };
  history_project: WholeHomeProject;
  replay_capability: WholeHomeReplayCapability;
}

export interface WholeHomeHistoryEvent {
  event_id: string;
  type: string;
  occurred_at: number;
  project_id: string;
  root_project_id: string;
  run_id?: string;
  variant_batch_id?: string;
  title: string;
  status: string;
  summary: string;
  thumbnail_urls?: string[];
  model_revision?: number;
  model_hash?: string;
  style?: string;
  lighting?: string;
  prompt?: string;
  counts?: Record<string, number>;
  variant_of_run_id?: string;
}

export interface WholeHomeProjectHistory {
  root_project_id: string;
  selected_project_id: string;
  branches: Array<{
    project_id: string;
    summary: string;
    status: string;
    revision: number;
    verified: boolean;
    updated_at: number;
    lineage?: WholeHomeProjectLineage;
  }>;
  events: WholeHomeHistoryEvent[];
  next_cursor: string;
}

export interface WholeHomeVariantBatchItem {
  item_id: string;
  source_artifact_id: string;
  capture_id: string;
  camera_name: string;
  room_id: string;
  model_key: "b2" | "pro";
  status: "pending" | "claimed" | "running" | "done" | "failed" | "cancelled" | "needs_reconcile";
  child_run_id: string;
  error: string;
}

export interface WholeHomeVariantBatch {
  schema_version: number;
  variant_batch_id: string;
  project_id: string;
  source_run_id: string;
  status: "previewed" | "expired" | "queued" | "running" | "done" | "partial" | "failed" | "cancelled";
  preview_hash: string;
  confirmation_phrase?: string;
  style_spec: {
    style: string;
    lighting: string;
    prompt: string;
    floor_path: string;
    style_ref_path: string;
    aspect_ratio: "4:3" | "16:9" | "3:4" | "9:16";
    resolution: "2K";
  };
  aggregate_caps: { image_calls: number; qa_calls: number; items: number; concurrency: 1 };
  items: WholeHomeVariantBatchItem[];
  child_run_ids: string[];
  counts: Record<string, number>;
  created_at: number;
  updated_at: number;
  expires_at: number;
  error: string;
}

export interface WholeHomeManualCapabilities {
  schema_version: number;
  mode: string;
  manual_safe: boolean;
  manual_paid: boolean;
  feature_flags: Record<string, boolean>;
  startup: {
    authoritative_migration: boolean;
    interruption_recovery: boolean;
    autopilot_reconciliation: boolean;
    single_data_root_owner: boolean;
  };
  manual_run_contract: {
    material_modes: Array<"floor_sample" | "style_pack">;
    capture_count: 1;
    fallback_capture_count: 0;
    model_count: 1;
    candidates_per_camera: 1;
    resolution: "2K";
    image_call_cap: number;
    qa_call_cap: number;
    requires_preview: boolean;
    requires_dynamic_confirmation: boolean;
  };
}

export interface WholeHomeManualRunPreview {
  schema_version: number;
  preview_id: string;
  preview_sha256: string;
  confirmation_phrase: string;
  expires_at: number;
  request: Record<string, unknown>;
  input_manifest: Array<{
    label: string;
    path: string;
    sha256: string;
    byte_length: number;
  }>;
  caps: { image_calls: number; qa_calls: number };
  paid_enabled: boolean;
}

export interface Swatch {
  path: string;
  url: string;
  name: string;
  thumb: string;
}

export interface ConfigView {
  has_gemini_key: boolean;
  has_fal_key: boolean;
  image_provider: string;
  speed_profile: string;
  auto_failover: boolean;
  auto_color_match_enabled: boolean;
  tls_verify: boolean;
  tls_ca_bundle: string;
  proxy: string;
  fal_queue_proxy?: string;
  max_concurrent_per_model: number;
  speed_params: Record<string, unknown>;
  has_deepseek_key?: boolean;
  omakase_enabled?: boolean;
  sd_enabled?: boolean;
  omakase_gemini_model?: string;
  deepseek_model?: string;
  deepseek_base_url?: string;
  usage_prices?: Record<string, number>;
  pptx_company?: string;
  pptx_contact?: string;
  pptx_logo_url?: string;
  inpaint_provider?: string;
  inpaint_remove_model?: string;
  inpaint_add_model?: string;
  comfyui_base_url?: string;
  comfyui_workflow_path?: string;
  comfyui_timeout?: number;
  inpaint_remove_prompt?: string;
}

export interface ConfigPatch {
  gemini_api_key?: string;
  fal_api_key?: string;
  image_provider?: string;
  speed_profile?: string;
  auto_failover?: boolean;
  auto_color_match_enabled?: boolean;
  proxy?: string;
  fal_queue_proxy?: string;
  tls_verify?: boolean;
  tls_ca_bundle?: string;
  max_concurrent_per_model?: number;
  deepseek_api_key?: string;
  deepseek_base_url?: string;
  deepseek_model?: string;
  omakase_gemini_model?: string;
  omakase_enabled?: boolean;
  sd_enabled?: boolean;
  usage_prices?: Record<string, number>;
  pptx_company?: string;
  pptx_contact?: string;
  inpaint_provider?: string;
  inpaint_remove_model?: string;
  inpaint_add_model?: string;
  comfyui_base_url?: string;
  comfyui_workflow_path?: string;
  comfyui_timeout?: number;
  inpaint_remove_prompt?: string;
}

/** Omakase 文本模型返回的单个场景散文候选 */
export interface OmakaseOption {
  text: string;
  why: string;
  recommended: boolean;
  subject_type: "none" | "person" | "pet" | "both";
}

export interface OmakaseScenesResponse {
  options: OmakaseOption[];
  provider: "gemini" | "deepseek";
  fallback_used: boolean;
  notice: string;
}

export interface ModelsView {
  gemini: Record<string, string>;
  fal: Record<string, string>;
  provider: string;
}

export interface OptionsView {
  workflow_modes: string[];
  model_filters: { value: "b2" | "pro" | "both"; label: string }[];
  resolutions: string[];
  aspect_ratios: string[];
  seam_types: string[];
  glossiness: string[];
  // 通用
  room_types: string[];
  property_types: string[];
  views: string[];
  floor_tones: string[];
  styles: string[];
  lightings: string[];
  angles: string[];
  floor_sizes: string[];
  panel_sizes: string[];
  market_furniture: string[];
  avoid_items: string[];
  // 地区级联
  continents: string[];
  location_map: Record<string, Record<string, string[]>>;
  // 宠物
  pet_types: string[];
  pet_actions: string[];
  pet_focus: string[];
  // 国内市场
  cn_room_types: string[];
  cn_developers: string[];
  cn_cities: string[];
  cn_tiers: string[];
  cn_unit_types: string[];
  cn_delivery_choices: string[];
  cn_space_features: string[];
  cn_facilities: string[];
}

export type Recipe = Record<string, unknown> & { label?: string; sub?: string };

/** 自定义配方（/api/recipes/custom；params=存入时的 GenParams 快照） */
export interface CustomRecipe {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  params: GenParams;
}

export type ReviewStatus = "unreviewed" | "pass" | "backup" | "rejected";

export interface RecordResult {
  result_id: string;
  result_url?: string;
  result_thumb?: string;
  has_inline?: boolean;
  model?: string;
  model_label?: string;
  comment?: string;
  favorite?: boolean;
  best?: boolean;
  review_status?: ReviewStatus;
  review_tags?: string[];
  review_note?: string;
  reviewed_at?: string;
  result_timestamp?: string;
  source_result_id?: string;
  generation_metadata?: CandidateGenerationMetadata;
  [k: string]: unknown;
}

export interface ResultReviewPatch {
  json_path: string;
  record_id: string;
  result_id: string;
  review_status: ReviewStatus;
  review_tags: string[];
  review_note: string;
  best: boolean;
}

/** 生成入参快照（记录 JSON 的 gen_context 字段；老记录无此字段） */
export interface GenContext {
  image_path?: string;
  room_path?: string;
  ref_path?: string;
  room_url?: string;       // 后端 load 时由 room_path 换算，供前后对比直接用
  image_url?: string;      // 后端 load 时由 image_path 换算，供手动校色参照展示
  floor_path?: string;     // 球面效果图路线的地板小样字段
  floor_url?: string;      // 后端 load 时由 floor_path 换算
  model_filter?: ModelFilter;
  model_targets?: ModelKey[];
  sd_options?: SDOptions;
  params?: GenParams;
  free_image_paths?: string[];
  free_image_urls?: string[];
  free_options?: {
    aspect_ratio?: string;
    resolution?: string;
  };
}

export interface PanoAuditRecordMetadata {
  project_id?: string;
  pano_id?: string;
  capture_id?: string;
  source_hash?: string;
  candidate_sha256?: string;
  projection?: PanoProjection;
  erp_width?: number;
  erp_height?: number;
  canonical_forward?: string;
  heading_deg?: number;
  gate?: WholeHomePanoGate;
  provider_call?: Record<string, unknown>;
}

export interface GeometryAuditMetric {
  metric_id: string;
  field: string;
  label: string;
  actual: number;
  actual_display: string;
  operator: ">=" | "<=";
  threshold: number;
  threshold_display: string;
  unit: string;
  status: "passed" | "failed" | "pending";
}

export interface GeometryAuditArtifact {
  artifact_id: string;
  label: string;
  file_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  expected_sha256: string;
  integrity_status: "passed" | "failed";
  integrity_problems: string[];
  available?: boolean;
  relative_path?: string;
}

export interface GeometryAuditChannel {
  channel_id: "cad" | "raster" | string;
  label: string;
  status: "passed" | "failed" | "blocked" | "pending";
  metrics: GeometryAuditMetric[];
  source_sha256?: string;
  derived_source_sha256?: string;
  model_facts_hash?: string;
  registration_hash?: string;
  evidence_hash?: string;
  hard_errors?: unknown[];
  warning_count?: number;
}

export interface GeometryAuditMetadata {
  schema_version: number;
  publisher_version: string;
  audit_kind: "plan_to_3d_geometry_gold" | "dwg_live_geometry";
  level: "L1" | "L2" | "L3" | "L4" | "L5";
  case_id: string;
  title: string;
  status: "passed" | "failed" | "blocked" | "invalidated" | "pending";
  archived_status?: string;
  invalidation?: { code: string; message: string; required_schema?: string };
  executed_at: string;
  runner_version: string;
  audit_hash: string;
  source: {
    dataset?: string;
    license?: string;
    file_name?: string;
    source_sha256: string;
    ifcopenshell_version?: string;
    storey?: { ifc_id?: number; name?: string; elevation_m?: number };
    counts?: Record<string, number>;
    coordinate_contract?: Record<string, string>;
    metadata_unit_scale_to_m?: number;
    extraction_warnings?: Array<Record<string, unknown>>;
    raster_variants?: Record<string, Record<string, unknown>>;
  };
  channels: GeometryAuditChannel[];
  artifacts: GeometryAuditArtifact[];
  integrity: {
    status: "passed" | "failed";
    checked_count: number;
    failures: Array<{ artifact_id: string; problems: string[] }>;
  };
  issues: unknown[];
  review: {
    checked_metric_ids: string[];
    reviewer: string;
    note: string;
    reviewed_at: string;
  };
}

export interface RecordEntry {
  id?: string;
  room_type?: string;
  workflow_mode?: string;
  user_prompt?: string;
  immutable_audit?: boolean;
  pano_audit?: PanoAuditRecordMetadata;
  geometry_audit?: GeometryAuditMetadata;
  results?: RecordResult[];
  gen_context?: GenContext;
  color_match_ref_url?: string;  // 后端从记录同目录优化图/历史小样解析
  color_match_ref_path?: string;
  [k: string]: unknown;
}

export interface RecordFile {
  json_path: string;
  labels: [string, string][];
  favorite_count: number;
}

export interface UsageRow {
  mode: string;
  operation: string;
  model: string;
  provider: string;
  ok: number;
  fail: number;
  cost?: number | null; // 估算成本（元）；未配单价时为 null
}
export interface UsageSummary {
  rows: UsageRow[];
  totals: {
    ok: number;
    fail: number;
    total: number;
    cost?: number | null;
    unpriced_ok: number;
    cost_complete: boolean;
  };
}

/** 评审复盘（/api/review/summary） */
export interface ReviewDimRow {
  key: string;
  pass: number;
  backup: number;
  rejected: number;
  unreviewed: number;
  total: number;
  pass_rate: number | null; // pass / 已评审数；行无已评审时 null
}
export interface ReviewSummary {
  overview: {
    total: number;
    reviewed: number;
    coverage: number | null;
    pass: number;
    backup: number;
    rejected: number;
    pass_rate: number | null;
    best: number;
  };
  dimensions: Record<string, ReviewDimRow[]>;
  tags: { tag: string; count: number }[];
}

/** 好图样本库条目（/api/review/gallery） */
export interface ReviewGalleryItem {
  json_path: string;
  material: string;
  record_id: string;
  result_id: string;
  style: string;
  room_type: string;
  workflow_mode: string;
  model_label: string;
  result_timestamp: string;
  review_status: ReviewStatus;
  review_tags: string[];
  review_note: string;
  best: boolean;
  result_url: string;
  result_thumb: string;
}

export interface ResolvedRecipe {
  key: string;
  label: string;
  sub: string;
  style_type: string;
  lighting: string;
  angle: string;
  aspect_ratio: string;
  resolution: string;
}

export interface FloorAnalyze {
  tone: string;
  recipes: ResolvedRecipe[];
}

/** 手动校色（区域化 Reinhard，/api/color-match/*） */
export interface ColorMatchRect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface ColorMatchAdjustments {
  temperature: number;
  tint: number;
  exposure: number;
  contrast: number;
  highlights: number;
  shadows: number;
  whites: number;
  blacks: number;
  midtones: number;
  saturation: number;
}
export type ColorMatchHintCode =
  | "warm"
  | "cool"
  | "green"
  | "magenta"
  | "gray"
  | "saturated"
  | "matched"
  | "unavailable";
export interface ColorMatchHint {
  code: ColorMatchHintCode;
  text: string;
}
export interface ColorMatchZoneAnalysis {
  zone: "highlight" | "penumbra" | "shadow";
  label: string;
  preview: string | null;
  luminance: number | null;
  hints: ColorMatchHint[];
}
export interface ColorMatchAnalysis {
  status: "ok" | "low_dynamic_range" | "insufficient_region";
  confidence: "high" | "low";
  summary: string;
  recommended_adjustments: ColorMatchAdjustments;
  zones: ColorMatchZoneAnalysis[];
}
export type ColorMatchAlgorithm = "classic" | "distribution";
export type ColorIlluminationMode = "off" | "chroma" | "full";
export interface ColorQualityReport {
  score: number;
  level: "high" | "medium" | "low";
  summary: string;
  source_usable_ratio: number;
  reference_usable_ratio: number;
  clipped_ratio: number;
  glare_ratio: number;
  shadow_ratio: number;
  outlier_ratio: number;
  spatial_chroma_span: number;
  spatial_luminance_span: number;
  initial_delta_e00: number;
  estimated_delta_e00: number;
  predicted_gamut_clip_ratio: number;
  algorithm: ColorMatchAlgorithm;
  requested_illumination_mode: ColorIlluminationMode;
  applied_illumination_mode: ColorIlluminationMode;
  fallback_reason: string;
  warnings: string[];
  diagnostic_overlay: string | null;
}
export interface ColorMatchPreviewRequest {
  image_rel: string; // 成图相对 /outputs 路径
  ref_path: string;
  rect: ColorMatchRect; // 只用于地板统计
  strength?: number; // 默认 0.8
  feather?: number; // 兼容旧客户端；全图校色时忽略
  adjustments?: ColorMatchAdjustments;
  adjustment_mode?: "auto" | "manual"; // auto=框选取样后全图校准；manual=以 Gemini 原图为零点全图调整
  include_analysis?: boolean; // 仅首帧/重新框选/更换小样时请求三区诊断
  scope?: "global" | "floor_mask";
  mask_b64?: string;
  mask_feather?: number;
  algorithm?: ColorMatchAlgorithm;
  illumination_mode?: ColorIlluminationMode;
}
export interface ColorMatchPreviewView {
  preview: string; // data URL
  width: number;
  height: number;
  auto_adjustments: ColorMatchAdjustments; // 满强度自动校准对应的原图基准滑杆值
  analysis?: ColorMatchAnalysis;
  quality_report?: ColorQualityReport;
}
export interface JobColorMatchRequest extends ColorMatchPreviewRequest {
  stage: ModelKey;
}
export interface SuiteColorMatchRequest extends ColorMatchPreviewRequest {
  suite_id: string;
  room_id: string;
  result_id: string;
}
export interface RecordColorMatchRequest {
  json_path: string;
  record_id: string;
  result_id: string;
  ref_path?: string; // 空 → 后端回退 gen_context.image_path
  rect: ColorMatchRect; // 只用于地板统计
  strength?: number;
  feather?: number; // 兼容字段，全图校色忽略
  adjustments?: ColorMatchAdjustments;
  adjustment_mode?: "auto" | "manual"; // 两种模式均作用于全图
  scope?: "global" | "floor_mask";
  mask_b64?: string;
  mask_feather?: number;
  algorithm?: ColorMatchAlgorithm;
  illumination_mode?: ColorIlluminationMode;
}
export interface ColorMatchSegmentRequest {
  image_rel: string;
  positive_mask_b64?: string;
  negative_mask_b64?: string;
  previous_mask_b64?: string;
  auto_seed?: boolean;
}
export interface ColorMatchSegmentView {
  mask_b64: string;
  width: number;
  height: number;
  confidence: number;
  status: "ok" | "needs_guidance";
  warnings: string[];
  model: string;
}

export interface RecordEditRequest {
  json_path: string;
  record_id: string;
  result_id: string;
  instruction: string;
  api_key?: string;
  image_size?: string;
  preserve_floor_geometry?: boolean;
  model_choice?: string;
  color_match?: boolean; // 保持原图色彩（防偏色），后端默认 true
}

// ── 生成式修补（inpaint：画笔涂抹选区 → 候选抽卡 → 挑选提交）──
export interface InpaintPayload {
  mask_b64: string; // 纯 base64 PNG（无 data: 前缀），白=重绘区
  prompt?: string; // add 模式必填
  mode: "remove" | "add";
  grow?: number; // remove=最小外扩且后端自适应；add=仅显式外扩，默认 0
  feather?: number; // 羽化 / 短边比例；add 的羽化限制在有效选区内部
  seed?: number;
  n?: number; // 候选数 1-3（Lightroom 式抽卡；n 张记 n 次费用）
}
export interface InpaintTargetPayload {
  kind: "job" | "record" | "room" | "suite";
  jid?: string;
  stage?: ModelKey;
  image_rel?: string;
  json_path?: string;
  record_id?: string;
  result_id?: string;
  room_path?: string;
  suite_id?: string;
  room_id?: string;
}
export interface GenericInpaintRequest extends InpaintPayload {
  target: InpaintTargetPayload;
}
export interface SmartMaskCandidate {
  id: string;
  rle: number[]; // 行优先，0/1 交替计数，从 0 值游程开始
  bbox: [number, number, number, number];
  area: number;
  confidence: number;
  stability: number;
}
export interface InpaintSegmentRequest {
  target: InpaintTargetPayload;
  strategy: "scan_objects" | "point";
  point?: { x: number; y: number };
}
export interface InpaintSegmentView {
  width: number;
  height: number;
  status: "ok" | "needs_guidance";
  warnings: string[];
  model: string;
  candidates: SmartMaskCandidate[];
}
export interface InpaintCandidate {
  url: string;
  thumb: string;
}
export interface InpaintStatusView {
  inpaint_id: string;
  status: "running" | "applying" | "done" | "failed" | "cancelled";
  stage: string;
  error: string;
  notice: string;
  requested_n: number;
  effective_n: number;
  candidates: InpaintCandidate[];
}
export interface InpaintSubmitView {
  inpaint_id: string;
  requested_n: number;
  effective_n: number;
  notice: string;
}
export interface InpaintApplyResponse {
  ok: boolean;
  job?: JobView; // target=job：写回后的任务快照
  result_url?: string; // target=record
  path?: string; // target=room：新上传文件
  url?: string;
  thumb?: string;
  suite?: FloorplanSuite; // target=suite：追加修补候选后的套图快照
}
export interface ComfyUIPingView {
  ok: boolean;
  version?: string;
  devices?: string[];
  error?: string;
}

// ── 真实纹理投影（本地确定性渲染）──
export interface FloorPoint {
  x: number;
  y: number;
}
export interface FloorVisualizeTargetPayload {
  kind: "job" | "record" | "room";
  jid?: string;
  stage?: ModelKey;
  image_rel?: string;
  json_path?: string;
  record_id?: string;
  result_id?: string;
  room_path?: string;
}
export interface FloorVisualizeRequest {
  target: FloorVisualizeTargetPayload;
  texture_path: string;
  mask_b64: string;
  calibration_quad: FloorPoint[];
  scale: number;
  rotation: number;
  offset_x: number;
  offset_y: number;
  illumination_strength: number;
  shadow_strength: number;
  feather: number;
  texture_width_mm?: number;
  texture_height_mm?: number;
  plank_width_mm?: number;
  plank_length_mm?: number;
}
export interface FloorVisualizePreview {
  preview: string;
  width: number;
  height: number;
  warnings: string[];
  metadata: Record<string, unknown>;
}
export interface FloorVisualizeApplyResponse {
  ok: boolean;
  job?: JobView;
  result_url?: string;
  result_id?: string;
  path?: string;
  url?: string;
  thumb?: string;
  warnings: string[];
  metadata: Record<string, unknown>;
}
