/* eslint-disable @next/next/no-img-element */
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ColorMatchRect, JobView } from "@/lib/types";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Slider } from "@/components/ui/slider";
import { ImageZoom } from "@/components/ImageZoom";

/** 校色目标：任务候选（并入该 stage 候选列表）或记录结果（append 回记录）。 */
export type ColorMatchTarget =
  | { kind: "job"; jobId: string; stage: "b2" | "pro" }
  | { kind: "record"; jsonPath: string; recordId: string; resultId: string };

const DEFAULT_RECT: ColorMatchRect = { x: 0.05, y: 0.45, w: 0.9, h: 0.5 };

/**
 * 手动校色弹窗（大窗双栏 + 实时预览）：
 * 左栏原图上拖框住地板（常驻对照），右栏 canvas 实时显示校色结果。
 * 实时原理：强度是线性混合（src·(1−m·s)+t·m·s ≡ 先按 s=1 出完整结果 F，再以 alpha=s 混合 src 与 F），
 * 故服务端只在选区/参照变化时按 strength=1.0 重算一次（防抖自动请求），
 * 强度滑杆纯客户端 canvas 混合、零网络即时生效；提交仍带真实 strength，结果与预览一致。
 */
export function ColorMatchDialog({
  open,
  onOpenChange,
  srcUrl,
  imageRel,
  refUrl,
  refPath,
  target,
  onDone,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  srcUrl: string; // 成图完整 URL（展示用）
  imageRel: string; // 成图相对 /outputs 路径（回传后端）
  refUrl: string; // 参照小样 URL（展示用，可为空）
  refPath: string; // 参照小样后端路径（可为空，记录侧后端会回退 gen_context）
  target: ColorMatchTarget;
  onDone?: (jobView?: JobView) => void;
}) {
  const [rect, setRect] = useState<ColorMatchRect>(DEFAULT_RECT);
  const [strength, setStrength] = useState(0.8);
  const [ref, setRef] = useState<{ url: string; path: string }>({ url: refUrl, path: refPath });
  const [previewing, setPreviewing] = useState(false);
  const [ready, setReady] = useState(false); // fullPreview 与当前 rect/ref 对应（可保存）
  const [saving, setSaving] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);
  // 小样参照贴片：叠在右栏结果图上，紧挨结果方便比色；可收起、可点开全屏
  const [showRefPatch, setShowRefPatch] = useState(true);

  const box = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  // 实时管线：原图 / 服务端 strength=1.0 完整结果；请求序号防乱序；防抖计时器
  const srcImgRef = useRef<HTMLImageElement | null>(null);
  const fullPreviewRef = useRef<HTMLImageElement | null>(null);
  const previewSeq = useRef(0);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const strengthRef = useRef(strength);
  strengthRef.current = strength;

  // canvas 重绘：原图打底 + fullPreview 按当前强度 alpha 叠加（即时，无网络）
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const full = fullPreviewRef.current;
    const src = srcImgRef.current;
    if (!canvas || !full) return;
    if (canvas.width !== full.naturalWidth || canvas.height !== full.naturalHeight) {
      canvas.width = full.naturalWidth;
      canvas.height = full.naturalHeight;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.globalAlpha = 1;
    if (src && src.complete && src.naturalWidth > 0) {
      ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
      ctx.globalAlpha = strengthRef.current;
    }
    ctx.drawImage(full, 0, 0, canvas.width, canvas.height);
    ctx.globalAlpha = 1;
  }, []);

  // 服务端预览（strength 固定 1.0；选区/参照变化时防抖调用）
  const requestPreview = useCallback(
    (r: ColorMatchRect, refPathNow: string) => {
      if (!refPathNow || r.w < 0.02 || r.h < 0.02) return;
      const seq = ++previewSeq.current;
      setPreviewing(true);
      api
        .colorMatchPreview({ image_rel: imageRel, ref_path: refPathNow, rect: r, strength: 1.0 })
        .then((res) => {
          if (seq !== previewSeq.current) return; // 过期响应丢弃
          const img = new Image();
          img.onload = () => {
            if (seq !== previewSeq.current) return;
            fullPreviewRef.current = img;
            setReady(true);
            setPreviewing(false);
            redraw();
          };
          img.src = res.preview;
        })
        .catch((e) => {
          if (seq !== previewSeq.current) return;
          setPreviewing(false);
          toast.error("预览失败：" + (e as Error).message);
        });
    },
    [imageRel, redraw],
  );

  const schedulePreview = useCallback(
    (r: ColorMatchRect, refPathNow: string) => {
      setReady(false);
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
      debounceTimer.current = setTimeout(() => requestPreview(r, refPathNow), 350);
    },
    [requestPreview],
  );

  // 打开时重置并按默认选区自动出首帧预览；关闭时作废在途请求
  useEffect(() => {
    if (open) {
      setRect(DEFAULT_RECT);
      setStrength(0.8);
      setRef({ url: refUrl, path: refPath });
      fullPreviewRef.current = null;
      setReady(false);
      const src = new Image();
      src.src = api.imgUrl(srcUrl);
      src.onload = () => redraw();
      srcImgRef.current = src;
      requestPreview(DEFAULT_RECT, refPath);
    } else {
      previewSeq.current++;
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
      setPreviewing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, refUrl, refPath, srcUrl]);

  function norm(e: React.PointerEvent): { x: number; y: number } | null {
    const el = box.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return null;
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    };
  }

  function onDown(e: React.PointerEvent) {
    const p = norm(e);
    if (!p) return;
    dragStart.current = p;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setRect({ x: p.x, y: p.y, w: 0.001, h: 0.001 });
    setReady(false);
  }
  function onMove(e: React.PointerEvent) {
    const s = dragStart.current;
    if (!s) return;
    const p = norm(e);
    if (!p) return;
    setRect({
      x: Math.min(s.x, p.x),
      y: Math.min(s.y, p.y),
      w: Math.max(0.001, Math.abs(p.x - s.x)),
      h: Math.max(0.001, Math.abs(p.y - s.y)),
    });
  }
  function onUp() {
    if (!dragStart.current) return;
    dragStart.current = null;
    // 松手才请求服务端（拖动过程只画框）
    setRect((r) => {
      schedulePreview(r, ref.path);
      return r;
    });
  }

  async function doSave() {
    setSaving(true);
    try {
      if (target.kind === "job") {
        const jv = await api.jobColorMatch(target.jobId, {
          image_rel: imageRel,
          ref_path: ref.path,
          rect,
          strength,
          stage: target.stage,
        });
        toast.success("已保存为新候选（‹n/N› 可切回原图对比）");
        onDone?.(jv);
      } else {
        await api.recordColorMatch({
          json_path: target.jsonPath,
          record_id: target.recordId,
          result_id: target.resultId,
          ref_path: ref.path,
          rect,
          strength,
        });
        toast.success("校色结果已追加到该记录");
        onDone?.();
      }
      onOpenChange(false);
    } catch (e) {
      toast.error("保存失败：" + (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function pickRef(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    try {
      const s = await api.uploadRef(f);
      setRef({ url: s.url, path: s.path }); // 用原图 URL：贴片点开放大要看全尺寸小样
      schedulePreview(rect, s.path);
      toast.success("已更换参照图");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  const pct = (v: number) => `${v * 100}%`;
  const paneTitle =
    "mb-1.5 flex items-center gap-1.5 text-[11px] font-extrabold tracking-[0.08em] text-accent-foreground";

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* sm:前缀必须带：DialogContent 默认 sm:max-w-sm，无前缀的 max-w 在 sm+ 会被它覆盖 */}
      <DialogContent className="max-w-[96vw] sm:max-w-[min(96vw,1500px)]">
        <div className="space-y-3">
          <div>
            <div className="text-[15.5px] font-bold">手动校色</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              左图拖框住地板区域，右图实时显示校色结果；强度即时生效。只改选区颜色，边缘自动羽化，本地处理不计费。
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 max-[1000px]:grid-cols-1">
            {/* 左栏：原图 + 框选 */}
            <div className="min-w-0">
              <div className={paneTitle}>
                原图 · 拖动框选地板
                <button
                  title="放大原图"
                  onClick={() => setZoom(api.imgUrl(srcUrl))}
                  className="ml-auto rounded px-1.5 text-[12px] font-semibold text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  🔍
                </button>
              </div>
              <div
                ref={box}
                className="relative cursor-crosshair select-none overflow-hidden rounded-[10px] border border-border bg-black/5"
                style={{ touchAction: "none" }}
                onPointerDown={onDown}
                onPointerMove={onMove}
                onPointerUp={onUp}
                onPointerCancel={onUp}
              >
                <img
                  src={api.imgUrl(srcUrl)}
                  alt="原图"
                  className="block max-h-[62vh] w-full object-contain"
                  draggable={false}
                />
                <div className="pointer-events-none absolute inset-x-0 top-0 bg-black/45" style={{ height: pct(rect.y) }} />
                <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-black/45" style={{ height: pct(Math.max(0, 1 - rect.y - rect.h)) }} />
                <div className="pointer-events-none absolute bg-black/45" style={{ top: pct(rect.y), height: pct(rect.h), left: 0, width: pct(rect.x) }} />
                <div className="pointer-events-none absolute bg-black/45" style={{ top: pct(rect.y), height: pct(rect.h), right: 0, width: pct(Math.max(0, 1 - rect.x - rect.w)) }} />
                <div
                  className="pointer-events-none absolute border-2 border-primary shadow-[0_0_0_1px_rgba(255,255,255,.5)]"
                  style={{ left: pct(rect.x), top: pct(rect.y), width: pct(rect.w), height: pct(rect.h) }}
                />
              </div>
            </div>

            {/* 右栏：实时校色结果（canvas） */}
            <div className="min-w-0">
              <div className={paneTitle}>
                校色后 · 实时
                {previewing && (
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.6"
                    strokeLinecap="round"
                    className="animate-dc-spin text-primary"
                  >
                    <path d="M21 12a9 9 0 1 1-6.2-8.6" />
                  </svg>
                )}
                <span className="ml-auto text-[11px] font-semibold text-muted-foreground">
                  点击放大看细节
                </span>
              </div>
              <div className="relative overflow-hidden rounded-[10px] border border-border bg-black/5">
                <canvas
                  ref={canvasRef}
                  onClick={() => {
                    const c = canvasRef.current;
                    if (c && fullPreviewRef.current) setZoom(c.toDataURL("image/jpeg", 0.9));
                  }}
                  className="block max-h-[62vh] w-full cursor-zoom-in object-contain"
                />
                {!fullPreviewRef.current && (
                  <div className="absolute inset-0 flex items-center justify-center text-[12.5px] text-muted-foreground">
                    {previewing ? "首次预览生成中…" : "框选地板区域后自动出结果"}
                  </div>
                )}
                {/* 小样参照贴片：校色的目标色，贴着结果图对比 */}
                {ref.url && showRefPatch && (
                  <div className="absolute right-2 top-2 overflow-hidden rounded-[10px] border-2 border-white/90 shadow-[0_4px_14px_rgba(0,0,0,.35)]">
                    <img
                      src={api.imgUrl(ref.url)}
                      alt="小样参照"
                      title="点击放大小样"
                      onClick={(e) => {
                        e.stopPropagation();
                        setZoom(api.imgUrl(ref.url));
                      }}
                      className="block h-[clamp(100px,14vw,180px)] w-[clamp(100px,14vw,180px)] cursor-zoom-in object-cover"
                    />
                    <span className="pointer-events-none absolute left-0 top-0 rounded-br-md bg-[rgba(26,24,21,.6)] px-[6px] py-[2px] text-[10px] font-bold text-white">
                      小样
                    </span>
                    <button
                      title="收起小样"
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowRefPatch(false);
                      }}
                      className="absolute right-0 top-0 flex h-[18px] w-[18px] items-center justify-center rounded-bl-md bg-[rgba(26,24,21,.6)] text-[10px] font-bold text-white hover:bg-[rgba(26,24,21,.85)]"
                    >
                      ✕
                    </button>
                  </div>
                )}
                {ref.url && !showRefPatch && (
                  <button
                    title="显示小样参照"
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowRefPatch(true);
                    }}
                    className="absolute right-2 top-2 rounded-lg bg-[rgba(26,24,21,.6)] px-2 py-1 text-[11px] font-bold text-white hover:bg-[rgba(26,24,21,.85)]"
                  >
                    小样
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* 参照小样 */}
            <div className="flex flex-none items-center gap-2">
              <div className="h-[44px] w-[44px] overflow-hidden rounded-[8px] border border-border bg-panel">
                {ref.url ? (
                  <img src={api.imgUrl(ref.url)} alt="参照" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center text-[10px] text-muted-foreground">无</div>
                )}
              </div>
              <label className="cursor-pointer text-[11.5px] font-semibold text-secondary-foreground hover:text-accent-foreground">
                换参照
                <input type="file" accept="image/*" className="hidden" onChange={pickRef} />
              </label>
            </div>
            {/* 强度（客户端即时混合） */}
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <span className="flex-none text-[12px] font-semibold text-secondary-foreground">强度</span>
              <Slider
                value={strength}
                min={0.1}
                max={1}
                step={0.05}
                onValueChange={(v) => {
                  const s = Array.isArray(v) ? v[0] : (v as number);
                  setStrength(s);
                  strengthRef.current = s;
                  redraw();
                }}
              />
              <span className="w-10 flex-none text-right text-[12px] tabular-nums text-muted-foreground">
                {Math.round(strength * 100)}%
              </span>
            </div>
            <button
              onClick={doSave}
              disabled={!ready || saving}
              title={!ready ? "预览更新中…" : undefined}
              className="h-9 flex-none rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
            >
              {saving
                ? "保存中…"
                : !ready
                ? "更新预览中…"
                : target.kind === "job"
                ? "保存为新候选"
                : "保存到记录"}
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
    <ImageZoom url={zoom} onClose={() => setZoom(null)} />
    </>
  );
}
