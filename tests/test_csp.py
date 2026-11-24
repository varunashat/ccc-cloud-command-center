import sys, types, unittest
from unittest import mock

for name in ("azure", "azure.identity"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["azure.identity"].ManagedIdentityCredential = object
try:
    import server_app
except Exception as error:  # base-image modules are not all installed in CI
    server_app = None
    SKIP = str(error)


@unittest.skipIf(server_app is None, "server_app not importable here")
class CspTest(unittest.TestCase):
    def rewrite(self, value):
        got = {}
        with mock.patch.object(server_app.app.Handler, "send_header", lambda self, k, v: got.setdefault(k, v)):
            handler = server_app.Handler.__new__(server_app.Handler)
            server_app.Handler.send_header(handler, "Content-Security-Policy", value)
        return got["Content-Security-Policy"]

    def test_adds_executor(self):
        self.assertIn("connect-src 'self' http://127.0.0.1:8765", self.rewrite("default-src 'self'; frame-ancestors 'none'"))

    def test_extends_existing(self):
        v = self.rewrite("default-src 'self'; connect-src 'self' http://127.0.0.1:8766")
        self.assertIn("http://127.0.0.1:8765", v)
        self.assertIn("8766", v)


if __name__ == "__main__":
    unittest.main()
