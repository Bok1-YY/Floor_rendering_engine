"use client";
import { inpaintSessionKey, type InpaintDialogProps } from "./types";
export type { InpaintTarget } from "./types";
import { useInpaintSession } from "./useInpaintSession";
import { InpaintView } from "./InpaintView";
export function InpaintDialog(props: InpaintDialogProps) { if (!props.open) return null; return <Session key={inpaintSessionKey(props)} {...props} />; }
function Session(props: InpaintDialogProps) { return <InpaintView {...useInpaintSession(props)} />; }
