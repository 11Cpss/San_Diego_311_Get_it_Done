-- Calendar table from 2016-01-01 through 2027-12-31 (covers all Get It Done data).
-- dbt.date_spine is a cross-database macro that generates one row per day.
with spine as (
    {{ dbt.date_spine('day', "cast('2016-01-01' as date)", "cast('2028-01-01' as date)") }}
)

select
    cast(date_day as date)                                         as date_day,
    cast({{ dbt.date_trunc('month', 'date_day') }} as date)        as month_start,
    extract(year from date_day)                                    as year,
    extract(month from date_day)                                   as month,
    -- extract(dow ...) numbers days differently on DuckDB (0-6) and Spark (1-7),
    -- so count days since a known Sunday (2016-01-03) instead: 0 = Sunday everywhere.
    mod({{ dbt.datediff("cast('2016-01-03' as date)", 'date_day', 'day') }}, 7) as day_of_week
from spine
