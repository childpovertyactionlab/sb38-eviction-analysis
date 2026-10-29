"""Checks on the design: placebo dates, filing volume and composition at the cutoff."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np
import polars as pl

from sb38.rd import estimate
from sb38.rd.outcomes import BANDWIDTH, CUTOFF_YEARS, DONUT, TREATED, running

HALF_DONUT = (DONUT[1] - DONUT[0]) // 2  # a symmetric donut as wide as the holiday one


def _in_holiday_season(d: date) -> bool:
    return (d.month == 11 and d.day >= 15) or d.month == 12 or (d.month == 1 and d.day <= 15)


def clear_dates(
    start: date, end: date, *, h: int = BANDWIDTH, closures: Sequence[date] = ()
) -> list[date]:
    """Weekdays whose ±h window holds no holiday-season day and no court closure."""
    out = []
    d = start
    while d <= end:
        window = [d + timedelta(days=i) for i in range(-h, h + 1)]
        if (
            d.weekday() < 5
            and not any(_in_holiday_season(w) for w in window)
            and not any(c in window for c in closures)
        ):
            out.append(d)
        d += timedelta(days=1)
    return out


def placebo_dates(
    oc: pl.DataFrame,
    outcome: str,
    dates: Sequence[date],
    *,
    h: int = BANDWIDTH,
    half_donut: int = HALF_DONUT,
) -> pl.DataFrame:
    """The conventional discontinuity at each placebo date, and its change from a year before."""
    day = estimate.daily(oc, outcome)
    ordinal = np.array([d.toordinal() for d in day.get_column("filed_date")])
    n = day.get_column("n").to_numpy().astype(float)
    ybar = day.get_column("ybar").to_numpy()

    def at(d: date) -> float:
        r = ordinal - d.toordinal()
        keep = np.abs(r) > half_donut
        return estimate.local_linear(r[keep].astype(float), n[keep], ybar[keep], h)[0]

    taus = {d: at(d) for d in dates}

    def year_before(d: date) -> date | None:
        try:
            return d.replace(year=d.year - 1)
        except ValueError:  # February 29
            return None

    rows = []
    for d, tau in taus.items():
        prior = year_before(d)
        rows.append(
            {
                "date": d,
                "tau": tau,
                "change_from_year_before": tau - taus[prior] if prior in taus else None,
            }
        )
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "tau": pl.Float64, "change_from_year_before": pl.Float64},
    )


def observed(oc: pl.DataFrame, outcome: str, *, h: int = BANDWIDTH) -> dict:
    """The conventional estimate at each real cutoff and their difference, as the placebos are."""
    day = estimate.daily(oc, outcome)
    ordinal = np.array([d.toordinal() for d in day.get_column("filed_date")])
    n = day.get_column("n").to_numpy().astype(float)
    ybar = day.get_column("ybar").to_numpy()
    taus = {}
    for y in CUTOFF_YEARS:
        r = ordinal - date(y, 1, 1).toordinal()
        keep = (r < DONUT[0]) | (r > DONUT[1])
        taus[y] = estimate.local_linear(r[keep].astype(float), n[keep], ybar[keep], h)[0]
    placebo = [taus[y] for y in CUTOFF_YEARS if y != TREATED]
    return {"tau_treated": taus[TREATED], "difference": taus[TREATED] - sum(placebo) / len(placebo)}


def placebo_rank(placebos: pl.DataFrame, obs: dict) -> dict:
    """Share of placebo dates with an estimate at least as large in absolute value."""
    tau = placebos.get_column("tau").drop_nans().drop_nulls().to_numpy()
    chg = placebos.get_column("change_from_year_before").drop_nans().drop_nulls().to_numpy()
    return {
        "placebo_dates": len(tau),
        "share_as_large_tau": float((np.abs(tau) >= abs(obs["tau_treated"])).mean()),
        "placebo_pairs": len(chg),
        "share_as_large_difference": float((np.abs(chg) >= abs(obs["difference"])).mean())
        if len(chg)
        else math.nan,
    }


def filing_volume(
    cases: pl.DataFrame,
    *,
    h: int = BANDWIDTH,
    closures: Sequence[date] = (),
) -> pl.DataFrame:
    """Jump in log weekday filings at each cutoff, with weekday indicators, and the difference.

    Weekdays with no filings at all (court holidays) are left out.
    """
    import statsmodels.api as sm

    counts = (
        cases.filter(pl.col("is_residential") & ~pl.col("filed_date").is_in(list(closures)))
        .group_by("filed_date")
        .agg(pl.len().alias("n"))
        .filter(pl.col("filed_date").dt.weekday() <= 5)
    )
    rows = []
    for y in CUTOFF_YEARS:
        s = counts.with_columns(running(y).alias("r")).filter(
            pl.col("r").is_between(-h, h) & ~pl.col("r").is_between(*DONUT)
        )
        r = s.get_column("r").to_numpy().astype(float)
        d = (r >= 0).astype(float)
        wd = s.get_column("filed_date").dt.weekday().to_numpy()
        dummies = np.column_stack([(wd == k).astype(float) for k in range(2, 6)])
        X = np.column_stack([np.ones_like(r), d, r, d * r, dummies])
        fit = sm.WLS(
            np.log(s.get_column("n").to_numpy()), X, weights=np.clip(1 - np.abs(r) / h, 0, None)
        ).fit(cov_type="HC1")
        rows.append(
            {
                "outcome": "log_weekday_filings",
                "estimate": str(y),
                "h": float(h),
                "tau": float(fit.params[1]),
                "se": float(fit.bse[1]),
                "n_left": int((d == 0).sum()),
                "n_right": int((d == 1).sum()),
            }
        )
    rows.append(estimate.difference(rows))
    return estimate._finish(rows)


def predicted_index(oc: pl.DataFrame, outcome: str) -> pl.DataFrame:
    """Each case's outcome predicted from court and number of defendants.

    Fitted on cases filed January–May 2024 and 2025. Cases in courts absent from that fit get
    no prediction.
    """
    import statsmodels.formula.api as smf

    cols = ["court", "defendant_count", outcome]
    fit_rows = oc.filter(
        pl.col("filed_date").dt.year().is_in([2024, 2025])
        & (pl.col("filed_date").dt.month() <= 5)
        & pl.all_horizontal(pl.col(cols).is_not_null())
    )
    model = smf.ols(
        f"{outcome} ~ C(court) + defendant_count", estimate.to_pandas(fit_rows.select(cols))
    )
    fit = model.fit()
    known = set(fit_rows.get_column("court").unique())
    usable = oc.filter(pl.col("court").is_in(list(known)) & pl.col("defendant_count").is_not_null())
    pred = fit.predict(estimate.to_pandas(usable.select("court", "defendant_count")))
    return usable.with_columns(pl.Series(f"predicted_{outcome}", np.asarray(pred, dtype=float)))


def composition(oc: pl.DataFrame, outcomes: Sequence[str], *, h: int = BANDWIDTH) -> pl.DataFrame:
    """The same difference in discontinuities on number of defendants and predicted outcomes."""
    parts = [estimate.estimates(oc, ["defendant_count"], h=h)]
    for outcome in outcomes:
        idx = predicted_index(oc, outcome)
        parts.append(estimate.estimates(idx, [f"predicted_{outcome}"], h=h))
    return pl.concat(parts, how="diagonal_relaxed")
