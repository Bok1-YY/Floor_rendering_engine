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
  proxy: string;
  max_concurrent_per_model: number;
  speed_params: Record<string, unknown>;
}

export interface ConfigPatch {
  gemini_api_key?: string;
  fal_api_key?: string;
  image_provider?: string;
  speed_profile?: string;
  auto_failover?: boolean;
  proxy?: string;
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
  seam_types: string[];
  glossiness: string[];
  room_types: string[];
  cn_room_types: string[];
  floor_tones: string[];
  continents: string[];
  property_types: string[];
}

export type Recipe = Record<string, unknown> & { label?: string; sub?: string };

export interface RecordResult {
  result_url?: string;
  result_thumb?: string;
  has_inline?: boolean;
  model?: string;
  comment?: string;
  [k: string]: unknown;
}

export interface RecordEntry {
  id?: string;
  room_type?: string;
  results?: RecordResult[];
  [k: string]: unknown;
}

export interface RecordFile {
  json_path: string;
  labels: string[];
}
