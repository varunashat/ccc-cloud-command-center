"""Fill the official Azure Pricing Calculator WITHOUT any AI model (no Foundry, no Claude, no ChatGPT).

How it works (deterministic, plain Python + Playwright):
  1. Open the calculator in the dedicated Edge profile (Microsoft sign-in is remembered there).
  2. Create a new, empty estimate in the calculator's own browser storage (IndexedDB "localforage").
  3. Click "Add to estimate" for each service slug, so the calculator creates each module with its own defaults.
  4. Write the discovered configuration into each module (region, VM size, OS, count, disk tier/size, ...).
  5. Reload: the calculator recalculates every price itself from Microsoft's price list.
  6. Verify each module (size / count / price) against what was requested, then Save and Share -> azure.com/e/... link.

The calculator's storage format was read from the live calculator on 20 Nov 2026 (module states carry plain
fields such as size 'd8sv5', region 'uae-north', operatingSystem, count, managedDiskType 'p20').
If Microsoft changes that format, step 6 reports the mismatch instead of returning a wrong link.
"""
import json
import math
import re
import uuid
from pathlib import Path

import calculator_agent as ca

# nearest region on the same continent, for intra-continent data transfer read from the bill
_INTRA = {'uae-north': 'uae-central', 'uae-central': 'uae-north', 'europe-west': 'europe-north', 'europe-north': 'europe-west',
          'us-east': 'us-east-2', 'us-east-2': 'us-east', 'asia-pacific-east': 'asia-pacific-southeast', 'asia-pacific-southeast': 'asia-pacific-east',
          'central-india': 'south-india', 'united-kingdom-south': 'united-kingdom-west', 'qatar-central': 'uae-north'}

# calculator region slugs (display name -> slug), read from the calculator's Region drop-down
REGION_SLUGS = {
    'Central US': 'us-central', 'East US': 'us-east', 'East US 2': 'us-east-2', 'North Central US': 'us-north-central',
    'South Central US': 'us-south-central', 'West Central US': 'us-west-central', 'West US': 'us-west', 'West US 2': 'us-west-2',
    'West US 3': 'us-west-3', 'UK South': 'united-kingdom-south', 'UK West': 'united-kingdom-west', 'UAE Central': 'uae-central',
    'UAE North': 'uae-north', 'Taiwan North': 'taiwan-north', 'Switzerland North': 'switzerland-north', 'Switzerland West': 'switzerland-west',
    'Sweden Central': 'sweden-central', 'Sweden South': 'sweden-south', 'Spain Central': 'spain-central', 'Qatar Central': 'qatar-central',
    'Poland Central': 'poland-central', 'Norway East': 'norway-east', 'Norway West': 'norway-west', 'New Zealand North': 'new-zealand-north',
    'Mexico Central': 'mexico-central', 'Malaysia West': 'malaysia-west', 'Korea Central': 'korea-central', 'Korea South': 'korea-south',
    'Japan East': 'japan-east', 'Japan West': 'japan-west', 'Italy North': 'italy-north', 'Israel Central': 'israel-central',
    'Indonesia Central': 'indonesia-central', 'Central India': 'central-india', 'India South Central': 'south-central-india',
    'South India': 'south-india', 'West India': 'west-india', 'Germany North': 'germany-north', 'Germany West Central': 'germany-west-central',
    'France Central': 'france-central', 'France South': 'france-south', 'North Europe': 'europe-north', 'West Europe': 'europe-west',
    'Denmark East': 'denmark-east', 'Chile Central': 'chile-central', 'Canada Central': 'canada-central', 'Canada East': 'canada-east',
    'Brazil South': 'brazil-south', 'Brazil Southeast': 'brazil-southeast', 'Belgium Central': 'belgium-central',
    'Austria East': 'austria-east', 'Australia Central': 'australia-central', 'Australia Central 2': 'australia-central-2',
    'Australia East': 'australia-east', 'Australia Southeast': 'australia-southeast', 'East Asia': 'asia-pacific-east',
    'Southeast Asia': 'asia-pacific-southeast', 'South Africa North': 'south-africa-north', 'South Africa West': 'south-africa-west',
}
DISK_N = [4, 6, 10, 15, 20, 30, 40, 50, 60, 70, 80]
DISK_GB = [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32767]
DISK_TIER = {'premium': ('premiumssd', 'p', 'Premium SSD'), 'standardssd': ('standardssd', 'e', 'Standard SSD'),
             'standard': ('standardhdd', 's', 'Standard HDD')}
