"""The settled measures: filings, default rate and case length.

Each function takes a case table in the schema (see ``sb38.schema``) and returns a long table.
It has one row per cohort, per frame, per measure, and it carries the numerator and denominator
beside the value, so no figure travels without what it was computed on.

Frames are ``jan_may`` (the whole January–May cohort) and ``month`` (each filing month). Both
come from the same rows, so the months always add up to the Jan–May total.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sb38.schema import JUDGMENT_ENTERED

COHORT_YEARS = (2024, 2025, 2026)
MONTHS = (1, 2, 3, 4, 5)
BENCHMARKS = (2024, 2025)
TREATED = 2026

# Columns every measure table carries, in this order.
COLUMNS = ["frame", "year", "month", "measure", "value", "numerator", "denominator", "note"]


def cohorts(cases: pl.DataFrame, years=COHORT_YEARS, months=MONTHS) -> pl.DataFrame:
    """Residential cases filed January–May of the cohort years, tagged with year and month."""
    return cases.filter(
        pl.col("is_residential")
        & pl.col("filed_date").dt.year().is_in(list(years))
        & pl.col("filed_date").dt.month().is_in(list(months))
    ).with_columns(
        pl.col("filed_date").dt.year().alias("year"),
        pl.col("filed_date").dt.month().alias("month"),
    )


def _by_frame(df: pl.DataFrame, aggs: list[pl.Expr]) -> pl.DataFrame:
    """Aggregate once over the Jan–May cohort and once per filing month, stacked."""
    whole = (
        df.group_by("year")
        .agg(aggs)
        .with_columns(pl.lit("jan_may").alias("frame"), pl.lit(None, dtype=pl.Int8).alias("month"))
    )
    monthly = df.group_by("year", "month").agg(aggs).with_columns(pl.lit("month").alias("frame"))
    return pl.concat(
        [whole, monthly.with_columns(pl.col("month").cast(pl.Int8))], how="diagonal_relaxed"
    )


def _long(wide: pl.DataFrame, specs: list[tuple[str, str, str]], note: str = "") -> pl.DataFrame:
    """Turn wide aggregates into the long layout: one row per measure."""
    parts = []
    for measure, num, den in specs:
        parts.append(
            wide.select(
                "frame",
                "year",
                "month",
                pl.lit(measure).alias("measure"),
                (pl.col(num) / pl.col(den)).alias("value")
                if den
                else pl.col(num).cast(pl.Float64).alias("value"),
                pl.col(num).cast(pl.Int64).alias("numerator"),
                (pl.col(den).cast(pl.Int64) if den else pl.lit(None, dtype=pl.Int64)).alias(
                    "denominator"
                ),
                pl.lit(note).alias("note"),
            )
        )
    return pl.concat(parts).select(COLUMNS).sort("measure", "frame", "year", "month")


def filings(cases: pl.DataFrame) -> pl.DataFrame:
    """Number of cases filed in each cohort, as the Jan–May total and by month."""
    wide = _by_frame(cohorts(cases), [pl.len().alias("filed")])
    return _long(wide, [("filings", "filed", "")])


def default_rate(cases: pl.DataFrame) -> pl.DataFrame:
    """Default judgments as a share of cases with an outcome, and of all cases filed."""
    df = cohorts(cases)
    wide = _by_frame(
        df,
        [
            pl.len().alias("filed"),
            pl.col("disposition_class").is_not_null().sum().alias("with_outcome"),
            (pl.col("disposition_class") == "default_judgment").sum().alias("defaults"),
        ],
    )
    return _long(
        wide,
        [
            ("default_rate", "defaults", "with_outcome"),
            ("default_share_of_filed", "defaults", "filed"),
            ("cases_with_outcome", "with_outcome", ""),
        ],
    )


def case_length(cases: pl.DataFrame, *, as_of: date) -> pl.DataFrame:
    """Days from filing to disposition, and the share of filed cases resolved within 30 and 60.

    ``as_of`` is the date the data were pulled. A within-N share is computed only when every
    case in the cohort has had N days to resolve by then; otherwise it is left out, not
    understated. Dispositions dated before filing are recording errors: they are dropped from
    the timing figures and counted in the ``note``.
    """
    df = cohorts(cases).with_columns(
        (pl.col("disposition_date") - pl.col("filed_date")).dt.total_days().alias("days")
    )
    bad = df.filter(pl.col("days") < 0)
    df = df.filter(pl.col("days").is_null() | (pl.col("days") >= 0))

    disposed = df.filter(pl.col("days").is_not_null())
    judged = disposed.filter(pl.col("disposition_class").is_in(list(JUDGMENT_ENTERED)))

    rows = []
    for subset, label in ((disposed, "any_disposition"), (judged, "judgment_only")):
        wide = _by_frame(
            subset,
            [
                pl.len().alias("n"),
                pl.col("days").mean().alias("mean_days"),
                pl.col("days").median().alias("median_days"),
            ],
        )
        for stat in ("mean_days", "median_days"):
            rows.append(
                wide.select(
                    "frame",
                    "year",
                    "month",
                    pl.lit(f"{stat}_{label}").alias("measure"),
                    pl.col(stat).cast(pl.Float64).alias("value"),
                    pl.lit(None, dtype=pl.Int64).alias("numerator"),
                    pl.col("n").cast(pl.Int64).alias("denominator"),
                    pl.lit("").alias("note"),
                )
            )

    for window in (30, 60):
        aggs = [
            pl.len().alias("filed"),
            (pl.col("days").is_not_null() & (pl.col("days") <= window)).sum().alias("resolved"),
        ]
        wide = _by_frame(df, aggs)
        share = _long(wide, [(f"resolved_within_{window}_days", "resolved", "filed")])
        # Drop any cohort or month whose last filing day hasn't had `window` days by as_of.
        last_day = pl.date(pl.col("year"), pl.coalesce(pl.col("month"), pl.lit(5)), 1)
        share = share.with_columns(_month_end(last_day).alias("_last")).filter(
            pl.col("_last") + timedelta(days=window) <= as_of
        )
        rows.append(share.drop("_last"))

    out = pl.concat(rows).select(COLUMNS).sort("measure", "frame", "year", "month")
    if bad.height:
        # Each note belongs to its own year's Jan–May row and to the month the case was filed in.
        excluded = pl.concat(
            [
                bad.group_by("year")
                .len()
                .with_columns(
                    pl.lit("jan_may").alias("frame"), pl.lit(None, dtype=pl.Int8).alias("month")
                ),
                bad.group_by("year", "month")
                .len()
                .with_columns(pl.lit("month").alias("frame"), pl.col("month").cast(pl.Int8)),
            ],
            how="diagonal_relaxed",
        ).select("frame", "year", "month", "len")
        timing = pl.col("measure").str.starts_with("mean_days") | pl.col("measure").str.starts_with(
            "median_days"
        )
        out = (
            out.join(excluded, on=["frame", "year", "month"], how="left", nulls_equal=True)
            .with_columns(
                pl.when(timing & pl.col("len").is_not_null())
                .then(pl.format("{} disposition(s) dated before filing, excluded", pl.col("len")))
                .otherwise(pl.col("note"))
                .alias("note")
            )
            .drop("len")
        )
    return out


def _month_end(first_of_month: pl.Expr) -> pl.Expr:
    return first_of_month.dt.month_end()


def compare(measures: pl.DataFrame) -> pl.DataFrame:
    """Set 2026 beside each benchmark year, with the change in value and in percent."""
    wide = measures.pivot(on="year", index=["measure", "frame", "month"], values="value")
    exprs = []
    for b in BENCHMARKS:
        if str(b) in wide.columns and str(TREATED) in wide.columns:
            exprs += [
                (pl.col(str(TREATED)) - pl.col(str(b))).alias(f"change_vs_{b}"),
                (pl.col(str(TREATED)) / pl.col(str(b)) - 1).alias(f"pct_change_vs_{b}"),
            ]
    return wide.with_columns(exprs).sort("measure", "frame", "month", nulls_last=False)


def all_measures(cases: pl.DataFrame, *, as_of: date) -> pl.DataFrame:
    """Every settled measure for one county's case table."""
    return pl.concat([filings(cases), default_rate(cases), case_length(cases, as_of=as_of)])
