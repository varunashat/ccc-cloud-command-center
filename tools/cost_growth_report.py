"""Cost growth report for every active customer (billing profile) of a billing account - Azure Cost Management.

Runs on your own laptop with plain Python: no AI, no tokens, no credits. Read-only (Cost Management Reader is enough).

What it does
  1. Signs you in to Azure with your Microsoft work account (browser window, remembered on this PC).
  2. Finds the billing account (default: the one whose name contains "Seven Seas") and lists its billing profiles.
  3. For every ACTIVE billing profile (customer) it pulls Actual cost per month (default April-October 2026)
     grouped by subscription + service, region, pricing model (On-demand / Reservation / Savings plan / Spot),
     charge type (Usage / Purchase / Refund) and publisher type (Azure / Marketplace).
  4. Writes one Excel workbook in the pattern of the Al-Futtaim ACR growth report:
       Portfolio Summary, Portfolio Trend, one sheet per customer (monthly growth, RI / savings plan / Marketplace check,
       subscription-wise, service-wise, region-wise, spike analysis + chart), and a flat Data sheet for dashboards.
     It also writes cost_growth_data.json (same data) for the Cloud Command Center dashboard.

Usage (Windows):  Cost-Growth-Report.cmd                  (April-October 2026, all active customers)
                  Cost-Growth-Report.cmd --start 2026-04 --end 2026-10 --profiles 87219556,5151240
Options: --account NAME|ID   --tenant TENANT_ID   --currency usd|billing   --refresh (ignore cached pulls)   --out FOLDER
Cached pulls are kept in reports/cache so a stopped run continues where it stopped.
"""
import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ARM = 'https://management.azure.com'
BILLING_API = '2024-04-01'
PROFILE_APIS = ('2024-08-01-preview', '2026-03-01-preview', '2024-04-01', '2020-05-01')   # EA partner (pcn.) accounts need the preview
DEFAULT_ACCOUNT = '8372436B'                      # Seven Seas Technology (EA partner billing account pcn.8372436B)
COST_API = '2023-11-01'
COST_APIS = ('2023-11-01', '2024-08-01', '2025-03-01')
QUERIES = {                                   # name -> grouping (Cost Management allows two dimensions per query)
    'sub_service': ['SubscriptionName', 'ServiceName'],
    'region': ['ResourceLocation'],
    'pricing': ['PricingModel', 'ChargeType'],
    'publisher': ['PublisherType', 'PublisherName'],
}
SPIKE_PCT, SPIKE_ABS = 0.10, 500.0            # a month is a spike when it moves >=10% and >= 500 vs the month before


# ----------------------------------------------------------------------------- Azure access
class Azure:
    def __init__(self, tenant=None, fake=None, token_fn=None, extra_headers=None, on_denied=None):
        self.fake = fake
        self.tenant = tenant
        self.use_tenant = tenant
        self.token_fn = token_fn                 # portal mode: token taken from your signed-in portal session
        self.extra_headers = extra_headers or {}
        self.on_denied = on_denied              # portal mode: refresh the portal session once on 401

    def _credential(self):
        if hasattr(self, '_cred'):
            return self._cred
        from azure.identity import InteractiveBrowserCredential
        kw = {'tenant_id': self.tenant, 'additionally_allowed_tenants': ['*']}
        try:
            from azure.identity import TokenCachePersistenceOptions
            self._cred = InteractiveBrowserCredential(cache_persistence_options=TokenCachePersistenceOptions(name='ccc-cost-growth'), **kw)
        except Exception:
            self._cred = InteractiveBrowserCredential(**kw)
        return self._cred

    def token(self):
        if self.fake:
            return 'x'
        if self.token_fn:
            return self.token_fn()
        key = self.use_tenant or ''
        tok = getattr(self, '_toks', {}).get(key)
        if tok and tok[1] - time.time() > 300:
            return tok[0]
        kw = {'tenant_id': self.use_tenant} if self.use_tenant else {}
        try:
            t = self._credential().get_token(ARM + '/.default', **kw)
        except Exception as e:                            # e.g. the encrypted token cache is blocked on this PC
            if 'persist' not in str(e).lower() and 'cache' not in str(e).lower():
                raise
            from azure.identity import InteractiveBrowserCredential
            self._cred = InteractiveBrowserCredential(tenant_id=self.tenant, additionally_allowed_tenants=['*'])
            t = self._cred.get_token(ARM + '/.default', **kw)
        self._toks = getattr(self, '_toks', {})
        self._toks[key] = (t.token, t.expires_on)
        return t.token

    def tenants(self):
        try:
            return [t['tenantId'] for t in self.list('/tenants?api-version=2022-12-01') if t.get('tenantId')]
        except RuntimeError:
            return []

    def call(self, method, url, body=None, busy_limit=None):
        if self.fake:
            return self.fake(method, url, body)
        if url.startswith('/'):
            url = globals()['ARM'] + url
        denied_once = False
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(40):
            req = urllib.request.Request(url, data=data, method=method,
                                         headers=dict(self.extra_headers, **{'Authorization': 'Bearer ' + self.token(), 'Content-Type': 'application/json'}))
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                text = e.read().decode('utf-8', 'replace')[:600]
                if e.code >= 500 and attempt >= 6:
                    raise RuntimeError(f'HTTP {e.code}: {text}')
                if e.code == 429 or e.code >= 500:
                    hdr = {k.lower(): v for k, v in (e.headers or {}).items()}
                    waits = {}
                    for k, v in hdr.items():
                        if 'retry-after' in k:
                            try:
                                waits[k] = float(v)
                            except ValueError:
                                pass
                    wait = max(waits.values()) if waits else 10 * (attempt + 1)
                    which = max(waits, key=waits.get) if waits else ''
                    per_customer = 'entity' in which          # only this customer's scope is throttled
                    if e.code == 429 and per_customer and busy_limit is not None and attempt >= busy_limit:
                        raise Busy(f'HTTP {e.code}: Azure is throttling this customer')
                    if e.code == 429 and not per_customer and waits:
                        # the hourly/minute query quota of your sign-in is used up: Azure says exactly how long
                        # to wait, and every customer would get the same answer, so wait and carry on
                        wait = min(wait + 2, 900)
                        until = (dt.datetime.now() + dt.timedelta(seconds=wait)).strftime('%H:%M:%S')
                        print(f'    Azure query quota used up for now; continuing by itself at {until} ({wait:.0f}s) ...', flush=True)
                    else:
                        wait = min(wait or 10, 60)
                        print(f'    Azure is busy ({e.code}); waiting {wait:.0f}s ...', flush=True)
                    time.sleep(wait)
                    continue
                if e.code == 401 and self.on_denied and not denied_once:
                    denied_once = True                   # token expired: let the portal hand over a fresh one
                    self.on_denied()
                    continue
                raise RuntimeError(f'HTTP {e.code}: {text}')
            except (TimeoutError, OSError) as e:          # big customers: Azure can take minutes to answer
                if isinstance(e, urllib.error.URLError) and not isinstance(getattr(e, 'reason', None), TimeoutError) and attempt >= 3:
                    raise RuntimeError(f'Network error: {e}')
                if attempt < 4:
                    print('    Azure is slow to answer; asking again ...', flush=True)
                    time.sleep(10)
                    continue
                raise RuntimeError(f'Azure did not answer in time: {e}')
            except urllib.error.URLError as e:
                if attempt < 3:
                    time.sleep(5)
                    continue
                raise RuntimeError(f'Network error: {e}')
        raise RuntimeError('Azure kept answering "busy"; run again later (finished customers are cached).')

    def list(self, url):
        out = []
        while url:
            j = self.call('GET', url)
            out += j.get('value', [])
            url = j.get('nextLink')
        return out


