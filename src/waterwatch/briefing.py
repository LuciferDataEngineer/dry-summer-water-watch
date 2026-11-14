"""Turn measured tables into the sentences and rankings the pages show."""

from __future__ import annotations

from datetime import datetime

from waterwatch.config import LOW_LINE, RESTRICTION_SAVING


def _num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    if digits == 0:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def _days(value: float | None) -> str:
    if value is None:
        return "holds through summer"
    if value <= 0:
        return "already under the line"
    rounded = int(round(value))
    unit = "day" if rounded == 1 else "days"
    return f"{rounded} {unit}"


def _billion_litres(ml: float | None) -> str:
    if ml is None:
        return "n/a"
    billions = ml / 1000.0
    if abs(billions) >= 100:
        return f"{billions:,.0f} billion litres"
    if abs(billions) >= 10:
        return f"{billions:,.0f} billion litres"
    return f"{billions:,.1f} billion litres"


ROLE_LABEL = {
    "urban": "town supply",
    "irrigation": "farming supply",
    "hydro": "power-station lake",
    "regional": "regional supply",
}


def role_label(role: str) -> str:
    return ROLE_LABEL.get(role, "regional supply")


def urgency_key(storage: dict) -> tuple:
    days = storage.get("days_dry")
    return (
        0 if storage.get("already_under_line") else 1,
        0 if storage.get("dry_before_christmas") else 1,
        days if isinstance(days, (int, float)) else 10_000,
        storage.get("percent_full") if storage.get("percent_full") is not None else 999,
        -storage.get("capacity_ml", 0),
    )


def ranked(storages: list[dict]) -> list[dict]:
    return sorted(storages, key=urgency_key)


def lowest_city(cities: list[dict]) -> dict | None:
    usable = [city for city in cities if city.get("percent_full") is not None]
    if not usable:
        return None
    return min(usable, key=lambda city: city["percent_full"])


def headline(storages: list[dict], cities: list[dict]) -> tuple[str, str]:
    ordered = ranked([storage for storage in storages if storage.get("role") != "hydro"])
    if not ordered:
        ordered = ranked(storages)
    city = lowest_city(cities)
    city_bit = ""
    if city is not None:
        last = city.get("percent_last_year")
        change = ""
        if last is not None and city.get("percent_full") is not None:
            delta = city["percent_full"] - last
            change = f", {abs(delta):.1f} points {'down' if delta < 0 else 'up'} on last year"
        city_bit = f"{city['city']}’s system is {_pct(city['percent_full'])} full{change}."
    under = [storage for storage in ordered if storage.get("already_under_line")]
    towns = [storage for storage in under if storage.get("role") == "urban"]
    soon = [storage for storage in ordered if storage.get("dry_before_christmas") and not storage.get("already_under_line")]
    if under:
        lead = towns[0] if towns else under[0]
        empty = [storage for storage in under if (storage.get("percent_full") or 0) <= 0.5]
        empty_bit = ""
        if empty and empty[0]["place"] != lead["place"]:
            since = empty[0].get("empty_since")
            when = ""
            if since:
                when = " since " + datetime.strptime(since[:10], "%Y-%m-%d").strftime("%-d %B %Y")
            empty_bit = f" {empty[0]['place']} has read empty{when}."
        if lead.get("role") == "urban":
            title = f"{lead['place']} is the town supply already under the 40% line"
        else:
            title = f"{lead['place']} is already under the 40% line"
        lede = (
            f"{lead['place']} ({role_label(lead['role'])}, {lead['state']}) is at {_pct(lead['percent_full'])}. "
            f"{len(under)} tracked supplies are already under 40% full.{empty_bit} "
            f"{city_bit}"
        ).strip()
        return title, lede
    if soon:
        lead = soon[0]
        title = f"{lead['place']} reaches the 40% line first if summer stays dry"
        lede = (
            f"On the pace of its driest recent summer, {lead['place']} "
            f"({role_label(lead['role'])}, {lead['state']}) has about {_days(lead.get('days_dry'))} "
            f"above the 40% line. {len(soon)} tracked supplies would cross that line before Christmas. "
            f"{city_bit}"
        ).strip()
        return title, lede
    lead = ordered[0]
    title = "No tracked supply is on course to cross 40% before Christmas"
    lede = (
        f"Even on a dry-summer pace, the tracked supplies stay above the 40% line through Christmas. "
        f"The fullest attention still goes to {lead['place']} at {_pct(lead['percent_full'])}. "
        f"{city_bit}"
    ).strip()
    return title, lede


