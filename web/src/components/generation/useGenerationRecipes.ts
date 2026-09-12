"use client";
import { useEffect, useRef } from 'react';
import type { SetStateAction } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { hydrateSceneParams } from '@/lib/scene';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { useSessionStore } from '@/lib/editor/session-store';
import type { CustomRecipe, ResolvedRecipe } from '@/lib/types';
import { recipeSnapshot } from './rules';
import type { GenerationState } from './useGenerationState';
export function useGenerationRecipes({ store, updateParams }: GenerationState) {
  const scope = useAsyncScope(); const revision = useRef(0); const removed = useRef(new Set<string>()); const busy = useRef(false);
  const { state, store: recipes } = useSessionStore(() => ({ myRecipes: [] as CustomRecipe[], recipeDlg: { open: false, id: '', name: '' } }));
  useEffect(() => {
    const token = scope.token();
    api.listCustomRecipes().then(list => {
      if (!scope.valid(token)) return;
      recipes.set('myRecipes', current => {
        const merged = new Map(list.filter(r => !removed.current.has(r.id)).map(r => [r.id, r]));
        current.forEach(r => merged.set(r.id, r)); return [...merged.values()];
      });
    }).catch(() => { });
  }, [scope, recipes]);
  function setRecipeDlg(value: SetStateAction<typeof state.recipeDlg>) { ++revision.current; recipes.set('recipeDlg', value); }
  function applyRecipe(r: ResolvedRecipe) {
    const { params } = store.getSnapshot();
    updateParams({
      style_type: r.style_type || params.style_type, lighting: r.lighting || params.lighting,
      angle: r.angle || params.angle, aspect_ratio: r.aspect_ratio || params.aspect_ratio, resolution: r.resolution || params.resolution
    });
    toast.success(`已套用配方：${r.label}`);
  }
  function applyCustomRecipe(r: CustomRecipe) {
    const s = store.getSnapshot();
    const params = s.options ? hydrateSceneParams({ ...s.params, ...recipeSnapshot(r.params) }, s.options.scene_catalog) : { ...s.params, ...recipeSnapshot(r.params) };
    store.patch({ params, modelTargets: params.workflow_mode.includes('自由创作') ? s.modelTargets.filter(k => k !== 'sd35') : s.modelTargets });
    toast.success(`已套用配方：${r.name}`);
  }
  async function submitRecipeDlg() {
    if (busy.current) return;
    const dlg = recipes.getSnapshot().recipeDlg, name = dlg.name.trim();
    if (!name) { toast.warning('请输入配方名'); return; }
    busy.current = true; const version = ++revision.current, token = scope.token();
    const snapshot = structuredClone(recipeSnapshot(store.getSnapshot().params));
    try {
      const r = dlg.id ? await api.updateCustomRecipe(dlg.id, { name }) : await api.addCustomRecipe(name, snapshot);
      if (!scope.valid(token)) return;
      if (!removed.current.has(r.id)) recipes.set('myRecipes', list => dlg.id ? list.map(x => x.id === r.id ? r : x) : [r, ...list]);
      if (version === revision.current) { recipes.set('recipeDlg', { open: false, id: '', name: '' }); toast.success(dlg.id ? '已改名' : `已保存配方：${name}`); }
    } catch (e) { if (scope.valid(token) && version === revision.current) toast.error((e as Error).message); }
    finally { busy.current = false; }
  }
  async function removeCustomRecipe(r: CustomRecipe) {
    if (!window.confirm(`删除配方「${r.name}」？`)) return;
    const token = scope.token(); ++revision.current;
    try {
      await api.deleteCustomRecipe(r.id); if (!scope.valid(token)) return;
      removed.current.add(r.id); recipes.set('myRecipes', list => list.filter(x => x.id !== r.id)); toast.success('已删除');
    } catch (e) { if (scope.valid(token)) toast.error((e as Error).message); }
  }
  return { ...state, setRecipeDlg, applyRecipe, applyCustomRecipe, submitRecipeDlg, removeCustomRecipe };
}
