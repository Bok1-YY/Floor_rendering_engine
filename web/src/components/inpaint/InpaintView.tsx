"use client";
import { InpaintCandidates } from "./InpaintCandidates";
import { InpaintCanvas } from "./InpaintCanvas";
import { InpaintToolbar } from "./InpaintToolbar";





import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Slider } from "@/components/ui/slider";




import type { useInpaintSession } from "./useInpaintSession";
export function InpaintView(vm: ReturnType<typeof useInpaintSession>) {
  const { candidates, nCount, task, smartMessage, mode, selectedCandidateIds, scanCandidates, hasMask, advancedOpen, prompt, addModel, submitting, seedText } = vm.state;
  const { handleOpenChange, startObjectScan, setPrompt, submit, setSeedText, setGrow, setFeather } = vm.actions;

  const { smartBusy, eraserRemove, grow, feather } = vm.display;
  const { open } = vm.input;
  // ── 候选挑选视图（三种目标统一）──
  if (candidates.length > 0) {
    return (
      <InpaintCandidates {...vm} />
    );
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {/* sm:前缀必须带：DialogContent 默认 sm:max-w-sm，无前缀的 max-w 在 sm+ 会被它覆盖 */}
      <DialogContent className="max-h-[94vh] max-w-[96vw] overflow-y-auto sm:max-w-[min(96vw,1280px)]">
        <div className="space-y-3">
          <div>
            <div className="text-[15.5px] font-bold">生成式修补</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              智能选区可自动贴合物件或承载区域；画笔和橡皮始终保留，用于补阴影、收窄或修正边缘。最终处理范围之外保持原图。
            </div>
          </div>

          {/* 画布区：内层容器收缩包裹图像（不能用 w-full+object-contain，
              信箱留白会让 mask 画布与图像内容错位） */}
          <InpaintCanvas {...vm} />

          <div className="flex min-h-5 flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            <span>{smartBusy ? "⏳ " : ""}{smartMessage}</span>
            {mode === "remove" && selectedCandidateIds.length > 0 && (
              <span className="font-semibold text-primary">已选 {selectedCandidateIds.length} 个物件</span>
            )}
            {mode === "remove" && !smartBusy && scanCandidates.length === 0 && (
              <button type="button" className="font-semibold text-primary hover:underline" onClick={() => void startObjectScan(true)}>
                重新识别
              </button>
            )}
          </div>

          {/* 工具行 */}
          <InpaintToolbar {...vm} />

          {/* prompt 行（纯 eraser 移除时无需描述，隐藏输入框） */}
          <div className="flex flex-wrap items-center gap-2.5">
            {eraserRemove ? (
              <span className="flex h-9 min-w-[260px] flex-1 items-center rounded-[9px] border border-dashed border-border bg-panel/50 px-3 text-[12px] text-muted-foreground">
                当前移除模型自动擦除并重建背景，无需文字描述
              </span>
            ) : (
              <input
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={
                  mode === "remove"
                    ? "可选补充说明：比如“这里应延续木地板”（留空 = 自动重建周边材质）"
                    : `必填：描述要添加的内容，如“一盆大型龟背竹绿植”（当前 ${addModel}）`
                }
                className="h-9 min-w-[260px] flex-1 rounded-[9px] border border-border bg-panel px-3 text-[12.5px] outline-none placeholder:text-muted-foreground focus:border-primary"
              />
            )}
            <button
              onClick={submit}
              disabled={!hasMask || !!task || submitting}
              title={!hasMask ? "请先智能选择或涂抹选区" : undefined}
              className="h-9 flex-none rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
            >
              {task || submitting
                ? "处理中…"
                : mode === "remove"
                  ? `移除选中区域 ×${nCount}`
                  : `在选中区域生成 ×${nCount}`}
            </button>
          </div>

          {advancedOpen && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel/60 p-3">
              <div className="flex min-w-[220px] items-center gap-2" title={mode === "remove" ? "移除会按选区尺寸自动外扩，这里设置最小值；请仍把完整阴影涂上" : "添加默认不外扩；只有你主动调大时才扩展处理范围"}>
                <span className="flex-none text-[11.5px] font-semibold text-secondary-foreground">选区外扩</span>
                <Slider
                  value={grow}
                  min={0}
                  max={64}
                  step={1}
                  onValueChange={(v) => setGrow(Array.isArray(v) ? v[0] : (v as number))}
                />
                <span className="w-10 flex-none text-right text-[11px] tabular-nums text-muted-foreground">{grow}px</span>
              </div>
              <div className="flex min-w-[220px] items-center gap-2" title={mode === "add" ? "添加模式只向有效选区内部羽化，选区外像素保持不变" : "移除模式在外扩后的边缘做柔和过渡"}>
                <span className="flex-none text-[11.5px] font-semibold text-secondary-foreground">边缘羽化</span>
                <Slider
                  value={feather}
                  min={0}
                  max={0.1}
                  step={0.005}
                  onValueChange={(v) => setFeather(Array.isArray(v) ? v[0] : (v as number))}
                />
                <span className="w-10 flex-none text-right text-[11px] tabular-nums text-muted-foreground">
                  {(feather * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex items-center gap-2" title="固定随机种子可复现同一批结果；留空随机（纯 eraser 模型无此参数）">
                <span className="flex-none text-[11.5px] font-semibold text-secondary-foreground">Seed</span>
                <input
                  value={seedText}
                  onChange={(e) => setSeedText(e.target.value.replace(/[^0-9]/g, ""))}
                  placeholder="随机"
                  className="h-8 w-[110px] rounded-[8px] border border-border bg-panel px-2 text-[12px] outline-none focus:border-primary"
                />
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );

}
