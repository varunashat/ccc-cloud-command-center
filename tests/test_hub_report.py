"""Report numbers come from evidence; model findings citing unknown resources are dropped."""
import json
import unittest

import hub_report as report

SUB = '00000000-0000-4000-8000-000000000002'
RID = f'/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1'


def job():
    rows = [{'id': RID, 'name': 'vm1', 'type': 'microsoft.compute/virtualmachines', 'location': 'uaenorth',
             'resourceGroup': 'rg', 'subscriptionId': SUB, 'properties': {'vmSize': 'Standard_D4s_v5', 'availabilityZones': None}}]
    section = {'workflow': 'infrastructure', 'status': 'available', 'captured_at': '2026-11-24T10:00:00Z', 'rows': rows,
               'findings': [], 'gaps': ['Backup coverage needs vault evidence.'], 'sources': []}
    cost = {'workflow': 'cost', 'status': 'available', 'captured_at': '2026-11-24T10:00:00Z', 'findings': [], 'gaps': [], 'sources': [],
            'rows': [{'Cost': 10.5, 'ServiceName': 'Virtual Machines', 'Currency': 'USD', 'subscriptionId': SUB},
                     {'Cost': 4.5, 'ServiceName': 'Storage', 'Currency': 'USD', 'subscriptionId': SUB}]}
    return {'id': 'abc', 'customer': 'Contoso', 'status': 'Completed', 'scope': {'tenant': 't', 'subscriptions': [SUB]},
            'sections': {'infrastructure': section, 'cost': cost}}


class ReportTest(unittest.TestCase):
    def test_facts_are_computed_from_rows(self):
        j = job()
        self.assertEqual(report.facts(j['sections']['infrastructure'])['vms_without_zone_count'], 1)
        self.assertEqual(report.facts(j['sections']['cost'])['total_30_days'], 15.0)

    def test_unsupported_findings_are_dropped(self):
        ids = {RID.lower()}
        clean, dropped = report.validate({'summary': 's', 'findings': [
            {'title': 'ok', 'evidence_ids': [RID], 'detail': 'd', 'recommendation': 'r'},
            {'title': 'invented', 'evidence_ids': ['/subscriptions/x/fake'], 'detail': 'd', 'recommendation': 'r'}]}, ids)
        self.assertEqual([f['title'] for f in clean['findings']], ['ok'])
        self.assertEqual(dropped, 1)

    def test_pdf_builds_with_and_without_ai(self):
        j = job()
        self.assertTrue(report.build(j).startswith(b'%PDF'))
        def fake(messages, max_tokens):
            return json.dumps({'summary': 'One VM has no zone.', 'findings': [
                {'title': 'No zone', 'severity': 'Medium', 'detail': 'vm1 is zonal-less', 'evidence_ids': [RID], 'recommendation': 'Move to zone'}]}), 'gpt'
        self.assertTrue(report.build(j, ai=fake).startswith(b'%PDF'))
        def missing(messages, max_tokens):
            raise LookupError('not_configured')
        self.assertTrue(report.build(j, ai=missing).startswith(b'%PDF'))

    def test_empty_run_is_refused(self):
        j = job(); j['sections'] = {}
        with self.assertRaises(ValueError):
            report.build(j)


if __name__ == '__main__':
    unittest.main()
