"""Makes Cloud Command Center installable on Android and iOS (home-screen app). No data is cached: the service worker only
passes requests through, and shows a small offline page when the network is down. Served before sign-in so the browser can read it."""
import json
from pathlib import Path

DIR = Path(__file__).resolve().parent / 'pwa'
MANIFEST = {
    'name': 'Cloud Command Center', 'short_name': 'CCC', 'description': 'Noventiq Seven Seas Cloud Command Center',
    'start_url': '/', 'scope': '/', 'display': 'standalone', 'orientation': 'any', 'background_color': '#0b1220', 'theme_color': '#0b1220',
    'icons': [{'src': '/pwa/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
              {'src': '/pwa/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
              {'src': '/pwa/icon-maskable-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}],
}
SW = """self.addEventListener('install',function(e){self.skipWaiting()});
self.addEventListener('activate',function(e){e.waitUntil(self.clients.claim())});
self.addEventListener('fetch',function(e){
  if(e.request.mode==='navigate'){e.respondWith(fetch(e.request).catch(function(){return new Response('<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><body style="font:16px sans-serif;background:#0b1220;color:#e7ecf6;padding:32px"><h2>Cloud Command Center</h2><p>You are offline. Connect to the internet and reopen the app.</p>',{headers:{'Content-Type':'text/html'}})}))}
});
"""
HEAD_TAGS = ('<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#0b1220">'
             '<link rel="icon" href="/pwa/icon-192.png"><link rel="apple-touch-icon" href="/pwa/icon-192.png">'
             '<meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes">')
REGISTER = "<script>if('serviceWorker' in navigator){addEventListener('load',function(){navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(function(){})})}</script>"
FILES = {'/pwa/icon-192.png', '/pwa/icon-512.png', '/pwa/icon-maskable-512.png'}


def install(handler_cls):
    if getattr(handler_cls, '_pwa_installed', False):
        return
    real_get = handler_cls.do_GET

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/manifest.webmanifest':
            return self.send_raw(200, json.dumps(MANIFEST).encode(), 'application/manifest+json', 'public, max-age=3600')
        if path == '/sw.js':
            self.send_response(200)
            data = SW.encode()
            self.send_header('Content-Type', 'application/javascript'); self.send_header('Service-Worker-Allowed', '/'); self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            return
        if path in FILES:
            return self.send_raw(200, (DIR / path.rsplit('/', 1)[1]).read_bytes(), 'image/png', 'public, max-age=86400')
        return real_get(self)
    handler_cls.do_GET = do_GET
    handler_cls._pwa_installed = True
