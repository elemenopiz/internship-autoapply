# System Architecture Document

**Document ID**: SAD-TASK-007-app-detail-view
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-007-app-detail-view

---

## 1. Executive Summary

This architecture adds an application detail view to AutoApply's dashboard, allowing users to click on any application row to see full details, related feed events, and edit status/notes inline. The changes span three layers: a new `get_feed_events_for_job()` database method, three new/updated API endpoints in `app.py`, and a detail modal in the frontend.

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────────────┐
│  templates/index.html │
│  ┌──────────────────┐ │
│  │ Applications     │ │
│  │ Table (existing) │ │    click row
│  │ ┌──────────────┐ │ │ ──────────────┐
│  │ │  row click   │ │ │               │
│  │ └──────────────┘ │ │               ▼
│  └──────────────────┘ │    ┌──────────────────┐
│  ┌──────────────────┐ │    │ modal-app-detail  │
│  │ Detail Modal     │◀├────│ viewApplicationDe-│
│  │ (new)            │ │    │ tail()            │
│  │ - Status badge   │ │    └──────────────────┘
│  │ - Notes editor   │ │
│  │ - Event timeline │ │
│  └──────────────────┘ │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐      ┌──────────────────┐
│   app.py              │      │  db/database.py   │
│ GET /applications/:id │─────▶│ get_application() │
│ GET /applications/:id/│─────▶│ get_feed_events_  │
│     events            │      │ for_job()         │
│ PATCH /applications/  │─────▶│ update_           │
│       :id             │      │ application()     │
└──────────────────────┘      └──────────────────┘
```

### 2.2 Data Flow

1. User clicks an application row in the dashboard table.
2. `event.stopPropagation()` prevents click when user interacts with status dropdown or notes within the row (interactive cells).
3. `viewApplicationDetail(appId)` is called.
4. Frontend fetches `GET /api/applications/{id}` to load application details.
5. Frontend fetches `GET /api/applications/{id}/events` to load related feed events.
6. Detail modal (`modal-app-detail`) is populated and shown.
7. User can update status via `updateDetailStatus()` or save notes via `saveDetailNotes()`.
8. Changes sent via `PATCH /api/applications/{id}`.

### 2.3 Layer Architecture

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| UI | `templates/index.html` | Detail modal, clickable rows, status/notes editing |
| API | `app.py` | GET detail, GET events, PATCH update endpoints |
| Data | `db/database.py` | `get_application()`, `get_feed_events_for_job()`, `update_application()` |

---

## 3. Interface Contracts

### 3.1 db.database.Database.get_feed_events_for_job()

**Purpose**: Retrieve feed events related to a specific job by matching job_title and company.
**Category**: query

**Signature**:
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| job_title | `str` | yes | — | Job title to match |
| company | `str` | yes | — | Company name to match |
| limit | `int` | no | 50 | Max events to return |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `list[FeedEvent]` | Feed events matching job+company, ordered by `created_at DESC, id DESC` |

**SQL**:
```sql
SELECT * FROM feed_events
WHERE job_title = ? AND company = ?
ORDER BY created_at DESC, id DESC
LIMIT ?
```

**Errors**: None raised — returns empty list if no matches.
**Thread Safety**: Safe — uses connection-per-call pattern.

---

### 3.2 GET /api/applications/<int:app_id>

**Purpose**: Retrieve full details for a single application.
**Category**: query

**Output** (200):
```json
{
  "id": 42,
  "job_title": "Software Engineer",
  "company": "Acme Corp",
  "platform": "linkedin",
  "apply_url": "https://linkedin.com/jobs/12345",
  "status": "applied",
  "notes": "Submitted via Easy Apply",
  "applied_at": "2026-03-10T14:30:00",
  "created_at": "2026-03-10T14:30:00"
}
```

**Errors**:
| Status | Condition | Body |
|--------|-----------|------|
| 404 | Application not found | `{"error": "Application not found"}` |

**Implementation**:
1. Call `db.get_application(app_id)`.
2. If `None`, return 404.
3. Return `application.model_dump()` via `jsonify`.

---

### 3.3 GET /api/applications/<int:app_id>/events

**Purpose**: Retrieve feed events associated with a specific application.
**Category**: query

**Output** (200):
```json
{
  "events": [
    {
      "id": 101,
      "event_type": "applied",
      "job_title": "Software Engineer",
      "company": "Acme Corp",
      "message": "Applied to Software Engineer at Acme Corp",
      "created_at": "2026-03-10T14:30:00"
    }
  ]
}
```

**Errors**:
| Status | Condition | Body |
|--------|-----------|------|
| 404 | Application not found | `{"error": "Application not found"}` |

**Implementation**:
1. Call `db.get_application(app_id)`.
2. If `None`, return 404.
3. Call `db.get_feed_events_for_job(app.job_title, app.company)`.
4. Return list of `event.model_dump()` dicts.

**Note**: Events are matched by `job_title` + `company`, not by application ID. This is because feed events are recorded independently during the bot loop and reference jobs by title/company rather than application ID.

---

### 3.4 PATCH /api/applications/<int:app_id>

**Purpose**: Partially update an application's status and/or notes.
**Category**: command

**Input**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | `str` | no | New status value |
| notes | `str` | no | Updated notes text |

**Output** (200):
```json
{
  "id": 42,
  "status": "interviewing",
  "notes": "Phone screen scheduled for March 12"
}
```

**Errors**:
| Status | Condition | Body |
|--------|-----------|------|
| 404 | Application not found | `{"error": "Application not found"}` |

**Implementation**:
1. Call `db.get_application(app_id)`.
2. If `None`, return 404.
3. Read `status` from request JSON, defaulting to `existing_app.status`.
4. Read `notes` from request JSON, defaulting to `existing_app.notes`.
5. Call `db.update_application(app_id, status=status, notes=notes)`.
6. Return updated fields.

**Design Note**: The endpoint fetches the existing application first and uses its current `status`/`notes` as defaults. This enables true partial updates — the caller can send just `{"status": "rejected"}` without needing to re-send the notes.

---

### 3.5 templates/index.html — Detail Modal

**Modal ID**: `modal-app-detail`

**JavaScript Functions**:
| Function | Purpose | Triggers |
|----------|---------|----------|
| `viewApplicationDetail(appId)` | Fetches app + events, populates and shows modal | Row click |
| `badgeClass(status)` | Returns CSS class for status badge coloring | Modal render |
| `updateDetailStatus(appId, newStatus)` | PATCH status change from modal dropdown | Status select change |
| `saveDetailNotes(appId)` | PATCH notes from modal textarea | Save notes button click |

**Row Click Behavior**:
- Each application table row gets a `click` handler calling `viewApplicationDetail(row.appId)`.
- Interactive cells (status dropdown, notes input, action buttons) use `event.stopPropagation()` to prevent the row click from firing when the user interacts with inline controls.

**Modal Layout**:
```
┌────────────────────────────────────────┐
│  [X] Application Detail                │
├────────────────────────────────────────┤
│  Job Title          Company            │
│  Platform           Applied At         │
│  Status: [dropdown]                    │
│  Apply URL: [link]                     │
├────────────────────────────────────────┤
│  Notes:                                │
│  ┌──────────────────────────────────┐  │
│  │  (textarea)                      │  │
│  └──────────────────────────────────┘  │
│  [Save Notes]                          │
├────────────────────────────────────────┤
│  Event Timeline:                       │
│  • 14:30 — Applied to ... at ...       │
│  • 14:29 — Generated resume for ...    │
│  • 14:28 — Found job: ... at ...       │
└────────────────────────────────────────┘
```

---

## 4. Data Model

### 4.1 Database Changes

No new tables or columns. The feature leverages existing tables:

| Table | Usage |
|-------|-------|
| `applications` | `get_application(id)` for detail view |
| `feed_events` | `get_feed_events_for_job(job_title, company)` for event timeline |

### 4.2 New Database Method

`get_feed_events_for_job()` is the only new method added to `Database`. It queries `feed_events` filtered by `job_title` and `company` with `ORDER BY created_at DESC, id DESC` to show the most recent events first. The secondary sort on `id` ensures deterministic ordering when multiple events share the same `created_at` timestamp.

---

## 5. Error Handling Strategy

| Scenario | Handling | User Impact |
|----------|----------|-------------|
| Application ID not found (GET detail) | 404 JSON response | Modal shows error message |
| Application ID not found (GET events) | 404 JSON response | Modal shows error message |
| Application ID not found (PATCH) | 404 JSON response | Save action shows error |
| No feed events for job | Empty events list returned | Modal shows "No events" message |
| Network error during fetch | Frontend catch block | Toast/alert notification |
| Invalid status value in PATCH | Accepted (no enum constraint in DB) | Stored as-is |
| Empty notes in PATCH | Saved as empty string | Notes cleared |

---

## 6. Architecture Decision Records

No new ADRs. This feature follows established patterns:
- API endpoints follow the Flask route conventions from Phase 1 (static routes before parameterized).
- Pydantic `.model_dump()` used before `jsonify()` (per Lessons Learned 10.3).
- Modal UI follows the existing vanilla JS SPA pattern.
- Database method follows existing connection-per-call pattern.

---

## 7. Design Traceability Matrix

| Requirement | Type | Design Component | Interface | ADR |
|-------------|------|-----------------|-----------|-----|
| FR-065 | FR | app.py | GET /api/applications/<id> | — |
| FR-066 | FR | app.py + db/database.py | GET /api/applications/<id>/events, get_feed_events_for_job() | — |
| FR-067 | FR | app.py | PATCH /api/applications/<id> with partial update | — |
| FR-068 | FR | templates/index.html | modal-app-detail, viewApplicationDetail(), badgeClass() | — |
| FR-068.1 | FR | templates/index.html | event.stopPropagation() on interactive cells | — |
| FR-068.2 | FR | templates/index.html | updateDetailStatus(), saveDetailNotes() | — |

---

## 8. Implementation Plan

| Order | Task ID | Description | Depends On | Size | FR Coverage |
|-------|---------|-------------|------------|------|-------------|
| 1 | IMPL-040 | Add `get_feed_events_for_job(job_title, company, limit)` to `db/database.py` | — | S | FR-066 |
| 2 | IMPL-041 | Add `GET /api/applications/<int:app_id>` endpoint to `app.py` | — | S | FR-065 |
| 3 | IMPL-042 | Add `GET /api/applications/<int:app_id>/events` endpoint to `app.py` | IMPL-040 | S | FR-066 |
| 4 | IMPL-043 | Update `PATCH /api/applications/<int:app_id>` to fetch existing app first, use defaults for partial update | — | S | FR-067 |
| 5 | IMPL-044 | Add detail modal (`modal-app-detail`) to `templates/index.html` with layout and styling | — | M | FR-068 |
| 6 | IMPL-045 | Add `viewApplicationDetail()`, `badgeClass()`, `updateDetailStatus()`, `saveDetailNotes()` JS functions | IMPL-041, IMPL-042, IMPL-043 | M | FR-068 |
| 7 | IMPL-046 | Add clickable rows with `event.stopPropagation()` on interactive cells | IMPL-045 | S | FR-068.1 |
| 8 | IMPL-047 | Unit tests for `get_feed_events_for_job()` — matching, ordering, limit | IMPL-040 | S | FR-066 |
| 9 | IMPL-048 | Integration tests for detail API endpoints — 200, 404, partial PATCH | IMPL-041, IMPL-042, IMPL-043 | M | FR-065, FR-066, FR-067 |
