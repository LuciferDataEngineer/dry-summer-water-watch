"""Turn measured tables into the sentences and rankings the pages show."""

from __future__ import annotations

from datetime import datetime

from waterwatch.config import RESTRICTION_SAVING


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


def decision_set(storages: list[dict]) -> list[dict]:
    """Town, farm and regional supplies. Power-station lakes are tracked, not counted."""
    tracked = [
        storage
        for storage in storages
        if storage.get("days_dry") is not None or storage.get("percent_full") is not None
    ]
    decision = [storage for storage in tracked if storage.get("role") != "hydro"]
    return decision or tracked


def hydro_under_line(storages: list[dict]) -> list[dict]:
    return [storage for storage in storages if storage.get("role") == "hydro" and storage.get("already_under_line")]


def under_line_exact(storages: list[dict]) -> str:
    """One sentence for the under-40% count, used on every tab."""
    decision = decision_set(storages)
    under = [storage for storage in decision if storage.get("already_under_line")]
    sentence = (
        f"{len(under)} of {len(decision)} town, farm and regional supplies are under 40% of accessible capacity. "
        "Power-station lakes are not in this count."
    )
    excluded = hydro_under_line(storages)
    if excluded:
        bits = "; ".join(
            f"{storage['place']} ({storage['state']}) at {_pct(storage.get('percent_full'))}" for storage in excluded
        )
        sentence += f" Also under 40%, but excluded: {bits}."
    return sentence


def next_above_the_line(storages: list[dict]) -> dict | None:
    for storage in ranked(decision_set(storages)):
        days = storage.get("days_dry")
        if storage.get("already_under_line"):
            continue
        if isinstance(days, (int, float)) and days > 0:
            return storage
    return None


