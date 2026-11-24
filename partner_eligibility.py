"""Partner Center customer eligibility check (read-only).

Opens Incentives > Customer claims > Add customer in the app's Edge profile, fills the
first wizard step (claim name, customer ID type, customer ID), clicks Next, reads the
Eligible and Ineligible engagement lists, then closes the wizard.

Safety: it never ticks an engagement, never goes past the engagement step and never
clicks Submit / Complete / Create. No claim is created. The user signs in and approves
MFA themselves; passwords are never typed by the app.
"""
import datetime, re, time

CLAIMS_URL = 'https://partner.microsoft.com/dashboard/v2/incentives/customer-association/claims'
ID_TYPES = {'domain': 'Domain name', 'tenant': 'Tenant ID', 'tpid': 'TPID'}
BLOCKED = re.compile(r'\b(submit|complete|create claim|finish|confirm claim)\b', re.I)
MIGRATION = re.compile(r'migrat|modernize', re.I)
GROUPS = [
    ('Migration (former AMMP)', re.compile(r'migrat|modernize|avs', re.I)),
    ('AI & Agents', re.compile(r'\bai\b|agent|copilot|openai', re.I)),
    ('Data & Analytics', re.compile(r'data platform|analytics|fabric', re.I)),
    ('Databases', re.compile(r'database|sql', re.I)),
    ('Security', re.compile(r'secur|sentinel|defender', re.I)),
    ('Digital Sovereignty', re.compile(r'sovereign', re.I)),
    ('Apps & Infra', re.compile(r'app|infra|kubernetes|aks|developer', re.I)),
]

# Collects the label text of every checkbox in the engagement list, scrolling any
# scrollable container so virtualised lists are fully read.
COLLECT_JS = r"""async () => {
  const seen = new Map();
  const grab = () => {
    for (const cb of document.querySelectorAll('input[type=checkbox], [role=checkbox]')) {
      const vis = el => { for (let n = el; n && n !== document.body; n = n.parentElement) { const cs = getComputedStyle(n); if (cs.display === 'none' || cs.visibility === 'hidden' || n.hidden) return false; } return true; };
      if (!vis(cb.closest('label') || cb)) continue;
      let t = (cb.getAttribute('aria-label') || '').trim();
      if (!t && cb.id) { const l = document.querySelector('label[for="' + CSS.escape(cb.id) + '"]'); if (l) t = l.innerText.trim(); }
      if (!t) { const l = cb.closest('label') || cb.parentElement; if (l) t = (l.innerText || '').trim(); }
      if (!t) continue;
      const row = cb.closest('[role=row], li, label, div');
      const extra = row ? (row.innerText || '').replace(t, '').trim() : '';
      if (!seen.has(t)) seen.set(t, { name: t, disabled: !!(cb.disabled || cb.getAttribute('aria-disabled') === 'true'), note: extra.slice(0, 300) });
    }
  };
  grab();
  const boxes = [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 20 && /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.querySelector('input[type=checkbox], [role=checkbox]'));
  for (const b of boxes) { for (let y = 0; y <= b.scrollHeight; y += Math.max(80, b.clientHeight - 40)) { b.scrollTop = y; await new Promise(r => setTimeout(r, 120)); grab(); } b.scrollTop = 0; }
  return [...seen.values()];
}"""

TEXT_JS = r"""() => { const m = document.querySelector('main, [role=main]') || document.body; return (m.innerText || '').slice(0, 6000); }"""


def fiscal_year(today=None):
    d = today or datetime.date.today()
    return 'FY%02d' % ((d.year + (1 if d.month >= 7 else 0)) % 100)


def suggest_claim_name(customer, workload='Azure', today=None):
    name = re.sub(r'\s+', ' ', (customer or '').strip()) or 'Customer'
    return f'{name} – {workload} – {fiscal_year(today)}'[:100]


def customer_from_domain(domain):
    base = (domain or '').strip().lower().split('@')[-1].split('.')[0]
    return base.replace('-', ' ').title() if base else ''


