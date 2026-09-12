"use client";
import { useRef } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useAsyncScope } from '@/lib/editor/async-scope';
import type { Swatch } from '@/lib/types';
import type { GenerationState } from './useGenerationState';
export function useFloorAnalysis({ store }: GenerationState) {
  const scope = useAsyncScope(); const sequence = useRef(0);
  async function pickFloor(floor: Swatch) {
    const run = ++sequence.current, token = scope.token();
    store.patch({ floor, recipes: [] });
    const revision = store.getSnapshot().toneRevision;
    try {
      const result = await api.floorAnalyze(floor.path);
      const current = store.getSnapshot();
      if (!scope.valid(token) || run !== sequence.current || current.floor?.path !== floor.path) return;
      store.patch({
        recipes: result.recipes || [], params: current.toneRevision === revision
          ? { ...current.params, floor_tone: result.tone } : current.params
      });
      toast.success(current.toneRevision === revision ? '已识别色调，并给出智能配方' : '已更新智能配方，保留手动色调');
    } catch { /* Analysis is optional; manual tone remains available. */ }
  }
  function clearFloor() { ++sequence.current; store.patch({ floor: null, recipes: [] }); }
  return { pickFloor, clearFloor };
}
