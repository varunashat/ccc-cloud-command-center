(() => {
  const pages = {
    "#hub": [
      "Assessment Hub",
      "Run assessments for the selected customer and reopen saved results.",
    ],
    "#hub-infrastructure": [
      "Infrastructure Assessment",
      "Azure resources, capacity evidence and dependency gaps.",
    ],
    "#hub-security": [
      "Security Assessment",
      "Defender assessments and configured network controls.",
    ],
    "#hub-cost": [
      "Cost Optimization",
      "Actual cost for the last 30 days and Azure Advisor evidence.",
    ],
    "#hub-modernization": [
      "Application Modernization",
      "VM platform evidence and application discovery requirements.",
    ],
    "#hub-deployment": [
      "Environment & Deployment",
      "Describe what to deploy in chat. AI Foundry plans it, and can operate your laptop browser.",
    ],
  };
  const labels = {
    infrastructure: "Infrastructure",
    security: "Security",
    cost: "Cost optimization",
    modernization: "Modernization",
  };
  const style = document.createElement("style");
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
  const root = document.createElement("div");
  root.id = "assessment-workspace";
  root.hidden = true;
  const title = document.createElement("h1");
  const subtitle = document.createElement("p");
  const scopeBox = document.createElement("section");
  scopeBox.className = "panel";
  scopeBox.append(element("h2", "Customer and subscription"));
  const selectedLabel = element(
    "p",
    "Load the customer directory to select authorized subscriptions.",
  );
  const content = document.createElement("div");
  const status = element("p", "");
  status.setAttribute("role", "status");
  root.append(title, subtitle, scopeBox, selectedLabel, status, content);
  document.querySelector("main").append(root);
  let moved = [],
    timer,
    generation = 0,
    currentJobs = [],
    shownJob;
  function element(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  }
  function control(parent, label, action) {
    const button = element("button", label);
    button.type = "button";
    parent.append(button);
    button.onclick = async () => {
      button.disabled = true;
      status.textContent = "";
      try {
        await action();
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
    return button;
  }
  function context() {
    return {
      tenant: document.getElementById("dc-tenant")?.value,
      subscriptions: dcSubs().sort(),
    };
  }
  function customer() {
    return (
      document
        .getElementById("dc-tenant")
        ?.selectedOptions[0]?.textContent.split(" · ")[0] || "Customer"
    );
  }
  function ready() {
    const c = context();
    if (!c.tenant || !c.subscriptions.length)
      throw new Error(
        "Load a customer directory and select subscriptions first.",
      );
    return c;
  }
  const signInBox = document.createElement("div");
  scopeBox.append(signInBox);
  control(signInBox, "Refresh selected directory sign-in", async () => {
    const c = context();
    if (!c.tenant)
      throw new Error("Load directories and select the customer first.");
    const response = await fetch("/api/supervisor", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Local-Token": token },
      body: JSON.stringify({
        action: "refresh-tenant-signin",
        tenant: c.tenant,
      }),
    });
    const flow = await response.json();
    if (!response.ok) throw new Error(flow.error || "Could not start sign-in.");
    const instructions = document.createElement("p");
    instructions.append(element("span", "Directory " + c.tenant + " · "));
    const link = element("a", "Open Microsoft sign-in");
    link.href = flow.url;
    link.target = "_blank";
    link.rel = "noopener";
    instructions.append(
      link,
      element("span", " and enter this code: "),
      element("strong", flow.code),
    );
    signInBox.append(instructions);
    const poll = async () => {
      try {
        const r = await fetch("/auth/poll", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fid: flow.fid }),
        });
        const state = await r.json();
        if (state.status === "done") {
          instructions.textContent =
            "Microsoft sign-in refreshed. Reloading subscriptions.";
          document
            .getElementById("dc-tenant")
            .dispatchEvent(new Event("change", { bubbles: true }));
          return;
        }
        if (state.status === "pending") {
          setTimeout(poll, 3000);
          return;
        }
        instructions.textContent =
          state.message || state.error || "Sign-in did not complete.";
      } catch (error) {
        instructions.textContent = error.message;
      }
    };
    setTimeout(poll, 3000);
  });
  async function api(body) {
    const response = await fetch("/api/supervisor", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Local-Token": token },
      body: JSON.stringify({
        channel: "assessment-hub",
        ...context(),
        ...body,
      }),
    });
    const data = await response.json();
    if (!response.ok)
      throw new Error(data.error || "Assessment request failed.");
    return data;
  }
  function download(parent, name, data, type = "application/json") {
    control(parent, "Download " + name, () => {
      const a = document.createElement("a");
      const url = URL.createObjectURL(
        new Blob(
          [typeof data === "string" ? data : JSON.stringify(data, null, 2)],
          { type },
        ),
      );
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  function moveScope() {
    if (moved.length) return;
    const nodes = [
      document.getElementById("dc-tenants")?.closest(".row"),
      document.getElementById("dc-tenant")?.closest(".row"),
      document.getElementById("dc-sublist"),
    ];
    for (const node of nodes) {
      if (!node) continue;
      const mark = document.createComment("shared customer scope");
      node.before(mark);
      scopeBox.append(node);
      moved.push([node, mark]);
    }
  }
  function restoreScope() {
    for (const [node, mark] of moved) {
      mark.replaceWith(node);
    }
    moved = [];
  }
  async function start(workflows) {
    const c = ready();
    const job = await api({
      action: "start",
      workflows,
      id: crypto.randomUUID(),
      customer: customer(),
    });
    shownJob = job;
    status.textContent =
      "Assessment queued. The cloud worker checks every five minutes.";
    await load();
  }
  function table(parent, rows) {
    if (!rows.length) {
      parent.append(element("p", "No matching rows were returned."));
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "hub-table";
    const t = document.createElement("table");
    const fields = [...new Set(rows.flatMap((row) => Object.keys(row)))];
    const header = document.createElement("tr");
    fields.forEach((field) => header.append(element("th", field)));
    t.append(header);
    for (const row of rows.slice(0, 200)) {
      const tr = document.createElement("tr");
      for (const field of fields) {
        const value = row[field];
        tr.append(
          element(
            "td",
            value && typeof value === "object"
              ? JSON.stringify(value)
              : String(value ?? ""),
          ),
        );
      }
      t.append(tr);
    }
    wrap.append(t);
    parent.append(wrap);
    if (rows.length > 200)
      parent.append(
        element(
          "p",
          `Showing 200 of ${rows.length} rows. Download the complete result for every row.`,
        ),
      );
  }
  function showSection(parent, section) {
    parent.append(
      element("h3", labels[section.workflow] || section.workflow),
      element(
        "p",
        `${section.status} · Captured ${new Date(section.captured_at).toLocaleString()}`,
      ),
    );
    if (section.estimated_savings == null)
      parent.append(
        element(
          "p",
          "Estimated savings await validated cost and utilization evidence.",
        ),
      );
    for (const gap of section.gaps || []) parent.append(element("p", gap));
    for (const finding of section.findings || []) {
      const details = document.createElement("details");
      details.append(element("summary", finding.title));
      const pre = element("pre", JSON.stringify(finding.evidence, null, 2));
      details.append(pre);
      parent.append(details);
    }
    table(parent, section.rows);
    pdfControls(parent, section.workflow);
  }
  function pdfControls(parent, workflow) {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    label.append(
      box,
      document.createTextNode(
        " Validate and write findings with my Azure AI Foundry model (billable; sends computed summary data, no credentials)",
      ),
    );
    wrap.append(label);
    control(
      wrap,
      workflow ? "Download PDF report" : "Download full PDF report",
      async () => {
        if (!shownJob) throw new Error("Open a saved run first.");
        status.textContent = "Building report. AI validation can take a minute...";
        const out = await api({
          action: "report",
          id: shownJob.id,
          workflow,
          validate: box.checked,
          consent: box.checked,
        });
        const bytes = Uint8Array.from(atob(out.pdf_base64), (c) => c.charCodeAt(0));
        const a = document.createElement("a");
        const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
        a.href = url;
        a.download = out.filename;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        status.textContent = "Report downloaded.";
      },
    );
    parent.append(wrap);
  }
  function renderJobs(parent, workflow) {
    const relevant = currentJobs.filter(
      (job) => !workflow || job.workflows.includes(workflow),
    );
    if (!relevant.length) {
      parent.append(
        element("p", "No saved runs in this customer and subscription scope."),
      );
      return;
    }
    relevant.forEach((job) => {
      const row = document.createElement("div");
      row.className = "hub-run";
      row.append(
        element("strong", `${job.customer} · ${job.status}`),
        element("p", new Date(job.created).toLocaleString()),
      );
      control(row, "Open saved run", async () => {
        shownJob = await api({ action: "status", id: job.id });
        render();
      });
      const actions =
        job.status === "Paused" || job.status === "Needs sign-in"
          ? ["resume", "cancel"]
          : ["Queued", "In progress"].includes(job.status)
            ? ["pause", "cancel"]
            : [];
      for (const action of actions)
        control(row, action[0].toUpperCase() + action.slice(1), async () => {
          await api({ action, id: job.id });
          await load();
        });
      parent.append(row);
    });
  }
  let overviewData = null;
  async function loadOverview() {
    overviewData = await api({ action: "overview" });
    if (location.hash === "#hub") render();
  }
  function dashboard(parent) {
    const panel = document.createElement("section");
    panel.className = "panel";
    panel.append(element("h2", "Customer assessment dashboard"));
    if (!overviewData) {
      panel.append(element("p", "Loading saved assessments..."));
      parent.append(panel);
      return;
    }
    const t = overviewData.totals;
    const tiles = document.createElement("div");
    tiles.className = "hub-cards";
    for (const [name, value] of [
      ["Customers assessed", t.customers],
      ["Assessment runs", t.runs],
      ["Completed", t.completed],
      ["Open / needs action", t.open],
    ]) {
      const tile = document.createElement("div");
      tile.append(element("small", name), element("h2", String(value)));
      tiles.append(tile);
    }
    panel.append(tiles);
    if (!overviewData.customers.length) {
      panel.append(
        element("p", "No assessments yet. Select a customer above and run an assessment; each customer you assess is added here."),
      );
    } else {
      const rows = overviewData.customers.map((c) => ({
        Customer: c.customer,
        Runs: c.runs,
        "Last run": new Date(c.latest).toLocaleString(),
        "Last status": c.latest_status,
        Infrastructure: c.done.infrastructure ?? "-",
        Security: c.done.security ?? "-",
        Cost: c.done.cost ?? "-",
        Modernization: c.done.modernization ?? "-",
      }));
      table(panel, rows);
      panel.append(element("small", "Assessment columns show records collected in the customer's latest saved result. - means not run yet."));
    }
    parent.append(panel);
  }
  function render() {
    content.replaceChildren();
    const hash = location.hash;
    const workflow = Object.keys(labels).find((key) => hash === "#hub-" + key);
    if (hash === "#hub-deployment") {
      deploymentPage();
      return;
    }
    if (workflow) {
      control(content, "Run " + labels[workflow] + " assessment", () =>
        start([workflow]),
      );
    } else {
      dashboard(content);
      const cards = document.createElement("div");
      cards.className = "hub-cards";
      for (const [key, label] of Object.entries(labels)) {
        const card = document.createElement("section");
        card.className = "panel";
        card.append(element("h2", label));
        const a = element("a", "Open assessment");
        a.href = "#hub-" + key;
        card.append(a);
        const latest = currentJobs.find((job) => job.sections[key]);
        card.append(
          element(
            "p",
            latest
              ? `${latest.status} · ${new Date(latest.created).toLocaleString()}`
              : "No saved assessment",
          ),
        );
        cards.append(card);
      }
      content.append(cards);
      control(content, "Run all assessments", () => start(Object.keys(labels)));
    }
    const history = document.createElement("section");
    history.className = "panel";
    history.append(element("h2", "Customer run history"));
    renderJobs(history, workflow);
    content.append(history);
    if (
      shownJob &&
      JSON.stringify(shownJob.scope) === JSON.stringify(context())
    ) {
      const output = document.createElement("section");
      output.className = "panel";
      output.append(
        element("h2", "Saved run results"),
        element("p", shownJob.status),
      );
      if (shownJob.pending)
        output.append(element("p", shownJob.pending.message));
      if (!workflow && Object.keys(shownJob.sections).length > 1)
        pdfControls(output, undefined);
      for (const [key, section] of Object.entries(shownJob.sections)) {
        if (!workflow || key === workflow) showSection(output, section);
      }
      content.append(output);
    }
  }
  async function load() {
    const seq = ++generation;
    const current = ready();
    const identity = JSON.stringify(current);
    const data = await api({ action: "list" });
    if (seq !== generation || identity !== JSON.stringify(context())) return;
    currentJobs = data.jobs;
    if (shownJob) {
      const latest = currentJobs.find((job) => job.id === shownJob.id);
      shownJob = latest ? await api({ action: "status", id: latest.id }) : null;
    }
    selectedLabel.textContent =
      customer() +
      " · " +
      current.subscriptions.length +
      " selected subscription(s)";
    status.textContent = "";
    render();
  }
  async function deployApi(body) {
    const c = context();
    const scope = c.tenant && c.subscriptions.length ? c : {};
    const response = await fetch("/api/supervisor", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Local-Token": token },
      body: JSON.stringify({ channel: "deploy-chat", ...scope, ...body }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Deployment request failed.");
    return data;
  }
  function deploymentPage() {
    const panel = document.createElement("section");
    panel.className = "panel";
    panel.append(
      element("h2", "Deploy with AI Foundry"),
      element(
        "p",
        "Describe what to deploy in your own words: virtual machines, containers, App Service, Azure Firewall, networking, storage or a whole environment. Azure AI Foundry plans it with you. Nothing runs until you approve.",
      ),
    );
    const modeRow = document.createElement("div");
    const modes = {};
    for (const [value, text] of [
      ["cloud", "Plan in the cloud (AI Foundry drafts resources, CLI steps and checks)"],
      ["laptop", "Control my laptop browser (AI Foundry writes portal steps; the laptop agent performs them in my Edge/Chrome)"],
    ]) {
      const label = document.createElement("label");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "deploy-mode";
      radio.value = value;
      radio.checked = value === "cloud";
      label.append(radio, document.createTextNode(" " + text));
      modeRow.append(label);
      modes[value] = radio;
    }
    panel.append(modeRow);
    const history = document.createElement("select");
    history.setAttribute("aria-label", "Deployment chat history");
    const log = document.createElement("div");
    const planBox = document.createElement("div");
    const input = document.createElement("textarea");
    input.rows = 4;
    input.style.width = "100%";
    input.setAttribute("aria-label", "What should be deployed");
    input.placeholder =
      "e.g. Create a hub VNet in UAE North with Azure Firewall Premium, two Windows Server 2022 VMs in a spoke, and a Log Analytics workspace. Open my Azure portal and set it up.";
    const consent = document.createElement("input");
    consent.type = "checkbox";
    const consentLabel = document.createElement("label");
    consentLabel.append(
      consent,
      document.createTextNode(" Use my Azure AI Foundry model (billable; your messages and the selected customer scope are sent to your Azure resource, never credentials)"),
    );
    let current = null;
    const chatTitle = (c) => c.title + " · " + c.mode;
    function paint(conv) {
      current = conv;
      log.replaceChildren();
      for (const m of conv.messages) {
        const who = m.role === "user" ? "You" : "AI Foundry";
        let text = m.content;
        if (m.role === "assistant") {
          try {
            const p = JSON.parse(text.match(/\{[\s\S]*\}/)[0]);
            text = [p.reply, ...(p.questions || []).map((q) => "? " + q)].filter(Boolean).join("\n");
          } catch (_) {}
        }
        const row = element("p", "");
        row.append(element("strong", who + ": "), document.createTextNode(text));
        row.style.whiteSpace = "pre-wrap";
        log.append(row);
      }
      planBox.replaceChildren();
      const plan = conv.plan;
      if (!plan) return;
      planBox.append(element("h3", "Plan: " + plan.title), element("p", plan.summary));
      if (plan.resources.length) table(planBox, plan.resources);
      for (const [key, title] of [
        ["checks_before_run", "Checks before running"],
        ["cli_steps", "Azure CLI steps"],
        ["portal_steps", "Azure portal steps (laptop agent)"],
        ["estimated_risks", "Risks"],
      ]) {
        if (!plan[key].length) continue;
        planBox.append(element("h3", title));
        const list = document.createElement("ol");
        plan[key].forEach((x) => list.append(element("li", x)));
        planBox.append(list);
      }
      if (conv.mode === "laptop" && plan.portal_steps.length) {
        control(planBox, "Download laptop task", async () => {
          const task = await deployApi({ action: "laptop-task", id: conv.id });
          const a = document.createElement("a");
          const url = URL.createObjectURL(new Blob([JSON.stringify(task, null, 2)], { type: "application/json" }));
          a.href = url;
          a.download = "ccc-deploy-task.json";
          a.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        });
        planBox.append(
          element(
            "p",
            "Run on your laptop: python laptop/portal_agent.py ccc-deploy-task.json. It opens your Edge, you sign in to the Azure portal yourself, and AI Foundry operates the portal. It asks you before every Create, Delete or Save.",
          ),
        );
      }
    }
    async function refreshHistory() {
      const out = await deployApi({ action: "list" });
      history.replaceChildren(new Option("New chat", ""));
      out.conversations.forEach((c) => history.add(new Option(chatTitle(c), c.id)));
      if (current) history.value = current.id;
    }
    history.onchange = async () => {
      if (!history.value) {
        current = null;
        log.replaceChildren();
        planBox.replaceChildren();
        return;
      }
      const conv = await deployApi({ action: "open", id: history.value });
      modes[conv.mode].checked = true;
      paint(conv);
    };
    control(panel, "Send", async () => {
      const text = input.value.trim();
      if (!text) throw new Error("Describe what to deploy.");
      if (!consent.checked) throw new Error("Tick the Azure AI Foundry consent box first.");
      const mode = modes.laptop.checked ? "laptop" : "cloud";
      if (!current) {
        const id = crypto.randomUUID();
        paint(await deployApi({ action: "new", id, mode }));
      }
      status.textContent = "AI Foundry is planning...";
      const conv = await deployApi({ action: "message", id: current.id, message: text, consent: true });
      input.value = "";
      status.textContent = "";
      paint(conv);
      await refreshHistory();
    });
    panel.append(history, log, input, consentLabel);
    // Send button was created before the inputs were appended; move it after the consent box.
    panel.append(panel.querySelector("button"));
    panel.append(planBox);
    content.append(panel);
    refreshHistory().catch((error) => {
      status.textContent = error.message;
    });
  }
  function navigate() {
    generation++;
    clearInterval(timer);
    const page = pages[location.hash];
    document.body.classList.toggle("hub-active", !!page);
    root.hidden = !page;
    if (!page) {
      restoreScope();
      return;
    }
    moveScope();
    title.textContent = page[0];
    subtitle.textContent = page[1];
    document.title = page[0] + " · Cloud Command Center";
    shownJob = null;
    currentJobs = [];
    render();
    if (location.hash === "#hub")
      loadOverview().catch((error) => {
        status.textContent = error.message;
      });
    load().catch((error) => {
      status.textContent = error.message;
    });
    timer = setInterval(
      () =>
        load().catch((error) => {
          status.textContent = error.message;
        }),
      15000,
    );
  }
  const nav = document.querySelector("nav.workspace-nav");
  // Separate Infrastructure/Security/Cost/Modernization links are not shown in the sidebar:
  // the Assessment Hub is the single entry point and its cards open each assessment.
  for (const hash of ["#hub", "#hub-deployment"]) {
    const a = element("a", pages[hash][0]);
    a.href = hash;
    nav?.append(a);
  }
  scopeBox.addEventListener("change", (event) => {
    if (event.target === document.getElementById("dc-tenant"))
      document.getElementById("dc-sublist").replaceChildren();
    shownJob = null;
    currentJobs = [];
    selectedLabel.textContent =
      customer() + " · Select authorized subscriptions.";
    render();
    load().catch((error) => {
      status.textContent = error.message;
    });
  });
  window.addEventListener("hashchange", navigate);
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", navigate);
  else navigate();
  function syncWorkspaceHeader() {
    const page = pages[location.hash],
      crumb = document.getElementById("ccc-crumb");
    if (!page) return;
    const text = "Cloud Command Center / Assessments / " + page[0];
    if (crumb && crumb.textContent !== text) crumb.textContent = text;
    const pageTitle = page[0] + " · Cloud Command Center";
    if (document.title !== pageTitle) document.title = pageTitle;
  }
  function watchHeader() {
    const crumb = document.getElementById("ccc-crumb");
    if (crumb)
      new MutationObserver(syncWorkspaceHeader).observe(crumb, {
        childList: true,
        subtree: true,
      });
    syncWorkspaceHeader();
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", watchHeader);
  else watchHeader();
  window.addEventListener("hashchange", syncWorkspaceHeader);

  const selectionKey = "ccc-azure-scope:" + window.CCC_SERVER.user;
  let restoredTenant = false,
    remembered;
  try {
    remembered = JSON.parse(localStorage.getItem(selectionKey) || "null");
  } catch {}
  function rememberScope() {
    const c = context();
    if (c.tenant && c.subscriptions.length) {
      try {
        localStorage.setItem(selectionKey, JSON.stringify(c));
      } catch {}
    }
  }
  document
    .getElementById("dc-sublist")
    ?.addEventListener("change", rememberScope);
  const tenantSelect = document.getElementById("dc-tenant"),
    subList = document.getElementById("dc-sublist");
  if (tenantSelect && subList) {
    let directoryRequest = 0,
      subscriptionRequest = 0;
    async function loadSubscriptions() {
      const request = ++subscriptionRequest;
      const tenant = tenantSelect.value;
      subList.replaceChildren();
      if (!tenant) return;
      const data = await dcRun("disc_subscriptions", { tenant });
      if (
        request !== subscriptionRequest ||
        tenant !== tenantSelect.value ||
        !data?.subscriptions
      )
        return;
      for (const subscription of data.subscriptions) {
        const label = element("label", "");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.className = "dc-sub";
        input.value = subscription.id;
        input.checked = subscription.state === "Enabled";
        input.disabled = subscription.state !== "Enabled";
        label.append(
          input,
          element(
            "span",
            subscription.name +
              " · " +
              subscription.id +
              " · " +
              subscription.state,
          ),
        );
        subList.append(label);
      }
    }
    document.getElementById("dc-tenants").onclick = async () => {
      const request = ++directoryRequest;
      const selected = tenantSelect.value || remembered?.tenant;
      const data = await dcRun("disc_tenants");
      if (request !== directoryRequest || !data?.tenants) return;
      tenantSelect.replaceChildren();
      for (const tenant of data.tenants)
        tenantSelect.add(
          new Option(tenant.name + " · " + tenant.id, tenant.id),
        );
      if (
        selected &&
        [...tenantSelect.options].some((option) => option.value === selected)
      )
        tenantSelect.value = selected;
      await loadSubscriptions();
    };
    tenantSelect.onchange = loadSubscriptions;
    const restore = () => {
      if (!remembered) return;
      if (
        !restoredTenant &&
        [...tenantSelect.options].some(
          (option) => option.value === remembered.tenant,
        )
      ) {
        restoredTenant = true;
        tenantSelect.value = remembered.tenant;
        tenantSelect.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
      if (
        restoredTenant &&
        tenantSelect.value === remembered.tenant &&
        subList.querySelector(".dc-sub")
      ) {
        for (const checkbox of subList.querySelectorAll(".dc-sub"))
          checkbox.checked = remembered.subscriptions.includes(checkbox.value);
        remembered = null;
        rememberScope();
        if (pages[location.hash])
          load().catch((error) => {
            status.textContent = error.message;
          });
      }
    };
    new MutationObserver(restore).observe(tenantSelect, { childList: true });
    new MutationObserver(() => {
      restore();
      if (subList.querySelector(".dc-sub:checked")) {
        rememberScope();
        if (pages[location.hash])
          load().catch((error) => {
            status.textContent = error.message;
          });
      }
    }).observe(subList, { childList: true });
    restore();
  }

  const homeSummary = document.createElement("section");
  homeSummary.className = "panel";
  homeSummary.append(element("h2", "Latest customer assessments"));
  const homeResults = document.createElement("div");
  homeSummary.append(homeResults);
  document.getElementById("work-home")?.append(homeSummary);
  async function updateHome() {
    if (location.hash && location.hash !== "#work-home") return;
    homeResults.replaceChildren();
    const c = context();
    if (!c.tenant || !c.subscriptions.length) {
      homeResults.append(
        element(
          "p",
          "Select a customer in Assessment Hub to see its latest saved runs.",
        ),
      );
      return;
    }
    try {
      const data = await api({ action: "list" });
      homeResults.append(element("p", customer()));
      for (const [key, label] of Object.entries(labels)) {
        const job = data.jobs.find((item) => item.sections[key]);
        if (!job) continue;
        const section = job.sections[key];
        homeResults.append(
          element(
            "p",
            `${label} · ${job.status} · ${new Date(section.captured_at).toLocaleString()}`,
          ),
        );
      }
      const link = element("a", "Open customer assessments");
      link.href = "#hub";
      homeResults.append(link);
    } catch (error) {
      homeResults.append(element("p", error.message));
    }
  }
  window.addEventListener("hashchange", updateHome);
  setInterval(updateHome, 30000);
  updateHome();

  const chat = document.createElement("details");
  chat.style.cssText =
    "position:fixed;bottom:20px;right:20px;z-index:10000;background:#111c30;border:1px solid #43617c;border-radius:14px;padding:16px;max-width:460px;width:calc(100vw - 32px);box-shadow:0 12px 40px #0008";
  chat.append(element("summary", "Cloud Command Center chat"));
  const chatScope = element("p", "");
  const chatStatus = element("p", "");
  chatStatus.setAttribute("role", "status");
  const chatControls = document.createElement("div");
  const historySelect = document.createElement("select");
  historySelect.setAttribute("aria-label", "Chat history");
  const messages = document.createElement("div");
  messages.style.cssText = "max-height:40vh;overflow:auto;white-space:pre-wrap";
  const question = document.createElement("textarea");
  question.setAttribute("aria-label", "Message to Cloud Command Center");
  question.maxLength = 3000;
  question.placeholder = "Ask about the selected customer or saved assessment";
  question.style.width = "100%";
  const sendControls = document.createElement("div");
  chat.append(
    chatScope,
    chatControls,
    historySelect,
    messages,
    question,
    sendControls,
    chatStatus,
  );
  document.body.append(chat);
  let thread = null,
    threadScope = "",
    chatGeneration = 0;
  async function chatApi(body) {
    const c = ready();
    const response = await fetch("/api/supervisor", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Local-Token": token },
      body: JSON.stringify({ channel: "assessment-chat", ...c, ...body }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Chat unavailable.");
    return data;
  }
  function renderConversation(conversation) {
    if (JSON.stringify(conversation.scope) !== JSON.stringify(context()))
      return;
    thread = conversation.id;
    threadScope = JSON.stringify(context());
    messages.replaceChildren();
    for (const item of conversation.messages) {
      const bubble = document.createElement("div");
      bubble.style.cssText = "padding:12px 0;border-bottom:1px solid #293950";
      bubble.append(
        element(
          "strong",
          item.role === "user" ? "You" : "Cloud Command Center",
        ),
        element("p", item.content),
      );
      messages.append(bubble);
    }
    messages.scrollTop = messages.scrollHeight;
  }
  async function chatHistory() {
    const data = await chatApi({ action: "list" });
    historySelect.replaceChildren(new Option("Open a saved conversation", ""));
    for (const item of data.conversations)
      historySelect.add(new Option(item.title, item.id));
  }
  async function newChat() {
    const generation = ++chatGeneration;
    const conversation = await chatApi({
      action: "new",
      id: crypto.randomUUID(),
    });
    if (generation !== chatGeneration) return;
    renderConversation(conversation);
    await chatHistory();
  }
  function chatButton(parent, label, fn) {
    const b = element("button", label);
    parent.append(b);
    b.onclick = async () => {
      b.disabled = true;
      chatStatus.textContent = "";
      try {
        await fn();
      } catch (error) {
        chatStatus.textContent = error.message;
      } finally {
        b.disabled = false;
      }
    };
  }
  chatButton(chatControls, "New Chat", newChat);
  chatButton(chatControls, "Chat History", chatHistory);
  historySelect.onchange = async () => {
    if (!historySelect.value) return;
    const generation = ++chatGeneration;
    try {
      const conversation = await chatApi({
        action: "open",
        id: historySelect.value,
      });
      if (generation === chatGeneration) renderConversation(conversation);
    } catch (error) {
      chatStatus.textContent = error.message;
    }
  };
  chatButton(sendControls, "Send to Azure model", async () => {
    if (!question.value.trim()) return;
    if (!thread || threadScope !== JSON.stringify(context())) await newChat();
    if (!thread || threadScope !== JSON.stringify(context())) return;
    const generation = chatGeneration,
      id = thread;
    chatStatus.textContent = "Consulting Azure Foundry…";
    const conversation = await chatApi({
      action: "message",
      id,
      message: question.value,
      page: location.hash,
      consent: true,
    });
    if (
      generation !== chatGeneration ||
      id !== thread ||
      JSON.stringify(conversation.scope) !== JSON.stringify(context())
    )
      return;
    question.value = "";
    renderConversation(conversation);
    chatStatus.textContent = "Conversation saved.";
    await chatHistory();
  });
  document.addEventListener("change", (event) => {
    if (
      event.target === tenantSelect ||
      event.target.classList?.contains("dc-sub")
    ) {
      chatGeneration++;
      thread = null;
      threadScope = "";
      messages.replaceChildren();
      historySelect.replaceChildren();
      chatScope.textContent =
        customer() +
        " · " +
        context().subscriptions.length +
        " selected subscription(s). Azure model usage applies.";
    }
  });
  chat.addEventListener("toggle", () => {
    if (chat.open) {
      chatScope.textContent =
        customer() +
        " · " +
        context().subscriptions.length +
        " selected subscription(s). Azure model usage applies.";
      if (threadScope !== JSON.stringify(context())) {
        thread = null;
        messages.replaceChildren();
        historySelect.replaceChildren();
      }
    }
  });
})();
