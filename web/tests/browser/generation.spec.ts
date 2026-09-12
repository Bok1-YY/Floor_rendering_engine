import { test, expect, type Page } from '@playwright/test';
async function setup(page: Page, reuse = false, configure?: () => Promise<void>) {
  const submissions: Array<{ image_path: string; params: { room_type: string } }> = [];
  await page.addInitScript(({ reuse }) => {
    localStorage.clear();
    const params = { workflow_mode: '纯效果图 (生成全新空间)', style_type: 'baseline-style', floor_tone: 'baseline-tone' };
    localStorage.setItem('floor-engine:generate-draft:v1', JSON.stringify({ params, modelTargets: ['b2'], floor: { path: '/floor.png', name: 'baseline-floor', url: '/uploads/floor.png', thumb: '/uploads/floor.png' } }));
    if (reuse) localStorage.setItem('floor-engine:reuse-request:v1', JSON.stringify({ params: { ...params, style_type: 'reuse-style' }, floorPath: '/reuse.png', modelTargets: ['pro'] }));
  }, { reuse });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/options') return route.continue();
    if (path === '/api/jobs' && route.request().method() === 'POST') {
      submissions.push(route.request().postDataJSON());
      return route.fulfill({ json: { job_id: `created-${submissions.length}`, status: 'queued', model_targets: ['b2'], model_runs: {}, workflow_mode: '纯效果图', display_name: 'baseline-created' } });
    }
    if (path === '/api/config') return route.fulfill({ json: { sd_enabled: true } });
    return route.fulfill({ json: [] });
  });
  await configure?.();
  await page.goto('/');
  await expect(page.getByRole('button', { name: '生成效果图', exact: true })).toBeEnabled();
  return submissions;
}
test('generation restores draft and submits its parameter snapshot', async ({ page }) => {
  const submissions = await setup(page);
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0]).toMatchObject({ image_path: '/floor.png', model_targets: ['b2'], params: { style_type: 'baseline-style', floor_tone: 'baseline-tone' } });
});
test('history reuse takes priority over ordinary draft and is consumed once', async ({ page }) => {
  const submissions = await setup(page, true);
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0]).toMatchObject({ image_path: '/reuse.png', model_targets: ['pro'], params: { style_type: 'reuse-style' } });
  expect(await page.evaluate(() => localStorage.getItem('floor-engine:reuse-request:v1'))).toBeNull();
});
test('room batch retains per-room request payloads', async ({ page }) => {
  const submissions = await setup(page);
  await page.getByRole('button', { name: '批量', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '全选', exact: true }).click();
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(submissions.length).toBeGreaterThan(1);
  expect(new Set(submissions.map(s => s.params.room_type)).size).toBe(submissions.length);
  expect(submissions.every(s => s.image_path === '/floor.png')).toBeTruthy();
});

test('partial room batch removes successful selections before retry', async ({ page }) => {
  await setup(page);
  const attempts: string[] = [];
  let rejectedRoom = '';
  await page.route('**/api/jobs', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    const room = route.request().postDataJSON().params.room_type; attempts.push(room);
    if (!rejectedRoom) rejectedRoom = room;
    if (attempts.length === 1) return route.fulfill({ status: 422, json: { detail: 'invalid fixture' } });
    return route.fulfill({ json: { job_id: `ok-${attempts.length}`, workflow_mode: '纯效果图', status: 'queued', model_targets: ['b2'], model_runs: {} } });
  });
  await page.getByRole('button', { name: '批量', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '全选', exact: true }).click();
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect(dialog.getByRole('status')).toContainText('已成功项已移出选择');
  const count = attempts.length; expect(count).toBeGreaterThan(1);
  await expect(dialog.getByText('当前已选 1 项')).toBeVisible();
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(attempts.slice(count)).toEqual([rejectedRoom]);
});

test('unknown batch result needs explicit confirmation before a new submission', async ({ page }) => {
  await setup(page);
  let attempts = 0;
  await page.route('**/api/jobs', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    attempts++; return route.abort('failed');
  });
  await page.getByRole('button', { name: '批量', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '全选', exact: true }).click();
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect(dialog.getByRole('status')).toContainText('提交结果未确认');
  const before = attempts;
  page.once('dialog', prompt => prompt.dismiss());
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  expect(attempts).toBe(before);
  page.once('dialog', prompt => prompt.accept());
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect.poll(() => attempts).toBe(before * 2);
});

test('multi-floor batch sends individual analysis with existing fallback', async ({ page }) => {
  const submissions = await setup(page);
  await page.route('**/api/swatches/recent?*', route => route.fulfill({ json: [
    { path: '/floor.png', name: 'first', url: '', thumb: '' }, { path: '/other.png', name: 'second', url: '', thumb: '' },
  ] }));
  await page.route('**/api/floor/analyze?*', route => new URL(route.request().url()).searchParams.get('path') === '/floor.png'
    ? route.fulfill({ json: { tone: 'individual-tone', recipes: [] } }) : route.fulfill({ status: 503, json: { detail: 'unavailable' } }));
  await page.getByRole('button', { name: '批量', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '多地板 × 同场景', exact: true }).click();
  await dialog.getByRole('button', { name: '全选', exact: true }).click();
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(submissions).toHaveLength(2);
  expect(submissions.find(s => s.image_path === '/floor.png')).toMatchObject({ params: { floor_tone: 'individual-tone' } });
  expect(submissions.find(s => s.image_path === '/other.png')).toMatchObject({ params: { floor_tone: 'baseline-tone' } });
});

