"use client";
import { RecipeDialog } from './RecipeDialog';
import { PreviewDialog } from './PreviewDialog';
import { BatchDialog } from './BatchDialog';
import { GenerationResults } from './GenerationResults';
import { SceneOutputSteps } from './SceneOutputSteps';
import { ProductStep } from './ProductStep';
import { SubmissionRecovery } from './SubmissionRecovery';

import { Eye, Grid2X2, Sparkles } from "lucide-react";

import { ImageZoom } from "@/components/ImageZoom";

import type { GenerationWorkspaceModel } from "./useGenerationWorkspace";
export function GenerationView({ model }: { model: GenerationWorkspaceModel }) {
  const {
    options,
    floor,
    refImg,
    roomImg,
    freePrompt,
    freeImages,
    modelTargets,
    sdOptions,
    sdEnabled,
    params,
    recipes,
    setRefImg,
    setRoomImg,
    setFreePrompt,
    setFreeImages,
    setModelTargets,
    setSdOptions,
    updateParams,
    pickFloor,
    clearFloor,
    jobs,
    refreshJobs,
    clearCompleted,
    cancelAll,
    removeJob,
    preview,
    previewOpen,
    runPreview,
    closePreview,
    myRecipes,
    recipeDlg,
    setRecipeDlg,
    applyRecipe,
    applyCustomRecipe,
    submitRecipeDlg,
    removeCustomRecipe,
    submitting,
    batchSubmitting,
    batchOpen,
    batchTab,
    batchRooms,
    batchFloors,
    recentFloors,
    batchNotice,
    generate,
    openBatch,
    setBatchOpen,
    runBatch,
    runBatchFloors,
    setBatchTab,
    setBatchRooms,
    setBatchFloors,
    openStep,
    setOpenStep,
    zoom,
    setZoom,
    floorUploaderRef,
    total,
    activeCount,
    doneCount,
    pct,
    isFreeMode,
    batchRoomOptions,
    outputLabel
  } = model;
  return (
    <div className="flex h-full min-w-0 overflow-hidden">
      {/* ── 左：参数列 ── */}
      <section className="flex w-[clamp(430px,40vw,540px)] min-w-[430px] flex-none flex-col border-r border-border bg-panel max-[980px]:min-w-[410px]">
        <div className="flex flex-1 flex-col gap-[10px] overflow-y-auto px-[18px] py-4 max-[1080px]:px-4">
          <SubmissionRecovery recovery={model.recovery} />

          <ProductStep
            floor={floor}
            freeImages={freeImages}
            params={params}
            recipes={recipes}
            updateParams={updateParams}
            pickFloor={pickFloor}
            clearFloor={clearFloor}
            myRecipes={myRecipes}
            setRecipeDlg={setRecipeDlg}
            applyRecipe={applyRecipe}
            applyCustomRecipe={applyCustomRecipe}
            removeCustomRecipe={removeCustomRecipe}
            openStep={openStep}
            setOpenStep={setOpenStep}
            floorUploaderRef={floorUploaderRef}
            isFreeMode={isFreeMode}
          />
          <SceneOutputSteps
            options={options}
            refImg={refImg}
            roomImg={roomImg}
            freePrompt={freePrompt}
            freeImages={freeImages}
            modelTargets={modelTargets}
            sdOptions={sdOptions}
            sdEnabled={sdEnabled}
            params={params}
            setRefImg={setRefImg}
            setRoomImg={setRoomImg}
            setFreePrompt={setFreePrompt}
            setFreeImages={setFreeImages}
            setModelTargets={setModelTargets}
            setSdOptions={setSdOptions}
            updateParams={updateParams}
            openStep={openStep}
            setOpenStep={setOpenStep}
            isFreeMode={isFreeMode}
            outputLabel={outputLabel}
          />
        </div>

        {/* sticky 底栏 */}
        <div className="flex flex-none flex-col gap-1.5 border-t border-border bg-card px-[18px] py-[13px] max-[1080px]:px-4">
          <div className="flex gap-2.5">
            <button
              onClick={generate}
              disabled={
                submitting ||
                (isFreeMode ? !freePrompt.trim() || freeImages.length === 0 : !floor)
              }
              className="flex h-[46px] flex-1 items-center justify-center gap-2 rounded-xl bg-primary text-[14.5px] font-bold text-primary-foreground shadow-[0_6px_16px_rgba(193,95,60,.32)] transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Sparkles size={18} strokeWidth={2.2} />
              {submitting ? "提交中…" : isFreeMode ? "生成图片" : "生成效果图"}
            </button>
            {!isFreeMode && (<>
              <button
                onClick={runPreview}
                disabled={!floor || previewOpen}
                title="用 Nano Banana 2 Lite 出一张 1K 快速预览（几秒、便宜），满意再点「生成效果图」出 4K"
                className="h-[46px] flex-none rounded-xl border border-border bg-card px-[15px] text-[13px] font-bold text-secondary-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="inline-flex items-center gap-1.5"><Eye size={15} />预览</span>
              </button>
              <button
                onClick={openBatch}
                disabled={batchSubmitting || !floor || !options || params.workflow_mode.includes("墙板")}
                title={
                  params.workflow_mode.includes("墙板")
                    ? "墙板模式不支持批量"
                    : params.workflow_mode.includes("Omakase")
                      ? "Omakase 支持多地板批量（同场景 × 多块地板）"
                      : undefined
                }
                className="h-[46px] flex-none rounded-xl border border-border bg-card px-[18px] text-[13.5px] font-bold text-secondary-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="inline-flex items-center gap-1.5"><Grid2X2 size={15} />批量</span>
              </button>
            </>)}
          </div>
          <div className="truncate text-center text-[11px] text-muted-foreground">
            {params.workflow_mode.includes("Omakase")
              ? "Omakase 可按同一场景批量替换多块地板"
              : `${outputLabel || "选择模型"} 并行 · 正式出图速度取决于所选 API · 1K 预览通常更快`}
          </div>
        </div>
      </section>

      <GenerationResults
        floor={floor}
        jobs={jobs}
        refreshJobs={refreshJobs}
        clearCompleted={clearCompleted}
        cancelAll={cancelAll}
        removeJob={removeJob}
        setOpenStep={setOpenStep}
        floorUploaderRef={floorUploaderRef}
        total={total}
        activeCount={activeCount}
        doneCount={doneCount}
        pct={pct}
        isFreeMode={isFreeMode}
      />
      <BatchDialog
        recovery={model.recovery}
        options={options}
        modelTargets={modelTargets}
        params={params}
        batchSubmitting={batchSubmitting}
        batchOpen={batchOpen}
        batchTab={batchTab}
        batchRooms={batchRooms}
        batchFloors={batchFloors}
        recentFloors={recentFloors}
        batchNotice={batchNotice}
        setBatchOpen={setBatchOpen}
        runBatch={runBatch}
        runBatchFloors={runBatchFloors}
        setBatchTab={setBatchTab}
        setBatchRooms={setBatchRooms}
        setBatchFloors={setBatchFloors}
        batchRoomOptions={batchRoomOptions}
      />
      <PreviewDialog preview={preview} previewOpen={previewOpen} closePreview={closePreview} setZoom={setZoom} />
      <RecipeDialog recipeDlg={recipeDlg} setRecipeDlg={setRecipeDlg} submitRecipeDlg={submitRecipeDlg} />
      <ImageZoom url={zoom} onClose={() => setZoom(null)} />
    </div>
  );
}
