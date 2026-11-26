// Browser agent executor. The Cloud Command Center page runs the loop (AI Foundry decides on the server);
// this file only observes and acts on ONE tab, and only while that tab is on an allowed Azure sign-in/portal host.
const AGENT_CCC = 'https://cloud-command-center.icyhill-ea3b0d83.uaenorth.azurecontainerapps.io';
const AGENT_HOSTS = ['portal.azure.com', 'portal.azure.net', 'ms.portal.azure.com', 'login.microsoftonline.com', 'login.live.com', 'microsoftonline.com',
  'msauth.net', 'msftauth.net', 'aadcdn.msftauth.net', 'azure.com', 'azure.microsoft.com'];
const AGENT_HOME = 'https://portal.azure.com/';
const hostOk = (u) => { try { const x = new URL(u); return x.protocol === 'https:' && AGENT_HOSTS.some((h) => x.hostname === h || x.hostname.endsWith('.' + h)); } catch (e) { return false; } };
const AKEY = 'agent_state';
const getA = async () => (await chrome.storage.session.get(AKEY))[AKEY] || {};
const setA = (v) => chrome.storage.session.set({ [AKEY]: v });

// ---- injected into every frame of the agent tab (must be self-contained)
function observeInFrame(limit) {
  const sel = 'a,button,input,select,textarea,[role=button],[role=option],[role=combobox],[role=radio],[role=checkbox],[role=tab],[role=menuitem],[role=switch],[role=link],[role=treeitem]';
  document.querySelectorAll('[data-agent-id]').forEach((e) => e.removeAttribute('data-agent-id'));
  const vh = window.innerHeight, out = [];
  const txt = (s) => (s || '').replace(/\s+/g, ' ').trim();
  let n = 0;
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect(), st = getComputedStyle(e);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (e.disabled && e.tagName !== 'SELECT') continue;
    if (r.bottom < -vh * 0.25 || r.top > vh * 1.35) continue;
    if (e.type === 'hidden') continue;
    const label = txt(e.getAttribute('aria-label')) || txt(e.labels && e.labels[0] && e.labels[0].innerText) || txt(e.title) || txt(e.innerText) || txt(e.placeholder) || txt(e.name) || txt(e.id);
    if (!label && e.tagName !== 'SELECT') continue;
    const id = ++n;
    e.setAttribute('data-agent-id', String(id));
    const item = { l: id, kind: e.getAttribute('role') || e.tagName.toLowerCase(), label: label.slice(0, 90) };
    if (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') { item.type = e.type; item.value = e.type === 'password' ? '' : String(e.value || '').slice(0, 60); if (e.type === 'radio' || e.type === 'checkbox') item.checked = e.checked; }
    if (e.getAttribute('aria-checked')) item.checked = e.getAttribute('aria-checked') === 'true';
    if (e.tagName === 'SELECT') { item.value = txt(e.options[e.selectedIndex] && e.options[e.selectedIndex].text); item.options = [...e.options].map((o) => txt(o.text)).slice(0, 60); }
    let a = e.parentElement, ctx = '';
    for (let i = 0; i < 4 && a; i++, a = a.parentElement) { const t = txt(a.innerText); if (t.length > label.length + 3 && t.length < 220) { ctx = t.slice(0, 110); break; } }
    if (ctx && ctx !== label) item.ctx = ctx;
    out.push(item);
    if (out.length >= limit) break;
  }
  return { url: location.href, title: document.title, elements: out, text: txt(document.body && document.body.innerText).slice(0, 600) };
}
function actInFrame(a) {
  const el = a.l ? document.querySelector('[data-agent-id="' + a.l + '"]') : null;
  const ev = (t) => el.dispatchEvent(new Event(t, { bubbles: true }));
  switch (a.do) {
    case 'click': if (!el) return 'element not found'; el.scrollIntoView({ block: 'center' }); el.focus && el.focus(); el.click(); return 'ok';
    case 'type': {
      if (!el) return 'element not found';
      if (el.type === 'password') return 'refused: password field';
      el.focus();
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const set = Object.getOwnPropertyDescriptor(proto, 'value');
      if (set && set.set) set.set.call(el, a.text); else el.value = a.text;
      ev('input'); ev('change'); return 'ok';
    }
    case 'select': {
      if (!el || el.tagName !== 'SELECT') return 'not a select';
      const o = [...el.options].find((x) => x.text.trim().toLowerCase() === String(a.option).trim().toLowerCase());
      if (!o) return 'option not found';
      el.value = o.value; ev('input'); ev('change'); return 'ok';
    }
    case 'scroll': window.scrollBy(0, a.direction === 'up' ? -700 : 700); return 'ok';
    case 'press': {
      const t = document.activeElement || document.body;
      for (const type of ['keydown', 'keypress', 'keyup']) t.dispatchEvent(new KeyboardEvent(type, { key: a.key, bubbles: true }));
      if (a.key === 'Enter' && t.form && t.form.requestSubmit) t.form.requestSubmit();
      return 'ok';
    }
    default: return 'unsupported';
  }
}

