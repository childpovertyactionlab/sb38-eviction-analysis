"""Service, writ and appeal measures: court selection, the lag rule, and the layout."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sb38 import procedure, schema
from sb38.fixtures import synthetic_cases


def _case(i: int, court: str, year: int, **kw) -> dict:
    filed = date(year, 2, 1) + timedelta(days=i % 20)
    row = {
        "case_id": f"{court}-{year}-{i}",
        "court": court,
        "filed_date": filed,
        "is_residential": True,
        "disposition_class": "default_judgment",
        "disposition_date": filed + timedelta(days=15),
        "citation_served_date": filed + timedelta(days=5),
        "writ_issued_date": None,
        "writ_executed_date": None,
        "appeal_date": None,
    }
    return {**row, **kw}


def _table(rows: list[dict]) -> pl.DataFrame:
    df, _ = schema.conform(pl.DataFrame(rows, infer_schema_length=None))
    return df


def test_a_court_must_record_a_field_every_year_to_be_used() -> None:
    rows = []
    for year in (2024, 2025, 2026):
        rows += [_case(i, "steady", year) for i in range(10)]
        # "late" records service only from 2025 on
        rows += [
            _case(i, "late", year, citation_served_date=None if year == 2024 else date(year, 2, 9))
            for i in range(10)
        ]
    cov = procedure.coverage(_table(rows))
    assert cov.get_column("share").max() <= 1.0
    assert cov.filter(pl.col("court") == "steady").get_column("base").to_list()[:3] == [10, 10, 10]
    assert procedure.recording_courts(cov, "citation_served_date") == ["steady"]
    svc = procedure.service(_table(rows), cov)
    median = svc.filter(
        (pl.col("measure") == "median_days_to_service") & (pl.col("frame") == "jan_may")
    )
    assert median.get_column("value").to_list() == [5.0, 5.0, 5.0]
    assert "steady" in median.get_column("note").item(0) and "late" not in median.get_column(
        "note"
    ).item(0)


def test_writ_and_appeal_events_after_june_9_do_not_count() -> None:
    rows = []
    for year in (2024, 2025, 2026):
        for i in range(10):
            judged = date(year, 2, 20)
            rows.append(
                _case(
                    i,
                    "x",
                    year,
                    disposition_date=judged,
                    writ_issued_date=judged + timedelta(days=10),
                    # half executed before the cut, half after it
                    writ_executed_date=date(year, 3, 15) if i < 5 else date(year, 6, 20),
                    appeal_date=date(year, 3, 1)
                    if i < 2
                    else (date(year, 7, 1) if i < 4 else None),
                )
            )
    t = _table(rows)
    cov = procedure.coverage(t)
    assert procedure.recording_courts(cov, "writ_executed_date") == ["x"]
    w = procedure.writs(t, cov)
    share = w.filter((pl.col("measure") == "writ_executed_share") & (pl.col("frame") == "jan_may"))
    assert share.get_column("value").to_list() == [0.5, 0.5, 0.5]
    a = procedure.appeals(t, cov)
    count = a.filter((pl.col("measure") == "appealed_count") & (pl.col("frame") == "jan_may"))
    assert count.get_column("value").to_list() == [2.0, 2.0, 2.0]


def test_non_constable_share_is_over_cases_with_a_server_type() -> None:
    rows = [
        _case(
            i,
            "x",
            2026,
            citation_server_type=("other" if i == 0 else "constable" if i < 4 else None),
        )
        for i in range(10)
    ]
    t = procedure.non_constable_service(_table(rows))
    share = t.filter(
        (pl.col("measure") == "non_constable_service_share") & (pl.col("frame") == "jan_may")
    )
    assert share.get_column("value").item() == 0.25
    assert share.get_column("denominator").item() == 4


def test_everything_runs_on_the_fixture() -> None:
    cases, _ = schema.conform(synthetic_cases())
    m = procedure.all_measures(cases)
    assert {"mean_days_to_service", "writ_executed_share", "appealed_share"} <= set(
        m.get_column("measure")
    )
    assert procedure.cut_date(2025) == date(2025, 6, 9)
