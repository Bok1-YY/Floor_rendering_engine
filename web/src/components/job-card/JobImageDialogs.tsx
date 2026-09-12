"use client";

import { ImageZoom } from "@/components/ImageZoom";

import { ColorMatchDialog } from "@/components/ColorMatchDialog";
import { InpaintDialog } from "@/components/InpaintDialog";
import { FloorVisualizeDialog } from "@/components/FloorVisualizeDialog";

import type { JobCardModel } from "./useJobCard";
export function JobImageDialogs({
  job,
  zoom,
  setZoom,
  colorMatch,
  setColorMatch,
  inpaint,
  setInpaint,
  floorVisualize,
  setFloorVisualize,
  onEditorDone
}: Pick<JobCardModel, "job" | "zoom" | "setZoom" | "colorMatch" | "setColorMatch" | "inpaint" | "setInpaint" | "floorVisualize" | "setFloorVisualize" | "onEditorDone">) {
  return <>       <ImageZoom url={zoom} onClose={() => setZoom(null)} />

    {/* 生成式修补（画笔涂抹选区，引擎局部重绘，结果并入所点图槽的候选） */}
    {inpaint && (
      <InpaintDialog
        open={!!inpaint}
        onOpenChange={(o) => !o && setInpaint(null)}
        srcUrl={inpaint.srcUrl}
        target={{ kind: "job", jobId: job.job_id, stage: inpaint.stage, imageRel: inpaint.imageRel }}
        onDone={onEditorDone}
      />
    )}

    {/* 本地确定性纹理投影（不调用生成模型） */}
    {floorVisualize && (
      <FloorVisualizeDialog
        open={!!floorVisualize}
        onOpenChange={(o) => !o && setFloorVisualize(null)}
        srcUrl={floorVisualize.srcUrl}
        textureUrl={job.floor_url}
        texturePath={job.floor_path}
        target={{
          kind: "job",
          jobId: job.job_id,
          stage: floorVisualize.stage,
          imageRel: floorVisualize.imageRel,
        }}
        onDone={onEditorDone}
      />
    )}

    {/* 手动校色（区域化 Reinhard，结果并入所点图槽的候选） */}
    {colorMatch && (
      <ColorMatchDialog
        open={!!colorMatch}
        onOpenChange={(o) => !o && setColorMatch(null)}
        srcUrl={colorMatch.srcUrl}
        imageRel={colorMatch.imageRel}
        refUrl={job.floor_url}
        refPath={job.floor_path}
        target={{ kind: "job", jobId: job.job_id, stage: colorMatch.stage }}
        onDone={onEditorDone}
      />
    )}

  </>;
}
