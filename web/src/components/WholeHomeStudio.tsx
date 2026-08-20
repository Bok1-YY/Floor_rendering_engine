"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  Box,
  Camera,
  CheckCircle2,
  Download,
  DoorOpen,
  Eye,
  Move3d,
  Rotate3d,
  Square,
  Trash2,
  View,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { prepareVisibleEdgePass } from "@/lib/wholeHomeEdgePass";
import { PANO_ATLAS_CELL, PANO_FACE_ORDER, PANO_P0_ERP_SIZE } from "@/lib/wholeHomePano";
import { analyzeSubjectIdPixels, buildSubjectIdLegend } from "@/lib/wholeHomeSubjectId";
import {
  createOrthographicCameraContractV2,
  ORTHOGRAPHIC_AUDIT_EXPORT_SIZE,
  resolveWholeHomeAuditBounds,
  type OrthographicAuditBounds,
} from "@/lib/wholeHomeOrthographicAudit";
import {
  analyzeWholeHomeSemanticPixels,
  evaluateReferenceBaseRenderGate,
  filterWholeHomeRenderCandidates,
  WHOLE_HOME_SEMANTIC_COLORS,
  type WholeHomeRenderGateProfile,
} from "@/lib/wholeHomeRenderGate";
import type {
  MetricXZ,
  MetricXYZ,
  WholeHomeAutoCameraPlan,
  WholeHomeCamera,
  WholeHomeCameraCandidate,
  WholeHomeCameraCandidateProposal,
  WholeHomeCapture,
  WholeHomeGeometryManifest,
  WholeHomeModel,
  WholeHomeObject,
  WholeHomeRoom,
  WholeHomeOpening,
  WholeHomeReferenceContract,
  WholeHomeSceneInstance,
  WholeHomeSubjectIdLegend,
  WholeHomeWall,
} from "@/lib/types";
import { wallOpeningParts } from "@/lib/wholeHomeWallOpenings";

type AspectRatio = "4:3" | "16:9" | "3:4" | "9:16";
type EditorTool = "select" | "wall" | "room";
type ViewerMode = "perspective" | "orthographic-audit";
type DragState =
  | { type: "wall" | "room"; start: MetricXZ; current: MetricXZ }
  | { type: "endpoint"; wallId: string; end: "start" | "end" }
  | null;

const ASPECTS: Record<AspectRatio, number> = { "4:3": 4 / 3, "16:9": 16 / 9, "3:4": 3 / 4, "9:16": 9 / 16 };
const inputClass = "h-8 rounded-lg border border-border bg-card px-2 text-xs outline-none focus:border-primary";
const SEMANTIC_COLORS = WHOLE_HOME_SEMANTIC_COLORS;

const ROLE_DEFAULTS: Record<string, { name: string; size: MetricXYZ }> = {
  kitchen_run: { name: "厨房操作台", size: { x: 2.4, y: .9, z: .65 } },
  sink: { name: "水槽", size: { x: .7, y: .18, z: .5 } },
  hob: { name: "灶台", size: { x: .65, y: .12, z: .52 } },
  fridge: { name: "冰箱", size: { x: .75, y: 1.85, z: .72 } },
  basin: { name: "洗手台", size: { x: .8, y: .82, z: .5 } },
  toilet: { name: "马桶", size: { x: .7, y: .75, z: .42 } },
  shower_zone: { name: "淋浴区", size: { x: .9, y: 2, z: .9 } },
  bed: { name: "床", size: { x: 2, y: .55, z: 1.6 } },
  wardrobe: { name: "衣柜", size: { x: 1.8, y: 2.2, z: .6 } },
  sofa: { name: "沙发", size: { x: 2.2, y: .85, z: .9 } },
  tv: { name: "电视", size: { x: 1.6, y: 1, z: .25 } },
  dining_table: { name: "餐桌", size: { x: 1.4, y: .76, z: .8 } },
  entry_storage: { name: "玄关柜", size: { x: 1.2, y: 2, z: .45 } },
  balcony_rail: { name: "阳台栏板", size: { x: 2, y: 1.1, z: .12 } },
  washing_machine: { name: "洗衣机", size: { x: .65, y: .85, z: .65 } },
};
const PROFILE_ROLES: Record<string, string[]> = {
  kitchen: ["kitchen_run", "sink", "hob", "fridge"], bathroom: ["basin", "toilet", "shower_zone"],
  bedroom: ["bed", "wardrobe"], living_room: ["sofa", "tv", "dining_table"],
  foyer: ["entry_storage"], balcony: ["washing_machine", "balcony_rail"], other: Object.keys(ROLE_DEFAULTS),
};

function id(prefix: string) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function length(wall: WholeHomeWall) {
  return Math.hypot(wall.end.x - wall.start.x, wall.end.z - wall.start.z);
}

function midpoint(wall: WholeHomeWall, t = 0.5): MetricXZ {
  return { x: wall.start.x + (wall.end.x - wall.start.x) * t, z: wall.start.z + (wall.end.z - wall.start.z) * t };
}

function polygonAverage(points: MetricXZ[]): MetricXZ {
  if (!points.length) return { x: 0, z: 0 };
  return { x: points.reduce((sum, point) => sum + point.x, 0) / points.length, z: points.reduce((sum, point) => sum + point.z, 0) / points.length };
}

function semanticFootprint(item: WholeHomeObject): MetricXZ[] {
  const angle = THREE.MathUtils.degToRad(item.rotation_y_deg);
  const cosine = Math.cos(angle); const sine = Math.sin(angle);
  return [
    [-item.size.x / 2, -item.size.z / 2], [item.size.x / 2, -item.size.z / 2],
    [item.size.x / 2, item.size.z / 2], [-item.size.x / 2, item.size.z / 2],
  ].map(([x, z]) => ({ x: item.position.x + x * cosine - z * sine, z: item.position.z + x * sine + z * cosine }));
}

function cloneModel(model: WholeHomeModel): WholeHomeModel {
  return structuredClone(model);
}

function roomCenter(model: WholeHomeModel): MetricXZ {
  const points = (model.physical_spaces?.length ? model.physical_spaces : model.rooms)
    .flatMap((room) => room.polygon);
  if (!points.length) return { x: model.width_m / 2, z: model.depth_m / 2 };
  return { x: points.reduce((sum, p) => sum + p.x, 0) / points.length, z: points.reduce((sum, p) => sum + p.z, 0) / points.length };
}

export interface WholeHomeStudioHandle {
  autoSelectAndCapture: () => Promise<void>;
}

function rescaleModel(model: WholeHomeModel, nextWidth: number, nextDepth: number): WholeHomeModel {
  const value = cloneModel(model);
  const sx = nextWidth / Math.max(model.width_m, 0.001);
  const sz = nextDepth / Math.max(model.depth_m, 0.001);
  const scalePoint = (point: MetricXZ) => ({ x: point.x * sx, z: point.z * sz });
  const oldLengths = new Map(model.walls.map((wall) => [wall.id, length(wall)]));
  value.width_m = nextWidth;
  value.depth_m = nextDepth;
  value.walls = value.walls.map((wall) => ({ ...wall, start: scalePoint(wall.start), end: scalePoint(wall.end), source: wall.source === "ai" ? "ai_edited" : wall.source }));
  value.global_wall_footprints = value.global_wall_footprints?.map((footprint) => ({
    ...footprint,
    points: footprint.points.map(scalePoint),
    interior_rings: footprint.interior_rings.map((ring) => ring.map(scalePoint)),
  }));
  value.rooms = value.rooms.map((room) => ({ ...room, polygon: room.polygon.map(scalePoint), area_m2: room.area_m2 * sx * sz, source: room.source === "ai" ? "ai_edited" : room.source }));
  value.physical_spaces = value.physical_spaces?.map((space) => ({
    ...space, polygon: space.polygon.map(scalePoint),
  }));
  value.semantic_zones = value.semantic_zones?.map((zone) => ({
    ...zone,
    geometry: {
      ...zone.geometry,
      points: zone.geometry.points?.map(scalePoint),
      polygon: zone.geometry.polygon?.map(scalePoint),
      start: zone.geometry.start ? scalePoint(zone.geometry.start) : undefined,
      end: zone.geometry.end ? scalePoint(zone.geometry.end) : undefined,
    },
  }));
  value.fixed_objects = value.fixed_objects.map((item) => ({
    ...item,
    position: { ...item.position, x: item.position.x * sx, z: item.position.z * sz },
    size: { ...item.size, x: item.size.x * sx, z: item.size.z * sz },
  }));
  value.cameras = value.cameras.map((camera) => ({
    ...camera,
    position: { ...camera.position, x: camera.position.x * sx, z: camera.position.z * sz },
    target: { ...camera.target, x: camera.target.x * sx, z: camera.target.z * sz },
  }));
  const newWallMap = new Map(value.walls.map((wall) => [wall.id, wall]));
  value.openings = value.openings.map((opening) => {
    const ratio = length(newWallMap.get(opening.wall_id)!) / Math.max(oldLengths.get(opening.wall_id) || 1, 0.001);
    return { ...opening, offset_m: opening.offset_m * ratio, width_m: opening.width_m * ratio, source: opening.source === "ai" ? "ai_edited" : opening.source };
  });
  value.scale = { ...value.scale, status: "calibrated", method: "manual_extent", reference_length_m: nextWidth };
  return value;
}

function disposeScene(scene: THREE.Scene) {
  scene.traverse((object) => {
    if (!(object instanceof THREE.Mesh || object instanceof THREE.LineSegments)) return;
    object.geometry.dispose();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.forEach((material) => material.dispose());
  });
}

const PRESENTATION_PALETTE: Record<string, number> = {
  rug: 0xcdbb9c,
  sofa: 0xb7a58f,
  coffee_table: 0xa8744f,
  tv_console: 0x6f6258,
  dining_table: 0xa8744f,
  dining_chair: 0x927359,
  bed: 0xd8d0c1,
  bedside: 0xa8744f,
  wardrobe: 0xb7926e,
  desk: 0xa8744f,
  chair: 0x927359,
  kitchen_run: 0xd2c7b8,
  vanity: 0xd2c7b8,
  toilet: 0xf4f1ea,
  plant: 0x708163,
};

function addSceneRecipeInstances(scene: THREE.Scene, instances: WholeHomeSceneInstance[]) {
  instances.forEach((instance) => {
    const size = instance.size_m || {
      width: instance.footprint_m.width,
      depth: instance.footprint_m.depth,
      height: Math.max(.15, instance.transform.position_m.y * 2),
    };
    if (size.width <= .02 || size.depth <= .02 || size.height <= .02) return;
    const role = instance.semantic_role || "other";
    const material = new THREE.MeshStandardMaterial({
      color: PRESENTATION_PALETTE[role] || 0xb99a78,
      roughness: role === "toilet" || role === "vanity" ? .42 : .82,
      metalness: 0,
      transparent: role === "rug",
      opacity: role === "rug" ? .72 : 1,
    });
    const height = role === "rug" ? Math.min(.025, size.height) : size.height;
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(size.width, height, size.depth),
      material,
    );
    mesh.position.set(
      instance.transform.position_m.x,
      role === "rug" ? height / 2 + .018 : instance.transform.position_m.y,
      instance.transform.position_m.z,
    );
    mesh.rotation.y = THREE.MathUtils.degToRad(instance.transform.rotation_y_deg);
    mesh.castShadow = role !== "rug";
    mesh.receiveShadow = true;
    mesh.userData.kind = "scene_recipe_object";
    mesh.userData.semanticRole = role;
    mesh.userData.semanticId = instance.instance_id;
    mesh.userData.sceneRecipeAssetId = instance.asset_id;
    scene.add(mesh);
  });
}

function setAuditStructureVisibility(scene: THREE.Scene, enabled: boolean) {
  scene.traverse((object) => {
    const structuralKind = String(object.userData.kind || "");
    const structural = structuralKind === "floor" || structuralKind === "wall";
    if (object instanceof THREE.Mesh && !structural) {
      const storedVisible = object.userData.auditStructureOnlyPreviousVisible as boolean | undefined;
      if (enabled && storedVisible === undefined) {
        object.userData.auditStructureOnlyPreviousVisible = object.visible;
        object.visible = false;
      } else if (!enabled && storedVisible !== undefined) {
        object.visible = storedVisible;
        delete object.userData.auditStructureOnlyPreviousVisible;
      }
    }
    if (object instanceof THREE.Mesh && structural) {
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach((material) => {
        if (!(material instanceof THREE.MeshStandardMaterial)) return;
        const auditState = material.userData.auditDeterministicAppearance as {
          color: number; emissive: number; emissiveIntensity: number;
        } | undefined;
        if (enabled && !auditState) {
          material.userData.auditDeterministicAppearance = {
            color: material.color.getHex(),
            emissive: material.emissive.getHex(),
            emissiveIntensity: material.emissiveIntensity,
          };
          // A correspondence mask is evidence, not a beauty render.  Make the
          // structure emissive-only so triangle winding, face normals and
          // overlapping coplanar source faces cannot create false dark wedges.
          material.color.setHex(0x000000);
          material.emissive.setHex(structuralKind === "floor" ? 0xefe8d9 : 0xffffff);
          material.emissiveIntensity = 1;
          material.needsUpdate = true;
        } else if (!enabled && auditState) {
          material.color.setHex(auditState.color);
          material.emissive.setHex(auditState.emissive);
          material.emissiveIntensity = auditState.emissiveIntensity;
          delete material.userData.auditDeterministicAppearance;
          material.needsUpdate = true;
        }
      });
    }
    if (object.userData.auditOnly === true) {
      object.visible = enabled;
      return;
    }
    const suppressed = object instanceof THREE.GridHelper
      || object.userData.kind === "object"
      || object.userData.kind === "review_wall_evidence"
      || object.userData.subjectOnly === true;
    if (!suppressed) return;
    if (enabled) {
      if (object.userData.auditPreviousVisible === undefined) object.userData.auditPreviousVisible = object.visible;
      object.visible = false;
    } else if (object.userData.auditPreviousVisible !== undefined) {
      object.visible = Boolean(object.userData.auditPreviousVisible);
      delete object.userData.auditPreviousVisible;
    }
  });
}

