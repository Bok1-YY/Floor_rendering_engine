"use client";
import { useEffect } from "react";
import { API } from "@/lib/api";
import type { JobView } from "@/lib/types";

/**
 * 订阅某任务的 SSE 进度流。jobId 为 null 时不连接（用于终态任务不浪费连接）。
 * - 默认 message 事件：解析 JobView 调 onUpdate。
 * - 服务端 'done' 事件：更新一次后主动 close（避免 EventSource 在服务端关闭后自动重连成死循环）。
 * - 服务端 'error' 事件(带 data，如 job not found)：close；原生连接错误(无 data)不处理，交给 EventSource 自动重连自愈。
 */
export function useJobStream(
  jobId: string | null,
  onUpdate: (j: JobView) => void,
) {
  useEffect(() => {
    if (!jobId) return;
    const es = new EventSource(`${API}/api/jobs/${jobId}/stream`);

    const apply = (e: MessageEvent) => {
      try {
        onUpdate(JSON.parse(e.data) as JobView);
      } catch {
        /* 忽略坏帧 */
      }
    };

    es.onmessage = apply;
    es.addEventListener("done", (e) => {
      apply(e as MessageEvent);
      es.close();
    });
    es.addEventListener("error", (e) => {
      // 服务端显式 error 事件会带 data；原生断连错误无 data → 放任自动重连
      if ((e as MessageEvent).data) es.close();
    });

    return () => es.close();
  }, [jobId, onUpdate]);
}
