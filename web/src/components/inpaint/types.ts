import type { JobView, ModelKey, InpaintTargetPayload } from "@/lib/types";
export type InpaintTarget =
  | { kind: "job"; jobId: string; stage: ModelKey; imageRel: string }
  | { kind: "record"; jsonPath: string; recordId: string; resultId: string }
  | { kind: "room"; roomPath: string };
export type InpaintDialogProps = {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  srcUrl: string; // 待修补图的相对 URL（/outputs/.. 或 /uploads/..）
  target: InpaintTarget;
  /** job 目标：apply 后带回任务快照；record 目标：无参调用（外层刷新） */
  onDone?: (jobView?: JobView) => void;
  /** room 目标专用：apply 后回填新房间图 */
  onRoomCleaned?: (path: string, url: string, thumb: string) => void;
};
export function toTargetPayload(t: InpaintTarget): InpaintTargetPayload {
  if (t.kind === "job") return { kind: "job", jid: t.jobId, stage: t.stage, image_rel: t.imageRel };
  if (t.kind === "record")
    return { kind: "record", json_path: t.jsonPath, record_id: t.recordId, result_id: t.resultId };
  return { kind: "room", room_path: t.roomPath };
}
export const PURE_ERASERS = new Set(["bria-eraser", "finegrain-eraser", "lama"]);

export function inpaintSessionKey(props: InpaintDialogProps): string {
  const target = props.target;
  const identity = target.kind === "job" ? [target.kind, target.jobId, target.stage, target.imageRel]
    : target.kind === "record" ? [target.kind, target.jsonPath, target.recordId, target.resultId]
    : [target.kind, target.roomPath];
  return JSON.stringify([identity, props.srcUrl]);
}
