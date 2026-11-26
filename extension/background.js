importScripts('agent_bg.js');
// Keeps one auto-fill task per calculator tab and relays progress / the link back to the dashboard tab.
const CALC = 'https://azure.microsoft.com/en-us/pricing/calculator/';
const CCC = 'https://cloud-command-center.icyhill-ea3b0d83.uaenorth.azurecontainerapps.io';
const SLUG = /^[a-z0-9\\-]{2,60}$/, FIELD = /^[A-Za-z0-9]{1,60}$/;
const valid = (p) => p && p.v === 1 && Array.isArray(p.lines) && p.lines.length > 0 && p.lines.length <= 200 &&
  p.lines.every((l) => Array.isArray(l) && SLUG.test(l[0]) && l[1] && typeof l[1] === 'object' &&
    Object.entries(l[1]).every(([k, v]) => FIELD.test(k) && (Array.isArray(v) ? v.length <= 10 && v.every((x) => typeof x === 'string' && /^[a-z0-9-]{1,40}$/.test(x)) : ['string', 'number', 'boolean'].includes(typeof v) && String(v).length < 120)));
const key = (id) => 'task_' + id;
const getTask = async (id) => (await chrome.storage.session.get(key(id)))[key(id)];
const setTask = (id, t) => chrome.storage.session.set({ [key(id)]: t });
const tell = (t, msg) => t && chrome.tabs.sendMessage(t.dash, msg).catch(() => {});

chrome.runtime.onMessage.addListener((m, sender, reply) => {
  if (m && String(m.type).startsWith('agent')) return false; // handled by agent_bg.js
  (async () => {
    const tab = sender.tab;
    if (!tab) return reply(null);
    if (m.type === 'fill') {
      if (!tab.url || !tab.url.startsWith(CCC + '/')) return reply(null);
      let plan; try { plan = JSON.parse(m.plan); } catch (e) { plan = null; }
      if (!valid(plan)) { chrome.tabs.sendMessage(tab.id, { type: 'status', ok: false, text: 'the copied data is not valid calculator data.' }); return reply(null); }
      const calc = await chrome.tabs.create({ url: CALC, active: true, openerTabId: tab.id });
      await setTask(calc.id, { plan, phase: 'new', dash: tab.id, idx: m.idx, login: false, started: Date.now() });
      return reply(true);
    }
    if (m.type === 'readlink') {
      if (!tab.url || !tab.url.startsWith(CCC + '/')) return reply(null);
      if (!/^https:\/\/azure\.com\/e\/[A-Za-z0-9]{6,80}$/.test(String(m.url || ''))) { chrome.tabs.sendMessage(tab.id, { type: 'estimate', ok: false, error: 'Use an Azure Calculator share link like https://azure.com/e/...' }); return reply(null); }
      const rd = await chrome.tabs.create({ url: m.url, active: true, openerTabId: tab.id });
      await setTask(rd.id, { read: true, url: m.url, dash: tab.id, started: Date.now() });
      return reply(true);
    }
    const t = await getTask(tab.id);
    if (m.type === 'estimate' && t && t.read) {
      await chrome.storage.session.remove(key(tab.id));
      tell(t, { type: 'estimate', ok: !m.error, error: m.error || '', url: t.url, estimate: m.estimate || null, text: String(m.text || '').slice(0, 20000), total: String(m.total || '').slice(0, 30) });
      chrome.tabs.update(t.dash, { active: true }).catch(() => {});
      chrome.tabs.remove(tab.id).catch(() => {});
      return reply(true);
    }
    if (m.type === 'task?') return reply(t && Date.now() - t.started < 3600e3 ? t : null);
    if (!t) return reply(null);
    if (m.type === 'update') { Object.assign(t, m.patch || {}); await setTask(tab.id, t); return reply(true); }
    if (m.type === 'status') { tell(t, { type: 'status', text: m.text, ok: m.ok }); return reply(true); }
    if (m.type === 'done') {
      await chrome.storage.session.remove(key(tab.id));
      if (/^https:\/\/azure\.com\/e\/[A-Za-z0-9]+$/.test(m.url)) {
        tell(t, { type: 'link', url: m.url, idx: t.idx, total: /^[^\d]{0,4}\s?[\d,]+\.\d{2}$/.test(String(m.total || '')) ? String(m.total) : '' });
        chrome.tabs.update(t.dash, { active: true }).catch(() => {});
      } else tell(t, { type: 'status', ok: false, text: m.text || 'stopped.' });
      return reply(true);
    }
    reply(null);
  })();
  return true;
});
chrome.tabs.onRemoved.addListener((id) => chrome.storage.session.remove(key(id)));
