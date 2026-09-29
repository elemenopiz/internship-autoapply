# Production Readiness — Issue Tracker

**Created**: 2026-03-10
**Current Score**: 10.0 / 10
**Target Score**: 10.0 / 10

---

## Quick Wins (Small Effort, High Impact)

### QW-1: Fix Hardcoded SECRET_KEY + Use Keyring for API Keys
- **Score Impact**: +0.3
- **Files**: `app.py`, `config/settings.py`, `core/ai_engine.py`
- **Issue**: Flask SECRET_KEY is `"autoapply-secret-key"` (committed to repo). API keys stored in plaintext JSON despite `keyring` being in requirements.txt but never imported.
- **Fix**: Generate random SECRET_KEY at first run, store in config. Use `keyring` for API key storage. Remove plaintext keys from config.json.
- **Status**: [x] DONE (v1.8.1)

### QW-2: Fix 16 Swallowed Exceptions
- **Score Impact**: +0.2
- **Files**: `bot/bot.py:73,83`, `bot/browser.py:124,132`, `app.py:609,651,713`
- **Issue**: Silent `except Exception: pass` in hot paths. Feed events vanish, browser cleanup failures go unnoticed.
- **Fix**: Replace `pass` with `logger.debug()` or `logger.warning()` at minimum.
- **Status**: [x] DONE (v1.8.1)

### QW-3: Add Logging Configuration
- **Score Impact**: +0.2
- **Files**: `run.py`
- **Issue**: No `logging.basicConfig()` anywhere. Most `logger.info()` and `logger.debug()` calls produce no output. Inline `logging.getLogger(__name__)` in app.py instead of module-level variable.
- **Fix**: Add `logging.basicConfig(level=INFO, format=...)` in `run.py`. Fix inline logger in app.py. Add JSON structured logging option.
- **Status**: [x] DONE (v1.8.1)

### QW-4: Fix Race Condition on `_bot_thread`
- **Score Impact**: +0.1
- **Files**: `app.py`
- **Issue**: `_bot_thread` global read/written from Flask handlers and scheduler without locking. `_login_proc` has a lock but `_bot_thread` doesn't.
- **Fix**: Add `_bot_thread_lock` similar to `_login_lock`.
- **Status**: [x] DONE (v1.8.1)

### QW-5: Fix Temp File Leak in CSV Export
- **Score Impact**: +0.1
- **Files**: `app.py:321-330`
- **Issue**: `NamedTemporaryFile(delete=False)` created for CSV export, never cleaned up.
- **Fix**: Use `after_this_request` callback to delete, or serve from memory with `io.BytesIO`.
- **Status**: [x] DONE (v1.8.1)

---

## Medium Effort, High Impact

### ME-1: Add GitHub Actions CI Pipeline
- **Score Impact**: +0.5
- **Files**: New `.github/workflows/ci.yml`
- **Issue**: Zero CI/CD. No automated testing, linting, or checks on push/PR.
- **Fix**: Add workflow with: `pytest` (all tests), `ruff` (lint), `mypy` (type check). Run on push to master and PRs.
- **Status**: [x] DONE (v1.8.1) — `.github/workflows/ci.yml` with ruff + pytest

