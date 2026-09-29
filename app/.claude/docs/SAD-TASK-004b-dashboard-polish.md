# System Architecture Document

**Document ID**: SAD-TASK-004b-dashboard-polish
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved (retroactive)
**Author**: Claude (System Engineer)
**SRS Reference**: Phase 4 PRD checklist (FR-053 through FR-056)

---

## 1. Executive Summary

Phase 4 adds two major capabilities to AutoApply: a **review gate** that lets users approve, edit, or skip each application before submission, and an **analytics dashboard** with Chart.js visualizations. The review gate is implemented as a blocking `threading.Event` in the bot thread, unblocked by REST API calls from the frontend. Three application modes (`full_auto`, `review`, `watch`) are selectable at runtime via a dropdown in the bot control bar. The analytics screen queries aggregate data from SQLite and renders three client-side charts: a daily application line chart, a status breakdown doughnut, and a platform distribution bar chart.

---

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        templates/index.html                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │ Review Card   │  │ Mode Selector│  │ Analytics    │                   │
│  │ (approve/edit │  │ (full_auto/  │  │ Screen       │                   │
│  │  /skip/manual)│  │  review/     │  │ (Chart.js)   │                   │
│  └──────┬───────┘  │  watch)       │  └──────┬───────┘                   │
│         │          └──────┬───────┘         │                            │
└─────────┼─────────────────┼─────────────────┼────────────────────────────┘
          │ POST             │ PATCH            │ GET
          ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                            app.py (Flask)                                │
│  /api/bot/review/approve   /api/config       /api/analytics/summary     │
│  /api/bot/review/skip                        /api/analytics/daily       │
│  /api/bot/review/edit                                                    │
│  /api/bot/review/manual                                                  │
└──────┬──────────────────────────────────────────────┬────────────────────┘
       │                                              │
       ▼                                              ▼
┌──────────────┐                             ┌──────────────┐
│ bot/state.py │                             │ db/database.py│
│  BotState    │                             │  get_analytics│
│  (Event gate)│                             │  _summary()   │
└──────┬───────┘                             │  get_daily_   │
       │                                     │  analytics()  │
       ▼                                     └──────────────┘
┌──────────────┐
│ bot/bot.py   │
│  run_bot()   │
│  (blocks on  │
│   Event.wait)│
└──────────────┘
```

### 2.2 Review Gate Integration in Bot Pipeline

The review gate inserts between document generation and application submission:

```
search → filter → generate_docs → [REVIEW GATE] → apply → save
                                       │
                          (only if apply_mode == "review" or "watch")
                                       │
                          bot thread blocks on Event.wait()
                                       │
                          user clicks Approve/Edit/Skip/Manual in UI
                                       │
                          POST /api/bot/review/{action}
                                       │
                          BotState.set_review_decision() → Event.set()
                                       │
                          bot thread unblocks, reads decision
