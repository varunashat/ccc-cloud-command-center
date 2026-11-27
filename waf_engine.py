"""Azure Well-Architected assessment engine (read-only).

collect()  reads inventory (Resource Graph), Advisor, Defender, backup, policy, cost and 30-day Azure Monitor metrics.
analyze()  turns that evidence into findings under the five WAF pillars, utilisation and right-sizing tables, a landing-zone
           check and an indicative score per pillar. analyze() is pure (no network) so it can be tested with sample data.
Savings are estimates for the last 30 days of actual cost and are labelled as such in the report."""
import concurrent.futures as cf
import datetime as dt
import math
import re
import time
import urllib.parse

from discovery import azure_discovery as az

PILLARS = ['Cost Optimization', 'Reliability', 'Security', 'Operational Excellence', 'Performance Efficiency']
SEV_W = {'High': 12, 'Medium': 6, 'Low': 2}
ADVISOR_PILLAR = {'cost': 'Cost Optimization', 'highavailability': 'Reliability', 'security': 'Security',
                  'operationalexcellence': 'Operational Excellence', 'performance': 'Performance Efficiency'}

MODULES = {
    'finops': 'FinOps & Cost Optimization',
    'infra': 'Azure Infrastructure & Landing Zone',
    'security': 'Cloud Security',
    'waf': 'WAF & Network Security',
    'modern': 'Application & Database Modernization',
    'backup': 'Backup, DR & Resilience',
    'perfops': 'Performance & Operations',
}
# which evidence each module needs (inventory is always read)
MODULE_SOURCES = {'finops': {'cost', 'advisor', 'metrics', 'vmsizes'}, 'infra': {'policy', 'mgmt'}, 'security': {'defender', 'plans', 'advisor', 'rbac'}, 'waf': set(),
                  'modern': {'metrics', 'vmsizes'}, 'backup': {'backup', 'advisor'}, 'perfops': {'metrics', 'vmsizes', 'advisor'}}
SOURCE_LABEL = {'inventory': 'Resource Graph inventory', 'advisor': 'Azure Advisor', 'defender': 'Defender for Cloud assessments', 'plans': 'Defender plans', 'backup': 'Azure Backup coverage',
                'policy': 'Azure Policy assignments', 'rbac': 'Azure RBAC role assignments', 'mgmt': 'Management groups', 'cost': 'Cost Management (30 days)', 'vmsizes': 'VM size catalogue', 'metrics': 'Azure Monitor metrics (30 days)'}
ROLE_NEEDED = {'inventory': 'Reader', 'advisor': 'Reader', 'defender': 'Security Reader', 'plans': 'Security Reader', 'backup': 'Reader', 'policy': 'Reader', 'rbac': 'Reader',
               'mgmt': 'Reader at tenant root / management group', 'cost': 'Cost Management Reader', 'vmsizes': 'Reader', 'metrics': 'Monitoring Reader (or Reader)'}


def module_of(f):
    t, a, p = f['title'].lower(), f['area'], f['pillar']
    if 'web application firewall' in t or 'waf' in t or 'application gateway' in t or 'front door' in t:
        return 'waf'
    if p == 'Cost Optimization':
        return 'finops'
    if a == 'Modernisation':
        return 'modern'
    if a in ('Backup and recovery', 'Data resilience', 'High availability'):
        return 'backup'
    if p == 'Performance Efficiency' or a == 'Monitoring':
        return 'perfops'
    if p == 'Security':
        return 'security'
    return 'infra'


# ----------------------------------------------------------------------------- queries
Q_INVENTORY = ("Resources | project id, name, type, location, resourceGroup, subscriptionId, kind, sku, zones, tags, "
               "identity, properties")
Q_ADVISOR = "AdvisorResources | where type =~ 'microsoft.advisor/recommendations' | project subscriptionId, id, properties"
Q_DEFENDER = ("SecurityResources | where type =~ 'microsoft.security/assessments' "
              "| where tostring(properties.status.code) =~ 'Unhealthy' "
              "| project id, subscriptionId, name=tostring(properties.displayName), severity=tostring(properties.metadata.severity), "
              "resource=tostring(properties.resourceDetails.Id)")
Q_BACKUP = ("RecoveryServicesResources | where type =~ 'microsoft.recoveryservices/vaults/backupfabrics/protectioncontainers/protecteditems' "
            "| project source=tostring(properties.sourceResourceId)")
Q_RBAC = ("AuthorizationResources | where type =~ 'microsoft.authorization/roleassignments' "
          "| project subscriptionId, principalType=tostring(properties.principalType), role=tostring(properties.roleDefinitionId), scope=tostring(properties.scope), principal=tostring(properties.principalId)")
Q_POLICY = ("PolicyResources | where type =~ 'microsoft.authorization/policyassignments' "
            "| project subscriptionId, name=tostring(properties.displayName), scope=tostring(properties.scope)")

METRIC_PLAN = {
    'microsoft.compute/virtualmachines': [('Percentage CPU', 'cpu'), ('Available Memory Bytes', 'mem_free')],
    'microsoft.web/serverfarms': [('CpuPercentage', 'cpu'), ('MemoryPercentage', 'mem')],
    'microsoft.sql/servers/databases': [('cpu_percent', 'cpu'), ('dtu_consumption_percent', 'dtu'), ('storage_percent', 'storage')],
    'microsoft.cache/redis': [('percentProcessorTime', 'cpu'), ('usedmemorypercentage', 'mem')],
    'microsoft.storage/storageaccounts': [('Transactions', 'tx')],
}
METRIC_CAP = {'microsoft.compute/virtualmachines': 250, 'microsoft.web/serverfarms': 120, 'microsoft.sql/servers/databases': 120,
              'microsoft.cache/redis': 40, 'microsoft.storage/storageaccounts': 150}


def g(d, path, default=None):
    """Safe nested get: g(vm, 'properties.hardwareProfile.vmSize')."""
    cur = d
    for part in path.split('.'):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return default
        if cur is None:
            return default
    return cur


def low(x):
    return str(x or '').lower()


# ----------------------------------------------------------------------------- collection
def _p95(vals):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(math.ceil(0.95 * len(s))) - 1)]


def read_metrics(call, tenant, rid, rtype, now, days=30):
    """{key: {avg, max, p95}} for one resource. Each metric is tried on its own so an unsupported one is skipped."""
    out = {}
    span = az.iso_z(now - dt.timedelta(days=days)) + '/' + az.iso_z(now)
    for name, key in METRIC_PLAN.get(rtype, []):
        agg = 'Total' if key == 'tx' else 'Average,Maximum'
        url = (f"{az.ARM}{rid}/providers/microsoft.insights/metrics?api-version=2018-01-01&metricnames={urllib.parse.quote(name)}"
               f"&aggregation={agg}&interval=P1D&timespan={span}")
        try:
            series = ((call('GET', url, None, tenant).get('value') or [{}])[0].get('timeseries') or [{}])[0].get('data') or []
        except az.DiscoveryError:
            continue
        if key == 'tx':
            vals = [d.get('total') for d in series if d.get('total') is not None]
            if series:
                out[key] = {'sum': sum(vals), 'days': len(vals)}
            continue
        avgs = [d['average'] for d in series if d.get('average') is not None]
        maxs = [d['maximum'] for d in series if d.get('maximum') is not None]
        if avgs:
            out[key] = {'avg': round(sum(avgs) / len(avgs), 2), 'max': round(max(maxs), 2) if maxs else None,
                        'p95': round(_p95(maxs), 2) if maxs else None, 'days': len(avgs)}
    return out


def vm_size_table(call, tenant, inv):
    """{(sub, location, size lower): (cores, memory GB)} from the Compute vmSizes API, one call per subscription and region."""
    seen, out = set(), {}
    for r in inv:
        if low(r.get('type')) != 'microsoft.compute/virtualmachines':
            continue
        key = (r.get('subscriptionId'), low(r.get('location')))
        if key in seen:
            continue
        seen.add(key)
        try:
            data = call('GET', f"{az.ARM}/subscriptions/{key[0]}/providers/Microsoft.Compute/locations/{key[1]}/vmSizes?api-version=2024-03-01", None, tenant)
        except az.DiscoveryError:
            continue
        for s in data.get('value') or []:
            out[(key[0], key[1], low(s.get('name')))] = (s.get('numberOfCores'), round((s.get('memoryInMB') or 0) / 1024, 1))
    return out