APP_TIERS = [(r'^P\d+V4', 'premiumv4'), (r'^P\d+M?V3', 'premiumv3'), (r'^P\d+V2', 'premiumv2'), (r'^S\d', 'standard'),
             (r'^B\d', 'basic'), (r'^I\d+V2', 'isolatedv2'), (r'^F1', 'free'), (r'^D1', 'shared')]
SEARCH_TIERS = {'free': 'free', 'basic': 'basic', 'standard': 'standard-s1', 'standard2': 'standard-s2', 'standard3': 'standard-s3',
                'storage_optimized_l1': 'storage-optimized-l1', 'storage_optimized_l2': 'storage-optimized-l2'}
ZERO_VM_EXTRAS = {'managedDisks': 0, 'addSnapshot': False, 'snapshotStorage': 0, 'internetEgressUnits': 0, 'interRegionUnits': 0,
                  'internetEgressStorageUnits': 0, 'interRegionStorageUnits': 0, 'dataTransferType': 'internet'}
BLOB_ZERO_OPS = {k: 0 for k in ('blobWriteOperations', 'blobWriteOperationsV1', 'blobCreateContainerOperations', 'blobCreateContainerOperationsV1',
                                'blobReadOperations', 'blobReadOperationsV1', 'blobOtherOperations', 'blobOtherOperationsV1', 'blobDataRetrieval',
                                'blobDataWrite', 'blobIterativeReadOperations', 'blobIterativeWriteOperations', 'blobArchiveRead',
                                'blobArchiveRetrieval', 'blobIndex')}


def region_slug(region, regions_map):
    """ARM name (uaenorth) or display name (UAE North) -> calculator slug (uae-north)."""
    r = str(region or '').strip()
    disp = regions_map.get(r.lower().replace(' ', ''), r)
    if disp in REGION_SLUGS:
        return REGION_SLUGS[disp]
    key = r.lower().replace(' ', '').replace('-', '')
    for name, slug in REGION_SLUGS.items():           # ARM name 'eastasia' == display 'East Asia' without spaces
        if name.lower().replace(' ', '') == key or slug.replace('-', '') == key:
            return slug
    return 'uae-north'


def vm_slug(sku):
    """Standard_D8s_v5 -> d8sv5 (calculator size slug)."""
    return re.sub(r'[^a-z0-9]', '', re.sub(r'^standard_', '', str(sku).lower()))


def disk_n(gb, tier):
    n = next((n for n, g in zip(DISK_N, DISK_GB) if g >= float(gb)), 80)
    return n


def _app_tier(sku):
    s = str(sku).upper()
    return next((t for pat, t in APP_TIERS if re.match(pat, s)), 'standard')


