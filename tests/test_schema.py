"""The case schema: what it accepts, what it refuses, and what it only warns about."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from sb38 import schema
from sb38.fixtures import synthetic_cases

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_cases.csv"


def _case(**over):
    row = {
        "case_id": "X1",
        "court": "Court A",
        "filed_date": date(2026, 1, 5),
        "is_residential": True,
        "disposition_class": "default_judgment",
        "disposition_date": date(2026, 1, 20),
    }
    row.update(over)
    return row


def test_the_synthetic_fixtures_pass_cleanly() -> None:
    report = schema.validate(synthetic_cases())
    assert report.ok, report.problems
    assert report.coverage is not None
    assert set(report.coverage.get_column("filed_year").to_list()) == {2023, 2024, 2025, 2026}


def test_the_committed_fixture_file_matches_the_generator() -> None:
    """The CSV in the repo is regenerated, never hand-edited."""
    committed = schema.read_cases(FIXTURE)
    fresh, _ = schema.conform(synthetic_cases())
    assert committed.equals(fresh.select(committed.columns))


def test_a_missing_required_column_is_an_error() -> None:
    df = pl.DataFrame([_case()]).drop("disposition_date")
    report = schema.validate(df)
    assert not report.ok
    assert "disposition_date" in report.errors()[0].message


def test_pending_cases_are_allowed() -> None:
    """A case with no disposition yet is data, not an error."""
    df = pl.DataFrame([_case(), _case(case_id="X2", disposition_class=None, disposition_date=None)])
    assert schema.validate(df).ok


def test_duplicate_cases_are_an_error() -> None:
    report = schema.validate(pl.DataFrame([_case(), _case()]))
    assert any(p.check == "grain" for p in report.errors())


def test_an_unknown_disposition_class_is_an_error() -> None:
    report = schema.validate(pl.DataFrame([_case(disposition_class="DEFAULT JUDGMENT")]))
    assert any(p.check == "disposition" for p in report.errors())


def test_unreadable_dates_are_counted_not_dropped() -> None:
    df = pl.DataFrame(
        [
            {
                k: (str(v) if isinstance(v, date) else v)
                for k, v in _case(filed_date="01/05/2026").items()
            }
        ]
    )
    report = schema.validate(df)
    assert any(p.check == "type" and p.rows == 1 for p in report.errors())


def test_out_of_order_dates_warn_but_do_not_stop() -> None:
    report = schema.validate(pl.DataFrame([_case(disposition_date=date(2025, 12, 1))]))
    assert report.ok
    assert any(p.check == "order" for p in report.problems)


def test_read_cases_refuses_a_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    pl.DataFrame([_case()]).drop("court").write_csv(bad)
    with pytest.raises(ValueError, match="court"):
        schema.read_cases(bad)


def test_text_booleans_and_dates_are_cast() -> None:
    df = pl.DataFrame([{k: str(v) for k, v in _case().items()}])
    out, problems = schema.conform(df)
    assert not problems
    assert out.schema["filed_date"] == pl.Date
    assert out.get_column("is_residential").to_list() == [True]


def test_every_schema_field_is_documented() -> None:
    """docs/schema.md is hand-written, so this keeps it from drifting from the code."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "schema.md").read_text()
    missing = [f.name for f in schema.FIELDS if f"`{f.name}`" not in doc]
    assert not missing, f"undocumented in docs/schema.md: {missing}"
    for cls in schema.DISPOSITION_CLASSES:
        assert f"`{cls}`" in doc
