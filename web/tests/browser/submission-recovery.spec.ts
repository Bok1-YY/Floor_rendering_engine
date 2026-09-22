import { test, expect, type Page } from '@playwright/test';

async function setup(page: Page, free = false) {
  const fixture = await (await page.request.post('/__test/reset')).json();
  await page.addInitScript(({ image, free }) => {
    if (localStorage.getItem('r7-seeded')) return;
    localStorage.setItem('r7-seeded', 'yes');
    localStorage.setItem('floor-engine:generate-draft:v1', JSON.stringify({
      floor: { path: image, name: '恢复测试地板', url: '', thumb: '' },
      params: { workflow_mode: free ? '自由创作 (自定义提示词/多图)' : '纯效果图 (生成全新空间)', style_type: '原始风格', floor_tone: '原始色调' },
      modelTargets: ['b2'], freePrompt: '保留这段原始提示词', freeImages: [{ path: image, name: '素材一', url: '', thumb: '' }],
    }));
  }, { image: fixture.image_path, free });
  await page.goto('/');
  await expect(page.getByRole('button', { name: free ? '生成图片' : '生成效果图', exact: true })).toBeEnabled();
  return fixture;
}

async function intents(page: Page) {
  return page.evaluate(() => new Promise<Array<{ id: string; status: string; payload?: { params?: { style_type: string }; prompt?: string }; hidden?: boolean }>>((resolve, reject) => {
    const request = indexedDB.open('floor-engine-submissions', 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => { const db = request.result, read = db.transaction('intents').objectStore('intents').getAll(); read.onsuccess = () => { db.close(); resolve(read.result); }; };
  }));
}

for (const free of [false, true]) {
  test(`lost ${free ? 'free' : 'ordinary'} creation response recovers through real server without another POST`, async ({ page }) => {
    await setup(page, free);
    let posts = 0;
    const endpoint = free ? '**/api/jobs/free' : '**/api/jobs';
    await page.route(endpoint, async route => {
      if (route.request().method() !== 'POST') return route.continue();
      posts++;
      await route.fetch(); // Real route, real receipt, offline worker only.
      await route.abort('failed');
    });
    await page.getByRole('button', { name: free ? '生成图片' : '生成效果图', exact: true }).click();
    await expect.poll(() => posts).toBe(1);
    await expect.poll(async () => (await page.request.get('/__test/submissions')).json().then(data => data.calls.length)).toBe(1);
    await page.reload();
    await expect(page.getByText('已找到原任务', { exact: true })).toBeVisible();
    expect(posts).toBe(1);
    const stored = await intents(page);
    expect(stored).toHaveLength(1); expect(stored[0].payload).toBeUndefined();
    const evidence = await (await page.request.get('/__test/submissions')).json();
    expect(evidence.receipts.find((row: { submission_id: string }) => row.submission_id === stored[0].id).phase).toBe('dispatch_committed');
    if (!free) {
      await page.request.post('/api/jobs/clear-completed');
      await page.getByRole('button', { name: '定位原任务', exact: true }).click();
      await expect(page.getByText('已受理，任务卡已不可用，请核对历史记录')).toBeVisible();
      expect((await (await page.request.get('/__test/submissions')).json()).calls).toHaveLength(1);
    }
  });
}

test('manual continuation retains the original snapshot after form draft changes', async ({ page }) => {
  await setup(page);
  let fail = true;
  const bodies: Array<{ submission_id: string; params: { style_type: string } }> = [];
  await page.route('**/api/jobs', route => {
    if (route.request().method() !== 'POST') return route.continue();
    bodies.push(route.request().postDataJSON());
    return fail ? route.abort('failed') : route.continue();
  });
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => bodies.length).toBe(1);
  await page.evaluate(() => {
    const draft = JSON.parse(localStorage.getItem('floor-engine:generate-draft:v1')!);
    draft.params.style_type = '修改后的风格';
    localStorage.setItem('floor-engine:generate-draft:v1', JSON.stringify(draft));
  });
  await page.reload();
  const resume = page.getByRole('button', { name: '继续本次提交', exact: true });
  await expect(resume).toBeEnabled();
  expect(bodies).toHaveLength(1);
  fail = false;
  await resume.click();
  await expect(page.getByText('已找到原任务', { exact: true })).toBeVisible();
  expect(bodies[1]).toMatchObject({ submission_id: bodies[0].submission_id, params: { style_type: '原始风格' } });
});

test('unavailable IndexedDB blocks all creation requests', async ({ page }) => {
  await page.addInitScript(() => { IDBFactory.prototype.open = () => { throw new Error('storage denied'); }; });
  await setup(page);
  let posts = 0;
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/api/jobs')) posts++; });
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect(page.getByText(/storage denied/).first()).toBeVisible();
  expect(posts).toBe(0);
});

test('older server cannot silently downgrade submission protection', async ({ page }) => {
  await page.route('**/api/healthz', route => route.fulfill({ json: { ok: true } }));
  await setup(page);
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect(page.getByText(/后端不支持当前提交恢复协议/).first()).toBeVisible();
  expect(await intents(page)).toHaveLength(0);
});

