import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app")
server = ROOT / "server_app.py"
page = ROOT / "dashboard.html"
host = server.read_text(encoding="utf-8")
dashboard = page.read_text(encoding="utf-8")

ui = Path(__file__).with_name("proposal-studio.js").read_text(encoding="utf-8-sig").rstrip()
placeholders = (
    "  panel('proposal', 'RFP and attachments', 'Requirement extraction and proposal generation are pending setup.');",
    "  panel('proposal', 'Email, BOQ and supporting documents', 'Intake and missing-input checks are pending setup.');",
    "  panel('proposal', 'GDC proposal conversion', 'Seven Seas conversion depends on the Scope of Work Builder and approved templates.');",
)
for placeholder in placeholders:
    dashboard = dashboard.replace(placeholder + "\n", "")

if "return serve_proposal(self, app.config, app.foundry_token)" in host:
    pattern = r"(?ms)^if \(window\.CCC_SERVER && document\.getElementById\('work-proposal'\)\) \{\n.*?^\}"
    dashboard, replaced = re.subn(pattern, lambda _: ui, dashboard)
    if replaced != 1:
        raise RuntimeError("Unexpected installed Proposal Studio script layout")
    page.write_text(dashboard, encoding="utf-8")
    raise SystemExit(0)

run = "        if self.path == '/api/run':"
if host.count(run) != 1 or host.count("import app\n") != 1:
    raise RuntimeError("Unexpected hosted app route layout")
host = host.replace("import app\n", "import app\nfrom proposal.proposal_studio import serve as serve_proposal\n", 1)
host = host.replace(run, """        if self.path == '/api/proposal-studio':
            return serve_proposal(self, app.config, app.foundry_token)
""" + run, 1)

authenticated_dashboard = "        if self.path == '/':\n            self._local_dms_dashboard = True"
if host.count(authenticated_dashboard) != 1:
    raise RuntimeError("Authenticated dashboard alias anchor was not found")
host = host.replace(authenticated_dashboard, """        if self.path == '/sow-builder':
            self.send_response(302)
            self.send_header('Location', '/#work-proposal')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
""" + authenticated_dashboard, 1)
compile(host, str(server), "exec")

if dashboard.count("</script></main>") != 1:
    raise RuntimeError("Unexpected Proposal Studio dashboard layout")
css = """
.proposal-form{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(250px,1fr);gap:18px;align-items:start}
.proposal-form .panel{display:grid;gap:14px}
.proposal-form h2,.proposal-form p,.proposal-files{margin:0}
.proposal-form h2:not(:first-child){margin-top:8px;padding-top:16px;border-top:1px solid #293a55}
.proposal-form label{display:grid;gap:6px;font-weight:600}
.proposal-form input,.proposal-form select,.proposal-form textarea{min-width:0}
.proposal-form .proposal-export label{display:flex;align-items:flex-start;line-height:1.45}
.proposal-form .proposal-export input{margin-top:4px}
.proposal-files{overflow-wrap:anywhere}
@media(max-width:860px){.proposal-form{grid-template-columns:1fr}}
"""
dashboard = dashboard.replace("</style>", css + "</style>", 1)
dashboard = dashboard.replace("</script></main>", ui + "\n</script></main>", 1)
server.write_text(host, encoding="utf-8")
page.write_text(dashboard, encoding="utf-8")
