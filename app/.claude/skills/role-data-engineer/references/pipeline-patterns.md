# Data Engineering Patterns Reference

## Data Quality Framework (6 Dimensions)

| Dimension | Definition | How to Check | Tool |
|-----------|-----------|--------------|------|
| Completeness | No unexpected NULLs | `COUNT(col) / COUNT(*)` | dbt test / Great Expectations |
| Accuracy | Values match source of truth | Sample comparison | Custom validation |
| Consistency | No contradictions across tables | Cross-table joins | dbt test |
| Timeliness | Data arrives within SLA | `NOW() - MAX(loaded_at)` | Airflow SLA |
| Uniqueness | No unexpected duplicates | `COUNT(DISTINCT pk) = COUNT(pk)` | dbt unique test |
| Validity | Values in expected range/format | Range checks, regex | Great Expectations |

## Schema Evolution Rules
| Change Type | Safe? | Strategy |
|------------|-------|----------|
| Add nullable column | ✅ Safe | Backward compatible — add with default NULL |
| Add non-nullable column | ⚠️ Risky | Backfill first, then add constraint |
| Remove column | ❌ Breaking | Deprecate → stop writing → stop reading → drop |
| Rename column | ❌ Breaking | Add new → migrate → deprecate old → drop old |
| Change type (widen) | ⚠️ Careful | INT → BIGINT usually safe, test downstream |
| Change type (narrow) | ❌ Breaking | Validate all data fits, then migrate |

## Slowly Changing Dimensions
| Type | Strategy | When |
|------|----------|------|
| Type 0 | Never change | Reference data (country codes) |
| Type 1 | Overwrite | Only current value matters |
| Type 2 | Add row with valid_from/valid_to | Need full history |
| Type 3 | Add previous_value column | Need current + one previous |

## Partitioning Strategies
| Strategy | When | Example |
|----------|------|---------|
| By date | Time-series, logs, events | `PARTITION BY (date)` — daily partitions |
| By hash | Even distribution, point lookups | `PARTITION BY HASH(user_id)` |
| By range | Ordered data, range queries | `PARTITION BY RANGE(price)` |
| By list | Categorical data | `PARTITION BY LIST(region)` |

## dbt Project Structure
```
models/
├── staging/          # 1:1 with source tables, light transforms
│   ├── stg_orders.sql
│   └── stg_customers.sql
├── intermediate/     # Business logic, joins, complex transforms
│   └── int_order_items.sql
├── marts/            # Final business entities for consumption
│   ├── fct_orders.sql      # Facts (events)
│   └── dim_customers.sql   # Dimensions (entities)
└── schema.yml        # Tests and documentation
```

## Airflow DAG Best Practices
```python
# 1. Idempotent: safe to re-run for any execution_date
# 2. Atomic: each task succeeds or fails completely
# 3. Incremental: process only new/changed data (not full reload)
# 4. Parameterized: use execution_date, not datetime.now()
# 5. Small tasks: each task does one thing
# 6. Retry: default_retries=3, retry_delay=timedelta(minutes=5)
# 7. Alerting: on_failure_callback sends to PagerDuty/Slack
# 8. SLAs: sla=timedelta(hours=1) per task
```

## Data Lake vs Data Warehouse
| Aspect | Data Lake | Data Warehouse |
|--------|-----------|----------------|
| Schema | Schema-on-read | Schema-on-write |
| Data types | Raw, semi-structured, unstructured | Structured, modeled |
| Query engine | Athena, Presto, Spark | Redshift, BigQuery, Snowflake |
| Cost | Cheap storage (S3) | Expensive compute |
| Best for | Exploration, ML, archival | BI, reporting, dashboards |
| Governance | Harder (catalog needed) | Built-in |
