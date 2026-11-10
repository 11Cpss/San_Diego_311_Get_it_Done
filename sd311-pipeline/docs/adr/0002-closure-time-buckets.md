# ADR 0002: Predict closure-time buckets, not days

**Status:** accepted (2026-10-08)

## Context
v1 regressed `log1p(days)` and reported MAE. Two problems:
1. Requests that are still open have no `days` value. A regression target forces you to either
   drop them (biased: you drop the slowest cases) or invent a value.
2. The distribution is extremely skewed (most close in 0-2 days, some take years), so MAE is
   dominated by a few huge cases and is hard to explain to a resident.

## Decision
Predict one of four buckets: `0-2`, `3-7`, `8-30`, `31+` days, where
`days = date_closed - date(date_requested)` in whole days.

Labels are assigned **as of** a date (`2025-12-31`, see `sd311_core/labels.py`):
- closed on or before as_of: exact bucket;
- still open (or closed after as_of) and at least 31 days old at as_of: `31+`;
- otherwise unlabeled and excluded (we genuinely do not know yet).

## Consequences
- Open, slow requests are kept as `31+` instead of being dropped. This is the main fix.
- `31+` merges "5 weeks" and "3 years". We cannot say *how* slow a slow case will be.
- Requests younger than 31 days at as_of cannot be used for training or evaluation. Every
  train/test split needs a 31-day embargo before the period it is scored on.
- Metrics change to classification metrics (macro-F1, log-loss, per-class recall), which are not
  comparable to v1's MAE numbers.
