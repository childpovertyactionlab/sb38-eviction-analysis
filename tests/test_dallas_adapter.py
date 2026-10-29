"""The Dallas adapter's mapping, checked offline on invented rows.

The live pull is covered by an integration test that runs only where CPAL's Databricks access
is configured.
"""

from __future__ import annotations

import importlib.util
from datetime import date

import polars as pl
import pytest

from sb38 import measures, schema
from sb38.adapters import dallas


def _raw(rows: list[dict]) -> pl.DataFrame:
    base = {
        "case_number": None,
        "court": "JP 1-1",
        "date_filed": date(2026, 2, 2),
        "is_commercial": False,
        "orr_status": "MATCHED",
        "outcome_category": None,
        "judgment_type": None,
        "judgment_date": None,
        "defendant_count": 1,
        "outcome_reliability": "FULL",
        "orr_last_delivery_date": date(2026, 8, 26),
        "writ_issued_dt": None,
        "writ_served_dt": None,
        "appeal_date": None,
    }
    return pl.DataFrame(
        [{**base, **r} for r in rows],
        schema_overrides={
            "outcome_category": pl.Utf8,
            "judgment_type": pl.Utf8,
            "judgment_date": pl.Date,
            "is_commercial": pl.Boolean,
            "writ_issued_dt": pl.Date,
            "writ_served_dt": pl.Date,
            "appeal_date": pl.Date,
        },
    )


RAW = _raw(
    [
        {
            "case_number": "A",
            "outcome_category": "DEFAULT_FOR_LANDLORD",
            "judgment_type": "DEFAULT JUDGMENT",
            "judgment_date": date(2026, 2, 20),
        },
        {
            "case_number": "B",
            "outcome_category": "DISMISSED_WANT_OF_PROSECUTION",
            "judgment_type": "DEFAULT JUDGMENT",
            "judgment_date": date(2026, 2, 21),
        },
        {
            "case_number": "C",
            "outcome_category": "NONSUIT",
            "judgment_type": "NONSUIT/DISMISSAL BY PLAINTIFF/PETITIONER",
            "judgment_date": date(2026, 3, 1),
        },
        {"case_number": "D", "outcome_category": "NO_DISPOSITION", "judgment_type": None},
        {"case_number": "E", "orr_status": "NO_ORR_RECORD", "is_commercial": None},
        {
            "case_number": "F",
            "is_commercial": True,
            "outcome_category": "DEFAULT_FOR_LANDLORD",
            "judgment_type": "DEFAULT JUDGMENT",
            "judgment_date": date(2026, 2, 25),
        },
    ]
)


def _class(cases: pl.DataFrame, cid: str):
    return cases.filter(pl.col("case_id") == cid).get_column("disposition_class").item()


def test_the_map_is_well_formed() -> None:
    m = dallas.load_map()
    assert m.height == 28
    assert set(m.get_column("disposition_class").drop_nulls()) <= set(schema.DISPOSITION_CLASSES)


def test_mapping_rules() -> None:
    cases = dallas.to_cases(RAW)
    assert schema.validate(cases).ok
    assert _class(cases, "A") == "default_judgment"
    # a default recorded for the tenant is a dismissal for want of prosecution
    assert _class(cases, "B") == "dismissed"
    assert _class(cases, "C") == "nonsuit"
    # an outcome record with no disposition, and a filing with no record, are both pending
    assert _class(cases, "D") is None
    assert _class(cases, "E") is None
    pending = cases.filter(pl.col("case_id").is_in(["D", "E"]))
    assert pending.get_column("disposition_date").is_null().all()


def test_commercial_cases_are_flagged_and_dropped_from_cohorts() -> None:
    cases = dallas.to_cases(RAW)
    flags = dict(zip(cases.get_column("case_id"), cases.get_column("is_residential"), strict=True))
    assert flags["F"] is False
    assert flags["E"] is True  # no record, so no commercial flag: the filings feed is residential
    assert "F" not in measures.cohorts(cases).get_column("case_id").to_list()


def test_an_unmapped_outcome_pair_stops_the_run() -> None:
    raw = _raw(
        [
            {
                "case_number": "Z",
                "outcome_category": "SOMETHING_NEW",
                "judgment_type": "NEW JUDGMENT TYPE",
                "judgment_date": date(2026, 3, 3),
            }
        ]
    )
    with pytest.raises(ValueError, match="SOMETHING_NEW, NEW JUDGMENT TYPE"):
        dallas.to_cases(raw)


@pytest.mark.integration
@pytest.mark.skipif(
    importlib.util.find_spec("analyst_pier") is None, reason="needs CPAL Databricks access"
)
def test_live_pull_matches_the_reconciled_cohorts() -> None:
    """Checked against the county delivery files on 2026-10-27. Filings move a little as the
    daily feed is corrected, so counts get a small tolerance; a bigger move needs explaining."""
    pull = dallas.load(date(2024, 1, 1), date(2026, 5, 31))
    f = measures.filings(pull.cases).filter(pl.col("frame") == "jan_may")
    got = dict(zip(f.get_column("year"), f.get_column("value"), strict=True))
    for year, expected in {2024: 20_184, 2025: 19_317, 2026: 19_024}.items():
        assert abs(got[year] - expected) / expected < 0.005, (year, got[year])
