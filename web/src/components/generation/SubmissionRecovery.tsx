"use client";
import type { useSubmissionRecovery } from './useSubmissionRecovery';
import { resolved } from './submission-store';

export function SubmissionRecovery({ recovery }: { recovery: ReturnType<typeof useSubmissionRecovery> }) {
  const rows = recovery.intents.filter(row => recovery.showHidden || !row.hidden);
  const pending = rows.filter(row => !resolved(row));
  const recent = rows.filter(resolved).slice(0, 3);
  if (!rows.length && !recovery.recoveryError && !recovery.intents.length) return null;
  return <section aria-label="提交恢复" className="space-y-2 rounded-xl border border-border bg-card p-3 text-xs">
    <div className="flex flex-wrap items-center gap-3">
      <strong>待确认提交 · {pending.length}</strong>
      <button className="underline" onClick={() => void recovery.refreshSubmissions()}>查询提交状态</button>
      <button className="underline" onClick={() => recovery.setShowHidden(!recovery.showHidden)}>{recovery.showHidden ? '收起隐藏项' : '显示隐藏项'}</button>
    </div>
    {recovery.recoveryError && <p role="alert" className="text-destructive">{recovery.recoveryError}</p>}
    {[...pending, ...recent].map(row => <div key={row.id} data-submission-id={row.id} className="space-y-1 border-t border-border pt-2">
      <div className="font-medium">{row.name} · {new Date(row.created).toLocaleString()}</div>
      <p role="status">{row.status === 'accepted' ? '已找到原任务' : row.status === 'unavailable' ? '已受理，任务卡已不可用，请核对历史记录'
        : row.error || (row.status === 'unknown' ? '提交结果未确认，正在查询' : row.status === 'rejected' ? '提交被拒绝，请核对素材和设置' : '尚未受理，可继续本次提交')}</p>
      {row.jobId && <p className="break-all text-muted-foreground">任务：{row.jobId}</p>}
      <div className="flex flex-wrap gap-3">
        {!resolved(row) && <button className="underline disabled:opacity-50" disabled={recovery.recoveryBusy || !row.canContinue} onClick={() => void recovery.continueIntent(row)}>继续本次提交</button>}
        {!resolved(row) && <button className="underline" onClick={() => void recovery.another(row)}>另外创建一次</button>}
        {row.status === 'accepted' && <button className="underline" onClick={() => void recovery.locate(row)}>定位原任务</button>}
        <button className="underline" onClick={() => void recovery.hide(row)}>{row.hidden ? '恢复显示' : '隐藏提示'}</button>
      </div>
    </div>)}
  </section>;
}
