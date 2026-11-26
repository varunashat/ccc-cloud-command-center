"""Partner Center Azure: every Seven Seas Azure customer (EA / CSP), month by month, service by service.

Source: the Azure usage report from Partner Center > Insights > Downloads hub (CSV/TSV). Partner Center only publishes this report
monthly (refreshed after the 5th of the following month), so this page is monthly; day-by-day cost comes from Cost Management
(the Cost Growth page). Uploaded files are layered: for every month the newest file that contains that month wins, so re-uploading a
fresh 12-month export never double counts. A daily watcher also picks up any file dropped in the inbox folder on the share.
"""
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import logging
import re
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

PARTNER_NAME_RE = re.compile(r'seven\s*seas|\bss\s*uae\b|noventiq', re.I)   # only applied when the file lists several partners
MAX_UPLOAD = 200_000_000
LOCK = threading.Lock()

SYN = {
    'month': ('month', 'reportmonth', 'usagemonth', 'usagedate', 'date', 'billingmonth'),
    'name': ('customertenantname', 'customername', 'tenantname', 'customer', 'customerdisplayname'),
    'cid': ('customertpid', 'customertenantid', 'tenantid', 'customerid', 'tpid'),
    'channel': ('saleschannel', 'channel'),
    'attrib': ('partnerattributiontype', 'attributiontype', 'partnerattribution'),
    'enroll': ('enrollmentnumber', 'enrollment', 'enrollmentid'),
    'sub': ('subscriptionid', 'azuresubscriptionid'),
    'state': ('subscriptionstate',),
    'cost': ('acrusd', 'acr', 'azureconsumedrevenue', 'azureconsumedrevenueusd', 'consumedrevenue'),
    'costsub': ('monthlysubscriptionlevelacr',),
    'l1': ('servicelevel1', 'metercategory', 'service'),
    'l2': ('servicelevel2', 'meterSubcategory'.lower()),
    'l3': ('servicelevel3', 'servicename', 'metername'),
    'partner': ('partnername', 'partnerorgname'),
    'mpn': ('pgampnid', 'mpnid', 'partnerid'),
    'dup': ('isacrduplicateatpgalevel',),
}
MONTHS = {m: i for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}


def _root():
    try:
        import app
        base = Path(app.ROOT)
    except Exception:
        base = Path(__file__).resolve().parent
    d = base / 'reports' / 'partner-azure'
    (d / 'layers').mkdir(parents=True, exist_ok=True)
    (d / 'inbox').mkdir(parents=True, exist_ok=True)
    return d


def _norm(h):
    return re.sub(r'[^a-z0-9]', '', str(h or '').lower())


def parse_month(v):
    s = str(v or '').strip()
    m = re.match(r'^(\d{4})-(\d{1,2})', s) or re.match(r'^(\d{4})/(\d{1,2})', s)
    if m:
        return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}'
    m = re.match(r'^([A-Za-z]{3})[A-Za-z]*[- ,]+(\d{4})$', s)
    if m and m.group(1).lower() in MONTHS:
        return f'{int(m.group(2)):04d}-{MONTHS[m.group(1).lower()]:02d}'
    m = re.match(r'^(\d{4})(\d{2})$', s)
    if m:
        return f'{m.group(1)}-{m.group(2)}'
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        return f'{int(m.group(3)):04d}-{int(m.group(1)):02d}'
    m = re.match(r'^(\d{1,2})[/-](\d{4})$', s)
    if m:
        return f'{int(m.group(2)):04d}-{int(m.group(1)):02d}'
    return None


def _num(v):
    s = str(v or '').strip().replace(',', '').replace('$', '')
    if not s or s.lower() in ('null', 'nan', 'none'):
        return 0.0
    neg = s.startswith('(') and s.endswith(')')
    try:
        x = float(s.strip('()'))
    except ValueError:
        return 0.0
    return -x if neg else x


def channel_class(ch):
    c = str(ch or '').lower()
    if re.search(r'\bea\b|^ea ', c):
        return 'EA'
    if 'partner-led' in c or 'partner led' in c:
        return 'CSP'
    return 'Other' if c else ''


