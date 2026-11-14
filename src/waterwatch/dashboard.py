"""Static tabbed pages for ministers, utilities and councils."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

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


def _kpis(cards: list[dict]) -> str:
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
    return f'<section class="kpis">{"".join(blocks)}</section>'


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
    rows = [row for row in states if row.get("percent_full") is not None and row.get("capacity_ml")]
    rows = [row for row in rows if (row.get("capacity_ml") or 0) > 0]
    rows.sort(key=lambda row: row["percent_full"])
    if not rows:
        return ""
    width = 760
    row_h = 36
    left = 210
    height = 28 + row_h * len(rows)
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="How full each state is">']
    max_x = width - 80
    for index, row in enumerate(rows):
        y = 16 + index * row_h
        percent = float(row["percent_full"])
        bar = left + (max_x - left) * max(0.0, min(percent, 100)) / 100
        colour = "#ff5c7a" if percent < 40 else "#ffc14d" if percent < 60 else "#2ee6a6"
        parts.append(f'<text x="8" y="{y + 16}" font-size="14">{esc(row["name"])}</text>')
        parts.append(f'<rect x="{left}" y="{y + 4}" width="{max(bar - left, 0):.1f}" height="16" rx="4" fill="{colour}"/>')
        parts.append(f'<text x="{bar + 8:.1f}" y="{y + 16}" font-size="13">{percent:.1f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def _tight_chart(storages: list[dict]) -> str:
    rows = storages[:12]
    if not rows:
        return ""
    width = 760
    row_h = 34
    left = 168
    height = 20 + row_h * len(rows)
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Days until the 40 percent line on a dry summer">'
    ]
    span = width - left - 90
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
        parts.append(f'<text x="8" y="{y + 16}" font-size="13">{esc(storage["place"][:22])}</text>')
        parts.append(
            f'<rect x="{left}" y="{y + 4}" width="{max(bar - left, 2):.1f}" height="16" rx="4" fill="{colour}">'
            f"<title>{esc(storage['place'])} {label}</title></rect>"
        )
        parts.append(f'<text x="{bar + 8:.1f}" y="{y + 16}" font-size="13">{esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _line_chart(storage: dict, path: dict) -> str:
    capacity = storage.get("capacity_ml") or 1
    series = {
        "History": (path.get("history") or [], "#d6e4f0", False),
        "Dry summer": (path.get("dry") or [], "#ff5c7a", False),
        "Typical summer": (path.get("typical") or [], "#ffc14d", False),
        "Wet summer": (path.get("wet") or [], "#2ee6a6", False),
    }
    band = path.get("band") or []
    points: list[tuple[str, float]] = []
    for rows, _colour, _flag in series.values():
        for row in rows:
            points.append((row["date"], row["volume_ml"] / capacity * 100))
    for row in band:
        points.append((row["date"], row["p10_ml"] / capacity * 100))
        points.append((row["date"], row["p90_ml"] / capacity * 100))
    if len(points) < 2:
        return "<p>Not enough daily readings to draw this path.</p>"
    dates = sorted({item[0] for item in points})
    index = {day: position for position, day in enumerate(dates)}
    values = [item[1] for item in points]
    low = min(0, min(values))
    high = max(100, max(values))
    if high == low:
        high = low + 1
    width, height = 760, 420
    left, right, top, bottom = 48, 128, 28, 36

    def xy(day: str, percent: float) -> tuple[float, float]:
        x = left + (index[day] / max(len(dates) - 1, 1)) * (width - left - right)
        y = top + (high - percent) / (high - low) * (height - top - bottom)
        return x, y

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Summer paths for {esc(storage["place"])}">'
    ]
    # 40% line
    y40 = xy(dates[0], 40)[1]
    parts.append(f'<line x1="{left}" y1="{y40:.1f}" x2="{width - right}" y2="{y40:.1f}" stroke="#ff5c7a" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{left}" y="{y40 - 4:.1f}" font-size="12" fill="#ff5c7a">40% line</text>')
    for tick in (0, 50, 100):
        if low <= tick <= high:
            y = xy(dates[0], tick)[1]
            parts.append(f'<text x="6" y="{y + 4:.1f}" font-size="12" class="muted-text">{tick:.0f}%</text>')
    if band:
        polygon = []
        for row in band:
            polygon.append(xy(row["date"], max(row["p10_ml"], 0) / capacity * 100))
        for row in reversed(band):
            polygon.append(xy(row["date"], max(row["p90_ml"], 0) / capacity * 100))
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in polygon)
        parts.append(f'<polygon points="{poly}" fill="#7ec8ff" fill-opacity="0.28" stroke="#7ec8ff" stroke-width="1"/>')
    end_labels = []
    for name, (rows, colour, _flag) in series.items():
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
        if last:
            end_labels.append((last[0], last[1], f"{name} {last[2]:.0f}%", colour))
    end_labels.sort(key=lambda item: item[1])
    last_y = -999
    for x, y, text, colour in end_labels:
        y = max(y, last_y + 16)
        last_y = y
        parts.append(f'<text x="{x + 6:.1f}" y="{y + 4:.1f}" font-size="12" fill="{colour}">{esc(text)}</text>')
    if dates:
        parts.append(f'<text x="{left}" y="{height - 10}" font-size="12" class="muted-text">{esc(dates[0])}</text>')
        parts.append(
            f'<text x="{width - right - 70}" y="{height - 10}" font-size="12" class="muted-text">{esc(dates[-1])}</text>'
        )
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


def _lever_chart(levers: list[dict]) -> str:
    rows = [row for row in levers if row["geography"] != "Australia"]
    rows = [row for row in rows if any((row.get(key) or 0) > 0 for key in ("urban_gap_ml", "restriction_ml", "desal_ml", "reuse_ml"))]
    if not rows:
        return "<p>No state gap to draw.</p>"
    width = 760
    left = 150
    group_h = 78
    height = 36 + group_h * len(rows)
    biggest = 1.0
    for row in rows:
        for key in ("urban_gap_ml", "restriction_ml", "desal_ml", "reuse_ml"):
            biggest = max(biggest, row.get(key) or 0)
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Town-supply gap against three what-if responses">'
    ]
    colours = (
        ("urban_gap_ml", "#ff5c7a", "Gap"),
        ("restriction_ml", "#ffc14d", "15% household cut"),
        ("desal_ml", "#7ec8ff", "Desalination at last year’s pace"),
        ("reuse_ml", "#2ee6a6", "Recycled water at last year’s pace"),
    )
    span = width - left - 90
    for index, row in enumerate(rows):
        top = 20 + index * group_h
        parts.append(f'<text x="8" y="{top + 28}" font-size="14">{esc(row["geography"])}</text>')
        for slot, (key, colour, _label) in enumerate(colours):
            value = row.get(key) or 0
            y = top + slot * 16
            bar = left + span * (value / biggest)
            parts.append(f'<rect x="{left}" y="{y}" width="{max(bar - left, 0):.1f}" height="12" rx="3" fill="{colour}"/>')
            parts.append(f'<text x="{bar + 6:.1f}" y="{y + 11}" font-size="11">{value / 1000:,.0f} GL</text>')
    parts.append("</svg>")
    legend = "".join(
        f'<span><i class="swatch" style="background:{colour}"></i>{esc(label)}</span>' for _key, colour, label in colours
    )
    parts.append(f'<div class="legend">{legend}</div>')
    return "".join(parts)


def _means(title: str, text: str) -> str:
    return f'<aside class="means"><strong>What this means</strong>{esc(text)}</aside>'


def _banner(briefing: dict) -> str:
    return (
        '<section class="banner">'
        f'<p class="eyebrow">Australia · summer 2026–27 · as at {esc(briefing["as_of_display"])}</p>'
        f'<h1>{esc(briefing["headline"])}</h1>'
        f'<p class="lede">{esc(briefing["lede"])}</p>'
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
        + '<section class="layout"><div class="panel"><h2>Where the tracked supplies stand</h2>'
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
      const chartRows = rows.slice(0, 12);
      const width = 760, rowH = 34, left = 168, height = 20 + rowH * Math.max(chartRows.length, 1);
      const span = width - left - 90;
      let svg = '<svg class="chart" viewBox="0 0 ' + width + ' ' + height + '" role="img">';
      chartRows.forEach((row, index) => {{
        const y = 8 + index * rowH;
        const fraction = row.days_dry === null || row.days_dry === undefined ? 1 : Math.max(0, Math.min(row.days_dry, 140)) / 140;
        const colour = row.status === 'comfortable' ? '#2ee6a6' : (row.status === 'watch' ? '#ffc14d' : '#ff5c7a');
        const bar = left + span * fraction;
        const label = days(row.days_dry);
        svg += '<text x="8" y="' + (y + 16) + '" font-size="13">' + row.place.slice(0, 22) + '</text>';
        svg += '<rect x="' + left + '" y="' + (y + 4) + '" width="' + Math.max(bar - left, 2) + '" height="16" rx="4" fill="' + colour + '"></rect>';
        svg += '<text x="' + (bar + 8) + '" y="' + (y + 16) + '" font-size="13">' + label + '</text>';
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
    body = (
        _banner(briefing)
        + '<section class="panel"><h2>Days above the 40% line if the driest recent summer repeats</h2>'
        + f'<div class="pills" role="group" aria-label="Filter by state">{pills}</div>'
        + f'<div id="tight-chart">{_tight_chart(briefing["storages"])}</div>'
        + '<p class="exact">Exact values: “Holds” means the storage is not falling toward 40% on that path. '
        + "A bar at the right edge is 140 days, which is past the end of March. The list below is every supply followed day by day.</p>"
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
        payload[storage["storage_id"]] = {
            "place": storage["place"],
            "capacity_ml": storage.get("capacity_ml"),
            "path": briefing["paths"].get(storage["storage_id"], {}),
            "percent_full": storage.get("percent_full"),
            "end_percent_dry": storage.get("end_percent_dry"),
            "end_percent_typical": storage.get("end_percent_typical"),
            "end_percent_wet": storage.get("end_percent_wet"),
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
      const end = pack.end_percent_dry;
      const text = 'By the end of March, ' + pack.place + ' would be about ' +
        (end == null ? 'unchanged' : end.toFixed(1) + '%') + ' full on its driest recent summer, ' +
        (pack.end_percent_typical == null ? '–' : pack.end_percent_typical.toFixed(1) + '%') + ' on a middle summer, and ' +
        (pack.end_percent_wet == null ? '–' : pack.end_percent_wet.toFixed(1) + '%') + ' on its wettest recent summer.';
      document.getElementById('scenario-means').textContent = text;
    }));
    window.renderScenario = function(pack) {
      const capacity = pack.capacity_ml || 1;
      const series = [
        ['Recent readings', (pack.path.history || []), '#d6e4f0'],
        ['Dry summer', (pack.path.dry || []), '#ff5c7a'],
        ['Typical summer', (pack.path.typical || []), '#ffc14d'],
        ['Wet summer', (pack.path.wet || []), '#2ee6a6']
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
      band.forEach(row => { values.push(row.p10_ml / capacity * 100); values.push(row.p90_ml / capacity * 100); });
      const low = Math.min(0, ...values), high = Math.max(100, ...values);
      const width = 760, height = 420, left = 48, right = 128, top = 28, bottom = 36;
      const index = {};
      dates.forEach((day, position) => index[day] = position);
      const xy = (day, percent) => {
        const x = left + (index[day] / (dates.length - 1)) * (width - left - right);
        const y = top + (high - percent) / (high - low) * (height - top - bottom);
        return [x, y];
      };
      let svg = '<svg class="chart" viewBox="0 0 ' + width + ' ' + height + '" role="img">';
      const y40 = xy(dates[0], 40)[1];
      svg += '<line x1="' + left + '" y1="' + y40 + '" x2="' + (width - right) + '" y2="' + y40 + '" stroke="#ff5c7a" stroke-dasharray="4 4"/>';
      svg += '<text x="' + left + '" y="' + (y40 - 4) + '" font-size="12" fill="#ff5c7a">40% line</text>';
      if (band.length) {
        const poly = [];
        band.forEach(row => poly.push(xy(row.date, Math.max(row.p10_ml, 0) / capacity * 100)));
        band.slice().reverse().forEach(row => poly.push(xy(row.date, Math.max(row.p90_ml, 0) / capacity * 100)));
        svg += '<polygon points="' + poly.map(pair => pair[0].toFixed(1) + ',' + pair[1].toFixed(1)).join(' ') + '" fill="#7ec8ff" fill-opacity="0.28" stroke="#7ec8ff"/>';
      }
      const labels = [];
      series.forEach(item => {
        if (item[1].length < 2) return;
        let d = '';
        let last = null;
        item[1].forEach(row => {
          const pair = xy(row.date, Math.max(row.volume_ml, 0) / capacity * 100);
          d += (d ? 'L' : 'M') + pair[0].toFixed(1) + ',' + pair[1].toFixed(1);
          last = [pair[0], pair[1], Math.max(row.volume_ml, 0) / capacity * 100];
        });
        svg += '<path d="' + d + '" fill="none" stroke="' + item[2] + '" stroke-width="2.4"/>';
        if (last) labels.push([last[0], last[1], item[0] + ' ' + last[2].toFixed(0) + '%', item[2]]);
      });
      labels.sort((a, b) => a[1] - b[1]);
      let lastY = -999;
      labels.forEach(label => {
        const y = Math.max(label[1], lastY + 16);
        lastY = y;
        svg += '<text x="' + (label[0] + 6) + '" y="' + (y + 4) + '" font-size="12" fill="' + label[3] + '">' + label[2] + '</text>';
      });
      svg += '</svg>';
      return svg;
    };
    </script>
    """.replace("PAYLOAD", json.dumps(payload).replace("<", "\\u003c"))
    title = featured["place"] if featured else "Summer paths"
    body = (
        _banner(briefing)
        + '<section class="panel">'
        + f'<h2 id="scenario-title">{esc(title)}</h2>'
        + f'<div class="pills">{"".join(pills)}</div>'
        + f'<div id="scenario-chart">{chart}</div>'
        + '<p class="exact">Exact values: the lines are percent of accessible capacity. '
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
    body = (
        _banner(briefing)
        + '<section class="panel"><h2>Town-supply gap to the end of March, and three ways to close it</h2>'
        + _lever_chart(briefing["levers"])
        + f"<p>{esc(national)}</p>"
        + "<table><thead><tr><th>State</th><th class='num'>Town gap (GL)</th><th class='num'>Farming gap (GL)</th>"
        + "<th class='num'>15% household cut</th><th class='num'>Desalination</th><th class='num'>Recycled water</th>"
        + f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        + '<p class="exact">Exact values: 1 GL is a billion litres. Shares are how much of the town-supply gap that what-if would cover, capped at 100%. '
        + "The household cut is 15% of last year’s household use, scaled to the days left until 31 March 2027. "
        + "Desalination and recycled water are last year’s annual supply, scaled to the same days. "
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
