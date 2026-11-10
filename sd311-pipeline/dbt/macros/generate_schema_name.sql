{#-
  By default dbt names schemas "<target schema>_<custom schema>" (e.g. gold_silver).
  We want the custom schema verbatim (silver / gold / ml) so the tables land in
  sd311.silver, sd311.gold, sd311.ml on Databricks - matching the medallion layout.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
