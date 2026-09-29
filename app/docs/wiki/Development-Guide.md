# Development Guide

## Local Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ and npm 9+
- Git

### Clone and Install

```bash
git clone https://github.com/AbhishekMandapmalvi/AutoApply.git
cd AutoApply

# Create and activate virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install Python dependencies (including dev extras)
pip install -e ".[dev]"

# Install Playwright Chromium
playwright install chromium

# Install Electron dependencies
cd electron
npm install
cd ..
```

### Running the Application

```bash
# Option 1: Run via Electron (full desktop experience)
cd electron && npm start

# Option 2: Run Flask backend standalone (for API development)
python run.py

# Option 3: Run with development bypass (no auth required)
AUTOAPPLY_DEV=1 python run.py
```

---

## Running Tests

```bash
# Run all tests
pytest tests/ -x -q

# Run with coverage
pytest tests/ --cov=app --cov=bot --cov=core --cov=config --cov=db --cov=routes -x -q

# Run a specific test file
pytest tests/test_api.py -x -q

# Run a specific test
pytest tests/test_api.py::test_health_endpoint -x -q

# Run with verbose output
pytest tests/ -v
```

The test suite contains **542+ tests** covering bot, core, config, db, and routes modules at approximately **97% coverage**.

> **Windows note**: Exit code 15 is a known gevent signal-handling quirk on Windows and does not indicate a test failure. Always check the actual pass/fail counts in the test output.

### Test Organization

| File | Tests | Scope |
|------|-------|-------|
| `tests/test_settings.py` | 17 | Pydantic config models, load/save |
| `tests/test_database.py` | 22 | SQLite CRUD, WAL mode, migrations |
| `tests/test_state.py` | 29 | App state management, thread safety |
| `tests/test_api.py` | 56 | Flask route handlers, auth, validation |
| `tests/test_integration.py` | 8 | End-to-end API workflows |
| `tests/test_ai_engine.py` | 25 | LLM provider calls, fallback |
| `tests/test_resume_renderer.py` | 11 | PDF generation |
| `tests/test_filter.py` | 35 | Job scoring, ATS detection |
| `tests/test_bot_base.py` | 14 | BaseApplier, human-like behavior |
| `tests/test_bot_loop.py` | 44 | Bot main loop, state transitions |
| `tests/test_bot_helpers.py` | 31 | Bot utility functions |
| `tests/test_search_engines.py` | 43 | LinkedIn/Indeed searchers |
| `tests/test_browser_manager.py` | 21 | Playwright context management |
| `tests/test_appliers_ats.py` | 19 | Greenhouse, Lever appliers |
| `tests/test_appliers_coverage.py` | 73 | Applier edge cases, coverage boost |
| `tests/test_appliers_workday_ashby.py` | 69 | Workday, Ashby appliers |
| `tests/test_scheduler.py` | 25 | Schedule logic, timezone handling |
| `tests/test_app_detail.py` | 15 | Application detail view API |
| `tests/test_login_api.py` | 22 | Login browser API |
| `tests/test_quick_wins.py` | 29 | Rate limiter, security headers |
| `tests/test_rate_limiter.py` | 12 | Token bucket algorithm |

### Test Configuration

The `tests/conftest.py` file provides shared fixtures:

- **`_dev_mode_auth_bypass`** (autouse) -- Sets `AUTOAPPLY_DEV=1` to bypass API auth in all tests.
- **`app`** -- Creates a Flask test application via `create_app()`.
- **`client`** -- Flask test client.
- **`tmp_path`** -- Used for filesystem tests to avoid polluting real data directories.
- **`mock_db`** -- In-memory SQLite database for test isolation.

---

## Linting

### Ruff

