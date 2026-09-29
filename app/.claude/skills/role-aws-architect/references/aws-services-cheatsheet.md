# AWS Service Selection Cheat Sheet

## Compute
| Need | Service | When |
|------|---------|------|
| Short-lived functions (< 15 min) | Lambda | Event-driven, variable load, pay-per-use |
| Containers (managed) | ECS Fargate | Steady containers, no server management |
| Containers (k8s) | EKS | Need Kubernetes ecosystem/portability |
| VMs | EC2 | Full OS control, GPU, persistent workloads |
| Batch jobs | AWS Batch | Large-scale parallel compute |
| Edge compute | Lambda@Edge / CloudFront Functions | Transform at CDN edge |

## Database
| Need | Service | When |
|------|---------|------|
| Relational (managed) | RDS (Postgres/MySQL) | Standard RDBMS, < 64TB |
| Relational (serverless) | Aurora Serverless v2 | Variable/unpredictable load |
| Key-value / document | DynamoDB | Single-digit ms latency, massive scale |
| In-memory cache | ElastiCache (Redis/Memcached) | Caching, session store, pub/sub |
| Graph | Neptune | Relationship-heavy queries |
| Time-series | Timestream | IoT, metrics, time-indexed data |
| Search | OpenSearch | Full-text search, log analytics |
| Ledger | QLDB | Immutable, verifiable transaction history |

## Storage
| Need | Service | When |
|------|---------|------|
| Object storage | S3 | Files, backups, data lake, static assets |
| File system (shared) | EFS | Multi-instance file share, POSIX |
| Block storage | EBS | EC2 attached disk |
| Archive | S3 Glacier | Rarely accessed, cost-optimized |
| Hybrid storage | Storage Gateway | On-prem ↔ cloud bridge |

## Messaging & Events
| Need | Service | When |
|------|---------|------|
| Queue (standard) | SQS | Decouple producers/consumers, at-least-once |
| Queue (FIFO) | SQS FIFO | Ordered, exactly-once delivery |
| Pub/Sub | SNS | Fan-out to multiple subscribers |
| Event bus | EventBridge | Cross-service events, rules, scheduling |
| Streaming | Kinesis Data Streams | Real-time streaming, ordering by shard |
| Streaming (managed) | Managed Kafka (MSK) | Kafka ecosystem needed |

## Networking & Content
| Need | Service | When |
|------|---------|------|
| CDN | CloudFront | Static assets, API acceleration |
| DNS | Route 53 | Domain management, health checks, routing |
| Load balancer (HTTP) | ALB | HTTP/HTTPS, path/host routing |
| Load balancer (TCP) | NLB | Ultra-low latency, TCP/UDP |
| API management | API Gateway | REST/WebSocket APIs, auth, throttling |
| VPN / Direct Connect | Site-to-Site VPN / DX | Hybrid connectivity |
| Service mesh | App Mesh | Microservice traffic management |

## ML & AI
| Need | Service | When |
|------|---------|------|
| Full ML platform | SageMaker | Training, tuning, hosting, pipelines |
| Pre-built AI | Rekognition/Comprehend/Textract | Vision, NLP, document processing |
| Foundation models | Bedrock | LLM access (Claude, Titan, etc.) |
| Custom training | SageMaker Training | Distributed training, spot instances |
| Inference | SageMaker Endpoints / Lambda | Real-time or batch predictions |

## Security & Identity
| Need | Service | When |
|------|---------|------|
| Identity | IAM | Service roles, user policies |
| User auth | Cognito | User signup/signin, OAuth/OIDC |
| Secrets | Secrets Manager | API keys, DB creds, rotation |
| Encryption keys | KMS | Envelope encryption, key rotation |
| WAF | AWS WAF | Web app firewall, rate limiting |
| Cert management | ACM | Free TLS certs, auto-renewal |
| Audit | CloudTrail | API call logging, compliance |

## Observability
| Need | Service | When |
|------|---------|------|
| Metrics & alarms | CloudWatch | Infrastructure + custom metrics |
| Logs | CloudWatch Logs | Centralized logging |
| Tracing | X-Ray | Distributed tracing across services |
| Dashboards | CloudWatch Dashboards | Operational visibility |

## Cost Optimization Rules
1. Use **Savings Plans / Reserved Instances** for baseline (saves 30-60%).
2. Use **Spot Instances** for fault-tolerant workloads (saves 60-90%).
3. Use **S3 Lifecycle Policies** to tier storage automatically.
4. Use **Lambda** for variable/bursty workloads (zero cost at zero traffic).
5. **Right-size** after 2 weeks of CloudWatch metrics.
6. Enable **AWS Cost Explorer** and set **budgets with alerts**.
7. Use **Graviton (ARM)** instances for 20% price-performance improvement.
8. Delete unused **EBS volumes, old snapshots, idle NAT Gateways**.
