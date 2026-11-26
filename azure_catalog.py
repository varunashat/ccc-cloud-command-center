"""Azure service and SKU catalogue: every service and SKU in the official Azure Retail Prices API, searchable, with a basket that is priced into a BOQ.

Harvested in the background (all consumption meters for the chosen regions, in parallel) and refreshed daily. Prices are public list prices.
"""
import datetime as dt
import json
import logging
import re
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = 'https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview'
REGIONS = ['uaenorth', 'uaecentral', 'Global', 'westeurope', 'northeurope', 'eastus', 'eastus2', 'westus2', 'centralus', 'southeastasia', 'centralindia',
           'uksouth', 'germanywestcentral', 'francecentral', 'swedencentral', 'qatarcentral', 'southafricanorth', 'australiaeast', 'japaneast', 'canadacentral',
           'brazilsouth', 'koreacentral', 'switzerlandnorth', 'norwayeast', 'israelcentral', 'polandcentral', 'italynorth', 'spaincentral']
LOCK = threading.Lock()
STATE = {'status': 'idle', 'done': 0, 'total': len(REGIONS), 'current': ''}
_CACHE = {}


def _root():
    try:
        import app
        base = Path(app.ROOT)
    except Exception:
        base = Path(__file__).resolve().parent
    d = base / 'reports' / 'azure-catalog'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch_region(region, out, lock, stop):
    flt = f"armRegionName eq '{region}' and priceType eq 'Consumption'"
    url = API + '&$filter=' + urllib.parse.quote(flt)
    pages = 0
    while url and pages < 400 and not stop.is_set():
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=40) as r:
                    d = json.load(r)
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        else:
            return
        with lock:
            for i in d.get('Items', []):
                m = i.get('meterName', '') + ' ' + i.get('skuName', '')
                if re.search(r'Spot|Low Priority', m):
                    continue
                k = (i.get('serviceName', ''), i.get('productName', ''), i.get('skuName', ''))
                e = out.setdefault(k, {'u': i.get('unitOfMeasure', ''), 'r': set(), 'm': {}, 'p': {}})
                e['r'].add(region)
                mn = i.get('meterName', '')
                if mn and (mn in e['m'] or len(e['m']) < 5):
                    e['m'][mn] = e['m'].get(mn, 0) + 1
                pr = float(i.get('retailPrice') or 0)
                if region in ('uaenorth', 'Global') and float(i.get('tierMinimumUnits') or 0) == 0 and pr > 0:
                    e['p'][region] = min(pr, e['p'].get(region, pr))
        url, pages = d.get('NextPageLink'), pages + 1