def outlook_kpis(storages: list[dict], cities: list[dict], states: list[dict]) -> list[dict]:
    tracked = [storage for storage in storages if storage.get("days_dry") is not None or storage.get("percent_full") is not None]
    decision = [storage for storage in tracked if storage.get("role") != "hydro"] or tracked
    under = [storage for storage in decision if storage.get("already_under_line")]
    soon = [storage for storage in decision if storage.get("dry_before_christmas") and not storage.get("already_under_line")]
    ordered = ranked(decision)
    lead = ordered[0] if ordered else None
    city = lowest_city(cities)
    nsw = next((state for state in states if state.get("name") == "New South Wales"), None)
    cards = []
    cards.append(
        {
            "label": "Already under 40% full",
            "value": str(len(under)),
            "detail": "Tracked town, farm and regional supplies, not counting power-station lakes.",
            "exact": f"Exact count: {len(under)} of {len(decision)} tracked supplies below {LOW_LINE * 100:.0f}% of accessible capacity.",
            "tone": "bad" if under else "good",
        }
    )
    cards.append(
        {
            "label": "Would cross 40% before Christmas if it stays dry",
            "value": str(len(soon)),
            "detail": "Dry pace means each storage repeats its driest recent summer.",
            "exact": f"Exact count: {len(soon)}. Christmas is 25 December 2026.",
            "tone": "bad" if soon else "good",
        }
    )
    if lead is not None:
        cards.append(
            {
                "label": "First to watch",
                "value": lead["place"],
                "detail": f"{_pct(lead.get('percent_full'))} full · {_days(lead.get('days_dry'))} on a dry summer.",
                "exact": (
                    f"Exact value: {_pct(lead.get('percent_full'))} full, "
                    f"{_num(lead.get('volume_ml'))} ML of {_num(lead.get('capacity_ml'))} ML. "
                    f"Dry-path buffer: {lead.get('days_dry') if lead.get('days_dry') is not None else 'not falling'}."
                ),
                "tone": "bad" if lead.get("already_under_line") or lead.get("dry_before_christmas") else "watch",
            }
        )
    if city is not None:
        delta = None
        if city.get("percent_last_year") is not None:
            delta = city["percent_full"] - city["percent_last_year"]
        cards.append(
            {
                "label": f"Lowest capital system · {city['city']}",
                "value": _pct(city["percent_full"]),
                "detail": "Whole-of-city storages, as published this week.",
                "exact": (
                    f"Exact value: {_pct(city['percent_full'])} of {_num(city.get('capacity_ml'))} ML, "
                    f"{_num(city.get('volume_ml'))} ML in storage"
                    + (f". Last year {_pct(city.get('percent_last_year'))} ({delta:+.1f} points)." if delta is not None else ".")
                ),
                "tone": "bad" if city["percent_full"] < 50 else "watch" if city["percent_full"] < 70 else "good",
            }
        )
    if nsw is not None and nsw.get("percent_full") is not None:
        last = nsw.get("percent_last_year")
        delta = None if last is None else nsw["percent_full"] - last
        cards.append(
            {
                "label": "New South Wales storages",
                "value": _pct(nsw["percent_full"]),
                "detail": "All reported NSW storages together, not only the ones tracked day by day.",
                "exact": (
                    f"Exact value: {_pct(nsw['percent_full'])}, {_num(nsw.get('volume_ml'))} ML of "
                    f"{_num(nsw.get('capacity_ml'))} ML"
                    + (f". A year ago {_pct(last)} ({delta:+.1f} points)." if delta is not None else ".")
                ),
                "tone": "watch" if nsw["percent_full"] < 70 else "good",
            }
        )
    return cards[:5]


