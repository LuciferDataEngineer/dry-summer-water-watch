"""Method notes. Numbers are filled from the same briefing the pages use."""

from __future__ import annotations

from pathlib import Path

from waterwatch.briefing import decision_set, hydro_under_line


def _n(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    if digits == 0:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def write_insights(briefing: dict, path: Path) -> None:
    storages = briefing["storages"]
    decision = decision_set(storages)
    under = [row for row in decision if row.get("already_under_line")]
    soon = [row for row in decision if row.get("dry_before_christmas") and not row.get("already_under_line")]
    chronos = briefing.get("chronos") or {}
    balance = briefing.get("balance") or {}
    australia = next(row for row in briefing["accounts"] if row["geography"] == "Australia")
    lines = [
        "# Insights — dry summer water watch",
        "",
        f"Snapshot: {briefing['snapshot_display']}. Storage readings used below are current to {briefing['as_of_display']}.",
        "These figures are computed from the committed public snapshot. They are not copied from a press summary.",
        "",
        "## The decision",
        "",
        briefing["headline"] + ".",
        "",
        briefing["lede"],
        "",
        f"Tracked supplies with a daily history: {len(storages)}. "
        f"Of the town, farming and regional supplies in that set, {len(under)} are already under 40% of accessible capacity "
        f"and {len(soon)} more would cross 40% before Christmas if they repeated their driest recent summer.",
        "",
    ]
    excluded = hydro_under_line(storages)
    if excluded:
        bits = ", ".join(f"{row['place']} ({row['state']}) at {_pct(row.get('percent_full'))}" for row in excluded)
        lines.append(
            f"Power-station lakes are not in that count. Also under 40%, but excluded: {bits}."
        )
    lines += [
        "",
        "### Supplies already under 40%",
        "",
    ]
    if not under:
        lines.append("None of the tracked town, farming or regional supplies are under 40% full.")
    for row in under[:12]:
        lines.append(
            f"- {row['place']} ({row['state']}, {row['role']}): {_pct(row.get('percent_full'))} full, "
            f"{_n(row.get('volume_ml'))} ML of {_n(row.get('capacity_ml'))} ML."
        )
    lines += ["", "### Would cross 40% before Christmas on a dry summer", ""]
    if not soon:
        lines.append("None. The supplies that are in trouble are already under the line.")
    for row in soon[:12]:
        lines.append(
            f"- {row['place']} ({row['state']}): {_pct(row.get('percent_full'))} full, "
            f"about {_n(row.get('days_dry'), 1)} days to the 40% line on the dry path, "
            f"{_n(row.get('days_recent'), 1)} days if the last month continues."
        )
    later = [
        row
        for row in decision
        if row.get("dry_before_summer_end") and not row.get("already_under_line") and not row.get("dry_before_christmas")
    ]
    later.sort(key=lambda row: row.get("days_dry") if isinstance(row.get("days_dry"), (int, float)) else 10_000)
    lines += ["", "### Would cross 40% after Christmas and before the end of March", ""]
    if not later:
        lines.append("None.")
    for row in later[:8]:
        lines.append(
            f"- {row['place']} ({row['state']}, {row['role']}): {_pct(row.get('percent_full'))} full now, "
            f"about {_n(row.get('days_dry'), 0)} days to the 40% line, "
            f"finishing March at {_pct(row.get('end_percent_dry'))}."
        )
    lines += ["", "## Capital-city systems", ""]
    for city in briefing["cities"]:
        last = city.get("percent_last_year")
        delta = None if last is None or city.get("percent_full") is None else city["percent_full"] - last
        extra = "" if delta is None else f" ({delta:+.1f} points on last year)"
        lines.append(
            f"- {city['city']}: {_pct(city.get('percent_full'))} full, "
            f"{_n(city.get('volume_ml'))} ML of {_n(city.get('capacity_ml'))} ML{extra}. "
            f"Reported {city.get('reported_date')}."
        )
    lines += ["", "## States", ""]
    for state in briefing["states"]:
        last = state.get("percent_last_year")
        delta = None if last is None or state.get("percent_full") is None else state["percent_full"] - last
        extra = "" if delta is None else f", {delta:+.1f} points on last year ({_pct(last)})"
        lines.append(
            f"- {state['name']}: {_pct(state.get('percent_full'))}, "
            f"{_n(state.get('volume_ml'))} ML of {_n(state.get('capacity_ml'))} ML{extra}."
        )
    lines += ["", "## Drainage divisions under 60%", ""]
    low = [
        row
        for row in briefing["drainage"]
        if row.get("percent_full") is not None and row["percent_full"] < 60 and (row.get("capacity_ml") or 0) > 0
    ]
    low.sort(key=lambda row: row["percent_full"])
    if not low:
        lines.append("None.")
    for row in low:
        lines.append(
            f"- {row['name']}: {_pct(row.get('percent_full'))}, "
            f"{_n(row.get('volume_ml'))} ML of {_n(row.get('capacity_ml'))} ML. "
            f"A year ago {_pct(row.get('percent_last_year'))}."
        )
    lines += [
        "",
        "## What closes the gap",
        "",
        f"Australia, 2024–25: households used {_n((australia.get('household_ml') or 0) / 1000)} billion litres "
        f"(distributed water plus reuse). Desalination supplied to the water industry was "
        f"{_n((australia.get('desal_ml') or 0) / 1000)} billion litres, against "
        f"{_n((australia.get('desal_previous_ml') or 0) / 1000)} billion litres the year before. "
        f"Total reuse supplied was {_n((australia.get('reuse_supply_ml') or 0) / 1000)} billion litres.",
        "",
        "The town-supply gap is the volume by which tracked urban storages in that state would sit under 40% "
        "on 31 March 2027 if each repeated its driest recent summer. The 15% household cut, desalination and "
        "recycled water are last year’s annual figures scaled by the days left until 31 March, divided by 365. "
        "A share above 100% is reported as fully covered. Farming storages are a separate gap: a city desalination "
        "plant does not refill Hume or Eildon.",
        "",
    ]
    for row in briefing["levers"]:
        if row["geography"] == "Australia":
            continue
        lines.append(
            f"- {row['geography']}: town gap {_n((row.get('urban_gap_ml') or 0) / 1000, 1)} GL "
            f"across {row.get('tracked_urban')} tracked town "
            f"{'supply' if row.get('tracked_urban') == 1 else 'supplies'}; "
            f"farming gap {_n((row.get('irrigation_gap_ml') or 0) / 1000, 1)} GL "
            f"across {row.get('tracked_irrigation')} "
            f"{'storage' if row.get('tracked_irrigation') == 1 else 'storages'}; "
            f"15% household cut {_n((row.get('restriction_ml') or 0) / 1000, 1)} GL; "
            f"desalination {_n((row.get('desal_ml') or 0) / 1000, 1)} GL; "
            f"recycled water {_n((row.get('reuse_ml') or 0) / 1000, 1)} GL."
        )
    beats = chronos.get("beats_seasonal")
    compared = chronos.get("compared_seasonal")
    lines += [
        "",
        "## Likely range, and whether it beat two simple paths",
        "",
        "For up to 24 of the tightest storages with at least 400 days of readings, a small open time-series "
        "foundation model (Chronos-Bolt small, Apache-2.0, CPU) produced a 10th-to-90th percentile band for the "
        "next 64 days. That horizon stays inside the length the model was trained to predict and covers Christmas "
        "through late January. The dry, typical and wet lines on the page are not from this model. They replay summers.",
        "",
        "The last 60 days were held out. Error is the average miss, in megalitres, of the middle of the band. "
        "The same-date-last-year path uses the volume 365 days earlier. The last-month path carries forward the "
        "average daily change of the 30 days before the hold-out.",
        "",
        f"Series scored: {chronos.get('series', 0)}. "
        f"Average miss, middle of the band: {_n(chronos.get('mae_chronos_ml'), 0)} ML "
        f"({_n(chronos.get('mae_chronos_pct_capacity'), 1)}% of that storage’s capacity). "
        f"Same-date-last-year: {_n(chronos.get('mae_seasonal_ml'), 0)} ML "
        f"({_n(chronos.get('mae_seasonal_pct_capacity'), 1)}% of capacity). "
        f"Last-month pace: {_n(chronos.get('mae_recent_rate_ml'), 0)} ML "
        f"({_n(chronos.get('mae_recent_pct_capacity'), 1)}% of capacity). "
        f"The band’s middle beat the same-date-last-year path on {beats} of {compared} storages, "
        f"and beat the last-month pace on {chronos.get('beats_recent_rate')} of {chronos.get('compared_recent')}.",
        "",
        "## Rain and evaporation cross-check",
        "",
        "For each tracked storage, daily change was lined up with that day’s rain and reference evaporation. "
        "The fit is kept only when rain has a positive link and explains at least 5% of the day-to-day change. "
        "Releases and demand dominate many dams, so a weak fit is discarded rather than used for the briefing.",
        "",
        f"Usable fits: {balance.get('usable')} of {balance.get('tracked')} tracked storages. "
        f"On those, the dry-weather pace and the driest-summer pace disagree about crossing 40% before Christmas "
        f"in {balance.get('disagree_on_christmas')} cases. The page uses the driest-summer pace, because it is "
        "taken straight from the storage record.",
        "",
        "## How the paths are defined",
        "",
        "- Accessible capacity, latest volume and percent full come from the Bureau’s water storage dashboard.",
        "- Daily history starts on 1 January 2022. A gap between readings is spread evenly across the missing days.",
        "- A completed summer is 1 November to 31 March. The four summers from 2022–23 to 2025–26 are the analogues. "
        "The current summer is not used as an analogue.",
        "- A summer counts only if it has at least 40 days of change.",
        "- Dry path: the summer with the lowest average daily change. Typical: the middle summer. Wet: the highest.",
        "- Last month: the average daily change over the 30 days up to the latest reading.",
        "- Days of buffer: megalitres above 40% of capacity, divided by the daily fall. "
        "If the storage is not falling by at least 1 ML a day, the buffer is reported as holding through summer.",
        "- Already under the line: official percent full below 40. Critical on the page’s colour scale is below 25.",
        "- Watch: 40–60%, or still above 40% but the dry path crosses 40% before 31 March 2027.",
        "- The 15% household cut is an illustration, not a measured saving from a past restriction.",
        "",
        "## Streaming check and the batch rebuild",
        "",
        "The same 30-day and 90-day change, the status band, and the days to the 40% line on the last-month pace "
        "are expressed as streaming queries and as a full batch rebuild. The measured times are in the benchmark "
        "section of the README once that job has run. Both use the snapshot, not a live feed.",
        "",
        "## What this does not say",
        "",
        "- It does not set a restriction, a pumping schedule, or an allocation.",
        "- Power-station lakes are tracked but kept out of the “already under 40%” count on the front page, "
        "because a low hydro lake is not a town running out of drinking water.",
        "- Town and farming roles are name rules (Warragamba is a town supply, Hume is a farming supply). "
        "They are a labelling choice so the levers are not asked to refill the wrong dam.",
        f"- {sum(1 for row in storages if row.get('latitude') is None)} tracked supplies did not match a Bureau station coordinate and are left off the map.",
        "- Where two states publish the same storage name, both rows are kept. Hume appears on the border.",
        "- One national clock (Australian Eastern Daylight Time, UTC+11) is used for dates and weather. "
        "A reading in Western Australia can sit a few hours off that clock.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
