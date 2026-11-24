FROM cccacr1a5492.azurecr.io/ccc@sha256:a8c0259f1b3e46cf49aea9aab5f8d249e6e0623018b5de03e13f170440214764
COPY server_app.py supervisor.js hub_jobs.py hub_worker.py hub_workspace.js hub_chat.py /app/
