import type { ModelKey } from '@/lib/types';
export type Candidate = { idx: number; url: string; thumb: string };
type Work = { key: string; model: ModelKey; idx: number; version: number; controller: AbortController; resolve: (v: Candidate | null) => void; reject: (e: unknown) => void };
/** One per card: all foreground/background requests share the four-request budget. */
export class CandidateCache {
  private version = 0;
  private running = 0;
  private stopped = false;
  private queue: Work[] = [];
  private pending = new Map<string, Promise<Candidate | null>>();
  private controllers = new Set<AbortController>();
  private cache = new Map<string, Candidate>();
  constructor(private fetch: (model: ModelKey, idx: number, signal: AbortSignal) => Promise<Candidate>, private changed: () => void) { }
  values(model: ModelKey) { return [...this.cache.entries()].filter(([key]) => key.startsWith(model + ':')).map(([, value]) => value).sort((a, b) => a.idx - b.idx); }
  activate() { this.stopped = false; }
  read(model: ModelKey, idx: number): Promise<Candidate | null> {
    if (this.stopped) return Promise.resolve(null);
    const key = `${model}:${idx}`, cached = this.cache.get(key);
    if (cached) return Promise.resolve(cached);
    const existing = this.pending.get(key); if (existing) return existing;
    let resolve!: Work['resolve'], reject!: Work['reject'];
    const promise = new Promise<Candidate | null>((yes, no) => { resolve = yes; reject = no; });
    this.pending.set(key, promise);
    this.queue.push({ key, model, idx, version: this.version, controller: new AbortController(), resolve, reject });
    this.pump(); return promise;
  }
  private pump() {
    while (!this.stopped && this.running < 4 && this.queue.length) {
      const work = this.queue.shift()!; this.running++; this.controllers.add(work.controller);
      this.fetch(work.model, work.idx, work.controller.signal).then(value => {
        if (work.version !== this.version || this.stopped) { work.resolve(null); return; }
        this.cache.set(work.key, value); this.changed(); work.resolve(value);
      }).catch(error => { if (work.version !== this.version || this.stopped) work.resolve(null); else work.reject(error); })
        .finally(() => { this.running--; this.controllers.delete(work.controller); if (work.version === this.version) this.pending.delete(work.key); this.pump(); });
    }
  }
  load(model: ModelKey, total: number, selected: number) {
    const indices = [...new Set([selected, ...Array.from({ length: total }, (_, i) => i)])].filter(i => i >= 0 && i < total);
    indices.forEach(idx => { void this.read(model, idx).catch(() => { }); });
  }
  invalidate() {
    ++this.version; this.controllers.forEach(c => c.abort()); this.queue.splice(0).forEach(work => work.resolve(null)); this.pending.clear(); this.cache.clear(); this.changed();
  }
  stop() { this.stopped = true; this.invalidate(); }
}
