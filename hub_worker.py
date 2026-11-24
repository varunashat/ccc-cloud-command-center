"""Run the existing Supervisor and live assessment checkpoints under one lease."""
import fcntl
import os
from pathlib import Path

import app
import hub_jobs
import server_app
import supervisor
from discovery import azure_discovery as azure


def caller_for(owner):
    if owner not in server_app.allowed():
        raise ValueError('Account access has been revoked.')
    return azure.make_caller(lambda tenant: server_app.user_token(owner, tenant), retries=0, timeout=20)


def main():
    state = Path(os.environ.get('CCC_STATE', '/state'))
    # Use the same encrypted sign-in cache as the web application.
    app.ROOT = state
    with (state / 'supervisor-worker.lock').open('a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('An earlier Supervisor execution still holds the lease.', flush=True)
            return
        supervisor.process()
        hub_jobs.process(caller_for)
        print('Supervisor and assessment checkpoints processed.', flush=True)


if __name__ == '__main__':
    main()
