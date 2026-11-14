"""RisingWave materialized view against the same definition as the batch query."""

import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.risingwave]


def test_streaming_matches_batch():
    if os.environ.get("RISINGWAVE_DSN") is None and os.environ.get("RUN_RISINGWAVE") != "1":
        pytest.skip("RisingWave is not started")
    from waterwatch.risingwave_job import run_benchmark

    result = run_benchmark()
    assert result["mismatches"] == 0
    assert result["rows_loaded"] > 100
    assert result["risingwave_incremental_seconds"] >= 0
    assert result["duckdb_full_recompute_seconds"] >= 0
