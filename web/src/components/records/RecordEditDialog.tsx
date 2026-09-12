"use client";

import { Input } from "@/components/ui/input";
import { Dialog, DialogContent } from "@/components/ui/dialog";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordEditDialog({ edit, setEdit, doEditSubmit }: Pick<RecordLibraryModel, "edit" | "setEdit" | "doEditSubmit">) {
  return <>       {/* 记录内二改 */}
    <Dialog
      open={edit.open}
      onOpenChange={(o) => setEdit((s) => ({ ...s, open: o }))}
    >
      <DialogContent>
        <div className="space-y-3">
          <div className="text-[15px] font-bold">二改（对这张结果图做图生图编辑）</div>
          <Input
            value={edit.instruction}
            onChange={(e) =>
              setEdit((s) => ({ ...s, instruction: e.target.value }))
            }
            placeholder="编辑指令，例如：把沙发换成米白色布艺"
            className="h-10 rounded-[10px] bg-panel"
          />
          <label className="flex cursor-pointer items-start gap-2 text-[12.5px]">
            <input
              type="checkbox"
              checked={edit.colorMatch}
              onChange={(e) =>
                setEdit((s) => ({ ...s, colorMatch: e.target.checked }))
              }
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
              onClick={() => setEdit((s) => ({ ...s, open: false }))}
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