def nkey(name):
    return re.sub(r'[^a-z0-9]', '', str(name or '').lower())


def match_channel(name, chmap):
    """Customer names differ slightly between Partner Center reports, so match exactly, then by prefix / similarity."""
    k = nkey(name)
    if k in chmap:
        return chmap[k]
    best, score = None, 0.0
    import difflib
    for o, v in chmap.items():
        if len(k) >= 6 and len(o) >= 6 and (o.startswith(k) or k.startswith(o)):
            r = 0.95
        else:
            r = difflib.SequenceMatcher(None, k, o).ratio()
        if r > score:
            best, score = v, r
    return best if score >= 0.82 else None


def classify(channel, attrib, enroll):
    """EA / CSP / Other from the Partner Center fields (documented in the page footer)."""
    c, a = str(channel or '').lower(), str(attrib or '').lower()
    if re.search(r'\bea\b|enterprise', c) or (enroll and str(enroll).strip() not in ('', '0', 'UNKNOWN', 'None')):
        return 'EA'
    if 'csp' in a or 'csp' in c or re.search(r'partner[- ]?led', c):
        return 'CSP'
    return 'Other'


def parse_file(raw, filename=''):
    text = raw.decode('utf-8-sig', errors='replace')
    first = text.split('\n', 1)[0]
    delim = '\t' if first.count('\t') > first.count(',') else ','
    rd = csv.reader(io.StringIO(text, newline=''), delimiter=delim)
    try:
        header = next(rd)
    except StopIteration:
        raise ValueError('The file is empty.')
    idx = {}
    nh = [_norm(h) for h in header]
    for key, names in SYN.items():
        for n in names:
            if n in nh:
                idx[key] = nh.index(n)
                break
    if 'cost' not in idx and 'costsub' not in idx and 'channel' in idx and 'name' in idx:
        cm = {}
        for row in rd:
            if row and idx['name'] < len(row) and idx['channel'] < len(row) and row[idx['name']].strip():
                d = cm.setdefault(nkey(row[idx['name']]), {})
                ch = row[idx['channel']].strip()
                d[ch] = d.get(ch, 0) + 1
        if not cm:
            raise ValueError('No customers found in the file.')
        return {'kind': 'map', 'map': cm, 'months': [], 'source_rows': len(cm), 'skipped': 0, 'warnings': [], 'filename': filename}
    missing = [k for k in ('month', 'name') if k not in idx]
    if missing or not ('cost' in idx or 'costsub' in idx):
        raise ValueError('This does not look like the Partner Center Azure usage report. Need columns Month, CustomerTenantName and ACR_USD '
                         '(found: ' + ', '.join(header[:12]) + ').')
    warn = []
    cost_key = 'cost' if 'cost' in idx else 'costsub'
    if cost_key == 'costsub':
        warn.append('ACR_USD column missing: used MonthlySubscriptionLevelACR (may repeat across service rows). Re-export with ACR_USD.')
    allrows = [r for r in rd if r and any(r)]
    g = lambda row, k: (row[idx[k]].strip() if k in idx and idx[k] < len(row) else '')
    keep_only = False
    if 'partner' in idx:     # a file listing several partners: keep Seven Seas rows only (if any row matches)
        keep_only = any(PARTNER_NAME_RE.search(g(r, 'partner')) for r in allrows)
    agg = {}
    rows = skipped = 0
    others = 0
    for row in allrows:
        month = parse_month(g(row, 'month'))
        name = g(row, 'name')
        if not month or not name:
            skipped += 1
            continue
        if keep_only and not PARTNER_NAME_RE.search(g(row, 'partner')):
            others += 1
            continue
        rows += 1
        svc = ' / '.join(x for x in (g(row, 'l1'), g(row, 'l2'), g(row, 'l3')) if x and x.upper() != 'UNKNOWN') or '(not split)'
        cid = g(row, 'cid') or name.upper()
        key = (month, cid, name, svc, g(row, 'attrib'), g(row, 'channel'), g(row, 'enroll'), g(row, 'sub'), g(row, 'state'),
               1 if g(row, 'dup') in ('1', 'True', 'true') else 0)
        agg[key] = agg.get(key, 0.0) + _num(g(row, cost_key))
    if not agg:
        raise ValueError('No usable rows found in the file.')
    if others:
        warn.append(f'{others} rows of other partners were ignored.')
    months = sorted({k[0] for k in agg})
    return {'kind': 'usage', 'rows': [list(k) + [round(v, 4)] for k, v in agg.items()], 'months': months, 'source_rows': rows, 'skipped': skipped, 'warnings': warn,
            'has_services': 'l1' in idx, 'has_subs': 'sub' in idx, 'has_attrib': 'attrib' in idx, 'filename': filename}


