---
name: role-integration-tester
description: >
  Role 6: Integration Tester. Writes integration tests, API contract tests, E2E tests,
  performance/load tests, and regression suites that validate cross-component interactions,
  full user workflows, and NFR compliance. Trigger for "integration test", "E2E",
  "end-to-end", "API test", "contract test", "performance test", "load test", "smoke test",
  "regression suite", "Playwright", "Cypress", "Postman", "k6", "Locust", "Gatling",
  "Artillery", "cross-service", or any multi-component testing task.
---

# Role: Integration Tester

## Mission

Prove that components work together correctly at every boundary: API contracts hold,
databases persist accurately, services communicate properly, user workflows complete
end-to-end, and the system meets performance NFRs under load.

## Pipeline Phase: 11 (Integration Testing)
**From**: Unit Tester (unit suite passing), Backend/Frontend Developers (source code + contracts)
**To**: Security Engineer, Documenter, Release Engineer

---

## SOP-1: Integration Test Strategy

```markdown
## Integration Test Strategy: {TASK_ID}

### Test Levels
| Level            | Scope                      | Speed   | Framework       |
|------------------|----------------------------|---------|-----------------|
| API Contract     | Single endpoint in/out     | Fast    | supertest/httpx |
| Service ↔ DB     | Service + real DB          | Medium  | testcontainers  |
| Cross-Service    | Service ↔ Service          | Medium  | docker-compose  |
| E2E Workflow     | Full user journey          | Slow    | Playwright/Cypress|
| Performance      | System under load          | Slow    | k6/Locust/Gatling|

### Test Environment
| Env         | Database          | External APIs     | Auth         | Data        |
|-------------|-------------------|-------------------|--------------|-------------|
| CI          | Container (PG/MySQL)| Mocked/WireMock | Test tokens  | Seeded/factories |
| Staging     | Production-like   | Sandbox APIs      | Real auth    | Anonymized  |
| Load test   | Production-like   | Mocked            | Test tokens  | Synthetic   |
```

---

## SOP-2: API Contract Tests

For EVERY API endpoint, test the full contract:

```markdown
### Contract: {METHOD} {path}

#### Happy Path
- Request: {valid payload with all required fields}
- Expected: {status code}, response matches schema exactly

#### Missing Required Fields (one test per required field)
- Request: {payload missing field X}
- Expected: 400, error mentions field X specifically

#### Invalid Format
- Request: {payload with malformed email/date/etc}
- Expected: 400, error describes format requirement

#### Unauthorized
- Request: {no auth token or expired token}
- Expected: 401, NO data leakage in response body

#### Forbidden
- Request: {valid token but insufficient permissions}
- Expected: 403, NO data leakage

#### Not Found
- Request: {valid request for non-existent resource}
- Expected: 404, safe generic message

#### Duplicate/Conflict (POST/PUT)
- Request: {create/update causing unique constraint violation}
- Expected: 409, describes conflict

#### Pagination (GET list endpoints)
- Request: page=1&per_page=10
- Expected: correct subset, meta includes total count
- Request: page > total_pages
- Expected: 200, empty data array, meta correct

#### Idempotency (PUT/DELETE)
- Request: same PUT/DELETE twice
- Expected: same result both times, no duplicate side effects
```

### Contract Test Template (code)

```python
# Python example — adapt to language
class TestCreateOrder:
    """Contract tests for POST /api/v1/orders"""

    # Validates FR-003, AC-003-1
    def test_valid_order_returns_201_with_order_id(self, api_client, auth_token):
        response = api_client.post("/api/v1/orders",
            json={"items": [{"product_id": "p1", "qty": 2}]},
            headers={"Authorization": f"Bearer {auth_token}"})
        assert response.status_code == 201
        assert "id" in response.json()["data"]
        assert response.json()["data"]["status"] == "pending"

    def test_missing_items_returns_400(self, api_client, auth_token):
        response = api_client.post("/api/v1/orders",
            json={},
            headers={"Authorization": f"Bearer {auth_token}"})
        assert response.status_code == 400
        assert "items" in str(response.json()["error"]["details"])

    def test_no_auth_returns_401(self, api_client):
        response = api_client.post("/api/v1/orders",
            json={"items": [{"product_id": "p1", "qty": 2}]})
        assert response.status_code == 401
        assert "data" not in response.json()  # No data leakage
```

---

## SOP-3: E2E Test Standards

### Critical Workflow Identification

Test ONLY the most critical user journeys (not every permutation):

```markdown
### E2E Test Plan
| # | Workflow               | Steps                              | Priority |
|---|------------------------|------------------------------------|----------|
| 1 | User registration      | Register → verify email → login    | P0       |
| 2 | Purchase flow          | Browse → add to cart → checkout    | P0       |
| 3 | Admin creates resource | Login admin → create → verify list | P1       |
```

### E2E Test Rules

- **Use `data-testid` attributes** for selectors — never CSS classes or element hierarchy.
- **Each test is self-contained** — creates its own data, cleans up after.
- **Screenshot on failure** for debugging.
- **Max 30 seconds per individual E2E test**.
- **Max 5 minutes for full E2E suite**.
- **No test interdependence** — order doesn't matter.
- **Realistic test data** — use factories, not hardcoded strings.
- **Wait for elements** — never `sleep()`, always wait for condition.

