"""Report tables: laid out from the results CSV, one sheet per topic, nothing recomputed."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sb38 import cli, export, schema, scope, tables
from sb38.fixtures import synthetic_cases

AS_OF = date(2026, 8, 26)
RENTERS = pl.DataFrame(
    {
        "source": ["acs1", "acs1", "acs5"],
        "vintage": [2024, 2025, 2024],
        "estimate": [495_000.0, 501_000.0, 484_000.0],
        "moe90": [8_800.0, 10_900.0, 4_300.0],
    }
)


@pytest.fixture(scope="module")
def csvs(tmp_path_factory) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The results and coverage tables as a reader gets them: written to CSV and read back."""
    cases, _ = schema.conform(synthetic_cases())
    d = tmp_path_factory.mktemp("results")
    export.write(
        export.results(cases, as_of=AS_OF, county="Synthetic", renters=RENTERS),
        d / "synthetic.csv",
    )
    export.write(
        export.coverage(cases, as_of=AS_OF, county="Synthetic"), d / "synthetic-coverage.csv"
    )
    return pl.read_csv(d / "synthetic.csv"), pl.read_csv(d / "synthetic-coverage.csv")


def test_one_sheet_per_topic_in_order(csvs) -> None:
    results, coverage = csvs
    names = [name for name, _ in tables.sheets(results, coverage)]
    assert names == ["About"] + [tables.sheet_name(t) for t in scope.TOPICS]
    assert all(len(n) <= 31 and "“" not in n and "”" not in n for n in names)


def test_every_stat_appears_and_gaps_say_so(csvs) -> None:
    results, coverage = csvs
    blocks = [b for _, bs in tables.sheets(results, coverage) for b in bs]
    titles = " ".join(b.title or "" for b in blocks)
    for topic in scope.TOPICS:
        for stat in topic.stats:
            assert stat.text in titles, stat.text
    empty = [b for b in blocks if b.lines == (scope.NOT_COMPUTED,)]
    assert {b.title for b in empty} == {
        s.text for t in scope.TOPICS for s in t.stats if s.measure is None
    }


def test_tables_repeat_the_results_without_recomputing(csvs) -> None:
    results, coverage = csvs
    sheet = dict(tables.sheets(results, coverage))["Default rate"]
    jan_may = sheet[0].table.filter(pl.col("period") == "Jan–May")
    expected = results.filter(
        (pl.col("measure") == "default_rate") & (pl.col("frame") == "jan_may")
    ).sort("year")
    for year, value in expected.select("year", "value").iter_rows():
        assert jan_may.get_column(str(year)).item() == pytest.approx(value * 100, abs=0.05)


def test_filing_rate_shows_the_one_year_estimate_only(csvs) -> None:
    results, coverage = csvs
    sheet = dict(tables.sheets(results, coverage))["Filing trends"]
    rate = next(b for b in sheet if b.title and b.title.startswith("filing rate"))
    acs1 = results.filter(
        (pl.col("measure") == "filing_rate")
        & (pl.col("frame") == "jan_may")
        & (pl.col("denominator_source") == "acs1")
    )
    jan_may = rate.table.filter(pl.col("period") == "Jan–May")
    for year, value in acs1.select("year", "value").iter_rows():
        assert jan_may.get_column(str(year)).item() == pytest.approx(value, abs=0.05)


def test_filing_rate_names_a_carried_forward_estimate(csvs) -> None:
    results, coverage = csvs
    sheet = dict(tables.sheets(results, coverage))["Filing trends"]
    rate = next(b for b in sheet if b.title and b.title.startswith("filing rate"))
    # The fixture's renters stop at 2025, so 2026 reuses the 2025 estimate.
    assert rate.lines[0] == "ACS 1-year (estimate carried forward for 2026)"


def test_filing_rate_without_a_one_year_estimate_says_so(csvs) -> None:
    results, coverage = csvs
    acs5_only = results.filter(
        (pl.col("measure") != "filing_rate") | (pl.col("denominator_source") == "acs5")
    )
    sheet = dict(tables.sheets(acs5_only, coverage))["Filing trends"]
    rate = next(b for b in sheet if b.title and b.title.startswith("filing rate"))
    assert rate.table is None and rate.lines == (scope.NOT_COMPUTED,)


def test_a_count_has_no_counts_behind_it(csvs) -> None:
    results, coverage = csvs
    sheet = dict(tables.sheets(results, coverage))["Filing trends"]
    titles = [b.title for b in sheet]
    assert titles[0] == "Number of filings"
    assert titles[1] != "Jan–May counts behind the figure"


def test_procedure_topics_carry_their_coverage(csvs) -> None:
    results, coverage = csvs
    sheets = dict(tables.sheets(results, coverage))
    for topic, field in tables.COVERAGE_FIELDS.items():
        assert not coverage.filter(pl.col("field") == field).is_empty(), field
        assert sheets[topic][-1].title == "Share recorded by court and year (%)"


def test_cli_writes_the_workbook(csvs, tmp_path) -> None:
    pytest.importorskip("xlsxwriter")
    results, coverage = csvs
    results.write_csv(tmp_path / "synthetic.csv")
    coverage.write_csv(tmp_path / "synthetic-coverage.csv")
    out = tmp_path / "synthetic.xlsx"
    assert cli.main(["tables", str(tmp_path / "synthetic.csv"), "--out", str(out)]) == 0
    assert out.stat().st_size > 0


def test_cli_stops_on_a_coverage_file_that_is_not_there(csvs, tmp_path) -> None:
    results, _ = csvs
    results.write_csv(tmp_path / "synthetic.csv")
    with pytest.raises(FileNotFoundError):
        cli.main(
            [
                "tables",
                str(tmp_path / "synthetic.csv"),
                "--coverage",
                str(tmp_path / "missing.csv"),
                "--out",
                str(tmp_path / "x.xlsx"),
            ]
        )


def test_an_undefined_share_is_left_blank(csvs, tmp_path) -> None:
    pytest.importorskip("xlsxwriter")
    results, coverage = csvs
    nan_first = results.with_columns(
        pl.when((pl.col("measure") == "default_rate") & (pl.col("frame") == "month"))
        .then(float("nan"))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    assert tables.write(nan_first, tmp_path / "nan.xlsx", coverage).stat().st_size > 0
