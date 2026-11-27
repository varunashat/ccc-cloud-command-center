"""Azure Well-Architected assessment: routes, background run, saved results, PDF and Excel reports."""
import datetime as dt
import io
import json
import logging
import re
import threading
import traceback
import urllib.parse
import uuid
from pathlib import Path

import waf_engine as eng

LOCK = threading.Lock()
STATE = {}
PILLAR_BLURB = {
    'Cost Optimization': 'Spend only for what the workload needs: right-size, remove waste, use commitments and licence benefits.',
    'Reliability': 'Recover from failures and keep serving: redundancy, backup, zones, and no retired SKUs.',
    'Security': 'Protect data and identities: network exposure, encryption, patching, Defender coverage.',
    'Operational Excellence': 'Run and improve the estate: landing zone, governance, tagging, monitoring and modernisation.',
    'Performance Efficiency': 'Meet demand efficiently: sizing from real metrics, hardware generation, disk and network features.',
}


def _root():
    try:
        import app
        base = Path(app.ROOT)
    except Exception:
        import os
        base = Path(os.environ.get('CCC_STATE', '/tmp/ccc'))
    p = base / 'reports' / 'waf'
    (p / 'runs').mkdir(parents=True, exist_ok=True)
    return p


def _jload(p, default):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _jsave(p, obj):
    tmp = Path(str(p) + '.tmp')
    tmp.write_text(json.dumps(obj, default=str), encoding='utf-8')
    tmp.replace(p)


def history():
    return _jload(_root() / 'history.json', [])


def narrative(res, chat=None):
    s = res['summary']
    sc = res['scores']
    worst = sorted(sc.items(), key=lambda kv: kv[1]['score'])[:2]
    top = [f for f in res['findings'] if f['severity'] == 'High'][:6]
    base = (f"{s['resources']} resources in {s['subscriptions']} subscription(s) were assessed against the five Well-Architected pillars, with {s['metrics_read']} resources checked against 30 days of usage metrics. "
            f"{s['findings']} findings were raised, {s['high']} of them high severity. The weakest pillars are {worst[0][0]} ({worst[0][1]['score']}/100) and {worst[1][0]} ({worst[1][1]['score']}/100). "
            f"Estimated saving from the cost findings (overlaps counted once) is about {s['currency']} {s['estimated_saving_30d']:,.0f} per month, against {s['currency']} {s['cost_30d']:,.0f} spent in the last 30 days.")
    if not chat:
        return base
    try:
        facts = json.dumps({'summary': base, 'scores': sc, 'top_high_findings': [{'pillar': f['pillar'], 'title': f['title'], 'count': f['count'], 'saving': f['saving']} for f in top],
                            'landing_zone_gaps': [l['check'] for l in res['landing_zone'] if not l['ok']]})[:9000]
        a, _m = chat([{'role': 'system', 'content': 'You write the executive summary of an Azure Well-Architected assessment for a customer IT manager. Use ONLY the facts given, never invent numbers. 5 to 7 short sentences: overall health, the main risks, the biggest savings opportunity, landing zone maturity, and the first three actions in priority order. Plain text, no bullets, no markdown.'},
                      {'role': 'user', 'content': facts}], 700)
        return a.strip() if a and len(a) > 60 else base
    except Exception:
        return base


def run(rid, user, tenant, subs, customer, modules=None):
    import boq_compliance
    st = STATE[rid]

    def progress(m):
        st['msg'] = m
    try:
        call = boq_compliance._call(user, tenant)
        sources = set()
        for m in modules:
            sources |= eng.MODULE_SOURCES[m]
        ev = eng.collect(call, tenant, subs, progress, sources=sources)
        progress('Analysing against the five pillars…')
        res = eng.analyze(ev, modules)
        try:
            import ai_assist
            progress('Writing the executive summary…')
            res['executive_summary'] = narrative(res, ai_assist.chat)
        except Exception:
            res['executive_summary'] = narrative(res, None)
        rec = {'id': rid, 'time': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'customer': customer, 'tenant': tenant, 'subscriptions': subs, 'user': user, 'modules': modules, 'result': res}
        _jsave(_root() / 'runs' / (rid + '.json'), rec)
        with LOCK:
            h = history()
            h.insert(0, {'id': rid, 'time': rec['time'], 'customer': customer, 'tenant': tenant, 'subscriptions': len(subs), 'scores': {k: v['score'] for k, v in res['scores'].items()},
                         'findings': res['summary']['findings'], 'saving': res['summary']['estimated_saving_30d'], 'modules': modules})
            _jsave(_root() / 'history.json', h[:200])
        STATE[rid] = {'status': 'done', 'id': rid}
    except Exception as e:
        logging.warning('waf run failed: %s', traceback.format_exc()[-600:])
        STATE[rid] = {'status': 'failed', 'error': str(e)[:300]}