def tight_takeaway(storages: list[dict]) -> str:
    ordered = ranked([storage for storage in storages if storage.get("role") != "hydro"] or storages)
    if not ordered:
        return "No storage readings were available."
    lead = ordered[0]
    under = [storage for storage in ordered if storage.get("already_under_line")]
    if under:
        names = ", ".join(storage["place"] for storage in under[:4])
        return (
            f"{len(under)} supplies are already under 40% full, led by {lead['place']} "
            f"at {_pct(lead.get('percent_full'))}. Names: {names}."
        )
    soon = [storage for storage in ordered if storage.get("dry_before_christmas")]
    if soon:
        return (
            f"{soon[0]['place']} is the first that would cross 40% before Christmas "
            f"if it repeated its driest recent summer, in {_days(soon[0].get('days_dry'))}."
        )
    return (
        f"None of these supplies cross 40% before Christmas on a dry-summer pace. "
        f"{ordered[0]['place']} is the lowest, at {_pct(ordered[0].get('percent_full'))}."
    )


def _path_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value <= 0:
        return "empty"
    return f"{value:.1f}%"


def scenario_takeaway(storage: dict | None) -> str:
    if storage is None:
        return "A summer path could not be drawn."
    return (
        f"By the end of March, {storage['place']} would be {_path_pct(storage.get('end_percent_dry'))} "
        f"on its driest recent summer, {_path_pct(storage.get('end_percent_typical'))} on a middle summer, "
        f"and {_path_pct(storage.get('end_percent_wet'))} on its wettest recent summer. "
        f"The shaded band is the likely range for the next two months."
    )


def lever_takeaway(levers: list[dict]) -> str:
    usable = [row for row in levers if row.get("geography") != "Australia" and (row.get("urban_gap_ml") or 0) > 0]
    if not usable:
        covered = [row for row in levers if row.get("geography") != "Australia"]
        if not covered:
            return "State supply figures are not available."
        return (
            "On a dry-summer pace, the town supplies we track do not finish March under the 40% line, "
            "so there is no volume gap for restrictions or desalination to close in this briefing."
        )
    focus = max(usable, key=lambda row: row.get("urban_gap_ml") or 0)
    restriction = focus.get("restriction_share")
    desal = focus.get("desal_share")
    parts = [
        f"The largest town-supply gap is in {focus['geography']}: {_billion_litres(focus.get('urban_gap_ml'))} "
        f"short of the 40% line by the end of March if those storages repeat their driest recent summer."
    ]
    if restriction is not None:
        parts.append(
            f"A what-if 15% cut in household use from now to the end of March covers about {restriction * 100:.0f}% of that gap."
        )
    if desal is not None:
        parts.append(
            f"Running desalination at last year’s pace covers about {desal * 100:.0f}% of it."
        )
    elif focus.get("desal_ml") == 0:
        parts.append("Last year’s desalination figure for this state is nil.")
    return " ".join(parts)


def lever_rows(states: list[dict], storages: list[dict], accounts: list[dict], days_remaining: int) -> list[dict]:
    from waterwatch.scenarios import gap_ml, lever_volumes, share_closed

    by_geo = {row["geography"]: row for row in accounts}
    rows = []
    for account in accounts:
        name = account["geography"]
        in_state = [
            storage
            for storage in storages
            if storage.get("state") == name or (name == "Australia")
        ]
        urban = [storage for storage in in_state if storage.get("role") == "urban"]
        irrigation = [storage for storage in in_state if storage.get("role") == "irrigation"]
        urban_gap = sum(gap_ml(storage.get("end_volume_dry_ml"), storage.get("capacity_ml") or 0) for storage in urban)
        irrigation_gap = sum(gap_ml(storage.get("end_volume_dry_ml"), storage.get("capacity_ml") or 0) for storage in irrigation)
        volumes = lever_volumes(account.get("household_ml"), account.get("desal_ml"), account.get("reuse_supply_ml"), days_remaining)
        rows.append(
            {
                "geography": name,
                "household_ml": account.get("household_ml"),
                "desal_ml_year": account.get("desal_ml"),
                "desal_previous_ml": account.get("desal_previous_ml"),
                "reuse_ml_year": account.get("reuse_supply_ml"),
                "urban_gap_ml": urban_gap,
                "irrigation_gap_ml": irrigation_gap,
                "tracked_urban": len(urban),
                "tracked_irrigation": len(irrigation),
                **volumes,
                "restriction_share": share_closed(urban_gap, volumes["restriction_ml"]),
                "desal_share": share_closed(urban_gap, volumes["desal_ml"]),
                "reuse_share": share_closed(urban_gap, volumes["reuse_ml"]),
                "restriction_rate": RESTRICTION_SAVING,
            }
        )
    # Silence unused.
    del states, by_geo
    return rows
