import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

test("Plan-to-3D audit cards open a case-specific interactive detail instead of acting as a scroll-only strip", async () => {
  const component = await readFile(new URL("../src/components/WholeHomeHistoryPanel.tsx", import.meta.url), "utf8");
  assert.match(component, /data-testid=\{`geometry-audit-card-/);
  assert.match(component, /onClick=\{\(\) => setSelectedAudit\(\{ file, entry \}\)\}/);
  assert.match(component, /api\.listGeometryAudits\(40\)/);
  assert.doesNotMatch(component, /Promise\.all\(files\.slice/);
  assert.match(component, /<GeometryAuditModelViewer/);
  assert.match(component, /truth_gray_model\.obj/);
  assert.match(component, /打开完整逐项核对/);
  assert.match(component, /不会替换或修改当前用户 CAD 项目/);
  assert.match(component, /status === "pending"\) return "bg-sky-100 text-sky-800"/);
  assert.match(component, /待独立真值核对/);
  assert.match(component, /status === "pending" \? "待核对"/);
});

test("geometry audit OBJ viewer uses orbit controls and the checksum-gated artifact endpoint", async () => {
  const [viewer, component] = await Promise.all([
    readFile(new URL("../src/components/GeometryAuditModelViewer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/WholeHomeHistoryPanel.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(viewer, /OBJLoader/);
  assert.match(viewer, /OrbitControls/);
  assert.match(viewer, /ResizeObserver/);
  assert.match(viewer, /fetch\(src/);
  assert.match(viewer, /object\.rotation\.x = -Math\.PI \/ 2/);
  assert.match(component, /api\.geometryAuditArtifactUrl/);
});

test("geometry audit viewer exposes a deterministic orthographic structure-only capture", async () => {
  const [viewer, studio, framing] = await Promise.all([
    readFile(new URL("../src/components/GeometryAuditModelViewer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/WholeHomeStudio.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/lib/wholeHomeOrthographicAudit.ts", import.meta.url), "utf8"),
  ]);
  assert.match(framing, /ORTHOGRAPHIC_AUDIT_PADDING = 0\.05/);
  assert.match(framing, /ORTHOGRAPHIC_AUDIT_EXPORT_SIZE = 1600/);
  assert.match(framing, /ORTHOGRAPHIC_CAMERA_CONTRACT_VERSION = 2/);
  assert.match(framing, /right-handed-y-up-x-east-z-south-v2/);
  assert.match(framing, /view_direction: \[0, -1, 0\]/);
  assert.match(framing, /cad_y: "\+screen_up"/);
  assert.match(framing, /manifest\.wall_parts/);
  assert.match(framing, /manifest\.floor_parts/);
  assert.doesNotMatch(framing, /manifest\.object_parts/);
  assert.match(viewer, /new THREE\.OrthographicCamera/);
  assert.match(viewer, /camera\.up\.set\(\.\.\.contract\.camera_up\)/);
  assert.match(viewer, /data-testid="geometry-audit-orthographic-button"/);
  assert.match(viewer, /data-testid="geometry-audit-download-png"/);
  assert.match(viewer, /data-audit-frame-state=/);
  assert.match(viewer, /preserveDrawingBuffer: true/);
  assert.match(studio, /data-testid="whole-home-orthographic-audit"/);
  assert.match(studio, /data-testid="whole-home-download-audit-png"/);
  assert.match(studio, /setAuditStructureVisibility\(scene, true\)/);
  assert.match(studio, /function auditOverhead\(\)[\s\S]*renderer\.shadowMap\.enabled = false/);
  assert.match(studio, /function activatePerspectiveView\(\)[\s\S]*renderer\.shadowMap\.enabled = true/);
  assert.match(studio, /function downloadAuditPng\(\)[\s\S]*renderer\.shadowMap\.enabled = false/);
  assert.match(studio, /auditDeterministicAppearance/);
  assert.match(studio, /material\.color\.setHex\(0x000000\)/);
  assert.match(studio, /structuralKind === "floor" \? 0xefe8d9 : 0xffffff/);
  assert.match(studio, /resolveWholeHomeAuditBounds\(model, geometryManifest\)/);
  assert.match(studio, /camera\.top = frame\.top/);
  assert.match(studio, /camera\.bottom = frame\.bottom/);
  assert.match(studio, /camera\.up\.set\(\.\.\.contract\.camera_up\)/);
  assert.doesNotMatch(studio, /camera\.top = frame\.bottom/);
  assert.doesNotMatch(studio, /camera\.bottom = frame\.top/);
  assert.doesNotMatch(studio, /auditLight\.userData\.auditOnly/);
  assert.match(studio, /model\.physical_spaces\?\.length \? model\.physical_spaces : model\.rooms/);
});

test("orthographic audit framing applies five percent per side and is deterministic", async () => {
  const source = await readFile(new URL("../src/lib/wholeHomeOrthographicAudit.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const cjsModule = { exports: {} };
  vm.runInNewContext(compiled, { exports: cjsModule.exports, module: cjsModule });
  const { createOrthographicAuditFrame, createOrthographicCameraContractV2 } = cjsModule.exports;
  const bounds = { minX: 0, maxX: 10, minY: 0, maxY: 3, minZ: 0, maxZ: 5 };
  const wide = createOrthographicAuditFrame(bounds, 2);
  assert.equal(wide.left, -5.5);
  assert.equal(wide.right, 5.5);
  assert.equal(wide.top, 2.75);
  assert.equal(wide.bottom, -2.75);
  const square = createOrthographicAuditFrame(bounds, 1);
  assert.equal(square.left, -5.5);
  assert.equal(square.right, 5.5);
  assert.equal(square.top, 5.5);
  assert.equal(square.bottom, -5.5);
  assert.equal(JSON.stringify(createOrthographicAuditFrame(bounds, 1)), JSON.stringify(square));
  const contract = createOrthographicCameraContractV2(bounds, 1);
  assert.equal(contract.schema_version, 2);
  assert.deepEqual([...contract.view_direction], [0, -1, 0]);
  assert.deepEqual([...contract.camera_up], [0, 0, -1]);
  assert.deepEqual([...contract.screen_right], [1, 0, 0]);
  assert.ok(contract.eye[1] > bounds.maxY);
  assert.ok(contract.frustum.top > contract.frustum.bottom);
});

test("records page honors the exact audit file and record deep link", async () => {
  const recordsPage = await readFile(new URL("../src/app/records/page.tsx", import.meta.url), "utf8");
  assert.match(recordsPage, /searchParams\.get\("json_path"\)/);
  assert.match(recordsPage, /searchParams\.get\("record_id"\)/);
  assert.match(recordsPage, /next\.some\(\(file\) => file\.json_path === requestedPath\)/);
  assert.match(recordsPage, /id=\{rid \? `record-\$\{rid\}` : undefined\}/);
});
