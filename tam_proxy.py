"""TAM Enterprise Dashboard inside Cloud Command Center (one URL, one sign-in).

The TAM dashboard keeps living where it is: GitHub repo varunashat/tam-dashboard -> Azure Static Web App
(Noventiq tenant). Its Excel -> Power Automate -> GitHub -> Static Web App automation is untouched.
Cloud Command Center serves those pages under /tam/... by reading them from the Static Web App on the
server side (short cache), only for users already signed in to Cloud Command Center. Pages are shown in a
frame of the /tam shell, so the TAM password screen is skipped (same host) and the allow-list of
Cloud Command Center is the gate.
"""
import os
import re
import threading
import time
import urllib.error
import urllib.request

ORIGIN = os.environ.get('TAM_ORIGIN', 'https://lemon-pebble-059ff980f.7.azurestaticapps.net').rstrip('/')
CACHE_SECONDS = int(os.environ.get('TAM_CACHE_SECONDS', '120'))
MAX_BYTES = 40 * 1024 * 1024
SAFE_PATH = re.compile(r'^[A-Za-z0-9._~!$&\'()*+,;=:@%/?-]*$')
API_PASS = ('api/ask-services', 'api/live/', 'api/edit/', 'api/ask')   # TAM API routes forwarded as-is

PAGES = [  # (file, menu label)
    ('index.html', 'TAM Home'), ('tam360-dashboard.html', 'TAM 360'), ('ammp-opportunities.html', 'AMMP Opportunities'),
    ('ammp-claims-dashboard.html', 'AMMP Claims'), ('services-forecast-dashboard.html', 'Services Forecast FY27'),
    ('workbook.html', 'Services Workbook'), ('powerbi-azure-details.html', 'Azure Details'), ('azure-acr.html', 'Azure ACR'),
]

_cache = {}
_lock = threading.Lock()


def clean(path):
    """'/tam/x/y.html?z' -> 'x/y.html?z'; None when the path is not allowed."""
    p = path[len('/tam/'):] if path.startswith('/tam/') else ''
    base = p.split('?', 1)[0].split('#', 1)[0]
    if not SAFE_PATH.match(p) or '..' in base.split('/') or base.startswith('/') or '\\' in p:
        return None
    return p or 'index.html'


def _rewrite(body, ctype):
    if 'html' not in ctype and 'javascript' not in ctype:
        return body
    text = body.decode('utf-8', 'replace')
    # absolute API / data paths of the static site -> relative, so they stay under /tam/
    text = re.sub(r'''(["'`])/(api|data)/''', r'\1\2/', text)
    return text.encode('utf-8')


def fetch(rel, method='GET', body=None, headers=None):
    """Returns (status, content_type, bytes). GET answers are cached for CACHE_SECONDS."""
    key = rel
    if method == 'GET':
        with _lock:
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < CACHE_SECONDS:
                return hit[1]
    req = urllib.request.Request(ORIGIN + '/' + rel, data=body, method=method, headers=dict(headers or {}, **{'User-Agent': 'CloudCommandCenter-TAM/1'}))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                return 502, 'text/plain', b'TAM page too large'
            ctype = r.headers.get('Content-Type', 'application/octet-stream')
            out = (r.status, ctype, _rewrite(data, ctype))
    except urllib.error.HTTPError as e:
        out = (e.code, e.headers.get('Content-Type', 'text/plain') if e.headers else 'text/plain', e.read(200000) if e.fp else b'')
    except Exception as e:
        return 502, 'text/plain', ('The TAM dashboard could not be reached (' + type(e).__name__ + '). Try again in a minute.').encode()
    if method == 'GET' and out[0] == 200:
        with _lock:
            _cache[key] = (time.time(), out)
            if len(_cache) > 400:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                    _cache.pop(k, None)
    return out


def api_allowed(rel):
    return any(rel.startswith(p) for p in API_PASS)


def shell(user):
    items = ''.join(f'<a href="/tam/{f}" target="tamframe" data-f="{f}">{label}</a>' for f, label in PAGES)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAM Dashboard - Cloud Command Center</title>
<style>
body{{margin:0;font:14px Segoe UI,system-ui,sans-serif;background:#0b1220;color:#e2e8f0;display:flex;height:100vh}}
nav{{width:230px;flex:none;background:#0f172a;border-right:1px solid #1e293b;padding:18px 12px;display:flex;flex-direction:column;gap:4px;overflow:auto}}
nav h1{{font-size:17px;margin:0 6px 4px}} nav .who{{font-size:12px;color:#94a3b8;margin:0 6px 14px}}
nav a{{color:#cbd5e1;text-decoration:none;padding:8px 10px;border-radius:8px}} nav a:hover{{background:#1e293b}}
nav a.on{{background:#1e3a5f;color:#fff;font-weight:600}} nav .back{{margin-top:auto;border:1px solid #334155;text-align:center}}
iframe{{flex:1;border:0;background:#fff;height:100vh}}
@media(max-width:700px){{body{{flex-direction:column}} nav{{width:auto;flex-direction:row;flex-wrap:wrap;padding:8px}} nav h1,nav .who{{display:none}} iframe{{height:auto;flex:1}}}}
</style></head><body>
<nav><h1>TAM Dashboard</h1><div class="who">Signed in: {user}</div>{items}
<a class="back" href="/">&larr; Cloud Command Center</a></nav>
<iframe name="tamframe" id="tamframe" src="/tam/index.html" title="TAM dashboard"></iframe>
<script>
const fr=document.getElementById('tamframe'),links=[...document.querySelectorAll('nav a[data-f]')];
const mark=()=>{{let f='';try{{f=fr.contentWindow.location.pathname.replace('/tam/','')}}catch(e){{}};links.forEach(a=>a.classList.toggle('on',a.dataset.f===f));
  if(f)history.replaceState(null,'','/tam#'+f)}};
fr.addEventListener('load',mark);
const start=location.hash.slice(1);if(/^[a-z0-9._-]+\\.html$/i.test(start))fr.src='/tam/'+start;
</script></body></html>"""


# Adds a "TAM Dashboard" entry to Cloud Command Center's menu (works with any dashboard layout: side menu if found,
# otherwise a button next to Sign out).
NAV_SCRIPT = """<script>(function(){function add(){if(document.getElementById('ccc-tam-link'))return;
var a=document.createElement('a');a.id='ccc-tam-link';a.href='/tam';a.textContent='TAM Dashboard';
var menu=[...document.querySelectorAll('nav,aside,[role=navigation]')].find(function(n){return n.querySelectorAll('a,button').length>2});
if(menu){var ref=[...menu.querySelectorAll('a,button')].pop();a.className=ref.className||'';a.style.cssText=ref.getAttribute('style')||'';
 a.style.display='block';ref.parentElement.appendChild(a);}
else{a.style.cssText='position:fixed;top:10px;right:120px;z-index:9999;background:#2dd4bf;color:#0f172a;padding:7px 12px;border-radius:8px;font-weight:600;text-decoration:none';document.body.appendChild(a);}}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',add);else add();setTimeout(add,1500);})();</script>"""
