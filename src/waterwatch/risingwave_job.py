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
from datetime import date, datetime
from pathlib import Path

import duckdb
import psycopg

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
OUTPUTS = ROOT / "outputs"

# Calendar-day offset, not an interval cast. `date - integer` is a date in both
# engines, so the 30-day and 90-day lookups land on the same day.
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
        ELSE (a.volume_ml - 0.40 * a.capacity_ml)
             / (-(a.volume_ml - b.volume_ml) / 30.0)
    END AS days_to_low_on_30d_pace
FROM storage_daily a
LEFT JOIN storage_daily b
    ON a.storage_id = b.storage_id
   AND b.obs_date = a.obs_date - 30
LEFT JOIN storage_daily c
    ON a.storage_id = c.storage_id
   AND c.obs_date = a.obs_date - 90
"""

# Both engines emit the same expression. Results are rounded to 6 decimal
# days before they are compared, which is about a tenth of a second.
DAY_DECIMALS = 6


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _sample_rows(limit_storages: int = 12) -> tuple[list[tuple], list[tuple], date]:
    connection = duckdb.connect()
    ids = [
        row[0]
        for row in connection.execute(
            f"""
            SELECT storage_id
            FROM read_parquet('{(SNAPSHOT / "daily.parquet").as_posix()}')
            GROUP BY 1
            HAVING COUNT(DISTINCT CAST(obs_date AS DATE)) > 120
            ORDER BY COUNT(*) DESC, 1
            LIMIT {int(limit_storages)}
            """
        ).fetchall()
    ]
    if not ids:
        raise RuntimeError("snapshot has no storage with enough daily history to hold a day out")
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
    normalised = [(_row[0], _as_date(_row[1]), float(_row[2]), float(_row[3])) for _row in frame]
    # The hold-out day is the latest date shared by every selected storage, so
    # the incremental insert is one row per storage and both engines see it.
    by_storage: dict[str, set[date]] = {}
    for storage_id, obs_date, _volume, _capacity in normalised:
        by_storage.setdefault(storage_id, set()).add(obs_date)
    shared = set.intersection(*by_storage.values()) if by_storage else set()
    if not shared:
        raise RuntimeError("snapshot did not yield a hold-out day shared by the selected storages")
    latest = max(shared)
    base = [row for row in normalised if row[1] < latest]
    extra = [row for row in normalised if row[1] == latest]
    if len(extra) != len(ids) or not base:
        raise RuntimeError(
            f"snapshot did not yield a hold-out day (storages={len(ids)}, holdout_rows={len(extra)}, earlier_rows={len(base)})"
        )
    return base, extra, latest


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
        _wait_until_caught_up(connection, len(base), None, 0)
        create_seconds = time.perf_counter() - started
        cpu, memory = _docker_sample(container)
        started = time.perf_counter()
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO storage_daily (storage_id, obs_date, volume_ml, capacity_ml) VALUES (%s, %s, %s, %s)",
                extra,
            )
        _wait_until_caught_up(connection, len(base) + len(extra), latest, len(extra))
        incremental_seconds = time.perf_counter() - started
        streamed = connection.execute(
            """
            SELECT storage_id, CAST(obs_date AS VARCHAR), days_to_low_on_30d_pace, status_band
            FROM mv_drawdown
            WHERE obs_date = %s
            ORDER BY storage_id
            """,
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
        f"""
        SELECT storage_id, CAST(obs_date AS VARCHAR), days_to_low_on_30d_pace, status_band
        FROM ({DRAWDOWN_SQL.strip()}) q
        WHERE obs_date = ?
        ORDER BY storage_id
        """,
        [latest],
    ).fetchall()
    batch_seconds = time.perf_counter() - started

    mismatches, samples = _compare(streamed, batched)
    result = {
        "rows_loaded": len(base) + len(extra),
        "storages": len({row[0] for row in base}),
        "holdout_day": latest.isoformat(),
        "risingwave_materialize_seconds": round(create_seconds, 3),
        "risingwave_incremental_seconds": round(incremental_seconds, 3),
        "duckdb_full_recompute_seconds": round(batch_seconds, 3),
        "risingwave_cpu_percent": cpu,
        "risingwave_memory_mib": None if memory is None else round(memory, 1),
        "compared_rows": len(streamed),
        "mismatches": mismatches,
        "mismatch_samples": samples,
    }
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / "benchmark.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if mismatches:
        raise AssertionError(f"streaming and batch results differ on {mismatches} rows: {samples}")
    return result


def _flush(connection) -> None:
    connection.execute("FLUSH")


def _wait_until_caught_up(connection, expected_rows: int, holdout: date | None, holdout_rows: int, timeout: float = 180) -> None:
    """Block until the view has the source row count and, when asked, the hold-out day."""
    _flush(connection)
    deadline = time.perf_counter() + timeout
    stable = 0
    previous: tuple[int, int] | None = None
    while time.perf_counter() < deadline:
        total = connection.execute("SELECT COUNT(*) FROM mv_drawdown").fetchone()[0]
        if holdout is None:
            found = 0
        else:
            found = connection.execute(
                "SELECT COUNT(*) FROM mv_drawdown WHERE obs_date = %s",
                (holdout,),
            ).fetchone()[0]
        signature = (int(total), int(found))
        ready = signature[0] == expected_rows and (holdout is None or signature[1] == holdout_rows)
        if ready and signature == previous:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 1 if ready else 0
        previous = signature
        time.sleep(0.4)
        _flush(connection)
    raise TimeoutError(
        f"materialized view did not settle at {expected_rows} rows"
        + ("" if holdout is None else f" with {holdout_rows} on {holdout}")
        + f"; last count was {previous}"
    )


def _round_days(value: object) -> float | None:
    if value is None:
        return None
    return round(float(value), DAY_DECIMALS)


def _compare(streamed, batched) -> tuple[int, list[str]]:
    def key(row):
        return (row[0], str(row[1])[:10])

    left = {key(row): row for row in streamed}
    right = {key(row): row for row in batched}
    mismatches = 0
    samples: list[str] = []

    def note(message: str) -> None:
        if len(samples) < 5:
            samples.append(message)

    for item in sorted(set(left) | set(right)):
        row = left.get(item)
        other = right.get(item)
        if row is None or other is None:
            mismatches += 1
            note(f"missing {item}: stream={row is not None} batch={other is not None}")
            continue
        if row[3] != other[3]:
            mismatches += 1
            note(f"{item} status stream={row[3]} batch={other[3]}")
            continue
        stream_days = _round_days(row[2])
        batch_days = _round_days(other[2])
        if stream_days is None or batch_days is None:
            if stream_days is not None or batch_days is not None:
                mismatches += 1
                note(f"{item} days stream={stream_days} batch={batch_days}")
            continue
        if stream_days != batch_days:
            mismatches += 1
            note(f"{item} days stream={stream_days} batch={batch_days}")
    return mismatches, samples


def main() -> None:
    print(json.dumps(run_benchmark(), indent=2))


if __name__ == "__main__":
    main()
