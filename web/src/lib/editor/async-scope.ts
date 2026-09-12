"use client";
import { useEffect, useState } from 'react';

export class AsyncScope {
  private epoch = 0;
  private active = true;
  private cleanup = new Set<() => void>();
  token() { return this.epoch; }
  valid(token: number) { return this.active && token === this.epoch; }
  activate() { this.active = true; }
  own(dispose: () => void) { this.cleanup.add(dispose); return () => this.cleanup.delete(dispose); }
  invalidate() { ++this.epoch; const work = [...this.cleanup]; this.cleanup.clear(); work.forEach(dispose => dispose()); }
  dispose() { this.active = false; this.invalidate(); }
  frame(callback: () => void) {
    const token = this.token();
    const id = requestAnimationFrame(() => { release(); if (this.valid(token)) callback(); });
    const release = this.own(() => cancelAnimationFrame(id));
    return id;
  }
  image(src: string): Promise<HTMLImageElement | null> {
    const token = this.token();
    return new Promise(resolve => {
      const image = new Image();
      const finish = (value: HTMLImageElement | null) => { image.onload = image.onerror = null; release(); resolve(value); };
      const release = this.own(() => finish(null));
      image.onload = () => finish(this.valid(token) ? image : null);
      image.onerror = () => finish(null);
      image.src = src;
    });
  }
}

export function useAsyncScope() {
  const [scope] = useState(() => new AsyncScope());
  useEffect(() => { scope.activate(); return () => scope.dispose(); }, [scope]);
  return scope;
}
