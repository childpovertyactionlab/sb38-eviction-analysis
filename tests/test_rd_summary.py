"""The prototype's summary table and statistics sheet, checked on invented estimates."""

from __future__ import annotations

import math
import zipfile
from datetime import date

import polars as pl
import pytest

from sb38 import cli, export, schema, scope
from sb38.fixtures import synthetic_cases
from sb38.rd import summary
from sb38.rd.outcomes import PRIMARY, SECONDARY

FIXTURE = "fixtures/synthetic_cases.csv"

Z95 = 1.959963984540054


def _est(outcomes, label: str, tau: float = -0.015, se: float = 0.0087, **extra) -> pl.DataFrame:
    n = len(outcomes)
    p = math.erfc(abs(tau / se) / math.sqrt(2))
    return pl.DataFrame(
        {
            "outcome": list(outcomes),
            "estimate": [label] * n,
            "tau": [tau] * n,
            "se": [se] * n,
            "mde": [2.8 * se] * n,
            "n_total": [1000] * n,
            "ci_low": [tau - Z95 * se] * n,
            "ci_high": [tau + Z95 * se] * n,
            "p": [p] * n,
            **{k: [v] * n for k, v in extra.items()},
        }
    )


@pytest.fixture(scope="module")
def res() -> dict[str, pl.DataFrame]:
    main = _est(PRIMARY + SECONDARY, "main", comparison_mean_after=0.33).with_columns(
        pl.when(pl.col("outcome").is_in(PRIMARY)).then(0.3).otherwise(None).alias("p_holm")
    )
    labels = ("with court indicators", "without court indicators")
    composition = pl.concat(
        [_est(["defendant_count", *[f"predicted_{o}" for o in PRIMARY]], lab) for lab in labels]
    )
    rd_check = pl.concat(
        [_est(PRIMARY, "2026", tau=0.1), _est(PRIMARY, "difference", tau=-1.5, p_holm=0.15)],
        how="diagonal",
    )
    rd_sens = _est(PRIMARY, "difference", tau=0.4, p_holm=0.03).with_columns(
        pl.lit("45 days, Thanksgiving week dropped").alias("spec")
    )
    return {
        "estimates": main,
        "placebo_season": _est(PRIMARY, "placebo", tau=-0.017),
        "sensitivity": pl.concat([_est(PRIMARY, "2023–24 season dropped", tau=-0.007)]),
        "event_study": pl.DataFrame(
            {"outcome": list(PRIMARY), "week": [0] * 4, "pre_period_p": [0.11, 0.005, 0.02, 1.0]}
        ),
        "filing_volume": _est(["log_weekly_filings"], "main", tau=0.0453),
        "composition": composition,
        "rd_check": rd_check,
        "rd_sensitivity": rd_sens,
        "rd_placebo_dates": pl.DataFrame(
            {
                "outcome": list(PRIMARY),
                "placebo_pairs": [181] * 4,
                "share_as_large_difference": [0.4, 0.2, 0.3, 0.05],
            }
        ),
    }


def test_the_summary_holds_every_section(res) -> None:
    t = summary.summary(res)
    assert tuple(t.columns) == summary.COLUMNS
    counts = dict(t.group_by("section").len().iter_rows())
    assert counts == {
        "main": 8,
        "placebo_season": 4,
        "sensitivity": 4,
        "pre_january": 4,
        "filing_volume": 1,
        "composition": 10,
        "discontinuity": 8,
        "placebo_dates": 4,
    }


