import type { JobView } from '@/lib/types';
export class JobSnapshot {
  private latest: JobView;
  private stamp: number;
  constructor(initial: JobView) { this.latest = initial; this.stamp = initial.snapshot_at || 0; }
  accept(next: JobView) {
    if (next.job_id !== this.latest.job_id || (next.snapshot_at && next.snapshot_at < this.stamp)) return null;
    const previous = this.latest;
    this.stamp = next.snapshot_at || this.stamp; this.latest = next;
    const wasActive = previous.status === 'queued' || previous.status === 'running';
    const notifications: { status: 'done' | 'partial' | 'failed'; name: string; error: string }[] = [];
    if (wasActive && (next.status === 'done' || next.status === 'partial' || next.status === 'failed')) notifications.push({ status: next.status, name: next.display_name, error: next.error });
    if (!wasActive && previous.operation_status === 'running' && (next.operation_status === 'done' || next.operation_status === 'failed')) notifications.push({ status: next.operation_status, name: `${next.display_name} · ${next.operation}`, error: next.operation_error });
    return { job: next, notifications };
  }
}
