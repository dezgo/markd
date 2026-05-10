const CACHE = 'markd-v20';
const PRECACHE = [
  '/',
  '/static/app.css',
  '/static/app.js',
  '/static/manifest.json',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('push', e => {
  console.log('[SW] push event received', e);
  let data = {};
  try { data = e.data ? e.data.json() : {}; }
  catch (err) { console.error('[SW] failed to parse push data', err); }
  console.log('[SW] push payload:', data);
  const title = data.title || 'Markd';
  const options = {
    body: data.body || '',
    icon: '/static/icons/icon-192.png?v=2',
    badge: '/static/icons/favicon-96x96.png?v=2',
    tag: data.tag || 'markd',
    requireInteraction: true,
    data: { url: '/' },
  };
  e.waitUntil(
    self.registration.showNotification(title, options)
      .then(() => console.log('[SW] showNotification resolved'))
      .catch(err => console.error('[SW] showNotification failed', err))
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      const hit = list.find(c => new URL(c.url).origin === self.location.origin);
      if (hit) return hit.focus();
      return clients.openWindow('/');
    })
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Don't intercept non-GET requests (Cache API can't store them) or API/auth routes
  if (e.request.method !== 'GET' ||
      url.pathname.startsWith('/todos') ||
      url.pathname.startsWith('/push') ||
      url.pathname === '/login' ||
      url.pathname === '/logout') {
    return;
  }

  // Cache-first for static assets; network-first for the app shell
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  } else {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
  }
});



