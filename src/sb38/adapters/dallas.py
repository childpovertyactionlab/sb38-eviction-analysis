"""Dallas County: the county's production outcome tables in Databricks, as the case table.

The source is the case-outcomes view built by CPAL's data engineering pipeline. It starts from
every filed case and attaches the county's judgment record where one exists, so cases with no
outcome yet are already in it, which is the denominator the timing measures need. Event dates
not in the view (writ, appeal) come from the matching silver case table.

Rows are pulled into memory and nothing is written to disk. Querying needs CPAL's Databricks
access and CPAL's analyst-pier helper; mapping a frame you already hold (``to_cases``) does
not.

The live tables change with every county delivery, so released figures read a frozen copy of
the query result kept in CPAL's Databricks sandbox (``V1_0``). ``freeze`` makes such a copy
inside Databricks; pass ``snapshot=None`` to read the live tables instead.

The mapping from the county's outcome labels to disposition classes lives in
``config/dallas/disposition_map.csv``. It is exhaustive: an outcome pair missing from that file
stops the run rather than falling into a default class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from sb38 import schema

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "dallas"
DISPOSITION_MAP = CONFIG_DIR / "disposition_map.csv"
CLOSURES = CONFIG_DIR / "closures.csv"

SQL = """
SELECT
    o.case_number,
    o.court,
    o.date_filed,
    o.is_commercial,
    o.orr_status,
    o.outcome_category,
    o.judgment_type,
    o.judgment_date,
    o.defendant_count,
    o.outcome_reliability,
    o.orr_last_delivery_date,
    f.writ_issued_dt,
    f.writ_served_dt,
    f.appeal_date,
    s.citation_served_date,
    s.any_constable,
    s.any_other_server
FROM {gold}.housing.evictions_dallas_outcomes AS o
LEFT JOIN {silver}.county.evictions_dallas_orr_case_final AS f
    ON f.case_number = o.case_number
LEFT JOIN (
    -- One row per case from the defendant-level service records in each defendant's latest
    -- delivery. A server label naming a constable counts as constable. "OUT OF COUNTY" names no
    -- agency, so it counts as neither.
    SELECT
        case_number,
        min(citation_served_date) AS citation_served_date,
        max(CASE WHEN upper(actual_server) LIKE '%CONST%' THEN 1 ELSE 0 END) AS any_constable,
        max(CASE WHEN actual_server IS NOT NULL
                  AND upper(actual_server) NOT LIKE '%CONST%'
                  AND upper(trim(actual_server)) <> 'OUT OF COUNTY'
            THEN 1 ELSE 0 END) AS any_other_server
    FROM {silver}.county.evictions_dallas_orr_service_final
    WHERE is_current
    GROUP BY case_number
) AS s
    ON s.case_number = o.case_number
