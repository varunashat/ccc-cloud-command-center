"""Cost Growth inside Cloud Command Center (runs on Azure, no laptop needed).

Reads every active Seven Seas (pcn.8372436B) customer's Cost Management data with the signed-in user's saved Azure
sign-in (same engine as tools/cost_growth_report.py), writes the Excel + JSON to the app's persistent share and shows
them on /cost-growth. Read-only. One run at a time; a run interrupted by a restart resumes by itself (finished customers
are cached); refreshes itself once a month for the last complete month.
"""
import datetime as dt
import importlib
import json
import logging
import os
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

ACCOUNT = 'pcn.8372436B'
TENANT = '1f3f9d61-e763-4e33-b8eb-e95c97f9f2ff'          # Noventiq Holdings, where the billing account lives
DEFAULT_START = '2026-04'
_LOCK = threading.Lock()
_JOB = {'thread': None}


def _root():
    try:
        import app
        base = Path(app.ROOT)
    except Exception:
        base = Path(__file__).resolve().parent
    d = base / 'reports' / 'cost-growth'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file():
    return _root() / 'state.json'


def load_state():
    try:
        return json.loads(_state_file().read_text(encoding='utf-8'))
    except Exception:
        return {'status': 'never'}


def save_state(st):
    p = _state_file()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, indent=1), encoding='utf-8')
    tmp.replace(p)


def last_complete_month(today=None):
    t = today or dt.date.today()
    first = t.replace(day=1)
    prev = first - dt.timedelta(days=1)
    return f'{prev.year:04d}-{prev.month:02d}'


def _engine():
    here = Path(__file__).resolve().parent
    for p in (here / 'tools', here):
        if (p / 'cost_growth_report.py').is_file() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import cost_growth_report as C
    return importlib.reload(C)


def _token_getter(user):
    srv = sys.modules.get('__main__')
    f = getattr(srv, 'user_token', None) or getattr(sys.modules.get('server_app'), 'user_token', None)
    if not f:
        raise RuntimeError('This build has no Azure sign-in.')
    return lambda: f(user, TENANT)


def _keep_awake(stop):
    """Container Apps may scale to zero when no requests arrive: ping our own public URL while a run is going."""
    name, suffix = os.environ.get('CONTAINER_APP_NAME'), os.environ.get('CONTAINER_APP_ENV_DNS_SUFFIX')
    if not name or not suffix:
        return
    url = f'https://{name}.{suffix}/healthz'
    while not stop.wait(45):
        try:
            urllib.request.urlopen(url, timeout=15).read()
        except Exception:
            pass


def _run(user, start, end, refresh, reason):
    st = load_state()
    st.update(status='running', user=user, start=start, end=end, reason=reason, error='',
              started=dt.datetime.now().isoformat(timespec='seconds'), progress={'i': 0, 'n': 0, 'current': ''}, log=[])
    save_state(st)
    stop = threading.Event()
    threading.Thread(target=_keep_awake, args=(stop,), daemon=True).start()
    last_save = [0.0]

    def log(*a, **k):
        line = ' '.join(str(x) for x in a)
        for ln in line.splitlines() or ['']:
            st['log'] = (st.get('log') or [])[-300:] + [ln]
            s = ln.strip()
            if s.startswith('[') and '/' in s.split(']')[0]:
                try:
                    i, n = s[1:].split(']')[0].split('/')
                    st['progress'] = {'i': int(i), 'n': int(n), 'current': s.split('] ', 1)[1] if '] ' in s else ''}
                except ValueError:
                    pass
        if time.time() - last_save[0] > 3:
            last_save[0] = time.time()
            save_state(st)
    try:
        C = _engine()
        C.print = log                                    # engine output goes to the page's progress log
        C.SCOPE_PREF[:] = ['enrollment']                 # proven working scope for this account (Cost access test)
        C.COST_APIS = ('2021-10-01',) + tuple(v for v in C.COST_APIS if v != '2021-10-01')
        tok = _token_getter(user)
        tok()                                            # fail fast if the saved sign-in has expired
        az = C.Azure(token_fn=tok)
        months = C.months_between(start, end)
        out = _root() / 'out'
        xlsx = C.report(az, ACCOUNT, '', months, str(out), True, refresh, False, pick=False)
        st = dict(st, status='done', finished=dt.datetime.now().isoformat(timespec='seconds'),
                  xlsx=str(xlsx), data=str(out / 'cost_growth_data.json'))
        if reason == 'monthly':
            st['last_auto'] = end
        log('Finished.')
    except BaseException as e:                           # SystemExit from the engine included
        logging.warning('cost growth run failed: %s', traceback.format_exc()[-800:])
        st = dict(st, status='failed', finished=dt.datetime.now().isoformat(timespec='seconds'), error=str(e)[:500])
    finally:
        stop.set()
        save_state(st)