def refresh():
    with LOCK:
        if STATE['status'] == 'running':
            return False
        STATE.update(status='running', done=0, total=len(REGIONS), current='', started=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), error='')

    def go():
        out, lock, stop = {}, threading.Lock(), threading.Event()
        try:
            pend = list(REGIONS)

            def worker():
                while True:
                    with lock:
                        if not pend:
                            return
                        r = pend.pop(0)
                    STATE['current'] = r
                    _fetch_region(r, out, lock, stop)
                    STATE['done'] += 1
            ts = [threading.Thread(target=worker, daemon=True) for _ in range(5)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            rows = [[k[0], k[1], k[2], v['u'], sorted(v['r']), list(v['m']), v['p']] for k, v in out.items()]
            if len(rows) < 1000:
                raise RuntimeError('The price list returned too little data; kept the previous catalogue.')
            rows.sort(key=lambda r: (r[0].lower(), r[1].lower(), r[2].lower()))
            data = {'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'regions': REGIONS, 'rows': rows}
            p = _root() / 'catalog.json'
            tmp = p.with_suffix('.tmp')
            tmp.write_text(json.dumps(data), encoding='utf-8')
            tmp.replace(p)
            _CACHE.clear()
            STATE.update(status='done')
        except Exception as e:
            logging.warning('azure catalog refresh failed: %s', traceback.format_exc()[-400:])
            STATE.update(status='failed', error=str(e)[:300])
    threading.Thread(target=go, daemon=True).start()
    return True


def _load():
    p = _root() / 'catalog.json'
    if not p.is_file():
        return None
    mt = p.stat().st_mtime
    if _CACHE.get('mt') != mt:
        d = json.loads(p.read_text(encoding='utf-8'))
        d['low'] = [' '.join([r[0], r[1], r[2], ' '.join(r[5])]).lower() for r in d['rows']]
        _CACHE.clear()
        _CACHE.update(mt=mt, d=d)
    return _CACHE['d']


def stats():
    d = _load()
    st = dict(STATE)
    if not d:
        return dict(st, has_data=False)
    sv = {}
    for r in d['rows']:
        sv.setdefault(r[0], [0, set()])
        sv[r[0]][0] += 1
        sv[r[0]][1].add(r[1])
    return dict(st, has_data=True, generated=d['generated'], services=len(sv), skus=len(d['rows']), regions=d['regions'],
                meters=sum(max(1, len(r[5])) for r in d['rows']),
                service_list=[{'service': k, 'skus': v[0], 'products': len(v[1])} for k, v in sorted(sv.items(), key=lambda kv: kv[0].lower())])


def search(q='', service='', region='', limit=100, offset=0):
    d = _load()
    if not d:
        return {'results': [], 'total': 0}
    toks = [t for t in re.split(r'\s+', q.lower().strip()) if t]
    out = []
    for r, low in zip(d['rows'], d['low']):
        if service and r[0] != service:
            continue
        if region and region not in r[4]:
            continue
        if toks and not all(t in low for t in toks):
            continue
        out.append(r)
    page = out[offset:offset + limit]
    return {'total': len(out), 'results': [{'service': r[0], 'product': r[1], 'sku': r[2], 'unit': r[3], 'regions': len(r[4]), 'in_uae': 'uaenorth' in r[4],
                                            'meters': r[5], 'price': r[6].get('uaenorth', r[6].get('Global'))} for r in page]}


def price_basket(handler_app, items, project, customer_id, region):
    """Write the basket as an estimator input and start the normal pricing run (official prices, Excel BOQ)."""
    clean = []
    for it in items[:200]:
        svc = str(it.get('service', '')).strip()
        if not svc:
            continue
        clean.append({'type': 'azure_service', 'name': (str(it.get('sku') or it.get('product') or svc))[:100], 'service_name': svc,
                      'product_name': str(it.get('product', ''))[:200], 'sku_name': str(it.get('sku', ''))[:200], 'meter_name': str(it.get('meter', ''))[:200],
                      'quantity': max(0.0, min(1e7, float(it.get('quantity') or 1))), 'region': region, 'hours': 730})
    if not clean:
        raise ValueError('The basket is empty.')
    name = uuid.uuid4().hex[:8] + '-catalog-basket.json'
    (handler_app.ROOT / 'estimator' / 'inputs').mkdir(parents=True, exist_ok=True)
    (handler_app.ROOT / 'estimator' / 'inputs' / name).write_text(json.dumps({'items': clean}), encoding='utf-8')
    return handler_app.start_estimate({'input': name, 'project': project[:120], 'customer_id': customer_id, 'region': region})


def _watcher():
    time.sleep(300)
    while True:
        try:
            d = _root() / 'catalog.json'
            if not d.is_file() or time.time() - d.stat().st_mtime > 24 * 3600:
                refresh()
        except Exception:
            logging.warning('azure catalog watcher: %s', traceback.format_exc()[-300:])
        time.sleep(3600)


def install(handler_cls):
    if getattr(handler_cls, '_cat_installed', False):
        return
    real_get, real_post = handler_cls.do_GET, handler_cls.do_POST
    G = ('/azure-catalog', '/ai/azure-catalog/stats', '/ai/azure-catalog/search')

    def do_GET(self):
        path, _, qs = self.path.partition('?')
        if path not in G:
            return real_get(self)
        if not self._user():
            if path == '/azure-catalog':
                self.send_response(302); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers(); return
            return self.send(401, {'error': 'Sign in first.'})
        q = urllib.parse.parse_qs(qs)
        a = lambda k: (q.get(k) or [''])[0]
        if path == '/azure-catalog':
            from azure_catalog_page import PAGE
            try:
                from partner_azure import NAV
            except Exception:
                NAV = ''
            return self.send_raw(200, PAGE.replace('__CGNAV__', NAV).encode('utf-8'), 'text/html; charset=utf-8', 'no-store')
        if path == '/ai/azure-catalog/stats':
            return self.send(200, stats())
        try:
            off = max(0, int(a('offset') or 0))
        except ValueError:
            off = 0
        return self.send(200, search(a('q')[:100], a('service')[:120], a('region')[:40], 100, off))

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if not path.startswith('/ai/azure-catalog/'):
            return real_post(self)
        o = self.headers.get('Origin')
        if o is not None and urllib.parse.urlparse(o).netloc != self.headers.get('Host'):
            return self.send(403, {'error': 'Invalid origin'})
        if not self._user():
            return self.send(401, {'error': 'Sign in first.'})
        try:
            size = int(self.headers.get('Content-Length', '0') or 0)
            p = json.loads(self.rfile.read(size) or b'{}') if 0 < size < 200_000 else {}
            if path == '/ai/azure-catalog/refresh':
                return self.send(200, {'started': refresh(), 'state': stats()})
            if path == '/ai/azure-catalog/price':
                import app
                reg = re.sub(r'[^a-z0-9]', '', str(p.get('region') or 'uaenorth').lower()) or 'uaenorth'
                return self.send(200, price_basket(app, p.get('items') or [], str(p.get('project') or 'Azure catalogue basket'), str(p.get('customer_id') or ''), reg))
            return self.send(404, {'error': 'Unknown action.'})
        except Exception as e:
            return self.send(400, {'error': str(e)[:300]})

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._cat_installed = True
    threading.Thread(target=_watcher, daemon=True).start()
