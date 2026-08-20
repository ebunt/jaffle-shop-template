# MetricFlow in this project

MetricFlow is dbt Labs' semantic layer engine. It sits on top of the dbt DAG
and lets you define metrics once — with their aggregation logic, grain, and
allowed dimensions — instead of re-deriving them per BI tool or ad hoc query.
Consumers (BI tools, `dbt sl`/`mf` CLI, notebooks) ask for a metric plus a
`group_by`, and MetricFlow compiles that into SQL against the underlying dbt
models.

## Core concepts

- **Semantic model** — maps a dbt model (via `model: ref(...)`) to the
  entities, dimensions, and measures MetricFlow can query against it. One per
  mart, roughly.
- **Entity** — a join key (`primary`, `foreign`, `unique`). MetricFlow uses
  these to auto-join semantic models when a query spans more than one.
- **Dimension** — an attribute to group or filter by. `categorical` (a plain
  column) or `time` (requires a `time_granularity`).
- **Measure** — a raw aggregation over a column (`sum`, `count_distinct`,
  `average`, `median`, ...). Measures aren't queried directly; metrics wrap
  them.
- **Metric** — the queryable unit. Types used in this project:
  - `simple` — one measure, optionally filtered.
  - `ratio` — numerator measure / denominator measure.
  - `derived` — an expression over other metrics (supports `offset_window`
    for period-over-period comparisons).
  - `cumulative` — running total of a measure over time.
- **Saved query** — a named, reusable `metrics` + `group_by` combination.
- **Time spine** — a model of one row per calendar day, used by MetricFlow to
  fill gaps and align time-based joins/offsets.

## Where this lives in the repo

Semantic layer YAML is colocated with the mart it describes, under
`dbt/models/marts/*.yml`, alongside the existing column/test definitions —
not in separate files. The time spine is a real dbt model:
`dbt/models/marts/metricflow_time_spine.sql` (built from
`dbt_date.get_base_dates`, 10 years of days).

| Mart | Semantic model | Grain | Notable metrics |
|---|---|---|---|
| `orders.yml` | `orders` | one row per order | `order_total`, `orders`, `new_customer_orders`, `large_orders`, `food_orders`, `drink_orders` |
| `order_items.yml` | `order_item` | one row per order item | `revenue`, `food_revenue`, `drink_revenue` (simple); `food_revenue_pct`, `drink_revenue_pct` (ratio); `revenue_growth_mom`, `order_gross_profit` (derived); `cumulative_revenue` (cumulative) |
| `customers.yml` | `customers` | one row per customer | `lifetime_spend_pretax`, `count_lifetime_orders` (simple); `average_order_value` (derived) |
| `locations.yml` | `locations` | one row per location | dimension-only (`average_tax_rate` measure, no metric defined yet) |
| `products.yml` | `products` | one row per product | dimension-only, no measures/metrics defined |
| `supplies.yml` | `supplies` | one row per supply/product combo | dimension-only, no measures/metrics defined |

Entities chain these together — e.g. `orders` has a `customer` foreign entity
(`expr: customer_id`) and a `location` foreign entity, so a query can join
`order_total` (from `orders`) by `location_name` (from `locations`) without
either semantic model mentioning the other directly.

Three saved queries currently exist: `order_metrics` (on `orders.yml`),
`revenue_metrics` (on `order_items.yml`), and `customer_order_metrics` (on
`customers.yml`).

## Current status

The semantic layer YAML is complete and valid — `dbt parse` compiles it into
`target/semantic_manifest.json` without errors (one informational warning:
`cumulative_revenue`'s cumulative window/grain can't be represented in the
OSI export, which doesn't affect dbt or MetricFlow itself).

There is **no local metric-querying CLI installed** (`dbt sl` / `mf`). The
`dbt-metricflow` package (which provides both) currently pins
`dbt-core<1.12.0`, and its dependency chain is incompatible with Python
3.14 — installing it downgrades dbt-core and breaks the `dbt` CLI outright on
this project's toolchain (dbt-core 1.12.0, Python 3.14). See the git history
around this file for the investigation.

## Working with it today

Validate the semantic layer compiles, without any extra package:

```bash
task run ARGS="--select tag:none"   # or any dbt invocation that parses
uv run dbt parse --project-dir dbt --profiles-dir dbt
```

Inspect the compiled manifest directly:

```bash
uv run python -c "
import json
d = json.load(open('dbt/target/semantic_manifest.json'))
print([m['name'] for m in d['metrics']])
"
```

## Getting local metric queries working (future)

Once `dbt-metricflow` ships a release compatible with `dbt-core>=1.12`:

```bash
uv add --group dev "dbt-metricflow[dbt-duckdb]"
uv run mf query --metrics order_total --group-by metric_time__day
```

Check compatibility before retrying: `curl -s
https://pypi.org/pypi/dbt-metricflow/json | jq -r '.info.requires_dist[]
| select(startswith("dbt-core"))'` — as long as that upper bound excludes
1.12+, the install will conflict with this project's dbt-core pin.
