import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import Module from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

// Execute the actual TypeScript utilities; no source-text assertions.
function load(relative) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const code = ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const compiledModule = new Module(file);
  compiledModule.paths = Module._nodeModulePaths(fileURLToPath(new URL('..', import.meta.url)));
  compiledModule._compile(code, file);
  return compiledModule.exports;
}
const { createSessionStore } = load('../src/lib/editor/session-store.ts');
const { AsyncScope } = load('../src/lib/editor/async-scope.ts');

test('imperative field access and rendered snapshot have one source of truth', () => {
  const store = createSessionStore({ strength: .7, mode: 'auto' });
  const original = store.getSnapshot();
  const field = store.field('strength');
  field.current = .2;
  assert.equal(store.getSnapshot().strength, .2);
  store.set('strength', n => n + .3);
  assert.equal(field.current, .5);
  assert.equal(original.strength, .7);
});

test('a task transition publishes one coherent snapshot', () => {
  const store = createSessionStore({ phase: 'running', id: 'one', candidates: [] });
  const seen = [];
  store.subscribe(() => seen.push(store.getSnapshot()));
  store.patch({ phase: 'pick', candidates: ['first', 'second'] });
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0], { phase: 'pick', id: 'one', candidates: ['first', 'second'] });
});

test('dispose invalidates old tokens even after strict-mode reactivation', () => {
  const scope = new AsyncScope();
  const old = scope.token(); let disposed = 0;
  scope.own(() => disposed++);
  scope.dispose(); scope.activate();
  assert.equal(disposed, 1);
  assert.equal(scope.valid(old), false);
  assert.equal(scope.valid(scope.token()), true);
});

test('late image decode settles without retaining callbacks after disposal', async t => {
  const previous = globalThis.Image;
  const images = [];
  globalThis.Image = class { constructor() { images.push(this); } };
  t.after(() => { globalThis.Image = previous; });
  const scope = new AsyncScope();
  const promise = scope.image('test');
  scope.dispose();
  assert.equal(await promise, null);
  assert.equal(images[0].onload, null);
  assert.equal(images[0].onerror, null);
});


test('same image on different targets creates distinct editor sessions', () => {
  const { colorSessionKey } = load('../src/components/color-match/types.ts');
  const input = { srcUrl: '/image', imageRel: 'image', refPath: '/ref', refUrl: '/ref' };
  assert.notEqual(colorSessionKey({ ...input, target: { kind: 'job', jobId: 'a', stage: 'b2' } }),
    colorSessionKey({ ...input, target: { kind: 'job', jobId: 'b', stage: 'b2' } }));
});

test('target property order does not reset a live editor', () => {
  const { inpaintSessionKey } = load('../src/components/inpaint/types.ts');
  assert.equal(inpaintSessionKey({ srcUrl: '/image', target: { kind: 'job', jobId: 'a', stage: 'b2', imageRel: 'image' } }),
    inpaintSessionKey({ srcUrl: '/image', target: { imageRel: 'image', stage: 'b2', jobId: 'a', kind: 'job' } }));
});
