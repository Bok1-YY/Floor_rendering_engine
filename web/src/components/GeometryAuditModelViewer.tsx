"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { Download, Move3d, View } from "lucide-react";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { Button } from "@/components/ui/button";
import {
  createOrthographicCameraContractV2,
  ORTHOGRAPHIC_AUDIT_EXPORT_SIZE,
  type OrthographicAuditBounds,
} from "@/lib/wholeHomeOrthographicAudit";

interface Props {
  src: string;
  label: string;
}

type ViewMode = "perspective" | "orthographic-audit";

interface ViewerRuntime {
  scene: THREE.Scene;
  renderer: THREE.WebGLRenderer;
  perspective: THREE.PerspectiveCamera;
  orthographic: THREE.OrthographicCamera;
  controls: OrbitControls<THREE.Camera>;
  grid: THREE.GridHelper | null;
  bounds: OrthographicAuditBounds | null;
  mode: ViewMode;
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    mesh.geometry?.dispose();
    if (Array.isArray(mesh.material)) mesh.material.forEach((material) => material.dispose());
    else mesh.material?.dispose();
  });
}

function applyOrthographicFrame(
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

function safeFilename(label: string) {
  const name = label.trim().replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, "-");
  return `${name || "geometry-audit"}-orthographic-1600.png`;
}

