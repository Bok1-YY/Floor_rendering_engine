/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Swatch } from "@/lib/types";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent } from "@/components/ui/dialog";

export function FloorUploader({
  value,
  onPick,
}: {
  value: Swatch | null;
  onPick: (s: Swatch) => void;
}) {
  const [recent, setRecent] = useState<Swatch[]>([]);
  const [busy, setBusy] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [more, setMore] = useState<Swatch[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  function openMore() {
    setMoreOpen(true);
    api.recentSwatches(200).then(setMore).catch(() => {});
  }

  const loadRecent = () =>
    api.recentSwatches(18).then(setRecent).catch(() => {});
  useEffect(() => {
    loadRecent();
  }, []);

  async function handleFile(file: File) {
    setBusy(true);
    try {
      const s = await api.uploadFloor(file);
      onPick(s);
      toast.success("地板图已上传");
      loadRecent();
    } catch (e) {
      toast.error("上传失败：" + (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2.5">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) handleFile(f);
        }}
      />

      {!value ? (
        <div
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f) handleFile(f);
          }}
          className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-[13px] border-2 border-dashed border-[#d3c8b3] bg-card px-4 py-[26px] text-center transition hover:border-primary hover:bg-[#fbf6ee]"
        >
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#c15f3c" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v3a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-3" />
            <path d="M12 3v13M7.5 7.5L12 3l4.5 4.5" />
          </svg>
          <div className="text-[13.5px] font-bold text-[#2a241f]">
            {busy ? "上传中…" : "点击或拖拽上传地板小样"}
          </div>
          <div className="text-[11.5px] text-[#9a9082]">
            支持 JPG / PNG · 上传后自动识别色调并推荐配方
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-[13px] border border-border bg-card p-[11px]">
          <img
            src={api.imgUrl(value.thumb)}
            alt={value.name}
            className="h-12 w-16 flex-none rounded-[9px] border border-border object-cover"
          />
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[13px] font-bold text-[#2a241f]">
              {value.name}
            </div>
            <div className="mt-[3px] inline-flex items-center gap-1.5 rounded-full bg-[#f2e9e0] px-2 py-0.5 text-[11px] font-semibold text-[#a8472a]">
              已选地板
            </div>
          </div>
          <button
            onClick={() => inputRef.current?.click()}
            className="h-[30px] flex-none rounded-lg border border-border bg-card px-[11px] text-[12px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
          >
            重新上传
          </button>
        </div>
      )}

      {recent.length > 0 && (
        <div className="mt-2.5">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11px] font-semibold text-[#9a9082]">最近小样</span>
            <button
              onClick={openMore}
              className="text-[11px] text-[#9a9082] hover:text-foreground"
            >
              更多历史 →
            </button>
          </div>
          <div className="grid grid-cols-8 gap-1.5">
            {recent.map((s) => (
              <button
                key={s.path}
                onClick={() => onPick(s)}
                title={s.name}
                className={cn(
                  "overflow-hidden rounded-lg border",
                  value?.path === s.path
                    ? "border-primary ring-2 ring-primary/20"
                    : "border-border hover:opacity-80",
                )}
              >
                <img
                  src={api.imgUrl(s.thumb)}
                  alt={s.name}
                  className="aspect-square w-full object-cover"
                />
              </button>
            ))}
          </div>
        </div>
      )}

      <Dialog open={moreOpen} onOpenChange={setMoreOpen}>
        <DialogContent className="max-h-[80vh] max-w-2xl overflow-auto">
          <div className="text-[15px] font-bold">历史小样（点选即用）</div>
          <div className="mt-2 grid grid-cols-5 gap-2 sm:grid-cols-6">
            {more.map((s) => (
              <button
                key={s.path}
                onClick={() => {
                  onPick(s);
                  setMoreOpen(false);
                }}
                title={s.name}
                className="overflow-hidden rounded-lg border border-border hover:opacity-80"
              >
                <img
                  src={api.imgUrl(s.thumb)}
                  alt={s.name}
                  className="aspect-square w-full object-cover"
                />
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
