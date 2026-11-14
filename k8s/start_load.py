"""Queue a burst of slow workflows so the autoscaler has a backlog to see."""

from __future__ import annotations

import asyncio
import uuid

from scale_worker import HoldWorkflow
from temporalio.client import Client


async def main() -> None:
    client = await Client.connect("localhost:7233")
    run = uuid.uuid4().hex[:8]
    for index in range(9):
        await client.start_workflow(
            HoldWorkflow.run,
            75,
            id=f"hold-{run}-{index}",
            task_queue="water-ingest",
        )
    print(f"queued 9 workflows run={run}")


if __name__ == "__main__":
    asyncio.run(main())
