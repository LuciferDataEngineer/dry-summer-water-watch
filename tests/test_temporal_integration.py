"""Durable ingest: retries, idempotent writes, and resume after the worker dies."""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.temporal]

ROOT = Path(__file__).resolve().parents[1]


def _wait_port(timeout: float = 30) -> None:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 7233), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("Temporal dev server did not open port 7233")


def _worker(env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "waterwatch.temporal_worker"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _prepare(inbox: Path, bronze: Path, storage_ids: list[str]) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    bronze.mkdir(parents=True, exist_ok=True)
    for storage_id in storage_ids:
        (inbox / f"{storage_id}.json").write_text(
            json.dumps({"storage_id": storage_id, "rows": [{"volume_ml": 1}, {"volume_ml": 2}]}),
            encoding="utf-8",
        )


@pytest.fixture(scope="module")
def temporal_server():
    binary = os.environ.get("TEMPORAL_BIN", "temporal")
    process = subprocess.Popen(
        [binary, "server", "start-dev", "--ip", "127.0.0.1", "--port", "7233", "--db-filename", "/tmp/waterwatch-temporal.db"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_port()
        yield process
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_retry_then_idempotent_skip(temporal_server, tmp_path):
    del temporal_server
    inbox = tmp_path / "inbox"
    bronze = tmp_path / "bronze"
    storage_ids = ["retry-storage"]
    _prepare(inbox, bronze, storage_ids)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "WATERWATCH_INBOX": str(inbox),
            "WATERWATCH_BRONZE": str(bronze),
            "WATERWATCH_FAIL_FIRST": "1",
            "TEMPORAL_ADDRESS": "127.0.0.1:7233",
        }
    )
    worker = _worker(env)
    try:
        from temporalio.client import Client

        async def go():
            client = await Client.connect("127.0.0.1:7233")
            return await client.execute_workflow(
                "IngestAllWorkflow",
                storage_ids,
                id=f"retry-{uuid.uuid4().hex[:8]}",
                task_queue="water-ingest",
                execution_timeout=timedelta(seconds=45),
            )

        import asyncio

        result = asyncio.run(go())
        assert result[0]["status"] == "written"
        assert (bronze / "retry-storage.attempts").read_text(encoding="utf-8") == "2"
        again = asyncio.run(go())
        assert again[0]["status"] == "skipped"
    finally:
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=15)


def test_resume_after_worker_crash(temporal_server, tmp_path):
    del temporal_server
    inbox = tmp_path / "inbox"
    bronze = tmp_path / "bronze"
    storage_ids = [f"crash-{index}" for index in range(4)]
    _prepare(inbox, bronze, storage_ids)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "WATERWATCH_INBOX": str(inbox),
            "WATERWATCH_BRONZE": str(bronze),
            "WATERWATCH_ACTIVITY_SLEEP": "1.2",
            "TEMPORAL_ADDRESS": "127.0.0.1:7233",
            "WATERWATCH_MAX_ACTIVITIES": "1",
        }
    )
    worker = _worker(env)
    workflow_id = f"crash-{uuid.uuid4().hex[:8]}"

    async def start():
        from temporalio.client import Client

        client = await Client.connect("127.0.0.1:7233")
        handle = await client.start_workflow(
            "IngestAllWorkflow",
            storage_ids,
            id=workflow_id,
            task_queue="water-ingest",
            execution_timeout=timedelta(seconds=90),
        )
        return handle

    async def finish():
        from temporalio.client import Client

        client = await Client.connect("127.0.0.1:7233")
        handle = client.get_workflow_handle(workflow_id)
        return await handle.result()

    import asyncio

    try:
        asyncio.run(start())
        deadline = time.time() + 20
        while time.time() < deadline and not any(bronze.glob("crash-*.json")):
            time.sleep(0.2)
        worker.kill()
        worker.wait(timeout=10)
        replacement = _worker(env)
        try:
            result = asyncio.run(asyncio.wait_for(finish(), timeout=60))
        finally:
            replacement.send_signal(signal.SIGTERM)
            replacement.wait(timeout=15)
        assert sorted(item["storage_id"] for item in result) == storage_ids
        for storage_id in storage_ids:
            assert (bronze / f"{storage_id}.json").exists()
    finally:
        if worker.poll() is None:
            worker.kill()
