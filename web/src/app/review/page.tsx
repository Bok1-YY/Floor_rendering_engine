/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ReviewGalleryItem, ReviewSummary } from "@/lib/types";
import { toast } from "sonner";
import { ImageZoom } from "@/components/ImageZoom";
import { cn } from "@/lib/utils";

const DIM_LABELS: [string, string][] = [
  ["workflow_mode", "工作流"],
  ["style", "风格"],
  ["room_type", "房间"],
  ["seam", "缝型"],
];

const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${Math.round(v * 100)}%`;

export default function ReviewPage() {
  const [data, setData] = useState<ReviewSummary | null>(null);
  const [galleryFilter, setGalleryFilter] = useState<"pass" | "best">("pass");
  const [gallery, setGallery] = useState<ReviewGalleryItem[]>([]);
  const [zoom, setZoom] = useState<string | null>(null);
  const summarySeq = useRef(0);
  const gallerySeq = useRef(0);

  function loadSummary() {
    const seq = ++summarySeq.current;
    api
      .getReviewSummary()
      .then((next) => seq === summarySeq.current && setData(next))
      .catch((e) => seq === summarySeq.current && toast.error((e as Error).message));
  }
  function loadGallery(filter: "pass" | "best") {
    const seq = ++gallerySeq.current;
    api
      .getReviewGallery(filter, 60)
      .then((next) => seq === gallerySeq.current && setGallery(next))
      .catch((e) => seq === gallerySeq.current && toast.error((e as Error).message));
  }
  function loadAll() {
    loadSummary();
    loadGallery(galleryFilter);
  }
  useEffect(() => {
    loadSummary();
  }, []);
  useEffect(() => {
    loadGallery(galleryFilter);
  }, [galleryFilter]);

  const chip = (active: boolean) =>
    cn(
      "rounded-lg border px-[13px] py-1.5 text-[12.5px] font-semibold transition-colors",
      active
        ? "border-primary bg-primary-soft text-accent-foreground"
        : "border-border bg-card text-secondary-foreground hover:bg-accent",
    );

  return (
    <div className="h-full overflow-y-auto p-[26px]">
      <div className="mx-auto max-w-[980px]">
        <div className="mb-[18px] flex items-center justify-between">
          <div className="text-[17px] font-extrabold tracking-tight">评审复盘</div>
          <button
            onClick={loadAll}
            className="h-[34px] rounded-[9px] border border-border bg-card px-[14px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
          >
            刷新
          </button>
        </div>

        {!data ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : (
          <>
            {/* 总览 */}
            <div className="grid grid-cols-4 gap-[14px]">
              <Stat label="总出图" value={String(data.overview.total)} color="var(--foreground)" />
              <Stat
                label={`已评审（覆盖率 ${pct(data.overview.coverage)}）`}
                value={String(data.overview.reviewed)}
                color="var(--foreground)"
              />
              <Stat label="通过率（按已评审）" value={pct(data.overview.pass_rate)} color="var(--success)" />
              <Stat label="最佳图" value={String(data.overview.best)} color="var(--accent-foreground)" />
            </div>

            {/* 维度通过率 */}
            <div className="mt-[14px] grid grid-cols-2 gap-[14px]">
              {DIM_LABELS.map(([dim, label]) => {
                const rows = (data.dimensions[dim] || []).filter(
                  (r) => r.pass + r.backup + r.rejected > 0,
                );
                return (
                  <div
                    key={dim}
                    className="rounded-[14px] border border-border bg-card p-[18px] shadow-[0_2px_8px_rgba(120,90,60,.05)]"
                  >
                    <div className="mb-3 text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                      按{label}的通过率
                    </div>
                    {rows.length === 0 ? (
                      <div className="py-3 text-[12px] text-muted-foreground">
                        该维度还没有已评审的图
                      </div>
                    ) : (
                      <div className="space-y-[9px]">
                        {rows.slice(0, 8).map((r) => (
                          <div key={r.key}>
                            <div className="mb-1 flex items-baseline justify-between text-[12px]">
                              <span className="truncate font-semibold text-foreground">
                                {r.key}
                              </span>
                              <span className="ml-2 flex-none text-muted-foreground">
                                {r.pass}/{r.pass + r.backup + r.rejected} 通过 ·{" "}
                                <b className="text-success">{pct(r.pass_rate)}</b>
                              </span>
                            </div>
                            <div className="h-[7px] w-full overflow-hidden rounded-md bg-muted">
                              <div
                                className="h-full rounded-md"
                                style={{
                                  width: `${Math.round((r.pass_rate ?? 0) * 100)}%`,
                                  background:
                                    "linear-gradient(90deg,var(--success),var(--chart-3))",
                                }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* 问题标签分布 */}
            <div className="mt-[14px] rounded-[14px] border border-border bg-card p-[18px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-3 text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                问题标签分布
              </div>
              {data.tags.length === 0 ? (
                <div className="py-2 text-[12px] text-muted-foreground">
                  还没有标注过问题标签（在「记录」页给效果图打标签后这里会出统计）
                </div>
              ) : (
                <div className="space-y-[8px]">
                  {data.tags.slice(0, 10).map((t) => {
                    const max = data.tags[0]?.count || 1;
                    return (
                      <div key={t.tag} className="flex items-center gap-2.5">
                        <span className="w-[110px] flex-none truncate text-[12px] font-semibold text-foreground">
                          {t.tag}
                        </span>
                        <div className="h-[7px] flex-1 overflow-hidden rounded-md bg-muted">
                          <div
                            className="h-full rounded-md bg-primary"
                            style={{ width: `${Math.round((t.count / max) * 100)}%` }}
                          />
                        </div>
                        <span className="w-8 flex-none text-right text-[12px] tabular-nums text-muted-foreground">
                          {t.count}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* 好图样本库 */}
            <div className="mt-[14px] rounded-[14px] border border-border bg-card p-[18px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-3 flex items-center justify-between">
                <div className="text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                  好图样本库
                </div>
                <div className="flex gap-[7px]">
                  <button className={chip(galleryFilter === "pass")} onClick={() => setGalleryFilter("pass")}>
                    评审通过
                  </button>
                  <button className={chip(galleryFilter === "best")} onClick={() => setGalleryFilter("best")}>
                    最佳图
                  </button>
                </div>
              </div>
              {gallery.length === 0 ? (
                <div className="rounded-xl border-[1.5px] border-dashed border-border-strong px-4 py-10 text-center text-[12.5px] text-muted-foreground">
                  还没有{galleryFilter === "pass" ? "评审通过" : "最佳"}的图 ·
                  到「记录」页给好图打上标注
                </div>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-[11px]">
                  {gallery.map((g) => (
                    <div key={`${g.record_id}-${g.result_id}`}>
                      <div className="relative aspect-[4/3] overflow-hidden rounded-[10px] border border-border">
                        {g.result_url ? (
                          <img
                            src={api.imgUrl(g.result_thumb || g.result_url)}
                            alt={g.material}
                            onClick={() => setZoom(api.imgUrl(g.result_url))}
                            className="absolute inset-0 h-full w-full cursor-zoom-in object-cover"
                          />
                        ) : (
                          <div className="flex h-full items-center justify-center bg-muted text-[11px] text-muted-foreground">
                            无图
                          </div>
                        )}
                        {g.best && (
                          <span className="absolute right-[7px] top-[7px] rounded-md bg-primary px-[7px] py-[2px] text-[10px] font-bold text-white">
                            最佳
                          </span>
                        )}
                      </div>
                      <div className="mt-1.5 truncate text-[12px] font-semibold text-foreground" title={g.material}>
                        {g.material}
                      </div>
                      <div className="truncate text-[11px] text-muted-foreground">
                        {[g.workflow_mode, g.room_type, g.style].filter(Boolean).join(" · ")}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
      <ImageZoom url={zoom} onClose={() => setZoom(null)} />
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-[14px] border border-border bg-card p-[18px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
      <div className="text-[30px] font-extrabold leading-none tracking-tight" style={{ color }}>
        {value}
      </div>
      <div className="mt-[9px] text-[12px] font-semibold text-muted-foreground">{label}</div>
    </div>
  );
}
