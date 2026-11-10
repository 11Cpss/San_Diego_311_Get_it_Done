-- Monthly KPIs by request month x district x service.
-- Bucket shares are over ALL requests in the cell, so still-open requests count
-- in the denominator (share_open makes the four shares + open add to 1).
select
    cast({{ dbt.date_trunc('month', 'date_requested_day') }} as date)          as month_start,
    council_district,
    service_name,
    count(*)                                                                   as volume,
    avg(case when closure_bucket = '0-2'  then 1.0 else 0.0 end)               as share_0_2,
    avg(case when closure_bucket = '3-7'  then 1.0 else 0.0 end)               as share_3_7,
    avg(case when closure_bucket = '8-30' then 1.0 else 0.0 end)               as share_8_30,
    avg(case when closure_bucket = '31+'  then 1.0 else 0.0 end)               as share_31_plus,
    avg(case when closure_bucket is null  then 1.0 else 0.0 end)               as share_open,
    {{ median('days_to_close') }}                                              as median_days_to_close
from {{ ref('fct_requests') }}
group by 1, 2, 3
