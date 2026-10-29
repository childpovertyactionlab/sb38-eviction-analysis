"""Settled measures, checked on tables small enough to verify by hand."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sb38 import measures, schema
from sb38.fixtures import synthetic_cases

AS_OF = date(2026, 8, 26)


def _cases(rows: list[tuple]) -> pl.DataFrame:
    """rows: (case_id, filed, class, disposed, residential)."""
    return pl.DataFrame(
        [
            {
                "case_id": cid,
                "court": "Court A",
                "filed_date": filed,
                "is_residential": res,
                "disposition_class": cls,
                "disposition_date": disposed,
            }
            for cid, filed, cls, disposed, res in rows
        ],
        schema_overrides={"disposition_class": pl.Utf8, "disposition_date": pl.Date},
    )


TABLE = _cases(
    [
        ("a", date(2026, 1, 5), "default_judgment", date(2026, 1, 20), True),  # 15 days
        ("b", date(2026, 1, 6), "nonsuit", date(2026, 2, 20), True),  # 45 days
        ("c", date(2026, 2, 2), "other_judgment", date(2026, 2, 27), True),  # 25 days
        ("d", date(2026, 2, 3), None, None, True),  # pending
        ("e", date(2026, 3, 2), "default_judgment", date(2026, 2, 1), True),  # before filing
        ("f", date(2026, 1, 7), "default_judgment", date(2026, 1, 20), False),  # commercial
        ("g", date(2026, 6, 1), "default_judgment", date(2026, 6, 20), True),  # June: out
        ("h", date(2025, 1, 6), "default_judgment", date(2025, 1, 21), True),
        ("i", date(2025, 1, 7), "dismissed", date(2025, 3, 20), True),  # 72 days
    ]
)


def _get(df: pl.DataFrame, measure: str, year: int, frame: str = "jan_may", month=None):
    q = df.filter(
        (pl.col("measure") == measure) & (pl.col("year") == year) & (pl.col("frame") == frame)
    )
    if month is not None:
        q = q.filter(pl.col("month") == month)
    assert q.height == 1, (measure, year, frame, month, q)
    return q.row(0, named=True)


def test_cohorts_keep_residential_january_to_may_only() -> None:
    ids = set(measures.cohorts(TABLE).get_column("case_id").to_list())
    assert ids == {"a", "b", "c", "d", "e", "h", "i"}


def test_filings_by_cohort_and_month() -> None:
    f = measures.filings(TABLE)
    assert _get(f, "filings", 2026)["value"] == 5
    assert _get(f, "filings", 2026, "month", 1)["value"] == 2
    assert _get(f, "filings", 2026, "month", 2)["value"] == 2
    assert _get(f, "filings", 2025)["value"] == 2


def test_default_rate_uses_cases_with_an_outcome() -> None:
    d = measures.default_rate(TABLE)
    row = _get(d, "default_rate", 2026)
    # a, b, c, e have outcomes (d is pending); a and e are defaults
    assert (row["numerator"], row["denominator"]) == (2, 4)
    assert row["value"] == pytest.approx(0.5)
    of_filed = _get(d, "default_share_of_filed", 2026)
    assert (of_filed["numerator"], of_filed["denominator"]) == (2, 5)


def test_case_length_excludes_dispositions_before_filing_and_says_so() -> None:
    c = measures.case_length(TABLE, as_of=AS_OF)
    med = _get(c, "median_days_any_disposition", 2026)
    assert med["denominator"] == 3  # a, b, c; e excluded, d pending
    assert med["value"] == 25
    assert "dated before filing" in med["note"]
    # the bad case was filed in 2026, so the 2025 row carries no note
    assert _get(c, "median_days_any_disposition", 2025)["note"] == ""
    judged = _get(c, "median_days_judgment_only", 2026)
    assert judged["denominator"] == 2  # a and c
    assert judged["value"] == 20


def test_resolved_within_counts_pending_as_unresolved() -> None:
    c = measures.case_length(TABLE, as_of=AS_OF)
    r30 = _get(c, "resolved_within_30_days", 2026)
    # filed in cohort, after dropping e: a, b, c, d. Resolved by day 30: a (15), c (25).
    assert (r30["numerator"], r30["denominator"]) == (2, 4)
    r60 = _get(c, "resolved_within_60_days", 2026)
    assert r60["numerator"] == 3  # a, b, c


def test_resolved_within_is_withheld_when_the_data_are_too_early() -> None:
    """A 60-day share for May filings needs data pulled after July 30."""
    early = measures.case_length(TABLE, as_of=date(2026, 7, 1))
    assert early.filter(
        (pl.col("measure") == "resolved_within_60_days")
        & (pl.col("year") == 2026)
        & (pl.col("frame") == "jan_may")
    ).is_empty()
    # January 2026 had its 60 days by July 1, so the month-level figure survives.
    assert not early.filter(
        (pl.col("measure") == "resolved_within_60_days")
        & (pl.col("year") == 2026)
        & (pl.col("frame") == "month")
        & (pl.col("month") == 1)
    ).is_empty()


def test_months_add_up_to_the_cohort() -> None:
    cases, _ = schema.conform(synthetic_cases())
    f = measures.filings(cases)
    for year in measures.COHORT_YEARS:
        total = _get(f, "filings", year)["value"]
        months = f.filter((pl.col("frame") == "month") & (pl.col("year") == year)).get_column(
            "value"
        )
        assert months.sum() == total


def test_compare_sets_2026_beside_each_benchmark() -> None:
    cases, _ = schema.conform(synthetic_cases())
    cmp = measures.compare(measures.all_measures(cases, as_of=AS_OF))
    row = cmp.filter((pl.col("measure") == "filings") & (pl.col("frame") == "jan_may")).row(
        0, named=True
    )
    assert row["change_vs_2025"] == pytest.approx(row["2026"] - row["2025"])
    assert row["pct_change_vs_2024"] == pytest.approx(row["2026"] / row["2024"] - 1)
