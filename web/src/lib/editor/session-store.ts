"use client";
import { useState, useSyncExternalStore, type SetStateAction } from 'react';

export type FieldAction<S> = { [K in keyof S]: { type: 'field'; key: K; value: SetStateAction<S[K]> } }[keyof S];

export function sessionReducer<S>(state: S, action: FieldAction<S> | { type: "patch"; value: Partial<S> }): S {
  if (action.type === "patch") return { ...state, ...action.value };
  const previous = state[action.key];
  const next = typeof action.value === 'function'
    ? (action.value as (value: typeof previous) => typeof previous)(previous) : action.value;
  return Object.is(previous, next) ? state : { ...state, [action.key]: next };
}

export function createSessionStore<S extends object>(initial: S) {
  let snapshot = initial;
  const listeners = new Set<() => void>();
  const dispatch = (action: FieldAction<S> | { type: "patch"; value: Partial<S> }) => {
    const next = sessionReducer(snapshot, action);
    if (next === snapshot) return;
    snapshot = next;
    listeners.forEach(listener => listener());
  };
  const set = <K extends keyof S>(key: K, value: SetStateAction<S[K]>) =>
    dispatch({ type: 'field', key, value } as FieldAction<S>);
  const field = <K extends keyof S>(key: K) => ({
    get current() { return snapshot[key]; },
    set current(value: S[K]) { set(key, value); },
  });
  return {
    getSnapshot: () => snapshot, dispatch, set, field, patch: (value: Partial<S>) => dispatch({ type: "patch", value }),
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; }
  };
}

/** React and imperative image callbacks read the same reducer snapshot. */
export function useSessionStore<S extends object>(initialize: () => S) {
  const [store] = useState(() => createSessionStore(initialize()));
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return { state, store };
}
