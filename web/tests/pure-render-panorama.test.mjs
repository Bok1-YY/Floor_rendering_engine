import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  emptyPureRenderPanoChecklist,
  panoramaGateLabel,
  pureRenderPanoChecklistComplete,
  pureRenderPanoChecklistPassed,
} from "../src/lib/pureRenderPano.ts";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("pure panorama review requires all six explicit decisions", () => {
  const checklist = emptyPureRenderPanoChecklist();
  assert.equal(pureRenderPanoChecklistComplete(checklist), false);
  for (const key of Object.keys(checklist)) checklist[key] = "pass";
  assert.equal(pureRenderPanoChecklistComplete(checklist), true);
  assert.equal(pureRenderPanoChecklistPassed(checklist), true);
  checklist.poles = "uncertain";
  assert.equal(pureRenderPanoChecklistPassed(checklist), false);
  assert.equal(panoramaGateLabel("repair_recommended"), "建议修复结构/接缝");
});

test("ordinary submit types cannot target the derived vr360 slot", async () => {
  const source = await read("../src/lib/types.ts");
  assert.match(source, /type GenerationModelKey = "b2" \| "pro" \| "sd35"/);
  assert.match(source, /type JobSlotKey = GenerationModelKey \| "vr360"/);
  assert.match(source, /model_targets\?: GenerationModelKey\[\]/);
});

test("job card converts the selected candidate through a paid preview", async () => {
  const source = await read("../src/components/JobCard.tsx");
  assert.match(source, /生成 360° VR/);
  assert.match(source, /source_model: activeGenerationKey/);
  assert.match(source, /source_index: activeSlot\.idx/);
  assert.match(source, /activeSlot\.key === "vr360" \? "aspect-\[2\/1\]"/);
  assert.match(source, /PureRenderPanoramaViewerDialog/);
  assert.match(source, /恢复已有 Fal 结果/);
  assert.match(source, /不会重新提交生图/);
  assert.match(source, /job\.panorama_resume/);
});

test("paid dialog makes cost cap and AI expansion limitation visible", async () => {
  const source = await read("../src/components/PureRenderPanoramaDialogs.tsx");
  assert.match(source, /本次确认最多 1 次供应商调用/);
  assert.match(source, /不是可行走三维空间/);
  assert.match(source, /不能用于量尺或施工判断/);
  assert.match(source, /action: "repair"/);
  assert.match(source, /360° VR 质量导演规划/);
  assert.match(source, /Gemini 规划 · 缓存命中/);
  assert.match(source, /本地规则回退/);
  assert.match(source, /查看交给 Fal 的英文导演指令/);
});

test("direct sphere workflow is visible and uses a two-engine paid confirmation", async () => {
  const page = await read("../src/app/page.tsx");
  const params = await read("../src/components/ParamsForm.tsx");
  const output = await read("../src/components/OutputForm.tsx");
  const dialogs = await read("../src/components/PureRenderPanoramaDialogs.tsx");
  const client = await read("../src/lib/api.ts");

  assert.match(params, /球面效果图: "同球心六面图集直出/);
  assert.match(page, /生成球面 VR/);
  assert.match(page, /DirectPanoramaPaidDialog/);
  assert.match(output, /B2 与 GPT Image 2 各生成一张同球心 3×2 六面图集/);
  assert.match(output, /3840×1920 · 2:1 ERP/);
  assert.match(dialogs, /确认并提交两条候选/);
  assert.match(dialogs, /本次确认最多 \{preview\.max_provider_calls\} 次/);
  assert.match(dialogs, /六面方向合同/);
  assert.match(dialogs, /确认后也不会重新规划/);
  assert.match(dialogs, /原厂彩膜物理分切合同/);
  assert.match(dialogs, /原厂彩膜路线材质扩展费用 0 次/);
  assert.match(params, /空间参考效果图（可选 · 本地几何锚点）/);
  assert.match(dialogs, /本地相机与地板几何合同/);
  assert.match(dialogs, /最终彩膜不会沿用模型生成的错缝木纹/);
  assert.match(client, /room_reference_path\?: string/);
  assert.match(client, /\/api\/jobs\/panorama-direct\/preview/);
  assert.match(client, /\/api\/jobs\/panorama-direct\/commit/);
});

test("manufacturer repeat film is a physical optional product input", async () => {
  const [page, panel, client, types] = await Promise.all([
    read("../src/app/page.tsx"),
    read("../src/components/FilmRepeatPanel.tsx"),
    read("../src/lib/api.ts"),
    read("../src/lib/types.ts"),
  ]);
  assert.match(page, /FilmRepeatPanel/);
  assert.match(panel, /原厂整体彩膜/);
  assert.match(panel, /彩膜宽度 mm/);
  assert.match(panel, /长边重复周期 mm/);
  assert.match(panel, /标签自动识别并避让/);
  assert.match(client, /\/api\/uploads\/film/);
  assert.match(types, /film_repeat_length_mm/);
});

test("records detect panoramas per result and keep immutable legacy fallback", async () => {
  const source = await read("../src/app/records/page.tsx");
  assert.match(source, /const purePanorama = res\.generation_metadata\?\.panorama/);
  assert.match(source, /immutableAudit && panoAudit/);
  assert.match(source, /purePanorama\?\.projection === "equirectangular" \|\| isLegacyPanoRecord/);
  assert.match(source, /initialYawDeg=\{panoView\.initialYawDeg\}/);
});

test("viewer exposes a pure-render checklist without changing whole-home callback", async () => {
  const source = await read("../src/components/PanoViewer.tsx");
  assert.match(source, /reviewProfile\?: "whole_home" \| "pure_render"/);
  assert.match(source, /onChecklistResult\?: \(result: PanoChecklistResult\)/);
  assert.match(source, /onPureChecklistResult\?: \(result: PureRenderPanoramaReviewChecklist\)/);
  assert.match(source, /PURE_RENDER_PANO_REVIEW_ITEMS/);
});

test("panorama floor correction uses five editable views and local preview/apply APIs", async () => {
  const [dialog, card, client, viewer] = await Promise.all([
    read("../src/components/PanoramaFloorDialog.tsx"),
    read("../src/components/JobCard.tsx"),
    read("../src/lib/api.ts"),
    read("../src/components/PanoViewer.tsx"),
  ]);
  assert.match(card, /本地几何\/地板校准/);
  assert.match(dialog, /前、右、后、左、脚下五个本地遮罩/);
  assert.match(dialog, /擦除家具\/墙体/);
  assert.match(dialog, /使用这个结果/);
  assert.match(client, /panorama\/floor\/prepare/);
  assert.match(client, /panorama\/floor\/preview/);
  assert.match(client, /panorama\/floor\/apply/);
  assert.match(viewer, /自然 90°（水平）/);
  assert.match(viewer, /边缘为超广角拉伸/);
});
