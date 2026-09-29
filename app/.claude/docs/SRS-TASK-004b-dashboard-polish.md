# Software Requirements Specification

**Document ID**: SRS-TASK-004b-dashboard-polish
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved (retroactive)
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Section 9.4, 10, 11

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for AutoApply Phase 4 (Dashboard Polish & Review Mode, v1.4.0): apply mode selection (full_auto / review / watch), a review gate that pauses the bot for user approval before each application, and an analytics dashboard with charts.

### 1.2 Scope
The system SHALL provide: a three-mode apply selector (full_auto, review, watch), a review gate that blocks the bot thread until the user approves/skips/edits/marks-manual each application, a review card UI with cover letter editing, and an analytics screen with daily application line chart, status breakdown doughnut chart, and platform bar chart.

The system SHALL NOT provide: CAPTCHA solving, scheduling/cron (Phase 6), Greenhouse/Lever/Workday appliers (Phase 5), screening question AI answers (Phase 8).

### 1.3 Definitions
| Term | Definition |
|------|-----------|
| Apply Mode | One of three operational modes: `full_auto`, `review`, or `watch` |
| Full Auto | Bot searches, generates documents, and applies without user intervention |
| Review Mode | Bot pauses after document generation and presents a review card; user must approve, edit, skip, or mark manual before the bot proceeds |
| Watch Mode | Identical to review mode in behavior, but the browser runs in headed (visible) mode so the user can observe the automation |
| Review Gate | The blocking synchronization point in the bot loop where execution pauses for a user decision |
| Review Card | The UI component displaying job title, company, platform, match score, cover letter textarea, and action buttons |
| Analytics Summary | Aggregate counts grouped by application status and platform |
| Daily Analytics | Time-series count of applications per day over a configurable window |

### 1.4 References
- SRS-TASK-004-bot-core (FR-041 through FR-052)
- SAD-TASK-004-bot-core (bot loop architecture, BotState design)
- PRD Section 9.4 (Dashboard Polish), Section 10 (Review Mode), Section 11 (Analytics)

---

## 2. Functional Requirements

### FR-053: Review Mode — Review Gate

**Description**: When `apply_mode` is `"review"` or `"watch"`, the bot SHALL pause after generating documents for each job and emit a `REVIEW` SocketIO event. The bot thread SHALL block until the user submits a review decision via one of four API endpoints. The four decisions are: approve, skip, edit (with modified cover letter), and manual (user will apply themselves).

**Priority**: P0
**Dependencies**: FR-050 (Main Bot Loop), FR-051 (SocketIO Events)

**Acceptance Criteria**:

- **AC-053-1**: Given `config.bot.apply_mode` is `"review"`, When a job passes the filter and documents are generated, Then the bot emits a `REVIEW` feed event containing `job_title`, `company`, `platform`, `match_score`, `cover_letter`, and `apply_url`, and the bot thread blocks.
- **AC-053-2**: Given the bot is awaiting review, When the user calls `POST /api/bot/review/approve`, Then `BotState.set_review_decision("approve")` is called, the bot thread unblocks, and the bot proceeds to apply to the job.
- **AC-053-3**: Given the bot is awaiting review, When the user calls `POST /api/bot/review/skip`, Then the bot thread unblocks with decision `"skip"`, the job is not applied to, a `SKIPPED` feed event is emitted, and the bot proceeds to the next job.
- **AC-053-4**: Given the bot is awaiting review, When the user calls `POST /api/bot/review/edit` with a JSON body containing `cover_letter`, Then the bot thread unblocks with decision `"edit"` and the edited cover letter text replaces the generated one for that application.
- **AC-053-5**: Given the bot is awaiting review, When the user calls `POST /api/bot/review/manual`, Then the bot thread unblocks with decision `"manual"`, an `ApplyResult` with `status="manual_required"` and `message="User chose to apply manually"` is saved to the database, an `APPLIED` event is emitted with a "Marked for manual apply" message, and the bot proceeds to the next job without automating the application.
- **AC-053-6**: Given the bot is awaiting review, When the user calls `POST /api/bot/stop`, Then the review event is set, the bot thread unblocks with decision `"stop"`, and the bot loop exits.
- **AC-053-7**: Given `config.bot.apply_mode` is `"full_auto"`, When a job passes the filter and documents are generated, Then no review gate is triggered and the bot proceeds directly to apply.

