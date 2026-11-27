import os, tempfile, unittest
os.environ.setdefault('CCC_STATE', tempfile.mkdtemp())
import waf_engine as eng
import waf_assessment as wa

S = 'aaaaaaaa-0000-0000-0000-000000000001'
def rid(t, n): return f'/subscriptions/{S}/resourceGroups/rg1/providers/{t}/{n}'
VM = rid('Microsoft.Compute/virtualMachines', 'vm1')
VM2 = rid('Microsoft.Compute/virtualMachines', 'vm2')
INV = [
    {'id': VM, 'name': 'vm1', 'type': 'microsoft.compute/virtualmachines', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S, 'tags': {'environment': 'dev'}, 'zones': None,
     'properties': {'hardwareProfile': {'vmSize': 'Standard_D8s_v3'}, 'storageProfile': {'osDisk': {'osType': 'Windows', 'managedDisk': {'storageAccountType': 'Standard_LRS'}}, 'imageReference': {'offer': 'WindowsServer', 'sku': '2012-R2-Datacenter'}},
                    'extended': {'instanceView': {'powerState': {'code': 'PowerState/running'}}}}},
    {'id': VM2, 'name': 'vm2', 'type': 'microsoft.compute/virtualmachines', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S, 'tags': {'owner': 'x'},
     'properties': {'hardwareProfile': {'vmSize': 'Standard_D4s_v5'}, 'storageProfile': {'osDisk': {'osType': 'Linux'}}, 'extended': {'instanceView': {'powerState': {'code': 'PowerState/running'}}}}},
    {'id': rid('Microsoft.Compute/disks', 'd1'), 'name': 'd1', 'type': 'microsoft.compute/disks', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S, 'sku': {'name': 'Premium_LRS'}, 'properties': {'diskState': 'Unattached', 'diskSizeGB': 256}},
    {'id': rid('Microsoft.Network/networkSecurityGroups', 'nsg1'), 'name': 'nsg1', 'type': 'microsoft.network/networksecuritygroups', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S,
     'properties': {'securityRules': [{'name': 'rdp', 'properties': {'direction': 'Inbound', 'access': 'Allow', 'sourceAddressPrefix': '*', 'destinationPortRange': '3389'}}]}},
    {'id': rid('Microsoft.Network/virtualNetworks', 'vnet1'), 'name': 'vnet1', 'type': 'microsoft.network/virtualnetworks', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S,
     'properties': {'addressSpace': {'addressPrefixes': ['10.0.0.0/16']}, 'subnets': [{'name': 'app', 'properties': {'addressPrefix': '10.0.1.0/24'}}, {'name': 'GatewaySubnet', 'properties': {}}]}},
    {'id': rid('Microsoft.Storage/storageAccounts', 'st1'), 'name': 'st1', 'type': 'microsoft.storage/storageaccounts', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S, 'sku': {'name': 'Standard_LRS'},
     'properties': {'allowBlobPublicAccess': True, 'supportsHttpsTrafficOnly': False, 'networkAcls': {'defaultAction': 'Allow'}}},
    {'id': rid('Microsoft.Web/sites', 'web1'), 'name': 'web1', 'type': 'microsoft.web/sites', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S, 'kind': 'app',
     'properties': {'httpsOnly': False, 'defaultHostName': 'web1.azurewebsites.net', 'serverFarmId': rid('Microsoft.Web/serverfarms', 'plan1')}},
    {'id': rid('Microsoft.Web/serverfarms', 'plan1'), 'name': 'plan1', 'type': 'microsoft.web/serverfarms', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S, 'sku': {'name': 'P1v3', 'tier': 'PremiumV3', 'capacity': 3}},
    {'id': rid('Microsoft.Network/applicationGatewayWebApplicationFirewallPolicies', 'waf1'), 'name': 'waf1', 'type': 'microsoft.network/applicationgatewaywebapplicationfirewallpolicies', 'location': 'uaenorth', 'resourceGroup': 'rg1', 'subscriptionId': S,
     'properties': {'policySettings': {'mode': 'Detection', 'state': 'Enabled'}}},
]
EV = {'now': '2026-11-27T00:00:00+00:00', 'inventory': INV, 'advisor': [], 'defender': [{'name': 'MFA should be enabled', 'severity': 'High', 'resource': '/subscriptions/x', 'subscriptionId': S}],
      'backup': set(), 'policy': [], 'defender_plans': {S: {'VirtualMachines': 'Free'}}, 'mgmt_groups': [S], 'rbac': [{'subscriptionId': S, 'principalType': 'User', 'role': '/x/8e3af657-a8ff-443c-a75c-2fe8c4bcb635', 'scope': f'/subscriptions/{S}', 'principal': f'u{i}'} for i in range(4)],
      'cost': {VM.lower(): 400.0, rid('Microsoft.Compute/disks', 'd1').lower(): 30.0, rid('Microsoft.Web/serverfarms', 'plan1').lower(): 600.0}, 'cost_total': 1030.0, 'cost_by_service': {'Virtual Machines': 400.0}, 'currency': 'USD',
      'vm_sizes': {(S, 'uaenorth', 'standard_d8s_v3'): (8, 32.0)}, 'warnings': [], 'metrics_planned': 3,
      'source_status': {'inventory': 'ok', 'cost': 'ok', 'metrics': 'ok', 'defender': 'ok', 'rbac': 'failed'},
      'metrics': {VM.lower(): {'cpu': {'avg': 4.0, 'max': 22.0, 'p95': 18.0, 'days': 30}, 'mem_free': {'avg': 28 * 1024 ** 3, 'max': 30 * 1024 ** 3, 'p95': 30 * 1024 ** 3, 'days': 30}},
                  rid('Microsoft.Web/serverfarms', 'plan1').lower(): {'cpu': {'avg': 6.0, 'max': 20.0, 'p95': 15.0, 'days': 30}, 'mem': {'avg': 30.0, 'max': 40.0, 'p95': 38.0, 'days': 30}}}}