def signed_in_as(az):
    try:
        import base64
        part = az.token().split('.')[1]
        c = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        return c.get('upn') or c.get('unique_name') or c.get('preferred_username') or '?', c.get('tid', '?')
    except Exception:
        return '?', '?'


def _profiles(az, acct_id):
    last = None
    for v in PROFILE_APIS:
        try:
            return az.list(f'{acct_id}/billingProfiles?api-version={v}')
        except RuntimeError as e:
            last = e
    raise last


def find_account(az, want):
    """Opens the billing account directly (EA partner accounts are not in the account list), else searches the list."""
    want = (want or DEFAULT_ACCOUNT).strip()
    names = [want] if want.startswith('pcn.') or '/' in want else ['pcn.' + want, want]
    errs = []
    for n in names:
        aid = n if n.startswith('/providers/') else f'/providers/Microsoft.Billing/billingAccounts/{n}'
        try:
            profs = _profiles(az, aid)
            return {'id': aid, 'name': n, 'properties': {'displayName': f'Seven Seas technology ({n.replace("pcn.", "")})'
                                                         if n.replace('pcn.', '') == DEFAULT_ACCOUNT else n}}, profs
        except RuntimeError as e:
            errs.append(f'{n}: {str(e)[:220]}')
    accts = []
    try:
        accts = az.list(f'/providers/Microsoft.Billing/billingAccounts?api-version={BILLING_API}')
    except RuntimeError as e:
        errs.append(str(e)[:220])
    w = want.lower()
    hit = [a for a in accts if w in (a.get('name') or '').lower() or w in ((a.get('properties') or {}).get('displayName') or '').lower()]
    if hit:
        return hit[0], _profiles(az, hit[0]['id'])
    print('Could not open billing account "' + want + '":')
    for e in errs:
        print('   ' + e)
    if accts:
        print('Billing accounts in the list for this sign-in:')
        for a in accts:
            print(f"  - {(a.get('properties') or {}).get('displayName') or a.get('name')}  [{a.get('name')}]")
    raise SystemExit('Use --account with the ID shown in the portal, e.g. --account 8372436B.')


def list_customers(az, acct, profs, only=None):
    """Billing profiles of the account (each EA enrollment / customer). Active only, unless named in --profiles."""
    out = []
    for b in profs:
        p = b.get('properties') or {}
        out.append({'id': b['id'], 'code': b.get('name'), 'name': p.get('displayName') or b.get('name'),
                    'status': p.get('status') or 'Active', 'read': p.get('hasReadAccess', True), 'currency': p.get('currency')})
    if len(out) <= 1:                                     # MCA partner account: customers sit under one billing profile
        for bp in list(out):
            try:
                for c in az.list(f"{bp['id']}/customers?api-version={BILLING_API}"):
                    p = c.get('properties') or {}
                    out.append({'id': c['id'], 'code': c.get('name'), 'name': p.get('displayName') or c.get('name'),
                                'status': p.get('status') or 'Active', 'read': True, 'currency': None})
            except RuntimeError:
                pass
    if only:
        keys = {k.strip().lower() for k in only.split(',') if k.strip()}
        act = [c for c in out if (c['code'] or '').lower() in keys or c['name'].lower() in keys]
    else:
        act = [c for c in out if str(c['status']).lower() in ('active', 'enabled', '')]
    noread = [c for c in act if c['read'] is False]
    if noread:
        print(f'  {len(noread)} active profile(s) without read access are skipped: ' + ', '.join(c['name'] for c in noread[:8]))
    seen, res = set(), []
    for c in act:
        if c['id'] not in seen and c['read'] is not False:
            seen.add(c['id'])
            res.append(c)
    return res


def pick_directory(az, cust, months, usd):
    """Cost data is only readable with a sign-in from the directory where your billing role lives (for Seven Seas:
    Noventiq Holdings). Try the current directory first, then every other directory of this account, and keep the first
    one that can read costs. No manual setting needed."""
    tried = []
    order = [az.use_tenant] + [t for t in az.tenants() if t != az.use_tenant]
    for t in order:
        az.use_tenant = t
        try:
            query(az, cust['id'], ['ServiceName'], months[-1:], usd)
            if tried:
                print(f'  Cost data is readable from directory {t} - using it.', flush=True)
            return t
        except RuntimeError as e:
            if 'HTTP 401' not in str(e) and 'HTTP 403' not in str(e):
                raise
            tried.append(t or 'home')
            print(f'  Directory {t or "(home)"}: no cost access, trying the next one ...', flush=True)
    az.use_tenant = order[0]
    print('  No directory of this account can read Cost Management for these billing profiles.\n'
          '  Ask the Seven Seas billing admin for "Enterprise/Billing profile reader" (cost view) on the account.', flush=True)
    return None


class Busy(RuntimeError):
    """Cost Management keeps answering 429 for this customer: move on and retry it at the end."""


BUSY_LIMIT = 1           # 429 waits per cost call before moving on to the next customer
PACE = 3.0               # seconds between cost calls (Cost Management allows only a few queries per minute)
WORKING = []             # (api-version, aggregation index) that worked: used first, so no failing probes are repeated
BAD_DIMS = {}           # grouping Azure rejected -> what is used instead ('' = dropped); learnt once, used for all customers
DIM_SUBST = {'PublisherName': 'ServiceName', 'PricingModel': 'ChargeType', 'SubscriptionName': 'SubscriptionId',
             'ServiceName': 'MeterCategory', 'ResourceLocation': 'ResourceGroupName'}
SCOPE_PREF = []          # which scope form worked last ('profile' or 'enrollment'), tried first for the next customer


def scope_forms(c):
    code = str(c['code'])
    forms = {'profile': c['id'], 'enrollment': f'/providers/Microsoft.Billing/billingAccounts/{code}'}
    order = SCOPE_PREF + [k for k in ('profile', 'enrollment') if k not in SCOPE_PREF]
    return [(k, forms[k]) for k in order]


def pick_scope(az, c, months, usd, cache_dir=None, first=None):
    """EA partner accounts: costs of a billing profile are readable either at the billing profile scope or at the
    customer's own EA enrollment scope (billingAccounts/<enrollment number>), depending on your role. Use the first
    one that answers and remember it for the next customers."""
    if cache_dir and os.path.isfile(os.path.join(cache_dir, 'scope.txt')):
        with open(os.path.join(cache_dir, 'scope.txt'), encoding='utf-8') as f:
            return f.read().strip()
    last = None
    for kind, scope in scope_forms(c):
        try:
            rows = query(az, scope, QUERIES['sub_service'], months, usd)
            if first is not None:
                first[:] = [rows]
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
                with open(os.path.join(cache_dir, 'scope.txt'), 'w', encoding='utf-8') as f:
                    f.write(scope)
            if not SCOPE_PREF or SCOPE_PREF[0] != kind:
                SCOPE_PREF[:] = [kind]
                print(f'    reading costs at the {"billing profile" if kind == "profile" else "EA enrollment"} scope', flush=True)
            return scope
        except RuntimeError as e:
            last = e
            if not any(k in str(e) for k in ('HTTP 401', 'HTTP 403', 'HTTP 404', 'BillingAccessDenied')):
                raise
    raise last


