/* QUANTARA installable shell — static assets only; live trading APIs stay network-first. */
const SHELL_CACHE = "quantara-shell-v1";
const PRECACHE = ["/icons/icon-192.png", "/icons/icon-512.png", "/offline.html"];
const NETWORK_ONLY_PREFIXES = ["/api/", "/_next/data/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      if (self.registration.navigationPreload) {
        await self.registration.navigationPreload.enable();
      }
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith("quantara-shell-") && key !== SHELL_CACHE)
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
});

function isNetworkOnly(pathname) {
  return NETWORK_ONLY_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function isShellAsset(pathname) {
  return PRECACHE.includes(pathname);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isNetworkOnly(url.pathname)) return;

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const preload = await event.preloadResponse;
          if (preload) return preload;
          return await fetch(request, { cache: "no-store" });
        } catch {
          const cache = await caches.open(SHELL_CACHE);
          const fallback = await cache.match("/offline.html");
          return fallback || Response.error();
        }
      })()
    );
    return;
  }

  if (url.pathname === "/manifest.webmanifest") {
    event.respondWith(fetch(request, { cache: "no-store" }));
    return;
  }

  if (isShellAsset(url.pathname)) {
    event.respondWith(
      caches.open(SHELL_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        if (cached) return cached;
        const response = await fetch(request);
        if (response.ok) await cache.put(request, response.clone());
        return response;
      })
    );
  }
});
