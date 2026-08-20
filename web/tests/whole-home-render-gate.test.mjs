import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";
import { prepareVisibleEdgePass } from "../src/lib/wholeHomeEdgePass.ts";
import {
  analyzeWholeHomeSemanticPixels,
  evaluateReferenceBaseRenderGate,
  evaluateWholeHomeRenderFractions,
  filterWholeHomeRenderCandidates,
  WHOLE_HOME_RENDER_GATE_THRESHOLDS,
  WHOLE_HOME_SEMANTIC_COLORS,
} from "../src/lib/wholeHomeRenderGate.ts";

test("reference slots use base frame ratios while subject-ID owns slot-specific visibility", () => {
  const generic = evaluateWholeHomeRenderFractions("living_room", {
    floor: 0.091,
    wall: 0.537,
    fridge: 0.238,
    tv: 0.027,
  });
  assert.equal(generic.pass, false);
  assert.match(generic.reasons.join(" "), /沙发/);
  const reference = evaluateReferenceBaseRenderGate(generic);
  assert.equal(reference.pass, true);
  assert.deepEqual(reference.required_groups, []);
  assert.equal(reference.reasons.length, 0);
});

test("real Round6 v2 semantic ratios reject master primary and retain six rooms plus backup", () => {
  const fixtures = [
    ["kitchen", { floor: .1407, wall: .38, kitchen_run: .2768, sink: .02 }, true],
    ["bedroom", { floor: .0888, wall: .48, bed: .1936, wardrobe: .18 }, true],
    ["foyer", { floor: .091, wall: .55, entry_storage: .1696 }, true],
    ["bathroom", { floor: .1042, wall: .56, basin: .1196, toilet: .06 }, true],
    ["living_room", { floor: .1808, wall: .4, sofa: .2754, tv: .03 }, true],
    ["balcony", { floor: .1355, wall: .6, washing_machine: .1363 }, true],
    ["bedroom", { floor: .0554, wall: .45, bed: .4422 }, false],
    ["bedroom", { floor: .087, wall: .31, wardrobe: .32, bed: .268 }, true],
  ];
  for (const [profile, fractions, expected] of fixtures) {
    assert.equal(evaluateWholeHomeRenderFractions(profile, fractions).pass, expected, profile);
  }
});

test("thresholds are inclusive and room contracts use actual colored roles", () => {
  const t = WHOLE_HOME_RENDER_GATE_THRESHOLDS;
  assert.equal(evaluateWholeHomeRenderFractions("bedroom", {
    floor: t.floor_min,
    wall: t.wall_max,
    bed: t.bedroom_bed_min,
  }).pass, true);
  assert.equal(evaluateWholeHomeRenderFractions("bathroom", {
    floor: t.floor_min,
    wall: t.wall_max,
    basin: t.bathroom_fixture_min,
    toilet: t.bathroom_fixture_min,
  }).pass, true);
  assert.equal(evaluateWholeHomeRenderFractions("bedroom", {
    floor: t.floor_min,
    wall: t.wall_max,
    bed: t.semantic_role_peak_max,
  }).pass, true);
  const rolePeak = evaluateWholeHomeRenderFractions("bedroom", {
    floor: .1,
    wall: .3,
    bed: t.semantic_role_peak_max + .0001,
  });
  assert.equal(rolePeak.pass, false);
  assert.equal(rolePeak.peak_semantic_role, "bed");
  assert.ok(rolePeak.reasons.some((reason) => reason.includes("bed") && reason.includes("40.01%")));
  assert.equal(evaluateWholeHomeRenderFractions("other", {
    floor: .1,
    wall: .3,
    other: .8,
  }).pass, true);
  const noInventedOpening = evaluateWholeHomeRenderFractions("living_room", {
    floor: .2,
    wall: .3,
    sofa: .1,
    open_connection: .4,
  });
  assert.equal(noInventedOpening.pass, false);
  assert.ok(noInventedOpening.reasons.some((reason) => reason.includes("电视")));
});