def verify(call, tenant, subs):
    """Quick access check, one small call per evidence source. Returns [{source, ok, detail, role}]."""
    out = []
    first = subs[0]

    def t(key, fn):
        try:
            fn()
            out.append({'source': SOURCE_LABEL[key], 'ok': True, 'detail': 'Readable', 'role': ROLE_NEEDED[key]})
        except az.DiscoveryError as e:
            out.append({'source': SOURCE_LABEL[key], 'ok': False, 'detail': str(e)[:200], 'role': ROLE_NEEDED[key]})
    t('inventory', lambda: az.rg_query(call, subs, 'Resources | take 1 | project id', tenant))
    t('advisor', lambda: az.rg_query(call, subs, 'AdvisorResources | take 1 | project id', tenant))
    t('defender', lambda: az.rg_query(call, subs, 'SecurityResources | take 1 | project id', tenant))
    t('plans', lambda: call('GET', f'{az.ARM}/subscriptions/{first}/providers/Microsoft.Security/pricings?api-version=2023-01-01', None, tenant))
    t('backup', lambda: az.rg_query(call, subs, 'RecoveryServicesResources | take 1 | project id', tenant))
    t('rbac', lambda: az.rg_query(call, subs, 'AuthorizationResources | take 1 | project id', tenant))
    t('policy', lambda: az.rg_query(call, subs, 'PolicyResources | take 1 | project id', tenant))
    t('mgmt', lambda: call('GET', f'{az.ARM}/providers/Microsoft.Management/managementGroups?api-version=2021-04-01', None, tenant))

    def cm():
        now = dt.datetime.now(dt.timezone.utc)
        body = {'type': 'ActualCost', 'timeframe': 'Custom', 'timePeriod': {'from': az.iso_z(now - dt.timedelta(days=2)), 'to': az.iso_z(now)},
                'dataset': {'granularity': 'None', 'aggregation': {'c': {'name': 'Cost', 'function': 'Sum'}}}}
        call('POST', f'{az.ARM}/subscriptions/{first}/providers/Microsoft.CostManagement/query?api-version=2023-03-01', body, tenant)
    t('cost', cm)

    def mon():
        rows = az.rg_query(call, [first], "Resources | where type =~ 'microsoft.compute/virtualmachines' | take 1 | project id", tenant)
        if not rows:
            return
        now = dt.datetime.now(dt.timezone.utc)
        call('GET', f"{az.ARM}{rows[0]['id']}/providers/microsoft.insights/metrics?api-version=2018-01-01&metricnames=Percentage%20CPU&interval=PT1H&timespan={az.iso_z(now - dt.timedelta(hours=3))}/{az.iso_z(now)}", None, tenant)
    t('metrics', mon)
    return out


def collect(call, tenant, subs, progress=lambda m: None, budget_s=420, now=None, sources=None):
    """sources: set of evidence names to read (None = everything). Inventory is always read."""
    now = now or dt.datetime.now(dt.timezone.utc)
    want = (lambda k: True) if sources is None else (lambda k: k == 'inventory' or k in sources)
    ev = {'warnings': [], 'now': now.isoformat(timespec='seconds'), 'source_status': {}}

    def part(key, label, fn, default):
        if not want(key):
            ev['source_status'][key] = 'skipped'
            return default
        progress(label)
        try:
            r = fn()
            ev['source_status'].setdefault(key, 'ok')
            return r
        except az.DiscoveryError as e:
            ev['warnings'].append(f'{SOURCE_LABEL.get(key, key)}: {e}')
            ev['source_status'][key] = 'failed'
            return default
    ev['inventory'] = part('inventory', 'Reading every resource (Resource Graph)…', lambda: az.rg_query(call, subs, Q_INVENTORY, tenant), [])
    ev['advisor'] = az.parse_advisor(part('advisor', 'Reading Azure Advisor…', lambda: az.rg_query(call, subs, Q_ADVISOR, tenant), []))
    ev['defender'] = part('defender', 'Reading Defender for Cloud assessments…', lambda: az.rg_query(call, subs, Q_DEFENDER, tenant), [])
    ev['backup'] = {low(r.get('source')) for r in part('backup', 'Reading backup coverage…', lambda: az.rg_query(call, subs, Q_BACKUP, tenant), [])}
    ev['policy'] = part('policy', 'Reading policy assignments…', lambda: az.rg_query(call, subs, Q_POLICY, tenant), [])
    ev['rbac'] = part('rbac', 'Reading RBAC role assignments…', lambda: az.rg_query(call, subs, Q_RBAC, tenant), [])
    plans = {}
    for s_ in subs:
        r = part('plans', 'Reading Defender plans…', lambda s_=s_: call('GET', f'{az.ARM}/subscriptions/{s_}/providers/Microsoft.Security/pricings?api-version=2023-01-01', None, tenant), {})
        if r:
            plans[s_] = {x.get('name'): g(x, 'properties.pricingTier') for x in (r.get('value') or [])}
    ev['defender_plans'] = plans
    mg = part('mgmt', 'Reading management groups…', lambda: call('GET', f'{az.ARM}/providers/Microsoft.Management/managementGroups?api-version=2021-04-01', None, tenant), {})
    ev['mgmt_groups'] = [x.get('name') for x in (mg.get('value') or [])]
    # cost per resource, last 30 days
    cost, total, by_service = {}, 0.0, {}
    cur = ''
    ev['source_status']['cost'] = 'ok' if want('cost') else 'skipped'
    for s in (subs if want('cost') else []):
        progress('Reading 30-day cost (Cost Management)…')
        try:
            url = f'{az.ARM}/subscriptions/{s}/providers/Microsoft.CostManagement/query?api-version=2023-03-01'
            body = {'type': 'ActualCost', 'timeframe': 'Custom', 'timePeriod': {'from': az.iso_z(now - dt.timedelta(days=30)), 'to': az.iso_z(now)},
                    'dataset': {'granularity': 'None', 'aggregation': {'c': {'name': 'Cost', 'function': 'Sum'}},
                                'grouping': [{'type': 'Dimension', 'name': 'ResourceId'}]}}
            for r in az._cm_rows(call, url, body, tenant):
                c = float(r.get('Cost') or r.get('PreTaxCost') or 0)
                rid = low(r.get('ResourceId'))
                if rid:
                    cost[rid] = cost.get(rid, 0) + c
                total += c
            for r in az.cost_query(call, s, 30, tenant):
                by_service[r.get('ServiceName') or 'Other'] = by_service.get(r.get('ServiceName') or 'Other', 0) + float(r.get('Cost') or r.get('PreTaxCost') or 0)
                cur = r.get('Currency') or cur
        except az.DiscoveryError as e:
            ev['warnings'].append(f'Cost for {s[:8]}…: {e}')
            ev['source_status']['cost'] = 'failed'
    ev['cost'], ev['cost_total'], ev['cost_by_service'], ev['currency'] = cost, round(total, 2), by_service, cur or 'USD'
    progress('Reading VM sizes…')
    ev['vm_sizes'] = vm_size_table(call, tenant, ev['inventory']) if want('vmsizes') else {}
    ev['source_status']['vmsizes'] = 'ok' if want('vmsizes') else 'skipped'
    # metrics
    deadline = time.time() + budget_s
    todo = []
    for rtype, cap in (METRIC_CAP.items() if want('metrics') else []):
        rows = [r for r in ev['inventory'] if low(r.get('type')) == rtype and low(r.get('name')) != 'master' and r.get('id')]
        if rtype == 'microsoft.compute/virtualmachines':
            rows.sort(key=lambda r: -cost.get(low(r['id']), 0))
        todo += [(rtype, r) for r in rows[:cap]]
    ev['metrics'], done = {}, [0]

    def work(item):
        rtype, r = item
        if time.time() > deadline:
            return None
        return low(r['id']), read_metrics(call, tenant, r['id'], rtype, now)
    ev['metrics_planned'] = len(todo)
    progress(f'Reading 30-day usage metrics for {len(todo)} resources…')
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(work, todo):
            done[0] += 1
            if done[0] % 20 == 0:
                progress(f'Usage metrics {done[0]} of {len(todo)}…')
            if res and res[1]:
                ev['metrics'][res[0]] = res[1]
    ev['source_status']['metrics'] = ('skipped' if not want('metrics') else ('ok' if ev['metrics'] or not todo else 'failed'))
    if len(ev['metrics']) < len(todo) and want('metrics'):
        ev['warnings'].append(f'Usage metrics were read for {len(ev["metrics"])} of {len(todo)} resources (the rest had no data, no permission, or the time budget ended).')
    return ev


# ----------------------------------------------------------------------------- analysis helpers
def _rg(r):
    return str(r.get('resourceGroup') or '')


def _item(r, detail='', saving=0.0):
    return {'name': r.get('name') or r.get('id', ''), 'type': str(r.get('type', '')).split('/')[-1], 'rg': _rg(r),
            'sub': str(r.get('subscriptionId', ''))[:8], 'location': r.get('location', ''), 'detail': detail, 'saving': round(saving, 2),
            'id': r.get('id', '')}


class Report:
    def __init__(self):
        self.findings = []

    def add(self, pillar, area, sev, title, items, rec, saving_est=False, basis='', total_pop=None, source='Rule'):
        if not items:
            return
        saving = round(sum(i.get('saving', 0) for i in items), 2)
        self.findings.append({'pillar': pillar, 'area': area, 'severity': sev, 'title': title, 'count': len(items), 'population': total_pop,
                              'saving': saving, 'saving_is_estimate': saving_est, 'basis': basis, 'recommendation': rec,
                              'items': items[:500], 'source': source})


def vm_step_down(size):
    """Standard_D8s_v5 -> Standard_D4s_v5 (halve the vCPU number inside the name). None if it cannot be derived."""
    m = re.match(r'^(Standard_[A-Za-z]+)(\d+)(.*)$', size or '')
    if not m or int(m.group(2)) < 2 or m.group(1).startswith('Standard_B'):
        return None
    n = int(m.group(2))
    return f'{m.group(1)}{n // 2 if n > 2 else 1}{m.group(3)}' if n // 2 >= 1 else None


