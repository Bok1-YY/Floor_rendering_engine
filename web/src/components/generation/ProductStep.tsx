"use client";

import { Pencil, Plus, Star, Trash2 } from "lucide-react";

import { FloorUploader } from "@/components/FloorUploader";
import FilmRepeatPanel from "@/components/FilmRepeatPanel";

import { GenerateStepCard } from "@/components/GenerateStepCard";

import { SectionHeader } from "@/components/dc-ui";

import type { GenerationWorkspaceModel } from "./useGenerationWorkspace";
const cleanLabel = (value: string) => value.replace(/^[\p{Extended_Pictographic}\uFE0F\u200D\s]+/u, "");
export function ProductStep({
  floor,
  freeImages,
  params,
  recipes,
  updateParams,
  pickFloor,
  clearFloor,
  myRecipes,
  setRecipeDlg,
  applyRecipe,
  applyCustomRecipe,
  removeCustomRecipe,
  openStep,
  setOpenStep,
  floorUploaderRef,
  isFreeMode
}: Pick<GenerationWorkspaceModel, "floor" | "freeImages" | "params" | "recipes" | "updateParams" | "pickFloor" | "clearFloor" | "myRecipes" | "setRecipeDlg" | "applyRecipe" | "applyCustomRecipe" | "removeCustomRecipe" | "openStep" | "setOpenStep" | "floorUploaderRef" | "isFreeMode">) {
  return <>          <GenerateStepCard
    step={1}
    title="产品 · 地板小样"
    summary={isFreeMode ? `${freeImages.length} 张自由创作素材` : floor ? `${floor.name} · ${params.floor_tone ? cleanLabel(params.floor_tone.split(" (")[0]) : "已识色"}` : "选择或上传一块地板小样"}
    open={openStep === 1}
    complete={isFreeMode ? freeImages.length > 0 : !!floor}
    onToggle={() => setOpenStep((step) => step === 1 ? 0 : 1)}
  >
    {isFreeMode ? (
      <div className="rounded-xl bg-accent px-3 py-3 text-[11.5px] leading-relaxed text-muted-foreground">
        自由创作的指令和 1–3 张素材图在“场景”步骤中配置。
      </div>
    ) : (
      <>
        <FloorUploader
          ref={floorUploaderRef}
          value={floor}
          onPick={pickFloor}
          tone={params.floor_tone}
          onClear={clearFloor}
        />
        <FilmRepeatPanel params={params} onParams={updateParams} />
      </>
    )}

    {!isFreeMode && recipes.length > 0 && (
      <>
        <SectionHeader className="mx-0.5 mb-[9px] mt-[16px]">
          智能配方 / 按色调推荐
        </SectionHeader>
        <div className="flex gap-[9px] overflow-x-auto pb-1.5">
          {recipes.map((r) => (
            <button
              key={r.key}
              onClick={() => applyRecipe(r)}
              className="w-[172px] flex-none rounded-xl border border-border bg-card p-[11px] text-left transition hover:border-primary hover:shadow-[0_6px_16px_rgba(120,90,60,.1)]"
            >
              <div className="mb-[7px] flex items-center gap-[7px]">
                <span
                  className="h-[18px] w-[18px] flex-none rounded-md ring-1 ring-black/5"
                  style={{
                    background: "linear-gradient(135deg,#d8b48a,#bf945f)",
                  }}
                />
                <span className="truncate text-[13px] font-bold text-foreground">
                  {cleanLabel(r.label)}
                </span>
              </div>
              <div className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                {r.sub}
              </div>
            </button>
          ))}
        </div>
      </>
    )}

    {!isFreeMode && (<>
      <SectionHeader className="mx-0.5 mb-[9px] mt-[16px]">
        我的配方 / MY RECIPES
      </SectionHeader>
      <div className="flex gap-[9px] overflow-x-auto pb-1.5">
        {myRecipes.map((r) => (
          <div
            key={r.id}
            className="group relative w-[172px] flex-none rounded-xl border border-border bg-card p-[11px] transition hover:border-primary hover:shadow-[0_6px_16px_rgba(120,90,60,.1)]"
          >
            <button
              onClick={() => applyCustomRecipe(r)}
              className="block w-full text-left"
            >
              <div className="mb-[7px] flex items-center gap-[7px]">
                <span className="flex h-[18px] w-[18px] flex-none items-center justify-center rounded-md bg-accent text-accent-foreground">
                  <Star size={11} />
                </span>
                <span className="truncate text-[13px] font-bold text-foreground">
                  {r.name}
                </span>
              </div>
              <div className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                {[r.params.workflow_mode?.split(" ")[0], r.params.style_type, r.params.lighting]
                  .filter(Boolean)
                  .map((value) => cleanLabel(String(value)))
                  .join(" · ") || "参数快照"}
              </div>
            </button>
            <div className="absolute right-[7px] top-[7px] hidden gap-1 group-hover:flex">
              <button
                title="改名"
                onClick={() => setRecipeDlg({ open: true, id: r.id, name: r.name })}
                className="flex h-[20px] w-[20px] items-center justify-center rounded-md bg-accent text-[11px] text-secondary-foreground hover:text-foreground"
              >
                <Pencil size={11} />
              </button>
              <button
                title="删除"
                onClick={() => removeCustomRecipe(r)}
                className="flex h-[20px] w-[20px] items-center justify-center rounded-md bg-accent text-[11px] text-secondary-foreground hover:text-destructive"
              >
                <Trash2 size={11} />
              </button>
            </div>
          </div>
        ))}
        <button
          onClick={() => setRecipeDlg({ open: true, id: "", name: "" })}
          className="flex w-[172px] flex-none flex-col items-center justify-center gap-1 rounded-xl border-[1.5px] border-dashed border-border-strong p-[11px] text-[12px] font-semibold text-muted-foreground transition hover:border-primary hover:text-accent-foreground"
        >
          <Plus size={16} />
          存为配方
        </button>
      </div>
    </>)}

  </GenerateStepCard>

  </>;
}
