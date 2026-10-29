"""Case-level outcomes for the discontinuity prototype, each inside a fixed window from filing.

Every outcome is measured on every filed case, so nothing is conditioned on a case having an
outcome yet. A window that holds a court-closure day is extended by the number of closure days
in it. An outcome whose window runs past the data date is left empty for that case.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl

from sb38 import schema

CUTOFF_YEARS = (2024, 2025, 2026)
TREATED = 2026
DONUT = (-12, 6)  # days from January 1 left out at every cutoff: December 20 to January 7
BANDWIDTH = 30

PRIMARY = ("default_60", "judgment_60", "dismissed_60", "disposed_21")
SECONDARY = ("disposed_30", "disposed_60", "default_share_60", "judgment_by_9")
EXPLORATORY = ("writ_90", "appeal_60", "early_writ")
ALL = PRIMARY + SECONDARY + EXPLORATORY


def _closure_days(k: int, closures: Sequence[date]) -> pl.Expr:
    """Closure days that fall in the k days after filing."""
    if not closures:
        return pl.lit(0)
    f = pl.col("filed_date")
    return pl.sum_horizontal(
        [
            ((pl.lit(c) > f) & (pl.lit(c) <= f + pl.duration(days=k))).cast(pl.Int32)
            for c in closures
        ]
    )


def _deadline(k: int, closures: Sequence[date]) -> pl.Expr:
    return pl.col("filed_date") + pl.duration(days=_closure_days(k, closures) + k)


def _flag(cond: pl.Expr, k: int, closures: Sequence[date], as_of: date) -> pl.Expr:
    """1 or 0 where the k-day window has closed by the data date, empty where it hasn't."""
    return (
        pl.when(_deadline(k, closures) <= pl.lit(as_of))
        .then(cond.fill_null(False).cast(pl.Int8))
        .otherwise(None)
    )


def case_outcomes(
    cases: pl.DataFrame, *, as_of: date, closures: Sequence[date] = ()
) -> pl.DataFrame:
    """Residential cases with every outcome in ``ALL`` as a 0/1 column.

    Cases filed on a court-closure day are left out. Writ and appeal outcomes are empty when the
    case table has no writ or appeal column.
    """
    cls = pl.col("disposition_class")
    judged = cls.is_in(schema.JUDGMENT_ENTERED)
    days = (pl.col("disposition_date") - pl.col("filed_date")).dt.total_days()

    def disposed(k: int) -> pl.Expr:
        return pl.col("disposition_date").is_not_null() & (
            pl.col("disposition_date") <= _deadline(k, closures)
        )

    def event_within(col: str, k: int) -> pl.Expr:
        if col not in cases.columns:
            return pl.lit(None, dtype=pl.Boolean)
        return pl.col(col).is_not_null() & (pl.col(col) <= _deadline(k, closures))

    if "writ_issued_date" in cases.columns:
        gap = (pl.col("writ_issued_date") - pl.col("disposition_date")).dt.total_days()
        early_writ = judged & gap.is_between(0, 5)
    else:
        early_writ = pl.lit(None, dtype=pl.Boolean)

    out = cases.filter(pl.col("is_residential") & ~pl.col("filed_date").is_in(list(closures)))
    return out.with_columns(
        _flag(disposed(60) & (cls == "default_judgment"), 60, closures, as_of).alias("default_60"),
        _flag(disposed(60) & judged, 60, closures, as_of).alias("judgment_60"),
        _flag(disposed(60) & cls.is_in(schema.DISMISSED_OR_WITHDRAWN), 60, closures, as_of).alias(
            "dismissed_60"
        ),
        _flag(disposed(21), 21, closures, as_of).alias("disposed_21"),
        _flag(disposed(30), 30, closures, as_of).alias("disposed_30"),
        _flag(disposed(60), 60, closures, as_of).alias("disposed_60"),
        pl.when((_deadline(60, closures) <= pl.lit(as_of)) & disposed(60))
        .then((cls == "default_judgment").cast(pl.Int8))
        .otherwise(None)
        .alias("default_share_60"),
        _flag(judged & (days <= 9), 9, closures, as_of).alias("judgment_by_9"),
        _flag(event_within("writ_issued_date", 90), 90, closures, as_of).alias("writ_90"),
        _flag(event_within("appeal_date", 60), 60, closures, as_of).alias("appeal_60"),
        _flag(early_writ, 90, closures, as_of).alias("early_writ"),
    )


def running(cutoff_year: int) -> pl.Expr:
    """Days from January 1 of the cutoff year to the filing date."""
    return (pl.col("filed_date") - pl.lit(date(cutoff_year, 1, 1))).dt.total_days()
