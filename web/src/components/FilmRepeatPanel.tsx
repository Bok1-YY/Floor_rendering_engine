/* eslint-disable @next/next/no-img-element */
"use client";

import { useRef, useState } from "react";
import { Film, LoaderCircle, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { FilmRepeatContract, GenParams } from "@/lib/types";

const fileName = (value?: string) => String(value || "").split(/[\\/]/).pop() || "";

export default function FilmRepeatPanel({
  params,
  onParams,
}: {
  params: GenParams;
  onParams: (patch: Partial<GenParams>) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [contract, setContract] = useState<FilmRepeatContract | null>(null);
  const name = fileName(params.film_path);
  const thumb = name ? `/thumb/uploads/${encodeURIComponent(name)}?s=480` : "";

  async function upload(file: File) {
    setBusy(true);
    try {
      const result = await api.uploadFilm(file);
      onParams({
        film_path: result.path,
        film_width_mm: params.film_width_mm || 984,
        film_repeat_length_mm: params.film_repeat_length_mm || 1890,
        film_repeat_axis: "long_edge",
      });
      toast.success("原厂彩膜已上传；VR 将按物理周期分切，不再生成新木纹");
    } catch (error) {
      toast.error(`彩膜上传失败：${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  function numberValue(value: number | null | undefined) {
    return value == null ? "" : String(value);
  }

  async function analyze() {
    if (!params.film_path || !params.film_width_mm || !params.film_repeat_length_mm || !params.floor_size) return;
    setAnalyzing(true);
    try {
      const value = await api.analyzeFilm({
        film_path: params.film_path,
        film_width_mm: params.film_width_mm,
        film_repeat_length_mm: params.film_repeat_length_mm,
        floor_size: params.floor_size,
        seam_type: params.seam_type || "无缝拼接 (SPC/LVT专用)",
        film_slit_origin_mm: params.film_slit_origin_mm,
      });
      setContract(value);
      toast.success(value.manifest.status === "ready" ? "彩膜周期和物理分切验证通过" : "彩膜周期未通过，请检查尺寸");
    } catch (error) {
      setContract(null);
      toast.error(`彩膜校验失败：${(error as Error).message}`);
    } finally {
      setAnalyzing(false);
    }
  }

  return (
    <div className="mt-3 rounded-xl border border-border bg-panel p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-[12.5px] font-bold">
            <Film size={14} />原厂整体彩膜 · VR 精确铺贴
          </div>
          <div className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">
            可选。提供生产彩膜和毫米周期后，本地按卷材真实分切；不会把实拍小样整图循环，也不会调用 AI 生成木纹。
          </div>
        </div>
        {params.film_path && (
          <button
            type="button"
            title="移除彩膜"
            onClick={() => onParams({ film_path: "", film_width_mm: null, film_repeat_length_mm: null, film_slit_origin_mm: null })}
            className="rounded-lg border border-border bg-card p-2 text-muted-foreground hover:text-destructive"
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void upload(file);
        }}
      />

      {params.film_path ? (
        <div className="mt-3 flex items-center gap-3 rounded-lg border border-border bg-card p-2.5">
          <img src={api.imgUrl(thumb)} alt="原厂彩膜" className="h-16 w-12 flex-none rounded-md border border-border object-cover" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[11.5px] font-bold">{name}</div>
            <div className="mt-1 text-[10px] text-muted-foreground">长边纵向连续 · 标签自动识别并避让</div>
          </div>
          <button type="button" onClick={() => inputRef.current?.click()} className="rounded-lg border border-border px-2.5 py-1.5 text-[10.5px] font-bold hover:bg-accent">
            更换
          </button>
        </div>
      ) : (
        <button
          type="button"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-border-strong bg-card px-3 py-3 text-[11.5px] font-bold text-secondary-foreground hover:border-primary hover:bg-primary-soft disabled:opacity-60"
        >
          {busy ? <LoaderCircle size={14} className="animate-dc-spin" /> : <Upload size={14} />}
          {busy ? "上传彩膜中…" : "上传原厂 Full Layout 彩膜"}
        </button>
      )}

      {params.film_path && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <label className="text-[10.5px] font-semibold text-muted-foreground">
            彩膜宽度 mm
            <input
              type="number"
              min={100}
              max={10000}
              step="0.1"
              value={numberValue(params.film_width_mm)}
              onChange={(event) => onParams({ film_width_mm: event.target.value ? Number(event.target.value) : null })}
              className="mt-1 h-9 w-full rounded-lg border border-border bg-card px-2.5 text-[12px] text-foreground outline-none focus:border-primary"
            />
          </label>
          <label className="text-[10.5px] font-semibold text-muted-foreground">
            长边重复周期 mm
            <input
              type="number"
              min={100}
              max={20000}
              step="0.1"
              value={numberValue(params.film_repeat_length_mm)}
              onChange={(event) => onParams({ film_repeat_length_mm: event.target.value ? Number(event.target.value) : null })}
              className="mt-1 h-9 w-full rounded-lg border border-border bg-card px-2.5 text-[12px] text-foreground outline-none focus:border-primary"
            />
          </label>
          <label className="col-span-2 text-[10.5px] font-semibold text-muted-foreground">
            分切起点 mm（留空＝按板宽自动居中排带）
            <input
              type="number"
              min={0}
              max={10000}
              step="0.1"
              placeholder="自动"
              value={numberValue(params.film_slit_origin_mm)}
              onChange={(event) => onParams({ film_slit_origin_mm: event.target.value ? Number(event.target.value) : null })}
              className="mt-1 h-9 w-full rounded-lg border border-border bg-card px-2.5 text-[12px] text-foreground outline-none focus:border-primary"
            />
          </label>
        </div>
      )}
      {params.film_path && (
        <button
          type="button"
          disabled={analyzing || !params.film_width_mm || !params.film_repeat_length_mm || !params.floor_size}
          onClick={() => void analyze()}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2.5 text-[11.5px] font-bold text-primary-foreground disabled:opacity-50"
        >
          {analyzing && <LoaderCircle size={14} className="animate-dc-spin" />}
          {analyzing ? "正在校验周期和分切…" : "免费校验彩膜周期与分切"}
        </button>
      )}
      {contract && (
        <div className="mt-3 grid gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-2.5 text-emerald-950 sm:grid-cols-[112px_1fr]">
          <img src={`data:image/png;base64,${contract.guide_b64}`} alt="彩膜分切预览" className="aspect-square w-full rounded-md border border-emerald-200 object-cover" />
          <div className="space-y-1 text-[10.5px] leading-relaxed">
            <div className="font-bold">{contract.manifest.status === "ready" ? "周期验证通过" : "周期验证失败"}</div>
            <div>{contract.manifest.slitting.lane_count} 条分切通道 · 起点 {contract.manifest.slitting.slit_origin_mm}mm</div>
            <div>{contract.manifest.phase_state_count} 个纵向相位 · {contract.manifest.effective_board_states} 个有效板状态</div>
            <div>首尾 X 配准 {contract.manifest.repeat_registration.translation_px_x}px · 标签禁区 {contract.manifest.exclusion_rects.length} 个</div>
            <div className="text-emerald-900/65">本地分析与预览，不调用 Gemini/Fal</div>
          </div>
        </div>
      )}
    </div>
  );
}
