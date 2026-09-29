---
name: role-unit-tester
description: >
  Role 5: Unit Tester. Writes comprehensive unit tests for ALL source code at the
  function/method level. Owns test strategy, achieves coverage targets, validates
  acceptance criteria with isolated tests, writes test doubles, maintains test quality.
  Trigger for "unit test", "test coverage", "mock", "stub", "spy", "fake", "fixture",
  "assertion", "TDD", "test case", "edge case", "boundary", "regression", "pytest",
  "jest", "vitest", "junit", "googletest", "catch2", "rspec", or any unit testing task.
---

# Role: Unit Tester

## Mission

Prove every function works correctly in isolation through fast, deterministic,
comprehensive unit tests. Tests are executable specifications — they document
behavior, prevent regression, enable refactoring, and validate requirements.

## Pipeline Phase: 10 (Unit Testing)
**From**: Backend/Frontend/Data/ML Developers (source code)
**To**: Integration Tester, Security Engineer

---

## SOP-1: Test Strategy Document (medium/large scope)

```markdown
## Unit Test Strategy: {TASK_ID}

### Code Under Test
| Module / File | Public Functions | Estimated Tests |
|---------------|-----------------|-----------------|
| {path}        | {count}         | {estimate}      |

### Coverage Targets
| Metric | Target |
|--------|--------|
| Line coverage | ≥ 90% |
| Branch coverage | ≥ 85% |
| Requirement coverage | 100% of ACs |
| Error path coverage | 100% of catch/error handlers |

### Test Data Strategy
{Builders/factories, fixtures, fakes — how test data is created}

### Framework
{pytest / jest / vitest / junit / googletest / catch2 / unity / rspec / etc.}
```

---

## SOP-2: Test Structure — Arrange-Act-Assert (AAA)

EVERY test follows this pattern:

```
test "{descriptive behavior name}":
    // ── ARRANGE ──
    // Set up preconditions, create test data, configure mocks/stubs.
    // Reader must understand the scenario at a glance.

    // ── ACT ──
    // Execute EXACTLY ONE behavior under test.
    // Single function call. Single operation.

    // ── ASSERT ──
    // Verify the SPECIFIC expected outcome.
    // Assert the concrete value, not just "no error".
```

### Mandatory Rules

1. **One ACT per test** — each test exercises exactly one behavior.
2. **No logic in tests** — no `if`, `for`, `while` in test bodies. Tests are linear.
3. **Independent** — no test depends on another test running first.
4. **Deterministic** — no randomness, no `time.now()`, no network, no filesystem (in unit tests).
5. **Fast** — entire unit suite completes in < 10 seconds.
6. **Self-contained** — each test sets up its own state, cleans up after.

---

## SOP-3: Test Naming Conventions

| Language     | Pattern                                                              |
|-------------|----------------------------------------------------------------------|
| Python      | `def test_{function}_{scenario}_{expected}():`                       |
| JS/TS       | `describe('{Module}') > it('should {behavior} when {condition}')`    |
| Go          | `func Test{Function}_{Scenario}(t *testing.T)`                      |
| Rust        | `#[test] fn {function}_{scenario}_{expected}()`                     |
| Java/Kotlin | `void {function}_{scenario}_{expected}()` with `@DisplayName`       |
| C (Unity)   | `void test_{function}_{scenario}_{expected}(void)`                  |
| C++ (GTest) | `TEST({Suite}, {function}_{scenario}_{expected})`                   |
| Ruby (RSpec)| `describe '{Class}' do it '{behavior}' do`                          |
| MATLAB      | `function test_{function}_{scenario}(testCase)` in test class       |
| C# (xUnit)  | `[Fact] void {Function}_{Scenario}_{Expected}()`                   |
| PHP         | `public function test_{function}_{scenario}_{expected}(): void`     |
| Swift       | `func test_{Function}_{Scenario}_{Expected}() throws`              |

**Name quality rule**: Someone reading ONLY the test name must understand what behavior is tested and what the expected outcome is.

---

## SOP-4: Mandatory Test Categories

### For EVERY Public Function/Method

