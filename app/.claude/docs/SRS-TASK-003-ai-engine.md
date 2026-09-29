# Software Requirements Specification

**Document ID**: SRS-TASK-003-ai-engine
**Version**: 1.0
**Date**: 2026-03-09
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD Section 8, Section 9.4, Section 17 Phase 2

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies all functional and non-functional requirements for AutoApply Phase 2 (Claude Code AI Engine). Audience: System Engineer, Backend Developer, Unit Tester, Integration Tester, Security Engineer, Documenter, Release Engineer.

### 1.2 Scope
The system SHALL provide: Claude Code CLI availability detection, AI-powered resume generation from experience files, AI-powered cover letter generation, Markdown-to-PDF resume rendering (ATS-safe), fallback behavior when Claude Code is unavailable, and a dashboard warning banner for missing Claude Code.

The system SHALL NOT provide: job searching, browser automation, application submission, or portal-specific appliers (deferred to Phase 3+).

### 1.3 Definitions and Acronyms
| Term | Definition |
|------|-----------|
| Claude Code | Anthropic's CLI tool (`claude`) that provides AI capabilities via `--print` flag for non-interactive use |
| ATS | Applicant Tracking System — software that parses resumes; requires simple formatting |
| Experience file | A plain `.txt` file in `~/.autoapply/profile/experiences/` containing user work history |
| Fallback | Behavior when Claude Code is unavailable: use static template instead of AI generation |
| ReportLab | Python library for generating PDF documents programmatically |

---

## 2. Overall Description

### 2.1 Product Perspective
Phase 2 adds the AI document generation layer on top of Phase 1's foundation (config, database, API, UI). The bot loop (Phase 3) will call `generate_documents()` for each matched job.

### 2.2 User Classes and Characteristics
| User Class | Description | Frequency | Technical Expertise |
|-----------|-------------|-----------|---------------------|
| Job Seeker | Professional using AutoApply to generate tailored application documents | Daily | Intermediate — has Claude Code CLI installed and authenticated |

### 2.3 Operating Environment
| Requirement | Specification |
|-------------|---------------|
| OS | macOS 12+, Windows 10/11 (64-bit), Ubuntu 20.04/22.04 LTS |
| Runtime | Python 3.11+ |
| External dependency | Claude Code CLI installed and authenticated (optional — fallback available) |
| Python dependency | `reportlab` for PDF generation |

### 2.4 Assumptions
| # | Assumption | Risk if Wrong | Mitigation |
|---|-----------|---------------|------------|
| A1 | Claude Code CLI supports `--print` flag for non-interactive output | Generation fails | Check `claude --version` at startup; fallback to template |
| A2 | Claude Code CLI supports `--version` flag for availability check | Availability detection fails | Wrap in try/except, treat exception as unavailable |
| A3 | Experience files are UTF-8 encoded `.txt` files | Read errors | Catch encoding errors, skip corrupted files with warning |
| A4 | ReportLab Helvetica font is available on all platforms | PDF rendering fails | Helvetica is built into ReportLab, no OS font dependency |

### 2.5 Constraints
| Type | Constraint | Rationale |
|------|-----------|-----------|
| Technical | PDF must use only Helvetica font, single-column, no colors/tables/images | ATS parsers reject complex formatting |
| Technical | Claude Code invocation via subprocess only (no API key, no SDK) | User runs locally with their own Claude Code auth |
| Timeline | Must complete before Phase 3 (bot core) can begin | Bot loop depends on `generate_documents()` |

---

## 3. Functional Requirements

### FR-031: Claude Code Availability Check

**Description**: The system SHALL detect whether Claude Code CLI is installed and accessible by running `claude --version` (or `claude.cmd --version` on Windows if `claude` is not found).

**Priority**: P0
**Source**: PRD Section 8 — Checking Claude Code Availability
**Dependencies**: None

**Acceptance Criteria**:

- **AC-031-1**: Given Claude Code CLI is installed and in PATH, When `check_claude_code_available()` is called, Then it returns `True`.

- **AC-031-2**: Given Claude Code CLI is NOT installed, When `check_claude_code_available()` is called, Then it returns `False` (no exception raised).

- **AC-031-3**: Given Claude Code CLI is installed but hangs on `--version`, When `check_claude_code_available()` is called, Then it returns `False` after a 10-second timeout.

