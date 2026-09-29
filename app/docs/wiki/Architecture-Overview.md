# Architecture Overview

## System Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        ELECTRON SHELL                            │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  BrowserWindow (Chromium)                                  │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │  Vanilla JS SPA (17 ES modules)                      │  │  │
│  │  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐       │  │  │
│  │  │  │ app.js │ │ dash-  │ │ appli- │ │config- │ ...   │  │  │
│  │  │  │        │ │board.js│ │cations │ │  .js   │       │  │  │
│  │  │  └────────┘ └────────┘ └────────┘ └────────┘       │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  └───────────────────────┬────────────────────────────────────┘  │
│                          │ HTTP + WebSocket (SocketIO)            │
│  ┌───────────────────────▼────────────────────────────────────┐  │
│  │  FLASK BACKEND (child process)                             │  │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │  │
│  │  │ Routes  │ │ Bot Core │ │ AI Engine│ │   Database   │  │  │
│  │  │(7 BPs)  │ │          │ │          │ │  (SQLite)    │  │  │
│  │  └─────────┘ └────┬─────┘ └──────────┘ └──────────────┘  │  │
│  │                    │                                       │  │
│  │  ┌─────────────────▼───────────────────────────────────┐  │  │
│  │  │  PLAYWRIGHT (persistent browser context)            │  │  │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐           │  │  │
│  │  │  │ LinkedIn │ │  Indeed  │ │ ATS Sites│           │  │  │
│  │  │  │ Searcher │ │ Searcher │ │ Appliers │           │  │  │
│  │  │  └──────────┘ └──────────┘ └──────────┘           │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Layer Architecture

AutoApply follows a four-layer architecture with clear separation of concerns:

```
┌─────────────────────────────────────────────┐
│  PRESENTATION LAYER                         │
│  templates/index.html, static/js/, static/css/ │
│  Vanilla JS SPA, SocketIO client            │
├─────────────────────────────────────────────┤
│  SERVICE LAYER                              │
│  routes/ (7 Flask Blueprints)               │
│  app.py (create_app factory)                │
│  app_state.py (shared state singleton)      │
├─────────────────────────────────────────────┤
│  DOMAIN LAYER                               │
│  bot/ (search, apply, bot loop)             │
│  core/ (ai_engine, filter, scheduler,       │
│         resume_renderer, i18n)              │
│  config/ (settings, Pydantic models)        │
├─────────────────────────────────────────────┤
│  INFRASTRUCTURE LAYER                       │
│  db/ (database.py — SQLite)                 │
│  bot/browser.py (Playwright manager)        │
│  ~/.autoapply/ (data directory)             │
└─────────────────────────────────────────────┘
```

### Presentation Layer
- **`templates/index.html`** -- Jinja2 template serving the SPA shell, injects API token via `window.__apiToken`.
- **`static/js/`** -- 17 ES modules (no build step): `app.js` (entry point), `dashboard.js`, `applications.js`, `config.js`, `api.js`, `i18n.js`, etc.
- **`static/css/main.css`** -- All application styles.
- **`static/locales/`** -- JSON translation files (`en.json`, etc.).

### Service Layer
- **`app.py`** -- `create_app()` factory function. Registers blueprints, middleware, error handlers, SocketIO, security headers.
- **`app_state.py`** -- Shared application state singleton (bot status, active connections, scheduler reference).
- **`routes/`** -- 7 Flask Blueprints: `bot.py`, `applications.py`, `config.py`, `profile.py`, `analytics.py`, `login.py`, `lifecycle.py`.

### Domain Layer
- **`bot/bot.py`** -- `run_bot()` main loop orchestrating the search-filter-apply pipeline.
- **`bot/search/`** -- `BaseSearcher` ABC, `LinkedInSearcher`, `IndeedSearcher`.
- **`bot/apply/`** -- `BaseApplier` ABC, 6 applier implementations (LinkedIn, Indeed, Greenhouse, Lever, Workday, Ashby).
- **`core/ai_engine.py`** -- Multi-provider LLM integration (`invoke_llm()`, `generate_documents()`).
- **`core/filter.py`** -- `score_job()`, `detect_ats()`, `ScoredJob` dataclass.
- **`core/scheduler.py`** -- `BotScheduler`, time-based auto-start/stop.
- **`core/resume_renderer.py`** -- `render_resume_to_pdf()` using ReportLab.
- **`core/i18n.py`** -- Backend internationalization (`t()` function).
- **`config/settings.py`** -- Pydantic v2 models for all configuration.

### Infrastructure Layer
- **`db/database.py`** -- SQLite database with WAL mode, connection management, CRUD operations.
- **`bot/browser.py`** -- `BrowserManager` wrapping Playwright persistent context at `~/.autoapply/browser_profile/`.

---

## Component Catalog

