"""The discontinuity prototype, on invented cases with known jumps planted in them.

Every January 1 carries a holiday jump of +0.04 in the default rate that lasts six weeks, and
the 2026 cutoff adds a lasting +0.08 on top. A working design recovers +0.08 from the
difference in discontinuities, not +0.12 from the raw 2026 discontinuity.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

pytest.importorskip("rdrobust")
pytest.importorskip("statsmodels")

from sb38.rd import checks, estimate, eventstudy  # noqa: E402
from sb38.rd.outcomes import PRIMARY, case_outcomes  # noqa: E402

AS_OF = date(2026, 8, 26)
HOLIDAY = 0.04
LAW = 0.08
COURTS = {"1-1": 0.30, "1-2": 0.36, "2-1": 0.33, "3-1": 0.28, "5-1": 0.38}


def make_cases(seed: int = 7, per_day: int = 200) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    days = []
    d = date(2023, 4, 1)
    while d <= date(2026, 6, 30):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    filed = np.repeat(np.array(days, dtype="datetime64[D]"), rng.poisson(per_day, len(days)))
    n = len(filed)

    years = filed.astype("datetime64[Y]").astype(int) + 1970
    jan1 = (years - 1970).astype("datetime64[Y]").astype("datetime64[D]")
    since = (filed - jan1).astype(int)
    court = rng.choice(list(COURTS), n)
    p_def = (
        np.vectorize(COURTS.get)(court)
        + HOLIDAY * (since < 45)
        + LAW * (filed >= np.datetime64("2026-01-01"))
    )

    u = rng.random(n)
    rest = (u - p_def) / (1 - p_def)
    cls = np.where(
        u < p_def,
        "default_judgment",
        np.where(
            rest < 0.25,
            "other_judgment",
            np.where(rest < 0.40, "dismissed", np.where(rest < 0.95, "nonsuit", "other")),
        ),
    )
    lag = np.round(8 + rng.gamma(2.0, 6.0, n)).astype(int)
    disp = filed + lag.astype("timedelta64[D]")
    pending = disp > np.datetime64(AS_OF)
    judged = np.isin(cls, ["default_judgment", "other_judgment"])
    has_writ = judged & (rng.random(n) < 0.25) & ~pending
    writ = disp + rng.integers(0, 20, n).astype("timedelta64[D]")
    return pl.DataFrame(
        {
            "case_id": [f"S{i:07d}" for i in range(n)],
            "court": court,
            "filed_date": filed,
            "is_residential": np.ones(n, dtype=bool),
            "cls": cls,
            "disp": disp,
            "writ": writ,
            "pending": pending,
            "has_writ": has_writ,
            "defendant_count": rng.integers(1, 4, n),
        }
    ).select(
        "case_id",
        "court",
        "filed_date",
        "is_residential",
        pl.when(~pl.col("pending")).then(pl.col("cls")).alias("disposition_class"),
        pl.when(~pl.col("pending")).then(pl.col("disp")).alias("disposition_date"),
        "defendant_count",
        pl.when(pl.col("has_writ")).then(pl.col("writ")).alias("writ_issued_date"),
    )


@pytest.fixture(scope="module")
def oc() -> pl.DataFrame:
    return case_outcomes(make_cases(), as_of=AS_OF)


def test_windows_extend_over_closures_and_stop_at_the_data_date() -> None:
    cases = pl.DataFrame(
        {
            "case_id": ["a", "b", "c", "d"],
            "court": ["x"] * 4,
            "filed_date": [date(2026, 1, 12)] * 3 + [date(2026, 8, 20)],
            "is_residential": [True] * 4,
            "disposition_class": ["default_judgment"] * 3 + [None],
            # day 21, day 23 (inside 21 + 2 closure days), day 24 (outside)
            "disposition_date": [date(2026, 2, 2), date(2026, 2, 4), date(2026, 2, 5), None],
        }
    )
    closures = [date(2026, 1, 27), date(2026, 1, 28)]
    got = case_outcomes(cases, as_of=AS_OF, closures=closures)
    assert got.get_column("disposed_21").to_list() == [1, 1, 0, None]
    assert got.get_column("default_60").to_list()[:3] == [1, 1, 1]

    filed_on_closure = cases.with_columns(pl.lit(date(2026, 1, 27)).alias("filed_date"))
    assert case_outcomes(filed_on_closure, as_of=AS_OF, closures=closures).is_empty()


def test_local_linear_recovers_a_planted_jump() -> None:
    rng = np.random.default_rng(1)
    r = np.arange(-30, 31, dtype=float)
    n = np.full(r.shape, 300.0)
    ybar = 0.3 + 0.002 * r + 0.1 * (r >= 0) + rng.normal(0, 0.01, r.shape)
    tau, se, left, right = estimate.local_linear(r, n, ybar, 30)
    assert abs(tau - 0.1) < 3 * se
    assert left > 0 and right > 0


def test_the_difference_nets_out_the_holiday_jump(oc: pl.DataFrame) -> None:
    t = estimate.estimates(oc, ["default_60"])
    raw = t.filter(pl.col("estimate") == "2026").row(0, named=True)
    diff = t.filter(pl.col("estimate") == "difference").row(0, named=True)
    assert abs(raw["tau"] - (HOLIDAY + LAW)) < 4 * raw["se"]
    assert abs(diff["tau"] - LAW) < 4 * diff["se"]
    assert diff["mde"] == pytest.approx(2.8 * diff["se"])
    assert diff["p_holm"] is not None


def test_holm() -> None:
    assert estimate.holm([0.01, 0.04, 0.03, 0.20]) == pytest.approx([0.04, 0.09, 0.09, 0.20])


def test_placebo_dates_center_on_zero_and_rank_the_law_change(oc: pl.DataFrame) -> None:
    dates = checks.clear_dates(date(2023, 7, 1), date(2025, 10, 31))
    assert all(not (d.month == 12 or (d.month == 1 and d.day < 16)) for d in dates)
    p = checks.placebo_dates(oc, "default_60", dates)
    assert abs(p.get_column("tau").drop_nans().mean()) < 0.01
    rank = checks.placebo_rank(p, checks.observed(oc, "default_60"))
    assert rank["share_as_large_tau"] < 0.05


def test_filing_volume_runs_on_every_cutoff() -> None:
    t = checks.filing_volume(make_cases(per_day=60))
    assert t.get_column("estimate").to_list() == ["2024", "2025", "2026", "difference"]
    assert t.filter(pl.col("estimate") == "difference").get_column("p").item() > 0.001


@pytest.mark.slow
def test_composition_shows_no_jump(oc: pl.DataFrame) -> None:
    t = checks.composition(oc, PRIMARY[:1])
    diff = t.filter(pl.col("estimate") == "difference")
    assert (diff.get_column("p") > 0.001).all()


@pytest.mark.slow
def test_event_study_finds_the_lasting_change(oc: pl.DataFrame) -> None:
    es = eventstudy.event_study(oc, "default_60")
    post = es.filter(pl.col("week").is_between(2, 15)).get_column("coef").mean()
    pre = es.filter(pl.col("week") < -3).get_column("coef").mean()
    assert abs(post - LAW) < 0.03
    assert abs(pre) < 0.02
    assert es.get_column("pre_period_p").item(0) > 0.001
