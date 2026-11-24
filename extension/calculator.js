// Runs on the Azure Pricing Calculator. Acts only when this tab was opened by Cloud Command Center.
(async () => {
  const ask = (m) => new Promise((r) => chrome.runtime.sendMessage(m, (x) => { void chrome.runtime.lastError; r(x); }));
  const task = await ask({ type: 'task?' });
  if (!task) return;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const box = (msg, ok) => { let d = document.getElementById('ccc-box');
    if (!d) { d = document.createElement('div'); d.id = 'ccc-box';
      d.style.cssText = 'position:fixed;z-index:2147483647;top:16px;right:16px;max-width:420px;padding:14px 16px;color:#fff;font:14px system-ui;border-radius:10px;box-shadow:0 4px 20px #0008';
      document.body.appendChild(d); }
    d.style.background = ok === false ? '#7f1d1d' : '#0f172a';
    d.textContent = 'Cloud Command Center: ' + msg; ask({ type: 'status', text: msg, ok }); };
  const fail = (msg) => { box('stopped – ' + msg, false); ask({ type: 'done', text: msg }); };
  const update = (patch) => ask({ type: 'update', patch });
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const button = (name) => [...document.querySelectorAll('button')].find((b) =>
    (norm(b.getAttribute('aria-label')) === name || norm(b.textContent) === name) && b.offsetParent !== null);
  const readTotal = () => {   // "$1,234.56 / Estimated monthly cost" in the sticky header
    const t = (document.body.innerText || '').replace(/ /g, ' ');
    const m = /([^\s\d]{0,4}\s?[\d,]+\.\d{2})\s*\/?\s*Estimated monthly cost/i.exec(t);
    return m ? m[1].trim() : '';
  };
  const addBtn = (slug) => document.querySelector('button.pickerItem__button--add[data-slug="' + CSS.escape(slug) + '"]');

  // Wait for the calculator to finish loading.
  for (let i = 0; i < 360 && !document.querySelector('button.pickerItem__button--add'); i++) await sleep(250);
  if (!document.querySelector('button.pickerItem__button--add')) return fail('the calculator page did not load. Close this tab and try again.');

  const hasDb = async () => (indexedDB.databases ? (await indexedDB.databases()).some((d) => d.name === 'localforage') : true);
  const openDb = () => new Promise((res, rej) => { const r = indexedDB.open('localforage'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  let db = null;
  const kv = async (k, v) => {
    if (!db) db = await openDb();
    return new Promise((r, j) => { const s = db.transaction('keyvaluepairs', v === undefined ? 'readonly' : 'readwrite').objectStore('keyvaluepairs');
      const q = v === undefined ? s.get(k) : s.put(v, k); q.onsuccess = () => r(v === undefined ? q.result : true); q.onerror = () => j(q.error); }); };
  const plan = task.plan;

  try {
    if (task.phase === 'new') {
      box('preparing a new, empty estimate…');
      let est = (await hasDb()) ? await kv('azure_calculator_v3_estimates') : null;
      if (!est || !Object.keys(est).length) {           // brand-new browser profile: let the calculator create its storage first
        const b = addBtn('virtual-machines'); if (!b) return fail('the calculator layout changed (no Virtual Machines button).');
        b.click();
        for (let i = 0; i < 80 && !(est && Object.keys(est).length); i++) { await sleep(250); if (await hasDb()) { db = null; est = await kv('azure_calculator_v3_estimates').catch(() => null); } }
        if (!est || !Object.keys(est).length) return fail('could not find the calculator estimate storage.');
      }
      const act = await kv('azure_calculator_v3_active_estimate_id');
      const base = est[act] || Object.values(est)[0];
      const id = crypto.randomUUID();
      est[id] = Object.assign(JSON.parse(JSON.stringify(base)), { id, saved: false, estimateName: String(plan.name || 'Estimate').slice(0, 120), modules: [] });
      await kv('azure_calculator_v3_estimates', est); await kv('azure_calculator_v3_active_estimate_id', id);
      await update({ phase: 'fill', estimate: id });
      box('new estimate created, reloading…');
      await sleep(800); location.reload(); return;
    }

    if (task.phase === 'fill') {
      const act = await kv('azure_calculator_v3_active_estimate_id');
      if (act !== task.estimate) return fail('a different estimate is open. Click "Copy calculator data" again to start over.');
      const count = async () => (((await kv('azure_calculator_v3_estimates'))[act] || {}).modules || []).length;
      if ((await count()) !== 0) return fail('the new estimate is not empty. Click "Copy calculator data" again to start over.');
      for (let i = 0; i < plan.lines.length; i++) {
        box(`adding service ${i + 1} of ${plan.lines.length}… keep this tab open.`);
        const b = addBtn(plan.lines[i][0]);
        if (!b) return fail('the calculator has no "' + plan.lines[i][0] + '" service (page changed).');
        b.click();
        let k = 0; while ((await count()) < i + 1 && k < 160) { await sleep(250); k++; }
        if (k >= 160) return fail('adding "' + plan.lines[i][0] + '" timed out.');
      }
      const est = await kv('azure_calculator_v3_estimates');
      const mods = est[act].modules;
      if (mods.map((m) => m.serviceSlug).join(',') !== plan.lines.map((l) => l[0]).join(',')) return fail('the services in the calculator do not match the data.');
      const applySet = (st, set) => { if (set.cccZero) { for (const k of Object.keys(st)) { if (typeof st[k] === 'number' && /(count|units|instances|hours|nodes|users|servers|vcores|capacity|quantity|gb|operations|executions|messages|requests|transactions|ingested|devices|seats|minutes|calls|circuits|resources|policyunits)$/i.test(k) && !/factor$/i.test(k)) st[k] = 0; } } for (const [k, v] of Object.entries(set)) { if (k !== 'cccZero') st[k] = v; } };
  mods.forEach((m, i) => applySet(m.currentModuleState, plan.lines[i][1]));
      est[act].estimateName = String(plan.name || est[act].estimateName).slice(0, 120);
      await kv('azure_calculator_v3_estimates', est);
      await update({ phase: 'share' });
      box(`${mods.length} services configured, reloading so the calculator recalculates prices…`);
      await sleep(800); location.reload(); return;
    }

    if (task.phase === 'share') {
      box('prices calculated. Saving the estimate and creating the share link…');
      const enabled = async (name, ms) => { let x = null; for (let i = 0; i < ms / 250; i++) { x = button(name); if (x && !x.disabled) return x; await sleep(250); } return null; };
      let save = null;
      for (let i = 0; i < 120 && !(save = button('save estimate')); i++) await sleep(250);
      if (!save) return fail('no "Save estimate" button found. Click Share yourself and paste the link into Cloud Command Center.');
      if (save.disabled) {
        if (!task.login) {
          await update({ login: true });
          const login = button('log in') || button('sign in');
          box('please sign in to the Azure Calculator (one time). Filling continues automatically after sign-in.');
          if (login) login.click();
        } else box('waiting for you to finish signing in to the Azure Calculator…');
        save = await enabled('save estimate', 600000);
        if (!save) return fail('sign-in did not finish within 10 minutes.');
      }
      await sleep(4000);                                   // let every module finish pricing
      let total = readTotal();
      for (let i = 0; i < 40 && (!total || !/[1-9]/.test(total)); i++) { await sleep(250); total = readTotal(); }   // header still recalculating
      save.click();
      box('estimate saved, creating the share link…');
      const RX = /https:\/\/azure\.com\/e\/[A-Za-z0-9]{8,64}/;
      const findLink = () => {
        for (const x of document.querySelectorAll('input, textarea')) { const m = RX.exec(String(x.value || '')); if (m) return m[0]; }
        for (const d of document.querySelectorAll('[role="dialog"], [aria-modal="true"]')) { const m = RX.exec(d.innerText || ''); if (m) return m[0]; }
        return '';
      };
      let url = '';
      for (let attempt = 0; attempt < 3 && !url; attempt++) {
        const share = await enabled('share estimate', 90000) || await enabled('share', 5000);   // Share is disabled until the save finishes
        if (!share) break;
        share.click();
        for (let i = 0; i < 120 && !(url = findLink()); i++) await sleep(250);
      }
      if (!url) return fail('the share link did not appear. Click Share yourself and paste the link into Cloud Command Center.');
      const close = [...document.querySelectorAll('[role="dialog"] button, [aria-modal="true"] button')].find((x) => /close|cancel/i.test((x.getAttribute('aria-label') || '') + x.textContent));
      if (close) close.click();
      box('done – share link sent back to Cloud Command Center: ' + url + (total ? ' (total ' + total + ')' : ''));
      ask({ type: 'done', url, total });
    }
  } catch (e) { fail(String(e && e.message || e)); }
})();
