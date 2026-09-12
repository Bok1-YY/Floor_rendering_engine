"use client";
import { useEffect } from 'react';
import type { SetStateAction } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { loadDraft, saveDraft, takeReuseRequest } from '@/lib/draft';
import { applySceneChange, hydrateSceneParams } from '@/lib/scene';
import { useSessionStore } from '@/lib/editor/session-store';
import type { GenParams, ResolvedRecipe } from '@/lib/types';
import { buildDefaultParams, DEFAULT_SD_OPTIONS, legacyFromTargets, swatchFromPath, targetsFromLegacy, type GenerationInput } from './rules';

export function useGenerationState() {
  const { state, store } = useSessionStore(() => ({
    options: null, params: { workflow_mode: '纯效果图 (生成全新空间)' },
    floor: null, refImg: null, roomImg: null, freePrompt: '', freeImages: [],
    modelTargets: ['b2', 'pro'], sdOptions: DEFAULT_SD_OPTIONS,
    recipes: [], ready: false, sdEnabled: false, toneRevision: 0,
  } as GenerationInput & { recipes: ResolvedRecipe[]; ready: boolean; sdEnabled: boolean; toneRevision: number }));
  useEffect(() => {
    let active = true;
    const edited = new Set<keyof typeof state>();
    const editedParams = new Set<keyof GenParams>();
    let previous = store.getSnapshot();
    // Inputs can be used while options are loading. Track only changed keys so
    // late defaults cannot replace those edits, while untouched draft fields restore.
    const unwatch = store.subscribe(() => {
      const next = store.getSnapshot();
      if (!next.ready) {
        for (const key of ['floor', 'refImg', 'roomImg', 'freePrompt', 'freeImages', 'modelTargets', 'sdOptions', 'recipes'] as const)
          if (next[key] !== previous[key]) edited.add(key);
        for (const key of Object.keys(next.params) as (keyof GenParams)[])
          if (next.params[key] !== previous.params[key]) editedParams.add(key);
      }
      previous = next;
    });
    api.getOptions().then(options => {
      if (!active || store.getSnapshot().ready) return;
      const reuse = takeReuseRequest();
      const draft = reuse?.params ? null : loadDraft();
      const source = reuse?.params ? reuse : draft;
      const params = hydrateSceneParams({ ...buildDefaultParams(options), ...source?.params }, options.scene_catalog);
      const targets = source?.modelTargets || targetsFromLegacy(source?.modelFilter);
      const restored = {
        options, params, ready: true,
        modelTargets: params.workflow_mode.includes('自由创作') ? targets.filter(key => key !== 'sd35') : targets,
        sdOptions: { ...DEFAULT_SD_OPTIONS, ...source?.sdOptions },
        floor: reuse?.params ? swatchFromPath(reuse.floorPath) : draft?.floor ?? null,
        refImg: reuse?.params ? swatchFromPath(reuse.refPath) : draft?.refImg ?? null,
        roomImg: reuse?.params ? swatchFromPath(reuse.roomPath) : draft?.roomImg ?? null,
        freePrompt: source?.freePrompt || '',
        freeImages: reuse?.params ? (reuse.freeImagePaths || []).slice(0, 3).map(path => swatchFromPath(path)!) : draft?.freeImages?.slice(0, 3) || [],
        recipes: reuse?.params ? [] : draft?.recipes || [],
      };
      const current = store.getSnapshot();
      const overrides = Object.fromEntries([...edited].map(key => [key, current[key]]));
      const parameterEdits = Object.fromEntries([...editedParams].map(key => [key, current.params[key]]));
      const finalParams = hydrateSceneParams({ ...restored.params, ...parameterEdits }, options.scene_catalog);
      const finalTargets = edited.has('modelTargets') ? current.modelTargets : restored.modelTargets;
      store.patch({
        ...restored, ...overrides, params: finalParams,
        modelTargets: finalParams.workflow_mode.includes('自由创作') ? finalTargets.filter(key => key !== 'sd35') : finalTargets
      });
      if (reuse?.params) toast.success('已回填历史参数，可直接生成或微调');
    }).catch(e => { if (active) toast.error('加载选项失败：' + (e as Error).message); });
    api.getConfig().then(c => { if (active) store.set('sdEnabled', !!c.sd_enabled); }).catch(() => { });
    return () => { active = false; unwatch(); };
  }, [store]);
  useEffect(() => {
    const persist = () => {
      const s = store.getSnapshot();
      if (!s.ready) return;
      saveDraft({
        params: s.params, modelFilter: legacyFromTargets(s.modelTargets), modelTargets: s.modelTargets,
        sdOptions: s.sdOptions, floor: s.floor, refImg: s.refImg, roomImg: s.roomImg,
        recipes: s.recipes, freePrompt: s.freePrompt, freeImages: s.freeImages
      });
    };
    persist(); return store.subscribe(persist);
  }, [store]);
  function updateParams(patch: Partial<GenParams>) {
    const s = store.getSnapshot();
    const result = s.options ? applySceneChange(s.params, patch, s.options.scene_catalog) : { params: { ...s.params, ...patch }, corrections: [] };
    store.patch({
      params: result.params,
      toneRevision: s.toneRevision + ('floor_tone' in patch ? 1 : 0),
      modelTargets: result.params.workflow_mode.includes('自由创作') ? s.modelTargets.filter(k => k !== 'sd35') : s.modelTargets
    });
    if (result.corrections.length) toast.info(`为保持场景合理，已联动：${result.corrections.join('；')}`);
  }
  const field = <K extends keyof typeof state>(key: K) => (value: SetStateAction<(typeof state)[K]>) => store.set(key, value);
  return {
    ...state, store, updateParams, setFloor: field('floor'), setRefImg: field('refImg'), setRoomImg: field('roomImg'),
    setFreePrompt: field('freePrompt'), setFreeImages: field('freeImages'), setModelTargets: field('modelTargets'), setSdOptions: field('sdOptions')
  };
}
export type GenerationState = ReturnType<typeof useGenerationState>;
