"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { requestNotifyPermission } from "@/lib/notify";
import type {
  GenParams,
  JobView,
  ModelFilter,
  OptionsView,
  ResolvedRecipe,
  Swatch,
} from "@/lib/types";
import { toast } from "sonner";
import { FloorUploader } from "@/components/FloorUploader";
import { ParamsForm } from "@/components/ParamsForm";
import { JobCard } from "@/components/JobCard";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { SectionHeader, Pill } from "@/components/dc-ui";

export default function GeneratePage() {
  const [options, setOptions] = useState<OptionsView | null>(null);
  const [floor, setFloor] = useState<Swatch | null>(null);
  const [refImg, setRefImg] = useState<Swatch | null>(null);
  const [modelFilter, setModelFilter] = useState<ModelFilter>("both");
  const [params, setParams] = useState<GenParams>({
    workflow_mode: "纯效果图 (生成全新空间)",
  });
  const [recipes, setRecipes] = useState<ResolvedRecipe[]>([]);
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchRooms, setBatchRooms] = useState<string[]>([]);

  useEffect(() => {
    requestNotifyPermission();
    api
      .getOptions()
      .then((o) => {
        setOptions(o);
        const cont = o.continents[0];
        const country = Object.keys(o.location_map[cont] || {})[0] || "";
        const city = (o.location_map[cont]?.[country] || [])[0] || "";
        setParams((p) => ({
          ...p,
          workflow_mode: p.workflow_mode || o.workflow_modes[0],
          continent: cont,
          country,
          city,
          property_type: o.property_types[0],
          room_type: o.room_types[0],
          view: o.views[0],
          market_furniture: o.market_furniture[0],
          cn_room_type: o.cn_room_types[0],
          cn_view: o.views[0],
          cn_developer: o.cn_developers[0],
          cn_city: o.cn_cities[0],
          cn_tier: o.cn_tiers[0],
          cn_unit_type: o.cn_unit_types[0],
          cn_delivery: o.cn_delivery_choices[0],
          cn_space_features: [],
          cn_facilities: [],
          floor_size: o.floor_sizes[0],
          seam_type: o.seam_types[0],
          glossiness: o.glossiness[1] || o.glossiness[0],
          floor_tone: o.floor_tones[0],
          style_type: o.styles[0],
          lighting: o.lightings[0],
          angle: o.angles[0],
          aspect_ratio: o.aspect_ratios[0],
          resolution: o.resolutions[0],
          pet_type: o.pet_types[0],
          pet_action: o.pet_actions[0],
          pet_focus: o.pet_focus[0],
          avoid_items: o.avoid_items,
        }));
      })
      .catch((e) => toast.error("加载选项失败：" + (e as Error).message));
    api.listJobs(50).then(setJobs).catch(() => {});
  }, []);

  // 队列整体进度：轮询任务列表（卡片各自走 SSE，这里只为聚合进度/新任务出现）
  useEffect(() => {
    const t = setInterval(() => {
      api.listJobs(50).then(setJobs).catch(() => {});
    }, 2500);
    return () => clearInterval(t);
  }, []);

  const updateParams = (patch: Partial<GenParams>) =>
    setParams((p) => ({ ...p, ...patch }));

  async function pickFloor(s: Swatch) {
    setFloor(s);
    try {
      const a = await api.floorAnalyze(s.path);
      updateParams({ floor_tone: a.tone });
      setRecipes(a.recipes || []);
      toast.success("已识别色调，并给出智能配方");
    } catch {
      /* 识色失败不阻断，用户仍可手选 */
    }
  }

  function applyRecipe(r: ResolvedRecipe) {
    updateParams({
      style_type: r.style_type || params.style_type,
      lighting: r.lighting || params.lighting,
      angle: r.angle || params.angle,
      aspect_ratio: r.aspect_ratio || params.aspect_ratio,
      resolution: r.resolution || params.resolution,
    });
    toast.success(`已套用配方：${r.label}`);
  }

  async function generate() {
    if (!floor) {
      toast.warning("请先上传地板图");
      return;
    }
    if (params.workflow_mode.includes("参照模式") && !refImg) {
      toast.warning("参照模式需上传参照图");
      return;
    }
    setSubmitting(true);
    try {
      const job = await api.createJob({
        image_path: floor.path,
        model_filter: modelFilter,
        room_path: null,
        ref_path: refImg?.path ?? null,
        params,
      });
      setJobs((j) => [job, ...j]);
      toast.success("任务已提交");
    } catch (e) {
      toast.error("提交失败：" + (e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  async function runBatch() {
    if (!floor) {
      toast.warning("请先上传地板图");
      return;
    }
    if (batchRooms.length === 0) {
      toast.warning("请至少勾选一个房间类型");
      return;
    }
    const cnMode = !!params.cn_mode;
    try {
      const created = await Promise.all(
        batchRooms.map((room) =>
          api.createJob({
            image_path: floor.path,
            model_filter: modelFilter,
            room_path: null,
            ref_path: refImg?.path ?? null,
            params: cnMode
              ? { ...params, cn_room_type: room }
              : { ...params, room_type: room },
          }),
        ),
      );
      setJobs((j) => [...created.reverse(), ...j]);
      toast.success(`已批量提交 ${created.length} 个房间`);
      setBatchOpen(false);
    } catch (e) {
      toast.error("批量提交失败：" + (e as Error).message);
    }
  }

  const total = jobs.length;
  const activeCount = jobs.filter(
    (j) => j.status === "queued" || j.status === "running" || j.pro_polishing,
  ).length;
  const doneCount = total - activeCount;
  const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

  const batchRoomOptions = options
    ? params.cn_mode
      ? options.cn_room_types
      : options.room_types
    : [];

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── 左：参数列 ── */}
      <section className="flex w-[600px] flex-none flex-col border-r border-border bg-panel">
        <div className="flex-1 space-y-0 overflow-y-auto px-5 pb-2.5 pt-4">
          <SectionHeader className="mx-0.5 mb-[11px] mt-1">
            地板小样 / SWATCH
          </SectionHeader>
          <FloorUploader value={floor} onPick={pickFloor} />

          {recipes.length > 0 && (
            <>
              <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
                智能配方 / 按色调推荐
              </SectionHeader>
              <div className="flex gap-[9px] overflow-x-auto pb-1.5">
                {recipes.map((r) => (
                  <button
                    key={r.key}
                    onClick={() => applyRecipe(r)}
                    className="w-[172px] flex-none rounded-xl border border-border bg-card p-[11px] text-left transition hover:border-primary hover:shadow-[0_6px_16px_rgba(120,90,60,.1)]"
                  >
                    <div className="mb-[7px] flex items-center gap-[7px]">
                      <span
                        className="h-[18px] w-[18px] flex-none rounded-md ring-1 ring-black/5"
                        style={{
                          background: "linear-gradient(135deg,#d8b48a,#bf945f)",
                        }}
                      />
                      <span className="truncate text-[13px] font-bold text-[#2a241f]">
                        {r.label}
                      </span>
                    </div>
                    <div className="line-clamp-2 text-[11px] leading-relaxed text-[#857c6e]">
                      {r.sub}
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}

          {options ? (
            <ParamsForm
              options={options}
              params={params}
              modelFilter={modelFilter}
              onParams={updateParams}
              onModelFilter={setModelFilter}
              refValue={refImg}
              onRefPick={setRefImg}
            />
          ) : (
            <div className="mt-4 text-sm text-muted-foreground">加载选项中…</div>
          )}
          <div className="h-2" />
        </div>

        {/* sticky 底栏 */}
        <div className="flex flex-none gap-2.5 border-t border-border bg-card px-[18px] py-[13px]">
          <button
            onClick={generate}
            disabled={submitting || !floor}
            className="flex h-[46px] flex-1 items-center justify-center gap-2 rounded-xl bg-primary text-[14.5px] font-bold text-primary-foreground shadow-[0_6px_16px_rgba(193,95,60,.32)] transition hover:bg-[#a8472a] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <path d="M13 2L4.5 13.5H11l-1 8.5L19.5 10H13l0-8z" />
            </svg>
            {submitting ? "提交中…" : "生成效果图"}
          </button>
          <button
            onClick={() => setBatchOpen(true)}
            disabled={!floor || !options}
            className="h-[46px] flex-none rounded-xl border border-border bg-card px-[18px] text-[13.5px] font-bold text-[#6b6356] transition hover:bg-[#f2e9e0] disabled:cursor-not-allowed disabled:opacity-50"
          >
            批量
          </button>
        </div>
      </section>

      {/* ── 右：任务队列 ── */}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        <div className="flex flex-none items-center justify-between border-b border-border px-[22px] py-[14px]">
          <div className="flex items-baseline gap-2.5">
            <span className="text-[14.5px] font-bold">任务队列</span>
            <span className="text-[12px] text-[#9a9082]">
              {total} 个任务 · 完成 {doneCount}/{total}
            </span>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => api.listJobs(50).then(setJobs)}
              className="h-[30px] rounded-lg border border-border bg-card px-3 text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
            >
              刷新
            </button>
            <button
              onClick={() =>
                api
                  .clearCompleted()
                  .then((r) => toast.success(`已清除 ${r.cleared} 个已完成`))
                  .then(() => api.listJobs(50).then(setJobs))
                  .catch((e) => toast.error((e as Error).message))
              }
              className="h-[30px] rounded-lg border border-border bg-card px-3 text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
            >
              清除已完成
            </button>
            <button
              onClick={() =>
                api
                  .cancelAll()
                  .then((r) => toast.success(`已停止 ${r.stopped} 个`))
                  .then(() => api.listJobs(50).then(setJobs))
                  .catch((e) => toast.error((e as Error).message))
              }
              className="h-[30px] rounded-lg border border-border bg-card px-3 text-[12.5px] font-semibold text-[#b5503a] hover:bg-[#f9e7e2]"
            >
              全部停止
            </button>
          </div>
        </div>

        {activeCount > 0 && (
          <div className="flex-none border-b border-border bg-card px-[22px] py-[11px]">
            <div className="mb-1.5 flex justify-between text-[11.5px] text-[#857c6e]">
              <span>
                进行中 {activeCount} · 完成 {doneCount} / 共 {total}
              </span>
              <span className="font-bold text-[#2a241f]">{pct}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-md bg-[#efe9dc]">
              <div
                className="h-full rounded-md bg-primary transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-[22px] py-[18px]">
          {jobs.length === 0 ? (
            <div className="rounded-2xl border-[1.5px] border-dashed border-[#d3c8b3] px-5 py-[54px] text-center text-[13px] text-[#9a9082]">
              还没有任务 · 左侧上传地板图并点「生成效果图」
            </div>
          ) : (
            <div className="grid items-start gap-4 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
              {jobs.map((j) => (
                <JobCard
                  key={j.job_id}
                  initial={j}
                  onRemove={(id) =>
                    setJobs((s) => s.filter((x) => x.job_id !== id))
                  }
                />
              ))}
            </div>
          )}
        </div>
      </section>

      {/* 批量跨房间 */}
      <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
        <DialogContent className="max-w-[520px]">
          <div className="space-y-3">
            <div>
              <div className="text-[15.5px] font-bold">批量生成</div>
              <div className="mt-0.5 text-[12px] text-[#9a9082]">
                同一地板 × 多个房间类型，一次性提交
              </div>
            </div>
            <div className="flex flex-wrap gap-[7px] pt-1">
              {batchRoomOptions.map((rt) => {
                const on = batchRooms.includes(rt);
                return (
                  <Pill
                    key={rt}
                    active={on}
                    onClick={() =>
                      setBatchRooms((s) =>
                        on ? s.filter((x) => x !== rt) : [...s, rt],
                      )
                    }
                  >
                    {rt}
                  </Pill>
                );
              })}
            </div>
            <div className="flex items-center justify-between pt-1">
              <div className="flex gap-2">
                <button
                  onClick={() => setBatchRooms(batchRoomOptions)}
                  className="h-8 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
                >
                  全选
                </button>
                <button
                  onClick={() => setBatchRooms([])}
                  className="h-8 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
                >
                  清空
                </button>
              </div>
              <button
                onClick={runBatch}
                disabled={batchRooms.length === 0}
                className="h-[38px] rounded-[10px] bg-primary px-5 text-[13.5px] font-bold text-primary-foreground hover:bg-[#a8472a] disabled:opacity-50"
              >
                提交批量 ({batchRooms.length})
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
