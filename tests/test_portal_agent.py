import unittest

from laptop import portal_agent as pa


class GuardTest(unittest.TestCase):
    def test_hosts(self):
        self.assertTrue(pa.host_ok('https://portal.azure.com/#home'))
        self.assertFalse(pa.host_ok('https://evil.example.com/portal.azure.com'))
        self.assertFalse(pa.host_ok('http://portal.azure.com'))

    def test_confirmation_and_secrets(self):
        self.assertTrue(pa.needs_confirmation({'do': 'click'}, {'label': 'Review + create'}))
        self.assertTrue(pa.needs_confirmation({'do': 'click'}, {'label': 'Delete'}))
        self.assertFalse(pa.needs_confirmation({'do': 'click'}, {'label': 'Next: Networking'}))
        self.assertTrue(pa.blocked_typing({'label': 'Administrator password'}))
        self.assertFalse(pa.blocked_typing({'label': 'Virtual machine name'}))


if __name__ == '__main__':
    unittest.main()
