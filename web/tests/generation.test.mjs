import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import Module from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';
function load(relative) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const compiledModule = new Module(file);
  compiledModule.paths = Module._nodeModulePaths(fileURLToPath(new URL('..', import.meta.url)));
  compiledModule._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
  return compiledModule.exports;
}
const { JobCollection } = load('../src/components/generation/job-collection.ts');
const { validateGeneration, jobPayload, freePayload, recipeSnapshot } = load('../src/components/generation/rules.ts');
const { normalizeDraft } = load('../src/lib/draft.ts');
const input = () => ({ params: { workflow_mode: '纯效果图', floor_tone: 'warm' }, floor: { path: '/floor' }, refImg: null, roomImg: null, modelTargets: ['b2'], sdOptions: {}, freeImages: [], freePrompt: '', options: null });

test('single and batch share model and required-image validation', () => {
  for (const kind of ['single', 'rooms', 'floors']) {
    const s = input(); s.modelTargets = [];
    assert.match(validateGeneration(s, kind), /模型/);
    s.modelTargets = ['sd35']; s.params.workflow_mode = '参照模式';
    assert.match(validateGeneration(s, kind), /SD 3.5/);
    s.modelTargets = ['b2']; assert.match(validateGeneration(s, kind), /参照图/);
  }
  const disabled = input(); disabled.modelTargets = ['sd35']; disabled.sdEnabled = false;
  assert.match(validateGeneration(disabled, 'single'), /启用 SD/);
  const preview = input(); preview.modelTargets = []; assert.equal(validateGeneration(preview, 'preview'), null);
});
test('workflow-specific restrictions remain explicit', () => {
  const s = input(); s.params.workflow_mode = 'Omakase'; s.params.scene_override = 'scene';
  assert.match(validateGeneration(s, 'rooms'), /房间/); assert.equal(validateGeneration(s, 'floors'), null);
  s.params.workflow_mode = '墙板'; assert.match(validateGeneration(s, 'floors'), /不支持批量/);
  s.params.panel_submode = '再设计'; assert.match(validateGeneration(s, 'single'), /参照图/);
});
test('payload preserves Omakase cleanup and a detached submission snapshot', () => {
  const s = input(); s.params.workflow_mode = 'Omakase'; s.params.custom_addition = 'hidden'; s.options = { avoid_items: ['default'] };
  const payload = jobPayload(structuredClone(s)); s.params.floor_tone = 'changed';
  assert.equal(payload.params.floor_tone, 'warm'); assert.equal(payload.params.custom_addition, ''); assert.deepEqual(payload.params.avoid_items, ['default']);
  assert.equal(s.params.custom_addition, 'hidden');
});
test('free payload and recipe snapshots retain original field rules', () => {
  const s = input(); s.freePrompt = 'free'; s.freeImages = [{ path: '/one' }, { path: '/two' }]; s.modelTargets = ['b2', 'pro'];
  assert.deepEqual(freePayload(s).image_paths, ['/one', '/two']);
  assert.deepEqual(recipeSnapshot({ workflow_mode: 'x', floor_tone: 'warm', last_image_path: '/old', film_width_mm: 20 }), { workflow_mode: 'x', film_width_mm: 20 });
});
test('late lists preserve locally submitted jobs until the server acknowledges them', () => {
  const c = new JobCollection(); const run = c.begin(); c.add([{ job_id: 'new', snapshot_at: 20 }]);
  assert.equal(c.accept(run, []), true); assert.equal(c.values()[0].job_id, 'new');
  c.accept(c.begin(), [{ job_id: 'new', snapshot_at: 10 }]); assert.equal(c.values()[0].snapshot_at, 20);
  c.accept(c.begin(), []); assert.deepEqual(c.values(), []);
});
test('list versions, removal tombstones and teardown reject stale responses', () => {
  const c = new JobCollection(); const old = c.begin(), fresh = c.begin();
  assert.equal(c.accept(old, [{ job_id: 'old' }]), false);
  c.accept(fresh, [{ job_id: 'one' }]); c.remove('one');
  c.accept(c.begin(), [{ job_id: 'one' }]); assert.deepEqual(c.values(), []);
  const late = c.begin(); c.stop(); c.activate(); assert.equal(c.accept(late, [{ job_id: 'late' }]), false);
});
test('duplicate job acknowledgements never produce duplicate cards', () => {
  const c = new JobCollection(); c.add([{ job_id: 'one', snapshot_at: 2 }, { job_id: 'one', snapshot_at: 2 }]); assert.equal(c.values().length, 1); c.add([{ job_id: 'one', snapshot_at: 1 }]);
  c.accept(c.begin(), [{ job_id: 'one', snapshot_at: 3 }, { job_id: 'one', snapshot_at: 3 }]);
  assert.equal(c.values().length, 1); assert.equal(c.values()[0].snapshot_at, 3);
});
test('draft validation retains legacy and nullable fields while discarding malformed values', () => {
  const draft = normalizeDraft({ params: { workflow_mode: 5, style_type: [], floor_tone: 'warm', film_width_mm: null, cn_facilities: null, cn_mode: 'false', avoid_items: [5] }, modelFilter: 'pro', modelTargets: ['b2', 'bad', 'b2'], floor: { path: [] }, freeImages: {}, sdOptions: { steps: 'bad', seed: null } });
  assert.deepEqual(draft.params, { floor_tone: 'warm', film_width_mm: null, cn_facilities: null });
  assert.deepEqual(draft.modelTargets, ['b2']); assert.equal(draft.modelFilter, 'pro'); assert.equal(draft.floor, undefined);
  assert.deepEqual(draft.sdOptions, { seed: null }); assert.equal(normalizeDraft([]), null);
});