**Negative Cases**:

- **AC-053-N1**: Given the bot is NOT awaiting review, When the user calls any `/api/bot/review/*` endpoint, Then the endpoint returns HTTP 409 with `{"error": "No application awaiting review"}`.
- **AC-053-N2**: Given the bot is awaiting review, When the user calls `POST /api/bot/review/edit` without a `cover_letter` field in the JSON body, Then the endpoint returns HTTP 400 with `{"error": "cover_letter is required"}`.
- **AC-053-N3**: Given the bot is awaiting review and then stopped, When `wait_for_review()` returns, Then the decision is `"stop"` (not `None`), preventing the bot from crashing on a null decision.

---

### FR-054: Watch Mode — Headed Browser with Review Gate

**Description**: When `apply_mode` is `"watch"`, the system SHALL behave identically to review mode (FR-053) for the review gate, AND the Playwright browser SHALL launch in headed (visible, non-headless) mode so the user can observe the automation in real time.

**Priority**: P1
**Dependencies**: FR-053 (Review Gate), FR-047 (Browser Manager)

**Acceptance Criteria**:

- **AC-054-1**: Given `config.bot.apply_mode` is `"watch"`, When `BrowserManager` is initialized, Then `self.headless` is `False` and the Playwright browser launches with a visible window.
- **AC-054-2**: Given `config.bot.apply_mode` is `"review"`, When `BrowserManager` is initialized, Then `self.headless` is `True` (browser is hidden).
- **AC-054-3**: Given `config.bot.apply_mode` is `"full_auto"`, When `BrowserManager` is initialized, Then `self.headless` is `True` (browser is hidden).
- **AC-054-4**: Given `config.bot.apply_mode` is `"watch"`, When a job reaches the review gate, Then the review card is shown to the user with the same approve/skip/edit/manual actions as review mode.

**Negative Cases**:

- **AC-054-N1**: Given the deprecated `watch_mode` boolean field exists in config, When the system reads configuration, Then `apply_mode` takes precedence and `watch_mode` is ignored (backward compatibility only).

---

### FR-055: Apply Mode Selection

**Description**: The dashboard SHALL provide a mode selector dropdown allowing the user to switch between `full_auto`, `review`, and `watch` modes. The selection SHALL persist to `config.json` via the config API.

**Priority**: P0
**Dependencies**: FR-001 (Configuration Management)

**Acceptance Criteria**:

- **AC-055-1**: Given the dashboard loads, When the bot control panel renders, Then a `<select>` dropdown with options "Full Auto", "Review", and "Watch" is displayed.
- **AC-055-2**: Given the dashboard loads, When `loadApplyMode()` runs, Then it fetches `GET /api/config` and sets the dropdown to the current `config.bot.apply_mode` value.
- **AC-055-3**: Given the user selects a different mode from the dropdown, When `changeApplyMode(mode)` fires, Then it sends `PUT /api/config` with `{"bot": {"apply_mode": "<mode>"}}` and the new mode is saved to `config.json`.
- **AC-055-4**: Given the `apply_mode` field in `BotConfig`, Then it is a string with default value `"full_auto"` and valid values `"full_auto"`, `"review"`, or `"watch"`.
- **AC-055-5**: Given the user changes mode while the bot is running, Then the new mode takes effect on the next job (the current in-flight job is not affected).

**Negative Cases**:

- **AC-055-N1**: Given the config API call to save mode fails (network error), Then the frontend logs a warning to the console but does not crash or show an error dialog (graceful degradation).
- **AC-055-N2**: Given `apply_mode` is missing from config, Then the system defaults to `"full_auto"`.

---

### FR-056: Analytics Dashboard

**Description**: The system SHALL provide an Analytics screen accessible from the navigation bar, displaying three charts: (1) a line chart showing applications over time (last 30 days), (2) a doughnut chart showing application status breakdown, and (3) a bar chart showing applications by platform. Data is fetched from two API endpoints backed by SQLite aggregate queries.

**Priority**: P1
**Dependencies**: FR-004 (Database), FR-050 (Bot Loop saves applications)

**Acceptance Criteria**:

