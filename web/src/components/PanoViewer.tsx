"use client";

// 定点球面全景 Viewer：2:1 ERP 贴到球体内侧，显式 yaw/pitch/FOV 控制。
import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { Maximize2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  PANO_CHECKLIST_ITEMS,
  PANO_VIEW_DEFAULT_FOV_DEG,
  PANO_VIEW_WIDE_FOV_DEG,
  clampPanoFov,
  clampPanoPitch,
  emptyPanoChecklistResult,
  horizontalPanoFovToVertical,
  isEquirectangularSize,
  panoChecklistComplete,
  panoChecklistPassed,
  panoChecklistSequence,
  type PanoChecklistResult,
} from "@/lib/wholeHomePano";
import {
  PURE_RENDER_PANO_REVIEW_ITEMS,
  PURE_RENDER_PANO_REVIEW_LABELS,
  emptyPureRenderPanoChecklist,
  pureRenderPanoChecklistComplete,
  pureRenderPanoChecklistPassed,
  type PureRenderPanoChecklistDraft,
} from "@/lib/pureRenderPano";
import type { PureRenderPanoramaReviewChecklist } from "@/lib/types";

const CHECKLIST_LABELS: Record<string, string> = {
  wall_openings: "墙/开口位置正确",
  duplicates: "无重复物体",
  material_continuity: "材质连续",
  lighting_continuity: "光线连续",
  poles: "天顶/地底正常",
  cross_hotspot_same_object: "与其他热点同一物体一致",
};

type PanoViewerMode = "view" | "review";

interface ViewState {
  yawDeg: number;
  pitchDeg: number;
  fovDeg: number;
}

