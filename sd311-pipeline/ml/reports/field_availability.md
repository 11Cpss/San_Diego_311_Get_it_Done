# Field availability (extract date 2026-01-14)

Closed comparison group: closed requests submitted 2025-07-01 or later, reweighted to the service mix of each open group.

A second comparison group keeps only closed requests that took >= 3 days, because a request that is closed within a day or two can never show up in the open file (survivor bias).

| field | open, last 3 days | closed, same mix | closed >=3d, same mix | open, last 14 days | closed, same mix |
|---|---|---|---|---|---|
| service_name_detail missing | 68.8% (n=3,394) | 68.9% | 68.9% | 72.7% (n=9,489) | 73.0% |
| case_record_type missing | 0.0% (n=3,394) | 0.0% | 0.0% | 0.0% (n=9,489) | 0.0% |
| parent id present (is_followup) | 22.4% (n=3,394) | 9.8% | 12.1% | 18.1% (n=9,489) | 11.4% |
| desc_len == 0 | 26.0% (n=3,394) | 10.9% | 2.9% | 23.7% (n=9,489) | 7.8% |

Share of rows whose case_record_type equals the service's usual type: open-14d 98.3% vs closed (same mix) 98.3%.
