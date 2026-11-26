"""BOQ alignment check: compare a saved estimate (the BOQ) with what is really deployed, and say who changed what and when.

Baseline = a saved estimate run (boq.json). Deployed = Azure Resource Graph inventory of the customer's subscription(s).
Who / when = Azure Activity Log (Azure keeps 90 days). Read-only: nothing in Azure is ever changed.
"""
import datetime as dt
import json
import logging
import re
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from pathlib import Path

LOCK = threading.Lock()
ARM = 'https://management.azure.com'
TYPE_NAMES = {'vm': 'Virtual machine', 'disk': 'Managed disk', 'sql_db': 'SQL database', 'public_ip': 'Public IP'}
COMPARABLE = tuple(TYPE_NAMES)


def _root():
    try:
        import app
        base = Path(app.ROOT)
    except Exception:
        base = Path(__file__).resolve().parent
    d = base / 'reports' / 'boq-compliance'
    (d / 'checks').mkdir(parents=True, exist_ok=True)
    return d


def _jload(p, default):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:
        return default


def _jsave(p, obj):
    p = Path(p)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, indent=1), encoding='utf-8')
    tmp.replace(p)


# ------------------------------------------------------------------ baselines
def baselines():
    try:
        import app
        runs = Path(app.ROOT) / 'estimator' / 'runs'
        results = _jload(Path(app.ROOT) / 'results.json', [])
    except Exception:
        return []
    cust = {}
    for r in results if isinstance(results, list) else []:
        j = ((r or {}).get('result') or {}).get('job')
        if j:
            cust[j] = (r or {}).get('customer_id') or ''
    out = []
    for d in sorted(runs.glob('*/boq.json'), key=lambda p: -p.stat().st_mtime)[:80]:
        j = d.parent.name
        b = _jload(d, {})
        lines = [x for x in b.get('lines', []) if x.get('type') in COMPARABLE]
        out.append({'job': j, 'project': str(b.get('project') or b.get('title') or b.get('customer') or '')[:80], 'customer_id': cust.get(j, ''),
                    'saved': dt.datetime.fromtimestamp(d.stat().st_mtime, dt.timezone.utc).isoformat(timespec='minutes'),
                    'lines': len(b.get('lines', [])), 'comparable': sum(float(x.get('quantity') or 1) for x in lines),
                    'calculator_url': _jload(d.parent / 'status.json', {}).get('calculator_url', '')})
    return out


def load_baseline(job):
    if not re.fullmatch(r'[a-f0-9]{10}', job or ''):
        raise ValueError('Unknown BOQ.')
    import app
    p = Path(app.ROOT) / 'estimator' / 'runs' / job / 'boq.json'
    if not p.is_file():
        raise ValueError('That BOQ is not finished or no longer exists.')
    return _jload(p, {})


# ------------------------------------------------------------------ comparison
def _lbl(sku):
    return str(sku or '').replace('Standard_', '').replace('_', ' ').strip().lower()


def _cfg(line):
    return str(line.get('config', '') or '').lower()


def _boq_vm(line):
    c = _cfg(line)
    m = re.search(r'\b([a-z]{1,2}\d{1,3}[a-z]{0,4}(?:\s?v\d)?)\b', c)
    return {'size': m.group(1).replace(' ', '') if m else '', 'os': 'windows' if 'windows' in c else ('linux' if 'linux' in c else ''),
            'ahub': ('ahub' in c or 'hybrid benefit' in c) and 'no ahub' not in c}


def _dep_vm(it):
    return {'size': _lbl(it.get('sku')).replace(' ', ''), 'os': it.get('os', ''), 'ahub': it.get('ahub') == 'yes'}


def _pairs(boq, dep, key):
    """Pair BOQ lines with deployed resources of one type: exact name, then same key, then leftovers in order."""
    pairs, ub, ud = [], list(boq), list(dep)
    for b in boq:
        d = next((x for x in ud if x['name'].lower() == str(b.get('name', '')).lower()), None)
        if d:
            pairs.append((b, d)); ub.remove(b); ud.remove(d)
    for b in list(ub):
        d = next((x for x in ud if key(b, x)), None)
        if d:
            pairs.append((b, d)); ub.remove(b); ud.remove(d)
    for b in list(ub):
        if ud:
            d = ud.pop(0); pairs.append((b, d)); ub.remove(b)
    return pairs, ub, ud