test('two tabs continue one saved intent with one real worker', async ({ page, context }) => {
  await setup(page);
  let failedPosts = 0;
  await page.route('**/api/jobs', route => { if (route.request().method() !== 'POST') return route.continue(); failedPosts++; return route.abort('failed'); });
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => failedPosts).toBe(1);
  await expect(page.getByRole('button', { name: '生成效果图', exact: true })).toBeEnabled();
  await page.unroute('**/api/jobs');
  const other = await context.newPage(); await other.goto('/');
  await page.getByRole('button', { name: '查询提交状态' }).click();
  await expect(page.getByRole('button', { name: '继续本次提交', exact: true })).toBeEnabled();
  await expect(other.getByRole('button', { name: '继续本次提交', exact: true })).toBeEnabled();
  await Promise.all([page.getByRole('button', { name: '继续本次提交', exact: true }).click(), other.getByRole('button', { name: '继续本次提交', exact: true }).click()]);
  await expect.poll(async () => (await page.request.get('/__test/submissions')).json().then(data => data.calls.length)).toBe(1);
  expect(await intents(page)).toHaveLength(1);
});

test('corrupt local record blocks a fresh submission instead of forgetting its identity', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => new Promise<void>((resolve, reject) => {
    const request = indexedDB.open('floor-engine-submissions', 1);
    request.onsuccess = () => {
      const db = request.result, tx = db.transaction('intents', 'readwrite');
      tx.objectStore('intents').put({ id: 'broken', version: 99 });
      tx.oncomplete = () => { db.close(); resolve(); }; tx.onerror = () => reject(tx.error);
    };
  }));
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect(page.getByText(/本地提交记录版本不兼容或已损坏/).first()).toBeVisible();
  expect((await (await page.request.get('/__test/submissions')).json()).calls).toHaveLength(0);
});

test('changed data instance retains the original pending intent without sending it', async ({ page }) => {
  await setup(page);
  let posts = 0;
  await page.route('**/api/jobs', route => { if (route.request().method() !== 'POST') return route.continue(); posts++; return route.abort('failed'); });
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => posts).toBe(1);
  await expect(page.getByRole('button', { name: '生成效果图', exact: true })).toBeEnabled();
  await page.route('**/api/healthz', route => route.fulfill({ json: { ok: true, submissions: { version: 1, ready: true, store_id: '22222222-2222-4222-8222-222222222222' } } }));
  await page.reload();
  await expect(page.getByText(/部分提交属于另一套数据目录/)).toBeVisible();
  expect(posts).toBe(1); expect(await intents(page)).toHaveLength(1);
});

test('receipt for a cleared card stays accepted after a lost response', async ({ page }) => {
  await setup(page);
  await page.route('**/api/jobs', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fetch();
    await page.request.post('/api/jobs/clear-completed');
    return route.abort('failed');
  });
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect(page.getByRole('button', { name: '生成效果图', exact: true })).toBeEnabled();
  await page.reload();
  await expect(page.getByText('已受理，任务卡已不可用，请核对历史记录')).toBeVisible();
  await expect(page.getByRole('button', { name: '继续本次提交', exact: true })).toHaveCount(0);
  expect((await (await page.request.get('/__test/submissions')).json()).calls).toHaveLength(1);
});

test('local acknowledgement write failure is recovered after reload without another worker', async ({ page }) => {
  await page.addInitScript(() => {
    if (sessionStorage.getItem('ack-write-failed')) return;
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function(value, key) {
      if (value.status === 'accepted') {
        sessionStorage.setItem('ack-write-failed', 'yes');
        throw new DOMException('simulated quota', 'QuotaExceededError');
      }
      return key === undefined ? put.call(this, value) : put.call(this, value, key);
    };
  });
  await setup(page);
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('ack-write-failed'))).toBe('yes');
  await page.reload();
  await expect(page.getByText('已找到原任务', { exact: true })).toBeVisible();
  expect((await (await page.request.get('/__test/submissions')).json()).calls).toHaveLength(1);
});

test('leaving during floor analysis keeps an unsent intent and never starts a worker', async ({ page }) => {
  const fixture = await setup(page);
  let respond: (() => Promise<void>) | undefined;
  await page.route('**/api/swatches/recent?*', route => route.fulfill({ json: [{ path: fixture.image_path, name: '地板', url: '', thumb: '' }] }));
  await page.route('**/api/floor/analyze?*', route => { respond = () => route.fulfill({ json: { tone: 'late', recipes: [] } }); });
  await page.getByRole('button', { name: '批量', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '多地板 × 同场景', exact: true }).click();
  await dialog.getByRole('button', { name: '提交批量', exact: true }).click();
  await expect.poll(() => !!respond).toBe(true);
  await page.keyboard.press('Escape');
  await page.locator('a[href="/records/"]').click();
  await expect(page).toHaveURL(/records/);
  await respond!();
  await page.locator('a[href="/"]').click();
  await expect(page.getByRole('button', { name: '继续本次提交', exact: true })).toBeEnabled();
  expect((await (await page.request.get('/__test/submissions')).json()).calls).toHaveLength(0);
});

test('hidden pending intent survives reload and an explicit new generation gets another id', async ({ page }) => {
  await setup(page);
  await page.route('**/api/jobs', route => route.request().method() === 'POST' ? route.abort('failed') : route.continue());
  await page.getByRole('button', { name: '生成效果图', exact: true }).click();
  await expect(page.getByRole('button', { name: '隐藏提示', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '生成效果图', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '隐藏提示', exact: true }).click();
  await expect.poll(async () => (await intents(page))[0]?.hidden).toBe(true);
  await page.reload();
  await page.getByRole('button', { name: '显示隐藏项', exact: true }).click();
  await expect(page.getByRole('button', { name: '恢复显示', exact: true })).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '另外创建一次', exact: true }).click();
  await expect.poll(async () => (await intents(page)).length).toBe(2);
  expect(new Set((await intents(page)).map(row => row.id)).size).toBe(2);
});
