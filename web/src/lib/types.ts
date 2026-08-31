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
  failure_code: string;
  retry_safety: "safe" | "ambiguous" | "fatal";
  may_have_been_billed: boolean;
  attempts: Array<{
    attempt: number;
    phase: string;
    http_status?: number | null;
    started_at: number;
    duration_ms: number;
    outcome: string;
  }>;
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
  model_targets: GenerationModelKey[];
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
  operation_failure_code: string;
  operation_retry_safety: "safe" | "ambiguous" | "fatal";
  operation_may_have_been_billed: boolean;
  operation_attempts: ModelRunView["attempts"];
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
  scene_preset?: string;
  site_context?: string;
  floor_level?: string;
  room_scale?: string;
  room_layout?: string;
  window_type?: string;
  scene_notes?: string;
  scene_anchor?: string;
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

export interface SceneOption {
  value: string;
  label: string;
  description: string;
  markets: ("overseas" | "cn")[];
  compatibility: Record<string, string | string[]>;
}

export interface ScenePreset {
  value: string;
  label: string;
  market: "海外" | "国内" | "all";
  description: string;
  defaults: Partial<GenParams>;
}

export interface SceneCatalog {
  version: string;
  presets: ScenePreset[];
  property_options: SceneOption[];
  room_options: SceneOption[];
  site_contexts: SceneOption[];
  floor_levels: SceneOption[];
  room_scales: SceneOption[];
  room_layouts: SceneOption[];
  window_types: SceneOption[];
  view_options: { group: string; options: SceneOption[] }[];
  compatibility_rules: {
    policy: string;
    custom_preset: string;
    legacy_preset: string;
    scene_fields: string[];
  };
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
  secret_backend: {
    name: string;
    available: boolean;
    persistent: boolean;
    error?: string;
  };
  secret_sources: {
    gemini: "environment" | "keyring" | "legacy" | "missing";
    fal: "environment" | "keyring" | "legacy" | "missing";
    deepseek: "environment" | "keyring" | "legacy" | "missing";
  };
  plaintext_secret_migration_required: boolean;
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
  scene_catalog: SceneCatalog;
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
  floor_path?: string;     // 旧记录兼容：生成任务绑定的地板小样
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
  projection?: "equirectangular";
  erp_width?: number;
  erp_height?: number;
  canonical_forward?: string;
  heading_deg?: number;
  gate?: Record<string, unknown> & { gate_pass?: boolean; failures?: string[] };
  provider_call?: Record<string, unknown>;
}

