"use client";

import { api } from "@/lib/api";

import { Dialog, DialogContent } from "@/components/ui/dialog";

import { CompareSlider } from "@/components/CompareSlider";

import type { JobCardModel } from "./useJobCard";
export function JobCompareDialog({ job, compareOpen, setCompareOpen, compareAfter }: Pick<JobCardModel, "job" | "compareOpen" | "setCompareOpen" | "compareAfter">) {
  return <>       {/* 前后对比（原房间图 vs 出图，拖动滑块） */}
    <Dialog open={compareOpen} onOpenChange={setCompareOpen}>
      <DialogContent className="max-w-[96vw] sm:max-w-[min(92vw,1100px)]">
        <div className="space-y-3">
          <div>
            <div className="text-[15.5px] font-bold">前后对比</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              拖动中缝滑块对比原图与效果图 · 切换 ‹n/N› 候选后重开可对比其他张
            </div>
          </div>
          {compareOpen && (
            <CompareSlider
              before={api.imgUrl(job.room_url)}
              after={api.imgUrl(compareAfter)}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>

  </>;
}
