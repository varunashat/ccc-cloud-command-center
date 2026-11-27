"""PDF report for a BOQ alignment check: proposed BOQ, what is deployed, what changed and when, who changed it, trend, extra cost."""
import datetime as dt
import io
import json

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

NAVY, TEAL, GREY = colors.HexColor('#0b2545'), colors.HexColor('#13a89e'), colors.HexColor('#5b6b7f')
RED, AMBER, GREEN = colors.HexColor('#c0392b'), colors.HexColor('#d68910'), colors.HexColor('#1e8449')
STATUS_COL = {'Not aligned': AMBER, 'Not in BOQ': RED, 'In BOQ, not deployed': GREY}
SS = getSampleStyleSheet()
H1 = ParagraphStyle('h1', parent=SS['Heading1'], textColor=NAVY, fontSize=16, spaceAfter=6)
H2 = ParagraphStyle('h2', parent=SS['Heading2'], textColor=NAVY, fontSize=12, spaceBefore=10, spaceAfter=4)
P = ParagraphStyle('p', parent=SS['BodyText'], fontSize=8.5, leading=11)
SM = ParagraphStyle('sm', parent=P, fontSize=7.3, leading=9)
NOTE = ParagraphStyle('note', parent=P, textColor=GREY, fontSize=7.5)


