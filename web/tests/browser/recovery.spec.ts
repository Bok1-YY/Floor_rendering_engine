import { test, expect, type Page } from '@playwright/test';

function sdJob(action: 'resume' | 'confirm') {
  return {
    job_id: 'job_sd', display_name: 'SD recovery', ts: '12:00', status: 'partial',
    model_targets: ['sd35'], model_filter: 'sd35', workflow_mode: '纯效果图',
    operation: 'sd_upscale', operation_status: 'failed', operation_retry_safety: 'ambiguous',
    error: '超分结果不确定', operation_error: '超分结果不确定', has_retry: true,
    b2_total: 0, pro_total: 0, b2_idx: 0, pro_idx: 0, b2_url: '', pro_url: '',
    model_runs: { sd35: { key: 'sd35', label: 'SD 3.5', status: 'partial', delivery_status: 'upscale_failed',
      recovery_action: action, retry_safety: 'ambiguous', may_have_been_billed: true,
      total: 0, idx: 0, candidates: [], settings: {}, url: '', thumb: '', base_url: '' } },
  };
}

async function setup(page: Page, action: 'resume' | 'confirm') {
  let job = sdJob(action);
  const submissions: { url: string; body: Record<string, unknown> }[] = [];
  page.on('pageerror', error => { throw error; });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/options') return route.continue();
    if (path === '/api/config') return route.fulfill({ json: { sd_enabled: true } });
    if (path === '/api/jobs' && route.request().method() === 'GET') return route.fulfill({ json: [job] });
    if (path === '/api/jobs/job_sd/sd-upscale') {
      submissions.push({ url: path, body: route.request().postDataJSON() });
      job = { ...job, status: 'done', operation_status: 'done',
        model_runs: { sd35: { ...job.model_runs.sd35, status: 'done', delivery_status: 'upscaled' } } };
      return route.fulfill({ json: job });
    }
    if (path === '/api/healthz') return route.fulfill({ json: { ok: true } });
    return route.fulfill({ json: [] });
  });
  await page.goto('/');
  return submissions;
}

test('existing upscale request resumes without duplicate-charge confirmation', async ({ page }) => {
  const submissions = await setup(page, 'resume');
  page.on('dialog', () => { throw new Error('resuming a request must not ask for new billing'); });
  await page.getByRole('button', { name: '恢复已有超分请求' }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0].body.confirm_possible_duplicate_charge).toBe(false);
});

test('new uncertain upscale requires explicit confirmation', async ({ page }) => {
  const submissions = await setup(page, 'confirm');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '重新超分（可能重复计费）' }).click();
  expect(submissions).toHaveLength(0);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '重新超分（可能重复计费）' }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0].body.confirm_possible_duplicate_charge).toBe(true);
});

test('saved record can recover after reload using only the local commit endpoint', async ({ page }) => {
  let pending = [{ commit_id: 'abc', label: '修补结果', result_url: '/saved.png', can_restore: true }];
  const writes: string[] = [];
  page.on('pageerror', error => { throw error; });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') writes.push(path);
    if (path === '/api/config') return route.fulfill({ json: { has_gemini_key: false } });
    if (path === '/api/result-commits') return route.fulfill({ json: pending });
    if (path === '/api/result-commits/abc/retry') {
      pending = [];
      return route.fulfill({ json: { ok: true, commit_id: 'abc', result_url: '/saved.png' } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto('/design/');
  await page.reload();
  await expect(page.getByText('有 1 个本地结果待写入，图片已保留。')).toBeVisible();
  await page.getByRole('button', { name: '恢复本地写入', exact: true }).click();
  await expect(page.getByText('本地结果已恢复写入，未再次调用模型')).toBeVisible();
  expect(writes).toEqual(['/api/result-commits/abc/retry']);
  await expect(page.getByText('有 1 个本地结果待写入，图片已保留。')).toHaveCount(0);
});
