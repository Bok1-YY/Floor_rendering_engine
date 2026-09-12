import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';
const root = fileURLToPath(new URL('../src/', import.meta.url));
function runtime(api = {}) {
  const effects = [], snapshots = [], cache = new Map();
  const react = { useEffect: effect => effects.push(effect), useRef: current => ({ current }), useCallback: fn => fn,
    useState: init => [typeof init === 'function' ? init() : init, () => {}],
    useSyncExternalStore: (_subscribe, get) => { snapshots.push(get); return get(); } };
  function load(file) {
    if (!path.extname(file)) file += '.ts';
    if (cache.has(file)) return cache.get(file).exports;
    const compiledModule = new Module(file); cache.set(file, compiledModule);
    compiledModule.require = name => {
      if (name === 'react') return react;
      if (name === 'sonner') return { toast: { success() {}, error() {}, info() {} } };
      if (name === '@/lib/api') return { api };
      if (name.startsWith('@/')) return load(path.join(root, name.slice(2)));
      if (name.startsWith('.')) return load(path.resolve(path.dirname(file), name));
      throw new Error(`Unexpected dependency ${name}`);
    };
    compiledModule._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
    return compiledModule.exports;
  }
  return { load: relative => load(path.join(root, relative)), snapshots, setup: () => { const cleanup = effects.map(effect => effect()); return () => cleanup.forEach(fn => fn?.()); } };
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));
const job = (patch = {}) => ({ job_id: 'one', status: 'running', snapshot_at: 1, display_name: 'one', error: '', operation_status: '', ...patch });

test('job snapshot rejects foreign and older frames and notifies once per transition', () => {
  const { JobSnapshot } = runtime().load('components/job-card/snapshot');
  const gate = new JobSnapshot(job());
  assert.equal(gate.accept(job({ job_id: 'other', snapshot_at: 99 })), null);
  assert.equal(gate.accept(job({ snapshot_at: .5 })), null);
  assert.equal(gate.accept(job({ status: 'done', snapshot_at: 2 })).notifications.length, 1);
  assert.equal(gate.accept(job({ status: 'done', snapshot_at: 2 })).notifications.length, 0);
  gate.accept(job({ status: 'done', operation_status: 'running', snapshot_at: 3 }));
  assert.equal(gate.accept(job({ status: 'done', operation_status: 'failed', snapshot_at: 4 })).notifications.length, 1);
});

test('legacy unstamped snapshots retain compatibility without lowering the timestamp guard', () => {
  const { JobSnapshot } = runtime().load('components/job-card/snapshot');
  const gate = new JobSnapshot(job({ snapshot_at: 10 }));
  assert.ok(gate.accept(job({ snapshot_at: undefined })));
  assert.equal(gate.accept(job({ snapshot_at: 9 })), null);
});

test('result identity prefers full paths and rejects ambiguous legacy filenames', () => {
  const { locateResult, targetKey } = runtime().load('lib/results/identity');
  const rows = [{ id: 'r', results: [{ result_id: 'a', result_url: '/a/shared.png' }, { result_id: 'b', result_url: '/b/shared.png' }] }];
  assert.equal(locateResult(rows, 'r', 'https://local/b/shared.png?size=2').result_id, 'b');
  assert.throws(() => locateResult(rows, 'r', '/outputs/shared.png'), /同名/);
  assert.equal(locateResult(rows, 'other', '/b/shared.png'), null);
  assert.notEqual(targetKey({ jsonPath: '/one', recordId: 'r', resultId: 'a' }), targetKey({ jsonPath: '/two', recordId: 'r', resultId: 'a' }));
});

test('review payload retains tags and note while changing only the chosen status', () => {
  const { reviewPayload } = runtime().load('lib/results/identity');
  const payload = reviewPayload({ jsonPath: '/one', recordId: 'r', resultId: 'a' }, { review_tags: ['tag'], review_note: 'note', best: true }, { review_status: 'pass' });
  assert.deepEqual(payload, { json_path: '/one', record_id: 'r', result_id: 'a', review_tags: ['tag'], review_note: 'note', best: true, review_status: 'pass' });
});

test('result locks prevent conflicting operations but allow separate results', () => {
  const { ResultOperationLock } = runtime().load('lib/results/operation-lock');
  const locks = new ResultOperationLock();
  const release = locks.claim('/a', 'r', 'one'); assert.ok(release);
  assert.equal(locks.claim('/a', 'r', 'one'), null); assert.equal(locks.claim('/a', 'r'), null);
  const second = locks.claim('/a', 'r', 'two'); assert.ok(second); second(); release();
  const entire = locks.claim('/a', 'r'); assert.ok(entire); assert.equal(locks.claim('/a', 'r', 'two'), null); entire();
});

test('candidate cache limits concurrency, deduplicates reads and fetches only added indices', async () => {
  const { CandidateCache } = runtime().load('components/job-card/candidate-cache');
  const requests = [];
  const cache = new CandidateCache((model, idx) => { const reply = deferred(); requests.push({ model, idx, ...reply }); return reply.promise; }, () => {});
  cache.load('b2', 6, 0); assert.equal(requests.length, 4);
  const duplicate = cache.read('b2', 0);
  requests[0].resolve({ idx: 0, url: '/0', thumb: '/0' }); await duplicate; await flush();
  assert.equal(requests.length, 5);
  requests[1].resolve({ idx: 1 }); await flush(); assert.equal(requests.length, 6);
  requests.slice(2).forEach(r => r.resolve({ idx: r.idx })); await flush();
  cache.load('b2', 6, 0); assert.equal(requests.length, 6);
  cache.load('b2', 8, 0); assert.deepEqual(requests.slice(6).map(r => r.idx), [6, 7]);
  requests.slice(6).forEach(r => r.resolve({ idx: r.idx })); await flush(); cache.stop();
});

