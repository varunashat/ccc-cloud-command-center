# CCC – Cloud Command Center

Noventiq's Azure assessment, pricing and deployment portal (Azure Container Apps, UAE North, AI via Azure AI Foundry).
Source of truth for the live app `cloud-command-center` in resource group `rg-cloud-command-center`.

## Layout
- `app.py`, `server_app.py`, `dashboard.html`, `ui_theme.html` – web app and UI shell
- `hub_jobs.py`, `hub_worker.py`, `hub_workspace.js` – Assessment Hub: runs, dashboard, saved results
- `hub_report.py` – detailed PDF reports; numbers computed from evidence, findings written and validated by Azure AI Foundry
- `hub_chat.py`, `deploy_chat.py` – Foundry chat for assessments and free-text deployment planning
- `laptop/portal_agent.py` – runs on the operator's laptop; Foundry drives their own Edge/Azure portal (approval before every Create/Delete/Save)
- `calculator_*.py`, `estimator/`, `discovery/`, `proposal/`, `tools/` – calculator, BOQ, discovery and proposal tooling
- `tests/` – `python -m unittest tests.test_hub_jobs tests.test_hub_chat tests.test_hub_report tests.test_deploy_chat tests.test_hub_overview tests.test_portal_agent`

## Deploying
`Dockerfile` layers the changed files on the pinned production image (`az acr build`, then `az containerapp update --image ...`).
See `IMPLEMENTATION.md` for status and the remaining requirements. `data/`, `profiles/`, `logs/`, `reports/` and `.env*` are never committed.
