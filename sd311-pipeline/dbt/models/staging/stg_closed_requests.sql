-- Staging: type the raw closed-request strings (closed_<YEAR> and backfill files).
-- The same request can appear in several files; dedupe happens in requests_clean.
-- safe_cast = try_cast: a malformed value becomes NULL instead of failing the build.
with src as (
    select * from {{ source('bronze', 'closed_requests') }}
)

select
    service_request_id,
    nullif(service_request_parent_id, '')                                        as service_request_parent_id,
    {{ dbt.safe_cast('date_requested', api.Column.translate_type('timestamp')) }} as date_requested,
    {{ dbt.safe_cast('case_age_days', api.Column.translate_type('integer')) }}   as case_age_days,
    case_record_type,
    service_name,
    service_name_detail,
    {{ dbt.safe_cast('date_closed', 'date') }}                                   as date_closed,
    status,
    {{ dbt.safe_cast('lat', api.Column.translate_type('float')) }}               as lat,
    {{ dbt.safe_cast('lng', api.Column.translate_type('float')) }}               as lng,
    zipcode,
    {{ dbt.safe_cast('council_district', api.Column.translate_type('integer')) }} as council_district,
    comm_plan_code,
    comm_plan_name,
    case_origin,
    -- Only the LENGTH of the free-text description is kept (a model feature).
    -- Missing text -> 0, the same rule as ml/scripts/make_slim.py and snapshots.py.
    coalesce(length(public_description), 0)                                      as desc_len,
    {{ dbt.safe_cast('snapshot_date', 'date') }}                                 as snapshot_date,
    source,
    _ingested_at
from src
