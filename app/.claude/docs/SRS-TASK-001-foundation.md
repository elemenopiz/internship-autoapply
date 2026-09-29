# Software Requirements Specification

**Document ID**: SRS-TASK-001-foundation
**Version**: 1.0
**Date**: 2026-03-09
**Status**: approved
**Author**: Claude (Requirements Analyst)
**PRD Reference**: PRD-TASK-001

---

## 1. Purpose and Scope

### 1.1 Purpose
Specifies all functional and non-functional requirements for AutoApply Phase 1 (Foundation). Audience: System Engineer, Backend/Frontend Developers, Testers, Security Engineer, Documenter, Release Engineer.

### 1.2 Scope
The system SHALL provide: cross-platform setup, Pydantic-based configuration persistence, SQLite database with schema, Flask REST API, SocketIO real-time transport, a 7-step setup wizard, dashboard with bot controls, live feed UI, applications table, profile screen with experience file CRUD, and settings screen.

The system SHALL NOT provide: job searching, AI document generation, browser automation, portal-specific appliers, analytics charts, or CAPTCHA handling (deferred to Phases 2-6).

### 1.3 Definitions and Acronyms
| Term | Definition |
|------|-----------|
| Experience file | A plain `.txt` file in `~/.autoapply/profile/experiences/` containing user-authored work history |
| Data directory | `~/.autoapply/` — all persistent application data lives here |
| Bot state | Thread-safe in-memory object tracking automation status (stopped/running/paused) |
| SPA | Single Page Application — one HTML page with JS-controlled screen switching |
| ATS | Applicant Tracking System (LinkedIn, Indeed, Greenhouse, Lever, Workday) |
| Setup wizard | 7-step first-run configuration flow |
| Config | `AppConfig` Pydantic model persisted as `~/.autoapply/config.json` |

---

## 2. Overall Description

### 2.1 Product Perspective
Phase 1 is the foundation layer. All future phases (AI engine, bot, portals) build on the config, database, API, and UI established here.

### 2.2 User Classes and Characteristics
| User Class | Description | Frequency of Use | Technical Expertise |
|-----------|-------------|-------------------|---------------------|
| Job Seeker | Professional using the tool to automate applications | Daily | Intermediate — comfortable with running Python scripts |

### 2.3 Operating Environment
| Requirement | Specification |
|-------------|---------------|
| OS | macOS 12+, Windows 10/11 (64-bit), Ubuntu 20.04/22.04 LTS |
| Runtime | Python 3.11+ |
| Browser | Any modern browser supporting ES6+ and WebSocket |
| Network | Internet connection (for future phases; Phase 1 runs locally) |
| RAM | Minimum 4 GB |
| Disk | Minimum 2 GB free |

### 2.4 Assumptions
| # | Assumption | Risk if Wrong | Mitigation |
|---|-----------|---------------|------------|
| A1 | User has Python 3.11+ installed | Setup fails | setup.py checks version and exits with clear error |
| A2 | User has write access to home directory | Cannot create ~/.autoapply/ | setup.py reports permission error |
| A3 | Port 5000 is available on localhost | Flask cannot start | Report error with suggestion to check port |
| A4 | Claude Code CLI may or may not be installed | AI features unavailable | Detect and show status; fallback path exists |
| A5 | User uses a modern browser (Chrome, Firefox, Edge, Safari) | UI may not render | No IE11 support; document requirement |

### 2.5 Constraints
| Type | Constraint | Rationale |
|------|-----------|-----------|
| Technical | Pure Python stack — no Node.js dependency | PRD Section 3 requirement |
| Technical | SQLite only — no external DB server | Privacy, simplicity, zero-config |
| Technical | pathlib.Path everywhere — no hardcoded separators | Cross-platform compatibility |
| Technical | Credentials via keyring — never in config.json | Security |
| Technical | Single process — Flask main thread + bot background thread | Simplicity |

---

## 3. Functional Requirements

### FR-001: Cross-Platform Setup Script

**Description**: The system shall provide a `setup.py` script that validates the Python version, installs all dependencies from `requirements.txt`, installs Playwright Chromium, creates the full `~/.autoapply/` directory tree, and writes a `README.txt` guide into the experiences folder.

