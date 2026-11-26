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
