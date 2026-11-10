# ADR 0003: Exclude `service_name_detail` from features

**Status:** accepted (2026-10-08)

## Context
`service_name_detail` (e.g. `DAMAGED CURB`, `SIDEWALK MINOR REHAB CONTRACT`) was v1's #2 feature.
The raw comparison looked alarming: it is missing for ~69% of the newest open requests but only
~37% of closed test rows, which suggested it is filled in by staff after submission.

`analysis/field_availability.py` repeats the comparison after reweighting the closed rows to the
same **service mix** as the newest open rows (`reports/field_availability.md`):

| | newest open (3 days) | closed, same service mix |
|---|---|---|
| detail missing | 68.8% | 68.9% |

So most of the raw gap is explained by *which services* are in each group, not by the field being
filled in later. The missingness test does **not** show leakage. It also cannot rule it out:
staff may still *change* the value (for example to a contract or crew type that encodes the
resolution path), and some values read like work-order categories rather than what a resident
would choose.

## Decision
Keep it excluded from v2.
- The cost of a leaked feature, a model that looks good offline and fails in production, is larger
  than the cost of losing some honest signal.
- The daily pipeline scores requests minutes after submission, when the field is often empty.

## Consequences / follow-up
- v2 numbers are slightly conservative.
- To revisit: snapshot new requests daily for a few weeks (the new pipeline can do this) and check
  whether `service_name_detail` *values* change between first sighting and closure. If they don't,
  add it back and run the backtest with and without it.
- `case_record_type` and `is_followup` were checked the same way and kept: case_record_type is
  never missing and has the same per-service distribution; follow-ups are *more* common among the
  newest open requests (22% vs 10-12%), the opposite of what a later-filled field would show.
- `desc_len` showed an odd pattern (zero-length descriptions are much more common among newest open
  requests, driven mostly by `ESD Collections`). It is kept but flagged as a caveat in RESULTS.md.