function applyOrthographicAuditCamera(
  camera: THREE.OrthographicCamera,
  bounds: OrthographicAuditBounds,
  aspect: number,
) {
  const contract = createOrthographicCameraContractV2(bounds, aspect);
  const frame = contract.frustum;
  camera.left = frame.left;
  camera.right = frame.right;
  camera.top = frame.top;
  camera.bottom = frame.bottom;
  camera.near = frame.near;
  camera.far = frame.far;
  camera.position.set(...contract.eye);
  camera.up.set(...contract.camera_up);
  camera.lookAt(...contract.target);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return { ...frame, centerX: contract.target[0], centerY: contract.target[1],
    centerZ: contract.target[2], cameraY: contract.eye[1] };
}

function addLockedWholeHome(scene: THREE.Scene, model: WholeHomeModel, manifest: WholeHomeGeometryManifest) {
  const vertices = manifest.vertices.flat();
  const parts = [
    ...manifest.floor_parts,
    ...manifest.wall_parts,
    ...(manifest.object_parts || []),
  ];
  parts.forEach((part) => {
    if (!part.indices.length) return;
    const role = String(part.render_role || part.semantic_kind || "other");
    const color = role === "floor" ? 0xbcb6aa
      : role === "wall" || role === "ceiling" ? 0xd8d4ca : 0xc9c3b8;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
    geometry.setIndex(part.indices);
    geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(
      geometry,
      new THREE.MeshStandardMaterial({ color, roughness: role === "floor" ? 1 : .94, metalness: 0, side: THREE.DoubleSide }),
    );
    mesh.castShadow = role !== "floor";
    mesh.receiveShadow = true;
    mesh.userData.kind = role === "floor" ? "floor"
      : role === "wall" || role === "ceiling" ? "wall" : "object";
    mesh.userData.semanticRole = role;
    mesh.userData.semanticId = String(part.entity_id || part.id);
    mesh.userData.geometryManifestPartId = part.id;
    scene.add(mesh);
  });

  model.openings.filter((opening) => opening.review_status !== "rejected" && opening.width_m > .02).forEach((opening) => {
    const wall = model.walls.find((row) => row.id === opening.wall_id
      || (!!opening.wall_assembly_id && row.wall_assembly_id === opening.wall_assembly_id));
    if (!wall) return;
    const wallLength = Math.max(length(wall), .001);
    const dx = (wall.end.x - wall.start.x) / wallLength;
    const dz = (wall.end.z - wall.start.z) / wallLength;
    const center = midpoint(wall, (opening.offset_m + opening.width_m / 2) / wallLength);
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(opening.width_m, opening.height_m, .018),
      new THREE.MeshBasicMaterial({ color: 0x000000, side: THREE.DoubleSide }),
    );
    mesh.position.set(center.x, opening.sill_height_m + opening.height_m / 2, center.z);
    mesh.rotation.y = -Math.atan2(dz, dx);
    mesh.visible = false;
    mesh.userData.kind = "subject_anchor";
    mesh.userData.subjectOnly = true;
    mesh.userData.semanticId = opening.id;
    mesh.userData.semanticRole = opening.kind;
    scene.add(mesh);
  });

  const grid = new THREE.GridHelper(Math.max(model.width_m, model.depth_m) * 1.4, 24, 0x9b9488, 0xd3cec5);
  grid.position.set(model.width_m / 2, -0.01, model.depth_m / 2);
  scene.add(grid);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x756f67, 2.2));
  const key = new THREE.DirectionalLight(0xffffff, 3.4);
  key.position.set(-model.width_m * 0.3, 10, -model.depth_m * 0.2);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  scene.add(key);
}

function addWholeHome(scene: THREE.Scene, model: WholeHomeModel,
                      geometryManifest?: WholeHomeGeometryManifest | null,
                      sceneInstances: WholeHomeSceneInstance[] = []) {
  if (geometryManifest?.manifest_hash) {
    addLockedWholeHome(scene, model, geometryManifest);
    addSceneRecipeInstances(scene, sceneInstances);
    return;
  }
  const wallMaterial = new THREE.MeshStandardMaterial({ color: 0xd8d4ca, roughness: 0.95, metalness: 0 });
  const reviewWallMaterial = new THREE.MeshStandardMaterial({
    color: 0xf59e0b, emissive: 0x4a2600, emissiveIntensity: .18,
    roughness: .82, metalness: 0, transparent: true, opacity: .68,
  });
  const floorMaterial = new THREE.MeshStandardMaterial({ color: 0xbcb6aa, roughness: 1, metalness: 0, side: THREE.DoubleSide });
  const objectMaterial = new THREE.MeshStandardMaterial({ color: 0xc9c3b8, roughness: 0.92, metalness: 0 });

  const floorSpaces = model.physical_spaces?.length ? model.physical_spaces : model.rooms;
  floorSpaces.forEach((room) => {
    if (room.polygon.length < 3) return;
    const shape = new THREE.Shape();
    room.polygon.forEach((point, index) => index ? shape.lineTo(point.x, point.z) : shape.moveTo(point.x, point.z));
    shape.closePath();
    const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), floorMaterial.clone());
    mesh.rotation.x = Math.PI / 2;
    mesh.position.y = Number(room.floor_elevation_m || 0) + 0.01;
    mesh.receiveShadow = true;
    mesh.userData.kind = "floor";
    scene.add(mesh);
  });

  const addWallPart = (wall: WholeHomeWall, from: number, to: number, bottom: number, height: number) => {
    if (to - from < 0.03 || height < 0.03) return;
    const wallLength = length(wall);
    const dx = (wall.end.x - wall.start.x) / wallLength;
    const dz = (wall.end.z - wall.start.z) / wallLength;
    const center = midpoint(wall, ((from + to) / 2) / wallLength);
    const reviewOnly = wall.review_status === "needs_review"
      || wall.boundary_kind === "unresolved_review_evidence";
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(to - from, height, wall.thickness_m),
      (reviewOnly ? reviewWallMaterial : wallMaterial).clone(),
    );
    mesh.position.set(center.x, bottom + height / 2, center.z);
    mesh.rotation.y = -Math.atan2(dz, dx);
    mesh.castShadow = !reviewOnly;
    mesh.receiveShadow = true;
    mesh.userData.kind = reviewOnly ? "review_wall_evidence" : "wall";
    mesh.userData.semanticId = wall.id;
    mesh.userData.wallAssemblyId = wall.wall_assembly_id || "";
    scene.add(mesh);
  };

  const globalWallFootprints = (model.global_wall_footprints || []).filter((footprint) => footprint.points.length >= 3);
  if (globalWallFootprints.length) {
    globalWallFootprints.forEach((footprint) => {
      const shape = new THREE.Shape();
      footprint.points.forEach((point, index) => {
        if (index === 0) shape.moveTo(point.x, -point.z);
        else shape.lineTo(point.x, -point.z);
      });
      shape.closePath();
      footprint.interior_rings.forEach((ring) => {
        if (ring.length < 3) return;
        const hole = new THREE.Path();
        ring.forEach((point, index) => {
          if (index === 0) hole.moveTo(point.x, -point.z);
          else hole.lineTo(point.x, -point.z);
        });
        hole.closePath();
        shape.holes.push(hole);
      });
      const geometry = new THREE.ExtrudeGeometry(shape, {
        depth: Math.max(.1, footprint.height_m || model.wall_height_m),
        bevelEnabled: false,
        curveSegments: 1,
      });
      geometry.rotateX(-Math.PI / 2);
      geometry.translate(0, footprint.floor_elevation_m || 0, 0);
      geometry.computeVertexNormals();
      const mesh = new THREE.Mesh(geometry, wallMaterial.clone());
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.kind = "wall";
      mesh.userData.semanticId = footprint.id;
      mesh.userData.sourceRepresentation = footprint.source_representation;
      scene.add(mesh);
    });
    model.openings.filter((opening) => opening.review_status !== "rejected").forEach((opening) => {
      const wall = model.walls.find((row) => row.id === opening.wall_id
        || (!!opening.wall_assembly_id && row.wall_assembly_id === opening.wall_assembly_id));
      if (!wall) return;
      const openingTop = Math.min(wall.height_m, opening.sill_height_m + opening.height_m);
      if (opening.sill_height_m > .02) {
        addWallPart(wall, opening.offset_m, opening.offset_m + opening.width_m, 0, opening.sill_height_m);
      }
      if (openingTop < wall.height_m - .02) {
        addWallPart(wall, opening.offset_m, opening.offset_m + opening.width_m, openingTop, wall.height_m - openingTop);
      }
    });
  } else {
    model.walls.forEach((wall) => {
      const wallLength = length(wall);
      const openings = wallOpeningParts(wall, model.openings, model.walls);
      let cursor = 0;
      openings.forEach((opening) => {
        addWallPart(wall, cursor, opening.from, 0, wall.height_m);
        const top = Math.min(wall.height_m, opening.sill_height_m + opening.height_m);
        if (opening.sill_height_m > 0.02) addWallPart(wall, opening.from, opening.to, 0, opening.sill_height_m);
        if (top < wall.height_m - 0.02) addWallPart(wall, opening.from, opening.to, top, wall.height_m - top);
        cursor = Math.max(cursor, opening.to);
      });
      addWallPart(wall, cursor, wallLength, 0, wall.height_m);
    });
  }

  model.openings.filter((opening) => opening.review_status !== "rejected" && opening.width_m > .02).forEach((opening) => {
    const wall = model.walls.find((row) => row.id === opening.wall_id);
    if (!wall) return;
    const wallLength = Math.max(length(wall), .001);
    const dx = (wall.end.x - wall.start.x) / wallLength;
    const dz = (wall.end.z - wall.start.z) / wallLength;
    const center = midpoint(wall, (opening.offset_m + opening.width_m / 2) / wallLength);
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(opening.width_m, opening.height_m, .025),
      new THREE.MeshBasicMaterial({ color: 0x000000, side: THREE.DoubleSide }),
    );
    mesh.position.set(center.x, opening.sill_height_m + opening.height_m / 2, center.z);
    mesh.rotation.y = -Math.atan2(dz, dx);
    mesh.visible = false;
    mesh.userData.kind = "subject_anchor";
    mesh.userData.subjectOnly = true;
    mesh.userData.semanticId = opening.id;
    mesh.userData.semanticRole = opening.kind;
    scene.add(mesh);
  });

  model.fixed_objects.filter((item) => item.review_status !== "rejected").forEach((item) => {
    if (item.size.x <= .02 || item.size.y <= .02 || item.size.z <= .02) return;
    const group = new THREE.Group();
    group.position.set(item.position.x, item.position.y, item.position.z);
    group.rotation.y = THREE.MathUtils.degToRad(item.rotation_y_deg);
    const role = item.semantic_role || item.kind || "other";
    const addBox = (size: MetricXYZ, offset: MetricXYZ, material = objectMaterial.clone()) => {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), material);
      mesh.position.set(offset.x, offset.y + size.y / 2, offset.z);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.kind = "object";
      mesh.userData.semanticRole = role;
      mesh.userData.semanticId = item.id;
      group.add(mesh);
      return mesh;
    };
    const sx = item.size.x; const sy = item.size.y; const sz = item.size.z;
    if (role === "bed") {
      addBox({ x: sx, y: Math.min(.28, sy * .5), z: sz }, { x: 0, y: 0, z: 0 });
      addBox({ x: sx * .94, y: Math.max(.18, sy * .42), z: sz * .9 }, { x: 0, y: Math.min(.24, sy * .42), z: 0 });
      addBox({ x: sx * .34, y: .12, z: sz * .2 }, { x: -sx * .22, y: sy * .72, z: -sz * .3 });
      addBox({ x: sx * .34, y: .12, z: sz * .2 }, { x: sx * .22, y: sy * .72, z: -sz * .3 });
    } else if (role === "sofa") {
      addBox({ x: sx, y: sy * .42, z: sz }, { x: 0, y: 0, z: 0 });
      addBox({ x: sx, y: sy * .58, z: sz * .22 }, { x: 0, y: sy * .35, z: -sz * .39 });
      addBox({ x: sx * .12, y: sy * .55, z: sz }, { x: -sx * .44, y: sy * .2, z: 0 });
      addBox({ x: sx * .12, y: sy * .55, z: sz }, { x: sx * .44, y: sy * .2, z: 0 });
    } else if (role === "toilet") {
      addBox({ x: sx * .78, y: sy * .42, z: sz }, { x: 0, y: 0, z: sz * .08 });
      addBox({ x: sx, y: sy * .5, z: sz * .38 }, { x: 0, y: sy * .34, z: -sz * .28 });
    } else if (role === "basin" || role === "sink") {
      addBox({ x: sx, y: Math.max(.45, sy * .72), z: sz }, { x: 0, y: 0, z: 0 });
      addBox({ x: sx * .9, y: .1, z: sz * .82 }, { x: 0, y: Math.max(.45, sy * .72), z: 0 });
    } else if (role === "kitchen_run") {
      addBox({ x: sx, y: Math.max(.65, sy - .08), z: sz }, { x: 0, y: 0, z: 0 });
      addBox({ x: sx * 1.02, y: .08, z: sz * 1.04 }, { x: 0, y: Math.max(.65, sy - .08), z: 0 });
    } else if (role === "tv") {
      addBox({ x: sx, y: sy, z: Math.min(.12, sz) }, { x: 0, y: .35, z: 0 });
      addBox({ x: sx * .12, y: .35, z: sz }, { x: 0, y: 0, z: 0 });
    } else if (role === "shower_zone") {
      addBox({ x: sx, y: .05, z: sz }, { x: 0, y: 0, z: 0 });
      addBox({ x: .05, y: sy, z: sz }, { x: -sx / 2, y: 0, z: 0 });
      addBox({ x: sx, y: sy, z: .05 }, { x: 0, y: 0, z: -sz / 2 });
    } else if (role === "balcony_rail") {
      addBox({ x: sx, y: .1, z: sz }, { x: 0, y: 0, z: 0 });
      addBox({ x: sx, y: .1, z: sz }, { x: 0, y: sy - .1, z: 0 });
      [-.45, 0, .45].forEach((ratio) => addBox({ x: .06, y: sy, z: sz }, { x: sx * ratio, y: 0, z: 0 }));
    } else if (role === "washing_machine") {
      addBox({ x: sx, y: sy, z: sz }, { x: 0, y: 0, z: 0 });
      addBox({ x: sx * .82, y: sy * .13, z: .04 }, { x: 0, y: sy * .8, z: -sz / 2 - .02 });
      const drum = new THREE.Mesh(
        new THREE.CylinderGeometry(Math.min(sx, sy) * .27, Math.min(sx, sy) * .27, .055, 28),
        objectMaterial.clone(),
      );
      drum.rotation.x = Math.PI / 2;
      drum.position.set(0, sy * .52, -sz / 2 - .035);
      drum.castShadow = true;
      drum.userData.kind = "object";
      drum.userData.semanticRole = role;
      drum.userData.semanticId = item.id;
      group.add(drum);
    } else {
      addBox({ x: sx, y: sy, z: sz }, { x: 0, y: 0, z: 0 });
    }
    scene.add(group);
  });

  addSceneRecipeInstances(scene, sceneInstances);

  const grid = new THREE.GridHelper(Math.max(model.width_m, model.depth_m) * 1.4, 24, 0x9b9488, 0xd3cec5);
  grid.position.set(model.width_m / 2, -0.01, model.depth_m / 2);
  scene.add(grid);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x756f67, 2.2));
  const key = new THREE.DirectionalLight(0xffffff, 3.4);
  key.position.set(-model.width_m * 0.3, 10, -model.depth_m * 0.2);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  scene.add(key);
}