UC_STATE = {}


def _amt(v):
    if isinstance(v, dict):
        v = v.get('value')
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def billing_charges(az, c, months, scope_hint=None, cache_file=None):
    """Billing > Usage + charges for one customer, per month: {'azure': {m: x}, 'marketplace': {m: x}, 'currency': '..'}.
    A second, independent source used only to cross-check Cost analysis. Returns None if it cannot be read."""
    if cache_file and os.path.isfile(cache_file):
        with open(cache_file, encoding='utf-8') as f:
            return json.load(f)
    y2, m2 = map(int, months[-1].split('-'))
    start, end = months[0] + '-01', f'{months[-1]}-{calendar.monthrange(y2, m2)[1]:02d}'
    if UC_STATE.get('off'):
        return None
    forms = {'profile': c['id'], 'enrollment': f"/providers/Microsoft.Billing/billingAccounts/{c['code']}"}
    qss = (f'startDate={start}&endDate={end}',
           urllib.parse.quote(f"$filter=usageStart ge '{start}' and usageEnd le '{end}'", safe="$=&'"))
    combos = [(k, v, q) for k in forms for v in ('2023-05-01', '2021-10-01', '2019-10-01') for q in range(len(qss))]
    if UC_STATE.get('ok'):
        combos = [UC_STATE['ok']]                     # learnt from an earlier customer: one call per customer
    j = None
    for k, v, q in combos:
        try:
            j = az.call('GET', f'{forms[k]}/providers/Microsoft.Consumption/charges?api-version={v}&{qss[q]}', busy_limit=1)
            UC_STATE['ok'] = (k, v, q)
            break
        except Busy:
            return None
        except Exception:
            j = None
    if j is None:
        UC_STATE['fails'] = UC_STATE.get('fails', 0) + 1
        if not UC_STATE.get('ok') and UC_STATE['fails'] >= 2:
            UC_STATE['off'] = True
            print('    note: Billing > Usage + charges is not readable through the API for this account; '
                  'the cross-check is skipped (Cost analysis figures are unaffected)', flush=True)
        return None
    out = {'azure': {m: 0.0 for m in months}, 'marketplace': {m: 0.0 for m in months}, 'currency': ''}
    for it in j.get('value') or []:
        p = it.get('properties') or {}
        m = None
        bp = str(p.get('billingPeriodId') or '')
        mm = re.search(r'(\d{4})(\d{2})\d{0,2}$', bp)
        if mm:
            m = f'{mm.group(1)}-{mm.group(2)}'
        elif p.get('usageStart'):
            m = month_key(p['usageStart'])
        if m not in out['azure']:
            continue
        out['azure'][m] += _amt(p.get('azureCharges'))
        out['marketplace'][m] += _amt(p.get('azureMarketplaceCharges') if 'azureMarketplaceCharges' in p else p.get('marketplaceCharges'))
        out['currency'] = out['currency'] or p.get('currency') or ((p.get('azureCharges') or {}).get('currency') if isinstance(p.get('azureCharges'), dict) else '') or ''
    if cache_file:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(out, f)
    return out


def months_between(start, end):
    y, m = map(int, start.split('-'))
    y2, m2 = map(int, end.split('-'))
    out = []
    while (y, m) <= (y2, m2):
        out.append(f'{y:04d}-{m:02d}')
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def month_key(v):
    s = str(v)
    if re.fullmatch(r'\d{8}', s):
        return s[:4] + '-' + s[4:6]
    m = re.match(r'(\d{4})-(\d{2})', s)
    return m.group(0) if m else s[:7]


def query(az, scope, grouping, months, want_usd, cache_file=None):
    """Monthly Actual cost rows: list of dicts {month, <dims>..., cost, currency}."""
    if cache_file and os.path.isfile(cache_file):
        with open(cache_file, encoding='utf-8') as f:
            return json.load(f)
    y2, m2 = map(int, months[-1].split('-'))
    body = {'type': 'ActualCost', 'timeframe': 'Custom',
            'timePeriod': {'from': months[0] + '-01T00:00:00Z',
                           'to': f'{months[-1]}-{calendar.monthrange(y2, m2)[1]:02d}T23:59:59Z'},
            'dataset': {'granularity': 'Monthly',
                        'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
                        'grouping': [{'type': 'Dimension', 'name': g} for g in grouping]}}
    url = f'{scope}/providers/Microsoft.CostManagement/query?api-version={COST_API}'
    rows = []
    aggs = ([{'totalCost': {'name': 'Cost', 'function': 'Sum'}, 'totalCostUSD': {'name': 'CostUSD', 'function': 'Sum'}}] if want_usd else []) + \
        [{'totalCost': {'name': 'Cost', 'function': 'Sum'}}, {'totalCost': {'name': 'PreTaxCost', 'function': 'Sum'}}]
    want = list(grouping)

    def actual_dims():
        out, used = [], set()
        for g in want:
            x = g
            while x in BAD_DIMS:
                x = BAD_DIMS[x]
            if x and x not in used:
                out.append(x)
                used.add(x)
        return out

    def src(g):                                       # column that answers requested dimension g
        x = g
        while x in BAD_DIMS:
            x = BAD_DIMS[x]
        return x
    body['dataset']['grouping'] = [{'type': 'Dimension', 'name': g} for g in actual_dims()]
    j, last = None, None
    combos = [(v, i) for v in COST_APIS for i in range(len(aggs))]
    combos = [w for w in WORKING if w in combos] + [w for w in combos if w not in WORKING]
    for ver, ai in combos:
        url = f'{scope}/providers/Microsoft.CostManagement/query?api-version={ver}'
        if True:
            body['dataset']['aggregation'] = aggs[ai]
            try:
                j = az.call('POST', url, body, busy_limit=BUSY_LIMIT)
                WORKING[:] = [(ver, ai)]
                if not az.fake:
                    time.sleep(PACE)
                break
            except RuntimeError as e:
                last = e
                if 'HTTP 400' not in str(e):
                    raise
                bad = re.search(r"Invalid dataset grouping: '([^']+)'", str(e))
                if bad:                               # this dimension is not offered for this account: substitute it
                    d = bad.group(1)
                    alt = DIM_SUBST.get(d, '')
                    BAD_DIMS[d] = alt if alt and alt not in BAD_DIMS else ''
                    print(f"    note: Azure has no '{d}' here; using {BAD_DIMS[d] or 'nothing'} instead", flush=True)
                    return query(az, scope, grouping, months, want_usd, cache_file)
    if j is None:
        raise last
    while True:
        p = j.get('properties') or {}
        cols = [c['name'] for c in p.get('columns') or []]
        low = [c.lower() for c in cols]
        ci = low.index('costusd') if want_usd and 'costusd' in low else next(
            (low.index(k) for k in ('cost', 'pretaxcost', 'costinbillingcurrency') if k in low), None)
        mi = next((low.index(k) for k in ('billingmonth', 'usagedate', 'billingperiod', 'date') if k in low), None)
        cur = low.index('currency') if 'currency' in low else None
        for r in p.get('rows') or []:
            d = {'month': month_key(r[mi]) if mi is not None else months[0],
                 'cost': float(r[ci] or 0),
                 'currency': 'USD' if (want_usd and 'costusd' in low) else (r[cur] if cur is not None else '')}
            for g in grouping:
                gi = low.index(src(g).lower()) if src(g) and src(g).lower() in low else None
                d[g] = (r[gi] if gi is not None else '') or '(none)'
            rows.append(d)
        nxt = p.get('nextLink')
        if not nxt:
            break
        j = az.call('POST', nxt, body, busy_limit=BUSY_LIMIT + 2)
    if cache_file:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(rows, f)
    return rows


