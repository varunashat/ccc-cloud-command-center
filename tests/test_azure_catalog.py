import os, tempfile, unittest
os.environ.setdefault('CCC_STATE', tempfile.mkdtemp())
import azure_catalog as ac, json

class T(unittest.TestCase):
    def test_search_and_stats(self):
        rows = [['Azure Bastion', 'Azure Bastion', 'Standard', '1 Hour', ['uaenorth', 'Global'], ['Standard Gateway'], {'uaenorth': 0.29}],
                ['Virtual Machines', 'VM Dv5', 'D4s v5', '1 Hour', ['uaenorth'], ['D4s v5'], {}]]
        (ac._root() / 'catalog.json').write_text(json.dumps({'generated': '2026-11-26T00:00:00+00:00', 'regions': ['uaenorth', 'Global'], 'rows': rows}))
        ac._CACHE.clear()
        s = ac.stats()
        self.assertEqual((s['services'], s['skus']), (2, 2))
        r = ac.search('bastion standard')
        self.assertEqual(r['total'], 1); self.assertEqual(r['results'][0]['price'], 0.29)
        self.assertEqual(ac.search('', service='Virtual Machines')['total'], 1)
        self.assertEqual(ac.search('', region='Global')['total'], 1)
