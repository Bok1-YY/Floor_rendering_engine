"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ClipboardCheck, LayoutTemplate, LoaderCircle, LockKeyhole, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type {
  WholeHomeConstructionProfile,
  WholeHomeFloorplanGraph,
  WholeHomeMarketingProposal,
  WholeHomeProject,
  WholeHomeSceneRecipe,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const PROFILE_FIELDS = [
  ["wall_height_m", "墙体高度", "二维户型没有立面信息，默认 2.80m"],
  ["interior_door_height_m", "室内门高度", "默认 2.10m"],
  ["window_sill_height_m", "窗台高度", "默认 0.90m"],
  ["window_head_height_m", "窗顶高度", "默认 2.10m"],
  ["floor_finish_thickness_m", "地面完成面厚度", "默认 0.015m"],
  ["ceiling_drop_m", "基础吊顶下沉", "默认 0.08m"],
  ["skirting_height_m", "踢脚线高度", "默认 0.08m"],
] as const;

function operationId(prefix: string) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

function ScenePlanPreview({ graph, recipe }: { graph: WholeHomeFloorplanGraph; recipe: WholeHomeSceneRecipe }) {
  const points = graph.rooms.flatMap((room) => room.polygon);
  const minX = Math.min(...points.map((point) => point.x), 0);
  const minZ = Math.min(...points.map((point) => point.z), 0);
  const maxX = Math.max(...points.map((point) => point.x), graph.extent_m.width || 1);
  const maxZ = Math.max(...points.map((point) => point.z), graph.extent_m.depth || 1);
  const width = Math.max(1, maxX - minX);
  const depth = Math.max(1, maxZ - minZ);
  const padding = Math.max(width, depth) * .05;
  return (
    <svg
      data-testid={`professional-scene-preview-${recipe.variant_index}`}
      viewBox={`${minX - padding} ${minZ - padding} ${width + padding * 2} ${depth + padding * 2}`}
      className="aspect-square w-full rounded-lg border border-border bg-[#f7f3ea]"
      aria-label={`方案 ${recipe.variant_index} 天空向地面正交预览`}
    >
      {graph.rooms.map((room) => (
        <polygon key={room.id} points={room.polygon.map((point) => `${point.x},${point.z}`).join(" ")}
          fill="#fbfaf7" stroke="#d6d3d1" strokeWidth={.035} />
      ))}
      {graph.walls.map((wall) => (
        <line key={wall.id} x1={wall.start.x} y1={wall.start.z} x2={wall.end.x} y2={wall.end.z}
          stroke="#292524" strokeWidth={Math.max(.08, wall.thickness_m)} strokeLinecap="square" />
      ))}
      {recipe.instances.map((instance) => {
        const { x, z } = instance.transform.position_m;
        const { width: itemWidth, depth: itemDepth } = instance.footprint_m;
        const palette = instance.semantic_role === "rug" ? "#d6c6ad"
          : instance.semantic_role.includes("bed") ? "#d8d2c4"
            : instance.semantic_role.includes("sofa") ? "#b9aa97"
              : instance.semantic_role.includes("plant") ? "#6f8465" : "#a97952";
        return <g key={instance.instance_id} transform={`rotate(${instance.transform.rotation_y_deg} ${x} ${z})`}>
          <rect x={x - itemWidth / 2} y={z - itemDepth / 2} width={itemWidth} height={itemDepth}
            rx={.05} fill={palette} fillOpacity={instance.semantic_role === "rug" ? .45 : .86}
            stroke="#574536" strokeWidth={.025} />
        </g>;
      })}
    </svg>
  );
}

