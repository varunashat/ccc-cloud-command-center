import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import hub_jobs as hub


class OverviewTest(unittest.TestCase):
    def test_overview_lists_customers_for_owner_only(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(hub, 'ROOT', Path(temp)):
            tenant, sub = str(uuid4()), str(uuid4())
            call = lambda m, u, b=None, tenant_=None, **kw: {'tenantId': tenant, 'subscriptionId': sub, 'state': 'Enabled'} if m == 'GET' else {'data': []}
            hub.dispatch('a@x', {'tenant': tenant, 'subscriptions': [sub], 'action': 'start', 'workflows': ['cost'],
                                 'id': str(uuid4()), 'customer': 'Contoso'}, call)
            mine, theirs = hub.overview('a@x'), hub.overview('b@x')
            self.assertEqual(mine['totals']['customers'], 1)
            self.assertEqual(mine['customers'][0]['customer'], 'Contoso')
            self.assertEqual(theirs['totals']['runs'], 0)


if __name__ == '__main__':
    unittest.main()