def _index_path():
    return _root() / 'layers' / 'index.json'


def _load_index():
    try:
        return json.loads(_index_path().read_text(encoding='utf-8'))
    except Exception:
        return []


def _save_index(items):
    p = _index_path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, indent=1), encoding='utf-8')
    tmp.replace(p)


def add_layer(raw, filename):
    digest = hashlib.sha256(raw).hexdigest()[:16]
    with LOCK:
        idx = _load_index()
        if any(i['id'] == digest for i in idx):
            return {'duplicate': True, 'id': digest}
    parsed = parse_file(raw, filename)
    path = _root() / 'layers' / (digest + '.json.gz')
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(parsed['map'] if parsed['kind'] == 'map' else parsed['rows'], f)
    item = {'kind': parsed['kind'], 'id': digest, 'file': Path(filename).name[:120], 'months': parsed['months'], 'rows': parsed['source_rows'], 'bytes': len(raw),
            'added': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'warnings': parsed['warnings'],
            'has_services': parsed.get('has_services', False), 'has_subs': parsed.get('has_subs', False), 'has_attrib': parsed.get('has_attrib', False)}
    with LOCK:
        idx = _load_index()
        idx.append(item)
        _save_index(idx)
    rebuild()
    return item


def remove_layer(layer_id):
    if not re.fullmatch(r'[0-9a-f]{16}', layer_id or ''):
        raise ValueError('Unknown file.')
    with LOCK:
        idx = [i for i in _load_index() if i['id'] != layer_id]
        _save_index(idx)
    try:
        (_root() / 'layers' / (layer_id + '.json.gz')).unlink()
    except OSError:
        pass
    rebuild()


