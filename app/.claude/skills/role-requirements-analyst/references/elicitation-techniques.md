# Requirements Elicitation Techniques

## Given/When/Then Pattern (BDD)
```
Given {precondition — the starting state}
When {action — what the user or system does}
Then {outcome — the observable result}

Example:
Given a user is logged in with role "admin"
When they access the /admin/users endpoint
Then they receive a 200 response with a list of all users

Given a user has an expired session token
When they make any API request
Then they receive a 401 with message "Session expired"
And the response includes no user data
```

## Requirements Classification
| Type | Describes | Example |
|------|-----------|---------|
| Functional (FR) | What system DOES | "System authenticates users via email+password" |
| Non-Functional (NFR) | How well it does it | "Login response < 200ms at p95" |
| Constraint | Limitations | "Must use PostgreSQL 15+" |
| Business Rule | Domain logic | "Orders > $500 require manager approval" |
| Interface | Integration points | "Exposes REST API consumed by mobile app" |

## Vague Words to Replace
| Vague Word | Replacement Strategy |
|---|---|
| "fast" | Specify: "p95 latency < 200ms" |
| "secure" | Specify: "TLS 1.3, AES-256, bcrypt hashing" |
| "scalable" | Specify: "Handle 10,000 concurrent users" |
| "user-friendly" | Specify: "Task completion rate > 90% in usability test" |
| "appropriate" | Specify the exact behavior |
| "etc." | List all items explicitly |
| "should" | Use "shall" (mandatory) or remove (optional) |
| "handle" | Specify: "validate and return 400" or "retry 3 times" |

## INVEST Criteria for User Stories
| Letter | Criterion | What It Means |
|--------|-----------|---------------|
| I | Independent | Can be developed in any order |
| N | Negotiable | Details can be discussed |
| V | Valuable | Delivers value to user/business |
| E | Estimable | Team can estimate effort |
| S | Small | Fits in one iteration |
| T | Testable | Has clear acceptance criteria |

## Requirements Traceability Seed Template
| Req ID | Traces Forward To | Traced Back From |
|--------|-------------------|------------------|
| FR-001 | Design: {component} → Code: {file} → Test: {test} | US-001, PRD §3 |
