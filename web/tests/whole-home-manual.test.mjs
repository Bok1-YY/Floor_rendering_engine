import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const pageUrl = new URL("../src/app/floorplan/page.tsx", import.meta.url);
const studioUrl = new URL("../src/components/WholeHomeStudio.tsx", import.meta.url);
const apiUrl = new URL("../src/lib/api.ts", import.meta.url);


test("manual-safe defaults to the local style pack without selecting a paid model", async () => {
  const page = await readFile(pageUrl, "utf8");
  assert.match(page, /useState<"2K" \| "4K">\("2K"\)/);
  assert.match(page, /useState<WholeHomeMaterialMode>\("style_pack"\)/);
  assert.match(page, /useState<\("b2" \| "pro"\)\[]>\(\[]\)/);
  assert.match(page, /useState<string\[]>\(\[]\)/);
  assert.match(page, /activeSceneRecipe\?\.status === "locked"/);
  assert.match(page, /生成只读付费预览/);
});


test("saving automatic or manual cameras never submits a run", async () => {
  const page = await readFile(pageUrl, "utf8");
  const start = page.indexOf("async function finishAutoCapture");
  const end = page.indexOf("async function commitManualPreview", start);
  assert.ok(start >= 0 && end > start);
  const finish = page.slice(start, end);
  assert.doesNotMatch(finish, /submitRun\(/);
  assert.match(finish, /不会自动提交生图/);
});


test("manual generation is preview then exact-phrase commit", async () => {
  const [page, api] = await Promise.all([
    readFile(pageUrl, "utf8"), readFile(apiUrl, "utf8"),
  ]);
  assert.match(page, /api\.previewWholeHomeManualRun\(/);
  assert.match(page, /manualConfirmation !== manualPreview\.confirmation_phrase/);
  assert.match(page, /api\.commitWholeHomeManualRun\(/);
  assert.match(api, /\/api\/whole-home\/manual\/runs\/preview/);
  assert.match(api, /\/api\/whole-home\/manual\/runs\/commit/);
});


test("manual-safe hides reference, automatic camera and retry controls", async () => {
  const [page, studio] = await Promise.all([
    readFile(pageUrl, "utf8"), readFile(studioUrl, "utf8"),
  ]);
  assert.match(page, /!manualSafe && project\?\.source_type === "cad" && <ReferenceContractPanel/);
  assert.match(page, /!manualSafe && unavailableQa > 0/);
  assert.match(page, /!manualSafe \|\| activeReviewState\?\.round_status !== "review_complete"/);
  assert.match(studio, /if \(manualSafe\) throw new Error\("手动安全模式不开放 AI 自动机位/);
});


test("ordinary image input remains visible in manual-safe generation mode", async () => {
  const page = await readFile(pageUrl, "utf8");
  assert.match(page, /data-testid="whole-home-image-source-tab"/);
  assert.match(page, /data-testid="whole-home-image-analyze"/);
  assert.doesNotMatch(page, /!manualSafe && <button[^>]+whole-home-image-source-tab/);
  assert.doesNotMatch(page, /!manualSafe && <div[^>]*>\s*<Button[^>]+whole-home-image-analyze/);
});
