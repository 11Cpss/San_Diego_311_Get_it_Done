-- One row per (community plan, council district) pair seen in the data.
-- location_id is a hash so fct_requests can join on a single column.
with pairs as (
    select distinct
        comm_plan_code,
        comm_plan_name,
        council_district
    from {{ ref('requests_clean') }}
)

select
    {{ dbt.hash(dbt.concat(["coalesce(comm_plan_name, '')", "'|'", dbt.safe_cast('council_district', api.Column.translate_type('string'))])) }} as location_id,
    comm_plan_code,
    comm_plan_name,
    council_district,
    council_district in (5, 6) as is_district_5_6
from pairs
