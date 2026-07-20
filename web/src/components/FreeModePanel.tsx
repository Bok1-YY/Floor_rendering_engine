/* eslint-disable @next/next/no-img-element */
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Swatch } from "@/lib/types";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

export function FreeModePanel({
  prompt,
  images,
  onPrompt,
  onImages,
}: {
  prompt: string;
  images: Swatch[];
  onPrompt: (value: string) => void;
  onImages: (value: Swatch[]) => void;
}) {
  const [busySlot, setBusySlot] = useState<number | null>(null);

  async function upload(file: File, index: number) {
    setBusySlot(index);
    try {
      const next = [...images];
      const uploaded = await api.uploadRef(file);
      if (index < next.length) next[index] = uploaded;
      else next.push(uploaded);
      onImages(next.slice(0, 3));
      toast.success(`Slot ${index + 1} 已上传`);
    } catch (error) {
      toast.error("上传失败：" + (error as Error).message);
    } finally {
      setBusySlot(null);
    }
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= images.length) return;
    const next = [...images];
    [next[index], next[target]] = [next[target], next[index]];
    onImages(next);
  }

  return (
    <div className="mt-[13px] space-y-4 rounded-xl border border-primary/40 bg-primary-soft p-[13px]">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11.5px] font-semibold text-accent-foreground">
            自由指令词（必填）
          </span>
          <span className="text-[10.5px] text-muted-foreground">{prompt.length}/10000</span>
        </div>
        <Textarea
          rows={7}
          maxLength={10000}
          value={prompt}
          onChange={(event) => onPrompt(event.target.value)}
          placeholder="直接输入要发给 B2 / Pro 的完整中文或英文指令……"
          className="rounded-[9px] bg-card"
        />
        <p className="text-[10.5px] leading-relaxed text-muted-foreground">
          系统会原样发送，不翻译、不追加内置提示词。可在指令中使用“第一张图 / 第二张图 / 第三张图”指定用途。
        </p>
      </div>

      <div>
        <div className="mb-2 text-[11.5px] font-semibold text-accent-foreground">
          参考图 Slot（至少 1 张，最多 3 张）
        </div>
        <div className="grid grid-cols-3 gap-2.5">
          {[0, 1, 2].map((index) => {
            const value = images[index];
            const enabled = index <= images.length;
            return (
              <div key={index} className="rounded-[10px] border border-border bg-card p-2">
                <div className="mb-1.5 text-[10.5px] font-bold text-secondary-foreground">
                  Slot {index + 1}
                </div>
                {value ? (
                  <>
                    <img
                      src={api.imgUrl(value.thumb)}
                      alt={`Slot ${index + 1}`}
                      className="aspect-[4/3] w-full rounded-md border border-border object-cover"
                    />
                    <div className="mt-2 grid grid-cols-2 gap-1">
                      <label className="cursor-pointer rounded-md border border-border px-1.5 py-1 text-center text-[10.5px] font-semibold hover:bg-accent">
                        {busySlot === index ? "上传中…" : "替换"}
                        <input
                          type="file"
                          accept="image/*"
                          className="hidden"
                          disabled={busySlot !== null}
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            event.target.value = "";
                            if (file) upload(file, index);
                          }}
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() => onImages(images.filter((_, i) => i !== index))}
                        className="rounded-md border border-border px-1.5 py-1 text-[10.5px] font-semibold hover:bg-accent"
                      >
                        删除
                      </button>
                      <button
                        type="button"
                        disabled={index === 0}
                        onClick={() => move(index, -1)}
                        className="rounded-md border border-border px-1.5 py-1 text-[10.5px] disabled:opacity-30 hover:bg-accent"
                      >
                        ← 前移
                      </button>
                      <button
                        type="button"
                        disabled={index >= images.length - 1}
                        onClick={() => move(index, 1)}
                        className="rounded-md border border-border px-1.5 py-1 text-[10.5px] disabled:opacity-30 hover:bg-accent"
                      >
                        后移 →
                      </button>
                    </div>
                  </>
                ) : (
                  <label
                    className={`flex aspect-[4/3] items-center justify-center rounded-md border border-dashed px-2 text-center text-[11px] font-semibold ${
                      enabled
                        ? "cursor-pointer border-primary/60 text-accent-foreground hover:bg-accent"
                        : "cursor-not-allowed border-border text-muted-foreground opacity-45"
                    }`}
                  >
                    {busySlot === index ? "上传中…" : enabled ? "+ 上传图片" : "请先填前一槽"}
                    <input
                      type="file"
                      accept="image/*"
                      className="hidden"
                      disabled={!enabled || busySlot !== null}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (file) upload(file, index);
                      }}
                    />
                  </label>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
