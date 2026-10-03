// Immer zuerst die aktuelle Version aus dem Netz holen; ohne Netz die zuletzt gespeicherte zeigen.
const SPEICHER = 'spielplan-v1';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET' || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then(antwort => {
        const kopie = antwort.clone();
        caches.open(SPEICHER).then(c => c.put(e.request, kopie));
        return antwort;
      })
      .catch(() => caches.match(e.request))
  );
});
