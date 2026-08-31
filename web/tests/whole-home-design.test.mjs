import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../src/app/design/page.tsx", import.meta.url), "utf8");
const shell = readFileSync(new URL("../src/components/AppShell.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/lib/types.ts", import.meta.url), "utf8");
const viewer = readFileSync(new URL("../src/components/PanoViewer.tsx", import.meta.url), "utf8");
const anchors = readFileSync(new URL("../src/components/FloorplanAnchorEditor.tsx", import.meta.url), "utf8");
const structure = readFileSync(new URL("../src/components/StructureResearchPanel.tsx", import.meta.url), "utf8");

test("navigation replaces whole-home VR with whole-home design", () => {
  assert.match(shell, /href: "\/design"/);
  assert.match(shell, /label: "全屋设计"/);
  assert.doesNotMatch(shell, /href: "\/floorplan"/);
});

test("design page exposes two zoomable 2K concepts and strict structure review", () => {
  assert.match(page, /两张 2K 设计草稿/);
  assert.match(page, /锁定 2K 方案与高级 Agent 任务包/);
  assert.match(page, /点击放大/);
  assert.match(page, /<ImageZoom url=\{zoomUrl\}/);
  assert.match(page, /必须逐项核对并确认全部结构硬项/);
  assert.match(page, /自动\/人工 QA 已发现结构硬错误，不能覆写/);
  assert.match(page, /Blender Agent 建模任务包/);
  assert.match(page, /Gemini 双重识别/);
  assert.match(page, /不能确认空摘要/);
  assert.match(page, /AI 标出的待确认项/);
});

test("manual anchors use normalized coordinates and gate Gemini recognition", () => {
  assert.match(anchors, /Math\.min\(1000|0–1000|规范化证据图左上角/);
  assert.match(anchors, /mapClientPointToNormalized/);
  assert.match(anchors, /我已标完所有空间、入户门和一条真实比例尺/);
  assert.match(anchors, /比例尺真实长度毫米/);
  assert.match(page, /saveWholeHomeDesignAnchors/);
  assert.match(page, /anchor_verification\?\.status === "verified"/);
  assert.match(page, /Gemini 自动补充/);
});

test("research fast lane exposes nine questions and direct Blender artifacts", () => {
  assert.match(page, /StructureResearchPanel/);
  assert.match(structure, /九问结构确认与 Blender 研究灰模/);
  assert.match(structure, /生成 Blender \/ GLB \/ 研究 IFC/);
  assert.match(structure, /external_review_pending/);
  assert.match(api, /structure-review/);
  assert.match(api, /model-runs/);
  assert.match(types, /research-structure-bundle-v1|DesignStructureReview/);
});

test("paid calls stop after the two 2K concept endpoints", () => {
  assert.match(api, /drafts\/preview/);
  assert.match(api, /drafts\/commit/);
  assert.doesNotMatch(api, /refine\/preview/);
  assert.doesNotMatch(api, /refine\/commit/);
  assert.doesNotMatch(page, /Pro 4K|4K 精修|refinementText/);
  assert.match(page, /失败或结构不通过不会自动重新付费/);
});

test("project list rows are hydrated through the detail endpoint before rendering", () => {
  assert.match(types, /WholeHomeDesignProjectListItem/);
  assert.match(api, /jget<WholeHomeDesignProjectListItem\[\]>/);
  assert.match(page, /getWholeHomeDesignProject\(items\[0\]\.project_id\)/);
  assert.doesNotMatch(page, /setProject\(items\[0\]\)/);
  assert.match(page, /project\.plan_summary\?\.source/);
  assert.doesNotMatch(page, /project\.plan_summary\.source/);
});

test("whole-home design owns a bounded vertical scroll container", () => {
  assert.match(page, /data-testid="whole-home-design-scroll"/);
  assert.match(page, /className="[^"]*h-full[^"]*min-h-0[^"]*overflow-y-auto[^"]*"/);
  assert.doesNotMatch(page, /grid min-h-full grid-cols-\[260px_minmax\(0,1fr\)\]/);
});

test("each 2K concept can be zoomed and locked directly", () => {
  assert.match(page, /aria-label=\{`放大预览 2K 方案方向/);
  assert.match(page, /onZoom=\{\(\) => candidate\.url && setZoomUrl/);
  assert.match(page, /锁定此 2K 方案/);
  assert.doesNotMatch(page, /onRefine|openRefinePreview|精修要求/);
});

test("historical panorama viewer is explicitly read only", () => {
  assert.match(viewer, /Historical ERP viewer only/);
  assert.match(viewer, /历史 360° 只读查看/);
  assert.doesNotMatch(viewer, /paid-preview|floor\/apply|onRequestRepair/i);
});
