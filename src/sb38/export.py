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

from sb38 import __version__, denominators, measures, procedure, scope

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

COVERAGE_COLUMNS = [
    "county",
    "field",
    "court",
    "year",
    "cases",
    "base",
    "recorded",
    "share",
    "threshold",
    "recording",
    "data_as_of",
    "sb38_version",
]


def closure_notes(closures: pl.DataFrame) -> pl.DataFrame:
    """One note per closure month, e.g. "Courts closed 2026-01-26 to 2026-01-28 (Winter Storm
    Fern; dates unconfirmed)". ``closures`` has columns date, event and confirmed (yes/no).
    """
    day = pl.col("date") if closures.schema["date"] == pl.Date else pl.col("date").str.to_date()
    c = closures.with_columns(day.alias("date")).with_columns(
        pl.col("date").dt.year().alias("year"),
        pl.col("date").dt.month().cast(pl.Int8).alias("month"),
    )
    return (
        c.group_by("year", "month", "event")
        .agg(
            pl.col("date").min().alias("first"),
            pl.col("date").max().alias("last"),
            (pl.col("confirmed").str.to_lowercase() == "yes").all().alias("confirmed"),
        )
        .with_columns(
            pl.format(
                "Courts closed {}{} ({}{})",
                pl.col("first"),
                pl.when(pl.col("last") > pl.col("first"))
                .then(pl.format(" to {}", pl.col("last")))
                .otherwise(pl.lit("")),
                pl.col("event"),
                pl.when(pl.col("confirmed"))
                .then(pl.lit(""))
                .otherwise(pl.lit("; dates unconfirmed")),
            ).alias("closure_note")
        )
        .group_by("year", "month")
        .agg(pl.col("closure_note").sort().str.join("; "))
    )


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
    closures: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Every scoping-document stat for one county's case table.

    ``as_of`` is how current the outcome data are; ``pulled_on`` is when the case table was
    pulled, which defaults to ``as_of``. They differ where filings refresh daily but outcomes
    arrive in deliveries. ``renters`` is a table of renter-household estimates
    (``denominators.fetch``). Without it, the filing rate is marked not computed.
    ``closures`` (date, event, confirmed) flags every figure for a month in which courts closed.
    """
    m = pl.concat(
        [measures.all_measures(cases, as_of=as_of), procedure.all_measures(cases)],
        how="diagonal_relaxed",
    )
    rates = _rates(cases, renters) if renters is not None else pl.DataFrame()
    if renters is not None and rates.is_empty():
        raise ValueError("Renter-household estimates were supplied but no filing rate came out.")

    parts = []
    for topic in scope.TOPICS:
        for stat in topic.stats:
            if stat.measure == "filing_rate" and not rates.is_empty():
                rows = rates
            elif stat.measure and stat.measure != "filing_rate":
                wanted = [stat.measure] + ([stat.count] if stat.count else [])
                rows = m.filter(pl.col("measure").is_in(wanted))
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
    if closures is not None and closures.height:
        out = (
            out.with_columns(pl.col("month").cast(pl.Int8), pl.col("year").cast(pl.Int32))
            .join(
                closure_notes(closures).with_columns(pl.col("year").cast(pl.Int32)),
                on=["year", "month"],
                how="left",
            )
            .with_columns(
                pl.when(pl.col("closure_note").is_null())
                .then(pl.col("note"))
                .when(pl.col("note").is_null() | (pl.col("note") == ""))
                .then(pl.col("closure_note"))
                .otherwise(pl.format("{}; {}", pl.col("note"), pl.col("closure_note")))
                .alias("note")
            )
        )
    return out.select(COLUMNS)


def coverage(cases: pl.DataFrame, *, as_of: date, county: str) -> pl.DataFrame:
    """Share of each procedure field recorded, by court and cohort year (``procedure.coverage``)."""
    cov = procedure.coverage(cases)
    if cov.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in COVERAGE_COLUMNS})
    return cov.with_columns(
        pl.lit(county).alias("county"),
        pl.lit(as_of).alias("data_as_of"),
        pl.lit(__version__).alias("sb38_version"),
    ).select(COVERAGE_COLUMNS)


def write(table: pl.DataFrame, path: str | Path) -> Path:
    """Write a results or coverage table to CSV.

    Only a table with exactly the results or coverage columns is written. That keeps the case
    table, which is data rather than analysis output, from ever going out through here.
    """
    if table.columns not in (COLUMNS, COVERAGE_COLUMNS):
        raise ValueError("Only a results table from export.results() can be written here.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.write_csv(path)
    return path
