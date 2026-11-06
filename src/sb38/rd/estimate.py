"""Local linear discontinuities at each January 1 cutoff, and their difference.

The main estimates come from ``rdrobust`` (robust bias-corrected, clustered by filing date),
the same package that exists in R and Stata. ``local_linear`` is a plain conventional
estimator on daily means, used where thousands of estimates are needed (placebo dates); it
gives the same point estimate as case-level weighted least squares with a triangular kernel.
"""

from __future__ import annotations

import contextlib
import io
import math
import warnings
from collections.abc import Iterable, Sequence
from datetime import date, timedelta

import numpy as np
import polars as pl

from sb38.rd.outcomes import BANDWIDTH, CUTOFF_YEARS, DONUT, PRIMARY, TREATED, running

Z95 = 1.959963984540054
MDE_MULTIPLIER = 2.8  # 80% power, 5% two-sided


def _p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def thanksgiving_week(year: int) -> tuple[date, date]:
    """Monday to Sunday of the week holding the fourth Thursday of November."""
    first = date(year, 11, 1)
    thursday = first + timedelta(days=(3 - first.weekday()) % 7 + 21)
    return thursday - timedelta(days=3), thursday + timedelta(days=3)


def sample(
    oc: pl.DataFrame,
    outcome: str,
    year: int,
    *,
    h: int = BANDWIDTH,
    donut: tuple[int, int] = DONUT,
    drop_thanksgiving: bool = False,
) -> pl.DataFrame:
    """Cases inside the bandwidth around January 1 of ``year``, outside the donut."""
    s = oc.with_columns(running(year).alias("r")).filter(
        pl.col(outcome).is_not_null()
        & pl.col("r").is_between(-h, h)
        & ~pl.col("r").is_between(*donut)
    )
    if drop_thanksgiving:
        lo, hi = thanksgiving_week(year - 1)
        s = s.filter(~pl.col("filed_date").is_between(lo, hi))
    return s


def local_linear(
    r: np.ndarray, n: np.ndarray, ybar: np.ndarray, h: float
) -> tuple[float, float, int, int]:
    """Conventional local linear discontinuity at r = 0 from daily cell means.

    Triangular kernel. Standard errors clustered by day: with day-level regressors the
    case-level score for a day is its weight times its mean residual.
    """
    k = np.clip(1 - np.abs(r) / h, 0, None)
    keep = (k > 0) & (n > 0)
    r, n, ybar, k = r[keep], n[keep], ybar[keep], k[keep]
    d = (r >= 0).astype(float)
    if len(np.unique(r[d == 0])) < 2 or len(np.unique(r[d == 1])) < 2:
        return math.nan, math.nan, 0, 0
    X = np.column_stack([np.ones_like(r, dtype=float), d, r, d * r])
    w = n * k
    B = X.T @ (X * w[:, None])
    beta = np.linalg.solve(B, X.T @ (w * ybar))
    s = X * (w * (ybar - X @ beta))[:, None]
    g = len(r)
    V = np.linalg.solve(B, np.linalg.solve(B, s.T @ s).T) * g / (g - 1)
    return float(beta[1]), float(math.sqrt(V[1, 1])), int(n[d == 0].sum()), int(n[d == 1].sum())


def to_pandas(df: pl.DataFrame):
    """A pandas frame for statsmodels, built from numpy so pyarrow isn't needed."""
    import pandas as pd

    return pd.DataFrame({c: df.get_column(c).to_numpy() for c in df.columns})


def daily(oc: pl.DataFrame, outcome: str) -> pl.DataFrame:
    """Daily case counts and outcome means, for ``local_linear``."""
    return (
        oc.filter(pl.col(outcome).is_not_null())
        .group_by("filed_date")
        .agg(pl.len().alias("n"), pl.col(outcome).cast(pl.Float64).mean().alias("ybar"))
        .sort("filed_date")
    )


