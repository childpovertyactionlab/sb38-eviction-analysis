"""Command line: ``sb38 validate <file>`` checks a case table against the schema."""

from __future__ import annotations

import argparse
import sys

import polars as pl

from sb38 import schema


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sb38")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="check a case table (CSV or parquet) against the schema")
    v.add_argument("path")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.path)
    return 2


if __name__ == "__main__":
    sys.exit(main())