**Priority**: P0
**Source**: US-001, PRD Section 6, PRD Phase 1
**Dependencies**: None

**Acceptance Criteria**:
- **AC-001-1**: Given Python 3.11+ is installed, When user runs `python setup.py`, Then all pip dependencies install successfully and Playwright Chromium is downloaded.
- **AC-001-2**: Given setup completes, When the data directory is inspected, Then `~/.autoapply/profile/experiences/`, `~/.autoapply/profile/jobs/`, `~/.autoapply/profile/resumes/`, `~/.autoapply/profile/cover_letters/`, `~/.autoapply/browser_profile/`, and `~/.autoapply/backups/` all exist.
- **AC-001-3**: Given setup completes, When `~/.autoapply/profile/experiences/README.txt` is read, Then it contains usage instructions for experience files.

**Negative Cases**:
- **AC-001-N1**: Given Python < 3.11 is installed, When user runs `python setup.py`, Then the script prints "Python 3.11+ required" with the current version and exits with code 1.
- **AC-001-N2**: Given `requirements.txt` is missing, When user runs `python setup.py`, Then the script prints "requirements.txt not found" and exits with code 1.

---

### FR-002: Application Entry Point

**Description**: The system shall provide a `run.py` script that creates all data directories (idempotent), writes README.txt if absent, starts the Flask+SocketIO server on `127.0.0.1:5000`, and opens the user's default browser to `http://localhost:5000` after a 1.5-second delay.

**Priority**: P0
**Source**: US-005, PRD Section 6
**Dependencies**: FR-001

**Acceptance Criteria**:
- **AC-002-1**: Given dependencies are installed, When user runs `python run.py`, Then the Flask server starts on port 5000 and prints "AutoApply starting at http://localhost:5000".
- **AC-002-2**: Given the server starts, When 1.5 seconds have elapsed, Then the user's default browser opens to `http://localhost:5000`.
- **AC-002-3**: Given data directories do not exist, When `run.py` executes, Then all directories from FR-001 AC-001-2 are created.

**Negative Cases**:
- **AC-002-N1**: Given port 5000 is already in use, When user runs `python run.py`, Then the server fails with a clear "Address already in use" error.

---

### FR-003: Configuration Models and Persistence

**Description**: The system shall define Pydantic v2 models (`UserProfile`, `SearchCriteria`, `BotConfig`, `AppConfig`) and provide functions to load, save, and detect first-run status from `~/.autoapply/config.json`.

**Priority**: P0
**Source**: US-001, US-004, US-010, PRD Section 13
**Dependencies**: None

**Acceptance Criteria**:
- **AC-003-1**: Given a valid `AppConfig` object, When `save_config()` is called, Then `~/.autoapply/config.json` contains the JSON representation with all fields.
- **AC-003-2**: Given `config.json` exists with valid data, When `load_config()` is called, Then it returns an `AppConfig` instance with all fields correctly populated.
- **AC-003-3**: Given `config.json` does not exist, When `load_config()` is called, Then it returns `None`.
- **AC-003-4**: Given `config.json` does not exist, When `is_first_run()` is called, Then it returns `True`.
- **AC-003-5**: Given `config.json` exists, When `is_first_run()` is called, Then it returns `False`.

**Negative Cases**:
- **AC-003-N1**: Given `config.json` contains invalid JSON, When `load_config()` is called, Then it raises an exception (not silently returns corrupt data).
- **AC-003-N2**: Given `config.json` contains valid JSON but missing required fields, When `load_config()` is called, Then Pydantic raises a `ValidationError`.

---

### FR-004: Database Schema and Operations

**Description**: The system shall provide a `Database` class using SQLite that initializes the schema (applications + feed_events tables), supports CRUD for applications, deduplication check, CSV export, feed event logging, and analytics queries.

**Priority**: P0
**Source**: US-008, PRD Section 11
**Dependencies**: None

