"use client";

import { api } from "@/lib/api";

import { Dialog, DialogContent } from "@/components/ui/dialog";

import type { GenerationWorkspaceModel } from "./useGenerationWorkspace";
export function PreviewDialog({ preview, previewOpen, closePreview, setZoom }: Pick<GenerationWorkspaceModel, "preview" | "previewOpen" | "closePreview" | "setZoom">) {
  return <>      {/* 快速预览（NB2 Lite · 1K） */}
    <Dialog open={previewOpen} onOpenChange={closePreview}>
      <DialogContent className="max-w-[720px]">
        <div className="space-y-3">
          <div>
            <div className="text-[15.5px] font-bold">快速预览 · 1K 草稿</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              Nano Banana 2 Lite · 满意就关掉点「生成效果图」出 4K
            </div>
          </div>

          {(!preview || preview.status === "running") && (
            <div className="flex flex-col items-center justify-center gap-3 py-14 text-[13px] text-muted-foreground">
              <svg
                width="26"
                height="26"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.4"
                strokeLinecap="round"
                className="animate-dc-spin text-primary"
              >
                <path d="M21 12a9 9 0 1 1-6.2-8.6" />
              </svg>
              <span>{preview?.stage || "生成中…"}</span>
            </div>
          )}

          {preview?.status === "failed" && (
            <div className="rounded-[9px] bg-destructive-soft px-[12px] py-[10px] text-[12.5px] leading-relaxed text-destructive-ink">
              预览失败：{preview.error || "未知错误"}
            </div>
          )}

          {preview?.status === "done" && preview.url && (
            <>
              <div
                className="cursor-zoom-in overflow-hidden rounded-[10px] border border-border"
                onClick={() => setZoom(api.imgUrl(preview.url))}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={api.imgUrl(preview.thumb || preview.url)}
                  alt="快速预览"
                  className="w-full object-contain"
                />
              </div>
              <div className="flex items-center justify-between text-[11.5px] text-muted-foreground">
                <span>点击图片放大 · 1K 预览（非最终画质）</span>
                <a
                  href={api.imgUrl(preview.url)}
                  target="_blank"
                  rel="noreferrer"
                  className="hover:text-foreground"
                >
                  ↗ 原图
                </a>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>

  </>;
}
