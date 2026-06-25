/* eslint-disable @next/next/no-img-element */
"use client";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { GenParams, ModelFilter, OptionsView, Swatch } from "@/lib/types";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const short = (s: string) => s.split(" (")[0];

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <Select value={value} onValueChange={(v) => onChange(v ?? "")}>
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function RefUpload({
  value,
  onPick,
}: {
  value: Swatch | null;
  onPick: (s: Swatch) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  async function up(f: File) {
    setBusy(true);
    try {
      const s = await api.uploadRef(f);
      onPick(s);
      toast.success("参照图已上传");
    } catch (e) {
      toast.error("上传失败：" + (e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => ref.current?.click()}
      >
        {busy ? "上传中…" : "上传参照图"}
      </Button>
      <input
        ref={ref}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) up(f);
        }}
      />
      {value && (
        <img
          src={api.imgUrl(value.thumb)}
          alt="ref"
          className="h-10 w-14 rounded object-cover"
        />
      )}
    </div>
  );
}

export function ParamsForm({
  options,
  params,
  modelFilter,
  onParams,
  onModelFilter,
  refValue,
  onRefPick,
}: {
  options: OptionsView;
  params: GenParams;
  modelFilter: ModelFilter;
  onParams: (patch: Partial<GenParams>) => void;
  onModelFilter: (m: ModelFilter) => void;
  refValue: Swatch | null;
  onRefPick: (s: Swatch) => void;
}) {
  const cnMode = !!params.cn_mode;
  const isRef = params.workflow_mode.includes("参照模式");

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">工作流</Label>
        <div className="grid grid-cols-2 gap-1.5">
          {options.workflow_modes.map((m) => (
            <Button
              key={m}
              type="button"
              size="sm"
              variant={params.workflow_mode === m ? "default" : "outline"}
              onClick={() => onParams({ workflow_mode: m })}
              className="justify-start"
            >
              {short(m)}
            </Button>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">市场</Label>
        <Tabs
          value={cnMode ? "cn" : "overseas"}
          onValueChange={(v) => onParams({ cn_mode: v === "cn" })}
        >
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="overseas">海外</TabsTrigger>
            <TabsTrigger value="cn">国内</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">模型</Label>
        <div className="grid grid-cols-3 gap-1.5">
          {options.model_filters.map((m) => (
            <Button
              key={m.value}
              type="button"
              size="sm"
              variant={modelFilter === m.value ? "default" : "outline"}
              onClick={() => onModelFilter(m.value)}
            >
              {m.label}
            </Button>
          ))}
        </div>
      </div>

      <SelectField
        label="房间类型"
        value={
          cnMode
            ? params.cn_room_type || options.cn_room_types[0]
            : params.room_type || options.room_types[0]
        }
        onChange={(v) =>
          onParams(cnMode ? { cn_room_type: v } : { room_type: v })
        }
        options={cnMode ? options.cn_room_types : options.room_types}
      />

      <div className="grid grid-cols-2 gap-3">
        <SelectField
          label="画质"
          value={params.resolution || options.resolutions[0]}
          onChange={(v) => onParams({ resolution: v })}
          options={options.resolutions}
        />
        <SelectField
          label="拼缝"
          value={params.seam_type || options.seam_types[0]}
          onChange={(v) => onParams({ seam_type: v })}
          options={options.seam_types}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <SelectField
          label="光泽度"
          value={params.glossiness || options.glossiness[1] || options.glossiness[0]}
          onChange={(v) => onParams({ glossiness: v })}
          options={options.glossiness}
        />
        <SelectField
          label="色调"
          value={params.floor_tone || options.floor_tones[0]}
          onChange={(v) => onParams({ floor_tone: v })}
          options={options.floor_tones}
        />
      </div>

      {isRef && (
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">
            参照风格图（参照模式必传）
          </Label>
          <RefUpload value={refValue} onPick={onRefPick} />
        </div>
      )}

      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">自定义补充（可选）</Label>
        <Textarea
          rows={2}
          value={params.custom_addition || ""}
          onChange={(e) => onParams({ custom_addition: e.target.value })}
          placeholder="例如：增加一盏落地灯、暖色调灯光…"
        />
      </div>
    </div>
  );
}
