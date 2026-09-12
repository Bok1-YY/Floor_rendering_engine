"use client";

import { Dialog, DialogContent } from "@/components/ui/dialog";

import type { GenerationWorkspaceModel } from "./useGenerationWorkspace";
export function RecipeDialog({ recipeDlg, setRecipeDlg, submitRecipeDlg }: Pick<GenerationWorkspaceModel, "recipeDlg" | "setRecipeDlg" | "submitRecipeDlg">) {
  return <>      {/* 我的配方：命名 / 改名 */}
    <Dialog
      open={recipeDlg.open}
      onOpenChange={(o) => setRecipeDlg((s) => ({ ...s, open: o }))}
    >
      <DialogContent className="max-w-[420px]">
        <div className="space-y-3">
          <div>
            <div className="text-[15.5px] font-bold">
              {recipeDlg.id ? "配方改名" : "存为配方"}
            </div>
            {!recipeDlg.id && (
              <div className="mt-0.5 text-[12px] text-muted-foreground">
                保存当前全部场景参数（不含地板图与识色色调），下次一键套用
              </div>
            )}
          </div>
          <input
            value={recipeDlg.name}
            onChange={(e) => setRecipeDlg((s) => ({ ...s, name: e.target.value }))}
            onKeyDown={(e) => e.key === "Enter" && submitRecipeDlg()}
            maxLength={40}
            placeholder="配方名，例如：北欧客厅·顺光"
            className="h-10 w-full rounded-[10px] border border-border bg-panel px-3 text-[13px] outline-none focus:ring-2 focus:ring-primary/20"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setRecipeDlg({ open: false, id: "", name: "" })}
              className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
            >
              取消
            </button>
            <button
              onClick={submitRecipeDlg}
              className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover"
            >
              保存
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>

  </>;
}
