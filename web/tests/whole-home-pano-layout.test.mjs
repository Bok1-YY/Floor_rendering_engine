// 前端全景布局约定测试:face order / 3×2 atlas 单元格 / ERP 2:1 尺寸。
// 这些约定必须与后端 whole_home_pano_render.face_basis / _ATLAS_LAYOUT 一致,
// 前端改动一旦破坏布局,此处 golden 断言立即失败。
import assert from "node:assert/strict";
import test from "node:test";
import {
  PANO_ATLAS_CELL,
  PANO_FACE_ORDER,
  emptyPanoChecklistResult,
  panoAtlasSize,
  panoChecklistComplete,
  panoChecklistPassed,
  panoChecklistSequence,
  panoErpSize,
  panoSizeError,
} from "../src/lib/wholeHomePano.ts";

test("face order 与后端项目级固定顺序一致", () => {
  assert.deepEqual([...PANO_FACE_ORDER], ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]);
});

test("3×2 图集单元格 golden:row0 = +X|-X|+Y,row1 = -Y|+Z|-Z", () => {
  const expected = {
    "+X": { row: 0, col: 0 },
    "-X": { row: 0, col: 1 },
    "+Y": { row: 0, col: 2 },
    "-Y": { row: 1, col: 0 },
    "+Z": { row: 1, col: 1 },
    "-Z": { row: 1, col: 2 },
  };
  for (const face of PANO_FACE_ORDER) {
    assert.deepEqual(PANO_ATLAS_CELL[face], expected[face], `face ${face} 单元格映射漂移`);
  }
  // 六格互不重叠、完全覆盖。
  const seen = new Set(PANO_FACE_ORDER.map((face) => `${PANO_ATLAS_CELL[face].row},${PANO_ATLAS_CELL[face].col}`));
  assert.equal(seen.size, 6, "六面必须占据六个不同单元格");
});

test("atlas 尺寸 = 3×2 正方形面;ERP 严格 2:1(4N×2N)", () => {
  assert.deepEqual(panoAtlasSize(512), { width: 1536, height: 1024 });
  assert.deepEqual(panoErpSize(512), { width: 2048, height: 1024 });
  const erp = panoErpSize(512);
  assert.equal(erp.width, erp.height * 2, "ERP 必须严格 2:1");
});

test("尺寸契约校验能拦截错位图集", () => {
  assert.equal(panoSizeError(512, 1536, 1024), "");
  assert.notEqual(panoSizeError(512, 1537, 1024), "");
  assert.notEqual(panoSizeError(512, 1536, 1000), "");
});

test("人工验收检查序列(文档 §9.3):水平四向 + 每 45° 俯仰 ±60° + 接缝与极点收尾", () => {
  const steps = panoChecklistSequence();
  assert.deepEqual(steps.slice(0, 5).map((step) => [step.yawDeg, step.pitchDeg]), [
    [0, 0], [90, 0], [180, 0], [270, 0], [360, 0],
  ]);
  const pitchSteps = steps.slice(5, 5 + 16);
  assert.equal(pitchSteps.length, 16);
  for (let index = 0; index < 8; index += 1) {
    assert.equal(pitchSteps[index * 2].yawDeg, index * 45);
    assert.equal(pitchSteps[index * 2].pitchDeg, 60);
    assert.equal(pitchSteps[index * 2 + 1].yawDeg, index * 45);
    assert.equal(pitchSteps[index * 2 + 1].pitchDeg, -60);
  }
  assert.deepEqual(steps.slice(-3).map((step) => step.label), [
    "接缝方向(λ=±180°)", "天顶(+90°)", "地底(-90°)",
  ]);
});

test("六项人工验收:全部回答才有效,任一不确定按失败(文档 §9.3)", () => {
  const result = emptyPanoChecklistResult();
  assert.equal(panoChecklistComplete(result), false);
  result.wall_openings = "pass";
  result.duplicates = "pass";
  result.material_continuity = "pass";
  result.lighting_continuity = "pass";
  result.poles = "pass";
  result.cross_hotspot_same_object = "pass";
  assert.equal(panoChecklistComplete(result), true);
  assert.equal(panoChecklistPassed(result), true);
  result.poles = "uncertain";
  assert.equal(panoChecklistPassed(result), false);
});