def build_lines(items, inventory, e, disk_tier='premium', defender_plan='p2', default_region='uaenorth'):
    """Group discovered items into calculator modules. Returns (lines, not_in_calculator).

    Each line: {'slug', 'label', 'count', 'set': {module fields}, 'check': [text expected in the module description]}."""
    R = e.REGIONS
    groups, notes = {}, []

    def add(key, slug, label, fields, check, n=1, hours=730.0):
        g = groups.setdefault(key, {'slug': slug, 'label': label, 'count': 0, 'set': fields, 'check': check, 'hours': 0.0})
        g['count'] += n
        g['hours'] += float(hours) * n

    for i in items:
        t = i['type']
        rs = region_slug(i.get('region') or default_region, R)
        q = int(i.get('quantity') or 1)
        if t == 'vm':
            if i.get('sku'):
                sku = e.norm_vm_sku(i['sku'])
            else:
                try:
                    sku, _ = e.size_vm(float(i['vcpu']), float(i['ram_gb']))
                except Exception:
                    notes.append(f"VM {i.get('name')}: no Azure size fits {i.get('vcpu')} vCPU / {i.get('ram_gb')} GB")
                    continue
            os_ = 'windows' if i.get('os') == 'windows' else 'linux'
            ahb = i.get('ahub') == 'yes' and os_ == 'windows'
            lab = f"{e.calc_label(sku)} {os_.title()}" + (' AHB' if ahb else '')
            if i.get('sql_edition'):
                notes.append(f"VM {i.get('name')}: SQL Server {i['sql_edition']} licence is NOT included in the calculator line (add it to that VM)")
            add(('vm', sku, os_, ahb, rs), 'virtual-machines', 'Virtual Machines ' + lab,
                dict(ZERO_VM_EXTRAS, region=rs, size=vm_slug(sku), operatingSystem=os_, type='os-only', tier='standard', category='all',
                     instanceSeries='all', hours=730, hoursFactor=1, computeBillingOption='payg', billingOption='payg',
                     osBillingOption='ahb' if ahb else 'payg', subscriptionOption='ahb' if ahb else 'payg'),
                [e.calc_label(sku)], q, float(i.get('hours') or 730))
        elif t == 'disk':
            tier = i.get('tier') or disk_tier
            ctier, pre, lab = DISK_TIER.get(tier, DISK_TIER['premium'])
            n = disk_n(i['disk_gb'], tier)
            add(('disk', ctier, n, rs), 'managed-disks', f'Managed Disks {lab} {pre.upper()}{n}',
                dict(region=rs, tier=ctier, managedDiskType=f'{pre}{n}', managedDiskRedundancy='lrs', managedDisksTransactions=0,
                     addSnapshot=False, snapshotStorage=0, managedDiskBillingOption='payg'), [f'{pre.upper()}{n}'])
        elif t == 'blob':
            red = str(i.get('redundancy', 'LRS')).lower().replace('ragrs', 'ra-grs').replace('ragzrs', 'ra-gzrs')
            add(('blob', i['tier'], red, rs, i.get('name')), 'storage', f"Blob Storage {i['tier']} {red.upper()} {max(1, round(i['gb']))} GB",
                dict(BLOB_ZERO_OPS, region=rs, type='block-blob', performanceTier='standard', storageAccountType='general-purpose-v2',
                     accessTier=str(i['tier']).lower(), redundancy=red, count=max(1, round(i['gb']))), [f"{max(1, round(i['gb'])):,} GB"])
        elif t == 'public_ip':
            add(('pip', rs), 'ip-addresses', 'Public IP addresses (Standard, static)', dict(region=rs, type='standardarm'), ['Standard'])
        elif t == 'app_service':
            add(('app', i['sku'], i['os'], rs), 'app-service', f"App Service {i['sku']} {i['os']}",
                dict(region=rs, tier=_app_tier(i['sku']), size=str(i['sku']).lower(), type=i['os'], hours=730, billingOption='payg'),
                [str(i['sku']).upper()], q)
        elif t == 'sql_db':
            add(('sqldb', i['tier'], i['vcores'], i['storage_gb'], i.get('ahub'), rs), 'azure-sql-database',
                f"Azure SQL Database {i['tier'].upper()} {i['vcores']} vCore",
                dict(region=rs, type='single', purchaseModel='vcore', vcoreTier='general-purpose' if i['tier'] == 'gp' else 'business-critical',
                     computeTier='provisioned', generation='gen5', instanceSize=int(i['vcores']), databaseBillingOption='payg',
                     softwareBillingOption='ahb' if i.get('ahub') == 'yes' else 'payg', singleStorageUnits=int(i['storage_gb']), hours=730),
                [f"{int(i['vcores'])} vCore"])
        elif t == 'sql_mi':
            add(('sqlmi', i['tier'], i['vcores'], i['storage_gb'], i.get('ahub'), rs), 'azure-sql',
                f"Azure SQL Managed Instance {i['tier'].upper()} {i['vcores']} vCore",
                dict(region=rs, vcoreTier='general-purpose' if i['tier'] == 'gp' else 'business-critical', instanceSize=int(i['vcores']),
                     databaseBillingOption='payg', softwareBillingOption='ahb' if i.get('ahub') == 'yes' else 'payg',
                     managedStorageUnits=max(1, math.ceil(float(i['storage_gb']) / 32)), backupStorageSize=0, ltrDatabaseSize=0, hours=730),
                [f"{int(i['vcores'])} vCore"])
        elif t == 'aks':
            add(('aks', i['tier'], rs), 'kubernetes-service', f"AKS cluster management ({i['tier']} tier)",
                dict(region=rs, kubernetesServiceOfferTier='standard', slaOptionValue='sla' if i['tier'] == 'standard' else 'no-sla-free-non-production',
                     count=0), ['Cluster management'])
        elif t == 'defender':
            plan = 'serversPlan1Nodes' if i['plan'] == 'p1' else 'serversPlan2Nodes'
            add(('defender',), 'defender-for-cloud', f"Defender for Servers Plan {i['plan'][-1]}: {i['count']} servers (from the bill)",
                {'region': 'uae-north', 'planType': 'workloadprotection', plan: i['count']}, [f"{i['count']} Plan {i['plan'][-1]}"])
            groups[('defender',)]['set'][plan] = i['count']
        elif t == 'law':
            add(('monitor',), 'monitor', f"Azure Monitor / Log Analytics: {i['gb_per_day']:g} GB/day ingested (from the bill)",
                dict(region=region_slug(default_region, R), dailyLogsIngested=i['gb_per_day'], logAnalyticsRetention=1), ['Log analytics'])
        elif t == 'arc_sql':
            add(('arcsql', i['edition']), 'azure-arc', f"Arc-enabled SQL Server {i['edition'].title()}: {i['vcores']} vCores pay-as-you-go (from the bill)",
                dict(region=region_slug(default_region, R), servers=0, sqlServerServiceTier=i['edition'], sqlServerOnPremVCores=i['vcores'],
                     sqlServerOnPremVCoreHours=730, sqlServerOnPremVCoreHoursFactor=1, sqlLicenseBillingOption='payg', onPremVCores=0), ['SQL Server'])
        elif t == 'calc':                                  # line built straight from billed usage (discovery.meter_lines)
            f = {}
            for k2, v in i['set'].items():
                if isinstance(v, list):
                    f[k2] = [rs if x == '__region__' else x for x in v]; continue
                f[k2] = rs if v == '__region__' else (_INTRA.get(rs, rs) if v == '__near__' else ('europe-west' if rs != 'europe-west' else 'us-east') if v == '__far__' else v)
            if not i.get('noregion'):
                f.setdefault('region', rs)
            add(('calc',) + tuple(i['key']), i['slug'], i['label'], f, i.get('check', []))
            groups[('calc',) + tuple(i['key'])]['fixed'] = True
        elif t == 'firewall':
            tier = str(i.get('tier', 'Standard')).lower()
            tier = tier if tier in ('basic', 'standard', 'premium') else 'standard'
            add(('fw', tier, rs), 'azure-firewall', f'Azure Firewall {tier.title()}',
                {'region': rs, 'tier': tier, f'{tier}DataProcessed': 0}, [tier.title() + ' tier'])
        elif t == 'vpn_gateway':
            add(('vpn', i.get('name'), rs), 'vpn-gateway', f"VPN Gateway {i.get('sku', 'VpnGw1')} ({i.get('name')})",
                dict(region=rs, type='vpngateways', vpnType='vpn', tier=str(i.get('sku', 'VpnGw1')).lower()), [str(i.get('sku', 'VpnGw1'))])
        else:
            notes.append(f"{t} {i.get('name')}: not mapped to the calculator")

    # usage-based services found in the inventory: added with zero / known usage so they are listed in the estimate
    def rtype(t):
        return [r for r in inventory if str(r.get('type', '')).lower() == t]
    for r in rtype('microsoft.search/searchservices'):
        sku = str((r.get('sku') or {}).get('name', 'basic')).lower()
        add(('search', sku, r.get('location')), 'search', f'Azure AI Search {sku}',
            dict(region=region_slug(r.get('location'), R), tier=SEARCH_TIERS.get(sku, 'basic'), units=int((r.get('properties') or {}).get('replicaCount') or 1), hours=730),
            [sku.title() if sku != 'free' else 'Free'])
    for r in rtype('microsoft.web/staticsites'):
        tier = 'standard' if str((r.get('sku') or {}).get('name', '')).lower() == 'standard' else 'free'
        add(('swa', tier), 'app-service\\static', f'Static Web Apps {tier}', dict(tier=tier), [tier.title()])
    for k in list(groups):
        if k[0] == 'swa':
            groups[k]['set']['standardApps'] = groups[k]['count']; groups[k]['count'] = 1
    billed_slugs = {i['slug'] for i in items if i['type'] == 'calc'}
    for r in ([] if 'cosmos-db' in billed_slugs else rtype('microsoft.documentdb/databaseaccounts')):
        serverless = 'EnableServerless' in json.dumps((r.get('properties') or {}).get('capabilities') or []) or \
            str((r.get('properties') or {}).get('capacityMode', '')).lower() == 'serverless'
        add(('cosmos', r.get('name')), 'cosmos-db', f"Azure Cosmos DB {r.get('name')} ({'serverless' if serverless else 'provisioned'}, usage 0)",
            dict(region=region_slug(r.get('location'), R), databaseOperation='serverless' if serverless else 'standard',
                 serverlessRegion=region_slug(r.get('location'), R)), ['Cosmos DB'])
    from_bill = {i['type'] for i in items}
    law = [] if 'law' in from_bill else rtype('microsoft.operationalinsights/workspaces')
    alerts = len(rtype('microsoft.insights/scheduledqueryrules'))
    if ('monitor',) in groups and alerts:
        groups[('monitor',)]['set']['logAlerts'] = alerts
    elif law or alerts:
        cap = 0.0
        for w in law:
            c = ((w.get('properties') or {}).get('workspaceCapping') or {}).get('dailyQuotaGb')
            if c not in (None, -1, '-1'):
                cap += float(c)
        reg = law[0].get('location') if law else default_region
        add(('monitor',), 'monitor', f"Azure Monitor: {len(law)} Log Analytics workspace(s), {cap:g} GB/day (daily cap), {alerts} log alert rules",
            dict(region=region_slug(reg, R), dailyLogsIngested=cap, logAnalyticsRetention=1, logAlerts=alerts), ['Log analytics'])
    mde = sum(1 for r in inventory if str(r.get('type', '')).lower().endswith('/extensions')
              and str(r.get('name', '')).split('/')[-1].upper().startswith('MDE.')
              and str(r.get('id', '')).lower().split('/extensions/')[0] in {it.get('resource_id') for it in items})
    if mde and 'defender' not in from_bill:
        plan = 'serversPlan2Nodes' if defender_plan == 'p2' else 'serversPlan1Nodes'
        add(('defender',), 'defender-for-cloud', f"Defender for Servers Plan {defender_plan[-1]}: {mde} servers",
            {'region': 'uae-north', 'planType': 'workloadprotection', plan: mde}, [f'{mde} Plan {defender_plan[-1]}'])
    simple = {'microsoft.keyvault/vaults': ('key-vault', 'Key Vault (operations 0)'),
              'microsoft.logic/workflows': ('logic-apps', 'Logic Apps Consumption (executions 0)'),
              'microsoft.communication/communicationservices': ('communication-services', 'Azure Communication Services (usage 0)'),
              'microsoft.cognitiveservices/accounts': ('openai-service', 'Azure OpenAI / AI services (tokens 0)')}
    billed_kinds = {i['slug'] for i in items if i['type'] == 'calc'}
    for t, (slug, lab) in simple.items():
        n = len(rtype(t)) if slug not in billed_kinds else 0
        if n:
            f = {'standardPlan': 'consumption'} if slug == 'logic-apps' else {}
            add(('simple', slug), slug, f'{n} x {lab}', f, [])
    fn = [r for r in rtype('microsoft.web/serverfarms') if re.match(r'(?i)^(FC1|Y1)', str((r.get('sku') or {}).get('name', '')))]
    if fn and 'functions' not in billed_slugs:
        add(('functions',), 'functions', f'{len(fn)} x Azure Functions consumption / Flex (executions 0)', {}, [])
    lines = []
    for g in groups.values():
        if g.get('fixed'):
            pass
        elif g['slug'] in ('virtual-machines',):
            g['set']['count'] = g['count']
            h = round(g['hours'] / max(1, g['count']), 1)
            if h < 729.5:                                  # billed hours of that month (month mode): average per VM
                g['set']['hours'] = h; g['label'] += f' ({h:g} h each)'
        elif g['slug'] == 'managed-disks':
            g['set']['managedDisks'] = g['count']
        elif g['slug'] == 'ip-addresses':
            g['set']['staticAddresses'] = g['count']
        elif g['slug'] == 'app-service':
            g['set']['instances'] = g['count']
        elif g['slug'] in ('azure-sql-database',):
            g['set']['singleCount'] = g['count']; g['set']['managedCount'] = g['count']
        elif g['slug'] == 'azure-sql':
            g['set']['managedCount'] = g['count']
        elif g['slug'] == 'azure-firewall':
            g['set'][g['set']['tier'] + 'LogicalFirewallUnits'] = g['count']
        elif g['slug'] == 'kubernetes-service':
            g['set']['clusterCount'] = g['count']
        lines.append(g)
    order = ['virtual-machines', 'managed-disks', 'storage', 'azure-sql-database', 'azure-sql', 'app-service', 'kubernetes-service', 'ip-addresses']
    lines.sort(key=lambda g: (order.index(g['slug']) if g['slug'] in order else 99, g['label']))
    return lines, notes


