"use client";
import { useEffect, useState, useRef } from 'react';
import type { SetStateAction } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useSessionStore } from '@/lib/editor/session-store';
import { useAsyncScope } from '@/lib/editor/async-scope';
import type { JobView, ModelKey } from '@/lib/types';
import { CandidateCache, type Candidate } from './candidate-cache';
export function useJobCandidates(job: JobView) {
  const scope = useAsyncScope();
  const { state, store } = useSessionStore(() => ({ activeModel: job.model_targets?.[0] || 'b2', view: {} as Partial<Record<ModelKey, Candidate>>, cacheRevision: 0, resetRevision: 0 }));
  const [cache] = useState(() => new CandidateCache((model, idx, signal) => api.jobResult(job.job_id, model, idx, signal), () => store.set('cacheRevision', n => n + 1)));
  const intent = useRef<Partial<Record<ModelKey, number>>>({});
  const sequence = useRef<Partial<Record<ModelKey, number>>>({});
  const totals = JSON.stringify(Object.fromEntries(Object.entries(job.model_runs || {}).map(([key, run]) => [key, run.total])));
  const priorTotals = useRef(totals);
  const slots = (job.model_targets || ['b2', 'pro'] as ModelKey[]).flatMap(key => {
    const run = job.model_runs?.[key]; if (!run?.url) return [];
    return [{ key, name: run.label, url: state.view[key]?.url ?? run.url, thumb: state.view[key]?.thumb ?? run.thumb, idx: state.view[key]?.idx ?? run.idx, total: run.total, run }];
  });
  const activeSlot = slots.find(s => s.key === state.activeModel) || slots[0];
  const activeKey = activeSlot?.key, activeTotal = activeSlot?.total || 0;
  function reset(clearCache = true) {
    intent.current = {}; sequence.current = {}; scope.invalidate();
    if (clearCache) cache.invalidate();
    store.patch({ view: {}, resetRevision: store.getSnapshot().resetRevision + 1 });
  }
  useEffect(() => { cache.activate(); return () => cache.stop(); }, [cache]);
  useEffect(() => {
    if (priorTotals.current !== totals) {
      const old = JSON.parse(priorTotals.current) as Record<string, number>, next = JSON.parse(totals) as Record<string, number>;
      priorTotals.current = totals;
      reset(Object.keys(old).some(key => (next[key] || 0) < old[key]));
    }
    // reset reads only stable stores and resources.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totals]);
  const serverIndex = activeSlot?.run.idx || 0;
  useEffect(() => { if (activeKey && activeTotal) cache.load(activeKey, activeTotal, serverIndex); }, [cache, activeKey, activeTotal, state.resetRevision, serverIndex]);
  function setView(action: SetStateAction<typeof state.view>) {
    scope.invalidate(); intent.current = {}; sequence.current = {}; store.set('view', action);
  }
  async function nav(model: ModelKey, delta: number) {
    const run = job.model_runs?.[model]; if (!run || run.total <= 1) return;
    const next = Math.max(0, Math.min((intent.current[model] ?? store.getSnapshot().view[model]?.idx ?? run.idx) + delta, run.total - 1));
    intent.current[model] = next;
    const seq = (sequence.current[model] || 0) + 1; sequence.current[model] = seq; const token = scope.token();
    try { const candidate = await cache.read(model, next); if (candidate && scope.valid(token) && sequence.current[model] === seq) store.set('view', v => ({ ...v, [model]: candidate })); }
    catch (e) { if (scope.valid(token) && sequence.current[model] === seq) toast.error((e as Error).message); }
  }
  return {
    ...state, slots, activeSlot, activeKey, activeUrl: activeSlot?.url || '', activeCandidates: activeKey ? cache.values(activeKey) : [],
    setActiveModel: (key: ModelKey) => store.set('activeModel', key), setView, nav, resetCandidates: reset
  };
}
export type JobCandidates = ReturnType<typeof useJobCandidates>;
