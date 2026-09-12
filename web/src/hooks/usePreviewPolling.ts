"use client";
import { useEffect, useRef, type Dispatch, type SetStateAction, type RefObject } from "react";
import { api } from "@/lib/api";
import type { PreviewView } from "@/lib/types";

export function usePreviewPolling(runRef: RefObject<number>, setPreview: Dispatch<SetStateAction<PreviewView | null>>) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const epoch = useRef(0);
  function stopPreviewPoll() {
    ++epoch.current;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }
  useEffect(() => () => { ++epoch.current; if (timer.current) clearTimeout(timer.current); }, []);
  function pollPreview(pid: string, run: number) {
    stopPreviewPoll();
    const generation = epoch.current;
    let fails = 0;
    const poll = async () => {
      try {
        const value = await api.previewStatus(pid);
        if (run !== runRef.current || generation !== epoch.current) return;
        fails = 0;
        setPreview(value);
        if (value.status === "done" || value.status === "failed") return;
      } catch {
        if (run !== runRef.current || generation !== epoch.current) return;
        if (++fails >= 3) {
          setPreview({ preview_id: pid, status: "failed", stage: "", url: "", thumb: "",
            error: "预览状态查询连续失败，已停止刷新；关闭后可在后端查看任务状态" });
          return;
        }
      }
      timer.current = setTimeout(poll, 1000);
    };
    timer.current = setTimeout(poll, 1000);
  }
  return { stopPreviewPoll, pollPreview };
}