def start_run(user, start=None, end=None, refresh=False, reason='manual'):
    with _LOCK:
        t = _JOB.get('thread')
        if t and t.is_alive():
            return False, 'A run is already in progress.'
        start = start or load_state().get('start') or DEFAULT_START
        end = end or last_complete_month()
        if not (len(start) == 7 and len(end) == 7 and start <= end):
            return False, 'Pick a start month before the end month.'
        t = threading.Thread(target=_run, args=(user, start, end, bool(refresh), reason), daemon=True)
        _JOB['thread'] = t
        t.start()
        return True, 'Started.'


def _watcher():
    """Resume a run cut off by a restart; refresh once a month (from the 3rd) for the last complete month."""
    time.sleep(30)
    while True:
        try:
            st = load_state()
            alive = _JOB.get('thread') and _JOB['thread'].is_alive()
            if not alive and st.get('status') == 'running' and st.get('user'):
                start_run(st['user'], st.get('start'), st.get('end'), False, st.get('reason') or 'resume')
            elif not alive and st.get('user') and dt.date.today().day >= 3:
                want = last_complete_month()
                if st.get('status') in ('done', 'failed') and st.get('end') != want and st.get('last_auto') != want:
                    st['last_auto'] = want
                    save_state(st)
                    start_run(st['user'], st.get('start') or DEFAULT_START, want, False, 'monthly')
        except Exception:
            logging.warning('cost growth watcher: %s', traceback.format_exc()[-400:])
        time.sleep(3600)


def public_state():
    st = load_state()
    alive = bool(_JOB.get('thread') and _JOB['thread'].is_alive())
    out = {k: st.get(k) for k in ('status', 'user', 'start', 'end', 'reason', 'started', 'finished', 'error', 'progress', 'last_auto')}
    out['running'] = alive
    out['log'] = (st.get('log') or [])[-80:]
    out['has_data'] = bool(st.get('data') and Path(st['data']).is_file())
    out['has_xlsx'] = bool(st.get('xlsx') and Path(st['xlsx']).is_file())
    out['default_end'] = last_complete_month()
    return out


def data_bytes():
    st = load_state()
    p = st.get('data')
    if p and Path(p).is_file():
        return Path(p).read_bytes()
    p = _root() / 'out' / 'cost_growth_data.json'
    return p.read_bytes() if p.is_file() else None


