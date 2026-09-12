import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

// Run actual hooks with explicit effect setup/cleanup replay. This tests lifecycle
// ownership without pretending to test React rendering (covered by Playwright).
function harness(api) {
  const effects = [], cache = new Map();
  const root = fileURLToPath(new URL('../src/', import.meta.url));
  const react = {
    useEffect: effect => effects.push(effect), useRef: current => ({ current }),
    useState: init => [typeof init === 'function' ? init() : init, () => { throw new Error('unexpected rendered-state setter'); }],
    useSyncExternalStore: (_subscribe, get) => get(),
  };
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
    assert.ok(existsSync(file));
    compiledModule._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
    return compiledModule.exports;
  }
  return { load: relative => load(path.join(root, relative)), setup: () => { const cleanup = effects.map(effect => effect()); return () => cleanup.forEach(fn => fn?.()); } };
}
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const flush = () => new Promise(resolve => setImmediate(resolve));
function options() {
  const lists = 'continents workflow_modes room_types market_furniture cn_room_types cn_developers cn_cities cn_tiers cn_unit_types cn_delivery_choices floor_sizes seam_types glossiness floor_tones styles lightings angles aspect_ratios resolutions pet_types pet_actions pet_focus avoid_items panel_sizes'.split(' ');
  return { ...Object.fromEntries(lists.map(key => [key, ['default']])), workflow_modes: ['纯效果图'], location_map: {},
    scene_catalog: { presets: [], property_options: [], room_options: [], site_contexts: [], floor_levels: [], room_scales: [], room_layouts: [], window_types: [], view_options: [], compatibility_rules: { scene_fields: [] } } };
}

test('effect replay leaves history reuse for the active initialization and publishes one restored draft', async () => {
  const first = deferred(), second = deferred(); let calls = 0;
  const h = harness({ getOptions: () => (++calls === 1 ? first : second).promise, getConfig: async () => ({}) });
  const values = new Map([['floor-engine:reuse-request:v1', JSON.stringify({ params: { workflow_mode: '纯效果图', style_type: 'reused' }, floorPath: '/reuse' })]]);
  const writes = [];
  globalThis.window = { localStorage: { getItem: key => values.get(key) ?? null, removeItem: key => values.delete(key), setItem: (key, value) => { values.set(key, value); writes.push(JSON.parse(value)); } } };
  try {
    const { useGenerationState } = h.load('components/generation/useGenerationState');
    const state = useGenerationState(); const cleanup = h.setup(); cleanup(); const stop = h.setup();
    first.resolve(options()); await flush();
    assert.ok(values.has('floor-engine:reuse-request:v1')); assert.equal(writes.length, 0);
    second.resolve(options()); await flush();
    assert.equal(values.has('floor-engine:reuse-request:v1'), false);
    assert.equal(state.store.getSnapshot().params.style_type, 'reused');
    assert.ok(writes.length > 0); assert.ok(writes.every(s => s.params.style_type === 'reused' && s.floor.path === '/reuse'));
    stop();
  } finally { delete globalThis.window; }
});

test('floor analysis rejects older selections and preserves manual tone revisions', async () => {
  const replies = [deferred(), deferred()]; let calls = 0;
  const h = harness({ floorAnalyze: () => replies[calls++].promise });
  const { createSessionStore } = h.load('lib/editor/session-store');
  const store = createSessionStore({ floor: null, params: { floor_tone: 'original' }, toneRevision: 0, recipes: [] });
  const { useFloorAnalysis: createAnalysis } = h.load('components/generation/useFloorAnalysis');
  const actions = createAnalysis({ store }); const stop = h.setup();
  const one = actions.pickFloor({ path: '/one' }), two = actions.pickFloor({ path: '/two' });
  store.patch({ params: { floor_tone: 'manual' }, toneRevision: 1 });
  replies[1].resolve({ tone: 'second', recipes: [{ key: 'second' }] }); await two;
  replies[0].resolve({ tone: 'old', recipes: [{ key: 'old' }] }); await one;
  assert.equal(store.getSnapshot().params.floor_tone, 'manual'); assert.equal(store.getSnapshot().recipes[0].key, 'second');
  stop();
});

test('clearing or unmounting a floor selection prevents late analysis from repopulating it', async () => {
  for (const unmount of [false, true]) {
    const reply = deferred(), h = harness({ floorAnalyze: () => reply.promise });
    const { createSessionStore } = h.load('lib/editor/session-store');
    const store = createSessionStore({ floor: null, params: { floor_tone: 'original' }, toneRevision: 0, recipes: [] });
    const { useFloorAnalysis: createAnalysis } = h.load('components/generation/useFloorAnalysis');
    const actions = createAnalysis({ store }); const stop = h.setup();
    const pending = actions.pickFloor({ path: '/one' }); if (unmount) stop(); else actions.clearFloor();
    reply.resolve({ tone: 'late', recipes: [{ key: 'late' }] }); await pending;
    assert.equal(store.getSnapshot().params.floor_tone, 'original'); assert.deepEqual(store.getSnapshot().recipes, []);
    if (!unmount) { assert.equal(store.getSnapshot().floor, null); stop(); }
  }
});

test('edits made before options arrive override only their own restored fields', async () => {
  const reply = deferred(), h = harness({ getOptions: () => reply.promise, getConfig: async () => ({}) });
  globalThis.window = { localStorage: { getItem: key => key.includes('draft') ? JSON.stringify({ params: { workflow_mode: '纯效果图', style_type: 'old', floor_tone: 'restored-tone' }, floor: { path: '/old', name: 'old', url: '/old', thumb: '/old' } }) : null, setItem() {}, removeItem() {} } };
  try {
    const { useGenerationState } = h.load('components/generation/useGenerationState');
    const state = useGenerationState(), stop = h.setup();
    state.setFloor({ path: '/new', name: 'new', url: '/new', thumb: '/new' });
    state.updateParams({ style_type: 'new-style' });
    reply.resolve(options()); await flush();
    assert.equal(state.store.getSnapshot().floor.path, '/new');
    assert.equal(state.store.getSnapshot().params.style_type, 'new-style');
    assert.equal(state.store.getSnapshot().params.floor_tone, 'restored-tone');
    stop();
  } finally { delete globalThis.window; }
});