**Negative Cases**:
- **AC-031-N1**: Given `claude` command returns a non-zero exit code, When `check_claude_code_available()` is called, Then it returns `False`.

---

### FR-032: Claude Code Invocation

**Description**: The system SHALL invoke Claude Code CLI non-interactively using `subprocess.run([CLAUDE_CMD, "--print", prompt])` and return stdout as a string.

**Priority**: P0
**Source**: PRD Section 8 — Invoking Claude Code
**Dependencies**: FR-031

**Acceptance Criteria**:

- **AC-032-1**: Given Claude Code is available and prompt is valid, When `invoke_claude_code(prompt)` is called, Then it returns the stdout output as a stripped string.

- **AC-032-2**: Given Claude Code returns non-zero exit code, When `invoke_claude_code(prompt)` is called, Then it raises `RuntimeError` with the exit code and stderr in the message.

- **AC-032-3**: Given Claude Code does not respond within `timeout_seconds`, When `invoke_claude_code(prompt, timeout_seconds=120)` is called, Then it raises `RuntimeError` indicating timeout.

**Negative Cases**:
- **AC-032-N1**: Given Claude Code is not installed, When `invoke_claude_code(prompt)` is called, Then it raises `RuntimeError` (not `FileNotFoundError`).

- **AC-032-N2**: Given an empty prompt string, When `invoke_claude_code("")` is called, Then it still invokes Claude Code and returns whatever output is produced (no pre-validation of prompt content).

---

### FR-033: Read Experience Files

**Description**: The system SHALL read all `.txt` files from the experience directory, excluding `README.txt`, and return their contents concatenated with section separators.

**Priority**: P0
**Source**: PRD Section 8 — `read_all_experience_files()`
**Dependencies**: None

**Acceptance Criteria**:

- **AC-033-1**: Given the experience directory contains files `skills.txt` and `work_history.txt`, When `read_all_experience_files(dir)` is called, Then it returns both files' contents separated by `=== filename.txt ===` headers, sorted alphabetically by filename.

- **AC-033-2**: Given the experience directory contains `README.txt` and `skills.txt`, When `read_all_experience_files(dir)` is called, Then `README.txt` is excluded and only `skills.txt` content is returned.

- **AC-033-3**: Given the experience directory is empty (no `.txt` files or only `README.txt`), When `read_all_experience_files(dir)` is called, Then it returns an empty string.

**Negative Cases**:
- **AC-033-N1**: Given the experience directory does not exist, When `read_all_experience_files(dir)` is called, Then it returns an empty string (not an exception).

- **AC-033-N2**: Given a `.txt` file contains non-UTF-8 bytes, When `read_all_experience_files(dir)` is called, Then that file is skipped with a logged warning, and remaining files are still read.

---

### FR-034: Resume Generation via Claude Code

**Description**: The system SHALL generate a tailored resume in Markdown format by invoking Claude Code with the `RESUME_PROMPT` template populated with experience files, job description, and applicant profile fields.

**Priority**: P0
**Source**: PRD Section 8 — Resume Generation Prompt
**Dependencies**: FR-032, FR-033

**Acceptance Criteria**:

- **AC-034-1**: Given experience files, a job description, and a user profile, When resume generation is invoked, Then the prompt sent to Claude Code includes all experience file content, the full job description, and all profile fields (full_name, email, phone, location, linkedin_url, portfolio_url).

- **AC-034-2**: Given Claude Code returns valid Markdown, When resume generation completes, Then the Markdown text is saved to `{output_dir_resumes}/{job_id}_{company}_{date}.md`.

- **AC-034-3**: Given a profile field is `None` (e.g., `linkedin_url`), When the prompt is formatted, Then the field value is substituted with `"N/A"`.

**Negative Cases**:
- **AC-034-N1**: Given Claude Code fails during resume generation, When `generate_documents()` is called, Then it raises `RuntimeError` and does not create partial files.

---

### FR-035: Cover Letter Generation via Claude Code

**Description**: The system SHALL generate a tailored cover letter in plain text by invoking Claude Code with the `COVER_LETTER_PROMPT` template populated with experience files, job description, applicant name, and bio.

