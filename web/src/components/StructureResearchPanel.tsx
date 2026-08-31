"use client";

/* eslint-disable @next/next/no-img-element */
import { useState } from "react";
import { Box, CheckCircle2, Download, LoaderCircle, RefreshCw, TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import type { DesignModelRun, WholeHomeDesignProject } from "@/lib/types";
import { Button } from "@/components/ui/button";

const RUN_STATUS_LABELS: Record<DesignModelRun["status"], string> = {
  queued: "等待建模", building: "Blender 建模中", mechanical_verified: "机械验证通过",
  external_review_pending: "等待 Gemini 复审", needs_correction: "需要结构校正",
  ready_research: "研究灰模可用", blocked_dependency_missing: "缺少本地依赖",
  failed_product: "建模失败", interrupted: "重启中断", cancelled: "已取消",
};

export function StructureResearchPanel({ project, busy, onPrepare, onSubmit, onStartModel, onRetryReview }: {
  project: WholeHomeDesignProject;
  busy: string;
  onPrepare: () => void;
  onSubmit: (answers: Record<string, string>) => void;
  onStartModel: () => void;
  onRetryReview: (run: DesignModelRun) => void;
}) {
  const review = project.structure_review;
  const [answers, setAnswers] = useState<Record<string, string>>(review?.answers || {});
  const complete = Boolean(review?.questions?.length) && review.questions.every((question) => Boolean(answers[question.id]));
  const activeRuns = (project.model_runs || []).filter((run) => !run.stale);

  return <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
    <div className="flex flex-wrap items-center gap-3">
      <Box className="text-primary" />
      <div><h2 className="font-extrabold">3. 九问结构确认与 Blender 研究灰模</h2><p className="mt-1 text-xs text-muted-foreground">不等待 2K 概念图；原户型和人工比例尺始终是几何权威。</p></div>
      <span className="ml-auto rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-800">研究模式 · 非施工 BIM</span>
    </div>

    {review?.status === "not_run" && <div className="mt-4 rounded-xl border border-dashed p-5 text-center"><p className="text-sm text-muted-foreground">确认户型摘要后，先让系统生成墙、门窗和连通候选，再回答九个普通问题。</p><Button className="mt-3" disabled={!project.plan_summary_confirmed || busy === "structure-prepare"} onClick={onPrepare}>{busy === "structure-prepare" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}准备九问与结构候选</Button></div>}

    {review?.status !== "not_run" && <>
      {review.error && <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900"><b>外部结构审查暂不可用。</b><div className="mt-1">{review.error}</div><div className="mt-1">这不会计入产品失败，也不会删除本地答案；待线路恢复后可重新准备。</div></div>}
      {review.scale_calibration && <div className="mt-4 rounded-xl border border-emerald-300 bg-emerald-50 p-3 text-xs text-emerald-900">人工比例尺：{review.scale_calibration.distance_mm.toLocaleString()} mm · {review.scale_calibration.metres_per_pixel.toFixed(6)} m/px</div>}
      <div className="mt-4 space-y-3">{(review.questions || []).map((question, index) => <article key={question.id} className="rounded-xl border border-border p-3">
        <div className="text-xs font-bold text-primary">问题 {index + 1}</div><h3 className="mt-1 text-sm font-extrabold">{question.title}</h3><p className="mt-1 text-sm">{question.prompt}</p><p className="mt-1 text-[11px] text-muted-foreground">{question.hint}</p>
        <div className="mt-3 flex flex-wrap gap-2">{question.choices.map((choice) => <label key={choice.value} className={`cursor-pointer rounded-lg border px-3 py-2 text-xs ${answers[question.id] === choice.value ? "border-primary bg-primary/10 font-bold text-primary" : "border-border"}`}><input className="sr-only" type="radio" name={question.id} value={choice.value} checked={answers[question.id] === choice.value} onChange={() => setAnswers((value) => ({ ...value, [question.id]: choice.value }))} />{choice.label}</label>)}</div>
      </article>)}</div>
      {!!review.questions?.length && <div className="mt-4 flex flex-wrap justify-end gap-2"><Button variant="outline" disabled={busy === "structure-prepare"} onClick={onPrepare}><RefreshCw />重新读取结构</Button><Button disabled={!complete || busy === "structure-save"} onClick={() => onSubmit(answers)}>{busy === "structure-save" ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}保存九问并编译结构图</Button></div>}
      {review.unresolved?.length > 0 && <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900"><b>仍需校正</b>{review.unresolved.map((item) => <div key={item}>• {item}</div>)}</div>}
      {review.status === "verified" && <div className="mt-4 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-emerald-950"><div className="flex flex-wrap items-center gap-3"><CheckCircle2 /><div><b>研究结构图已编译</b><div className="font-mono text-[10px]">SHA256 {review.structure_hash.slice(0, 20)}…</div></div><Button className="ml-auto" disabled={busy === "model-run" || activeRuns.some((run) => ["queued", "building"].includes(run.status))} onClick={onStartModel}>{busy === "model-run" ? <LoaderCircle className="animate-spin" /> : <Box />}生成 Blender / GLB / 研究 IFC</Button></div></div>}
    </>}

    <div className="mt-4 space-y-4">{activeRuns.map((run) => {
      const top = run.artifacts.find((artifact) => artifact.kind === "top");
      const waiting = ["queued", "building"].includes(run.status);
      return <article key={run.run_id} className="overflow-hidden rounded-xl border border-border">
        {top && <img src={api.imgUrl(top.download_url)} alt="Blender研究灰模顶视图" className="max-h-[520px] w-full bg-stone-100 object-contain" />}
        <div className="p-3"><div className="flex flex-wrap items-center gap-2"><b className="text-sm">{run.run_id}</b><span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${run.status === "ready_research" ? "bg-emerald-100 text-emerald-800" : run.status === "failed_product" || run.status === "needs_correction" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800"}`}>{RUN_STATUS_LABELS[run.status]}</span>{waiting && <LoaderCircle className="animate-spin" size={16} />}</div><p className="mt-1 text-xs text-muted-foreground">{run.stage}</p>{run.error && <p className="mt-2 text-xs text-amber-800">{run.error}</p>}
          <div className="mt-3 flex flex-wrap gap-2">{run.artifacts.map((artifact) => <a key={artifact.kind} href={api.imgUrl(artifact.download_url)} className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs font-bold hover:border-primary"><Download size={14} />{artifact.filename}</a>)}{run.status === "external_review_pending" && <Button size="sm" variant="outline" disabled={busy === "model-review"} onClick={() => onRetryReview(run)}><RefreshCw />重试 Gemini 审查</Button>}</div>
          {run.status === "external_review_pending" && <div className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-900"><TriangleAlert size={15} />本地研究模型可下载；Gemini 复合审查未完成，因此不会晋级正式 BIM。</div>}
        </div>
      </article>;
    })}</div>
  </section>;
}
