"""Read-only multi-tenant / multi-subscription Azure discovery.

Ported from the "Azure Multi-Customer BOQ Dashboard v0.2" (Streamlit) into the Cloud Command
Center, with these changes:
  * standard library + openpyxl only (no Streamlit / pandas / azure-mgmt-* packages)
  * tenant list -> subscription list -> Resource Graph inventory -> Advisor -> Cost Management
  * FIX: v0.2 built cost-query timestamps as "...+00:00Z" (invalid); they are now "YYYY-MM-DDTHH:MM:SSZ"
  * throttling (429/5xx) is retried a few times using Retry-After
  * discovered VMs / managed disks / Azure SQL DBs / public IPs become estimator line items,
    so an existing estate can be priced with the official Azure Retail Prices API
  * Excel export hardened against formula injection and cell-size limits

Only GET requests and the two read-only POST queries (Resource Graph, Cost Management query) are
issued. Nothing is created, changed, deleted or purchased.
"""
import datetime as dt
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ARM = 'https://management.azure.com'
GUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
DOMAIN = re.compile(r'[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+')
LOOKBACK_DAYS = (30, 60, 90)
MAX_ROWS = 100_000
MAX_SUBS = 200

INVENTORY_QUERY = ('Resources | project subscriptionId, resourceGroup, name, type, location, sku, kind, '
                   'properties, tags, id')
ADVISOR_QUERY = ("AdvisorResources | where type =~ 'microsoft.advisor/recommendations' "
                 "| project subscriptionId, id, properties")


class DiscoveryError(ValueError):
    """Message is safe to show to the user."""


# ----------------------------------------------------------------------------- validation
def check_tenant(tenant):
    t = str(tenant or '').strip()
    if not (GUID.fullmatch(t) or DOMAIN.fullmatch(t)):
        raise DiscoveryError('Choose a directory by tenant ID (GUID) or domain name.')
    return t


def check_subscriptions(subs):
    if not isinstance(subs, list) or not 1 <= len(subs) <= MAX_SUBS:
        raise DiscoveryError(f'Select 1 to {MAX_SUBS} subscriptions.')
    out = []
    for s in subs:
        if not isinstance(s, str) or not GUID.fullmatch(s.strip()):
            raise DiscoveryError('Invalid subscription ID.')
        if s.strip().lower() not in out:
            out.append(s.strip().lower())
    return out


def check_days(days):
    if days not in LOOKBACK_DAYS:
        raise DiscoveryError('Cost lookback must be 30, 60 or 90 days.')
    return days


# ----------------------------------------------------------------------------- HTTP
def make_caller(token_fn, sleep=time.sleep, retries=4, timeout=90, opener=urlopen):
    """Return call(method, url, body=None) -> parsed JSON. token_fn(tenant) -> bearer token string.

    Only https://management.azure.com is ever contacted (also for nextLink pagination)."""
    def call(method, url, body=None, tenant=None):
        u = urlparse(url)
        if u.scheme != 'https' or u.hostname != 'management.azure.com' or u.port not in (None, 443) or u.username:
            raise DiscoveryError('Refusing a non-Azure-Resource-Manager URL.')
        if method not in ('GET', 'POST'):
            raise DiscoveryError('Only read operations are allowed.')
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries + 1):
            req = Request(url, data=data, method=method,
                          headers={'Authorization': 'Bearer ' + token_fn(tenant), 'Content-Type': 'application/json'})
            try:
                with opener(req, timeout=timeout) as r:
                    return json.load(r)
            except HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    ra = e.headers.get('Retry-After') or ''
                    wait = float(ra) if ra.replace('.', '', 1).isdigit() else min(30, 3 * 2 ** attempt)
                    sleep(max(1, min(60, wait)))
                    continue
                detail = ''
                try:
                    detail = (json.load(e).get('error') or {}).get('message', '')
                except Exception:
                    pass
                hint = {401: 'Sign-in expired or wrong tenant.', 403: 'Your account lacks permission (Reader / Cost Management Reader).',
                        404: 'Not found (check the subscription or provider registration).',
                        429: 'Azure throttled the request; retry in a few minutes.'}.get(e.code, '')
                raise DiscoveryError(f'Azure returned HTTP {e.code}. {hint} {detail[:200]}'.strip())
            except (URLError, OSError) as e:
                if attempt < retries:
                    sleep(min(20, 2 * 2 ** attempt))
                    continue
                raise DiscoveryError('Could not reach management.azure.com (network, proxy or VPN).')
    return call


# ----------------------------------------------------------------------------- directories & subscriptions
def _paged(call, url, tenant=None, limit=50):
    out = []
    for _ in range(limit):
        r = call('GET', url, None, tenant)
        out.extend(r.get('value', []))
        url = r.get('nextLink')
        if not url:
            return out
    raise DiscoveryError('Too many result pages; narrow the scope.')


def list_tenants(call):
    rows = _paged(call, ARM + '/tenants?api-version=2022-12-01')
    return sorted(({'id': r.get('tenantId', ''), 'name': r.get('displayName') or r.get('defaultDomain') or r.get('tenantId', ''),
                    'domain': r.get('defaultDomain', '')} for r in rows if GUID.fullmatch(r.get('tenantId', ''))),
                  key=lambda t: t['name'].lower())


def list_subscriptions(call, tenant):
    tenant = check_tenant(tenant)
    rows = _paged(call, ARM + '/subscriptions?api-version=2022-12-01', tenant)
    out = [{'id': r['subscriptionId'].lower(), 'name': r.get('displayName', ''), 'state': r.get('state', ''),
            'tenant': r.get('tenantId', tenant)} for r in rows if GUID.fullmatch(r.get('subscriptionId', ''))]
    return sorted(out, key=lambda s: s['name'].lower())


# ----------------------------------------------------------------------------- Resource Graph / Advisor / Cost
def rg_query(call, subs, query, tenant=None):
    rows, token = [], None
    while True:
        opts = {'resultFormat': 'objectArray', '$top': 1000}
        if token:
            opts['$skipToken'] = token
        r = call('POST', ARM + '/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01',
                 {'subscriptions': subs, 'query': query, 'options': opts}, tenant)
        rows.extend(r.get('data') or [])
        token = r.get('$skipToken')
        if not token:
            return rows
        if len(rows) > MAX_ROWS:
            raise DiscoveryError('Inventory is too large; select fewer subscriptions.')


def parse_advisor(rows):
    out = []
    for r in rows:
        p = r.get('properties') or {}
        ext = p.get('extendedProperties') or {}
        short = p.get('shortDescription') or {}
        out.append({'subscriptionId': r.get('subscriptionId'), 'category': p.get('category', ''), 'impact': p.get('impact', ''),
                    'description': short.get('problem'), 'solution': short.get('solution'),
                    'resourceId': (p.get('resourceMetadata') or {}).get('resourceId'),
                    'estimatedSavingsRaw': ext.get('annualSavingsAmount') or ext.get('savingsAmount'),
                    'savingsCurrency': ext.get('savingsCurrency'), 'recommendationId': r.get('id')})
    return out


