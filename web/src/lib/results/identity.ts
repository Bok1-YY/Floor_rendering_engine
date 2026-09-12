import type { RecordEntry, RecordResult, ResultReviewPatch } from '@/lib/types';
export interface ResultTarget { jsonPath: string; recordId: string; resultId: string }
export const targetKey = (t: ResultTarget) => JSON.stringify([t.jsonPath, t.recordId, t.resultId]);
export function resourcePath(value: string): string {
  let path = value.replace(/\\/g, '/').split(/[?#]/)[0];
  if (/^https?:\/\//i.test(path)) { try { path = new URL(path).pathname; } catch { return ''; } }
  try { return decodeURIComponent(path); } catch { return path; }
}
export function locateResult(records: RecordEntry[], recordId: string, url: string): RecordResult | null {
  const results = records.find(r => r.id === recordId)?.results || [];
  const path = resourcePath(url);
  if (!path) return null;
  const exact = results.filter(r => resourcePath(r.result_url || '') === path);
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) throw new Error('存在多个相同路径的结果，无法唯一定位');
  const name = path.split('/').pop();
  const legacy = results.filter(r => resourcePath(r.result_url || '').split('/').pop() === name);
  if (legacy.length > 1) throw new Error('存在同名图片，无法唯一定位当前结果');
  return legacy[0] || null;
}
export function reviewPayload(t: ResultTarget, source: RecordResult, patch: Partial<ResultReviewPatch>): ResultReviewPatch {
  return { json_path: t.jsonPath, record_id: t.recordId, result_id: t.resultId,
    review_status: patch.review_status ?? source.review_status ?? 'unreviewed',
    review_tags: patch.review_tags ?? source.review_tags ?? [], review_note: patch.review_note ?? source.review_note ?? '', best: patch.best ?? !!source.best };
}
