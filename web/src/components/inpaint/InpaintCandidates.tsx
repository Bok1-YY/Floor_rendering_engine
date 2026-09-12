/* eslint-disable @next/next/no-img-element */
"use client";
import { api } from "@/lib/api";
import { Dialog, DialogContent } from "@/components/ui/dialog";

import { CompareSlider } from "@/components/CompareSlider";
import { cn } from "@/lib/utils";
import type { useInpaintSession } from "./useInpaintSession";
export function InpaintCandidates(vm: ReturnType<typeof useInpaintSession>) {
  const { candidates, partialNote, selected, applying, nCount } = vm.state;
  const { handleOpenChange, setSelected, redraw, reroll, applySelected } = vm.actions;

  const { toolBtn } = vm.display;
  const { open, srcUrl } = vm.input;
  return (<Dialog open={open} onOpenChange={handleOpenChange}>
    <DialogContent className="max-h-[94vh] max-w-[96vw] overflow-y-auto sm:max-w-[min(96vw,1280px)]">
      <div className="space-y-3">
        <div>
          <div className="text-[15.5px] font-bold">挑选修补结果</div>
          <div className="mt-0.5 text-[12px] text-muted-foreground">
            生成了 {candidates.length} 个候选：点缩略图切换，拖动中缝对比原图，满意就「使用这张」，不满意可再抽。
            {partialNote ? ` ⚠ ${partialNote}` : ""}
          </div>
        </div>
        <CompareSlider
          before={api.imgUrl(srcUrl)}
          after={api.imgUrl(candidates[selected]?.url || "")}
        />
        <div className="flex flex-wrap items-center gap-2">
          {candidates.map((c, i) => (
            <button
              key={c.url}
              onClick={() => setSelected(i)}
              className={cn(
                "overflow-hidden rounded-[10px] border-2",
                i === selected ? "border-primary" : "border-border opacity-70 hover:opacity-100",
              )}
              title={`候选 ${i + 1}`}
            >
              <img
                src={api.imgUrl(c.thumb || c.url)}
                alt={`候选 ${i + 1}`}
                className="block h-[84px] w-[112px] object-cover"
              />
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            <button className={toolBtn} onClick={redraw} disabled={applying}>
              ← 重新涂抹
            </button>
            <button
              className={toolBtn}
              onClick={reroll}
              disabled={applying}
              title="重新生成一批候选（按张计费）"
            >
              🎲 再抽 {nCount} 张
            </button>
            <button
              onClick={applySelected}
              disabled={applying}
              className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
            >
              {applying ? "保存中…" : "✓ 使用这张"}
            </button>
          </div>
        </div>
      </div>
    </DialogContent>
  </Dialog>);
}