test('late task-list response cannot hide a just-submitted task', async ({ page }) => {
  const submissions = await setup(page);
  let respond: (() => Promise<void>) | undefined;
  await page.route('**/api/jobs?*', route => { respond = () => route.fulfill({ json: [] }); });
  await page.getByRole('button', { name: '刷新任务', exact: true }).click();
  await expect.poll(() => !!respond).toBe(true);
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  await respond!();
  await expect(page.getByText('进行中 1 · 完成 0/1', { exact: true })).toBeVisible();
});

for (const leavePage of [false, true]) {
  test(`late preview creation is cancelled after ${leavePage ? 'navigation' : 'close'}`, async ({ page }) => {
    await setup(page);
    let respond: (() => Promise<void>) | undefined; let cancellations = 0; let polls = 0;
    await page.route('**/api/preview', route => { respond = () => route.fulfill({ json: { preview_id: 'late-preview' } }); });
    await page.route('**/api/preview/late-preview/cancel', route => { cancellations++; return route.fulfill({ json: { cancelled: true } }); });
    await page.route('**/api/preview/late-preview', route => { polls++; return route.fulfill({ json: { status: 'running' } }); });
    await page.getByRole('button', { name: '预览', exact: true }).click();
    await expect.poll(() => !!respond).toBe(true);
    if (leavePage) {
      await page.locator('a[href="/records/"]').evaluate((link: HTMLAnchorElement) => link.click());
      await expect(page).toHaveURL(/records/);
    } else await page.keyboard.press('Escape');
    await respond!();
    await expect.poll(() => cancellations).toBe(1);
    expect(polls).toBe(0);
  });
}

test('range slider retains two independently editable bounds', async ({ page }) => {
  const submissions = await setup(page);
  await page.getByRole('button', { name: /更多场景约束/ }).click();
  const lower = page.getByRole('slider', { name: '地板最小占比' });
  const upper = page.getByRole('slider', { name: '地板最大占比' });
  await expect(lower).toHaveCount(1); await expect(upper).toHaveCount(1);
  await lower.focus(); await page.keyboard.press('ArrowRight');
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0]).toMatchObject({ params: { floor_coverage_min: 41, floor_coverage_max: 50 } });
});

test('delayed options cannot consume reuse after leaving the workbench', async ({ page }) => {
  const replies: Array<() => Promise<void>> = [];
  const startup = setup(page, true, async () => {
    await page.route('**/api/options', async route => {
      const response = await route.fetch();
      replies.push(() => route.fulfill({ response }));
    });
  });
  await expect.poll(() => replies.length).toBe(1);
  await page.locator('a[href="/records/"]').click();
  await expect(page).toHaveURL(/records/);
  await replies[0]();
  expect(await page.evaluate(() => localStorage.getItem('floor-engine:reuse-request:v1'))).not.toBeNull();
  await page.locator('a[href="/"]').click();
  await expect.poll(() => replies.length).toBe(2);
  await replies[1]();
  const submissions = await startup;
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0]).toMatchObject({ image_path: '/reuse.png', params: { style_type: 'reuse-style' } });
});

test('old recipe save does not close a newly opened recipe dialog', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: /产品 · 地板小样/ }).click();
  await page.getByRole('button', { name: '存为配方', exact: true }).click();
  let respond: (() => Promise<void>) | undefined;
  await page.route('**/api/recipes/custom', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON();
    respond = () => route.fulfill({ json: { id: 'saved', ...body } });
  });
  await page.getByRole('dialog').getByRole('textbox').fill('first recipe');
  await page.getByRole('dialog').getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => !!respond).toBe(true);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '存为配方', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('second recipe');
  await respond!();
  await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue('second recipe');
});

test('batch rejects an empty model selection before any create request', async ({ page }) => {
  const submissions = await setup(page);
  await page.getByRole('button', { name: /输出/ }).click();
  await page.getByRole('button', { name: '✓ B2', exact: true }).click();
  await page.getByRole('button', { name: '批量', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '全选', exact: true }).click();
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect(page.getByText('请至少选择一个生图模型', { exact: true })).toBeVisible();
  expect(submissions).toHaveLength(0);
});

test('closing preview aborts a pending poll without scheduling another', async ({ page }) => {
  await page.clock.install();
  await setup(page);
  let respond: (() => Promise<void>) | undefined, polls = 0, cancellations = 0;
  await page.route('**/api/preview', route => route.fulfill({ json: { preview_id: 'poll-preview' } }));
  await page.route('**/api/preview/poll-preview', route => { polls++; respond = () => route.fulfill({ json: { preview_id: 'poll-preview', status: 'running', stage: 'late' } }); });
  await page.route('**/api/preview/poll-preview/cancel', route => { cancellations++; return route.fulfill({ json: { cancelled: true } }); });
  await page.getByRole('button', { name: '预览', exact: true }).click();
  await page.clock.runFor(1500);
  await expect.poll(() => !!respond).toBe(true);
  await page.keyboard.press('Escape');
  await respond!(); await page.clock.runFor(5000);
  await expect.poll(() => cancellations).toBe(1); expect(polls).toBe(1);
  await expect(page.getByRole('dialog')).not.toBeVisible();
});