def _merged_rows():
    """Newest usage layer wins per month; customer-channel files are merged into one lookup."""
    idx = _load_index()
    usage = [i for i in idx if i.get('kind', 'usage') == 'usage']
    owner = {}
    for it in sorted(usage, key=lambda i: i['added']):
        for m in it['months']:
            owner[m] = it['id']
    rows, chmap = [], {}
    for it in idx:
        try:
            with gzip.open(_root() / 'layers' / (it['id'] + '.json.gz'), 'rt', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            logging.warning('partner azure layer unreadable: %s', it['id'])
            continue
        if it.get('kind') == 'map':
            for k, d in data.items():
                t = chmap.setdefault(k, {})
                for ch, n in d.items():
                    t[ch] = max(t.get(ch, 0), n)
        else:
            rows.extend(r for r in data if owner.get(r[0]) == it['id'])
    return rows, idx, chmap


def build_dataset(rows, idx, chmap=None):
    """Cost shown = every row (as the Power BI report counts each attribution). Unique ACR = rows not flagged IsACRDuplicateAtPGALevel."""
    chmap = chmap or {}
    months = sorted({r[0] for r in rows})
    cust, svc_total, by_attrib = {}, {}, {}
    for r in rows:
        month, cid, name, svc, attrib, channel, enroll, sub, state = r[:9]
        dup, cost = (r[9], r[10]) if len(r) > 10 else (0, r[9])
        c = cust.setdefault(cid, {'id': cid, 'name': name, 'by_month': {}, 'uniq': {}, 'services': {}, 'subs': {}, 'attrib': {}, 'channels': {},
                                  'enroll': set(), 'cls_cost': {}})
        c['by_month'][month] = c['by_month'].get(month, 0) + cost
        if not dup:
            c['uniq'][month] = c['uniq'].get(month, 0) + cost
        s = c['services'].setdefault(svc, {})
        s[month] = s.get(month, 0) + cost
        t = svc_total.setdefault(svc, {})
        t[month] = t.get(month, 0) + cost
        if attrib:
            ab = c['attrib'].setdefault(attrib, {})
            ab[month] = ab.get(month, 0) + cost
            ga = by_attrib.setdefault(attrib, {})
            ga[month] = ga.get(month, 0) + cost
        if channel:
            c['channels'][channel] = c['channels'].get(channel, 0) + 1
            k = channel_class(channel) or 'Other'
            c['cls_cost'][k] = c['cls_cost'].get(k, 0) + abs(cost)
        elif enroll:
            c['cls_cost']['EA'] = c['cls_cost'].get('EA', 0) + abs(cost)
        if enroll and enroll.upper() not in ('UNKNOWN', '0'):
            c['enroll'].add(enroll)
        if sub:
            sb = c['subs'].setdefault(sub, {'state': state, 'by_month': {}})
            sb['by_month'][month] = sb['by_month'].get(month, 0) + cost
            if state:
                sb['state'] = state
    now_m = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')
    out, by_cls_month = [], {}
    for c in cust.values():
        if not c['channels'] and not c['cls_cost']:                 # channel comes from the customer-channel file when usage has none
            ch = match_channel(c['name'], chmap)
            if ch:
                c['channels'] = dict(ch)
                for chn, n in ch.items():
                    k = channel_class(chn) or 'Other'
                    c['cls_cost'][k] = c['cls_cost'].get(k, 0) + n
        tot_cls = sum(c['cls_cost'].values())
        if tot_cls:
            real = [k for k, v in c['cls_cost'].items() if v > 0.1 * tot_cls]
            cls = real[0] if len(real) == 1 else 'Mixed'
            if len(real) > 1:
                cls = 'Mixed'
        else:
            cls = 'CSP' if any('csp' in a.lower() for a in c['attrib']) and len(c['attrib']) == 1 else 'Unmapped'
        bm = c['by_month']
        closed = [m for m in months if m != now_m]
        seq = [bm.get(m, 0) for m in closed]
        last, prev = (seq[-1] if seq else 0), (seq[-2] if len(seq) > 1 else 0)
        spikes = [closed[i] for i in range(1, len(seq)) if seq[i - 1] > 0 and seq[i] - seq[i - 1] >= 500 and seq[i] >= 1.1 * seq[i - 1]]
        first = next((m for m in months if bm.get(m, 0) > 0), None)
        for m, v in bm.items():
            d = by_cls_month.setdefault(cls, {})
            d[m] = d.get(m, 0) + v
        rd2 = lambda d: {m: round(v, 2) for m, v in d.items()}
        out.append({
            'id': c['id'], 'name': c['name'], 'cls': cls, 'attrib': sorted(c['attrib']), 'channels': sorted(c['channels']),
            'enroll': sorted(c['enroll'])[:5], 'by_month': rd2(bm), 'uniq_by_month': rd2(c['uniq']), 'total': round(sum(bm.values()), 2),
            'last': round(last, 2), 'prev': round(prev, 2), 'mom': (round((last - prev) / prev * 100, 1) if prev > 0 else None),
            'first_seen': first, 'spikes': spikes, 'attrib_by_month': {k: rd2(v) for k, v in c['attrib'].items()},
            'services': {k: rd2(d) for k, d in sorted(c['services'].items(), key=lambda kv: -sum(kv[1].values()))[:60]},
            'subs': {k: {'state': v['state'], 'by_month': rd2(v['by_month'])} for k, v in list(c['subs'].items())[:80]},
        })
    out.sort(key=lambda c: -(c['last'] or c['total'] * 0.001))
    tot_m = {m: round(sum(c['by_month'].get(m, 0) for c in out), 2) for m in months}
    uniq_m = {m: round(sum(c['uniq_by_month'].get(m, 0) for c in out), 2) for m in months}
    return {
        'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'months': months, 'partial_month': now_m if now_m in months else None,
        'currency': 'USD', 'customers': out, 'by_month': tot_m, 'unique_by_month': uniq_m,
        'by_class': {k: {m: round(v, 2) for m, v in d.items()} for k, d in by_cls_month.items()},
        'by_attrib': {k: {m: round(v, 2) for m, v in d.items()} for k, d in by_attrib.items()},
        'services': {k: {m: round(v, 2) for m, v in d.items()} for k, d in sorted(svc_total.items(), key=lambda kv: -sum(kv[1].values()))[:150]},
        'files': [{k: i.get(k) for k in ('id', 'file', 'kind', 'months', 'rows', 'added', 'warnings', 'has_services', 'has_subs', 'has_attrib')} for i in idx],
    }


def rebuild():
    rows, idx, chmap = _merged_rows()
    data = build_dataset(rows, idx, chmap) if rows else {'generated': None, 'months': [], 'customers': [], 'by_month': {}, 'by_class': {}, 'services': {},
                                                   'files': idx, 'currency': 'USD'}
    p = _root() / 'data.json'
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data), encoding='utf-8')
    tmp.replace(p)
    try:
        export_xlsx(data, _root() / 'Partner_Center_Azure_SevenSeas.xlsx')
    except Exception:
        logging.warning('partner azure excel failed: %s', traceback.format_exc()[-300:])
    return data