**Acceptance Criteria**:
- **AC-004-1**: Given a new database path, When `Database(path)` is constructed, Then both `applications` and `feed_events` tables are created with all columns per PRD Section 11.
- **AC-004-2**: Given valid application data, When `save_application()` is called, Then the row is inserted and the auto-generated integer ID is returned.
- **AC-004-3**: Given an application exists, When `update_status(id, status, notes)` is called, Then the status, notes, and updated_at fields are modified.
- **AC-004-4**: Given applications exist, When `get_all_applications(status="applied")` is called, Then only applications with status "applied" are returned, ordered by `applied_at` DESC.
- **AC-004-5**: Given applications exist, When `get_all_applications(search="Stripe")` is called, Then applications where job_title or company contains "Stripe" (case-insensitive LIKE) are returned.
- **AC-004-6**: Given an application with external_id "123" and platform "linkedin" exists, When `exists("123", "linkedin")` is called, Then it returns `True`.
- **AC-004-7**: Given applications exist, When `export_csv(path)` is called, Then a CSV file with headers and all application rows is written.
- **AC-004-8**: Given valid event data, When `save_feed_event(event_type, ...)` is called, Then the event is inserted with auto-timestamp.
- **AC-004-9**: Given events exist, When `get_feed_events(limit=10)` is called, Then at most 10 events are returned ordered by `created_at` DESC.
- **AC-004-10**: Given applications exist, When `get_analytics_summary()` is called, Then it returns `{total, by_status, by_platform}` with correct counts.
- **AC-004-11**: Given applications exist, When `get_daily_analytics(days=30)` is called, Then it returns daily counts for the last 30 days.

**Negative Cases**:
- **AC-004-N1**: Given an application with external_id "123" and platform "linkedin" already exists, When `save_application()` is called with the same external_id and platform, Then an `IntegrityError` is raised (unique index enforced).
- **AC-004-N2**: Given application_id 999 does not exist, When `get_application(999)` is called, Then it returns `None`.
- **AC-004-N3**: Given no applications exist, When `export_csv(path)` is called, Then the function completes without error (empty file or no-op).

---

### FR-005: Bot State Management

**Description**: The system shall provide a thread-safe `BotState` class that tracks bot status (stopped/running/paused), stop_flag, daily counters (jobs_found, applied, errors), start_time, and uptime_seconds.

**Priority**: P0
**Source**: US-006, PRD Section 9.5
**Dependencies**: None

**Acceptance Criteria**:
- **AC-005-1**: Given a new BotState, When inspected, Then status is "stopped", stop_flag is False, all counters are 0, start_time is None.
- **AC-005-2**: Given stopped state, When `start()` is called, Then status is "running", stop_flag is False, start_time is set to current UTC time.
- **AC-005-3**: Given running state, When `pause()` is called, Then status is "paused".
- **AC-005-4**: Given any state, When `stop()` is called, Then status is "stopped", stop_flag is True, start_time is None.
- **AC-005-5**: Given running state with start_time set, When `get_status_dict()` is called, Then `uptime_seconds` is a positive float.
- **AC-005-6**: Given counters have been incremented, When `reset_daily()` is called, Then all counters reset to 0.

**Negative Cases**:
- **AC-005-N1**: Given two threads call `increment_applied()` concurrently 1000 times each, When complete, Then `applied_today` equals exactly 2000 (thread safety).

---

### FR-006: Flask REST API — Bot Control

**Description**: The system shall expose REST endpoints for bot start, pause, stop, and status retrieval.

**Priority**: P0
**Source**: US-006, PRD Section 12
**Dependencies**: FR-005

**Acceptance Criteria**:
- **AC-006-1**: Given the server is running, When `POST /api/bot/start` is called, Then response is `{"status": "running"}` with HTTP 200.
- **AC-006-2**: Given the server is running, When `POST /api/bot/pause` is called, Then response is `{"status": "paused"}` with HTTP 200.
- **AC-006-3**: Given the server is running, When `POST /api/bot/stop` is called, Then response is `{"status": "stopped"}` with HTTP 200.
- **AC-006-4**: Given the server is running, When `GET /api/bot/status` is called, Then response contains `{status, stop_flag, jobs_found_today, applied_today, errors_today, start_time, uptime_seconds, claude_code_available}` with HTTP 200.

