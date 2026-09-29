# Integration & E2E Testing Patterns

## API Contract Test Template (per endpoint)
```
Endpoint: {METHOD} {path}
├── Happy path → {status}, {response shape validation}
├── Missing required fields → 400, {field-specific errors}
├── Invalid format → 400, {format error}
├── Unauthorized → 401, {no data leakage}
├── Forbidden → 403, {no data leakage}
├── Not found → 404, {safe message}
├── Conflict → 409 (for POST with duplicate)
└── Idempotency → Same request twice = same result (PUT/DELETE)
```

## E2E Test Structure (Playwright/Cypress)
```
test('user can complete checkout', async ({ page }) => {
  // ARRANGE: seed test data, authenticate
  // ACT: navigate, fill form, click buttons (as a real user)
  // ASSERT: verify final state (confirmation page, DB record, email)
});
```
Rules: Use `data-testid` selectors. Screenshot on failure. < 30s per test. Isolated state.

## Performance Testing with k6
```javascript
export const options = {
  stages: [
    { duration: '1m', target: 50 },   // Ramp up
    { duration: '5m', target: 50 },   // Sustain
    { duration: '1m', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<200'],  // 95% under 200ms
    http_req_failed: ['rate<0.01'],    // < 1% error rate
  },
};
```

## Test Environment Matrix
| Environment | DB | External APIs | Auth | Data |
|---|---|---|---|---|
| Unit | In-memory | Mocked | Fake | Generated |
| Integration | Real (container) | Mocked/sandbox | Test tokens | Seeded |
| Staging | Production-like | Real (sandbox) | Real auth | Anonymized |
| Load test | Production-like | Mocked | Test tokens | Synthetic |
