"use client";
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import type { PreviewView } from '@/lib/types';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { safeParams, validateGeneration } from './rules';
import type { GenerationState } from './useGenerationState';
export function useGenerationPreview({ store }: GenerationState) {
  const scope = useAsyncScope();
  const [preview, setPreview] = useState<PreviewView | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const current = useRef<{ id: string; running: boolean; wanted: boolean }>({ id: '', running: false, wanted: false });
  const cancelled = useRef(new Set<string>());
  function cancel(id: string) { if (!id || cancelled.current.has(id)) return; cancelled.current.add(id); void api.cancelPreview(id).catch(() => { }); }
  useEffect(() => {
    const owned = current.current; const sent = cancelled.current; return () => {
      owned.wanted = false;
      if (owned.id && owned.running && !sent.has(owned.id)) { sent.add(owned.id); void api.cancelPreview(owned.id).catch(() => { }); }
    };
  }, []);
  function closePreview(open: boolean) {
    setPreviewOpen(open);
    if (open) return;
    scope.invalidate(); current.current.wanted = false;
    if (current.current.running) cancel(current.current.id);
  }
  async function runPreview() {
    if (current.current.wanted) return;
    const snapshot = structuredClone(store.getSnapshot());
    const error = validateGeneration(snapshot, 'preview'); if (error) { toast.warning(error); return; }
    scope.invalidate(); const token = scope.token();
    current.current.id = ''; current.current.running = true; current.current.wanted = true;
    setPreviewOpen(true);
    setPreview({ preview_id: '', status: 'running', stage: '', url: '', thumb: '', error: '' });
    try {
      const { preview_id: id } = await api.createPreview({
        image_path: snapshot.floor!.path,
        room_path: snapshot.roomImg?.path ?? null, ref_path: snapshot.refImg?.path ?? null, params: safeParams(snapshot)
      });
      if (!scope.valid(token) || !current.current.wanted) { cancel(id); return; }
      current.current.id = id;
      setPreview(p => p ? { ...p, preview_id: id } : p);
      let failures = 0;
      const schedule = () => { const timer = setTimeout(() => { release(); void poll(); }, 1000); const release = scope.own(() => clearTimeout(timer)); };
      const poll = async () => {
        const controller = new AbortController(); const release = scope.own(() => controller.abort());
        try {
          const value = await api.previewStatus(id, controller.signal);
          if (!scope.valid(token)) return;
          failures = 0; setPreview(value);
          if (value.status === 'done' || value.status === 'failed') { current.current.running = false; return; }
        } catch {
          if (!scope.valid(token)) return;
          if (++failures >= 3) {
            setPreview({ preview_id: id, status: 'failed', stage: '', url: '', thumb: '', error: '预览状态查询连续失败，已停止刷新；关闭后会请求取消任务' }); return;
          }
        } finally { release(); }
        if (scope.valid(token)) schedule();
      };
      schedule();
    } catch (e) {
      if (!scope.valid(token)) return;
      current.current.running = false;
      setPreview({ preview_id: '', status: 'failed', stage: '', url: '', thumb: '', error: (e as Error).message });
    }
  }
  return { preview, previewOpen, runPreview, closePreview };
}
