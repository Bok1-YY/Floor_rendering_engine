import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  buildReferenceCaptureGate,
  buildReferencePreflightGate,
  cadFormatReadiness,
  canMutateWholeHomeGeometry,
  materialModeGate,
  switchWholeHomeSource,
} from "../src/lib/wholeHomeCad.ts";
import {
  cadDraftBounds,
  buildCadSpaceDraftPut,
  describeCadApiError,
  mergePhysicalSpaces,
  retainCadFace,
  updateSpaceSelection,
} from "../src/lib/wholeHomeCadSpace.ts";

const runtime = (overrides = {}) => ({
  ready_for_dxf: true,
  ready_for_dwg: false,
  ezdxf_available: true,
  ezdxf_version: "1",
  shapely_available: true,
  shapely_version: "2",
  converter_available: false,
  commercial_use_authorized: false,
  converter_adapter: "oda_file_converter_v1",
  converter_configuration: { path_env_names: [], commercial_authorization_env: "DWG_OK" },
  ...overrides,
});

test("image and CAD source selections are mutually exclusive", () => {
  assert.deepEqual(switchWholeHomeSource("cad", { plan: { id: "plan" }, cad: { id: "cad" } }), {
    mode: "cad", plan: null, cad: { id: "cad" },
  });
  assert.deepEqual(switchWholeHomeSource("image", { plan: { id: "plan" }, cad: { id: "cad" } }), {
    mode: "image", plan: { id: "plan" }, cad: null,
  });
});

test("Gemini CAD review is visibly advisory-only and uses a dedicated API", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../src/app/floorplan/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /data-testid="cad-gemini-advisory-panel"/);
  assert.match(page, /永不直接改墙 \/ 门窗 \/ 尺寸/);
  assert.match(page, /geometry_mutated=false/);
  assert.match(page, /project\.model\?\.coordinate_contract_version !== 2/);
  assert.match(api, /\/cad\/ai-assist/);
  assert.match(api, /review_passes/);
});

test("DWG readiness never confuses converter detection with commercial authorization", () => {
  assert.equal(cadFormatReadiness(runtime(), "dxf").ready, true);
  assert.equal(cadFormatReadiness(runtime(), "dwg").code, "cad_converter_missing");
  const detected = runtime({ converter_available: true });
  assert.equal(cadFormatReadiness(detected, "dwg").ready, false);
  assert.equal(cadFormatReadiness(detected, "dwg").code, "cad_commercial_authorization_missing");
  const authorized = runtime({ converter_available: true, commercial_use_authorized: true, ready_for_dwg: true });
  assert.equal(cadFormatReadiness(authorized, "dwg").ready, true);
  assert.equal(cadFormatReadiness(runtime({ ready_for_dxf: false }), "dxf").code, "cad_parser_missing");
});

