import { test, expect, type Page } from '@playwright/test';

async function setup(page: Page) {
  const images = await page.evaluate(() => {
    const c = document.createElement('canvas'); c.width = 128; c.height = 96;
    const ctx = c.getContext('2d')!;
    const make = (color: string) => { ctx.fillStyle = color; ctx.fillRect(0, 0, 128, 96); return c.toDataURL(); };
    return { source: make('red'), result: make('blue'), mask: make('white') };
  });
  const job = { job_id: 'editor', display_name: 'Editor baseline', status: 'done', ts: '',
    model_targets: ['b2'], model_filter: 'b2', workflow_mode: '纯效果图', operation_status: 'done',
    b2_url: '/outputs/source.png', b2_thumb: '/outputs/source.png', b2_total: 1, b2_idx: 0,
    pro_url: '', pro_thumb: '', pro_total: 0, pro_idx: 0, floor_url: '/outputs/source.png', floor_path: '/ref.png',
    model_runs: { b2: { key: 'b2', label: 'B2', status: 'done', total: 1, idx: 0, url: '/outputs/source.png',
      thumb: '/outputs/source.png', candidates: [], settings: {} } } };
  const previews: Record<string, unknown>[] = [], saves: Record<string, unknown>[] = [], submits: Record<string, unknown>[] = [];
  page.on('pageerror', error => { throw error; });
  await page.route('**/outputs/**', route => route.fulfill({ contentType: 'image/png', body: Buffer.from(images.source.split(',')[1], 'base64') }));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/options') return route.continue();
    if (path === '/api/jobs') return route.fulfill({ json: [job] });
    if (path === '/api/config') return route.fulfill({ json: { inpaint_provider: 'fal', inpaint_remove_model: 'bria-eraser' } });
    if (path === '/api/color-match/segment') return route.fulfill({ json: { mask_b64: images.mask.split(',')[1], warnings: [], confidence: 1 } });
    if (path === '/api/color-match/preview') {
      previews.push(route.request().postDataJSON());
      return route.fulfill({ json: { preview: images.result, auto_adjustments: { temperature: 0, tint: 0, exposure: 0, contrast: 0, highlights: 0, shadows: 0, whites: 0, blacks: 0, midtones: 0, saturation: 0 } } });
    }
    if (path === '/api/jobs/editor/color-match') { saves.push(route.request().postDataJSON()); return route.fulfill({ json: job }); }
    if (path === '/api/inpaint/segment') return route.fulfill({ json: { width: 128, height: 96, candidates: [], warnings: [] } });
    if (path === '/api/inpaint') { submits.push(route.request().postDataJSON()); return route.fulfill({ json: { inpaint_id: 'edit-task', effective_n: 1 } }); }
    if (path === '/api/inpaint/edit-task') return route.fulfill({ json: { status: 'running', stage: 'testing' } });
    return route.fulfill({ json: [] });
  });
  await page.goto('/');
  return { previews, saves, submits, images };
}

test('color preview strength is local and save preserves effective parameters', async ({ page }) => {
  const { previews, saves } = await setup(page);
  await page.getByRole('button', { name: /校色/ }).first().click();
  const dialog = page.getByRole('dialog');
  const save = dialog.getByRole('button', { name: '保存为新候选', exact: true });
  await expect(save).toBeEnabled();
  const before = previews.length;
  const strength = dialog.getByText('地板自动校准', { exact: true }).locator('..').getByRole('slider').first();
  await strength.focus(); await page.keyboard.press('Home');
  const pixel = () => dialog.locator('canvas').last().evaluate((canvas: HTMLCanvasElement) => Array.from(canvas.getContext('2d')!.getImageData(64, 48, 1, 1).data).slice(0, 3));
  await expect.poll(pixel).toEqual([255, 0, 0]);
  await page.keyboard.press('End');
  await expect.poll(pixel).toEqual([0, 0, 255]);
  expect(previews).toHaveLength(before);
  await save.click();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0]).toMatchObject({ strength: 1, scope: 'floor_mask', algorithm: 'distribution', adjustment_mode: 'auto', stage: 'b2' });
});