AutoApply uses [Ruff](https://docs.astral.sh/ruff/) for Python formatting and linting:

```bash
# Check for lint errors
ruff check .

# Auto-fix lint errors
ruff check . --fix

# Format code
ruff format .

# Check formatting without modifying
ruff format . --check
```

### mypy

Static type checking with mypy:

```bash
mypy app.py app_state.py run.py config/ core/ db/ bot/ routes/
```

---

## Pre-commit Hooks

The project uses [pre-commit](https://pre-commit.com/) with Ruff hooks configured in `.pre-commit-config.yaml`:

```bash
# Install pre-commit hooks
pre-commit install

# Run all hooks on all files (manual check)
pre-commit run --all-files
```

Hooks run automatically on every `git commit`:
- **ruff-format** -- Ensures consistent code formatting.
- **ruff** -- Checks for lint errors with auto-fix.

---

## CI Pipeline

The project uses GitHub Actions with 3 jobs that run on every push and pull request:

### 1. Lint Job
- Runs `ruff check .` and `ruff format . --check`.
- Runs `mypy` on all source modules.

### 2. Test Job
- Sets up Python 3.11.
- Installs dependencies from `pyproject.toml`.
- Runs `pytest tests/ -x -q --tb=short`.
- Reports test results and coverage.

### 3. Security Job
- Runs `pip-audit` to check for known vulnerabilities in dependencies.
- Checks for hardcoded secrets or sensitive patterns.

All three jobs must pass for a PR to be mergeable.

---

## Project Layout Conventions

```
AutoApply/
├── app.py                  # create_app() factory, Flask app setup
├── app_state.py            # Shared application state singleton
├── run.py                  # Entry point: starts Flask+SocketIO server
├── pyproject.toml          # Python project metadata, dependencies
├── config/
│   └── settings.py         # Pydantic v2 config models, load/save
├── core/
│   ├── ai_engine.py        # Multi-provider LLM integration
│   ├── filter.py           # Job scoring, ATS detection
│   ├── scheduler.py        # Time-based bot scheduling
│   ├── resume_renderer.py  # PDF resume generation
│   └── i18n.py             # Backend internationalization
├── db/
│   └── database.py         # SQLite database operations
├── bot/
│   ├── bot.py              # Main bot loop
│   ├── browser.py          # Playwright browser manager
│   ├── state.py            # Bot state machine
│   ├── search/
│   │   ├── base.py         # BaseSearcher ABC, RawJob
│   │   ├── linkedin.py     # LinkedIn searcher
│   │   └── indeed.py       # Indeed searcher
│   └── apply/
│       ├── base.py         # BaseApplier ABC, ApplyResult
│       ├── linkedin.py     # LinkedIn Easy Apply
│       ├── indeed.py       # Indeed Quick Apply
│       ├── greenhouse.py   # Greenhouse ATS
│       ├── lever.py        # Lever ATS
│       ├── workday.py      # Workday ATS
│       └── ashby.py        # Ashby ATS
├── routes/
│   ├── bot.py              # Bot blueprint
│   ├── applications.py     # Applications blueprint
│   ├── config.py           # Config blueprint
│   ├── profile.py          # Profile blueprint
│   ├── analytics.py        # Analytics blueprint
│   ├── login.py            # Login blueprint
│   └── lifecycle.py        # Lifecycle blueprint
├── templates/
│   └── index.html          # SPA shell (Jinja2)
├── static/
│   ├── css/main.css        # Application styles
│   ├── js/                 # 17 ES modules
│   │   ├── app.js          # Entry point
│   │   ├── api.js          # API client
│   │   ├── i18n.js         # Frontend i18n
│   │   ├── dashboard.js    # Dashboard tab
│   │   ├── applications.js # Applications tab
│   │   ├── config.js       # Settings tab
│   │   └── ...             # Other modules
│   └── locales/
│       └── en.json         # English translations
├── electron/
│   ├── main.js             # Electron main process
│   ├── python-backend.js   # Flask child process manager
│   ├── package.json        # Electron dependencies
│   └── build/              # electron-builder config
├── tests/
│   ├── conftest.py         # Shared fixtures
│   ├── test_api.py         # Route tests
│   ├── test_bot_loop.py    # Bot tests
│   └── ...                 # Other test files
└── .github/
    └── workflows/          # CI pipeline definitions
```

### Key Conventions

- **Blueprints in `routes/`**: Each Blueprint handles one domain area. Shared state lives in `app_state.py`, not in blueprint modules.
- **`create_app()` factory**: All app setup happens in `app.py :: create_app()`. This pattern supports test isolation (each test gets a fresh app).
- **Pydantic models**: All configuration uses Pydantic v2 models in `config/settings.py`. No raw dict access for config data.
- **ABC pattern**: Search engines extend `BaseSearcher`; appliers extend `BaseApplier`. Both define the contract in `base.py`.

---

## Adding a New API Endpoint

1. **Choose the blueprint** in `routes/` (or create a new one if the domain warrants it).

2. **Add the route**:
   ```python
   @bp.route('/api/example', methods=['GET'])
   def get_example():
       return jsonify({"data": "value"})
   ```

3. **Add tests** in the corresponding test file:
   ```python
   def test_get_example(client):
       resp = client.get('/api/example')
       assert resp.status_code == 200
       assert resp.json['data'] == 'value'
   ```

4. **Add i18n strings** if the endpoint returns user-facing messages:
   - Add key to `static/locales/en.json`.
   - Use `t("key")` from `core/i18n.py` in the route.

5. **Update the API reference** documentation if the endpoint is public.

---

## Adding a New ATS Applier

1. **Create the applier** in `bot/apply/`:
   ```python
   # bot/apply/myats.py
   from bot.apply.base import BaseApplier, ApplyResult

   class MyATSApplier(BaseApplier):
       async def apply(self, page, job, resume_path, cover_letter):
           # Fill form fields using self._human_type()
           # Click buttons with self._random_pause()
           # Return ApplyResult(success=True/False, message="...")
           pass
   ```

2. **Register in APPLIERS dict** (`bot/bot.py`):
   ```python
   from bot.apply.myats import MyATSApplier

   APPLIERS = {
       # ... existing entries
       "myats": MyATSApplier,
   }
   ```

3. **Add URL fingerprint** (`core/filter.py`):
   ```python
   ATS_FINGERPRINTS = {
       # ... existing entries
       "myats.example.com": "myats",
   }
   ```

4. **Write tests** in `tests/test_appliers_*.py`:
   - Test form filling with mocked Playwright page.
   - Test error handling (form not found, submission failure, captcha).
   - Test human-like delay behavior.

5. **Update documentation** and the supported platforms table.

---

## Git Workflow

1. **Branch from master**:
   ```bash
   git checkout master
   git pull
   git checkout -b feature/my-feature
   ```

2. **Make changes**, committing frequently with descriptive messages.

3. **Run checks before pushing**:
   ```bash
   ruff check .
   ruff format . --check
   pytest tests/ -x -q
   ```

4. **Push and open a PR**:
   ```bash
   git push -u origin feature/my-feature
   gh pr create --title "Add my feature" --body "Description..."
   ```

5. **Required checks**: All three CI jobs (lint, test, security) must pass.

6. **Merge**: Squash-merge to master once approved and all checks pass.

---

## CONTRIBUTING.md

For contribution guidelines, code of conduct, and additional details, see [CONTRIBUTING.md](https://github.com/AbhishekMandapmalvi/AutoApply/blob/master/CONTRIBUTING.md) in the repository root.