```

### 2.3 Layer Architecture

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| Presentation | `templates/index.html` | Review card UI, mode selector, Chart.js charts |
| API | `app.py` | Review endpoints (4), analytics endpoints (2) |
| State | `bot/state.py` | Thread-safe review gate via `threading.Event` |
| Bot Logic | `bot/bot.py` | `_wait_for_review()`, decision branching |
| Data | `db/database.py` | `get_analytics_summary()`, `get_daily_analytics()` |
| Config | `config/settings.py` | `BotConfig.apply_mode` field |
| Browser | `bot/browser.py` | Headed/headless toggle based on `apply_mode` |

---

## 3. Component Catalog

### 3.1 Review Gate (bot/state.py — BotState)

The review gate is a synchronization mechanism built from `threading.Event` and guarded by `threading.Lock`. It allows the bot thread to block until the user makes a decision via the UI.

**State fields**:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `_review_event` | `threading.Event` | (cleared) | Blocks bot thread until set |
| `_review_decision` | `str \| None` | `None` | `"approve"`, `"skip"`, `"edit"`, or `"manual"` |
| `_review_edits` | `str \| None` | `None` | Edited cover letter text (only for `"edit"`) |
| `_awaiting_review` | `bool` | `False` | Exposed in `get_status_dict()` for UI polling |

**Methods**:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `begin_review()` | `() -> None` | Clears event, resets decision/edits, sets `_awaiting_review = True` |
| `set_review_decision()` | `(decision: str, edits: str \| None = None) -> None` | Stores decision, sets `_awaiting_review = False`, calls `_review_event.set()` |
| `wait_for_review()` | `() -> tuple[str, str \| None]` | Blocks on `_review_event.wait()`. Returns `("stop", None)` if `_stop_flag` is set; otherwise `(decision, edits)` |

**Critical safety**: `BotState.stop()` calls `_review_event.set()` to unblock a waiting bot thread, preventing deadlock on shutdown.

### 3.2 Bot Loop Review Integration (bot/bot.py)

The `_wait_for_review()` helper in `bot/bot.py` calls `state.begin_review()` then `state.wait_for_review()`. The main loop checks `config.bot.apply_mode in ("review", "watch")` before entering the gate.

Decision handling after unblock:

| Decision | Action |
|----------|--------|
| `"approve"` | Continue to `_apply_to_job()` with original cover letter |
| `"edit"` | Replace `cover_letter_text` with `edited_cl`, then apply |
| `"skip"` | Emit `SKIPPED` event, `continue` to next job |
| `"manual"` | Save as `manual_required` status, emit `APPLIED` with manual message, `continue` |
| `"stop"` | `break` out of search loop (bot stopping) |

### 3.3 Review API Endpoints (app.py)

Four REST endpoints, all guarded by `if not bot_state.awaiting_review: return 409`:

| Endpoint | Method | Request Body | Response | BotState Call |
|----------|--------|-------------|----------|---------------|
| `/api/bot/review/approve` | POST | (none) | `{"status": "approved"}` | `set_review_decision("approve")` |
| `/api/bot/review/skip` | POST | (none) | `{"status": "skipped"}` | `set_review_decision("skip")` |
| `/api/bot/review/edit` | POST | `{"cover_letter": "..."}` | `{"status": "edited"}` | `set_review_decision("edit", edits=data["cover_letter"])` |
| `/api/bot/review/manual` | POST | (none) | `{"status": "manual"}` | `set_review_decision("manual")` |

### 3.4 Analytics API Endpoints (app.py)

| Endpoint | Method | Query Params | Response |
|----------|--------|-------------|----------|
| `/api/analytics/summary` | GET | (none) | `{"total": int, "by_status": {status: count}, "by_platform": {platform: count}}` |
| `/api/analytics/daily` | GET | `?days=30` (default 30) | `[{"date": "YYYY-MM-DD", "count": int}, ...]` |

### 3.5 Analytics Data Layer (db/database.py)

| Method | Signature | SQL |
|--------|-----------|-----|
| `get_analytics_summary()` | `() -> dict` | Three queries: `COUNT(*)`, `GROUP BY status`, `GROUP BY platform` |
| `get_daily_analytics()` | `(days: int = 30) -> list[dict]` | `SELECT DATE(applied_at), COUNT(*) ... WHERE applied_at >= DATE('now', -N days) GROUP BY DATE(applied_at)` |

### 3.6 Chart Rendering (templates/index.html — client-side)

All charts use **Chart.js 4.4.1** loaded from CDN. Chart instances are stored in `chartInstances` dict and `destroy()`ed before re-creation to prevent memory leaks.

| Chart | Canvas ID | Type | Data Source | Colors |
|-------|-----------|------|-------------|--------|
| Applications Over Time | `chart-daily` | `line` | `/api/analytics/daily` | `#4da6ff` fill |
| Status Breakdown | `chart-status` | `doughnut` | `summary.by_status` | Per-status: applied=#4da6ff, interview=#53d769, offer=#ffc107, rejected=#e94560, error=#ff6b6b |
| By Platform | `chart-platform` | `bar` | `summary.by_platform` | `#4da6ff` bars |