def rd(
    oc: pl.DataFrame,
    outcome: str,
    year: int,
    *,
    h: int = BANDWIDTH,
    donut: tuple[int, int] = DONUT,
    drop_thanksgiving: bool = False,
) -> dict:
    """``rdrobust`` at January 1 of ``year``, clustered by filing date.

    The count of filing days on each side is returned too: those are the clusters, and with a
    wide holiday donut there are not many.
    """
    from rdrobust import rdrobust

    s = sample(oc, outcome, year, h=h, donut=donut, drop_thanksgiving=drop_thanksgiving)
    x = s.get_column("r").cast(pl.Float64).to_numpy()
    y = s.get_column(outcome).cast(pl.Float64).to_numpy()
    with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        fit = rdrobust(y, x, c=0, h=h, cluster=x, masspoints="adjust")
    return {
        "outcome": outcome,
        "estimate": str(year),
        "h": float(np.asarray(fit.bws).ravel()[0]),
        "tau": float(fit.coef.iloc[2, 0]),
        "se": float(fit.se.iloc[2, 0]),
        "tau_conventional": float(fit.coef.iloc[0, 0]),
        "se_conventional": float(fit.se.iloc[0, 0]),
        "n_left": int(fit.N_h[0]),
        "n_right": int(fit.N_h[1]),
        "days_left": int(np.unique(x[x < 0]).size),
        "days_right": int(np.unique(x[x >= 0]).size),
        "mean_left": float(y[x < 0].mean()) if (x < 0).any() else math.nan,
    }


def difference(rows: Sequence[dict], treated: int = TREATED) -> dict:
    """tau(treated) minus the mean of the placebo-year taus, from disjoint samples."""
    by_year = {int(r["estimate"]): r for r in rows}
    t = by_year[treated]
    placebo = [by_year[y] for y in by_year if y != treated]
    tau = t["tau"] - sum(p["tau"] for p in placebo) / len(placebo)
    se = math.sqrt(t["se"] ** 2 + sum(p["se"] ** 2 for p in placebo) / len(placebo) ** 2)
    return {
        "outcome": t["outcome"],
        "estimate": "difference",
        "h": t["h"],
        "tau": tau,
        "se": se,
        "n_left": sum(r["n_left"] for r in rows),
        "n_right": sum(r["n_right"] for r in rows),
        "days_left": min(r.get("days_left", 0) for r in rows),
        "days_right": min(r.get("days_right", 0) for r in rows),
        "mde": MDE_MULTIPLIER * se,
    }


def holm(p: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order."""
    m = len(p)
    order = sorted(range(m), key=lambda i: p[i])
    adj = [0.0] * m
    running_max = 0.0
    for rank, i in enumerate(order):
        running_max = max(running_max, min(1.0, (m - rank) * p[i]))
        adj[i] = running_max
    return adj


def _finish(rows: list[dict]) -> pl.DataFrame:
    t = pl.DataFrame(rows)
    return t.with_columns(
        (pl.col("tau") - Z95 * pl.col("se")).alias("ci_low"),
        (pl.col("tau") + Z95 * pl.col("se")).alias("ci_high"),
        pl.struct("tau", "se")
        .map_elements(lambda s: _p(s["tau"] / s["se"]), return_dtype=pl.Float64)
        .alias("p"),
    )


def estimates(
    oc: pl.DataFrame,
    outcomes: Iterable[str],
    *,
    h: int = BANDWIDTH,
    years: Sequence[int] = CUTOFF_YEARS,
    drop_thanksgiving: bool = False,
) -> pl.DataFrame:
    """Each cutoff's discontinuity and the difference, per outcome.

    Holm-adjusted p-values are added for the difference on the primary outcomes.
    """
    rows = []
    for outcome in outcomes:
        per_year = [rd(oc, outcome, y, h=h, drop_thanksgiving=drop_thanksgiving) for y in years]
        rows += per_year + [difference(per_year)]
    t = _finish(rows)
    prim = t.filter((pl.col("estimate") == "difference") & pl.col("outcome").is_in(PRIMARY))
    adj = dict(zip(prim.get_column("outcome"), holm(prim.get_column("p").to_list()), strict=True))
    return t.with_columns(
        pl.when(pl.col("estimate") == "difference")
        .then(pl.col("outcome").replace_strict(adj, default=None, return_dtype=pl.Float64))
        .otherwise(None)
        .alias("p_holm")
    )
