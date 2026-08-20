import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  canCompleteHumanReview,
  canContinueHumanReview,
  groupWholeHomeReviewables,
  isWholeHomeGenerationLocked,
  reviewValidationMessage,
} from "../src/lib/wholeHomeHumanReview.ts";

function state(overrides = {}) {
  return {
    round_status: "awaiting_human_review",
    can_complete: false,
    pending_count: 1,
    completion_event_id: "",
    ...overrides,
  };
}

test("round completion is gated by every reviewable image", () => {
  assert.equal(canCompleteHumanReview(state()), false);
  assert.equal(canCompleteHumanReview(state({ pending_count: 0, can_complete: true })), true);
  assert.equal(canCompleteHumanReview(state({
    round_status: "review_not_required",
    pending_count: 0,
    can_complete: true,
  })), true);
  assert.equal(canCompleteHumanReview(state({
    round_status: "review_complete",
    pending_count: 0,
    can_complete: true,
  })), false);
});

test("reject requires a reason while pass and backup remain one-click", () => {
  assert.match(reviewValidationMessage("reject", []), /至少选择一个/);
  assert.equal(reviewValidationMessage("reject", ["明显幻觉"]), "");
  assert.equal(reviewValidationMessage("pass", []), "");
  assert.equal(reviewValidationMessage("backup", []), "");
});

test("awaiting rounds lock generation and only a completed event can continue", () => {
  assert.equal(isWholeHomeGenerationLocked(state()), true);
  assert.equal(isWholeHomeGenerationLocked(state({ round_status: "review_not_required" })), true);
  assert.equal(isWholeHomeGenerationLocked(state({ round_status: "review_complete" })), false);
  assert.equal(canContinueHumanReview(state({
    round_status: "review_complete",
    pending_count: 0,
    completion_event_id: "review_complete_1",
  })), true);
  assert.equal(canContinueHumanReview(state({
    round_status: "review_complete",
    pending_count: 0,
    completion_event_id: "",
  })), false);
});

test("material artifacts stay independent and are grouped by room", () => {
  const artifact = (artifact_id, room_id, review_status) => ({ artifact_id, room_id, review_status });
  const groups = groupWholeHomeReviewables([
    artifact("a1", "living", "pass"),
    artifact("a2", "living", "reject"),
    artifact("a3", "bedroom", "unreviewed"),
  ]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].artifacts.length, 2);
  assert.deepEqual(groups[0].counts, { pass: 1, backup: 0, reject: 1, unreviewed: 0 });
});

test("continue is wired only to the explicit user click path", async () => {
  const component = await readFile(new URL("../src/components/WholeHomeHumanReview.tsx", import.meta.url), "utf8");
  const calls = component.match(/api\.continueWholeHomeRun\(/g) || [];
  assert.equal(calls.length, 1);
  assert.match(component, /async function continueOptimization\(\)/);
  assert.match(component, /onClick=\{\(\) => void continueOptimization\(\)\}/);
  assert.doesNotMatch(component, /useEffect\([\s\S]{0,500}continueWholeHomeRun/);
  assert.doesNotMatch(component, /Notification\.requestPermission/);
});

