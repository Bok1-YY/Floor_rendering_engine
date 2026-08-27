import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../src/app/design/page.tsx", import.meta.url), "utf8");
const shell = readFileSync(new URL("../src/components/AppShell.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const viewer = readFileSync(new URL("../src/components/PanoViewer.tsx", import.meta.url), "utf8");

test("navigation replaces whole-home VR with whole-home design", () => {
  assert.match(shell, /href: "\/design"/);
  assert.match(shell, /label: "全屋设计"/);
  assert.doesNotMatch(shell, /href: "\/floorplan"/);
});

test("design page exposes two drafts, 4K refine and strict structure review", () => {
  assert.match(page, /两张 2K 设计草稿/);
  assert.match(page, /4K 精修/);
  assert.match(page, /必须逐项核对并确认全部结构硬项/);
  assert.match(page, /自动\/人工 QA 已发现结构硬错误，不能覆写/);
  assert.match(page, /Blender Agent 建模任务包/);
  assert.match(page, /自动识别\/重新识别/);
  assert.match(page, /不能确认空摘要/);
  assert.match(page, /AI 标出的待确认项/);
});

test("paid calls use separate preview and commit endpoints", () => {
  assert.match(api, /drafts\/preview/);
  assert.match(api, /drafts\/commit/);
  assert.match(api, /refine\/preview/);
  assert.match(api, /refine\/commit/);
  assert.match(page, /失败或结构不通过不会自动重新付费/);
});

test("project list hydrates the selected project before rendering plan summary fields", () => {
  assert.match(page, /api\.getWholeHomeDesignProject\(items\[0\]\.project_id\)/);
  assert.doesNotMatch(page, /if \(items\[0\]\) setProject\(items\[0\]\)/);
});

test("whole-home design owns a bounded vertical scroll container", () => {
  assert.match(page, /data-testid="whole-home-design-scroll"/);
  assert.match(page, /className="[^"]*h-full[^"]*min-h-0[^"]*overflow-y-auto[^"]*"/);
  assert.doesNotMatch(page, /grid min-h-full grid-cols-\[260px_minmax\(0,1fr\)\]/);
});

test("draft inputs are isolated by candidate and 2K images open the zoom viewer", () => {
  assert.match(page, /refinementTextByCandidate\[candidate\.candidate_id\]/);
  assert.match(page, /\[candidate\.candidate_id\]: value/);
  assert.doesNotMatch(page, /const \[refinementText, setRefinementText\]/);
  assert.match(page, /aria-label=\{`放大预览 2K 方案方向/);
  assert.match(page, /<ImageZoom url=\{zoomUrl\}/);
});

test("historical panorama viewer is explicitly read only", () => {
  assert.match(viewer, /Historical ERP viewer only/);
  assert.match(viewer, /历史 360° 只读查看/);
  assert.doesNotMatch(viewer, /paid-preview|floor\/apply|onRequestRepair/i);
});
