-- requests_clean: one row per service request, latest known state, cleaned.
--
-- Inputs: the LATEST open snapshot + every closed record.
-- Order of operations matters:
--   1. union open + closed, 2. dedupe ids preferring closed (closed is final),
--   3. THEN apply the drop filters (so a request that closed as 'Referred'
--      is dropped instead of falling back to its stale open version),
--   4. normalise text columns.

with latest_open as (
    select *
    from {{ ref('stg_open_snapshots') }}
    where snapshot_date = (select max(snapshot_date) from {{ ref('stg_open_snapshots') }})
),

unioned as (
    select *, 0 as is_closed_record from latest_open
    union all
    select *, 1 as is_closed_record from {{ ref('stg_closed_requests') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by service_request_id
            order by is_closed_record desc,   -- closed beats open
                     snapshot_date desc,      -- then the newest file
                     _ingested_at desc
        ) as rn
    from unioned
)

select
    service_request_id,
    service_request_parent_id,
    date_requested,
    cast(date_requested as date)                                  as date_requested_day,
    case_age_days,
    case_record_type,
    {{ clean_service_name('service_name') }}                      as service_name,
    service_name_detail,
    date_closed,
    status,
    is_closed_record = 1                                          as is_closed,
    lat,
    lng,
    zipcode,
    coalesce(council_district, 0)                                 as council_district,
    comm_plan_code,
    coalesce({{ title_case('comm_plan_name') }}, 'Unknown')       as comm_plan_name,
    coalesce(nullif(case_origin, ''), 'Unknown')                  as case_origin,
    desc_len,                                                     -- length of public_description, 0 if missing
    snapshot_date                                                 as source_snapshot_date,
    source
from deduped
where rn = 1
  and coalesce(status, '') <> 'Referred'
  and coalesce(service_name, '') <> 'Other'
  and coalesce(case_age_days, 0) >= 0
