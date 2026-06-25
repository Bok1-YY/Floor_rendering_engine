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
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

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
    <div className="mx-auto grid max-w-7xl grid-cols-1 gap-4 p-4 lg:grid-cols-[360px_1fr]">
      <aside className="space-y-4">
        <section className="rounded-xl border bg-background p-3">
          <h2 className="mb-2 text-sm font-semibold">地板小样</h2>
          <FloorUploader value={floor} onPick={pickFloor} />
        </section>

        {recipes.length > 0 && (
          <section className="rounded-xl border bg-background p-3">
            <h2 className="mb-2 text-sm font-semibold">✨ 智能配方（按色调推荐）</h2>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {recipes.map((r) => (
                <button
                  key={r.key}
                  onClick={() => applyRecipe(r)}
                  className="w-36 shrink-0 rounded-lg border bg-muted/20 p-2 text-left hover:border-primary"
                >
                  <div className="truncate text-xs font-medium">{r.label}</div>
                  <div className="line-clamp-2 text-[11px] text-muted-foreground">
                    {r.sub}
                  </div>
                </button>
              ))}
            </div>
          </section>
        )}

        <section className="rounded-xl border bg-background p-3">
          <h2 className="mb-2 text-sm font-semibold">参数</h2>
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
            <div className="text-sm text-muted-foreground">加载选项中…</div>
          )}
        </section>

        <div className="flex gap-2">
          <Button
            className="flex-1"
            size="lg"
            disabled={submitting || !floor}
            onClick={generate}
          >
            {submitting ? "提交中…" : "⚡ 生成"}
          </Button>
          <Button
            size="lg"
            variant="outline"
            disabled={!floor || !options}
            onClick={() => setBatchOpen(true)}
          >
            批量
          </Button>
        </div>
      </aside>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">任务队列</h2>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => api.listJobs(50).then(setJobs)}
            >
              刷新
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                api
                  .cancelAll()
                  .then((r) => toast.success(`已停止 ${r.stopped} 个`))
                  .then(() => api.listJobs(50).then(setJobs))
                  .catch((e) => toast.error((e as Error).message))
              }
            >
              全部停止
            </Button>
          </div>
        </div>

        {activeCount > 0 && (
          <div className="rounded-lg border bg-background p-2">
            <div className="mb-1 flex justify-between text-xs text-muted-foreground">
              <span>进行中 {activeCount} · 完成 {doneCount} / 共 {total}</span>
              <span>{pct}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}

        {jobs.length === 0 ? (
          <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
            还没有任务，左侧上传地板图并点「⚡ 生成」。
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {jobs.map((j) => (
              <JobCard key={j.job_id} initial={j} />
            ))}
          </div>
        )}
      </section>

      {/* 批量跨房间 */}
      <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
        <DialogContent>
          <div className="space-y-3">
            <div className="text-sm font-medium">
              批量生成（同一地板 × 多个房间类型）
            </div>
            <div className="flex flex-wrap gap-1.5">
              {batchRoomOptions.map((rt) => {
                const on = batchRooms.includes(rt);
                return (
                  <button
                    key={rt}
                    onClick={() =>
                      setBatchRooms((s) =>
                        on ? s.filter((x) => x !== rt) : [...s, rt],
                      )
                    }
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs",
                      on
                        ? "border-primary bg-primary text-primary-foreground"
                        : "bg-background hover:bg-muted",
                    )}
                  >
                    {rt}
                  </button>
                );
              })}
            </div>
            <div className="flex items-center justify-between">
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setBatchRooms(batchRoomOptions)}
                >
                  全选
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setBatchRooms([])}
                >
                  清空
                </Button>
              </div>
              <Button size="sm" onClick={runBatch} disabled={batchRooms.length === 0}>
                提交批量 ({batchRooms.length})
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
