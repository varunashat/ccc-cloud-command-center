import os, tempfile, unittest
os.environ.setdefault('CCC_STATE', tempfile.mkdtemp())
import partner_azure as pa

CSV = (b"Month,CustomerTenantName,CustomerTpid,SalesChannel,PartnerAttributionType,EnrollmentNumber,ServiceLevel1,ServiceLevel2,ACR_USD,PartnerName\n"
       b"2026-09,Acme,1,EA (Global),Partner of record,E1,Compute,VM,1000,Seven Seas Technology\n"
       b"2026-10,Acme,1,EA (Global),Partner of record,E1,Compute,VM,1500,Seven Seas Technology\n"
       b"2026-10,Beta,2,Direct,CSP Tier 1,,Storage,Blob,300,Seven Seas Technology\n"
       b"2026-10,Other,3,Direct,CSP Tier 1,,Storage,Blob,900,Someone Else\n")

class T(unittest.TestCase):
    def test_parse_and_classify(self):
        r = pa.parse_file(CSV, 'x.csv')
        self.assertEqual(r['months'], ['2026-09', '2026-10'])
        self.assertEqual(len(r['rows']), 3)               # other partner dropped
        self.assertEqual(pa.classify('EA (Global)', '', ''), 'EA')
        self.assertEqual(pa.classify('Direct', 'CSP Tier 1', ''), 'CSP')
    def test_dataset(self):
        r = pa.parse_file(CSV, 'x.csv')
        d = pa.build_dataset(r['rows'], [])
        acme = [c for c in d['customers'] if c['name'] == 'Acme'][0]
        self.assertEqual(acme['cls'], 'EA'); self.assertEqual(acme['mom'], 50.0)
        self.assertEqual(d['by_month']['2026-10'], 1800)
    def test_bad_file(self):
        with self.assertRaises(ValueError): pa.parse_file(b"a,b\n1,2\n", 'bad.csv')
    def test_months(self):
        for v, m in (('Oct-2026', '2026-10'), ('2026-10-01', '2026-10'), ('10/1/2026', '2026-10'), ('202610', '2026-10')):
            self.assertEqual(pa.parse_month(v), m)

class T2(unittest.TestCase):
    def test_channel_map_and_dup(self):
        m = pa.parse_file(b"Month,ServiceLevel1,CustomerTenantName,CustomerTpid,SalesChannel\nOct-2026,Compute,Acme Corp LLC,1,EA (Global)\n", 'm.csv')
        self.assertEqual(m['kind'], 'map')
        self.assertEqual(pa.match_channel('ACME CORP', m['map']), {'EA (Global)': 1})
        u = pa.parse_file(b"PartnerName,Month,CustomerName,CustomerTenantId,PartnerAttributionType,IsACRDuplicateAtPGALevel,ACR_USD\n"
                          b"Seven Seas Technology,Oct-2026,Acme Corp LLC,t1,Partner Admin Link,0,100\n"
                          b"Seven Seas Technology,Oct-2026,Acme Corp LLC,t1,CSP Tier 2,1,100\n", 'u.csv')
        d = pa.build_dataset(u['rows'], [], m['map'])
        c = d['customers'][0]
        self.assertEqual(c['cls'], 'EA'); self.assertEqual(c['by_month']['2026-10'], 200); self.assertEqual(c['uniq_by_month']['2026-10'], 100)
