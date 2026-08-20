export const WHOLE_HOME_RENDER_GATE_VERSION = "whole-home-render-gate-v2";

export const WHOLE_HOME_SEMANTIC_COLORS: Readonly<Record<string, string>> = Object.freeze({
  wall: "#d8d4ca",
  floor: "#bcb6aa",
  kitchen_run: "#d97706",
  sink: "#0ea5e9",
  hob: "#ef4444",
  fridge: "#64748b",
  basin: "#06b6d4",
  toilet: "#f8fafc",
  shower_zone: "#67e8f9",
  bed: "#8b5cf6",
  wardrobe: "#a16207",
  sofa: "#ec4899",
  tv: "#111827",
  dining_table: "#84cc16",
  entry_storage: "#f59e0b",
  balcony_rail: "#22c55e",
  washing_machine: "#3b82f6",
  other: "#94a3b8",
});

export const WHOLE_HOME_RENDER_GATE_THRESHOLDS = Object.freeze({
  floor_min: 0.08,
  wall_max: 0.72,
  semantic_role_peak_max: 0.40,
  bedroom_bed_min: 0.035,
  bathroom_fixture_min: 0.01,
  bathroom_fixture_count_min: 2,
  kitchen_run_min: 0.03,
  kitchen_appliance_min: 0.01,
  living_sofa_min: 0.03,
  living_tv_min: 0.005,
  foyer_storage_min: 0.025,
  balcony_anchor_min: 0.02,
  rgb_tolerance: 10,
});

export type WholeHomeRenderGateProfile =
  | "kitchen"
  | "bathroom"
  | "bedroom"
  | "living_room"
  | "foyer"
  | "balcony"
  | "other";

export interface WholeHomeRenderGateGroupVerdict {
  key: string;
  passed: boolean;
  roles: string[];
  minimum_fraction?: number;
  minimum_count?: number;
  passing_roles: string[];
}

export interface WholeHomeRenderGateResult {
  version: string;
  pass: boolean;
  status: "pass" | "blocked";
  profile: WholeHomeRenderGateProfile;
  denominator_pixels: number;
  matched_pixels: number;
  unmatched_pixels: number;
  floor_fraction: number;
  wall_fraction: number;
  peak_semantic_role: string;
  peak_semantic_role_fraction: number;
  semantic_role_fractions: Record<string, number>;
  required_groups: WholeHomeRenderGateGroupVerdict[];
  reasons: string[];
}

export function evaluateReferenceBaseRenderGate(
  result: WholeHomeRenderGateResult,
): WholeHomeRenderGateResult {
  const thresholds = WHOLE_HOME_RENDER_GATE_THRESHOLDS;
  const reasons: string[] = [];
  if (result.floor_fraction < thresholds.floor_min) {
    reasons.push(`地板仅 ${percent(result.floor_fraction)}，低于 ${percent(thresholds.floor_min)}`);
  }
  if (result.wall_fraction > thresholds.wall_max) {
    reasons.push(`墙面达到 ${percent(result.wall_fraction)}，高于 ${percent(thresholds.wall_max)}`);
  }
  if (result.peak_semantic_role
      && result.peak_semantic_role_fraction > thresholds.semantic_role_peak_max) {
    reasons.push(`${result.peak_semantic_role} 占画面 ${percent(result.peak_semantic_role_fraction)}，高于单一语义角色上限 ${percent(thresholds.semantic_role_peak_max)}`);
  }
  return {
    ...result,
    version: `${WHOLE_HOME_RENDER_GATE_VERSION}-reference-base-v1`,
    pass: reasons.length === 0,
    status: reasons.length === 0 ? "pass" : "blocked",
    // Slot-specific visibility is verified by the depth-tested subject-ID
    // buffer.  Generic room groups (for example living_room=>sofa) must not
    // override a contract that deliberately asks for dining+TV+corridor.
    required_groups: [],
    reasons,
  };
}

export interface WholeHomeRenderCandidateLike {
  candidate_id: string;
  room_id: string;
  local_score: number;
  camera: { focal_length_mm: number };
  metrics: { render_gate?: WholeHomeRenderGateResult; [key: string]: unknown };
}

export interface WholeHomeRenderCandidateDecision<T extends WholeHomeRenderCandidateLike> {
  eligible: T[];
  rejected: T[];
  room_results: Record<string, {
    eligible: T[];
    rejected: T[];
    used_deferred_20mm: boolean;
    reasons: string[];
    best_rejected_gate?: WholeHomeRenderGateResult;
  }>;
}

function fraction(value: number | undefined): number {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, Number(value))) : 0;
}

function percent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function roleFraction(fractions: Record<string, number>, role: string): number {
  return fraction(fractions[role]);
}

