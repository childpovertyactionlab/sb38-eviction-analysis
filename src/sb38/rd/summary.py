"""The prototype's estimates as one aggregate table, and the workbook sheet laid out from it.

Draft prototype. The table holds estimates only, never case rows, so it can be written to disk
(``sb38 rd``) and laid out beside the results tables (``sb38 tables ... --rd PATH``). Shares are
stored as proportions and shown in percentage points; weekly filings are stored in log points
and shown as a percent change.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl

from sb38.rd.outcomes import PRIMARY, SECONDARY
from sb38.scope import NOT_COMPUTED
from sb38.tables import Block

# The filings the prototype needs: placebo dates start July 1, 2023 and each looks 30 days back;
# the 2025–26 season ends May 31, 2026. The last week is left as slack for a county whose courts
# file nothing in the final days of May.
FIRST_FILING, LAST_FILING = date(2023, 6, 1), date(2026, 5, 25)

COLUMNS = (
    "section",
    "outcome",
    "spec",
    "estimate",
    "ci_low",
    "ci_high",
    "p",
    "p_holm",
    "mde",
    "comparison_mean",
    "n",
)

OUTCOMES = {
    "default_60": "Default judgment within 60 days",
    "judgment_60": "Any judgment within 60 days",
    "dismissed_60": "Dismissal or nonsuit within 60 days",
    "disposed_21": "Any disposition within 21 days",
    "disposed_30": "Any disposition within 30 days",
    "disposed_60": "Any disposition within 60 days",
    "default_share_60": "Default share of dispositions within 60 days",
    "judgment_by_9": "Judgment within 9 days",
    "defendant_count": "Defendants per 100 cases",
    "log_weekly_filings": "Weekly residential filings",
}

TITLE = "Statistical comparison of cases filed before and after January 1, 2026"
STATUS = "Draft. These estimates have not yet had their quality check."
ABOUT = (
    "Change is the difference, in percentage points, between cases filed from January 1, 2026 "
    "and cases filed in the same weeks of the 2023–24 and 2024–25 seasons, from a model with "
    "week, season and court indicators. Standard errors are clustered by court and filing week.",
    "Each outcome is measured within a fixed number of days of filing, on every residential case "
    "filed. Cases filed on a court-closure day are left out.",
    "Holm-adjusted p-values cover the four primary outcomes, the first four rows. A change "
    "smaller than the smallest detectable change can't be told apart from no change.",
    "The analysis plan is docs/rdd/analysis-plan.md in the sb38-eviction-analysis repository.",
)
PRE_JANUARY = (
    "A joint test of the weeks before December 11. A small p means the season was already "
    "moving before the law applied.",
)
DISCONTINUITY = (
    "Compares cases filed just before and just after January 1, with December 20 to January 7 "
    "left out, against the same comparison at January 1, 2024 and 2025. Window is the days "
    "used on each side.",
    "This check can only detect very large jumps (see the smallest detectable change), so it "
    "is shown for completeness. A change beyond 100 points can't happen for a share; it shows "
    "how unstable this estimate is once the holiday weeks are left out. The 45-day version's "
    "large jumps shrink at 60 days and read as artifacts of estimating across the holiday gap.",
)
CASE_MIX = (
    "Predicted outcomes come from court and number of defendants alone. With court indicators "
    "they move only with the number of defendants and repeat its p-value; the version without "
    "court indicators is the one that would show a shift in court mix.",
)
PLACEBO_DATES = (
    "Share of non-holiday dates from July 2023 to October 2025 where the same comparison moved "
    "at least as much as it did at January 1, 2026.",
)


def _col(df: pl.DataFrame, name: str) -> pl.Expr:
    return pl.col(name) if name in df.columns else pl.lit(None, dtype=pl.Float64)


def _rows(df: pl.DataFrame, section: str, spec: str | None = None) -> pl.DataFrame:
    """One section of the summary from a frame of estimates (tau, se, CI and p)."""
    spec_expr = pl.col("spec") if "spec" in df.columns else pl.col("estimate").cast(pl.Utf8)
    return df.select(
        pl.lit(section).alias("section"),
        pl.col("outcome"),
        (pl.lit(spec) if spec is not None else spec_expr).alias("spec"),
        pl.col("tau").cast(pl.Float64).alias("estimate"),
        _col(df, "ci_low").cast(pl.Float64).alias("ci_low"),
        _col(df, "ci_high").cast(pl.Float64).alias("ci_high"),
        _col(df, "p").cast(pl.Float64).alias("p"),
        _col(df, "p_holm").cast(pl.Float64).alias("p_holm"),
        _col(df, "mde").cast(pl.Float64).alias("mde"),
        _col(df, "comparison_mean_after").cast(pl.Float64).alias("comparison_mean"),
        _col(df, "n_total").cast(pl.Int64).alias("n"),
    )


def summary(res: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Flatten the output of ``run.run`` into one table of estimates."""
    diff = pl.col("estimate") == "difference"
    pre = (
        res["event_study"]
        .group_by("outcome", maintain_order=True)
        .agg(pl.col("pre_period_p").first().alias("p"))
    )
    placebo_dates = res["rd_placebo_dates"].select(
        "outcome",
        pl.lit("share of placebo dates").alias("spec"),
        pl.col("share_as_large_difference").alias("tau"),
        pl.col("placebo_pairs").alias("n_total"),
    )
    parts = [
        _rows(res["estimates"], "main", "main"),
        _rows(res["placebo_season"], "placebo_season", "2024–25 against 2023–24"),
        _rows(res["sensitivity"], "sensitivity"),
        _rows(pre.with_columns(pl.lit(None, dtype=pl.Float64).alias("tau")), "pre_january", ""),
        _rows(res["filing_volume"], "filing_volume", "main"),
        _rows(res["rd_check"].filter(diff), "discontinuity", "30 days"),
        _rows(res["rd_sensitivity"].filter(diff), "discontinuity"),
        _rows(placebo_dates, "placebo_dates"),
    ]
    # Without defendant counts there is no case-mix check.
    if res["composition"].height:
        parts.append(_rows(res["composition"], "composition"))
    return pl.concat(parts, how="vertical_relaxed").select(COLUMNS)