Charts load on navigation to the analytics screen (`loadAnalytics()` called when `name === 'analytics'`).

### 3.7 Mode Selector (config/settings.py + templates/index.html)

`BotConfig.apply_mode` is a string field with three valid values:

| Value | Behavior |
|-------|----------|
| `"full_auto"` | Bot applies without pausing. No review gate. |
| `"review"` | Bot pauses before each submission. Review card shown. Headless browser. |
| `"watch"` | Same as review, plus browser launches in headed (visible) mode. |

The `<select id="apply-mode-select">` dropdown calls `changeApplyMode(value)` which PATCHes `/api/config` with `{ bot: { apply_mode: mode } }`. The `BrowserManager` checks `config.bot.apply_mode != "watch"` to set `self.headless`.

---

## 4. Interface Contracts

### 4.1 POST /api/bot/review/approve

**Purpose**: User approves the pending application for automated submission.
**Category**: command

**Request**: Empty body (or empty JSON).

**Response** (200):
```json
{"status": "approved"}
```

**Error** (409 — no pending review):
```json
{"error": "No application awaiting review"}
```

**Side Effects**: Calls `bot_state.set_review_decision("approve")`, which sets `_review_event`, unblocking the bot thread.

---

### 4.2 POST /api/bot/review/skip

**Purpose**: User skips the pending application.
**Category**: command

**Request**: Empty body.

**Response** (200):
```json
{"status": "skipped"}
```

**Error** (409): Same as 4.1.

**Side Effects**: Bot thread unblocks, emits `SKIPPED` event, moves to next job.

---

### 4.3 POST /api/bot/review/edit

**Purpose**: User edits the cover letter and then approves submission.
**Category**: command

**Request**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cover_letter` | `string` | yes | Edited cover letter text |

**Response** (200):
```json
{"status": "edited"}
```

**Error** (400 — missing field):
```json
{"error": "cover_letter is required"}
```

**Error** (409): Same as 4.1.

**Side Effects**: Bot thread unblocks with `decision="edit"` and `edits=<edited text>`. The bot replaces the generated cover letter with the edited version before applying.

---

### 4.4 POST /api/bot/review/manual

**Purpose**: User marks application for manual submission (they will apply themselves).
**Category**: command

**Request**: Empty body.

**Response** (200):
```json
{"status": "manual"}
```

**Error** (409): Same as 4.1.

**Side Effects**: Bot saves application with `status="manual_required"` and moves to next job. No automated form submission occurs.

---

### 4.5 GET /api/analytics/summary

**Purpose**: Aggregate application statistics.
**Category**: query

**Request**: No parameters.

**Response** (200):
```json
{
  "total": 142,
  "by_status": {
    "applied": 120,
    "error": 15,
    "manual_required": 7
  },
  "by_platform": {
    "linkedin": 80,
    "greenhouse": 35,
    "lever": 27
  }
}
```

---

### 4.6 GET /api/analytics/daily

**Purpose**: Daily application counts over a time window.
**Category**: query

**Request**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | `int` (query) | 30 | Number of days to look back |

**Response** (200):
```json
[
  {"date": "2026-02-10", "count": 5},
  {"date": "2026-02-11", "count": 3}
]
```

---

## 5. Data Flow

### 5.1 Review Flow (end-to-end)

```
1. Bot thread: _generate_docs() produces resume + cover letter
2. Bot thread: checks config.bot.apply_mode in ("review", "watch")
3. Bot thread: emit("REVIEW", job_title=..., cover_letter=..., match_score=..., apply_url=...)
4. Bot thread: calls _wait_for_review(state)
   └─ state.begin_review()  →  clears Event, sets awaiting_review=True
   └─ state.wait_for_review()  →  blocks on _review_event.wait()
5. SocketIO delivers "feed_event" with type="REVIEW" to browser
6. Browser: handleFeedEvent() detects type === "REVIEW", calls showReviewCard(evt)
   └─ Populates #review-job-title, #review-platform, #review-score, #review-cover-letter
   └─ Unhides #review-card