- **AC-056-1**: Given the user navigates to the Analytics screen, When `loadAnalytics()` fires, Then it fetches both `GET /api/analytics/summary` and `GET /api/analytics/daily?days=30` in parallel.
- **AC-056-2**: Given `GET /api/analytics/summary` is called, Then it returns JSON with `total` (integer), `by_status` (object mapping status string to count), and `by_platform` (object mapping platform string to count).
- **AC-056-3**: Given `GET /api/analytics/daily` is called with `days=30`, Then it returns a JSON array of objects `[{"date": "YYYY-MM-DD", "count": N}, ...]` for each day in the window that has at least one application.
- **AC-056-4**: Given daily analytics data is received, When `renderDailyChart()` runs, Then a Chart.js line chart is rendered on `#chart-daily` with dates on the X axis, application counts on the Y axis, `tension: 0.4`, fill enabled, and the Y axis starting at zero.
- **AC-056-5**: Given summary status data is received, When `renderStatusChart()` runs, Then a Chart.js doughnut chart is rendered on `#chart-status` with color-coded segments: applied (blue `#4da6ff`), interview (green `#53d769`), offer (yellow `#ffc107`), rejected (red `#e94560`), error (light red `#ff6b6b`), and gray (`#8892a4`) for all other statuses.
- **AC-056-6**: Given summary platform data is received, When `renderPlatformChart()` runs, Then a Chart.js bar chart is rendered on `#chart-platform` with platform names on the X axis and counts on the Y axis.
- **AC-056-7**: Given the user navigates away from Analytics and back, When charts are re-rendered, Then existing Chart.js instances are destroyed before creating new ones (no memory leak from duplicate canvas bindings).
- **AC-056-8**: Given the Analytics screen contains three chart areas, Then they are laid out as: "Applications Over Time" (full width), "Status Breakdown" and "By Platform" (side by side in a 2-column grid on desktop, stacked on mobile via `@media max-width: 900px`).

**Negative Cases**:

- **AC-056-N1**: Given the database has zero applications, When the analytics endpoints are called, Then `summary.total` is 0, `by_status` is `{}`, `by_platform` is `{}`, and `daily` is `[]` — the charts render empty without errors.
- **AC-056-N2**: Given the analytics API call fails (network error), Then the frontend logs a console warning and does not crash.
- **AC-056-N3**: Given the `days` query parameter is not provided on `GET /api/analytics/daily`, Then it defaults to 30.

---

## 3. Non-Functional Requirements

### NFR-028: Review Gate Thread Safety
**Description**: The review gate synchronization between the bot thread and Flask request threads SHALL use `threading.Event` for blocking and a `threading.Lock` for state mutation. No race conditions SHALL occur when multiple rapid review decisions arrive.
**Metric**: `BotState._review_event`, `_review_decision`, `_review_edits`, and `_awaiting_review` are all accessed exclusively under `_lock`.
**Priority**: P0

### NFR-029: Review Gate Responsiveness
**Description**: When the user clicks approve/skip/edit/manual, the review card SHALL disappear immediately (optimistic UI) and the bot thread SHALL unblock within 100ms of the API response.
**Metric**: Event.set() provides near-instant unblocking of Event.wait().
**Priority**: P1

### NFR-030: Analytics Query Performance
**Description**: Analytics summary and daily queries SHALL complete within 200ms for databases with up to 10,000 application records.
**Metric**: Simple `GROUP BY` aggregates on indexed `status`, `platform`, and `applied_at` columns.
**Priority**: P1

### NFR-031: Chart Rendering Performance
**Description**: Chart.js charts SHALL render within 500ms after data is received. Existing chart instances SHALL be destroyed before re-creation to prevent memory leaks.
**Metric**: `chartInstances[key].destroy()` is called before each `new Chart()`.
**Priority**: P1

### NFR-032: Review Card UI Accessibility
**Description**: The review card SHALL be scrolled into view with smooth animation when it appears. Action buttons SHALL be clearly labeled and color-coded: Approve (green), Edit & Apply (blue/primary), Apply Manually (yellow/warning), Skip (ghost/gray).
**Metric**: `scrollIntoView({ behavior: 'smooth', block: 'start' })` is called on show.
**Priority**: P2

### NFR-033: Graceful Stop During Review
**Description**: If the bot is stopped while awaiting a review decision, the review event SHALL be set immediately, `wait_for_review()` SHALL return `("stop", None)`, and the bot loop SHALL exit cleanly without attempting to apply.
**Metric**: `BotState.stop()` calls `self._review_event.set()` to unblock a waiting bot thread.
**Priority**: P0

---

