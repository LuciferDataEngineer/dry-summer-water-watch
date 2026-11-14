"""Shared constants. Scenario rules live here so the briefing and the tests agree."""

from __future__ import annotations

from datetime import date

SEED = 16

# Briefing lines, as a share of accessible capacity. These are planning bands
# for this briefing, not a statutory trigger for any one utility.
CRITICAL_LINE = 0.25
LOW_LINE = 0.40
WATCH_LINE = 0.60

SUMMER_END = date(2027, 3, 31)
CHRISTMAS = date(2026, 12, 25)

# Illustrative household saving used on the levers page. Not a measured saving.
RESTRICTION_SAVING = 0.15

# Completed summers used as analogues. The current summer is not one of them.
ANALOGUE_SUMMERS = (
    (date(2022, 11, 1), date(2023, 3, 31)),
    (date(2023, 11, 1), date(2024, 3, 31)),
    (date(2024, 11, 1), date(2025, 3, 31)),
    (date(2025, 11, 1), date(2026, 3, 31)),
)

MIN_SUMMER_DAYS = 40
RECENT_DAYS = 30

CHRONOS_MODEL = "amazon/chronos-bolt-small"
CHRONOS_CONTEXT = 512
CHRONOS_HOLDOUT_DAYS = 60
CHRONOS_QUANTILES = (0.1, 0.5, 0.9)
MAX_CHRONOS_SERIES = 24

# The Bureau's storage service answers browser-like clients and rejects a bare bot string.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 "
    "dry-summer-water-watch/1.0"
)

BOM_DASHBOARD = "https://www.bom.gov.au/water/dashboards/rest.php"
BOM_KIWIS = "https://www.bom.gov.au/waterdata/services"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
ABS_WATER_CSV = (
    "https://www.abs.gov.au/statistics/environment/environmental-accounts/"
    "water-account-australia/2024-25/WAADC13_.csv"
)
# 110m admin-1 on the public mirrors currently contains only the United States.
# 50m includes the Australian states. The snapshot keeps Australia and rounds coordinates.
NATURAL_EARTH_STATES = (
    "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/"
    "50m/cultural/ne_50m_admin_1_states_provinces.json"
)

# Words that must not appear in the stakeholder pages.
DASHBOARD_BANNED = (
    "temporal",
    "risingwave",
    "keda",
    "duckdb",
    "chronos",
    "parquet",
    "bronze",
    "silver",
    "gold",
    "pipeline",
    "schema",
    "quantile",
    "backtest",
    "spark",
    "kafka",
    "docker",
    "python",
    "regression",
    "medallion",
    "workflow",
    "materialized",
    "huggingface",
    "pytorch",
    "numpy",
    "pandas",
    "postgres",
    "kiwis",
    "open-meteo",
    "monte carlo",
    "api",
    "model",
    "forecast",
    "algorithm",
    "dataset",
    "sql",
)
