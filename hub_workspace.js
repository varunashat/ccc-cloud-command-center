(() => {
  const pages = {
    '#hub': ['Assessment Hub', 'Run assessments for the selected customer and reopen saved results.'],
    '#hub-infrastructure': ['Infrastructure Assessment', 'Azure resources, capacity evidence and dependency gaps.'],
    '#hub-security': ['Security Assessment', 'Defender assessments and configured network controls.'],
    '#hub-cost': ['Cost Optimization', 'Actual cost for the last 30 days and Azure Advisor evidence.'],
    '#hub-modernization': ['Application Modernization', 'VM platform evidence and application discovery requirements.'],
    '#hub-deployment': ['Environment & Deployment', 'Prepare a private deployment plan for the selected subscription.']
  };
  const labels = {infrastructure:'Infrastructure',security:'Security',cost:'Cost optimization',modernization:'Modernization'};
  const style = document.createElement('style');
  style.textContent = `#ccc-ai-btn{display:none!important}body.hub-active main > :not(#assessment-workspace){display:none!important}
    #assessment-workspace{max-width:1250px} #assessment-workspace .hub-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}
    #assessment-workspace .panel{margin:18px 0;padding:22px;border:1px solid #293950;border-radius:14px;background:#111c30}
    #assessment-workspace h2{font-size:20px;margin:0 0 12px} #assessment-workspace button{margin:4px}
    #assessment-workspace .hub-table{overflow:auto;max-height:420px} #assessment-workspace table{border-collapse:collapse;width:100%;font-size:13px}
    #assessment-workspace td,#assessment-workspace th{padding:10px;text-align:left;border-bottom:1px solid #293950;max-width:450px;overflow-wrap:anywhere}
    #assessment-workspace label{display:block;margin:10px 0} #assessment-workspace input{max-width:100%}
    #assessment-workspace pre{white-space:pre-wrap;max-height:380px;overflow:auto} #assessment-workspace [role=status]{color:#a8c4df}
    #assessment-workspace .hub-run{padding:14px 0;border-bottom:1px solid #293950}`;
  document.head.append(style);
  const root = document.createElement('div'); root.id='assessment-workspace'; root.hidden=true;
  const title=document.createElement('h1'); const subtitle=document.createElement('p');
  const scopeBox=document.createElement('section'); scopeBox.className='panel';
  scopeBox.append(element('h2','Customer and subscription'));
  const selectedLabel=element('p','Load the customer directory to select authorized subscriptions.');
  const content=document.createElement('div'); const status=element('p','');status.setAttribute('role','status');
  root.append(title,subtitle,scopeBox,selectedLabel,status,content);
  document.querySelector('main').append(root);
  let moved=[], timer, generation=0, currentJobs=[], shownJob;
  function element(tag,text){const node=document.createElement(tag);node.textContent=text;return node;}
  function control(parent,label,action){const button=element('button',label);button.type='button';parent.append(button);
    button.onclick=async()=>{button.disabled=true;status.textContent='';try{await action();}catch(error){status.textContent=error.message;}finally{button.disabled=false;}};return button;}
  function context(){return {tenant:document.getElementById('dc-tenant')?.value,subscriptions:dcSubs().sort()};}
  function customer(){return document.getElementById('dc-tenant')?.selectedOptions[0]?.textContent.split(' · ')[0]||'Customer';}
  function ready(){const c=context();if(!c.tenant||!c.subscriptions.length)throw new Error('Load a customer directory and select subscriptions first.');return c;}
  const signInBox=document.createElement('div');scopeBox.append(signInBox);
  control(signInBox,'Refresh selected directory sign-in',async()=>{
    const c=context();if(!c.tenant)throw new Error('Load directories and select the customer first.');
    const response=await fetch('/api/supervisor',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({action:'refresh-tenant-signin',tenant:c.tenant})});
    const flow=await response.json();if(!response.ok)throw new Error(flow.error||'Could not start sign-in.');
    const instructions=document.createElement('p');const link=element('a','Open Microsoft sign-in');link.href=flow.url;link.target='_blank';link.rel='noopener';
    instructions.append(link,element('span',' and enter this code: '),element('strong',flow.code));signInBox.append(instructions);
    const poll=async()=>{try{const r=await fetch('/auth/poll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fid:flow.fid})});const state=await r.json();
      if(state.status==='done'){instructions.textContent='Microsoft sign-in refreshed. You can resume the selected customer run.';await load();return;}
      if(state.status==='pending'){setTimeout(poll,3000);return;}
      instructions.textContent=state.message||state.error||'Sign-in did not complete.';
    }catch(error){instructions.textContent=error.message;}};
    setTimeout(poll,3000);
  });
  async function api(body){const response=await fetch('/api/supervisor',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({channel:'assessment-hub',...context(),...body})});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Assessment request failed.');return data;}
  function download(parent,name,data,type='application/json'){control(parent,'Download '+name,()=>{const a=document.createElement('a');const url=URL.createObjectURL(new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});}
  function moveScope(){if(moved.length)return;
    const nodes=[document.getElementById('dc-tenants')?.closest('.row'),document.getElementById('dc-tenant')?.closest('.row'),document.getElementById('dc-sublist')];
    for(const node of nodes){if(!node)continue;const mark=document.createComment('shared customer scope');node.before(mark);scopeBox.append(node);moved.push([node,mark]);}}
  function restoreScope(){for(const [node,mark] of moved){mark.replaceWith(node);}moved=[];}
  async function start(workflows){const c=ready();const job=await api({action:'start',workflows,id:crypto.randomUUID(),customer:customer()});shownJob=job;status.textContent='Assessment queued. The cloud worker checks every five minutes.';await load();}
  function table(parent,rows){if(!rows.length){parent.append(element('p','No matching rows were returned.'));return;}
    const wrap=document.createElement('div');wrap.className='hub-table';const t=document.createElement('table');const fields=[...new Set(rows.flatMap(row=>Object.keys(row)))];
    const header=document.createElement('tr');fields.forEach(field=>header.append(element('th',field)));t.append(header);
    for(const row of rows.slice(0,200)){const tr=document.createElement('tr');for(const field of fields){const value=row[field];tr.append(element('td',value&&typeof value==='object'?JSON.stringify(value):String(value??'')));}t.append(tr);}wrap.append(t);parent.append(wrap);
    if(rows.length>200)parent.append(element('p',`Showing 200 of ${rows.length} rows. Download the complete result for every row.`));}
  function showSection(parent,section){parent.append(element('h3',labels[section.workflow]||section.workflow),element('p',`${section.status} · Captured ${new Date(section.captured_at).toLocaleString()}`));
    if(section.estimated_savings==null)parent.append(element('p','Estimated savings await validated cost and utilization evidence.'));
    for(const gap of section.gaps||[])parent.append(element('p',gap));
    for(const finding of section.findings||[]){const details=document.createElement('details');details.append(element('summary',finding.title));const pre=element('pre',JSON.stringify(finding.evidence,null,2));details.append(pre);parent.append(details);}
    table(parent,section.rows);if(section.csv)download(parent,section.workflow+'.csv',section.csv,'text/csv');download(parent,section.workflow+'.json',section);}
  function renderJobs(parent,workflow){const relevant=currentJobs.filter(job=>!workflow||job.workflows.includes(workflow));
    if(!relevant.length){parent.append(element('p','No saved runs in this customer and subscription scope.'));return;}
    relevant.forEach(job=>{const row=document.createElement('div');row.className='hub-run';row.append(element('strong',`${job.customer} · ${job.status}`),element('p',new Date(job.created).toLocaleString()));
      control(row,'Open saved run',async()=>{shownJob=await api({action:'status',id:job.id});render();});
      const actions=job.status==='Paused'||job.status==='Needs sign-in'?['resume','cancel']:['Queued','In progress'].includes(job.status)?['pause','cancel']:[];
      for(const action of actions)control(row,action[0].toUpperCase()+action.slice(1),async()=>{await api({action,id:job.id});await load();});parent.append(row);});}
  function render(){content.replaceChildren();const hash=location.hash;const workflow=Object.keys(labels).find(key=>hash==='#hub-'+key);
    if(hash==='#hub-deployment'){deploymentPage();return;}
    if(workflow){control(content,'Run '+labels[workflow]+' assessment',()=>start([workflow]));}
    else{const cards=document.createElement('div');cards.className='hub-cards';for(const [key,label]of Object.entries(labels)){const card=document.createElement('section');card.className='panel';card.append(element('h2',label));const a=element('a','Open assessment');a.href='#hub-'+key;card.append(a);const latest=currentJobs.find(job=>job.sections[key]);card.append(element('p',latest?`${latest.status} · ${new Date(latest.created).toLocaleString()}`:'No saved assessment'));cards.append(card);}content.append(cards);control(content,'Run all assessments',()=>start(Object.keys(labels)));}
    const history=document.createElement('section');history.className='panel';history.append(element('h2','Customer run history'));renderJobs(history,workflow);content.append(history);
    if(shownJob&&JSON.stringify(shownJob.scope)===JSON.stringify(context())){const output=document.createElement('section');output.className='panel';output.append(element('h2','Saved run results'),element('p',shownJob.status));if(shownJob.pending)output.append(element('p',shownJob.pending.message));for(const [key,section]of Object.entries(shownJob.sections)){if(!workflow||key===workflow)showSection(output,section);}content.append(output);}}
  async function load(){const seq=++generation;const current=ready();const identity=JSON.stringify(current);const data=await api({action:'list'});if(seq!==generation||identity!==JSON.stringify(context()))return;currentJobs=data.jobs;
    if(shownJob){const latest=currentJobs.find(job=>job.id===shownJob.id);shownJob=latest?await api({action:'status',id:latest.id}):null;}
    selectedLabel.textContent=customer()+' · '+current.subscriptions.length+' selected subscription(s)';render();}
  function deploymentPage(){const panel=document.createElement('section');panel.className='panel';panel.append(element('h2','Private Linux VM plan'),element('p','Prepare an ARM template with an existing private subnet. The generated draft lists pricing, quota, policy and pairing checks still required before execution.'));
    const fields={};for(const [key,label]of Object.entries({group:'Resource group',region:'Azure region',names:'VM names, separated by commas',size:'VM size',vnet:'Existing virtual network',subnet:'Existing subnet',image_version:'Pinned Ubuntu 24.04 image version'})){const labelNode=element('label',label);const input=document.createElement('input');labelNode.append(input);panel.append(labelNode);fields[key]=input;}
    const out=document.createElement('div');control(panel,'Prepare plan',async()=>{const c=ready();const plan=Object.fromEntries(Object.entries(fields).map(([key,input])=>[key,input.value.trim()]));plan.names=plan.names.split(',').map(name=>name.trim());const response=await fetch('/api/supervisor',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({action:'live-plan',...c,plan})});const data=await response.json();if(!response.ok)throw new Error(data.error);out.replaceChildren(element('p','Draft prepared. Complete the pending verification before approval.'));table(out,data.vm_names.map(name=>({name,region:data.region,size:data.size,subscription:data.subscription})));data.pending.forEach(text=>out.append(element('p',text)));download(out,'deployment-plan.json',data);download(out,'azuredeploy.json',data.arm_template);});panel.append(out);content.append(panel);
    const paired=element('section','');paired.className='panel';paired.append(element('h2','Laptop execution'),element('p','Azure Portal deployment pairing is awaiting implementation. The installed DMS helper provides file search. Deployment execution requires its own approved-plan and device checks.'));content.append(paired);}
  function navigate(){generation++;clearInterval(timer);const page=pages[location.hash];document.body.classList.toggle('hub-active',!!page);root.hidden=!page;
    if(!page){restoreScope();return;}moveScope();title.textContent=page[0];subtitle.textContent=page[1];document.title=page[0]+' · Cloud Command Center';shownJob=null;currentJobs=[];render();
    load().catch(error=>{status.textContent=error.message;});timer=setInterval(()=>load().catch(error=>{status.textContent=error.message;}),15000);}
  const nav=document.querySelector('nav.workspace-nav');for(const [hash,page]of Object.entries(pages)){const a=element('a',page[0]);a.href=hash;nav?.append(a);}
  scopeBox.addEventListener('change',event=>{if(event.target===document.getElementById('dc-tenant'))document.getElementById('dc-sublist').replaceChildren();shownJob=null;currentJobs=[];selectedLabel.textContent=customer()+' · Select authorized subscriptions.';render();load().catch(error=>{status.textContent=error.message;});});
  window.addEventListener('hashchange',navigate);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',navigate);else navigate();

  const selectionKey='ccc-azure-scope:'+window.CCC_SERVER.user;
  let restoredTenant=false,remembered;
  try{remembered=JSON.parse(localStorage.getItem(selectionKey)||'null');}catch{}
  function rememberScope(){const c=context();if(c.tenant&&c.subscriptions.length){try{localStorage.setItem(selectionKey,JSON.stringify(c));}catch{}}}
  document.getElementById('dc-sublist')?.addEventListener('change',rememberScope);
  const tenantSelect=document.getElementById('dc-tenant'),subList=document.getElementById('dc-sublist');
  if(tenantSelect&&subList){
    const restore=()=>{
      if(!remembered)return;
      if(!restoredTenant&&[...tenantSelect.options].some(option=>option.value===remembered.tenant)){
        restoredTenant=true;tenantSelect.value=remembered.tenant;tenantSelect.dispatchEvent(new Event('change',{bubbles:true}));return;
      }
      if(restoredTenant&&tenantSelect.value===remembered.tenant&&subList.querySelector('.dc-sub')){
        for(const checkbox of subList.querySelectorAll('.dc-sub'))checkbox.checked=remembered.subscriptions.includes(checkbox.value);
        remembered=null;rememberScope();if(pages[location.hash])load().catch(error=>{status.textContent=error.message;});
      }
    };
    new MutationObserver(restore).observe(tenantSelect,{childList:true});new MutationObserver(()=>{restore();if(subList.querySelector('.dc-sub:checked')){rememberScope();if(pages[location.hash])load().catch(error=>{status.textContent=error.message;});}}).observe(subList,{childList:true});restore();
  }

  const homeSummary=document.createElement('section');homeSummary.className='panel';homeSummary.append(element('h2','Latest customer assessments'));
  const homeResults=document.createElement('div');homeSummary.append(homeResults);document.getElementById('work-home')?.append(homeSummary);
  async function updateHome(){if(location.hash&&location.hash!=='#work-home')return;homeResults.replaceChildren();const c=context();if(!c.tenant||!c.subscriptions.length){homeResults.append(element('p','Select a customer in Assessment Hub to see its latest saved runs.'));return;}
    try{const data=await api({action:'list'});homeResults.append(element('p',customer()));for(const [key,label]of Object.entries(labels)){const job=data.jobs.find(item=>item.sections[key]);if(!job)continue;const section=job.sections[key];homeResults.append(element('p',`${label} · ${job.status} · ${new Date(section.captured_at).toLocaleString()}`));}const link=element('a','Open customer assessments');link.href='#hub';homeResults.append(link);}catch(error){homeResults.append(element('p',error.message));}}
  window.addEventListener('hashchange',updateHome);setInterval(updateHome,30000);updateHome();

  const chat=document.createElement('details');
  chat.style.cssText='position:fixed;bottom:20px;right:20px;z-index:10000;background:#111c30;border:1px solid #43617c;border-radius:14px;padding:16px;max-width:460px;width:calc(100vw - 32px);box-shadow:0 12px 40px #0008';
  chat.append(element('summary','Cloud Command Center chat'));
  const chatScope=element('p','');const chatStatus=element('p','');chatStatus.setAttribute('role','status');
  const chatControls=document.createElement('div');const historySelect=document.createElement('select');historySelect.setAttribute('aria-label','Chat history');
  const messages=document.createElement('div');messages.style.cssText='max-height:40vh;overflow:auto;white-space:pre-wrap';
  const question=document.createElement('textarea');question.setAttribute('aria-label','Message to Cloud Command Center');question.maxLength=3000;question.placeholder='Ask about the selected customer or saved assessment';question.style.width='100%';
  const sendControls=document.createElement('div');
  chat.append(chatScope,chatControls,historySelect,messages,question,sendControls,chatStatus);document.body.append(chat);
  let thread=null,threadScope='';
  async function chatApi(body){const c=ready();const response=await fetch('/api/supervisor',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({channel:'assessment-chat',...c,...body})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Chat unavailable.');return data;}
  function renderConversation(conversation){thread=conversation.id;threadScope=JSON.stringify(context());messages.replaceChildren();for(const item of conversation.messages){const bubble=document.createElement('div');bubble.style.cssText='padding:12px 0;border-bottom:1px solid #293950';bubble.append(element('strong',item.role==='user'?'You':'Cloud Command Center'),element('p',item.content));messages.append(bubble);}messages.scrollTop=messages.scrollHeight;}
  async function chatHistory(){const data=await chatApi({action:'list'});historySelect.replaceChildren(new Option('Open a saved conversation',''));for(const item of data.conversations)historySelect.add(new Option(item.title,item.id));}
  async function newChat(){const conversation=await chatApi({action:'new',id:crypto.randomUUID()});renderConversation(conversation);await chatHistory();}
  function chatButton(parent,label,fn){const b=element('button',label);parent.append(b);b.onclick=async()=>{b.disabled=true;chatStatus.textContent='';try{await fn();}catch(error){chatStatus.textContent=error.message;}finally{b.disabled=false;}};}
  chatButton(chatControls,'New Chat',newChat);chatButton(chatControls,'Chat History',chatHistory);
  historySelect.onchange=async()=>{if(!historySelect.value)return;try{renderConversation(await chatApi({action:'open',id:historySelect.value}));}catch(error){chatStatus.textContent=error.message;}};
  chatButton(sendControls,'Send to Azure model',async()=>{if(!question.value.trim())return;if(!thread||threadScope!==JSON.stringify(context()))await newChat();chatStatus.textContent='Consulting Azure Foundry…';const conversation=await chatApi({action:'message',id:thread,message:question.value,page:location.hash,consent:true});question.value='';renderConversation(conversation);chatStatus.textContent='Conversation saved.';await chatHistory();});
  chat.addEventListener('toggle',()=>{if(chat.open){chatScope.textContent=customer()+' · '+context().subscriptions.length+' selected subscription(s). Azure model usage applies.';if(threadScope!==JSON.stringify(context())){thread=null;messages.replaceChildren();historySelect.replaceChildren();}}});
})();
