"""Slow worker used only to prove the autoscaler sees a task-queue backlog.

One activity at a time, held open with heartbeats, so a burst of workflows
sits on the water-ingest queue until more copies of this process start.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker


@activity.defn
def hold_open(seconds: int) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        activity.heartbeat("holding")
        time.sleep(5)
    return "done"


@workflow.defn
class HoldWorkflow:
    @workflow.run
    async def run(self, seconds: int) -> str:
        return await workflow.execute_activity(
            hold_open,
            seconds,
            start_to_close_timeout=timedelta(seconds=seconds + 60),
            heartbeat_timeout=timedelta(seconds=30),
        )


async def main() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
    client = None
    for _ in range(90):
        try:
            client = await Client.connect(address)
            break
        except Exception:
            await asyncio.sleep(2)
    if client is None:
        raise RuntimeError(f"could not reach Temporal at {address}")
    worker = Worker(
        client,
        task_queue="water-ingest",
        workflows=[HoldWorkflow],
        activities=[hold_open],
        max_concurrent_activities=1,
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
