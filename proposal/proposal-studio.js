if (window.CCC_SERVER && document.getElementById('work-proposal')) {
  const view = document.getElementById('work-proposal');
  const routes = [
    {id: 'rfp', title: 'RFP and attachments', upload: 'Choose RFP, BOQ, images and supporting documents'},
    {id: 'email', title: 'Email, BOQ and supporting documents', upload: 'Choose BOQ and supporting documents'},
    {id: 'gdc', title: 'GDC proposal conversion', upload: 'Choose supporting documents (optional)'}
  ];
  const formats = '.pdf,.docx,.xlsx,.xlsm,.csv,.txt,.png,.jpg,.jpeg,.webp';
  const primaryFormats = '.pdf,.docx,.txt,.png,.jpg,.jpeg,.webp';
  const picker = el('select');
  const field = (caption, node) => {
    const label = el('label', caption);
    label.append(node);
    return label;
  };
  const pickerPanel = el('section', '', 'panel section');
  pickerPanel.append(field('Proposal source', picker));
  view.append(pickerPanel);
  const encode = (file, role) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read ' + file.name));
    reader.onload = () => resolve({name: file.name, role, data: String(reader.result).split(',', 2)[1]});
    reader.readAsDataURL(file);
  });
  const panels = new Map();

  for (const route of routes) {
    picker.add(new Option(route.title, route.id));
    const container = el('div', '', 'section');
    container.hidden = route.id !== 'rfp';
    panels.set(route.id, container);
    const form = el('form', '', 'proposal-form');
    const inputs = el('section', '', 'panel');
    const exportPanel = el('section', '', 'panel proposal-export');
    const project = el('input');
    project.required = true;
    project.maxLength = 140;
    project.placeholder = 'e.g. Azure Landing Zone and Migration';
    const client = el('input');
    client.required = true;
    client.maxLength = 140;
    const selectedCustomer = document.getElementById('customer')?.selectedOptions[0];
    if (selectedCustomer?.value !== 'unassigned') client.value = selectedCustomer?.textContent || '';
    const solution = el('select');
    for (const option of [route.id === 'rfp' ? 'Infer from RFP' : 'Infer from sources', 'Azure', 'Modern Workplace',
      'Microsoft licensing', 'Security', 'Data and AI', 'Hybrid']) {
      solution.add(new Option(option));
    }
    const timeline = el('input');
    timeline.placeholder = 'Optional if stated in the attachments';
    timeline.maxLength = 240;
    const notes = el('textarea');
    notes.maxLength = 5000;
    notes.placeholder = 'Add known objectives, quantities, constraints or dates.';
    inputs.append(el('h2', 'Project details'), field('Project name', project), field('Customer name', client),
      field('Solution area', solution), field('Target timeline', timeline), field('Project brief', notes),
      el('h2', route.title));

    const email = el('textarea');
    email.maxLength = 20000;
    email.rows = 7;
    email.placeholder = 'Paste the customer email or relevant thread here.';
    if (route.id === 'email') {
      inputs.append(field('Customer email (optional if attaching documents)', email));
    }
    const primary = el('input');
    primary.type = 'file';
    primary.required = route.id === 'gdc';
    primary.accept = primaryFormats;
    if (route.id === 'gdc') inputs.append(field('Choose the GDC proposal (PDF, DOCX, TXT or image)', primary));
    const attachments = el('input');
    attachments.type = 'file';
    attachments.multiple = true;
    attachments.required = route.id === 'rfp';
    attachments.accept = formats;
    const files = el('ul', '', 'proposal-files');
    const refreshFiles = () => {
      files.replaceChildren(...[...primary.files, ...attachments.files].map(file => el('li', file.name)));
    };
    primary.onchange = refreshFiles;
    attachments.onchange = refreshFiles;
    inputs.append(field(route.upload, attachments),
      el('p', 'Up to 8 files and 12 MB total. For scanned PDFs, attach the relevant pages as images.', 'muted'), files);

    const control = el('input');
    control.type = 'checkbox';
    const controlLabel = el('label');
    controlLabel.append(control, ' Fill the document control table');
    const consent = el('input');
    consent.type = 'checkbox';
    consent.required = true;
    const consentLabel = el('label');
    consentLabel.append(consent, ' Send these inputs to the configured Azure Foundry model.');
    const submit = el('button', route.id === 'gdc' ? 'Convert to Word proposal' : 'Generate Word proposal');
    submit.type = 'submit';
    const output = el('p', '', 'muted');
    output.setAttribute('role', 'status');
    exportPanel.append(el('h2', 'Seven Seas proposal'),
      el('p', 'Editable Word draft using the corporate SOW template. Review missing inputs and commitments before sending.', 'muted'),
      controlLabel, consentLabel, submit, output);
    form.append(inputs, exportPanel);
    container.append(form);
    view.append(container);

    form.onsubmit = async event => {
      event.preventDefault();
      const selected = [...primary.files, ...attachments.files];
      const emailText = email.value.trim();
      if (route.id === 'email' && !emailText && !selected.length) {
        output.textContent = 'Paste the email or attach a BOQ or supporting document.';
        return;
      }
      if (selected.length > 8 || selected.reduce((bytes, file) => bytes + file.size, 0) > 12000000) {
        output.textContent = 'Select up to 8 attachments totaling 12 MB.';
        return;
      }
      const projectName = project.value.trim();
      submit.disabled = true;
      output.textContent = 'Reading sources and preparing the Word draft.';
      try {
        const response = await fetch('/api/proposal-studio', {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-Local-Token': token},
          body: JSON.stringify({
            route: route.id, project: projectName, customer: client.value, solution: solution.value,
            timeline: timeline.value, notes: notes.value, email: emailText, control: control.checked,
            consent: consent.checked, files: await Promise.all(selected.map((file, index) =>
              encode(file, route.id === 'gdc' && index === 0 ? 'gdc' : 'attachment')))
          })
        });
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.error || 'Proposal generation failed. Try again.');
        }
        const url = URL.createObjectURL(await response.blob());
        const download = el('a', 'Download Word proposal');
        download.href = url;
        download.download = projectName.replace(/[<>:"/\\|?*]/g, '_') + ' - Seven Seas Proposal.docx';
        output.replaceChildren('Draft ready. ', download);
        download.addEventListener('click', () => setTimeout(() => URL.revokeObjectURL(url), 120000), {once: true});
      } catch (error) {
        output.textContent = error.message;
      } finally {
        submit.disabled = false;
      }
    };
  }
  // Noventiq PPT builder: templates + source decks library, brief -> deck on the Noventiq template
  const pptPanel = el('div', '', 'section');
  pptPanel.hidden = true;
  const pptApi = (body, raw) => fetch('/api/ppt-studio', {method: 'POST', headers: Object.assign({'X-Local-Token': token},
    raw ? {'Content-Type': 'application/octet-stream', 'X-Ppt-Kind': raw.kind, 'X-File-Name': encodeURIComponent(raw.file.name)}
        : {'Content-Type': 'application/json'}), body: raw ? raw.file : JSON.stringify(body)});
  const pptJson = async response => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Request failed (' + response.status + ').');
    return data;
  };
  const pptForm = el('form', '', 'proposal-form');
  const pptMain = el('section', '', 'panel');
  const pptSide = el('section', '', 'panel proposal-export');
  const pptTitle = el('input');
  pptTitle.maxLength = 120;
  pptTitle.placeholder = 'e.g. Azure Migration Proposal for Acme';
  const pptTemplate = el('select');
  const pptBrief = el('textarea');
  pptBrief.rows = 9;
  pptBrief.maxLength = 6000;
  pptBrief.placeholder = 'Describe the deck: customer, audience, goal, services to cover, key messages, any figures to include. ' +
    'Noventiq facts (offices, certifications, service lines) are taken from the selected source decks.';
  const pptCount = el('input');
  pptCount.type = 'number';
  pptCount.min = 3;
  pptCount.max = 40;
  pptCount.placeholder = 'Auto';
  const pptSources = el('div', '', 'proposal-files');
  const pptConsent = el('input');
  pptConsent.type = 'checkbox';
  const pptConsentLabel = el('label');
  pptConsentLabel.append(pptConsent, ' Send the brief and selected source text to the configured Azure Foundry model.');
  const pptBuild = el('button', 'Build Noventiq deck');
  pptBuild.type = 'submit';
  const pptOut = el('p', '', 'muted');
  pptOut.setAttribute('role', 'status');
  pptMain.append(el('h2', 'Build a Noventiq deck'), field('Deck title', pptTitle), field('Template', pptTemplate),
    field('Brief', pptBrief), field('Number of slides (optional)', pptCount), el('h2', 'Noventiq source decks'),
    el('p', 'Tick the decks the facts should come from.', 'muted'), pptSources);
  pptSide.append(el('h2', 'Output'),
    el('p', 'An editable .pptx built on the real layouts of the chosen Noventiq template. Check figures and placeholders in [brackets] before sending.', 'muted'),
    pptConsentLabel, pptBuild, pptOut);
  pptForm.append(pptMain, pptSide);

  const libPanel = el('section', '', 'panel');
  const libList = el('ul', '', 'proposal-files');
  const libKind = el('select');
  for (const [value, label] of [['template', 'Template (.potx / .pptx)'], ['source', 'Noventiq source deck'], ['guideline', 'Brand guideline']]) {
    libKind.add(new Option(label, value));
  }
  const libFile = el('input');
  libFile.type = 'file';
  libFile.accept = '.pptx,.potx';
  const libAdd = el('button', 'Add to library');
  libAdd.type = 'button';
  const libOut = el('p', '', 'muted');
  libOut.setAttribute('role', 'status');
  libPanel.append(el('h2', 'Noventiq library'),
    el('p', 'Templates supply the slide layouts. Source decks are the factual source. Guidelines are passed to the model as brand rules.', 'muted'),
    libList, el('h2', 'Add a file'), field('Type', libKind), field('PowerPoint file (up to 60 MB)', libFile), libAdd, libOut);
  pptPanel.append(pptForm, libPanel);

  const kindNames = {template: 'Template', source: 'Source deck', guideline: 'Guideline'};
  const showLibrary = items => {
    const keepTemplate = pptTemplate.value;
    const ticked = new Set([...pptSources.querySelectorAll('input:checked')].map(box => box.value));
    pptTemplate.replaceChildren(...items.filter(i => i.kind === 'template').map(i => new Option(i.name, i.id)));
    if (keepTemplate) pptTemplate.value = keepTemplate;
    pptSources.replaceChildren(...items.filter(i => i.kind === 'source').map(i => {
      const box = el('input');
      box.type = 'checkbox';
      box.value = i.id;
      box.checked = ticked.size ? ticked.has(i.id) : true;
      const row = el('label');
      row.append(box, ' ' + i.name);
      return row;
    }));
    libList.replaceChildren(...items.map(i => {
      const row = el('li', kindNames[i.kind] + ': ' + i.name + (i.layouts ? ' - ' + i.layouts.length + ' layouts' : ''));
      if (!i.builtin) {
        const remove = el('button', 'Remove');
        remove.type = 'button';
        remove.onclick = async () => {
          try { showLibrary((await pptJson(await pptApi({action: 'remove', id: i.id}))).library); }
          catch (error) { libOut.textContent = error.message; }
        };
        row.append(' ', remove);
      }
      return row;
    }));
  };
  const loadLibrary = async () => {
    try { showLibrary((await pptJson(await pptApi({action: 'list'}))).library); }
    catch (error) { libOut.textContent = error.message; }
  };
  libAdd.onclick = async () => {
    const file = libFile.files[0];
    if (!file) { libOut.textContent = 'Choose a .pptx or .potx file first.'; return; }
    libAdd.disabled = true;
    libOut.textContent = 'Uploading and reading ' + file.name + '.';
    try {
      showLibrary((await pptJson(await pptApi(null, {kind: libKind.value, file}))).library);
      libFile.value = '';
      libOut.textContent = 'Added ' + file.name + '.';
    } catch (error) {
      libOut.textContent = error.message;
    } finally {
      libAdd.disabled = false;
    }
  };
  pptForm.onsubmit = async event => {
    event.preventDefault();
    const name = pptTitle.value.trim() || 'Noventiq deck';
    pptBuild.disabled = true;
    pptOut.textContent = 'Planning the slides and building the deck. This can take a minute.';
    try {
      const response = await pptApi({action: 'generate', title: pptTitle.value, brief: pptBrief.value, template: pptTemplate.value,
        slides: pptCount.value, consent: pptConsent.checked,
        sources: [...pptSources.querySelectorAll('input:checked')].map(box => box.value)});
      if (!response.ok) await pptJson(response);
      const url = URL.createObjectURL(await response.blob());
      const download = el('a', 'Download Noventiq deck');
      download.href = url;
      download.download = name.replace(/[<>:"/\\|?*]/g, '_') + ' - Noventiq.pptx';
      pptOut.replaceChildren((response.headers.get('X-Slides') || '') + ' slides ready. ', download);
      download.addEventListener('click', () => setTimeout(() => URL.revokeObjectURL(url), 120000), {once: true});
    } catch (error) {
      pptOut.textContent = error.message;
    } finally {
      pptBuild.disabled = false;
    }
  };
  picker.add(new Option('Noventiq PPT builder', 'ppt'));
  panels.set('ppt', pptPanel);
  view.append(pptPanel);
  loadLibrary();
  picker.onchange = () => {
    for (const [id, panel] of panels) panel.hidden = id !== picker.value;
  };
}
