# System Architecture Document

**Document ID**: SAD-TASK-018-resume-versioning
**Version**: 1.0
**Date**: 2026-03-11
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-018-resume-versioning

---

## 1. Executive Summary

Resume Versioning adds a `resume_versions` table to track AI-generated resume metadata,
a new Flask Blueprint (`routes/resumes.py`) with 4 API endpoints, a frontend ES module
(`static/js/resumes.js`) for the Resume Library screen, and a modification to
`core/ai_engine.py` to record version metadata at generation time.

---

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  resumes.js      │────▶│  routes/         │────▶│  db/database.py  │
│  (UI module)     │     │  resumes.py      │     │  (resume_versions│
│  [Vanilla JS]    │     │  [Flask BP]      │     │   table)         │
└──────────────────┘     └──────────────────┘     └──────────────────┘
        │                        │                         │
        ▼                        ▼                         ▼
  [navigation.js]          [filesystem]              [SQLite DB]
  [app-detail.js]       (~/.autoapply/profile/
                         resumes/*.pdf/*.md)
```

### 2.2 Data Flow

1. **Recording**: `generate_documents()` → saves files → calls `db.save_resume_version()` → inserts row
2. **Listing**: Frontend → `GET /api/resumes` → `db.get_resume_versions()` → JOIN with applications → JSON response
3. **Detail**: Frontend → `GET /api/resumes/<id>` → `db.get_resume_version()` → read .md file from disk → JSON response
4. **PDF serve**: Frontend → `GET /api/resumes/<id>/pdf` → path validation → `send_file()` → PDF stream
5. **Metrics**: Frontend → `GET /api/resumes/metrics` → `db.get_resume_metrics()` → aggregate query → JSON response

### 2.3 Layer Architecture

| Layer          | Component                   | Responsibility                        |
|----------------|-----------------------------|---------------------------------------|
| Presentation   | `static/js/resumes.js`      | Resume Library UI, detail view        |
| Presentation   | `templates/index.html`      | Screen container HTML                 |
| Service        | `routes/resumes.py`         | HTTP endpoints, param validation      |
| Domain         | `db/models.py`              | `ResumeVersion` Pydantic model        |
| Repository     | `db/database.py`            | SQL queries for resume_versions       |
| Infrastructure | Filesystem                  | Resume .pdf/.md file storage          |

---

## 3. Interface Contracts

### 3.1 GET /api/resumes

**Purpose**: Return paginated list of resume versions with application status.

**Input Parameters**:
| Parameter | Type | Required | Constraints           | Description                |
|-----------|------|----------|-----------------------|----------------------------|
| page      | int  | no       | default: 1, min: 1    | Page number                |
| per_page  | int  | no       | default: 50, max: 100 | Items per page             |
| search    | str  | no       | max 100 chars         | Filter by company/job_title|
| sort      | str  | no       | default: created_at   | Sort column                |
| order     | str  | no       | default: desc         | asc or desc                |

**Output**:
```json
{
  "items": [
    {
      "id": 1,
      "application_id": 42,
      "job_title": "Software Engineer",
      "company": "Google",
      "match_score": 85,
      "llm_provider": "anthropic",
      "llm_model": "claude-sonnet-4-20250514",
      "created_at": "2026-03-10 14:30:00",
      "application_status": "interview",
      "resume_pdf_exists": true
    }
  ],
  "total_count": 75,
  "page": 1,
  "per_page": 50
}
```

**Errors**:
| Condition       | HTTP Status | Error                   |
|-----------------|-------------|-------------------------|
| DB not ready    | 503         | Database not initialized|

### 3.2 GET /api/resumes/<id>

**Purpose**: Return full detail of a single resume version including markdown content.

**Output**:
```json
{
  "id": 1,
  "application_id": 42,
  "job_title": "Software Engineer",
  "company": "Google",
  "match_score": 85,
  "llm_provider": "anthropic",
  "llm_model": "claude-sonnet-4-20250514",
  "created_at": "2026-03-10 14:30:00",
  "application_status": "interview",
  "resume_md_content": "# John Doe\n...",
  "resume_pdf_exists": true,
  "file_missing": false
}
```

**Errors**:
| Condition       | HTTP Status | Error                   |
|-----------------|-------------|-------------------------|
| Not found       | 404         | Resume version not found|
| Path traversal  | 403         | Access denied           |

### 3.3 GET /api/resumes/<id>/pdf

**Purpose**: Serve resume PDF for inline viewing or download.

**Input Parameters**:
| Parameter | Type | Required | Constraints | Description            |
|-----------|------|----------|-------------|------------------------|
| download  | str  | no       | "true"      | Force download headers |

**Output**: PDF file stream with appropriate Content-Type and Content-Disposition.

**Errors**:
| Condition          | HTTP Status | Error                    |
|--------------------|-------------|--------------------------|
| Not found (record) | 404         | Resume version not found |
| Not found (file)   | 404         | Resume PDF file not found|
| Path traversal     | 403         | Access denied            |

### 3.4 GET /api/resumes/metrics

**Purpose**: Return aggregate resume effectiveness metrics.

**Output**:
```json
{
  "total_versions": 150,
  "tailored_interview_rate": 32.5,
  "fallback_interview_rate": 12.0,
  "avg_score_interviewed": 78.3,
  "avg_score_rejected": 52.1,
  "by_provider": [
    {
      "provider": "anthropic",
      "count": 100,
      "interview_rate": 35.0
    },
    {
      "provider": "openai",
      "count": 50,
      "interview_rate": 28.0
    }
  ]
}
```

### 3.5 Database.save_resume_version()

**Purpose**: Insert a resume version record after successful AI generation.

**Signature**:
```python
def save_resume_version(
    self,
    application_id: int,
    job_title: str,
    company: str,
    resume_md_path: str,
    resume_pdf_path: str,
    match_score: int,
    llm_provider: str | None,
    llm_model: str | None,
) -> int:
    """Returns the new resume_version id."""
```

### 3.6 Database.get_resume_versions()

**Purpose**: Query paginated resume versions joined with application status.

**Signature**:
```python
def get_resume_versions(
    self,
    page: int = 1,
    per_page: int = 50,
    search: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
) -> tuple[list[ResumeVersion], int]:
    """Returns (list of ResumeVersion, total_count)."""
```

**SQL**:
```sql
SELECT rv.*, a.status AS application_status
FROM resume_versions rv
LEFT JOIN applications a ON rv.application_id = a.id
WHERE (rv.company LIKE ? OR rv.job_title LIKE ?)  -- if search provided
ORDER BY rv.{sort} {order}
LIMIT ? OFFSET ?
```

### 3.7 Database.get_resume_version()

**Purpose**: Get single resume version by id.

**Signature**:
```python
def get_resume_version(self, version_id: int) -> dict | None:
    """Returns dict with all fields + application_status, or None."""
```

### 3.8 Database.get_resume_metrics()

**Purpose**: Compute aggregate effectiveness metrics.

**SQL**:
```sql
-- Total versions
SELECT COUNT(*) FROM resume_versions;

-- Tailored interview rate
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN a.status IN ('interview','interviewing','interviewed','offer','accepted')
        THEN 1 ELSE 0 END) AS positive
FROM resume_versions rv
JOIN applications a ON rv.application_id = a.id;

-- Fallback rate (applications with resume_path but no resume_version)
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN a.status IN ('interview','interviewing','interviewed','offer','accepted')
        THEN 1 ELSE 0 END) AS positive
FROM applications a
LEFT JOIN resume_versions rv ON a.id = rv.application_id
WHERE rv.id IS NULL AND a.resume_path IS NOT NULL;

-- Avg scores
SELECT
    AVG(CASE WHEN a.status IN ('interview','interviewing','interviewed','offer','accepted')
        THEN rv.match_score END) AS avg_interviewed,
    AVG(CASE WHEN a.status = 'rejected' THEN rv.match_score END) AS avg_rejected
FROM resume_versions rv
JOIN applications a ON rv.application_id = a.id;

-- By provider
SELECT
    rv.llm_provider,
    COUNT(*) AS count,
    SUM(CASE WHEN a.status IN ('interview','interviewing','interviewed','offer','accepted')
        THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS interview_rate
FROM resume_versions rv
JOIN applications a ON rv.application_id = a.id
WHERE rv.llm_provider IS NOT NULL
GROUP BY rv.llm_provider;
```

---

## 4. Data Model

### 4.1 Entity Definition

#### ResumeVersion (new table: `resume_versions`)

| Field           | Type     | Constraints                   | Description              |
|-----------------|----------|-------------------------------|--------------------------|
| id              | INTEGER  | PK, autoincrement             | Primary identifier       |
| application_id  | INTEGER  | FK → applications(id), NOT NULL | Associated application |
| job_title       | TEXT     | NOT NULL                      | Target job title         |
| company         | TEXT     | NOT NULL                      | Target company           |
| resume_md_path  | TEXT     | NOT NULL                      | Path to markdown file    |
| resume_pdf_path | TEXT     | NOT NULL                      | Path to PDF file         |
| match_score     | INTEGER  |                               | Job match score (0-100)  |
| llm_provider    | TEXT     |                               | AI provider used         |
| llm_model       | TEXT     |                               | AI model used            |
| created_at      | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Generation time    |

**Indexes**:
| Index Name                    | Columns        | Type   | Rationale                    |
|-------------------------------|----------------|--------|------------------------------|
| idx_rv_app_id                 | application_id | B-tree | FK lookup, JOIN performance   |
| idx_rv_created_at             | created_at     | B-tree | Sort by date                 |

### 4.2 Relationships
```
applications ──1:N──▶ resume_versions (via resume_versions.application_id)
```
Note: Typically 1:1 in practice (one resume per application), but 1:N allows
for future re-generation capability.

### 4.3 Pydantic Model

```python
class ResumeVersion(BaseModel):
    id: int
    application_id: int
    job_title: str
    company: str
    resume_md_path: str
    resume_pdf_path: str
    match_score: int | None
    llm_provider: str | None
    llm_model: str | None
    created_at: str
```

---

## 5. Schema Migration Strategy

The `resume_versions` table is added to `SCHEMA_SQL` in `db/database.py` using
`CREATE TABLE IF NOT EXISTS`. This is the existing pattern — no migration framework
is used. The table is additive-only; no existing tables are modified.

---

## 6. Frontend Design

### 6.1 New Module: `static/js/resumes.js`

Exports:
- `loadResumes()` — fetch and render resume library
- `loadResumeDetail(id)` — fetch and render single resume detail
- `switchResumePage(page)` — pagination handler

### 6.2 HTML Structure (added to `templates/index.html`)

```html
<div class="screen hidden" id="screen-resumes">
  <!-- Resume Library: search + sort controls -->
  <!-- Resume list table -->
  <!-- Pagination -->
  <!-- Detail overlay (shown when viewing a single resume) -->
</div>
```

### 6.3 Navigation Tab

Add to navbar `nav-tabs`:
```html
<a role="tab" data-screen="resumes" data-i18n="nav.resumes" tabindex="-1"
   aria-selected="false">Resume Library</a>
```

### 6.4 i18n Keys (new, under `resumes` section)

- `nav.resumes` — "Resume Library"
- `resumes.title` — "Resume Library"
- `resumes.search_placeholder` — "Search by company or job title..."
- `resumes.empty` — "No resume versions yet. Resumes are generated automatically when you apply to jobs."
- `resumes.col_company` — "Company"
- `resumes.col_job_title` — "Job Title"
- `resumes.col_score` — "Score"
- `resumes.col_date` — "Date"
- `resumes.col_status` — "Status"
- `resumes.col_provider` — "AI Provider"
- `resumes.col_actions` — "Actions"
- `resumes.view` — "View"
- `resumes.download` — "Download"
- `resumes.view_pdf` — "View PDF"
- `resumes.download_pdf` — "Download PDF"
- `resumes.view_application` — "View Application"
- `resumes.back_to_library` — "Back to Library"
- `resumes.file_missing` — "Resume file not found on disk"
- `resumes.metadata` — "Resume Details"
- `resumes.generated_on` — "Generated on"
- `resumes.ai_model` — "AI Model"
- `resumes.match_score` — "Match Score"
- `resumes.metrics_title` — "Resume Effectiveness"
- `resumes.total_versions` — "Total Versions"
- `resumes.tailored_rate` — "Tailored Interview Rate"
- `resumes.fallback_rate` — "Fallback Interview Rate"
- `resumes.avg_score_interview` — "Avg Score (Interviewed)"
- `resumes.avg_score_rejected` — "Avg Score (Rejected)"
- `resumes.by_provider` — "By AI Provider"
- `resumes.loading` — "Loading resumes..."
- `resumes.error_loading` — "Error loading resumes"

### 6.5 CSS Additions (in `static/css/main.css`)

```css
.resume-library-controls { ... }  /* search + sort bar */
.resume-table { ... }             /* reuse .analytics-table styles */
.resume-detail-overlay { ... }    /* detail view panel */
.resume-detail-meta { ... }       /* metadata grid */
.resume-detail-content { ... }    /* markdown rendered content */
.resume-pdf-embed { ... }         /* iframe for PDF preview */
.resume-metrics-cards { ... }     /* effectiveness metrics grid */
```

---

## 7. Architecture Decision Records

### ADR-022: Resume Versions Table vs. Reusing applications.resume_path

**Status**: accepted
**Context**: Resume metadata (provider, model, match score) needs to be stored.
The existing `applications.resume_path` only stores the file path.

**Decision**: Create a new `resume_versions` table rather than adding columns to
`applications`.

**Alternatives Considered**:
| Option                    | Pros                        | Cons                            |
|---------------------------|-----------------------------|---------------------------------|
| Add columns to applications | No new table               | Schema change on existing table |
| **New resume_versions table** | **No ALTER TABLE, clean separation** | **Extra JOIN needed** |
| JSON metadata file on disk | No DB changes              | Fragile, no query support       |

**Consequences**:
- Positive: Clean separation of concerns, no risk to existing data
- Negative: JOIN required for listing (minimal perf impact on SQLite)
- Risks: None significant

### ADR-023: Resume Detail as Overlay vs. Separate Screen

**Status**: accepted
**Context**: Resume detail view needs to show metadata + rendered content + PDF.

**Decision**: Use an overlay panel within the resume library screen (similar to
how application detail works) rather than a completely separate screen.

**Alternatives Considered**:
| Option              | Pros                     | Cons                        |
|---------------------|--------------------------|-----------------------------|
| **Overlay panel**   | **Quick navigation, no screen switching** | **More complex JS** |
| Separate screen     | Simple routing           | Loses list context          |
| Modal dialog        | Familiar pattern         | Too small for PDF preview   |

**Consequences**:
- Positive: Users can quickly browse between resumes
- Negative: Slightly more complex state management

---

## 8. Design Traceability Matrix

| Requirement | Type | Design Component(s)         | Interface(s)                          | ADR   |
|-------------|------|-----------------------------|---------------------------------------|-------|
| FR-110      | FR   | db/database.py              | save_resume_version()                 | ADR-022|
| FR-111      | FR   | routes/resumes.py           | GET /api/resumes                      | —     |
| FR-112      | FR   | routes/resumes.py           | GET /api/resumes/<id>                 | —     |
| FR-113      | FR   | routes/resumes.py           | GET /api/resumes/<id>/pdf             | —     |
| FR-114      | FR   | routes/resumes.py           | GET /api/resumes/metrics              | —     |
| FR-115      | FR   | static/js/resumes.js        | loadResumes()                         | —     |
| FR-116      | FR   | static/js/resumes.js        | loadResumeDetail()                    | ADR-023|
| FR-117      | FR   | static/js/app-detail.js     | resume link button                    | —     |
| FR-118      | FR   | core/ai_engine.py           | generate_documents() modified         | —     |
| FR-119      | FR   | templates/index.html        | nav tab + screen div                  | —     |
| NFR-018-01  | NFR  | db/database.py              | Indexed queries                       | —     |
| NFR-018-02  | NFR  | routes/resumes.py           | _is_safe_path()                       | —     |
| NFR-018-03  | NFR  | All new code                | t() / data-i18n                       | —     |
| NFR-018-04  | NFR  | static/js/resumes.js + HTML | ARIA, keyboard nav                    | —     |
| NFR-018-05  | NFR  | tests/test_resume_versions.py | pytest                              | —     |
| NFR-018-06  | NFR  | All                         | Backward compat                       | —     |

---

## 9. Implementation Plan

| Order | Task ID  | Description                         | Depends On | Size | Risk   | FR Coverage           |
|-------|----------|-------------------------------------|------------|------|--------|-----------------------|
| 1     | IMPL-001 | Pydantic model + DB schema + methods| —          | M    | Low    | FR-110                |
| 2     | IMPL-002 | Routes blueprint (4 endpoints)      | IMPL-001   | M    | Low    | FR-111,112,113,114    |
| 3     | IMPL-003 | Register blueprint in app.py        | IMPL-002   | S    | Low    | FR-111                |
| 4     | IMPL-004 | Modify generate_documents()         | IMPL-001   | S    | Medium | FR-118                |
| 5     | IMPL-005 | i18n keys in en.json                | —          | S    | Low    | NFR-018-03            |
| 6     | IMPL-006 | HTML screen + nav tab               | IMPL-005   | M    | Low    | FR-115,119            |
| 7     | IMPL-007 | Frontend resumes.js module          | IMPL-006   | L    | Medium | FR-115,116            |
| 8     | IMPL-008 | App-detail resume link              | IMPL-007   | S    | Low    | FR-117                |
| 9     | IMPL-009 | CSS styles                          | IMPL-006   | S    | Low    | NFR-018-04            |
| 10    | IMPL-010 | Unit + integration tests            | 001-004    | M    | Low    | NFR-018-05            |

---

## System Architecture — GATE 4 OUTPUT

**Document**: SAD-TASK-018-resume-versioning
**Components**: 6 components defined
**Interfaces**: 8 contracts specified
**Entities**: 1 data entity modeled (ResumeVersion)
**ADRs**: 2 decisions documented (ADR-022, ADR-023)
**Impl Tasks**: 10 tasks in dependency order
**Traceability**: 16/16 requirements mapped (100%)

### Handoff Routing
| Recipient            | What They Receive                        |
|----------------------|------------------------------------------|
| Backend Developer    | Interface contracts §3.1-§3.8, data model §4, impl plan |
| Frontend Developer   | UI design §6, i18n keys, CSS specs       |
| Unit Tester          | Interface contracts for test generation  |
| Integration Tester   | API contracts, file I/O edge cases       |
| Security Engineer    | Path traversal requirements, file I/O    |
