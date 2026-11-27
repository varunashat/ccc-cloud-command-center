import os, tempfile, unittest
os.environ.setdefault('CCC_STATE', tempfile.mkdtemp())
import boq_compliance as bc

BOQ = {'lines': [
    {'type': 'vm', 'name': 'app01', 'region': 'uaenorth', 'config': 'D4s v5, Windows, AHUB', 'quantity': 1},
    {'type': 'vm', 'name': 'db01', 'region': 'uaenorth', 'config': 'E8s v5, Linux', 'quantity': 1},
    {'type': 'public_ip', 'name': 'pip', 'region': 'uaenorth', 'config': 'Standard', 'quantity': 1}]}
DEP = [
    {'type': 'vm', 'name': 'app01', 'region': 'uaenorth', 'sku': 'Standard_D8s_v5', 'os': 'windows', 'ahub': 'yes', 'resource_id': '/x/app01'},
    {'type': 'vm', 'name': 'db01', 'region': 'uaenorth', 'sku': 'Standard_E8s_v5', 'os': 'linux', 'ahub': 'no', 'resource_id': '/x/db01'},
    {'type': 'vm', 'name': 'extra', 'region': 'uaenorth', 'sku': 'Standard_B2s', 'os': 'linux', 'ahub': 'no', 'resource_id': '/x/extra'}]
EV = {'/x/app01': [{'time': '2026-11-20T10:00:00Z', 'by': 'bob@x.com', 'op': 'write virtualMachines', 'delete': False}]}

class T(unittest.TestCase):
    def test_compare(self):
        r = bc.compare(BOQ, DEP, EV)
        self.assertEqual(r['aligned'], 1)
        by = {f['resource']: f for f in r['findings']}
        self.assertEqual(by['app01']['status'], 'Not aligned')
        self.assertEqual(by['app01']['changes'][0]['parameter'], 'VM size')
        self.assertEqual(by['app01']['last_change']['by'], 'bob@x.com')
        self.assertEqual(by['extra']['status'], 'Not in BOQ')
        self.assertEqual(by['pip']['status'], 'In BOQ, not deployed')
    def test_aligned(self):
        r = bc.compare({'lines': BOQ['lines'][1:2]}, DEP[1:2], {})
        self.assertEqual(r['findings'], [])

class T3(unittest.TestCase):
    def test_other_services_and_parse(self):
        boq = {'lines': [{'type': 'other', 'name': 'Bastion', 'service': 'Azure Bastion', 'arm_type': 'microsoft.network/bastionhosts', 'quantity': 1},
                         {'type': 'other', 'name': 'Redis', 'service': 'Azure Cache', 'arm_type': 'microsoft.cache/redis', 'quantity': 1}]}
        inv = [{'id': '/s/b1', 'name': 'b1', 'type': 'Microsoft.Network/bastionHosts'}, {'id': '/s/b2', 'name': 'b2', 'type': 'Microsoft.Network/bastionHosts'},
               {'id': '/s/lb', 'name': 'lb', 'type': 'Microsoft.Network/loadBalancers'}, {'id': '/s/nic', 'name': 'nic', 'type': 'Microsoft.Network/networkInterfaces'}]
        r = bc.compare_other(boq, inv, {})
        st = {(f['status'], f['type']) for f in r['findings']}
        self.assertIn(('Not aligned', 'Azure Bastion'), st)
        self.assertIn(('In BOQ, not deployed', 'Azure Cache'), st)
        self.assertIn(('Not in BOQ', 'microsoft.network/loadbalancers'), st)
        self.assertFalse(any('networkinterfaces' in f['type'] for f in r['findings']))
    def test_parse_estimate(self):
        fake = lambda msgs, n: ('{"project":"P","monthly_total":100,"lines":[{"type":"vm","name":"web","service":"Virtual Machines","region":"UAE North","config":"D4s v5","quantity":2}]}', 'm')
        b = bc.parse_estimate(fake, 'https://azure.com/e/abc123def', {'modules': []}, 'text', '$100.00')
        self.assertEqual(b['lines'][0]['region'], 'uaenorth')
        self.assertEqual(bc.load_baseline(b['id'])['lines'][0]['quantity'], 2.0)
        with self.assertRaises(ValueError): bc.parse_estimate(fake, 'http://evil.example/x', {}, '', '')

class T4(unittest.TestCase):
    def test_map_services(self):
        fake = lambda msgs, n: ('{"items":[{"id":0,"service_name":"Azure Bastion","product_name":"Azure Bastion","sku_name":"Standard","meter_name":""}]}', 'm')
        out = bc.map_services(fake, [{'type': 'microsoft.network/bastionhosts', 'name': 'b', 'location': 'uaenorth', 'sku': 'Standard', 'kind': ''}])
        self.assertEqual(out[0]['service_name'], 'Azure Bastion'); self.assertEqual(out[0]['quantity'], 1); self.assertEqual(out[0]['region'], 'uaenorth')
        self.assertEqual(bc.map_services(fake, []), [])


class T5(unittest.TestCase):
    def test_pdf(self):
        import boq_report as br
        f1 = {'status': 'Not aligned', 'type': 'Virtual machine', 'resource': 'app01', 'resource_id': '/x/app01', 'changes': [{'parameter': 'VM size', 'boq': 'D4s v5', 'deployed': 'D8s v5'}],
              'last_change': {'time': '2026-11-20T10:00:00Z', 'by': 'bob@x.com', 'op': 'write'}, 'created': {'time': '2026-10-01T10:00:00Z', 'by': 'amy@x.com'}}
        f2 = {'status': 'Not in BOQ', 'type': 'Virtual machine', 'resource': 'extra', 'resource_id': '/x/e', 'changes': [{'parameter': 'VM size', 'boq': '', 'deployed': 'B2s'}], 'last_change': None, 'created': None}
        old = {'time': '2026-11-10T00:00:00Z', 'findings': [dict(f1)]}
        rec = {'id': 'a' * 10, 'time': '2026-11-27T00:00:00Z', 'job': 'j', 'tenant': 't', 'subscriptions': ['s'], 'project': 'Demo', 'user': 'u', 'deployed_resources': 5, 'aligned': 3, 'findings': [f1, f2], 'warnings': []}
        pdf = br.build_pdf(rec, BOQ, [old], None, None)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(rec['findings'][0]['first_seen'], '2026-11-10T00:00:00Z')
        pdf2 = br.build_pdf(dict(rec, findings=[]), BOQ, [], None)
        self.assertTrue(pdf2.startswith(b'%PDF'))

class T6(unittest.TestCase):
    def test_pdf_cost(self):
        import boq_report as br
        rec = {'id': 'b' * 10, 'time': '2026-11-27T00:00:00Z', 'job': 'j', 'tenant': 't', 'subscriptions': [], 'project': 'D', 'user': 'u', 'deployed_resources': 1, 'aligned': 0, 'findings': [], 'warnings': []}
        cost = {'status': 'done', 'payg_monthly': 500.0, 'boq_monthly_of_changed': 200.0, 'priced_at': '2026-11-27T01:00:00', 'link': 'https://azure.com/e/abcdef123456', 'link_total': '$300',
                'top': [{'name': 'vm1', 'config': 'D8', 'qty': 1, 'payg': 500}], 'notes': ['x']}
        self.assertTrue(br.build_pdf(rec, BOQ, [], None, cost).startswith(b'%PDF'))
        self.assertTrue(br.build_pdf(rec, BOQ, [], None, {'status': 'running', 'job': 'x'}).startswith(b'%PDF'))
        self.assertIsNone(br.cost_info(rec))
