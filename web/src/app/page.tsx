"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  GenParams,
  JobView,
  ModelFilter,
  OptionsView,
  Swatch,
} from "@/lib/types";
import { toast } from "sonner";
import { FloorUploader } from "@/components/FloorUploader";
import { ParamsForm } from "@/components/ParamsForm";
import { JobCard } from "@/components/JobCard";
import { Button } from "@/components/ui/button";

export default function GeneratePage() {
  const [options, setOptions] = useState<OptionsView | null>(null);
  const [floor, setFloor] = useState<Swatch | null>(null);
  const [refImg, setRefImg] = useState<Swatch | null>(null);
  const [modelFilter, setModelFilter] = useState<ModelFilter>("both");
  const [params, setParams] = useState<GenParams>({
    workflow_mode: "纯效果图 (生成全新空间)",
  });
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .getOptions()
      .then((o) => {
        setOptions(o);
        setParams((p) => ({
          ...p,
          workflow_mode: p.workflow_mode || o.workflow_modes[0],
          room_type: o.room_types[0],
          cn_room_type: o.cn_room_types[0],
          resolution: o.resolutions[0],
          seam_type: o.seam_types[0],
          glossiness: o.glossiness[1] || o.glossiness[0],
          floor_tone: o.floor_tones[0],
        }));
      })
      .catch((e) => toast.error("加载选项失败：" + (e as Error).message));
    api.listJobs(50).then(setJobs).catch(() => {});
  }, []);

  const updateParams = (patch: Partial<GenParams>) =>
    setParams((p) => ({ ...p, ...patch }));

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

  return (
    <div className="mx-auto grid max-w-7xl grid-cols-1 gap-4 p-4 lg:grid-cols-[360px_1fr]">
      <aside className="space-y-4">
        <section className="rounded-xl border bg-background p-3">
          <h2 className="mb-2 text-sm font-semibold">地板小样</h2>
          <FloorUploader value={floor} onPick={setFloor} />
        </section>

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

        <Button
          className="w-full"
          size="lg"
          disabled={submitting || !floor}
          onClick={generate}
        >
          {submitting ? "提交中…" : "⚡ 生成"}
        </Button>
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
    </div>
  );
}
