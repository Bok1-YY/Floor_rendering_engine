"use client";

// Historical ERP viewer only. Production, repair and review actions were retired.
import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { Maximize2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value));
}

export default function PanoViewer({ erpUrl, initialYawDeg = 0 }: {
  erpUrl: string;
  initialYawDeg?: number;
}) {
  const shellRef = useRef<HTMLDivElement>(null);
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({ yaw: initialYawDeg, pitch: 0, fov: 78 });
  const pointerRef = useRef<{ id: number; x: number; y: number } | null>(null);
  const renderRef = useRef<() => void>(() => undefined);
  const [view, setView] = useState({ yaw: initialYawDeg, pitch: 0, fov: 78 });

  const applyView = useCallback((next: { yaw: number; pitch: number; fov: number }) => {
    stateRef.current = { yaw: next.yaw, pitch: clamp(next.pitch, -85, 85), fov: clamp(next.fov, 35, 105) };
    setView(stateRef.current);
    renderRef.current();
  }, []);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(mount.clientWidth || 800, mount.clientHeight || 500);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(78, 1, 0.1, 10);
    const geometry = new THREE.SphereGeometry(5, 64, 40);
    geometry.scale(-1, 1, 1);
    const texture = new THREE.TextureLoader().load(erpUrl, () => renderRef.current());
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.MeshBasicMaterial({ map: texture });
    scene.add(new THREE.Mesh(geometry, material));
    const render = () => {
      const current = stateRef.current;
      camera.fov = current.fov;
      camera.aspect = Math.max(1, mount.clientWidth) / Math.max(1, mount.clientHeight);
      camera.updateProjectionMatrix();
      const phi = THREE.MathUtils.degToRad(90 - current.pitch);
      const theta = THREE.MathUtils.degToRad(current.yaw);
      camera.lookAt(
        Math.sin(phi) * Math.cos(theta),
        Math.cos(phi),
        Math.sin(phi) * Math.sin(theta),
      );
      renderer.setSize(mount.clientWidth || 800, mount.clientHeight || 500, false);
      renderer.render(scene, camera);
    };
    renderRef.current = render;
    const observer = new ResizeObserver(render);
    observer.observe(mount);
    render();
    return () => {
      observer.disconnect();
      texture.dispose();
      material.dispose();
      geometry.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      renderRef.current = () => undefined;
    };
  }, [erpUrl]);

  return <div ref={shellRef} className="relative h-[min(70vh,680px)] min-h-[420px] w-full overflow-hidden rounded-xl bg-black">
    <div
      ref={mountRef}
      className="size-full cursor-grab active:cursor-grabbing"
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        pointerRef.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
      }}
      onPointerMove={(event) => {
        const previous = pointerRef.current;
        if (!previous || previous.id !== event.pointerId) return;
        const dx = event.clientX - previous.x;
        const dy = event.clientY - previous.y;
        pointerRef.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
        applyView({ ...stateRef.current, yaw: stateRef.current.yaw - dx * 0.16, pitch: stateRef.current.pitch + dy * 0.13 });
      }}
      onPointerUp={() => { pointerRef.current = null; }}
      onPointerCancel={() => { pointerRef.current = null; }}
      onWheel={(event) => {
        event.preventDefault();
        applyView({ ...stateRef.current, fov: stateRef.current.fov + Math.sign(event.deltaY) * 4 });
      }}
    />
    <div className="absolute left-3 top-3 rounded-full bg-black/65 px-3 py-1.5 text-[11px] font-bold text-white">历史 360° 只读查看</div>
    <div className="absolute bottom-3 right-3 flex gap-2">
      <Button size="sm" variant="secondary" onClick={() => applyView({ yaw: initialYawDeg, pitch: 0, fov: 78 })}><RotateCcw />复位</Button>
      <Button size="sm" variant="secondary" onClick={() => shellRef.current?.requestFullscreen()}><Maximize2 />全屏</Button>
    </div>
    <div className="pointer-events-none absolute bottom-3 left-3 rounded-full bg-black/60 px-3 py-1 text-[10px] text-white/80">yaw {view.yaw.toFixed(0)}° · pitch {view.pitch.toFixed(0)}° · FOV {view.fov.toFixed(0)}°</div>
  </div>;
}
