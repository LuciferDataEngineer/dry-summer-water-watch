"""Read the ABS Water Account table. Nil ('-') is zero. Suppressed cells stay blank."""

from __future__ import annotations

import csv
from pathlib import Path


def _cell(value: str) -> float | None:
    text = value.strip().replace(",", "")
    if text in {"-", "–"}:
        return 0.0
    if text in {"", "na", "np", ".."}:
        return None
    return float(text)


def load_water_account(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    header_index = next(i for i, row in enumerate(rows) if row and row[0].strip() == "Geography Description")
    header = [cell.strip() for cell in rows[header_index]]
    year_columns = [name for name in header if name[:4].isdigit()]
    records = []
    for row in rows[header_index + 1 :]:
        if len(row) < len(header):
            continue
        record = {
            "geography": row[0].strip(),
            "industry": row[2].strip(),
            "supply_use": row[4].strip(),
            "product": row[5].strip(),
        }
        for name in year_columns:
            record[name] = _cell(row[header.index(name)])
        records.append(record)
    return records


def _pick(
    records: list[dict],
    geography: str,
    supply_use: str,
    product: str,
    industry_contains: str | None = None,
    industry_exact: str | None = None,
) -> dict | None:
    for record in records:
        if record["geography"] != geography:
            continue
        if record["supply_use"] != supply_use or record["product"] != product:
            continue
        if industry_exact is not None and record["industry"] != industry_exact:
            continue
        if industry_contains is not None and industry_contains.lower() not in record["industry"].lower():
            continue
        return record
    return None


def state_levers(records: list[dict], year: str = "2024-25", previous: str = "2023-24") -> list[dict]:
    """Household use, utility desalination and total reuse, in megalitres."""
    names = [
        "Australia",
        "New South Wales",
        "Victoria",
        "Queensland",
        "South Australia",
        "Western Australia",
        "Tasmania",
        "Northern Territory",
        "Australian Capital Territory",
    ]
    rows = []
    for name in names:
        household_distributed = _pick(records, name, "Use", "Distributed", "Households")
        household_reuse = _pick(records, name, "Use", "Reuse", "Households")
        desal = _pick(records, name, "Use", "Sea water for desalination", "Water supply, sewerage")
        reuse_supply = _pick(records, name, "Supply", "Reuse", industry_exact="Total")
        distributed = None if household_distributed is None else household_distributed.get(year)
        reuse_hh = None if household_reuse is None else household_reuse.get(year)
        household = None
        if distributed is not None or reuse_hh is not None:
            household = (distributed or 0.0) + (reuse_hh or 0.0)
        desal_now = None if desal is None else desal.get(year)
        desal_prev = None if desal is None else desal.get(previous)
        reuse_now = None if reuse_supply is None else reuse_supply.get(year)
        rows.append(
            {
                "geography": name,
                "household_ml": household,
                "household_distributed_ml": distributed,
                "desal_ml": desal_now,
                "desal_previous_ml": desal_prev,
                "reuse_supply_ml": reuse_now,
            }
        )
    return rows
