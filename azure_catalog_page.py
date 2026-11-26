PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Azure services and SKUs</title>
<style>
:root{--bg:#0b1220;--p:#111a2e;--p2:#16213a;--line:#24324f;--t:#e7ecf6;--m:#8fa0c0;--a:#4da3ff;--g:#34d399}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 Segoe UI,system-ui,sans-serif}.wrap{max-width:1400px;margin:0 auto;padding:18px}a{color:var(--a)}
.hero{background:linear-gradient(120deg,#0f2a52,#13203b);border:1px solid var(--line);border-radius:14px;padding:18px 22px}.hero h1{margin:0;font-size:22px}.hero p{margin:4px 0 0;color:var(--m)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:14px 0}.k{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.k b{display:block;font-size:24px}.k small{color:var(--m)}
.card{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}.card h2{margin:0 0 10px;font-size:15px}
.cols{display:grid;grid-template-columns:280px 1fr;gap:14px}@media(max-width:900px){.cols{grid-template-columns:1fr}}
input,select{background:var(--p2);border:1px solid var(--line);color:var(--t);border-radius:8px;padding:7px 10px;width:100%}.btn{background:var(--a);color:#06101f;border:0;border-radius:8px;padding:7px 12px;font-weight:600;cursor:pointer}.btn.s{background:var(--p2);color:var(--t);border:1px solid var(--line)}
table{border-collapse:collapse;width:100%}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--m);position:sticky;top:0;background:var(--p)}.scr{overflow:auto;max-height:560px}
.sv{cursor:pointer;padding:4px 8px;border-radius:6px;display:flex;justify-content:space-between}.sv:hover,.sv.on{background:var(--p2)}.sv small{color:var(--m)}.note{color:var(--m);font-size:12.5px}.row{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap}.row>*{flex:1;min-width:140px}
</style></head><body><div class="wrap">__CGNAV__
<div class="hero"><h1>Azure services and SKUs</h1><p>Every service and SKU in the official Azure price list, searchable, with a basket that is priced into an Excel BOQ. Refreshed daily from the Azure Retail Prices API (public list prices).</p></div>
<div class="kpis" id="kpis"></div>
<div class="cols"><div class="card"><h2>Services</h2><input id="svq" placeholder="Filter services"><div class="scr" id="svl" style="margin-top:8px;max-height:620px"></div></div>
<div><div class="card"><h2>Find SKUs</h2><div class="row"><input id="q" placeholder="Search service, product, SKU or meter, e.g. bastion standard"><select id="reg"><option value="">Any region</option></select></div>
<div class="scr"><table id="tb"></table></div><div class="note" id="info"></div><button class="btn s" id="more" style="display:none;margin-top:8px">Load more</button></div>
<div class="card"><h2>Basket</h2><div id="basket" class="note">Empty. Use Add on a SKU.</div>
<div class="row" style="margin-top:8px"><input id="proj" placeholder="Project name"><select id="breg"></select><button class="btn" id="price">Price into BOQ</button></div><div class="note" id="pm"></div></div></div></div>
<div class="card"><h2>Azure Calculator with the AI browser</h2><p class="note">The hosted app prices the basket from the official price list and gives you the Excel BOQ. To have the AI browser fill the real Azure Calculator, use the Calculator AI agent in the laptop version of Cloud Command Center: price the basket here first, then pass that estimate to the agent.</p></div>
<div class="card"><button class="btn s" id="refresh">Refresh catalogue now</button> <span class="note" id="rs"></span></div></div>
<script>
var $=function(i){return document.getElementById(i)};function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function j(u,o){return fetch(u,o).then(function(r){return r.json()})}
var SV='',OFF=0,R=[],BK=[],SL=[];
function stats(){j('/ai/azure-catalog/stats').then(function(s){
 if(!s.has_data){$('kpis').innerHTML='';$('svl').innerHTML='<p class="note">Catalogue is being built ('+(s.done||0)+' of '+(s.total||0)+' regions). Come back in a few minutes.</p>';if(s.status!=='running')j('/ai/azure-catalog/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});setTimeout(stats,8000);return}
 $('kpis').innerHTML=[['Azure services',s.services],['SKUs',s.skus.toLocaleString()],['Meters',s.meters.toLocaleString()],['Regions covered',s.regions.length],['Refreshed',s.generated.slice(0,10)]].map(function(k){return '<div class="k"><small>'+k[0]+'</small><b>'+k[1]+'</b></div>'}).join('');
 SL=s.service_list;svl();$('reg').innerHTML='<option value="">Any region</option>'+s.regions.map(function(r){return '<option>'+r+'</option>'}).join('');$('breg').innerHTML=s.regions.filter(function(r){return r!=='Global'}).map(function(r){return '<option'+(r==='uaenorth'?' selected':'')+'>'+r+'</option>'}).join('');
 $('rs').textContent=s.status==='running'?'Refreshing: '+s.done+' of '+s.total+' regions':'';if(s.status==='running')setTimeout(stats,8000);find(true)})}
function svl(){var f=$('svq').value.toLowerCase();$('svl').innerHTML='<div class="sv'+(SV?'':' on')+'" data-s="">All services <small>'+SL.length+'</small></div>'+SL.filter(function(x){return !f||x.service.toLowerCase().indexOf(f)>=0}).map(function(x){return '<div class="sv'+(SV===x.service?' on':'')+'" data-s="'+esc(x.service)+'">'+esc(x.service)+' <small>'+x.skus+'</small></div>'}).join('');
 [].forEach.call(document.querySelectorAll('.sv'),function(d){d.onclick=function(){SV=d.dataset.s;svl();find(true)}})}
$('svq').oninput=svl;
function find(reset){if(reset){OFF=0;R=[]}j('/ai/azure-catalog/search?q='+encodeURIComponent($('q').value)+'&service='+encodeURIComponent(SV)+'&region='+encodeURIComponent($('reg').value)+'&offset='+OFF).then(function(d){R=R.concat(d.results||[]);OFF=R.length;
 $('tb').innerHTML='<tr><th>Service</th><th>Product</th><th>SKU</th><th>Meters</th><th>Unit</th><th>List price</th><th></th></tr>'+R.map(function(r,i){return '<tr><td>'+esc(r.service)+'</td><td>'+esc(r.product)+'</td><td>'+esc(r.sku)+'</td><td class="note">'+esc(r.meters.slice(0,3).join(', '))+'</td><td>'+esc(r.unit)+'</td><td>'+(r.price!=null?'$'+r.price:'')+'</td><td><button class="btn s" data-i="'+i+'">Add</button></td></tr>'}).join('');
 $('info').textContent=d.total.toLocaleString()+' SKUs match'+(R.length<d.total?' · showing '+R.length:'');$('more').style.display=R.length<d.total?'':'none';
 [].forEach.call(document.querySelectorAll('[data-i]'),function(b){b.onclick=function(){var r=R[+b.dataset.i];BK.push({service:r.service,product:r.product,sku:r.sku,meter:r.meters.length===1?r.meters[0]:'',quantity:1});bk()}})})}
$('q').oninput=function(){clearTimeout(window._t);window._t=setTimeout(function(){find(true)},300)};$('reg').onchange=function(){find(true)};$('more').onclick=function(){find(false)};
function bk(){$('basket').innerHTML=BK.length?'<table><tr><th>Service</th><th>SKU</th><th>Meter (optional)</th><th>Quantity</th><th></th></tr>'+BK.map(function(x,i){return '<tr><td>'+esc(x.service)+'</td><td>'+esc(x.product+' / '+x.sku)+'</td><td><input data-m="'+i+'" value="'+esc(x.meter)+'"></td><td><input data-q="'+i+'" type="number" min="0" value="'+x.quantity+'" style="width:90px"></td><td><button class="btn s" data-d="'+i+'">Remove</button></td></tr>'}).join('')+'</table>':'Empty. Use Add on a SKU.';
 [].forEach.call(document.querySelectorAll('[data-q]'),function(e){e.onchange=function(){BK[+e.dataset.q].quantity=+e.value}});[].forEach.call(document.querySelectorAll('[data-m]'),function(e){e.onchange=function(){BK[+e.dataset.m].meter=e.value}});[].forEach.call(document.querySelectorAll('[data-d]'),function(e){e.onclick=function(){BK.splice(+e.dataset.d,1);bk()}})}
$('price').onclick=function(){$('pm').textContent='Starting...';j('/ai/azure-catalog/price',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:BK,project:$('proj').value,region:$('breg').value})}).then(function(d){if(d.error){$('pm').textContent=d.error;return}$('pm').textContent=d.status;poll(d.job)})};
function poll(job){j('/api/state').then(function(s){var e=(s.estimator_jobs||{})[job];if(!e){setTimeout(function(){poll(job)},3000);return}if(e.status==='running'){$('pm').textContent=e.message||'Pricing...';setTimeout(function(){poll(job)},3000)}else if(e.status==='failed'||e.status==='error'){$('pm').textContent=e.message||'Failed'}else{var d=e.downloads||{};$('pm').innerHTML='Done. '+(d['boq.xlsx']?'<a href="'+d['boq.xlsx']+'">Download Excel BOQ</a>':'')+(e.message?' · '+esc(e.message):'')}})}
$('refresh').onclick=function(){j('/ai/azure-catalog/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(function(){stats()})};
stats();
</script></body></html>"""
