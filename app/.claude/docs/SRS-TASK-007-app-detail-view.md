# Software Requirements Specification

**Document ID**: SRS-TASK-007-app-detail-view
**Version**: 1.0
**Date**: 2026-03-10
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Section 9.3, 10

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for AutoApply Phase 7 (Application Detail View): API endpoints for retrieving full application data and associated feed events, partial update support for application records, and a detail modal in the dashboard UI.

### 1.2 Scope
The system SHALL provide: a GET endpoint returning full application data by ID, a GET endpoint returning feed events associated with an application, enhanced PATCH endpoint supporting partial updates (notes-only or status-only), and a clickable detail modal in the dashboard showing all fields, editable status/notes, activity timeline, and action links.

The system SHALL NOT provide: application deletion, bulk status updates, single-application export, application merging or deduplication from this view.

### 1.3 Definitions
| Term | Definition |
|------|-----------|
| Application record | A row in the applications database table with 17 fields (id, job_title, company, location, salary, platform, apply_url, status, applied_at, resume_path, cover_letter_path, match_score, notes, external_id, description, error_message, ats_type) |
| Feed event | A real-time SocketIO event record with type, job_title, company, platform, message, and timestamp |
| Activity timeline | Chronological list of feed events associated with a specific application, ordered newest first |
| Detail modal | A modal dialog overlay displaying full application data with editing capabilities |

---

## 2. Functional Requirements

### FR-065: Get Application by ID

**Description**: The system SHALL expose `GET /api/applications/:id` returning the full application record with all 17 fields.
**Priority**: P0
**Dependencies**: Existing applications database table

**Acceptance Criteria**:
- **AC-065-1**: Given a valid application ID, When `GET /api/applications/:id` is called, Then it returns a JSON object with all 17 fields: id, job_title, company, location, salary, platform, apply_url, status, applied_at, resume_path, cover_letter_path, match_score, notes, external_id, description, error_message, ats_type.
- **AC-065-2**: Given an application ID that does not exist in the database, When `GET /api/applications/:id` is called, Then it returns 404 with an error message.
- **AC-065-3**: Given a valid application ID, When the response is returned, Then all nullable fields that are NULL in the database are serialized as JSON `null`.

---

### FR-066: Get Application Events

**Description**: The system SHALL expose `GET /api/applications/:id/events` returning feed events that match the application's `job_title` and `company`, ordered newest first.
**Priority**: P0
**Dependencies**: FR-065, FR-051 (Live Feed SocketIO Events)

**Acceptance Criteria**:
- **AC-066-1**: Given a valid application ID with matching feed events, When `GET /api/applications/:id/events` is called, Then it returns a JSON array of feed events where `job_title` and `company` match the application's values.
- **AC-066-2**: Given feed events exist for the application, When the response is returned, Then events are ordered by timestamp descending (newest first).
- **AC-066-3**: Given a valid application ID with no matching feed events, When `GET /api/applications/:id/events` is called, Then it returns an empty JSON array `[]`.
- **AC-066-4**: Given an application ID that does not exist, When `GET /api/applications/:id/events` is called, Then it returns 404 with an error message.

---

### FR-067: Partial Application Update

**Description**: The existing `PATCH /api/applications/:id` endpoint SHALL support partial updates, allowing the client to send notes-only, status-only, or both fields in the request body.
**Priority**: P0
**Dependencies**: Existing PATCH endpoint

**Acceptance Criteria**:
- **AC-067-1**: Given a valid application ID and a request body with only `notes`, When `PATCH /api/applications/:id` is called, Then only the `notes` field is updated; all other fields remain unchanged.
- **AC-067-2**: Given a valid application ID and a request body with only `status`, When `PATCH /api/applications/:id` is called, Then only the `status` field is updated; all other fields remain unchanged.
- **AC-067-3**: Given a valid application ID and a request body with both `notes` and `status`, When `PATCH /api/applications/:id` is called, Then both fields are updated.
- **AC-067-4**: Given an application ID that does not exist, When `PATCH /api/applications/:id` is called, Then it returns 404 with an error message.
- **AC-067-5**: Given a valid application ID, When the update succeeds, Then the response includes the updated application record.

---

### FR-068: Application Detail Modal

**Description**: The system SHALL display a detail modal when the user clicks on an application row in the dashboard table, showing all fields, editable status and notes, an activity timeline, and action links.
**Priority**: P1
**Dependencies**: FR-065, FR-066, FR-067

**Acceptance Criteria**:
- **AC-068-1**: Given the applications table is displayed, When the user clicks on a row, Then a modal dialog opens showing the full application details.
- **AC-068-2**: Given the detail modal is open, When the user views the modal, Then all 17 application fields are displayed with appropriate labels.
- **AC-068-3**: Given the detail modal is open, When the user views the status field, Then it is editable (e.g., dropdown or text input) and can be saved via the PATCH endpoint.
- **AC-068-4**: Given the detail modal is open, When the user views the notes field, Then it is editable (text area) and can be saved via the PATCH endpoint.
- **AC-068-5**: Given the detail modal is open, When the activity timeline section loads, Then it displays feed events from `GET /api/applications/:id/events` ordered newest first.
- **AC-068-6**: Given the application has an `apply_url`, When the user views the modal, Then a "View Posting" link is displayed that opens the URL in a new tab.
- **AC-068-7**: Given the application has a `resume_path`, When the user views the modal, Then a "Download Resume" link is displayed.
- **AC-068-8**: Given the application has a `cover_letter_path`, When the user views the modal, Then a "View Cover Letter" link is displayed.
- **AC-068-N1**: Given the application has no `resume_path` or `cover_letter_path`, When the user views the modal, Then the corresponding action links are hidden or disabled.

---

## 3. Non-Functional Requirements

### NFR-028: Application Existence Validation
**Description**: All application-specific endpoints (`GET /:id`, `GET /:id/events`, `PATCH /:id`) SHALL validate that the application exists before processing and return 404 with a descriptive error for non-existent IDs.
**Metric**: Every request with an invalid ID returns 404 within 100ms.
**Priority**: P0

---

## 4. Out of Scope

- **Application deletion** — No DELETE endpoint for applications
- **Bulk status updates** — No batch PATCH for multiple applications
- **Export single application** — No PDF or CSV export of individual application details
- **Application merging or deduplication** — Not handled from the detail view
- **Inline editing in the table view** — Editing only available in the detail modal
