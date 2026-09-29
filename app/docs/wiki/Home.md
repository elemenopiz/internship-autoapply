# AutoApply Wiki

**AutoApply** is an AI-powered job application automation tool that searches for jobs, generates tailored resumes and cover letters, and applies automatically across multiple ATS platforms.

**Current Version**: v1.9.0 | **Production Readiness**: 10.0/10 | **Test Coverage**: 97% (bot/core/config/db)

---

## Quick Links

| Section | Description |
|---------|-------------|
| [Getting Started](Getting-Started) | Installation, setup wizard, first run |
| [Architecture Overview](Architecture-Overview) | System design, components, data flow |
| [API Reference](API-Reference) | All REST endpoints with request/response examples |
| [Configuration Guide](Configuration-Guide) | Settings, LLM providers, search criteria, scheduling |
| [Bot Operations](Bot-Operations) | How the bot works, apply modes, ATS support |
| [Development Guide](Development-Guide) | Local setup, testing, CI/CD, contributing |
| [Internationalization](Internationalization) | i18n system, adding new languages |
| [Distribution & Packaging](Distribution-and-Packaging) | Building installers, release workflow |
| [Security](Security) | Auth, headers, rate limiting, threat model |
| [Changelog](Changelog) | Version history from v1.0.0 to v1.9.0 |
| [Roadmap](Roadmap) | Upcoming features and enhancements |

---

## Project Structure

```
AutoApply/
├── app.py                  # Flask app factory + middleware
├── app_state.py            # Shared mutable state (thread-safe)
├── run.py                  # Entry point (logging, port detection, gevent)
├── config/settings.py      # Pydantic config models
├── core/
│   ├── ai_engine.py        # Multi-provider LLM API
│   ├── filter.py           # Job scoring + ATS detection
│   ├── resume_renderer.py  # PDF generation (ReportLab)
│   ├── scheduler.py        # Time-based bot scheduling
│   └── i18n.py             # Backend translation system
├── db/
│   ├── database.py         # SQLite operations (WAL mode)
│   └── models.py           # Pydantic data models
├── bot/
│   ├── bot.py              # Main loop: search → filter → generate → apply
│   ├── browser.py          # BrowserManager (Playwright persistent context)
│   ├── state.py            # Bot state machine
│   ├── search/             # LinkedIn, Indeed searchers
│   └── apply/              # 6 appliers: LinkedIn, Indeed, Greenhouse, Lever, Workday, Ashby
├── routes/                 # 7 Flask Blueprints (bot, applications, config, profile, analytics, login, lifecycle)
├── static/
│   ├── css/main.css        # All styles
│   ├── js/                 # 17 ES modules (no build step)
│   └── locales/en.json     # 383 translation keys
├── templates/index.html    # SPA shell with data-i18n attributes
├── electron/               # Desktop shell (main.js, python-backend.js, tray, build scripts)
├── tests/                  # 738 tests across 27 files
└── .github/workflows/      # CI (lint+test+security) + Release (3-platform builds)
```

---

## Architecture Decisions

| ADR | Decision | Rationale |
|-----|----------|-----------|
| ADR-005 | Electron wrapping Flask | Desktop app with Python backend |
| ADR-006 | Separate Chromium for Playwright | Persistent browser contexts incompatible with Electron's Chromium |
| ADR-008 | Port auto-detection (5000-5010) | Avoid conflicts on common ports |
| ADR-009 | Multi-provider LLM via direct HTTP | No SDK dependency, supports 4 providers |
| ADR-010 | ReportLab for PDF | ATS-safe resume rendering |
| ADR-011 | Fallback templates | Works without AI configured |
| ADR-014 | Flask Blueprint architecture | 7 blueprints, shared state module |
| ADR-017 | Vanilla ES modules | No bundler, native browser support |
| ADR-018 | Python bundling strategy | Windows embeddable + python-build-standalone |
| ADR-019 | Programmatic icon generation | canvas + png2icons, no manual assets |
| ADR-020 | CI release on v* tags | GitHub Actions → GitHub Releases |

---

## Requirements Traceability

| Category | Total | Covered | Partial | Missing |
|----------|-------|---------|---------|---------|
| Functional (FR-001–082) | 79 | 66 | 13 | 0 |
| Quick Wins (QW-1–5) | 5 | 5 | 0 | 0 |
| Medium Effort (ME-1–9) | 9 | 9 | 0 | 0 |
| Deferred (D-5–7) | 3 | 3 | 0 | 0 |
| Large Effort (LE-1–3) | 3 | 3 | 0 | 0 |
| Distribution (DIST-01–09) | 9 | 9 | 0 | 0 |
| **Total** | **108** | **95** | **13** | **0** |

The 13 partial items are Electron E2E and frontend DOM rendering — cannot be tested with pytest.
