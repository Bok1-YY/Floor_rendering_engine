"use client";
// 生成页草稿持久化：把「作业中」的配置存到 localStorage，避免切到记录页再回来时被清空。
// 只在 effect 里读写（不在渲染期），故不会引发 SSR / 水合不一致。

import type { GenParams, ModelFilter, ResolvedRecipe, Swatch } from "./types";

// 版本化 key：将来草稿结构不兼容时改 v2，旧草稿自然作废（loadDraft 读不到新 key → 回落默认）。
const KEY = "floor-engine:generate-draft:v1";

/** 需要跨页面/刷新保留的生成页状态切片。 */
export interface GenerateDraft {
  params: GenParams;
  modelFilter: ModelFilter;
  floor: Swatch | null;
  refImg: Swatch | null;
  roomImg: Swatch | null;
  recipes: ResolvedRecipe[];
}

/** 读草稿；无 / 损坏 / 非浏览器环境一律返回 null（调用方回落默认）。 */
export function loadDraft(): Partial<GenerateDraft> | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Partial<GenerateDraft>) : null;
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
