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

test('failed review keeps the dialog and its note', async ({ page }) => {
  await recordsSetup(page);
  await page.route('**/api/records/result/review', route => route.fulfill({ status: 503, json: { detail: 'review unavailable' } }));
  await page.getByRole('button', { name: '标注', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox').fill('keep this note');
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByText('review unavailable', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('textbox')).toHaveValue('keep this note');
});

test('switching files closes dialogs and restores target-specific drafts', async ({ page }) => {
  await recordsSetup(page);
  await page.getByRole('button', { name: '标注', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('draft for A');
  // Native click models a file switch while the modal owns pointer input.
  await page.getByRole('button', { name: 'B.json (1)', exact: true, includeHidden: true }).evaluate((button: HTMLButtonElement) => button.click());
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('img', { name: 'image-B', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '标注', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue('');
  await page.getByRole('dialog').getByRole('textbox').fill('draft for B');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'A.json (1)', exact: true }).click();
  await page.getByRole('button', { name: '标注', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue('draft for A');
});

test('old mutation completion does not select or populate the previous file', async ({ page }) => {
  await recordsSetup(page);
  let respond: (() => Promise<void>) | undefined;
  await page.route('**/api/records/result/favorite', route => { respond = () => route.fulfill({ json: { favorite: true } }); });
  await page.getByRole('button', { name: '☆ 收藏', exact: true }).click();
  await expect.poll(() => !!respond).toBe(true);
  await page.getByRole('button', { name: 'B.json (1)', exact: true }).click();
  await expect(page.getByRole('img', { name: 'image-B', exact: true })).toBeVisible();
  await respond!();
  await expect(page.getByText('已收藏', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'image-B', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'image-A', exact: true })).toHaveCount(0);
});

test('typing during review save survives its acknowledgement and prevents duplicate writes', async ({ page }) => {
  await recordsSetup(page); let respond: (() => Promise<void>) | undefined, writes = 0;
  await page.route('**/api/records/result/review', route => { writes++; respond = () => route.fulfill({ json: { ok: true } }); });
  await page.getByRole('button', { name: '标注', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox').fill('submitted');
  await dialog.getByRole('button', { name: '保存', exact: true }).dblclick();
  await expect.poll(() => writes).toBe(1);
  await dialog.getByRole('textbox').fill('newer edit');
  await respond!();
  await expect(page.getByText('已保存标注', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('textbox')).toHaveValue('newer edit');
});

test('a deleted result cannot return through a stale server read', async ({ page }) => {
  await recordsSetup(page);
  page.once('dialog', prompt => prompt.accept());
  await page.getByRole('button', { name: '删除', exact: true }).last().click();
  await expect(page.getByRole('img', { name: 'image-A', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByRole('img', { name: 'image-A', exact: true })).toHaveCount(0);
});

test('record edit draft survives a file round trip and submission failure', async ({ page }) => {
  await recordsSetup(page);
  await page.getByRole('button', { name: '✎ 二改', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('keep edit A');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'B.json (1)', exact: true }).click();
  await page.getByRole('button', { name: 'A.json (1)', exact: true }).click();
  await page.getByRole('button', { name: '✎ 二改', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue('keep edit A');
  await page.route('**/api/records/edit', route => route.fulfill({ status: 503, json: { detail: 'record edit failed' } }));
  await page.getByRole('dialog').getByRole('button', { name: '提交', exact: true }).click();
  await expect(page.getByText('record edit failed', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue('keep edit A');
});