def pivot(rows, keyf, months):
    out = {}
    for r in rows:
        if r['month'] not in months:
            continue
        k = keyf(r)
        out.setdefault(k, {m: 0.0 for m in months})[r['month']] += r['cost']
    return out


def analyse(cust, data, months):
    """Plain-number analysis of one customer (used for the summary columns and the spike text)."""
    ss = data['sub_service']
    total = {m: 0.0 for m in months}
    for r in ss:
        if r['month'] in total:
            total[r['month']] += r['cost']
    pub = pivot(data['publisher'], lambda r: r['PublisherType'], months)
    mkt = pub.get('Marketplace', {m: 0.0 for m in months})
    price = pivot(data['pricing'], lambda r: r['PricingModel'], months)
    charge = pivot(data['pricing'], lambda r: r['ChargeType'], months)
    subs = pivot(ss, lambda r: r['SubscriptionName'], months)
    svcs = pivot(ss, lambda r: r['ServiceName'], months)
    pair = pivot(ss, lambda r: (r['SubscriptionName'], r['ServiceName']), months)
    first, last = months[0], months[-1]

    def top_growth(piv):
        best = max(piv.items(), key=lambda kv: kv[1][last] - kv[1][first], default=None)
        return (best[0], best[1][last] - best[1][first]) if best else ('', 0.0)

    spikes = []
    for i in range(1, len(months)):
        a, b = months[i - 1], months[i]
        d = total[b] - total[a]
        if abs(d) >= SPIKE_ABS and (total[a] == 0 or abs(d) / max(total[a], 1e-9) >= SPIKE_PCT):
            movers = sorted(pair.items(), key=lambda kv: -(kv[1][b] - kv[1][a]) if d > 0 else (kv[1][b] - kv[1][a]))[:3]
            spikes.append({'from': a, 'to': b, 'change': d, 'pct': (d / total[a]) if total[a] else None,
                           'drivers': [(s, v, p[b] - p[a]) for (s, v), p in movers if abs(p[b] - p[a]) > 0.5]})
    big = max(spikes, key=lambda s: s['change'], default=None)

    def tot(piv, *names):
        return sum(sum(piv.get(n, {}).values()) for n in names)

    currencies = sorted({r['currency'] for r in ss if r.get('currency')})
    return {'total': total, 'marketplace': mkt, 'pricing': price, 'charge': charge, 'subs': subs, 'svcs': svcs,
            'region': pivot(data['region'], lambda r: r['ResourceLocation'], months),
            'pub_names': pivot([r for r in data['publisher'] if r['PublisherType'] == 'Marketplace'],
                               lambda r: r['PublisherName'], months),
            'spikes': spikes, 'biggest_spike': big, 'top_sub': top_growth(subs), 'top_svc': top_growth(svcs),
            'ri': tot(price, 'Reservation'), 'sp': tot(price, 'SavingsPlan', 'Savings Plan'), 'spot': tot(price, 'Spot'),
            'purchase': tot(charge, 'Purchase'), 'mkt_total': sum(mkt.values()),
            'currency': currencies[0] if len(currencies) == 1 else ('/'.join(currencies) or 'USD')}


