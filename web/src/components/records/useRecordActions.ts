"use client";
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { saveReuseRequest } from '@/lib/draft';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { ResultOperationLock } from '@/lib/results/operation-lock';
import { reviewPayload } from '@/lib/results/identity';
import type { RecordEntry, RecordResult, ReviewStatus } from '@/lib/types';
import type { RecordQuery } from './useRecordQuery';
import type { RecordDialogModel } from './useRecordDialogs';
export function useRecordActions(query: RecordQuery, dialogs: RecordDialogModel) {
  const router = useRouter(), scope = useAsyncScope();
  const [locks] = useState(() => new ResultOperationLock());
  async function operate(path: string, rid: string, result: string | undefined, fn: (notify: (message: string) => void) => Promise<void>) {
    const release = locks.claim(path, rid, result); if (!release) return false;
    const token = scope.token();
    try { await fn(message => { if (scope.valid(token)) toast.success(message); }); if (!scope.valid(token)) return false; await query.afterMutation(path); return scope.valid(token); }
    catch (e) { if (scope.valid(token)) toast.error((e as Error).message); return false; }
    finally { release(); }
  }
  async function doDeleteResult(rid: string, resultId: string) {
    const path = query.store.getSnapshot().active;
    if (!path || !window.confirm('确认删除这张效果图？')) return;
    await operate(path, rid, resultId, async notify => {
      const result = await api.deleteResult(path, rid, resultId); query.markDeleted(path, rid, resultId);
      notify(result.file_deleted ? `已删除记录和图片，释放 ${(result.freed_bytes / 1024 / 1024).toFixed(2)} MB` : result.kept_shared ? '已删除当前引用；图片仍被其他记录使用，已安全保留' : '已删除记录引用');
    });
  }
  async function doDeleteRecord(rid: string) {
    const path = query.store.getSnapshot().active;
    if (!path || !window.confirm('确认删除整条记录（含其所有效果图引用）？')) return;
    await operate(path, rid, undefined, async notify => {
      const result = await api.deleteRecord(path, rid); query.markDeleted(path, rid);
      notify(result.files_deleted ? `已删除记录并回收 ${result.files_deleted} 个无引用文件` : '已删除记录；共享文件已安全保留');
    });
  }
  async function doFav(rid: string, resultId: string) {
    const path = query.store.getSnapshot().active; if (!path) return;
    await operate(path, rid, resultId, async notify => { const r = await api.favoriteResult(path, rid, resultId); notify(r.favorite ? '已收藏' : '已取消收藏'); });
  }
  async function doReview(rid: string, resultId: string, patch: Partial<{ status: ReviewStatus; tags: string[]; note: string; best: boolean }>, source?: RecordResult, path = query.store.getSnapshot().active) {
    if (!path) return false;
    const src = source || query.store.getSnapshot().records.find(r => r.id === rid)?.results?.find(r => r.result_id === resultId);
    if (!src) return false;
    const payload = reviewPayload({ jsonPath: path, recordId: rid, resultId }, src, { review_status: patch.status, review_tags: patch.tags, review_note: patch.note, best: patch.best });
    return operate(path, rid, resultId, async notify => { await api.reviewResult(payload); notify('已保存标注'); });
  }
  function openReviewDialog(rid: string, resultId: string, res: RecordResult) {
    dialogs.setReview({ open: true, rid, resultId, status: res.review_status || 'unreviewed', tags: [...(res.review_tags || [])], note: res.review_note || '', best: !!res.best });
  }
  async function doReviewSubmit() {
    const captured = dialogs.dialogStore.getSnapshot().review;
    const source = { result_id: captured.resultId, review_status: captured.status, review_tags: captured.tags, review_note: captured.note, best: captured.best } as RecordResult;
    if (await doReview(captured.rid, captured.resultId, captured, source, captured.jsonPath)) dialogs.complete('review', captured);
  }
  async function doReveal() {
    const captured = dialogs.dialogStore.getSnapshot().reveal, token = scope.token();
    if (!captured.jsonPath) return;
    try {
      const result = await api.reveal(captured.jsonPath, captured.rid, captured.pw);
      if (scope.valid(token) && dialogs.dialogStore.getSnapshot().reveal === captured) dialogs.setReveal({ ...captured, text: result.text });
    } catch (e) { if (scope.valid(token) && dialogs.dialogStore.getSnapshot().reveal === captured) toast.error((e as Error).message); }
  }
  async function doEditSubmit() {
    const captured = dialogs.dialogStore.getSnapshot().edit;
    if (!captured.jsonPath || !captured.instruction.trim()) return;
    const ok = await operate(captured.jsonPath, captured.rid, captured.resultId, async notify => {
      await api.recordEdit({ json_path: captured.jsonPath!, record_id: captured.rid, result_id: captured.resultId, instruction: captured.instruction.trim(), color_match: captured.colorMatch });
      notify('已提交二改（在「生成」页可看进度，完成后回此刷新）');
    });
    if (ok) dialogs.complete('edit', captured);
  }
  // 复用参数：把这条记录的 gen_context 快照写进一次性回填请求，跳到生成页。
  // 老记录（无 gen_context）不显示入口。floor_tone 沿用快照值，不重新识色。
  function doReuse(r: RecordEntry) {
    const gc = r.gen_context;
    if (r.user_prompt && gc?.free_image_paths?.length) {
      saveReuseRequest({
        params: {
          workflow_mode: "自由创作 (自定义提示词/多图)",
          aspect_ratio: gc.free_options?.aspect_ratio,
          resolution: gc.free_options?.resolution,
        },
        modelFilter: gc.model_filter,
        modelTargets: gc.model_targets,
        freePrompt: r.user_prompt,
        freeImagePaths: gc.free_image_paths,
        freeOptions: gc.free_options,
      });
      toast.success("自由提示词与 Slot 顺序已载入生成页");
      router.push("/");
      return;
    }
    if (!gc?.params) return;
    saveReuseRequest({
      params: gc.params,
      modelFilter: gc.model_filter,
      modelTargets: gc.model_targets,
      sdOptions: gc.sd_options,
      floorPath: gc.image_path,
      roomPath: gc.room_path || undefined,
      refPath: gc.ref_path || undefined,
    });
    toast.success("参数已载入生成页");
    router.push("/");
  }

  return { doDeleteResult, doDeleteRecord, doFav, doReview, openReviewDialog, doReviewSubmit, doReveal, doEditSubmit, doReuse, download: (url: string) => { window.open(url, '_blank'); } };
}
