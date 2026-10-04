// Service worker: makes the app installable and keeps its static shell
// available offline. API responses are never cached, so data is always live.
const CACHE = "marketalyzer-v3";
const SHELL = [
  "/static/app.css",
  "/static/app.js",
  "/static/js/core.js",
  "/static/js/charts.js",
  "/static/fonts/geist-latin.woff2",
  "/static/fonts/doto-latin.woff2",
  "/static/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || !url.pathname.startsWith("/static/")) return;
  // Network first, always revalidated, so a new version shows up at once; the
  // cache covers offline.
  event.respondWith(
    fetch(event.request, { cache: "no-cache" })
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request)),
  );
});
