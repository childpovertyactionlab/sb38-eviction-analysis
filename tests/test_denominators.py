"""Filing-rate denominators, tested offline with Census-shaped fake responses."""

from __future__ import annotations

import urllib.error

import polars as pl
import pytest

from sb38 import denominators as d


def _get(url: str):
    if "/2026/" in url:  # not published yet
        raise urllib.error.HTTPError(url, 404, "not found", None, None)
    assert "B25003_003E" in url and "county:113" in url and "key=" not in url
    est = "500000" if "acs1" in url else "480000"
    return [["B25003_003E", "B25003_003M", "state", "county"], [est, "9000", "48", "113"]]


def test_fetch_skips_unpublished_years() -> None:
    t = d.fetch("48", "113", [2025, 2026], get=_get)
    assert t.height == 2  # one per source for 2025; 2026 skipped
    assert set(t.get_column("source")) == {"acs1", "acs5"}


def test_year_matching_carries_forward_and_flags_it() -> None:
    m = d.year_matched(d.fetch("48", "113", [2024, 2025, 2026], get=_get), [2024, 2025, 2026])
    acs1 = m.filter(pl.col("source") == "acs1").sort("year")
    assert acs1.get_column("vintage").to_list() == [2024, 2025, 2025]
    assert acs1.get_column("carried_forward").to_list() == [False, False, True]


def test_filing_rates_per_thousand_with_range() -> None:
    filings = pl.DataFrame(
        {"frame": ["jan_may"], "year": [2026], "month": [None], "value": [20_000.0]},
        schema_overrides={"month": pl.Int8},
    )
    dens = pl.DataFrame(
        {
            "year": [2026],
            "source": ["acs1"],
            "vintage": [2025],
            "renter_households": [400_000.0],
            "moe90": [4_000.0],
            "carried_forward": [True],
        }
    )
    r = d.filing_rates(filings, dens).row(0, named=True)
    assert r["rate_per_1000"] == pytest.approx(50.0)
    assert r["rate_low"] < 50.0 < r["rate_high"]
