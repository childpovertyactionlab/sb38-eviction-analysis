"""The analysis scope, as written in the project's scoping document.

Topics, indicators and stats are copied verbatim from the scoping document, so the export and
the notebook line up one to one with it. Each stat names the measure that computes it, or
``None`` where nothing does yet. Changing the wording here means changing it in the scoping
document first.
"""

from __future__ import annotations

from dataclasses import dataclass

INTRO = (
    "JA and CPAL: Preliminary look at before and after trends. Do an initial analysis of "
    "eviction case trends in Harris/Dallas county. Use 2024 and 2025 as benchmarks for "
    "comparison. Compare month to month, e.g., Jan-May '24, Jan-May '25, and Jan-May '26. "
    "Capture the following (or however much is possible):"
)

NOTE = (
    "Note: # of cases for nonpayment of rent going up or down was inconclusive in both Dallas "
    "and Harris County due to data limitations."
)

NOT_COMPUTED = "Not computed in this version."


@dataclass(frozen=True)
class Stat:
    text: str
    measure: str | None  # key in sb38.measures / sb38.export, or None if not computed
    percent: bool = False


@dataclass(frozen=True)
class Topic:
    name: str
    indicator: str
    stats: tuple[Stat, ...]


TOPICS: tuple[Topic, ...] = (
    Topic(
        "“Squatters”",
        "Summary disposition",
        (Stat("# of cases that request summary disposition", None),),
    ),
    Topic(
        "Filing trends",
        "Filing records",
        (Stat("Number of filings", "filings"), Stat("filing rate", "filing_rate")),
    ),
    Topic(
        "Service of eviction notice",
        "Unclear. Likely timestamps in court records and/or information from eviction petition "
        "scans, constable records, etc.",
        (
            Stat("Average length of time from eviction filing to service of eviction notice", None),
            Stat("Median length of time from eviction filing to service of eviction notice", None),
            Stat("Share of eviction notices served by non-constable", None),
        ),
    ),
    Topic(
        "Default rate",
        "Judgment field",
        (Stat("Defaults as share of cases with outcomes", "default_rate", percent=True),),
    ),
    Topic(
        "Case length until judgment",
        "Filing and judgment timestamps",
        (
            Stat("Average length of time from filing to judgment", "mean_days_any_disposition"),
            Stat("Median case length from filing to judgment", "median_days_any_disposition"),
            Stat("Share of cases resolved within 30 days", "resolved_within_30_days", percent=True),
            Stat("Share of cases resolved within 60 days", "resolved_within_60_days", percent=True),
        ),
    ),
    Topic(
        "Immediate possession bonds",
        "Unclear",
        (
            Stat(
                "Cases where an immediate possession bond is filed as a share of all cases with "
                "case outcomes",
                None,
            ),
        ),
    ),
    Topic(
        "Writ service",
        "Timestamps for judgment + various writ stages. Possibly scrapes of scanned records "
        "(for non-constable service.)",
        (
            Stat("Average length of time from judgment to writ issuance", None),
            Stat("Median length of time from judgment to writ issuance", None),
            Stat("Average length of time from judgment to writ execution", None),
            Stat("Median length of time from judgment to writ execution", None),
            Stat("Average length of time from writ issuance to writ execution", None),
            Stat("median length of time from writ issuance to writ execution", None),
            Stat("Number and share of all cases where writ execution is recorded", None),
            Stat(
                "Number of cases with writ service performed by non-constables, and as a share "
                "of all cases where writs have been issued.",
                None,
            ),
        ),
    ),
    Topic(
        "Appeals",
        "Unclear",
        (
            Stat("Number and share of cases appealed", None),
            Stat("Average/median length of time from filing to judgment in appeals court", None),
            Stat(
                "Average/median length of time from judgment in JP court to judgment in appeals "
                "court",
                None,
            ),
        ),
    ),
    Topic(
        "Notice to pay rent or vacate",
        "Unclear",
        (Stat("#/% of nonpayment of rent cases that deliver notice to pay rent or vacate", None),),
    ),
)


def computed() -> list[tuple[Topic, Stat]]:
    """Every stat that has a measure behind it, in scoping-document order."""
    return [(t, s) for t in TOPICS for s in t.stats if s.measure]
