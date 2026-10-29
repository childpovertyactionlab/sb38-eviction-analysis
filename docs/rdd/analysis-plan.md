# SB 38 effects prototype: analysis plan (Dallas County)

> **Draft prototype.** This is not part of the partner method in `docs/methods.md`, and nothing
> here is for citation. The plan is committed before any estimate is run on Dallas data.
> Revisions made before that point are listed at the end. Changes made after the first
> estimate are logged under "Deviations" with a date and a reason.

## Question

Did SB 38 change how Dallas County eviction cases end, and how fast, for cases filed on or after
January 1, 2026?

## Design

SB 38 §17 applies the new law only to suits whose petition is filed on or after January 1, 2026.
Treatment is assigned by filing date alone, which gives two ways in:

- **Seasonal difference (main estimate).** Cases filed after January 1, 2026 are compared with
  cases filed in the same weeks of the two prior seasons, after removing each season's own
  October–December level. Holidays, the court calendar and ordinary seasonality fall on the
  same weeks every year and cancel out. An outcome shift that was already in place before
  January, such as the one between the 2024 and 2025 cohorts, is absorbed by the season's level.
- **Discontinuity at January 1 (check).** Cases filed just before and just after January 1,
  2026, with the same comparison at January 1, 2024 and 2025 to net out the holidays. This asks
  whether there was a sharp jump on the day the law changed. With one county's volume and the
  holiday weeks removed, it can only detect very large jumps, so it is reported as a check with
  its minimum detectable effect.

The seasonal difference rests on one assumption: without the law, the 2025–26 season would have
followed the same week-by-week pattern as the two seasons before it. The October–December weeks
test that before the law took effect.

## What has already been seen

- The January–May comparisons in `results/dallas.csv` (release v0.1.0).
- Weekly filing counts around January 1 for each year: no surge before the 2026 cutoff, and a
  collapse in the week of the January 2026 court closure.
- A first look at the statutory clocks by cohort.

No estimate in this plan has been computed on Dallas data.

## Sample

- Residential eviction cases in the Dallas County case table, one row per case.
- **Seasons:** cases filed October 1 to May 31 of 2023–24, 2024–25 and 2025–26. Each is aligned
  on its own January 1, in weeks: week 0 is January 1–7.
- **Court closures:** cases filed on a day the courts were closed are left out. Dallas County
  closed its courts January 9–10, 2025 and around January 26–28, 2026. The 2026 dates are not
  confirmed and will be updated when they are.
- The discontinuity check also uses filing dates from April 1, 2023 for its placebo dates, and
  leaves out a **holiday donut** of December 20 to January 7 around each cutoff.

## Outcomes

Every outcome is measured on every filed case, inside a fixed window from filing, so no case is
dropped for lacking an outcome and every case in the sample has complete follow-up. A window that
contains a court-closure day is extended by the number of closure days in it.

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

## Main estimate: seasonal difference

- A case-level linear probability model of each outcome on an indicator for "filed in the
  2025–26 season, on or after January 1", with week-from-January-1, season and court indicators.
  The coefficient on that indicator is the estimate.
- Standard errors clustered by court and filing week.
- **Minimum detectable effect:** 2.8 × the standard error (80% power, 5% two-sided), reported
  beside every estimate. A null result is reported as "no change larger than the MDE".
- **Sensitivity:** the same estimate with the January 2026 storm-affected cases left out
  (filed January 5–28, 2026, whose 21-day window reaches the closure), and with the 2023–24
  season dropped.

## Checks on the seasonal difference

- **Pre-January tracking.** The event study below gives a 2025–26 coefficient for every week.
  The weeks before December 11 are tested jointly for zero. If they reject, the main estimate
  isn't interpreted until the reason is found.
- **Placebo season.** The same estimate with 2024–25 as the "treated" season and 2023–24 as the
  comparison. No law changed on January 1, 2025, so a large placebo estimate means post-January
  shifts happen anyway, and the main estimate is read against it.
- **Filing volume.** Weekly residential filing counts, log scale, on week and season indicators
  and the same post-January 2026 indicator. A surge before January (landlords filing early to
  stay under the old law) or a shortfall after it would show here and in the event study.
- **Composition.** The same estimate on the number of defendants, and on a predicted-outcome
  index for each primary outcome (a linear model of the outcome on court and number of
  defendants, fitted on cases filed January–May of 2024 and 2025).

## Event study

The picture behind the main estimate. Same sample and model, with the 2025–26 indicator split
into one interaction per week. The reference is December 11–17, the last full week before the
holidays, because the week just before January 1 is thin and holiday-shaped in every season.
Standard errors clustered by court and filing week. (Clustering by filing week alone leaves each
2025–26 coefficient resting on a single cluster, which understates its standard error.)

## Discontinuity check

- At each January 1, local linear regression on each side, triangular kernel, bandwidth 30
  days, robust bias-corrected confidence intervals (`rdrobust`, mass points adjusted, standard
  errors clustered by filing date). After the donut that leaves roughly 12 filing weekdays
  before the cutoff and 17 after.
- The difference in discontinuities, τ(2026) − ½[τ(2024) + τ(2025)], with standard error
  √[se(2026)² + ¼(se(2024)² + se(2025)²)] and its minimum detectable effect.
- Bandwidths of 45 and 60 days, Thanksgiving week dropped, as sensitivity.
- Placebo dates: the same conventional estimate at every weekday from July 1, 2023 to October
  31, 2025 whose window stays clear of November 15 – January 15 and of court closures, and the
  change from the same date a year earlier. Report where the 2026 estimate falls.

## Not in this prototype

- Synthetic control or out-of-state comparisons. No comparable outcome data exist.
- A difference-in-differences on court-level exposure. It needs more assumptions than the
  hearing timeline allows.
- Competing-risks survival models for case length. Possible later; the fixed windows above
  answer the same question for this purpose.

## Revisions before any Dallas estimate

All made on 2026-10-29, after testing the code on invented data at Dallas volume and before
running anything on Dallas data.

- **The main estimate moved from the difference in discontinuities to the seasonal
  difference.** With a planted 8-point effect, the discontinuity as first planned had a minimum
  detectable effect of about 69 points (20 points even at a 60-day bandwidth). The holiday donut
  leaves about 13 filing days on each side and forces a two-week extrapolation to the cutoff.
  The seasonal difference recovered the planted effect with a minimum detectable effect of about
  2 points. The discontinuity stays as a check.
- **Event-study standard errors are clustered by court and filing week,** not filing week alone
  (see above).
- **Discontinuity bandwidth sensitivity is 45 and 60 days.** Twenty-one days left about six
  filing days before the donut, and the MSE-optimal selector picks bandwidths inside the donut.

## Deviations

None yet.
