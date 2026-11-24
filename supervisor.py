"""Durable, user-scoped sample workflows for the production dashboard."""
import argparse
import hashlib
import json
import logging
import os
import uuid
import threading
from pathlib import Path

import assessment_hub
import deployment_plan

MODES = {"one": ["assessment"], "two": ["deployment"], "both": ["assessment", "deployment"]}
WORK = {"assessment": assessment_hub.sample, "deployment": deployment_plan.sample}
ROOT = Path(os.environ.get("CCC_SUPERVISOR_STATE", "/state/supervisor-v1"))


def save(path, value):
    temporary = path.with_name("." + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def scope(user):
    if not user:
        raise ValueError("Sign in first.")
    return ROOT / hashlib.sha256(user.strip().casefold().encode()).hexdigest()


def jobpath(user, key):
    return scope(user) / uuid.UUID(str(key)).hex


def record(path):
    spec = read(path / "request.json")
    results = {step: read(path / (step + ".json")) for step in MODES[spec["mode"]]
               if (path / (step + ".json")).is_file()}
    status = "Queued"
    if len(results) == len(MODES[spec["mode"]]):
        status = "Needs deployment approval" if "deployment" in results else "Completed"
    elif (path / "cancelled").exists():
        status = "Cancelled"
    elif (path / "paused").exists():
        status = "Paused"
    elif (path / "failed").exists():
        status = "Failed"
    elif results:
        status = "In progress"
    return dict(id=path.name, mode=spec["mode"], status=status, results=results)


def dispatch(user, body):
    action = body.get("action")
    if action == "list":
        jobs = sorted(scope(user).glob("*/request.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return {"jobs": [record(p.parent) for p in jobs]}
    path = jobpath(user, body.get("id"))
    if action == "start":
        mode = body.get("mode")
        if mode not in MODES:
            raise ValueError("Select a prompt.")
        path.mkdir(parents=True, exist_ok=True)
        # Immutable request file, published atomically under the mode-specific directory lock.
        marker = path / "intent"
        try:
            marker.mkdir()
        except FileExistsError:
            if not (path / "request.json").exists():
                raise ValueError("Submission interrupted. Start a new job.")
            if read(path / "request.json")["mode"] != mode:
                raise ValueError("This job already has a different plan.")
        else:
            save(path / "request.json", {"mode": mode})
    elif not (path / "request.json").is_file():
        raise ValueError("Job unavailable.")
    elif action == "pause":
        (path / "paused").touch()
    elif action == "resume":
        (path / "paused").unlink(missing_ok=True)
    elif action == "cancel":
        (path / "cancelled").touch()
    elif action != "status":
        raise ValueError("Unknown action.")
    return record(path)


def process():
    for request in ROOT.glob("*/*/request.json"):
        path = request.parent
        for step in MODES[read(request)["mode"]]:
            if any((path / flag).exists() for flag in ("cancelled", "paused", "failed")):
                break
            output = path / (step + ".json")
            if output.exists():
                continue
            attempts = path / (step + ".attempts")
            count = read(attempts) if attempts.exists() else 0
            if count >= 3:
                (path / "failed").touch()
                break
            save(attempts, count + 1)
            try:
                save(output, WORK[step]())
            except Exception:
                logging.exception("Supervisor sample step failed: %s", step)
                break


_wake = threading.Event()


def wake():
    _wake.set()


def start_worker():
    if os.environ.get("CCC_SUPERVISOR_EXTERNAL") == "1":
        return
    def serve():
        while True:
            _wake.clear()
            try:
                process()
            except Exception:
                logging.exception("Supervisor worker interrupted")
            _wake.wait(timeout=30)
    threading.Thread(target=serve, name="ccc-supervisor", daemon=True).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", required=True, action="store_true")
    parser.parse_args()
    if not ROOT.is_dir():
        raise SystemExit("Supervisor storage mount unavailable")
    process()
    print("Supervisor checkpoint scan completed", flush=True)

