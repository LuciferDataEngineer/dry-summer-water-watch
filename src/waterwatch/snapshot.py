"""Pull the public snapshot. Live network only. CI reads the committed files.

Sources, all free and keyless:
- Bureau of Meteorology water storage dashboard (rest.php) and KiWIS station list
- Open-Meteo archive (rain, temperature, FAO ET0)
- ABS Water Account, Australia 2024-25, Table 13
- Natural Earth admin-1 boundaries (public domain) for the map outline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from waterwatch.config import (
    ABS_WATER_CSV,
    BOM_DASHBOARD,
    BOM_KIWIS,
    NATURAL_EARTH_STATES,
    OPEN_METEO_ARCHIVE,
    USER_AGENT,
)

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
RAW = ROOT / "data" / "raw"

MELBOURNE = ZoneInfo("Australia/Melbourne")

PRIORITY = (
    "warragamba",
    "wivenhoe",
    "somerset",
    "hinze",
    "north pine",
    "hume",
    "dartmouth",
    "eildon",
    "thomson",
    "upper yarra",
    "cardinia",
    "sugarloaf",
    "silvan",
    "menindee",
    "wyangala",
    "burrinjuck",
    "blowering",
    "copeton",
    "keepit",
    "burrendong",
    "googong",
    "corin",
    "bendora",
    "cotter",
    "canning",
    "serpentine",
    "mundaring",
    "happy valley",
    "mount bold",
    "myponga",
    "ross river",
    "burdekin",
    "tinaroo",
    "fairbairn",
    "awoonga",
    "moogerah",
    "wyaralong",
    "baroon",
    "gordon",
    "great lake",
    "argyle",
    "harding",
    "prospect",
    "cataract",
    "glenbawn",
    "grahamstown",
    "chichester",
    "split rock",
    "windamere",
    "pindari",
    "glenlyon",
    "coolmunda",
    "leslie",
    "atkinson",
    "south dandalup",
    "woronora",
    "nepean",
    "cordeaux",
    "avon",
    "maroondah",
    "yan yean",
    "greenvale",
    "goond",
)

HYDRO_KEYS = (
    "gordon",
    "great lake",
    "pedder",
    "lake echo",
    "rowallan",
    "cethana",
    "meadowbank",
    "murchison",
    "repulse",
    "cluny",
    "penstock",
    "poatina",
)
URBAN_KEYS = (
    "warragamba",
    "wivenhoe",
    "cataract",
    "avon",
    "cordeaux",
    "nepean",
    "woronora",
    "prospect",
    "upper yarra",
    "cardinia",
    "sugarloaf",
    "silvan",
    "thomson",
    "maroondah",
    "yan yean",
    "greenvale",
    "canning",
    "serpentine",
    "mundaring",
    "south dandalup",
    "north dandalup",
    "happy valley",
    "mount bold",
    "myponga",
    "barossa",
    "hinze",
    "north pine",
    "somerset",
    "baroon",
    "googong",
    "corin",
    "bendora",
    "cotter",
    "ross river",
    "harding",
    "grahamstown",
    "chichester",
    "wyaralong",
    "leslie",
    "atkinson",
    "moogerah",
)
IRRIGATION_KEYS = (
    "hume",
    "dartmouth",
    "eildon",
    "menindee",
    "wyangala",
    "burrinjuck",
    "blowering",
    "copeton",
    "keepit",
    "burrendong",
    "fairbairn",
    "burdekin",
    "glenlyon",
    "coolmunda",
    "pindari",
    "tinaroo",
    "awoonga",
    "argyle",
    "split rock",
    "windamere",
    "glenbawn",
    "glenmaggie",
    "rocklands",
    "eildon",
)

STATES = (
    "New South Wales",
    "Victoria",
    "Queensland",
    "South Australia",
    "Western Australia",
    "Tasmania",
    "Northern Territory",
    "Australian Capital Territory",
)

MAX_TRACKED = 64
HISTORY_FROM = "2022-01-01"


class Limiter:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
            self._next = time.monotonic() + self.interval


def _request(url: str, data: dict | None, timeout: int, limiter: Limiter | None, method: str) -> bytes:
    if limiter:
        limiter.wait()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*"}
    target = url
    payload = None
    if method == "GET" and data:
        joiner = "&" if "?" in url else "?"
        target = url + joiner + urllib.parse.urlencode(data)
    elif data is not None:
        payload = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Referer"] = "https://www.bom.gov.au/water/dashboards/"
    last_error: Exception | None = None
    for attempt in range(5):
        req = urllib.request.Request(target, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            exc.close()
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(min(2**attempt, 16))
        if limiter:
            limiter.wait()
    raise RuntimeError(f"request failed: {target}") from last_error


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    RAW.mkdir(parents=True, exist_ok=True)
    return RAW / f"{digest}.bin"


def fetch(
    url: str,
    data: dict | None = None,
    timeout: int = 90,
    limiter: Limiter | None = None,
    live: bool = True,
    method: str = "GET",
) -> bytes:
    key = method + " " + url + "?" + urllib.parse.urlencode(data or {})
    path = _cache_path(key)
    if path.exists() and not live:
        return path.read_bytes()
    body = _request(url, data, timeout, limiter, method)
    path.write_bytes(body)
    return body


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "na", "np", "..", "None"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def slug(state: str, place: str) -> str:
    raw = f"{state}-{place}".lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return raw


def role_for(place: str) -> str:
    name = place.lower()
    if any(key in name for key in HYDRO_KEYS):
        return "hydro"
    if any(key in name for key in URBAN_KEYS):
        return "urban"
    if any(key in name for key in IRRIGATION_KEYS):
        return "irrigation"
    return "regional"


def priority_hit(place: str) -> bool:
    name = place.lower()
    return any(key in name for key in PRIORITY)


def bom_table(form: dict, limiter: Limiter, live: bool) -> dict:
    body = fetch(BOM_DASHBOARD, form, timeout=120, limiter=limiter, live=live, method="POST")
    return json.loads(body)


def load_sites(limiter: Limiter, live: bool) -> list[dict]:
    sites: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for state in STATES:
        payload = bom_table(
            {
                "themeview": "storages.summary",
                "purpose": "data",
                "table": "site_table",
                "table_filter": "state",
                "timeScale": "latest",
                "filter_by": state,
                "draw": "1",
                "start": "0",
                "length": "500",
            },
            limiter,
            live,
        )
        for row in payload.get("data", []):
            place = str(row.get("place") or "").strip()
            if not place:
                continue
            key = (state, place)
            if key in seen:
                continue
            seen.add(key)
            capacity = parse_number(row.get("active_capacity_ml"))
            volume = parse_number(row.get("volume_active"))
            percent = parse_number(row.get("proportion_full"))
            level = parse_number(row.get("water_level"))
            depth = parse_number(row.get("water_depth"))
            if capacity is None or capacity <= 0 or percent is None:
                continue
            if volume is None:
                volume = percent / 100.0 * capacity
            sites.append(
                {
                    "storage_id": slug(state, place),
                    "state": state,
                    "place": place,
                    "role": role_for(place),
                    "capacity_ml": capacity,
                    "volume_ml": volume,
                    "percent_full": percent,
                    "water_level_m": level,
                    "water_depth_m": depth,
                    "obs_date": str(row.get("obs_date") or ""),
                    "priority": priority_hit(place),
                }
            )
    return sites


def choose_tracked(sites: list[dict]) -> list[dict]:
    chosen = []
    for site in sites:
        large = site["capacity_ml"] >= 80_000
        low = site["percent_full"] < 50 and site["capacity_ml"] >= 15_000
        if site["priority"] or large or low:
            chosen.append(site)
    if len(chosen) <= MAX_TRACKED:
        return chosen
    priority = [site for site in chosen if site["priority"]]
    rest = [site for site in chosen if not site["priority"]]
    rest.sort(key=lambda site: -((100 - site["percent_full"]) * math.log10(site["capacity_ml"])))
    return (priority + rest)[:MAX_TRACKED]


def load_history(site: dict, limiter: Limiter, live: bool) -> list[dict]:
    payload = bom_table(
        {
            "themeview": "storages.summary",
            "purpose": "data",
            "table": "site_timeseries_table",
            "table_filter": "state",
            "timeScale": "latest",
            "storageName": site["place"],
            "draw": "1",
            "start": "0",
            "length": "20000",
        },
        limiter,
        live,
    )
    rows = []
    for row in payload.get("data", []):
        if str(row.get("storage_name") or "").strip() != site["place"]:
            continue
        obs = str(row.get("observation_day") or "")
        volume = parse_number(row.get("volume_active"))
        if volume is None or obs < HISTORY_FROM:
            continue
        rows.append(
            {
                "storage_id": site["storage_id"],
                "obs_date": obs,
                "volume_ml": volume,
                "capacity_ml": site["capacity_ml"],
            }
        )
    rows.sort(key=lambda item: item["obs_date"])
    deduped: dict[str, dict] = {}
    for row in rows:
        deduped[row["obs_date"]] = row
    return [deduped[key] for key in sorted(deduped)]


def load_group_table(table: str, table_filter: str, limiter: Limiter, live: bool) -> list[dict]:
    current = bom_table(
        {
            "themeview": "storages.summary",
            "purpose": "data",
            "table": table,
            "table_filter": table_filter,
            "timeScale": "latest",
            "draw": "1",
            "start": "0",
            "length": "50",
        },
        limiter,
        live,
    )
    history = bom_table(
        {
            "themeview": "storages.summary",
            "purpose": "data",
            "table": "history_table",
            "table_filter": table_filter,
            "timeScale": "latest",
            "draw": "1",
            "start": "0",
            "length": "50",
        },
        limiter,
        live,
    )
    history_by_name = {row.get("state"): row for row in history.get("data", [])}
    rows = []
    for row in current.get("data", []):
        name = str(row.get("state") or "")
        past = history_by_name.get(name, {})
        rows.append(
            {
                "name": name,
                "group": table_filter,
                "volume_ml": parse_number(row.get("current_volume")),
                "capacity_ml": parse_number(row.get("accessible_capacity")),
                "percent_full": parse_number(row.get("current_percentage_full")),
                "reported_date": str(row.get("most_recent_report_date") or ""),
                "percent_yesterday": parse_number(past.get("yesterday")),
                "percent_last_week": parse_number(past.get("last_week")),
                "percent_last_month": parse_number(past.get("last_month")),
                "percent_last_year": parse_number(past.get("last_year")),
            }
        )
    return rows


def load_cities(limiter: Limiter, live: bool) -> list[dict]:
    payload = json.loads(
        fetch(
            BOM_DASHBOARD,
            {"themeview": "storages.summary", "purpose": "city_cards", "filter_by_data": "[]"},
            limiter=limiter,
            live=live,
            method="POST",
        )
    )
    rows = []
    for city in payload.get("data", []):
        today = city.get("TODAY") or {}
        last = city.get("LAST_YEAR") or {}
        rows.append(
            {
                "city": city.get("name"),
                "percent_full": parse_number(today.get("percentageFull")),
                "volume_ml": parse_number(today.get("volume")),
                "capacity_ml": parse_number(today.get("capacity")),
                "percent_last_year": parse_number(last.get("percentageFull")),
                "volume_last_year_ml": parse_number(last.get("volume")),
                "reported_date": str(city.get("reported_date") or ""),
            }
        )
    return rows


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    for word in (
        "dam",
        "reservoir",
        "lake",
        "weir",
        "headwater",
        "storage",
        "volume",
        "res",
        "the",
        "at",
        "ck",
        "river",
        "hw",
    ):
        text = re.sub(rf"\b{word}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def in_australia(lat: float, lon: float) -> bool:
    return -44.5 <= lat <= -10.0 and 112.0 <= lon <= 154.5


def load_stations(limiter: Limiter, live: bool) -> list[dict]:
    body = fetch(
        BOM_KIWIS,
        {
            "service": "kisters",
            "type": "queryServices",
            "datasource": "0",
            "format": "json",
            "request": "getStationList",
            "parametertype_name": "Storage Volume",
            "returnfields": "station_name,station_no,station_id,station_latitude,station_longitude",
        },
        timeout=120,
        limiter=limiter,
        live=live,
    )
    table = json.loads(body)
    header = table[0]
    index = {name: pos for pos, name in enumerate(header)}
    stations = []
    for row in table[1:]:
        try:
            lat = float(row[index["station_latitude"]])
            lon = float(row[index["station_longitude"]])
        except (TypeError, ValueError):
            continue
        if not in_australia(lat, lon):
            continue
        name = str(row[index["station_name"]])
        stations.append(
            {
                "station_name": name,
                "station_no": str(row[index["station_no"]]),
                "station_id": str(row[index["station_id"]]),
                "latitude": lat,
                "longitude": lon,
                "norm": _norm(name),
            }
        )
    return stations


def match_station(place: str, stations: list[dict]) -> dict | None:
    target = _norm(place)
    if not target:
        return None
    scored: list[tuple[int, int, dict]] = []
    target_tokens = set(target.split())
    for station in stations:
        name = station["norm"]
        if not name:
            continue
        if name == target:
            score = 100
        elif name.startswith(target) or target.startswith(name):
            score = 85
        else:
            tokens = set(name.split())
            if not tokens or not target_tokens:
                continue
            overlap = len(tokens & target_tokens) / len(tokens | target_tokens)
            if overlap < 0.6:
                continue
            score = int(70 + 20 * overlap)
        scored.append((score, -abs(len(name) - len(target)), station))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = scored[0]
    if best[0] < 80:
        return None
    if len(scored) > 1 and scored[1][0] >= best[0] and scored[1][2]["station_no"] != best[2]["station_no"]:
        # Ambiguous exact ties: keep the shorter station name, already preferred by the sort.
        if scored[1][0] == best[0] and scored[1][1] == best[1]:
            return None
    return best[2]


def load_weather(tracked: list[dict], live: bool) -> list[dict]:
    points = [site for site in tracked if site.get("latitude") is not None]
    rows: list[dict] = []
    limiter = Limiter(0.4)
    for start in range(0, len(points), 12):
        batch = points[start : start + 12]
        if not batch:
            continue
        body = fetch(
            OPEN_METEO_ARCHIVE,
            {
                "latitude": ",".join(f"{site['latitude']:.5f}" for site in batch),
                "longitude": ",".join(f"{site['longitude']:.5f}" for site in batch),
                "start_date": HISTORY_FROM,
                "end_date": "2026-11-14",
                "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,et0_fao_evapotranspiration",
                "timezone": "Australia/Sydney",
            },
            timeout=120,
            limiter=limiter,
            live=live,
        )
        payload = json.loads(body)
        blocks = payload if isinstance(payload, list) else [payload]
        if len(blocks) != len(batch):
            raise RuntimeError(f"Open-Meteo returned {len(blocks)} blocks for {len(batch)} places")
        for site, block in zip(batch, blocks, strict=True):
            daily = block["daily"]
            for obs, rain, tmax, tmin, et0 in zip(
                daily["time"],
                daily["precipitation_sum"],
                daily["temperature_2m_max"],
                daily["temperature_2m_min"],
                daily["et0_fao_evapotranspiration"],
                strict=True,
            ):
                rows.append(
                    {
                        "storage_id": site["storage_id"],
                        "obs_date": obs,
                        "rain_mm": rain,
                        "tmax_c": tmax,
                        "tmin_c": tmin,
                        "et0_mm": et0,
                    }
                )
    return rows


def write_table(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise RuntimeError(f"no rows for {path}")
    columns = list(rows[0].keys())
    arrays = []
    for column in columns:
        values = [row[column] for row in rows]
        if all(value is None or isinstance(value, bool) for value in values):
            arrays.append(pa.array(values, type=pa.bool_()))
        elif all(value is None or isinstance(value, int) and not isinstance(value, bool) for value in values):
            arrays.append(pa.array(values, type=pa.int64()))
        elif all(value is None or isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            arrays.append(pa.array([None if value is None else float(value) for value in values], type=pa.float64()))
        else:
            arrays.append(pa.array([None if value is None else str(value) for value in values], type=pa.string()))
    table = pa.Table.from_arrays(arrays, names=columns)
    pq.write_table(table, path, compression="zstd")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parquet_rows(path: Path) -> int:
    return pq.ParquetFile(path).metadata.num_rows


def build_snapshot(live: bool = True) -> dict:
    limiter = Limiter(0.3)
    taken = datetime.now(MELBOURNE)
    print("fetching sites", flush=True)
    sites = load_sites(limiter, live)
    print(f"sites {len(sites)}", flush=True)
    tracked = choose_tracked(sites)
    tracked_ids = {site["storage_id"] for site in tracked}
    print(f"tracked {len(tracked)}", flush=True)

    print("fetching group summaries and cities", flush=True)
    states = load_group_table("storage_table", "state", limiter, live)
    drainage = load_group_table("storage_table", "drainage", limiter, live)
    cities = load_cities(limiter, live)

    print("fetching station coordinates", flush=True)
    stations = load_stations(limiter, live)
    matched = 0
    for site in sites:
        station = match_station(site["place"], stations)
        if station is None:
            site["latitude"] = None
            site["longitude"] = None
            site["station_no"] = None
            site["station_name"] = None
            continue
        site["latitude"] = station["latitude"]
        site["longitude"] = station["longitude"]
        site["station_no"] = station["station_no"]
        site["station_name"] = station["station_name"]
        if site["storage_id"] in tracked_ids:
            matched += 1
    print(f"tracked with coordinates {matched}/{len(tracked)}", flush=True)

    print("fetching daily history", flush=True)
    daily: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(load_history, site, limiter, live): site for site in tracked}
        done = 0
        for future in as_completed(futures):
            site = futures[future]
            rows = future.result()
            daily.extend(rows)
            done += 1
            print(f"  {done}/{len(tracked)} {site['place']} {len(rows)} days", flush=True)
    if not daily:
        raise RuntimeError("daily history came back empty")

    print("fetching weather", flush=True)
    weather = load_weather([site for site in sites if site["storage_id"] in tracked_ids], live=live)
    print(f"weather rows {len(weather)}", flush=True)

    print("fetching ABS water account and map outline", flush=True)
    abs_bytes = fetch(ABS_WATER_CSV, timeout=120, limiter=None, live=live)
    geo_bytes = fetch(NATURAL_EARTH_STATES, timeout=120, limiter=None, live=live)
    geo = json.loads(geo_bytes)
    def _round_coords(value):
        if isinstance(value, list):
            if value and isinstance(value[0], (int, float)) and not isinstance(value[0], bool):
                return [round(float(value[0]), 2), round(float(value[1]), 2)]
            return [_round_coords(item) for item in value]
        return value

    australia_features = []
    for feature in geo.get("features", []):
        props = feature.get("properties") or {}
        if props.get("adm0_a3") != "AUS" and props.get("admin") != "Australia":
            continue
        australia_features.append(
            {
                "type": "Feature",
                "properties": {"name": props.get("name"), "postal": props.get("postal")},
                "geometry": _round_coords(feature.get("geometry")),
            }
        )
    australia = {"type": "FeatureCollection", "features": australia_features}
    if len(australia["features"]) < 5:
        raise RuntimeError(f"expected Australian state polygons, got {len(australia['features'])}")

    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    write_table(SNAPSHOT / "sites.parquet", sites)
    write_table(SNAPSHOT / "daily.parquet", daily)
    write_table(SNAPSHOT / "states.parquet", states)
    write_table(SNAPSHOT / "drainage.parquet", drainage)
    write_table(SNAPSHOT / "cities.parquet", cities)
    if weather:
        write_table(SNAPSHOT / "weather.parquet", weather)
    (SNAPSHOT / "abs_water_account_2024_25.csv").write_bytes(abs_bytes)
    (SNAPSHOT / "australia_states.geojson").write_text(json.dumps(australia), encoding="utf-8")

    stamp = taken.isoformat(timespec="seconds")
    zone = taken.tzname() or "AEST"
    files = []
    catalogue = [
        (
            "sites.parquet",
            "Bureau of Meteorology water storage dashboard, latest site table",
            BOM_DASHBOARD,
            "CC BY 4.0",
        ),
        (
            "daily.parquet",
            "Bureau of Meteorology water storage dashboard, daily accessible volume",
            BOM_DASHBOARD,
            "CC BY 4.0",
        ),
        (
            "states.parquet",
            "Bureau of Meteorology water storage dashboard, state totals and change",
            BOM_DASHBOARD,
            "CC BY 4.0",
        ),
        (
            "drainage.parquet",
            "Bureau of Meteorology water storage dashboard, drainage division totals",
            BOM_DASHBOARD,
            "CC BY 4.0",
        ),
        (
            "cities.parquet",
            "Bureau of Meteorology water storage dashboard, capital-city system cards",
            BOM_DASHBOARD,
            "CC BY 4.0",
        ),
        (
            "weather.parquet",
            "Open-Meteo archive daily rain, temperature and FAO reference evapotranspiration",
            OPEN_METEO_ARCHIVE,
            "CC BY 4.0",
        ),
        (
            "abs_water_account_2024_25.csv",
            "ABS Water Account, Australia 2024-25, Table 13 physical supply and use",
            ABS_WATER_CSV,
            "CC BY 4.0",
        ),
        (
            "australia_states.geojson",
            "Natural Earth 1:110m admin-1 states and provinces, Australia only",
            NATURAL_EARTH_STATES,
            "Public domain",
        ),
    ]
    for name, description, url, licence in catalogue:
        path = SNAPSHOT / name
        if not path.exists():
            continue
        rows = None
        if path.suffix == ".parquet":
            rows = parquet_rows(path)
        elif path.suffix == ".csv":
            rows = max(sum(1 for _ in path.open(encoding="utf-8", errors="replace")) - 1, 0)
        elif path.suffix == ".geojson":
            rows = len(json.loads(path.read_text(encoding="utf-8")).get("features", []))
        files.append(
            {
                "path": f"data/snapshot/{name}",
                "description": description,
                "source_url": url,
                "licence": licence,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": rows,
            }
        )
    manifest = {
        "project": "dry-summer-water-watch",
        "access_timestamp": stamp,
        "access_timezone": zone,
        "access_display": taken.strftime(f"%-d %B %Y, %-I:%M %p {zone}"),
        "sites": len(sites),
        "tracked_storages": len(tracked),
        "tracked_with_coordinates": matched,
        "daily_rows": len(daily),
        "weather_rows": len(weather),
        "history_from": HISTORY_FROM,
        "files": files,
        "notes": [
            "Daily history is kept for a tracked subset (large, low, or priority urban and regional storages), not every storage on the dashboard.",
            "Coordinates come from the Bureau KiWIS station list, matched by name. Unmatched storages are kept in the tables and left off the map.",
            "Weather dates use Australian Eastern time so they line up with the briefing clock.",
            "No Murray–Darling Basin Authority file is in this snapshot: the drainage totals from the Bureau dashboard are the basin cross-check.",
        ],
    }
    (SNAPSHOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("access_display", "sites", "tracked_storages", "daily_rows", "weather_rows")}, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the public water snapshot")
    parser.add_argument("--live", action="store_true", help="Ignore cached responses and call the sources again")
    args = parser.parse_args()
    build_snapshot(live=args.live)


if __name__ == "__main__":
    main()