# ----------------------------------------------------------------------------- Excel
def build_workbook(results, months, path, account_name, pulled):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as L

    F = 'Arial'
    BLUE, GREEN, BLACK = Font(name=F, color='0000FF', size=10), Font(name=F, color='008000', size=10), Font(name=F, size=10)
    HEAD = Font(name=F, bold=True, color='FFFFFF', size=10)
    HFILL = PatternFill('solid', fgColor='0B408B')
    TITLE = Font(name=F, bold=True, size=14, color='0B408B')
    SUB = Font(name=F, bold=True, size=11, color='0B408B')
    NOTE = Font(name=F, italic=True, size=9, color='555555')
    BOLD = Font(name=F, bold=True, size=10)
    TFILL = PatternFill('solid', fgColor='E8EEF7')
    thin = Side(style='thin', color='D0D7E2')
    BOX = Border(top=thin, bottom=thin, left=thin, right=thin)
    MONEY = '#,##0.00;[Red]-#,##0.00;"-"'
    PCT = '0.0%;[Red]-0.0%;"-"'
    MLABEL = [dt.date(int(m[:4]), int(m[5:]), 1).strftime('%b %Y') for m in months]
    n = len(months)
    src = f'Source: Azure Cost Management - {account_name}, Actual cost, monthly, pulled {pulled}. Blue = values from Azure, black = formulas, green = links to other sheets. Negative numbers are red with a minus sign.'

    wb = Workbook()
    ps = wb.active
    ps.title = 'Portfolio Summary'

    def header(ws, row, labels, col=1):
        for i, t in enumerate(labels):
            c = ws.cell(row=row, column=col + i, value=t)
            c.font, c.fill, c.border = HEAD, HFILL, BOX
            c.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
        ws.row_dimensions[row].height = 30

    def put(ws, row, col, v, font=BLACK, fmt=None, bold=False):
        c = ws.cell(row=row, column=col, value=v)
        c.font = Font(name=F, size=10, bold=bold, color=font.color) if bold else font
        c.border = BOX
        if fmt:
            c.number_format = fmt
        return c

    def widths(ws, first=34, rest=14, cols=20):
        ws.column_dimensions['A'].width = first
        for i in range(2, cols + 1):
            ws.column_dimensions[L(i)].width = rest

    def matrix(ws, row, title, label, piv, top=None, note=None, other_label='Other'):
        """Title + table label | months... | change first->last ($,%) | change last-1 -> last; returns next free row."""
        ws.cell(row=row, column=1, value=title).font = SUB
        row += 1
        if note:
            ws.cell(row=row, column=1, value=note).font = NOTE
            row += 1
        header(ws, row, [label] + MLABEL + [f'Change {MLABEL[0][:3]}->{MLABEL[-1][:3]}', 'Change %', f'Change {MLABEL[-2][:3]}->{MLABEL[-1][:3]}'])
        row += 1
        items = sorted(piv.items(), key=lambda kv: -kv[1][months[-1]] - 1e-6 * sum(kv[1].values()))
        items = [kv for kv in items if any(abs(v) > 0.004 for v in kv[1].values())]
        rest = items[top:] if top else []
        items = items[:top] if top else items
        if rest:
            agg = {m: sum(kv[1][m] for kv in rest) for m in months}
            items.append((f'{other_label} ({len(rest)} more)', agg))
        if not items:
            ws.cell(row=row, column=1, value='No cost in this period.').font = NOTE
            return row + 2
        r0 = row
        for name, vals in items:
            put(ws, row, 1, str(name))
            for j, m in enumerate(months):
                put(ws, row, 2 + j, round(vals[m], 2), BLUE, MONEY)
            a, b, p = L(2), L(1 + n), L(n)
            put(ws, row, 2 + n, f'={b}{row}-{a}{row}', BLACK, MONEY)
            put(ws, row, 3 + n, f'=IF({a}{row}=0,0,{L(2 + n)}{row}/{a}{row})', BLACK, PCT)
            put(ws, row, 4 + n, f'={b}{row}-{p}{row}', BLACK, MONEY)
            row += 1
        put(ws, row, 1, 'TOTAL', BLACK, bold=True).fill = TFILL
        for j in range(n + 3):
            col = 2 + j
            if j == n + 1:
                f = f'=IF({L(2)}{row}=0,0,{L(2 + n)}{row}/{L(2)}{row})'
                put(ws, row, col, f, BLACK, PCT, bold=True).fill = TFILL
            else:
                put(ws, row, col, f'=SUM({L(col)}{r0}:{L(col)}{row - 1})', BLACK, MONEY, bold=True).fill = TFILL
        return row + 2

    used_names = set()

    def sheet_name(cust):
        code = str(cust.get('code') or '')
        name = re.sub(r'\s+', ' ', re.sub(r'[\[\]\*\?/\\:]', ' ', str(cust.get('name') or ''))).strip() or 'Customer'
        base = (name[:30 - len(code)].rstrip(' -') + ' ' + code).strip() if code else name[:28]
        s, k = base, 2
        while s.lower() in used_names:
            s = f'{base[:25]} {k}'
            k += 1
        used_names.add(s.lower())
        return s

    cust_rows = []
    for cust, an, err in results:
        if err:
            continue
        ws = wb.create_sheet(sheet_name(cust))
        widths(ws, 40, 14, n + 6)
        cur = an['currency']
        ws['A1'] = f"{cust['name']} - Azure cost growth {MLABEL[0]} to {MLABEL[-1]} ({cur})"
        ws['A1'].font = TITLE
        ws['A2'] = src.replace(account_name, f"{account_name} / billing profile {cust['code']}")
        ws['A2'].font = NOTE
        ws['A3'] = 'Recurring = total minus third-party Marketplace (PublisherType = Marketplace), which is usually one-off. Growth is measured against the first month.'
        ws['A3'].font = NOTE
        header(ws, 5, ['Month', f'Total cost ({cur})', 'Marketplace (third-party)', 'Recurring (excl. Marketplace)',
                       f'Growth vs {MLABEL[0][:3]} ($)', f'Growth vs {MLABEL[0][:3]} (%)', 'Change vs previous month ($)',
                       'Change vs previous month (%)', 'Direction vs previous month'])
        r0 = 6
        for i, m in enumerate(months):
            r = r0 + i
            put(ws, r, 1, MLABEL[i])
            put(ws, r, 2, round(an['total'][m], 2), BLUE, MONEY)
            put(ws, r, 3, round(an['marketplace'][m], 2), BLUE, MONEY)
            put(ws, r, 4, f'=B{r}-C{r}', BLACK, MONEY)
            put(ws, r, 5, f'=D{r}-$D${r0}', BLACK, MONEY)
            put(ws, r, 6, f'=IF($D${r0}=0,0,E{r}/$D${r0})', BLACK, PCT)
            if i:
                put(ws, r, 7, f'=D{r}-D{r - 1}', BLACK, MONEY)
                put(ws, r, 8, f'=IF(D{r - 1}=0,0,G{r}/D{r - 1})', BLACK, PCT)
                put(ws, r, 9, f'=IF(G{r}>=0,"▲ Up","▼ Down")')
            else:
                for c in (7, 8, 9):
                    put(ws, r, c, '')
        rl = r0 + n - 1
        r = rl + 1
        put(ws, r, 1, f'TOTAL {MLABEL[0]} - {MLABEL[-1]}', BLACK, bold=True).fill = TFILL
        for c in (2, 3, 4):
            put(ws, r, c, f'=SUM({L(c)}{r0}:{L(c)}{rl})', BLACK, MONEY, bold=True).fill = TFILL
        put(ws, r + 1, 1, 'Average per month')
        put(ws, r + 1, 4, f'=AVERAGE(D{r0}:D{rl})', BLACK, MONEY)
        put(ws, r + 2, 1, f'{MLABEL[-1][:3]} vs {MLABEL[0][:3]} (run-rate change)')
        put(ws, r + 2, 5, f'=E{rl}', BLACK, MONEY)
        put(ws, r + 2, 6, f'=F{rl}', BLACK, PCT)
        put(ws, r + 3, 1, 'Compound monthly growth (recurring)')
        put(ws, r + 3, 6, f'=IF(OR(D{r0}<=0,D{rl}<=0),0,(D{rl}/D{r0})^(1/{n - 1})-1)', BLACK, PCT)
        put(ws, r + 4, 1, 'Peak month')
        put(ws, r + 4, 2, f'=INDEX(A{r0}:A{rl},MATCH(MAX(B{r0}:B{rl}),B{r0}:B{rl},0))')
        ch = LineChart()
        ch.title, ch.height, ch.width, ch.roundedCorners = 'Monthly cost', 7.5, 17, False
        ch.add_data(Reference(ws, min_col=2, min_row=5, max_row=rl), titles_from_data=True)
        ch.add_data(Reference(ws, min_col=4, min_row=5, max_row=rl), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=r0, max_row=rl))
        ch.legend.position, ch.legend.overlay, ch.title.overlay = 'b', False, False
        ch.x_axis.delete = ch.y_axis.delete = False
        ws.add_chart(ch, 'K5')

        row = r + 7
        ws.cell(row=row, column=1, value='Reservations (RI), savings plans and Marketplace - check').font = SUB
        row += 1
        findings = [
            ('Reservations (RI) cost in period', an['ri'], 'Yes - the customer has reservations' if an['ri'] > 0.5 else 'No reservation charges'),
            ('Savings plan cost in period', an['sp'], 'Yes - savings plan in use' if an['sp'] > 0.5 else 'No savings plan charges'),
            ('Spot cost in period', an['spot'], 'Spot VMs used' if an['spot'] > 0.5 else 'No spot'),
            ('Purchases (one-off: RI / SP / Marketplace)', an['purchase'], 'One-off purchases inside the period' if an['purchase'] > 0.5 else 'No one-off purchases'),
            ('Marketplace (third-party) cost in period', an['mkt_total'], 'Marketplace purchases present' if an['mkt_total'] > 0.5 else 'No Marketplace'),
        ]
        header(ws, row, ['Check', f'Amount ({cur})', 'Finding'])
        row += 1
        for lbl, v, txt in findings:
            put(ws, row, 1, lbl)
            put(ws, row, 2, round(v, 2), BLUE, MONEY)
            put(ws, row, 3, txt)
            row += 1
        row += 1
        row = matrix(ws, row, 'Pricing model by month', 'Pricing model', an['pricing'])
        row = matrix(ws, row, 'Charge type by month', 'Charge type', an['charge'],
                     note='Purchase = one-off reservation, savings plan or Marketplace purchase; Refund = returned reservations or credits.')
        if an['pub_names']:
            row = matrix(ws, row, 'Marketplace by publisher / offer', 'Publisher / offer', an['pub_names'])
        row = matrix(ws, row, 'Subscription-wise', 'Subscription', an['subs'])
        row = matrix(ws, row, 'Service-wise (top 30)', 'Service', an['svcs'], top=30, other_label='Other services')
        row = matrix(ws, row, 'Region-wise', 'Region', an['region'], top=20, other_label='Other regions')

        ws.cell(row=row, column=1, value=f'Spike analysis (months that moved >= {SPIKE_PCT:.0%} and >= {SPIKE_ABS:,.0f} vs the month before)').font = SUB
        row += 1
        header(ws, row, ['Month', 'Change ($)', 'Change (%)', 'Top driver (subscription / service)', 'Driver change ($)',
                         '2nd driver', '2nd change ($)', '3rd driver', '3rd change ($)'])
        row += 1
        if not an['spikes']:
            ws.cell(row=row, column=1, value='No spikes: every month moved less than the threshold.').font = NOTE
            row += 1
        for sp in an['spikes']:
            put(ws, row, 1, f"{dt.date(int(sp['from'][:4]), int(sp['from'][5:]), 1):%b} -> {dt.date(int(sp['to'][:4]), int(sp['to'][5:]), 1):%b %Y}")
            put(ws, row, 2, round(sp['change'], 2), BLUE, MONEY)
            put(ws, row, 3, sp['pct'] if sp['pct'] is not None else 0, BLUE, PCT)
            for k, (s, v, d) in enumerate(sp['drivers'][:3]):
                put(ws, row, 4 + 2 * k, f'{s} / {v}')
                put(ws, row, 5 + 2 * k, round(d, 2), BLUE, MONEY)
            row += 1
        ws.freeze_panes = 'B6'
        cust_rows.append((cust, an, ws.title, r0, rl))

    # portfolio summary
    ps['A1'] = f'{account_name} - Azure cost growth by customer, {MLABEL[0]} to {MLABEL[-1]}'
    ps['A1'].font = TITLE
    ps['A2'] = src
    ps['A2'].font = NOTE
    ps['A3'] = (f'One row per active billing profile. Monthly values link to each customer sheet (recurring = excl. Marketplace). '
                f'Spike = a month that moved >= {SPIKE_PCT:.0%} and >= {SPIKE_ABS:,.0f}. RI / SP / Marketplace amounts are totals for the period.')
    ps['A3'].font = NOTE
    cols = (['Customer', 'Billing profile', 'Currency'] + MLABEL +
            [f'Total {MLABEL[0][:3]}-{MLABEL[-1][:3]}', f'Change {MLABEL[0][:3]}->{MLABEL[-1][:3]} ($)', 'Change (%)',
             'Compound monthly growth', 'Peak month', 'Biggest monthly spike', 'Top growth subscription', 'Top growth service',
             'Reservations (RI)', 'Savings plan', 'Marketplace', 'Flags'])
    header(ps, 5, cols)
    ps.column_dimensions['A'].width = 38
    ps.column_dimensions['B'].width = 16
    ps.column_dimensions['C'].width = 9
    for i in range(4, len(cols) + 1):
        ps.column_dimensions[L(i)].width = 14
    for c in ('Biggest monthly spike', 'Top growth subscription', 'Top growth service', 'Flags'):
        ps.column_dimensions[L(cols.index(c) + 1)].width = 34
    row = 6
    cust_rows.sort(key=lambda t: -t[1]['total'][months[-1]])
    for cust, an, sname, r0, rl in cust_rows:
        q = "'" + sname.replace("'", "''") + "'"
        put(ps, row, 1, cust['name'])
        put(ps, row, 2, str(cust['code']))
        put(ps, row, 3, an['currency'])
        for j in range(n):
            put(ps, row, 4 + j, f'={q}!D{r0 + j}', GREEN, MONEY)
        a, b = L(4), L(3 + n)
        put(ps, row, 4 + n, f'=SUM({a}{row}:{b}{row})', BLACK, MONEY)
        put(ps, row, 5 + n, f'={b}{row}-{a}{row}', BLACK, MONEY)
        put(ps, row, 6 + n, f'=IF({a}{row}=0,0,{L(5 + n)}{row}/{a}{row})', BLACK, PCT)
        put(ps, row, 7 + n, f'=IF(OR({a}{row}<=0,{b}{row}<=0),0,({b}{row}/{a}{row})^(1/{n - 1})-1)', BLACK, PCT)
        put(ps, row, 8 + n, f'=INDEX($D$5:${b}$5,MATCH(MAX({a}{row}:{b}{row}),{a}{row}:{b}{row},0))')
        bs = an['biggest_spike']
        put(ps, row, 9 + n, (f"{dt.date(int(bs['to'][:4]), int(bs['to'][5:]), 1):%b}: +{bs['change']:,.0f}"
                             + (f" - {bs['drivers'][0][1]} ({bs['drivers'][0][0]})" if bs['drivers'] else '')) if bs and bs['change'] > 0 else 'No upward spike')
        put(ps, row, 10 + n, f"{an['top_sub'][0]} ({an['top_sub'][1]:+,.0f})" if an['top_sub'][0] else '')
        put(ps, row, 11 + n, f"{an['top_svc'][0]} ({an['top_svc'][1]:+,.0f})" if an['top_svc'][0] else '')
        put(ps, row, 12 + n, round(an['ri'], 2), BLUE, MONEY)
        put(ps, row, 13 + n, round(an['sp'], 2), BLUE, MONEY)
        put(ps, row, 14 + n, round(an['mkt_total'], 2), BLUE, MONEY)
        flags = [t for t, ok in (('RI', an['ri'] > 0.5), ('Savings plan', an['sp'] > 0.5), ('Marketplace', an['mkt_total'] > 0.5),
                                 ('One-off purchases', an['purchase'] > 0.5), (f"{len(an['spikes'])} spike month(s)", bool(an['spikes'])),
                                 (f"Billing check: {an.get('billing_status')}", str(an.get('billing_status', '')).startswith('Differs'))) if ok]
        put(ps, row, 15 + n, ', '.join(flags) or '-')
        ps.cell(row=row, column=1).hyperlink = f'#{q}!A1'
        row += 1
    last = row - 1
    put(ps, row, 1, 'PORTFOLIO TOTAL', BLACK, bold=True).fill = TFILL
    if len({an['currency'] for _, an, *_ in cust_rows}) > 1:
        ps.cell(row=row + 1, column=1, value='Customers are in different currencies: the total mixes them. Run with --currency usd for one currency.').font = Font(name=F, color='FF0000', size=9)
    for j in range(n + 4):
        col = 4 + j
        if j == n + 2:
            put(ps, row, col, f'=IF({L(4)}{row}=0,0,{L(5 + n)}{row}/{L(4)}{row})', BLACK, PCT, bold=True).fill = TFILL
        elif j == n + 3:
            put(ps, row, col, f'=IF(OR({L(4)}{row}<=0,{L(3 + n)}{row}<=0),0,({L(3 + n)}{row}/{L(4)}{row})^(1/{n - 1})-1)', BLACK, PCT, bold=True).fill = TFILL
        else:
            put(ps, row, col, f'=SUM({L(col)}6:{L(col)}{last})', BLACK, MONEY, bold=True).fill = TFILL
    for col in (12 + n, 13 + n, 14 + n):
        put(ps, row, col, f'=SUM({L(col)}6:{L(col)}{last})', BLACK, MONEY, bold=True).fill = TFILL
    ps.freeze_panes = 'D6'
    tot_row = row
    failed = [(c, e) for c, a, e in results if e and e != 'NO_COST']
    nocost = [c for c, a, e in results if e == 'NO_COST']
    r = tot_row + 1
    if nocost:
        r = tot_row + 3
        ps.cell(row=r, column=1, value=f'Customers with no cost in the period ({len(nocost)}):').font = SUB
        for c in nocost:
            r += 1
            ps.cell(row=r, column=1, value=f"{c['name']} ({c['code']})").font = BLACK
    if failed:
        r = r + 3
        ps.cell(row=r, column=1, value='Customers that could not be read (no access or no data):').font = SUB
        for c, e in failed:
            r += 1
            ps.cell(row=r, column=1, value=f"{c['name']} ({c['code']})").font = BLACK
            ps.cell(row=r, column=4, value=str(e)[:200]).font = NOTE

    # portfolio trend + charts
    tr = wb.create_sheet('Portfolio Trend', 1)
    tr['A1'] = 'Portfolio trend - all active customers'
    tr['A1'].font = TITLE
    tr['A2'] = 'Links to Portfolio Summary totals (recurring cost, excl. Marketplace).'
    tr['A2'].font = NOTE
    header(tr, 4, ['Month', 'Recurring cost (all customers)', 'Change vs previous month ($)', 'Change vs previous month (%)', 'Direction'])
    for j in range(n):
        r = 5 + j
        put(tr, r, 1, MLABEL[j])
        put(tr, r, 2, f"='Portfolio Summary'!{L(4 + j)}{tot_row}", GREEN, MONEY)
        if j:
            put(tr, r, 3, f'=B{r}-B{r - 1}', BLACK, MONEY)
            put(tr, r, 4, f'=IF(B{r - 1}=0,0,C{r}/B{r - 1})', BLACK, PCT)
            put(tr, r, 5, f'=IF(C{r}>=0,"▲ Up","▼ Down")')
        else:
            for c in (3, 4, 5):
                put(tr, r, c, '')
    for c, w in (('A', 16), ('B', 22), ('C', 22), ('D', 20), ('E', 14)):
        tr.column_dimensions[c].width = w
    lc = LineChart()
    lc.title, lc.height, lc.width, lc.roundedCorners = 'All customers - monthly recurring cost', 8, 18, False
    lc.add_data(Reference(tr, min_col=2, min_row=4, max_row=4 + n), titles_from_data=True)
    lc.set_categories(Reference(tr, min_col=1, min_row=5, max_row=4 + n))
    lc.legend.position, lc.legend.overlay, lc.title.overlay = 'b', False, False
    lc.x_axis.delete = lc.y_axis.delete = False
    tr.add_chart(lc, 'G4')
    top = min(10, len(cust_rows))
    if top:
        bc = BarChart()
        bc.type, bc.title, bc.height, bc.width, bc.roundedCorners = 'bar', f'Top {top} customers - {MLABEL[-1]}', 9, 18, False
        bc.add_data(Reference(ps, min_col=3 + n, min_row=5, max_row=5 + top), titles_from_data=True)
        bc.set_categories(Reference(ps, min_col=1, min_row=6, max_row=5 + top))
        bc.legend.overlay, bc.title.overlay = False, False
        bc.x_axis.delete = bc.y_axis.delete = False
        tr.add_chart(bc, 'G22')
        gc = BarChart()
        gc.title, gc.height, gc.width, gc.roundedCorners = f'Top {top} customers - change {MLABEL[0][:3]} to {MLABEL[-1][:3]}', 9, 18, False
        gc.add_data(Reference(ps, min_col=5 + n, min_row=5, max_row=5 + top), titles_from_data=True)
        gc.set_categories(Reference(ps, min_col=1, min_row=6, max_row=5 + top))
        gc.legend.overlay, gc.title.overlay = False, False
        gc.x_axis.delete = gc.y_axis.delete = False
        tr.add_chart(gc, 'G41')

    # flat data for dashboards
    bc = wb.create_sheet('Billing Check', 2)
    bc['A1'] = 'Cross-check: Cost analysis vs Billing > Usage + charges'
    bc['A1'].font = TITLE
    bc['A2'] = ('Cost analysis = Actual cost excl. Marketplace (blue, from Cost Management). Usage + charges = Azure charges from the '
                'billing Usage + charges view (blue). Difference within 2% = Match. Small gaps are normal for credits, rounding and timing.')
    bc['A2'].font = NOTE
    header(bc, 4, ['Customer', 'Billing profile', 'Month', 'Cost analysis', 'Usage + charges', 'Difference', 'Difference (%)', 'Status'])
    bc.column_dimensions['A'].width = 38
    for col in 'BCDEFGH':
        bc.column_dimensions[col].width = 16
    r = 5
    for cust, an, err in results:
        if err or not an.get('billing') or not any(an['billing']['azure'].values()):
            continue
        r_first = r
        for j, m in enumerate(months):
            put(bc, r, 1, cust['name'])
            put(bc, r, 2, str(cust['code']))
            put(bc, r, 3, MLABEL[j])
            put(bc, r, 4, round(an['total'][m] - an['marketplace'][m], 2), BLUE, MONEY)
            put(bc, r, 5, round(an['billing']['azure'][m], 2), BLUE, MONEY)
            put(bc, r, 6, f'=D{r}-E{r}', BLACK, MONEY)
            put(bc, r, 7, f'=IF(E{r}=0,0,F{r}/E{r})', BLACK, PCT)
            put(bc, r, 8, f'=IF(ABS(G{r})<=0.02,"Match","Check")')
            r += 1
        put(bc, r, 1, f"{cust['name']} - period total", BLACK, bold=True).fill = TFILL
        for col in (4, 5, 6):
            put(bc, r, col, f'=SUM({L(col)}{r_first}:{L(col)}{r - 1})', BLACK, MONEY, bold=True).fill = TFILL
        put(bc, r, 7, f'=IF(E{r}=0,0,F{r}/E{r})', BLACK, PCT, bold=True).fill = TFILL
        put(bc, r, 8, f'=IF(ABS(G{r})<=0.02,"Match","Check")', BLACK, bold=True).fill = TFILL
        r += 2
    if r == 5:
        bc['A5'] = 'Usage + charges could not be read for any customer in this run (Cost analysis figures are unaffected).'
        bc['A5'].font = NOTE
    bc.freeze_panes = 'A5'

    ds = wb.create_sheet('Data')
    header(ds, 1, ['Customer', 'Billing profile', 'Month', 'Subscription', 'Service', 'Cost', 'Currency'])
    r = 2
    for cust, an, *_ in cust_rows:
        for row_ in an['_rows']:
            for v, c in zip([cust['name'], str(cust['code']), row_['month'], row_['SubscriptionName'], row_['ServiceName'],
                             round(row_['cost'], 4), row_['currency']], range(1, 8)):
                ds.cell(row=r, column=c, value=v).font = BLACK
            ds.cell(row=r, column=6).number_format = MONEY
            r += 1
    for c, w in zip('ABCDEFG', (36, 16, 10, 40, 34, 14, 9)):
        ds.column_dimensions[c].width = w
    ds.auto_filter.ref = f'A1:G{max(r - 1, 1)}'
    ds.freeze_panes = 'A2'
    wb.save(path)


