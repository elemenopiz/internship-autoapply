# Backend Patterns Reference

## REST API Status Codes Quick Reference
| Code | Meaning | When |
|------|---------|------|
| 200 | OK | Successful GET, PUT, PATCH |
| 201 | Created | Successful POST that creates |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Invalid input, validation failure |
| 401 | Unauthorized | Missing or invalid auth token |
| 403 | Forbidden | Valid auth but insufficient permissions |
| 404 | Not Found | Resource doesn't exist |
| 409 | Conflict | Duplicate resource, version conflict |
| 422 | Unprocessable Entity | Valid syntax but semantic error |
| 429 | Too Many Requests | Rate limited — include Retry-After |
| 500 | Internal Server Error | Unexpected server failure |
| 502 | Bad Gateway | Upstream service failure |
| 503 | Service Unavailable | Overloaded or maintenance |

## Pagination Patterns
```json
// Cursor-based (preferred for large/dynamic datasets)
GET /api/v1/items?cursor=eyJpZCI6MTAwfQ&limit=20
{ "data": [...], "meta": { "next_cursor": "eyJpZCI6MTIwfQ", "has_more": true } }

// Offset-based (simpler, acceptable for small/static datasets)
GET /api/v1/items?page=3&per_page=20
{ "data": [...], "meta": { "page": 3, "per_page": 20, "total": 500, "total_pages": 25 } }
```

## Database Index Strategy
| Query Pattern | Index Type | Example |
|---|---|---|
| Exact match | B-tree (default) | `WHERE user_id = ?` |
| Range query | B-tree | `WHERE created_at > ?` |
| Full-text search | GIN / Full-text | `WHERE body @@ to_tsquery(?)` |
| JSON field | GIN | `WHERE metadata->>'key' = ?` |
| Composite query | Composite B-tree | `WHERE status = ? AND created_at > ?` |
| Geospatial | GiST / R-tree | `WHERE ST_DWithin(location, ?, 1000)` |

Rules: Index every FK. Index every WHERE clause column in hot queries.
Monitor slow query log. Don't over-index (write penalty).

## Error Handling Patterns
```
// Typed error hierarchy
BaseError
├── ValidationError (400) — invalid input
│   ├── MissingFieldError
│   └── InvalidFormatError
├── AuthenticationError (401) — not authenticated
├── AuthorizationError (403) — not authorized
├── NotFoundError (404) — resource missing
├── ConflictError (409) — duplicate / version mismatch
└── InternalError (500) — unexpected failure

// Each error includes:
{
  "code": "AUTH_TOKEN_EXPIRED",        // Machine-readable
  "message": "Authentication token has expired",  // Human-readable
  "details": { "expired_at": "..." },  // Extra context
  "request_id": "req_abc123"           // For debugging
}
```

## Retry & Circuit Breaker
```
// Retry with exponential backoff
delay = base_delay * (2 ^ attempt) + random_jitter
Max retries: 3. Only for transient errors (5xx, timeout). Never for 4xx.

// Circuit breaker states
CLOSED (normal) → failure_count > threshold → OPEN (reject all)
OPEN → after timeout → HALF_OPEN (allow one test request)
HALF_OPEN → success → CLOSED | failure → OPEN
```

## Authentication Patterns
| Pattern | When | Storage |
|---------|------|---------|
| JWT (stateless) | Microservices, API-first | Token in header, no server session |
| Session (stateful) | Monolith, server-rendered | Session ID in cookie, data in Redis/DB |
| OAuth 2.0 + OIDC | Third-party login, SSO | Authorization code flow with PKCE |
| API Key | Machine-to-machine, simple | Key in header, rate limited per key |

## Caching Strategy
| Cache Level | TTL | Use |
|---|---|---|
| CDN (CloudFront) | Hours-days | Static assets, public API responses |
| Application (Redis) | Minutes-hours | DB query results, computed values |
| Request (in-memory) | Single request | Avoid duplicate DB calls per request |

Invalidation: Prefer TTL expiry. For active invalidation, use cache-aside pattern.
Never cache: auth decisions, financial transactions, PII.
