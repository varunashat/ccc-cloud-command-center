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
