"use client";
import { useRef } from 'react';
import type { SetStateAction } from 'react';
import { api, API } from '@/lib/api';
import { toast } from 'sonner';
import { requestNotifyPermission } from '@/lib/notify';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { useSessionStore } from '@/lib/editor/session-store';
import type { JobView, Swatch } from '@/lib/types';
import type { GenerationState } from './useGenerationState';
import { freePayload, jobPayload, validateGeneration } from './rules';
import { limitedMap, sendIntent, submissionStore } from './submission-client';
import { randomId, reserveIntents, resolved, type Intent } from './submission-store';
import { useSubmissionRecovery } from './useSubmissionRecovery';

export function useGenerationSubmit({ store }: GenerationState, addJobs: (jobs: JobView[]) => void) {
  const scope = useAsyncScope(); const singleLock = useRef(false); const batchLock = useRef(false); const epoch = useRef(0);
  const recovery = useSubmissionRecovery(addJobs);
  const batchIntents = useRef(new Map<string, Intent>());
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
      const instance = await submissionStore(), free = snapshot.params.workflow_mode.includes('自由创作');
      if (!scope.valid(token)) return;
      const [intent] = await reserveIntents([{ backend: API, store: instance, kind: free ? 'free' : 'job',
        name: free ? '自由创作' : `${snapshot.floor?.name || '地板'} · ${snapshot.params.cn_mode ? snapshot.params.cn_room_type : snapshot.params.room_type}`,
        payload: free ? freePayload(snapshot) : jobPayload(snapshot) }]);
      if (intent.reused) { if (scope.valid(token)) toast.info('已有相同内容的待确认提交，请在提交恢复区域继续'); return; }
      const result = await sendIntent(intent.row, () => scope.valid(token));
      if (result.job && scope.valid(token)) { addJobs([result.job]); toast.success('任务已提交'); }
    } catch (error) { if (scope.valid(token)) toast.error('提交未完成：' + (error as Error).message); }
    finally {
      singleLock.current = false;
      if (scope.valid(token)) { submission.set('submitting', false); await recovery.reload().catch(() => {}); }
    }
  }
  function openBatch() {
    if (batchLock.current) return;
    batchIntents.current.clear();
    const s = store.getSnapshot(), version = ++epoch.current, token = scope.token();
    submission.patch({ batchOpen: true, batchTab: s.params.workflow_mode.includes('Omakase') ? 'floors' : 'rooms', batchFloors: s.floor ? [s.floor] : [], batchNotice: '' });
    api.recentSwatches(24).then(list => { if (scope.valid(token) && version === epoch.current) submission.set('recentFloors', list); }).catch(() => {});
  }
  async function run(kind: 'rooms' | 'floors') {
    if (batchLock.current) return;
    const snapshot = structuredClone(store.getSnapshot()), selection = structuredClone(submission.getSnapshot());
    const error = validateGeneration(snapshot, kind); if (error) { toast.warning(error); return; }
    const items = kind === 'rooms' ? selection.batchRooms.map(room => ({ key: `room:${room}`, name: room, room, floor: snapshot.floor! }))
      : selection.batchFloors.map(floor => ({ key: `floor:${floor.path}`, name: floor.name, room: '', floor }));
    if (!items.length) { toast.warning(kind === 'rooms' ? '请至少勾选一个房间类型' : '请至少选择一块地板'); return; }
    batchLock.current = true; submission.patch({ batchSubmitting: true, batchNotice: '' }); requestNotifyPermission();
    const token = scope.token();
    try {
      const instance = await submissionStore(), batch = randomId();
      if (!scope.valid(token)) return;
      const newItems = items.filter(item => !batchIntents.current.has(`${kind}:${item.key}`));
      const reserved = await reserveIntents(newItems.map(item => {
        const params = kind === 'rooms' ? snapshot.params.cn_mode ? { ...snapshot.params, cn_room_type: item.room } : { ...snapshot.params, room_type: item.room } : snapshot.params;
        return { backend: API, store: instance, kind: 'job' as const, name: item.name, batch, item: item.key,
          payload: jobPayload(snapshot, item.floor, params), analyze: kind === 'floors' ? item.floor.path : undefined };
      }));
      reserved.forEach((intent, index) => batchIntents.current.set(`${kind}:${newItems[index].key}`, intent.row));
      const intents = items.map(item => batchIntents.current.get(`${kind}:${item.key}`)!);
      const settled = await limitedMap(intents, intent => sendIntent(intent, () => scope.valid(token)));
      if (!scope.valid(token)) return;
      const created: JobView[] = [], remaining = items.filter((_item, index) => {
        const result = settled[index];
        if (result.status === 'fulfilled' && resolved(result.value.row)) {
          if (result.value.job) created.push(result.value.job);
          return false;
        }
        return true;
      });
      if (created.length) addJobs(created.reverse());
      submission.patch(kind === 'rooms' ? { batchRooms: remaining.map(item => item.room) } : { batchFloors: remaining.map(item => item.floor) });
      if (remaining.length) {
        const notice = `已提交 ${items.length - remaining.length} 个；未完成：${remaining.map(item => item.name).join('、')}。提交结果未确认或被拒绝的条目已保存，请在提交恢复区域查询。已成功项已移出选择。`;
        submission.set('batchNotice', notice); toast.error(notice);
      } else { submission.set('batchOpen', false); toast.success(`已批量提交 ${items.length} 个任务`); }
    } catch (error) {
      if (scope.valid(token)) { const notice = (error as Error).message; submission.set('batchNotice', notice); toast.error(notice); }
    } finally {
      batchLock.current = false;
      if (scope.valid(token)) { submission.set('batchSubmitting', false); await recovery.reload().catch(() => {}); }
    }
  }
  return {
    ...state, recovery, generate, openBatch, setBatchOpen, runBatch: () => run('rooms'), runBatchFloors: () => run('floors'),
    setBatchTab: field('batchTab'), setBatchRooms: field('batchRooms'), setBatchFloors: field('batchFloors')
  };
}