export const WholeHomeStudio = forwardRef<WholeHomeStudioHandle, {
  model: WholeHomeModel;
  floorplanUrl: string;
  aspectRatio: AspectRatio;
  verified: boolean;
  geometryManifest?: WholeHomeGeometryManifest | null;
  sceneInstances?: WholeHomeSceneInstance[];
  busy: boolean;
  cadGeometryReadOnly?: boolean;
  manualSafe?: boolean;
  onChange: (model: WholeHomeModel, operation: string) => void;
  referenceMode?: boolean;
  referenceContract?: WholeHomeReferenceContract;
  completedReferenceSlotIds?: string[];
  onSaveCapture: (camera: WholeHomeCamera, buffers: { rgb: string; depth: string; normal: string; edge: string; semantic: string; subjectId?: string; subjectIdLegend?: WholeHomeSubjectIdLegend; proposalId?: string; proposalHash?: string }, plan?: WholeHomeAutoCameraPlan) => Promise<WholeHomeCapture>;
  onSavePanoCapture?: (camera: WholeHomeCamera, payload: {
    pano_id: string;
    camera_center_m: MetricXYZ;
    cube_face_size: number;
    erp_width: number;
    erp_height: number;
    near_m: number;
    far_m: number;
    heading_deg: number;
    pitch_deg: number;
    roll_deg: number;
    atlases: { rgb: string; depth: string; normal: string; edge: string; semantic: string; subject_id: string };
    subject_id_legend: WholeHomeSubjectIdLegend;
    render_contract: {
      materials: Record<string, unknown>;
      lighting: Record<string, unknown>;
    };
  }) => Promise<unknown>;
  onGenerateCameraCandidates: () => Promise<WholeHomeCameraCandidateProposal>;
  onRankAutoCameras: (candidates: WholeHomeCameraCandidate[], roomPools: WholeHomeCameraCandidateProposal["room_pools"]) => Promise<WholeHomeAutoCameraPlan>;
  onAutoCaptureComplete: (captures: WholeHomeCapture[], plan: WholeHomeAutoCameraPlan) => Promise<void>;
}>(function WholeHomeStudio({
  model,
  floorplanUrl,
  aspectRatio,
  verified,
  geometryManifest,
  sceneInstances = [],
  busy,
  cadGeometryReadOnly = false,
  manualSafe = false,
  referenceMode = false,
  referenceContract,
  completedReferenceSlotIds = [],
  onChange,
  onSaveCapture,
  onSavePanoCapture,
  onGenerateCameraCandidates,
  onRankAutoCameras,
  onAutoCaptureComplete,
}, ref) {
  const planRef = useRef<SVGSVGElement>(null);
  const mountRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const auditCameraRef = useRef<THREE.OrthographicCamera | null>(null);
  const auditBoundsRef = useRef<OrthographicAuditBounds | null>(null);
  const controlsRef = useRef<OrbitControls<THREE.Camera> | null>(null);
  const poseRef = useRef<{ position: THREE.Vector3; target: THREE.Vector3 } | null>(null);
  const viewerModeRef = useRef<ViewerMode>("perspective");
  const [tool, setTool] = useState<EditorTool>("select");
  const [selectedWallId, setSelectedWallId] = useState("");
  const [selectedRoomId, setSelectedRoomId] = useState(model.rooms[0]?.id || "");
  const [selectedObjectId, setSelectedObjectId] = useState("");
  const [drag, setDrag] = useState<DragState>(null);
  const [focal, setFocal] = useState(24);
  const [cameraName, setCameraName] = useState(`机位 ${model.cameras.length + 1}`);
  const [viewerReady, setViewerReady] = useState(false);
  const [viewerMode, setViewerMode] = useState<ViewerMode>("perspective");
  const [autoStage, setAutoStage] = useState("");
  const [lastAutoPlan, setLastAutoPlan] = useState<WholeHomeAutoCameraPlan | null>(null);
  const [lastCandidateProposal, setLastCandidateProposal] = useState<WholeHomeCameraCandidateProposal | null>(null);
  const [autoRunning, setAutoRunning] = useState(false);
  const autoRunningRef = useRef(false);

  const selectedWall = model.walls.find((wall) => wall.id === selectedWallId);
  const selectedRoom = model.rooms.find((room) => room.id === selectedRoomId) || model.rooms[0];
  const selectedObject = model.fixed_objects.find((item) => item.id === selectedObjectId);
  const pendingOpenings = model.openings.filter((opening) => opening.review_status === "pending").length;
  const confirmedWallCount = model.walls.filter((wall) => wall.review_status !== "needs_review"
    && wall.boundary_kind !== "unresolved_review_evidence").length;
  const reviewWallCount = model.walls.length - confirmedWallCount;
  const globalWallShellCount = model.global_wall_footprints?.length || 0;
  const globalWallCoverage = model.global_wall_topology?.source_coverage_ratio;
  const manualReviewOpenings = model.openings.filter((opening) =>
    opening.review_status === "pending" && opening.opening_topology_review?.status === "manual_review_required");
  const autoAcceptablePendingOpenings = pendingOpenings - manualReviewOpenings.length;

  const planPoint = useCallback((event: React.PointerEvent<SVGSVGElement>): MetricXZ => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(model.width_m, (event.clientX - rect.left) / rect.width * model.width_m)),
      z: Math.max(0, Math.min(model.depth_m, (event.clientY - rect.top) / rect.height * model.depth_m)),
    };
  }, [model.depth_m, model.width_m]);

  function snap(point: MetricXZ): MetricXZ {
    const step = 0.05;
    return { x: Math.round(point.x / step) * step, z: Math.round(point.z / step) * step };
  }

  function planDown(event: React.PointerEvent<SVGSVGElement>) {
    if (cadGeometryReadOnly || event.button !== 0 || tool === "select") return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = snap(planPoint(event));
    setDrag({ type: tool, start: point, current: point });
  }

  function planMove(event: React.PointerEvent<SVGSVGElement>) {
    if (cadGeometryReadOnly || !drag) return;
    const point = snap(planPoint(event));
    if (drag.type === "endpoint") {
      const value = cloneModel(model);
      const wall = value.walls.find((item) => item.id === drag.wallId);
      if (wall) wall[drag.end] = point;
      onChange(value, "move_wall_endpoint");
    } else {
      setDrag({ ...drag, current: point });
    }
  }

  function planUp(event: React.PointerEvent<SVGSVGElement>) {
    if (cadGeometryReadOnly) {
      setDrag(null);
      return;
    }
    if (!drag) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (drag.type === "wall" && Math.hypot(drag.current.x - drag.start.x, drag.current.z - drag.start.z) > 0.2) {
      const value = cloneModel(model);
      const wallId = id("wall");
      value.walls.push({
        id: wallId, start: drag.start, end: drag.current, height_m: value.wall_height_m,
        thickness_m: value.wall_thickness_m, kind: "interior", source: "human", confidence: 1,
      });
      setSelectedWallId(wallId);
      onChange(value, "draw_wall");
    }
    if (drag.type === "room" && Math.abs(drag.current.x - drag.start.x) > 0.3 && Math.abs(drag.current.z - drag.start.z) > 0.3) {
      const value = cloneModel(model);
      const left = Math.min(drag.start.x, drag.current.x);
      const right = Math.max(drag.start.x, drag.current.x);
      const top = Math.min(drag.start.z, drag.current.z);
      const bottom = Math.max(drag.start.z, drag.current.z);
      value.rooms.push({
        id: id("room"), label: `新房间 ${value.rooms.length + 1}`, room_type: "其他",
        polygon: [{ x: left, z: top }, { x: right, z: top }, { x: right, z: bottom }, { x: left, z: bottom }],
        area_m2: (right - left) * (bottom - top), floor_elevation_m: 0,
        ceiling_height_m: value.wall_height_m, selected: true, source: "human", confidence: 1,
        semantic_profile: "other", semantic_status: "needs_review",
      });
      onChange(value, "draw_room");
    }
    setDrag(null);
  }

  function deleteSelectedWall() {
    if (cadGeometryReadOnly || !selectedWallId) return;
    const value = cloneModel(model);
    value.walls = value.walls.filter((wall) => wall.id !== selectedWallId);
    value.openings = value.openings.filter((opening) => opening.wall_id !== selectedWallId);
    setSelectedWallId("");
    onChange(value, "delete_wall");
  }

  function addOpening(kind: WholeHomeOpening["kind"]) {
    if (cadGeometryReadOnly || !selectedWall) return;
    const value = cloneModel(model);
    const wallLength = length(selectedWall);
    const width = Math.min(kind === "window" ? 1.5 : 0.9, Math.max(0.4, wallLength - 0.2));
    value.openings.push({
      id: id("opening"), wall_id: selectedWall.id, kind, offset_m: Math.max(0, (wallLength - width) / 2),
      width_m: width, height_m: kind === "window" ? 1.2 : 2.1, sill_height_m: kind === "window" ? 0.9 : 0,
      source: "human", confidence: 1, review_status: "accepted",
    });
    onChange(value, `add_${kind}`);
  }

  function patchOpening(openingId: string, patch: Partial<WholeHomeOpening>) {
    if (cadGeometryReadOnly) return;
    const value = cloneModel(model);
    value.openings = value.openings.map((opening) => opening.id === openingId ? { ...opening, ...patch, source: opening.source === "ai" ? "ai_edited" : opening.source } : opening);
    onChange(value, "edit_opening");
  }

  function acceptAllOpenings() {
    if (cadGeometryReadOnly) return;
    const value = cloneModel(model);
    value.openings = value.openings.map((opening) =>
      opening.review_status === "pending" && opening.opening_topology_review?.status !== "manual_review_required"
        ? { ...opening, review_status: "accepted", source: opening.source === "ai" ? "ai_edited" : opening.source }
        : opening);
    onChange(value, "accept_all_openings");
  }

  function addSemanticObject(role: string) {
    if (cadGeometryReadOnly || !selectedRoom || !ROLE_DEFAULTS[role]) return;
    const value = cloneModel(model);
    const defaults = ROLE_DEFAULTS[role];
    const center = polygonAverage(selectedRoom.polygon);
    const objectId = id("semantic");
    value.fixed_objects.push({
      id: objectId, name: defaults.name, kind: role, semantic_role: role,
      position: { x: center.x, y: 0, z: center.z }, size: { ...defaults.size }, rotation_y_deg: 0,
      room_id: selectedRoom.id, source: "human", confidence: 1,
      purpose: "layout_proxy", observed: false, review_status: "accepted",
      blocks_camera: role !== "shower_zone", required_for_camera: true, clearance_m: .25,
    });
    setSelectedObjectId(objectId);
    onChange(value, "add_semantic_object");
  }

  function patchSemanticObject(patch: Partial<WholeHomeObject>) {
    if (cadGeometryReadOnly || !selectedObject) return;
    const value = cloneModel(model);
    value.fixed_objects = value.fixed_objects.map((item) => item.id === selectedObject.id ? {
      ...item, ...patch, source: item.source === "ai" ? "ai_edited" : item.source,
      review_status: "accepted",
    } : item);
    onChange(value, "edit_semantic_object");
  }

  function deleteSemanticObject() {
    if (cadGeometryReadOnly || !selectedObject) return;
    const value = cloneModel(model);
    value.fixed_objects = value.fixed_objects.filter((item) => item.id !== selectedObject.id);
    setSelectedObjectId("");
    onChange(value, "delete_semantic_object");
  }

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    setViewerReady(false);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xe9e6df);
    addWholeHome(scene, model, geometryManifest, sceneInstances);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(1);
    const aspect = ASPECTS[aspectRatio];
    const width = aspect >= 1 ? 1280 : Math.round(1280 * aspect);
    const height = aspect >= 1 ? Math.round(1280 / aspect) : 1280;
    renderer.setSize(width, height, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "auto";
    renderer.domElement.setAttribute("data-testid", "whole-home-3d-canvas");
    renderer.domElement.setAttribute("aria-label", "整屋 3D 灰模");
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    mount.replaceChildren(renderer.domElement);

    const fov = THREE.MathUtils.radToDeg(2 * Math.atan(24 / (2 * focal)));
    const camera = new THREE.PerspectiveCamera(fov, aspect, 0.05, Math.max(150, model.width_m + model.depth_m));
    const auditCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 10_000);
    const auditBounds = resolveWholeHomeAuditBounds(model, geometryManifest);
    applyOrthographicAuditCamera(auditCamera, auditBounds, aspect);
    const center = roomCenter(model);
    if (poseRef.current) {
      camera.position.copy(poseRef.current.position);
    } else {
      camera.position.set(center.x + model.width_m * 0.65, Math.max(7, model.width_m * 0.65), center.z + model.depth_m * 0.65);
    }
    const controls = new OrbitControls<THREE.Camera>(camera, renderer.domElement);
    controls.target.copy(poseRef.current?.target || new THREE.Vector3(center.x, 0.8, center.z));
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.minDistance = 0.2;
    controls.maxDistance = Math.max(80, model.width_m * 5);
    controls.addEventListener("change", () => {
      if (viewerModeRef.current !== "perspective") return;
      poseRef.current = { position: camera.position.clone(), target: controls.target.clone() };
    });
    if (viewerModeRef.current === "orthographic-audit") {
      // Canonical correspondence evidence must not contain view-dependent
      // wall shadows: those diagonal wedges look like geometry in a top-down
      // mask and make otherwise identical CAD/model footprints disagree.
      renderer.shadowMap.enabled = false;
      setAuditStructureVisibility(scene, true);
      controls.object = auditCamera;
      controls.target.set(
        (auditBounds.minX + auditBounds.maxX) / 2,
        (auditBounds.minY + auditBounds.maxY) / 2,
        (auditBounds.minZ + auditBounds.maxZ) / 2,
      );
      controls.enabled = false;
    }

    let frame = 0;
    let disposed = false;
    const animate = () => {
      if (disposed) return;
      controls.update();
      renderer.render(scene, viewerModeRef.current === "orthographic-audit" ? auditCamera : camera);
      frame = requestAnimationFrame(animate);
    };
    animate();
    rendererRef.current = renderer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    auditCameraRef.current = auditCamera;
    auditBoundsRef.current = auditBounds;
    controlsRef.current = controls;
    setViewerReady(true);
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      poseRef.current = { position: camera.position.clone(), target: controls.target.clone() };
      controls.dispose();
      renderer.dispose();
      disposeScene(scene);
      mount.replaceChildren();
      if (rendererRef.current === renderer) rendererRef.current = null;
      if (auditCameraRef.current === auditCamera) auditCameraRef.current = null;
      if (auditBoundsRef.current === auditBounds) auditBoundsRef.current = null;
    };
  }, [aspectRatio, focal, geometryManifest, model, sceneInstances]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (viewerModeRef.current !== "perspective"
        || !cameraRef.current
        || !controlsRef.current
        || !["w", "a", "s", "d", "q", "e"].includes(event.key.toLowerCase())) return;
      const camera = cameraRef.current;
      const controls = controlsRef.current;
      const forward = controls.target.clone().sub(camera.position);
      forward.y = 0;
      if (forward.lengthSq() < 0.0001) forward.set(0, 0, -1);
      forward.normalize();
      const right = new THREE.Vector3().crossVectors(forward, camera.up).normalize();
      const delta = new THREE.Vector3();
      const key = event.key.toLowerCase();
      if (key === "w") delta.add(forward);
      if (key === "s") delta.sub(forward);
      if (key === "d") delta.add(right);
      if (key === "a") delta.sub(right);
      if (key === "q") delta.y -= 1;
      if (key === "e") delta.y += 1;
      delta.multiplyScalar(event.shiftKey ? 0.5 : 0.18);
      camera.position.add(delta);
      controls.target.add(delta);
      poseRef.current = { position: camera.position.clone(), target: controls.target.clone() };
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function activatePerspectiveView() {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    const scene = sceneRef.current;
    const renderer = rendererRef.current;
    if (!camera || !controls || !scene) return false;
    if (renderer) {
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.needsUpdate = true;
    }
    setAuditStructureVisibility(scene, false);
    controls.object = camera;
    controls.enabled = true;
    controls.enableRotate = true;
    viewerModeRef.current = "perspective";
    setViewerMode("perspective");
    return true;
  }

  function overhead() {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || !activatePerspectiveView()) return;
    const center = roomCenter(model);
    camera.position.set(center.x, Math.max(model.width_m, model.depth_m) * 1.2, center.z + 0.001);
    controls.target.set(center.x, 0, center.z);
    controls.update();
    poseRef.current = { position: camera.position.clone(), target: controls.target.clone() };
  }

  function auditOverhead() {
    const camera = auditCameraRef.current;
    const controls = controlsRef.current;
    const scene = sceneRef.current;
    const renderer = rendererRef.current;
    const bounds = auditBoundsRef.current;
    if (!camera || !controls || !scene || !renderer || !bounds) return;
    renderer.shadowMap.enabled = false;
    setAuditStructureVisibility(scene, true);
    const width = Math.max(1, renderer.domElement.width);
    const height = Math.max(1, renderer.domElement.height);
    const frame = applyOrthographicAuditCamera(camera, bounds, width / height);
    controls.object = camera;
    controls.target.set(frame.centerX, frame.centerY, frame.centerZ);
    controls.enabled = false;
    controls.update();
    viewerModeRef.current = "orthographic-audit";
    setViewerMode("orthographic-audit");
    renderer.render(scene, camera);
  }

  function downloadAuditPng() {
    const camera = auditCameraRef.current;
    const scene = sceneRef.current;
    const renderer = rendererRef.current;
    const bounds = auditBoundsRef.current;
    if (!camera || !scene || !renderer || !bounds) return;
    const previousSize = renderer.getSize(new THREE.Vector2());
    const previousPixelRatio = renderer.getPixelRatio();
    const previousShadowMapEnabled = renderer.shadowMap.enabled;
    setAuditStructureVisibility(scene, true);
    renderer.shadowMap.enabled = false;
    renderer.setPixelRatio(1);
    renderer.setSize(ORTHOGRAPHIC_AUDIT_EXPORT_SIZE, ORTHOGRAPHIC_AUDIT_EXPORT_SIZE, false);
    applyOrthographicAuditCamera(camera, bounds, 1);
    renderer.render(scene, camera);
    const link = document.createElement("a");
    link.href = renderer.domElement.toDataURL("image/png");
    const manifestTag = geometryManifest?.manifest_hash?.slice(0, 12) || "editable-model";
    link.download = `whole-home-${manifestTag}-orthographic-1600.png`;
    link.click();
    renderer.setPixelRatio(previousPixelRatio);
    renderer.setSize(previousSize.x, previousSize.y, false);
    if (viewerModeRef.current === "orthographic-audit") {
      renderer.shadowMap.enabled = false;
      applyOrthographicAuditCamera(camera, bounds, Math.max(1, previousSize.x) / Math.max(1, previousSize.y));
      renderer.render(scene, camera);
    } else {
      renderer.shadowMap.enabled = previousShadowMapEnabled;
      renderer.shadowMap.needsUpdate = previousShadowMapEnabled;
      setAuditStructureVisibility(scene, false);
      if (cameraRef.current) renderer.render(scene, cameraRef.current);
    }
  }

  function enterHome() {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || !activatePerspectiveView()) return;
    const room = [...model.rooms]
      .filter((item) => item.selected)
      .sort((left, right) => right.area_m2 - left.area_m2)[0] || model.rooms[0];
    const points = room?.polygon || [];
    const fallback = roomCenter(model);
    const minX = points.length ? Math.min(...points.map((point) => point.x)) : fallback.x - 1;
    const maxX = points.length ? Math.max(...points.map((point) => point.x)) : fallback.x + 1;
    const minZ = points.length ? Math.min(...points.map((point) => point.z)) : fallback.z - 1;
    const maxZ = points.length ? Math.max(...points.map((point) => point.z)) : fallback.z + 1;
    // Start safely inside the largest room and look across its diagonal. This
    // avoids the old failure where the default camera spawned inside a wall.
    camera.position.set(minX + (maxX - minX) * 0.24, 1.55, minZ + (maxZ - minZ) * 0.28);
    controls.target.set(minX + (maxX - minX) * 0.78, 1.18, minZ + (maxZ - minZ) * 0.74);
    controls.update();
    poseRef.current = { position: camera.position.clone(), target: controls.target.clone() };
  }

  function createRenderCamera(value: WholeHomeCamera, width: number, height: number) {
    const fov = THREE.MathUtils.radToDeg(2 * Math.atan(24 / (2 * value.focal_length_mm)));
    const camera = new THREE.PerspectiveCamera(fov, width / height, 0.05, Math.max(150, model.width_m + model.depth_m));
    camera.position.set(value.position.x, value.position.y, value.position.z);
    camera.lookAt(value.target.x, value.target.y, value.target.z);
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld(true);
    return camera;
  }

  // ── 定点球面全景:同光心六面 capture(文档 §7.3)──────────────
  // face order 与 basis 约定和后端 whole_home_pano_render.face_basis 完全一致;
  // 逐面用 90° FOV 透视相机渲染,与既有 renderBuffer 通道管线共用同一套
  // rgb/depth/normal/edge/semantic/subject-ID 逻辑(edge pass 无法套用 CubeCamera 的
  // 内部六面循环,因此刻意不用 CubeCamera,保证六通道全部走已审计管线)。
  const PANO_FACE_BASES: Record<(typeof PANO_FACE_ORDER)[number], { forward: THREE.Vector3; right: THREE.Vector3; up: THREE.Vector3 }> = {
    "+X": { forward: new THREE.Vector3(1, 0, 0), right: new THREE.Vector3(0, 0, 1), up: new THREE.Vector3(0, 1, 0) },
    "-X": { forward: new THREE.Vector3(-1, 0, 0), right: new THREE.Vector3(0, 0, -1), up: new THREE.Vector3(0, 1, 0) },
    "+Y": { forward: new THREE.Vector3(0, 1, 0), right: new THREE.Vector3(1, 0, 0), up: new THREE.Vector3(0, 0, 1) },
    "-Y": { forward: new THREE.Vector3(0, -1, 0), right: new THREE.Vector3(-1, 0, 0), up: new THREE.Vector3(0, 0, 1) },
    "+Z": { forward: new THREE.Vector3(0, 0, 1), right: new THREE.Vector3(-1, 0, 0), up: new THREE.Vector3(0, 1, 0) },
    "-Z": { forward: new THREE.Vector3(0, 0, -1), right: new THREE.Vector3(1, 0, 0), up: new THREE.Vector3(0, 1, 0) },
  };

  function createPanoFaceCamera(center: MetricXYZ, face: (typeof PANO_FACE_ORDER)[number], nearM: number, farM: number) {
    const basis = PANO_FACE_BASES[face];
    const camera = new THREE.PerspectiveCamera(90, 1, nearM, farM);
    camera.position.set(center.x, center.y, center.z);
    // 相机矩阵列 = right / up / -forward(three 相机看向 -Z)。
    const matrix = new THREE.Matrix4().makeBasis(basis.right, basis.up, basis.forward.clone().negate());
    camera.quaternion.setFromRotationMatrix(matrix);
    camera.updateMatrixWorld(true);
    return camera;
  }

  function loadAtlasImage(dataUrl: string): Promise<HTMLImageElement> {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("全景图集解码失败"));
      image.src = dataUrl;
    });
  }

  function renderPanoSubjectIdBuffer(
    renderCamera: THREE.PerspectiveCamera,
    legend: WholeHomeSubjectIdLegend,
  ): string {
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    if (!renderer || !scene) throw new Error("3D 渲染器尚未就绪");
    const previousOverride = scene.overrideMaterial;
    const previousBackground = scene.background;
    const previousAutoClear = renderer.autoClear;
    const previousClearColor = renderer.getClearColor(new THREE.Color()).clone();
    const previousClearAlpha = renderer.getClearAlpha();
    const states: Array<{ mesh: THREE.Mesh; material: THREE.Material | THREE.Material[]; visible: boolean }> = [];
    const palette = new Map<string, THREE.MeshBasicMaterial>();
    const colorById = new Map(legend.subjects.map((entry) => [entry.anchor_id, entry.color]));
    const black = new THREE.MeshBasicMaterial({ color: 0x000000, side: THREE.DoubleSide });
    try {
      scene.traverse((object) => {
        if (!(object instanceof THREE.Mesh)) return;
        states.push({ mesh: object, material: object.material, visible: object.visible });
        const semanticId = String(object.userData.semanticId || "");
        const color = colorById.get(semanticId);
        if (color) {
          const key = color.join(",");
          let replacement = palette.get(key);
          if (!replacement) {
            replacement = new THREE.MeshBasicMaterial({
              color: new THREE.Color(color[0] / 255, color[1] / 255, color[2] / 255),
              side: THREE.DoubleSide,
            });
            palette.set(key, replacement);
          }
          object.material = replacement;
          object.visible = true;
        } else {
          // 非目标几何仍以黑色参与深度测试，避免透视穿透后方 subject。
          object.material = black;
          object.visible = !object.userData.subjectOnly && object.visible;
        }
      });
      scene.overrideMaterial = null;
      scene.background = new THREE.Color(0x000000);
      renderer.autoClear = true;
      renderer.setClearColor(0x000000, 1);
      renderer.render(scene, renderCamera);
      return renderer.domElement.toDataURL("image/png");
    } finally {
      states.forEach(({ mesh, material, visible }) => {
        mesh.material = material;
        mesh.visible = visible;
      });
      palette.forEach((material) => material.dispose());
      black.dispose();
      scene.overrideMaterial = previousOverride;
      scene.background = previousBackground;
      renderer.autoClear = previousAutoClear;
      renderer.setClearColor(previousClearColor, previousClearAlpha);
    }
  }

  async function renderPanoAtlases(center: MetricXYZ, faceSize: number, nearM: number, farM: number) {
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    if (!renderer || !scene) throw new Error("3D 渲染器尚未就绪");
    const originalWidth = renderer.domElement.width;
    const originalHeight = renderer.domElement.height;
    renderer.setSize(faceSize, faceSize, false);
    const kinds = ["rgb", "depth", "normal", "edge", "semantic"] as const;
    const atlases: Record<(typeof kinds)[number] | "subject_id", string> = {
      rgb: "", depth: "", normal: "", edge: "", semantic: "", subject_id: "",
    };
    const subjectIdLegend = buildSubjectIdLegend([
      ...model.openings.filter((row) => row.review_status !== "rejected").map((row) => ({
        subject: row.kind, anchor_id: row.id, anchor_kind: "opening", role: row.kind,
      })),
      ...model.fixed_objects.filter((row) => row.review_status !== "rejected").map((row) => ({
        subject: row.name || row.kind, anchor_id: row.id, anchor_kind: "fixed_object", role: row.semantic_role || row.kind,
      })),
      ...sceneInstances.map((row) => ({
        subject: row.semantic_role, anchor_id: row.instance_id, anchor_kind: "scene_recipe_object", role: row.semantic_role,
      })),
    ]);
    try {
      for (const kind of kinds) {
        const canvas = document.createElement("canvas");
        canvas.width = faceSize * 3;
        canvas.height = faceSize * 2;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("无法创建全景图集画布");
        for (const face of PANO_FACE_ORDER) {
          const faceCamera = createPanoFaceCamera(center, face, nearM, farM);
          const { dataUrl } = renderBuffer(kind, faceCamera, false, { worldNormal: true, metricDepthRange: [nearM, farM] });
          const image = await loadAtlasImage(dataUrl);
          const cell = PANO_ATLAS_CELL[face];
          context.drawImage(image, cell.col * faceSize, cell.row * faceSize);
        }
        atlases[kind] = canvas.toDataURL("image/png");
      }
      const subjectCanvas = document.createElement("canvas");
      subjectCanvas.width = faceSize * 3;
      subjectCanvas.height = faceSize * 2;
      const subjectContext = subjectCanvas.getContext("2d");
      if (!subjectContext) throw new Error("无法创建 subject-ID 全景图集画布");
      for (const face of PANO_FACE_ORDER) {
        const faceCamera = createPanoFaceCamera(center, face, nearM, farM);
        const image = await loadAtlasImage(renderPanoSubjectIdBuffer(faceCamera, subjectIdLegend));
        const cell = PANO_ATLAS_CELL[face];
        subjectContext.drawImage(image, cell.col * faceSize, cell.row * faceSize);
      }
      atlases.subject_id = subjectCanvas.toDataURL("image/png");
    } finally {
      renderer.setSize(originalWidth, originalHeight, false);
      if (cameraRef.current) renderer.render(scene, cameraRef.current);
    }
    return { atlases, subjectIdLegend };
  }

  function renderBuffer(
    kind: "rgb" | "depth" | "normal" | "edge" | "semantic",
    renderCamera = cameraRef.current,
    includePixels = false,
    options?: { worldNormal?: boolean; metricDepthRange?: [number, number] },
  ): { dataUrl: string; pixels?: Uint8Array; width: number; height: number } {
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    if (!renderer || !scene || !renderCamera) throw new Error("3D 渲染器尚未就绪");
    const previousOverride = scene.overrideMaterial;
    const previousBackground = scene.background;
    const previousAutoClear = renderer.autoClear;
    const previousClearColor = renderer.getClearColor(new THREE.Color()).clone();
    const previousClearAlpha = renderer.getClearAlpha();
    let material: THREE.Material | null = null;
    let edgePass: ReturnType<typeof prepareVisibleEdgePass> | null = null;
    const semanticMaterials: Array<{ mesh: THREE.Mesh; material: THREE.Material | THREE.Material[] }> = [];
    const semanticPalette = new Map<string, THREE.MeshBasicMaterial>();
    const helperStates: Array<{ object: THREE.Object3D; visible: boolean }> = [];
    let data = "";
    let pixels: Uint8Array | undefined;
    scene.traverse((object) => {
      if (object instanceof THREE.GridHelper) {
        helperStates.push({ object, visible: object.visible });
        object.visible = false;
      }
    });
    try {
      if (kind === "depth") {
        const metricRange = options?.metricDepthRange;
        const nearDistance = metricRange ? metricRange[0] : 0.1;
        const farDistance = metricRange ? metricRange[1] : Math.max(8, Math.hypot(model.width_m, model.depth_m) * 1.35);
        material = new THREE.ShaderMaterial({
          side: THREE.DoubleSide,
          uniforms: { nearDistance: { value: nearDistance }, farDistance: { value: farDistance } },
          vertexShader: "varying float cameraDepth; void main(){ vec4 viewPosition = modelViewMatrix * vec4(position, 1.0); cameraDepth = -viewPosition.z; gl_Position = projectionMatrix * viewPosition; }",
          fragmentShader: "varying float cameraDepth; uniform float nearDistance; uniform float farDistance; void main(){ float value = 1.0 - clamp((cameraDepth - nearDistance) / (farDistance - nearDistance), 0.0, 1.0); gl_FragColor = vec4(vec3(value), 1.0); }",
        });
      }
      if (kind === "normal" && options?.worldNormal) {
        // 球面全景契约:world-space XYZ→RGB(与相机朝向无关,六面边缘不跳色)。
        material = new THREE.ShaderMaterial({
          side: THREE.DoubleSide,
          vertexShader: "varying vec3 vWorldNormal; void main(){ vWorldNormal = normalize(mat3(modelMatrix) * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
          fragmentShader: "varying vec3 vWorldNormal; void main(){ gl_FragColor = vec4(vWorldNormal * 0.5 + 0.5, 1.0); }",
        });
      }
      if (kind === "normal" && !options?.worldNormal) material = new THREE.MeshNormalMaterial({ side: THREE.DoubleSide });
      if (kind === "semantic") {
        scene.traverse((object) => {
          if (!(object instanceof THREE.Mesh)) return;
          const role = String(object.userData.semanticRole || object.userData.kind || "other");
          const color = SEMANTIC_COLORS[role] || SEMANTIC_COLORS.other;
          let replacement = semanticPalette.get(color);
          if (!replacement) {
            replacement = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide });
            semanticPalette.set(color, replacement);
          }
          semanticMaterials.push({ mesh: object, material: object.material });
          object.material = replacement;
        });
      }
      scene.background = new THREE.Color(kind === "edge" ? 0xffffff : kind === "rgb" ? 0xe9e6df : 0x000000);
      renderer.autoClear = true;
      if (kind === "edge") {
        scene.overrideMaterial = null;
        edgePass = prepareVisibleEdgePass(scene);
        // White surfaces and their edge siblings share one depth-tested render.
        // This avoids the previous cross-render depth buffer, which WebGLRenderer
        // cleared before the line pass and therefore exposed back-side edges.
        renderer.autoClear = false;
        renderer.clear(true, true, true);
        renderer.render(scene, renderCamera);
      } else {
        scene.overrideMaterial = kind === "semantic" ? null : material;
        renderer.render(scene, renderCamera);
      }
      data = renderer.domElement.toDataURL("image/png");
      if (includePixels) {
        const gl = renderer.getContext();
        const width = renderer.domElement.width;
        const height = renderer.domElement.height;
        pixels = new Uint8Array(width * height * 4);
        gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      }
    } finally {
      edgePass?.restore();
      helperStates.forEach(({ object, visible }) => { object.visible = visible; });
      semanticMaterials.forEach(({ mesh, material: original }) => {
        mesh.material = original;
      });
      semanticPalette.forEach((replacement) => replacement.dispose());
      scene.overrideMaterial = previousOverride;
      scene.background = previousBackground;
      renderer.autoClear = previousAutoClear;
      renderer.setClearColor(previousClearColor, previousClearAlpha);
      material?.dispose();
    }
    return { dataUrl: data, pixels, width: renderer.domElement.width, height: renderer.domElement.height };
  }

  function projectSubjectIdBuffer(
    renderCamera: THREE.PerspectiveCamera,
    width: number,
    height: number,
    legend: WholeHomeSubjectIdLegend,
    subjects: NonNullable<WholeHomeCamera["reference_contract_validation"]>["must_show_subjects"],
  ) {
    const scene = sceneRef.current;
    if (!scene) throw new Error("3D 场景尚未就绪");
    renderCamera.updateMatrixWorld(true);
    scene.updateMatrixWorld(true);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("浏览器不支持 subject-ID Canvas");
    context.fillStyle = "rgb(0,0,0)";
    context.fillRect(0, 0, width, height);

    const projected = legend.subjects.map((entry) => {
      const subject = subjects.find((row) => row.anchor_id === entry.anchor_id);
      const box = new THREE.Box3();
      let found = false;
      scene.traverse((object) => {
        if (!(object instanceof THREE.Mesh) || String(object.userData.semanticId || "") !== entry.anchor_id) return;
        box.union(new THREE.Box3().setFromObject(object));
        found = true;
      });
      if (!found && subject?.anchor_kind === "cad_open_semantic_boundary" && subject.position) {
        const geometry = new THREE.BoxGeometry(.42, 1.8, .035);
        const mesh = new THREE.Mesh(geometry);
        mesh.position.set(subject.position.x, .9, subject.position.z);
        mesh.rotation.y = Math.atan2(
          renderCamera.position.x - subject.position.x,
          renderCamera.position.z - subject.position.z,
        );
        mesh.updateMatrixWorld(true);
        box.setFromObject(mesh);
        geometry.dispose();
        found = true;
      }
      if (!found || box.isEmpty()) return { entry, distance: Number.POSITIVE_INFINITY, bounds: null };
      const corners = [
        [box.min.x, box.min.y, box.min.z], [box.min.x, box.min.y, box.max.z],
        [box.min.x, box.max.y, box.min.z], [box.min.x, box.max.y, box.max.z],
        [box.max.x, box.min.y, box.min.z], [box.max.x, box.min.y, box.max.z],
        [box.max.x, box.max.y, box.min.z], [box.max.x, box.max.y, box.max.z],
      ].map(([x, y, z]) => new THREE.Vector3(x, y, z).project(renderCamera));
      const visible = corners.filter((corner) => Number.isFinite(corner.x) && Number.isFinite(corner.y)
        && corner.z >= -1 && corner.z <= 1);
      if (!visible.length) return { entry, distance: Number.POSITIVE_INFINITY, bounds: null };
      const xMin = Math.min(...visible.map((corner) => (corner.x + 1) / 2));
      const xMax = Math.max(...visible.map((corner) => (corner.x + 1) / 2));
      const yMin = Math.min(...visible.map((corner) => (1 - corner.y) / 2));
      const yMax = Math.max(...visible.map((corner) => (1 - corner.y) / 2));
      return {
        entry,
        distance: box.getCenter(new THREE.Vector3()).distanceTo(renderCamera.position),
        bounds: { xMin, xMax, yMin, yMax },
      };
    }).sort((left, right) => right.distance - left.distance);

    for (const row of projected) {
      if (!row.bounds) continue;
      const left = Math.max(0, Math.floor(row.bounds.xMin * width));
      const top = Math.max(0, Math.floor(row.bounds.yMin * height));
      const right = Math.min(width, Math.ceil(row.bounds.xMax * width));
      const bottom = Math.min(height, Math.ceil(row.bounds.yMax * height));
      if (right <= left || bottom <= top) continue;
      context.fillStyle = `rgb(${row.entry.color[0]},${row.entry.color[1]},${row.entry.color[2]})`;
      context.fillRect(left, top, right - left, bottom - top);
    }
    const pixels = context.getImageData(0, 0, width, height).data;
    return { dataUrl: canvas.toDataURL("image/png"), pixels, width, height };
  }

  function renderCandidatePreview(candidate: WholeHomeCameraCandidate) {
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    if (!renderer || !scene) throw new Error("3D 渲染器尚未就绪");
    const originalWidth = renderer.domElement.width;
    const originalHeight = renderer.domElement.height;
    const aspect = ASPECTS[aspectRatio];
    const width = aspect >= 1 ? 512 : Math.round(512 * aspect);
    const height = aspect >= 1 ? Math.round(512 / aspect) : 512;
    renderer.setSize(width, height, false);
    try {
      const renderCamera = createRenderCamera(candidate.camera, width, height);
      const preview = renderBuffer("rgb", renderCamera).dataUrl;
      const semantic = renderBuffer("semantic", renderCamera, true);
      if (!semantic.pixels) throw new Error("semantic buffer 未返回像素");
      const profile = String(candidate.metrics.room_profile || "other") as WholeHomeRenderGateProfile;
      return {
        preview,
        renderGate: analyzeWholeHomeSemanticPixels(
          semantic.pixels,
          semantic.width,
          semantic.height,
          profile,
        ),
      };
    } finally {
      renderer.setSize(originalWidth, originalHeight, false);
      if (cameraRef.current) renderer.render(scene, cameraRef.current);
    }
  }

  function renderReferenceCandidateEvidence(candidate: WholeHomeCameraCandidate) {
    const renderer = rendererRef.current;
    const scene = sceneRef.current;
    const validation = candidate.camera.reference_contract_validation;
    if (!renderer || !scene || !validation || !referenceContract) throw new Error("reference candidate 缺少合同或锚点证据");
    const originalWidth = renderer.domElement.width;
    const originalHeight = renderer.domElement.height;
    // Reference preflight is a browser-side hard gate, not the paid output.
    // 384x288 keeps the subject bounds deterministic while avoiding the GPU
    // pressure that previously crashed Chrome before the first slot persisted.
    const width = 384;
    const height = 288;
    const previousShadowMapEnabled = renderer.shadowMap.enabled;
    renderer.shadowMap.enabled = false;
    renderer.setSize(width, height, false);
    const checkpoint = (stage: string, details: Record<string, unknown> = {}) => {
      window.localStorage.setItem("whole_home_reference_render_checkpoint", JSON.stringify({
        stage,
        candidate_id: candidate.candidate_id,
        slot_id: candidate.slot_id,
        recorded_at: Date.now(),
        ...details,
      }));
    };
    try {
      checkpoint("create_camera");
      const renderCamera = createRenderCamera(candidate.camera, width, height);
      checkpoint("semantic_start");
      const semantic = renderBuffer("semantic", renderCamera, true);
      checkpoint("semantic_done");
      if (!semantic.pixels) throw new Error("semantic buffer 未返回像素");
      const profile = String(candidate.metrics.room_profile || "other") as WholeHomeRenderGateProfile;
      const renderGate = evaluateReferenceBaseRenderGate(
        analyzeWholeHomeSemanticPixels(semantic.pixels, semantic.width, semantic.height, profile),
      );
      checkpoint("semantic_gate_done", { render_gate: renderGate });
      if (!renderGate.pass) {
        throw new Error(`Reference semantic gate blocked ${candidate.slot_id}: ${renderGate.reasons.join("；")}`);
      }
      const legend = buildSubjectIdLegend(validation.must_show_subjects.map((row) => ({
        subject: row.subject, anchor_id: row.anchor_id, anchor_kind: row.anchor_kind, role: row.role,
      })));
      checkpoint("subject_id_start");
      const subjectId = projectSubjectIdBuffer(
        renderCamera, width, height, legend, validation.must_show_subjects,
      );
      checkpoint("subject_id_done");
      const subjectEvidence = analyzeSubjectIdPixels(
        subjectId.pixels, width, height, legend, referenceContract.camera.safe_frame, "top-left",
      );
      checkpoint("subject_gate_done", { subject_gate_pass: subjectEvidence.pass });
      if (!subjectEvidence.pass) {
        throw new Error(`Reference subject-ID gate blocked ${candidate.slot_id}: ${subjectEvidence.reasons.join("；")}`);
      }
      checkpoint("rgb_start");
      const rgb = renderBuffer("rgb", renderCamera).dataUrl;
      checkpoint("rgb_done");
      checkpoint("depth_start");
      const depth = renderBuffer("depth", renderCamera).dataUrl;
      checkpoint("depth_done");
      checkpoint("normal_start");
      const normal = renderBuffer("normal", renderCamera).dataUrl;
      checkpoint("normal_done");
      checkpoint("edge_start");
      const edge = renderBuffer("edge", renderCamera).dataUrl;
      checkpoint("edge_done");
      return {
        buffers: { rgb, depth, normal, edge, semantic: semantic.dataUrl, subjectId: subjectId.dataUrl,
          subjectIdLegend: legend, proposalId: candidate.proposal_id || "", proposalHash: candidate.proposal_hash || "" },
        renderGate,
        subjectEvidence,
      };
    } finally {
      renderer.shadowMap.enabled = previousShadowMapEnabled;
      renderer.setSize(originalWidth, originalHeight, false);
      if (cameraRef.current) renderer.render(scene, cameraRef.current);
    }
  }

  async function autoCaptureReferenceProposal(proposal: WholeHomeCameraCandidateProposal) {
    const pools = proposal.slot_pools || [];
    if (proposal.status !== "ready" || pools.length !== 9 || !proposal.proposal_id || !proposal.proposal_hash) {
      const details = proposal.hard_errors?.map((row) => row.code || row.message).filter(Boolean).join("、");
      throw new Error(`Reference 本地候选未就绪：${details || "9-slot pool 不完整"}`);
    }
    const completedSlots = new Set(completedReferenceSlotIds);
    const selectedCandidates: WholeHomeCameraCandidate[] = [];
    const selectedCameras: WholeHomeCamera[] = [];
    const saved: WholeHomeCapture[] = [];
    const buildPlan = (): WholeHomeAutoCameraPlan => ({
      plan_id: proposal.proposal_id!, project_id: "", status: "done", aspect_ratio: "4:3", shots_per_room: 1,
      summary: `本地 reference-slot ranker：9 个 slot，逐项固化 ${selectedCameras.length} 个新机位`,
      ai_model: "local-reference-ranker", ai_error: "", candidates: selectedCandidates, contact_sheets: [],
      selections: selectedCandidates.map((candidate) => ({
        candidate_id: candidate.candidate_id, room_id: candidate.room_id, rank: 1,
        visual_score: candidate.local_score, reason: "browser subject-ID + local geometry pass",
        strengths: ["subject-id-safe-frame"], risks: [], selection_source: "local_fallback",
      })),
      selected_cameras: selectedCameras,
      room_pools: pools.map((pool) => ({ ...pool, status: "ready", reasons: [],
        candidate_ids: selectedCandidates.filter((row) => row.slot_id === pool.slot_id).map((row) => row.candidate_id) })),
      created_at: Date.now() / 1000,
    });
    for (const pool of pools) {
      if (pool.slot_id && completedSlots.has(pool.slot_id)) {
        setAutoStage(`已存在可用证据，跳过 ${pool.slot_id}`);
        continue;
      }
      const candidates = proposal.candidates.filter((row) => row.slot_id === pool.slot_id);
      let persisted = false;
      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        setAutoStage(`Reference 浏览器证据 ${pool.slot_id} · ${index + 1}/${candidates.length}`);
        const evidence = renderReferenceCandidateEvidence(candidate);
        if (evidence.renderGate.pass && evidence.subjectEvidence?.pass && evidence.buffers) {
          const camera: WholeHomeCamera = {
            ...candidate.camera,
            candidate_id: candidate.candidate_id,
            reference_slot_id: candidate.slot_id,
            reference_proposal_id: proposal.proposal_id,
            reference_proposal_hash: proposal.proposal_hash,
            pool_rank: 1,
            is_primary: true,
            render_gate: evidence.renderGate,
            reference_contract_validation: {
              ...candidate.camera.reference_contract_validation!,
              width: evidence.subjectEvidence.width,
              height: evidence.subjectEvidence.height,
              pixel_origin: "top-left" as const,
              must_show_bounds: evidence.subjectEvidence.must_show_bounds,
              safe_frame_status: "pass",
              safe_frame_pass: true,
            },
          };
          selectedCandidates.push(candidate);
          selectedCameras.push(camera);
          const plan = buildPlan();
          setLastAutoPlan(plan);
          setAutoStage(`正在永久保存 ${pool.slot_id}（完成后才进入下一个 slot）`);
          saved.push(await onSaveCapture(camera, evidence.buffers, plan));
          completedSlots.add(pool.slot_id || "");
          persisted = true;
          // Drop all six data URLs before the next slot and yield to Chrome so
          // a renderer failure can never erase work that already passed.
          await new Promise<void>((resolve) => window.setTimeout(resolve, 80));
          break;
        }
        await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
      }
      if (!persisted) throw new Error(`${pool.slot_id} 没有同时通过灰模与 subject-ID safe-frame 的候选；已保存的其他 slot 会保留，可修正后断点续跑`);
    }
    const plan = buildPlan();
    setLastAutoPlan(plan);
    setAutoStage("本轮 reference 浏览器证据已逐项保存，正在重新读取项目并执行 paid gate…");
    await onAutoCaptureComplete(saved, plan);
  }

  async function autoSelectAndCapture() {
    if (manualSafe) throw new Error("手动安全模式不开放 AI 自动机位；请在灰模中保存一个明确机位");
    if (!verified) throw new Error("请先锁定整屋几何");
    if (!viewerReady || !rendererRef.current || !sceneRef.current) throw new Error("3D 灰模尚未就绪");
    if (autoRunningRef.current) throw new Error("自动机位流程正在运行");
    autoRunningRef.current = true;
    setAutoRunning(true);
    setLastAutoPlan(null);
    try {
      setAutoStage("正在从房间几何生成安全候选机位…");
      const proposal = await onGenerateCameraCandidates();
      setLastCandidateProposal(proposal);
      const rawCandidates = proposal.candidates;
      if (!rawCandidates.length) throw new Error("没有找到位于房间内部的安全机位，请检查房间边界和固定物");
      if (referenceMode) {
        await autoCaptureReferenceProposal(proposal);
        setAutoStage("Reference 本地证据流程完成；付费生成需通过页面 paid gate 后另行点击");
        return;
      }
      const auditedCandidates: WholeHomeCameraCandidate[] = [];
      for (let index = 0; index < rawCandidates.length; index += 1) {
        const candidate = rawCandidates[index];
        setAutoStage(`正在执行灰模渲染门禁 ${index + 1}/${rawCandidates.length} · ${candidate.room_label}`);
        const rendered = renderCandidatePreview(candidate);
        auditedCandidates.push({
          ...candidate,
          preview_data_url: rendered.preview,
          metrics: { ...candidate.metrics, render_gate: rendered.renderGate },
        });
        if (index % 3 === 2) await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
      }
      const decision = filterWholeHomeRenderCandidates(auditedCandidates);
      const auditedPools = proposal.room_pools.map((pool) => {
        if (pool.status === "blocked") return pool;
        const result = decision.room_results[pool.room_id];
        const rejectionSummary = {
          ...(pool.rejection_summary || {}),
          render_gate_rejected: result?.rejected.filter((row) => row.metrics.render_gate?.pass !== true).length || 0,
          render_gate_deferred_20mm_used: result?.used_deferred_20mm ? 1 : 0,
          render_gate_best: result?.best_rejected_gate || {},
        };
        if (!result?.eligible.length) {
          return {
            ...pool,
            status: "blocked" as const,
            reasons: result?.reasons || ["灰模渲染门禁无合格机位"],
            candidate_ids: [],
            rejection_summary: rejectionSummary,
          };
        }
        return {
          ...pool,
          status: "ready" as const,
          reasons: [],
          candidate_ids: result.eligible.slice(0, 3).map((row) => row.candidate_id),
          rejection_summary: rejectionSummary,
        };
      });
      const auditedProposal: WholeHomeCameraCandidateProposal = {
        ...proposal,
        status: auditedPools.every((pool) => pool.status === "ready")
          ? "ready"
          : decision.eligible.length ? "partial" : "blocked",
        candidates: auditedCandidates,
        room_pools: auditedPools,
        blocked_rooms: auditedPools.filter((pool) => pool.status === "blocked"),
        rejection_summary: Object.fromEntries(
          auditedPools.map((pool) => [pool.room_id, pool.rejection_summary || {}]),
        ),
      };
      setLastCandidateProposal(auditedProposal);
      if (!decision.eligible.length) throw new Error("全部房间均被灰模渲染门禁阻断；不会提交 Gemini 或付费生成");
      setAutoStage(`Gemini 正在复排 ${decision.eligible.length} 个渲染门禁合格候选…`);
      const plan = await onRankAutoCameras(decision.eligible, auditedPools);
      setLastAutoPlan(plan);
      if (!plan.selected_cameras.length) throw new Error("自动机位复排没有返回可保存机位");
      const renderer = rendererRef.current;
      const width = renderer.domElement.width;
      const height = renderer.domElement.height;
      const saved: WholeHomeCapture[] = [];
      for (let index = 0; index < plan.selected_cameras.length; index += 1) {
        const camera = plan.selected_cameras[index];
        if (camera.render_gate?.pass !== true) {
          throw new Error(`${camera.name} 缺少通过的灰模渲染门禁，已阻止保存和付费生成`);
        }
        setAutoStage(`正在固化五通道约束 ${index + 1}/${plan.selected_cameras.length} · ${camera.name}`);
        const renderCamera = createRenderCamera(camera, width, height);
        const buffers = {
          rgb: renderBuffer("rgb", renderCamera).dataUrl, depth: renderBuffer("depth", renderCamera).dataUrl,
          normal: renderBuffer("normal", renderCamera).dataUrl, edge: renderBuffer("edge", renderCamera).dataUrl,
          semantic: renderBuffer("semantic", renderCamera).dataUrl,
        };
        saved.push(await onSaveCapture(camera, buffers, plan));
      }
      setAutoStage(`已保存 ${saved.length} 个自动机位，正在提交 B2 / Pro 生成…`);
      await onAutoCaptureComplete(saved, plan);
      setAutoStage(`自动链路已提交：${plan.summary}`);
    } finally {
      autoRunningRef.current = false;
      setAutoRunning(false);
    }
  }

  useImperativeHandle(ref, () => ({ autoSelectAndCapture }));

  async function saveCamera() {
    if (viewerModeRef.current !== "perspective") return;
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;
    const value: WholeHomeCamera = {
      id: id("camera"), name: cameraName.trim() || `机位 ${model.cameras.length + 1}`,
      position: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
      target: { x: controls.target.x, y: controls.target.y, z: controls.target.z },
      focal_length_mm: focal, room_id: "", enabled: true, source: "human_3d",
    };
    await onSaveCapture(value, {
      rgb: renderBuffer("rgb").dataUrl, depth: renderBuffer("depth").dataUrl, normal: renderBuffer("normal").dataUrl, edge: renderBuffer("edge").dataUrl,
      semantic: renderBuffer("semantic").dataUrl,
    });
    setCameraName(`机位 ${model.cameras.length + 2}`);
  }

  async function savePanoCapture() {
    if (viewerModeRef.current !== "perspective") return;
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || !onSavePanoCapture) return;
    const center: MetricXYZ = { x: camera.position.x, y: camera.position.y, z: camera.position.z };
    const faceSize = 512;
    const nearM = 0.1;
    const farM = Math.max(8, Math.hypot(model.width_m, model.depth_m) * 1.35);
    const rendered = await renderPanoAtlases(center, faceSize, nearM, farM);
    const panoCamera: WholeHomeCamera = {
      id: id("pano"), name: cameraName.trim() || "全景热点",
      position: center,
      target: { x: controls.target.x, y: controls.target.y, z: controls.target.z },
      focal_length_mm: 12, projection: "equirectangular",
      room_id: "", enabled: true, source: "human_3d",
    };
    await onSavePanoCapture(panoCamera, {
      pano_id: `pano_${panoCamera.id}`,
      camera_center_m: center,
      cube_face_size: faceSize,
      erp_width: PANO_P0_ERP_SIZE.width,
      erp_height: PANO_P0_ERP_SIZE.height,
      near_m: nearM, far_m: farM,
      heading_deg: 0, pitch_deg: 0, roll_deg: 0,
      atlases: rendered.atlases,
      subject_id_legend: rendered.subjectIdLegend,
      render_contract: {
        materials: {
          wall: { color: "#d8d4ca", roughness: 0.95, metalness: 0 },
          floor: { color: "#bcb6aa", roughness: 1, metalness: 0, side: "double" },
          fixed_object: { color: "#c9c3b8", roughness: 0.92, metalness: 0 },
          semantic_palette: WHOLE_HOME_SEMANTIC_COLORS,
          subject_id_version: rendered.subjectIdLegend.version,
        },
        lighting: {
          hemisphere: { sky: "#ffffff", ground: "#756f67", intensity: 2.2 },
          directional: {
            color: "#ffffff", intensity: 3.4,
            position: [-model.width_m * 0.3, 10, -model.depth_m * 0.2],
            cast_shadow: true, shadow_map_size: [2048, 2048], shadow_type: "PCFSoftShadowMap",
          },
        },
      },
    });
    setCameraName(`机位 ${model.cameras.length + 2}`);
  }

  const previewRect = (() => {
    if (!drag || drag.type === "endpoint") return null;
    return {
      x: Math.min(drag.start.x, drag.current.x), z: Math.min(drag.start.z, drag.current.z),
      width: Math.abs(drag.current.x - drag.start.x), height: Math.abs(drag.current.z - drag.start.z),
    };
  })();

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-[minmax(0,1fr)_320px] gap-4 max-[1050px]:grid-cols-1">
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <b className="mr-auto text-sm">整屋 2D 建模底图</b>
            {(cadGeometryReadOnly ? ([['select', '只读查看']] as const) : ([['select', '选择'], ['wall', '拖画墙'], ['room', '拖画房间']] as const)).map(([key, label]) => (
              <Button key={key} size="sm" variant={tool === key ? "default" : "outline"} onClick={() => setTool(key)}>
                {key === "wall" ? <DoorOpen /> : key === "room" ? <Square /> : <Eye />}{label}
              </Button>
            ))}
            {cadGeometryReadOnly && <span className="text-[11px] font-semibold text-amber-700">CAD 几何权威：本页不能移动、增删或缩放任何建筑事实</span>}
          </div>
          <div className="relative bg-[#f6f1e8]">
            <img src={floorplanUrl} alt="原始户型图" className="pointer-events-none absolute inset-0 h-full w-full opacity-55" />
            <svg
              ref={planRef}
              viewBox={`0 0 ${model.width_m} ${model.depth_m}`}
              preserveAspectRatio="none"
              className={`relative z-10 block min-h-[460px] w-full ${tool === "select" ? "cursor-default" : "cursor-crosshair"}`}
              onPointerDown={planDown}
              onPointerMove={planMove}
              onPointerUp={planUp}
            >
              {model.rooms.map((room) => (
                <g key={room.id} onPointerDown={(event) => { if (tool === "select") { event.stopPropagation(); setSelectedRoomId(room.id); setSelectedObjectId(""); } }}>
                  <polygon points={room.polygon.map((point) => `${point.x},${point.z}`).join(" ")} fill={room.id === selectedRoom?.id ? "rgba(205,118,72,.18)" : "rgba(205,118,72,.10)"} stroke={room.id === selectedRoom?.id ? "#c45f32" : "rgba(205,118,72,.45)"} strokeWidth={room.id === selectedRoom?.id ? 0.08 : 0.04} vectorEffect="non-scaling-stroke" />
                  <text x={room.polygon.reduce((sum, p) => sum + p.x, 0) / room.polygon.length} y={room.polygon.reduce((sum, p) => sum + p.z, 0) / room.polygon.length} textAnchor="middle" fontSize={0.28} fill="#7c3d20">{room.label}</text>
                </g>
              ))}
              {model.fixed_objects.filter((item) => item.review_status !== "rejected").map((item) => (
                <g key={item.id} onPointerDown={(event) => { if (tool === "select") { event.stopPropagation(); setSelectedRoomId(item.room_id); setSelectedObjectId(item.id); } }}>
                  <polygon
                    points={semanticFootprint(item).map((point) => `${point.x},${point.z}`).join(" ")}
                    fill={`${SEMANTIC_COLORS[item.semantic_role] || SEMANTIC_COLORS.other}55`}
                    stroke={item.id === selectedObjectId ? "#dc2626" : SEMANTIC_COLORS[item.semantic_role] || SEMANTIC_COLORS.other}
                    strokeWidth={item.id === selectedObjectId ? .1 : .06}
                  />
                  <text x={item.position.x} y={item.position.z} textAnchor="middle" fontSize={.2} fill="#111827">{item.name}</text>
                </g>
              ))}
              {model.walls.map((wall) => {
                const active = wall.id === selectedWallId;
                const reviewOnly = wall.review_status === "needs_review"
                  || wall.boundary_kind === "unresolved_review_evidence";
                return (
                  <g key={wall.id} onPointerDown={(event) => { if (tool === "select") { event.stopPropagation(); setSelectedWallId(wall.id); } }}>
                    <line x1={wall.start.x} y1={wall.start.z} x2={wall.end.x} y2={wall.end.z} stroke="transparent" strokeWidth={0.35} />
                    <line x1={wall.start.x} y1={wall.start.z} x2={wall.end.x} y2={wall.end.z} stroke={active ? "#e24e19" : reviewOnly ? "#d97706" : wall.kind === "exterior" ? "#111827" : "#4b5563"} strokeWidth={active ? 0.12 : reviewOnly ? 0.08 : wall.kind === "exterior" ? 0.1 : 0.07} strokeDasharray={reviewOnly ? "0.16 0.11" : undefined} vectorEffect="non-scaling-stroke" />
                    {active && <>
                      {(['start', 'end'] as const).map((end) => <circle key={end} cx={wall[end].x} cy={wall[end].z} r={0.15} fill="#fff" stroke="#e24e19" strokeWidth={0.05} onPointerDown={(event) => { event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId); setDrag({ type: "endpoint", wallId: wall.id, end }); }} />)}
                    </>}
                  </g>
                );
              })}
              {model.openings.filter((opening) => opening.review_status !== "rejected").map((opening) => {
                const wall = model.walls.find((item) => item.id === opening.wall_id);
                if (!wall) return null;
                const wallLength = length(wall);
                const a = midpoint(wall, opening.offset_m / wallLength);
                const b = midpoint(wall, (opening.offset_m + opening.width_m) / wallLength);
                return <line key={opening.id} x1={a.x} y1={a.z} x2={b.x} y2={b.z} stroke={opening.kind === "window" ? "#0284c7" : opening.kind === "door" ? "#16a34a" : "#9333ea"} strokeWidth={0.16} strokeDasharray={opening.review_status === "pending" ? "0.15 0.1" : undefined} />;
              })}
              {(model.geometry_report?.hard_errors || []).map((issue, index) => issue.start && issue.end ? (
                <g key={`geometry-gap-${index}`} pointerEvents="none">
                  <line x1={issue.start.x} y1={issue.start.z} x2={issue.end.x} y2={issue.end.z} stroke="#dc2626" strokeWidth={0.18} strokeDasharray="0.2 0.1" vectorEffect="non-scaling-stroke" />
                  <circle cx={issue.start.x} cy={issue.start.z} r={0.11} fill="#fff" stroke="#dc2626" strokeWidth={0.05} />
                  <circle cx={issue.end.x} cy={issue.end.z} r={0.11} fill="#fff" stroke="#dc2626" strokeWidth={0.05} />
                </g>
              ) : issue.point ? (
                <circle key={`geometry-point-${index}`} cx={issue.point.x} cy={issue.point.z} r={0.16} fill="#fff" stroke="#dc2626" strokeWidth={0.08} pointerEvents="none" />
              ) : null)}
              {drag?.type === "wall" && <line x1={drag.start.x} y1={drag.start.z} x2={drag.current.x} y2={drag.current.z} stroke="#e24e19" strokeWidth={0.1} strokeDasharray="0.18 0.1" />}
              {drag?.type === "room" && previewRect && <rect x={previewRect.x} y={previewRect.z} width={previewRect.width} height={previewRect.height} fill="rgba(226,78,25,.12)" stroke="#e24e19" strokeWidth={0.08} strokeDasharray="0.18 0.1" />}
            </svg>
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-xl border border-border bg-card p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-bold"><Box size={16} />模型尺度与墙体</div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <label>整图宽度 m<Input disabled={cadGeometryReadOnly} type="number" min={2} max={80} step={0.1} value={model.width_m} onChange={(event) => onChange(rescaleModel(model, Number(event.target.value), model.depth_m), "calibrate_width")} /></label>
              <label>整图深度 m<Input disabled={cadGeometryReadOnly} type="number" min={2} max={80} step={0.1} value={model.depth_m} onChange={(event) => onChange(rescaleModel(model, model.width_m, Number(event.target.value)), "calibrate_depth")} /></label>
              <label>默认墙高 m<Input disabled={cadGeometryReadOnly} type="number" min={2} max={6} step={0.05} value={model.wall_height_m} onChange={(event) => onChange({ ...model, wall_height_m: Number(event.target.value) }, "wall_height")} /></label>
              <label>默认墙厚 m<Input disabled={cadGeometryReadOnly} type="number" min={0.05} max={0.8} step={0.01} value={model.wall_thickness_m} onChange={(event) => onChange({ ...model, wall_thickness_m: Number(event.target.value) }, "wall_thickness")} /></label>
            </div>
            <div className="mt-2 text-[11px] text-muted-foreground">{cadGeometryReadOnly ? "CAD $INSUNITS 权威尺度；任何通用缩放都会被前后端共同阻断" : model.scale.status === "calibrated" ? "已人工校准尺度" : "AI 估算尺度；请用图纸标注尺寸复核"}</div>
          </div>

          <div className="rounded-xl border border-border bg-card p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-bold"><DoorOpen size={16} />选中墙与开口</div>
            {selectedWall ? <>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <label>墙体类型<select className={`${inputClass} block w-full`} value={selectedWall.kind} onChange={(event) => { const value = cloneModel(model); const wall = value.walls.find((item) => item.id === selectedWall.id)!; wall.kind = event.target.value as WholeHomeWall["kind"]; onChange(value, "wall_kind"); }}><option value="exterior">外墙</option><option value="interior">内墙</option><option value="partition">隔断</option></select></label>
                <label>长度<div className="mt-1 h-8 rounded-lg bg-muted px-2 py-2">{length(selectedWall).toFixed(2)} m</div></label>
              </div>
              {!cadGeometryReadOnly && <div className="mt-2 flex flex-wrap gap-1"><Button size="sm" variant="outline" onClick={() => addOpening("door")}>+ 门</Button><Button size="sm" variant="outline" onClick={() => addOpening("window")}>+ 窗</Button><Button size="sm" variant="outline" onClick={() => addOpening("open_connection")}>+ 开洞</Button><Button size="sm" variant="destructive" onClick={deleteSelectedWall}><Trash2 />删除墙</Button></div>}
            </> : <div className="text-xs text-muted-foreground">在左侧选择一段墙，再添加门窗或拖动端点。</div>}
            {!cadGeometryReadOnly && autoAcceptablePendingOpenings > 0 && <Button className="mt-2 w-full" size="sm" onClick={acceptAllOpenings}><CheckCircle2 />一键接受 {autoAcceptablePendingOpenings} 个规则可自动确认的 AI 门窗</Button>}
            {cadGeometryReadOnly && <div className="mt-2 rounded-lg bg-amber-50 p-2 text-[11px] text-amber-800">门窗来自可追溯 CAD 实体。本版只展示证据；若解析错误，请修正源 CAD/DXF 后重新解析。</div>}
            {manualReviewOpenings.length > 0 && <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-[11px] text-amber-800">{manualReviewOpenings.length} 个开口存在房间邻接语义冲突，已从一键接受中排除，需人工修改或排除。</div>}
          </div>

          <div className="max-h-[300px] overflow-y-auto rounded-xl border border-border bg-card p-3">
            <div className="mb-2 text-sm font-bold">全部门窗（{model.openings.length}）</div>
            <div className="space-y-2">
              {model.openings.map((opening) => (
                <div key={opening.id} className="rounded-lg border border-border p-2 text-xs">
                  <div className="mb-1 flex items-center justify-between"><b>{opening.kind === "door" ? "门" : opening.kind === "window" ? "窗" : "开放连接"} · {opening.id}</b>{!cadGeometryReadOnly && <button onClick={() => { const value = cloneModel(model); value.openings = value.openings.filter((item) => item.id !== opening.id); onChange(value, "delete_opening"); }}><Trash2 size={14} /></button>}</div>
                  <div className="grid grid-cols-2 gap-1">
                    <label>距墙起点<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" step={0.05} value={opening.offset_m} onChange={(event) => patchOpening(opening.id, { offset_m: Number(event.target.value) })} /></label>
                    <label>宽度<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" step={0.05} value={opening.width_m} onChange={(event) => patchOpening(opening.id, { width_m: Number(event.target.value) })} /></label>
                  </div>
                  {!cadGeometryReadOnly && <div className="mt-1 flex gap-1"><Button size="sm" variant={opening.review_status === "accepted" ? "default" : "outline"} onClick={() => patchOpening(opening.id, { review_status: "accepted" })}>接受</Button><Button size="sm" variant={opening.review_status === "rejected" ? "destructive" : "outline"} onClick={() => patchOpening(opening.id, { review_status: "rejected" })}>排除</Button></div>}
                  {opening.opening_topology_review?.status === "manual_review_required" && <div className="mt-1 text-amber-700">需人工复核：{opening.opening_topology_review.reason}</div>}
                  {opening.duplicate_of && <div className="mt-1 text-muted-foreground">重复于 {opening.duplicate_of}，记录保留但不参与建模/机位。</div>}
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-3 text-xs">
            <div className="mb-2 flex items-center justify-between gap-2"><b className="text-sm">房间语义与代理</b><span className={model.semantic_report?.hard_errors?.length ? "text-red-600" : "text-emerald-600"}>{model.semantic_report?.hard_errors?.length ? `${model.semantic_report.hard_errors.length} 个必须修正` : "本地规则通过"}</span></div>
            {selectedRoom ? <>
              <label>当前房间<select className={`${inputClass} mt-1 block w-full`} value={selectedRoom.id} onChange={(event) => { setSelectedRoomId(event.target.value); setSelectedObjectId(""); }}>{model.rooms.map((room) => <option key={room.id} value={room.id}>{room.label} · {room.semantic_profile}</option>)}</select></label>
              <label className="mt-2 block">房间类型<select disabled={cadGeometryReadOnly} className={`${inputClass} mt-1 block w-full`} value={selectedRoom.semantic_profile} onChange={(event) => { const value = cloneModel(model); const room = value.rooms.find((item) => item.id === selectedRoom.id)!; room.semantic_profile = event.target.value as WholeHomeRoom["semantic_profile"]; room.room_type = event.target.value; room.source = room.source === "ai" ? "ai_edited" : room.source; onChange(value, "edit_room_semantic_profile"); }}><option value="kitchen">厨房</option><option value="bathroom">卫生间</option><option value="bedroom">卧室</option><option value="living_room">客厅</option><option value="foyer">玄关</option><option value="balcony">阳台</option><option value="other">其他</option></select></label>
              {!cadGeometryReadOnly && <div className="mt-2 flex flex-wrap gap-1">{(PROFILE_ROLES[selectedRoom.semantic_profile] || PROFILE_ROLES.other).map((role) => <Button key={role} size="sm" variant="outline" onClick={() => addSemanticObject(role)}>+ {ROLE_DEFAULTS[role]?.name || role}</Button>)}</div>}
              <div className="mt-2 text-[11px] text-muted-foreground">{cadGeometryReadOnly ? "CAD 房型、观测固定物和 layout proxy 在 v1 中整体只读；请回源修正后重新解析，禁止在浏览器里解除 CAD 事实。" : "AI 代理通过本地边界、重叠、门洞和必需角色检查后会自动接受；这里用于少量人工纠偏。"}</div>
            </> : <div className="text-muted-foreground">先在左侧选择房间。</div>}
            {selectedObject && <div className="mt-3 rounded-lg border border-border p-2">
              <div className="mb-2 flex items-center justify-between"><b>{selectedObject.name} · {selectedObject.semantic_role}</b>{!cadGeometryReadOnly && <Button size="sm" variant="destructive" onClick={deleteSemanticObject}><Trash2 />删除</Button>}</div>
              <div className="grid grid-cols-2 gap-1">
                <label>X m<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" step={.05} value={selectedObject.position.x} onChange={(event) => patchSemanticObject({ position: { ...selectedObject.position, x: Number(event.target.value) } })} /></label>
                <label>Z m<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" step={.05} value={selectedObject.position.z} onChange={(event) => patchSemanticObject({ position: { ...selectedObject.position, z: Number(event.target.value) } })} /></label>
                <label>宽 m<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" min={.1} step={.05} value={selectedObject.size.x} onChange={(event) => patchSemanticObject({ size: { ...selectedObject.size, x: Number(event.target.value) } })} /></label>
                <label>深 m<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" min={.1} step={.05} value={selectedObject.size.z} onChange={(event) => patchSemanticObject({ size: { ...selectedObject.size, z: Number(event.target.value) } })} /></label>
                <label>高 m<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" min={.1} step={.05} value={selectedObject.size.y} onChange={(event) => patchSemanticObject({ size: { ...selectedObject.size, y: Number(event.target.value) } })} /></label>
                <label>旋转 °<input disabled={cadGeometryReadOnly} className={`${inputClass} w-full`} type="number" step={5} value={selectedObject.rotation_y_deg} onChange={(event) => patchSemanticObject({ rotation_y_deg: Number(event.target.value) })} /></label>
              </div>
            </div>}
          </div>
        </div>
      </div>

      <div
        className="overflow-hidden rounded-xl border border-border bg-card"
        data-testid="whole-home-3d-viewer"
        data-view-mode={viewerMode}
        data-structure-only={viewerMode === "orthographic-audit" ? "true" : "false"}
        data-audit-frame-state={viewerMode === "orthographic-audit" ? "canonical" : "inactive"}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
          <div className="mr-auto"><b className="text-sm">整屋 3D 灰模（含审阅线索）</b><div className="text-[10px] text-muted-foreground">鼠标左键旋转 · 右键平移 · 滚轮缩放 · WASD/QE 漫游 · Shift 加速</div></div>
          {globalWallShellCount > 0 ? <span className="rounded-full border border-emerald-300 bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-800">全局实体墙壳 {globalWallShellCount} · 源线覆盖 {globalWallCoverage != null ? `${(globalWallCoverage * 100).toFixed(1)}%` : "—"}</span> : <span className="rounded-full border border-stone-300 bg-stone-50 px-2 py-1 text-[10px] text-stone-700">白色实体墙 {confirmedWallCount}</span>}
          {reviewWallCount > 0 && <span className="rounded-full border border-amber-300 bg-amber-50 px-2 py-1 text-[10px] font-semibold text-amber-800">局部审计线索 {reviewWallCount}（已由全局墙壳接管，不重复渲染）</span>}
          <Button size="sm" variant="outline" data-testid="whole-home-perspective-overhead" onClick={overhead}><View />普通俯视</Button>
          <Button size="sm" variant={viewerMode === "orthographic-audit" ? "default" : "outline"} data-testid="whole-home-orthographic-audit" onClick={auditOverhead}><View />正交审计俯视</Button>
          <Button size="sm" variant="outline" data-testid="whole-home-download-audit-png" disabled={!viewerReady} onClick={downloadAuditPng}><Download />下载审计 PNG</Button>
          <Button size="sm" variant="outline" onClick={enterHome}><Move3d />进入室内</Button>
          <span className="text-[10px] font-semibold text-muted-foreground" data-testid="whole-home-view-status">{viewerMode === "orthographic-audit" ? "ORTHOGRAPHIC · STRUCTURE_ONLY · CANONICAL · 5% PAD" : "PERSPECTIVE · INTERACTIVE"}</span>
        </div>
        <div ref={mountRef} className="bg-[#e9e6df]" data-testid="whole-home-3d-canvas-host" />
        <div className="grid grid-cols-[180px_1fr_auto] items-end gap-3 border-t border-border p-3 max-[720px]:grid-cols-1">
          <label className="text-xs font-semibold">镜头焦距 {focal}mm<input className="mt-1 w-full accent-primary" type="range" min={14} max={50} step={1} value={focal} onChange={(event) => setFocal(Number(event.target.value))} /></label>
          <label className="text-xs font-semibold">机位名称<Input value={cameraName} onChange={(event) => setCameraName(event.target.value)} /></label>
          <Button disabled={!viewerReady || !verified || busy || autoRunning || viewerMode !== "perspective"} onClick={saveCamera}>{busy || autoRunning ? <Rotate3d className="animate-spin" /> : <Camera />}{verified ? "手动保存机位（高级纠偏）" : "先锁定整屋几何"}</Button>
          <Button disabled={!viewerReady || !verified || busy || autoRunning || !onSavePanoCapture || viewerMode !== "perspective"} onClick={savePanoCapture} title="以当前相机位置为投影中心渲染六面六通道并保存球面热点">{busy || autoRunning ? <Rotate3d className="animate-spin" /> : <Camera />}保存全景热点（360°）</Button>
        </div>
        {autoStage && <div className="border-t border-border bg-primary/5 px-3 py-2 text-xs text-primary"><b>自动机位：</b>{autoStage}</div>}
        {lastCandidateProposal?.blocked_rooms.length ? <div className="border-t border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800"><b>已阻断房间：</b>{lastCandidateProposal.blocked_rooms.map((room) => `${room.room_label}（${room.reasons.join("；")}）`).join("；")}</div> : null}
        {lastAutoPlan && <div className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">本次记录 {lastAutoPlan.plan_id} · {lastAutoPlan.candidates.length} 个候选 · 固化 {lastAutoPlan.selected_cameras.length} 个主/备用机位 · {lastAutoPlan.ai_model || "本地回退"}{lastAutoPlan.ai_error ? ` · AI 复排异常已保留：${lastAutoPlan.ai_error}` : ""}</div>}
      </div>
    </div>
  );
});
