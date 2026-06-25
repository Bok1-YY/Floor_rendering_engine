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
    <div className="space-y-3">
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files?.[0];
          if (f) handleFile(f);
        }}
        className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30 bg-muted/30 p-5 text-center text-sm hover:border-primary/50"
      >
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
        {busy ? "上传中…" : "📁 点击或拖拽上传地板小样"}
      </div>

      {value && (
        <div className="flex items-center gap-3 rounded-lg border bg-background p-2">
          {/* 用普通 img：图源是 127.0.0.1 本地后端，next/image 在 Next16 默认拦本地 IP */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={api.imgUrl(value.thumb)}
            alt={value.name}
            className="h-14 w-20 rounded object-cover"
          />
          <div className="min-w-0 text-xs">
            <div className="truncate font-medium">{value.name}</div>
            <div className="text-muted-foreground">已选地板</div>
          </div>
        </div>
      )}

      {recent.length > 0 && (
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              最近小样
            </span>
            <button
              onClick={openMore}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              更多历史 →
            </button>
          </div>
          <div className="grid grid-cols-6 gap-1.5">
            {recent.map((s) => (
              <button
                key={s.path}
                onClick={() => onPick(s)}
                title={s.name}
                className={cn(
                  "overflow-hidden rounded border",
                  value?.path === s.path
                    ? "ring-2 ring-primary"
                    : "hover:opacity-80",
                )}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
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
          <div className="text-sm font-medium">历史小样（点选即用）</div>
          <div className="mt-2 grid grid-cols-5 gap-2 sm:grid-cols-6">
            {more.map((s) => (
              <button
                key={s.path}
                onClick={() => {
                  onPick(s);
                  setMoreOpen(false);
                }}
                title={s.name}
                className="overflow-hidden rounded border hover:opacity-80"
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
