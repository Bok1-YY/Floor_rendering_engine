"use client";
import { useCallback, useEffect, useState } from 'react';
import { useJobStream } from '@/hooks/useJobStream';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { useSessionStore } from '@/lib/editor/session-store';
import { notifyJobEnd } from '@/lib/notify';
import type { JobView } from '@/lib/types';
import { JobSnapshot } from './snapshot';
export function useJobSnapshot(initial: JobView) {
  const [gate] = useState(() => new JobSnapshot(initial));
  const scope = useAsyncScope();
  const { state, store } = useSessionStore(() => ({ job: initial }));
  const applySnapshot = useCallback((next: JobView) => {
    if (!scope.valid(scope.token())) return false;
    const accepted = gate.accept(next); if (!accepted) return false;
    store.set('job', accepted.job);
    accepted.notifications.forEach(n => notifyJobEnd(n.status, n.name, n.error));
    return true;
  }, [gate, store, scope]);
  const job = state.job;
  const active = job.status === 'queued' || job.status === 'running' || job.pro_polishing || job.operation_status === 'running';
  useJobStream(active ? job.job_id : null, applySnapshot);
  useEffect(() => { let alive = true; queueMicrotask(() => { if (alive) applySnapshot(initial); }); return () => { alive = false; }; }, [initial, applySnapshot]);
  return { job, jobStore: store, active, applySnapshot };
}
