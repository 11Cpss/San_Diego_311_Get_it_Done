-- LESSON 7 STUB: you write requests_clean (lessons/07_dbt_models_and_tests.md).
-- Until you do, this returns the right COLUMNS with ZERO rows, so `dbt build`
-- (and CI) stay green while you work. Delete this whole comment block, including
-- the marker line above, when you write the model.
--
-- Contract: one row per service_request_id, the latest known state, cleaned.
-- Inputs: ref('stg_open_snapshots') and ref('stg_closed_requests') (same columns).
--
-- TODO 1  Use only the LATEST open snapshot (max snapshot_date), plus every closed row.
-- TODO 2  Stack them and keep ONE row per id. A closed record beats an open one
--         (closed is final); between two rows of the same kind, the newer
--         snapshot_date wins, then the newer _ingested_at.
--         Hint: row_number() over (partition by ... order by ...), keep rn = 1.
-- TODO 3  THEN drop: status 'Referred', service_name 'Other', negative
--         case_age_days (a NULL case_age_days is kept). Think about why the
--         drops must come after step 2 (lesson: "order matters").
-- TODO 4  Normalise:
--           service_name    -> macro clean_service_name('service_name') (given)
--           comm_plan_name  -> title case (macro title_case(col), given), NULL -> 'Unknown'
--           case_origin     -> '' or NULL -> 'Unknown'
--           council_district NULL -> 0
--           is_closed       -> true if the winning row came from the closed file
--           date_requested_day = date_requested as a date
--           source_snapshot_date = snapshot_date of the winning row
-- Macros are called with double curly braces, as in the select below.
-- Keep the column names and order below: gold, ml and the Databricks tasks read them.
-- Check: python scripts/lessons.py check 7
select
    service_request_id,
    service_request_parent_id,
    date_requested,
    cast(date_requested as date)    as date_requested_day,
    case_age_days,
    case_record_type,
    service_name,
    service_name_detail,
    date_closed,
    status,
    true                            as is_closed,
    lat,
    lng,
    zipcode,
    council_district,
    comm_plan_code,
    comm_plan_name,
    case_origin,
    desc_len,
    snapshot_date                   as source_snapshot_date,
    source
from {{ ref('stg_closed_requests') }}
where 1 = 0