export default function PanoViewer({
  erpUrl,
  mode,
  initialYawDeg = 0,
  reviewProfile = "whole_home",
  onChecklistResult,
  onPureChecklistResult,
  reviewBlockedReason = "",
}: {
  erpUrl: string;
  mode: PanoViewerMode;
  initialYawDeg?: number;
  reviewProfile?: "whole_home" | "pure_render";
  onChecklistResult?: (result: PanoChecklistResult) => void;
  onPureChecklistResult?: (result: PureRenderPanoramaReviewChecklist) => void;
  reviewBlockedReason?: string;
}) {
  const shellRef = useRef<HTMLDivElement>(null);
  const mountRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const renderRef = useRef<() => void>(() => undefined);
  const draggingRef = useRef(false);
  const pointerIdRef = useRef<number | null>(null);
  const lastPointerRef = useRef({ x: 0, y: 0 });
  const viewRef = useRef<ViewState>({
    yawDeg: initialYawDeg,
    pitchDeg: 0,
    fovDeg: PANO_VIEW_DEFAULT_FOV_DEG,
  });
  const [view, setView] = useState<ViewState>({
    yawDeg: initialYawDeg,
    pitchDeg: 0,
    fovDeg: PANO_VIEW_DEFAULT_FOV_DEG,
  });
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [playState, setPlayState] = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [aspect, setAspect] = useState(16 / 9);
  const [checklist, setChecklist] = useState<PanoChecklistResult>(emptyPanoChecklistResult);
  const [pureChecklist, setPureChecklist] = useState<PureRenderPanoChecklistDraft>(
    emptyPureRenderPanoChecklist,
  );
  const playingRef = useRef(false);

  const applyView = useCallback((yawDeg: number, pitchDeg: number, fovDeg?: number) => {
    const next = {
      yawDeg,
      pitchDeg: clampPanoPitch(pitchDeg),
      fovDeg: clampPanoFov(fovDeg ?? viewRef.current.fovDeg),
    };
    viewRef.current = next;
    const camera = cameraRef.current;
    if (camera) {
      // 相机默认朝 -Z；YXZ 保证 yaw 始终绕世界竖轴，拖动不会积累 roll。
      camera.rotation.set(
        THREE.MathUtils.degToRad(next.pitchDeg),
        THREE.MathUtils.degToRad(next.yawDeg),
        0,
        "YXZ",
      );
      camera.fov = horizontalPanoFovToVertical(next.fovDeg, camera.aspect);
      camera.updateProjectionMatrix();
      renderRef.current();
    }
    setView(next);
  }, []);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    setReady(false);
    setLoadError("");
    setPlayState("");
    setChecklist(emptyPanoChecklistResult());
    setPureChecklist(emptyPureRenderPanoChecklist());
    playingRef.current = false;
    viewRef.current = {
      yawDeg: initialYawDeg, pitchDeg: 0, fovDeg: PANO_VIEW_DEFAULT_FOV_DEG,
    };

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    mount.replaceChildren(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(PANO_VIEW_DEFAULT_FOV_DEG, 1, 0.1, 100);
    camera.position.set(0, 0, 0);
    camera.rotation.order = "YXZ";
    scene.add(camera);

    // 保持既有坐标/UV 约定，避免历史 ERP 的朝向在升级后发生变化。
    const geometry = new THREE.SphereGeometry(50, 64, 40);
    geometry.scale(-1, 1, 1);
    const material = new THREE.MeshBasicMaterial();
    const sphere = new THREE.Mesh(geometry, material);
    scene.add(sphere);

    rendererRef.current = renderer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    renderRef.current = () => renderer.render(scene, camera);

    let lastWidth = 0;
    let lastHeight = 0;
    const resize = () => {
      const bounds = mount.getBoundingClientRect();
      const width = Math.min(8192, Math.max(1, Math.round(bounds.width)));
      const height = Math.min(8192, Math.max(1, Math.round(bounds.height)));
      if (width === lastWidth && height === lastHeight) return;
      lastWidth = width;
      lastHeight = height;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.fov = horizontalPanoFovToVertical(viewRef.current.fovDeg, camera.aspect);
      setAspect(camera.aspect);
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();

    const texture = new THREE.TextureLoader().load(
      erpUrl,
      (loaded) => {
        const image = loaded.image as {
          naturalWidth?: number;
          naturalHeight?: number;
          width?: number;
          height?: number;
        };
        const width = Number(image.naturalWidth || image.width || 0);
        const height = Number(image.naturalHeight || image.height || 0);
        if (!isEquirectangularSize(width, height)) {
          sphere.visible = false;
          setLoadError(`图片 ${width}×${height} 不是可用的 2:1 球面 ERP`);
          renderer.render(scene, camera);
          return;
        }
        loaded.colorSpace = THREE.SRGBColorSpace;
        material.map = loaded;
        material.needsUpdate = true;
        setReady(true);
        applyView(initialYawDeg, 0, PANO_VIEW_DEFAULT_FOV_DEG);
      },
      undefined,
      () => setLoadError("全景图片加载失败，请检查文件是否仍存在"),
    );

    return () => {
      observer.disconnect();
      texture.dispose();
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      mount.replaceChildren();
      rendererRef.current = null;
      sceneRef.current = null;
      cameraRef.current = null;
      renderRef.current = () => undefined;
    };
  }, [applyView, erpUrl, initialYawDeg]);

  useEffect(() => {
    const onFullscreenChange = () => setIsFullscreen(document.fullscreenElement === shellRef.current);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  const playChecklist = useCallback(async () => {
    if (playingRef.current) return;
    playingRef.current = true;
    for (const step of panoChecklistSequence()) {
      if (!playingRef.current) break;
      setPlayState(step.label);
      applyView(step.yawDeg, step.pitchDeg);
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    if (playingRef.current) setPlayState("序列完成");
    playingRef.current = false;
  }, [applyView]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!ready || event.button !== 0) return;
    draggingRef.current = true;
    pointerIdRef.current = event.pointerId;
    lastPointerRef.current = { x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current || pointerIdRef.current !== event.pointerId) return;
    const dx = event.clientX - lastPointerRef.current.x;
    const dy = event.clientY - lastPointerRef.current.y;
    lastPointerRef.current = { x: event.clientX, y: event.clientY };
    applyView(
      viewRef.current.yawDeg - dx * 0.15,
      viewRef.current.pitchDeg - dy * 0.15,
    );
  };

  const stopPointer = (event: React.PointerEvent<HTMLDivElement>) => {
    if (pointerIdRef.current === event.pointerId) {
      draggingRef.current = false;
      pointerIdRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    }
  };

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !ready) return;
    // React/browser wheel delegation may be passive, in which case calling
    // preventDefault() logs an error and the page scrolls behind the dialog.
    // Register directly with passive:false so zoom owns the gesture while the
    // pointer is over the panorama.
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      applyView(
        viewRef.current.yawDeg,
        viewRef.current.pitchDeg,
        viewRef.current.fovDeg + event.deltaY * 0.04,
      );
    };
    mount.addEventListener("wheel", onWheel, { passive: false });
    return () => mount.removeEventListener("wheel", onWheel);
  }, [applyView, ready]);

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === shellRef.current) await document.exitFullscreen();
      else if (shellRef.current?.requestFullscreen) await shellRef.current.requestFullscreen();
      else setLoadError("当前浏览器不支持全屏模式");
    } catch {
      setLoadError("浏览器拒绝进入全屏模式");
    }
  };

  const submit = () => {
    if (reviewProfile === "pure_render") {
      if (!pureRenderPanoChecklistComplete(pureChecklist)) return;
      onPureChecklistResult?.(pureChecklist);
      return;
    }
    if (!panoChecklistComplete(checklist)) return;
    onChecklistResult?.(checklist);
  };

  return (
    <div className="space-y-3">
      <div ref={shellRef} className="relative overflow-hidden rounded-xl bg-black">
        <div
          ref={mountRef}
          className={`${isFullscreen ? "h-screen" : "h-[420px]"} w-full touch-none cursor-grab overflow-hidden border border-border bg-black active:cursor-grabbing`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={stopPointer}
          onPointerCancel={stopPointer}
          onLostPointerCapture={stopPointer}
        />
        <div className="pointer-events-none absolute inset-x-2 top-2 flex justify-end gap-1">
          <Button className="pointer-events-auto bg-black/55 text-white hover:bg-black/75" size="sm"
            variant="ghost" disabled={!ready}
            onClick={() => applyView(viewRef.current.yawDeg, viewRef.current.pitchDeg, PANO_VIEW_DEFAULT_FOV_DEG)}>
            自然 90°（水平）
          </Button>
          <Button className="pointer-events-auto bg-black/55 text-white hover:bg-black/75" size="sm"
            variant="ghost" disabled={!ready}
            onClick={() => applyView(viewRef.current.yawDeg, viewRef.current.pitchDeg, PANO_VIEW_WIDE_FOV_DEG)}>
            广角 105°（水平）
          </Button>
          <Button className="pointer-events-auto bg-black/55 text-white hover:bg-black/75" size="sm"
            variant="ghost" disabled={!ready}
            onClick={() => applyView(initialYawDeg, 0, PANO_VIEW_DEFAULT_FOV_DEG)}>
            <RotateCcw />重置视角
          </Button>
          <Button className="pointer-events-auto bg-black/55 text-white hover:bg-black/75" size="sm"
            variant="ghost" disabled={!ready}
            onClick={() => void toggleFullscreen()}>
            <Maximize2 />{isFullscreen ? "退出全屏" : "全屏"}
          </Button>
        </div>
        <div className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-black/55 px-2 py-1 text-[11px] text-white">
          {loadError || (ready
            ? `拖动环视 · 滚轮缩放 · yaw ${Math.round(view.yawDeg)}° · pitch ${Math.round(view.pitchDeg)}° · 水平 FOV ${Math.round(view.fovDeg)}° · 垂直 ${Math.round(horizontalPanoFovToVertical(view.fovDeg, aspect))}°${view.fovDeg >= 100 ? " · 边缘为超广角拉伸" : ""}`
            : "全景加载中…")}
        </div>
      </div>

      {mode === "review" && (
        <>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{ready ? "已加载 2:1 全景" : loadError || "全景加载中…"}</span>
            {playState && <span className="text-primary">检查序列：{playState}</span>}
            <Button size="sm" variant="outline" disabled={!ready} onClick={() => void playChecklist()}>
              自动播放检查序列
            </Button>
          </div>
          <div className="rounded-xl border border-border p-3">
            <div className="mb-2 text-xs font-bold">
              人工验收（六项，任一“不确定”或“不通过”均不能验收）
            </div>
            {reviewBlockedReason && <div className="mb-2 rounded-lg bg-amber-50 px-2 py-1 text-xs font-bold text-amber-800">{reviewBlockedReason}</div>}
            <div className="grid grid-cols-1 gap-1 max-[720px]:grid-cols-1 sm:grid-cols-2">
              {reviewProfile === "pure_render"
                ? PURE_RENDER_PANO_REVIEW_ITEMS.map((item) => (
                    <div key={item} className="flex items-center justify-between gap-2 rounded-lg border border-border px-2 py-1 text-xs">
                      <span>{PURE_RENDER_PANO_REVIEW_LABELS[item]}</span>
                      <span className="flex gap-1">
                        <Button size="sm" variant={pureChecklist[item] === "pass" ? "default" : "outline"}
                          onClick={() => setPureChecklist({ ...pureChecklist, [item]: "pass" })}>通过</Button>
                        <Button size="sm" variant={pureChecklist[item] === "uncertain" ? "secondary" : "outline"}
                          onClick={() => setPureChecklist({ ...pureChecklist, [item]: "uncertain" })}>不确定</Button>
                        <Button size="sm" variant={pureChecklist[item] === "fail" ? "destructive" : "outline"}
                          onClick={() => setPureChecklist({ ...pureChecklist, [item]: "fail" })}>不通过</Button>
                      </span>
                    </div>
                  ))
                : PANO_CHECKLIST_ITEMS.map((item) => (
                    <div key={item} className="flex items-center justify-between gap-2 rounded-lg border border-border px-2 py-1 text-xs">
                      <span>{CHECKLIST_LABELS[item]}</span>
                      <span className="flex gap-1">
                        <Button size="sm" variant={checklist[item] === "pass" ? "default" : "outline"}
                          onClick={() => setChecklist({ ...checklist, [item]: "pass" })}>通过</Button>
                        <Button size="sm" variant={checklist[item] === "uncertain" ? "destructive" : "outline"}
                          onClick={() => setChecklist({ ...checklist, [item]: "uncertain" })}>不确定</Button>
                      </span>
                    </div>
                  ))}
            </div>
            <Button className="mt-2 w-full" disabled={reviewProfile === "pure_render"
              ? !onPureChecklistResult || !pureRenderPanoChecklistComplete(pureChecklist)
              : !onChecklistResult || !panoChecklistComplete(checklist)}
              onClick={submit}>
              {reviewProfile === "pure_render"
                ? pureRenderPanoChecklistPassed(pureChecklist)
                  ? "提交验收：全部通过"
                  : "提交复核结果（不能标记为已验收）"
                : panoChecklistPassed(checklist)
                  ? "提交验收：全部通过"
                  : "提交验收（含不确定，按失败）"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
