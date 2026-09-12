"use client";
import { toolBtn, REVIEW_STATUS, roomChip, reviewChip } from './presentation';

import { api } from "@/lib/api";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordFilters({
  active,
  roomFilter,
  reviewFilter,
  setRoomFilter,
  setReviewFilter,
  reload,
  roomCounts,
  download
}: Pick<RecordLibraryModel, "active" | "roomFilter" | "reviewFilter" | "setRoomFilter" | "setReviewFilter" | "reload" | "roomCounts" | "download">) {
  return <>             <div className="flex flex-none flex-wrap items-center justify-between gap-2.5 border-b border-border px-[22px] py-[14px]">
    <div className="flex min-w-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap gap-[7px]">
        <button
          onClick={() => setRoomFilter("__all__")}
          className={roomChip(roomFilter === "__all__")}
        >
          全部房间
        </button>
        {Object.entries(roomCounts).map(([rt, n]) => (
          <button
            key={rt}
            onClick={() => setRoomFilter(rt)}
            className={roomChip(roomFilter === rt)}
          >
            {rt} ({n})
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-[7px]">
        <button
          onClick={() => setReviewFilter("__all__")}
          className={reviewChip(reviewFilter === "__all__")}
        >
          全部评审
        </button>
        <button
          onClick={() => setReviewFilter("__best__")}
          className={reviewChip(reviewFilter === "__best__")}
        >
          最佳
        </button>
        {REVIEW_STATUS.map((s) => (
          <button
            key={s.value}
            onClick={() => setReviewFilter(s.value)}
            className={reviewChip(reviewFilter === s.value)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
    <div className="flex gap-2">
      <button onClick={reload} className={toolBtn}>
        刷新
      </button>
      <button onClick={() => download(api.exportHtmlUrl(active!))} className={toolBtn}>
        导出 HTML
      </button>
      <button onClick={() => download(api.exportPptxUrl(active!))} className={toolBtn}>
        导出 PPTX
      </button>
    </div>
  </div>

  </>;
}
