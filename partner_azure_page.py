PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Partner Center Azure · Seven Seas (SS UAE)</title>
<style>
:root{--bg:#0b1220;--p:#111a2e;--p2:#16213a;--line:#24324f;--t:#e7ecf6;--m:#8fa0c0;--a:#4da3ff;--g:#34d399;--r:#f87171;--y:#fbbf24;--v:#a78bfa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 Segoe UI,system-ui,sans-serif}
a{color:var(--a);text-decoration:none}.wrap{max-width:1400px;margin:0 auto;padding:18px}
.hero{background:linear-gradient(120deg,#0f2a52,#13203b);border:1px solid var(--line);border-radius:14px;padding:18px 22px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
.hero h1{margin:0;font-size:22px}.hero p{margin:4px 0 0;color:var(--m)}
.btn{background:var(--a);color:#06101f;border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer}.btn.s{background:var(--p2);color:var(--t);border:1px solid var(--line)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:14px 0}
.k{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.k b{display:block;font-size:22px;margin-top:2px}.k small{color:var(--m)}
.card{background:var(--p);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}.card h2{margin:0 0 10px;font-size:15px}
.row{display:grid;grid-template-columns:2fr 1fr;gap:14px}@media(max-width:900px){.row{grid-template-columns:1fr}}
.seg button{background:var(--p2);color:var(--m);border:1px solid var(--line);padding:5px 12px;cursor:pointer}.seg button.on{background:var(--a);color:#06101f;font-weight:700}
.seg button:first-child{border-radius:8px 0 0 8px}.seg button:last-child{border-radius:0 8px 8px 0}
table{border-collapse:collapse;width:100%}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child,th.l,td.l{text-align:left}
th{color:var(--m);font-weight:600;position:sticky;top:0;background:var(--p)}.scr{overflow:auto;max-height:560px}
tr.c{cursor:pointer}tr.c:hover{background:var(--p2)}tr.sub td{background:#0e1730;color:var(--m);font-size:12.5px}
.pill{border-radius:99px;padding:1px 9px;font-size:12px;font-weight:600}.EA{background:#12385f;color:#7cc0ff}.CSP{background:#2a1f55;color:#c4b2ff}.Mixed{background:#4a3a0b;color:#fbd56a}.Other{background:#2b3345;color:#9fb0cc}
.up{color:var(--g)}.dn{color:var(--r)}input[type=text]{background:var(--p2);border:1px solid var(--line);color:var(--t);border-radius:8px;padding:6px 10px}
.drop{border:2px dashed var(--line);border-radius:12px;padding:16px;text-align:center;color:var(--m)}.drop.h{border-color:var(--a)}
.note{color:var(--m);font-size:12.5px}.msg{margin:8px 0;color:var(--y)}
</style></head><body><div class="wrap">
__CGNAV__
<div class="hero"><div><h1>Partner Center Azure · Seven Seas (SS UAE)</h1>
<p id="sub">Every Azure customer, EA or CSP, month by month and service by service. Source: Partner Center Azure usage (ACR, USD).</p></div>
<div><a class="btn s" href="/cost-growth">Cost Growth (daily)</a> <a class="btn" href="/ai/partner-azure/download">Download Excel</a></div></div>
<div class="kpis" id="kpis"></div>
<div class="card"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px"><h2>Filters</h2>
<div><span class="seg" id="seg"><button data-v="All" class="on">All</button><button data-v="EA">EA</button><button data-v="CSP">CSP</button><button data-v="Mixed">Mixed</button><button data-v="Other">Other</button></span>
<input type="text" id="q" placeholder="Search customer / enrollment / subscription"></div></div></div>
<div class="row"><div class="card"><h2>Monthly ACR by agreement type</h2><div id="trend"></div></div>
<div class="card"><h2>Top services (latest month)</h2><div id="topsvc"></div></div></div>
<div class="card"><h2>Customers by month <span class="note">(click a row for services and subscriptions by month)</span></h2><div class="scr" id="cust"></div></div>
<div class="card"><h2>Services across all customers</h2><div class="scr" id="svc"></div></div>
<div class="card"><h2>Data files</h2><div class="drop" id="drop">Drop the Partner Center Azure usage CSV here, or <label style="color:var(--a);cursor:pointer">browse<input type="file" id="file" accept=".csv,.tsv,.txt" hidden multiple></label></div>
<div class="msg" id="msg"></div><div id="files"></div>
<p class="note">Partner Center publishes Azure usage monthly (after the 5th of the next month). Re-upload any time: for each month the newest file wins, so nothing is double counted. Files saved in the inbox folder are picked up automatically every few hours.
Agreement type is inferred: EA = Sales channel EA or an enrollment number is present; CSP = CSP / partner-led attribution; Mixed = both. Day-by-day figures are on the Cost Growth page (Cost Management), as Partner Center is monthly.</p></div>
</div>
<script>
var D=null,F='All',Q='',open={};
var $=function(i){return document.getElementById(i)};
function fm(n){n=n||0;var a=Math.abs(n);return (n<0?'-':'')+(a>=1e6?(a/1e6).toFixed(2)+'M':a>=1e3?(a/1e3).toFixed(1)+'K':a.toFixed(0))}
function fx(n){return (n||0).toLocaleString('en-US',{maximumFractionDigits:0})}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function mon(m){var p=m.split('-');return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+p[1]-1]+' '+p[0].slice(2)}
function pct(a,b){return b>0?(a-b)/b*100:null}
function pc(v){return v==null?'<span class="note">n/a</span>':'<span class="'+(v>=0?'up':'dn')+'">'+(v>=0?'+':'')+v.toFixed(1)+'%</span>'}
function load(){fetch('/ai/partner-azure/data',{cache:'no-store'}).then(function(r){return r.ok?r.json():null}).then(function(d){D=d;render();status()}).catch(function(){render()})}
function status(){fetch('/ai/partner-azure/status').then(function(r){return r.json()}).then(function(s){
 var h='<table><tr><th class="l">File</th><th>Months</th><th>Rows</th><th>Added (UTC)</th><th>Services</th><th>Attribution</th><th></th></tr>';
 (s.files||[]).forEach(function(f){h+='<tr><td class="l">'+esc(f.file)+(f.warnings&&f.warnings.length?'<div class="note">'+esc(f.warnings.join(' '))+'</div>':'')+'</td><td>'+esc(f.months[0]||'')+' → '+esc(f.months[f.months.length-1]||'')+'</td><td>'+fx(f.rows)+'</td><td>'+esc(f.added.replace('T',' '))+'</td><td>'+(f.has_services?'yes':'no')+'</td><td>'+(f.has_attrib?'yes':'no')+'</td><td><button class="btn s" data-rm="'+f.id+'">Remove</button></td></tr>'});
 $('files').innerHTML=(s.files||[]).length?h+'</table>':'<p class="note">No files yet.</p>';
 [].forEach.call(document.querySelectorAll('[data-rm]'),function(b){b.onclick=function(){post('remove',{id:b.dataset.rm})}})})}
function post(a,o){return fetch('/ai/partner-azure/'+a,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o||{})}).then(function(r){return r.json()}).then(function(j){if(j.error)$('msg').textContent=j.error;load()})}
function up(files){var l=[].slice.call(files);(function nx(){var f=l.shift();if(!f){load();return}$('msg').textContent='Uploading '+f.name+' ...';
 fetch('/ai/partner-azure/upload',{method:'POST',headers:{'X-File-Name':encodeURIComponent(f.name),'Content-Type':'application/octet-stream'},body:f}).then(function(r){return r.json()}).then(function(j){$('msg').textContent=j.error?j.error:(j.result&&j.result.duplicate?'Already loaded: '+f.name:'Loaded '+f.name);nx()}).catch(function(){$('msg').textContent='Upload failed';nx()})})()}
$('file').onchange=function(){up(this.files)};
var dr=$('drop');dr.ondragover=function(e){e.preventDefault();dr.classList.add('h')};dr.ondragleave=function(){dr.classList.remove('h')};dr.ondrop=function(e){e.preventDefault();dr.classList.remove('h');up(e.dataTransfer.files)};
[].forEach.call(document.querySelectorAll('#seg button'),function(b){b.onclick=function(){F=b.dataset.v;[].forEach.call(document.querySelectorAll('#seg button'),function(x){x.classList.toggle('on',x===b)});render()}});
$('q').oninput=function(){Q=this.value.toLowerCase();render()};
function rows(){if(!D||!D.customers)return[];return D.customers.filter(function(c){if(F!=='All'&&c.cls!==F)return false;if(!Q)return true;
 return (c.name+' '+c.enroll.join(' ')+' '+Object.keys(c.subs).join(' ')).toLowerCase().indexOf(Q)>=0})}
function render(){
 if(!D||!D.customers||!D.customers.length){$('kpis').innerHTML='';$('cust').innerHTML='<p class="note">No data yet. Upload the Partner Center Azure usage CSV below.</p>';$('trend').innerHTML='';$('topsvc').innerHTML='';$('svc').innerHTML='';return}
 var M=D.months,L=M[M.length-1],P=M[M.length-2],R=rows();
 var tot=function(m){return R.reduce(function(s,c){return s+(c.by_month[m]||0)},0)};
 var active=R.filter(function(c){return (c.by_month[L]||0)>0}).length;
 var cnt={};R.forEach(function(c){cnt[c.cls]=(cnt[c.cls]||0)+((c.by_month[L]||0)>0?1:0)});
 var first=M[Math.max(0,M.length-13)],yoy=M.length>12?pct(tot(L),tot(M[M.length-13])):null;
 var all=R.reduce(function(s,c){return s+c.total},0),spk=R.filter(function(c){return c.spikes.indexOf(L)>=0}).length;
 $('kpis').innerHTML=[['Latest month',mon(L),'closed month in the data'],['ACR '+mon(L),'$'+fm(tot(L)),''],['MoM',pc(pct(tot(L),tot(P))),'vs '+mon(P)],['YoY',pc(yoy),''],
  ['Active customers',active,Object.keys(cnt).map(function(k){return k+' '+cnt[k]}).join(' · ')],['Period total','$'+fm(all),mon(M[0])+' – '+mon(L)],['Customers with a spike',spk,'≥ $500 and ≥ 10% in '+mon(L)]]
  .map(function(k){return '<div class="k"><small>'+k[0]+'</small><b>'+k[1]+'</b><small>'+k[2]+'</small></div>'}).join('');
 // trend stacked by class
 var cls=['EA','CSP','Mixed','Other'],col={EA:'#4da3ff',CSP:'#a78bfa',Mixed:'#fbbf24',Other:'#64748b'},W=760,H=250,pl=44,pb=26,bw=(W-pl)/M.length;
 var st=M.map(function(m){var o={};R.forEach(function(c){o[c.cls]=(o[c.cls]||0)+(c.by_month[m]||0)});return o});
 var mx=Math.max.apply(null,st.map(function(o){return cls.reduce(function(s,k){return s+(o[k]||0)},0)}))||1,svg='<svg viewBox="0 0 '+W+' '+(H+pb)+'" width="100%">';
 for(var i=0;i<=4;i++){var y=H-i/4*(H-10);svg+='<line x1="'+pl+'" x2="'+W+'" y1="'+y+'" y2="'+y+'" stroke="#24324f"/><text x="2" y="'+(y+4)+'" fill="#8fa0c0" font-size="10">$'+fm(mx*i/4)+'</text>'}
 M.forEach(function(m,i){var y0=H,x=pl+i*bw+3;cls.forEach(function(k){var v=st[i][k]||0,h=v/mx*(H-10);if(h>0){svg+='<rect x="'+x+'" y="'+(y0-h)+'" width="'+(bw-6)+'" height="'+h+'" fill="'+col[k]+'"><title>'+mon(m)+' '+k+' $'+fx(v)+'</title></rect>';y0-=h}});
  svg+='<text x="'+(x+(bw-6)/2)+'" y="'+(H+16)+'" fill="#8fa0c0" font-size="10" text-anchor="middle">'+mon(m)+'</text>'});
 $('trend').innerHTML=svg+'</svg><div class="note">'+cls.map(function(k){return '<span style="color:'+col[k]+'">■</span> '+k}).join(' &nbsp; ')+'</div>';
 // top services
 var sv={};R.forEach(function(c){for(var s in c.services)sv[s]=(sv[s]||0)+(c.services[s][L]||0)});
 var top=Object.keys(sv).sort(function(a,b){return sv[b]-sv[a]}).slice(0,10),sm=sv[top[0]]||1;
 $('topsvc').innerHTML=top.map(function(s){return '<div style="margin:6px 0"><div style="display:flex;justify-content:space-between"><span>'+esc(s)+'</span><b>$'+fm(sv[s])+'</b></div><div style="height:6px;background:#1c2946;border-radius:4px"><div style="height:6px;width:'+(sv[s]/sm*100)+'%;background:#4da3ff;border-radius:4px"></div></div></div>'}).join('');
 // customers
 var h='<table><tr><th class="l">Customer</th><th class="l">Agreement</th><th class="l">Enrollment</th>'+M.map(function(m){return '<th>'+mon(m)+'</th>'}).join('')+'<th>Total</th><th>MoM</th></tr>';
 R.forEach(function(c){h+='<tr class="c" data-id="'+esc(c.id)+'"><td class="l">'+(open[c.id]?'▾ ':'▸ ')+esc(c.name)+'</td><td class="l"><span class="pill '+c.cls+'">'+c.cls+'</span></td><td class="l">'+esc(c.enroll.join(', '))+'</td>'+
  M.map(function(m){return '<td>'+(c.by_month[m]?fx(c.by_month[m]):'')+'</td>'}).join('')+'<td><b>'+fx(c.total)+'</b></td><td>'+pc(c.mom)+'</td></tr>';
  if(open[c.id]){var n=M.length+5;h+='<tr class="sub"><td class="l" colspan="'+n+'"><b>Services</b></td></tr>';
   Object.keys(c.services).forEach(function(s){var d=c.services[s];h+='<tr class="sub"><td class="l" colspan="3">'+esc(s)+'</td>'+M.map(function(m){return '<td>'+(d[m]?fx(d[m]):'')+'</td>'}).join('')+'<td>'+fx(Object.keys(d).reduce(function(a,k){return a+d[k]},0))+'</td><td></td></tr>'});
   var ks=Object.keys(c.subs);if(ks.length){h+='<tr class="sub"><td class="l" colspan="'+n+'"><b>Subscriptions</b></td></tr>';
    ks.forEach(function(s){var d=c.subs[s].by_month;h+='<tr class="sub"><td class="l" colspan="3">'+esc(s)+' <span class="note">'+esc(c.subs[s].state||'')+'</span></td>'+M.map(function(m){return '<td>'+(d[m]?fx(d[m]):'')+'</td>'}).join('')+'<td>'+fx(Object.keys(d).reduce(function(a,k){return a+d[k]},0))+'</td><td></td></tr>'})}}});
 h+='<tr><td class="l"><b>Total</b></td><td></td><td></td>'+M.map(function(m){return '<td><b>'+fx(tot(m))+'</b></td>'}).join('')+'<td><b>'+fx(all)+'</b></td><td>'+pc(pct(tot(L),tot(P)))+'</td></tr></table>';
 $('cust').innerHTML=h;
 [].forEach.call(document.querySelectorAll('tr.c'),function(r){r.onclick=function(){open[r.dataset.id]=!open[r.dataset.id];render()}});
 // services portfolio
 var ps=D.services||{},ks2=Object.keys(ps).slice(0,60),s2='<table><tr><th class="l">Service</th>'+M.map(function(m){return '<th>'+mon(m)+'</th>'}).join('')+'<th>Total</th><th>MoM</th></tr>';
 ks2.forEach(function(s){var d=ps[s];s2+='<tr><td class="l">'+esc(s)+'</td>'+M.map(function(m){return '<td>'+(d[m]?fx(d[m]):'')+'</td>'}).join('')+'<td><b>'+fx(Object.keys(d).reduce(function(a,k){return a+d[k]},0))+'</b></td><td>'+pc(pct(d[L]||0,d[P]||0))+'</td></tr>'});
 $('svc').innerHTML=s2+'</table>';
}
load();setInterval(load,600000);
</script></body></html>"""
