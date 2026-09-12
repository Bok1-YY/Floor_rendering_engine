import type { RecordEntry, RecordFile, RecordResult } from "@/lib/types";
// 替换类工作流（地板替换 / 墙板·替换）且有房间原图 → 结果图可做前后对比
export function compareBeforeUrl(r: RecordEntry): string {
  const gc = r.gen_context;
  if (!gc?.room_url || !gc.params) return "";
  const wf = gc.params.workflow_mode || "";
  const isReplace =
    wf.includes("地板替换") ||
    (wf.includes("墙板") && (gc.params.panel_submode || "").includes("替换"));
  return isReplace ? gc.room_url : "";
}

export function recordSelection(files: RecordFile[], records: RecordEntry[], search: string, favoriteOnly: boolean, roomFilter: string, reviewFilter: string) {
  const visibleFiles = files.filter(f => (!favoriteOnly || f.favorite_count > 0) && (!search.trim() || (f.json_path.split(/[\\/]/).pop() || '').toLowerCase().includes(search.trim().toLowerCase())));
  const roomCounts: Record<string, number> = {};
  records.forEach(r => { const name = (r.room_type || '').trim(); if (name) roomCounts[name] = (roomCounts[name] || 0) + 1; });
  const resultVisible = (res: RecordResult) => (!favoriteOnly || res.favorite) && (reviewFilter === '__all__' || (reviewFilter === '__best__' ? res.best : (res.review_status || 'unreviewed') === reviewFilter));
  const shownRecords = records.filter(r => roomFilter === '__all__' || (r.room_type || '') === roomFilter)
    .map(r => ({ ...r, results: (r.results || []).map((res, idx) => ({ ...res, __idx: idx })).filter(resultVisible) })).filter(r => r.results.length);
  return { visibleFiles, totalFavorites: files.reduce((sum, f) => sum + f.favorite_count, 0), roomCounts, shownRecords };
}
