-- LESSON 7 STUB: you write ml_labels (lessons/07_dbt_models_and_tests.md).
-- Until you do, this returns the right COLUMNS with ZERO rows, so `dbt build`
-- (and CI) stay green while you work. Delete this whole comment block, including
-- the marker line above, when you write the model.
--
-- Contract: one training label per request whose outcome is FINAL at as_of_date.
-- Input: ref('requests_clean'). as_of_date = macro as_of_date_sql() (given:
-- the `as_of` var if set, else the latest open snapshot date).
--
-- TODO 1  Only requests filed on or before as_of_date.
-- TODO 2  days_to_close = whole days from date_requested_day to date_closed, but ONLY
--         if the request closed on or before as_of_date. A request closed AFTER
--         as_of_date was still open at as_of_date: treat it as open (no future info).
--         Hint: the dbt.datediff(start, end, 'day') macro works on DuckDB and Databricks.
-- TODO 3  age_at_as_of = whole days from date_requested_day to as_of_date.
-- TODO 4  Keep a row only if its label is final:
--           closed by as_of_date                     -> bucket from days_to_close
--           still open at as_of_date, age >= 31 days -> '31+' (can't land lower)
--           still open at as_of_date, age <  31 days -> EXCLUDED (unknown yet)
--         Buckets: 0-2, 3-7, 8-30, 31+ (macro closure_bucket(days), given).
-- TODO 5  is_censored_31_plus = true for the "still open, 31+" rows.
-- tests/assert_no_label_for_young_open_requests.sql guards rule 4.
-- Check: python scripts/lessons.py check 7
select
    service_request_id,
    date_requested_day,
    cast(null as date)                      as as_of_date,
    cast(null as integer)                   as days_to_close,
    cast(null as integer)                   as age_at_as_of,
    cast(null as {{ dbt.type_string() }})   as label_bucket,
    cast(null as boolean)                   as is_censored_31_plus
from {{ ref('requests_clean') }}
where 1 = 0