async function agentTab() {
  const st = await getA();
  if (!st.tabId) return null;
  try { const t = await chrome.tabs.get(st.tabId); return t; } catch (e) { return null; }
}

async function agentOp(m, sender) {
  if (!sender.tab || !String(sender.tab.url || '').startsWith(AGENT_CCC + '/')) throw new Error('not from Cloud Command Center');
  if (m.op === 'ping') return { ok: true, version: chrome.runtime.getManifest().version };
  if (m.op === 'open') {
    let t = await agentTab();
    const url = m.url && hostOk(m.url) ? m.url : AGENT_HOME;
    if (!t) { t = await chrome.tabs.create({ url, active: true }); await setA({ tabId: t.id }); } else await chrome.tabs.update(t.id, { url, active: true });
    return { ok: true, tabId: t.id };
  }
  if (m.op === 'close') { const t = await agentTab(); await setA({}); if (t) chrome.tabs.remove(t.id).catch(() => {}); return { ok: true }; }
  const tab = await agentTab();
  if (!tab) throw new Error('The agent tab was closed. Send your request again.');
  if (!hostOk(tab.url)) throw new Error('The agent tab left the Azure portal (' + (new URL(tab.url).hostname) + '). Stopped for safety.');
  if (m.op === 'observe') {
    let frames = [];
    try { frames = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: observeInFrame, args: [80] }); }
    catch (e) {
      // some frame blocked the call: fall back to the top frame so the agent still sees something
      try { frames = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: observeInFrame, args: [80] }); } catch (e2) { throw new Error('Cannot read the portal tab: ' + e2.message); }
    }
    const map = {}, elements = [];
    let g = 0, text = '';
    for (const f of frames) {
      if (!f.result) continue;
      if (f.frameId === 0) text = f.result.text;
      for (const e of f.result.elements) { if (g >= 120) break; g++; map[g] = { frameId: f.frameId, l: e.l }; elements.push({ ...e, id: g, l: undefined }); }
    }
    await setA({ tabId: tab.id, map });
    return { url: tab.url, title: tab.title || '', elements, text };
  }
  if (m.op === 'act') {
    const a = m.action || {};
    if (a.do === 'goto') { if (!hostOk(a.url)) return { result: 'refused: host not allowed' }; await chrome.tabs.update(tab.id, { url: a.url }); return { result: 'ok' }; }
    if (a.do === 'wait') { await new Promise((r) => setTimeout(r, 1500)); return { result: 'ok' }; }
    const st = await getA();
    const target = a.id ? (st.map || {})[a.id] : { frameId: 0, l: 0 };
    if (a.id && !target) return { result: 'element not found' };
    const res = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [target.frameId] }, func: actInFrame, args: [{ ...a, l: target.l }] });
    return { result: (res[0] && res[0].result) || 'no result' };
  }
  throw new Error('unknown op');
}
chrome.runtime.onMessage.addListener((m, sender, reply) => {
  if (!m || m.type !== 'agent') return false;
  agentOp(m, sender).then(reply, (e) => reply({ error: String(e && e.message || e) }));
  return true;
});