test('inpaint brush undo and mode isolation preserve binary mask export', async ({ page }) => {
  const { submits } = await setup(page);
  await page.getByRole('button', { name: /修补/ }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: /画笔/ }).click();
  const canvas = dialog.locator('canvas').last();
  const box = await canvas.boundingBox(); expect(box).not.toBeNull();
  const stroke = async () => {
    await page.mouse.move(box!.x + box!.width * .3, box!.y + box!.height * .5);
    await page.mouse.down(); await page.mouse.move(box!.x + box!.width * .6, box!.y + box!.height * .5, { steps: 5 }); await page.mouse.up();
  };
  const count = () => canvas.evaluate((node: HTMLCanvasElement) => Array.from(node.getContext('2d')!.getImageData(0, 0, node.width, node.height).data).filter((n, i) => i % 4 === 3 && n > 0).length);
  await stroke(); await expect.poll(count).toBeGreaterThan(0);
  await dialog.getByRole('button', { name: /撤销/ }).click(); await expect.poll(count).toBe(0);
  await stroke();
  const removePixels = await count();
  await dialog.getByRole('button', { name: '✨ 生成式添加', exact: true }).click();
  await expect.poll(count).toBe(0);
  await dialog.getByRole('button', { name: '🧹 生成式移除', exact: true }).click();
  await expect.poll(count).toBe(removePixels);
  await dialog.getByRole('button', { name: '移除选中区域 ×1', exact: true }).click();
  await expect.poll(() => submits.length).toBe(1);
  expect(submits[0]).toMatchObject({ mode: 'remove', grow: 8, feather: .01, n: 1 });
  const values = await page.evaluate(async data => {
    const image = new Image(); image.src = `data:image/png;base64,${data}`; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
    return { width: canvas.width, height: canvas.height, values: [...new Set(ctx.getImageData(0, 0, canvas.width, canvas.height).data)] };
  }, String(submits[0].mask_b64));
  expect(values.width).toBe(128); expect(values.height).toBe(96); expect(values.values.sort()).toEqual([0, 255]);
});


async function paintAndSubmit(page: Page) {
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: /画笔/ }).click();
  const box = await dialog.locator('canvas').last().boundingBox();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down(); await page.mouse.move(box!.x + box!.width * .6, box!.y + box!.height / 2); await page.mouse.up();
  await dialog.getByRole('button', { name: '移除选中区域 ×1', exact: true }).click();
}

test('closing inpaint stops a late poll from scheduling another request', async ({ page }) => {
  await setup(page);
  let polls = 0, completed = false;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/inpaint/edit-task', async route => {
    polls++;
    await pending;
    await route.fulfill({ json: { status: 'running', stage: 'late' } }).catch(() => {});
    completed = true;
  });
  await page.getByRole('button', { name: /修补/ }).first().click();
  await paintAndSubmit(page);
  await expect.poll(() => polls).toBe(1);
  await page.clock.install();
  await page.keyboard.press('Escape');
  release();
  await expect.poll(() => completed).toBe(true);
  await page.clock.runFor(7000);
  expect(polls).toBe(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('late created task is cancelled and cannot enter the reopened session', async ({ page }) => {
  await setup(page);
  let submitted = false, cancelled = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/inpaint', async route => {
    submitted = true; await pending;
    await route.fulfill({ json: { inpaint_id: 'late-created', effective_n: 1 } });
  });
  await page.route('**/api/inpaint/late-created/cancel', async route => { cancelled++; await route.fulfill({ json: { cancelled: true } }); });
  await page.getByRole('button', { name: /修补/ }).first().click();
  await paintAndSubmit(page); await expect.poll(() => submitted).toBe(true);
  await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: /修补/ }).first().click();
  release(); await expect.poll(() => cancelled).toBe(1);
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('button', { name: '移除选中区域 ×1', exact: true })).toBeDisabled();
});

test('new color preview wins over an older delayed algorithm response', async ({ page }) => {
  const { images } = await setup(page);
  let requested = 0, oldCompleted = false;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/color-match/preview', async route => {
    const body = route.request().postDataJSON(); requested++;
    if (body.algorithm === 'distribution') await pending;
    await route.fulfill({ json: { preview: body.algorithm === 'classic' ? images.result : images.source,
      auto_adjustments: { temperature: 0, tint: 0, exposure: 0, contrast: 0, highlights: 0, shadows: 0, whites: 0, blacks: 0, midtones: 0, saturation: 0 } } }).catch(() => {});
    if (body.algorithm === 'distribution') oldCompleted = true;
  });
  await page.getByRole('button', { name: /校色/ }).first().click();
  await expect.poll(() => requested).toBe(1);
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '经典 1.0', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '保存为新候选', exact: true })).toBeEnabled();
  const pixel = () => dialog.locator('canvas').last().evaluate((canvas: HTMLCanvasElement) => Array.from(canvas.getContext('2d')!.getImageData(64, 48, 1, 1).data));
  const current = await pixel();
  release(); await expect.poll(() => oldCompleted).toBe(true);
  await expect.poll(pixel).toEqual(current);
  expect(requested).toBe(2);
});


