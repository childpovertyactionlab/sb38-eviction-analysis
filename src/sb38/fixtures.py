"""Synthetic cases for tests and for anyone building an adapter without real data.

Every value here is invented by a seeded random generator. No row is derived from, perturbed
from or sampled out of a real case. The shape is only loosely realistic (seasonal filing
volume, a plausible disposition mix, a few pending and commercial cases), which is all an
adapter or a test needs.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import polars as pl

from sb38.schema import DISPOSITION_CLASSES

# Rough disposition mix, deliberately not any county's real figures.
_MIX = (
    ("default_judgment", 0.33),
    ("other_judgment", 0.17),
    ("dismissed", 0.17),
    ("nonsuit", 0.30),
    ("other", 0.03),
)
assert {name for name, _ in _MIX} == set(DISPOSITION_CLASSES)

_COURTS = ("Court A", "Court B", "Court C", "Court D", "Court E")


def synthetic_cases(
    n: int = 3_000,
    *,
    seed: int = 38,
    start: date = date(2023, 10, 1),
    end: date = date(2026, 5, 31),
    as_of: date = date(2026, 8, 26),
) -> pl.DataFrame:
    """Return ``n`` invented cases filed between ``start`` and ``end``, observed to ``as_of``."""
    rng = random.Random(seed)
    span = (end - start).days
    classes = [name for name, _ in _MIX]
    weights = [w for _, w in _MIX]
    rows = []
    for i in range(n):
        filed = start + timedelta(days=rng.randrange(span + 1))
        while filed.weekday() >= 5:  # courts take filings on weekdays
            filed -= timedelta(days=1)

        cls = rng.choices(classes, weights)[0]
        days = max(4, int(rng.lognormvariate(3.0, 0.35)))  # median around 20 days
        disposed = filed + timedelta(days=days)
        if disposed > as_of or rng.random() < 0.01:
            cls, disposed = None, None  # still pending when the data was pulled

        served = filed + timedelta(days=rng.randint(1, 12)) if rng.random() < 0.7 else None
        writ = None
        if cls == "default_judgment" and rng.random() < 0.45:
            writ = disposed + timedelta(days=rng.randint(6, 25))
        executed = (
            writ + timedelta(days=rng.randint(1, 20)) if writ and rng.random() < 0.6 else None
        )
        appeal = (
            disposed + timedelta(days=rng.randint(1, 8)) if cls and rng.random() < 0.05 else None
        )

        rows.append(
            {
                "case_id": f"SYN-{i:06d}",
                "court": rng.choice(_COURTS),
                "filed_date": filed,
                "is_residential": rng.random() > 0.01,
                "disposition_class": cls,
                "disposition_date": disposed,
                "county": "Synthetic",
                "judgment_type_raw": None if cls is None else cls.upper(),
                "defendant_count": rng.choices([1, 2, 3], [0.82, 0.15, 0.03])[0],
                "citation_served_date": served,
                "writ_issued_date": writ,
                "writ_executed_date": executed,
                "appeal_date": appeal,
            }
        )
    return pl.DataFrame(rows).sort("filed_date")


if __name__ == "__main__":
    from pathlib import Path

    out = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic_cases.csv"
    synthetic_cases().write_csv(out)
    print(f"wrote {out}")
