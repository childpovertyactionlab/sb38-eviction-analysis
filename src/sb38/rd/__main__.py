"""Run the prototype on Dallas County and write its estimates: ``python -m sb38.rd --out PATH``.

Reads the v1.0 snapshot through the Dallas adapter, so it needs CPAL's Databricks access and the
``rdd`` extra; cases stay in memory. Any other county exports its case table and runs ``sb38 rd``.
Only the estimates are written, never case rows.
"""

from __future__ import annotations

import argparse
from datetime import date

import polars as pl

from sb38.adapters import dallas
from sb38.rd import summary

# The snapshot's window, which covers every filing the prototype needs.
START, END = date(2023, 4, 1), date(2026, 5, 31)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sb38.rd")
    parser.add_argument("--out", required=True, help="where to write the estimates CSV")
    args = parser.parse_args(argv)
    pull = dallas.load(START, END)
    closures = pl.read_csv(dallas.CLOSURES, infer_schema=False).get_column("date")
    table = summary.estimates(
        pull.cases, as_of=pull.as_of, closures=[date.fromisoformat(d) for d in closures]
    )
    print(f"wrote {summary.write(table, args.out)}: {table.height} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
