"""The results CSV: one to one with the scoping document, and never written into the repo."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from sb38 import cli, export, measures, schema, scope
from sb38.fixtures import synthetic_cases

AS_OF = date(2026, 8, 26)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_cases.csv"
RENTERS = pl.DataFrame(
    {
        "source": ["acs1", "acs1", "acs5"],
        "vintage": [2024, 2025, 2024],
        "estimate": [495_000.0, 501_000.0, 484_000.0],
        "moe90": [8_800.0, 10_900.0, 4_300.0],
    }
)


@pytest.fixture(scope="module")
def cases() -> pl.DataFrame:
    c, _ = schema.conform(synthetic_cases())
    return c


def test_every_measure_named_in_scope_exists(cases: pl.DataFrame) -> None:
    computed = set(measures.all_measures(cases, as_of=AS_OF).get_column("measure"))
    for _, stat in scope.computed():
        assert stat.measure == "filing_rate" or stat.measure in computed, stat.measure


def test_rows_follow_the_scoping_document(cases: pl.DataFrame) -> None:
    t = export.results(cases, as_of=AS_OF, county="Synthetic", renters=RENTERS)
    assert t.columns == export.COLUMNS
    order = [s.text for topic in scope.TOPICS for s in topic.stats]
    seen = list(dict.fromkeys(t.get_column("stat").to_list()))
    assert seen == order


def test_stats_without_a_measure_get_one_not_computed_row(cases: pl.DataFrame) -> None:
    t = export.results(cases, as_of=AS_OF, county="Synthetic", renters=RENTERS)
    for topic in scope.TOPICS:
        for stat in topic.stats:
            rows = t.filter(pl.col("stat") == stat.text)
            if stat.measure is None:
                assert rows.height == 1
                assert rows.get_column("status").item() == "not computed"
            else:
                assert (rows.get_column("status") == "computed").all()


def test_filing_rate_needs_denominators(cases: pl.DataFrame) -> None:
    without = export.results(cases, as_of=AS_OF, county="Synthetic")
    row = without.filter(pl.col("measure") == "filing_rate")
    assert row.get_column("status").to_list() == ["not computed"]

    with_r = export.results(cases, as_of=AS_OF, county="Synthetic", renters=RENTERS)
    rate = with_r.filter(pl.col("measure") == "filing_rate")
    assert set(rate.get_column("denominator_source")) == {"acs1", "acs5"}
    carried = rate.filter(
        (pl.col("denominator_source") == "acs1") & (pl.col("year") == 2026)
    ).get_column("carried_forward")
    assert carried.all()


def test_write_refuses_tracked_paths_in_the_repo(cases: pl.DataFrame, tmp_path: Path) -> None:
    t = export.results(cases, as_of=AS_OF, county="Synthetic")
    with pytest.raises(ValueError, match="inside the repository"):
        export.write(t, export.REPO_ROOT / "results.csv")
    assert export.write(t, tmp_path / "out" / "results.csv").exists()


def test_the_command_end_to_end(tmp_path: Path) -> None:
    out = tmp_path / "results.csv"
    rc = cli.main(
        [
            "measures",
            str(FIXTURE),
            "--as-of",
            "2026-08-26",
            "--county",
            "Synthetic",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    back = pl.read_csv(out)
    assert back.columns == export.COLUMNS
    assert set(back.get_column("county")) == {"Synthetic"}
