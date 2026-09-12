/* eslint-disable @next/next/no-img-element */
"use client";
import { resultToolBtn, REVIEW_STATUS } from './presentation';

import { api } from "@/lib/api";

import { cn } from "@/lib/utils";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordResultTile({
  setZoom,
  setPanoView,
  setCompare,
  setColorMatch,
  setInpaint,
  setFloorVisualize,
  setEdit,
  download,
  doDeleteResult,
  doFav,
  doReview,
  openReviewDialog,
  compareBeforeUrl,
  r,
  rid,
  res,
  j
}: { r: RecordLibraryModel['shownRecords'][number]; rid: string; res: RecordLibraryModel['shownRecords'][number]['results'][number]; j: number } & Pick<RecordLibraryModel, "setZoom" | "setPanoView" | "setCompare" | "setColorMatch" | "setInpaint" | "setFloorVisualize" | "setEdit" | "download" | "doDeleteResult" | "doFav" | "doReview" | "openReviewDialog" | "compareBeforeUrl">) {
  const resultId = res.result_id;
  const url = res.result_url || "";
  const thumb = res.result_thumb || url;
  const status = res.review_status || "unreviewed";
  const statusMeta =
    REVIEW_STATUS.find((s) => s.value === status) || REVIEW_STATUS[0];
  const panorama = res.generation_metadata?.panorama;
  const isPanoResult = Boolean(
    panorama?.projection === "equirectangular" || r.pano_audit?.projection === "equirectangular",
  );
  const openPano = () => setPanoView({
    url: api.imgUrl(url),
    label: `${rid} · ${res.model_label || "历史球面全景"}`,
    initialYawDeg: panorama?.viewer_initial_yaw_deg ?? 0,
  });
  return (
    <div key={resultId || j}>
      <div className={cn("relative overflow-hidden rounded-[10px] border border-border", isPanoResult ? "aspect-[2/1]" : "aspect-[4/3]")}>
        {url ? (
          <>
            <img
              src={api.imgUrl(thumb)}
              alt={res.model_label || "result"}
              onClick={() => isPanoResult ? openPano() : setZoom(api.imgUrl(url))}
              className="absolute inset-0 h-full w-full cursor-zoom-in object-cover"
            />
            {res.model_label && (
              <span className="absolute left-[7px] top-[7px] rounded-md bg-[rgba(26,24,21,.55)] px-[7px] py-[2px] text-[10px] font-bold text-white">
                {res.model_label}
              </span>
            )}
            {res.best && (
              <span className="absolute right-[7px] top-[7px] rounded-md bg-primary px-[7px] py-[2px] text-[10px] font-bold text-white">
                最佳
              </span>
            )}
          </>
        ) : (
          <div className="flex h-full items-center justify-center bg-muted text-[11px] text-muted-foreground">
            {res.has_inline ? "内联图(旧)" : "无图"}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-col gap-2 text-[11.5px] text-muted-foreground">
        <span className="truncate font-semibold">{res.model_label || "候选图"}</span>
        <span className="flex flex-wrap items-center gap-1.5">
          {!isPanoResult && <button
            title="收藏"
            onClick={() => doFav(rid, resultId)}
            className={cn(resultToolBtn, res.favorite && "border-primary/30 bg-primary-soft text-primary")}
          >
            {res.favorite ? "★ 已收藏" : "☆ 收藏"}
          </button>}
          {!isPanoResult && <button
            title="二改"
            onClick={() =>
              setEdit({ open: true, rid, resultId, instruction: "", colorMatch: true })
            }
            className={resultToolBtn}
          >
            ✎ 二改
          </button>}
          {!isPanoResult && url && url.startsWith("/outputs/") && (
            <button
              title="用原始小样像素重新投影地板（无生成模型费用）"
              onClick={() =>
                setFloorVisualize({
                  open: true,
                  srcUrl: url,
                  textureUrl: r.gen_context?.image_url || "",
                  texturePath: r.gen_context?.image_path || "",
                  recordId: rid,
                  resultId,
                })
              }
              disabled={!r.gen_context?.image_path}
              className={cn(resultToolBtn, "disabled:hidden")}
            >
              🪵 贴地板
            </button>
          )}
          {!isPanoResult && url && url.startsWith("/outputs/") && (
            <button
              title="生成式修补（画笔涂抹移除/添加物体）"
              onClick={() =>
                setInpaint({ open: true, srcUrl: url, recordId: rid, resultId })
              }
              className={resultToolBtn}
            >
              🖌️ 智能修补
            </button>
          )}
          {!isPanoResult && url && url.startsWith("/outputs/") && (
            <button
              title="手动校色（以地板小样为参照，框选地板区域）"
              onClick={() =>
                setColorMatch({
                  open: true,
                  srcUrl: url,
                  imageRel: url.slice("/outputs/".length),
                  refUrl: r.color_match_ref_url || "",
                  refPath: r.color_match_ref_path || "",
                  recordId: rid,
                  resultId,
                })
              }
              className={resultToolBtn}
            >
              🎯 校色
            </button>
          )}
          {!isPanoResult && url && compareBeforeUrl(r) && (
            <button
              title="前后对比"
              onClick={() =>
                setCompare({
                  before: compareBeforeUrl(r),
                  after: url,
                })
              }
              className={resultToolBtn}
            >
              ⇔ 对比
            </button>
          )}
          {isPanoResult && url && <button title="历史 360° 只读查看" onClick={openPano} className={resultToolBtn}>◉ 360°查看</button>}
          {url && (
            <button
              title="下载"
              onClick={() => download(api.imgUrl(url))}
              className={resultToolBtn}
            >
              ↓ 下载
            </button>
          )}
          {!isPanoResult && <button
            title="删除"
            onClick={() => doDeleteResult(rid, resultId)}
            className={cn(resultToolBtn, "hover:bg-destructive-soft hover:text-destructive")}
          >
            删除
          </button>}
        </span>
      </div>
      {!isPanoResult && <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {REVIEW_STATUS.slice(1).map((s) => (
          <button
            key={s.value}
            onClick={() =>
              doReview(
                rid,
                resultId,
                { status: status === s.value ? "unreviewed" : s.value },
                res,
              )
            }
            className={cn(
              "h-6 rounded-md border px-2 text-[11px] font-semibold",
              status === s.value
                ? "border-transparent text-white"
                : "border-border bg-card text-secondary-foreground hover:bg-accent",
            )}
            style={status === s.value ? { background: s.color } : undefined}
          >
            {s.label}
          </button>
        ))}
        <button
          onClick={() =>
            doReview(rid, resultId, { best: !res.best }, res)
          }
          className={cn(
            "h-6 rounded-md border px-2 text-[11px] font-semibold",
            res.best
              ? "border-primary bg-primary text-white"
              : "border-border bg-card text-secondary-foreground hover:bg-accent",
          )}
        >
          最佳
        </button>
        <button
          onClick={() => openReviewDialog(rid, resultId, res)}
          className="h-6 rounded-md border border-border bg-card px-2 text-[11px] font-semibold text-secondary-foreground hover:bg-accent"
        >
          标注
        </button>
      </div>}
      <div className="mt-1 flex items-center gap-1.5 text-[11px]">
        <span style={{ color: statusMeta.color }} className="font-bold">
          {statusMeta.label}
        </span>
        {(res.review_tags || []).length > 0 && (
          <span className="truncate text-muted-foreground">
            {(res.review_tags || []).join("、")}
          </span>
        )}
      </div>
      {res.review_note ? (
        <div className="mt-1 text-[11px] leading-snug text-success">
          评审：{res.review_note}
        </div>
      ) : null}
      {res.comment ? (
        <div className="mt-1 text-[11px] leading-snug text-muted-foreground">
          💬 {res.comment}
        </div>
      ) : null}
    </div>
  );
}
