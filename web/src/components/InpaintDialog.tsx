/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  InpaintCandidate,
  InpaintStatusView,
  InpaintTargetPayload,
  JobView,
  ModelKey,
} from "@/lib/types";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Slider } from "@/components/ui/slider";
import { CompareSlider } from "@/components/CompareSlider";
import { cn } from "@/lib/utils";

/** 修补目标：任务候选（并入 stage 候选）/ 记录结果（append 回记录）/ 房间图预处理（另存新上传）。 */
export type InpaintTarget =
  | { kind: "job"; jobId: string; stage: ModelKey; imageRel: string }
  | { kind: "record"; jsonPath: string; recordId: string; resultId: string }
  | { kind: "room"; roomPath: string };

type InpaintDialogProps = {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  srcUrl: string; // 待修补图的相对 URL（/outputs/.. 或 /uploads/..）
  target: InpaintTarget;
  /** job 目标：apply 后带回任务快照；record 目标：无参调用（外层刷新） */
  onDone?: (jobView?: JobView) => void;
  /** room 目标专用：apply 后回填新房间图 */
  onRoomCleaned?: (path: string, url: string, thumb: string) => void;
};

// mask 画布长边上限：4K 原图按此缩放涂抹（省内存、笔触流畅），
// 后端 _prepare_inpaint_mask 会按宽高比校验后 NEAREST 对齐回原图尺寸。
const MASK_MAX_SIDE = 2048;
const UNDO_LIMIT = 20;
// 纯 eraser 模型不吃 prompt（BRIA/Finegrain/LaMa）；指令式模型把 prompt 当补充说明
const PURE_ERASERS = new Set(["bria-eraser", "finegrain-eraser", "lama"]);

function toTargetPayload(t: InpaintTarget): InpaintTargetPayload {
  if (t.kind === "job") return { kind: "job", jid: t.jobId, stage: t.stage, image_rel: t.imageRel };
  if (t.kind === "record")
    return { kind: "record", json_path: t.jsonPath, record_id: t.recordId, result_id: t.resultId };
  return { kind: "room", room_path: t.roomPath };
}

export function InpaintDialog(props: InpaintDialogProps) {
  if (!props.open) return null;
  return <InpaintSession key={props.srcUrl} {...props} />;
}

