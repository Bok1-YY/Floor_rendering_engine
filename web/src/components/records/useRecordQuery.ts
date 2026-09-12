"use client";
import { useEffect, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useSessionStore } from '@/lib/editor/session-store';
import type { RecordEntry, RecordFile, ReviewStatus } from '@/lib/types';
export function useRecordQuery() {
  const { state, store } = useSessionStore(() => ({
    files: [] as RecordFile[], records: [] as RecordEntry[], active: null as string | null,
    search: '', favoriteOnly: false, roomFilter: '__all__', reviewFilter: '__all__' as '__all__' | '__best__' | ReviewStatus, loading: false
  }));
  const life = useRef({ active: true, content: 0, directory: 0, controller: null as AbortController | null, listController: null as AbortController | null });
  const deleted = useRef(new Map<string, Set<string>>());
  const read = useCallback(async (path: string, select = false) => {
    const owned = life.current, run = ++owned.content;
    owned.controller?.abort(); const controller = new AbortController(); owned.controller = controller;
    if (select) store.patch({ active: path, records: [], loading: true, roomFilter: '__all__', reviewFilter: '__all__' });
    try {
      const rows = await api.loadRecord(path, controller.signal);
      if (!owned.active || run !== owned.content || store.getSnapshot().active !== path) return;
      const removed = deleted.current.get(path);
      store.set('records', rows.filter(r => !removed?.has(JSON.stringify([r.id]))).map(r => ({
        ...r,
        results: r.results?.filter(item => !removed?.has(JSON.stringify([r.id, item.result_id])))
      })));
    } catch (e) { if (owned.active && run === owned.content && !controller.signal.aborted) toast.error((e as Error).message); }
    finally { if (owned.active && run === owned.content) store.set('loading', false); }
  }, [store]);
  const reloadFiles = useCallback(async () => {
    const owned = life.current, run = ++owned.directory;
    owned.listController?.abort(); const controller = new AbortController(); owned.listController = controller;
    try {
      const files = await api.listRecords(controller.signal);
      if (!owned.active || run !== owned.directory) return;
      store.set('files', files);
      const active = store.getSnapshot().active;
      if (!active || !files.some(f => f.json_path === active)) {
        if (files[0]) await read(files[0].json_path, true);
        else { ++owned.content; owned.controller?.abort(); store.patch({ active: null, records: [], loading: false }); }
      }
    } catch (e) { if (owned.active && run === owned.directory && !controller.signal.aborted) toast.error((e as Error).message); }
  }, [store, read]);
  const reload = useCallback(async () => { const path = store.getSnapshot().active; if (path) await read(path); }, [store, read]);
  const afterMutation = useCallback(async (path: string) => {
    if (!life.current.active) return;
    await Promise.all([reloadFiles(), store.getSnapshot().active === path ? read(path) : Promise.resolve()]);
  }, [reloadFiles, store, read]);
  useEffect(() => {
    const owned = life.current; owned.active = true; void reloadFiles();
    const restored = () => { const path = store.getSnapshot().active; if (path) void afterMutation(path); else void reloadFiles(); };
    window.addEventListener('floor-result-restored', restored);
    return () => { owned.active = false; ++owned.content; ++owned.directory; owned.controller?.abort(); owned.listController?.abort(); window.removeEventListener('floor-result-restored', restored); };
  }, [reloadFiles, afterMutation, store]);
  function markDeleted(path: string, rid: string, result?: string) {
    const set = deleted.current.get(path) || new Set<string>(); set.add(JSON.stringify(result ? [rid, result] : [rid])); deleted.current.set(path, set);
  }
  return {
    ...state, store, open: (path: string) => read(path, true), reload, reloadFiles, afterMutation, markDeleted,
    setSearch: (v: string) => store.set('search', v), setFavoriteOnly: (v: boolean | ((p: boolean) => boolean)) => store.set('favoriteOnly', v),
    setRoomFilter: (v: string) => store.set('roomFilter', v), setReviewFilter: (v: typeof state.reviewFilter) => store.set('reviewFilter', v)
  };
}
export type RecordQuery = ReturnType<typeof useRecordQuery>;
