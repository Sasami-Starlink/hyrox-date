/* HYROX ウォッチ Service Worker
 * - events.json: network-first（常に最新の開催・販売状況を優先、オフライン時はキャッシュ）
 * - それ以外の静的ファイル: stale-while-revalidate
 *     （即表示＝キャッシュ、裏で最新取得＝次回反映。コード更新が確実に届く）*/
const CACHE = 'hyrox-v2';
const ASSETS = [
  './', './index.html', './styles.css', './app.js',
  './manifest.webmanifest', './icon.svg',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;

  // データは network-first
  if (url.pathname.endsWith('events.json')) {
    e.respondWith(
      fetch(e.request)
        .then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // 静的ファイルは stale-while-revalidate
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const network = fetch(e.request)
        .then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
        .catch(() => cached);
      return cached || network;
    })
  );
});
