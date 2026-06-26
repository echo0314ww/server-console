const CACHE_NAME = "server-console-v101";
const CACHE_MAX_BYTES = 32 * 1024;
const cachePromise = caches.open(CACHE_NAME);
const ASSETS = [
  "/",
  "/index.html",
  "/styles.css?v=101",
  "/manifest.webmanifest?v=101",
  "/icons/icon.svg",
  "/vendor/xterm/xterm.min.css",
  "/vendor/xterm/addon-fit.min.js"
];

self.addEventListener("install", event => {
  event.waitUntil(precacheSmallAssets());
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  if (event.request.url.includes("/terminal")) return;

  const url = new URL(event.request.url);
  if (url.pathname === "/" || url.pathname === "/index.html") {
    event.respondWith(networkFirst(event.request));
    return;
  }

  if (isStaticAsset(url.pathname)) {
    event.respondWith(cacheFirst(event.request));
    return;
  }

  event.respondWith(networkFirst(event.request));
});

async function networkFirst(request) {
  const cache = await cachePromise;

  try {
    const response = await fetch(request, { cache: "no-store" });
    cacheResponseIfSmall(cache, request, response).catch(() => {});
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw new Error("network unavailable and no cache entry found");
  }
}

async function precacheSmallAssets() {
  const cache = await cachePromise;
  for (const asset of ASSETS) {
    try {
      const request = new Request(asset);
      const response = await fetch(request, { cache: "no-store" });
      await cacheResponseIfSmall(cache, request, response);
    } catch {
      // Installation should continue when an optional cached asset is unavailable.
    }
  }
}

async function cacheFirst(request) {
  const cache = await cachePromise;
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  cacheResponseIfSmall(cache, request, response).catch(() => {});
  return response;
}

async function cacheResponseIfSmall(cache, request, response) {
  if (!response.ok) return;

  const contentLength = Number(response.headers.get("content-length") || "0");
  if (contentLength > CACHE_MAX_BYTES) return;
  if (contentLength > 0) {
    await cache.put(request, response.clone());
    await trimCache(cache);
    return;
  }

  const blob = await response.clone().blob();
  if (blob.size <= CACHE_MAX_BYTES) {
    await cache.put(request, response.clone());
    await trimCache(cache);
  }
}

async function trimCache(cache) {
  const requests = await cache.keys();
  let totalBytes = 0;
  const entries = [];

  for (const request of requests) {
    const response = await cache.match(request);
    if (!response) continue;
    const size = await responseSize(response);
    entries.push({ request, size });
    totalBytes += size;
  }

  for (const entry of entries) {
    if (totalBytes <= CACHE_MAX_BYTES) break;
    await cache.delete(entry.request);
    totalBytes -= entry.size;
  }
}

async function responseSize(response) {
  const contentLength = Number(response.headers.get("content-length") || "0");
  if (contentLength > 0) return contentLength;
  try {
    return (await response.clone().blob()).size;
  } catch {
    return CACHE_MAX_BYTES + 1;
  }
}

function isStaticAsset(pathname) {
  return pathname === "/styles.css" ||
    pathname === "/app.js" ||
    pathname === "/manifest.webmanifest" ||
    pathname === "/icons/icon.svg" ||
    pathname === "/vendor/xterm/xterm.min.css" ||
    pathname === "/vendor/xterm/xterm.min.js" ||
    pathname === "/vendor/xterm/addon-fit.min.js" ||
    pathname === "/vendor/xterm/addon-canvas.min.js" ||
    pathname === "/vendor/xterm/addon-webgl.min.js";
}