7. User clicks one of four buttons → JS function fires POST to /api/bot/review/{action}
8. Flask route: validates bot_state.awaiting_review, calls set_review_decision(decision, edits?)
   └─ Sets _review_decision, clears _awaiting_review, calls _review_event.set()
9. Bot thread: unblocks from wait_for_review(), returns (decision, edits)
10. Bot thread: branches on decision — apply, skip, save-as-manual, or stop
11. Bot thread: emits APPLIED/SKIPPED/ERROR event
12. Browser: handleFeedEvent() hides review card on APPLIED/SKIPPED/ERROR
```

### 5.2 Analytics Flow

```
1. User navigates to Analytics screen
2. loadAnalytics() fires two parallel fetches:
   └─ GET /api/analytics/summary
   └─ GET /api/analytics/daily?days=30
3. Flask routes call db.get_analytics_summary() and db.get_daily_analytics(30)
4. SQLite queries aggregate the applications table
5. JSON responses returned to browser
6. renderDailyChart(), renderStatusChart(), renderPlatformChart() create Chart.js instances
```

### 5.3 Review Event via SocketIO

The REVIEW feed event carries extra fields beyond the standard feed event:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"REVIEW"` | Event type identifier |
| `job_title` | `str` | Job title |
| `company` | `str` | Company name |
| `platform` | `str` | Source platform |
| `match_score` | `int` | Filter score (0-100) |
| `cover_letter` | `str` | Generated cover letter text |
| `apply_url` | `str` | Direct application URL |
| `message` | `str` | Human-readable summary |

---

## 6. State Machine

### 6.1 Bot Status States

```
                    start()
         ┌────────────────────┐
         │                    ▼
     ┌────────┐         ┌─────────┐
     │STOPPED │         │ RUNNING │◄────────┐
     └────────┘         └────┬────┘         │
         ▲                   │              │
         │ stop()            │ pause()      │ resume()
         │                   ▼              │
         │              ┌─────────┐         │
         └──────────────│ PAUSED  │─────────┘
                        └─────────┘
```

### 6.2 Review Gate Interaction with Bot Status

The review gate is orthogonal to the main status state machine but has critical interactions:

| Scenario | Behavior |
|----------|----------|
| Bot RUNNING + awaiting_review=True | Bot thread blocked on `_review_event.wait()`. Status remains "running". |
| User calls `stop()` while awaiting_review | `stop()` sets `_stop_flag=True`, calls `_review_event.set()`. Bot thread unblocks, sees `_stop_flag`, returns `("stop", None)`. Bot exits loop. |
| User calls `pause()` while awaiting_review | Status changes to "paused" but bot thread stays blocked on `_review_event.wait()` (not on `_wait_while_paused`). User must still make a review decision to unblock. |
| User sends review decision after `stop()` | `awaiting_review` is already `False` (cleared by `stop()`). API returns 409. |

### 6.3 awaiting_review Lifecycle

```
False ──[begin_review()]──▶ True ──[set_review_decision()]──▶ False
                                    or
                            True ──[stop()]──▶ False (via Event.set())
```

The `get_status_dict()` method exposes `awaiting_review` so the frontend can poll and hide the review card if the bot is no longer awaiting review (e.g., after a stop).

---

## 7. Architecture Decision Records

### ADR-012b: Blocking Review Gate via threading.Event

**Status**: accepted
**Context**: The bot thread must pause mid-pipeline (after document generation, before application submission) and wait for a user decision. Options considered:
1. **Polling loop** — bot thread polls `_review_decision` every N seconds.
2. **threading.Event** — bot thread blocks on `Event.wait()`, unblocked by `Event.set()`.
3. **Queue** — bot reads from a `queue.Queue`, frontend pushes decisions.

**Decision**: Use `threading.Event` with a lock-protected decision field.

**Rationale**:
- Zero-latency unblocking: `Event.set()` wakes the blocked thread immediately, unlike polling which introduces up to N seconds of delay.
- Simpler than Queue for a single-decision-at-a-time pattern.
- `stop()` can call `Event.set()` to unblock the bot thread during shutdown, preventing deadlock.
- No CPU burn from a polling loop.

