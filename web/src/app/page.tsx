"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { requestNotifyPermission } from "@/lib/notify";
import type {
  CustomRecipe,
  GenParams,
  JobView,
  ModelFilter,
  OptionsView,
  PreviewView,
  ResolvedRecipe,
  Swatch,
} from "@/lib/types";
import { toast } from "sonner";
import { FloorUploader } from "@/components/FloorUploader";
import { ParamsForm } from "@/components/ParamsForm";
import { JobCard } from "@/components/JobCard";
import { ImageZoom } from "@/components/ImageZoom";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { SectionHeader, Segmented, Pill } from "@/components/dc-ui";
import { loadDraft, saveDraft, takeReuseRequest } from "@/lib/draft";

// 由后端文件路径重建 Swatch（复用参数回填：记录里只有 path，url/缩略图按上传目录约定拼出）
function swatchFromPath(p?: string): Swatch | null {
  if (!p) return null;
  const name = p.split(/[\\/]/).pop() || p;
  return {
    path: p,
    name,
    url: `/uploads/${name}`,
    thumb: `/thumb/uploads/${name}?s=320`,
  };
}

// 依据后端 options 组装一份「全字段默认值」的生成参数。
// 原先内联在初始化 useEffect 里，抽出以便首次加载与草稿回落两条路径复用同一份默认。
function buildDefaultParams(o: OptionsView, prevWorkflow?: string): GenParams {
  const cont = o.continents[0];
  const country = Object.keys(o.location_map[cont] || {})[0] || "";
  const city = (o.location_map[cont]?.[country] || [])[0] || "";
  return {
    workflow_mode: prevWorkflow || o.workflow_modes[0],
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
    panel_submode: "再设计",
    panel_size: o.panel_sizes[0],
  };
}

