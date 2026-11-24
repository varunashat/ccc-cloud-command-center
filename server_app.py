"""Cloud Command Center - hosted (Azure Container Apps) entry point.

The desktop app (app.py, Start.cmd) is unchanged. This wrapper serves the same dashboard over HTTPS for a short
allow-list of users, with no Entra app registration and no admin rights needed:

* Sign-in: Microsoft device-code flow with the Azure CLI public client (first-party, pre-consented in every tenant).
  The same sign-in opens the app (only e-mails in CCC_ALLOWED_USERS) and reads the customer directories.
  Fallback when a tenant blocks device code: authorization code + PKCE with a localhost redirect; the user pastes the
  final browser address back into the app.
* Tokens: MSAL token cache per user, encrypted (Fernet, key derived from the CCC_SECRET container secret), on the
  mounted Azure Files share, so sign-in survives scale-to-zero. No password is ever seen or stored by the app.
* Calculator: filled in the user's OWN browser by the "CCC fill" bookmark (calculator_bookmarklet.js); the server
  never drives a browser.
"""
import base64, hashlib, hmac, html, json, logging, os, re, secrets, threading, time, uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

import app
from proposal.proposal_studio import serve as serve_proposal
import tam_proxy
import supervisor
import hub_jobs
import hub_chat
import live_workflow
import weather_console

CLI_CLIENT = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'          # Microsoft Azure CLI (public client, first-party)
ARM_SCOPE = ['https://management.azure.com/.default']
AUTHORITY = 'https://login.microsoftonline.com/'
SESSION_HOURS = 12
_LOCAL = threading.local()
_FLOWS = {}                       # pending device / code flows by flow id
_LOCK = threading.Lock()


def allowed():
    return {e.strip().lower() for e in os.environ.get('CCC_ALLOWED_USERS', '').split(',') if e.strip()}


def secret():
    s = os.environ.get('CCC_SECRET', '')
    if len(s) < 32:
        raise RuntimeError('CCC_SECRET (at least 32 characters) is not set.')
    return s.encode()


def fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'ccc-token-cache|' + secret()).digest()))


# ----------------------------------------------------------------------------- sessions (signed cookie)
def make_session(email):
    body = base64.urlsafe_b64encode(json.dumps({'u': email, 'exp': int(time.time()) + SESSION_HOURS * 3600}).encode()).decode()
    sig = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
    return body + '.' + sig


def read_session(cookie_header):
    m = re.search(r'(?:^|;\s*)ccc_session=([A-Za-z0-9_\-=]+\.[a-f0-9]{64})', cookie_header or '')
    if not m:
        return None
    body, sig = m.group(1).rsplit('.', 1)
    if not hmac.compare_digest(sig, hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(body))
    except Exception:
        return None
    if d.get('exp', 0) < time.time() or d.get('u', '').lower() not in allowed():
        return None
    return d['u'].lower()


# ----------------------------------------------------------------------------- per-user encrypted MSAL cache
def cache_path(email):
    return app.ROOT / 'profiles' / 'users' / (hashlib.sha256(email.encode()).hexdigest()[:24] + '.bin')


def load_cache(email):
    import msal
    c = msal.SerializableTokenCache()
    p = cache_path(email)
    if p.is_file():
        try:
            c.deserialize(fernet().decrypt(p.read_bytes()).decode())
        except Exception:
            logging.warning('token cache unreadable; user must sign in again')
    return c


def save_cache(email, c):
    if c.has_state_changed:
        p = cache_path(email); p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp'); tmp.write_bytes(fernet().encrypt(c.serialize().encode())); tmp.replace(p)


def msal_app(email_or_cache, tenant='organizations'):
    import msal
    cache = email_or_cache if not isinstance(email_or_cache, str) else load_cache(email_or_cache)
    return msal.PublicClientApplication(CLI_CLIENT, authority=AUTHORITY + tenant, token_cache=cache), cache


def _acquire_user_token(email, tenant=None):
    """ARM access token for this user in a customer tenant (silent, from the refresh token)."""
    a, cache = msal_app(email, tenant or 'organizations')
    accts = [x for x in a.get_accounts() if x.get('username', '').lower() == email]
    if not accts:
        raise ValueError('Your Azure sign-in has expired. Sign out and sign in again.')
    r = a.acquire_token_silent(ARM_SCOPE, account=accts[0])
    save_cache(email, cache)
    if not r or 'access_token' not in r:
        err = (r or {}).get('error_description', '')[:160]
        raise ValueError('Could not get a token for that directory (MFA or Conditional Access may need a fresh sign-in). ' + err)
    return r['access_token']


