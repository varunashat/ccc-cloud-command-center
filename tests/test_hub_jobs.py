"""Regression checks for scope enforcement and persisted worker checkpoints."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import hub_jobs as hub


class HubJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = patch.object(hub, 'ROOT', Path(self.temp.name))
        self.root.start()
        self.addCleanup(self.root.stop)
        self.tenant, self.sub = str(uuid4()), str(uuid4())
        self.body = {'tenant': self.tenant, 'subscriptions': [self.sub], 'action': 'start',
                     'workflows': ['infrastructure', 'security'], 'id': str(uuid4())}
        self.queries = []

    def call(self, method, url, body=None, tenant=None):
        if method == 'GET':
            return {'tenantId': self.tenant, 'subscriptionId': self.sub, 'state': 'Enabled'}
        self.queries.append(body['query'])
        return {'data': []}

    def test_scope_and_plan_reuse_are_checked(self):
        job = hub.dispatch('one@example.invalid', self.body, self.call)
        self.assertEqual(job['id'], hub.dispatch('one@example.invalid', self.body, self.call)['id'])
        with self.assertRaises(ValueError):
            hub.dispatch('one@example.invalid', dict(self.body, workflows=['cost']), self.call)
        with self.assertRaises(ValueError):
            hub.dispatch('one@example.invalid', dict(self.body, tenant=str(uuid4())), self.call)
        self.assertEqual(hub.dispatch('two@example.invalid', dict(self.body, action='list'), self.call), {'jobs': []})

    def test_pause_restart_empty_results_and_checkpoint_reuse(self):
        owner = 'one@example.invalid'
        hub.dispatch(owner, self.body, self.call)
        hub.dispatch(owner, dict(self.body, action='pause'), self.call)
        hub.process(lambda owner: self.call)
        self.assertEqual(self.queries, [])
        hub.dispatch(owner, dict(self.body, action='resume'), self.call)
        hub.process(lambda owner: self.call)
        job = hub.dispatch(owner, dict(self.body, action='status'), self.call)
        self.assertEqual(job['status'], 'Completed')
        self.assertEqual(job['sections']['infrastructure']['status'], 'empty')
        count = len(self.queries)
        hub.process(lambda owner: self.call)
        self.assertEqual(len(self.queries), count)

    def test_revoked_access_stops_queued_job(self):
        owner = 'one@example.invalid'
        hub.dispatch(owner, self.body, self.call)
        def denied(owner):
            raise ValueError('Sign-in required')
        hub.process(denied)
        job = hub.dispatch(owner, dict(self.body, action='status'), self.call)
        self.assertEqual(job['status'], 'Needs sign-in')
        self.assertEqual(job['sections'], {})

    def test_failed_security_does_not_block_independent_work(self):
        hub.dispatch('one@example.invalid', self.body, self.call)
        def partial(method, url, body=None, tenant=None):
            if body and 'securityRules' in body['query']:
                raise ValueError('Azure returned HTTP 403.')
            return self.call(method, url, body, tenant)
        hub.process(lambda owner: partial)
        job = hub.dispatch('one@example.invalid', dict(self.body, action='status'), self.call)
        self.assertEqual(job['sections']['security']['status'], 'unavailable')
        self.assertEqual(job['sections']['infrastructure']['status'], 'empty')
        self.assertEqual(job['status'], 'Completed with gaps')

    def test_csv_formula_is_escaped(self):
        section = {'scope': {'tenant': self.tenant, 'subscriptions': [self.sub]},
                   'captured_at': 'test', 'rows': [{'name': '=HYPERLINK("malicious")'}]}
        self.assertIn("'=HYPERLINK", hub.export_csv(section))


if __name__ == '__main__':
    unittest.main()
