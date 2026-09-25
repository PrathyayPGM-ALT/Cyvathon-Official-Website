/* ============================================================
   CYVATHON — SERVICE WORKER
   Served from /sw.js so it covers the whole site.

   It keeps only the app's own shell (styles, scripts, icons and an
   offline screen) so the app opens and explains itself with no signal.

   It never stores balances, chat, votes or anything else from the
   server: every one of those requests goes straight to the network,
   exactly as if this file didn't exist. Money must always be live.
============================================================ */
const VERSION = "cyv-app-v1";
const OFFLINE = "/static/offline.html";
const SHELL = [
  OFFLINE,
  "/static/theme.css",
  "/static/app.js",
  "/static/appshell.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];
// Only the app's own files are kept, never documents or uploads.
const KEEP = /\.(css|js|png|svg|ico|webp|woff2?)$/i;

self.addEventListener("install", event => {
  event.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Pages always come from the server. With no connection, the offline screen.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
    return;
  }

  // The app's own files: the newest copy when online, the kept one when not.
  if (url.pathname.startsWith("/static/") && KEEP.test(url.pathname)) {
    event.respondWith(
      fetch(req).then(res => {
        if (res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(VERSION).then(c => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req)));
  }
  // Everything else is untouched and goes to the network.
});
