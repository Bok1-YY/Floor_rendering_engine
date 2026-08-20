/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, Clapperboard, Sparkles, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { GenParams, OptionsView, Swatch, OmakaseOption } from "@/lib/types";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SectionHeader, Segmented } from "@/components/dc-ui";
import { InpaintDialog } from "@/components/InpaintDialog";
import { FreeModePanel } from "@/components/FreeModePanel";

const cleanLabel = (value: string) => value.replace(/^[\p{Extended_Pictographic}\uFE0F\u200D\s]+/u, "");
const short = (s: string) => cleanLabel(s.split(" (")[0]);

const WF_SUB: Record<string, string> = {
  纯效果图: "用地板小样直接生成全新空间",
  球面效果图: "同球心六面图集直出，再确定性合成 360° VR",
  参照模式: "按参照图的风格与氛围生成",
  宠物友好: "画面中加入宠物生活场景",
  地板替换: "保留原图空间，仅替换原地面",
  Omakase: "AI 代笔场景，配置精简",
  墙板模式: "护墙板/木饰面：再设计 / 替换 / 原创",
  自由创作: "自己写完整指令，按顺序上传 1–3 张图",
};

function Field({
  label,
  value,
  onChange,
  options,
  adjusted = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  adjusted?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className={cn("text-[11.5px] font-semibold", adjusted ? "text-primary-2" : "text-muted-foreground")}>
        {label}{adjusted ? " ·已调整" : ""}
      </span>
      <Select value={value} onValueChange={(v) => onChange(v ?? "")}>
        <SelectTrigger className={cn("h-10 w-full rounded-[10px] bg-card text-[13px] text-foreground", adjusted && "border-primary-2")}>
          <SelectValue>{cleanLabel(value)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {cleanLabel(o)}
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
      <span className={cn("text-[11.5px] font-semibold text-muted-foreground", labelClassName)}>
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
                  : "border-border bg-card text-secondary-foreground hover:bg-accent",
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

function ImageUpload({
  value,
  onPick,
  uploadFn,
  buttonLabel,
  okMsg,
}: {
  value: Swatch | null;
  onPick: (s: Swatch) => void;
  uploadFn: (f: File) => Promise<Swatch>;
  buttonLabel: string;
  okMsg: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  async function up(f: File) {
    setBusy(true);
    try {
      onPick(await uploadFn(f));
      toast.success(okMsg);
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
        className="h-9 rounded-[9px] border border-border bg-card px-3 text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
      >
        {busy ? "上传中…" : value ? "重新上传" : buttonLabel}
      </button>
      <input
        ref={ref}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = ""; // 重置：否则上传失败后重选同一文件不触发 onChange
          if (f) up(f);
        }}
      />
      {value && (
        <img
          src={api.imgUrl(value.thumb)}
          alt="preview"
          className="h-10 w-14 rounded-md border border-border object-cover"
        />
      )}
    </div>
  );
}

export function ParamsForm({
  options,
  params,
  onParams,
  refValue,
  onRefPick,
  roomValue,
  onRoomPick,
  freePrompt,
  freeImages,
  onFreePrompt,
  onFreeImages,
}: {
  options: OptionsView;
  params: GenParams;
  onParams: (patch: Partial<GenParams>) => void;
  refValue: Swatch | null;
  onRefPick: (s: Swatch) => void;
  roomValue: Swatch | null;
  onRoomPick: (s: Swatch) => void;
  freePrompt: string;
  freeImages: Swatch[];
  onFreePrompt: (value: string) => void;
  onFreeImages: (value: Swatch[]) => void;
}) {
  const cnMode = !!params.cn_mode;
  const isRef = params.workflow_mode.includes("参照模式");
  const isReplace = params.workflow_mode.includes("地板替换");
  const isPet = params.workflow_mode.includes("宠物友好");
  const isOmakase = params.workflow_mode.includes("Omakase");
  const isPanel = params.workflow_mode.includes("墙板");
  const isFree = params.workflow_mode.includes("自由创作");
  const isPure = params.workflow_mode.includes("纯效果图");
  const isSphere = params.workflow_mode.includes("球面效果图");
  const supportsCinematic = isPure || isPet || isRef || isOmakase;
  const panelSub = params.panel_submode || "再设计";
  const isPanelScene = isPanel && !panelSub.includes("替换"); // 再设计/纯原创：暴露场景控件；替换保留原图
  const [advOpen, setAdvOpen] = useState(false);
  const [coverageDraft, setCoverageDraft] = useState<[number, number] | null>(null);
  // 房间图生成前预处理：画笔涂抹移除原有家具/杂物，清理结果另存并回填为当前房间图
  const [roomCleanOpen, setRoomCleanOpen] = useState(false);

  // ── Omakase：本地状态（诉求输入 / 加载 / 候选）。最终 scene_override 存进 params 交给后端 ──
  const [omaIdea, setOmaIdea] = useState("");
  const [omaLoading, setOmaLoading] = useState(false);
  const [omaOptions, setOmaOptions] = useState<OmakaseOption[]>([]);
  const runOmakase = async () => {
    const idea = omaIdea.trim();
    if (!idea) {
      toast.error("先用一句话描述想要的画面/氛围");
      return;
    }
    setOmaLoading(true);
    try {
      const r = await api.omakaseScenes(idea);
      setOmaOptions(r.options || []);
      if (r.fallback_used && r.notice) toast.warning(r.notice);
      if (!r.options?.length) toast.error("没生成到候选，换个说法再试");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Omakase 生成失败");
    } finally {
      setOmaLoading(false);
    }
  };

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

  const rawCoverageMin = params.floor_coverage_min ?? 40;
  const rawCoverageMax = params.floor_coverage_max ?? 50;
  const coverageMin = Math.min(80, Math.max(10, Math.round(rawCoverageMin)));
  const coverageMax = Math.min(80, Math.max(coverageMin, Math.round(rawCoverageMax)));
  const coverageRange = coverageDraft ?? [coverageMin, coverageMax];
  const readCoverageRange = (value: number | readonly number[]): [number, number] => {
    const values = Array.isArray(value) ? value : [value, value];
    return [Math.round(values[0]), Math.round(values[1])];
  };

  useEffect(() => {
    if (rawCoverageMin !== coverageMin || rawCoverageMax !== coverageMax) {
      onParams({ floor_coverage_min: coverageMin, floor_coverage_max: coverageMax });
    }
  }, [coverageMax, coverageMin, onParams, rawCoverageMax, rawCoverageMin]);

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
      <div className="flex flex-wrap gap-[7px]">
        {options.workflow_modes.map((m) => {
          const active = params.workflow_mode === m;
          const name = short(m);
          return (
            <button
              key={m}
              type="button"
              onClick={() => {
                // 宠物场景默认开启；Omakase 在选中含生命主体候选时自动开启；
                // 纯效果图/参照默认关闭但可手动打开。切换到其他模式一律清除。
                onParams({
                  workflow_mode: m,
                  cinematic_enabled: m.includes("宠物友好"),
                });
              }}
              className={cn(
                "whitespace-nowrap rounded-full border px-3 py-[7px] text-[12px] font-bold transition-colors",
                active
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-card text-secondary-foreground hover:bg-accent",
              )}
            >
              {name}
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">
        {WF_SUB[short(params.workflow_mode)] ?? ""}
        {supportsCinematic ? " · 支持电影模式" : ""}
      </p>

      {isSphere && (
        <div className="mt-3 rounded-xl border border-primary/35 bg-primary-soft px-3 py-2.5 text-[11.5px] leading-relaxed text-accent-foreground">
          这条路线不先生成普通透视图：B2 与 GPT Image 2 会各自一次性生成六个同球心方向，系统随后自动拆面、校验边界并合成可拖动查看的 2:1 全景。
        </div>
      )}

      {isFree && (
        <FreeModePanel
          prompt={freePrompt}
          images={freeImages}
          onPrompt={onFreePrompt}
          onImages={onFreeImages}
        />
      )}

      {/* ── 源图上传：替换必传 / 参照必传 / 球面几何锚点可选 ── */}
      {(isReplace || isRef || isSphere) && (
        <div className="mt-[13px] space-y-2.5 rounded-xl border border-primary/40 bg-primary-soft p-[13px]">
          {isReplace && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11.5px] font-semibold text-accent-foreground">
                待替换图 · 房间原图（地板替换必传）
              </span>
              <ImageUpload
                value={roomValue}
                onPick={onRoomPick}
                uploadFn={api.uploadRoom}
                buttonLabel="上传房间原图"
                okMsg="房间原图已上传"
              />
              <span className="text-[11px] text-muted-foreground">
                保留原图的空间、家具与采光，仅把原地面替换为所选地板
              </span>
              {roomValue && (
                <button
                  type="button"
                  onClick={() => setRoomCleanOpen(true)}
                  className="h-9 w-fit rounded-[9px] border border-border bg-card px-3 text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
                  title="生成前先用画笔涂抹移除房间里的家具/杂物，清理后的图自动作为当前房间图"
                >
                  <Trash2 size={14} className="mr-1.5 inline" />
                  清理家具
                </button>
              )}
              {roomCleanOpen && roomValue && (
                <InpaintDialog
                  open={roomCleanOpen}
                  onOpenChange={setRoomCleanOpen}
                  srcUrl={roomValue.url}
                  target={{ kind: "room", roomPath: roomValue.path }}
                  onRoomCleaned={(path, url, thumb) =>
                    onRoomPick({ ...roomValue, path, url, thumb })
                  }
                />
              )}
            </div>
          )}
          {isSphere && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11.5px] font-semibold text-accent-foreground">
                空间参考效果图（可选 · 本地几何锚点）
              </span>
              <ImageUpload
                value={roomValue}
                onPick={onRoomPick}
                uploadFn={api.uploadRoom}
                buttonLabel="上传空间参考图"
                okMsg="空间参考图已上传"
              />
              <span className="text-[11px] leading-relaxed text-muted-foreground">
                有图时，本地算法锁定正前方地平线、墙体比例、铺装方向和尺度；不上传仍可直出六面，但不会宣称与某张房间原图一致。
              </span>
            </div>
          )}
          {isRef && (
            <>
              <div className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-accent-foreground">
                  参照风格图（参照模式必传）
                </span>
                <ImageUpload
                  value={refValue}
                  onPick={onRefPick}
                  uploadFn={api.uploadRef}
                  buttonLabel="上传参照图"
                  okMsg="参照图已上传"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">
                  参照修正（可选，纠正风格提取偏差）
                </span>
                <Input
                  value={params.style_ref_correction || ""}
                  onChange={(e) =>
                    onParams({ style_ref_correction: e.target.value })
                  }
                  placeholder="例如：墙面其实是米白色，不是灰色"
                  className="h-10 rounded-[10px] bg-card"
                />
              </div>
            </>
          )}
        </div>
      )}

      {/* ── 墙板模式：子行为切换 + 按需第二张图（上方的地板小样此时即墙板木纹样图）── */}
      {isPanel && (
        <div className="mt-[13px] space-y-2.5 rounded-xl border border-primary/40 bg-primary-soft p-[13px]">
          <div className="flex flex-col gap-1.5">
            <span className="text-[11.5px] font-semibold text-accent-foreground">墙板子模式</span>
            <Segmented
              value={panelSub}
              options={[
                { value: "再设计", label: "再设计" },
                { value: "替换", label: "替换" },
                { value: "纯原创", label: "纯原创" },
              ]}
              onChange={(v) => onParams({ panel_submode: v })}
            />
            <span className="text-[11px] text-muted-foreground">
              上方上传的小样即「墙板木纹样图」。再设计=按场景参照图生新图；替换=保留原场景仅换木纹；纯原创=仅凭木纹样图原创场景。
            </span>
          </div>
          {panelSub.includes("再设计") && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11.5px] font-semibold text-accent-foreground">
                场景参照图（再设计必传）
              </span>
              <ImageUpload
                value={refValue}
                onPick={onRefPick}
                uploadFn={api.uploadRef}
                buttonLabel="上传场景参照图"
                okMsg="场景参照图已上传"
              />
              <Input
                value={params.style_ref_correction || ""}
                onChange={(e) => onParams({ style_ref_correction: e.target.value })}
                placeholder="参照修正（可选）：例如强调竖向线条更细密"
                className="h-10 rounded-[10px] bg-card"
              />
            </div>
          )}
          {panelSub.includes("替换") && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11.5px] font-semibold text-accent-foreground">
                原墙板场景图（替换必传）
              </span>
              <ImageUpload
                value={roomValue}
                onPick={onRoomPick}
                uploadFn={api.uploadRoom}
                buttonLabel="上传原墙板场景图"
                okMsg="原墙板场景图已上传"
              />
              <span className="text-[11px] text-muted-foreground">
                保留原图的结构、比例、收口条与光影，仅把墙板木纹替换为所选样图
              </span>
            </div>
          )}
        </div>
      )}

      {/* ── 墙板场景（再设计/纯原创）：空间类型 + 墙板尺寸（预设下拉 + 自定义）── */}
      {isPanelScene && (
        <div className="mt-[13px] grid grid-cols-2 gap-[11px] rounded-xl bg-accent p-[11px]">
          <Field
            label="空间类型"
            value={params.room_type || options.room_types[0]}
            onChange={(v) => onParams({ room_type: v })}
            options={options.room_types}
          />
          <Field
            label="墙板尺寸"
            value={
              options.panel_sizes.includes(params.panel_size || "")
                ? (params.panel_size as string)
                : options.panel_sizes[0]
            }
            onChange={(v) => onParams({ panel_size: v })}
            options={options.panel_sizes}
          />
          <div className="col-span-2 flex flex-col gap-1.5">
            <span className="text-[11.5px] font-semibold text-muted-foreground">自定义墙板尺寸（可选）</span>
            <Input
              value={
                options.panel_sizes.includes(params.panel_size || "")
                  ? ""
                  : params.panel_size || ""
              }
              onChange={(e) => onParams({ panel_size: e.target.value })}
              placeholder="填了以此为准，如：600宽菱形拼 / 竖向凹槽线条板"
              className="h-10 rounded-[10px] bg-card"
            />
          </div>
        </div>
      )}

      {!isOmakase && !isFree && !(isPanel && panelSub.includes("替换")) && (
        <div className="mt-[14px]">
          <div className="mb-2 text-[11.5px] font-bold text-secondary-foreground">场景核心</div>
          <div className="grid grid-cols-2 gap-[11px]">
            <Field
              label="房间类型"
              value={cnMode ? (params.cn_room_type || options.cn_room_types[0]) : (params.room_type || options.room_types[0])}
              onChange={(value) => onParams(cnMode ? { cn_room_type: value } : { room_type: value })}
              options={cnMode ? options.cn_room_types : options.room_types}
              adjusted={cnMode
                ? (params.cn_room_type || options.cn_room_types[0]) !== options.cn_room_types[0]
                : (params.room_type || options.room_types[0]) !== options.room_types[0]}
            />
            <Field
              label="风格"
              value={params.style_type || options.styles[0]}
              onChange={(style_type) => onParams({ style_type })}
              options={options.styles}
              adjusted={(params.style_type || options.styles[0]) !== options.styles[0]}
            />
            <Field
              label="光线"
              value={params.lighting || options.lightings[0]}
              onChange={(lighting) => onParams({ lighting })}
              options={options.lightings}
              adjusted={(params.lighting || options.lightings[0]) !== options.lightings[0]}
            />
            {!isSphere && (
              <Field
                label="镜头"
                value={params.angle || options.angles[0]}
                onChange={(angle) => onParams({ angle })}
                options={options.angles}
                adjusted={(params.angle || options.angles[0]) !== options.angles[0]}
              />
            )}
          </div>
        </div>
      )}

      {!isFree && !isSphere && (
        <div
          className={cn(
            "mt-[14px] flex items-start justify-between gap-4 rounded-xl border p-[12px] transition-colors",
            supportsCinematic ? "border-border bg-card" : "border-border bg-muted/45",
          )}
        >
          <div className="flex min-w-0 gap-2.5">
            <Clapperboard size={17} className={cn("mt-0.5 flex-none", supportsCinematic ? "text-primary" : "text-muted-foreground")} />
            <div className="min-w-0">
              <div className={cn("text-[12.5px] font-bold", supportsCinematic ? "text-foreground" : "text-muted-foreground")}>
                电影模式 · 真实感导演
              </div>
              <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                {supportsCinematic
                  ? "生图前规划可信机位、自然动作、视线关系与现实光源"
                  : `当前“${short(params.workflow_mode)}”不支持；可切换纯效果图、宠物友好、参照模式或 Omakase`}
              </p>
            </div>
          </div>
          <Switch
            className="mt-0.5 shrink-0"
            checked={supportsCinematic && !!params.cinematic_enabled}
            disabled={!supportsCinematic}
            onCheckedChange={(cinematic_enabled) => onParams({ cinematic_enabled })}
            aria-label="电影模式：电影真实感"
          />
        </div>
      )}

      {!isFree && (
        <button
          type="button"
          onClick={() => setAdvOpen((open) => !open)}
          className="mt-[14px] flex w-full items-center justify-between gap-3 rounded-xl border border-dashed border-border-strong bg-panel px-[13px] py-[11px] text-left transition-colors hover:border-primary hover:bg-primary-soft"
        >
          <span className="min-w-0">
            <span className="block text-[12.5px] font-bold text-secondary-foreground">更多场景参数 · 12 项</span>
            {!advOpen && (
              <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                {[cnMode ? params.cn_city : city, params.property_type, params.view, params.floor_size, params.seam_type, params.glossiness].filter(Boolean).map((value) => short(String(value))).join(" · ")}
              </span>
            )}
          </span>
          <ChevronDown size={15} className={cn("flex-none text-muted-foreground transition-transform", advOpen && "rotate-180")} />
        </button>
      )}

      {advOpen && !isOmakase && !isPanel && !isFree && (<>
      {/* ── 位置 ── */}
      <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
        {cnMode ? "位置 / 国内市场" : "位置 / 海外市场"}
      </SectionHeader>
      <div className="mb-[11px] w-40">
        <div className="mb-[7px] text-[11.5px] font-semibold text-muted-foreground">市场</div>
        <Segmented
          value={cnMode ? "cn" : "overseas"}
          options={[{ value: "overseas", label: "海外" }, { value: "cn", label: "国内" }]}
          onChange={(value) => onParams({ cn_mode: value === "cn" })}
        />
      </div>
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
            <span className="text-[11.5px] font-semibold text-muted-foreground">小区/地段（可选）</span>
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
      </>)}

      {/* ── 板材与工艺（墙板模式不适用：木纹/竖纹/哑光已由墙板模板固化，隐藏地板专用字段）── */}
      {advOpen && !isPanel && !isFree && (<>
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
      </>)}

      {/* ── Omakase 独立模式：AI 代笔场景层，接管风格/光线/镜头；地板规格仍由下方控制 ── */}
      {isOmakase && (
        <>
          <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
            OMAKASE · 场景代笔（必填）
          </SectionHeader>
          <div className="space-y-2.5 rounded-xl border border-border bg-card p-[13px]">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              一句话描述想要的画面/氛围（可以很抽象），AI 会写几段「怎么拍」的场景供你选或改。选定后将
              <b className="text-primary-2">接管风格·光线·镜头等场景描述</b>；地板与物理规格仍由下方设置控制。场景定稿为必填——未选或未填写场景时无法提交生成。
            </p>
            <Textarea
              rows={2}
              value={omaIdea}
              onChange={(e) => setOmaIdea(e.target.value)}
              placeholder="例：荷兰普通人家，一个女人和孩子在玩耍；或：体现耐刮耐磨；或：温馨时光…"
              className="rounded-[9px] bg-panel"
            />
            <button
              type="button"
              disabled={omaLoading}
              onClick={runOmakase}
              className="w-full rounded-[9px] bg-primary-2 px-[13px] py-[9px] text-[12.5px] font-bold text-white transition hover:brightness-105 disabled:opacity-60"
            >
              {omaLoading ? "生成中…" : <span className="inline-flex items-center gap-1.5"><Sparkles size={14} />生成场景候选</span>}
            </button>
            {omaOptions.length > 0 && (
              <div className="space-y-2">
                {omaOptions.map((o, i) => {
                  const chosen = (params.scene_override || "") === o.text;
                  return (
                    <button
                      key={i}
                      type="button"
                      onClick={() => onParams({
                        scene_override: o.text,
                        cinematic_enabled: o.subject_type !== "none",
                      })}
                      className={cn(
                        "block w-full rounded-[9px] border p-[11px] text-left transition",
                        chosen
                          ? "border-primary-2 bg-primary-soft"
                          : "border-border bg-panel hover:bg-accent",
                      )}
                    >
                      <div className="mb-1 flex items-center gap-1.5">
                        {o.recommended && (
                          <span className="rounded bg-primary-2 px-1.5 py-0.5 text-[10px] font-bold text-white">
                            推荐
                          </span>
                        )}
                        {chosen && (
                          <span className="text-[11px] font-bold text-primary-2">✓ 已选</span>
                        )}
                      </div>
                      <p className="text-[12px] leading-relaxed text-secondary-foreground">{o.text}</p>
                      {o.why && <p className="mt-1 text-[10.5px] text-muted-foreground">推荐理由：{o.why}</p>}
                    </button>
                  );
                })}
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <span className="text-[11.5px] font-semibold text-muted-foreground">
                场景定稿（必填，可直接编辑）
              </span>
              <Textarea
                rows={3}
                value={params.scene_override || ""}
                onChange={(e) => onParams({ scene_override: e.target.value })}
                placeholder="选一段候选会填到这里，可直接改；也可完全手写。必填。"
                className="rounded-[9px] bg-panel"
              />
            </div>
          </div>
        </>
      )}

      {/* ── 宠物友好 ── */}
      {isPet && (
        <div className="mt-[13px] grid grid-cols-3 gap-[9px] rounded-xl bg-accent p-[11px]">
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

      {advOpen && !isPanel && !isFree && (<>
      {/* ── 高级：地板占比 / 回避清单 / 自定义补充 ── */}
        <div className="mt-[18px] space-y-3 rounded-xl border border-border bg-card p-[13px]">
          <div>
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <span className="text-[11.5px] font-semibold text-muted-foreground">地板占画面面积</span>
              <span className="text-[10.5px] text-muted-foreground">允许 10–80% · 模型会近似执行</span>
            </div>
            <div className="rounded-[12px] border border-border bg-panel px-3.5 py-3">
              <div className="mb-2.5 flex items-center justify-between gap-3">
                <div className="rounded-lg bg-card px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-sm ring-1 ring-border">
                  最小 <b className="ml-1 text-[13px] tabular-nums text-foreground">{coverageRange[0]}%</b>
                </div>
                <div className="text-[10.5px] font-semibold text-primary">当前范围 {coverageRange[0]}–{coverageRange[1]}%</div>
                <div className="rounded-lg bg-card px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-sm ring-1 ring-border">
                  最大 <b className="ml-1 text-[13px] tabular-nums text-foreground">{coverageRange[1]}%</b>
                </div>
              </div>
              <Slider
                value={coverageRange}
                min={10}
                max={80}
                step={1}
                thumbCollisionBehavior="none"
                getAriaLabel={(index) => index === 0 ? "地板最小占比" : "地板最大占比"}
                onValueChange={(value) => setCoverageDraft(readCoverageRange(value))}
                onValueCommitted={(value) => {
                  const [min, max] = readCoverageRange(value);
                  setCoverageDraft(null);
                  onParams({ floor_coverage_min: min, floor_coverage_max: max });
                }}
                className="py-2 [&_[data-slot=slider-thumb]]:size-4 [&_[data-slot=slider-thumb]]:border-2 [&_[data-slot=slider-track]]:h-2"
              />
              <div className="mt-1.5 flex justify-between text-[10px] tabular-nums text-muted-foreground">
                <span>10%</span>
                <span>拖动两个圆点，松手后应用</span>
                <span>80%</span>
              </div>
            </div>
          </div>
          {!isOmakase && (<>
            <Chips
              label="避免出现"
              options={options.avoid_items}
              selected={params.avoid_items ?? []}
              onToggle={(v) => toggle("avoid_items", v)}
            />
            <div className="flex flex-col gap-1.5">
              <span className="text-[11.5px] font-semibold text-muted-foreground">自定义补充（可选）</span>
              <Textarea
                rows={2}
                value={params.custom_addition || ""}
                onChange={(e) => onParams({ custom_addition: e.target.value })}
                placeholder="可追加任何中/英文补充说明…"
                className="rounded-[9px] bg-panel"
              />
            </div>
          </>)}
        </div>
      </>)}
    </div>
  );
}
