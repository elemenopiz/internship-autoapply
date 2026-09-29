# System Architecture Document

**Document ID**: SAD-TASK-001-foundation
**Version**: 1.0
**Date**: 2026-03-09
**Status**: approved
**Author**: Claude (System Engineer)
**SRS Reference**: SRS-TASK-001-foundation

---

## 1. Executive Summary

AutoApply Phase 1 establishes a three-layer local application: a Flask+SocketIO web server (presentation), configuration and bot state management (service), and SQLite+filesystem persistence (infrastructure). The architecture prioritizes simplicity — single-process, no external services, pathlib everywhere — while providing clean extension points for future phases.

## 2. Architecture Overview

See full document in conversation context. Key components:
- Flask App (app.py) — Presentation Layer
- BotState (bot/state.py) — Service Layer
- AppConfig (config/settings.py) — Service Layer
- Database (db/database.py) — Infrastructure Layer
- SPA Dashboard (templates/index.html) — Presentation Layer

## 3. ADRs

- ADR-001: Single-process Flask + background thread (threading over multiprocess/asyncio)
- ADR-002: SQLite with direct sqlite3 (no ORM)
- ADR-003: Gevent async mode for Flask-SocketIO
- ADR-004: Inline CSS/JS in single HTML template

## 4. Critical Gap

NFR-007 (Path Traversal Protection): Must add filename validation to experience file API endpoints.

## 5. Design Traceability Matrix

### 5.1 Functional Requirements

| Requirement | Description | Design Component(s) | Interface / Source Files | ADR | Status |
|-------------|-------------|---------------------|--------------------------|-----|--------|
| FR-001 | Cross-Platform Setup Script | Infrastructure Layer — setup module | `setup.py` | — | ✅ |
| FR-002 | Application Entry Point | Presentation Layer — server bootstrap | `run.py` | ADR-001 | ✅ |
| FR-003 | Configuration Models and Persistence | Service Layer — config module | `config/settings.py` — `AppConfig`, `UserProfile`, `SearchCriteria`, `BotConfig`, `load_config()`, `save_config()`, `is_first_run()` | — | ✅ |
| FR-004 | Database Schema and Operations | Infrastructure Layer — data layer | `db/database.py` — `Database` class (CRUD, analytics, CSV export, feed events) | ADR-002 | ✅ |
| FR-005 | Bot State Management | Service Layer — bot module | `bot/state.py` — `BotState` class (thread-safe status, counters, uptime) | ADR-001 | ✅ |
| FR-006 | Flask REST API — Bot Control | Presentation Layer — API routes | `app.py` — `POST /api/bot/start`, `POST /api/bot/pause`, `POST /api/bot/stop`, `GET /api/bot/status` | ADR-001, ADR-003 | ✅ |
| FR-007 | Flask REST API — Applications | Presentation Layer — API routes | `app.py` — `GET /api/applications`, `PATCH /api/applications/<id>`, `GET /api/applications/<id>/cover_letter`, `GET /api/applications/<id>/resume`, `GET /api/applications/export` | ADR-002 | ✅ |
| FR-008 | Flask REST API — Experience File Management | Presentation Layer — API routes | `app.py` — `GET /api/profile/experiences`, `POST /api/profile/experiences`, `PUT /api/profile/experiences/<filename>`, `DELETE /api/profile/experiences/<filename>`, `GET /api/profile/status` | — | ✅ |
| FR-009 | Flask REST API — Configuration | Presentation Layer — API routes | `app.py` — `GET /api/config`, `PUT /api/config` | — | ✅ |
| FR-010 | Flask REST API — Analytics | Presentation Layer — API routes | `app.py` — `GET /api/analytics/summary`, `GET /api/analytics/daily` | ADR-002 | ✅ |
| FR-011 | Flask REST API — Setup Status | Presentation Layer — API routes | `app.py` — `GET /api/setup/status` | — | ✅ |
| FR-012 | SocketIO Real-Time Connection | Presentation Layer — WebSocket | `app.py` — SocketIO `connect` handler, `bot_status` event emission | ADR-003 | ✅ |
| FR-013 | Setup Wizard — 7-Step First-Run Flow | Presentation Layer — SPA UI | `templates/index.html` — wizard screens (Welcome, Experience Folder, Profile, Job Preferences, Fallback Resume, Platform Login, Done) | ADR-004 | ✅ |
| FR-014 | Dashboard Screen | Presentation Layer — SPA UI | `templates/index.html` — nav tabs, bot control panel, live feed section | ADR-004 | ✅ |
| FR-015 | Applications Screen | Presentation Layer — SPA UI | `templates/index.html` — filterable/sortable applications table, cover letter modal, resume download, CSV export | ADR-004 | ✅ |
| FR-016 | Profile Screen | Presentation Layer — SPA UI | `templates/index.html` — experience file cards, CRUD modals | ADR-004 | ✅ |
| FR-017 | Settings Screen | Presentation Layer — SPA UI | `templates/index.html` — editable config form | ADR-004 | ✅ |
| FR-018 | Claude Code Availability Detection | Service Layer — utility | `app.py` — `check_claude_code_available()` (checks `claude` and `claude.cmd` in PATH) | — | ✅ |

### 5.2 Non-Functional Requirements

| Requirement | Description | Design Component(s) | Validation | Status |
|-------------|-------------|---------------------|------------|--------|
| NFR-001 | API Response Time (<200ms p95) | Flask single-process, SQLite local I/O | Integration test timing | ✅ |
| NFR-002 | Dashboard Load Time (<1s) | Inline CSS/JS in single HTML (ADR-004), no external fetches | Manual browser test | ✅ |
| NFR-003 | Unit Test Coverage (>=90% line) | All modules under `config/`, `db/`, `bot/`, `app.py` | `pytest --cov` report | ✅ |
| NFR-004 | Thread Safety | `BotState` with `threading.Lock` | Concurrent unit test (10 threads x 1000 ops) | ✅ |
| NFR-005 | Cross-Platform Compatibility | `pathlib.Path` everywhere, no hardcoded separators | CI matrix / manual | ✅ |
| NFR-006 | Data Integrity | SQLite unique index on `(external_id, platform)` (ADR-002) | Unit test with duplicate data | ✅ |
| NFR-007 | Security — File Path Safety | Filename validation regex in experience file endpoints | Security-focused unit tests | ✅ |
| NFR-008 | Security — No Credentials in Config | Pydantic schema excludes password/token fields; `keyring` for secrets | Schema inspection + security audit | ✅ |
| NFR-009 | Graceful Error Handling | Global Flask error handlers returning JSON | Integration tests with invalid inputs | ✅ |
| NFR-010 | Configuration Validation | Pydantic v2 strict validation on `AppConfig` | Unit tests with invalid config data | ✅ |

### 5.3 Coverage Summary

- **18/18 FRs** mapped to design components, interfaces, and source files.
- **10/10 NFRs** mapped to design components and validation methods.
- **28/28 total requirements** — 100% design coverage.
