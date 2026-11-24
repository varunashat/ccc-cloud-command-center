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
