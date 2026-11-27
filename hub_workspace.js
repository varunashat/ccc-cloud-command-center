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
    const hash = location.hash;
    // The deployment chat must survive background refreshes: rebuilding it wiped what the user was typing.
    if (hash === "#hub-deployment" && content.querySelector("[data-deploy-panel]")) return;
    content.replaceChildren();
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
    panel.dataset.deployPanel = "1";
    panel.append(
      element("h2", "Deploy with AI Foundry"),
      element(
        "p",
        "Describe what to deploy in your own words, the way you would tell Claude: virtual machines, containers, App Service, Azure Firewall, networking, storage or a whole environment. In browser mode AI Foundry then does it end to end in your own signed-in Azure portal tab, step by step, and reports back here. You stay in control with Stop. Passwords and MFA are always entered by you.",
      ),
    );
    const modeRow = document.createElement("div");
    const modes = {};
    for (const [value, text] of [
      ["browser", "Deploy through my browser, fully automatic (AI Foundry operates my signed-in Azure portal tab via the CCC extension)"],
      ["laptop", "Run on my laptop: Azure CLI, PowerShell, Git, GitHub and my selected folders (AI Foundry plans, you approve, then it executes and verifies)"],
      ["cloud", "Plan only (AI Foundry drafts resources, CLI steps and checks; nothing runs)"],
    ]) {
      const label = document.createElement("label");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "deploy-mode";
      radio.value = value;
      radio.checked = value === "browser";
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
    log.style.cssText = "max-height:60vh;overflow-y:auto;border:1px solid #293950;border-radius:12px;padding:12px;margin:12px 0;display:flex;flex-direction:column;gap:10px";
    let stick = true;
    log.addEventListener("scroll", () => {
      stick = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    });
    function scrollDown() {
      if (stick) log.scrollTop = log.scrollHeight;
    }
    function addBubble(role, text) {
      const bubble = document.createElement("div");
      bubble.style.cssText = "white-space:pre-wrap;overflow-wrap:anywhere;padding:10px 14px;border-radius:14px;max-width:88%;" +
        (role === "user" ? "align-self:flex-end;background:#1e4f8f;" : "align-self:flex-start;background:#1a2740;");
      bubble.textContent = text;
      log.append(bubble);
      scrollDown();
      return bubble;
    }
    let runBubble = null;
    let agentJob = null;
    let agentKind = "browser";
    let queue = [];
    let queueTimer = null;
    let queueFails = 0;
    function queueEntry(role, text) {
      queue.push({ role, text });
      clearTimeout(queueTimer);
      queueTimer = setTimeout(flushQueue, 800);
    }
    async function flushQueue() {
      if (!agentJob || !queue.length) return;
      const entries = queue.splice(0);
      try {
        const response = await fetch("/api/supervisor", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Local-Token": token },
          body: JSON.stringify({ channel: "chat-history", action: "note", kind: agentKind, id: agentJob, entries }),
        });
        if (!response.ok) throw new Error("not saved");
        queueFails = 0;
      } catch (e) {
        queue.unshift(...entries);
        if (++queueFails < 6) queueTimer = setTimeout(flushQueue, 2000);
      }
    }
    function userSays(text) {
      runBubble = null;
      stick = true;
      addBubble("user", text);
      queueEntry("user", text);
    }
    let runPromise = null;
    let jobReady = false;
    let startContext = "";
    let pictures = [];
    let startImages = [];
    const thumbs = document.createElement("div");
    thumbs.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin:6px 0;";
    function shrink(file) {
      return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
          URL.revokeObjectURL(url);
          const scale = Math.min(1, 1400 / Math.max(img.width, img.height));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(img.width * scale));
          canvas.height = Math.max(1, Math.round(img.height * scale));
          const ctx = canvas.getContext("2d");
          ctx.fillStyle = "#fff";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          let quality = 0.85;
          let data = canvas.toDataURL("image/jpeg", quality);
          while (data.length > 1200000 && quality > 0.4) { quality -= 0.15; data = canvas.toDataURL("image/jpeg", quality); }
          data.length > 1500000 ? reject(new Error("Picture is too large.")) : resolve(data);
        };
        img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("That file is not a picture.")); };
        img.src = url;
      });
    }
    function drawThumbs() {
      thumbs.replaceChildren(...pictures.map((src, index) => {
        const box = document.createElement("span");
        const img = document.createElement("img");
        img.src = src;
        img.alt = "Attached picture " + (index + 1);
        img.style.cssText = "height:64px;border-radius:8px;border:1px solid #2a3a57;";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "x";
        remove.setAttribute("aria-label", "Remove picture " + (index + 1));
        remove.onclick = () => { pictures.splice(index, 1); drawThumbs(); };
        box.append(img, remove);
        return box;
      }));
    }
    async function addPictures(files) {
      for (const file of [...files].filter((f) => /^image\//.test(f.type))) {
        if (pictures.length >= 4) { status.textContent = "Up to 4 pictures per message."; break; }
        try { pictures.push(await shrink(file)); } catch (error) { status.textContent = error.message; }
      }
      drawThumbs();
    }
    const sayWaiters = [];
    function threadContext() {
      return [...log.children].map((b) => (b.style.alignSelf === "flex-end" ? "Operator: " : "Assistant: ") + b.textContent).join("\n").slice(-2500);
    }
    async function sayToRun(text, images = []) {
      for (let i = 0; i < 40 && runPromise && !jobReady; i++) await new Promise((r) => setTimeout(r, 100));
      if (!agentJob || !jobReady) return false;
      const api = agentKind === "laptop" ? laptopApi : deployAgentApi;
      const res = await api({ action: "say", id: agentJob, text, images });
      if (!res.accepted) return false;
      if (resumeUser) resumeUser();
      for (const release of sayWaiters.splice(0)) release();
      return true;
    }
    function startRun(runner, text) {
      const promise = runner(text)
        .catch((error) => logLine("Error: " + error.message))
        .finally(() => {
          if (runPromise === promise) runPromise = null;
        });
      runPromise = promise;
    }
    async function openSaved(kind, id) {
      const chat = await fetch("/api/supervisor", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Local-Token": token },
        body: JSON.stringify({ channel: "chat-history", action: "open", kind, id }),
      }).then((r) => r.json());
      if (chat.error) throw new Error(chat.error);
      current = null;
      runBubble = null;
      log.replaceChildren();
      planBox.replaceChildren();
      stick = true;
      let group = null;
      for (const entry of chat.transcript) {
        if (entry.role === "user") { group = null; addBubble("user", entry.text); continue; }
        if (!group) group = addBubble("assistant", entry.text);
        else group.textContent += "\n" + entry.text;
      }
      addBubble("assistant", `Saved ${kind} chat (${chat.status || "no status"}). This is a read-only copy; send a new message to continue with a new run.`);
    }

    function paint(conv) {
      current = conv;
      log.replaceChildren();
      for (const m of conv.messages) {
        let text = m.content;
        if (m.role === "assistant") {
          try {
            const p = JSON.parse(text.match(/\{[\s\S]*\}/)[0]);
            text = [p.reply, ...(p.questions || []).map((q) => "? " + q)].filter(Boolean).join("\n");
          } catch (_) {}
        }
        addBubble(m.role === "user" ? "user" : "assistant", text);
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
      const agents = await fetch("/api/supervisor", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Local-Token": token },
        body: JSON.stringify({ channel: "chat-history", action: "list" }),
      }).then((r) => r.json()).catch(() => ({ chats: [] }));
      const rows = [
        ...out.conversations.map((c) => ({ value: "cloud:" + c.id, label: chatTitle(c), when: c.created })),
        ...(agents.chats || []).map((c) => ({ value: c.kind + ":" + c.id, label: `${c.title} · ${c.kind} · ${c.status}`, when: c.updated })),
      ].sort((a, b) => String(b.when).localeCompare(String(a.when)));
      history.replaceChildren(new Option("New chat", ""));
      rows.forEach((row) => history.add(new Option(row.label, row.value)));
      if (current) history.value = "cloud:" + current.id;
    }
    history.onchange = async () => {
      if (!history.value) {
        current = null;
        log.replaceChildren();
        planBox.replaceChildren();
        return;
      }
      const [kind, id] = [history.value.split(":")[0], history.value.split(":")[1]];
      if (kind !== "cloud") {
        await openSaved(kind, id);
        return;
      }
      const conv = await deployApi({ action: "open", id });
      if (modes[conv.mode]) modes[conv.mode].checked = true;
      paint(conv);
    };
    let stopRequested = false;
    let resumeUser = null;
    const stopButton = control(panel, "Stop", async () => {
      if (stopRequested) return;
      stopRequested = true;
      if (agentJob) await (lastRunner === runLaptop ? laptopApi : deployAgentApi)({ action: "stop", id: agentJob });
      if (resumeUser) resumeUser();
      for (const release of stopWaiters.splice(0)) release();
      logLine("Stopped. Type a follow-up any time and I will continue from this conversation.");
    });
    stopButton.hidden = true;
    const continueButton = control(panel, "I'm done, continue", async () => {
      continueButton.hidden = true;
      if (resumeUser) resumeUser();
    });
    continueButton.hidden = true;
    let approveNext = false;
    let lastTask = "";
    let lastRunner = null;
    const recovery = document.createElement("div");
    recovery.hidden = true;
    function showRecovery(reason) {
      recovery.replaceChildren();
      recovery.hidden = false;
      const note = document.createElement("p");
      note.textContent = "Not completed: " + reason;
      recovery.append(note);
      control(recovery, "Retry", async () => {
        recovery.hidden = true;
        userSays("Retry");
        await lastRunner("Previous attempt failed (" + reason + "). Read the error, change something concrete, then try again.\n" + lastTask, lastTask);
      });
      if (lastRunner === runAgent) control(recovery, "Suggest another way", async () => {
        const res = await deployAgentApi({ action: "alternatives", id: agentJob });
        const box = document.createElement("div");
        if (!res.alternatives.length) box.textContent = "No alternative suggested. Rephrase the task and send again.";
        for (const option of res.alternatives) {
          control(box, option.title, async () => {
            recovery.hidden = true;
            userSays("Try this way: " + option.title);
            await runAgent(option.task, lastTask);
          });
          const detail = document.createElement("small");
          detail.textContent = " " + option.detail;
          box.append(detail, document.createElement("br"));
        }
        recovery.append(box);
      });
    }
    async function autoRecover(reason, attempt, runner) {
      if (stopRequested || attempt >= 2) {
        logLine("Stopped trying automatically after " + attempt + " recovery attempt(s). Choose Retry or Suggest another way below, or rephrase the task.");
        showRecovery(reason);
        return;
      }
      try {
        if (runner === runAgent) {
          const res = await deployAgentApi({ action: "alternatives", id: agentJob });
          const option = res.alternatives[0];
          if (!option) throw new Error("no alternative suggested");
          logLine(`Trying another way automatically (${attempt + 1}/2): ${option.title}. ${option.detail}`);
          await runAgent(option.task, lastTask, attempt + 1);
        } else {
          logLine(`Trying a different approach automatically (${attempt + 1}/2). You will be asked to approve the new plan.`);
          await runLaptop("Previous attempt was blocked (" + reason + "). Use a different approach within the same goal and verify before finishing.\n" + lastTask, lastTask, attempt + 1);
        }
      } catch (error) {
        logLine("Automatic recovery failed: " + error.message);
        showRecovery(reason);
      }
    }
    function logLine(text) {
      if (!runBubble) runBubble = addBubble("assistant", "");
      runBubble.textContent += (runBubble.textContent ? "\n" : "") + text;
      queueEntry("assistant", text);
      scrollDown();
    }
    async function deployAgentApi(body) {
      const response = await fetch("/api/supervisor", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Local-Token": token },
        body: JSON.stringify({ channel: "browser-agent", ...body }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Browser agent request failed.");
      return data;
    }
    function ext(op, extra = {}, timeout = 30000) {
      return new Promise((resolve, reject) => {
        const id = crypto.randomUUID();
        const timer = setTimeout(() => {
          window.removeEventListener("message", onMessage);
          reject(new Error("The CCC browser extension did not answer. Install extension v2.0 (Edge/Chrome, Load unpacked) and reload this page."));
        }, timeout);
        function onMessage(event) {
          const m = event.data;
          if (event.source !== window || event.origin !== location.origin || !m || m.src !== "ccc-ext" || m.type !== "agent-result" || m.id !== id) return;
          clearTimeout(timer);
          window.removeEventListener("message", onMessage);
          if (m.result && m.result.error) reject(new Error(m.result.error));
          else resolve(m.result);
        }
        window.addEventListener("message", onMessage);
        window.postMessage({ src: "ccc-page", type: "agent", id, op, ...extra }, location.origin);
      });
    }
    async function runAgent(task, original, attempt = 0) {
      stopRequested = false;
      lastTask = original || task;
      lastRunner = runAgent;
      recovery.hidden = true;
      approveNext = false;
      runBubble = null;
      agentKind = "browser";
      agentJob = null;
      jobReady = false;
      const ping = await ext("ping", {}, 4000);
      if (!ping || !ping.ok || parseInt(ping.version, 10) < 2)
        throw new Error("Update the CCC browser extension to v2.0 first (see extension/README in the repo).");
      agentJob = crypto.randomUUID();
      await deployAgentApi({ action: "start", id: agentJob, task, consent: true, context: startContext || threadContext(), images: startImages.splice(0) });
      startContext = "";
      jobReady = true;
      flushQueue();
      stopButton.hidden = false;
      logLine("Started. Opening the Azure portal in your browser...");
      try {
        await ext("open");
        await new Promise((r) => setTimeout(r, 5000));
        let results = [];
        for (let step = 1; step <= 250 && !stopRequested; step++) {
          const observation = await ext("observe");
          const out = await deployAgentApi({ action: "step", id: agentJob, observation, results, approve: approveNext });
          approveNext = false;
          results = [];
          logLine(`[${step}] ${out.log.length ? out.log[out.log.length - 1].thought : ""}`);
          for (const note of out.log.length ? out.log[out.log.length - 1].notes : []) logLine("   ! " + note);
          if (out.blocked || out.status === "blocked") {
            logLine("Blocked: " + (out.blocked || out.blocker));
            await autoRecover(out.blocked || out.blocker, attempt, runAgent);
            break;
          }
          if (out.done) {
            logLine("Finished: " + (out.summary || "done"));
            if (/^could not|^unable|^failed/i.test(out.summary || "")) await autoRecover(out.summary, attempt, runAgent);
            break;
          }
          if (out.status === "failed" || out.status === "stopped") {
            logLine("Agent " + out.status + ".");
            if (out.status === "failed") showRecovery("step limit reached or agent error");
            break;
          }
          if (out.wait_user) {
            logLine("Waiting for you: " + out.wait_user);
            const approval = /^Approval needed/.test(out.wait_user);
            approveNext = approval;
            continueButton.textContent = approval ? "Approve and continue" : "I'm done, continue";
            continueButton.hidden = false;
            await new Promise((resolve) => (resumeUser = resolve));
            resumeUser = null;
            continue;
          }
          for (const action of out.actions) {
            if (stopRequested) break;
            const r = await ext("act", { action });
            results.push(`${action.do} ${action.id || ""} -> ${r.result}`);
            await new Promise((resolve) => setTimeout(resolve, 900));
          }
          if (!out.actions.length) await new Promise((resolve) => setTimeout(resolve, 1500));
        }
      } catch (error) {
        logLine("Error: " + error.message);
        showRecovery(error.message);
      } finally {
        flushQueue();
        refreshHistory().catch(() => {});
        stopButton.hidden = true;
        continueButton.hidden = true;
      }
    }
    const EXE = "http://127.0.0.1:8765";
    const exePanel = document.createElement("div");
    const exeStatus = document.createElement("div");
    const exeCode = document.createElement("input");
    exeCode.placeholder = "Pairing code shown in the executor window";
    exeCode.setAttribute("aria-label", "Executor pairing code");
    let exeToken = "";
    try { exeToken = sessionStorage.getItem("ccc-exe-token") || ""; } catch (e) { exeToken = ""; }
    let exeInfo = null;
    // Chrome's Local Network Access needs the request to declare its target; fall back for older browsers.
    async function localFetch(url, opts) {
      for (const space of ["loopback", "local", null]) {
        try {
          return await fetch(url, space ? { ...opts, targetAddressSpace: space } : opts);
        } catch (e) {
          if (e && e.name === "AbortError") throw e;
          if (space === null) throw e;
        }
      }
    }
    async function exe(path, body, timeout = 650000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);
      try {
        const response = await localFetch(EXE + path, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Executor-Token": exeToken },
          body: JSON.stringify(body || {}),
          signal: controller.signal,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Executor request failed.");
        return data;
      } finally {
        clearTimeout(timer);
      }
    }
    async function refreshExe() {
      exeInfo = null;
      try {
        const hello = await localFetch(EXE + "/", {}).then((r) => r.json());
        if (exeToken) {
          try { exeInfo = await exe("/status", {}, 5000); } catch (e) { exeToken = ""; }
        }
        exeStatus.textContent = exeInfo
          ? `Connected to laptop executor v${exeInfo.version}. Tools: ${Object.entries(exeInfo.tools).map(([k, v]) => k + (v ? " ok" : " missing")).join(", ")}. Folders: ${exeInfo.folders.join("; ") || "none selected"}.`
          : `Executor v${hello.version} is running on this laptop but not connected. Type the pairing code and press Connect.`;
      } catch (e) {
        exeStatus.textContent = "Cannot reach the laptop executor from this page. If its window is open, Chrome is blocking this site from your computer: click the icon left of the address bar, Site settings, set Local network access to Allow, reload and press Check. Otherwise start Start-CCC-Executor.cmd first.";
      }
    }
    control(exePanel, "Check", refreshExe);
    control(exePanel, "Connect", async () => {
      const pair = await fetch(EXE + "/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: exeCode.value.trim() }) }).then((r) => r.json());
      if (!pair.token) throw new Error(pair.error || "Pairing failed.");
      exeToken = pair.token;
      try { sessionStorage.setItem("ccc-exe-token", exeToken); } catch (e) { /* kept in memory only */ }
      exeCode.value = "";
      await refreshExe();
    });
    control(exePanel, "Disconnect", async () => {
      try { await exe("/disconnect", {}, 5000); } catch (e) { /* already gone */ }
      exeToken = "";
      try { sessionStorage.removeItem("ccc-exe-token"); } catch (e) { /* ignore */ }
      await refreshExe();
    });
    exePanel.prepend(exeStatus, exeCode);
    const syncModePanels = () => { exePanel.hidden = !modes.laptop.checked; };
    for (const radio of Object.values(modes)) radio.addEventListener("change", syncModePanels);
    async function laptopApi(body) {
      const response = await fetch("/api/supervisor", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Local-Token": token },
        body: JSON.stringify({ channel: "laptop-agent", ...body }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Laptop assistant request failed.");
      return data;
    }
    function waitFor(buttons) {
      return new Promise((resolve) => {
        const box = document.createElement("div");
        for (const [label, value] of buttons) {
          control(box, label, async () => { box.remove(); resolve(value); });
        }
        recovery.after(box);
        stopWaiters.push(() => { box.remove(); resolve("stop"); });
        sayWaiters.push(() => { box.remove(); resolve("say"); });
      });
    }
    const stopWaiters = [];
    function showPlan(out) {
      const plan = out.plan || {};
      const cost = out.cost || { lines: [] };
      logLine("PLAN: " + (plan.summary || ""));
      logLine("Target: " + JSON.stringify(plan.target || {}));
      (plan.steps || []).forEach((step, i) => logLine(`  ${i + 1}. ${step}`));
      for (const risk of plan.risks || []) logLine("  risk: " + risk);
      for (const line of cost.lines) logLine("  cost: " + line.item + " -> " + (line.error ? line.error : `${line.monthly} ${line.currency}/month (${line.unit_price} per ${line.unit})`));
      logLine(`Estimated new monthly cost: ${cost.total_monthly} USD${cost.complete ? "" : " (INCOMPLETE: some prices were not found)"}. Source: ${cost.source || "n/a"}. ${cost.note || ""}`);
    }
    async function runLaptop(task, original, attempt = 0) {
      stopRequested = false;
      lastTask = original || task;
      lastRunner = runLaptop;
      recovery.hidden = true;
      runBubble = null;
      agentKind = "laptop";
      agentJob = null;
      jobReady = false;
      await refreshExe();
      if (!exeInfo) throw new Error("Connect the laptop executor first (start it, enter the pairing code, press Connect).");
      agentJob = crypto.randomUUID();
      await laptopApi({ action: "start", id: agentJob, task, consent: true, context: startContext || threadContext(), images: startImages.splice(0) });
      startContext = "";
      jobReady = true;
      flushQueue();
      stopButton.hidden = false;
      logLine("Checking identity on your laptop (read-only)...");
      const run = async (tool, args, extra = {}) => exe("/run", { tool, args, ...extra });
      const identityRun = await run("az", ["account", "show", "-o", "json"]);
      let identity = identityRun.exit === 0 ? identityRun.stdout : "az account show failed: " + (identityRun.stderr || identityRun.error || "not signed in. Run az login yourself in a terminal, then retry.");
      logLine(identityRun.exit === 0 ? "Azure identity (sanitized): " + identity.slice(0, 600) : identity);
      let results = [];
      let approvePlan = false;
      let approveCommands = [];
      try {
        for (let step = 1; step <= 60 && !stopRequested; step++) {
          const out = await laptopApi({ action: "step", id: agentJob, results, identity, laptop: { tools: exeInfo.tools, folders: exeInfo.folders }, approve_plan: approvePlan, approve_commands: approveCommands });
          results = [];
          approvePlan = false;
          approveCommands = [];
          const last = out.log && out.log.length ? out.log[out.log.length - 1] : null;
          if (last) {
            logLine(`[${step}] ${last.thought}`);
            for (const note of last.notes) logLine("   ! " + note);
          }
          if (out.blocked) { logLine("Blocked: " + out.blocked); await autoRecover(out.blocked, attempt, runLaptop); break; }
          if (out.done) { logLine("Finished: " + (out.summary || "done")); break; }
          if (out.status === "stopped") { logLine("Stopped."); break; }
          if (out.needs_plan_approval) {
            showPlan(out);
            const choice = await waitFor([["Approve plan and run", "go"], ["Reject", "stop"]]);
            if (choice === "say") continue;
            if (choice !== "go") { logLine("Plan rejected. Nothing was changed."); break; }
            approvePlan = true;
            logLine("Plan approved by you.");
            continue;
          }
          if (out.wait_user) {
            logLine("Waiting for you: " + out.wait_user);
            const choice = await waitFor([["I'm done, continue", "go"]]);
            if (choice === "stop") break;
            continue;
          }
          if (out.held && out.held.length) {
            for (const held of out.held) logLine(`HELD for your approval: ${held.tool} ${held.args.join(" ")}  (${held.reason})`);
            const choice = await waitFor([["Approve these commands", "go"], ["Skip them", "skip"]]);
            if (choice === "stop") break;
            if (choice === "say") continue;
            if (choice === "go") approveCommands = out.held.map((h) => h.key);
          }
          for (const command of out.commands) {
            if (stopRequested) break;
            const result = await run(command.tool, command.args, { cwd: command.cwd, plan_approved: out.plan_approved === true, approved: command.kind === "approval" });
            result.key = command.key;
            results.push(result);
            const text = (result.stdout || result.stderr || result.error || "").trim().replace(/\s+/g, " ").slice(0, 300);
            logLine(`  $ ${command.tool} ${command.args.join(" ")}\n    exit ${result.exit === undefined ? "not run" : result.exit}${result.seconds ? " (" + result.seconds + "s)" : ""}: ${text}`);
          }
          for (const path of out.reads || []) {
            const file = await exe("/read", { path });
            results.push({ tool: "read", path, exit: file.error ? 1 : 0, text: file.text || (file.entries || []).join("\n"), error: file.error });
            logLine(`  read ${path}: ${file.error || "ok"}`);
          }
          if (!out.commands.length && !(out.reads || []).length && !approveCommands.length) await new Promise((r) => setTimeout(r, 1500));
        }
      } catch (error) {
        logLine("Error: " + error.message);
        showRecovery(error.message);
      } finally {
        flushQueue();
        refreshHistory().catch(() => {});
        stopButton.hidden = true;
        for (const release of stopWaiters.splice(0)) release();
      }
    }
    const checksBox = document.createElement("div");
    checksBox.style.whiteSpace = "pre-wrap";
    const checksButton = control(panel, "Check connections", async () => {
      const rows = [];
      const add = (name, state, detail) => rows.push(`${state.padEnd(15)} ${name}${detail ? " - " + detail : ""}`);
      try {
        const id = crypto.randomUUID();
        await deployAgentApi({ action: "start", id, task: "connection check", consent: true });
        const res = await deployAgentApi({ action: "selftest", id });
        add("Azure AI Foundry response", res.ok ? "PASS" : "FAIL", res.detail);
      } catch (error) { add("Azure AI Foundry response", "FAIL", error.message); }
      try {
        const ping = await ext("ping", {}, 4000);
        add("Browser extension connected", ping && ping.ok ? "PASS" : "FAIL", ping && ping.version ? "v" + ping.version : "");
        const tab = await ext("observe", {}, 8000).catch((error) => ({ error: error.message }));
        add("Read-only browser action (read the portal tab)", tab.elements ? "PASS" : "NOT CONFIGURED", tab.elements ? `${tab.elements.length} controls on ${tab.title || tab.url}` : "open the Azure portal via a task first (" + (tab.error || "") + ")");
      } catch (error) { add("Browser extension connected", "FAIL", error.message); }
      await refreshExe();
      const todo = [
        ["az account show on laptop", () => exe("/run", { tool: "az", args: ["account", "show", "--query", "{name:name,tenant:tenantId,user:user.name}", "-o", "json"] }, 60000)],
        ["Read-only Azure query on laptop", () => exe("/run", { tool: "az", args: ["group", "list", "--only-show-errors", "--query", "[].name", "-o", "tsv"] }, 60000)],
        ["Read a selected local project file", async () => {
          const folder = exeInfo && exeInfo.folders[0];
          if (!folder) return { error: "no folder selected: start the executor with --folder" };
          const list = await exe("/read", { path: folder }, 20000);
          return list.error ? list : { exit: 0, stdout: `${list.entries.length} entries in ${folder}` };
        }],
        ["git status in a selected repo", () => exe("/run", { tool: "git", args: ["status", "--short", "--branch"], cwd: exeInfo && exeInfo.folders[0] }, 30000)],
        ["Read-only GitHub query", () => exe("/run", { tool: "gh", args: ["api", "user", "--jq", ".login"] }, 30000)],
      ];
      for (const [name, fn] of todo) {
        if (!exeInfo) { add(name, "NOT CONFIGURED", "start the laptop executor and press Connect"); continue; }
        try {
          const r = await fn();
          const state = r.exit === 0 ? "PASS" : (r.ran === false || /not installed|no folder|not signed|Please run/i.test((r.error || "") + (r.stderr || "")) ? "NOT CONFIGURED" : "FAIL");
          const text = ((state === "PASS" ? r.stdout : (r.stderr || r.error || r.stdout)) || "").trim().replace(/\s+/g, " ");
          add(name, state, state === "PASS" ? (name.startsWith("Read-only Azure query") ? text.split(" ").filter(Boolean).length + " resource groups visible" : text.slice(0, 160)) : text.slice(-300));
        } catch (error) { add(name, "FAIL", error.message); }
      }
      checksBox.textContent = rows.join("\n");
    });
    const sendButton = control(panel, "Send", async () => {
      const typed = input.value.trim();
      if (!typed && !pictures.length) throw new Error("Describe what to deploy, or attach a picture.");
      if (!consent.checked) throw new Error("Tick the Azure AI Foundry consent box first.");
      const text = typed || "Look at the attached picture(s) and do what they show or ask.";
      const imgs = pictures.splice(0);
      drawThumbs();
      const earlier = threadContext();
      userSays(text + (imgs.length ? "\n[" + imgs.length + " picture" + (imgs.length > 1 ? "s" : "") + " attached]" : ""));
      if (modes.laptop.checked || modes.browser.checked) {
        input.value = "";
        status.textContent = "";
        if (runPromise && !stopRequested && (await sayToRun(text, imgs))) return;
        if (runPromise) await runPromise;
        startContext = earlier;
        startImages = imgs;
        startRun(modes.laptop.checked ? runLaptop : runAgent, text);
        return;
      }
      const mode = "cloud";
      if (!current) {
        const id = crypto.randomUUID();
        paint(await deployApi({ action: "new", id, mode }));
      }
      status.textContent = "AI Foundry is planning...";
      const conv = await deployApi({ action: "message", id: current.id, message: text, consent: true, images: imgs });
      input.value = "";
      status.textContent = "";
      paint(conv);
      await refreshHistory();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendButton.click();
      }
    });
    const voiceRow = document.createElement("div");
    const voiceLang = document.createElement("select");
    voiceLang.setAttribute("aria-label", "Voice language");
    for (const [value, label] of [["en-IN", "English (India)"], ["hi-IN", "हिन्दी"], ["en-US", "English (US)"]]) voiceLang.add(new Option(label, value));
    const mic = element("button", "Speak");
    mic.type = "button";
    let recognition = null;
    mic.onclick = () => {
      const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!Recognition) { status.textContent = "Voice input needs Chrome or Edge."; return; }
      if (recognition) { recognition.stop(); return; }
      recognition = new Recognition();
      recognition.lang = voiceLang.value;
      recognition.interimResults = true;
      recognition.continuous = true;
      const base = input.value ? input.value + " " : "";
      recognition.onresult = (event) => {
        input.value = base + [...event.results].map((r) => r[0].transcript).join(" ");
      };
      recognition.onerror = (event) => { status.textContent = "Voice input: " + event.error; };
      recognition.onend = () => { recognition = null; mic.textContent = "Speak"; };
      recognition.start();
      mic.textContent = "Stop speaking";
    };
    const attach = element("button", "Attach picture");
    attach.type = "button";
    const picker = document.createElement("input");
    picker.type = "file";
    picker.accept = "image/*";
    picker.multiple = true;
    picker.hidden = true;
    picker.onchange = async () => { await addPictures(picker.files); picker.value = ""; };
    attach.onclick = () => picker.click();
    input.addEventListener("paste", (event) => {
      const files = [...(event.clipboardData ? event.clipboardData.files : [])].filter((f) => /^image\//.test(f.type));
      if (files.length) { event.preventDefault(); addPictures(files); }
    });
    input.addEventListener("dragover", (event) => event.preventDefault());
    input.addEventListener("drop", (event) => {
      const files = [...(event.dataTransfer ? event.dataTransfer.files : [])].filter((f) => /^image\//.test(f.type));
      if (files.length) { event.preventDefault(); addPictures(files); }
    });
    voiceRow.append(attach, picker, mic, voiceLang, element("small", " Enter sends, Shift+Enter adds a line. You can type while it works. Paste or drop a screenshot to send it."));
    // Buttons were created before the inputs; append everything in display order.
    panel.append(exePanel, history, log, input, thumbs, voiceRow, consentLabel, sendButton, stopButton, continueButton, checksButton, checksBox, recovery, planBox);
    content.append(panel);
    syncModePanels();
    try {                                              // a task handed over from the Azure services and SKUs page
      const handed = localStorage.getItem("ccc_agent_task");
      if (handed) {
        localStorage.removeItem("ccc_agent_task");
        input.value = handed;
        modes.browser.checked = true;
        syncModePanels();
      }
    } catch (e) { /* storage unavailable */ }
    refreshExe();
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
