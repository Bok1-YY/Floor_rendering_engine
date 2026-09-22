"use client";
import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import type { JobView } from '@/lib/types';
import { useSessionStore } from '@/lib/editor/session-store';
import { JobCollection } from './job-collection';
export function useGenerationJobs() {
  const [collection] = useState(() => new JobCollection());
  const { state, store } = useSessionStore(() => ({ jobs: [] as JobView[], jobsLoaded: false }));
  const refreshJobs = useCallback(async () => {
    const run = collection.begin();
    try {
      const jobs = await api.listJobs(50);
      if (collection.accept(run, jobs)) store.set('jobs', collection.values());
    } finally { if (collection.valid(run)) store.set('jobsLoaded', true); }
  }, [collection, store]);
  useEffect(() => {
    collection.activate();
    let stopped = false; let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try { await refreshJobs(); } catch { /* Preserve the previous list on transient failure. */ }
      if (!stopped) timer = setTimeout(poll, 2500);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); collection.stop(); };
  }, [collection, refreshJobs]);
  function addJobs(jobs: JobView[]) { collection.add(jobs); if (collection.active) store.set('jobs', collection.values()); }
  function removeJob(id: string) { collection.remove(id); store.set('jobs', collection.values()); }
  const refresh = () => refreshJobs().catch(e => toast.error('刷新失败：' + (e as Error).message));
  async function clearCompleted() {
    try {
      const r = await api.clearCompleted(); if (!collection.active) return;
      collection.clearPendingCompleted(r.cleared_job_ids); store.set('jobs', collection.values());
      toast.success(`已清除 ${r.cleared} 个已完成任务卡；图片和历史记录均已保留`); await refreshJobs();
    } catch (e) { if (collection.active) toast.error((e as Error).message); }
  }
  async function cancelAll() {
    try { const r = await api.cancelAll(); if (!collection.active) return; toast.success(`已停止 ${r.stopped} 个`); await refreshJobs(); }
    catch (e) { if (collection.active) toast.error((e as Error).message); }
  }
  return { ...state, addJobs, removeJob, refreshJobs: refresh, clearCompleted, cancelAll };
}
