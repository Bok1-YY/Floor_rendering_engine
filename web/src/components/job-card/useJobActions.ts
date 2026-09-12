"use client";
import { useRef } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { reportCommitError } from '@/lib/result-commit';
import { useAsyncScope } from '@/lib/editor/async-scope';
import type { JobView } from '@/lib/types';
export function useJobActions(job: JobView, applySnapshot: (j: JobView) => boolean, invalidate: () => void, onRemove?: (id: string) => void) {
  const scope = useAsyncScope(), busy = useRef(false), cancelling = useRef(false);
  const ambiguousRuns = Object.values(job.model_runs || {}).filter(run => run?.retry_safety === 'ambiguous' && run?.recovery_action !== 'resume');
  const hasAmbiguousBilling = ambiguousRuns.length > 0 || (job.operation_retry_safety === 'ambiguous' && job.model_runs?.sd35?.recovery_action !== 'resume');
  async function act(fn: () => Promise<JobView>, message?: string, cancel = false) {
    const lock = cancel ? cancelling : busy; if (lock.current) return false;
    lock.current = true; const token = scope.token();
    try {
      const result = await fn(); if (!scope.valid(token)) return false;
      if (applySnapshot(result)) invalidate(); if (message) toast.success(message); return true;
    } catch (e) {
      if (scope.valid(token)) reportCommitError(e, result => { if (scope.valid(token) && result.job && applySnapshot(result.job)) invalidate(); });
      return false;
    } finally { lock.current = false; }
  }
  async function retryFailedRuns() {
    if (hasAmbiguousBilling && !window.confirm('上一次请求可能已经被模型接受或计费，但结果未能确认。再次生成可能产生重复费用，确认继续吗？')) return;
    return act(() => api.retryJob(job.job_id, hasAmbiguousBilling), hasAmbiguousBilling ? '已确认可能重复计费并重新生成' : '已重试');
  }
  async function retryUpscale() {
    const confirm = job.model_runs?.sd35?.recovery_action === 'confirm';
    if (confirm && !window.confirm('上一次超分可能已经计费。重新提交可能产生重复费用，确认继续吗？')) return;
    return act(() => api.retrySdUpscale(job.job_id, confirm), job.model_runs?.sd35?.recovery_action === 'resume' ? '正在恢复已有超分请求' : '已提交超分重试');
  }
  async function remove() {
    if (busy.current) return; busy.current = true; const token = scope.token();
    try { await api.deleteJob(job.job_id); if (scope.valid(token)) onRemove?.(job.job_id); }
    catch (e) { if (scope.valid(token)) toast.error((e as Error).message); }
    finally { busy.current = false; }
  }
  return {
    hasAmbiguousBilling, retryFailedRuns, retryUpscale, remove,
    cancel: () => act(() => api.cancelJob(job.job_id).then(() => api.getJob(job.job_id)), '已请求停止', true),
    restore: () => act(async () => { const result = await api.retryResultCommit(job.pending_result_commit!); return result.job || job; }, '本地结果已恢复写入，未再次调用模型'),
    polish: () => act(() => api.polishJob(job.job_id), '已提交磨缝'),
    regen: (n: number) => act(() => api.regenJob(job.job_id, n), `已开始重抽 ×${n}`),
    submitEdit: (instruction: string, color_match: boolean) => act(() => api.editJob(job.job_id, { instruction, color_match }), '已提交二改')
  };
}
