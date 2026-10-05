// Service worker: caches the app so it opens with no signal at the courts.
// Bump CACHE when you change files, so phones pick up the new version.
const CACHE = 'pb-drills-v4';
const ASSETS = [
  './', 'index.html', 'styles.css', 'app.js', 'manifest.webmanifest', 'sample-plan.json',
  'icons/icon.svg', 'icons/icon-192.png', 'icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

// Stale-while-revalidate: serve from cache instantly, refresh the cache in the background.
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET' || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(caches.open(CACHE).then(async (cache) => {
    const cached = await cache.match(e.request, { ignoreSearch: true });
    const network = fetch(e.request)
      .then((res) => { if (res.ok) cache.put(e.request, res.clone()); return res; })
      .catch(() => cached);
    e.waitUntil(network.then(() => {}, () => {}));
    return cached || network;
  }));
});
