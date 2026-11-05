"""The results as report tables: one sheet per scoping-document topic, years across.

Pure layout over a results table (``export.results``) and its coverage table, so a county's
tables can be rebuilt from its committed CSVs. Nothing here computes a new figure. ``sheets``
needs only polars; ``write`` needs the optional ``tables`` extra (xlsxwriter).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from sb38 import report, scope

# The coverage table shown under each procedure topic.
COVERAGE_FIELDS = {
    "Service of eviction notice": "citation_served_date",
    "Writ service": "writ_executed_date",
    "Appeals": "appeal_date",
}

ABOUT = (
    "Cohorts are cases filed January 1 to May 31 of each year. Monthly rows split each cohort "
    "by filing month.",
    "Writ and appeal events count only if dated on or before June 9 of the cohort year.",
    '"pts vs 2024" is 2026 minus 2024 in percentage points. "% vs 2024" is the percent '
    "change from 2024. The same goes for 2025.",
    "Under service, writs and appeals, the coverage table gives the share of each court's "
    'cases with the step recorded. "used" marks the courts in the figures above it.',
    "Definitions for every figure are in docs/methods.md in the sb38-eviction-analysis repository.",
)


@dataclass(frozen=True)
class Block:
    """A heading, then a table, then lines of text. Any part may be empty."""

    title: str | None = None
    table: pl.DataFrame | None = None
    lines: tuple[str, ...] = ()


def _units(stat: scope.Stat) -> str:
    if stat.percent:
        return " (%)"
    if stat.measure and "days" in stat.measure:
        return " (days)"
    return ""


def _counts_behind(rows: pl.DataFrame, measure: str) -> pl.DataFrame | None:
    """Jan–May numerator and denominator, or for a days figure the cases it is taken over.

    None for a count, which has no denominator and whose figure already is the count.
    """
    d = report.denominators_used(rows, measure).select("year", "numerator", "denominator")
    if d.get_column("numerator").null_count() == d.height:
        return d.select("year", pl.col("denominator").alias("cases"))
    if d.get_column("denominator").null_count() == d.height:
        return None
    return d


def _stat_blocks(results: pl.DataFrame, stat: scope.Stat) -> list[Block]:
    rows = results.filter((pl.col("stat") == stat.text) & (pl.col("status") == "computed"))
    if stat.measure == "filing_rate":
        # The year-matched ACS 1-year estimate is the figure; the 5-year check stays in the
        # notebook.
        rows = rows.filter(pl.col("denominator_source") == "acs1")
    if rows.is_empty():
        return [Block(stat.text, lines=(scope.NOT_COMPUTED,))]
    notes = tuple(report.note_parts(rows, stat.measure))
    if stat.measure == "filing_rate":
        return [
            Block(
                f"{stat.text} (per 1,000 renter households, ACS 1-year)",
                report.table(rows, stat.measure),
                (report.source_label(rows), *notes),
            )
        ]
    figure = report.table(rows, stat.measure, percent=stat.percent)
    counts = _counts_behind(rows, stat.measure)
    blocks = [Block(stat.text + _units(stat), figure, () if counts is not None else notes)]
    if stat.count:
        blocks.append(Block("Count", report.table(rows, stat.count)))
    if counts is not None:
        blocks.append(Block("Jan–May counts behind the figure", counts, notes))
    return blocks


def sheet_name(topic: scope.Topic) -> str:
    """The topic's name as an Excel sheet name: no quote marks, at most 31 characters."""
    return topic.name.strip('“”"').strip()[:31]


def about(results: pl.DataFrame) -> list[Block]:
    """What the workbook holds and where its figures came from."""
    first = results.row(0, named=True)
    source = (
        f"{first['county']} County. sb38-eviction-analysis {first['sb38_version']}. "
        f"Outcome data through {first['data_as_of']}; cases read {first['pulled_on']}."
    )
    return [Block("SB 38 eviction analysis: tables", lines=(source, *ABOUT))]


def sheets(
    results: pl.DataFrame, coverage: pl.DataFrame | None = None
) -> list[tuple[str, list[Block]]]:
    """One sheet per scoping-document topic, in the document's order, after an "About" sheet."""
    out = [("About", about(results))]
    for topic in scope.TOPICS:
        blocks = [b for stat in topic.stats for b in _stat_blocks(results, stat)]
        field = COVERAGE_FIELDS.get(topic.name)
        if (
            field
            and coverage is not None
            and not coverage.filter(pl.col("field") == field).is_empty()
        ):
            blocks.append(
                Block("Share recorded by court and year (%)", report.coverage(coverage, field))
            )
        out.append((sheet_name(topic), blocks))
    return out


def write(results: pl.DataFrame, path: str | Path, coverage: pl.DataFrame | None = None) -> Path:
    """Write the tables to an Excel workbook at ``path``."""
    try:
        import xlsxwriter
    except ImportError as exc:  # pragma: no cover - optional extra
        raise ImportError(
            "Writing the workbook needs the tables extra: uv sync --extra tables"
        ) from exc

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path))
    bold = wb.add_format({"bold": True})
    head = wb.add_format({"bold": True, "bottom": 1})
    for name, blocks in sheets(results, coverage):
        ws = wb.add_worksheet(name)
        ws.set_column(0, 0, 24)
        ws.set_column(1, 8, 12)
        r = 0
        for b in blocks:
            if b.title:
                ws.write(r, 0, b.title, bold)
                r += 1
            if b.table is not None:
                for c, col in enumerate(b.table.columns):
                    ws.write(r, c, col, head)
                r += 1
                for row in b.table.iter_rows():
                    for c, v in enumerate(row):
                        # Missing values and 0/0 shares are left blank.
                        if v is not None and not (isinstance(v, float) and not math.isfinite(v)):
                            ws.write(r, c, v)
                    r += 1
            for line in b.lines:
                ws.write(r, 0, line)
                r += 1
            r += 1
    wb.close()
    return path
