"""Bronze, silver and gold tables, then the stakeholder pages.

The snapshot is the only input. Nothing here calls the network.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from waterwatch.accounts import load_water_account, state_levers
from waterwatch.briefing import (
    headline,
    lever_rows,
    lever_takeaway,
    outlook_kpis,
    ranked,
    scenario_takeaway,
    tight_takeaway,
)
from waterwatch.config import SUMMER_END
from waterwatch.quality import check_manifest, run_checks
from waterwatch.scenarios import build_storage_outlook, daily_changes, parse_date, trajectory, water_balance

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
OUTPUTS = ROOT / "outputs"


def _connect() -> duckdb.DuckDBPyConnection:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(OUTPUTS / "waterwatch.duckdb"))


def _load_bronze(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE bronze_sites AS SELECT * FROM read_parquet('{SNAPSHOT / "sites.parquet"}');
        CREATE OR REPLACE TABLE bronze_daily AS SELECT * FROM read_parquet('{SNAPSHOT / "daily.parquet"}');
        CREATE OR REPLACE TABLE bronze_states AS SELECT * FROM read_parquet('{SNAPSHOT / "states.parquet"}');
        CREATE OR REPLACE TABLE bronze_drainage AS SELECT * FROM read_parquet('{SNAPSHOT / "drainage.parquet"}');
        CREATE OR REPLACE TABLE bronze_cities AS SELECT * FROM read_parquet('{SNAPSHOT / "cities.parquet"}');
        CREATE OR REPLACE TABLE bronze_weather AS SELECT * FROM read_parquet('{SNAPSHOT / "weather.parquet"}');
        """
    )
    # Polars is the second engine for the same bronze files: a state count cross-check.
    counts = (
        pl.read_parquet(SNAPSHOT / "sites.parquet")
        .group_by("state")
        .len()
        .sort("state")
    )
    duck_counts = connection.execute(
        "SELECT state, COUNT(*) AS len FROM bronze_sites GROUP BY 1 ORDER BY 1"
    ).fetchall()
    polar_counts = [(row[0], row[1]) for row in counts.iter_rows()]
    if duck_counts != polar_counts:
        raise AssertionError(f"state counts differ between engines: {duck_counts} vs {polar_counts}")


