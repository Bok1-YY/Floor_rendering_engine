"use client";
import { RecordResultTile } from './RecordResultTile';

import { toast } from "sonner";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordGroup({
  setZoom,
  setPanoView,
  setCompare,
  setColorMatch,
  setInpaint,
  setFloorVisualize,
  setReveal,
  setEdit,
  download,
  doDeleteResult,
  doFav,
  doReview,
  openReviewDialog,
  compareBeforeUrl,
  doReuse,
  doDeleteRecord,
  r,
  i
}: { r: RecordLibraryModel['shownRecords'][number]; i: number } & Pick<RecordLibraryModel, "setZoom" | "setPanoView" | "setCompare" | "setColorMatch" | "setInpaint" | "setFloorVisualize" | "setReveal" | "setEdit" | "download" | "doDeleteResult" | "doFav" | "doReview" | "openReviewDialog" | "compareBeforeUrl" | "doReuse" | "doDeleteRecord">) {
  const rid = r.id || "";
  return (
    <div
      key={rid || i}
      className="rounded-[14px] border border-border bg-card p-[15px] shadow-[0_2px_8px_rgba(120,90,60,.05)]"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <span className="min-w-0 flex-1 break-words text-[13.5px] font-bold leading-snug text-foreground">
          {rid || `记录 ${i + 1}`}
          {r.room_type ? ` · ${r.room_type}` : ""}
          {r.workflow_mode ? ` · ${String(r.workflow_mode)}` : ""}
        </span>
        <div className="flex flex-none flex-wrap justify-end gap-1.5 text-muted-foreground">
          {(r.gen_context?.params || (r.user_prompt && r.gen_context?.free_image_paths?.length)) && (
            <button
              title="用这套参数再生成"
              onClick={() => doReuse(r)}
              className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold hover:bg-accent hover:text-accent-foreground"
            >
              ⟳ 复用
            </button>
          )}
          {!r.user_prompt && (
            <button
              title="解密提示词"
              onClick={() => setReveal({ open: true, rid, pw: "", text: "" })}
              className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold hover:bg-accent hover:text-foreground"
            >
              🔑 提示词
            </button>
          )}
          <button
            title="删除记录"
            onClick={() => doDeleteRecord(rid)}
            className="h-[30px] rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold hover:bg-destructive-soft hover:text-destructive"
          >
            删除
          </button>
        </div>
      </div>

      {r.user_prompt && (
        <div className="mb-3 rounded-[10px] border border-border bg-panel px-3 py-2.5">
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <span className="text-[11px] font-bold text-secondary-foreground">自由指令词</span>
            <button
              onClick={() => {
                navigator.clipboard.writeText(r.user_prompt || "");
                toast.success("指令词已复制");
              }}
              className="text-[11px] font-semibold text-muted-foreground hover:text-foreground"
            >
              复制
            </button>
          </div>
          <p className="whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-secondary-foreground">
            {r.user_prompt}
          </p>
        </div>
      )}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-[14px]">
        {(r.results || []).map((res, j) => (<RecordResultTile
          key={res.result_id || j}

          setZoom={setZoom}
          setPanoView={setPanoView}
          setCompare={setCompare}
          setColorMatch={setColorMatch}
          setInpaint={setInpaint}
          setFloorVisualize={setFloorVisualize}
          setEdit={setEdit}

          download={download}
          doDeleteResult={doDeleteResult}
          doFav={doFav}
          doReview={doReview}
          openReviewDialog={openReviewDialog}
          compareBeforeUrl={compareBeforeUrl}
          r={r}
          rid={rid}
          res={res}
          j={j}
        />))}
      </div>
    </div>
  );
}
