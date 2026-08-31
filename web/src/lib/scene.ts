import type { GenParams, SceneCatalog, SceneOption } from "./types";

const LEGACY_PROPERTY: Record<string, string> = {
  现代别墅: "现代花园别墅",
  普通独立住宅: "普通独立住宅",
  联排别墅: "联排住宅",
  普通公寓: "标准城市公寓",
  豪华大平层: "豪华大平层",
};

const LEGACY_ROOM: Record<string, string> = {
  客厅: "独立客厅",
  餐厅: "独立餐厅",
  厨房: "封闭式厨房",
  餐厨一体: "LDK 客餐厨一体",
  入户区: "玄关 / 入户区",
  玄关: "玄关 / 入户区",
  独立书房: "书房 / 家庭办公室",
};

const LEGACY_VIEW: Record<string, string> = {
  自然通透景观: "树木遮挡的局部景观",
  带修剪整齐草坪的私家后院: "修剪整齐的私家草坪后院",
  带泳池的阳光后院: "带泳池的阳光后院",
  充满园艺绿植的私家小院: "层次丰富的花园庭院",
  宁静干净的现代社区街道: "绿树成荫的住宅街道",
  自然绿植与树木: "树木遮挡的局部景观",
};

const FIELD_LABELS: Record<string, string> = {
  property_type: "物业",
  cn_unit_type: "户型",
  site_context: "地段",
  floor_level: "楼层",
  room_scale: "尺度",
  room_layout: "布局",
  window_type: "窗型",
  view: "窗景",
  cn_view: "窗景",
};

const asStrings = (value: string | string[] | undefined): string[] =>
  Array.isArray(value) ? value : [];
const asString = (value: string | string[] | undefined): string =>
  typeof value === "string" ? value : "";

export const flatViewOptions = (catalog: SceneCatalog): SceneOption[] =>
  catalog.view_options.flatMap((group) => group.options);

const optionByValue = (options: SceneOption[], value?: string) =>
  options.find((option) => option.value === value);

const propertyKind = (params: GenParams, catalog: SceneCatalog): string => {
  if (params.cn_mode) {
    const unit = params.cn_unit_type || "";
    if (["别墅", "叠墅"].some((part) => unit.includes(part))) return "villa";
    if (unit.includes("Loft")) return "loft";
    return "apartment";
  }
  return asString(optionByValue(catalog.property_options, params.property_type)?.compatibility.kind);
};

const fallbackView = (kind: string, floor: string, site: string): string => {
  if (["高层 16–30F", "超高层 31F+"].includes(floor) || (kind === "apartment" && site === "城市核心高密区")) {
    return "高层城市天际线";
  }
  if (site === "河湖滨水区") return ["apartment", "loft"].includes(kind) ? "城市河景" : "宁静湖景";
  if (site === "滨海度假区") return "开阔海平线";
  if (site === "山地森林") return "森林树海";
  if (["house", "villa", "cabin", "courtyard"].includes(kind)) return "层次丰富的花园庭院";
  return "树木遮挡的局部景观";
};

const marketPreset = (catalog: SceneCatalog, cnMode: boolean) =>
  catalog.presets.find((preset) => preset.value === (cnMode ? "普通成熟社区刚需公寓" : "低密郊区独立住宅"));