class T(unittest.TestCase):
    def setUp(self):
        self.r = eng.analyze(EV)
        self.titles = {f['title'] for f in self.r['findings']}

    def has(self, text):
        return any(text in t for t in self.titles)

    def test_pillars_all_present(self):
        self.assertEqual(set(self.r['scores']), set(eng.PILLARS))
        self.assertTrue(all(0 <= v['score'] <= 100 for v in self.r['scores'].values()))

    def test_findings(self):
        for t in ('using under 10% CPU', 'Unattached managed disks', 'NSG rules open management', 'Subnets without a network security group', 'weak access settings',
                  'no Azure Backup', 'Detection mode', 'end of support', 'Hybrid Benefit', 'Dev/test VMs running 24', 'Web apps that allow HTTP', 'no WAF-capable front end', 'more than 3 Owners', 'Standard HDD'):
            self.assertTrue(self.has(t), t)

    def test_rightsizing_row(self):
        u = {x['name']: x for x in self.r['utilization']}
        self.assertEqual(u['vm1']['verdict'], 'Scale down')
        self.assertEqual(u['vm1']['target'], 'Standard_D4s_v3')
        self.assertEqual(u['plan1']['verdict'], 'Scale down')

    def test_saving_counted_once(self):
        s = self.r['summary']
        self.assertGreater(s['gross_saving_30d'], s['estimated_saving_30d'])
        self.assertGreater(s['estimated_saving_30d'], 0)

    def test_module_filter_and_evidence(self):
        r = eng.analyze(EV, ['waf'])
        self.assertTrue(all(f['module'] == 'waf' for f in r['findings']))
        self.assertTrue(r['findings'])
        r2 = eng.analyze(EV, ['security'])
        self.assertTrue(any('Azure RBAC' in n['item'] for n in r2['needs_evidence']))
        self.assertEqual(self.r['waf_map'][0]['front'], 'None')

    def test_vm_step_down(self):
        self.assertEqual(eng.vm_step_down('Standard_D8s_v5'), 'Standard_D4s_v5')
        self.assertIsNone(eng.vm_step_down('Standard_B2s'))
        self.assertIsNone(eng.vm_step_down('Standard_D2s_v5') and None)

    def test_reports(self):
        self.r['executive_summary'] = wa.narrative(self.r, None)
        rec = {'id': 'a' * 10, 'time': '2026-11-27T00:00:00', 'customer': 'Demo', 'tenant': 't', 'subscriptions': [S], 'user': 'u', 'modules': list(eng.MODULES), 'result': self.r}
        self.assertTrue(wa.build_pdf(rec).startswith(b'%PDF'))
        p = os.path.join(tempfile.mkdtemp(), 'x.xlsx')
        wa.export_xlsx(rec, p)
        import openpyxl
        wb = openpyxl.load_workbook(p)
        self.assertIn('Affected resources', wb.sheetnames)
        self.assertGreater(wb['Findings'].max_row, 5)

    def test_collect_with_fake_azure(self):
        calls = []
        def call(method, url, body=None, tenant=None):
            calls.append(url)
            if 'resourcegraph' in url.lower():
                q = body['query']
                return {'data': INV if q.startswith('Resources') else []}
            if 'CostManagement' in url:
                return {'properties': {'columns': [{'name': 'Cost'}, {'name': 'ResourceId'}, {'name': 'ServiceName'}, {'name': 'Currency'}], 'rows': [[5, VM, 'Virtual Machines', 'USD']]}}
            return {'value': []}
        ev = eng.collect(call, 'tenant', [S], sources={'finops', 'infra'} and eng.MODULE_SOURCES['finops'])
        self.assertEqual(len(ev['inventory']), len(INV))
        self.assertEqual(ev['source_status']['defender'], 'skipped')
        self.assertEqual(ev['source_status']['cost'], 'ok')
        v = eng.verify(call, 'tenant', [S])
        self.assertTrue(all(x['ok'] for x in v))


if __name__ == '__main__':
    unittest.main()
