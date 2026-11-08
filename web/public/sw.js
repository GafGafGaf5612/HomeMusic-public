/* HomeMusic service worker: app shell cache, Web Share Target, notification clicks.
   Never caches /api/*. BUILD_ID is stamped by vite.config.ts at build time. */
const BUILD_ID = '__BUILD_ID__';
const SHELL = `hm-shell-${BUILD_ID}`;
const ASSETS = 'hm-assets-v1'; // hashed file names: safe to keep across builds
const SHELL_URLS = ['/', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png', '/icons/badge-96.png'];

// Same layout as src/lib/shareStore.ts
const SHARE_DB = 'homemusic-share';
const SHARE_STORE = 'shares';
const SHARE_KEY = 'pending';

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const shell = await caches.open(SHELL);
      await shell.addAll(SHELL_URLS);
      // Precache the hashed bundles referenced by index.html so the shell works offline right away.
      try {
        const html = await (await shell.match('/')).text();
        const urls = [...new Set([...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]))];
        const assets = await caches.open(ASSETS);
        await assets.addAll(urls);
      } catch (e) {
        /* best effort */
      }
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([SHELL, ASSETS]);
      for (const key of await caches.keys()) if (!keep.has(key)) await caches.delete(key);
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // covers from Deezer etc.: browser cache

  if (req.method === 'POST' && url.pathname === '/share-target') {
    event.respondWith(handleShare(req));
    return;
  }
  if (req.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/healthz' || url.pathname.startsWith('/app/')) return;

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(req, ASSETS));
    return;
  }
  if (req.mode === 'navigate' || url.pathname === '/' || url.pathname === '/index.html') {
    event.respondWith(networkFirstShell(req));
    return;
  }
  if (SHELL_URLS.includes(url.pathname) || url.pathname.startsWith('/icons/')) {
    event.respondWith(staleWhileRevalidate(req, SHELL));
  }
});

async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}

async function networkFirstShell(req) {
  const cache = await caches.open(SHELL);
  try {
    const res = await fetch(req);
    if (res.ok && res.headers.get('content-type')?.includes('text/html')) cache.put('/', res.clone());
    return res;
  } catch (e) {
    const hit = (await cache.match('/')) || (await cache.match(req));
    if (hit) return hit;
    throw e;
  }
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  const net = fetch(req)
    .then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => hit);
  return hit || net;
}

// --- Web Share Target ------------------------------------------------------------------------------

function openShareDb() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(SHARE_DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(SHARE_STORE);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function handleShare(req) {
  try {
    const form = await req.formData();
    const files = form.getAll('files').filter((f) => f && typeof f === 'object' && 'size' in f && f.size > 0);
    const payload = {
      files,
      title: String(form.get('title') || ''),
      text: String(form.get('text') || ''),
      url: String(form.get('url') || ''),
      ts: Date.now(),
    };
    const db = await openShareDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(SHARE_STORE, 'readwrite');
      tx.objectStore(SHARE_STORE).put(payload, SHARE_KEY);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    db.close();
    return Response.redirect('/#/share', 303);
  } catch (e) {
    // Could not store locally: let the server-side fallback handle it next time.
    return Response.redirect('/#/assistant', 303);
  }
}

// --- notifications -----------------------------------------------------------------------------------

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/#/downloads';
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      for (const c of all) {
        if (new URL(c.url).origin === self.location.origin) {
          await c.focus();
          if ('navigate' in c) return c.navigate(target);
          return;
        }
      }
      return self.clients.openWindow(target);
    })(),
  );
});