---

### FR-007: Flask REST API — Applications

**Description**: The system shall expose REST endpoints for listing, updating, retrieving cover letters, downloading resumes, and exporting applications.

**Priority**: P1
**Source**: US-008, PRD Section 12
**Dependencies**: FR-004

**Acceptance Criteria**:
- **AC-007-1**: Given applications exist, When `GET /api/applications?status=applied&limit=10&offset=0` is called, Then at most 10 applications with status "applied" are returned as JSON array.
- **AC-007-2**: Given application ID 1 exists, When `PATCH /api/applications/1` with body `{"status": "interview", "notes": "Phone screen"}` is called, Then the application is updated and response is `{"success": true}`.
- **AC-007-3**: Given application ID 1 exists with cover_letter_text, When `GET /api/applications/1/cover_letter` is called, Then response contains `{cover_letter_text, file_path}`.
- **AC-007-4**: Given application ID 1 exists with a valid resume_path pointing to an existing PDF, When `GET /api/applications/1/resume` is called, Then the PDF file is returned with `application/pdf` mimetype.
- **AC-007-5**: Given applications exist, When `GET /api/applications/export` is called, Then a CSV file is returned as attachment.

**Negative Cases**:
- **AC-007-N1**: Given application ID 999 does not exist, When `GET /api/applications/999/cover_letter` is called, Then response is HTTP 404 with `{"error": "Application not found"}`.
- **AC-007-N2**: Given application ID 1 exists but resume_path is null or file does not exist, When `GET /api/applications/1/resume` is called, Then response is HTTP 404 with `{"error": "Resume not found"}`.

---

### FR-008: Flask REST API — Experience File Management

**Description**: The system shall expose REST endpoints for listing, creating, updating, and deleting experience files in `~/.autoapply/profile/experiences/`.

**Priority**: P0
**Source**: US-002, US-003, PRD Section 12
**Dependencies**: FR-001

**Acceptance Criteria**:
- **AC-008-1**: Given experience files exist, When `GET /api/profile/experiences` is called, Then response contains `{files: [{name, content, size, modified_at}]}` for every `.txt` file.
- **AC-008-2**: Given valid data, When `POST /api/profile/experiences` with body `{"filename": "skills.txt", "content": "Python, Flask..."}` is called, Then the file is created and response is `{"success": true}`.
- **AC-008-3**: Given file "skills.txt" exists, When `PUT /api/profile/experiences/skills.txt` with body `{"content": "Updated content"}` is called, Then the file content is overwritten and response is `{"success": true}`.
- **AC-008-4**: Given file "skills.txt" exists, When `DELETE /api/profile/experiences/skills.txt` is called, Then the file is deleted and response is `{"success": true}`.
- **AC-008-5**: Given experience files exist, When `GET /api/profile/status` is called, Then response contains `{file_count, total_words, claude_code_available}`.

**Negative Cases**:
- **AC-008-N1**: Given file "nonexistent.txt" does not exist, When `PUT /api/profile/experiences/nonexistent.txt` is called, Then response is HTTP 404 with `{"error": "File not found"}`.
- **AC-008-N2**: Given file "nonexistent.txt" does not exist, When `DELETE /api/profile/experiences/nonexistent.txt` is called, Then response is HTTP 404 with `{"error": "File not found"}`.

---

### FR-009: Flask REST API — Configuration

**Description**: The system shall expose REST endpoints for reading and updating the application configuration.

**Priority**: P0
**Source**: US-004, US-010, PRD Section 12
**Dependencies**: FR-003

**Acceptance Criteria**:
- **AC-009-1**: Given config.json exists, When `GET /api/config` is called, Then the full config object is returned as JSON.
- **AC-009-2**: Given config.json does not exist, When `GET /api/config` is called, Then response is `{}`.
- **AC-009-3**: Given valid config data, When `PUT /api/config` with a full config body is called, Then config.json is written and response is `{"success": true}`.
- **AC-009-4**: Given existing config, When `PUT /api/config` with a partial body is called, Then only the specified fields are updated, other fields are preserved.

---

### FR-010: Flask REST API — Analytics