def vm_generation(size):
    m = re.search(r'_v(\d+)$', size or '')
    return int(m.group(1)) if m else 1


OPEN_PORTS = {'22': 'SSH', '3389': 'RDP', '445': 'SMB', '1433': 'SQL', '3306': 'MySQL', '5432': 'PostgreSQL', '23': 'Telnet', '5985': 'WinRM', '5986': 'WinRM', '21': 'FTP'}
ANY_SRC = {'*', 'internet', 'any', '0.0.0.0/0', '<nw>/0'}
NO_NSG_SUBNETS = {'gatewaysubnet', 'azurefirewallsubnet', 'azurefirewallmanagementsubnet', 'routeserversubnet'}
DEV_WORDS = ('dev', 'test', 'uat', 'qa', 'poc', 'sandbox', 'staging', 'demo', 'lab')
EOL_OS = [('windowsserver', ('2008', '2012')), ('ubuntuserver', ('14.04', '16.04', '18.04')), ('centos', ('6', '7', '8')), ('rhel', ('6', '7'))]


def _ports_open(rule):
    p = rule.get('properties') or {}
    rng = [str(x) for x in ([p.get('destinationPortRange')] if p.get('destinationPortRange') else []) + list(p.get('destinationPortRanges') or [])]
    hit = set()
    for r in rng:
        if r in ('*', 'any'):
            hit.add('ANY')
        elif '-' in r:
            try:
                a, b = (int(x) for x in r.split('-', 1))
                hit |= {OPEN_PORTS[k] for k in OPEN_PORTS if a <= int(k) <= b}
            except ValueError:
                pass
        elif r in OPEN_PORTS:
            hit.add(OPEN_PORTS[r])
    return hit


def _env_of(r):
    tags = {low(k): low(v) for k, v in (r.get('tags') or {}).items()}
    text = ' '.join([tags.get('environment', ''), tags.get('env', ''), tags.get('stage', ''), low(r.get('name')), low(_rg(r))])
    return next((w for w in DEV_WORDS if re.search(r'(^|[^a-z])' + w + r'([^a-z]|$)', text)), '')


