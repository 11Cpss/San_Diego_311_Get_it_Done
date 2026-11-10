-- ml_labels: one training label per request whose outcome is FINAL at as_of.
--
--   closed on/before as_of            -> bucket from actual days to close
--   still open at as_of, age >= 31    -> '31+' (it can no longer land in a smaller bucket)
--   still open at as_of, age <  31    -> EXCLUDED (label not known yet; using it would leak)
--
-- This model stores no "label final at" timestamp. In Python (sd311_core/labels.py)
-- a '31+' label is final at date_requested + 31 days (even if the request closed
-- later), a 0-30 day label at the end of its close day. That rule is why the
-- old/young split below uses age_at_as_of >= 31.
--
-- A request closed AFTER as_of is treated as open at as_of: we must not use
-- information from the future when building a training set "as of" a date.
with params as (
    select {{ as_of_date_sql() }} as as_of_date
),

base as (
    select
        r.service_request_id,
        r.date_requested_day,
        r.date_closed,
        p.as_of_date,
        case when r.date_closed is not null and r.date_closed <= p.as_of_date
             then {{ dbt.datediff('r.date_requested_day', 'r.date_closed', 'day') }}
        end                                                                  as days_to_close,
        {{ dbt.datediff('r.date_requested_day', 'p.as_of_date', 'day') }}    as age_at_as_of
    from {{ ref('requests_clean') }} r
    cross join params p
    where r.date_requested_day <= p.as_of_date
)

select
    service_request_id,
    date_requested_day,
    as_of_date,
    days_to_close,
    age_at_as_of,
    case when days_to_close is not null then {{ closure_bucket('days_to_close') }}
         else '31+'
    end                                       as label_bucket,
    days_to_close is null                     as is_censored_31_plus
from base
where days_to_close is not null
   or age_at_as_of >= 31
