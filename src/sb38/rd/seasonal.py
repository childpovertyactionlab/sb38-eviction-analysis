"""The main estimate: cases filed after January 1, 2026 against the same weeks of earlier seasons.

Each season runs October 1 to May 31 and is aligned on its own January 1. Week indicators take
out the holidays and ordinary seasonality, season indicators take out each season's level, and
court indicators take out court mix. What is left is how far the 2025–26 season moved after
January 1 relative to where the earlier seasons moved in the same weeks.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

import polars as pl

from sb38.rd import checks, estimate
from sb38.rd.eventstudy import season_weeks
from sb38.rd.outcomes import CUTOFF_YEARS, PRIMARY, TREATED


def _cluster() -> pl.Expr:
    return pl.concat_str("season", "week", "court", separator="|").rank("dense").alias("cluster")


def difference(
    oc: pl.DataFrame,
    outcome: str,
    *,
    treated: int = TREATED,
    seasons: Sequence[int] = CUTOFF_YEARS,
    drop: Sequence[tuple[date, date]] = (),
    label: str = "main",
) -> dict:
    """The coefficient on "filed in the treated season, on or after January 1".

    ``drop`` leaves out cases filed in the given date ranges.
    """
    import statsmodels.formula.api as smf

    s = season_weeks(oc).filter(
        pl.col("season").is_in(list(seasons)) & pl.col(outcome).is_not_null()
    )
    for lo, hi in drop:
        s = s.filter(~pl.col("filed_date").is_between(lo, hi))
    s = s.with_columns(
        ((pl.col("season") == treated) & (pl.col("r") >= 0)).cast(pl.Int8).alias("post"),
        _cluster(),
    )
    df = estimate.to_pandas(s.select(outcome, "post", "week", "season", "court", "cluster"))
    fit = smf.ols(f"{outcome} ~ post + C(week) + C(season) + C(court)", df).fit(
        cov_type="cluster", cov_kwds={"groups": df["cluster"]}
    )
    comparison = s.filter((pl.col("season") != treated) & (pl.col("r") >= 0))
    se = float(fit.bse["post"])
    return {
        "outcome": outcome,
        "estimate": label,
        "tau": float(fit.params["post"]),
        "se": se,
        "mde": estimate.MDE_MULTIPLIER * se,
        "n_treated_after": int(s.get_column("post").sum()),
        "n_total": s.height,
        "comparison_mean_after": float(comparison.get_column(outcome).cast(pl.Float64).mean()),
    }


def exposed(
    closures: Iterable[date], *, year: int = TREATED, window: int = 21
) -> list[tuple[date, date]]:
    """Filing-date ranges whose ``window``-day follow-up reaches a closure in ``year``'s January–May."""
    days = sorted(c for c in closures if c.year == year and c.month <= 5)
    runs: list[list[date]] = []
    for d in days:
        if runs and (d - runs[-1][-1]).days == 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return [(r[0] - timedelta(days=window), r[-1]) for r in runs]


def estimates(oc: pl.DataFrame, outcomes: Iterable[str]) -> pl.DataFrame:
    """The main estimate per outcome, with Holm-adjusted p-values on the primary outcomes."""
    t = estimate._finish([difference(oc, o) for o in outcomes])
    prim = t.filter(pl.col("outcome").is_in(PRIMARY))
    adj = dict(
        zip(prim.get_column("outcome"), estimate.holm(prim.get_column("p").to_list()), strict=True)
    )
    return t.with_columns(
        pl.col("outcome").replace_strict(adj, default=None, return_dtype=pl.Float64).alias("p_holm")
    )


def sensitivity(oc: pl.DataFrame, *, closures: Sequence[date] = ()) -> pl.DataFrame:
    """Storm-affected cases left out, and the 2023–24 season dropped."""
    storm = exposed(closures)
    rows = []
    for o in PRIMARY:
        if storm:
            rows.append(difference(oc, o, drop=storm, label="storm-affected cases left out"))
        rows.append(difference(oc, o, seasons=(2025, TREATED), label="2023–24 season dropped"))
    return estimate._finish(rows)


def placebo_season(oc: pl.DataFrame) -> pl.DataFrame:
    """2024–25 treated as if a law changed on January 1, 2025, against 2023–24."""
    return estimate._finish(
        [
            difference(oc, o, treated=2025, seasons=(2024, 2025), label="placebo: 2024–25")
            for o in PRIMARY
        ]
    )


def filing_volume(cases: pl.DataFrame, *, closures: Sequence[date] = ()) -> pl.DataFrame:
    """Weekly residential filings, log scale, on the same model.

    The first and last weeks of each season are partial and left out, as is any week holding a
    court-closure day.
    """
    import statsmodels.formula.api as smf

    s = season_weeks(cases.filter(pl.col("is_residential")))
    weeks = s.group_by("season", "week").agg(pl.len().alias("n"))
    closed = (
        season_weeks(pl.DataFrame({"filed_date": list(closures)}, schema={"filed_date": pl.Date}))
        .select("season", "week")
        .unique()
        if closures
        else s.select("season", "week").clear()
    )
    bounds = weeks.group_by("season").agg(
        pl.col("week").min().alias("lo"), pl.col("week").max().alias("hi")
    )
    weeks = (
        weeks.join(bounds, on="season")
        .filter((pl.col("week") > pl.col("lo")) & (pl.col("week") < pl.col("hi")))
        .join(closed, on=["season", "week"], how="anti")
        .with_columns(
            ((pl.col("season") == TREATED) & (pl.col("week") >= 0)).cast(pl.Int8).alias("post"),
            pl.col("n").log().alias("log_n"),
        )
    )
    df = estimate.to_pandas(weeks.select("log_n", "post", "week", "season"))
    fit = smf.ols("log_n ~ post + C(week) + C(season)", df).fit(cov_type="HC1")
    se = float(fit.bse["post"])
    return estimate._finish(
        [
            {
                "outcome": "log_weekly_filings",
                "estimate": "main",
                "tau": float(fit.params["post"]),
                "se": se,
                "mde": estimate.MDE_MULTIPLIER * se,
                "n_total": weeks.height,
            }
        ]
    )


def composition(oc: pl.DataFrame, outcomes: Sequence[str] = PRIMARY) -> pl.DataFrame:
    """The main estimate on number of defendants and on predicted-outcome indexes."""
    rows = [difference(oc.filter(pl.col("defendant_count").is_not_null()), "defendant_count")]
    for o in outcomes:
        idx = checks.predicted_index(oc, o)
        rows.append(difference(idx, f"predicted_{o}"))
    return estimate._finish(rows)


def weekly_means(oc: pl.DataFrame, outcome: str) -> pl.DataFrame:
    """Raw weekly outcome means by season, for the chart beside the event study."""
    return (
        season_weeks(oc)
        .filter(pl.col(outcome).is_not_null())
        .group_by("season", "week")
        .agg(pl.len().alias("n"), pl.col(outcome).cast(pl.Float64).mean().alias("mean"))
        .with_columns(pl.lit(outcome).alias("outcome"))
        .sort("season", "week")
    )
