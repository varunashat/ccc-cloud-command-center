"""Configure the existing worker with the web app's encrypted-cache access."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import requests

SUB = '03a0c620-9c56-4e80-abb4-e91c65b17286'
BASE = 'https://management.azure.com/subscriptions/' + SUB + '/resourceGroups/rg-cloud-command-center/providers/Microsoft.App'
IMAGE = sys.argv[1]
if not IMAGE.startswith('cccacr1a5492.azurecr.io/ccc@sha256:'):
    raise SystemExit('An immutable reviewed image digest is required.')
token = subprocess.check_output([shutil.which('az.cmd'),'account','get-access-token','--subscription',SUB,'--query','accessToken','-o','tsv'],text=True).strip()
session = requests.Session()
session.verify = str(Path(os.environ['LOCALAPPDATA'])/'CloudCommandCenter-DMS/windows-trust.pem')
session.headers.update({'Authorization':'Bearer '+token})

def api(method, suffix, payload=None):
    response = session.request(method,BASE+suffix+'?api-version=2025-07-01',json=payload,timeout=90)
    if response.status_code >= 400:
        raise RuntimeError('Azure worker configuration failed with HTTP '+str(response.status_code))
    return response.json()

app = api('GET','/containerApps/cloud-command-center')
if app['properties']['latestReadyRevisionName'] != 'cloud-command-center--0000027':
    raise SystemExit('Review the changed production baseline before deployment.')
job = api('GET','/jobs/ccc-supervisor-worker')
app_env = {entry['name']:entry for entry in app['properties']['template']['containers'][0]['env']}
secret_name = app_env['CCC_SECRET']['secretRef']
secrets = api('POST','/containerApps/cloud-command-center/listSecrets')['value']
secret = next(entry['value'] for entry in secrets if entry['name']==secret_name)
properties = job['properties']
configuration = properties['configuration']
configuration['secrets'] = [{'name':'ccc-cache-secret','value':secret}]
configuration['replicaTimeout'] = 300
container = properties['template']['containers'][0]
container.update(image=IMAGE,command=['/usr/local/bin/python'],args=['/app/hub_worker.py'])
container['env'] = [
 {'name':'CCC_SECRET','secretRef':'ccc-cache-secret'},
 {'name':'CCC_ALLOWED_USERS','value':app_env['CCC_ALLOWED_USERS']['value']},
 {'name':'CCC_STATE','value':'/state'},
 {'name':'CCC_SUPERVISOR_STATE','value':'/state/supervisor-v1'},
]
container['volumeMounts'] = [{'volumeName':'jobs','mountPath':'/state'}]
payload = {key:job[key] for key in ('location','identity')}
payload['properties'] = {key:properties[key] for key in ('environmentId','configuration','template')}
result = api('PUT','/jobs/ccc-supervisor-worker',payload)
print('Existing worker updated:',result['properties'].get('provisioningState'),flush=True)