**Description**: The system shall expose REST endpoints for application analytics summary and daily counts.

**Priority**: P2
**Source**: PRD Section 12
**Dependencies**: FR-004

**Acceptance Criteria**:
- **AC-010-1**: Given applications exist, When `GET /api/analytics/summary` is called, Then response contains `{total, by_status, by_platform}`.
- **AC-010-2**: Given applications exist, When `GET /api/analytics/daily?days=7` is called, Then response contains daily counts for the last 7 days.

---

### FR-011: Flask REST API — Setup Status

**Description**: The system shall expose a REST endpoint to check first-run status and Claude Code availability.

**Priority**: P0
**Source**: US-001, US-009
**Dependencies**: FR-003

**Acceptance Criteria**:
- **AC-011-1**: Given config.json does not exist, When `GET /api/setup/status` is called, Then response contains `{"is_first_run": true, "claude_code_available": <bool>}`.
- **AC-011-2**: Given config.json exists, When `GET /api/setup/status` is called, Then `is_first_run` is `false`.

---

### FR-012: SocketIO Real-Time Connection

**Description**: The system shall establish a SocketIO connection between server and browser client, emitting `bot_status` on connect.

**Priority**: P1
**Source**: US-007, PRD Section 12
**Dependencies**: FR-005

**Acceptance Criteria**:
- **AC-012-1**: Given client connects via SocketIO, When connection is established, Then server emits `bot_status` event with current bot state dict.

---

### FR-013: Setup Wizard — 7-Step First-Run Flow

**Description**: The system shall display a 7-step setup wizard when `is_first_run` is true, collecting profile data, job preferences, and saving config on completion.

**Priority**: P0
**Source**: US-001, US-004, PRD Section 14
**Dependencies**: FR-003, FR-009, FR-011

**Acceptance Criteria**:
- **AC-013-1**: Given first run, When user opens localhost:5000, Then the setup wizard is displayed (not the dashboard).
- **AC-013-2**: Given wizard Step 1 (Welcome), When displayed, Then Claude Code availability status is shown (green check or yellow warning).
- **AC-013-3**: Given wizard Step 2 (Experience Folder), When displayed, Then the path `~/.autoapply/profile/experiences/` is shown and live file count is fetched from `/api/profile/status`.
- **AC-013-4**: Given wizard Step 3 (Profile), When user fills Full Name, Email, Phone, Location, Bio and clicks Next, Then the data is retained in memory for later save.
- **AC-013-5**: Given wizard Step 4 (Job Preferences), When user adds job titles as tags and presses Enter, Then tags appear as removable pills.
- **AC-013-6**: Given wizard Step 4, When user clicks X on a tag, Then the tag is removed.
- **AC-013-7**: Given wizard Step 5 (Fallback Resume), When user skips, Then no fallback_resume_path is set in config.
- **AC-013-8**: Given wizard Step 6 (Platform Login), When displayed, Then placeholder buttons for LinkedIn and Indeed login are shown.
- **AC-013-9**: Given wizard Step 7 (Done), When user clicks "Go to Dashboard", Then all collected data is saved via `PUT /api/config` and the dashboard screen is displayed.
- **AC-013-10**: Given wizard, When user clicks "Back" on any step (except step 1), Then the previous step is displayed with previously entered data intact.

**Negative Cases**:
- **AC-013-N1**: Given wizard Step 3, When user leaves required field (Full Name) empty and clicks Next, Then validation prevents advancing and highlights the empty field.

---

### FR-014: Dashboard Screen

**Description**: The system shall display a dashboard with navigation tabs, bot control panel, and live feed section.

**Priority**: P1
**Source**: US-005, US-006, US-007, PRD Section 14
**Dependencies**: FR-006, FR-012