test('inpaint local-write failure preserves candidates and recovers without generating again', async ({ page }) => {
  const { submits } = await setup(page);
  let recovered = 0;
  await page.route('**/api/inpaint/edit-task', route => route.fulfill({ json: { status: 'done', candidates: [{ url: '/outputs/source.png', thumb: '/outputs/source.png' }] } }));
  await page.route('**/api/inpaint/edit-task/apply', route => route.fulfill({ status: 503, json: { detail: {
    code: 'result_commit_pending', commit_id: 'recover-editor', image_saved: true, message: '图片已保留，记录写入未完成' } } }));
  await page.route('**/api/result-commits/recover-editor/retry', route => { recovered++; return route.fulfill({ json: { ok: true, commit_id: 'recover-editor', result_url: '/outputs/source.png' } }); });
  await page.getByRole('button', { name: /修补/ }).first().click();
  await paintAndSubmit(page);
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '✓ 使用这张', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '✓ 使用这张', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '恢复本地写入', exact: true }).click();
  await expect.poll(() => recovered).toBe(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(submits).toHaveLength(1);
});

test('late smart selection cannot overwrite the other mask mode', async ({ page }) => {
  await setup(page);
  let requested = false, completed = false;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/inpaint/segment', async route => {
    if (route.request().postDataJSON().strategy === 'point') {
      requested = true; await pending;
      await route.fulfill({ json: { width: 128, height: 96, warnings: [], candidates: [{ id: 'late', area: 12288, rle: [0, 12288], confidence: 1 }] } }).catch(() => {});
      completed = true;
    } else await route.fulfill({ json: { width: 128, height: 96, warnings: [], candidates: [] } });
  });
  await page.getByRole('button', { name: /修补/ }).first().click();
  const dialog = page.getByRole('dialog');
  const box = await dialog.locator('canvas').last().boundingBox();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect.poll(() => requested).toBe(true);
  await dialog.getByRole('button', { name: '✨ 生成式添加', exact: true }).click();
  release(); await expect.poll(() => completed).toBe(true);
  await dialog.getByRole('button', { name: '🧹 生成式移除', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '移除选中区域 ×1', exact: true })).toBeDisabled();
});


test('color fallback, reference replacement and manual save keep their contracts', async ({ page }) => {
  const { images, saves } = await setup(page);
  const requests: Record<string, unknown>[] = [];
  await page.route('**/api/color-match/preview', async route => {
    const body = route.request().postDataJSON(); requests.push(body);
    await route.fulfill({ json: { preview: body.adjustment_mode === 'manual' ? images.source : images.result,
      auto_adjustments: { temperature: 0, tint: 0, exposure: 0, contrast: 0, highlights: 0, shadows: 0, whites: 0, blacks: 0, midtones: 0, saturation: 0 },
      quality_report: { algorithm: 'classic', applied_illumination_mode: 'off', fallback_reason: 'fixture fallback',
        level: 'low', score: 40, summary: 'fixture', source_usable_ratio: .5, initial_delta_e00: 3, estimated_delta_e00: 1, warnings: [] } } });
  });
  await page.route('**/api/uploads/ref', route => route.fulfill({ json: { path: '/new-ref.png', url: '/outputs/ref.png' } }));
  await page.getByRole('button', { name: /校色/ }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('当前画面：经典 1.0 · 精细 2.0已回退', { exact: false })).toBeVisible();
  await dialog.locator('input[type=file]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from(images.source.split(',')[1], 'base64') });
  await expect.poll(() => requests.at(-1)?.ref_path).toBe('/new-ref.png');
  await expect(dialog.getByRole('button', { name: '保存为新候选', exact: true })).toBeEnabled();
  await dialog.getByRole('button', { name: '全图校准（兼容）', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '保存为新候选', exact: true })).toBeEnabled();
  await dialog.getByRole('button', { name: '保存为新候选', exact: true }).click();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0]).toMatchObject({ scope: 'global', adjustment_mode: 'manual', strength: 0, algorithm: 'distribution', ref_path: '/new-ref.png' });
});
