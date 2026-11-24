(async () => {
  // "CCC fill" bookmark: fills the Azure Pricing Calculator in YOUR browser from Cloud Command Center data.
  // Click 1: paste the data -> a new, empty estimate is created and the page reloads.
  // Click 2: the services are added and configured; the calculator recalculates every price itself.
  const KEY = 'ccc_plan_v1';
  const SLUG = /^[a-z0-9\\-]{2,60}$/, FIELD = /^[A-Za-z0-9]{1,60}$/;
  const box = (msg) => { let d = document.getElementById('ccc-box');
    if (!d) { d = document.createElement('div'); d.id = 'ccc-box';
      d.style.cssText = 'position:fixed;z-index:2147483647;top:16px;right:16px;max-width:420px;padding:14px 16px;background:#0f172a;color:#e2e8f0;font:14px system-ui;border-radius:10px;box-shadow:0 4px 20px #0008';
      document.body.appendChild(d); }
    d.textContent = 'Cloud Command Center: ' + msg; };
  if (location.hostname !== 'azure.microsoft.com' || !/\/pricing\/calculator/.test(location.pathname)) {
    alert('Open the Azure Pricing Calculator (azure.microsoft.com/pricing/calculator) first, then click this bookmark.'); return; }
  const db = await new Promise((res, rej) => { const r = indexedDB.open('localforage'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const store = (m) => db.transaction('keyvaluepairs', m).objectStore('keyvaluepairs');
  const get = (k) => new Promise((r) => { const q = store('readonly').get(k); q.onsuccess = () => r(q.result); });
  const put = (k, v) => new Promise((r, j) => { const q = store('readwrite').put(v, k); q.onsuccess = () => r(true); q.onerror = () => j(q.error); });
  const valid = (p) => p && p.v === 1 && Array.isArray(p.lines) && p.lines.length > 0 && p.lines.length <= 200 &&
    p.lines.every((l) => Array.isArray(l) && SLUG.test(l[0]) && l[1] && typeof l[1] === 'object' &&
      Object.entries(l[1]).every(([k, v]) => FIELD.test(k) && (Array.isArray(v) ? v.length <= 10 && v.every((x) => typeof x === 'string' && /^[a-z0-9-]{1,40}$/.test(x)) : ['string', 'number', 'boolean'].includes(typeof v) && String(v).length < 120)));
  let plan = null;
  try { plan = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) { plan = null; }
  if (!plan) {
    const raw = prompt('Paste the calculator data copied from Cloud Command Center:');
    if (!raw) return;
    try { plan = JSON.parse(raw); } catch (e) { alert('That is not Cloud Command Center calculator data.'); return; }
    if (!valid(plan)) { alert('That is not valid Cloud Command Center calculator data.'); return; }
    const est = (await get('azure_calculator_v3_estimates')) || {};
    const act = await get('azure_calculator_v3_active_estimate_id');
    const base = est[act] || Object.values(est)[0];
    if (!base) { alert('Add any service once in the calculator, remove it again, then retry (no estimate storage found).'); return; }
    const id = crypto.randomUUID();
    est[id] = Object.assign(JSON.parse(JSON.stringify(base)), { id, saved: false, estimateName: String(plan.name || 'Estimate').slice(0, 120), modules: [] });
    await put('azure_calculator_v3_estimates', est); await put('azure_calculator_v3_active_estimate_id', id);
    plan.estimate = id; sessionStorage.setItem(KEY, JSON.stringify(plan));
    box('New empty estimate created. The page reloads now: then click the "CCC fill" bookmark ONE more time.');
    setTimeout(() => location.reload(), 1500); return;
  }
  if (!valid(plan)) { sessionStorage.removeItem(KEY); alert('Saved data was invalid; start again.'); return; }
  const act = await get('azure_calculator_v3_active_estimate_id');
  if (act !== plan.estimate) { sessionStorage.removeItem(KEY); alert('A different estimate is open. Click the bookmark again to start over.'); return; }
  const count = async () => { const e = await get('azure_calculator_v3_estimates'); return ((e[act] || {}).modules || []).length; };
  const start = await count();
  if (start !== 0) { sessionStorage.removeItem(KEY); alert('The new estimate is not empty. Click the bookmark again to start over.'); return; }
  for (let i = 0; i < plan.lines.length; i++) {
    box(`Adding service ${i + 1} of ${plan.lines.length}... keep this tab open.`);
    const b = document.querySelector('button.pickerItem__button--add[data-slug="' + CSS.escape(plan.lines[i][0]) + '"]');
    if (!b) { sessionStorage.removeItem(KEY); box('Stopped: the calculator has no "' + plan.lines[i][0] + '" service button (page changed).'); return; }
    b.click();
    let k = 0; while ((await count()) < i + 1 && k < 160) { await new Promise((r) => setTimeout(r, 250)); k++; }
    if (k >= 160) { sessionStorage.removeItem(KEY); box('Stopped: adding "' + plan.lines[i][0] + '" timed out. Click the bookmark again to start over.'); return; }
  }
  const est = await get('azure_calculator_v3_estimates');
  const mods = est[act].modules;
  if (mods.map((m) => m.serviceSlug).join(',') !== plan.lines.map((l) => l[0]).join(',')) {
    sessionStorage.removeItem(KEY); box('Stopped: the services in the calculator do not match the data. Nothing was changed further.'); return; }
  const applySet = (st, set) => { if (set.cccZero) { for (const k of Object.keys(st)) { if (typeof st[k] === 'number' && /(count|units|instances|hours|nodes|users|servers|vcores|capacity|quantity|gb|operations|executions|messages|requests|transactions|ingested|devices|seats|minutes|calls|circuits|resources|policyunits)$/i.test(k) && !/factor$/i.test(k)) st[k] = 0; } } for (const [k, v] of Object.entries(set)) { if (k !== 'cccZero') st[k] = v; } };
  mods.forEach((m, i) => applySet(m.currentModuleState, plan.lines[i][1]));
  est[act].estimateName = String(plan.name || est[act].estimateName).slice(0, 120);
  await put('azure_calculator_v3_estimates', est);
  sessionStorage.removeItem(KEY);
  box(`Done: ${mods.length} services configured. The page reloads and recalculates; then click "Share" (or Export) to get the link.`);
  setTimeout(() => location.reload(), 1500);
})();
