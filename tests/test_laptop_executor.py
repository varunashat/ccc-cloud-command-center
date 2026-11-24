import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from laptop import executor

ORIGIN = 'https://ccc.example'


class ExecutorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / 'repo'
        root.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
        (root / 'app.py').write_text('print(1)\n')
        (root / '.env').write_text('SECRET=1\n')
        executor.STATE.update(folders=[str(root)], origin=ORIGIN, code='AAAA-BBBB', token=None, paired=False)
        cls.root = root
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), executor.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.tmp.cleanup()

    def call(self, path, body=None, token=None, origin=ORIGIN):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        if token: headers['X-Executor-Token'] = token
        req = urllib.request.Request(self.base + path, json.dumps(body or {}).encode(), headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def pair(self):
        return self.call('/pair', {'code': 'AAAA-BBBB'})[1]['token']

    def test_origin_pairing_and_disconnect(self):
        self.assertEqual(self.call('/status', origin='https://evil.example')[0], 403)
        self.assertEqual(self.call('/status')[0], 401)
        self.assertEqual(self.call('/pair', {'code': 'wrong'})[0], 403)
        token = self.pair()
        self.assertEqual(self.call('/status', token=token)[0], 200)
        self.call('/disconnect', token=token)
        self.assertEqual(self.call('/status', token=token)[0], 401)
        executor.STATE['code'] = 'AAAA-BBBB'

    def test_git_status_and_file_rules(self):
        token = self.pair()
        code, out = self.call('/run', {'tool': 'git', 'args': ['status', '--short'], 'cwd': str(self.root)}, token)
        self.assertEqual(out['exit'], 0); self.assertIn('app.py', out['stdout'])
        self.assertIn('outside', self.call('/run', {'tool': 'git', 'args': ['status'], 'cwd': '/tmp'}, token)[1]['error'])
        self.assertIn('print(1)', self.call('/read', {'path': str(self.root / 'app.py')}, token)[1]['text'])
        self.assertIn('error', self.call('/read', {'path': str(self.root / '.env')}, token)[1])
        self.assertIn('error', self.call('/read', {'path': '/etc/hosts'}, token)[1])

    def test_gates(self):
        token = self.pair()
        forbidden = self.call('/run', {'tool': 'az', 'args': ['account', 'get-access-token']}, token)[1]
        self.assertFalse(forbidden['ran']); self.assertIn('Refused', forbidden['error'])
        need = self.call('/run', {'tool': 'git', 'args': ['push'], 'cwd': str(self.root)}, token)[1]
        self.assertEqual(need['needs'], 'approval')
        write = self.call('/run', {'tool': 'git', 'args': ['add', '-A'], 'cwd': str(self.root)}, token)[1]
        self.assertEqual(write['needs'], 'plan')
        ok = self.call('/run', {'tool': 'git', 'args': ['add', '-A'], 'cwd': str(self.root), 'plan_approved': True}, token)[1]
        self.assertEqual(ok['exit'], 0)


if __name__ == '__main__':
    unittest.main()