def headline(storages: list[dict], cities: list[dict]) -> tuple[str, str]:
    ordered = ranked(decision_set(storages))
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
            qualifier = ""
            if empty[0].get("role") != "urban":
                qualifier = f", a {role_label(empty[0]['role'])} rather than a town supply,"
            empty_bit = f" {empty[0]['place']}{qualifier} has read empty{when}."
        if lead.get("role") == "urban":
            title = f"{lead['place']} is the town supply already under the 40% line"
        else:
            title = f"{lead['place']} is already under the 40% line"
        lede = (
            f"{lead['place']} ({role_label(lead['role'])}, {lead['state']}) is at {_pct(lead['percent_full'])}. "
            f"{len(under)} town, farm and regional supplies are already under 40% full.{empty_bit} "
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
    decision = decision_set(storages)
    under = [storage for storage in decision if storage.get("already_under_line")]
    soon = [storage for storage in decision if storage.get("dry_before_christmas") and not storage.get("already_under_line")]
    ordered = ranked(decision)
    lead = ordered[0] if ordered else None
    towns = [storage for storage in under if storage.get("role") == "urban"]
    city = lowest_city(cities)
    nsw = next((state for state in states if state.get("name") == "New South Wales"), None)
    cards = []
    cards.append(
        {
            "label": "Already under 40% full",
            "value": str(len(under)),
            "detail": "Town, farm and regional supplies. Power-station lakes are not counted.",
            "exact": under_line_exact(storages),
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
        town = towns[0] if towns else None
        if lead.get("role") != "urban":
            town_bit = f" {town['place']} is the town supply already under the 40% line." if town else ""
            cards.append(
                {
                    "label": "Emptiest storage",
                    "value": lead["place"],
                    "detail": f"A {role_label(lead['role'])}, not a town supply.{town_bit}",
                    "exact": (
                        f"Exact value: {_pct(lead.get('percent_full'))} full, "
                        f"{_num(lead.get('volume_ml'))} ML of {_num(lead.get('capacity_ml'))} ML. "
                        f"{lead['place']} is a {role_label(lead['role'])} in {lead['state']}, not a town supply. "
                        f"Dry-path buffer: {lead.get('days_dry') if lead.get('days_dry') is not None else 'not falling'}."
                    ),
                    "tone": "bad" if lead.get("already_under_line") or lead.get("dry_before_christmas") else "watch",
                }
            )
        else:
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


def tight_story(storages: list[dict]) -> tuple[str, str]:
    ordered = ranked(decision_set(storages))
    if not ordered:
        text = "No storage readings were available."
        return text, text
    under = [storage for storage in ordered if storage.get("already_under_line")]
    nxt = next_above_the_line(storages)
    emptiest = ordered[0]
    if nxt is not None:
        days = int(round(nxt["days_dry"]))
        unit = "day" if days == 1 else "days"
        title = f"{nxt['place']} runs out first, in {days} {unit}"
        not_town = "" if emptiest.get("role") == "urban" else ", not a town supply"
        lede = (
            f"{nxt['place']} ({role_label(nxt['role'])}, {nxt['state']}) would cross 40% in {_days(nxt.get('days_dry'))} "
            f"if it repeated its driest recent summer. It is the first supply still above the line. "
            f"{len(under)} town, farm and regional supplies are already under 40% full. "
            f"{emptiest['place']} is the emptiest of those, a {role_label(emptiest['role'])}{not_town}, "
            f"at {_pct(emptiest.get('percent_full'))}."
        )
        return title, lede
    if under:
        title = f"{len(under)} supplies are already under the 40% line"
        lede = (
            f"{len(under)} town, farm and regional supplies are already under 40% full, "
            f"led by {emptiest['place']} at {_pct(emptiest.get('percent_full'))}."
        )
        return title, lede
    title = f"{emptiest['place']} is the lowest supply still above 40%"
    lede = (
        f"None of these supplies cross 40% before Christmas on a dry-summer pace. "
        f"{emptiest['place']} is the lowest, at {_pct(emptiest.get('percent_full'))}."
    )
    return title, lede


def tight_takeaway(storages: list[dict]) -> str:
    _title, lede = tight_story(storages)
    return lede


def tight_kpis(storages: list[dict]) -> list[dict]:
    ordered = ranked(decision_set(storages))
    under = [storage for storage in ordered if storage.get("already_under_line")]
    towns = [storage for storage in under if storage.get("role") == "urban"]
    nxt = next_above_the_line(storages)
    cards = [
        {
            "label": "Already under 40% full",
            "value": str(len(under)),
            "detail": "Town, farm and regional supplies. The same count as the outlook.",
            "exact": under_line_exact(storages),
            "tone": "bad" if under else "good",
        }
    ]
    if nxt is not None:
        cards.append(
            {
                "label": "Next to cross 40%",
                "value": nxt["place"],
                "detail": f"{_days(nxt.get('days_dry'))} on a dry summer. A {role_label(nxt['role'])} in {nxt['state']}.",
                "exact": (
                    f"Dry-path buffer: {nxt.get('days_dry')} days, shown as {_days(nxt.get('days_dry'))}. "
                    f"Now {_pct(nxt.get('percent_full'))} full."
                ),
                "tone": "watch",
            }
        )
    if towns:
        names = ", ".join(storage["place"] for storage in towns[:4])
        extra = "" if len(towns) <= 4 else f" and {len(towns) - 4} more"
        cards.append(
            {
                "label": "Town supplies under 40%",
                "value": str(len(towns)),
                "detail": f"{names}{extra}.",
                "exact": f"{len(towns)} of the {len(under)} already under the line are town supplies: {names}{extra}.",
                "tone": "bad",
            }
        )
    emptiest = ordered[0] if ordered else None
    if emptiest is not None and emptiest.get("role") != "urban":
        cards.append(
            {
                "label": "Emptiest storage",
                "value": emptiest["place"],
                "detail": f"A {role_label(emptiest['role'])}, not a town supply.",
                "exact": (
                    f"{_pct(emptiest.get('percent_full'))} full. {emptiest['place']} is a {role_label(emptiest['role'])} "
                    f"in {emptiest['state']} and is not the town supply in the outlook headline."
                ),
                "tone": "bad" if emptiest.get("already_under_line") else "watch",
            }
        )
    return cards[:4]


def scenario_title(storage: dict | None) -> str:
    if storage is None:
        return "Summer paths"
    end = storage.get("end_percent_dry")
    if end is None:
        return f"How {storage['place']} could finish the summer"
    if end <= 0.5:
        return f"{storage['place']} is empty by the end of March on a dry summer"
    return f"{storage['place']} finishes March at {end:.0f}% full on a dry summer"


def _end_value(percent: float | None) -> str:
    if percent is None:
        return "n/a"
    if percent <= 0.5:
        return "Empty"
    return f"{percent:.1f}%"


def _percent_tone(percent: float | None) -> str:
    if percent is None:
        return "watch"
    if percent < 40:
        return "bad"
    if percent < 60:
        return "watch"
    return "good"


def scenario_kpis(storage: dict) -> list[dict]:
    cards = [
        {
            "label": "Full now",
            "value": _pct(storage.get("percent_full")),
            "detail": f"{role_label(storage.get('role') or '')}, {storage.get('state') or ''}.",
            "exact": (
                f"{_pct(storage.get('percent_full'))} of accessible capacity, "
                f"{_num(storage.get('volume_ml'))} ML of {_num(storage.get('capacity_ml'))} ML."
            ),
            "tone": _percent_tone(storage.get("percent_full")),
        }
    ]
    for label, key in (
        ("Dry summer, end of March", "end_percent_dry"),
        ("Typical summer, end of March", "end_percent_typical"),
        ("Wet summer, end of March", "end_percent_wet"),
    ):
        percent = storage.get(key)
        shown = 0 if percent is not None and percent < 0 else percent
        cards.append(
            {
                "label": label,
                "value": _end_value(percent),
                "detail": "Where this supply finishes on 31 March 2027 if that recent summer repeats.",
                "exact": f"Finishes 31 March 2027 {_path_pct(percent)} on this path.",
                "tone": _percent_tone(shown),
            }
        )
    return cards


def gap_states(levers: list[dict]) -> list[dict]:
    return [row for row in levers if row.get("geography") != "Australia" and (row.get("urban_gap_ml") or 0) > 0]


def _gl_label(ml: float | None) -> str:
    gl = (ml or 0) / 1000.0
    if abs(gl) >= 10:
        return f"{gl:,.0f}"
    return f"{gl:,.1f}"


_SHORT_STATE = {
    "Western Australia": "WA",
    "South Australia": "SA",
    "New South Wales": "NSW",
    "Australian Capital Territory": "ACT",
    "Northern Territory": "NT",
}


def _best_closer(row: dict) -> tuple[str, float | None]:
    options = (
        ("desalination", row.get("desal_share")),
        ("a 15% household cut", row.get("restriction_share")),
        ("recycled water", row.get("reuse_share")),
    )
    return max(options, key=lambda item: -1.0 if item[1] is None else item[1])


def _closes_phrase(share: float | None, noun: str) -> str:
    if share is None:
        return f"{noun} is not available for this gap"
    if share >= 0.995:
        return f"{noun} covers it"
    if 0.45 <= share <= 0.55:
        return f"{noun} closes about half"
    return f"{noun} closes about {share * 100:.0f}%"


def lever_title(levers: list[dict]) -> str:
    rows = gap_states(levers)
    if not rows:
        return "No town-supply gap by the end of March"
    focus = max(rows, key=lambda row: row.get("urban_gap_ml") or 0)
    place = _SHORT_STATE.get(focus["geography"], focus["geography"])
    noun, share = _best_closer(focus)
    return f"{place}’s {_gl_label(focus.get('urban_gap_ml'))} GL gap, where {_closes_phrase(share, noun)}"


def _share_value(share: float | None) -> str:
    if share is None:
        return "–"
    if 0.45 <= share <= 0.55:
        return "About half"
    return f"{share * 100:.0f}%"


def lever_kpis(levers: list[dict]) -> list[dict]:
    rows = gap_states(levers)
    if not rows:
        return [
            {
                "label": "Town-supply gap",
                "value": "0 GL",
                "detail": "No tracked town supply finishes March under 40% on a dry summer.",
                "exact": "Urban gap is 0 ML in every state.",
                "tone": "good",
            }
        ]
    focus = max(rows, key=lambda row: row.get("urban_gap_ml") or 0)
    gap_gl = _gl_label(focus.get("urban_gap_ml"))
    desal = focus.get("desal_share")
    restriction = focus.get("restriction_share")
    if len(rows) == 1:
        names = rows[0]["geography"]
    elif len(rows) == 2:
        names = f"{rows[0]['geography']} and {rows[1]['geography']}"
    else:
        names = ", ".join(row["geography"] for row in rows[:-1]) + f" and {rows[-1]['geography']}"
    return [
        {
            "label": "Largest town-supply gap",
            "value": f"{gap_gl} GL",
            "detail": f"{focus['geography']}, short of 40% by the end of March on a dry summer.",
            "exact": f"{(focus.get('urban_gap_ml') or 0):,.0f} ML, which is {gap_gl} GL. 1 GL is a billion litres.",
            "tone": "bad",
        },
        {
            "label": "Desalination",
            "value": _share_value(desal),
            "detail": f"Last year’s pace, scaled to the days left, against the {focus['geography']} gap.",
            "exact": (
                f"Desalination {_gl_label(focus.get('desal_ml'))} GL against a {gap_gl} GL gap"
                + (f" ({desal * 100:.0f}%)." if desal is not None else ".")
            ),
            "tone": "watch" if desal is not None and desal >= 0.4 else "bad",
        },
        {
            "label": "15% household cut",
            "value": _share_value(restriction),
            "detail": "An illustration, not a measured saving from a past restriction.",
            "exact": (
                f"Household cut {_gl_label(focus.get('restriction_ml'))} GL"
                + (f" ({restriction * 100:.0f}% of the gap)." if restriction is not None else ".")
            ),
            "tone": "watch",
        },
        {
            "label": "States with a town gap",
            "value": str(len(rows)),
            "detail": f"{names}. The chart shows only these. The table lists every state.",
            "exact": "A state is in the chart only when its tracked town supplies finish March under 40% on the dry path.",
            "tone": "watch",
        },
    ]


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
