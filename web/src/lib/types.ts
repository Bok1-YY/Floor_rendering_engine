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
  usage_prices?: Record<string, number>;
  pptx_company?: string;
  pptx_contact?: string;
  pptx_logo_url?: string;
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
  usage_prices?: Record<string, number>;
  pptx_company?: string;
  pptx_contact?: string;
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
  model_filter?: ModelFilter;
  params?: GenParams;
}

export interface RecordEntry {
  id?: string;
  room_type?: string;
  workflow_mode?: string;
  results?: RecordResult[];
  gen_context?: GenContext;
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
export interface ColorMatchPreviewRequest {
  image_rel: string; // 成图相对 /outputs 路径
  ref_path: string;
  rect: ColorMatchRect;
  strength?: number; // 默认 0.8
  feather?: number; // 默认 0.05
  adjustments?: ColorMatchAdjustments;
  adjustment_mode?: "auto" | "manual"; // auto=自动校准；manual=以 Gemini 原图为零点
}
export interface ColorMatchPreviewView {
  preview: string; // data URL
  width: number;
  height: number;
  auto_adjustments: ColorMatchAdjustments; // 满强度自动校准对应的原图基准滑杆值
}
export interface JobColorMatchRequest extends ColorMatchPreviewRequest {
  stage: "b2" | "pro";
}
export interface RecordColorMatchRequest {
  json_path: string;
  record_id: string;
  result_id: string;
  ref_path?: string; // 空 → 后端回退 gen_context.image_path
  rect: ColorMatchRect;
  strength?: number;
  feather?: number;
  adjustments?: ColorMatchAdjustments;
  adjustment_mode?: "auto" | "manual";
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
