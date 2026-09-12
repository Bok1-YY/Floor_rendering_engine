"use client";
import { useEffect, useRef } from 'react';
import { api } from '@/lib/api';
import { reportCommitError } from '@/lib/result-commit';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { useSessionStore } from '@/lib/editor/session-store';
import type { InpaintCandidate, InpaintStatusView } from '@/lib/types';
import { toast } from 'sonner';
import { toTargetPayload, type InpaintDialogProps } from './types';
import type { InpaintSessionState } from './useInpaintState';

type TaskState = {
  phase: 'draw' | 'submitting' | 'running' | 'pick' | 'applying'; id: string; stage: string;
  candidates: InpaintCandidate[]; selected: number; partialNote: string
};

export function useInpaintTask(props: InpaintDialogProps, options: InpaintSessionState['store'], exportMask: () => string | null) {
  const { state, store } = useSessionStore<TaskState>(() => ({ phase: 'draw', id: '', stage: '', candidates: [], selected: 0, partialNote: '' }));
  const scope = useAsyncScope();
  const cancelled = useRef(new Set<string>());
  function cancelOnce(id: string) {
    if (!id || cancelled.current.has(id)) return;
    cancelled.current.add(id);
    void api.cancelInpaint(id).catch(() => { });
  }
  useEffect(() => () => {
    const id = store.getSnapshot().id;
    if (id && !cancelled.current.has(id)) { cancelled.current.add(id); void api.cancelInpaint(id).catch(() => { }); }
  }, [store]);
  const isBusy = () => ['submitting', 'running', 'applying'].includes(store.getSnapshot().phase);
  function reset() { store.patch({ phase: 'draw', id: '', stage: '', candidates: [], partialNote: '', selected: 0 }); }
  function poll(id: string, token: number, delay = 0) {
    if (!scope.valid(token)) return;
    const timer = setTimeout(async () => {
      release();
      if (!scope.valid(token)) return;
      const controller = new AbortController();
      const forget = scope.own(() => controller.abort());
      try {
        const result: InpaintStatusView = await api.inpaintStatus(id, controller.signal);
        if (!scope.valid(token)) return;
        if (result.status === 'done') store.patch({
          phase: 'pick', id, stage: '', candidates: result.candidates || [], selected: 0,
          partialNote: [result.notice, result.error].filter(Boolean).join('；')
        });
        else if (result.status === 'failed' || result.status === 'cancelled') {
          reset();
          if (result.status === 'failed') toast.error('修补失败：' + (result.error || '未知错误'));
        } else { store.patch({ phase: 'running', id, stage: result.stage }); poll(id, token, 1500); }
      } catch {
        if (scope.valid(token)) poll(id, token, 3000);
      } finally { forget(); }
    }, delay);
    const release = scope.own(() => clearTimeout(timer));
  }
  async function submit() {
    if (isBusy()) return;
    const mask = exportMask();
    if (!mask) { toast.error('请先涂抹要处理的区域'); return; }
    const p = options.getSnapshot();
    if (p.mode === 'add' && !p.prompt.trim()) { toast.error('生成式添加需要描述要添加的内容'); return; }
    const seed = p.seedText.trim() ? Number(p.seedText.trim()) : undefined;
    scope.invalidate(); const token = scope.token();
    store.patch({ phase: 'submitting', candidates: [], id: '' });
    try {
      const result = await api.submitInpaint({
        mask_b64: mask, prompt: p.prompt.trim(), mode: p.mode,
        grow: p.mode === 'remove' ? p.removeGrow : p.addGrow, feather: p.mode === 'remove' ? p.removeFeather : p.addFeather,
        n: p.nCount, ...(Number.isFinite(seed) ? { seed } : {}), target: toTargetPayload(props.target)
      });
      if (!scope.valid(token)) { cancelOnce(result.inpaint_id); return; }
      options.set('nCount', result.effective_n);
      store.patch({ phase: 'running', id: result.inpaint_id, stage: '', partialNote: result.notice || '' });
      poll(result.inpaint_id, token);
    } catch (error) { if (scope.valid(token)) { reset(); toast.error((error as Error).message); } }
  }
  async function discardCandidates() {
    const id = store.getSnapshot().id;
    scope.invalidate(); const token = scope.token();
    if (id) { cancelled.current.add(id); await api.cancelInpaint(id).catch(() => { }); }
    if (!scope.valid(token)) return false;
    reset(); return true;
  }
  async function reroll() { if (await discardCandidates()) await submit(); }
  async function applySelected() {
    const current = store.getSnapshot();
    if (current.phase !== 'pick' || !current.id || !current.candidates[current.selected]) return;
    const token = scope.token(); store.set('phase', 'applying');
    try {
      const result = await api.applyInpaint(current.id, current.selected);
      if (!scope.valid(token)) return;
      reset();
      if (props.target.kind === 'job') { toast.success('已保存为新候选（‹n/N› 可切回原图对比）'); props.onDone?.(result.job); }
      else if (props.target.kind === 'record') { toast.success('修补结果已追加到该记录'); props.onDone?.(); }
      else { props.onRoomCleaned?.(result.path || '', result.url || '', result.thumb || result.url || ''); toast.success('已替换为清理后的房间图'); }
      props.onOpenChange(false);
    } catch (error) {
      if (!scope.valid(token)) return;
      store.set('phase', 'pick');
      reportCommitError(error, result => {
        if (!scope.valid(token)) return;
        cancelOnce(current.id); reset(); props.onDone?.(result.job); props.onOpenChange(false);
      });
    }
  }
  function cancelRunning() { scope.invalidate(); cancelOnce(store.getSnapshot().id); reset(); }
  function handleOpenChange(open: boolean) { if (!open) cancelRunning(); props.onOpenChange(open); }
  return {
    state, isBusy, actions: {
      submit, reroll, redraw: discardCandidates, applySelected, cancelRunning, handleOpenChange,
      setSelected: (selected: number) => store.set('selected', selected)
    }
  };
}
