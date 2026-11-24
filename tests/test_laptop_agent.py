import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import hub_jobs as hub
import laptop_agent as la

PLAN = {'summary': 'rg', 'target': {'subscription': 's'}, 'steps': ['az group create'],
        'cost_items': [{'service': 'Virtual Machines', 'sku': 'Standard_B2s', 'region': 'uaenorth', 'quantity': 2, 'hours': 730}]}


def fetch(url):
    assert 'Virtual%20Machines' in url or 'Virtual Machines' in url
    return {'Items': [{'retailPrice': 0.05, 'unitOfMeasure': '1 Hour', 'currencyCode': 'USD', 'meterName': 'B2s'}]}


class LaptopAgentTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        p = patch.object(hub, 'ROOT', Path(self.t.name)); p.start(); self.addCleanup(p.stop)
        self.id = str(uuid4())
        la.dispatch('a@x', {'action': 'start', 'id': self.id, 'task': 'make rg', 'consent': True}, None)

    def step(self, reply, **extra):
        return la.dispatch('a@x', {'action': 'step', 'id': self.id, **extra}, lambda m, t: (json.dumps(reply), 'gpt'), fetch)

    def test_plan_is_priced_and_writes_wait_for_approval(self):
        out = self.step({'plan': PLAN, 'commands': [{'tool': 'az', 'args': ['group', 'create', '-n', 'rg1']}]})
        self.assertTrue(out['needs_plan_approval']); self.assertEqual(out['commands'], [])
        self.assertEqual(out['cost']['total_monthly'], 73.0); self.assertTrue(out['cost']['complete'])
        write = {'commands': [{'tool': 'az', 'args': ['group', 'create', '-n', 'rg1']}, {'tool': 'az', 'args': ['group', 'delete', '-n', 'old']},
                              {'tool': 'az', 'args': ['account', 'get-access-token']}, {'tool': 'az', 'args': ['group', 'list']}]}
        held = self.step(write)
        self.assertEqual([c['args'][1] for c in held['commands']], ['list'])
        out = self.step(write, approve_plan=True)
        self.assertEqual([c['kind'] for c in out['commands']], ['write', 'readonly'])
        self.assertEqual(out['held'][0]['args'][1], 'delete')
        self.assertTrue(any('refused' in n for n in out['log'][-1]['notes']))
        approved = self.step(write, approve_commands=[out['held'][0]['key']])
        self.assertIn('delete', [c['args'][1] for c in approved['commands']])

    def test_price_lookup_failure_is_reported_not_guessed(self):
        cost = la.price(PLAN, lambda url: (_ for _ in ()).throw(OSError('offline')))
        self.assertFalse(cost['complete']); self.assertEqual(cost['total_monthly'], 0.0); self.assertIn('failed', cost['lines'][0]['error'])

    def test_repeated_failure_blocks_and_done_needs_plan_approval(self):
        self.step({'plan': PLAN}); self.step({'commands': []}, approve_plan=True)
        cmd = {'tool': 'az', 'args': ['group', 'create', '-n', 'x']}
        key = la.key(cmd)
        for _ in range(3):
            self.step({'commands': [cmd]}, results=[{'ran': True, 'exit': 1, 'error': 'boom', 'key': key}])
        out = self.step({'commands': [cmd]}, results=[{'ran': True, 'exit': 1, 'error': 'boom', 'key': key}])
        self.assertEqual(out['status'], 'blocked'); self.assertIn('3 times', out['blocked'])

    def test_secrets_in_results_are_scrubbed_before_the_model(self):
        seen = []
        la.dispatch('a@x', {'action': 'step', 'id': self.id, 'results': [{'tool': 'az', 'args': ['x'], 'exit': 0, 'stdout': 'AccountKey=SUPERSECRET123=='}]},
                    lambda m, t: (seen.append(m[-1]['content']) or '{"commands":[]}', 'gpt'), fetch)
        self.assertNotIn('SUPERSECRET123', seen[0])


if __name__ == '__main__':
    unittest.main()