_TOKEN_CACHE_LOCK = threading.RLock()


def user_token(email, tenant=None):
    with _TOKEN_CACHE_LOCK:
        lock_path = cache_path(email).with_suffix('.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a') as cache_lock:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(cache_lock, fcntl.LOCK_EX)
            try:
                return _acquire_user_token(email, tenant)
            finally:
                if os.name == 'posix':
                    fcntl.flock(cache_lock, fcntl.LOCK_UN)


def finish_login(result, cache, fid):
    claims = result.get('id_token_claims') or {}
    email = str(claims.get('preferred_username') or claims.get('upn') or claims.get('email') or '').lower()
    st = _FLOWS.get(fid, {})
    if 'access_token' not in result:
        st.update(status='failed', message=(result.get('error_description') or 'Sign-in failed.')[:300]); return
    if email not in allowed():
        st.update(status='denied', message=f'{email or "This account"} is not allowed to use this app.'); return
    if st.get('owner') and st['owner'] != email:
        st.update(status='denied', message='Use the same work account that is signed into this dashboard.'); return
    p = cache_path(email); p.parent.mkdir(parents=True, exist_ok=True)
    cache.has_state_changed = True; save_cache(email, cache)
    st.update(status='done', email=email, cookie=make_session(email))
    logging.info('login=ok')


def start_device(fid, tenant='organizations', owner=None):
    cache = load_cache(owner) if owner else __import__('msal').SerializableTokenCache()
    a, cache = msal_app(cache, tenant)
    flow = a.initiate_device_flow(scopes=ARM_SCOPE)
    if 'user_code' not in flow:
        raise ValueError('Microsoft refused the device-code sign-in: ' + str(flow.get('error_description', ''))[:200])
    _FLOWS[fid] = {'status': 'pending', 'kind': 'device', 'code': flow['user_code'], 'url': flow['verification_uri'], 'created': time.time(), 'owner': owner}

    def wait():
        try:
            finish_login(a.acquire_token_by_device_flow(flow), cache, fid)
        except Exception as e:
            _FLOWS[fid].update(status='failed', message=type(e).__name__)
    threading.Thread(target=wait, daemon=True).start()
    return _FLOWS[fid]


def start_code(fid):
    a, cache = msal_app(__import__('msal').SerializableTokenCache())
    flow = a.initiate_auth_code_flow(ARM_SCOPE, redirect_uri='http://localhost:8400')
    _FLOWS[fid] = {'status': 'pending', 'kind': 'code', 'url': flow['auth_uri'], 'created': time.time(), '_flow': flow, '_app': a, '_cache': cache}
    return {'status': 'pending', 'kind': 'code', 'url': flow['auth_uri']}


def finish_code(fid, pasted):
    st = _FLOWS.get(fid)
    if not st or st.get('kind') != 'code':
        raise ValueError('Start the sign-in again.')
    q = parse_qs(urlparse(str(pasted).strip()).query)
    resp = {k: v[0] for k, v in q.items()}
    if 'code' not in resp:
        raise ValueError('Paste the full address from the browser (it starts with http://localhost:8400/?code=...).')
    finish_login(st['_app'].acquire_token_by_auth_code_flow(st['_flow'], resp), st['_cache'], fid)
    return st


# ----------------------------------------------------------------------------- calculator plan (filled in the user's browser)
BOOKMARKLET = (Path(__file__).resolve().parent / 'calculator_bookmarklet.js')


def bookmarklet_href():
    code = BOOKMARKLET.read_text(encoding='utf-8')
    code = re.sub(r'^\s*//.*$', '', code, flags=re.M)
    code = re.sub(r'\s*\n\s*', ' ', code).strip()
    return 'javascript:' + code.replace('%', '%25').replace('#', '%23')


def start_browser_plan(lines, name, notes=()):
    """Server replacement for app.start_direct_calc: store the plan; the user's bookmark fills the calculator."""
    job = uuid.uuid4().hex[:10]; d = app.ROOT / 'data' / 'calc-agent' / job; d.mkdir(parents=True, exist_ok=True)
    plan = {'v': 1, 'name': name, 'lines': [[l['slug'], l['set']] for l in lines]}
    status = {'status': 'waiting_browser', 'mode': 'browser', 'user': current_user(), 'name': name, 'notes': list(notes)[:30],
              'request': '\n'.join(f"- {l['count']} x {l['label']}" for l in lines), 'plan': json.dumps(plan, separators=(',', ':')),
              'lines': len(lines), 'message': 'Ready: copy the calculator data, open the Azure Calculator and click the "CCC fill" bookmark.',
              'created_utc': app.datetime.datetime.now(app.datetime.timezone.utc).isoformat(), 'log': []}
    app.save_calc(d, status)
    return {'job': job, 'status': status['message']}


