"""Command line.

``sb38 validate <file>`` checks a case table against the schema.
``sb38 measures <file> --as-of DATE --county NAME --out PATH`` writes the results CSV, and
coverage by court beside it.
``sb38 tables <results.csv> --out PATH`` lays the results out as report tables in a workbook.
``sb38 rd <file> --as-of DATE --out PATH`` writes the RDD prototype's estimates, which
``sb38 tables ... --rd PATH`` adds to the workbook as a sheet.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import polars as pl

from sb38 import denominators, export, measures, schema, tables


def _validate(path: str) -> int:
    raw = (
        pl.read_parquet(path)
        if path.lower().endswith(".parquet")
        else pl.read_csv(path, infer_schema=False)
    )
    report = schema.validate(raw)
    print(f"{path}: {report.rows:,} rows")
    for p in report.problems:
        print(f"  {p.level.upper():<8}{p.check:<12}{p.message}")
    if not report.problems:
        print("  no problems found")
    if report.coverage is not None:
        cov = report.coverage
        years = [str(y) for y in cov.get_column("filed_year").to_list()]
        print("\nfield coverage by filing year (share of rows with a value)")
        print(f"  {'field':<24}" + "".join(f"{y:>9}" for y in years))
        print(f"  {'cases':<24}" + "".join(f"{n:>9,}" for n in cov.get_column("cases").to_list()))
        for name in cov.columns[2:]:
            cells = "".join(f"{v:>9.1%}" for v in cov.get_column(name).to_list())
            print(f"  {name:<24}{cells}")
    return 0 if report.ok else 1


def _measures(args: argparse.Namespace) -> int:
    cases = schema.read_cases(args.path)
    renters = None
    if args.census_state and args.census_county:
        renters = denominators.fetch(args.census_state, args.census_county, measures.COHORT_YEARS)
    closures = pl.read_csv(args.closures, infer_schema=False) if args.closures else None
    table = export.results(
        cases,
        as_of=date.fromisoformat(args.as_of),
        county=args.county,
        renters=renters,
        closures=closures,
    )
    out = export.write(table, args.out)
    cov = export.coverage(cases, as_of=date.fromisoformat(args.as_of), county=args.county)
    if cov.height:
        cov_out = export.write(cov, export.coverage_path(args.out))
        print(f"wrote {cov_out}: {cov.height:,} rows")
    done = table.filter(table["status"] == "computed").get_column("stat").n_unique()
    print(
        f"wrote {out}: {table.height:,} rows, {done} of {table.get_column('stat').n_unique()} stats computed"
    )
    return 0


def _tables(args: argparse.Namespace) -> int:
    results = pl.read_csv(args.results)
    # A coverage file named on the command line has to exist; the default one is optional.
    if args.coverage:
        coverage = pl.read_csv(args.coverage)
    else:
        default = export.coverage_path(args.results)
        coverage = pl.read_csv(default) if default.exists() else None
    extra = None
    if args.rd:
        from sb38.rd import summary

        extra = [summary.sheet(pl.read_csv(args.rd))]
    out = tables.write(results, args.out, coverage, extra)
    print(f"wrote {out}")
    return 0


def _rd(args: argparse.Namespace) -> int:
    from sb38.rd import summary

    cases = schema.read_cases(args.path)
    closures = (
        [date.fromisoformat(d) for d in pl.read_csv(args.closures, infer_schema=False)["date"]]
        if args.closures
        else []
    )
    try:
        table = summary.estimates(cases, as_of=date.fromisoformat(args.as_of), closures=closures)
    except ImportError as exc:  # pragma: no cover - optional extra
        raise ImportError("The RDD prototype needs the rdd extra: uv sync --extra rdd") from exc
    print(f"wrote {summary.write(table, args.out)}: {table.height} rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sb38")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="check a case table (CSV or parquet) against the schema")
    v.add_argument("path")
    m = sub.add_parser("measures", help="compute every scoping-document stat and write a CSV")
    m.add_argument("path", help="case table (CSV or parquet) in the schema")
    m.add_argument("--as-of", required=True, help="date the data were pulled, YYYY-MM-DD")
    m.add_argument("--county", required=True, help="county name, written on every row")
    m.add_argument(
        "--out", required=True, help="where to write the results CSV, e.g. results/<county>.csv"
    )
    m.add_argument("--census-state", help="state FIPS for the filing-rate denominator, e.g. 48")
    m.add_argument("--census-county", help="county FIPS for the denominator, e.g. 113")
    m.add_argument("--closures", help="CSV of court-closure days (date, event, confirmed)")
    t = sub.add_parser("tables", help="lay out a results CSV as report tables in a workbook")
    t.add_argument("results", help="results CSV from `sb38 measures`, e.g. results/<county>.csv")
    t.add_argument("--coverage", help="coverage CSV (default: beside the results CSV, if there)")
    t.add_argument("--out", required=True, help="where to write the workbook, e.g. <county>.xlsx")
    t.add_argument("--rd", help="RDD prototype estimates CSV (sb38 rd), added as a sheet")
    r = sub.add_parser("rd", help="write the RDD prototype estimates CSV for a case table")
    r.add_argument("path", help="case table (CSV or parquet) in the schema")
    r.add_argument("--as-of", required=True, help="date the data were pulled, YYYY-MM-DD")
    r.add_argument("--closures", help="CSV of court-closure days (date, event, confirmed)")
    r.add_argument("--out", required=True, help="where to write the estimates CSV")
    args = parser.parse_args(argv)
    if args.command == "tables":
        return _tables(args)
    if args.command == "rd":
        return _rd(args)
    if args.command == "validate":
        return _validate(args.path)
    if args.command == "measures":
        return _measures(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
