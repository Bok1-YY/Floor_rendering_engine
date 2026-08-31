"use client";

/* eslint-disable @next/next/no-img-element */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Archive,
  Check,
  CheckCircle2,
  Download,
  FileImage,
  ImagePlus,
  LoaderCircle,
  LockKeyhole,
  ZoomIn,
  Plus,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type {
  DesignFloorplanUpload,
  DesignPlanRoom,
  DesignPlanAnchor,
  DesignReferenceUpload,
  WholeHomeDesignCandidate,
  DesignModelRun,
  WholeHomeDesignPaidPreview,
  WholeHomeDesignProject,
  WholeHomeDesignProjectListItem,
} from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ImageZoom } from "@/components/ImageZoom";
import { FloorplanAnchorEditor } from "@/components/FloorplanAnchorEditor";
import { StructureResearchPanel } from "@/components/StructureResearchPanel";

const REVIEW_ITEMS = [
  ["orientation_and_crop", "朝向、镜像和裁切与原户型一致"],
  ["outer_footprint", "外轮廓、凹凸边界和阳台投影一致"],
  ["room_count_and_positions", "房间数量、类型和相对位置一致"],
  ["partitions_and_adjacencies", "隔墙与房间相邻关系一致"],
  ["entrance_balcony_and_openings", "入口、阳台和主要门窗一致"],
  ["kitchen_bathroom_wet_zones", "厨房、卫生间和湿区没有移动"],
  ["no_added_or_missing_spaces", "没有新增、删除或合并空间"],
  ["orthographic_topdown_view", "是垂直正俯视正交 2.5D，而非斜轴测"],
  ["no_labels_dimensions_or_watermarks", "画面没有文字、尺寸、图例或水印"],
] as const;

const ACTIVE = new Set(["analyzing_plan", "verifying_plan", "generating_drafts", "refining", "interrupted"]);

function csv(value: string[]) {
  return value.join("\n");
}

function rows(value: string) {
  return value.split(/\r?\n|，|,/).map((item) => item.trim()).filter(Boolean);
}

