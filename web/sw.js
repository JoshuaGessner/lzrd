const CACHE = 'lzrd-app-v7';
const SHELL = [
  '/',
  '/style.css',
  '/app.js',
  '/manifest.json',
  '/badge-icon.png',
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

self.addEventListener('push', e => {
  try {
    const data = e.data ? e.data.json() : { title: 'LZRD Alert', body: 'Movement detected!' };
    e.waitUntil(
      self.registration.showNotification(data.title || 'LZRD Alert', {
        body: data.body || 'Movement detected!',
        icon: data.icon || '/icons/icon-192.png',
        badge: data.badge || '/badge-icon.png',
        tag: 'lzrd-alert',
        requireInteraction: true
      })
    );
  } catch (err) {
    console.error('[LZRD SW] Push event error:', err);
  }
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window' })
      .then(clientList => {
        for (let client of clientList) {
          if (client.url.startsWith(self.location.origin) && 'focus' in client) {
            return client.focus();
          }
        }
        if (clients.openWindow) return clients.openWindow('/');
      })
  );
});