## 4. Review Gate State Machine

```
                    ┌──────────────────────────────┐
                    │  Bot generates docs for job   │
                    └─────────────┬────────────────┘
                                  │
                    apply_mode == "review" or "watch"?
                         │                    │
                        YES                   NO (full_auto)
                         │                    │
                         ▼                    ▼
              ┌──────────────────┐   ┌────────────────┐
              │ Emit REVIEW event│   │ Proceed to Apply│
              │ begin_review()   │   └────────────────┘
              │ Block on Event   │
              └────────┬─────────┘
                       │
          ┌────────────┼───────────────┬──────────────┬──────────────┐
          ▼            ▼               ▼              ▼              ▼
     "approve"      "skip"          "edit"        "manual"       "stop"
          │            │               │              │              │
          ▼            ▼               ▼              ▼              ▼
     Apply job    Emit SKIPPED    Replace CL     Save as         Exit
                  Continue        then Apply     manual_required  loop
                                               Emit APPLIED
                                               Continue
```

---

## 5. API Endpoint Summary

| Method | Path | Request Body | Success Response | Error Response |
|--------|------|-------------|-----------------|----------------|
| POST | `/api/bot/review/approve` | — | `{"status": "approved"}` 200 | `{"error": "No application awaiting review"}` 409 |
| POST | `/api/bot/review/skip` | — | `{"status": "skipped"}` 200 | `{"error": "No application awaiting review"}` 409 |
| POST | `/api/bot/review/edit` | `{"cover_letter": "..."}` | `{"status": "edited"}` 200 | 409 if not awaiting; 400 if missing `cover_letter` |
| POST | `/api/bot/review/manual` | — | `{"status": "manual"}` 200 | `{"error": "No application awaiting review"}` 409 |
| GET | `/api/analytics/summary` | — | `{"total": N, "by_status": {...}, "by_platform": {...}}` | — |
| GET | `/api/analytics/daily` | Query: `?days=30` | `[{"date": "...", "count": N}, ...]` | — |

---

## 6. SocketIO Event Additions

| Event Type | Fields | Emitted When |
|------------|--------|-------------|
| `REVIEW` | `job_title`, `company`, `platform`, `match_score`, `cover_letter`, `apply_url`, `message` | Bot awaits review decision in review/watch mode |
| `SKIPPED` | `job_title`, `company`, `platform`, `message` | User chose to skip during review |

---

## 7. Out of Scope

- **CAPTCHA solving** — detected and reported, not solved
- **Scheduled start/stop** — Phase 6 (SRS-TASK-006)
- **Greenhouse, Lever, Workday appliers** — Phase 5 (SRS-TASK-005)
- **AI-powered screening question answers** — Phase 8 (SRS-TASK-008)
- **Historical analytics export (CSV of charts)** — future consideration

---

## 8. Traceability Seeds

| Req ID | User Story | Design Ref | Source Files | Unit Tests | Integ Tests | Docs |
|--------|-----------|-----------|-------------|-----------|------------|------|
| FR-053 | As a user, I want to review each application before the bot submits it, so I can approve, edit, or skip jobs. | SAD §review-gate | `bot/bot.py` (review gate block), `bot/state.py` (BotState review methods), `app.py` (review endpoints) | `tests/test_state.py`, `tests/test_api.py` | `tests/test_integration.py` | `docs/guides/how-the-bot-works.md` |
| FR-054 | As a user, I want to watch the bot apply in real time with a visible browser, so I can observe and intervene. | SAD §browser-manager | `bot/browser.py` (headless flag based on apply_mode) | `tests/test_bot_base.py` | — | `docs/guides/how-the-bot-works.md` |
| FR-055 | As a user, I want to switch between full auto, review, and watch modes from the dashboard, so I can control the bot's autonomy level. | SAD §config | `config/settings.py` (BotConfig.apply_mode), `templates/index.html` (mode selector + changeApplyMode) | `tests/test_settings.py` | `tests/test_api.py` | `docs/guides/configuration.md` |
| FR-056 | As a user, I want to see charts showing my application history, status breakdown, and platform distribution, so I can track my job search progress. | SAD §analytics | `db/database.py` (get_analytics_summary, get_daily_analytics), `app.py` (analytics endpoints), `templates/index.html` (Chart.js rendering) | `tests/test_database.py` | `tests/test_api.py` | `docs/guides/configuration.md` |
