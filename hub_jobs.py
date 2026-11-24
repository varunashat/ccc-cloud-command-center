"""Customer-scoped, resumable Azure assessment jobs stored on the mounted share."""
import csv
import hashlib
import io
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from discovery import azure_discovery as azure
from supervisor import save, read

ROOT = Path(os.environ.get('CCC_STATE', '/state')) / 'assessment-hub-v1'
WORKFLOWS = ('infrastructure', 'security', 'cost', 'modernization')
_LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def scope(body):
    tenant = str(uuid.UUID(str(body.get('tenant'))))
    subscriptions = sorted(azure.check_subscriptions(body.get('subscriptions')))
    return {'tenant': tenant, 'subscriptions': subscriptions}


def authorized(selected, call):
    for sub in selected['subscriptions']:
        account = call('GET', f'{azure.ARM}/subscriptions/{sub}?api-version=2022-12-01', tenant=selected['tenant'])
        if (str(account.get('tenantId', '')).lower() != selected['tenant']
                or str(account.get('subscriptionId', '')).lower() != sub
                or account.get('state') != 'Enabled'):
            raise ValueError('The selected subscription is unavailable in this directory.')


def folder(owner):
    return ROOT / hashlib.sha256(owner.lower().encode()).hexdigest()


def result(path):
    job = read(path / 'request.json')
    sections = {}
    for workflow in job['workflows']:
        checkpoint = path / (workflow + '.json')
        if checkpoint.exists():
            sections[workflow] = read(checkpoint)
    state = 'Queued'
    if (path / 'cancelled').exists():
        state = 'Cancelled'
    elif (path / 'paused').exists():
        state = 'Paused'
    elif (path / 'blocked.json').exists():
        state = 'Needs sign-in'
    elif len(sections) == len(job['workflows']):
        state = 'Completed with gaps' if any(s['status'] not in ('available', 'empty') for s in sections.values()) else 'Completed'
    elif sections:
        state = 'In progress'
    return {**{key: value for key, value in job.items() if key != 'owner'}, 'id': path.name,
            'status': state, 'sections': sections,
            'pending': read(path / 'blocked.json') if (path / 'blocked.json').exists() else None}


def dispatch(owner, body, call):
    selected = scope(body)
    authorized(selected, call)
    action = body.get('action')
    if action == 'list':
        jobs = []
        for request in folder(owner).glob('*/request.json'):
            saved = read(request)
            if saved['scope'] == selected:
                jobs.append(result(request.parent))
        return {'jobs': sorted(jobs, key=lambda job: job['created'], reverse=True)}
    path = folder(owner) / uuid.UUID(str(body.get('id'))).hex
    with _LOCK:
        if action == 'start':
            workflows = body.get('workflows')
            if not isinstance(workflows, list) or not workflows or any(w not in WORKFLOWS for w in workflows):
                raise ValueError('Choose a supported assessment.')
            workflows = list(dict.fromkeys(workflows))
            if (path / 'request.json').exists():
                old = read(path / 'request.json')
                if old['scope'] != selected or old['workflows'] != workflows:
                    raise ValueError('This job ID already has a different scope or plan.')
            else:
                path.mkdir(parents=True, exist_ok=True)
                customer = str(body.get('customer') or selected['tenant'])[:160]
                save(path / 'request.json', {'owner': owner, 'customer': customer, 'scope': selected,
                                           'created': now(), 'workflows': workflows})
        else:
            if not (path / 'request.json').exists() or read(path / 'request.json')['scope'] != selected:
                raise ValueError('Run unavailable in the selected scope.')
            if action == 'pause':
                (path / 'paused').touch()
            elif action == 'cancel':
                (path / 'cancelled').touch()
            elif action == 'resume':
                (path / 'paused').unlink(missing_ok=True)
                (path / 'blocked.json').unlink(missing_ok=True)
            elif action != 'status':
                raise ValueError('Unknown run action.')
        return result(path)


QUERIES = {
    'infrastructure': "Resources | project id,name,type,location,resourceGroup,subscriptionId,kind,sku,properties=bag_pack('vmSize',properties.hardwareProfile.vmSize,'availabilityZones',zones,'availabilitySet',properties.availabilitySet.id,'subnet',properties.ipConfigurations[0].properties.subnet.id,'nics',properties.networkProfile.networkInterfaces)",
    'security': "Resources | where type in~ ('microsoft.network/publicipaddresses','microsoft.network/networksecuritygroups','microsoft.network/routetables','microsoft.network/azurefirewalls','microsoft.network/applicationgateways') | project id,name,type,location,resourceGroup,subscriptionId,properties=bag_pack('ipAddress',properties.ipAddress,'securityRules',properties.securityRules,'routes',properties.routes,'waf',properties.webApplicationFirewallConfiguration)",
    'modernization': "Resources | where type in~ ('microsoft.compute/virtualmachines','microsoft.hybridcompute/machines') | project id,name,type,location,subscriptionId,resourceGroup,properties=bag_pack('vmSize',properties.hardwareProfile.vmSize,'osType',properties.storageProfile.osDisk.osType,'image',properties.storageProfile.imageReference,'osName',properties.osName)",
}