def _e(s):
    return str(s if s is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _p(s, st=SM):
    return Paragraph(_e(s), st)


def _d(iso):
    return str(iso or '')[:16].replace('T', ' ')


def _day(iso):
    return str(iso or '')[:10]


def finding_key(f):
    return (f.get('resource'), f.get('status'), json.dumps(f.get('changes'), sort_keys=True))


def trend(rec, prior):
    """prior: earlier check records (oldest first) for the same BOQ and directory. Adds first_seen to each finding; returns summary of what is new / resolved / unchanged."""
    first = {}
    for p in prior:
        for f in p.get('findings', []):
            first.setdefault(finding_key(f), p.get('time'))
    cur = {finding_key(f): f for f in rec.get('findings', [])}
    for k, f in cur.items():
        f['first_seen'] = first.get(k) or rec.get('time')
    last = prior[-1] if prior else None
    prev_keys = {finding_key(f): f for f in (last or {}).get('findings', [])}
    new = [f for k, f in cur.items() if k not in prev_keys] if last else []
    resolved = [f for k, f in prev_keys.items() if k not in cur] if last else []
    return {'previous': last, 'new': new, 'resolved': resolved, 'series': [(_day(p.get('time')), len(p.get('findings', []))) for p in prior] + [(_day(rec.get('time')), len(rec.get('findings', [])))]}


def narrative(rec, tr, boq, chat=None):
    f = rec.get('findings', [])
    by = {}
    for x in f:
        by.setdefault(x['status'], []).append(x)
    lines = []
    lines.append(f"{rec.get('deployed_resources', 0)} resources were read from the customer environment. {rec.get('aligned', 0)} match the proposed BOQ and {len(f)} do not.")
    if by.get('Not in BOQ'):
        lines.append(f"{len(by['Not in BOQ'])} item(s) are running that are not in the BOQ (incremental workload): " + ', '.join(sorted({x['type'] for x in by['Not in BOQ']})[:8]) + '.')
    if by.get('Not aligned'):
        lines.append(f"{len(by['Not aligned'])} item(s) are in the BOQ but were deployed or modified with different settings (upgrade or amendment).")
    if by.get('In BOQ, not deployed'):
        lines.append(f"{len(by['In BOQ, not deployed'])} BOQ line(s) are not deployed yet.")
    dated = sorted([x for x in f if (x.get('last_change') or {}).get('time')], key=lambda x: x['last_change']['time'])
    if dated:
        who = {}
        for x in dated:
            who[x['last_change'].get('by') or 'unknown'] = who.get(x['last_change'].get('by') or 'unknown', 0) + 1
        top = sorted(who.items(), key=lambda t: -t[1])[:3]
        lines.append(f"Dated changes run from {_day(dated[0]['last_change']['time'])} to {_day(dated[-1]['last_change']['time'])}; most were made by " + ', '.join(f'{w} ({n})' for w, n in top) + '.')
    if tr['previous']:
        lines.append(f"Since the previous check ({_day(tr['previous'].get('time'))}): {len(tr['new'])} new difference(s), {len(tr['resolved'])} resolved.")
    base = ' '.join(lines)
    if chat:
        try:
            facts = json.dumps({'summary': base, 'findings': [{k: x.get(k) for k in ('status', 'type', 'resource', 'changes', 'last_change', 'created', 'first_seen')} for x in f[:40]],
                                'project': rec.get('project')})[:14000]
            a, _m = chat([{'role': 'system', 'content': 'You write the executive summary of an Azure BOQ compliance report for a cloud account manager. Use ONLY the facts given. 4 to 6 short sentences: overall verdict, what was added or amended beyond the BOQ, dates and who, the biggest risk or cost implication, and the recommended action. No bullet characters, no markdown.'},
                          {'role': 'user', 'content': facts}], 600)
            if a and len(a) > 40:
                return a.strip(), base
        except Exception:
            pass
    return base, base


def cost_info(rec):
    """Reads the saved pricing run for this check. Returns a dict, or None when the difference was never priced."""
    c = rec.get('cost')
    if not c:
        return None
    out = dict(c)
    try:
        import app
        st = json.loads((app.ROOT / 'estimator' / 'runs' / c['job'] / 'status.json').read_text(encoding='utf-8'))
        out['status'] = st.get('status')
        out['message'] = st.get('message', '')
        if st.get('status') == 'done':
            pg = (st.get('summary') or {}).get('payg') or {}
            out['payg_monthly'] = pg.get('monthly')
            out['payg_annual'] = pg.get('annual')
            out['top'] = st.get('top') or []
            out['by_type'] = st.get('by_type') or {}
            out['errors'] = st.get('errors') or []
    except Exception:
        out['status'] = 'unknown'
    return out


def build_pdf(rec, boq, prior, chat=None, cost=None):
    tr = trend(rec, prior)
    exe, facts_line = narrative(rec, tr, boq, chat)
    buf = io.BytesIO()
    W = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=W, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title='BOQ alignment report', author='Cloud Command Center')
    fw = W[0] - 28 * mm
    f = rec.get('findings', [])
    ok = not f
    S = []
    S.append(Paragraph('Azure BOQ alignment report', ParagraphStyle('t', parent=H1, fontSize=22)))
    S.append(Paragraph(_e(rec.get('project') or boq.get('project') or 'Customer BOQ'), H2))
    meta = [['Report generated (UTC)', _d(dt.datetime.now(dt.timezone.utc).isoformat()), 'Check run (UTC)', _d(rec.get('time'))],
            ['Customer directory', rec.get('tenant', ''), 'Run by', rec.get('user', '')],
            ['Subscriptions', ', '.join(s[:8] for s in rec.get('subscriptions', [])) or 'all selected', 'Resource group filter', rec.get('rg') or 'none'],
            ['BOQ saved', _d(boq.get('saved')), 'BOQ calculator link', rec.get('calculator_url') or boq.get('calculator_url') or '-']]
    t = Table([[_p(a, SM), _p(b, SM), _p(c, SM), _p(d, SM)] for a, b, c, d in meta], colWidths=[fw * .16, fw * .34, fw * .16, fw * .34])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#eef2f7')), ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#eef2f7')),
                           ('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#c9d3df')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    S += [t, Spacer(1, 6)]
    # KPI band
    cnt = {k: sum(1 for x in f if x['status'] == k) for k in ('Not aligned', 'Not in BOQ', 'In BOQ, not deployed')}
    kp = [[Paragraph('<b>%s</b>' % ('ALIGNED WITH BOQ' if ok else 'NOT ALIGNED WITH BOQ'), ParagraphStyle('v', parent=P, fontSize=13, textColor=GREEN if ok else RED)),
           _p(f"{sum(float(x.get('quantity') or 1) for x in boq.get('lines', [])):g} BOQ units in {len(boq.get('lines', []))} lines", P),
           _p(f"{rec.get('deployed_resources', 0)} deployed resources", P), _p(f"{rec.get('aligned', 0)} aligned", P)],
          [_p(f"{cnt['Not in BOQ']} not in BOQ (added)", P), _p(f"{cnt['Not aligned']} modified vs BOQ", P), _p(f"{cnt['In BOQ, not deployed']} BOQ lines not deployed", P),
           _p(f"{len(tr['new'])} new / {len(tr['resolved'])} resolved since last check" if tr['previous'] else 'First check (no earlier comparison)', P)]]
    k = Table(kp, colWidths=[fw * .28, fw * .24, fw * .24, fw * .24])
    k.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), .6, NAVY), ('INNERGRID', (0, 0), (-1, -1), .3, colors.HexColor('#c9d3df')), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f4f7fb')), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    S += [k, Paragraph('Executive summary', H2), Paragraph(_e(exe), P)]
    S.append(Paragraph('Additional cost of the differences', H2))
    if not cost:
        S.append(Paragraph('The differences have not been priced yet. Open the check in Cloud Command Center, press "Price the difference", then download this report again.', P))
    elif cost.get('status') != 'done' or cost.get('payg_monthly') is None:
        S.append(Paragraph('Pricing was started on %s but has not finished (%s). Download the report again in a minute.' % (_d(cost.get('priced_at')), _e(cost.get('message') or cost.get('status') or 'running')), P))
    else:
        dep = float(cost['payg_monthly'])
        boqp = float(cost.get('boq_monthly_of_changed') or 0)
        extra = dep - boqp
        kp2 = [[_p('Deployed cost of the items that differ', P), _p('Already in the BOQ for the modified items', P), _p('Estimated additional cost', P), _p('Additional per year', P)],
               [Paragraph('<b>$%s / month</b>' % f'{dep:,.0f}', P), Paragraph('<b>$%s / month</b>' % f'{boqp:,.0f}', P),
                Paragraph('<b><font color="%s">$%s / month</font></b>' % ('#c0392b' if extra > 0 else '#1e8449', f'{extra:,.0f}'), P), Paragraph('<b>$%s</b>' % f'{extra * 12:,.0f}', P)]]
        k2 = Table(kp2, colWidths=[fw * .25] * 4)
        k2.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), .6, NAVY), ('INNERGRID', (0, 0), (-1, -1), .3, colors.HexColor('#c9d3df')), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f4f7fb'))]))
        S.append(k2)
        S.append(Paragraph('Pay-as-you-go list prices (Azure Retail Prices API, uaenorth). Additional cost = deployed cost of everything that differs minus what the BOQ already priced for the modified items. Priced %s UTC.' % _d(cost.get('priced_at')), NOTE))
        top = cost.get('top') or []
        if top:
            rows = [['Item', 'Configuration', 'Qty', 'Monthly (PAYG)']] + [[_p(x.get('name')), _p(x.get('config')), str(x.get('qty', '')), f"{float(x.get('payg') or 0):,.2f}"] for x in top[:12]]
            t = Table(rows, colWidths=[fw * .3, fw * .45, fw * .06, fw * .14], repeatRows=1)
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTSIZE', (0, 0), (-1, -1), 7.3), ('GRID', (0, 0), (-1, -1), .25, colors.HexColor('#c9d3df')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            S += [Spacer(1, 4), t]
    if cost and cost.get('link'):
        S.append(Paragraph('Azure Pricing Calculator link for the difference: %s%s' % (_e(cost['link']), ' (estimated %s / month)' % _e(cost['link_total']) if cost.get('link_total') else ''), P))
    for n in (cost or {}).get('notes') or []:
        S.append(Paragraph('• ' + _e(n), NOTE))
    S.append(PageBreak())
    # proposed BOQ
    S.append(Paragraph('1. Proposed BOQ (what was agreed)', H1))
    rows = [['#', 'Type', 'Name', 'Region', 'Configuration', 'Qty', 'Monthly (PAYG)']]
    for i, b in enumerate(boq.get('lines', [])[:120], 1):
        um = (b.get('unit_monthly') or {}).get('payg') if isinstance(b.get('unit_monthly'), dict) else None
        rows.append([str(i), _p(b.get('type') or b.get('service') or ''), _p(b.get('name', '')), _p(b.get('region', '')), _p(b.get('config') or b.get('sku') or ''), str(b.get('quantity', 1)), '' if um is None else f'{float(um):,.2f}'])
    t = Table(rows, colWidths=[fw * .04, fw * .12, fw * .24, fw * .1, fw * .34, fw * .05, fw * .11], repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTSIZE', (0, 0), (-1, -1), 7.3), ('GRID', (0, 0), (-1, -1), .25, colors.HexColor('#c9d3df')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                           ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7f9fc')])]))
    S += [t, PageBreak()]
    # change timeline
    S.append(Paragraph('2. What changed and when (timeline)', H1))
    S.append(Paragraph('Dates come from the Azure Activity Log (last 89 days). Older changes cannot be dated by Azure; those rows show the first check that saw the difference.', NOTE))
    ev = []
    for x in f:
        c, lc = x.get('created') or {}, x.get('last_change') or {}
        when = lc.get('time') or c.get('time') or x.get('first_seen')
        ev.append((when or '', x, c, lc))
    ev.sort(key=lambda e: e[0], reverse=True)
    rows = [['Date (UTC)', 'Status', 'Service', 'Resource', 'What differs (BOQ → deployed)', 'Changed by', 'Operation', 'Created', 'First seen']]
    st = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTSIZE', (0, 0), (-1, -1), 7), ('GRID', (0, 0), (-1, -1), .25, colors.HexColor('#c9d3df')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]
    for w, x, c, lc in ev[:250]:
        diff = '; '.join(f"{ch['parameter']}: {ch['boq']} → {ch['deployed']}" if x['status'] == 'Not aligned' else f"{ch['parameter']}: {ch['deployed'] or ch['boq']}" for ch in x.get('changes', [])[:6])
        rows.append([_p(_d(w)), _p(x['status']), _p(x['type']), _p(x['resource']), _p(diff), _p(lc.get('by', '')), _p(lc.get('op', '')), _p(_d(c.get('time')) + (' by ' + c['by'] if c.get('by') else '')), _p(_day(x.get('first_seen')))])
        st.append(('TEXTCOLOR', (1, len(rows) - 1), (1, len(rows) - 1), STATUS_COL.get(x['status'], GREY)))
    if len(rows) == 1:
        rows.append([_p('No differences found.')] + [''] * 8)
    t = Table(rows, colWidths=[fw * w for w in (.08, .08, .09, .13, .27, .13, .09, .08, .05)], repeatRows=1)
    t.setStyle(TableStyle(st))
    S += [t, PageBreak()]
    # trend
    S.append(Paragraph('3. Comparison with earlier checks', H1))
    if tr['previous']:
        S.append(Paragraph(f"Previous check: {_d(tr['previous'].get('time'))}. New differences since then are listed first.", P))
        rows = [['Change', 'Status', 'Service', 'Resource', 'Detail']]
        for tag, lst in (('NEW', tr['new']), ('RESOLVED', tr['resolved'])):
            for x in lst[:100]:
                rows.append([_p(tag), _p(x['status']), _p(x['type']), _p(x['resource']), _p('; '.join(f"{c['parameter']}: {c['boq']} → {c['deployed']}" for c in x.get('changes', [])[:5]))])
        if len(rows) == 1:
            rows.append([_p('No change'), '', '', '', _p('Nothing new and nothing resolved since the previous check.')])
        t = Table(rows, colWidths=[fw * .08, fw * .12, fw * .14, fw * .2, fw * .46], repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTSIZE', (0, 0), (-1, -1), 7), ('GRID', (0, 0), (-1, -1), .25, colors.HexColor('#c9d3df')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        S.append(t)
    else:
        S.append(Paragraph('This is the first saved check for this BOQ and directory, so there is nothing to compare with yet. Turn on "Check this every day" to build the history.', P))
    ser = tr['series'][-14:]
    if len(ser) > 1:
        S.append(Paragraph('Differences found per check', H2))
        rows = [[_p('Date')] + [_p(d) for d, _n in ser], [_p('Differences')] + [_p(str(n)) for _d2, n in ser]]
        t = Table(rows, repeatRows=0)
        t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .25, colors.HexColor('#c9d3df')), ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#eef2f7'))]))
        S.append(t)
    if rec.get('warnings'):
        S += [Paragraph('Notes', H2)] + [Paragraph('• ' + _e(w), P) for w in rec['warnings'][:10]]
    S.append(Spacer(1, 10))
    S.append(Paragraph('Read-only comparison of the BOQ with the Azure Resource Graph inventory and the Activity Log. Costs are pay-as-you-go list prices.', NOTE))

    def foot(c, d):
        c.saveState(); c.setFont('Helvetica', 7); c.setFillColor(GREY)
        c.drawString(14 * mm, 8 * mm, 'Cloud Command Center · BOQ alignment report · ' + (rec.get('project') or ''))
        c.drawRightString(W[0] - 14 * mm, 8 * mm, 'Page %d' % d.page); c.restoreState()
    doc.build(S, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()
