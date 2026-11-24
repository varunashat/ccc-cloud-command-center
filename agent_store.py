"""Chat history and memory across the Environment & Deployment modes (browser agent, laptop agent, planning chat).

Each agent job keeps its on-screen transcript so the page can reopen it like any chat. `memory` hands the model a short list of the
operator's recent tasks and outcomes so "do it like last time" works. Everything is stored per owner on the same Azure Files share.
"""
import re
import threading
import uuid

import hub_jobs as hub
from supervisor import read, save

LOCK = threading.Lock()
KINDS = {'browser': 'browser-jobs', 'laptop': 'laptop-jobs'}
MAX_ENTRIES = 600
MAX_TEXT = 4000


def _path(owner, kind, job_id):
    if kind not in KINDS:
        raise ValueError('Unknown chat kind.')
    return hub.folder(owner) / KINDS[kind] / (uuid.UUID(str(job_id)).hex + '.json')


def note(owner, kind, job_id, entries):
    clean = [{'role': 'user' if e.get('role') == 'user' else 'assistant', 'text': str(e.get('text', ''))[:MAX_TEXT]}
             for e in (entries if isinstance(entries, list) else [])[:200] if isinstance(e, dict) and str(e.get('text', '')).strip()]
    path = _path(owner, kind, job_id)
    with LOCK:
        if not path.exists():
            raise ValueError('Chat unavailable.')
        job = read(path)
        job['transcript'] = (job.get('transcript', []) + clean)[-MAX_ENTRIES:]
        job['updated'] = hub.now()
        save(path, job)
    return {'saved': len(clean)}


def open_chat(owner, kind, job_id):
    path = _path(owner, kind, job_id)
    if not path.exists():
        raise ValueError('Chat unavailable.')
    job = read(path)
    return {'id': job['id'], 'kind': kind, 'task': job.get('task', ''), 'status': job.get('status', ''), 'created': job.get('created', ''),
            'transcript': job.get('transcript', []), 'summary': job.get('summary', ''), 'blocker': job.get('blocker', '')}


def _jobs(owner, kind):
    folder = hub.folder(owner) / KINDS[kind]
    jobs = []
    for path in folder.glob('*.json') if folder.exists() else []:
        try:
            jobs.append(read(path))
        except (OSError, ValueError):
            continue
    return jobs


def history(owner, limit=60):
    rows = [{'kind': kind, 'id': str(uuid.UUID(job['id'])), 'title': re.sub(r'\s+', ' ', job.get('task', ''))[:70], 'status': job.get('status', ''),
             'created': job.get('created', ''), 'updated': job.get('updated', job.get('created', ''))}
            for kind in KINDS for job in _jobs(owner, kind) if job.get('task') and job.get('task') != 'connection check']
    return sorted(rows, key=lambda r: r['updated'], reverse=True)[:limit]


def memory(owner, exclude=None, limit=5):
    """Short text of recent finished or blocked work, for the model prompt."""
    items = []
    for row in history(owner, 40):
        if row['id'].replace('-', '') == str(exclude or '').replace('-', '') or row['status'] not in ('done', 'blocked', 'failed'):
            continue
        job = read(_path(owner, row['kind'], row['id']))
        items.append(f"[{row['created'][:10]} {row['kind']}] {job.get('task', '')[:200]} -> {row['status']}: {(job.get('summary') or job.get('blocker') or '')[:250]}")
        if len(items) >= limit:
            break
    return '\n'.join(items) or 'none'


def dispatch(owner, body):
    action = body.get('action')
    if action == 'list':
        return {'chats': history(owner)}
    if action == 'open':
        return open_chat(owner, body.get('kind'), body.get('id'))
    if action == 'note':
        return note(owner, body.get('kind'), body.get('id'), body.get('entries'))
    raise ValueError('Unknown action.')