def _diff(t, b, d):
    out = []
    reg = lambda r: re.sub(r'[^a-z0-9]', '', str(r or '').lower())
    if reg(b.get('region')) != reg(d.get('region')):
        out.append(('Region', b.get('region'), d.get('region')))
    c = _cfg(b)
    if t == 'vm':
        e, a = _boq_vm(b), _dep_vm(d)
        if e['size'] and e['size'] != a['size']:
            out.append(('VM size', e['size'].upper(), d.get('sku', '').replace('Standard_', '')))
        if e['os'] and e['os'] != a['os']:
            out.append(('Operating system', e['os'], a['os']))
        if e['ahub'] != a['ahub']:
            out.append(('Azure Hybrid Benefit', 'yes' if e['ahub'] else 'no', 'yes' if a['ahub'] else 'no'))
    elif t == 'disk':
        tier = str(d.get('tier', ''))
        if tier and tier.lower() not in c.replace(' ', '') and tier.lower().replace('ssd', '') not in c.replace(' ', ''):
            out.append(('Disk tier', b.get('config'), tier))
        m = re.search(r'(\d+)\s*gb', c)
        if m and d.get('disk_gb') and abs(int(m.group(1)) - float(d['disk_gb'])) > 1:
            out.append(('Disk size (GB)', m.group(1), d['disk_gb']))
    elif t == 'sql_db':
        m = re.search(r'(\d+)\s*v?core', c)
        if m and d.get('vcores') and int(m.group(1)) != int(d['vcores']):
            out.append(('vCores', m.group(1), d['vcores']))
        if d.get('tier') and str(d['tier']) == 'bc' and 'business' not in c and 'bc' not in c:
            out.append(('Service tier', 'General Purpose', 'Business Critical'))
        if d.get('tier') and str(d['tier']) == 'gp' and ('business' in c):
            out.append(('Service tier', 'Business Critical', 'General Purpose'))
    return out


def compare(boq, items, events):
    """boq: boq.json dict; items: deployed estimator-style items; events: {resource_id_lower: [event,...]} newest first."""
    findings, aligned = [], 0
    for t in COMPARABLE:
        bl = [x for x in boq.get('lines', []) if x.get('type') == t]
        expand = []
        for x in bl:
            for i in range(int(round(float(x.get('quantity') or 1)))):
                expand.append(dict(x, name=x.get('name', '') if float(x.get('quantity') or 1) == 1 else f"{x.get('name', '')}"))
        dep = [d for d in items if d.get('type') == t]
        key = (lambda b, d: _boq_vm(b)['size'] == _dep_vm(d)['size']) if t == 'vm' else (lambda b, d: True)
        pairs, miss, extra = _pairs(expand, dep, key)
        for b, d in pairs:
            diffs = _diff(t, b, d)
            ev = events.get(d.get('resource_id', ''), [])
            if diffs:
                findings.append({'status': 'Not aligned', 'type': TYPE_NAMES[t], 'resource': d['name'], 'resource_id': d.get('resource_id'),
                                 'boq_line': b.get('name'), 'changes': [{'parameter': p, 'boq': str(e), 'deployed': str(a)} for p, e, a in diffs],
                                 'last_change': ev[0] if ev else None, 'created': ev[-1] if ev else None, 'history': ev[:6]})
            else:
                aligned += 1
        for d in extra:
            ev = events.get(d.get('resource_id', ''), [])
            findings.append({'status': 'Not in BOQ', 'type': TYPE_NAMES[t], 'resource': d['name'], 'resource_id': d.get('resource_id'), 'boq_line': '',
                             'changes': [{'parameter': 'Resource', 'boq': 'not in BOQ', 'deployed': d.get('sku') or d.get('tier') or 'deployed'}],
                             'last_change': ev[0] if ev else None, 'created': ev[-1] if ev else None, 'history': ev[:6]})
        for b in miss:
            dele = next((e for k, v in events.items() for e in v if e.get('delete') and str(b.get('name', '')).lower() and k.endswith('/' + str(b.get('name', '')).lower())), None)
            findings.append({'status': 'In BOQ, not deployed', 'type': TYPE_NAMES[t], 'resource': b.get('name', ''), 'resource_id': '', 'boq_line': b.get('name'),
                             'changes': [{'parameter': 'Resource', 'boq': b.get('config', ''), 'deployed': 'missing'}],
                             'last_change': dele, 'created': None, 'history': [dele] if dele else []})
    return {'aligned': aligned, 'findings': findings}


# ------------------------------------------------------------------ azure
def _call(user, tenant):
    from discovery.azure_discovery import make_caller
    srv = sys.modules.get('__main__')
    f = getattr(srv, 'user_token', None) or getattr(sys.modules.get('server_app'), 'user_token', None)
    if not f:
        raise RuntimeError('This build has no Azure sign-in.')
    return make_caller(lambda t: f(user, t or tenant))


