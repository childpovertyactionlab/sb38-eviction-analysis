# Methods

> Draft ver. 0.1

This document describes methods implemented by the codebase found in `src/sb38/`.

## Goal of analysis

Texas Senate Bill 38 (89th Legislature, 2025) changed eviction procedure in the justice courts.
Section 17 of the Act says its changes "apply only to an eviction suit in which the petition is
filed on or after January 1, 2026," so any earlier suits remain unaffected.

The analysis in this repo describes how eviction cases filed under the new law compare with cases filed in the
same months of the two prior years. It reports what changed between those groups of cases. It
does not, by itself, separate the effect of SB 38 from anything else that changed over the
same period, such as court staffing, local practice or the rental market.

## Eviction cases included in analysis

- Residential eviction cases filed in a county's justice courts are included, but commercial eviction cases
  are excluded.
- One count per case, so a case naming several tenants is only counted once.
- A predetermined analysis window filters cases by filing date, such that a case still pending when the
  data were pulled stays in the analysis with no disposition. (Leaving pending cases out would
  make every timing figure look faster than it is.) We use filing date for the window filter to match SB 38's own language.

## Cohorts and comparisons

We use "cohort" to describe a set of cases files in a single analysis window. A case filed on December 30, 2025 and
decided in January 2026 belongs to 2025.

| Cohort | Filed | Law |
|---|---|---|
| 2024 | Jan 1 – May 31, 2024 | before SB 38 |
| 2025 | Jan 1 – May 31, 2025 | before SB 38 |
| 2026 | Jan 1 – May 31, 2026 | SB 38 |

2024 and 2025 exist as benchmarks, and we report findings in two ways:

- Jan–May cohort total, per year, and;
- Month by month, where cases filed in January 2024, January 2025 and January 2026 would be compared side by side, and likewise
  through May.

Changes are reported against each benchmark year separately (2026 against 2025, and 2026 against 2024).

These are counts of every case, so the figures carry no sampling error, and
differences are reported as observed. Whether a change is larger than normal year-to-year movement is a separate question, left unanswered as of now.

## Date cut-offs

Every analyis output will carry the date its data were pulled.

- The 60-day figures below need data pulled on or after July 30 of the cohort year (60 days after May 31).
- A case still pending on the date pulled doesn't count as resolved within 30 or 60 days if  those days have passed. If they haven't, the figure isn't computed.

## Disposition classes

Courts record how a case ended in their own vocabulary, so any new dedicated county adapter script must map its labels
onto five classes:

| Class | Meaning |
|---|---|
| `default_judgment` | Judgment for the landlord because the tenant did not appear |
| `other_judgment` | Any other judgment: after a trial, agreed, or on a motion |
| `dismissed` | Dismissed by the court, including for want of prosecution |
| `nonsuit` | Withdrawn by the landlord |
| `other` | Transferred, set aside, post-judgment entries, or not classifiable |

A case with no class would have no recorded disposition yet. Each county's mapping, with a note on every label, is designed to live in in `config/<county>/`.

> A judgment recorded as a default *for the tenant* is improper record keeping by JP Courts and thus is not counted as a default. Practitioners in Dallas County treat that record as the landlord failing to appear, which is a dismissal for want of prosecution.

## Analysis results

### Filings

Filings reports the number of cases in the cohort, reported as the Jan–May total and by month, with the percentage change against each benchmark year.

### Filing rate

Filing rate reports filings per 1,000 renter-occupied households in the county, using the Census Bureau's American Community Survey (ACS) 1-year estimate of renter-occupied households (table B25003).

- Year-matched: each year's filings are divided by renter households estimated by the Census Bureau for that same calendar year. Where the matching estimate isn't published yet (2026, as of late 2026), the most recent estimate will be used and labelled as such.
- Not annualised: a Jan–May rate is five months of filings over an annual household count, and a monthly rate is one month's.

### Default rate

Default rate reports the share of cases with a recorded disposition whose class is `default_judgment`.

### Case length

Case length reports calendar days from filing to disposition, in addition to:

- Average and median days among cases with a recorded disposition.
- Share resolved within 30 days and within 60 days out of all cases filed in the cohort, pending cases included.

"Resolved" means any disposition including a judgment, a dismissal or a nonsuit, whichever ended the  case. Nonsuits and dismissals can end a case on a different clock.

## Events after judgment

This rule applies to topics still under assessment. It is stated here because it decides
whether cohorts can be compared at all.

Writs of possession and appeals come after judgment, so a case filed in the window can have its
later events land after the data stop. If one year's data were pulled sooner after judgment
than another's, that year shows fewer writs and appeals simply because there was less time to
record them.

**The rule.** When cohorts are compared, an event after judgment counts only if it is dated on
or before **June 9 of the cohort's year**. The same cut applies to every year. June 9 is when the
earliest of the Dallas County deliveries was pulled. Rates under this rule are labelled
"recorded by June 9" and run lower than complete-follow-up rates. They are comparable across
years, which complete-follow-up rates are not.

A county whose data have no such constraint can still apply the same cut, which keeps its
figures comparable with Dallas.

## Known disruptions

- **Winter Storm Fern:** Some courts closed in late January 2026, around January 26–28. Filings for that week, and the timing of cases filed in the weeks before it, are affected. January 2026 figures are flagged.
- **Holidays:** Court holidays fall on different weekdays each year, which affects month-by-month comparisons of timing.

## TBD (Under construction)

- Summary disposition requests
- Service of the citation
- Immediate possession bonds
- Writ issuance and execution
- Appeals
- Notice to pay rent or vacate

## Applying this method to a new county

1. Write an adapter script that produces the case table in [`schema.md`](schema.md).
2. Map the county's outcome labels onto the five disposition classes in `config/<county>/`.
3. Record the date the data were pulled.
4. Check the table with `sb38 validate <file>`.
