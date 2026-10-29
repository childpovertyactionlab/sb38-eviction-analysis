"""Display tables: a measure laid out with the years side by side, benchmarks against 2026.

Pure presentation over ``sb38.measures`` output. Nothing here computes a new figure.
"""

from __future__ import annotations

import polars as pl

from sb38.measures import BENCHMARKS, TREATED

MONTH_NAMES = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May"}


def table(measures: pl.DataFrame, measure: str, *, percent: bool = False) -> pl.DataFrame:
    """One measure: a Jan–May row then one row per month, years across, changes at the end.

    ``percent`` formats shares as percentages and reports changes in percentage points, the
    only honest way to describe the change in a rate.
    """
    df = measures.filter(pl.col("measure") == measure)
    wide = df.pivot(on="year", index=["frame", "month"], values="value")
    years = sorted(c for c in wide.columns if c.isdigit())
    wide = wide.with_columns(
        pl.when(pl.col("frame") == "jan_may")
        .then(pl.lit("Jan–May"))
        .otherwise(pl.col("month").replace_strict(MONTH_NAMES, default=None))
        .alias("period"),
        (pl.col("frame") != "jan_may").alias("_order"),
    ).sort("_order", "month")

    changes = []
    for b in BENCHMARKS:
        if str(b) in years and str(TREATED) in years:
            diff = pl.col(str(TREATED)) - pl.col(str(b))
            if percent:
                changes.append((diff * 100).round(1).alias(f"pts vs {b}"))
            else:
                changes.append(
                    ((pl.col(str(TREATED)) / pl.col(str(b)) - 1) * 100).round(1).alias(f"% vs {b}")
                )
    counts = not percent and all(
        v is None or float(v).is_integer() for y in years for v in wide.get_column(y).to_list()
    )
    if percent:
        fmt = [(pl.col(y) * 100).round(1).alias(y) for y in years]
    elif counts:
        fmt = [pl.col(y).cast(pl.Int64).alias(y) for y in years]
    else:
        fmt = [pl.col(y).round(1).alias(y) for y in years]
    return wide.select(["period", *fmt, *changes])


def denominators_used(measures: pl.DataFrame, measure: str) -> pl.DataFrame:
    """The numerator and denominator behind each Jan–May figure, for the record."""
    return (
        measures.filter((pl.col("measure") == measure) & (pl.col("frame") == "jan_may"))
        .select("year", "numerator", "denominator", "value", "note")
        .sort("year")
    )
