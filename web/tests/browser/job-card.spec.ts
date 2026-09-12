import { test, expect, type Page } from '@playwright/test';
async function setup(page: Page, block = false) {
  const models = ['b2', 'pro'];
  const job = { job_id: 'card', display_name: 'Candidate fixture', workflow_mode: '纯效果图', status: 'done', snapshot_at: 1,
    json_path: '/card.json', record_id: 'record', model_targets: models, b2_url: '/outputs/b2-0.png', pro_url: '/outputs/pro-0.png', has_retry: true,
    model_runs: Object.fromEntries(models.map(key => [key, { key, label: key === 'b2' ? 'B2' : 'Pro', status: 'done', total: 6, idx: 0, url: `/outputs/${key}-0.png`, thumb: `/outputs/${key}-0.png`, settings: {} }])) };
  const reads: string[] = [], replies: Array<() => Promise<void>> = [], writes: Record<string, unknown>[] = [];
  let inFlight = 0, maxInFlight = 0, recordReads = 0;
  page.on('pageerror', error => { throw error; });
  await page.route('**/api/**', route => {
    const u = new URL(route.request().url());
    if (u.pathname === '/api/options') return route.continue();
    if (u.pathname === '/api/jobs') return route.fulfill({ json: [job] });
    if (u.pathname === '/api/jobs/card/result') {
      const model = u.searchParams.get('model')!, idx = Number(u.searchParams.get('idx'));
      reads.push(`${model}:${idx}`); inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
      const reply = async () => { inFlight--; await route.fulfill({ json: { idx, total: 6, model, url: `/outputs/${model}-${idx}.png`, thumb: `/outputs/${model}-${idx}.png` } }); };
      if (block) { replies.push(reply); return; } return reply();
    }
    if (u.pathname === '/api/records/load') {
      recordReads++;
      return route.fulfill({ json: [{ id: 'record', results: models.flatMap(model => Array.from({ length: 6 }, (_, idx) => ({ result_id: `${model}-${idx}`, result_url: `/outputs/${model}-${idx}.png`, review_status: 'unreviewed', review_tags: ['existing'], review_note: 'keep note' }))) }] });
    }
    if (route.request().method() === 'POST') { writes.push(route.request().postDataJSON() || {}); return route.fulfill({ json: { favorite: true } }); }
    return route.fulfill({ json: [] });
  });
  await page.goto('/');
  await expect(page.getByText('Candidate fixture', { exact: true })).toBeVisible();
  return { job, reads, replies, writes, max: () => maxInFlight, recordReads: () => recordReads };
}

test('candidate prefetch uses four slots and reuses cached models', async ({ page }) => {
  const state = await setup(page, true);
  await expect.poll(() => state.replies.length).toBe(4);
  await state.replies[0](); await expect.poll(() => state.replies.length).toBe(5);
  await state.replies[1](); await expect.poll(() => state.replies.length).toBe(6);
  await Promise.all(state.replies.slice(2).map(reply => reply()));
  await expect(page.getByRole('img', { name: 'B2 候选 6', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Pro · 6 张', exact: true }).click();
  await expect.poll(() => state.replies.length).toBe(10);
  await Promise.all(state.replies.slice(6, 10).map(reply => reply()));
  await expect.poll(() => state.replies.length).toBe(12);
  await Promise.all(state.replies.slice(10).map(reply => reply()));
  await page.getByRole('button', { name: 'B2 · 6 张', exact: true }).click();
  await expect(page.getByRole('img', { name: 'B2 候选 6', exact: true })).toBeVisible();
  expect(state.reads).toHaveLength(12); expect(state.max()).toBeLessThanOrEqual(4);
});

test('candidate review uses the selected result ID and shares the record read', async ({ page }) => {
  const state = await setup(page);
  await page.getByRole('img', { name: 'B2 候选 3', exact: true }).locator('..').click();
  await expect(page.getByRole('link', { name: '大图', exact: true })).toHaveAttribute('href', /b2-2\.png$/);
  await page.getByRole('button', { name: '通过', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toMatchObject({ json_path: '/card.json', record_id: 'record', result_id: 'b2-2', review_tags: ['existing'], review_note: 'keep note', review_status: 'pass' });
  await page.getByRole('img', { name: 'B2 候选 2', exact: true }).locator('..').click();
  await page.getByRole('button', { name: '收藏', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1]).toMatchObject({ result_id: 'b2-1' });
  expect(state.recordReads()).toBe(1);
});

test('job edit failure preserves input and a late success cannot erase newer text', async ({ page }) => {
  const state = await setup(page);
  await page.route('**/api/jobs/card/edit', route => route.fulfill({ status: 503, json: { detail: 'edit failed' } }));
  await page.getByRole('button', { name: '二改', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox').fill('first');
  await dialog.getByRole('button', { name: '提交', exact: true }).click();
  await expect(page.getByText('edit failed', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('textbox')).toHaveValue('first');
  let reply: (() => Promise<void>) | undefined, calls = 0;
  await page.route('**/api/jobs/card/edit', route => { calls++; reply = () => route.fulfill({ json: { ...state.job, snapshot_at: 2 } }); });
  await dialog.getByRole('button', { name: '提交', exact: true }).dblclick();
  await expect.poll(() => calls).toBe(1);
  await dialog.getByRole('textbox').fill('newer');
  await reply!(); await expect(page.getByText('已提交二改', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('textbox')).toHaveValue('newer');
});
