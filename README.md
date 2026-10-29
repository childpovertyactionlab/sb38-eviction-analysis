# SB 38 Evictions Analysis

> Draft version ... methods are still being settled on, so don't cite figures yet!

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

## Layout

| Path | Purpose |
|---|---|
| `docs/methods.md` | Methods explainer |
| `docs/schema.md` | Intended case table schema |
| `config/` | Per-county settings, such as the Dallas disposition map |
| `notebooks/` | Results notebooks, committed with outputs |
| `results/` | Per-county analysis results, as CSV |
| `src/sb38/` | Implemented codebase |
| `fixtures/` | Invented test data from a seeded generator |
| `tests/` | Tests, runnable without any real data |

No real case data is to ever be committed to this repo.

## License

MIT. See [`LICENSE`](LICENSE).