def _silver(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE silver_sites AS
        SELECT
            storage_id,
            state,
            place,
            role,
            storage_id IN (SELECT DISTINCT storage_id FROM bronze_daily) AS tracked,
            capacity_ml,
            volume_ml,
            percent_full,
            water_level_m,
            water_depth_m,
            TRY_CAST(NULLIF(obs_date, '') AS DATE) AS obs_date,
            latitude,
            longitude,
            station_no,
            station_name
        FROM bronze_sites
        WHERE capacity_ml > 0
          AND percent_full IS NOT NULL;

        CREATE OR REPLACE TABLE silver_daily AS
        SELECT storage_id, obs_date, volume_ml, capacity_ml,
               volume_ml / capacity_ml * 100 AS percent_full
        FROM (
            SELECT
                storage_id,
                CAST(obs_date AS DATE) AS obs_date,
                volume_ml,
                capacity_ml,
                ROW_NUMBER() OVER (
                    PARTITION BY storage_id, CAST(obs_date AS DATE)
                    ORDER BY volume_ml DESC
                ) AS rn
            FROM bronze_daily
            WHERE volume_ml IS NOT NULL
              AND capacity_ml > 0
              AND obs_date IS NOT NULL
              AND obs_date <> ''
        )
        WHERE rn = 1;

        CREATE OR REPLACE TABLE silver_drawdown AS
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
        FROM silver_daily a
        LEFT JOIN silver_daily b
            ON a.storage_id = b.storage_id
           AND b.obs_date = a.obs_date - INTERVAL 30 DAY
        LEFT JOIN silver_daily c
            ON a.storage_id = c.storage_id
           AND c.obs_date = a.obs_date - INTERVAL 90 DAY;

        CREATE OR REPLACE TABLE silver_weather AS
        SELECT
            storage_id,
            CAST(obs_date AS DATE) AS obs_date,
            rain_mm,
            tmax_c,
            tmin_c,
            et0_mm
        FROM bronze_weather
        WHERE obs_date IS NOT NULL;
        """
    )


def _records(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    cursor = connection.execute(sql)
    columns = [item[0] for item in cursor.description]
    rows = []
    for record in cursor.fetchall():
        row = {}
        for name, value in zip(columns, record, strict=True):
            if hasattr(value, "isoformat") and not isinstance(value, str):
                row[name] = value.isoformat()
            else:
                row[name] = value
        rows.append(row)
    return rows


def _outlooks(connection: duckdb.DuckDBPyConnection) -> list[dict]:
    sites = {row["storage_id"]: row for row in _records(connection, "SELECT * FROM silver_sites WHERE tracked")}
    daily: dict[str, list[tuple[date, float]]] = {}
    for row in _records(connection, "SELECT storage_id, obs_date, volume_ml FROM silver_daily ORDER BY 1, 2"):
        daily.setdefault(row["storage_id"], []).append((parse_date(row["obs_date"]), row["volume_ml"]))
    weather: dict[str, dict[date, tuple[float | None, float | None]]] = {}
    for row in _records(connection, "SELECT storage_id, obs_date, rain_mm, et0_mm FROM silver_weather"):
        weather.setdefault(row["storage_id"], {})[parse_date(row["obs_date"])] = (row["rain_mm"], row["et0_mm"])
    outlooks = []
    for storage_id, site in sites.items():
        observations = daily.get(storage_id, [])
        outlook = build_storage_outlook(observations, site["capacity_ml"], site["percent_full"])
        if not outlook:
            continue
        balance = water_balance(daily_changes(observations), weather.get(storage_id, {}))
        end_dry = outlook.get("end_volume_dry_ml")
        end_typical = outlook.get("end_volume_typical_ml")
        end_wet = outlook.get("end_volume_wet_ml")
        capacity = outlook["capacity_ml"]
        outlook.update(
            {
                "storage_id": storage_id,
                "state": site["state"],
                "place": site["place"],
                "role": site["role"],
                "latitude": site["latitude"],
                "longitude": site["longitude"],
                "official_percent": site["percent_full"],
                "official_volume_ml": site["volume_ml"],
                "official_date": site["obs_date"],
                "end_percent_dry": None if end_dry is None else end_dry / capacity * 100,
                "end_percent_typical": None if end_typical is None else end_typical / capacity * 100,
                "end_percent_wet": None if end_wet is None else end_wet / capacity * 100,
                "balance_usable": bool(balance and balance.get("usable")),
                "balance_r2": None if not balance else balance.get("r2"),
                "balance_dry_ml_per_day": None if not balance else balance.get("dry_ml_per_day"),
            }
        )
        outlooks.append(outlook)
    return outlooks


def _write_outlooks(connection: duckdb.DuckDBPyConnection, outlooks: list[dict]) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE gold_outlook (
            storage_id VARCHAR,
            state VARCHAR,
            place VARCHAR,
            role VARCHAR,
            latitude DOUBLE,
            longitude DOUBLE,
            as_of DATE,
            volume_ml DOUBLE,
            capacity_ml DOUBLE,
            percent_full DOUBLE,
            days_to_christmas INTEGER,
            days_to_summer_end INTEGER,
            recent_ml_per_day DOUBLE,
            dry_ml_per_day DOUBLE,
            typical_ml_per_day DOUBLE,
            wet_ml_per_day DOUBLE,
            summers_used INTEGER,
            days_recent DOUBLE,
            days_dry DOUBLE,
            days_typical DOUBLE,
            days_wet DOUBLE,
            end_volume_dry_ml DOUBLE,
            end_volume_typical_ml DOUBLE,
            end_volume_wet_ml DOUBLE,
            end_percent_dry DOUBLE,
            end_percent_typical DOUBLE,
            end_percent_wet DOUBLE,
            status VARCHAR,
            already_under_line BOOLEAN,
            dry_before_christmas BOOLEAN,
            recent_before_christmas BOOLEAN,
            dry_before_summer_end BOOLEAN,
            history_days INTEGER,
            balance_usable BOOLEAN,
            balance_r2 DOUBLE,
            balance_dry_ml_per_day DOUBLE
        )
        """
    )
    if not outlooks:
        return
    columns = [row[0] for row in connection.execute("DESCRIBE gold_outlook").fetchall()]
    placeholders = ", ".join(["?"] * len(columns))
    values = []
    for outlook in outlooks:
        values.append(tuple(outlook.get(column) for column in columns))
    connection.executemany(f"INSERT INTO gold_outlook VALUES ({placeholders})", values)


def _dedupe_border_storages(outlooks: list[dict]) -> list[dict]:
    """Hume is published under two states. Keep one copy so the gap is not counted twice."""
    kept: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for outlook in outlooks:
        key = (str(outlook.get("place", "")).lower(), round(float(outlook.get("capacity_ml") or 0)))
        if key in seen:
            continue
        seen.add(key)
        kept.append(outlook)
    return kept


def _chronos(connection: duckdb.DuckDBPyConnection, outlooks: list[dict]) -> dict:
    from waterwatch.forecast import attach_chronos

    return attach_chronos(connection, outlooks)


