-- Singular test (leakage guard): a request that was still open at as_of and is
-- younger than 31 days has an UNKNOWN outcome, so it must not get a label.
select l.service_request_id, l.as_of_date, r.date_closed, l.age_at_as_of
from {{ ref('ml_labels') }} l
join {{ ref('requests_clean') }} r
  on r.service_request_id = l.service_request_id
where (r.date_closed is null or r.date_closed > l.as_of_date)
  and l.age_at_as_of < 31
