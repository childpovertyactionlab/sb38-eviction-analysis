# SB 38 Evictions Analysis

This repo contains methods that measure how eviction cases changed after Texas Senate Bill 38 took effect for suits filed on or after January 1, 2026.

## Structure

The codebase reads a table where every observation is an eviction case (see [`docs/schema.md`](docs/schema.md)). The methods are designed to be agnostic to source as long as the data are plugged in correctly to adhere to the schema.

```
county eviction records  ->  adapter script ->  case table (fits [`docs/schema.md`](docs/schema.md))  ->  analysis
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11 or later.

```
uv sync --extra dev
uv run sb38 validate fixtures/synthetic_cases.csv
uv run pytest
```

To check data fit, export one row per case with the schema's column names and run `uv run sb38 validate <file>`.

To lay out a results CSV as report tables, one sheet per topic, run `uv sync --extra tables` and then `uv run sb38 tables results/<county>.csv --out <county>.xlsx`.

## Statistical comparison (draft)

The regression discontinuity prototype tests whether changes after January 1, 2026 are larger than ordinary movement from one season to the next. It needs a case table with filings from June 1, 2023 through May 31, 2026. To add its estimates to the workbook, run `uv sync --extra rdd --extra tables`, then `uv run sb38 rd <file> --as-of <date> --closures config/<county>/closures.csv --out <county>-rd.csv`, then `uv run sb38 tables results/<county>.csv --rd <county>-rd.csv --out <county>.xlsx`. `--as-of` is the date the data were pulled, and the closures file lists court-closure days in the format of `config/dallas/closures.csv`. The estimates are a draft until they have had their quality check. The method is in `docs/rdd/analysis-plan.md`.

## Layout

| Path | Purpose |
|---|---|
| `docs/methods.md` | Methods explainer |
| `docs/schema.md` | Intended case table schema |
| `docs/rdd/` | Analysis plan for the regression discontinuity prototype, committed before any estimate was run |
| `config/` | Per-county settings, such as the Dallas disposition map |
| `notebooks/` | Results notebooks, committed with outputs |
| `results/` | Per-county analysis results, as CSV. Workbooks built from them are not committed. |
| `src/sb38/` | Implemented codebase |
| `fixtures/` | Invented test data from a seeded generator |
| `tests/` | Tests, runnable without any real data |

No real case data is to ever be committed to this repo.

## License

MIT. See [`LICENSE`](LICENSE).