const presetDefaultsForAnchor = (params: GenParams, anchor: string, catalog: SceneCatalog): Partial<GenParams> => {
  if (anchor === "property_type" && !params.cn_mode) {
    const exact = catalog.presets.find((preset) => preset.market === "海外" && preset.defaults.property_type === params.property_type);
    if (exact) return exact.defaults;
    if (["半独立住宅", "普通独立住宅"].includes(params.property_type || "")) {
      return catalog.presets.find((preset) => preset.value === "低密郊区独立住宅")?.defaults || {};
    }
  }
  if (anchor === "cn_unit_type" && params.cn_mode) {
    const exact = catalog.presets.find((preset) => preset.market === "国内" && preset.defaults.cn_unit_type === params.cn_unit_type);
    if (exact) return exact.defaults;
    const unit = params.cn_unit_type || "";
    const name = ["别墅", "叠墅"].some((part) => unit.includes(part))
      ? "城市近郊联排 / 叠墅"
      : ["大平层", "四房", "复式", "跃层"].some((part) => unit.includes(part))
        ? "成熟高端社区改善住宅"
        : "普通成熟社区刚需公寓";
    return catalog.presets.find((preset) => preset.value === name)?.defaults || {};
  }
  if (anchor === "site_context") {
    const overseas: Record<string, string> = {
      城市核心高密区: "核心城区高层公寓", 成熟城市住宅区: "成熟街区标准公寓", 历史街区: "历史街区公寓",
      高端低密社区: "现代花园别墅", 新建综合社区: "成熟街区标准公寓", 郊区家庭社区: "低密郊区独立住宅",
      私密庄园社区: "现代花园别墅", 滨海度假区: "海滨住宅", 河湖滨水区: "湖畔 / 河岸住宅",
      "港口 / 码头区": "城市景观豪华大平层", 乡村村落: "乡村住宅 / 农舍", 山地森林: "山地林间木屋",
      热带花园社区: "现代花园别墅", 沙漠绿洲社区: "现代花园别墅", 国际中性住宅区: "低密郊区独立住宅",
    };
    const domestic: Record<string, string> = {
      城市核心高密区: "一线城市核心区高层改善", 河湖滨水区: "滨水大平层", 成熟城市住宅区: "成熟高端社区改善住宅",
      新建综合社区: "普通成熟社区刚需公寓", 高端低密社区: "城市近郊联排 / 叠墅", 私密庄园社区: "独栋花园别墅",
      山地森林: "独栋花园别墅", 滨海度假区: "独栋花园别墅",
    };
    const name = (params.cn_mode ? domestic : overseas)[params.site_context || ""];
    return catalog.presets.find((preset) => preset.value === name)?.defaults || {};
  }
  return {};
};

export interface SceneChangeResult {
  params: GenParams;
  corrections: string[];
}

