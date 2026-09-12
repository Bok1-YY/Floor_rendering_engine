"use client";
import { useState } from "react";

import type { JobView, ModelKey } from "@/lib/types";

import { useSessionStore } from '@/lib/editor/session-store';
import { useJobSnapshot } from './useJobSnapshot';
import { useJobCandidates } from './useJobCandidates';
import { useJobReview } from './useJobReview';
import { useJobActions } from './useJobActions';
export function useJobCard(initial: JobView, onRemove?: (id: string) => void) {
  const snapshot = useJobSnapshot(initial), { job, active, applySnapshot } = snapshot;
  const candidates = useJobCandidates(job);
  const review = useJobReview(job, candidates.activeUrl);
  const invalidate = () => { candidates.resetCandidates(); review.invalidateReview(); };
  const actions = useJobActions(job, applySnapshot, invalidate, onRemove);
  const [zoom, setZoom] = useState<string | null>(null);
  const { state: editState, store: editStore } = useSessionStore(() => ({ editOpen: false, editText: '', editColorMatch: true }));
  const { editOpen, editText, editColorMatch } = editState;
  const setEditOpen = (v: boolean) => editStore.set('editOpen', v);
  const setEditText = (v: string) => editStore.set('editText', v);
  const setEditColorMatch = (v: boolean) => editStore.set('editColorMatch', v);
  const [compareOpen, setCompareOpen] = useState(false);
  // 手动校色：记录点的是哪个图槽（B2/Pro 各自的当前浏览候选）
  const [colorMatch, setColorMatch] = useState<{
    stage: ModelKey;
    srcUrl: string;
    imageRel: string;
  } | null>(null);
  // 生成式修补：同样记录所点图槽的当前浏览候选
  const [inpaint, setInpaint] = useState<{
    stage: ModelKey;
    srcUrl: string;
    imageRel: string;
  } | null>(null);
  const [floorVisualize, setFloorVisualize] = useState<{
    stage: ModelKey;
    srcUrl: string;
    imageRel: string;
  } | null>(null);
  const [regenN, setRegenN] = useState(1);

  async function doEditSubmit() {
    const captured = editStore.getSnapshot();
    if (!captured.editText.trim()) return;
    if (await actions.submitEdit(captured.editText.trim(), captured.editColorMatch)) {
      if (editStore.getSnapshot() === captured) editStore.patch({ editOpen: false, editText: '' });
    }
  }
  function onEditorDone(next?: JobView) { if (next && applySnapshot(next)) invalidate(); }
  const stageLine = (job.model_targets || []).map(key => { const run = job.model_runs?.[key]; return run?.stage ? `${run.label} ${run.stage}` : ''; }).filter(Boolean).join(' · ') || '处理中…';
  const terminal = !active && (job.status === 'done' || job.status === 'partial' || job.status === 'failed');
  const compareAfter = (candidates.slots.find(s => s.key === 'pro') ?? candidates.slots.find(s => s.key === 'b2') ?? candidates.slots[0])?.url || '';
  return {
    ...snapshot, ...candidates, ...review, ...actions, zoom, setZoom, editOpen, setEditOpen, editText, setEditText, editColorMatch, setEditColorMatch,
    compareOpen, setCompareOpen, colorMatch, setColorMatch, inpaint, setInpaint, floorVisualize, setFloorVisualize, regenN, setRegenN,
    stageLine, terminal, compareAfter, isFree: job.workflow_mode.includes('自由创作'), doEditSubmit, onEditorDone
  };
}
export type JobCardModel = ReturnType<typeof useJobCard>;
