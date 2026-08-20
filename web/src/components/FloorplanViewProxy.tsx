"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { CheckCircle2, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type {
  FloorplanCamera,
  FloorplanOpening,
  FloorplanRoom,
  FloorplanSpatialPlan,
  FloorplanViewProxy,
  NormalizedPoint,
} from "@/lib/types";

type AspectRatio = "4:3" | "16:9" | "3:4" | "9:16";

const ASPECTS: Record<AspectRatio, number> = {
  "4:3": 4 / 3,
  "16:9": 16 / 9,
  "3:4": 3 / 4,
  "9:16": 9 / 16,
};

function furnitureSize(name: string): [number, number, number] {
  const value = name.toLowerCase();
  if (/sofa|沙发/.test(value)) return [2.2, 0.82, 0.92];
  if (/dining.*table|餐桌/.test(value)) return [1.6, 0.76, 0.9];
  if (/coffee.*table|茶几/.test(value)) return [1.1, 0.42, 0.65];
  if (/tv|电视/.test(value)) return [1.8, 0.55, 0.42];
  if (/fridge|refrigerator|冰箱/.test(value)) return [0.8, 1.85, 0.72];
  if (/bed|床/.test(value)) return [2.0, 0.55, 1.65];
  if (/cabinet|storage|柜/.test(value)) return [1.5, 1.8, 0.48];
  if (/tatami|platform|榻榻米|地台/.test(value)) return [2.8, 0.34, 2.0];
  return [1.0, 0.78, 0.72];
}

function orientationRadians(value = "") {
  const text = value.toLowerCase();
  if (/east|东/.test(text)) return Math.PI / 2;
  if (/south|南/.test(text)) return Math.PI;
  if (/west|西/.test(text)) return -Math.PI / 2;
  return 0;
}

function nearestEdge(opening: FloorplanOpening, points: THREE.Vector2[]) {
  const midpoint = new THREE.Vector2(
    (opening.points[0].x + opening.points[1].x) / 2,
    (opening.points[0].y + opening.points[1].y) / 2,
  );
  let result = { index: -1, distance: Number.POSITIVE_INFINITY };
  points.forEach((start, index) => {
    const end = points[(index + 1) % points.length];
    const edge = end.clone().sub(start);
    const lengthSq = Math.max(edge.lengthSq(), 1e-9);
    const t = Math.max(0, Math.min(1, midpoint.clone().sub(start).dot(edge) / lengthSq));
    const distance = midpoint.distanceTo(start.clone().add(edge.multiplyScalar(t)));
    if (distance < result.distance) result = { index, distance };
  });
  return result.index;
}

