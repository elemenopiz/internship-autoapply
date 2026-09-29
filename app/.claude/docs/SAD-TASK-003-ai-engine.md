# System Architecture Document

**Document ID**: SAD-TASK-003-ai-engine
**Version**: 1.0
**Date**: 2026-03-09
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-003-ai-engine

---

## 1. Executive Summary

This architecture adds two modules — `core/ai_engine.py` and `core/resume_renderer.py` — to AutoApply's existing foundation. The AI engine wraps Claude Code CLI invocations behind a clean Python API, while the renderer converts Markdown resumes to ATS-safe PDFs. A fallback path ensures the system degrades gracefully when Claude Code is unavailable.

## 2. Architecture Overview

### 2.1 Component Diagram

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   app.py     │─────▶│ ai_engine.py │─────▶│ Claude Code  │
│ (API layer)  │      │ (orchestrate)│      │   CLI        │
│ [Flask]      │      │ [subprocess] │      │ [external]   │
└──────────────┘      └──────┬───────┘      └──────────────┘
       │                     │
       │                     ▼
       │              ┌──────────────┐
       │              │ resume_      │
       │              │ renderer.py  │
       │              │ [ReportLab]  │
       │              └──────────────┘
       │                     │
       ▼                     ▼
 [config.json]    [~/.autoapply/profile/]
                  ├── experiences/*.txt
                  ├── resumes/*.md, *.pdf
                  └── cover_letters/*.txt
```

### 2.2 Data Flow

1. Caller (bot loop or test) invokes `generate_documents(job, profile, ...)`.
2. `read_all_experience_files()` reads all `.txt` files from experience directory (excluding README.txt).
3. `RESUME_PROMPT` template is populated with experience content, job description, and profile fields.
4. `invoke_claude_code(prompt)` spawns `claude --print <prompt>` subprocess.
5. Claude Code returns Markdown resume text via stdout.
6. `COVER_LETTER_PROMPT` template is populated and sent to Claude Code.
7. Claude Code returns cover letter plain text.
8. Resume Markdown saved to `.md` file.
9. `render_resume_to_pdf()` converts Markdown to PDF via ReportLab.
10. Cover letter saved to `.txt` file.
11. Returns `(resume_pdf_path, cover_letter_txt_path)`.

### 2.3 Layer Architecture

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| API | `app.py` | Exposes `claude_code_available` in bot status |
| Service | `core/ai_engine.py` | Orchestrates document generation |
| Infrastructure | `subprocess` | Invokes external Claude Code CLI |
| Infrastructure | `core/resume_renderer.py` | PDF rendering via ReportLab |
| Data | `~/.autoapply/profile/` | File I/O for experience and output files |

---

## 3. Interface Contracts

### 3.1 core.ai_engine.check_claude_code_available()

**Purpose**: Determine if Claude Code CLI is installed and responsive.
**Category**: query

**Signature**:
| Parameter | Type | Required | Constraints | Description |
|-----------|------|----------|-------------|-------------|
| (none) | — | — | — | — |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `bool` | `True` if `claude --version` exits with code 0 within 10s |

**Errors**: None — all exceptions caught internally.

**Side Effects**: Spawns and waits for a subprocess (≤10s).
**Thread Safety**: Safe — no shared state.

**Example**:
```python
available = check_claude_code_available()  # True or False
```

---

### 3.2 core.ai_engine.invoke_claude_code(prompt, timeout_seconds)

**Purpose**: Run Claude Code non-interactively and return output.
**Category**: command (external side effect)

**Signature**:
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| prompt | `str` | yes | — | Full prompt text to send |
| timeout_seconds | `int` | no | 120 | Max wait time in seconds |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `str` | Stripped stdout from Claude Code |

**Errors**:
| Condition | Error Type | Detail |
|-----------|-----------|--------|
| Non-zero exit code | `RuntimeError` | Includes exit code and stderr |
| Timeout exceeded | `RuntimeError` | Indicates timeout |
| Command not found | `RuntimeError` | Wrapped FileNotFoundError |

**Preconditions**: Claude Code CLI is in PATH (caller should check via FR-031 first).
**Thread Safety**: Safe — each call spawns an independent subprocess.

---

### 3.3 core.ai_engine.read_all_experience_files(experience_dir)

**Purpose**: Read and concatenate all experience `.txt` files.
**Category**: query

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| experience_dir | `Path` | yes | Directory containing `.txt` files |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `str` | Concatenated content with `=== filename ===` separators |

**Errors**: None raised — returns empty string if directory missing/empty. Logs warning for unreadable files.

---

### 3.4 core.ai_engine.generate_documents(...)

**Purpose**: Full orchestration — read files, call Claude Code twice, save outputs.
**Category**: saga

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| job | object | yes | Must have `.id` (str), `.raw.company` (str), `.raw.description` (str) |
| profile | `UserProfile` | yes | From config |
| experience_dir | `Path` | yes | Experience files directory |
| output_dir_resumes | `Path` | yes | Where to save resume files |
| output_dir_cover_letters | `Path` | yes | Where to save cover letter files |

**Output**:
| Field | Type | Description |
|-------|------|-------------|
| return | `tuple[Path, Path]` | `(resume_pdf_path, cover_letter_txt_path)` |

**Errors**:
| Condition | Error Type |
|-----------|-----------|
| Claude Code fails | `RuntimeError` |
| File write fails | `OSError` |

**Side Effects**: Creates 3 files (`.md`, `.pdf`, `.txt`). Two subprocess invocations.

---

### 3.5 core.resume_renderer.render_resume_to_pdf(resume_md_text, resume_pdf_path)

**Purpose**: Convert Markdown resume text to ATS-safe PDF.
**Category**: command (file write)

**Signature**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| resume_md_text | `str` | yes | Markdown-formatted resume |
| resume_pdf_path | `Path` | yes | Output PDF file path |

**Output**: None (writes file to disk).

**Errors**:
| Condition | Error Type |
|-----------|-----------|
| Invalid path | `OSError` |
| ReportLab error | `RuntimeError` |

**PDF Formatting Spec**:
| Element | Font | Size | Style |
|---------|------|------|-------|
| Name (H1 `#`) | Helvetica-Bold | 18pt | Bold |
| Contact line | Helvetica | 10pt | Normal, pipe-separated |
| Section header (H2 `##`) | Helvetica-Bold | 12pt | Bold + thin rule below |
| Subsection (H3 `###`) | Helvetica-Bold | 11pt | Bold |
| Body text | Helvetica | 10pt | Normal |
| Bullet `- ` | Helvetica | 10pt | Em-dash prefix, left indent |
| Margins | — | 0.75" | All sides |
| Colors | Black text on white only | — | — |

**Markdown Parsing Rules** (limited subset):
- `# text` → H1 (name)
- `## text` → H2 (section header with rule)
- `### text` → H3 (subsection)
- `- text` → Bullet point
- `**text**` → Bold span
- Everything else → body paragraph
- Blank lines → paragraph spacing
- `---` → Horizontal rule (thin line)

---

## 4. Data Model

No new database entities. File outputs only (see SRS §6.1).

### 4.1 Filename Convention

```
{job_id}_{company_slug}_{date}.{ext}

job_id:       UUID string from ScoredJob
company_slug: company name, spaces → hyphens, lowercased
date:         YYYY-MM-DD
ext:          md | pdf | txt
```

---

## 5. Error Handling Strategy

| Scenario | Handling | User Impact |
|----------|----------|-------------|
| Claude Code not installed | `check_claude_code_available()` returns False | Warning banner, fallback templates |
| Claude Code times out | `RuntimeError` raised after 120s | Bot logs error, skips this job |
| Claude Code returns error | `RuntimeError` with stderr | Bot logs error, skips this job |
| Experience directory empty | Returns empty string | Claude Code gets no experience context |
| PDF rendering fails | `RuntimeError` raised | Bot logs error, skips this job |
| File write permission denied | `OSError` raised | Bot logs error, skips this job |

---

## 6. Architecture Decision Records

### ADR-009: Claude Code via Subprocess (Not SDK/API)

**Status**: accepted
**Context**: Need to invoke Claude Code for document generation. Options: subprocess CLI, API SDK, or HTTP API.
**Decision**: Use subprocess with `--print` flag.
**Rationale**: User already has Claude Code CLI installed and authenticated. No API key management needed. Simplest integration path. PRD mandates this approach.
**Consequences**: Dependent on CLI interface stability. Cannot stream responses. Each call spawns a process.

### ADR-010: ReportLab for PDF Generation

**Status**: accepted
**Context**: Need to convert Markdown to ATS-safe PDF. Options: WeasyPrint, fpdf2, ReportLab, pandoc+LaTeX.
**Decision**: ReportLab with custom Markdown parser.
**Rationale**: ReportLab is mature, pure Python, no system dependencies. Custom parser ensures we control exactly what formatting is used (ATS-safe). WeasyPrint requires system libraries. Pandoc requires external binary.
**Consequences**: Must implement a limited Markdown parser. Only supports the Markdown subset defined in §3.5.

### ADR-011: Fallback Strategy for Missing Claude Code

**Status**: accepted
**Context**: Claude Code may not be installed. System must still function.
**Decision**: Fall back to static templates from config (`cover_letter_template`, `fallback_resume_path`).
**Rationale**: Users should be able to use the app even without Claude Code, with reduced functionality. Dashboard shows warning banner.
**Consequences**: Fallback documents are not tailored to specific jobs.

---

## 7. Design Traceability Matrix

| Requirement | Type | Design Component | Interface | ADR |
|-------------|------|-----------------|-----------|-----|
| FR-031 | FR | ai_engine | check_claude_code_available() | ADR-009 |
| FR-032 | FR | ai_engine | invoke_claude_code() | ADR-009 |
| FR-033 | FR | ai_engine | read_all_experience_files() | — |
| FR-034 | FR | ai_engine | generate_documents() | ADR-009 |
| FR-035 | FR | ai_engine | generate_documents() | ADR-009 |
| FR-036 | FR | ai_engine | generate_documents() | — |
| FR-037 | FR | resume_renderer | render_resume_to_pdf() | ADR-010 |
| FR-038 | FR | ai_engine | fallback path | ADR-011 |
| FR-039 | FR | templates/index.html | JS warning banner | ADR-011 |
| FR-040 | FR | app.py | GET /api/bot/status | — |
| NFR-017 | NFR | ai_engine | timeout param | ADR-009 |
| NFR-018 | NFR | resume_renderer | ReportLab perf | ADR-010 |
| NFR-019 | NFR | ai_engine | file I/O | — |
| NFR-020 | NFR | ai_engine | prompt templates | — |
| NFR-021 | NFR | resume_renderer | formatting rules | ADR-010 |
| NFR-022 | NFR | ai_engine | subprocess list-form | ADR-009 |

---

## 8. Implementation Plan

| Order | Task ID | Description | Depends On | Size | FR Coverage |
|-------|---------|-------------|------------|------|-------------|
| 1 | IMPL-010 | Create `core/ai_engine.py` with `CLAUDE_CMD`, `check_claude_code_available()`, `invoke_claude_code()`, `read_all_experience_files()` | — | M | FR-031, FR-032, FR-033 |
| 2 | IMPL-011 | Add prompt templates and `generate_documents()` to `core/ai_engine.py` | IMPL-010 | M | FR-034, FR-035, FR-036 |
| 3 | IMPL-012 | Create `core/resume_renderer.py` with `render_resume_to_pdf()` | — | M | FR-037 |
| 4 | IMPL-013 | Add fallback logic to `generate_documents()` | IMPL-011 | S | FR-038 |
| 5 | IMPL-014 | Update `app.py` bot status with real `claude_code_available` check | IMPL-010 | S | FR-040 |
| 6 | IMPL-015 | Add warning banner to `templates/index.html` | IMPL-014 | S | FR-039 |