export function applySceneChange(
  current: GenParams,
  patch: Partial<GenParams>,
  catalog: SceneCatalog,
): SceneChangeResult {
  const corrections: string[] = [];
  let merged: GenParams = { ...current, ...patch };
  let anchor = Object.keys(patch).find((key) => catalog.compatibility_rules.scene_fields.includes(key)) || "";

  if (Object.prototype.hasOwnProperty.call(patch, "cn_mode") && patch.cn_mode !== current.cn_mode) {
    const preset = marketPreset(catalog, !!patch.cn_mode);
    if (preset) {
      merged = { ...merged, ...preset.defaults, scene_preset: preset.value, scene_anchor: "scene_preset" };
      anchor = "scene_preset";
    }
  } else if (patch.scene_preset) {
    const preset = catalog.presets.find((item) => item.value === patch.scene_preset);
    if (preset?.defaults) merged = { ...merged, ...preset.defaults, scene_preset: preset.value, scene_anchor: "scene_preset" };
    anchor = "scene_preset";
  } else if (anchor) {
    merged.scene_anchor = anchor;
    merged.scene_preset = catalog.compatibility_rules.custom_preset;
  }

  const setCorrection = (key: keyof GenParams, next: string) => {
    const previous = String(merged[key] || "未指定");
    if (!next || previous === next) return;
    merged = { ...merged, [key]: next };
    corrections.push(`${FIELD_LABELS[String(key)] || String(key)}：${previous} → ${next}`);
  };

  const dependentDefaults = presetDefaultsForAnchor(merged, anchor, catalog);
  for (const [key, value] of Object.entries(dependentDefaults)) {
    if (key !== anchor && typeof value === "string") setCorrection(key as keyof GenParams, value);
  }

  let kind = propertyKind(merged, catalog);
  if (anchor === "floor_level") {
    if (["高层 16–30F", "超高层 31F+"].includes(merged.floor_level || "") && ["house", "villa", "cabin", "courtyard"].includes(kind)) {
      setCorrection(merged.cn_mode ? "cn_unit_type" : "property_type", merged.cn_mode ? "改善大平层 (160-220㎡)" : "核心城区高层公寓");
    } else if (merged.floor_level === "独栋住宅内部楼层" && ["apartment", "loft"].includes(kind)) {
      setCorrection(merged.cn_mode ? "cn_unit_type" : "property_type", merged.cn_mode ? "独栋别墅" : "普通独立住宅");
    }
  }
  if (anchor === "room_layout" && merged.room_layout === "挑高 / 复式布局") {
    setCorrection(merged.cn_mode ? "cn_unit_type" : "property_type", merged.cn_mode ? "复式 / 跃层" : "Loft / 仓库改造住宅");
    setCorrection("floor_level", "低层 2–5F");
  }
  const viewKey: "view" | "cn_view" = merged.cn_mode ? "cn_view" : "view";
  if (anchor === "window_type" && ["高侧窗 / 天窗", "不强调窗 / 弱化窗景"].includes(merged.window_type || "")) {
    setCorrection(viewKey, "无明显窗外景观");
  }

  const viewValue = LEGACY_VIEW[merged[viewKey] || ""] || merged[viewKey] || "";
  if (viewValue !== merged[viewKey]) merged[viewKey] = viewValue;
  const view = optionByValue(flatViewOptions(catalog), viewValue);
  if (!view) {
    if (corrections.length > 0 && merged.scene_preset !== catalog.compatibility_rules.legacy_preset) {
      merged.scene_preset = catalog.compatibility_rules.custom_preset;
    }
    return { params: merged, corrections };
  }

  kind = propertyKind(merged, catalog);
  const allowedKinds = asStrings(view.compatibility.allowed_property_kinds);
  const allowedFloors = asStrings(view.compatibility.allowed_floor_levels);
  const incompatibleKind = allowedKinds.length > 0 && !!kind && !allowedKinds.includes(kind);
  const incompatibleFloor = allowedFloors.length > 0 && !allowedFloors.includes(merged.floor_level || "");

  if (["view", "cn_view"].includes(anchor) && (incompatibleKind || incompatibleFloor)) {
    if (incompatibleKind) {
      if (merged.cn_mode) setCorrection("cn_unit_type", asString(view.compatibility.preferred_cn_unit));
      else setCorrection("property_type", asString(view.compatibility.preferred_property));
    }
    if (incompatibleFloor) setCorrection("floor_level", asString(view.compatibility.preferred_floor));
    setCorrection("window_type", asString(view.compatibility.preferred_window));
    setCorrection("site_context", asString(view.compatibility.preferred_site));
  } else if (incompatibleKind || incompatibleFloor) {
    setCorrection(viewKey, fallbackView(kind, merged.floor_level || "", merged.site_context || ""));
  }

  if (corrections.length > 0 && merged.scene_preset !== catalog.compatibility_rules.legacy_preset) {
    merged.scene_preset = catalog.compatibility_rules.custom_preset;
  }
  return { params: merged, corrections };
}

export function hydrateSceneParams(params: GenParams, catalog: SceneCatalog): GenParams {
  const cnMode = !!params.cn_mode;
  const preset = marketPreset(catalog, cnMode);
  const mapped: GenParams = {
    ...params,
    property_type: LEGACY_PROPERTY[params.property_type || ""] || params.property_type,
    room_type: LEGACY_ROOM[params.room_type || ""] || params.room_type,
    view: LEGACY_VIEW[params.view || ""] || params.view,
    cn_view: LEGACY_VIEW[params.cn_view || ""] || params.cn_view,
  };
  const hasStructuredScene = ["site_context", "floor_level", "room_scale", "room_layout", "window_type"]
    .some((key) => Boolean(mapped[key as keyof GenParams]));
  const base: GenParams = {
    ...(preset?.defaults || {}),
    ...mapped,
    scene_preset: mapped.scene_preset || (hasStructuredScene ? catalog.compatibility_rules.custom_preset : catalog.compatibility_rules.legacy_preset),
    scene_anchor: mapped.scene_anchor || (cnMode ? "cn_unit_type" : "property_type"),
  };
  return applySceneChange(base, {}, catalog).params;
}

export function sceneSummary(params: GenParams): string {
  const property = params.cn_mode ? params.cn_unit_type : params.property_type;
  const view = params.cn_mode ? params.cn_view : params.view;
  return [params.scene_preset, property, params.site_context, params.floor_level, params.room_scale, params.room_layout, params.window_type, view]
    .filter((value) => value && value !== "自定义组合" && value !== "历史参数 / 自定义组合")
    .join(" · ");
}
