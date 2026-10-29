# Regression discontinuity prototype: analysis plan (Dallas County)

> **Draft prototype.** This is not part of the partner method in `docs/methods.md`, and nothing
> here is for citation. The plan is committed before any discontinuity is estimated on Dallas
> data. Changes made after the first estimate are logged under "Deviations" with a date and a
> reason.

## Question

Did SB 38 change how Dallas County eviction cases end, and how fast, for cases filed on or after
January 1, 2026?

## Why a discontinuity

SB 38 §17 applies the new law only to suits whose petition is filed on or after January 1, 2026.
Treatment is assigned by filing date alone. A case filed December 30 and one filed January 2 go
through the same courts in the same weeks under different law, so a jump in outcomes at the
cutoff is evidence of the law's effect in a way a year-over-year comparison is not.

A plain discontinuity at January 1 has three problems. The holidays move filings and court
calendars at every New Year, an outcome shift already happened between the 2024 and 2025
cohorts, and one county's volume (about 140 residential filings per weekday) only detects
large jumps. The design below handles each.

## What has already been seen

- The January–May comparisons in `results/dallas.csv` (release v0.1.0).
- Weekly filing counts around January 1 for each year: no surge before the 2026 cutoff, and a
  collapse in the week of the January 2026 court closure.
- A first look at the statutory clocks by cohort.

No discontinuity estimate, event-study coefficient or placebo distribution has been computed.

## Sample

- Residential eviction cases in the Dallas County case table, one row per case.
- Filing dates from April 1, 2023 to the data date less 60 days.
- Three cutoffs: **January 1, 2026** (treated) and **January 1, 2024 and 2025** (placebo
  cutoffs with the same calendar, no change in law).
- **Holiday donut:** cases filed December 20 to January 7 around each cutoff are left out. In
  days from the cutoff, that is −12 to +6.
- **Court closures:** cases filed on a day the courts were closed are left out. Dallas County
  closed its courts January 9–10, 2025 and around January 26–28, 2026. The 2026 dates are not
  confirmed and will be updated when they are.

## Running variable and outcomes

The running variable is days from January 1 of the cutoff year to the filing date.

Every outcome is measured on every filed case, inside a fixed window from filing, so no case is
dropped for lacking an outcome and every case near every cutoff has complete follow-up. A
window that contains a court-closure day is extended by the number of closure days in it.

**Primary outcomes** (Holm correction across these four):

1. Default judgment within 60 days of filing.
2. Any judgment (default or other) within 60 days of filing.
3. Dismissal or nonsuit within 60 days of filing.
4. Any disposition within 21 days of filing, the latest trial day SB 38 allows.

**Secondary outcomes** (reported, no correction):

- Any disposition within 30 and 60 days (the scoping document's measures).
- Default judgment as a share of cases with a disposition within 60 days (the scoping
  document's denominator).
- Judgment within 9 days of filing, before the earliest trial day SB 38 allows.

**Exploratory outcomes**, held until the definitions of the writ and appeal fields are
confirmed with the court:

- Writ of possession issued within 90 days of filing.
- Appeal recorded within 60 days of filing.
- Writ issued 0–5 days after a judgment, a proxy for an immediate possession bond.

## Estimation

- At each cutoff, local linear regression on each side, triangular kernel, **bandwidth 30
  days**, with robust bias-corrected 95% confidence intervals (`rdrobust`, mass points
  adjusted, standard errors clustered by filing date). Thirty days keeps every window clear of
  Thanksgiving week in all three years. After the donut it leaves roughly 12 filing weekdays
  before the cutoff and 17 after, and those days are the clusters behind each standard error.
  That is thin, which is why the placebo dates and the event study carry weight.
- **Main estimate, the difference in discontinuities:**
  τ(2026) − ½[τ(2024) + τ(2025)]. The three samples are disjoint, so its standard error is
  √[se(2026)² + ¼(se(2024)² + se(2025)²)].
- **Bandwidth sensitivity:** 45 and 60 days, with Thanksgiving week dropped from both. A
  narrower bandwidth leaves too few filing days before the donut (21 days leaves about six),
  and the MSE-optimal selector doesn't account for the donut and picks bandwidths inside it, so
  neither is used.
- **Minimum detectable effect:** 2.8 × the standard error of the main estimate (80% power, 5%
  two-sided), reported beside every estimate. A null result is reported as "no change larger
  than the MDE".

## Checks on the design

- **Placebo cutoffs.** The 2024 and 2025 discontinuities are reported on their own. If either
  is large, the calendar is doing something the donut doesn't remove, and the 2026 estimate is
  not interpreted until that is explained.
- **Placebo dates.** The same local linear estimate (conventional, same bandwidth, a symmetric
  donut of the same width) at every weekday from July 1, 2023 to October 31, 2025 whose window
  stays clear of November 15 – January 15 and of court closures. Two distributions: the
  discontinuity at each date, and the difference between a date and the same date a year
  earlier. Report where the 2026 estimate and the main estimate fall in each.
- **Filing volume.** Weekday filing counts, log scale, local linear on each side of each cutoff
  with weekday indicators, differenced the same way. A surge before the cutoff (landlords
  filing early to stay under the old law) or a shortfall after it would show here.
- **Composition.** The same difference in discontinuities on the number of defendants, and on
  a predicted-outcome index for each primary outcome (a linear model of the outcome on court
  and number of defendants, fitted on cases filed in the 2024 and 2025 cohorts).

## Event study (secondary)

Weekly filing cohorts from October 1 to the end of the sample in each season (2023–24,
2024–25, 2025–26), aligned on January 1. A case-level linear probability model of each primary
outcome on week-from-January-1 and season indicators, court indicators, and interactions of the
2025–26 season with each week. The reference is December 11–17, the last full week before the
holiday donut, because the week just before January 1 is thin and holiday-shaped in every
season. Standard errors clustered by court and filing week. (Clustering by filing week alone
leaves each 2025–26 coefficient resting on a single cluster, which understates its standard
error.) The pre-January coefficients test whether the 2025–26 season tracked
the earlier seasons before the law changed.

## Not in this prototype

- Synthetic control or out-of-state comparisons. No comparable outcome data exist.
- A difference-in-differences on court-level exposure. It needs more assumptions than the
  hearing timeline allows.
- Competing-risks survival models for case length. Possible later; the fixed windows above
  answer the same question for this purpose.

## Deviations

None yet.
