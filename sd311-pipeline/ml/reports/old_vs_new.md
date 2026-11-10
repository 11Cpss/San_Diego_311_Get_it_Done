# Old (v1) vs new evaluation population

* v1 test set (closed by 2025-12-31, requested >= 2025-07-01, case_age <= 730): **157,431** rows.
* v1 baselines reproduced: global train median (4 days) MAE **9.71**, per-service train median MAE **25.93**.
* All cleaned H2-2025 requests: **180,360**. Missing from the v1 test set: **22,940 (12.7%)**, of which 22,940 are still open in the snapshot.

Bucket shares for requests submitted in Q3-2025 (all of these have a final label at 2025-12-31):

| bucket | v1 truncated test | proper labels |
|---|---|---|
| 0-2 | 50.9% | 46.7% |
| 3-7 | 18.3% | 16.8% |
| 8-30 | 16.4% | 15.0% |
| 31+ | 14.4% | 21.4% |

January-gap analog: of requests submitted Jan-Sep 2024, 0.23% (711) closed between 2025-01-01 and 2025-01-14. The same kind of request from Jan-Sep 2025 is missing from both files, so our test sets probably lack a similar small share of (mostly 31+) cases.
