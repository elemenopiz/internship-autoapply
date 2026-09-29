# SRS+SAD TASK-015: ME-5 Test Coverage to 70%

**Version**: 1.0
**Date**: 2026-03-10
**Traces to**: PRODUCTION-READINESS.md ME-5
**Scope**: Increase line coverage from 41% to 70% via 7 incremental chunks.

---

## Problem Statement

Current test coverage is ~41% (8,450 statements, 4,982 missed). The bot layer
(appliers, search engines, browser manager, orchestration loop) accounts for
the majority of uncovered code. Previous attempts to tackle ME-5 in a single
session exceeded local compute limits. This plan breaks the work into 7
lightweight chunks, each completable in one session.

---

## Requirements

### NFR-ME5-COV: Line Coverage >= 70%

**Acceptance Criteria**

**AC-ME5-COV.1**
- **Given** `pytest --cov=. --cov-report=term-missing tests/` is run
- **When** all tests pass
- **Then** overall line coverage is >= 70%

**AC-ME5-COV.2**
- **Given** any module in `bot/` or `core/`
- **When** its individual coverage is measured
- **Then** no module is below 50% line coverage

**AC-ME5-COV.3**
- **Given** new tests are added
- **When** they execute
- **Then** they use mocks/patches for Playwright, network calls, and file I/O
  (no real browser or network required)

---

## Current Coverage Baseline (41%)

| Module | Stmts | Miss | Cover | Priority |
|--------|-------|------|-------|----------|
| `bot/apply/workday.py` | 214 | 191 | 11% | CRITICAL |
| `bot/apply/ashby.py` | 119 | 106 | 11% | CRITICAL |
| `bot/apply/linkedin.py` | 70 | 58 | 17% | HIGH |
| `bot/apply/indeed.py` | 63 | 52 | 17% | HIGH |
| `bot/bot.py` | 172 | 122 | 29% | HIGH |
| `bot/browser.py` | 66 | 54 | 18% | MEDIUM |
| `bot/search/linkedin.py` | 84 | 72 | 14% | MEDIUM |
| `bot/search/indeed.py` | 81 | 69 | 15% | MEDIUM |
| `bot/search/base.py` | — | — | ~80% | OK |
| `bot/apply/greenhouse.py` | — | — | ~40% | LOW |
| `bot/apply/lever.py` | — | — | ~40% | LOW |
| `run.py` | 62 | 62 | 0% | SKIP (startup script) |
| `setup.py` | 54 | 54 | 0% | SKIP (deprecated) |

**Well-covered modules (no work needed)**:
- `db/database.py` (99%), `core/scheduler.py` (98%), `core/resume_renderer.py` (95%),
  `config/settings.py` (90%), `core/filter.py` (90%), `core/ai_engine.py` (87%),
  `bot/state.py` (high), `app.py` / routes (covered by test_api + test_quick_wins)

---

## Chunked Implementation Plan

### Chunk 1: `bot/apply/workday.py` (11% -> ~70%)

**Target file**: `tests/test_appliers_workday_ashby.py` (extend) or new `tests/test_workday_coverage.py`
**Lines to cover**: ~150 of 214
**Approach**:
- Mock `page` (Playwright Page) with MagicMock
- Test `_fill_personal_info()` — mock locator chains, verify fill calls
- Test `_fill_work_experience()` — mock form fields, verify typed values
- Test `_fill_education()` — mock dropdowns and text fields
- Test `_upload_resume()` — mock file chooser
- Test `_answer_screening_questions()` — mock question elements, verify answers from `screening_answers`
- Test `_submit_application()` — mock button click, verify navigation
- Test `apply()` orchestration — mock all sub-methods, verify call order
- Test error paths — element not found, timeout, CAPTCHA detection

**Estimated tests**: ~15-20 new tests

---

### Chunk 2: `bot/apply/ashby.py` (11% -> ~70%)

**Target file**: `tests/test_ashby_coverage.py` (new)
**Lines to cover**: ~90 of 119
**Approach**:
- Mock Playwright Page for single-page Ashby form
- Test `_fill_basic_fields()` — name, email, phone, LinkedIn URL
- Test `_upload_resume()` — file input mock
- Test `_answer_custom_questions()` — text, select, radio, checkbox question types
- Test `_submit()` — button click, success detection
- Test `apply()` orchestration — full flow with mocked sub-methods
- Test error paths — missing fields, form validation errors

**Estimated tests**: ~10-12 new tests

---

### Chunk 3: `bot/apply/linkedin.py` + `bot/apply/indeed.py` (17% -> ~70%)

**Target file**: `tests/test_appliers_linkedin_indeed.py` (new)
**Lines to cover**: ~110 combined (58 + 52)
**Approach**:
- **LinkedIn**: Mock Easy Apply modal flow — open modal, fill fields, upload resume, handle multi-step pages, submit
- **Indeed**: Mock Quick Apply flow — fill fields, upload resume, answer questions, submit
- Both: test CAPTCHA detection, timeout handling, already-applied detection
- Both: test `apply()` returns correct `ApplyResult` (success/failure/skip)

