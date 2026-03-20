// BIST AI Scanner - Service Worker
// Uygulama kapalıyken push bildirimi alır

const CACHE_NAME = 'bist-ai-v6';
const STATIC = ['/', '/index.html'];

// ── Kurulum ──────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(STATIC))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch cache ───────────────────────────────────
self.addEventListener('fetch', e => {
  if (e.request.url.includes('api.') || e.request.url.includes('proxy')) return;
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});

// ── Push bildirimi ────────────────────────────────
self.addEventListener('push', e => {
  let data = { title: 'BIST AI Sinyal', body: 'Yeni sinyal var!', ticker: '' };
  try {
    data = e.data ? e.data.json() : data;
  } catch {}

  const title = data.title || `${data.type === 'master' ? 'MASTER AI' : 'AL'} — ${data.ticker}`;
  const options = {
    body:    data.body || `${data.ticker} | %${data.consensus} | TL${data.price}`,
    icon:    '/icon-192.png',
    badge:   '/icon-72.png',
    tag:     `bist-${data.ticker}-${data.tf}`,
    requireInteraction: false,
    data:    { url: `https://www.tradingview.com/chart/?symbol=BIST:${data.ticker}&interval=1D` },
    actions: [
      { action: 'open',    title: 'Grafik Aç' },
      { action: 'dismiss', title: 'Kapat' }
    ]
  };
  e.waitUntil(self.registration.showNotification(title, options));
});

// ── Bildirime tıklama ─────────────────────────────
self.addEventListener('notificationclick', e => {
  e.notification.close();
  if (e.action === 'dismiss') return;
  const url = e.notification.data?.url || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(clientList => {
      for (const client of clientList) {
        if (client.url === url && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});

// ── Arka plan periyodik senkron ───────────────────
// (Destekleyen tarayıcılarda arka planda çalışır)
self.addEventListener('periodicsync', e => {
  if (e.tag === 'bist-scan') {
    e.waitUntil(backgroundScan());
  }
});

async function backgroundScan() {
  // Proxy'e ping at, sinyal varsa bildirim gönder
  try {
    const r = await fetch('https://bist-price-proxy.onrender.com/health');
    if (r.ok) {
      // Proxy çalışıyorsa scheduler zaten hallediyor
      console.log('[SW] Proxy alive, scheduler handles scanning');
    }
  } catch {}
}
