"""Idempotent per-storage writes. Safe to retry after a worker crash."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from temporalio import activity


def _bronze_dir() -> Path:
    path = Path(os.environ.get("WATERWATCH_BRONZE", "outputs/bronze"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _inbox_dir() -> Path:
    return Path(os.environ.get("WATERWATCH_INBOX", "outputs/inbox"))


@activity.defn(name="write_storage_slice")
def write_storage_slice(storage_id: str) -> dict:
    """Copy one storage slice into bronze. A second run with the same bytes is a no-op."""
    activity.heartbeat({"storage_id": storage_id, "phase": "read"})
    source = _inbox_dir() / f"{storage_id}.json"
    if not source.exists():
        raise FileNotFoundError(f"missing slice {storage_id}")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    target = _bronze_dir() / f"{storage_id}.json"
    stamp = _bronze_dir() / f"{storage_id}.stamp"
    if target.exists() and stamp.exists():
        previous = json.loads(stamp.read_text(encoding="utf-8"))
        if previous.get("sha256") == digest:
            return {"storage_id": storage_id, "rows": previous.get("rows", 0), "status": "skipped"}
    # Optional first-attempt failure, used to prove the retry policy.
    if os.environ.get("WATERWATCH_FAIL_FIRST") == "1":
        marker = _bronze_dir() / f"{storage_id}.attempts"
        attempts = int(marker.read_text(encoding="utf-8")) if marker.exists() else 0
        attempts += 1
        marker.write_text(str(attempts), encoding="utf-8")
        if attempts < 2:
            raise RuntimeError(f"transient failure for {storage_id}")
    sleep_s = float(os.environ.get("WATERWATCH_ACTIVITY_SLEEP", "0"))
    waited = 0.0
    while waited < sleep_s:
        activity.heartbeat({"storage_id": storage_id, "phase": "write", "waited": waited})
        step = min(0.4, sleep_s - waited)
        time.sleep(step)
        waited += step
    document = json.loads(payload)
    rows = len(document.get("rows", []))
    temporary = target.with_suffix(".json.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, target)
    stamp.write_text(json.dumps({"sha256": digest, "rows": rows}), encoding="utf-8")
    activity.heartbeat({"storage_id": storage_id, "phase": "done"})
    return {"storage_id": storage_id, "rows": rows, "status": "written"}
