# Case schema

In the case table design, one observation is one eviction case. Check a table for fit with:

```
sb38 validate path/to/cases
```

## Required columns

These columns must be present.

| Column | Type | Empty allowed | Meaning |
|---|---|---|---|
| `case_id` | text | no | A unique case identifier. |
| `court` | text | no | The JP court the case was filed in. |
| `filed_date` | date | no | Date the petition was filed. |
| `is_residential` | boolean | no | True for residential evictions, so that commercial cases are excluded from the end analysis. |
| `disposition_class` | text | yes | `default_judgment`, `other_judgment`, `dismissed`, `nonsuit` or `other`. If pending, this remains empty. |
| `disposition_date` | date | yes | Date of the judgment, dismissal or nonsuit that ended the case. If pending, this remains empty. |

For the purpose of the analysis scripts, include every case filed in the specified window of analysis, including cases with no disposition yet.

## Optional columns

| Column | Type | Meaning |
|---|---|---|
| `county` | text | County name. |
| `judgment_type_raw` | text | A raw outcome label to be mapped to a disposition class. |
| `defendant_count` | integer | Number of named defendants. |
| `citation_served_date` | date | Earliest date the citation was served on any defendant. |
| `writ_issued_date` | date | Date a writ of possession was issued. |
| `writ_executed_date` | date | Date the writ was executed. |
| `appeal_date` | date | Date an appeal was recorded. |

## What the `validate` CLI function checks

| Check | Level |
|---|---|
| Required columns present | error |
| Values readable as their type | error |
| No empty values where they aren't allowed | error |
| One row per `case_id` | error |
| Every `disposition_class` is one of the five classes | error |
| Class and date present or empty together | warning |
| No event dated before `filed_date` | warning |

`validate` also prints a description of the table (rows and cols).

## Test data

`fixtures/synthetic_cases.csv` is 6,000 invented cases as fake test data (`python -m sb38.fixtures`). Its intended use is for testing an adapter script or running the package end to end before real data has been fitted.