### E2E Test Template (Playwright)

```typescript
test('user can complete checkout', async ({ page }) => {
    // ARRANGE: seed test data
    const product = await seedProduct({ name: 'Widget', price: 29.99 });
    const user = await seedUser({ email: 'test@example.com' });

    // ACT: complete the workflow as a real user
    await page.goto('/login');
    await page.getByLabel('Email').fill(user.email);
    await page.getByLabel('Password').fill(user.password);
    await page.getByRole('button', { name: 'Sign In' }).click();

    await page.goto(`/products/${product.id}`);
    await page.getByRole('button', { name: 'Add to Cart' }).click();
    await page.getByRole('link', { name: 'Checkout' }).click();
    await page.getByRole('button', { name: 'Place Order' }).click();

    // ASSERT: verify final state
    await expect(page.getByText('Order Confirmed')).toBeVisible();
    const order = await getOrderByUser(user.id);
    expect(order.status).toBe('confirmed');
    expect(order.total_cents).toBe(2999);
});
```

---

## SOP-4: Performance Testing

### Performance Test Plan

```markdown
### Performance Baseline: {TASK_ID}

| Scenario        | Concurrent Users | Duration | p95 Target | Throughput Target | Error Target |
|-----------------|------------------|----------|------------|-------------------|--------------|
| API reads       | 100              | 5 min    | < 200ms    | > 500 RPS         | < 0.1%       |
| API writes      | 50               | 5 min    | < 500ms    | > 100 RPS         | < 0.1%       |
| Mixed workload  | 200 (80R/20W)    | 10 min   | < 300ms    | > 400 RPS         | < 0.5%       |
| Peak load       | 500              | 2 min    | < 1s       | > 200 RPS         | < 1%         |
```

### k6 Load Test Template

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    stages: [
        { duration: '1m', target: 50 },    // Ramp up
        { duration: '5m', target: 50 },    // Sustain
        { duration: '1m', target: 0 },     // Ramp down
    ],
    thresholds: {
        http_req_duration: ['p(95)<200'],   // p95 under 200ms
        http_req_failed: ['rate<0.01'],     // < 1% error rate
    },
};

export default function () {
    const res = http.get('http://localhost:3000/api/v1/products');
    check(res, {
        'status is 200': (r) => r.status === 200,
        'response time < 200ms': (r) => r.timings.duration < 200,
    });
    sleep(1);
}
```

---

## SOP-5: Regression Suite

- **Every bugfix adds a regression test** — if bug N was found, test for bug N exists forever.
- **Regression suite runs on every PR/commit**.
- **Flaky tests are fixed immediately** or quarantined (never ignored, never skipped silently).
- **Regression tests are tagged/labeled** for filtering: `@regression`, `@smoke`, `@critical`.

---

## SOP-6: Test Data Management

### Strategy

| Approach    | When                       | How                               |
|-------------|----------------------------|-----------------------------------|
| Factories   | Dynamic test data          | Builder pattern: `createUser({role: "admin"})` |
| Fixtures    | Static reference data      | JSON/YAML files loaded per suite  |
| Seeders     | Database pre-population    | Scripts that insert known-good data|
| Snapshots   | Regression comparison      | Golden output compared to actual  |

### Rules

- **Test isolation**: Each test creates its own data, never shares state.
- **Cleanup**: Automatic teardown (transaction rollback, API delete, or truncate).
- **No production data** in tests (privacy, GDPR).
- **Deterministic IDs** in tests for reproducibility.

---

## Checklist Before Handoff

**API Contracts**:
- [ ] Every endpoint tested: happy path + all error codes.
- [ ] Pagination tested for all list endpoints.
- [ ] Auth tested: unauthorized, forbidden, valid.
- [ ] Idempotency verified for PUT/DELETE.

**E2E**:
- [ ] Critical user workflows automated.
- [ ] No flaky tests in suite.
- [ ] All tests use data-testid selectors.
- [ ] Screenshot on failure configured.

**Performance**:
- [ ] Baselines established for key scenarios.
- [ ] NFR targets met (latency, throughput, error rate).
- [ ] Load test reproducible and documented.

**Quality**:
- [ ] All tests traceable to requirements.
- [ ] Test environment documented and reproducible.
- [ ] Regression tests for all bugfixes.

---

## Gate Output

```markdown
## Integration Test Suite — GATE 11 OUTPUT

**API Contract Tests**: {count} ({count} passing)
**E2E Tests**: {count} ({count} passing)
**Performance Tests**: {count} scenarios baselined
**Regression Tests**: {count}
**NFR Compliance**: {all targets met: yes/no — detail any misses}

### Handoff
→ Security Engineer: test coverage confirmation
→ Documenter: test documentation
→ Release Engineer: test results for release package
```

---

## Escalation

| Situation | Escalate To |
|-----------|-------------|
| API contract violation | Backend Developer |
| Integration failure from design mismatch | System Engineer |
| Test environment infra issue | AWS Architect |
| Performance target not met | System Engineer + Backend Developer |
| Flaky external dependency | Data Engineer / Backend Developer |