export function GeometryAuditModelViewer({ src, label }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<ViewerRuntime | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [viewMode, setViewMode] = useState<ViewMode>("perspective");
  const [message, setMessage] = useState("正在加载独立 3D 灰模…");

  const activateView = (mode: ViewMode) => {
    const runtime = runtimeRef.current;
    if (!runtime?.bounds) return;
    runtime.mode = mode;
    if (runtime.grid) runtime.grid.visible = mode === "perspective";
    if (mode === "orthographic-audit") {
      const rect = runtime.renderer.domElement.getBoundingClientRect();
      applyOrthographicFrame(runtime.orthographic, runtime.bounds, Math.max(1, rect.width) / Math.max(1, rect.height));
      runtime.controls.object = runtime.orthographic;
      runtime.controls.target.set(
        (runtime.bounds.minX + runtime.bounds.maxX) / 2,
        (runtime.bounds.minY + runtime.bounds.maxY) / 2,
        (runtime.bounds.minZ + runtime.bounds.maxZ) / 2,
      );
      // Audit framing is intentionally locked. Re-clicking the button always
      // yields the same pixels and cannot inherit a user's orbit/pan state.
      runtime.controls.enabled = false;
      setMessage("正交审计俯视 · 固定 CAD 朝向 · 结构灰模 · 四边 5% 留白");
    } else {
      runtime.controls.object = runtime.perspective;
      runtime.controls.enabled = true;
      runtime.controls.enableRotate = true;
      setMessage("透视查看 · 鼠标左键旋转 · 右键平移 · 滚轮缩放");
    }
    runtime.controls.update();
    runtime.renderer.render(
      runtime.scene,
      mode === "orthographic-audit" ? runtime.orthographic : runtime.perspective,
    );
    setViewMode(mode);
  };

  const downloadAuditPng = () => {
    const runtime = runtimeRef.current;
    if (!runtime?.bounds) return;
    const renderer = runtime.renderer;
    const previousSize = renderer.getSize(new THREE.Vector2());
    const previousPixelRatio = renderer.getPixelRatio();
    const previousGridVisibility = runtime.grid?.visible;
    renderer.setPixelRatio(1);
    renderer.setSize(ORTHOGRAPHIC_AUDIT_EXPORT_SIZE, ORTHOGRAPHIC_AUDIT_EXPORT_SIZE, false);
    if (runtime.grid) runtime.grid.visible = false;
    applyOrthographicFrame(runtime.orthographic, runtime.bounds, 1);
    renderer.render(runtime.scene, runtime.orthographic);
    const link = document.createElement("a");
    link.href = renderer.domElement.toDataURL("image/png");
    link.download = safeFilename(label);
    link.click();
    renderer.setPixelRatio(previousPixelRatio);
    renderer.setSize(previousSize.x, previousSize.y, false);
    if (runtime.grid) runtime.grid.visible = previousGridVisibility ?? true;
    if (runtime.mode === "orthographic-audit") {
      applyOrthographicFrame(runtime.orthographic, runtime.bounds, Math.max(1, previousSize.x) / Math.max(1, previousSize.y));
    }
    renderer.render(runtime.scene, runtime.mode === "orthographic-audit" ? runtime.orthographic : runtime.perspective);
  };

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !src) return;
    const abort = new AbortController();
    let disposed = false;
    let animationFrame = 0;
    setState("loading");
    setViewMode("perspective");
    setMessage("正在加载独立 3D 灰模…");

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf1eee8);
    const perspective = new THREE.PerspectiveCamera(42, 1, 0.01, 10_000);
    const orthographic = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 10_000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.domElement.setAttribute("aria-label", `${label} 可交互 3D 灰模`);
    renderer.domElement.setAttribute("data-testid", "geometry-audit-canvas");
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.style.display = "block";
    host.replaceChildren(renderer.domElement);

    const controls = new OrbitControls<THREE.Camera>(perspective, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.minDistance = 0.05;
    controls.maxDistance = 10_000;

    const runtime: ViewerRuntime = {
      scene,
      renderer,
      perspective,
      orthographic,
      controls,
      grid: null,
      bounds: null,
      mode: "perspective",
    };
    runtimeRef.current = runtime;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x8c8174, 2.1));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.5);
    keyLight.position.set(8, 14, 10);
    keyLight.castShadow = true;
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0xbcd7ff, 1.1);
    fillLight.position.set(-10, 6, -8);
    scene.add(fillLight);

    const resize = () => {
      const bounds = host.getBoundingClientRect();
      const width = Math.max(1, Math.floor(bounds.width));
      const height = Math.max(1, Math.floor(bounds.height));
      perspective.aspect = width / height;
      perspective.updateProjectionMatrix();
      if (runtime.bounds) applyOrthographicFrame(orthographic, runtime.bounds, width / height);
      renderer.setSize(width, height, false);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    const render = () => {
      if (disposed) return;
      controls.update();
      renderer.render(scene, runtime.mode === "orthographic-audit" ? orthographic : perspective);
      animationFrame = window.requestAnimationFrame(render);
    };
    render();

    void fetch(src, { signal: abort.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((contents) => {
        if (disposed) return;
        const object = new OBJLoader().parse(contents);
        const clay = new THREE.MeshStandardMaterial({
          color: 0xd8d2c8,
          roughness: 0.82,
          metalness: 0.02,
          side: THREE.DoubleSide,
        });
        object.traverse((child) => {
          const mesh = child as THREE.Mesh;
          if (!mesh.isMesh) return;
          const previous = mesh.material;
          if (Array.isArray(previous)) previous.forEach((material) => material.dispose());
          else previous?.dispose();
          mesh.material = clay;
          mesh.castShadow = true;
          mesh.receiveShadow = true;
          mesh.userData.kind = "wall";
        });
        // The locked IFC/DXF audit exports use CAD Z-up coordinates. Three.js
        // uses Y-up, so normalize the archived OBJ before framing the model.
        object.rotation.x = -Math.PI / 2;
        object.updateMatrixWorld(true);
        const initialBounds = new THREE.Box3().setFromObject(object);
        if (initialBounds.isEmpty()) throw new Error("OBJ 不包含可显示网格");
        const center = initialBounds.getCenter(new THREE.Vector3());
        object.position.sub(center);
        object.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(object);
        const size = bounds.getSize(new THREE.Vector3());
        runtime.bounds = {
          minX: bounds.min.x,
          maxX: bounds.max.x,
          minY: bounds.min.y,
          maxY: bounds.max.y,
          minZ: bounds.min.z,
          maxZ: bounds.max.z,
        };
        scene.add(object);

        const span = Math.max(size.x, size.y, size.z, 0.1);
        const floorY = bounds.min.y;
        const grid = new THREE.GridHelper(span * 1.8, 20, 0x9b9082, 0xc8c0b5);
        grid.position.y = floorY - span * 0.002;
        scene.add(grid);
        runtime.grid = grid;
        perspective.near = Math.max(span / 10_000, 0.005);
        perspective.far = Math.max(span * 20, 100);
        perspective.position.set(span * 1.15, span * 0.85, span * 1.15);
        controls.target.set(0, Math.max(0, floorY + size.y * 0.35), 0);
        controls.minDistance = Math.max(span * 0.08, 0.02);
        controls.maxDistance = span * 8;
        perspective.updateProjectionMatrix();
        applyOrthographicFrame(orthographic, runtime.bounds, Math.max(1, host.clientWidth) / Math.max(1, host.clientHeight));
        controls.update();
        setState("ready");
        setMessage("透视查看 · 鼠标左键旋转 · 右键平移 · 滚轮缩放");
      })
      .catch((error: unknown) => {
        if (disposed || abort.signal.aborted) return;
        setState("failed");
        setMessage(`灰模加载失败：${error instanceof Error ? error.message : String(error)}`);
      });

    return () => {
      disposed = true;
      abort.abort();
      window.cancelAnimationFrame(animationFrame);
      observer.disconnect();
      controls.dispose();
      disposeObject(scene);
      renderer.dispose();
      renderer.domElement.remove();
      if (runtimeRef.current === runtime) runtimeRef.current = null;
    };
  }, [label, src]);

  return (
    <div
      className="overflow-hidden rounded-xl border border-border bg-muted"
      data-testid="geometry-audit-model-viewer"
      data-load-state={state}
      data-view-mode={viewMode}
      data-structure-only={viewMode === "orthographic-audit" ? "true" : "false"}
      data-audit-frame-state={viewMode === "orthographic-audit" ? "canonical" : "inactive"}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card px-3 py-2">
        <Button
          size="sm"
          variant={viewMode === "perspective" ? "default" : "outline"}
          disabled={state !== "ready"}
          data-testid="geometry-audit-perspective-button"
          onClick={() => activateView("perspective")}
        ><Move3d />透视查看</Button>
        <Button
          size="sm"
          variant={viewMode === "orthographic-audit" ? "default" : "outline"}
          disabled={state !== "ready"}
          data-testid="geometry-audit-orthographic-button"
          onClick={() => activateView("orthographic-audit")}
        ><View />正交审计俯视</Button>
        <Button
          size="sm"
          variant="outline"
          disabled={state !== "ready"}
          data-testid="geometry-audit-download-png"
          onClick={downloadAuditPng}
        ><Download />下载 1600×1600 PNG</Button>
        <span className="ml-auto text-[10px] font-semibold text-muted-foreground" data-testid="geometry-audit-view-status">
          {viewMode === "orthographic-audit" ? "ORTHOGRAPHIC · STRUCTURE_ONLY · CANONICAL" : "PERSPECTIVE · INTERACTIVE"}
        </span>
      </div>
      <div ref={hostRef} className="h-[min(54vh,520px)] min-h-80 w-full" />
      <div className={`border-t border-border px-3 py-2 text-xs ${state === "failed" ? "text-destructive" : "text-muted-foreground"}`}>
        {message}
      </div>
    </div>
  );
}