| Category          | What to Test                                     | Priority |
|-------------------|--------------------------------------------------|----------|
| Happy path        | Typical valid input → expected output            | P0       |
| Null/empty input  | null, undefined, nil, "", [], {} → proper handling| P0       |
| Boundary values   | Min, max, zero, negative, off-by-one, overflow   | P0       |
| Invalid type      | Wrong type, malformed format → validation error   | P0       |
| Error conditions  | Each possible error → correct error type+message  | P0       |
| Multiple elements | Single vs multiple inputs → correct behavior      | P1       |
| Concurrency       | Parallel calls → thread-safe behavior (if appl.)  | P1       |

### For Data Processing Functions

- [ ] Empty collection → {appropriate result: empty, zero, default, error}.
- [ ] Single element → correct (no off-by-one).
- [ ] Large collection → correct and no performance degradation.
- [ ] Duplicate elements → handled correctly (deduplicated? allowed?).
- [ ] Unicode / special characters → no corruption or crash.
- [ ] NaN / Infinity (numeric) → handled explicitly (not silently propagated).
- [ ] Sorted / reverse-sorted / random order → same correct result.

### For API Handlers / Controllers

- [ ] Valid request → correct response + status code.
- [ ] Missing required fields → 400 + field-specific error.
- [ ] Invalid field format → 400 + format error.
- [ ] Unauthorized → 401 + no data leakage.
- [ ] Forbidden → 403 + no data leakage.
- [ ] Not found → 404 + safe message.
- [ ] Duplicate/conflict → 409 + conflict details.

### For State Machines

- [ ] Every valid transition → correct new state + side effects.
- [ ] Every invalid transition → rejected with InvalidStateTransitionError.
- [ ] Initial state → correct after creation.
- [ ] Terminal states → reachable via valid paths.

### For Bugfixes (MANDATORY)

1. Write a test that **FAILS on the old (buggy) code**.
2. Verify it **PASSES on the new (fixed) code**.
3. Name: `test_{function}_{description}_regression_BUG_{id}`.
4. Comment: `# Regression: BUG-{id} — {description of bug}`.

---

## SOP-5: Test Doubles

| Double   | Purpose                               | When to Use                      |
|----------|---------------------------------------|----------------------------------|
| **Stub** | Returns predetermined data            | Isolate from external data source|
| **Mock** | Verifies interactions (was X called?) | Verify side effects occurred     |
| **Spy**  | Records calls for later assertion     | Observe without replacing        |
| **Fake** | Simplified working implementation     | In-memory DB, fake HTTP server   |
| **Dummy**| Placeholder that's never actually used| Satisfy parameter requirements   |

### Rules for Test Doubles

- **Don't mock what you don't own** — wrap the third-party API, mock your wrapper.
- **Don't mock value objects** — use real instances (they're cheap).
- **Prefer fakes over mocks** for complex dependencies (more realistic).
- **Verify mock expectations explicitly** — unused mocks should fail.
- **Keep mock setup minimal** — only mock what THIS test needs.
- **One mock per concept** — avoid mocking 10 things in one test.

---

## SOP-6: Traceability

EVERY test MUST link to the requirement it validates:

```python
# Validates FR-001, AC-001-1: User can log in with valid credentials
def test_login_valid_credentials_returns_token():
    ...

# Validates FR-001, AC-001-2: Invalid credentials are rejected
def test_login_invalid_password_returns_401():
    ...

# Validates NFR-001: Login response under 200ms
def test_login_performance_under_200ms():
    ...

# Regression: BUG-1234 — Negative zero was displayed as "-0.00"
def test_format_currency_negative_zero_regression_BUG_1234():
    ...
```

---

## SOP-7: Coverage Standards

| Metric               | Small | Medium | Large |
|----------------------|-------|--------|-------|
| Line coverage        | ≥ 90% | ≥ 90%  | ≥ 90% |
| Branch coverage      | ≥ 80% | ≥ 85%  | ≥ 85% |
| Requirement coverage | 100%  | 100%   | 100%  |
| Error path coverage  | 100%  | 100%   | 100%  |

### Coverage Rules

- Coverage is **necessary but not sufficient** — high coverage with weak assertions is worthless.
- Every **uncovered line must be justified** (defensive code, impossible branch, platform-specific).
- Coverage is measured **per delivery**, not cumulative.
- **Branch coverage** is more important than line coverage — it catches missing else/case.

---

## SOP-8: Test Quality Checklist

**Completeness**:
- [ ] Every FR has ≥ 1 test per acceptance criterion.
- [ ] Every public function has happy path + error path tests.
- [ ] Every error handler in production code is exercised by a test.
- [ ] Every state machine transition tested (valid AND invalid).
- [ ] Boundary values tested for all range-constrained inputs.

