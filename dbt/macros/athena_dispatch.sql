{# dbt-athena has no convert_timezone override, so dbt_date's internal
   dispatch falls back to default__convert_timezone, which emits a raw
   `convert_timezone(...)` call Trino/Athena doesn't have. dbt_date already
   ships a working trino__convert_timezone (Athena's query engine); this
   shim makes dispatch use it instead. See dbt_project.yml's `dispatch:`
   config for the search_order that makes this project's macros checked
   before dbt_date's own. #}
{% macro athena__convert_timezone(column, target_tz, source_tz) %}
    {{ return(dbt_date.trino__convert_timezone(column, target_tz, source_tz)) }}
{% endmacro %}
