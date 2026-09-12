/** Record-wide operations conflict with every result; different results remain independent. */
export class ResultOperationLock {
  private held = new Map<string, Set<string>>();
  claim(path: string, record: string, result = '*'): (() => void) | null {
    const key = JSON.stringify([path, record]), held = this.held.get(key) || new Set<string>();
    if (held.has('*') || held.has(result) || (result === '*' && held.size)) return null;
    held.add(result); this.held.set(key, held);
    return () => { held.delete(result); if (!held.size) this.held.delete(key); };
  }
}