**Priority**: P0
**Source**: PRD Section 8 — Cover Letter Generation Prompt
**Dependencies**: FR-032, FR-033

**Acceptance Criteria**:

- **AC-035-1**: Given experience files, a job description, and a user profile, When cover letter generation is invoked, Then the prompt includes experience content, job description, full_name, and bio.

- **AC-035-2**: Given Claude Code returns cover letter text, When generation completes, Then the text is saved to `{output_dir_cover_letters}/{job_id}_{company}_{date}.txt`.

**Negative Cases**:
- **AC-035-N1**: Given Claude Code fails during cover letter generation, When `generate_documents()` is called, Then it raises `RuntimeError` and does not create partial files.

---

### FR-036: Full Document Generation Orchestration

**Description**: The system SHALL provide a `generate_documents()` function that reads experience files, calls Claude Code twice (resume + cover letter), saves all outputs, and returns paths to the generated PDF resume and cover letter text file.

**Priority**: P0
**Source**: PRD Section 8 — Full Generation Flow
**Dependencies**: FR-033, FR-034, FR-035, FR-037

**Acceptance Criteria**:

- **AC-036-1**: Given all inputs are valid and Claude Code is available, When `generate_documents(job, profile, experience_dir, output_dir_resumes, output_dir_cover_letters)` is called, Then it returns a tuple `(resume_pdf_path, cover_letter_txt_path)` where both files exist on disk.

- **AC-036-2**: Given the function completes successfully, Then three files are created: `{base}.md` (resume Markdown source), `{base}.pdf` (resume PDF), and `{base}.txt` (cover letter), where `base = {job_id}_{company}_{date}`.

- **AC-036-3**: Given company name contains spaces, When the filename is constructed, Then spaces are replaced with hyphens and the name is lowercased.

**Negative Cases**:
- **AC-036-N1**: Given Claude Code fails on the resume generation step, When `generate_documents()` is called, Then it raises `RuntimeError` and no cover letter generation is attempted.

---

### FR-037: Resume PDF Rendering

**Description**: The system SHALL convert a Markdown resume string to an ATS-safe PDF using ReportLab with strict formatting rules.

**Priority**: P0
**Source**: PRD Section 9.4 — PDF Rendering Rules
**Dependencies**: None (standalone renderer)

**Acceptance Criteria**:

- **AC-037-1**: Given valid resume Markdown, When `render_resume_to_pdf(md_text, pdf_path)` is called, Then a valid PDF file is created at `pdf_path`.

- **AC-037-2**: Given the PDF is generated, Then it uses ONLY Helvetica font family (no other fonts).

- **AC-037-3**: Given the Markdown contains `# Name`, When rendered, Then the name appears at 18pt bold.

- **AC-037-4**: Given the Markdown contains `## Section`, When rendered, Then section headers appear at 12pt bold with a thin rule underneath.

- **AC-037-5**: Given the Markdown contains body text, When rendered, Then body text appears at 10pt.

- **AC-037-6**: Given the Markdown contains `- bullet`, When rendered, Then bullets use em-dash prefix at 10pt.

- **AC-037-7**: Given the PDF is generated, Then margins are 0.75 inches on all sides.

- **AC-037-8**: Given the PDF is generated, Then it contains NO colors (all black text on white), NO tables, NO images, and single-column layout only.

**Negative Cases**:
- **AC-037-N1**: Given empty Markdown input, When `render_resume_to_pdf("", path)` is called, Then it creates a valid (blank) PDF without crashing.

---

### FR-038: Fallback When Claude Code Unavailable

**Description**: The system SHALL provide fallback behavior when Claude Code is not available: use the static cover letter template from `config.bot.cover_letter_template` and the fallback resume PDF path from `config.profile.fallback_resume_path`.

**Priority**: P1
**Source**: PRD Section 8 — If not available
**Dependencies**: FR-031

**Acceptance Criteria**:

- **AC-038-1**: Given Claude Code is unavailable and `config.bot.cover_letter_template` is non-empty, When fallback is triggered, Then the template text is used as the cover letter.

- **AC-038-2**: Given Claude Code is unavailable and `config.profile.fallback_resume_path` points to an existing PDF, When fallback is triggered, Then that PDF path is returned as the resume.

