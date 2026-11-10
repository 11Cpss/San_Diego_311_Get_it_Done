-- Fact table: one row per request, with foreign keys to the dims and closure metrics.
select
    r.service_request_id,
    r.service_name,                                     -- -> dim_service
    l.location_id,                                      -- -> dim_location
    r.date_requested_day,                               -- -> dim_date
    r.date_requested,
    r.date_closed,
    r.status,
    r.is_closed,
    r.council_district,
    r.case_origin,
    case when r.date_closed is not null
         then {{ dbt.datediff('r.date_requested_day', 'r.date_closed', 'day') }}
    end                                                 as days_to_close,
    {{ closure_bucket(
        "case when r.date_closed is not null then " ~ dbt.datediff('r.date_requested_day', 'r.date_closed', 'day') ~ " end"
    ) }}                                                as closure_bucket
from {{ ref('requests_clean') }} r
left join {{ ref('dim_location') }} l
    on  coalesce(r.comm_plan_name, '') = coalesce(l.comm_plan_name, '')
    and r.council_district = l.council_district