function referenceProject() {
  const slots = Array.from({ length: 9 }, (_, index) => {
    const slotId = `slot_${index + 1}`;
    return {
      slot_id: slotId,
      reference_image_id: `image_${index + 1}`,
      room_profile: `profile_${index + 1}`,
      focal_length_mm: { min: 24, max: 35 },
      must_show: [`subject_${index + 1}`],
      hard_constraints: [],
      reference_asset: { url: `/api/reference/slot_${index + 1}`, sha256: `hash_${index + 1}`, status: "verified" },
      reference_viewpoint: {
        scene_id: `scene_${index + 1}`,
        scene_name: `节点 ${index + 1}`,
        point_mapping: { status: "not_available", evidence: ["is_showmap=0", "map_url_empty"] },
        landing_policy: {
          mode: "cad_semantic_relative_region",
          source: "inferred_from_reference_visual_and_cad_anchors",
          anchors: [`subject_${index + 1}`],
        },
      },
    };
  });
  const rooms = slots.map((slot, index) => ({
    id: `room_${index + 1}`,
    reference_room_profile: slot.room_profile,
  }));
  const captures = slots.map((slot, index) => ({
    capture_id: `capture_${index + 1}`,
    camera_id: `camera_${index + 1}`,
    room_id: rooms[index].id,
    reference_slot_id: slot.slot_id,
    aspect_ratio: "4:3",
    status: "confirmed",
    is_primary: true,
    pool_rank: 1,
    camera: {
      room_id: rooms[index].id,
      reference_slot_id: slot.slot_id,
      focal_length_mm: 28,
      position: { x: index + 1, y: 1.45, z: index + 1 },
      target: { x: index + 2, y: 1.45, z: index + 2 },
      render_gate: {
        version: "whole-home-reference-render-gate-v3-software",
        pass: true,
        status: "pass",
      },
      reference_contract_validation: {
        version: 1,
        slot_id: slot.slot_id,
        scene_id: slot.reference_viewpoint.scene_id,
        room_id: rooms[index].id,
        landing_policy_mode: "cad_semantic_relative_region",
        yaw_source: "local_360_coarse10_refine2",
        projection_method: "backend_2d_fov_los",
        pixel_origin: "top-left",
        width: 512,
        height: 384,
        buffer_sha: `buffer_${index + 1}`,
        pixel_gate_version: "whole-home-subject-pixel-gate-v2",
        proposal_id: "proposal",
        proposal_hash: "proposal_hash",
        safe_frame_status: "pass",
        safe_frame_pass: true,
        landing_source: "inferred_from_reference_visual_and_cad_anchors",
        cad_position_pass: true,
        collision_pass: true,
        visibility_pass: true,
        must_show_subjects: [{ subject: slot.must_show[0], anchor_id: `anchor_${index + 1}` }],
        must_validate: {},
        must_show_bounds: [{ subject: slot.must_show[0], x_min: .1, x_max: .7, y_min: .1, y_max: .8 }],
      },
    },
  }));
  return {
    source_type: "cad",
    verified: true,
    reference_contract: {
      contract_id: "contract_9",
      output: { aspect_ratio: "4:3", resolution: "4K" },
      camera: {
        eye_height_m: { min: 1.35, max: 1.55 },
        vertical_deviation_deg_max: 1,
        safe_frame: { x_min: .08, x_max: .92, y_min: .08, y_max: .94 },
      },
      slots,
    },
    model: { rooms, reference_anchor_report: { status: "ready", hard_errors: [] } },
    captures,
  };
}

test("reference gate uses audited scenes with CAD-relative validated landing, never invented absolute mapping", () => {
  const project = referenceProject();
  const gate = buildReferenceCaptureGate(project);
  assert.equal(buildReferencePreflightGate(project).ready, true);
  assert.equal(gate.ready, true);
  assert.equal(gate.activeSlots, 9);
  assert.equal(gate.estimatedResults, 18);
  assert.equal(gate.captureGroups.length, 9);
  assert.equal(materialModeGate({ mode: "reference", referenceGate: gate }).ready, true);

  const noAsset = structuredClone(project);
  delete noAsset.reference_contract.slots[0].reference_asset;
  const assetGate = buildReferenceCaptureGate(noAsset);
  assert.equal(assetGate.ready, false);
  assert.equal(assetGate.code, "reference_slot_asset_missing");

  const noViewpoint = structuredClone(project);
  delete noViewpoint.reference_contract.slots[0].reference_viewpoint;
  const viewpointGate = buildReferenceCaptureGate(noViewpoint);
  assert.equal(viewpointGate.ready, false);
  assert.equal(viewpointGate.code, "reference_viewpoint_landing_missing");

  const invalidCamera = structuredClone(project);
  invalidCamera.captures[0].camera.reference_contract_validation.projection_method = "ai_guess";
  const cameraGate = buildReferenceCaptureGate(invalidCamera);
  assert.equal(cameraGate.ready, false);
  assert.equal(cameraGate.code, "reference_slot_camera_missing");
});

test("floor sample and CAD mutation rules remain fail closed", () => {
  const gate = buildReferenceCaptureGate(referenceProject());
  assert.equal(materialModeGate({ mode: "floor_sample", floorPath: "", referenceGate: gate }).code, "floor_sample_missing");
  assert.equal(materialModeGate({ mode: "floor_sample", floorPath: "floor.jpg", referenceGate: gate }).ready, true);
  assert.equal(canMutateWholeHomeGeometry("cad"), false);
  assert.equal(canMutateWholeHomeGeometry("floorplan"), true);
  assert.equal(canMutateWholeHomeGeometry("import"), true);
});

