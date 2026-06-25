/* eslint-disable @next/next/no-img-element */
"use client";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { GenParams, ModelFilter, OptionsView, Swatch } from "@/lib/types";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
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

function Chips({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string;
  options: string[];
  selected: string[];
  onToggle: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = selected.includes(o);
          return (
            <button
              key={o}
              type="button"
              onClick={() => onToggle(o)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs transition-colors",
                on
                  ? "border-primary bg-primary text-primary-foreground"
                  : "bg-background hover:bg-muted",
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
  const isPet = params.workflow_mode.includes("宠物友好");

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
    <div className="space-y-4">
      {/* 工作流 */}
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

      {/* 市场 */}
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

      {/* 模型 */}
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

      {/* ── 地区/空间：按市场切换 ── */}
      {!cnMode ? (
        <div className="space-y-3 rounded-lg border bg-muted/20 p-2.5">
          <Field
            label="大洲"
            value={continent}
            onChange={onContinent}
            options={options.continents}
          />
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="国家"
              value={country}
              onChange={onCountry}
              options={countries}
            />
            <Field
              label="城市"
              value={city}
              onChange={(v) => onParams({ city: v })}
              options={cities}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
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
          </div>
          <div className="grid grid-cols-2 gap-3">
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
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">
              小区/地段（可选）
            </Label>
            <Input
              value={params.neighborhood || ""}
              onChange={(e) => onParams({ neighborhood: e.target.value })}
              placeholder="自由填写…"
            />
          </div>
        </div>
      ) : (
        <div className="space-y-3 rounded-lg border bg-muted/20 p-2.5">
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="交付/装修状态"
              value={params.cn_delivery || options.cn_delivery_choices[0]}
              onChange={(v) => onParams({ cn_delivery: v })}
              options={options.cn_delivery_choices}
            />
            <Field
              label="开发商"
              value={params.cn_developer || options.cn_developers[0]}
              onChange={(v) => onParams({ cn_developer: v })}
              options={options.cn_developers}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
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
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="户型"
              value={params.cn_unit_type || options.cn_unit_types[0]}
              onChange={(v) => onParams({ cn_unit_type: v })}
              options={options.cn_unit_types}
            />
            <Field
              label="国内空间类型"
              value={params.cn_room_type || options.cn_room_types[0]}
              onChange={(v) => onParams({ cn_room_type: v })}
              options={options.cn_room_types}
            />
          </div>
          <Field
            label="视野"
            value={params.cn_view || options.views[0]}
            onChange={(v) => onParams({ cn_view: v })}
            options={options.views}
          />
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
      )}

      {/* ── 地板 / 风格 / 相机（通用）── */}
      <div className="grid grid-cols-2 gap-3">
        <Field
          label="板材尺寸"
          value={params.floor_size || options.floor_sizes[0]}
          onChange={(v) => onParams({ floor_size: v })}
          options={options.floor_sizes}
        />
        <Field
          label="拼缝"
          value={params.seam_type || options.seam_types[0]}
          onChange={(v) => onParams({ seam_type: v })}
          options={options.seam_types}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
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
      <Field
        label="风格"
        value={params.style_type || options.styles[0]}
        onChange={(v) => onParams({ style_type: v })}
        options={options.styles}
      />
      <div className="grid grid-cols-2 gap-3">
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
      <div className="grid grid-cols-2 gap-3">
        <Field
          label="比例"
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

      {/* ── 宠物友好 ── */}
      {isPet && (
        <div className="grid grid-cols-3 gap-2 rounded-lg border bg-muted/20 p-2.5">
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
        <div className="space-y-2 rounded-lg border bg-muted/20 p-2.5">
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">
              参照风格图（参照模式必传）
            </Label>
            <RefUpload value={refValue} onPick={onRefPick} />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">
              参照修正（可选，纠正风格提取偏差）
            </Label>
            <Input
              value={params.style_ref_correction || ""}
              onChange={(e) => onParams({ style_ref_correction: e.target.value })}
              placeholder="例如：墙面其实是米白色，不是灰色"
            />
          </div>
        </div>
      )}

      {/* ── 更多：回避清单 / 自定义补充 ── */}
      <details className="rounded-lg border bg-muted/20 p-2.5">
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
          ⚙️ 更多 · 回避清单 / 自定义补充
        </summary>
        <div className="mt-3 space-y-3">
          <Chips
            label="🚫 避免出现"
            options={options.avoid_items}
            selected={params.avoid_items ?? []}
            onToggle={(v) => toggle("avoid_items", v)}
          />
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">自定义补充（可选）</Label>
            <Textarea
              rows={2}
              value={params.custom_addition || ""}
              onChange={(e) => onParams({ custom_addition: e.target.value })}
              placeholder="可追加任何中/英文补充说明…"
            />
          </div>
        </div>
      </details>
    </div>
  );
}
