/* eslint-disable @next/next/no-img-element */
"use client";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { GenParams, ModelKey, OptionsView, SDOptions, Swatch, OmakaseOption } from "@/lib/types";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
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

const short = (s: string) => s.split(" (")[0];

const WF_SUB: Record<string, string> = {
  纯效果图: "用地板小样直接生成全新空间",
  参照模式: "按参照图的风格与氛围生成",
  宠物友好: "画面中加入宠物生活场景",
  地板替换: "保留原图空间，仅替换原地面",
  Omakase: "AI 代笔场景，配置精简",
  墙板模式: "护墙板/木饰面：再设计 / 替换 / 原创",
  自由创作: "自己写完整指令，按顺序上传 1–3 张图",
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
      <span className="text-[11.5px] font-semibold text-muted-foreground">{label}</span>
      <Select value={value} onValueChange={(v) => onChange(v ?? "")}>
        <SelectTrigger className="h-10 w-full rounded-[10px] bg-card text-[13px] text-foreground">
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
  modelTargets,
  sdOptions,
  sdEnabled,
  onParams,
  onModelTargets,
  onSDOptions,
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
  modelTargets: ModelKey[];
  sdOptions: SDOptions;
  sdEnabled: boolean;
  onParams: (patch: Partial<GenParams>) => void;
  onModelTargets: (m: ModelKey[]) => void;
  onSDOptions: (patch: Partial<SDOptions>) => void;
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
  const supportsCinematic = isPure || isPet || isRef || isOmakase;
  const panelSub = params.panel_submode || "再设计";
  const isPanelScene = isPanel && !panelSub.includes("替换"); // 再设计/纯原创：暴露场景控件；替换保留原图
  const [advOpen, setAdvOpen] = useState(false);
  const [sdOpen, setSdOpen] = useState(false);
  // 房间图生成前预处理：画笔涂抹移除原有家具/杂物，清理结果另存并回填为当前房间图
  const [roomCleanOpen, setRoomCleanOpen] = useState(false);
  const toggleModel = (key: ModelKey) => {
    const selected = modelTargets.includes(key);
    // 不合法时只禁止新增；草稿恢复或切换工作流后，已选中的 SD 仍必须允许取消。
    if (key === "sd35" && !selected && (!sdEnabled || !params.workflow_mode.includes("纯效果图"))) return;
    onModelTargets(
      selected
        ? modelTargets.filter((m) => m !== key)
        : [...modelTargets, key],
    );
  };

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
              onClick={() => {
                // 宠物场景默认开启；Omakase 在选中含生命主体候选时自动开启；
                // 纯效果图/参照默认关闭但可手动打开。切换到其他模式一律清除。
                onParams({
                  workflow_mode: m,
                  cinematic_enabled: m.includes("宠物友好"),
                });
                if (m.includes("自由创作")) {
                  onModelTargets(modelTargets.filter((key) => key !== "sd35"));
                }
              }}
              className={cn(
                "rounded-xl border p-[12px] text-left transition",
                active
                  ? "border-primary bg-primary-soft ring-[3px] ring-[rgba(193,95,60,.1)]"
                  : "border-border bg-card",
              )}
            >
              <div className="flex items-center justify-between gap-1.5">
                <span
                  className={cn(
                    "text-[13px] font-bold",
                    active ? "text-accent-foreground" : "text-foreground",
                  )}
                >
                  {name}
                </span>
                {active && (
                  <span className="text-accent-foreground">
                    <Check />
                  </span>
                )}
              </div>
              <div
                className={cn(
                  "mt-[5px] text-[11px] leading-snug",
                  active ? "text-primary-2" : "text-muted-foreground",
                )}
              >
                {WF_SUB[name] ?? ""}
              </div>
            </button>
          );
        })}
      </div>

      {supportsCinematic && (
        <div className="mt-3 flex items-start justify-between gap-4 rounded-xl border border-border bg-card p-[13px]">
          <div className="min-w-0">
            <div className="text-[12.5px] font-bold text-secondary-foreground">🎬 电影真实感</div>
            <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">
              Gemini 会在生图前规划可信机位、自然动作、视线关系和现实光源，减少人物/宠物摆拍与 CG 感。
              地板规格仍由原技术提示词锁定；当前只影响 B2 / Pro。
            </p>
          </div>
          <Switch
            checked={!!params.cinematic_enabled}
            onCheckedChange={(checked) => onParams({ cinematic_enabled: checked })}
            aria-label="电影真实感"
          />
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

      {/* ── 源图上传：随工作流出现（地板替换=房间原图 / 参照模式=参照图）── */}
      {(isReplace || isRef) && (
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
                  🧹 清理家具（生成式移除）
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

      {/* ── 市场 + 模型线路 ── */}
      <div className="mt-[18px] flex gap-4">
        {!isOmakase && !isPanel && !isFree && (
        <div className="w-40 flex-none">
          <div className="mb-[7px] text-[11.5px] font-semibold text-muted-foreground">市场</div>
          <Segmented
            value={cnMode ? "cn" : "overseas"}
            options={[
              { value: "overseas", label: "海外" },
              { value: "cn", label: "国内" },
            ]}
            onChange={(v) => onParams({ cn_mode: v === "cn" })}
          />
        </div>
        )}
        <div className="flex-1">
          <div className="mb-[7px] text-[11.5px] font-semibold text-muted-foreground">模型线路</div>
          <div className={cn("grid gap-2", isFree ? "grid-cols-2" : "grid-cols-3")}>
            {([
              ["b2", "B2"],
              ["pro", "Pro"],
              ["sd35", "SD 3.5"],
            ] as const).filter(([key]) => !isFree || key !== "sd35").map(([key, label]) => {
              const active = modelTargets.includes(key);
              const disabled = key === "sd35" && !active && (!sdEnabled || !params.workflow_mode.includes("纯效果图"));
              return (
                <button
                  key={key}
                  type="button"
                  disabled={disabled}
                  onClick={() => toggleModel(key)}
                  title={key === "sd35" && disabled
                    ? (!sdEnabled ? "请先在设置中启用 SD 3.5" : "SD 3.5 当前仅支持纯效果图")
                    : `选择 ${label}`}
                  className={cn(
                    "h-10 rounded-[10px] border text-[12.5px] font-bold transition-colors",
                    active
                      ? "border-primary bg-primary-soft text-accent-foreground"
                      : "border-border bg-card text-secondary-foreground hover:bg-accent",
                    disabled && "cursor-not-allowed opacity-45",
                  )}
                >
                  {active ? "✓ " : ""}{label}
                </button>
              );
            })}
          </div>
          <div className="mt-1.5 text-[10.5px] text-muted-foreground">
            {isFree
              ? "B2 / Pro 会收到完全相同的自由指令与 Slot 顺序。"
              : "可同时选择多个模型并行生成；SD 3.5 使用独立提示词与地板参考图。"}
          </div>
        </div>
      </div>

      {modelTargets.includes("sd35") && (
        <div className="mt-3 rounded-xl border border-border bg-card">
          <button
            type="button"
            onClick={() => setSdOpen((v) => !v)}
            className="flex w-full items-center justify-between px-[13px] py-[11px]"
          >
            <span className="text-[12.5px] font-bold text-secondary-foreground">SD 3.5 高级参数</span>
            <span className="text-[11px] text-muted-foreground">{sdOpen ? "收起 ▲" : "展开 ▼"}</span>
          </button>
          {sdOpen && (
            <div className="grid grid-cols-2 gap-[11px] border-t border-border p-[13px]">
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">Seed（留空为随机）</span>
                <Input
                  type="number"
                  min={0}
                  value={sdOptions.seed ?? ""}
                  onChange={(e) => onSDOptions({ seed: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })}
                  className="h-10 rounded-[10px] bg-panel"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">采样步数（10–50）</span>
                <Input
                  type="number" min={10} max={50} value={sdOptions.steps}
                  onChange={(e) => onSDOptions({ steps: Number(e.target.value) })}
                  className="h-10 rounded-[10px] bg-panel"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">CFG（1–10）</span>
                <Input
                  type="number" min={1} max={10} step={0.1} value={sdOptions.guidance_scale}
                  onChange={(e) => onSDOptions({ guidance_scale: Number(e.target.value) })}
                  className="h-10 rounded-[10px] bg-panel"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">地板参考强度（0.1–1）</span>
                <Input
                  type="number" min={0.1} max={1} step={0.05} value={sdOptions.reference_strength}
                  onChange={(e) => onSDOptions({ reference_strength: Number(e.target.value) })}
                  className="h-10 rounded-[10px] bg-panel"
                />
              </label>
              <label className="col-span-2 flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">正向追加（不改内置结构）</span>
                <Textarea
                  rows={2} value={sdOptions.positive_addition}
                  onChange={(e) => onSDOptions({ positive_addition: e.target.value })}
                  placeholder="仅追加 Stable Diffusion 正向提示词…"
                  className="rounded-[9px] bg-panel"
                />
              </label>
              <label className="col-span-2 flex flex-col gap-1.5">
                <span className="text-[11.5px] font-semibold text-muted-foreground">负向追加（不改内置结构）</span>
                <Textarea
                  rows={2} value={sdOptions.negative_addition}
                  onChange={(e) => onSDOptions({ negative_addition: e.target.value })}
                  placeholder="例如：warped floor, duplicate furniture…"
                  className="rounded-[9px] bg-panel"
                />
              </label>
              <p className="col-span-2 text-[10.5px] leading-relaxed text-muted-foreground">
                基础图约 1MP；选择 2K/4K 时由 AuraSR 放大，再按目标长边交付。固定 Seed 可复现实验。
              </p>
            </div>
          )}
        </div>
      )}

      {!isOmakase && !isPanel && !isFree && (<>
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
      </>)}

      {/* ── 板材与工艺（墙板模式不适用：木纹/竖纹/哑光已由墙板模板固化，隐藏地板专用字段）── */}
      {!isPanel && !isFree && (<>
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
            ✨ OMAKASE · 场景代笔（必填）
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
              {omaLoading ? "生成中…" : "✨ Omakase 生成场景"}
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
                      {o.why && <p className="mt-1 text-[10.5px] text-muted-foreground">💡 {o.why}</p>}
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

      {!isOmakase && !isFree && !(isPanel && panelSub.includes("替换")) && (<>
      {/* ── 风格与镜头（墙板·替换保留原图风格/光影/镜头，故隐藏）── */}
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
      </>)}

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

      {!isOmakase && !isFree && (<>
      {/* ── 高级：回避清单 / 自定义补充 ── */}
      <button
        type="button"
        onClick={() => setAdvOpen((v) => !v)}
        className="mt-[18px] flex w-full items-center justify-between rounded-xl border border-border bg-card px-[13px] py-[11px] transition hover:bg-accent"
      >
        <span className="text-[12.5px] font-bold text-secondary-foreground">
          ⚙ 高级 · 回避清单 / 自定义补充
        </span>
        <span className="text-[11px] text-muted-foreground">{advOpen ? "收起 ▲" : "展开 ▼"}</span>
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
            <span className="text-[11.5px] font-semibold text-muted-foreground">自定义补充（可选）</span>
            <Textarea
              rows={2}
              value={params.custom_addition || ""}
              onChange={(e) => onParams({ custom_addition: e.target.value })}
              placeholder="可追加任何中/英文补充说明…"
              className="rounded-[9px] bg-panel"
            />
          </div>
        </div>
      )}
      </>)}
    </div>
  );
}
