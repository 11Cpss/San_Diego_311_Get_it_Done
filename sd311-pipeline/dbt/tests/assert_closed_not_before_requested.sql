-- Singular test: a request cannot close before the day it was requested.
-- dbt fails the test if this query returns any rows.
select service_request_id, date_requested_day, date_closed
from {{ ref('requests_clean') }}
where date_closed is not null
  and date_closed < date_requested_day
