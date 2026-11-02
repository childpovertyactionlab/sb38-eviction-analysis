"""Service of the citation, writs and appeals, on the courts that record each one.

These steps are recorded unevenly. Some courts fill a field for nearly every case and others
leave it empty, and a court can start or stop recording between years. A comparison across
years is only fair on courts that recorded the field in every cohort year, so each measure here
is computed on those courts and says which they are. ``coverage`` gives the share recorded by
court and year, so readers can see what was left out.

Events after judgment (writs and appeals) follow the lag rule: an event counts only if it is
dated on or before June 9 of the cohort's year. Service of the citation comes before judgment,
so the rule doesn't apply to it.

Output has the same long layout as ``sb38.measures``.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from sb38.measures import COHORT_YEARS, COLUMNS, _by_frame, _long, cohorts
from sb38.schema import JUDGMENT_ENTERED

CUT_MONTH, CUT_DAY = 6, 9  # the lag rule's common cut: June 9 of the cohort year

# A court records a field if, in every cohort year, at least this share is filled.
SERVICE_THRESHOLD = 0.80  # cases filed with a service date
WRIT_EXECUTION_THRESHOLD = 0.40  # writs issued by the cut with an execution date by the cut
APPEAL_THRESHOLD = 0.01  # cases filed with an appeal recorded by the cut

SERVER_TYPES = ("constable", "other")


def _cut() -> pl.Expr:
    return pl.date(pl.col("year"), CUT_MONTH, CUT_DAY)


def _by_cut(col: str) -> pl.Expr:
    return pl.col(col).is_not_null() & (pl.col(col) <= _cut())


def _every_case() -> pl.Expr:
    """True on every row. A bare literal would sum to 1 per group, not to the row count."""
    return pl.col("case_id").is_not_null()


def _has(cases: pl.DataFrame, *cols: str) -> bool:
    return all(c in cases.columns for c in cols)


def coverage(cases: pl.DataFrame, years=COHORT_YEARS) -> pl.DataFrame:
    """Share of each field recorded, by court and cohort year, and whether the court qualifies.

    One row per field, court and year: ``cases`` (filed), ``base`` (the cases the share is taken
    over), ``recorded``, ``share``, and ``recording`` (the court met the threshold in every year).
    """
    df = cohorts(cases, years=years)
    specs = []
    if _has(df, "citation_served_date"):
        specs.append(
            (
                "citation_served_date",
                _every_case(),
                pl.col("citation_served_date").is_not_null(),
                SERVICE_THRESHOLD,
            )
        )
    if _has(df, "writ_issued_date", "writ_executed_date"):
        specs.append(
            (
                "writ_executed_date",
                _by_cut("writ_issued_date"),
                _by_cut("writ_executed_date"),
                WRIT_EXECUTION_THRESHOLD,
            )
        )
    if _has(df, "appeal_date"):
        specs.append(("appeal_date", _every_case(), _by_cut("appeal_date"), APPEAL_THRESHOLD))

    parts = []
    for field, base, recorded, threshold in specs:
        t = (
            df.group_by("court", "year")
            .agg(
                pl.len().alias("cases"),
                base.sum().alias("base"),
                (base & recorded).sum().alias("recorded"),
            )
            .with_columns(
                pl.lit(field).alias("field"),
                (pl.col("recorded") / pl.col("base")).alias("share"),
                pl.lit(threshold).alias("threshold"),
            )
        )
        ok = t.group_by("court").agg(
            (
                (pl.col("share") >= threshold).fill_null(False).all() & (pl.len() == len(years))
            ).alias("recording")
        )
        parts.append(t.join(ok, on="court"))
    if not parts:
        return pl.DataFrame()
    return (
        pl.concat(parts)
        .select(
            "field", "court", "year", "cases", "base", "recorded", "share", "threshold", "recording"
        )
        .sort("field", "court", "year")
    )


def recording_courts(cov: pl.DataFrame, field: str) -> list[str]:
    """Courts that recorded ``field`` at or above its threshold in every cohort year."""
    if cov.is_empty():
        return []
    return sorted(
        cov.filter((pl.col("field") == field) & pl.col("recording")).get_column("court").unique()
    )


def _courts_note(cases: pl.DataFrame, courts: list[str], what: str) -> str:
    df = cohorts(cases)
    share = df.filter(pl.col("court").is_in(courts)).height / max(df.height, 1)
    return (
        f"{what} on {len(courts)} court(s) recording it every year ({share:.0%} of filings): "
        + ", ".join(courts)
    )


def _days_table(df: pl.DataFrame, days: pl.Expr, name: str, note: str) -> pl.DataFrame:
    """Mean and median of ``days`` per frame, with the count behind them."""
    d = df.with_columns(days.alias("_days")).filter(pl.col("_days").is_not_null())
    bad = d.filter(pl.col("_days") < 0).height
    d = d.filter(pl.col("_days") >= 0)
    wide = _by_frame(
        d,
        [
            pl.len().alias("n"),
            pl.col("_days").mean().alias("mean"),
            pl.col("_days").median().alias("median"),
        ],
    )
    full_note = note + (f"; {bad} event(s) dated before the start date, excluded" if bad else "")
    return pl.concat(
        [
            wide.select(
                "frame",
                "year",
                "month",
                pl.lit(f"{stat}_{name}").alias("measure"),
                pl.col(stat).cast(pl.Float64).alias("value"),
                pl.lit(None, dtype=pl.Int64).alias("numerator"),
                pl.col("n").cast(pl.Int64).alias("denominator"),
                pl.lit(full_note).alias("note"),
            )
            for stat in ("mean", "median")
        ]
    )


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema={c: pl.Utf8 for c in COLUMNS}).select(
        pl.col("frame"),
        pl.col("year").cast(pl.Int32),
        pl.col("month").cast(pl.Int8),
        pl.col("measure"),
        pl.col("value").cast(pl.Float64),
        pl.col("numerator").cast(pl.Int64),
        pl.col("denominator").cast(pl.Int64),
        pl.col("note"),
    )


def service(cases: pl.DataFrame, cov: pl.DataFrame) -> pl.DataFrame:
    """Days from filing to service of the citation, on courts recording service every year."""
    courts = recording_courts(cov, "citation_served_date")
    if not courts:
        return _empty()
    df = cohorts(cases).filter(pl.col("court").is_in(courts))
    days = (pl.col("citation_served_date") - pl.col("filed_date")).dt.total_days()
    return _days_table(df, days, "days_to_service", _courts_note(cases, courts, "Service"))


def non_constable_service(cases: pl.DataFrame) -> pl.DataFrame:
    """Share of served cases whose recorded server was not a constable.

    Taken over cases with a recorded server type, in whatever courts and years record it.
    A year with no server types recorded gets no row.
    """
    if not _has(cases, "citation_server_type"):
        return _empty()
    df = cohorts(cases).filter(pl.col("citation_server_type").is_in(list(SERVER_TYPES)))
    if df.is_empty():
        return _empty()
    wide = _by_frame(
        df,
        [pl.len().alias("typed"), (pl.col("citation_server_type") == "other").sum().alias("other")],
    )
    note = "Of served cases with a recorded server type; the field is not recorded in every court or year"
    return _long(
        wide,
        [
            ("non_constable_service_share", "other", "typed"),
            ("non_constable_service_count", "other", ""),
        ],
        note=note,
    )


def writs(cases: pl.DataFrame, cov: pl.DataFrame) -> pl.DataFrame:
    """Writ timing and execution, under the lag rule.

    Judgment to issuance uses every court. Execution measures use only courts recording
    execution every year. Days from judgment count only cases that ended in a judgment.
    """
    if not _has(cases, "writ_issued_date", "writ_executed_date"):
        return _empty()
    lag = "events dated on or before June 9 of the cohort year"
    df = cohorts(cases)
    judged = df.filter(pl.col("disposition_class").is_in(list(JUDGMENT_ENTERED)))
    issued = judged.filter(_by_cut("writ_issued_date"))
    parts = [
        _days_table(
            issued,
            (pl.col("writ_issued_date") - pl.col("disposition_date")).dt.total_days(),
            "days_judgment_to_writ_issued",
            f"All courts; {lag}",
        )
    ]

    courts = recording_courts(cov, "writ_executed_date")
    if courts:
        note = _courts_note(cases, courts, "Writ execution") + f"; {lag}"
        executed = judged.filter(
            pl.col("court").is_in(courts)
            & _by_cut("writ_issued_date")
            & _by_cut("writ_executed_date")
        )
        parts += [
            _days_table(
                executed,
                (pl.col("writ_executed_date") - pl.col("disposition_date")).dt.total_days(),
                "days_judgment_to_writ_executed",
                note,
            ),
            _days_table(
                executed,
                (pl.col("writ_executed_date") - pl.col("writ_issued_date")).dt.total_days(),
                "days_writ_issued_to_executed",
                note,
            ),
        ]
        in_courts = df.filter(pl.col("court").is_in(courts))
        wide = _by_frame(
            in_courts,
            [pl.len().alias("filed"), _by_cut("writ_executed_date").sum().alias("executed")],
        )
        parts.append(
            _long(
                wide,
                [
                    ("writ_executed_share", "executed", "filed"),
                    ("writ_executed_count", "executed", ""),
                ],
                note=note,
            )
        )
    return pl.concat(parts).select(COLUMNS).sort("measure", "frame", "year", "month")


def appeals(cases: pl.DataFrame, cov: pl.DataFrame) -> pl.DataFrame:
    """Number and share of filed cases with an appeal, on courts recording appeals every year."""
    courts = recording_courts(cov, "appeal_date")
    if not courts:
        return _empty()
    df = cohorts(cases).filter(pl.col("court").is_in(courts))
    wide = _by_frame(df, [pl.len().alias("filed"), _by_cut("appeal_date").sum().alias("appealed")])
    note = (
        _courts_note(cases, courts, "Appeals")
        + "; appeals dated on or before June 9 of the cohort year"
    )
    return _long(
        wide,
        [("appealed_share", "appealed", "filed"), ("appealed_count", "appealed", "")],
        note=note,
    )


def all_measures(cases: pl.DataFrame) -> pl.DataFrame:
    """Every procedure measure the case table supports."""
    cov = coverage(cases)
    return pl.concat(
        [service(cases, cov), non_constable_service(cases), writs(cases, cov), appeals(cases, cov)],
        how="diagonal_relaxed",
    ).select(COLUMNS)


def cut_date(year: int) -> date:
    """The lag rule's cut for a cohort year."""
    return date(year, CUT_MONTH, CUT_DAY)
