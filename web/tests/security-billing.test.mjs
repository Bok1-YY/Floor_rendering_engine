import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settings = readFileSync(new URL("../src/app/settings/page.tsx", import.meta.url), "utf8");
const jobCard = readFileSync(new URL("../src/components/JobCard.tsx", import.meta.url), "utf8");
const usage = readFileSync(new URL("../src/app/usage/page.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const storageHook = readFileSync(new URL("../src/hooks/useStorageMaintenance.ts", import.meta.url), "utf8");

test("settings exposes system keyring status and explicit secret deletion", () => {
  assert.match(settings, /安全后端/);
  assert.match(settings, /系统密钥环/);
  assert.match(settings, /清除 Key/);
  assert.match(api, /clearSecret/);
  assert.match(api, /method: "DELETE"/);
});

test("ambiguous paid calls require an explicit duplicate-charge confirmation", () => {
  assert.match(jobCard, /结果状态不确定/);
  assert.match(jobCard, /再次生成（可能重复计费）/);
  assert.match(jobCard, /api\.retryJob\(job\.job_id, hasAmbiguousBilling\)/);
  assert.match(api, /confirmPossibleDuplicateCharge/);
  assert.match(usage, /成本区间/);
  assert.match(usage, /结果不确定/);
});

test("orphan files use recoverable quarantine instead of direct deletion", () => {
  assert.match(storageHook, /30天可恢复隔离区/);
  assert.match(storageHook, /restoreQuarantine/);
  assert.match(storageHook, /永久删除/);
  assert.match(api, /quarantineOrphans/);
});
