"use client";

import { useState } from "react";
import { ChevronDown, Cuboid, Globe2, Sparkles } from "lucide-react";
import type { GenParams, ModelKey, OptionsView, SDOptions } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

function OutputField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className="text-[11.5px] font-semibold text-muted-foreground">{label}</span>
      <Select value={value} onValueChange={(next) => onChange(next ?? "")}>
        <SelectTrigger className="h-10 w-full rounded-[10px] bg-card text-[13px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>{option}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}

export function OutputForm({
  options,
  params,
  modelTargets,
  sdOptions,
  sdEnabled,
  onParams,
  onModelTargets,
  onSDOptions,
}: {
  options: OptionsView;
  params: GenParams;
  modelTargets: ModelKey[];
  sdOptions: SDOptions;
  sdEnabled: boolean;
  onParams: (patch: Partial<GenParams>) => void;
  onModelTargets: (models: ModelKey[]) => void;
  onSDOptions: (patch: Partial<SDOptions>) => void;
}) {
  const [sdOpen, setSdOpen] = useState(false);
  const isFree = params.workflow_mode.includes("自由创作");
  const isSphere = params.workflow_mode.includes("球面效果图");

  const toggleModel = (key: ModelKey) => {
    const selected = modelTargets.includes(key);
    const disabled = key === "sd35" && !selected && (!sdEnabled || !params.workflow_mode.includes("纯效果图"));
    if (disabled) return;
    onModelTargets(selected ? modelTargets.filter((model) => model !== key) : [...modelTargets, key]);
  };

  if (isSphere) {
    return (
      <div className="space-y-3">
        <div className="rounded-xl border border-primary/35 bg-primary-soft p-3.5">
          <div className="flex items-start gap-3">
            <div className="flex size-10 flex-none items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Globe2 size={19} />
            </div>
            <div className="min-w-0">
              <div className="text-[13px] font-extrabold text-foreground">双引擎球面候选</div>
              <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
                B2 与 GPT Image 2 各生成一张同球心 3×2 六面图集，再由本地算法拆成六面并合成全景。
              </p>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-border bg-card px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[11.5px] font-bold"><Sparkles size={13} />B2</div>
              <div className="mt-1 text-[10.5px] text-muted-foreground">六面图集候选 A</div>
            </div>
            <div className="rounded-lg border border-border bg-card px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[11.5px] font-bold"><Sparkles size={13} />GPT Image 2</div>
              <div className="mt-1 text-[10.5px] text-muted-foreground">六面图集候选 B</div>
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-[11px]">
          <div className="rounded-xl border border-border bg-card px-3 py-2.5">
            <div className="flex items-center gap-1.5 font-bold text-secondary-foreground"><Cuboid size={13} />中间格式</div>
            <div className="mt-1 leading-relaxed text-muted-foreground">3×2 · 六个 90° 方向</div>
          </div>
          <div className="rounded-xl border border-border bg-card px-3 py-2.5">
            <div className="flex items-center gap-1.5 font-bold text-secondary-foreground"><Globe2 size={13} />最终交付</div>
            <div className="mt-1 leading-relaxed text-muted-foreground">3840×1920 · 2:1 ERP</div>
          </div>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          此模式的引擎、图集比例和 ERP 尺寸为固定合同；点击生成后会先显示费用与两次调用上限，确认后才提交。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-[14px]">
      <div>
        <div className="mb-[7px] text-[11.5px] font-semibold text-muted-foreground">模型线路</div>
        <div className={cn("grid gap-2", isFree ? "grid-cols-2" : "grid-cols-3")}>
          {([[
            "b2", "B2",
          ], ["pro", "Pro"], ["sd35", "SD 3.5"]] as const)
            .filter(([key]) => !isFree || key !== "sd35")
            .map(([key, label]) => {
              const active = modelTargets.includes(key);
              const disabled = key === "sd35" && !active && (!sdEnabled || !params.workflow_mode.includes("纯效果图"));
              return (
                <button
                  key={key}
                  type="button"
                  disabled={disabled}
                  onClick={() => toggleModel(key)}
                  title={disabled ? (!sdEnabled ? "请先在设置中启用 SD 3.5" : "SD 3.5 当前仅支持纯效果图") : `选择 ${label}`}
                  className={cn(
                    "h-10 rounded-[10px] border text-[12.5px] font-bold transition-colors",
                    active
                      ? "border-primary bg-primary-soft text-accent-foreground"
                      : "border-border bg-card text-secondary-foreground hover:bg-accent",
                    disabled && "cursor-not-allowed opacity-45",
                  )}
                >
                  {active ? `✓ ${label}` : label}
                </button>
              );
            })}
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
          {isFree
            ? "B2 / Pro 会收到完全相同的自由指令与图片顺序。"
            : "可同时选择多个模型并行生成；SD 3.5 使用独立提示词与地板参考图。"}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-[11px]">
        <OutputField
          label="画面比例"
          value={params.aspect_ratio || options.aspect_ratios[0]}
          options={options.aspect_ratios}
          onChange={(aspect_ratio) => onParams({ aspect_ratio })}
        />
        <OutputField
          label="画质"
          value={params.resolution || options.resolutions[0]}
          options={options.resolutions}
          onChange={(resolution) => onParams({ resolution })}
        />
      </div>

      {modelTargets.includes("sd35") && (
        <div className="overflow-hidden rounded-xl border border-border bg-panel">
          <button
            type="button"
            onClick={() => setSdOpen((open) => !open)}
            className="flex w-full items-center justify-between px-[13px] py-[11px] text-left"
          >
            <span className="text-[12.5px] font-bold text-secondary-foreground">SD 3.5 高级参数</span>
            <ChevronDown size={15} className={cn("text-muted-foreground transition-transform", sdOpen && "rotate-180")} />
          </button>
          {sdOpen && (
            <div className="grid grid-cols-2 gap-[11px] border-t border-border p-[13px]">
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">Seed（留空为随机）</span>
                <Input type="number" min={0} value={sdOptions.seed ?? ""} onChange={(e) => onSDOptions({ seed: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })} className="h-10 rounded-[10px] bg-card" />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">采样步数（10–50）</span>
                <Input type="number" min={10} max={50} value={sdOptions.steps} onChange={(e) => onSDOptions({ steps: Number(e.target.value) })} className="h-10 rounded-[10px] bg-card" />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">CFG（1–10）</span>
                <Input type="number" min={1} max={10} step={0.1} value={sdOptions.guidance_scale} onChange={(e) => onSDOptions({ guidance_scale: Number(e.target.value) })} className="h-10 rounded-[10px] bg-card" />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">地板参考强度（0.1–1）</span>
                <Input type="number" min={0.1} max={1} step={0.05} value={sdOptions.reference_strength} onChange={(e) => onSDOptions({ reference_strength: Number(e.target.value) })} className="h-10 rounded-[10px] bg-card" />
              </label>
              <label className="col-span-2 flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">正向追加</span>
                <Textarea rows={2} value={sdOptions.positive_addition} onChange={(e) => onSDOptions({ positive_addition: e.target.value })} className="rounded-[9px] bg-card" />
              </label>
              <label className="col-span-2 flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">负向追加</span>
                <Textarea rows={2} value={sdOptions.negative_addition} onChange={(e) => onSDOptions({ negative_addition: e.target.value })} className="rounded-[9px] bg-card" />
              </label>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
