import type { FreeJobSubmit, JobSubmit } from '@/lib/types';

export type Payload = JobSubmit | FreeJobSubmit;
export type Intent = {
  version: 1; id: string; store: string; backend: string; kind: 'job' | 'free';
  name: string; created: number; updated: number; batch?: string; item?: string;
  payload?: Payload; match: string; analyze?: string;
  status: 'preparing' | 'ready' | 'unknown' | 'rejected' | 'accepted' | 'unavailable';
  jobId?: string; error?: string; hidden?: boolean; canContinue?: boolean;
};
export const resolved = (row: Intent) => row.status === 'accepted' || row.status === 'unavailable';

export function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.entries(value).filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => JSON.stringify(k) + ':' + canonical(v)).join(',') + '}';
  return JSON.stringify(value);
}

export function randomId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function validate(row: Intent) {
  const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
  if (row.version !== 1 || !uuid.test(row.id) || !uuid.test(row.store)
      || typeof row.backend !== 'string' || !Number.isFinite(row.created)
      || !['job', 'free'].includes(row.kind) || typeof row.name !== 'string'
      || !['preparing', 'ready', 'unknown', 'rejected', 'accepted', 'unavailable'].includes(row.status)
      || (!resolved(row) && (!row.payload || row.payload.api_key || typeof row.match !== 'string'))) {
    throw new Error('本地提交记录版本不兼容或已损坏；为避免重复生成，已停止新提交');
  }
}

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('floor-engine-submissions', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('intents', { keyPath: 'id' });
    request.onerror = () => reject(new Error('无法打开提交记录存储，请检查浏览器存储权限'));
    request.onblocked = () => reject(new Error('提交记录存储升级被其他页面阻止，请关闭旧页面'));
    request.onsuccess = () => resolve(request.result);
  });
}

async function transaction<T>(action: (rows: Intent[], store: IDBObjectStore) => T): Promise<T> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('intents', 'readwrite'), store = tx.objectStore('intents');
    let value: T, failure: unknown;
    const request = store.getAll();
    request.onsuccess = () => {
      try {
        const rows = request.result as Intent[]; rows.forEach(validate);
        value = action(rows, store);
      } catch (error) { failure = error; tx.abort(); }
    };
    tx.oncomplete = () => { db.close(); resolve(value); };
    tx.onabort = tx.onerror = () => { db.close(); reject(failure || new Error('提交记录保存失败；未安全保存的请求不会发送')); };
  });
}

export async function listIntents(backend: string) {
  return transaction((rows, store) => {
    const expiry = Date.now() - 30 * 86400000;
    rows.filter(row => resolved(row) && row.updated < expiry).forEach(row => store.delete(row.id));
    return rows.filter(row => row.backend === backend && !(resolved(row) && row.updated < expiry))
      .sort((a, b) => b.created - a.created);
  });
}

export async function reserveIntents(inputs: Array<Omit<Intent, 'version' | 'id' | 'created' | 'updated' | 'match' | 'status'>>, forceNew = false) {
  return transaction((rows, store) => inputs.map(input => {
    const payload = structuredClone(input.payload!);
    delete payload.api_key; delete payload.submission_id; delete payload.submission_store_id;
    const match = canonical({ kind: input.kind, payload });
    const old = !forceNew && rows.find(row => !resolved(row) && row.backend === input.backend && row.store === input.store && row.match === match);
    if (old) { const visible = { ...old, hidden: false }; store.put(visible); return { row: visible, reused: true }; }
    const row: Intent = { ...input, payload, match, version: 1, id: randomId(),
      status: input.analyze ? 'preparing' : 'ready', created: Date.now(), updated: Date.now(), canContinue: true };
    store.add(row); rows.push(row);
    return { row, reused: false };
  }));
}

export async function updateIntent(id: string, patch: Partial<Intent>): Promise<Intent> {
  return transaction((rows, store) => {
    const current = rows.find(row => row.id === id);
    if (!current) throw new Error('原提交记录不存在，请核对浏览器数据');
    // A late failed POST/query must not overwrite acceptance learned in another tab.
    const next = resolved(current) ? { ...current, ...(patch.hidden !== undefined ? { hidden: patch.hidden } : {}) }
      : { ...current, ...patch, updated: Date.now() };
    if (current.status === 'accepted' && patch.status === 'unavailable') next.status = 'unavailable';
    // Another tab may already have sealed the analysis. Its first finalized
    // payload wins, even if our slower analysis returned a different tone.
    if (patch.payload && !current.analyze) next.payload = current.payload;
    if (resolved(next)) { delete next.payload; delete next.analyze; next.match = ''; next.canContinue = false; }
    store.put(next);
    return next;
  });
}