**Consequences**:
- Only one review can be pending at a time (sufficient for sequential bot loop).
- The `_stop_flag` must be checked after `Event.wait()` returns to distinguish stop from a real decision.
- `pause()` does not unblock the review gate — the user must still decide before the bot can respond to pause. This is acceptable because the bot thread is already idle.

---

### ADR-013b: Client-Side Charting with Chart.js CDN

**Status**: accepted
**Context**: The analytics screen needs three chart types. Options:
1. **Server-side rendering** (matplotlib → PNG) — generates images on backend.
2. **Chart.js (client-side)** — JavaScript charting library, loaded from CDN.
3. **D3.js** — low-level, highly customizable, heavier.

**Decision**: Use Chart.js 4.4.1 from `cdnjs.cloudflare.com` CDN.

**Rationale**:
- Lightweight (~60KB gzipped) with built-in line, doughnut, and bar chart types — all three needed.
- Responsive and interactive out of the box (tooltips, hover effects).
- No build step required — loaded via `<script>` tag, consistent with the vanilla JS SPA approach (no framework).
- CDN avoids bundling a large library in the Electron app.
- Chart instances are tracked in `chartInstances` dict and `destroy()`ed before re-creation to prevent canvas memory leaks.

**Consequences**:
- Requires internet access on first load (CDN). Subsequent loads may be cached.
- Charts are destroyed and re-created on each analytics screen visit (stateless).
- Limited to Chart.js's built-in chart types (sufficient for current needs).

---

## 8. Design Traceability Matrix

| Requirement | Type | Design Component | Source Files | Interface / Method | ADR |
|-------------|------|-----------------|--------------|-------------------|-----|
| FR-053 | FR | Review gate — mode selector, review card, review endpoints, bot blocking | `config/settings.py`, `bot/state.py`, `bot/bot.py`, `app.py`, `templates/index.html` | `BotConfig.apply_mode`, `BotState.begin_review()`, `set_review_decision()`, `wait_for_review()`, `_wait_for_review()`, `POST /api/bot/review/*` | ADR-012b |
| FR-054 | FR | Watch mode — headed browser | `bot/browser.py`, `config/settings.py` | `BrowserManager.__init__()` checks `config.bot.apply_mode != "watch"` | — |
| FR-055 | FR | Analytics dashboard — charts | `db/database.py`, `app.py`, `templates/index.html` | `get_analytics_summary()`, `get_daily_analytics()`, `GET /api/analytics/*`, `loadAnalytics()`, `renderDailyChart()`, `renderStatusChart()`, `renderPlatformChart()` | ADR-013b |
| FR-056 | FR | Cover letter modal for past applications | `templates/index.html` | Application detail view, cover letter display | — |

---

## 9. Security Considerations

| Concern | Mitigation |
|---------|-----------|
| Review endpoints accept no auth | Acceptable — app runs on `127.0.0.1`, local-only access (per ADR from Phase 1). |
| 409 guard on review endpoints | All four review routes check `bot_state.awaiting_review` before processing. Prevents race conditions from stale UI. |
| SQL injection in analytics queries | All queries use parameterized statements (`?` placeholders). |
| XSS in review card | `escHtml()` applied to all dynamic text inserted into the review card DOM. |
| Chart.js CDN integrity | Loaded from `cdnjs.cloudflare.com` (trusted CDN). No SRI hash currently — acceptable for a local desktop app. |

---

## 10. Performance Notes

| Aspect | Detail |
|--------|--------|
| Bot thread blocking | `threading.Event.wait()` is OS-level blocking — zero CPU cost while waiting. |
| Analytics queries | Aggregate queries on SQLite. Fast for expected volumes (<10k rows). No indexing needed. |
| Chart rendering | Client-side, ~50ms for small datasets. `destroy()` prevents canvas accumulation. |
| SocketIO REVIEW event | Single event per review cycle. No high-frequency traffic. |
