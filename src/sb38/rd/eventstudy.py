"""Weekly event study across three seasons aligned on January 1."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from sb38.rd import estimate
from sb38.rd.outcomes import CUTOFF_YEARS, TREATED, running

REFERENCE_WEEK = -3  # December 11–17: the last full week before the holiday donut


def season_weeks(oc: pl.DataFrame) -> pl.DataFrame:
    """Cases filed October 1 to May 31 of each season, with season and week from January 1."""
    parts = []
    for y in CUTOFF_YEARS:
        parts.append(
            oc.filter(pl.col("filed_date").is_between(date(y - 1, 10, 1), date(y, 5, 31)))
            .with_columns(pl.lit(y).alias("season"), running(y).alias("r"))
            .with_columns((pl.col("r") // 7).alias("week"))
        )
    return pl.concat(parts)


def event_study(oc: pl.DataFrame, outcome: str) -> pl.DataFrame:
    """Treated-season-by-week coefficients from a linear probability model.

    Week, season and court indicators. Standard errors are clustered by court and filing week:
    clustering by filing week alone would leave each treated-season coefficient resting on one
    cluster, whose own noise then drops out of its standard error.
    """
    import statsmodels.formula.api as smf

    s = season_weeks(oc).filter(pl.col(outcome).is_not_null())
    s = s.with_columns(
        pl.when((pl.col("season") == TREATED) & (pl.col("week") != REFERENCE_WEEK))
        .then(pl.col("week").cast(pl.Utf8))
        .otherwise(pl.lit("ref"))
        .alias("tw"),
        pl.concat_str("season", "week", "court", separator="|").rank("dense").alias("cluster"),
    )
    df = estimate.to_pandas(s.select(outcome, "week", "season", "court", "tw", "cluster"))
    fit = smf.ols(f"{outcome} ~ C(tw, Treatment('ref')) + C(week) + C(season) + C(court)", df).fit(
        cov_type="cluster", cov_kwds={"groups": df["cluster"]}
    )

    prefix = "C(tw, Treatment('ref'))[T."
    names = [n for n in fit.params.index if n.startswith(prefix)]
    rows = [
        {"week": int(n[len(prefix) : -1]), "coef": float(fit.params[n]), "se": float(fit.bse[n])}
        for n in names
    ]
    rows.append({"week": REFERENCE_WEEK, "coef": 0.0, "se": 0.0})

    # Joint test that the treated season tracked the earlier ones before the holiday weeks.
    pre = [
        i for i, n in enumerate(fit.params.index) if n in names and int(n[len(prefix) : -1]) < -2
    ]
    R = np.zeros((len(pre), len(fit.params)))
    for row, i in enumerate(pre):
        R[row, i] = 1.0
    pre_p = float(fit.wald_test(R, scalar=True).pvalue) if pre else float("nan")

    counts = s.filter(pl.col("season") == TREATED).group_by("week").agg(pl.len().alias("n"))
    return (
        pl.DataFrame(rows)
        .sort("week")
        .join(counts, on="week", how="left")
        .with_columns(
            pl.lit(outcome).alias("outcome"),
            (pl.lit(date(TREATED, 1, 1)) + pl.duration(days=7 * pl.col("week"))).alias(
                "week_start"
            ),
            (pl.col("coef") - 1.96 * pl.col("se")).alias("ci_low"),
            (pl.col("coef") + 1.96 * pl.col("se")).alias("ci_high"),
            pl.lit(pre_p).alias("pre_period_p"),
        )
    )
