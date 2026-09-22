import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import Module from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

function load(name) {
  const file = fileURLToPath(new URL(`../src/components/generation/${name}.ts`, import.meta.url));
  const mod = new Module(file);
  mod.require = specifier => specifier === '@/lib/api' ? { api: {} } : load(specifier.replace('./', ''));
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
  return mod.exports;
}
const { canonical, randomId, resolved } = load('submission-store');
const { limitedMap } = load('submission-client');

test('intent comparison ignores object order while preserving image slot order and prompt text', () => {
  assert.equal(canonical({ b: 1, a: { d: 3, c: 2 }, unused: undefined }), canonical({ a: { c: 2, d: 3 }, b: 1 }));
  assert.notEqual(canonical({ image_paths: ['a', 'b'] }), canonical({ image_paths: ['b', 'a'] }));
  assert.notEqual(canonical({ prompt: 'text ' }), canonical({ prompt: 'text' }));
});
test('intent ids are distinct UUID v4 without relying on secure-context randomUUID', () => {
  const values = Array.from({ length: 1000 }, randomId);
  assert.equal(new Set(values).size, values.length);
  for (const value of values) assert.match(value, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});
test('only confirmed acceptance or unavailable cards permit snapshot removal', () => {
  for (const status of ['ready', 'preparing', 'unknown', 'rejected']) assert.equal(resolved({ status }), false);
  for (const status of ['accepted', 'unavailable']) assert.equal(resolved({ status }), true);
});
test('batch scheduling has four slots and retains input order across partial failures', async () => {
  let active = 0, maximum = 0;
  const result = await limitedMap(Array.from({ length: 13 }, (_, index) => index), async value => {
    active++; maximum = Math.max(maximum, active);
    await new Promise(resolve => setTimeout(resolve, (13 - value) % 4));
    active--;
    if (value === 5) throw new Error('one failure');
    return value;
  });
  assert.equal(maximum, 4); assert.equal(active, 0); assert.equal(result[5].status, 'rejected');
  result.forEach((item, index) => { if (index !== 5) assert.equal(item.value, index); });
});
