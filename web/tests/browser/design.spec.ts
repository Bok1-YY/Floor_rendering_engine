import { test, expect, type Page } from '@playwright/test';

function project(id: string, name: string) {
  return {
    project_id: id, source_name: name, revision: 1, status: 'analyzing_plan', stage: '识别中', error: '',
    source_hash: id, normalized_url: '', normalization: { aspect_ratio: '4:3' }, candidates: [], model_runs: [], bundles: [], paid_previews: {},
    anchor_verification: { status: 'not_run', changes: [], conflicts: [], inferred_anchor_gaps: [] },
    plan_summary_confirmed: true, anchor_set: { anchors: [], confirmed_complete: false },
    plan_summary: { source: 'human', rooms: [], room_count: 0, review_items: [], annotation_boxes: [],
      declared_layout: { bedrooms: 2, halls: 1, bathrooms: 1, source_text: '', confidence: 1 },
      declared_area_m2: 100, overall_dimensions_mm: { width: 10000, depth: 10000, evidence: [], confidence: 1 },
      entrances: [], openings_summary: [], wet_zones: [], balconies: [], dimension_evidence: [], must_preserve: [], uncertainties: [] },
    brief: { requirements_text: `${name} requirements`, reference_paths: [], reference_hashes: [] },
    structure_review: { status: 'not_run', questions: [], answers: {}, unresolved: [] },
  };
}

async function setup(page: Page, detail: (id: string) => Promise<unknown>) {
  page.on('pageerror', error => { throw error; });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/config') return route.fulfill({ json: { has_gemini_key: true, secret_backend: { name: 'test', available: true }, secret_sources: { gemini: 'environment', fal: '', deepseek: '' }, image_provider: 'google', speed_profile: 'fast', max_concurrent_per_model: 1 } });
    if (url.pathname === '/api/whole-home-design/projects') return route.fulfill({ json: [project('a', 'Project A'), project('b', 'Project B')] });
    const id = url.pathname.match(/\/projects\/([ab])$/)?.[1];
    if (id) {
      const value = await detail(id);
      await route.fulfill({ json: value }).catch(() => {}); // A superseded request may be aborted.
      return;
    }
    await route.fulfill({ json: {} });
  });
  await page.goto('/design/');
}

test('background refresh preserves unsaved design requirements', async ({ page }) => {
  let reads = 0;
  await setup(page, async id => { reads++; return project(id, `Project ${id.toUpperCase()}`); });
  const requirements = page.getByPlaceholder(/例如：全屋现代暖木自然风/);
  await expect(requirements).toHaveValue('Project A requirements');
  await requirements.fill('My unsaved local draft');
  const before = reads;
  await expect.poll(() => reads).toBeGreaterThan(before);
  await expect(requirements).toHaveValue('My unsaved local draft');
});

test('late response from previous project cannot change current selection', async ({ page }) => {
  let readsA = 0;
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await setup(page, async id => {
    if (id === 'a' && ++readsA > 1) await delayed;
    return project(id, `Project ${id.toUpperCase()}`);
  });
  await expect.poll(() => readsA).toBeGreaterThan(1);
  await page.getByRole('button', { name: /Project B/ }).click();
  const requirements = page.getByPlaceholder(/例如：全屋现代暖木自然风/);
  await expect(requirements).toHaveValue('Project B requirements');
  release();
  await expect(requirements).toHaveValue('Project B requirements');
});

test('structured queue error remains readable and preserves the draft', async ({ page }) => {
  await setup(page, async id => project(id, `Project ${id.toUpperCase()}`));
  await page.route('**/projects/a/brief', route => route.fulfill({
    status: 429, headers: { 'Retry-After': '5' }, json: { detail: { code: 'queue_full', message: '任务队列已满，请稍后再试' } },
  }));
  const requirements = page.getByPlaceholder(/例如：全屋现代暖木自然风/);
  await requirements.fill('Keep this draft after failure');
  await page.getByRole('button', { name: /保存需求|保存设计要求/ }).click();
  await expect(page.getByText('任务队列已满，请稍后再试', { exact: false })).toBeVisible();
  await expect(requirements).toHaveValue('Keep this draft after failure');
});

test('Python static hosting supports navigation and deep-link reload', async ({ page }) => {
  await setup(page, async id => project(id, `Project ${id.toUpperCase()}`));
  const failures: string[] = [];
  page.on('response', response => { if (response.status() === 404 && response.url().includes('__next')) failures.push(response.url()); });
  await page.getByRole('link', { name: /设置/ }).click();
  await expect(page).toHaveURL(/\/settings\/?$/);
  await page.reload();
  await expect(page.getByText('安全后端：', { exact: false })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'This page couldn’t load' })).toHaveCount(0);
  await expect(page.getByText('404', { exact: true })).toHaveCount(0);
  expect(failures).toEqual([]);
});


test('version conflict can be rebased without discarding local edits', async ({ page }) => {
  let current = project('a', 'Project A');
  await setup(page, async id => id === 'a' ? current : project(id, 'Project B'));
  const revisions: number[] = [];
  await page.route('**/projects/a/brief', async route => {
    const body = route.request().postDataJSON();
    revisions.push(body.base_revision);
    if (revisions.length === 1) {
      current = { ...current, revision: 2 };
      await route.fulfill({ status: 409, json: { detail: { code: 'revision_conflict', message: '项目已更新' } } });
    } else {
      current = { ...current, revision: 3, brief: { ...current.brief, requirements_text: body.requirements_text } };
      await route.fulfill({ json: current });
    }
  });
  const requirements = page.getByPlaceholder(/例如：全屋现代暖木自然风/);
  await expect(requirements).toHaveValue('Project A requirements');
  await requirements.fill('Preserved through conflict');
  await page.getByRole('button', { name: '保存设计要求' }).click();
  await page.getByRole('button', { name: '载入最新版本' }).click();
  await expect(requirements).toHaveValue('Preserved through conflict');
  await page.getByRole('button', { name: '保存设计要求' }).click();
  await expect.poll(() => revisions).toEqual([1, 2]);
});


test('typing during save is not overwritten by the save acknowledgement', async ({ page }) => {
  let current = project('a', 'Project A');
  let submitted = false;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await setup(page, async id => id === 'a' ? current : project(id, 'Project B'));
  await page.route('**/projects/a/brief', async route => {
    const body = route.request().postDataJSON();
    submitted = true;
    await pending;
    current = { ...current, revision: 2, brief: { ...current.brief, requirements_text: body.requirements_text } };
    await route.fulfill({ json: current });
  });
  const requirements = page.getByPlaceholder(/例如：全屋现代暖木自然风/);
  await expect(requirements).toHaveValue('Project A requirements');
  await requirements.fill('Submitted text');
  await page.getByRole('button', { name: '保存设计要求' }).click();
  await expect.poll(() => submitted).toBe(true);
  await requirements.fill('Newer unsaved text');
  release();
  await expect(page.getByText('设计要求已保存；修改要求会使旧候选自动过期')).toBeVisible();
  await expect(requirements).toHaveValue('Newer unsaved text');
});
