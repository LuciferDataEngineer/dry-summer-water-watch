"""Temporal worker for the storage ingest task queue."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from waterwatch.temporal_activities import write_storage_slice
from waterwatch.temporal_workflows import IngestAllWorkflow, StorageIngestWorkflow


async def serve() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = None
    last_error: Exception | None = None
    for _ in range(60):
        try:
            client = await Client.connect(address)
            break
        except Exception as exc:  # connection refused while the dev server starts
            last_error = exc
            await asyncio.sleep(1)
    if client is None:
        raise RuntimeError(f"could not reach Temporal at {address}") from last_error
    worker = Worker(
        client,
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "water-ingest"),
        workflows=[IngestAllWorkflow, StorageIngestWorkflow],
        activities=[write_storage_slice],
        activity_executor=ThreadPoolExecutor(max_workers=int(os.environ.get("WATERWATCH_MAX_ACTIVITIES", "4"))),
        max_concurrent_activities=int(os.environ.get("WATERWATCH_MAX_ACTIVITIES", "4")),
    )
    await worker.run()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