def activity(call, subs, tenant, days=89):
    """Resource changes from the Activity Log: {resource_id: [{time, by, op, delete}]} newest first."""
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    out, warn = {}, []
    for s in subs:
        flt = urllib.parse.quote(f"eventTimestamp ge '{since}' and eventChannels eq 'Operation'", safe="'=")
        url = (f"{ARM}/subscriptions/{s}/providers/Microsoft.Insights/eventtypes/management/values?api-version=2015-04-01"
               f"&$filter={flt}&$select=eventTimestamp,caller,operationName,resourceId,status,claims")
        try:
            for _ in range(60):
                r = call('GET', url, None, tenant)
                for e in r.get('value', []):
                    op = (e.get('operationName') or {}).get('value', '')
                    st = (e.get('status') or {}).get('value', '')
                    rid = str(e.get('resourceId') or '').lower()
                    if st != 'Succeeded' or not rid or not re.search(r'/(write|delete)$', op.lower()):
                        continue
                    out.setdefault(rid, []).append({'time': e.get('eventTimestamp'), 'by': e.get('caller') or 'unknown', 'op': op.split('/')[-1] + ' ' +
                                                    '/'.join(op.split('/')[:-1][-1:]), 'delete': op.lower().endswith('/delete')})
                url = r.get('nextLink')
                if not url:
                    break
        except Exception as ex:
            warn.append(f'Activity log {s[:8]}…: {str(ex)[:160]}')
    for v in out.values():
        v.sort(key=lambda e: e['time'] or '', reverse=True)
    return out, warn


def run_check(user, job, tenant, subs, rg='', watch_id=''):
    from discovery.azure_discovery import INVENTORY_QUERY, check_subscriptions, check_tenant, rg_query, to_items
    tenant, subs = check_tenant(tenant), check_subscriptions(subs)
    boq = load_baseline(job)
    call = _call(user, tenant)
    inv = rg_query(call, subs, INVENTORY_QUERY, tenant)
    if rg:
        inv = [r for r in inv if str(r.get('resourceGroup', '')).lower() == rg.lower()]
    items, _notes = to_items(inv, include_powered_off=True)
    events, warn = activity(call, subs, tenant)
    res = compare(boq, items, events)
    cid = uuid.uuid4().hex[:10]
    prev = [c for c in history() if watch_id and c.get('watch') == watch_id]
    seen = set()
    if prev:
        old = _jload(_root() / 'checks' / (prev[0]['id'] + '.json'), {})
        seen = {(f['resource'], f['status'], json.dumps(f['changes'], sort_keys=True)) for f in old.get('findings', [])}
    for f in res['findings']:
        f['new'] = bool(prev) and (f['resource'], f['status'], json.dumps(f['changes'], sort_keys=True)) not in seen
    rec = {'id': cid, 'time': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'job': job, 'tenant': tenant, 'subscriptions': subs, 'rg': rg,
           'user': user, 'watch': watch_id, 'deployed_resources': len(items), 'aligned': res['aligned'], 'findings': res['findings'], 'warnings': warn,
           'project': str(boq.get('project') or '')[:80], 'verdict': 'Aligned with BOQ' if not res['findings'] else 'NOT aligned with BOQ'}
    _jsave(_root() / 'checks' / (cid + '.json'), rec)
    with LOCK:
        h = _jload(_root() / 'history.json', [])
        h.insert(0, {k: rec[k] for k in ('id', 'time', 'job', 'tenant', 'project', 'verdict', 'aligned', 'watch', 'user')} | {'drift': len(rec['findings'])})
        _jsave(_root() / 'history.json', h[:300])
    return rec


def history():
    return _jload(_root() / 'history.json', [])


def watches():
    return _jload(_root() / 'watches.json', [])


STATE = {}


def start(user, job, tenant, subs, rg='', watch_id=''):
    tid = uuid.uuid4().hex[:8]
    STATE[tid] = {'status': 'running'}

    def go():
        try:
            STATE[tid] = {'status': 'done', 'id': run_check(user, job, tenant, subs, rg, watch_id)['id']}
        except Exception as e:
            logging.warning('boq check failed: %s', traceback.format_exc()[-500:])
            STATE[tid] = {'status': 'failed', 'error': str(e)[:300]}
    threading.Thread(target=go, daemon=True).start()
    return tid