def install(handler_cls):
    if getattr(handler_cls, '_cg_installed', False):
        return
    real_get, real_post = handler_cls.do_GET, handler_cls.do_POST

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path not in ('/cost-growth', '/ai/cost-growth/status', '/ai/cost-growth/data', '/ai/cost-growth/download'):
            return real_get(self)
        if not self._user():
            if path == '/cost-growth':
                self.send_response(302); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers()
                return
            return self.send(401, {'error': 'Sign in first.'})
        if path == '/cost-growth':
            return self.send_raw(200, PAGE.encode('utf-8'), 'text/html; charset=utf-8', 'no-store')
        if path == '/ai/cost-growth/status':
            return self.send(200, public_state())
        if path == '/ai/cost-growth/data':
            b = data_bytes()
            return self.send_raw(200, b, 'application/json', 'no-store') if b else self.send(404, {'error': 'No report yet.'})
        st = load_state()
        p = st.get('xlsx')
        if not p or not Path(p).is_file():
            return self.send(404, {'error': 'No Excel yet.'})
        b = Path(p).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.send_header('Content-Disposition', 'attachment; filename="' + Path(p).name.replace('"', '') + '"')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if path != '/ai/cost-growth/start':
            return real_post(self)
        o = self.headers.get('Origin')
        if o is not None and urllib.parse.urlparse(o).netloc != self.headers.get('Host'):
            return self.send(403, {'error': 'Invalid origin'})
        user = self._user()
        if not user:
            return self.send(401, {'error': 'Sign in first.'})
        size = int(self.headers.get('Content-Length', '0') or 0)
        try:
            body = json.loads(self.rfile.read(size) or b'{}') if 0 <= size <= 4000 else {}
        except ValueError:
            body = {}
        ok, msg = start_run(user, str(body.get('start') or '') or None, str(body.get('end') or '') or None, bool(body.get('refresh')))
        return self.send(200 if ok else 409, {'ok': ok, 'message': msg, 'state': public_state()})

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._cg_installed = True
    threading.Thread(target=_watcher, daemon=True).start()


