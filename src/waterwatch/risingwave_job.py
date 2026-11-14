"""Replay storage readings into RisingWave and time that against a full DuckDB rebuild.

The materialized view carries the 30-day and 90-day change, a status band, and
the days to the 40% line on the 30-day pace. The batch query in DuckDB is the
same definition.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import duckdb
import psycopg

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
OUTPUTS = ROOT / "outputs"

DRAWDOWN_SQL = """
SELECT
    a.storage_id,
    a.obs_date,
    a.volume_ml,
    a.capacity_ml,
    a.volume_ml - b.volume_ml AS delta_30d_ml,
    a.volume_ml - c.volume_ml AS delta_90d_ml,
    CASE
        WHEN a.volume_ml < 0.25 * a.capacity_ml THEN 'critical'
        WHEN a.volume_ml < 0.40 * a.capacity_ml THEN 'low'
        WHEN a.volume_ml < 0.60 * a.capacity_ml THEN 'watch'
        ELSE 'comfortable'
    END AS status_band,
    CASE
        WHEN a.volume_ml <= 0.40 * a.capacity_ml THEN 0
        WHEN b.volume_ml IS NULL THEN NULL
        WHEN (a.volume_ml - b.volume_ml) / 30.0 >= -1 THEN NULL
        ELSE (a.volume_ml - 0.40 * a.capacity_ml) / (-(a.volume_ml - b.volume_ml) / 30.0)
    END AS days_to_low_on_30d_pace
FROM storage_daily a
LEFT JOIN storage_daily b
    ON a.storage_id = b.storage_id
   AND b.obs_date = a.obs_date - INTERVAL '30 days'
LEFT JOIN storage_daily c
    ON a.storage_id = c.storage_id
   AND c.obs_date = a.obs_date - INTERVAL '90 days'
