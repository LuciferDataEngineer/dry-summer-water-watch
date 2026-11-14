"""Static tabbed pages for ministers, utilities and councils."""

from __future__ import annotations

import html
import json
import math
import shutil
from pathlib import Path

from waterwatch.briefing import (
    gap_states,
    lever_kpis,
    lever_title,
    scenario_kpis,
    scenario_takeaway,
    scenario_title,
    tight_kpis,
    tight_story,
    under_line_exact,
)

ASSETS = Path(__file__).resolve().parent / "assets"

TABS = (
    ("index.html", "The outlook"),
    ("tight.html", "Where it's tight"),
    ("scenarios.html", "Summer scenarios"),
    ("levers.html", "What closes the gap"),
)


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _pct(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:.1f}%"


def _ml(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:,.0f}"


def _days(value: float | None) -> str:
    if value is None:
        return "Holds"
    if value <= 0:
        return "Under the line"
    return f"{int(round(value))} days"


def _tone_for_percent(value: float | None) -> str:
    if value is None:
        return "watch"
    if value < 40:
        return "bad"
    if value < 60:
        return "watch"
    return "good"


def _status_colour(status: str) -> str:
    return {"critical": "#ff5c7a", "low": "#ff5c7a", "watch": "#ffc14d", "comfortable": "#2ee6a6"}.get(status, "#7ec8ff")


def _shell(active: str, body: str, briefing: dict) -> str:
    tabs = []
    for filename, label in TABS:
        klass = "tab active" if filename == active else "tab"
        tabs.append(f'<a class="{klass}" href="./{filename}">{esc(label)}</a>')
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(dict(TABS)[active])} · Dry summer water watch</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&amp;display=swap">
  <link rel="stylesheet" href="./assets/style.css">
</head>
<body>
  <header class="topbar">
    <div class="brand">Dry summer <span>water watch</span></div>
    <nav class="tabs" aria-label="Sections">{''.join(tabs)}</nav>
  </header>
  <main class="wrap">
    {body}
    <footer class="sources">
      <p>Sources: Bureau of Meteorology water storage dashboard (latest readings and daily history),
      the public daily weather archive (rain and evaporation at the gauge), and the Australian Bureau of Statistics
      Water Account 2024–25. Readings are current to {esc(briefing['as_of_display'])}.
      Snapshot taken {esc(briefing['snapshot_display'])}. Times are Australian Eastern Daylight Time.</p>
      <p>The 40% line is the planning line used on this page, not a legal restriction trigger for any one utility.
      Dry, typical and wet paths replay each storage’s own recent summers. They are not a promise of what the season will do.
      Licence: Bureau and Statistics data CC BY 4.0, weather CC BY 4.0, map outline public domain.
      <a href="https://github.com/LuciferDataEngineer/dry-summer-water-watch">Method notes</a>.</p>
    </footer>
  </main>