export function FloorplanViewProxy({
  room,
  camera,
  plan,
  openings,
  sourceSize,
  aspectRatio,
  proxy,
  busy,
  onConfirm,
  onDirtyChange,
}: {
  room: FloorplanRoom;
  camera: FloorplanCamera;
  plan: FloorplanSpatialPlan;
  openings: FloorplanOpening[];
  sourceSize?: { width?: number; height?: number };
  aspectRatio: AspectRatio;
  proxy?: FloorplanViewProxy;
  busy: boolean;
  onConfirm: (imageDataUrl: string, config: Record<string, number | string>) => Promise<void>;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const reportedDirtyRef = useRef<boolean | null>(null);
  const [ready, setReady] = useState(false);
  const [height, setHeight] = useState(proxy?.render_config.camera_height_m ?? camera.height_m ?? 1.55);
  const [focal, setFocal] = useState(proxy?.render_config.focal_length_mm ?? camera.focal_length_mm ?? 24);
  const sourceAspect = Math.max(0.2, Math.min(5, (sourceSize?.width || 1) / (sourceSize?.height || 1)));
  const acceptedOpenings = useMemo(
    () => openings.filter((opening) => opening.review_status === "accepted" && opening.room_ids.includes(room.id)),
    [openings, room.id],
  );
  const matchesSaved = Boolean(
    proxy?.status === "confirmed"
    && proxy.aspect_ratio === aspectRatio
    && Math.abs((proxy.render_config.camera_height_m ?? 1.55) - height) < 0.001
    && Math.abs((proxy.render_config.focal_length_mm ?? 24) - focal) < 0.001,
  );

  useEffect(() => {
    const dirty = !matchesSaved;
    if (reportedDirtyRef.current === dirty) return;
    reportedDirtyRef.current = dirty;
    onDirtyChange?.(dirty);
  }, [matchesSaved, onDirtyChange]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    setReady(false);
    const targetAspect = ASPECTS[aspectRatio];
    const pixelWidth = targetAspect >= 1 ? 1280 : Math.round(1280 * targetAspect);
    const pixelHeight = targetAspect >= 1 ? Math.round(1280 / targetAspect) : 1280;
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, alpha: false });
    renderer.setPixelRatio(1);
    renderer.setSize(pixelWidth, pixelHeight, false);
    renderer.setClearColor(0xe9e6df, 1);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "auto";
    renderer.domElement.style.display = "block";
    mount.replaceChildren(renderer.domElement);
    rendererRef.current = renderer;

    const scene = new THREE.Scene();
    const roomPoints = room.polygon.map((point) => new THREE.Vector2(point.x * sourceAspect, point.y));
    const minX = Math.min(...roomPoints.map((point) => point.x));
    const maxX = Math.max(...roomPoints.map((point) => point.x));
    const minZ = Math.min(...roomPoints.map((point) => point.y));
    const maxZ = Math.max(...roomPoints.map((point) => point.y));
    const centerX = (minX + maxX) / 2;
    const centerZ = (minZ + maxZ) / 2;
    const scale = 8 / Math.max(maxX - minX, maxZ - minZ, 0.05);
    const worldPoint = (point: NormalizedPoint) => new THREE.Vector2(
      (point.x * sourceAspect - centerX) * scale,
      (point.y - centerZ) * scale,
    );
    const worldPolygon = room.polygon.map(worldPoint);

    const clay = new THREE.MeshStandardMaterial({ color: 0xd8d4cb, roughness: 0.94, metalness: 0 });
    const floorMaterial = new THREE.MeshStandardMaterial({ color: 0xc8c3b8, roughness: 1, side: THREE.DoubleSide });
    const shape = new THREE.Shape();
    worldPolygon.forEach((point, index) => index ? shape.lineTo(point.x, point.y) : shape.moveTo(point.x, point.y));
    shape.closePath();
    const floor = new THREE.Mesh(new THREE.ShapeGeometry(shape), floorMaterial);
    floor.rotation.x = Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);

    const wallHeight = 2.8;
    const wallThickness = 0.12;
    const addWallPart = (start: THREE.Vector2, end: THREE.Vector2, from: number, to: number, bottom: number, partHeight: number) => {
      if (to - from < 0.025 || partHeight < 0.025) return;
      const edge = end.clone().sub(start);
      const edgeLength = edge.length();
      const direction = edge.clone().normalize();
      const middle = start.clone().add(direction.multiplyScalar((from + to) / 2));
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(to - from, partHeight, wallThickness),
        clay,
      );
      mesh.position.set(middle.x, bottom + partHeight / 2, middle.y);
      mesh.rotation.y = -Math.atan2(edge.y, edge.x);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      scene.add(mesh);
      void edgeLength;
    };

    worldPolygon.forEach((start, edgeIndex) => {
      const end = worldPolygon[(edgeIndex + 1) % worldPolygon.length];
      const edgeLength = start.distanceTo(end);
      const edgeOpenings = acceptedOpenings.flatMap((opening) => {
        if (nearestEdge(opening, roomPoints) !== edgeIndex) return [];
        const originalStart = roomPoints[edgeIndex];
        const originalEnd = roomPoints[(edgeIndex + 1) % roomPoints.length];
        const originalEdge = originalEnd.clone().sub(originalStart);
        const project = (point: NormalizedPoint) => {
          const value = new THREE.Vector2(point.x * sourceAspect, point.y).sub(originalStart);
          return Math.max(0, Math.min(1, value.dot(originalEdge) / Math.max(originalEdge.lengthSq(), 1e-9))) * edgeLength;
        };
        const a = project(opening.points[0]);
        const b = project(opening.points[1]);
        return [{ opening, from: Math.min(a, b), to: Math.max(a, b) }];
      }).sort((left, right) => left.from - right.from);
      let cursor = 0;
      edgeOpenings.forEach(({ opening, from, to }) => {
        addWallPart(start, end, cursor, from, 0, wallHeight);
        if (opening.kind === "window") {
          addWallPart(start, end, from, to, 0, 0.9);
          addWallPart(start, end, from, to, 2.1, wallHeight - 2.1);
        } else {
          addWallPart(start, end, from, to, 2.1, wallHeight - 2.1);
        }
        cursor = Math.max(cursor, to);
      });
      addWallPart(start, end, cursor, edgeLength, 0, wallHeight);
    });

    (plan.furniture || []).filter((item) => item.required_visible).forEach((item) => {
      const [width, itemHeight, depth] = furnitureSize(item.item || "");
      const position = worldPoint(item.plan_position);
      const material = /tatami|platform|榻榻米|地台/i.test(item.item || "")
        ? new THREE.MeshStandardMaterial({ color: 0xb8b2a6, roughness: 1 })
        : clay;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, itemHeight, depth), material);
      mesh.position.set(position.x, itemHeight / 2, position.y);
      mesh.rotation.y = orientationRadians(item.orientation);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      scene.add(mesh);
    });

    scene.add(new THREE.HemisphereLight(0xffffff, 0x8d887f, 2.2));
    const sunlight = new THREE.DirectionalLight(0xffffff, 2.8);
    sunlight.position.set(-5, 8, -3);
    sunlight.castShadow = true;
    sunlight.shadow.mapSize.set(2048, 2048);
    scene.add(sunlight);

    const fov = THREE.MathUtils.radToDeg(2 * Math.atan(24 / (2 * focal)));
    const viewCamera = new THREE.PerspectiveCamera(fov, targetAspect, 0.03, 100);
    const cameraPosition = worldPoint(camera.position);
    const cameraTarget = worldPoint(camera.target);
    viewCamera.position.set(cameraPosition.x, height, cameraPosition.y);
    viewCamera.lookAt(cameraTarget.x, Math.min(1.25, height * 0.78), cameraTarget.y);
    renderer.render(scene, viewCamera);
    setReady(true);

    return () => {
      rendererRef.current = null;
      renderer.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          const materials = Array.isArray(object.material) ? object.material : [object.material];
          materials.forEach((material) => material.dispose());
        }
      });
      mount.replaceChildren();
    };
  }, [acceptedOpenings, aspectRatio, camera.position, camera.target, focal, height, plan.furniture, room.polygon, sourceAspect]);

  async function confirm() {
    const renderer = rendererRef.current;
    if (!renderer) return;
    await onConfirm(renderer.domElement.toDataURL("image/png"), {
      camera_height_m: height,
      focal_length_mm: focal,
      wall_height_m: 2.8,
      room_long_side_m: 8,
      renderer: "threejs-clay-v1",
    });
  }

  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-lg border border-border bg-[#e9e6df]" ref={mountRef} />
      <div className="grid grid-cols-2 gap-3 text-xs">
        <label className="space-y-1">
          <span className="flex justify-between"><b>相机高度</b><span>{height.toFixed(2)} m</span></span>
          <input className="w-full accent-primary" type="range" min="0.9" max="2.1" step="0.05" value={height} onChange={(event) => setHeight(Number(event.target.value))} />
        </label>
        <label className="space-y-1">
          <span className="flex justify-between"><b>镜头焦距</b><span>{Math.round(focal)} mm</span></span>
          <input className="w-full accent-primary" type="range" min="16" max="50" step="1" value={focal} onChange={(event) => setFocal(Number(event.target.value))} />
        </label>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className={`text-xs ${matchesSaved ? "text-emerald-700" : "text-amber-700"}`}>
          {matchesSaved ? "此画幅与机位参数已确认" : "当前预览尚未确认，不能用于正式出图"}
        </span>
        <Button size="sm" disabled={!ready || busy || matchesSaved} onClick={confirm}>
          {busy ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}
          {matchesSaved ? "灰模已确认" : "确认此机位灰模"}
        </Button>
      </div>
    </div>
  );
}