| Component | File(s) | Responsibility |
|-----------|---------|----------------|
| App Factory | `app.py` | Creates Flask app, registers blueprints, middleware, error handlers |
| App State | `app_state.py` | Shared mutable state: bot status, scheduler ref, active connections |
| Bot Routes | `routes/bot.py` | Start/stop bot, review queue, approve/reject/skip/manual |
| App Routes | `routes/applications.py` | CRUD for applications, export, event timeline |
| Config Routes | `routes/config.py` | Read/write config, setup status, API key validation |
| Profile Routes | `routes/profile.py` | Experience file upload/download/delete |
| Analytics Routes | `routes/analytics.py` | Summary stats, feed history |
| Login Routes | `routes/login.py` | Open/close platform login browser |
| Lifecycle Routes | `routes/lifecycle.py` | Health check, graceful shutdown, locale listing |
| Bot Loop | `bot/bot.py` | Main search-filter-apply loop, APPLIERS registry |
| LinkedIn Search | `bot/search/linkedin.py` | Scrapes LinkedIn job listings via Playwright |
| Indeed Search | `bot/search/indeed.py` | Scrapes Indeed job listings via Playwright |
| LinkedIn Applier | `bot/apply/linkedin.py` | Easy Apply automation |
| Indeed Applier | `bot/apply/indeed.py` | Quick Apply automation |
| Greenhouse Applier | `bot/apply/greenhouse.py` | Greenhouse ATS form filling |
| Lever Applier | `bot/apply/lever.py` | Lever ATS form filling |
| Workday Applier | `bot/apply/workday.py` | Multi-step Workday forms (data-automation-id selectors) |
| Ashby Applier | `bot/apply/ashby.py` | Single-page Ashby forms (jobs.ashbyhq.com) |
| AI Engine | `core/ai_engine.py` | LLM calls to Anthropic/OpenAI/Google/DeepSeek |
| Job Filter | `core/filter.py` | Scoring, ATS detection, ScoredJob |
| Scheduler | `core/scheduler.py` | Cron-like bot scheduling |
| Resume Renderer | `core/resume_renderer.py` | PDF generation with ReportLab |
| i18n | `core/i18n.py` | Backend translation function |
| Settings | `config/settings.py` | Pydantic v2 config models, load/save |
| Database | `db/database.py` | SQLite WAL, all persistence operations |
| Browser Manager | `bot/browser.py` | Playwright persistent context lifecycle |

---

## Data Flow

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  User    │    │   Bot    │    │  Filter  │    │    AI    │    │ Applier  │
│Configures│───▶│ Searches │───▶│  Scores  │───▶│Generates │───▶│ Submits  │
│          │    │          │    │          │    │          │    │          │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                      │
                     ┌──────────┐    ┌──────────┐                     │
                     │    UI    │◀───│    DB    │◀────────────────────┘
                     │ Displays │    │  Stores  │
                     └──────────┘    └──────────┘
```

1. **User configures** -- Profile, search criteria, apply mode, AI provider via the setup wizard or settings page.
2. **Bot searches** -- `LinkedInSearcher` and/or `IndeedSearcher` query job boards via Playwright, returning `RawJob` objects.
3. **Filter scores** -- `score_job()` evaluates each job against user criteria (keywords, location, experience). `detect_ats()` identifies the application platform.
4. **AI generates** -- `generate_documents()` calls the configured LLM to produce a tailored resume and cover letter. Falls back to templates if no AI provider is configured.
5. **Applier submits** -- The appropriate `BaseApplier` subclass fills and submits the application form with human-like delays.
6. **DB stores** -- Application record saved to SQLite with status, timestamps, and event history.
7. **UI displays** -- SocketIO pushes real-time updates to the dashboard; REST endpoints serve application data.

---

## Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Language | Python | 3.11+ | Backend logic, bot automation |
| Web Framework | Flask | 3.x | REST API, template rendering |
| Real-time | Flask-SocketIO | 5.x | Live feed, bot status updates |
| WSGI Server | gevent | 24.x | Async worker for SocketIO |
| Database | SQLite | 3.x (stdlib) | Application storage, WAL mode |
| Validation | Pydantic | 2.x | Config models, data validation |
| Browser Automation | Playwright | 1.x | Job search and application submission |
| PDF Generation | ReportLab | 4.x | ATS-safe resume PDFs |
| Desktop Shell | Electron | 33.x | Native desktop wrapper |
| Desktop Build | electron-builder | 25.x | Installers for Win/Mac/Linux |
| Linting | Ruff | 0.8.x | Python formatting and linting |
| Type Checking | mypy | 1.x | Static type analysis |
| Testing | pytest | 8.x | Unit and integration tests |

---

## Key Architecture Decision Records (ADRs)

| ADR | Title | Decision |
|-----|-------|----------|
| ADR-005 | Desktop Distribution | Electron shell wrapping Flask backend as child process |
| ADR-006 | Browser Automation | Playwright with separate Chromium (not Electron's); persistent contexts require custom user data directory |
| ADR-008 | Port Selection | Auto-detect available port in range 5000-5010 |
| ADR-009 | AI Provider | Multi-provider LLM API via direct HTTP (Anthropic, OpenAI, Google, DeepSeek) |
| ADR-010 | PDF Generation | ReportLab for ATS-safe PDF resume rendering |
| ADR-011 | AI Fallback | Template-based document generation when no AI provider configured |
| ADR-012 | ATS Detection | URL fingerprinting to route to correct applier |
| ADR-013 | Scheduling | Time-window based auto-start/stop with timezone support |
| ADR-014 | Blueprint Architecture | 7 Flask Blueprints in routes/, shared state in app_state.py, create_app() factory |
| ADR-015 | API Authentication | Bearer token from ~/.autoapply/.api_token, AUTOAPPLY_DEV=1 bypass for development |
| ADR-016 | API Key Security | OS keyring storage with auto-migration from plaintext config |
| ADR-017 | Frontend Modules | 17 ES modules in static/js/, CSS in static/css/, no build step |

Full ADR documents are located in `.claude/docs/SAD-TASK-*.md` files within the repository.
