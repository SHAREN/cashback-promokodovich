const CACHE = "cashback-promokodovich-v5";
const SHELL = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

async function networkFirstData(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      await cache.put("./data.json", response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match("./data.json");
    if (cached) {
      const headers = new Headers(cached.headers);
      headers.set("X-Cashback-Cache", "1");
      return new Response(await cached.blob(), {
        status: cached.status,
        statusText: cached.statusText,
        headers
      });
    }
    throw err;
  }
}

async function cachedPageFirst(request, event) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request, { ignoreSearch: true })
    || await cache.match("./index.html");

  const update = fetch(request).then(async response => {
    if (response && response.ok) {
      await cache.put(request, response.clone());
      await cache.put("./index.html", response.clone());
    }
    return response;
  }).catch(() => null);

  if (cached) {
    event.waitUntil(update);
    return cached;
  }

  const fresh = await update;
  if (fresh) return fresh;
  throw new Error("No cached page and network unavailable");
}

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);

  if (url.pathname.endsWith("/data.json")) {
    event.respondWith(networkFirstData(event.request));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(cachedPageFirst(event.request, event));
    return;
  }

  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (response && response.ok && url.origin === self.location.origin) {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(event.request, copy));
        }
        return response;
      });
    })
  );
});
