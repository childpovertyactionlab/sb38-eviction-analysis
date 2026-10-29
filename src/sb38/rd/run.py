"""Everything the analysis plan calls for, from one case table. Draft prototype."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl

from sb38.rd import checks, estimate, eventstudy
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
    """Main estimates, bandwidth sensitivity, design checks and event studies.

    Exploratory outcomes stay off until the writ and appeal field definitions are confirmed.
    """
    oc = case_outcomes(cases, as_of=as_of, closures=closures)
    outcomes = PRIMARY + SECONDARY + (EXPLORATORY if exploratory else ())

    main = estimate.estimates(oc, outcomes)
    sensitivity = pl.concat(
        [
            estimate.estimates(oc, PRIMARY, h=h, drop_thanksgiving=True).with_columns(
                pl.lit(f"{h} days, Thanksgiving week dropped").alias("spec")
            )
            for h in (45, 60)
        ],
        how="diagonal_relaxed",
    )

    dates = checks.clear_dates(PLACEBO_START, PLACEBO_END, h=BANDWIDTH, closures=closures)
    placebo_rows = []
    for outcome in PRIMARY:
        pd_ = checks.placebo_dates(oc, outcome, dates)
        placebo_rows.append(
            {
                "outcome": outcome,
                **checks.observed(oc, outcome),
                **checks.placebo_rank(pd_, checks.observed(oc, outcome)),
            }
        )

    return {
        "estimates": main,
        "sensitivity": sensitivity,
        "placebo_dates": pl.DataFrame(placebo_rows),
        "filing_volume": checks.filing_volume(cases, closures=closures),
        "composition": checks.composition(oc, PRIMARY),
        "event_study": pl.concat([eventstudy.event_study(oc, o) for o in PRIMARY]),
    }
