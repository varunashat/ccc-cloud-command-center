"""End-to-end check of the browser-agent extension in headless Chrome.

A local 'CCC page' talks to the extension through the real bridge; a local 'portal' page (with an iframe) stands in for
the Azure portal; the real browser_agent.dispatch decides steps via a scripted stand-in for AI Foundry.
Run: python tests/e2e/run_extension_e2e.py
"""
import json
import re
import shutil
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import browser_agent as ba  # noqa: E402
import hub_jobs as hub  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PORT = 8801
PORTAL = f'http://localhost:{PORT}/portal'

CCC_PAGE = """<!doctype html><title>ccc</title><body><div id=log></div><script>
function ext(op, extra={}){return new Promise((res,rej)=>{const id=crypto.randomUUID();const t=setTimeout(()=>rej(new Error('ext timeout '+op)),20000);
 function on(e){const m=e.data;if(e.source!==window||!m||m.src!=='ccc-ext'||m.type!=='agent-result'||m.id!==id)return;clearTimeout(t);removeEventListener('message',on);m.result&&m.result.error?rej(new Error(m.result.error)):res(m.result)}
 addEventListener('message',on);postMessage({src:'ccc-page',type:'agent',id,op,...extra},location.origin)})}
async function api(b){const r=await fetch('/api',{method:'POST',body:JSON.stringify(b)});return r.json()}
window.runAgent=async function(task){const id=crypto.randomUUID();await api({action:'start',id,task,consent:true});
 await ext('open',{url:%PORTAL%});await new Promise(r=>setTimeout(r,1500));let results=[];
 for(let s=0;s<30;s++){const obs=await ext('observe');const out=await api({action:'step',id,observation:obs,results});results=[];
  if(out.done)return out;if(out.wait_user)return out;
  for(const a of out.actions){const r=await ext('act',{action:a});results.push(a.do+' '+(a.id||'')+' -> '+r.result);console.log('ACT',JSON.stringify(a),JSON.stringify(r));await new Promise(r=>setTimeout(r,300))}}
 return {status:'gave up'}};
</script>"""

PORTAL_PAGE = """<!doctype html><title>Create a virtual machine</title><body><h1>Create a virtual machine</h1>
<iframe src="/frame" width=600 height=300></iframe>
<label>Virtual machine name <input id=n></label><label>Password <input id=p type=password></label>
<label>Region <select id=r><option>East US</option><option>UAE North</option></select></label>
<button onclick="document.getElementById('o').textContent='Deployment succeeded: '+document.getElementById('n').value+' in '+document.getElementById('r').value+' pw='+document.getElementById('p').value">Review + create</button>
<div id=o></div>"""
FRAME_PAGE = "<!doctype html><button id=b onclick=\"parent.document.title='frame-clicked'\">Accept terms</button>"


def scripted_chat(messages, tokens):
    """Stands in for AI Foundry: reads the CONTROLS list the server sent and decides like a person would."""
    user = messages[-1]['content']
    ids = {m.group(2).strip(): int(m.group(1)) for m in re.finditer(r'\[(\d+)\] [^|]+\| ([^|\n]+)', user)}
    ids = {**ids, **{k.split(' ')[0]: v for k, v in ids.items() if k.startswith('Region')}}
    done_so_far = user.split('DONE SO FAR:')[1].split('PAGE:')[0]
    if 'Deployment succeeded' in user:
        return json.dumps({'thought': 'verified', 'actions': [], 'done': True, 'summary': 'created vm1'}), 'scripted'
    if 'typed' not in done_so_far:
        return json.dumps({'thought': 'typed', 'actions': [
            {'do': 'click', 'id': ids['Accept terms']},
            {'do': 'type', 'id': ids['Virtual machine name'], 'text': 'vm1'},
            {'do': 'type', 'id': ids['Password'], 'text': 'hunter2'},
            {'do': 'select', 'id': ids['Region'], 'option': 'UAE North'},
            {'do': 'click', 'id': ids['Review + create']}]}), 'scripted'
    return json.dumps({'thought': 'waiting', 'actions': [{'do': 'wait'}]}), 'scripted'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        page = {'/': CCC_PAGE.replace('%PORTAL%', json.dumps(PORTAL)), '/portal': PORTAL_PAGE, '/frame': FRAME_PAGE}.get(self.path.split('?')[0])
        if page is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(page.encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        try:
            out = ba.dispatch('test@x', body, scripted_chat)
        except ValueError as error:
            out = {'error': str(error)}
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)


def build_test_extension(target):
    shutil.copytree(ROOT / 'extension', target)
    bg = (target / 'agent_bg.js').read_text()
    bg = bg.replace("'https://cloud-command-center.icyhill-ea3b0d83.uaenorth.azurecontainerapps.io'", f"'http://localhost:{PORT}'")
    bg = bg.replace("['portal.azure.com',", "['localhost', 'portal.azure.com',").replace("x.protocol === 'https:'", "true")
    (target / 'agent_bg.js').write_text(bg)
    manifest = json.loads((target / 'manifest.json').read_text())
    manifest['host_permissions'].append(f'http://localhost:{PORT}/*')
    for cs in manifest['content_scripts']:
        if any('cloud-command-center' in m for m in cs['matches']):
            cs['matches'] = [f'http://localhost:{PORT}/*']
    (target / 'manifest.json').write_text(json.dumps(manifest))
    (target / 'background.js').write_text((target / 'background.js').read_text().replace(
        "https://cloud-command-center.icyhill-ea3b0d83.uaenorth.azurecontainerapps.io", f"http://localhost:{PORT}"))


def main():
    tmp = Path(tempfile.mkdtemp())
    with patch.object(hub, 'ROOT', tmp / 'state'):
        server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        ext_dir = tmp / 'ext'
        build_test_extension(ext_dir)
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(tmp / 'profile'), executable_path='/usr/bin/google-chrome', headless=False,
                args=['--headless=new', '--no-sandbox', f'--disable-extensions-except={ext_dir}', f'--load-extension={ext_dir}'])
            page = ctx.new_page()
            page.goto(f'http://localhost:{PORT}/')
            page.wait_for_timeout(1500)
            page.on('console', lambda m: print('console:', m.text))
            page.on('requestfailed', lambda r: print('reqfailed:', r.url, r.failure))
            print('probe:', page.evaluate("fetch('/api',{method:'POST',body:'{}'}).then(r=>r.text()).catch(e=>'ERR '+e)"))
            result = page.evaluate("runAgent('Create VM vm1 in UAE North')")
            portal = [p for p in ctx.pages if '/portal' in p.url]
            print('log:', [l.get('actions') for l in ba.read(next((tmp / 'state').rglob('*.json')))['log']] if False else ''); print('result:', json.dumps({k: result.get(k) for k in ('status', 'summary', 'steps')}))
            if portal:
                out = portal[0].inner_text('#o')
                print('portal output:', out, '| title:', portal[0].title())
                assert 'vm1' in out and 'UAE North' in out, out
                assert 'hunter2' not in out, 'password was typed!'
                assert portal[0].title() == 'frame-clicked', 'iframe click did not work'
            else:
                raise AssertionError('portal tab not found')
            assert result.get('status') == 'done', result
            # stop / safety: a tab that leaves the allowed hosts must be refused
            print('E2E PASSED')
            ctx.close()
        server.shutdown()


if __name__ == '__main__':
    main()
