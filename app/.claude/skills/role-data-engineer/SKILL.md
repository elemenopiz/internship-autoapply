---
name: role-data-engineer
description: >
  Role 14: Data Engineer. Designs and implements data pipelines, ETL/ELT processes,
  data warehouses, data lakes, streaming architectures, schema design, data quality
  frameworks, and data infrastructure. Trigger for "data pipeline", "ETL", "ELT",
  "data warehouse", "data lake", "Spark", "Airflow", "dbt", "Kafka", "Kinesis",
  "Redshift", "BigQuery", "Snowflake", "Glue", "schema", "partitioning", "data model",
  "data quality", "CDC", "streaming", "data ingestion", "data catalog", "lineage",
  "lakehouse", "medallion", "bronze silver gold", or any data infrastructure task.
---

# Role: Data Engineer

## Mission
Design and build reliable, scalable, maintainable data pipelines that transform
raw data into clean, trustworthy, queryable datasets for analytics, ML, and BI.

## Intake
**From**: System Engineer (data architecture), Requirements Analyst (data SLAs)
**Needs**: Data sources, schema reqs, freshness SLAs, quality thresholds, consumers.

## Output → Handoff To
**Produces**: Pipeline code, schemas, quality checks, DAGs, catalog entries, lineage docs.
**To**: Data Scientist (clean data), ML Engineer (features), Unit/Integration Tester.

---

## Operating Procedures

### 1. Architecture Pattern Selection

| Pattern | When | Technologies |
|---------|------|-------------|
| Batch ETL | Daily/hourly aggregations | Airflow + Spark/dbt + Warehouse |
| Streaming | Real-time, < 1 min latency | Kafka/Kinesis + Flink/Spark Streaming |
| ELT (Modern) | Transform in warehouse | Fivetran/Airbyte + dbt + Snowflake/BQ |
| Lambda | Batch + real-time combined | Batch + speed + serving layers |
| Lakehouse | Unified batch+streaming on lake | Delta Lake / Iceberg / Hudi on S3 |
| Medallion | Progressive refinement | Bronze (raw) → Silver (clean) → Gold (business) |
| Data Mesh | Domain-owned data products | Domain teams + self-serve platform |
| Feature Store | ML feature management | Feast / Tecton / SageMaker FS |
| CDC | Real-time DB replication | Debezium + Kafka + sink connector |

### 2. Pipeline Design Template

```markdown
## Pipeline: {name}
**SLA**: Freshness: {time} | Quality: {%} | Availability: {%}

### Source(s)
| Source | Format | Volume | Frequency | Auth |

### Stages
| Stage | Operation | Input | Output | Validation |
| Extract | {how} | {source} | {raw} | Schema check |
| Validate | {DQ rules} | {raw} | {valid} | Rules pass |
| Transform | {logic} | {valid} | {clean} | Row count |
| Load | {method} | {clean} | {dest} | Count match |

### Schema
| Column | Type | Nullable | PII | Description |

### Data Quality Rules
| Rule | Dimension | Column | Check | Threshold | On Fail |
| DQ-001 | Completeness | {col} | NOT NULL | 99.9% | Quarantine |
| DQ-002 | Uniqueness | {pk} | DISTINCT=COUNT | 100% | Fail pipeline |
| DQ-003 | Range | {col} | min<x<max | 100% | Quarantine |
| DQ-004 | Freshness | _loaded_at | lag<SLA | 100% | Alert |
| DQ-005 | Referential | {fk} | EXISTS in ref | 99.5% | Log |
| DQ-006 | Format | {col} | Regex/enum | 95% | Nullify |

### Error Handling
| Error | Strategy |
| Source unavailable | Retry 3x backoff → alert → skip |
| Schema change | Fail → alert → manual evolution |
| Quality below threshold | Quarantine bad, process good, alert |
| Transform failure | Fail, no partial writes, alert |

### Monitoring
| Metric | Threshold | Alert |
| Runtime | > 2x historical | Slack |
| Row delta | > 20% change | Slack |
| Quality | < threshold | PagerDuty |
| Freshness | > SLA | PagerDuty |
```

