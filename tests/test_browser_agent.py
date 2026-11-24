import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import browser_agent as ba
import hub_jobs as hub

OBS = {'url': 'https://portal.azure.com/#create', 'title': 'Create a VM', 'text': 'Create a virtual machine',
       'elements': [{'id': 1, 'kind': 'input', 'type': 'text', 'label': 'Virtual machine name'},
                    {'id': 2, 'kind': 'input', 'type': 'password', 'label': 'Password'},
                    {'id': 3, 'kind': 'button', 'label': 'Review + create'}]}


class BrowserAgentTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        p = patch.object(hub, 'ROOT', Path(self.t.name)); p.start(); self.addCleanup(p.stop)
        self.id = str(uuid4())

    def chat(self, reply):
        return lambda messages, tokens: (json.dumps(reply), 'gpt')

    def start(self, owner='a@x'):
        return ba.dispatch(owner, {'action': 'start', 'id': self.id, 'task': 'Create a VM', 'consent': True}, None)

    def test_secret_typing_and_foreign_hosts_are_refused(self):
        self.start()
        out = ba.dispatch('a@x', {'action': 'step', 'id': self.id, 'observation': OBS}, self.chat({
            'thought': 't', 'actions': [{'do': 'type', 'id': 1, 'text': 'vm1'}, {'do': 'type', 'id': 2, 'text': 'hunter2'},
                                        {'do': 'goto', 'url': 'https://evil.example.com/x'}, {'do': 'click', 'id': 99}]}))
        self.assertEqual([a['do'] for a in out['actions']], ['type'])
        self.assertEqual(out['actions'][0]['text'], 'vm1')
        self.assertEqual(len(out['log'][-1]['notes']), 3)

    def test_done_and_wait_user_and_stop(self):
        self.start()
        waiting = ba.dispatch('a@x', {'action': 'step', 'id': self.id, 'observation': OBS}, self.chat({'actions': [], 'wait_user': 'Approve MFA'}))
        self.assertEqual(waiting['status'], 'waiting')
        done = ba.dispatch('a@x', {'action': 'step', 'id': self.id, 'observation': OBS}, self.chat({'actions': [], 'done': True, 'summary': 'VM created'}))
        self.assertEqual((done['status'], done['summary']), ('done', 'VM created'))
        self.assertEqual(ba.dispatch('a@x', {'action': 'step', 'id': self.id, 'observation': OBS}, self.chat({'actions': [{'do': 'wait'}]}))['actions'], [])

    def test_stop_flag_blocks_actions_and_owner_isolation(self):
        self.start()
        ba.dispatch('a@x', {'action': 'stop', 'id': self.id}, None)
        out = ba.dispatch('a@x', {'action': 'step', 'id': self.id, 'observation': OBS}, self.chat({'actions': [{'do': 'click', 'id': 3}]}))
        self.assertEqual(out['actions'], [])
        with self.assertRaises(ValueError):
            ba.dispatch('b@x', {'action': 'status', 'id': self.id}, None)

    def test_consent_required(self):
        with self.assertRaises(ValueError):
            ba.dispatch('a@x', {'action': 'start', 'id': self.id, 'task': 'x'}, None)

    def test_destructive_click_needs_approval_then_runs(self):
        self.start()
        obs = {**OBS, 'elements': OBS['elements'] + [{'id': 4, 'kind': 'button', 'label': 'Delete resource group'}]}
        step = {'action': 'step', 'id': self.id, 'observation': obs}
        chat = self.chat({'thought': 't', 'actions': [{'do': 'click', 'id': 4}]})
        held = ba.dispatch('a@x', step, chat)
        self.assertEqual(held['actions'], [])
        self.assertIn('Approval needed', held['wait_user'])
        ok = ba.dispatch('a@x', {**step, 'approve': True}, chat)
        self.assertEqual([a['do'] for a in ok['actions']], ['click'])

    def test_blocked_and_idle_loop_and_alternatives(self):
        self.start()
        out = ba.dispatch('a@x', {'action': 'step', 'id': self.id, 'observation': OBS}, self.chat({'actions': [], 'blocked': 'portal empty'}))
        self.assertEqual(out['status'], 'blocked')
        alt = {'options': [{'title': 'Cloud Shell', 'detail': 'use az', 'task': 'Use Cloud Shell to run az group create'}]}
        res = ba.dispatch('a@x', {'action': 'alternatives', 'id': self.id}, self.chat(alt))
        self.assertEqual(res['alternatives'][0]['title'], 'Cloud Shell')
        self.id = str(uuid4()); self.start()
        wait = self.chat({'actions': [{'do': 'wait'}]})
        for _ in range(6):
            out = ba.dispatch('a@x', {'action': 'step', 'id': self.id, 'observation': {**OBS, 'elements': []}}, wait)
        self.assertEqual(out['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
