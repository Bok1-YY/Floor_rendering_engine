"use client";

import { api } from "@/lib/api";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import { ImageZoom } from "@/components/ImageZoom";
import { CompareSlider } from "@/components/CompareSlider";
import { ColorMatchDialog } from "@/components/ColorMatchDialog";
import { InpaintDialog } from "@/components/InpaintDialog";
import { FloorVisualizeDialog } from "@/components/FloorVisualizeDialog";
import PanoViewer from "@/components/PanoViewer";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordImageDialogs({
  active,
  zoom,
  panoView,
  compare,
  colorMatch,
  inpaint,
  floorVisualize,
  setZoom,
  setPanoView,
  setCompare,
  setColorMatch,
  setInpaint,
  setFloorVisualize,
  afterMutation
}: Pick<RecordLibraryModel, "active" | "zoom" | "panoView" | "compare" | "colorMatch" | "inpaint" | "floorVisualize" | "setZoom" | "setPanoView" | "setCompare" | "setColorMatch" | "setInpaint" | "setFloorVisualize" | "afterMutation">) {
  return <>       {/* 放大 */}
    <ImageZoom url={zoom} onClose={() => setZoom(null)} />

    <Dialog open={Boolean(panoView)} onOpenChange={(open) => { if (!open) setPanoView(null); }}>
      <DialogContent className="max-h-[96vh] max-w-[98vw] overflow-y-auto sm:max-w-[min(96vw,1200px)]">
        {panoView && <>
          <div className="pr-10 text-sm font-bold">360° 球面全景 · {panoView.label}</div>
          <div className="rounded-lg bg-sky-50 px-3 py-2 text-xs font-bold text-sky-800">历史只读资产 · 新建、恢复、修补和球面地板处理已退役</div>
          <PanoViewer erpUrl={panoView.url} initialYawDeg={panoView.initialYawDeg} />
        </>}
      </DialogContent>
    </Dialog>

    {/* 生成式修补 */}
    {inpaint && active && (
      <InpaintDialog
        open={inpaint.open}
        onOpenChange={(o) => !o && setInpaint(null)}
        srcUrl={inpaint.srcUrl}
        target={{
          kind: "record",
          jsonPath: inpaint.jsonPath!,
          recordId: inpaint.recordId,
          resultId: inpaint.resultId,
        }}
        onDone={() => afterMutation(inpaint.jsonPath!)}
      />
    )}

    {/* 真实纹理投影 */}
    {floorVisualize && active && (
      <FloorVisualizeDialog
        open={floorVisualize.open}
        onOpenChange={(o) => !o && setFloorVisualize(null)}
        srcUrl={floorVisualize.srcUrl}
        textureUrl={floorVisualize.textureUrl}
        texturePath={floorVisualize.texturePath}
        target={{
          kind: "record",
          jsonPath: floorVisualize.jsonPath!,
          recordId: floorVisualize.recordId,
          resultId: floorVisualize.resultId,
        }}
        onDone={() => afterMutation(floorVisualize.jsonPath!)}
      />
    )}

    {/* 手动校色 */}
    {colorMatch && active && (
      <ColorMatchDialog
        open={colorMatch.open}
        onOpenChange={(o) => !o && setColorMatch(null)}
        srcUrl={colorMatch.srcUrl}
        imageRel={colorMatch.imageRel}
        refUrl={colorMatch.refUrl}
        refPath={colorMatch.refPath}
        target={{
          kind: "record",
          jsonPath: colorMatch.jsonPath!,
          recordId: colorMatch.recordId,
          resultId: colorMatch.resultId,
        }}
        onDone={() => afterMutation(colorMatch.jsonPath!)}
      />
    )}

    {/* 前后对比 */}
    <Dialog open={!!compare} onOpenChange={(o) => !o && setCompare(null)}>
      <DialogContent className="max-w-[96vw] sm:max-w-[min(92vw,1100px)]">
        <div className="space-y-3">
          <div>
            <div className="text-[15px] font-bold">前后对比</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              拖动中缝滑块对比原图与效果图
            </div>
          </div>
          {compare && (
            <CompareSlider
              before={api.imgUrl(compare.before)}
              after={api.imgUrl(compare.after)}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>

  </>;
}
