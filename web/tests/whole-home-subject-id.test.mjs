import assert from "node:assert/strict";
import test from "node:test";

import {
  analyzeSubjectIdPixels,
  buildSubjectIdLegend,
} from "../src/lib/wholeHomeSubjectId.ts";

function buffer(width, height) {
  const pixels = new Uint8Array(width * height * 4);
  for (let index = 3; index < pixels.length; index += 4) pixels[index] = 255;
  return pixels;
}

function put(pixels, width, x, sourceY, color) {
  const offset = (sourceY * width + x) * 4;
  pixels[offset] = color[0]; pixels[offset + 1] = color[1]; pixels[offset + 2] = color[2]; pixels[offset + 3] = 255;
}

test("subject IDs are unique per object/opening and bottom-left WebGL rows become top-left bounds", () => {
  const legend = buildSubjectIdLegend([
    { subject: "bed", anchor_id: "object_bed", anchor_kind: "fixed_object", role: "bed" },
    { subject: "CAD window", anchor_id: "opening_window", anchor_kind: "opening", role: "window" },
  ]);
  assert.notDeepEqual(legend.subjects[0].color, legend.subjects[1].color);
  const pixels = buffer(10, 10);
  // readPixels sourceY=7 maps to top-left y=2.
  put(pixels, 10, 2, 7, legend.subjects[0].color);
  put(pixels, 10, 3, 7, legend.subjects[0].color);
  put(pixels, 10, 6, 3, legend.subjects[1].color);
  const result = analyzeSubjectIdPixels(
    pixels, 10, 10, legend, { x_min: .1, x_max: .9, y_min: .1, y_max: .9 }, "bottom-left",
  );
  assert.equal(result.pass, true);
  assert.equal(result.pixel_origin, "top-left");
  assert.equal(result.must_show_bounds[0].y_min, .2);
  assert.equal(result.must_show_bounds[1].anchor_id, "opening_window");
});

test("occluded zero pixels and partial safe-frame overflow both fail", () => {
  const legend = buildSubjectIdLegend([
    { subject: "toilet", anchor_id: "toilet_1", anchor_kind: "fixed_object", role: "toilet" },
    { subject: "shower", anchor_id: "shower_1", anchor_kind: "fixed_object", role: "shower_zone" },
  ]);
  const pixels = buffer(10, 10);
  put(pixels, 10, 0, 5, legend.subjects[0].color);
  const result = analyzeSubjectIdPixels(
    pixels, 10, 10, legend, { x_min: .08, x_max: .92, y_min: .08, y_max: .94 }, "bottom-left",
  );
  assert.equal(result.pass, false);
  assert.ok(result.reasons.some((row) => row.includes("safe frame")));
  assert.ok(result.reasons.some((row) => row.includes("被遮挡")));
});