def group(names):
    out = {}
    for n in names:
        g = next((label for label, rx in GROUPS if rx.search(n)), 'Other')
        out.setdefault(g, []).append(n)
    order = [g for g, _ in GROUPS] + ['Other']
    return {g: out[g] for g in order if g in out}


def summarise(customer, domain, claim_name, eligible, ineligible):
    el_names = [e['name'] for e in eligible]
    mig = [n for n in el_names if MIGRATION.search(n)]
    return {
        'customer': customer, 'customer_id': domain, 'claim_name': claim_name,
        'eligible_count': len(el_names), 'ineligible_count': len(ineligible),
        'customer_eligible': bool(el_names),
        'ammp_available': bool(mig), 'ammp_engagements': mig,
        'eligible': el_names, 'eligible_groups': group(el_names),
        'ineligible': [{'name': e['name'], 'note': e.get('note', '')} for e in ineligible],
        'checked_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
    }


class Checker:
    def __init__(self, page, notify=None, signin_wait=900):
        self.page = page
        self.notify = notify or (lambda m: None)
        self.signin_wait = signin_wait

    # ---------- helpers
    def _visible(self, locator, timeout=1500):
        try:
            locator.first.wait_for(state='visible', timeout=timeout); return True
        except Exception:
            return False

    def _click(self, locator, what):
        loc = locator.first
        label = ''
        try: label = (loc.inner_text(timeout=2000) or loc.get_attribute('aria-label') or '').strip()
        except Exception: pass
        if BLOCKED.search(label): raise RuntimeError(f'Refused to click "{label}" (safety rule).')
        loc.click(timeout=15000)

    def _heading(self, text, timeout):
        return self._visible(self.page.get_by_text(re.compile(text, re.I)), timeout)

    def _field(self, label):
        p = self.page
        for loc in (p.get_by_role('textbox', name=re.compile('^' + label, re.I)), p.get_by_label(re.compile('^' + label, re.I))):
            if self._visible(loc, 1500): return loc.first
        # fallback: the first input after the label text
        loc = p.locator(f'xpath=//*[normalize-space(text())="{label}"]/following::input[1]')
        return loc.first if self._visible(loc, 1500) else None

    # ---------- steps
    def open_claims(self):
        p = self.page
        p.goto(CLAIMS_URL, wait_until='domcontentloaded', timeout=90000)
        end = time.time() + self.signin_wait; told = False
        while time.time() < end:
            if 'partner.microsoft.com' in p.url and self._visible(p.get_by_text(re.compile('Customer claims|Add customer', re.I)), 3000):
                return
            if not told and ('login.' in p.url or 'microsoftonline' in p.url or 'login.live' in p.url):
                self.notify('ACTION NEEDED: sign in to Partner Center in the Edge window (approve MFA, choose "Stay signed in"). The check continues automatically.')
                told = True
            time.sleep(2)
        raise RuntimeError('Partner Center did not open (not signed in within 15 minutes).')

    def open_wizard(self):
        p = self.page
        tab = p.get_by_role('tab', name=re.compile('^Azure$', re.I))
        if self._visible(tab, 3000):
            try: tab.first.click(timeout=5000)
            except Exception: pass
        btn = p.get_by_role('button', name=re.compile('^Add customer', re.I))
        if not self._visible(btn, 8000): btn = p.get_by_text(re.compile('^Add customer$', re.I))
        self._click(btn, 'Add customer')
        if self._heading('Associate customer', 10000): return
        # Split button: the main part may only have opened its menu.
        p.keyboard.press('Escape')
        try: p.get_by_text(re.compile('^Add customer$', re.I)).first.click(timeout=5000)
        except Exception: pass
        if self._heading('Associate customer', 8000): return
        self.notify('ACTION NEEDED: click "Add customer" in Partner Center (Azure tab). The check continues once the form opens.')
        if not self._heading('Associate customer', 300000):
            raise RuntimeError('The Add customer form did not open.')

    def fill_customer(self, claim_name, id_type, customer_id):
        p = self.page
        f = self._field('Claim name')
        if not f: raise RuntimeError('Claim name box not found on the form.')
        f.fill(claim_name)
        # Partner location is pre-selected by Partner Center; leave it.
        combo = p.get_by_role('combobox', name=re.compile('Customer ID type', re.I))
        if not self._visible(combo, 2000):
            combo = p.locator('xpath=//*[contains(normalize-space(.),"Customer ID type") and not(*)]/following::*[@role="combobox" or self::select][1]')
        if self._visible(combo, 2000):
            current = ''
            try: current = combo.first.evaluate("e => e.tagName === 'SELECT' ? e.options[e.selectedIndex].text : (e.innerText || e.value || '')")
            except Exception: pass
            if id_type.lower() not in (current or '').lower():
                try:
                    combo.first.select_option(label=id_type, timeout=3000)
                except Exception:
                    combo.first.click(timeout=5000)
                    p.get_by_role('option', name=re.compile('^' + re.escape(id_type) + '$', re.I)).first.click(timeout=8000)
        cid = self._field('Customer ID') or p.locator('input[type=text]').last
        cid.fill(customer_id)
        time.sleep(0.5)
        nxt = p.get_by_role('button', name=re.compile('^Next$', re.I))
        self._click(nxt, 'Next')
        end = time.time() + 90
        while time.time() < end:
            if self._heading('Select the engagements|Associate engagement \\|', 2000) or self._visible(p.get_by_role('tab', name=re.compile('^Eligible$', re.I)), 500):
                return
            err = p.get_by_role('alert')
            if self._visible(err, 300):
                msg = (err.first.inner_text() or '').strip()
                if msg: raise RuntimeError('Partner Center: ' + msg[:300])
            time.sleep(1)
        txt = p.evaluate(TEXT_JS)
        hint = next((l for l in txt.splitlines() if re.search(r"not found|invalid|couldn.t|unable|error|not eligible", l, re.I)), '')
        raise RuntimeError('The engagement list did not appear. ' + (('Partner Center says: ' + hint.strip()) if hint else 'Check the customer ID and try Tenant ID or TPID.'))

    def read_tab(self, name):
        p = self.page
        tab = p.get_by_role('tab', name=re.compile('^' + name + '$', re.I))
        if not self._visible(tab, 2000): tab = p.get_by_text(re.compile('^' + name + '$', re.I))
        if self._visible(tab, 2000):
            tab.first.click(timeout=8000); time.sleep(1.5)
        items = p.evaluate(COLLECT_JS) or []
        return [i for i in items if not BLOCKED.search(i['name']) and len(i['name']) < 200]

    def close_wizard(self):
        p = self.page
        try:
            p.get_by_role('button', name=re.compile('^Close$', re.I)).last.click(timeout=8000)
            for label in ('Yes', 'Discard', 'Leave', 'Close', 'OK'):
                b = p.get_by_role('dialog').get_by_role('button', name=re.compile('^' + label + '$', re.I))
                if self._visible(b, 1500): b.first.click(timeout=5000); break
        except Exception:
            pass

    def run(self, customer_id, id_type='Domain name', claim_name='', customer=''):
        customer = customer or customer_from_domain(customer_id)
        claim_name = claim_name or suggest_claim_name(customer)
        self.notify('Opening Partner Center › Incentives › Customer claims…')
        self.open_claims()
        self.notify('Opening Add customer…')
        self.open_wizard()
        self.notify(f'Checking {customer_id} ({id_type})…')
        self.fill_customer(claim_name, id_type, customer_id)
        self.notify('Reading the eligible engagements…')
        eligible = self.read_tab('Eligible')
        ineligible = self.read_tab('Ineligible')
        self.close_wizard()
        return summarise(customer, customer_id, claim_name, eligible, ineligible)