def iso_z(t):
    """UTC datetime -> 2026-11-19T10:00:00Z (v0.2 produced '+00:00Z', which Azure rejects)."""
    return t.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def cost_body(now, days, start=None):
    start = start or (now - dt.timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {'type': 'ActualCost', 'timeframe': 'Custom', 'timePeriod': {'from': iso_z(start), 'to': iso_z(now)},
            'dataset': {'granularity': 'Monthly', 'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
                        'grouping': [{'type': 'Dimension', 'name': 'ServiceName'}, {'type': 'Dimension', 'name': 'Currency'}]}}


def cost_query(call, sub, days, tenant=None, now=None, month=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    url = f'{ARM}/subscriptions/{sub}/providers/Microsoft.CostManagement/query?api-version=2023-03-01'
    if month:
        a, b = month_window(month)
        body = cost_body(b, days, start=a)
    else:
        body = cost_body(now, days)
    try:
        props = call('POST', url, body, tenant).get('properties') or {}
    except DiscoveryError as e:
        if 'grouping' not in str(e).lower():
            raise
        # some agreement types (e.g. CSP / MCA customer scope) reject Currency as a grouping: retry without it
        body['dataset']['grouping'] = [g for g in body['dataset']['grouping'] if g['name'] != 'Currency']
        props = call('POST', url, body, tenant).get('properties') or {}
    cols = [c.get('name') for c in props.get('columns', [])]
    return [dict(zip(cols, row), subscriptionId=sub) for row in props.get('rows', [])]


def run_assessment(call, tenant, subs, days, progress=lambda m: None, now=None, month=None):
    """Collect inventory, Advisor recommendations and actual cost. Each part fails independently.
    month='YYYY-MM' also reads that month's bill per resource and meter (see items_for_month)."""
    tenant, subs, days = check_tenant(tenant), check_subscriptions(subs), check_days(days)
    if month:
        month = check_month(month, now)
    out = {'tenant': tenant, 'subscriptions': subs, 'days': days, 'month': month, 'inventory': [], 'advisor': [], 'cost': [], 'failures': [],
           'collected_utc': (now or dt.datetime.now(dt.timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')}
    progress('Reading resource inventory (Azure Resource Graph)…')
    try:
        out['inventory'] = rg_query(call, subs, INVENTORY_QUERY, tenant)
    except DiscoveryError as e:
        out['failures'].append('Inventory: ' + str(e))
    progress('Reading Azure Advisor recommendations…')
    try:
        out['advisor'] = parse_advisor(rg_query(call, subs, ADVISOR_QUERY, tenant))
    except DiscoveryError as e:
        out['failures'].append('Advisor: ' + str(e))
    try:
        out['storage_gb'], w = storage_usage(call, out['inventory'], tenant, now, progress)
        out['failures'] += w
    except DiscoveryError as e:
        out['storage_gb'] = {}
        out['failures'].append('Storage usage: ' + str(e))
    for i, s in enumerate(subs, 1):
        progress(f'Reading actual cost, subscription {i} of {len(subs)}…')
        try:
            out['cost'] += cost_query(call, s, days, tenant, now, month=month)
        except DiscoveryError as e:
            out['failures'].append(f'Cost {s}: {e}')
    win = bill_window(month, now)
    out['bill_window'] = {'from': iso_z(win[0]), 'to': iso_z(win[1]), 'label': win[2], 'hours': win[3], 'month': month}
    out['month_cost'] = []
    for i, s in enumerate(subs, 1):
        progress(f'Reading the actual bill per resource ({win[2]}), subscription {i} of {len(subs)}…')
        try:
            out['month_cost'] += month_resource_cost(call, s, month, tenant, window=win[:2])
        except DiscoveryError as e:
            out['failures'].append(f'Bill per resource {s}: {e}')
    out['summary'] = summarize(out['inventory'])
    return out


def storage_usage(call, inventory, tenant=None, now=None, progress=lambda m: None, limit=300):
    """Best effort: used capacity (GB) per storage account from Azure Monitor (UsedCapacity, last 3 hours).
    Returns ({resource id lower: GB}, [warnings])."""
    now = now or dt.datetime.now(dt.timezone.utc)
    span = iso_z(now - dt.timedelta(hours=3)) + '/' + iso_z(now)
    accts = [r for r in inventory if str(r.get('type', '')).lower() == 'microsoft.storage/storageaccounts' and r.get('id')][:limit]
    out, bad = {}, 0
    for n, r in enumerate(accts, 1):
        if n % 10 == 1:
            progress(f'Reading storage usage {n}-{min(n + 9, len(accts))} of {len(accts)}…')
        rid = str(r['id'])
        if not rid.startswith('/subscriptions/'):
            continue
        url = (f"{ARM}{rid}/providers/microsoft.insights/metrics?api-version=2018-01-01&metricnames=UsedCapacity"
               f"&aggregation=Average&interval=PT1H&timespan={span}")
        try:
            series = ((call('GET', url, None, tenant).get('value') or [{}])[0].get('timeseries') or [{}])[0].get('data') or []
            vals = [d['average'] for d in series if d.get('average') is not None]
            if vals:
                out[rid.lower()] = round(vals[-1] / 1024 ** 3, 2)
        except DiscoveryError:
            bad += 1
    warn = [f'Storage usage: could not read {bad} of {len(accts)} storage accounts (Monitoring Reader / Reader needed).'] if bad else []
    return out, warn


MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']


def month_label(m):
    y, mo = map(int, m.split('-'))
    return f'{MONTHS[mo - 1]} {y}'


def month_window(m):
    """'2026-08' -> (2026-08-01 00:00:00Z, 2026-08-31 23:59:59Z)."""
    y, mo = map(int, m.split('-'))
    a = dt.datetime(y, mo, 1, tzinfo=dt.timezone.utc)
    b = dt.datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=dt.timezone.utc) - dt.timedelta(seconds=1)
    return a, b


def month_hours(m, now=None):
    a, b = month_window(m)
    now = now or dt.datetime.now(dt.timezone.utc)
    return round(((min(b, now) - a).total_seconds() + 1) / 3600)


def recent_months(now=None, n=12):
    """The current month and the 11 before it, newest first: ['2026-11', '2026-10', ...]."""
    now = now or dt.datetime.now(dt.timezone.utc)
    y, m, out = now.year, now.month, []
    for _ in range(n):
        out.append(f'{y}-{m:02d}')
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def bill_window(month=None, now=None):
    """(from, to, label, hours): the chosen month, or the last 30 days for 'Current'."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if month:
        a, b = month_window(month)
        return a, min(b, now), month_label(month), month_hours(month, now)
    a = (now - dt.timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    return a, now, f"last 30 days ({a.strftime('%d %b')} - {now.strftime('%d %b %Y')})", round((now - a).total_seconds() / 3600)


def check_month(m, now=None):
    if not isinstance(m, str) or m not in recent_months(now):
        raise DiscoveryError('Choose a month from the last 12 months.')
    return m


def _cm_rows(call, url, body, tenant, pages=20):
    rows, cols = [], []
    for _ in range(pages):
        props = call('POST', url, body, tenant).get('properties') or {}
        cols = [c.get('name') for c in props.get('columns', [])] or cols
        rows += [dict(zip(cols, r)) for r in props.get('rows', [])]
        nxt = props.get('nextLink')
        if not nxt:
            return rows
        url = nxt
    raise DiscoveryError('The monthly bill has too many rows; select fewer subscriptions.')


def month_resource_cost(call, sub, month, tenant=None, window=None):
    """Actual bill per resource and meter for a month (or a (from, to) window). Cost Management allows two group-bys per
    query, so two queries are joined: [{resourceId, meter, meterSubCategory, cost, quantity, currency, subscriptionId}]."""
    a, b = window or month_window(month)
    url = f'{ARM}/subscriptions/{sub}/providers/Microsoft.CostManagement/query?api-version=2023-03-01'

    def body(second):
        return {'type': 'ActualCost', 'timeframe': 'Custom', 'timePeriod': {'from': iso_z(a), 'to': iso_z(b)},
                'dataset': {'granularity': 'None', 'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'},
                                                                   'totalQuantity': {'name': 'UsageQuantity', 'function': 'Sum'}},
                            'grouping': [{'type': 'Dimension', 'name': 'ResourceId'}, {'type': 'Dimension', 'name': second}]}}
    meters = _cm_rows(call, url, body('Meter'), tenant)
    category = {}                                            # meter -> Azure service (MeterCategory), the costliest if a name repeats
    try:
        cat_body = body('Meter')
        cat_body['dataset']['grouping'] = [{'type': 'Dimension', 'name': 'MeterCategory'}, {'type': 'Dimension', 'name': 'Meter'}]
        best = {}
        for r in _cm_rows(call, url, cat_body, tenant):
            m, c = str(r.get('Meter') or ''), float(r.get('Cost') or r.get('PreTaxCost') or 0)
            if m and r.get('MeterCategory') and c >= best.get(m, -1):
                best[m] = c; category[m] = str(r['MeterCategory'])
    except Exception:
        category = {}
    subcat = {}
    for r in _cm_rows(call, url, body('MeterSubCategory'), tenant):
        rid = str(r.get('ResourceId') or '').lower()
        if rid and r.get('MeterSubCategory'):
            subcat.setdefault(rid, set()).add(str(r['MeterSubCategory']))
    out = []
    for r in meters:
        rid = str(r.get('ResourceId') or '').lower()
        out.append({'resourceId': rid, 'meter': str(r.get('Meter') or ''), 'meterSubCategory': ' | '.join(sorted(subcat.get(rid, ()))),
                    'meterCategory': category.get(str(r.get('Meter') or ''), ''),
                    'cost': float(r.get('Cost') or r.get('PreTaxCost') or 0), 'quantity': float(r.get('UsageQuantity') or 0),
                    'currency': r.get('Currency') or '', 'subscriptionId': sub})
    return out


try:
    from service_catalog import SERVICE_SLUGS, ZERO_OVERRIDES, RTYPE_SLUGS
except ImportError:                                          # loaded by file path (tests, app): import next to this file
    import importlib.util as _ilu, os as _os
    _sp = _ilu.spec_from_file_location('service_catalog', _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'service_catalog.py'))
    try:
        _sc = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_sc); SERVICE_SLUGS, ZERO_OVERRIDES, RTYPE_SLUGS = _sc.SERVICE_SLUGS, _sc.ZERO_OVERRIDES, _sc.RTYPE_SLUGS
    except (OSError, AttributeError):
        SERVICE_SLUGS, ZERO_OVERRIDES, RTYPE_SLUGS = {}, {}, {}


_VM_METER = re.compile(r'^([A-Z]+\d+[a-z]*(?: v\d)?)(?:\s+(Low Priority|Spot))?$', re.I)
_DISK_METER = re.compile(r'^([PES])(\d+)\s+(?:LRS|ZRS)?\s*Disks?$', re.I)


def _meter_vm(meter):
    m = _VM_METER.match(meter.strip())
    if not m or m.group(2):
        return None
    return 'Standard_' + m.group(1).replace(' v', '_v').replace(' ', '_')


def _rtype(rid):
    return '/'.join(rid.split('/providers/')[-1].split('/')[:2]) if '/providers/' in rid else ''


def bill_items(month_cost, hours, scale=1.0):
    """Calculator items read straight from bill meters (Azure Arc / hybrid charges and usage that inventory cannot see):
    Defender for Servers nodes, Log Analytics ingestion (GB/day), Arc-enabled SQL Server pay-as-you-go licences."""
    out, nodes, gb, sql = [], {}, 0.0, {}
    days = max(1.0, hours / 24)
    for r in month_cost:
        m, rid, q = r['meter'], r['resourceId'], r['quantity']
        t = _rtype(rid)
        if re.search(r'(?i)\bnode\b', m) and (t.startswith('microsoft.hybridcompute/') or t.startswith('microsoft.compute/') or 'defender' in r['meterSubCategory'].lower()
                                                or 'security' in r['meterSubCategory'].lower()):
            plan = 'p1' if re.search(r'(?i)\bP1\b|plan 1', m + ' ' + r['meterSubCategory']) else 'p2'
            nodes.setdefault(plan, set()).add(rid.split('/extensions/')[0])
        elif re.search(r'(?i)data ingestion|data included per node|analytics logs', m) and q > 0:
            gb += q
        elif t == 'microsoft.azurearcdata/sqlserverinstances' and re.search(r'(?i)(standard|enterprise)', m) and q > 0:
            ed = 'enterprise' if 'enterprise' in m.lower() else 'standard'
            sql[ed] = sql.get(ed, 0.0) + q
    for plan, ids in nodes.items():
        out.append({'type': 'defender', 'plan': plan, 'count': len(ids), 'name': f'Defender for Servers {plan.upper()}', 'resource_ids': sorted(ids)})
    if gb:
        out.append({'type': 'law', 'gb_per_day': round(gb / days, 2), 'name': 'Log Analytics ingestion'})
    for ed, core_hours in sql.items():
        out.append({'type': 'arc_sql', 'edition': ed, 'vcores': max(1, round(core_hours / hours)), 'name': f'Arc-enabled SQL Server {ed.title()} (pay-as-you-go)'})
    return out


def _red(m):
    for r in ('ra-gzrs', 'gzrs', 'ra-grs', 'grs', 'zrs', 'lrs'):
        if re.search(r'(?<![a-z])' + r + r'(?![a-z])', m):
            return r
    return 'lrs'


# Billed services the Azure Pricing Calculator has no module for: shown separately, never "add manually".
NO_CALC_EQUIVALENT = {'microsoft.monitor/observabilityagents'}


def _service_row(t, m, s, q, rid):
    """Bill row -> (kind, extra key parts, {field: quantity}) for services priced per resource/tier from the bill."""
    name = rid.rsplit('/', 1)[-1]
    if t == 'microsoft.network/loadbalancers':
        if 'data processed' in m:
            return 'lb', (name,), {'gb': q}
        if 'overage' in m:
            return 'lb', (name,), {'overage': q}
        if 'rule' in m or 'included' in m:
            return 'lb', (name,), {'hours': q}
        if 'gateway' in m:
            return 'lb', (name,), {'gw': q}
    if t == 'microsoft.network/natgateways':
        return 'nat', (name,), ({'gb': q} if 'data' in m else {'hours': q})
    if t == 'microsoft.network/bastionhosts':
        tier = 'premium' if 'premium' in m + s else 'standard' if 'standard' in m + s else 'developer' if 'developer' in m + s else 'basic'
        if 'scale unit' in m or 'additional' in m:
            return 'bastion', (name, tier), {'units': q}
        return 'bastion', (name, tier), {'hours': q}
    if t == 'microsoft.network/privateendpoints' or 'private endpoint' in m or s.startswith('private link'):
        if 'ingress' in m:
            return 'privatelink', (), {'inb': q}
        if 'egress' in m or 'data processed' in m:
            return 'privatelink', (), {'outb': q}
        return 'privatelink', (), {'hours': q}
    if t == 'microsoft.network/applicationgateways':
        waf = 'waf' in m + s
        if re.search(r'capacity unit', m):
            return 'appgw', (name, 'wafv2' if waf else 'standard'), {'cu': q}
        if 'fixed' in m:
            return 'appgw', (name, 'wafv2' if waf else 'standard'), {'hours': q}
        if 'data processed' in m:
            return 'appgw', (name, 'wafv2' if waf else 'standard'), {'gb': q}
        size = 'large' if 'large' in m else 'medium' if 'medium' in m else 'small'
        return 'appgw1', (name, 'wafv1' if waf else 'basicv1', size), {'hours': q}
    if t == 'microsoft.containerregistry/registries':
        tier = 'premium' if 'premium' in m else 'standard' if 'standard' in m else 'basic'
        if 'registry unit' in m:
            return 'acr', (tier,), {'days': q}
        if 'data stored' in m or 'storage' in m:
            return 'acr', (tier,), {'gb': q}
        if 'vcpu' in m or 'task' in m or 'build' in m:
            return 'acr', (tier,), {'cpus': q}
    if t in ('microsoft.cache/redis', 'microsoft.cache/redisenterprise') or s.startswith('azure redis cache') or 'redis cache' in s:
        mm = re.match(r'\s*([cpe]\d+)\b', m)
        if mm:
            tier = 'premium' if mm.group(1)[0] == 'p' else 'standard' if 'standard' in s else 'basic' if 'basic' in s else 'standard'
            return 'redis', (tier, mm.group(1)), {'hours': q}
    if t == 'microsoft.eventhub/namespaces':
        tier = 'premium' if 'premium' in m + s else 'basic' if 'basic' in m + s else 'standard'
        if 'throughput unit' in m or 'processing unit' in m:
            return 'eventhubs', (name, tier), {'hours': q}
        if 'ingress' in m or 'event' in m:
            return 'eventhubs', (name, tier), {'events': q}
        if 'capture' in m:
            return 'eventhubs', (name, tier), {'capture': q}
    if t == 'microsoft.servicebus/namespaces':
        tier = 'premium' if 'premium' in m + s else 'basic' if 'basic' in m + s else 'standard'
        if 'messaging unit' in m:
            return 'servicebus', (name, 'premium'), {'mu': q}
        if 'base unit' in m:
            return 'servicebus', (name, tier), {'hours': q}
        if 'operation' in m:
            return 'servicebus', (name, tier), {'ops': q}
        if 'connection' in m:
            return 'servicebus', (name, tier), {'conn': q}
    if t == 'microsoft.recoveryservices/vaults' and re.search(r'replicated|site recovery', m + ' ' + s):
        return 'asr', (), {('customer' if 'customer' in m or 'on-premises' in m else 'azure'): q}
    if t == 'microsoft.documentdb/databaseaccounts' or s.startswith('azure cosmos db') or 'cosmos' in s:
        mode = 'autoscale' if 'autoscale' in m + s else 'serverless' if re.search(r'serverless|request unit|1m ru|1 m ru', m) else 'standard'
        if re.search(r'data stored|storage', m):
            return 'cosmos', (name,), {'gb': q}
        if mode == 'serverless':
            return 'cosmos', (name,), {'mru': q, 'serverless': 1}
        if 'ru' in m:
            return 'cosmos', (name,), {('ru_auto' if mode == 'autoscale' else 'ru'): q}
    if t.startswith('microsoft.sql/servers') and ('/databases/' in rid or '/elasticpools/' in rid or 'dtu' in m + s):
        mm = re.match(r'\s*(b|basic|s\d+|p\d+)\s*(dtus?)?\b', m)
        if mm and ('dtu' in m or 'dtu' in s):
            lvl = 'b' if mm.group(1) in ('b', 'basic') else mm.group(1)
            tier = 'basic' if lvl == 'b' else 'premium' if lvl[0] == 'p' else 'standard'
            return 'sqldtu', (tier, lvl), {'days': q}
    if s.startswith('functions') or (t == 'microsoft.web/sites' and re.search(r'execution', m)):
        if 'time' in m:
            return 'functions', (), {'gbs': q}
        if 'execution' in m:
            return 'functions', (), {'execs': q}
    if t == 'microsoft.operationsmanagement/solutions' or 'sentinel' in m + ' ' + s:
        if re.search(r'analysis|ingestion|data', m):
            return 'sentinel', (), {'gb': q}
    if t == 'microsoft.storage/storageaccounts' and not re.search(r'snapshot|data transfer|egress|ingress', m):
        files = 'file' in s or 'files' in m
        tier = 'archive' if 'archive' in m else 'cold' if 'cold' in m else 'cool' if 'cool' in m else 'hot'
        red = _red(m)
        acct = (name, 'files' if files else tier, red)
        if 'data stored' in m or 'capacity' in m or 'metadata' in m:
            return 'storage', acct, {'gb': q}
        if 'write' in m:
            return 'storage', acct, {'write': q}
        if 'read' in m:
            return 'storage', acct, {'read': q}
        if 'list' in m or 'create container' in m:
            return 'storage', acct, {'list': q}
        if 'retrieval' in m:
            return 'storage', acct, {'retrieval': q}
        if 'operation' in m or 'transaction' in m:
            return 'storage', acct, {'other': q}
    return None


def _per_month(v, hours):
    return max(1, round(v / hours)) if v else 0


def _l_lb(key, a, hours):
    rules = 5 + (round(a.get('overage', 0) / hours) if a.get('overage') else 0)
    return {'slug': 'load-balancer', 'label': f"Load Balancer {key[2]}: Standard, {rules} rules, {a.get('gb', 0):,.1f} GB processed (from the bill)",
            'set': {'tier': 'standard', 'rules': rules, 'capacity': round(a.get('gb', 0), 2), 'capacityFactor': 1,
                    'dataProcessedUnits': round(a.get('gb', 0), 2), 'gatewayHours': 0, 'chainHours': 0}, 'check': ['Standard']}


def _l_nat(key, a, hours):
    return {'slug': 'virtual-network-nat', 'label': f"NAT Gateway {key[2]}: {a.get('hours', 0):,.0f} h, {a.get('gb', 0):,.1f} GB (from the bill)",
            'set': {'hours': round(min(a.get('hours', 0), 730), 1), 'dataProcessed': round(a.get('gb', 0), 2)}, 'check': ['NAT']}


def _l_bastion(key, a, hours):
    tier = key[3]
    f = {'tier': tier, f'{tier}Hours': round(min(a.get('hours', 0), 730), 1)}
    if tier in ('standard', 'premium'):
        f[f'{tier}AdditionalScaleUnits'] = round(a.get('units', 0) / hours) if a.get('units') else 0
        f[f'{tier}OutboundDataTransfer'] = 0
    if tier == 'basic':
        f['basicOutboundDataTransfer'] = 0
    return {'slug': 'azure-bastion', 'label': f"Azure Bastion {key[2]}: {tier.title()}, {a.get('hours', 0):,.0f} h (from the bill)", 'set': f, 'check': [tier.title()]}


def _l_privatelink(key, a, hours):
    n = _per_month(a.get('hours', 0), hours)
    return {'slug': 'private-link', 'label': f"Private Link: {n} endpoint(s), {a.get('inb', 0) + a.get('outb', 0):,.1f} GB processed (from the bill)",
            'set': {'privateEndpointUnits': n, 'privateEndpointHours': 730, 'outboundDataUnits': round(a.get('outb', 0), 2),
                    'inboundDataUnits': round(a.get('inb', 0), 2)}, 'check': ['Endpoint']}


def _l_appgw(key, a, hours):
    tier = key[3]
    return {'slug': 'application-gateway', 'label': f"Application Gateway {key[2]}: {'WAF' if tier == 'wafv2' else 'Standard'} v2, {a.get('cu', 0) / hours:,.1f} capacity units avg (from the bill)",
            'set': {'tier': tier, 'hours': round(min(a.get('hours', 0) or hours, 730), 1), 'computeUnits': round(a.get('cu', 0) / hours, 2),
                    'persistentConnections': 0, 'throughput': 0, 'units': round(a.get('gb', 0), 2)}, 'check': ['V2']}


def _l_appgw1(key, a, hours):
    tier, size = key[3], key[4]
    return {'slug': 'application-gateway', 'label': f"Application Gateway {key[2]}: {tier} {size}, {a.get('hours', 0):,.0f} instance-hours (from the bill)",
            'set': {'tier': tier, 'size': size, 'instances': _per_month(a.get('hours', 0), hours), 'hours': 730, 'dataUnits': 0}, 'check': [size.title()]}


def _l_acr(key, a, hours):
    days = max(1.0, hours / 24)
    n = max(1, round(a.get('days', 0) / days)) if a.get('days') else 1
    return {'slug': 'container-registry', 'label': f"Container Registry {key[2].title()}: {n} registry, {a.get('gb', 0):,.0f} GB extra storage (from the bill)",
            'set': {'tier': key[2], 'units': n, 'registry': n, 'days': 30, 'storageUnits': round(a.get('gb', 0), 1), 'buildUnits': round(a.get('cpus', 0)),
                    'bandwidthUnits': 0, 'addGeoReplication': False, 'geoReplicatedRegions': 0, 'interRegionUnits': 0, 'internetEgressUnits': 0}, 'check': [key[2].title()]}


def _l_redis(key, a, hours):
    n = _per_month(a.get('hours', 0), hours)
    return {'slug': 'redis-cache', 'label': f"Azure Cache for Redis {key[2].title()} {key[3].upper()}: {n} instance(s) (from the bill)",
            'set': {'tier': key[2], 'size': key[3], 'count': n, 'hours': 730}, 'check': [key[3].upper()]}


def _l_eventhubs(key, a, hours):
    tier = key[3]
    f = {'tier': tier, 'units': _per_month(a.get('hours', 0), hours) or 1, 'hours': 730, 'events': round(a.get('events', 0), 2), 'capture': bool(a.get('capture'))}
    if tier == 'premium':
        f.update(processingUnits='one', premiumHours=730)
    return {'slug': 'event-hubs', 'label': f"Event Hubs {key[2]}: {tier.title()}, {f['units']} unit(s), {f['events']:g} M events (from the bill)", 'set': f, 'check': [tier.title()]}


def _l_servicebus(key, a, hours):
    tier = key[3]
    f = {'tier': tier, 'messagingOperationsHours': 730, f'{tier}Operations' if tier in ('basic', 'standard') else 'standardOperations': round(a.get('ops', 0), 2),
         'brokeredConnections': round(a.get('conn', 0))}
    if tier == 'premium':
        f['messageUnits'] = _per_month(a.get('mu', 0), hours) or 1
    return {'slug': 'service-bus', 'label': f"Service Bus {key[2]}: {tier.title()}, {a.get('ops', 0):g} M operations (from the bill)", 'set': f, 'check': [tier.title()]}


def _l_asr(key, a, hours):
    return {'slug': 'site-recovery', 'label': f"Site Recovery: {round(a.get('azure', 0))} Azure / {round(a.get('customer', 0))} on-premises protected instance(s) (from the bill)",
            'set': {'azure': round(a.get('azure', 0)), 'customer': round(a.get('customer', 0))}, 'check': ['instances']}


def _l_cosmos(key, a, hours):
    f = {'apiChoice': 'azure-cosmos-db-nosql', 'serviceTier': 'generalpurpose', 'userStorageUnits': round(a.get('gb', 0), 1),
         'requestUnitsRegionsAdded': ['__region__'], 'reservedInstanceRegion': '__region__', 'serverlessRegion': '__region__', 'hours': 730}
    if a.get('serverless'):
        f.update(databaseOperation='serverless', userRequestUnits=round(a.get('mru', 0), 2))
        what = f"serverless, {a.get('mru', 0):g} M RUs"
    elif a.get('ru_auto'):
        f.update(databaseOperation='autoscale', userRequestUnits=max(100, round(a['ru_auto'] * 100 / hours / 100) * 100), averageUtilization=100)
        what = f"autoscale {f['userRequestUnits']} RU/s"
    else:
        f.update(databaseOperation='standard', userRequestUnits=max(100, round(a.get('ru', 0) * 100 / hours / 100) * 100))
        what = f"{f['userRequestUnits']} RU/s"
    return {'slug': 'cosmos-db', 'label': f"Azure Cosmos DB {key[2]}: {what}, {a.get('gb', 0):,.0f} GB (from the bill)", 'set': f, 'check': ['Cosmos']}


def _l_sqldtu(key, a, hours):
    days = max(1.0, hours / 24)
    n = max(1, round(a.get('days', 0) / days))
    return {'slug': 'azure-sql-database', 'label': f"Azure SQL Database DTU {key[3].upper()}: {n} database(s) (from the bill)",
            'set': {'type': 'single', 'purchaseModel': 'dtu', 'dtuTier': key[2], 'singlePerformanceLevel': key[3], 'singleCount': n, 'hours': 730,
                    'singleStorageUnits': 0}, 'check': [key[3].upper()]}


def _l_functions(key, a, hours):
    execs = round(a.get('execs', 0) * 10)          # bill unit: 10 executions
    gbs = a.get('gbs', 0)
    ms = round(gbs * 1000 / max(execs, 1) / 0.5) if execs else 100   # GB-s -> ms per execution at 512 MB
    return {'slug': 'functions', 'label': f"Azure Functions Consumption: {execs:,} executions, {gbs:,.0f} GB-s (from the bill)",
            'set': {'tier': 'consumption', 'memorySize': 512, 'executionCount': execs, 'executionTime': max(100, ms)}, 'check': ['Consumption']}


def _l_sentinel(key, a, hours):
    days = max(1.0, hours / 24)
    return {'slug': 'azure-sentinel', 'label': f"Microsoft Sentinel: {a.get('gb', 0):,.1f} GB analysed (from the bill)",
            'set': {'dailyAnalyticLogsIngested': round(a.get('gb', 0) / days, 2), 'dataRetentionTotalRetention': 3}, 'check': ['Logs ingested']}


def _l_storage(key, a, hours):
    acct, kind, red = key[2], key[3], key[4]
    if kind == 'files':
        return {'slug': 'storage\\files', 'label': f"Azure Files {acct}: {a.get('gb', 0):,.0f} GiB {red.upper()} (from the bill)",
                'set': {'tier': 'standard', 'billingModel': 'payg', 'accessTier': 'hot', 'redundancy': red if red in ('lrs', 'zrs', 'grs', 'gzrs') else 'lrs',
                        'usedPaygStorageUnits': round(a.get('gb', 0), 1), 'filesWrite': round(a.get('write', 0)), 'filesRead': round(a.get('read', 0)),
                        'filesList': round(a.get('list', 0)), 'filesOther': round(a.get('other', 0)), 'usedSnapshotPaygStorageUnits': 0,
                        'usedSoftDeletedPaygStorageUnits': 0, 'fileSyncPaygServers': 0, 'dataRetrievalUnits': 0, 'metadataAtRestUnits': 0, 'geoReplicationUnits': 0},
                'check': ['Pay-as-you-go']}
    return {'slug': 'storage', 'label': f"Blob Storage {acct}: {kind.title()} {red.upper()} {a.get('gb', 0):,.0f} GB + operations (from the bill)",
            'set': {'type': 'block-blob', 'performanceTier': 'standard', 'storageAccountType': 'general-purpose-v2', 'accessTier': kind, 'redundancy': red,
                    'count': round(a.get('gb', 0), 1), 'blobWriteOperations': round(a.get('write', 0)), 'blobReadOperations': round(a.get('read', 0)),
                    'blobOtherOperations': round(a.get('other', 0)), 'blobCreateContainerOperations': round(a.get('list', 0)), 'blobIterativeReadOperations': 0,
                    'blobIterativeWriteOperations': 0, 'blobDataRetrieval': round(a.get('retrieval', 0)), 'blobArchiveRetrieval': 0,
                    'queueClass1Operations': 0, 'queueClass2Operations': 0, 'dataRetrievalUnits': 0}, 'check': ['Capacity']}


_SERVICE_LINES = {'lb': _l_lb, 'nat': _l_nat, 'bastion': _l_bastion, 'privatelink': _l_privatelink, 'appgw': _l_appgw, 'appgw1': _l_appgw1,
                  'acr': _l_acr, 'redis': _l_redis, 'eventhubs': _l_eventhubs, 'servicebus': _l_servicebus, 'asr': _l_asr, 'cosmos': _l_cosmos,
                  'sqldtu': _l_sqldtu, 'functions': _l_functions, 'sentinel': _l_sentinel, 'storage': _l_storage}


def meter_lines(month_cost, region_of, hours):
    """Usage that the inventory cannot price but the bill can: one calculator line per kind of usage, built from the
    billed quantities. Returns (items, claimed_row_indexes). Region is the resource's region (region_of(resourceId))."""
    acc, claimed = {}, set()

    def put(key, k, **add):
        a = acc.setdefault(key, {})
        for f, v in add.items():
            a[f] = a.get(f, 0.0) + v
        claimed.add(k)
    for k, r in enumerate(month_cost):
        t, m, s, q = _rtype(r['resourceId']), str(r['meter']).lower(), str(r.get('meterSubCategory') or '').lower(), float(r['quantity'] or 0)
        reg = region_of(r['resourceId'])
        if q <= 0 and r['cost'] <= 0:
            continue
        svc = None if re.search(r'data transfer out|internet egress', m) else _service_row(t, m, s, q, r['resourceId'])
        if svc:
            put((svc[0], reg) + tuple(svc[1]), k, **svc[2])
        elif 'snapshot' in m:
            put(('snapshot', reg, _red(m), 'premium' in m), k, gb=q)
        elif 'peering' in s or 'peering' in m or (t == 'microsoft.network/virtualnetworks' and re.search(r'ingress|egress', m)):
            put(('peering', reg), k, gb=q)
        elif re.search(r'data transfer out|egress', m) and re.search(r'inter.?region|intra.?continent|inter.?continent', m + ' ' + s):
            far = 'inter' in re.sub(r'inter.?region', '', m + ' ' + s) and 'continent' in m + ' ' + s
            put(('interregion', reg, far), k, gb=q)
        elif re.search(r'data transfer out|internet egress', m):
            put(('internet', reg), k, gb=q)
        elif t == 'microsoft.recoveryservices/vaults' or 'backup' in s:
            if re.search(r'data stored|storage|gb', m):
                put(('backup', reg), k, gb=q, **{'red_' + _red(m): q})
            else:
                put(('backup', reg), k, inst=q)
        elif t == 'microsoft.keyvault/vaults' or s.startswith('key vault'):
            f = 'renewals' if 'renewal' in m else 'advancedOperations' if 'advanced' in m and 'operation' in m else \
                'advancedKeys' if 'advanced' in m else 'keys' if 'key' in m else 'operations'
            put(('keyvault', reg), k, **{f: q})
        elif t.startswith('microsoft.network/dnszones') or t.startswith('microsoft.network/privatednszones') or s.startswith('azure dns'):
            priv = 'private' in m or 'private' in t
            f = ('privateQueries' if priv else 'queries') if 'quer' in m else ('privateZones' if priv else 'zones')
            put(('dns',), k, **{f: q})
        elif re.search(r'network logs|flow log', m) or t == 'microsoft.network/networkwatchers':
            put(('netwatch', reg), k, gb=q)
        elif t == 'microsoft.compute/disks' and 'operation' in m:
            tier = 'standardssd' if 'ssd' in m + ' ' + s else 'standardhdd'
            put(('diskops', reg, tier), k, units=q)
    items = []
    days = max(1.0, hours / 24)
    for key, a in acc.items():
        kind, reg = key[0], (key[1] if len(key) > 1 else 'uaenorth')
        if kind == 'snapshot':
            red, prem = key[2], key[3]
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'managed-disks',
                          'label': f"Snapshots {'Premium' if prem else 'Standard'} {red.upper()}: {a['gb']:,.0f} GB (from the bill)",
                          'set': {'tier': 'premiumssd' if prem else 'standardhdd', 'managedDiskType': 'p10' if prem else 's4', 'managedDisks': 0,
                                  'managedDisksTransactions': 0, 'addSnapshot': True, 'snapshotStorage': round(a['gb'], 1),
                                  'managedSnapshotRedundancy': 'zrs' if red == 'zrs' else 'lrs', 'managedDiskRedundancy': 'lrs'}, 'check': ['Snapshot']})
        elif kind == 'internet':
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'bandwidth', 'label': f"Internet egress: {a['gb']:,.1f} GB (from the bill)",
                          'set': {'dataTransferType': 'internetegress', 'sourceRegion': '__region__', 'internetEgressUnits': round(a['gb'], 2),
                                  'routedVia': 'microsoftglobalnetwork'}, 'check': ['Internet egress']})
        elif kind == 'interregion':
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'bandwidth', 'label': f"Inter-region data transfer: {a['gb']:,.1f} GB (from the bill)",
                          'set': {'dataTransferType': 'interregion', 'sourceRegion': '__region__', 'destinationRegion': '__far__' if key[2] else '__near__',
                                  'interRegionUnits': round(a['gb'], 2)}, 'check': ['Inter Region']})
        elif kind == 'peering':
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'virtual-network', 'label': f"VNet peering: {a['gb']:,.1f} GB in + out (from the bill)",
                          'set': {'vNet1Region': '__region__', 'vNet2Region': '__region__', 'vNet1Peering': round(a['gb'] / 2, 2), 'vNet2Peering': 0},
                          'check': ['Virtual Network 1']})
        elif kind == 'backup':
            n = max(1, round(a.get('inst', 0) * (730.0 / hours) if a.get('inst', 0) > 40 else a.get('inst', 0)) or 1)
            size = max(1, round(a.get('gb', 0) / (n * 1.3)))
            reds = {f[4:]: v for f, v in a.items() if f.startswith('red_')}
            red = max(reds, key=reds.get) if reds else 'lrs'
            red = red if red in ('lrs', 'grs', 'zrs', 'ra-grs') else 'grs'
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'backup', 'label': f"Azure Backup: {n} protected instance(s), {a.get('gb', 0):,.0f} GB stored {red.upper()} (from the bill)",
                          'set': {'type': 'backup-azure-vm', 'backuppolicytype': 'standard', 'instances': n, 'backup': size, 'backupUnits': 1,
                                  'redundancy': red, 'averageDailyChurn': 'low'}, 'check': ['Instance']})
        elif kind == 'keyvault':
            f = {x: round(a.get(x, 0), 2) for x in ('renewals', 'keys', 'advancedKeys')}
            f.update(operations=round(a.get('operations', 0) * 10000), advancedOperations=round(a.get('advancedOperations', 0) * 10000))  # bill unit: 10,000
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'key-vault', 'label': f"Key Vault: {f['operations']:,} operations (from the bill)",
                          'set': f, 'check': ['operations']})
        elif kind == 'dns':
            f = {x: round(a.get(x, 0), 3) for x in ('zones', 'queries', 'privateZones', 'privateQueries')}
            f['zones'] = max(f['zones'], 0); f['privateZones'] = max(f['privateZones'], 0)
            items.append({'type': 'calc', 'key': key, 'region': 'uaenorth', 'slug': 'dns', 'label': f"Azure DNS: {f['zones']:g} public / {f['privateZones']:g} private zones (from the bill)",
                          'set': dict(f, zone='zone1', type='public'), 'check': ['DNS'], 'noregion': True})
        elif kind == 'netwatch':
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'network-watcher', 'label': f"Network Watcher logs: {a['gb']:,.1f} GB (from the bill)",
                          'set': {'networkLogs': round(a['gb'], 2), 'diagnosticTools': 0, 'pingMesh': 0, 'networkAnalyticsMonitored': 0,
                                  'trafficAnalyticsAcceleratedLogs': 0, 'trafficAnalyticsStandardLogs': 0}, 'check': ['Network Logs']})
        elif kind in _SERVICE_LINES:
            line = _SERVICE_LINES[kind](key, a, hours)
            if line:
                items.append(dict({'type': 'calc', 'key': key, 'region': reg}, **line))
        elif kind == 'diskops':
            tier = key[2]
            items.append({'type': 'calc', 'key': key, 'region': reg, 'slug': 'managed-disks', 'label': f"Disk transactions {'Standard SSD' if tier == 'standardssd' else 'Standard HDD'}: {a['units']:,.0f} x 10,000 (from the bill)",
                          'set': {'tier': tier, 'managedDiskType': 'e1' if tier == 'standardssd' else 's4', 'managedDisks': 0, 'managedDisksTransactions': round(a['units']),
                                  'addSnapshot': False, 'snapshotStorage': 0, 'managedDiskRedundancy': 'lrs'}, 'check': ['transactions']})
    return items, claimed


