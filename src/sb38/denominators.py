"""Renter-occupied households: the filing rate's denominator, from three Census sources.

The filing rate divides filings by renter households, so the choice of household estimate moves
the rate. Rather than pick one and hope, each year's rate is computed under three estimates and
the spread is reported:

- ``acs1``: American Community Survey 1-year estimate, table B25003 (renter occupied). The most
  timely, with the widest margin of error.
- ``acs5``: ACS 5-year estimate, same table. The most precise, but it averages five years, so it
  lags and smooths.
- ``pums1``: built from the ACS 1-year Public Use Microdata Sample. It sums household weights for
  tenure "rented" (3) or "occupied without payment of rent" (4) over the microdata areas making
  up the county, with a margin of error from the 80 replicate weights. It is not controlled to
  the published county totals, so it can differ from ``acs1`` even in the same year.

Estimates are fetched once, written to ``config/<county>/renter_households.csv`` with their
source and retrieval date, and committed. They are public aggregate figures, so running the
analysis doesn't need the network.

The Census API needs a key. It is read from the ``CENSUS_API_KEY`` environment variable and
never written anywhere.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

API = "https://api.census.gov/data"
PAUSE_SECONDS = 1.0  # one request at a time, with a pause between them
SOURCES = ("acs1", "acs5", "pums1")
Z90 = 1.645  # ACS margins of error are published at 90%

Getter = Callable[[str], list[list[str]]]


@dataclass(frozen=True)
class Estimate:
    source: str
    vintage: int  # the survey year; for acs5, the last year of the five
    estimate: float
    moe90: float
    url: str  # with the key removed


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


def acs_table(
    year: int, dataset: str, state: str, county: str, *, get: Getter = _get_json
) -> Estimate:
    """B25003_003 (renter-occupied households) for one county from ACS 1-year or 5-year."""
    if dataset not in ("acs1", "acs5"):
        raise ValueError(dataset)
    url = (
        f"{API}/{year}/acs/{dataset}?get=B25003_003E,B25003_003M"
        f"&for=county:{county}&in=state:{state}"
    )
    header, row = get(url)[:2]
    rec = dict(zip(header, row, strict=False))
    return Estimate(dataset, year, float(rec["B25003_003E"]), float(rec["B25003_003M"]), url)


def pums_renters(year: int, state: str, pumas: list[str], *, get: Getter = _get_json) -> Estimate:
    """Renter households summed over a county's microdata areas, with a replicate-weight MOE.

    One record per occupied housing unit: the first person listed (``SPORDER=1``) in a housing
    unit rather than group quarters (``TYPEHUGQ=1``). Vacant units have no person records, so
    they drop out on their own. The API caps a request at 50 variables, so the 80 replicate
    weights come in two batches joined on the household serial number.
    """
    where = (
        f"&for=public%20use%20microdata%20area:{','.join(pumas)}&in=state:{state}"
        "&SPORDER=1&TYPEHUGQ=1"
    )
    batches = [
        ["SERIALNO", "TEN", "WGTP"] + [f"WGTP{i}" for i in range(1, 41)],
        ["SERIALNO"] + [f"WGTP{i}" for i in range(41, 81)],
    ]
    frames = []
    url0 = ""
    for cols in batches:
        url = f"{API}/{year}/acs/acs1/pums?get={','.join(cols)}{where}"
        url0 = url0 or url
        data = get(url)
        frames.append(pl.DataFrame(data[1:], schema=data[0], orient="row").select(cols))
    df = frames[0].join(frames[1], on="SERIALNO", how="inner")
    renters = df.filter(pl.col("TEN").is_in(["3", "4"]))
    weights = renters.select(pl.all().exclude("SERIALNO", "TEN").cast(pl.Float64)).sum()
    est = weights.get_column("WGTP").item()
    reps = [weights.get_column(f"WGTP{i}").item() for i in range(1, 81)]
    se = math.sqrt(4 / 80 * sum((r - est) ** 2 for r in reps))
    return Estimate("pums1", year, est, Z90 * se, url0)


def to_frame(estimates: list[Estimate], *, county: str, retrieved: date) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "county": county,
                "source": e.source,
                "vintage": e.vintage,
                "estimate": e.estimate,
                "moe90": e.moe90,
                "retrieved": retrieved,
                "url": e.url,
            }
            for e in estimates
        ]
    ).sort("source", "vintage")


def read(path: Path) -> pl.DataFrame:
    return pl.read_csv(path, try_parse_dates=True)


def year_matched(table: pl.DataFrame, years: list[int]) -> pl.DataFrame:
    """For each filing year and source, the estimate for that year, or the latest before it.

    A carried-forward estimate is labelled, so a 2026 rate built on a 2025 estimate says so.
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
    """Filings per 1,000 renter households under every source, with a 90% range.

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


def refresh(
    county_dir: Path, state: str, county: str, years: list[int], *, get: Getter = _get_json
) -> pl.DataFrame:
    """Fetch every source for every year that is published, and write the county's CSV.

    A year a source hasn't published yet is skipped and reported, not treated as an error.
    """
    pumas = (
        pl.read_csv(county_dir / "pumas.csv", infer_schema=False).get_column("puma5ce").to_list()
    )
    estimates: list[Estimate] = []
    for year in years:
        for label, fn in (
            ("acs1", lambda y: acs_table(y, "acs1", state, county, get=get)),
            ("acs5", lambda y: acs_table(y, "acs5", state, county, get=get)),
            ("pums1", lambda y: pums_renters(y, state, pumas, get=get)),
        ):
            try:
                estimates.append(fn(year))
            except (urllib.error.HTTPError, json.JSONDecodeError, ValueError) as exc:
                print(f"  {label} {year}: not available ({type(exc).__name__})")
    frame = to_frame(estimates, county=county_dir.name, retrieved=date.today())
    frame.write_csv(county_dir / "renter_households.csv")
    return frame


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[2] / "config"
    if sys.argv[1:] != ["dallas"]:
        sys.exit("usage: python -m sb38.denominators dallas")
    print(refresh(root / "dallas", "48", "113", [2023, 2024, 2025]))