export default function GeneratePage() {
  const [options, setOptions] = useState<OptionsView | null>(null);
  const [floor, setFloor] = useState<Swatch | null>(null);
  const [refImg, setRefImg] = useState<Swatch | null>(null);
  const [roomImg, setRoomImg] = useState<Swatch | null>(null);
  const [modelFilter, setModelFilter] = useState<ModelFilter>("both");
  const [params, setParams] = useState<GenParams>({
    workflow_mode: "纯效果图 (生成全新空间)",
  });
  const [recipes, setRecipes] = useState<ResolvedRecipe[]>([]);
  // 我的配方：用户保存的参数组合快照；弹窗 id 为空=新建（存当前参数），否则=改名
  const [myRecipes, setMyRecipes] = useState<CustomRecipe[]>([]);
  const [recipeDlg, setRecipeDlg] = useState<{ open: boolean; id: string; name: string }>({
    open: false,
    id: "",
    name: "",
  });
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchTab, setBatchTab] = useState<"rooms" | "floors">("rooms");
  const [batchRooms, setBatchRooms] = useState<string[]>([]);
  // 多地板批量：候选=最近小样，选中=本次要批量的地板
  const [recentFloors, setRecentFloors] = useState<Swatch[]>([]);
  const [batchFloors, setBatchFloors] = useState<Swatch[]>([]);
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const submitLock = useRef(false);
  const batchLock = useRef(false);
  // 快速预览（NB2 Lite · 1K，与 4K 队列解耦，短轮询）
  const [preview, setPreview] = useState<PreviewView | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);
  const previewTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // 预览竞态防护：run=序号（关弹窗/新预览即 ++，令在途 createPreview 与旧轮询的响应全部过期）；
  // wanted=弹窗是否还想要本次预览（await 期间关弹窗时 state 是 stale closure，必须走 ref 判断）
  const previewRun = useRef(0);
  const previewWanted = useRef(false);
  // 识色响应乱序防护：连续快换地板时丢弃过期响应
  const floorSeq = useRef(0);

  useEffect(() => {
    api
      .getOptions()
      .then((o) => {
        setOptions(o);
        // 「复用参数」优先于普通草稿：记录页发起的一次性回填请求（读后即删），
        // 用历史快照整表覆盖；floor_tone 沿用快照值，不触发重新识色。
        const reuse = takeReuseRequest();
        if (reuse?.params) {
          setParams(() => ({ ...buildDefaultParams(o), ...reuse.params }));
          if (reuse.modelFilter) setModelFilter(reuse.modelFilter);
          setFloor(swatchFromPath(reuse.floorPath));
          setRefImg(swatchFromPath(reuse.refPath));
          setRoomImg(swatchFromPath(reuse.roomPath));
          setRecipes([]);
          toast.success("已回填历史参数，可直接生成或微调");
          return;
        }
        const draft = loadDraft();
        setParams((p) => {
          const defaults = buildDefaultParams(o, p.workflow_mode);
          // 有草稿：默认值打底、用户已存值覆盖（新增字段仍有默认，抗缺失/过期）；无草稿：纯默认。
          return draft?.params ? { ...defaults, ...draft.params } : defaults;
        });
        if (draft) {
          // 一并回填「作业中」的图片/配方/模型选择，切到记录页再回来、刷新、重开浏览器都不丢。
          if (draft.floor) setFloor(draft.floor);
          if (draft.refImg) setRefImg(draft.refImg);
          if (draft.roomImg) setRoomImg(draft.roomImg);
          if (draft.recipes) setRecipes(draft.recipes);
          if (draft.modelFilter) setModelFilter(draft.modelFilter);
        }
      })
      .catch((e) => toast.error("加载选项失败：" + (e as Error).message));
    api.listJobs(50).then(setJobs).catch(() => {});
    api.listCustomRecipes().then(setMyRecipes).catch(() => {});
  }, []);

  // 任一配置变化即写回草稿；options 未就绪（尚未从后端拿到默认、恢复未完成）时跳过，
  // 避免用「仅含 workflow_mode 的空 params」把已存的好草稿覆盖掉（clobber 防护）。
  useEffect(() => {
    if (!options) return;
    saveDraft({ params, modelFilter, floor, refImg, roomImg, recipes });
  }, [options, params, modelFilter, floor, refImg, roomImg, recipes]);

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
    const seq = ++floorSeq.current;
    setFloor(s);
    try {
      const a = await api.floorAnalyze(s.path);
      if (seq !== floorSeq.current) return; // 用户已换下一块地板，丢弃过期识色结果
      updateParams({ floor_tone: a.tone });
      setRecipes(a.recipes || []);
      toast.success("已识别色调，并给出智能配方");
    } catch {
      /* 识色失败不阻断，用户仍可手选 */
    }
  }

  // Omakase：被隐藏的「高级」字段按表单默认值发送——避免项=默认全选（保住过曝/反光等地板保护词），
  // 自定义指令=空。防止其他工作流残留的旧值在精简表单下看不见地进提示词。
  const omakaseSafeParams = (p: GenParams): GenParams =>
    p.workflow_mode?.includes("Omakase")
      ? { ...p, avoid_items: options?.avoid_items ?? p.avoid_items, custom_addition: "" }
      : p;

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

  // ── 我的配方：整表快照回填（区别于智能配方的 5 字段）。
  // 存入时剔除易变字段：last_image_path（跟图走）、floor_tone（跟当前地板识色走，配方是场景模板）。
  function recipeSnapshot(p: GenParams): GenParams {
    const snap = { ...p };
    delete snap.last_image_path;
    delete snap.floor_tone;
    return snap;
  }

  function applyCustomRecipe(r: CustomRecipe) {
    setParams((p) => ({ ...p, ...recipeSnapshot(r.params) }));
    toast.success(`已套用配方：${r.name}`);
  }

  async function submitRecipeDlg() {
    const name = recipeDlg.name.trim();
    if (!name) {
      toast.warning("请输入配方名");
      return;
    }
    try {
      if (recipeDlg.id) {
        const r = await api.updateCustomRecipe(recipeDlg.id, { name });
        setMyRecipes((s) => s.map((x) => (x.id === r.id ? r : x)));
        toast.success("已改名");
      } else {
        const r = await api.addCustomRecipe(name, recipeSnapshot(params));
        setMyRecipes((s) => [r, ...s]);
        toast.success(`已保存配方：${name}`);
      }
      setRecipeDlg({ open: false, id: "", name: "" });
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function removeCustomRecipe(r: CustomRecipe) {
    if (!window.confirm(`删除配方「${r.name}」？`)) return;
    try {
      await api.deleteCustomRecipe(r.id);
      setMyRecipes((s) => s.filter((x) => x.id !== r.id));
      toast.success("已删除");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function generate() {
    if (submitLock.current) return;
    if (!floor) {
      toast.warning("请先上传地板图");
      return;
    }
    if (params.workflow_mode.includes("参照模式") && !refImg) {
      toast.warning("参照模式需上传参照图");
      return;
    }
    if (params.workflow_mode.includes("地板替换") && !roomImg) {
      toast.warning("地板替换需上传房间原图");
      return;
    }
    if (params.workflow_mode.includes("Omakase") && !(params.scene_override || "").trim()) {
      toast.warning("Omakase 模式：请先生成或填写场景定稿");
      return;
    }
    if (params.workflow_mode.includes("墙板")) {
      const sub = params.panel_submode || "再设计";
      if (sub.includes("替换") && !roomImg) { toast.warning("墙板替换需上传原墙板场景图"); return; }
      if (sub.includes("再设计") && !refImg) { toast.warning("墙板再设计需上传场景参照图"); return; }
    }
    submitLock.current = true;
    requestNotifyPermission();
    setSubmitting(true);
    try {
      const job = await api.createJob({
        image_path: floor.path,
        model_filter: modelFilter,
        room_path: roomImg?.path ?? null,
        ref_path: refImg?.path ?? null,
        params: omakaseSafeParams(params),
      });
      setJobs((j) => [job, ...j]);
      toast.success("任务已提交");
    } catch (e) {
      toast.error("提交失败：" + (e as Error).message);
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  // 打开批量弹窗：拉最近小样做多地板候选；Omakase 只支持多地板（按房间批量=同图重复计费）
  function openBatch() {
    setBatchTab(params.workflow_mode.includes("Omakase") ? "floors" : "rooms");
    setBatchFloors(floor ? [floor] : []);
    api.recentSwatches(24).then(setRecentFloors).catch(() => {});
    setBatchOpen(true);
  }

  async function runBatch() {
    if (batchLock.current) return;
    if (!floor) {
      toast.warning("请先上传地板图");
      return;
    }
    if (params.workflow_mode.includes("Omakase")) {
      // Omakase 提示词不含房间类型（场景全由散文接管），按房间批量只会 N 倍计费出 N 张同图
      toast.warning("Omakase 工作流不支持批量：提示词不含房间类型，多张只是同图重复计费");
      return;
    }
    if (params.workflow_mode.includes("墙板")) {
      // 墙板模式按空间类型批量意义不大（替换/再设计由图片驱动），避免 N× 重复计费
      toast.warning("墙板模式不支持按房间类型批量");
      return;
    }
    if (batchRooms.length === 0) {
      toast.warning("请至少勾选一个房间类型");
      return;
    }
    if (params.workflow_mode.includes("参照模式") && !refImg) {
      toast.warning("参照模式需上传参照图");
      return;
    }
    if (params.workflow_mode.includes("地板替换") && !roomImg) {
      toast.warning("地板替换需上传房间原图");
      return;
    }
    const cnMode = !!params.cn_mode;
    batchLock.current = true;
    requestNotifyPermission();
    setBatchSubmitting(true);
    try {
      const settled = await Promise.allSettled(
        batchRooms.map((room) =>
          api.createJob({
            image_path: floor.path,
            model_filter: modelFilter,
            room_path: roomImg?.path ?? null,
            ref_path: refImg?.path ?? null,
            params: cnMode
              ? { ...params, cn_room_type: room }
              : { ...params, room_type: room },
          }),
        ),
      );
      const created = settled.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : [],
      );
      const failedRooms = settled.flatMap((result, index) =>
        result.status === "rejected" ? [batchRooms[index]] : [],
      );
      if (created.length) setJobs((j) => [...created.reverse(), ...j]);
      if (failedRooms.length) {
        toast.error(`已提交 ${created.length} 个，失败：${failedRooms.join("、")}`);
      } else {
        toast.success(`已批量提交 ${created.length} 个房间`);
        setBatchOpen(false);
      }
    } finally {
      batchLock.current = false;
      setBatchSubmitting(false);
    }
  }

  // 多地板 × 同场景：每块地板先本地识色（analyze_floor_tone 不走生图 API、免费），
  // 保证各自 floor_tone 正确，而不是沿用当前地板的识色结果。
  async function runBatchFloors() {
    if (batchLock.current) return;
    if (batchFloors.length === 0) {
      toast.warning("请至少选择一块地板");
      return;
    }
    if (params.workflow_mode.includes("参照模式") && !refImg) {
      toast.warning("参照模式需上传参照图");
      return;
    }
    if (params.workflow_mode.includes("地板替换") && !roomImg) {
      toast.warning("地板替换需上传房间原图");
      return;
    }
    if (params.workflow_mode.includes("Omakase") && !(params.scene_override || "").trim()) {
      toast.warning("Omakase 模式：请先生成或填写场景定稿");
      return;
    }
    batchLock.current = true;
    requestNotifyPermission();
    setBatchSubmitting(true);
    try {
      const settled = await Promise.allSettled(
        batchFloors.map(async (f) => {
          let tone = params.floor_tone;
          try {
            tone = (await api.floorAnalyze(f.path)).tone || tone;
          } catch {
            /* 识色失败回落当前色调，不阻断提交 */
          }
          return api.createJob({
            image_path: f.path,
            model_filter: modelFilter,
            room_path: roomImg?.path ?? null,
            ref_path: refImg?.path ?? null,
            params: omakaseSafeParams({ ...params, floor_tone: tone }),
          });
        }),
      );
      const created = settled.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : [],
      );
      const failedFloors = settled.flatMap((result, index) =>
        result.status === "rejected" ? [batchFloors[index].name] : [],
      );
      if (created.length) setJobs((j) => [...created.reverse(), ...j]);
      if (failedFloors.length) {
        toast.error(`已提交 ${created.length} 个，失败：${failedFloors.join("、")}`);
      } else {
        toast.success(`已批量提交 ${created.length} 块地板`);
        setBatchOpen(false);
      }
    } finally {
      batchLock.current = false;
      setBatchSubmitting(false);
    }
  }

  function stopPreviewPoll() {
    if (previewTimer.current) {
      clearInterval(previewTimer.current);
      previewTimer.current = null;
    }
  }
  // 组件卸载时停轮询，避免泄漏
  useEffect(() => stopPreviewPoll, []);

  function pollPreview(pid: string, run: number) {
    stopPreviewPoll();
    let fails = 0; // 连续失败计数：偶发一次 fetch 失败不放弃，连续 3 次才报错停止（不再静默转圈）
    previewTimer.current = setInterval(async () => {
      try {
        const p = await api.previewStatus(pid);
        if (run !== previewRun.current) return; // 过期轮询（弹窗已关/新预览已开），丢弃
        fails = 0;
        setPreview(p);
        if (p.status === "done" || p.status === "failed") stopPreviewPoll();
      } catch {
        if (run !== previewRun.current) return;
        if (++fails >= 3) {
          stopPreviewPoll();
          setPreview({
            preview_id: pid, status: "failed", stage: "", url: "", thumb: "",
            error: "预览状态查询连续失败（网络/后端不可达），已停止刷新",
          });
        }
      }
    }, 1000);
  }

  // 快速预览：用 NB2 Lite 出一张 1K 草图（几秒、便宜），满意再点「生成效果图」出 4K
  async function runPreview() {
    if (previewWanted.current) return; // 在途防护：上一次预览尚未落定，忽略连点（防孤儿计费任务）
    if (!floor) {
      toast.warning("请先上传地板图");
      return;
    }
    if (params.workflow_mode.includes("参照模式") && !refImg) {
      toast.warning("参照模式需上传参照图");
      return;
    }
    if (params.workflow_mode.includes("地板替换") && !roomImg) {
      toast.warning("地板替换需上传房间原图");
      return;
    }
    if (params.workflow_mode.includes("Omakase") && !(params.scene_override || "").trim()) {
      toast.warning("Omakase 模式：请先生成或填写场景定稿");
      return;
    }
    if (params.workflow_mode.includes("墙板")) {
      const sub = params.panel_submode || "再设计";
      if (sub.includes("替换") && !roomImg) { toast.warning("墙板替换需上传原墙板场景图"); return; }
      if (sub.includes("再设计") && !refImg) { toast.warning("墙板再设计需上传场景参照图"); return; }
    }
    const run = ++previewRun.current;
    previewWanted.current = true;
    stopPreviewPoll();
    setPreview({ preview_id: "", status: "running", stage: "", url: "", thumb: "", error: "" });
    setPreviewOpen(true);
    try {
      const { preview_id } = await api.createPreview({
        image_path: floor.path,
        room_path: roomImg?.path ?? null,
        ref_path: refImg?.path ?? null,
        params: omakaseSafeParams(params),
      });
      if (run !== previewRun.current || !previewWanted.current) {
        // await 期间弹窗已被关掉（关闭时还没有 pid、兜底取消发不出）：此刻拿到 pid 立即补发取消，不启动轮询
        api.cancelPreview(preview_id).catch(() => {});
        return;
      }
      setPreview((p) => (p ? { ...p, preview_id } : p)); // 回填 pid，closePreview 的兜底取消才拿得到
      pollPreview(preview_id, run);
    } catch (e) {
      if (run !== previewRun.current) return; // 弹窗已关/新预览已开，不覆盖新状态
      setPreview({
        preview_id: "", status: "failed", stage: "", url: "", thumb: "",
        error: (e as Error).message,
      });
    }
  }

  // 关预览弹窗：停轮询，若仍在跑则请求后端取消（不浪费一次计费）
  function closePreview(open: boolean) {
    setPreviewOpen(open);
    if (!open) {
      previewWanted.current = false;
      previewRun.current++; // 令在途 createPreview / 旧轮询的响应全部过期
      stopPreviewPoll();
      if (preview?.status === "running" && preview.preview_id) {
        api.cancelPreview(preview.preview_id).catch(() => {});
      }
    }
  }

  const total = jobs.length;
  const activeCount = jobs.filter(
    (j) =>
      j.status === "queued" ||
      j.status === "running" ||
      j.pro_polishing ||
      j.operation_status === "running",
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
                      <span className="truncate text-[13px] font-bold text-foreground">
                        {r.label}
                      </span>
                    </div>
                    <div className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                      {r.sub}
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}

          <SectionHeader className="mx-0.5 mb-[11px] mt-[22px]">
            我的配方 / MY RECIPES
          </SectionHeader>
          <div className="flex gap-[9px] overflow-x-auto pb-1.5">
            {myRecipes.map((r) => (
              <div
                key={r.id}
                className="group relative w-[172px] flex-none rounded-xl border border-border bg-card p-[11px] transition hover:border-primary hover:shadow-[0_6px_16px_rgba(120,90,60,.1)]"
              >
                <button
                  onClick={() => applyCustomRecipe(r)}
                  className="block w-full text-left"
                >
                  <div className="mb-[7px] flex items-center gap-[7px]">
                    <span className="flex h-[18px] w-[18px] flex-none items-center justify-center rounded-md bg-accent text-[11px]">
                      ★
                    </span>
                    <span className="truncate text-[13px] font-bold text-foreground">
                      {r.name}
                    </span>
                  </div>
                  <div className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                    {[r.params.workflow_mode?.split(" ")[0], r.params.style_type, r.params.lighting]
                      .filter(Boolean)
                      .join(" · ") || "参数快照"}
                  </div>
                </button>
                <div className="absolute right-[7px] top-[7px] hidden gap-1 group-hover:flex">
                  <button
                    title="改名"
                    onClick={() => setRecipeDlg({ open: true, id: r.id, name: r.name })}
                    className="flex h-[20px] w-[20px] items-center justify-center rounded-md bg-accent text-[11px] text-secondary-foreground hover:text-foreground"
                  >
                    ✎
                  </button>
                  <button
                    title="删除"
                    onClick={() => removeCustomRecipe(r)}
                    className="flex h-[20px] w-[20px] items-center justify-center rounded-md bg-accent text-[11px] text-secondary-foreground hover:text-destructive"
                  >
                    🗑
                  </button>
                </div>
              </div>
            ))}
            <button
              onClick={() => setRecipeDlg({ open: true, id: "", name: "" })}
              className="flex w-[172px] flex-none flex-col items-center justify-center gap-1 rounded-xl border-[1.5px] border-dashed border-border-strong p-[11px] text-[12px] font-semibold text-muted-foreground transition hover:border-primary hover:text-accent-foreground"
            >
              <span className="text-[16px] leading-none">＋</span>
              存为配方
            </button>
          </div>

          {options ? (
            <ParamsForm
              options={options}
              params={params}
              modelFilter={modelFilter}
              onParams={updateParams}
              onModelFilter={setModelFilter}
              refValue={refImg}
              onRefPick={setRefImg}
              roomValue={roomImg}
              onRoomPick={setRoomImg}
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
            className="flex h-[46px] flex-1 items-center justify-center gap-2 rounded-xl bg-primary text-[14.5px] font-bold text-primary-foreground shadow-[0_6px_16px_rgba(193,95,60,.32)] transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <path d="M13 2L4.5 13.5H11l-1 8.5L19.5 10H13l0-8z" />
            </svg>
            {submitting ? "提交中…" : "生成效果图"}
          </button>
          <button
            onClick={runPreview}
            disabled={!floor || previewOpen}
            title="用 Nano Banana 2 Lite 出一张 1K 快速预览（几秒、便宜），满意再点「生成效果图」出 4K"
            className="h-[46px] flex-none rounded-xl border border-border bg-card px-[15px] text-[13px] font-bold text-secondary-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            ⚡ 预览
          </button>
          <button
            onClick={openBatch}
            disabled={!floor || !options || params.workflow_mode.includes("墙板")}
            title={
              params.workflow_mode.includes("墙板")
                ? "墙板模式不支持批量"
                : params.workflow_mode.includes("Omakase")
                ? "Omakase 支持多地板批量（同场景 × 多块地板）"
                : undefined
            }
            className="h-[46px] flex-none rounded-xl border border-border bg-card px-[18px] text-[13.5px] font-bold text-secondary-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
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
            <span className="text-[12px] text-muted-foreground">
              {total} 个任务 · 完成 {doneCount}/{total}
            </span>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() =>
                api
                  .listJobs(50)
                  .then(setJobs)
                  .catch((e) => toast.error("刷新失败：" + (e as Error).message))
              }
              className="h-[30px] rounded-lg border border-border bg-card px-3 text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
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
              className="h-[30px] rounded-lg border border-border bg-card px-3 text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
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
              className="h-[30px] rounded-lg border border-border bg-card px-3 text-[12.5px] font-semibold text-destructive hover:bg-destructive-soft"
            >
              全部停止
            </button>
          </div>
        </div>

        {activeCount > 0 && (
          <div className="flex-none border-b border-border bg-card px-[22px] py-[11px]">
            <div className="mb-1.5 flex justify-between text-[11.5px] text-muted-foreground">
              <span>
                进行中 {activeCount} · 完成 {doneCount} / 共 {total}
              </span>
              <span className="font-bold text-foreground">{pct}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-md bg-muted">
              <div
                className="h-full rounded-md bg-primary transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-[22px] py-[18px]">
          {jobs.length === 0 ? (
            <div className="rounded-2xl border-[1.5px] border-dashed border-border-strong px-5 py-[54px] text-center text-[13px] text-muted-foreground">
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

      {/* 批量：多房间 × 同地板 ｜ 多地板 × 同场景 */}
      <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
        <DialogContent className="max-w-[560px]">
          <div className="space-y-3">
            <div>
              <div className="text-[15.5px] font-bold">批量生成</div>
              <div className="mt-0.5 text-[12px] text-muted-foreground">
                {batchTab === "rooms"
                  ? "同一地板 × 多个房间类型，一次性提交"
                  : "同一场景参数 × 多块地板小样（客户选款对比），逐板自动识色"}
              </div>
            </div>
            {!params.workflow_mode.includes("Omakase") && (
              <Segmented
                value={batchTab}
                onChange={setBatchTab}
                options={[
                  { value: "rooms", label: "多房间 × 同地板" },
                  { value: "floors", label: "多地板 × 同场景" },
                ]}
              />
            )}

            {batchTab === "rooms" ? (
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
            ) : (
              <div className="max-h-[320px] overflow-y-auto pt-1">
                {recentFloors.length === 0 ? (
                  <div className="py-6 text-center text-[12.5px] text-muted-foreground">
                    没有最近小样 · 请先在生成页上传地板图
                  </div>
                ) : (
                  <div className="grid grid-cols-4 gap-[9px]">
                    {recentFloors.map((s) => {
                      const on = batchFloors.some((x) => x.path === s.path);
                      return (
                        <button
                          key={s.path}
                          onClick={() =>
                            setBatchFloors((sel) =>
                              on
                                ? sel.filter((x) => x.path !== s.path)
                                : [...sel, s],
                            )
                          }
                          title={s.name}
                          className={
                            "relative overflow-hidden rounded-[10px] border-2 transition " +
                            (on
                              ? "border-primary shadow-[0_2px_8px_rgba(193,95,60,.25)]"
                              : "border-border hover:border-border-strong")
                          }
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={api.imgUrl(s.thumb)}
                            alt={s.name}
                            className="aspect-square w-full object-cover"
                          />
                          {on && (
                            <span className="absolute right-1 top-1 flex h-[18px] w-[18px] items-center justify-center rounded-full bg-primary text-[11px] font-bold text-white">
                              ✓
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center justify-between pt-1">
              <div className="flex gap-2">
                <button
                  onClick={() =>
                    batchTab === "rooms"
                      ? setBatchRooms(batchRoomOptions)
                      : setBatchFloors([...recentFloors])
                  }
                  className="h-8 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
                >
                  全选
                </button>
                <button
                  onClick={() =>
                    batchTab === "rooms" ? setBatchRooms([]) : setBatchFloors([])
                  }
                  className="h-8 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
                >
                  清空
                </button>
              </div>
              <button
                onClick={batchTab === "rooms" ? runBatch : runBatchFloors}
                disabled={
                  (batchTab === "rooms"
                    ? batchRooms.length === 0
                    : batchFloors.length === 0) || batchSubmitting
                }
                className="h-[38px] rounded-[10px] bg-primary px-5 text-[13.5px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
              >
                {batchSubmitting
                  ? "提交中…"
                  : `提交批量 (${batchTab === "rooms" ? batchRooms.length : batchFloors.length})`}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 快速预览（NB2 Lite · 1K） */}
      <Dialog open={previewOpen} onOpenChange={closePreview}>
        <DialogContent className="max-w-[720px]">
          <div className="space-y-3">
            <div>
              <div className="text-[15.5px] font-bold">快速预览 · 1K 草稿</div>
              <div className="mt-0.5 text-[12px] text-muted-foreground">
                Nano Banana 2 Lite · 满意就关掉点「生成效果图」出 4K
              </div>
            </div>

            {(!preview || preview.status === "running") && (
              <div className="flex flex-col items-center justify-center gap-3 py-14 text-[13px] text-muted-foreground">
                <svg
                  width="26"
                  height="26"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  className="animate-dc-spin text-primary"
                >
                  <path d="M21 12a9 9 0 1 1-6.2-8.6" />
                </svg>
                <span>{preview?.stage || "生成中…"}</span>
              </div>
            )}

            {preview?.status === "failed" && (
              <div className="rounded-[9px] bg-destructive-soft px-[12px] py-[10px] text-[12.5px] leading-relaxed text-destructive-ink">
                预览失败：{preview.error || "未知错误"}
              </div>
            )}

            {preview?.status === "done" && preview.url && (
              <>
                <div
                  className="cursor-zoom-in overflow-hidden rounded-[10px] border border-border"
                  onClick={() => setZoom(api.imgUrl(preview.url))}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={api.imgUrl(preview.thumb || preview.url)}
                    alt="快速预览"
                    className="w-full object-contain"
                  />
                </div>
                <div className="flex items-center justify-between text-[11.5px] text-muted-foreground">
                  <span>点击图片放大 · 1K 预览（非最终画质）</span>
                  <a
                    href={api.imgUrl(preview.url)}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:text-foreground"
                  >
                    ↗ 原图
                  </a>
                </div>
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* 我的配方：命名 / 改名 */}
      <Dialog
        open={recipeDlg.open}
        onOpenChange={(o) => setRecipeDlg((s) => ({ ...s, open: o }))}
      >
        <DialogContent className="max-w-[420px]">
          <div className="space-y-3">
            <div>
              <div className="text-[15.5px] font-bold">
                {recipeDlg.id ? "配方改名" : "存为配方"}
              </div>
              {!recipeDlg.id && (
                <div className="mt-0.5 text-[12px] text-muted-foreground">
                  保存当前全部场景参数（不含地板图与识色色调），下次一键套用
                </div>
              )}
            </div>
            <input
              value={recipeDlg.name}
              onChange={(e) => setRecipeDlg((s) => ({ ...s, name: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && submitRecipeDlg()}
              maxLength={40}
              placeholder="配方名，例如：北欧客厅·顺光"
              className="h-10 w-full rounded-[10px] border border-border bg-panel px-3 text-[13px] outline-none focus:ring-2 focus:ring-primary/20"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setRecipeDlg({ open: false, id: "", name: "" })}
                className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
              >
                取消
              </button>
              <button
                onClick={submitRecipeDlg}
                className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover"
              >
                保存
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ImageZoom url={zoom} onClose={() => setZoom(null)} />
    </div>
  );
}