def items_for_month(inventory, month_cost, month, storage_gb=None, include_arc_disconnected=False, now=None,
                    arc_as_vm=False, hours=None, scale=False):
    """Line items for what RAN in that month: resources on that month's bill (current specs where the resource still
    exists, specs from the meter where it was deleted), with the billed VM hours, plus Azure Arc servers (on-premises
    machines are not on the Azure bill, so they come from the current inventory). Returns (items, notes, summary).
    arc_as_vm=False (default): Azure Arc / on-premises servers are NOT priced as Azure VMs (that would be a migration
    estimate); only their real Azure charges from the bill are used (Defender, Log Analytics, Arc SQL licences)."""
    hrs_month = hours or month_hours(month, now)
    to_month = (730.0 / hrs_month) if scale else 1.0
    billed = {}
    for r in month_cost:
        b = billed.setdefault(r['resourceId'], {'cost': 0.0, 'meters': []})
        b['cost'] += r['cost']; b['meters'].append(r)
    use = [r for r in inventory if str(r.get('id', '')).lower() in billed
           or (arc_as_vm and str(r.get('type', '')).lower() in ('microsoft.hybridcompute/machines', 'microsoft.azurearcdata/sqlserverinstances'))]
    all_items, notes = to_items(use, True, storage_gb)
    by_id = {str(x.get('id', '')).lower(): x for x in inventory}
    items = []
    for it in all_items:
        rid = it.get('resource_id', '')
        if it.get('arc'):
            if arc_as_vm and (it.get('arc_status') == 'connected' or include_arc_disconnected):
                items.append(it)
            continue
        if rid not in billed:
            continue
        if it['type'] == 'vm' and not it.get('quantity', 1) > 1:
            q = sum(m['quantity'] for m in billed[rid]['meters'] if _meter_vm(m['meter']))
            if q <= 0:
                continue
            it = dict(it, hours=round(min(q * to_month, 730 if scale else hrs_month), 1), notes=(it.get('notes') or '') + f'; ran {q:g} h in the billing period')
        items.append(it)
    # deleted since then: rebuild VMs and managed disks from their meters
    for rid, b in billed.items():
        if not rid or rid in by_id:
            continue
        t = '/'.join(rid.split('/providers/')[-1].split('/')[:2]) if '/providers/' in rid else ''
        name = rid.rsplit('/', 1)[-1][:100]
        loc = 'uaenorth'
        merged = {}
        for m in b['meters']:                      # one row per meter (the same meter can come back from several queries)
            x = merged.setdefault(m['meter'], dict(m, quantity=0.0)); x['quantity'] += m['quantity']
        for m in merged.values():
            if t == 'microsoft.compute/virtualmachines' and _meter_vm(m['meter']) and m['quantity'] > 0:
                os_ = 'windows' if 'windows' in m['meterSubCategory'].lower() else 'linux'
                items.append({'type': 'vm', 'name': name, 'sku': _meter_vm(m['meter']), 'os': os_, 'ahub': 'no', 'quantity': 1,
                              'hours': round(min(m['quantity'] * to_month, 730 if scale else hrs_month), 1), 'region': loc, 'resource_id': rid, 'source': 'Bill (deleted resource)',
                              'notes': f"Deleted since; {m['meter']} ran {m['quantity']:g} h in the billing period"})
            dm = _DISK_METER.match(m['meter'].strip())
            if t == 'microsoft.compute/disks' and dm:
                tier = {'P': 'premium', 'E': 'standardssd', 'S': 'standard'}[dm.group(1).upper()]
                n = int(dm.group(2)); sizes = {4: 32, 6: 64, 10: 128, 15: 256, 20: 512, 30: 1024, 40: 2048, 50: 4096, 60: 8192, 70: 16384, 80: 32767, 1: 4, 2: 8, 3: 16}
                items.append({'type': 'disk', 'name': name, 'tier': tier, 'disk_gb': sizes.get(n, 128), 'quantity': 1, 'region': loc, 'resource_id': rid,
                              'source': 'Bill (deleted resource)', 'notes': f"Deleted since; {m['meter']} in the billing period"})
    extra = bill_items(month_cost, hrs_month)
    items += extra
    loc = {str(x.get('id', '')).lower(): x.get('location') for x in inventory}
    kinds = {str(x.get('id', '')).lower(): x.get('kind') or '' for x in inventory}
    usage, usage_rows = meter_lines(month_cost, lambda rid: loc.get(rid.split('/extensions/')[0]) or loc.get(rid) or 'uaenorth', hrs_month)
    bill_storage = {month_cost[k]['resourceId'] for k in usage_rows if _rtype(month_cost[k]['resourceId']) == 'microsoft.storage/storageaccounts'}
    items = [i for i in items if not (i.get('type') == 'blob' and i.get('resource_id') in bill_storage)]
    items += usage
    covered = {i.get('resource_id') for i in items}
    bill_covered = {'defender': lambda r: re.search(r'(?i)\bnode\b', r['meter']), 'law': lambda r: re.search(r'(?i)data ingestion|analytics logs', r['meter']),
                    'arc_sql': lambda r: _rtype(r['resourceId']) == 'microsoft.azurearcdata/sqlserverinstances'}
    covered_rows = set(usage_rows)
    for x in extra:
        for k, r in enumerate(month_cost):
            if bill_covered[x['type']](r):
                covered_rows.add(k)
    uncovered, no_calc, defaults = {}, {}, {}
    for k, r in enumerate(month_cost):
        if k in covered_rows or r['resourceId'] in covered or r['cost'] < 0.005:      # sub-cent rows are rounding, not a gap
            continue
        t = (_rtype(r['resourceId']) or '(no resource)') + ' · ' + (r['meter'] or '?')
        cat = r.get('meterCategory') or ''
        rt = _rtype(r['resourceId'])
        kind = str(kinds.get(r['resourceId'].split('/extensions/')[0], '')).lower().replace('.', '')
        slug = RTYPE_SLUGS.get(rt + '#' + kind) or RTYPE_SLUGS.get(rt) or (SERVICE_SLUGS.get(cat, '?') if cat else '?')
        cat = cat or rt
        if _rtype(r['resourceId']) in NO_CALC_EQUIVALENT or slug is None:
            no_calc[(cat or t.split(' · ')[0]) + ' · ' + (r['meter'] or '?')] = no_calc.get((cat or t.split(' · ')[0]) + ' · ' + (r['meter'] or '?'), 0) + r['cost']
        elif slug != '?':                                   # known Azure service: add its calculator page with default settings
            reg = loc.get(r['resourceId'].split('/extensions/')[0]) or loc.get(r['resourceId']) or 'uaenorth'
            g = defaults.setdefault((slug, cat, reg), {'cost': 0.0, 'meters': {}})
            g['cost'] += r['cost']; g['meters'][r['meter'] or '?'] = g['meters'].get(r['meter'] or '?', 0) + float(r['quantity'] or 0)
        else:
            uncovered[t] = uncovered.get(t, 0) + r['cost']
    cur0 = next((r['currency'] for r in month_cost if r.get('currency')), '')
    for (slug, cat, reg), g in sorted(defaults.items(), key=lambda kv: -kv[1]['cost']):
        what = ', '.join(f"{m} {q:g}" for m, q in sorted(g['meters'].items(), key=lambda x: -x[1])[:3])
        items.append({'type': 'calc', 'key': ('default', slug, cat, reg), 'region': reg, 'slug': slug, 'check': [],
                      'label': f"{cat}: billed {cur0} {g['cost']:,.2f} ({what}) - added with default settings, set the quantity",
                      'set': dict(ZERO_OVERRIDES.get(slug, {}), cccZero=True, displayName=f"{cat} - billed {g['cost']:,.2f} {cur0} - set quantity"[:110])})
    # inventory notes for types that are now priced from the bill ("66 x snapshots: default usage assumed") are stale
    bill_types = {_rtype(month_cost[k]['resourceId']) for k in usage_rows}
    shorts = {t.split('/')[-1] for t in bill_types if t}
    notes = [n for n in notes if not any(re.match(r'^\d+ x ' + re.escape(sh) + r'\b', str(n)) for sh in shorts)]
    total = sum(r['cost'] for r in month_cost)
    cur = next((r['currency'] for r in month_cost if r.get('currency')), '')
    not_itemised_total = sum(uncovered.values()) + sum(g['cost'] for g in defaults.values()) + sum(no_calc.values())
    summary = {'month': month, 'label': month_label(month) if month else 'last 30 days', 'actual_total': round(total, 2), 'currency': cur,
               'itemised_pct': round(100 * (1 - not_itemised_total / total), 1) if total else None, 'arc_as_vm': arc_as_vm,
               'billed_resources': len([k for k in billed if k]), 'hours_in_month': hrs_month,
               'not_itemised': sorted(({'type': k, 'cost': round(v, 2)} for k, v in uncovered.items()), key=lambda x: -x['cost'])[:40],
               'default_settings': sorted(({'type': f"{cat} ({slug})", 'cost': round(g['cost'], 2)} for (slug, cat, reg), g in defaults.items()), key=lambda x: -x['cost'])[:40],
               'in_calculator_pct': round(100 * (1 - (sum(uncovered.values()) + sum(no_calc.values())) / total), 1) if total else None,
               'no_calculator_equivalent': sorted(({'type': k, 'cost': round(v, 2)} for k, v in no_calc.items()), key=lambda x: -x['cost'])[:20],
               'priced_from_bill': sorted({month_cost[k]['resourceId'] for k in usage_rows})}
    return items, notes, summary