def build_briefing(connection: duckdb.DuckDBPyConnection, outlooks: list[dict], chronos_summary: dict) -> dict:
    outlooks = _dedupe_border_storages(outlooks)
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    states = _records(connection, "SELECT * FROM bronze_states")
    drainage = _records(connection, "SELECT * FROM bronze_drainage")
    cities = _records(connection, "SELECT * FROM bronze_cities")
    accounts = state_levers(load_water_account(SNAPSHOT / "abs_water_account_2024_25.csv"))
    as_of_values = [parse_date(item["as_of"]) for item in outlooks if item.get("as_of")]
    as_of = max(as_of_values) if as_of_values else date(2026, 11, 15)
    days_remaining = (SUMMER_END - as_of).days
    levers = lever_rows(states, outlooks, accounts, days_remaining)
    ordered = ranked(outlooks)
    featured = next(
        (
            item
            for item in ordered
            if item.get("role") == "urban" and (item.get("percent_full") or 0) > 1
        ),
        next((item for item in ordered if item.get("role") != "hydro"), ordered[0] if ordered else None),
    )
    title, lede = headline(outlooks, cities)
    paths = {}
    for outlook in ordered[:8]:
        start = parse_date(outlook["as_of"])
        history_rows = connection.execute(
            """
            SELECT CAST(obs_date AS VARCHAR), volume_ml
            FROM silver_daily
            WHERE storage_id = ? AND obs_date >= CAST(? AS DATE) - INTERVAL 120 DAY
            ORDER BY obs_date
            """,
            [outlook["storage_id"], outlook["as_of"]],
        ).fetchall()
        paths[outlook["storage_id"]] = {
            "history": [{"date": row[0][:10], "volume_ml": row[1]} for row in history_rows],
            "dry": [
                {"date": day.isoformat(), "volume_ml": volume}
                for day, volume in trajectory(start, outlook["volume_ml"], outlook.get("dry_ml_per_day"))
            ],
            "typical": [
                {"date": day.isoformat(), "volume_ml": volume}
                for day, volume in trajectory(start, outlook["volume_ml"], outlook.get("typical_ml_per_day"))
            ],
            "wet": [
                {"date": day.isoformat(), "volume_ml": volume}
                for day, volume in trajectory(start, outlook["volume_ml"], outlook.get("wet_ml_per_day"))
            ],
            "band": chronos_summary.get("bands", {}).get(outlook["storage_id"]),
        }
    usable = [item for item in outlooks if item.get("balance_usable")]
    disagree = 0
    for item in usable:
        balance_days_hit = False
        # A positive dry water-balance rate does not cross the line.
        rate = item.get("balance_dry_ml_per_day")
        if rate is not None and rate < -1 and item["volume_ml"] > 0.4 * item["capacity_ml"]:
            days = (item["volume_ml"] - 0.4 * item["capacity_ml"]) / (-rate)
            balance_days_hit = days <= item["days_to_christmas"]
        if bool(item.get("dry_before_christmas")) != balance_days_hit:
            disagree += 1
    return {
        "snapshot_display": manifest["access_display"],
        "as_of": as_of.isoformat(),
        "as_of_display": as_of.strftime("%-d %B %Y"),
        "headline": title,
        "lede": lede,
        "kpis": outlook_kpis(outlooks, cities, states),
        "states": states,
        "drainage": drainage,
        "cities": cities,
        "storages": ordered,
        "featured_id": None if featured is None else featured["storage_id"],
        "paths": paths,
        "levers": levers,
        "means": {
            "outlook": lede,
            "tight": tight_takeaway(outlooks),
            "scenarios": scenario_takeaway(featured),
            "levers": lever_takeaway(levers),
        },
        "chronos": {key: value for key, value in chronos_summary.items() if key != "bands"},
        "balance": {
            "usable": len(usable),
            "tracked": len(outlooks),
            "disagree_on_christmas": disagree,
        },
        "accounts": accounts,
        "generated_note": "Figures are from the committed public snapshot. Times are Australian Eastern time.",
    }


def _json_ready(value):
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, float):
        return round(value, 4)
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    return value


def run() -> dict:
    check_manifest(ROOT)
    connection = _connect()
    _load_bronze(connection)
    _silver(connection)
    outlooks = _outlooks(connection)
    _write_outlooks(connection, outlooks)
    chronos_summary = _chronos(connection, outlooks)
    checks = run_checks(connection)
    briefing = build_briefing(connection, outlooks, chronos_summary)
    briefing["quality_checks"] = len(checks)
    payload = _json_ready(briefing)
    (OUTPUTS / "briefing.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    from waterwatch.dashboard import render_site
    from waterwatch.insights import write_insights

    render_site(payload, ROOT / "site")
    write_insights(payload, ROOT / "INSIGHTS.md")
    connection.close()
    return payload


def main() -> None:
    payload = run()
    print(
        json.dumps(
            {
                "headline": payload["headline"],
                "storages": len(payload["storages"]),
                "as_of": payload["as_of"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