def export_xlsx(rec, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = 'BOQ alignment'
    ws.append(['Status', 'Type', 'Resource', 'Parameter', 'In BOQ', 'Deployed', 'Last changed (UTC)', 'Changed by', 'Operation', 'Created (UTC)', 'Created by', 'New since last check'])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='0F4C81')
    for f in rec['findings']:
        lc, cr = f.get('last_change') or {}, f.get('created') or {}
        for ch in f['changes']:
            ws.append([f['status'], f['type'], f['resource'], ch['parameter'], ch['boq'], ch['deployed'], lc.get('time', ''), lc.get('by', ''), lc.get('op', ''),
                       cr.get('time', ''), cr.get('by', ''), 'yes' if f.get('new') else ''])
    for col, w in zip('ABCDEFGHIJKL', (20, 18, 34, 20, 26, 26, 24, 34, 26, 24, 34, 12)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    wb.save(path)


def _watcher():
    time.sleep(120)
    last = ''
    while True:
        day = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')
        if day != last:
            for w in watches():
                try:
                    run_check(w['user'], w['job'], w['tenant'], w['subscriptions'], w.get('rg', ''), w['id'])
                except Exception:
                    logging.warning('boq watch %s failed: %s', w.get('id'), traceback.format_exc()[-300:])
            last = day
        time.sleep(1800)


def install(handler_cls):
    if getattr(handler_cls, '_boq_installed', False):
        return
    real_get, real_post = handler_cls.do_GET, handler_cls.do_POST
    G = ('/boq-compliance', '/ai/boq/baselines', '/ai/boq/subs', '/ai/boq/history', '/ai/boq/result', '/ai/boq/state', '/ai/boq/watches', '/ai/boq/xlsx')

    def do_GET(self):
        path, _, qs = self.path.partition('?')
        if path not in G:
            return real_get(self)
        user = self._user()
        if not user:
            if path == '/boq-compliance':
                self.send_response(302); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers(); return
            return self.send(401, {'error': 'Sign in first.'})
        q = urllib.parse.parse_qs(qs)
        a = lambda k: (q.get(k) or [''])[0]
        try:
            if path == '/boq-compliance':
                from boq_compliance_page import PAGE
                try:
                    from partner_azure import NAV
                except Exception:
                    NAV = ''
                return self.send_raw(200, PAGE.replace('__CGNAV__', NAV).encode('utf-8'), 'text/html; charset=utf-8', 'no-store')
            if path == '/ai/boq/baselines':
                return self.send(200, {'baselines': baselines()})
            if path == '/ai/boq/history':
                return self.send(200, {'history': history()})
            if path == '/ai/boq/watches':
                return self.send(200, {'watches': watches()})
            if path == '/ai/boq/state':
                return self.send(200, STATE.get(a('id'), {'status': 'unknown'}))
            if path == '/ai/boq/subs':
                from discovery.azure_discovery import check_tenant, list_subscriptions
                t = check_tenant(a('tenant'))
                return self.send(200, {'subscriptions': list_subscriptions(_call(user, t), t)})
            cid = a('id')
            if not re.fullmatch(r'[a-f0-9]{10}', cid):
                return self.send(400, {'error': 'Unknown check.'})
            rec = _jload(_root() / 'checks' / (cid + '.json'), None)
            if not rec:
                return self.send(404, {'error': 'Unknown check.'})
            if path == '/ai/boq/result':
                return self.send(200, rec)
            tmp = _root() / ('x-' + cid + '.xlsx')
            export_xlsx(rec, tmp)
            b = tmp.read_bytes(); tmp.unlink()
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            self.send_header('Content-Disposition', 'attachment; filename="BOQ_alignment_' + cid + '.xlsx"')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        except Exception as e:
            return self.send(400, {'error': str(e)[:300]})

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if not path.startswith('/ai/boq/'):
            return real_post(self)
        o = self.headers.get('Origin')
        if o is not None and urllib.parse.urlparse(o).netloc != self.headers.get('Host'):
            return self.send(403, {'error': 'Invalid origin'})
        user = self._user()
        if not user:
            return self.send(401, {'error': 'Sign in first.'})
        try:
            size = int(self.headers.get('Content-Length', '0') or 0)
            p = json.loads(self.rfile.read(size) or b'{}') if 0 < size < 100_000 else {}
            subs = [str(s) for s in (p.get('subscriptions') or [])][:20]
            if path == '/ai/boq/check':
                return self.send(200, {'task': start(user, str(p.get('job', '')), str(p.get('tenant', '')), subs, str(p.get('rg', ''))[:90])})
            if path == '/ai/boq/watch':
                from discovery.azure_discovery import check_subscriptions, check_tenant
                load_baseline(str(p.get('job', '')))
                w = {'id': uuid.uuid4().hex[:8], 'user': user, 'job': p['job'], 'tenant': check_tenant(p.get('tenant')), 'subscriptions': check_subscriptions(subs),
                     'rg': str(p.get('rg', ''))[:90], 'added': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}
                with LOCK:
                    _jsave(_root() / 'watches.json', watches() + [w])
                return self.send(200, {'watches': watches()})
            if path == '/ai/boq/unwatch':
                with LOCK:
                    _jsave(_root() / 'watches.json', [w for w in watches() if w['id'] != p.get('id')])
                return self.send(200, {'watches': watches()})
            return self.send(404, {'error': 'Unknown action.'})
        except Exception as e:
            return self.send(400, {'error': str(e)[:300]})

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._boq_installed = True
    threading.Thread(target=_watcher, daemon=True).start()
