"use client";
import { useEffect, useRef, type SetStateAction } from 'react';
import { useSessionStore } from '@/lib/editor/session-store';
import type { ReviewStatus } from '@/lib/types';
import type { RecordQuery } from './useRecordQuery';
export interface RecordDialogs {
  zoom: string | null;
  panoView: { url: string; label: string; initialYawDeg: number } | null;
  compare: { before: string; after: string } | null;
  colorMatch: (Exclude<{
    open: boolean;
    srcUrl: string;
    imageRel: string;
    refUrl: string;
    refPath: string;
    recordId: string;
    resultId: string;
  } | null, null> & { jsonPath?: string }) | null;
  inpaint: (Exclude<{
    open: boolean;
    srcUrl: string;
    recordId: string;
    resultId: string;
  } | null, null> & { jsonPath?: string }) | null;
  floorVisualize: (Exclude<{
    open: boolean;
    srcUrl: string;
    textureUrl: string;
    texturePath: string;
    recordId: string;
    resultId: string;
  } | null, null> & { jsonPath?: string }) | null;
  reveal: (Exclude<{
    open: boolean;
    rid: string;
    pw: string;
    text: string;
  }, null> & { jsonPath?: string });
  edit: (Exclude<{
    open: boolean;
    rid: string;
    resultId: string;
    instruction: string;
    colorMatch: boolean;
  }, null> & { jsonPath?: string });
  review: (Exclude<{
    open: boolean;
    rid: string;
    resultId: string;
    status: ReviewStatus;
    tags: string[];
    note: string;
    best: boolean;
  }, null> & { jsonPath?: string });
}
const initial = (): RecordDialogs => ({
  zoom: null, panoView: null, compare: null, colorMatch: null, inpaint: null, floorVisualize: null, reveal: { open: false, rid: "", pw: "", text: "" }, edit: { open: false, rid: "", resultId: "", instruction: "", colorMatch: true }, review: {
    open: false,
    rid: "",
    resultId: "",
    status: "unreviewed",
    tags: [],
    note: "",
    best: false,
  }
});

const signature = (value: RecordDialogs['edit'] | RecordDialogs['review']) => JSON.stringify({ ...value, open: undefined });
export function useRecordDialogs(query: RecordQuery) {
  const { state, store } = useSessionStore(initial);
  const drafts = useRef(new Map<string, RecordDialogs['edit'] | RecordDialogs['review']>());
  const key = (name: string, value: RecordDialogs['edit'] | RecordDialogs['review']) => JSON.stringify([name, value.jsonPath, value.rid, value.resultId]);
  function field<K extends keyof RecordDialogs>(name: K, action: SetStateAction<RecordDialogs[K]>) {
    const previous = store.getSnapshot()[name];
    let value = typeof action === 'function' ? (action as (v: RecordDialogs[K]) => RecordDialogs[K])(previous) : action;
    if (value && typeof value === 'object' && 'open' in value) {
      const old = previous as typeof value | null;
      if (value.open && (!('jsonPath' in value) || !value.jsonPath || !old || !old.open || ('rid' in value && 'rid' in old && value.rid !== old.rid) || ('resultId' in value && 'resultId' in old && value.resultId !== old.resultId))) {
        value = { ...value, jsonPath: query.store.getSnapshot().active || '' };
        if (name === 'review' || name === 'edit') {
          const saved = drafts.current.get(key(name, value as RecordDialogs['edit']));
          if (saved) value = { ...saved, open: true } as RecordDialogs[K];
        }
      }
      if (name === 'reveal' && value && typeof value === 'object' && 'open' in value && !value.open) value = { open: false, rid: '', pw: '', text: '' } as RecordDialogs[K];
    }
    if (name === 'review' || name === 'edit') {
      const draft = value as RecordDialogs['edit'] | RecordDialogs['review'];
      if (draft.jsonPath && draft.resultId) drafts.current.set(key(name, draft), draft);
    }
    store.set(name, value);
  }
  function complete(name: 'edit' | 'review', captured: RecordDialogs['edit'] | RecordDialogs['review']) {
    const id = key(name, captured), saved = drafts.current.get(id);
    if (saved && signature(saved) === signature(captured)) drafts.current.delete(id);
    const current = store.getSnapshot()[name];
    if (key(name, current) === id && signature(current) === signature(captured)) store.set(name, initial()[name]);
  }
  useEffect(() => {
    let path = query.store.getSnapshot().active;
    return query.store.subscribe(() => {
      const next = query.store.getSnapshot().active;
      if (path === next) return; path = next;
      const current = store.getSnapshot();
      field('edit', { ...current.edit, open: false }); field('review', { ...current.review, open: false });
      store.patch({ colorMatch: null, inpaint: null, floorVisualize: null, zoom: null, compare: null, panoView: null, reveal: initial().reveal });
    });
    // The stores are stable; field only accesses their current snapshots.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.store, store]);
  return {
    ...state, dialogStore: store, complete,
    setZoom: (value: SetStateAction<RecordDialogs['zoom']>) => field('zoom', value),
    setPanoView: (value: SetStateAction<RecordDialogs['panoView']>) => field('panoView', value),
    setCompare: (value: SetStateAction<RecordDialogs['compare']>) => field('compare', value),
    setColorMatch: (value: SetStateAction<RecordDialogs['colorMatch']>) => field('colorMatch', value),
    setInpaint: (value: SetStateAction<RecordDialogs['inpaint']>) => field('inpaint', value),
    setFloorVisualize: (value: SetStateAction<RecordDialogs['floorVisualize']>) => field('floorVisualize', value),
    setReveal: (value: SetStateAction<RecordDialogs['reveal']>) => field('reveal', value),
    setEdit: (value: SetStateAction<RecordDialogs['edit']>) => field('edit', value),
    setReview: (value: SetStateAction<RecordDialogs['review']>) => field('review', value)
  };
}
export type RecordDialogModel = ReturnType<typeof useRecordDialogs>;
