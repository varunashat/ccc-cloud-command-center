PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BOQ alignment check</title>
<style>
:root{--bg:#0b1220;--p:#111a2e;--p2:#16213a;--line:#24324f;--t:#e7ecf6;--m:#8fa0c0;--a:#4da3ff;--g:#34d399;--r:#f87171;--y:#fbbf24}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 Segoe UI,system-ui,sans-serif}.wrap{max-width:1300px;margin:0 auto;padding:18px}a{color:var(--a)}
.hero{background:linear-gradient(120deg,#0f2a52,#13203b);border:1px solid var(--line);border-radius:14px;padding:18px 22px}.hero h1{margin:0;font-size:22px}.hero p{margin:4px 0 0;color:var(--m)}
.card{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:14px;margin:14px 0}.card h2{margin:0 0 10px;font-size:15px}
label{display:block;color:var(--m);font-size:12.5px;margin:8px 0 3px}select,input{width:100%;background:var(--p2);border:1px solid var(--line);color:var(--t);border-radius:8px;padding:7px 10px}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.btn{background:var(--a);color:#06101f;border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer;margin-top:10px}.btn.s{background:var(--p2);color:var(--t);border:1px solid var(--line)}
table{border-collapse:collapse;width:100%}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--m)}.scr{overflow:auto;max-height:600px}
.v{border-radius:12px;padding:14px 18px;font-size:18px;font-weight:700;margin-bottom:10px}.bad{background:#4a1d1d;color:#fca5a5}.ok{background:#12372a;color:#6ee7b7}
.pill{border-radius:99px;padding:1px 9px;font-size:12px;font-weight:600;background:#4a1d1d;color:#fca5a5}.pill.n{background:#4a3a0b;color:#fbd56a}.new{color:var(--y);font-weight:700}.note{color:var(--m);font-size:12.5px}
</style></head><body><div class="wrap">__CGNAV__
<div class="hero"><h1>BOQ alignment check</h1><p>Compares a customer's saved BOQ with what is really deployed in Azure. Anything different is reported as <b>not aligned with the BOQ</b>, with the parameter, the date and who changed it (Azure Activity Log, last 90 days). Read-only.</p></div>
<div class="card"><h2>1 · Choose BOQ and environment</h2><div class="g">
<div><label>BOQ (saved estimate)</label><select id="job"></select><div class="note" id="jinfo"></div></div>
<div><label>Customer tenant ID</label><input id="tenant" placeholder="00000000-0000-0000-0000-000000000000"><button class="btn s" id="loadsubs">Load subscriptions</button></div>
<div><label>Subscription(s)</label><select id="subs" multiple size="4"></select></div>
<div><label>Resource group (optional)</label><input id="rg" placeholder="leave empty for the whole subscription"></div></div>
<button class="btn" id="run">Run alignment check</button> <button class="btn s" id="watch">Check this every day</button> <span class="note" id="msg"></span></div>
<div id="res"></div>
<div class="card"><h2>Daily monitors</h2><div id="watches" class="note">None yet.</div></div>
<div class="card"><h2>Previous checks</h2><div id="hist" class="scr"></div></div></div>
<script>
var $=function(i){return document.getElementById(i)};function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function j(u,o){return fetch(u,o).then(function(r){return r.json()})}
function post(a,b){return j('/ai/boq/'+a,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
var B=[];
function body(){var s=[].slice.call($('subs').selectedOptions).map(function(o){return o.value});return {job:$('job').value,tenant:$('tenant').value.trim(),subscriptions:s,rg:$('rg').value.trim()}}
j('/ai/boq/baselines').then(function(d){B=d.baselines||[];$('job').innerHTML=B.map(function(b){return '<option value="'+b.job+'">'+esc((b.project||'BOQ')+' · '+b.saved.slice(0,10)+(b.customer_id?' · '+b.customer_id:'')+' · '+b.job)+'</option>'}).join('')||'<option value="">No saved BOQs yet</option>';info()});
function info(){var b=B.filter(function(x){return x.job===$('job').value})[0];$('jinfo').innerHTML=b?b.lines+' lines, '+b.comparable+' comparable resources (VM, disk, SQL, public IP)'+(b.calculator_url?' · <a href="'+esc(b.calculator_url)+'" target="_blank" rel="noopener">calculator link</a>':''):''}
$('job').onchange=info;
$('loadsubs').onclick=function(){$('msg').textContent='Loading subscriptions...';j('/ai/boq/subs?tenant='+encodeURIComponent($('tenant').value.trim())).then(function(d){$('msg').textContent=d.error||'';$('subs').innerHTML=(d.subscriptions||[]).map(function(s){return '<option value="'+s.id+'">'+esc(s.name)+' ('+s.id.slice(0,8)+')</option>'}).join('')})};
$('run').onclick=function(){$('msg').textContent='Running... reading inventory and Activity Log';post('check',body()).then(function(d){if(d.error){$('msg').textContent=d.error;return}poll(d.task)})};
function poll(t){j('/ai/boq/state?id='+t).then(function(s){if(s.status==='running'){setTimeout(function(){poll(t)},3000)}else if(s.status==='done'){$('msg').textContent='';show(s.id);hist()}else{$('msg').textContent=s.error||'Failed'}})}
$('watch').onclick=function(){post('watch',body()).then(function(d){$('msg').textContent=d.error||'Daily monitor added';wl(d.watches)})};
function wl(w){$('watches').innerHTML=(w&&w.length)?'<table><tr><th>BOQ</th><th>Tenant</th><th>Subscriptions</th><th></th></tr>'+w.map(function(x){return '<tr><td>'+x.job+'</td><td>'+x.tenant.slice(0,8)+'</td><td>'+x.subscriptions.length+'</td><td><button class="btn s" data-w="'+x.id+'">Stop</button></td></tr>'}).join('')+'</table>':'None yet.';[].forEach.call(document.querySelectorAll('[data-w]'),function(b){b.onclick=function(){post('unwatch',{id:b.dataset.w}).then(function(d){wl(d.watches)})}})}
j('/ai/boq/watches').then(function(d){wl(d.watches)});
function show(id){j('/ai/boq/result?id='+id).then(function(r){if(r.error){$('res').innerHTML='<div class="card">'+esc(r.error)+'</div>';return}
 var f=r.findings||[],h='<div class="card"><div class="v '+(f.length?'bad':'ok')+'">'+esc(r.verdict)+'</div><div class="note">'+r.deployed_resources+' deployed resources read · '+r.aligned+' aligned · '+f.length+' differences · checked '+esc(r.time.replace('T',' '))+' UTC · <a href="/ai/boq/xlsx?id='+r.id+'">Download Excel</a></div>';
 (r.warnings||[]).forEach(function(w){h+='<div class="note" style="color:var(--y)">'+esc(w)+'</div>'});
 if(f.length){h+='<div class="scr"><table><tr><th>Status</th><th>Resource</th><th>Parameter</th><th>In BOQ</th><th>Deployed</th><th>Last changed (UTC)</th><th>Changed by</th><th>Created</th></tr>';
  f.forEach(function(x){var lc=x.last_change||{},cr=x.created||{};x.changes.forEach(function(c,i){h+='<tr><td>'+(i?'':'<span class="pill'+(x.status==='Not aligned'?'':' n')+'">'+esc(x.status)+'</span>'+(x.new?' <span class="new">NEW</span>':''))+'</td><td>'+(i?'':esc(x.type)+'<br><b>'+esc(x.resource)+'</b>')+'</td><td>'+esc(c.parameter)+'</td><td>'+esc(c.boq)+'</td><td>'+esc(c.deployed)+'</td><td>'+(i?'':esc((lc.time||'not in last 90 days').replace('T',' ').slice(0,19))+(lc.op?'<div class="note">'+esc(lc.op)+'</div>':''))+'</td><td>'+(i?'':esc(lc.by||''))+'</td><td>'+(i?'':esc((cr.time||'').replace('T',' ').slice(0,10))+(cr.by?'<div class="note">'+esc(cr.by)+'</div>':''))+'</td></tr>'})});
  h+='</table></div><p class="note">Who and when come from the Azure Activity Log, which keeps 90 days. A change older than that shows no date. VM size, OS, Hybrid Benefit, region, disk tier/size and SQL vCores are compared.</p>'}
 $('res').innerHTML=h+'</div>'})}
function hist(){j('/ai/boq/history').then(function(d){var h=d.history||[];$('hist').innerHTML=h.length?'<table><tr><th>When (UTC)</th><th>BOQ</th><th>Result</th><th>Differences</th><th></th></tr>'+h.map(function(x){return '<tr><td>'+esc(x.time.replace('T',' '))+'</td><td>'+esc(x.project||x.job)+'</td><td>'+esc(x.verdict)+'</td><td>'+x.drift+'</td><td><button class="btn s" data-h="'+x.id+'">Open</button></td></tr>'}).join('')+'</table>':'No checks yet.';[].forEach.call(document.querySelectorAll('[data-h]'),function(b){b.onclick=function(){show(b.dataset.h);window.scrollTo(0,0)}})})}
hist();
</script></body></html>"""