WHERE o.date_filed BETWEEN :start AND :end
"""


@dataclass(frozen=True)
class Snapshot:
    """A frozen copy of the query result, for cases filed from ``start`` to ``end``."""

    table: str  # schema.table, in the workspace's sandbox catalog
    taken: date
    start: date
    end: date

    def covers(self, start: date, end: date) -> bool:
        return self.start <= start and end <= self.end


# Behind the v1.0 results. Wide enough for the RDD draft's seasons as well as the cohorts.
V1_0 = Snapshot(
    table="taylor.sb38_dallas_cases_20261104",
    taken=date(2026, 11, 4),
    start=date(2023, 4, 1),
    end=date(2026, 5, 31),
)


@dataclass(frozen=True)
class Pull:
    """A Dallas pull: the case table plus what is needed to state it honestly."""

    cases: pl.DataFrame
    as_of: date  # latest outcome delivery; the data date for every outcome figure
    status_counts: pl.DataFrame  # filed cases by year and match status


def load_map(path: Path = DISPOSITION_MAP) -> pl.DataFrame:
    """The disposition map, with empty cells as nulls so unrecorded judgment types still join."""
    m = pl.read_csv(path, infer_schema=False).with_columns(
        [
            pl.when(pl.col(c).str.strip_chars() == "").then(None).otherwise(pl.col(c)).alias(c)
            for c in ("outcome_category", "judgment_type", "disposition_class")
        ]
    )
    dupes = m.select("outcome_category", "judgment_type").is_duplicated().sum()
    if dupes:
        raise ValueError(f"{dupes} duplicate (outcome_category, judgment_type) rows in {path.name}")
    unknown = set(m.get_column("disposition_class").drop_nulls()) - set(schema.DISPOSITION_CLASSES)
    if unknown:
        raise ValueError(f"unknown disposition class(es) in {path.name}: {sorted(unknown)}")
    return m


def to_cases(raw: pl.DataFrame, mapping: pl.DataFrame | None = None) -> pl.DataFrame:
    """Map a frame shaped like the query result onto the case schema."""
    mapping = load_map() if mapping is None else mapping
    key = ["outcome_category", "judgment_type"]
    with_outcome = raw.filter(pl.col("outcome_category").is_not_null())
    joined = with_outcome.join(
        mapping.select(*key, "disposition_class"), on=key, how="left", nulls_equal=True
    )

    known = mapping.select(key).with_columns(pl.lit(True).alias("_in_map"))
    missing = (
        with_outcome.select(key)
        .unique()
        .join(known, on=key, how="left", nulls_equal=True)
        .filter(pl.col("_in_map").is_null())
    )
    if missing.height:
        pairs = ", ".join(f"({a}, {b})" for a, b in missing.select(key).iter_rows())
        raise ValueError(
            f"outcome pair(s) not in {DISPOSITION_MAP.name}: {pairs}. "
            "Add a row before using this pull."
        )

    no_outcome = raw.filter(pl.col("outcome_category").is_null()).with_columns(
        pl.lit(None, dtype=pl.Utf8).alias("disposition_class")
    )
    both = pl.concat([joined, no_outcome], how="diagonal_relaxed")

    return both.select(
        pl.col("case_number").cast(pl.Utf8).alias("case_id"),
        pl.col("court").cast(pl.Utf8),
        pl.col("date_filed").cast(pl.Date).alias("filed_date"),
        # The county's filings feed drops commercial cases upstream, so a filed case with no
        # outcome record (and so no commercial flag) is residential.
        (~pl.col("is_commercial").fill_null(False)).alias("is_residential"),
        pl.col("disposition_class"),
        pl.when(pl.col("disposition_class").is_not_null())
        .then(pl.col("judgment_date").cast(pl.Date))
        .otherwise(None)
        .alias("disposition_date"),
        pl.lit("Dallas").alias("county"),
        pl.col("judgment_type").alias("judgment_type_raw"),
        pl.col("defendant_count").cast(pl.Int64),
        pl.col("writ_issued_dt").cast(pl.Date).alias("writ_issued_date"),
        pl.col("writ_served_dt").cast(pl.Date).alias("writ_executed_date"),
        pl.col("appeal_date").cast(pl.Date),
        pl.col("citation_served_date").cast(pl.Date),
        pl.when(pl.col("any_other_server") == 1)
        .then(pl.lit("other"))
        .when(pl.col("any_constable") == 1)
        .then(pl.lit("constable"))
        .otherwise(None)
        .alias("citation_server_type"),
    ).sort("filed_date", "case_id")


def _warehouse(role: str):
    try:
        from analyst_pier import Warehouse, load_config
    except ImportError as exc:  # pragma: no cover - only CPAL has the helper
        raise ImportError(
            "Querying Dallas needs CPAL's Databricks access and its analyst-pier helper: "
            'uv run --with "analyst-pier @ git+ssh://git@github.com/childpovertyactionlab/'
            'analyst-pier.git@v0.1.0#subdirectory=python" ...'
        ) from exc
    workspace = load_config().workspace(role)
    return Warehouse(workspace), workspace


def _live_sql(workspace) -> str:
    return SQL.format(gold=workspace.catalog("gold"), silver=workspace.catalog("silver"))


def fetch(
    start: date, end: date, *, role: str = "public", snapshot: Snapshot | None = V1_0
) -> pl.DataFrame:
    """Run the query through CPAL's Databricks helper and return the raw frame, in memory.

    Reads ``snapshot`` when given, the live tables otherwise.
    """
    if snapshot is not None and not snapshot.covers(start, end):
        raise ValueError(
            f"{snapshot.table} holds cases filed {snapshot.start} to {snapshot.end}; "
            f"asked for {start} to {end}. Pass snapshot=None to read the live tables."
        )
    warehouse, workspace = _warehouse(role)
    if snapshot is None:
        sql = _live_sql(workspace)
    else:
        sql = (
            f"SELECT * FROM {workspace.catalog('sandbox')}.{snapshot.table} "
            "WHERE date_filed BETWEEN :start AND :end"
        )
    return warehouse.query(sql, {"start": start, "end": end})


def freeze(snapshot: Snapshot, *, role: str = "public") -> int:
    """Copy the live query result into ``snapshot.table``. Returns the row count.

    The copy is made inside Databricks, so no case row leaves it. An existing table is never
    replaced.
    """
    warehouse, workspace = _warehouse(role)
    target = f"{workspace.catalog('sandbox')}.{snapshot.table}"
    comment = (
        f"SB 38 analysis: Dallas case table for cases filed {snapshot.start} to {snapshot.end}, "
        f"frozen {snapshot.taken} from the county outcome view and ORR silver tables"
    )
    warehouse.execute(
        f"CREATE TABLE {target} COMMENT '{comment}' AS {_live_sql(workspace)}",
        {"start": snapshot.start, "end": snapshot.end},
    )
    return warehouse.query(f"SELECT count(*) AS n FROM {target}").item()


def load(
    start: date = date(2024, 1, 1),
    end: date = date(2026, 5, 31),
    *,
    snapshot: Snapshot | None = V1_0,
) -> Pull:
    """Pull Dallas cases filed between ``start`` and ``end`` and return them in the schema."""
    raw = fetch(start, end, snapshot=snapshot)
    cases = to_cases(raw)
    report = schema.validate(cases)
    if not report.ok:
        raise ValueError("; ".join(p.message for p in report.errors()))
    as_of = raw.get_column("orr_last_delivery_date").drop_nulls().max()
    status = (
        raw.with_columns(pl.col("date_filed").dt.year().alias("year"))
        .group_by("year", "orr_status")
        .len()
        .sort("year", "orr_status")
    )
    return Pull(cases=cases, as_of=as_of, status_counts=status)


STATE_FIPS, COUNTY_FIPS = "48", "113"


def results(
    start: date = date(2024, 1, 1),
    end: date = date(2026, 5, 31),
    *,
    with_coverage: bool = False,
    snapshot: Snapshot | None = V1_0,
):
    """The results table for Dallas County: pull, fetch renter households, compute.

    With ``with_coverage``, also returns the coverage-by-court table.
    """
    from sb38 import denominators, export, measures

    pull = load(start, end, snapshot=snapshot)
    renters = denominators.fetch(STATE_FIPS, COUNTY_FIPS, measures.COHORT_YEARS)
    table = export.results(
        pull.cases,
        as_of=pull.as_of,
        county="Dallas",
        renters=renters,
        pulled_on=snapshot.taken if snapshot is not None else date.today(),
        closures=pl.read_csv(CLOSURES, infer_schema=False),
    )
    if with_coverage:
        return table, export.coverage(pull.cases, as_of=pull.as_of, county="Dallas")
    return table


if __name__ == "__main__":
    import argparse

    from sb38 import export

    parser = argparse.ArgumentParser(prog="python -m sb38.adapters.dallas")
    parser.add_argument("--out", required=True, help="where to write the results CSV")
    parser.add_argument(
        "--coverage-out", help="where to write coverage by court (default: beside --out)"
    )
    parser.add_argument(
        "--live", action="store_true", help="read the live tables instead of the v1.0 snapshot"
    )
    args = parser.parse_args()
    table, cov = results(with_coverage=True, snapshot=None if args.live else V1_0)
    print(f"wrote {export.write(table, args.out)}: {table.height:,} rows")
    cov_out = args.coverage_out or export.coverage_path(args.out)
    print(f"wrote {export.write(cov, cov_out)}: {cov.height:,} rows")