export interface RecordEntry {
  id?: string;
  room_type?: string;
  workflow_mode?: string;
  user_prompt?: string;
  immutable_audit?: boolean;
  pano_audit?: PanoAuditRecordMetadata;
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
  uncertain: number;
  cost?: number | null; // 估算成本（元）；未配单价时为 null
  cost_min?: number | null;
  cost_max?: number | null;
}
export interface UsageSummary {
  rows: UsageRow[];
  totals: {
    ok: number;
    fail: number;
    uncertain: number;
    total: number;
    cost?: number | null;
    cost_min?: number | null;
    cost_max?: number | null;
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
  kind: "job" | "record" | "room";
  jid?: string;
  stage?: ModelKey;
  image_rel?: string;
  json_path?: string;
  record_id?: string;
  result_id?: string;
  room_path?: string;
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

// ── 全屋设计：户型图 → 正俯视 2.5D 概念图 → Blender Agent 任务包 ──
export type WholeHomeDesignStatus =
  | "analyzing_plan"
  | "needs_plan_review"
  | "needs_brief"
  | "ready"
  | "draft_previewed"
  | "generating_drafts"
  | "needs_draft_selection"
  | "refine_previewed"
  | "refining"
  | "needs_structure_review"
  | "locked"
  | "interrupted"
  | "failed"
  | "cancelled";

export interface DesignPlanRoom {
  id: string;
  label: string;
  room_type: string;
  coarse_location: string;
  adjacent_room_ids: string[];
  confidence?: number;
  evidence?: string;
  needs_confirmation?: boolean;
}

export interface DesignPlanSummary {
  version: "plan-summary-v1";
  room_count: number;
  rooms: DesignPlanRoom[];
  declared_layout: {
    bedrooms: number;
    halls: number;
    bathrooms: number;
    source_text: string;
    confidence: number;
  };
  declared_area_m2: number;
  overall_dimensions_mm: {
    width: number;
    depth: number;
    evidence: string[];
    confidence: number;
  };
  summary_confidence: number;
  review_items: Array<{
    id: string;
    room_id?: string;
    kind: string;
    label: string;
    evidence: string;
    confidence: number;
    status: string;
  }>;
  annotation_boxes: Array<{
    label: string;
    kind: string;
    box_2d: number[];
    confidence: number;
    safe_to_erase: boolean;
  }>;
  entrances: string[];
  openings_summary: string[];
  wet_zones: string[];
  balconies: string[];
  dimension_evidence: string[];
  must_preserve: string[];
  uncertainties: string[];
  source: "gemini" | "human" | "human_confirmed_ai";
  prompt_version: string;
}

export interface DesignStructureCheck {
  check_id: string;
  status: "pass" | "fail" | "uncertain";
  evidence: string;
}

export interface DesignStructureQa {
  version?: string;
  status: "not_run" | "manual_required" | "passed" | "failed";
  hard_fail: boolean;
  summary?: string;
  checks: DesignStructureCheck[];
  provider?: string;
}

export interface WholeHomeDesignCandidate {
  candidate_id: string;
  phase: "draft" | "final";
  direction_index: number;
  parent_candidate_id: string;
  status: "queued" | "running" | "qa_running" | "interrupted" | "done" | "failed" | "cancelled";
  stage: string;
  error: string;
  url: string;
  thumb: string;
  provider: "google" | "fal";
  model_label: string;
  model_id: string;
  endpoint: string;
  resolution: "2K" | "4K";
  aspect_ratio: string;
  result_hash: string;
  image_size: number[];
  structure_qa: DesignStructureQa;
  human_review: {
    status: "pending" | "passed" | "failed";
    checks: Record<string, boolean>;
    reviewer: string;
    note: string;
    reviewed_at?: number;
  };
  stale: boolean;
  stale_reason?: string;
  locked_at?: number;
  created_at: number;
}

export interface WholeHomeDesignBundle {
  bundle_id: string;
  sha256: string;
  candidate_id: string;
  project_revision: number;
  stale: boolean;
  stale_reason?: string;
  created_at: number;
  download_url: string;
}

export interface WholeHomeDesignProject {
  schema_version: "whole-home-design-project-v1";
  project_id: string;
  revision: number;
  status: WholeHomeDesignStatus;
  stage: string;
  error: string;
  source_name: string;
  source_hash: string;
  generation_hash: string;
  source_url: string;
  normalized_url: string;
  generation_url: string;
  normalization: {
    original_size: number[];
    cropped_box: number[];
    content_size: number[];
    canvas_size: number[];
    offset: number[];
    aspect_ratio: string;
  };
  generation_crop?: {
    version: string;
    source_size: number[];
    crop_box: number[];
    content_size: number[];
    canvas_size: number[];
    offset: number[];
    aspect_ratio: string;
    structural_component_count: number;
    fallback_reason: string;
  };
  generation_cleanup?: {
    version: string;
    applied_count: number;
    boxes: Array<Record<string, unknown>>;
    source_hash?: string;
    clean_hash?: string;
    error?: string;
  };
  plan_summary: DesignPlanSummary;
  plan_summary_confirmed: boolean;
  brief: {
    requirements_text: string;
    reference_paths: string[];
    reference_hashes: string[];
  };
  brief_hash: string;
  candidates: WholeHomeDesignCandidate[];
  bundles: WholeHomeDesignBundle[];
  locked_candidate_id: string;
  cancel_requested: boolean;
  created_at: number;
  updated_at: number;
}

export interface WholeHomeDesignPaidPreview {
  preview_id: string;
  preview_hash: string;
  kind: "drafts" | "refine";
  project_revision: number;
  provider: "google" | "fal";
  model_label: string;
  model_id: string;
  call_count: number;
  resolution: "2K" | "4K";
  aspect_ratio: string;
  estimated_cost: number | null;
  confirmation_phrase: string;
  expires_at: number;
}

export interface DesignFloorplanUpload {
  kind: "image" | "pdf";
  name: string;
  path: string;
  url?: string;
  thumb?: string;
  pages: Array<{ page: number; path: string; url: string; thumb: string; width: number; height: number }>;
}

export interface DesignReferenceUpload {
  name: string;
  path: string;
  url: string;
  thumb: string;
  sha256: string;
}

export type {
  AssetDeleteView,
  QuarantineEntryView,
  StorageAuditView,
  StorageCleanupView,
} from "./types/storage";