# ----------------------------------------------------------------------------- main
def run(argv=None, fake=None):
    ap = argparse.ArgumentParser(description='Azure cost growth by customer (billing profile), Cost Management, read-only.')
    ap.add_argument('--start', default='2026-04')
    ap.add_argument('--end', default='2026-10')
    ap.add_argument('--account', default=DEFAULT_ACCOUNT, help='billing account ID as shown in the portal (default 8372436B = Seven Seas technology)')
    ap.add_argument('--profiles', default='', help='comma-separated billing profile IDs or names; default = all active')
    ap.add_argument('--tenant', default=None)
    ap.add_argument('--currency', choices=('usd', 'billing'), default='usd')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports'))
    a = ap.parse_args(argv)
    months = months_between(a.start, a.end)
    if len(months) < 2:
        raise SystemExit('Pick at least two months.')
    az = Azure(a.tenant, fake)
    print('Signing in to Azure (a browser window may open; use your Noventiq work account)...', flush=True)
    return report(az, a.account, a.profiles, months, a.out, a.currency == 'usd', a.refresh, bool(fake), pick=not fake)


def report(az, account, profiles, months, out, usd=True, refresh=False, fake=False, pick=False):
    """Pull every active billing profile and write the workbook + JSON. Used by the API tool and the portal tool."""
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    cur_mode = 'usd' if usd else 'billing'
    who, tid = signed_in_as(az)
    print(f'Signed in as {who} (directory {tid})', flush=True)
    acct, profs = find_account(az, account)
    acct_name = (acct.get('properties') or {}).get('displayName') or acct.get('name')
    print(f'Billing account {acct_name}: {len(profs)} billing profile(s) in total', flush=True)
    custs = list_customers(az, acct, profs, profiles)
    print(f'\n{acct_name}: {len(custs)} active customer(s). Months {months[0]} to {months[-1]}.\n', flush=True)
    results = []
    if custs and pick:
        pick_directory(az, custs[0], months, usd)
    def read_one(c):
        cache_dir = os.path.join(out, 'cache', f"{months[0]}_{months[-1]}_{cur_mode}", re.sub(r'[^A-Za-z0-9_.-]', '_', str(c['code'])))
        data, first = {}, []
        if refresh and os.path.isdir(cache_dir):
            for fn in os.listdir(cache_dir):
                try:
                    os.remove(os.path.join(cache_dir, fn))
                except OSError:
                    pass
        scope = pick_scope(az, c, months, usd, cache_dir, first)
        for qn, grp in QUERIES.items():
            cf = os.path.join(cache_dir, qn + '.json')
            if qn == 'sub_service' and first:
                data[qn] = first[0]
                os.makedirs(cache_dir, exist_ok=True)
                with open(cf, 'w', encoding='utf-8') as f:
                    json.dump(data[qn], f)
            else:
                data[qn] = query(az, scope, grp, months, usd, cf)
            if qn == 'sub_service' and not any(abs(r['cost']) > 0.005 for r in data[qn] if r['month'] in months):
                return None                           # no cost in the period: nothing more to read, next client
        an = analyse(c, data, months)
        an['_rows'] = [r for r in data['sub_service'] if r['month'] in months]
        try:
            an['billing'] = billing_charges(az, c, months, None, os.path.join(cache_dir, 'usage_charges.json'))
        except Exception:
            an['billing'] = None
        b = an['billing']
        if b and any(b['azure'].values()):
            ca = sum(an['total'][m] - an['marketplace'][m] for m in months)
            uc = sum(b['azure'].values())
            diff = (ca - uc) / uc if uc else 0
            an['billing_status'] = 'Match' if abs(diff) <= 0.02 else f'Differs {diff:+.1%}'
            print(f"    check vs Usage + charges: {an['billing_status']} (cost analysis {ca:,.0f} / usage + charges {uc:,.0f})", flush=True)
        else:
            an['billing_status'] = 'Usage + charges not readable'
        return an

    pending = list(custs)
    for rnd in range(5):                              # round 1 = everyone; rounds 2-3 = customers Azure throttled
        later = []
        if rnd:
            print(f'\nComing back to {len(pending)} customer(s) that could not be read the first time (round {rnd + 1}) ...', flush=True)
            if not fake:
                time.sleep(60)
        for i, c in enumerate(pending, 1):
            print(f"[{i}/{len(pending)}] {c['name']} ({c['code']})", flush=True)
            try:
                an = read_one(c)
                if an is None:
                    print('    no cost in this period - next client', flush=True)
                    results.append((c, None, 'NO_COST'))
                    continue
                tot = an['total']
                print('    ' + '  '.join(f"{m[5:]}:{tot[m]:,.0f}" for m in months), flush=True)
                results.append((c, an, None))
            except Busy:
                if rnd < 4:
                    print('    Azure is throttling this one - moving to the next client, will come back to it', flush=True)
                    later.append(c)
                else:
                    results.append((c, None, RuntimeError('Azure throttled (429) every time; run again later - finished customers are cached')))
            except Exception as e:                    # one customer failing must not stop the others
                if rnd == 0:
                    print(f'    could not read: {str(e)[:200]} - next client, will try it once more at the end', flush=True)
                    later.append(c)
                else:
                    print(f'    could not read: {str(e)[:200]} - next client', flush=True)
                    results.append((c, None, e))
        pending = later
        if not pending:
            break
    order = {str(c['code']): k for k, c in enumerate(custs)}
    results.sort(key=lambda r: order.get(str(r[0]['code']), 0))
    ok = [r for r in results if not r[2]]
    nocost = [r for r in results if r[2] == 'NO_COST']
    if not ok and not nocost:
        raise SystemExit('No customer could be read. Check that your account has Cost Management Reader on the billing account.')
    stamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    label = f"{months[0]}_to_{months[-1]}"
    xlsx = os.path.join(out, f"Cost_Growth_Report_{re.sub(r'[^A-Za-z0-9]+', '_', acct_name).strip('_')}_{label}.xlsx")
    js = os.path.join(out, 'cost_growth_data.json')
    with open(js, 'w', encoding='utf-8') as f:
        json.dump({'account': acct_name, 'months': months, 'pulled': stamp, 'customers': [
            {'name': c['name'], 'code': c['code'], 'currency': an['currency'], 'total': an['total'],
             'marketplace': an['marketplace'], 'reservations': an['ri'], 'savings_plan': an['sp'],
             'subscriptions': an['subs'], 'services': an['svcs'], 'regions': an.get('region', {}),
             'pricing': an.get('pricing', {}), 'charge_types': an.get('charge', {}), 'marketplace_offers': an.get('pub_names', {}),
             'usage_charges': an.get('billing'), 'billing_check': an.get('billing_status'),
             'spikes': [{'from': s['from'], 'to': s['to'], 'change': s['change'],
                         'drivers': [{'subscription': d[0], 'service': d[1], 'change': d[2]} for d in s['drivers']]} for s in an['spikes']]}
            for c, an, e in results if not e],
            'no_cost': [{'name': c['name'], 'code': c['code']} for c, an, e in results if e == 'NO_COST'],
            'failed': [{'name': c['name'], 'code': c['code'], 'error': str(e)[:300]} for c, an, e in results if e and e != 'NO_COST']}, f, indent=1)
    try:
        build_workbook(results, months, xlsx, acct_name, stamp)
    except PermissionError:
        xlsx = xlsx[:-5] + dt.datetime.now().strftime('_%H%M%S') + '.xlsx'   # old report still open in Excel
        build_workbook(results, months, xlsx, acct_name, stamp)
    print(f'\nDone. {len(ok)} customers with cost, {len(nocost)} with no cost, {len(results) - len(ok) - len(nocost)} not readable.\nExcel: {xlsx}\nData:  {js}')
    return xlsx


if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        sys.exit('Stopped. Run again to continue: finished customers are cached.')
