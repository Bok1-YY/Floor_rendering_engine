"use client";

import { api } from "@/lib/api";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Segmented, Pill } from "@/components/dc-ui";

import type { GenerationWorkspaceModel } from "./useGenerationWorkspace";
export function BatchDialog({
  modelTargets,
  params,
  batchSubmitting,
  batchOpen,
  batchTab,
  batchRooms,
  batchFloors,
  recentFloors,
  batchNotice,
  setBatchOpen,
  runBatch,
  runBatchFloors,
  setBatchTab,
  setBatchRooms,
  setBatchFloors,
  batchRoomOptions
}: Pick<GenerationWorkspaceModel, "options" | "modelTargets" | "params" | "batchSubmitting" | "batchOpen" | "batchTab" | "batchRooms" | "batchFloors" | "recentFloors" | "batchNotice" | "setBatchOpen" | "runBatch" | "runBatchFloors" | "setBatchTab" | "setBatchRooms" | "setBatchFloors" | "batchRoomOptions">) {
  return <>      {/* 批量：多房间 × 同地板 ｜ 多地板 × 同场景 */}
    <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
      <DialogContent className="max-h-[90vh] overflow-y-auto max-w-[min(92vw,560px)] rounded-[18px]">
        <div className="space-y-4">
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
            <div>
              <div className="mb-2 text-[11.5px] font-semibold text-muted-foreground">房间类型 · 已选 {batchRooms.length}</div>
              <div className="flex flex-wrap gap-[7px]">
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
            </div>
          ) : (
            <div className="max-h-[320px] overflow-y-auto pt-1">
              {recentFloors.length === 0 ? (
                <div className="py-6 text-center text-[12.5px] text-muted-foreground">
                  没有最近小样 · 请先在生成页上传地板图
                </div>
              ) : (
                <div className="grid grid-cols-5 gap-[9px]">
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

          <div className="flex items-center justify-between">
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
            <span className="text-[11.5px] text-muted-foreground">
              当前已选 {batchTab === "rooms" ? batchRooms.length : batchFloors.length} 项
            </span>
          </div>

          {batchNotice && <p role="status" className="text-sm text-destructive">{batchNotice}</p>}
          <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
            <div className="min-w-0 text-[11.5px] leading-relaxed text-muted-foreground">
              将提交 {batchTab === "rooms" ? batchRooms.length : batchFloors.length} 个任务 × {modelTargets.length} 模型 = {(batchTab === "rooms" ? batchRooms.length : batchFloors.length) * modelTargets.length} 张起
            </div>
            <div className="flex flex-none gap-2">
              <button
                onClick={() => setBatchOpen(false)}
                className="h-[38px] rounded-[10px] border border-border bg-card px-4 text-[13px] font-semibold text-secondary-foreground hover:bg-accent"
              >
                取消
              </button>
              <button
                onClick={batchTab === "rooms" ? runBatch : runBatchFloors}
                disabled={
                  (batchTab === "rooms"
                    ? batchRooms.length === 0
                    : batchFloors.length === 0) || batchSubmitting
                }
                className="h-[38px] rounded-[10px] bg-primary px-5 text-[13.5px] font-bold text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
              >
                {batchSubmitting ? "提交中…" : "提交批量"}
              </button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>

  </>;
}
