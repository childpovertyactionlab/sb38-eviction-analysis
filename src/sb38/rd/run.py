"""Everything the analysis plan calls for, from one case table. Draft prototype."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl

from sb38.rd import checks, estimate, eventstudy, seasonal
from sb38.rd.outcomes import BANDWIDTH, EXPLORATORY, PRIMARY, SECONDARY, case_outcomes

PLACEBO_START = date(2023, 7, 1)
PLACEBO_END = date(2025, 10, 31)


def run(
    cases: pl.DataFrame,
    *,
    as_of: date,
    closures: Sequence[date] = (),
    exploratory: bool = False,
) -> dict[str, pl.DataFrame]:
    """The seasonal difference and its checks, the event study, and the discontinuity check.

    Exploratory outcomes stay off until the writ and appeal field definitions are confirmed.
    """
    oc = case_outcomes(cases, as_of=as_of, closures=closures)
    outcomes = PRIMARY + SECONDARY + (EXPLORATORY if exploratory else ())

    rd_dates = checks.clear_dates(PLACEBO_START, PLACEBO_END, h=BANDWIDTH, closures=closures)
    rd_placebo = []
    for outcome in PRIMARY:
        obs = checks.observed(oc, outcome)
        rd_placebo.append(
            {
                "outcome": outcome,
                **obs,
                **checks.placebo_rank(checks.placebo_dates(oc, outcome, rd_dates), obs),
            }
        )

    return {
        "estimates": seasonal.estimates(oc, outcomes),
        "sensitivity": seasonal.sensitivity(oc, closures=closures),
        "placebo_season": seasonal.placebo_season(oc),
        "filing_volume": seasonal.filing_volume(cases, closures=closures),
        "composition": seasonal.composition(oc),
        "event_study": pl.concat([eventstudy.event_study(oc, o) for o in PRIMARY]),
        "weekly_means": pl.concat([seasonal.weekly_means(oc, o) for o in PRIMARY]),
        "rd_check": estimate.estimates(oc, PRIMARY),
        "rd_sensitivity": pl.concat(
            [
                estimate.estimates(oc, PRIMARY, h=h, drop_thanksgiving=True).with_columns(
                    pl.lit(f"{h} days, Thanksgiving week dropped").alias("spec")
                )
                for h in (45, 60)
            ],
            how="diagonal_relaxed",
        ),
        "rd_placebo_dates": pl.DataFrame(rd_placebo),
    }
