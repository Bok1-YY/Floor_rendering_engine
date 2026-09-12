"use client";

import { ParamsForm } from "@/components/ParamsForm";
import { OutputForm } from "@/components/OutputForm";
import { GenerateStepCard } from "@/components/GenerateStepCard";

import type { GenerationWorkspaceModel } from "./useGenerationWorkspace";
const cleanLabel = (value: string) => value.replace(/^[\p{Extended_Pictographic}\uFE0F\u200D\s]+/u, "");
export function SceneOutputSteps({
  options,
  refImg,
  roomImg,
  freePrompt,
  freeImages,
  modelTargets,
  sdOptions,
  sdEnabled,
  params,
  setRefImg,
  setRoomImg,
  setFreePrompt,
  setFreeImages,
  setModelTargets,
  setSdOptions,
  updateParams,
  openStep,
  setOpenStep,
  isFreeMode,
  outputLabel
}: Pick<GenerationWorkspaceModel, "options" | "refImg" | "roomImg" | "freePrompt" | "freeImages" | "modelTargets" | "sdOptions" | "sdEnabled" | "params" | "setRefImg" | "setRoomImg" | "setFreePrompt" | "setFreeImages" | "setModelTargets" | "setSdOptions" | "updateParams" | "openStep" | "setOpenStep" | "isFreeMode" | "outputLabel">) {
  return <>          {options ? (
    <>
      <GenerateStepCard
        step={2}
        title="场景"
        summary={[
          params.workflow_mode.split(" (")[0],
          isFreeMode ? `${freeImages.length} 张素材` : params.cn_mode ? params.cn_room_type : params.room_type,
          params.style_type,
          params.lighting,
          params.angle,
        ].filter(Boolean).map((value) => cleanLabel(String(value).split(" (")[0])).join(" · ")}
        open={openStep === 2}
        complete={isFreeMode ? !!freePrompt.trim() && freeImages.length > 0 : true}
        onToggle={() => setOpenStep((step) => step === 2 ? 0 : 2)}
      >
        <ParamsForm
          options={options}
          params={params}
          onParams={updateParams}
          refValue={refImg}
          onRefPick={setRefImg}
          roomValue={roomImg}
          onRoomPick={setRoomImg}
          freePrompt={freePrompt}
          freeImages={freeImages}
          onFreePrompt={setFreePrompt}
          onFreeImages={setFreeImages}
        />
      </GenerateStepCard>
      <GenerateStepCard
        step={3}
        title="输出"
        summary={`${outputLabel || "未选择模型"} · ${params.resolution || options.resolutions[0]} · ${(params.aspect_ratio || options.aspect_ratios[0]).split(" (")[0]}`}
        open={openStep === 3}
        complete={modelTargets.length > 0}
        onToggle={() => setOpenStep((step) => step === 3 ? 0 : 3)}
      >
        <OutputForm
          options={options}
          params={params}
          modelTargets={modelTargets}
          sdOptions={sdOptions}
          sdEnabled={sdEnabled}
          onParams={updateParams}
          onModelTargets={setModelTargets}
          onSDOptions={(patch) => setSdOptions((value) => ({ ...value, ...patch }))}
        />
      </GenerateStepCard>
    </>
  ) : (
    <div className="rounded-[14px] border border-border bg-card p-5 text-sm text-muted-foreground">加载选项中…</div>
  )}
  </>;
}
