"""The case table every calculation in this package reads.

One row per eviction case, in a shape that doesn't depend on any county's records system. Each
data source gets a small adapter that produces this table. The analysis never knows whether the
rows came from a warehouse, a CSV export or a scrape, and that is what lets two counties run the
same method on differently stored data.

The table has to hold every case filed in the window, including cases with no disposition yet.
Pending cases are what make "share resolved within 60 days" honest, so dropping them
would bias every timing figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

# How a case ended. Counties record outcomes in their own vocabulary, so each adapter maps its
# raw labels onto these. A null class means the case has no recorded disposition yet.
DISPOSITION_CLASSES = ("default_judgment", "other_judgment", "dismissed", "nonsuit", "other")

JUDGMENT_ENTERED = ("default_judgment", "other_judgment")
DISMISSED_OR_WITHDRAWN = ("dismissed", "nonsuit")


@dataclass(frozen=True)
class Field:
    name: str
    dtype: type[pl.DataType]
    required: bool  # the column must be present
    nullable: bool  # individual values may be empty
    description: str


FIELDS: tuple[Field, ...] = (
    Field("case_id", pl.Utf8, True, False, "The county's case identifier. Unique per row."),
    Field("court", pl.Utf8, True, False, "The court the case was filed in."),
    Field("filed_date", pl.Date, True, False, "Date the petition was filed."),
    Field(
        "is_residential",
        pl.Boolean,
        True,
        False,
        "True for residential evictions. Commercial cases are excluded from every figure.",
    ),
    Field(
        "disposition_class",
        pl.Utf8,
        True,
        True,
        "How the case ended: one of " + ", ".join(DISPOSITION_CLASSES) + ". Null while pending.",
    ),
    Field(
        "disposition_date",
        pl.Date,
        True,
        True,
        "Date of the judgment, dismissal or nonsuit that ended the case. Null while pending.",
    ),
    Field("county", pl.Utf8, False, True, "County name."),
    Field("judgment_type_raw", pl.Utf8, False, True, "The county's own outcome label, unmapped."),
    Field("defendant_count", pl.Int64, False, True, "Number of named defendants."),
    Field("citation_served_date", pl.Date, False, True, "Earliest service of the citation."),
    Field("writ_issued_date", pl.Date, False, True, "Date a writ of possession was issued."),
    Field("writ_executed_date", pl.Date, False, True, "Date the writ was executed."),
    Field("appeal_date", pl.Date, False, True, "Date an appeal was recorded."),
)

BY_NAME = {f.name: f for f in FIELDS}
REQUIRED = tuple(f.name for f in FIELDS if f.required)
DATE_FIELDS = tuple(f.name for f in FIELDS if f.dtype is pl.Date)


@dataclass(frozen=True)
class Problem:
    level: str  # "error" stops the analysis; "warning" is reported and carried
    check: str
    message: str
    rows: int = 0


@dataclass
class Report:
    rows: int
    problems: list[Problem] = field(default_factory=list)
    coverage: pl.DataFrame | None = None

    @property
    def ok(self) -> bool:
        return not any(p.level == "error" for p in self.problems)

    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]


def conform(df: pl.DataFrame) -> tuple[pl.DataFrame, list[Problem]]:
    """Cast known columns to the schema's types, keeping unknown columns untouched.

    Dates may arrive as real dates or as ``YYYY-MM-DD`` text. A value that fails to parse
    becomes null and is counted as a problem, never silently dropped.
    """
    problems: list[Problem] = []
    exprs = []
    for name in df.columns:
        spec = BY_NAME.get(name)
        if spec is None:
            exprs.append(pl.col(name))
            continue
        col = df.get_column(name)
        if spec.dtype is pl.Date and col.dtype == pl.Utf8:
            exprs.append(pl.col(name).str.strip_chars().str.to_date("%Y-%m-%d", strict=False))
        elif spec.dtype is pl.Boolean and col.dtype == pl.Utf8:
            lowered = pl.col(name).str.strip_chars().str.to_lowercase()
            exprs.append(
                pl.when(lowered.is_in(["true", "t", "1", "yes", "y"]))
                .then(True)
                .when(lowered.is_in(["false", "f", "0", "no", "n"]))
                .then(False)
                .otherwise(None)
                .alias(name)
            )
        else:
            exprs.append(pl.col(name).cast(spec.dtype, strict=False))
    out = df.select(exprs)

    for name in df.columns:
        if name not in BY_NAME:
            continue
        before = df.get_column(name)
        if before.dtype == pl.Utf8:
            blank = before.is_null() | (before.str.strip_chars() == "")
        else:
            blank = before.is_null()
        failed = int((~blank & out.get_column(name).is_null()).sum())
        if failed:
            problems.append(
                Problem("error", "type", f"{failed:,} value(s) in {name} could not be read", failed)
            )
    return out, problems


def validate(df: pl.DataFrame) -> Report:
    """Check a case table against the schema and measure field coverage by filing year."""
    report = Report(rows=df.height)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        report.problems.append(
            Problem("error", "columns", "missing required column(s): " + ", ".join(missing))
        )
        return report

    df, problems = conform(df)
    report.problems.extend(problems)

    for spec in FIELDS:
        if spec.name in df.columns and not spec.nullable:
            n = int(df.get_column(spec.name).is_null().sum())
            if n:
                report.problems.append(
                    Problem("error", "nulls", f"{n:,} row(s) have no {spec.name}", n)
                )

    dupes = df.height - df.get_column("case_id").n_unique()
    if dupes:
        report.problems.append(
            Problem(
                "error",
                "grain",
                f"{dupes:,} duplicate case_id row(s); expected one per case",
                dupes,
            )
        )

    bad = df.filter(
        pl.col("disposition_class").is_not_null()
        & ~pl.col("disposition_class").is_in(DISPOSITION_CLASSES)
    )
    if bad.height:
        values = ", ".join(sorted(bad.get_column("disposition_class").unique().to_list())[:5])
        report.problems.append(
            Problem(
                "error",
                "disposition",
                f"{bad.height:,} row(s) with unknown class: {values}",
                bad.height,
            )
        )

    half = df.filter(pl.col("disposition_class").is_null() != pl.col("disposition_date").is_null())
    if half.height:
        report.problems.append(
            Problem(
                "warning",
                "disposition",
                f"{half.height:,} row(s) have a disposition class without a date, or a date without a class",
                half.height,
            )
        )

    for name in DATE_FIELDS:
        if name == "filed_date" or name not in df.columns:
            continue
        early = df.filter(pl.col(name) < pl.col("filed_date")).height
        if early:
            report.problems.append(
                Problem(
                    "warning", "order", f"{early:,} row(s) have {name} before filed_date", early
                )
            )

    report.coverage = coverage(df)
    return report


def coverage(df: pl.DataFrame) -> pl.DataFrame:
    """Share of rows with a value, for every schema field present, by filing year.

    Printed beside any figure that depends on the field, because a measure built on a field
    that half the courts don't fill describes recording habits as much as it describes cases.
    """
    present = [f.name for f in FIELDS if f.name in df.columns]
    return (
        df.with_columns(pl.col("filed_date").dt.year().alias("filed_year"))
        .group_by("filed_year")
        .agg([pl.len().alias("cases")] + [pl.col(c).is_not_null().mean().alias(c) for c in present])
        .sort("filed_year")
    )


def read_cases(path: str | Path) -> pl.DataFrame:
    """Read a case table from CSV or parquet and cast it to the schema.

    The entry point for anyone bringing their own data: export one row per case with the
    schema's column names, then call this. Raises if the table fails validation.
    """
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        raw = pl.read_parquet(path)
    else:
        raw = pl.read_csv(path, infer_schema=False)
    report = validate(raw)
    if not report.ok:
        details = "; ".join(p.message for p in report.errors())
        raise ValueError(f"{path.name} does not match the case schema: {details}")
    out, _ = conform(raw)
    return out
