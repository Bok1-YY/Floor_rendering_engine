import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  clampPanoFov,
  clampPanoPitch,
  horizontalPanoFovToVertical,
  isEquirectangularSize,
  PANO_VIEW_DEFAULT_FOV_DEG,
} from "../src/lib/wholeHomePano.ts";

test("pano viewer clamps pitch and FOV without rejecting full yaw rotation", () => {
  assert.equal(clampPanoPitch(120), 85);
  assert.equal(clampPanoPitch(-120), -85);
  assert.equal(clampPanoPitch(42), 42);
  assert.equal(clampPanoFov(10), 45);
  assert.equal(clampPanoFov(120), 105);
  assert.equal(clampPanoFov(64), 64);
  assert.equal(PANO_VIEW_DEFAULT_FOV_DEG, 90);
  assert.ok(Math.abs(horizontalPanoFovToVertical(90, 16 / 9) - 58.716) < 0.01);
  assert.ok(Math.abs(horizontalPanoFovToVertical(90, 1752 / 626) - 39.318) < 0.01);
});

test("only complete 2:1 ERP dimensions enter the sphere viewer", () => {
  assert.equal(isEquirectangularSize(3840, 1920), true);
  assert.equal(isEquirectangularSize(4096, 2048), true);
  assert.equal(isEquirectangularSize(1920, 1080), false);
  assert.equal(isEquirectangularSize(0, 0), false);
});

test("history pano audits use the shared view-only viewer and preserve flat ERP access", async () => {
  const [recordsPage, floorplanPage, viewer] = await Promise.all([
    readFile(new URL("../src/app/records/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/app/floorplan/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/PanoViewer.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(recordsPage, /<PanoViewer erpUrl=\{panoView\.url\} mode="view"/);
  assert.match(recordsPage, /360°查看/);
  assert.match(recordsPage, /原始2:1/);
  assert.match(viewer, /ResizeObserver/);
  assert.match(viewer, /domElement\.style\.width = "100%"/);
  assert.match(viewer, /getBoundingClientRect\(\)/);
  assert.match(viewer, /requestFullscreen/);
  assert.match(viewer, /addEventListener\("wheel", onWheel, \{ passive: false \}\)/);
  assert.doesNotMatch(viewer, /onWheel=\{onWheel\}/);
  assert.match(viewer, /mode === "review"/);
  assert.match(viewer, /initialYawDeg = 0/);
  assert.match(viewer, /applyView\(initialYawDeg, 0, PANO_VIEW_DEFAULT_FOV_DEG\)/);
  assert.match(viewer, /horizontalPanoFovToVertical\(viewRef\.current\.fovDeg, camera\.aspect\)/);
  assert.match(floorplanPage, /initialYawDeg=\{Number\(capture\?\.manifest\?\.heading_deg \|\| 0\)\}/);
});