**Estimated tests**: ~15-18 new tests

---

### Chunk 4: `bot/bot.py` (29% -> ~70%)

**Target file**: `tests/test_bot_loop.py` (extend existing)
**Lines to cover**: ~120 of 172
**Approach**:
- Mock all dependencies: BrowserManager, searchers, filter, AI engine, appliers, database
- Test `run_bot()` main loop — search -> filter -> generate -> apply -> save
- Test platform routing — correct applier selected per ATS type
- Test skip logic — already applied, blacklisted company, low score
- Test error handling — search failure, apply failure, AI engine failure
- Test stop signal — bot stops when `bot_state.should_stop` is set
- Test stats accumulation — applied count, skipped count, error count

**Estimated tests**: ~15-20 new tests

---

### Chunk 5: `bot/browser.py` (18% -> ~70%)

**Target file**: `tests/test_browser_manager.py` (extend existing)
**Lines to cover**: ~54 of 66
**Approach**:
- Mock `playwright.chromium.launch_persistent_context()`
- Test `get_browser()` — creates context on first call, reuses on subsequent
- Test `get_page()` — returns page from context
- Test `close()` — closes context and browser
- Test context options — user data dir, headless flag, viewport
- Test error recovery — browser crash, context lost

**Estimated tests**: ~8-10 new tests

---

### Chunk 6: `bot/search/linkedin.py` + `bot/search/indeed.py` (14-15% -> ~70%)

**Target file**: `tests/test_search_engines.py` (extend existing)
**Lines to cover**: ~140 combined (72 + 69)
**Approach**:
- Mock Playwright Page for search result scraping
- **LinkedIn**: Test `search()` — navigate to jobs page, parse job cards, extract title/company/URL/location
- **Indeed**: Test `search()` — navigate to search URL, parse result cards, extract fields
- Both: test pagination, empty results, rate limiting, login-wall detection
- Both: test `RawJob` construction with correct fields

**Estimated tests**: ~15-18 new tests

---

### Chunk 7: Mop-up + Verification

**Target**: Remaining gaps in `bot/apply/greenhouse.py`, `bot/apply/lever.py`, and any module still below 50%
**Approach**:
- Run full coverage report, identify remaining gaps
- Add targeted tests for uncovered branches
- Verify overall >= 70% and no bot/core module below 50%
- Update PRODUCTION-READINESS.md status to DONE

**Estimated tests**: ~10-15 new tests

---

## Design Constraints

### Test Isolation
- All Playwright interactions mocked via `unittest.mock.MagicMock` or `AsyncMock`
- No real browser launched during tests
- No network calls — all HTTP mocked
- Use `tmp_path` for any file operations

### Mock Patterns (reusable across chunks)

```python
# Standard Playwright page mock
def mock_page():
    page = MagicMock()
    page.goto = AsyncMock()
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.locator.return_value = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[])
    return page

# Standard BrowserManager mock
def mock_browser_manager():
    bm = MagicMock()
    bm.get_page = MagicMock(return_value=mock_page())
    return bm
```

### Conftest Additions
- Shared fixtures for mock page, mock browser manager, sample UserProfile, sample ScoredJob
- Keep in `tests/conftest.py` or chunk-specific conftest

---

## Traceability

| Req ID | Chunk | Source Files | Test Files | Status |
|--------|-------|-------------|------------|--------|
| NFR-ME5-COV | 1 | `bot/apply/workday.py` | `tests/test_appliers_workday_ashby.py` | DONE (100%) |
| NFR-ME5-COV | 2 | `bot/apply/ashby.py` | `tests/test_appliers_workday_ashby.py` | DONE (100%) |
| NFR-ME5-COV | 3 | `bot/apply/linkedin.py`, `bot/apply/indeed.py` | `tests/test_appliers_coverage.py` | DONE (100%) |
| NFR-ME5-COV | 4 | `bot/bot.py` | `tests/test_bot_loop.py` | DONE (99%) |
| NFR-ME5-COV | 5 | `bot/browser.py` | `tests/test_browser_manager.py` | DONE (98%) |
| NFR-ME5-COV | 6 | `bot/search/linkedin.py`, `bot/search/indeed.py` | `tests/test_search_engines.py` | DONE (99%) |
| NFR-ME5-COV | 7 | `routes/login.py`, mop-up | `tests/test_login_api.py` | DONE (97% overall) |

---

## Execution Notes

- Each chunk is designed to complete in a single Claude Code session (~15-20 new tests)
- Run `pytest --cov=. tests/ -q` after each chunk to measure progress
- Expected coverage progression: 41% -> 47% -> 52% -> 57% -> 63% -> 66% -> 70% -> 70%+
- **Actual final coverage**: 97% (bot/core/config/db), routes/login 100%. All bot/core modules >= 85%.
- If a chunk finishes early, start the next chunk in the same session
