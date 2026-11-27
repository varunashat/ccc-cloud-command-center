// Bridge between the Cloud Command Center page and the extension (isolated world).
window.addEventListener('message', (e) => {
  const m = e.data;
  if (e.source !== window || e.origin !== location.origin || !m || m.src !== 'ccc-page') return;
  if (m.type === 'agent') {
    const fail = (text) => window.postMessage({ src: 'ccc-ext', type: 'agent-result', id: m.id, result: { error: text } }, location.origin);
    if (!chrome.runtime || !chrome.runtime.id) return fail('The extension was reloaded. Refresh this Cloud Command Center tab (F5) and try again.');
    try { chrome.runtime.sendMessage({ type: 'agent', op: String(m.op || ''), url: m.url, action: m.action }).then(
      (r) => window.postMessage({ src: 'ccc-ext', type: 'agent-result', id: m.id, result: r }, location.origin),
      (err) => fail(String(err)));
    } catch (error) { fail('The extension was reloaded. Refresh this Cloud Command Center tab (F5) and try again.'); }
    return;
  }
  if (m.type === 'readlink') {
    try { chrome.runtime.sendMessage({ type: 'readlink', url: String(m.url || '') }); } catch (error) { /* extension reloaded: page refresh needed */ }
    return;
  }
  if (m.type !== 'fill') return;
  try { chrome.runtime.sendMessage({ type: 'fill', plan: String(m.plan || ''), idx: Number(m.idx) || 0 }); } catch (error) { /* extension reloaded: page refresh needed */ }
});
chrome.runtime.onMessage.addListener((m) => {
  if (m && (m.type === 'status' || m.type === 'link' || m.type === 'estimate')) window.postMessage(Object.assign({ src: 'ccc-ext' }, m), location.origin);
});
window.postMessage({ src: 'ccc-ext', type: 'ready', version: chrome.runtime.getManifest().version }, location.origin);
