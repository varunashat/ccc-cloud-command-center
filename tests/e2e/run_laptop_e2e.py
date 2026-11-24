"""Headless-Chrome check of the Environment & Deployment 'laptop' mode: real page JS, real executor, real server brain, scripted Foundry.
Run: python tests/e2e/run_laptop_e2e.py   (needs playwright + google-chrome; no Azure access; `az` may be absent: reported honestly)."""
import json, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import hub_jobs as hub  # noqa: E402
import laptop_agent as la  # noqa: E402
import agent_store  # noqa: E402
from laptop import executor  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGE_PORT, EXE_PORT = 8803, 8765
ORIGIN = f'http://localhost:{PAGE_PORT}'
calls = {'n': 0}
SCRIPT = [
    {'thought': 'check repo', 'commands': [{'tool': 'git', 'args': ['status', '--short', '--branch']}]},
    {'thought': 'plan', 'plan': {'summary': 'Stage files in the repo', 'target': {'repo': 'demo'}, 'steps': ['git add -A', 'git push (held)'], 'cost_items': []}},
    {'thought': 'run', 'commands': [{'tool': 'git', 'args': ['add', '-A']}, {'tool': 'git', 'args': ['push']}]},
    {'thought': 'verify', 'commands': [{'tool': 'git', 'args': ['status', '--short']}]},
    {'thought': 'finished', 'done': True, 'summary': 'files staged; push was skipped by the operator'},
]


def chat(messages, tokens):
    reply = SCRIPT[min(calls['n'], len(SCRIPT) - 1)]
    calls['n'] += 1
    return json.dumps(reply), 'scripted'


HTML = """<!doctype html><title>t</title><nav class="workspace-nav"></nav><main></main><script>var token='t'; window.CCC_SERVER={user:'t'}; function dcSubs(){return []}</script>
<script src="/hub_workspace.js"></script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith('/hub_workspace.js'):
            data, kind = (ROOT / 'hub_workspace.js').read_bytes(), 'application/javascript'
        else:
            data, kind = HTML.encode(), 'text/html'
        self.send_response(200); self.send_header('Content-Type', kind); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        try:
            out = {'conversations': []} if body.get('channel') == 'deploy-chat' else agent_store.dispatch('t@x', body) if body.get('channel') == 'chat-history' else la.dispatch('t@x', body, chat, lambda url: {'Items': []})
            code = 200
        except ValueError as error:
            out, code = {'error': str(error)}, 400
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)


def main():
    tmp = Path(tempfile.mkdtemp())
    repo = tmp / 'repo'; repo.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    (repo / 'a.txt').write_text('hi')
    executor.STATE.update(folders=[str(repo)], origin=ORIGIN, code='AAAA-BBBB', token=None, paired=False)
    exe_server = ThreadingHTTPServer(('127.0.0.1', EXE_PORT), executor.Handler)
    threading.Thread(target=exe_server.serve_forever, daemon=True).start()
    with patch.object(hub, 'ROOT', tmp / 'state'):
        web = ThreadingHTTPServer(('127.0.0.1', PAGE_PORT), Handler)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--headless=new', '--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(ORIGIN + '/#hub-deployment')
            page.wait_for_selector('text=Run on my laptop', timeout=15000)
            page.click('text=Run on my laptop')
            page.fill('input[aria-label="Executor pairing code"]', 'AAAA-BBBB')
            page.click('button:has-text("Connect")')
            page.wait_for_selector('text=Connected to laptop executor', timeout=10000)
            print('status:', page.inner_text('text=Connected to laptop executor')[:160])
            page.click('button:has-text("Check connections")')
            page.wait_for_selector('text=Read a selected local project file', timeout=60000)
            page.wait_for_function("document.body.innerText.includes('git status in a selected repo')", timeout=60000)
            checks = page.evaluate("[...document.querySelectorAll('div')].map(d=>d.innerText).find(t=>t.includes('Foundry response')&&t.includes('GitHub query'))")
            print('CHECKS:\n' + (checks or '').split('\n\n')[0][:900])
            page.check('input[type=checkbox] >> nth=-1') if False else None
            for box in page.query_selector_all('#assessment-workspace input[type=checkbox]'):
                if not box.is_checked(): box.check()
            page.fill('textarea[aria-label="What should be deployed"]', 'Stage the files in the demo repo')
            page.click('button:has-text("Send")')
            page.wait_for_selector('button:has-text("Approve plan and run")', timeout=60000)
            page.click('button:has-text("Approve plan and run")')
            try:
                page.wait_for_selector('button:has-text("Skip them")', timeout=15000)
            except Exception:
                print('PAGE TEXT AT FAILURE:\n', page.inner_text('#assessment-workspace')[-2500:]); raise
            page.click('button:has-text("Skip them")')
            page.wait_for_function("document.body.innerText.includes('Finished:')", timeout=60000)
            text = page.inner_text('#assessment-workspace')
            print('LOG:\n' + text[text.find('PLAN:') - 200: text.find('Finished:') + 120])
            assert 'HELD for your approval: git push' in text
            assert 'Stage the files in the demo repo' in text, 'your own message must stay visible'
            page.wait_for_timeout(2500)
            chats = agent_store.dispatch('t@x', {'action': 'list'})['chats']
            saved = agent_store.dispatch('t@x', {'action': 'open', 'kind': 'laptop', 'id': chats[0]['id']})
            assert saved['transcript'][0] == {'role': 'user', 'text': 'Stage the files in the demo repo'}, saved['transcript'][:1]
            assert any('Finished' in e['text'] for e in saved['transcript']), 'transcript not saved'
            page.reload(); page.wait_for_selector('text=Run on my laptop')
            page.wait_for_timeout(2000)
            print('OPTIONS:', page.eval_on_selector('select[aria-label="Deployment chat history"]', 'e=>[...e.options].map(o=>o.value+"|"+o.text)'), 'ERR:', errors)
            page.select_option('select[aria-label="Deployment chat history"]', index=1)
            page.wait_for_selector('text=read-only copy', timeout=10000)
            assert 'Stage the files in the demo repo' in page.inner_text('#assessment-workspace'), 'history did not reopen'
            print('chat history persisted and reopened')
            assert 'exit 0' in text and 'Finished:' in text
            staged = subprocess.run(['git', 'status', '--short'], cwd=repo, capture_output=True, text=True).stdout
            assert 'A  a.txt' in staged, staged
            assert not errors, errors
            # automatic recovery: first attempt blocked, the page retries on its own without being asked
            SCRIPT[:] = [{'thought': 'try', 'blocked': 'quota exceeded in region'}, {'thought': 'other region', 'done': True, 'summary': 'finished via another approach'}]
            calls['n'] = 0
            page.click('text=Run on my laptop')
            for box in page.query_selector_all('#assessment-workspace input[type=checkbox]'):
                if not box.is_checked(): box.check()
            page.fill('textarea[aria-label="What should be deployed"]', 'Make something that will be blocked first')
            page.click('button:has-text("Send")')
            try:
                page.wait_for_function("document.body.innerText.includes('Finished: finished via another approach')", timeout=20000)
            except Exception:
                print('TAIL:', page.inner_text('#assessment-workspace')[-1200:]); raise
            body = page.inner_text('#assessment-workspace')
            assert 'Blocked: quota exceeded' in body and 'Trying a different approach automatically (1/2)' in body, body[-800:]
            print('automatic recovery worked')
            print('LAPTOP E2E PASSED')
            browser.close()


if __name__ == '__main__':
    main()
