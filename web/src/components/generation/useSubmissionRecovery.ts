"use client";
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { API } from '@/lib/api';
import type { JobView } from '@/lib/types';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { useSessionStore } from '@/lib/editor/session-store';
import { checkIntent, limitedMap, sendIntent, submissionStore } from './submission-client';
import { listIntents, reserveIntents, resolved, updateIntent, type Intent } from './submission-store';

export function useSubmissionRecovery(addJobs: (jobs: JobView[]) => void) {
  const scope = useAsyncScope(), callbacks = useRef({ addJobs });
  useEffect(() => { callbacks.current = { addJobs }; }, [addJobs]);
  const { state, store } = useSessionStore(() => ({ intents: [] as Intent[], recoveryError: '', recoveryBusy: false, showHidden: false }));
  const refreshing = useRef(false), sending = useRef(new Set<string>());
  async function reload() {
    const token = scope.token(), rows = await listIntents(API);
    if (scope.valid(token)) store.set('intents', rows);
    return rows;
  }
  async function refresh(signal?: AbortSignal) {
    if (refreshing.current) return;
    refreshing.current = true;
    const token = scope.token();
    try {
      const rows = await listIntents(API);
      if (!scope.valid(token)) return;
      const instance = await submissionStore();
      const results = await limitedMap(rows.filter(row => !resolved(row) && row.store === instance), async row => {
        if (signal?.aborted || !scope.valid(token)) return;
        const result = await checkIntent(row, signal);
        if (result.job && scope.valid(token)) callbacks.current.addJobs([result.job]);
      });
      const failed = results.find(result => result.status === 'rejected');
      if (scope.valid(token)) store.set('recoveryError', failed?.status === 'rejected' ? String(failed.reason?.message || failed.reason)
        : rows.some(row => !resolved(row) && row.store !== instance) ? '部分提交属于另一套数据目录，已隔离保存，不能补发到当前服务' : '');
      await reload();
    } catch (error) { if (scope.valid(token)) store.set('recoveryError', (error as Error).message); }
    finally { refreshing.current = false; }
  }
  useEffect(() => {
    let stopped = false, ticking = false, delay = 1000, timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const tick = async () => {
      if (stopped || ticking) return;
      ticking = true;
      if (!document.hidden) await refresh(controller.signal);
      ticking = false;
      if (!stopped) { timer = setTimeout(tick, delay); delay = Math.min(delay * 2, 30000); }
    };
    const wake = () => { if (!document.hidden && !stopped) { delay = 1000; clearTimeout(timer); void tick(); } };
    void tick();
    window.addEventListener('online', wake); window.addEventListener('focus', wake); document.addEventListener('visibilitychange', wake);
    return () => { stopped = true; controller.abort(); clearTimeout(timer); window.removeEventListener('online', wake); window.removeEventListener('focus', wake); document.removeEventListener('visibilitychange', wake); };
    // The session store and callbacks ref outlive renders; the effect owns its timers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function continueIntent(row: Intent) {
    if (sending.current.has(row.id)) return;
    sending.current.add(row.id); store.set('recoveryBusy', true);
    const token = scope.token();
    try {
      const result = await sendIntent(row, () => scope.valid(token));
      if (result.job && scope.valid(token)) callbacks.current.addJobs([result.job]);
      await reload();
    } catch (error) { if (scope.valid(token)) toast.error((error as Error).message); await reload().catch(() => {}); }
    finally { sending.current.delete(row.id); if (scope.valid(token)) store.set('recoveryBusy', sending.current.size > 0); }
  }
  async function another(row: Intent) {
    if (!row.payload || !window.confirm('旧提交可能已经开始。确定使用相同参数另外创建一次任务吗？可能产生额外费用。')) return;
    try {
      const instance = await submissionStore();
      if (instance !== row.store) throw new Error('请先切回原数据目录，或在表单重新选择素材');
      const [next] = await reserveIntents([{ backend: row.backend, store: row.store, kind: row.kind,
        name: row.name, payload: row.payload, analyze: row.analyze }], true);
      await continueIntent(next.row);
    } catch (error) { toast.error((error as Error).message); }
  }
  async function hide(row: Intent) {
    try { await updateIntent(row.id, { hidden: !row.hidden }); await reload(); }
    catch (error) { toast.error((error as Error).message); }
  }
  async function locate(row: Intent) {
    const token = scope.token();
    try {
      const found = await checkIntent(row);
      if (found.job && scope.valid(token)) {
        callbacks.current.addJobs([found.job]);
        scope.frame(() => document.getElementById(`job-${found.job!.job_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
      }
      await reload();
    } catch (error) { if (scope.valid(token)) toast.error((error as Error).message); }
  }
  return { ...state, reload, refreshSubmissions: () => refresh(), continueIntent, another, hide, locate,
    setShowHidden: (value: boolean) => store.set('showHidden', value) };
}