JS_DB = """
const db = await new Promise((res, rej) => { const r = indexedDB.open('localforage'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error) });
const store = m => db.transaction('keyvaluepairs', m).objectStore('keyvaluepairs');
const get = k => new Promise(r => { const q = store('readonly').get(k); q.onsuccess = () => r(q.result) });
const put = (k, v) => new Promise((r, j) => { const q = store('readwrite').put(v, k); q.onsuccess = () => r(true); q.onerror = () => j(q.error) });
"""


class DirectCalculator(ca.CalculatorAgent):
    """Deterministic calculator filler (no language model). Reuses the agent's Edge profile, sign-in and share code."""

    def __init__(self, lines, out_dir, session_path, share_fn, **kw):
        super().__init__('direct', None, out_dir, session_path, share_fn, **kw)
        self.lines = lines

    def _db(self, page, body, arg=None):
        return page.evaluate('async (arg) => {' + JS_DB + body + '}', arg)

    def new_estimate(self, page):
        eid = self._db(page, """
const est = (await get('azure_calculator_v3_estimates')) || {};
const act = await get('azure_calculator_v3_active_estimate_id');
const base = est[act] || Object.values(est)[0];
if (!base) return null;
const id = crypto.randomUUID();
est[id] = Object.assign(JSON.parse(JSON.stringify(base)), {id, saved: false, estimateName: arg, modules: []});
await put('azure_calculator_v3_estimates', est); await put('azure_calculator_v3_active_estimate_id', id);
return id;""", self.name)
        if not eid:
            raise ca.AgentError('The calculator storage was not found (the page may have changed). No link was created.')
        self._reload(page)
        return eid

    def _reload(self, page):
        page.goto(self.url, wait_until='domcontentloaded', timeout=90000)
        page.get_by_role('textbox', name='Search products', exact=True).wait_for(timeout=90000)
        page.wait_for_timeout(1500)
        self.dismiss_popups(page)

    def modules(self, page):
        return self._db(page, """
const est = await get('azure_calculator_v3_estimates'); const act = await get('azure_calculator_v3_active_estimate_id');
return ((est[act] || {}).modules || []).map(m => ({slug: m.serviceSlug, description: m.currentModuleState.description || '',
  total: m.currentModuleState.total || 0}));""")

    def add_modules(self, page):
        for n, line in enumerate(self.lines, 1):
            if n % 5 == 1:
                self.notify(f'Adding service {n} of {len(self.lines)} to the calculator…')
            ok = page.evaluate("""(slug) => { const b = document.querySelector('button.pickerItem__button--add[data-slug="' + CSS.escape(slug) + '"]');
                                   if (!b) return false; b.click(); return true; }""", line['slug'])
            if not ok:
                raise ca.AgentError(f"The calculator has no '{line['slug']}' service button any more (page changed). No link was created.")
            for _ in range(160):
                if len(self.modules(page)) >= n:
                    break
                page.wait_for_timeout(250)
            else:
                raise ca.AgentError(f"Adding '{line['label']}' did not create a calculator module. No link was created.")
            self.dismiss_popups(page)

    def configure(self, page):
        mods = self.modules(page)
        got = [m['slug'] for m in mods]
        want = [l['slug'] for l in self.lines]
        if got != want:
            raise ca.AgentError(f'Calculator modules {got[:5]}… do not match the request {want[:5]}…. No link was created.')
        self._db(page, """
const est = await get('azure_calculator_v3_estimates'); const act = await get('azure_calculator_v3_active_estimate_id');
const applySet = (st, set) => { if (set.cccZero) { for (const k of Object.keys(st)) { if (typeof st[k] === 'number' && /(count|units|instances|hours|nodes|users|servers|vcores|capacity|quantity|gb|operations|executions|messages|requests|transactions|ingested|devices|seats|minutes|calls|circuits|resources|policyunits)$/i.test(k) && !/factor$/i.test(k)) st[k] = 0; } } for (const [k, v] of Object.entries(set)) { if (k !== 'cccZero') st[k] = v; } };
est[act].modules.forEach((m, i) => applySet(m.currentModuleState, arg[i]));
est[act].estimateName = arg.name || est[act].estimateName;
await put('azure_calculator_v3_estimates', est); return true;""", [l['set'] for l in self.lines])
        self.notify('Configured every service; the calculator is recalculating prices…')
        self._reload(page)
        page.wait_for_timeout(4000)

    def verify(self, page):
        mods = self.modules(page)
        rows, problems = [], []
        for line, m in zip(self.lines, mods):
            miss = [c for c in line['check'] if c.lower() not in m['description'].lower()]
            cnt = line['count']
            if line['slug'] == 'virtual-machines' and not re.match(rf'^\s*{cnt}\s', m['description']):
                miss.append(f'count {cnt}')
            if line['slug'] == 'managed-disks' and f'{cnt} Disks' not in m['description'] and not (cnt == 1 and '1 Disks' in m['description']):
                miss.append(f'{cnt} disks')
            if miss:
                problems.append(f"{line['label']}: calculator shows '{m['description'][:120]}' (expected {', '.join(miss)})")
            rows.append({'service': line['label'], 'count': cnt, 'calculator': m['description'][:300], 'monthly': round(m['total'], 2), 'ok': not miss})
        if len(mods) != len(self.lines):
            problems.append(f'{len(mods)} modules in the calculator, {len(self.lines)} expected')
        return rows, problems

    def run(self):
        from playwright.sync_api import sync_playwright
        result = {'status': 'failed', 'url': None, 'rows': [], 'problems': [], 'total': None}
        self.notify('Opening the Azure Pricing Calculator in the automation Edge window (no AI is used)…')
        with sync_playwright() as p:
            browser, context, page = self._open(p)
            page.set_default_timeout(30000)
            try:
                page.goto(self.url, wait_until='domcontentloaded', timeout=90000)
                page.get_by_role('textbox', name='Search products', exact=True).wait_for(timeout=90000)
                try:
                    page.get_by_role('button', name=re.compile(r'^(Accept|Reject)', re.I)).first.click(timeout=3000)
                except Exception:
                    pass
                if self.sign_in_first:
                    self.notify('Checking the Azure Calculator sign-in (needed to save and share)…')
                    page = self.ensure_signed_in(page)
                    page.set_default_timeout(30000)
                    self._save_session(context)
                self._reload(page)
                self.new_estimate(page)
                self.notify(f'New empty estimate created. Adding {len(self.lines)} services…')
                self.add_modules(page)
                self.configure(page)
                rows, problems = self.verify(page)
                result.update(rows=rows, problems=problems, total=self.observe(page)['total'])
                (self.dir / 'calculator-lines.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
                if problems:
                    self.notify(f'{len(problems)} service(s) did not match exactly; see the report. Saving anyway…')
                page.screenshot(path=str(self.dir / 'estimate.png'), full_page=True)
                try:
                    page.locator('#estimate-name').fill(self.name, timeout=5000)
                except Exception:
                    pass
                page = self.ensure_signed_in(page)
                self.notify('Saving the estimate and creating the share link…')
                url = self.share_fn(page, self.dir, ca._Audit(self.dir), self.wait_for_login, lambda m: self.notify(m))
                result.update(status='completed', url=url)
                self._save_session(context)
                return result
            except Exception as e:
                result['error'] = ca._friendly(e)
                try:
                    page.screenshot(path=str(self.dir / 'failure.png'))
                except Exception:
                    pass
                return result
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
