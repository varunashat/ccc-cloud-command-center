import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import deploy_chat as dc
import hub_jobs as hub

PLAN = {'reply': 'Plan ready', 'questions': [], 'plan': {'title': 'FW', 'summary': 's', 'resources': [{'type': 'firewall', 'name': 'fw1'}],
        'cli_steps': ['az network firewall create ...'], 'portal_steps': ['Open Firewalls'], 'checks_before_run': ['quota'], 'estimated_risks': []}}


class DeployChatTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        p = patch.object(hub, 'ROOT', Path(self.t.name)); p.start(); self.addCleanup(p.stop)
        self.id = str(uuid4())

    def chat(self, messages, tokens):
        return json.dumps(PLAN), 'gpt'

    def test_plan_is_saved_and_consent_required(self):
        dc.dispatch('a@x', {'action': 'new', 'id': self.id, 'mode': 'laptop'}, self.chat)
        with self.assertRaises(ValueError):
            dc.dispatch('a@x', {'action': 'message', 'id': self.id, 'message': 'deploy firewall'}, self.chat)
        out = dc.dispatch('a@x', {'action': 'message', 'id': self.id, 'message': 'deploy firewall', 'consent': True}, self.chat)
        self.assertEqual(out['plan']['title'], 'FW')
        self.assertEqual(dc.laptop_task(out)['portal_steps'], ['Open Firewalls'])

    def test_other_owner_cannot_open_chat(self):
        dc.dispatch('a@x', {'action': 'new', 'id': self.id, 'mode': 'cloud'}, self.chat)
        with self.assertRaises(ValueError):
            dc.dispatch('b@x', {'action': 'open', 'id': self.id}, self.chat)

    def test_cloud_mode_has_no_laptop_task(self):
        dc.dispatch('a@x', {'action': 'new', 'id': self.id, 'mode': 'cloud'}, self.chat)
        out = dc.dispatch('a@x', {'action': 'message', 'id': self.id, 'message': 'vm', 'consent': True}, self.chat)
        with self.assertRaises(ValueError):
            dc.laptop_task(out)


if __name__ == '__main__':
    unittest.main()
