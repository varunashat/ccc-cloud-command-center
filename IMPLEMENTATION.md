# Implementation and verification

The original A-to-Z document remains the requirements baseline. This file records the implementation status as of 24 November 2026.

## Current delivered increment

- Production source was recovered from revision 23 and combined with the reviewed overlays through revision 27 into this local Git repository.
- Separate routes provide the Assessment Hub, infrastructure, security, cost, modernization and Environment & Deployment views.
- The existing customer directory and subscription controls are shared across assessment pages. The saved selection is a browser preference. Every backend read validates current tenant and subscription access.
- Live assessment jobs save their request and per-workflow checkpoints on Azure Files. The scheduled Supervisor worker uses the operator's existing encrypted Microsoft cache. Pause, resume and cancellation affect pending work. A fresh Microsoft sign-in requirement is recorded per affected job.
- The collectors return infrastructure evidence, Defender assessments, configured network-control evidence, Advisor recommendations and actual cost rows, and VM platform evidence. Backup coverage, observed traffic, application dependency and utilization evidence remain gaps.
- CSV and JSON downloads are available for completed assessment sections. The Hub shows scoped saved runs, and Home shows latest scoped results.
- Shared Foundry chat saves conversations under user and tenant/subscription scope, with New Chat and Chat History. New chats have empty conversation history. The model receives bounded saved assessment evidence and no credentials.
- The deployment view prepares a private Linux VM draft and ARM template. Pricing, quota, policy, image validation, device pairing and execution remain pending.

## Verification

Six targeted regression tests passed for ownership and scope checks, request idempotency, pause/resume, checkpoint reuse, revoked-session blocking, independent workflow progress and fresh-chat isolation. An authenticated local HTTP check passed and rejected unsigned API calls.

Read-only SHJ collection through the authorized local Azure CLI session returned 400 infrastructure resources, 113 VM/Arc platform records and 22 actual-cost rows. The network-control query returned zero matches. These counts describe the captured visible results.

The production cloud session for the SHJ tenant could not obtain an ARM token. The application now offers a selected-directory sign-in refresh. Live queued-run completion and model chat acceptance through that refreshed session remain unverified.

## Remaining original requirements

| Requirement | Status |
| --- | --- |
| Detailed backup/DR, dependency and utilization assessments | Further collectors and acceptance required |
| Validated rightsizing, RI and Savings Plan calculations | Pending |
| Full date-range, grouping, drill-down and amortized cost UI | Pending |
| Dynamic EA/Excel/CSV portfolio reconciliation and four-hour refresh | Pending |
| Project management and project-scoped connectors, GitHub and MCP administration | Pending |
| Skills upload/catalog and artifact tooling | Pending |
| Saved-record change previews and approvals from chat | Pending |
| Voice, image and research tools | Backend capability wiring pending |
| Paired deployment extension and Windows agent | Pending |
| Versioned deployment approval, two-VM and App Service execution | Pending |
| Cross-tenant migration readiness and governed execution | Pending source/target scope and implementation |
| Comprehensive PDF/Excel exports and acceptance matrix | Pending |

The deployed weather agent in del_test passed its separate forecast test through the local runner. That verifies the weather case. The full A-to-Z specification remains in progress.

Production rollout verified on revision 0000030. Azure reports it ready and healthy with all traffic. The browser confirmed distinct assessment routes, shared scope controls, deployment plan inputs and shared chat. The synthetic start buttons have been removed. Customer-directory load concurrency was corrected. The SHJ Microsoft verification flow was opened for the operator, and authenticated SHJ background acceptance remains pending. Registry build dg1j pins image digest sha256:d59ab379d55dd1248c737a61dead90e9aca49a77ae149f6966681d65b41e9e83. The worker uses the same assessment implementation with encrypted-cache access and a 300-second execution limit.

After the operator completed Microsoft verification, a user-initiated HIPA assessment run completed through the scheduled Azure worker. The browser displayed all four saved sections. Its downloaded infrastructure CSV was checked and contained 56 rows, all within the selected tenant and two subscriptions. This verifies the unattended customer assessment path for HIPA. The previously selected SHJ tenant requires its own successful cloud sign-in before its queued acceptance can be verified.

Final rollout is revision 0000032, image sha256:1e0073558f15b81ce9e91aa65d86a504e9ea43e4fbf59e2a7faa1b2b84ddc665 from build dg1m. The application health endpoint and Azure ready state passed. Recent scheduled worker executions succeeded. Live Foundry chat correctly reported the completed HIPA job and its four saved workflow names after job status was added to model evidence. The final UI guards late responses when conversations or customer scope change.

## 24 November 2026: Client Inventory, Hub dashboard, PDF reports, deployment chat (branch feature/client-inventory-hub-pdf-deploy-chat)

- Home area renamed Client Inventory; the eight workflow tiles are hidden there.
- Assessment Hub: sidebar lists only Assessment Hub and Environment & Deployment; per-assessment links stay as cards. New customer dashboard (all customers/runs of the signed-in operator).
- Results export is a PDF (`hub_report.py`): computed metrics, Foundry-written findings validated against saved resource IDs, evidence gaps, appendix. CSV/JSON buttons removed.
- Environment & Deployment: the VM/region form is replaced by a Foundry planning chat (cloud mode) and a laptop mode that exports a task for `laptop/portal_agent.py`.
- Verified: unit tests (report, deploy chat, overview, laptop guards). NOT verified: rendering in a browser, a live Foundry call, the laptop agent against the real Azure portal, the container build with reportlab.

## Browser agent (Environment & Deployment chat)
- Page (hub_workspace.js `runAgent`) runs the loop: extension `observe` -> `/api/supervisor` channel `browser-agent` `step` -> extension `act`.
- Server (browser_agent.py) asks AI Foundry for up to 6 actions, vets them (host allowlist, secret fields refused, step cap 250, stop flag).
- Extension (extension/agent_bg.js, ccc_bridge.js) observes/acts across frames in one tab.
- Test: `python tests/e2e/run_extension_e2e.py` (headless Chrome, local fake portal with an iframe; needs playwright + google-chrome).
- Not yet exercised against the real Azure portal; portal widgets (custom comboboxes) may need follow-up tuning.

## Laptop executor ("Run on my laptop" mode)
- laptop/executor.py (local 127.0.0.1 server, pairing code, CORS only for the CCC origin), laptop/policy.py (forbidden / approval / write / read-only classes, secret scrubbing, folder guard).
- laptop_agent.py: Foundry plans -> priced estimate (Azure Retail Prices API) -> operator approves -> commands -> verification; blocks after 3 identical failures.
- Page (hub_workspace.js `runLaptop`): connect status, plan + cost, held-command approvals, command evidence, Check connections (real checks).
- Tests: tests/test_laptop_*.py and tests/e2e/run_laptop_e2e.py. Windows installer and az / PowerShell paths not yet run on a real laptop.
