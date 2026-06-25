/* eslint-disable @next/next/no-img-element */
"use client";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { GenParams, ModelFilter, OptionsView, Swatch } from "@/lib/types";
import { toast } from "sonner";
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
import { SectionHeader, Segmented } from "@/components/dc-ui";

const short = (s: string) => s.split(" (")[0];

const WF_SUB: Record<string, string> = {
  纯效果图: "用地板小样直接生成全新空间",
  参照模式: "按参照图的风格与氛围生成",
  宠物友好: "画面中加入宠物生活场景",
  实景替换: "替换实拍照片里的原地面",
};

function Check() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" className="flex-none">
      <path d="M20 6L9 17l-5-5" />
    </svg>
  );
}

function Field({
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
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-[11.5px] font-semibold text-[#857c6e]">{label}</span>
      <Select value={value} onValueChange={(v) => onChange(v ?? "")}>
        <SelectTrigger className="h-10 w-full rounded-[10px] bg-card text-[13px] text-[#2a241f]">
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

function Chips({
  label,
  options,
  selected,
  onToggle,
  labelClassName,
}: {
  label: string;
  options: string[];
  selected: string[];
  onToggle: (v: string) => void;
  labelClassName?: string;
}) {
  return (
    <div>
      <span className={cn("text-[11.5px] font-semibold text-[#857c6e]", labelClassName)}>
        {label}
      </span>
      <div className="mt-[7px] flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = selected.includes(o);
          return (
            <button
              key={o}
              type="button"
              onClick={() => onToggle(o)}
              className={cn(
                "rounded-full border px-3 py-[5px] text-[12px] font-semibold transition-colors",
                on
                  ? "border-primary bg-primary text-white"
                  : "border-border bg-card text-[#6b6356] hover:bg-[#f2e9e0]",
              )}
            >
              {o}
            </button>
          );
        })}
      </div>
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
      onPick(await api.uploadRef(f));
      toast.success("参照图已上传");
    } catch (e) {
      toast.error("上传失败：" + (e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex items-center gap-2.5">
      <button
        type="button"
        onClick={() => ref.current?.click()}
        className="h-9 rounded-[9px] border border-border bg-card px-3 text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
      >
        {busy ? "上传中…" : "上传参照图"}
      </button>
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
          className="h-10 w-14 rounded-md border border-border object-cover"
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
  const isPet = params.workflow_mode.includes("宠物友好");
  const [advOpen, setAdvOpen] = useState(false);

  // 地区级联
  const continent = params.continent || options.continents[0];
  const countries = Object.keys(options.location_map[continent] || {});
  const country = params.country || countries[0] || "";
  const cities = options.location_map[continent]?.[country] || [];
  const city = params.city || cities[0] || "";
  const onContinent = (v: string) => {
    const c0 = Object.keys(options.location_map[v] || {})[0] || "";
    const city0 = (options.location_map[v]?.[c0] || [])[0] || "";
    onParams({ continent: v, country: c0, city: city0 });
  };
  const onCountry = (v: string) => {
    const city0 = (options.location_map[continent]?.[v] || [])[0] || "";
    onParams({ country: v, city: city0 });
  };

  // 多选 toggle
  const toggle = (
    key: "avoid_items" | "cn_space_features" | "cn_facilities",
    v: string,
  ) => {
    const cur = params[key] ?? [];
    onParams({
      [key]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v],
    } as Partial<GenParams>);
  };

  return (
    <div>
      {/* ── 工作流 ── */}
      <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
        工作流 / WORKFLOW
      </SectionHeader>
      <div className="grid grid-cols-2 gap-[9px]">
        {options.workflow_modes.map((m) => {
          const active = params.workflow_mode === m;
          const name = short(m);
          return (
            <button
              key={m}
              type="button"
              onClick={() => onParams({ workflow_mode: m })}
              className={cn(
                "rounded-xl border p-[12px] text-left transition",
                active
                  ? "border-primary bg-[#fbf3ee] ring-[3px] ring-[rgba(193,95,60,.1)]"
                  : "border-border bg-card",
              )}
            >
              <div className="flex items-center justify-between gap-1.5">
                <span
                  className={cn(
                    "text-[13px] font-bold",
                    active ? "text-[#a8472a]" : "text-[#2a241f]",
                  )}
                >
                  {name}
                </span>
                {active && (
                  <span className="text-[#a8472a]">
                    <Check />
                  </span>
                )}
              </div>
              <div
                className={cn(
                  "mt-[5px] text-[11px] leading-snug",
                  active ? "text-[#bd7a5b]" : "text-[#9a9082]",
                )}
              >
                {WF_SUB[name] ?? ""}
              </div>
            </button>
          );
        })}
      </div>

      {/* ── 市场 + 模型线路 ── */}
      <div className="mt-[18px] flex gap-4">
        <div className="w-40 flex-none">
          <div className="mb-[7px] text-[11.5px] font-semibold text-[#857c6e]">市场</div>
          <Segmented
            value={cnMode ? "cn" : "overseas"}
            options={[
              { value: "overseas", label: "海外" },
              { value: "cn", label: "国内" },
            ]}
            onChange={(v) => onParams({ cn_mode: v === "cn" })}
          />
        </div>
        <div className="flex-1">
          <div className="mb-[7px] text-[11.5px] font-semibold text-[#857c6e]">模型线路</div>
          <Segmented<ModelFilter>
            value={modelFilter}
            options={options.model_filters.map((m) => ({
              value: m.value,
              label: m.label,
            }))}
            onChange={onModelFilter}
          />
        </div>
      </div>

      {/* ── 位置 ── */}
      <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
        {cnMode ? "位置 / 国内市场" : "位置 / 海外市场"}
      </SectionHeader>
      {!cnMode ? (
        <div className="space-y-[11px]">
          <div className="grid grid-cols-2 gap-[11px]">
            <Field label="大洲" value={continent} onChange={onContinent} options={options.continents} />
            <Field label="国家" value={country} onChange={onCountry} options={countries} />
            <Field label="城市" value={city} onChange={(v) => onParams({ city: v })} options={cities} />
            <Field
              label="物业类型"
              value={params.property_type || options.property_types[0]}
              onChange={(v) => onParams({ property_type: v })}
              options={options.property_types}
            />
            <Field
              label="房间类型"
              value={params.room_type || options.room_types[0]}
              onChange={(v) => onParams({ room_type: v })}
              options={options.room_types}
            />
            <Field
              label="视野"
              value={params.view || options.views[0]}
              onChange={(v) => onParams({ view: v })}
              options={options.views}
            />
            <Field
              label="家具地区风格"
              value={params.market_furniture || options.market_furniture[0]}
              onChange={(v) => onParams({ market_furniture: v })}
              options={options.market_furniture}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] font-semibold text-[#857c6e]">小区/地段（可选）</span>
            <Input
              value={params.neighborhood || ""}
              onChange={(e) => onParams({ neighborhood: e.target.value })}
              placeholder="自由填写…"
              className="h-10 rounded-[10px] bg-card"
            />
          </div>
        </div>
      ) : (
        <div className="space-y-[13px]">
          <div className="grid grid-cols-2 gap-[11px]">
            <Field
              label="城市"
              value={params.cn_city || options.cn_cities[0]}
              onChange={(v) => onParams({ cn_city: v })}
              options={options.cn_cities}
            />
            <Field
              label="楼盘定位"
              value={params.cn_tier || options.cn_tiers[0]}
              onChange={(v) => onParams({ cn_tier: v })}
              options={options.cn_tiers}
            />
            <Field
              label="开发商"
              value={params.cn_developer || options.cn_developers[0]}
              onChange={(v) => onParams({ cn_developer: v })}
              options={options.cn_developers}
            />
            <Field
              label="户型"
              value={params.cn_unit_type || options.cn_unit_types[0]}
              onChange={(v) => onParams({ cn_unit_type: v })}
              options={options.cn_unit_types}
            />
            <Field
              label="交付/装修状态"
              value={params.cn_delivery || options.cn_delivery_choices[0]}
              onChange={(v) => onParams({ cn_delivery: v })}
              options={options.cn_delivery_choices}
            />
            <Field
              label="空间类型"
              value={params.cn_room_type || options.cn_room_types[0]}
              onChange={(v) => onParams({ cn_room_type: v })}
              options={options.cn_room_types}
            />
            <Field
              label="视野"
              value={params.cn_view || options.views[0]}
              onChange={(v) => onParams({ cn_view: v })}
              options={options.views}
            />
          </div>
          <div className="grid grid-cols-2 gap-[13px]">
            <Chips
              label="空间特征"
              options={options.cn_space_features}
              selected={params.cn_space_features ?? []}
              onToggle={(v) => toggle("cn_space_features", v)}
            />
            <Chips
              label="配套"
              options={options.cn_facilities}
              selected={params.cn_facilities ?? []}
              onToggle={(v) => toggle("cn_facilities", v)}
            />
          </div>
        </div>
      )}

      {/* ── 板材与工艺 ── */}
      <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
        板材与工艺 / MATERIAL
      </SectionHeader>
      <div className="grid grid-cols-2 gap-[11px]">
        <Field
          label="板材尺寸"
          value={params.floor_size || options.floor_sizes[0]}
          onChange={(v) => onParams({ floor_size: v })}
          options={options.floor_sizes}
        />
        <Field
          label="拼缝工艺"
          value={params.seam_type || options.seam_types[0]}
          onChange={(v) => onParams({ seam_type: v })}
          options={options.seam_types}
        />
        <Field
          label="光泽度"
          value={params.glossiness || options.glossiness[1] || options.glossiness[0]}
          onChange={(v) => onParams({ glossiness: v })}
          options={options.glossiness}
        />
        <Field
          label="色调"
          value={params.floor_tone || options.floor_tones[0]}
          onChange={(v) => onParams({ floor_tone: v })}
          options={options.floor_tones}
        />
      </div>

      {/* ── 风格与镜头 ── */}
      <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
        风格与镜头 / STYLE
      </SectionHeader>
      <div className="grid grid-cols-2 gap-[11px]">
        <Field
          label="风格"
          value={params.style_type || options.styles[0]}
          onChange={(v) => onParams({ style_type: v })}
          options={options.styles}
        />
        <Field
          label="光线"
          value={params.lighting || options.lightings[0]}
          onChange={(v) => onParams({ lighting: v })}
          options={options.lightings}
        />
        <Field
          label="镜头"
          value={params.angle || options.angles[0]}
          onChange={(v) => onParams({ angle: v })}
          options={options.angles}
        />
      </div>

      {/* ── 宠物友好 ── */}
      {isPet && (
        <div className="mt-[13px] grid grid-cols-3 gap-[9px] rounded-xl bg-[#f2e9e0] p-[11px]">
          <Field
            label="种类"
            value={params.pet_type || options.pet_types[0]}
            onChange={(v) => onParams({ pet_type: v })}
            options={options.pet_types}
          />
          <Field
            label="动作"
            value={params.pet_action || options.pet_actions[0]}
            onChange={(v) => onParams({ pet_action: v })}
            options={options.pet_actions}
          />
          <Field
            label="焦点"
            value={params.pet_focus || options.pet_focus[0]}
            onChange={(v) => onParams({ pet_focus: v })}
            options={options.pet_focus}
          />
        </div>
      )}

      {/* ── 参照模式 ── */}
      {isRef && (
        <div className="mt-[13px] space-y-2.5 rounded-xl border border-border bg-card p-[13px]">
          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] font-semibold text-[#857c6e]">
              参照风格图（参照模式必传）
            </span>
            <RefUpload value={refValue} onPick={onRefPick} />
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] font-semibold text-[#857c6e]">
              参照修正（可选，纠正风格提取偏差）
            </span>
            <Input
              value={params.style_ref_correction || ""}
              onChange={(e) => onParams({ style_ref_correction: e.target.value })}
              placeholder="例如：墙面其实是米白色，不是灰色"
              className="h-10 rounded-[10px] bg-[#faf7f0]"
            />
          </div>
        </div>
      )}

      {/* ── 输出 ── */}
      <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
        输出 / OUTPUT
      </SectionHeader>
      <div className="grid grid-cols-2 gap-[11px]">
        <Field
          label="画面比例"
          value={params.aspect_ratio || options.aspect_ratios[0]}
          onChange={(v) => onParams({ aspect_ratio: v })}
          options={options.aspect_ratios}
        />
        <Field
          label="画质"
          value={params.resolution || options.resolutions[0]}
          onChange={(v) => onParams({ resolution: v })}
          options={options.resolutions}
        />
      </div>

      {/* ── 高级：回避清单 / 自定义补充 ── */}
      <button
        type="button"
        onClick={() => setAdvOpen((v) => !v)}
        className="mt-[18px] flex w-full items-center justify-between rounded-xl border border-border bg-card px-[13px] py-[11px] transition hover:bg-[#f2e9e0]"
      >
        <span className="text-[12.5px] font-bold text-[#6b6356]">
          ⚙ 高级 · 回避清单 / 自定义补充
        </span>
        <span className="text-[11px] text-[#9a9082]">{advOpen ? "收起 ▲" : "展开 ▼"}</span>
      </button>
      {advOpen && (
        <div className="mt-2.5 space-y-3 rounded-xl border border-border bg-card p-[13px]">
          <Chips
            label="🚫 避免出现"
            options={options.avoid_items}
            selected={params.avoid_items ?? []}
            onToggle={(v) => toggle("avoid_items", v)}
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] font-semibold text-[#857c6e]">自定义补充（可选）</span>
            <Textarea
              rows={2}
              value={params.custom_addition || ""}
              onChange={(e) => onParams({ custom_addition: e.target.value })}
              placeholder="可追加任何中/英文补充说明…"
              className="rounded-[9px] bg-[#faf7f0]"
            />
          </div>
        </div>
      )}
    </div>
  );
}
