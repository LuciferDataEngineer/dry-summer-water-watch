"""Days of buffer and the what-if levers.

The dry, typical and wet paths replay the average daily change from real
completed summers. They are scenarios, not a weather forecast. The "last month"
path replays the last 30 observed daily changes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from waterwatch.config import (
    ANALOGUE_SUMMERS,
    CHRISTMAS,
    CRITICAL_LINE,
    LOW_LINE,
    MIN_SUMMER_DAYS,
    RECENT_DAYS,
    RESTRICTION_SAVING,
    SUMMER_END,
    WATCH_LINE,
)


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def daily_changes(observations: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Mean change per day between successive readings, spread across gaps."""
    ordered = sorted((day, volume) for day, volume in observations if day is not None)
    changes: list[tuple[date, float]] = []
    for (previous_day, previous_volume), (day, volume) in zip(ordered, ordered[1:], strict=False):
        span = (day - previous_day).days
        if span <= 0:
            continue
        step = (volume - previous_volume) / span
        for offset in range(span):
            changes.append((previous_day + timedelta(days=offset + 1), step))
    return changes


def summer_rates(changes: list[tuple[date, float]]) -> dict[str, float | None]:
    by_summer: dict[str, list[float]] = defaultdict(list)
    for day, step in changes:
        for start, end in ANALOGUE_SUMMERS:
            if start <= day <= end:
                by_summer[start.isoformat()].append(step)
                break
    means = []
    for values in by_summer.values():
        if len(values) < MIN_SUMMER_DAYS:
            continue
        average = _mean(values)
        if average is not None:
            means.append(average)
    if not means:
        return {"dry_ml_per_day": None, "typical_ml_per_day": None, "wet_ml_per_day": None, "summers_used": 0}
    return {
        "dry_ml_per_day": min(means),
        "typical_ml_per_day": _median(means),
        "wet_ml_per_day": max(means),
        "summers_used": len(means),
    }


def recent_rate(changes: list[tuple[date, float]], as_of: date) -> float | None:
    window = [step for day, step in changes if as_of - timedelta(days=RECENT_DAYS) < day <= as_of]
    if len(window) < 10:
        return None
    return _mean(window)


def days_until_line(volume_ml: float, capacity_ml: float, rate_ml_per_day: float | None, line: float = LOW_LINE) -> float | None:
    """Days until volume falls to the line. None means the storage is not falling toward it."""
    if capacity_ml <= 0 or rate_ml_per_day is None:
        return None
    target = line * capacity_ml
    if volume_ml <= target:
        return 0.0
    if rate_ml_per_day >= -1.0:
        return None
    return (volume_ml - target) / (-rate_ml_per_day)


def project_volume(volume_ml: float, rate_ml_per_day: float | None, days: int) -> float | None:
    if rate_ml_per_day is None:
        return None
    return volume_ml + rate_ml_per_day * days


def status_band(percent_full: float, dry_days: float | None, days_to_summer_end: int) -> str:
    if percent_full < CRITICAL_LINE * 100:
        return "critical"
    if percent_full < LOW_LINE * 100:
        return "low"
    crosses = dry_days is not None and dry_days <= days_to_summer_end
    if percent_full < WATCH_LINE * 100 or crosses:
        return "watch"
    return "comfortable"


def hits_before(days_to_line: float | None, horizon_days: int) -> bool:
    return days_to_line is not None and days_to_line <= horizon_days


def build_storage_outlook(observations: list[tuple[date, float]], capacity_ml: float, official_percent: float | None) -> dict:
    observations = sorted((day, volume) for day, volume in observations if day is not None)
    if not observations or capacity_ml <= 0:
        return {}
    as_of, volume = observations[-1]
    changes = daily_changes(observations)
    rates = summer_rates(changes)
    recent = recent_rate(changes, as_of)
    percent = official_percent if official_percent is not None else (volume / capacity_ml * 100)
    days_to_christmas = (CHRISTMAS - as_of).days
    days_to_summer = (SUMMER_END - as_of).days
    paths = {
        "recent": recent,
        "dry": rates["dry_ml_per_day"],
        "typical": rates["typical_ml_per_day"],
        "wet": rates["wet_ml_per_day"],
    }
    days = {name: days_until_line(volume, capacity_ml, rate) for name, rate in paths.items()}
    end_volume = {name: project_volume(volume, rate, max(days_to_summer, 0)) for name, rate in paths.items()}
    return {
        "as_of": as_of.isoformat(),
        "volume_ml": volume,
        "capacity_ml": capacity_ml,
        "percent_full": percent,
        "days_to_christmas": days_to_christmas,
        "days_to_summer_end": days_to_summer,
        "recent_ml_per_day": recent,
        "dry_ml_per_day": rates["dry_ml_per_day"],
        "typical_ml_per_day": rates["typical_ml_per_day"],
        "wet_ml_per_day": rates["wet_ml_per_day"],
        "summers_used": rates["summers_used"],
        "days_recent": days["recent"],
        "days_dry": days["dry"],
        "days_typical": days["typical"],
        "days_wet": days["wet"],
        "end_volume_dry_ml": end_volume["dry"],
        "end_volume_typical_ml": end_volume["typical"],
        "end_volume_wet_ml": end_volume["wet"],
        "end_volume_recent_ml": end_volume["recent"],
        "status": status_band(percent, days["dry"], max(days_to_summer, 0)),
        "already_under_line": percent < LOW_LINE * 100,
        "already_critical": percent < CRITICAL_LINE * 100,
        "dry_before_christmas": hits_before(days["dry"], max(days_to_christmas, 0)),
        "recent_before_christmas": hits_before(days["recent"], max(days_to_christmas, 0)),
        "dry_before_summer_end": hits_before(days["dry"], max(days_to_summer, 0)),
        "history_days": (observations[-1][0] - observations[0][0]).days,
        "empty_since": _empty_since(observations, capacity_ml),
    }


