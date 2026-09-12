"use client";
import { useRef } from 'react';
import type { SetStateAction } from 'react';
import { api, ApiError } from '@/lib/api';
import { toast } from 'sonner';
import { requestNotifyPermission } from '@/lib/notify';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { useSessionStore } from '@/lib/editor/session-store';
import type { JobView, Swatch } from '@/lib/types';
import type { GenerationState } from './useGenerationState';
import { freePayload, jobPayload, validateGeneration } from './rules';

export function useGenerationSubmit({ store }: GenerationState, addJobs: (jobs: JobView[]) => void) {
  const scope = useAsyncScope(); const singleLock = useRef(false); const batchLock = useRef(false); const epoch = useRef(0);
  const uncertain = useRef(new Set<string>());
  const { state, store: submission } = useSessionStore(() => ({
    submitting: false, batchSubmitting: false,
    batchOpen: false, batchTab: 'rooms' as 'rooms' | 'floors', batchRooms: [] as string[], batchFloors: [] as Swatch[], recentFloors: [] as Swatch[], batchNotice: ''
  }));
  const field = <K extends keyof typeof state>(key: K) => (value: SetStateAction<(typeof state)[K]>) => { if (!batchLock.current) submission.set(key, value); };
  function setBatchOpen(open: boolean) { if (open && batchLock.current) return; ++epoch.current; submission.set('batchOpen', open); }
  async function generate() {
    if (singleLock.current) return;
    const snapshot = structuredClone(store.getSnapshot());
    const error = validateGeneration(snapshot, 'single'); if (error) { toast.warning(error); return; }
    singleLock.current = true; submission.set('submitting', true); requestNotifyPermission(); const token = scope.token();
    try {
      const job = snapshot.params.workflow_mode.includes('自由创作') ? await api.createFreeJob(freePayload(snapshot)) : await api.createJob(jobPayload(snapshot));
      if (scope.valid(token)) { addJobs([job]); toast.success('任务已提交'); }
    } catch (e) { if (scope.valid(token)) toast.error('提交失败：' + (e as Error).message); }
    finally { singleLock.current = false; if (scope.valid(token)) submission.set('submitting', false); }
  }
  function openBatch() {
    if (batchLock.current) return;
    const s = store.getSnapshot(), version = ++epoch.current, token = scope.token();
    submission.patch({ batchOpen: true, batchTab: s.params.workflow_mode.includes('Omakase') ? 'floors' : 'rooms', batchFloors: s.floor ? [s.floor] : [], batchNotice: '' });
    api.recentSwatches(24).then(list => { if (scope.valid(token) && version === epoch.current) submission.set('recentFloors', list); }).catch(() => { });
  }
  async function run(kind: 'rooms' | 'floors') {
    if (batchLock.current) return;
    const snapshot = structuredClone(store.getSnapshot());
    const selection = structuredClone(submission.getSnapshot());
    const error = validateGeneration(snapshot, kind); if (error) { toast.warning(error); return; }
    const items = kind === 'rooms' ? selection.batchRooms.map(room => ({ key: `room:${room}`, name: room, room, floor: snapshot.floor! }))
      : selection.batchFloors.map(floor => ({ key: `floor:${floor.path}`, name: floor.name, room: '', floor }));
    if (!items.length) { toast.warning(kind === 'rooms' ? '请至少勾选一个房间类型' : '请至少选择一块地板'); return; }
    if (items.some(item => uncertain.current.has(item.key)) && !window.confirm('部分条目的提交结果未确认，后端可能已创建任务。请先核对任务列表。仍要再次提交这些条目吗？')) return;
    batchLock.current = true; submission.patch({ batchSubmitting: true, batchNotice: '' }); requestNotifyPermission();
    const token = scope.token();
    try {
      const settled = await Promise.allSettled(items.map(async item => {
        let params = snapshot.params;
        if (kind === 'rooms') params = snapshot.params.cn_mode ? { ...params, cn_room_type: item.room } : { ...params, room_type: item.room };
        else {
          let tone = params.floor_tone;
          try { tone = (await api.floorAnalyze(item.floor.path)).tone || tone; } catch { /* Retain the established tone fallback. */ }
          // Leaving the page before analysis completes must not start a new paid task.
          if (!scope.valid(token)) throw new Error('页面已离开，未提交');
          params = { ...params, floor_tone: tone };
        }
        return api.createJob(jobPayload(snapshot, item.floor, params));
      }));
      if (!scope.valid(token)) return;
      const created: JobView[] = [], failed = new Set<string>(), unknown: string[] = [];
      settled.forEach((result, index) => {
        const item = items[index];
        if (result.status === 'fulfilled') { created.push(result.value); uncertain.current.delete(item.key); }
        else {
          failed.add(item.key);
          const e = result.reason;
          const definite = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408;
          if (!definite) { uncertain.current.add(item.key); unknown.push(item.name); } else uncertain.current.delete(item.key);
        }
      });
      if (created.length) addJobs(created.reverse());
      const remaining = items.filter(item => failed.has(item.key));
      submission.patch(kind === 'rooms' ? { batchRooms: remaining.map(item => item.room) } : { batchFloors: remaining.map(item => item.floor) });
      if (remaining.length) {
        const notice = `已提交 ${created.length} 个；未成功：${remaining.map(item => item.name).join('、')}`
          + (unknown.length ? `。提交结果未确认：${unknown.join('、')}，请先核对任务列表，再决定是否重新提交。` : '。已成功项已移出选择。');
        submission.set('batchNotice', notice); toast.error(notice);
      } else { submission.set('batchOpen', false); toast.success(`已批量提交 ${created.length} 个任务`); }
    } finally { batchLock.current = false; if (scope.valid(token)) submission.set('batchSubmitting', false); }
  }
  return {
    ...state, generate, openBatch, setBatchOpen, runBatch: () => run('rooms'), runBatchFloors: () => run('floors'),
    setBatchTab: field('batchTab'), setBatchRooms: field('batchRooms'), setBatchFloors: field('batchFloors')
  };
}
