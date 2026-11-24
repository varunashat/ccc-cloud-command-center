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
  picker.onchange = () => {
    for (const [id, panel] of panels) panel.hidden = id !== picker.value;
  };
}
