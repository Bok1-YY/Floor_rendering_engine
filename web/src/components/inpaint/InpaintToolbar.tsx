"use client";


import { Slider } from "@/components/ui/slider";

import { cn } from "@/lib/utils";
import type { useInpaintSession } from "./useInpaintSession";
export function InpaintToolbar(vm: ReturnType<typeof useInpaintSession>) {
  const { nCount, tool, brush, mode, scanCandidates, canUndo, hasMask, advancedOpen } = vm.state;
  const { changeMode, setTool, setSmartMessage, setBrush, undo, clearMask, setNCount, setAdvancedOpen } = vm.actions;

  const { toolBtn, smartBusy, modeBtn, eraserRemove } = vm.display;

  return (<div className="flex flex-wrap items-center gap-2.5">
    <div className="flex items-center gap-1.5">
      <button className={modeBtn(mode === "remove")} onClick={() => changeMode("remove")}>
        🧹 生成式移除
      </button>
      <button className={modeBtn(mode === "add")} onClick={() => changeMode("add")}>
        ✨ 生成式添加
      </button>
    </div>
    <div className="flex items-center gap-1">
      <button
        className={`${toolBtn} ${tool === "smart" ? "border-primary text-primary" : ""}`}
        onClick={() => {
          setTool("smart");
          setSmartMessage(mode === "add"
            ? "智能选区：点击地面、墙面或桌面，再用画笔收窄"
            : smartBusy
              ? "物件仍在后台识别，请稍等片刻；也可以切换画笔直接涂抹"
              : scanCandidates.length
                ? `已识别 ${scanCandidates.length} 个候选：点青色轮廓；点其他位置也会单独识别`
                : "点击图中物件即可识别；没有命中时可使用画笔补选");
        }}
        title={mode === "remove" ? "点击青色轮廓选择或取消物件" : "点击地面、墙面或桌面识别目标区域"}
      >
        {mode === "remove" ? "◎ 智能选物" : "◎ 智能选区"}
      </button>
      <button
        className={`${toolBtn} ${tool === "brush" ? "border-primary text-primary" : ""}`}
        onClick={() => setTool("brush")}
      >
        🖌 画笔
      </button>
      <button
        className={`${toolBtn} ${tool === "erase" ? "border-primary text-primary" : ""}`}
        onClick={() => setTool("erase")}
        title="擦掉智能或手工选区中多余的部分"
      >
        🧽 橡皮
      </button>
    </div>
    <div className="flex min-w-[180px] flex-1 items-center gap-2">
      <span className="flex-none text-[12px] font-semibold text-secondary-foreground">笔刷</span>
      <Slider
        value={brush}
        min={8}
        max={120}
        step={2}
        onValueChange={(v) => setBrush(Array.isArray(v) ? v[0] : (v as number))}
      />
      <span className="w-8 flex-none text-right text-[12px] tabular-nums text-muted-foreground">
        {brush}
      </span>
    </div>
    <button className={toolBtn} onClick={undo} disabled={!canUndo}>
      ↩ 撤销
    </button>
    <button className={toolBtn} onClick={clearMask} disabled={!hasMask}>
      清空
    </button>
    {/* 候选数：Lightroom 式一次多变体挑选 */}
    <div
      className="flex items-center gap-1 rounded-lg border border-border px-1.5 py-0.5"
      title="一次生成几个候选供挑选（按张计费）"
    >
      <span className="text-[11px] font-semibold text-muted-foreground">候选</span>
      {[1, 2, 3].map((k) => (
        <button
          key={k}
          onClick={() => setNCount(k)}
          disabled={eraserRemove && k > 1}
          className={cn(
            "rounded px-1.5 text-[11.5px] font-semibold",
            nCount === k
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-35",
          )}
          title={eraserRemove && k > 1 ? "专职移除模型不支持可控变体，避免重复计费" : undefined}
        >
          ×{k}
        </button>
      ))}
    </div>
    <button
      type="button"
      aria-expanded={advancedOpen}
      onClick={() => setAdvancedOpen((v) => !v)}
      className={toolBtn}
    >
      高级 {advancedOpen ? "收起" : "展开"}
    </button>
  </div>);
}