# ----------------------------------------------------------------------------- analysis
def analyze(ev, modules=None):
    inv = ev['inventory']
    cost = ev.get('cost', {})
    metrics = ev.get('metrics', {})
    R = Report()
    by_type = {}
    for r in inv:
        by_type.setdefault(low(r.get('type')), []).append(r)
    T = lambda t: by_type.get(t, [])
    c = lambda r: cost.get(low(r.get('id')), 0.0)
    vms, disks, nics, pips = T('microsoft.compute/virtualmachines'), T('microsoft.compute/disks'), T('microsoft.network/networkinterfaces'), T('microsoft.network/publicipaddresses')
    nsgs, vnets, stor, kvs = T('microsoft.network/networksecuritygroups'), T('microsoft.network/virtualnetworks'), T('microsoft.storage/storageaccounts'), T('microsoft.keyvault/vaults')
    ext_by_vm = {}
    for r in T('microsoft.compute/virtualmachines/extensions'):
        ext_by_vm.setdefault(low(r['id']).split('/extensions/')[0], set()).add(low(r.get('name')))
    util, rightsize = [], []

    # ---------- VM utilisation & right-sizing (Cost + Performance)
    down, up, idle, spend_flat = [], [], [], []
    for v in vms:
        rid = low(v['id'])
        size = g(v, 'properties.hardwareProfile.vmSize', '')
        power = low(g(v, 'properties.extended.instanceView.powerState.code', '')).replace('powerstate/', '')
        m = metrics.get(rid, {})
        cores, mem_gb = ev.get('vm_sizes', {}).get((v.get('subscriptionId'), low(v.get('location')), low(size)), (None, None))
        cpu, mf = m.get('cpu'), m.get('mem_free')
        mem_used = None
        if mf and mem_gb:
            mem_used = round(max(0.0, 100 - (mf['avg'] / (mem_gb * 1024 ** 3)) * 100), 1)
        row = {'name': v.get('name'), 'rg': _rg(v), 'size': size, 'vcpu': cores, 'memory_gb': mem_gb, 'power': power or 'unknown',
               'cpu_avg': cpu and cpu['avg'], 'cpu_max': cpu and cpu['max'], 'cpu_p95': cpu and cpu['p95'], 'mem_used_avg': mem_used,
               'cost_30d': round(c(v), 2), 'verdict': 'No metrics', 'target': ''}
        if cpu:
            tgt = vm_step_down(size)
            mem_ok = mem_used is None or mem_used < 50
            if cpu['avg'] < 10 and (cpu['p95'] or 0) < 40 and mem_ok and power != 'deallocated':
                row['verdict'], row['target'] = ('Scale down', tgt or '')
                down.append(_item(v, f"CPU avg {cpu['avg']}%, peak {cpu['max']}%" + (f", memory ~{mem_used}% used" if mem_used is not None else '') + (f'. Try {tgt}.' if tgt else ''), c(v) * 0.5 if tgt else 0))
            elif cpu['avg'] > 75 or (cpu['p95'] or 0) > 95 or (mem_used or 0) > 90:
                row['verdict'] = 'Scale up / out'
                up.append(_item(v, f"CPU avg {cpu['avg']}%, p95 of daily peaks {cpu['p95']}%" + (f", memory ~{mem_used}% used" if mem_used is not None else '')))
            else:
                row['verdict'] = 'Right-sized'
        if power in ('deallocated', 'stopped'):
            row['verdict'] = 'Stopped'
        util.append(row)
        if power == 'stopped':
            idle.append(_item(v, 'Stopped but not deallocated: Azure still bills compute.', c(v)))
    R.add('Cost Optimization', 'Right-sizing', 'High' if sum(i['saving'] for i in down) > 500 else 'Medium', 'Virtual machines using under 10% CPU (scale down)', down,
          'Move to the next smaller size in the same series (about half the vCPU), or to a burstable B-series. Resize in a maintenance window and watch memory and disk IOPS for a week.',
          True, 'Estimated at 50% of the VM compute cost of the last 30 days for one size step down.', len(vms))
    R.add('Performance Efficiency', 'Right-sizing', 'Medium', 'Virtual machines under sustained CPU or memory pressure (scale up or out)', up,
          'Scale up to the next size, or scale out behind a load balancer or a scale set. Check the application before resizing.', False, '', len(vms))
    R.add('Cost Optimization', 'Idle resources', 'Medium', 'VMs stopped but not deallocated (still billed)', idle, 'Deallocate them, or delete them and keep a snapshot if they are not needed.', True,
          'Saving = last 30 days of compute cost.', len(vms))

    # ---------- Cost: licences, commitments, dev/test, orphans
    ahub = [_item(v, 'Windows VM without Azure Hybrid Benefit.', c(v) * 0.30) for v in vms
            if low(g(v, 'properties.storageProfile.osDisk.osType')) == 'windows' and not g(v, 'properties.licenseType')]
    R.add('Cost Optimization', 'Licensing', 'Medium', 'Windows VMs not using Azure Hybrid Benefit', ahub,
          'If the customer owns Windows Server licences with Software Assurance (or Windows Server subscriptions), enable Azure Hybrid Benefit. Confirm entitlement first.', True,
          'Indicative 30% of VM cost; the exact saving depends on VM size, so confirm in the Azure Pricing Calculator.', len(vms))
    sqlvm = [v for v in vms if str(g(v, 'properties.storageProfile.imageReference.offer', '')).lower().startswith('sql')]
    R.add('Cost Optimization', 'Licensing', 'Medium', 'SQL Server on VMs: check Hybrid Benefit for SQL', [_item(v, 'Image: ' + str(g(v, 'properties.storageProfile.imageReference.offer'))) for v in sqlvm
          if low(g(v, 'properties.licenseType')) not in ('windows_server', 'sql_server')], 'Confirm SQL licence ownership and enable the SQL Hybrid Benefit on the SQL virtual machine resource.', False, '', len(sqlvm))
    run24 = []
    for v in vms:
        env = _env_of(v)
        if env and low(g(v, 'properties.extended.instanceView.powerState.code', '')).endswith('running'):
            run24.append(_item(v, f'Looks like a {env} workload running all day.', c(v) * 0.60))
    R.add('Cost Optimization', 'Scheduling', 'Medium', 'Dev/test VMs running 24 x 7', run24,
          'Use auto-shutdown or Start/Stop automation (for example 08:00 to 20:00 on weekdays).', True,
          'Estimated at 60% of cost when run about 12 hours on weekdays only. Detected from tags and names, so confirm each one.', len(vms))
    steady = [_item(v, f"Running continuously, CPU avg {metrics[low(v['id'])]['cpu']['avg']}%.", c(v) * 0.25) for v in vms
              if low(v['id']) in metrics and 'cpu' in metrics[low(v['id'])] and low(g(v, 'properties.extended.instanceView.powerState.code', '')).endswith('running')
              and not _env_of(v) and metrics[low(v['id'])]['cpu']['days'] >= 25 and c(v) > 0 and low(g(v, 'properties.priority')) != 'spot']
    R.add('Cost Optimization', 'Commitments', 'Medium', 'Steady always-on VMs: candidates for Reserved Instances or a Savings Plan', steady,
          'Right-size first, then buy a 1 or 3 year reservation or an Azure Savings Plan for compute. Reservations typically save well over pay-as-you-go; the real rate depends on SKU and term.', True,
          'Indicative 25% of current VM cost after right-sizing; confirm in the Azure Pricing Calculator.', len(vms))
    unatt = [_item(d, f"{g(d, 'properties.diskSizeGB', '?')} GB {g(d, 'sku.name', '')} not attached to any VM.", c(d)) for d in disks if low(g(d, 'properties.diskState')) == 'unattached' and not g(d, 'managedBy')]
    R.add('Cost Optimization', 'Orphaned resources', 'Medium', 'Unattached managed disks', unatt, 'Snapshot if needed, then delete.', False, 'Saving = actual last 30 days of cost.', len(disks))
    oldsnap = []
    now = dt.datetime.fromisoformat(ev['now'].replace('Z', '+00:00')) if ev.get('now') else dt.datetime.now(dt.timezone.utc)
    for s in T('microsoft.compute/snapshots'):
        t = g(s, 'properties.timeCreated')
        try:
            age = (now - dt.datetime.fromisoformat(str(t).replace('Z', '+00:00'))).days
        except (ValueError, TypeError):
            continue
        if age > 90:
            oldsnap.append(_item(s, f'{age} days old, {g(s, "properties.diskSizeGB", "?")} GB.', c(s)))
    R.add('Cost Optimization', 'Orphaned resources', 'Low', 'Snapshots older than 90 days', oldsnap, 'Keep only snapshots covered by a retention policy; move long-term copies to Azure Backup.', False, '', len(T('microsoft.compute/snapshots')))
    free_pip = [_item(p, f"{g(p, 'sku.name', '')} {g(p, 'properties.publicIPAllocationMethod', '')} public IP with no resource attached.", c(p))
                for p in pips if not g(p, 'properties.ipConfiguration') and not g(p, 'properties.natGateway')]
    R.add('Cost Optimization', 'Orphaned resources', 'Low', 'Public IP addresses not attached to anything', free_pip, 'Release them.', False, '', len(pips))
    free_nic = [_item(n, 'Network interface not attached to a VM or private endpoint.') for n in nics if not g(n, 'properties.virtualMachine.id') and not g(n, 'properties.privateEndpoint.id')]
    R.add('Cost Optimization', 'Orphaned resources', 'Low', 'Network interfaces not attached', free_nic, 'Delete unused network interfaces to keep the estate tidy.', False, '', len(nics))
    empty_lb = [_item(lb, f"{g(lb, 'sku.name', '')} load balancer with no backend members.", c(lb)) for lb in T('microsoft.network/loadbalancers')
                if not any(g(p, 'properties.backendIPConfigurations') or g(p, 'properties.loadBalancerBackendAddresses') for p in (g(lb, 'properties.backendAddressPools', []) or []))]
    R.add('Cost Optimization', 'Orphaned resources', 'Medium', 'Load balancers with no backend', empty_lb, 'Remove them if not planned for use.', False, '', len(T('microsoft.network/loadbalancers')))
    # app service plans
    plans_idle, plans_low, plans_hot = [], [], []
    sites_by_plan = {}
    for s in T('microsoft.web/sites'):
        sites_by_plan.setdefault(low(g(s, 'properties.serverFarmId')), []).append(s)
    for p in T('microsoft.web/serverfarms'):
        n = int(g(p, 'sku.capacity', 1) or 1)
        tier = g(p, 'sku.tier', '')
        apps = len(sites_by_plan.get(low(p['id']), []))
        m = metrics.get(low(p['id']), {})
        cpu, mem = m.get('cpu'), m.get('mem')
        if apps == 0 and tier not in ('Free', 'Shared', 'Dynamic', 'ElasticPremium'):
            plans_idle.append(_item(p, f'{tier} plan x{n} hosts no apps.', c(p)))
        elif cpu and mem and cpu['avg'] < 15 and mem['avg'] < 50 and (cpu['max'] or 0) < 50:
            plans_low.append(_item(p, f"{tier} x{n}: CPU avg {cpu['avg']}%, memory avg {mem['avg']}%." + (' Reduce instances.' if n > 1 else ' Move to a smaller tier.'), c(p) * ((n - 1) / n if n > 1 else 0.3)))
        elif cpu and (cpu['avg'] > 70 or (mem and mem['avg'] > 85)):
            plans_hot.append(_item(p, f"{tier} x{n}: CPU avg {cpu['avg']}%" + (f", memory avg {mem['avg']}%" if mem else '') + '.'))
        util.append({'name': p.get('name'), 'rg': _rg(p), 'size': f'{tier} x{n}', 'power': 'plan', 'cpu_avg': cpu and cpu['avg'], 'cpu_max': cpu and cpu['max'], 'cpu_p95': cpu and cpu['p95'],
                     'mem_used_avg': mem and mem['avg'], 'cost_30d': round(c(p), 2), 'verdict': 'Scale down' if any(i['id'] == p['id'] for i in plans_low) else ('Scale up / out' if any(i['id'] == p['id'] for i in plans_hot) else ('Empty' if any(i['id'] == p['id'] for i in plans_idle) else ('Right-sized' if cpu else 'No metrics'))), 'target': ''})
    R.add('Cost Optimization', 'Idle resources', 'Medium', 'App Service plans with no apps', plans_idle, 'Delete the plan, or move apps onto it before deleting another.', False, '', len(T('microsoft.web/serverfarms')))
    R.add('Cost Optimization', 'Right-sizing', 'Medium', 'App Service plans with low CPU and memory', plans_low, 'Scale in the instance count or move to a smaller tier; add autoscale rules.', True, 'Estimate: removed instances share of the plan cost, or 30% for one tier down.', len(T('microsoft.web/serverfarms')))
    R.add('Performance Efficiency', 'Right-sizing', 'Medium', 'App Service plans under load', plans_hot, 'Scale up or out and enable autoscale on CPU.', False, '', len(T('microsoft.web/serverfarms')))
    # SQL databases
    sql_low, sql_hot = [], []
    for d in T('microsoft.sql/servers/databases'):
        if low(d.get('name')) == 'master':
            continue
        m = metrics.get(low(d['id']), {})
        key = 'dtu' if 'dtu' in m else 'cpu'
        mm = m.get(key)
        sku = g(d, 'sku.name', '')
        if mm:
            util.append({'name': d.get('name'), 'rg': _rg(d), 'size': f"{sku} {g(d, 'sku.tier', '')}", 'power': 'database', 'cpu_avg': mm['avg'], 'cpu_max': mm['max'], 'cpu_p95': mm['p95'],
                         'mem_used_avg': None, 'cost_30d': round(c(d), 2), 'verdict': 'Scale down' if mm['avg'] < 15 and (mm['max'] or 0) < 50 else ('Scale up / out' if mm['avg'] > 75 else 'Right-sized'), 'target': ''})
            if mm['avg'] < 15 and (mm['max'] or 0) < 50 and c(d) > 20:
                sql_low.append(_item(d, f"{sku}: {key.upper()} avg {mm['avg']}%, peak {mm['max']}%.", c(d) * 0.4))
            elif mm['avg'] > 75:
                sql_hot.append(_item(d, f"{sku}: {key.upper()} avg {mm['avg']}%."))
    R.add('Cost Optimization', 'Right-sizing', 'Medium', 'SQL databases with low utilisation', sql_low, 'Move to a smaller vCore/DTU size, or to serverless with auto-pause for intermittent workloads.', True, 'Estimated at 40% of database cost.', len(T('microsoft.sql/servers/databases')))
    R.add('Performance Efficiency', 'Right-sizing', 'Medium', 'SQL databases near their compute limit', sql_hot, 'Scale up the service objective and check for missing indexes (Query Performance Insight).', False, '', len(T('microsoft.sql/servers/databases')))
    cold = []
    for a in stor:
        m = metrics.get(low(a['id']), {}).get('tx')
        if m and m['sum'] == 0 and m['days'] >= 25:
            cold.append(_item(a, 'No transactions in the last 30 days.', c(a)))
    R.add('Cost Optimization', 'Idle resources', 'Low', 'Storage accounts with no activity for 30 days', cold, 'Confirm with the owner, then delete, or move blobs to the Cool or Archive tier.', False, '', len(stor))
    adv_pillar = {}
    for a in ev.get('advisor', []):
        pil = ADVISOR_PILLAR.get(low(a.get('category')))
        if not pil:
            continue
        adv_pillar.setdefault((pil, a.get('description') or 'Advisor recommendation'), []).append(a)
    for (pil, title), rows in adv_pillar.items():
        its = []
        for a in rows[:300]:
            rid = str(a.get('resourceId') or '')
            its.append({'name': rid.split('/')[-1] or '(subscription)', 'type': (rid.split('/')[-2] if rid.count('/') > 6 else ''), 'rg': (rid.split('/resourceGroups/')[1].split('/')[0] if '/resourceGroups/' in rid else ''),
                        'sub': str(a.get('subscriptionId', ''))[:8], 'location': '', 'detail': a.get('solution') or '', 'saving': 0, 'id': rid})
        sev = {'High': 'High', 'Medium': 'Medium'}.get(rows[0].get('impact'), 'Low')
        sav = 0.0
        for a in rows:
            try:
                sav += float(a.get('estimatedSavingsRaw') or 0)
            except (TypeError, ValueError):
                pass
        R.add(pil, 'Azure Advisor', sev, title, its, rows[0].get('solution') or 'See the Azure Advisor recommendation.', False, '', None, source='Azure Advisor')
        if sav and pil == 'Cost Optimization':
            R.findings[-1]['saving'] = round(sav / 12 if sav > 0 else 0, 2)
            R.findings[-1]['saving_is_estimate'] = True
            R.findings[-1]['basis'] = 'Advisor annual savings estimate divided by 12.'

    # ---------- Security
    open_mgmt = []
    for n in nsgs:
        for rule in g(n, 'properties.securityRules', []) or []:
            p = rule.get('properties') or {}
            if low(p.get('direction')) != 'inbound' or low(p.get('access')) != 'allow':
                continue
            srcs = [low(p.get('sourceAddressPrefix'))] + [low(x) for x in (p.get('sourceAddressPrefixes') or [])]
            if not any(s in ANY_SRC for s in srcs):
                continue
            hit = _ports_open(rule)
            if hit:
                open_mgmt.append(_item(n, f"Rule '{rule.get('name')}' allows {', '.join(sorted(hit))} from the Internet / any source."))
    R.add('Security', 'Network', 'High', 'NSG rules open management or database ports to the Internet', open_mgmt,
          'Restrict the source to known IP ranges, use Azure Bastion or Just-in-Time VM access, and remove "Any" rules.', False, '', len(nsgs))
    no_nsg_sub, bad_peer, overlap = [], [], []
    seen_prefix = {}
    for vn in vnets:
        for s in g(vn, 'properties.subnets', []) or []:
            if low(s.get('name')) in NO_NSG_SUBNETS:
                continue
            if not g(s, 'properties.networkSecurityGroup.id'):
                no_nsg_sub.append(_item(vn, f"Subnet '{s.get('name')}' ({g(s, 'properties.addressPrefix', '')}) has no network security group."))
        for pr in g(vn, 'properties.virtualNetworkPeerings', []) or []:
            if g(pr, 'properties.peeringState') != 'Connected':
                bad_peer.append(_item(vn, f"Peering '{pr.get('name')}' is {g(pr, 'properties.peeringState')}."))
        for a in g(vn, 'properties.addressSpace.addressPrefixes', []) or []:
            seen_prefix.setdefault(a, []).append(vn)
    for a, vs in seen_prefix.items():
        if len(vs) > 1:
            overlap += [_item(v, f'Address space {a} is also used by {len(vs) - 1} other virtual network(s).') for v in vs]
    R.add('Security', 'Network', 'High', 'Subnets without a network security group (no NSG)', no_nsg_sub, 'Attach an NSG to every workload subnet and deny by default. Gateway and firewall subnets are excluded.', False, '', len(vnets))
    R.add('Reliability', 'Network', 'Medium', 'Virtual network peerings that are not connected', bad_peer, 'Recreate the peering from both sides.', False, '', len(vnets))
    R.add('Operational Excellence', 'Network', 'Medium', 'Overlapping virtual network address spaces', overlap, 'Overlapping ranges block peering and VPN. Plan non-overlapping ranges (IP address management).', False, '', len(vnets))
    pip_vm = []
    for n in nics:
        if g(n, 'properties.virtualMachine.id') and any(g(ic, 'properties.publicIPAddress.id') for ic in (g(n, 'properties.ipConfigurations', []) or [])):
            pip_vm.append(_item(n, 'VM network interface has a public IP address.'))
    R.add('Security', 'Network', 'High', 'VMs directly exposed with a public IP', pip_vm, 'Remove the public IP and publish through Azure Front Door, Application Gateway with WAF, or Bastion.', False, '', len(vms))
    nic_no_nsg = [_item(n, 'No NSG on the NIC (check the subnet NSG).') for n in nics if g(n, 'properties.virtualMachine.id') and not g(n, 'properties.networkSecurityGroup.id')]
    sub_nsg = {low(vn['id']) + '/subnets/' + low(s.get('name')): bool(g(s, 'properties.networkSecurityGroup.id')) for vn in vnets for s in (g(vn, 'properties.subnets', []) or [])}
    nic_no_nsg = [i for i, n in zip(nic_no_nsg, [n for n in nics if g(n, 'properties.virtualMachine.id') and not g(n, 'properties.networkSecurityGroup.id')])
                  if not any(sub_nsg.get(low(g(ic, 'properties.subnet.id')), False) for ic in (g(n, 'properties.ipConfigurations', []) or []))]
    R.add('Security', 'Network', 'High', 'VM network interfaces with no NSG on the NIC or the subnet', nic_no_nsg, 'Apply an NSG at the subnet or NIC.', False, '', len(nics))
    weak_st = []
    for a in stor:
        why = []
        if g(a, 'properties.allowBlobPublicAccess') is True:
            why.append('anonymous blob access allowed')
        if g(a, 'properties.supportsHttpsTrafficOnly') is False:
            why.append('HTTP allowed')
        if low(g(a, 'properties.minimumTlsVersion')) in ('tls1_0', 'tls1_1'):
            why.append('TLS below 1.2')
        if low(g(a, 'properties.networkAcls.defaultAction')) == 'allow' and not g(a, 'properties.privateEndpointConnections'):
            why.append('open to all networks')
        if why:
            weak_st.append(_item(a, '; '.join(why) + '.'))
    R.add('Security', 'Data protection', 'High', 'Storage accounts with weak access settings', weak_st, 'Disable anonymous access, require HTTPS and TLS 1.2, and restrict with firewall rules or private endpoints.', False, '', len(stor))
    weak_kv = []
    for k in kvs:
        why = []
        if g(k, 'properties.enablePurgeProtection') is not True:
            why.append('purge protection off')
        if g(k, 'properties.enableSoftDelete') is False:
            why.append('soft delete off')
        if low(g(k, 'properties.publicNetworkAccess')) == 'enabled' and low(g(k, 'properties.networkAcls.defaultAction')) in ('allow', ''):
            why.append('open to all networks')
        if why:
            weak_kv.append(_item(k, '; '.join(why) + '.'))
    R.add('Security', 'Data protection', 'Medium', 'Key Vaults with weak protection', weak_kv, 'Enable purge protection and restrict network access (private endpoint).', False, '', len(kvs))
    sqls = T('microsoft.sql/servers')
    R.add('Security', 'Data protection', 'High', 'SQL servers reachable from the public Internet', [_item(s, 'Public network access enabled.') for s in sqls if low(g(s, 'properties.publicNetworkAccess')) == 'enabled'],
          'Use private endpoints, or at minimum restrict the firewall to known ranges, and require Entra ID authentication.', False, '', len(sqls))
    R.add('Security', 'Identity', 'Medium', 'SQL servers allowing SQL authentication', [_item(s, 'Entra-only authentication is off.') for s in sqls if g(s, 'properties.administrators.azureADOnlyAuthentication') is not True],
          'Switch to Microsoft Entra-only authentication where applications support it.', False, '', len(sqls))
    web = T('microsoft.web/sites')
    R.add('Security', 'Data protection', 'Medium', 'Web apps that allow HTTP', [_item(w, 'HTTPS only is off.') for w in web if g(w, 'properties.httpsOnly') is False], 'Turn on HTTPS Only and set the minimum TLS version to 1.2.', False, '', len(web))
    aks = T('microsoft.containerservice/managedclusters')
    aks_sec, aks_rel, aks_old = [], [], []
    for k in aks:
        why = []
        if g(k, 'properties.enableRBAC') is False:
            why.append('Kubernetes RBAC off')
        if not g(k, 'properties.aadProfile'):
            why.append('no Entra ID integration')
        if not g(k, 'properties.networkProfile.networkPolicy'):
            why.append('no network policy')
        if not g(k, 'properties.apiServerAccessProfile.enablePrivateCluster') and not g(k, 'properties.apiServerAccessProfile.authorizedIPRanges'):
            why.append('API server open to the Internet')
        if why:
            aks_sec.append(_item(k, '; '.join(why) + '.'))
        pools = g(k, 'properties.agentPoolProfiles', []) or []
        if any(not p.get('availabilityZones') for p in pools):
            aks_rel.append(_item(k, 'Node pools are not spread over availability zones.'))
        if low(g(k, 'sku.tier')) == 'free':
            aks_rel.append(_item(k, 'Free tier has no uptime SLA.'))
        if not g(k, 'properties.autoUpgradeProfile.upgradeChannel') or low(g(k, 'properties.autoUpgradeProfile.upgradeChannel')) == 'none':
            aks_old.append(_item(k, f"Kubernetes {g(k, 'properties.kubernetesVersion', '?')} with no automatic upgrade channel."))
    R.add('Security', 'Containers', 'High', 'AKS clusters with weak security settings', aks_sec, 'Enable Entra ID + Kubernetes RBAC, a network policy, and a private cluster or authorized IP ranges.', False, '', len(aks))
    R.add('Reliability', 'Containers', 'Medium', 'AKS clusters without zone spread or an SLA', aks_rel, 'Use zone-redundant node pools and the Standard tier for production.', False, '', len(aks))
    R.add('Operational Excellence', 'Containers', 'Medium', 'AKS clusters with no automatic upgrade channel', aks_old, 'Set an upgrade channel (patch or stable) and planned maintenance windows.', False, '', len(aks))
    noenc = [_item(v, 'Host-based encryption is not enabled.') for v in vms if not g(v, 'properties.securityProfile.encryptionAtHost')]
    R.add('Security', 'Data protection', 'Low', 'VMs without encryption at host', noenc, 'Enable encryption at host on supported sizes. Disks are encrypted at rest with platform keys by default.', False, '', len(vms))
    dsev = {}
    for d in ev.get('defender', []):
        dsev.setdefault((d.get('name') or 'Defender recommendation', d.get('severity') or 'Low'), []).append(d)
    for (name, sev), rows in sorted(dsev.items(), key=lambda kv: ({'High': 0, 'Medium': 1}.get(kv[0][1], 2), -len(kv[1])))[:40]:
        its = [{'name': str(r.get('resource') or '').split('/')[-1] or '(subscription)', 'type': '', 'rg': '', 'sub': str(r.get('subscriptionId', ''))[:8], 'location': '', 'detail': '', 'saving': 0, 'id': r.get('resource') or ''} for r in rows[:300]]
        R.add('Security', 'Defender for Cloud', sev if sev in SEV_W else 'Low', name, its, 'Follow the Microsoft Defender for Cloud remediation steps for this recommendation.', False, '', None, source='Defender for Cloud')
    plans = ev.get('defender_plans', {})
    nodef = [{'name': s[:8], 'type': 'subscription', 'rg': '', 'sub': s[:8], 'location': '', 'detail': 'No paid Defender plans enabled.', 'saving': 0, 'id': s}
             for s, p in plans.items() if p and not any(v == 'Standard' for v in p.values())]
    R.add('Security', 'Defender for Cloud', 'Medium', 'Subscriptions with no Defender for Cloud plans enabled', nodef, 'Enable at least Defender for Servers, Storage and Key Vault on production subscriptions.', False, '', len(plans))


    # ---------- WAF & network security: coverage map and policy mode
    agws = T('microsoft.network/applicationgateways')
    wafpol = T('microsoft.network/applicationgatewaywebapplicationfirewallpolicies') + T('microsoft.network/frontdoorwebapplicationfirewallpolicies')
    afd = T('microsoft.cdn/profiles') + T('microsoft.network/frontdoors')
    weak_mode = []
    for w in wafpol:
        mode = low(g(w, 'properties.policySettings.mode'))
        state = low(g(w, 'properties.policySettings.state') or g(w, 'properties.policySettings.enabledState'))
        if mode == 'detection':
            weak_mode.append(_item(w, 'WAF policy is in Detection mode: attacks are logged but not blocked.'))
        elif state == 'disabled':
            weak_mode.append(_item(w, 'WAF policy is disabled.'))
    for a in agws:
        if low(g(a, 'properties.webApplicationFirewallConfiguration.firewallMode')) == 'detection':
            weak_mode.append(_item(a, 'Application Gateway WAF is in Detection mode.'))
    R.add('Security', 'WAF', 'High', 'WAF policies in Detection mode or disabled (web application firewall not blocking)', weak_mode, 'Tune false positives from the WAF logs, then switch the policy to Prevention mode.', False, '', len(wafpol) + len(agws))
    fwpol = [p for p in T('microsoft.network/firewallpolicies') if low(g(p, 'properties.threatIntelMode')) == 'off']
    R.add('Security', 'WAF', 'Medium', 'Azure Firewall policies with threat intelligence turned off', [_item(p, 'Threat intelligence mode is Off.') for p in fwpol], 'Set threat intelligence to Alert and deny, and consider IDPS on the Premium SKU.', False, '', len(T('microsoft.network/firewallpolicies')))
    backend_hosts = {}
    for a in agws:
        for pool in g(a, 'properties.backendAddressPools', []) or []:
            for b in g(pool, 'properties.backendAddresses', []) or []:
                if b.get('fqdn'):
                    backend_hosts[low(b['fqdn'])] = 'Application Gateway ' + a.get('name', '')
    for o in T('microsoft.cdn/profiles/origingroups/origins'):
        if g(o, 'properties.hostName'):
            backend_hosts[low(g(o, 'properties.hostName'))] = 'Front Door / CDN ' + low(o['id']).split('/profiles/')[-1].split('/')[0]
    wafmap, direct = [], []
    for w in web:
        host = low(g(w, 'properties.defaultHostName'))
        public = low(g(w, 'properties.publicNetworkAccess')) != 'disabled'
        front = backend_hosts.get(host, '')
        row = {'app': w.get('name'), 'kind': w.get('kind', ''), 'host': host, 'public': public, 'front': front or ('None' if public else 'Private'), 'rg': _rg(w)}
        wafmap.append(row)
        if public and not front and (agws or afd or True):
            direct.append(_item(w, 'Public app with no Application Gateway or Front Door in front (by backend hostname).'))
    R.add('Security', 'WAF', 'Medium', 'Public web apps with no WAF-capable front end in front of them', direct,
          'Publish through Front Door or Application Gateway with a WAF policy and restrict the app to that front end (access restrictions or private endpoint).', False,
          'Matched by default hostname against Application Gateway and Front Door backends; custom domains or other proxies need a manual check.', len(web))
    # ---------- RBAC
    OWNER, CONTRIB = '8e3af657-a8ff-443c-a75c-2fe8c4bcb635', 'b24988ac-6180-42a0-ab88-20f7382dd24c'
    owners = {}
    direct_users = []
    for ra in ev.get('rbac', []):
        sc = low(ra.get('scope'))
        if low(ra.get('role')).endswith(OWNER) and sc.count('/') <= 2:
            owners.setdefault(ra.get('subscriptionId'), []).append(ra)
        if low(ra.get('principalType')) == 'user' and (low(ra.get('role')).endswith(OWNER) or low(ra.get('role')).endswith(CONTRIB)) and sc.count('/') <= 2:
            direct_users.append(ra)
    R.add('Security', 'Identity', 'High', 'Subscriptions with more than 3 Owners', [{'name': str(sid)[:8], 'type': 'subscription', 'rg': '', 'sub': str(sid)[:8], 'location': '', 'detail': f'{len(v)} Owner assignments at subscription scope.', 'saving': 0, 'id': sid}
          for sid, v in owners.items() if len(v) > 3], 'Reduce standing Owners to two or three break-glass or platform admins; use Privileged Identity Management for the rest.', False, '', len(owners))
    R.add('Security', 'Identity', 'Medium', 'Owner or Contributor assigned directly to individual users', [{'name': str(r.get('principal'))[:8], 'type': 'user assignment', 'rg': '', 'sub': str(r.get('subscriptionId'))[:8], 'location': '', 'detail': 'Role: ' + ('Owner' if low(r.get('role')).endswith(OWNER) else 'Contributor') + ' at subscription scope.', 'saving': 0, 'id': ''}
          for r in direct_users[:200]], 'Assign roles to Entra ID groups and make them eligible through PIM.', False, '', None)

    # ---------- Reliability
    prot = ev.get('backup', set())
    nobk = [_item(v, f"No backup. {low(g(v, 'properties.extended.instanceView.powerState.code', '')).replace('powerstate/', '')}") for v in vms if low(v['id']) not in prot]
    R.add('Reliability', 'Backup and recovery', 'High', 'VMs with no Azure Backup protection', nobk, 'Protect with an Azure Backup policy (daily, 30 day retention as a start) and test a restore.', False, '', len(vms))
    single = [_item(v, 'Not in an availability zone or availability set.') for v in vms if not v.get('zones') and not g(v, 'properties.availabilitySet.id') and not g(v, 'properties.virtualMachineScaleSet.id')]
    R.add('Reliability', 'High availability', 'Medium', 'VMs with no availability zone or availability set', single, 'Place VM pairs in different zones, or use scale sets / availability sets, behind a load balancer.', False, '', len(vms))
    hdd = [_item(v, f"OS disk is {g(v, 'properties.storageProfile.osDisk.managedDisk.storageAccountType')}.") for v in vms if low(g(v, 'properties.storageProfile.osDisk.managedDisk.storageAccountType')) == 'standard_lrs']
    R.add('Performance Efficiency', 'Storage', 'Medium', 'VM OS disks on Standard HDD', hdd, 'Move production OS disks to Standard SSD or Premium SSD.', False, '', len(vms))
    lrs = [_item(a, f"{g(a, 'sku.name')}.") for a in stor if low(g(a, 'sku.name')).endswith('_lrs')]
    R.add('Reliability', 'Data resilience', 'Medium', 'Storage accounts with locally redundant storage only', lrs, 'Use ZRS (same-region zones) or GRS/GZRS for data that must survive a datacenter or region outage.', False, '', len(stor))
    dbs = [d for d in T('microsoft.sql/servers/databases') if low(d.get('name')) != 'master']
    R.add('Reliability', 'Data resilience', 'Medium', 'SQL databases with local backup redundancy or no zone redundancy', [_item(d, f"Backup storage: {g(d, 'properties.currentBackupStorageRedundancy', 'unknown')}, zone redundant: {g(d, 'properties.zoneRedundant', False)}.") for d in dbs
          if low(g(d, 'properties.currentBackupStorageRedundancy')) == 'local' or (g(d, 'properties.zoneRedundant') is False and low(g(d, 'sku.tier')) in ('businesscritical', 'premium', 'generalpurpose'))],
          'Use geo- or zone-redundant backup storage, and zone redundancy or a failover group for production.', False, '', len(dbs))
    one = [_item(p, f"{g(p, 'sku.tier', '')} plan with a single instance.") for p in T('microsoft.web/serverfarms') if int(g(p, 'sku.capacity', 1) or 1) < 2 and g(p, 'sku.tier') not in ('Free', 'Shared', 'Dynamic', 'ElasticPremium')
           and sites_by_plan.get(low(p['id']))]
    R.add('Reliability', 'High availability', 'Medium', 'App Service plans with a single instance', one, 'Run two or more instances (zone redundant on Premium v3) for production apps.', False, '', len(T('microsoft.web/serverfarms')))
    basic = [_item(r, f"Basic SKU {str(r.get('type')).split('/')[-1]}.") for r in pips + T('microsoft.network/loadbalancers') if low(g(r, 'sku.name')) == 'basic']
    R.add('Reliability', 'Retirements', 'High', 'Basic SKU public IPs and load balancers (retired)', basic, 'Upgrade to Standard SKU. Basic has no zone redundancy or SLA and is being retired.', False, '', len(pips))
    agw = [_item(a, f"SKU {g(a, 'properties.sku.name')}.") for a in T('microsoft.network/applicationgateways') if low(g(a, 'properties.sku.tier')) in ('standard', 'waf')]
    R.add('Reliability', 'Retirements', 'High', 'Application Gateway v1 (retired)', agw, 'Migrate to Application Gateway v2 (WAF_v2).', False, '', len(T('microsoft.network/applicationgateways')))
    nowaf = [_item(a, 'No WAF on this Application Gateway.') for a in T('microsoft.network/applicationgateways') if 'waf' not in low(g(a, 'properties.sku.tier')) and not g(a, 'properties.firewallPolicy')]
    R.add('Security', 'Network', 'Medium', 'Application Gateways without a web application firewall', nowaf, 'Use WAF_v2 with a WAF policy in prevention mode for internet-facing apps.', False, '', len(T('microsoft.network/applicationgateways')))

    # ---------- Operational Excellence
    key = lambda r: any(k in {low(x) for x in (r.get('tags') or {})} for k in ('owner', 'costcenter', 'cost-center', 'environment', 'env', 'application', 'project'))
    taggable = [r for r in inv if low(r.get('type')) not in ('microsoft.compute/virtualmachines/extensions',) and r.get('type') and '/' in low(r.get('type')) and low(r.get('type')).count('/') == 1]
    untagged = [r for r in taggable if not r.get('tags')]
    tag_cov = round(100 * (1 - len(untagged) / len(taggable)), 1) if taggable else 100.0
    R.add('Operational Excellence', 'Governance', 'Medium', f'Resources with no tags (tag coverage {tag_cov}%)', [_item(r, 'No tags.') for r in untagged[:400]], 'Tag owner, environment, application and cost centre (enforce with Azure Policy), so cost and ownership are traceable.', False, '', len(taggable))
    no_policy = [{'name': s[:8], 'type': 'subscription', 'rg': '', 'sub': s[:8], 'location': '', 'detail': 'No policy assignments found.', 'saving': 0, 'id': s}
                 for s in {r.get('subscriptionId') for r in inv if r.get('subscriptionId')} if not any(p.get('subscriptionId') == s for p in ev.get('policy', []))]
    R.add('Operational Excellence', 'Governance', 'High', 'Subscriptions with no Azure Policy assignments', no_policy, 'Assign the Azure Security Benchmark initiative and tagging, allowed-location and allowed-SKU policies at management group or subscription level.', False, '', None)
    ag = {low(r['id']).split('/extensions/')[0] for r in T('microsoft.compute/virtualmachines/extensions') if 'monitor' in low(r.get('name')) or 'oms' in low(r.get('name')) or 'loganalytics' in low(r.get('name'))}
    nomon = [_item(v, 'No Azure Monitor / Log Analytics agent extension.') for v in vms if low(v['id']) not in ag]
    R.add('Operational Excellence', 'Monitoring', 'Medium', 'VMs without a monitoring agent', nomon, 'Install the Azure Monitor Agent with a data collection rule, and alert on CPU, memory, disk and heartbeat.', False, '', len(vms))
    nodiag = [_item(v, 'Boot diagnostics off.') for v in vms if g(v, 'properties.diagnosticsProfile.bootDiagnostics.enabled') is False]
    R.add('Operational Excellence', 'Monitoring', 'Low', 'VMs with boot diagnostics off', nodiag, 'Enable managed boot diagnostics for serial console and screenshots.', False, '', len(vms))
    eol = []
    for v in vms:
        ref = g(v, 'properties.storageProfile.imageReference', {}) or {}
        offer, sku = low(ref.get('offer')), low(ref.get('sku'))
        for off, vers in EOL_OS:
            if off in offer and any(vv in sku or vv in offer for vv in vers):
                eol.append(_item(v, f"Image {ref.get('offer')} {ref.get('sku')} is at or near end of support."))
                break
    R.add('Security', 'Patching and support', 'High', 'VMs on operating systems at or near end of support', eol, 'Upgrade or migrate. Extended Security Updates on Azure keep Windows Server 2012 patched while you migrate.', False, '', len(vms))
    classic = [_item(r, 'Classic (ASM) resource.') for r in inv if low(r.get('type')).startswith(('microsoft.classic', 'microsoft.compute/domainnames'))]
    R.add('Operational Excellence', 'Retirements', 'High', 'Classic resources (retired platform)', classic, 'Migrate to Azure Resource Manager resources.', False, '', None)
    # ---------- Performance
    noacc = [_item(n, 'Accelerated networking is off.') for n in nics if g(n, 'properties.virtualMachine.id') and g(n, 'properties.enableAcceleratedNetworking') is False]
    R.add('Performance Efficiency', 'Network', 'Low', 'VM NICs without accelerated networking', noacc, 'Enable it on supported sizes (4+ vCPU) for lower latency and CPU use. Needs a VM stop.', False, '', len(nics))
    oldgen = [_item(v, f"{g(v, 'properties.hardwareProfile.vmSize')} is generation v{vm_generation(g(v, 'properties.hardwareProfile.vmSize', ''))}.") for v in vms if vm_generation(g(v, 'properties.hardwareProfile.vmSize', '')) <= 3
              and not str(g(v, 'properties.hardwareProfile.vmSize', '')).startswith('Standard_B')]
    R.add('Performance Efficiency', 'Modernisation', 'Low', 'VMs on older hardware generations (v1 to v3)', oldgen, 'Move to current v5 / v6 sizes: usually better price/performance, and a smaller size often fits.', False, '', len(vms))

    # ---------- Modernisation candidates
    mod = {'SQL Server on VM': [], 'Windows web/app server': [], 'Linux VM': [], 'Other': []}
    for v in vms:
        ref = g(v, 'properties.storageProfile.imageReference', {}) or {}
        offer = low(ref.get('offer'))
        os_ = low(g(v, 'properties.storageProfile.osDisk.osType'))
        if offer.startswith('sql'):
            mod['SQL Server on VM'].append(v)
        elif os_ == 'windows':
            mod['Windows web/app server'].append(v)
        elif os_ == 'linux':
            mod['Linux VM'].append(v)
        else:
            mod['Other'].append(v)
    advice = {'SQL Server on VM': 'Candidate for Azure SQL Managed Instance or Azure SQL Database (run Azure Migrate SQL assessment for compatibility).',
              'Windows web/app server': 'Candidate for App Service or containers if it runs IIS/.NET (run the Azure Migrate web app assessment). Otherwise keep as IaaS and right-size.',
              'Linux VM': 'Candidate for AKS, Container Apps or App Service for Linux if it runs a containerisable app; databases to Azure Database for PostgreSQL/MySQL.',
              'Other': 'Review the workload.'}
    modern = [{'category': k, 'count': len(v), 'advice': advice[k], 'examples': [x.get('name') for x in v[:8]]} for k, v in mod.items() if v]
    R.add('Operational Excellence', 'Modernisation', 'Low', 'Application modernisation candidates (VM to PaaS / containers)', [_item(v, advice[k]) for k, vs in mod.items() if k != 'Other' for v in vs[:150]],
          'Run Azure Migrate application discovery on the VMs to confirm; PaaS lowers patching and licence effort.', False, 'Based on image and OS only; runtime and dependencies need discovery.', len(vms), source='Heuristic')

    # ---------- Landing zone
    firewalls, bastions, gws = T('microsoft.network/azurefirewalls'), T('microsoft.network/bastionhosts'), T('microsoft.network/virtualnetworkgateways')
    peered = [vn for vn in vnets if g(vn, 'properties.virtualNetworkPeerings')]
    pdns, pe, la = T('microsoft.network/privatednszones'), T('microsoft.network/privateendpoints'), T('microsoft.operationalinsights/workspaces')
    udr = T('microsoft.network/routetables')
    defender_on = sum(1 for p in plans.values() if any(v == 'Standard' for v in (p or {}).values()))
    subs_n = len({r.get('subscriptionId') for r in inv if r.get('subscriptionId')})
    lz = [
        ('Management groups / subscription hierarchy', len(ev.get('mgmt_groups', [])) > 1, f"{len(ev.get('mgmt_groups', []))} management group(s) visible.", 'Use a management group hierarchy (platform, landing zones, sandbox) so policy and RBAC inherit.'),
        ('Azure Policy governance', not no_policy, f"{len(ev.get('policy', []))} policy assignment(s).", 'Assign baseline policy initiatives at management group level.'),
        ('Hub-and-spoke or virtual WAN connectivity', len(peered) >= 2 and bool(firewalls or gws), f'{len(vnets)} VNets, {len(peered)} peered, {len(firewalls)} firewall(s), {len(gws)} gateway(s).', 'Build a hub VNet with firewall/gateway and peer workload spokes to it.'),
        ('Central egress and inspection (Azure Firewall / NVA)', bool(firewalls) or any('0.0.0.0/0' in str(g(t, 'properties.routes', [])) for t in udr), f'{len(firewalls)} firewall(s), {len(udr)} route table(s).', 'Force-tunnel internet traffic through a firewall with user-defined routes.'),
        ('Secure remote access (Bastion / JIT)', bool(bastions), f'{len(bastions)} Bastion host(s).', 'Deploy Azure Bastion in the hub and remove public RDP/SSH.'),
        ('Network segmentation (NSG on workload subnets)', not no_nsg_sub, f'{len(no_nsg_sub)} subnet(s) without an NSG.', 'Attach NSGs to all workload subnets.'),
        ('Private connectivity to PaaS (private endpoints + private DNS)', bool(pe) and bool(pdns), f'{len(pe)} private endpoint(s), {len(pdns)} private DNS zone(s).', 'Use private endpoints for storage, SQL and Key Vault with linked private DNS zones.'),
        ('Central logging (Log Analytics)', bool(la), f'{len(la)} workspace(s).', 'Create a central Log Analytics workspace and send activity and resource logs to it.'),
        ('Threat protection (Defender for Cloud plans)', bool(plans) and defender_on == len(plans), f'{defender_on} of {len(plans)} subscription(s) have paid plans.', 'Enable Defender plans on all production subscriptions.'),
        ('Backup and recovery', bool(T('microsoft.recoveryservices/vaults')) and len(nobk) < max(1, len(vms)) * 0.2, f"{len(T('microsoft.recoveryservices/vaults'))} vault(s); {len(vms) - len(nobk)} of {len(vms)} VMs protected.", 'Use Recovery Services vaults with policies for all production VMs.'),
        ('Tagging standard', tag_cov >= 80, f'Tag coverage {tag_cov}%.', 'Enforce owner, environment, application and cost-centre tags with policy.'),
        ('Workload separation across subscriptions', subs_n > 1, f'{subs_n} subscription(s) assessed.', 'Separate platform and workload subscriptions (a landing zone per workload or environment).'),
    ]
    landing = [{'check': a, 'ok': bool(b), 'evidence': c_, 'fix': d} for a, b, c_, d in lz]
    for L in landing:
        if not L['ok']:
            R.add('Operational Excellence', 'Landing zone', 'High' if L['check'].startswith(('Management', 'Hub', 'Azure Policy')) else 'Medium', 'Landing zone gap: ' + L['check'],
                  [{'name': L['check'], 'type': 'landing zone', 'rg': '', 'sub': '', 'location': '', 'detail': L['evidence'], 'saving': 0, 'id': ''}], L['fix'], False, '', None, source='Landing zone check')

    # ---------- modules, de-duplicated savings, scores
    for f in R.findings:
        f['module'] = module_of(f)
    chosen = set(modules) if modules else set(MODULES)
    R.findings = [f for f in R.findings if f['module'] in chosen]
    order = {'High': 0, 'Medium': 1, 'Low': 2}
    R.findings.sort(key=lambda f: (PILLARS.index(f['pillar']), order.get(f['severity'], 3), -f['saving'], -f['count']))
    scores = {}
    for p in PILLARS:
        pen = 0.0
        for f in R.findings:
            if f['pillar'] != p:
                continue
            share = min(1.0, f['count'] / f['population']) if f.get('population') else 1.0
            pen += SEV_W.get(f['severity'], 2) * (0.4 + 0.6 * share)
        sc = max(0, round(100 - pen))
        scores[p] = {'score': sc, 'rating': 'Good' if sc >= 80 else ('Fair' if sc >= 60 else 'Needs attention'),
                     'findings': sum(1 for f in R.findings if f['pillar'] == p), 'high': sum(1 for f in R.findings if f['pillar'] == p and f['severity'] == 'High')}
    mod_summary = {}
    for k, name in MODULES.items():
        fl = [f for f in R.findings if f['module'] == k]
        mod_summary[k] = {'name': name, 'selected': k in chosen, 'findings': len(fl), 'high': sum(1 for f in fl if f['severity'] == 'High'), 'saving': round(sum(f['saving'] for f in fl), 2)}
    # one saving per resource: the largest estimate wins, so a VM that is both right-sized and reservation-eligible is counted once
    per_res = {}
    for f in R.findings:
        if f['pillar'] == 'Cost Optimization':
            for i in f['items']:
                if i['saving']:
                    per_res[i['id'] or i['name']] = max(per_res.get(i['id'] or i['name'], 0), i['saving'])
    advisor_only = sum(f['saving'] for f in R.findings if f['pillar'] == 'Cost Optimization' and f.get('source') == 'Azure Advisor' and not any(i['saving'] for i in f['items']))
    sav = round(sum(per_res.values()) + advisor_only, 2)
    gross = round(sum(f['saving'] for f in R.findings if f['pillar'] == 'Cost Optimization'), 2)
    # verified vs needs evidence
    status = ev.get('source_status', {})
    needed = set()
    for k in chosen:
        needed |= MODULE_SOURCES[k] | {'inventory'}
    verified = [{'source': SOURCE_LABEL[k], 'status': status.get(k, 'ok' if k == 'inventory' else 'skipped'), 'role': ROLE_NEEDED[k]} for k in SOURCE_LABEL if k in needed]
    extra_need = {'waf': ['WAF logs (Log Analytics) to review exclusions and false positives', 'Azure Firewall logs to map traffic flows'],
                  'modern': ['Azure Migrate application discovery (runtimes, dependencies, inbound connections)', 'Application owners: code, support dates, licence terms'],
                  'backup': ['Agreed RPO and RTO per workload', 'Restore test and failover test evidence', 'Recovery point history (Backup reports)'],
                  'perfops': ['Application Insights latency and failure rates', 'Disk throughput and memory metrics from the Azure Monitor Agent', 'Alert ownership and on-call routing'],
                  'security': ['Microsoft Entra ID (MFA, Conditional Access, guest users): needs Entra roles', 'Microsoft Sentinel / log coverage'],
                  'finops': ['Reservation and Savings Plan utilisation (Billing Reader on the billing scope)', 'Cost allocation by business unit (tag or billing data)'],
                  'infra': ['Application dependencies and workload objectives']}
    needs = []
    for k in MODULES:
        if k in chosen:
            needs += [{'module': MODULES[k], 'item': x} for x in extra_need.get(k, [])]
    for v in verified:
        if v['status'] == 'failed':
            needs.insert(0, {'module': 'Access', 'item': f"{v['source']} could not be read. Needs the role: {v['role']}."})
    top = sorted(R.findings, key=lambda f: (order.get(f['severity'], 3), -f['saving'], -f['count']))[:10]
    util.sort(key=lambda r: -(r.get('cost_30d') or 0))
    return {'scores': scores, 'findings': R.findings, 'utilization': util[:400], 'landing_zone': landing, 'modernization': modern, 'waf_map': wafmap[:300],
            'modules': mod_summary, 'verified': verified, 'needs_evidence': needs,
            'top_actions': [{'pillar': f['pillar'], 'module': MODULES[f['module']], 'title': f['title'], 'severity': f['severity'], 'count': f['count'], 'saving': f['saving'], 'recommendation': f['recommendation']} for f in top],
            'summary': {'resources': len(inv), 'subscriptions': subs_n, 'cost_30d': ev.get('cost_total', 0), 'currency': ev.get('currency', 'USD'),
                        'cost_by_service': sorted(((k, round(v, 2)) for k, v in ev.get('cost_by_service', {}).items()), key=lambda kv: -kv[1])[:15],
                        'estimated_saving_30d': sav, 'gross_saving_30d': gross, 'findings': len(R.findings), 'high': sum(1 for f in R.findings if f['severity'] == 'High'),
                        'metrics_read': len(metrics), 'metrics_planned': ev.get('metrics_planned', 0), 'tag_coverage': tag_cov,
                        'types': sorted(((k, len(v)) for k, v in by_type.items() if '/' in k and k.count('/') == 1), key=lambda kv: -kv[1])[:20]},
            'warnings': ev.get('warnings', [])}