def collect(workflow, selected, call):
    tenant, subs = selected['tenant'], selected['subscriptions']
    findings, gaps, rows, sources = [], [], [], []
    if workflow == 'cost':
        advisor_query = "AdvisorResources | where type =~ 'microsoft.advisor/recommendations' | project id,subscriptionId,properties"
        try:
            recommendations = azure.rg_query(call, subs, advisor_query, tenant)
            findings.append({'title': 'Azure Advisor recommendations', 'evidence': recommendations})
            sources.append({'source': 'Azure Advisor', 'status': 'available' if recommendations else 'empty'})
        except ValueError as error:
            gaps.append(str(error))
            sources.append({'source': 'Azure Advisor', 'status': 'unavailable'})
        for sub in subs:
            try:
                rows.extend(azure.cost_query(call, sub, 30, tenant))
                sources.append({'source': 'Azure Cost Management', 'subscription': sub, 'status': 'available'})
            except ValueError as error:
                gaps.append(str(error))
                sources.append({'source': 'Azure Cost Management', 'subscription': sub, 'status': 'unavailable'})
        gaps.append('Utilization metrics, reservation and Savings Plan eligibility require further evidence before savings can be calculated.')
    else:
        rows = azure.rg_query(call, subs, QUERIES[workflow], tenant)
        sources.append({'source': 'Azure Resource Graph', 'status': 'empty' if not rows else 'available'})
        if workflow == 'infrastructure':
            gaps.extend(['Backup and recovery coverage requires vault and protected-item evidence.',
                         'Availability and dependency checks require workload objectives and application dependencies.'])
        elif workflow == 'security':
            gaps.append('Configured rules describe possible traffic paths. Observed traffic requires flow or firewall logs.')
            try:
                defender = azure.rg_query(call, subs, "SecurityResources | where type =~ 'microsoft.security/assessments' | project id,subscriptionId,displayName=properties.displayName,status=properties.status.code", tenant)
                findings.append({'title': 'Defender assessments', 'evidence': defender})
            except ValueError as error:
                gaps.append(str(error))
        else:
            gaps.append('Application runtime, installed software, inbound dependencies, data persistence and licensing need discovery before container or PaaS readiness can be determined.')
    status = 'partial' if any(s['status'] == 'unavailable' for s in sources) else ('available' if rows else 'empty')
    return {'workflow': workflow, 'captured_at': now(), 'scope': selected, 'status': status,
            'rows': rows, 'findings': findings, 'gaps': gaps, 'sources': sources,
            'estimated_savings': None, 'period_days': 30 if workflow == 'cost' else None}


def process(caller_for):
    for request in ROOT.glob('*/*/request.json'):
        path = request.parent
        job = read(request)
        if any((path / flag).exists() for flag in ('cancelled', 'paused', 'blocked.json')):
            continue
        for workflow in job['workflows']:
            output = path / (workflow + '.json')
            if output.exists():
                continue
            if any((path / flag).exists() for flag in ('cancelled', 'paused')):
                break
            try:
                call = caller_for(job['owner'])
                authorized(job['scope'], call)
            except Exception:
                save(path / 'blocked.json', {'message': 'Refresh your Microsoft sign-in, then resume this run.', 'at': now()})
                break
            try:
                section = collect(workflow, job['scope'], call)
            except Exception as error:
                logging.warning('Assessment collection failed: %s', type(error).__name__)
                section = {'workflow': workflow, 'scope': job['scope'], 'captured_at': now(),
                           'status': 'unavailable', 'rows': [], 'findings': [], 'sources': [],
                           'gaps': [str(error)[:500]], 'estimated_savings': None}
            save(output, section)


def export_csv(section):
    stream = io.StringIO(newline='')
    fields = sorted({key for row in section['rows'] for key in row})
    writer = csv.writer(stream)
    writer.writerow(['Tenant', 'Subscriptions', 'Captured at', *fields])
    for row in section['rows']:
        values = [section['scope']['tenant'], ', '.join(section['scope']['subscriptions']), section['captured_at']]
        values.extend(json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (dict, list)) else str(row.get(key, '')) for key in fields)
        writer.writerow(["'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value for value in values])
    return stream.getvalue()