def start(user, tenant, subs, customer, modules=None):
    from discovery.azure_discovery import check_subscriptions, check_tenant
    tenant, subs = check_tenant(tenant), check_subscriptions(subs)
    if any(s.get('status') == 'running' and s.get('user') == user for s in STATE.values()):
        raise ValueError('An assessment is already running for you. Wait for it to finish.')
    modules = [m for m in (modules or list(eng.MODULES)) if m in eng.MODULES] or list(eng.MODULES)
    rid = uuid.uuid4().hex[:10]
    STATE[rid] = {'status': 'running', 'msg': 'Starting…', 'user': user}
    threading.Thread(target=run, args=(rid, user, tenant, subs, str(customer or '')[:120], modules), daemon=True).start()
    return rid


# ----------------------------------------------------------------------------- reports
def export_xlsx(rec, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    res = rec['result']
    wb = Workbook()
    hdr = lambda ws: [setattr(c, 'font', Font(bold=True, color='FFFFFF')) or setattr(c, 'fill', PatternFill('solid', fgColor='0F4C81')) for c in ws[1]]
    ws = wb.active
    ws.title = 'Summary'
    s = res['summary']
    ws.append(['Customer', rec.get('customer', '')]); ws.append(['Directory', rec['tenant']]); ws.append(['Assessed (UTC)', rec['time']])
    ws.append(['Resources', s['resources']]); ws.append(['Spend last 30 days', f"{s['currency']} {s['cost_30d']:,.2f}"])
    ws.append(['Estimated monthly saving', f"{s['currency']} {s['estimated_saving_30d']:,.2f}"]); ws.append([])
    ws.append(['Pillar', 'Score /100', 'Rating', 'Findings', 'High severity'])
    for p, v in res['scores'].items():
        ws.append([p, v['score'], v['rating'], v['findings'], v['high']])
    ws.append([]); ws.append(['Executive summary']); ws.append([res.get('executive_summary', '')])
    ws.column_dimensions['A'].width = 34; ws.column_dimensions['B'].width = 28
    ws2 = wb.create_sheet('Findings')
    ws2.append(['Module', 'Pillar', 'Area', 'Severity', 'Finding', 'Affected', 'Of', 'Est. saving / 30d', 'Estimate basis', 'Recommendation', 'Source'])
    hdr(ws2)
    for f in res['findings']:
        ws2.append([eng.MODULES.get(f.get('module'), ''), f['pillar'], f['area'], f['severity'], f['title'], f['count'], f.get('population') or '', f['saving'] or '', f.get('basis', ''), f['recommendation'], f.get('source', '')])
    for col, w in zip('ABCDEFGHIJK', (30, 22, 18, 10, 60, 9, 7, 16, 40, 70, 16)):
        ws2.column_dimensions[col].width = w
    ws2.freeze_panes = 'A2'
    ws3 = wb.create_sheet('Affected resources')
    ws3.append(['Pillar', 'Finding', 'Severity', 'Resource', 'Type', 'Resource group', 'Subscription', 'Region', 'Detail', 'Est. saving / 30d'])
    hdr(ws3)
    for f in res['findings']:
        for i in f['items']:
            ws3.append([f['pillar'], f['title'], f['severity'], i['name'], i['type'], i['rg'], i['sub'], i['location'], i['detail'], i['saving'] or ''])
    for col, w in zip('ABCDEFGHIJ', (22, 50, 10, 30, 18, 24, 12, 14, 70, 14)):
        ws3.column_dimensions[col].width = w
    ws3.freeze_panes = 'A2'
    ws4 = wb.create_sheet('Utilisation')
    ws4.append(['Resource', 'Resource group', 'Size / tier', 'vCPU', 'Memory GB', 'State', 'CPU avg %', 'CPU peak %', 'CPU p95 of daily peaks %', 'Memory used avg %', 'Cost 30d', 'Verdict', 'Suggested size'])
    hdr(ws4)
    for u in res['utilization']:
        ws4.append([u.get('name'), u.get('rg'), u.get('size'), u.get('vcpu'), u.get('memory_gb'), u.get('power'), u.get('cpu_avg'), u.get('cpu_max'), u.get('cpu_p95'), u.get('mem_used_avg'), u.get('cost_30d'), u.get('verdict'), u.get('target')])
    for col, w in zip('ABCDEFGHIJKLM', (30, 24, 22, 7, 10, 12, 10, 11, 14, 14, 10, 16, 20)):
        ws4.column_dimensions[col].width = w
    ws4.freeze_panes = 'A2'
    ws5 = wb.create_sheet('Landing zone')
    ws5.append(['Check', 'Result', 'Evidence', 'What to do'])
    hdr(ws5)
    for l in res['landing_zone']:
        ws5.append([l['check'], 'In place' if l['ok'] else 'Gap', l['evidence'], l['fix']])
    for col, w in zip('ABCD', (58, 10, 60, 80)):
        ws5.column_dimensions[col].width = w
    ws6 = wb.create_sheet('Verified and gaps')
    ws6.append(['Evidence source', 'Status', 'Role needed'])
    hdr(ws6)
    for v in res.get('verified', []):
        ws6.append([v['source'], v['status'], v['role']])
    ws6.append([]); ws6.append(['Still needs evidence', 'Module'])
    for n in res.get('needs_evidence', []):
        ws6.append([n['item'], n['module']])
    ws6.column_dimensions['A'].width = 80; ws6.column_dimensions['B'].width = 22; ws6.column_dimensions['C'].width = 40
    ws7 = wb.create_sheet('WAF coverage map')
    ws7.append(['Web app', 'Kind', 'Default hostname', 'Public', 'Front end in front', 'Resource group'])
    hdr(ws7)
    for w in res.get('waf_map', []):
        ws7.append([w['app'], w['kind'], w['host'], 'yes' if w['public'] else 'no', w['front'], w['rg']])
    for col, w in zip('ABCDEF', (30, 14, 50, 8, 40, 24)):
        ws7.column_dimensions[col].width = w
    wb.save(path)


def build_pdf(rec):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    NAVY, GREY = colors.HexColor('#0b2545'), colors.HexColor('#5b6b7f')
    SEVC = {'High': colors.HexColor('#c0392b'), 'Medium': colors.HexColor('#d68910'), 'Low': colors.HexColor('#5b6b7f')}
    SS = getSampleStyleSheet()
    H1 = ParagraphStyle('h1', parent=SS['Heading1'], textColor=NAVY, fontSize=16, spaceAfter=6)
    H2 = ParagraphStyle('h2', parent=SS['Heading2'], textColor=NAVY, fontSize=11.5, spaceBefore=8, spaceAfter=3)
    P = ParagraphStyle('p', parent=SS['BodyText'], fontSize=8.5, leading=11)
    SM = ParagraphStyle('sm', parent=P, fontSize=7.2, leading=9)
    NOTE = ParagraphStyle('n', parent=P, textColor=GREY, fontSize=7.3)
    E = lambda s: str(s if s is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    p = lambda s, st=SM: Paragraph(E(s), st)
    res = rec['result']
    s = res['summary']
    cur = s['currency']
    buf = io.BytesIO()
    W = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=W, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=13 * mm, bottomMargin=13 * mm, title='Azure Well-Architected assessment')
    fw = W[0] - 28 * mm
    TS = lambda extra=(): TableStyle([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTSIZE', (0, 0), (-1, -1), 7.2), ('GRID', (0, 0), (-1, -1), .25, colors.HexColor('#c9d3df')),
                                      ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7f9fc')])] + list(extra))
    S = [Paragraph('Azure Well-Architected assessment', ParagraphStyle('t', parent=H1, fontSize=22)), Paragraph(E(rec.get('customer') or rec['tenant']), H2)]
    meta = [[p('Assessed (UTC)'), p(rec['time'].replace('T', ' ')), p('Directory'), p(rec['tenant'])],
            [p('Subscriptions'), p(', '.join(x[:8] for x in rec['subscriptions'])), p('Resources assessed'), p(f"{s['resources']} (usage metrics read for {s['metrics_read']})")],
            [p('Spend, last 30 days'), p(f"{cur} {s['cost_30d']:,.0f}"), p('Estimated saving / month'), p(f"{cur} {s['estimated_saving_30d']:,.0f} (estimate)")]]
    t = Table(meta, colWidths=[fw * .16, fw * .34, fw * .16, fw * .34])
    t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#c9d3df')), ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#eef2f7')), ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#eef2f7'))]))
    S += [t, Spacer(1, 6), Paragraph('Well-Architected scorecard (indicative)', H2)]
    rows = [['Pillar', 'Score', 'Rating', 'Findings', 'High', 'What it covers']]
    extra = []
    for i, (pil, v) in enumerate(res['scores'].items(), 1):
        rows.append([p(pil), p(f"{v['score']}/100"), p(v['rating']), p(str(v['findings'])), p(str(v['high'])), p(PILLAR_BLURB[pil])])
        extra.append(('TEXTCOLOR', (2, i), (2, i), colors.HexColor('#1e8449') if v['rating'] == 'Good' else (colors.HexColor('#d68910') if v['rating'] == 'Fair' else colors.HexColor('#c0392b'))))
    t = Table(rows, colWidths=[fw * .17, fw * .08, fw * .12, fw * .08, fw * .06, fw * .49])
    t.setStyle(TS(extra))
    S += [t, Paragraph('Executive summary', H2), Paragraph(E(res.get('executive_summary', '')), P),
          Paragraph('Scores start at 100 and lose points per finding by severity and by the share of resources affected. They are a guide to prioritise work, not a Microsoft rating.', NOTE), PageBreak()]
    # top actions, modules, verified and gaps
    S.append(Paragraph('Highest-value actions', H1))
    rows = [['#', 'Module', 'Action', 'Sev.', 'Affected', f'Est. {cur}/month', 'What to do']]
    for i, a in enumerate(res.get('top_actions', []), 1):
        rows.append([p(str(i)), p(a['module']), p(a['title']), p(a['severity']), p(str(a['count'])), p(f"{a['saving']:,.0f}" if a['saving'] else ''), p(a['recommendation'])])
    t = Table(rows, colWidths=[fw * w for w in (.03, .13, .25, .06, .07, .09, .37)], repeatRows=1)
    t.setStyle(TS())
    S.append(t)
    S.append(Paragraph('Modules assessed', H2))
    rows = [['Module', 'Findings', 'High', f'Est. saving {cur}/month']] + [[p(m['name']), p(str(m['findings'])), p(str(m['high'])), p(f"{m['saving']:,.0f}" if m['saving'] else '')] for m in res.get('modules', {}).values() if m['selected']]
    t = Table(rows, colWidths=[fw * .45, fw * .1, fw * .1, fw * .2])
    t.setStyle(TS())
    S.append(t)
    S.append(Paragraph('What was verified, and what still needs evidence', H2))
    rows = [['Evidence source', 'Status', 'Role needed']] + [[p(v['source']), p({'ok': 'Read', 'failed': 'Could not read', 'skipped': 'Not requested'}.get(v['status'], v['status'])), p(v['role'])] for v in res.get('verified', [])]
    t = Table(rows, colWidths=[fw * .4, fw * .15, fw * .3])
    t.setStyle(TS())
    S += [t, Spacer(1, 4)] + [Paragraph('• ' + E(n['item']) + ' <font color="#5b6b7f">(' + E(n['module']) + ')</font>', P) for n in res.get('needs_evidence', [])]
    S.append(PageBreak())
    # cost
    S.append(Paragraph('Cost and utilisation', H1))
    if s['cost_by_service']:
        rows = [['Azure service', f'Cost, last 30 days ({cur})']] + [[p(a), p(f'{b:,.0f}')] for a, b in s['cost_by_service'][:12]]
        t = Table(rows, colWidths=[fw * .4, fw * .2])
        t.setStyle(TS())
        S.append(t)
    sav = [f for f in res['findings'] if f['pillar'] == 'Cost Optimization' and f['saving']]
    if sav:
        rows = [['Saving opportunity', 'Affected', f'Est. {cur}/month', 'Basis']] + [[p(f['title']), p(str(f['count'])), p(f"{f['saving']:,.0f}"), p(f.get('basis', ''))] for f in sav]
        S += [Paragraph('Where the savings are', H2)]
        t = Table(rows, colWidths=[fw * .42, fw * .08, fw * .12, fw * .38], repeatRows=1)
        t.setStyle(TS())
        S += [t, Paragraph('Savings overlap (for example a VM that is both right-sized and covered by a reservation), so do not add them up blindly. All are estimates for the last 30 days of actual cost.', NOTE)]
    S.append(Paragraph('Resource utilisation and right-sizing (30-day Azure Monitor metrics)', H2))
    rows = [['Resource', 'Size / tier', 'State', 'CPU avg %', 'CPU peak %', 'Memory used %', f'Cost 30d', 'Verdict', 'Suggested']]
    vc = []
    for u in [u for u in res['utilization'] if u.get('verdict') not in ('No metrics',)][:60]:
        rows.append([p(u.get('name')), p(u.get('size')), p(u.get('power')), p(u.get('cpu_avg')), p(u.get('cpu_max')), p(u.get('mem_used_avg')), p(u.get('cost_30d')), p(u.get('verdict')), p(u.get('target')) if u.get('target') else ''])
        if u.get('verdict') == 'Scale down':
            vc.append(('TEXTCOLOR', (7, len(rows) - 1), (7, len(rows) - 1), colors.HexColor('#d68910')))
        elif u.get('verdict') == 'Scale up / out':
            vc.append(('TEXTCOLOR', (7, len(rows) - 1), (7, len(rows) - 1), colors.HexColor('#c0392b')))
    if len(rows) > 1:
        t = Table(rows, colWidths=[fw * w for w in (.2, .14, .08, .07, .07, .09, .08, .13, .14)], repeatRows=1)
        t.setStyle(TS(vc))
        S.append(t)
    else:
        S.append(Paragraph('No usage metrics could be read (check that your account has Monitoring Reader or Reader on the subscription).', P))
    S.append(PageBreak())
    # findings per pillar
    for pil in eng.PILLARS:
        fl = [f for f in res['findings'] if f['pillar'] == pil]
        S.append(Paragraph(f"{pil} · {res['scores'][pil]['score']}/100 · {res['scores'][pil]['rating']}", H1))
        S.append(Paragraph(PILLAR_BLURB[pil], P))
        if not fl:
            S += [Paragraph('No findings from the checks that could be run.', P), PageBreak()]
            continue
        rows = [['Sev.', 'Area', 'Finding', 'Affected', 'Est. saving', 'What to do', 'Examples']]
        ex = []
        for f in fl:
            names = ', '.join(i['name'] for i in f['items'][:5]) + (' …' if f['count'] > 5 else '')
            rows.append([p(f['severity']), p(f['area']), p(f['title']), p(f"{f['count']}" + (f" of {f['population']}" if f.get('population') else '')), p(f"{f['saving']:,.0f}" if f['saving'] else ''), p(f['recommendation']), p(names)])
            ex.append(('TEXTCOLOR', (0, len(rows) - 1), (0, len(rows) - 1), SEVC.get(f['severity'], GREY)))
        t = Table(rows, colWidths=[fw * w for w in (.05, .09, .21, .07, .07, .31, .20)], repeatRows=1)
        t.setStyle(TS(ex))
        S += [t, PageBreak()]
    # landing zone
    S.append(Paragraph('Landing zone and network design', H1))
    rows = [['Check', 'Result', 'Evidence', 'What to do']] + [[p(l['check']), p('In place' if l['ok'] else 'GAP'), p(l['evidence']), p('' if l['ok'] else l['fix'])] for l in res['landing_zone']]
    t = Table(rows, colWidths=[fw * .32, fw * .08, fw * .28, fw * .32], repeatRows=1)
    t.setStyle(TS([('TEXTCOLOR', (1, i), (1, i), colors.HexColor('#1e8449') if l['ok'] else colors.HexColor('#c0392b')) for i, l in enumerate(res['landing_zone'], 1)]))
    S.append(t)
    if res.get('modernization'):
        S.append(Paragraph('Modernisation candidates', H2))
        rows = [['Workload type', 'VMs', 'Option', 'Examples']] + [[p(m['category']), p(str(m['count'])), p(m['advice']), p(', '.join(m['examples']))] for m in res['modernization']]
        t = Table(rows, colWidths=[fw * .18, fw * .06, fw * .50, fw * .26], repeatRows=1)
        t.setStyle(TS())
        S.append(t)
    S.append(Paragraph('Limits of this assessment', H2))
    lim = ['Read-only: it reads Resource Graph, Advisor, Defender for Cloud, Cost Management and Azure Monitor. Nothing was changed.',
           'Findings come from configuration and 30-day platform metrics. Memory metrics only exist where the VM reports them; application behaviour, licensing entitlement and business criticality need a customer conversation.',
           'Modernisation candidates are based on image and OS only. Run Azure Migrate application discovery before committing to PaaS or containers.',
           'Savings are estimates. Confirm with the Azure Pricing Calculator before quoting them.'] + res.get('warnings', [])[:8]
    S += [Paragraph('• ' + E(x), P) for x in lim]

    def foot(c, d):
        c.saveState(); c.setFont('Helvetica', 7); c.setFillColor(GREY)
        c.drawString(14 * mm, 7 * mm, 'Cloud Command Center · Azure Well-Architected assessment · ' + (rec.get('customer') or ''))
        c.drawRightString(W[0] - 14 * mm, 7 * mm, 'Page %d' % d.page); c.restoreState()
    doc.build(S, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()


# ----------------------------------------------------------------------------- routes
def install(handler_cls):
    if getattr(handler_cls, '_waf_installed', False):
        return
    real_get, real_post = handler_cls.do_GET, handler_cls.do_POST
    G = ('/waf-assessment', '/ai/waf/tenants', '/ai/waf/subs', '/ai/waf/state', '/ai/waf/result', '/ai/waf/history', '/ai/waf/pdf', '/ai/waf/xlsx')

    def do_GET(self):
        path, _, qs = self.path.partition('?')
        if path not in G:
            return real_get(self)
        user = self._user()
        if not user:
            if path == '/waf-assessment':
                self.send_response(302); self.send_header('Location', '/'); self.send_header('Content-Length', '0'); self.end_headers(); return
            return self.send(401, {'error': 'Sign in first.'})
        q = urllib.parse.parse_qs(qs)
        a = lambda k: (q.get(k) or [''])[0]
        try:
            import boq_compliance
            from discovery.azure_discovery import check_tenant, list_subscriptions, list_tenants
            if path == '/waf-assessment':
                from waf_page import PAGE
                try:
                    from partner_azure import NAV
                except Exception:
                    NAV = ''
                return self.send_raw(200, PAGE.replace('__CGNAV__', NAV).encode('utf-8'), 'text/html; charset=utf-8', 'no-store')
            if path == '/ai/waf/tenants':
                return self.send(200, {'tenants': list_tenants(boq_compliance._call(user, None))})
            if path == '/ai/waf/subs':
                t = check_tenant(a('tenant'))
                return self.send(200, {'subscriptions': list_subscriptions(boq_compliance._call(user, t), t)})
            if path == '/ai/waf/history':
                return self.send(200, {'history': history()})
            if path == '/ai/waf/state':
                return self.send(200, {k: v for k, v in STATE.get(a('id'), {'status': 'unknown'}).items() if k != 'user'})
            rid = a('id')
            if not re.fullmatch(r'[a-f0-9]{10}', rid):
                return self.send(400, {'error': 'Unknown assessment.'})
            rec = _jload(_root() / 'runs' / (rid + '.json'), None)
            if not rec:
                return self.send(404, {'error': 'Unknown assessment.'})
            if path == '/ai/waf/result':
                return self.send(200, rec)
            if path == '/ai/waf/pdf':
                data, ctype, name = build_pdf(rec), 'application/pdf', f'WAF_assessment_{rid}.pdf'
            else:
                tmp = _root() / ('x-' + rid + '.xlsx')
                export_xlsx(rec, tmp)
                data = tmp.read_bytes(); tmp.unlink()
                ctype, name = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', f'WAF_assessment_{rid}.xlsx'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        except Exception as e:
            return self.send(400, {'error': str(e)[:300]})

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if path not in ('/ai/waf/start', '/ai/waf/verify'):
            return real_post(self)
        o = self.headers.get('Origin')
        if o is not None and urllib.parse.urlparse(o).netloc != self.headers.get('Host'):
            return self.send(403, {'error': 'Invalid origin'})
        user = self._user()
        if not user:
            return self.send(401, {'error': 'Sign in first.'})
        try:
            size = int(self.headers.get('Content-Length', '0') or 0)
            p = json.loads(self.rfile.read(size) or b'{}') if 0 < size < 200_000 else {}
            subs = [str(x) for x in (p.get('subscriptions') or [])][:50]
            if not subs:
                return self.send(400, {'error': 'Tick at least one subscription.'})
            if path == '/ai/waf/verify':
                import boq_compliance
                from discovery.azure_discovery import check_subscriptions, check_tenant
                t, ss = check_tenant(str(p.get('tenant', ''))), check_subscriptions(subs)
                return self.send(200, {'checks': eng.verify(boq_compliance._call(user, t), t, ss)})
            return self.send(200, {'task': start(user, str(p.get('tenant', '')), subs, p.get('customer'), p.get('modules'))})
        except Exception as e:
            return self.send(400, {'error': str(e)[:300]})

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._waf_installed = True
