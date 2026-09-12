"use client";
// 生成页草稿持久化：把「作业中」的配置存到 localStorage，避免切到记录页再回来时被清空。
// 只在 effect 里读写（不在渲染期），故不会引发 SSR / 水合不一致。

import type { GenParams, ModelFilter, ModelKey, ResolvedRecipe, SDOptions, Swatch } from "./types";

// 版本化 key：将来草稿结构不兼容时改 v2，旧草稿自然作废（loadDraft 读不到新 key → 回落默认）。
const KEY = "floor-engine:generate-draft:v1";

/** 需要跨页面/刷新保留的生成页状态切片。 */
export interface GenerateDraft {
  params: GenParams;
  modelFilter: ModelFilter;
  modelTargets: ModelKey[];
  sdOptions: SDOptions;
  floor: Swatch | null;
  refImg: Swatch | null;
  roomImg: Swatch | null;
  recipes: ResolvedRecipe[];
  freePrompt: string;
  freeImages: Swatch[];
}

/** 读草稿；无 / 损坏 / 非浏览器环境一律返回 null（调用方回落默认）。 */
export function loadDraft(): Partial<GenerateDraft> | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? normalizeDraft(JSON.parse(raw)) : null;
  } catch {
    return null; // JSON 损坏 / 存储被禁用等，当作没有草稿
  }
}

/** 写草稿（配额满/隐私模式等失败时静默忽略，不阻断生成流程）。 */
export function saveDraft(d: GenerateDraft): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(d));
  } catch {
    /* 忽略 */
  }
}

/** 清空草稿（供「重置配置」使用）。 */
export function clearDraft(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* 忽略 */
  }
}

// ── 复用参数：记录页 → 生成页的一次性传递（独立 key，绝不覆写上面的作业草稿）──
const REUSE_KEY = "floor-engine:reuse-request:v1";

/** 记录页「复用参数」发起的一次性回填请求。 */
export interface ReuseRequest {
  params: GenParams;
  modelFilter?: ModelFilter;
  modelTargets?: ModelKey[];
  sdOptions?: SDOptions;
  floorPath?: string;
  roomPath?: string;
  refPath?: string;
  freePrompt?: string;
  freeImagePaths?: string[];
  freeOptions?: {
    aspect_ratio?: string;
    resolution?: string;
  };
}

export function saveReuseRequest(r: ReuseRequest): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(REUSE_KEY, JSON.stringify(r));
  } catch {
    /* 忽略 */
  }
}

/** 取出并删除复用请求（读后即删：只消费一次，刷新后不再重复回填）。 */
export function takeReuseRequest(): ReuseRequest | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(REUSE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    const normalized = normalizeDraft(parsed);
    if (!normalized?.params || !isObject(parsed)) { window.localStorage.removeItem(REUSE_KEY); return null; }
    const result: ReuseRequest = { params: normalized.params, modelFilter: normalized.modelFilter,
      modelTargets: normalized.modelTargets, sdOptions: normalized.sdOptions, freePrompt: normalized.freePrompt };
    for (const key of ['floorPath', 'roomPath', 'refPath'] as const) if (typeof parsed[key] === 'string') result[key] = parsed[key];
    if (Array.isArray(parsed.freeImagePaths)) result.freeImagePaths = parsed.freeImagePaths.filter((v): v is string => typeof v === 'string').slice(0, 3);
    window.localStorage.removeItem(REUSE_KEY);
    return result;
  } catch {
    return null;
  }
}

const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const isSwatch = (value: unknown): value is Swatch => isObject(value) && ['path', 'name', 'url', 'thumb'].every(key => typeof value[key] === 'string');
/** Accept legacy slices, dropping malformed fields before hydration or rendering. */
export function normalizeDraft(value: unknown): Partial<GenerateDraft> | null {
  if (!isObject(value)) return null;
  const result: Partial<GenerateDraft> = {};
  if (isObject(value.params)) {
    const numbers = new Set(['film_width_mm', 'film_repeat_length_mm', 'film_slit_origin_mm', 'floor_coverage_min', 'floor_coverage_max']);
    const arrays = new Set(['avoid_items', 'cn_space_features', 'cn_facilities']);
    const booleans = new Set(['cn_mode', 'cinematic_enabled']);
    const nullable = new Set(['film_width_mm', 'film_repeat_length_mm', 'film_slit_origin_mm', 'cn_space_features', 'cn_facilities']);
    const params = Object.fromEntries(Object.entries(value.params).filter(([key, v]) => {
      if (v === null) return nullable.has(key);
      if (numbers.has(key)) return typeof v === 'number' && Number.isFinite(v);
      if (arrays.has(key)) return Array.isArray(v) && v.every(x => typeof x === 'string');
      if (booleans.has(key)) return typeof v === 'boolean';
      return typeof v === 'string';
    }));
    if (typeof params.workflow_mode !== 'string') delete params.workflow_mode;
    result.params = params as unknown as GenParams;
  }
  if (value.modelFilter === 'b2' || value.modelFilter === 'pro' || value.modelFilter === 'both') result.modelFilter = value.modelFilter;
  if (Array.isArray(value.modelTargets)) result.modelTargets = [...new Set(value.modelTargets.filter((key): key is ModelKey => key === 'b2' || key === 'pro' || key === 'sd35'))];
  if (isObject(value.sdOptions)) {
    const sd: Partial<SDOptions> = {};
    for (const key of ['steps', 'guidance_scale', 'reference_strength', 'seed'] as const) {
      const n = value.sdOptions[key]; if (typeof n === 'number' && Number.isFinite(n)) sd[key] = n;
    }
    if (value.sdOptions.seed === null) sd.seed = null;
    for (const key of ['positive_addition', 'negative_addition'] as const) if (typeof value.sdOptions[key] === 'string') sd[key] = value.sdOptions[key];
    result.sdOptions = sd as SDOptions;
  }
  for (const key of ['floor', 'refImg', 'roomImg'] as const) if (value[key] === null || isSwatch(value[key])) result[key] = value[key];
  if (typeof value.freePrompt === 'string') result.freePrompt = value.freePrompt;
  if (Array.isArray(value.freeImages)) result.freeImages = value.freeImages.filter(isSwatch).slice(0, 3);
  if (Array.isArray(value.recipes)) result.recipes = value.recipes.filter((r): r is ResolvedRecipe => isObject(r) && typeof r.key === 'string' && typeof r.label === 'string' &&
    ['sub', 'style_type', 'lighting', 'angle', 'aspect_ratio', 'resolution'].every(key => r[key] === undefined || typeof r[key] === 'string'));
  return result;
}
