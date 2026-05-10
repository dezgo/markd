const CACHE = 'markd-v12';
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
  const data = e.data ? e.data.json() : {};
  const title = data.title || 'Markd';
  const options = {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/favicon-96x96.png',
    tag: data.tag || 'markd',
    data: { url: '/' },
  };
  e.waitUntil(self.registration.showNotification(title, options));
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

  // Never cache API calls or login/logout
  if (url.pathname.startsWith('/todos') || url.pathname === '/login' || url.pathname === '/logout') {
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



