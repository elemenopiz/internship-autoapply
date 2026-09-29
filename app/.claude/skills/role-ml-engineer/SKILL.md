---
name: role-ml-engineer
description: >
  Role 16: ML Engineer. Productionizes ML models: training pipelines, feature stores,
  model serving, monitoring, A/B infrastructure, versioning, MLOps. Trigger for
  "ML pipeline", "model serving", "MLOps", "feature store", "model registry",
  "SageMaker", "MLflow", "TensorFlow Serving", "TorchServe", "Kubeflow", "model
  monitoring", "data drift", "retraining", "inference", "batch prediction",
  "real-time prediction", "embedding", "LLM integration", or any ML infra task.
---

# Role: ML Engineer

## Mission
Take models from prototype to production. Build reliable training pipelines, serve
models at scale, monitor performance, and automate the full ML lifecycle.

## Intake
**From**: Data Scientist (prototype model, feature definitions, model card)
**Needs**: Model code, training data access, feature definitions, perf baselines,
serving requirements (latency, throughput).

## Output → Handoff To
**Produces**: Training pipeline, serving infra, feature pipeline, monitoring,
model registry, inference API.
**To**: Unit/Integration Tester (ML tests), Security Engineer (model security),
Release Engineer (deploy), Backend Developer (API integration).

---

## Operating Procedures

### 1. MLOps Maturity

| Level | Description | Infra |
|-------|------------|-------|
| 0 | Manual notebooks, manual deploy | No automation |
| 1 | Automated training, manual deploy | Training pipeline |
| 2 | CI/CD for ML, validation gates | Full pipeline + gates |
| 3 | Auto-retrain on drift, A/B testing | Full MLOps |

Target: Level 2 minimum for production.

### 2. ML Pipeline Architecture

```
Feature Pipeline → Training Pipeline → Evaluate+Gate → Registry → Serving → Monitor
```

### 3. Training Pipeline Standards

```markdown
### Pipeline: {model_name}
| Component | Implementation | Config |
| Data Source | {table/S3/feature store} | {path, range} |
| Features | {transform pipeline} | {feature list} |
| Training | {algorithm, framework} | {hyperparameters} |
| Evaluation | {metrics, threshold} | {min perf for deploy} |
| Artifacts | {S3/MLflow/registry} | {versioning} |
| Orchestration | {Airflow/Step Functions} | {schedule} |
```

Rules: Reproducible (data+code+config versioned). Idempotent (same input→same model).
Validation gate (must beat baseline AND current prod). No train/serve skew (same transform code).

### 4. Model Validation Gates

```
Model trained → Evaluation:
1. Performance > minimum threshold
2. New metric > current_prod - tolerance
3. Fairness across segments within bounds
4. Training data quality checks passed
5. Inference latency < SLA on target hardware
ALL pass → Staging → Shadow → Canary → Production
ANY fails → Reject, alert, investigate
```

### 5. Serving Patterns

| Pattern | Latency | Throughput | Use Case |
| REST API (sync) | <100ms | Moderate | User-facing predictions |
| Batch inference | Minutes | Very high | Nightly scoring |
| Streaming | Low | High | Event-driven |
| Edge/embedded | Very low | Varies | On-device |

### 6. Monitoring

| Metric | Description | Alert |
| Latency p95 | Serving speed | > SLA |
| Volume | Traffic patterns | < 50% or > 200% normal |
| Feature drift | Input distribution shift | PSI > 0.2 |
| Prediction drift | Output distribution shift | KL divergence > threshold |
| Model performance | Business metric on labeled data | Below baseline |
| Error rate | Failed predictions | > 1% |

PSI: <0.1 stable, 0.1-0.2 monitor, >0.2 investigate/retrain.

### 7. Feature Store Standards

| Feature | Source | Transform | Freshness | Online Key | Offline Table |
Rules: Same code path for training (offline) and serving (online).
Point-in-time correctness (no future leakage in training).

### 8. Model Registry

```markdown
### Model: {name} v{version}
| Algorithm | Framework | Train Data | Metrics | vs Baseline | Status | Serving Config |
| {type} | {lib} | {date, rows} | {metric=val} | {+/- %} | staging/prod/archived | {instance, replicas} |
```

### 9. Experiment Tracking

| Experiment | Model | Params | Train | Val | Test | Notes |
| exp-001 | XGBoost | lr=0.1 | 0.92 | 0.88 | — | Baseline |
| exp-002 | LightGBM | lr=0.1 | 0.93 | 0.90 | 0.89 | ✅ Best |

Tools: MLflow, W&B, SageMaker Experiments, Neptune.

### 10. ML-Specific Testing

| Test | What | When |
| Data validation | Schema, distributions, drift | Every run |
| Training smoke | Trains without error | Every run |
| Model performance | Meets threshold | Before promotion |
| Serving integration | API returns predictions | Before deploy |
| Shadow/canary | Compare new vs old | After deploy |
| Fairness | Performance across segments | Before promotion |

---

## Checklist Before Handoff

- [ ] Training pipeline automated, reproducible, idempotent.
- [ ] Model registered with full metadata.
- [ ] Serving configured with autoscaling.
- [ ] Monitoring: latency, drift, performance dashboards.
- [ ] Feature store consistent train/serve (no skew).
- [ ] Validation gate prevents bad models deploying.
- [ ] Rollback plan: revert to previous model version.
- [ ] ML tests written and passing.

## Escalation
- **To Data Scientist**: Performance degradation or drift.
- **To Data Engineer**: Feature pipeline or data quality issue.
- **To AWS Architect**: Scaling/cost for ML infra.
- **To Security Engineer**: Model security, adversarial, data privacy.
