# Historical Pattern / Anomaly Detection — Design

**Date:** 2026-11-03
**Branch:** `evan-test`
**Status:** Implemented (CPSC 491 Week 7 — "Pattern detector integrated")

## Problem

Every existing check looks at one receipt in isolation (policy rules) or only
at image similarity (pHash). Nothing compares a receipt with the business's
own history, so two kinds of fraud slip through:

- an employee spending far more in a week or month than they normally do
  (one large receipt, or many ordinary ones), and
- the same expense claimed twice — by two employees splitting one dinner, or
  by one employee re-photographing a receipt (a new photo defeats pHash).

Businesses now have several members, each submitting their own receipts, so
a business's receipt history is the natural baseline.

## Goal

A detector that compares each receipt with the business's earlier receipts,
produces explainable flags, and plugs into the existing pipeline — without
raising false positives on live scores before the thresholds are tuned
(Week 8).

## Non-goals

- Model-based detection (Isolation Forest etc.) — Week 12, once there is
  labelled history to train and evaluate on.
- A rule-editing API or UI; thresholds are edited in `policy_rules` for now.
- Dashboard views of anomaly signals — Weeks 8–9.

## Decisions

| Question | Decision |
|---|---|
| Whose history? | The **submitting user** (`submitted_by_user_id`) within the **business**. Every member submits their own receipts. Nothing crosses businesses. `employee_id` is unused by uploads and not relied on. |
| Signals | `amount_outlier` and `content_match` (below). |
| Outlier period | Spend per **calendar week** (Monday–Sunday) and per **calendar month**, by transaction date — two rules, so two separate flags, each configured and shadowed on its own. |
| Outlier baseline | The employee's own other weeks/months; employees with too little history are compared with every employee's weeks/months in the business. |
| Content-match scope | Any submitter. The flag says whether the match is another employee's or the same employee's. |
| Effect on score | **Shadow mode**: flags are stored and shown but don't score or change status until a rule is switched on. |
| Configuration | Per business, as new rule types in `policy_rules`, seeded with the defaults. No migration. |
| Statistics | Median / MAD robust z-scores — interpretable, and one past outlier can't mask the next. |
| Which history counts | Only receipts submitted **before** this one, so re-analyzing later gives the same result. Rejected receipts are excluded from the amount baseline but still count as content-match candidates. |

## Signals

### `amount_outlier` — "Unusual weekly / monthly amount for employee"

Two rules of this type, `"period": "week"` and `"period": "month"`. For a
receipt dated in a given calendar week or month, `x` is the employee's spend
for that period: this receipt plus their earlier, non-rejected receipts dated
in it. The baseline is the employee's totals for their other periods
(periods with no receipts don't count; most recent 500). Robust z-score
`0.6745 · (x − median) / MAD`; fires when **both**:

- z ≥ `threshold` (3.5, the Iglewicz–Hoaglin cutoff), and
- x ≥ `min_ratio` × median (2.0) — stops a $15 week being flagged against a
  history of near-identical $5 weeks.

With fewer than `min_history` (5) other periods, every employee's periods in
the business are the baseline; with fewer than `min_company_history` (10)
there, it doesn't fire. If MAD is 0, mean absolute deviation is used instead.
Confidence 75 (employee baseline) or 60 (business baseline). Only unusually
high spend is flagged, and undated receipts are skipped.

Each receipt that leaves the period over the line is flagged, so a heavy week
shows on every receipt submitted after it crossed. The weekly rule catches a
burst; the monthly one catches steady overspending that no single week shows
(e.g. ~1.8× a usual week, every week). `details` carries `period`,
`period_start` and `period_total` alongside the statistics.

### `content_match` — "Matching receipt in business"

An earlier receipt in the business with the same `transaction_date`, the same
total to the cent, and a merchant name where one normalized name contains the
other (OCR of two photos rarely reads a name identically). One flag per match,
confidence 90, `details.same_submitter` distinguishing the two cases. Receipts
the pHash detector already flagged as image duplicates are skipped, so one
resubmission isn't flagged twice on the same evidence.

Both emit `flag_type="anomaly"` with `details.signal` naming the rule type.

## Shadow mode

`details.shadow = true` is set on each flag from a rule whose parameters don't
say `"shadow": false` (missing means shadowed). Shadow flags:

- are saved and returned like any flag, and labelled "shadow" in the receipt
  detail view;
- add 0 to the fraud score (`fraud_scorer.is_shadow`) and don't move the
  receipt to `flagged`;
- are left out of the Gemini explanation, which explains the score.

Reviewers keep approving and rejecting as usual. A shadow flag on a receipt
later approved is a likely false positive, on one rejected a likely true
positive, which gives Week 8 a false-positive rate per signal before it counts.

To promote a signal for one business, set `"shadow": false` on its rule row.
`("anomaly", severity)` base scores (50/30/15, mirroring policy violations)
then apply; they are placeholders for Week 8 to tune.

## Components

| File | Change |
|---|---|
| `app/services/anomaly_detector.py` | New. `detect_anomalies(receipt, db, exclude_receipt_ids)` runs the business's active `amount_outlier` / `content_match` rules. Period totals are grouped in Postgres with `date_trunc`. |
| `app/services/fraud_detection_pipeline.py` | Calls the detector after policy validation, passing pHash-duplicate ids; status is `flagged` only for non-shadow flags. |
| `app/services/fraud_scorer.py` | `is_shadow(flag)`; shadow flags ignored; `anomaly` base scores. |
| `app/services/policy_validator.py` | Skips anomaly rule types. `short_window_duplicate` now matches on the submitter instead of `employee_id`, which uploads never set, so the rule had never fired. |
| `app/services/policy_defaults.py` | Three new default rules (9 total: weekly and monthly amount, content match), all shadowed. |
| `app/api/v1/receipts.py` | `/explain` uses only scored flags. |
| `frontend/src/pages/ReceiptDetail.tsx` | "shadow" label, signal name (with "weekly"/"monthly"), link to the matching receipt; flag count and "Why this score?" use scored flags. |

Existing businesses get the new rules with
`python seed_policies.py --tenant-id <id>` (adds only missing rules, by name).

## Testing

`tests/test_services/test_anomaly_detector.py` (DB-backed): weekly and
monthly totals, the two rules firing independently (a heavy week, a
steadily high month, both, neither), Monday–Sunday weeks, employee and
business baselines, minimum history, rejected and later receipts excluded,
the no-spread ratio guard, undated receipts, a missing `period`,
content-match field requirements, rejected
matches, excluded ids, tenant isolation, shadow default, inactive rules, and
the pipeline/scorer/explain behavior of shadow flags. The short-window tests
now key on the submitter.

## Week 8 follow-ups

- Measure each signal's false-positive rate from reviewer decisions on shadowed
  receipts, then tune `threshold` / `min_ratio` / `min_history` and promote.
- Tune the `anomaly` base scores before promotion.
- The monthly rule needs 5 months of an employee's history before it uses
  their own baseline, so early on it compares with the business; consider a
  lower `min_history` for it.
- The repaired short-window rule is live and overlaps with pHash for an exact
  resubmission (both fire, so 75 becomes 95). Consider skipping pHash
  duplicates there too, as content matching does.