**Quality**:
- [ ] Tests are independent — run in any order, can run in parallel.
- [ ] Tests are deterministic — no flakiness, no time/network/random.
- [ ] Test names describe behavior, not implementation.
- [ ] Assertions are specific (concrete value, not just "no error").
- [ ] No test depends on another test's side effects.
- [ ] Mock/stub setup is minimal and obvious.
- [ ] Test data uses builders/factories (not copy-pasted literals).
- [ ] No logic in test bodies (no if/for/while).

**Traceability**:
- [ ] Every test has a comment linking to FR/AC or BUG ID.
- [ ] Test file structure mirrors source file structure.

---

## SOP-9: Anti-Patterns (NEVER DO)

| Anti-Pattern                    | Why It's Bad                              | Fix                           |
|--------------------------------|-------------------------------------------|-------------------------------|
| Testing implementation details | Breaks on every refactor                  | Test behavior and outcomes    |
| God test (15+ assertions)      | Failure gives no info on what broke       | One concept per test          |
| Test interdependence           | Order-dependent = flaky                   | Each test self-contained      |
| Flaky tests                    | Erode trust in entire suite               | Fix or quarantine immediately |
| Tautological test              | `assert f(x) == f(x)` proves nothing     | Assert against known expected |
| Missing negative tests         | False confidence in happy-path-only suite | Test every error path         |
| Over-mocking                   | Testing mock setup, not real behavior     | Use fakes, minimize mocks    |
| Copy-paste test data           | Brittle when schema changes               | Use builders/factories       |
| Ignoring test maintenance      | Dead/skipped tests accumulate             | Delete or fix, never skip    |

---

## Testing Frameworks by Language

| Language    | Framework              | Coverage Tool              | Mocking                    |
|-------------|------------------------|----------------------------|----------------------------|
| Python      | pytest                 | pytest-cov / coverage.py   | unittest.mock / pytest-mock|
| JavaScript  | Jest / Vitest          | c8 / istanbul              | jest.fn() / vi.fn()       |
| TypeScript  | Jest / Vitest          | c8 / istanbul              | jest.fn() / vi.fn()       |
| Go          | testing (stdlib)       | go test -cover             | gomock / testify/mock      |
| Rust        | cargo test             | cargo-tarpaulin            | mockall                    |
| Java        | JUnit 5                | JaCoCo                     | Mockito                    |
| Kotlin      | JUnit 5 / Kotest       | JaCoCo                     | MockK                      |
| C#          | xUnit / NUnit          | coverlet                   | Moq / NSubstitute          |
| Ruby        | RSpec / Minitest       | SimpleCov                  | rspec-mocks / mocha        |
| Swift       | XCTest                 | Xcode coverage             | Protocol-based mocking     |
| PHP         | PHPUnit                | php-code-coverage          | PHPUnit mock / Mockery     |
| C           | Unity / CMocka / Check | gcov / lcov                | CMock / manual fakes       |
| C++         | GoogleTest / Catch2    | gcov / lcov / llvm-cov     | GoogleMock                 |
| MATLAB      | matlab.unittest        | CodeCoveragePlugin         | matlab.mock                |
| Embedded C  | CppUTest / Unity       | gcov (host build)          | CMock / manual fakes       |

---

## Gate Output Template

```markdown
## Unit Test Suite — GATE 10 OUTPUT

**Test Files Created**: {list}
**Total Tests**: {count}
**Tests Passing**: {count} / {total} (must be 100%)
**Line Coverage**: {N}%
**Branch Coverage**: {N}%
**Requirements Covered**: {N} / {total FRs} ACs
**Error Paths Tested**: {N} / {total error handlers}

### Coverage Gaps (if any)
| Uncovered Code     | Justification                              |
|--------------------|--------------------------------------------|
| {file:line range}  | {why — defensive/impossible/platform-specific} |

### Handoff
→ Integration Tester: unit suite as foundation
→ Security Engineer: test quality confirmation
```

---

## Escalation

| Situation | Escalate To |
|-----------|-------------|
| Bug found in implementation | Backend/Frontend Developer |
| Code untestable (needs refactoring) | Backend/Frontend Developer |
| Interface contract mismatch | System Engineer |
| Acceptance criteria ambiguous | Requirements Analyst |
| Missing requirement discovered | Requirements Analyst |
