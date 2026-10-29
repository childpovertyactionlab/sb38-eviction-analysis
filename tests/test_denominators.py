"""Filing-rate denominators, tested offline with Census-shaped fake responses."""

from __future__ import annotations

import math

import polars as pl
import pytest

from sb38 import denominators as d


def test_acs_table_reads_estimate_and_moe() -> None:
    def get(url: str):
        assert "B25003_003E" in url and "county:113" in url and "key=" not in url
        return [["B25003_003E", "B25003_003M", "state", "county"], ["400000", "5000", "48", "113"]]

    e = d.acs_table(2024, "acs1", "48", "113", get=get)
    assert (e.source, e.vintage, e.estimate, e.moe90) == ("acs1", 2024, 400_000, 5_000)


def test_pums_counts_renters_and_takes_the_replicate_moe() -> None:
    """Two renter households and one owner; every replicate weight is the base weight plus 1."""

    def get(url: str):
        reps = list(range(1, 41)) if "WGTP1," in url else list(range(41, 81))
        if "TEN" in url:
            header = ["SERIALNO", "TEN", "WGTP"] + [f"WGTP{i}" for i in reps]
            rows = [
                ["h1", "3", "100"] + ["101"] * 40,
                ["h2", "4", "50"] + ["51"] * 40,
                ["h3", "1", "70"] + ["71"] * 40,  # owner: excluded
            ]
        else:
            header = ["SERIALNO"] + [f"WGTP{i}" for i in reps]
            rows = [["h1"] + ["101"] * 40, ["h2"] + ["51"] * 40, ["h3"] + ["71"] * 40]
        return [header, *rows]

    e = d.pums_renters(2024, "48", ["02301", "02302"], get=get)
    assert e.estimate == 150
    # each replicate total is 152, so SE = sqrt(4/80 * 80 * 2^2) = 4
    assert e.moe90 == pytest.approx(d.Z90 * math.sqrt(4 / 80 * 80 * 4))


def test_year_matching_carries_forward_and_says_so() -> None:
    table = pl.DataFrame(
        {
            "source": ["acs1", "acs1", "acs5"],
            "vintage": [2024, 2025, 2024],
            "estimate": [400_000.0, 410_000.0, 395_000.0],
            "moe90": [5_000.0, 5_000.0, 2_000.0],
        }
    )
    m = d.year_matched(table, [2024, 2025, 2026])
    acs1 = m.filter(pl.col("source") == "acs1").sort("year")
    assert acs1.get_column("vintage").to_list() == [2024, 2025, 2025]
    assert acs1.get_column("carried_forward").to_list() == [False, False, True]
    acs5 = m.filter(pl.col("source") == "acs5").sort("year")
    assert acs5.get_column("carried_forward").to_list() == [False, True, True]


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
    assert r["carried_forward"] is True