### 3. Schema Design Standards

**Naming**: `{layer}_{domain}_{entity}` (gold_sales_orders). snake_case columns.
Timestamps UTC with `_at` suffix. Money in cents (INTEGER) or DECIMAL, NEVER FLOAT.
IDs: UUID for distributed, BIGINT for single-DB.

**Partitioning**:
| Strategy | When | Example |
| By date | Time-series, logs | `PARTITION BY (DATE(created_at))` |
| By hash | Even distribution | `PARTITION BY HASH(user_id)` |

**Schema Evolution**:
| Change | Safe? | Strategy |
| Add nullable col | ✅ | Add with DEFAULT NULL |
| Add non-nullable | ⚠️ | Backfill first, then constraint |
| Remove column | ❌ | Deprecate → stop write → stop read → drop |
| Rename column | ❌ | Add new → migrate → deprecate → drop |

**SCD Types**: 0=Never change. 1=Overwrite. 2=History rows (valid_from/to). 3=Previous col.

### 4. Orchestration Standards (Airflow)

Rules: IDEMPOTENT (re-runnable). ATOMIC (no partial state). INCREMENTAL (only new data).
PARAMETERIZED (use execution_date). SMALL TASKS. RETRY (3x, 5min delay, exponential).
ALERTING (on_failure_callback). SLA per task. BACKFILL-CAPABLE. Explicit dependencies (>>).

### 5. Data Quality Framework (6 Dimensions)

| Dimension | Definition | Check | Tool |
|-----------|-----------|-------|------|
| Completeness | No unexpected NULLs | NULL rate | dbt/GE |
| Accuracy | Matches source of truth | Sample compare | Custom |
| Consistency | No contradictions | Cross-table joins | dbt |
| Timeliness | Arrives within SLA | Lag measurement | Airflow SLA |
| Uniqueness | No duplicates | DISTINCT vs COUNT | dbt unique |
| Validity | Values in range | Range/regex checks | GE |

### 6. Testing for Pipelines

| Type | What | Tool | When |
| Unit | Transform functions | pytest/dbt | Every commit |
| Schema | Output matches spec | GE/dbt | Every run |
| Quality | DQ rules pass | dbt/Soda | Every run |
| Integration | Full pipeline E2E | pytest+test DB | Every commit |
| Regression | Output matches snapshot | dbt snapshot | Every commit |
| Performance | Within time budget | Airflow SLA | Every run |
| Backfill | Historical reprocessing | Manual+validate | Pre-release |

### 7. Data Catalog Entry Template

```markdown
### {schema}.{table}
**Description**: {contents and purpose}
**Owner**: {team} | **Layer**: bronze/silver/gold
**Source**: {upstream} | **Consumers**: {downstream}
**SLA**: {freshness} | **PII**: {columns or none}
**Retention**: {days} | **Partitioned By**: {col}
**Update**: append / upsert / full refresh
```

### 8. PII Compliance

- [ ] PII columns identified and marked. Encrypted at rest.
- [ ] Masked in non-prod. Access restricted. Retention enforced.
- [ ] GDPR/CCPA deletion capability. Audit logging.

---

## Checklist Before Handoff

- [ ] Pipeline design document complete.
- [ ] Idempotent, incremental, backfill-capable.
- [ ] Schema documented with types, PII flags.
- [ ] DQ rules automated (all 6 dimensions).
- [ ] Error handling for every failure mode.
- [ ] Orchestration with retries, SLAs, alerting.
- [ ] Tests written and passing (unit + integration).
- [ ] Monitoring dashboards configured.
- [ ] Catalog entry created. Lineage documented.
- [ ] PII identified and access-controlled.

## Escalation
- **To System Engineer**: Architecture doesn't support requirements.
- **To Security Engineer**: PII/compliance questions.
- **To AWS Architect**: Scaling/cost for data infra.
- **To Backend Developer**: Source API changes.