- **AC-038-3**: Given Claude Code is unavailable and no fallback template/path is configured, When fallback is triggered, Then the system logs a warning and returns `None` for the missing document paths.

**Negative Cases**:
- **AC-038-N1**: Given `fallback_resume_path` points to a non-existent file, When fallback is triggered, Then the system logs a warning and returns `None` for resume path.

---

### FR-039: Dashboard Claude Code Warning Banner

**Description**: The system SHALL display a persistent warning banner in the dashboard when Claude Code is not detected, reading: "Claude Code not detected — using fallback templates. Install Claude Code for AI-tailored documents."

**Priority**: P1
**Source**: PRD Section 8 — If not available
**Dependencies**: FR-031

**Acceptance Criteria**:

- **AC-039-1**: Given Claude Code is unavailable, When the dashboard loads, Then a warning banner is visible with the specified message text.

- **AC-039-2**: Given Claude Code is available, When the dashboard loads, Then no warning banner is displayed.

- **AC-039-3**: Given `GET /api/bot/status` is called, Then the response includes a `claude_code_available` boolean field.

**Negative Cases**:
- **AC-039-N1**: Given the availability check fails with an unexpected error, When the dashboard loads, Then the banner shows (treats unknown as unavailable).

---

### FR-040: Bot Status Claude Code Field

**Description**: The `GET /api/bot/status` endpoint SHALL include a `claude_code_available` boolean field indicating whether Claude Code CLI is currently accessible.

**Priority**: P1
**Source**: PRD Section 17 Phase 2
**Dependencies**: FR-031

**Acceptance Criteria**:

- **AC-040-1**: Given Claude Code is installed, When `GET /api/bot/status` is called, Then the response includes `"claude_code_available": true`.

- **AC-040-2**: Given Claude Code is not installed, When `GET /api/bot/status` is called, Then the response includes `"claude_code_available": false`.

**Notes**: This field already exists in the Phase 1 API response (hardcoded). Phase 2 replaces it with an actual runtime check.

---

## 4. Non-Functional Requirements

### NFR-017: Claude Code Invocation Timeout

**Description**: Every Claude Code subprocess invocation SHALL have a configurable timeout defaulting to 120 seconds.
**Metric**: Subprocess terminates and raises error if no response within timeout.
**Priority**: P0
**Validation**: Unit test with mocked subprocess that exceeds timeout.

### NFR-018: PDF Generation Performance

**Description**: `render_resume_to_pdf()` SHALL complete in under 2 seconds for a typical one-page resume.
**Metric**: p95 < 2 seconds.
**Priority**: P1
**Validation**: Benchmark test with sample Markdown input.

### NFR-019: Experience File Size Limit

**Description**: `read_all_experience_files()` SHALL handle experience directories with up to 50 files totaling up to 5 MB without degradation.
**Metric**: Returns within 1 second for 50 files / 5 MB total.
**Priority**: P1
**Validation**: Unit test with 50 files of 100 KB each.

### NFR-020: No Secrets in Prompts

**Description**: Prompts sent to Claude Code SHALL NOT include any authentication tokens, API keys, or passwords. Only experience content, job descriptions, and profile fields (name, email, phone, location, URLs, bio) are permitted.
**Metric**: Code review confirms no secret fields in prompt templates.
**Priority**: P0
**Validation**: Security audit of prompt construction.

### NFR-021: PDF ATS Compatibility

**Description**: Generated PDFs SHALL be parseable by common ATS systems. This requires: embedded text (not images), single-column layout, standard font (Helvetica), no tables, no colors.
**Metric**: PDF text is extractable via `pdfplumber` or equivalent.
**Priority**: P0
**Validation**: Extract text from generated PDF and verify content matches source Markdown.

### NFR-022: Subprocess Security

**Description**: Claude Code invocation SHALL use list-form `subprocess.run()` (not shell=True) to prevent command injection.
**Metric**: No `shell=True` in any subprocess call.
**Priority**: P0
**Validation**: Security audit grep for `shell=True`.

---

## 5. Interface Requirements

### 5.1 Internal Interfaces

