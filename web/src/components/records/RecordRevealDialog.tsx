"use client";

import { Input } from "@/components/ui/input";
import { Dialog, DialogContent } from "@/components/ui/dialog";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordRevealDialog({ reveal, setReveal, doReveal }: Pick<RecordLibraryModel, "reveal" | "setReveal" | "doReveal">) {
  return <>       {/* 解密 */}
    <Dialog
      open={reveal.open}
      onOpenChange={(o) => setReveal((s) => ({ ...s, open: o }))}
    >
      <DialogContent>
        <div className="space-y-3">
          <div className="text-[15px] font-bold">解密原始提示词</div>
          <Input
            type="password"
            value={reveal.pw}
            onChange={(e) => setReveal((s) => ({ ...s, pw: e.target.value }))}
            placeholder="输入密码"
            className="h-10 rounded-[10px] bg-panel"
          />
          <div className="flex justify-end">
            <button
              onClick={doReveal}
              className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover"
            >
              🔓 解密
            </button>
          </div>
          {reveal.text && (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-[10px] bg-accent p-3 text-xs text-foreground">
              {reveal.text}
            </pre>
          )}
        </div>
      </DialogContent>
    </Dialog>

  </>;
}