"""


def _sample_rows(limit_storages: int = 12) -> tuple[list[tuple], list[tuple], str]:
    connection = duckdb.connect()
    ids = [
        row[0]
        for row in connection.execute(
            f"""
            SELECT storage_id
            FROM read_parquet('{(SNAPSHOT / "daily.parquet").as_posix()}')
            GROUP BY 1
            ORDER BY COUNT(*) DESC, 1
            LIMIT {int(limit_storages)}
            """
        ).fetchall()
    ]
    quoted = ", ".join("'" + storage_id.replace("'", "''") + "'" for storage_id in ids)
    frame = connection.execute(
        f"""
        SELECT storage_id, CAST(obs_date AS DATE), volume_ml, capacity_ml
        FROM read_parquet('{(SNAPSHOT / "daily.parquet").as_posix()}')
        WHERE storage_id IN ({quoted})
          AND volume_ml IS NOT NULL
        ORDER BY storage_id, obs_date
        """
    ).fetchall()
    # Hold the last calendar day back so the incremental insert has something to add.
    latest = max(row[1] for row in frame)
    base = [row for row in frame if row[1] < latest]
    extra = [row for row in frame if row[1] == latest]
    return base, extra, str(latest)


def _docker_sample(container: str) -> tuple[float | None, float | None]:
    try:
        raw = subprocess.check_output(
            ["docker", "stats", container, "--no-stream", "--format", "{{.CPUPerc}} {{.MemUsage}}"],
            text=True,
            timeout=15,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None, None
    parts = raw.split()
    cpu = float(parts[0].replace("%", "")) if parts else None
    mem = parts[1] if len(parts) > 1 else None
    mem_mb = None
    if mem and mem.lower().endswith("gib"):
        mem_mb = float(mem[:-3]) * 1024
    elif mem and mem.lower().endswith("mib"):
        mem_mb = float(mem[:-3])
    return cpu, mem_mb


def run_benchmark(dsn: str | None = None, container: str = "risingwave") -> dict:
    dsn = dsn or os.environ.get("RISINGWAVE_DSN", "host=localhost port=4566 user=root dbname=dev")
    base, extra, latest = _sample_rows()
    if not base or not extra:
        raise RuntimeError("snapshot did not yield a hold-out day")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("DROP MATERIALIZED VIEW IF EXISTS mv_drawdown")
        connection.execute("DROP TABLE IF EXISTS storage_daily")
        connection.execute(
            """
            CREATE TABLE storage_daily (
                storage_id VARCHAR,
                obs_date DATE,
                volume_ml DOUBLE PRECISION,
                capacity_ml DOUBLE PRECISION,
                PRIMARY KEY (storage_id, obs_date)
            )
            """
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO storage_daily (storage_id, obs_date, volume_ml, capacity_ml) VALUES (%s, %s, %s, %s)",
                base,
            )
        started = time.perf_counter()
        connection.execute(f"CREATE MATERIALIZED VIEW mv_drawdown AS {DRAWDOWN_SQL}")
        _wait_for_rows(connection, len(base))
        create_seconds = time.perf_counter() - started
        cpu, memory = _docker_sample(container)
        started = time.perf_counter()
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO storage_daily (storage_id, obs_date, volume_ml, capacity_ml) VALUES (%s, %s, %s, %s)",
                extra,
            )
        _wait_for_rows(connection, len(base) + len(extra))
        incremental_seconds = time.perf_counter() - started
        streamed = connection.execute(
            "SELECT storage_id, obs_date::text, days_to_low_on_30d_pace, status_band FROM mv_drawdown WHERE obs_date = %s",
            (latest,),
        ).fetchall()

    duck = duckdb.connect()
    duck.execute(
        """
        CREATE TABLE storage_daily (
            storage_id VARCHAR,
            obs_date DATE,
            volume_ml DOUBLE,
            capacity_ml DOUBLE
        )
        """
    )
    duck.executemany("INSERT INTO storage_daily VALUES (?, ?, ?, ?)", base + extra)
    started = time.perf_counter()
    batched = duck.execute(
        DRAWDOWN_SQL.replace("INTERVAL '30 days'", "INTERVAL 30 DAY").replace("INTERVAL '90 days'", "INTERVAL 90 DAY")
        + " WHERE a.obs_date = CAST(? AS DATE)",
        [latest],
    ).fetchall()
    batch_seconds = time.perf_counter() - started

    mismatches = _compare(streamed, batched)
    result = {
        "rows_loaded": len(base) + len(extra),
        "storages": len({row[0] for row in base}),
        "holdout_day": latest,
        "risingwave_materialize_seconds": round(create_seconds, 3),
        "risingwave_incremental_seconds": round(incremental_seconds, 3),
        "duckdb_full_recompute_seconds": round(batch_seconds, 3),
        "risingwave_cpu_percent": cpu,
        "risingwave_memory_mib": None if memory is None else round(memory, 1),
        "compared_rows": len(streamed),
        "mismatches": mismatches,
    }
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "benchmark.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if mismatches:
        raise AssertionError(f"streaming and batch results differ on {mismatches} rows")
    return result


def _wait_for_rows(connection, expected: int, timeout: float = 120) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        count = connection.execute("SELECT COUNT(*) FROM mv_drawdown").fetchone()[0]
        if count >= expected:
            return
        time.sleep(0.5)
    raise TimeoutError(f"materialized view did not reach {expected} rows")


def _compare(streamed, batched) -> int:
    def key(row):
        return (row[0], str(row[1])[:10])

    left = {key(row): row for row in streamed}
    right = {key(row): row for row in batched}
    mismatches = 0
    for item, row in left.items():
        other = right.get(item)
        if other is None:
            mismatches += 1
            continue
        if row[3] != other[3]:
            mismatches += 1
            continue
        a, b = row[2], other[2]
        if a is None or b is None:
            if a is not None or b is not None:
                mismatches += 1
        elif abs(float(a) - float(b)) > 0.05:
            mismatches += 1
    if len(left) != len(right):
        mismatches += abs(len(left) - len(right))
    return mismatches


def main() -> None:
    print(json.dumps(run_benchmark(), indent=2))


if __name__ == "__main__":
    main()