def _empty_since(observations: list[tuple[date, float]], capacity_ml: float) -> str | None:
    if not observations or observations[-1][1] > 1:
        return None
    last_water = None
    for day, volume in observations:
        if volume > max(1.0, 0.005 * capacity_ml):
            last_water = day
    if last_water is None or last_water == observations[-1][0]:
        return None
    return (last_water + timedelta(days=1)).isoformat()


def gap_ml(end_volume_ml: float | None, capacity_ml: float, line: float = LOW_LINE) -> float:
    """Megalitres needed to finish on the line. A path below empty counts as empty."""
    if end_volume_ml is None or capacity_ml <= 0:
        return 0.0
    return max(0.0, line * capacity_ml - max(end_volume_ml, 0.0))


def lever_volumes(household_ml: float | None, desal_ml: float | None, reuse_ml: float | None, days: int) -> dict[str, float | None]:
    """Scale a full year of supply or use down to the remaining summer days."""
    if days < 0:
        days = 0
    share = days / 365.0

    def scale(value: float | None) -> float | None:
        if value is None:
            return None
        return value * share

    household_summer = scale(household_ml)
    restriction = None if household_summer is None else household_summer * RESTRICTION_SAVING
    return {
        "household_summer_ml": household_summer,
        "restriction_ml": restriction,
        "desal_ml": scale(desal_ml),
        "reuse_ml": scale(reuse_ml),
    }


def share_closed(gap: float, lever: float | None) -> float | None:
    if lever is None or gap <= 0:
        return None
    return min(1.0, lever / gap)


def _solve3(matrix: list[list[float]], target: list[float]) -> list[float] | None:
    size = 3
    aug = [row[:] + [target[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        for column in range(col, size + 1):
            aug[col][column] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = aug[row][col]
            for column in range(col, size + 1):
                aug[row][column] -= factor * aug[col][column]
    return [aug[row][size] for row in range(size)]


def water_balance(changes: list[tuple[date, float]], weather: dict[date, tuple[float | None, float | None]]) -> dict | None:
    """Daily change against rain and evaporation. Used only as a cross-check.

    A fit is kept when rain has a positive link and explains at least 5% of the
    day-to-day change. Releases dominate many storages, so a weak fit is dropped.
    """
    points: list[tuple[float, float, float]] = []
    summer_points: list[tuple[date, float, float, float]] = []
    for day, step in changes:
        pair = weather.get(day)
        if pair is None:
            continue
        rain, et0 = pair
        if rain is None or et0 is None:
            continue
        points.append((step, rain, et0))
        if day.month in {11, 12, 1, 2, 3} and day <= date(2026, 3, 31):
            summer_points.append((day, step, rain, et0))
    if len(points) < 90 or len(summer_points) < 40:
        return None
    xtx = [[0.0, 0.0, 0.0] for _ in range(3)]
    xty = [0.0, 0.0, 0.0]
    for step, rain, et0 in points:
        features = (1.0, rain, et0)
        for row in range(3):
            xty[row] += features[row] * step
            for column in range(3):
                xtx[row][column] += features[row] * features[column]
    coef = _solve3(xtx, xty)
    if coef is None:
        return None
    mean_step = sum(step for step, _, _ in points) / len(points)
    ss_tot = sum((step - mean_step) ** 2 for step, _, _ in points)
    ss_res = sum((step - (coef[0] + coef[1] * rain + coef[2] * et0)) ** 2 for step, rain, et0 in points)
    r2 = None if ss_tot <= 0 else 1 - ss_res / ss_tot
    if r2 is None or r2 < 0.05 or coef[1] <= 0:
        return {"usable": False, "r2": r2, "rain_coef": coef[1], "et0_coef": coef[2], "intercept": coef[0]}
    ordered = sorted(summer_points, key=lambda item: item[2])
    third = max(len(ordered) // 3, 1)
    bands = {
        "dry": ordered[:third],
        "typical": ordered[third : len(ordered) - third],
        "wet": ordered[len(ordered) - third :],
    }
    rates = {}
    for name, group in bands.items():
        rain = sum(item[2] for item in group) / len(group)
        et0 = sum(item[3] for item in group) / len(group)
        rates[name] = coef[0] + coef[1] * rain + coef[2] * et0
    return {
        "usable": True,
        "r2": r2,
        "rain_coef": coef[1],
        "et0_coef": coef[2],
        "intercept": coef[0],
        "dry_ml_per_day": rates["dry"],
        "typical_ml_per_day": rates["typical"],
        "wet_ml_per_day": rates["wet"],
    }


def trajectory(as_of: date, volume_ml: float, rate_ml_per_day: float | None, end: date = SUMMER_END) -> list[tuple[date, float]]:
    if rate_ml_per_day is None:
        return []
    points = []
    day = as_of
    volume = volume_ml
    while day <= end:
        points.append((day, volume))
        day += timedelta(days=7)
        volume += rate_ml_per_day * 7
    if points and points[-1][0] != end:
        last_day = points[-1][0]
        points.append((end, points[-1][1] + rate_ml_per_day * (end - last_day).days))
    return points
