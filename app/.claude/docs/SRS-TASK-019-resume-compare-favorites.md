# Software Requirements Specification

**Document ID**: SRS-TASK-019-resume-compare-favorites
**Version**: 1.0
**Date**: 2026-03-11
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD-TASK-019

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies requirements for resume comparison and favorites features extending
the Resume Library (TASK-018).

### 1.2 Scope
The system SHALL allow users to star/unstar resume versions and compare two
versions side by side with line-level diff highlighting.

### 1.3 Definitions

| Term       | Definition                                                    |
|------------|---------------------------------------------------------------|
| Favorite   | A resume version marked with is_favorite=1 in the database   |
| Diff       | Line-by-line comparison showing added, removed, and unchanged lines |

---

## 2. Constraints

| Type      | Constraint                                | Rationale              |
|-----------|-------------------------------------------|------------------------|
| Technical | No new Python or JS dependencies          | Bundle size control    |
| Technical | Diff computed client-side in vanilla JS   | No server load for diff|
| Technical | Schema change via additive ALTER TABLE    | Backward compatible    |

---

## 3. Functional Requirements

### FR-120: Favorite Toggle API

**Description**: The system shall provide an API endpoint to toggle the favorite
status of a resume version.

**Priority**: P0
**Source**: US-097
**Dependencies**: FR-110

**Acceptance Criteria**:

- **AC-120-1**: Given a resume version with id=5 and is_favorite=0,
  When `PUT /api/resumes/5/favorite` is called,
  Then is_favorite is set to 1 and the response contains `{"is_favorite": true}`.

- **AC-120-2**: Given a resume version with id=5 and is_favorite=1,
  When `PUT /api/resumes/5/favorite` is called,
  Then is_favorite is set to 0 and the response contains `{"is_favorite": false}`.

**Negative Cases**:
- **AC-120-N1**: Given no resume version with id=999 exists,
  When `PUT /api/resumes/999/favorite` is called,
  Then the response is 404.

---

### FR-121: Favorite Status in Resume List

**Description**: The system shall include the is_favorite field in resume list
and detail API responses, and support sorting favorites first.

**Priority**: P0
**Source**: US-097
**Dependencies**: FR-120

**Acceptance Criteria**:

- **AC-121-1**: Given resume versions exist with varying favorite status,
  When `GET /api/resumes` is called,
  Then each item includes an `is_favorite` boolean field.

- **AC-121-2**: Given starred and unstarred versions exist,
  When `GET /api/resumes?sort=is_favorite&order=desc` is called,
  Then starred versions appear first.

- **AC-121-3**: Given a resume version detail is requested,
  When `GET /api/resumes/<id>` is called,
  Then the response includes `is_favorite`.

---

### FR-122: Favorite UI Toggle

**Description**: The system shall display a clickable star icon on each resume
version row and in the detail view to toggle favorite status.

**Priority**: P0
**Source**: US-097
**Dependencies**: FR-120, FR-121

**Acceptance Criteria**:

- **AC-122-1**: Given the resume library list is displayed,
  When each row renders,
  Then a star icon appears, filled for favorites and outlined for non-favorites.

- **AC-122-2**: Given a user clicks the star icon on a row,
  When the click is processed,
  Then the API is called to toggle favorite and the icon updates without full page reload.

- **AC-122-3**: Given the resume detail view is open,
  When a star icon is displayed,
  Then it reflects the current favorite status and is clickable.

- **AC-122-4**: Given a user navigates via keyboard to the star icon,
  When they press Enter or Space,
  Then the favorite toggles.

---

### FR-123: Resume Comparison API

**Description**: The system shall provide an API endpoint that returns the
markdown content of two resume versions for client-side comparison.

**Priority**: P0
**Source**: US-095
**Dependencies**: FR-112

**Acceptance Criteria**:

- **AC-123-1**: Given two resume versions with ids 3 and 7,
  When `GET /api/resumes/compare?left=3&right=7` is called,
  Then the response contains both versions' metadata and markdown content.

- **AC-123-2**: Given both markdown files exist on disk,
  When the comparison data is returned,
  Then each side includes: id, company, job_title, created_at, and resume_md_content.

**Negative Cases**:
- **AC-123-N1**: Given left or right id does not exist,
  When the comparison is requested,
  Then the response is 404 with an error message.

- **AC-123-N2**: Given a markdown file is missing from disk,
  When the comparison is requested,
  Then that side's resume_md_content is null and file_missing is true.

---

### FR-124: Resume Comparison UI

**Description**: The system shall display a side-by-side comparison view with
line-level diff highlighting when two resume versions are selected.

**Priority**: P0
**Source**: US-095
**Dependencies**: FR-123

**Acceptance Criteria**:

- **AC-124-1**: Given the resume library list is displayed,
  When checkboxes appear on each row,
  Then users can select exactly 2 versions for comparison.

- **AC-124-2**: Given 2 versions are selected,
  When the user clicks the "Compare" button,
  Then a side-by-side view opens showing both markdown contents with diff highlighting.

- **AC-124-3**: Given the diff is displayed,
  When lines differ between versions,
  Then added lines are highlighted green, removed lines red, and unchanged lines neutral.

- **AC-124-4**: Given fewer than 2 versions are selected,
  When the user looks at the Compare button,
  Then it is disabled with an appropriate tooltip.

- **AC-124-5**: Given the comparison view is open,
  When the user clicks "Back to Library",
  Then the comparison view closes and the library list is restored.

**Negative Cases**:
- **AC-124-N1**: Given one or both markdown files are missing,
  When the comparison view opens,
  Then a message indicates which file is missing.

---

### FR-125: Comparison Selection Logic

**Description**: The system shall limit comparison selection to exactly 2 versions
and auto-deselect the oldest when a 3rd is chosen.

**Priority**: P0
**Source**: US-095
**Dependencies**: FR-124

**Acceptance Criteria**:

- **AC-125-1**: Given 2 versions are already selected,
  When the user selects a 3rd,
  Then the first-selected version is deselected and the new one is added.

- **AC-125-2**: Given the user deselects one of 2 selected versions,
  When 1 remains selected,
  Then the Compare button becomes disabled.

---

## 4. Non-Functional Requirements

### NFR-019-01: Performance
Diff computation for two 100-line markdown files shall complete in < 100ms on client side.
**Validation**: Manual test with 100-line files.

### NFR-019-02: Internationalization
All new strings via `t()` / `data-i18n`. Zero hardcoded English.
**Validation**: Code review.

### NFR-019-03: Accessibility
Star icons, checkboxes, and comparison view shall be keyboard accessible with
ARIA labels. Diff colors shall have sufficient contrast and not rely on color alone.
**Validation**: Manual a11y review.

### NFR-019-04: Test Coverage
All new backend methods and endpoints shall have unit and integration tests.
**Validation**: pytest.

---

## 5. Out of Scope
- Semantic/section-level diff
- Comparing 3+ versions simultaneously
- Exporting comparison as PDF
- AI-powered diff summary

---

## SRS — GATE 3 OUTPUT

**Document**: SRS-TASK-019
**FRs**: 6 (FR-120 to FR-125)
**NFRs**: 4 (NFR-019-01 to NFR-019-04)
**ACs**: 20 total (16 positive + 4 negative)