</body>
</html>
"""


def _kpis(cards: list[dict], section_id: str = "") -> str:
    blocks = []
    for card in cards:
        blocks.append(
            f"""<article class="kpi {esc(card['tone'])}">
              <div class="label">{esc(card['label'])}</div>
              <div class="value">{esc(card['value'])}</div>
              <p class="detail">{esc(card['detail'])}</p>
              <p class="exact">Exact values: {esc(card['exact'])}</p>
            </article>"""
        )
    cols = min(max(len(cards), 1), 5)
    ident = f' id="{esc(section_id)}"' if section_id else ""
    return f'<section class="kpis"{ident} style="--cols: {cols}">{"".join(blocks)}</section>'


def _text_width(text: str, font_size: float) -> float:
    """Conservative width in SVG user units so labels are not clipped or overlapping bars."""
    return len(text) * font_size * 0.62


def _project(lon: float, lat: float, width: float, height: float) -> tuple[float, float]:
    x = (lon - 112.0) / (154.0 - 112.0) * (width - 36) + 18
    y = ((-10.0) - lat) / ((-10.0) - (-44.0)) * (height - 36) + 18
    return x, y


def _ring_path(ring: list, width: float, height: float) -> str:
    commands = []
    for index, pair in enumerate(ring):
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        x, y = _project(float(pair[0]), float(pair[1]), width, height)
        commands.append(f"{'M' if index == 0 else 'L'}{x:.1f},{y:.1f}")
    if commands:
        commands.append("Z")
    return " ".join(commands)


def _geometry_paths(geometry: dict, width: float, height: float) -> list[str]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    paths = []
    if kind == "Polygon":
        for ring in coordinates:
            paths.append(_ring_path(ring, width, height))
    elif kind == "MultiPolygon":
        for polygon in coordinates:
            for ring in polygon:
                paths.append(_ring_path(ring, width, height))
    return [path for path in paths if path]


def _map_svg(briefing: dict, root: Path) -> str:
    width, height = 760, 640
    geo_path = root / "data" / "snapshot" / "australia_states.geojson"
    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    shapes = []
    for feature in geo.get("features", []):
        for path in _geometry_paths(feature.get("geometry") or {}, width, height):
            shapes.append(f'<path d="{path}" fill="#13202c" stroke="#3d5368" stroke-width="1"/>')
    dots = []
    plotted = 0
    capacities = [storage["capacity_ml"] for storage in briefing["storages"] if storage.get("capacity_ml")]
    biggest = max(capacities) if capacities else 1
    for storage in briefing["storages"]:
        if storage.get("latitude") is None or storage.get("longitude") is None:
            continue
        x, y = _project(float(storage["longitude"]), float(storage["latitude"]), width, height)
        radius = 4 + 9 * (float(storage["capacity_ml"]) / biggest) ** 0.5
        colour = _status_colour(storage.get("status") or "")
        label = f"{storage['place']}, {storage['percent_full']:.1f}% full"
        dots.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{colour}" fill-opacity="0.92" stroke="#0c1218" stroke-width="1">'
            f"<title>{esc(label)}</title></circle>"
        )
        plotted += 1
    missing = len(briefing["storages"]) - plotted
    legend = (
        '<g font-size="13">'
        '<circle cx="24" cy="24" r="6" fill="#ff5c7a"/><text x="36" y="28">Under 40%, or on a dry path to get there</text>'
        '<circle cx="24" cy="46" r="6" fill="#ffc14d"/><text x="36" y="50">Between 40% and 60%, or slipping this summer</text>'
        '<circle cx="24" cy="68" r="6" fill="#2ee6a6"/><text x="36" y="72">Above 60% and holding on a dry summer</text>'
        "</g>"
    )
    note = f"{missing} tracked supplies have no published gauge position, so they are listed rather than mapped."
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Map of tracked storages coloured by how full they are">'
        f"{''.join(shapes)}{''.join(dots)}{legend}</svg>"
        f'<p class="exact">Exact values: dot size grows with accessible capacity. {esc(note)} '
        f"Positions are the Bureau gauge matched to the storage name. Snapshot {esc(briefing['snapshot_display'])}.</p>"
    )


def _state_chart(states: list[dict]) -> str:
    rows = [row for row in states if row.get("percent_full") is not None and (row.get("capacity_ml") or 0) > 0]
    rows.sort(key=lambda row: row["percent_full"])
    if not rows:
        return ""
    label_font, value_font = 14, 13
    width, row_h = 760, 40
    label_w = max(_text_width(str(row["name"]), label_font) for row in rows)
    left = 12 + label_w + 18
    max_x = width - 72
    height = 16 + row_h * len(rows)
    parts = [
        f'<svg class="chart state-chart" viewBox="0 0 {width} {height}" role="img" aria-label="How full each state is">'
    ]
    for index, row in enumerate(rows):
        y = 8 + index * row_h
        percent = float(row["percent_full"])
        bar = left + (max_x - left) * max(0.0, min(percent, 100)) / 100
        colour = "#ff5c7a" if percent < 40 else "#ffc14d" if percent < 60 else "#2ee6a6"
        parts.append(f'<text x="8" y="{y + 20}" font-size="{label_font}">{esc(row["name"])}</text>')
        parts.append(
            f'<rect x="{left}" y="{y + 8}" width="{max(bar - left, 0):.1f}" height="16" rx="4" fill="{colour}"/>'
        )
        parts.append(f'<text x="{bar + 10:.1f}" y="{y + 20}" font-size="{value_font}">{percent:.1f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def _tight_rows(storages: list[dict]) -> list[dict]:
    return [storage for storage in storages if storage.get("role") != "hydro"]


def _tight_chart(storages: list[dict]) -> str:
    rows = _tight_rows(storages)[:12]
    if not rows:
        return ""
    label_font, value_font = 13, 13
    width, row_h = 760, 36
    label_w = max(_text_width(storage["place"], label_font) for storage in rows)
    left = 12 + label_w + 16
    height = 16 + row_h * len(rows)
    parts = [
        f'<svg class="chart tight-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Days until the 40 percent line on a dry summer">'
    ]
    span = width - left - 120
    for index, storage in enumerate(rows):
        y = 8 + index * row_h
        days = storage.get("days_dry")
        if days is None:
            fraction = 1
            label = "Holds"
        else:
            fraction = max(0.0, min(float(days), 140)) / 140
            label = _days(days)
        colour = _status_colour(storage.get("status") or "")
        bar = left + span * fraction
        parts.append(f'<text x="8" y="{y + 18}" font-size="{label_font}">{esc(storage["place"])}</text>')
        parts.append(
            f'<rect x="{left}" y="{y + 6}" width="{max(bar - left, 2):.1f}" height="16" rx="4" fill="{colour}">'
            f"<title>{esc(storage['place'])} {label}</title></rect>"
        )
        parts.append(f'<text x="{bar + 10:.1f}" y="{y + 18}" font-size="{value_font}">{esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def displayed_percents(storage: dict, path: dict) -> list[float]:
    capacity = storage.get("capacity_ml") or 1
    values: list[float] = []
    for key in ("history", "dry", "typical", "wet"):
        for row in path.get(key) or []:
            values.append(max(row["volume_ml"], 0) / capacity * 100)
    for row in path.get("band") or []:
        values.append(max(row.get("p10_ml") or 0, 0) / capacity * 100)
        values.append(max(row.get("p90_ml") or 0, 0) / capacity * 100)
    return values


def percent_axis(values: list[float]) -> tuple[float, float]:
    """Fit the axis to the paths. A near-empty supply is not drawn on a 0–100 scale."""
    if not values:
        return 0.0, 8.0
    data_max = max(values)
    data_min = min(values)
    span = max(data_max - data_min, 1.0)
    pad = max(1.0, span * 0.18)
    low = 0.0 if data_min >= -0.05 else data_min - pad
    high = data_max + pad
    if data_max >= 80:
        high = max(high, 100.0)
    elif data_max + 12 >= 40:
        high = max(high, 46.0)
    if high - low < 8:
        high = low + 8
    return low, high


def axis_ticks(low: float, high: float) -> list[float]:
    span = high - low
    step = 25.0
    for candidate in (1, 2, 5, 10, 20, 25):
        if span / candidate <= 6:
            step = float(candidate)
            break
    value = math.ceil((low - 1e-9) / step) * step
    ticks: list[float] = []
    while value <= high + 1e-6:
        ticks.append(float(value))
        value += step
    return ticks


def scale_note(high: float) -> str:
    if high < 40:
        return "The scale is zoomed to these paths, which sit near empty. The 40% line is above this chart."
    return "The dashed line is 40% of accessible capacity."


def _series_label(name: str, percent: float, span: float) -> str:
    if percent < 0.05:
        shown = "0%"
    elif span < 30:
        shown = f"{percent:.1f}%"
    else:
        shown = f"{percent:.0f}%"
    return f"{name} {shown}"


def _spread_ys(ys: list[float], gap: float, min_y: float, max_y: float) -> list[float]:
    if not ys:
        return []
    order = sorted(range(len(ys)), key=lambda index: ys[index])
    placed = [ys[index] for index in order]
    for index in range(1, len(placed)):
        placed[index] = max(placed[index], placed[index - 1] + gap)
    if placed[-1] > max_y:
        shift = placed[-1] - max_y
        placed = [item - shift for item in placed]
    if placed[0] < min_y:
        if len(placed) == 1:
            placed = [min(max(placed[0], min_y), max_y)]
        else:
            step = (max_y - min_y) / (len(placed) - 1)
            placed = [min_y + step * index for index in range(len(placed))]
    result = [0.0] * len(ys)
    for position, index in enumerate(order):
        result[index] = placed[position]
    return result


def _line_chart(storage: dict, path: dict) -> str:
    capacity = storage.get("capacity_ml") or 1
    series = (
        ("History", path.get("history") or [], "#d6e4f0"),
        ("Dry summer", path.get("dry") or [], "#ff5c7a"),
        ("Typical summer", path.get("typical") or [], "#ffc14d"),
        ("Wet summer", path.get("wet") or [], "#2ee6a6"),
    )
    band = path.get("band") or []
    values = displayed_percents(storage, path)
    if len(values) < 2:
        return "<p>Not enough daily readings to draw this path.</p>"
    dates = sorted(
        {row["date"] for _name, rows, _colour in series for row in rows}
        | {row["date"] for row in band}
    )
    index = {day: position for position, day in enumerate(dates)}
    low, high = percent_axis(values)
    span = high - low
    width, height = 760, 440
    left, right, top, bottom = 56, 188, 28, 42

    def xy(day: str, percent: float) -> tuple[float, float]:
        x = left + (index[day] / max(len(dates) - 1, 1)) * (width - left - right)
        y = top + (high - percent) / (high - low) * (height - top - bottom)
        return x, y

    parts = [
        f'<svg class="chart scenario-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Summer paths for {esc(storage["place"])}">'
    ]
    plot_bottom = height - bottom
    if low <= 40 <= high:
        y40 = xy(dates[0], 40)[1]
        parts.append(
            f'<line x1="{left}" y1="{y40:.1f}" x2="{width - right}" y2="{y40:.1f}" stroke="#ff5c7a" stroke-dasharray="4 4"/>'
        )
        parts.append(f'<text x="{left}" y="{y40 - 14:.1f}" font-size="13" fill="#ff5c7a">40% line</text>')
    for tick in axis_ticks(low, high):
        y = xy(dates[0], tick)[1]
        if low <= 40 <= high and abs(y - xy(dates[0], 40)[1]) < 16:
            continue
        parts.append(f'<text x="8" y="{y + 4:.1f}" font-size="13" class="muted-text">{tick:.0f}%</text>')
    if band:
        polygon = []
        for row in band:
            polygon.append(xy(row["date"], max(row["p10_ml"], 0) / capacity * 100))
        for row in reversed(band):
            polygon.append(xy(row["date"], max(row["p90_ml"], 0) / capacity * 100))
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in polygon)
        parts.append(f'<polygon points="{poly}" fill="#7ec8ff" fill-opacity="0.28" stroke="#7ec8ff" stroke-width="1"/>')
    history_label = None
    gutter: list[tuple[float, str, str]] = []
    for name, rows, colour in series:
        if len(rows) < 2:
            continue
        commands = []
        last = None
        for row in rows:
            percent = max(row["volume_ml"], 0) / capacity * 100
            x, y = xy(row["date"], percent)
            commands.append(f"{'M' if not commands else 'L'}{x:.1f},{y:.1f}")
            last = (x, y, percent)
        parts.append(f'<path d="{" ".join(commands)}" fill="none" stroke="{colour}" stroke-width="2.4"/>')
        if last is None:
            continue
        text = _series_label(name, last[2], span)
        if name == "History":
            history_label = (last[0], last[1], text, colour)
        else:
            gutter.append((last[1], text, colour))
    if history_label is not None:
        hx, hy, text, colour = history_label
        label_y = hy - 24
        if label_y < top + 14:
            label_y = min(hy + 24, plot_bottom - 6)
        label_x = max(hx - 8, left + _text_width(text, 13) + 4)
        parts.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="end" font-size="13" fill="{colour}">{esc(text)}</text>'
        )
    spread = _spread_ys([item[0] for item in gutter], 22, top + 14, plot_bottom - 6)
    gutter_x = width - right + 12
    for ( _y, text, colour), y in zip(gutter, spread, strict=False):
        parts.append(f'<text x="{gutter_x:.1f}" y="{y:.1f}" font-size="13" fill="{colour}">{esc(text)}</text>')
    if dates:
        parts.append(f'<text x="{left}" y="{height - 16}" font-size="13" class="muted-text">{esc(dates[0])}</text>')
        end_x = max(left + 80, width - right - _text_width(dates[-1], 13) - 8)
        parts.append(f'<text x="{end_x:.1f}" y="{height - 16}" font-size="13" class="muted-text">{esc(dates[-1])}</text>')
    parts.append("</svg>")
    parts.append(
        '<div class="legend">'
        '<span><i class="swatch" style="background:#d6e4f0"></i>Recent readings</span>'
        '<span><i class="swatch" style="background:#ff5c7a"></i>Dry summer</span>'
        '<span><i class="swatch" style="background:#ffc14d"></i>Typical summer</span>'
        '<span><i class="swatch" style="background:#2ee6a6"></i>Wet summer</span>'
        '<span><i class="swatch" style="background:#7ec8ff"></i>Likely range, next 64 days</span>'
        "</div>"
    )
    return "".join(parts)


def _chart_geography(name: str) -> str:
    if name == "Australian Capital Territory":
        return "ACT"
    return name


def _lever_chart(levers: list[dict]) -> str:
    rows = gap_states(levers)
    if not rows:
        return "<p>No state has a town-supply gap to draw. The table below still lists every state.</p>"
    label_font, value_font = 14, 13
    labels = [_chart_geography(row["geography"]) for row in rows]
    width = 760
    left = 12 + max(_text_width(label, label_font) for label in labels) + 20
    slot_h = 22
    group_h = 16 + slot_h * 4
    height = 24 + group_h * len(rows)
    biggest = 1.0
    for row in rows:
        for key in ("urban_gap_ml", "restriction_ml", "desal_ml", "reuse_ml"):
            biggest = max(biggest, row.get(key) or 0)
    parts = [
        f'<svg class="chart lever-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Town-supply gap against three what-if responses">'
    ]
    colours = (
        ("urban_gap_ml", "#ff5c7a", "Gap"),
        ("restriction_ml", "#ffc14d", "15% household cut"),
        ("desal_ml", "#7ec8ff", "Desalination at last year’s pace"),
        ("reuse_ml", "#2ee6a6", "Recycled water at last year’s pace"),
    )
    span = width - left - 84
    for index, row in enumerate(rows):
        top = 12 + index * group_h
        parts.append(
            f'<text x="8" y="{top + group_h / 2:.0f}" font-size="{label_font}">{esc(labels[index])}</text>'
        )
        for slot, (key, colour, _label) in enumerate(colours):
            value = row.get(key) or 0
            if value <= 0:
                continue
            y = top + 6 + slot * slot_h
            bar = left + span * (value / biggest)
            parts.append(
                f'<rect x="{left}" y="{y}" width="{max(bar - left, 2):.1f}" height="14" rx="3" fill="{colour}"/>'
            )
            parts.append(
                f'<text x="{bar + 8:.1f}" y="{y + 12}" font-size="{value_font}">{value / 1000:,.0f} GL</text>'
            )
    parts.append("</svg>")
    legend = "".join(
        f'<span><i class="swatch" style="background:{colour}"></i>{esc(label)}</span>' for _key, colour, label in colours
    )
    parts.append(f'<div class="legend">{legend}</div>')
    return "".join(parts)


def _means(title: str, text: str) -> str:
    return f'<aside class="means"><strong>What this means</strong>{esc(text)}</aside>'


def _banner(briefing: dict, title: str | None = None, lede: str | None = None) -> str:
    heading = briefing["headline"] if title is None else title
    standfirst = briefing["lede"] if lede is None else lede
    return (
        '<section class="banner">'
        f'<p class="eyebrow">Australia · summer 2026–27 · as at {esc(briefing["as_of_display"])}</p>'
        f'<h1>{esc(heading)}</h1>'
        f'<p class="lede">{esc(standfirst)}</p>'
        "</section>"
    )


def _outlook(briefing: dict, root: Path) -> str:
    drainage = [row for row in briefing["drainage"] if row.get("percent_full") is not None and (row.get("capacity_ml") or 0) > 0]
    extra = ""
    if drainage:
        lowest = min(drainage, key=lambda row: row["percent_full"])
        last = lowest.get("percent_last_year")
        change = ""
        if last is not None:
            change = f" A year ago it was {_pct(last)}."
        extra = f" The driest drainage division on the Bureau’s books is {lowest['name']} at {_pct(lowest['percent_full'])}.{change}"
    body = (
        _banner(briefing)
        + _kpis(briefing["kpis"])
        + '<section class="layout"><div class="panel map-panel"><h2>Where the tracked supplies stand</h2>'
        + _map_svg(briefing, root)
        + '</div><div class="panel"><h2>Whole-of-state storages</h2>'
        + _state_chart(briefing["states"])
        + '<p class="exact">Exact values are the percent labels. These are every storage the Bureau rolls up, not only the 64 followed day by day.</p>'
        + "</div></section>"
        + _means("What this means", briefing["means"]["outlook"] + extra)
    )
    return _shell("index.html", body, briefing)


def _table(storages: list[dict]) -> str:
    rows = []
    for storage in storages:
        rows.append(
            "<tr>"
            f"<td>{esc(storage['place'])}</td>"
            f"<td>{esc(storage['state'])}</td>"
            f"<td>{esc({'urban': 'Town supply', 'irrigation': 'Farming supply', 'hydro': 'Power-station lake', 'regional': 'Regional supply'}.get(storage['role'], 'Regional supply'))}</td>"
            f"<td class='num'>{_pct(storage.get('percent_full'))}</td>"
            f"<td class='num'>{_days(storage.get('days_dry'))}</td>"
            f"<td class='num'>{_days(storage.get('days_recent'))}</td>"
            "</tr>"
        )
    return (
        "<table id='tight-table'><thead><tr>"
        "<th>Supply</th><th>State</th><th>Kind</th>"
        "<th class='num'>Full now</th><th class='num'>Dry-summer buffer</th><th class='num'>If last month continues</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _tight(briefing: dict) -> str:
    states = ["All"] + sorted({storage["state"] for storage in briefing["storages"]})
    pills = "".join(
        f'<button class="pill{" active" if name == "All" else ""}" type="button" data-state="{esc(name)}">{esc(name)}</button>'
        for name in states
    )
    payload = json.dumps(
        [
            {
                "place": storage["place"],
                "state": storage["state"],
                "role": storage["role"],
                "percent_full": storage.get("percent_full"),
                "days_dry": storage.get("days_dry"),
                "days_recent": storage.get("days_recent"),
                "status": storage.get("status"),
                "capacity_ml": storage.get("capacity_ml"),
                "volume_ml": storage.get("volume_ml"),
            }
            for storage in briefing["storages"]
        ]
    )
    script = f"""
    <script id="tight-data" type="application/json">{payload}</script>
    <script>
    const data = JSON.parse(document.getElementById('tight-data').textContent);
    const pills = document.querySelectorAll('.pill');
    function draw(state) {{
      const rows = data.filter(row => state === 'All' || row.state === state);
      const body = document.querySelector('#tight-table tbody');
      const kind = {{urban:'Town supply', irrigation:'Farming supply', hydro:'Power-station lake', regional:'Regional supply'}};
      const days = value => value === null || value === undefined ? 'Holds' : (value <= 0 ? 'Under the line' : Math.round(value) + ' days');
      const pct = value => value === null || value === undefined ? '–' : value.toFixed(1) + '%';
      body.innerHTML = rows.map(row => '<tr><td>' + row.place + '</td><td>' + row.state + '</td><td>' + (kind[row.role] || 'Regional supply') + '</td><td class="num">' + pct(row.percent_full) + '</td><td class="num">' + days(row.days_dry) + '</td><td class="num">' + days(row.days_recent) + '</td></tr>').join('');
      const chartRows = rows.filter(row => row.role !== 'hydro').slice(0, 12);
      const width = 760, rowH = 36, font = 13;
      const longest = chartRows.reduce((size, row) => Math.max(size, row.place.length), 8);
      const left = 12 + longest * font * 0.62 + 16;
      const height = 16 + rowH * Math.max(chartRows.length, 1);
      const span = width - left - 120;
      let svg = '<svg class="chart tight-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Days until the 40 percent line on a dry summer">';
      chartRows.forEach((row, index) => {{
        const y = 8 + index * rowH;
        const fraction = row.days_dry === null || row.days_dry === undefined ? 1 : Math.max(0, Math.min(row.days_dry, 140)) / 140;
        const colour = row.status === 'comfortable' ? '#2ee6a6' : (row.status === 'watch' ? '#ffc14d' : '#ff5c7a');
        const bar = left + span * fraction;
        const label = days(row.days_dry);
        svg += '<text x="8" y="' + (y + 18) + '" font-size="13">' + row.place + '</text>';
        svg += '<rect x="' + left + '" y="' + (y + 6) + '" width="' + Math.max(bar - left, 2) + '" height="16" rx="4" fill="' + colour + '"></rect>';
        svg += '<text x="' + (bar + 10) + '" y="' + (y + 18) + '" font-size="13">' + label + '</text>';
      }});
      svg += '</svg>';
      document.getElementById('tight-chart').innerHTML = svg;
    }}
    pills.forEach(pill => pill.addEventListener('click', () => {{
      pills.forEach(item => item.classList.remove('active'));
      pill.classList.add('active');
      draw(pill.dataset.state);
    }}));
    </script>
    """
    title, lede = tight_story(briefing["storages"])
    body = (
        _banner(briefing, title, lede)
        + _kpis(tight_kpis(briefing["storages"]))
        + '<section class="panel"><h2>Days above the 40% line if the driest recent summer repeats</h2>'
        + f'<div class="pills" role="group" aria-label="Filter by state">{pills}</div>'
        + f'<div id="tight-chart">{_tight_chart(briefing["storages"])}</div>'
        + '<p class="exact">Exact values: '
        + esc(under_line_exact(briefing["storages"]))
        + " “Holds” means the storage is not falling toward 40% on that path. "
        + "A bar at the right edge is 140 days, which is past the end of March. "
        + "The chart uses this same count. Power-station lakes stay in the table below, which lists every supply followed day by day.</p>"
        + _table(briefing["storages"])
        + "</section>"
        + _means("What this means", briefing["means"]["tight"])
        + script
    )
    return _shell("tight.html", body, briefing)


def _scenarios(briefing: dict) -> str:
    featured = next((storage for storage in briefing["storages"] if storage["storage_id"] == briefing["featured_id"]), None)
    choices = briefing["storages"][:6]
    pills = []
    for storage in choices:
        active = " active" if featured and storage["storage_id"] == featured["storage_id"] else ""
        pills.append(
            f'<button class="pill{active}" type="button" data-id="{esc(storage["storage_id"])}">{esc(storage["place"])}</button>'
        )
    chart = ""
    if featured:
        chart = _line_chart(featured, briefing["paths"].get(featured["storage_id"], {}))
    payload = {}
    for storage in choices:
        path = briefing["paths"].get(storage["storage_id"], {})
        _low, high = percent_axis(displayed_percents(storage, path))
        payload[storage["storage_id"]] = {
            "place": storage["place"],
            "capacity_ml": storage.get("capacity_ml"),
            "path": path,
            "percent_full": storage.get("percent_full"),
            "end_percent_dry": storage.get("end_percent_dry"),
            "end_percent_typical": storage.get("end_percent_typical"),
            "end_percent_wet": storage.get("end_percent_wet"),
            "cards": scenario_kpis(storage),
            "means": scenario_takeaway(storage),
            "scale_note": scale_note(high),
        }
    script = """
    <script id="scenario-data" type="application/json">PAYLOAD</script>
    <script>
    const packs = JSON.parse(document.getElementById('scenario-data').textContent);
    document.querySelectorAll('[data-id]').forEach(pill => pill.addEventListener('click', () => {
      document.querySelectorAll('[data-id]').forEach(item => item.classList.remove('active'));
      pill.classList.add('active');
      const pack = packs[pill.dataset.id];
      document.getElementById('scenario-title').textContent = pack.place;
      document.getElementById('scenario-chart').innerHTML = window.renderScenario(pack);
      document.getElementById('scenario-means').textContent = pack.means;
      const scale = document.getElementById('scenario-scale');
      if (scale) scale.textContent = pack.scale_note;
      const cards = document.querySelectorAll('#scenario-kpis .kpi');
      (pack.cards || []).forEach((card, index) => {
        const node = cards[index];
        if (!node) return;
        node.className = 'kpi ' + card.tone;
        node.querySelector('.label').textContent = card.label;
        node.querySelector('.value').textContent = card.value;
        node.querySelector('.detail').textContent = card.detail;
        node.querySelector('.exact').textContent = 'Exact values: ' + card.exact;
      });
    }));
    window.renderScenario = function(pack) {
      const capacity = pack.capacity_ml || 1;
      const series = [
        ['History', (pack.path.history || []), '#d6e4f0', true],
        ['Dry summer', (pack.path.dry || []), '#ff5c7a', false],
        ['Typical summer', (pack.path.typical || []), '#ffc14d', false],
        ['Wet summer', (pack.path.wet || []), '#2ee6a6', false]
      ];
      const band = pack.path.band || [];
      const dates = [];
      function add(day) { if (dates.indexOf(day) === -1) dates.push(day); }
      series.forEach(item => item[1].forEach(row => add(row.date)));
      band.forEach(row => add(row.date));
      dates.sort();
      if (dates.length < 2) return '<p>Not enough daily readings to draw this path.</p>';
      const values = [];
      series.forEach(item => item[1].forEach(row => values.push(Math.max(row.volume_ml, 0) / capacity * 100)));
      band.forEach(row => {
        values.push(Math.max(row.p10_ml || 0, 0) / capacity * 100);
        values.push(Math.max(row.p90_ml || 0, 0) / capacity * 100);
      });
      const dataMax = Math.max.apply(null, values);
      const dataMin = Math.min.apply(null, values);
      const dataSpan = Math.max(dataMax - dataMin, 1);
      const pad = Math.max(1, dataSpan * 0.18);
      let low = dataMin >= -0.05 ? 0 : dataMin - pad;
      let high = dataMax + pad;
      if (dataMax >= 80) high = Math.max(high, 100);
      else if (dataMax + 12 >= 40) high = Math.max(high, 46);
      if (high - low < 8) high = low + 8;
      const span = high - low;
      const width = 760, height = 440, left = 56, right = 188, top = 28, bottom = 42;
      const index = {};
      dates.forEach((day, position) => { index[day] = position; });
      const xy = (day, percent) => {
        const x = left + (index[day] / (dates.length - 1)) * (width - left - right);
        const y = top + (high - percent) / span * (height - top - bottom);
        return [x, y];
      };
      const plotBottom = height - bottom;
      let svg = '<svg class="chart scenario-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Summer paths">';
      const showLine = low <= 40 && 40 <= high;
      const y40 = xy(dates[0], 40)[1];
      if (showLine) {
        svg += '<line x1="' + left + '" y1="' + y40 + '" x2="' + (width - right) + '" y2="' + y40 + '" stroke="#ff5c7a" stroke-dasharray="4 4"/>';
        svg += '<text x="' + left + '" y="' + (y40 - 14) + '" font-size="13" fill="#ff5c7a">40% line</text>';
      }
      let step = 25;
      const steps = [1, 2, 5, 10, 20, 25];
      for (let s = 0; s < steps.length; s++) {
        if (span / steps[s] <= 6) { step = steps[s]; break; }
      }
      for (let tick = Math.ceil((low - 1e-9) / step) * step; tick <= high + 1e-6; tick += step) {
        const y = xy(dates[0], tick)[1];
        if (showLine && Math.abs(y - y40) < 16) continue;
        svg += '<text x="8" y="' + (y + 4) + '" font-size="13" class="muted-text">' + Math.round(tick) + '%</text>';
      }
      if (band.length) {
        const poly = [];
        band.forEach(row => poly.push(xy(row.date, Math.max(row.p10_ml || 0, 0) / capacity * 100)));
        band.slice().reverse().forEach(row => poly.push(xy(row.date, Math.max(row.p90_ml || 0, 0) / capacity * 100)));
        svg += '<polygon points="' + poly.map(pair => pair[0].toFixed(1) + ',' + pair[1].toFixed(1)).join(' ') + '" fill="#7ec8ff" fill-opacity="0.28" stroke="#7ec8ff" stroke-width="1"/>';
      }
      function seriesLabel(name, percent) {
        const shown = percent < 0.05 ? '0%' : (span < 30 ? percent.toFixed(1) + '%' : Math.round(percent) + '%');
        return name + ' ' + shown;
      }
      let history = null;
      const gutter = [];
      series.forEach(item => {
        if (item[1].length < 2) return;
        let d = '';
        let last = null;
        item[1].forEach(row => {
          const percent = Math.max(row.volume_ml, 0) / capacity * 100;
          const pair = xy(row.date, percent);
          d += (d ? 'L' : 'M') + pair[0].toFixed(1) + ',' + pair[1].toFixed(1);
          last = [pair[0], pair[1], percent];
        });
        svg += '<path d="' + d + '" fill="none" stroke="' + item[2] + '" stroke-width="2.4"/>';
        if (!last) return;
        const text = seriesLabel(item[0], last[2]);
        if (item[3]) history = [last[0], last[1], text, item[2]];
        else gutter.push([last[1], text, item[2]]);
      });
      if (history) {
        let labelY = history[1] - 24;
        if (labelY < top + 14) labelY = Math.min(history[1] + 24, plotBottom - 6);
        const textW = history[2].length * 13 * 0.62;
        const labelX = Math.max(history[0] - 8, left + textW + 4);
        svg += '<text x="' + labelX + '" y="' + labelY + '" text-anchor="end" font-size="13" fill="' + history[3] + '">' + history[2] + '</text>';
      }
      const order = gutter.map((_item, i) => i).sort((a, b) => gutter[a][0] - gutter[b][0]);
      const placed = order.map(i => gutter[i][0]);
      for (let i = 1; i < placed.length; i++) placed[i] = Math.max(placed[i], placed[i - 1] + 22);
      const maxY = plotBottom - 6, minY = top + 14;
      if (placed.length && placed[placed.length - 1] > maxY) {
        const shift = placed[placed.length - 1] - maxY;
        for (let i = 0; i < placed.length; i++) placed[i] -= shift;
      }
      if (placed.length && placed[0] < minY) {
        if (placed.length === 1) placed[0] = Math.min(Math.max(placed[0], minY), maxY);
        else {
          const stepY = (maxY - minY) / (placed.length - 1);
          for (let i = 0; i < placed.length; i++) placed[i] = minY + stepY * i;
        }
      }
      const ys = new Array(gutter.length);
      order.forEach((source, position) => { ys[source] = placed[position]; });
      const gutterX = width - right + 12;
      gutter.forEach((item, i) => {
        svg += '<text x="' + gutterX + '" y="' + ys[i] + '" font-size="13" fill="' + item[2] + '">' + item[1] + '</text>';
      });
      svg += '<text x="' + left + '" y="' + (height - 16) + '" font-size="13" class="muted-text">' + dates[0] + '</text>';
      const endLabel = dates[dates.length - 1];
      const endX = Math.max(left + 80, width - right - endLabel.length * 13 * 0.62 - 8);
      svg += '<text x="' + endX + '" y="' + (height - 16) + '" font-size="13" class="muted-text">' + endLabel + '</text>';
      svg += '</svg>';
      return svg;
    };
    </script>
    """.replace("PAYLOAD", json.dumps(payload).replace("<", "\\u003c"))
    heading = scenario_title(featured)
    lede = scenario_takeaway(featured)
    note = ""
    if featured:
        _low, high = percent_axis(displayed_percents(featured, briefing["paths"].get(featured["storage_id"], {})))
        note = scale_note(high)
    body = (
        _banner(briefing, heading, lede)
        + (_kpis(scenario_kpis(featured), "scenario-kpis") if featured else "")
        + '<section class="panel">'
        + f'<h2 id="scenario-title">{esc(featured["place"] if featured else "Summer paths")}</h2>'
        + f'<div class="pills">{"".join(pills)}</div>'
        + f'<div id="scenario-chart">{chart}</div>'
        + '<p class="exact">Exact values: the lines are percent of accessible capacity. '
        + '<span id="scenario-scale">'
        + esc(note)
        + "</span> "
        + "The shaded band is the likely range for the next 64 days, which runs past Christmas into late January. "
        + "Paths after that replay a whole summer’s average daily change. Volumes are not allowed to change the 40% line, which stays at two-fifths of capacity.</p>"
        + "</section>"
        + f'<aside class="means"><strong>What this means</strong><span id="scenario-means">{esc(briefing["means"]["scenarios"])}</span></aside>'
        + script
    )
    return _shell("scenarios.html", body, briefing)


def _levers(briefing: dict) -> str:
    rows = []
    for row in briefing["levers"]:
        if row["geography"] == "Australia":
            continue
        def share(value: float | None) -> str:
            if value is None:
                return "–"
            return f"{value * 100:.0f}%"
        rows.append(
            "<tr>"
            f"<td>{esc(row['geography'])}</td>"
            f"<td class='num'>{(row.get('urban_gap_ml') or 0) / 1000:,.1f}</td>"
            f"<td class='num'>{(row.get('irrigation_gap_ml') or 0) / 1000:,.1f}</td>"
            f"<td class='num'>{share(row.get('restriction_share'))}</td>"
            f"<td class='num'>{share(row.get('desal_share'))}</td>"
            f"<td class='num'>{share(row.get('reuse_share'))}</td>"
            "</tr>"
        )
    australia = next(row for row in briefing["accounts"] if row["geography"] == "Australia")
    national = (
        f"Last year, households used {(australia.get('household_ml') or 0) / 1000:,.0f} billion litres, "
        f"desalination plants supplied {(australia.get('desal_ml') or 0) / 1000:,.0f} billion litres "
        f"and recycled water supplied {(australia.get('reuse_supply_ml') or 0) / 1000:,.0f} billion litres."
    )
    gaps = gap_states(briefing["levers"])
    if gaps:
        focus = max(gaps, key=lambda row: row.get("urban_gap_ml") or 0)
        lede = (
            f"The chart shows only states whose town supplies finish March under 40%. "
            f"{focus['geography']} is the largest of those gaps."
        )
    else:
        lede = "No tracked town supply finishes March under the 40% line on a dry summer."
    body = (
        _banner(briefing, lever_title(briefing["levers"]), lede)
        + _kpis(lever_kpis(briefing["levers"]))
        + '<section class="panel"><h2>Town-supply gap to the end of March, and three ways to close it</h2>'
        + _lever_chart(briefing["levers"])
        + f"<p>{esc(national)}</p>"
        + "<table><thead><tr><th>State</th><th class='num'>Town gap (GL)</th><th class='num'>Farming gap (GL)</th>"
        + "<th class='num'>15% household cut</th><th class='num'>Desalination</th><th class='num'>Recycled water</th>"
        + f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        + '<p class="exact">Exact values: 1 GL is a billion litres. Shares are how much of the town-supply gap that what-if would cover, capped at 100%. '
        + "The household cut is 15% of last year’s household use, scaled to the days left until 31 March 2027. "
        + "Desalination and recycled water are last year’s annual supply, scaled to the same days. "
        + "The chart draws only states with a town-supply gap, and shortens Australian Capital Territory to ACT. "
        + "The table lists every state, including those with no gap. "
        + "A dash means there is no town-supply gap on the dry path, so a share is not meaningful. "
        + "Farming storages are shown beside the town gap and are not something a city’s desalination plant can fill.</p>"
        + "</section>"
        + _means("What this means", briefing["means"]["levers"])
    )
    return _shell("levers.html", body, briefing)


def render_site(briefing: dict, site: Path) -> None:
    site.mkdir(parents=True, exist_ok=True)
    assets = site / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / "style.css", assets / "style.css")
    root = Path(__file__).resolve().parents[2]
    (site / "index.html").write_text(_outlook(briefing, root), encoding="utf-8")
    (site / "tight.html").write_text(_tight(briefing), encoding="utf-8")
    (site / "scenarios.html").write_text(_scenarios(briefing), encoding="utf-8")
    (site / "levers.html").write_text(_levers(briefing), encoding="utf-8")
