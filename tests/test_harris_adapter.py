"""The Harris adapter's mapping, checked offline on invented rows."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sb38 import schema
from sb38.adapters import harris

COLS = [
    "case_number",
    "court",
    "filed_date",
    "nature_of_claim",
    "disposition",
    "judgment_titles",
    "disposition_date",
    "judgment_date",
    "first_writ_issued",
    "first_appeal_any",
    "page_as_of",
]


def _raw(rows: list[dict]) -> pl.DataFrame:
    base = dict.fromkeys(COLS, "")
    base.update(court="Precinct 1, Place 1", filed_date="2026-02-02", nature_of_claim="Eviction")
    return pl.DataFrame([{**base, **r} for r in rows], schema=dict.fromkeys(COLS, pl.Utf8))


RAW = _raw(
    [
        {
            "case_number": "A",
            "disposition": "Default Judgment (OCA)",
            "disposition_date": "2026-02-20",
            "first_writ_issued": "2026-03-02",
        },
        {
            "case_number": "B",
            "disposition": "Appeal Filed",
            "judgment_titles": "Default Judgment",
            "disposition_date": "2026-02-18",
            "first_appeal_any": "2026-02-24",
        },
        {
            "case_number": "C",
            "disposition": "Appeal Filed",
            "judgment_titles": "Final Judgment",
            "disposition_date": "2026-02-18",
        },
        {"case_number": "D", "disposition": "Appeal Filed", "disposition_date": "2026-02-18"},
        {
            "case_number": "E",
            "disposition": "Non-suited or Dismissed by Plaintiff (OCA)",
            # A judgment title on a non-appeal row is ignored.
            "judgment_titles": "Default Judgment",
            "disposition_date": "2026-02-10",
        },
        {"case_number": "F", "nature_of_claim": "Nonpayment - Commercial"},
    ]
)


def test_classes_and_dates():
    cases = harris.to_cases(RAW).sort("case_id")
    assert schema.validate(cases).ok
    got = dict(zip(cases["case_id"], cases["disposition_class"], strict=True))
    assert got == {
        "A": "default_judgment",
        "B": "default_judgment",
        "C": "other_judgment",
        "D": "other",
        "E": "nonsuit",
        "F": None,
    }
    a = cases.filter(pl.col("case_id") == "A").row(0, named=True)
    assert a["disposition_date"] == date(2026, 2, 20)
    assert a["writ_issued_date"] == date(2026, 3, 2)
    assert a["writ_executed_date"] is None
    b = cases.filter(pl.col("case_id") == "B").row(0, named=True)
    assert b["appeal_date"] == date(2026, 2, 24)
    f = cases.filter(pl.col("case_id") == "F").row(0, named=True)
    assert f["disposition_date"] is None


def test_every_case_is_kept_as_residential():
    # Harris records the residential type only from 2026, so every year keeps every case.
    assert harris.to_cases(RAW)["is_residential"].all()


def test_unmapped_disposition_stops_the_run():
    bad = _raw(
        [{"case_number": "Z", "disposition": "Something New", "disposition_date": "2026-03-01"}]
    )
    with pytest.raises(ValueError, match="Something New"):
        harris.to_cases(bad)


def test_map_is_valid():
    m = harris.load_map()
    assert m.height > 0
    assert set(m["disposition_class"].drop_nulls()) <= set(schema.DISPOSITION_CLASSES)
