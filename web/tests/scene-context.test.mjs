import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const form = readFileSync(new URL("../src/components/ParamsForm.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/components/GenerationWorkspace.tsx", import.meta.url), "utf8");
const scene = readFileSync(new URL("../src/lib/scene.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/lib/types.ts", import.meta.url), "utf8");

test("scene controls expose presets, spatial dimensions, grouped views and summary", () => {
  for (const label of ["场景预设", "地段类型", "楼层关系", "房间尺度", "空间布局", "窗型 / 开口", "窗外景观", "当前场景约束"]) {
    assert.match(form, new RegExp(label.replace("/", "\\/")));
  }
  assert.doesNotMatch(form, /更多场景参数 · 12 项/);
  assert.match(types, /scene_catalog: SceneCatalog/);
  assert.match(types, /view_options: \{ group: string; options: SceneOption\[\] \}\[\]/);
});

test("drafts, reuse and live edits pass through scene normalization", () => {
  assert.match(page, /hydrateSceneParams\(\{ \.\.\.buildDefaultParams\(o\), \.\.\.reuse\.params \}/);
  assert.match(page, /hydrateSceneParams\(draft\?\.params/);
  assert.match(page, /applySceneChange\(paramsRef\.current, patch, options\.scene_catalog\)/);
  assert.match(page, /为保持场景合理，已联动/);
});

test("latest selection compatibility policy and legacy aliases are explicit", () => {
  assert.match(scene, /\["view", "cn_view"\]\.includes\(anchor\)/);
  assert.match(scene, /scene_anchor/);
  assert.match(scene, /现代别墅: "现代花园别墅"/);
  assert.match(scene, /自然通透景观: "树木遮挡的局部景观"/);
  assert.match(scene, /fallbackView/);
});
