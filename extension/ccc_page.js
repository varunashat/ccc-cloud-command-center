// Runs inside the Cloud Command Center page. When the dashboard copies calculator data,
// hand it to the extension, then put the returned share link into "Save link" and click it.
(() => {
  if (window.__cccExt) return; window.__cccExt = true;
  const BTN = /^(Copy calculator data|Copied)/;
  let lastIdx = 0;
  const copyButtons = () => [...document.querySelectorAll('button')].filter((b) => BTN.test(b.textContent.trim()));
  const toast = (msg, ok) => {
    let d = document.getElementById('ccc-ext-toast');
    if (!d) { d = document.createElement('div'); d.id = 'ccc-ext-toast';
      d.style.cssText = 'position:fixed;z-index:2147483647;bottom:18px;right:18px;max-width:440px;padding:12px 16px;border-radius:10px;font:14px system-ui;box-shadow:0 4px 20px #0008';
      (document.body || document.documentElement).appendChild(d); }
    d.style.background = ok === false ? '#7f1d1d' : '#0f766e'; d.style.color = '#fff';
    d.textContent = 'Calculator auto-fill: ' + msg; };
  document.addEventListener('click', (e) => {
    const b = e.target && e.target.closest && e.target.closest('button');
    if (b && BTN.test(b.textContent.trim())) lastIdx = Math.max(0, copyButtons().indexOf(b));
  }, true);
  const isPlan = (t) => { try { const p = JSON.parse(t); return p && p.v === 1 && Array.isArray(p.lines); } catch (e) { return false; } };
  const send = (text) => { if (isPlan(text)) { window.postMessage({ src: 'ccc-page', type: 'fill', plan: text, idx: lastIdx }, location.origin);
    toast('opening the Azure Calculator in a new tab…'); } };
  try {
    const cb = navigator.clipboard, orig = cb && cb.writeText && cb.writeText.bind(cb);
    if (orig) cb.writeText = function (t) { try { send(String(t)); } catch (e) {} return orig(t); };
  } catch (e) {}
  const origExec = document.execCommand.bind(document);
  document.execCommand = function (cmd, ...a) {
    try { if (String(cmd).toLowerCase() === 'copy' && document.activeElement && 'value' in document.activeElement) send(document.activeElement.value); } catch (e) {}
    return origExec(cmd, ...a); };
  window.addEventListener('message', (e) => {
    const m = e.data;
    if (e.source !== window || e.origin !== location.origin || !m || m.src !== 'ccc-ext') return;
    if (m.type === 'ready') document.documentElement.dataset.cccExtension = '1';
    if (m.type === 'status') toast(m.text, m.ok);
    if (m.type === 'link' && /^https:\/\/azure\.com\/e\/[A-Za-z0-9]+$/.test(m.url)) {
      const bs = copyButtons(); const b = bs[m.idx] || bs[0];
      const inp = (b && b.closest('ol') && b.closest('ol').querySelector('input[placeholder^="https://azure.com/e/"]')) ||
        document.querySelector('input[placeholder^="https://azure.com/e/"]');
      const save = inp && [...inp.parentElement.querySelectorAll('button')].find((x) => /Save link/.test(x.textContent));
      if (!inp || !save) { toast('link ready: ' + m.url + ' (paste it into "Save link")', false); return; }
      inp.value = m.url; inp.dataset.total = m.total || ''; save.click(); toast('done – share link saved: ' + m.url + (m.total ? ' · calculator total ' + m.total : ''));
    }
  });
})();
