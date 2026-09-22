import { api, API, ApiError } from '@/lib/api';
import type { FreeJobSubmit, JobSubmit, JobView } from '@/lib/types';
import { updateIntent, type Intent, resolved } from './submission-store';

export async function submissionStore() {
  const health = await api.health(), protocol = health.submissions;
  if (!protocol || protocol.version !== 1) throw new Error('后端不支持当前提交恢复协议，请更新服务并刷新页面');
  if (!protocol.ready || !protocol.store_id) throw new Error('服务端提交凭据不可用，请检查数据目录');
  return protocol.store_id;
}

export async function checkIntent(row: Intent, signal?: AbortSignal): Promise<{ row: Intent; job?: JobView }> {
  const result = await api.submission(row.id, row.store, signal);
  if (!result || result.submission_id !== row.id || !['not_found', 'prepared', 'accepted', 'job_unavailable'].includes(result.status)) {
    throw new Error('服务返回了无法识别的提交状态，请更新服务后重试查询');
  }
  if (result.status === 'accepted' || result.status === 'job_unavailable') {
    if (!result.job_id) throw new Error('提交凭据缺少任务标识');
    return { row: await updateIntent(row.id, { status: result.status === 'accepted' ? 'accepted' : 'unavailable', jobId: result.job_id, error: '' }), job: result.job ?? undefined };
  }
  return { row: await updateIntent(row.id, { canContinue: result.can_continue,
    status: row.analyze ? 'preparing' : row.status === 'rejected' ? 'rejected' : 'ready',
    error: row.status === 'rejected' ? row.error : '' }) };
}

export async function sendIntent(input: Intent, valid: () => boolean): Promise<{ row: Intent; job?: JobView }> {
  let row = input;
  if (!valid()) return { row };
  const instance = await submissionStore();
  if (row.backend !== API || row.store !== instance) throw new Error('当前数据目录与原提交不同，已停止补发');
  if (resolved(row)) return { row };
  // Manual continuation always checks the authoritative receipt first.
  const found = await checkIntent(row); row = found.row;
  if (resolved(row)) return found;
  if (!row.canContinue) throw new Error('本次提交仍在受理，请稍后查询');
  if (!valid()) return { row };
  if (row.analyze) {
    const payload = structuredClone(row.payload!) as JobSubmit;
    try { payload.params.floor_tone = (await api.floorAnalyze(row.analyze)).tone || payload.params.floor_tone; } catch { /* Established tone fallback. */ }
    if (!valid()) return { row };
    row = await updateIntent(row.id, { payload, analyze: undefined, status: 'ready' });
    if (resolved(row)) return { row };
  }
  if (!valid()) return { row };
  row = await updateIntent(row.id, { status: 'unknown', canContinue: false, error: '正在确认提交结果' });
  if (!valid() || resolved(row)) return { row };
  const body = { ...structuredClone(row.payload!), submission_id: row.id, submission_store_id: row.store };
  try {
    const job = row.kind === 'free' ? await api.createFreeJob(body as FreeJobSubmit) : await api.createJob(body as JobSubmit);
    if (!job?.job_id || job.submission_id !== row.id) throw new Error('任务响应无法确认，请查询原提交');
    return { row: await updateIntent(row.id, { status: 'accepted', jobId: job.job_id, error: '' }), job };
  } catch (error) {
    if (error instanceof ApiError && error.code === 'submission_job_unavailable') {
      const detail = error.detail as { job_id: string };
      return { row: await updateIntent(row.id, { status: 'unavailable', jobId: detail.job_id, error: '' }) };
    }
    const definite = error instanceof ApiError && [400, 422, 429].includes(error.status);
    await updateIntent(row.id, { status: definite ? 'rejected' : 'unknown', canContinue: false,
      error: (error as Error).message || '提交结果未确认' });
    throw error;
  }
}

export async function limitedMap<T, R>(values: T[], action: (value: T) => Promise<R>): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(values.length); let index = 0;
  await Promise.all(Array.from({ length: Math.min(4, values.length) }, async () => {
    while (index < values.length) {
      const current = index++;
      try { results[current] = { status: 'fulfilled', value: await action(values[current]) }; }
      catch (reason) { results[current] = { status: 'rejected', reason }; }
    }
  }));
  return results;
}