function statusLabel(project: Pick<WholeHomeDesignProject, "status">) {
  const labels: Record<string, string> = {
    needs_anchor_review: "待标注锚点",
    needs_plan_review: "待确认户型",
    analyzing_plan: "正在识别户型",
    verifying_plan: "正在复核户型",
    needs_brief: "待填写需求",
    ready: "可生成草稿",
    draft_previewed: "待确认费用",
    generating_drafts: "草稿生成中",
    needs_draft_selection: "待选草稿",
    needs_structure_review: "待结构核对",
    locked: "已锁定",
    interrupted: "恢复任务中",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[project.status] || project.status;
}

function qaTone(candidate: WholeHomeDesignCandidate) {
  if (candidate.structure_qa?.hard_fail) return "border-red-300 bg-red-50 text-red-800";
  if (candidate.structure_qa?.status === "passed") return "border-emerald-300 bg-emerald-50 text-emerald-800";
  return "border-amber-300 bg-amber-50 text-amber-800";
}

function qaLabel(candidate: WholeHomeDesignCandidate) {
  if (candidate.structure_qa?.hard_fail) return "结构硬错误";
  if (candidate.structure_qa?.status === "passed") return "结构检查通过";
  if (candidate.structure_qa?.status === "manual_required") return "需要人工核对";
  return candidate.status === "done" ? "等待结构检查" : candidate.stage || candidate.status;
}

export default function WholeHomeDesignPage() {
  const fileInput = useRef<HTMLInputElement>(null);
  const referenceInput = useRef<HTMLInputElement>(null);
  const [projects, setProjects] = useState<WholeHomeDesignProjectListItem[]>([]);
  const [project, setProject] = useState<WholeHomeDesignProject | null>(null);
  const [uploadResult, setUploadResult] = useState<DesignFloorplanUpload | null>(null);
  const [uploading, setUploading] = useState(false);
  const [orientationPolicy, setOrientationPolicy] = useState<"exif_transpose-v1" | "ignore_invalid_exif_user_confirmed_raw">("exif_transpose-v1");
  const [busy, setBusy] = useState("");
  const [references, setReferences] = useState<DesignReferenceUpload[]>([]);
  const [requirements, setRequirements] = useState("");
  const [roomsState, setRoomsState] = useState<DesignPlanRoom[]>([]);
  const [hasGemini, setHasGemini] = useState<boolean | null>(null);
  const [declaredLayout, setDeclaredLayout] = useState({ bedrooms: 0, halls: 0, bathrooms: 0, source_text: "", confidence: 0 });
  const [declaredArea, setDeclaredArea] = useState(0);
  const [overallDimensions, setOverallDimensions] = useState({ width: 0, depth: 0, evidence: [] as string[], confidence: 0 });
  const [listFields, setListFields] = useState({
    entrances: "", openings_summary: "", wet_zones: "", balconies: "",
    dimension_evidence: "", must_preserve: "", uncertainties: "",
  });
  const [preview, setPreview] = useState<WholeHomeDesignPaidPreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [zoomUrl, setZoomUrl] = useState<string | null>(null);
  const [designProvider, setDesignProvider] = useState<"google" | "fal">("google");
  const [reviewCandidate, setReviewCandidate] = useState<WholeHomeDesignCandidate | null>(null);
  const [reviewChecks, setReviewChecks] = useState<Record<string, boolean>>({});
  const [reviewNote, setReviewNote] = useState("");

  const refreshProject = useCallback((id: string) => {
    api.getWholeHomeDesignProject(id).then((value) => {
      setProject(value);
      setProjects((items) => [value, ...items.filter((item) => item.project_id !== value.project_id)]);
    }).catch((error) => toast.error(String(error)));
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const [items, config] = await Promise.all([
          api.listWholeHomeDesignProjects(),
          api.getConfig(),
        ]);
        if (!active) return;
        setProjects(items);
        setHasGemini(config.has_gemini_key);
        if (!items[0]) return;

        const detail = await api.getWholeHomeDesignProject(items[0].project_id);
        if (!active) return;
        setProject((current) => current ?? detail);
        setProjects((current) => [
          detail,
          ...current.filter((item) => item.project_id !== detail.project_id),
        ]);
      } catch (error) {
        if (active) toast.error(`加载全屋设计项目失败：${String(error)}`);
      }
    })();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!project || (!ACTIVE.has(project.status) && !(project.model_runs || []).some((run) => ["queued", "building"].includes(run.status)))) return;
    const id = project.project_id;
    const timer = window.setInterval(() => refreshProject(id), 2000);
    return () => window.clearInterval(timer);
  }, [project, refreshProject]);

  useEffect(() => {
    if (!project) return;
    const timer = window.setTimeout(() => {
      setRoomsState(project.plan_summary?.rooms || []);
      setDeclaredLayout(project.plan_summary?.declared_layout || { bedrooms: 0, halls: 0, bathrooms: 0, source_text: "", confidence: 0 });
      setDeclaredArea(project.plan_summary?.declared_area_m2 || 0);
      setOverallDimensions(project.plan_summary?.overall_dimensions_mm || { width: 0, depth: 0, evidence: [], confidence: 0 });
      setListFields({
        entrances: csv(project.plan_summary?.entrances || []),
        openings_summary: csv(project.plan_summary?.openings_summary || []),
        wet_zones: csv(project.plan_summary?.wet_zones || []),
        balconies: csv(project.plan_summary?.balconies || []),
        dimension_evidence: csv(project.plan_summary?.dimension_evidence || []),
        must_preserve: csv(project.plan_summary?.must_preserve || []),
        uncertainties: csv(project.plan_summary?.uncertainties || []),
      });
      setRequirements(project.brief?.requirements_text || "");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [project]);

  async function uploadPlan(file: File) {
    setUploading(true);
    try {
      const value = await api.uploadDesignFloorplan(file);
      setUploadResult(value);
      if (value.kind === "image") await startProject(value.path, value.name);
    } catch (error) {
      toast.error(`户型上传失败：${String(error)}`);
    } finally {
      setUploading(false);
    }
  }

  async function startProject(path: string, name: string) {
    setBusy("create");
    try {
      const value = await api.createWholeHomeDesignProject(path, name, orientationPolicy);
      setProject(value);
      setProjects((items) => [value, ...items]);
      setUploadResult(null);
      toast.success("项目已创建；请先点选全部空间、入户门和一条真实比例尺");
    } catch (error) {
      toast.error(`创建失败：${String(error)}`);
    } finally {
      setBusy("");
    }
  }

  async function savePlanSummary() {
    if (!project) return;
    setBusy("summary");
    try {
      const value = await api.saveWholeHomeDesignPlanSummary(project.project_id, {
        base_revision: project.revision,
        room_count: roomsState.length,
        rooms: roomsState,
        declared_layout: declaredLayout,
        declared_area_m2: declaredArea,
        overall_dimensions_mm: overallDimensions,
        summary_confidence: project.plan_summary?.summary_confidence || 0,
        review_items: project.plan_summary?.review_items || [],
        annotation_boxes: project.plan_summary?.annotation_boxes || [],
        entrances: rows(listFields.entrances),
        openings_summary: rows(listFields.openings_summary),
        wet_zones: rows(listFields.wet_zones),
        balconies: rows(listFields.balconies),
        dimension_evidence: rows(listFields.dimension_evidence),
        must_preserve: rows(listFields.must_preserve),
        uncertainties: rows(listFields.uncertainties),
        confirmed: true,
      });
      setProject(value);
      toast.success("户型摘要已确认；原图仍是唯一结构权威");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function analyzePlanAgain() {
    if (!project) return;
    if (!hasGemini) return toast.warning("请先到设置页配置 Gemini API Key");
    setBusy("analyze-plan");
    try {
      const value = await api.analyzeWholeHomeDesignPlan(project.project_id, project.revision);
      setProject(value);
      toast.success("正在自动读取标题、尺寸、空间角色和待确认项");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function saveAnchorsAndAnalyze(anchors: DesignPlanAnchor[], confirmedComplete: boolean) {
    if (!project) return;
    setBusy("anchors");
    try {
      const saved = await api.saveWholeHomeDesignAnchors(project.project_id, {
        base_revision: project.revision,
        coordinate_space: "normalized-evidence-1000-v1",
        source_hash: project.source_hash,
        confirmed_complete: confirmedComplete,
        anchors,
      });
      setProject(saved);
      if (hasGemini) {
        const analyzing = await api.analyzeWholeHomeDesignPlan(saved.project_id, saved.revision);
        setProject(analyzing);
        toast.success("人工锚点已保存；Gemini 正在进行提取与独立复核");
      } else {
        toast.success("人工锚点和比例尺已保存；可以先人工确认摘要，Gemini 恢复后再复核");
      }
    } catch (error) {
      toast.error(`锚点识别失败：${String(error)}`);
    } finally {
      setBusy("");
    }
  }

  async function uploadReference(file: File) {
    if (references.length >= 4) return toast.warning("最多上传 4 张风格或材料参考图");
    setBusy("reference");
    try {
      const value = await api.uploadDesignReference(file);
      setReferences((items) => [...items, value]);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function saveBrief() {
    if (!project || !requirements.trim()) return toast.warning("请填写完整的自由设计需求");
    setBusy("brief");
    try {
      const value = await api.saveWholeHomeDesignBrief(project.project_id, {
        base_revision: project.revision,
        requirements_text: requirements,
        reference_paths: references.map((item) => item.path),
      });
      setProject(value);
      toast.success("设计要求已保存；修改要求会使旧候选自动过期");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function openDraftPreview() {
    if (!project) return;
    setBusy("preview");
    try {
      const value = await api.previewWholeHomeDesignDrafts(project.project_id, project.revision, designProvider);
      setPreview(value);
      setConfirmation("");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function commitPreview() {
    if (!project || !preview) return;
    if (confirmation !== preview.confirmation_phrase) return toast.warning("请输入完整确认短语");
    setBusy("commit");
    const body = {
      base_revision: project.revision,
      preview_id: preview.preview_id,
      preview_hash: preview.preview_hash,
      confirmation_phrase: confirmation,
      idempotency_key: `${preview.preview_id}-web-client`,
    };
    try {
      const value = await api.commitWholeHomeDesignDrafts(project.project_id, body);
      setProject(value);
      setPreview(null);
      toast.success("两张 2K 方案已提交");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  function openReview(candidate: WholeHomeDesignCandidate) {
    setReviewCandidate(candidate);
    setReviewChecks(candidate.human_review?.checks || {});
    setReviewNote(candidate.human_review?.note || "");
  }

  async function submitReview() {
    if (!project || !reviewCandidate) return;
    if (REVIEW_ITEMS.some(([key]) => reviewChecks[key] !== true)) {
      return toast.warning("必须逐项核对并确认全部结构硬项");
    }
    setBusy("review");
    try {
      const value = await api.reviewWholeHomeDesignStructure(
        project.project_id, reviewCandidate.candidate_id, {
          base_revision: project.revision,
          checks: reviewChecks,
          decision: "pass",
          reviewer: "local-user",
          note: reviewNote,
        });
      setProject(value);
      setReviewCandidate(null);
      toast.success("人工结构核对已保存");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function rejectReview() {
    if (!project || !reviewCandidate) return;
    if (!reviewNote.trim()) return toast.warning("标记结构失败时请写明发现的问题");
    setBusy("review");
    try {
      const value = await api.reviewWholeHomeDesignStructure(
        project.project_id, reviewCandidate.candidate_id, {
          base_revision: project.revision,
          checks: reviewChecks,
          decision: "fail",
          reviewer: "local-user",
          note: reviewNote,
        });
      setProject(value);
      setReviewCandidate(null);
      toast.error("该候选已标记为结构失败，不能进入精修或锁定");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function lockCandidate(candidate: WholeHomeDesignCandidate) {
    if (!project) return;
    setBusy("lock");
    try {
      const value = await api.lockWholeHomeDesignCandidate(
        project.project_id, candidate.candidate_id, project.revision);
      setProject(value);
      toast.success("2K 全屋概念方案已锁定");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function createBundle() {
    if (!project) return;
    setBusy("bundle");
    try {
      const value = await api.createWholeHomeDesignBundle(project.project_id, project.revision);
      setProject(value);
      toast.success("Blender Agent 建模任务包已生成");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function prepareStructureReview() {
    if (!project) return;
    setBusy("structure-prepare");
    try {
      const value = await api.prepareWholeHomeDesignStructure(project.project_id, project.revision);
      setProject(value);
      toast.success(value.structure_review.status === "needs_answers" ? "结构候选已生成，请回答九个问题" : "九问已准备；外部结构线路暂不可用");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function saveStructureGuidance(answers: Record<string, string>) {
    if (!project) return;
    setBusy("structure-save");
    try {
      const value = await api.saveWholeHomeDesignStructureGuidance(project.project_id, { base_revision: project.revision, answers });
      setProject(value);
      if (value.structure_review.status === "verified") toast.success("九问和结构图已通过研究建模门");
      else toast.warning("答案已保存；仍需 Gemini 或专业结构校正");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function startModelRun() {
    if (!project?.structure_review?.structure_hash) return;
    setBusy("model-run");
    try {
      const value = await api.createWholeHomeDesignModelRun(project.project_id, {
        base_revision: project.revision,
        structure_hash: project.structure_review.structure_hash,
        idempotency_key: `${project.project_id}-${project.structure_review.structure_hash.slice(0, 20)}`,
      });
      setProject(value);
      toast.success("本地 Blender 研究建模已启动，不会重复调用 2K 图像模型");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  async function retryModelReview(run: DesignModelRun) {
    if (!project) return;
    setBusy("model-review");
    try {
      const value = await api.retryWholeHomeDesignModelReview(project.project_id, run.run_id, project.revision);
      setProject(value);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setBusy("");
    }
  }

  const draftCandidates = useMemo(
    () => (project?.candidates || []).filter((candidate) => candidate.phase === "draft" && !candidate.stale),
    [project?.candidates],
  );
  const lockedCandidate = project?.candidates.find((candidate) => candidate.candidate_id === project.locked_candidate_id);
  const roomIds = new Set(roomsState.map((room) => room.id));
  const duplicateRoomIds = roomIds.size !== roomsState.length;
  const incompleteRooms = roomsState.some((room) => !room.id.trim() || !room.label.trim() || !room.room_type.trim());
  const unknownAdjacency = roomsState.some((room) => room.adjacent_room_ids.some((id) => !roomIds.has(id)));
  const summaryValid = roomsState.length > 0 && !duplicateRoomIds && !incompleteRooms && !unknownAdjacency;
  const canStartDraftBatch = Boolean(
    project?.plan_summary_confirmed && project?.brief?.requirements_text
    && project?.anchor_set?.confirmed_complete && project?.anchor_verification?.status === "verified"
    && !ACTIVE.has(project.status) && project.status !== "locked" && project.status !== "cancelled");

  return (
    <div
      data-testid="whole-home-design-scroll"
      className="grid h-full min-h-0 grid-cols-[260px_minmax(0,1fr)] items-start gap-4 overflow-y-auto p-4 max-[980px]:grid-cols-1"
    >
      <aside className="rounded-2xl border border-border bg-card p-3 shadow-sm">
        <div className="flex items-center justify-between gap-2">
          <div><div className="text-xs font-bold text-primary">WHOLE HOME DESIGN</div><h2 className="mt-1 font-extrabold">全屋设计项目</h2></div>
          <Button size="sm" variant="outline" onClick={() => { setProject(null); setUploadResult(null); }}><Plus />新建</Button>
        </div>
        <div className="mt-3 space-y-2">
          {projects.map((item) => (
            <button key={item.project_id} type="button" onClick={() => refreshProject(item.project_id)}
              className={`w-full rounded-xl border p-3 text-left transition ${project?.project_id === item.project_id ? "border-primary bg-primary/5" : "border-border hover:border-primary/40"}`}>
              <div className="truncate text-sm font-bold">{item.source_name || item.project_id}</div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground"><span>{statusLabel(item)}</span><span>{new Date(item.updated_at * 1000).toLocaleDateString()}</span></div>
            </button>
          ))}
          {!projects.length && <div className="rounded-xl border border-dashed border-border p-5 text-center text-xs text-muted-foreground">尚无全屋设计项目</div>}
        </div>
      </aside>

      <main className="min-w-0 space-y-4">
        <section className="rounded-2xl border border-border bg-gradient-to-br from-card to-primary/5 p-5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><div className="mb-2 inline-flex rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-800">EXPERIMENTAL · 必须通过结构门禁</div><h1 className="text-2xl font-black">户型图 → 全屋装修概念设计</h1><p className="mt-2 max-w-4xl text-sm leading-relaxed text-muted-foreground">输出垂直正俯视、无屋顶、无文字标注的 2.5D 鸟瞰概念图。原户型始终是结构权威；AI 图只负责家具、材料、色彩和氛围，不是 CAD、BIM 或施工图。当前真实户型仍可能因房间语义或镜头偏差被门禁阻断。</p></div>
            {project && <span className="rounded-full border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs font-bold text-primary">{statusLabel(project)}</span>}
          </div>
        </section>

        {!project ? (
          <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
            <div className="mb-4 flex items-center gap-2"><UploadCloud className="text-primary" /><h2 className="font-extrabold">1. 上传户型图</h2></div>
            <label className="mb-3 block text-xs font-bold">图片方向
              <select aria-label="户型图方向策略" value={orientationPolicy} onChange={(event) => setOrientationPolicy(event.target.value as typeof orientationPolicy)} className="mt-1 h-9 w-full rounded-lg border border-border bg-background px-2 text-sm font-normal">
                <option value="exif_transpose-v1">按照片方向（默认，读取 EXIF）</option>
                <option value="ignore_invalid_exif_user_confirmed_raw">保持原始像素（确认 EXIF 方向错误时）</option>
              </select>
              <span className="mt-1 block font-normal text-muted-foreground">若预览被横置或文字侧倒，重新新建并选择“保持原始像素”。方向策略会写入结构来源合同。</span>
            </label>
            <button type="button" onClick={() => fileInput.current?.click()} className="flex min-h-52 w-full flex-col items-center justify-center rounded-2xl border border-dashed border-primary/40 bg-primary/5 p-6 text-center hover:border-primary">
              <input ref={fileInput} hidden type="file" accept=".png,.jpg,.jpeg,.webp,.pdf" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadPlan(file); event.currentTarget.value = ""; }} />
              {uploading ? <LoaderCircle className="animate-spin text-primary" /> : <FileImage className="text-primary" size={34} />}
              <b className="mt-3">JPG / PNG / WebP / PDF</b><span className="mt-1 text-xs text-muted-foreground">单文件不超过 50 MiB；PDF 上传后明确选择一页，不会盲选户型。</span>
            </button>
            {uploadResult?.kind === "pdf" && <div className="mt-4 grid grid-cols-3 gap-3 max-[760px]:grid-cols-2">{uploadResult.pages.map((page) => <button key={page.page} className="overflow-hidden rounded-xl border border-border bg-background p-2 hover:border-primary" onClick={() => startProject(page.path, `${uploadResult.name} · 第 ${page.page} 页`)}><img src={api.imgUrl(page.thumb || page.url)} alt={`第 ${page.page} 页`} className="aspect-[4/3] w-full object-contain" /><div className="mt-2 text-xs font-bold">第 {page.page} 页 · {page.width}×{page.height}</div></button>)}</div>}
          </section>
        ) : (
          <>
            <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <FileImage className="text-primary" /><h2 className="font-extrabold">1. 人工锚点与 Gemini 双重摘要</h2>
                <span className="text-xs text-muted-foreground">revision {project.revision}</span>
                <Button className="ml-auto" size="sm" variant="outline" disabled={!hasGemini || !project.anchor_set?.confirmed_complete || ACTIVE.has(project.status) || busy === "analyze-plan"} onClick={analyzePlanAgain}>
                  {project.status === "analyzing_plan" || project.status === "verifying_plan" || busy === "analyze-plan" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}重新执行 Gemini 双重识别
                </Button>
              </div>
              {hasGemini === false && <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"><b>当前未配置 Gemini Key，自动摘要不可用。</b><div className="mt-1 text-xs">可以先人工补录，但不能确认空摘要。推荐先到 <a className="font-bold underline" href="/settings/">设置</a> 配置 Gemini，然后回来点击“自动识别”。</div></div>}
              {project.error && <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">自动识别提示：{project.error}</div>}
              <FloorplanAnchorEditor key={`${project.project_id}-${project.anchor_set?.updated_at || 0}`} imageUrl={api.imgUrl(project.normalized_url)} initial={project.anchor_set?.anchors || []} busy={busy === "anchors"} onSave={saveAnchorsAndAnalyze} />
              {project.anchor_verification?.status !== "not_run" && <div className={`mt-4 rounded-xl border p-3 text-xs ${project.anchor_verification.status === "verified" ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}><b>Gemini 复核：{project.anchor_verification.status}</b>{project.anchor_verification.changes?.map((value) => <div key={value}>• 修正：{value}</div>)}{project.anchor_verification.conflicts?.map((value) => <div key={value}>• 冲突：{value}</div>)}{project.anchor_verification.inferred_anchor_gaps?.map((value) => <div key={value}>• 自动补充提示：{value}</div>)}</div>}
              <div className="my-4 border-t border-border" />
              <div className="grid grid-cols-[minmax(260px,0.8fr)_minmax(0,1.2fr)] gap-4 max-[900px]:grid-cols-1">
                <div className="space-y-3">
                  <div className="rounded-xl border border-border bg-[#f8f8f6] p-3"><div className="mb-2 text-xs font-bold">证据原图 · 用于标题/尺寸/摘要</div><img src={api.imgUrl(project.normalized_url)} alt="规范化户型证据图" className="max-h-[390px] w-full object-contain" /><div className="mt-2 text-[11px] text-muted-foreground">只裁近白页边并补中性留白 · {project.normalization.aspect_ratio} · 保留尺寸和图纸说明</div></div>
                  {project.generation_url && <div className="rounded-xl border border-emerald-300 bg-emerald-50/40 p-3"><div className="mb-2 text-xs font-bold text-emerald-900">生成结构图 · 只把这一块交给生图模型</div><img src={api.imgUrl(project.generation_url)} alt="自动提取并清理标注的主户型结构图" className="max-h-[390px] w-full object-contain" /><div className="mt-2 text-[11px] text-emerald-800">已排除独立节点详图、标题和外围尺寸，并清理 {project.generation_cleanup?.applied_count || 0} 处安全文字框 · {project.generation_crop?.aspect_ratio || project.normalization.aspect_ratio}{project.generation_crop?.fallback_reason ? ` · 自动裁图回退：${project.generation_crop.fallback_reason}` : ""}</div></div>}
                </div>
                <div className="space-y-3">
                  <div className="grid grid-cols-6 gap-2 rounded-xl border border-border bg-muted/20 p-3 max-[760px]:grid-cols-2">
                    <label className="text-[11px] font-bold">卧室<Input className="mt-1" type="number" min={0} max={20} value={declaredLayout.bedrooms} onChange={(event) => setDeclaredLayout((value) => ({ ...value, bedrooms: Number(event.target.value) }))} /></label>
                    <label className="text-[11px] font-bold">厅<Input className="mt-1" type="number" min={0} max={20} value={declaredLayout.halls} onChange={(event) => setDeclaredLayout((value) => ({ ...value, halls: Number(event.target.value) }))} /></label>
                    <label className="text-[11px] font-bold">卫生间<Input className="mt-1" type="number" min={0} max={20} value={declaredLayout.bathrooms} onChange={(event) => setDeclaredLayout((value) => ({ ...value, bathrooms: Number(event.target.value) }))} /></label>
                    <label className="text-[11px] font-bold">建筑面积㎡<Input className="mt-1" type="number" min={0} value={declaredArea} onChange={(event) => setDeclaredArea(Number(event.target.value))} /></label>
                    <label className="text-[11px] font-bold">总宽 mm<Input className="mt-1" type="number" min={0} value={overallDimensions.width} onChange={(event) => setOverallDimensions((value) => ({ ...value, width: Number(event.target.value) }))} /></label>
                    <label className="text-[11px] font-bold">总深 mm<Input className="mt-1" type="number" min={0} value={overallDimensions.depth} onChange={(event) => setOverallDimensions((value) => ({ ...value, depth: Number(event.target.value) }))} /></label>
                    <div className="col-span-6 flex flex-wrap gap-2 text-[11px] text-muted-foreground max-[760px]:col-span-2"><span>空间条目：{roomsState.length}</span><span>来源：{project.plan_summary?.source || "摘要尚未加载"}</span><span>AI 总置信度：{Math.round((project.plan_summary?.summary_confidence || 0) * 100)}%</span>{declaredLayout.source_text && <span>标题证据：{declaredLayout.source_text}</span>}</div>
                  </div>
                  <div className="space-y-2">{roomsState.map((room, index) => <div key={`${room.id}-${index}`} className={`rounded-xl border p-2 ${room.needs_confirmation ? "border-amber-300 bg-amber-50/50" : "border-border"}`}>
                    <div className="grid grid-cols-[100px_0.8fr_1fr_1fr_34px] gap-2 max-[840px]:grid-cols-1">
                      <Input value={room.id} placeholder="room_id" onChange={(event) => setRoomsState((items) => items.map((item, i) => i === index ? { ...item, id: event.target.value } : item))} />
                      <Input value={room.label} placeholder="房间名称" onChange={(event) => setRoomsState((items) => items.map((item, i) => i === index ? { ...item, label: event.target.value, needs_confirmation: false } : item))} />
                      <Input value={`${room.room_type}|${room.coarse_location}`} placeholder="类型|大致位置" onChange={(event) => { const [room_type, coarse_location = ""] = event.target.value.split("|"); setRoomsState((items) => items.map((item, i) => i === index ? { ...item, room_type, coarse_location, needs_confirmation: false } : item)); }} />
                      <Input value={room.adjacent_room_ids.join(",")} placeholder="相邻 room_id，逗号分隔" onChange={(event) => setRoomsState((items) => items.map((item, i) => i === index ? { ...item, adjacent_room_ids: event.target.value.split(/[,，]/).map((value) => value.trim()).filter(Boolean) } : item))} />
                      <button className="text-muted-foreground hover:text-red-600" onClick={() => setRoomsState((items) => items.filter((_, i) => i !== index))}><X size={16} /></button>
                    </div>
                    {(room.evidence || room.confidence != null) && <div className="mt-2 flex flex-wrap items-start gap-2 text-[11px] text-muted-foreground"><span className={`rounded-full px-2 py-0.5 font-bold ${room.source === "human_anchor" ? "bg-emerald-100 text-emerald-800" : room.source === "gemini_inferred" ? "bg-blue-100 text-blue-800" : "bg-stone-100 text-stone-700"}`}>{room.source === "human_anchor" ? `人工锚点 ${room.anchor_ids?.join(",") || ""}` : room.source === "gemini_inferred" ? "Gemini 自动补充" : "历史摘要"}</span><span className={`rounded-full px-2 py-0.5 font-bold ${(room.confidence || 0) >= 0.8 ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>置信度 {Math.round((room.confidence || 0) * 100)}%</span>{room.needs_confirmation && <span className="rounded-full bg-amber-100 px-2 py-0.5 font-bold text-amber-800">待确认</span>}<span>{room.evidence}</span></div>}
                  </div>)}</div>
                  <Button size="sm" variant="outline" onClick={() => setRoomsState((items) => [...items, { id: `room_${items.length + 1}`, label: "", room_type: "", coarse_location: "", adjacent_room_ids: [], confidence: 1, evidence: "人工添加", needs_confirmation: false }])}><Plus />添加空间</Button>
                  <div className="grid grid-cols-2 gap-2 max-[720px]:grid-cols-1">{Object.entries({ entrances: "入口", openings_summary: "主要门窗/开口", wet_zones: "厨房/卫生间湿区", balconies: "阳台", dimension_evidence: "尺寸证据", must_preserve: "必须保留", uncertainties: "不确定项" }).map(([key, label]) => <label key={key} className="text-xs font-bold">{label}<Textarea className="mt-1 min-h-20 font-normal" value={listFields[key as keyof typeof listFields]} onChange={(event) => setListFields((fields) => ({ ...fields, [key]: event.target.value }))} placeholder="每行一项" /></label>)}</div>
                  {(project.plan_summary?.review_items?.length || 0) > 0 && <div className="rounded-xl border border-amber-300 bg-amber-50 p-3"><b className="text-xs text-amber-900">AI 标出的待确认项</b><div className="mt-2 space-y-1">{project.plan_summary?.review_items?.map((item) => <div key={item.id} className="text-[11px] text-amber-900">• {item.label}（{Math.round(item.confidence * 100)}%）：{item.evidence}</div>)}</div></div>}
                  {unknownAdjacency && <div className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800">存在相邻关系引用了未知 room_id，请修正后确认。</div>}
                  {duplicateRoomIds && <div className="rounded-lg bg-red-50 p-2 text-xs text-red-800">room_id 必须唯一。</div>}
                  {incompleteRooms && <div className="rounded-lg bg-red-50 p-2 text-xs text-red-800">每个空间必须有 room_id、名称和类型。</div>}
                  {!roomsState.length && <div className="rounded-lg bg-red-50 p-2 text-xs text-red-800">不能确认空摘要。请配置 Gemini 自动识别，或人工添加空间。</div>}
                  <Button disabled={busy === "summary" || !summaryValid} onClick={savePlanSummary}>{busy === "summary" ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}确认户型摘要</Button>
                </div>
              </div>
            </section>

            <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
              <div className="mb-4 flex items-center gap-2"><Sparkles className="text-primary" /><h2 className="font-extrabold">2. 自由设计需求与参考图</h2></div>
              <Textarea value={requirements} onChange={(event) => setRequirements(event.target.value)} className="min-h-32" placeholder="例如：全屋现代暖木自然风；客厅重视社媒画面中的地板展示；保留厨房和卫浴位置；家具克制、真实、适合年轻家庭；自然日光，不要奢华酒店风……" />
              <div className="mt-3 grid grid-cols-4 gap-3 max-[820px]:grid-cols-2">
                {references.map((reference) => <div key={reference.sha256} className="relative overflow-hidden rounded-xl border border-border"><img src={api.imgUrl(reference.thumb || reference.url)} alt={reference.name} className="aspect-square w-full object-cover" /><button className="absolute right-2 top-2 rounded-full bg-black/60 p-1 text-white" onClick={() => setReferences((items) => items.filter((item) => item.sha256 !== reference.sha256))}><X size={14} /></button><div className="truncate p-2 text-[11px]">{reference.name}</div></div>)}
                {references.length < 4 && <button className="flex aspect-square flex-col items-center justify-center rounded-xl border border-dashed border-border text-xs text-muted-foreground hover:border-primary hover:text-primary" onClick={() => referenceInput.current?.click()}><input ref={referenceInput} hidden type="file" accept=".png,.jpg,.jpeg,.webp" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadReference(file); event.currentTarget.value = ""; }} /><ImagePlus /><span className="mt-2">可选风格/地板/墙板图</span></button>}
              </div>
              <div className="mt-4 flex justify-end"><Button disabled={!project.plan_summary_confirmed || !requirements.trim() || busy === "brief"} onClick={saveBrief}>{busy === "brief" ? <LoaderCircle className="animate-spin" /> : <Check />}保存设计要求</Button></div>
            </section>

            <StructureResearchPanel key={`${project.project_id}-${project.structure_review?.updated_at || 0}`} project={project} busy={busy} onPrepare={prepareStructureReview} onSubmit={saveStructureGuidance} onStartModel={startModelRun} onRetryReview={retryModelReview} />

            <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3"><div><div className="flex items-center gap-2"><Sparkles className="text-primary" /><h2 className="font-extrabold">4. 两张 2K 设计草稿</h2></div><p className="mt-1 text-xs text-muted-foreground">与 Blender 结构灰模并行；默认 Google Gemini，可手动切 Fal，不会自动切线或重复付费。</p></div><div className="flex items-center gap-2"><select aria-label="全屋设计生图线路" value={designProvider} onChange={(event) => setDesignProvider(event.target.value as "google" | "fal")} className="h-9 rounded-lg border border-border bg-background px-2 text-sm"><option value="google">Google Gemini（推荐）</option><option value="fal">Fal（手动切换）</option></select><Button disabled={!canStartDraftBatch || busy === "preview"} onClick={openDraftPreview}>{busy === "preview" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}{draftCandidates.length ? "重新预览并生成新批次" : "预览并确认 2 次调用"}</Button></div></div>
              <div className="mt-4 grid grid-cols-2 gap-4 max-[780px]:grid-cols-1">{draftCandidates.map((candidate) => <CandidateCard key={candidate.candidate_id} candidate={candidate} busy={busy} onReview={() => openReview(candidate)} onZoom={() => candidate.url && setZoomUrl(api.imgUrl(candidate.url))} onLock={() => lockCandidate(candidate)} />)}{!draftCandidates.length && <div className="col-span-2 rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">保存户型摘要和设计要求后，先生成两张不同方向的 2K 草稿。</div>}</div>
            </section>

            <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
              <div className="flex items-center gap-2"><LockKeyhole className="text-primary" /><h2 className="font-extrabold">5. 锁定 2K 方案与高级 Agent 任务包</h2></div>
              {!lockedCandidate && <div className="mt-4 rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">在上方放大检查 2K 方案；通过全部结构核对后，直接锁定其中一张作为建模外观参考。</div>}
              {lockedCandidate && <div className="mt-4 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-emerald-950"><div className="flex flex-wrap items-center gap-3"><CheckCircle2 /><div><b>2K 方案已锁定</b><div className="text-xs">锁定图只作为家具、材料和氛围参考；Blender Agent 必须以原户型为几何权威。</div></div><Button className="ml-auto" disabled={busy === "bundle"} onClick={createBundle}>{busy === "bundle" ? <LoaderCircle className="animate-spin" /> : <Archive />}生成 Blender 任务包</Button></div>{project.bundles.filter((bundle) => !bundle.stale).map((bundle) => <a key={bundle.bundle_id} href={api.imgUrl(bundle.download_url)} className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-300 bg-white px-3 py-2 text-sm font-bold hover:border-emerald-600"><Download size={16} />下载 {bundle.bundle_id}<span className="ml-auto font-mono text-[10px] text-muted-foreground">SHA256 {bundle.sha256.slice(0, 16)}…</span></a>)}</div>}
            </section>
          </>
        )}
      </main>

      <Dialog open={!!preview} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent className="max-w-xl">
          {preview && <div><h2 className="text-lg font-extrabold">付费调用确认</h2><div className="mt-4 grid grid-cols-2 gap-2 text-sm">{[["线路", preview.provider], ["模型", preview.model_label], ["调用数", `${preview.call_count} 次`], ["输出", `${preview.resolution} · ${preview.aspect_ratio}`], ["预计费用", preview.estimated_cost == null ? "未配置单价" : `¥${preview.estimated_cost}`], ["过期时间", new Date(preview.expires_at * 1000).toLocaleTimeString()]].map(([label, value]) => <div key={label} className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">{label}</div><b>{value}</b></div>)}</div><div className="mt-4 rounded-lg bg-amber-50 p-3 text-xs text-amber-900"><AlertTriangle className="mr-2 inline" size={15} />失败或结构不通过不会自动重新付费，也不会自动切换 Google/Fal。</div><label className="mt-4 block text-sm font-bold">输入确认短语：<code>{preview.confirmation_phrase}</code><Input className="mt-2" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label><div className="mt-4 flex justify-end gap-2"><Button variant="outline" onClick={() => setPreview(null)}>取消</Button><Button disabled={confirmation !== preview.confirmation_phrase || busy === "commit"} onClick={commitPreview}>{busy === "commit" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}确认并提交</Button></div></div>}
        </DialogContent>
      </Dialog>

      <Dialog open={!!reviewCandidate} onOpenChange={(open) => !open && setReviewCandidate(null)}>
        <DialogContent className="max-w-2xl">
          {reviewCandidate && <div><h2 className="text-lg font-extrabold">严格结构核对 · 2K 方案</h2>{reviewCandidate.structure_qa?.hard_fail ? <div className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800"><b>自动/人工 QA 已发现结构硬错误，不能覆写。</b><div className="mt-1">{reviewCandidate.structure_qa.summary}</div></div> : <><p className="mt-2 text-sm text-muted-foreground">逐项对照原户型与候选图。任何不确定都不要勾选；发现结构错误时写明问题并标记失败。</p><div className="mt-4 space-y-2">{REVIEW_ITEMS.map(([key, label]) => <label key={key} className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${reviewChecks[key] ? "border-emerald-300 bg-emerald-50" : "border-border"}`}><input type="checkbox" className="mt-0.5 size-4" checked={reviewChecks[key] || false} onChange={(event) => setReviewChecks((checks) => ({ ...checks, [key]: event.target.checked }))} /><span className="text-sm">{label}</span></label>)}</div><Textarea className="mt-3" value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} placeholder="通过时可选；标记失败时必须写清新增/删除/移动了什么" /><div className="mt-4 flex flex-wrap justify-end gap-2"><Button variant="outline" className="border-red-300 text-red-700 hover:bg-red-50" disabled={busy === "review" || !reviewNote.trim()} onClick={rejectReview}>标记结构失败</Button><Button disabled={busy === "review" || REVIEW_ITEMS.some(([key]) => !reviewChecks[key])} onClick={submitReview}>{busy === "review" ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}保存全部通过</Button></div></>}</div>}
        </DialogContent>
      </Dialog>
      <ImageZoom url={zoomUrl} onClose={() => setZoomUrl(null)} />
    </div>
  );
}

function CandidateCard({ candidate, busy, onReview, onZoom, onLock }: {
  candidate: WholeHomeDesignCandidate;
  busy: string;
  onReview: () => void;
  onZoom: () => void;
  onLock?: () => void;
}) {
  const waiting = ["queued", "running", "qa_running", "interrupted"].includes(candidate.status);
  const eligible = candidate.status === "done" && !candidate.stale && !candidate.structure_qa?.hard_fail
    && (candidate.structure_qa?.status === "passed" || candidate.human_review?.status === "passed");
  return <article className="overflow-hidden rounded-2xl border border-border bg-background">
    <div className="relative aspect-[4/3] bg-muted/40">{candidate.url ? <button type="button" aria-label={`放大预览 2K 方案方向 ${candidate.direction_index}`} onClick={onZoom} className="group size-full cursor-zoom-in"><img src={api.imgUrl(candidate.url)} alt={candidate.candidate_id} className="size-full object-contain" /><span className="absolute right-3 top-3 flex items-center gap-1 rounded-full bg-black/65 px-2 py-1 text-[11px] font-bold text-white opacity-90 transition group-hover:bg-black/80"><ZoomIn size={13} />点击放大</span></button> : <div className="flex size-full flex-col items-center justify-center text-sm text-muted-foreground">{waiting ? <LoaderCircle className="animate-spin text-primary" /> : <AlertTriangle />}<span className="mt-2">{candidate.error || candidate.stage}</span></div>}<span className={`pointer-events-none absolute left-3 top-3 rounded-full border px-2 py-1 text-[11px] font-bold ${qaTone(candidate)}`}>{qaLabel(candidate)}</span></div>
    <div className="p-3"><div className="flex items-center gap-2 text-xs"><b>2K 方案方向 {candidate.direction_index}</b><span className="ml-auto text-muted-foreground">{candidate.provider} · {candidate.model_label} · {candidate.resolution}</span></div>{candidate.structure_qa?.summary && <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{candidate.structure_qa.summary}</p>}{candidate.structure_qa?.hard_fail && <div className="mt-2 space-y-1 text-[11px] text-red-700">{candidate.structure_qa.checks.filter((check) => check.status !== "pass").map((check) => <div key={check.check_id}>• {check.check_id}: {check.evidence}</div>)}</div>}
      {candidate.status === "done" && !candidate.structure_qa?.hard_fail && candidate.human_review?.status !== "passed" && <Button className="mt-3" size="sm" variant="outline" onClick={onReview}>人工核对结构</Button>}
      {onLock && <Button className="mt-3 w-full" disabled={!eligible || candidate.human_review?.status !== "passed" || busy === "lock"} onClick={onLock}>{busy === "lock" ? <LoaderCircle className="animate-spin" /> : <LockKeyhole />}锁定此 2K 方案</Button>}
    </div>
  </article>;
}
