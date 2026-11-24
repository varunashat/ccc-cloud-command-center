"""CCC laptop executor: lets Cloud Command Center run Azure CLI, Git, GitHub CLI, PowerShell and read selected folders on THIS laptop.

  python executor.py --folder C:\\Work\\my-repo [--folder ...] [--origin https://cloud-command-center....azurecontainerapps.io]

- Listens only on 127.0.0.1. Answers only the CCC origin. Each run prints a pairing code; paste it into the CCC page once.
- You sign in yourself (az login / gh auth login, MFA included). This program never reads, prints or sends passwords, cookies or tokens:
  token-extraction commands are refused and output is scrubbed for secrets.
- Files: only inside the folders you pass with --folder, and never key / .env / credential-looking files.
- Commands: az, git, gh, pwsh. Read-only runs freely; resource-creating commands need the plan approval from the CCC page;
  destructive or scope-changing commands need your approval of that exact command. Ctrl+C or the Disconnect button ends access.
Standard library only.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policy  # noqa: E402

VERSION = '1.0.0'
DEFAULT_ORIGIN = 'https://cloud-command-center.icyhill-ea3b0d83.uaenorth.azurecontainerapps.io'
TIMEOUT = 600
MAX_READ = 200_000
STATE = {'token': None, 'paired': False, 'folders': [], 'origin': DEFAULT_ORIGIN, 'log': []}
LOCK = threading.Lock()


def which(tool):
    names = {'pwsh': ['pwsh', 'powershell']}.get(tool, [tool])
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def tool_status():
    return {tool: bool(which(tool)) for tool in policy.TOOLS}


def run_command(tool, args, cwd=None, approved=False, plan_approved=False):
    kind, reason = policy.classify(tool, args)
    entry = {'tool': tool, 'args': policy.sanitize(' '.join(args), 400), 'kind': kind}
    if kind == 'forbidden':
        return {**entry, 'ran': False, 'error': 'Refused: ' + reason}
    if kind == 'approval' and not approved:
        return {**entry, 'ran': False, 'needs': 'approval', 'error': 'Needs your approval (' + reason + ').'}
    if kind == 'write' and not (approved or plan_approved):
        return {**entry, 'ran': False, 'needs': 'plan', 'error': 'Needs the plan to be approved first (' + reason + ').'}
    cwd = cwd or (STATE['folders'][0] if STATE['folders'] else None)
    exe = which(tool)
    if not exe:
        return {**entry, 'ran': False, 'error': f'{tool} is not installed on this laptop.'}
    if cwd and not policy.path_inside(cwd, STATE['folders']):
        return {**entry, 'ran': False, 'error': 'Working folder is outside the folders you selected.'}
    argv = [exe] + (['-NoProfile', '-NonInteractive', '-Command', ' '.join(args)] if tool == 'pwsh' else args)
    started = time.time()
    try:
        proc = subprocess.run(argv, cwd=cwd or None, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL, shell=False,
                              env={**os.environ, 'AZURE_CORE_ONLY_SHOW_ERRORS': 'false', 'GH_PROMPT_DISABLED': '1', 'GIT_TERMINAL_PROMPT': '0'})
    except subprocess.TimeoutExpired:
        return {**entry, 'ran': True, 'exit': None, 'error': f'Timed out after {TIMEOUT}s.', 'seconds': TIMEOUT}
    except OSError as error:
        return {**entry, 'ran': False, 'error': str(error)}
    return {**entry, 'ran': True, 'exit': proc.returncode, 'seconds': round(time.time() - started, 1),
            'stdout': policy.sanitize(proc.stdout), 'stderr': policy.sanitize(proc.stderr, 4000)}


def read_file(path):
    if not policy.path_inside(path, STATE['folders']):
        return {'error': 'That path is outside your selected folders or looks like a credential file.'}
    target = Path(path).expanduser()
    if target.is_dir():
        names = sorted(p.name + ('/' if p.is_dir() else '') for p in target.iterdir())[:300]
        return {'type': 'dir', 'entries': names}
    if not target.is_file():
        return {'error': 'File not found.'}
    data = target.read_bytes()[:MAX_READ]
    return {'type': 'file', 'size': target.stat().st_size, 'text': policy.sanitize(data.decode('utf-8', 'replace'), MAX_READ)}


class Handler(BaseHTTPRequestHandler):
    server_version = 'CCCExecutor/' + VERSION

    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', STATE['origin'])
        self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Executor-Token')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Private-Network', 'true')

    def _send(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self):
        return self.headers.get('Origin') == STATE['origin']

    def do_OPTIONS(self):
        if not self._origin_ok():
            return self._send(403, {'error': 'origin not allowed'})
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            return self._send(403, {'error': 'origin not allowed'})
        self._send(200, {'executor': 'ccc', 'version': VERSION, 'paired': STATE['paired'], 'host': __import__('socket').gethostname()})

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {'error': 'origin not allowed'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(min(length, 200_000)) or b'{}')
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {'error': 'bad request'})
        path = self.path.split('?')[0]
        if path == '/pair':
            with LOCK:
                if secrets.compare_digest(str(body.get('code', '')), STATE['code']):
                    STATE['token'] = secrets.token_urlsafe(24)
                    STATE['paired'] = True
                    print('[connected] CCC page paired.')
                    return self._send(200, {'token': STATE['token'], 'version': VERSION})
            return self._send(403, {'error': 'wrong pairing code'})
        if not STATE['token'] or not secrets.compare_digest(self.headers.get('X-Executor-Token', ''), STATE['token']):
            return self._send(401, {'error': 'not paired'})
        if path == '/status':
            return self._send(200, {'version': VERSION, 'tools': tool_status(), 'folders': STATE['folders'], 'paired': True})
        if path == '/disconnect':
            with LOCK:
                STATE.update(token=None, paired=False, code=new_code())
            print('[disconnected] new pairing code:', STATE['code'])
            return self._send(200, {'ok': True})
        if path == '/run':
            result = run_command(str(body.get('tool', '')), body.get('args'), body.get('cwd'), body.get('approved') is True, body.get('plan_approved') is True)
            print('[run]', result['tool'], result['args'], '->', result.get('exit', result.get('error')))
            return self._send(200, result)
        if path == '/read':
            return self._send(200, read_file(str(body.get('path', ''))))
        self._send(404, {'error': 'unknown'})


def new_code():
    return '-'.join(secrets.token_hex(2).upper() for _ in range(2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--folder', action='append', default=[], help='folder the assistant may read / run git in (repeatable)')
    parser.add_argument('--origin', default=DEFAULT_ORIGIN)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    STATE['folders'] = [str(Path(f).expanduser().resolve()) for f in args.folder]
    STATE['origin'] = args.origin.rstrip('/')
    STATE['code'] = new_code()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'CCC laptop executor {VERSION} on 127.0.0.1:{args.port}')
    print('Allowed origin :', STATE['origin'])
    print('Folders        :', ', '.join(STATE['folders']) or '(none: file and git access disabled)')
    print('Tools found    :', ', '.join(f'{k}={"yes" if v else "no"}' for k, v in tool_status().items()))
    print('\nPAIRING CODE   :', STATE['code'], '  (type it into the Connect box on the Environment & Deployment page)')
    print('Press Ctrl+C to stop. Sign in with `az login` / `gh auth login` yourself in another window.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    main()