export function evaluateWholeHomeRenderFractions(
  profile: WholeHomeRenderGateProfile,
  rawFractions: Record<string, number>,
  pixelCounts: { denominator: number; matched: number } = { denominator: 0, matched: 0 },
): WholeHomeRenderGateResult {
  const fractions = Object.fromEntries(
    Object.entries(rawFractions).map(([role, value]) => [role, fraction(value)]),
  );
  const floorFraction = roleFraction(fractions, "floor");
  const wallFraction = roleFraction(fractions, "wall");
  const peak = Object.entries(fractions)
    .filter(([role]) => !["floor", "wall", "other"].includes(role))
    .sort((left, right) => right[1] - left[1])[0] || ["", 0];
  const peakRole = peak[0];
  const peakFraction = fraction(peak[1]);
  const reasons: string[] = [];
  const requiredGroups: WholeHomeRenderGateGroupVerdict[] = [];
  const thresholds = WHOLE_HOME_RENDER_GATE_THRESHOLDS;

  if (floorFraction < thresholds.floor_min) {
    reasons.push(`地板仅 ${percent(floorFraction)}，低于 ${percent(thresholds.floor_min)}`);
  }
  if (wallFraction > thresholds.wall_max) {
    reasons.push(`墙面达到 ${percent(wallFraction)}，高于 ${percent(thresholds.wall_max)}`);
  }
  if (peakRole && peakFraction > thresholds.semantic_role_peak_max) {
    reasons.push(`${peakRole} 占画面 ${percent(peakFraction)}，高于单一语义角色上限 ${percent(thresholds.semantic_role_peak_max)}`);
  }

  const addMinimumGroup = (key: string, roles: string[], minimum: number) => {
    const passing = roles.filter((role) => roleFraction(fractions, role) >= minimum);
    const passed = passing.length > 0;
    requiredGroups.push({
      key,
      passed,
      roles,
      minimum_fraction: minimum,
      passing_roles: passing,
    });
    return passed;
  };

  if (profile === "bedroom") {
    if (!addMinimumGroup("bedroom_bed", ["bed"], thresholds.bedroom_bed_min)) {
      reasons.push(`床体仅 ${percent(roleFraction(fractions, "bed"))}，低于 ${percent(thresholds.bedroom_bed_min)}`);
    }
  } else if (profile === "bathroom") {
    const roles = ["basin", "toilet", "shower_zone"];
    const passing = roles.filter((role) => roleFraction(fractions, role) >= thresholds.bathroom_fixture_min);
    const passed = passing.length >= thresholds.bathroom_fixture_count_min;
    requiredGroups.push({
      key: "bathroom_two_fixtures",
      passed,
      roles,
      minimum_fraction: thresholds.bathroom_fixture_min,
      minimum_count: thresholds.bathroom_fixture_count_min,
      passing_roles: passing,
    });
    if (!passed) {
      reasons.push(`卫浴洁具需至少 2 类各达到 ${percent(thresholds.bathroom_fixture_min)}，当前 ${passing.length} 类`);
    }
  } else if (profile === "kitchen") {
    if (!addMinimumGroup("kitchen_run", ["kitchen_run"], thresholds.kitchen_run_min)) {
      reasons.push(`厨房操作台仅 ${percent(roleFraction(fractions, "kitchen_run"))}，低于 ${percent(thresholds.kitchen_run_min)}`);
    }
    if (!addMinimumGroup("kitchen_appliance", ["sink", "hob", "fridge"], thresholds.kitchen_appliance_min)) {
      reasons.push(`水槽/灶台/冰箱均未达到 ${percent(thresholds.kitchen_appliance_min)}`);
    }
  } else if (profile === "living_room") {
    if (!addMinimumGroup("living_sofa", ["sofa"], thresholds.living_sofa_min)) {
      reasons.push(`沙发仅 ${percent(roleFraction(fractions, "sofa"))}，低于 ${percent(thresholds.living_sofa_min)}`);
    }
    if (!addMinimumGroup("living_tv", ["tv"], thresholds.living_tv_min)) {
      reasons.push(`电视仅 ${percent(roleFraction(fractions, "tv"))}，低于 ${percent(thresholds.living_tv_min)}`);
    }
  } else if (profile === "foyer") {
    if (!addMinimumGroup("foyer_storage", ["entry_storage"], thresholds.foyer_storage_min)) {
      reasons.push(`玄关柜仅 ${percent(roleFraction(fractions, "entry_storage"))}，低于 ${percent(thresholds.foyer_storage_min)}`);
    }
  } else if (profile === "balcony") {
    if (!addMinimumGroup("balcony_anchor", ["washing_machine", "balcony_rail"], thresholds.balcony_anchor_min)) {
      reasons.push(`洗衣机/栏板均未达到 ${percent(thresholds.balcony_anchor_min)}`);
    }
  }

  return {
    version: WHOLE_HOME_RENDER_GATE_VERSION,
    pass: reasons.length === 0,
    status: reasons.length === 0 ? "pass" : "blocked",
    profile,
    denominator_pixels: Math.max(0, Math.floor(pixelCounts.denominator)),
    matched_pixels: Math.max(0, Math.floor(pixelCounts.matched)),
    unmatched_pixels: Math.max(0, Math.floor(pixelCounts.denominator - pixelCounts.matched)),
    floor_fraction: floorFraction,
    wall_fraction: wallFraction,
    peak_semantic_role: peakRole,
    peak_semantic_role_fraction: peakFraction,
    semantic_role_fractions: fractions,
    required_groups: requiredGroups,
    reasons,
  };
}

