-- Backlog: for each snapshot day x service x district, how many requests were
-- open and their median age. Each daily open snapshot IS an "as of" view of the
-- backlog, so the history grows by one day every time the pipeline runs.
-- Same filters as requests_clean so the numbers agree with the other gold tables.
with snap as (
    select
        snapshot_date                                                    as as_of_date,
        {{ clean_service_name('service_name') }}                         as service_name,
        coalesce(council_district, 0)                                    as council_district,
        {{ dbt.datediff('cast(date_requested as date)', 'snapshot_date', 'day') }} as age_days
    from {{ ref('stg_open_snapshots') }}
    where coalesce(status, '') <> 'Referred'
      and coalesce(service_name, '') <> 'Other'
      and coalesce(case_age_days, 0) >= 0
)

select
    as_of_date,
    service_name,
    council_district,
    count(*)                    as open_count,
    {{ median('age_days') }}    as median_age_days
from snap
group by as_of_date, service_name, council_district
