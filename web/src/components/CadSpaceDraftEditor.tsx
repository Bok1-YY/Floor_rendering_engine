"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { GitMerge, LoaderCircle, MousePointer2, PencilLine, RefreshCw, Save, SquareDashed, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type {
  MetricXZ,
  WholeHomeCadPhysicalSpace,
  WholeHomeCadSemanticZone,
  WholeHomeCadSpaceDraft,
  WholeHomeProject,
} from "@/lib/types";
import {
  cadAnchorPoint,
  cadDraftBounds,
  cadFaceId,
  cadFacePolygon,
  cadSpaceColor,
  cadZonePoints,
  buildCadSpaceDraftPut,
  describeCadApiError,
  mergePhysicalSpaces,
  newCadOperationId,
  retainCadFace,
  updateSpaceSelection,
} from "@/lib/wholeHomeCadSpace";

type DrawTool = "inspect" | "rectangle" | "split_halfplane";

const PHYSICAL_SPACE_TYPES = [
  ["enclosed_room", "封闭房间"], ["open_plan", "开放空间"], ["circulation", "交通空间"],
  ["wet_suite", "湿区套间"], ["balcony", "阳台"], ["service", "设备 / 服务空间"], ["other", "其他"],
] as const;

const SEMANTIC_ZONE_TYPES = [
  ["living_room", "客厅"], ["dining_room", "餐厅"], ["foyer", "玄关"], ["kitchen", "厨房"],
  ["bedroom", "卧室"], ["primary_bedroom", "主卧"], ["secondary_bedroom", "次卧"], ["bathroom", "卫浴"],
  ["balcony", "阳台"], ["circulation", "交通"], ["storage", "储物"], ["utility", "家政 / 设备"], ["other", "其他"],
] as const;

function editableSnapshot(draft: WholeHomeCadSpaceDraft | null) {
  return draft ? JSON.stringify({
    physical_spaces: draft.physical_spaces,
    semantic_zones: draft.semantic_zones,
    excluded_face_ids: draft.excluded_face_ids,
  }) : "";
}

function polygonPoints(points: MetricXZ[]) {
  return points.map((point) => `${point.x},${point.z}`).join(" ");
}

function pointDistance(a: MetricXZ, b: MetricXZ) {
  return Math.hypot(a.x - b.x, a.z - b.z);
}

function physicalSpaceTypeOptions() {
  return PHYSICAL_SPACE_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>);
}

function semanticZoneTypeOptions() {
  return SEMANTIC_ZONE_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>);
}

function initialSemanticType(spaceType: string) {
  if (spaceType === "wet_suite") return "bathroom";
  if (spaceType === "balcony") return "balcony";
  if (spaceType === "circulation") return "circulation";
  if (spaceType === "service") return "utility";
  return "other";
}