def month_inventory(inventory, month_cost):
    """Inventory rows that were on that month's bill (used for usage-based services such as AI Search or Log Analytics)."""
    billed = {r['resourceId'] for r in month_cost}
    return [r for r in inventory if str(r.get('id', '')).lower() in billed]


def summarize(inventory):
    counts = {}
    for r in inventory:
        k = (str(r.get('subscriptionId', '')), str(r.get('type', '')).lower())
        counts[k] = counts.get(k, 0) + 1
    return [{'subscriptionId': k[0], 'type': k[1], 'count': v} for k, v in sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))]


# ----------------------------------------------------------------------------- inventory -> estimator line items
def _g(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


DISK_TIERS = {'premium_lrs': 'premium', 'premium_zrs': 'premium', 'standardssd_lrs': 'standardssd', 'standardssd_zrs': 'standardssd',
              'standard_lrs': 'standard'}
SQL_TIERS = {'generalpurpose': 'gp', 'businesscritical': 'bc'}


def to_items(inventory, include_powered_off=False, storage_gb=None):
    """Map discovered resources to estimator line items. Returns (items, notes).

    Only what can be priced from metadata is mapped: VMs (SKU, OS, Hybrid Benefit), managed disks
    (tier + size), vCore Azure SQL databases and public IPs. Everything else is listed in the notes
    as not priced; storage accounts need usage data (GB) that inventory does not contain."""
    items, skipped, notes = [], {}, []
    why_by_id = to_items.why = {}
    nocharge_kinds = set()
    cur = [None]

    def skip(kind, why):
        skipped.setdefault((kind, why), 0)
        skipped[(kind, why)] += 1
        if cur[0]:
            why_by_id[cur[0]] = f'{kind}: {why}'
    sql_on = {}                                   # Arc SQL Server instances by host machine id
    for r in inventory:
        if str(r.get('type', '')).lower() == 'microsoft.azurearcdata/sqlserverinstances':
            p = r.get('properties') or {}
            host = str(p.get('containerResourceId') or '').lower()
            if host:
                sql_on.setdefault(host, []).append(str(p.get('edition') or 'unknown'))
    for r in inventory:
        t = str(r.get('type', '')).lower()
        name = str(r.get('name', ''))[:100]
        loc = str(r.get('location', '') or '')
        p = r.get('properties') or {}
        base = {'name': name, 'region': loc, 'source': 'Azure discovery', 'quantity': 1, 'resource_id': str(r.get('id', '')).lower()}
        cur[0] = base['resource_id']
        if t == 'microsoft.compute/virtualmachines':
            size = _g(p, 'hardwareProfile', 'vmSize')
            state = str(_g(p, 'extended', 'instanceView', 'powerState', 'code', default='')).lower()
            if not size:
                skip('Virtual machine', 'no VM size returned'); continue
            if 'deallocated' in state and not include_powered_off:
                skip('Virtual machine', 'deallocated (tick "include powered-off")'); continue
            os_ = str(_g(p, 'storageProfile', 'osDisk', 'osType', default='')).lower()
            lic = str(p.get('licenseType', '')).lower()
            items.append(dict(base, type='vm', sku=size, os='windows' if os_ == 'windows' else 'linux',
                              ahub='yes' if lic in ('windows_server', 'windows_client') else 'no',
                              notes='Discovered; ' + (state.replace('powerstate/', '') or 'power state unknown')))
        elif t == 'microsoft.compute/disks':
            sku = str((r.get('sku') or {}).get('name', '')).lower()
            size = p.get('diskSizeGB')
            if sku not in DISK_TIERS:
                skip('Managed disk', f"SKU '{(r.get('sku') or {}).get('name', '?')}' is not priced (Ultra / Premium SSD v2)"); continue
            if not size:
                skip('Managed disk', 'no size returned'); continue
            items.append(dict(base, type='disk', tier=DISK_TIERS[sku], disk_gb=size))
        elif t == 'microsoft.sql/servers/databases':
            if name == 'master' or str(r.get('kind', '')).lower().find('system') >= 0:
                continue
            sku = r.get('sku') or {}
            tier = SQL_TIERS.get(str(sku.get('tier', '')).lower().replace(' ', ''))
            cap = sku.get('capacity')
            if p.get('elasticPoolId'):
                skip('SQL database', 'in an elastic pool (pool not priced)'); continue
            if not tier or not cap or 'serverless' in str(sku.get('name', '')).lower() or '_S_' in str(sku.get('name', '')):
                skip('SQL database', 'DTU, Hyperscale or serverless (not priced)'); continue
            items.append(dict(base, type='sql_db', tier=tier, vcores=cap,
                              storage_gb=round((p.get('maxSizeBytes') or 0) / 1024 ** 3) or 32,
                              ahub='yes' if str(p.get('licenseType', '')).lower() == 'baseprice' else 'no'))
        elif t == 'microsoft.network/publicipaddresses':
            items.append(dict(base, type='public_ip', notes='Discovered'))
        elif t == 'microsoft.hybridcompute/machines':
            # Azure Arc server (on-premises / VMware / other cloud): price an equivalent Azure VM, as-is (no utilisation data)
            dp = p.get('detectedProperties') or {}
            hw = p.get('hardwareProfile') or {}
            status = str(p.get('status', '')).lower()
            if status != 'connected' and not include_powered_off:
                skip('Arc server', f"status {p.get('status')} (tick \"include powered-off\" to include)"); continue
            vcpu = _num(dp.get('logicalCoreCount')) or sum(_num(x.get('numberOfCores')) or 0 for x in hw.get('processors') or []) or None
            ram = _num(dp.get('totalPhysicalMemoryInGigabytes')) or ((_num(hw.get('totalPhysicalMemoryInBytes')) or 0) / 1024 ** 3) or None
            if not vcpu or not ram:
                skip('Arc server', 'no CPU / memory reported by the Arc agent'); continue
            os_ = 'windows' if str(p.get('osType', '')).lower() == 'windows' else 'linux'
            editions = [e for e in sql_on.get(base['resource_id'], [])]
            paid_sql = [e for e in editions if e.lower() in ('standard', 'enterprise')]
            note = f"Azure Arc server ({dp.get('manufacturer') or 'unknown host'} {dp.get('model') or ''}".strip() + \
                   f"), {p.get('osSku') or os_}, {vcpu:g} vCPU / {ram:g} GB as-is"
            if editions:
                note += '; SQL Server ' + ', '.join(editions) + (' (licence needed)' if paid_sql else ' (free edition)')
            if 'Server 2012' in str(p.get('osSku', '')):
                note += '; Windows Server 2012 R2: free Extended Security Updates on Azure'
            items.append(dict(base, type='vm', vcpu=vcpu, ram_gb=round(ram), os=os_, ahub='no', notes=note, arc_status=status,
                              arc=True, sql_edition=paid_sql[0].title() if paid_sql else ''))
            seen_vol = set()
            for d in (p.get('storageProfile') or {}).get('disks') or []:
                gb = round((_num(d.get('maxSizeInBytes')) or 0) / 1024 ** 3)
                if gb < 1:
                    continue
                dn = str(d.get('name', ''))
                vol = (dn.lower(), round(gb / 16))       # same model + size on one server = multipath copy of one LUN
                if vol in seen_vol:
                    skip('Arc server disk', 'duplicate path to the same SAN LUN (multipath), counted once'); continue
                seen_vol.add(vol)
                san = bool(re.search(r'(?i)iscsi|huawei|netapp|qnap|synology|emc|pure|hitachi|3par|nimble|multi-path|mpio|fibre', dn))
                parts = -(-gb // 32767)                      # Azure managed disks max 32 TiB: split larger volumes
                for k in range(parts):
                    items.append(dict(base, type='disk', arc_status=status, name=f"{name} disk {dn}"[:90] + (f' part {k + 1}' if parts > 1 else ''),
                                      disk_gb=min(32767, gb - 32767 * k), arc=True, large=gb > 4096, san=san,
                                      **({'tier': 'standard'} if san else {}),
                                      notes=('SAN / iSCSI volume' if san else 'Disk') + f' of Azure Arc server, provisioned {gb} GB'
                                            + (f', split into {parts} x 32 TiB disks' if parts > 1 else '')))
        elif t == 'microsoft.compute/virtualmachinescalesets':
            sku = r.get('sku') or {}
            if not sku.get('name') or not sku.get('capacity'):
                skip('VM scale set', 'no size or instance count'); continue
            os_ = str(_g(p, 'virtualMachineProfile', 'storageProfile', 'osDisk', 'osType', default='')).lower()
            items.append(dict(base, type='vm', sku=sku['name'], quantity=int(sku['capacity']), os='windows' if os_ == 'windows' else 'linux',
                              ahub='yes' if str(_g(p, 'virtualMachineProfile', 'licenseType', default='')).lower() == 'windows_server' else 'no',
                              notes='Scale set at its current instance count'))
        elif t == 'microsoft.web/serverfarms':
            sku = r.get('sku') or {}
            nm = str(sku.get('name', ''))
            if not nm or re.match(r'(?i)^(Y1|FC1|EP|WS|I\d|D1|F1)', nm):
                skip('App Service plan', f"tier '{nm or '?'}' is usage based: sent to the calculator with default usage"); continue
            items.append(dict(base, type='app_service', sku=nm, os='linux' if p.get('reserved') else 'windows', quantity=max(1, int(sku.get('capacity') or 1))))
        elif t == 'microsoft.containerservice/managedclusters':
            tier = str((r.get('sku') or {}).get('tier', 'Free')).lower()
            items.append(dict(base, type='aks', tier='standard' if tier in ('standard', 'premium') else 'free', notes='Control plane tier'))
            for pool in p.get('agentPoolProfiles') or []:
                if pool.get('vmSize') and pool.get('count'):
                    items.append(dict(base, type='vm', name=f"{name}/{pool.get('name', 'pool')}", sku=pool['vmSize'], quantity=int(pool['count']),
                                      os='windows' if str(pool.get('osType', '')).lower() == 'windows' else 'linux', notes='AKS node pool'))
        elif t == 'microsoft.network/azurefirewalls':
            tier = str(_g(p, 'sku', 'tier', default='Standard')).title()
            items.append(dict(base, type='firewall', tier=tier if tier in ('Standard', 'Premium', 'Basic') else 'Standard', gb=0,
                              notes='Data processed not known; set to 0'))
        elif t == 'microsoft.network/virtualnetworkgateways':
            if str(p.get('gatewayType', '')).lower() != 'vpn':
                skip('Virtual network gateway', 'ExpressRoute gateway (not priced)'); continue
            items.append(dict(base, type='vpn_gateway', sku=_g(p, 'sku', 'name', default='VpnGw1'), s2s=1))
        elif t == 'microsoft.sql/managedinstances':
            tier = SQL_TIERS.get(str((r.get('sku') or {}).get('tier', '')).lower().replace(' ', ''))
            vc = p.get('vCores') or (r.get('sku') or {}).get('capacity')
            if not tier or not vc:
                skip('SQL managed instance', 'tier or vCores unknown'); continue
            items.append(dict(base, type='sql_mi', tier=tier, vcores=vc, storage_gb=p.get('storageSizeInGB') or 32,
                              ahub='yes' if str(p.get('licenseType', '')).lower() == 'baseprice' else 'no'))
        elif t == 'microsoft.storage/storageaccounts':
            gb = (storage_gb or {}).get(str(r.get('id', '')).lower())
            if not gb:
                skip('Storage account', 'no usage data (Azure Monitor returned nothing); priced only if usage is read'); continue
            sku = str((r.get('sku') or {}).get('name', 'Standard_LRS')).split('_')[-1].upper()
            tier = str(p.get('accessTier') or 'Hot').title()
            items.append(dict(base, type='blob', tier=tier if tier in ('Hot', 'Cool', 'Cold', 'Archive') else 'Hot',
                              redundancy=sku if sku in ('LRS', 'GRS', 'ZRS', 'RAGRS', 'GZRS', 'RAGZRS') else 'LRS', gb=gb,
                              notes=f'Used capacity read from Azure Monitor: {gb} GB (capacity only; transactions not included)'))
        else:
            if _nocharge(t):
                nocharge_kinds.add(t.split('/')[-1])
            skip(t.split('/')[-1] if t else 'unknown', 'calculator only, default usage assumed (not in the estimator BOQ)' if t in EXTRA_LABELS else 'not priced from inventory')
    for (kind, why), n in sorted(skipped.items(), key=lambda kv: -kv[1]):
        if why == 'not priced from inventory' and kind in nocharge_kinds:
            continue
        notes.append(f'{n} x {kind}: {why}')
    return items, notes


NO_CHARGE = (  # resource types that carry no charge of their own (or are billed through another resource)
    'microsoft.network/virtualnetworks', 'microsoft.network/networksecuritygroups', 'microsoft.network/networkinterfaces',
    'microsoft.network/routetables', 'microsoft.network/applicationsecuritygroups', 'microsoft.network/networkwatchers',
    'microsoft.managedidentity/userassignedidentities', 'microsoft.compute/availabilitysets', 'microsoft.compute/sshpublickeys',
    'microsoft.compute/proximityplacementgroups', 'microsoft.compute/virtualmachines/extensions', 'microsoft.compute/restorepointcollections',
    'microsoft.insights/actiongroups', 'microsoft.insights/activitylogalerts',
    'microsoft.insights/workbooks', 'microsoft.operationsmanagement/solutions', 'microsoft.web/sites/slots', 'microsoft.network/networkwatchers/flowlogs',
    'microsoft.authorization/', 'microsoft.resources/', 'microsoft.compute/galleries', 'microsoft.network/privatednszones/virtualnetworklinks',
    'microsoft.network/virtualnetworks/subnets', 'microsoft.network/networkintentpolicies', 'microsoft.alertsmanagement/', 'microsoft.devtestlab/schedules',
    'microsoft.compute/virtualmachinescalesets/extensions', 'microsoft.hybridcompute/machines/extensions',
    'microsoft.azurearcdata/', 'microsoft.cognitiveservices/accounts/projects', 'microsoft.web/connections',
    'microsoft.insights/datacollectionrules', 'microsoft.insights/datacollectionendpoints', 'microsoft.security/',
    'microsoft.hybridcompute/gateways', 'microsoft.hybridcompute/privatelinkscopes', 'microsoft.communication/emailservices/domains',
    'microsoft.portal/dashboards', 'microsoft.network/privateendpoints/', 'microsoft.guestconfiguration/', 'microsoft.maintenance/', 'microsoft.web/sites', 'microsoft.sql/servers', 'microsoft.storage/storageaccounts/', 'microsoft.network/serviceendpointpolicies',
)

EXTRA_LABELS = {
    'microsoft.network/applicationgateways': 'Application Gateway', 'microsoft.network/loadbalancers': 'Load Balancer',
    'microsoft.network/bastionhosts': 'Azure Bastion', 'microsoft.network/natgateways': 'NAT Gateway',
    'microsoft.cache/redis': 'Azure Cache for Redis', 'microsoft.documentdb/databaseaccounts': 'Azure Cosmos DB account',
    'microsoft.dbforpostgresql/flexibleservers': 'Azure Database for PostgreSQL flexible server',
    'microsoft.dbformysql/flexibleservers': 'Azure Database for MySQL flexible server',
    'microsoft.containerregistry/registries': 'Container Registry', 'microsoft.recoveryservices/vaults': 'Recovery Services vault (Backup)',
    'microsoft.operationalinsights/workspaces': 'Log Analytics workspace', 'microsoft.keyvault/vaults': 'Key Vault',
    'microsoft.network/expressroutecircuits': 'ExpressRoute circuit', 'microsoft.network/frontdoors': 'Front Door',
    'microsoft.cdn/profiles': 'Front Door / CDN profile', 'microsoft.eventhub/namespaces': 'Event Hubs namespace',
    'microsoft.servicebus/namespaces': 'Service Bus namespace', 'microsoft.network/ddosprotectionplans': 'DDoS Protection plan',
    'microsoft.cognitiveservices/accounts': 'Azure AI services / Azure OpenAI (token usage)', 'microsoft.search/searchservices': 'Azure AI Search',
    'microsoft.web/staticsites': 'Static Web Apps', 'microsoft.logic/workflows': 'Logic Apps (Consumption)',
    'microsoft.communication/communicationservices': 'Azure Communication Services', 'microsoft.communication/emailservices': 'Azure Communication Services Email',
    'microsoft.monitor/accounts': 'Azure Monitor workspace (managed Prometheus)', 'microsoft.insights/scheduledqueryrules': 'Azure Monitor log alert rules',
    'microsoft.insights/metricalerts': 'Azure Monitor metric alert rules', 'microsoft.insights/components': 'Application Insights',
    'microsoft.apimanagement/service': 'API Management', 'microsoft.datafactory/factories': 'Data Factory',
    'microsoft.synapse/workspaces': 'Azure Synapse Analytics', 'microsoft.databricks/workspaces': 'Azure Databricks',
    'microsoft.machinelearningservices/workspaces': 'Azure Machine Learning', 'microsoft.app/containerapps': 'Container Apps',
    'microsoft.app/managedenvironments': 'Container Apps environment', 'microsoft.containerinstance/containergroups': 'Container Instances',
    'microsoft.eventgrid/topics': 'Event Grid', 'microsoft.eventgrid/systemtopics': 'Event Grid system topic', 'microsoft.signalrservice/signalr': 'SignalR',
    'microsoft.netapp/netappaccounts': 'Azure NetApp Files', 'microsoft.compute/snapshots': 'Managed disk snapshots',
    'microsoft.network/privateendpoints': 'Private Endpoint', 'microsoft.network/privatednszones': 'Private DNS zone', 'microsoft.network/dnszones': 'DNS zone',
    'microsoft.network/trafficmanagerprofiles': 'Traffic Manager', 'microsoft.network/firewallpolicies': 'Azure Firewall policy',
    'microsoft.network/frontdoorwebapplicationfirewallpolicies': 'Front Door WAF policy', 'microsoft.automation/automationaccounts': 'Automation',
    'microsoft.kusto/clusters': 'Azure Data Explorer', 'microsoft.dataprotection/backupvaults': 'Backup vault',
    'microsoft.web/serverfarms': 'Azure Functions / App Service consumption plan', 'microsoft.devcenter/devcenters': 'Dev Box',
    'microsoft.desktopvirtualization/hostpools': 'Azure Virtual Desktop host pool', 'microsoft.purview/accounts': 'Microsoft Purview',
    'microsoft.fabric/capacities': 'Microsoft Fabric capacity', 'microsoft.powerbidedicated/capacities': 'Power BI Embedded',
}
HANDLED = {'microsoft.compute/virtualmachines', 'microsoft.compute/disks', 'microsoft.sql/servers/databases', 'microsoft.network/publicipaddresses',
           'microsoft.hybridcompute/machines', 'microsoft.compute/virtualmachinescalesets', 'microsoft.web/serverfarms',
           'microsoft.containerservice/managedclusters', 'microsoft.network/azurefirewalls', 'microsoft.network/virtualnetworkgateways',
           'microsoft.sql/managedinstances', 'microsoft.storage/storageaccounts'}


def _nocharge(t):
    return any(t == n or (n.endswith('/') and t.startswith(n)) for n in NO_CHARGE)


def _spec(r):
    sku = r.get('sku') or {}
    p = r.get('properties') or {}
    cap = _g(p, 'workspaceCapping', 'dailyQuotaGb')
    parts = [sku.get('name') or _g(p, 'sku', 'name'), sku.get('tier') or _g(p, 'sku', 'tier'),
             ('capacity ' + str(sku['capacity'])) if sku.get('capacity') else None, r.get('kind') or None,
             ('capacity mode ' + p['capacityMode']) if p.get('capacityMode') else None,
             ('daily cap ' + str(cap) + ' GB') if cap not in (None, -1, '-1') else None,
             ('retention ' + str(p['retentionInDays']) + ' days') if p.get('retentionInDays') else None]
    out = []
    for x in parts:
        if x and str(x) not in out:
            out.append(str(x))
    return ', '.join(out)


def extra_lines(inventory, items=None):
    """Everything chargeable that is not an exact-spec line: known usage-based services plus ANY other resource type
    (generic fallback, so nothing is left out). Returned as 'N x Label (spec), region r' strings."""
    if items is None:
        items = to_items(inventory)[0]
    exact = {i.get('resource_id') for i in items if i.get('resource_id')}
    g = {}
    for r in inventory:
        t = str(r.get('type', '')).lower()
        if str(r.get('id', '')).lower() in exact or _nocharge(t):
            continue
        if t in HANDLED and not (t == 'microsoft.web/serverfarms'):
            continue                                   # handled types that were skipped keep their reason in Coverage
        if t == 'microsoft.web/serverfarms' and not re.match(r'(?i)^(Y1|FC1|EP|WS)', str((r.get('sku') or {}).get('name', ''))):
            continue
        lab = EXTRA_LABELS.get(t) or ('Azure resource type ' + t)
        key = (lab, _spec(r), str(r.get('location', '')))
        g[key] = g.get(key, 0) + 1
    out = [f"{n} x {lab}" + (f" ({spec})" if spec else '') + (f", region {loc}" if loc else '') for (lab, spec, loc), n in sorted(g.items())]
    mde = sum(1 for r in inventory if str(r.get('type', '')).lower().endswith('/extensions') and str(r.get('name', '')).split('/')[-1].upper().startswith('MDE.')
              and (str(r.get('id', '')).lower().split('/extensions/')[0] in exact or '/extensions/' not in str(r.get('id', '')).lower()))
    if mde:
        out.append(f'{mde} x Microsoft Defender for Servers (Defender for Endpoint agent found on {mde} servers; confirm Plan 1 or Plan 2)')
    return out


def coverage(inventory, items, billed=None):
    """Account for EVERY resource: how it is represented in the calculator (nothing silently dropped)."""
    exact = {i.get('resource_id') for i in items if i.get('resource_id')}
    why = getattr(to_items, 'why', {})
    rows = []
    for r in inventory:
        t = str(r.get('type', '')).lower()
        rid = str(r.get('id', '')).lower()
        if rid in exact:
            st, note = 'In calculator - exact spec', ''
        elif billed is not None and rid not in billed:
            st, note = 'Not on the bill in this period', 'No charge in the billing period (or billed through another resource)'
        elif _nocharge(t):
            st, note = 'No charge of its own', 'Billed through the resources it belongs to'
            if t.endswith('/extensions') and str(r.get('name', '')).split('/')[-1].upper().startswith('MDE.') and rid.split('/extensions/')[0] in exact:
                st, note = 'In calculator - default usage (assumption)', 'Counted in the Microsoft Defender for Servers line'
        elif rid in why and t in HANDLED and not (t == 'microsoft.web/serverfarms' and 'usage based' in why[rid]):
            st, note = 'NOT priced - add manually', why[rid]
        else:
            st, note = 'In calculator - default usage (assumption)', ('Generic line: ' if t not in EXTRA_LABELS else '') + 'cost depends on usage that inventory does not contain'
        rows.append({'subscriptionId': r.get('subscriptionId'), 'resourceGroup': r.get('resourceGroup'), 'name': r.get('name'), 'type': t,
                     'location': r.get('location'), 'status': st, 'note': note, 'id': r.get('id')})
    return rows


def coverage_summary(rows):
    out = {}
    for r in rows:
        out[r['status']] = out.get(r['status'], 0) + 1
    return out


def compute_detail(inventory):
    """One row per VM: what is running and what it is."""
    rows = []
    for r in inventory:
        if str(r.get('type', '')).lower() != 'microsoft.compute/virtualmachines':
            continue
        p = r.get('properties') or {}
        rows.append({'subscriptionId': r.get('subscriptionId'), 'resourceGroup': r.get('resourceGroup'), 'name': r.get('name'),
                     'location': r.get('location'), 'vmSize': _g(p, 'hardwareProfile', 'vmSize'), 'os': _g(p, 'storageProfile', 'osDisk', 'osType'),
                     'powerState': str(_g(p, 'extended', 'instanceView', 'powerState', 'code', default='unknown')).replace('PowerState/', ''),
                     'licenseType': p.get('licenseType'), 'id': r.get('id')})
    return rows


# ----------------------------------------------------------------------------- Excel export
_FORMULA = ('=', '+', '-', '@', '\t', '\r')


def cell(v):
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, str):
        v = ''.join(ch for ch in v if ch >= ' ' or ch in '\n\t')
        if v.startswith(_FORMULA):
            v = "'" + v
        if len(v) > 32000:
            v = v[:32000] + '…[truncated]'
    return v


def write_xlsx(result, path, customer='Customer'):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(write_only=False)
    wb.remove(wb.active)
    head = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', fgColor='1F3A5F')

    def sheet(name, rows, cols):
        ws = wb.create_sheet(name[:31])
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill = head, fill
        for r in rows:
            ws.append([cell(r.get(c)) for c in cols])
        ws.freeze_panes = 'A2'
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(60, max(12, len(c) + 4))
        return ws
    ws = wb.create_sheet('Read Me')
    for line in [f'Azure discovery for: {customer}', f"Directory (tenant): {result['tenant']}", f"Collected (UTC): {result['collected_utc']}",
                 f"Subscriptions: {', '.join(result['subscriptions'])}", f"Cost lookback: {result['days']} days",
                 f"Billing period used for the calculator: {(result.get('bill_window') or {}).get('label', 'n/a')}", '',
                 'Read-only Azure inventory; it is not a complete priced BOQ. Use the Advanced Estimator for prices.',
                 'Actual Cost Management data is historical billing data (discounts, tax, credits and reservations apply); it is not a retail estimate.',
                 'Azure Advisor savings are recommendations, not guaranteed savings; values may overlap, do not add them blindly.',
                 'Missing data can mean missing RBAC permissions (Reader, Cost Management Reader) or API access.',
                 'A shareable Azure Pricing Calculator estimate is created with the Azure Calculator agent panel.']:
        ws.append([line])
    ws['A1'].font = Font(bold=True, size=14)
    ws.column_dimensions['A'].width = 120
    sheet('Workload Summary', result.get('summary', []), ['subscriptionId', 'type', 'count'])
    sheet('Existing Resources', result['inventory'], ['subscriptionId', 'resourceGroup', 'name', 'type', 'location', 'sku', 'kind', 'tags', 'properties', 'id'])
    if result.get('bill_window'):
        its = items_for_month(result['inventory'], result.get('month_cost', []), result.get('month'), result.get('storage_gb'),
                              hours=result['bill_window']['hours'], scale=not result.get('month'))[0]
        cov = coverage(result['inventory'], its, {x['resourceId'] for x in result.get('month_cost', [])})
    else:
        cov = coverage(result['inventory'], to_items(result['inventory'], False, result.get('storage_gb'))[0])
    sheet('Coverage A-Z', sorted(cov, key=lambda c: (c['status'] != 'NOT priced - add manually', c['type'], str(c['name']))), ['status', 'subscriptionId', 'resourceGroup', 'name', 'type', 'location', 'note', 'id'])
    sheet('Compute (VMs)', compute_detail(result['inventory']), ['subscriptionId', 'resourceGroup', 'name', 'location', 'vmSize', 'os', 'powerState', 'licenseType', 'id'])
    sheet('Actual Costs', result['cost'], sorted({k for r in result['cost'] for k in r}) or ['(none returned)'])
    sheet('Storage Usage GB', [{'id': k, 'usedGB': v} for k, v in sorted((result.get('storage_gb') or {}).items())], ['id', 'usedGB'])
    if result.get('month'):
        sheet('Bill ' + month_label(result['month'])[:20], result.get('month_cost', []), ['subscriptionId', 'resourceId', 'meter', 'meterSubCategory', 'quantity', 'cost', 'currency'])
    sheet('Advisor', result['advisor'], ['subscriptionId', 'category', 'impact', 'description', 'solution', 'resourceId', 'estimatedSavingsRaw', 'savingsCurrency', 'recommendationId'])
    sheet('Warnings', [{'warning': f} for f in result['failures']] or [{'warning': 'None'}], ['warning'])
    wb.save(path)


def disk_warning(items):
    big = [i for i in items if i.get('type') == 'disk' and i.get('large')]
    if not big:
        return ''
    tb = sum(i['disk_gb'] for i in big) / 1024
    san = sum(1 for i in big if i.get('san'))
    return (f'{len(big)} disks over 4 TB ({tb:,.0f} TB in total, {san} of them SAN / iSCSI volumes, mostly backup or file servers) are priced '
            'as-is (SAN / iSCSI volumes as Standard HDD, the rest at the chosen tier). Provisioned size, not used space: they can dominate '
            'the cost, so confirm used capacity and consider Azure Files or Blob for backup / file data.')
