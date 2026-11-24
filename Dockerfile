FROM cccacr1a5492.azurecr.io/ccc@sha256:a8c0259f1b3e46cf49aea9aab5f8d249e6e0623018b5de03e13f170440214764
RUN pip install --no-cache-dir "reportlab>=4,<6"
COPY server_app.py supervisor.js hub_jobs.py hub_worker.py hub_workspace.js hub_chat.py hub_report.py deploy_chat.py browser_agent.py laptop_agent.py dashboard.html ai_assist.py /app/

COPY laptop/__init__.py laptop/policy.py /app/laptop/
