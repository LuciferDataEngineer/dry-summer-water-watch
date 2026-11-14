from datetime import date

import pytest

from waterwatch.scenarios import (
    build_storage_outlook,
    daily_changes,
    days_until_line,
    gap_ml,
    lever_volumes,
    share_closed,
    summer_rates,
)


def _summer(year: int, daily_change: float) -> list[tuple[date, float]]:
    points = []
    volume = 100_000.0
    day = date(year, 11, 1)
    end = date(year + 1, 3, 31)
    while day <= end:
        points.append((day, volume))
        volume += daily_change
        day = date.fromordinal(day.toordinal() + 1)
    return points


def test_dry_path_is_the_driest_completed_summer():
    observations = []
    observations += _summer(2022, -10)
    observations += _summer(2023, -40)
    observations += _summer(2024, -20)
    observations += _summer(2025, -5)
    rates = summer_rates(daily_changes(observations))
    assert rates["summers_used"] == 4
    # Each summer restarts the volume, so the first day of a later summer carries a little of the gap.
    assert rates["dry_ml_per_day"] == pytest.approx(-40, abs=1)
    assert rates["wet_ml_per_day"] == pytest.approx(-5, abs=1)
    assert rates["dry_ml_per_day"] < rates["typical_ml_per_day"] < rates["wet_ml_per_day"]


def test_days_until_line_and_already_there():
    assert days_until_line(50_000, 100_000, -100) == 100
    assert days_until_line(30_000, 100_000, -100) == 0
    assert days_until_line(80_000, 100_000, 5) is None


def test_outlook_flags_christmas_when_the_dry_summer_was_steep():
    observations = []
    observations += _summer(2022, -5)
    observations += _summer(2023, -2000)
    observations += _summer(2024, -5)
    observations += _summer(2025, -5)
    # Land on 15 Nov 2026 with plenty still above the line, then apply the dry rate.
    observations.append((date(2026, 11, 15), 80_000))
    outlook = build_storage_outlook(observations, 100_000, 80)
    assert outlook["already_under_line"] is False
    assert outlook["days_dry"] is not None
    assert outlook["days_dry"] < outlook["days_to_christmas"]
    assert outlook["dry_before_christmas"] is True


def test_gap_and_lever_share():
    assert gap_ml(30_000, 100_000) == 10_000
    assert gap_ml(50_000, 100_000) == 0
    assert gap_ml(-20_000, 100_000) == 40_000
    volumes = lever_volumes(365_000, 365_000, None, 365)
    assert volumes["restriction_ml"] == 365_000 * 0.15
    assert volumes["desal_ml"] == 365_000
    assert share_closed(100, 250) == 1
    assert share_closed(0, 10) is None
