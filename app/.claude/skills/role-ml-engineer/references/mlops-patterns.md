# MLOps Patterns Reference

## ML Pipeline Components

### Feature Store Pattern
```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ Raw Data    │────▶│ Feature      │────▶│ Online Store  │ → Real-time serving
│ (DB/S3/API) │     │ Transform    │     │ (Redis/DynamoDB)│
└─────────────┘     │ Pipeline     │     └──────────────┘
                    │              │────▶│ Offline Store │ → Training
                    └──────────────┘     │ (S3/Hive)     │
                                         └──────────────┘
```
Key rule: SAME transform code for training and serving (no train/serve skew).

### Model Validation Gates
```
Model trained → Evaluation pipeline:
  1. Performance threshold: metric > minimum (e.g., F1 > 0.85)
  2. Comparison: new_metric > current_production_metric - tolerance
  3. Fairness: performance across segments within bounds
  4. Data quality: training data passed all quality checks
  5. Latency: inference time < SLA on target hardware
  
  ALL gates pass → Promote to staging → Shadow test → Canary → Production
  ANY gate fails → Reject, alert, investigate
```

## Model Monitoring Metrics

### Performance Monitoring
| Metric | What | Alert |
|--------|------|-------|
| Prediction latency p95 | Serving speed | > SLA |
| Prediction volume | Traffic patterns | > 2× or < 0.5× normal |
| Error rate | Failed predictions | > 1% |
| Business metric | Downstream KPI | Below baseline |

### Data/Model Drift Detection
| Type | What Changes | Detection | Action |
|------|-------------|-----------|--------|
| Data drift | Input feature distributions | PSI, KS-test, KL-divergence | Alert → investigate |
| Concept drift | Relationship between features and target | Performance degradation | Retrain |
| Label drift | Target distribution shifts | Target distribution monitoring | Retrain + alert |
| Feature drift | Individual feature changes | Per-feature PSI | Alert → root cause |

PSI (Population Stability Index):
- < 0.1: No significant shift
- 0.1 - 0.2: Moderate shift — monitor
- \> 0.2: Significant shift — investigate/retrain

## Model Serving Configurations

### SageMaker Endpoint Pattern
```yaml
model_name: order-prediction-v2
instance_type: ml.m5.xlarge
instance_count: 2
autoscaling:
  min_instances: 1
  max_instances: 10
  target_metric: InvocationsPerInstance
  target_value: 100
health_check:
  path: /ping
  interval: 30s
  timeout: 3s
```

### Batch Transform Pattern
```yaml
model_name: customer-scoring-v3
input: s3://bucket/input/date={date}/
output: s3://bucket/output/date={date}/
instance_type: ml.m5.4xlarge
instance_count: 5
max_concurrent_transforms: 4
schedule: daily at 02:00 UTC
```

## Experiment Tracking Template
```markdown
| Experiment | Model | Hyperparams | Train Metric | Val Metric | Test Metric | Notes |
|------------|-------|-------------|-------------|------------|-------------|-------|
| exp-001 | XGBoost | lr=0.1, depth=6 | 0.92 | 0.88 | — | Baseline |
| exp-002 | XGBoost | lr=0.05, depth=8 | 0.95 | 0.89 | — | Slight overfit |
| exp-003 | LightGBM | lr=0.1, leaves=63 | 0.93 | 0.90 | 0.89 | ✅ Best |
```

Tools: MLflow, Weights & Biases, SageMaker Experiments, Neptune.

## Model Card Template
```markdown
## Model: {name} v{version}
### Overview
| Field | Value |
|-------|-------|
| Task | {classification/regression/...} |
| Algorithm | {type + framework} |
| Target | {variable definition} |
| Primary Metric | {metric = value ± CI} |
| Baseline | {naive baseline value} |

### Training Data
| Field | Value |
|-------|-------|
| Source | {table/path} |
| Date Range | {start — end} |
| Rows | {count} |
| Features | {count} (see feature table) |

### Evaluation
| Split | Metric | Value |
|-------|--------|-------|
| Train | {metric} | {value} |
| Validation | {metric} | {value} |
| Test | {metric} | {value} |

### Fairness
| Segment | Metric | Value | Within Bounds? |
|---------|--------|-------|----------------|

### Limitations
{Known weaknesses, edge cases, failure modes}

### Ethical Considerations
{Potential for bias, misuse, or harm}
```
