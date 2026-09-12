import { test, expect, type Page } from '@playwright/test';
export async function recordsSetup(page: Page) {
  const writes: Record<string, unknown>[] = [];
  const files = ['A', 'B'].map(name => ({ json_path: `/${name}.json`, labels: [name], favorite_count: 0 }));
  const rows = (name: string) => [{ id: `record-${name}`, room_type: '客厅', workflow_mode: '纯效果图', user_prompt: 'fixture',
    gen_context: { params: { workflow_mode: '纯效果图', style_type: 'fixture-style' }, image_path: '/floor.png', model_targets: ['b2'] },
    results: [{ result_id: `result-${name}`, result_url: `/outputs/${name}.png`, model_label: `image-${name}`, review_status: 'unreviewed', favorite: false }] }];
  page.on('pageerror', error => { throw error; });
  await page.route('**/api/**', route => {
    const u = new URL(route.request().url());
    if (u.pathname === '/api/options') return route.continue();
    if (u.pathname === '/api/records') return route.fulfill({ json: files });
    if (u.pathname === '/api/records/load') return route.fulfill({ json: rows(u.searchParams.get('json_path') === '/B.json' ? 'B' : 'A') });
    if (route.request().method() === 'POST') { writes.push(route.request().postDataJSON()); return route.fulfill({ json: { ok: true, favorite: true } }); }
    return route.fulfill({ json: [] });
  });
  await page.goto('/records/');
  await expect(page.getByRole('img', { name: 'image-A', exact: true })).toBeVisible();
  return { writes, rows, files };
}
test('record library switches files and preserves review target payload', async ({ page }) => {
  const { writes } = await recordsSetup(page);
  await page.getByRole('button', { name: 'B.json (1)', exact: true }).click();
  await expect(page.getByRole('img', { name: 'image-B', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '标注', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('baseline note');
  await page.getByRole('dialog').getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ json_path: '/B.json', record_id: 'record-B', result_id: 'result-B', review_note: 'baseline note' });
});
test('record parameter reuse still restores the generation workbench', async ({ page }) => {
  await recordsSetup(page);
  await page.getByRole('button', { name: '⟳ 复用', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('button', { name: '生成效果图', exact: true })).toBeEnabled();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('floor-engine:generate-draft:v1')!).params.style_type)).toBe('fixture-style');
});
