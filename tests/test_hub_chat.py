import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import hub_chat
import hub_jobs


class HubChatTest(unittest.TestCase):
    def test_new_chat_has_no_previous_messages_and_scope_is_enforced(self):
        tenant, subscription = str(uuid4()), str(uuid4())
        scope = {'tenant': tenant, 'subscriptions': [subscription]}

        def call(*args, **kwargs):
            return {'tenantId': tenant, 'subscriptionId': subscription, 'state': 'Enabled'}

        with tempfile.TemporaryDirectory() as folder, patch.object(hub_jobs, 'ROOT', Path(folder)):
            first = hub_chat.dispatch('owner@example.invalid', dict(scope, action='new', id=str(uuid4())), call)
            with patch.object(hub_chat.ai_assist, 'chat', return_value=('Evidence is missing.', 'test-model')) as model:
                hub_chat.dispatch('owner@example.invalid', dict(scope, action='message', id=first['id'],
                                                              message='First customer discussion', consent=True), call)
                second = hub_chat.dispatch('owner@example.invalid', dict(scope, action='new', id=str(uuid4())), call)
                self.assertEqual(second['messages'], [])
                hub_chat.dispatch('owner@example.invalid', dict(scope, action='message', id=second['id'],
                                                              message='Fresh discussion', consent=True), call)
                self.assertNotIn('First customer discussion', str(model.call_args))
            with self.assertRaises(ValueError):
                hub_chat.dispatch('other@example.invalid', dict(scope, action='open', id=first['id']), call)
            with self.assertRaises(ValueError):
                hub_chat.dispatch('owner@example.invalid', dict(scope, action='message', id=first['id'], message='No consent'), call)
            saved = hub_chat.dispatch('owner@example.invalid', dict(scope, action='open', id=first['id']), call)
            self.assertEqual(len(saved['messages']), 2)
