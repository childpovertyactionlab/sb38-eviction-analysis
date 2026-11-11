"""Harris County: the justice courts' public case pages, as the case table.

January Advisors scraped the public case page of every Harris County justice court eviction case
filed January through May of 2024, 2025 and 2026 (jpwebsite.harriscountytx.gov, scraped
2026-11-06 to 2026-11-09) and froze one row per case to a CSV that it keeps outside this
repository. This adapter maps a frame shaped like that file onto the case schema. It has no
database dependency, so anyone holding the frozen file can rerun the Harris results.

Why case pages and not the county's bulk extract: when a case is appealed, the bulk extract
replaces the justice court outcome with "Appeal Filed", while the case page keeps the original
judgment in its Judgments section. ``config/harris/disposition_map.csv`` classes those cases by
that judgment.

What the frozen file holds (one row per case):

- ``case_number``, ``court`` (from the bulk extract), ``filed_date``, ``nature_of_claim``
- ``disposition`` and ``disposition_date``: the case page's disposition
- ``judgment_titles``: the justice court judgment, used only where the disposition is
  "Appeal Filed"
- ``first_writ_issued``: the first "Writ of Possession Issued" docket event
- ``first_appeal_any``: the first appeal docket event (appeal filed, appeal bond, statement of
  inability to pay or cash deposit for appeal)

Departures from the Dallas table, each stated in the results:

- **Residential.** Harris case pages give the residential or commercial type only from 2026;
  earlier cases say only "Eviction". Every case is kept as residential in every year so the
  cohorts compare. In 2026, 29,933 of 30,449 cases are residential, 162 are commercial, and
  354 have no type or a type that is not an eviction (such as debt or small claims).
- **Service and writ execution.** These dates are on the constables' scanned returns, not on the
  case page. January Advisors read every 2026 return and a court-stratified sample of 2024 and
  2025 returns. The pipeline expects every case, so service is left out of this table and writ
  execution is left empty, and the sample-based figures are reported separately with their
  margins. Writ issuance is complete in every year.
- **Pages not available.** 28 case pages say the case is confidential or not available, and
  one 2024 page always returned an error. They have no filed date and are not in the table.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from sb38 import schema

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "harris"
DISPOSITION_MAP = CONFIG_DIR / "disposition_map.csv"
CLOSURES = CONFIG_DIR / "closures.csv"
STATE_FIPS, COUNTY_FIPS = "48", "201"
APPEAL = "Appeal Filed"


@dataclass(frozen=True)
class Snapshot:
    """A frozen case table: its file name, when it was scraped, and the filing window."""

    name: str
    taken: date  # last day of the scrape; the data date for every figure
    start: date
    end: date


V1_0 = Snapshot(
    name="harris-cpal-cases-2026-11-10.csv",
    taken=date(2026, 11, 9),
    start=date(2024, 1, 1),
    end=date(2026, 5, 31),
)


def load_map(path: Path = DISPOSITION_MAP) -> pl.DataFrame:
    m = pl.read_csv(path, infer_schema=False).with_columns(
        [
            pl.when(pl.col(c).str.strip_chars() == "").then(None).otherwise(pl.col(c)).alias(c)
            for c in ("judgment_title", "disposition_class")
        ]
    )
    dupes = m.select("disposition", "judgment_title").is_duplicated().sum()
    if dupes:
        raise ValueError(f"{dupes} duplicate (disposition, judgment_title) rows in {path.name}")
    unknown = set(m.get_column("disposition_class").drop_nulls()) - set(schema.DISPOSITION_CLASSES)
    if unknown:
        raise ValueError(f"unknown disposition class(es) in {path.name}: {sorted(unknown)}")
    return m


def _blank_to_null(col: str) -> pl.Expr:
    c = pl.col(col).cast(pl.Utf8).str.strip_chars()
    return pl.when(c == "").then(None).otherwise(c)


def to_cases(raw: pl.DataFrame, mapping: pl.DataFrame | None = None) -> pl.DataFrame:
    """Map a frame shaped like the frozen file onto the case schema."""
    mapping = load_map() if mapping is None else mapping
    key = ["disposition", "judgment_title"]
    df = raw.with_columns(
        _blank_to_null("disposition").alias("disposition"),
        # The judgment title matters only when the page shows the appeal as the disposition.
        pl.when(_blank_to_null("disposition") == APPEAL)
        .then(_blank_to_null("judgment_titles"))
        .otherwise(None)
        .alias("judgment_title"),
    )
    with_outcome = df.filter(pl.col("disposition").is_not_null())
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
            f"disposition pair(s) not in {DISPOSITION_MAP.name}: {pairs}. "
            "Add a row before using this table."
        )
    joined = df.join(
        mapping.select(*key, "disposition_class"), on=key, how="left", nulls_equal=True
    )

    def day(col: str) -> pl.Expr:
        return _blank_to_null(col).str.slice(0, 10).str.to_date("%Y-%m-%d")

    return joined.select(
        pl.col("case_number").cast(pl.Utf8).alias("case_id"),
        pl.col("court").cast(pl.Utf8),
        day("filed_date").alias("filed_date"),
        # See the module note: residential type is recorded only from 2026.
        pl.lit(True).alias("is_residential"),
        pl.col("disposition_class"),
        pl.when(pl.col("disposition_class").is_not_null())
        .then(day("disposition_date"))
        .otherwise(None)
        .alias("disposition_date"),
        pl.lit("Harris").alias("county"),
        pl.col("disposition").alias("judgment_type_raw"),
        day("first_writ_issued").alias("writ_issued_date"),
        # Execution dates are on the scanned returns; see the module note.
        pl.lit(None, dtype=pl.Date).alias("writ_executed_date"),
        day("first_appeal_any").alias("appeal_date"),
    ).sort("filed_date", "case_id")


def read(path: str | Path) -> pl.DataFrame:
    """Read the frozen file as text, so case numbers and blank cells survive as written."""
    return pl.read_csv(path, infer_schema=False)


def load(path: str | Path) -> pl.DataFrame:
    cases = to_cases(read(path))
    report = schema.validate(cases)
    if not report.ok:
        raise ValueError("; ".join(p.message for p in report.errors()))
    return cases


SUMMARY_FILE = (
    "https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/"
    "{folder}/acs{tag}{year}-b25003.dat"
)


def summary_file_renters(years=(2024, 2025, 2026)) -> pl.DataFrame:
    """Renter households (B25003_003) for Harris County from the ACS summary files.

    Same shape as ``denominators.fetch``, but read from the Census Bureau's published summary
    files, which need no API key. A year not yet published (404) is skipped.
    """
    rows = []
    for source, folder, tag in (("acs1", "1YRData", "dt1y"), ("acs5", "5YRData", "dt5y")):
        for year in years:
            url = SUMMARY_FILE.format(year=year, folder=folder, tag=tag)
            try:
                with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - fixed host
                    lines = resp.read().decode().splitlines()
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                continue
            header = lines[0].split("|")
            geo = f"0500000US{STATE_FIPS}{COUNTY_FIPS}"
            rec = next(
                dict(zip(header, ln.split("|"), strict=False)) for ln in lines if ln.startswith(geo)
            )
            rows.append(
                {
                    "source": source,
                    "vintage": year,
                    "estimate": float(rec["B25003_E003"]),
                    "moe90": float(rec["B25003_M003"]),
                }
            )
    if not rows:
        raise RuntimeError("No renter-household estimates found in the ACS summary files.")
    return pl.DataFrame(
        rows,
        schema={
            "source": pl.Utf8,
            "vintage": pl.Int64,
            "estimate": pl.Float64,
            "moe90": pl.Float64,
        },
    ).sort("source", "vintage")


def results(path: str | Path, *, snapshot: Snapshot = V1_0, with_coverage: bool = False):
    """The results table for Harris County from the frozen file at ``path``."""
    from sb38 import export, measures

    cases = load(path)
    renters = summary_file_renters(measures.COHORT_YEARS)
    table = export.results(
        cases,
        as_of=snapshot.taken,
        county="Harris",
        renters=renters,
        pulled_on=snapshot.taken,
        closures=pl.read_csv(CLOSURES, infer_schema=False),
    )
    if with_coverage:
        cov = export.coverage(cases, as_of=snapshot.taken, county="Harris")
        # Writ execution is left empty on purpose (module note), so its coverage rows would
        # read as courts that record nothing. They are dropped rather than published.
        cov = cov.filter(pl.col("field") != "writ_executed_date")
        return table, cov
    return table


if __name__ == "__main__":
    import argparse

    from sb38 import export

    parser = argparse.ArgumentParser(prog="python -m sb38.adapters.harris")
    parser.add_argument("cases", help="the frozen Harris case file (kept outside this repository)")
    parser.add_argument("--out", required=True, help="where to write the results CSV")
    parser.add_argument("--coverage-out", help="default: beside --out")
    args = parser.parse_args()
    table, cov = results(args.cases, with_coverage=True)
    print(f"wrote {export.write(table, args.out)}: {table.height:,} rows")
    cov_out = args.coverage_out or export.coverage_path(args.out)
    print(f"wrote {export.write(cov, cov_out)}: {cov.height:,} rows")