function InpaintSession({
  open,
  onOpenChange,
  srcUrl,
  target,
  onDone,
  onRoomCleaned,
}: InpaintDialogProps) {
  const [mode, setMode] = useState<"remove" | "add">("remove");
  const [prompt, setPrompt] = useState("");
  const [brush, setBrush] = useState(36); // 屏幕像素直径
  const [eraser, setEraser] = useState(false);
  const [hasMask, setHasMask] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [removeGrow, setRemoveGrow] = useState(8);
  const [addGrow, setAddGrow] = useState(0);
  const [removeFeather, setRemoveFeather] = useState(0.01);
  const [addFeather, setAddFeather] = useState(0.005);
  const [seedText, setSeedText] = useState("");
  const [nCount, setNCount] = useState(3); // Lightroom 式默认 3 变体
  // 流程：draw（涂抹）→ running（生成中）→ pick（候选挑选）
  const [task, setTask] = useState<{ iid: string; stage: string } | null>(null);
  const [candidates, setCandidates] = useState<InpaintCandidate[]>([]);
  const [pickIid, setPickIid] = useState("");
  const [selected, setSelected] = useState(0);
  const [partialNote, setPartialNote] = useState("");
  const [applying, setApplying] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // 当前配置的移除模型（决定 remove 模式下 prompt 框是否显示）
  const [removeModel, setRemoveModel] = useState("bria-eraser");
  const [addModel, setAddModel] = useState("flux-fill");
  const [inpaintProvider, setInpaintProvider] = useState("fal");
  const [inpaintConfigLoaded, setInpaintConfigLoaded] = useState(false);

  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const drawing = useRef(false);
  const lastPt = useRef<{ x: number; y: number } | null>(null);
  const undoStack = useRef<string[]>([]);
  const eraserRef = useRef(false);
  const brushRef = useRef(36);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const submitLock = useRef(false);
  const taskIdRef = useRef("");
  const pickIdRef = useRef("");
  const openRef = useRef(open);
  const modeRef = useRef<"remove" | "add">("remove");
  useEffect(() => {
    eraserRef.current = eraser;
  }, [eraser]);
  useEffect(() => {
    brushRef.current = brush;
  }, [brush]);
  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        const configuredRemove = c.inpaint_remove_model || "bria-eraser";
        const configuredProvider = c.inpaint_provider || "fal";
        setRemoveModel(configuredRemove);
        setAddModel(c.inpaint_add_model || "flux-fill");
        setInpaintProvider(configuredProvider);
        if (
          modeRef.current === "remove" &&
          configuredProvider === "fal" &&
          PURE_ERASERS.has(configuredRemove)
        ) {
          setNCount(1);
        }
      })
      .catch(() => {
        /* 拉不到配置就按默认展示，不阻塞 */
      })
      .finally(() => setInpaintConfigLoaded(true));
  }, []);
  useEffect(
    () => () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
      const iid = taskIdRef.current || pickIdRef.current;
      if (iid) void api.cancelInpaint(iid).catch(() => {});
    },
    [],
  );
  useEffect(() => {
    taskIdRef.current = task?.iid || "";
  }, [task]);
  useEffect(() => {
    pickIdRef.current = pickIid;
  }, [pickIid]);
  useEffect(() => {
    openRef.current = open;
  }, [open]);
  const eraserRemove =
    inpaintConfigLoaded && mode === "remove" && inpaintProvider === "fal" && PURE_ERASERS.has(removeModel);
  const grow = mode === "remove" ? removeGrow : addGrow;
  const feather = mode === "remove" ? removeFeather : addFeather;
  const setGrow = mode === "remove" ? setRemoveGrow : setAddGrow;
  const setFeather = mode === "remove" ? setRemoveFeather : setAddFeather;

  function changeMode(next: "remove" | "add") {
    modeRef.current = next;
    setMode(next);
    if (next === "remove" && inpaintConfigLoaded && inpaintProvider === "fal" && PURE_ERASERS.has(removeModel)) {
      setNCount(1);
    }
  }

  // ── 画布 ──
  const onImgLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    const canvas = canvasRef.current;
    if (!canvas || !img.naturalWidth) return;
    const scale = Math.min(1, MASK_MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
  }, []);

  function toCanvas(e: React.PointerEvent): { x: number; y: number; ratio: number } | null {
    const el = boxRef.current;
    const canvas = canvasRef.current;
    if (!el || !canvas || !canvas.width) return null;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return null;
    const ratio = canvas.width / r.width;
    return { x: (e.clientX - r.left) * ratio, y: (e.clientY - r.top) * ratio, ratio };
  }

  function strokeTo(p: { x: number; y: number; ratio: number }) {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(2, brushRef.current * p.ratio);
    ctx.globalCompositeOperation = eraserRef.current ? "destination-out" : "source-over";
    ctx.strokeStyle = "rgba(255,60,60,0.9)";
    ctx.beginPath();
    const from = lastPt.current ?? { x: p.x, y: p.y };
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(p.x + (from.x === p.x ? 0.01 : 0), p.y);
    ctx.stroke();
    lastPt.current = { x: p.x, y: p.y };
  }

  function onDown(e: React.PointerEvent) {
    const p = toCanvas(e);
    const canvas = canvasRef.current;
    if (!p || !canvas) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    undoStack.current.push(canvas.toDataURL());
    if (undoStack.current.length > UNDO_LIMIT) undoStack.current.shift();
    setCanUndo(true);
    drawing.current = true;
    lastPt.current = null;
    strokeTo(p);
  }

  function onMove(e: React.PointerEvent) {
    const cursor = cursorRef.current;
    const el = boxRef.current;
    if (cursor && el) {
      const r = el.getBoundingClientRect();
      cursor.style.left = `${e.clientX - r.left}px`;
      cursor.style.top = `${e.clientY - r.top}px`;
      cursor.style.display = "block";
    }
    if (!drawing.current) return;
    const p = toCanvas(e);
    if (p) strokeTo(p);
  }

  function onUp() {
    if (!drawing.current) return;
    drawing.current = false;
    lastPt.current = null;
    setHasMask(maskNotEmpty());
  }

  function maskNotEmpty(): boolean {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !canvas.width) return false;
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
    return false;
  }

  function undo() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const snap = undoStack.current.pop();
    if (!canvas || !ctx || snap === undefined) return;
    setCanUndo(undoStack.current.length > 0);
    const img = new Image();
    img.onload = () => {
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0);
      setHasMask(maskNotEmpty());
    };
    img.src = snap;
  }

  function clearMask() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    undoStack.current.push(canvas.toDataURL());
    if (undoStack.current.length > UNDO_LIMIT) undoStack.current.shift();
    setCanUndo(true);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasMask(false);
  }

  /** 涂抹层 → 黑白 mask PNG（alpha>0 = 白 = 重绘区），返回纯 base64。 */
  function exportMask(): string | null {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !canvas.width) return null;
    const src = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const out = new ImageData(canvas.width, canvas.height);
    for (let i = 0; i < src.data.length; i += 4) {
      const v = src.data[i + 3] > 0 ? 255 : 0;
      out.data[i] = out.data[i + 1] = out.data[i + 2] = v;
      out.data[i + 3] = 255;
    }
    const off = document.createElement("canvas");
    off.width = canvas.width;
    off.height = canvas.height;
    const octx = off.getContext("2d");
    if (!octx) return null;
    octx.putImageData(out, 0, 0);
    return off.toDataURL("image/png").split(",", 2)[1] ?? null;
  }

  // ── 提交 / 轮询 / 挑选 / 应用 ──
  const pollStatus = useCallback((iid: string) => {
    const tick = () => {
      api
        .inpaintStatus(iid)
        .then((s: InpaintStatusView) => {
          if (s.status === "done") {
            taskIdRef.current = "";
            pickIdRef.current = iid;
            setTask(null);
            setCandidates(s.candidates || []);
            setPickIid(iid);
            setSelected(0);
            setPartialNote([s.notice, s.error].filter(Boolean).join("；"));
          } else if (s.status === "failed") {
            taskIdRef.current = "";
            setTask(null);
            toast.error("修补失败：" + (s.error || "未知错误"));
          } else if (s.status === "cancelled") {
            taskIdRef.current = "";
            setTask(null);
          } else {
            setTask({ iid, stage: s.stage });
            pollTimer.current = setTimeout(tick, 1500);
          }
        })
        .catch(() => {
          pollTimer.current = setTimeout(tick, 3000);
        });
    };
    tick();
  }, []);

  async function submit() {
    if (submitLock.current) return;
    const mask = exportMask();
    if (!mask) {
      toast.error("请先涂抹要处理的区域");
      return;
    }
    if (mode === "add" && !prompt.trim()) {
      toast.error("生成式添加需要描述要添加的内容");
      return;
    }
    const seed = seedText.trim() ? Number(seedText.trim()) : undefined;
    submitLock.current = true;
    setSubmitting(true);
    try {
      const r = await api.submitInpaint({
        mask_b64: mask,
        prompt: prompt.trim(),
        mode,
        grow,
        feather,
        n: nCount,
        ...(Number.isFinite(seed) ? { seed } : {}),
        target: toTargetPayload(target),
      });
      if (!openRef.current) {
        void api.cancelInpaint(r.inpaint_id).catch(() => {});
        return;
      }
      setNCount(r.effective_n);
      setPartialNote(r.notice || "");
      taskIdRef.current = r.inpaint_id;
      setTask({ iid: r.inpaint_id, stage: "" });
      pollStatus(r.inpaint_id);
    } catch (e) {
      toast.error("提交失败：" + (e as Error).message);
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  async function discardCandidates() {
    if (pickIid) {
      try {
        await api.cancelInpaint(pickIid); // 终态 cancel = 清理服务端临时候选
      } catch {
        /* 已被清理则忽略 */
      }
    }
    setCandidates([]);
    setPickIid("");
    setPartialNote("");
  }

  async function redraw() {
    await discardCandidates();
    // 回到涂抹层，保留用户已涂的 mask
  }

  async function reroll() {
    await discardCandidates();
    await submit();
  }

  async function applySelected() {
    if (!pickIid || !candidates[selected]) return;
    setApplying(true);
    try {
      const r = await api.applyInpaint(pickIid, selected);
      pickIdRef.current = "";
      setPickIid("");
      if (target.kind === "job") {
        toast.success("已保存为新候选（‹n/N› 可切回原图对比）");
        onDone?.(r.job);
      } else if (target.kind === "record") {
        toast.success("修补结果已追加到该记录");
        onDone?.();
      } else {
        onRoomCleaned?.(r.path || "", r.url || "", r.thumb || r.url || "");
        toast.success("已替换为清理后的房间图");
      }
      onOpenChange(false);
    } catch (e) {
      toast.error("提交失败：" + (e as Error).message);
    } finally {
      setApplying(false);
    }
  }

  async function cancelRunning() {
    if (!task) return;
    try {
      await api.cancelInpaint(task.iid);
    } catch {
      /* 已终态则忽略 */
    }
    if (pollTimer.current) clearTimeout(pollTimer.current);
    setTask(null);
  }

  function handleOpenChange(next: boolean) {
    openRef.current = next;
    if (!next) {
      if (pollTimer.current) clearTimeout(pollTimer.current);
      const iid = taskIdRef.current || pickIdRef.current;
      if (iid) void api.cancelInpaint(iid).catch(() => {});
      taskIdRef.current = "";
      pickIdRef.current = "";
      setTask(null);
      setPickIid("");
      setCandidates([]);
      setPartialNote("");
    }
    onOpenChange(next);
  }

  const modeBtn = (active: boolean) =>
    `h-8 rounded-[8px] px-3 text-[12px] font-bold transition-colors ${
      active
        ? "bg-primary text-primary-foreground"
        : "border border-border bg-panel text-secondary-foreground hover:bg-accent"
    }`;
  const toolBtn =
    "h-8 rounded-[8px] border border-border bg-panel px-2.5 text-[12px] font-semibold text-secondary-foreground hover:bg-accent disabled:opacity-40";

  // ── 候选挑选视图（三种目标统一）──
  if (candidates.length > 0) {
    return (
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="max-h-[94vh] max-w-[96vw] overflow-y-auto sm:max-w-[min(96vw,1280px)]">
          <div className="space-y-3">
            <div>
              <div className="text-[15.5px] font-bold">挑选修补结果</div>
              <div className="mt-0.5 text-[12px] text-muted-foreground">
                生成了 {candidates.length} 个候选：点缩略图切换，拖动中缝对比原图，满意就「使用这张」，不满意可再抽。
                {partialNote ? ` ⚠ ${partialNote}` : ""}
              </div>
            </div>
            <CompareSlider
              before={api.imgUrl(srcUrl)}
              after={api.imgUrl(candidates[selected]?.url || "")}
            />
            <div className="flex flex-wrap items-center gap-2">
              {candidates.map((c, i) => (
                <button
                  key={c.url}
                  onClick={() => setSelected(i)}
                  className={cn(
                    "overflow-hidden rounded-[10px] border-2",
                    i === selected ? "border-primary" : "border-border opacity-70 hover:opacity-100",
                  )}
                  title={`候选 ${i + 1}`}
                >
                  <img
                    src={api.imgUrl(c.thumb || c.url)}
                    alt={`候选 ${i + 1}`}
                    className="block h-[84px] w-[112px] object-cover"
                  />
                </button>
              ))}
              <div className="ml-auto flex items-center gap-2">
                <button className={toolBtn} onClick={redraw} disabled={applying}>
                  ← 重新涂抹
                </button>
                <button
                  className={toolBtn}
                  onClick={reroll}
                  disabled={applying}
                  title="重新生成一批候选（按张计费）"
                >
                  🎲 再抽 {nCount} 张
                </button>
                <button
                  onClick={applySelected}
                  disabled={applying}
                  className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
                >
                  {applying ? "保存中…" : "✓ 使用这张"}
                </button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {/* sm:前缀必须带：DialogContent 默认 sm:max-w-sm，无前缀的 max-w 在 sm+ 会被它覆盖 */}
      <DialogContent className="max-h-[94vh] max-w-[96vw] overflow-y-auto sm:max-w-[min(96vw,1280px)]">
        <div className="space-y-3">
          <div>
            <div className="text-[15.5px] font-bold">生成式修补</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              用画笔涂抹要处理的区域：移除会适度外扩，请把物体及阴影一起涂上；添加默认严格限制在涂抹区。最终处理范围之外保持原图。
            </div>
          </div>

          {/* 画布区：内层容器收缩包裹图像（不能用 w-full+object-contain，
              信箱留白会让 mask 画布与图像内容错位） */}
          <div className="flex justify-center overflow-hidden rounded-[10px] border border-border bg-black/5">
          <div
            ref={boxRef}
            className="relative select-none"
            style={{ touchAction: "none", cursor: "none" }}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onUp}
            onPointerLeave={() => {
              if (cursorRef.current) cursorRef.current.style.display = "none";
            }}
          >
            <img
              src={api.imgUrl(srcUrl)}
              alt="待修补图"
              draggable={false}
              onLoad={onImgLoad}
              className="block max-h-[58vh] max-w-full"
            />
            <canvas
              ref={canvasRef}
              className="absolute inset-0 h-full w-full opacity-60"
            />
            {/* 笔刷光标 */}
            <div
              ref={cursorRef}
              className="pointer-events-none absolute hidden -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow-[0_0_0_1px_rgba(0,0,0,.6)]"
              style={{ width: brush, height: brush }}
            />
            {task && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/45 text-white">
                <svg
                  width="22"
                  height="22"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.6"
                  strokeLinecap="round"
                  className="animate-dc-spin"
                >
                  <path d="M21 12a9 9 0 1 1-6.2-8.6" />
                </svg>
                <span className="text-[12.5px] font-semibold">
                  {task.stage || `生成 ${nCount} 个候选中…`}
                </span>
                <button
                  onClick={cancelRunning}
                  className="mt-1 rounded-md border border-white/60 px-2.5 py-0.5 text-[11.5px] font-semibold hover:bg-white/15"
                >
                  取消
                </button>
              </div>
            )}
          </div>
          </div>

          {/* 工具行 */}
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex items-center gap-1.5">
              <button className={modeBtn(mode === "remove")} onClick={() => changeMode("remove")}>
                🧹 生成式移除
              </button>
              <button className={modeBtn(mode === "add")} onClick={() => changeMode("add")}>
                ✨ 生成式添加
              </button>
            </div>
            <div className="flex min-w-[180px] flex-1 items-center gap-2">
              <span className="flex-none text-[12px] font-semibold text-secondary-foreground">笔刷</span>
              <Slider
                value={brush}
                min={8}
                max={120}
                step={2}
                onValueChange={(v) => setBrush(Array.isArray(v) ? v[0] : (v as number))}
              />
              <span className="w-8 flex-none text-right text-[12px] tabular-nums text-muted-foreground">
                {brush}
              </span>
            </div>
            <button
              className={`${toolBtn} ${eraser ? "border-primary text-primary" : ""}`}
              onClick={() => setEraser((v) => !v)}
              title="橡皮擦：擦掉多涂的部分"
            >
              🧽 橡皮 {eraser ? "开" : "关"}
            </button>
            <button className={toolBtn} onClick={undo} disabled={!canUndo}>
              ↩ 撤销
            </button>
            <button className={toolBtn} onClick={clearMask} disabled={!hasMask}>
              清空
            </button>
            {/* 候选数：Lightroom 式一次多变体挑选 */}
            <div
              className="flex items-center gap-1 rounded-lg border border-border px-1.5 py-0.5"
              title="一次生成几个候选供挑选（按张计费）"
            >
              <span className="text-[11px] font-semibold text-muted-foreground">候选</span>
              {[1, 2, 3].map((k) => (
                <button
                  key={k}
                  onClick={() => setNCount(k)}
                  disabled={eraserRemove && k > 1}
                  className={cn(
                    "rounded px-1.5 text-[11.5px] font-semibold",
                    nCount === k
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-35",
                  )}
                  title={eraserRemove && k > 1 ? "专职移除模型不支持可控变体，避免重复计费" : undefined}
                >
                  ×{k}
                </button>
              ))}
            </div>
            <button
              type="button"
              aria-expanded={advancedOpen}
              onClick={() => setAdvancedOpen((v) => !v)}
              className={toolBtn}
            >
              高级 {advancedOpen ? "收起" : "展开"}
            </button>
          </div>

          {/* prompt 行（纯 eraser 移除时无需描述，隐藏输入框） */}
          <div className="flex flex-wrap items-center gap-2.5">
            {eraserRemove ? (
              <span className="flex h-9 min-w-[260px] flex-1 items-center rounded-[9px] border border-dashed border-border bg-panel/50 px-3 text-[12px] text-muted-foreground">
                当前移除模型自动擦除并重建背景，无需文字描述
              </span>
            ) : (
              <input
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={
                  mode === "remove"
                    ? "可选补充说明：比如“这里应延续木地板”（留空 = 自动重建周边材质）"
                    : `必填：描述要添加的内容，如“一盆大型龟背竹绿植”（当前 ${addModel}）`
                }
                className="h-9 min-w-[260px] flex-1 rounded-[9px] border border-border bg-panel px-3 text-[12.5px] outline-none placeholder:text-muted-foreground focus:border-primary"
              />
            )}
            <button
              onClick={submit}
              disabled={!hasMask || !!task || submitting}
              title={!hasMask ? "请先涂抹选区" : undefined}
              className="h-9 flex-none rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
            >
              {task || submitting
                ? "处理中…"
                : mode === "remove"
                ? `移除所涂区域 ×${nCount}`
                : `在所涂区域生成 ×${nCount}`}
            </button>
          </div>

          {advancedOpen && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-[10px] border border-border bg-panel/60 p-3">
              <div className="flex min-w-[220px] items-center gap-2" title={mode === "remove" ? "移除会按选区尺寸自动外扩，这里设置最小值；请仍把完整阴影涂上" : "添加默认不外扩；只有你主动调大时才扩展处理范围"}>
                <span className="flex-none text-[11.5px] font-semibold text-secondary-foreground">选区外扩</span>
                <Slider
                  value={grow}
                  min={0}
                  max={64}
                  step={1}
                  onValueChange={(v) => setGrow(Array.isArray(v) ? v[0] : (v as number))}
                />
                <span className="w-10 flex-none text-right text-[11px] tabular-nums text-muted-foreground">{grow}px</span>
              </div>
              <div className="flex min-w-[220px] items-center gap-2" title={mode === "add" ? "添加模式只向有效选区内部羽化，选区外像素保持不变" : "移除模式在外扩后的边缘做柔和过渡"}>
                <span className="flex-none text-[11.5px] font-semibold text-secondary-foreground">边缘羽化</span>
                <Slider
                  value={feather}
                  min={0}
                  max={0.1}
                  step={0.005}
                  onValueChange={(v) => setFeather(Array.isArray(v) ? v[0] : (v as number))}
                />
                <span className="w-10 flex-none text-right text-[11px] tabular-nums text-muted-foreground">
                  {(feather * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex items-center gap-2" title="固定随机种子可复现同一批结果；留空随机（纯 eraser 模型无此参数）">
                <span className="flex-none text-[11.5px] font-semibold text-secondary-foreground">Seed</span>
                <input
                  value={seedText}
                  onChange={(e) => setSeedText(e.target.value.replace(/[^0-9]/g, ""))}
                  placeholder="随机"
                  className="h-8 w-[110px] rounded-[8px] border border-border bg-panel px-2 text-[12px] outline-none focus:border-primary"
                />
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
