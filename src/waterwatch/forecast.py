"""Open time-series foundation model paths, plus two simple baselines.

Chronos-Bolt small runs on CPU. The likely range is the 10th to 90th percentile
it was trained to produce. The hold-out is the last 60 days of each series.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from waterwatch.config import (
    CHRONOS_CONTEXT,
    CHRONOS_HOLDOUT_DAYS,
    CHRONOS_MODEL,
    CHRONOS_QUANTILES,
    MAX_CHRONOS_SERIES,
    SEED,
)
from waterwatch.scenarios import daily_changes, parse_date, recent_rate

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
FORECAST_PATH = SNAPSHOT / "chronos_forecast.parquet"
BACKTEST_PATH = SNAPSHOT / "chronos_backtest.parquet"
HORIZON = 64


def regular_series(observations: list[tuple[date, float]]) -> list[tuple[date, float]]:
    ordered = sorted((day, volume) for day, volume in observations if day is not None)
    if not ordered:
        return []
    points: list[tuple[date, float]] = []
    for (start, start_volume), (end, end_volume) in zip(ordered, ordered[1:], strict=False):
        span = (end - start).days
        if span <= 0:
            continue
        for offset in range(span):
            share = offset / span
            points.append((start + timedelta(days=offset), start_volume + (end_volume - start_volume) * share))
    points.append(ordered[-1])
    return points


def _mae(errors: list[float]) -> float | None:
    if not errors:
        return None
    return sum(errors) / len(errors)


def _baselines(series: list[tuple[date, float]]) -> dict:
    if len(series) <= CHRONOS_HOLDOUT_DAYS + 30:
        return {"mae_seasonal": None, "mae_recent_rate": None, "holdout_days": 0}
    context = series[:-CHRONOS_HOLDOUT_DAYS]
    holdout = series[-CHRONOS_HOLDOUT_DAYS:]
    by_date = {day: volume for day, volume in series}
    rate = recent_rate(daily_changes(context), context[-1][0])
    seasonal_errors = []
    rate_errors = []
    for index, (day, actual) in enumerate(holdout, start=1):
        earlier = by_date.get(day - timedelta(days=365))
        if earlier is not None:
            seasonal_errors.append(abs(earlier - actual))
        if rate is not None:
            rate_errors.append(abs(context[-1][1] + rate * index - actual))
    return {
        "mae_seasonal": _mae(seasonal_errors),
        "mae_recent_rate": _mae(rate_errors),
        "holdout_days": len(holdout),
    }


def _select(outlooks: list[dict], series_by_id: dict[str, list[tuple[date, float]]]) -> list[str]:
    ranked = sorted(
        outlooks,
        key=lambda item: (
            0 if item.get("already_under_line") else 1,
            0 if item.get("dry_before_christmas") else 1,
            item.get("days_dry") if isinstance(item.get("days_dry"), (int, float)) else 10_000,
            item.get("percent_full") or 999,
        ),
    )
    chosen = []
    for outlook in ranked:
        series = series_by_id.get(outlook["storage_id"])
        if series and len(series) >= 400:
            chosen.append(outlook["storage_id"])
        if len(chosen) >= MAX_CHRONOS_SERIES:
            break
    return chosen


def _run_model(chosen: list[str], series_by_id: dict[str, list[tuple[date, float]]]) -> tuple[list[dict], list[dict]]:
    import torch
    from chronos import ChronosBoltPipeline

    torch.manual_seed(SEED)
    torch.set_num_threads(int(os.environ.get("WATERWATCH_THREADS", "2")))
    pipeline = ChronosBoltPipeline.from_pretrained(
        CHRONOS_MODEL,
        device_map="cpu",
        torch_dtype=torch.float32,
    )
    forecast_rows: list[dict] = []
    backtest_rows: list[dict] = []
    for storage_id in chosen:
        series = series_by_id[storage_id]
        values = [volume for _, volume in series]
        context_values = values[-(CHRONOS_CONTEXT + CHRONOS_HOLDOUT_DAYS) : -CHRONOS_HOLDOUT_DAYS]
        holdout = series[-CHRONOS_HOLDOUT_DAYS:]
        context = torch.tensor(context_values, dtype=torch.float32)
        quantiles, _mean = pipeline.predict_quantiles(
            context,
            prediction_length=CHRONOS_HOLDOUT_DAYS,
            quantile_levels=list(CHRONOS_QUANTILES),
            limit_prediction_length=True,
        )
        median = quantiles[0, :, 1].tolist()
        errors = [abs(predicted - actual) for predicted, (_, actual) in zip(median, holdout, strict=False)]
        baselines = _baselines(series)
        backtest_rows.append(
            {
                "storage_id": storage_id,
                "mae_chronos_ml": _mae(errors),
                "mae_seasonal_ml": baselines["mae_seasonal"],
                "mae_recent_rate_ml": baselines["mae_recent_rate"],
                "holdout_days": baselines["holdout_days"],
            }
        )
        future_context = torch.tensor(values[-CHRONOS_CONTEXT:], dtype=torch.float32)
        future, _mean = pipeline.predict_quantiles(
            future_context,
            prediction_length=HORIZON,
            quantile_levels=list(CHRONOS_QUANTILES),
            limit_prediction_length=True,
        )
        start = series[-1][0]
        low = future[0, :, 0].tolist()
        mid = future[0, :, 1].tolist()
        high = future[0, :, 2].tolist()
        for index in range(HORIZON):
            forecast_rows.append(
                {
                    "storage_id": storage_id,
                    "obs_date": (start + timedelta(days=index + 1)).isoformat(),
                    "p10_ml": float(low[index]),
                    "p50_ml": float(mid[index]),
                    "p90_ml": float(high[index]),
                }
            )
    return forecast_rows, backtest_rows


def _write_table(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    arrays = []
    for column in columns:
        values = [row[column] for row in rows]
        if all(isinstance(value, str) for value in values):
            arrays.append(pa.array(values, type=pa.string()))
        elif all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            arrays.append(pa.array(values, type=pa.int64()))
        else:
            arrays.append(pa.array([None if value is None else float(value) for value in values], type=pa.float64()))
    pq.write_table(pa.Table.from_arrays(arrays, names=columns), path, compression="zstd")


def _read_table(path: Path) -> list[dict]:
    if not path.exists():
        return []
    table = pq.read_table(path)
    columns = table.column_names
    rows = []
    for index in range(table.num_rows):
        rows.append({column: table.column(column)[index].as_py() for column in columns})
    return rows


def _update_manifest(paths: list[Path]) -> None:
    manifest_path = SNAPSHOT / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    kept = [item for item in manifest["files"] if Path(item["path"]).name not in {path.name for path in paths}]
    for path in paths:
        if not path.exists():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        kept.append(
            {
                "path": f"data/snapshot/{path.name}",
                "description": (
                    "Chronos-Bolt small likely range for tracked storages, computed from the storage history in this snapshot"
                    if "forecast" in path.name
                    else "Hold-out error of the likely-range midpoint against a same-date-last-year path and a last-month pace"
                ),
                "source_url": "https://huggingface.co/amazon/chronos-bolt-small",
                "licence": "Apache-2.0",
                "sha256": digest,
                "bytes": path.stat().st_size,
                "rows": pq.ParquetFile(path).metadata.num_rows,
            }
        )
    manifest["files"] = kept
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _summary(forecast_rows: list[dict], backtest_rows: list[dict]) -> dict:
    bands: dict[str, list[dict]] = {}
    for row in forecast_rows:
        bands.setdefault(row["storage_id"], []).append(
            {
                "date": str(row["obs_date"])[:10],
                "p10_ml": row["p10_ml"],
                "p50_ml": row["p50_ml"],
                "p90_ml": row["p90_ml"],
            }
        )
    def mean(key: str) -> float | None:
        values = [row[key] for row in backtest_rows if row.get(key) is not None]
        if not values:
            return None
        return sum(values) / len(values)

    beats_seasonal = 0
    beats_recent = 0
    compared_seasonal = 0
    compared_recent = 0
    for row in backtest_rows:
        if row.get("mae_chronos_ml") is None:
            continue
        if row.get("mae_seasonal_ml") is not None:
            compared_seasonal += 1
            if row["mae_chronos_ml"] < row["mae_seasonal_ml"]:
                beats_seasonal += 1
        if row.get("mae_recent_rate_ml") is not None:
            compared_recent += 1
            if row["mae_chronos_ml"] < row["mae_recent_rate_ml"]:
                beats_recent += 1
    return {
        "model": CHRONOS_MODEL,
        "series": len(backtest_rows),
        "horizon_days": HORIZON,
        "holdout_days": CHRONOS_HOLDOUT_DAYS,
        "mae_chronos_ml": mean("mae_chronos_ml"),
        "mae_seasonal_ml": mean("mae_seasonal_ml"),
        "mae_recent_rate_ml": mean("mae_recent_rate_ml"),
        "beats_seasonal": beats_seasonal,
        "compared_seasonal": compared_seasonal,
        "beats_recent_rate": beats_recent,
        "compared_recent": compared_recent,
        "bands": bands,
    }


def _mae_as_percent(backtest_rows: list[dict], outlooks: list[dict]) -> dict:
    capacity = {item["storage_id"]: item.get("capacity_ml") for item in outlooks}
    scores = {"chronos": [], "seasonal": [], "recent": []}
    for row in backtest_rows:
        size = capacity.get(row["storage_id"]) or 0
        if size <= 0:
            continue
        if row.get("mae_chronos_ml") is not None:
            scores["chronos"].append(row["mae_chronos_ml"] / size * 100)
        if row.get("mae_seasonal_ml") is not None:
            scores["seasonal"].append(row["mae_seasonal_ml"] / size * 100)
        if row.get("mae_recent_rate_ml") is not None:
            scores["recent"].append(row["mae_recent_rate_ml"] / size * 100)

    def mean(values: list[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    return {
        "mae_chronos_pct_capacity": mean(scores["chronos"]),
        "mae_seasonal_pct_capacity": mean(scores["seasonal"]),
        "mae_recent_pct_capacity": mean(scores["recent"]),
    }


def load_saved(outlooks: list[dict] | None = None) -> dict:
    backtest = _read_table(BACKTEST_PATH)
    summary = _summary(_read_table(FORECAST_PATH), backtest)
    if outlooks:
        summary.update(_mae_as_percent(backtest, outlooks))
    return summary


def attach_chronos(connection, outlooks: list[dict]) -> dict:
    """connection is accepted so the caller can keep the gold database in one place."""
    del connection
    series_by_id: dict[str, list[tuple[date, float]]] = {}
    # The caller passes outlooks that already know their ids. Series are re-read
    # from the snapshot parquet so this step does not depend on a live database.
    import duckdb

    frame = duckdb.connect().execute(
        f"""
        SELECT storage_id, CAST(obs_date AS VARCHAR), volume_ml
        FROM read_parquet('{(SNAPSHOT / "daily.parquet").as_posix()}')
        WHERE volume_ml IS NOT NULL
        ORDER BY 1, 2
        """
    ).fetchall()
    grouped: dict[str, list[tuple[date, float]]] = {}
    for storage_id, obs_date, volume in frame:
        grouped.setdefault(storage_id, []).append((parse_date(obs_date), float(volume)))
    for storage_id, observations in grouped.items():
        series_by_id[storage_id] = regular_series(observations)

    skip = os.environ.get("WATERWATCH_SKIP_CHRONOS") == "1"
    if skip:
        return load_saved(outlooks)
    try:
        chosen = _select(outlooks, series_by_id)
        forecast_rows, backtest_rows = _run_model(chosen, series_by_id)
    except ImportError:
        return load_saved(outlooks)
    summary = _summary(forecast_rows, backtest_rows)
    summary.update(_mae_as_percent(backtest_rows, outlooks))
    if os.environ.get("WATERWATCH_SAVE_CHRONOS", "0") == "1":
        _write_table(FORECAST_PATH, forecast_rows)
        _write_table(BACKTEST_PATH, backtest_rows)
        _update_manifest([FORECAST_PATH, BACKTEST_PATH])
    elif os.environ.get("WATERWATCH_CHECK_CHRONOS") == "1":
        saved = load_saved(outlooks)
        _compare(summary, saved)
    return summary


def _compare(fresh: dict, saved: dict) -> None:
    if not saved.get("series"):
        raise AssertionError("no saved chronos backtest to compare")
    for key in ("mae_chronos_ml", "mae_seasonal_ml", "mae_recent_rate_ml"):
        new = fresh.get(key)
        old = saved.get(key)
        if new is None or old is None:
            continue
        if old == 0:
            if abs(new) > 1:
                raise AssertionError(f"{key} moved from 0 to {new}")
            continue
        if abs(new - old) / abs(old) > 0.05:
            raise AssertionError(f"{key} changed by more than 5%: saved {old} fresh {new}")


def main() -> None:
    os.environ["WATERWATCH_SAVE_CHRONOS"] = "1"
    # A direct call computes and saves. The pipeline normally does this.
    from waterwatch.pipeline import run

    run()


if __name__ == "__main__":
    main()
