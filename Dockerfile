FROM cccacr1a5492.azurecr.io/ccc@sha256:983e7660ac2659063f3c13d65ade9870faa703947fb15b531c7ad75166c6b29d
COPY server_app.py supervisor.py weather_console.py live_workflow.py deployment_plan.py assessment_hub.py supervisor.js /app/