**Acceptance Criteria**:
- **AC-014-1**: Given dashboard is displayed, When user views nav bar, Then tabs for Dashboard, Applications, Profile, Analytics, Settings are visible.
- **AC-014-2**: Given dashboard, When user clicks a nav tab, Then the corresponding screen is shown without page reload.
- **AC-014-3**: Given dashboard, When user clicks "Start" button, Then `POST /api/bot/start` is called and status indicator turns green.
- **AC-014-4**: Given dashboard, When user clicks "Pause" button, Then status indicator turns yellow.
- **AC-014-5**: Given dashboard, When user clicks "Stop" button, Then status indicator turns red.
- **AC-014-6**: Given dashboard, When a `feed_event` SocketIO event arrives, Then it appears in the live feed with timestamp, colored type badge, job title, company, and platform.
- **AC-014-7**: Given an APPLIED feed event, When displayed, Then the resume filename is shown.

---

### FR-015: Applications Screen

**Description**: The system shall display a filterable, sortable table of all applications with inline status editing, cover letter viewing, resume downloading, and CSV export.

**Priority**: P1
**Source**: US-008, PRD Section 14
**Dependencies**: FR-007

**Acceptance Criteria**:
- **AC-015-1**: Given applications screen, When displayed, Then filter bar with Status dropdown, Platform dropdown, Search input, and Export CSV button is visible.
- **AC-015-2**: Given applications exist, When user selects status filter "applied", Then table shows only applied applications.
- **AC-015-3**: Given an application row, When user clicks "View" on cover letter column, Then a modal displays the full cover letter text.
- **AC-015-4**: Given an application with a resume, When user clicks the resume download link, Then the PDF downloads.
- **AC-015-5**: Given application row, When user changes status via dropdown, Then `PATCH /api/applications/:id` is called with the new status.
- **AC-015-6**: Given applications screen, When user clicks "Export CSV", Then CSV file downloads.

---

### FR-016: Profile Screen

**Description**: The system shall display all experience files with metadata and provide CRUD operations via modals.

**Priority**: P0
**Source**: US-002, US-003, PRD Section 14
**Dependencies**: FR-008

**Acceptance Criteria**:
- **AC-016-1**: Given experience files exist, When profile screen loads, Then each file is displayed with filename, word count, last modified date, and first 100 characters preview.
- **AC-016-2**: Given profile screen, When user clicks "+ New File", Then a modal appears with filename input and content textarea.
- **AC-016-3**: Given new file modal, When user enters filename and content and saves, Then `POST /api/profile/experiences` is called and file appears in the list.
- **AC-016-4**: Given an existing file, When user clicks "Edit", Then a modal shows the current content for editing.
- **AC-016-5**: Given edit modal, When user modifies content and saves, Then `PUT /api/profile/experiences/:filename` is called.
- **AC-016-6**: Given an existing file, When user clicks "Delete" and confirms, Then `DELETE /api/profile/experiences/:filename` is called and file is removed from the list.
- **AC-016-7**: Given profile screen, When displayed, Then Claude Code availability status indicator is visible.

---

### FR-017: Settings Screen

**Description**: The system shall display the current configuration in an editable form and allow saving changes.

**Priority**: P2
**Source**: US-010, PRD Section 14
**Dependencies**: FR-009

**Acceptance Criteria**:
- **AC-017-1**: Given settings screen loads, When config exists, Then all config fields are populated with current values.
- **AC-017-2**: Given settings form, When user changes values and clicks Save, Then `PUT /api/config` is called with the updated data and a success message is shown.

---

### FR-018: Claude Code Availability Detection

**Description**: The system shall detect whether the Claude Code CLI is available in the system PATH, checking for both `claude` and `claude.cmd` (Windows).

**Priority**: P1
**Source**: US-009, PRD Section 15
**Dependencies**: None

**Acceptance Criteria**:
- **AC-018-1**: Given `claude` is in PATH, When `check_claude_code_available()` is called, Then it returns `True`.
- **AC-018-2**: Given Windows OS and `claude.cmd` is in PATH (but not `claude`), When `check_claude_code_available()` is called, Then it returns `True`.
- **AC-018-3**: Given neither `claude` nor `claude.cmd` is in PATH, When `check_claude_code_available()` is called, Then it returns `False`.

---

## 4. Non-Functional Requirements

### NFR-001: API Response Time

**Description**: All REST API endpoints shall respond within 200ms under normal operation (single user, < 10,000 applications in database).
**Metric**: p95 response time < 200ms
**Priority**: P1
**Validation Method**: Integration test measuring response times across all endpoints.