def test_the_sheet_shows_points_and_filings_as_a_percent_change(res) -> None:
    name, blocks = summary.sheet(summary.summary(res))
    assert name == "Statistical comparison"
    assert blocks[0].lines[0] == summary.STATUS
    main = next(b for b in blocks if b.title and b.title.startswith("Main estimate"))
    first = main.table.row(0, named=True)
    assert first["Outcome"] == summary.OUTCOMES[PRIMARY[0]]
    assert first["Change"] == -1.5 and first["Comparison mean (%)"] == 33.0
    assert main.table.get_column("p, Holm-adjusted").null_count() == len(SECONDARY)
    filings = next(b for b in blocks if b.title and b.title.startswith("Weekly filings"))
    assert filings.table.get_column("Change").item() == round((math.exp(0.0453) - 1) * 100, 1)
    mix = next(b for b in blocks if b.title and b.title.startswith("Case mix"))
    # Defendants per case are shown per 100 cases.
    assert mix.table.filter(pl.col("Outcome") == "Defendants per 100 cases")["Change"][0] == -1.5
    assert mix.lines == summary.CASE_MIX


def test_without_defendant_counts_the_case_mix_says_so(res) -> None:
    t = summary.summary({**res, "composition": pl.DataFrame()})
    assert "composition" not in t.get_column("section").to_list()
    _, blocks = summary.sheet(t)
    mix = next(b for b in blocks if b.title and b.title.startswith("Case mix"))
    assert mix.table is None and mix.lines == (scope.NOT_COMPUTED,)


def test_estimates_refuse_a_table_that_starts_too_late() -> None:
    late = pl.DataFrame({"filed_date": [date(2024, 1, 2), date(2026, 5, 29)]})
    with pytest.raises(ValueError, match="2023-06-01 or earlier"):
        summary.estimates(late, as_of=date(2026, 8, 26))


def test_cli_rd_writes_the_estimates(res, tmp_path, monkeypatch) -> None:
    seen = {}

    def fake(cases, *, as_of, closures):
        seen.update(rows=cases.height, as_of=as_of, closures=list(closures))
        return summary.summary(res)

    monkeypatch.setattr(summary, "estimates", fake)
    closures = tmp_path / "closures.csv"
    closures.write_text("date,event,confirmed\n2026-01-26,storm,no\n")
    out = tmp_path / "rd.csv"
    argv = ["rd", FIXTURE, "--as-of", "2026-08-26", "--closures", str(closures)]
    assert cli.main([*argv, "--out", str(out)]) == 0
    assert seen["as_of"] == date(2026, 8, 26) and seen["closures"] == [date(2026, 1, 26)]
    assert tuple(pl.read_csv(out).columns) == summary.COLUMNS


@pytest.mark.slow
def test_the_prototype_runs_on_any_case_table_with_or_without_defendants() -> None:
    pytest.importorskip("rdrobust")
    pytest.importorskip("statsmodels")
    from test_rd import make_cases

    cases = make_cases(per_day=20)
    full = summary.estimates(cases, as_of=date(2026, 8, 26))
    assert tuple(full.columns) == summary.COLUMNS
    assert "composition" in full.get_column("section").to_list()
    bare = summary.estimates(
        cases.with_columns(pl.lit(None, dtype=pl.Int64).alias("defendant_count")),
        as_of=date(2026, 8, 26),
    )
    assert "composition" not in bare.get_column("section").to_list()
    assert bare.filter(pl.col("section") == "main").height == len(PRIMARY + SECONDARY)


def test_write_refuses_any_other_table(tmp_path) -> None:
    with pytest.raises(ValueError):
        summary.write(pl.DataFrame({"case_id": ["A"]}), tmp_path / "x.csv")


def test_cli_adds_the_statistics_sheet(res, tmp_path) -> None:
    pytest.importorskip("xlsxwriter")
    cases, _ = schema.conform(synthetic_cases())
    export.write(
        export.results(cases, as_of=date(2026, 8, 26), county="Synthetic"),
        tmp_path / "synthetic.csv",
    )
    summary.write(summary.summary(res), tmp_path / "rd.csv")
    out = tmp_path / "synthetic.xlsx"
    argv = ["tables", str(tmp_path / "synthetic.csv"), "--rd", str(tmp_path / "rd.csv")]
    assert cli.main([*argv, "--out", str(out)]) == 0
    with zipfile.ZipFile(out) as z:
        assert "Statistical comparison" in z.read("xl/workbook.xml").decode()
