import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import agent_store as store
import browser_agent as ba
import hub_jobs as hub
import laptop_agent as la


class AgentStoreTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        p = patch.object(hub, 'ROOT', Path(self.t.name)); p.start(); self.addCleanup(p.stop)

    def test_transcript_history_and_isolation(self):
        a, b = str(uuid4()), str(uuid4())
        ba.dispatch('a@x', {'action': 'start', 'id': a, 'task': 'Create rg one', 'consent': True}, None)
        la.dispatch('a@x', {'action': 'start', 'id': b, 'task': 'git status in repo', 'consent': True}, None)
        store.dispatch('a@x', {'action': 'note', 'kind': 'browser', 'id': a, 'entries': [{'role': 'user', 'text': 'Create rg one'}, {'role': 'assistant', 'text': 'Started'}, {'role': 'assistant', 'text': '  '}]})
        opened = store.dispatch('a@x', {'action': 'open', 'kind': 'browser', 'id': a})
        self.assertEqual([e['role'] for e in opened['transcript']], ['user', 'assistant'])
        titles = {c['title'] for c in store.dispatch('a@x', {'action': 'list'})['chats']}
        self.assertEqual(titles, {'Create rg one', 'git status in repo'})
        self.assertEqual(store.dispatch('other@x', {'action': 'list'})['chats'], [])
        with self.assertRaises(ValueError):
            store.dispatch('other@x', {'action': 'open', 'kind': 'browser', 'id': a})

    def test_memory_goes_into_the_next_prompt(self):
        a = str(uuid4())
        ba.dispatch('a@x', {'action': 'start', 'id': a, 'task': 'Create rg weather-rg in uaenorth', 'consent': True}, None)
        ba.dispatch('a@x', {'action': 'step', 'id': a, 'observation': {'url': 'https://portal.azure.com/', 'title': 't', 'text': '', 'elements': []}},
                    lambda m, t: ('{"done":true,"summary":"created weather-rg"}', 'g'))
        seen = []
        b = str(uuid4())
        ba.dispatch('a@x', {'action': 'start', 'id': b, 'task': 'same as last time but in westeurope', 'consent': True}, None)
        ba.dispatch('a@x', {'action': 'step', 'id': b, 'observation': {'url': 'https://portal.azure.com/', 'title': 't', 'text': '', 'elements': []}},
                    lambda m, t: (seen.append(m[-1]['content']) or '{"actions":[]}', 'g'))
        self.assertIn('weather-rg', seen[0]); self.assertIn('created weather-rg', seen[0])


if __name__ == '__main__':
    unittest.main()
