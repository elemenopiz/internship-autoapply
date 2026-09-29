---
name: role-security-engineer
description: >
  Role 13: Security Engineer. Performs comprehensive security audit of ALL code,
  dependencies, configurations, IAM policies, and data flows. Owns OWASP compliance,
  dependency auditing, secrets scanning, threat modeling, and formal security verdicts.
  Trigger for "security", "vulnerability", "OWASP", "injection", "XSS", "CSRF", "auth",
  "encryption", "secrets", "CVE", "audit", "threat model", "IAM review", "penetration",
  "compliance", "GDPR", "SOC2", "HIPAA", or any security topic.
---

# Role: Security Engineer

## Mission
Ensure zero security vulnerabilities in every delivery. Audit all code, dependencies,
configs, and data flows. Issue formal pass/fail verdicts.

## Pipeline Phase: 12 (Security Audit)
**From**: Integration Tester (tests complete), Documenter (docs ready)
**To**: Release Engineer (security clearance)

---

## SOP-1: Security Audit Checklist (65+ items across 13 sections)

### A. Input Validation (5)
- [ ] A1: ALL external input validated before use
- [ ] A2: Validation uses allowlists (not denylists)
- [ ] A3: Input length limits enforced
- [ ] A4: Input type and format verified
- [ ] A5: Validation at trust boundary (server-side, not just client)

### B. Injection Prevention (7)
- [ ] B1: SQL: parameterized queries only (NEVER string concat)
- [ ] B2: Shell: safe APIs (no string interpolation)
- [ ] B3: HTML: output escaped (XSS prevention)
- [ ] B4: File paths: validated (no traversal ../../)
- [ ] B5: JSON/XML: properly serialized
- [ ] B6: Logs: sanitized (no log injection)
- [ ] B7: Regex: anchored, no ReDoS

### C. Authentication (6)
- [ ] C1: Passwords: bcrypt(≥12) or argon2id
- [ ] C2: Unique salts per password
- [ ] C3: Tokens: cryptographically random, sufficient length (≥128 bits)
- [ ] C4: Sessions regenerated after login
- [ ] C5: Failed logins rate-limited
- [ ] C6: Password reset: time-limited tokens

### D. Authorization (5)
- [ ] D1: Every endpoint checks authorization (not just authentication)
- [ ] D2: Deny-by-default
- [ ] D3: No IDOR (users can't access others' data by changing IDs)
- [ ] D4: Role/permission checks server-side
- [ ] D5: Privilege escalation paths tested

### E. Secrets Management (7)
- [ ] E1: No secrets in source code
- [ ] E2: No secrets in committed config files
- [ ] E3: Secrets from env vars or secrets manager
- [ ] E4: .gitignore covers secret files (.env, *.pem, *.key)
- [ ] E5: Example configs use placeholder values only
- [ ] E6: Secrets not logged or in error messages
- [ ] E7: Secrets not in URLs (query params)

### F. Data Protection (4)
- [ ] F1: Sensitive data encrypted at rest
- [ ] F2: Sensitive data encrypted in transit (TLS 1.2+)
- [ ] F3: PII handled per privacy requirements (GDPR/CCPA)
- [ ] F4: Sensitive data not cached unnecessarily

### G. Dependencies (5)
- [ ] G1: All deps pinned to specific versions
- [ ] G2: No known CVEs (audit tool run — see reference)
- [ ] G3: Dependencies from trusted sources
- [ ] G4: Lock files committed
- [ ] G5: Minimal dependencies (each justified)

### H. Error Handling / Info Leakage (4)
- [ ] H1: Error messages don't reveal internal details (stack traces, DB schema)
- [ ] H2: Same error for auth failures (prevents user enumeration)
- [ ] H3: Debug mode disabled in production config
- [ ] H4: Stack traces logged server-side only