def estimates(cases: pl.DataFrame, *, as_of: date, closures: Sequence[date] = ()) -> pl.DataFrame:
    """Run the prototype on a case table in the schema and return its summary table."""
    filed = cases.get_column("filed_date")
    first, last = filed.min(), filed.max()
    if first > FIRST_FILING or last < LAST_FILING:
        raise ValueError(
            f"the case table holds filings from {first} to {last}; the prototype needs filings "
            f"from {FIRST_FILING} or earlier through {LAST_FILING} or later"
        )
    from sb38.rd.run import run  # needs the rdd extra

    return summary(run(cases, as_of=as_of, closures=closures))


def write(table: pl.DataFrame, path: str | Path) -> Path:
    """Write the summary to CSV. Only a table with exactly the summary columns is written."""
    if tuple(table.columns) != COLUMNS:
        raise ValueError("only the prototype's summary table is written here")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.write_csv(path)
    return path


def _label(outcome: str) -> str:
    if outcome.startswith("predicted_"):
        return f"Predicted: {OUTCOMES[outcome.removeprefix('predicted_')].lower()}"
    return OUTCOMES.get(outcome, outcome)


def _shown(t: pl.DataFrame, cols: dict[str, str]) -> pl.DataFrame:
    """Rename and round for display: points to one decimal, p-values to three."""
    out = t.select(
        pl.col("outcome").map_elements(_label, return_dtype=pl.Utf8).alias("Outcome"),
        *[pl.col(c).alias(name) for c, name in cols.items()],
    )
    return out.with_columns(
        [
            pl.col(name).round(3 if name.startswith("p") else 1)
            for name in cols.values()
            if out.schema[name].is_float()
        ]
    )


def _points(t: pl.DataFrame) -> pl.DataFrame:
    """Shares as percentage points, and defendants per case as defendants per 100 cases."""
    return t.with_columns(
        [pl.col(c) * 100 for c in ("estimate", "ci_low", "ci_high", "mde", "comparison_mean")]
    )


CHANGE = {"estimate": "Change", "ci_low": "95% CI low", "ci_high": "95% CI high", "p": "p"}


def sheet(table: pl.DataFrame) -> tuple[str, list[Block]]:
    """The statistics sheet: the main estimate, then each check the analysis plan calls for."""
    section = {
        s: table.filter(pl.col("section") == s) for s in table.get_column("section").unique()
    }
    order = {o: i for i, o in enumerate(PRIMARY + SECONDARY)}
    main = _points(section["main"]).sort(pl.col("outcome").replace_strict(order, default=99))
    volume = section["filing_volume"].with_columns(
        [(pl.col(c).exp() - 1) * 100 for c in ("estimate", "ci_low", "ci_high")]
    )
    blocks = [
        Block(TITLE, lines=(STATUS, *ABOUT)),
        Block(
            "Main estimate (percentage points)",
            _shown(
                main,
                {
                    "comparison_mean": "Comparison mean (%)",
                    **CHANGE,
                    "p_holm": "p, Holm-adjusted",
                    "mde": "Smallest detectable change",
                },
            ),
        ),
        Block(
            "Placebo season: 2024–25 against 2023–24, when no law changed (percentage points)",
            _shown(_points(section["placebo_season"]), CHANGE),
        ),
        Block(
            "Before January: did 2025–26 already differ from earlier seasons?",
            _shown(section["pre_january"], {"p": "p"}),
            PRE_JANUARY,
        ),
        Block(
            "Sensitivity (percentage points)",
            _shown(_points(section["sensitivity"]), {"spec": "Version", **CHANGE}),
        ),
        Block(
            "Case mix (percentage points; defendants per 100 cases)",
            _shown(_points(section["composition"]), {"spec": "Version", **CHANGE}),
            CASE_MIX,
        )
        if "composition" in section
        else Block("Case mix (percentage points; defendants per 100 cases)", lines=(NOT_COMPUTED,)),
        Block("Weekly filings (% change)", _shown(volume, CHANGE)),
        Block(
            "Discontinuity at January 1, a check (percentage points)",
            _shown(
                _points(section["discontinuity"]),
                {
                    "spec": "Window",
                    **CHANGE,
                    "p_holm": "p, Holm-adjusted",
                    "mde": "Smallest detectable change",
                },
            ),
            DISCONTINUITY,
        ),
        Block(
            "Placebo dates for the discontinuity",
            _shown(
                section["placebo_dates"].with_columns(pl.col("estimate") * 100),
                {"estimate": "Share as large (%)", "n": "Placebo dates"},
            ),
            PLACEBO_DATES,
        ),
    ]
    return "Statistical comparison", blocks
