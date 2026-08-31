"use client";
/* eslint-disable @next/next/no-img-element */

import { useEffect, useMemo, useRef, useState } from "react";
import type { DesignPlanAnchor } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Kind = DesignPlanAnchor["kind"];

export function mapClientPointToNormalized(rect: Pick<DOMRect, "left" | "top" | "width" | "height">, clientX: number, clientY: number) {
  return {
    x: Math.max(0, Math.min(1000, Math.round((clientX - rect.left) / rect.width * 1000))),
    y: Math.max(0, Math.min(1000, Math.round((clientY - rect.top) / rect.height * 1000))),
  };
}

export function FloorplanAnchorEditor({ imageUrl, initial, busy, onSave }: {
  imageUrl: string;
  initial: DesignPlanAnchor[];
  busy: boolean;
  onSave: (anchors: DesignPlanAnchor[], confirmedComplete: boolean) => void;
}) {
  const [anchors, setAnchors] = useState<DesignPlanAnchor[]>(initial);
  const [kind, setKind] = useState<Kind>("space");
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [distanceMm, setDistanceMm] = useState("");
  const [twoPoint, setTwoPoint] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [scale, setScale] = useState(1);
  const [pending, setPending] = useState<string | null>(null);
  const drag = useRef<{ id: string; point: number } | null>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setScale((value) => Math.max(0.7, Math.min(3, value * (event.deltaY < 0 ? 1.1 : 0.9))));
    };
    surface.addEventListener("wheel", wheel, { passive: false });
    return () => surface.removeEventListener("wheel", wheel);
  }, []);

  const nextId = useMemo(() => {
    const used = new Set(anchors.map((a) => a.anchor_id));
    for (let index = 1; index <= 99; index++) {
      const value = `P${String(index).padStart(2, "0")}`;
      if (!used.has(value)) return value;
    }
    return `P${String(anchors.length + 1).padStart(2, "0")}_extra`;
  }, [anchors]);

  function point(event: React.PointerEvent<SVGSVGElement>) {
    return mapClientPointToNormalized(event.currentTarget.getBoundingClientRect(), event.clientX, event.clientY);
  }

  function addPoint(event: React.PointerEvent<SVGSVGElement>) {
    if (drag.current) return;
    const p = point(event);
    if (pending) {
      setAnchors((rows) => rows.map((row) => row.anchor_id === pending ? { ...row, points: [...row.points, p] } : row));
      setPending(null);
      setLabel("");
      setDistanceMm("");
      return;
    }
    if (!label.trim()) return;
    if (kind === "scale" && Number(distanceMm) < 10) return;
    const anchor: DesignPlanAnchor = {
      anchor_id: nextId, kind, label: label.trim(), note: note.trim(), points: [p], source: "human",
      ...(kind === "scale" ? { distance_mm: Number(distanceMm) } : {}),
    };
    setAnchors((rows) => [...rows, anchor]);
    if (kind === "scale" || (twoPoint && (kind === "entrance" || kind === "opening"))) setPending(nextId);
    else setLabel("");
  }

  function movePoint(event: React.PointerEvent<SVGSVGElement>) {
    if (!drag.current) return;
    const p = point(event);
    const target = drag.current;
    setAnchors((rows) => rows.map((row) => row.anchor_id === target.id ? {
      ...row, points: row.points.map((value, index) => index === target.point ? p : value),
    } : row));
  }

  const hasSpace = anchors.some((row) => row.kind === "space");
  const hasEntrance = anchors.some((row) => row.kind === "entrance");
  const hasScale = anchors.some((row) => row.kind === "scale" && Number(row.distance_mm) >= 10);
  const canAdd = kind !== "scale" || Number(distanceMm) >= 10;
  const canSubmit = anchors.length > 0 && hasSpace && hasEntrance && hasScale && !pending;

  return <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_340px]">
    <div className="min-w-0">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <b>人工锚点画布</b><span className="text-muted-foreground">滚轮缩放 · 拖动编号点 · 坐标以规范化证据图左上角为原点</span>
        <Button className="ml-auto" size="sm" variant="outline" onClick={() => setScale(1)}>复位缩放</Button>
      </div>
      <div className="max-h-[72vh] overflow-auto rounded-xl border border-border bg-[#eeeae2] p-2">
        <div ref={surfaceRef} className="relative origin-top-left" style={{ width: `${scale * 100}%` }}>
          <img src={imageUrl} alt="人工标注户型证据图" className="block w-full select-none" draggable={false} />
          <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" className="absolute inset-0 size-full touch-none cursor-crosshair" onPointerDown={addPoint} onPointerMove={movePoint} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
            {anchors.map((anchor) => <g key={anchor.anchor_id}>
              {anchor.points.length === 2 && <line x1={anchor.points[0].x} y1={anchor.points[0].y} x2={anchor.points[1].x} y2={anchor.points[1].y} stroke={anchor.kind === "scale" ? "#d97706" : "#2563eb"} strokeWidth="4" vectorEffect="non-scaling-stroke" />}
              {anchor.points.map((p, index) => <g key={index} transform={`translate(${p.x} ${p.y})`} className="cursor-move" onPointerDown={(event) => { event.stopPropagation(); drag.current = { id: anchor.anchor_id, point: index }; event.currentTarget.setPointerCapture(event.pointerId); }}>
                <circle r="15" fill="white" stroke={anchor.kind === "entrance" ? "#dc2626" : anchor.kind === "space" ? "#0f766e" : anchor.kind === "scale" ? "#d97706" : "#2563eb"} strokeWidth="5" vectorEffect="non-scaling-stroke" />
                <text x="20" y="-16" fontSize="24" fontWeight="700" fill="#111827" stroke="white" strokeWidth="5" paintOrder="stroke">{anchor.anchor_id}{anchor.points.length === 2 ? String.fromCharCode(65 + index) : ""}</text>
              </g>)}
            </g>)}
          </svg>
        </div>
      </div>
    </div>
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-muted/20 p-3">
        <div className="text-xs font-bold">添加锚点</div>
        <select aria-label="锚点类型" value={kind} onChange={(event) => { const next = event.target.value as Kind; setKind(next); if (!(next === "entrance" || next === "opening")) setTwoPoint(false); }} className="mt-2 h-9 w-full rounded-lg border border-border bg-background px-2 text-sm">
          <option value="space">空间中心</option><option value="entrance">入户门</option><option value="scale">比例尺（两点）</option><option value="opening">门窗/开口</option><option value="fixed_feature">固定结构特征</option><option value="ignore">忽略区域</option>
        </select>
        <Input aria-label="人工标签" className="mt-2" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="自由文本，例如：主卧、公卫、节点详图" />
        <Input aria-label="锚点备注" className="mt-2" value={note} onChange={(event) => setNote(event.target.value)} placeholder="可选备注" />
        {kind === "scale" && <Input aria-label="比例尺真实长度毫米" className="mt-2" type="number" min={10} max={1000000} value={distanceMm} onChange={(event) => setDistanceMm(event.target.value)} placeholder="两点之间真实长度，例如 9170 mm" />}
        {(kind === "entrance" || kind === "opening") && <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={twoPoint} onChange={(event) => setTwoPoint(event.target.checked)} />用两点表达方向/长度</label>}
        <div className="mt-2 text-[11px] text-muted-foreground">填写标签后在左图点击。{pending ? `请再点一次完成 ${pending} 的第二点。` : kind === "scale" && !canAdd ? "请先填写真实毫米长度。" : "编号自动生成。"}</div>
      </div>
      <div className="max-h-[48vh] space-y-2 overflow-y-auto pr-1">
        {anchors.map((anchor) => <div key={anchor.anchor_id} className="rounded-xl border border-border bg-background p-2">
          <div className="flex items-center gap-2"><b className="text-xs text-primary">{anchor.anchor_id}</b><span className="text-[10px] text-muted-foreground">{anchor.kind} · {anchor.points.map((p) => `${p.x},${p.y}`).join(" → ")}</span><button className="ml-auto text-xs text-red-600" onClick={() => { setAnchors((rows) => rows.filter((row) => row.anchor_id !== anchor.anchor_id)); if (pending === anchor.anchor_id) setPending(null); }}>删除</button></div>
          <Input className="mt-1 h-8" value={anchor.label} onChange={(event) => setAnchors((rows) => rows.map((row) => row.anchor_id === anchor.anchor_id ? { ...row, label: event.target.value } : row))} />
          {anchor.kind === "scale" && <Input className="mt-1 h-8" type="number" min={10} value={anchor.distance_mm || ""} onChange={(event) => setAnchors((rows) => rows.map((row) => row.anchor_id === anchor.anchor_id ? { ...row, distance_mm: Number(event.target.value) } : row))} />}
        </div>)}
        {!anchors.length && <div className="rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground">尚未添加锚点</div>}
      </div>
      <label className="flex items-start gap-2 rounded-xl border border-border p-3 text-xs"><input className="mt-0.5" type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>我已标完所有空间、入户门和一条真实比例尺；允许 Gemini 自动补充图上明显但未标的结构。</span></label>
      {!hasSpace && <div className="text-xs text-red-700">至少添加一个空间锚点。</div>}
      {!hasEntrance && <div className="text-xs text-red-700">至少添加一个入户门锚点。</div>}
      {!hasScale && <div className="text-xs text-red-700">至少添加一条两点比例尺并填写真实毫米长度。</div>}
      <Button className="w-full" disabled={busy || !canSubmit || !confirmed} onClick={() => onSave(anchors, true)}>{busy ? "正在保存锚点…" : "保存锚点并交给 Gemini 双重识别"}</Button>
    </div>
  </div>;
}