def save_link(payload):
    job, url = str(payload.get('job', '')), str(payload.get('url', '')).strip()
    if not re.fullmatch(r'[a-f0-9]{10}', job) or not re.fullmatch(r'https://azure\.com/e/[A-Za-z0-9]{8,64}', url):
        raise ValueError('Paste the share link from the calculator (https://azure.com/e/...).')
    d = app.ROOT / 'data' / 'calc-agent' / job
    st = json.loads((d / 'status.json').read_text(encoding='utf-8'))
    if st.get('user') not in (None, current_user()):
        raise ValueError('Not your estimate.')
    total = re.sub(r'\s+', ' ', str(payload.get('total') or '')).strip()
    if total and not re.fullmatch(r'[^\d\s]{0,4} ?[\d,]{1,15}(\.\d{1,2})?', total):
        total = ''
    st.update(status='completed', url=url, message='Share link saved.')
    if total:
        st['total'] = total
    app.save_calc(d, st)
    return {'status': 'Saved.'}


def current_user():
    return getattr(_LOCAL, 'user', None)


DISABLED = {'calc_agent', 'calc_signin', 'calculator', 'partner', 'chat', 'browser'}

LOGIN_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cloud Command Center - sign in</title><style>body{font:16px system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh;margin:0}
.box{max-width:520px;padding:28px;background:#111c33;border-radius:14px}button{font:inherit;padding:10px 16px;border-radius:8px;border:0;background:#5eead4;color:#04211d;font-weight:600;cursor:pointer}
code{font-size:26px;letter-spacing:3px;background:#0b1224;padding:6px 12px;border-radius:8px}a{color:#5eead4}input{width:100%;padding:8px;margin:8px 0;border-radius:6px;border:1px solid #334}small{color:#94a3b8}</style>
<div class="box"><h1>Cloud Command Center</h1><p>Sign in with your work account. Only approved accounts can open this app.</p>
<p><button id="go">Sign in with Microsoft</button></p><div id="out" role="status"></div>
<p><small>Device code blocked by your company? <a href="#" id="alt">Use the browser sign-in instead</a>.</small></p></div>
<script>
const out=document.getElementById('out');let fid=null;
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return [r.ok,await r.json()]}
async function poll(){const [ok,j]=await post('/auth/poll',{fid});if(j.status==='done'){out.textContent='Signed in. Opening the app...';location.href='/';return}
 if(j.status==='pending'){setTimeout(poll,3000);return}out.textContent='âš  '+(j.message||'Sign-in failed.')}
document.getElementById('go').onclick=async()=>{const [ok,j]=await post('/auth/device');if(!ok){out.textContent='âš  '+j.error;return}fid=j.fid;
 out.innerHTML='';const p=document.createElement('p');p.append('1. Open ');const a=document.createElement('a');a.href=j.url;a.target='_blank';a.rel='noopener';a.textContent=j.url;p.append(a,document.createElement('br'),'2. Enter this code and approve MFA: ');const c=document.createElement('code');c.textContent=j.code;p.append(c);out.append(p);poll()};
document.getElementById('alt').onclick=async(e)=>{e.preventDefault();const [ok,j]=await post('/auth/code');if(!ok){out.textContent='âš  '+j.error;return}fid=j.fid;
 out.innerHTML='';const p=document.createElement('p');const a=document.createElement('a');a.href=j.url;a.target='_blank';a.rel='noopener';a.textContent='1. Open the Microsoft sign-in';p.append(a,document.createElement('br'),'2. After signing in the browser shows an error page at localhost:8400. Copy that full address and paste it here:');
 const i=document.createElement('input');i.placeholder='http://localhost:8400/?code=...';const b=document.createElement('button');b.textContent='Finish sign-in';b.onclick=async()=>{const [ok2,k]=await post('/auth/code/finish',{fid,url:i.value});if(ok2&&k.status==='done')location.href='/';else out.append(document.createTextNode('âš  '+(k.error||k.message||'failed')))};p.append(i,b);out.append(p)};
</script></html>"""


class Handler(app.Handler):
    def valid_host(self):
        return True                      # behind Container Apps ingress (TLS); requests are authenticated below

    def _user(self):
        u = read_session(self.headers.get('Cookie'))
        _LOCAL.user = u
        return u

    def _origin_ok(self):
        o = self.headers.get('Origin')
        return o is None or urlparse(o).netloc == self.headers.get('Host')

    origin_allowed = _origin_ok

    def send_cookie(self, code, body, cookie):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Set-Cookie', f'ccc_session={cookie}; Path=/; Max-Age={SESSION_HOURS * 3600 if cookie else 0}; HttpOnly; Secure; SameSite=Strict')
        self.end_headers(); self.wfile.write(data)

    def send_raw(self, code, data, ctype, cache='private, max-age=60'):
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache); self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'SAMEORIGIN'); self.end_headers(); self.wfile.write(data)

    def _tam(self, method):
        """TAM dashboard pages (and their API calls) under /tam, for signed-in users only."""
        if not self._user():
            if method == 'POST':
                return self.send(401, {'error': 'Sign in first.'})
            self.send_response(302); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if self.path in ('/tam', '/tam/'):
            return self.send_raw(200, tam_proxy.shell(current_user() or '').encode(), 'text/html; charset=utf-8', 'no-store')
        rel = tam_proxy.clean(self.path)
        if rel is None:
            return self.send(404, {'error': 'Not found'})
        if method == 'POST':
            if not tam_proxy.api_allowed(rel):
                return self.send(404, {'error': 'Not found'})
            size = int(self.headers.get('Content-Length', '0') or 0)
            if not 0 <= size <= 2000000:
                return self.send(413, {'error': 'Too large'})
            fwd = {k: self.headers[k] for k in ('Content-Type', 'x-tam-pass') if self.headers.get(k)}
            code, ctype, data = tam_proxy.fetch(rel, 'POST', self.rfile.read(size), fwd)
            return self.send_raw(code, data, ctype, 'no-store')
        code, ctype, data = tam_proxy.fetch(rel)
        return self.send_raw(code, data, ctype)

    def do_GET(self):
        if self.path == '/healthz':
            return self.send(200, {'ok': True})
        if self.path == '/tam' or self.path.startswith('/tam/'):
            return self._tam('GET')
        if not self._user():
            if self.path == '/':
                return self.send(200, LOGIN_PAGE, 'text/html')
            return self.send(401, {'error': 'Sign in first.'})
        if self.path == '/sow-builder':
            self.send_response(302)
            self.send_header('Location', '/#work-proposal')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/':
            self._local_dms_dashboard = True
            page = (app.ROOT / 'dashboard.html').read_text(encoding='utf-8').replace('__TOKEN__', app.TOKEN)
            boot = ('<script>window.CCC_SERVER=' + json.dumps({'user': current_user(), 'bookmarklet': bookmarklet_href()}) + '</script>')
            script = (app.ROOT / 'supervisor.js').read_text(encoding='utf-8') + '\n' + (app.ROOT / 'hub_workspace.js').read_text(encoding='utf-8')
            page += '<script>' + script + '</script>'
            return self.send(200, page.replace('<script>', boot + '<script>', 1), 'text/html')
        return super().do_GET()

    def do_POST(self):
        if not self._origin_ok():
            return self.send(403, {'error': 'Invalid origin'})
        if self.path.startswith('/auth/'):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(size) or b'{}') if 0 <= size <= 8000 else {}
                if self.path == '/auth/device':
                    fid = secrets.token_urlsafe(16); r = start_device(fid); return self.send(200, {'fid': fid, 'code': r['code'], 'url': r['url']})
                if self.path == '/auth/code':
                    fid = secrets.token_urlsafe(16); r = start_code(fid); return self.send(200, {'fid': fid, 'url': r['url']})
                st = _FLOWS.get(str(body.get('fid', '')))
                if self.path == '/auth/code/finish':
                    st = finish_code(str(body.get('fid', '')), body.get('url', ''))
                if self.path not in ('/auth/poll', '/auth/code/finish'):
                    return self.send(404, {'error': 'Not found'})
                if not st:
                    return self.send(400, {'error': 'Start the sign-in again.'})
                if st['status'] == 'done':
                    _FLOWS.pop(str(body.get('fid', '')), None)
                    return self.send_cookie(200, {'status': 'done'}, st['cookie'])
                return self.send(200, {'status': st['status'], 'message': st.get('message', '')})
            except ValueError as e:
                return self.send(400, {'error': str(e)})
            except Exception as e:
                logging.warning('auth_failed type=%s', type(e).__name__)
                return self.send(400, {'error': 'Sign-in failed (' + type(e).__name__ + ').'})
        if not self._user():
            return self.send(401, {'error': 'Sign in first.'})
        if self.path.startswith('/tam/'):
            return self._tam('POST')
        if self.headers.get('X-Local-Token') != app.TOKEN:
            return self.send(403, {'error': 'Invalid request'})
        if self.path == '/api/supervisor':
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384:
                    return self.send(400, {'error': 'Invalid job request length.'})
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('Invalid job request.')
                if payload.get('action') == 'refresh-tenant-signin':
                    tenant = str(uuid.UUID(str(payload.get('tenant'))))
                    fid = secrets.token_urlsafe(16)
                    flow = start_device(fid, tenant, current_user())
                    return self.send(200, {'fid': fid, 'code': flow['code'], 'url': flow['url']})
                if payload.get('channel') == 'assessment-chat':
                    who = current_user()
                    caller = hub_jobs.azure.make_caller(lambda tenant: user_token(who, tenant), retries=0, timeout=20)
                    try:
                        return self.send(200, hub_chat.dispatch(who, payload, caller))
                    except (ValueError, LookupError) as error:
                        return self.send(400, {'error': str(error)})
                if payload.get('channel') == 'assessment-hub':
                    who = current_user()
                    caller = hub_jobs.azure.make_caller(lambda tenant: user_token(who, tenant), retries=0, timeout=20)
                    answer = hub_jobs.dispatch(who, payload, caller)
                    for section in answer.get('sections', {}).values():
                        section['csv'] = hub_jobs.export_csv(section)
                    return self.send(200, answer)
                if payload.get('action') == 'weather':
                    try:
                        return self.send(200, weather_console.forecast(payload))
                    except ValueError:
                        raise
                    except Exception:
                        logging.exception('Weather agent request failed')
                        return self.send(502, {'error': 'Weather service unavailable. Retry shortly.'})
                if payload.get('action') in ('live-assessment', 'live-plan'):
                    who = current_user()
                    caller = live_workflow.azure.make_caller(lambda tenant: user_token(who, tenant))
                    return self.send(200, live_workflow.run(payload, caller))
                result = supervisor.dispatch(current_user(), payload)
                if payload.get('action') in ('start', 'resume'):
                    supervisor.wake()
                return self.send(200, result)
            except (ValueError, TypeError) as error:
                return self.send(400, {'error': str(error)})
        if self.path == '/api/proposal-studio':
            return serve_proposal(self, app.config, app.foundry_token)
        if self.path == '/api/run':
            try:
                size = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(size) if 0 < size <= 64000 else b''
                payload = json.loads(raw or b'{}')
                act = payload.get('action')
                if act in DISABLED:
                    return self.send(400, {'error': 'This feature needs a desktop browser and is only in the laptop version.'})
                if act == 'logout':
                    return self.send_cookie(200, {'status': 'Signed out.'}, '')
                if act == 'calc_link_save':
                    return self.send(200, save_link(payload))
                self.rfile = __import__('io').BytesIO(raw)        # hand the body to the normal handler
                self.headers.replace_header('Content-Length', str(len(raw)))
            except ValueError as e:
                return self.send(400, {'error': str(e)})
        return super().do_POST()


def install_hooks():
    app.SERVER_MODE = True
    app.disc_call = lambda: (lambda u: app.disc().make_caller(lambda tenant=None: user_token(u, tenant)))(current_user())
    app.disc_account = lambda: {'signed_in': True, 'user': current_user() or '', 'server': True}
    app.disc_signout = lambda: {'status': 'Use "Sign out" at the top right to leave the app.'}
    app.start_direct_calc = start_browser_plan
    real_jobs = app.discovery_jobs
    def my_jobs():
        u = current_user()
        return {k: v for k, v in real_jobs().items() if v.get('user') in (None, u)}
    app.discovery_jobs = my_jobs
    real_start = app.start_discovery
    def start(payload):
        return real_start(dict(payload, _user=current_user()))
    app.start_discovery = start


def main():
    secret()
    app.setup()
    if not (app.ROOT / 'config.json').is_file():
        (app.ROOT / 'config.json').write_text(json.dumps({'live_azure': True, 'foundry_enabled': False, 'browser_channel': 'msedge'}), encoding='utf-8')
    install_hooks()
    port = int(os.environ.get('PORT', '8080'))
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'Cloud Command Center (hosted) on :{port}; allowed users: {len(allowed())}')
    supervisor.start_worker()
    srv.serve_forever()


import ai_assist  # noqa: E402
ai_assist.install(Handler)


if __name__ == '__main__':
    main()