function hexToRgb(value: string): [number, number, number] {
  const hex = value.replace("#", "");
  return [
    Number.parseInt(hex.slice(0, 2), 16),
    Number.parseInt(hex.slice(2, 4), 16),
    Number.parseInt(hex.slice(4, 6), 16),
  ];
}

export function analyzeWholeHomeSemanticPixels(
  rgba: ArrayLike<number>,
  width: number,
  height: number,
  profile: WholeHomeRenderGateProfile,
  colors: Readonly<Record<string, string>> = WHOLE_HOME_SEMANTIC_COLORS,
  tolerance = WHOLE_HOME_RENDER_GATE_THRESHOLDS.rgb_tolerance,
): WholeHomeRenderGateResult {
  const expectedLength = Math.max(0, Math.floor(width)) * Math.max(0, Math.floor(height)) * 4;
  if (!expectedLength || rgba.length < expectedLength) {
    throw new Error("semantic buffer 像素尺寸不完整");
  }
  const palette = Object.entries(colors).map(([role, color]) => ({ role, rgb: hexToRgb(color) }));
  const counts: Record<string, number> = {};
  let matched = 0;
  const toleranceSquared = tolerance * tolerance * 3;
  const denominator = expectedLength / 4;

  for (let offset = 0; offset < expectedLength; offset += 4) {
    if (Number(rgba[offset + 3]) === 0) continue;
    let bestRole = "";
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const entry of palette) {
      const dr = Number(rgba[offset]) - entry.rgb[0];
      const dg = Number(rgba[offset + 1]) - entry.rgb[1];
      const db = Number(rgba[offset + 2]) - entry.rgb[2];
      const distance = dr * dr + dg * dg + db * db;
      if (distance < bestDistance) {
        bestDistance = distance;
        bestRole = entry.role;
      }
    }
    if (bestRole && bestDistance <= toleranceSquared) {
      counts[bestRole] = (counts[bestRole] || 0) + 1;
      matched += 1;
    }
  }

  const fractions = Object.fromEntries(
    Object.entries(counts).map(([role, count]) => [role, count / denominator]),
  );
  return evaluateWholeHomeRenderFractions(profile, fractions, { denominator, matched });
}

export function filterWholeHomeRenderCandidates<T extends WholeHomeRenderCandidateLike>(
  candidates: T[],
  maxPerRoom = 8,
): WholeHomeRenderCandidateDecision<T> {
  const grouped = new Map<string, T[]>();
  for (const candidate of candidates) {
    grouped.set(candidate.room_id, [...(grouped.get(candidate.room_id) || []), candidate]);
  }
  const eligible: T[] = [];
  const rejected: T[] = [];
  const roomResults: WholeHomeRenderCandidateDecision<T>["room_results"] = {};
  for (const [roomId, rows] of grouped) {
    const valid = rows.filter((row) => row.metrics.render_gate?.pass === true);
    const base = valid.filter((row) => row.camera.focal_length_mm !== 20);
    const selected = (base.length ? base : valid.filter((row) => row.camera.focal_length_mm === 20))
      .sort((left, right) => right.local_score - left.local_score)
      .slice(0, Math.max(1, Math.min(8, maxPerRoom)));
    const selectedIds = new Set(selected.map((row) => row.candidate_id));
    const roomRejected = rows.filter((row) => !selectedIds.has(row.candidate_id));
    const hardRejected = rows.filter((row) => row.metrics.render_gate?.pass !== true);
    const bestRejected = [...hardRejected].sort((left, right) => {
      const leftGate = left.metrics.render_gate;
      const rightGate = right.metrics.render_gate;
      const leftDistance = (leftGate?.reasons.length || 99) - left.local_score / 1000;
      const rightDistance = (rightGate?.reasons.length || 99) - right.local_score / 1000;
      return leftDistance - rightDistance;
    })[0]?.metrics.render_gate;
    const reasons = selected.length ? [] : bestRejected
      ? [
          `灰模渲染门禁无合格机位；最佳候选地板 ${percent(bestRejected.floor_fraction)}、墙面 ${percent(bestRejected.wall_fraction)}`,
          ...bestRejected.reasons,
        ]
      : ["灰模渲染门禁无可审计候选"];
    eligible.push(...selected);
    rejected.push(...roomRejected);
    roomResults[roomId] = {
      eligible: selected,
      rejected: roomRejected,
      used_deferred_20mm: selected.length > 0 && base.length === 0,
      reasons,
      ...(bestRejected ? { best_rejected_gate: bestRejected } : {}),
    };
  }
  return { eligible, rejected, room_results: roomResults };
}
