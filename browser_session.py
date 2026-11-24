"""Own Playwright thread: persistent separate Edge profile; no password collection."""
import queue, threading
class BrowserSession:
 def __init__(self,root,channel):
  self.root=root; self.channel=channel; self.q=queue.Queue(); self.thread=None
 def _start(self):
  if self.thread is None or not self.thread.is_alive():
   self.thread=threading.Thread(target=self.worker,daemon=True); self.thread.start()
 def open(self,url):
  self._start(); done=queue.Queue(); self.q.put((url,done))
  result=done.get(timeout=100)
  if result: raise ValueError(result)
 def call(self,fn,timeout=1800):
  """Run fn(page) on the browser thread in a new tab of the same signed-in profile."""
  self._start(); done=queue.Queue(); self.q.put((fn,done))
  ok,value=done.get(timeout=timeout)
  if not ok: raise value
  return value
 def worker(self):
  context=None; playwright=None
  while True:
   item,done=self.q.get()
   if item is None:
    if context: context.close()
    if playwright: playwright.stop()
    done.put(None); return
   try:
    page=None
    if context:
     try: page=context.new_page()
     except Exception:
      try: context.close()
      except Exception: pass
      context=None
    if not context:
     from playwright.sync_api import sync_playwright
     if playwright is None: playwright=sync_playwright().start()
     context=playwright.chromium.launch_persistent_context(str(self.root/'profiles'/'edge'),channel=self.channel,headless=False,no_viewport=True,args=['--start-maximized'])
    if page is None: page=context.pages[0] if context.pages and context.pages[0].url in ('about:blank','') else context.new_page()
    if callable(item):
     try: done.put((True,item(page)))
     except Exception as e: done.put((False,e))
     continue
    page.goto(item,wait_until='domcontentloaded',timeout=60000); done.put(None)
   except Exception as e:
    if context:
     try: context.close()
     except Exception: pass
    if playwright:
     try: playwright.stop()
     except Exception: pass
    context=None; playwright=None
    msg=type(e).__name__+': browser failed. Close other automation Edge windows, then retry.'
    done.put((False,RuntimeError(msg)) if callable(item) else msg)
 def close(self):
  if self.thread and self.thread.is_alive():
   done=queue.Queue(); self.q.put((None,done)); self.thread.join(timeout=8)