### NFR-002: Dashboard Load Time

**Description**: The index page shall load and become interactive within 1 second on localhost.
**Metric**: Time to interactive < 1000ms
**Priority**: P1
**Validation Method**: Manual browser test.

### NFR-003: Unit Test Coverage

**Description**: All Python backend modules shall have >= 90% line coverage and >= 85% branch coverage.
**Metric**: Line >= 90%, Branch >= 85%
**Priority**: P0
**Validation Method**: `pytest --cov` report.

### NFR-004: Thread Safety

**Description**: `BotState` shall be safe for concurrent access from multiple threads without data corruption.
**Metric**: Zero race conditions under concurrent test (1000 operations from 10 threads).
**Priority**: P0
**Validation Method**: Concurrent unit test.

### NFR-005: Cross-Platform Compatibility

**Description**: The application shall run without modification on macOS 12+, Windows 10/11, and Ubuntu 20.04/22.04.
**Metric**: All tests pass on all three platforms.
**Priority**: P1
**Validation Method**: CI matrix or manual cross-platform run.

### NFR-006: Data Integrity

**Description**: The SQLite database shall enforce the unique constraint on `(external_id, platform)` to prevent duplicate applications.
**Metric**: Duplicate insert raises IntegrityError 100% of the time.
**Priority**: P0
**Validation Method**: Unit test with duplicate data.

### NFR-007: Security — File Path Safety

