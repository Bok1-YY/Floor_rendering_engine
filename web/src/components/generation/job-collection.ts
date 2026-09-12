import type { JobView } from '@/lib/types';
const newer = (old: JobView | undefined, next: JobView) => old && (old.snapshot_at || 0) > (next.snapshot_at || 0) ? old : next;
export class JobCollection {
  active = true;
  private version = 0;
  private jobs: JobView[] = [];
  private pending = new Map<string, JobView>();
  private removed = new Set<string>();
  activate() { this.active = true; }
  begin() { return ++this.version; }
  valid(run: number) { return this.active && run === this.version; }
  stop() { this.active = false; ++this.version; }
  values() { return this.jobs; }
  accept(run: number, incoming: JobView[]) {
    if (!this.valid(run)) return false;
    const previous = new Map(this.jobs.map(job => [job.job_id, job]));
    const merged = new Map<string, JobView>();
    for (const job of incoming) {
      this.pending.delete(job.job_id);
      if (!this.removed.has(job.job_id)) merged.set(job.job_id, newer(previous.get(job.job_id), job));
    }
    this.jobs = [...this.pending.values(), ...merged.values()];
    return true;
  }
  add(jobs: JobView[]) {
    const merged = new Map(this.jobs.map(job => [job.job_id, job]));
    for (const job of jobs) { this.pending.set(job.job_id, newer(merged.get(job.job_id), job)); merged.delete(job.job_id); }
    this.jobs = [...new Set(jobs.map(job => job.job_id))].map(id => this.pending.get(id)!).concat([...merged.values()]);
  }
  remove(id: string) { this.removed.add(id); this.pending.delete(id); this.jobs = this.jobs.filter(job => job.job_id !== id); }
  clearPendingCompleted() {
    for (const [id, job] of this.pending) if (job.status !== 'queued' && job.status !== 'running' && !job.pro_polishing && job.operation_status !== 'running') this.pending.delete(id);
  }
}
