"""Parent workflow fans out one child workflow per storage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from waterwatch.temporal_activities import write_storage_slice


@workflow.defn
class StorageIngestWorkflow:
    @workflow.run
    async def run(self, storage_id: str) -> dict:
        return await workflow.execute_activity(
            write_storage_slice,
            storage_id,
            start_to_close_timeout=timedelta(minutes=2),
            heartbeat_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=8),
                maximum_attempts=5,
            ),
        )


@workflow.defn
class IngestAllWorkflow:
    @workflow.run
    async def run(self, storage_ids: list[str]) -> list[dict]:
        parent = workflow.info().workflow_id
        handles = []
        for storage_id in storage_ids:
            handles.append(
                await workflow.start_child_workflow(
                    StorageIngestWorkflow.run,
                    storage_id,
                    id=f"{parent}-{storage_id}",
                )
            )
        results = []
        for handle in handles:
            results.append(await handle)
        return results
