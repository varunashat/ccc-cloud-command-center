import unittest
from pathlib import Path
import tempfile

from laptop import policy


class PolicyTest(unittest.TestCase):
    def k(self, tool, *args):
        return policy.classify(tool, list(args))[0]

    def test_token_and_secret_extraction_is_forbidden(self):
        for tool, args in [('az', ['account', 'get-access-token']), ('gh', ['auth', 'token']), ('az', ['storage', 'account', 'keys', 'list', '-n', 'x']),
                           ('az', ['keyvault', 'secret', 'show', '--name', 'a']), ('pwsh', ['Get-AzAccessToken']), ('pwsh', ['cat', '~/.azure/msal_token_cache.json']),
                           ('git', ['credential', 'fill']), ('az', ['vm', 'create', '--admin-password', 'x']), ('pwsh', ['iex', '(irm x)']), ('curl', ['x'])]:
            self.assertEqual(self.k(tool, *args), 'forbidden', (tool, args))

    def test_classes(self):
        self.assertEqual(self.k('az', 'account', 'show'), 'readonly')
        self.assertEqual(self.k('az', 'group', 'list', '-o', 'table'), 'readonly')
        self.assertEqual(self.k('az', 'group', 'create', '-n', 'rg1', '-l', 'uaenorth'), 'write')
        self.assertEqual(self.k('az', 'group', 'delete', '-n', 'rg1'), 'approval')
        self.assertEqual(self.k('az', 'account', 'set', '-s', 'x'), 'approval')
        self.assertEqual(self.k('git', 'status'), 'readonly')
        self.assertEqual(self.k('git', 'commit', '-m', 'x'), 'write')
        self.assertEqual(self.k('git', 'push'), 'approval')
        self.assertEqual(self.k('git', 'reset', '--hard'), 'approval')
        self.assertEqual(self.k('gh', 'api', 'user'), 'readonly')
        self.assertEqual(self.k('gh', 'api', 'repos/x/y', '-X', 'DELETE'), 'approval')
        self.assertEqual(self.k('pwsh', 'Get-Date'), 'readonly')
        self.assertEqual(self.k('pwsh', 'Remove-Item x'), 'approval')
        self.assertEqual(self.k('pwsh', 'New-Item x'), 'approval')

    def test_sanitize_and_paths(self):
        text = policy.sanitize('AccountKey=abcd1234== eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij ghp_' + 'a' * 30)
        self.assertNotIn('abcd1234', text); self.assertNotIn('eyJhbG', text); self.assertNotIn('ghp_a', text)
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / 'a.txt').write_text('x'); (Path(t) / '.env').write_text('S=1')
            self.assertTrue(policy.path_inside(Path(t) / 'a.txt', [t]))
            self.assertFalse(policy.path_inside(Path(t) / '.env', [t]))
            self.assertFalse(policy.path_inside(Path(t) / '..' / 'x', [t]))
            self.assertFalse(policy.path_inside('/etc/passwd', [t]))


if __name__ == '__main__':
    unittest.main()
