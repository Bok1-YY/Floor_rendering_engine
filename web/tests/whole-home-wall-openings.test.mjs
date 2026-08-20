import assert from "node:assert/strict";
import test from "node:test";
import { wallOpeningParts } from "../src/lib/wholeHomeWallOpenings.ts";

const wall = (id, x, z0 = 0, z1 = 4) => ({
  id,
  start: { x, z: z0 },
  end: { x, z: z1 },
  height_m: 2.8,
  thickness_m: .12,
});

test("one CAD opening cuts all nearby parallel wall faces but not a deeper wall", () => {
  const walls = [wall("source", 0), wall("finish", .24), wall("deep", .96)];
  const openings = [{
    id: "window", wall_id: "source", kind: "window", offset_m: 1.1,
    width_m: 1.2, sill_height_m: .8, height_m: 1.4, review_status: "accepted",
  }];
  assert.deepEqual(
    wallOpeningParts(walls[0], openings, walls).map(({ from, to }) => [from, to]),
    [[1.1, 2.3]],
  );
  assert.equal(wallOpeningParts(walls[1], openings, walls).length, 1);
  assert.equal(wallOpeningParts(walls[2], openings, walls).length, 0);
});

test("opening propagation never cuts a perpendicular wall", () => {
  const source = wall("source", 0);
  const perpendicular = {
    id: "perpendicular", start: { x: -.1, z: 1.5 }, end: { x: .3, z: 1.5 },
    height_m: 2.8, thickness_m: .12,
  };
  const opening = [{
    id: "door", wall_id: "source", kind: "door", offset_m: 1,
    width_m: 1, sill_height_m: 0, height_m: 2.1, review_status: "accepted",
  }];
  assert.equal(wallOpeningParts(perpendicular, opening, [source, perpendicular]).length, 0);
});