### ME-2: Add Authentication on API Endpoints
- **Score Impact**: +0.4
- **Files**: `app.py`, `templates/index.html`
- **Issue**: Every endpoint is open. Any local process (or website via DNS rebinding with `cors_allowed_origins="*"`) can control the bot and read API keys.
- **Fix**: Generate a random auth token at startup, pass to Electron via IPC, require `Authorization` header on all API calls. Restrict CORS to Electron origin.
- **Status**: [x] DONE (v1.8.1) — Bearer token auth on all /api/* endpoints, CORS locked to localhost, token injected into frontend via template variable, dev mode bypass

### ME-2b: Input Validation + Error Handlers (added during implementation)
- **Score Impact**: +0.3
- **Files**: `app.py`
- **Issue**: PUT /api/config didn't catch Pydantic ValidationError (returned 500). PATCH /api/applications didn't validate status values. No 400 error handler.
- **Fix**: Added ValidationError catch, status allowlist, 400 handler, hardened generic exception handler to log tracebacks.
- **Status**: [x] DONE (v1.8.1)

### ME-3: Add Python Tooling (ruff + mypy + pyproject.toml)
- **Score Impact**: +0.3
- **Files**: New `pyproject.toml`, `ruff.toml`
- **Issue**: No linting, no type checking, no formatting enforcement. Legacy `requirements.txt` + `setup.py`.
- **Fix**: Create `pyproject.toml` with project metadata, dependencies, ruff config, mypy config. Add `pre-commit` hooks.
- **Status**: [x] DONE (v1.8.1) — `pyproject.toml` with ruff+pytest config, `.pre-commit-config.yaml`, 64 lint issues fixed, CI updated

### ME-4: Split app.py into Blueprints
- **Score Impact**: +0.3
- **Files**: `app.py` → `routes/bot.py`, `routes/applications.py`, `routes/config.py`, `routes/profile.py`, `routes/login.py`, `routes/analytics.py`, `routes/lifecycle.py`, `app_state.py`
- **Issue**: 852-line monolith with 30+ routes, login browser management, scheduler init, and global mutable state all in one file.
- **Fix**: Extract into Flask Blueprints. Move globals into a shared state module. Add `create_app()` factory pattern.
- **Status**: [x] DONE (v1.8.1) — 7 blueprints, `app_state.py` shared state, `create_app()` factory, all 339 tests passing

### ME-5: Increase Test Coverage to 70%
- **Score Impact**: +0.4
- **Files**: New test files for `bot/bot.py`, `bot/apply/workday.py`, `bot/apply/ashby.py`
- **Issue**: ~35% coverage. Entire bot loop, 4/6 appliers, browser manager untested.
- **Fix**: Add mock-based tests for `run_bot()` orchestration, WorkdayApplier, AshbyApplier, LinkedInApplier, IndeedApplier. Target 70% line coverage.
- **Plan**: Chunked into 7 incremental sessions. See `.claude/docs/SRS-SAD-TASK-015-ME5-test-coverage.md`.
- **Status**: [x] DONE — 97% coverage on bot/core/config/db (target was 70%). All modules ≥ 85%. 542+ tests.

### ME-6: Pin Dependency Versions + Add Security Scanning
- **Score Impact**: +0.2
- **Files**: `pyproject.toml`, `.github/workflows/ci.yml`, `.github/dependabot.yml`
- **Issue**: All deps use `>=` minimum versions. No lock file. No vulnerability scanning.
- **Fix**: Pin exact versions (`==`) for all 13 runtime deps, add `pip-audit --strict` to CI, add Dependabot for weekly automated update PRs.
- **Status**: [x] DONE (v1.8.1)

### ME-7: mypy Type Checking (CI Enforced)
- **Score Impact**: +0.3
- **Files**: `pyproject.toml`, `.github/workflows/ci.yml`, `routes/applications.py`, `routes/analytics.py`, `routes/bot.py`, `bot/bot.py`, `bot/browser.py`, `core/ai_engine.py`, `core/resume_renderer.py`, `core/scheduler.py`, `bot/apply/workday.py`, `app_state.py`
- **Issue**: No static type checking. Type errors (null derefs, wrong kwargs, untyped returns) only caught at runtime.
- **Fix**: Added mypy config to `pyproject.toml` (`check_untyped_defs = true`), mypy to CI lint job, `types-requests` stub. Fixed 21 type errors across 7 files — including null-safety helpers for `Database | None`, explicit `str()`/`float()` casts on API JSON, and a latent `ApplyResult` constructor bug.
- **Status**: [x] DONE (v1.8.1)

### ME-8: Graceful Shutdown + Signal Handling
- **Score Impact**: +0.2
- **Files**: `app.py`, `run.py`, `routes/lifecycle.py`, `db/database.py`
- **Issue**: No resource cleanup on exit. SIGINT/SIGTERM killed process immediately, leaving orphaned Chromium processes, dangling bot threads, and open scheduler.
- **Fix**: Added idempotent `graceful_shutdown()` in `app.py` with `atexit` registration. Tears down: bot thread (10s join), scheduler, login browser, database. Signal handlers (SIGINT/SIGTERM) in `run.py`. `/api/shutdown` endpoint calls `graceful_shutdown()` before process exit.
- **Status**: [x] DONE (v1.8.1)

### ME-9: Security Hardening (Session A)
- **Score Impact**: +0.5
- **Files**: `app.py`, `routes/applications.py`, `routes/login.py`, `tests/test_quick_wins.py`
- **Issue**: Missing security headers, no request size limit, path traversal possible on resume/description endpoints, login URL validation used substring matching (bypassable), login browser TOCTOU race condition.
- **Fix**:
  1. Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Cache-Control: no-store` on API responses) via `_add_security_headers()` after_request hook.
  2. `MAX_CONTENT_LENGTH = 16MB` to prevent DoS via large uploads, with 413 error handler.
  3. Path traversal protection (`_is_safe_path()`) on resume and job description endpoints — validates resolved path is within `get_data_dir()`.
  4. URL validation in login open endpoint — uses `urlparse` + domain allowlist instead of substring matching. Rejects `linkedin.com.evil.com` style tricks.
  5. TOCTOU fix — moved `subprocess.Popen` inside `login_lock` context manager.
- **Tests**: 10 new tests in `TestSecurityHeaders`, `TestPathTraversalProtection`, `TestLoginURLValidation`.
- **Status**: [x] DONE (v1.8.2)

---

## Large Effort, High Impact

### LE-1: Refactor Frontend into Components
- **Score Impact**: +0.5
- **Files**: `templates/index.html` (909 lines, HTML only) + `static/css/main.css` (815 lines) + 17 ES modules in `static/js/` (~1,545 lines)
- **Issue**: Was single-file SPA with 3,170 lines of mixed HTML/CSS/JS.
- **Fix**: Extracted CSS to `static/css/main.css`, JS into 17 ES modules under `static/js/`. No build step required — native ES module support. Flask serves static files directly.
- **Modules**: app.js (entry), state.js, auth.js, socket.js, navigation.js, wizard.js, bot-control.js, feed.js, applications.js, profile.js, settings.js, analytics.js, review.js, modals.js, tag-input.js, file-upload.js, helpers.js, ai-status.js, login.js
- **Status**: [x] DONE (v1.8.2) — ADR-017 in SRS-SAD-TASK-013. All 334 tests passing.

### LE-2: Add Accessibility (WCAG 2.1 AA)
- **Score Impact**: +0.3
- **Files**: `templates/index.html`, `static/css/main.css`, `static/js/modals.js`, `static/js/navigation.js`, `static/js/tag-input.js`, `static/js/applications.js`, `static/js/profile.js`, `static/js/wizard.js`, `static/js/app.js`
- **Issue**: Zero ARIA attributes, zero keyboard navigation, zero screen reader support. Custom components (toggles, modals, tag inputs) use plain divs.
- **Fix**: Comprehensive WCAG 2.1 AA compliance:
  1. **Skip navigation link** — `.sr-only-focusable` skip link to main content
  2. **Semantic HTML** — `<main>`, `<nav aria-label>`, `role="tablist"` with `aria-selected`, `role="dialog"` / `role="alertdialog"` on modals
  3. **ARIA attributes** — `aria-live="polite"` on feed/bot status, `aria-label` on all form controls/tables/selects/inputs, `aria-required` on required fields, `aria-hidden` on decorative dots/icons, `aria-modal="true"` on dialogs, `aria-labelledby`/`aria-describedby` on modals
  4. **Keyboard navigation** — Arrow key navigation on nav tabs (Home/End support), Enter/Space on clickable table rows, Enter/Space on tag remove buttons, Enter/Space on file upload zone, focus trap in modal dialogs
  5. **Focus management** — Focus moves to first element on modal open, focus returns to trigger on close, `:focus-visible` outlines on all interactive elements (buttons, inputs, selects, nav tabs, pagination, toggles, file upload, tag suggestions, modal close)
  6. **Semantic fixes** — Tag suggestion `<span onclick>` → `<button type="button">`, AI warning link → `<button>`, toggle switch labels → `role="switch"` with `aria-labelledby`, table headers → `scope="col"`, pagination → `<nav>` with `aria-label`
  7. **Event delegation** — Centralized in app.js for clickable rows, cover letter buttons, pagination, status changes, notes blur-save, profile edit/delete
  8. **Reduced motion** — `@media (prefers-reduced-motion: reduce)` disables all animations and transitions
- **Status**: [x] DONE (v1.8.3) — All 64 API/integration tests passing

### LE-3: Add Internationalization (i18n)
- **Score Impact**: +0.2
- **Files**: `static/locales/en.json` (460+ strings), `static/js/i18n.js`, `core/i18n.py`, all route files, `app.py`, `static/js/app.js`
- **Issue**: All strings hardcoded in English. No locale support.
- **Fix**: JSON-based translation system with ~460 strings extracted to `static/locales/en.json`. Backend `core/i18n.py` with `t()` function and `{placeholder}` interpolation. Frontend `static/js/i18n.js` ES module with locale auto-detection (`?lang=` param or `<html lang>`). All backend error/status strings in 7 route files + `app.py` migrated to `t()` calls. `/api/locales` endpoint for locale discovery. To add a new language: copy `en.json` to `xx.json` and translate.
- **Status**: [x] DONE (v1.8.3) — 21 tests in `TestLocaleFile`, `TestBackendI18n`, `TestLocalesAPI`, `TestRoutesUseI18n`, `TestFrontendI18nModule`.

---

## Deferred (Nice-to-Have for v2.0)

| ID | Issue | Notes |
|----|-------|-------|
| D-1 | Auto-update (electron-updater) | Requires code signing first |
| D-2 | Crash reporting (Sentry) | Requires user consent flow |
| D-3 | Telemetry / usage analytics | Requires user consent flow |
| D-4 | Code signing + notarization | Requires Apple/Microsoft certificates |
| D-5 | SQLite WAL mode + busy timeout | [x] DONE (v1.8.3) — WAL journal mode for concurrent reads, 5s busy timeout. 3 tests in `TestSQLiteWALMode`. |
| D-6 | LLM retry with exponential backoff | [x] DONE (v1.8.3) — 3 retries, 1s/2s/4s backoff, retries on 429/5xx/network errors, fails fast on 400/401/403. 9 tests in `TestLLMRetry`. |
| D-7 | Structured JSON logging | [x] DONE (v1.8.3) — `JsonFormatter` in `run.py`, `AUTOAPPLY_LOG_FORMAT=json` env var, ISO 8601 timestamps, exception serialization. 5 tests in `TestStructuredJsonLogging`. |

---

## Recommended Tackle Order

**Phase A — Quick Wins (1 session)**
1. QW-1: SECRET_KEY + keyring
2. QW-2: Fix swallowed exceptions
3. QW-3: Logging configuration
4. QW-4: Race condition fix
5. QW-5: Temp file leak

**Phase B — DevOps Foundation (1-2 sessions)**
6. ME-3: pyproject.toml + ruff + mypy
7. ME-1: GitHub Actions CI
8. ME-6: Pin deps + security scanning

**Phase C — Architecture (2-3 sessions)**
9. ME-4: Blueprint refactor
10. ME-2: API authentication
11. ME-5: Test coverage to 70%

**Phase D — Frontend & UX (3-4 sessions)**
12. LE-1: Frontend component refactor
13. LE-2: Accessibility
14. LE-3: i18n (if needed)

**Estimated score after Phase A**: ~5.0
**Estimated score after Phase B**: ~6.0
**Estimated score after Phase C**: ~7.5
**Estimated score after Phase D**: ~8.5
