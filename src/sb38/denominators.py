"""Renter-occupied households: the filing rate's denominator, fetched from the Census API.

The rate uses the American Community Survey 1-year estimate (table B25003, renter occupied).
The 5-year estimate is fetched beside it as a sensitivity check: it is more precise, but it
averages five years, so it lags the current renter count.

Estimates are fetched at run time and held in memory. Nothing is stored in the repository.
The Census API needs a key, read from the ``CENSUS_API_KEY`` environment variable and never
written anywhere.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

import polars as pl

API = "https://api.census.gov/data"
PAUSE_SECONDS = 1.0  # one request at a time, with a pause between them
SOURCES = ("acs1", "acs5")
PRIMARY = "acs1"

Getter = Callable[[str], list[list[str]]]


def _get_json(url: str) -> list[list[str]]:
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "The Census API needs a key. Set CENSUS_API_KEY in the environment and retry."
        )
    full = f"{url}&key={urllib.parse.quote(key)}"
    with urllib.request.urlopen(full, timeout=120) as resp:  # noqa: S310 - fixed https host
        body = resp.read()
    time.sleep(PAUSE_SECONDS)
    return json.loads(body)


def acs_table(year: int, dataset: str, state: str, county: str, *, get: Getter = _get_json) -> dict:
    """B25003_003 (renter-occupied households) for one county, from ACS 1-year or 5-year."""
    if dataset not in SOURCES:
        raise ValueError(dataset)
    url = (
        f"{API}/{year}/acs/{dataset}?get=B25003_003E,B25003_003M"
        f"&for=county:{county}&in=state:{state}"
    )
    header, row = get(url)[:2]
    rec = dict(zip(header, row, strict=False))
    return {
        "source": dataset,
        "vintage": year,
        "estimate": float(rec["B25003_003E"]),
        "moe90": float(rec["B25003_003M"]),
    }


def fetch(
    state: str,
    county: str,
    years: Iterable[int],
    *,
    sources: Iterable[str] = SOURCES,
    get: Getter = _get_json,
) -> pl.DataFrame:
    """Every published estimate for the given years. A year not yet published is skipped."""
    rows = []
    for source in sources:
        for year in years:
            try:
                rows.append(acs_table(year, source, state, county, get=get))
            except (urllib.error.HTTPError, json.JSONDecodeError, ValueError):
                continue
    return pl.DataFrame(
        rows,
        schema={
            "source": pl.Utf8,
            "vintage": pl.Int64,
            "estimate": pl.Float64,
            "moe90": pl.Float64,
        },
    ).sort("source", "vintage")


def year_matched(table: pl.DataFrame, years: list[int]) -> pl.DataFrame:
    """For each filing year and source, the estimate for that year, or the latest before it.

    A carried-forward estimate is flagged, so a 2026 rate built on a 2025 estimate says so.
    """
    rows = []
    for source in table.get_column("source").unique().sort().to_list():
        avail = table.filter(pl.col("source") == source).sort("vintage")
        for year in years:
            usable = avail.filter(pl.col("vintage") <= year)
            if usable.is_empty():
                continue
            pick = usable.row(-1, named=True)
            rows.append(
                {
                    "year": year,
                    "source": source,
                    "vintage": pick["vintage"],
                    "renter_households": pick["estimate"],
                    "moe90": pick["moe90"],
                    "carried_forward": pick["vintage"] != year,
                }
            )
    return pl.DataFrame(rows)


def filing_rates(filings: pl.DataFrame, denominators: pl.DataFrame) -> pl.DataFrame:
    """Filings per 1,000 renter households for each source, with a 90% range.

    ``filings`` is the output of ``measures.filings``. The range carries only the household
    estimate's sampling error. Filings are a full count, so they add none.
    """
    joined = filings.join(denominators, on="year", how="inner")
    lo = (pl.col("renter_households") + pl.col("moe90")).clip(lower_bound=1)
    hi = (pl.col("renter_households") - pl.col("moe90")).clip(lower_bound=1)
    return joined.select(
        "frame",
        "year",
        "month",
        "source",
        "vintage",
        "carried_forward",
        pl.col("value").alias("filings"),
        "renter_households",
        "moe90",
        (pl.col("value") / pl.col("renter_households") * 1000).alias("rate_per_1000"),
        (pl.col("value") / lo * 1000).alias("rate_low"),
        (pl.col("value") / hi * 1000).alias("rate_high"),
    ).sort("source", "frame", "year", "month")