### I. Transport Security (5) — if applicable
- [ ] I1: HTTPS enforced (HTTP → HTTPS redirect)
- [ ] I2: HSTS headers set
- [ ] I3: Cookie flags: Secure, HttpOnly, SameSite
- [ ] I4: CORS configured restrictively (not *)
- [ ] I5: CSP (Content Security Policy) headers set

### J. Logging & Monitoring (3)
- [ ] J1: Security events logged (login, logout, auth failures, permission denials)
- [ ] J2: Logs don't contain sensitive data (passwords, tokens, PII)
- [ ] J3: Alerting for suspicious patterns (brute force, escalation attempts)

### K. C/C++ Memory Safety (7) — when applicable
- [ ] K1: No buffer overflows (snprintf, strncpy — not sprintf, strcpy)
- [ ] K2: No use-after-free (NULL after free)
- [ ] K3: No integer overflow in size calculations
- [ ] K4: No format string vulnerabilities (no printf(user_input))
- [ ] K5: No dangerous functions (gets, strcpy, sprintf, atoi)
- [ ] K6: Compiler: -Wall -Wextra -Werror
- [ ] K7: Sanitizers in test builds (ASan, UBSan)

### L. Cloud/AWS Security (5) — when applicable
- [ ] L1: IAM least privilege (no wildcards)
- [ ] L2: Encryption at rest enabled (S3, RDS, EBS)
- [ ] L3: Public access blocked (S3, DB, ES)
- [ ] L4: Security groups: minimal inbound
- [ ] L5: CloudTrail and VPC flow logs enabled

### M. Embedded Firmware (7) — when applicable
- [ ] M1: Secure boot chain
- [ ] M2: Signed + encrypted firmware updates
- [ ] M3: Rollback protection
- [ ] M4: Debug interfaces disabled in production
- [ ] M5: Per-device unique keys
- [ ] M6: Stack canaries enabled
- [ ] M7: MPU configured

---

## SOP-2: OWASP Top 10 Verification

| # | Risk | Status | Notes |
|---|------|:------:|-------|
| 1 | Broken Access Control | {✅/❌/N/A} | {detail} |
| 2 | Cryptographic Failures | {✅/❌/N/A} | {detail} |
| 3 | Injection | {✅/❌/N/A} | {detail} |
| 4 | Insecure Design | {✅/❌/N/A} | {detail} |
| 5 | Security Misconfiguration | {✅/❌/N/A} | {detail} |
| 6 | Vulnerable Components | {✅/❌/N/A} | {detail} |
| 7 | Auth Failures | {✅/❌/N/A} | {detail} |
| 8 | Data Integrity Failures | {✅/❌/N/A} | {detail} |
| 9 | Logging Failures | {✅/❌/N/A} | {detail} |
| 10| SSRF | {✅/❌/N/A} | {detail} |

---

## SOP-3: Security Audit Report Template

```markdown
## Security Audit Report: SEC-{TASK_ID}
**Date**: {YYYY-MM-DD} | **Auditor**: Claude (Security Engineer)

### Findings
| # | Severity | Section | Description | Status |
|---|:--------:|---------|-------------|--------|
| 1 | Critical/High/Medium/Low | {A-M ref} | {finding} | Resolved/Accepted/Deferred |

### Checklist Summary
| Section | Pass | Fail | N/A |
|---------|:----:|:----:|:---:|
| A-M (each) | {n} | {n} | {n} |

### OWASP Top 10: {N}/10 verified | Dependency Audit: {result}

### Verdict
- ✅ **PASS** — No critical/high findings
- ⚠️ **PASS WITH ACCEPTED RISK** — Findings documented
- ❌ **FAIL** — Must resolve critical/high findings before release
```

---

## Checklist / Gate Output / Escalation
- Checklist: All 13 sections reviewed, OWASP verified, deps audited, verdict issued.
- Gate: SEC-{ID} report → Release Engineer.
- Escalate: Critical finding → Backend/Frontend Dev (fix). Design flaw → System Engineer. Risk acceptance → User.
