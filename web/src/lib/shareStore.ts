// Reads what the service worker stored on POST /share-target (see public/sw.js — same DB layout).
export interface SharedPayload {
  files: File[];
  title: string;
  text: string;
  url: string;
  ts: number;
}

const DB = 'homemusic-share';
const STORE = 'shares';
const KEY = 'pending';

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const r = fn(db.transaction(STORE, mode).objectStore(STORE));
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  } finally {
    db.close();
  }
}

export async function readShare(): Promise<SharedPayload | null> {
  const v = await tx<SharedPayload | undefined>('readonly', (s) => s.get(KEY) as IDBRequest<SharedPayload | undefined>);
  return v ?? null;
}

export async function clearShare(): Promise<void> {
  await tx('readwrite', (s) => s.delete(KEY));
}
