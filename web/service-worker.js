const CACHE_NAME = "server-console-v88";
const cachePromise = caches.open(CACHE_NAME);
const ASSETS = [
  "/",
  "/index.html",
  "/styles.css?v=88",
  "/app.js?v=88",
  "/manifest.webmanifest?v=88",
  "/icons/icon.svg",
  "/vendor/xterm/xterm.min.css",
  "/vendor/xterm/xterm.min.js",
  "/vendor/xterm/addon-fit.min.js",
  "/vendor/xterm/addon-canvas.min.js",
  "/vendor/xterm/addon-webgl.min.js"
];

self.addEventListener("install", event => {
  event.waitUntil(cachePromise.then(cache => cache.addAll(ASSETS)));
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
    if (response.ok) {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw new Error("network unavailable and no cache entry found");
  }
}

async function cacheFirst(request) {
  const cache = await cachePromise;
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok) {
    cache.put(request, response.clone()).catch(() => {});
  }
  return response;
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
