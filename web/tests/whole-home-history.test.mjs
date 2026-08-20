import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("whole-home history opens the immutable replay endpoint instead of the current project", async () => {
  const page = await readFile(new URL("../src/app/floorplan/page.tsx", import.meta.url), "utf8");
  assert.match(page, /api\.getWholeHomeRunReplay\(item\.run_id\)/);
  assert.match(page, /applyProject\(replay\.history_project\)/);
  assert.match(page, /历史只读快照/);
  assert.match(page, /project\.history_read_only/);
  assert.doesNotMatch(page, /api\.getWholeHomeProject\(item\.project_id\),\s*api\.getWholeHomeLearningSummary/);
});

test("history panel requires copy-on-write and an explicit aggregate paid confirmation", async () => {
  const component = await readFile(new URL("../src/components/WholeHomeHistoryPanel.tsx", import.meta.url), "utf8");
  assert.match(component, /复制为新方案/);
  assert.match(component, /预览整套付费清单（零调用）/);
  assert.match(component, /batchConfirmation !== batch\.confirmation_phrase/);
  assert.match(component, /api\.commitWholeHomeVariantBatch/);
  assert.match(component, /取消尚未开始的项/);
  assert.doesNotMatch(component, /useEffect\([\s\S]{0,300}commitWholeHomeVariantBatch/);
});

test("style variants use a dedicated lineage contract and keep review continuation separate", async () => {
  const [types, api] = await Promise.all([
    readFile(new URL("../src/lib/types.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(types, /variant_of_run_id\?: string/);
  assert.match(types, /interface WholeHomeProjectLineage/);
  assert.match(types, /interface WholeHomeVariantBatch/);
  assert.match(api, /\/api\/whole-home\/runs\/\$\{encodeURIComponent\(id\)\}\/fork/);
  assert.match(api, /\/api\/whole-home\/variant-batches\/preview/);
});