NAV = r"""<script id="ccc-cg-nav">(function(){function add(){var n=document.querySelector('nav.workspace-nav');if(!n||n.querySelector('a[href="/cost-growth"]'))return;
var a=document.createElement('a');a.href='/cost-growth';a.textContent='Cost Growth (Seven Seas)';a.title='Monthly cost growth of every Seven Seas customer: spikes, RI / Savings plan / Marketplace, Excel';
var t=[].slice.call(n.querySelectorAll('a')).filter(function(x){return /TAM/.test(x.textContent)})[0];if(t&&t.parentNode)t.parentNode.insertBefore(a,t.nextSibling);else n.appendChild(a)}
add();setTimeout(add,800);setTimeout(add,2500);}());</script>"""

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cost Growth · Cloud Command Center</title>
<style>
:root{--bg:#060a14;--p:#0d1424;--p2:#111a2e;--line:#1e2a44;--t:#e2e8f0;--m:#94a3b8;--a:#22d3ee;--g:#4ade80;--r:#f87171;--y:#fbbf24;--v:#a78bfa}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 10% -10%,#0b1b3a,transparent),var(--bg);color:var(--t);font:14px/1.45 'Segoe UI',system-ui,sans-serif}
a{color:var(--a)}header{display:flex;align-items:center;gap:14px;padding:14px 26px;border-bottom:1px solid var(--line);background:rgba(6,10,20,.8);position:sticky;top:0;z-index:5;backdrop-filter:blur(12px)}
header .sp{flex:1}header b{font-size:16px}.crumb{color:var(--m);font-size:12px}
main{padding:20px 26px 60px;max-width:1500px;margin:0 auto}
.hero{background:linear-gradient(135deg,#0e2246,#0d1424);border:1px solid var(--line);border-radius:14px;padding:18px 20px;display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end}
.hero h1{margin:0 0 4px;font-size:22px}.hero p{margin:0;color:var(--m)}.hero .sp{flex:1}
.ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.ctl label{font-size:12px;color:var(--m)}
input,select,button{font:inherit}input[type=month],input[type=search]{background:var(--p2);border:1px solid var(--line);color:var(--t);border-radius:8px;padding:6px 8px}
button{background:#0f766e;color:#fff;border:0;border-radius:8px;padding:7px 14px;cursor:pointer}button.sec{background:#334155}button:disabled{opacity:.5;cursor:default}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}
.k{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.k small{color:var(--m);text-transform:uppercase;font-size:11px;letter-spacing:.06em}.k b{display:block;font-size:22px;margin-top:4px}.k i{font-style:normal;font-size:12px}
.up{color:var(--r)}.dn{color:var(--g)}
.card{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-top:14px}.card h3{margin:0 0 10px;font-size:14px}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:14px}@media(max-width:1000px){.grid2{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left;white-space:normal}
th{color:var(--m);font-weight:600;font-size:12px;cursor:pointer;position:sticky;top:0;background:var(--p)}tbody tr:hover{background:#13203a;cursor:pointer}tr.sel{background:#16284a}
.tw{max-height:560px;overflow:auto}.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;margin:1px;background:#1e293b;color:#cbd5e1}.tag.ri{background:#1e3a8a}.tag.sp{background:#4c1d95}.tag.mk{background:#78350f}.tag.sk{background:#7f1d1d}
#log{background:#030712;border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:12px Consolas,monospace;max-height:220px;overflow:auto;white-space:pre-wrap;color:#a5b4c8}
.bar{height:8px;background:#1e293b;border-radius:6px;overflow:hidden}.bar>div{height:100%;background:linear-gradient(90deg,#0f766e,#22d3ee)}
.muted{color:var(--m)}.pill{padding:3px 10px;border-radius:12px;font-size:12px;background:#1e293b}.pill.run{background:#0e7490}.pill.ok{background:#166534}.pill.bad{background:#7f1d1d}
#detail{display:none}.dh{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.dh h2{margin:0;font-size:18px}
.tabs{display:flex;gap:6px;margin:10px 0}.tabs button{background:#1e293b}.tabs button.on{background:#0f766e}
svg text{fill:#94a3b8;font-size:11px}
</style></head><body>
<header><a href="/" title="Back to Cloud Command Center">← Cloud Command Center</a><span class="crumb">/ Business / <b style="color:#e2e8f0">Cost Growth (Seven Seas)</b></span><span class="sp"></span><span id="pill" class="pill">…</span></header>
<main>
<section class="hero"><div><h1>Cost Growth · Seven Seas technology (8372436B)</h1>
<p>Every active customer's Azure cost per month, from Cost Management (Actual cost, USD), read with your Azure sign-in. Read-only · refreshes itself monthly.</p></div><span class="sp"></span>
<div class="ctl"><label>From <input type="month" id="from"></label><label>To <input type="month" id="to"></label>
<label title="Ignore saved results and read every customer again"><input type="checkbox" id="refresh"> re-read all</label>
<button id="run">Run now</button><button id="dl" class="sec" disabled>⬇ Excel</button></div></section>
<div id="prog" class="card" style="display:none"><h3 id="ptitle">Running…</h3><div class="bar"><div id="pbar" style="width:0"></div></div><p class="muted" id="pnote"></p><div id="log"></div></div>
<div id="empty" class="card" style="display:none"><h3>No report yet</h3><p class="muted">Press <b>Run now</b>. All ~50 customers take 1–3 hours because Azure limits cost queries; you can close this page, the run continues on Azure and the results appear here.</p></div>
<div id="body" style="display:none">
<div class="kpis" id="kpis"></div>
<div class="grid2"><div class="card"><h3>Portfolio trend · all customers (excl. Marketplace)</h3><div id="trend"></div></div>
<div class="card"><h3>Top movers (first → last month)</h3><div id="movers"></div></div></div>
<div class="card"><div class="dh"><h3 style="margin:0">All customers</h3><span class="sp" style="flex:1"></span><input type="search" id="q" placeholder="Filter customers…"></div>
<div class="tw"><table id="tbl"><thead></thead><tbody></tbody></table></div>
<p class="muted" id="foot"></p></div>
<div class="card" id="detail"><div class="dh"><h2 id="dname"></h2><span id="dtags"></span><span class="sp" style="flex:1"></span><button class="sec" id="dclose">Close</button></div>
<div class="kpis" id="dk"></div><div id="dchart"></div>
<div class="tabs" id="dtabs"></div><div class="tw" id="dtab"></div><div id="dspikes"></div></div>
</div>
</main>
<script>
var $=function(s){return document.querySelector(s)},D=null,SORT={k:'last',d:-1},SEL=null;
function esc(t){return String(t==null?'':t).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function f0(v){return (v<0?'-':'')+'$'+Math.abs(v).toLocaleString('en-US',{maximumFractionDigits:0})}
function fk(v){var a=Math.abs(v);return (v<0?'-':'')+'$'+(a>=1e6?(a/1e6).toFixed(2)+'M':a>=1e3?(a/1e3).toFixed(1)+'K':a.toFixed(0))}
function pc(v){return v==null||!isFinite(v)?'–':(v>0?'+':'')+(v*100).toFixed(1)+'%'}
function ML(m){var d=new Date(m+'-01T00:00:00');return d.toLocaleString('en-US',{month:'short'})+' '+String(d.getFullYear()).slice(2)}
function rec(c,m){return (c.total[m]||0)-((c.marketplace||{})[m]||0)}
async function J(u,o){var r=await fetch(u,Object.assign({credentials:'same-origin'},o||{}));var j=null;try{j=await r.json()}catch(e){}return {s:r.status,j:j}}
function bars(vals,labels,h){h=h||170;var w=Math.max(360,labels.length*70),mx=Math.max.apply(null,vals.concat([1])),bw=(w-50)/vals.length;
 var s='<svg viewBox="0 0 '+w+' '+(h+30)+'" width="100%" height="'+(h+30)+'">';for(var i=0;i<=4;i++){var y=10+(h-10)*i/4;s+='<line x1="44" x2="'+w+'" y1="'+y+'" y2="'+y+'" stroke="#1e2a44"/><text x="40" y="'+(y+4)+'" text-anchor="end">'+fk(mx*(1-i/4))+'</text>'}
 vals.forEach(function(v,i){var bh=Math.max(1,(h-10)*v/mx),x=50+i*bw+bw*.18,y=h-bh;var up=i&&v>vals[i-1]*1.1&&v-vals[i-1]>=500;
 s+='<rect x="'+x+'" y="'+y+'" width="'+(bw*.64)+'" height="'+bh+'" rx="4" fill="'+(up?'#f97316':'#22d3ee')+'"><title>'+labels[i]+': '+f0(v)+'</title></rect><text x="'+(x+bw*.32)+'" y="'+(h+18)+'" text-anchor="middle">'+labels[i]+'</text>'});
 return s+'</svg><p class="muted" style="margin:4px 0 0;font-size:12px">Orange = month up ≥10% and ≥$500 vs previous (spike).</p>'}
function state(){J('/ai/cost-growth/status').then(function(r){var s=r.j||{};var p=$('#pill');
 p.className='pill '+(s.running?'run':s.status=='done'?'ok':s.status=='failed'?'bad':'');p.textContent=s.running?'Running':s.status=='done'?'Updated '+(s.finished||'').replace('T',' '):s.status=='failed'?'Last run failed':'Not run yet';
 if(!$('#from').value){$('#from').value=s.start||'2026-04';$('#to').value=s.end||s.default_end}
 $('#run').disabled=!!s.running;$('#dl').disabled=!s.has_xlsx;
 var pr=$('#prog');if(s.running||s.status=='failed'){pr.style.display='block';var g=s.progress||{};$('#ptitle').textContent=s.running?('Running · '+(g.n?('customer '+g.i+' of '+g.n+(g.current?' · '+g.current:'')):'starting…')):'Last run failed';
  $('#pbar').style.width=(g.n?Math.round(100*g.i/g.n):3)+'%';$('#pnote').textContent=s.running?('Started '+(s.started||'').replace('T',' ')+' by '+(s.user||'')+'. Runs on Azure; you can close this page.'):(s.error||'');
  var L=$('#log');L.textContent=(s.log||[]).join('\n');L.scrollTop=L.scrollHeight}else pr.style.display='none';
 if(!s.has_data&&!s.running)$('#empty').style.display='block';else $('#empty').style.display='none';
 if(s.has_data&&(!D||D._f!==s.finished))load(s.finished);
 setTimeout(state,s.running?8000:60000)})}
function load(fin){J('/ai/cost-growth/data').then(function(r){if(r.s!=200)return;D=r.j;D._f=fin;render()})}
function rows(){var M=D.months,a=M[0],z=M[M.length-1];return (D.customers||[]).map(function(c){var f=rec(c,a),l=rec(c,z),t=M.reduce(function(s,m){return s+(c.total[m]||0)},0);
 return {c:c,name:c.name,code:c.code,first:f,last:l,chg:l-f,pct:f>0?(l-f)/f:null,total:t,spikes:(c.spikes||[]).length,ri:c.reservations||0,sp:c.savings_plan||0,mk:M.reduce(function(s,m){return s+((c.marketplace||{})[m]||0)},0)}})}
function render(){$('#body').style.display='block';var M=D.months,R=rows(),a=M[0],z=M[M.length-1];
 var tot=M.map(function(m){return R.reduce(function(s,r){return s+rec(r.c,m)},0)});var ch=tot[0]?(tot[tot.length-1]-tot[0])/tot[0]:null;
 var mom=tot.length>1&&tot[tot.length-2]?(tot[tot.length-1]-tot[tot.length-2])/tot[tot.length-2]:null;
 $('#kpis').innerHTML=[['Customers with cost',R.length,(D.no_cost||[]).length+' with no cost · '+(D.failed||[]).length+' not readable'],
 ['Spend '+ML(z),fk(tot[tot.length-1]),'<span class="'+(mom>0?'up':'dn')+'">'+pc(mom)+'</span> vs '+ML(M[M.length-2]||z)],
 ['Growth '+ML(a)+' → '+ML(z),'<span class="'+(ch>0?'up':'dn')+'">'+pc(ch)+'</span>',fk(tot[tot.length-1]-tot[0])+' per month'],
 ['Period total',fk(R.reduce(function(s,r){return s+r.total},0)),M.length+' months'],
 ['Customers with spikes',R.filter(function(r){return r.spikes}).length,'≥10% and ≥$500 jump'],
 ['RI / SP / Marketplace',R.filter(function(r){return r.ri>.5}).length+' / '+R.filter(function(r){return r.sp>.5}).length+' / '+R.filter(function(r){return r.mk>.5}).length,'customers using each']]
 .map(function(k){return '<div class="k"><small>'+k[0]+'</small><b>'+k[1]+'</b><i class="muted">'+k[2]+'</i></div>'}).join('');
 $('#trend').innerHTML=bars(tot,M.map(ML));
 var mv=R.slice().sort(function(x,y){return Math.abs(y.chg)-Math.abs(x.chg)}).slice(0,8);
 $('#movers').innerHTML='<table><tbody>'+mv.map(function(r){return '<tr data-c="'+esc(r.code)+'"><td>'+esc(r.name)+'</td><td class="'+(r.chg>0?'up':'dn')+'">'+(r.chg>0?'+':'')+fk(r.chg)+'</td><td class="muted">'+pc(r.pct)+'</td></tr>'}).join('')+'</tbody></table>';
 $('#movers').querySelectorAll('tr').forEach(function(t){t.onclick=function(){show(t.getAttribute('data-c'))}});
 var H=[['name','Customer'],['code','Profile']].concat(M.map(function(m){return ['m:'+m,ML(m)]})).concat([['chg','Change $'],['pct','Change %'],['total','Period total'],['flags','Flags']]);
 $('#tbl thead').innerHTML='<tr>'+H.map(function(h){return '<th data-k="'+h[0]+'">'+h[1]+(SORT.k==h[0]?(SORT.d>0?' ▲':' ▼'):'')+'</th>'}).join('')+'</tr>';
 $('#tbl thead').querySelectorAll('th').forEach(function(th){th.onclick=function(){var k=th.getAttribute('data-k');if(k=='flags')return;SORT={k:k,d:SORT.k==k?-SORT.d:-1};render()}});
 var q=($('#q').value||'').toLowerCase();var L=R.filter(function(r){return !q||(r.name+' '+r.code).toLowerCase().indexOf(q)>=0});
 L.sort(function(x,y){var k=SORT.k,vx,vy;if(k.slice(0,2)=='m:'){vx=rec(x.c,k.slice(2));vy=rec(y.c,k.slice(2))}else if(k=='last'){vx=x.last;vy=y.last}else{vx=x[k];vy=y[k]}
  if(typeof vx=='string')return SORT.d*vx.localeCompare(vy);return SORT.d*((vx==null?-1e18:vx)-(vy==null?-1e18:vy))});
 $('#tbl tbody').innerHTML=L.map(function(r){var fl=(r.ri>.5?'<span class="tag ri">RI</span>':'')+(r.sp>.5?'<span class="tag sp">SP</span>':'')+(r.mk>.5?'<span class="tag mk">Mkt</span>':'')+(r.spikes?'<span class="tag sk">'+r.spikes+' spike</span>':'');
  return '<tr data-c="'+esc(r.code)+'" class="'+(SEL==r.code?'sel':'')+'"><td>'+esc(r.name)+'</td><td>'+esc(r.code)+'</td>'+M.map(function(m){return '<td>'+f0(rec(r.c,m))+'</td>'}).join('')+
  '<td class="'+(r.chg>0?'up':'dn')+'">'+f0(r.chg)+'</td><td>'+pc(r.pct)+'</td><td>'+f0(r.total)+'</td><td style="text-align:left">'+fl+'</td></tr>'}).join('');
 $('#tbl tbody').querySelectorAll('tr').forEach(function(t){t.onclick=function(){show(t.getAttribute('data-c'))}});
 $('#foot').innerHTML='Recurring cost = total minus Marketplace. Pulled '+esc(D.pulled||'')+'. '+((D.no_cost||[]).length?'<br>No cost in the period: '+D.no_cost.map(function(x){return esc(x.name)}).join(', ')+'.':'')+
  ((D.failed||[]).length?'<br><span class="up">Not readable:</span> '+D.failed.map(function(x){return esc(x.name)+' ('+esc((x.error||'').slice(0,90))+')'}).join('; '):'');
 if(SEL)show(SEL,true)}
function mtable(obj,M,top){var ks=Object.keys(obj||{}).map(function(k){var v=obj[k];return {k:k,v:v,f:v[M[0]]||0,l:v[M[M.length-1]]||0,t:M.reduce(function(s,m){return s+(v[m]||0)},0)}}).filter(function(x){return Math.abs(x.t)>0.5});
 ks.sort(function(a,b){return b.t-a.t});ks=ks.slice(0,top||40);
 return '<table><thead><tr><th>Name</th>'+M.map(function(m){return '<th>'+ML(m)+'</th>'}).join('')+'<th>Change $</th><th>Change %</th><th>Total</th></tr></thead><tbody>'+ks.map(function(x){var c=x.l-x.f;
 return '<tr><td>'+esc(x.k)+'</td>'+M.map(function(m){return '<td>'+f0(x.v[m]||0)+'</td>'}).join('')+'<td class="'+(c>0?'up':'dn')+'">'+f0(c)+'</td><td>'+pc(x.f>0?c/x.f:null)+'</td><td>'+f0(x.t)+'</td></tr>'}).join('')+'</tbody></table>'}
function show(code,keep){var c=(D.customers||[]).filter(function(x){return String(x.code)==String(code)})[0];if(!c)return;SEL=code;var M=D.months,d=$('#detail');d.style.display='block';
 $('#dname').textContent=c.name+' ('+c.code+')';var vals=M.map(function(m){return rec(c,m)});var f=vals[0],l=vals[vals.length-1];
 var mk=M.reduce(function(s,m){return s+((c.marketplace||{})[m]||0)},0);
 $('#dtags').innerHTML=(c.reservations>.5?'<span class="tag ri">RI '+fk(c.reservations)+'</span>':'')+(c.savings_plan>.5?'<span class="tag sp">Savings plan '+fk(c.savings_plan)+'</span>':'')+(mk>.5?'<span class="tag mk">Marketplace '+fk(mk)+'</span>':'');
 $('#dk').innerHTML=[[ML(M[0]),fk(f)],[ML(M[M.length-1]),fk(l)],['Change',(l-f>0?'+':'')+fk(l-f)+' · '+pc(f>0?(l-f)/f:null)],['Period total',fk(M.reduce(function(s,m){return s+(c.total[m]||0)},0))],['Spikes',(c.spikes||[]).length],['Billing check',esc(c.billing_check||'–')]]
  .map(function(k){return '<div class="k"><small>'+k[0]+'</small><b style="font-size:18px">'+k[1]+'</b></div>'}).join('');
 $('#dchart').innerHTML=bars(vals,M.map(ML),150);
 var T={'Subscriptions':c.subscriptions,'Services':c.services,'Regions':c.regions,'Pricing model':c.pricing,'Charge type':c.charge_types,'Marketplace offers':c.marketplace_offers};
 var names=Object.keys(T).filter(function(k){return T[k]&&Object.keys(T[k]).length});var cur=d.getAttribute('data-tab');if(names.indexOf(cur)<0)cur=names[0];
 $('#dtabs').innerHTML=names.map(function(n){return '<button class="'+(n==cur?'on':'')+'">'+n+'</button>'}).join('');
 $('#dtabs').querySelectorAll('button').forEach(function(b){b.onclick=function(){d.setAttribute('data-tab',b.textContent);show(code,true)}});
 $('#dtab').innerHTML=cur?mtable(T[cur],M):'<p class="muted">No breakdown.</p>';
 $('#dspikes').innerHTML=(c.spikes||[]).length?'<h3 style="margin:14px 0 6px">Spikes and what drove them</h3><table><thead><tr><th>Month</th><th>Change</th><th style="text-align:left">Top drivers (subscription · service · change)</th></tr></thead><tbody>'+
  c.spikes.map(function(s){return '<tr><td>'+ML(s.from)+' → '+ML(s.to)+'</td><td class="up">+'+f0(s.change)+'</td><td style="text-align:left">'+(s.drivers||[]).map(function(x){return esc(x.subscription)+' · '+esc(x.service)+' · '+(x.change>0?'+':'')+f0(x.change)}).join('<br>')+'</td></tr>'}).join('')+'</tbody></table>':'<p class="muted">No spike months.</p>';
 document.querySelectorAll('#tbl tbody tr').forEach(function(t){t.classList.toggle('sel',t.getAttribute('data-c')==String(code))});if(!keep)d.scrollIntoView({behavior:'smooth'})}
$('#dclose').onclick=function(){$('#detail').style.display='none';SEL=null};$('#q').oninput=function(){render()};
$('#dl').onclick=function(){location.href='/ai/cost-growth/download'};
$('#run').onclick=async function(){$('#run').disabled=true;var r=await J('/ai/cost-growth/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start:$('#from').value,end:$('#to').value,refresh:$('#refresh').checked})});
 if(!r.j||!r.j.ok){$('#prog').style.display='block';$('#ptitle').textContent=(r.j&&(r.j.message||r.j.error))||('HTTP '+r.s)}state()};
state();
</script></body></html>"""