def load_data():
    try:
        return json.loads((_root() / 'data.json').read_text(encoding='utf-8'))
    except Exception:
        return None


def export_xlsx(data, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter as L
    months = data['months']
    wb = Workbook()
    head = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', fgColor='0F4C81')

    def sheet(title, header, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = head, fill
        for r in rows:
            ws.append(r)
        ws.freeze_panes = 'C2' if len(header) > 3 else 'A2'
        for i, h in enumerate(header, 1):
            ws.column_dimensions[L(i)].width = 38 if i == 1 else 16
        return ws
    cs = data['customers']
    sheet('Customers by month', ['Customer', 'Agreement', 'Attribution', 'Enrollment'] + months + ['Total', 'MoM %'],
          [[c['name'], c['cls'], ', '.join(c['attrib']), ', '.join(c['enroll'])] + [c['by_month'].get(m, 0) for m in months] +
           [c['total'], c['mom']] for c in cs], first=True)
    rows = []
    for c in cs:
        for s, d in c['services'].items():
            rows.append([c['name'], c['cls'], s] + [d.get(m, 0) for m in months] + [round(sum(d.values()), 2)])
    sheet('Customer services by month', ['Customer', 'Agreement', 'Service'] + months + ['Total'], rows)
    sheet('Portfolio services', ['Service'] + months + ['Total'],
          [[s] + [d.get(m, 0) for m in months] + [round(sum(d.values()), 2)] for s, d in data['services'].items()])
    rows = []
    for c in cs:
        for sid, sb in c['subs'].items():
            rows.append([c['name'], sid, sb.get('state', '')] + [sb['by_month'].get(m, 0) for m in months] + [round(sum(sb['by_month'].values()), 2)])
    sheet('Subscriptions', ['Customer', 'Subscription', 'State'] + months + ['Total'], rows)
    tmp = Path(str(path) + '.tmp')
    wb.save(tmp)
    tmp.replace(path)


def public_state():
    d = load_data() or {}
    idx = _load_index()
    return {'has_data': bool(d.get('customers')), 'generated': d.get('generated'), 'files': idx, 'months': d.get('months', []),
            'next_data': 'Partner Center refreshes the Azure usage report after the 5th of each month.',
            'inbox': 'reports/partner-azure/inbox on the app share'}


def scan_inbox():
    """Ingest any new .csv/.tsv in the inbox folder (also what the daily watcher runs)."""
    done = 0
    for p in sorted((_root() / 'inbox').glob('*')):
        if p.suffix.lower() not in ('.csv', '.tsv', '.txt') or not p.is_file():
            continue
        try:
            r = add_layer(p.read_bytes(), p.name)
            done += 0 if r.get('duplicate') else 1
        except Exception as e:
            logging.warning('partner azure inbox %s: %s', p.name, e)
    return done


def _watcher():
    time.sleep(45)
    while True:
        try:
            scan_inbox()
        except Exception:
            logging.warning('partner azure watcher: %s', traceback.format_exc()[-300:])
        time.sleep(6 * 3600)


def install(handler_cls):
    if getattr(handler_cls, '_pa_installed', False):
        return
    real_get, real_post = handler_cls.do_GET, handler_cls.do_POST
    ROUTES_GET = ('/partner-azure', '/ai/partner-azure/status', '/ai/partner-azure/data', '/ai/partner-azure/download')

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path not in ROUTES_GET:
            return real_get(self)
        if not self._user():
            if path == '/partner-azure':
                self.send_response(302); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers()
                return
            return self.send(401, {'error': 'Sign in first.'})
        if path == '/partner-azure':
            return self.send_raw(200, PAGE.replace('__CGNAV__', NAV).encode('utf-8'), 'text/html; charset=utf-8', 'no-store')
        if path == '/ai/partner-azure/status':
            return self.send(200, public_state())
        if path == '/ai/partner-azure/data':
            p = _root() / 'data.json'
            return self.send_raw(200, p.read_bytes(), 'application/json', 'no-store') if p.is_file() else self.send(404, {'error': 'No data yet.'})
        p = _root() / 'Partner_Center_Azure_SevenSeas.xlsx'
        if not p.is_file():
            return self.send(404, {'error': 'No Excel yet.'})
        b = p.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.send_header('Content-Disposition', 'attachment; filename="Partner_Center_Azure_SevenSeas.xlsx"')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if not path.startswith('/ai/partner-azure/'):
            return real_post(self)
        o = self.headers.get('Origin')
        if o is not None and urllib.parse.urlparse(o).netloc != self.headers.get('Host'):
            return self.send(403, {'error': 'Invalid origin'})
        if not self._user():
            return self.send(401, {'error': 'Sign in first.'})
        try:
            size = int(self.headers.get('Content-Length', '0') or 0)
            if not 0 < size <= MAX_UPLOAD:
                return self.send(413, {'error': 'Upload a CSV up to 200 MB.'})
            body = self.rfile.read(size)
            if path == '/ai/partner-azure/upload':
                name = urllib.parse.unquote(self.headers.get('X-File-Name', 'upload.csv'))
                r = add_layer(body, name)
                return self.send(200, {'result': r, 'state': public_state()})
            payload = json.loads(body or b'{}')
            if path == '/ai/partner-azure/remove':
                remove_layer(str(payload.get('id', '')))
                return self.send(200, {'state': public_state()})
            if path == '/ai/partner-azure/scan':
                return self.send(200, {'added': scan_inbox(), 'state': public_state()})
            return self.send(404, {'error': 'Unknown action.'})
        except ValueError as e:
            return self.send(400, {'error': str(e)})
        except Exception:
            logging.warning('partner azure: %s', traceback.format_exc()[-400:])
            return self.send(500, {'error': 'Could not process the file.'})

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._pa_installed = True
    threading.Thread(target=_watcher, daemon=True).start()


NAV = r"""<script id="ccc-pa-nav">(function(){var L=[['/partner-azure','Partner Center Azure (EA / CSP)','Every Seven Seas Azure customer, EA or CSP, monthly cost and services'],['/boq-compliance','BOQ alignment check','Compare a customer BOQ with what is deployed: what changed, when and by whom'],['/azure-catalog','Azure services and SKUs','Every Azure service and SKU with list prices; price a basket into a BOQ']];
function add(){var n=document.querySelector('nav.workspace-nav');if(!n)return;var t=[].slice.call(n.querySelectorAll('a')).filter(function(x){return /Cost Growth/.test(x.textContent)})[0],prev=t;
L.forEach(function(l){if(n.querySelector('a[href="'+l[0]+'"]'))return;var a=document.createElement('a');a.href=l[0];a.textContent=l[1];a.title=l[2];if(prev&&prev.parentNode){prev.parentNode.insertBefore(a,prev.nextSibling);prev=a}else n.appendChild(a)})}
add();setTimeout(add,800);setTimeout(add,2500);setTimeout(add,5000);}());</script>"""

try:
    from partner_azure_page import PAGE
except Exception:                                   # keeps the module importable in tests that skip the page
    PAGE = '<!doctype html><title>Partner Center Azure</title>Page unavailable.'