export function CadSpaceDraftEditor({ project, onSaved }: {
  project: WholeHomeProject;
  onSaved: (revision: number) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState<WholeHomeCadSpaceDraft | null>(null);
  const [baseline, setBaseline] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [activeSpaceId, setActiveSpaceId] = useState("");
  const [mergeIds, setMergeIds] = useState<string[]>([]);
  const [tool, setTool] = useState<DrawTool>("inspect");
  const [drawStart, setDrawStart] = useState<MetricXZ | null>(null);
  const [drawCurrent, setDrawCurrent] = useState<MetricXZ | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const loadSequence = useRef(0);

  const loadDraft = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError("");
    try {
      const value = await api.getWholeHomeCadSpaceDraft(project.project_id);
      if (sequence !== loadSequence.current) return;
      setDraft(value);
      setBaseline(editableSnapshot(value));
      setActiveSpaceId((current) => value.physical_spaces.some((space) => space.id === current)
        ? current
        : value.physical_spaces.find((space) => space.selected)?.id || value.physical_spaces[0]?.id || "");
      setMergeIds([]);
    } catch (caught) {
      if (sequence === loadSequence.current) setError(describeCadApiError(caught));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [project.project_id]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadDraft(); }, 0);
    return () => { window.clearTimeout(timer); loadSequence.current += 1; };
  }, [loadDraft, project.revision]);

  const bounds = useMemo(() => draft ? cadDraftBounds(draft) : null, [draft]);
  const dirty = editableSnapshot(draft) !== baseline;
  const activeSpace = draft?.physical_spaces.find((space) => space.id === activeSpaceId) || null;
  const excludedFaces = useMemo(() => new Set(draft?.excluded_face_ids || []), [draft?.excluded_face_ids]);

  function svgPoint(event: ReactPointerEvent<SVGSVGElement>): MetricXZ | null {
    const svg = svgRef.current;
    if (!svg) return null;
    const matrix = svg.getScreenCTM();
    if (!matrix) return null;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(matrix.inverse());
    const z = bounds ? bounds.minZ + bounds.maxZ - local.y : local.y;
    return { x: Number(local.x.toFixed(4)), z: Number(z.toFixed(4)) };
  }

  function beginDraw(event: ReactPointerEvent<SVGSVGElement>) {
    if (tool === "inspect") return;
    if (!activeSpace) {
      setError("请先选择一个物理空间，再绘制它的语义分区。");
      return;
    }
    const point = svgPoint(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrawStart(point);
    setDrawCurrent(point);
  }

  function moveDraw(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drawStart) return;
    const point = svgPoint(event);
    if (point) setDrawCurrent(point);
  }

  function finishDraw(event: ReactPointerEvent<SVGSVGElement>) {
    if (!draft || !activeSpace || !drawStart) return;
    const end = svgPoint(event) || drawCurrent;
    setDrawStart(null);
    setDrawCurrent(null);
    if (!end || pointDistance(drawStart, end) < Math.max((bounds?.width || 10) * 0.01, 0.05)) {
      setError("绘制范围太小，未创建语义区。");
      return;
    }
    const operationSuffix = newCadOperationId("zone");
    const previousZone = draft.semantic_zones.find((zone) => zone.physical_space_id === activeSpace.id);
    const defaultZoneType = previousZone?.zone_type || initialSemanticType(activeSpace.space_type);
    const zones: WholeHomeCadSemanticZone[] = tool === "rectangle"
      ? [{
        id: operationSuffix,
        physical_space_id: activeSpace.id,
        label: "矩形语义区",
        zone_type: defaultZoneType,
        geometry: {
        kind: "rectangle",
        min_x: Math.min(drawStart.x, end.x), min_z: Math.min(drawStart.z, end.z),
        max_x: Math.max(drawStart.x, end.x), max_z: Math.max(drawStart.z, end.z),
        },
      }]
      : (["left", "right"] as const).map((side) => ({
        id: `${operationSuffix}-${side}`,
        physical_space_id: activeSpace.id,
        label: previousZone?.label ? `${previousZone.label} · ${side === "left" ? "左" : "右"}` : `分割线${side === "left" ? "左" : "右"}侧`,
        zone_type: defaultZoneType,
        geometry: { kind: "split_halfplane", start: drawStart, end, side },
      }));
    setDraft({
      ...draft,
      semantic_zones: tool === "split_halfplane"
        ? [...draft.semantic_zones.filter((zone) => zone.physical_space_id !== activeSpace.id), ...zones]
        : [...draft.semantic_zones, ...zones],
    });
    setError("");
  }

  function patchSpace(spaceId: string, patch: Partial<WholeHomeCadPhysicalSpace>) {
    if (!draft) return;
    setDraft({ ...draft, physical_spaces: draft.physical_spaces.map((space) => space.id === spaceId ? { ...space, ...patch } : space) });
  }

  function patchZone(zoneId: string, patch: Partial<WholeHomeCadSemanticZone>) {
    if (!draft) return;
    setDraft({ ...draft, semantic_zones: draft.semantic_zones.map((zone) => zone.id === zoneId ? { ...zone, ...patch } : zone) });
  }

  function toggleRawFace(faceId: string) {
    if (!draft || tool !== "inspect") return;
    const owner = draft.physical_spaces.find((space) => space.face_ids.includes(faceId));
    if (owner) {
      setActiveSpaceId(owner.id);
      return;
    }
    const face = draft.raw_faces.find((value, index) => cadFaceId(value, index) === faceId);
    if (face?.manual_eligible !== true) {
      setError("该面因 invalid polygon 或 hole 等硬几何原因不可人工保留；原始证据仍可查看。");
      return;
    }
    const retained = retainCadFace(draft, faceId);
    setDraft(retained);
    setActiveSpaceId(retained.physical_spaces.at(-1)?.id || "");
    setError("");
  }

  function mergeSelectedSpaces() {
    if (!draft || mergeIds.length < 2) return;
    const merged = mergePhysicalSpaces(draft, mergeIds);
    const remaining = merged.physical_spaces.find((space) => mergeIds.includes(space.id));
    setDraft(merged);
    setActiveSpaceId(remaining?.id || "");
    setMergeIds([]);
    setError("");
  }

  async function save() {
    if (!draft || saving || !dirty) return;
    setSaving(true);
    setError("");
    try {
      const result = await api.saveWholeHomeCadSpaceDraft(
        project.project_id,
        buildCadSpaceDraftPut(draft, newCadOperationId("save-space-draft")),
      );
      setBaseline(editableSnapshot(draft));
      toast.success("CAD 空间确认已保存");
      try {
        await onSaved(result.revision);
      } catch (caught) {
        toast.warning(`空间确认已保存，但项目摘要刷新失败：${describeCadApiError(caught)}`);
      }
      await loadDraft();
    } catch (caught) {
      setError(describeCadApiError(caught));
    } finally {
      setSaving(false);
    }
  }

  if (loading && !draft) return <section className="rounded-xl border border-border bg-panel p-4"><div className="flex items-center gap-2 text-sm"><LoaderCircle className="animate-spin text-primary" size={18} />读取 CAD 空间草稿…</div></section>;

  if (!draft || !bounds) return <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"><b>CAD 空间草稿不可用</b><div className="mt-1 text-xs">{error || "专用空间草稿接口未返回可编辑数据。"}</div><Button className="mt-3" size="sm" variant="outline" onClick={() => void loadDraft()}><RefreshCw />重试</Button></section>;

  const previewPoints = drawStart && drawCurrent ? [drawStart, drawCurrent] : [];
  const recoverableFaces = draft.raw_faces.filter((face, index) =>
    face.manual_eligible === true && excludedFaces.has(cadFaceId(face, index)));
  return <section className="rounded-xl border border-border bg-panel p-4" aria-label="CAD 人工空间确认">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><div className="flex items-center gap-2"><PencilLine size={18} className="text-primary" /><h2 className="font-extrabold">CAD 人工房间确认</h2><span className="rounded-full bg-card px-2 py-1 font-mono text-[10px]">rev {draft.revision}</span>{dirty && <span className="rounded-full bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-800">有未保存修改</span>}</div><p className="mt-1 text-xs text-muted-foreground">原始墙、门、窗和 CAD 面始终只读；这里只确认面归属与房间语义。保存采用版本 CAS，绝不会触发生图或 provider。</p></div>
      <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={saving || loading} onClick={() => void loadDraft()}><RefreshCw className={loading ? "animate-spin" : ""} />重载</Button><Button size="sm" disabled={!dirty || saving} onClick={() => void save()}>{saving ? <LoaderCircle className="animate-spin" /> : <Save />}保存人工确认</Button></div>
    </div>
    {error && <div role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800"><b>CAD 空间草稿错误</b><div className="mt-1 break-words">{error}</div><div className="mt-1 text-[11px]">若为版本冲突，请重载服务器最新草稿后再编辑；本页不会静默覆盖。保存成功后的摘要刷新失败不会回滚已保存版本。</div></div>}

    <div className="mt-3 grid grid-cols-[minmax(0,1.5fr)_minmax(280px,1fr)] gap-4 max-[980px]:grid-cols-1">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Button size="sm" variant={tool === "inspect" ? "default" : "outline"} onClick={() => setTool("inspect")}><MousePointer2 />选择 / 排除面</Button>
          <Button size="sm" variant={tool === "rectangle" ? "default" : "outline"} onClick={() => setTool("rectangle")}><SquareDashed />绘制矩形语义区</Button>
          <Button size="sm" variant={tool === "split_halfplane" ? "default" : "outline"} onClick={() => setTool("split_halfplane")}><PencilLine />绘制分割线</Button>
        </div>
        <div className="overflow-hidden rounded-xl border border-border bg-[#f7f5ef]">
          <svg ref={svgRef} className={`block aspect-[16/10] w-full touch-none ${tool === "inspect" ? "cursor-pointer" : "cursor-crosshair"}`} viewBox={`${bounds.minX} ${bounds.minZ} ${bounds.width} ${bounds.height}`} preserveAspectRatio="xMidYMid meet" onPointerDown={beginDraw} onPointerMove={moveDraw} onPointerUp={finishDraw}>
            <g transform={`translate(0 ${bounds.minZ + bounds.maxZ}) scale(1 -1)`}>
            {draft.raw_faces.map((face, index) => {
              const id = cadFaceId(face, index);
              const excluded = excludedFaces.has(id);
              const candidate = face.manual_eligible === true;
              return <polygon key={id} points={polygonPoints(cadFacePolygon(face))} fill={excluded ? "#fecaca" : "#e5e7eb"} fillOpacity={excluded ? 0.7 : 0.5} stroke={excluded ? "#dc2626" : "#6b7280"} strokeWidth={bounds.width * 0.002} vectorEffect="non-scaling-stroke" onClick={(event) => { event.stopPropagation(); toggleRawFace(id); }}><title>{id} · {candidate ? excluded ? "点击重新保留" : "房间候选" : "解析器排除，只读"}</title></polygon>;
            })}
            {draft.physical_spaces.map((space) => <polygon key={space.id} points={polygonPoints(space.polygon || [])} fill={cadSpaceColor(space.id)} fillOpacity={space.selected ? (space.id === activeSpaceId ? 0.32 : 0.18) : 0.04} stroke={space.id === activeSpaceId ? cadSpaceColor(space.id) : "transparent"} strokeWidth={bounds.width * 0.005} vectorEffect="non-scaling-stroke" pointerEvents="none"><title>{space.label}</title></polygon>)}
            {draft.semantic_zones.map((zone) => {
              const points = cadZonePoints(zone);
              const zoneColor = cadSpaceColor(zone.id);
              return zone.geometry.kind === "split_halfplane" && points.length >= 2
                ? <line key={zone.id} x1={points[0].x} y1={points[0].z} x2={points[1].x} y2={points[1].z} stroke={zoneColor} strokeWidth={bounds.width * 0.006} strokeDasharray="7 4" vectorEffect="non-scaling-stroke"><title>{zone.label}</title></line>
                : <polygon key={zone.id} points={polygonPoints(points)} fill={zoneColor} fillOpacity="0.26" stroke={zoneColor} strokeWidth={bounds.width * 0.003} vectorEffect="non-scaling-stroke"><title>{zone.label}</title></polygon>;
            })}
            {previewPoints.length === 2 && (tool === "rectangle" ? <rect x={Math.min(previewPoints[0].x, previewPoints[1].x)} y={Math.min(previewPoints[0].z, previewPoints[1].z)} width={Math.abs(previewPoints[1].x - previewPoints[0].x)} height={Math.abs(previewPoints[1].z - previewPoints[0].z)} fill="#8b5cf6" fillOpacity="0.2" stroke="#7c3aed" strokeDasharray="7 4" vectorEffect="non-scaling-stroke" /> : <line x1={previewPoints[0].x} y1={previewPoints[0].z} x2={previewPoints[1].x} y2={previewPoints[1].z} stroke="#7c3aed" strokeWidth={bounds.width * 0.006} strokeDasharray="7 4" vectorEffect="non-scaling-stroke" />)}
            </g>
            {draft.text_anchors.map((anchor, index) => {
              const point = cadAnchorPoint(anchor);
              const displayZ = point ? bounds.minZ + bounds.maxZ - point.z : 0;
              return point ? <g key={String(anchor.anchor_id || anchor.id || index)} pointerEvents="none"><circle cx={point.x} cy={displayZ} r={bounds.width * 0.006} fill="#b45309" /><text x={point.x + bounds.width * 0.008} y={displayZ} fontSize={bounds.width * 0.018} fill="#92400e" dominantBaseline="central">{String(anchor.text || "")}</text></g> : null;
            })}
          </svg>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground"><span><i className="mr-1 inline-block size-2 rounded-full bg-gray-400" />raw faces（只读）</span><span><i className="mr-1 inline-block size-2 rounded-full bg-red-400" />排除面；安全候选面可点击恢复</span><span><i className="mr-1 inline-block size-2 rounded-full bg-blue-500" />physical spaces（按空间着色）</span><span><i className="mr-1 inline-block size-2 rounded-full bg-violet-500" />semantic zones（按分区着色）</span><span><i className="mr-1 inline-block size-2 rounded-full bg-amber-700" />text anchors</span></div>
      </div>

      <div className="max-h-[650px] space-y-3 overflow-y-auto pr-1">
        {recoverableFaces.length > 0 && <div className="rounded-lg border border-red-200 bg-red-50 p-3"><b className="text-xs text-red-800">可人工恢复的排除面 {recoverableFaces.length}</b><div className="mt-2 flex flex-wrap gap-1">{recoverableFaces.map((face, index) => { const id = cadFaceId(face, index); return <Button key={id} size="sm" variant="outline" onClick={() => toggleRawFace(id)}>恢复为物理空间 · {id}</Button>; })}</div></div>}
        <div><div className="flex items-center justify-between gap-2"><b className="text-sm">物理空间 {draft.physical_spaces.length}</b><Button size="sm" variant="outline" disabled={mergeIds.length < 2} onClick={mergeSelectedSpaces}><GitMerge />合并所选 {mergeIds.length || ""}</Button></div><div className="mt-1 text-[11px] text-muted-foreground">合并只提交 face 归属；后端负责检查 Shapely invalid polygon 并规范化，前端不宣称几何有效。</div></div>
        {draft.physical_spaces.map((space) => <article key={space.id} className={`rounded-lg border p-3 ${space.id === activeSpaceId ? "border-primary bg-primary/5" : "border-border bg-card"}`}>
          <div className="flex items-center gap-2"><button type="button" className="min-w-0 flex-1 truncate text-left text-xs font-bold" onClick={() => setActiveSpaceId(space.id)}><i className="mr-2 inline-block size-2 rounded-full" style={{ background: cadSpaceColor(space.id) }} />{space.label || space.id}</button><label className="flex items-center gap-1 text-[10px] text-muted-foreground"><input type="checkbox" checked={mergeIds.includes(space.id)} onChange={(event) => setMergeIds((ids) => event.target.checked ? [...ids, space.id] : ids.filter((id) => id !== space.id))} />合并</label></div>
          <div className="mt-2 grid grid-cols-2 gap-2"><Input aria-label={`${space.id} 名称`} value={space.label} onChange={(event) => patchSpace(space.id, { label: event.target.value })} /><select aria-label={`${space.id} 物理空间类型`} className="h-9 rounded-lg border border-border bg-card px-2 text-xs" value={space.space_type} onChange={(event) => patchSpace(space.id, { space_type: event.target.value })}>{physicalSpaceTypeOptions()}</select></div>
          <div className="mt-2 flex items-center justify-between gap-2"><span className="font-mono text-[10px] text-muted-foreground">{space.face_ids.length} faces · {space.id}</span><div className="flex items-center gap-2"><label className="flex items-center gap-1 text-[10px] text-muted-foreground"><input type="checkbox" checked={space.selected} onChange={(event) => patchSpace(space.id, { selected: event.target.checked })} />参与下游</label><Button size="sm" variant="outline" onClick={() => { setDraft(updateSpaceSelection(draft, space.id, false)); setMergeIds((ids) => ids.filter((id) => id !== space.id)); if (activeSpaceId === space.id) setActiveSpaceId(""); }}><Trash2 />排除此空间</Button></div></div>
        </article>)}

        <div className="border-t border-border pt-3"><b className="text-sm">语义区 {draft.semantic_zones.length}</b><div className="mt-1 text-[11px] text-muted-foreground">矩形或分割线仅创建派生语义区，不会切断或改写 CAD 物理墙。分割线会用互补 left/right 两区替换当前物理空间的旧分区，确保覆盖；保存后由后端规范成米制 polygon，并保留 source_geometry 审计证据。</div></div>
        {draft.semantic_zones.map((zone) => <article key={zone.id} className="rounded-lg border border-violet-200 bg-violet-50/50 p-3">
          <div className="grid grid-cols-2 gap-2"><Input aria-label={`${zone.id} 名称`} value={zone.label} onChange={(event) => patchZone(zone.id, { label: event.target.value })} /><select aria-label={`${zone.id} 语义房型`} className="h-9 rounded-lg border border-border bg-card px-2 text-xs" value={zone.zone_type} onChange={(event) => patchZone(zone.id, { zone_type: event.target.value })}>{semanticZoneTypeOptions()}</select></div>
          <div className="mt-2 flex items-center justify-between gap-2"><span className="font-mono text-[10px] text-muted-foreground">{zone.geometry.kind} · {zone.physical_space_id}</span><Button size="sm" variant="ghost" aria-label={`删除 ${zone.label}`} onClick={() => setDraft({ ...draft, semantic_zones: draft.semantic_zones.filter((value) => value.id !== zone.id) })}><Trash2 />删除</Button></div>
        </article>)}
      </div>
    </div>
  </section>;
}
