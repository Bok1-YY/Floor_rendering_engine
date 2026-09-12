"use client";
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { reportCommitError } from '@/lib/result-commit';
import { toast } from 'sonner';

export function PendingResultCommits() {
  const [pending, setPending] = useState<Awaited<ReturnType<typeof api.pendingResultCommits>>>([]);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const rows = await api.pendingResultCommits();
        if (!stopped) setPending(Array.isArray(rows) ? rows : []);
      } catch { /* Keep the last known recovery links through an offline interval. */ }
      if (!stopped) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  if (!pending.length) return null;
  return <div className="max-h-36 shrink-0 overflow-y-auto border-b border-amber-300 bg-amber-50 p-3 text-xs text-amber-950" role="status">
    <b>有 {pending.length} 个本地结果待写入，图片已保留。</b>
    {pending.map(row => <div key={row.commit_id} className="mt-2 flex items-center gap-3">
      <span>{row.label}</span>
      <a href={api.imgUrl(row.result_url)} target="_blank" rel="noreferrer" className="underline">查看已保存图片</a>
      {row.can_restore ? <button disabled={busy !== null} className="rounded border px-2 py-1 disabled:opacity-50" onClick={async () => {
        setBusy(row.commit_id);
        try {
          await api.retryResultCommit(row.commit_id);
          setPending(items => items.filter(item => item.commit_id !== row.commit_id));
          window.dispatchEvent(new Event('floor-result-restored'));
          toast.success('本地结果已恢复写入，未再次调用模型');
        } catch (error) { reportCommitError(error); }
        finally { setBusy(null); }
      }}>{busy === row.commit_id ? '正在恢复…' : '恢复本地写入'}</button> : <span>原任务卡已移除，请先保存图片</span>}
    </div>)}
  </div>;
}
