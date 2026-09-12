import type { JobView, ModelKey } from "@/lib/types";
export type ColorMatchTarget =
  | { kind: "job"; jobId: string; stage: ModelKey }
  | { kind: "record"; jsonPath: string; recordId: string; resultId: string };
export type ColorMatchDialogProps = {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  srcUrl: string;
  imageRel: string;
  refUrl: string;
  refPath: string;
  target: ColorMatchTarget;
  onDone?: (jobView?: JobView) => void;
};

export function colorSessionKey(props: ColorMatchDialogProps): string {
  const target = props.target;
  const identity = target.kind === "job" ? [target.kind, target.jobId, target.stage]
    : [target.kind, target.jsonPath, target.recordId, target.resultId];
  return JSON.stringify([identity, props.srcUrl, props.imageRel, props.refPath, props.refUrl]);
}
