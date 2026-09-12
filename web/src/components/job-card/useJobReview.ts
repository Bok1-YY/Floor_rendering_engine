"use client";
import { useEffect, useRef } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useSessionStore } from '@/lib/editor/session-store';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { locateResult, targetKey, reviewPayload } from '@/lib/results/identity';
import type { JobView, RecordEntry, RecordResult, ReviewStatus } from '@/lib/types';
export function useJobReview(job: JobView, activeUrl: string) {
  const scope = useAsyncScope(), locked = useRef(false);
  const cache = useRef<{ rows?: RecordEntry[]; pending?: Promise<RecordEntry[]>; controller?: AbortController; epoch: number }>({ epoch: 0 });
  const { state, store } = useSessionStore(() => ({ results: {} as Record<string, RecordResult>, links: {} as Record<string, string>, reviewBusy: false, revision: 0 }));
  function invalidateReview() {
    cache.current.controller?.abort(); cache.current = { epoch: cache.current.epoch + 1 };
    store.patch({ results: {}, links: {}, revision: store.getSnapshot().revision + 1 });
  }
  const totals = JSON.stringify(Object.entries(job.model_runs || {}).map(([key, run]) => [key, run.total]));
  useEffect(() => {
    invalidateReview(); const restored = () => invalidateReview();
    window.addEventListener('floor-result-restored', restored);
    return () => { cache.current.controller?.abort(); window.removeEventListener('floor-result-restored', restored); };
    // Identity/count changes invalidate the per-card record cache; snapshot ticks do not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.json_path, job.record_id, totals]);
  async function resolve(url: string) {
    if (!url || !job.json_path || !job.record_id) return null;
    const owner = cache.current;
    if (!owner.rows) {
      if (!owner.pending) {
        owner.controller = new AbortController();
        owner.pending = api.loadRecord(job.json_path, owner.controller.signal).then(rows => { if (cache.current === owner) owner.rows = rows; return rows; }).finally(() => { owner.pending = undefined; });
      }
      await owner.pending;
    }
    if (cache.current !== owner) return null;
    const result = locateResult(owner.rows || [], job.record_id, url); if (!result) return null;
    const target = { jsonPath: job.json_path, recordId: job.record_id, resultId: result.result_id }, key = targetKey(target);
    return { target, key, result: store.getSnapshot().results[key] || result };
  }
  useEffect(() => {
    let alive = true; const token = scope.token();
    void resolve(activeUrl).then(found => {
      if (!alive || !scope.valid(token) || !found) return;
      store.patch({ results: { ...store.getSnapshot().results, [found.key]: found.result }, links: { ...store.getSnapshot().links, [activeUrl]: found.key } });
    }).catch(() => { });
    return () => { alive = false; };
    // resolve reads the current per-card cache. These values define its input identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeUrl, job.json_path, job.record_id, state.revision, scope, store]);
  async function modify(status?: ReviewStatus) {
    if (!activeUrl || locked.current) return;
    locked.current = true; store.set('reviewBusy', true); const token = scope.token(), epoch = cache.current.epoch, url = activeUrl;
    try {
      const found = await resolve(url);
      if (!scope.valid(token) || epoch !== cache.current.epoch) return;
      if (!found) throw new Error('当前候选尚未写入记录，请稍后再试');
      const { target, key, result } = found; let next: RecordResult;
      if (status) {
        const nextStatus = result.review_status === status ? 'unreviewed' : status;
        await api.reviewResult(reviewPayload(target, result, { review_status: nextStatus }));
        next = { ...result, review_status: nextStatus };
      } else {
        const response = await api.favoriteResult(target.jsonPath, target.recordId, target.resultId);
        next = { ...result, favorite: response.favorite };
      }
      if (!scope.valid(token)) return;
      if (epoch === cache.current.epoch) store.patch({ results: { ...store.getSnapshot().results, [key]: next }, links: { ...store.getSnapshot().links, [url]: key } });
      else invalidateReview();
      toast.success(status ? '评审已保存' : next.favorite ? '已收藏' : '已取消收藏');
    } catch (e) { if (scope.valid(token)) toast.error((e as Error).message); }
    finally { locked.current = false; if (scope.valid(token)) store.set('reviewBusy', false); }
  }
  const current = state.results[state.links[activeUrl]];
  return {
    activeReview: { status: current?.review_status || 'unreviewed', favorite: !!current?.favorite, best: !!current?.best }, reviewBusy: state.reviewBusy,
    setReviewStatus: (status: ReviewStatus) => modify(status), toggleFavorite: () => modify(), invalidateReview
  };
}
