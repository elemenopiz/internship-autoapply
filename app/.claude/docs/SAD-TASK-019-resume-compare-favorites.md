# System Architecture Document

**Document ID**: SAD-TASK-019-resume-compare-favorites
**Version**: 1.0
**Date**: 2026-03-11
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-019

---

## 1. Executive Summary

Adds `is_favorite` column to `resume_versions`, a toggle endpoint, a comparison
endpoint, client-side line diff in vanilla JS, star icons in the resume library,
and a side-by-side comparison overlay.

---

## 2. Schema Change

```sql
ALTER TABLE resume_versions ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0;
```

Applied via `CREATE TABLE IF NOT EXISTS` update (additive, backward compatible).
Existing rows get `is_favorite=0` by default.

---

## 3. Interface Contracts

### 3.1 PUT /api/resumes/<id>/favorite

**Purpose**: Toggle favorite status.

**Output**:
```json
{"id": 5, "is_favorite": true}
```

**Errors**: 404 if version not found.

### 3.2 GET /api/resumes/compare?left=<id>&right=<id>

**Purpose**: Return metadata + markdown content for two versions.

**Output**:
```json
{
  "left": {
    "id": 3, "company": "Google", "job_title": "SWE",
    "created_at": "2026-03-10", "resume_md_content": "# ...",
    "file_missing": false
  },
  "right": {
    "id": 7, "company": "Meta", "job_title": "SRE",
    "created_at": "2026-03-11", "resume_md_content": "# ...",
    "file_missing": false
  }
}
```

**Errors**: 404 if either id not found. 400 if left/right params missing.

### 3.3 Database.toggle_favorite(version_id) -> bool

```python
def toggle_favorite(self, version_id: int) -> bool | None:
    """Toggle is_favorite, returns new value. None if not found."""
```

### 3.4 Client-Side Diff Algorithm

Simple line-based diff using Longest Common Subsequence (LCS):
1. Split both markdown texts by `\n`
2. Compute LCS of lines
3. Walk both arrays: lines in LCS = unchanged, lines only in left = removed, lines only in right = added
4. Render with CSS classes: `.diff-added`, `.diff-removed`, `.diff-unchanged`

No external library needed — vanilla JS implementation (~40 lines).

---

## 4. Frontend Design

### 4.1 Star Icon in Resume List

Each row gets a star button before the company name:
```html
<button class="resume-star" aria-label="Toggle favorite"
        onclick="toggleFavorite(id)" aria-pressed="true/false">
  ★ / ☆
</button>
```

### 4.2 Comparison Checkboxes

Each row gets a checkbox:
```html
<input type="checkbox" class="resume-compare-check"
       data-id="5" aria-label="Select for comparison">
```

Compare button in controls bar, disabled when < 2 selected:
```html
<button id="resume-compare-btn" disabled
        onclick="compareSelected()" data-i18n="resumes.compare">Compare</button>
```

### 4.3 Comparison Overlay

Reuses the existing `resume-detail-overlay` pattern — replaces content with
two-column diff view.

### 4.4 New i18n Keys

- `resumes.compare` — "Compare"
- `resumes.compare_title` — "Resume Comparison"
- `resumes.compare_select` — "Select 2 resumes to compare"
- `resumes.compare_select_info` — "{count} of 2 selected"
- `resumes.favorite` — "Favorite"
- `resumes.unfavorite` — "Unfavorite"
- `resumes.sort_favorites` — "Favorites first"
- `resumes.diff_added` — "Added"
- `resumes.diff_removed` — "Removed"
- `resumes.diff_unchanged` — "Unchanged"
- `resumes.diff_legend` — "Diff Legend"
- `resumes.compare_file_missing` — "File not available for comparison"

---

## 5. ADR-024: Client-Side vs Server-Side Diff

**Status**: accepted
**Context**: Diff computation could happen on server (Python difflib) or client (JS).

**Decision**: Client-side diff in vanilla JS.

**Rationale**:
- Markdown files are small (< 5KB typically)
- Avoids adding server load for a read-only operation
- No new Python dependency needed
- Diff result doesn't need to be stored

---

## 6. Implementation Plan

| Order | Task     | Description                         | Depends On | Size | FR Coverage     |
|-------|----------|-------------------------------------|------------|------|-----------------|
| 1     | IMPL-001 | Add is_favorite column to schema    | —          | S    | FR-120          |
| 2     | IMPL-002 | DB methods: toggle + update queries | IMPL-001   | S    | FR-120, FR-121  |
| 3     | IMPL-003 | API endpoints: favorite + compare   | IMPL-002   | M    | FR-120, FR-123  |
| 4     | IMPL-004 | i18n keys in en.json                | —          | S    | NFR-019-02      |
| 5     | IMPL-005 | Frontend: star icons + toggles      | IMPL-003   | M    | FR-122          |
| 6     | IMPL-006 | Frontend: checkboxes + compare btn  | IMPL-003   | S    | FR-124, FR-125  |
| 7     | IMPL-007 | Frontend: diff algorithm + view     | IMPL-006   | M    | FR-124          |
| 8     | IMPL-008 | CSS for diff + star                 | IMPL-007   | S    | NFR-019-03      |
| 9     | IMPL-009 | Unit + integration tests            | 001-003    | M    | NFR-019-04      |

---

## Design Traceability

| Requirement | Design Component          | Interface                        |
|-------------|---------------------------|----------------------------------|
| FR-120      | db/database.py, routes    | toggle_favorite(), PUT /favorite |
| FR-121      | db/database.py            | get_resume_versions() updated    |
| FR-122      | static/js/resumes.js      | toggleFavorite()                 |
| FR-123      | routes/resumes.py         | GET /compare                     |
| FR-124      | static/js/resumes.js      | compareSelected(), renderDiff()  |
| FR-125      | static/js/resumes.js      | selection logic                  |
