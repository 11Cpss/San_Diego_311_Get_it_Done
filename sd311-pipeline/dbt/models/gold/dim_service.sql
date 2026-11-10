-- One row per (cleaned) service name, with an infrastructure flag used in reporting.
{% set infrastructure = [
    'Pothole', 'Street Light Maintenance', 'Traffic Signal Issue',
    'Traffic Signal Timing', 'Traffic Sign Maintenance', 'Sidewalk Repair Issue',
    'Pavement Maintenance', 'Street Repair', 'Curb Repair'
] %}

select
    service_name,
    service_name in ('{{ infrastructure | join("', '") }}') as is_infrastructure,
    count(*)                                                 as n_requests
from {{ ref('requests_clean') }}
where service_name is not null
group by service_name