**Description**: Experience file API endpoints shall reject filenames containing path traversal sequences (`..`, `/`, `\`).
**Metric**: All traversal attempts return HTTP 400.
**Priority**: P0
**Validation Method**: Security-focused unit tests.

### NFR-008: Security — No Credentials in Config

**Description**: `config.json` shall never contain passwords or authentication tokens. Credentials shall only be stored via the `keyring` library.
**Metric**: config.json schema contains zero password/token fields.
**Priority**: P0
**Validation Method**: Schema inspection + security audit.

### NFR-009: Graceful Error Handling

**Description**: All API endpoints shall return JSON error responses (not HTML stack traces) for all error conditions.
**Metric**: Every error path returns `{"error": "<message>"}` with appropriate HTTP status code.
**Priority**: P1
**Validation Method**: Integration tests with invalid inputs.

### NFR-010: Configuration Validation

**Description**: The system shall reject invalid configuration data via Pydantic validation, not silently accept corrupt data.
**Metric**: Invalid data raises `ValidationError` 100% of the time.
**Priority**: P0
**Validation Method**: Unit tests with invalid config data.

---

## 5. Interface Requirements

### 5.1 User Interfaces
- Single-page application served at `http://localhost:5000`
- Dark theme UI with responsive layout (minimum 1024px width)
- 6 screens: Setup Wizard, Dashboard, Applications, Profile, Analytics, Settings

### 5.2 External System Interfaces
| External System | Protocol | Direction | Data Exchanged | Notes |
|----------------|----------|-----------|----------------|-------|
| Claude Code CLI | subprocess | out | PATH check only in Phase 1 | Full invocation in Phase 2 |

### 5.3 Internal Interfaces
| Module | Interface | Consumers |
|--------|-----------|-----------|
| config/settings.py | load_config(), save_config(), is_first_run(), get_data_dir() | app.py, run.py, setup.py |
| db/database.py | Database class with all CRUD methods | app.py |
| bot/state.py | BotState class | app.py |

---

## 6. Data Requirements

### 6.1 Data Entities
| Entity | Key Attributes | Storage |
|--------|---------------|---------|
| Application | id, external_id, platform, job_title, company, status, resume_path, cover_letter_text | SQLite applications table |
| FeedEvent | id, event_type, job_title, company, platform, message | SQLite feed_events table |
| AppConfig | profile, search_criteria, bot, company_blacklist | ~/.autoapply/config.json |
| Experience File | filename, content | ~/.autoapply/profile/experiences/*.txt |

### 6.2 Data Retention
| Data Category | Retention | Deletion Method |
|--------------|-----------|-----------------|
| Applications | Indefinite (user's machine) | Manual or future feature |
| Feed events | Indefinite | Manual |
| Config | Indefinite | User deletes file |
| Experience files | User-managed | User deletes via UI or filesystem |

---

## 7. Out of Scope

- **Job searching/scraping**: Deferred to Phase 3
- **AI document generation**: Deferred to Phase 2
- **Resume PDF rendering**: Deferred to Phase 2
- **Browser automation (Playwright)**: Deferred to Phase 3
- **Portal-specific appliers**: Deferred to Phases 3-6
- **Analytics charts**: Deferred to Phase 4
- **CAPTCHA handling**: Deferred to Phase 4
- **Database backup automation**: Deferred to Phase 6

---

## 8. Dependencies

### External Dependencies
| Dependency | Type | Status | Risk if Unavailable |
|-----------|------|--------|---------------------|
| Flask 3.x | runtime | Available | Cannot serve dashboard |
| Flask-SocketIO 5.x | runtime | Available | No real-time updates |
| Pydantic 2.x | runtime | Available | No config validation |
| SQLite (stdlib) | runtime | Available (Python built-in) | No database |
| gevent | runtime | Available | SocketIO async mode unavailable |

---

## 9. Risks

| # | Risk | Prob | Impact | Score | Mitigation |
|---|------|:----:|:------:|:-----:|------------|
| R1 | Path traversal in file API | M | H | 6 | Validate filenames, reject .. and separators |
| R2 | Port 5000 conflict | L | M | 2 | Clear error message, document alternative |
| R3 | Large experience files slow API | L | L | 1 | Reasonable file size limit (10MB) |
| R4 | SQLite concurrent writes | L | M | 2 | Single writer pattern, check_same_thread=False |

---

## 10. Requirements Traceability Seeds

| Req ID | Source (PRD) | Traces Forward To |
|--------|-------------|-------------------|
| FR-001 | US-001 | Design: setup module -> Code: setup.py -> Test: test_setup.py -> Docs: installation guide |
| FR-002 | US-005 | Design: entry point -> Code: run.py -> Test: test_run.py -> Docs: usage guide |
| FR-003 | US-001,004,010 | Design: config module -> Code: config/settings.py -> Test: test_settings.py -> Docs: config reference |
| FR-004 | US-008 | Design: data layer -> Code: db/database.py, db/models.py -> Test: test_database.py -> Docs: schema reference |
| FR-005 | US-006 | Design: bot module -> Code: bot/state.py -> Test: test_state.py -> Docs: bot controls |
| FR-006 | US-006 | Design: API layer -> Code: app.py (bot routes) -> Test: test_api_bot.py |
| FR-007 | US-008 | Design: API layer -> Code: app.py (app routes) -> Test: test_api_applications.py |
| FR-008 | US-002,003 | Design: API layer -> Code: app.py (profile routes) -> Test: test_api_profile.py |
| FR-009 | US-004,010 | Design: API layer -> Code: app.py (config routes) -> Test: test_api_config.py |
| FR-010 | PRD S12 | Design: API layer -> Code: app.py (analytics routes) -> Test: test_api_analytics.py |
| FR-011 | US-001,009 | Design: API layer -> Code: app.py (setup route) -> Test: test_api_setup.py |
| FR-012 | US-007 | Design: WebSocket -> Code: app.py (SocketIO) -> Test: test_socketio.py |
| FR-013 | US-001,004 | Design: UI wizard -> Code: templates/index.html -> Test: test_wizard_e2e.py |
| FR-014 | US-005,006,007 | Design: UI dashboard -> Code: templates/index.html -> Test: test_dashboard_e2e.py |
| FR-015 | US-008 | Design: UI apps -> Code: templates/index.html -> Test: test_applications_e2e.py |
| FR-016 | US-002,003 | Design: UI profile -> Code: templates/index.html -> Test: test_profile_e2e.py |
| FR-017 | US-010 | Design: UI settings -> Code: templates/index.html -> Test: test_settings_e2e.py |
| FR-018 | US-009 | Design: utility -> Code: app.py (helper) -> Test: test_claude_check.py |
