import type { GenParams, ModelFilter, ModelKey, OptionsView, SDOptions, Swatch } from "@/lib/types";

// 由后端文件路径重建 Swatch（复用参数回填：记录里只有 path，url/缩略图按上传目录约定拼出）
export function swatchFromPath(p?: string): Swatch | null {
  if (!p) return null;
  const name = p.split(/[\\/]/).pop() || p;
  return {
    path: p,
    name,
    url: `/uploads/${name}`,
    thumb: `/thumb/uploads/${name}?s=320`,
  };
}

// 依据后端 options 组装一份「全字段默认值」的生成参数。
// 原先内联在初始化 useEffect 里，抽出以便首次加载与草稿回落两条路径复用同一份默认。
export function buildDefaultParams(o: OptionsView, prevWorkflow?: string): GenParams {
  const cont = o.continents[0];
  const country = Object.keys(o.location_map[cont] || {})[0] || "";
  const city = (o.location_map[cont]?.[country] || [])[0] || "";
  const scenePreset = o.scene_catalog.presets.find((preset) => preset.value === "低密郊区独立住宅");
  return {
    workflow_mode: prevWorkflow || o.workflow_modes[0],
    continent: cont,
    country,
    city,
    room_type: o.room_types[0],
    ...scenePreset?.defaults,
    scene_preset: scenePreset?.value || "低密郊区独立住宅",
    scene_anchor: "scene_preset",
    market_furniture: o.market_furniture[0],
    cn_room_type: o.cn_room_types[0],
    cn_view: "带绿植的社区内院",
    cn_developer: o.cn_developers[0],
    cn_city: o.cn_cities[0],
    cn_tier: o.cn_tiers[0],
    cn_unit_type: o.cn_unit_types[0],
    cn_delivery: o.cn_delivery_choices[0],
    cn_space_features: [],
    cn_facilities: [],
    floor_size: o.floor_sizes[0],
    seam_type: o.seam_types[0],
    glossiness: o.glossiness[1] || o.glossiness[0],
    floor_coverage_min: 40,
    floor_coverage_max: 50,
    floor_tone: o.floor_tones[0],
    style_type: o.styles[0],
    lighting: o.lightings[0],
    angle: o.angles[0],
    aspect_ratio: o.aspect_ratios[0],
    resolution: o.resolutions[0],
    pet_type: o.pet_types[0],
    pet_action: o.pet_actions[0],
    pet_focus: o.pet_focus[0],
    avoid_items: o.avoid_items,
    cinematic_enabled: false,
    panel_submode: "再设计",
    panel_size: o.panel_sizes[0],
  };
}

export const DEFAULT_SD_OPTIONS: SDOptions = {
  seed: null,
  steps: 28,
  guidance_scale: 3.5,
  reference_strength: 0.5,
  positive_addition: "",
  negative_addition: "",
};

export const targetsFromLegacy = (filter?: ModelFilter): ModelKey[] =>
  filter === "b2" ? ["b2"] : filter === "pro" ? ["pro"] : ["b2", "pro"];

export const legacyFromTargets = (targets: ModelKey[]): "b2" | "pro" | "both" => {
  const hasB2 = targets.includes("b2");
  const hasPro = targets.includes("pro");
  return hasB2 && hasPro ? "both" : hasPro ? "pro" : "b2";
};

export type SubmissionKind = 'single' | 'rooms' | 'floors' | 'preview';
export interface GenerationInput {
  params: GenParams; modelTargets: ModelKey[]; sdOptions: SDOptions;
  floor: Swatch | null; refImg: Swatch | null; roomImg: Swatch | null;
  freePrompt: string; freeImages: Swatch[]; options: OptionsView | null; sdEnabled?: boolean;
}
export function validateGeneration(s: GenerationInput, kind: SubmissionKind): string | null {
  const mode = s.params.workflow_mode || '';
  const free = mode.includes('自由创作');
  if (free && kind !== 'single') return '自由创作不支持此提交方式';
  if (free && !s.freePrompt.trim()) return '请先输入自由指令词';
  if (free && !s.freeImages.length) return '自由创作至少需要上传一张图片';
  if (!free && kind !== 'floors' && !s.floor) return '请先上传地板图';
  if (kind !== 'preview') {
    if (!s.modelTargets.length) return '请至少选择一个生图模型';
    if (free && !s.modelTargets.some(key => key === 'b2' || key === 'pro')) return '自由创作请至少选择 B2 或 Pro';
    if (s.modelTargets.includes('sd35') && s.sdEnabled === false) return '请先在设置中启用 SD 3.5';
    if (s.modelTargets.includes('sd35') && !mode.includes('纯效果图')) return 'SD 3.5 当前仅支持纯效果图工作流';
  }
  if (kind === 'rooms' && mode.includes('Omakase')) return 'Omakase 工作流不支持按房间类型批量';
  if ((kind === 'rooms' || kind === 'floors') && mode.includes('墙板')) return '墙板模式不支持批量';
  if (mode.includes('参照模式') && !s.refImg) return '参照模式需上传参照图';
  if (mode.includes('地板替换') && !s.roomImg) return '地板替换需上传房间原图';
  if (mode.includes('Omakase') && !(s.params.scene_override || '').trim()) return 'Omakase 模式：请先生成或填写场景定稿';
  if (mode.includes('墙板')) {
    const sub = s.params.panel_submode || '再设计';
    if (sub.includes('替换') && !s.roomImg) return '墙板替换需上传原墙板场景图';
    if (sub.includes('再设计') && !s.refImg) return '墙板再设计需上传场景参照图';
  }
  return null;
}
export function safeParams(s: GenerationInput, params = s.params): GenParams {
  return params.workflow_mode?.includes('Omakase')
    ? { ...params, avoid_items: s.options?.avoid_items ?? params.avoid_items, custom_addition: '' } : params;
}
export function jobPayload(s: GenerationInput, floor = s.floor, params = s.params) {
  return {
    image_path: floor!.path, model_filter: legacyFromTargets(s.modelTargets), model_targets: s.modelTargets,
    sd_options: s.sdOptions, room_path: s.roomImg?.path ?? null, ref_path: s.refImg?.path ?? null, params: safeParams(s, params)
  };
}
export function freePayload(s: GenerationInput) {
  return {
    prompt: s.freePrompt, image_paths: s.freeImages.map(image => image.path),
    model_targets: s.modelTargets.filter((key): key is 'b2' | 'pro' => key === 'b2' || key === 'pro'),
    aspect_ratio: ((s.params.aspect_ratio || '4:3').split(' ')[0] || '4:3') as '4:3' | '16:9' | '3:4' | '9:16',
    resolution: ((s.params.resolution || '4K').split(' ')[0] || '4K') as '2K' | '4K'
  };
}
export function recipeSnapshot(p: GenParams): GenParams {
  const snap = { ...p }; delete snap.last_image_path; delete snap.floor_tone; return snap;
}
