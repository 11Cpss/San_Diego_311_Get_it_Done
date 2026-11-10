{#- Small helpers shared by several models. Each one is tiny on purpose. -#}

{#- Graffiti has many variants ("Graffiti - Public", "Graffiti - Code Enforcement", ...).
    Any service name containing 'graffiti' (any case) collapses to 'Graffiti'.
    ~0.5% of rows have no service_name at all; they become 'Unknown' so every
    fact row can join to dim_service. -#}
{% macro clean_service_name(col) -%}
    case
        when {{ col }} is null or {{ col }} = '' then 'Unknown'
        when lower({{ col }}) like '%graffiti%' then 'Graffiti'
        else {{ col }}
    end
{%- endmacro %}


{#- Map "days to close" to one of the four label buckets. -#}
{% macro closure_bucket(days) -%}
    case
        when {{ days }} is null then null
        when {{ days }} <= 2  then '0-2'
        when {{ days }} <= 7  then '3-7'
        when {{ days }} <= 30 then '8-30'
        else '31+'
    end
{%- endmacro %}


{#- Title case ("MID-CITY:CITY HEIGHTS" -> "Mid-city:city Heights").
    Databricks has initcap(); DuckDB does not, so we use adapter.dispatch
    to pick an engine-specific implementation. Both capitalise after spaces only. -#}
{% macro title_case(col) -%}
    {{ return(adapter.dispatch('title_case')(col)) }}
{%- endmacro %}

{% macro default__title_case(col) -%}
    initcap({{ col }})
{%- endmacro %}

{% macro duckdb__title_case(col) -%}
    array_to_string(
        list_transform(string_split(lower({{ col }}), ' '), lambda w: upper(w[1]) || w[2:]),
        ' '
    )
{%- endmacro %}


{#- Median. Both engines have median(); kept as a macro so it is easy to swap
    (e.g. to percentile_approx on very large tables). -#}
{% macro median(col) -%}
    median({{ col }})
{%- endmacro %}


{#- The as_of date for labels: the `as_of` var if set, else the latest open snapshot. -#}
{% macro as_of_date_sql() -%}
    {%- if var('as_of') -%}
        cast('{{ var("as_of") }}' as date)
    {%- else -%}
        (select max(snapshot_date) from {{ ref('stg_open_snapshots') }})
    {%- endif -%}
{%- endmacro %}


{#- safe_cast on DuckDB. dbt-duckdb's built-in safe_cast is a plain cast(), so ONE
    malformed value (e.g. case_age_days = 'N/A') fails the whole local/CI build,
    while Databricks uses try_cast() and turns it into NULL. This override makes
    DuckDB behave like production. It is picked up because dbt_project.yml puts
    this project before `dbt` in the dispatch search order. -#}
{% macro duckdb__safe_cast(field, type) -%}
    try_cast({{ field }} as {{ type }})
{%- endmacro %}
