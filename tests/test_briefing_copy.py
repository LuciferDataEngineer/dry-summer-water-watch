"""Copy rules that keep the tabs on one under-40% definition."""

from waterwatch.briefing import lever_title, tight_story, under_line_exact
from waterwatch.dashboard import percent_axis


def _storage(**overrides):
    base = {
        "place": "Example",
        "state": "New South Wales",
        "role": "urban",
        "percent_full": 50.0,
        "days_dry": 80.0,
        "already_under_line": False,
        "capacity_ml": 1000,
        "volume_ml": 500,
    }
    base.update(overrides)
    return base


def test_power_station_lakes_are_outside_the_shared_count():
    storages = [
        _storage(place="Lake Menindee", role="irrigation", percent_full=0, days_dry=0, already_under_line=True),
        _storage(place="South Dandalup", state="Western Australia", percent_full=9.1, days_dry=0, already_under_line=True),
        _storage(place="Keepit", role="irrigation", percent_full=52.7, days_dry=42.6, already_under_line=False),
        _storage(place="Great Lake", state="Tasmania", role="hydro", percent_full=35.1, days_dry=0, already_under_line=True),
    ]
    sentence = under_line_exact(storages)
    assert sentence.startswith("2 of 3 town, farm and regional supplies are under 40% of accessible capacity.")
    assert "Power-station lakes are not in this count." in sentence
    assert "Great Lake (Tasmania) at 35.1%" in sentence
    title, lede = tight_story(storages)
    assert title == "Keepit runs out first, in 43 days"
    assert "2 town, farm and regional supplies are already under 40% full" in lede
    assert "not a town supply" in lede


def test_near_empty_paths_are_not_drawn_on_a_full_scale():
    low, high = percent_axis([0.0, 3.0, 6.1, 9.1])
    assert low == 0
    assert 9.1 < high < 40


def test_a_path_near_the_planning_line_keeps_that_line_on_the_scale():
    _low, high = percent_axis([21.0, 32.0, 46.0])
    assert high >= 46


def test_lever_headline_uses_the_largest_town_gap():
    levers = [
        {"geography": "Australia", "urban_gap_ml": 0},
        {"geography": "South Australia", "urban_gap_ml": 6000, "desal_share": 1, "restriction_share": 0.2, "reuse_share": 0.3},
        {"geography": "Western Australia", "urban_gap_ml": 97600, "desal_share": 0.544, "restriction_share": 0.16, "reuse_share": 0.14},
        {"geography": "New South Wales", "urban_gap_ml": 0, "desal_share": None, "restriction_share": None, "reuse_share": None},
    ]
    assert lever_title(levers) == "WA’s 98 GL gap, where desalination closes about half"
