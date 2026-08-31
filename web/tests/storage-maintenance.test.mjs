import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settings = readFileSync(new URL("../src/app/settings/page.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/components/GenerationWorkspace.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");

test("storage maintenance is an explicit scan then cleanup flow", () => {
  assert.match(api, /storageAudit/);
  assert.match(api, /cleanupStorage/);
  assert.match(api, /snapshot_id/);
  assert.match(settings, /扫描存储/);
  assert.match(settings, /备份并清理/);
  assert.match(settings, /生成结果不会批量去重或删除/);
});

test("clearing completed jobs explicitly preserves images and records", () => {
  assert.match(page, /清除已完成任务卡/);
  assert.match(page, /图片和历史记录均已保留/);
});