export function WholeHomeProfessionalProposal({
  project,
  onProjectUpdate,
  onActiveRecipeChange,
}: {
  project: WholeHomeProject;
  onProjectUpdate: (project: WholeHomeProject) => void;
  onActiveRecipeChange?: (recipe: WholeHomeSceneRecipe | null) => void;
}) {
  const [graph, setGraph] = useState<WholeHomeFloorplanGraph | null>(null);
  const [profile, setProfile] = useState<WholeHomeConstructionProfile | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [previews, setPreviews] = useState<WholeHomeSceneRecipe[]>([]);
  const [recipes, setRecipes] = useState<WholeHomeSceneRecipe[]>([]);
  const [activeRecipeId, setActiveRecipeId] = useState("");
  const [proposal, setProposal] = useState<WholeHomeMarketingProposal | null>(null);
  const [busy, setBusy] = useState("");

  const refresh = useCallback(async () => {
    const [graphResult, profileResult, recipeResult, proposalResult] = await Promise.allSettled([
      api.getWholeHomeFloorplanGraph(project.project_id),
      api.getWholeHomeConstructionProfile(project.project_id),
      api.listWholeHomeSceneRecipes(project.project_id),
      api.getWholeHomeMarketingProposal(project.project_id),
    ]);
    if (graphResult.status === "fulfilled") setGraph(graphResult.value);
    else setGraph(null);
    if (profileResult.status === "fulfilled") {
      setProfile(profileResult.value);
      setValues(Object.fromEntries(Object.entries(profileResult.value.fields).map(([key, row]) => [key, String(row.value)])));
    }
    if (recipeResult.status === "fulfilled") {
      setRecipes(recipeResult.value.recipes);
      setActiveRecipeId(recipeResult.value.active_scene_recipe_id);
    }
    if (proposalResult.status === "fulfilled") setProposal(proposalResult.value);
  }, [project.project_id]);

  useEffect(() => { void refresh(); }, [refresh, project.professional_revision]);

  useEffect(() => {
    setPreviews([]);
    setRecipes([]);
    setActiveRecipeId("");
    setProposal(null);
    onActiveRecipeChange?.(null);
  }, [project.project_id, onActiveRecipeChange]);

  const activeRecipe = useMemo(() => recipes.find((row) => row.recipe_id === activeRecipeId) || null,
    [activeRecipeId, recipes]);

  useEffect(() => {
    onActiveRecipeChange?.(activeRecipe);
  }, [activeRecipe, onActiveRecipeChange]);

  async function confirmProfile() {
    setBusy("profile");
    try {
      const numeric = Object.fromEntries(PROFILE_FIELDS.map(([key]) => [key, Number(values[key])]));
      if (Object.values(numeric).some((value) => !Number.isFinite(value))) throw new Error("请填写全部构造参数");
      const updated = await api.confirmWholeHomeConstructionProfile(project.project_id, {
        base_revision: project.revision,
        operation_id: operationId("profile"), reviewer: "local-sales-user", values: numeric,
      });
      onProjectUpdate(updated);
      toast.success("构造假设已确认；这些数值会绑定到后续场景和渲染历史。 ");
      await refresh();
    } catch (error) {
      toast.error(`构造参数保存失败：${(error as Error).message}`);
    } finally { setBusy(""); }
  }

  async function generatePreviews() {
    setBusy("preview");
    try {
      const rows = await Promise.all(([1, 2, 3] as const).map((variant) =>
        api.previewWholeHomeSceneRecipe(project.project_id, variant)));
      setPreviews(rows);
      toast.success("三个本地确定性方案已生成；本步骤不调用生图 API，也不产生费用。 ");
    } catch (error) {
      toast.error(`方案候选生成失败：${(error as Error).message}`);
    } finally { setBusy(""); }
  }

  async function saveCandidate(recipe: WholeHomeSceneRecipe) {
    setBusy(`save-${recipe.variant_index}`);
    try {
      const updated = await api.createWholeHomeSceneRecipe(project.project_id, {
        base_revision: project.revision, operation_id: operationId("scene"),
        reviewer: "local-sales-user", variant_index: recipe.variant_index,
      });
      onProjectUpdate(updated);
      await refresh();
      toast.success(`方案 ${recipe.variant_index} 已保存为可追溯 SceneRecipe。`);
    } catch (error) {
      toast.error(`方案保存失败：${(error as Error).message}`);
    } finally { setBusy(""); }
  }

  async function reviewActive(lock: boolean) {
    if (!activeRecipe) return;
    setBusy(lock ? "lock" : "review");
    try {
      const updated = await api.reviewWholeHomeSceneRecipe(project.project_id, activeRecipe.recipe_id, {
        base_revision: project.revision, operation_id: operationId(lock ? "lockscene" : "reviewscene"),
        reviewer: "local-sales-user", note: "已在二维正交预览和三维灰模中复核布局、通道与房间用途。",
        action: lock ? "lock" : "review",
      });
      onProjectUpdate(updated);
      await refresh();
      toast.success(lock ? "场景方案已锁定，后续全景必须绑定同一 scene_hash。" : "场景方案已标记为人工复核。 ");
    } catch (error) {
      toast.error(`场景复核失败：${(error as Error).message}`);
    } finally { setBusy(""); }
  }

  return (
    <section data-testid="whole-home-professional-proposal" className="rounded-xl border border-amber-300/70 bg-gradient-to-br from-amber-50 via-card to-stone-50 p-4 dark:from-amber-950/20 dark:to-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><Sparkles size={18} className="text-amber-700" /><h2 className="font-extrabold">装修获客提案 · 现代暖木自然</h2></div>
          <p className="mt-1 max-w-4xl text-xs leading-relaxed text-muted-foreground">空户型图经过少量校正后，生成三个整屋一致方案，再进入 3–8 点位 VR。这里交付营销概念，不承诺真实 SKU、报价或施工尺寸。</p>
        </div>
        <div className="rounded-full border border-amber-300 bg-white/80 px-3 py-1 text-xs font-bold text-amber-900">图片主线 · CAD 为高级输入</div>
      </div>

      <div className="mt-4 grid grid-cols-4 gap-2 text-xs max-[900px]:grid-cols-2">
        <div className="rounded-lg border border-border bg-white/70 p-2"><span className="text-muted-foreground">户型拓扑</span><div className="mt-1 font-bold">{graph?.review.status || "等待模型"}</div></div>
        <div className="rounded-lg border border-border bg-white/70 p-2"><span className="text-muted-foreground">构造假设</span><div className="mt-1 font-bold">{profile?.status === "confirmed" ? "已逐项确认" : "待确认"}</div></div>
        <div className="rounded-lg border border-border bg-white/70 p-2"><span className="text-muted-foreground">当前方案</span><div className="mt-1 font-bold">{activeRecipe ? `候选 ${activeRecipe.variant_index} · ${activeRecipe.status}` : "未保存"}</div></div>
        <div className="rounded-lg border border-border bg-white/70 p-2"><span className="text-muted-foreground">营销成果</span><div className="mt-1 font-bold">{proposal?.status === "ready" ? "可分享" : "尚未完成 3 个认证全景"}</div></div>
      </div>

      {graph && graph.review.unresolved_ids.length > 0 && (
        <div className="mt-3 rounded-lg border border-amber-300 bg-amber-100/60 p-3 text-xs text-amber-950">
          <b>户型仍有 {graph.review.unresolved_ids.length} 项需要校正</b>
          <div className="mt-1 break-words text-[11px]">{graph.review.unresolved_ids.slice(0, 12).join(" · ")}</div>
        </div>
      )}

      {profile && (
        <div className="mt-4 rounded-xl border border-border bg-white/70 p-3">
          <div className="flex items-center gap-2"><ClipboardCheck size={16} /><b className="text-sm">A. 确认二维图中缺失的立面参数</b></div>
          <div className="mt-3 grid grid-cols-4 gap-2 max-[1000px]:grid-cols-2 max-[620px]:grid-cols-1">
            {PROFILE_FIELDS.map(([key, label, hint]) => (
              <label key={key} className="space-y-1 text-xs font-semibold">{label}（m）
                <Input type="number" min="0" step="0.005" value={values[key] || ""}
                  disabled={profile.status === "confirmed" && Boolean(activeRecipe?.status === "locked")}
                  onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} />
                <span className="block text-[10px] font-normal text-muted-foreground">{hint}</span>
              </label>
            ))}
          </div>
          <div className="mt-3 flex justify-end"><Button onClick={confirmProfile} disabled={Boolean(busy) || activeRecipe?.status === "locked"}>
            {busy === "profile" ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}{profile.status === "confirmed" ? "重新确认并使旧方案失效" : "确认全部构造假设"}
          </Button></div>
        </div>
      )}

      <div className="mt-4 rounded-xl border border-border bg-white/70 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2"><LayoutTemplate size={16} /><b className="text-sm">B. 生成三个确定性整屋方案</b></div>
          <Button variant="outline" onClick={generatePreviews} disabled={Boolean(busy) || !graph}>
            {busy === "preview" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}本地生成 3 个候选
          </Button>
        </div>
        {previews.length > 0 ? <div className="mt-3 grid grid-cols-3 gap-3 max-[900px]:grid-cols-1">
          {previews.map((recipe) => (
            <article key={recipe.recipe_id} className={`rounded-xl border p-3 ${recipe.quality.status === "passed" ? "border-emerald-300 bg-emerald-50/50" : "border-amber-300 bg-amber-50"}`}>
              {graph && <ScenePlanPreview graph={graph} recipe={recipe} />}
              <div className="mt-2 flex items-center justify-between"><b>方案 {recipe.variant_index}</b><span className="text-xs font-bold">{recipe.quality.score} 分</span></div>
              <div className="mt-1 text-[11px] text-muted-foreground">{recipe.quality.instance_count} 个资产 · {recipe.quality.blocking_issues.length} 个阻断 · {recipe.quality.warnings.length} 个提示</div>
              <Button className="mt-2 w-full" size="sm" disabled={Boolean(busy) || profile?.status !== "confirmed" || recipe.quality.status !== "passed"}
                onClick={() => saveCandidate(recipe)}>
                {busy === `save-${recipe.variant_index}` ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}选择并保存方案 {recipe.variant_index}
              </Button>
            </article>
          ))}
        </div> : <div className="mt-3 rounded-lg border border-dashed border-border p-5 text-center text-xs text-muted-foreground">确认户型后，点击右上角生成三个免费、本地、可复现的布局候选。</div>}
      </div>

      {activeRecipe && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/70 p-3 text-xs">
          <div><b>当前 SceneRecipe：方案 {activeRecipe.variant_index}</b><div className="mt-1 font-mono text-[10px] text-muted-foreground">scene {activeRecipe.scene_hash}<br />recipe {activeRecipe.recipe_hash}</div></div>
          <div className="flex gap-2"><Button variant="outline" disabled={Boolean(busy) || activeRecipe.status === "locked"} onClick={() => reviewActive(false)}>
            {busy === "review" ? <LoaderCircle className="animate-spin" /> : <ClipboardCheck />}标记已复核
          </Button><Button disabled={Boolean(busy) || activeRecipe.status === "locked" || !project.verified || profile?.status !== "confirmed"} onClick={() => reviewActive(true)}>
            {busy === "lock" ? <LoaderCircle className="animate-spin" /> : <LockKeyhole />}{activeRecipe.status === "locked" ? "方案已锁定" : "锁定方案并进入全景"}
          </Button></div>
        </div>
      )}

      {proposal && proposal.blockers.length > 0 && (
        <div className="mt-3 rounded-lg bg-stone-100 p-3 text-[11px] text-stone-700"><b>营销包尚缺：</b>{proposal.blockers.join(" · ")}</div>
      )}
      {proposal && <div className="mt-3 space-y-1 text-[10px] text-muted-foreground">{proposal.disclaimers.map((row) => <div key={row}>• {row}</div>)}</div>}
    </section>
  );
}
