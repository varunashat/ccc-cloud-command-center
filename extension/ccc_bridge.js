// Bridge between the Cloud Command Center page and the extension (isolated world).
window.addEventListener('message', (e) => {
  const m = e.data;
  if (e.source !== window || e.origin !== location.origin || !m || m.src !== 'ccc-page') return;
  if (m.type === 'agent') {
    chrome.runtime.sendMessage({ type: 'agent', op: String(m.op || ''), url: m.url, action: m.action }).then(
      (r) => window.postMessage({ src: 'ccc-ext', type: 'agent-result', id: m.id, result: r }, location.origin),
      (err) => window.postMessage({ src: 'ccc-ext', type: 'agent-result', id: m.id, result: { error: String(err) } }, location.origin));
    return;
  }
  if (m.type !== 'fill') return;
  chrome.runtime.sendMessage({ type: 'fill', plan: String(m.plan || ''), idx: Number(m.idx) || 0 });
});
chrome.runtime.onMessage.addListener((m) => {
  if (m && (m.type === 'status' || m.type === 'link')) window.postMessage(Object.assign({ src: 'ccc-ext' }, m), location.origin);
});
window.postMessage({ src: 'ccc-ext', type: 'ready' }, location.origin);
