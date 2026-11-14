"""Plain SQL checks against the gold database. A failing check stops the build."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_manifest(root: Path) -> None:
    manifest = json.loads((root / "data" / "snapshot" / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = root / item["path"]
        if not path.exists():
            raise AssertionError(f"missing snapshot file {item['path']}")
        actual = _sha256(path)
        if actual != item["sha256"]:
            raise AssertionError(f"hash mismatch for {item['path']}")


def run_checks(connection: duckdb.DuckDBPyConnection) -> list[dict]:
    checks = [
        ("daily_rows", "SELECT COUNT(*) FROM silver_daily", lambda n: n > 1000),
        ("no_negative_volume", "SELECT COUNT(*) FROM silver_daily WHERE volume_ml < 0", lambda n: n == 0),
        ("capacity_positive", "SELECT COUNT(*) FROM silver_daily WHERE capacity_ml <= 0", lambda n: n == 0),
        (
            "no_duplicate_days",
            """
            SELECT COUNT(*) FROM (
                SELECT storage_id, obs_date
                FROM silver_daily
                GROUP BY 1, 2
                HAVING COUNT(*) > 1
            )
            """,
            lambda n: n == 0,
        ),
        (
            "percent_in_range",
            "SELECT COUNT(*) FROM silver_sites WHERE percent_full < -1 OR percent_full > 200",
            lambda n: n == 0,
        ),
        ("sites_present", "SELECT COUNT(*) FROM silver_sites", lambda n: n >= 200),
        ("cities_present", "SELECT COUNT(*) FROM bronze_cities", lambda n: n >= 6),
        ("states_present", "SELECT COUNT(*) FROM bronze_states", lambda n: n >= 8),
        (
            "tracked_have_history",
            """
            SELECT COUNT(*) FROM silver_sites s
            WHERE s.tracked
              AND NOT EXISTS (SELECT 1 FROM silver_daily d WHERE d.storage_id = s.storage_id)
            """,
            lambda n: n == 0,
        ),
        (
            "outlook_covers_tracked",
            """
            SELECT COUNT(*) FROM silver_sites s
            WHERE s.tracked
              AND NOT EXISTS (SELECT 1 FROM gold_outlook o WHERE o.storage_id = s.storage_id)
            """,
            lambda n: n == 0,
        ),
        (
            "status_known",
            "SELECT COUNT(*) FROM gold_outlook WHERE status NOT IN ('critical', 'low', 'watch', 'comfortable')",
            lambda n: n == 0,
        ),
    ]
    results = []
    for name, sql, expect in checks:
        value = connection.execute(sql).fetchone()[0]
        ok = bool(expect(value))
        results.append({"name": name, "value": value, "ok": ok})
        if not ok:
            raise AssertionError(f"quality check failed: {name} = {value}")
    return results
