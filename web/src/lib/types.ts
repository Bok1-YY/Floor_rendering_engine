// 后端 server_api.py 返回结构的 TS 镜像（手动对齐；后端改字段时同步这里）

export type JobStatus = "queued" | "running" | "done" | "partial" | "failed";
export type ModelFilter = "b2" | "pro" | "both";

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
  workflow_mode: string;
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
  has_retry: boolean;
  record_id: string;
  json_path: string;
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
  panel_submode?: string;    // 墙板模式子行为：再设计 / 替换 / 纯原创（仅墙板模式生效，其他工作流忽略）
  panel_size?: string;       // 墙板尺寸/板型（预设或自定义；仅墙板再设计/纯原创生效）
}

export interface JobSubmit {
  image_path: string;
  model_filter: ModelFilter;
  api_key?: string;
  room_path?: string | null;
  ref_path?: string | null;
  params: GenParams;
}

export interface EditRequest {
  instruction: string;
  api_key?: string;
  image_size?: string;
  preserve_floor_geometry?: boolean;
  model_choice?: string;
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
  tls_verify: boolean;
  tls_ca_bundle: string;
  proxy: string;
  max_concurrent_per_model: number;
  speed_params: Record<string, unknown>;
  has_deepseek_key?: boolean;
  omakase_enabled?: boolean;
  omakase_gemini_model?: string;
  deepseek_model?: string;
  deepseek_base_url?: string;
}

export interface ConfigPatch {
  gemini_api_key?: string;
  fal_api_key?: string;
  image_provider?: string;
  speed_profile?: string;
  auto_failover?: boolean;
  proxy?: string;
  tls_verify?: boolean;
  tls_ca_bundle?: string;
  max_concurrent_per_model?: number;
  deepseek_api_key?: string;
  deepseek_base_url?: string;
  deepseek_model?: string;
  omakase_gemini_model?: string;
  omakase_enabled?: boolean;
}

/** Omakase 文本模型返回的单个场景散文候选 */
export interface OmakaseOption {
  text: string;
  why: string;
  recommended: boolean;
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
  model_filters: { value: ModelFilter; label: string }[];
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

export interface RecordEntry {
  id?: string;
  room_type?: string;
  workflow_mode?: string;
  results?: RecordResult[];
  [k: string]: unknown;
}

export interface RecordFile {
  json_path: string;
  labels: [string, string][];
}

export interface UsageRow {
  mode: string;
  operation: string;
  model: string;
  provider: string;
  ok: number;
  fail: number;
}
export interface UsageSummary {
  rows: UsageRow[];
  totals: { ok: number; fail: number; total: number };
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

export interface RecordEditRequest {
  json_path: string;
  record_id: string;
  result_id: string;
  instruction: string;
  api_key?: string;
  image_size?: string;
  preserve_floor_geometry?: boolean;
  model_choice?: string;
}
