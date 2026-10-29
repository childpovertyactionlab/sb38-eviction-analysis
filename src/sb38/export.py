"""The per-county results table: every scoping-document stat, as one CSV.

Rows follow the scoping document's topics and stats in order. Each carries the document's own
topic, indicator and stat text beside the computed value, so a county's file reads one to one
against the document and two counties' files stack into one comparison. A stat with nothing
behind it yet still gets a row, marked not computed.

Results are analysis output, not data: aggregates computed from the case table. They may be
kept in the repository under ``results/``; the case table itself never is.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from sb38 import __version__, denominators, measures, scope

COLUMNS = [
    "county",
    "topic",
    "indicator",
    "stat",
    "measure",
    "status",
    "frame",
    "year",
    "month",
    "value",
    "numerator",
    "denominator",
    "denominator_source",
    "denominator_vintage",
    "carried_forward",
    "note",
    "data_as_of",
    "pulled_on",
    "sb38_version",
]


def _rates(cases: pl.DataFrame, renters: pl.DataFrame) -> pl.DataFrame:
    dens = denominators.year_matched(renters, list(measures.COHORT_YEARS))
    if dens.is_empty():
        return pl.DataFrame()
    r = denominators.filing_rates(measures.filings(cases), dens)
    return r.select(
        "frame",
        "year",
        "month",
        pl.lit("filing_rate").alias("measure"),
        pl.col("rate_per_1000").alias("value"),
        pl.col("filings").cast(pl.Int64).alias("numerator"),
        pl.col("renter_households").cast(pl.Int64).alias("denominator"),
        pl.col("source").alias("denominator_source"),
        pl.col("vintage").cast(pl.Int64).alias("denominator_vintage"),
        "carried_forward",
        pl.lit("").alias("note"),
    )


def results(
    cases: pl.DataFrame,
    *,
    as_of: date,
    county: str,
    renters: pl.DataFrame | None = None,
    pulled_on: date | None = None,
) -> pl.DataFrame:
    """Every scoping-document stat for one county's case table.

    ``as_of`` is how current the outcome data are; ``pulled_on`` is when the case table was
    pulled, which defaults to ``as_of``. They differ where filings refresh daily but outcomes
    arrive in deliveries. ``renters`` is a table of renter-household estimates
    (``denominators.fetch``). Without it, the filing rate is marked not computed.
    """
    m = measures.all_measures(cases, as_of=as_of)
    rates = _rates(cases, renters) if renters is not None else pl.DataFrame()
    if renters is not None and rates.is_empty():
        raise ValueError("Renter-household estimates were supplied but no filing rate came out.")

    parts = []
    for topic in scope.TOPICS:
        for stat in topic.stats:
            if stat.measure == "filing_rate" and not rates.is_empty():
                rows = rates
            elif stat.measure and stat.measure != "filing_rate":
                rows = m.filter(pl.col("measure") == stat.measure)
            else:
                rows = pl.DataFrame()

            label = {
                "county": county,
                "topic": topic.name,
                "indicator": topic.indicator,
                "stat": stat.text,
            }
            if rows.is_empty():
                parts.append(
                    pl.DataFrame([{**label, "measure": stat.measure, "status": "not computed"}])
                )
            else:
                parts.append(
                    rows.with_columns(
                        *[pl.lit(v).alias(k) for k, v in label.items()],
                        pl.lit("computed").alias("status"),
                    )
                )

    out = pl.concat(parts, how="diagonal_relaxed").with_columns(
        pl.lit(as_of).alias("data_as_of"),
        pl.lit(pulled_on or as_of).alias("pulled_on"),
        pl.lit(__version__).alias("sb38_version"),
    )
    for col in COLUMNS:
        if col not in out.columns:
            out = out.with_columns(pl.lit(None).alias(col))
    return out.select(COLUMNS)


def write(table: pl.DataFrame, path: str | Path) -> Path:
    """Write a results table to CSV.

    Only a table with exactly the results columns is written. That keeps the case table, which
    is data rather than analysis output, from ever going out through here.
    """
    if table.columns != COLUMNS:
        raise ValueError("Only a results table from export.results() can be written here.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.write_csv(path)
    return path