test("CAD studio and page keep mutation and reference submission gates explicit", async () => {
  const studio = await readFile(new URL("../src/components/WholeHomeStudio.tsx", import.meta.url), "utf8");
  const page = await readFile(new URL("../src/app/floorplan/page.tsx", import.meta.url), "utf8");
  assert.match(studio, /cadGeometryReadOnly/);
  assert.match(studio, /if \(cadGeometryReadOnly \|\| !selectedObject\) return/);
  assert.match(page, /reference_slot_camera_missing/);
  assert.match(page, /generic style image is never a substitute/i);
  assert.match(page, /const openHistoryProject = useCallback/);
  assert.match(page, /historySelectionVersionRef = useRef\(0\)/);
  assert.match(page, /const selectionVersion = \+\+historySelectionVersionRef\.current/);
  assert.match(page, /selectionVersion !== historySelectionVersionRef\.current/);
  assert.match(page, /onProjectSelected=\{\(value\) => \{ void openHistoryProject\(value\); \}\}/);
  assert.doesNotMatch(page, /useEffect\([\s\S]{0,500}continueWholeHomeRun/);
});

function cadSpaceDraft() {
  return {
    project_id: "cad-project",
    revision: 7,
    state_hash: "state-7",
    raw_faces: [
      { face_id: "face-a", disposition: "physical_space_candidate", manual_eligible: true, polygon: [{ x: 0, z: 0 }, { x: 3, z: 0 }, { x: 3, z: 2 }, { x: 0, z: 2 }] },
      { face_id: "face-b", disposition: "physical_space_candidate", manual_eligible: true, polygon: [{ x: 3, z: 0 }, { x: 6, z: 0 }, { x: 6, z: 2 }, { x: 3, z: 2 }] },
    ],
    physical_spaces: [
      { id: "space-a", label: "客餐区", space_type: "open_plan", face_ids: ["face-a"], polygon: [{ x: 0, z: 0 }, { x: 3, z: 0 }, { x: 3, z: 2 }, { x: 0, z: 2 }], selected: true },
      { id: "space-b", label: "交通区", space_type: "circulation", face_ids: ["face-b"], polygon: [{ x: 3, z: 0 }, { x: 6, z: 0 }, { x: 6, z: 2 }, { x: 3, z: 2 }], selected: true },
    ],
    semantic_zones: [
      { id: "zone-b", physical_space_id: "space-b", label: "走廊", zone_type: "circulation", geometry: { kind: "polygon", points: [{ x: 3, z: 0 }, { x: 6, z: 0 }, { x: 6, z: 2 }] } },
    ],
    excluded_face_ids: [],
    text_anchors: [{ anchor_id: "text-a", text: "LIVING", point: { x: 1, z: 1 } }],
  };
}

test("CAD room decisions update exclusions without mutating raw CAD evidence", () => {
  const source = cadSpaceDraft();
  const excluded = updateSpaceSelection(source, "space-a", false);
  assert.deepEqual(excluded.excluded_face_ids, ["face-a"]);
  assert.equal(excluded.physical_spaces.some((space) => space.id === "space-a"), false);
  assert.equal(excluded.semantic_zones.some((zone) => zone.physical_space_id === "space-a"), false);
  assert.deepEqual(excluded.raw_faces, source.raw_faces);
  const retained = retainCadFace(excluded, "face-a");
  assert.deepEqual(retained.excluded_face_ids, []);
  assert.deepEqual(retained.physical_spaces.at(-1).face_ids, ["face-a"]);
  assert.equal(retained.semantic_zones.at(-1).zone_type, "other");
});

test("merging spaces only combines face ownership and leaves polygon normalization to server", () => {
  const source = cadSpaceDraft();
  const merged = mergePhysicalSpaces(source, ["space-a", "space-b"]);
  assert.equal(merged.physical_spaces.length, 1);
  assert.deepEqual(merged.physical_spaces[0].face_ids, ["face-a", "face-b"]);
  assert.deepEqual(merged.physical_spaces[0].polygon, []);
  assert.equal(merged.semantic_zones[0].physical_space_id, "space-a");
  assert.deepEqual(source.physical_spaces[0].face_ids, ["face-a"]);
});

test("CAD preview bounds include faces and anchors in model-meter coordinates", () => {
  const bounds = cadDraftBounds(cadSpaceDraft());
  assert.ok(bounds.minX < 0);
  assert.ok(bounds.maxX > 6);
  assert.ok(bounds.minZ < 0);
  assert.ok(bounds.maxZ > 2);
});

test("manual CAD confirmation uses only draft APIs and never submits generation or a provider call", async () => {
  const editor = await readFile(new URL("../src/components/CadSpaceDraftEditor.tsx", import.meta.url), "utf8");
  const page = await readFile(new URL("../src/app/floorplan/page.tsx", import.meta.url), "utf8");
  const apiCalls = [...editor.matchAll(/api\.([A-Za-z0-9_]+)/g)].map((match) => match[1]).sort();
  assert.deepEqual(apiCalls, ["getWholeHomeCadSpaceDraft", "saveWholeHomeCadSpaceDraft"]);
  assert.doesNotMatch(editor, /createWholeHomeRun|commitWholeHomeManualRun|reconstructWholeHomeCadSemantics|generate/i);
  assert.match(editor, /buildCadSpaceDraftPut\(draft, newCadOperationId/);
  assert.match(editor, /split_halfplane/);
  assert.match(editor, /\["left", "right"\] as const/);
  assert.match(editor, /filter\(\(zone\) => zone\.physical_space_id !== activeSpace\.id\)/);
  assert.match(editor, /恢复为物理空间/);
  assert.match(editor, /kind: "rectangle"/);
  assert.match(editor, /translate\(0 \$\{bounds\.minZ \+ bounds\.maxZ\}\) scale\(1 -1\)/);
  assert.match(editor, /const displayZ = point \? bounds\.minZ \+ bounds\.maxZ - point\.z/);
  assert.match(editor, /enclosed_room/);
  assert.match(editor, /primary_bedroom/);
  assert.doesNotMatch(page, /onSemanticReconstruct=/);
  assert.match(page, /getWholeHomeCadReparseOperation/);
  assert.match(page, /cad-entity-role-summary/);
  assert.match(page, /cad-raw-opening-summary/);
  assert.match(page, /roleSummary\.retained_wall_entity_count/);
  assert.match(page, /openingSummary\.accepted_count/);
});

test("CAD save payload includes both CAS values and strips GET-only provenance", () => {
  const source = cadSpaceDraft();
  source.physical_spaces[0].source = "cad_local_faces_v1";
  source.semantic_zones[0].source_geometry = { kind: "rectangle" };
  const payload = buildCadSpaceDraftPut(source, "save-space-draft-1234", "tester");
  assert.equal(payload.base_revision, 7);
  assert.equal(payload.base_state_hash, "state-7");
  assert.equal(payload.operation_id, "save-space-draft-1234");
  assert.equal(payload.editor_id, "tester");
  assert.equal("source" in payload.physical_spaces[0], false);
  assert.equal("source_geometry" in payload.semantic_zones[0], false);
  assert.deepEqual(Object.keys(payload.semantic_zones[0]).sort(), ["geometry", "id", "label", "physical_space_id", "zone_type"]);
});

test("CAD CAS conflicts remain visible with the server revision", () => {
  const message = describeCadApiError(new Error(JSON.stringify({
    code: "whole_home_state_conflict",
    message: "空间草稿已被其他编辑者修改",
    current_revision: 9,
    current_state_hash: "server-state-9",
  })));
  assert.match(message, /whole_home_state_conflict/);
  assert.match(message, /空间草稿已被其他编辑者修改/);
  assert.match(message, /服务器当前版本 9/);
});
