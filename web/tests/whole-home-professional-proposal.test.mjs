import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pageUrl = new URL("../src/app/floorplan/page.tsx", import.meta.url);
const panelUrl = new URL("../src/components/WholeHomeProfessionalProposal.tsx", import.meta.url);
const studioUrl = new URL("../src/components/WholeHomeStudio.tsx", import.meta.url);
const cadUrl = new URL("../src/lib/wholeHomeCad.ts", import.meta.url);

test("raster input and locked versioned style pack form the default product path", async () => {
  const [page, cad] = await Promise.all([readFile(pageUrl, "utf8"), readFile(cadUrl, "utf8")]);
  assert.match(page, /useState<WholeHomeSourceMode>\("image"\)/);
  assert.match(page, /useState<WholeHomeMaterialMode>\("style_pack"\)/);
  assert.match(page, /data-testid="whole-home-style-pack-mode"/);
  assert.match(cad, /locked_scene_recipe_required/);
  assert.match(page, /scene_recipe_id: activeSceneRecipe\?\.status === "locked"/);
  assert.match(page, /scene_hash: activeSceneRecipe\?\.status === "locked"/);
});

test("three deterministic layouts feed the same recipe into 3D and panorama evidence", async () => {
  const [page, panel, studio] = await Promise.all([
    readFile(pageUrl, "utf8"), readFile(panelUrl, "utf8"), readFile(studioUrl, "utf8"),
  ]);
  assert.match(panel, /\(\[1, 2, 3\] as const\)/);
  assert.match(panel, /onActiveRecipeChange\?\.\(activeRecipe\)/);
  assert.match(page, /sceneInstances=\{activeSceneRecipe\?\.instances \|\| \[]\}/);
  assert.match(studio, /addSceneRecipeInstances\(scene, sceneInstances\)/);
  assert.match(studio, /anchor_kind: "scene_recipe_object"/);
  assert.match(panel, /不承诺真实 SKU、报价或施工尺寸/);
});
