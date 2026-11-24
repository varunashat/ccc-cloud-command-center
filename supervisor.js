(() => {
  const panel = document.createElement('section');
  panel.className = 'panel';
  panel.innerHTML = '<h2>Supervisor jobs</h2><p>Run a sample assessment, prepare a private VM draft, or do both. Each job uses synthetic data.</p>';
  const controls = document.createElement('div');
  const history = document.createElement('div');
  const message = document.createElement('p');
  message.setAttribute('role', 'status');
  panel.append(controls, message, history);
  function button(parent, label, fn) {
    const element = document.createElement('button');
    element.textContent = label;
    element.onclick = async () => {
      element.disabled = true;
      try { await fn(); } catch (err) { message.textContent = err.message; }
      finally { element.disabled = false; }
    };
    parent.append(element);
  }
  async function api(body) {
    const response = await fetch('/api/supervisor', {method:'POST', headers:{
      'Content-Type':'application/json', 'X-Local-Token':token
    },body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Job unavailable.');
    return data;
  }
  async function refresh() {
    const {jobs} = await api({action:'list'});
    history.replaceChildren();
    for (const job of jobs) {
      const row = document.createElement('p');
      row.textContent = ({one:'Prompt One',two:'Prompt Two',both:'Both prompts'})[job.mode] + ' Â· ' + job.status + ' ';
      history.append(row);
      for (const [step, result] of Object.entries(job.results)) {
        button(row, 'Download ' + step, () => {
          const csv = step === 'assessment';
          const link = document.createElement('a');
          const url = URL.createObjectURL(new Blob([csv ? result.inventory_csv : JSON.stringify(result,null,2)]));
          link.href=url; link.download=step+(csv?'.csv':'.json'); link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        });
      }
      const actions = job.status === 'Paused' ? ['resume','cancel'] :
        ['Queued','In progress'].includes(job.status) ? ['pause','cancel'] : [];
      for (const action of actions) button(row,action,async () => { await api({action,id:job.id}); await refresh(); });
    }
    message.textContent = jobs.length ? '' : 'No saved jobs yet.';
  }
  for (const [mode,label] of [['one','Start Prompt One'],['two','Start Prompt Two'],['both','Start both prompts']]) {
    button(controls,label,async () => {await api({action:'start',mode,id:crypto.randomUUID()}); await refresh();});
  }
  const live = document.createElement('section');
  const heading = document.createElement('h2');
  heading.textContent = 'Selected Azure subscription';
  const scopeLabel = document.createElement('p');
  const output = document.createElement('div');
  live.append(heading,scopeLabel);
  const currentScope = () => ({tenant:document.getElementById('dc-tenant')?.value,subscriptions:dcSubs()});
  const showScope = () => {
    const selection=currentScope();
    scopeLabel.textContent = selection.subscriptions.length ? 'Subscriptions: '+selection.subscriptions.join(', ') :
      'Open Azure Assessment, load your directory and select a subscription. Then return here.';
  };
  live.addEventListener('pointerenter',showScope);
  function resultLink(name, content, mime) {
    button(output,'Download '+name,()=>{
      const link=document.createElement('a');
      const url=URL.createObjectURL(new Blob([content],{type:mime}));
      link.href=url; link.download=name; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
  }
  async function liveRun(action,plan) {
    output.replaceChildren();
    const scope=currentScope();
    if (!scope.tenant || !scope.subscriptions.length) throw new Error('Select a tenant and subscription in Azure Assessment first.');
    const result=await api({action,...scope,plan});
    if (action==='live-assessment') {
      output.textContent=result.resources.length+' visible resources at '+result.captured_at+' ';
      resultLink('inventory.csv',result.inventory_csv,'text/csv');
    } else {
      output.textContent='Draft prepared for '+result.vm_names.join(', ')+'. Pricing and deployment preflight are pending. ';
      resultLink('deployment-plan.json',JSON.stringify(result,null,2),'application/json');
    }
  }
  button(live,'Assess live inventory',()=>liveRun('live-assessment'));
  const details=document.createElement('details');
  const summary=document.createElement('summary'); summary.textContent='Plan private Linux VMs'; details.append(summary);
  const fields={};
  for (const [key,label] of Object.entries({group:'Resource group',region:'Azure region',names:'VM names (one or two, comma-separated)',size:'VM size',vnet:'Existing virtual network',subnet:'Existing subnet',image_version:'Ubuntu 24.04 image version'})) {
    const wrapper=document.createElement('label');wrapper.textContent=label;
    const input=document.createElement('input');wrapper.append(input);details.append(wrapper);fields[key]=input;
  }
  button(details,'Prepare deployment draft',()=>{
    const plan=Object.fromEntries(Object.entries(fields).map(([key,input])=>[key,input.value.trim()]));
    plan.names=plan.names.split(',').map(name=>name.trim());
    return liveRun('live-plan',plan);
  });
  live.append(details,output);panel.append(live);


  const weather = document.createElement('section');
  const weatherTitle = document.createElement('h2');
  weatherTitle.textContent = 'Weather forecast agent';
  const notice = document.createElement('p');
  notice.textContent = 'Uses GPT-6.1 Sol in Azure Foundry and public Open-Meteo forecasts. Each request incurs Azure model usage.';
  const prompt = document.createElement('input');
  prompt.setAttribute('aria-label','Weather forecast question');
  prompt.maxLength=800; prompt.placeholder='Tomorrow’s weather in Sharjah';
  const answer = document.createElement('div');
  answer.setAttribute('role','status');
  weather.append(weatherTitle,notice,prompt,answer);
  button(weather,'Send to Azure model',async () => {
    answer.textContent='Getting forecast…';
    const result=await api({action:'weather',prompt:prompt.value,consent:true});
    answer.textContent=result.answer;
    for (const source of result.sources) {
      const link=document.createElement('a');link.href=source;link.textContent='Open forecast source';
      link.target='_blank';link.rel='noopener';answer.append(document.createElement('br'),link);
    }
  });
  panel.append(weather);
  let timer;
  function mount() {
    clearInterval(timer);
    panel.remove();
    if (location.hash !== '#work-jobs') return;
    (document.querySelector('main') || document.body).prepend(panel);
    const update = () => refresh().catch(err => {message.textContent=err.message;});
    showScope(); update(); timer=setInterval(update,10000);
  }
  window.addEventListener('hashchange',mount); mount();
})();
