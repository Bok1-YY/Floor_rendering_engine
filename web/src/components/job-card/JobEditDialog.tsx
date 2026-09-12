"use client";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

import type { JobCardModel } from "./useJobCard";
export function JobEditDialog({
  editOpen,
  setEditOpen,
  editText,
  setEditText,
  editColorMatch,
  setEditColorMatch,
  doEditSubmit
}: Pick<JobCardModel, "editOpen" | "setEditOpen" | "editText" | "setEditText" | "editColorMatch" | "setEditColorMatch" | "doEditSubmit">) {
  return <>       <Dialog open={editOpen} onOpenChange={setEditOpen}>
    <DialogContent>
      <div className="space-y-3">
        <div className="text-[15px] font-bold">二改（对成图做图生图编辑）</div>
        <Input
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          placeholder="编辑指令，例如：把墙换成米白色"
          className="h-10 rounded-[10px] bg-panel"
        />
        <label className="flex cursor-pointer items-start gap-2 text-[12.5px]">
          <input
            type="checkbox"
            checked={editColorMatch}
            onChange={(e) => setEditColorMatch(e.target.checked)}
            className="mt-[3px] accent-[var(--primary)]"
          />
          <span>
            <span className="font-semibold text-foreground">保持原图色彩（防偏色）</span>
            <span className="mt-0.5 block leading-snug text-muted-foreground">
              二改后自动把整体色温/饱和度拉回原图，消除偏色。若本次就是想改颜色，请取消勾选。
            </span>
          </span>
        </label>
        <div className="flex justify-end gap-2">
          <button
            onClick={() => setEditOpen(false)}
            className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
          >
            取消
          </button>
          <button
            onClick={doEditSubmit}
            className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover"
          >
            提交
          </button>
        </div>
      </div>
    </DialogContent>
  </Dialog>
  </>;
}