test("semantic pixel analyzer counts tolerant palette matches and leaves background unmatched", () => {
  const width = 100;
  const height = 100;
  const rgba = new Uint8Array(width * height * 4);
  const paint = (start, count, hex, delta = 0) => {
    const rgb = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16));
    for (let index = start; index < start + count; index += 1) {
      rgba[index * 4] = rgb[0] + delta;
      rgba[index * 4 + 1] = rgb[1];
      rgba[index * 4 + 2] = rgb[2];
      rgba[index * 4 + 3] = 255;
    }
  };
  paint(0, 1000, WHOLE_HOME_SEMANTIC_COLORS.floor, 2);
  paint(1000, 3000, WHOLE_HOME_SEMANTIC_COLORS.wall);
  paint(4000, 400, WHOLE_HOME_SEMANTIC_COLORS.sofa);
  paint(4400, 60, WHOLE_HOME_SEMANTIC_COLORS.tv);
  paint(4460, 5540, "#000000");
  const result = analyzeWholeHomeSemanticPixels(rgba, width, height, "living_room");
  assert.equal(result.pass, true);
  assert.equal(result.floor_fraction, .1);
  assert.equal(result.wall_fraction, .3);
  assert.equal(result.semantic_role_fractions.sofa, .04);
  assert.equal(result.semantic_role_fractions.tv, .006);
  assert.equal(result.unmatched_pixels, 5540);
});

test("20mm candidates are deferred until every base focal candidate fails render gate", () => {
  const row = (id, focal, pass, score) => ({
    candidate_id: id,
    room_id: "room",
    local_score: score,
    camera: { focal_length_mm: focal },
    metrics: { render_gate: { pass } },
  });
  let decision = filterWholeHomeRenderCandidates([
    row("base", 24, true, 70), row("wide", 20, true, 99), row("bad", 28, false, 100),
  ]);
  assert.deepEqual(decision.eligible.map((candidate) => candidate.candidate_id), ["base"]);
  assert.equal(decision.room_results.room.used_deferred_20mm, false);
  decision = filterWholeHomeRenderCandidates([
    row("base", 24, false, 70), row("wide", 20, true, 60),
  ]);
  assert.deepEqual(decision.eligible.map((candidate) => candidate.candidate_id), ["wide"]);
  assert.equal(decision.room_results.room.used_deferred_20mm, true);
});

test("blocked room audit exposes exact floor and dominant-role percentages", () => {
  const gate = evaluateWholeHomeRenderFractions("bedroom", {
    floor: .0554,
    wall: .45,
    bed: .4422,
  });
  const decision = filterWholeHomeRenderCandidates([{
    candidate_id: "secondary-real",
    room_id: "secondary",
    local_score: 92,
    camera: { focal_length_mm: 24 },
    metrics: { render_gate: gate },
  }]);
  assert.deepEqual(decision.eligible, []);
  assert.ok(decision.room_results.secondary.reasons[0].includes("地板 5.54%"));
  assert.ok(decision.room_results.secondary.reasons.some((reason) => reason.includes("bed") && reason.includes("44.22%")));
  assert.equal(decision.room_results.secondary.best_rejected_gate, gate);
});

test("visible edge pass keeps meshes visible, shares depth, and restores/disposes state", () => {
  const scene = new THREE.Scene();
  const original = new THREE.MeshBasicMaterial({ color: 0xff0000 });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), original);
  mesh.renderOrder = 7;
  scene.add(mesh);
  const edgePass = prepareVisibleEdgePass(scene);
  assert.equal(mesh.visible, true);
  assert.notEqual(mesh.material, original);
  assert.equal(mesh.renderOrder, 0);
  assert.equal(edgePass.group.children.length, 1);
  const line = edgePass.group.children[0];
  assert.equal(line.material.depthTest, true);
  assert.equal(line.material.depthWrite, false);
  assert.equal(line.material.depthFunc, THREE.LessEqualDepth);
  assert.ok(line.renderOrder > mesh.renderOrder);
  let meshMaterialDisposed = false;
  let lineGeometryDisposed = false;
  edgePass.meshes[0].replacement.dispose = () => { meshMaterialDisposed = true; };
  line.geometry.dispose = () => { lineGeometryDisposed = true; };
  edgePass.restore();
  assert.equal(mesh.material, original);
  assert.equal(mesh.renderOrder, 7);
  assert.equal(mesh.visible, true);
  assert.equal(scene.getObjectByName("whole-home-visible-edge-pass"), undefined);
  assert.equal(meshMaterialDisposed, true);
  assert.equal(lineGeometryDisposed, true);
});