test('candidate failures retain successful siblings and invalidation rejects late results', async () => {
  const { CandidateCache } = runtime().load('components/job-card/candidate-cache');
  const requests = [];
  const cache = new CandidateCache((_model, idx, signal) => { const reply = deferred(); requests.push({ idx, signal, ...reply }); return reply.promise; }, () => {});
  cache.load('b2', 3, 0);
  requests[0].resolve({ idx: 0 }); requests[1].reject(new Error('one failed')); await flush();
  assert.deepEqual(cache.values('b2').map(r => r.idx), [0]);
  cache.load('b2', 3, 0); assert.equal(requests.length, 4);
  cache.invalidate(); assert.ok(requests[2].signal.aborted);
  requests[2].resolve({ idx: 2 }); requests[3].resolve({ idx: 1 }); await flush();
  assert.deepEqual(cache.values('b2'), []); cache.stop();
});

test('rapid candidate navigation applies the newest requested index', async () => {
  const requests = new Map();
  const h = runtime({ jobResult: (_job, _model, idx) => { const reply = deferred(); requests.set(idx, reply); return reply.promise; } });
  const { useJobCandidates: createCandidates } = h.load('components/job-card/useJobCandidates');
  const actions = createCandidates(job({ model_targets: ['b2'], model_runs: { b2: { url: '/0', thumb: '/0', total: 3, idx: 0 } } }));
  const stop = h.setup(); const first = actions.nav('b2', 1), second = actions.nav('b2', 1);
  requests.get(2).resolve({ idx: 2, url: '/2', thumb: '/2' }); await second;
  requests.get(1).resolve({ idx: 1, url: '/1', thumb: '/1' }); await first;
  assert.equal(h.snapshots[0]().view.b2.idx, 2);
  requests.get(0).resolve({ idx: 0 }); await flush(); stop();
});

test('record dialog drafts are scoped by target and successful old saves preserve newer input', () => {
  const h = runtime(); const { createSessionStore } = h.load('lib/editor/session-store');
  const query = { store: createSessionStore({ active: '/A' }) };
  const { useRecordDialogs: createDialogs } = h.load('components/records/useRecordDialogs');
  const dialogs = createDialogs(query), stop = h.setup();
  const base = { open: true, rid: 'r', resultId: 'x', note: 'first', status: 'pass', tags: [], best: false };
  dialogs.setReview(base); const saved = dialogs.dialogStore.getSnapshot().review;
  dialogs.setReview(v => ({ ...v, note: 'newer' }));
  query.store.set('active', '/B'); assert.equal(dialogs.dialogStore.getSnapshot().review.open, false);
  dialogs.setReview({ ...base, note: 'B' }); dialogs.complete('review', saved);
  assert.equal(dialogs.dialogStore.getSnapshot().review.note, 'B');
  query.store.set('active', '/A'); dialogs.setReview(base);
  assert.equal(dialogs.dialogStore.getSnapshot().review.note, 'newer'); stop();
});

test('closing a reveal dialog erases password and decrypted text', () => {
  const h = runtime(); const { createSessionStore } = h.load('lib/editor/session-store');
  const { useRecordDialogs: createDialogs } = h.load('components/records/useRecordDialogs');
  const dialogs = createDialogs({ store: createSessionStore({ active: '/A' }) });
  dialogs.setReveal({ open: true, rid: 'r', pw: 'secret', text: 'decrypted' });
  dialogs.setReveal(v => ({ ...v, open: false }));
  assert.equal(dialogs.dialogStore.getSnapshot().reveal.pw, ''); assert.equal(dialogs.dialogStore.getSnapshot().reveal.text, '');
});

test('successful draft completion remains cleared after switching away and back', () => {
  const h = runtime(), { createSessionStore } = h.load('lib/editor/session-store');
  const query = { store: createSessionStore({ active: '/A' }) };
  const { useRecordDialogs: createDialogs } = h.load('components/records/useRecordDialogs');
  const dialogs = createDialogs(query), stop = h.setup();
  const base = { open: true, rid: 'r', resultId: 'x', instruction: 'submitted', colorMatch: true };
  dialogs.setEdit(base); dialogs.complete('edit', dialogs.dialogStore.getSnapshot().edit);
  query.store.set('active', '/B'); query.store.set('active', '/A');
  dialogs.setEdit({ ...base, instruction: '' });
  assert.equal(dialogs.dialogStore.getSnapshot().edit.instruction, ''); stop();
});

test('disposed SSE handlers cannot update a card even when a queued done event arrives', () => {
  const previous = globalThis.EventSource, streams = [];
  globalThis.EventSource = class {
    constructor() { this.listeners = {}; streams.push(this); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    close() { this.closed = true; }
  };
  try {
    const h = runtime(), { useJobStream: subscribe } = h.load('hooks/useJobStream');
    const values = []; subscribe('one', value => values.push(value)); const stop = h.setup();
    streams[0].onmessage({ data: JSON.stringify(job()) }); assert.equal(values.length, 1);
    stop(); streams[0].listeners.done({ data: JSON.stringify(job({ status: 'done' })) });
    assert.equal(values.length, 1); assert.equal(streams[0].closed, true);
  } finally { globalThis.EventSource = previous; }
});