| Function | Module | Input | Output |
|----------|--------|-------|--------|
| `check_claude_code_available()` | `core/ai_engine.py` | None | `bool` |
| `invoke_claude_code(prompt, timeout)` | `core/ai_engine.py` | `str`, `int` | `str` |
| `read_all_experience_files(dir)` | `core/ai_engine.py` | `Path` | `str` |
| `generate_documents(job, profile, exp_dir, res_dir, cl_dir)` | `core/ai_engine.py` | See signature | `tuple[Path, Path]` |
| `render_resume_to_pdf(md_text, pdf_path)` | `core/resume_renderer.py` | `str`, `Path` | `None` (writes file) |

### 5.2 External Interfaces

| System | Protocol | Direction | Data |
|--------|----------|-----------|------|
| Claude Code CLI | subprocess (stdin/stdout) | Out → In | Prompt text → Generated document text |

---

## 6. Data Requirements

### 6.1 File Outputs
| File Type | Location | Naming Convention | Retention |
|-----------|----------|-------------------|-----------|
| Resume Markdown | `~/.autoapply/profile/resumes/` | `{job_id}_{company}_{date}.md` | Indefinite |
| Resume PDF | `~/.autoapply/profile/resumes/` | `{job_id}_{company}_{date}.pdf` | Indefinite |
| Cover letter | `~/.autoapply/profile/cover_letters/` | `{job_id}_{company}_{date}.txt` | Indefinite |

---

## 7. Out of Scope

- **Job searching and scraping** — Phase 3.
- **Browser automation and application submission** — Phase 3.
- **Live feed SocketIO events (GENERATING, APPLIED)** — Phase 3.
- **Resume/cover letter preview in dashboard** — Phase 4.
- **Prompt customization by user** — Future consideration.

---

## 8. Dependencies

### External Dependencies
| Dependency | Type | Status | Risk if Unavailable |
|-----------|------|--------|---------------------|
| Claude Code CLI | Runtime (optional) | Available | Fallback to templates — degraded but functional |
| ReportLab | Build | Available via pip | PDF generation impossible — must install |

### Internal Dependencies
| This Feature Needs | From | Status |
|-------------------|------|--------|
| `UserProfile` model | Phase 1 `config/settings.py` | Done |
| `BotConfig.cover_letter_template` | Phase 1 `config/settings.py` | Done |
| `UserProfile.fallback_resume_path` | Phase 1 `config/settings.py` | Done |
| `GET /api/bot/status` endpoint | Phase 1 `app.py` | Done |

---

## 9. Risks

| # | Risk | Probability | Impact | Score | Mitigation |
|---|------|:-----------:|:------:|:-----:|------------|
| R1 | Claude Code CLI changes `--print` flag behavior | Low | High | M | Pin to known working version, wrap calls |
| R2 | ReportLab Markdown parsing insufficient | Medium | Medium | M | Custom parser for limited Markdown subset |
| R3 | Generated resume exceeds one page | Medium | Low | L | Prompt instructs one-page limit |
| R4 | Claude Code generates hallucinated experience | Low | High | M | Prompt explicitly forbids inventing content |

---

## 10. Requirements Traceability Seeds

| Req ID | Source (PRD) | Traces Forward To |
|--------|-------------|-------------------|
| FR-031 | Section 8 | Design: ai_engine → Code: core/ai_engine.py → Test: test_ai_engine.py |
| FR-032 | Section 8 | Design: ai_engine → Code: core/ai_engine.py → Test: test_ai_engine.py |
| FR-033 | Section 8 | Design: ai_engine → Code: core/ai_engine.py → Test: test_ai_engine.py |
| FR-034 | Section 8 | Design: ai_engine → Code: core/ai_engine.py → Test: test_ai_engine.py |
| FR-035 | Section 8 | Design: ai_engine → Code: core/ai_engine.py → Test: test_ai_engine.py |
| FR-036 | Section 8 | Design: ai_engine → Code: core/ai_engine.py → Test: test_ai_engine.py |
| FR-037 | Section 9.4 | Design: resume_renderer → Code: core/resume_renderer.py → Test: test_resume_renderer.py |
| FR-038 | Section 8 | Design: ai_engine → Code: core/ai_engine.py → Test: test_ai_engine.py |
| FR-039 | Section 8 | Design: dashboard → Code: templates/index.html → Test: test_api.py |
| FR-040 | Section 17 | Design: api → Code: app.py → Test: test_api.py |
