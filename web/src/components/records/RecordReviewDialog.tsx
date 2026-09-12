"use client";
import { REVIEW_TAGS, REVIEW_STATUS } from './presentation';

import { Dialog, DialogContent } from "@/components/ui/dialog";

import { cn } from "@/lib/utils";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordReviewDialog({ review, setReview, doReviewSubmit }: Pick<RecordLibraryModel, "review" | "setReview" | "doReviewSubmit">) {
  return <>       {/* 人工评审标注 */}
    <Dialog
      open={review.open}
      onOpenChange={(o) => setReview((s) => ({ ...s, open: o }))}
    >
      <DialogContent>
        <div className="space-y-3">
          <div className="text-[15px] font-bold">人工评审标注</div>
          <div className="flex flex-wrap gap-2">
            {REVIEW_STATUS.map((s) => (
              <button
                key={s.value}
                onClick={() => setReview((v) => ({ ...v, status: s.value }))}
                className={cn(
                  "h-8 rounded-lg border px-3 text-[12.5px] font-semibold",
                  review.status === s.value
                    ? "border-transparent text-white"
                    : "border-border bg-card text-secondary-foreground hover:bg-accent",
                )}
                style={review.status === s.value ? { background: s.color } : undefined}
              >
                {s.label}
              </button>
            ))}
            <button
              onClick={() => setReview((v) => ({ ...v, best: !v.best }))}
              className={cn(
                "h-8 rounded-lg border px-3 text-[12.5px] font-semibold",
                review.best
                  ? "border-primary bg-primary text-white"
                  : "border-border bg-card text-secondary-foreground hover:bg-accent",
              )}
            >
              最佳图
            </button>
          </div>
          <div>
            <div className="mb-1.5 text-[12px] font-semibold text-muted-foreground">
              问题标签
            </div>
            <div className="flex flex-wrap gap-1.5">
              {REVIEW_TAGS.map((tag) => {
                const on = review.tags.includes(tag);
                return (
                  <button
                    key={tag}
                    onClick={() =>
                      setReview((v) => ({
                        ...v,
                        tags: on
                          ? v.tags.filter((x) => x !== tag)
                          : [...v.tags, tag],
                      }))
                    }
                    className={cn(
                      "rounded-full border px-3 py-[5px] text-[12px] font-semibold",
                      on
                        ? "border-primary bg-primary text-white"
                        : "border-border bg-card text-secondary-foreground hover:bg-accent",
                    )}
                  >
                    {tag}
                  </button>
                );
              })}
            </div>
          </div>
          <textarea
            value={review.note}
            onChange={(e) => setReview((s) => ({ ...s, note: e.target.value }))}
            placeholder="人工备注，例如：颜色准但空间略假，适合做备选"
            className="min-h-[90px] w-full resize-none rounded-[10px] border border-border bg-panel px-3 py-2 text-[13px] outline-none focus:ring-2 focus:ring-primary/20"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setReview((s) => ({ ...s, open: false }))}
              className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
            >
              取消
            </button>
            <button
              onClick={doReviewSubmit}
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
