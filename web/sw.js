const CACHE = 'lzrd-app-v3';
const SHELL = [
  '/',
  '/style.css',
  '/app.js',
  '/manifest.json',
  '/icon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.all(
        SHELL.map(url => c.add(new Request(url, { cache: 'reload' })))
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/')) return;

  e.respondWith((async () => {
    try {
      const networkResponse = await fetch(e.request, {
        cache: e.request.mode === 'navigate' ? 'no-store' : 'no-cache',
      });
      if (networkResponse && networkResponse.ok) {
        const cache = await caches.open(CACHE);
        cache.put(e.request, networkResponse.clone());
      }
      return networkResponse;
    } catch {
      const cached = await caches.match(e.request);
      if (cached) return cached;
      if (e.request.mode === 'navigate') {
        const fallback = await caches.match('/');
        if (fallback) return fallback;
      }
      throw new Error('Network unavailable and no cache entry');
    }
  })());
});
